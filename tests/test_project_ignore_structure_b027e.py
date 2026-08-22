# -*- coding: utf-8 -*-
"""B027e: `list_structure` was the one skip gate the overlay could not reach.

B027 gave `should_skip_dir` an optional `project_root`; B027c gave the FILE gate a
shared helper; B027d gave the graph walk the same. `orchestrator/tools/read/fs.py:52`
was the last site still asking the global-only question -- and Anh's verification
flagged it with a fair defence: *a directory should stay browsable*.

The defence does not survive measurement. `list_structure` ALREADY filters through
`should_skip_dir`, so it never showed a raw tree: `node_modules`, `venv312` and
`__pycache__` were hidden before B027 and are hidden now. So the tool is not "the
browsable one"; it is a filtered view whose filter a project could not correct. That
makes `!name` unreachable at the one surface a human uses to check what the overlay
did, which is the worst place for it to be missing -- an ignore file whose effect you
cannot see is an ignore file you cannot debug.

What the overlay must NOT do here is hide something the global rules would have shown.
That direction is CASE-2008's shape: a structure listing that silently omits a real
directory is how a caller concludes a file does not exist. So the extra-skip half is
deliberately NOT honoured, and one test below pins that asymmetry as intended rather
than as an oversight.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.tools.read import fs  # noqa: E402


def _tree(tmp: Path, overlay: str | None) -> str:
    """A project holding one global-skipped dir, one plain dir, one extra-skip name."""
    (tmp / "src").mkdir(parents=True, exist_ok=True)
    (tmp / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp / "build").mkdir(exist_ok=True)          # in the global SKIP_DIRS
    (tmp / "build" / "made.py").write_text("y = 2\n", encoding="utf-8")
    (tmp / "2.working").mkdir(exist_ok=True)      # not global; overlay may declare it
    (tmp / "2.working" / "j.py").write_text("z = 3\n", encoding="utf-8")
    if overlay is not None:
        (tmp / ".orchestrator_ignore").write_text(overlay, encoding="utf-8")
    out = asyncio.run(fs.list_structure(path=str(tmp), depth=2,
                                        project_path=str(tmp), save_output=False))
    payload = json.loads(out)
    assert payload.get("ok") is True, payload
    return payload["result"]


class TheStructureListingHonoursAnUnskipTest(unittest.TestCase):
    """`!name` must be visible at the surface a human uses to verify the overlay."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(prefix="b027e_")
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_global_skip_is_still_hidden_without_an_overlay(self):
        """The baseline this tool has always had -- pinned so the fix cannot widen it."""
        tree = _tree(self.tmp, None)
        self.assertIn("src/", tree)
        self.assertNotIn("build/", tree,
                         "list_structure has always filtered through should_skip_dir")

    def test_an_unskipped_directory_becomes_visible(self):
        tree = _tree(self.tmp, "!build\n")
        self.assertIn("build/", tree,
                      "!build is unreachable at the one surface used to verify it")
        self.assertIn("src/", tree)

    def test_the_unskip_reaches_the_files_inside_it(self):
        """Showing the directory but not its contents would be a half answer."""
        tree = _tree(self.tmp, "!build\n")
        self.assertIn("made.py", tree)

    def test_an_extra_skip_is_deliberately_not_honoured(self):
        """The asymmetry is the design, not an oversight.

        Hiding a real directory from a structure listing is how a caller concludes a
        file does not exist -- CASE-2008's shape. `!name` only ever REVEALS, so it
        cannot cause that; a project-declared skip could, so this gate does not take
        one. If that ever changes it must be a decision with its own entry, not a
        side effect of sharing a helper.
        """
        tree = _tree(self.tmp, "2.working\n")
        self.assertIn("2.working/", tree,
                      "an overlay must not be able to hide a directory from a listing")

    def test_council_stays_visible_regardless(self):
        (self.tmp / ".council").mkdir(exist_ok=True)
        (self.tmp / ".council" / "t.md").write_text("x\n", encoding="utf-8")
        tree = _tree(self.tmp, ".council\n")
        self.assertIn(".council/", tree, "the SOT transcript tree is unhideable")

    def test_a_corrupt_overlay_falls_back_to_the_global_rules(self):
        tree = _tree(self.tmp, "\x00\x01 not a name \x00\n")
        self.assertIn("src/", tree)
        self.assertNotIn("build/", tree)

    def test_no_project_path_still_answers_the_global_rule(self):
        """`list_structure` may be called with only `path`; that must not raise."""
        (self.tmp / "src").mkdir(parents=True, exist_ok=True)
        (self.tmp / "build").mkdir(exist_ok=True)
        (self.tmp / ".orchestrator_ignore").write_text("!build\n", encoding="utf-8")
        out = asyncio.run(fs.list_structure(path=str(self.tmp), depth=2,
                                            save_output=False))
        payload = json.loads(out)
        self.assertTrue(payload.get("ok"))
        self.assertIn("build/", payload["result"],
                      "the walk root IS the project here, so the overlay applies")


if __name__ == "__main__":
    unittest.main()
