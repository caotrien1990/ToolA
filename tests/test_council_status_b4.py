"""B4 (2026-08-08): regression tests for council_status + dispatch_id acks + durable record.

WHY each test exists (not merely what it asserts):
- dispatch_ack_carries_dispatch_id: parallel fan-out callers previously could not pair
  acks to replies because the HTTP ack only had request_id. Pin the dispatch_id field that
  the new council_status tool needs (it resolves whichever id is passed - request_id or
  dispatch_id, they must be equal for backward compat).
- ledger_written_on_dispatch:  the queued-before-start interval used to vanish after a
  restart (broker + runtime entries appear only later). The durable ledger closes that gap.
- unknown_id_returns_not_found: never fold missing dispatches into "dispatching" - the old
  "done via artifact marker" bug class was exactly a false positive on weak evidence.
- done_only_from_reply_ok_true: a runtime-completed slot implies done; broker reply ok=False
  implies stuck (approval/retry gate). This tests the deterministic precedence order.
- worker_alive_..._working / _dead_..._stuck: state derivation vs the live process check -
  a stale runtime slot must not be misread as running (council rule T0).
- fresh_spawn_grace_dispatching: a running slot with worker_pid=None is spawn grace; do NOT
  label it working prematurely (would hide a slow CLI spawn).
- ledger_only_restart_gap_dispatching: accepted record only (no runtime) -> dispatching;
  keeps restart-gap queryable without going stuck.
- old_message_no_runtime_stuck_dispatch_state_lost: if the broker message exists but no
  runtime ever started AND its age exceeds COUNCIL_STALL_LIMIT_SEC the state collapses to
  stuck (dispatch_state_lost). We lower the threshold locally; we do NOT add a second
  numeric constant - the same knob drives both quiet-stall and this lost-state rule.
- artifact_mtime_supporting_telemetry_none_when_missing: artifact is telemetry ONLY; a
  missing file must NOT pretend the dispatch exists, and must not contribute a fake 0.
- council_status_tool_registered: T1 tool is reachable via the normal exposure surfaces
  (ct.TOOL_MAP + ct.TOOL_SCHEMAS) as council_message_async is.
- council_pack_includes_council_status: HTTP-relay MCP-pack users see the new tool.

Test setup mirrors tests/test_council_async_dispatch.py: a temporary _HERE/.council,
stub _council_run_agent, no [Q] capture.
"""

import asyncio
import json
import time
import unittest
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import orchestrator_server


def _write_worker_models(root: Path) -> None:
    (root / ".council" / "worker_models.json").write_text(
        json.dumps({
            "ope": {"provider": "opencode-zen", "model": "opencode/test-model"},
            "codo": {"provider": "codex", "model": "gpt-test-model"},
            "kilo": {"provider": "ltnai", "model": "ltnai/test-model"},
        }),
        encoding="utf-8",
    )


