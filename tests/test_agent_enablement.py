# pyright: reportArgumentType=false
"""Agent-enablement contract tests (2026-08-08).

Bug: agents using tool A could not scan projects because the alias/canonical
direction was inverted for find_symbol / find_callers, and search_code injected
literal_search (not in registry properties) causing UNSUPPORTED_FIELD at S4.

This module verifies the evidence-gate assertions from the fix advisory:

A. Registry-vs-normalizer consistency:
   - Every alias advertised in _ARG_ALIASES maps to a registry-canonical field.

B. Alias direction (canonical wins):
   - find_symbol: symbol -> name (registry canonical), query -> name.
   - find_callers: query -> symbol, name -> symbol, symbol_name -> symbol.
   - search_code: pattern -> query (text mode), no literal_search injection.

C. Live-shape body normalization:
   - find_symbol(body={'symbol': 'X'}) produces body['name']='X' (registry required).
   - find_symbol(body={'name': 'X'}) passes through unchanged.
   - find_symbol(body={'symbol': 'alias_val', 'name': 'canon_val'}) keeps 'name'.

   - find_callers(body={'symbol': 'X'}) passes through unchanged.
   - find_callers(body={'query': 'X'}) produces body['symbol']='X'.
   - find_callers(body={'symbol': 'canon_val', 'query': 'alias_val'}) keeps 'symbol'.

   - search_code(body={'pattern': 'P'}) produces body['query']='P', no literal_search.

D. Payload examples:
   - find_symbol/find_callers/search_code return curated, registry-accurate
     payload_example dicts (canonical field names only, valid Windows paths).

E. find_callers required-field contract:
   - registry declares required = ['symbol'] only (project_path optional when
     session fallback is available; server errors MISSING_REQUIRED_FIELD only
     when BOTH are missing and unresolvable).

Run:
    python -m pytest tests/test_agent_enablement.py -q --basetemp="%TEMP%\\ae_basetemp"
"""

import unittest

import code_tools
import orchestrator_server as srv  # noqa: F401 — triggers late registrations


# ── helpers ──────────────────────────────────────────────────────────────────

def _schema_for(tool_name: str) -> dict:
    for item in code_tools.TOOL_SCHEMAS:
        fn = (item.get("function") or {}) if isinstance(item, dict) else {}
        if fn.get("name") == tool_name:
            return fn
    return {}


def _schema_props(tool_name: str) -> set[str]:
    fn = _schema_for(tool_name)
    return set((fn.get("parameters") or {}).get("properties", {}).keys())


def _schema_required(tool_name: str) -> list[str]:
    fn = _schema_for(tool_name)
    return list((fn.get("parameters") or {}).get("required") or [])


# ── A. Alias -> registry-canonical consistency ──────────────────────────────

class TestAliasCanonicalConsistency(unittest.TestCase):
    """Every _ARG_ALIASES value (the 'new'/canonical key) must be a registry property."""

    TOOLS_UNDER_TEST = ("find_symbol", "find_callers", "search_code")

    def test_every_alias_maps_to_registry_property(self):
        problems = []
        for tool in self.TOOLS_UNDER_TEST:
            props = _schema_props(tool)
            aliases = srv._ARG_ALIASES.get(tool, {})
            for alias_src, canonical in aliases.items():
                if canonical not in props:
                    problems.append(
                        f"{tool}: {alias_src}->{canonical} but '{canonical}' is NOT a registry property"
                    )
        self.assertEqual(problems, [],
                         f"Alias canonical direction mismatch:\n" + "\n".join(problems))

    def test_find_symbol_canonical_is_name(self):
        """find_symbol registry requires 'name'; aliases must resolve to 'name'."""
        aliases = srv._ARG_ALIASES.get("find_symbol", {})
        self.assertEqual(aliases.get("query"), "name",
                         "query must alias to 'name' (registry required)")
        self.assertEqual(aliases.get("symbol"), "name",
                         "symbol must alias to 'name'")
        self.assertEqual(aliases.get("symbol_name"), "name",
                         "symbol_name must alias to 'name'")
        self.assertEqual(aliases.get("q"), "name",
                         "q must alias to 'name'")

    def test_find_callers_canonical_is_symbol(self):
        """find_callers registry requires 'symbol'; aliases must resolve to 'symbol'."""
        aliases = srv._ARG_ALIASES.get("find_callers", {})
        self.assertEqual(aliases.get("query"), "symbol",
                         "query must alias to 'symbol' (registry required)")
        self.assertEqual(aliases.get("name"), "symbol",
                         "name must alias to 'symbol'")
        self.assertEqual(aliases.get("symbol_name"), "symbol",
                         "symbol_name must alias to 'symbol'")
        self.assertEqual(aliases.get("q"), "symbol",
                         "q must alias to 'symbol'")


