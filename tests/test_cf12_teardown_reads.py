"""CF-12: late readers must not undo a completed sessions.db teardown."""
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import os
import sqlite3
import threading

import pytest
from orchestrator.session import store


@pytest.fixture
def db(tmp_path, monkeypatch):
    store.close_writer()
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(store, "_SESSIONS_DB", path)
    store._get_session_writer()
    yield path
    store.close_writer()


def assert_memory_read():
    with closing(store._session_read_connection()) as conn:
        assert conn.execute("PRAGMA database_list").fetchone()[2] == ""
        # No fake empty schema: a post-teardown lookup must still fail loudly.
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            conn.execute("SELECT * FROM sessions")
    assert store._TEARDOWN_COMPLETE


def test_late_read_does_not_resurrect_writer_or_pin_db(db):
    store.close_writer()
    assert store._SESSION_WRITER is None
    assert_memory_read()
    assert store._SESSION_WRITER is None
    # Windows refuses this rename while a file-backed connection pins the DB.
    renamed = db.with_suffix(".released")
    db.rename(renamed)
    renamed.rename(db)


def test_late_daemon_reader_cannot_reopen_db(db):
    store.close_writer()
    errors = []
    def late_reader():
        try:
            assert_memory_read()
        except BaseException as exc:
            errors.append(exc)
    reader = threading.Thread(target=late_reader, daemon=True)
    reader.start()
    reader.join(3)
    assert not reader.is_alive()
    assert not errors
    assert store._SESSION_WRITER is None


def test_different_db_path_resumes_real_reads(db, monkeypatch):
    store.close_writer()
    next_path = db.with_name("next.db")
    monkeypatch.setattr(store, "_SESSIONS_DB", next_path)
    conn = store._session_read_connection()
    assert Path(conn.execute("PRAGMA database_list").fetchone()[2]) == next_path
    assert store._SESSION_WRITER.db_path == next_path
    assert not store._TEARDOWN_COMPLETE


def test_equivalent_path_spelling_does_not_resume(db, monkeypatch):
    store.close_writer()
    monkeypatch.setattr(store, "_SESSIONS_DB", str(db.parent) + os.sep + "." + os.sep + db.name)
    assert_memory_read()
    assert store._SESSION_WRITER is None


def test_explicit_writer_creation_resumes_same_path(db):
    store.close_writer()
    new_writer = store._get_session_writer()
    conn = store._session_read_connection()
    assert store._SESSION_WRITER is new_writer
    assert Path(conn.execute("PRAGMA database_list").fetchone()[2]) == db
    assert not store._TEARDOWN_COMPLETE


def test_closing_writer_does_not_clear_teardown(db, monkeypatch):
    store.close_writer()
    closing_flag = threading.Event()
    closing_flag.set()
    with monkeypatch.context() as local:
        local.setattr(store, "_SESSION_WRITER", SimpleNamespace(db_path=db, _closing=closing_flag))
        assert_memory_read()


def test_new_live_writer_can_clear_teardown(db, monkeypatch):
    writer = store._SESSION_WRITER
    monkeypatch.setattr(store, "_TEARDOWN_COMPLETE", True)
    monkeypatch.setattr(store, "_TEARDOWN_DB_PATH", str(db))
    conn = store._session_read_connection()
    assert store._SESSION_WRITER is writer
    assert Path(conn.execute("PRAGMA database_list").fetchone()[2]) == db
    assert not store._TEARDOWN_COMPLETE