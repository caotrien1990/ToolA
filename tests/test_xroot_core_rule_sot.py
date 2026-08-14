"""CORE_RULE.txt is SOT: write_fix edits it; sync_rule only distributes."""
import asyncio
import json

import code_tools
from orchestrator.fileops import xroot


def test_xroot_core_rule_sot_not_live_file(tmp_path, monkeypatch):
    project = tmp_path / "project"
    target = project / "examples" / "CORE_RULE.txt"
    project.mkdir()
    target.parent.mkdir()
    target.write_text("# CORE\n", encoding="utf-8")
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)

    result = xroot.xroot_check(target, "write_fix")
    assert result["allowed"] is True
    assert result.get("group") != "live_files"


def test_xroot_core_rule_sot_writable(tmp_path, monkeypatch):
    """SOT CORE_RULE.txt must not be blocked as live_files / sync_rule."""
    project = tmp_path / "project"
    target = project / "examples" / "CORE_RULE.txt"
    project.mkdir()
    target.parent.mkdir()
    target.write_text("# old core\n", encoding="utf-8")
    monkeypatch.setattr(xroot, "PROJECT_ROOT", project)

    result = json.loads(asyncio.run(code_tools.write_fix(
        str(target), 1, 1, "# via write_fix\n", project_path=str(project),
    )))
    blob = json.dumps(result, ensure_ascii=False)
    assert result.get("error_name") != "XROOT_DENIED", result
    assert "must be modified through manage_agent" not in blob
    assert result.get("group") != "live_files"
