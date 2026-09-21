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

    # ISS-05: the integrity probe is now `PRAGMA quick_check` on the connection
    # we already hold, not a second connection through _store.integrity_check.
    # One blanket fetchone would answer "wal" to quick_check and send the whole
    # call down the freshness=corrupt branch, so route by statement.
    def _execute(sql, *_args, **_kwargs):
        row = ("ok",) if "quick_check" in str(sql).lower() else ("wal",)
        return mock.MagicMock(fetchone=mock.MagicMock(return_value=row))

    conn.execute.side_effect = _execute
    monkeypatch.setattr(graph_status._store, "graph_db_path", lambda _: str(tmp_path / "graph.db"))
    (tmp_path / "graph.db").write_bytes(b"x")
    monkeypatch.setattr(graph_status._store, "get_db", lambda *a, **k: conn)
    monkeypatch.setattr(graph_status._store, "status_counts", lambda *_: fake)
    monkeypatch.setattr(graph_status, "_stale_count", lambda *a, **k: {
        "stale_files": 0, "files_checked": 1, "files_total": 5,
        "complete": False, "stop_reason": "response_budget"})
    result = graph_status.graph_status(str(tmp_path))
    assert result["freshness"] == "unknown"
    assert result["stale_scan"]["complete"] is False
    # ISS-05: the probe is disclosed, and the scan keeps a floor budget instead of
    # inheriting whatever a slow integrity check left behind (which on an HDD was
    # ~0ms, turning every answer into a permanent freshness=unknown).
    assert result["integrity_probe"] == "quick_check"
    assert result["stale_scan"]["budget_s"] >= graph_status.CBM_STATUS_SCAN_MIN_BUDGET_S
    assert "pre_scan_ms" in result["stale_scan"]


def _gap_c_status(tmp_path, monkeypatch, counts_delay=0.0):
    """Run graph_status against a mocked db, like the ISS-05 test above.

    Deliberately a separate helper rather than a refactor of that test: it pins a
    different defect, and rewriting it to share a fixture would put both at risk.
    counts_delay makes the PRE-SCAN phase cost real wall-clock, which is the only way
    to reach the overrun branch on Windows, where time.monotonic() has ~15.6ms
    granularity and an all-mock pre-scan can measure exactly 0.0ms.
    """
    conn = mock.MagicMock()

    def _execute(sql, *_args, **_kwargs):
        row = ("ok",) if "quick_check" in str(sql).lower() else ("wal",)
        return mock.MagicMock(fetchone=mock.MagicMock(return_value=row))

    def _counts(*_args):
        if counts_delay:
            time.sleep(counts_delay)
        return {"nodes": 1, "edges": 0, "files": 5,
                "last_run": {"finished_at": 1, "status": "completed"}}

    conn.execute.side_effect = _execute
    monkeypatch.setattr(graph_status._store, "graph_db_path",
                        lambda _: str(tmp_path / "graph.db"))
    (tmp_path / "graph.db").write_bytes(b"x")
    monkeypatch.setattr(graph_status._store, "get_db", lambda *a, **k: conn)
    monkeypatch.setattr(graph_status._store, "status_counts", _counts)
    monkeypatch.setattr(graph_status, "_stale_count", lambda *a, **k: {
        "stale_files": 0, "files_checked": 5, "files_total": 5,
        "complete": True, "stop_reason": None})
    return graph_status.graph_status(str(tmp_path)), conn


