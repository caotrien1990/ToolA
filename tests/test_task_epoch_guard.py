import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import code_tools as ct
import orchestrator_server as srv


class TaskEpochGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        srv._DUPLICATE_FAILURE_STATE.clear()
        # CAN-054: checkpoints persist in SQLite across runs now — purge the
        # fixed test session keys so epoch assertions stay deterministic.
        try:
            import sqlite3 as _sq
            conn = _sq.connect(str(ct._SESSIONS_DB))
            try:
                for _k in ("session-a", "session-b", "session-c", "session-d", "default"):
                    conn.execute("DELETE FROM task_checkpoints WHERE session_key=?", (_k,))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    async def _begin_root(self, session_id: str, prompt: str, agent_id: str):
        claim_id = f"claim-{session_id}-{agent_id}-0123456789abcdef0123456789abcdef"
        bootstrap = json.loads(await ct.bootstrap_session(
            session_id=session_id, agent_id=agent_id, owner_claim_id=claim_id,
        ))
        result = json.loads(await ct.begin_task(
            session_id=session_id, prompt=prompt, agent_id=agent_id,
            owner_capability=bootstrap["owner_capability"],
            owner_claim_id=claim_id,
        ))
        result["owner_capability"] = bootstrap["owner_capability"]
        return result

    async def test_begin_task_increments_epoch_and_hashes_prompt(self):
        first = await self._begin_root("session-a", "first task", "hermes")
        second = json.loads(await ct.begin_task(
            session_id="session-a", prompt="second task", agent_id="hermes",
            owner_capability=first["owner_capability"],
        ))
        self.assertEqual(first["task_epoch"], 1)
        self.assertEqual(second["task_epoch"], 2)
        self.assertNotEqual(first["prompt_hash"], second["prompt_hash"])
        self.assertLessEqual(len(second["active_task"]), 2000)

    async def test_begin_task_repairs_utf8_prompt_mojibake(self):
        expected = "tiến hành sửa mojibake và đóng session"
        corrupted = expected.encode("utf-8").decode("latin-1")

        result = await self._begin_root("session-a", corrupted, "hermes")

        self.assertEqual(result["active_task"], expected)
        self.assertEqual(result["prompt_hash"], ct._task_prompt_hash(expected))
        self.assertTrue(result["prompt_encoding_repaired"])

    async def test_begin_task_preserves_valid_unicode_prompt(self):
        expected = "tiến hành sửa UTF-8 nguyên bản"

        result = await self._begin_root("session-a", expected, "hermes")

        self.assertEqual(result["active_task"], expected)
        self.assertFalse(result["prompt_encoding_repaired"])

    async def test_concurrent_root_tasks_receive_unique_epochs(self):
        owner = await self._begin_root("session-d", "claim root", "hermes")
        results = await asyncio.gather(*(
            ct.begin_task("session-d", f"task {index}", "hermes",
                          owner_capability=owner["owner_capability"])
            for index in range(8)
        ))
        epochs = sorted(json.loads(result)["task_epoch"] for result in results)
        self.assertEqual(epochs, list(range(2, 10)))

    async def test_stale_epoch_is_rejected_before_tool_runs(self):
        current = await self._begin_root("session-b", "current task", "ope")
        response = await srv.dispatch_tool_call(
            "workflow_list",
            {"session_id": "session-b", "task_epoch": current["task_epoch"] - 1},
            transport="mcp",
        )
        payload = json.loads(response.text)
        self.assertEqual(payload["error_name"], "STALE_TASK_EPOCH")
        self.assertFalse(payload["retryable"])

    async def test_prompt_hash_mismatch_is_rejected_before_tool_runs(self):
        current = await self._begin_root("session-c", "current task", "hermes")
        response = await srv.dispatch_tool_call(
            "workflow_list",
            {"session_id": "session-c", "task_epoch": current["task_epoch"],
             "prompt_hash": "not-the-current-hash"},
            transport="mcp",
        )
        payload = json.loads(response.text)
        self.assertEqual(payload["error_name"], "STALE_TASK_EPOCH")
        self.assertFalse(payload["retryable"])

    async def test_current_epoch_metadata_is_stamped_on_response(self):
        current = await self._begin_root("session-c", "current task", "hermes")
        response = await srv.dispatch_tool_call(
            "workflow_list",
            {"session_id": "session-c", "task_epoch": current["task_epoch"]},
            transport="mcp",
        )
        payload = json.loads(response.text)
        self.assertEqual(payload["task_epoch"], current["task_epoch"])
        self.assertEqual(payload["prompt_hash"], current["prompt_hash"])
        self.assertEqual(payload["generation"], srv.SERVER_GENERATION)

    async def test_stale_server_generation_is_rejected(self):
        response = await srv.dispatch_tool_call(
            "workflow_list", {"generation": "stale-generation"}, transport="mcp"
        )
        payload = json.loads(response.text)
        self.assertEqual(payload["error_name"], "STALE_GENERATION")
        self.assertEqual(payload["generation"], srv.SERVER_GENERATION)
        self.assertEqual(payload["ca_next_action"], "rebootstrap_and_retry")
        self.assertFalse(payload["retryable"])

    async def test_mutating_call_requires_epoch_when_checkpoint_exists(self):
        current = await self._begin_root("session-b", "current task", "ope")
        response = await srv.dispatch_tool_call(
            "write_new_file", {"session_id": "session-b", "project_path": "D:/tmp",
                               "file_path": "a.txt", "content": "x"}, transport="mcp"
        )
        payload = json.loads(response.text)
        self.assertEqual(payload["error_name"], "TASK_EPOCH_REQUIRED")
        self.assertEqual(payload["task_epoch"], current["task_epoch"])

    async def test_subtask_epoch_is_resolved_by_agent(self):
        root = await self._begin_root("session-a", "root task", "hermes")
        delegated = json.loads(await ct.begin_task(
            session_id="session-a", prompt="worker task", agent_id="hermes",
            owner_capability=root["owner_capability"], delegate_agent_id="ope",
        ))
        subtask = json.loads(await ct.begin_task(
            session_id="session-a", prompt="worker task", agent_id="ope",
            delegation_capability=delegated["delegation_capability"],
        ))
        self.assertNotEqual(root["prompt_hash"], subtask["prompt_hash"])
        response = await srv.dispatch_tool_call(
            "workflow_list", {"session_id": "session-a", "task_epoch": subtask["task_epoch"],
                              "prompt_hash": subtask["prompt_hash"], "agent_id": "ope"},
            transport="mcp",
        )
        payload = json.loads(response.text)
        self.assertNotEqual(payload.get("error_name"), "STALE_TASK_EPOCH")
        self.assertEqual(payload["task_epoch"], subtask["task_epoch"])

    async def test_attacker_cannot_mint_subtask_by_agent_payload(self):
        attack = json.loads(await ct.begin_task(
            session_id="session-a", prompt="attacker task", agent_id="attacker"
        ))

        self.assertEqual(attack["error_name"], "TASK_OWNER_REQUIRED")
        self.assertIsNone(ct._task_checkpoint_load("session-a"))

    async def test_existing_root_rejects_agent_mismatch_even_with_owner_capability(self):
        root = await self._begin_root("session-a", "root task", "hermes")

        attack = json.loads(await ct.begin_task(
            session_id="session-a", prompt="replace root", agent_id="attacker",
            owner_capability=root["owner_capability"],
        ))

        self.assertEqual(attack["error_name"], "TASK_OWNER_MISMATCH")
        self.assertEqual(ct._task_checkpoint_load("session-a")["agent_id"], "hermes")

    async def test_same_agent_cannot_renew_without_owner_capability(self):
        root = await self._begin_root("session-a", "root task", "hermes")

        unauthenticated = json.loads(await ct.begin_task(
            session_id="session-a", prompt="replace task", agent_id="hermes"
        ))

        self.assertIn("owner_capability", root)
        self.assertEqual(unauthenticated["error_name"], "TASK_OWNER_MISMATCH")
        self.assertEqual(ct._task_checkpoint_load("session-a")["task_epoch"], 1)

    async def test_first_claim_cannot_issue_delegation(self):
        result = json.loads(await ct.begin_task(
            session_id="session-a", prompt="worker task", agent_id="hermes",
            delegate_agent_id="ope",
        ))

        self.assertEqual(result["error_name"], "TASK_OWNER_REQUIRED")
        self.assertIsNone(ct._task_checkpoint_load("session-a"))

    async def test_checkpoint_read_redacts_authority_internals(self):
        root = await self._begin_root("session-a", "root", "hermes")
        await ct.begin_task(
            session_id="session-a", prompt="worker", agent_id="hermes",
            owner_capability=root["owner_capability"], delegate_agent_id="ope",
        )

        public = json.loads(await ct.get_task_checkpoint("session-a"))

        self.assertNotIn("owner_capability_sha256", public)
        self.assertNotIn("owner_identity_id", public)
        self.assertNotIn("delegation_capability_sha256", public["subtasks"]["ope"])

    async def test_bootstrap_owner_capability_is_one_time_and_owner_is_immutable(self):
        claim_id = "claim-owner-0123456789abcdef0123456789abcdef"
        first = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes", owner_claim_id=claim_id,
        ))
        repeated = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes", owner_claim_id=claim_id,
        ))
        attacker = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="attacker",
        ))

        self.assertIn("owner_capability", first)
        self.assertEqual(first["owner_capability"], repeated["owner_capability"])
        self.assertTrue(repeated["owner_claim_recovered"])
        self.assertEqual(attacker["error_name"], "OWNER_CLAIM_ID_REQUIRED")
        self.assertEqual(ct._task_checkpoint_load("session-a")["agent_id"], "hermes")

    async def test_lost_bootstrap_response_retry_same_claim_recovers_capability(self):
        claim_id = "lost-response-0123456789abcdef0123456789abcdef"
        first = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes", owner_claim_id=claim_id,
        ))
        stored_commitment = ct._task_checkpoint_load("session-a")["owner_capability_sha256"]
        recovered = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes", owner_claim_id=claim_id,
        ))
        self.assertEqual(recovered["owner_capability"], first["owner_capability"])
        self.assertEqual(
            ct.hashlib.sha256(recovered["owner_capability"].encode()).hexdigest(),
            stored_commitment,
        )
        self.assertEqual(recovered["owner_claim_status"], "pending")
        self.assertIn("owner_claim_expires_in_seconds", recovered)
        self.assertTrue(recovered["owner_claim_recovered"])

    async def test_competing_pending_claim_is_rejected(self):
        await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes",
            owner_claim_id="pending-a-0123456789abcdef0123456789abcdef",
        )

        competing = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes",
            owner_claim_id="pending-b-0123456789abcdef0123456789abcdef",
        ))

        self.assertEqual(competing["error_name"], "TASK_OWNER_MISMATCH")

    async def test_confirmed_owner_is_immutable(self):
        root = await self._begin_root("session-a", "root", "hermes")

        competing = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes",
            owner_claim_id="replacement-0123456789abcdef0123456789abcdef",
        ))

        self.assertEqual(competing["error_name"], "TASK_OWNER_MISMATCH")
        self.assertEqual(ct._task_checkpoint_load("session-a")["task_epoch"], 1)
        self.assertIn("owner_capability", root)

    async def test_expired_pending_claim_allows_new_claim(self):
        await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes",
            owner_claim_id="expired-a-0123456789abcdef0123456789abcdef",
        )
        expired = ct._task_checkpoint_load("session-a")
        expired["owner_claim_expires_at"] = 0
        ct._task_checkpoint_save("session-a", expired)

        replacement = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes",
            owner_claim_id="expired-b-0123456789abcdef0123456789abcdef",
        ))

        self.assertTrue(replacement["ok"])
        self.assertFalse(replacement["owner_claim_recovered"])

    async def test_capability_never_leaks_through_checkpoint(self):
        claim_id = "no-leak-0123456789abcdef0123456789abcdef"
        bootstrap = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes", owner_claim_id=claim_id,
        ))
        await ct.begin_task(
            session_id="session-a", prompt="root", agent_id="hermes",
            owner_capability=bootstrap["owner_capability"], owner_claim_id=claim_id,
        )

        checkpoint_text = await ct.get_task_checkpoint("session-a")

        self.assertNotIn(bootstrap["owner_capability"], checkpoint_text)
        self.assertNotIn(claim_id, checkpoint_text)
        self.assertNotIn("owner_claim_sha256", checkpoint_text)
        sanitized = ct._sanitize_audit_value({
            "owner_capability": bootstrap["owner_capability"],
            "owner_claim_id": claim_id,
            "delegation_capability": "delegated-secret",
        })
        self.assertEqual(set(sanitized.values()), {"<redacted>"})

    async def test_missing_claim_is_rejected_before_persistence(self):
        missing = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes",
        ))

        self.assertEqual(missing["error_name"], "OWNER_CLAIM_ID_REQUIRED")
        self.assertIsNone(ct._task_checkpoint_load("session-a"))

    async def test_wrong_claim_is_rejected_without_mutating_checkpoint(self):
        claim_id = "right-claim-0123456789abcdef0123456789abcdef"
        await ct.bootstrap_session(session_id="session-a", agent_id="hermes", owner_claim_id=claim_id)
        before = dict(ct._task_checkpoint_load("session-a"))
        rejected = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes",
            owner_claim_id="wrong-claim-0123456789abcdef0123456789abcdef",
        ))
        self.assertEqual(rejected["error_name"], "TASK_OWNER_MISMATCH")
        self.assertEqual(ct._task_checkpoint_load("session-a"), before)
        self.assertNotIn(claim_id, json.dumps(rejected))

    async def test_capability_is_bound_to_its_session(self):
        owner_a = await self._begin_root("session-a", "root a", "hermes")
        await self._begin_root("session-b", "root b", "hermes")
        before_b = dict(ct._task_checkpoint_load("session-b"))
        rejected = json.loads(await ct.begin_task(
            session_id="session-b", prompt="cross-session", agent_id="hermes",
            owner_capability=owner_a["owner_capability"],
        ))
        self.assertEqual(rejected["error_name"], "TASK_OWNER_MISMATCH")
        self.assertEqual(ct._task_checkpoint_load("session-b"), before_b)

    async def test_tampered_capability_commitment_fails_closed(self):
        claim_id = "tamper-check-0123456789abcdef0123456789abcdef"
        first = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes", owner_claim_id=claim_id,
        ))
        tampered = ct._task_checkpoint_load("session-a")
        tampered["owner_capability_sha256"] = "0" * 64
        ct._task_checkpoint_save("session-a", tampered)
        recovered = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes", owner_claim_id=claim_id,
        ))
        self.assertEqual(recovered["error_name"], "TASK_OWNER_STATE_INTEGRITY")
        self.assertNotIn("owner_capability", recovered)
        self.assertNotIn(first["owner_capability"], json.dumps(recovered))

    async def test_committed_claim_recovery_reports_durable_status(self):
        claim_id = "committed-recovery-0123456789abcdef0123456789abcdef"
        first = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes", owner_claim_id=claim_id,
        ))
        await ct.begin_task(
            session_id="session-a", prompt="root", agent_id="hermes",
            owner_capability=first["owner_capability"], owner_claim_id=claim_id,
        )
        recovered = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes", owner_claim_id=claim_id,
        ))
        self.assertEqual(recovered["owner_claim_status"], "committed")
        self.assertTrue(recovered["owner_claim_recovered"])
        self.assertNotIn("owner_claim_expires_in_seconds", recovered)

    async def test_expired_pending_claim_cannot_be_confirmed(self):
        claim_id = "expired-confirm-0123456789abcdef0123456789abcdef"
        bootstrap = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes", owner_claim_id=claim_id,
        ))
        expired = ct._task_checkpoint_load("session-a")
        expired["owner_claim_expires_at"] = 0
        ct._task_checkpoint_save("session-a", expired)
        rejected = json.loads(await ct.begin_task(
            session_id="session-a", prompt="too late", agent_id="hermes",
            owner_capability=bootstrap["owner_capability"], owner_claim_id=claim_id,
        ))
        self.assertEqual(rejected["error_name"], "OWNER_CLAIM_EXPIRED")
        self.assertNotIn("owner_capability", rejected)
    async def test_committed_legacy_checkpoint_remains_readable_and_renewable(self):
        legacy_capability = "legacy-held-capability"
        checkpoint = {
            "agent_id": "hermes", "scope": "root", "task_epoch": 3,
            "prompt_hash": "legacy-hash", "active_task": "legacy",
            "owner_capability_sha256": ct.hashlib.sha256(
                legacy_capability.encode("utf-8")
            ).hexdigest(),
            "owner_claim_status": "committed",
        }
        ct._task_checkpoint_save("session-a", checkpoint)

        readable = json.loads(await ct.bootstrap_session(
            session_id="session-a", agent_id="hermes",
        ))
        renewed = json.loads(await ct.begin_task(
            session_id="session-a", prompt="renewed", agent_id="hermes",
            owner_capability=legacy_capability,
        ))

        self.assertTrue(readable["ok"])
        self.assertNotIn("owner_capability", readable)
        self.assertEqual(renewed["task_epoch"], 4)

    async def test_mcp_bridge_does_not_mint_mutation_token(self):
        response = await srv.dispatch_tool_call(
            "delete_file", {"project_path": "D:/tmp", "file_path": "missing.txt"},
            transport="mcp",
        )
        payload = json.loads(response.text)
        self.assertNotEqual(payload.get("error_name"), "MUTATION_TOKEN_REQUIRED")
        self.assertNotEqual(payload.get("error_name"), "STALE_TASK_EPOCH")

    def test_range_aliases_preserve_intent(self):
        for tool in ("read_file_range", "write_fix", "write_fix_html"):
            normalized, _ = srv._normalize_body(tool, {
                "line_start": 7,
                "line_end": 12,
            })
            self.assertEqual(normalized["start_line"], 7)
            self.assertEqual(normalized["end_line"], 12)


