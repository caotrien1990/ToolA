"""Sprint 1 tests: T4 bridge core (loop, lock, deadlines, call_seq fence,
loop recycle) + the D-S1 transport gate regression.

Filed plan: T4_HTTP_PLAN_20261110.md sections 2, 3.4, 5.1, 6;
Stage-1 synthesis decisions D-S1, D-S3, D-S4.
"""
import asyncio
import concurrent.futures
import json
import threading
import unittest

from orchestrator import t4_bridge
from orchestrator.t4_bridge import (
    BRIDGED_TOOLS, T4Bridge, deadline_for, resolve_page_ids, should_bridge,
)


def _ok(**extra):
    payload = {"ok": True}
    payload.update(extra)
    return json.dumps(payload)


async def _fast_handler(**_body):
    return _ok()


class BridgeCase(unittest.IsolatedAsyncioTestCase):
    def make_bridge(self, **kw):
        kw.setdefault("grace_s", 0.2)
        kw.setdefault("watchdog_tick_s", 60.0)
        bridge = T4Bridge(**kw)
        bridge.start(watchdog=False)
        self.addCleanup(bridge.stop)
        return bridge

    def effectuate(self, bridge, decision):
        future = asyncio.run_coroutine_threadsafe(
            bridge.effectuate_quarantine(decision), bridge._loop)
        return future.result(timeout=10)


class TestTransportGateDS1(BridgeCase):
    async def test_predicate(self):
        self.assertTrue(should_bridge("click", "api"))
        self.assertFalse(should_bridge("click", "mcp"))
        self.assertFalse(should_bridge("read_file_range", "api"))

    async def test_mcp_submit_does_not_take_bridge_path(self):
        bridge = self.make_bridge()
        ran_on = []

        async def handler(**_body):
            ran_on.append(threading.get_ident())
            return _ok()

        result = await bridge.submit("click", handler, {}, transport="mcp")
        self.assertIsNone(result)  # seam falls through to direct invoke
        self.assertEqual(ran_on, [])  # handler never ran on the bridge

    async def test_api_submit_runs_on_bridge_loop_thread(self):
        bridge = self.make_bridge()
        ran_on = []

        async def handler(**_body):
            ran_on.append(threading.get_ident())
            return _ok()

        result = await bridge.submit("click", handler, {}, transport="api")
        self.assertEqual(json.loads(result), {"ok": True})
        self.assertEqual(ran_on, [bridge._loop_thread.ident])

    async def test_bridged_set_is_the_plan_routing_set(self):
        self.assertTrue({"cleanup_test_session", "query_dom_candidates"} <= BRIDGED_TOOLS)
        self.assertIn("compare_dom_snapshot", BRIDGED_TOOLS)
        self.assertIn("verify_active_electron_source", BRIDGED_TOOLS)
        self.assertNotIn("t4_host_status", BRIDGED_TOOLS)


class TestDriverLock(BridgeCase):
    async def test_second_driver_gets_t4_busy_after_grace(self):
        bridge = self.make_bridge()
        started = threading.Event()
        release = threading.Event()

        async def slow(**_body):
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return _ok()

        first = asyncio.create_task(
            bridge.submit("click", slow, {}, agent_id="agent-a"))
        self.assertTrue(await asyncio.to_thread(started.wait, 5))
        busy_raw = await bridge.submit(
            "fill", _fast_handler, {}, agent_id="agent-b")
        busy = json.loads(busy_raw)
        self.assertEqual(busy["error_name"], "T4_BUSY")
        self.assertNotIn("agent", busy["holder"])
        self.assertNotIn("token", busy["holder"])
        self.assertEqual(busy["holder"]["tool"], "click")
        self.assertIn("held_ms", busy["holder"])
        self.assertTrue(busy["retryable"])
        release.set()
        self.assertEqual(json.loads(await first), {"ok": True})

    async def test_reads_are_lock_exempt(self):
        bridge = self.make_bridge()
        started = threading.Event()
        release = threading.Event()

        async def slow(**_body):
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return _ok()

        first = asyncio.create_task(bridge.submit("click", slow, {}))
        self.assertTrue(await asyncio.to_thread(started.wait, 5))
        read_raw = await bridge.submit("read_page", _fast_handler, {})
        self.assertEqual(json.loads(read_raw), {"ok": True})
        release.set()
        await first


