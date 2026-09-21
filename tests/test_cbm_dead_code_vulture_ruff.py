import asyncio
import json
from fnmatch import fnmatch

from orchestrator.graph.extractors.vulture_ruff import (
    _merge_dedup,
    _parse_ruff_output,
    _parse_vulture_output,
    scan_ruff,
    scan_vulture,
)
from orchestrator.graph.tools import cbm_dead_code, ruff_fix
from orchestrator.skip_rules import SKIP_DIRS


class _Completed:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 1):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_scan_vulture_parses_fixture(monkeypatch, tmp_path):
    fixture = "pkg/mod.py:7: unused function 'helper' (60% confidence)\n"

    def fake_run(*args, **kwargs):
        return _Completed(fixture)

    monkeypatch.setattr("subprocess.run", fake_run)
    candidates = scan_vulture(str(tmp_path))

    assert candidates == [{
        "symbol": "helper",
        "name": "helper",
        "file": "pkg/mod.py",
        "file_path": "pkg/mod.py",
        "line": 7,
        "start_line": 7,
        "kind": "unused function",
        "category": "dead_code",
        "confidence": 0.6,
        "source": "vulture",
        "reason": "unused function 'helper'",
    }]
    assert _parse_vulture_output(fixture)[0]["symbol"] == "helper"


def test_scan_ruff_parses_fixture(monkeypatch, tmp_path):
    fixture = "pkg/mod.py:1:8: F401 [*] `os` imported but unused\n"

    def fake_run(*args, **kwargs):
        return _Completed(fixture)

    monkeypatch.setattr("subprocess.run", fake_run)
    candidates = scan_ruff(str(tmp_path))

    assert candidates[0]["symbol"] == "os"
    assert candidates[0]["kind"] == "unused import"
    assert candidates[0]["confidence"] == 1.0
    assert candidates[0]["source"] == "ruff"
    assert _parse_ruff_output(fixture)[0]["rule"] == "F401"


def test_merge_dedup_keeps_highest_confidence_duplicate():
    candidates = [
        {"file_path": "pkg/mod.py", "symbol": "os", "kind": "unused import", "confidence": 0.9, "source": "vulture"},
        {"file_path": "pkg/mod.py", "symbol": "os", "kind": "unused import", "confidence": 1.0, "source": "ruff"},
    ]

    merged = _merge_dedup(candidates)

    assert len(merged) == 1
    assert merged[0]["source"] == "ruff"


def test_cbm_dead_code_python_project_uses_vulture_ruff(tmp_path):
    (tmp_path / "mod.py").write_text(
        "import os\n\n"
        "def used():\n"
        "    unused_var = 1\n"
        "    return 2\n\n"
        "used()\n",
        encoding="utf-8",
    )

    payload = json.loads(asyncio.run(cbm_dead_code(project_path=str(tmp_path), limit=20)))

    assert payload["ok"] is True
    result = payload["result"]
    assert result["engine"] == "vulture+ruff"
    findings = {(c["kind"], c["symbol"]) for c in result["candidates"]}
    assert ("unused import", "os") in findings
    assert ("unused variable", "unused_var") in findings


def test_cbm_dead_code_js_project_falls_back_ast(tmp_path):
    (tmp_path / "app.js").write_text("function unused() { return 1; }\n", encoding="utf-8")

    payload = json.loads(asyncio.run(cbm_dead_code(project_path=str(tmp_path), limit=5)))

    assert payload["ok"] is True
    assert payload["result"]["engine"] == "ast-p0-v1"
    assert "candidates" in payload["result"]


def test_ruff_fix_previews_without_mutating(tmp_path):
    source = tmp_path / "mod.py"
    original = "import os\nprint('ok')\n"
    source.write_text(original, encoding="utf-8")

    payload = json.loads(asyncio.run(ruff_fix(
        project_path=str(tmp_path), path="mod.py",
    )))

    assert payload["ok"] is True
    result = payload["result"]
    assert result["mode"] == "preview"
    assert result["applied"] is False
    assert "-import os" in result["diff"]
    assert source.read_text(encoding="utf-8") == original


def test_ruff_fix_applies_safe_fixes(tmp_path):
    source = tmp_path / "mod.py"
    source.write_text("import os\nprint('ok')\n", encoding="utf-8")

    payload = json.loads(asyncio.run(ruff_fix(
        project_path=str(tmp_path), path="mod.py", apply=True,
    )))

    assert payload["ok"] is True
    assert payload["result"]["mode"] == "apply"
    assert payload["result"]["applied"] is True
    assert source.read_text(encoding="utf-8") == "print('ok')\n"


def test_ruff_fix_rejects_path_outside_project(tmp_path):
    payload = json.loads(asyncio.run(ruff_fix(
        project_path=str(tmp_path), path="../outside.py",
    )))

    assert payload["ok"] is False
    assert payload["error_name"] == "PATH_OUTSIDE_PROJECT"


def test_ruff_fix_clean_file_reports_unchanged(tmp_path):
    source = tmp_path / "clean.py"
    source.write_text("print('ok')\n", encoding="utf-8")

    payload = json.loads(asyncio.run(ruff_fix(
        project_path=str(tmp_path), path="clean.py", apply=True,
    )))

    assert payload["ok"] is True
    assert payload["result"]["changed"] is False


