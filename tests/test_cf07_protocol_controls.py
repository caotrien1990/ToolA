"""Protocol controls for the CF-07 hardening round; never use the live DB."""
import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock



import code_tools as ct
import orchestrator_server as server
from orchestrator.session import store

def test_ledger_survives_a_real_short_external_lock(tmp_path, monkeypatch):
    store.close_writer()
    db = tmp_path / "ledger.db"
    monkeypatch.setattr(store, "_SESSIONS_DB", db)
    monkeypatch.setattr(ct, "_SESSIONS_DB", db)
    store._init_session_db()
    locked = threading.Event()
    def holder():
        with sqlite3.connect(db) as conn:
            conn.execute("BEGIN IMMEDIATE")
            locked.set()
            time.sleep(0.4)
    thread = threading.Thread(target=holder)
    thread.start()
    assert locked.wait(2)
    try:
        assert server._write_ledger_db_upsert_once({
            "operation_id": "budget", "tool": "write_fix", "status": "in_flight",
            "started_at": time.time(), "origin_generation": "test", "updated_at": time.time(),
        }, db)
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT count(*) FROM write_operations WHERE operation_id='budget'").fetchone()[0] == 1
    finally:
        thread.join(2)
        store.close_writer()


def test_health_exposes_writer_without_waiting_for_its_locks(tmp_path, monkeypatch):
    writer = store.SessionWriter(tmp_path / "metrics.db", auto_start=False)
    monkeypatch.setattr(store, "_SESSION_WRITER", writer)
    monkeypatch.setattr(server, "_durable_metrics_for_health", AsyncMock(return_value=({}, None)))
    monkeypatch.setattr(server, "_controller_health_for_health", AsyncMock(return_value={}))
    for lock in (writer._queue.mutex, writer._metrics_lock):
        with lock:
            before = time.monotonic()
            response = asyncio.run(server.handle_health(SimpleNamespace(query={}, headers={})))
            assert time.monotonic() - before < 1
            assert json.loads(response.text)["session_writer"]["metrics_stale"] is True
    assert not writer.is_alive


def test_boot_maintenance_requires_continuously_healthy_window(monkeypatch):
    monkeypatch.setattr(server, "SERVER_READY", True)
    monkeypatch.setattr(server, "SERVER_STOPPING", False)
    monkeypatch.setattr(server, "SERVER_STARTUP_DEGRADED", False)
    monkeypatch.setattr(store, "_SESSION_WRITER", SimpleNamespace(metrics=lambda: {}))
    original_sleep = asyncio.sleep
    async def exercise():
        task = asyncio.create_task(server._wait_session_maintenance_window(0.3))
        await original_sleep(0.1)
        server.SERVER_STARTUP_DEGRADED = True
        await original_sleep(0.2)
        assert not task.done()
        server.SERVER_STARTUP_DEGRADED = False
        await original_sleep(0.2)
        assert not task.done()
        assert await asyncio.wait_for(task, 1)
    asyncio.run(exercise())


def test_pending_checkpoint_http_does_not_advertise_resubmit():
    exc = store._checkpoint_storage_error(store.SessionStoreCommitPendingError("accepted"), "key")
    response = server._task_checkpoint_storage_response(exc, {})
    body = json.loads(response.text)
    assert response.status == 503
    assert body["error_name"] == "TASK_CHECKPOINT_COMMIT_PENDING"
    assert body["commit_state"] == "unknown" and body["safe_to_retry"] is False


def test_wal_checkpoint_runs_after_commit_when_time_threshold_expires(tmp_path):
    writer = store.SessionWriter(tmp_path / "wal.db", auto_start=False)
    conn = writer._open_connection()
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        writer._last_checkpoint_at -= 31
        item = store._WriteItem("test commit", lambda conn: 9, True, 750)
        assert writer._process_batch(conn, [item])
        assert item.future.result() == 9
        assert statements.index("COMMIT") < statements.index("PRAGMA wal_checkpoint(PASSIVE)")
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# E1 (checkpoint-contention Stage 2): the PASSIVE checkpoint result row must
# be READ. A reader-pinned WAL (busy=1) must surface as ONE latched warning,
# and recovery must surface too -- pre-fix the row was discarded, so a pinned
# WAL was indistinguishable from a drained one.
# --------------------------------------------------------------------------- #
def _insert_session_item(sid):
    def fn(conn):
        conn.execute(
            "INSERT INTO sessions(session_id, session_title, project_path, started_at) "
            "VALUES (?, 't', 'D:/proj', '2026-01-01T00:00:00')",
            (sid,),
        )
        return 9
    return store._WriteItem(f"insert {sid}", fn, True, 750)


