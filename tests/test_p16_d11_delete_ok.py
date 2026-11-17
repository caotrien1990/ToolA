"""P16 Sprint 5 (D11): delete_file handler must emit ok:false on error paths.

Decided root cause (DW-5 5/5, FIX-AT-HANDLER): three bare returns in
orchestrator/tools/write/core.py:2696/:2698/:2705 construct values with
no `ok` key. The falsy-error hole is code-proven: _tool_result_failed
(orchestrator_server.py:2220) tests `res.get("error")` for truthiness,
_status is gated on it (:16884), and the funnel's ok stamp is gated on
error-status (:17026/:17039). The O-7 edge (empty str(exc) at :2705)
defeats detection AND rescue: {"error": ""} reads as success.

The full enumeration (all 8 returns, key sets, bare/carrier classes,
builder disposition, funnel-stamp analysis, consumer check) is filed
at records/d11-enumeration.md in the campaign home. The :2676 builder
path already emits ok:false via _json_error_dict and is verified, not
edited; the :2715 carrier inherits :2703/:2705 and is never counted
separately. The funnel rescue stays as defense-in-depth and is not
touched by the fix.

Every test below pins one enumerated return path. A future bare return
fails these tests. The empty-str(exc) test is the O-7 unit evidence;
the live served-shape evidence is owed to Stage 4 (see the sprint
record). All file operations happen under TemporaryDirectory scratch;
no repo file is touched. The unlink-failure tests stub Path.unlink so
no real deletion is attempted on those paths.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest import mock

import code_tools as ct
import orchestrator_server as srv  # noqa: F401  (assembles TOOL_SCHEMAS: 238 -> 260)
from orchestrator.core import returns_shape as rs
from orchestrator.tools.write.core import delete_file

_TOOL = "delete_file"


def _run(file_path, project_path=""):
    return json.loads(asyncio.run(delete_file(file_path, project_path)))


def _raw_len(answer) -> int:
    return len(json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _check(answer):
    return rs.check_returns(_TOOL, answer, _raw_len(answer))


def _assert_leaf_pass(answer):
    verdict, _reason, mismatches = _check(answer)
    assert verdict == "pass", (verdict, mismatches, answer)
    assert mismatches == [], (mismatches, answer)


def test_missing_file_emits_ok_false():
    """core.py:2696 -- the missing-file bare return."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run("missing-target.txt", project_path=tmp)
    assert result.get("ok") is False, result
    assert result["error"] == "File not found: missing-target.txt", result
    assert set(result) == {"ok", "error"}, result
    assert srv._tool_result_failed(result) is True
    _assert_leaf_pass(result)


def test_not_a_file_emits_ok_false():
    """core.py:2698 -- the not-a-file bare return."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(tmp, project_path="")
    assert result.get("ok") is False, result
    assert result["error"] == "Not a file: " + tmp, result
    assert set(result) == {"ok", "error"}, result
    assert srv._tool_result_failed(result) is True
    _assert_leaf_pass(result)


def test_unlink_failure_emits_ok_false():
    """core.py:2705 -- the unlink-exception dict, via the :2715 carrier."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "victim.txt"
        target.write_text("do not delete\n", encoding="utf-8")
        with mock.patch.object(ct.Path, "unlink", side_effect=Exception("boom")):
            result = _run(str(target), project_path=tmp)
        assert target.exists(), "stubbed unlink must not delete the file"
    assert result.get("ok") is False, result
    assert result["error"] == "boom", result
    assert set(result) == {"ok", "error"}, result
    assert srv._tool_result_failed(result) is True
    _assert_leaf_pass(result)


