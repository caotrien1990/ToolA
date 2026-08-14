import asyncio
import json
from pathlib import Path

import code_tools
from orchestrator.fileops import xroot


def _rows(root: Path) -> list[dict]:
    ledger = root / ".orchestrator_cache" / xroot.LEDGER_NAME
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]


def test_xroot_outside_write_allowed_and_ledger_two_phase(tmp_path, monkeypatch):
    project = tmp_path / "project"
    outside = tmp_path / "BR_like" / "artifact.txt"
    project.mkdir()
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)

    result = json.loads(asyncio.run(code_tools.write_new_file(str(outside), "hello\n")))

    assert result["ok"] is True
    assert outside.read_text(encoding="utf-8") == "hello\n"
    rows = _rows(project)
    assert [row["phase"] for row in rows] == ["intent", "result"]
    assert rows[0]["path"] == str(outside.resolve())
    assert rows[0]["caller_tool"] == "write_new_file"
    assert rows[1]["ok"] is True
    assert rows[1]["bytes"] == len("hello\n".encode("utf-8"))
    assert rows[1]["sha256"]


def test_xroot_deny_groups(tmp_path, monkeypatch):
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)
    monkeypatch.setenv("USERPROFILE", str(home))

    cases = [
        ("system_dir", Path("C:/Windows/xroot_denied.txt")),
        ("drive_root", Path("D:/xroot_denied.txt")),
        ("user_dotfiles", home / ".ssh" / "config"),
    ]

    for group, target in cases:
        result = xroot.xroot_check(target, "write_new_file")
        assert result["allowed"] is False
        assert result["group"] == group


def test_xroot_live_files_refused_by_write_tool(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)
    live_targets = [
        project / "sessions.db",
        project / "graph.db-wal",
        project / "missions.db-shm",
        project / "librarian.db.bak",
        project / ".council" / "council_config.json",
        project / ".council" / "inbox_codoc.md",
        project / ".council" / "outbox_codoc.md",
    ]

    for target in live_targets:
        result = json.loads(asyncio.run(code_tools.write_new_file(str(target), "blocked\n")))
        assert result["ok"] is False
        assert result["error_name"] == "XROOT_DENIED"
        assert result["group"] == "live_files"


def test_xroot_whitelist_allows_user_dotfile_prefix(tmp_path, monkeypatch):
    project = tmp_path / "project"
    home = tmp_path / "home"
    allowed = home / ".ssh" / "allowed"
    project.mkdir()
    cache = project / ".orchestrator_cache"
    cache.mkdir()
    (cache / xroot.WHITELIST_NAME).write_text(json.dumps([str(allowed)]), encoding="utf-8")
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)
    monkeypatch.setenv("USERPROFILE", str(home))

    result = xroot.xroot_check(allowed / "config", "write_new_file")

    assert result["allowed"] is True
    assert result["group"] == "whitelist"


def test_xroot_policy_off_bypasses_denial_but_keeps_ledger(tmp_path, monkeypatch):
    project = tmp_path / "project"
    home = tmp_path / "home"
    target = home / ".aws" / "config"
    project.mkdir()
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("ORCHESTRATOR_XROOT_POLICY", "off")

    result = json.loads(asyncio.run(code_tools.write_new_file(str(target), "allowed\n")))

    assert result["ok"] is True
    rows = _rows(project)
    assert [row["phase"] for row in rows] == ["intent", "result"]
    assert rows[1]["ok"] is True

def test_xroot_write_fix_outside_allowed_and_ledger(tmp_path, monkeypatch):
    project = tmp_path / "project"
    outside = tmp_path / "BR_like" / "artifact.txt"
    project.mkdir()
    outside.parent.mkdir()
    outside.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)

    result = json.loads(asyncio.run(code_tools.write_fix(
        str(outside), 1, 1, "hello-fix\n", project_path=str(project),
    )))

    assert result.get("ok") is True, result
    assert result.get("error_name") != "PATH_OUTSIDE_PROJECT"
    assert outside.read_text(encoding="utf-8") == "hello-fix\n"
    rows = _rows(project)
    assert [row["phase"] for row in rows] == ["intent", "result"]
    assert rows[0]["path"] == str(outside.resolve())
    assert rows[0]["caller_tool"] == "write_fix"
    assert rows[1]["ok"] is True
    assert rows[1]["caller_tool"] == "write_fix"


def test_xroot_write_fix_system_dir_denied_missing_file(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)
    target = Path("C:/Windows/xroot_denied.txt")
    assert not target.exists()

    result = json.loads(asyncio.run(code_tools.write_fix(
        str(target), 1, 1, "blocked\n", project_path=str(project),
    )))

    assert result.get("ok") is False, result
    assert result.get("error_name") == "XROOT_DENIED"
    assert result.get("group") == "system_dir"


def test_xroot_write_fix_live_files_denied(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)

    result = json.loads(asyncio.run(code_tools.write_fix(
        ".council/council_config.json", 1, 1, "blocked\n",
        project_path=str(project),
    )))

    assert result.get("ok") is False, result
    assert result.get("error_name") == "XROOT_DENIED"
    assert result.get("group") == "live_files"


def test_xroot_write_transaction_outside_write_fix(tmp_path, monkeypatch):
    project = tmp_path / "project"
    outside = tmp_path / "BR_like" / "txn.txt"
    project.mkdir()
    outside.parent.mkdir()
    outside.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)

    result = json.loads(asyncio.run(code_tools.write_transaction(
        [{
            "file_path": str(outside),
            "start_line": 1,
            "end_line": 1,
            "new_content": "txn-fix\n",
        }],
        project_path=str(project),
        backup=False,
    )))

    assert result.get("ok") is True, result
    assert result.get("error_name") != "INVALID_OPERATION_TARGET"
    assert result.get("error_name") != "PATH_OUTSIDE_PROJECT"
    assert outside.read_text(encoding="utf-8") == "txn-fix\n"


def test_xroot_write_fix_in_project_relative_unchanged(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "inside.txt"
    target.write_text("keep\n", encoding="utf-8")
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)

    result = json.loads(asyncio.run(code_tools.write_fix(
        "inside.txt", 1, 1, "changed\n", project_path=str(project),
    )))

    assert result.get("error_name") != "PATH_OUTSIDE_PROJECT"
    assert result.get("error_name") != "XROOT_DENIED"
    ledger = project / ".orchestrator_cache" / xroot.LEDGER_NAME
    if ledger.exists():
        rows = _rows(project)
        assert all(row.get("caller_tool") != "write_fix" for row in rows)
