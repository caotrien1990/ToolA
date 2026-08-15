"""A08 regression: boot reaper for orphaned busy-snapshot temp files.

WHY (root cause): BusySnapshotPublisher.publish_once() writes
``restart_busy_snapshot.json.tmp.<pid>.<tid>.<hex>`` and then os.replace()s it
onto the real snapshot, unlinking the temp in a finally. When the writer process
is hard-killed (the restart pipeline SIGKILLs the old PID) between temp-create
and replace, the temp is orphaned and nothing ever cleans it — temps from dead
PIDs (observed: 3948, 61612, 38272) accumulate across restarts. There was no
boot reaper.

The fix (orchestrator.lifecycle.restart_protocol.reap_orphan_busy_snapshots)
removes a temp ONLY when its embedded writer PID is dead AND the file is older
than a floor (Windows PID-reuse defense), never the current PID, and returns a
structured summary so the sweep is observable (fail-loud, 7-RULE #7).

These are real-object tests: they synthesize temp files on disk with a dead PID,
a live PID (this process), and a dead-but-too-young PID, then assert the reaper
deletes exactly the dead+old orphan and preserves the others. If the reaper is
removed or made unconditional, these fail.
"""
import os
import time
import unittest
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.lifecycle.restart_protocol import (
    reap_orphan_busy_snapshots,
    _SNAPSHOT_TMP_MIN_AGE_S,
)


def _find_dead_pid() -> int:
    """Return a PID that is almost certainly not a live process."""
    # High, unlikely-to-be-allocated PID. Verified against the reaper's own
    # liveness check below via a direct probe so the test is not flaky.
    from orchestrator.lifecycle.restart_protocol import _snapshot_pid_alive
    for candidate in (4294967, 999999, 987654, 876543):
        if not _snapshot_pid_alive(candidate):
            return candidate
    raise unittest.SkipTest("could not find a reliably-dead PID on this host")


class ReapOrphanBusySnapshotsA08(unittest.TestCase):
    def setUp(self):
        self._dir = Path(tempfile.mkdtemp(prefix="a08reap_"))
        self.snapshot = self._dir / "restart_busy_snapshot.json"
        self.dead_pid = _find_dead_pid()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)

    def _mk_tmp(self, pid: int, *, age_s: float, tid: int = 111,
                hex_suffix: str = "deadbeef") -> Path:
        p = self.snapshot.with_name(
            f"{self.snapshot.name}.tmp.{pid}.{tid}.{hex_suffix}")
        p.write_text("{}", encoding="utf-8")
        if age_s:
            old = time.time() - age_s
            os.utime(p, (old, old))
        return p

    def test_reaps_dead_pid_old_temp(self):
        orphan = self._mk_tmp(self.dead_pid, age_s=_SNAPSHOT_TMP_MIN_AGE_S + 120)
        self.assertTrue(orphan.exists())
        result = reap_orphan_busy_snapshots(self.snapshot)
        self.assertFalse(orphan.exists(), "dead+old orphan must be reaped")
        self.assertIn(orphan.name, result["reaped"])
        self.assertEqual(result["reaped_count"], 1)
        self.assertEqual(result["errors"], [])

    def test_keeps_live_pid_temp(self):
        """A temp owned by a LIVE writer (this process) must never be reaped —
        deleting it would corrupt an in-flight atomic write."""
        live = self._mk_tmp(os.getpid(), age_s=_SNAPSHOT_TMP_MIN_AGE_S + 500)
        result = reap_orphan_busy_snapshots(self.snapshot)
        self.assertTrue(live.exists(), "live-PID temp must be preserved")
        self.assertIn(live.name, result["kept_alive"])
        self.assertEqual(result["reaped_count"], 0)

    def test_keeps_dead_pid_but_young_temp(self):
        """PID-reuse defense: a dead PID whose temp is fresher than the age floor
        is kept, because on Windows a recycled PID could belong to a live writer
        that just created this temp."""
        young = self._mk_tmp(self.dead_pid, age_s=1.0)
        result = reap_orphan_busy_snapshots(self.snapshot)
        self.assertTrue(young.exists(), "dead-but-young temp must be kept")
        self.assertIn(young.name, result["kept_young"])
        self.assertEqual(result["reaped_count"], 0)

    def test_mixed_population_reaps_only_the_right_one(self):
        orphan = self._mk_tmp(self.dead_pid, age_s=_SNAPSHOT_TMP_MIN_AGE_S + 300,
                              tid=222, hex_suffix="aaaa1111")
        live = self._mk_tmp(os.getpid(), age_s=_SNAPSHOT_TMP_MIN_AGE_S + 300,
                            tid=333, hex_suffix="bbbb2222")
        young = self._mk_tmp(self.dead_pid, age_s=2.0,
                             tid=444, hex_suffix="cccc3333")
        # A non-matching file that shares the prefix root but is the real snapshot.
        self.snapshot.write_text("{}", encoding="utf-8")

        result = reap_orphan_busy_snapshots(self.snapshot)

        self.assertFalse(orphan.exists())
        self.assertTrue(live.exists())
        self.assertTrue(young.exists())
        self.assertTrue(self.snapshot.exists(), "real snapshot must never be touched")
        self.assertEqual(result["reaped"], [orphan.name])
        self.assertIn(live.name, result["kept_alive"])
        self.assertIn(young.name, result["kept_young"])
        self.assertEqual(result["scanned"], 3)

    def test_does_not_touch_current_pid_even_if_probe_says_dead(self):
        """The current PID is short-circuited to kept_alive regardless of the
        liveness probe — we are, by definition, alive."""
        mine = self._mk_tmp(os.getpid(), age_s=_SNAPSHOT_TMP_MIN_AGE_S + 999)
        result = reap_orphan_busy_snapshots(
            self.snapshot,
            pid_alive=lambda _pid: False,  # force probe to claim everything dead
        )
        self.assertTrue(mine.exists())
        self.assertIn(mine.name, result["kept_alive"])

    def test_empty_dir_is_safe(self):
        result = reap_orphan_busy_snapshots(self.snapshot)
        self.assertEqual(result["reaped_count"], 0)
        self.assertEqual(result["scanned"], 0)
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
