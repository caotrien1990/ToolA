"""A32: the async council dispatch paths must not silently drop failures.

THE DEFECT (verified live, generation da994c949562):
``council_message_async`` (and the two sibling async relays) ack the caller with
``ok:true, dispatched:true`` and then run the real work in a fire-and-forget
``asyncio.create_task(_bg())``. Inside ``_bg`` the callee ``_council_enqueue_or_run``
*returns* an error dict (COUNCIL_MODEL_UNAVAILABLE / AGENT_BUSY / AGENT_QUEUE_FULL /
QUEUE_WAIT_TIMEOUT) instead of raising, so the wrapping ``except Exception`` catches
nothing, the dict is discarded, and the failure vanishes: no log, no ledger terminal
row, no broker event. The leader sees ok:true and waits forever.

Three identical sites, all fixed here:
  1. council_message_async               (MCP/HTTP tool, primary fan-out path)
  2. handle_council_send_async           (POST /api/council/send-async relay)
  3. handle_council_send  ?async=1       (GET plain-text relay)

Coverage:
  (a) pre-ack model resolution — an unavailable model returns a REAL error response
      before the ack, and never schedules background work.  One test per site.
  (b) don't-drop-the-error — when the background enqueue returns ok:false, a terminal
      ``rejected`` ledger row is written and council_status reports ``stuck`` (not the
      forever-``dispatching`` that hid the bug).  All three sites x two error classes
      (a config one, COUNCIL_MODEL_UNAVAILABLE, and a backpressure one,
      AGENT_QUEUE_FULL).
"""

import asyncio
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import orchestrator_server as srv


def _write_worker_models(root: Path) -> None:
    (root / ".council" / "worker_models.json").write_text(
        json.dumps({
            "ope": {"provider": "opencode-zen", "model": "opencode/test-model"},
            "codo": {"provider": "codex", "model": "gpt-test-model"},
        }),
        encoding="utf-8",
    )


def _available_resolution():
    """A CouncilModelResolution that resolves cleanly (status != unavailable)."""
    return srv.CouncilModelResolution(
        requested_provider="opencode-zen", requested_model="opencode/test-model",
        resolved_provider="opencode-zen", resolved_model="opencode/test-model",
        resolution_status="unverified")


def _unavailable_resolution():
    return srv.CouncilModelResolution(
        requested_provider="opencode-zen", requested_model="agent/bogus-model",
        resolved_provider="", resolved_model="", resolution_status="unavailable",
        warnings=({"warning_name": "REQUESTED_MODEL_UNAVAILABLE",
                   "model": "agent/bogus-model"},))


# -- patch shorthands (keep the with-blocks short + readable) ----------------
def _patch_resolve(resolution):
    return mock.patch.object(srv, "_council_resolve_model", return_value=resolution)


def _patch_model_configured():
    return mock.patch.object(srv, "_council_worker_model_configured", return_value=True)


def _patch_enqueue(side_effect):
    return mock.patch.object(srv, "_council_enqueue_or_run", side_effect=side_effect)


class _FakeRequest:
    """Minimal stand-in for an aiohttp request (POST body form)."""

    def __init__(self, body=None, headers=None, query=None, match_info=None):
        self._body = body or {}
        self.headers = headers or {}
        self.query = query or {}
        self.match_info = match_info or {}

    async def json(self):
        return self._body


