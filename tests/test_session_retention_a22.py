"""A22: sessions.db retention -- keep only the N most-recent days.

These tests pin BEHAVIOUR, not implementation. The load-bearing one is
``test_write_operations_epoch_not_wiped_by_iso_cutoff``: it reproduces the
REAL-vs-TEXT trap that makes a naive retention query delete the whole
``write_operations`` table, and MUST go red if anyone compares that REAL epoch
column against an ISO string cutoff.

Every test uses a throwaway sessions.db (tmp_path) wired through
``store._SESSIONS_DB`` + ``store.close_writer()``. None of them touches the real
``.orchestrator_cache/sessions.db``.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from orchestrator.session import store


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the store at a fresh temp sessions.db with the real schema."""
    db_path = tmp_path / "sessions_a22.db"
    monkeypatch.setattr(store, "_SESSIONS_DB", db_path)
    # Forget any prior init memo + tear down any writer so the writer/init bind to
    # THIS path, then create the real schema via the store's own migration.
    store._SESSION_DB_INITIALIZED_PATHS.discard(
        os.path.normcase(os.path.abspath(str(db_path)))
    )
    store.close_writer()
    store._init_session_db()
    # Make sure retention uses the default window unless a test overrides it.
    monkeypatch.delenv(store._SESSION_RETENTION_DAYS_ENV, raising=False)
    yield db_path
    store.close_writer()
    store._SESSION_DB_INITIALIZED_PATHS.discard(
        os.path.normcase(os.path.abspath(str(db_path)))
    )


