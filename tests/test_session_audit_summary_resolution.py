from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

import code_tools
import tools_v15
from orchestrator.session import store


@pytest.fixture
def summary_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    db_path = tmp_path / "sessions.db"
    project = str((tmp_path / "project").resolve())
    monkeypatch.setattr(store, "_SESSIONS_DB", db_path)
    monkeypatch.setattr(code_tools, "_SESSIONS_DB", db_path)
    store._init_session_db()
    session_id = "summary-session"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO sessions
               (session_id, session_title, project_path, mode, status, coverage, started_at, agent_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (session_id, "Summary", project, "T2", "active", "A-only", "2026-08-02T00:00:00", "Ope"),
        )
        conn.commit()
    return db_path, session_id


def _error(session_id: str, owner: str, name: str, target: str = "same.py") -> int:
    return store._log_session_event(
        session_id, "write_fix", {"file_path": target},
        {"event_kind": "error", "error_owner": owner, "error_name": name},
        1, "error", name,
    )


def _summary(session_id: str) -> dict:
    return json.loads(asyncio.run(tools_v15.session_audit_summary(target_session_id=session_id)))


def test_unrelated_later_ok_is_candidate_but_does_not_recover(summary_db) -> None:
    _, session_id = summary_db
    error_id = _error(session_id, "backend", "WRITE_FAILED")
    ok_id = store._log_session_event(
        session_id, "write_fix", {"file_path": "same.py"}, {}, 1, "ok"
    )

    result = _summary(session_id)

    assert result["recovered_errors"] == 0
    assert result["open_errors"] == 1
    assert result["legacy_resolution_candidates"] == [{
        "error_event_id": error_id, "candidate_event_id": ok_id,
        "tool": "write_fix", "target": "same.py",
    }]


def test_summary_uses_latest_explicit_state_and_structured_owner_counts(summary_db) -> None:
    _, session_id = summary_db
    recovered = _error(session_id, "backend", "BACKEND_DOWN", "one.py")
    expected = _error(session_id, "caller", "INVALID_INPUT", "two.py")
    reopened = _error(session_id, "backend", "WRITE_FAILED", "three.py")
    store._record_error_resolution(session_id, recovered, "recovered")
    store._record_error_resolution(session_id, expected, "expected_failure")
    store._record_error_resolution(session_id, reopened, "recovered")
    store._record_error_resolution(session_id, reopened, "reopened")

    result = _summary(session_id)

    assert result["errors"] == 3
    assert result["recovered_errors"] == 1
    assert result["expected_failure_errors"] == 1
    assert result["open_errors"] == 1
    assert result["error_owner_counts"] == {"backend": 2, "caller": 1}
    assert result["caller_errors"] == 1
    assert result["backend_errors"] == 2
    assert result["open_error_samples"] == [{
        "id": reopened, "tool": "write_fix", "target": "three.py",
        "error_owner": "backend", "error_name": "WRITE_FAILED",
        "resolution_state": "reopened",
    }]
    assert result["next_recommended_action"] == "investigate_open_risks_first"


def test_public_resolution_tool_updates_exact_error_and_event_projection(summary_db) -> None:
    _, session_id = summary_db
    resolved = _error(session_id, "backend", "WRITE_FAILED", "resolved.py")
    still_open = _error(session_id, "backend", "WRITE_FAILED", "open.py")

    recorded = json.loads(asyncio.run(tools_v15.record_error_resolution(
        session_id,
        resolved,
        "recovered",
        calling_context={"validated": True, "session_id": session_id, "agent_id": "Ope"},
        evidence={"suite": "focused", "passed": True},
    )))
    events = json.loads(asyncio.run(code_tools.get_session_events(
        target_session_id=session_id,
    )))
    result = _summary(session_id)

    assert recorded["ok"] is True
    assert recorded["target_session_id"] == session_id
    assert recorded["authority"] == "same_session"
    assert recorded["resolves_event_id"] == resolved
    assert recorded["resolution_kind"] == "recovered"
    resolution = next(
        event for event in events["events"]
        if event["id"] == recorded["resolution_event_id"]
    )
    assert resolution["event_kind"] == "resolution"
    assert resolution["resolves_event_id"] == resolved
    assert resolution["resolution_kind"] == "recovered"
    assert json.loads(resolution["evidence_json"]) == {"suite": "focused", "passed": True}
    assert result["recovered_errors"] == 1
    assert result["open_errors"] == 1
    assert result["open_error_samples"][0]["id"] == still_open


def test_public_resolution_rejects_unowned_session_without_append(summary_db) -> None:
    db_path, session_id = summary_db
    error_id = _error(session_id, "backend", "WRITE_FAILED")

    result = json.loads(asyncio.run(tools_v15.record_error_resolution(
        session_id, error_id, "recovered",
        calling_context={"validated": True, "session_id": "other-host", "agent_id": "Ope"},
    )))

    assert result["error_name"] == "SESSION_OWNERSHIP_REQUIRED"
    assert result["write_performed"] is False
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_kind='resolution'"
        ).fetchone() == (0,)


def test_public_resolution_allows_correlated_cross_session_host(summary_db) -> None:
    db_path, session_id = summary_db
    error_id = _error(session_id, "backend", "WRITE_FAILED")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE events SET host_session_id=? WHERE id=?", ("host-task", error_id)
        )
        conn.commit()

    result = json.loads(asyncio.run(tools_v15.record_error_resolution(
        session_id, error_id, "recovered",
        calling_context={"validated": True, "session_id": "host-task", "agent_id": "Ope"},
    )))

    assert result["ok"] is True
    assert result["authority"] == "host_task_correlation"
