from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from io import BytesIO

from orchestrator.lifecycle.restart_control_service import (
    ProcessIdentity,
    RestartController,
    TaskSchedulerAdapter,
    create_control_server,
)
from orchestrator.lifecycle.restart_guard import GuardDecision
from orchestrator.lifecycle.restart_protocol import encode_request, sign_request
from orchestrator.lifecycle.request_orchestrator_restart import request_restart


class FakeResult:
    returncode = 0


class SettlingScheduledTask:
    """Simulate schtasks against an action that owns its child until /end."""

    def __init__(self):
        self.state = "Running"
        self.calls = []

    def __call__(self, command, **_kwargs):
        action = command[1]
        self.calls.append((action, self.state))
        result = FakeResult()
        if action == "/end" and self.state == "Running":
            self.state = "Ready"
            result.returncode = 0
        elif action == "/run" and self.state == "Ready":
            self.state = "Running"
            result.returncode = 0
        else:
            result.returncode = 1
        return result


class RestartControlIntegrationTests(unittest.TestCase):
    def test_real_adapter_contract_handles_running_to_ready_to_running(self):
        scheduled_task = SettlingScheduledTask()
        settled_calls = []
        adapter = TaskSchedulerAdapter(
            runner=scheduled_task,
            settled_stop=lambda: settled_calls.append("stop") or True,
        )
        self.assertTrue(adapter.end())
        self.assertEqual(scheduled_task.state, "Ready")
        self.assertEqual(settled_calls, ["stop"])
        self.assertTrue(adapter.run())
        self.assertEqual(scheduled_task.state, "Running")
        self.assertEqual(scheduled_task.calls, [("/end", "Running"), ("/run", "Ready")])

    def test_scheduler_end_success_still_requires_strict_stop(self):
        scheduled_task = SettlingScheduledTask()
        adapter = TaskSchedulerAdapter(runner=scheduled_task, settled_stop=lambda: False)
        self.assertFalse(adapter.end())
        self.assertEqual(scheduled_task.calls, [("/end", "Running")])

    def test_ready_task_uses_authoritative_settled_stop_fallback(self):
        scheduled_task = SettlingScheduledTask()
        scheduled_task.state = "Ready"
        settled_calls = []
        adapter = TaskSchedulerAdapter(
            runner=scheduled_task,
            settled_stop=lambda: settled_calls.append("stop") or True,
        )
        self.assertTrue(adapter.end())
        self.assertEqual(settled_calls, ["stop"])
        self.assertEqual(scheduled_task.calls, [("/end", "Ready")])

    def setUp(self):
        self.secret = b"q" * 32
        self.audit = []
        self.scheduler_calls = []
        scheduler = TaskSchedulerAdapter(
            runner=lambda command, **_kwargs: self.scheduler_calls.append(command[1]) or FakeResult()
        )
        controller = RestartController(
            scheduler=scheduler,
            guard=lambda _request: GuardDecision(True, ()),
            current_identity=lambda: ProcessIdentity(10, "gen-a"),
            prove_stopped=lambda _old: True,
            prove_started=lambda _old: ProcessIdentity(20, "gen-b"),
            append_audit=self.audit.append,
        )
        self.server = create_control_server(
            controller=controller,
            secret=self.secret,
            current_generation=lambda: "gen-a",
            port=0,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/restart"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def post(self, body, signature):
        request = urllib.request.Request(
            self.url, data=body, method="POST", headers={"X-Restart-Signature": signature}
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read())

    def status(self, request_id):
        from orchestrator.lifecycle.restart_protocol import sign_request
        path = "/status/" + request_id
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}{path}", method="GET",
            headers={"X-Restart-Signature": sign_request(self.secret, "GET", path, b"")},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read())

    def test_authenticated_restart_uses_scheduler_end_then_run(self):
        body = encode_request("request-integration", "gen-a", time.time())
        result = self.post(body, sign_request(self.secret, "POST", "/restart", body))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "accepted")
        deadline = time.time() + 1
        while len(self.scheduler_calls) < 2 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.scheduler_calls, ["/end", "/run"])
        terminal = self.status("request-integration")
        self.assertTrue(terminal["terminal"])
        self.assertTrue(terminal["ok"])
        replay = self.post(body, sign_request(self.secret, "POST", "/restart", body))
        self.assertTrue(replay["terminal"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.scheduler_calls, ["/end", "/run"])
        serialized = json.dumps(self.audit)
        for credential_name in ("signature", "token", "secret"):
            self.assertNotIn(credential_name, serialized.lower())

    def test_auth_failure_and_force_are_never_exposed(self):
        body = encode_request("request-bad-auth", "gen-a", time.time())
        with self.assertRaises(urllib.error.HTTPError) as auth_error:
            self.post(body, "0" * 64)
        self.assertEqual(auth_error.exception.code, 401)
        forced = json.dumps({
            "request_id": "request-force", "expected_generation": "gen-a",
            "timestamp": time.time(), "force": True,
        }, separators=(",", ":"), sort_keys=True).encode()
        signature = sign_request(self.secret, "POST", "/restart", forced)
        with self.assertRaises(urllib.error.HTTPError) as force_error:
            self.post(forced, signature)
        self.assertEqual(force_error.exception.code, 400)

    def test_unsigned_status_is_rejected(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}/status/request-integration", method="GET"
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(error.exception.code, 401)

    def test_lost_post_response_recovers_same_request_status_without_second_transaction(self):
        calls = []

        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self): return json.dumps(self.payload).encode()

        def opener(request, timeout=0):
            method = request.get_method() if hasattr(request, "get_method") else "GET"
            url = request.full_url if hasattr(request, "full_url") else str(request)
            calls.append((method, url))
            if method == "POST":
                raise urllib.error.URLError("response lost after accept")
            if url.endswith("/health"):
                return Response({"ok": True})
            if url.endswith("/status/request-ambiguous"):
                return Response({
                    "ok": True, "terminal": True, "status": "terminal",
                    "request_id": "request-ambiguous", "error_name": "RESTART_VERIFIED",
                })
            raise AssertionError(url)

        result = request_restart(
            url="http://127.0.0.1:8085/restart", secret=self.secret,
            expected_generation="gen-a", request_id="request-ambiguous",
            timeout=1.0, opener=opener,
        )
        self.assertTrue(result["terminal"])
        self.assertEqual(sum(method == "POST" for method, _url in calls), 1)
        status_call = next(url for method, url in calls if "/status/" in url)
        self.assertTrue(status_call.endswith("/status/request-ambiguous"))

    def test_lost_post_and_temporarily_unreachable_reconciliation_returns_unknown(self):
        calls = []

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self): return b'{"ok":true}'

        def opener(request, timeout=0):
            method = request.get_method() if hasattr(request, "get_method") else "GET"
            url = request.full_url if hasattr(request, "full_url") else str(request)
            calls.append((method, url))
            if len(calls) == 1 and url.endswith("/health"):
                return Response()
            raise urllib.error.URLError(TimeoutError("temporarily unreachable"))

        result = request_restart(
            url="http://127.0.0.1:8085/restart", secret=self.secret,
            expected_generation="gen-a", request_id="request-background",
            timeout=0.1, opener=opener,
        )
        self.assertEqual(result["error_name"], "RESTART_RESULT_UNKNOWN")
        self.assertEqual(result["request_id"], "request-background")
        self.assertTrue(result["post_attempted"])
        self.assertEqual(sum(method == "POST" for method, _url in calls), 1)

    def test_preconnect_refusal_is_typed_before_post(self):
        from orchestrator.lifecycle.request_orchestrator_restart import ControllerUnavailableBeforePost

        calls = []
        def opener(request, timeout=0):
            calls.append(request)
            raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

        with self.assertRaises(ControllerUnavailableBeforePost):
            request_restart(
                url="http://127.0.0.1:8085/restart", secret=self.secret,
                expected_generation="gen-a", request_id="request-refused",
                timeout=0.1, opener=opener,
            )
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0], str)


if __name__ == "__main__":
    unittest.main()
