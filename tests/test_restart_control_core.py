from __future__ import annotations

import json
import tempfile
import concurrent.futures
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.lifecycle import restart_control_service
from orchestrator.lifecycle.restart_control_service import ProcessIdentity, RestartController, build_guard
from orchestrator.lifecycle.restart_guard import GuardDecision, evaluate_restart_guard
from orchestrator.lifecycle.restart_protocol import (
    BusySnapshotPublisher,
    RestartProtocolError,
    SlidingWindowRateLimiter,
    encode_request,
    load_control_secret,
    sign_request,
    validate_restart_request,
)


class RestartControlInstallerTests(unittest.TestCase):
    def test_installer_uses_cmd_and_verifies_registration(self):
        installer = (Path(__file__).resolve().parents[1] / "install_restart_control_task.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("System32\\cmd.exe", installer)
        self.assertIn("/c `\"$wrapper`\"", installer)
        self.assertIn("-Force | Out-Null", installer)
        self.assertIn("Get-ScheduledTask -TaskName 'ToolA_RestartControl'", installer)
        self.assertIn("$installedAction.Execute -ne $expectedExecute", installer)
        self.assertIn("$installedAction.Arguments -ne $expectedArguments", installer)
        self.assertIn("$installedAction.WorkingDirectory -ne $root", installer)

    def test_process_details_uses_psutil_identity(self):
        process = mock.Mock()
        process.create_time.return_value = 123.5
        process.exe.return_value = r"C:\Python\pythonw.exe"
        with mock.patch.object(restart_control_service.psutil, "Process", return_value=process), \
             mock.patch.object(restart_control_service.subprocess, "run") as run:
            self.assertEqual(
                restart_control_service._process_details(42),
                (123.5, r"c:\python\pythonw.exe"),
            )
        run.assert_not_called()

    def test_process_details_rejects_empty_fallback_output(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            restart_control_service.psutil, "Process",
            side_effect=restart_control_service.psutil.NoSuchProcess(42),
        ), mock.patch.object(restart_control_service.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "identity unavailable"):
                restart_control_service._process_details(42)

    def test_process_details_cim_fallback_uses_unix_seconds(self):
        completed = mock.Mock(
            returncode=0,
            stdout='{"created":1785687272.175,"exe":"C:\\\\Python\\\\pythonw.exe"}',
            stderr="",
        )
        with mock.patch.object(
            restart_control_service.psutil, "Process",
            side_effect=restart_control_service.psutil.AccessDenied(42),
        ), mock.patch.object(restart_control_service.subprocess, "run", return_value=completed) as run:
            self.assertEqual(
                restart_control_service._process_details(42),
                (1785687272.175, r"c:\python\pythonw.exe"),
            )
        script = run.call_args.args[0][-1]
        self.assertIn("ToUnixTimeMilliseconds()/1000.0", script)

    def test_controller_uses_console_python_for_strict_stop(self):
        source = Path(restart_control_service.__file__).read_text(encoding="utf-8")
        self.assertIn('console_python.stem.lower() == "pythonw"', source)
        self.assertIn('console_python.with_name("python.exe")', source)
        self.assertIn('[str(console_python), str(root / "restart_orchestrator.py")', source)


class RestartProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = b"s" * 32
        self.now = 1000.0
        self.body = encode_request("request-123", "gen-a", self.now)
        self.headers = {"X-Restart-Signature": sign_request(self.secret, "POST", "/restart", self.body)}

    def validate(self, **overrides):
        values = {
            "method": "POST", "path": "/restart", "peer_host": "127.0.0.1",
            "headers": self.headers, "body": self.body, "secret": self.secret,
            "current_generation": "gen-a", "limiter": SlidingWindowRateLimiter(),
            "now": lambda: self.now,
        }
        values.update(overrides)
        return validate_restart_request(**values)

    def test_auth_and_generation_mismatch(self):
        self.assertEqual(self.validate().request_id, "request-123")
        with self.assertRaisesRegex(RestartProtocolError, "AUTH_FAILED"):
            self.validate(headers={"X-Restart-Signature": "0" * 64})
        with self.assertRaisesRegex(RestartProtocolError, "GENERATION_MISMATCH"):
            self.validate(current_generation="gen-b")

    def test_busy_snapshot_concurrent_publish_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "busy.json"
            counter = iter(range(100))
            publisher = BusySnapshotPublisher(path, lambda: {"value": next(counter)})
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _index: publisher.publish_once(), range(20)))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("value", payload)
            self.assertIn("captured_at", payload)
            self.assertEqual(list(path.parent.glob("busy.json.tmp.*")), [])

    def test_method_origin_force_body_and_rate_limits(self):
        with self.assertRaisesRegex(RestartProtocolError, "METHOD_NOT_ALLOWED"):
            self.validate(method="GET")
        with self.assertRaisesRegex(RestartProtocolError, "ORIGIN_NOT_ALLOWED"):
            self.validate(headers={**self.headers, "Origin": "http://localhost"})
        forced = json.dumps({"request_id": "request-123", "expected_generation": "gen-a", "timestamp": self.now, "force": True}).encode()
        with self.assertRaisesRegex(RestartProtocolError, "FORCE_NOT_ALLOWED"):
            self.validate(body=forced, headers={"X-Restart-Signature": sign_request(self.secret, "POST", "/restart", forced)})
        limiter = SlidingWindowRateLimiter(limit=1)
        self.validate(limiter=limiter)
        with self.assertRaisesRegex(RestartProtocolError, "RATE_LIMITED"):
            self.validate(limiter=limiter)

    def test_request_id_is_ascii_path_safe(self):
        invalid = encode_request("restart-được", "gen-a", self.now)
        with self.assertRaisesRegex(RestartProtocolError, "INVALID_REQUEST_ID"):
            self.validate(
                body=invalid,
                headers={"X-Restart-Signature": sign_request(self.secret, "POST", "/restart", invalid)},
            )

    def test_rate_limiter_is_atomic_under_concurrency(self):
        limiter = SlidingWindowRateLimiter(limit=4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _index: limiter.allow("127.0.0.1", self.now), range(20)))
        self.assertEqual(sum(results), 4)

    def test_body_limit_and_persistent_secret_acl_fail_closed(self):
        with self.assertRaisesRegex(RestartProtocolError, "BODY_TOO_LARGE"):
            self.validate(body=b"x" * 4097)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "control.secret"
            with mock.patch(
                "orchestrator.lifecycle.restart_protocol._restrict_secret",
                return_value=(True, None),
            ):
                first = load_control_secret(path)
                second = load_control_secret(path)
            self.assertTrue(first.usable)
            self.assertEqual(first.secret, second.secret)
            with mock.patch(
                "orchestrator.lifecycle.restart_protocol._restrict_secret",
                return_value=(False, "weak acl"),
            ):
                rejected = load_control_secret(path)
            self.assertFalse(rejected.usable)
            self.assertIsNone(rejected.secret)


