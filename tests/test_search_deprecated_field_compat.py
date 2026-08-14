"""CAP R3: search_code deprecated-field compatibility regression tests.

Evidence gate coverage:
1. {query, limit} valid -> canonicalized success (compatibility_applied:true)
2. {query, path_glob} -> canonicalized success
3. {query, literal_search:true/false} preserves text/regex meaning
4. Conflicting legacy/canonical fields -> deterministic ambiguity error
5. Genuinely unknown field remains UNSUPPORTED_FIELD
6. Repeating identical deterministic payload does NOT cause multiplicative retries
7. Metrics distinguish mapped use vs ambiguous rejection vs truly-unknown
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import orchestrator_server as srv


def _make_search_project() -> str:
    """Create a minimal project with searchable content."""
    tmp = tempfile.mkdtemp(prefix="cap_r3_test_")
    root = Path(tmp)
    (root / "sample.py").write_text(
        "def hello_world():\n"
        "    return 'hello'\n"
        "\n"
        "class Widget:\n"
        "    def render(self):\n"
        "        return 'rendered'\n",
        encoding="utf-8",
    )
    (root / "data.js").write_text(
        "const greeting = 'hello';\n"
        "export default greeting;\n",
        encoding="utf-8",
    )
    return str(root)


class TestSearchDeprecatedFieldCompat(unittest.IsolatedAsyncioTestCase):
    """Server-side deprecated-field compatibility for search_code."""

    def setUp(self) -> None:
        self.project_path = _make_search_project()
        # Isolate duplicate-retry guard: tests share transport="test" scope,
        # so identical payloads across tests would trigger DUPLICATE_RETRY_BLOCKED.
        srv._DUPLICATE_FAILURE_STATE.clear()
        # Snapshot metrics before each test
        self._use_before = dict(srv._COMPAT_USE_TOTAL)
        self._amb_before = dict(srv._COMPAT_AMBIGUOUS_TOTAL)
        self._unk_before = dict(srv._COMPAT_UNKNOWN_TOTAL)

    def _metric_delta(self, store: dict, before: dict) -> dict:
        delta = {}
        for k, v in store.items():
            diff = v - before.get(k, 0)
            if diff > 0:
                delta[k] = diff
        return delta

    async def _dispatch(self, body: dict):
        """Dispatch search_code through the full server pipeline."""
        return await srv.dispatch_tool_call("search_code", body, transport="test")

    # -- 1. limit -> max_results canonicalization --

    async def test_limit_canonicalized_to_max_results(self):
        """Valid limit maps to max_results with compatibility metadata."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "limit": 3,
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 200)
        self.assertTrue(payload.get("compatibility_applied"))
        self.assertIn("limit", payload.get("deprecated_fields", []))
        self.assertEqual(payload.get("canonical_replacements", {}).get("limit"), "max_results")
        self.assertEqual(payload.get("removal_version"), "v16.0")
        # Verify actual search ran with bounded results
        self.assertIn("results", payload)

    async def test_limit_equal_to_max_results_canonicalized(self):
        """limit == max_results (both present, equal) -> canonicalize silently."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "limit": 5,
            "max_results": 5,
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 200)
        self.assertTrue(payload.get("compatibility_applied"))

    async def test_limit_conflicts_with_max_results(self):
        """limit != max_results -> deterministic ambiguity error."""
        resp = await self._dispatch({
            "query": "unique_conflict_limit_1",
            "project_path": self.project_path,
            "limit": 3,
            "max_results": 10,
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload.get("error_name"), "DEPRECATED_FIELD_CONFLICT")
        self.assertEqual(payload.get("deprecated_field"), "limit")
        self.assertFalse(payload.get("retryable", True))

    async def test_limit_invalid_type_rejected(self):
        """limit as string -> ambiguity error (not silently coerced)."""
        resp = await self._dispatch({
            "query": "unique_conflict_limit_type_1",
            "project_path": self.project_path,
            "limit": "5",
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload.get("error_name"), "DEPRECATED_FIELD_CONFLICT")

    async def test_limit_out_of_range_rejected(self):
        """limit > MAX_SEARCH_RESULTS_CAP -> ambiguity error."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "limit": 999,
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload.get("error_name"), "DEPRECATED_FIELD_CONFLICT")

    # -- 2. path_glob -> file_pattern canonicalization --

    async def test_path_glob_canonicalized_to_file_pattern(self):
        """Valid path_glob maps to file_pattern with compatibility metadata."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "path_glob": "*.py",
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 200)
        self.assertTrue(payload.get("compatibility_applied"))
        self.assertIn("path_glob", payload.get("deprecated_fields", []))
        self.assertEqual(payload.get("canonical_replacements", {}).get("path_glob"), "file_pattern")

    async def test_path_glob_conflicts_with_file_pattern(self):
        """path_glob != file_pattern -> deterministic ambiguity error."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "path_glob": "*.py",
            "file_pattern": "*.js",
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload.get("error_name"), "DEPRECATED_FIELD_CONFLICT")
        self.assertEqual(payload.get("deprecated_field"), "path_glob")

    async def test_path_glob_empty_string_rejected(self):
        """Empty path_glob -> ambiguity error."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "path_glob": "",
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload.get("error_name"), "DEPRECATED_FIELD_CONFLICT")

    # -- 3. literal_search -> mode canonicalization --

    async def test_literal_search_true_maps_to_text_mode(self):
        """literal_search:true -> mode=text."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "literal_search": True,
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 200)
        self.assertTrue(payload.get("compatibility_applied"))
        self.assertIn("literal_search", payload.get("deprecated_fields", []))
        self.assertEqual(payload.get("mode"), "text")

    async def test_literal_search_false_maps_to_regex_mode(self):
        """literal_search:false -> mode=regex."""
        resp = await self._dispatch({
            "query": "hel+o",
            "project_path": self.project_path,
            "literal_search": False,
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 200)
        self.assertTrue(payload.get("compatibility_applied"))
        self.assertEqual(payload.get("mode"), "regex")

    async def test_literal_search_conflicts_with_explicit_mode(self):
        """literal_search:true + mode=regex -> ambiguity error."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "literal_search": True,
            "mode": "regex",
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload.get("error_name"), "DEPRECATED_FIELD_CONFLICT")
        self.assertEqual(payload.get("deprecated_field"), "literal_search")

    async def test_literal_search_false_conflicts_with_text_mode(self):
        """literal_search:false + mode=text -> ambiguity error."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "literal_search": False,
            "mode": "text",
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload.get("error_name"), "DEPRECATED_FIELD_CONFLICT")

    async def test_literal_search_true_conflicts_with_regex_alias(self):
        """literal_search:true + pattern alias -> ambiguity error."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "literal_search": True,
            "pattern": "hel.*o",
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload.get("error_name"), "DEPRECATED_FIELD_CONFLICT")

    async def test_literal_search_non_boolean_rejected(self):
        """literal_search as string -> ambiguity error."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "literal_search": "true",
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload.get("error_name"), "DEPRECATED_FIELD_CONFLICT")

    # -- 4. Unknown fields still hit S4 UNSUPPORTED_FIELD --

    async def test_unknown_field_remains_unsupported(self):
        """A genuinely unknown field is NOT swallowed by compat."""
        resp = await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "totally_bogus_field": "value",
        })
        payload = json.loads(resp.text)
        self.assertEqual(resp.status, 400)
        self.assertEqual(payload.get("error_name"), "UNSUPPORTED_FIELD")
        self.assertIn("totally_bogus_field", payload.get("unsupported_fields", []))

    # -- 5. Duplicate retry does NOT multiply --

    async def test_duplicate_conflict_payload_blocked(self):
        """Repeating identical conflicting payload -> DUPLICATE_RETRY_BLOCKED."""
        body = {
            "query": "hello",
            "project_path": self.project_path,
            "limit": 3,
            "max_results": 10,
        }
        # First call: DEPRECATED_FIELD_CONFLICT
        resp1 = await self._dispatch(dict(body))
        payload1 = json.loads(resp1.text)
        self.assertEqual(payload1.get("error_name"), "DEPRECATED_FIELD_CONFLICT")

        # Second identical call: DUPLICATE_RETRY_BLOCKED (409)
        resp2 = await self._dispatch(dict(body))
        payload2 = json.loads(resp2.text)
        self.assertEqual(resp2.status, 409)
        self.assertEqual(payload2.get("error_name"), "DUPLICATE_RETRY_BLOCKED")

    # -- 6. Metrics distinguish categories --

    async def test_metrics_use_vs_ambiguous_vs_unknown(self):
        """Metrics counters track mapped, ambiguous, and unknown separately."""
        # Mapped use
        await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "limit": 5,
        })
        use_delta = self._metric_delta(srv._COMPAT_USE_TOTAL, self._use_before)
        self.assertIn(("search_code", "limit"), use_delta)

        # Ambiguous rejection
        await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "limit": 3,
            "max_results": 10,
        })
        amb_delta = self._metric_delta(srv._COMPAT_AMBIGUOUS_TOTAL, self._amb_before)
        self.assertIn(("search_code", "limit"), amb_delta)

        # Truly unknown
        await self._dispatch({
            "query": "hello",
            "project_path": self.project_path,
            "nonexistent_xyz": 1,
        })
        unk_delta = self._metric_delta(srv._COMPAT_UNKNOWN_TOTAL, self._unk_before)
        self.assertIn(("search_code", "nonexistent_xyz"), unk_delta)


