from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import code_tools
import orchestrator_server as server
from orchestrator.tools.read import context as read_context


class ExecutorAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.category = "backup"
        server._TOOL_SEMS.pop(self.category, None)
        server._SEMAPHORE_HOLDERS.pop(self.category, None)
        server._SEMAPHORE_WAITERS.pop(self.category, None)
        server._SEMAPHORE_WAITER_STARTED.pop(self.category, None)
        server._SEMAPHORE_REJECTED.pop(self.category, None)

    async def asyncTearDown(self):
        sem = server._TOOL_SEMS.pop(self.category, None)
        if sem is not None:
            while server._SEMAPHORE_HOLDERS.get(self.category):
                operation_id = next(iter(server._SEMAPHORE_HOLDERS[self.category]))
                server._release_tool_semaphore(sem, self.category, operation_id)
        server._SEMAPHORE_HOLDERS.pop(self.category, None)
        server._SEMAPHORE_WAITERS.pop(self.category, None)
        server._SEMAPHORE_WAITER_STARTED.pop(self.category, None)
        server._SEMAPHORE_REJECTED.pop(self.category, None)

    async def _wait_until(self, predicate, timeout=1.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() >= deadline:
                self.fail("condition was not reached before timeout")
            await asyncio.sleep(0.005)

    async def test_admission_rejects_when_bounded_wait_queue_is_full(self):
        # WHY unchanged: validate the bounded-wait-queue rejection mechanism
        # (holder fills a slot, waiter queues, overflow -> TOOL_QUEUE_FULL).
        # Pin ORCH_SEM_BACKUP=1 so exactly one slot exists and the "waiter" is
        # forced to queue regardless of the default backup concurrency (now 2
        # so concurrent HTTP backups reach the per-project fast-reject). Pins
        # queue=1 so only one waiter fits and the third call overflows.
        body = {"project_path": "D:/fixture"}
        with mock.patch.dict(os.environ, {"ORCH_SEM_BACKUP": "1", "ORCH_QUEUE_BACKUP": "1"}):
            sem, category = await server._acquire_tool_semaphore(
                "backup_project", body, "holder")
            waiter = asyncio.create_task(server._acquire_tool_semaphore(
                "backup_project", body, "waiter"))
            await self._wait_until(
                lambda: server._SEMAPHORE_WAITERS.get(category) == 1)

            with self.assertRaises(server.ToolAdmissionOverloaded) as caught:
                await server._acquire_tool_semaphore(
                    "backup_project", body, "overflow")

            payload = caught.exception.as_payload()
            self.assertEqual(payload["error_name"], "TOOL_QUEUE_FULL")
            self.assertTrue(payload["retryable"])
            row = server._admission_status(category)
            self.assertEqual(row["held"], 1)
            self.assertEqual(row["waiters"], 1)
            self.assertEqual(row["queue_capacity"], 1)
            self.assertEqual(row["queue_rejected"], 1)
            self.assertGreaterEqual(row["oldest_wait_ms"], 0)

            server._release_tool_semaphore(sem, category, "holder")
            waiter_sem, waiter_category = await waiter
            server._release_tool_semaphore(
                waiter_sem, waiter_category, "waiter")

    async def test_health_exposes_executor_queue_metrics(self):
        response = await server.handle_health(SimpleNamespace(headers={}))
        payload = json.loads(response.text)
        self.assertIn("executor_lanes", payload)
        self.assertIn("executor_active_total", payload)
        self.assertIn("executor_queued_total", payload)
        names = {row["name"] for row in payload["executor_lanes"]}
        self.assertTrue({"read_io", "command", "test", "git", "backup"}.issubset(names))

    async def test_lane_overload_http_contract_and_health_remains_direct(self):
        # WHY: overload must become a bounded HTTP response while health bypasses lanes.
        from orchestrator.server.executor_lanes import ExecutorLaneQueueWaitExceeded
        metrics = {"queue_wait_timeout_seconds": 0.2, "active": 1, "queued": 1}
        async def overloaded_tool():
            raise ExecutorLaneQueueWaitExceeded("read_io", "queue budget exceeded", metrics)
        with mock.patch.dict(server.ct.TOOL_MAP, {"unit_lane_overload": overloaded_tool}, clear=False):
            response = await server.dispatch_tool_call("unit_lane_overload", {}, transport="mcp")
        payload = json.loads(response.text)
        self.assertEqual(response.status, 503)
        self.assertEqual(response.headers.get("Retry-After"), "1")
        self.assertEqual(payload["error_name"], "LANE_QUEUE_WAIT_EXCEEDED")
        self.assertEqual(payload["lane"], "read_io")
        self.assertTrue(payload["retryable"])
        self.assertIn("do_not_retry_immediately_or_unchanged", payload["ca_next_action"])
        health = await server.handle_health(SimpleNamespace(headers={}))
        self.assertIn(health.status, {200, 503})
        self.assertIn("executor_lanes", json.loads(health.text))


class BackupHttpAdmissionTests(unittest.IsolatedAsyncioTestCase):
    """HTTP/dispatch-level regression for the backup admission gate.

    These tests exercise the REAL ``_dispatch_tool_call_request`` admission
    semaphore (via ``dispatch_tool_call``), not just ``_acquire_tool_semaphore``
    directly, so they catch the original High defect: a global 1-slot "backup"
    semaphore blocking concurrent HTTP ``backup_project`` calls BEFORE they
    could reach code_tools' per-project fast-reject / lane routing.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root_a = Path(self._tmp.name) / "proj_a"
        root_b = Path(self._tmp.name) / "proj_b"
        (root_a / "src").mkdir(parents=True)
        root_b.mkdir(parents=True)
        (root_a / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
        self.root_a = str(root_a)
        self.root_b = str(root_b)
        self._cleanup_locks = []

    def tearDown(self):
        for key in self._cleanup_locks:
            code_tools._BACKUP_PROJECT_LOCKS.pop(key, None)
        for attr in ("_TOOL_SEMS", "_SEMAPHORE_HOLDERS", "_SEMAPHORE_WAITERS",
                     "_SEMAPHORE_WAITER_STARTED", "_SEMAPHORE_REJECTED"):
            getattr(server, attr).pop("backup", None)

    async def _async_wait(self, event: threading.Event, timeout: float = 5.0) -> bool:
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, event.wait, timeout), timeout + 0.5)

    def _gate_patches(self):
        """Context manager that neutralizes token/session/ledger gates so the
        dispatch reaches the admission semaphore and the mocked tool."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            with mock.patch.object(server, "AUTO_WRITE_MODE", True), \
                    mock.patch.object(server.ct, "_auto_open_session", lambda *a, **k: "sess-test"), \
                    mock.patch.object(server.ct, "_get_active_session_id", lambda *a, **k: "sess-test"), \
                    mock.patch.object(code_tools, "_auto_open_session", lambda *a, **k: "sess-test"), \
                    mock.patch.object(code_tools, "_get_active_session_id", lambda *a, **k: "sess-test"), \
                    mock.patch.object(server.ct, "_log_usage", lambda *a, **k: None), \
                    mock.patch.object(server, "_write_ledger_begin", lambda *a, **k: ""), \
                    mock.patch.object(server, "_write_ledger_finish", lambda *a, **k: None):
                yield

        return _ctx()

    def _lock_key(self, root: str) -> str:
        return str(Path(root).resolve())

    async def _run_concurrent(self, num_calls: int):
        """Drive num_calls same-project full backups through the dispatch
        admission path. Call 0 must hold its slot; the others must each return
        BACKUP_IN_PROGRESS immediately (i.e. reach the per-project lock)."""
        gate = threading.Event()
        blocked_entered = threading.Event()
        failures = []
        outcomes = []

        async def fake_backup(project_path: str, **kw) -> str:
            lock = code_tools._backup_lock_for(self._lock_key(project_path))
            if lock.locked():
                # WHy: this is the code_tools fast-reject path. With the fixed
                # (2-slot) admission, contending calls REACH here instead of
                # queueing behind the first holder on the global semaphore.
                return json.dumps({"ok": False, "error_name": "BACKUP_IN_PROGRESS"})
            async with lock:
                if len(outcomes) == 0:
                    # First caller: hold the gate until all contenders have run.
                    blocked_entered.set()
                    observed = await self._async_wait(gate, timeout=8.0)
                    if not observed:
                        failures.append("gate was never released (deadlock)")
                return json.dumps({"ok": True, "skipped": True})

        tasks = []
        with self._gate_patches(), \
                mock.patch.dict(server.ct.TOOL_MAP, {"backup_project": fake_backup}, clear=False):
            # Pre-register the lock so the same event-loop Lock instance is reused.
            code_tools._backup_lock_for(self._lock_key(self.root_a))
            self._cleanup_locks.append(self._lock_key(self.root_a))
            for _ in range(num_calls):
                tasks.append(asyncio.create_task(server.dispatch_tool_call(
                    "backup_project", {"project_path": self.root_a}, transport="mcp")))
            # Wait until the first holder is inside its lock, then give every
            # contender a scheduler chance to run.
            await self._async_wait(blocked_entered, timeout=5.0)
            for _ in range(200):
                if len(outcomes) >= num_calls:
                    break
                await asyncio.sleep(0.005)
            gate.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 10.0)
            for r in results:
                outcomes.append(json.loads(r.text))
        return outcomes, failures

    async def test_second_concurrent_same_project_full_backup_reaches_fast_reject(self):
        # WHY: with a 1-slot global "backup" semaphore, calls 2..N blocked at
        # HTTP admission and NEVER reached the per-project lock, so callers
        # waited (or 429'd) instead of getting the immediate structured
        # BACKUP_IN_PROGRESS the code_tools fix was designed to return.
        outcomes, failures = await self._run_concurrent(3)
        self.assertFalse(failures, "; ".join(failures))
        in_progress = [o for o in outcomes if o.get("error_name") == "BACKUP_IN_PROGRESS"]
        self.assertEqual(
            len(in_progress), 2,
            f"expected 2 contending calls to reach BACKUP_IN_PROGRESS, got {outcomes}")
        successes = [o for o in outcomes if o.get("ok")]
        self.assertEqual(len(successes), 1, f"expected exactly one real backup, got {outcomes}")

    async def test_targeted_backup_not_blocked_by_full_backup_other_project(self):
        # WHY: with limit 1 a targeted backup (which the code_tools fix routes
        # to the wide read_io lane) could not even be ADMITTED while a full
        # backup on another project held the single global slot. Two slots let
        # the targeted call proceed concurrently.
        gate = threading.Event()
        holder_entered = threading.Event()
        entered_targeted = threading.Event()
        lock_a = code_tools._backup_lock_for(self._lock_key(self.root_a))
        self._cleanup_locks.extend([self._lock_key(self.root_a), self._lock_key(self.root_b)])

        async def holder(project_path: str, **kw) -> str:
            async with lock_a:
                holder_entered.set()
                observed = await self._async_wait(gate, timeout=8.0)
                self.assertTrue(observed, "holder gate never released")
                return json.dumps({"ok": True})

        async def targeted(project_path: str, **kw) -> str:
            entered_targeted.set()
            return json.dumps({"ok": True, "backup_scope": "targeted"})

        with self._gate_patches():
            with mock.patch.dict(
                    server.ct.TOOL_MAP, {"backup_project": holder}, clear=False):
                holder_task = asyncio.create_task(server.dispatch_tool_call(
                    "backup_project", {"project_path": self.root_a}, transport="mcp"))
                await self._async_wait(holder_entered, timeout=5.0)
            with mock.patch.dict(
                    server.ct.TOOL_MAP, {"backup_project": targeted}, clear=False):
                resp = await asyncio.wait_for(server.dispatch_tool_call(
                    "backup_project",
                    {"project_path": self.root_b, "target_paths": ["x"]},
                    transport="mcp"), 5.0)
            gate.set()
            holder_result = json.loads((await asyncio.wait_for(holder_task, 5.0)).text)
        self.assertTrue(entered_targeted.is_set(),
                        "targeted backup was blocked behind the in-flight full backup")
        self.assertTrue(json.loads(resp.text).get("ok"), resp.text)
        self.assertTrue(holder_result.get("ok"), holder_result)


