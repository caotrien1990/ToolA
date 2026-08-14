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

if __name__ == "__main__":
    unittest.main()