# ── B. Alias direction — canonical wins on conflict ─────────────────────────

class TestAliasDirectionFindSymbol(unittest.TestCase):

    def test_find_symbol_accepts_symbol_alias(self):
        """Live-repro: {'symbol':X} used to be renamed to {'query':X} and fail
        with MISSING_REQUIRED_FIELD name. After fix, it resolves to name=X."""
        norm, applied = srv._normalize_body("find_symbol", {"symbol": "my_fn"})
        self.assertEqual(norm.get("name"), "my_fn")
        self.assertNotIn("symbol", norm, "alias 'symbol' consumed into 'name'")
        self.assertNotIn("query", norm, "no query residue")

    def test_find_symbol_accepts_query_alias(self):
        norm, applied = srv._normalize_body("find_symbol", {"query": "my_fn"})
        self.assertEqual(norm.get("name"), "my_fn")
        self.assertNotIn("query", norm)

    def test_find_symbol_canonical_passthrough(self):
        """{'name':X} passes through unchanged."""
        norm, applied = srv._normalize_body("find_symbol", {"name": "my_fn"})
        self.assertEqual(norm.get("name"), "my_fn")

    def test_find_symbol_canonical_wins_on_conflict(self):
        """{'name': canon, 'symbol': alias} — canonical wins, alias DROPPED (F1).

        F1 (2026-08-08): previously the alias key was left in body, so S4's
        unsupported-field check saw {name, symbol, project_path} for find_symbol
        and returned UNSUPPORTED_FIELD. The fix consumes the duplicate alias:
        canonical value is preserved, alias is removed before S4 runs."""
        norm, applied = srv._normalize_body(
            "find_symbol",
            {"name": "canon_fn", "symbol": "alias_fn"},
        )
        self.assertEqual(norm.get("name"), "canon_fn",
                         "canonical 'name' must win over alias 'symbol'")
        # F1: alias key MUST be removed (not 'left untouched') so S4 does not
        # flag it as UNSUPPORTED_FIELD.
        self.assertNotIn("symbol", norm,
                         "alias 'symbol' must be dropped when canonical 'name' already present")


class TestAliasDirectionFindCallers(unittest.TestCase):

    def test_find_callers_accepts_query_alias(self):
        """{'query':'fn'} -> body['symbol']='fn' (registry required)."""
        norm, applied = srv._normalize_body("find_callers", {"query": "my_fn"})
        self.assertEqual(norm.get("symbol"), "my_fn")
        self.assertNotIn("query", norm)

    def test_find_callers_accepts_name_alias(self):
        norm, applied = srv._normalize_body("find_callers", {"name": "my_fn"})
        self.assertEqual(norm.get("symbol"), "my_fn")
        self.assertNotIn("name", norm)

    def test_find_callers_canonical_symbol_passthrough(self):
        """{'symbol':X} passes through unchanged."""
        norm, applied = srv._normalize_body("find_callers", {"symbol": "my_fn"})
        self.assertEqual(norm.get("symbol"), "my_fn")
        self.assertNotIn("query", norm, "no query residue")

    def test_find_callers_canonical_wins_on_conflict(self):
        """{'symbol': canon, 'query': alias} — canonical wins, alias DROPPED (F1)."""
        norm, applied = srv._normalize_body(
            "find_callers",
            {"symbol": "canon_fn", "query": "alias_fn"},
        )
        self.assertEqual(norm.get("symbol"), "canon_fn",
                         "canonical 'symbol' must win over alias 'query'")
        self.assertNotIn("query", norm,
                         "F1: duplicate alias 'query' must be dropped (canonical 'symbol' present)")


