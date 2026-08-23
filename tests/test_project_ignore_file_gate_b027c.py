# -*- coding: utf-8 -*-
"""B027c: the overlay stopped at the walk gate and never reached the FILE gate.

Reported by Anh's independent verification of B027, and both symptoms reproduce.
B027 bound `project_root` into every `should_skip_dir` call -- the DIRECTORY gate --
and that was correct. But `_skip_reason` (`surgical_mapper.py:380-383`) and
`classify_group` (`:420`) take `project_root` and still compare against the raw
`SKIP_DIRS` set, so the FILE gate never learned the overlay exists.

Two symptoms, one root:

  1. `!name` un-skip is dead end-to-end. `should_skip_dir('build', root)` returns
     False, so the walk descends into `build/` -- and then `_skip_reason` answers
     `ignored_directory:build` and the file is dropped anyway. Gate 1 opens, gate 2
     closes. This is exactly the case B027's own comment gives as the reason `!name`
     exists: "a JS repo whose real source lives in build/ could not opt out".

  2. An ignored file comes BACK to the snapshot after every write. The
     `changed_paths` fast path (`search_snapshots.py:1016-1042`) skips the walk
     entirely and filters only through `include_file` -> `_should_scan_file` ->
     `_skip_reason`. With the overlay invisible there, search results for an ignored
     tree flip-flop depending on whether a write just happened. Same family as A03.

Why no existing test caught it: every B027 test stops at `should_skip_dir`. In the
extra-skip direction the walk cuts the tree before any file reaches gate 2, so the
directory gate alone LOOKS sufficient. Only `!name` -- where the walk deliberately
descends -- exposes the second gate. A test must cross `_skip_reason` /
`generate_project_map` / the snapshot to see this at all.
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

import surgical_mapper as sm  # noqa: E402
from orchestrator import search_snapshots as ss  # noqa: E402
from orchestrator.fileops.common import should_skip_dir  # noqa: E402


# B031: a module-level factory has no `self`, so cleanup cannot be `addCleanup`.
# Every root it hands out is recorded here and removed by `tearDownModule`. Measured
# before this: 10 directories left in %TEMP% by one run of this module alone.
_B031_TEMP_ROOTS: list[Path] = []


def tearDownModule() -> None:
    for _root in _B031_TEMP_ROOTS:
        shutil.rmtree(_root, ignore_errors=True)
    _B031_TEMP_ROOTS.clear()


def _tree(ignore: str | None, *, extra_dir: str) -> Path:
    """A project with `src/app.py` plus one file under `extra_dir`."""
    root = Path(tempfile.mkdtemp(prefix="b027c_"))
    _B031_TEMP_ROOTS.append(root)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("NEEDLE = 1\n", encoding="utf-8")
    (root / extra_dir).mkdir(parents=True)
    (root / extra_dir / "made.py").write_text("NEEDLE = 2\n", encoding="utf-8")
    if ignore is not None:
        (root / ".orchestrator_ignore").write_text(ignore, encoding="utf-8")
    return root


class TheUnskipReachesTheFileGateTest(unittest.TestCase):
    """`!build` must open BOTH gates. Opening only the directory gate is what made
    the feature look present and behave absent."""

    def test_unskip_lets_the_file_through_skip_reason(self):
        root = _tree("!build\n", extra_dir="build")
        f = root / "build" / "made.py"
        self.assertFalse(should_skip_dir("build", project_root=root),
                         "gate 1 (walk) must descend -- this half already worked")
        self.assertIsNone(sm._skip_reason(f, str(root)),
                          "gate 2 (file) must not re-apply SKIP_DIRS after an explicit !unskip")

    def test_unskip_lets_the_file_through_should_scan_file(self):
        root = _tree("!build\n", extra_dir="build")
        self.assertTrue(sm._should_scan_file(root / "build" / "made.py", project_root=root))

    def test_unskip_is_not_classified_as_ignored(self):
        root = _tree("!build\n", extra_dir="build")
        f = root / "build" / "made.py"
        self.assertNotEqual(sm.classify_group(str(f), str(root)), "ignored")

    def test_without_the_unskip_the_same_file_stays_ignored(self):
        """The guard must not become 'never skip anything' -- teeth stay in."""
        root = _tree(None, extra_dir="build")
        f = root / "build" / "made.py"
        self.assertEqual(sm._skip_reason(f, str(root)), "ignored_directory:build")
        self.assertEqual(sm.classify_group(str(f), str(root)), "ignored")


class TheExtraSkipAlsoReachesTheFileGateTest(unittest.TestCase):
    """The other direction: a project-declared skip must close gate 2 as well, or the
    fast path re-admits the file (symptom 2)."""

    def test_a_project_declared_skip_closes_the_file_gate(self):
        root = _tree("2.working\n", extra_dir="2.working")
        f = root / "2.working" / "made.py"
        self.assertTrue(should_skip_dir("2.working", project_root=root))
        self.assertEqual(sm._skip_reason(f, str(root)), "ignored_directory:2.working")

    def test_a_project_declared_skip_classifies_as_ignored(self):
        root = _tree("2.working\n", extra_dir="2.working")
        f = root / "2.working" / "made.py"
        self.assertEqual(sm.classify_group(str(f), str(root)), "ignored")


class TheSnapshotDoesNotFlipFlopAcrossAWriteTest(unittest.TestCase):
    """Symptom 2, measured through the snapshot the way `search_code` reads it.

    Publishing with `changed_paths` is the walk-avoidance path a write takes. Before
    the fix this returned the ignored file, so a search answered differently
    depending on whether a write had just happened."""

    def _publish(self, root: Path, changed: dict | None) -> list[str]:
        snapshot_id = ss.publish_snapshot(
            root,
            include_file=functools.partial(sm._should_scan_file, project_root=root),
            skip_dir=functools.partial(should_skip_dir, project_root=root),
            index=None,
            changed_paths=changed,
        )
        pin = ss.pin_current_snapshot(root, expected_id=snapshot_id)
        try:
            return sorted(str(r.get("path")) for r in pin.iter_records())
        finally:
            pin.release()

    def test_an_ignored_file_does_not_return_after_a_write(self):
        root = _tree("2.working\n", extra_dir="2.working")
        self.assertEqual(self._publish(root, None), ["src/app.py"])
        after_write = self._publish(root, {"2.working/made.py": "modified"})
        self.assertEqual(after_write, ["src/app.py"],
                         "the fast path must honour the overlay, not just the walk")

    def test_an_unskipped_file_is_present_on_both_paths(self):
        """`!build` must be visible on the full walk AND after a write -- the same
        answer either way, which is the property that was broken."""
        root = _tree("!build\n", extra_dir="build")
        full = self._publish(root, None)
        self.assertIn("build/made.py", full)
        fast = self._publish(root, {"build/made.py": "modified"})
        self.assertEqual(fast, full)


class TheOverlayFailsOpenAtTheFileGateTest(unittest.TestCase):
    """B027's FAIL OPEN contract has to hold at gate 2 too: a corrupt or absent
    overlay must degrade to the global rules, never crash a map generation."""

    def test_garbage_bytes_fall_back_to_the_global_rules(self):
        root = _tree(None, extra_dir="build")
        (root / ".orchestrator_ignore").write_bytes(b"\xff\xfe\x00garbage\x00")
        f = root / "build" / "made.py"
        self.assertEqual(sm._skip_reason(f, str(root)), "ignored_directory:build")

    def test_no_root_keeps_the_one_argument_contract(self):
        root = _tree("!build\n", extra_dir="build")
        f = root / "build" / "made.py"
        self.assertEqual(sm._skip_reason(f, None), "ignored_directory:build")


if __name__ == "__main__":
    unittest.main()