def _checkpoint_warnings(caplog, needle):
    return [r for r in caplog.records if needle in r.getMessage()]


def test_wal_checkpoint_busy_is_latched_and_logged(tmp_path, caplog, monkeypatch):
    db_file = tmp_path / "wal_busy.db"
    monkeypatch.setattr(store, "_SESSIONS_DB", db_file)
    store._SESSION_DB_INITIALIZED_PATHS.discard(
        os.path.normcase(os.path.abspath(str(db_file)))
    )
    store._init_session_db()
    writer = store.SessionWriter(db_file, auto_start=False)
    conn = writer._open_connection()
    reader = sqlite3.connect(str(db_file))
    try:
        item = _insert_session_item("s1")
        assert writer._process_batch(conn, [item])
        assert item.future.result() == 9
        # Pin the WAL: the reader holds a read snapshot at the current frames.
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM sessions").fetchone()
        # Commit more frames beyond the reader's snapshot.
        item = _insert_session_item("s2")
        assert writer._process_batch(conn, [item])
        assert item.future.result() == 9
        writer._last_checkpoint_at -= 31  # force the 30 s gate due
        with caplog.at_level(logging.WARNING, logger="orchestrator.session.store"):
            item = _insert_session_item("s3")
            assert writer._process_batch(conn, [item])
        warnings = _checkpoint_warnings(caplog, "wal_checkpoint incomplete")
        assert len(warnings) == 1, caplog.text
        # A reader pin shows up in the frame counts, not the busy flag:
        # measured live, the pinned PASSIVE row is (0, 6, 3) -- busy=0 with
        # checkpointed < log. Assert the warning carries that evidence.
        m = re.search(r"log=(\d+) checkpointed=(\d+)", warnings[0].getMessage())
        assert m, warnings[0].getMessage()
        assert int(m.group(2)) < int(m.group(1)), warnings[0].getMessage()
        assert writer._checkpoint_busy_latched is True
        # Still pinned: another due attempt must NOT warn again (latch holds).
        writer._last_checkpoint_at -= 31
        with caplog.at_level(logging.WARNING, logger="orchestrator.session.store"):
            item = _insert_session_item("s4")
            assert writer._process_batch(conn, [item])
        warnings = _checkpoint_warnings(caplog, "wal_checkpoint incomplete")
        assert len(warnings) == 1, caplog.text
        # Release the reader: the next due checkpoint completes -> recovery.
        reader.execute("COMMIT")
        writer._last_checkpoint_at -= 31
        with caplog.at_level(logging.WARNING, logger="orchestrator.session.store"):
            item = _insert_session_item("s5")
            assert writer._process_batch(conn, [item])
        recovered = _checkpoint_warnings(caplog, "wal_checkpoint recovered")
        assert len(recovered) == 1, caplog.text
        assert writer._checkpoint_busy_latched is False
    finally:
        reader.close()
        conn.close()


def test_wal_checkpoint_attempt_spacing_and_timestamp_semantics(tmp_path):
    # Regression pin (PA-1): the timestamp keeps ATTEMPT semantics in every
    # branch -- a busy attempt must not be discounted (a now-25.0 backoff
    # would leave _last_checkpoint_at in the past and re-arm the size gate),
    # and an immediate second batch makes no new attempt (no per-batch storm).
    writer = store.SessionWriter(tmp_path / "wal_spacing.db", auto_start=False)
    conn = writer._open_connection()
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        writer._last_checkpoint_at -= 31
        before = time.monotonic()
        item = store._WriteItem("spacing 1", lambda conn: 9, True, 750)
        assert writer._process_batch(conn, [item])
        attempts = [s for s in statements if "wal_checkpoint(PASSIVE)" in s]
        assert len(attempts) == 1
        assert writer._last_checkpoint_at >= before
        item = store._WriteItem("spacing 2", lambda conn: 9, True, 750)
        assert writer._process_batch(conn, [item])
        attempts = [s for s in statements if "wal_checkpoint(PASSIVE)" in s]
        assert len(attempts) == 1
    finally:
        conn.close()
