"""Contract tests for the search_code page_id catch-22 fix (2026-11-11).

INVARIANTS encoded here:
  1) Direct ``search_code(mode='dom_candidates')`` can never succeed -- the
     public schema rejects ``page_id`` while the dom branch requires it.
     The dispatcher gate ``_search_code_dom_gate`` therefore redirects the
     call BEFORE S4 to the single supported route, ``query_dom_candidates``
     with a live ``page_id`` (REDIRECT_TO_ALIAS, never DUPLICATE_RETRY_BLOCKED).
  2) A non-string ``mode`` gets a clean SCHEMA_VIOLATION from the gate
     instead of crashing in-function on ``mode.lower()``.
  3) The gate is direct-only: the ``query_dom_candidates`` alias, dispatched
     under its own tool name, never trips it.
  4) The public contract is narrowed at the publishing layer: the
     ``search_code`` mode enum and description no longer advertise
     ``dom_candidates``, and the alias description no longer calls itself a
     deprecated alias of ``search_code(mode=dom_candidates)``.
  5) LR-3: ``_missing_required_error`` for a ``page_id``-accepting tool
     carries a real ``corrected_payload`` (missing fields present, value
     None -- never a placeholder), a ``fill_instruction``, and a
     ``payload_example`` whose missing-field values are None. Other tools'
     payloads are unchanged.
"""

from __future__ import annotations

import json
import unittest


def _schema_params(schema: dict) -> dict:
    params = schema.get("parameters")
    if not params:
        params = (schema.get("function") or {}).get("parameters") or {}
    return params


def _schema_text(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False)


class TestSearchCodeDomGate(unittest.TestCase):
    def setUp(self) -> None:
        # Import inside setUp so a broken orchestrator_server import fails
        # fast for THIS test rather than silently at module-import time.
        import orchestrator_server  # noqa: F401

        self._server = orchestrator_server

    def gate(self, tool_name, body):
        return self._server._search_code_dom_gate(tool_name, body)

    def test_dom_candidates_mode_redirects_to_alias(self) -> None:
        out = self.gate("search_code", {"mode": "dom_candidates", "query": "x"})
        self.assertIsNotNone(out)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_name"], "REDIRECT_TO_ALIAS")
        self.assertEqual(out["correct_tool"], "query_dom_candidates")
        self.assertEqual(
            out["ca_next_action"], "call_query_dom_candidates_with_live_page_id"
        )
        self.assertIn("query_dom_candidates", out["error"])
        self.assertFalse(out["retryable"])
        self.assertNotIn("dom_candidates", out["accepted_modes"])

    def test_dom_candidates_with_page_id_still_redirects(self) -> None:
        out = self.gate(
            "search_code", {"mode": "dom_candidates", "page_id": "bogus-page"}
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["error_name"], "REDIRECT_TO_ALIAS")
        self.assertEqual(out["correct_tool"], "query_dom_candidates")

    def test_dom_spellings_and_aliases_redirect(self) -> None:
        for mode in ("dom", "dom_candidate", " DOM ", "Dom_Candidates"):
            with self.subTest(mode=mode):
                out = self.gate("search_code", {"mode": mode})
                self.assertIsNotNone(out, f"mode={mode!r} must redirect")
                self.assertEqual(out["error_name"], "REDIRECT_TO_ALIAS")

    def test_non_string_mode_is_clean_schema_violation(self) -> None:
        out = self.gate("search_code", {"mode": 123})
        self.assertIsNotNone(out)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_name"], "SCHEMA_VIOLATION")
        self.assertEqual(out["error"], "mode must be a string")
        self.assertEqual(out["ca_next_action"], "resend_with_string_mode")
        self.assertFalse(out["retryable"])
        self.assertNotIn("dom_candidates", out["accepted_modes"])

    def test_hyphenated_spelling_falls_through(self) -> None:
        self.assertIsNone(self.gate("search_code", {"mode": "dom-candidates"}))

    def test_gate_is_direct_only_alias_name_is_no_op(self) -> None:
        self.assertIsNone(
            self.gate("query_dom_candidates", {"mode": "dom_candidates"})
        )

    def test_text_mode_is_no_op(self) -> None:
        self.assertIsNone(self.gate("search_code", {"mode": "text"}))

    def test_absent_none_and_empty_mode_are_no_op(self) -> None:
        self.assertIsNone(self.gate("search_code", {"query": "x"}))
        self.assertIsNone(self.gate("search_code", {"mode": None}))
        self.assertIsNone(self.gate("search_code", {"mode": ""}))


class TestRegistryContractNarrowing(unittest.TestCase):
    def setUp(self) -> None:
        import orchestrator_server  # noqa: F401

        self._server = orchestrator_server

    def test_search_code_mode_enum_excludes_dom_candidates(self) -> None:
        schema = self._server._schema_for_tool("search_code")
        props = _schema_params(schema).get("properties") or {}
        enum = (props.get("mode") or {}).get("enum") or []
        self.assertNotIn("dom_candidates", enum)
        self.assertIn("text", enum)
        self.assertIn("smart", enum)

    def test_search_code_description_points_to_alias(self) -> None:
        schema = self._server._schema_for_tool("search_code")
        text = _schema_text(schema)
        self.assertNotIn("index|dom_candidates|smart", text)
        self.assertIn("query_dom_candidates", text)

    def test_alias_description_is_single_supported_route(self) -> None:
        schema = self._server._schema_for_tool("query_dom_candidates")
        text = _schema_text(schema)
        self.assertNotIn("deprecated alias", text)
        self.assertIn("single supported route", text)


class TestMissingRequiredPageIdShape(unittest.TestCase):
    def setUp(self) -> None:
        import orchestrator_server  # noqa: F401

        self._server = orchestrator_server

    def test_query_dom_candidates_missing_page_id_lr3_shape(self) -> None:
        out = self._server._missing_required_error(
            "query_dom_candidates", {}, ["page_id"]
        )
        self.assertEqual(out["error_name"], "MISSING_REQUIRED_FIELD")
        self.assertEqual(out["missing_fields"], ["page_id"])
        self.assertIn("corrected_payload", out)
        self.assertIn("page_id", out["corrected_payload"])
        self.assertIsNone(out["corrected_payload"]["page_id"])
        self.assertTrue(out.get("fill_instruction"))
        self.assertIn("page_id", out["payload_example"])
        self.assertIsNone(out["payload_example"]["page_id"])

    def test_other_tools_missing_payload_unchanged(self) -> None:
        out = self._server._missing_required_error(
            "git_status", {}, ["project_path"]
        )
        self.assertEqual(out["missing_fields"], ["project_path"])
        self.assertNotIn("corrected_payload", out)
        self.assertNotIn("fill_instruction", out)

    def test_corrected_payload_drops_post_s4_internal_fields(self) -> None:
        out = self._server._missing_required_error(
            "query_dom_candidates",
            {
                "project_path": "x",
                "filter": "all",
                "mode": "dom_candidates",
                "calling_context": {"a": 1},
            },
            ["page_id"],
        )
        self.assertIsNone(out["corrected_payload"]["page_id"])
        self.assertEqual(out["corrected_payload"]["filter"], "all")
        self.assertEqual(out["corrected_payload"]["project_path"], "x")
        self.assertNotIn("mode", out["corrected_payload"])
        self.assertNotIn("calling_context", out["corrected_payload"])


if __name__ == "__main__":
    unittest.main()
