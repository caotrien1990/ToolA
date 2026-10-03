"""Regression test LIVE-GAP-01 (2026-08-10): the dispatcher must inject the
internal ``mode`` for the search_code alias tools (find_callers,
find_definition_and_usage, query_dom_candidates) AFTER public-schema
validation (S4), not inside ``_normalize_body``.

WHY: 0443b67 added the injection inside ``_normalize_body``. S4 runs AFTER
normalize and rejects any body key that is not a public-schema property.
None of the alias tools declare ``mode`` in their public schema (it is
internal delegation metadata consumed by ``search_code``'s branch dispatch),
so S4 returned ``UNSUPPORTED_FIELD: mode`` for every schema-valid alias
call. The live ``find_callers`` alias was therefore broken on the server
even though direct ``search_code(mode='callers')`` worked — verified live
against the running orchestrator on 2026-08-10.

INVARIANTS encoded here:
  1) ``_normalize_body`` must NOT inject ``mode`` for these aliases (that
     was the 0443b67 bug). The injected field reaches S4 and is rejected.
  2) The dispatcher-side helper ``_apply_alias_mode_default_post_s4`` MUST
     inject the documented mode when the caller did not supply one, and
     MUST preserve an explicit caller-supplied ``mode`` (defensive; S4
     rejects it in practice before the helper runs).
  3) A caller-supplied ``mode`` on ``find_callers`` must still be rejected
     by S4 — the alias's public contract deliberately does not expose
     ``mode``. This is asserted via the registry schema, mirroring the S4
     check: ``mode`` is not in the alias's allowed params.
"""

from __future__ import annotations

import unittest