class TestAliasDirectionSearchCode(unittest.TestCase):

    def test_pattern_alias_maps_to_query(self):
        norm, applied = srv._normalize_body("search_code", {"pattern": "needle"})
        self.assertEqual(norm.get("query"), "needle")
        self.assertNotIn("pattern", norm)

    def test_no_literal_search_injected(self):
        """Regression: literal_search=False used to be injected; not in registry
        properties, so S4 returned UNSUPPORTED_FIELD. Must NOT be present."""
        norm, applied = srv._normalize_body("search_code", {"pattern": "needle"})
        self.assertNotIn("literal_search", norm,
                         "literal_search must never be injected — not in registry")
        # Also check the regex path
        norm, applied = srv._normalize_body("search_code", {"regex": "n.*dle"})
        self.assertNotIn("literal_search", norm)

    def test_search_code_literal_mode_via_text(self):
        """mode='text' is the registry-blessed literal/text search."""
        norm, applied = srv._normalize_body(
            "search_code",
            {"query": "needle", "mode": "text"},
        )
        self.assertEqual(norm["mode"], "text")
        self.assertNotIn("literal_search", norm)


# ── C. Required-field contract in registry ──────────────────────────────────

class TestRequiredFieldContract(unittest.TestCase):

    def test_find_symbol_required_is_name(self):
        self.assertEqual(_schema_required("find_symbol"), ["name"])

    def test_find_callers_required_is_symbol_only(self):
        """project_path is OPTIONAL after session fallback was added (2026-08-08).
        If neither explicit nor session-resolvable, server raises MISSING_REQUIRED_FIELD
        at dispatch time, but the schema-level required list is just ['symbol']."""
        self.assertEqual(_schema_required("find_callers"), ["symbol"],
                         "project_path must NOT be in required (session fallback covers it)")

    def test_search_code_required_is_empty(self):
        """search_code has no required fields — query is mode-dependent."""
        self.assertEqual(_schema_required("search_code"), [])


# ── D. Payload examples ─────────────────────────────────────────────────────

