# pyright: reportArgumentType=false
"""Schema-param contract test for the orchestrator alias system.

Validates that:
1. Every alias canonical field exists in the tool's schema properties.
2. Harmless-ignore fields are NOT declared schema properties (known exception flagged).
3. _normalize_body applies alias renames correctly.
4. _normalize_body drops harmless-ignore fields after aliasing (CAN-009).
5. register_tool injects aliases into _ARG_ALIASES dynamically.
6. Live alias_contract_test tool returns all-pass (if server is running).

Run:  ./venv312/Scripts/python.exe -m pytest tests/test_alias_contract.py -v
"""

import json
import urllib.request
import urllib.error
import unittest

import code_tools
import orchestrator_server as srv  # noqa: F401 — triggers late registrations

from orchestrator.core.registry import register_tool, validate_registry


# ── helpers ──────────────────────────────────────────────────────────────────

def _schema_props() -> dict[str, set[str]]:
    """Build {tool_name: set(property_names)} from TOOL_SCHEMAS."""
    out: dict[str, set[str]] = {}
    for item in code_tools.TOOL_SCHEMAS:
        fn = (item.get("function") or {}) if isinstance(item, dict) else {}
        name = fn.get("name")
        if name:
            props = (fn.get("parameters") or {}).get("properties", {})
            out[name] = set(props.keys())
    return out


_SCHEMAS = _schema_props()


# ── 1. Alias canonical fields exist in schema ────────────────────────────────

class TestAliasCanonicalFieldsExist(unittest.TestCase):
    """Every alias canonical field must be a declared schema property."""

    def test_all_alias_canonicals_exist_in_schema(self):
        missing = []
        for tool, aliases in srv._ARG_ALIASES.items():
            tool_props = _SCHEMAS.get(tool, set())
            for legacy, canonical in aliases.items():
                if canonical not in tool_props:
                    missing.append(f"{tool}: {legacy}->{canonical}: canonical NOT in properties")
        self.assertEqual(missing, [], f"Canonical fields missing from schema: {missing}")

    def test_alias_count_is_113(self):
        """Contract snapshot: exactly 113 aliases across 42 tools.

        History: 102/39 until 2026-07-08; +2 aliases (delay, delay_sec ->
        delay_seconds) khi Anh yeu cau tool restart_orchestrator_via_bat
        (wire [A:S]); +9 aliases / +2 tools 2026-07-10 schema-drift fix
        (find_symbol/find_callers/regex_search & friends gained dual-spelling
        aliases folding vao canonical `query`) — see ALIAS_CONTRACT.md.
        """
        total = sum(len(v) for v in srv._ARG_ALIASES.values())
        # Re-based 2026-07-16: +2 CAN-051 open_browser_page (wait_until/timeout)
        # on top of the 07-16 mcp-surface alias additions that predated this run.
        # Re-based 2026-08-06: +2 P2 council_message (async/non_blocking ->
        # async_dispatch) for the opt-in non-blocking dispatch gate.
        # Re-based 2026-08-08: +1 B4 council_status alias (id -> dispatch_id)
        # so compact status callers retain registry-validated input.
        # Re-based 2026-08-11: +51 R44 result-cap synonym aliases (limit/max_results/
        # max_items/top_k folded per-tool) across 14 new + 4 existing aliased tools.
        self.assertEqual(total, 205, f"Expected 205 aliases, got {total}")

    def test_alias_tool_count_is_42(self):
        # Re-based 2026-08-11: R44 result-cap family added 14 new aliased tools.
        # Re-based 2026-08-12: R46 write-content family (+3 tools).
        # Re-based 2026-08-14: +1 ruff_fix (file_path/file -> path).
        # Re-based 2026-08-14 leftover: +1 list_structure (max_depth/file_path/folder/folder_path).
        self.assertEqual(len(srv._ARG_ALIASES), 69,
                         f"Expected 69 aliased tools, got {len(srv._ARG_ALIASES)}")


