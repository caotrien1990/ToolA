from __future__ import annotations

import sqlite3
from contextlib import closing
import tempfile
import threading
import time
import unittest
from pathlib import Path

from orchestrator.server.audit_writer import (
    AuditCapacityError,
    AuditClosedError,
    AuditIdConflict,
    AuditSchemaMismatch,
    AuditStorageBusy,
    DurableAuditWriter,
    SESSION_EVENT_KIND,
    WRITE_LEDGER_KIND,
)


class DurableAuditWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    @staticmethod
    def _row(db_path: Path, audit_id: str) -> tuple:
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute(
                """SELECT audit_id, state, attempt_count, applied_at
                   FROM durable_audit_outbox WHERE audit_id=?""",
                (audit_id,),
            ).fetchone()
        if row is None:
            raise AssertionError(f"missing outbox row {audit_id!r}")
        return row

    @staticmethod
    def _count(db_path: Path) -> int:
        with closing(sqlite3.connect(db_path)) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM durable_audit_outbox"
                ).fetchone()[0]
            )

    def test_stage_and_applied_ack_happen_after_their_commits(self) -> None:
        db_path = self.root / "ack.db"
        sink_observed_states: list[str] = []

        def sink(record) -> None:
            # The callback runs before the applied transition. A separate
            # connection therefore still sees the already-committed pending row.
            sink_observed_states.append(self._row(db_path, record.audit_id)[1])

        writer = DurableAuditWriter(db_path, sink, auto_start=False)

        staged = writer.stage(
            "ack-1", SESSION_EVENT_KIND, {"session_id": "s1", "status": "ok"}
        )
        self.assertTrue(staged.durable)
        self.assertEqual(staged.state, "pending")
        self.assertFalse(staged.duplicate)
        self.assertEqual(self._row(db_path, "ack-1")[1], "pending")

        writer.start()
        applied = writer.submit_and_wait(
            "ack-1",
            SESSION_EVENT_KIND,
            {"status": "ok", "session_id": "s1"},
            timeout=2.0,
        )
        self.assertTrue(applied.durable)
        self.assertTrue(applied.applied)
        self.assertTrue(applied.duplicate)
        self.assertEqual(sink_observed_states, ["pending"])
        row = self._row(db_path, "ack-1")
        self.assertEqual(row[1], "applied")
        self.assertEqual(row[2], 1)
        self.assertIsNotNone(row[3])
        result = writer.shutdown(timeout=2.0)
        self.assertTrue(result.drained)
        self.assertTrue(result.worker_stopped)

    def test_duplicate_is_idempotent_and_conflicting_reuse_fails_loud(self) -> None:
        db_path = self.root / "idempotent.db"
        sink_calls: list[str] = []
        writer = DurableAuditWriter(
            db_path,
            lambda record: sink_calls.append(record.audit_id),
            poll_interval_s=0.01,
        )

        first = writer.submit_and_wait(
            "same-id", WRITE_LEDGER_KIND, {"operation_id": "op-1"}, timeout=2.0
        )
        duplicate = writer.submit(
            "same-id", WRITE_LEDGER_KIND, {"operation_id": "op-1"}
        )

        self.assertTrue(first.applied)
        self.assertTrue(duplicate.applied)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(sink_calls, ["same-id"])
        self.assertEqual(self._count(db_path), 1)
        with self.assertRaises(AuditIdConflict):
            writer.submit(
                "same-id", WRITE_LEDGER_KIND, {"operation_id": "different"}
            )

        metrics = writer.metrics()
        self.assertEqual(metrics["pending"], 0)
        self.assertEqual(metrics["applied"], 1)
        self.assertEqual(metrics["staged_total"], 1)
        self.assertGreaterEqual(metrics["duplicate_total"], 1)
        self.assertEqual(metrics["id_conflicts_total"], 1)
        self.assertEqual(metrics["last_error_kind"], "id_conflict")
        self.assertEqual(metrics["last_error_context"], "same-id")
        self.assertIn("different content", metrics["last_error"] or "")
        self.assertIsNotNone(metrics["last_error_at"])
        writer.shutdown(timeout=2.0)

    def test_sqlite_lock_retries_synchronously_and_capacity_backpressures(self) -> None:
        db_path = self.root / "pressure.db"
        writer = DurableAuditWriter(
            db_path,
            lambda record: None,
            capacity=1,
            busy_timeout_ms=25,
            db_retry_attempts=12,
            retry_backoff_s=0.005,
            capacity_wait_timeout_s=0.12,
            poll_interval_s=0.01,
            auto_start=False,
        )

        lock_conn = sqlite3.connect(db_path, timeout=0, isolation_level=None)
        lock_conn.execute("BEGIN IMMEDIATE")
        started = threading.Event()
        retry_seen = threading.Event()
        results: list[object] = []
        errors: list[BaseException] = []
        real_bump = writer._bump

        def observe_retry(key: str, amount: int = 1) -> None:
            real_bump(key, amount)
            if key == "busy_retries":
                retry_seen.set()

        writer._bump = observe_retry  # type: ignore[method-assign]

        def synchronous_submitter() -> None:
            started.set()
            try:
                results.append(
                    writer.stage(
                        "locked-1", SESSION_EVENT_KIND, {"n": 1}, timeout=1.5
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=synchronous_submitter)
        thread.start()
        self.assertTrue(started.wait(1.0))
        self.assertTrue(retry_seen.wait(1.0))
        self.assertTrue(
            thread.is_alive(),
            "plain producer thread should synchronously backpressure on SQLite lock",
        )
        lock_conn.execute("ROLLBACK")
        lock_conn.close()
        thread.join(timeout=2.0)

        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(len(results), 1)
        self.assertEqual(self._row(db_path, "locked-1")[1], "pending")
        self.assertGreater(writer.metrics()["busy_retries"], 0)

        began = time.monotonic()
        with self.assertRaises(AuditCapacityError):
            writer.stage(
                "capacity-2", SESSION_EVENT_KIND, {"n": 2}, timeout=0.12
            )
        elapsed = time.monotonic() - began
        self.assertGreaterEqual(elapsed, 0.08)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(self._count(db_path), 1)
        self.assertEqual(writer.metrics()["capacity_rejections"], 1)

        result = writer.shutdown(timeout=0.2, drain=False)
        self.assertFalse(result.drained)
        self.assertEqual(result.pending, 1)

    def test_crash_reopen_boot_replays_one_outbox_row_once(self) -> None:
        db_path = self.root / "replay.db"

        # No worker is started: process loss after this point leaves a committed
        # pending row, which is the crash state a new instance must discover.
        before_crash = DurableAuditWriter(
            db_path, lambda record: None, auto_start=False
        )
        before_crash.stage(
            "crash-1", SESSION_EVENT_KIND, {"tool": "write_fix"}
        )
        before_crash.close_admission()
        self.assertEqual(self._count(db_path), 1)
        self.assertEqual(self._row(db_path, "crash-1")[1], "pending")

        replayed: list[str] = []
        after_reopen = DurableAuditWriter(
            db_path,
            lambda record: replayed.append(record.audit_id),
            poll_interval_s=0.01,
        )
        self.assertTrue(after_reopen.wait_until_idle(timeout=2.0))
        self.assertEqual(replayed, ["crash-1"])
        self.assertEqual(self._count(db_path), 1)
        self.assertEqual(self._row(db_path, "crash-1")[1], "applied")

        duplicate = after_reopen.submit(
            "crash-1", SESSION_EVENT_KIND, {"tool": "write_fix"}
        )
        self.assertTrue(duplicate.duplicate)
        self.assertTrue(duplicate.applied)
        time.sleep(0.03)
        self.assertEqual(replayed, ["crash-1"])
        after_reopen.shutdown(timeout=2.0)

    def test_clean_drain_and_timeout_retain_pending_for_next_boot(self) -> None:
        clean_db = self.root / "clean.db"
        clean_calls: list[str] = []
        clean = DurableAuditWriter(
            clean_db,
            lambda record: clean_calls.append(record.audit_id),
            batch_size=2,
            poll_interval_s=0.01,
        )
        for number in range(6):
            clean.stage(
                f"clean-{number}", WRITE_LEDGER_KIND, {"number": number}
            )
        clean_result = clean.shutdown(timeout=3.0, drain=True)
        self.assertTrue(clean_result.drained)
        self.assertEqual(clean_result.pending, 0)
        self.assertTrue(clean_result.worker_stopped)
        self.assertEqual(set(clean_calls), {f"clean-{n}" for n in range(6)})
        with self.assertRaises(AuditClosedError):
            clean.stage("late", WRITE_LEDGER_KIND, {})

        timeout_db = self.root / "timeout.db"
        sink_entered = threading.Event()
        release_sink = threading.Event()

        def blocked_sink(record) -> None:
            sink_entered.set()
            release_sink.wait(2.0)

        blocked = DurableAuditWriter(
            timeout_db, blocked_sink, poll_interval_s=0.01
        )
        blocked.stage("timeout-1", SESSION_EVENT_KIND, {"event": "slow"})
        self.assertTrue(sink_entered.wait(1.0))

        timeout_result = blocked.shutdown(timeout=0.06, drain=True)
        self.assertFalse(timeout_result.drained)
        self.assertTrue(timeout_result.timed_out)
        self.assertEqual(timeout_result.pending, 1)
        self.assertEqual(self._row(timeout_db, "timeout-1")[1], "pending")

        release_sink.set()
        self.assertTrue(blocked.wait_stopped(timeout=1.0))
        self.assertEqual(
            self._row(timeout_db, "timeout-1")[1],
            "pending",
            "a callback returning after the drain deadline must not forge an ACK",
        )

        replayed: list[str] = []
        reopened = DurableAuditWriter(
            timeout_db,
            lambda record: replayed.append(record.audit_id),
            poll_interval_s=0.01,
        )
        self.assertTrue(reopened.wait_until_idle(timeout=2.0))
        self.assertEqual(replayed, ["timeout-1"])
        self.assertEqual(self._row(timeout_db, "timeout-1")[1], "applied")
        reopened.shutdown(timeout=2.0)

    def test_two_writers_share_one_atomic_lease_during_slow_sink(self) -> None:
        db_path = self.root / "two-writers.db"
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []
        active = 0
        max_active = 0
        guard = threading.Lock()

        def sink(record) -> None:
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
                calls.append(record.audit_id)
            entered.set()
            release.wait(2.0)
            with guard:
                active -= 1

        first = DurableAuditWriter(
            db_path,
            sink,
            lease_duration_s=0.30,
            poll_interval_s=0.005,
            auto_start=False,
        )
        second = DurableAuditWriter(
            db_path,
            sink,
            lease_duration_s=0.30,
            poll_interval_s=0.005,
            auto_start=False,
        )
        first.stage("leased-once", SESSION_EVENT_KIND, {"n": 1})
        first_result = None
        second_result = None
        try:
            gate = threading.Barrier(3)
            starters = [
                threading.Thread(target=lambda w=writer: (gate.wait(), w.start()))
                for writer in (first, second)
            ]
            for thread in starters:
                thread.start()
            gate.wait()
            for thread in starters:
                thread.join(timeout=1.0)

            self.assertTrue(entered.wait(1.0))
            time.sleep(0.65)
            self.assertEqual(calls, ["leased-once"])
            self.assertEqual(max_active, 1)
            self.assertEqual(
                first.metrics()["lease_claimed_total"]
                + second.metrics()["lease_claimed_total"],
                1,
            )
            self.assertGreaterEqual(
                first.metrics()["lease_renewed_total"]
                + second.metrics()["lease_renewed_total"],
                2,
            )

            release.set()
            self.assertTrue(first.wait_until_idle(timeout=2.0))
            self.assertEqual(calls, ["leased-once"])
            self.assertEqual(self._row(db_path, "leased-once")[1], "applied")
        finally:
            release.set()
            first_result = first.shutdown(timeout=1.0)
            second_result = second.shutdown(timeout=1.0)
        self.assertTrue(first_result.worker_stopped)
        self.assertTrue(second_result.worker_stopped)

    def test_expired_foreign_lease_is_recovered_and_replayed(self) -> None:
        db_path = self.root / "lease-recovery.db"
        original = DurableAuditWriter(db_path, lambda record: None, auto_start=False)
        original.stage("expired-1", SESSION_EVENT_KIND, {"tool": "write_fix"})
        expires_at = time.time() + 0.12
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """UPDATE durable_audit_outbox
                   SET lease_owner='dead-process', lease_until=?
                   WHERE audit_id='expired-1'""",
                (expires_at,),
            )
            conn.commit()

        replayed: list[str] = []
        recovered = DurableAuditWriter(
            db_path,
            lambda record: replayed.append(record.audit_id),
            lease_duration_s=0.05,
            poll_interval_s=0.005,
        )
        time.sleep(0.04)
        self.assertEqual(replayed, [], "a live foreign lease must not be stolen early")
        self.assertEqual(recovered.metrics()["active_leases"], 1)

        self.assertTrue(recovered.wait_until_idle(timeout=2.0))
        self.assertEqual(replayed, ["expired-1"])
        self.assertGreaterEqual(recovered.metrics()["lease_recovered_total"], 1)
        self.assertEqual(self._row(db_path, "expired-1")[1], "applied")
        self.assertTrue(recovered.shutdown(timeout=1.0).ok)
        self.assertTrue(original.shutdown(timeout=1.0).ok)

    def test_shutdown_storage_failure_still_closes_and_joins_worker(self) -> None:
        db_path = self.root / "shutdown-busy.db"
        writer = DurableAuditWriter(
            db_path, lambda record: None, poll_interval_s=0.005
        )
        self.assertTrue(writer.metrics()["worker_alive"])
        real_pending_count = writer.pending_count

        def storage_busy() -> int:
            raise AuditStorageBusy("deterministic SQLite lock during shutdown")

        writer.pending_count = storage_busy  # type: ignore[method-assign]
        try:
            result = writer.shutdown(timeout=0.5, drain=True)
        finally:
            writer.pending_count = real_pending_count  # type: ignore[method-assign]

        self.assertFalse(result.ok)
        self.assertTrue(result.admission_closed)
        self.assertTrue(result.worker_stopped)
        self.assertIsNotNone(result.storage_error)
        self.assertIn("AuditStorageBusy", result.storage_error or "")
        self.assertTrue(writer.wait_stopped(timeout=0.2))
        with self.assertRaises(AuditClosedError):
            writer.stage("late-after-busy", SESSION_EVENT_KIND, {})

        moved = db_path.with_suffix(".moved")
        db_path.rename(moved)
        moved.rename(db_path)

    def test_sink_failure_uses_observable_retry_backoff(self) -> None:
        db_path = self.root / "sink-retry.db"
        attempts: list[float] = []
        first_failed = threading.Event()

        def flaky_sink(record) -> None:
            attempts.append(time.monotonic())
            if len(attempts) == 1:
                first_failed.set()
                raise RuntimeError("transient sink failure")

        writer = DurableAuditWriter(
            db_path,
            flaky_sink,
            sink_retry_base_s=0.12,
            sink_retry_max_s=0.12,
            poll_interval_s=0.005,
        )
        writer.stage("retry-1", SESSION_EVENT_KIND, {"n": 1})
        self.assertTrue(first_failed.wait(1.0))
        time.sleep(0.04)
        self.assertEqual(len(attempts), 1)
        during_backoff = writer.metrics()
        self.assertEqual(during_backoff["apply_failures"], 1)
        self.assertEqual(during_backoff["sink_retry_deferred_total"], 1)
        self.assertIsNotNone(during_backoff["next_retry_at"])
        self.assertEqual(during_backoff["active_leases"], 0)

        self.assertTrue(writer.wait_until_idle(timeout=2.0))
        self.assertEqual(len(attempts), 2)
        self.assertGreaterEqual(attempts[1] - attempts[0], 0.10)
        self.assertEqual(self._row(db_path, "retry-1")[1], "applied")
        recovered_health = writer.metrics()
        self.assertIsNone(recovered_health["last_error"])
        self.assertIsNone(recovered_health["last_error_kind"])
        self.assertEqual(recovered_health["apply_failures"], 1)
        self.assertEqual(recovered_health["sink_retry_deferred_total"], 1)
        self.assertTrue(writer.shutdown(timeout=1.0).ok)

    def test_previous_valid_schema_is_additively_migrated(self) -> None:
        db_path = self.root / "old-valid.db"
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """CREATE TABLE durable_audit_outbox (
                    audit_id TEXT PRIMARY KEY NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'applied')),
                    created_at REAL NOT NULL,
                    applied_at REAL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )"""
            )
            conn.execute(
                """CREATE INDEX ix_durable_audit_outbox_state_created
                   ON durable_audit_outbox(state, created_at, audit_id)"""
            )
            conn.commit()

        calls: list[str] = []
        migrated = DurableAuditWriter(
            db_path,
            lambda record: calls.append(record.audit_id),
            poll_interval_s=0.005,
            auto_start=False,
        )
        with closing(sqlite3.connect(db_path)) as conn:
            columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(durable_audit_outbox)"
                )
            }
        self.assertTrue(
            {"lease_owner", "lease_until", "next_attempt_at"}.issubset(columns)
        )
        migrated.start()
        self.assertTrue(
            migrated.submit_and_wait(
                "migrated-1", SESSION_EVENT_KIND, {"n": 1}, timeout=1.0
            ).applied
        )
        self.assertEqual(calls, ["migrated-1"])
        self.assertTrue(migrated.shutdown(timeout=1.0).ok)

    def test_concurrent_legacy_schema_migration_is_serialized(self) -> None:
        db_path = self.root / "concurrent-legacy.db"
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """CREATE TABLE durable_audit_outbox (
                    audit_id TEXT PRIMARY KEY NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'applied')),
                    created_at REAL NOT NULL,
                    applied_at REAL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )"""
            )
            conn.execute(
                """CREATE INDEX ix_durable_audit_outbox_state_created
                   ON durable_audit_outbox(state, created_at, audit_id)"""
            )
            conn.commit()

        writer_count = 6
        start_gate = threading.Barrier(writer_count + 1)
        snapshot_gate = threading.Barrier(writer_count)
        local = threading.local()
        original_actual_columns = DurableAuditWriter._actual_columns
        writers: list[DurableAuditWriter] = []
        errors: list[BaseException] = []
        result_lock = threading.Lock()

        def synchronize_old_race(conn) -> dict[str, tuple[str, int, int]]:
            columns = original_actual_columns(conn)
            local.snapshot_calls = getattr(local, "snapshot_calls", 0) + 1
            if local.snapshot_calls == 2:
                try:
                    snapshot_gate.wait(0.20)
                except threading.BrokenBarrierError:
                    pass
            return columns

        def open_writer() -> None:
            start_gate.wait()
            try:
                writer = DurableAuditWriter(
                    db_path,
                    lambda record: None,
                    busy_timeout_ms=50,
                    db_retry_attempts=20,
                    retry_backoff_s=0.005,
                    auto_start=False,
                )
                with result_lock:
                    writers.append(writer)
            except BaseException as exc:
                with result_lock:
                    errors.append(exc)

        DurableAuditWriter._actual_columns = staticmethod(synchronize_old_race)
        threads = [threading.Thread(target=open_writer) for _ in range(writer_count)]
        try:
            for thread in threads:
                thread.start()
            start_gate.wait()
            for thread in threads:
                thread.join(timeout=5.0)
        finally:
            DurableAuditWriter._actual_columns = staticmethod(original_actual_columns)

        try:
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(len(writers), writer_count)
            with closing(sqlite3.connect(db_path)) as conn:
                columns = {
                    row[1] for row in conn.execute(
                        "PRAGMA table_info(durable_audit_outbox)"
                    )
                }
            self.assertTrue(
                {"lease_owner", "lease_until", "next_attempt_at"}.issubset(columns)
            )
        finally:
            for writer in writers:
                writer.shutdown(timeout=1.0, drain=False)

    def test_existing_schema_mismatch_fails_loud_without_replacement(self) -> None:
        db_path = self.root / "mismatch.db"
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "CREATE TABLE durable_audit_outbox "
                "(audit_id TEXT PRIMARY KEY NOT NULL)"
            )

        with self.assertRaises(AuditSchemaMismatch):
            DurableAuditWriter(db_path, lambda record: None, auto_start=False)

        with closing(sqlite3.connect(db_path)) as conn:
            columns = [
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(durable_audit_outbox)"
                )
            ]
        self.assertEqual(columns, ["audit_id"])


if __name__ == "__main__":
    unittest.main()
