"""P10: ocr_scan must not answer ok:true with files:[] for a nonexistent path.

`_rel_inside` checks containment only (the candidate stays inside project_path),
not existence. A path that does not exist passed the check, the `ocr scan`
subprocess found nothing, and the tool answered ok:true with an empty file list
-- indistinguishable from "this path genuinely has nothing to review".

The fix adds a per-candidate existence check: a missing path now returns
ok:false / PATH_NOT_FOUND naming the offending path, before any subprocess
runs. This pins both sides: missing -> PATH_NOT_FOUND, present -> the scan
proceeds exactly as before (fake _ocr_run here; the real binary lives under
gitignored venv_ocr/).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.tools import ocr_review as mod


async def _fake_ocr_run(args, root, timeout):
    return {
        "ok": True,
        "stdout": json.dumps({"files": ["mod.py"], "findings": []}),
        "stderr": "",
        "returncode": 0,
    }


def test_missing_path_reports_path_not_found(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(mod, "_ocr_run", _fake_ocr_run)
    raw = asyncio.run(mod.ocr_scan(project_path=str(repo), path="no/such/file.py"))
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error_name"] == "PATH_NOT_FOUND"
    assert "no/such/file.py" in payload["error"]


def test_existing_path_scans_as_before(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(mod, "_ocr_run", _fake_ocr_run)
    raw = asyncio.run(mod.ocr_scan(project_path=str(repo), path="mod.py"))
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["paths"] == ["mod.py"]
    assert payload["files"] == ["mod.py"]