class ExecutorRoutingContractTests(unittest.TestCase):
    def test_blocking_workloads_do_not_share_asyncio_default_executor(self):
        command_source = inspect.getsource(code_tools.run_project_command)
        test_source = inspect.getsource(code_tools.run_project_test_script)
        # After the backup_project timeout/serialization fix, the executor
        # submission lives in `_backup_project_locked` (the per-project
        # locked body). Targeted backups are routed to the wide `read_io`
        # lane so they are not serialized behind an in-flight full-repo walk;
        # full backups continue to use the dedicated single-worker `backup`
        # lane. The contract is: backup work always goes through a bounded
        # executor lane, never the asyncio default executor.
        backup_source = (
            inspect.getsource(code_tools.backup_project)
            + inspect.getsource(code_tools._backup_project_locked)
        )
        read_source = inspect.getsource(read_context.read_file_range)
        git_source = "\n".join(
            inspect.getsource(getattr(server, name))
            for name in ("git_status", "git_diff", "git_commit")
        )
        council_source = inspect.getsource(server._council_run_blocking)

        self.assertIn('run_in_executor_lane(_executor_lane, _run)', command_source)
        test_lanes = [
            node.args[0].value
            for node in ast.walk(ast.parse(test_source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_in_executor_lane"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ]
        self.assertIn("test", test_lanes)
        self.assertIn('run_in_executor_lane(lane_name, _backup)', backup_source)
        self.assertIn('lane_name = "read_io" if normalized_targets is not None else "backup"', backup_source)
        self.assertIn('run_in_executor_lane("read_io"', read_source)
        self.assertIn('run_in_executor_lane("git"', git_source)
        self.assertIn('run_in_executor_lane("council_provider"', council_source)

    def test_write_transaction_backup_is_targeted_to_operations(self):
        import tools_v15

        source = inspect.getsource(tools_v15.write_transaction)
        self.assertIn('backup_groups.setdefault', source)
        self.assertIn('target_paths=targets', source)
        self.assertIn('_operation_target(op)', source)

    def test_backup_worker_has_cooperative_cancel_cleanup(self):
        # Same refactor as above: the worker/cleanup path now lives in
        # `_backup_project_locked`; the outer `backup_project` only owns the
        # per-project lock and the backup_in_progress fast-reject.
        source = (
            inspect.getsource(code_tools.backup_project)
            + inspect.getsource(code_tools._backup_project_locked)
        )
        self.assertIn('stop_backup.set()', source)
        self.assertIn('await _wait_for_cleanup()', source)
        self.assertIn('cleanup_pending=not cleanup_complete', source)


if __name__ == "__main__":
    unittest.main()
