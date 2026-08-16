import json
import unittest

import orchestrator_server as srv


class AntiDriftContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        srv._ANTI_DRIFT_CALL_STATE.clear()

    def test_common_line_range_aliases_preserve_intent(self):
        for tool in ("read_file_range", "write_fix", "write_fix_html"):
            body, applied = srv._normalize_body(
                tool,
                {"line_start": 7, "line_end": 11},
            )
            self.assertEqual(body["start_line"], 7)
            self.assertEqual(body["end_line"], 11)
            self.assertIn("line_start->start_line", applied)
            self.assertIn("line_end->end_line", applied)

    def test_unfiltered_tool_catalog_is_bounded(self):
        payload = srv._tool_names_payload()
        self.assertTrue(payload["bounded"])
        # Anti-drift ceiling: the unfiltered default returns the curated core SOT
        # (registry_data.CURATED_CORE_TOOLS), NOT the full ~246-tool catalog. The
        # curated set is a hand-governed list currently at 34 entries; 40 leaves a
        # small buffer for governed curation while staying far below total_available,
        # so the guard still fails loudly if the full catalog is ever dumped.
        self.assertLessEqual(payload["returned_count"], 40)
        self.assertLess(payload["returned_count"] * 3, payload["total_available"])
        self.assertGreater(payload["total_available"], payload["returned_count"])
        self.assertEqual(
            payload["ca_next_action"], "filter_or_limit_250_or_bootstrap_session"
        )

    def test_filtered_tool_catalog_returns_matches(self):
        payload = srv._tool_names_payload(name_contains="search_code")
        self.assertEqual([item["name"] for item in payload["tools"]], ["search_code"])
        self.assertTrue(payload["filtered"])

    def test_unknown_tool_does_not_dump_full_catalog(self):
        payload, status = srv._tool_schema_payload("definitely_missing_tool")
        self.assertEqual(status, 404)
        self.assertNotIn("available", payload)
        self.assertLessEqual(len(payload["suggestions"]), 8)
        self.assertEqual(payload["ca_next_action"], "filter_tool_names")

    def test_duplicate_deterministic_failure_is_blocked(self):
        meta = {"agent_id": "hermes", "session_id": "s1", "task_epoch": "2"}
        first = srv._anti_drift_duplicate_failure(
            "search_code", {"bad": 1}, "UNSUPPORTED_FIELD", meta
        )
        second = srv._anti_drift_duplicate_failure(
            "search_code", {"bad": 1}, "UNSUPPORTED_FIELD", meta
        )
        self.assertIsNone(first)
        self.assertEqual(second["error_name"], "DUPLICATE_RETRY_BLOCKED")
        self.assertFalse(second["retryable"])

    def test_new_task_epoch_resets_duplicate_failure_state(self):
        base = {"agent_id": "ope", "session_id": "s1"}
        self.assertIsNone(srv._anti_drift_duplicate_failure(
            "search_code", {"bad": 1}, "UNSUPPORTED_FIELD", {**base, "task_epoch": "1"}
        ))
        self.assertIsNone(srv._anti_drift_duplicate_failure(
            "search_code", {"bad": 1}, "UNSUPPORTED_FIELD", {**base, "task_epoch": "2"}
        ))

    def test_large_result_is_bounded_with_continuation_metadata(self):
        bounded = srv._anti_drift_bound_payload(
            {"ok": True, "content": "x" * 50_000},
            max_bytes=2_000,
        )
        encoded = json.dumps(bounded, ensure_ascii=False).encode("utf-8")
        self.assertLessEqual(len(encoded), 2_500)
        self.assertTrue(bounded["truncated"])
        self.assertIn("content_ref", bounded)
        self.assertEqual(bounded["ca_next_action"], "read_bounded_continuation")


if __name__ == "__main__":
    unittest.main()
