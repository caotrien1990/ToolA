from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.tools import search as search_module
from orchestrator.server import executor_lanes


def _reset_lane_suite() -> None:
    asyncio.run(executor_lanes.shutdown_executor_lanes(drain_timeout=0))
    executor_lanes._reset_executor_lanes_for_tests()

_FAIR_ENV = {}


def setUpModule() -> None:
    for name in ("ORCH_FAIR_ADMISSION_ENABLED", "ORCH_GLOBAL_CAP_ENABLED"):
        _FAIR_ENV[name] = os.environ.get(name)
        os.environ[name] = "0"
    _reset_lane_suite()


def tearDownModule() -> None:
    _reset_lane_suite()
    for name, value in _FAIR_ENV.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    _reset_lane_suite()


class TestSearchSingleflightConcurrency(unittest.IsolatedAsyncioTestCase):
    """Concurrency contracts: share computation, never share caller cancellation."""

    def _assert_async_coordinator(self) -> None:
        self.assertTrue(
            inspect.iscoroutinefunction(search_module._run_search_singleflight),
            "singleflight waiters must await an async coordinator instead of blocking executor threads",
        )

    async def _wait_event(self, event: threading.Event, timeout: float = 2.0) -> None:
        loop = asyncio.get_running_loop()
        observed = await asyncio.wait_for(
            loop.run_in_executor(None, event.wait, timeout),
            timeout + 0.5,
        )
        self.assertTrue(observed, "timed out waiting for the producer to enter")

    async def _assert_registry_drained(self) -> None:
        for _ in range(100):
            with search_module._SEARCH_SINGLEFLIGHT_LOCK:
                if not search_module._SEARCH_SINGLEFLIGHT:
                    return
            await asyncio.sleep(0.01)
        with search_module._SEARCH_SINGLEFLIGHT_LOCK:
            remaining = list(search_module._SEARCH_SINGLEFLIGHT)
        self.fail(f"singleflight registry leaked keys: {remaining!r}")

    async def test_fifty_waiters_share_one_producer_without_to_thread_followers(self):
        self._assert_async_coordinator()
        key = ("fifty", id(self))
        entered = threading.Event()
        release = threading.Event()
        count_lock = threading.Lock()
        producer_count = 0
        expected = [{"file": "a.py", "line": 1}]

        def producer():
            nonlocal producer_count
            with count_lock:
                producer_count += 1
            entered.set()
            if not release.wait(2.0):
                raise TimeoutError("test did not release producer")
            return expected

        with mock.patch.object(
            search_module.asyncio,
            "to_thread",
            side_effect=AssertionError("followers must not use the default executor"),
        ):
            tasks = [
                asyncio.create_task(search_module._run_search_singleflight(key, producer))
                for _ in range(50)
            ]
            await self._wait_event(entered)
            await asyncio.sleep(0.05)
            self.assertEqual(producer_count, 1)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 3.0)

        self.assertEqual(producer_count, 1)
        self.assertTrue(all(result is expected for result in results))
        await self._assert_registry_drained()

    async def test_cancelled_waiters_do_not_cancel_producer_or_survivors(self):
        self._assert_async_coordinator()
        key = ("cancel", id(self))
        entered = threading.Event()
        release = threading.Event()
        producer_count = 0
        count_lock = threading.Lock()
        expected = [{"match": "survived"}]

        def producer():
            nonlocal producer_count
            with count_lock:
                producer_count += 1
            entered.set()
            if not release.wait(2.0):
                raise TimeoutError("test did not release producer")
            return expected

        tasks = [
            asyncio.create_task(search_module._run_search_singleflight(key, producer))
            for _ in range(50)
        ]
        await self._wait_event(entered)
        await asyncio.sleep(0.05)
        for task in tasks[:20]:
            task.cancel()
        await asyncio.sleep(0)
        release.set()
        outcomes = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            3.0,
        )

        self.assertEqual(producer_count, 1)
        self.assertEqual(sum(isinstance(item, asyncio.CancelledError) for item in outcomes), 20)
        survivors = [item for item in outcomes if not isinstance(item, asyncio.CancelledError)]
        self.assertEqual(len(survivors), 30)
        self.assertTrue(all(item is expected for item in survivors))
        await self._assert_registry_drained()

    async def test_producer_exception_reaches_all_waiters_and_retry_is_clean(self):
        self._assert_async_coordinator()
        key = ("exception", id(self))
        entered = threading.Event()
        release = threading.Event()
        producer_count = 0
        count_lock = threading.Lock()

        def failing_producer():
            nonlocal producer_count
            with count_lock:
                producer_count += 1
            entered.set()
            if not release.wait(2.0):
                raise TimeoutError("test did not release producer")
            raise RuntimeError("singleflight-boom")

        tasks = [
            asyncio.create_task(search_module._run_search_singleflight(key, failing_producer))
            for _ in range(12)
        ]
        await self._wait_event(entered)
        await asyncio.sleep(0.05)
        release.set()
        outcomes = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            3.0,
        )
        self.assertEqual(producer_count, 1)
        self.assertTrue(
            all(isinstance(item, RuntimeError) and str(item) == "singleflight-boom" for item in outcomes)
        )
        await self._assert_registry_drained()

        recovered = await search_module._run_search_singleflight(key, lambda: [{"ok": True}])
        self.assertEqual(recovered, [{"ok": True}])
        await self._assert_registry_drained()

    async def test_shared_concurrent_future_is_safe_across_event_loops(self):
        self._assert_async_coordinator()
        key = ("multi-loop", id(self))
        entered = threading.Event()
        release = threading.Event()
        count_lock = threading.Lock()
        producer_count = 0
        expected = [{"loop_neutral": True}]

        def producer():
            nonlocal producer_count
            with count_lock:
                producer_count += 1
            entered.set()
            if not release.wait(2.0):
                raise TimeoutError("test did not release producer")
            return expected

        def run_caller():
            return asyncio.run(search_module._run_search_singleflight(key, producer))

        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as callers:
            first = loop.run_in_executor(callers, run_caller)
            second = loop.run_in_executor(callers, run_caller)
            await self._wait_event(entered)
            await asyncio.sleep(0.05)
            self.assertEqual(producer_count, 1)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(first, second), 3.0)

        self.assertEqual(producer_count, 1)
        self.assertTrue(all(item is expected for item in results))
        await self._assert_registry_drained()

    async def test_pin_precedes_key_and_splits_overlapping_snapshot_generations(self):
        self._assert_async_coordinator()
        self.assertTrue(hasattr(search_module, "_search_snapshot_token"))
        first_entered = threading.Event()
        release_first = threading.Event()
        read_lock = threading.Lock()
        read_count = 0
        real_read = search_module.search_snapshots.SnapshotPin.read_bytes

        def controlled_read(pin, relative_path):
            nonlocal read_count
            with read_lock:
                read_count += 1
                ordinal = read_count
            if ordinal == 1:
                first_entered.set()
                if not release_first.wait(2.0):
                    raise TimeoutError("test did not release first snapshot")
            return real_read(pin, relative_path)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text("needle = 1\n", encoding="utf-8")
            search_module.search_snapshots.publish_snapshot(
                root,
                include_file=lambda path: path.suffix == ".py",
                skip_dir=lambda name: name.startswith("."),
                index=None,
            )
            real_pin = search_module._pin_or_create_snapshot

            def pin_then_refresh(snapshot_root, expected_id=None, deadline_at=None):
                pin = real_pin(
                    snapshot_root,
                    expected_id=expected_id,
                    deadline_at=deadline_at,
                )
                if pin.snapshot_id == first_snapshot:
                    (root / "sample.py").write_text("needle = 2\n", encoding="utf-8")
                    search_module.search_snapshots.publish_snapshot(
                        root,
                        include_file=lambda path: path.suffix == ".py",
                        skip_dir=lambda name: name.startswith("."),
                        index=None,
                    )
                return pin

            first_snapshot = search_module.search_snapshots.current_snapshot_id(root)
            with mock.patch.object(
                search_module,
                "_pin_or_create_snapshot",
                side_effect=pin_then_refresh,
            ), mock.patch.object(
                search_module.search_snapshots.SnapshotPin,
                "read_bytes",
                controlled_read,
            ):
                first = asyncio.create_task(
                    search_module.search_code(query="needle", path=str(root), mode="text")
                )
                await self._wait_event(first_entered)
                second = asyncio.create_task(
                    search_module.search_code(query="needle", path=str(root), mode="text")
                )
                second_raw = await asyncio.wait_for(second, 1.0)
                release_first.set()
                first_raw = await asyncio.wait_for(first, 2.0)

        self.assertEqual(read_count, 2)
        self.assertEqual(json.loads(first_raw)["count"], 1)
        self.assertEqual(json.loads(second_raw)["count"], 1)
        await self._assert_registry_drained()

    async def test_unique_admission_is_bounded_and_search_lane_is_observable(self):
        lane = executor_lanes.ExecutorLane("search-test", max_workers=1, max_queue=1)
        entered = threading.Event()
        release = threading.Event()

        def producer():
            entered.set()
            release.wait(2.0)
            return {"results": [], "snapshot_id": "bounded"}

        with mock.patch.object(search_module, "get_executor_lane", return_value=lane):
            first = asyncio.create_task(search_module._run_search_singleflight(("one",), producer))
            await self._wait_event(entered)
            second = asyncio.create_task(search_module._run_search_singleflight(("two",), producer))
            await asyncio.sleep(0.02)
            with self.assertRaises(executor_lanes.ExecutorLaneOverloaded):
                await search_module._run_search_singleflight(("three",), producer)
            followers = [
                asyncio.create_task(search_module._run_search_singleflight(("one",), producer))
                for _ in range(20)
            ]
            status = lane.status()
            self.assertEqual(status["submitted"], 2)
            self.assertEqual(status["rejected"], 1)
            self.assertEqual(status["in_flight"], 2)
            release.set()
            await asyncio.gather(first, second, *followers)
        await lane.close()

    async def test_queued_cancel_does_not_acquire_producer_pin(self):
        lane = executor_lanes.ExecutorLane("search-pin-test", max_workers=1, max_queue=1)
        entered = threading.Event()
        release = threading.Event()
        released = 0

        class FakePin:
            snapshot_id = "queued-pin"

            def release(self):
                nonlocal released
                released += 1

        def blocker():
            entered.set()
            release.wait(2.0)

        lane.submit(blocker)
        await self._wait_event(entered)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(search_module, "get_executor_lane", return_value=lane), \
             mock.patch.object(search_module, "_pin_or_create_snapshot", return_value=FakePin()):
            queued = asyncio.create_task(search_module.search_code(
                query="needle", path=tmp, mode="text"
            ))
            await asyncio.sleep(0.02)
            await lane.close(drain_timeout=0)
            outcome = await asyncio.gather(queued, return_exceptions=True)
        release.set()
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual(released, 0)
        await self._assert_registry_drained()

    async def test_caller_timeout_then_producer_and_lane_drain_without_release(self):
        lane = executor_lanes.ExecutorLane("search-deadline-test", max_workers=1, max_queue=1)
        key = ("self-draining-timeout", id(self))
        deadline_at = time.monotonic() + 0.08

        def producer():
            while time.monotonic() < deadline_at:
                time.sleep(0.002)
            raise TimeoutError("producer hard deadline")

        with mock.patch.object(search_module, "get_executor_lane", return_value=lane):
            with self.assertRaises(asyncio.TimeoutError):
                await search_module._run_search_singleflight(
                    key,
                    producer,
                    timeout=0.05,
                )
            for _ in range(100):
                if lane.status()["in_flight"] == 0:
                    break
                await asyncio.sleep(0.01)

        self.assertEqual(lane.status()["in_flight"], 0)
        await self._assert_registry_drained()
        await lane.close()

    async def test_real_filesystem_fifty_callers_cancel_twenty(self):
        self._assert_async_coordinator()
        first_entered = threading.Event()
        release_first = threading.Event()
        walk_lock = threading.Lock()
        walk_count = 0
        real_walk = search_module.os.walk

        def controlled_walk(*args, **kwargs):
            nonlocal walk_count
            with walk_lock:
                walk_count += 1
                ordinal = walk_count
            if ordinal == 1:
                first_entered.set()
                if not release_first.wait(2.0):
                    raise TimeoutError("test did not release real scan")
            return real_walk(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(40):
                (root / f"file_{index:03d}.py").write_text(
                    f"value_{index} = 'shared-needle'\n",
                    encoding="utf-8",
                )
            search_module.search_snapshots.publish_snapshot(
                root,
                include_file=lambda path: path.suffix == ".py",
                skip_dir=lambda name: name.startswith("."),
                index=None,
            )
            real_read = search_module.search_snapshots.SnapshotPin.read_bytes

            def controlled_read(pin, relative_path):
                nonlocal walk_count
                with walk_lock:
                    walk_count += 1
                    ordinal = walk_count
                if ordinal == 1:
                    first_entered.set()
                    if not release_first.wait(2.0):
                        raise TimeoutError("test did not release real scan")
                return real_read(pin, relative_path)

            with mock.patch.object(
                search_module.search_snapshots.SnapshotPin,
                "read_bytes",
                controlled_read,
            ):
                tasks = [
                    asyncio.create_task(
                        search_module.search_code(
                            query="shared-needle",
                            path=str(root),
                            mode="text",
                            max_results=50,
                        )
                    )
                    for _ in range(50)
                ]
                await self._wait_event(first_entered)
                await asyncio.sleep(0.05)
                for task in tasks[:20]:
                    task.cancel()
                await asyncio.sleep(0)
                release_first.set()
                outcomes = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    4.0,
                )

        self.assertEqual(walk_count, 40)
        self.assertEqual(sum(isinstance(item, asyncio.CancelledError) for item in outcomes), 20)
        survivors = [item for item in outcomes if isinstance(item, str)]
        self.assertEqual(len(survivors), 30)
        self.assertTrue(all(item == survivors[0] for item in survivors))
        self.assertEqual(json.loads(survivors[0])["count"], 40)
        await self._assert_registry_drained()


if __name__ == "__main__":
    unittest.main()