class _A32Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_here = srv._HERE
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".council").mkdir()
        _write_worker_models(self.root)
        srv._HERE = self.root
        # keep each test independent
        self._old_queues = dict(srv._COUNCIL_AGENT_QUEUES)
        srv._COUNCIL_AGENT_QUEUES.clear()
        self._old_runtime = dict(srv._COUNCIL_AGENT_RUNTIME)
        srv._COUNCIL_AGENT_RUNTIME.clear()

    async def asyncTearDown(self):
        # drain any stragglers so tasks don't leak across tests
        await self._drain_pending()
        srv._HERE = self.old_here
        srv._COUNCIL_AGENT_QUEUES.clear()
        srv._COUNCIL_AGENT_QUEUES.update(self._old_queues)
        srv._COUNCIL_AGENT_RUNTIME.clear()
        srv._COUNCIL_AGENT_RUNTIME.update(self._old_runtime)
        self.tmp.cleanup()

    async def _drain_pending(self):
        for _ in range(5):
            pending = [t for t in list(srv._COUNCIL_PENDING_TASKS) if not t.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)

    def _ledger(self, dispatch_id):
        return [r for r in srv._council_dispatch_read_ledger(500)
                if r.get("dispatch_id") == dispatch_id]

    async def _status(self, dispatch_id):
        return json.loads(await srv.council_status(dispatch_id=dispatch_id))


# ---------------------------------------------------------------------------
# (a) pre-ack model resolution — unavailable model = real error, no dispatch
# ---------------------------------------------------------------------------
class PreAckModelResolution(_A32Base):
    async def test_tool_council_message_async_rejects_unavailable_before_ack(self):
        enqueue = mock.AsyncMock()
        with (
            _patch_model_configured(),
            _patch_resolve(_unavailable_resolution()),
            mock.patch.object(srv, "_council_enqueue_or_run", enqueue),
        ):
            ack = json.loads(await srv.council_message_async(
                sender="clau", to="ope", text="probe", model="agent/bogus-model"))
        self.assertFalse(ack.get("ok"), ack)
        self.assertEqual(ack.get("error_name"), "COUNCIL_MODEL_UNAVAILABLE")
        enqueue.assert_not_called()
        # and no accepted ledger row was left dangling for a request that never ran
        rid = ack.get("dispatch_id") or ack.get("request_id") or ""
        self.assertEqual(self._ledger(rid), [])

    async def test_http_send_async_rejects_unavailable_before_ack(self):
        enqueue = mock.AsyncMock()
        req = _FakeRequest(body={"sender": "clau", "to": "ope", "text": "probe",
                                 "model": "agent/bogus-model"})
        with (
            mock.patch.object(srv, "_is_local", return_value=True),
            mock.patch.object(srv, "_origin_ok", return_value=True),
            mock.patch.object(srv, "_council_resolve_sender", return_value="clau"),
            _patch_model_configured(),
            _patch_resolve(_unavailable_resolution()),
            mock.patch.object(srv, "_council_enqueue_or_run", enqueue),
        ):
            resp = await srv.handle_council_send_async(req)
        payload = json.loads(resp.body.decode("utf-8"))
        self.assertFalse(payload.get("ok"), payload)
        self.assertEqual(payload.get("error_name"), "COUNCIL_MODEL_UNAVAILABLE")
        enqueue.assert_not_called()

    async def test_http_send_get_async_rejects_unavailable_before_ack(self):
        enqueue = mock.AsyncMock()
        req = _FakeRequest(query={"to": "ope", "text": "probe", "async": "1",
                                  "model": "agent/bogus-model"})
        with (
            mock.patch.object(srv, "_is_local", return_value=True),
            mock.patch.object(srv, "_origin_ok", return_value=True),
            mock.patch.object(srv, "_council_resolve_sender", return_value="clau"),
            _patch_resolve(_unavailable_resolution()),
            mock.patch.object(srv, "_council_enqueue_or_run", enqueue),
        ):
            resp = await srv.handle_council_send(req)
        text = resp.text
        self.assertIn("COUNCIL_MODEL_UNAVAILABLE", text)
        self.assertNotIn("ASYNC_DISPATCHED", text)
        enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# (b) don't-drop-the-error — a background failure is recorded terminally
# ---------------------------------------------------------------------------
def _returning_enqueue(error_name):
    async def _enqueue(*a, **k):
        return {"ok": False, "error_name": error_name, "error": f"{error_name} in bg"}
    return _enqueue


