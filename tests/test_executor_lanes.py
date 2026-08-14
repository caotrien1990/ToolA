from __future__ import annotations

import asyncio
import threading
import unittest

import orchestrator.server.executor_lanes as executor_lanes
from orchestrator.server.executor_lanes import (
    ExecutorLane,
    ExecutorLaneClosed,
    ExecutorLaneOverloaded,
    executor_lane_status,
    get_executor_lane,
    run_in_executor_lane,
    shutdown_executor_lanes,
)


class ExecutorLaneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await shutdown_executor_lanes(drain_timeout=1)
        executor_lanes._reset_executor_lanes_for_tests()

    async def asyncTearDown(self) -> None:
        await shutdown_executor_lanes(drain_timeout=1)
        executor_lanes._reset_executor_lanes_for_tests()

    async def _wait_until(self, predicate, timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() >= deadline:
                self.fail("condition was not reached before timeout")
            await asyncio.sleep(0.005)

    async def test_capacity_rejects_without_unbounded_wait_and_metrics_are_exact(self):
        lane = ExecutorLane("unit_capacity", max_workers=1, max_queue=1)
        release = threading.Event()

        def blocker():
            release.wait(2)
            return "first"

        first = asyncio.create_task(lane.run(blocker))
        await self._wait_until(lambda: lane.status()["active"] == 1)
        second = asyncio.create_task(lane.run(lambda: "second"))
        await self._wait_until(lambda: lane.status()["queued"] == 1)

        with self.assertRaises(ExecutorLaneOverloaded):
            await lane.run(lambda: "overflow")

        status = lane.status()
        self.assertEqual(status["capacity"], 2)
        self.assertEqual(status["active"], 1)
        self.assertEqual(status["queued"], 1)
        self.assertEqual(status["rejected"], 1)
        self.assertEqual(status["high_watermark"], 2)

        release.set()
        self.assertEqual(await first, "first")
        self.assertEqual(await second, "second")
        await self._wait_until(lambda: lane.status()["completed"] == 2)
        final = lane.status()
        self.assertEqual(final["active"], 0)
        self.assertEqual(final["queued"], 0)
        self.assertEqual(final["submitted"], 2)
        await lane.close(drain_timeout=1)

    async def test_queue_wait_deadline_rejects_without_running_work(self):
        # WHY: admitted work must fail fast rather than wait indefinitely.
        lane = ExecutorLane("unit_deadline", max_workers=1, max_queue=1, queue_wait_timeout=0.05)
        release = threading.Event()
        ran = threading.Event()
        first = asyncio.create_task(lane.run(lambda: release.wait(2)))
        await self._wait_until(lambda: lane.status()["active"] == 1)
        try:
            with self.assertRaises(executor_lanes.ExecutorLaneQueueWaitExceeded) as caught:
                await lane.run(lambda: ran.set())
            payload = caught.exception.as_payload()
            self.assertEqual(payload["reason"], "queue_wait_budget_exceeded")
            self.assertEqual(payload["error_name"], "LANE_QUEUE_WAIT_EXCEEDED")
            self.assertFalse(ran.is_set())
            status = lane.status()
            self.assertEqual(status["rejection_counts"]["queue_wait_budget_exceeded"], 1)
            self.assertGreaterEqual(status["max_queue_wait_ms"], 40)
        finally:
            release.set()
            await first
            await lane.close(drain_timeout=1)

    async def test_caller_cancellation_is_distinct_from_overload(self):
        lane = ExecutorLane("unit_cancel", max_workers=1, max_queue=1, queue_wait_timeout=1)
        release = threading.Event()
        first = asyncio.create_task(lane.run(lambda: release.wait(2)))
        await self._wait_until(lambda: lane.status()["active"] == 1)
        queued = asyncio.create_task(lane.run(lambda: "never"))
        await self._wait_until(lambda: lane.status()["queued"] == 1)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        status = lane.status()
        self.assertEqual(status["rejection_counts"]["caller_cancelled"], 1)
        self.assertEqual(status["rejection_counts"]["queue_wait_budget_exceeded"], 0)
        release.set()
        await first
        await lane.close(drain_timeout=1)

    async def test_shutdown_cancellation_is_not_caller_cancellation(self):
        # WHY: executor shutdown owns cancellation of queued work; the caller did not cancel it.
        lane = ExecutorLane("unit_shutdown_cancel", max_workers=1, max_queue=1, queue_wait_timeout=1)
        release = threading.Event()
        first = asyncio.create_task(lane.run(lambda: release.wait(2)))
        await self._wait_until(lambda: lane.status()["active"] == 1)
        queued = asyncio.create_task(lane.run(lambda: "never"))
        await self._wait_until(lambda: lane.status()["queued"] == 1)
        try:
            close_result = await lane.close(drain_timeout=0)
            self.assertFalse(close_result["drained"])
            with self.assertRaises(ExecutorLaneClosed) as caught:
                await queued
            self.assertEqual(caught.exception.error_name, "EXECUTOR_SHUTTING_DOWN")
            await self._wait_until(lambda: lane.status()["cancelled"] == 1)
            status = lane.status()
            self.assertEqual(status["caller_cancelled"], 0)
            self.assertEqual(status["rejection_counts"]["caller_cancelled"], 0)
        finally:
            release.set()
            await first

    async def test_service_time_and_full_rejection_reason_metrics(self):
        lane = ExecutorLane("unit_metrics", max_workers=1, max_queue=0, queue_wait_timeout=1)
        release = threading.Event()
        first = asyncio.create_task(lane.run(lambda: release.wait(2)))
        await self._wait_until(lambda: lane.status()["active"] == 1)
        with self.assertRaises(ExecutorLaneOverloaded) as caught:
            await lane.run(lambda: None)
        self.assertEqual(caught.exception.as_payload()["reason"], "lane_full_at_submission")
        release.set()
        await first
        status = lane.status()
        self.assertEqual(status["rejection_counts"]["lane_full_at_submission"], 1)
        self.assertGreaterEqual(status["max_service_time_ms"], 0)
        await lane.close(drain_timeout=1)

    async def test_lanes_are_isolated(self):
        slow = ExecutorLane("unit_slow", max_workers=1, max_queue=1)
        fast = ExecutorLane("unit_fast", max_workers=1, max_queue=1)
        release = threading.Event()

        slow_task = asyncio.create_task(slow.run(lambda: release.wait(2)))
        await self._wait_until(lambda: slow.status()["active"] == 1)
        try:
            self.assertEqual(
                await asyncio.wait_for(fast.run(lambda: "fast"), timeout=0.25),
                "fast",
            )
        finally:
            release.set()
            await slow_task
            await slow.close(drain_timeout=1)
            await fast.close(drain_timeout=1)

    async def test_close_rejects_new_work_and_drains_accepted_work(self):
        lane = ExecutorLane("unit_close", max_workers=1, max_queue=1)
        release = threading.Event()
        first = asyncio.create_task(lane.run(lambda: release.wait(2)))
        await self._wait_until(lambda: lane.status()["active"] == 1)
        second = asyncio.create_task(lane.run(lambda: "queued"))
        await self._wait_until(lambda: lane.status()["queued"] == 1)

        closing = asyncio.create_task(lane.close(drain_timeout=1))
        await self._wait_until(lambda: not lane.status()["accepting"])
        with self.assertRaises(ExecutorLaneClosed):
            await lane.run(lambda: "late")

        release.set()
        await first
        self.assertEqual(await second, "queued")
        close_result = await closing
        self.assertTrue(close_result["drained"])
        self.assertEqual(lane.status()["active"], 0)
        self.assertEqual(lane.status()["queued"], 0)

    def test_status_is_observational_and_lazy_creation_still_works(self):
        self.assertEqual(executor_lanes._LANES, {})

        rows = {row["name"]: row for row in executor_lane_status()}

        # WHY: a health/status read must not allocate six pools or defeat lazy startup.
        self.assertEqual(executor_lanes._LANES, {})
        self.assertEqual(set(rows), set(executor_lanes._LANE_DEFAULTS))
        self.assertTrue(all(row["accepting"] for row in rows.values()))
        self.assertTrue(all(not row["registry_closing"] for row in rows.values()))

        lane = get_executor_lane("git")
        self.assertIs(executor_lanes._LANES["git"], lane)
        self.assertEqual(set(executor_lanes._LANES), {"git"})

    async def test_registry_shutdown_is_terminal_and_late_rejects_are_structured(self):
        original = get_executor_lane("read_io")
        rows = await shutdown_executor_lanes(drain_timeout=1)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["drained"])
        registry_keys = set(executor_lanes._LANES)

        # WHY: a closed lane must never be silently replaced by a fresh executor.
        with self.assertRaises(ExecutorLaneClosed) as get_error:
            get_executor_lane("read_io")
        self.assertIs(executor_lanes._LANES["read_io"], original)

        get_payload = get_error.exception.as_payload()
        self.assertEqual(get_payload["error_name"], "EXECUTOR_SHUTTING_DOWN")
        self.assertFalse(get_payload["retryable"])
        self.assertEqual(get_payload["ca_next_action"], "wait_for_orchestrator_restart")
        self.assertTrue(get_payload["executor"]["registry_closing"])

        # WHY: an uncreated lane requested after shutdown must reject without a pool.
        with self.assertRaises(ExecutorLaneClosed) as run_error:
            await run_in_executor_lane("command", lambda: "late")
        run_payload = run_error.exception.as_payload()
        self.assertEqual(run_payload["error_name"], "EXECUTOR_SHUTTING_DOWN")
        self.assertEqual(run_payload["executor_lane"], "command")
        self.assertTrue(run_payload["executor"]["registry_closing"])
        self.assertEqual(set(executor_lanes._LANES), registry_keys)
        self.assertNotIn("command", executor_lanes._LANES)

        executor_lane_status()
        self.assertEqual(set(executor_lanes._LANES), registry_keys)

    async def test_registry_shutdown_drains_work_accepted_before_gate(self):
        release = threading.Event()

        def accepted_work():
            release.wait(2)
            return "accepted"

        accepted = asyncio.create_task(run_in_executor_lane("test", accepted_work))
        await self._wait_until(
            lambda: get_executor_lane("test").status()["active"] == 1
        )
        closing = asyncio.create_task(shutdown_executor_lanes(drain_timeout=1))
        try:
            await self._wait_until(lambda: executor_lanes._LANES_CLOSING)

            # WHY: shutdown closes admission first but waits for already accepted work.
            self.assertFalse(closing.done())
            with self.assertRaises(ExecutorLaneClosed):
                await run_in_executor_lane("test", lambda: "late")

            release.set()
            self.assertEqual(await accepted, "accepted")
            rows = await closing
        finally:
            release.set()
            if not accepted.done():
                await accepted
            if not closing.done():
                await closing

        row = next(item for item in rows if item["name"] == "test")
        self.assertTrue(row["drained"])
        self.assertEqual(row["submitted"], 1)
        self.assertEqual(row["completed"], 1)
        self.assertEqual(row["active"], 0)
        self.assertEqual(row["queued"], 0)

    def test_default_lane_configuration_and_status_shape(self):
        expected = {
            "read_io": 12,
            "command": 4,
            "test": 2,
            "git": 4,
            "backup": 1,
            "council_provider": 8,
        }
        rows = {row["name"]: row for row in executor_lane_status()}
        for name, workers in expected.items():
            lane = get_executor_lane(name)
            self.assertEqual(lane.max_workers, workers)
            self.assertEqual(rows[name]["max_workers"], workers)
            for key in (
                "active", "queued", "capacity", "submitted", "completed",
                "rejected", "failed", "high_watermark", "oldest_wait_ms",
                "accepting", "registry_closing",
            ):
                self.assertIn(key, rows[name])


if __name__ == "__main__":
    unittest.main()