# ── 2. Harmless-ignore fields are NOT schema properties ──────────────────────

class TestHarmlessIgnoreNotInSchema(unittest.TestCase):
    """Harmless-ignore fields should be unknown fields, not declared properties.

    Known exception: get_token_meter.project_path (documented in ALIAS_CONTRACT.md §5.2).
    """

    KNOWN_EXCEPTIONS = {("get_token_meter", "project_path")}

    def test_no_ignore_field_is_declared_property(self):
        collisions = []
        for tool, fields in srv._HARMLESS_IGNORE_FIELDS.items():
            tool_props = _SCHEMAS.get(tool, set())
            for f in fields:
                if f in tool_props and (tool, f) not in self.KNOWN_EXCEPTIONS:
                    collisions.append(f"{tool}: ignore field {f!r} IS a declared property")
        self.assertEqual(collisions, [],
                         f"Ignore fields that are also schema properties: {collisions}")

    def test_harmless_ignore_count_is_46(self):
        total = sum(len(v) for v in srv._HARMLESS_IGNORE_FIELDS.values())
        self.assertEqual(total, 46, f"Expected 46 harmless-ignore fields, got {total}")

    def test_known_exception_is_documented(self):
        """The get_token_meter.project_path exception must still exist (if removed,
        update this test)."""
        if "get_token_meter" in srv._HARMLESS_IGNORE_FIELDS:
            self.assertIn("project_path", srv._HARMLESS_IGNORE_FIELDS["get_token_meter"],
                          "get_token_meter.project_path known exception was removed from ignore set")


# ── 3. _normalize_body applies aliases ───────────────────────────────────────

    def test_create_folders_project_path_not_dropped(self):
        """B1: relative create_folders needs project_path through HTTP normalize."""
        self.assertNotIn("create_folder", srv._HARMLESS_IGNORE_FIELDS)
        self.assertNotIn("create_folders", srv._HARMLESS_IGNORE_FIELDS)
        body = {"paths": ["rel/sub"], "project_path": "D:/tmp/proj"}
        norm, applied = srv._normalize_body("create_folders", dict(body))
        self.assertEqual(norm.get("project_path"), "D:/tmp/proj")
        self.assertFalse(any("project_path" in a and "ignored" in a for a in applied))


