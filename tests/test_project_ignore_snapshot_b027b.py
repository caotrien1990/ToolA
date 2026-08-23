# -*- coding: utf-8 -*-
"""B027 follow-up: the cache is restart-free, but `search_code` serves a SNAPSHOT.

Found by live-smoke-testing B027 on the restarted daemon. The first probe wrote the
`.orchestrator_ignore` AFTER a search had already run, and the entry appeared to do
nothing -- 2 hits both before and after. It was not a defect in the overlay: the
snapshot `search_code` serves had already been published, and a snapshot does not
re-walk. Measured on the same throwaway project, an ordinary NEW source file
(`src/b.py`) is equally invisible until the snapshot is republished, so this is
pre-existing snapshot behaviour that B027 inherits, not something B027 introduced.

It still matters, because I wrote **"an edit takes effect immediately"** into the
README off the cache design (`load_project_ignore` keys on `(root, st_mtime_ns,
st_size)`, so no restart is needed -- that part is true and is pinned by
`test_an_edit_is_picked_up_without_a_restart`). "No restart" and "immediately
visible in search_code" are two different claims, and only the first one is true.
A doc sentence a caller can follow to a wrong conclusion is the same defect class as
B028b's warning that named an absent field.

This file pins BOTH halves so the distinction cannot drift:
  * the overlay is honoured on the walk the snapshot is published from, and
  * the README sentence a caller reads says `refresh_project_map`, not "immediately".
"""

from __future__ import annotations

import functools
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import search_snapshots  # noqa: E402
from orchestrator.fileops import common as fo  # noqa: E402


def _project(with_ignore: bool) -> Path:
    root = Path(tempfile.mkdtemp(prefix="b027_snap_test_"))
    _B031_TEMP_ROOTS.append(root)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("NEEDLE = 1\n", encoding="utf-8")
    (root / "2.working").mkdir()
    (root / "2.working" / "junk.py").write_text("NEEDLE = 2\n", encoding="utf-8")
    if with_ignore:
        (root / ".orchestrator_ignore").write_text("2.working\n", encoding="utf-8")
    return root


def _records(root: Path) -> list[str]:
    """Publish a snapshot exactly the way `orchestrator/tools/search.py` does."""
    snapshot_id = search_snapshots.publish_snapshot(
        root,
        include_file=fo.should_scan_for_text,
        skip_dir=functools.partial(fo.should_skip_dir, project_root=root),
        index=None,
    )
    pin = search_snapshots.pin_current_snapshot(root, expected_id=snapshot_id)
    try:
        out = []
        for record in pin.iter_records():
            if isinstance(record, dict):
                out.append(str(record.get("path")))
            else:
                out.append(str(getattr(record, "path", record)))
        return sorted(out)
    finally:
        pin.release()



# B031: a module-level factory has no `self`, so cleanup cannot be `addCleanup`.
# Every root it hands out is recorded here and removed by `tearDownModule`. Measured
# before this: 3 directories left in %TEMP% by one run of this module alone.
_B031_TEMP_ROOTS: list[Path] = []


def tearDownModule() -> None:
    for _root in _B031_TEMP_ROOTS:
        shutil.rmtree(_root, ignore_errors=True)
    _B031_TEMP_ROOTS.clear()


class TheOverlayReachesThePublishedSnapshotTest(unittest.TestCase):
    """`search_code` never walks the tree at query time -- it reads a snapshot. If
    the overlay does not reach the PUBLISH walk, B027 is dead for the tool it was
    asked for, no matter how correct `should_skip_dir` is in isolation."""

    def test_an_ignored_directory_is_absent_from_the_snapshot(self):
        self.assertEqual(_records(_project(with_ignore=True)), ["src/a.py"])

    def test_without_the_overlay_the_same_tree_keeps_both_files(self):
        self.assertEqual(
            _records(_project(with_ignore=False)),
            ["2.working/junk.py", "src/a.py"])

    def test_the_bound_partial_is_what_carries_the_root(self):
        """A mutant that drops `project_root=` from either `functools.partial` leaves
        `should_skip_dir` perfectly correct and B027 completely dead on search."""
        root = _project(with_ignore=True)
        bound = functools.partial(fo.should_skip_dir, project_root=root)
        self.assertTrue(bound("2.working"))
        self.assertFalse(fo.should_skip_dir("2.working"))


class TheDocumentedRefreshPathIsTheMeasuredOneTest(unittest.TestCase):
    """Measured live on generation 69f4f4e33aff: writing the overlay after a search
    changed nothing until `refresh_project_map`, and then it took effect (2 hits ->
    1 hit). The README must say that, because a caller who reads "immediately",
    sees no change and concludes the entry is broken has been misled by the docs."""

    def _readme(self) -> str:
        return (ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_readme_does_not_promise_an_immediate_effect(self):
        readme = self._readme()
        start = readme.find("## Per-project ignore")
        self.assertNotEqual(start, -1, "the .orchestrator_ignore section is gone")
        section = readme[start:start + 2600]
        self.assertNotIn("takes effect immediately", section)

    def test_the_readme_names_refresh_project_map(self):
        readme = self._readme()
        start = readme.find("## Per-project ignore")
        section = readme[start:start + 2600]
        self.assertIn("refresh_project_map", section,
                      "the section must tell a caller how to make an edit visible")
        self.assertIn("no restart", section.lower(),
                      "the restart-free half of the claim is true and worth keeping")


if __name__ == "__main__":
    unittest.main()
