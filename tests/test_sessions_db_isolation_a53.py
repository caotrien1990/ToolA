"""A53: the test run must never write to the PRODUCTION sessions.db.

`orchestrator/session/store.py` resolves `_SESSIONS_DB` once, at import, from
``ORCHESTRATOR_SESSIONS_DB`` with the real ``.orchestrator_cache/sessions.db``
as its fallback. So a test that reaches the session layer without redirecting
the path writes live rows into the database the running daemon owns.

Measured on 2026-08-17 before the fix: a plain ``pytest tests/`` left permanent
``sess-test``, ``cross-process`` and ``agent-a-session`` rows in production, and
26 of the 50 test files touching the session layer had no redirect. Those stray
writes are also what makes the A22 retention sweep appear to under-delete: they
are session-less child rows, i.e. orphans, created continuously by CI.

The guard lives in the repo-root ``conftest.py`` (``pytest_configure`` runs
before test modules import, which is the only window where assigning the env var
still changes what ``store`` computes).

These tests pin the INVARIANT -- "the active DB is not the production file, and
nothing this process writes lands there" -- not the mechanism. A rewrite that
isolates some other way (a session-scoped fixture, a different env var, an
injected path) stays green (L-u). What must go red is the isolation being lost.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import code_tools
from orchestrator.session import store


def _production_db() -> Path:
    """The real sessions.db, resolved the same way store.py resolves its default.

    Deliberately recomputed from the repo layout rather than read from
    ``store._SESSIONS_DB`` -- that global is exactly what the fix moves, so
    reading it would make the assertions vacuous.
    """
    repo_root = Path(store.__file__).resolve().parents[2]
    return repo_root / ".orchestrator_cache" / "sessions.db"


def _same_file(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(
        os.path.abspath(str(b))
    )


def test_active_sessions_db_is_not_the_production_file():
    prod = _production_db()
    assert not _same_file(Path(store._SESSIONS_DB), prod), (
        "store._SESSIONS_DB points at the PRODUCTION sessions.db "
        f"({prod}). Every test that logs a session event is now writing live "
        "rows into the database the daemon owns. The repo-root conftest is "
        "supposed to redirect ORCHESTRATOR_SESSIONS_DB before store imports."
    )
    assert not _same_file(Path(code_tools._SESSIONS_DB), prod), (
        "code_tools._SESSIONS_DB points at production. Redirecting the store "
        "alone is not enough: every entry point calls "
        "code_tools._sync_session_db_alias(), which copies the code_tools value "
        "back over the store's global."
    )


def test_the_two_module_globals_agree():
    """A half-applied redirect is worse than none: writes and reads split.

    `_sync_session_db_alias` copies `code_tools._SESSIONS_DB` onto the store, so
    if only the store were redirected the first tool call would silently drag
    the path back to production mid-run.
    """
    assert _same_file(Path(store._SESSIONS_DB), Path(code_tools._SESSIONS_DB)), (
        f"store={store._SESSIONS_DB!s} != code_tools={code_tools._SESSIONS_DB!s}"
    )


def test_writing_a_session_event_does_not_touch_production():
    """End-to-end: the real write path, then count rows in the REAL database.

    This is the load-bearing test. The two above check a path; this one checks
    the consequence, so an isolation scheme that sets the globals but still ends
    up writing to production cannot pass.
    """
    prod = _production_db()
    if not prod.exists():
        # No production DB on this machine (fresh clone / CI): nothing to
        # protect, and an empty-file assertion would be meaningless.
        return

    def _counts() -> tuple[int, int]:
        conn = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True)
        try:
            return (
                conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            )
        finally:
            conn.close()

    before = _counts()

    session_id = "a53-isolation-probe"
    store._log_session_event(
        session_id,
        "read_file_range",
        {"file_path": "a53.py"},
        {"ok": True},
        0,
        "ok",
        "a53 isolation probe",
    )
    # The writer drains on a background thread; close it so the write is either
    # committed to the temp DB or gone, with nothing still in flight.
    store.close_writer()

    after = _counts()
    assert after == before, (
        "a session event written by the test suite landed in the PRODUCTION "
        f"database {prod}: sessions/events went {before} -> {after}. "
        "Test writes must never reach the daemon's DB."
    )
