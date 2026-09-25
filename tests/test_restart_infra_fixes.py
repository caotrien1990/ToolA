from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import tools_v15
from orchestrator.lifecycle.restart_control_service import (
    ProcessIdentity,
    RestartController,
    create_control_server,
)
from orchestrator.lifecycle.restart_guard import GuardDecision, evaluate_restart_guard
from orchestrator.lifecycle.restart_protocol import encode_request, sign_request


class _Scheduler:
    def end(self): return True
    def run(self): return True


def _write_council_files(root: Path, *, runtime: dict | list | None = None,
                          broker_lines: tuple[str, ...] | list[str] = (),
                          busy_ok: bool = False) -> None:
    council = root / ".council"
    council.mkdir(parents=True, exist_ok=True)
    cache = root / ".orchestrator_cache"
    cache.mkdir(parents=True, exist_ok=True)
    if busy_ok:
        (cache / "restart_busy_snapshot.json").write_text(json.dumps({
            "captured_at": time.time(), "busy": False, "generation": "gen-a",
        }), encoding="utf-8")
    if runtime is not None:
        (council / "runtime_state.json").write_text(json.dumps(runtime), encoding="utf-8")
    if broker_lines:
        (council / "broker_filtered.jsonl").write_text(
            "\n".join(broker_lines) + "\n", encoding="utf-8"
        )


