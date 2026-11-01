"""Regression evidence for the 2026-10-29 claim pack; all DBs are disposable."""
import sqlite3
import threading
import time

import pytest

from orchestrator.session import store
from orchestrator.storage import spool
from orchestrator import lock_holder_trace


@pytest.mark.parametrize("reader,args", [
    ("_get_active_session", ("project",)),
    ("_read_token_get", ("project", "file.py")),
])
def test_cf01_read_contention_is_not_absence(monkeypatch, reader, args):
    def locked():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(store, "_session_read_connection", locked)
    with pytest.raises(store.SessionStoreContentionError):
        getattr(store, reader)(*args)


def test_cf03_short_budget_does_not_fail_long_budget_neighbor(tmp_path):
    db = tmp_path / "batch.db"
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE rows (v TEXT)")
    held = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
    held.execute("BEGIN IMMEDIATE")
    timer = threading.Timer(0.12, held.rollback)
    timer.start()
    writer = store.SessionWriter(db, auto_start=False)
    short = store._WriteItem("short", lambda c: c.execute("INSERT INTO rows VALUES ('short')"), True, 20)
    long = store._WriteItem("long", lambda c: c.execute("INSERT INTO rows VALUES ('long')").rowcount, True, 500)
    try:
        writer._process_batch(conn, [short, long])
        assert isinstance(short.future.exception(), store.SessionStoreContentionError)
        assert long.future.result() == 1
        assert conn.execute("SELECT v FROM rows").fetchall() == [("long",)]
    finally:
        timer.join()
        held.close()
        conn.close()


def test_cf06_operation_is_published_inside_writer_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(lock_holder_trace, "_ENABLED", True)
    writer = store.SessionWriter(tmp_path / "trace.db")
    try:
        seen = writer.submit("owner-operation", lambda c: lock_holder_trace.current_tool(), durable=True)
        assert seen == "owner-operation"
        assert lock_holder_trace.current_tool() is None
    finally:
        writer.close()


def test_cf07_progressing_batch_does_not_wedge(tmp_path):
    db = tmp_path / "progress.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE rows(v INTEGER)")
    conn.close()
    writer = store.SessionWriter(db, auto_start=False, stall_seconds=0.1,
                                 wedge_multiplier=2, watchdog_interval=0.01)
    items = []
    verdicts = []
    def work(c):
        time.sleep(0.045)
        verdicts.append(writer._stalled_reason or writer._failed_reason)
        c.execute("INSERT INTO rows VALUES (1)")
        return 1
    for i in range(10):
        item = store._WriteItem(str(i), work, True, 750)
        items.append(item)
        writer._queue.put(item)
    try:
        writer.start()
        assert [item.future.result(timeout=3) for item in items] == [1] * 10
        assert not any(verdicts)
        assert writer._failed_count == 0
        assert not writer._failed_reason
    finally:
        writer.close()


def test_cf05_append_during_drain_remains_durable(tmp_path):
    path = tmp_path / "spool.jsonl"
    assert spool.append_spool(path, {"id": "old"})
    def handler(record):
        assert spool.append_spool(path, {"id": "new"})
        return True
    outcome = spool.drain_spool(path, handler)
    assert outcome["replayed"] == 1
    assert spool.read_spool(path)[0] == [{"id": "new"}]


def test_cf05_trim_reports_actual_loss(tmp_path, caplog):
    path = tmp_path / "spool.jsonl"
    for i in range(3):
        assert spool.append_spool(path, {"id": i}, max_records=2)
    assert len(spool.read_spool(path)[0]) == 2
    assert any("drop" in r.getMessage().lower() and "1" in r.getMessage()
               for r in caplog.records)


def test_cf07_single_sql_statement_progress_does_not_wedge(tmp_path):
    writer = store.SessionWriter(tmp_path / 'sql-progress.db', stall_seconds=.1,
                                 wedge_multiplier=2, watchdog_interval=.01)
    verdicts = []
    def write(conn):
        def slow(value):
            time.sleep(.0001)
            verdicts.append(writer._stalled_reason or writer._failed_reason)
            return value
        conn.create_function('probe', 1, slow)
        return conn.execute('''WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL
                               SELECT x+1 FROM n WHERE x<3000)
                               SELECT SUM(probe(x)) FROM n''').fetchone()[0]
    try:
        assert writer.submit('long-sql', write, durable=True) == 4501500
        assert not any(verdicts)
    finally:
        writer.close()


def test_cf10_bounded_wait_does_not_cancel_a_late_commit(tmp_path):
    writer = store.SessionWriter(tmp_path / 'pending.db')
    entered, release, committed = threading.Event(), threading.Event(), threading.Event()
    def work(conn):
        entered.set()
        assert release.wait(2)
        conn.execute('CREATE TABLE late(v)')
        committed.set()
    try:
        started = time.monotonic()
        with pytest.raises(store.SessionStoreCommitPendingError):
            writer.submit('late', work, durable=True, wait_timeout=.05)
        assert time.monotonic() - started < .5
        release.set()
        assert committed.wait(1)
        assert writer.close(1)
        conn = sqlite3.connect(writer.db_path)
        try:
            assert conn.execute("SELECT name FROM sqlite_master WHERE name='late'").fetchone()
        finally:
            conn.close()
    finally:
        release.set()
        writer.close()


def test_cf18_lane_returns_permit_after_store_wait_budget(tmp_path, monkeypatch):
    import asyncio
    from orchestrator.server.executor_lanes import run_in_executor_lane
    from orchestrator.server.fair_admission import FAIR_ADMISSION, set_admission_identity, reset_admission_identity
    monkeypatch.setattr(store, '_SESSION_CALL_WAIT_SECONDS', .05)
    release = threading.Event()
    writer = store.SessionWriter(tmp_path / 'lane.db')
    def blocked(conn):
        assert release.wait(2)
    async def run():
        tokens = set_admission_identity('principal:cf18')
        try:
            with pytest.raises(store.SessionStoreCommitPendingError):
                await run_in_executor_lane('read_io', lambda: writer.submit('blocked-store', blocked, durable=True))
            assert FAIR_ADMISSION.status()['permit_count'] == 0
            assert await run_in_executor_lane('read_io', lambda: 42) == 42
        finally:
            reset_admission_identity(tokens)
    try:
        asyncio.run(run())
    finally:
        release.set()
        writer.close()


def test_cf05_cross_process_append_survives_drain(tmp_path):
    import subprocess
    import sys
    target = tmp_path / 'process-spool.jsonl'
    assert spool.append_spool(target, {'id': 'old'})
    def handler(row):
        subprocess.run([sys.executable, '-c',
            'from orchestrator.storage.spool import append_spool; import sys; assert append_spool(sys.argv[1], {"id": "new"})',
            str(target)], check=True, timeout=10)
        return True
    assert spool.drain_spool(target, handler)['replayed'] == 1
    assert spool.read_spool(target)[0] == [{'id': 'new'}]
