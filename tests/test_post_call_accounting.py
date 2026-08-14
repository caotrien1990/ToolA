import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

import code_tools
import orchestrator_server
from orchestrator.server.accounting import PostCallAccountingQueue
from orchestrator.server.audit_writer import DurableAuditWriter


class TestPostCallAccountingIsolation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._old_accounting = orchestrator_server._POST_CALL_ACCOUNTING
        self._old_durable_audit = orchestrator_server._DURABLE_AUDIT
        self._accounting = PostCallAccountingQueue(
            orchestrator_server._persist_post_call_accounting,
            capacity=128,
            worker_name="accounting-isolation-test",
        )
        self._durable_audit = DurableAuditWriter(
            Path(self._temp_dir.name) / "durable-audit.db",
            orchestrator_server._persist_durable_session_event,
            poll_interval_s=0.01,
        )
        orchestrator_server._POST_CALL_ACCOUNTING = self._accounting
        orchestrator_server._DURABLE_AUDIT = self._durable_audit

    async def asyncTearDown(self):
        orchestrator_server._POST_CALL_ACCOUNTING = self._old_accounting
        orchestrator_server._DURABLE_AUDIT = self._old_durable_audit
        try:
            accounting_report = await asyncio.to_thread(self._accounting.close, 3.0)
            durable_report = await asyncio.to_thread(
                self._durable_audit.shutdown, 3.0
            )
            self.assertTrue(accounting_report["drained"])
            self.assertEqual(accounting_report["pending"], 0)
            self.assertTrue(durable_report.drained)
            self.assertEqual(durable_report.pending, 0)
        finally:
            self._temp_dir.cleanup()

    async def test_slow_accounting_does_not_block_responses_or_event_loop(self):
        tool_name = "__fast_accounting_probe__"
        sink_entered = threading.Event()
        release_sink = threading.Event()
        release_authorized = threading.Event()
        watchdog_forced_release = threading.Event()
        persisted = []
        persisted_lock = threading.Lock()
        old_log_usage = code_tools._log_usage

        async def probe():
            return json.dumps({"ok": True})

        def blocked_log_usage(*args, **kwargs):
            sink_entered.set()
            if not release_sink.wait(2.0):
                raise TimeoutError("accounting sink was not released")
            with persisted_lock:
                persisted.append(args[0])

        def watchdog():
            if not sink_entered.wait(1.0):
                watchdog_forced_release.set()
                release_sink.set()
                return
            # Anh 2026-08-10: 0.35s -> 2.0s. The property under test (a blocked
            # accounting sink never blocks the loop) is proven: standalone the 50
            # dispatches finish in ~47ms with the loop ticking freely. The old
            # 0.35s bound flaked only when the CI/host machine was heavily loaded
            # (50 concurrent dispatches + to_thread scheduling), not from any real
            # loop block. 2.0s stays well under the gather timeout, so a genuine
            # loop block still fails (gather never completes).
            if not release_authorized.wait(2.0):
                watchdog_forced_release.set()
                release_sink.set()

        code_tools.TOOL_MAP[tool_name] = probe
        code_tools._log_usage = blocked_log_usage
        watchdog_thread = threading.Thread(target=watchdog, name="accounting-test-watchdog", daemon=True)
        watchdog_thread.start()
        tick_count = 0
        ticker_stop = asyncio.Event()

        async def ticker():
            nonlocal tick_count
            while not ticker_stop.is_set():
                tick_count += 1
                await asyncio.sleep(0.001)

        ticker_task = asyncio.create_task(ticker())
        responses = []
        try:
            calls = [
                asyncio.create_task(
                    orchestrator_server.dispatch_tool_call(
                        tool_name,
                        {},
                        transport="mcp",
                        request_meta={"headers": {"X-Operation-ID": f"acct-{index:03d}"}},
                    )
                )
                for index in range(50)
            ]
            responses = await asyncio.wait_for(asyncio.gather(*calls), timeout=3.0)
            release_authorized.set()
            release_sink.set()
            flush = getattr(orchestrator_server, "_flush_post_call_accounting", None)
            if flush is not None:
                report = await flush(timeout=3.0)
                self.assertEqual(report["pending"], 0)
                self.assertEqual(report["failed"], 0)
        finally:
            release_authorized.set()
            release_sink.set()
            flush = getattr(orchestrator_server, "_flush_post_call_accounting", None)
            if flush is not None:
                await flush(timeout=3.0)
            ticker_stop.set()
            await ticker_task
            watchdog_thread.join(timeout=1.0)
            code_tools._log_usage = old_log_usage
            code_tools.TOOL_MAP.pop(tool_name, None)

        self.assertFalse(
            watchdog_forced_release.is_set(),
            "post-call accounting blocked the event loop until the watchdog released it",
        )
        self.assertGreaterEqual(tick_count, 2)
        self.assertEqual(len(responses), 50)
        self.assertTrue(all(response.status == 200 for response in responses))
        self.assertEqual(len(persisted), 50)