class RestartGuardTests(unittest.TestCase):
    def guard(self, **overrides):
        values = {
            "busy_snapshot": {"captured_at": 99.0, "busy": False, "generation": "gen-a"}, "council_slots": [],
            "broker_lines": [], "now": 100.0, "snapshot_max_age_seconds": 5.0,
            "pid_alive": lambda _pid: False,
            "expected_generation": "gen-a",
        }
        values.update(overrides)
        return evaluate_restart_guard(**values)

    def test_active_council_unmatched_broker_and_stale_state_block(self):
        # A live council worker (proven by worker_pid) blocks with both reasons.
        active = self.guard(council_slots=[{"state": "running", "worker_pid": 44}], pid_alive=lambda pid: pid == 44)
        self.assertIn("COUNCIL_SLOT_ACTIVE", active.reasons)
        self.assertIn("COUNCIL_PID_ALIVE", active.reasons)
        unmatched = self.guard(broker_lines=[json.dumps({"type": "message", "id": "m1"})])
        self.assertIn("BROKER_DISPATCH_UNMATCHED", unmatched.reasons)
        stale = self.guard(busy_snapshot={"captured_at": 1.0, "busy": False, "generation": "gen-a"})
        self.assertIn("BUSY_SNAPSHOT_STALE", stale.reasons)
        old_generation = self.guard(busy_snapshot={"captured_at": 99.0, "busy": False, "generation": "gen-old"})
        self.assertIn("BUSY_SNAPSHOT_GENERATION_MISMATCH", old_generation.reasons)

    def test_slot_pid_field_never_proves_worker_liveness(self):
        # Fake-process alarm fix: the slot `pid` field is the orchestrator's own
        # getpid (alive for the life of [A]) and must NEVER yield COUNCIL_PID_ALIVE.
        decision = self.guard(council_slots=[{"state": "running", "pid": 44}], pid_alive=lambda pid: pid == 44)
        self.assertIn("COUNCIL_SLOT_ACTIVE", decision.reasons)  # fail-closed: no worker_pid
        self.assertNotIn("COUNCIL_PID_ALIVE", decision.reasons)  # pid alone proves nothing

    def test_active_slot_with_dead_worker_is_stale_and_does_not_block(self):
        # "reports in-flight but no worker is running" false alarm: an active slot
        # whose recorded worker is provably dead must not wedge the restart.
        decision = self.guard(
            council_slots=[{"state": "running", "worker_pid": 44}],
            pid_alive=lambda pid: False,
        )
        self.assertNotIn("COUNCIL_SLOT_ACTIVE", decision.reasons)
        self.assertNotIn("COUNCIL_PID_ALIVE", decision.reasons)
        self.assertTrue(decision.allowed)

    def test_old_unmatched_dispatch_with_dead_owner_is_stale_and_does_not_block(self):
        # A council dispatch that errored long ago (no reply, no live owner) is a
        # historical failure, not in-flight work — it must not block a restart.
        old_ts = 100.0 - 5000.0  # now=100.0; far older than dispatch_stale_seconds
        decision = self.guard(
            broker_lines=[json.dumps({"type": "message", "id": "d-old", "ts": old_ts, "worker_pid": 44})],
            pid_alive=lambda pid: False,
            dispatch_stale_seconds=600.0,
        )
        self.assertNotIn("BROKER_DISPATCH_UNMATCHED", decision.reasons)
        self.assertTrue(decision.allowed)

    def test_recent_unmatched_dispatch_still_blocks(self):
        # A fresh unmatched dispatch may still be in flight → keep blocking.
        decision = self.guard(
            broker_lines=[json.dumps({"type": "message", "id": "d-new", "ts": 99.0})],
            now=100.0, dispatch_stale_seconds=600.0,
        )
        self.assertIn("BROKER_DISPATCH_UNMATCHED", decision.reasons)

    def test_unmatched_dispatch_with_live_owner_still_blocks(self):
        # Old timestamp but a LIVE worker owner → real in-flight work, block.
        decision = self.guard(
            broker_lines=[json.dumps({"type": "message", "id": "d-live", "ts": 100.0 - 5000.0, "worker_pid": 44})],
            pid_alive=lambda pid: pid == 44,
            dispatch_stale_seconds=600.0,
        )
        self.assertIn("BROKER_DISPATCH_UNMATCHED", decision.reasons)

    def test_broker_parse_failure_is_fail_closed(self):
        decision = self.guard(broker_lines=["not-json"])
        self.assertFalse(decision.allowed)
        self.assertIn("BROKER_PARSE_FAILED", decision.reasons)

    def test_filesystem_guard_uses_runtime_state_and_fails_closed_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            council = root / ".council"
            cache = root / ".orchestrator_cache"
            council.mkdir()
            cache.mkdir()
            (cache / "restart_busy_snapshot.json").write_text(json.dumps({
                "captured_at": time.time(), "busy": False, "generation": "gen-a",
            }), encoding="utf-8")
            missing = build_guard(root, "gen-a")()
            self.assertFalse(missing.allowed)
            self.assertIn("COUNCIL_STATE_INVALID", missing.reasons)
            (council / "runtime_state.json").write_text(json.dumps({
                "ope::model": {"state": "running"},
            }), encoding="utf-8")
            active = build_guard(root, "gen-a")()
            self.assertIn("COUNCIL_SLOT_ACTIVE", active.reasons)


