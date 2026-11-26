"""RT1-RT3: regression pins for the CI shard-1 causal chain (2026-11-26).

Measured root causes (see CI_SHARD1_10FAILURES_CLASSIFICATION_OPE_20261126.md):

* F-01 -- _init_session_db snapshotted db_key from the live _SESSIONS_DB alias
  but re-read the alias for mkdir/connect. A concurrent alias swap marked path
  A initialized while the DDL landed on path B, leaving A schema-less forever
  ("no such table" on every later access). Deterministic scratch repro:
  ope3rc1_repro.py RC1a/RC1b (FlippingLock around _SESSION_INIT_LOCK).
* F-02 -- DurableAuditWriter retried a PERMANENT sink error ("no such table")
  forever; every retry re-synced the store alias, producing a ~8 events/s
  storm for the whole 40-minute shard run.
* F-03 -- a writer could be created on a schema-less DB with no loud error.

These tests pin the fixes: init is atomic per target path, permanent sink
errors are quarantined after a bounded number of attempts, and writer
creation refuses a schema-less database. RT3 pins the F-04 fixture contract:
alias fixtures must point BOTH the store and code_tools aliases at the test
DB so background ct traffic cannot re-aim the store mid-test.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

import code_tools as ct
from orchestrator.session import store
from orchestrator.server.audit_writer import (
    SESSION_EVENT_KIND,
    AuditRecord,
    DurableAuditWriter,
)


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _tables(p) -> set:
    if not Path(p).exists():
        return set()
    conn = sqlite3.connect(str(p))
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


class InitAliasRaceRT1(unittest.TestCase):
    """F-01: the migration must target ONE snapshotted path, start to finish."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.A = self.root / "A_shared.db"
        self.B = self.root / "B_tmp.db"
        self._old_store_db = store._SESSIONS_DB
        self._old_ct_db = ct._SESSIONS_DB
        store.close_writer()
        for p in (self.A, self.B):
            store._SESSION_DB_INITIALIZED_PATHS.discard(_norm(p))
            store._SESSION_INIT_FAILURES.pop(_norm(p), None)

    def tearDown(self):
        store.close_writer()
        store._SESSIONS_DB = self._old_store_db
        ct._SESSIONS_DB = self._old_ct_db
        for p in (self.A, self.B):
            store._SESSION_DB_INITIALIZED_PATHS.discard(_norm(p))
            store._SESSION_INIT_FAILURES.pop(_norm(p), None)
        try:
            self._tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_flip_under_init_lock_keeps_ddl_on_the_snapshotted_path(self):
        # RC1a: the alias flips while init holds its lock. Pre-fix, db_key was
        # snapshotted BEFORE the flip but mkdir/connect read the LIVE alias, so
        # A was marked initialized while the DDL landed on B (A never created).
        store._SESSIONS_DB = self.A
        ct._SESSIONS_DB = self.A
        real_lock = store._SESSION_INIT_LOCK
        b_path = self.B

        class FlippingLock:
            def __enter__(self):
                store._SESSIONS_DB = b_path  # the concurrent alias swap
                return real_lock.__enter__()

            def __exit__(self, *a):
                return real_lock.__exit__(*a)

        store._SESSION_INIT_LOCK = FlippingLock()
        try:
            store._init_session_db()
        finally:
            store._SESSION_INIT_LOCK = real_lock
        self.assertIn(_norm(self.A), store._SESSION_DB_INITIALIZED_PATHS)
        self.assertTrue(
            {"sessions", "events", "write_operations"} <= _tables(self.A),
            "DDL must land on the snapshotted path A",
        )
        self.assertFalse(self.B.exists(), "DDL must not land on swapped-in path B")
        # RC1b: a writer on A must actually work (pre-fix: 'no such table').
        store._SESSIONS_DB = self.A
        ct._SESSIONS_DB = self.A
        writer = store._get_session_writer()
        self.assertEqual(writer.db_path, self.A)

    def test_explicit_db_path_overrides_the_live_alias(self):
        # _get_session_writer snapshots 'wanted' and must init THAT path, not
        # whatever the alias says when init gets around to reading it.
        store._SESSIONS_DB = self.A
        ct._SESSIONS_DB = self.A
        store._init_session_db(self.B)
        self.assertIn("sessions", _tables(self.B))
        self.assertFalse(self.A.exists())
        self.assertIn(_norm(self.B), store._SESSION_DB_INITIALIZED_PATHS)
        self.assertNotIn(_norm(self.A), store._SESSION_DB_INITIALIZED_PATHS)

    def test_writer_creation_refuses_a_schemaless_database(self):
        # F-03: residual state of the old bug -- path marked initialized, file
        # exists with zero tables. Serving it means permanent 'no such table'.
        conn = sqlite3.connect(str(self.A))
        conn.close()  # creates a zero-table file
        store._SESSION_DB_INITIALIZED_PATHS.add(_norm(self.A))
        store._SESSIONS_DB = self.A
        ct._SESSIONS_DB = self.A
        with self.assertRaises(store.SessionStoreSchemaError):
            store._get_session_writer()
        self.assertIsNone(store._SESSION_WRITER)


