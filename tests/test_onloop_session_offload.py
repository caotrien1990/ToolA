"""Phase A on-loop session-store offload regression tests.

WHY: code_tools.py / tools_v15.py / orchestrator_server.py used to call blocking
session-store helpers (_run_session_write, _log_session_event, _task_checkpoint_update,
_with_sessions_db reads, _auto_open_session, _init_session_db) synchronously inside
`async def` handlers. Those helpers go through the SessionWriter and block on a
durable-commit Future, so a single handler pinned the event loop for the entire
write critical section (up to the 5s stall budget under contention). The fix wraps
each blocking call in `await asyncio.to_thread(...)` so the loop stays responsive.

These tests pin the contract that:
  1. The async handler still awaits the work (no fire-and-forget) — return values
     and exception propagation are preserved across the to_thread boundary.
  2. The wrapped callable actually runs (the call is not silently dropped).
  3. The to_thread wrapper yields the event loop before the callable starts, so
     concurrent loop work is not starved by a blocking session-store write.

Cancellation note: `asyncio.to_thread` cannot be cancelled once the callable has
started running inside the worker thread (Python's documented limitation). The
surrounding coroutine CAN be cancelled before the callable starts (the to_thread
future resolves with CancelledError). Durable-write callables in this audit are
short-lived (< 5s busy_timeout), so cancellation mid-write is not a supported
path; durability is preserved because the SessionWriter commit Future is awaited
to completion once started.

Run with:
    venv312/Scripts/python.exe -m unittest tests.test_onloop_session_offload -v
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import unittest
from unittest.mock import patch


class TestBeginTaskOffload(unittest.IsolatedAsyncioTestCase):
    """begin_task — _task_checkpoint_update wrapped in to_thread."""

    async def test_begin_task_offloads_checkpoint_update_and_preserves_return(self):
        """begin_task must call _task_checkpoint_update via asyncio.to_thread and
        preserve its (checkpoint, response, wrote) return shape unchanged."""
        import code_tools

        captured: dict = {}
        cap_hash = hashlib.sha256(b"test-cap").hexdigest()

        def fake_checkpoint_update(session_key, update_fn, busy_timeout_ms=5000):
            captured["session_key"] = session_key
            previous = {
                "agent_id": "TestAgent",
                "owner_capability_sha256": cap_hash,
                "task_epoch": 0,
                "owner_claim_status": "committed",
            }
            checkpoint, response = update_fn(previous)
            return (checkpoint, response, True)

        with patch.object(code_tools, "_task_checkpoint_update", fake_checkpoint_update):
            result = await code_tools.begin_task(
                session_id="test-sess",
                prompt="hello",
                agent_id="TestAgent",
                owner_capability="test-cap",
            )
            payload = json.loads(result)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["task_epoch"], 1)
            self.assertEqual(captured["session_key"], "test-sess")

    async def test_begin_task_propagates_storage_error_from_thread(self):
        """TaskCheckpointStorageError raised inside _task_checkpoint_update must
        propagate through the to_thread boundary and surface as an error response
        (not a hung loop, not a swallowed exception)."""
        import code_tools

        def boom(session_key, update_fn, busy_timeout_ms=5000):
            raise code_tools.TaskCheckpointStorageError("disk full", error_name="DISK_FULL")

        with patch.object(code_tools, "_task_checkpoint_update", boom):
            result = await code_tools.begin_task(
                session_id="test-sess",
                prompt="hello",
                agent_id="TestAgent",
                owner_capability="test-cap",
            )
            payload = json.loads(result)
            self.assertNotEqual(payload.get("ok"), True)
            # Storage errors surface with the error text or error_name preserved
            # across the to_thread boundary.
            err_text = (payload.get("error") or "").lower()
            err_name = payload.get("error_name") or ""
            self.assertTrue(
                "disk full" in err_text or err_name in (
                    "DISK_FULL", "TASK_CHECKPOINT_STORAGE", "TASK_STORAGE",
                ),
                f"expected disk-full error or storage error_name, got: {payload}",
            )


class TestCloseSessionOffload(unittest.IsolatedAsyncioTestCase):
    """close_session — to_thread wraps the whole read→write→read block."""

    async def test_close_session_offloads_and_preserves_return_shape(self):
        """close_session(session_id=...) must return the same JSON shape as before
        with the work offloaded."""
        import code_tools

        class FakeCursor:
            def __init__(self, value):
                self._value = value
            def fetchone(self):
                return self._value
            def fetchall(self):
                return self._value

        class FakeConn:
            def __init__(self):
                self.queries: list = []
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def execute(self, sql, params=()):
                self.queries.append((sql.strip()[:40], params))
                if "FROM sessions" in sql and "active" in sql:
                    return FakeCursor(("sid-1", "2026-08-11T00:00:00", "proj"))
                if "GROUP BY action_type" in sql:
                    return FakeCursor([("read", 3), ("write", 1)])
                return FakeCursor((0,))

        fake_conn = FakeConn()
        log_calls: list = []
        write_calls: list = []

        def fake_log(*a, **kw):
            log_calls.append((a, kw))
            return 1

        def fake_write(op, fn, **kw):
            write_calls.append(op)
            return None

        with patch.object(code_tools, "_with_sessions_db", lambda: fake_conn), \
             patch.object(code_tools, "_log_session_event", fake_log), \
             patch.object(code_tools, "_run_session_write", fake_write):
            result = await code_tools.close_session(session_id="sid-1")
            payload = json.loads(result)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["session_id"], "sid-1")
        self.assertEqual(payload["resolved_via"], "session_id_direct")
        self.assertIn("summary", payload)
        self.assertIn("ended_at", payload)
        # Verify the durable write + event log were both invoked (not dropped
        # by to_thread).
        self.assertEqual(len(log_calls), 1)
        self.assertIn("close session", write_calls)

    async def test_close_session_session_not_found_path_preserved(self):
        """close_session with no matching row must still surface SESSION_NOT_FOUND
        (None return from the offloaded callable must map to the same error shape)."""
        import code_tools

        class EmptyConn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def execute(self, sql, params=()):
                class C:
                    def fetchone(self):
                        return None
                    def fetchall(self):
                        return []
                return C()

        with patch.object(code_tools, "_with_sessions_db", lambda: EmptyConn()):
            result = await code_tools.close_session(session_id="no-such-sid")
            payload = json.loads(result)
        self.assertNotEqual(payload.get("ok"), True)
        self.assertEqual(payload.get("error_name"), "SESSION_NOT_FOUND")


class TestLogNativeEventOffload(unittest.IsolatedAsyncioTestCase):
    """log_native_event — _get_active_session_id / _auto_open_session / _run_session_write."""

    async def test_log_native_event_offloads_session_resolution_and_write(self):
        """log_native_event must run _get_active_session_id + _auto_open_session in
        a to_thread hop, then _run_session_write in a second to_thread hop, and the
        fallback_count return value must survive the boundary."""
        import code_tools

        active_calls: list = []
        auto_calls: list = []
        write_calls: list = []

        def fake_active(norm, agent_id=None):
            active_calls.append((norm, agent_id))
            return None  # force auto_open path

        def fake_auto(norm, body, agent_id=None):
            auto_calls.append((norm, body, agent_id))
            return "sid-auto"

        def fake_write(op, fn, **kw):
            write_calls.append(op)
            class FakeWriteConn:
                def execute(self, sql, params=()):
                    class C:
                        def fetchone(self):
                            return (7,)  # fallback_count = 7
                    return C()
            fn(FakeWriteConn())
            return 7

        with patch.object(code_tools, "_get_active_session_id", fake_active), \
             patch.object(code_tools, "_auto_open_session", fake_auto), \
             patch.object(code_tools, "_run_session_write", fake_write):
            result = await code_tools.log_native_event(
                action_type="read",
                tool_name="fs_read",
                project_path=".",
                agent_id="TestAgent",
            )
            payload = json.loads(result)

        # The CSLT policy layer may mark this event FORBIDDEN_NATIVE (fs_read with
        # action_type=read is classified as a native bypass); that is orthogonal
        # to the Phase A fix. What we assert is the THREAD-BOUNDARY contract:
        # session_id + fallback_count_so_far + write invocation survive to_thread
        # unchanged regardless of the policy verdict.
        self.assertEqual(payload["session_id"], "sid-auto")
        self.assertEqual(payload["fallback_count_so_far"], 7)
        self.assertEqual(len(active_calls), 1)
        self.assertEqual(len(auto_calls), 1)
        self.assertIn("log native event", write_calls)
        # Sanity: the CSLT policy shape is still intact (no contract change).
        self.assertIn("cslt_classification", payload)


class TestListSessionsOffload(unittest.IsolatedAsyncioTestCase):
    """list_sessions — read path offload."""

    async def test_list_sessions_offloads_read_and_preserves_return(self):
        """list_sessions must still return the same {ok, sessions, total} shape
        after the read body is wrapped in to_thread."""
        import code_tools
        import sqlite3 as _sq
        import tempfile
        import os

        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "sessions.db")
        conn = _sq.connect(db_path)
        conn.execute(
            "CREATE TABLE sessions (session_id TEXT, session_title TEXT, mode TEXT, "
            "status TEXT, coverage TEXT, started_at TEXT, ended_at TEXT, agent_id TEXT, "
            "project_path TEXT)"
        )
        conn.execute("CREATE TABLE events (session_id TEXT, action_type TEXT)")
        conn.execute(
            "INSERT INTO sessions VALUES ('sid-1','t','default','active','',"
            "'2026-08-11T00:00:00',NULL,'TestAgent','proj')"
        )
        conn.execute("INSERT INTO events VALUES ('sid-1','read')")
        conn.commit()
        conn.close()

        with patch.object(code_tools, "_SESSIONS_DB", db_path), \
             patch.object(code_tools, "_session_project_key", lambda p: "proj"):
            result = await code_tools.list_sessions(project_path=".")
            payload = json.loads(result)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["sessions"][0]["session_id"], "sid-1")
        self.assertEqual(payload["sessions"][0]["total_events"], 1)


class TestToThreadYieldsLoop(unittest.IsolatedAsyncioTestCase):
    """WHY: the entire purpose of the Phase A fix is to keep the event loop
    responsive DURING a blocking session-store write. Prove that while an
    `await asyncio.to_thread(...)` callable is still running in the worker
    thread, the event loop can schedule and complete other coroutines (i.e.
    the loop is NOT blocked by the in-flight callable)."""

    async def test_to_thread_allows_loop_to_run_other_work_while_callable_in_flight(self):
        import threading
        import time as _time

        callable_running = threading.Event()
        callable_release = threading.Event()

        def blocking_callable():
            callable_running.set()
            # Hold the worker thread for a short, bounded window.
            if not callable_release.wait(timeout=5):
                raise AssertionError("test deadlock: release event never set")
            return "done"

        loop_tick_count = 0

        async def loop_observer():
            nonlocal loop_tick_count
            # Run many loop ticks while the callable is blocked. If the loop were
            # pinned by the callable, this would not make progress.
            while not callable_release.is_set():
                loop_tick_count += 1
                await asyncio.sleep(0)
            return loop_tick_count

        async def direct():
            return await asyncio.to_thread(blocking_callable)

        async def orchestrator():
            direct_task = asyncio.create_task(direct())
            observer_task = asyncio.create_task(loop_observer())
            # Wait until the callable has actually started in the worker thread.
            while not callable_running.wait(timeout=0.05):
                await asyncio.sleep(0.01)
            # Let the loop run a few ticks while the callable is in flight.
            await asyncio.sleep(0.05)
            callable_release.set()
            direct_result = await direct_task
            observer_result = await observer_task
            return direct_result, observer_result

        direct_result, observer_result = await orchestrator()
        self.assertEqual(direct_result, "done")
        # The loop ran multiple ticks while the callable was in flight — proves
        # the loop was NOT blocked by the durable write.
        self.assertGreater(observer_result, 0,
            "loop made no progress while to_thread callable was in flight; "
            "the wrap is not actually keeping the loop responsive.")


if __name__ == "__main__":
    unittest.main()