class FakeScheduler:
    def __init__(self, end=True, run=True, force_kill=True):
        self.end_result = end
        self.run_result = run
        self.force_kill_result = force_kill
        self.calls = []

    def end(self):
        self.calls.append("end")
        return self.end_result

    def run(self):
        self.calls.append("run")
        return self.run_result

    def force_kill(self, pid):
        self.calls.append(("force_kill", pid))
        return self.force_kill_result


class RestartControllerTests(unittest.TestCase):
    def build(self, scheduler, audit, **overrides):
        values = {
            "scheduler": scheduler, "guard": lambda _request: GuardDecision(True, ()),
            "current_identity": lambda: ProcessIdentity(10, "gen-a"),
            "prove_stopped": lambda old: old.pid == 10,
            "prove_started": lambda old: ProcessIdentity(20, "gen-b"),
            "append_audit": audit.append,
        }
        values.update(overrides)
        return RestartController(**values)

    def request(self, request_id="request-123", generation="gen-a"):
        from orchestrator.lifecycle.restart_protocol import RestartRequest
        return RestartRequest(request_id, generation, 1000.0)

    def test_scheduler_failures(self):
        audit = []
        # /end returned False AND the High-IL force-kill still could not settle the old
        # process (prove_stopped stays False) -> SCHEDULER_END_FAILED.
        self.assertEqual(self.build(FakeScheduler(end=False), audit, prove_stopped=lambda old: False).execute(self.request())["error_name"], "SCHEDULER_END_FAILED")
        self.assertEqual(self.build(FakeScheduler(run=False), audit).execute(self.request("request-456"))["error_name"], "SCHEDULER_RUN_FAILED")

    def test_end_failure_recovered_by_force_kill(self):
        # Root-cause fix for the recurring "restart kẹt": when /end signals the task but the
        # old server survives, the High-IL controller force-kills it. If that settles the
        # process (prove_stopped True) the restart proceeds instead of aborting.
        audit = []
        sched = FakeScheduler(end=False)
        result = self.build(sched, audit).execute(self.request("request-789"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["error_name"], "RESTART_VERIFIED")
        self.assertIn(("force_kill", 10), sched.calls)

    def test_success_replay_and_pid_generation_proof(self):
        audit = []
        scheduler = FakeScheduler()
        controller = self.build(scheduler, audit)
        first = controller.execute(self.request())
        second = controller.execute(self.request())
        self.assertTrue(first["ok"])
        self.assertEqual((first["old_pid"], first["new_pid"]), (10, 20))
        self.assertEqual((first["old_generation"], first["new_generation"]), ("gen-a", "gen-b"))
        self.assertTrue(second["replayed"])
        self.assertEqual(scheduler.calls, ["end", "run"])
        self.assertEqual(len(audit), 1)
        conflict = controller.execute(self.request(generation="gen-other"))
        self.assertEqual(conflict["error_name"], "REQUEST_ID_CONFLICT")

    def test_guard_blocks_and_audit_contains_no_credentials(self):
        audit = []
        controller = self.build(
            FakeScheduler(), audit,
            guard=lambda _request: GuardDecision(False, ("BROKER_DISPATCH_UNMATCHED",)),
        )
        result = controller.execute(self.request())
        self.assertEqual(result["error_name"], "RESTART_GUARD_BLOCKED")
        serialized = json.dumps(audit)
        self.assertNotIn("signature", serialized.lower())
        self.assertNotIn("token", serialized.lower())
        self.assertNotIn("secret", serialized.lower())

    def test_stop_proof_requires_old_identity_gone_and_sustained_livez_loss(self):
        old = ProcessIdentity(10, "gen-a", 123.0, "pythonw.exe")
        with mock.patch.object(restart_control_service, "_process_details", return_value=(123.0, "pythonw.exe")), \
             mock.patch.object(restart_control_service, "_fetch_identity", side_effect=OSError("down")), \
             mock.patch.object(restart_control_service.time, "sleep"), \
             mock.patch.object(restart_control_service.time, "monotonic", side_effect=[0.0, 0.0, 0.1, 0.2, 2.0]):
            self.assertFalse(restart_control_service._wait_stopped(old, "http://livez", timeout=1.0))
        with mock.patch.object(restart_control_service, "_process_details", side_effect=OSError("gone")), \
             mock.patch.object(restart_control_service, "_fetch_identity", side_effect=OSError("down")), \
             mock.patch.object(restart_control_service.time, "sleep"), \
             mock.patch.object(restart_control_service.time, "monotonic", side_effect=[0.0, 0.0, 0.1]):
            self.assertTrue(restart_control_service._wait_stopped(old, "http://livez", timeout=1.0))

    def test_accepted_worker_always_stores_internal_error_terminal(self):
        controller = self.build(
            FakeScheduler(), [], current_identity=lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        request = self.request("request-internal")
        controller._request_generations[request.request_id] = request.expected_generation
        controller._execute_accepted(request)
        status = controller.status(request.request_id)
        self.assertTrue(status["terminal"])
        self.assertEqual(status["error_name"], "CONTROLLER_INTERNAL_ERROR")

    def test_thread_start_failure_is_terminal_audited_and_idempotent(self):
        audit = []
        controller = self.build(FakeScheduler(), audit)
        request = self.request("request-thread-start")
        with mock.patch.object(restart_control_service.threading, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("cannot start")
            first = controller.accept(request)
        self.assertTrue(first["terminal"])
        self.assertEqual(first["error_name"], "CONTROLLER_INTERNAL_ERROR")
        self.assertEqual(first["detail"], "RuntimeError")
        self.assertEqual(len(audit), 1)
        status = controller.status(request.request_id)
        self.assertTrue(status["terminal"])
        replay = controller.accept(request)
        self.assertTrue(replay["terminal"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["error_name"], "CONTROLLER_INTERNAL_ERROR")
        self.assertEqual(len(audit), 1)

    def test_thread_start_failure_terminal_survives_audit_failure(self):
        controller = self.build(
            FakeScheduler(), [], append_audit=lambda _event: (_ for _ in ()).throw(OSError("audit down"))
        )
        request = self.request("request-thread-audit")
        with mock.patch.object(restart_control_service.threading, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("cannot start")
            result = controller.accept(request)
        self.assertEqual(result["error_name"], "CONTROLLER_INTERNAL_ERROR")
        self.assertTrue(controller.status(request.request_id)["terminal"])

    def test_distinct_request_is_rejected_while_transaction_is_active(self):
        entered = threading.Event()
        release = threading.Event()
        controller = self.build(
            FakeScheduler(), [],
            guard=lambda _request: (entered.set(), release.wait(1.0), GuardDecision(True, ()))[2],
        )
        first = controller.accept(self.request("request-active-one"))
        self.assertTrue(first["ok"])
        self.assertTrue(entered.wait(1.0))
        second = controller.accept(self.request("request-active-two"))
        self.assertEqual(second["error_name"], "RESTART_ALREADY_PENDING")
        self.assertEqual(second["active_request_id"], "request-active-one")
        self.assertIsNone(controller.status("request-active-two"))
        release.set()

    def test_matching_restart_marker_exempts_exactly_one_request(self):
        snapshot = {
            "captured_at": 99.0, "busy": True, "generation": "gen-a",
            "restart_request_id": "request-123", "requests_in_flight": 1,
            "executor_in_flight": 0, "accounting_pending": 0, "semaphores": [],
        }
        values = {
            "council_slots": [], "broker_lines": [], "now": 100.0,
            "snapshot_max_age_seconds": 5.0, "pid_alive": lambda _pid: False,
            "expected_generation": "gen-a", "expected_request_id": "request-123",
        }
        allowed = evaluate_restart_guard(busy_snapshot=snapshot, **values)
        self.assertTrue(allowed.allowed)
        unrelated = evaluate_restart_guard(
            busy_snapshot={**snapshot, "requests_in_flight": 2}, **values
        )
        self.assertIn("ORCHESTRATOR_BUSY", unrelated.reasons)
        hung = evaluate_restart_guard(
            busy_snapshot={
                **snapshot,
                "requests_in_flight": 2,
                "executor_in_flight": 2,
                "is_stale_or_hung": True,
            },
            **values,
        )
        self.assertNotIn("ORCHESTRATOR_BUSY", hung.reasons)
        self.assertNotIn("IN_FLIGHT_WORKERS_ACTIVE", hung.reasons)


class BusySnapshotPublisherTests(unittest.TestCase):
    def test_thread_lifecycle_and_atomic_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "busy.json"
            publisher = BusySnapshotPublisher(
                path,
                lambda: {"busy": False, "requests_in_flight": 0, "executor_in_flight": 0,
                         "accounting_pending": 0, "semaphores": [], "generation": "gen-a"},
                interval=0.01,
            )
            publisher.start()
            deadline = time.time() + 1
            while not path.exists() and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(publisher.alive)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["generation"], "gen-a")
            publisher.stop()
            self.assertFalse(publisher.alive)

    def test_windows_acl_requires_exact_verified_dacl(self):
        from orchestrator.lifecycle import restart_protocol
        completed = mock.Mock(returncode=7, stdout="", stderr="unexpected ACE")
        with mock.patch.object(restart_protocol.os, "name", "nt"), \
             mock.patch.object(restart_protocol.subprocess, "run", return_value=completed) as run:
            ok, error = restart_protocol._restrict_secret(Path("secret"))
        self.assertFalse(ok)
        self.assertIn("verification", error)
        command = run.call_args.args[0]
        self.assertIn("SetAccessRuleProtection", command[-1])
        self.assertIn("RemoveAccessRuleSpecific", command[-1])


if __name__ == "__main__":
    unittest.main()
