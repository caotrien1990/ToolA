"""R83: dry_run and the real write must enforce the SAME index precondition."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import code_tools
import surgical_mapper as sm


def _run(coro):
    return json.loads(asyncio.run(coro))


def _project(tmp_path, indexed: bool):
    root = tmp_path / "proj"
    root.mkdir()
    target = root / "mod.py"
    target.write_text("A = 1\nB = 2\nC = 3\n", encoding="utf-8")
    if indexed:
        sm.generate_project_map(str(root))
    return root, target


class TestDryRunParity:
    def test_dry_run_is_blocked_when_the_real_write_would_be(self, tmp_path):
        root, target = _project(tmp_path, indexed=False)
        dry = _run(code_tools.write_fix(str(target), 2, 2, "B = 20\n",
                                        project_path=str(root), dry_run=True))
        real = _run(code_tools.write_fix(str(target), 2, 2, "B = 20\n",
                                         project_path=str(root), dry_run=False))
        # The bug: dry-run said ok:true + handed out a dry_run_id, then the identical
        # payload with dry_run=false failed on INDEX. Both must now agree.
        assert dry.get("ok") is False, dry
        assert real.get("ok") is False, real
        assert "INDEX not found" in str(dry.get("error", ""))
        assert dry.get("ca_next_action") == "generate_project_map"
        assert "dry_run_id" not in dry
        assert target.read_text(encoding="utf-8") == "A = 1\nB = 2\nC = 3\n"

    def test_both_pass_once_the_index_exists(self, tmp_path):
        root, target = _project(tmp_path, indexed=True)
        dry = _run(code_tools.write_fix(str(target), 2, 2, "B = 20\n",
                                        project_path=str(root), dry_run=True))
        assert dry.get("ok") is True, dry
        assert dry.get("dry_run") is True
        assert target.read_text(encoding="utf-8") == "A = 1\nB = 2\nC = 3\n"

        real = _run(code_tools.write_fix(str(target), 2, 2, "B = 20\n",
                                         project_path=str(root), dry_run=False))
        assert real.get("ok") is True, real
        assert "B = 20" in target.read_text(encoding="utf-8")

    def test_outside_root_is_not_gated_on_this_projects_index(self, tmp_path):
        # Regression: the first R83 patch gated every dry-run, which broke
        # write_transaction's internal pre-check for cross-root targets. A file
        # outside the project root answers to the xroot policy, not to INDEX.json.
        root, _ = _project(tmp_path, indexed=False)
        outside = tmp_path / "elsewhere" / "note.txt"
        outside.parent.mkdir()
        outside.write_text("old\n", encoding="utf-8")
        from orchestrator.tools.write import core as write_core
        assert write_core._index_missing_error(outside, str(root)) is None
        assert write_core._index_missing_error(root / "mod.py", str(root)) is not None
