import asyncio
import json
import threading
import time
from unittest import mock

import orchestrator_server as srv
from orchestrator.progress_watchdog import OperationCancelled, ProgressWatchdog
from orchestrator.graph import status as graph_status


def test_slow_progress_has_no_total_time_ceiling():
    seen = []
    wd = ProgressWatchdog(operation_id="slow", heartbeat=seen.append)
    for i in range(3):
        time.sleep(0.02)
        wd.progress("walk", completed=i + 1, total=3)
    assert seen[-1]["completed_units"] == 3
    assert seen[-1]["progress_revision"] == 3


def test_cooperative_cancel_is_checked_at_progress_boundaries():
    event = threading.Event()
    wd = ProgressWatchdog(cancel_event=event)
    event.set()
    try:
        wd.progress("extract", increment=1)
    except OperationCancelled:
        pass
    else:
        raise AssertionError("cancel request was ignored")


def test_graph_status_partial_scan_never_claims_fresh(tmp_path, monkeypatch):
    fake = {"nodes": 1, "edges": 0, "files": 5,
            "last_run": {"finished_at": 1, "status": "completed"}}
    conn = mock.MagicMock()
    conn.execute.return_value.fetchone.return_value = ("wal",)
    monkeypatch.setattr(graph_status._store, "graph_db_path", lambda _: str(tmp_path / "graph.db"))
    (tmp_path / "graph.db").write_bytes(b"x")
    monkeypatch.setattr(graph_status._store, "get_db", lambda *a, **k: conn)
    monkeypatch.setattr(graph_status._store, "integrity_check", lambda *_: "ok")
    monkeypatch.setattr(graph_status._store, "status_counts", lambda *_: fake)
    monkeypatch.setattr(graph_status, "_stale_count", lambda *a, **k: {
        "stale_files": 0, "files_checked": 1, "files_total": 5,
        "complete": False, "stop_reason": "response_budget"})
    result = graph_status.graph_status(str(tmp_path))
    assert result["freshness"] == "unknown"
    assert result["stale_scan"]["complete"] is False


def test_write_status_surfaces_progress():
    op = "watchdog-progress-test"
    srv._WRITE_LEDGER[op] = {"operation_id": op, "tool": "cbm_index",
                             "status": "in_flight", "attempts": 1,
                             "origin_generation": srv.SERVER_GENERATION}
    try:
        srv._write_ledger_heartbeat(op, {"phase": "extract", "completed_units": 7,
                                         "total_units": 10, "progress_revision": 2})
        request = type("R", (), {"headers": {}, "query": {"operation_id": op}})()
        payload = json.loads(asyncio.run(srv.handle_write_status(request)).text)
        assert payload["commit_state"] == "in_flight"
        assert payload["operation_state"] == "running"
        assert payload["progress"]["completed_units"] == 7
    finally:
        srv._WRITE_LEDGER.pop(op, None)


def test_write_status_marks_no_progress_as_stalled(monkeypatch):
    op = "watchdog-stall-test"
    now = time.time()
    srv._WRITE_LEDGER[op] = {"operation_id": op, "tool": "cbm_index",
                             "status": "in_flight", "attempts": 1,
                             "origin_generation": srv.SERVER_GENERATION,
                             "last_progress_at": now - 500,
                             "progress": {"phase": "extract", "progress_revision": 4}}
    request = type("R", (), {"headers": {}, "query": {"operation_id": op}})()
    try:
        payload = json.loads(asyncio.run(srv.handle_write_status(request)).text)
        assert payload["commit_state"] == "in_flight"
        assert payload["operation_state"] == "stalled"
        assert payload["safe_to_retry"] is False
    finally:
        srv._WRITE_LEDGER.pop(op, None)


def test_long_index_has_no_wall_clock_budget(monkeypatch, tmp_path):
    from orchestrator.graph import indexer
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    # A zero legacy budget would have forced partial immediately before the fix.
    monkeypatch.setattr(indexer, "INDEX_TIME_BUDGET_FULL_S", 0)
    result = indexer.index_project(str(tmp_path), scope="full")
    assert result["status"] == "completed"
    assert result["budget_s"] is None
    assert result["meets_gates"]["duration"] is True


def test_cancel_endpoint_sets_cooperative_event():
    op = "watchdog-cancel-test"
    event = threading.Event()
    srv._OPERATION_CANCEL_EVENTS[op] = event
    request = mock.MagicMock()
    request.headers = {}
    request.json = mock.AsyncMock(return_value={"operation_id": op})
    try:
        payload = json.loads(asyncio.run(srv.handle_cancel_operation(request)).text)
        assert payload["ok"] and payload["cancelled"]
        assert event.is_set()
    finally:
        srv._OPERATION_CANCEL_EVENTS.pop(op, None)
