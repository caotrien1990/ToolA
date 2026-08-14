"""PC1-CLEANUP regression test.

WHY: `clear_closed_sessions` was requested as a session lifecycle cleanup
tool and its handler existed in code_tools.py (line 11856), but it was never
registered in TOOL_MAP / TOOL_SCHEMAS / PROTECTED_TOOL_NAMES, so
`/api/tools/clear_closed_sessions/schema` returned UNKNOWN_TOOL. The REST
route `DELETE /api/sessions/closed` already exposed the same handler with an
X-Orchestrator-Token requirement.

These tests pin the registration:
  1. The tool MUST be present in code_tools.TOOL_MAP (callable via /api/call).
  2. A schema MUST exist (so /api/tools/clear_closed_sessions/schema resolves).
  3. The tool MUST be in PROTECTED_TOOL_NAMES + TOKEN_REQUIRED_TOOLS (matches
     the REST route's existing X-Orchestrator-Token gate).
  4. End-to-end behavior: open 2 sessions, close both, call the handler,
     verify closed rows + their events are deleted and count is reported.
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
    """Fresh sessions DB per test."""
    db_path = tmp_path / "sessions_pc1c.db"
    monkeypatch.setattr(code_tools, "_SESSIONS_DB", db_path)
    code_tools._sync_session_db_alias()
    code_tools._init_session_db()
    project_key = str(tmp_path / "proj_pc1c")
    yield db_path, project_key
    monkeypatch.undo()
    code_tools._sync_session_db_alias()


def test_clear_closed_sessions_is_registered_in_tool_catalog() -> None:
    """Tool must appear in TOOL_MAP (callable), TOOL_SCHEMAS (schema resolves),
    PROTECTED_TOOL_NAMES (mutating gate), and TOKEN_REQUIRED_TOOLS (destructive
    gate matching the REST route)."""
    assert "clear_closed_sessions" in code_tools.TOOL_MAP, (
        "clear_closed_sessions missing from TOOL_MAP — /api/call would return UNKNOWN_TOOL"
    )
    schemas = [
        s for s in code_tools.TOOL_SCHEMAS
        if s.get("function", {}).get("name") == "clear_closed_sessions"
    ]
    assert schemas, (
        "clear_closed_sessions missing from TOOL_SCHEMAS — /api/tools/<name>/schema would return UNKNOWN_TOOL"
    )
    params = schemas[0]["function"].get("parameters", {})
    assert "project_path" in (params.get("properties") or {}), (
        "schema must accept project_path"
    )
    assert "clear_closed_sessions" in code_tools.PROTECTED_TOOL_NAMES, (
        "tool deletes rows — must be in PROTECTED_TOOL_NAMES (mutating gate)"
    )
    assert "clear_closed_sessions" in code_tools.TOKEN_REQUIRED_TOOLS, (
        "tool is an irreversible audit-history delete — must be in TOKEN_REQUIRED_TOOLS, "
        "matching the existing REST route's X-Orchestrator-Token gate"
    )
    assert code_tools.tool_mutation_scope("clear_closed_sessions") == "audit"
    assert code_tools.tool_phase("clear_closed_sessions") == "T3"


def test_clear_closed_sessions_deletes_only_closed(isolated_sessions_db) -> None:
    """End-to-end: closed sessions + their events are deleted; active stays."""
    db_path, project_key = isolated_sessions_db

    # 1. Open two sessions for the same project (different agents to allow
    #    concurrent active) — then close ONE of them.
    sid_active = code_tools._auto_open_session(project_key, {"agent_id": "agent-keep"})
    sid_closed = code_tools._auto_open_session(project_key, {"agent_id": "agent-drop"})
    assert sid_active and sid_closed and sid_active != sid_closed

    # Insert one event per session so we can verify events are cascade-deleted.
    def _insert_events(write_conn):
        for sid in (sid_active, sid_closed):
            write_conn.execute(
                "INSERT INTO events (session_id, action_type, tool_name, target, reason, status, duration_ms) "
                "VALUES (?, 'read', 'probe', 'x', 'setup', 'ok', 0)",
                (sid,),
            )
    code_tools._run_session_write("seed events", _insert_events)

    # Mark one session closed directly (bypasses close_session's project-path
    # resolution layer which would otherwise bind to the parent project root).
    def _close_one(write_conn):
        write_conn.execute(
            "UPDATE sessions SET status='closed', ended_at=CURRENT_TIMESTAMP "
            "WHERE session_id=?",
            (sid_closed,),
        )
    code_tools._run_session_write("close one", _close_one)

    # 2. Call the handler.
    raw = asyncio.run(code_tools.clear_closed_sessions(project_key))
    result = json.loads(raw)
    assert result.get("ok"), f"handler returned error: {result}"
    assert result.get("deleted_sessions") == 1, (
        f"expected exactly 1 closed session deleted; got {result}"
    )
    assert result.get("deleted_events") == 1, (
        f"expected the closed session's 1 event deleted; got {result}"
    )

    # 3. Verify DB state: active session remains, closed session is gone.
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT session_id, status FROM sessions WHERE project_path=?",
            (project_key,),
        ).fetchall()
        remaining_ids = {r[0] for r in rows}
        assert sid_active in remaining_ids, (
            f"active session {sid_active} must be preserved; remaining={rows}"
        )
        assert sid_closed not in remaining_ids, (
            f"closed session {sid_closed} must be deleted; remaining={rows}"
        )
        # Events for the closed session must also be gone.
        ev = conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id=?",
            (sid_closed,),
        ).fetchone()
        assert ev and ev[0] == 0, (
            f"events for closed session must be cascade-deleted; got {ev}"
        )


def test_clear_closed_sessions_with_no_closed_returns_zero(isolated_sessions_db) -> None:
    """Calling the tool on a project with no closed sessions is a safe no-op."""
    db_path, project_key = isolated_sessions_db

    sid = code_tools._auto_open_session(project_key, {"agent_id": "agent-live"})
    assert sid

    raw = asyncio.run(code_tools.clear_closed_sessions(project_key))
    result = json.loads(raw)
    assert result.get("ok"), f"handler returned error: {result}"
    assert result.get("deleted_sessions") == 0
    assert result.get("deleted_events") == 0

    # Active session still present.
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id=? AND status='active'",
            (sid,),
        ).fetchone()
        assert row and row[0] == 1, "active session must be preserved"


def test_clear_closed_sessions_requires_project_path(isolated_sessions_db) -> None:
    """Empty project_path must return a fail-loud error, not crash."""
    raw = asyncio.run(code_tools.clear_closed_sessions(""))
    result = json.loads(raw)
    assert not result.get("ok"), (
        f"empty project_path must fail; got {result}"
    )