class TestFindCallersAliasModeContract(unittest.TestCase):
    def setUp(self) -> None:
        # Import inside setUp so a broken orchestrator_server import fails
        # fast for THIS test rather than silently at module-import time.
        import orchestrator_server  # noqa: F401

        self._server = orchestrator_server

    # ── Invariant 1: _normalize_body must NOT inject mode ─────────────────
    def test_normalize_body_does_not_inject_mode_for_find_callers(self) -> None:
        body, _applied = self._server._normalize_body(
            "find_callers",
            {"symbol": "my_func", "project_path": r"D:\x"},
        )
        self.assertNotIn(
            "mode",
            body,
            f"_normalize_body must NOT inject mode (LIVE-GAP-01); got body={body}",
        )

    def test_normalize_body_does_not_inject_mode_for_find_definition_and_usage(
        self,
    ) -> None:
        body, _applied = self._server._normalize_body(
            "find_definition_and_usage",
            {"query": "my_func", "project_path": r"D:\x"},
        )
        self.assertNotIn(
            "mode",
            body,
            f"_normalize_body must NOT inject mode (LIVE-GAP-01); got body={body}",
        )

    def test_normalize_body_does_not_inject_mode_for_query_dom_candidates(
        self,
    ) -> None:
        body, _applied = self._server._normalize_body(
            "query_dom_candidates",
            {"page_id": "p1"},
        )
        self.assertNotIn(
            "mode",
            body,
            f"_normalize_body must NOT inject mode (LIVE-GAP-01); got body={body}",
        )

    # ── Invariant 2: helper injects mode post-S4 ──────────────────────────
    def test_helper_injects_mode_callers_for_find_callers(self) -> None:
        body = {"symbol": "my_func", "project_path": r"D:\x"}
        applied = self._server._apply_alias_mode_default_post_s4(
            "find_callers", body, []
        )
        self.assertEqual(body.get("mode"), "callers")
        self.assertTrue(
            any("callers" in a for a in applied),
            f"applied must record the injection; got {applied}",
        )

    def test_helper_injects_mode_definition_usage_for_find_definition_and_usage(
        self,
    ) -> None:
        body = {"query": "my_func", "project_path": r"D:\x"}
        self._server._apply_alias_mode_default_post_s4(
            "find_definition_and_usage", body, []
        )
        self.assertEqual(body.get("mode"), "definition_usage")

    def test_helper_injects_mode_dom_candidates_for_query_dom_candidates(
        self,
    ) -> None:
        body = {"page_id": "p1"}
        self._server._apply_alias_mode_default_post_s4(
            "query_dom_candidates", body, []
        )
        self.assertEqual(body.get("mode"), "dom_candidates")

    def test_helper_preserves_explicit_caller_supplied_mode(self) -> None:
        # Defensive: in practice S4 rejects caller-supplied mode before the
        # helper runs, but the helper itself must not silently overwrite.
        body = {"symbol": "my_func", "mode": "symbol"}
        applied = self._server._apply_alias_mode_default_post_s4(
            "find_callers", body, []
        )
        self.assertEqual(body.get("mode"), "symbol")
        self.assertEqual(applied, [])

    def test_helper_is_a_no_op_for_non_alias_tools(self) -> None:
        body = {"query": "x", "project_path": r"D:\x"}
        applied = self._server._apply_alias_mode_default_post_s4(
            "search_code", body, []
        )
        self.assertNotIn("mode", body)
        self.assertEqual(applied, [])

    # ── P19: find_symbol must route to the SYMBOL lane, not text ─────────
    def test_helper_injects_mode_symbol_for_find_symbol(self) -> None:
        """P19 root fix: the find_symbol TOOL is registered as
        ('find_symbol', 'search_code') in BASE_TOOL_MAP_SPECS, so without a
        dispatcher-side mode default it ran search_code with mode='text' --
        a whole-repo text scan that returned call sites alongside the
        definition and, on a 0-hit over native drift, paid the whole-repo
        cache_delta probe + P15 reconcile (measured 250s on 49. CUA).
        The alias must gain mode='symbol' exactly like find_callers gains
        mode='callers'."""
        body = {"name": "my_func", "project_path": r"D:\x"}
        applied = self._server._apply_alias_mode_default_post_s4(
            "find_symbol", body, []
        )
        self.assertEqual(
            body.get("mode"),
            "symbol",
            f"find_symbol must default to mode='symbol' (P19); got body={body}",
        )
        self.assertTrue(
            any("symbol" in a for a in applied),
            f"applied must record the injection; got {applied}",
        )

    def test_find_symbol_public_schema_does_not_expose_mode(self) -> None:
        """Same contract as the other aliases: mode stays internal
        delegation metadata, so S4 keeps rejecting a caller-supplied mode
        and the post-S4 default is the only door."""
        schema = self._server._schema_for_tool("find_symbol")
        props = (schema.get("parameters") or {}).get("properties") or {}
        self.assertNotIn(
            "mode",
            props,
            f"find_symbol public schema must not declare 'mode'; got props={sorted(props)}",
        )

    # ── Invariant 3: user-supplied mode is still rejected by S4 ───────────
    def test_find_callers_public_schema_does_not_expose_mode(self) -> None:
        """Mirror of the S4 check: the find_callers registry schema must not
        declare ``mode`` as a public property. If a future change adds it,
        this test fails — that is intentional, because the alias would then
        leak an internal delegation knob to the public contract and the S4
        rejection invariant (a user-supplied ``mode`` is rejected) would no
        longer hold.
        """
        schema = self._server._schema_for_tool("find_callers")
        props = (schema.get("parameters") or {}).get("properties") or {}
        self.assertNotIn(
            "mode",
            props,
            f"find_callers public schema must not declare 'mode'; got props={sorted(props)}",
        )

    def test_find_definition_and_usage_public_schema_does_not_expose_mode(
        self,
    ) -> None:
        schema = self._server._schema_for_tool("find_definition_and_usage")
        props = (schema.get("parameters") or {}).get("properties") or {}
        self.assertNotIn("mode", props)

    def test_query_dom_candidates_public_schema_does_not_expose_mode(self) -> None:
        schema = self._server._schema_for_tool("query_dom_candidates")
        props = (schema.get("parameters") or {}).get("properties") or {}
        self.assertNotIn("mode", props)


if __name__ == "__main__":
    unittest.main()