def test_empty_str_exc_emits_ok_false_o7():
    """O-7: Exception("") -- empty error text must still say ok:false.

    Pre-fix {"error": ""} defeats _tool_result_failed (falsy) AND the
    funnel rescue (status gates on it), so the ledger records success.
    """
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "victim.txt"
        target.write_text("do not delete\n", encoding="utf-8")
        with mock.patch.object(ct.Path, "unlink", side_effect=Exception("")):
            result = _run(str(target), project_path=tmp)
        assert target.exists(), "stubbed unlink must not delete the file"
    assert "ok" in result, result
    assert result.get("ok") is False, result
    assert result["error"] == "", result
    assert set(result) == {"ok", "error"}, result
    assert srv._tool_result_failed(result) is True
    _assert_leaf_pass(result)


def test_builder_path_emits_ok_false():
    """core.py:2676 -- the _json_path_resolve_error builder path.

    Enumerated shape: the builder already emits ok:false via
    _json_error_dict; verified-not-edited, pinned here so a builder
    regression fails loudly.
    """
    with tempfile.TemporaryDirectory() as tmp:
        result = _run("..\\escape.txt", project_path=tmp)
    assert result.get("ok") is False, result
    assert result["error_name"] == "PATH_OUTSIDE_PROJECT", result
    assert set(result) == {
        "ca_next_action",
        "error",
        "error_name",
        "ok",
        "phase",
        "reason",
        "retryable",
        "severity",
        "type",
    }, result
    assert srv._tool_result_failed(result) is True
    _assert_leaf_pass(result)


def test_relative_guard_emits_ok_false():
    """core.py:2668 -- relative path without project_path already says ok:false."""
    result = _run("rel.txt", project_path="")
    assert result.get("ok") is False, result
    assert result["error"] == "project_path is required when file_path is relative", result
    assert set(result) == {"ok", "error", "hint"}, result
    assert srv._tool_result_failed(result) is True
    _assert_leaf_pass(result)


def test_protected_path_emits_ok_false():
    """core.py:2686 -- restart-control denylist already says ok:false."""
    with tempfile.TemporaryDirectory() as tmp:
        secret = Path(tmp) / ".orchestrator_lifecycle" / "restart_control.secret"
        secret.parent.mkdir(parents=True)
        secret.write_bytes(b"X" * 32)
        result = _run(str(secret), project_path="")
        assert secret.exists(), "denied deletion must not remove the file"
    assert result.get("ok") is False, result
    assert result["error_name"] == "CONTROL_FILE_DELETE_DENIED", result
    assert set(result) == {"ok", "error", "error_name", "protected_path", "ca_next_action"}, result
    assert srv._tool_result_failed(result) is True
    _assert_leaf_pass(result)


def test_success_path_emits_ok_true():
    """core.py:2703 via the :2715 carrier -- the success shape is untouched."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "package.json").write_text("{}\n", encoding="utf-8")
        target = Path(tmp) / "gone.txt"
        target.write_text("bye\n", encoding="utf-8")
        result = _run(str(target), project_path=tmp)
        assert not target.exists(), "success path must delete the file"
    assert result.get("ok") is True, result
    assert result["deleted"] == str(target), result
    assert set(result) == {"ok", "deleted"}, result
    assert srv._tool_result_failed(result) is False
    _assert_leaf_pass(result)


def test_block_requires_ok_only():
    """The delete_file returns block demands exactly [ok] (leaf contract)."""
    block = rs.resolve_returns(_TOOL)
    assert rs.schema_state(block) == "audited"
    assert block["required"] == ["ok"]
    assert sorted(block["properties"]) == ["deleted", "error", "hint", "ok"]


def test_bare_shape_would_warn_missing_ok():
    """Control: the leaf still warns on a bare shape (no ok:false skip)."""
    verdict, _reason, mismatches = _check({"error": "x"})
    assert verdict == "warn", (verdict, mismatches)
    assert mismatches == ["missing:ok"], mismatches


def test_fixed_shapes_pass_leaf():
    """Control: fixed shapes pass the leaf, including the empty-error edge."""
    for shape in (
        {"ok": False, "error": "File not found: x"},
        {"ok": False, "error": "Not a file: x"},
        {"ok": False, "error": ""},
        {"ok": True, "deleted": "x"},
    ):
        _assert_leaf_pass(shape)
