import asyncio
import json
from pathlib import Path

import pytest

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


def _n35_sandbox(tmp_path, monkeypatch):
    """A home whose ``.ssh`` holds one file, used as project_path -- the #35 sandbox."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "exist").write_text("old\n", encoding="utf-8")
    src = tmp_path / "src.txt"
    src.write_text("payload\n", encoding="utf-8")
    monkeypatch.setattr(xroot, "PROJECT_ROOT", tmp_path / "a_tree")
    monkeypatch.setenv("USERPROFILE", str(home))
    return home, src


# One case per write gate, so a gate that goes back to judging "cross-root" from the
# spelling turns exactly its own case red. Each value maps (home, src) to the call.
_N35_SITES = {
    "write_new_file relative": lambda h, s: code_tools.write_new_file(".ssh/rel", "x\n", project_path=str(h)),
    "write_new_file absolute in project": lambda h, s: code_tools.write_new_file(
        str(h / ".ssh" / "abs"), "x\n", project_path=str(h)),
    "copy_file relative dst": lambda h, s: code_tools.copy_file(str(s), ".ssh/cp", project_path=str(h)),
    "write_fix relative": lambda h, s: code_tools.write_fix(".ssh/exist", 1, 1, "new\n", project_path=str(h)),
    "write_transaction relative": lambda h, s: code_tools.write_transaction(
        [{"file_path": ".ssh/exist", "start_line": 1, "end_line": 1, "new_content": "new\n"}],
        project_path=str(h), backup=False),
}


@pytest.mark.parametrize("site", sorted(_N35_SITES))
def test_the_deny_list_does_not_depend_on_how_the_target_is_spelled(site, tmp_path, monkeypatch):
    """#35, measured 2026-09-25: with project_path set to the home directory, four of
    these five landed in ``.ssh``; only an absolute path with NO project_path was
    refused, because each gate read the deny list only for what it judged cross-root,
    and judged that from the spelling. Being inside project_path grants nothing: the
    project root is whatever the caller says it is.
    """
    home, src = _n35_sandbox(tmp_path, monkeypatch)

    result = json.loads(asyncio.run(_N35_SITES[site](home, src)))

    assert result.get("ok") is False, result
    # Exact: write_transaction's pre-flight must refuse, not its dry run of write_fix,
    # which answers DRY_RUN_FAILED -- otherwise that gate is pinned by the next one down.
    assert result.get("error_name") == "XROOT_DENIED", result
    assert sorted(p.name for p in (home / ".ssh").iterdir()) == ["exist"]
    assert (home / ".ssh" / "exist").read_text(encoding="utf-8") == "old\n"


@pytest.mark.parametrize("site", sorted(_N35_SITES))
def test_the_same_spellings_outside_the_deny_list_still_write(site, tmp_path, monkeypatch):
    """Positive control for the test above: the fix refuses the credential directory,
    not the spelling: the same five calls, with the deny list pointed elsewhere, write."""
    home, src = _n35_sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(xroot, "_user_dotfile_roots", lambda: [home / ".ssh-elsewhere"])

    result = json.loads(asyncio.run(_N35_SITES[site](home, src)))

    assert result.get("ok") is True, result


def test_an_in_project_write_takes_no_xroot_route_however_spelled(tmp_path, monkeypatch):
    """#35: ``cross_root`` picks the audit route, and it is read from the resolved
    target -- an absolute path into the project is an in-project write, not an xroot
    one, so it leaves no xroot ledger row, exactly like the relative spelling."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(xroot, "PROJECT_ROOT", tmp_path / "a_tree")

    for name in ("rel.txt", str(project / "abs.txt")):
        result = json.loads(asyncio.run(code_tools.write_new_file(name, "x\n", project_path=str(project))))
        assert result.get("ok") is True, result

    assert not (tmp_path / "a_tree" / ".orchestrator_cache" / xroot.LEDGER_NAME).exists()
    verdict = xroot.classify_write_target(project / "abs.txt", project)
    assert (verdict["allowed"], verdict["cross_root"], verdict["managed"]) == (True, False, False)


def test_write_targets_reads_every_field_that_names_a_target(tmp_path):
    """#35: the dispatcher read only the FIRST of path/file_path/dst/target_file."""
    root = tmp_path / "p"
    other = tmp_path / "q"
    body = {
        "project_path": str(root),
        "file_path": "a.py",
        "destination": "b.py",
        "dst": str(other / "c.py"),
        "src": "read_only.py",
        "paths": ["d.py", ""],
        "operations": [{"file_path": "e.py"}, {"path": "f.py", "project_path": str(other)}],
    }

    assert xroot.write_targets(body) == [
        root / "a.py", other / "c.py", root / "b.py", root / "d.py", root / "e.py", other / "f.py",
    ]
    # A relative target with no project_path is the tool's to resolve, not the daemon cwd's.
    assert xroot.write_targets({"file_path": "a.py", "dst": str(other / "c.py")}) == [other / "c.py"]