class TestPostCallAccountingQueue(unittest.TestCase):
    def test_sink_failure_isolated_and_fifo_continues(self):
        seen = []

        def handler(record):
            seen.append((record["accounting_id"], record["accounting_sequence"]))
            if record["accounting_id"] == "bad":
                raise RuntimeError("synthetic sink failure")

        lane = PostCallAccountingQueue(handler, capacity=4, worker_name="accounting-failure-test")
        try:
            self.assertTrue(lane.enqueue({"accounting_id": "bad"}))
            self.assertTrue(lane.enqueue({"accounting_id": "good"}))
            report = lane.flush(timeout=2.0)
            self.assertTrue(report["drained"])
            self.assertEqual(report["pending"], 0)
            self.assertEqual(report["processed"], 2)
            self.assertEqual(report["succeeded"], 1)
            self.assertEqual(report["failed"], 1)
            self.assertTrue(report["degraded"])
            self.assertEqual(report["last_error"]["kind"], "RuntimeError")
            self.assertEqual(seen, [("bad", 1), ("good", 2)])
        finally:
            lane.close(timeout=2.0)

    def test_capacity_overflow_is_nonblocking_and_fail_loud(self):
        sink_entered = threading.Event()
        release_sink = threading.Event()

        def blocked_handler(_record):
            sink_entered.set()
            if not release_sink.wait(2.0):
                raise TimeoutError("overflow test sink was not released")

        lane = PostCallAccountingQueue(blocked_handler, capacity=1, worker_name="accounting-overflow-test")
        try:
            self.assertTrue(lane.enqueue({"accounting_id": "in-flight"}))
            self.assertTrue(sink_entered.wait(1.0))
            self.assertTrue(lane.enqueue({"accounting_id": "queued"}))
            self.assertFalse(lane.enqueue({"accounting_id": "rejected"}))
            status = lane.status()
            self.assertEqual(status["pending"], 2)
            self.assertEqual(status["rejected"], 1)
            self.assertTrue(status["degraded"])
            self.assertEqual(status["last_error"]["reason"], "queue_full")
        finally:
            release_sink.set()
            report = lane.close(timeout=2.0)
            self.assertTrue(report["drained"])
            self.assertEqual(report["processed"], 2)

    def test_process_control_exception_closes_lane_and_discards_queued_records(self):
        for exception_type in (SystemExit, KeyboardInterrupt):
            with self.subTest(exception_type=exception_type.__name__):
                entered = threading.Event()
                release = threading.Event()
                seen = []

                def handler(record):
                    seen.append(record["accounting_id"])
                    if record["accounting_id"] == "terminal":
                        entered.set()
                        self.assertTrue(release.wait(1.0))
                        raise exception_type("synthetic process control")

                lane = PostCallAccountingQueue(
                    handler,
                    capacity=2,
                    worker_name=f"accounting-{exception_type.__name__}-test",
                )
                try:
                    self.assertTrue(lane.enqueue({"accounting_id": "terminal"}))
                    self.assertTrue(entered.wait(1.0))
                    self.assertTrue(lane.enqueue({"accounting_id": "queued"}))
                    release.set()

                    report = lane.flush(timeout=2.0)
                    self.assertTrue(report["drained"])
                    self.assertFalse(report["timed_out"])
                    self.assertEqual(report["pending"], 0)
                    self.assertEqual(report["processed"], 2)
                    self.assertEqual(report["failed"], 2)
                    self.assertEqual(report["discarded"], 1)
                    self.assertEqual(report["terminal_failure"]["kind"], exception_type.__name__)
                    self.assertEqual(report["terminal_failure"]["discarded"], 1)
                    self.assertFalse(report["accepting"])
                    self.assertFalse(report["worker_alive"])
                    self.assertTrue(report["closed"])
                    self.assertEqual(report["worker_restarts"], 0)
                    self.assertEqual(seen, ["terminal"])

                    self.assertFalse(lane.enqueue({"accounting_id": "rejected"}))
                    rejected = lane.status()
                    self.assertEqual(rejected["rejected"], 1)
                    self.assertEqual(rejected["last_error"]["reason"], "lane_terminal")
                    self.assertEqual(
                        rejected["terminal_failure"]["kind"], exception_type.__name__
                    )

                    closed = lane.close(timeout=2.0)
                    self.assertTrue(closed["drained"])
                    self.assertFalse(closed["timed_out"])
                    self.assertEqual(closed["pending"], 0)
                    self.assertEqual(closed["discarded"], 1)
                finally:
                    release.set()
                    lane.close(timeout=2.0)

    def test_unexpected_worker_death_restarts_without_third_enqueue(self):
        class SyntheticWorkerDeath(BaseException):
            pass

        first_entered = threading.Event()
        release_death = threading.Event()
        death_window_entered = threading.Event()
        seen = []

        def handler(record):
            if record["accounting_id"] == "fatal":
                first_entered.set()
                self.assertTrue(release_death.wait(1.0))
                raise SyntheticWorkerDeath("synthetic worker death")
            seen.append(record["accounting_id"])

        lane = PostCallAccountingQueue(handler, capacity=2, worker_name="accounting-death-test")
        original_start = lane.start

        def death_window_start():
            started = original_start()
            if first_entered.is_set() and not death_window_entered.is_set():
                death_window_entered.set()
                old_worker = lane._worker
                release_death.set()
                old_worker.join(timeout=1.0)
                self.assertFalse(old_worker.is_alive())
            return started

        lane.start = death_window_start
        try:
            self.assertTrue(lane.enqueue({"accounting_id": "fatal"}))
            self.assertTrue(first_entered.wait(1.0))
            self.assertTrue(lane.enqueue({"accounting_id": "queued"}))
            self.assertTrue(death_window_entered.is_set())

            report = lane.flush(timeout=2.0)
            self.assertTrue(report["drained"])
            self.assertEqual(report["pending"], 0)
            self.assertEqual(report["processed"], 2)
            self.assertEqual(report["failed"], 1)
            self.assertEqual(report["worker_fatal_errors"], 1)
            self.assertEqual(report["worker_restarts"], 1)
            self.assertTrue(report["worker_alive"])
            self.assertEqual(report["last_worker_fatal"]["kind"], "SyntheticWorkerDeath")
            self.assertEqual(seen, ["queued"])
        finally:
            release_death.set()
            lane.start = original_start
            lane.close(timeout=2.0)

    def test_close_drains_and_lane_can_restart(self):
        seen = []
        lane = PostCallAccountingQueue(
            lambda record: seen.append((record["accounting_id"], record["accounting_sequence"])),
            capacity=2,
            worker_name="accounting-restart-test",
        )
        self.assertTrue(lane.enqueue({"accounting_id": "first"}))
        first_close = lane.close(timeout=2.0)
        self.assertTrue(first_close["drained"])
        self.assertTrue(first_close["closed"])
        self.assertFalse(first_close["worker_alive"])

        self.assertTrue(lane.enqueue({"accounting_id": "second"}))
        second_flush = lane.flush(timeout=2.0)
        self.assertTrue(second_flush["drained"])
        second_close = lane.close(timeout=2.0)
        self.assertTrue(second_close["drained"])
        self.assertEqual(seen, [("first", 1), ("second", 2)])

if __name__ == "__main__":
    unittest.main()
