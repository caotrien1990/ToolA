# -*- coding: utf-8 -*-
"""Guards for campaign-1608 (write-path hang on a mislocated snapshot store).

Root cause chain that hung two write tests forever:
  1. a write on a tempfile with NO project_path made find_project_root climb the
     parents of a %TEMP% path up to the user HOME dir, where a stray junk marker
     (.orchestrator_cache / package.json) made HOME look like a project root;
  2. that HOME snapshot store had ballooned to 4.3 GB / 106k files;
  3. surgical_mapper._publish_search_snapshot called publish_snapshot WITHOUT a
     deadline, so _load_manifest(verify_blobs=True) re-hashed every blob forever.

Two independent barriers pin the fix:
  * V1 - the write path now passes a wall-clock deadline; a store it cannot walk
    in budget raises SnapshotDeadlineExceeded (fail loud) instead of hanging;
  * V2 - find_project_root (BOTH copies) refuses to treat the HOME dir, any
    ancestor of HOME, or a filesystem/drive root as a project root, so tempfile
    paths no longer resolve to a mislocated store in the first place.
"""
import os
import sys
import time
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import surgical_mapper as sm                          # noqa: E402
from orchestrator import search_snapshots             # noqa: E402
import code_tools                                      # noqa: E402
from orchestrator.fileops import common               # noqa: E402


