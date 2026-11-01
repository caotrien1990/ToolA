import asyncio
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
import orchestrator_server as server
from orchestrator.session.store import SessionStoreContentionError
from orchestrator.storage.spool import read_spool


def test_cf01_ledger_read_failure_is_not_absence(monkeypatch):
    def locked(*a, **kw):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(server.sqlite3, 'connect', locked)
    with pytest.raises(SessionStoreContentionError):
        server._write_ledger_db_get('test')


def test_cf02_begin_requires_durable_record(monkeypatch):
    monkeypatch.setattr(server, '_write_ledger_db_get', lambda **kw: None)
    monkeypatch.setattr(server, '_write_ledger_db_upsert', lambda rec: False)
    monkeypatch.setattr(server, '_WRITE_LEDGER', {})
    with pytest.raises(SessionStoreContentionError):
        server._write_ledger_begin('write_fix', {'project_path': 'D:/cf02', 'file_path': 'file.py'}, 'session')
    assert not server._WRITE_LEDGER


def test_cf02_terminal_failure_has_durable_recovery_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(server.ct, '_SESSIONS_DB', tmp_path / 'sessions.db')
    monkeypatch.setattr(server, '_write_ledger_db_upsert', lambda rec: False)
    monkeypatch.setattr(server, '_WRITE_LEDGER', {'op': {'operation_id': 'op', 'status': 'in_flight', 'started_at': time.time(), 'attempts': 1}})
    # Stop automatic worker in this test so recovery evidence can be inspected.
    monkeypatch.setattr(server, '_write_ledger_schedule_recovery', lambda: None, raising=False)
    server._write_ledger_finish('op', {'ok': True}, 'ok')
    rows, _ = read_spool(tmp_path / 'sessions_write_ledger_spool.jsonl')
    assert rows and rows[0]['status'] == 'committed'
    assert server._WRITE_LEDGER['op']['persistence'] == 'spooled'


def test_cf14_read_does_not_hold_begin_finish_lock(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        release.wait(2)
    monkeypatch.setattr(server, '_write_ledger_db_get', slow)
    async def run():
        lookup = asyncio.create_task(server._write_ledger_db_get_async('op'))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.wait_for(server._write_ledger_async_lock().acquire(), .1)
            server._write_ledger_async_lock().release()
        finally:
            release.set()
            await lookup
    asyncio.run(run())


def test_cf19_exhausted_scan_is_explicit_not_absence(monkeypatch):
    class Cursor:
        def __iter__(self):
            for i in range(10000):
                yield (i,)
        def close(self):
            pass
        def fetchall(self):
            return []
    class Connection:
        def execute(self, *args):
            return Cursor()
        def set_progress_handler(self, *args):
            pass
    parsed = []
    def row(value):
        parsed.append(value)
        return {'operation_id': str(value[0]), 'project_path': 'D:/other', 'file_path': 'f.py'}
    monkeypatch.setattr(server, '_write_ledger_row_to_rec', row)
    with pytest.raises(SessionStoreContentionError):
        server._write_ledger_db_target_rows(Connection(), ('D:/wanted', 'f.py'), 20)
    assert len(parsed) <= 1000


def test_cf01_http_storage_failure_does_not_suggest_resend(monkeypatch):
    async def locked(**kw):
        raise SessionStoreContentionError('locked')
    monkeypatch.setattr(server, '_write_ledger_db_get_async', locked)
    monkeypatch.setattr(server, '_WRITE_LEDGER', {})
    result = asyncio.run(server.handle_write_status(SimpleNamespace(headers={}, query={'operation_id': 'op'})))
    body = json.loads(result.text)
    assert result.status == 503 and body['safe_to_retry'] is False
    assert body['commit_state'] == 'unknown'


def test_cf02_restart_with_parked_terminal_blocks_blind_resend(tmp_path, monkeypatch):
    from orchestrator.storage.spool import append_spool
    from orchestrator.session.store import SessionStoreCommitPendingError
    monkeypatch.setattr(server.ct, '_SESSIONS_DB', tmp_path / 'sessions.db')
    monkeypatch.setattr(server, '_WRITE_LEDGER', {})
    monkeypatch.setattr(server, '_write_ledger_schedule_recovery', lambda: None)
    assert append_spool(server._write_ledger_spool_path(), {'operation_id': 'old', 'status': 'committed'})
    with pytest.raises(SessionStoreCommitPendingError):
        server._write_ledger_begin('write_fix', {'project_path': str(tmp_path), 'file_path': 'x.py'})


def test_cf02_write_status_cannot_trust_db_while_terminal_is_parked(tmp_path, monkeypatch):
    from orchestrator.storage.spool import append_spool
    monkeypatch.setattr(server.ct, '_SESSIONS_DB', tmp_path / 'sessions.db')
    monkeypatch.setattr(server, '_write_ledger_schedule_recovery', lambda: None)
    monkeypatch.setattr(server, '_WRITE_LEDGER', {})
    assert append_spool(server._write_ledger_spool_path(), {'operation_id': 'op', 'status': 'committed'})
    result = asyncio.run(server.handle_write_status(SimpleNamespace(headers={}, query={'operation_id': 'op'})))
    payload = json.loads(result.text)
    assert result.status == 503 and payload['commit_state'] == 'unknown'
    assert payload['safe_to_retry'] is False


def test_cf02_memory_only_gap_retains_retry_owner(tmp_path, monkeypatch):
    from orchestrator.storage import spool
    monkeypatch.setattr(server.ct, '_SESSIONS_DB', tmp_path / 'sessions.db')
    monkeypatch.setattr(server, '_write_ledger_db_upsert', lambda rec: False)
    monkeypatch.setattr(spool, 'append_spool', lambda *a, **kw: False)
    monkeypatch.setattr(server, '_WRITE_LEDGER_MEMORY_GAPS', {})
    scheduled = []
    monkeypatch.setattr(server, '_write_ledger_schedule_recovery', lambda: scheduled.append(True))
    rec = {'operation_id': 'gap', 'status': 'committed'}
    server._write_ledger_persist_terminal(rec)
    assert rec['persistence'] == 'memory_only'
    assert server._WRITE_LEDGER_MEMORY_GAPS[(str(server.ct._SESSIONS_DB), 'gap')] is rec
    assert scheduled