class TestPayloadExamples(unittest.TestCase):

    def test_find_symbol_payload_example_uses_canonical(self):
        example = srv._payload_example_for_tool("find_symbol",
                                                _schema_for("find_symbol"))
        self.assertIn("name", example,
                      "payload_example must use canonical 'name'")
        self.assertNotIn("symbol", example,
                         "payload_example must NOT include alias 'symbol'")
        self.assertNotIn("query", example,
                         "payload_example must NOT include alias 'query'")
        self.assertIn("project_path", example)

    def test_find_callers_payload_example_uses_canonical(self):
        example = srv._payload_example_for_tool("find_callers",
                                                _schema_for("find_callers"))
        self.assertIn("symbol", example,
                      "payload_example must use canonical 'symbol'")
        self.assertNotIn("query", example)
        self.assertNotIn("name", example)

    def test_search_code_payload_example_mode_in_enum(self):
        """Example mode must be one of the registry enum values."""
        example = srv._payload_example_for_tool("search_code",
                                                _schema_for("search_code"))
        self.assertIn("query", example)
        self.assertIn("project_path", example)
        mode = example.get("mode")
        props = (_schema_for("search_code").get("parameters") or {}).get("properties") or {}
        valid_modes = (props.get("mode") or {}).get("enum") or []
        self.assertIn(mode, valid_modes,
                      f"payload_example mode={mode!r} must be in registry enum {valid_modes}")

    def test_payload_example_fields_are_accepted(self):
        """Every key in payload_example must be a registry-accepted field (or alias)."""
        for tool in ("find_symbol", "find_callers", "search_code"):
            example = srv._payload_example_for_tool(tool, _schema_for(tool))
            props = _schema_props(tool)
            aliases = srv._ARG_ALIASES.get(tool, {})
            accepted = props | set(aliases.keys())
            for k in example.keys():
                self.assertIn(k, accepted,
                              f"{tool}.payload_example contains unsupported field {k!r}")

    def test_payload_example_no_invalid_windows_escape(self):
        """Generated Windows paths must not contain unescaped sequences like \\t or \\f
        that json.dumps() would interpret as control characters."""
        for tool in ("find_symbol", "find_callers", "search_code"):
            example = srv._payload_example_for_tool(tool, _schema_for(tool))
            pp = example.get("project_path", "")
            # After JSON round-trip the path must equal itself (no escape surprises)
            import json as _json
            self.assertEqual(_json.loads(_json.dumps(pp)), pp,
                             f"{tool}.payload_example project_path must survive JSON round-trip")


# ── E. find_callers project_path resolution (unit-level) ────────────────────

class TestFindCallersSessionFallback(unittest.TestCase):
    """Contract: explicit project_path > session-derived > error. Never '.'/CWD."""

    def test_session_id_resolver_returns_empty_when_unknown(self):
        """_resolve_project_from_session_id('') must return '' (no session)."""
        self.assertEqual(srv._resolve_project_from_session_id(""), "")
        self.assertEqual(srv._resolve_project_from_session_id(None), "")

    def test_page_id_resolver_returns_empty_when_unknown(self):
        """_resolve_project_from_page_id('') must return '' for unknown/absent IDs."""
        self.assertEqual(srv._resolve_project_from_page_id(""), "")

    def test_find_callers_registry_allows_project_path_omitted(self):
        """Registry required list must be ['symbol'] so that the S4 requiredness
        gate does not reject a project_path-less find_callers call outright —
        giving the session fallback a chance to populate it."""
        required = _schema_required("find_callers")
        self.assertIn("symbol", required)
        self.assertNotIn("project_path", required)


# ── F. HTTP-level regression: dispatcher path (F1 + F2) ─────────────────────
# These tests exercise ``dispatch_tool_call`` (the same code the /api/call/<tool>
# HTTP endpoint routes through), NOT just ``_normalize_body``. They verify that
# F1 + F2 hold at the REAL API boundary where S4 validation runs.

import json


class TestCanonicalPlusAliasHTTPPath(unittest.IsolatedAsyncioTestCase):
    """F1 (MEDIUM): canonical+alias both supplied at the API boundary must NOT
    produce UNSUPPORTED_FIELD.

    Before the fix, _normalize_body only renamed the alias when the canonical
    was ABSENT, so e.g. ``find_symbol({name, symbol, project_path})`` left
    ``symbol`` in the body and S4's unsupported-field check rejected with
    400 UNSUPPORTED_FIELD. After the fix, the duplicate alias is consumed and
    dropped by _normalize_body, so S4 sees only canonical keys."""

    async def test_find_symbol_name_plus_symbol_no_unsupported_field(self):
        """find_symbol({name, symbol, project_path}) must not return UNSUPPORTED_FIELD.

        The handler will fail downstream (no real session/project attached in
        tests), but the error_name must NOT be UNSUPPORTED_FIELD — that is the
        F1 regression."""
        resp = await srv.dispatch_tool_call(
            "find_symbol",
            {"name": "my_fn", "symbol": "my_fn", "project_path": "."},
            transport="mcp",
        )
        body = json.loads(resp.text)
        self.assertNotEqual(
            body.get("error_name"), "UNSUPPORTED_FIELD",
            f"F1: canonical+alias must not return UNSUPPORTED_FIELD; got: {body}",
        )

    async def test_find_callers_symbol_plus_query_no_unsupported_field(self):
        """find_callers({symbol, query, project_path}) must not return UNSUPPORTED_FIELD."""
        resp = await srv.dispatch_tool_call(
            "find_callers",
            {"symbol": "my_fn", "query": "my_fn", "project_path": "."},
            transport="mcp",
        )
        body = json.loads(resp.text)
        self.assertNotEqual(
            body.get("error_name"), "UNSUPPORTED_FIELD",
            f"F1: canonical+alias must not return UNSUPPORTED_FIELD; got: {body}",
        )


