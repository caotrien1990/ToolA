from __future__ import annotations

import asyncio
import json
import inspect
import threading
import unittest
from unittest import mock

from aiohttp.test_utils import make_mocked_request

import orchestrator_server
from orchestrator.tools.write import core as write_core


class ServerLifecycleContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        orchestrator_server.SERVER_READY = True
        orchestrator_server.SERVER_STOPPING = False
        orchestrator_server.SERVER_STARTUP_PHASE = "ready"
        orchestrator_server.SERVER_STARTUP_DEGRADED = False
        orchestrator_server.SERVER_STARTUP_ERROR = ""

    async def test_boot_recovery_timeout_releases_readiness_as_degraded_and_stops_worker(self):
        stopped = asyncio.Event()

        async def cooperative_sweep(*, stop_event=None, deadline_monotonic=None):
            self.assertIsNotNone(stop_event)
            self.assertIsNotNone(deadline_monotonic)
            while not stop_event.is_set():
                await asyncio.sleep(0.002)
            stopped.set()
            return {"ok": False, "aborted": True, "reason": "deadline"}

        with mock.patch.object(write_core, "_boot_write_sweep", side_effect=cooperative_sweep), \
             mock.patch.dict("os.environ", {
                 "ORCHESTRATOR_BOOT_RECOVERY_TIMEOUT_SEC": "0.02",
                 "ORCHESTRATOR_BOOT_RECOVERY_STOP_GRACE_SEC": "0.2",
             }):
            result = await orchestrator_server._run_boot_recovery()

        self.assertTrue(stopped.is_set())
        self.assertTrue(orchestrator_server.SERVER_READY)
        self.assertTrue(orchestrator_server.SERVER_STARTUP_DEGRADED)
        self.assertEqual(orchestrator_server.SERVER_STARTUP_PHASE, "ready_degraded")
        self.assertEqual(result["status"], "timeout")
        self.assertIn("timed out", orchestrator_server.SERVER_STARTUP_ERROR.lower())

    async def test_boot_recovery_success_records_metrics_without_degradation(self):
        expected = {"ok": True, "checked": 2, "restored_count": 1}

        async def fast_sweep(*, stop_event=None, deadline_monotonic=None):
            self.assertFalse(stop_event.is_set())
            self.assertGreater(deadline_monotonic, 0)
            return dict(expected)

        with mock.patch.object(write_core, "_boot_write_sweep", side_effect=fast_sweep), \
             mock.patch.dict("os.environ", {"ORCHESTRATOR_BOOT_RECOVERY_TIMEOUT_SEC": "1"}):
            result = await orchestrator_server._run_boot_recovery()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sweep"], expected)
        self.assertTrue(orchestrator_server.SERVER_READY)
        self.assertFalse(orchestrator_server.SERVER_STARTUP_DEGRADED)
        self.assertEqual(orchestrator_server.SERVER_STARTUP_PHASE, "ready")

    async def test_stopping_gate_rejects_new_api_calls_but_keeps_health_available(self):
        called = []

        async def handler(request):
            called.append(request.path)
            return orchestrator_server.web.Response(status=204)

        with mock.patch.object(orchestrator_server, "SERVER_READY", True), \
             mock.patch.object(orchestrator_server, "SERVER_STOPPING", True), \
             mock.patch.object(orchestrator_server, "_RATE_LIMIT_MAX", 0), \
             mock.patch.object(orchestrator_server, "_OTHER_RATE_LIMIT_MAX", 0):
            blocked = await orchestrator_server._http_defense_middleware(
                make_mocked_request(
                    "POST", "/api/call/write_fix", match_info={"tool_name": "write_fix"}
                ),
                handler,
            )
            health = await orchestrator_server._http_defense_middleware(
                make_mocked_request("GET", "/health"), handler
            )

        self.assertEqual(blocked.status, 503)
        self.assertEqual(json.loads(blocked.text)["error_name"], "ORCHESTRATOR_STOPPING")
        self.assertEqual(health.status, 204)
        self.assertEqual(called, ["/health"])

    async def test_background_tasks_are_cancelled_and_awaited(self):
        finalized = asyncio.Event()

        async def worker():
            try:
                await asyncio.Event().wait()
            finally:
                finalized.set()

        task = asyncio.create_task(worker())
        await asyncio.sleep(0)
        rows = await orchestrator_server._cancel_and_await_background_tasks(
            {"worker": task}, timeout=0.2
        )

        self.assertTrue(task.done())
        self.assertTrue(finalized.is_set())
        self.assertTrue(rows["worker"]["done"])
        self.assertTrue(rows["worker"]["cancelled"])

    async def test_restart_preflight_rejects_live_work_without_force_drain(self):
        busy = {
            "busy": True,
            "requests_in_flight": 1,
            "executor_in_flight": 1,
            "oldest_executor_active_sec": 1.0,
            "accounting_pending": 0,
            "semaphores": [],
            "hung_threshold_sec": 15.0,
            "is_stale_or_hung": False,
        }
        helper = mock.AsyncMock(
            return_value=json.dumps({"ok": True, "status": "restart_queued"})
        )
        with mock.patch.object(orchestrator_server, "_restart_busy_state", return_value=busy), \
             mock.patch("tools_v15.restart_orchestrator_via_bat", helper):
            rejected = json.loads(await orchestrator_server.restart_orchestrator_via_bat())

        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error_name"], "RESTART_DRAIN_REQUIRED")
        self.assertEqual(rejected["busy_state"], busy)
        helper.assert_not_awaited()
        params = inspect.signature(orchestrator_server.restart_orchestrator_via_bat).parameters
        self.assertIn("force_drain", params)
        self.assertIn("force_drain_reason", params)

    async def test_restart_preflight_allows_hung_work_without_force_drain(self):
        busy = {
            "busy": True,
            "requests_in_flight": 2,
            "executor_in_flight": 1,
            "oldest_executor_active_sec": 20.0,
            "accounting_pending": 0,
            "semaphores": [{"held": 1, "waiters": 0, "held_for_sec": 20.0}],
            "hung_threshold_sec": 15.0,
            "is_stale_or_hung": True,
        }
        helper = mock.AsyncMock(
            return_value=json.dumps({"ok": True, "status": "restart_queued"})
        )
        with mock.patch.object(orchestrator_server, "_restart_busy_state", return_value=busy), \
             mock.patch("tools_v15.restart_orchestrator_via_bat", helper):
            queued = json.loads(await orchestrator_server.restart_orchestrator_via_bat())
        self.assertTrue(queued["ok"])
        helper.assert_awaited()

    async def test_restart_preflight_force_drain_overrides_live_work(self):
        busy = {
            "busy": True,
            "requests_in_flight": 2,
            "executor_in_flight": 1,
            "oldest_executor_active_sec": 1.0,
            "accounting_pending": 0,
            "semaphores": [],
            "hung_threshold_sec": 15.0,
            "is_stale_or_hung": False,
        }
        helper = mock.AsyncMock(
            return_value=json.dumps({"ok": True, "status": "restart_queued"})
        )
        with mock.patch.object(orchestrator_server, "_restart_busy_state", return_value=busy), \
             mock.patch("tools_v15.restart_orchestrator_via_bat", helper):
            queued = json.loads(await orchestrator_server.restart_orchestrator_via_bat(
                force_drain=True, force_drain_reason="operator approved hung-task restart",
            ))
        self.assertTrue(queued["ok"])
        helper.assert_awaited()

    def test_restart_busy_classifier_treats_hung_executor_as_stale(self):
        live = {
            "busy": True,
            "executor_in_flight": 1,
            "oldest_executor_active_sec": 2.0,
            "semaphores": [],
            "hung_threshold_sec": 15.0,
        }
        hung = {
            "busy": True,
            "executor_in_flight": 1,
            "oldest_executor_active_sec": 20.0,
            "semaphores": [{"held": 1, "held_for_sec": 20.0}],
            "hung_threshold_sec": 15.0,
        }
        self.assertFalse(orchestrator_server._restart_busy_is_stale_or_hung(live))
        self.assertTrue(orchestrator_server._restart_busy_is_stale_or_hung(hung))

    def test_council_id_models_map_includes_gro_and_grok(self):
        mapping = orchestrator_server._COUNCIL_ID_MODELS_MAP
        self.assertEqual(mapping.get("gro"), "GROK")
        self.assertEqual(mapping.get("grok"), "GROK")

    def test_write_path_valueerror_is_not_internal_error(self):
        import code_tools as ct
        outside = json.loads(write_core._json_path_resolve_error(
            ct, ValueError("File path 'x' is outside project root 'y'."),
        ))
        self.assertEqual(outside.get("error_name"), "PATH_OUTSIDE_PROJECT")
        self.assertNotEqual(outside.get("error_name"), "INTERNAL_ERROR")
        unc = json.loads(write_core._json_path_resolve_error(
            ct, ValueError("UNC project_path không dùng cho project guard"),
        ))
        self.assertEqual(unc.get("error_name"), "UNC_PATH_UNSUPPORTED")

    def test_restart_snapshot_never_subtracts_phantom_current_request(self):
        with mock.patch.object(orchestrator_server, "_requests_in_flight", 1), \
             mock.patch.object(orchestrator_server, "executor_lane_status", return_value=[]), \
             mock.patch.object(orchestrator_server, "_semaphore_status_rows", return_value=[]), \
             mock.patch.object(orchestrator_server._POST_CALL_ACCOUNTING, "status", return_value={"pending": 0}):
            idle = orchestrator_server._restart_busy_snapshot_state()
        self.assertTrue(idle["busy"])
        self.assertEqual(idle["requests_in_flight"], 1)
        with mock.patch.object(orchestrator_server, "_requests_in_flight", 2), \
             mock.patch.object(orchestrator_server, "executor_lane_status", return_value=[]), \
             mock.patch.object(orchestrator_server, "_semaphore_status_rows", return_value=[]), \
             mock.patch.object(orchestrator_server._POST_CALL_ACCOUNTING, "status", return_value={"pending": 0}):
            busy = orchestrator_server._restart_busy_snapshot_state()
        self.assertTrue(busy["busy"])
        self.assertEqual(busy["requests_in_flight"], 2)


class BootSweepCancellationContractTests(unittest.TestCase):
    def test_scan_honors_pre_cancel_without_touching_roots(self):
        stop_event = threading.Event()
        stop_event.set()
        with mock.patch.object(write_core, "_boot_sweep_project_roots") as roots:
            result = write_core._boot_sweep_scan(
                ["ignored"], stop_event=stop_event, deadline_monotonic=None
            )

        roots.assert_not_called()
        self.assertTrue(result["aborted"])
        self.assertEqual(result["abort_reason"], "stop_requested")
        self.assertEqual(result["pending"], [])


if __name__ == "__main__":
    unittest.main()