class RestartInfrastructureFixTests(unittest.TestCase):
    def test_blocking_guards_have_actionable_structure(self):
        decision = evaluate_restart_guard(
            busy_snapshot=None, council_slots=[], broker_lines=[], now=time.time(),
            snapshot_max_age_seconds=3, pid_alive=lambda _pid: False,
            expected_generation="gen-a",
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.blocking_guards[0]["name"], "BUSY_SNAPSHOT_MISSING")
        self.assertEqual(set(decision.blocking_guards[0]),
                         {"name", "held_by", "is_stale", "retryable", "how_to_clear"})

    def test_force_drain_closes_stale_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_council_files(
                root,
                runtime={},
                broker_lines=(
                    # Old enough + no live PID evidence => provably stale.
                    json.dumps({"type": "message", "id": "stale-1", "ts": time.time() - 3600}),
                ),
            )
            result = tools_v15._force_drain_restart_state(root, "operator approved stale cleanup")
            self.assertEqual(result["closed_dispatches"], ["stale-1"])
            events = [json.loads(line) for line in
                      (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["in_reply_to"], "stale-1")
            self.assertEqual(events[-1]["force_drain_reason"], "operator approved stale cleanup")

    def test_force_drain_does_not_close_recent_unmatched_dispatch(self):
        # A dispatch that just happened (ts=now) is unmatched but NOT stale —
        # it could be a live worker about to reply. Force-drain must leave it
        # alone and must NOT append a synthetic terminal reply.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_council_files(
                root,
                runtime={},
                broker_lines=(
                    json.dumps({"type": "message", "id": "live-1", "ts": time.time()}),
                ),
            )
            before = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
            result = tools_v15._force_drain_restart_state(root, "operator approved stale cleanup")
            self.assertEqual(result["closed_dispatches"], [])
            after = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_force_drain_does_not_close_unmatched_dispatch_without_ts(self):
        # A dispatch with no parseable `ts` is fail-closed to "preserve": we
        # cannot prove it is stale. Do not terminalize it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_council_files(
                root,
                runtime={},
                broker_lines=(
                    json.dumps({"type": "message", "id": "unknown-age"}),
                ),
            )
            before = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
            result = tools_v15._force_drain_restart_state(root, "operator approved stale cleanup")
            self.assertEqual(result["closed_dispatches"], [])
            after = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_force_drain_does_not_close_old_dispatch_with_live_worker_pid(self):
        # Age alone is not enough: a dispatch carrying an explicitly live
        # worker_pid must be preserved (live council work).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_council_files(
                root,
                runtime={},
                broker_lines=(
                    json.dumps({
                        "type": "message", "id": "owned-old",
                        "ts": time.time() - 3600,
                        "worker_pid": 999_999_999,  # treated alive via mock below
                    }),
                ),
            )
            # Patch psutil.pid_exists so the worker_pid looks alive.
            import psutil
            original_pid_exists = psutil.pid_exists
            psutil.pid_exists = lambda _pid: True
            try:
                before = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
                result = tools_v15._force_drain_restart_state(root, "operator approved stale cleanup")
            finally:
                psutil.pid_exists = original_pid_exists
            self.assertEqual(result["closed_dispatches"], [])
            after = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_force_drain_malformed_broker_performs_no_partial_mutation(self):
        # If broker JSON parsing fails, council runtime state must NOT have
        # been partially mutated. Both files must be untouched.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_council_files(
                root,
                runtime={"ope::model": {"state": "running"}},
            )
            # Append a malformed line AFTER a stale-but-legitimate dispatch so
            # the old code would mutate the council file before failing parse.
            broker_path = root / ".council" / "broker_filtered.jsonl"
            broker_path.write_text(
                json.dumps({"type": "message", "id": "stale-1", "ts": time.time() - 3600}) + "\n"
                + "{not valid json\n",
                encoding="utf-8",
            )
            runtime_path = root / ".council" / "runtime_state.json"
            runtime_before = runtime_path.read_text(encoding="utf-8")
            broker_before = broker_path.read_text(encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                tools_v15._force_drain_restart_state(root, "operator approved stale cleanup")
            self.assertIn("FORCE_DRAIN_BROKER_PARSE_FAILED", str(ctx.exception))
            self.assertEqual(runtime_path.read_text(encoding="utf-8"), runtime_before)
            self.assertEqual(broker_path.read_text(encoding="utf-8"), broker_before)

    def test_force_drain_preserves_dispatch_with_nan_timestamp(self):
        # float("nan") is accepted by float(text) but NaN comparisons are all
        # false. The old code would let the predicate fall through to
        # `return True` and terminalize the dispatch. Fail-closed fix: NaN is
        # NOT a valid timestamp → ts is None → dispatch preserved.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_council_files(
                root,
                runtime={},
                broker_lines=(
                    json.dumps({"type": "message", "id": "nan-ts", "ts": "nan"}),
                ),
            )
            before = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
            result = tools_v15._force_drain_restart_state(root, "operator approved stale cleanup")
            self.assertEqual(result["closed_dispatches"], [])
            after = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_force_drain_preserves_dispatch_with_infinite_timestamp(self):
        # "inf" parses via float(text) but is not finite. An infinite timestamp
        # cannot establish "older than stale_after_seconds", so the dispatch
        # must be preserved (fail-closed).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_council_files(
                root,
                runtime={},
                broker_lines=(
                    json.dumps({"type": "message", "id": "inf-ts", "ts": "inf"}),
                    json.dumps({"type": "message", "id": "neginf-ts", "ts": "-inf"}),
                ),
            )
            before = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
            result = tools_v15._force_drain_restart_state(root, "operator approved stale cleanup")
            self.assertEqual(result["closed_dispatches"], [])
            after = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_force_drain_preserves_dispatch_with_invalid_pid_string(self):
        # An old dispatch carrying worker_pid="not-a-pid" is NOT provably
        # stale: the pid field exists but cannot be verified. The old code
        # silently `continue`d past unparseable pids and terminalized the
        # dispatch. Fix: invalid PID evidence marks the dispatch UNVERIFIED →
        # preserve it (fail-closed).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_council_files(
                root,
                runtime={},
                broker_lines=(
                    json.dumps({
                        "type": "message", "id": "bad-pid",
                        "ts": time.time() - 3600,
                        "worker_pid": "not-a-pid",
                    }),
                ),
            )
            # Even if pid_alive would return True for any pid, the dispatch
            # must still be preserved because we cannot parse the pid value.
            import psutil
            original_pid_exists = psutil.pid_exists
            psutil.pid_exists = lambda _pid: True
            try:
                before = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
                result = tools_v15._force_drain_restart_state(root, "operator approved stale cleanup")
            finally:
                psutil.pid_exists = original_pid_exists
            self.assertEqual(result["closed_dispatches"], [])
            after = (root / ".council" / "broker_filtered.jsonl").read_text(encoding="utf-8")
            self.assertEqual(before, after)

    def test_force_drain_broker_append_failure_leaves_council_unchanged(self):
        # Regression for the non-atomic two-file commit: council was rewritten
        # first, then broker append; if the broker append failed, council had
        # already been mutated. Fix: stage both files in temp siblings and
        # only os.replace both after both temp writes succeed; on failure,
        # restore the council file from its pre-mutation bytes.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_council_files(
                root,
                runtime={"ope::model": {"state": "running"}},
                broker_lines=(
                    json.dumps({"type": "message", "id": "stale-1", "ts": time.time() - 3600}),
                ),
            )
            runtime_path = root / ".council" / "runtime_state.json"
            broker_path = root / ".council" / "broker_filtered.jsonl"
            runtime_before = runtime_path.read_bytes()
            broker_before = broker_path.read_bytes()

            # Force the broker temp write to fail by replacing the broker path
            # with a DIRECTORY at the moment write_bytes is attempted. Simplest
            # deterministic approach: monkey-patch Path.write_bytes to explode
            # only for the broker tmp sibling.
            from unittest import mock
            original_write_bytes = Path.write_bytes

            def exploding_write_bytes(self, data):
                if self.name == "broker_filtered.jsonl.force-drain.tmp":
                    raise OSError("simulated broker append failure")
                return original_write_bytes(self, data)

            with mock.patch.object(Path, "write_bytes", exploding_write_bytes):
                with self.assertRaises(OSError):
                    tools_v15._force_drain_restart_state(root, "operator approved stale cleanup")

            # Council file must be byte-for-byte unchanged; broker also unchanged.
            self.assertEqual(runtime_path.read_bytes(), runtime_before)
            self.assertEqual(broker_path.read_bytes(), broker_before)

    def test_force_drain_reads_a_record_holding_a_u2028_as_one_record(self):
        # F2, review of #32: the broker writer keeps a raw U+2028 / U+0085 inside a
        # record (ensure_ascii=False). str.splitlines() cut that record in two, so a
        # stale dispatch that had one in its text raised FORCE_DRAIN_BROKER_PARSE_FAILED
        # and could never be drained. One record per "\n" (text_lines.split_lines).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_council_files(
                root,
                runtime={},
                broker_lines=(
                    json.dumps({"type": "message", "id": "stale-1", "ts": time.time() - 3600,
                                "text": "a" + chr(0x2028) + "b" + chr(0x85) + "c"},
                               ensure_ascii=False),
                ),
            )
            result = tools_v15._force_drain_restart_state(root, "operator approved stale cleanup")
            self.assertEqual(result["closed_dispatches"], ["stale-1"])

    def test_controller_unavailable_fallback_emits_blocking_guards(self):
        # The legacy fallback path must include `blocking_guards` in its
        # RESTART_GUARD_BLOCKED response, matching the controller-path shape
        # emitted by restart_control_service._execute_once.
        import asyncio
        from unittest import mock
        from orchestrator.lifecycle import request_orchestrator_restart

        class HealthyResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self): return b'{"ok":true}'

        with tempfile.TemporaryDirectory(prefix="restart-blocking-guards-") as tmp:
            root = Path(tmp)
            (root / "start_orchestrator.bat").write_text("@echo off\r\n", encoding="utf-8")
            cache = root / ".orchestrator_cache"
            cache.mkdir()
            (cache / "restart_control.secret").write_bytes(b"s" * 32)
            _write_council_files(
                root,
                runtime={"ope::model": {"state": "running"}},
                busy_ok=True,
            )
            with (
                mock.patch.object(tools_v15, "__file__", str(root / "tools_v15.py")),
                mock.patch.object(
                    request_orchestrator_restart,
                    "request_restart",
                    side_effect=request_orchestrator_restart.ControllerUnavailableBeforePost("refused"),
                ),
                mock.patch("urllib.request.urlopen", return_value=HealthyResponse()),
                mock.patch("subprocess.Popen") as popen,
            ):
                raw = asyncio.run(tools_v15.restart_orchestrator_via_bat(expected_generation="gen-a"))
            result = json.loads(raw)
            self.assertEqual(result["error_name"], "RESTART_GUARD_BLOCKED")
            self.assertIn("COUNCIL_SLOT_ACTIVE", result["reasons"])
            self.assertIn("blocking_guards", result)
            self.assertIsInstance(result["blocking_guards"], list)
            names = {g.get("name") for g in result["blocking_guards"]}
            self.assertIn("COUNCIL_SLOT_ACTIVE", names)
            for guard in result["blocking_guards"]:
                self.assertEqual(
                    set(guard), {"name", "held_by", "is_stale", "retryable", "how_to_clear"},
                    f"blocking_guards item missing required shape: {guard}",
                )
            popen.assert_not_called()

    def test_controller_rereads_replaced_secret_and_signed_reload_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_path = Path(tmp) / "restart_control.secret"
            first_secret = b"a" * 32
            second_secret = b"b" * 32
            secret_path.write_bytes(first_secret)
            reload_calls = []
            controller = RestartController(
                scheduler=_Scheduler(), guard=lambda _request: GuardDecision(True, ()),
                current_identity=lambda: ProcessIdentity(10, "gen-a"),
                prove_stopped=lambda _old: True,
                prove_started=lambda _old: ProcessIdentity(20, "gen-b"),
                append_audit=lambda _event: None,
            )
            server = create_control_server(
                controller=controller, secret_path=secret_path,
                current_generation=lambda: "gen-a",
                reload_guards=lambda: reload_calls.append(True) or GuardDecision(True, ()), port=0,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                secret_path.write_bytes(second_secret)
                reload_url = f"http://127.0.0.1:{server.server_port}/reload_guards"
                reload_request = urllib.request.Request(
                    reload_url, data=b"", method="POST",
                    headers={"X-Restart-Signature": sign_request(second_secret, "POST", "/reload_guards", b"")},
                )
                with urllib.request.urlopen(reload_request, timeout=2) as response:
                    payload = json.loads(response.read())
                self.assertTrue(payload["reloaded"])
                self.assertEqual(reload_calls, [True])

                body = encode_request("new-secret-request", "gen-a", time.time())
                restart_request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/restart", data=body, method="POST",
                    headers={"X-Restart-Signature": sign_request(second_secret, "POST", "/restart", body)},
                )
                with urllib.request.urlopen(restart_request, timeout=2) as response:
                    self.assertTrue(json.loads(response.read())["ok"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)


if __name__ == "__main__":
    unittest.main()