class _StatusTmpBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_here = orchestrator_server._HERE
        self.old_run = orchestrator_server._council_run_agent
        self.old_capture = orchestrator_server._council_capture_to_q
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".council").mkdir()
        _write_worker_models(self.root)
        orchestrator_server._HERE = self.root
        orchestrator_server._council_capture_to_q = lambda *a, **k: None
        # Clear queue/runtime state to keep each test independent.
        self.old_queues = orchestrator_server._COUNCIL_AGENT_QUEUES
        orchestrator_server._COUNCIL_AGENT_QUEUES.clear()
        self.old_runtime = dict(orchestrator_server._COUNCIL_AGENT_RUNTIME)
        orchestrator_server._COUNCIL_AGENT_RUNTIME.clear()

    async def asyncTearDown(self):
        # P3 ROOT FIX (2026-10-02): a queued `_council_queue_drain` keeps running
        # after the ack-path tests return, and `_council_usage_append` resolves the
        # ledger through `_HERE` at append time -- so restoring the live `_HERE`
        # while the 2s-stub drain is in flight appended `opencode/test-model` rows
        # into the LIVE ledger (measured: 4 rows from this file, incl.
        # run-duplicate-active-b4). Wait for the drain to finish while `_HERE`
        # still points at the tmp root, THEN restore. Waits, never mutates state;
        # ~2s worst case (one stub sleep), only when a drain is actually running.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            with orchestrator_server._COUNCIL_AGENT_QUEUE_LOCK:
                draining = any(orchestrator_server._COUNCIL_AGENT_QUEUE_DRAINING.values())
                queued = any(orchestrator_server._COUNCIL_AGENT_QUEUES.values())
            if not draining and not queued:
                break
            await asyncio.sleep(0.05)
        orchestrator_server._HERE = self.old_here
        orchestrator_server._council_run_agent = self.old_run
        orchestrator_server._council_capture_to_q = self.old_capture
        orchestrator_server._COUNCIL_AGENT_QUEUES.clear()
        orchestrator_server._COUNCIL_AGENT_QUEUES.update(self.old_queues)
        orchestrator_server._COUNCIL_AGENT_RUNTIME.clear()
        orchestrator_server._COUNCIL_AGENT_RUNTIME.update(self.old_runtime)
        self.tmp.cleanup()

    # -- helpers ------------------------------------------------------------------
    def _ledger(self):
        return orchestrator_server._council_dispatch_read_ledger(200)

    def _runtime_state(self):
        return json.loads(
            (self.root / ".council" / "runtime_state.json").read_text(encoding="utf-8")
            if (self.root / ".council" / "runtime_state.json").exists() else "{}"
        )

    def _write_event(self, events):
        path = self.root / ".council" / orchestrator_server._COUNCIL_EVENT_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            for ev in events:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def _council_status_sync(self, dispatch_id):
        # The tool is a coroutine; run it inline.
        import asyncio as _asyncio
        return json.loads(_asyncio.get_event_loop().run_until_complete(
            orchestrator_server.council_status(dispatch_id=dispatch_id)
        ))


