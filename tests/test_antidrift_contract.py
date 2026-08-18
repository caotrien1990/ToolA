import asyncio
import json
import re
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
        # A36: this test used to assert `returned_count <= 30` and the exact
        # string "filter_or_bootstrap_session". Both were stale, and the number
        # was borrowed from the wrong knob: 30 is _TOOL_CATALOG_DEFAULT_LIMIT,
        # which only caps the FALLBACK branch taken when CURATED_CORE_TOOLS is
        # empty. The unfiltered default returns the curated set itself, so the
        # count tracks how many tools are curated (34 today) and is unrelated to
        # that limit. Pin the property the test is named after -- discovery is
        # bounded and materially smaller than the catalog -- not a number that
        # changes every time someone curates one more tool.
        payload = srv._tool_names_payload()
        self.assertTrue(payload["bounded"])
        self.assertTrue(payload["curated"])
        self.assertGreater(payload["total_available"], payload["returned_count"])
        # Materially smaller: a "bounded" catalog that returned 240 of 246 would
        # satisfy the assertion above while defeating the purpose.
        self.assertLess(payload["returned_count"], payload["total_available"] // 2)
        self.assertIn("bootstrap_session", payload["ca_next_action"])

    def test_bounded_and_curated_flags_describe_the_actual_payload(self):
        # Sabotage found both of these: hardcoding `bounded: True`, and letting
        # `curated: True` ride along with a plain alphabetical slice, both passed
        # the test above. A flag that can disagree with the list it describes is
        # worse than no flag -- the caller trusts it instead of counting.
        # Both directions. Checking only the default call cannot catch a
        # hardcoded `True`, because True is the right answer there -- the
        # mutation and the truth coincide. `limit=250` is the documented way to
        # ask for everything, and it is the one call where bounded must be False.
        for kwargs in ({}, {"limit": 250}):
            with self.subTest(call=kwargs or "default"):
                payload = srv._tool_names_payload(**kwargs)
                self.assertEqual(
                    payload["bounded"],
                    payload["returned_count"] < payload["total_available"],
                    "`bounded` must be derived from the counts, not asserted")
                self.assertEqual(payload["returned_count"], len(payload["tools"]))
        payload = srv._tool_names_payload()

        from orchestrator.core.registry_data import CURATED_CORE_TOOLS
        if payload["curated"]:
            names = {item["name"] for item in payload["tools"]}
            self.assertTrue(
                names <= set(CURATED_CORE_TOOLS),
                "`curated: true` must mean the tools ARE the curated set")

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

        compact = {item["name"]: item for item in payload["key_tools_compact"]}
        self.assertEqual(
            compact["bootstrap_session"]["required"],
            ["session_id", "agent_id", "owner_claim_id"],
        )
        self.assertEqual(
            compact["begin_task"]["required"],
            ["session_id", "prompt", "agent_id"],
        )
        self.assertIn("owner_capability", compact["begin_task"]["notes"])
        self.assertTrue(any("STALE_INDEX" in item for item in payload["common_pitfalls"]))

    def test_bootstrap_skills_index_carries_no_filesystem_paths(self):
        # A34: active_skills_index must identify skills by name only. Embedding an
        # absolute SKILL.md path makes the payload size depend on where the repo
        # lives on disk (JSON escapes every backslash), which is not a property
        # bootstrap should carry. Pin the behaviour: no entry may leak a path.
        payload = json.loads(asyncio.run(code_tools.bootstrap_session(
            "", session_id="host-1", agent_id="hermes",
            owner_claim_id="antidrift-host-1-0123456789abcdef0123456789abcdef",
        )))
        index = payload["active_skills_index"]
        self.assertGreater(len(index), 0)
        blob = json.dumps(index, ensure_ascii=False)
        self.assertNotIn("SKILL.md", blob)
        self.assertNotIn("/", blob)
        self.assertNotIn("\\", blob)
        # The one content consumer (ruleset_sha256) reads only the name, so a
        # name must still be recoverable from every entry.
        for entry in index:
            name = entry["name"] if isinstance(entry, dict) else entry
            self.assertTrue(name)

    def test_bootstrap_absolute_path_budget_is_pinned(self):
        # A34, second source. The test above pins one CONTAINER; the defect is a
        # property of the WHOLE payload -- its size must not depend on where the
        # repo sits on disk. Closing active_skills_index left exactly one leak:
        # rule.core_rule_path (96 chars here, and every backslash is JSON-escaped
        # to two). Pinning it at the payload level means a path re-entering under
        # ANY new key fails here, not just inside the skills list.
        #
        # This is a budget, not a ban: core_rule_path is an existing part of the
        # rule contract and removing it is a separate decision. What must not
        # happen silently is the budget growing again.
        payload = json.loads(asyncio.run(code_tools.bootstrap_session(
            "", session_id="host-1", agent_id="hermes",
            owner_claim_id="antidrift-host-1-0123456789abcdef0123456789abcdef",
        )))

        def leaves(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    yield from leaves(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    yield from leaves(v, f"{path}[{i}]")
            elif isinstance(obj, str) and re.match(r"^[A-Za-z]:[\\/]", obj):
                yield path, obj

        found = {p for p, _ in leaves(payload)}
        self.assertEqual(
            found, {".rule.core_rule_path"},
            f"absolute paths in bootstrap payload changed: {sorted(found)}")

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
