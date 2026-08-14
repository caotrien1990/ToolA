import asyncio
import json
import unittest

import code_tools
import orchestrator_server as srv


class AntiDriftAliasTests(unittest.TestCase):
    def test_line_range_aliases_preserve_bounds(self):
        for tool in ("read_file_range", "write_fix", "write_fix_html"):
            body = {"line_start": 7, "line_end": 19}
            normalized, applied = srv._normalize_body(tool, body)
            self.assertEqual(normalized["start_line"], 7)
            self.assertEqual(normalized["end_line"], 19)
            self.assertIn("line_start->start_line", applied)
            self.assertIn("line_end->end_line", applied)


class BoundedDiscoveryTests(unittest.TestCase):
    def test_unfiltered_catalog_is_bounded(self):
        payload = srv._tool_names_payload()
        self.assertTrue(payload["bounded"])
        self.assertLessEqual(payload["returned_count"], 30)
        self.assertGreater(payload["total_available"], payload["returned_count"])
        self.assertEqual(payload["ca_next_action"], "filter_or_bootstrap_session")

    def test_filtered_catalog_returns_matches(self):
        payload = srv._tool_names_payload(name_contains="bootstrap_session")
        self.assertEqual([item["name"] for item in payload["tools"]], ["bootstrap_session"])
        self.assertTrue(payload["filtered"])


class BootstrapContractTests(unittest.TestCase):
    def test_bootstrap_is_compact_and_has_task_contract(self):
        payload = json.loads(asyncio.run(code_tools.bootstrap_session(
            "", session_id="host-1", agent_id="hermes",
            owner_claim_id="antidrift-host-1-0123456789abcdef0123456789abcdef",
        )))
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        # Bootstrap carries generation, scoped fingerprints, and runtime hints.
        self.assertLessEqual(len(encoded), 13_500)
        self.assertIn("bootstrap_ref", payload)
        self.assertEqual(len(payload["bootstrap_ref"]), 16)
        self.assertEqual(len(payload["ruleset_sha256"]), 64)
        self.assertEqual(payload["bootstrap_ref"], payload["ruleset_sha256"][:16])
        self.assertEqual(payload["rule_source_sha256"], payload["rule"]["sha256"])
        self.assertEqual(len(payload["task_checkpoint_sha256"]), 64)
        self.assertEqual(payload["ca_next_action"], "call_begin_task_with_owner_capability")
        self.assertIn("task_contract", payload)
        self.assertIn("task_checkpoint", payload)
        self.assertIn("rule", payload)
        self.assertEqual(len(payload["rule"]["sha256"]), 64)
        self.assertIn("discovery", payload)
        self.assertIn("session_scope", payload)

        schema = next(
            item["function"] for item in code_tools.TOOL_SCHEMAS
            if item.get("function", {}).get("name") == "bootstrap_session"
        )
        self.assertIn("owner_claim_id", schema["parameters"]["required"])

    def test_bootstrap_preserves_explicit_session_scope(self):
        payload = json.loads(asyncio.run(code_tools.bootstrap_session(
            "", session_id="host-1", agent_id="hermes",
            owner_claim_id="antidrift-host-1-0123456789abcdef0123456789abcdef",
        )))
        self.assertEqual(payload["session_scope"]["session_id"], "host-1")
        self.assertEqual(payload["session_scope"]["agent_id"], "hermes")

    def test_bootstrap_requires_explicit_scope(self):
        payload = json.loads(asyncio.run(code_tools.bootstrap_session("")))
        self.assertEqual(payload["error_name"], "BOOTSTRAP_SCOPE_REQUIRED")

    def test_bootstrap_fingerprints_are_stable_and_session_scoped(self):
        first = json.loads(asyncio.run(code_tools.bootstrap_session(
            "", session_id="host-a", agent_id="hermes",
            owner_claim_id="antidrift-host-a-0123456789abcdef0123456789abcdef",
        )))
        repeat = json.loads(asyncio.run(code_tools.bootstrap_session(
            "", session_id="host-a", agent_id="hermes",
            owner_claim_id="antidrift-host-a-0123456789abcdef0123456789abcdef",
        )))
        other = json.loads(asyncio.run(code_tools.bootstrap_session(
            "", session_id="host-b", agent_id="hermes",
            owner_claim_id="antidrift-host-b-0123456789abcdef0123456789abcdef",
        )))
        self.assertEqual(first["ruleset_sha256"], repeat["ruleset_sha256"])
        self.assertEqual(first["task_checkpoint_sha256"], repeat["task_checkpoint_sha256"])
        self.assertNotEqual(first["task_checkpoint_sha256"], other["task_checkpoint_sha256"])
        self.assertEqual(first["session_scope"]["session_id"], "host-a")
        self.assertEqual(other["session_scope"]["session_id"], "host-b")


class ResultBudgetTests(unittest.TestCase):
    def test_large_result_is_bounded_with_continuation(self):
        payload = {"ok": True, "content": "x" * 30_000}
        bounded = srv._bound_tool_result(payload, max_bytes=4_000)
        self.assertTrue(bounded["truncated"])
        self.assertLessEqual(len(json.dumps(bounded).encode("utf-8")), 4_500)
        self.assertIn("continuation", bounded)


class DuplicateRetryTests(unittest.TestCase):
    def setUp(self):
        srv._DETERMINISTIC_RETRY_CACHE.clear()

    def test_second_identical_deterministic_failure_is_blocked(self):
        body = {"query": "x", "bad": True}
        first = srv._duplicate_retry_guard("agent-a", "search_code", body, "UNSUPPORTED_FIELD")
        second = srv._duplicate_retry_guard("agent-a", "search_code", body, "UNSUPPORTED_FIELD")
        self.assertIsNone(first)
        self.assertEqual(second["error_name"], "DUPLICATE_RETRY_BLOCKED")
        self.assertFalse(second["retryable"])


if __name__ == "__main__":
    unittest.main()