class TestAckAndLedger(_StatusTmpBase):
    """Dispatch acks + durable ledger (B4 contracts from P2 fan-out)."""

    def _stub_slow_run(self):
        # Signature MUST mirror the real _council_run_agent (12 params). The queue
        # drain calls it positionally with model_resolution/risk_level/dev_level;
        # a short stub raised TypeError inside the executor thread, so the sleep +
        # reply below never ran and the ack-path tests passed for the wrong reason.
        def _run(agent, prompt, timeout_sec=180, mode="broker", project_path="",
                 request_id="", runtime_key="", provider_override="", model_override="",
                 model_resolution=None, risk_level="unknown", dev_level="unknown"):
            time.sleep(2.0)
            return {"ok": True, "returncode": 0, "reply": f"reply-from-{agent}"}

        orchestrator_server._council_run_agent = _run

    async def test_council_message_async_ack_carries_dispatch_id(self):
        # The ack previously only exposed request_id; fan-out pairing needed dispatch_id.
        self._stub_slow_run()
        ack = json.loads(await orchestrator_server.council_message_async(
            sender="clau", to="ope", text="B4 ack contract probe"))
        self.assertTrue(ack.get("ok"))
        self.assertEqual(ack.get("request_id"), ack.get("dispatch_id"))
        self.assertTrue(str(ack.get("dispatch_id", "")).startswith("run-"))

    async def test_council_message_async_dispatched_true(self):
        self._stub_slow_run()
        ack = json.loads(await orchestrator_server.council_message_async(
            sender="clau", to="ope", text="B4 async dispatched flag probe"))
        self.assertTrue(ack.get("ok") and ack.get("dispatched"))

    async def test_ledger_written_on_dispatch(self):
        # Restart-gap guard: ledger exists BEFORE the worker spawns.
        self._stub_slow_run()
        ack = json.loads(await orchestrator_server.council_message_async(
            sender="clau", to="ope", text="B4 ledger probe"))
        ledger = self._ledger()
        match = [rec for rec in ledger
                 if rec.get("dispatch_id") == ack.get("dispatch_id")]
        self.assertEqual(len(match), 1, "durable accepted record missing")
        self.assertEqual(match[0].get("state"), "accepted")
        self.assertEqual(match[0].get("request_id"), ack.get("request_id"))
        self.assertEqual(match[0].get("target"), "ope")

    async def test_stub_worker_actually_runs_dispatch_reaches_done(self):
        # P17 (L-a guard): the ack-path tests above stay GREEN whether or not the
        # worker body ever runs, because the ack returns before dispatch. A stub
        # whose signature does not match the real _council_run_agent raises
        # TypeError INSIDE the executor thread; the drain then records the slot as
        # `stuck`, not `done`. This test drives the full drain and pins `done`, so
        # a future arity drift is caught by BEHAVIOR (terminal state), not by the
        # silent ack. Proven: broken 9-arg stub -> stuck; correct stub -> done.
        self._stub_slow_run()
        ack = json.loads(await orchestrator_server.council_message_async(
            sender="clau", to="ope", text="B4 worker-reachability probe"))
        did = ack["dispatch_id"]
        state = None
        for _ in range(80):
            await asyncio.sleep(0.1)
            status = json.loads(await orchestrator_server.council_status(dispatch_id=did))
            state = status.get("state")
            if state in ("done", "stuck"):
                break
        self.assertEqual(state, "done",
                         "worker body never completed — stub arity likely drifted "
                         "from _council_run_agent (TypeError swallowed in executor)")

    async def test_handoff_failure_is_terminal_rejected_not_dispatching(self):
        # WHY: pre-ack ledger writes made a failed relay look accepted forever.
        with mock.patch.object(orchestrator_server.asyncio, "to_thread", side_effect=OSError("relay unavailable")):
            ack = await orchestrator_server._council_dispatch_message(
                "clau", "ope", "handoff failure probe", async_dispatch=True)
        self.assertFalse(ack["ok"])
        self.assertEqual(ack["error_name"], "ASYNC_HANDOFF_FAILED")
        status = json.loads(await orchestrator_server.council_status(
            dispatch_id=ack["dispatch_id"]))
        self.assertEqual(status["state"], "stuck")
        self.assertNotEqual(status["state"], "dispatching")
        self.assertFalse(status["dispatch"]["accepted"])

    async def test_duplicate_active_request_id_is_rejected(self):
        # WHY: two active dispatches sharing one correlation id shadow each other.
        self._stub_slow_run()
        request_id = "run-duplicate-active-b4"
        first = json.loads(await orchestrator_server.council_message_async(
            sender="clau", to="ope", text="first owner", request_id=request_id))
        second = json.loads(await orchestrator_server.council_message_async(
            sender="clau", to="ope", text="second owner", request_id=request_id))
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["error_name"], "DUPLICATE_ACTIVE_REQUEST_ID")
        accepted = [rec for rec in self._ledger()
                    if rec.get("dispatch_id") == request_id and rec.get("state") == "accepted"]
        self.assertEqual(len(accepted), 1)

    async def test_http_concurrent_duplicate_request_id_has_one_owner(self):
        # WHY: the HTTP handler used to check for duplicates and append acceptance
        # separately, so concurrent request threads could both become owners.
        request_id = "run-http-concurrent-duplicate-b4"

        class _Request:
            headers = {}

            def __init__(self, text):
                self._text = text

            async def json(self):
                return {
                    "sender": "clau", "to": "ope", "text": self._text,
                    "request_id": request_id, "timeout_sec": 30,
                }

        async def _enqueue(*args, **kwargs):
            return None

        original_record = orchestrator_server._council_dispatch_record

        def _slow_record(*args, **kwargs):
            # Widen the old check/append gap. Atomic admission keeps this sleep
            # inside the ledger lock, forcing the other request to wait.
            time.sleep(0.1)
            return original_record(*args, **kwargs)

        def _invoke(text):
            return asyncio.run(orchestrator_server.handle_council_send_async(_Request(text)))

        patches = (
            mock.patch.object(orchestrator_server, "_is_local", return_value=True),
            mock.patch.object(orchestrator_server, "_origin_ok", return_value=True),
            mock.patch.object(orchestrator_server, "_council_worker_model_configured", return_value=True),
            mock.patch.object(orchestrator_server, "_council_enqueue_or_run", side_effect=_enqueue),
            mock.patch.object(orchestrator_server, "_council_dispatch_record", side_effect=_slow_record),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(_invoke, ("first owner", "second owner")))

        statuses = sorted(response.status for response in responses)
        payloads = [json.loads(response.body.decode("utf-8")) for response in responses]
        self.assertEqual(statuses, [200, 409])
        self.assertEqual(sum(1 for payload in payloads if payload.get("ok")), 1)
        duplicate = next(payload for payload in payloads if not payload.get("ok"))
        self.assertEqual(duplicate["error_name"], "DUPLICATE_ACTIVE_REQUEST_ID")
        accepted = [rec for rec in self._ledger()
                    if rec.get("dispatch_id") == request_id and rec.get("state") == "accepted"]
        self.assertEqual(len(accepted), 1)

