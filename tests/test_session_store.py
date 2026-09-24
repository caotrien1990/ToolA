from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from orchestrator.session import store


@pytest.fixture
def session_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store.close_writer()
    db_path = tmp_path / "sessions.db"
    monkeypatch.setattr(store, "_SESSIONS_DB", db_path)
    store._init_session_db()
    yield db_path
    store.close_writer()


def test_session_database_uses_wal_mode(session_db: Path) -> None:
    # WHY: WAL keeps readers from blocking the serialized session_db writer lane.
    with sqlite3.connect(session_db) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_fresh_database_persists_nullable_agent_id(session_db: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    session_id = store._auto_open_session(str(project), {})
    evidence = store._get_active_session(str(project))

    assert session_id is not None
    assert evidence == {
        "session_id": session_id,
        "session_title": evidence["session_title"],
        "project_path": str(project.resolve()),
        "mode": "T1",
        "status": "active",
        "coverage": "A-only",
        "started_at": evidence["started_at"],
        "ended_at": None,
        "agent_id": None,
    }
    with sqlite3.connect(session_db) as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(sessions)")}
        persisted = conn.execute(
            "SELECT agent_id FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
    assert columns["agent_id"][3] == 0
    assert persisted == (None,)


def test_init_migrates_legacy_database_and_preserves_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                session_title TEXT NOT NULL,
                project_path TEXT,
                mode TEXT DEFAULT 'T1',
                status TEXT DEFAULT 'active',
                coverage TEXT DEFAULT 'A-only',
                started_at TEXT,
                ended_at TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?)",
            ("legacy-session", "Legacy", "C:/legacy", "T1", "active", "A-only", "2026-07-17T10:00:00", None),
        )
    monkeypatch.setattr(store, "_SESSIONS_DB", db_path)

    store._init_session_db()
    store._init_session_db()

    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(sessions)")]
        row = conn.execute(
            "SELECT session_id, session_title, project_path, agent_id FROM sessions"
        ).fetchone()
    assert columns.count("agent_id") == 1
    assert row == ("legacy-session", "Legacy", "C:/legacy", None)


def test_init_migrates_legacy_events_with_result_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "legacy-events.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO events VALUES (?, ?)", (1, "legacy-session"))
    monkeypatch.setattr(store, "_SESSIONS_DB", db_path)

    store._init_session_db()
    store._init_session_db()

    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(events)")}
        legacy = conn.execute(
            "SELECT session_id, result_json, event_kind, resolves_event_id FROM events WHERE id=1"
        ).fetchone()
    resolution_columns = {
        "event_kind", "resolves_event_id", "resolution_kind", "error_owner",
        "error_name", "correlation_type", "correlation_key", "evidence_json",
    }
    assert all(columns.count(name) == 1 for name in resolution_columns)
    assert {"ix_events_resolution_target", "ix_events_error_scope", "ix_events_correlation"} <= indexes
    assert legacy == ("legacy-session", None, None, None)