def test_ruff_fix_nonstandard_exit_fails_loud(monkeypatch, tmp_path):
    from orchestrator.graph import tools

    source = tmp_path / "mod.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(
        tools.subprocess,
        "run",
        lambda *a, **k: type("P", (), {"returncode": 2, "stdout": "", "stderr": "boom"})(),
    )
    payload = json.loads(asyncio.run(ruff_fix(
        project_path=str(tmp_path), path="mod.py", apply=True,
    )))
    assert payload["ok"] is False
    assert payload["error_name"] == "RUFF_EXEC_FAILED"
    assert source.read_text(encoding="utf-8") == "print('ok')\n"


def test_ruff_fix_validation_failure_reverts(monkeypatch, tmp_path):
    from orchestrator.graph import tools
    import code_tools

    source = tmp_path / "mod.py"
    original = "import os\nprint('ok')\n"
    source.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        code_tools,
        "validate_file",
        lambda *_a, **_k: json.dumps({"ok": False, "error": "forced"}),
    )
    payload = json.loads(asyncio.run(tools.ruff_fix(
        project_path=str(tmp_path), path="mod.py", apply=True,
    )))
    assert payload["ok"] is False
    assert payload["error_name"] == "RUFF_VALIDATION_FAILED_AUTO_REVERT"
    assert payload["rollback_verified"] is True
    assert source.read_text(encoding="utf-8") == original


def _captured_vulture_argv(monkeypatch, target, **kwargs) -> list:
    """Run scan_vulture against a fake subprocess and hand back the argv it built."""
    seen: list = []

    def fake_run(cmd, *args, **kw):
        seen.append(list(cmd))
        return _Completed("", returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    scan_vulture(str(target), **kwargs)
    return seen[0]


def _excluded(path: str, patterns: list) -> bool:
    """Decide a path the way vulture itself does.

    vulture's exclude test is `any(fnmatch(name, pattern))` (core.py:276, case=False),
    so fnmatch IS the contract -- including its Windows normcase of "/" to "\\".
    Reimplemented here instead of imported: production drives vulture as a
    subprocess and never imports it, so `import vulture` in this file would turn a
    missing scanner into collection errors for every other test in it.
    """
    return any(fnmatch(path, pattern) for pattern in patterns)


def test_vulture_excludes_the_projects_own_skip_dirs(monkeypatch, tmp_path):
    """A tree scan must not lint the virtualenv.

    Measured 2026-09-22 on ToolA: vulture has no built-in exclude, so aiming it at
    the repo root took 66.1s and returned 1001 of 2004 findings from inside
    venv312/, against 727ms for orchestrator/ alone. cbm_dead_code(scope="full")
    therefore could not finish inside its own 25s server budget -- the default
    contract guaranteed its own failure. Ruff never needed this because its own
    default exclude already drops site-packages.
    """
    argv = _captured_vulture_argv(monkeypatch, tmp_path)
    assert "--exclude" in argv, "a tree scan without --exclude walks the venv"
    patterns = argv[argv.index("--exclude") + 1].split(",")

    # Derived from SKIP_DIRS, not from a list copied into this test: when the SOT
    # grows, the scanner follows it and this assertion keeps holding by itself.
    for name in SKIP_DIRS:
        assert f"*/{name}/*" in patterns
        assert f"*\\{name}\\*" in patterns


def test_vulture_exclude_patterns_are_directory_anchored(monkeypatch, tmp_path):
    """The patterns must match a DIRECTORY component, never a filename substring.

    vulture wraps a bare pattern into *pattern* (core.py:268-271), so passing the
    bare SKIP_DIRS entries would exclude `orchestrator/distill_points.py` (via
    "dist") and `build_graph.py` (via "build") -- measured, and silent: real source
    would simply stop being scanned with nothing in the payload to say so. Both
    separators are required because fnmatch normcases "/" to "\\" on Windows and
    leaves it untouched on POSIX, so neither shape alone covers both platforms.
    """
    argv = _captured_vulture_argv(monkeypatch, tmp_path)
    patterns = argv[argv.index("--exclude") + 1].split(",")

    for name in SKIP_DIRS:
        assert name not in patterns, f"bare {name!r} would match any path containing it"

    for path in (r"D:\repo\orchestrator\distill_points.py",
                 r"D:\repo\orchestrator\build_graph.py",
                 "/repo/orchestrator/distill_points.py"):
        assert not _excluded(path, patterns), f"{path} is source and must be scanned"

    for path in (r"D:\repo\venv312\Lib\site-packages\x.py",
                 "/repo/venv312/lib/x.py"):
        assert _excluded(path, patterns), f"{path} is not this project's source"


def test_an_explicit_file_list_is_never_filtered(monkeypatch, tmp_path):
    """`files` is the caller's own choice of targets, so no --exclude is added.

    Filtering it here would drop files that were asked for BY NAME and report
    nothing about the omission; the exclude exists only to stop a tree walk from
    wandering out of the project's source.
    """
    target = tmp_path / "venv312" / "mod.py"
    target.parent.mkdir(parents=True)
    target.write_text("x = 1\n", encoding="utf-8")

    argv = _captured_vulture_argv(monkeypatch, tmp_path, files=[str(target)])
    assert "--exclude" not in argv
    assert str(target) in argv