def test_integrity_budget_wires_its_interval_and_publishes_the_enforcement(
        tmp_path, monkeypatch):
    """Gap C: integrity_budget_s alone reads as a ceiling this code cannot honour.

    MEASURED 2026-09-21 on the ToolQ graph db (spinning D:): the live tool answered
    integrity_ms=3828 for a declared 1000ms, and then discarded the result as
    "unknown" -- 2.8s of pure waste. A PRAGMA takes no deadline, so the budget is
    enforced only by a progress handler that runs BETWEEN vm opcodes: at the old
    interval of 2000 it fired 9 times in 1172ms, max gap 828ms. The interval IS the
    resolution of the budget, so both halves are pinned here -- the value actually
    handed to set_progress_handler (a hardcoded literal is how the resolution would
    silently go back to 828ms) and the disclosure that the bound is best-effort.
    """
    result, conn = _gap_c_status(tmp_path, monkeypatch)
    interval = graph_status.CBM_STATUS_INTEGRITY_OPCODE_INTERVAL
    armed = [c for c in conn.set_progress_handler.call_args_list if c.args[1]]
    assert armed, "integrity probe ran with no progress handler: nothing bounds it"
    assert armed[0].args[1] == interval, (
        "handler armed with %r, not CBM_STATUS_INTEGRITY_OPCODE_INTERVAL=%r"
        % (armed[0].args[1], interval))
    assert str(interval) in result["integrity_budget_enforcement"], (
        "integrity_budget_enforcement must be built from the live constant or it keeps"
        " advertising an interval the code no longer uses")
    assert "best_effort" in result["integrity_budget_enforcement"]


def test_scan_budget_discloses_which_branch_produced_it(tmp_path, monkeypatch):
    """Gap C: a floored scan budget was indistinguishable from a healthy remainder.

    MEASURED 2026-09-21 on ToolQ: pre_scan_ms=6328 against a 2.0s response budget, so
    `budget - elapsed` went negative, max() returned CBM_STATUS_SCAN_MIN_BUDGET_S, and
    budget_s read 1.0 -- exactly what a healthy remainder looks like. elapsed_ms was
    6843, so a CA sizing its client timeout off the declared 2.0s abandons a call that
    completed. Both branches are asserted: either one alone would also pass on a
    constant expression that never looks at the budget at all.
    """
    monkeypatch.setattr(graph_status, "CBM_STATUS_RESPONSE_BUDGET_S", 0.0)
    over, _ = _gap_c_status(tmp_path, monkeypatch, counts_delay=0.05)
    assert over["stale_scan"]["budget_source"] == "scan_min_floor"
    assert over["stale_scan"]["budget_overrun_ms"] > 0.0
    assert over["stale_scan"]["budget_s"] == graph_status.CBM_STATUS_SCAN_MIN_BUDGET_S

    monkeypatch.setattr(graph_status, "CBM_STATUS_RESPONSE_BUDGET_S", 3600.0)
    under, _ = _gap_c_status(tmp_path, monkeypatch)
    assert under["stale_scan"]["budget_source"] == "response_budget_remainder"
    assert "budget_overrun_ms" not in under["stale_scan"]

def test_auto_reindex_skips_incomplete_stale_scan(monkeypatch):
    from orchestrator.graph import query
    conn = mock.MagicMock()
    monkeypatch.setattr(query._store, "get_db", lambda *a, **k: conn)
    monkeypatch.setattr(query, "_stale_count", lambda *a, **k: {
        "stale_files": 2, "files_checked": 5, "files_total": 100,
        "complete": False, "stop_reason": "response_budget"})
    with mock.patch.object(query, "incremental_index") as index:
        result = query._auto_reindex_if_stale("D:/repo", budget_s=0.01)
    index.assert_not_called()
    assert result["error"] == "stale_scan_incomplete"


def test_analysis_view_incomplete_stale_scan_is_unavailable(tmp_path, monkeypatch):
    from orchestrator.graph import analysis_view
    from orchestrator.graph import indexer
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    indexer.index_project(str(tmp_path), scope="full")
    monkeypatch.setattr(analysis_view._status, "_stale_count", lambda *a, **k: {
        "stale_files": 0, "files_checked": 0, "files_total": 1,
        "complete": False, "stop_reason": "response_budget"})
    result = analysis_view.build_analysis_view(str(tmp_path))
    assert result["status"] == "unavailable"
    assert result["freshness"] == "unknown"
    assert result["status_error"] == "stale_scan_incomplete"


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
