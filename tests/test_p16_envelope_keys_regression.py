"""P16 regression: envelope keys must never be demanded of tool answers.

Context (campaign p16-spec-fix-20261108): the 2026-11-07 returns-shape
snapshot alarmed at 100% warn for run_project_command because 18 authored
returns blocks (plus audit_tool_matrix via contention_note) demanded
envelope-only keys -- generation, operation_id, write_status_url,
operation_attempts -- in the ANSWER-level `required` list. Those keys live
only on the dispatch envelope (R101: they are stamped onto the envelope
after the verdict window) and can never appear in a handler answer, so
every verdict was warn by construction. The fix removed the envelope keys
from `required` and `properties` of the affected blocks and corrected the
write_fix `validation` property to anyOf [object, null] (finding S-1).

These tests lock the adjudicated invariant against the REAL assembled
schemas (code_tools.TOOL_SCHEMAS via rs.resolve_returns), and prove the
checker now passes handler-shaped answers for the tools that alarmed.
"""
from __future__ import annotations

import pytest

import code_tools as ct  # noqa: F401  (import assembles TOOL_SCHEMAS)
from orchestrator.core import returns_shape as rs

ENVELOPE_KEYS = {"generation", "operation_id", "write_status_url", "operation_attempts"}


def _all_required_paths(block, prefix=""):
    out = []
    for key in block.get("required") or []:
        out.append(prefix + key)
    for key, sub in (block.get("properties") or {}).items():
        if isinstance(sub, dict):
            out.extend(_all_required_paths(sub, prefix + key + "/"))
    return out


def _sample(schema):
    """Minimal value satisfying a (sub)schema's declared type/required."""
    if not isinstance(schema, dict):
        return None
    if "anyOf" in schema:
        return _sample(schema["anyOf"][0])
    if "oneOf" in schema:
        return _sample(schema["oneOf"][0])
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        return _sample({"type": non_null[0]}) if non_null else None
    if t == "object" or (t is None and "properties" in schema):
        props = schema.get("properties") or {}
        return {k: _sample(props.get(k, {})) for k in schema.get("required") or []}
    if t == "array":
        return []
    if t == "string":
        return "x"
    if t == "integer":
        return 1
    if t == "number":
        return 1.0
    if t == "boolean":
        return True
    if t == "null":
        return None
    return "x"


def _handler_shaped_answer(tool):
    block = rs.resolve_returns(tool)
    assert block is not None and rs.schema_state(block) == "audited"
    return _sample(block)


class TestNoEnvelopeKeysDemanded:
    @pytest.mark.parametrize("tool", sorted(rs.RETURNS_SET_38))
    def test_no_envelope_key_required_at_any_depth(self, tool):
        block = rs.resolve_returns(tool)
        if block is None:
            pytest.skip("no returns block")
        for path in _all_required_paths(block):
            assert path.split("/")[-1] not in ENVELOPE_KEYS, (tool, path)

    @pytest.mark.parametrize("tool", sorted(rs.RETURNS_SET_38))
    def test_no_envelope_key_in_top_level_properties(self, tool):
        block = rs.resolve_returns(tool)
        if block is None:
            pytest.skip("no returns block")
        assert not (set((block.get("properties") or {})) & ENVELOPE_KEYS), tool

    def test_audit_tool_matrix_does_not_demand_contention_note(self):
        block = rs.resolve_returns("audit_tool_matrix")
        assert block is not None
        assert "contention_note" not in (block.get("required") or [])
        assert "contention_note" not in (block.get("properties") or {})

    def test_census_shape_unchanged(self):
        states = [rs.schema_state(rs.resolve_returns(t)) for t in rs.RETURNS_SET_38]
        assert states.count("audited") == 32
        assert states.count("absent") == 6


class TestHandlerShapedAnswersPass:
    @pytest.mark.parametrize("tool", ["run_project_command", "run_ps1_script"])
    def test_main_path_answer_passes_without_envelope_keys(self, tool):
        answer = _handler_shaped_answer(tool)
        assert not (set(answer) & ENVELOPE_KEYS)
        verdict, reason, mismatches = rs.check_returns(tool, answer, 100)
        assert (verdict, mismatches) == ("pass", []), (tool, reason, mismatches)

    def test_checker_still_warns_when_required_key_missing(self):
        verdict, _, mismatches = rs.check_returns("run_project_command", {}, 100)
        assert verdict == "warn"
        assert any(m.startswith("missing:") for m in mismatches)

    def test_write_fix_validation_object_and_null_pass(self):
        base = _handler_shaped_answer("write_fix")
        for value in ({"ok": True, "lang": "py"}, None):
            answer = dict(base, validation=value)
            verdict, reason, mismatches = rs.check_returns("write_fix", answer, 100)
            assert (verdict, mismatches) == ("pass", []), (reason, mismatches)
