import asyncio
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import code_tools


class TestCacheDeltaIsolation(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="cache_delta_isolation_"))
        self.sample_file = self.tempdir / "sample.py"
        self.sample_file.write_text("def sample():\n    return 1\n", encoding="utf-8")
        json.loads(asyncio.run(code_tools.generate_project_map(str(self.tempdir))))

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_cache_delta_does_not_block_event_loop(self):
        original_hash = code_tools.surgical_mapper.get_file_hash
        release = threading.Event()

        def slow_hash(path):
            release.wait(timeout=1.0)
            return original_hash(path)

        async def scenario():
            done = False
            ticks = 0

            async def heartbeat():
                nonlocal ticks
                while not done:
                    ticks += 1
                    await asyncio.sleep(0.01)

            heartbeat_task = asyncio.create_task(heartbeat())
            timer = threading.Timer(0.2, release.set)
            timer.start()
            try:
                result = json.loads(await code_tools.cache_delta(str(self.tempdir)))
            finally:
                done = True
                release.set()
                timer.cancel()
                await heartbeat_task
            return ticks, result

        with mock.patch.object(code_tools.surgical_mapper, "get_file_hash", side_effect=slow_hash):
            ticks, result = asyncio.run(scenario())

        self.assertTrue(result.get("ok"), msg=result)
        self.assertGreaterEqual(ticks, 5, msg="cache_delta starved the asyncio event loop")

    def test_cache_delta_singleflights_duplicate_callers(self):
        original_hash = code_tools.surgical_mapper.get_file_hash
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        def slow_hash(path):
            nonlocal calls
            with calls_lock:
                calls += 1
            release.wait(timeout=1.0)
            return original_hash(path)

        async def scenario():
            timer = threading.Timer(0.2, release.set)
            timer.start()
            try:
                return await asyncio.gather(*(
                    code_tools.cache_delta(str(self.tempdir)) for _ in range(8)
                ))
            finally:
                release.set()
                timer.cancel()

        with mock.patch.object(code_tools.surgical_mapper, "get_file_hash", side_effect=slow_hash):
            results = asyncio.run(scenario())

        self.assertEqual(calls, 1, msg="duplicate callers performed duplicate full-index scans")
        self.assertEqual(len(set(results)), 1)

    def test_cancelled_waiter_does_not_cancel_shared_scan(self):
        original_hash = code_tools.surgical_mapper.get_file_hash
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        def slow_hash(path):
            nonlocal calls
            with calls_lock:
                calls += 1
            entered.set()
            release.wait(timeout=1.0)
            return original_hash(path)

        async def scenario():
            watchdog = threading.Timer(1.0, release.set)
            watchdog.start()
            producer = asyncio.create_task(code_tools.cache_delta(str(self.tempdir)))
            follower = asyncio.create_task(code_tools.cache_delta(str(self.tempdir)))
            try:
                await asyncio.to_thread(entered.wait, 0.5)
                self.assertTrue(entered.is_set(), msg="shared scan did not start")
                follower.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await follower
                self.assertFalse(producer.done(), msg="follower cancellation cancelled producer")
                release.set()
                result = json.loads(await producer)
                await asyncio.sleep(0)
                key = str(self.tempdir.resolve())
                return result, key in code_tools._CACHE_DELTA_FUTURES
            finally:
                release.set()
                watchdog.cancel()

        with mock.patch.object(code_tools.surgical_mapper, "get_file_hash", side_effect=slow_hash):
            result, future_leaked = asyncio.run(scenario())

        self.assertTrue(result.get("ok"), msg=result)
        self.assertEqual(calls, 1)
        self.assertFalse(future_leaked)

    # ---- P10: one drift question, one matcher -------------------------------
    #
    # Regression root: cache_delta always re-hashed every indexed file
    # ("a restore/copy can preserve both mtime and size"), while index_delta
    # answered the same question stat-first. The two drifted apart and the
    # always-hash half pinned large repos at O(read-everything) PER PROBE
    # (measured: 14.6s in-process on 49. CUA, re-paid on every 0-hit search).
    # The fix keeps the copy/restore guarantee honestly via a stat signature
    # (st_size, st_mtime_ns, st_ino) plus a per-process memo: a file whose
    # signature is unchanged since its last hash is never re-read; a
    # copy/replace yields a new inode on NTFS, so it is still re-hashed.

    def test_second_probe_hashes_zero_files(self):
        """Regression guard: a repeated drift probe must be O(stat), not O(read)."""
        real_hash = code_tools.surgical_mapper.get_file_hash
        hashes = 0
        hashes_lock = threading.Lock()

        def counting_hash(path):
            nonlocal hashes
            with hashes_lock:
                hashes += 1
            return real_hash(path)

        with mock.patch.object(
            code_tools.surgical_mapper, "get_file_hash", side_effect=counting_hash
        ):
            first = json.loads(asyncio.run(code_tools.cache_delta(str(self.tempdir))))
            self.assertTrue(first.get("ok"), msg=first)
            with hashes_lock:
                first_hashes = hashes
            self.assertGreaterEqual(
                first_hashes, 1, "a cold probe must measure, not guess"
            )
            with code_tools._CACHE_DELTA_FUTURES_LOCK:
                code_tools._CACHE_DELTA_FUTURES.clear()
            with hashes_lock:
                hashes = 0
            second = json.loads(asyncio.run(code_tools.cache_delta(str(self.tempdir))))
            self.assertTrue(second.get("ok"), msg=second)
            self.assertEqual(second.get("total_changed"), 0, msg=second)
            with hashes_lock:
                second_hashes = hashes

        self.assertEqual(
            second_hashes, 0,
            "second cache_delta probe re-hashed %d file(s); an unchanged file "
            "must never be re-read" % second_hashes,
        )

    def test_cache_status_rides_the_same_memo(self):
        """cache_status asks the same drift question; it must not re-read either."""
        real_hash = code_tools.surgical_mapper.get_file_hash
        hashes = 0
        hashes_lock = threading.Lock()

        def counting_hash(path):
            nonlocal hashes
            with hashes_lock:
                hashes += 1
            return real_hash(path)

        with mock.patch.object(
            code_tools.surgical_mapper, "get_file_hash", side_effect=counting_hash
        ):
            json.loads(asyncio.run(code_tools.cache_delta(str(self.tempdir))))
            with code_tools._CACHE_DELTA_FUTURES_LOCK:
                code_tools._CACHE_DELTA_FUTURES.clear()
            with hashes_lock:
                hashes = 0
            status = json.loads(asyncio.run(code_tools.cache_status(str(self.tempdir))))
            with hashes_lock:
                status_hashes = hashes

        self.assertTrue(status.get("ok"), msg=status)
        self.assertEqual(
            status_hashes, 0,
            "cache_status re-hashed %d file(s) after cache_delta had already "
            "measured them" % status_hashes,
        )

    def test_index_delta_shares_the_memo(self):
        """index_delta answers the same question through the same matcher: a file
        already hashed by cache_delta must not be re-read by index_delta."""
        from orchestrator.fileops import common as fo_common

        real_hash = code_tools.surgical_mapper.get_file_hash
        hashes = 0
        hashes_lock = threading.Lock()

        def counting_hash(path):
            nonlocal hashes
            with hashes_lock:
                hashes += 1
            return real_hash(path)

        with mock.patch.object(
            code_tools.surgical_mapper, "get_file_hash", side_effect=counting_hash
        ):
            json.loads(asyncio.run(code_tools.cache_delta(str(self.tempdir))))
            with hashes_lock:
                hashes = 0
            delta = fo_common.index_delta(str(self.tempdir))
            with hashes_lock:
                index_hashes = hashes

        self.assertIsNotNone(delta)
        self.assertEqual(delta.get("modified"), [], msg=delta)
        self.assertEqual(
            index_hashes, 0,
            "index_delta re-hashed %d file(s) that cache_delta had already "
            "measured" % index_hashes,
        )

    def test_plain_edit_is_detected_via_signature(self):
        """A normal in-place edit (mtime bumps) is detected after the fix."""
        self.sample_file.write_text("def sample():\n    return 2\n", encoding="utf-8")
        delta = json.loads(asyncio.run(code_tools.cache_delta(str(self.tempdir))))
        self.assertIn("sample.py", delta["modified"], msg=delta)

    def test_copy_replace_is_detected_via_new_inode(self):
        """The guarantee the always-hash comment existed for: a copy/restore that
        preserves mtime+size still gets a NEW file index on NTFS, so the stat
        signature catches it and the file is re-hashed."""
        import os as _os
        import shutil as _shutil

        target = self.sample_file
        original_stat = target.stat()
        replacement = self.tempdir / "replacement.py"
        replacement.write_text("def sample():\n    return 3\n", encoding="utf-8")
        # copy2 preserves mtime; then force the exact original mtime too, so the
        # ONLY surviving signal is the inode (size is unchanged by design: both
        # bodies are 'def sample():\n    return N\n' -- equal length).
        _shutil.copy2(replacement, target)
        _os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertEqual(target.stat().st_size, original_stat.st_size)

        delta = json.loads(asyncio.run(code_tools.cache_delta(str(self.tempdir))))
        self.assertIn(
            "sample.py", delta["modified"],
            "a copy-replace with restored mtime+size must be detected via st_ino: %s"
            % delta,
        )

    def test_same_size_same_mtime_in_place_rewrite_is_the_documented_boundary(self):
        """The exact undetectable case, stated so nobody rediscovers it as a bug:
        an IN-PLACE rewrite (same inode) whose size AND mtime_ns are both
        restored afterwards leaves no stat signal at all, so a warm memo does
        not re-read the file. A cold process still hashes every file once, so
        the edit is caught by the first probe after a daemon restart; only a
        warm-memo probe misses it. copy-replace (new inode) stays detectable --
        see the test above -- which is what the original always-hash comment
        actually promised ('a restore/copy can preserve both mtime and size')."""
        import os as _os

        target = self.sample_file
        original_stat = target.stat()
        # Prime the memo so the probe is warm.
        first = json.loads(asyncio.run(code_tools.cache_delta(str(self.tempdir))))
        self.assertEqual(first.get("total_changed"), 0, msg=first)
        with code_tools._CACHE_DELTA_FUTURES_LOCK:
            code_tools._CACHE_DELTA_FUTURES.clear()

        # Same-size in-place rewrite, then restore the exact original mtime.
        target.write_text("def sample():\n    return 9\n", encoding="utf-8")
        _os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        now = target.stat()
        self.assertEqual(now.st_size, original_stat.st_size)
        self.assertEqual(now.st_mtime_ns, original_stat.st_mtime_ns)

        delta = json.loads(asyncio.run(code_tools.cache_delta(str(self.tempdir))))
        self.assertNotIn(
            "sample.py", delta["modified"],
            "documented boundary violated: an in-place rewrite with restored "
            "size+mtime_ns leaves no stat signal and must read as unchanged: %s"
            % delta,
        )
if __name__ == "__main__":
    unittest.main()