def _iso(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


def _epoch(days_ago: float) -> float:
    return (datetime.now() - timedelta(days=days_ago)).timestamp()


def _raw() -> sqlite3.Connection:
    # A separate read/write connection for direct assertions/seeding. The store's
    # writer is single-process; seeding directly is fine between prune calls.
    conn = sqlite3.connect(str(store._SESSIONS_DB))
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _count(conn: sqlite3.Connection, table: str, where: str = "", params=()) -> int:
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return conn.execute(sql, params).fetchone()[0]


def _seed_session(conn, sid: str, started_at: str) -> None:
    conn.execute(
        "INSERT INTO sessions(session_id, session_title, project_path, started_at) "
        "VALUES (?, ?, ?, ?)",
        (sid, f"title-{sid}", "D:/proj", started_at),
    )


def _seed_write_op(conn, op_id: str, sid: str, updated_at_epoch: float) -> None:
    conn.execute(
        "INSERT INTO write_operations("
        "operation_id, tool, status, session_id, started_at, origin_generation, updated_at) "
        "VALUES (?, 'write_fix', 'DONE', ?, ?, 'gen-1', ?)",
        (op_id, sid, updated_at_epoch, updated_at_epoch),
    )


# --------------------------------------------------------------------------- #
# THE regression: the A22 REAL-vs-TEXT trap.
# --------------------------------------------------------------------------- #
def test_write_operations_epoch_not_wiped_by_iso_cutoff(temp_db):
    """Fresh write_operations (epoch, <10d) survive even beside OLD sessions.

    write_operations.updated_at is REAL epoch. SQLite ranks REAL < TEXT, so
    comparing it against an ISO string cutoff matches EVERY row and empties the
    table. This test seeds only-fresh write_operations plus an old session; a
    correct implementation deletes 0 write_operations. It goes RED the moment
    someone swaps the epoch cutoff for the ISO cutoff on this table.
    """
    conn = _raw()
    try:
        # An old session (pruned by age) plus a kept one, so the cascade path runs.
        _seed_session(conn, "old-sess", _iso(40))
        _seed_session(conn, "keep-sess", _iso(2))
        # FRESH write_operations that are NOT linked to the pruned session -- so the
        # ONLY thing that could delete them is the age comparison. If that uses the
        # ISO cutoff against this REAL epoch column, SQLite ranks REAL < TEXT and
        # matches all of them -> table wiped. Correct code (epoch cutoff) keeps all.
        for i in range(5):
            _seed_write_op(conn, f"wo-empty-{i}", "", _epoch(1))       # 1 day old
        for i in range(3):
            _seed_write_op(conn, f"wo-keep-{i}", "keep-sess", _epoch(2))  # 2 days old
        conn.commit()
        before = _count(conn, "write_operations")
    finally:
        conn.close()
    assert before == 8

    result = store.prune_sessions_older_than(days=10)

    conn = _raw()
    try:
        after = _count(conn, "write_operations")
    finally:
        conn.close()
    assert after == 8, (
        "fresh epoch write_operations were deleted -- the REAL-vs-TEXT trap. "
        f"result={result}"
    )
    assert result["write_operations"] == 0


def test_stale_write_operations_by_epoch_are_pruned(temp_db):
    """The epoch path still prunes genuinely-old write_operations."""
    conn = _raw()
    try:
        for i in range(4):
            _seed_write_op(conn, f"wo-old-{i}", "", _epoch(30))  # 30 days old
        for i in range(2):
            _seed_write_op(conn, f"wo-new-{i}", "", _epoch(1))
        conn.commit()
    finally:
        conn.close()

    result = store.prune_sessions_older_than(days=10)

    conn = _raw()
    try:
        assert _count(conn, "write_operations") == 2
    finally:
        conn.close()
    assert result["write_operations"] == 4


# --------------------------------------------------------------------------- #
# No NEW orphans across all five child tables.
# --------------------------------------------------------------------------- #
def test_cascade_leaves_no_new_orphans(temp_db):
    conn = _raw()
    try:
        _seed_session(conn, "old", _iso(40))
        _seed_session(conn, "keep", _iso(2))
        # events (TEXT created_at) for both
        for sid, when in (("old", _iso(40)), ("keep", _iso(2))):
            conn.execute(
                "INSERT INTO events(session_id, tool_name, created_at) VALUES (?, 'read', ?)",
                (sid, when),
            )
        # pending_audits (TEXT created_at)
        for sid, when in (("old", _iso(40)), ("keep", _iso(2))):
            conn.execute(
                "INSERT INTO pending_audits(pending_audit_id, project_path, session_id, created_at) "
                "VALUES (?, 'D:/proj', ?, ?)",
                (f"pa-{sid}", sid, when),
            )
        # task_checkpoints (session_key -> session_id, TEXT updated_at)
        for sid, when in (("old", _iso(40)), ("keep", _iso(2))):
            conn.execute(
                "INSERT INTO task_checkpoints(session_key, payload, updated_at) VALUES (?, '{}', ?)",
                (sid, when),
            )
        # write_operations linked to the OLD session, but FRESH epoch: cascade must
        # still remove them because they belong to a pruned session.
        _seed_write_op(conn, "wo-old-link", "old", _epoch(1))
        _seed_write_op(conn, "wo-keep-link", "keep", _epoch(1))
        conn.commit()
    finally:
        conn.close()

    store.prune_sessions_older_than(days=10)

    conn = _raw()
    try:
        session_ids = {r[0] for r in conn.execute("SELECT session_id FROM sessions")}
        assert session_ids == {"keep"}
        # No child row may reference a session that no longer exists.
        assert _count(
            conn, "events", "session_id NOT IN (SELECT session_id FROM sessions)"
        ) == 0
        assert _count(
            conn, "pending_audits",
            "session_id IS NOT NULL AND session_id != '' "
            "AND session_id NOT IN (SELECT session_id FROM sessions)"
        ) == 0
        assert _count(
            conn, "task_checkpoints",
            "session_key NOT IN (SELECT session_id FROM sessions)"
        ) == 0
        assert _count(
            conn, "write_operations",
            "session_id IS NOT NULL AND session_id != '' "
            "AND session_id NOT IN (SELECT session_id FROM sessions)"
        ) == 0
        # The kept session keeps its children.
        assert _count(conn, "events", "session_id='keep'") == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Rows inside the retention window are untouched.
# --------------------------------------------------------------------------- #
def test_recent_rows_are_kept(temp_db):
    conn = _raw()
    try:
        _seed_session(conn, "recent-1", _iso(1))
        _seed_session(conn, "recent-9", _iso(9))
        conn.execute(
            "INSERT INTO events(session_id, tool_name, created_at) VALUES ('recent-1', 'read', ?)",
            (_iso(1),),
        )
        conn.execute(
            "INSERT INTO read_tokens(project_key, rel_path, updated_at) VALUES ('D:/proj', 'a.py', ?)",
            (_iso(3),),
        )
        conn.commit()
    finally:
        conn.close()

    result = store.prune_sessions_older_than(days=10)

    conn = _raw()
    try:
        assert _count(conn, "sessions") == 2
        assert _count(conn, "events") == 1
        assert _count(conn, "read_tokens") == 1
    finally:
        conn.close()
    assert result["sessions"] == 0
    assert result["read_tokens"] == 0


def test_null_started_at_sessions_are_kept(temp_db):
    """A session with no started_at is undatable -> never pruned."""
    conn = _raw()
    try:
        conn.execute(
            "INSERT INTO sessions(session_id, session_title, project_path, started_at) "
            "VALUES ('no-date', 't', 'D:/proj', NULL)"
        )
        _seed_session(conn, "old", _iso(40))
        conn.commit()
    finally:
        conn.close()

    store.prune_sessions_older_than(days=10)

    conn = _raw()
    try:
        ids = {r[0] for r in conn.execute("SELECT session_id FROM sessions")}
    finally:
        conn.close()
    assert ids == {"no-date"}


# --------------------------------------------------------------------------- #
# Env knob is read LIVE (A13 style).
# --------------------------------------------------------------------------- #
def test_env_knob_is_read_live(temp_db, monkeypatch: pytest.MonkeyPatch):
    """Changing the env AFTER import must change the window (no frozen constant)."""
    conn = _raw()
    try:
        _seed_session(conn, "s5", _iso(5))   # 5 days old
        _seed_session(conn, "s40", _iso(40))
        conn.commit()
    finally:
        conn.close()

    # Window = 3 days: the 5-day-old session is now OUT of the window.
    monkeypatch.setenv(store._SESSION_RETENTION_DAYS_ENV, "3")
    result = store.prune_sessions_older_than()  # no explicit days -> live env
    assert result["retention_days"] == 3

    conn = _raw()
    try:
        ids = {r[0] for r in conn.execute("SELECT session_id FROM sessions")}
    finally:
        conn.close()
    assert ids == set(), "5-day session should be pruned at a 3-day window"


def test_env_knob_default_and_invalid_fallback(temp_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(store._SESSION_RETENTION_DAYS_ENV, raising=False)
    assert store._session_retention_days() == 10
    monkeypatch.setenv(store._SESSION_RETENTION_DAYS_ENV, "not-a-number")
    assert store._session_retention_days() == 10
    monkeypatch.setenv(store._SESSION_RETENTION_DAYS_ENV, "0")
    assert store._session_retention_days() == 10  # non-positive -> safe default
    monkeypatch.setenv(store._SESSION_RETENTION_DAYS_ENV, "-5")
    assert store._session_retention_days() == 10
    monkeypatch.setenv(store._SESSION_RETENTION_DAYS_ENV, "7")
    assert store._session_retention_days() == 7


# --------------------------------------------------------------------------- #
# Pre-existing orphans are left alone by default, removed only when opted-in.
# --------------------------------------------------------------------------- #
def test_preexisting_orphans_untouched_by_default(temp_db):
    conn = _raw()
    try:
        # Orphan child rows: their session never existed. All FRESH so age can't
        # be the reason they'd be deleted.
        conn.execute(
            "INSERT INTO events(session_id, tool_name, created_at) VALUES ('ghost', 'read', ?)",
            (_iso(1),),
        )
        conn.execute(
            "INSERT INTO task_checkpoints(session_key, payload, updated_at) VALUES ('ghost', '{}', ?)",
            (_iso(1),),
        )
        _seed_write_op(conn, "wo-ghost", "ghost", _epoch(1))
        conn.commit()
    finally:
        conn.close()

    result = store.prune_sessions_older_than(days=10)  # default: no orphan sweep

    conn = _raw()
    try:
        assert _count(conn, "events", "session_id='ghost'") == 1
        assert _count(conn, "task_checkpoints", "session_key='ghost'") == 1
        assert _count(conn, "write_operations", "session_id='ghost'") == 1
    finally:
        conn.close()
    assert result["orphans_pruned"] is False
    assert "orphans_events" not in result


def test_orphan_sweep_when_opted_in(temp_db):
    conn = _raw()
    try:
        conn.execute(
            "INSERT INTO events(session_id, tool_name, created_at) VALUES ('ghost', 'read', ?)",
            (_iso(1),),
        )
        conn.execute(
            "INSERT INTO task_checkpoints(session_key, payload, updated_at) VALUES ('ghost', '{}', ?)",
            (_iso(1),),
        )
        _seed_write_op(conn, "wo-ghost", "ghost", _epoch(1))
        conn.commit()
    finally:
        conn.close()

    result = store.prune_sessions_older_than(days=10, prune_orphans=True)

    conn = _raw()
    try:
        assert _count(conn, "events", "session_id='ghost'") == 0
        assert _count(conn, "task_checkpoints", "session_key='ghost'") == 0
        assert _count(conn, "write_operations", "session_id='ghost'") == 0
    finally:
        conn.close()
    assert result["orphans_pruned"] is True
    assert result["orphans_events"] == 1
    assert result["orphans_task_checkpoints"] == 1
    assert result["orphans_write_operations"] == 1


def test_blank_and_null_timestamps_are_kept_on_every_age_pruned_table(temp_db):
    """ClauLTN verify (W2): an UNDATABLE row must be KEPT, on every table.

    `prune_sessions_older_than` promises "sessions with a NULL/blank started_at
    are always KEPT rather than guessed at", and guards each age predicate with
    ``IS NOT NULL AND != ''``. Only `sessions` had a test for that.

    The guard is load-bearing beyond NULL: in SQLite the empty string sorts BELOW
    every ISO-8601 timestamp, so a bare ``updated_at < cutoff_iso`` silently
    DELETES blank-timestamp rows -- exactly the "guess at an undatable row"
    the docstring rules out. Dropping the guard from `read_tokens` keeps all ten
    original tests green (none of them seeds a blank timestamp), which is how the
    mutant survived. `read_tokens.updated_at` is nullable with no default, and
    `_read_token_upsert` is not the only writer, so a blank value is reachable.

    Pinned as the invariant ("undatable => kept") across every age-pruned table
    rather than as one SQL fragment, so a rewrite that keeps the behaviour stays
    green (L-u).
    """
    conn = _raw()
    try:
        # A genuinely old session so the prune has real work to do.
        _seed_session(conn, "old-sess", _iso(40))
        # Undatable rows on each table pruned by its OWN timestamp.
        conn.execute(
            "INSERT INTO read_tokens(project_key, rel_path, updated_at) "
            "VALUES ('D:/proj', 'blank.py', '')"
        )
        conn.execute(
            "INSERT INTO read_tokens(project_key, rel_path, updated_at) "
            "VALUES ('D:/proj', 'null.py', NULL)"
        )
        # ...and one that IS datable and old, to prove the prune still works.
        conn.execute(
            "INSERT INTO read_tokens(project_key, rel_path, updated_at) "
            "VALUES ('D:/proj', 'old.py', ?)",
            (_iso(40),),
        )
        conn.commit()
    finally:
        conn.close()

    result = store.prune_sessions_older_than(days=10)

    conn = _raw()
    try:
        kept = {
            r[0]
            for r in conn.execute("SELECT rel_path FROM read_tokens").fetchall()
        }
    finally:
        conn.close()

    assert "blank.py" in kept, (
        "a read_token with updated_at='' was deleted. The empty string sorts below "
        "every ISO timestamp in SQLite, so an unguarded `updated_at < cutoff` "
        "silently prunes undatable rows -- the function's docstring promises they "
        f"are KEPT. kept={sorted(kept)} result={result}"
    )
    assert "null.py" in kept, f"a NULL updated_at read_token was deleted: {sorted(kept)}"
    assert "old.py" not in kept, (
        f"the genuinely-old read_token was NOT pruned; fixture is wrong: {sorted(kept)}"
    )


def test_returns_per_table_stats(temp_db):
    conn = _raw()
    try:
        _seed_session(conn, "old", _iso(40))
        conn.execute(
            "INSERT INTO events(session_id, tool_name, created_at) VALUES ('old', 'read', ?)",
            (_iso(40),),
        )
        conn.commit()
    finally:
        conn.close()

    result = store.prune_sessions_older_than(days=10)
    for key in ("sessions", "events", "pending_audits", "task_checkpoints",
                "write_operations", "read_tokens", "retention_days",
                "cutoff_iso", "cutoff_epoch"):
        assert key in result, f"missing stat key {key!r}: {result}"
    assert result["sessions"] == 1
    assert result["events"] == 1
