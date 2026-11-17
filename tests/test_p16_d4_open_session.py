"""P16 Sprint 4 (D4): open_session 20-key top-level required -- regression tests.

Decided root cause (DW-2 5/5): the 20-key demand was satisfied on success
paths at all verbosity modes but missed by 17-19 keys on error paths; the
funnel stamps none of the 19; the leaf has no `ok:false` skip. The
intersection over ALL checked answers is `[ok]` (invalid-mode carries
`ok` + `requested_mode`, rotation-busy carries `ok` + `session_id`).
The D4 remedy DROPS the top-level `required` to `['ok']`; all 20 keys
STAY in `properties` (types checked when present); the nested
`matrix_audit` required stays `[]`. The enumeration proving the
intersection (handler returns, session-row passthrough, funnel stamps,
consumer check) is filed at records/d4-enumeration.md in the campaign
home. The five shapes below are the filed fixtures verbatim
(sandbox/fixtures/sprint4-d4.json, SHA-256 fc116615...); expected
verdicts are verbatim from the Sprint 4 post-fix probe run
(records/sprint4-d4-log.md). The warn reason is None: check_returns
returns ("warn", None, mismatches) -- see returns_shape.py check_returns.

ACCEPTED TRADE (R-17/F6 -- read before re-adding any required key):
success-path presence checking is SURRENDERED. Post-fix the leaf no
longer verifies that a success answer carries all 20 keys; it checks
only `ok` presence plus the declared types of properties that ARE
present. A success answer missing 19 keys passes. That is the decided
trade, pinned by test_dropping_any_other_declared_key_still_passes:
re-adding any key to `required` fails that test loudly, so a future
re-addition is a conscious decision, not drift.

LAPSE SCOPE: remainder (a)'s acceptance LAPSES FOR open_session ONLY
(Q1 fires -- the 20-key demand is unmeetable on error paths). Remainder
(a) STANDS for every other tool; this file's `[ok]` required list is
not a precedent for any other block.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import orchestrator_server as srv  # noqa: F401  (assembles TOOL_SCHEMAS: 238 -> 260)
from orchestrator.core import returns_shape as rs

_TOOL = "open_session"
_REPO_ROOT = Path(srv.__file__).resolve().parent
_BASELINE_FILE = _REPO_ROOT / "orchestrator" / "core" / "returns_baseline.json"
_DECLARED20 = ['ok', 'session_id', 'session_title', 'started_at', 'resumed', 'agent_id', 'project_name', 'project_path', 'requested_project_path', 'effective_project_path', 'relative_base_hint', 'matrix_audit', 'matrix_drift_present', 'progressive_gap_count', 'project_switch_context', 'transport_policy', 'cslt_policy', 'requested_mode', 'effective_mode', 'mode_applied']
_A_SUCCESS = {'agent_id': 'muse1', 'cslt_policy': {'a_first': True, 'agent_transport_policy': '', 'host_gate': {'agent_id': '', 'allowed': True, 'decision': '', 'is_hermes': False, 'native_policy': '', 'reason': '', 'scope': ''}, 'matrix': {'path': '', 'task_class_count': 0}, 'metrics_contract': {'bypass_event': '', 'bypass_rate_formula': '', 'group_by': [], 'required_counters': [], 'zero_denominator': ''}, 'north_star': {'acronym': 'CSLt', 'note': '', 'order': [], 'text': ''}, 'rules': []}, 'effective_mode': 'A', 'effective_project_path': 'D:/p', 'matrix_audit': {}, 'matrix_drift_present': False, 'mode_applied': True, 'ok': True, 'progressive_gap_count': 0, 'project_name': 'p', 'project_path': 'D:/p', 'project_switch_context': {'current_project_active_workflow_hint': '', 'current_project_recent_files_5': [], 'current_project_recent_queries_5': [], 'recent_projects_today': []}, 'relative_base_hint': '', 'requested_mode': 'A', 'requested_project_path': 'D:/p', 'resumed': False, 'session_id': 's1', 'session_title': 't', 'started_at': '2026-11-13T00:00:00', 'transport_policy': {'ca_next_action': '', 'platform': 'windows', 'posix': {'powershell_inline': '', 'preferred_tool': '', 'preferred_transport': '', 'shell_mode': ''}, 'schema_version': 'v15', 'windows': {'enforcement': {'error_names': [], 'tool': '', 'triggers_common': [], 'triggers_ps_only': []}, 'node_inline': '', 'one_shot_recipe_node': {'args': {'project_path': '', 'runtime': '', 'script_content': '', 'script_label': ''}, 'behaviour': '', 'tool': ''}, 'one_shot_recipe_powershell': {'args': {'project_path': '', 'runtime': '', 'script_content': '', 'script_label': ''}, 'behaviour': '', 'tool': ''}, 'path_as_argv_warning': '', 'powershell_inline': '', 'preferred_tool': '', 'preferred_transport': '', 'rationale': ''}}}
_A_INVALID_MODE = {'ok': False, 'requested_mode': 'bogus'}
_A_ROTATION_BUSY = {'ok': False, 'session_id': 's1'}
_A_MISTYPED = {'agent_id': 'muse1', 'cslt_policy': {'a_first': True, 'agent_transport_policy': '', 'host_gate': {'agent_id': '', 'allowed': True, 'decision': '', 'is_hermes': False, 'native_policy': '', 'reason': '', 'scope': ''}, 'matrix': {'path': '', 'task_class_count': 0}, 'metrics_contract': {'bypass_event': '', 'bypass_rate_formula': '', 'group_by': [], 'required_counters': [], 'zero_denominator': ''}, 'north_star': {'acronym': 'CSLt', 'note': '', 'order': [], 'text': ''}, 'rules': []}, 'effective_mode': 'A', 'effective_project_path': 'D:/p', 'matrix_audit': {}, 'matrix_drift_present': False, 'mode_applied': True, 'ok': True, 'progressive_gap_count': 0, 'project_name': 'p', 'project_path': 'D:/p', 'project_switch_context': {'current_project_active_workflow_hint': '', 'current_project_recent_files_5': [], 'current_project_recent_queries_5': [], 'recent_projects_today': []}, 'relative_base_hint': '', 'requested_mode': 'A', 'requested_project_path': 'D:/p', 'resumed': False, 'session_id': 5, 'session_title': 't', 'started_at': '2026-11-13T00:00:00', 'transport_policy': {'ca_next_action': '', 'platform': 'windows', 'posix': {'powershell_inline': '', 'preferred_tool': '', 'preferred_transport': '', 'shell_mode': ''}, 'schema_version': 'v15', 'windows': {'enforcement': {'error_names': [], 'tool': '', 'triggers_common': [], 'triggers_ps_only': []}, 'node_inline': '', 'one_shot_recipe_node': {'args': {'project_path': '', 'runtime': '', 'script_content': '', 'script_label': ''}, 'behaviour': '', 'tool': ''}, 'one_shot_recipe_powershell': {'args': {'project_path': '', 'runtime': '', 'script_content': '', 'script_label': ''}, 'behaviour': '', 'tool': ''}, 'path_as_argv_warning': '', 'powershell_inline': '', 'preferred_tool': '', 'preferred_transport': '', 'rationale': ''}}}
_A_MISSING_OK = {'agent_id': 'muse1', 'cslt_policy': {'a_first': True, 'agent_transport_policy': '', 'host_gate': {'agent_id': '', 'allowed': True, 'decision': '', 'is_hermes': False, 'native_policy': '', 'reason': '', 'scope': ''}, 'matrix': {'path': '', 'task_class_count': 0}, 'metrics_contract': {'bypass_event': '', 'bypass_rate_formula': '', 'group_by': [], 'required_counters': [], 'zero_denominator': ''}, 'north_star': {'acronym': 'CSLt', 'note': '', 'order': [], 'text': ''}, 'rules': []}, 'effective_mode': 'A', 'effective_project_path': 'D:/p', 'matrix_audit': {}, 'matrix_drift_present': False, 'mode_applied': True, 'progressive_gap_count': 0, 'project_name': 'p', 'project_path': 'D:/p', 'project_switch_context': {'current_project_active_workflow_hint': '', 'current_project_recent_files_5': [], 'current_project_recent_queries_5': [], 'recent_projects_today': []}, 'relative_base_hint': '', 'requested_mode': 'A', 'requested_project_path': 'D:/p', 'resumed': False, 'session_id': 's1', 'session_title': 't', 'started_at': '2026-11-13T00:00:00', 'transport_policy': {'ca_next_action': '', 'platform': 'windows', 'posix': {'powershell_inline': '', 'preferred_tool': '', 'preferred_transport': '', 'shell_mode': ''}, 'schema_version': 'v15', 'windows': {'enforcement': {'error_names': [], 'tool': '', 'triggers_common': [], 'triggers_ps_only': []}, 'node_inline': '', 'one_shot_recipe_node': {'args': {'project_path': '', 'runtime': '', 'script_content': '', 'script_label': ''}, 'behaviour': '', 'tool': ''}, 'one_shot_recipe_powershell': {'args': {'project_path': '', 'runtime': '', 'script_content': '', 'script_label': ''}, 'behaviour': '', 'tool': ''}, 'path_as_argv_warning': '', 'powershell_inline': '', 'preferred_tool': '', 'preferred_transport': '', 'rationale': ''}}}


def _raw_len(answer) -> int:
    """Byte length per the fixture raw_len convention (no sort_keys)."""
    return len(json.dumps(
        answer, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8"))


def _check(answer):
    return rs.check_returns(_TOOL, answer, _raw_len(answer))


def test_block_resolves_audited():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert rs.schema_state(block) == "audited", (
        f"{_TOOL}: schema_state is {rs.schema_state(block)!r}, "
        "the D4 fix requires 'audited'"
    )


def test_top_level_required_is_ok_only():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert block["required"] == ["ok"]
    props = block["properties"]
    assert len(props) == 34
    # All 20 declared keys STAY in properties (types checked when present).
    for key in _DECLARED20:
        assert key in props, f"{key!r} dropped from properties"
    assert props["ok"]["type"] == "boolean"  # ok also carries "x-dual": True
    assert props["session_id"]["type"] == "string"
    # Nested matrix_audit required [] UNCHANGED.
    assert props["matrix_audit"]["required"] == []


def test_sha_triple_and_depth_d():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    baseline = json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))
    pin = baseline[_TOOL]
    assert block["x-audit"]["schema_sha256"] == rs.canonical_schema_sha(block)
    assert pin["sha256"] == rs.canonical_schema_sha(block)
    assert block["x-audit"]["depth_D"] == 5
    assert pin["depth_D"] == 5


def test_success_shape_passes():
    assert _check(copy.deepcopy(_A_SUCCESS)) == ("pass", None, [])


def test_invalid_mode_shape_passes():
    # Error shape: ok + requested_mode only (E6 in the enumeration).
    assert _check(copy.deepcopy(_A_INVALID_MODE)) == ("pass", None, [])


def test_rotation_busy_shape_passes():
    # Error shape: ok + session_id only (E7 in the enumeration).
    assert _check(copy.deepcopy(_A_ROTATION_BUSY)) == ("pass", None, [])


def test_plain_error_shape_passes():
    # The _json_error shapes (E1-E4, E8) carry ok + error fields only.
    answer = {"ok": False, "error": "x", "error_name": "AGENT_ID_REQUIRED"}
    assert _check(answer) == ("pass", None, [])


def test_dropping_any_other_declared_key_still_passes():
    # The accepted trade, pinned: post-fix, no key but `ok` is demanded.
    for key in _DECLARED20:
        if key == "ok":
            continue
        answer = copy.deepcopy(_A_SUCCESS)
        del answer[key]
        assert _check(answer) == ("pass", None, []), (
            f"dropping {key!r} must pass post-fix (R-17/F6 trade)"
        )


def test_mistyped_session_id_warns():
    # Properties type checks stay live: integer session_id still warns.
    assert _check(copy.deepcopy(_A_MISTYPED)) == (
        "warn", None, ["type:session_id=integer!=string"],
    )


def test_missing_ok_warns():
    # The retained [ok] required list is still enforced.
    assert _check(copy.deepcopy(_A_MISSING_OK)) == (
        "warn", None, ["missing:ok"],
    )
