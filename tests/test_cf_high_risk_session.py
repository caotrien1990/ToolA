import asyncio
import json
import sqlite3

import pytest
from orchestrator.session import store, logs


def test_cf04_migration_failure_has_recovery_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(store, '_SESSIONS_DB', tmp_path / 'busy.db')
    monkeypatch.setattr(store, '_SESSION_WRITER', None)
    monkeypatch.setattr(store, '_SESSION_MIGRATION_ATTEMPTS', 1)
    monkeypatch.setattr(store, '_SESSION_DB_RETRY_BASE_MS', 0)
    calls = []
    def locked(conn):
        calls.append(1)
        raise sqlite3.OperationalError('database locked')
    monkeypatch.setattr(store, '_apply_session_schema', locked)
    for _ in range(2):
        with pytest.raises(store.SessionStoreContentionError):
            store._init_session_db()
    assert len(calls) == 1, 'failure fan-out must not repeat the DDL retry budget'


def test_cf08_native_log_never_adopts_another_owner(tmp_path, monkeypatch):
    db = tmp_path / 'sessions.db'
    conn = sqlite3.connect(db, check_same_thread=False)
    monkeypatch.setattr(logs, '_SESSIONS_DB', db)
    conn.execute('CREATE TABLE sessions(session_id, project_path, agent_id, status, started_at)')
    conn.executemany('INSERT INTO sessions VALUES(?,?,?,?,?)', [
        ('own', 'project-a', 'codo', 'active', '2020'),
        ('foreign', 'project-b', 'muse', 'active', '2030')])
    conn.commit()
    monkeypatch.setattr(store, '_session_read_connection', lambda: conn)
    monkeypatch.setattr(logs, '_log_to_file', lambda *a, **kw: None)
    monkeypatch.setattr(logs, '_auto_trim_log', lambda *a: None)
    captured = []
    monkeypatch.setattr(logs, '_log_session_event', lambda *args: (captured.append(args), 1)[1])
    token = store._CALLER_AGENT_ID.set('codo')
    try:
        result = json.loads(asyncio.run(logs.log_native([])))
        assert result['ok']
        assert captured and captured[0][0] == 'own'
        conn.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', ('ambiguous', 'project-c', 'codo', 'active', '2040'))
        result = json.loads(asyncio.run(logs.log_native([])))
        assert not result['ok']
        assert len(captured) == 1
    finally:
        store._CALLER_AGENT_ID.reset(token)
        conn.close()