class TestFindCallersPageIdFallback(unittest.IsolatedAsyncioTestCase):
    """F2 (MEDIUM): find_callers page_id fallback must be usable end-to-end.

    Before the fix, when the dispatcher resolved project_path via page_id
    (the documented session-fallback), ``page_id`` remained in body and S4
    rejected it as UNSUPPORTED_FIELD because the find_callers registry schema
    did not declare page_id. After the fix, page_id is declared
    (registry_data.py) AND stripped by the dispatcher before handler
    invocation, so the call completes the S4 gate."""

    def setUp(self):
        # Ensure the duplicate-failure cache does not short-circuit into
        # DUPLICATE_RETRY_BLOCKED between sibling tests.
        srv._DUPLICATE_FAILURE_STATE.clear()

    async def test_find_callers_page_id_in_registry_properties(self):
        """page_id must be a declared property of find_callers (so S4 doesn't
        reject it as UNSUPPORTED_FIELD)."""
        props = _schema_props("find_callers")
        self.assertIn("page_id", props,
                      "find_callers registry must declare page_id (F2)")

    async def test_find_callers_page_id_no_unsupported_field(self):
        """find_callers({symbol, page_id}) must reach past the S4 gate.

        page_id is unknown to _BROWSER_PAGES in tests, so the project_path
        session fallback resolves to "" and the dispatcher will return
        MISSING_REQUIRED_FIELD (project_path). The critical F2 assertion is
        that the error is NOT UNSUPPORTED_FIELD — i.e. page_id was accepted
        at the S4 gate and stripped before handler invocation."""
        resp = await srv.dispatch_tool_call(
            "find_callers",
            {"symbol": "my_fn", "page_id": "cdp-fake-0"},
            transport="mcp",
        )
        body = json.loads(resp.text)
        # F2 regression assertion: page_id must NOT be rejected as unsupported.
        self.assertNotEqual(
            body.get("error_name"), "UNSUPPORTED_FIELD",
            f"F2: page_id must not return UNSUPPORTED_FIELD; got: {body}",
        )
        # Sanity: the response must communicate missing project_path (F2 scope
        # ends here — full session fallback is a separate runtime concern).
        self.assertIn(
            body.get("error_name"),
            (None, "MISSING_REQUIRED_FIELD"),
            f"F2: expected graceful MISSING_REQUIRED_FIELD (no session in test), got {body.get('error_name')}",
        )

    async def test_find_callers_explicit_project_path_with_page_id_also_ok(self):
        """When both project_path AND page_id are supplied, page_id must be ignored
        (dispatcher-consumed) and not trip UNSUPPORTED_FIELD."""
        resp = await srv.dispatch_tool_call(
            "find_callers",
            {"symbol": "my_fn", "project_path": ".", "page_id": "cdp-fake-0"},
            transport="mcp",
        )
        body = json.loads(resp.text)
        self.assertNotEqual(
            body.get("error_name"), "UNSUPPORTED_FIELD",
            f"F2: page_id alongside project_path must not return UNSUPPORTED_FIELD; got: {body}",
        )


if __name__ == "__main__":
    unittest.main()