class TestDeadlineCompleting(BridgeCase):
    async def test_timeout_answers_at_deadline_and_holds_to_completion(self):
        bridge = self.make_bridge(deadline_overrides={"click": 0.3})
        bridge.register_entry("pg-1", owner_agent="agent-a")

        async def slow(**_body):
            await asyncio.sleep(1.0)
            return _ok()

        raw = await bridge.submit(
            "click", slow, {"page_id": "pg-1"}, agent_id="agent-a")
        result = json.loads(raw)
        self.assertEqual(result["error_name"], "T4_CALL_TIMEOUT")
        self.assertEqual(result["deadline_ms"], 300)
        holder = bridge.status_snapshot()["holder"]
        self.assertIsNotNone(holder)
        self.assertTrue(holder["completing"])
        entry = bridge.get_entry("pg-1")
        self.assertEqual(entry.suspect["reason"], "call_timeout")
        self.assertEqual(len(entry.in_flight), 1)  # held past the answer
        await asyncio.sleep(1.0)  # observed completion frees everything
        self.assertIsNone(bridge.status_snapshot()["holder"])
        self.assertEqual(len(bridge.get_entry("pg-1").in_flight), 0)


class TestCallSeqAndFence(BridgeCase):
    async def test_call_seq_increments_once_per_submission(self):
        bridge = self.make_bridge()
        bridge.register_entry("pg-1", owner_agent="agent-a")
        for _ in range(3):
            await bridge.submit(
                "read_page", _fast_handler, {"page_id": "pg-1"},
                agent_id="agent-a")
        self.assertEqual(bridge.get_entry("pg-1").call_seq, 3)

    async def test_stale_decision_is_whole_unit_noop_then_fresh_applies(self):
        bridge = self.make_bridge()
        bridge.register_entry("pg-1", owner_agent="agent-a")
        # Wedged mutating call W: seq 1, in flight, holding token T.
        entry = bridge.get_entry("pg-1")
        entry.call_seq = 1
        entry.in_flight.add(1)
        token = bridge._next_token()
        with bridge._state_lock:
            bridge._holder = {"token": token, "agent": "agent-a",
                              "tool": "click", "acquired_at": 0.0,
                              "completing": True}
        decision = {"token": token, "page_id": "pg-1", "quarantined_seq": 1,
                    "entry_call_seq": 1, "loop_epoch": bridge.loop_epoch,
                    "case": "entry"}
        # A read R is submitted (seq 2) and completes: decision stales.
        await bridge.submit("read_page", _fast_handler,
                            {"page_id": "pg-1"}, agent_id="agent-a")
        self.assertEqual(await asyncio.to_thread(
            self.effectuate, bridge, decision), "quarantine_stale")
        entry = bridge.get_entry("pg-1")
        self.assertIsNotNone(entry)  # registration intact
        self.assertIn(1, entry.in_flight)  # W's contribution stands
        self.assertEqual(bridge._holder["token"], token)  # lock intact
        # Fresh decision against the current call_seq applies the unit.
        decision2 = dict(decision, entry_call_seq=entry.call_seq)
        self.assertEqual(await asyncio.to_thread(
            self.effectuate, bridge, decision2), "quarantined")
        self.assertIsNone(bridge.get_entry("pg-1"))
        self.assertIsNone(bridge._holder)

    async def test_holder_case_token_only(self):
        bridge = self.make_bridge()
        token = bridge._next_token()
        with bridge._state_lock:
            bridge._holder = {"token": token, "agent": "agent-a",
                              "tool": "fill_folder_dialog",
                              "acquired_at": 0.0, "completing": True}
        wrong = {"token": token + 99, "page_id": None,
                 "loop_epoch": bridge.loop_epoch, "case": "holder"}
        self.assertEqual(await asyncio.to_thread(
            self.effectuate, bridge, wrong), "quarantine_stale")
        self.assertIsNotNone(bridge._holder)
        right = dict(wrong, token=token)
        self.assertEqual(await asyncio.to_thread(
            self.effectuate, bridge, right), "quarantined")
        self.assertIsNone(bridge._holder)

    async def test_no_token_entry_case_attempts_no_release(self):
        bridge = self.make_bridge()
        bridge.register_entry("pg-1", owner_agent="agent-a")
        entry = bridge.get_entry("pg-1")
        entry.call_seq = 1
        entry.in_flight.add(1)
        other_token = bridge._next_token()
        with bridge._state_lock:
            bridge._holder = {"token": other_token, "agent": "agent-b",
                              "tool": "click", "acquired_at": 0.0,
                              "completing": False}
        decision = {"token": None, "page_id": "pg-1", "quarantined_seq": 1,
                    "entry_call_seq": 1, "loop_epoch": bridge.loop_epoch,
                    "case": "entry"}
        self.assertEqual(await asyncio.to_thread(
            self.effectuate, bridge, decision), "quarantined")
        self.assertIsNone(bridge.get_entry("pg-1"))
        self.assertEqual(bridge._holder["token"], other_token)  # untouched

    async def test_cross_recycle_decision_is_stale(self):
        bridge = self.make_bridge()
        decision = {"token": 1, "page_id": None,
                    "loop_epoch": bridge.loop_epoch + 5, "case": "holder"}
        self.assertEqual(await asyncio.to_thread(
            self.effectuate, bridge, decision), "quarantine_stale")


