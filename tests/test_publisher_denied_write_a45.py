"""A45: a publisher that is alive but landing nothing must not vouch for its own
frozen snapshot.

Measured 2026-08-16 (campaign-1608) on the real publisher THREAD, D drive:

    readers  max age observed   deadline   .alive throughout
    0        0.54s              3.0s       True
    2        4.32s              3.0s       True     <-- past the deadline
    5        7.90s              3.0s       True     <-- 2.6x the deadline

On Windows a concurrent reader holding the snapshot open makes ``os.replace``
raise ``PermissionError`` (WinError 5) -- 93.7% of attempts under tight-loop
readers. ``run()`` swallowed the exception with a bare ``pass``: no log, no
counter, no trace. So ``captured_at`` froze in total silence while the publisher
thread stayed ``is_alive() == True``.

That combination is exactly the state A39/PH-5 reads as SAFE. Its two-condition
test asks "is the thread alive?" to separate "blocked but will catch up"
(suppress the stale block) from "dead" (keep it). A denied-write publisher is in
neither bucket: alive, looping, and publishing nothing. The guard suppressed
BUSY_SNAPSHOT_STALE and judged a restart on a reading up to 7.9s old -- a
fail-OPEN on the one signal the restart guard exists to trust.

Two independent fixes, both pinned here:
  1. publish_once retries os.replace (the ladder storage/fs.py already uses for
     this same error) and RECORDS the outcome instead of vanishing.
  2. _snapshot_publisher_alive gains a third condition: the last cycle must have
     LANDED. Alive-but-not-landing no longer vouches for the reading.

Fix 1 alone is not enough and this suite says so: a long enough denial streak
still outlives the ~175ms ladder. Fix 2 is what makes the failure fail CLOSED.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from orchestrator.lifecycle.restart_control_service import (  # noqa: E402
    _snapshot_publisher_alive,
)
from orchestrator.lifecycle.restart_protocol import BusySnapshotPublisher  # noqa: E402

GEN = "gen-a45-0001"


def _livez(alive: bool = True, generation: str = GEN, publish: dict | None = None) -> dict:
    out: dict = {"restart_busy_snapshot_alive": alive, "generation": generation}
    if publish is not None:
        out["restart_busy_snapshot_publish"] = publish
    return out


def _ok_publish() -> dict:
    return {"alive": True, "publish_ok": 42, "publish_denied": 0,
            "last_publish_ok": time.time(), "last_publish_error": ""}


def _denied_publish() -> dict:
    return {"alive": True, "publish_ok": 7, "publish_denied": 31,
            "last_publish_ok": time.time() - 9.0,
            "last_publish_error": "PermissionError: [WinError 5] Access is denied"}


class DeniedWriteIsRecordedTests(unittest.TestCase):
    """Fix 1: the denial must leave a trace instead of vanishing into `pass`."""

    def _publisher(self, path: Path) -> BusySnapshotPublisher:
        return BusySnapshotPublisher(path, lambda: {"busy": False}, interval=0.5)

    def test_a_successful_publish_stamps_last_publish_ok(self):
        with tempfile.TemporaryDirectory() as d:
            pub = self._publisher(Path(d) / "snap.json")
            before = time.time()
            pub.publish_once()
            health = pub.publish_health()

            self.assertEqual(health["publish_ok"], 1)
            self.assertEqual(health["publish_denied"], 0)
            self.assertEqual(health["last_publish_error"], "")
            self.assertGreaterEqual(health["last_publish_ok"], before)

    def test_a_denied_replace_is_counted_and_named_not_swallowed(self):
        with tempfile.TemporaryDirectory() as d:
            pub = self._publisher(Path(d) / "snap.json")
            with mock.patch("os.replace", side_effect=PermissionError(5, "Access is denied")):
                with self.assertRaises(PermissionError):
                    pub.publish_once()
            health = pub.publish_health()

            self.assertEqual(health["publish_denied"], 1)
            self.assertEqual(health["publish_ok"], 0)
            self.assertIn("PermissionError", health["last_publish_error"])

    def test_a_denied_publish_never_advances_last_publish_ok(self):
        """The whole point: the stamp must mean "a write LANDED", nothing weaker."""
        with tempfile.TemporaryDirectory() as d:
            pub = self._publisher(Path(d) / "snap.json")
            pub.publish_once()
            landed = pub.publish_health()["last_publish_ok"]
            time.sleep(0.01)
            with mock.patch("os.replace", side_effect=PermissionError(5, "denied")):
                with self.assertRaises(PermissionError):
                    pub.publish_once()

            self.assertEqual(pub.publish_health()["last_publish_ok"], landed,
                             "a failed cycle advanced the success stamp")

    def test_replace_is_retried_not_abandoned_on_the_first_denial(self):
        """A single transient denial must not cost a whole cycle."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snap.json"
            pub = self._publisher(path)
            real = os.replace
            calls = {"n": 0}

            def flaky(src, dst):
                calls["n"] += 1
                if calls["n"] < 3:
                    raise PermissionError(5, "Access is denied")
                return real(src, dst)

            with mock.patch("os.replace", side_effect=flaky):
                pub.publish_once()

            self.assertGreaterEqual(calls["n"], 3, "os.replace was not retried")
            self.assertTrue(path.exists())
            self.assertEqual(pub.publish_health()["publish_ok"], 1)
            self.assertEqual(pub.publish_health()["last_publish_error"], "")

    def test_the_retry_ladder_is_bounded(self):
        """A45 must not trade a frozen snapshot for a publisher stuck retrying."""
        with tempfile.TemporaryDirectory() as d:
            pub = self._publisher(Path(d) / "snap.json")
            calls = {"n": 0}

            def always_denied(src, dst):
                calls["n"] += 1
                raise PermissionError(5, "Access is denied")

            started = time.time()
            with mock.patch("os.replace", side_effect=always_denied):
                with self.assertRaises(PermissionError):
                    pub.publish_once()
            elapsed = time.time() - started

            self.assertEqual(calls["n"], 4, "attempt count drifted from the ladder")
            self.assertLess(elapsed, 0.5,
                            "the ladder outlasts the 0.5s publish interval")

    def test_no_temp_file_is_left_behind_when_every_attempt_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snap.json"
            pub = self._publisher(path)
            with mock.patch("os.replace", side_effect=PermissionError(5, "denied")):
                with self.assertRaises(PermissionError):
                    pub.publish_once()

            self.assertEqual([p.name for p in Path(d).iterdir()], [],
                             "a .tmp orphan survived the failed publish")

    def test_the_thread_survives_a_denied_cycle(self):
        """Removing the SILENCE must not remove the SURVIVAL: a dead publisher
        freezes captured_at forever, which is strictly worse than a skipped
        cycle. `run()` still swallows -- deliberately."""
        with tempfile.TemporaryDirectory() as d:
            pub = self._publisher(Path(d) / "snap.json")
            pub.interval = 0.05
            # A denied cycle costs the full retry ladder (~175ms) plus the
            # interval, so ~0.225s each: sleep long enough for several cycles or
            # this asserts on a single one and proves nothing about LOOPING.
            with mock.patch("os.replace", side_effect=PermissionError(5, "denied")):
                pub.start()
                time.sleep(1.0)
                still_alive = pub.alive
                denied = pub.publish_health()["publish_denied"]
            pub.stop()

            self.assertTrue(still_alive, "the publisher thread died on a denial")
            self.assertGreater(denied, 1,
                               "the thread stopped retrying after the first denial")