class SinkPermanentErrorRT2(unittest.TestCase):
    """F-02: a permanent sink error must be quarantined, not retried forever."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

        def broken_sink(record):
            raise sqlite3.OperationalError("no such table: sessions")

        self.writer = DurableAuditWriter(
            self.root / "audit.db",
            broken_sink,
            auto_start=False,
            sink_retry_base_s=0.01,
            sink_retry_max_s=0.02,
        )

    def tearDown(self):
        try:
            self.writer.shutdown(1.0)
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def _stage_and_claim(self, audit_id: str, attempt_count: int) -> AuditRecord:
        payload = {"session_id": "rt2", "tool_name": "probe"}
        self.writer.stage(audit_id, SESSION_EVENT_KIND, payload, timeout=0)
        # Claim the lease the way the drain loop would, so the failure UPDATE
        # and the quarantine DELETE both match on lease_owner.
        conn = sqlite3.connect(str(self.root / "audit.db"))
        try:
            conn.execute(
                "UPDATE durable_audit_outbox "
                "SET lease_owner=?, lease_until=?, attempt_count=? WHERE audit_id=?",
                (self.writer._owner_token, time.time() + 30.0, attempt_count, audit_id),
            )
            conn.commit()
        finally:
            conn.close()
        return AuditRecord(audit_id, SESSION_EVENT_KIND, payload, time.time(), attempt_count)

    def test_permanent_schema_error_is_quarantined_after_bounded_attempts(self):
        cap = 3  # must match audit_writer._SINK_PERMANENT_MAX_ATTEMPTS
        record = self._stage_and_claim("rt2-permanent", cap - 1)
        exc = sqlite3.OperationalError("no such table: sessions")
        self.assertTrue(self.writer._record_sink_failure(record, exc))
        # The row must be GONE from the outbox, not pending forever (the storm).
        self.assertIsNone(self.writer._read_row_state("rt2-permanent"))
        # Evidence must be durable in the dead-letter quarantine file.
        qpath = self.writer.spool_path.with_suffix(".quarantine.jsonl")
        self.assertTrue(qpath.exists(), "quarantine evidence file missing")
        self.assertIn("rt2-permanent", qpath.read_text(encoding="utf-8"))
        self.assertEqual(self.writer.metrics()["sink_permanent_quarantined_total"], 1)
        self.assertEqual(self.writer.metrics()["pending"], 0)

    def test_below_cap_permanent_error_still_defers_with_backoff(self):
        # Guard against over-parking: the first attempts keep the bounded
        # retry semantics (a schema can legitimately appear mid-migration).
        record = self._stage_and_claim("rt2-below", 0)
        exc = sqlite3.OperationalError("no such table: sessions")
        self.assertTrue(self.writer._record_sink_failure(record, exc))
        state, last_error = self.writer._read_row_state("rt2-below")
        self.assertEqual(state, "pending")
        self.assertIn("no such table", last_error)
        qpath = self.writer.spool_path.with_suffix(".quarantine.jsonl")
        self.assertFalse(qpath.exists())

    def test_transient_lock_error_is_never_quarantined(self):
        record = self._stage_and_claim("rt2-transient", 50)
        exc = sqlite3.OperationalError("database is locked")
        self.assertTrue(self.writer._record_sink_failure(record, exc))
        state, _ = self.writer._read_row_state("rt2-transient")
        self.assertEqual(state, "pending")
        qpath = self.writer.spool_path.with_suffix(".quarantine.jsonl")
        self.assertFalse(qpath.exists())


class AliasSyncContractRT3(unittest.TestCase):
    """F-04 contract: with BOTH aliases aligned (the fixed fixture pattern),
    background code_tools traffic must not steal the test DB or block init.

    This is the RC2 scenario with the a38 fixture defect corrected: the old
    fixture set only store._SESSIONS_DB, so one ct._log_session_event call
    synced the store alias back to the shared path and the migration never
    targeted the test DB (failures #7/#8: {'probe'} / no contention error).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "holder.db"
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE IF NOT EXISTS probe(x INTEGER)")
        conn.commit()
        conn.close()
        self._old_store_db = store._SESSIONS_DB
        self._old_ct_db = ct._SESSIONS_DB
        store.close_writer()
        store._SESSIONS_DB = self.db
        ct._SESSIONS_DB = self.db
        store._SESSION_DB_INITIALIZED_PATHS.discard(_norm(self.db))
        store._SESSION_INIT_FAILURES.pop(_norm(self.db), None)

    def tearDown(self):
        store.close_writer()
        store._SESSIONS_DB = self._old_store_db
        ct._SESSIONS_DB = self._old_ct_db
        store._SESSION_DB_INITIALIZED_PATHS.discard(_norm(self.db))
        store._SESSION_INIT_FAILURES.pop(_norm(self.db), None)
        try:
            self._tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_background_ct_calls_keep_an_aligned_test_db(self):
        store._init_session_db()
        conn = sqlite3.connect(str(self.db))
        conn.execute(
            "INSERT INTO sessions(session_id, session_title, project_path, "
            "mode, status, coverage, started_at) "
            "VALUES ('rt3-s','t','C:/rt3','T1','active','A-only','2026-11-26T00:00:00')"
        )
        conn.commit()
        conn.close()
        errors = []
        stop = threading.Event()

        def background():
            while not stop.is_set():
                try:
                    ct._log_session_event("rt3-s", "rt3-tool", {}, {}, 0, "ok")
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)
                    return

        worker = threading.Thread(target=background, daemon=True)
        worker.start()
        try:
            time.sleep(0.3)
            # Re-init while ct traffic is live: must still target OUR db.
            store._SESSION_DB_INITIALIZED_PATHS.discard(_norm(self.db))
            store._init_session_db()
        finally:
            stop.set()
            worker.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(_norm(store._SESSIONS_DB), _norm(self.db))
        self.assertTrue({"sessions", "write_operations"} <= _tables(self.db))
        self.assertIn("probe", _tables(self.db))


if __name__ == "__main__":
    unittest.main()
