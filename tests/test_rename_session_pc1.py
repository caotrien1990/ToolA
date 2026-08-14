"""PC1-SESSION regression test.

WHY: `rename_session` documents "Closed sessions cannot be renamed." Before
PC1-SESSION, when no ACTIVE session existed for (project_path, agent_id),
the server's AUTO_WRITE gate would silently auto-open a brand-new
"untitled-session-..." and `rename_session` would then rename THAT,
returning ok:true. The contract violation hid the fact that the user had
no live session to rename.

These tests pin the corrected behavior:
  1. code_tools.rename_session direct call returns a fail-loud SESSION_CLOSED
     error (with ca_next_action) instead of renaming an implicit session.
  2. The sessions DB MUST NOT gain an "untitled-session-" row from a rename
     attempt — proving no auto-open happened.
  3. Active-session rename path still works (no contract regression).

Note: tests use code_tools._auto_open_session + code_tools._get_active_session_id
to set up state, because open_session's path-resolution walks upward and would
bind to a parent project root in the test environment. Testing rename_session
against the resolved DB directly is the precise contract under test.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

import code_tools


@pytest.fixture
def isolated_sessions_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Give each test a fresh sessions DB so cross-test state cannot leak.

    Yields (db_path, project_path_str). The project_path is a simple string
    key (no resolution) matching what _session_project_key produces.
    """
    db_path = tmp_path / "sessions_pc1.db"
    monkeypatch.setattr(code_tools, "_SESSIONS_DB", db_path)
    # code_tools re-exports _SESSIONS_DB into _session_store_module and
    # _session_logs_module; without syncing those aliases, the writer thread
    # keeps using the production DB while the test reads the test DB.
    code_tools._sync_session_db_alias()
    code_tools._init_session_db()
    project_key = str(tmp_path / "proj_pc1")
    yield db_path, project_key
    # Restore aliases so subsequent tests see the production DB again.
    monkeypatch.undo()
    code_tools._sync_session_db_alias()


def _count_sessions(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
    return int(row[0]) if row else 0


def _session_titles(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT session_title FROM sessions").fetchall()
    return [r[0] for r in rows]


def _close_active_session(project_key: str, agent_id: str = "") -> None:
    """Mark the active session closed directly in the DB (mirrors close_session
    semantics without going through the project's path-resolution layer)."""
    def _close(write_conn):
        if agent_id:
            write_conn.execute(
                "UPDATE sessions SET status='closed', ended_at=CURRENT_TIMESTAMP "
                "WHERE project_path=? AND agent_id=? AND status='active'",
                (project_key, agent_id),
            )
        else:
            write_conn.execute(
                "UPDATE sessions SET status='closed', ended_at=CURRENT_TIMESTAMP "
                "WHERE project_path=? AND status='active'",
                (project_key,),
            )
    code_tools._run_session_write("test close", _close)


def test_rename_session_returns_session_closed_when_no_active(
    isolated_sessions_db,
) -> None:
    """Closed-then-renamed session must fail loud, not silently auto-open."""
    db_path, project_key = isolated_sessions_db

    # 1. Set up an active session, then close it (simulating the S2 finding).
    sid = code_tools._auto_open_session(project_key, {"agent_id": ""})
    assert sid, "setup: _auto_open_session returned no session_id"
    assert code_tools._get_active_session_id(project_key, None) == sid
    _close_active_session(project_key)
    assert code_tools._get_active_session_id(project_key, None) is None

    sessions_before = _count_sessions(db_path)

    # 2. Attempt rename on the now-closed session.
    renamed = json.loads(asyncio.run(code_tools.rename_session(
        project_key, "should-not-be-applied")))

    # 3. Contract: must NOT return ok:true. Must surface SESSION_CLOSED
    #    (or ACTIVE_SESSION_REQUIRED) with ca_next_action pointing at open_session.
    assert not renamed.get("ok"), (
        f"rename_session must not return ok:true when no active session exists; got {renamed}"
    )
    assert renamed.get("error_name") in ("SESSION_CLOSED", "ACTIVE_SESSION_REQUIRED"), (
        f"expected SESSION_CLOSED or ACTIVE_SESSION_REQUIRED, got {renamed.get('error_name')!r} "
        f"full={renamed}"
    )
    # 4. ca_next_action must guide the caller to open_session.
    assert renamed.get("ca_next_action") == "open_session", (
        f"ca_next_action should be 'open_session', got {renamed.get('ca_next_action')!r}"
    )

    # 5. Crucially: NO new "untitled-session-" row may have been created.
    sessions_after = _count_sessions(db_path)
    assert sessions_after == sessions_before, (
        f"rename_session auto-opened a new session (before={sessions_before} after={sessions_after}); "
        f"titles={_session_titles(db_path)}"
    )
    for title in _session_titles(db_path):
        assert not (title or "").startswith("untitled-session-"), (
            f"found implicit auto-opened session titled {title!r}; auto-open must not run for rename_session"
        )


def test_rename_session_still_works_on_active_session(
    isolated_sessions_db,
) -> None:
    """Active-session rename path must remain unchanged (no contract regression)."""
    db_path, project_key = isolated_sessions_db

    sid = code_tools._auto_open_session(project_key, {"agent_id": ""})
    assert sid, "setup: _auto_open_session returned no session_id"

    renamed = json.loads(asyncio.run(code_tools.rename_session(
        project_key, "Active Rename OK")))
    assert renamed.get("ok"), f"active rename must succeed; got {renamed}"
    assert renamed.get("session_id") == sid
    assert renamed.get("new_title") == "Active Rename OK"

    # Confirm the title actually landed in the DB.
    titles = _session_titles(db_path)
    assert "Active Rename OK" in titles, f"rename not persisted; titles={titles}"


def test_rename_session_agent_scoped_close_then_rename_fails(
    isolated_sessions_db,
) -> None:
    """Agent-scoped variant: closed agent session must also fail loudly."""
    db_path, project_key = isolated_sessions_db

    sid = code_tools._auto_open_session(project_key, {"agent_id": "agent-pc1"})
    assert sid, "setup: _auto_open_session returned no session_id"
    assert code_tools._get_active_session_id(project_key, "agent-pc1") == sid
    _close_active_session(project_key, agent_id="agent-pc1")
    assert code_tools._get_active_session_id(project_key, "agent-pc1") is None

    sessions_before = _count_sessions(db_path)

    renamed = json.loads(asyncio.run(code_tools.rename_session(
        project_key, "agent-rename-after-close", agent_id="agent-pc1")))
    assert not renamed.get("ok"), (
        f"agent-scoped rename on closed session must fail; got {renamed}"
    )
    assert renamed.get("error_name") in ("SESSION_CLOSED", "ACTIVE_SESSION_REQUIRED")
    assert renamed.get("ca_next_action") == "open_session"
    assert _count_sessions(db_path) == sessions_before


def test_rename_session_with_no_prior_session_at_all_fails(
    isolated_sessions_db,
) -> None:
    """No session ever opened: rename must fail loud, not bootstrap one."""
    db_path, project_key = isolated_sessions_db

    sessions_before = _count_sessions(db_path)
    assert sessions_before == 0, "fresh DB should have zero sessions"

    renamed = json.loads(asyncio.run(code_tools.rename_session(
        project_key, "no-prior-session")))
    assert not renamed.get("ok"), (
        f"rename_session with no prior session must fail; got {renamed}"
    )
    assert renamed.get("error_name") in ("SESSION_CLOSED", "ACTIVE_SESSION_REQUIRED")
    assert _count_sessions(db_path) == 0, (
        "rename_session must not create a session row when none existed before"
    )
