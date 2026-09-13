from __future__ import annotations

import asyncio
import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import search_regex_worker
from orchestrator.tools import search as search_module
from orchestrator.server import executor_lanes


def _reset_lane_suite() -> None:
    asyncio.run(executor_lanes.shutdown_executor_lanes(drain_timeout=0))
    executor_lanes._reset_executor_lanes_for_tests()


def setUpModule() -> None:
    _reset_lane_suite()


def tearDownModule() -> None:
    _reset_lane_suite()


class TestSearchRegexBudget(unittest.IsolatedAsyncioTestCase):
    def _helper_pids(self) -> set[int]:
        return {child.pid for child in multiprocessing.active_children() if child.pid is not None and child.name.startswith("search-regex-")}

    async def _wait_helpers_reaped(self, baseline: set[int], timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._helper_pids() <= baseline:
                return
            await asyncio.sleep(0.02)
        self.fail(f"regex helpers leaked: {self._helper_pids() - baseline}")

    def test_catastrophic_regex_times_out_and_reaps_helper(self) -> None:
        baseline = self._helper_pids()
        started = time.monotonic()
        with self.assertRaises(search_regex_worker.RegexSearchTimeout) as caught:
            search_regex_worker.search_records(r"^(a+)+$", 0, [("evil.txt", "evil.txt", ("a" * 100_000 + "!\n").encode())], max_results=10, context_lines=0, deadline_seconds=0.20)
        self.assertEqual(caught.exception.error_name, "REGEX_SEARCH_TIMEOUT")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(self._helper_pids(), baseline)

    async def test_search_code_returns_stable_timeout_and_event_loop_stays_responsive(self) -> None:
        baseline = self._helper_pids()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evil.txt").write_text("a" * 100_000 + "!\n", encoding="utf-8")
            attack = asyncio.create_task(search_module.search_code(query=r"^(a+)+$", path=str(root), mode="regex", force=True, regex_deadline_seconds=0.20))
            latencies = []
            for _ in range(12):
                tick = time.perf_counter()
                await asyncio.sleep(0)
                latencies.append(time.perf_counter() - tick)
                await asyncio.sleep(0.01)
            payload = json.loads(await asyncio.wait_for(attack, 2.0))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_name"], "REGEX_SEARCH_TIMEOUT")
        self.assertLess(max(latencies), 0.25)
        await self._wait_helpers_reaped(baseline)

    async def test_normal_regex_preserves_results_context_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text("before\nvalue_42 = True\nafter\n", encoding="utf-8")
            payload = json.loads(await search_module.search_code(query=r"value_\d+", path=str(root), mode="regex", max_context_lines=1))
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["line"], 2)
        self.assertEqual(payload["results"][0]["match"], "value_42 = True")
        self.assertEqual(payload["results"][0]["context_before"], "before")
        self.assertEqual(payload["results"][0]["context_after"], "after")
        self.assertTrue(payload["snapshot_id"])

    async def test_parallel_cancellation_does_not_leak_helpers(self) -> None:
        baseline = self._helper_pids()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evil.txt").write_text("a" * 100_000 + "!\n", encoding="utf-8")
            search_module.search_snapshots.publish_snapshot(
                root,
                include_file=lambda path: path.suffix == ".txt",
                skip_dir=lambda name: name.startswith("."),
                index=None,
            )
            tasks = [asyncio.create_task(search_module.search_code(query=r"^(a+)+$", path=str(root), mode="regex", force=True, regex_deadline_seconds=0.50)) for _ in range(16)]
            await asyncio.sleep(0.05)
            for task in tasks[:8]:
                task.cancel()
            outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3.0)
        self.assertEqual(sum(isinstance(item, asyncio.CancelledError) for item in outcomes), 8)
        survivors = [json.loads(item) for item in outcomes if isinstance(item, str)]
        self.assertEqual(len(survivors), 8)
        self.assertTrue(all(item.get("error_name") == "REGEX_SEARCH_TIMEOUT" for item in survivors))
        await self._wait_helpers_reaped(baseline)

    def test_pattern_and_input_budgets_fail_loud(self) -> None:
        with self.assertRaises(search_regex_worker.RegexSearchBudgetExceeded) as pattern_error:
            search_regex_worker.search_records("x" * 5000, 0, [], max_results=1, context_lines=0)
        self.assertEqual(pattern_error.exception.error_name, "REGEX_PATTERN_BUDGET_EXCEEDED")
        with self.assertRaises(search_regex_worker.RegexSearchBudgetExceeded) as input_error:
            search_regex_worker.search_records("x", 0, [("x.txt", "x.txt", b"x" * (search_regex_worker.MAX_INPUT_BYTES + 1))], max_results=1, context_lines=0)
        self.assertEqual(input_error.exception.error_name, "REGEX_INPUT_BUDGET_EXCEEDED")

    def test_record_budgets_stream_and_reject_oversize_line_before_spawn(self) -> None:
        from unittest import mock

        seen = []

        def records():
            for index in range(4):
                seen.append(index)
                yield (f"{index}.txt", f"{index}.txt", b"x")

        with mock.patch.object(search_regex_worker, "MAX_RECORDS", 2):
            with self.assertRaises(search_regex_worker.RegexSearchBudgetExceeded) as count_error:
                search_regex_worker.search_records("x", 0, records(), max_results=1, context_lines=0)
        self.assertEqual(count_error.exception.error_name, "REGEX_INPUT_BUDGET_EXCEEDED")
        self.assertEqual(seen, [0, 1, 2])

        with mock.patch.object(search_regex_worker, "MAX_LINE_BYTES", 16, create=True):
            with mock.patch.object(search_regex_worker.multiprocessing, "get_context", side_effect=AssertionError("worker spawned")):
                with self.assertRaises(search_regex_worker.RegexSearchBudgetExceeded) as line_error:
                    search_regex_worker.search_records("x", 0, [("long.txt", "long.txt", b"x" * 17)], max_results=1, context_lines=0)
        self.assertEqual(line_error.exception.error_name, "REGEX_LINE_BUDGET_EXCEEDED")

    def test_deadline_covers_record_materialization(self) -> None:
        seen = []

        def slow_records():
            for index in range(20):
                time.sleep(0.01)
                seen.append(index)
                yield (f"{index}.txt", f"{index}.txt", b"safe")

        started = time.monotonic()
        with self.assertRaises(search_regex_worker.RegexSearchTimeout):
            search_regex_worker.search_records("never", 0, slow_records(), max_results=1, context_lines=0, deadline_seconds=0.05)
        self.assertLess(len(seen), 20)
        self.assertLess(time.monotonic() - started, 0.18)

    def test_regex_deadline_is_explicit_for_http_schema(self) -> None:
        import inspect
        import code_tools

        parameter = inspect.signature(search_module.search_code).parameters["regex_deadline_seconds"]
        self.assertEqual(parameter.kind, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        self.assertIsNone(parameter.default)
        schema = next(item for item in code_tools.TOOL_SCHEMAS if item["function"]["name"] == "search_code")
        deadline_schema = schema["function"]["parameters"]["properties"]["regex_deadline_seconds"]
        self.assertEqual(deadline_schema["default"], search_regex_worker.DEFAULT_DEADLINE_SECONDS)
        self.assertEqual(deadline_schema["minimum"], search_regex_worker.MIN_DEADLINE_SECONDS)
        self.assertEqual(deadline_schema["maximum"], search_regex_worker.MAX_DEADLINE_SECONDS)
    async def test_user_regex_is_never_compiled_on_host(self) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.txt").write_text("safe\n", encoding="utf-8")
            with mock.patch.object(search_module.re, "compile", side_effect=AssertionError("host regex compile")):
                payload = json.loads(await search_module.search_code(query="[", path=str(root), mode="regex"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_name"], "REGEX_INVALID")

    async def test_singleflight_key_separates_mode_and_effective_regex_deadline(self) -> None:
        from unittest import mock

        keys = []

        async def capture(key, producer, **kwargs):
            keys.append(key)
            kwargs["owner_transfer"]()
            kwargs["owner_done"]()
            return {"results": [], "snapshot_id": f"snapshot-{len(keys)}"}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.txt").write_text("needle\n", encoding="utf-8")
            with mock.patch.object(search_module, "_run_search_singleflight", side_effect=capture):
                await search_module.search_code(query="needle", path=str(root), mode="text")
                await search_module.search_code(query="needle", path=str(root), mode="regex", regex_deadline_seconds=0.20)
                await search_module.search_code(query="needle", path=str(root), mode="regex", regex_deadline_seconds=0.40)
        self.assertNotEqual(keys[0], keys[1])
        self.assertNotEqual(keys[1], keys[2])

    async def test_singleflight_key_uses_normalized_regex_deadline(self) -> None:
        from unittest import mock

        keys = []

        async def capture(key, producer, **kwargs):
            keys.append(key)
            kwargs["owner_transfer"]()
            kwargs["owner_done"]()
            return {"results": [], "snapshot_id": f"snapshot-{len(keys)}"}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.txt").write_text("needle\n", encoding="utf-8")
            with mock.patch.object(search_module, "_run_search_singleflight", side_effect=capture):
                await search_module.search_code(query="needle", path=str(root), mode="regex", regex_deadline_seconds=11.0)
                await search_module.search_code(query="needle", path=str(root), mode="regex", regex_deadline_seconds=10.0)
        self.assertEqual(keys[0][:6] + keys[0][7:], keys[1][:6] + keys[1][7:])

    async def test_absolute_deadline_includes_singleflight_wait(self) -> None:
        observed_timeout = None

        async def delayed_singleflight(key, producer, **kwargs):
            nonlocal observed_timeout
            observed_timeout = kwargs["timeout"]
            await asyncio.sleep(observed_timeout)
            raise asyncio.TimeoutError

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.txt").write_text("needle\n", encoding="utf-8")
            search_module.search_snapshots.publish_snapshot(
                root,
                include_file=lambda path: path.suffix == ".txt",
                skip_dir=lambda name: name.startswith("."),
                index=None,
            )
            with mock.patch.object(search_module, "_run_search_singleflight", side_effect=delayed_singleflight):
                started = time.monotonic()
                payload = json.loads(await search_module.search_code(
                    query="needle",
                    path=str(root),
                    mode="regex",
                    regex_deadline_seconds=0.05,
                ))
        self.assertEqual(payload["error_name"], "REGEX_SEARCH_TIMEOUT")
        self.assertIsNotNone(observed_timeout)
        # Float arithmetic on the absolute deadline can overshoot the requested
        # budget by a few ULPs (observed 0.05000000000291038 vs 0.05), so compare
        # with a tolerance instead of an exact upper bound.
        self.assertLessEqual(observed_timeout, 0.05 + 1e-6)
        self.assertLess(time.monotonic() - started, 0.20)

    async def test_within_bucket_regex_follower_keeps_own_absolute_budget(self) -> None:
        producer_count = 0

        def producer():
            nonlocal producer_count
            producer_count += 1
            time.sleep(0.10)
            return {"results": [], "snapshot_id": "one-pin"}

        key = ("regex", "same-request", 0.10)
        owner = asyncio.create_task(
            search_module._run_search_singleflight(key, producer, timeout=0.06)
        )
        await asyncio.sleep(0.04)
        follower = asyncio.create_task(
            search_module._run_search_singleflight(key, producer, timeout=0.12)
        )

        with self.assertRaises(asyncio.TimeoutError):
            await owner
        result = await follower
        self.assertEqual(result["snapshot_id"], "one-pin")
        self.assertEqual(producer_count, 1)

    async def test_oversize_pattern_fails_before_snapshot_pin(self) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(search_module, "_pin_or_create_snapshot") as pin:
                payload = json.loads(await search_module.search_code(
                    query="x" * (search_regex_worker.MAX_PATTERN_CHARS + 1),
                    path=str(root),
                    mode="regex",
                ))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_name"], "REGEX_PATTERN_BUDGET_EXCEEDED")
        pin.assert_not_called()

    async def test_slow_snapshot_pin_respects_wall_deadline_before_helper_start(self) -> None:
        helper_started = False
        release_pin = threading.Event()

        def slow_pin(*args, **kwargs):
            release_pin.wait(5.0)
            raise AssertionError("slow pin completed during bounded response")

        def helper(*args, **kwargs):
            nonlocal helper_started
            helper_started = True
            raise AssertionError("regex helper started before snapshot acquisition")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.txt").write_text("needle\n", encoding="utf-8")
            search_module.search_snapshots.publish_snapshot(
                root, include_file=lambda path: True,
                skip_dir=lambda name: name.startswith("."), index=None,
            )
            started = time.monotonic()
            with mock.patch.object(search_module, "_pin_or_create_snapshot", side_effect=slow_pin), \
                 mock.patch.object(search_regex_worker, "preflight_pattern", side_effect=helper):
                payload = json.loads(await search_module.search_code(
                    query="needle", path=str(root), mode="regex",
                    regex_deadline_seconds=0.50,
                ))
            elapsed = time.monotonic() - started

        self.assertEqual(payload["error_name"], "REGEX_SEARCH_TIMEOUT")
        self.assertLess(elapsed, 0.80)
        self.assertFalse(helper_started)
        release_pin.set()
        for _ in range(100):
            with search_module._SEARCH_SINGLEFLIGHT_LOCK:
                if not search_module._SEARCH_SINGLEFLIGHT:
                    break
            await asyncio.sleep(0.01)

    async def test_held_snapshot_state_lock_times_out_drains_and_starts_no_helper(self) -> None:
        baseline = self._helper_pids()
        helper_started = False
        lock_held = threading.Event()
        release_lock = threading.Event()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.txt").write_text("needle\n", encoding="utf-8")
            search_module.search_snapshots.publish_snapshot(
                root, include_file=lambda path: True,
                skip_dir=lambda name: name.startswith("."), index=None,
            )
            store = search_module.search_snapshots.snapshot_store_path(root)
            state_lock = search_module.search_snapshots._state_thread_lock(store)

            def hold_lock():
                with state_lock:
                    lock_held.set()
                    release_lock.wait(3.0)

            holder = threading.Thread(target=hold_lock, daemon=True)
            holder.start()
            self.assertTrue(lock_held.wait(1.0))

            def helper(*args, **kwargs):
                nonlocal helper_started
                helper_started = True
                raise AssertionError("regex helper started while state lock was held")

            started = time.monotonic()
            try:
                with mock.patch.object(search_regex_worker, "preflight_pattern", side_effect=helper):
                    payload = json.loads(await search_module.search_code(
                        query="needle", path=str(root), mode="regex",
                        regex_deadline_seconds=0.20,
                    ))
                elapsed = time.monotonic() - started
                for _ in range(100):
                    lane = executor_lanes.get_executor_lane("search")
                    with search_module._SEARCH_SINGLEFLIGHT_LOCK:
                        drained = not search_module._SEARCH_SINGLEFLIGHT
                    if drained and lane.status()["in_flight"] == 0:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(payload["error_name"], "REGEX_SEARCH_TIMEOUT")
                self.assertLess(elapsed, 0.80)
                self.assertFalse(helper_started)
                self.assertTrue(drained)
                self.assertEqual(lane.status()["in_flight"], 0)
                self.assertEqual(self._helper_pids(), baseline)
            finally:
                release_lock.set()
                holder.join(1.0)

    async def test_cold_snapshot_slow_capture_aborts_cleanly_and_drains(self) -> None:
        baseline = self._helper_pids()
        helper_started = False
        real_read = search_module.search_snapshots._read_file_bytes

        def slow_read(path):
            time.sleep(0.15)
            return real_read(path)

        def helper(*args, **kwargs):
            nonlocal helper_started
            helper_started = True
            raise AssertionError("regex helper started after cold snapshot deadline")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "large.txt").write_bytes(b"needle\n" * 100_000)
            store = search_module.search_snapshots.snapshot_store_path(root)
            started = time.monotonic()
            with mock.patch.object(
                search_module.search_snapshots,
                "_read_file_bytes",
                side_effect=slow_read,
            ), mock.patch.object(
                search_regex_worker,
                "preflight_pattern",
                side_effect=helper,
            ):
                payload = json.loads(await search_module.search_code(
                    query="needle",
                    path=str(root),
                    mode="regex",
                    regex_deadline_seconds=0.10,
                ))
            elapsed = time.monotonic() - started

            lane = executor_lanes.get_executor_lane("search")
            for _ in range(100):
                with search_module._SEARCH_SINGLEFLIGHT_LOCK:
                    registry_empty = not search_module._SEARCH_SINGLEFLIGHT
                if registry_empty and lane.status()["in_flight"] == 0:
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(payload["error_name"], "REGEX_SEARCH_TIMEOUT")
            self.assertLess(elapsed, 0.40)
            self.assertFalse(helper_started)
            self.assertTrue(registry_empty)
            self.assertEqual(lane.status()["in_flight"], 0)
            self.assertEqual(self._helper_pids(), baseline)
            self.assertFalse((store / "CURRENT").exists())
            self.assertEqual(list((store / "staging").iterdir()), [])
            self.assertEqual(list((store / "generations").iterdir()), [])

    async def test_cold_publish_orphan_validation_respects_deadline_and_preserves_store(self) -> None:
        baseline = self._helper_pids()
        real_read_bytes = Path.read_bytes

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(40):
                (root / f"orphan_{index:03d}.txt").write_text(
                    f"needle {index}\n",
                    encoding="utf-8",
                )
            orphan_id = search_module.search_snapshots.publish_snapshot(
                root,
                include_file=lambda path: path.suffix == ".txt",
                skip_dir=lambda name: name.startswith("."),
                index=None,
            )
            store = search_module.search_snapshots.snapshot_store_path(root)
            current = store / "CURRENT"
            current.unlink()
            generation = store / "generations" / orphan_id
            manifest_before = (generation / "MANIFEST.json").read_bytes()
            blob_reads = 0

            def slow_blob_read(path):
                nonlocal blob_reads
                if "blobs" in Path(path).parts:
                    blob_reads += 1
                    time.sleep(0.02)
                return real_read_bytes(path)

            started = time.monotonic()
            with mock.patch.object(Path, "read_bytes", slow_blob_read):
                payload = json.loads(await search_module.search_code(
                    query="needle",
                    path=str(root),
                    mode="regex",
                    regex_deadline_seconds=0.10,
                ))
            elapsed = time.monotonic() - started

            lane = executor_lanes.get_executor_lane("search")
            for _ in range(100):
                with search_module._SEARCH_SINGLEFLIGHT_LOCK:
                    registry_empty = not search_module._SEARCH_SINGLEFLIGHT
                if registry_empty and lane.status()["in_flight"] == 0:
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(payload["error_name"], "REGEX_SEARCH_TIMEOUT")
            self.assertLess(elapsed, 0.40)
            self.assertLess(blob_reads, 40)
            self.assertTrue(registry_empty)
            self.assertEqual(lane.status()["in_flight"], 0)
            self.assertEqual(self._helper_pids(), baseline)
            self.assertFalse(current.exists())
            self.assertTrue(generation.is_dir())
            self.assertEqual((generation / "MANIFEST.json").read_bytes(), manifest_before)
            self.assertEqual(list((store / "staging").iterdir()), [])

    async def test_valid_current_slow_manifest_read_respects_pin_deadline_and_drains(self) -> None:
        baseline = self._helper_pids()
        real_read_bytes = Path.read_bytes

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.txt").write_text("needle\n", encoding="utf-8")
            snapshot_id = search_module.search_snapshots.publish_snapshot(
                root,
                include_file=lambda path: path.suffix == ".txt",
                skip_dir=lambda name: name.startswith("."),
                index=None,
            )
            store = search_module.search_snapshots.snapshot_store_path(root)
            current_before = (store / "CURRENT").read_bytes()

            def slow_manifest_read(path):
                if Path(path).name == search_module.search_snapshots.MANIFEST_FILENAME:
                    time.sleep(0.15)
                return real_read_bytes(path)

            started = time.monotonic()
            with mock.patch.object(Path, "read_bytes", slow_manifest_read):
                payload = json.loads(await search_module.search_code(
                    query="needle",
                    path=str(root),
                    mode="regex",
                    regex_deadline_seconds=0.10,
                ))
            elapsed = time.monotonic() - started

            lane = executor_lanes.get_executor_lane("search")
            for _ in range(100):
                with search_module._SEARCH_SINGLEFLIGHT_LOCK:
                    registry_empty = not search_module._SEARCH_SINGLEFLIGHT
                if registry_empty and lane.status()["in_flight"] == 0:
                    break
                await asyncio.sleep(0.01)

            lease_dir = store / "leases" / snapshot_id
            self.assertEqual(payload["error_name"], "REGEX_SEARCH_TIMEOUT")
            self.assertLess(elapsed, 0.40)
            self.assertTrue(registry_empty)
            self.assertEqual(lane.status()["in_flight"], 0)
            self.assertEqual(self._helper_pids(), baseline)
            self.assertEqual((store / "CURRENT").read_bytes(), current_before)
            self.assertTrue((store / "generations" / snapshot_id).is_dir())
            self.assertEqual(list(lease_dir.glob("*.lease")), [])

    async def test_manifest_budget_fails_before_snapshot_blob_read(self) -> None:
        from unittest import mock

        class FakePin:
            snapshot_id = "snapshot-budget"

            def __init__(self):
                self.read_count = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def iter_records(self, base):
                return [{"path": "huge.txt", "size": search_regex_worker.MAX_INPUT_BYTES + 1}]

            def read_bytes(self, relative_path):
                self.read_count += 1
                raise AssertionError("oversize blob was read")

            def release(self):
                return None

        fake_pin = FakePin()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(search_module, "_pin_or_create_snapshot", return_value=fake_pin):
                payload = json.loads(await search_module.search_code(query="needle", path=str(root), mode="regex"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_name"], "REGEX_INPUT_BUDGET_EXCEEDED")
        self.assertEqual(fake_pin.read_count, 0)

    def test_start_failure_closes_process_and_removes_temp_files(self) -> None:
        from unittest import mock

        class FakeProcess:
            def __init__(self):
                self.closed = False

            def start(self):
                raise OSError("spawn failed")

            def close(self):
                self.closed = True

        class FakeContext:
            def __init__(self, process):
                self.process = process

            def Process(self, *args, **kwargs):
                return self.process

        fake_process = FakeProcess()
        created = []
        real_mkstemp = tempfile.mkstemp
        with tempfile.TemporaryDirectory() as tmp:
            def tracked_mkstemp(*args, **kwargs):
                kwargs["dir"] = tmp
                fd, path = real_mkstemp(*args, **kwargs)
                created.append(path)
                return fd, path

            with mock.patch.object(search_regex_worker.tempfile, "mkstemp", side_effect=tracked_mkstemp):
                with mock.patch.object(search_regex_worker.multiprocessing, "get_context", return_value=FakeContext(fake_process)):
                    with self.assertRaises(OSError):
                        search_regex_worker.search_records("x", 0, [], max_results=1, context_lines=0)
            self.assertTrue(fake_process.closed)
            self.assertTrue(all(not Path(path).exists() for path in created))

    def test_primary_worker_error_survives_reap_failure(self) -> None:
        class FakeProcess:
            pid = 123
            exitcode = 7

            def start(self):
                return None

            def join(self, timeout=None):
                return None

            def is_alive(self):
                return False

        class FakeContext:
            def Process(self, *args, **kwargs):
                return FakeProcess()

        with mock.patch.object(search_regex_worker.multiprocessing, "get_context", return_value=FakeContext()), \
             mock.patch.object(search_regex_worker, "_stop_and_reap", side_effect=search_regex_worker.RegexSearchError("reap failed")):
            with self.assertRaisesRegex(search_regex_worker.RegexSearchError, "exit_code=7") as caught:
                search_regex_worker.preflight_pattern("safe", 0)
        self.assertTrue(any("cleanup also failed: reap failed" in note for note in getattr(caught.exception, "__notes__", [])))

    def test_reap_never_uses_unbounded_join(self) -> None:
        class FakeProcess:
            pid = 456

            def __init__(self):
                self.join_timeouts = []
                self.alive_checks = 0
                self.terminated = False
                self.killed = False
                self.closed = False

            def is_alive(self):
                self.alive_checks += 1
                return self.alive_checks <= 2

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

            def join(self, timeout=None):
                self.join_timeouts.append(timeout)

            def close(self):
                self.closed = True

        process = FakeProcess()
        search_regex_worker._stop_and_reap(process)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertTrue(process.closed)
        self.assertTrue(process.join_timeouts)
        self.assertTrue(all(timeout is not None and timeout > 0 for timeout in process.join_timeouts))
if __name__ == "__main__":
    unittest.main()