class BackgroundFailureRecorded(_A32Base):
    async def _assert_recorded(self, dispatch_id, error_name):
        rows = self._ledger(dispatch_id)
        terminal = [r for r in rows if r.get("state") in ("rejected", "failed")]
        self.assertTrue(terminal,
                        f"no terminal ledger row for {dispatch_id}; rows={rows}")
        self.assertIn(error_name, json.dumps(terminal[-1]))
        status = await self._status(dispatch_id)
        self.assertEqual(status.get("state"), "stuck",
                         f"forever-dispatching hides the drop: {status}")

    # -- site 1: council_message_async tool --
    async def _run_tool(self, error_name):
        with (
            _patch_model_configured(),
            _patch_resolve(_available_resolution()),
            _patch_enqueue(_returning_enqueue(error_name)),
        ):
            ack = json.loads(await srv.council_message_async(
                sender="clau", to="ope", text="bg failure probe"))
            self.assertTrue(ack.get("ok"))
            await self._drain_pending()
        await self._assert_recorded(ack["dispatch_id"], error_name)

    async def test_tool_config_error_recorded(self):
        await self._run_tool("COUNCIL_MODEL_UNAVAILABLE")

    async def test_tool_backpressure_error_recorded(self):
        await self._run_tool("AGENT_QUEUE_FULL")

    # -- site 2: handle_council_send_async POST relay --
    async def _run_post(self, error_name):
        req = _FakeRequest(body={"sender": "clau", "to": "ope",
                                 "text": "bg failure probe"})
        with (
            mock.patch.object(srv, "_is_local", return_value=True),
            mock.patch.object(srv, "_origin_ok", return_value=True),
            mock.patch.object(srv, "_council_resolve_sender", return_value="clau"),
            _patch_model_configured(),
            _patch_resolve(_available_resolution()),
            _patch_enqueue(_returning_enqueue(error_name)),
        ):
            resp = await srv.handle_council_send_async(req)
            payload = json.loads(resp.body.decode("utf-8"))
            self.assertTrue(payload.get("ok"))
            await self._drain_pending()
        await self._assert_recorded(payload["dispatch_id"], error_name)

    async def test_post_config_error_recorded(self):
        await self._run_post("COUNCIL_MODEL_UNAVAILABLE")

    async def test_post_backpressure_error_recorded(self):
        await self._run_post("AGENT_QUEUE_FULL")

    # -- site 3: handle_council_send GET ?async=1 relay --
    async def _run_get(self, error_name):
        req = _FakeRequest(query={"to": "ope", "text": "bg failure probe",
                                  "async": "1"})
        with (
            mock.patch.object(srv, "_is_local", return_value=True),
            mock.patch.object(srv, "_origin_ok", return_value=True),
            mock.patch.object(srv, "_council_resolve_sender", return_value="clau"),
            _patch_resolve(_available_resolution()),
            _patch_enqueue(_returning_enqueue(error_name)),
        ):
            resp = await srv.handle_council_send(req)
            text = resp.text
            self.assertIn("ASYNC_DISPATCHED", text)
            dispatch_id = ""
            for tok in text.split():
                if tok.startswith("request_id="):
                    dispatch_id = tok.split("=", 1)[1]
            self.assertTrue(dispatch_id.startswith("run-"), text)
            await self._drain_pending()
        await self._assert_recorded(dispatch_id, error_name)

    async def test_get_config_error_recorded(self):
        await self._run_get("COUNCIL_MODEL_UNAVAILABLE")

    async def test_get_backpressure_error_recorded(self):
        await self._run_get("AGENT_QUEUE_FULL")


