"""P2 (2026-08-06): non-blocking council dispatch gate + correlation ids.

WHY: council_message used to block each caller until the worker replied
(queue_wait_timeout_sec default 6000s). A leader fanning out N parallel
dispatches held N HTTP connections open and hit HTTP headers timeouts. The
non-blocking handoff (previously running-worker-only) now honors an explicit
async_dispatch=True opt-in from ANY caller: it acks immediately with a
request_id that is stamped on the broker message event, and the worker reply
maps back via in_reply_to. The default sync path is unchanged - the
regression class below pins the blocking semantics.
"""

import asyncio
import io
import json
import time
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

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


class _CouncilTmpBase(unittest.IsolatedAsyncioTestCase):
    """Shared fixture: tmp _HERE/.council, configured worker models, no [Q] capture."""

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

    async def asyncTearDown(self):
        orchestrator_server._HERE = self.old_here
        orchestrator_server._council_run_agent = self.old_run
        orchestrator_server._council_capture_to_q = self.old_capture
        self.tmp.cleanup()


class TestAsyncDispatchGate(_CouncilTmpBase):
    """Gate level: the /api/council/send-async relay HTTP call is stubbed."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.old_urlopen = urllib.request.urlopen
        self.relay_calls = []

        def _fake_urlopen(req, timeout=0):
            self.relay_calls.append(json.loads(req.data.decode("utf-8")))
            return io.BytesIO(b'{"ok": true}')

        urllib.request.urlopen = _fake_urlopen

        def _never_run(*args, **kwargs):
            raise AssertionError("async handoff must not run the worker in-process")

        orchestrator_server._council_run_agent = _never_run

    async def asyncTearDown(self):
        urllib.request.urlopen = self.old_urlopen
        await super().asyncTearDown()

    async def test_non_running_caller_can_opt_in_async(self):
        # sender 'clau' is a council agent but NOT running: the old gate kept this
        # caller blocking; P2 opens the handoff via async_dispatch=True.
        result = await orchestrator_server._council_dispatch_message(
            "clau", "ope", "P2 gate probe", async_dispatch=True,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["dispatched"])
        self.assertEqual(result["reply"], "")
        rid = result.get("request_id") or ""
        self.assertTrue(rid.startswith("run-"))
        self.assertEqual(len(self.relay_calls), 1)
        relay = self.relay_calls[0]
        self.assertEqual(relay["sender"], "clau")
        self.assertEqual(relay["to"], "ope")
        self.assertEqual(relay["request_id"], rid)

    async def test_user_caller_can_opt_in_async(self):
        result = await orchestrator_server._council_dispatch_message(
            "user", "ope", "P2 user opt-in probe", async_dispatch=True,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("request_id", "").startswith("run-"))
        self.assertEqual(len(self.relay_calls), 1)

    async def test_parallel_async_dispatch_does_not_block_callers(self):
        # Each fake relay sleeps 0.5s; 5 SERIAL handoffs would take >= 2.5s.
        def _slow_urlopen(req, timeout=0):
            self.relay_calls.append(json.loads(req.data.decode("utf-8")))
            time.sleep(0.5)
            return io.BytesIO(b'{"ok": true}')

        urllib.request.urlopen = _slow_urlopen
        targets = ("ope", "codo", "kilo", "ope", "codo")
        t0 = time.monotonic()
        results = await asyncio.gather(*(
            orchestrator_server._council_dispatch_message(
                "clau", target, f"fan-out {i}", async_dispatch=True,
            )
            for i, target in enumerate(targets)
        ))
        elapsed = time.monotonic() - t0
        self.assertTrue(all(r["ok"] and r["dispatched"] for r in results))
        ids = [r["request_id"] for r in results]
        self.assertEqual(len(set(ids)), len(targets))
        self.assertLess(
            elapsed, 2.0,
            f"{len(targets)} parallel async dispatches took {elapsed:.2f}s - handoff is blocking",
        )


class TestCouncilMessageAsyncRoundTrip(_CouncilTmpBase):
    """Full daemon-loop path: council_message_async -> queue drain -> broker events."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.run_calls = []

        def _stub_run(agent, prompt, timeout_sec=180, mode="broker", project_path="",
                      request_id="", runtime_key="", provider_override="", model_override="",
                      model_resolution=None, risk_level="unknown", dev_level="unknown"):
            self.run_calls.append({"agent": agent, "request_id": request_id})
            time.sleep(2.0)  # simulate a slow worker run
            return {"ok": True, "returncode": 0, "reply": f"reply-from-{agent}"}

        orchestrator_server._council_run_agent = _stub_run

    def _read_events(self):
        path = self.root / ".council" / "council_events.jsonl"
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events

    async def test_async_ack_id_maps_to_broker_reply(self):
        targets = ("ope", "codo", "kilo")
        t0 = time.monotonic()
        acks = await asyncio.gather(*(
            orchestrator_server.council_message_async(sender="clau", to=t, text=f"task {t}")
            for t in targets
        ))
        ack_elapsed = time.monotonic() - t0
        parsed = [json.loads(a) for a in acks]
        self.assertTrue(all(p.get("ok") and p.get("dispatched") for p in parsed))
        ack_ids = [p.get("request_id") or "" for p in parsed]
        self.assertTrue(all(r.startswith("run-") for r in ack_ids))
        self.assertEqual(len(set(ack_ids)), len(targets))
        # Workers sleep 2s each: if the acks blocked on the replies this gather
        # would take >= 2s (the old headers-timeout failure mode).
        self.assertLess(
            ack_elapsed, 1.5,
            f"async acks took {ack_elapsed:.2f}s - dispatch is not fire-and-forget",
        )
        # Wait for the drains to finish and the broker log to hold 3 replies.
        deadline = time.monotonic() + 30
        events = []
        while time.monotonic() < deadline:
            events = self._read_events()
            if sum(1 for ev in events if ev.get("type") == "reply") >= len(targets):
                break
            await asyncio.sleep(0.1)
        messages = {ev.get("request_id"): ev for ev in events if ev.get("type") == "message"}
        replies = [ev for ev in events if ev.get("type") == "reply"]
        self.assertEqual(len(replies), len(targets))
        for rid in ack_ids:
            self.assertIn(rid, messages, f"no broker message event stamped with ack id {rid}")
            msg_id = messages[rid].get("id")
            matched = [ev for ev in replies if ev.get("in_reply_to") == msg_id]
            self.assertEqual(len(matched), 1, f"reply for dispatch {rid} did not map via in_reply_to")
        # The run layer saw the same correlation id end-to-end.
        run_ids = {c["request_id"] for c in self.run_calls}
        self.assertTrue(set(ack_ids).issubset(run_ids))