class TestNormalizeBodyAliases(unittest.TestCase):

    def test_single_alias_rename(self):
        """read_file_range: path -> file_path, start -> start_line, end -> end_line"""
        body = {"path": "foo.py", "start": 10, "end": 20}
        norm, applied = srv._normalize_body("read_file_range", dict(body))
        self.assertEqual(norm["file_path"], "foo.py")
        self.assertEqual(norm["start_line"], 10)
        self.assertEqual(norm["end_line"], 20)
        self.assertNotIn("path", norm)
        self.assertNotIn("start", norm)
        self.assertNotIn("end", norm)
    def test_ruff_fix_file_path_alias(self):
        body = {"project_path": r"D:\proj", "file_path": "app.py"}
        norm, applied = srv._normalize_body("ruff_fix", dict(body))
        self.assertEqual(norm["path"], "app.py")
        self.assertNotIn("file_path", norm)
        self.assertTrue(any("file_path->path" in item for item in applied))

    def test_list_structure_max_depth_and_file_path_alias(self):
        body = {"project_path": r"D:\proj", "file_path": ".council", "max_depth": 2}
        norm, applied = srv._normalize_body("list_structure", dict(body))
        self.assertEqual(norm["path"], ".council")
        self.assertEqual(norm["max_depth"] if "max_depth" in norm else norm.get("depth"), 2)
        self.assertNotIn("file_path", norm)
        self.assertNotIn("max_depth", norm)
        self.assertTrue(any("max_depth->depth" in item for item in applied))
        self.assertTrue(any("file_path->path" in item for item in applied))
        self.assertTrue(any("file_path->path" in item for item in applied))

    def test_canonical_wins_over_legacy(self):
        """If both legacy and canonical are present, canonical value is preserved
        and the duplicate alias is DROPPED (F1, 2026-08-08).

        Prior behavior: alias was left in body untouched, so S4 computed
        unsupported from remaining keys and returned UNSUPPORTED_FIELD. Post-F1:
        the alias is consumed (applied audit marks 'dropped'), canonical untouched."""
        body = {"path": "legacy.py", "file_path": "canonical.py"}
        norm, applied = srv._normalize_body("read_file_range", dict(body))
        self.assertEqual(norm["file_path"], "canonical.py")
        self.assertNotIn("path", norm,
                         "duplicate alias 'path' must be dropped when canonical 'file_path' already present")
        self.assertTrue(
            any("dropped" in a for a in applied),
            f"applied audit must record the alias drop; got {applied}",
        )

    def test_council_message_aliases(self):
        """Dynamic aliases: frm->sender, msg->text, recipient->to"""
        body = {"frm": "ope", "msg": "hello", "recipient": "clau"}
        norm, applied = srv._normalize_body("council_message", dict(body))
        self.assertEqual(norm["sender"], "ope")
        self.assertEqual(norm["text"], "hello")
        self.assertEqual(norm["to"], "clau")

    def test_council_worker_model_aliases(self):
        """Dynamic aliases: worker->agent, id->model"""
        body = {"worker": "ope", "id": "glm-5.2"}
        norm, applied = srv._normalize_body("council_worker_model", dict(body))
        self.assertEqual(norm["agent"], "ope")
        self.assertEqual(norm["model"], "glm-5.2")

    def test_search_code_is_regex_special_case(self):
        """search_code: is_regex -> mode, not in _ARG_ALIASES."""
        body = {"query": "foo", "is_regex": True}
        norm, applied = srv._normalize_body("search_code", dict(body))
        self.assertEqual(norm.get("mode"), "regex")
        self.assertNotIn("is_regex", norm)
        self.assertIn("is_regex->mode", applied)

    def test_search_code_pattern_alias_does_not_inject_literal_search(self):
        """search_code: pattern->query must NOT inject literal_search (2026-08-08).

        The old behavior added literal_search=False to the body, which is NOT a
        declared schema property and would trip UNSUPPORTED_FIELD at S4 validation.
        Mode (text|regex) determines literal vs regex semantics — not a stray bool.
        """
        body = {"pattern": "foo"}
        norm, applied = srv._normalize_body("search_code", dict(body))
        self.assertEqual(norm["query"], "foo")
        self.assertNotIn("literal_search", norm,
                         "literal_search must not be injected — not in registry properties")

    def test_alias_on_unknown_tool_is_noop(self):
        """A tool with no aliases should pass through unchanged."""
        body = {"foo": "bar"}
        norm, applied = srv._normalize_body("nonexistent_tool_xyz", dict(body))
        self.assertEqual(norm, {"foo": "bar"})
        self.assertEqual(applied, [])

    def test_regex_search_query_alias_maps_to_pattern(self):
        """TT-A-03 (2026-08-10): regex_search canonical field is `pattern`.

        The schema (TOOL_SCHEMAS['regex_search'].required) declares `pattern`,
        but `_ARG_ALIASES['regex_search']` previously read {pattern: 'query',
        regex: 'query', q: 'query'} — i.e. it renamed the CANONICAL field
        `pattern` into `query`. That made _normalize_body strip the very field
        S4 then demanded, returning MISSING_REQUIRED_FIELD on a payload that
        literally supplied `pattern`. Worse, the duplicate-retry fingerprint
        is computed over the post-normalization body, so a follow-up retry
        with {query: ...} produced the same normalized body and was blocked
        as DUPLICATE_RETRY_BLOCKED.

        The contract being protected here is: `query` is an ALIAS for
        `pattern` (alias→canonical), not the other way around. A caller that
        passes `query=<regex>` must end up with body['pattern']==<regex> so
        the tools_v15.regex_search handler (which takes pattern=...) and the
        schema's required=['pattern','project_path'] both succeed.
        """
        body = {"query": "def foo\\(", "project_path": "."}
        norm, applied = srv._normalize_body("regex_search", dict(body))
        self.assertEqual(norm.get("pattern"), "def foo\\(",
                         "alias query must be renamed to canonical pattern")
        self.assertNotIn("query", norm,
                         "alias key must be consumed, not left in body")
        self.assertTrue(any("query->pattern" in a for a in applied),
                        f"applied audit must record query->pattern; got {applied}")

    def test_regex_search_canonical_pattern_is_not_renamed(self):
        """TT-A-03 companion: passing the canonical `pattern` must NOT be
        renamed into `query`. The inverted alias table used to do that, which
        is what produced MISSING_REQUIRED_FIELD even when callers supplied
        exactly what the schema asked for."""
        body = {"pattern": "def foo\\(", "project_path": "."}
        norm, applied = srv._normalize_body("regex_search", dict(body))
        self.assertEqual(norm.get("pattern"), "def foo\\(")
        self.assertNotIn("query", norm,
                         "canonical pattern must not be inverted into query")

    def test_regex_search_canonical_wins_when_both_supplied(self):
        """TT-A-03: if both pattern and query are supplied, canonical pattern
        wins and the alias is dropped (no data loss, no UNSUPPORTED_FIELD)."""
        body = {"pattern": "canonical", "query": "legacy", "project_path": "."}
        norm, applied = srv._normalize_body("regex_search", dict(body))
        self.assertEqual(norm["pattern"], "canonical")
        self.assertNotIn("query", norm)

    def test_non_dict_body_passthrough(self):
        norm, applied = srv._normalize_body("read_file_range", "not a dict")
        self.assertEqual(norm, "not a dict")
        self.assertEqual(applied, [])


