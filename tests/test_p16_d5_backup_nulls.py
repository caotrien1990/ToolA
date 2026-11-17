"""P16 Sprint 1 (D5): backup_status null types -- regression tests.

The backup_status returns block declares size_mb / backup_age_hours as
number-first anyOf (number, null) in BOTH the latest object branch and
the all_backups[] items node, and latest itself as an anyOf of the
typed object branch plus null, because the handler deterministically
emits None on legacy-manifest paths (code_tools.py backup_status:
size_mb None at :4446, backup_age_hours None at :4451, latest None at
:4455). The inside-branch required list is the SAME 4-key list the
latest node carried before the fix; a sibling required beside an anyOf
is never evaluated (returns_shape.py:288-303), so the list moved
INSIDE the object branch. Top-level required [ok, status] is
unchanged. These tests pin the leaf verdicts for those shapes against
the live assembled block -- no fixtures, per the F5-FIXTURE decision
(PLAN.md "Regression tests"): the a45 battery pins the committed
fixture count at 81, so regression shapes live here as explicit
(verdict, reason, mismatches) assertions instead of fixture files.

Expected values are verbatim from the Sprint 1 post-fix probe run
(records/sprint1-d5-log.md; the 7 filed fixtures, all matching
expect_post_fix, plus the handler-path shapes below). The warn reason
is None: check_returns returns ("warn", None, mismatches) -- see
returns_shape.py check_returns.

Mismatch-string note: when the latest OBJECT branch fails on an inner
constraint (a mistyped key or a missing inner required key), the leaf
reports the anyOf summary "type:latest=object!=object"
(returns_shape.py:297-300: the summary uses the FIRST branch's type
and the branch probes are discarded). The verdict (warn) is what
proves the inside-branch required and the per-key type checks are
evaluated; the summary form is the leaf's lawful output for an
object-typed first branch, not a distinct failure mode. The
all_backups[] items node carries no anyOf wrapper, so its inner
mismatches keep precise key paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import orchestrator_server as srv  # noqa: F401  (assembles TOOL_SCHEMAS: 238 -> 260)
from orchestrator.core import returns_shape as rs

_TOOL = "backup_status"
_REPO_ROOT = Path(srv.__file__).resolve().parent
_BASELINE_FILE = _REPO_ROOT / "orchestrator" / "core" / "returns_baseline.json"
_FOUR_KEY = ["created_at", "backup_path", "size_mb", "backup_age_hours"]
_ANYOF_NUMBER = {"anyOf": [{"type": "number"}, {"type": "null"}]}


def _canonical_raw_len(answer) -> int:
    """Byte length of the answer's canonical UTF-8 dump (fixture raw_len)."""
    return len(json.dumps(
        answer, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8"))


def _item(size_mb=12, backup_age_hours=3):
    """One backup entry carrying ALL declared properties."""
    return {
        "backup_age_hours": backup_age_hours,
        "backup_path": "D:/backups/backup-20261112",
        "backup_scope": "full",
        "created_at": "2026-11-12T00:00:00",
        "files_skipped": 0,
        "files_written": 10,
        "size_mb": size_mb,
        "skipped_sample": [],
        "target_paths": ["D:/project"],
    }


def _answer(latest, items):
    """A complete backup_status answer carrying ALL top-level properties."""
    return {
        "ok": True,
        "status": "available",
        "total_backups": len(items),
        "latest": latest,
        "all_backups": items,
    }


def _check(answer):
    return rs.check_returns(_TOOL, answer, _canonical_raw_len(answer))


def test_block_resolves_audited():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    assert rs.schema_state(block) == "audited", (
        f"{_TOOL}: schema_state is {rs.schema_state(block)!r}, "
        "the D5 fix requires 'audited'"
    )


def test_declarations_are_number_first_anyof_with_typed_latest_branch():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    props = block["properties"]
    assert block["required"] == ["ok", "status"]
    assert sorted(props.keys()) == [
        "all_backups", "latest", "ok", "status", "total_backups",
    ]
    items = props["all_backups"]["items"]
    assert props["all_backups"]["type"] == "array"
    assert items["type"] == "object"
    assert items["required"] == _FOUR_KEY
    assert items["properties"]["size_mb"] == _ANYOF_NUMBER
    assert items["properties"]["backup_age_hours"] == _ANYOF_NUMBER
    latest = props["latest"]
    assert set(latest.keys()) == {"anyOf"}
    assert len(latest["anyOf"]) == 2
    assert latest["anyOf"][1] == {"type": "null"}
    branch = latest["anyOf"][0]
    assert branch["type"] == "object"
    assert branch["required"] == _FOUR_KEY
    assert branch["properties"]["size_mb"] == _ANYOF_NUMBER
    assert branch["properties"]["backup_age_hours"] == _ANYOF_NUMBER


def test_sha_triple_and_depth_d():
    block = rs.resolve_returns(_TOOL)
    assert block is not None, f"{_TOOL}: no returns block resolved"
    baseline = json.loads(_BASELINE_FILE.read_text(encoding="utf-8"))
    pin = baseline[_TOOL]
    assert block["x-audit"]["schema_sha256"] == rs.canonical_schema_sha(block)
    assert pin["sha256"] == rs.canonical_schema_sha(block)
    assert block["x-audit"]["depth_D"] == 4
    assert pin["depth_D"] == 4


def test_legacy_manifest_nulls_pass():
    answer = _answer(_item(size_mb=None, backup_age_hours=None), [_item()])
    assert _check(answer) == ("pass", None, [])


def test_current_manifest_ints_pass():
    answer = _answer(_item(), [_item()])
    assert _check(answer) == ("pass", None, [])


def test_latest_none_passes():
    # Handler path: manifest present but no backup file survives
    # (existing empty -> latest None, code_tools.py:4455).
    answer = _answer(None, [])
    assert _check(answer) == ("pass", None, [])


def test_missing_manifest_shape_passes():
    # Handler path: no manifest file (ok + status only; latest and
    # all_backups absent -- absence is lawful, they are not required).
    answer = {
        "ok": True,
        "status": "missing",
        "message": "No Orchestrator backup manifest found.",
        "next_action": "Run backup_project(project_path, force=True) before large edits.",
    }
    assert _check(answer) == ("pass", None, [])


def test_item_with_null_size_mb_passes():
    answer = _answer(_item(), [_item(size_mb=None)])
    assert _check(answer) == ("pass", None, [])


def test_item_with_null_backup_age_hours_passes():
    answer = _answer(_item(), [_item(backup_age_hours=None)])
    assert _check(answer) == ("pass", None, [])


def test_string_size_mb_in_latest_warns():
    answer = _answer(_item(size_mb="x"), [_item()])
    assert _check(answer) == ("warn", None, ["type:latest=object!=object"])


def test_string_size_mb_in_item_warns_with_precise_path():
    answer = _answer(_item(), [_item(size_mb="x")])
    assert _check(answer) == (
        "warn", None, ["type:all_backups.size_mb=string!=number"],
    )


def test_latest_missing_inner_required_key_warns():
    latest = _item()
    del latest["backup_age_hours"]
    answer = _answer(latest, [_item()])
    assert _check(answer) == ("warn", None, ["type:latest=object!=object"])


def test_empty_latest_object_warns():
    answer = _answer({}, [_item()])
    assert _check(answer) == ("warn", None, ["type:latest=object!=object"])


def test_string_latest_warns():
    answer = _answer("x", [_item()])
    assert _check(answer) == ("warn", None, ["type:latest=string!=object"])


def test_number_latest_warns():
    answer = _answer(5, [_item()])
    assert _check(answer) == ("warn", None, ["type:latest=integer!=object"])
