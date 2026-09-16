"""ISS-01 / ISS-10 / ISS-13: three records that used to answer for each other.

ISS-01 -- write_status by target. `_WRITE_LEDGER_BY_TARGET` held ONE operation_id
per (project_path, file_path). Index / script / git tools all record
file_path="", so every non-file-scoped mutation on one root shared the key
("<root>", "") and the newest call silently overwrote the previous one: polling
"did my cbm_incremental_index commit?" was answered by an unrelated
run_project_command's ledger, with that tool's commit_state.

ISS-10 -- ledger evidence. `_WRITE_LEDGER_RESULT_KEYS` was written for the
write_fix family, so every other tool stored a summary of exactly {"ok": true}.
A caller polling write_status saw commit_state=committed with no evidence of WHAT
committed -- indistinguishable from a no-op run.

ISS-13 -- session mode. set_workflow_mode rewrote WORKFLOW_STATE.json only, while
the T1/T2 gate reads `sessions.mode`, which was write-once at open time.
open_session on resume answers `mode_ignored_on_resume: true` and points the
caller at set_workflow_mode; following that hint returned ok:true and left the
caller on the old mode forever, the two records disagreeing permanently.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import orchestrator_server as srv
from orchestrator.session import store as session_store


def _target(root: str) -> tuple[str, str]:
    return srv._write_ledger_target({"project_path": root})


def _row(op_id: str, tool: str, status: str, updated_at: float) -> dict:
    return {"operation_id": op_id, "tool": tool, "status": status,
            "project_path": "d:\\repo", "file_path": "",
            "started_at": updated_at, "finished_at": None,
            "updated_at": updated_at, "attempts": 1,
            "origin_generation": srv.SERVER_GENERATION}


def _install(rows: list[dict], root: str) -> tuple[str, str]:
    """Publish rows into the in-memory ledger under one target key."""
    pp, fp = _target(root)
    for row in rows:
        srv._WRITE_LEDGER[row["operation_id"]] = row
        srv._write_ledger_target_remember(pp, fp, row["operation_id"])
    return pp, fp


def _uninstall(rows: list[dict], key: tuple[str, str]) -> None:
    for row in rows:
        srv._WRITE_LEDGER.pop(row["operation_id"], None)
    srv._WRITE_LEDGER_BY_TARGET.pop(key, None)


def _status_by_target(root: str, tool: str = ""):
    request = type("R", (), {"headers": {},
                             "query": {"project_path": root, "tool": tool}})()
    response = asyncio.run(srv.handle_write_status(request))
    return response.status, json.loads(response.text)


# --------------------------------------------------------------------------
# ISS-01
# --------------------------------------------------------------------------

def test_target_key_keeps_every_candidate():
    key = ("d:\\iss01-many", "")
    srv._WRITE_LEDGER_BY_TARGET.pop(key, None)
    try:
        for op in ("op-a", "op-b", "op-a"):  # re-remember must dedupe, not duplicate
            srv._write_ledger_target_remember(key[0], key[1], op)
        assert srv._write_ledger_target_ids(*key) == ["op-b", "op-a"]
    finally:
        srv._WRITE_LEDGER_BY_TARGET.pop(key, None)


def test_legacy_single_string_value_is_tolerated():
    # A generation before ISS-01 stored a bare op id here; a live upgrade must
    # not KeyError on the value it left behind.
    key = ("d:\\iss01-legacy", "")
    srv._WRITE_LEDGER_BY_TARGET[key] = "old-single-id"
    try:
        assert srv._write_ledger_target_ids(*key) == ["old-single-id"]
        srv._write_ledger_target_remember(key[0], key[1], "new-id")
        assert srv._write_ledger_target_ids(*key) == ["old-single-id", "new-id"]
    finally:
        srv._WRITE_LEDGER_BY_TARGET.pop(key, None)


def test_one_candidate_resolves_without_ambiguity():
    rows = [_row("solo", "cbm_incremental_index", "committed", 100.0)]
    rec, candidates, ambiguous = srv._write_ledger_pick_candidate(rows)
    assert rec is rows[0]
    assert ambiguous is False
    assert [c["operation_id"] for c in candidates] == ["solo"]


def test_same_tool_many_payloads_picks_the_newest():
    rows = [_row("old", "run_project_test_script", "committed", 100.0),
            _row("new", "run_project_test_script", "committed", 200.0)]
    rec, candidates, ambiguous = srv._write_ledger_pick_candidate(rows)
    assert rec["operation_id"] == "new"
    assert ambiguous is False
    assert len(candidates) == 2


def test_different_tools_with_no_in_flight_is_ambiguous():
    rows = [_row("idx", "cbm_incremental_index", "committed", 100.0),
            _row("cmd", "run_project_command", "committed", 200.0)]
    rec, candidates, ambiguous = srv._write_ledger_pick_candidate(rows)
    assert rec is None
    assert ambiguous is True
    assert {c["tool"] for c in candidates} == {"cbm_incremental_index",
                                               "run_project_command"}


def test_exactly_one_in_flight_is_the_operation_being_waited_on():
    rows = [_row("done", "run_project_command", "committed", 100.0),
            _row("live", "cbm_incremental_index", "in_flight", 200.0)]
    rec, candidates, ambiguous = srv._write_ledger_pick_candidate(rows)
    assert len(candidates) == 2
    assert rec["operation_id"] == "live"
    assert ambiguous is False


def test_tool_filter_disambiguates_two_finished_tools():
    rows = [_row("idx", "cbm_incremental_index", "committed", 100.0),
            _row("cmd", "run_project_command", "committed", 200.0)]
    rec, candidates, ambiguous = srv._write_ledger_pick_candidate(
        rows, "cbm_incremental_index")
    assert rec["operation_id"] == "idx"
    assert ambiguous is False
    assert len(candidates) == 1


def test_write_status_refuses_an_ambiguous_target_instead_of_guessing():
    root = "D:/iss01-ambiguous-root"
    rows = [_row("idx", "cbm_incremental_index", "committed", 100.0),
            _row("cmd", "run_project_command", "committed", 200.0)]
    key = _install(rows, root)
    try:
        status, payload = _status_by_target(root)
    finally:
        _uninstall(rows, key)
    assert status == 409
    assert payload["ok"] is False
    assert payload["error_name"] == "WRITE_STATUS_AMBIGUOUS_TARGET"
    # unknown, NOT the newest row's committed: that swap is the whole bug.
    assert payload["commit_state"] == "unknown"
    assert payload["safe_to_retry"] is False
    assert payload["ca_next_action"] == "retry_with_operation_id"
    assert payload["tools"] == ["cbm_incremental_index", "run_project_command"]


def test_tool_query_param_rescues_the_ambiguous_target():
    root = "D:/iss01-ambiguous-root-2"
    rows = [_row("idx", "cbm_incremental_index", "committed", 100.0),
            _row("cmd", "run_project_command", "committed", 200.0)]
    key = _install(rows, root)
    try:
        status, payload = _status_by_target(root, tool="cbm_incremental_index")
    finally:
        _uninstall(rows, key)
    assert status == 200
    assert payload["ok"] is True
    assert payload["operation_id"] == "idx"
    assert payload["commit_state"] == "committed"


def test_resolvable_target_still_discloses_the_other_candidates():
    # One in_flight row is resolvable, but the caller must still be able to SEE
    # that this key is shared -- otherwise the swap risk is only discovered from
    # a mismatched tool name in the response.
    root = "D:/iss01-shared-root"
    rows = [_row("done", "run_project_command", "committed", 100.0),
            _row("live", "cbm_incremental_index", "in_flight", 200.0)]
    key = _install(rows, root)
    try:
        status, payload = _status_by_target(root)
    finally:
        _uninstall(rows, key)
    assert status == 200
    assert payload["operation_id"] == "live"
    assert payload["lookup"] == "target"
    assert {c["tool"] for c in payload["candidates"]} == {
        "run_project_command", "cbm_incremental_index"}


# --------------------------------------------------------------------------
# ISS-10
# --------------------------------------------------------------------------

def _finish(monkeypatch, result: dict) -> dict:
    op = "iss10-evidence"
    monkeypatch.setattr(srv, "_write_ledger_db_upsert", lambda _rec: None)
    srv._WRITE_LEDGER[op] = {"operation_id": op, "tool": "cbm_incremental_index",
                             "status": "in_flight", "attempts": 1,
                             "started_at": time.time(),
                             "origin_generation": srv.SERVER_GENERATION}
    try:
        srv._write_ledger_finish(op, result, "ok")
        return dict(srv._WRITE_LEDGER[op]["result"])
    finally:
        srv._WRITE_LEDGER.pop(op, None)


def test_index_run_keeps_its_own_evidence(monkeypatch):
    summary = _finish(monkeypatch, {
        "ok": True, "run_id": "r-77", "scope": "incremental", "status": "completed",
        "files_indexed": 12, "files_unchanged": 251, "nodes": 30, "edges": 44,
        "duration_s": 3.5, "irrelevant_blob": "x" * 5000,
    })
    assert summary["run_id"] == "r-77"
    assert summary["files_indexed"] == 12
    assert summary["nodes"] == 30
    assert summary["status"] == "completed"
    # Evidence found, so the fallback disclosure must NOT fire.
    assert "result_keys" not in summary
    assert "irrelevant_blob" not in summary


def test_command_and_git_families_are_covered(monkeypatch):
    summary = _finish(monkeypatch, {"ok": True, "exit_code": 0, "executed": True,
                                    "timed_out": False, "commit_hash": "2786e53"})
    assert summary["exit_code"] == 0
    assert summary["executed"] is True
    assert summary["commit_hash"] == "2786e53"
    assert "result_keys" not in summary


def test_bare_ok_result_discloses_where_the_evidence_lives(monkeypatch):
    summary = _finish(monkeypatch, {"ok": True, "totally_new_field": 1,
                                    "another": {"deep": True}})
    assert summary["ok"] is True
    assert "totally_new_field" in summary["result_keys"]
    assert "another" in summary["result_keys"]
    assert "no known evidence field" in summary["summary_note"]


def test_scalar_coercion_keeps_the_row_small():
    assert srv._write_ledger_scalar([1, 2, 3]) == {"count": 3}
    assert srv._write_ledger_scalar({"a": 1, "b": 2}) == {"keys": ["a", "b"]}
    long = srv._write_ledger_scalar("y" * (srv._WRITE_LEDGER_SCALAR_MAX + 50))
    assert long.endswith("…[truncated]")
    assert len(long) <= srv._WRITE_LEDGER_SCALAR_MAX + 20
    assert srv._write_ledger_scalar(None) is None
    assert srv._write_ledger_scalar(True) is True


# --------------------------------------------------------------------------
# ISS-13
# --------------------------------------------------------------------------

def _sessions_conn(status: str = "active", mode: str = "T1") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE sessions (session_id TEXT, mode TEXT, status TEXT)")
    conn.execute("INSERT INTO sessions VALUES (?,?,?)", ("sess-1", mode, status))
    return conn


def test_active_session_row_is_re_moded():
    conn = _sessions_conn(mode="T1")
    try:
        applied = session_store._set_session_mode(conn, "sess-1", "T2")
        stored = conn.execute(
            "SELECT mode FROM sessions WHERE session_id='sess-1'").fetchone()[0]
    finally:
        conn.close()
    assert applied == {"old_mode": "T1", "new_mode": "T2", "mode_changed": True}
    assert stored == "T2"


def test_re_moding_to_the_same_value_reports_no_change():
    conn = _sessions_conn(mode="T2")
    try:
        applied = session_store._set_session_mode(conn, "sess-1", "T2")
    finally:
        conn.close()
    assert applied["mode_changed"] is False


def test_closed_session_is_never_re_moded():
    conn = _sessions_conn(status="closed", mode="T1")
    try:
        applied = session_store._set_session_mode(conn, "sess-1", "T2")
        stored = conn.execute(
            "SELECT mode FROM sessions WHERE session_id='sess-1'").fetchone()[0]
    finally:
        conn.close()
    # None is the signal set_workflow_mode turns into reason=session_not_active,
    # instead of reporting a mode change that never reached any live gate.
    assert applied is None
    assert stored == "T1"


def test_unknown_session_id_is_not_an_error():
    conn = _sessions_conn()
    try:
        assert session_store._set_session_mode(conn, "does-not-exist", "T2") is None
    finally:
        conn.close()