# ── 4. _normalize_body drops harmless-ignore fields ──────────────────────────

class TestNormalizeBodyHarmlessIgnore(unittest.TestCase):

    def test_ignore_field_dropped(self):
        """read_skill: project_path is in harmless-ignore set."""
        body = {"name": "hermes-agent", "project_path": "."}
        norm, applied = srv._normalize_body("read_skill", dict(body))
        self.assertNotIn("project_path", norm)
        self.assertEqual(norm["name"], "hermes-agent")
        # Should appear in applied audit trail
        self.assertTrue(any("ignored" in a for a in applied))

    def test_ignore_runs_after_alias(self):
        """CAN-009: if a field is both alias source and ignore target, it gets
        renamed, not dropped. (No current field hits this, but test the pipeline
        order by checking that alias applied entries come before ignore entries.)"""
        body = {"path": "foo.py", "project_path": "."}
        norm, applied = srv._normalize_body("read_file_range", dict(body))
        # path->file_path (alias), project_path not in read_file_range's ignore set
        # so it stays — this just confirms aliasing works alongside body fields
        self.assertEqual(norm["file_path"], "foo.py")

    def test_verbosity_dropped(self):
        """Multiple tools ignore verbosity."""
        for tool in ["click_selector", "fill_value", "wait_for_idle"]:
            body = {"verbosity": "debug"}
            norm, applied = srv._normalize_body(tool, dict(body))
            self.assertNotIn("verbosity", norm, f"{tool} should drop verbosity")


# ── 5. register_tool injects aliases dynamically ─────────────────────────────