class AliveButNotLandingMustNotVouchTests(unittest.TestCase):
    """Fix 2: the guard's liveness suppression gains its missing third state."""

    def test_a_healthy_publisher_still_suppresses_the_stale_block(self):
        """The A39/PH-2 win must survive: blocked-but-alive stays suppressible."""
        self.assertTrue(_snapshot_publisher_alive(
            {"captured_at": time.time()}, GEN, _livez(publish=_ok_publish())))

    def test_alive_but_landing_nothing_does_not_vouch(self):
        """The A45 defect, stated directly."""
        self.assertFalse(
            _snapshot_publisher_alive(
                {"captured_at": time.time() - 7.9}, GEN, _livez(publish=_denied_publish())),
            "a publisher whose writes are denied vouched for its own frozen snapshot")

    def test_a_dead_thread_still_does_not_vouch(self):
        """Pre-A45 behaviour (PH-5) unchanged."""
        self.assertFalse(_snapshot_publisher_alive(
            {"captured_at": time.time()}, GEN, _livez(alive=False, publish=_ok_publish())))

    def test_a_foreign_generation_still_does_not_vouch(self):
        """Pre-A45 behaviour (PH-6) unchanged -- and checked BEFORE the new
        condition, so a healthy publish block cannot smuggle a wrong generation
        past the gate."""
        self.assertFalse(_snapshot_publisher_alive(
            {"captured_at": time.time()}, GEN,
            _livez(generation="some-other-gen", publish=_ok_publish())))

    def test_an_older_generation_without_the_publish_block_is_not_newly_blocked(self):
        """Fail-CLOSED regressions are a real cost, not a free default.

        A /livez payload from a generation that predates A45 has no publish block.
        Treating that absence as "not landing" would newly block restarts on a
        payload SHAPE -- a signal this function never required. Absence falls
        through to the pre-A45 answer."""
        self.assertTrue(_snapshot_publisher_alive(
            {"captured_at": time.time()}, GEN, _livez()))
        self.assertTrue(_snapshot_publisher_alive(
            {"captured_at": time.time()}, GEN, _livez(publish={})))

    def test_a_denial_history_with_a_clean_last_cycle_vouches_again(self):
        """Recovery must be immediate: the decision is about the CURRENT state, not
        a permanent black mark. A publisher that hit denials and then landed a write
        is healthy -- counting historical denials against it would make a transient
        reader burst wedge restarts long after it ended (the L-x trap)."""
        recovered = {"alive": True, "publish_ok": 8, "publish_denied": 31,
                     "last_publish_ok": time.time(), "last_publish_error": ""}
        self.assertTrue(_snapshot_publisher_alive(
            {"captured_at": time.time()}, GEN, _livez(publish=recovered)))

    def test_a_missing_snapshot_still_does_not_vouch(self):
        self.assertFalse(_snapshot_publisher_alive(None, GEN, _livez(publish=_ok_publish())))

    def test_a_missing_livez_still_does_not_vouch(self):
        self.assertFalse(_snapshot_publisher_alive({"captured_at": time.time()}, GEN, None))


if __name__ == "__main__":
    unittest.main()