class TestCouncilSyncRegression(_CouncilTmpBase):
    """Backward compat: council_message without async_dispatch keeps blocking."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        def _stub_run(agent, prompt, timeout_sec=180, mode="broker", project_path="",
                      request_id="", runtime_key="", provider_override="", model_override="",
                      model_resolution=None, risk_level="unknown", dev_level="unknown"):
            time.sleep(1.0)
            return {"ok": True, "returncode": 0, "reply": "sync reply"}

        orchestrator_server._council_run_agent = _stub_run

    async def test_default_dispatch_stays_blocking_sync(self):
        t0 = time.monotonic()
        result = await orchestrator_server._council_dispatch_message(
            "user", "ope", "sync regression probe", queue=False,
        )
        elapsed = time.monotonic() - t0
        self.assertTrue(result["ok"])
        self.assertEqual(result["reply"], "sync reply")
        self.assertNotIn("request_id", result)
        self.assertGreaterEqual(
            elapsed, 0.9,
            f"sync dispatch returned after {elapsed:.2f}s - default blocking semantics regressed",
        )

    async def test_explicit_async_false_stays_sync(self):
        result = await orchestrator_server._council_dispatch_message(
            "user", "ope", "sync explicit false probe", queue=False, async_dispatch=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["reply"], "sync reply")
        self.assertNotIn("request_id", result)


if __name__ == "__main__":
    unittest.main()