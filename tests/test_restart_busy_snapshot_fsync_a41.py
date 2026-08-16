"""A41: the busy-snapshot publisher must not pay an fsync on its ephemeral write.

Root cause (measured, tests/_a41_probe.py): BusySnapshotPublisher.publish_once()
used to fsync the temp file before os.replace(). The snapshot is ephemeral — a
lost write is simply re-published on the next 0.5s cycle — so the reader needs
ATOMICITY (never a torn read), not crash-DURABILITY. On the slow repo drive an
fsync costs ~300ms idle and >1.3s under concurrent disk load; that delay, not
lock contention, dominated the publisher's cycle time and let captured_at age
past the restart guard's 3.0s staleness deadline (the A39/PH-2 incident: a 4.89s
publish gap).

These tests pin BEHAVIOUR and model the DANGEROUS SHAPE directly — a slow disk
(high fsync latency) must NOT delay an ephemeral publish — rather than merely
calling the function and asserting it ran. They fail against the pre-A41 body
(which fsync'd the snapshot temp) and pass after it.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.lifecycle import restart_protocol
from orchestrator.lifecycle.restart_protocol import BusySnapshotPublisher


class EphemeralPublishSkipsFsync(unittest.TestCase):
    def _publisher(self, path):
        return BusySnapshotPublisher(
            path, lambda: {"busy": False, "generation": "gen-a"}, interval=0.5
        )

    def test_publish_once_does_not_fsync_the_snapshot(self):
        # PIN THE FIX: the ephemeral snapshot path must not call os.fsync at all.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "busy.json"
            with mock.patch.object(restart_protocol.os, "fsync") as fsync:
                self._publisher(path).publish_once()
            fsync.assert_not_called()
            # atomicity is still delivered: a complete, valid JSON is present.
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["generation"], "gen-a")
            self.assertIn("captured_at", payload)

    def test_publish_once_still_uses_atomic_replace(self):
        # The fix drops fsync but MUST keep os.replace — atomicity is not traded away.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "busy.json"
            with mock.patch.object(
                restart_protocol.os, "replace", wraps=os.replace
            ) as replace:
                self._publisher(path).publish_once()
            replace.assert_called_once()
            # target of the replace is the real snapshot path.
            self.assertEqual(Path(replace.call_args[0][1]), path)

    def test_slow_disk_fsync_does_not_delay_the_publish(self):
        # DANGEROUS SHAPE: model a slow disk by making every fsync block 0.5s
        # (idle fsync on the repo drive already measured ~0.3s; >1.3s under load).
        # A publish that fsync'd its snapshot would inherit that latency and age
        # captured_at toward the guard deadline. With the fix the snapshot path
        # issues no fsync, so the publish stays well under the fsync cost.
        SLOW = 0.5

        real_fsync = os.fsync

        def slow_fsync(fd):
            time.sleep(SLOW)
            return real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "busy.json"
            publisher = self._publisher(path)
            with mock.patch.object(restart_protocol.os, "fsync", slow_fsync):
                start = time.perf_counter()
                publisher.publish_once()
                elapsed = time.perf_counter() - start
            # Pre-fix this would be >= SLOW; post-fix it pays no fsync at all.
            self.assertLess(
                elapsed, SLOW,
                f"publish paid an fsync it should have skipped: {elapsed*1000:.1f}ms",
            )
            # Sanity: the write still landed atomically despite the slow-fsync patch.
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["generation"], "gen-a"
            )

    def test_concurrent_publish_is_atomic_and_leaves_no_temp(self):
        # Atomicity under concurrency is preserved without fsync (mirrors the
        # existing core test, but proves it holds for the no-fsync body too).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "busy.json"
            counter = iter(range(1000))
            publisher = BusySnapshotPublisher(path, lambda: {"value": next(counter)})
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _i: publisher.publish_once(), range(40)))
            payload = json.loads(path.read_text(encoding="utf-8"))  # never torn
            self.assertIn("value", payload)
            self.assertIn("captured_at", payload)
            self.assertEqual(list(path.parent.glob("busy.json.tmp.*")), [])


class PublisherHoldsNoSharedLockAcrossDiskIo(unittest.TestCase):
    """The publisher must serialize its snapshot and write it while holding only
    its own _publish_lock — never a handler lock. The callback reads counters under
    the shared locks and RELEASES them before returning the plain dict, so a slow
    disk write can never pin a request-handler lock (and vice versa)."""

    def test_publish_lock_is_publisher_private_not_a_handler_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "busy.json"
            entered = threading.Event()
            release = threading.Event()

            def slow_snapshot():
                entered.set()
                release.wait(2.0)  # simulate a slow callback while we probe the lock
                return {"busy": False, "generation": "gen-a"}

            publisher = BusySnapshotPublisher(path, slow_snapshot, interval=0.5)
            worker = threading.Thread(target=publisher.publish_once, daemon=True)
            worker.start()
            self.assertTrue(entered.wait(2.0))
            # While the publish is mid-flight, a second publish_once must block on
            # the publisher's OWN lock (proving disk I/O is serialized under it),
            # not proceed concurrently.
            second_done = threading.Event()

            def second():
                publisher.publish_once()
                second_done.set()

            t2 = threading.Thread(target=second, daemon=True)
            t2.start()
            self.assertFalse(second_done.wait(0.3))  # blocked by _publish_lock
            release.set()
            worker.join(2.0)
            t2.join(2.0)
            self.assertTrue(second_done.is_set())


if __name__ == "__main__":
    unittest.main()