class V1PublishDeadlineTestCase(unittest.TestCase):
    """V1: the write path bounds snapshot publication and fails loud on timeout."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v1_deadline_")).resolve()
        for i in range(6):
            (self.tmp / f"m{i}.py").write_text(f"x{i} = {i}\n", encoding="utf-8")
        sm.generate_project_map(str(self.tmp))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_budget_is_env_tunable_and_generous_by_default(self):
        # default must be generous (a real full-walk on a big repo is legitimate),
        # only a hang-stop - never a throttle. Mirrors the query-index budget shape.
        old = os.environ.pop("ORCHESTRATOR_SNAPSHOT_PUBLISH_DEADLINE_SEC", None)
        try:
            self.assertGreaterEqual(sm._publish_snapshot_deadline_seconds(), 120.0)
            os.environ["ORCHESTRATOR_SNAPSHOT_PUBLISH_DEADLINE_SEC"] = "7"
            self.assertEqual(sm._publish_snapshot_deadline_seconds(), 7.0)
            # garbage clamps to the floor, never crashes the write path
            os.environ["ORCHESTRATOR_SNAPSHOT_PUBLISH_DEADLINE_SEC"] = "not-a-number"
            self.assertGreaterEqual(sm._publish_snapshot_deadline_seconds(), 5.0)
        finally:
            os.environ.pop("ORCHESTRATOR_SNAPSHOT_PUBLISH_DEADLINE_SEC", None)
            if old is not None:
                os.environ["ORCHESTRATOR_SNAPSHOT_PUBLISH_DEADLINE_SEC"] = old

    def test_publish_passes_a_deadline_to_publish_snapshot(self):
        # The whole point of V1: the write path must hand publish_snapshot a
        # concrete wall-clock deadline (the read path already did; the write path
        # did NOT, which is what allowed the infinite blob re-hash). Assert the
        # kwarg is present and is a future monotonic timestamp.
        seen = {}

        def _spy(project_root, **kw):
            seen.update(kw)
            return "v1-spy"

        real = search_snapshots.publish_snapshot
        search_snapshots.publish_snapshot = _spy
        try:
            sm._publish_search_snapshot(self.tmp, {"files": {}}, None)
        finally:
            search_snapshots.publish_snapshot = real
        self.assertIn("deadline_at", seen)
        self.assertIsInstance(seen["deadline_at"], float)
        self.assertGreater(seen["deadline_at"], time.monotonic())

    def test_deadline_propagates_and_blocks_the_selfheal_fallback(self):
        # Two invariants at once:
        #  * SnapshotDeadlineExceeded from the fast path must be re-raised, NOT
        #    caught by the generic `except Exception -> _publish(None)` self-heal
        #    (which would just hit the same exhausted store again);
        #  * so publish_snapshot must be called exactly ONCE.
        calls = {"n": 0}

        def _boom(project_root, **kw):
            calls["n"] += 1
            raise search_snapshots.SnapshotDeadlineExceeded("blob hash deadline exceeded")

        real = search_snapshots.publish_snapshot
        search_snapshots.publish_snapshot = _boom
        try:
            t0 = time.monotonic()
            with self.assertRaises(search_snapshots.SnapshotDeadlineExceeded):
                # changed_paths set -> the self-heal branch is eligible; the deadline
                # must still short-circuit it.
                sm._publish_search_snapshot(self.tmp, {"files": {}}, {"m0.py": "modified"})
            self.assertLess(time.monotonic() - t0, 5.0)   # fast, never a hang
        finally:
            search_snapshots.publish_snapshot = real
        self.assertEqual(calls["n"], 1, "self-heal fallback was wrongly re-entered")

    def test_deadline_not_swallowed_by_cache_update(self):
        # 7-RULE #7: _update_cache_after_write must NOT mask a deadline as a plain
        # cache_synced=False; it has to re-raise so the caller sees the real state.
        # TEST SEAM: bypass volatile-root skip (self.tmp is under %TEMP%).
        os.environ["ORCH_RECONCILE_FORCE_FULL"] = "1"
        try:
            def _boom(project_root, **kw):
                raise search_snapshots.SnapshotDeadlineExceeded("blob hash deadline exceeded")

            real = search_snapshots.publish_snapshot
            search_snapshots.publish_snapshot = _boom
            try:
                target = self.tmp / "m0.py"
                target.write_text("x0 = 999\n", encoding="utf-8")
                with self.assertRaises(search_snapshots.SnapshotDeadlineExceeded):
                    code_tools._update_cache_after_write(str(target), str(self.tmp))
            finally:
                search_snapshots.publish_snapshot = real
        finally:
            del os.environ["ORCH_RECONCILE_FORCE_FULL"]

    @unittest.skipUnless(
        (Path.home() / ".orchestrator_cache" / "search_snapshots" / "CURRENT").exists(),
        "no real oversized snapshot store present to exercise",
    )
    def test_real_oversized_store_fails_fast_not_hang(self):
        # Integration proof against the ACTUAL mislocated store (campaign evidence):
        # a real budget (not a monkeypatch) must turn the former infinite blob
        # re-hash into a bounded SnapshotDeadlineExceeded. A generous 8s budget is
        # far above any healthy publish yet bounded well under a hang.
        home = Path.home()
        t0 = time.monotonic()
        with self.assertRaises(search_snapshots.SnapshotDeadlineExceeded):
            search_snapshots.publish_snapshot(
                home, index=None, changed_paths=None,
                deadline_at=time.monotonic() + 8.0,
            )
        self.assertLess(time.monotonic() - t0, 30.0)  # bounded, not the old infinite hang


class V2FindProjectRootGuardTestCase(unittest.TestCase):
    """V2: neither copy of find_project_root accepts HOME / an ancestor / a drive
    root as a project root, even when a marker file sits there."""

    # both live copies must behave identically - the guard is duplicated, so the
    # test drives BOTH to catch drift between them.
    _IMPLS = (code_tools.find_project_root, common.find_project_root)

    def test_home_with_marker_is_not_a_root(self):
        home = Path.home().resolve()
        marker = home / ".orchestrator_cache"
        created = False
        if not marker.exists():
            marker.mkdir(parents=True, exist_ok=True)
            created = True
        try:
            # a file directly under HOME must NOT resolve HOME as its project root.
            probe = home / "____cg1608_probe____.py"
            for impl in self._IMPLS:
                got = impl(str(probe))
                self.assertNotEqual(
                    got and Path(got).resolve(), home,
                    f"{impl.__module__}.{impl.__name__} returned HOME as root",
                )
        finally:
            if created:
                try:
                    marker.rmdir()
                except OSError:
                    pass

    def test_tempfile_path_does_not_climb_to_home(self):
        # A tempfile lives under HOME via %TEMP%. With no marker in the temp dir,
        # root inference must fall through (None) rather than latch onto HOME.
        with tempfile.TemporaryDirectory(prefix="cg1608_") as d:
            fp = Path(d) / "leaf.py"
            fp.write_text("z = 1\n", encoding="utf-8")
            home = Path.home().resolve()
            for impl in self._IMPLS:
                got = impl(str(fp))
                if got is not None:
                    self.assertNotEqual(Path(got).resolve(), home,
                                        f"{impl.__module__} climbed to HOME")

    def test_drive_root_is_not_a_root(self):
        # A drive/filesystem root must never be accepted, even if a marker exists
        # there. We do not write to a real drive root; we assert the anchor logic
        # by pointing at the drive root of HOME directly.
        anchor = Path(Path.home().anchor)  # e.g. C:\
        if not anchor.exists():
            self.skipTest("no filesystem anchor available")
        for impl in self._IMPLS:
            got = impl(str(anchor / "____cg1608_nonexistent____.py"))
            if got is not None:
                self.assertNotEqual(Path(got).resolve(), anchor.resolve(),
                                    f"{impl.__module__} accepted a drive root")

    def test_legitimate_nested_project_still_resolves(self):
        # The guard must NOT over-reach: a real project dir that happens to live
        # under HOME (but is not HOME itself) must still resolve normally.
        with tempfile.TemporaryDirectory(prefix="cg1608_proj_") as d:
            proj = Path(d) / "myproj"
            proj.mkdir()
            (proj / "requirements.txt").write_text("x\n", encoding="utf-8")
            leaf = proj / "src"
            leaf.mkdir()
            fp = leaf / "app.py"
            fp.write_text("a = 1\n", encoding="utf-8")
            for impl in self._IMPLS:
                got = impl(str(fp))
                self.assertIsNotNone(got, f"{impl.__module__} failed a real project")
                self.assertEqual(Path(got).resolve(), proj.resolve())


if __name__ == "__main__":
    unittest.main()