class TestSearchCompatUnitLogic(unittest.TestCase):
    """Unit tests for _search_code_deprecated_compat pure logic."""

    def test_limit_maps_to_max_results(self):
        body = {"query": "x", "limit": 10}
        result, meta = srv._search_code_deprecated_compat(body)
        self.assertEqual(result["max_results"], 10)
        self.assertNotIn("limit", result)
        self.assertTrue(meta["compatibility_applied"])
        self.assertEqual(meta["canonical_replacements"]["limit"], "max_results")

    def test_path_glob_maps_to_file_pattern(self):
        body = {"query": "x", "path_glob": "*.ts"}
        result, meta = srv._search_code_deprecated_compat(body)
        self.assertEqual(result["file_pattern"], "*.ts")
        self.assertNotIn("path_glob", result)

    def test_literal_search_true_maps_to_text(self):
        body = {"query": "x", "literal_search": True}
        result, meta = srv._search_code_deprecated_compat(body)
        self.assertEqual(result["mode"], "text")
        self.assertNotIn("literal_search", result)

    def test_literal_search_false_maps_to_regex(self):
        body = {"query": "x", "literal_search": False}
        result, meta = srv._search_code_deprecated_compat(body)
        self.assertEqual(result["mode"], "regex")

    def test_no_deprecated_fields_returns_empty_meta(self):
        body = {"query": "x", "max_results": 5}
        result, meta = srv._search_code_deprecated_compat(body)
        self.assertEqual(meta, {})

    def test_conflict_raises(self):
        body = {"query": "x", "limit": 3, "max_results": 10}
        with self.assertRaises(srv._DeprecatedFieldConflict):
            srv._search_code_deprecated_compat(body)


if __name__ == "__main__":
    unittest.main()
