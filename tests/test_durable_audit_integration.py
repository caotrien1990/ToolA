from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import code_tools
import orchestrator_server as srv
from orchestrator.server.accounting import PostCallAccountingQueue
from orchestrator.server.audit_writer import (
    SESSION_EVENT_KIND,
    AuditCapacityError,
    AuditIdConflict,
    AuditStorageBusy,
    DurableAuditWriter,
)
from orchestrator.storage.spool import read_spool, spool_depth


class DurableAuditIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        future = srv._DURABLE_METRICS_FUTURE
        if future is not None:
            try:
                future.result(timeout=1.0)
            except Exception:
                pass
        srv._DURABLE_METRICS_FUTURE = None
        srv._DURABLE_METRICS_CACHE_WRITER = None
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.project = self.root / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.doCleanups()
        self._temp_dir.cleanup()

    def _replace_durable_writer(self, writer: DurableAuditWriter) -> None:
        old_writer = srv._DURABLE_AUDIT
        srv._DURABLE_AUDIT = writer
        self.addCleanup(setattr, srv, "_DURABLE_AUDIT", old_writer)
        self.addCleanup(lambda: writer.shutdown(timeout=2.0, drain=False))

    def test_durable_sink_forwards_audit_id_and_is_outbox_idempotent(self) -> None:
        committed = []
        writer = DurableAuditWriter(
            self.root / "sink-idempotent.db",
            srv._persist_durable_session_event,
            auto_start=False,
            poll_interval_s=0.01,
        )
        self._replace_durable_writer(writer)

        def fake_log_session_event(*args, **kwargs):
            committed.append((args[0], args[1], kwargs.get("audit_id")))
            return 41

        body = {"project_path": str(self.project), "query": "same"}
        result = {"ok": True, "session_id": "session-before"}
        with mock.patch.object(
            code_tools, "_log_session_event", side_effect=fake_log_session_event
        ):
            first = srv._stage_durable_session_event(
                "sink-idempotent-1",
                "durable_sink_probe",
                body,
                result,
                17,
                "ok",
                "",
                {"agent_id": "agent-a"},
            )
            duplicate = srv._stage_durable_session_event(
                "sink-idempotent-1",
                "durable_sink_probe",
                body,
                result,
                17,
                "ok",
                "",
                {"agent_id": "agent-a"},
            )
            writer.start()
            self.assertTrue(writer.wait_until_idle(2.0))

        self.assertFalse(first.duplicate)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(
            committed,
            [("session-before", "durable_sink_probe", "sink-idempotent-1")],
        )

    def test_stage_captures_original_session_before_switch_or_close_then_replays(self) -> None:
        committed = []
        original_session_id = "session-original"
        writer = DurableAuditWriter(
            self.root / "capture.db",
            srv._persist_durable_session_event,
            auto_start=False,
            poll_interval_s=0.01,
        )
        self._replace_durable_writer(writer)

        with mock.patch.object(
            code_tools, "_get_active_session_id", return_value=original_session_id
        ):
            staged = srv._stage_durable_session_event(
                "capture-before-close",
                "durable_capture_probe",
                {"project_path": str(self.project), "query": "initial"},
                {"ok": True},
                21,
                "ok",
                "",
                {"agent_id": "agent-a", "host_session_id": "host-a"},
            )
        self.assertTrue(staged.durable)
        self.assertEqual(staged.state, "pending")

        def fake_log_session_event(*args, **kwargs):
            committed.append((args[0], args[1], kwargs.get("audit_id")))
            return 42

        # The source session is now unavailable; replay must use the identity
        # captured in the committed outbox payload, never a current active session.
        with (
            mock.patch.object(code_tools, "_get_active_session_id", return_value=None),
            mock.patch.object(
                code_tools, "_log_session_event", side_effect=fake_log_session_event
            ),
        ):
            writer.start()
            self.assertTrue(writer.wait_until_idle(2.0))

        self.assertEqual(
            committed,
            [
                (
                    original_session_id,
                    "durable_capture_probe",
                    "capture-before-close",
                )
            ],
        )

    def test_operation_id_reuse_is_duplicate_or_loud_conflict(self) -> None:
        writer = DurableAuditWriter(
            self.root / "operation-id.db",
            lambda record: None,
            auto_start=False,
        )
        self._replace_durable_writer(writer)
        body = {"project_path": str(self.project), "query": "same"}
        result = {"ok": True, "session_id": "session-a"}
        task_meta = {"agent_id": "agent-a"}

        first = srv._stage_durable_session_event(
            "same-operation-id", "durable_operation_probe", body, result,
            1, "ok", "", task_meta,
        )
        duplicate = srv._stage_durable_session_event(
            "same-operation-id", "durable_operation_probe", body, result,
            1, "ok", "", task_meta,
        )

        self.assertTrue(first.durable)
        self.assertFalse(first.duplicate)
        self.assertTrue(duplicate.duplicate)
        with self.assertRaises(AuditIdConflict):
            srv._stage_durable_session_event(
                "same-operation-id",
                "durable_operation_probe",
                {"project_path": str(self.project), "query": "changed"},
                result,
                1,
                "ok",
                "",
                task_meta,
            )
        self.assertEqual(writer.metrics()["staged_total"], 1)

    def test_concurrent_same_operation_id_creates_one_durable_outbox_record(self) -> None:
        writer = DurableAuditWriter(
            self.root / "concurrent.db",
            lambda record: None,
            auto_start=False,
            db_retry_attempts=12,
            retry_backoff_s=0.002,
        )
        self._replace_durable_writer(writer)
        worker_count = 32
        barrier = threading.Barrier(worker_count + 1)
        acknowledgements = []
        errors = []

        def stage_once() -> None:
            try:
                barrier.wait(timeout=5.0)
                acknowledgements.append(
                    srv._stage_durable_session_event(
                        "concurrent-operation-id",
                        "durable_concurrency_probe",
                        {"project_path": str(self.project), "query": "stable"},
                        {"ok": True, "session_id": "session-a"},
                        1,
                        "ok",
                        "",
                        {"agent_id": "agent-a"},
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=stage_once) for _ in range(worker_count)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5.0)
        for thread in threads:
            thread.join(timeout=5.0)

        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(acknowledgements), worker_count)
        self.assertEqual(sum(not ack.duplicate for ack in acknowledgements), 1)
        metrics = writer.metrics()
        self.assertEqual(metrics["staged_total"], 1)
        self.assertGreaterEqual(metrics["duplicate_total"], worker_count - 1)

    def test_stage_failure_preserves_completed_tool_response(self) -> None:
        tool_name = "__durable_stage_failure_probe__"

        async def probe() -> str:
            return json.dumps({"ok": True, "value": "tool-result"})

        old_tool = code_tools.TOOL_MAP.get(tool_name)
        code_tools.TOOL_MAP[tool_name] = probe
        try:
            with mock.patch.object(
                srv,
                "_stage_durable_session_event",
                side_effect=RuntimeError("synthetic durable admission failure"),
            ) as stage:
                response = asyncio.run(
                    srv.dispatch_tool_call(
                        tool_name,
                        {},
                        transport="http",
                        request_meta={"headers": {"X-Operation-ID": "stage-failure-op"}},
                    )
                )
            payload = json.loads(response.text)
        finally:
            if old_tool is None:
                code_tools.TOOL_MAP.pop(tool_name, None)
            else:
                code_tools.TOOL_MAP[tool_name] = old_tool

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["value"], "tool-result")
        stage.assert_called_once()

    def test_health_exposes_additive_durable_audit_metrics(self) -> None:
        class MetricsProbe:
            @staticmethod
            def metrics() -> dict:
                return {
                    "pending": 3,
                    "applied": 11,
                    "capacity": 64,
                    "staged_total": 14,
                    "duplicate_total": 5,
                    "applied_total": 11,
                    "apply_failures": 1,
                    "capacity_rejections": 2,
                    "admission_open": True,
                    "worker_alive": True,
                    "active_leases": 1,
                    "expired_leases": 0,
                    "lease_claimed_total": 13,
                    "lease_recovered_total": 1,
                    "next_retry_at": None,
                    "last_error": None,
                }

        with mock.patch.object(srv, "_DURABLE_AUDIT", MetricsProbe()):
            response = asyncio.run(srv.handle_health(SimpleNamespace(headers={})))
        payload = json.loads(response.text)

        self.assertEqual(payload["durable_audit_pending"], 3)
        self.assertEqual(payload["durable_audit_queue_capacity"], 64)
        self.assertEqual(payload["durable_audit_duplicate_total"], 5)
        self.assertEqual(payload["durable_audit_active_leases"], 1)
        self.assertTrue(payload["durable_audit_ok"])
        self.assertIsNone(payload["durable_audit_last_error"])
        self.assertIn("accounting_queue_depth", payload)

    def test_combined_flush_drains_durable_before_telemetry(self) -> None:
        durable_seen = []
        telemetry_seen = []
        writer = DurableAuditWriter(
            self.root / "flush.db",
            lambda record: durable_seen.append(record.audit_id),
            poll_interval_s=0.01,
        )
        accounting = PostCallAccountingQueue(
            lambda record: telemetry_seen.append(record["accounting_id"]),
            capacity=4,
            worker_name="durable-integration-accounting",
        )
        old_writer = srv._DURABLE_AUDIT
        old_accounting = srv._POST_CALL_ACCOUNTING
        srv._DURABLE_AUDIT = writer
        srv._POST_CALL_ACCOUNTING = accounting
        try:
            staged = srv._stage_durable_session_event(
                "flush-durable-id",
                "durable_flush_probe",
                {"project_path": str(self.project)},
                {"ok": True, "session_id": "session-a"},
                1,
                "ok",
                "",
                {"agent_id": "agent-a"},
            )
            self.assertTrue(staged.durable)
            self.assertTrue(
                accounting.enqueue(
                    {
                        "accounting_id": "flush-telemetry-id",
                        "tool_name": "durable_flush_probe",
                        "body": {},
                        "result": {},
                        "duration_ms": 1,
                        "status": "ok",
                        "error_msg": "",
                    }
                )
            )
            report = asyncio.run(srv._flush_post_call_accounting(timeout=2.0))
        finally:
            try:
                accounting.close(timeout=2.0)
            finally:
                writer.shutdown(timeout=2.0)
                srv._DURABLE_AUDIT = old_writer
                srv._POST_CALL_ACCOUNTING = old_accounting

        self.assertTrue(report["durable_drained"])
        self.assertEqual(report["pending"], 0)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(durable_seen, ["flush-durable-id"])
        self.assertEqual(telemetry_seen, ["flush-telemetry-id"])
    def test_create_app_cleanup_orders_background_executor_durable_then_telemetry(self) -> None:
        app = srv.create_app()
        callbacks = list(app.on_cleanup)
        expected = [
            srv._council_cron_cleanup,
            srv._executor_lanes_cleanup,
            srv._durable_metrics_cleanup,
            srv._durable_audit_cleanup,
            srv._post_call_accounting_cleanup,
        ]
        positions = [callbacks.index(callback) for callback in expected]

        self.assertEqual(positions, sorted(positions))
        self.assertEqual(
            positions,
            list(range(positions[0], positions[0] + len(expected))),
            "cleanup must drain background work, executors, durable audit, then telemetry",
        )

    def test_durable_metrics_cleanup_releases_future_executor_and_writer(self) -> None:
        release = threading.Event()

        class MetricsProbe:
            @staticmethod
            def metrics():
                release.wait(1.0)
                return {"pending": 0}

        old_writer = srv._DURABLE_AUDIT
        try:
            srv._DURABLE_AUDIT = MetricsProbe()
            asyncio.run(srv._durable_metrics_for_health())
            future = srv._DURABLE_METRICS_FUTURE
            executor = srv._DURABLE_METRICS_EXECUTOR
            release.set()
            asyncio.run(srv._durable_metrics_cleanup(None, timeout=1.0))
            self.assertTrue(future.done())
            self.assertIsNone(srv._DURABLE_METRICS_FUTURE)
            self.assertIsNone(srv._DURABLE_METRICS_EXECUTOR)
            self.assertIsNone(srv._DURABLE_METRICS_CACHE_WRITER)
            self.assertTrue(executor._shutdown)
        finally:
            release.set()
            srv._DURABLE_AUDIT = old_writer

    def test_health_metrics_exception_keeps_readiness_response_and_fails_durable_loud(self) -> None:
        class BrokenMetrics:
            @staticmethod
            def metrics() -> dict:
                raise RuntimeError("synthetic durable metrics failure")

        expected_ready = bool(srv.SERVER_READY)
        with mock.patch.object(srv, "_DURABLE_AUDIT", BrokenMetrics()):
            response = asyncio.run(srv.handle_health(SimpleNamespace(headers={})))
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200 if expected_ready else 503)
        self.assertEqual(payload["ready"], expected_ready)
        self.assertFalse(payload["durable_audit_ok"])
        self.assertIsNone(payload["durable_audit_pending"])
        self.assertIn("RuntimeError: synthetic durable metrics failure", payload["durable_audit_last_error"])
        self.assertIn("accounting_queue_depth", payload)

    def test_health_metrics_does_not_block_event_loop(self) -> None:
        class SlowMetrics:
            @staticmethod
            def metrics() -> dict:
                time.sleep(0.2)
                return {"pending": 1}

        async def probe() -> tuple[float, dict]:
            started = time.monotonic()
            response, _ = await asyncio.gather(
                srv.handle_health(SimpleNamespace(headers={})),
                asyncio.sleep(0.01),
            )
            return time.monotonic() - started, json.loads(response.text)

        with mock.patch.object(srv, "_DURABLE_AUDIT", SlowMetrics()):
            elapsed, payload = asyncio.run(probe())

        self.assertLess(elapsed, 0.15)
        self.assertFalse(payload["durable_audit_ok"])
        self.assertIn("TimeoutError", payload["durable_audit_last_error"])

    def test_repeated_health_timeouts_share_one_metrics_job_and_late_cache(self) -> None:
        release = threading.Event()

        class SlowMetrics:
            calls = 0

            @classmethod
            def metrics(cls) -> dict:
                cls.calls += 1
                release.wait(1.0)
                return {
                    "pending": 7, "admission_open": True,
                    "worker_alive": True, "last_error": None,
                }

        async def poll_many():
            return await asyncio.gather(*[
                srv.handle_health(SimpleNamespace(headers={})) for _ in range(12)
            ])

        old_future = srv._DURABLE_METRICS_FUTURE
        old_writer = srv._DURABLE_METRICS_CACHE_WRITER
        try:
            srv._DURABLE_METRICS_FUTURE = None
            srv._DURABLE_METRICS_CACHE_WRITER = None
            with mock.patch.object(srv, "_DURABLE_AUDIT", SlowMetrics()):
                responses = asyncio.run(poll_many())
                self.assertEqual(SlowMetrics.calls, 1)
                self.assertTrue(all(
                    json.loads(response.text)["durable_audit_metrics_stale"]
                    for response in responses
                ))
                release.set()
                time.sleep(0.05)
                response = asyncio.run(
                    srv.handle_health(SimpleNamespace(headers={})))
                payload = json.loads(response.text)
            self.assertEqual(payload["durable_audit_pending"], 7)
            self.assertFalse(payload["durable_audit_metrics_stale"])
        finally:
            release.set()
            srv._DURABLE_METRICS_FUTURE = old_future
            srv._DURABLE_METRICS_CACHE_WRITER = old_writer

    def test_health_no_such_table_metrics_is_degraded_not_failed(self) -> None:
        class MissingTableMetrics:
            @staticmethod
            def metrics() -> dict:
                raise sqlite3.OperationalError("no such table: durable_audit_outbox")

        with mock.patch.object(srv, "_DURABLE_AUDIT", MissingTableMetrics()):
            response = asyncio.run(srv.handle_health(SimpleNamespace(headers={})))
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200 if srv.SERVER_READY else 503)
        self.assertFalse(payload["durable_audit_ok"])
        self.assertIn("no such table", payload["durable_audit_last_error"])

    def test_process_shutdown_keeps_liveness_after_durable_shutdown(self) -> None:
        source = inspect.getsource(srv._run_server)
        runner_cleanup = source.index("await runner.cleanup()")
        durable_shutdown = source.index("_DURABLE_AUDIT.shutdown")
        liveness_shutdown = source.index("livez_server.shutdown()")
        liveness_close = source.index("livez_server.server_close()")

        # App cleanup provides durable flush before accounting close; process
        # shutdown then stops the durable worker before liveness is released.
        self.assertLess(runner_cleanup, durable_shutdown)
        self.assertLess(durable_shutdown, liveness_shutdown)
        self.assertLess(liveness_shutdown, liveness_close)