class TestCounciStatusDerivation(_StatusTmpBase):
    """Direct derivation rules: deterministic precedence, no artifact leakage."""

    def test_unknown_id_returns_dispatch_not_found(self):
        out = self._council_status_sync("run-nonexistent-b4-xyz")
        self.assertFalse(out["ok"])
        self.assertEqual(out.get("error_name"), "DISPATCH_NOT_FOUND")

    def test_log_only_recent_gives_dispatching(self):
        # Broker message only, recent -> still in the spawn grace window.
        did = "run-recent-msg-only"
        self._write_event([
            {"type": "message", "id": f"evt-{did}", "from": "clau", "to": "ope",
             "mode": "broker", "request_id": did, "text": "recent probe",
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "mtime": time.time()},
        ])
        out = self._council_status_sync(did)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "dispatching")

    def test_log_only_old_gives_stuck_dispatch_state_lost(self):
        did = "run-stale-msg-only"
        old_mtime = time.time() - (orchestrator_server._COUNCIL_STALL_LIMIT_SEC + 60)
        self._write_event([
            {"type": "message", "id": f"evt-{did}", "from": "clau", "to": "ope",
             "mode": "broker", "request_id": did, "text": "stale probe",
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "mtime": old_mtime},
        ])
        out = self._council_status_sync(did)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "stuck")
        self.assertIn("dispatch_state_lost", out.get("reason", "") + out.get("stuck_reason", ""))

    def test_runtime_running_pid_alive_gives_working(self):
        rid = "run-pid-alive"
        runtime_entry = {
            "state": "running", "request_id": rid, "started_at": time.time(),
            "worker_pid": 999999,  # deliberately non-existent but we stub _pid_alive
            "agent": "ope", "runtime_key": "ope::k1",
        }
        runtime_path = self.root / ".council" / "runtime_state.json"
        runtime_path.write_text(json.dumps({"ope::k1": runtime_entry}), encoding="utf-8")
        with mock.patch.object(orchestrator_server, "_pid_alive", return_value=True):
            out = self._council_status_sync(rid)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "working")
        self.assertTrue(out["worker_pid_alive"])

    def test_runtime_running_pid_dead_gives_stuck(self):
        rid = "run-pid-dead"
        runtime_entry = {
            "state": "running", "request_id": rid, "started_at": time.time(),
            "worker_pid": 424242,
            "agent": "ope", "runtime_key": "ope::k1",
        }
        runtime_path = self.root / ".council" / "runtime_state.json"
        runtime_path.write_text(json.dumps({"ope::k1": runtime_entry}), encoding="utf-8")
        with mock.patch.object(orchestrator_server, "_pid_alive", return_value=False):
            out = self._council_status_sync(rid)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "stuck")

    def test_runtime_running_no_pid_gives_dispatching_spawn_grace(self):
        rid = "run-spawn-grace"
        runtime_entry = {
            "state": "running", "request_id": rid, "started_at": time.time(),
            "worker_pid": None, "agent": "ope", "runtime_key": "ope::k1",
        }
        runtime_path = self.root / ".council" / "runtime_state.json"
        runtime_path.write_text(json.dumps({"ope::k1": runtime_entry}), encoding="utf-8")
        out = self._council_status_sync(rid)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "dispatching")
        self.assertIn("grace", out.get("reason", ""))

    def test_runtime_terminal_error_gives_stuck(self):
        rid = "run-terminal-error"
        runtime_entry = {
            "state": "error", "request_id": rid, "started_at": time.time() - 10,
            "finished_at": time.time(), "error": "worker_process_exited",
            "worker_pid": None, "agent": "ope", "runtime_key": "ope::k1",
        }
        runtime_path = self.root / ".council" / "runtime_state.json"
        runtime_path.write_text(json.dumps({"ope::k1": runtime_entry}), encoding="utf-8")
        out = self._council_status_sync(rid)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "stuck")
        self.assertIn("runtime terminal", out.get("reason", "") + out.get("stuck_reason", ""))

    def test_runtime_terminal_completed_gives_done(self):
        rid = "run-terminal-completed"
        runtime_entry = {
            "state": "completed", "request_id": rid,
            "started_at": time.time() - 5, "finished_at": time.time(),
            "worker_pid": 1, "agent": "ope", "runtime_key": "ope::k1",
        }
        runtime_path = self.root / ".council" / "runtime_state.json"
        runtime_path.write_text(json.dumps({"ope::k1": runtime_entry}), encoding="utf-8")
        out = self._council_status_sync(rid)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "done")

    def test_broker_reply_overrides_runtime_done(self):
        # done MUST be inferred from broker reply ok=True, never from runtime alone.
        rid = "run-precedence-done"
        self._write_event([
            {"type": "message", "id": "evt-1", "from": "clau", "to": "ope",
             "mode": "broker", "request_id": rid, "text": "peer precedence probe",
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mtime": time.time()},
            {"type": "reply", "id": "evt-2", "from": "ope", "to": "clau",
             "mode": "broker", "in_reply_to": "evt-1", "ok": True,
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mtime": time.time()},
        ])
        runtime_entry = {
            "state": "error", "request_id": rid,
            "started_at": time.time() - 10, "finished_at": time.time(),
            "worker_pid": None, "agent": "ope", "runtime_key": "ope::k1",
            "error": "worker crashed",
        }
        runtime_path = self.root / ".council" / "runtime_state.json"
        runtime_path.write_text(json.dumps({"ope::k1": runtime_entry}), encoding="utf-8")
        out = self._council_status_sync(rid)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "done")

    def test_broker_reply_false_overrides_runtime_final_stuck(self):
        # stuck MUST be inferred from broker reply ok=False when a reply exists.
        rid = "run-precedence-stuck"
        self._write_event([
            {"type": "message", "id": "evt-11", "from": "clau", "to": "ope",
             "mode": "broker", "request_id": rid, "text": "peer precedence stuck probe",
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mtime": time.time()},
            {"type": "reply", "id": "evt-22", "from": "ope", "to": "clau",
             "mode": "broker", "in_reply_to": "evt-11", "ok": False,
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mtime": time.time()},
        ])
        runtime_entry = {
            "state": "completed", "request_id": rid,
            "started_at": time.time() - 10, "finished_at": time.time(),
            "worker_pid": None, "agent": "ope", "runtime_key": "ope::k1",
        }
        runtime_path = self.root / ".council" / "runtime_state.json"
        runtime_path.write_text(json.dumps({"ope::k1": runtime_entry}), encoding="utf-8")
        out = self._council_status_sync(rid)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "stuck")

    def test_ledger_only_gives_dispatching_restart_gap(self):
        # Before this fix a restart between ack and worker spawn made the dispatch
        # invisible. Ledger-only must not become stuck; it's the spawn grace.
        did = "run-restart-gap"
        ok = orchestrator_server._council_dispatch_record(
            did, "clau", "ope", did, 0)
        self.assertTrue(ok)
        out = self._council_status_sync(did)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "dispatching")

    def test_queue_item_priority_over_ledger(self):
        did = "run-queue-pending"
        ok = orchestrator_server._council_dispatch_record(did, "clau", "ope", did, 0)
        self.assertTrue(ok)
        with orchestrator_server._COUNCIL_AGENT_QUEUE_LOCK:
            orchestrator_server._COUNCIL_AGENT_QUEUES["ope::k1"] = deque([{
                "request_id": did, "sender": "clau", "target": "ope",
                "text": "x", "started": False,
            }])
        try:
            out = self._council_status_sync(did)
        finally:
            with orchestrator_server._COUNCIL_AGENT_QUEUE_LOCK:
                orchestrator_server._COUNCIL_AGENT_QUEUES.pop("ope::k1", None)
        self.assertTrue(out["ok"])
        self.assertEqual(out["state"], "dispatching")

    def test_artifact_mtime_is_supporting_telemetry_none_when_missing(self):
        # B4 explicitly forbids artifact markers from proving done; this test pins the
        # telemetry-only role: missing artifact => None (NOT 0), so any consumer that
        # ever used 0 as "fresh" cannot silently reintroduce the defect.
        rid = "run-artifact-missing"
        runtime_entry = {
            "state": "running", "request_id": rid, "started_at": time.time(),
            "worker_pid": None, "agent": "ope", "runtime_key": "ope::k1",
            "artifact_path": str(self.root / "missing-output.final.txt"),
        }
        runtime_path = self.root / ".council" / "runtime_state.json"
        runtime_path.write_text(json.dumps({"ope::k1": runtime_entry}), encoding="utf-8")
        out = self._council_status_sync(rid)
        self.assertTrue(out["ok"])
        self.assertIsNone(out.get("last_artifact_mtime"))

    def test_artifact_mtime_supporting_when_present(self):
        rid = "run-artifact-present"
        art = self.root / "actual-final.txt"
        art.write_text("END_done_marker", encoding="utf-8")
        runtime_entry = {
            "state": "running", "request_id": rid, "started_at": time.time(),
            "worker_pid": None, "agent": "ope", "runtime_key": "ope::k1",
            "artifact_path": str(art),
        }
        runtime_path = self.root / ".council" / "runtime_state.json"
        runtime_path.write_text(json.dumps({"ope::k1": runtime_entry}), encoding="utf-8")
        out = self._council_status_sync(rid)
        self.assertIsNotNone(out.get("last_artifact_mtime"))

    def test_last_edit_ts_prefers_newest_event_or_runtime(self):
        rid = "run-last-edit-ts"
        base = time.time() - 100
        self._write_event([
            {"type": "message", "id": "evt-le", "from": "clau", "to": "ope",
             "mode": "broker", "request_id": rid, "text": "ts probe",
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mtime": base},
        ])
        runtime_entry = {
            "state": "running", "request_id": rid, "started_at": base,
            "worker_pid": None, "agent": "ope", "runtime_key": "ope::k1",
            "last_monitor_at": base + 50, "last_output_at": base + 40,
        }
        runtime_path = self.root / ".council" / "runtime_state.json"
        runtime_path.write_text(json.dumps({"ope::k1": runtime_entry}), encoding="utf-8")
        out = self._council_status_sync(rid)
        self.assertTrue(out["ok"])
        self.assertEqual(out.get("last_edit_ts"), base + 50)


class TestCouncilWiring(_StatusTmpBase):
    """Registry / exposure wiring."""

    def test_council_status_tool_registered(self):
        # T1 read-only tool reachable via the normal ct.TOOL_MAP surface.
        import code_tools as ct
        self.assertIn("council_status", ct.TOOL_MAP)
        self.assertTrue(any(
            "council_status" == s.get("function", {}).get("name")
            for s in ct.TOOL_SCHEMAS
        ))

    def test_council_tool_schema_requires_dispatch_id(self):
        import code_tools as ct
        schema = next(
            (s for s in ct.TOOL_SCHEMAS
             if s.get("function", {}).get("name") == "council_status"), None)
        self.assertIsNotNone(schema)
        params = schema.get("function", {}).get("parameters", {})
        self.assertIn("dispatch_id", params.get("required", []))

    def test_council_pack_includes_council_status(self):
        # MCP-relay flows need council_status in the council pack allowlist.
        # If the import fails (generator restructure), fall back to reading the file.
        try:
            from orchestrator.mcp_packs.generator import BUILTIN_PACKS
            tools = [t.get("name") for t in
                     BUILTIN_PACKS.get("council", {}).get("tools", [])]
            self.assertIn("council_status", tools)
        except Exception:
            path = Path(__file__).resolve().parents[1] / "orchestrator" / \
                "mcp_packs" / "generator.py"
            src = path.read_text(encoding="utf-8")
            self.assertIn('"council_status"', src)


if __name__ == "__main__":
    unittest.main()