class TestLoopRecycle(BridgeCase):
    async def test_recycle_bumps_epoch_and_old_entries_fail_recycled(self):
        bridge = self.make_bridge()
        bridge.register_entry("pg-old", owner_agent="agent-a")
        old_epoch = bridge.loop_epoch
        bridge._loop.call_soon_threadsafe(bridge._loop.stop)
        bridge._loop_thread.join(timeout=5)
        bridge.ensure_loop()
        self.assertEqual(bridge.loop_epoch, old_epoch + 1)
        raw = await bridge.submit("read_page", _fast_handler,
                                  {"page_id": "pg-old"}, agent_id="agent-a")
        result = json.loads(raw)
        self.assertEqual(result["error_name"], "PAGE_NOT_FOUND")
        self.assertEqual(result["reason"], "t4_host_loop_recycled")

    async def test_no_live_loop_answers_host_unavailable(self):
        bridge = T4Bridge(grace_s=0.2)
        raw = await bridge.submit("read_page", _fast_handler, {})
        self.assertEqual(json.loads(raw)["error_name"],
                         "T4_HOST_UNAVAILABLE")


class TestMultiEntryAndSuspect(BridgeCase):
    def test_resolve_returns_all_ids_in_argument_order(self):
        self.assertEqual(
            resolve_page_ids({"page_id_b": "pg-2", "page_id_a": "pg-1"}),
            ["pg-1", "pg-2"])
        self.assertEqual(resolve_page_ids({"page_id": "pg-9"}), ["pg-9"])
        self.assertEqual(resolve_page_ids({}), [])

    async def test_first_failing_entry_names_the_error(self):
        bridge = self.make_bridge()
        bridge.register_entry("pg-a", owner_agent="owner-a")
        bridge.register_entry("pg-b", owner_agent="owner-b")
        raw = await bridge.submit(
            "compare_dom_snapshot", _fast_handler,
            {"page_id_a": "pg-a", "page_id_b": "pg-b"}, agent_id="owner-b")
        result = json.loads(raw)
        self.assertEqual(result["error_name"], "PAGE_NOT_FOUND")
        self.assertEqual(result["page_id"], "pg-a")  # first in arg order

    async def test_suspect_blocks_mutation_until_roundtrip_read_clears(self):
        bridge = self.make_bridge()
        bridge.register_entry("pg-1", owner_agent="agent-a")
        entry = bridge.get_entry("pg-1")
        entry.suspect = {"reason": "eval_timeout", "at": 0.0}
        raw = await bridge.submit("click", _fast_handler,
                                  {"page_id": "pg-1"}, agent_id="agent-a")
        self.assertEqual(json.loads(raw)["error_name"], "PAGE_SUSPECT")
        # A buffer read proceeds but does NOT clear the mark.
        raw = await bridge.submit("read_console_errors", _fast_handler,
                                  {"page_id": "pg-1"}, agent_id="agent-a")
        self.assertEqual(json.loads(raw), {"ok": True})
        self.assertIsNotNone(bridge.get_entry("pg-1").suspect)
        # An evaluate round-trip read clears it.
        raw = await bridge.submit("read_value", _fast_handler,
                                  {"page_id": "pg-1"}, agent_id="agent-a")
        self.assertEqual(json.loads(raw), {"ok": True})
        self.assertIsNone(bridge.get_entry("pg-1").suspect)

    async def test_owner_mismatch_is_page_not_found(self):
        bridge = self.make_bridge()
        bridge.register_entry("pg-1", owner_agent="owner-a")
        raw = await bridge.submit("read_page", _fast_handler,
                                  {"page_id": "pg-1"}, agent_id="owner-b")
        self.assertEqual(json.loads(raw)["error_name"], "PAGE_NOT_FOUND")


class TestDeadlineMap(unittest.TestCase):
    def test_plan_rows(self):
        self.assertEqual(deadline_for("read_page"), 60.0)
        self.assertEqual(deadline_for("cdp_attach_target"), 90.0)
        self.assertEqual(deadline_for("close_browser_page"), 15.0)
        self.assertEqual(deadline_for("desktop_screenshot"), 30.0)
        self.assertEqual(deadline_for("open_browser_page"), 50.0)
        self.assertEqual(deadline_for("open_browser_page",
                                      {"timeout_ms": 60000}), 90.0)
        self.assertEqual(deadline_for("evaluate_js"), 38.0)
        self.assertEqual(deadline_for("wait_for_selector"), 35.0)
        self.assertEqual(deadline_for("evaluate_js",
                                      {"timeout_ms": 200000}), 120.0)


if __name__ == "__main__":
    unittest.main()