def test_same_project_and_agent_resumes_same_session(
    session_db: Path, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    first = store._auto_open_session(str(project), {"agent_id": "agent-a"})
    repeated = store._auto_open_session(str(project / "."), {}, agent_id="agent-a")
    evidence = store._get_active_session(str(project), agent_id="agent-a")

    assert repeated == first
    assert evidence["session_id"] == first
    assert evidence["agent_id"] == "agent-a"


def test_different_agents_can_have_distinct_sessions(
    session_db: Path, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    first = store._auto_open_session(str(project), {}, agent_id="agent-a")
    second = store._auto_open_session(str(project), {}, agent_id="agent-b")

    assert first != second
    assert store._get_active_session_id(str(project), agent_id="agent-a") == first
    assert store._get_active_session_id(str(project), agent_id="agent-b") == second


def test_an_unscoped_lookup_never_adopts_another_agents_session(
    session_db: Path, tmp_path: Path
) -> None:
    """#17/#19: lookup and open answer "which session is mine" with ONE rule.

    CASE-1909-H2 made an anonymous OPEN resume only an anonymous session, but the
    lookup kept the opposite default: omit agent_id and it returned the newest
    active session of ANY agent. write_fix, copy_file and mission pinning all look
    up without an agent_id, so one CA's work was attributed to another CA's session.
    An omitted agent_id now means the caller the dispatch runs as, and only a
    caller with no identity at all is anonymous.
    """
    project = tmp_path / "project"
    project.mkdir()
    theirs = store._auto_open_session(str(project), {}, agent_id="agent-a")

    assert store._get_active_session(str(project)) is None
    assert store._get_active_session_id(str(project)) is None

    mine = store._auto_open_session(str(project), {})
    assert mine not in (None, theirs)
    assert store._get_active_session_id(str(project)) == mine
    assert store._get_active_session_id(str(project), agent_id="agent-a") == theirs

    token = store._CALLER_AGENT_ID.set("agent-a")
    try:
        assert store._get_active_session_id(str(project)) == theirs
        assert store._auto_open_session(str(project), {}) == theirs
    finally:
        store._CALLER_AGENT_ID.reset(token)
    assert store._get_active_session_id(str(project)) == mine


def test_concurrent_same_scope_auto_open_returns_one_session(
    session_db: Path, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    worker_count = 8
    barrier = threading.Barrier(worker_count + 1)
    results: list[str | None] = []
    errors: list[BaseException] = []

    def open_scoped() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(store._auto_open_session(str(project), {}, agent_id="agent-a"))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=open_scoped) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == worker_count
    assert results[0] is not None
    assert len(set(results)) == 1
    with sqlite3.connect(session_db) as conn:
        rows = conn.execute(
            "SELECT session_id FROM sessions "
            "WHERE project_path=? AND agent_id=? AND status='active'",
            (str(project.resolve()), "agent-a"),
        ).fetchall()
    assert rows == [(results[0],)]


def test_concurrent_auto_open_keeps_different_agents_isolated(
    session_db: Path, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    barrier = threading.Barrier(3)
    results: dict[str, str | None] = {}

    def open_scoped(agent_id: str) -> None:
        barrier.wait(timeout=5)
        results[agent_id] = store._auto_open_session(
            str(project), {}, agent_id=agent_id
        )

    threads = [
        threading.Thread(target=open_scoped, args=(agent_id,))
        for agent_id in ("agent-a", "agent-b")
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert results["agent-a"] is not None
    assert results["agent-b"] is not None
    assert results["agent-a"] != results["agent-b"]
    with sqlite3.connect(session_db) as conn:
        rows = conn.execute(
            "SELECT agent_id, COUNT(*) FROM sessions WHERE status='active' "
            "GROUP BY agent_id ORDER BY agent_id"
        ).fetchall()
    assert rows == [("agent-a", 1), ("agent-b", 1)]


def test_init_demotes_duplicate_active_scopes_before_unique_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "duplicates.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                session_title TEXT NOT NULL,
                project_path TEXT,
                mode TEXT DEFAULT 'T1',
                status TEXT DEFAULT 'active',
                coverage TEXT DEFAULT 'A-only',
                started_at TEXT,
                ended_at TEXT,
                agent_id TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("older", "Older", "C:/repo", "T1", "active", "A-only", "2026-07-17T09:00:00", None, "agent-a"),
                ("winner", "Winner", "C:/repo", "T1", "active", "A-only", "2026-07-17T10:00:00", None, "agent-a"),
                ("null-one", "Null one", "C:/repo", "T1", "active", "A-only", "2026-07-17T08:00:00", None, None),
                ("null-two", "Null two", "C:/repo", "T1", "active", "A-only", "2026-07-17T11:00:00", None, None),
            ],
        )
    monkeypatch.setattr(store, "_SESSIONS_DB", db_path)

    store._init_session_db()
    store._init_session_db()

    with sqlite3.connect(db_path) as conn:
        scoped = conn.execute(
            "SELECT session_id, status, ended_at FROM sessions "
            "WHERE agent_id='agent-a' ORDER BY session_id"
        ).fetchall()
        null_rows = conn.execute(
            "SELECT session_id, status FROM sessions WHERE agent_id IS NULL ORDER BY session_id"
        ).fetchall()
        index_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='uq_sessions_active_project_agent'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
            ("null-three", "Null three", "C:/repo", "T1", "active", "A-only", "2026-07-17T12:00:00", None, None),
        )

    assert scoped[0][0:2] == ("older", "closed")
    assert scoped[0][2] is not None
    assert scoped[1] == ("winner", "active", None)
    assert null_rows == [("null-one", "active"), ("null-two", "active")]
    assert "UNIQUE" in index_sql.upper()
    assert "STATUS='ACTIVE'" in index_sql.upper().replace(" ", "")
    assert "AGENT_ID IS NOT NULL" in index_sql.upper()


def _log_test_event(session_id: str, audit_id: str = "") -> int | None:
    return store._log_session_event(
        session_id,
        "read_file_range",
        {"file_path": "example.py"},
        {},
        1,
        "ok",
        audit_id=audit_id,
    )


def test_session_event_persists_result_json_in_both_insert_paths(session_db: Path) -> None:
    plain = _log_test_event("session-result-plain")
    durable = _log_test_event("session-result-durable", "audit-result")

    with sqlite3.connect(session_db) as conn:
        rows = conn.execute(
            "SELECT id, result_json FROM events WHERE id IN (?, ?) ORDER BY id",
            (plain, durable),
        ).fetchall()
    assert [json.loads(row[1]) for row in rows] == [{}, {}]


def test_session_event_persistence_failure_logs_loud(
    session_db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with sqlite3.connect(session_db) as conn:
        conn.execute("DROP TABLE events")

    with caplog.at_level("ERROR", logger="orchestrator.session.store"):
        event_id = _log_test_event("session-broken")

    assert event_id is None
    assert "session event persistence failed" in caplog.text


def test_session_event_audit_id_is_idempotent_sequential(session_db: Path) -> None:
    first = _log_test_event("session-sequential", "audit-sequential")
    duplicate = _log_test_event("session-sequential", "audit-sequential")

    assert first is not None
    assert duplicate == first
    with sqlite3.connect(session_db) as conn:
        rows = conn.execute(
            "SELECT id, audit_id FROM events WHERE audit_id=?", ("audit-sequential",)
        ).fetchall()
    assert rows == [(first, "audit-sequential")]


def test_session_event_audit_id_is_idempotent_under_50_concurrent_calls(
    session_db: Path,
) -> None:
    worker_count = 50
    barrier = threading.Barrier(worker_count + 1)
    results: list[int | None] = []
    errors: list[BaseException] = []

    def emit() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(_log_test_event("session-concurrent", "audit-concurrent"))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=emit) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == worker_count
    assert results[0] is not None
    assert len(set(results)) == 1
    with sqlite3.connect(session_db) as conn:
        rows = conn.execute(
            "SELECT id, audit_id FROM events WHERE audit_id=?", ("audit-concurrent",)
        ).fetchall()
    assert rows == [(results[0], "audit-concurrent")]


def test_session_event_distinct_audit_ids_create_distinct_events(session_db: Path) -> None:
    first = _log_test_event("session-distinct", "audit-distinct-a")
    second = _log_test_event("session-distinct", "audit-distinct-b")

    assert first is not None
    assert second is not None
    assert first != second
    with sqlite3.connect(session_db) as conn:
        rows = conn.execute(
            "SELECT audit_id FROM events WHERE audit_id LIKE 'audit-distinct-%' "
            "ORDER BY audit_id"
        ).fetchall()
    assert rows == [("audit-distinct-a",), ("audit-distinct-b",)]


def test_code_tools_wrapper_forwards_session_event_audit_id(
    session_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import code_tools

    monkeypatch.setattr(code_tools, "_SESSIONS_DB", session_db)
    first = code_tools._log_session_event(
        "session-wrapper", "read_file_range", {}, {}, 1, "ok", audit_id="audit-wrapper"
    )
    duplicate = code_tools._log_session_event(
        "session-wrapper", "read_file_range", {}, {}, 1, "ok", audit_id="audit-wrapper"
    )

    assert first is not None
    assert duplicate == first
    with sqlite3.connect(session_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE audit_id=?", ("audit-wrapper",)
        ).fetchone() == (1,)


def test_record_error_resolution_is_append_only_and_exact(session_db: Path) -> None:
    error_id = store._log_session_event(
        "session-resolution", "write_fix", {"file_path": "a.py"},
        {"event_kind": "error", "error_owner": "backend", "error_name": "WRITE_FAILED",
         "correlation_type": "file", "correlation_key": "a.py"},
        1, "error", "write failed",
    )
    resolution_id = store._record_error_resolution(
        "session-resolution", error_id, "recovered", evidence={"test": "passed"}
    )
    with sqlite3.connect(session_db) as conn:
        source = conn.execute(
            "SELECT status, event_kind, error_name FROM events WHERE id=?", (error_id,)
        ).fetchone()
        resolution = conn.execute(
            """SELECT event_kind, resolves_event_id, resolution_kind, error_owner,
                      error_name, correlation_type, correlation_key, evidence_json
               FROM events WHERE id=?""", (resolution_id,),
        ).fetchone()
    assert source == ("error", "error", "WRITE_FAILED")
    assert resolution[:7] == (
        "resolution", error_id, "recovered", "backend", "WRITE_FAILED", "file", "a.py"
    )
    assert json.loads(resolution[7]) == {"test": "passed"}


def test_record_error_resolution_supports_reopen_and_expected_failure(session_db: Path) -> None:
    error_id = store._log_session_event(
        "session-transitions", "validate_file", {},
        {"event_kind": "error", "error_owner": "caller"}, 1, "error", "invalid input"
    )
    first = store._record_error_resolution("session-transitions", error_id, "expected_failure")
    second = store._record_error_resolution("session-transitions", error_id, "reopened")
    assert second > first > error_id
    with sqlite3.connect(session_db) as conn:
        assert conn.execute(
            "SELECT resolution_kind FROM events WHERE resolves_event_id=? ORDER BY id",
            (error_id,),
        ).fetchall() == [("expected_failure",), ("reopened",)]


def test_record_error_resolution_rejects_invalid_and_cross_scope_references(
    session_db: Path,
) -> None:
    ok_id = _log_test_event("session-a")
    error_id = store._log_session_event(
        "session-a", "write_fix", {}, {"event_kind": "error"}, 1, "error", "failed"
    )
    with pytest.raises(ValueError, match="resolution_kind"):
        store._record_error_resolution("session-a", error_id, "guessed")
    with pytest.raises(ValueError, match="error event not found"):
        store._record_error_resolution("session-a", 999999, "recovered")
    with pytest.raises(ValueError, match="must be an error event"):
        store._record_error_resolution("session-a", ok_id, "recovered")
    with pytest.raises(ValueError, match="different session"):
        store._record_error_resolution("session-b", error_id, "recovered")
    with sqlite3.connect(session_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_kind='resolution'"
        ).fetchone() == (0,)


def test_session_event_lock_wait_is_bounded_and_propagates_contention(
    session_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A20/A33: a bounded SQLite lock-wait that still cannot commit is INFRASTRUCTURE
    # failure. It must surface as SessionStoreContentionError, never be swallowed to
    # a null event_id -- a null id silently corrupts every downstream audit reader.
    blocker = sqlite3.connect(session_db, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    original = store._run_session_write

    def short_budget(operation, write_fn, **_kwargs):
        return original(operation, write_fn, busy_timeout_ms=60)

    monkeypatch.setattr(store, "_run_session_write", short_budget)
    started = time.monotonic()
    with pytest.raises(store.SessionStoreContentionError, match="busy budget"):
        _log_test_event("session-locked", "audit-locked")
    elapsed = time.monotonic() - started
    blocker.rollback()
    blocker.close()

    recovered = _log_test_event("session-locked", "audit-recovered")
    with sqlite3.connect(session_db) as conn:
        rows = conn.execute("SELECT audit_id FROM events ORDER BY id").fetchall()

    assert elapsed < 1.0
    assert recovered is not None
    assert rows == [("audit-recovered",)]


def test_log_session_event_propagates_contention_instead_of_returning_none(
    session_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A20/A33 property (deterministic, no disk load): the ONE failure mode that
    # must never collapse to a null event_id is store contention. A null id here
    # silently poisoned audit_reconciliation_report ({None,2,4,5} for {1,2,4,5}).
    def boom(*_args, **_kwargs):
        raise store.SessionStoreContentionError("simulated writer contention")

    monkeypatch.setattr(store, "_run_session_write", boom)
    with pytest.raises(store.SessionStoreContentionError, match="simulated writer contention"):
        _log_test_event("session-contended", "audit-contended")


def test_log_session_event_still_returns_none_on_genuine_persistence_bug(
    session_db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The None path is reserved for a real persistence defect (e.g. a dropped
    # table), which must stay non-fatal and loud -- NOT for infrastructure
    # contention. This is the boundary that keeps the two failure modes distinct.
    with sqlite3.connect(session_db) as conn:
        conn.execute("DROP TABLE events")
    with caplog.at_level("ERROR", logger="orchestrator.session.store"):
        event_id = _log_test_event("session-nodefect")
    assert event_id is None
    assert "session event persistence failed" in caplog.text


def test_session_write_rolls_back_whole_callback_on_failure(session_db: Path) -> None:
    def insert_then_fail(conn):
        conn.execute(
            "INSERT INTO events (session_id, tool_name) VALUES (?, ?)",
            ("session-partial", "write_transaction"),
        )
        raise RuntimeError("forced failure after insert")

    with pytest.raises(RuntimeError, match="forced failure"):
        store._run_session_write("rollback regression", insert_then_fail)

    with sqlite3.connect(session_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id='session-partial'"
        ).fetchone()[0]
    assert count == 0


def test_task_checkpoint_contention_is_bounded_and_preserves_previous_value(
    session_db: Path,
) -> None:
    assert store._task_checkpoint_save("checkpoint-locked", {"epoch": 1})
    blocker = sqlite3.connect(session_db, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()

    with pytest.raises(store.TaskCheckpointStorageError) as raised:
        store._task_checkpoint_update(
            "checkpoint-locked",
            lambda previous: ({"epoch": previous["epoch"] + 1}, "updated"),
            busy_timeout_ms=60,
        )
    elapsed = time.monotonic() - started
    blocker.rollback()
    blocker.close()

    assert raised.value.error_name == "TASK_CHECKPOINT_CONTENTION"
    assert elapsed < 1.0
    assert store._task_checkpoint_load("checkpoint-locked") == {"epoch": 1}