# ---------------------------------------------------------------------------
# (c) council_status must not invent a restart cause without restart evidence
# ---------------------------------------------------------------------------
class StatusRestartGapCause(_A32Base):
    def _write_ledger_row(self, dispatch_id, mtime):
        path = srv._council_dispatch_ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": srv._council_now_iso(), "mtime": mtime,
               "dispatch_id": dispatch_id, "request_id": dispatch_id,
               "sender": "clau", "target": "ope", "state": "accepted",
               "queue_wait_timeout_sec": 0}
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def test_recent_accepted_does_not_claim_restart(self):
        did = "run-recent-ackgap"
        self._write_ledger_row(did, time.time())  # after this boot => no restart
        status = await self._status(did)
        self.assertEqual(status["state"], "dispatching")
        self.assertIsNone(status.get("gap_cause"),
                          "attributed a gap to a restart with no restart evidence")
        self.assertNotIn("restart-gap", status.get("reason", ""))

    async def test_pre_boot_accepted_is_a_genuine_restart_gap(self):
        did = "run-preboot-ackgap"
        self._write_ledger_row(did, srv.SERVER_STARTED_AT - 120.0)  # predates boot
        status = await self._status(did)
        self.assertEqual(status["state"], "dispatching")
        self.assertEqual(status.get("gap_cause"), "restart")


# ---------------------------------------------------------------------------
# (b') the mirror of (b): a SUCCESSFUL background run must stay unrecorded.
#
# Added during independent verification of the A32 fix. The (b) tests above all
# feed ok:false, so they pin "a failure IS recorded" but say nothing about the
# `result.get("ok")` guard inside _council_record_async_result. Deleting that
# guard -- so every completed dispatch is written as a terminal `rejected` row --
# left the whole council suite green. That is the worse direction of the two:
# a healthy dispatch would be permanently mislabelled as rejected in the ledger
# and reported `stuck` by council_status. Pin both directions.
# ---------------------------------------------------------------------------
def _succeeding_enqueue():
    async def _enqueue(*a, **k):
        return {"ok": True, "reply": "done", "agent": "ope"}
    return _enqueue


class BackgroundSuccessNotRecorded(_A32Base):
    async def _assert_not_rejected(self, dispatch_id):
        rows = self._ledger(dispatch_id)
        terminal = [r for r in rows if r.get("state") in ("rejected", "failed")]
        self.assertEqual(terminal, [],
                         f"a successful dispatch was recorded as terminal-failed: {terminal}")
        status = await self._status(dispatch_id)
        self.assertNotEqual(status.get("state"), "stuck",
                            f"successful dispatch reported stuck: {status}")

    async def test_tool_success_leaves_no_rejected_row(self):
        with (
            _patch_model_configured(),
            _patch_resolve(_available_resolution()),
            _patch_enqueue(_succeeding_enqueue()),
        ):
            ack = json.loads(await srv.council_message_async(
                sender="clau", to="ope", text="happy path probe"))
            self.assertTrue(ack.get("ok"), ack)
            await self._drain_pending()
        await self._assert_not_rejected(ack["dispatch_id"])

    async def test_post_success_leaves_no_rejected_row(self):
        req = _FakeRequest(body={"sender": "clau", "to": "ope",
                                 "text": "happy path probe"})
        with (
            mock.patch.object(srv, "_is_local", return_value=True),
            mock.patch.object(srv, "_origin_ok", return_value=True),
            mock.patch.object(srv, "_council_resolve_sender", return_value="clau"),
            _patch_model_configured(),
            _patch_resolve(_available_resolution()),
            _patch_enqueue(_succeeding_enqueue()),
        ):
            resp = await srv.handle_council_send_async(req)
            payload = json.loads(resp.body.decode("utf-8"))
            self.assertTrue(payload.get("ok"), payload)
            await self._drain_pending()
        dispatch_id = payload.get("dispatch_id") or payload.get("request_id") or ""
        self.assertTrue(dispatch_id, payload)
        await self._assert_not_rejected(dispatch_id)

    async def test_non_dict_result_is_not_recorded(self):
        """A bare None (a callee that returns nothing) is not a failure signal."""
        srv._council_record_async_result("run-none-probe", "clau", "ope", 0, None)
        self.assertEqual(self._ledger("run-none-probe"), [])


if __name__ == "__main__":
    unittest.main()