class TestRegisterToolAliasInjection(unittest.TestCase):
    """register_tool(aliases=...) must inject into arg_aliases (registry.py:96)."""

    def test_register_injects_aliases(self):
        tool_map, schemas, phases, arg_aliases = {}, [], {}, {}
        register_tool(
            name="demo_alias_tool",
            handler=lambda: None,
            phase="T1",
            tool_map=tool_map,
            tool_schemas=schemas,
            tool_phases=phases,
            arg_aliases=arg_aliases,
            phase_labels=code_tools.PHASE_LABELS,
            aliases={"old_name": "new_name"},
            schema={"description": "d", "parameters": {"type": "object", "properties": {}}},
        )
        self.assertEqual(arg_aliases["demo_alias_tool"], {"old_name": "new_name"})

    def test_register_without_aliases_no_entry(self):
        tool_map, schemas, phases, arg_aliases = {}, [], {}, {}
        register_tool(
            name="no_alias_tool",
            handler=lambda: None,
            phase="T1",
            tool_map=tool_map,
            tool_schemas=schemas,
            tool_phases=phases,
            arg_aliases=arg_aliases,
            phase_labels=code_tools.PHASE_LABELS,
            schema={"description": "d", "parameters": {"type": "object", "properties": {}}},
        )
        self.assertNotIn("no_alias_tool", arg_aliases)

    def test_council_message_aliases_in_arg_aliases(self):
        """Verify dynamic registration worked: council_message aliases are in _ARG_ALIASES."""
        self.assertIn("council_message", srv._ARG_ALIASES)
        cm = srv._ARG_ALIASES["council_message"]
        self.assertEqual(cm["from"], "sender")
        self.assertEqual(cm["frm"], "sender")
        self.assertEqual(cm["msg"], "text")
        self.assertEqual(cm["recipient"], "to")

    def test_council_worker_model_aliases_in_arg_aliases(self):
        self.assertIn("council_worker_model", srv._ARG_ALIASES)
        cwm = srv._ARG_ALIASES["council_worker_model"]
        self.assertEqual(cwm["worker"], "agent")
        self.assertEqual(cwm["id"], "model")

    def test_council_status_alias_and_acceptance_order(self):
        self.assertEqual(srv._ARG_ALIASES["council_status"]["id"], "dispatch_id")
        import inspect
        src = inspect.getsource(srv.handle_council_send_async)
        self.assertLess(src.index("if target not in _COUNCIL_AGENTS"),
                        src.index("_council_accept_dispatch("))

# ── 6. Registry cross-consistency ────────────────────────────────────────────

class TestRegistryConsistency(unittest.TestCase):

    def test_zero_registry_drift(self):
        """TOOL_MAP / TOOL_SCHEMAS / TOOL_PHASES must be consistent."""
        issues = validate_registry(
            code_tools.TOOL_MAP, code_tools.TOOL_SCHEMAS, code_tools.TOOL_PHASES)
        self.assertEqual(issues, [], f"Registry drift: {issues}")


# ── 7. Live alias_contract_test (optional — skipped if server down) ──────────

class TestLiveAliasContractTest(unittest.TestCase):
    """Calls the running orchestrator's alias_contract_test tool.

    Skipped if server is not reachable at localhost:8083.
    """

    def test_live_alias_contract_all_pass(self):
        try:
            req = urllib.request.Request(
                "http://localhost:8083/api/call/alias_contract_test",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            self.skipTest("orchestrator server not running at localhost:8083")

        # Response may be a dict or a JSON string
        if isinstance(data, str):
            data = json.loads(data)

        # alias_contract_intact lives under summary, not top-level
        summary = data.get("summary", data)
        self.assertTrue(
            summary.get("alias_contract_intact", False),
            f"alias_contract_intact=False: {data.get('failures', [])}",
        )
        alias_tests = data.get("alias_tests", {})
        harmless_tests = data.get("harmless_tests", {})
        self.assertEqual(
            alias_tests.get("passed"), alias_tests.get("tested"),
            f"Not all aliases passed: {alias_tests.get('failed', 0)} failed",
        )
        self.assertEqual(
            harmless_tests.get("passed"), harmless_tests.get("tested"),
            f"Not all harmless-ignore tests passed: {harmless_tests.get('failed', 0)} failed",
        )


if __name__ == "__main__":
    unittest.main()