class ResultBudgetTests(unittest.TestCase):
    def test_large_json_result_is_bounded_with_continuation(self):
        raw = json.dumps({"ok": True, "content": "x" * 20000})
        shaped = srv._shape_tool_response_text("read_file_range", raw, max_bytes=2048)
        payload = json.loads(shaped)
        self.assertTrue(payload["truncated"])
        self.assertLessEqual(len(shaped.encode("utf-8")), 2600)
        self.assertIn("result_ref", payload)

    def test_tool_catalog_is_bounded_by_default(self):
        payload = srv._tool_names_payload()
        self.assertTrue(payload["bounded"])
        self.assertLessEqual(payload["returned_count"], 40)
        self.assertGreaterEqual(payload["total_available"], payload["returned_count"])

    def test_council_tracker_full_content_requires_explicit_diagnostic_opt_in(self):
        old_file = ct._COUNCIL_TRACKER_OUT_FILE
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "council_live.txt"
            target.write_text("FULL HISTORICAL PROMPT\n" + ("z" * 5000), encoding="utf-8")
            ct._COUNCIL_TRACKER_OUT_FILE = str(target)
            try:
                result = json.loads(asyncio.run(ct.council_tracker(
                    return_full=True, save_output=False
                )))
            finally:
                ct._COUNCIL_TRACKER_OUT_FILE = old_file
        self.assertNotIn("content", result)
        self.assertTrue(result["historical"])
        self.assertIn("excerpt", result)


if __name__ == "__main__":
    unittest.main()
