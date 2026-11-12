"""P16 Sprint 1 (F5): null exit codes -- regression tests.

The run_project_command returns block declares exit_code/returncode as
integer-first anyOf (integer, null) because the handler deliberately emits
None on four designed paths (PATH_QUOTING_ERROR, elevated runas, keep-alive,
runner exception). These tests pin the leaf verdicts for those shapes
against the live assembled block -- no fixtures, per the F5-FIXTURE
decision (PLAN.md "Regression tests"): the a45 battery pins the committed
fixture count at 81, so regression shapes live here as explicit
(verdict, reason, mismatches) assertions instead of fixture files.

Expected values are verbatim from the Sprint 1 log (sprint1-f5-log.md
section 5 pre-fix red, section 10 post-fix green). The warn reason is
None: check_returns returns ("warn", None, mismatches) -- see
returns_shape.py check_returns.
"""

from __future__ import annotations

import json

from orchestrator.core import returns_shape as rs

_TOOL = "run_project_command"


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
        "the F5 fix requires 'audited'"
    )


def test_declarations_are_integer_first_anyof():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    props = block["properties"]
    expected = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert props["exit_code"] == expected
    assert props["returncode"] == expected
    assert block["required"] == ["ok"]


def test_nullnull_both_codes_null_passes():
    answer = {"ok": True, "exit_code": None, "returncode": None}
    assert rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    ) == ("pass", None, [])


def test_zero_exit_code_passes():
    answer = {"ok": True, "exit_code": 0}
    assert rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    ) == ("pass", None, [])


def test_nonzero_exit_code_passes():
    answer = {"ok": True, "exit_code": 3}
    assert rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    ) == ("pass", None, [])


def test_string_exit_code_warns_with_integer_first_message():
    answer = {"ok": True, "exit_code": "x"}
    assert rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    ) == ("warn", None, ["type:exit_code=string!=integer"])


def test_path4_exit_code_null_without_returncode_passes():
    # Runner-exception path: exit_code None ONLY, no returncode key.
    # Absence is lawful (required == ["ok"]): no missing:returncode.
    answer = {"ok": False, "exit_code": None}
    assert "returncode" not in answer
    verdict, reason, mismatches = rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    )
    assert (verdict, reason, mismatches) == ("pass", None, [])
    assert "missing:returncode" not in mismatches


def test_minimal_ok_only_passes():
    answer = {"ok": True}
    assert rs.check_returns(
        _TOOL, answer, _canonical_raw_len(answer),
    ) == ("pass", None, [])
