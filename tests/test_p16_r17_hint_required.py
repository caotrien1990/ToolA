"""P16 Sprint 3 (R-17): tool_decision_hint error path -- regression tests.

The tool_decision_hint returns block retains required == ["ok",
"schema_version"] (J1-selected set, sprint3-r17-log.md section 2): every
answer that reaches the verdict window carries both keys -- the handler
stamps them via _ok/_err on all seven _legacy_decision_hint returns, and
every path lacking schema_version provably bypasses the window. The three
dropped keys (recommended_tier, recommended_tool, suggestion) are present
only on success shapes; the live error shape carries 6 keys with 2 OF the
5 formerly-required keys, which warned pre-fix.

These tests pin the leaf verdicts for those shapes against the live
assembled block -- no fixtures, per the F5-FIXTURE decision (PLAN.md
"Regression tests"): the a45 battery pins the committed fixture count at
81, so regression shapes live here as explicit (verdict, reason,
mismatches) assertions instead of fixture files.

Expected values are verbatim from the Sprint 3 log (sprint3-r17-log.md
section 4 pre-fix red, section 9 post-fix green). The warn reason is
None: check_returns returns ("warn", None, mismatches) -- see
returns_shape.py check_returns. The properties key set is verbatim from
the pre-fix J1 probe on the unedited tree (which equaled HEAD:
code_tools.py was unmodified before this sprint's edit).
"""

from __future__ import annotations

import json

from orchestrator.core import returns_shape as rs

_TOOL = "tool_decision_hint"

_LIVE_ERROR = {
    "ok": False,
    "error": "task_description required (alias `task` also accepted)",
    "error_name": "MISSING_REQUIRED_FIELD",
    "accepted_fields": ["task_description", "task", "scope_hint", "risk_hint"],
    "accepted_aliases": {"task": "task_description"},
    "schema_version": "v15.r18",
}

_RECIPE_ERROR = {
    "ok": False,
    "error": "Lifecycle recipe unavailable in this catalog.",
    "error_name": "RECIPE_TOOL_UNAVAILABLE",
    "missing_tools": ["server_lifecycle_status"],
    "ca_next_action": "inspect_live_catalog",
    "schema_version": "v15.r18",
}

# Live success body (served probe L5) minus post-window wire stamps
# (generation / pushed_schema / pushed_schema_note).
_LIVE_SUCCESS = {
    "recommended_tier": "T1",
    "recommended_tool": "find_symbol",
    "recommended_skill": "15_search_code_intel",
    "rationale": "Read-only; indexed lookup saves context vs grep.",
    "scope_hint": "",
    "risk_hint": "",
    "confidence": 0.85,
    "tools_validation": {
        "checked": ["find_symbol", "search_code"],
        "present": ["find_symbol", "search_code"],
        "missing": [],
        "inconclusive": False,
    },
    "all_recommended_tools_live": True,
    "payload_example_per_tool": {"find_symbol": {"name": "<value>"}},
    "payload_example_lookup_hint": (
        "Each key = tool name; value = working payload. "
        "Adjust placeholders before calling."
    ),
    "alternates": [
        {
            "tool": "find_definition_and_usage",
            "tier": "T1",
            "why": "V15 R8 B.1: deprecated alias of search_code - use search_code with query=<symbol> for definition lookup.",
        },
        {
            "tool": "cbm_template_query",
            "tier": "T1",
            "why": "T1 read-only: named graph queries without Cypher. template=callers|callees|imports|imported_by|orphan_symbols|module_dep",
        },
    ],
    "suggestion": {
        "tool": "find_symbol",
        "tier": "T1",
        "payload_example": {"name": "<value>"},
        "reason": "Read-only; indexed lookup saves context vs grep.",
    },
    "ok": True,
    "schema_version": "v15.r18",
    "ranked_tools": [
        {"tool": "find_symbol", "score": 39.6579,
         "why": "routing rule, definitions, find, symbol"},
        {"tool": "find_definition_and_usage", "score": 14.9124,
         "why": "find, query, symbol"},
        {"tool": "search_code", "score": 8.1533, "why": "find, query, symbol"},
        {"tool": "cbm_template_query", "score": 7.8034, "why": "query, symbol"},
        {"tool": "cbm_trace_path", "score": 7.2301, "why": "in, query, symbol"},
        {"tool": "cbm_council_context", "score": 6.7136, "why": "in, symbol"},
        {"tool": "read_context_batch", "score": 6.6741,
         "why": "find, in, symbol"},
        {"tool": "git_blame_symbol", "score": 6.258, "why": "symbol"},
    ],
}

_PROPERTIES_KEYS = {
    "all_recommended_tools_live",
    "candidates",
    "confidence",
    "next_step",
    "ok",
    "payload_example_per_tool",
    "q_memory_refs",
    "ranked_tools",
    "rationale",
    "recipe",
    "recommended_skill",
    "recommended_tier",
    "recommended_tool",
    "schema_version",
    "suggestion",
    "verbosity_mode",
}


def _canonical_raw_len(answer) -> int:
    """Byte length of the answer's canonical UTF-8 dump (fixture raw_len)."""
    return len(json.dumps(
        answer, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8"))


def test_block_resolves_audited():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert rs.schema_state(block) == "audited", (
        f"{_TOOL}: schema_state is {rs.schema_state(block)!r}, "
        "the R-17 fix requires 'audited'"
    )


def test_block_required_is_j1_selected_set():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert block["required"] == ["ok", "schema_version"]


def test_nested_suggestion_required_unchanged():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert block["properties"]["suggestion"]["required"] == [
        "tool", "tier", "payload_example", "reason",
    ]


def test_properties_keys_unchanged():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert set(block["properties"].keys()) == _PROPERTIES_KEYS


def test_live_error_shape_passes_post_fix():
    verdict, reason, mismatches = rs.check_returns(
        _TOOL, dict(_LIVE_ERROR), _canonical_raw_len(_LIVE_ERROR),
    )
    assert (verdict, reason, mismatches) == ("pass", None, [])
    assert "missing:recommended_tier" not in mismatches
    assert "missing:recommended_tool" not in mismatches
    assert "missing:suggestion" not in mismatches


def test_recipe_error_shape_passes_post_fix():
    assert rs.check_returns(
        _TOOL, dict(_RECIPE_ERROR), _canonical_raw_len(_RECIPE_ERROR),
    ) == ("pass", None, [])


def test_live_success_shape_passes():
    assert rs.check_returns(
        _TOOL, dict(_LIVE_SUCCESS), _canonical_raw_len(_LIVE_SUCCESS),
    ) == ("pass", None, [])


def test_success_with_int_tier_warns_with_type_string():
    answer = dict(_LIVE_SUCCESS)
    answer["recommended_tier"] = 5
    assert rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    ) == ("warn", None, ["type:recommended_tier=integer!=string"])


def test_shape_missing_ok_warns_with_missing_ok():
    answer = dict(_LIVE_SUCCESS)
    del answer["ok"]
    assert rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    ) == ("warn", None, ["missing:ok"])
