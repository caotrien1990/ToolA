"""P-47: alert-time frames belong to the holder, and queued logs are immutable."""
import asyncio
import logging
import threading
import time

import pytest
from orchestrator import lock_holder_trace as trace
from orchestrator.server import executor_lanes as lanes
from orchestrator.session import store


@pytest.fixture
def camera(tmp_path, monkeypatch):
    monkeypatch.setattr(trace, "_ENABLED", True)
    monkeypatch.setenv("ORCHESTRATOR_SESSIONS_DB", str(tmp_path / "sessions.db"))
    with trace._REG_LOCK:
        trace._REG.clear()
    yield tmp_path / "sessions.db"
    with trace._REG_LOCK:
        trace._REG.clear()


def test_alert_keeps_identity_when_commit_clears_live_record(camera, monkeypatch, caplog):
    conn = trace.traced_connect(camera)
    token = trace.set_current_tool("p47-owner")
    try:
        conn.execute("BEGIN IMMEDIATE")
        rec = trace._REG[id(conn)]
        rec.opened_at = time.monotonic() - 3
        original = trace._log_holder
        def commit_before_logging(snapshot, age):
            conn.commit()
            original(snapshot, age)
        monkeypatch.setattr(trace, "_log_holder", commit_before_logging)
        with caplog.at_level(logging.WARNING, logger=trace.__name__):
            trace._watchdog_tick()
        text = caplog.text
        assert "tool=p47-owner" in text
        assert "thread=MainThread" in text
    finally:
        trace.reset_current_tool(token)
        conn.close()


def test_alert_reads_holder_live_frame_not_watchdog(camera, monkeypatch, caplog):
    ready, finish = threading.Event(), threading.Event()
    errors = []
    def actual_holder():
        try:
            conn = trace.traced_connect(camera)
            token = trace.set_current_tool("p47-tool")
            try:
                conn.execute("BEGIN IMMEDIATE")
                with trace._REG_LOCK:
                    trace._REG[id(conn)].opened_at = time.monotonic() - 3
                ready.set()
                finish.wait(5)
                conn.commit()
            finally:
                trace.reset_current_tool(token)
                conn.close()
        except BaseException as exc:
            errors.append(exc)
            ready.set()
    thread = threading.Thread(target=actual_holder, name="p47-real-holder")
    thread.start()
    try:
        assert ready.wait(3)
        with caplog.at_level(logging.WARNING, logger=trace.__name__):
            trace._watchdog_tick()
        assert "thread=p47-real-holder" in caplog.text
        assert "actual_holder" in caplog.text
        assert "live_stack=" in caplog.text
        assert "_watchdog_tick" not in caplog.text
    finally:
        finish.set()
        thread.join(5)
    assert not errors and not thread.is_alive()


def test_executor_propagates_and_resets_camera_tool(camera):
    async def scenario():
        await lanes.shutdown_executor_lanes(drain_timeout=1)
        lanes._reset_executor_lanes_for_tests()
        token = trace.set_current_tool("p47-dispatch-tool")
        try:
            assert await lanes.run_in_executor_lane("read_io", trace.current_tool) == "p47-dispatch-tool"
        finally:
            trace.reset_current_tool(token)
        assert await lanes.run_in_executor_lane("read_io", trace.current_tool) is None
        await lanes.shutdown_executor_lanes(drain_timeout=1)
        lanes._reset_executor_lanes_for_tests()
    asyncio.run(scenario())


def test_real_store_transaction_names_holder_after_2_and_30_seconds(camera, monkeypatch, caplog):
    # Real wall-clock thresholds, real store transaction; no daemon/live DB.
    monkeypatch.setattr(store, "_SESSIONS_DB", camera)
    monkeypatch.setattr(trace, "_installed", False)
    monkeypatch.setattr(store.sqlite3, "connect", trace.traced_connect)
    writer = store._get_session_writer()
    entered, finish = threading.Event(), threading.Event()
    errors = []
    def hold(conn):
        entered.set()
        # Keep the independent writer-liveness watchdog informed while the
        # transaction still holds the real DB lock for the camera thresholds.
        while not finish.wait(.2):
            writer._record_progress()
        return 1
    def submit():
        try:
            writer.submit("p47-real-store-tool", hold, durable=True, wait_timeout=40)
        except BaseException as exc:
            errors.append(exc)
    caller = threading.Thread(target=submit)
    with caplog.at_level(logging.WARNING, logger=trace.__name__):
        caller.start()
        try:
            assert entered.wait(3)
            until = time.monotonic() + 31.2
            while time.monotonic() < until:
                trace._watchdog_tick()
                time.sleep(.2)
        finally:
            finish.set()
            caller.join(5)
            store.close_writer()
    assert not errors and not caller.is_alive()
    warnings = [r.getMessage() for r in caplog.records if "txn_open>" in r.getMessage()]
    cues = [r.getMessage() for r in caplog.records if "lock_hold>" in r.getMessage()]
    assert len(warnings) == len(cues) == 1
    for text in warnings + cues:
        assert "tool=p47-real-store-tool" in text
        assert "thread=None" not in text
        assert "live_stack=" in text and "hold" in text
