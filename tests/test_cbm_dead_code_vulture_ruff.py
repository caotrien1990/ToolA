import asyncio
import json

from orchestrator.graph.extractors.vulture_ruff import (
    _merge_dedup,
    _parse_ruff_output,
    _parse_vulture_output,
    scan_ruff,
    scan_vulture,
)
from orchestrator.graph.tools import cbm_dead_code, ruff_fix


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
