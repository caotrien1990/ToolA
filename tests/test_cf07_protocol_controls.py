"""Protocol controls for the CF-07 hardening round; never use the live DB."""
import asyncio
import json
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