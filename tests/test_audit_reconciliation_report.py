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
def reconciliation_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(store, "_SESSIONS_DB", db)
    monkeypatch.setattr(code_tools, "_SESSIONS_DB", db)
    store._init_session_db()
    session_id = "reconciliation"
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO sessions (session_id,session_title,project_path,mode,status,coverage,started_at,agent_id) VALUES (?,?,?,?,?,?,?,?)", (session_id,"C1",str(tmp_path),"T1","active","A-only","2026-08-09T00:00:00","Cozo"))
        conn.execute("INSERT INTO sessions (session_id,session_title,project_path,mode,status,coverage,started_at,agent_id) VALUES (?,?,?,?,?,?,?,?)", ("other","Other",str(tmp_path),"T1","active","A-only","2026-08-09T00:00:01","Other"))
        conn.commit()
    return db, session_id


def event(sid, tool, status, error_name="", correlation=("", ""), evidence=None, reason=""):
    return store._log_session_event(sid, tool, {"file_path": tool + ".py"}, {"event_kind":"error" if status=="error" else "operation", "error_name":error_name, "correlation_type":correlation[0], "correlation_key":correlation[1], "evidence":evidence}, 1, status, reason or error_name)


def report(sid):
    return json.loads(asyncio.run(tools_v15.audit_reconciliation_report(target_session_id=sid)))


def test_four_classes_accounting_idempotency_and_strong_evidence(reconciliation_db):
    db, sid = reconciliation_db
    expected = event(sid,"validate","error","EXPECTED",evidence={"expected_failure":True,"test_profile":"focused"})
    recovered = event(sid,"write_fix","error","WRITE_FAILED",("file","a.py"))
    operation = event(sid,"write_fix","ok",correlation=("file","a.py"),evidence={"test_name":"regression","commit_sha":"abcdef1"})
    still_open = event(sid,"write_fix","error","WRITE_FAILED")
    infrastructure = event(sid,"audit_writer","error","SQLITE",reason="no such table: durable_audit_outbox")
    before = db.stat().st_size
    first = report(sid); second = report(sid)
    assert first == second
    assert first["read_only"] is True and first["accounting_ok"] is True
    assert first["input_open_errors"] == first["accounted_errors"] == 4
    assert first["bucket_counts"] == {"expected":1,"recovered":1,"still_open":1,"infrastructure":1}
    assert {item["event_id"] for item in first["errors"]} == {expected,recovered,still_open,infrastructure}
    by_id = {item["event_id"]: item for item in first["errors"]}
    assert by_id[recovered]["evidence_links"] == {"operation":[{"event_id":operation,"tool":"write_fix","correlation_type":"file","correlation_key":"a.py"}],"test":[{"name":"regression"}],"commit":["abcdef1"]}
    assert by_id[recovered]["suggested_resolution_call"]["tool"] == "record_error_resolution"
    assert by_id[expected]["disposition"] == "expected_failure"
    assert by_id[still_open]["suggested_resolution_call"] is None
    assert by_id[infrastructure]["cause_category"] == "infrastructure"
    assert by_id[infrastructure]["disposition"] == "still_open"
    assert by_id[infrastructure]["evidence_links"]["commit"] == "not_verified"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone() == (5,)
        assert conn.execute("SELECT COUNT(*) FROM events WHERE event_kind='resolution'").fetchone() == (0,)
    assert db.stat().st_size == before


def test_explicit_resolution_changes_projection_but_preserves_other_open_error(reconciliation_db):
    db, sid = reconciliation_db
    resolved = event(sid,"write_fix","error","WRITE_FAILED",("file","a.py"))
    event(sid,"write_fix","ok",correlation=("file","a.py"))
    still_open = event(sid,"write_fix","error","WRITE_FAILED")
    before = report(sid)
    response = json.loads(asyncio.run(tools_v15.record_error_resolution(sid,resolved,"recovered",calling_context={"validated":True,"session_id":sid,"agent_id":"Cozo"},evidence={"suite":"focused","passed":True})))
    after = report(sid)
    assert response["ok"] is True
    assert before["input_open_errors"] == 2 and after["input_open_errors"] == 1
    assert after["errors"][0]["event_id"] == still_open
    with sqlite3.connect(db) as conn:
        rows=conn.execute("SELECT id,event_kind,resolves_event_id FROM events ORDER BY id").fetchall()
    assert rows[-1][1:] == ("resolution",resolved)
    assert len(rows) == 4


def test_resolution_cross_session_rejected_and_report_does_not_cross_correlate(reconciliation_db):
    db, sid = reconciliation_db
    error = event(sid,"write_fix","error","WRITE_FAILED",("file","shared.py"))
    event("other","write_fix","ok",correlation=("file","shared.py"))
    result = report(sid)
    assert result["errors"][0]["event_id"] == error
    assert result["errors"][0]["classification"] == "still_open"
    rejected=json.loads(asyncio.run(tools_v15.record_error_resolution(sid,error,"recovered",calling_context={"validated":True,"session_id":"other","agent_id":"Other"})))
    assert rejected["error_name"] == "SESSION_OWNERSHIP_REQUIRED"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE event_kind='resolution'").fetchone() == (0,)

def test_exactly_27_open_errors_are_accounted_once(reconciliation_db):
    _, sid = reconciliation_db
    event_ids = [event(sid, "write_fix", "error", "WRITE_FAILED") for _ in range(27)]

    result = report(sid)

    reported_ids = [item["event_id"] for item in result["errors"]]
    assert result["input_open_errors"] == 27
    assert result["accounted_errors"] == 27
    assert result["accounting_ok"] is True
    assert sum(result["bucket_counts"].values()) == 27
    assert reported_ids == event_ids
    assert len(reported_ids) == len(set(reported_ids)) == 27


def test_registry_discovery_is_t1_unprotected_and_read_only():
    import orchestrator_server as server
    name="audit_reconciliation_report"
    schema=next(item["function"] for item in code_tools.TOOL_SCHEMAS if item["function"]["name"]==name)
    assert code_tools.TOOL_MAP[name] is tools_v15.audit_reconciliation_report
    assert code_tools.TOOL_PHASES[name] == schema["x_phase"] == "T1"
    assert schema.get("requires_token",False) is False
    assert schema.get("requires_session",False) is False
    assert schema.get("mutation_scope") in (None,"none")
    assert name not in code_tools.PROTECTED_TOOL_NAMES
    assert name not in server._TOKEN_REQUIRED_TOOLS
    assert name not in server._MUTATING_TOOLS
