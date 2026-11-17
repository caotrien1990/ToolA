"""P16 Sprint 2 (O-17(ii)): exit_code timeout-drain -- regression tests.

Both the run_project_test_script and the run_ps1_script returns blocks
declare exit_code as an integer-first anyOf (integer, null), because the
timeout-drain handler path honestly reports "no exit code yet" (None)
when a killed process is still unreaped: the drain grace expires
(code_tools.py:5485-5488, still_running_after_kill), rc picks up
proc.returncode while it is still None (:10113; :10131 anticipates
None), and the answer emits it verbatim ("exit_code": rc, :10155).
run_ps1_script delegates to run_project_test_script and returns its
answer verbatim (:12437-12450), so both blocks must admit the null --
the delegation-inheritance pin below asserts the two declarations are
EQUAL as assembled by resolve_returns. The anyOf form follows the
PQ-2/F5 pattern (tests/test_p16_f5_null_codes.py pins the same form
for run_project_command). Both blocks' required lists are unchanged
(["ok", "ca_next_action"]); no handler code changed.

These tests pin the leaf verdicts for those shapes against the live
assembled blocks -- no fixtures, per the F5-FIXTURE decision (PLAN.md
"Regression tests"): the a45 battery pins the committed fixture count
at 81, so regression shapes live here as explicit (verdict, reason,
mismatches) assertions instead of fixture files.

Expected values are verbatim from the Sprint 2 post-fix probe run
(records/sprint2-o17ii-log.md; the 6 filed fixtures, all matching
expect_post_fix). The warn reason is None: check_returns returns
("warn", None, mismatches) -- see returns_shape.py check_returns.
The string-mismatch string keeps the integer-first form
("type:exit_code=string!=integer"): the leaf's anyOf summary uses the
FIRST branch's type (returns_shape.py:288-303).
"""

from __future__ import annotations

import json
from pathlib import Path

import orchestrator_server as srv  # noqa: F401  (assembles TOOL_SCHEMAS: 238 -> 260)
from orchestrator.core import returns_shape as rs

_RPTS = "run_project_test_script"
_RPS = "run_ps1_script"
_REPO_ROOT = Path(srv.__file__).resolve().parent
_BASELINE_FILE = _REPO_ROOT / "orchestrator" / "core" / "returns_baseline.json"
_ANYOF_INTEGER = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
_REQUIRED = ["ok", "ca_next_action"]
_RPTS_PROPS = [
    "ca_guidance", "ca_next_action", "child_encoding", "duration_seconds",
    "error", "error_name", "exit_code", "ok", "payload_example", "phase",
    "retry_sequence", "runtime_used", "script_hash", "script_label",
    "script_path", "stderr", "stderr_metadata", "stdout", "stdout_metadata",
    "test_gap", "timed_out",
]
_RPS_PROPS = [
    "ca_next_action", "duration_seconds", "exit_code", "ok", "runtime_used",
    "script_hash", "script_label", "script_path", "stderr", "stderr_metadata",
    "stdout", "stdout_metadata", "test_gap", "timed_out",
]


def _canonical_raw_len(answer) -> int:
    """Byte length of the answer's canonical UTF-8 dump (fixture raw_len)."""
    return len(json.dumps(
        answer, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8"))


def _check(tool, answer):
    return rs.check_returns(tool, answer, _canonical_raw_len(answer))


def test_blocks_resolve_audited():
    for tool in (_RPTS, _RPS):
        block = rs.resolve_returns(tool)
        assert block is not None, f"{tool}: no returns block resolved"
        assert rs.schema_state(block) == "audited", (
            f"{tool}: schema_state is {rs.schema_state(block)!r}, "
            "the O-17(ii) fix requires 'audited'"
        )


def test_declarations_are_integer_first_anyof():
    for tool, props in ((_RPTS, _RPTS_PROPS), (_RPS, _RPS_PROPS)):
        block = rs.resolve_returns(tool)
        assert block is not None, f"{tool}: no returns block resolved"
        assert block["required"] == _REQUIRED
        assert sorted(block["properties"].keys()) == props
        assert block["properties"]["exit_code"] == _ANYOF_INTEGER


def test_sha_triples_and_depth_d():
    baseline = json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))
    for tool, depth in ((_RPTS, 4), (_RPS, 2)):
        block = rs.resolve_returns(tool)
        assert block is not None, f"{tool}: no returns block resolved"
        pin = baseline[tool]
        assert block["x-audit"]["schema_sha256"] == rs.canonical_schema_sha(block)
        assert pin["sha256"] == rs.canonical_schema_sha(block)
        assert block["x-audit"]["depth_D"] == depth
        assert pin["depth_D"] == depth


def test_delegation_inheritance_pin():
    """run_ps1_script inherits run_project_test_script's answer verbatim
    (code_tools.py:12437-12450), so the two assembled exit_code
    declarations must be EQUAL -- both integer-first anyOf."""
    rpts = rs.resolve_returns(_RPTS)
    rps = rs.resolve_returns(_RPS)
    assert rpts is not None and rps is not None
    rpts_decl = rpts["properties"]["exit_code"]
    rps_decl = rps["properties"]["exit_code"]
    assert rps_decl == rpts_decl
    assert rps_decl == _ANYOF_INTEGER
    assert rpts_decl == _ANYOF_INTEGER


def test_null_exit_code_passes_both_blocks():
    for tool in (_RPTS, _RPS):
        answer = {"ok": False, "ca_next_action": "none", "exit_code": None,
                  "timed_out": True}
        assert _check(tool, answer) == ("pass", None, [])


def test_zero_exit_code_passes_both_blocks():
    for tool in (_RPTS, _RPS):
        answer = {"ok": True, "ca_next_action": "none", "exit_code": 0}
        assert _check(tool, answer) == ("pass", None, [])


def test_nonzero_exit_code_passes_both_blocks():
    for tool in (_RPTS, _RPS):
        answer = {"ok": False, "ca_next_action": "none", "exit_code": 3}
        assert _check(tool, answer) == ("pass", None, [])


def test_absent_exit_code_passes_both_blocks():
    for tool in (_RPTS, _RPS):
        answer = {"ok": True, "ca_next_action": "none"}
        assert _check(tool, answer) == ("pass", None, [])


def test_string_exit_code_warns_both_blocks():
    for tool in (_RPTS, _RPS):
        answer = {"ok": True, "ca_next_action": "none", "exit_code": "x"}
        assert _check(tool, answer) == (
            "warn", None, ["type:exit_code=string!=integer"],
        )
