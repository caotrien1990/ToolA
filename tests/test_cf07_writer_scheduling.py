"""Writer contention, scheduling and retention must preserve forward progress."""
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import pytest

from orchestrator.session import store


@pytest.mark.parametrize("heartbeat", [True, False])
def test_python_progress_and_silent_stall_are_distinguished(tmp_path, heartbeat):
    writer = store.SessionWriter(tmp_path / "python.db", stall_seconds=0.1,
                                 wedge_multiplier=2, watchdog_interval=0.01)
    started = threading.Event()
    def work(conn):
        started.set()
        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            if heartbeat:
                conn.progress_heartbeat()
            time.sleep(0.01)
        return 7
    try:
        if heartbeat:
            assert writer.submit("python control", work, durable=True, wait_timeout=2) == 7
            assert writer.metrics()["stall_count"] == 0
            assert writer.metrics()["wedge_count"] == 0
        else:
            with pytest.raises(store.SessionStoreCommitPendingError):
                writer.submit("silent control", work, durable=True, wait_timeout=2)
            assert writer.metrics()["wedge_count"] >= 1
    finally:
        assert writer.close(timeout=2)


def test_queue_does_not_starve_other_principal_or_invert_its_fifo(tmp_path):
    writer = store.SessionWriter(tmp_path / "queue.db", auto_start=False, batch_window_ms=0)
    items = []
    for principal, operation, count in (("bulk-owner", "event", 90), ("control-owner", "task checkpoint", 3)):
        token = store._CALLER_AGENT_ID.set(principal)
        try:
            for _ in range(count):
                item = store._WriteItem(operation, lambda conn: None, True, 750)
                items.append(item)
                writer._queue.put(item)
        finally:
            store._CALLER_AGENT_ID.reset(token)
    batch = writer._take_batch()
    assert batch[0].principal == "control-owner"
    assert all(item in batch for item in items[-3:])
    assert [item for item in batch if item.principal == "bulk-owner"] == items[:len(batch) - 3]
    assert writer._queue.unfinished_tasks == len(items), "selection is not completion"
    assert writer.metrics()["queue_depth_by_class"]["bulk"] == len(items) - len(batch)


def test_batch_window_accepts_a_late_arrival(tmp_path):
    writer = store.SessionWriter(tmp_path / "window.db", auto_start=False, batch_window_ms=60)
    first = store._WriteItem("one", lambda conn: None, True, 750)
    second = store._WriteItem("two", lambda conn: None, True, 750)
    writer._queue.put(first)
    thread = threading.Thread(target=lambda: (time.sleep(0.015), writer._queue.put(second)))
    thread.start()
    try:
        assert writer._take_batch() == [first, second]
    finally:
        thread.join(1)


def test_aged_maintenance_eventually_runs_under_control_stream(tmp_path):
    writer = store.SessionWriter(tmp_path / "aging.db", auto_start=False, batch_window_ms=0)
    old = store._WriteItem("prune retention", lambda conn: None, True, 750)
    old.principal = "maintenance"
    old.enqueued_at -= 1
    writer._queue.put(old)
    control = store._WriteItem("task checkpoint", lambda conn: None, True, 750)
    control.principal = "root"
    writer._queue.put(control)
    assert writer._take_batch()[0] is old


def test_retention_interruption_keeps_parent_and_resumes_bounded_children(tmp_path, monkeypatch):
    db = tmp_path / "retention.db"
    store.close_writer()
    monkeypatch.setattr(store, "_SESSIONS_DB", db)
    store._init_session_db()
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO sessions(session_id, session_title, project_path, started_at) VALUES(?, 'old', 'D:/test', ?)",
                     ("old", (datetime.now() - timedelta(days=40)).isoformat()))
        conn.executemany("INSERT INTO events(session_id, tool_name) VALUES('old', ?)",
                         [(str(i),) for i in range(11)])
    original = store._run_session_write
    attempts = []
    def interrupt(operation, callback, **kwargs):
        attempts.append(operation)
        if len(attempts) == 2:
            raise store.SessionStoreContentionError("interrupt between committed chunks")
        return original(operation, callback, **kwargs)
    try:
        with monkeypatch.context() as local:
            local.setattr(store, "_run_session_write", interrupt)
            with pytest.raises(store.SessionStoreContentionError):
                store.prune_sessions_older_than(days=10, chunk_size=3)
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT count(*) FROM sessions WHERE session_id='old'").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 8
        result = store.prune_sessions_older_than(days=10, chunk_size=3)
        assert result["events"] == 8 and result["sessions"] == 1
        assert result["committed_chunks"] >= 3
        with sqlite3.connect(db) as conn:
            assert not conn.execute("SELECT 1 FROM events").fetchone()
            indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            assert {"ix_sessions_started", "ix_pending_audits_session", "ix_write_operations_session",
                    "ix_write_operations_updated", "ix_read_tokens_updated"} <= indexes
    finally:
        store.close_writer()


def test_event_warning_episode_counts_every_failure_without_flood(monkeypatch, caplog):
    monkeypatch.setattr(store, "_SESSION_EVENT_CONTENTION_COUNT", 0)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise store.SessionStoreContentionError("hold")
    monkeypatch.setattr(store, "_run_session_write", fail)
    for _ in range(200):
        with pytest.raises(store.SessionStoreContentionError):
            store._log_session_event("s", "read_file_range", {}, {}, 0, "ok")
    monkeypatch.setattr(store, "_run_session_write", lambda *args, **kwargs: 9)
    assert store._log_session_event("s", "read_file_range", {}, {}, 0, "ok") == 9
    messages = [r.message for r in caplog.records if "session event persistence" in r.message]
    assert len(calls) == 200 and len(messages) == 2
    assert "suppressed_contention_warnings=199" in messages[1]


def test_event_payload_cap_is_visible_in_stored_row(tmp_path, monkeypatch):
    store.close_writer()
    monkeypatch.setattr(store, "_SESSIONS_DB", tmp_path / "event.db")
    try:
        event_id = store._log_session_event("event-owner", "read_file_range", {},
                                            {"text": "x" * 300000}, 0, "ok")
        raw = store._session_read_connection().execute(
            "SELECT result_json FROM events WHERE id=?", (event_id,)).fetchone()[0]
        assert len(raw.encode("utf-8")) < 262144
        assert json.loads(raw)["reason"] == "SESSION_EVENT_PAYLOAD_TOO_LARGE"
    finally:
        store.close_writer()