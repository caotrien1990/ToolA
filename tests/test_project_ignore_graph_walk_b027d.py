# -*- coding: utf-8 -*-
"""B027d: the CBM graph walk never saw the overlay either.

`indexer._walk_files` (`orchestrator/graph/indexer.py:117-129`) prunes with
`d not in skip` against the raw `SKIP_DIRS` set instead of calling `should_skip_dir`,
so `cbm_index` / `cbm_incremental_index` build a graph from a different file set than
`search_code` sees. Measured before the fix on a tree with `2.working` + `!build`:

    _walk_files  -> ['2.working/j.py', 'src/a.py']     # wrong in BOTH directions
    expected     -> ['build/made.py', 'src/a.py']

Both halves are wrong at once, which is why this is not cosmetic: the project-declared
skip is walked anyway, and the explicit un-skip is still pruned. All four call sites
(`:163`, `:165`, `:299`, `:301`) already hold the root, so nothing had to be threaded
through -- the parameter was simply never passed.

`skip_dirs=` stays honoured verbatim when a caller passes an explicit set: that
parameter means "use exactly this", and one test pins it so the overlay cannot start
overriding a caller who asked for something specific.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.graph.indexer import _walk_files  # noqa: E402


def _tree(ignore: str | None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="b027d_"))
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "2.working").mkdir()
    (root / "2.working" / "j.py").write_text("J = 1\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "made.py").write_text("B = 1\n", encoding="utf-8")
    if ignore is not None:
        (root / ".orchestrator_ignore").write_text(ignore, encoding="utf-8")
    return root


def _rel(root: Path, paths) -> list[str]:
    return sorted(str(Path(p).relative_to(root)).replace("\\", "/") for p in paths)


class TheGraphWalkHonoursTheOverlayTest(unittest.TestCase):

    def test_a_project_declared_skip_is_pruned(self):
        root = _tree("2.working\n")
        self.assertNotIn("2.working/j.py", _rel(root, _walk_files(str(root))))

    def test_an_unskipped_global_dir_is_walked(self):
        root = _tree("!build\n")
        self.assertIn("build/made.py", _rel(root, _walk_files(str(root))))

    def test_both_directions_at_once(self):
        root = _tree("2.working\n!build\n")
        self.assertEqual(_rel(root, _walk_files(str(root))),
                         ["build/made.py", "src/a.py"])

    def test_without_an_overlay_the_global_rules_still_apply(self):
        """Teeth stay in: no overlay means `build` is pruned and `2.working` is not."""
        root = _tree(None)
        got = _rel(root, _walk_files(str(root)))
        self.assertNotIn("build/made.py", got)
        self.assertIn("2.working/j.py", got)

    def test_an_explicit_skip_dirs_argument_still_wins(self):
        """`skip_dirs=` means 'use exactly this'. The overlay must not override a
        caller who asked for a specific set."""
        root = _tree("2.working\n")
        got = _rel(root, _walk_files(str(root), skip_dirs={"src"}))
        self.assertNotIn("src/a.py", got)
        self.assertIn("2.working/j.py", got)

    def test_a_corrupt_overlay_falls_back_to_the_global_rules(self):
        root = _tree(None)
        (root / ".orchestrator_ignore").write_bytes(b"\xff\xfe\x00junk")
        got = _rel(root, _walk_files(str(root)))
        self.assertIn("src/a.py", got)
        self.assertNotIn("build/made.py", got)


if __name__ == "__main__":
    unittest.main()
