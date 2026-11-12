"""P16 Sprint 2 (F8): empty alternative suggestion -- regression tests.

The write_fix returns block's ca_guidance-nested suggested_alternative_tool
node carries properties {tool: string, reason: string} with NO required
list: the producer returns {} (designed "no known alternative") on
unmapped errors, so the guaranteed set inside the node is empty. The
parent required list (13 keys, suggested_alternative_tool last) is kept,
and the TOP-LEVEL suggested_alternative_tool node still carries required
['tool', 'reason'] (guarded stamp; lawful under P-GATE).

Every fixture is top-level {ok, ca_guidance} with ca_guidance carrying
ALL 13 parent-required keys with plausible values, varying ONLY
suggested_alternative_tool: _validate checks required IN ORDER and stops
at MISMATCH_CAP = 5 before descending, so a minimal fixture never
reaches the nested node (false-green repro). No fixture files, per the
F5-FIXTURE decision (PLAN.md "Regression tests"): the a45 battery pins
the committed fixture count at 81, so regression shapes live here as
explicit (verdict, reason, mismatches) assertions instead.

Expected values are verbatim from the Sprint 2 log (sprint2-f8-log.md):
pre-fix red, post-fix green. The warn reason is None: check_returns
returns ("warn", None, mismatches) -- see returns_shape.py
check_returns.
"""

from __future__ import annotations

import json

from orchestrator.core import returns_shape as rs

_TOOL = "write_fix"

_PARENT_REQUIRED = [
    "phase",
    "phase_label",
    "error_name",
    "ca_next_action",
    "call_pattern",
    "payload_example",
    "required_fields",
    "accepted_aliases",
    "notes",
    "known_gotchas",
    "recommended_prompt_rule",
    "see_also",
    "suggested_alternative_tool",
]

_SUGGESTION_PROPERTIES = {
    "reason": {"type": "string"},
    "tool": {"type": "string"},
}


def _canonical_raw_len(answer) -> int:
    """Byte length of the answer's canonical UTF-8 dump (fixture raw_len)."""
    return len(json.dumps(
        answer, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8"))


def _aliases() -> dict:
    return {key: {"maps_to": "file_path", "status": "alias"} for key in
            ["path", "content", "replacement", "start", "end",
             "line_start", "line_end"]}


def _payload_example() -> dict:
    return {"backup_override": False, "content_encoding": "base64",
            "dry_run": False, "end_line": 1, "expected_hash": "",
            "file_path": "x.py", "new_content": "x",
            "project_path": "D:/r", "skip_symbol_check": False,
            "start_line": 1}


def _guidance() -> dict:
    """Full 13-key shape minus suggested_alternative_tool (added by _answer)."""
    return {"phase": "runtime", "phase_label": "Mutate",
            "error_name": "UNMAPPED_PROBE_ERROR", "ca_next_action": "retry",
            "call_pattern": "call", "payload_example": _payload_example(),
            "required_fields": ["file_path"],
            "accepted_aliases": _aliases(),
            "notes": ["n"], "known_gotchas": ["g"],
            "recommended_prompt_rule": "r",
            "see_also": {"write_transaction": "s", "search_code": "s",
                         "read_file_range": "s"}}


def _answer(suggestion) -> dict:
    guidance = _guidance()
    guidance["suggested_alternative_tool"] = suggestion
    return {"ok": False, "ca_guidance": guidance}


def _answer_absent() -> dict:
    return {"ok": False, "ca_guidance": _guidance()}


def test_block_resolves_audited():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert rs.schema_state(block) == "audited", (
        f"{_TOOL}: schema_state is {rs.schema_state(block)!r}, "
        "the F8 fix requires 'audited'"
    )


def test_nested_node_has_properties_without_required():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    nested = block["properties"]["ca_guidance"]["properties"][
        "suggested_alternative_tool"]
    assert "required" not in nested
    assert nested["properties"] == _SUGGESTION_PROPERTIES
    assert nested["type"] == "object"


def test_parent_required_is_the_13_key_list():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert block["properties"]["ca_guidance"]["required"] == _PARENT_REQUIRED


def test_top_level_node_still_carries_required_pair():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    top = block["properties"]["suggested_alternative_tool"]
    assert top["required"] == ["tool", "reason"]
    assert top["properties"] == _SUGGESTION_PROPERTIES
    assert top["type"] == "object"
    assert top["x-dual"] is True


def test_empty_suggestion_passes():
    answer = _answer({})
    verdict, reason, mismatches = rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    )
    assert (verdict, reason, mismatches) == ("pass", None, [])
    assert "missing:ca_guidance.suggested_alternative_tool.tool" not in mismatches
    assert "missing:ca_guidance.suggested_alternative_tool.reason" not in mismatches


def test_populated_suggestion_passes():
    answer = _answer({"tool": "x", "reason": "y"})
    assert rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    ) == ("pass", None, [])


def test_int_tool_warns_with_type_only():
    answer = _answer({"tool": 5})
    assert rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    ) == ("warn", None, [
        "type:ca_guidance.suggested_alternative_tool.tool=integer!=string",
    ])


def test_absent_key_warns_with_parent_missing():
    answer = _answer_absent()
    assert "suggested_alternative_tool" not in answer["ca_guidance"]
    assert rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    ) == ("warn", None, ["missing:ca_guidance.suggested_alternative_tool"])