class DurableAuditDropPathTests(unittest.TestCase):
    """The paths that lost 21 + 6 + 3 audit rows in the 48h to 2026-09-27.

    Every test here is anchored to a row that actually disappeared, not to a
    hypothetical: a busy DB (21), a dict file_path (6), a stopped writer (3).
    """

    _METRICS_GLOBALS = (
        "_DURABLE_METRICS_CACHE",
        "_DURABLE_METRICS_CACHE_AT",
        "_DURABLE_METRICS_CACHE_WRITER",
        "_DURABLE_METRICS_FUTURE",
    )

    def setUp(self) -> None:
        for name in self._METRICS_GLOBALS:
            self.addCleanup(setattr, srv, name, getattr(srv, name))
        future = srv._DURABLE_METRICS_FUTURE
        if future is not None:
            try:
                future.result(timeout=1.0)
            except Exception:
                pass
        srv._DURABLE_METRICS_FUTURE = None
        srv._DURABLE_METRICS_CACHE_WRITER = None
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.root = Path(self._temp_dir.name)
        self.project = self.root / "project"
        self.project.mkdir()

    def _writer(self, name: str) -> DurableAuditWriter:
        writer = DurableAuditWriter(
            self.root / name,
            srv._persist_durable_session_event,
            auto_start=False,
            poll_interval_s=0.01,
        )
        old_writer = srv._DURABLE_AUDIT
        srv._DURABLE_AUDIT = writer
        self.addCleanup(setattr, srv, "_DURABLE_AUDIT", old_writer)
        self.addCleanup(lambda: writer.shutdown(timeout=2.0, drain=False))
        return writer

    def _stage(self, audit_id: str, body: dict | None = None):
        # session_id comes from the RESULT here, which is what keeps the call past the
        # "no capturable session" return at _stage_durable_session_event.
        return srv._stage_durable_session_event(
            audit_id,
            "__durable_drop_probe__",
            {"project_path": str(self.project)} if body is None else body,
            {"ok": True, "session_id": "session-drop"},
            11,
            "ok",
            "",
            {"agent_id": "agent-drop"},
        )

    # ---- F1: a busy DB must park the row, not log it away -------------------

    def test_busy_submit_parks_the_row_instead_of_dropping_it(self) -> None:
        writer = self._writer("drop-busy.db")
        with mock.patch.object(
            writer, "submit", side_effect=AuditStorageBusy("database is locked")
        ):
            self.assertIsNone(self._stage("drop-busy-1"))

        self.assertEqual(spool_depth(writer.spool_path), 1)
        records, corrupt = read_spool(writer.spool_path)
        self.assertEqual(corrupt, 0)
        self.assertEqual(records[0]["kind"], SESSION_EVENT_KIND)
        self.assertIn("AuditStorageBusy", records[0]["reason"])
        self.assertEqual(records[0]["payload"]["session_id"], "session-drop")
        self.assertEqual(records[0]["payload"]["tool_name"], "__durable_drop_probe__")

    def test_a_failed_park_is_reported_loud_rather_than_swallowed(self) -> None:
        # The spool is the fallback, so a broken spool is the end of the line: it has
        # to reach the caller's logger.critical, not vanish into a second except.
        writer = self._writer("drop-spool-fail.db")
        with mock.patch.object(
            writer, "submit", side_effect=AuditStorageBusy("database is locked")
        ), mock.patch.object(writer, "spool_failed_row", return_value=False):
            with self.assertRaises(AuditStorageBusy):
                self._stage("drop-spool-fail-1")

    # ---- F5: a stopped writer must not raise out of the accessor ------------

    def test_a_stopped_writer_parks_the_row_instead_of_raising_out_of_the_accessor(
        self,
    ) -> None:
        writer = self._writer("drop-closed.db")
        writer.start()
        writer.shutdown(timeout=2.0, drain=True)

        # Before this fix _get_durable_audit_writer(start=True) called start() on a
        # stopped writer, which raises AuditClosedError BY DESIGN. That escaped the
        # accessor, so the row never reached the spool fallback below it at all.
        self.assertIsNone(self._stage("drop-closed-1"))

        self.assertEqual(spool_depth(writer.spool_path), 1)
        records, _corrupt = read_spool(writer.spool_path)
        self.assertIn("AuditClosedError", records[0]["reason"])

    def test_the_accessor_absorbs_only_a_stopped_writer_not_any_start_failure(
        self,
    ) -> None:
        # Absorbing every start() failure would hide a real defect in the writer.
        writer = self._writer("drop-start-defect.db")
        with mock.patch.object(writer, "start", side_effect=ValueError("boom")):
            with self.assertRaisesRegex(ValueError, "boom"):
                srv._get_durable_audit_writer(start=True)

    # ---- F4: a dict file_path must not cost the row ------------------------

    def test_a_dict_file_path_does_not_lose_the_row(self) -> None:
        writer = self._writer("drop-dict-path.db")
        writer.start()

        # No project_path on purpose: that is what forces the find_project_root
        # branch, which is exactly where a dict file_path raised on 2026-09-24.
        ack = self._stage("drop-dict-1", {"file_path": {"unexpected": "dict"}})

        self.assertIsNotNone(ack)
        self.assertFalse(ack.duplicate)
        self.assertEqual(spool_depth(writer.spool_path), 0)
        self.assertEqual(writer.metrics()["staged_total"], 1)

    # ---- F2: the counters that would have named the cause ------------------

    def test_health_publishes_the_previously_unpublished_drop_counters(self) -> None:
        class MetricsProbe:
            @staticmethod
            def metrics() -> dict:
                return {
                    "pending": 0,
                    "applied": 0,
                    "capacity": 64,
                    "storage_busy_failures": 21,
                    "busy_retries": 7,
                    "id_conflicts_total": 2,
                    "spool_depth": 3,
                    "busy_timeout_ms": 1000,
                    "db_retry_attempts": 4,
                    "last_error": None,
                }

        with mock.patch.object(srv, "_DURABLE_AUDIT", MetricsProbe()):
            response = asyncio.run(srv.handle_health(SimpleNamespace(headers={})))
        payload = json.loads(response.text)

        self.assertEqual(payload["durable_audit_storage_busy_failures"], 21)
        self.assertEqual(payload["durable_audit_busy_retries"], 7)
        self.assertEqual(payload["durable_audit_id_conflicts_total"], 2)
        self.assertEqual(payload["durable_audit_spool_depth"], 3)
        # The busy budget is env-tunable now, so it is a per-process fact /health
        # has to state or "is the timeout too tight" is unanswerable from outside.
        self.assertEqual(payload["durable_audit_busy_timeout_ms"], 1000)
        self.assertEqual(payload["durable_audit_db_retry_attempts"], 4)

    # ---- F23: stale has to say HOW stale -----------------------------------

    def test_a_stale_metrics_answer_carries_a_real_age(self) -> None:
        pending = concurrent.futures.Future()
        self.addCleanup(pending.cancel)
        srv._DURABLE_METRICS_CACHE = {"pending": 5}
        srv._DURABLE_METRICS_CACHE_AT = time.monotonic() - 3.0
        srv._DURABLE_METRICS_CACHE_WRITER = object()
        srv._DURABLE_METRICS_FUTURE = pending

        with mock.patch.object(srv, "_DURABLE_AUDIT", object()):
            metrics, error = asyncio.run(srv._durable_metrics_for_health())

        self.assertTrue(metrics["metrics_stale"])
        self.assertIsNotNone(metrics["metrics_age_ms"])
        self.assertGreaterEqual(metrics["metrics_age_ms"], 2900)
        self.assertIn("already in flight", error)

    def test_a_never_collected_answer_still_reports_a_null_age(self) -> None:
        # The other meaning of a null age, which must stay distinguishable: there is
        # no cached dict at all, so there is no age to state.
        pending = concurrent.futures.Future()
        self.addCleanup(pending.cancel)
        srv._DURABLE_METRICS_CACHE = {}
        srv._DURABLE_METRICS_CACHE_AT = 0.0
        srv._DURABLE_METRICS_CACHE_WRITER = object()
        srv._DURABLE_METRICS_FUTURE = pending

        with mock.patch.object(srv, "_DURABLE_AUDIT", object()):
            metrics, _error = asyncio.run(srv._durable_metrics_for_health())

        self.assertTrue(metrics["metrics_stale"])
        self.assertIsNone(metrics["metrics_age_ms"])

    # ---- F20 / F6: what the logs are allowed to claim ----------------------

    def test_mcp_shutdown_skip_is_debug_while_http_shutdown_stays_a_warning(
        self,
    ) -> None:
        source = Path(srv.__file__).read_text(encoding="utf-8")

        self.assertIn(
            'logger.debug("durable_audit_disabled_in_mcp; skipping MCP shutdown")',
            source,
        )
        self.assertNotIn(
            'logger.warning("Durable audit not initialized; skipping MCP shutdown")',
            source,
        )
        # The HTTP daemon's copy is a REAL anomaly -- it must stay loud.
        self.assertIn(
            'logger.warning("Durable audit not initialized; skipping shutdown")',
            source,
        )

    def test_the_stage_call_site_no_longer_claims_a_drop_cannot_happen(self) -> None:
        # The old comment said "never silently drop the row" above a body that did
        # nothing but log. A comment that contradicts its code is a defect.
        source = Path(srv.__file__).read_text(encoding="utf-8")

        self.assertIn("Durable session-event stage AND spool failed", source)
        self.assertNotIn("never silently drop the row", source)

    # ---- the boundary of the spool: what must NOT be parked ----------------

    def test_an_id_conflict_stays_loud_and_parks_nothing(self) -> None:
        # The spool is a RETRY queue, so only a transient failure belongs in it. An
        # id conflict means one operation_id carried two different payloads, which
        # can never succeed on replay -- parking it would grow the spool forever
        # while hiding the caller's bug. A bare `except Exception` on the spool
        # fallback did exactly that, and the older duplicate-or-conflict test is
        # what caught it; this pins the other half, that nothing gets parked.
        writer = self._writer("drop-conflict.db")
        writer.start()

        body = {"project_path": str(self.project), "query": "first"}
        first = srv._stage_durable_session_event(
            "same-id-two-payloads", "__durable_drop_probe__", body,
            {"ok": True, "session_id": "session-drop"}, 11, "ok", "",
            {"agent_id": "agent-drop"},
        )
        self.assertTrue(first.durable)

        with self.assertRaises(AuditIdConflict):
            srv._stage_durable_session_event(
                "same-id-two-payloads", "__durable_drop_probe__",
                {"project_path": str(self.project), "query": "second"},
                {"ok": True, "session_id": "session-drop"}, 22, "ok", "",
                {"agent_id": "agent-drop"},
            )

        self.assertEqual(spool_depth(writer.spool_path), 0)

    def test_a_full_queue_is_parked_like_a_busy_db(self) -> None:
        # AuditCapacityError is the third replayable case: the row is perfectly
        # valid, there is simply no room right now, so it must survive.
        writer = self._writer("drop-capacity.db")
        with mock.patch.object(
            writer, "submit", side_effect=AuditCapacityError("queue is full")
        ):
            self.assertIsNone(self._stage("drop-capacity-1"))

        self.assertEqual(spool_depth(writer.spool_path), 1)
        records, _corrupt = read_spool(writer.spool_path)
        self.assertIn("AuditCapacityError", records[0]["reason"])


if __name__ == "__main__":
    unittest.main()
