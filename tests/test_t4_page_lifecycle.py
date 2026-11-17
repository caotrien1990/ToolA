"""Sprint 2 tests: page lifecycle (grammar unification, entry stamps,
attach binding 5.2(b)/(c)/(f), reaper, close_browser_page,
t4_host_status) + Sprint-1 G3b rider regressions (F-1, F-2).

Filed plan: T4_HTTP_PLAN_20261110.md sections 3.1-3.5, 5.2, 6;
Stage-1 synthesis decisions D-S5, D-S6, D-S7.
"""
import asyncio
import inspect
import json
import re
import sys
import threading
import time
import types
import unittest
import urllib.request
from unittest import mock

import tools_v15
from orchestrator import t4_bridge
from orchestrator.t4_bridge import BRIDGED_TOOLS, T4Bridge


def _ok(**extra):
    payload = {"ok": True}
    payload.update(extra)
    return json.dumps(payload)


class BridgeCase(unittest.IsolatedAsyncioTestCase):
    def make_bridge(self, **kw):
        kw.setdefault("grace_s", 0.2)
        kw.setdefault("watchdog_tick_s", 60.0)
        kw.setdefault("reaper_period_s", 3600.0)
        bridge = T4Bridge(**kw)
        bridge.start(watchdog=False)
        self.addCleanup(bridge.stop)
        return bridge


# --------------------------------------------------------------------------
# Grammar unification (plan 3.1, D-S5)
# --------------------------------------------------------------------------

class TestGrammar(unittest.IsolatedAsyncioTestCase):
    def test_mint_format_and_randomness(self):
        pid1 = tools_v15._mint_page_id("pg")
        pid2 = tools_v15._mint_page_id("pg")
        aid = tools_v15._mint_page_id("cdp")
        self.assertRegex(pid1, r"^pg-\d+-\d+-[0-9a-f]{6}$")
        self.assertRegex(aid, r"^cdp-\d+-\d+-[0-9a-f]{6}$")
        self.assertNotEqual(pid1, pid2)

    def test_unified_grammar_accepts_both_families(self):
        for good in ("pg-1-0-abcdef", "cdp-1-0-abcdef", "pg-123-0",
                     "cdp-1700000000000-3-00ff00"):
            self.assertIsNotNone(tools_v15._PAGE_ID_RE.fullmatch(good), good)
        for bad in ("xx-1-0-abcdef", "pg-", "cdp-", "pg-a b", "",
                    "websocket-1", "pg--x"):
            self.assertIsNone(tools_v15._PAGE_ID_RE.fullmatch(bad), bad)

    async def test_get_page_taxonomy(self):
        _state, err = await tools_v15._get_page("cdp-999-0-abcdef")
        parsed = json.loads(err)
        self.assertEqual(parsed["error_name"], "PAGE_NOT_FOUND")
        _state, err = await tools_v15._get_page("pg-999-0-abcdef")
        self.assertEqual(json.loads(err)["error_name"], "PAGE_NOT_FOUND")
        _state, err = await tools_v15._get_page("not-a-page")
        parsed = json.loads(err)
        self.assertEqual(parsed["error_name"], "PAGE_ID_INVALID")
        self.assertIn("rand6", parsed["error"])

    def test_registry_pattern_is_unified(self):
        from orchestrator.core import registry_data
        spec = next(s for s in registry_data.V15_R8_NEW_SCHEMA_SPECS
                    if s[0] == "dom_state_diff")
        pattern = spec[2]["page_id"]["pattern"]
        self.assertIsNotNone(re.fullmatch(pattern, "cdp-1-0-abcdef"))
        self.assertIsNotNone(re.fullmatch(pattern, "pg-1-0-abcdef"))
        self.assertIsNone(re.fullmatch(pattern, "xx-1"))


# --------------------------------------------------------------------------
# Attach binding -- bridge preflight (plan 5.2(a)/(b) precedence)
# --------------------------------------------------------------------------

class TestAttachPreflight(BridgeCase):
    async def _submit(self, bridge, body):
        called = []

        async def handler(**_body):
            called.append(True)
            return _ok(page_id="cdp-1-0-abcdef")

        raw = await bridge.submit(
            "cdp_attach_target", handler, body, agent_id="agent-a")
        return json.loads(raw), called

    async def test_missing_target_id_is_first_refusal(self):
        bridge = self.make_bridge()
        result, called = await self._submit(bridge, {})
        self.assertEqual(result["error_name"], "CDP_TARGET_AMBIGUOUS")
        self.assertEqual(result["reason"], "target_id_required")
        self.assertEqual(called, [])

    async def test_websocket_only_mode_refused_for_bridged(self):
        bridge = self.make_bridge()
        result, called = await self._submit(
            bridge, {"websocket_url": "ws://127.0.0.1:9223/x",
                     "expected_title_substr": "App"})
        self.assertEqual(result["reason"], "target_id_required")
        self.assertEqual(called, [])

    async def test_expectation_required(self):
        bridge = self.make_bridge()
        result, called = await self._submit(bridge, {"target_id": "T1"})
        self.assertEqual(result["error_name"], "CDP_TARGET_AMBIGUOUS")
        self.assertEqual(result["reason"], "expectation_required")
        self.assertEqual(result["ca_next_action"],
                         "declare_expectation_then_retry")
        self.assertEqual(called, [])

    async def test_vacuous_expectation_required(self):
        bridge = self.make_bridge()
        result, called = await self._submit(
            bridge, {"target_id": "T1", "expected_title_substr": "   ",
                     "expected_url_substr": ""})
        self.assertEqual(result["reason"], "expectation_required")
        self.assertEqual(called, [])

    async def test_declared_expectation_reaches_handler(self):
        bridge = self.make_bridge()
        result, called = await self._submit(
            bridge, {"target_id": "T1", "expected_url_substr": "app"})
        self.assertTrue(result["ok"])
        self.assertEqual(called, [True])


# --------------------------------------------------------------------------
# Attach binding -- handler-side unique live match (plan 5.2(c))
# --------------------------------------------------------------------------

class FakePage:
    def __init__(self, title, url):
        self._title = title
        self.url = url

    async def title(self):
        return self._title

    def on(self, _event, _cb):
        pass

    def is_closed(self):
        return False


class BadPage(FakePage):
    async def title(self):
        raise RuntimeError("title boom")


class FakeContext:
    def __init__(self, pages):
        self.pages = list(pages)

    async def new_page(self):
        page = FakePage("", "")
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, contexts):
        self.contexts = list(contexts)

    def is_connected(self):
        return True

    def on(self, _event, _cb):
        pass

    async def new_context(self):
        ctx = FakeContext([])
        self.contexts.append(ctx)
        return ctx


class FakePW:
    def __init__(self, browser):
        self._browser = browser
        self.stopped = False
        outer = self

        class _Chromium:
            async def connect_over_cdp(self, _url):
                return outer._browser

        self.chromium = _Chromium()

    async def stop(self):
        self.stopped = True


class _Starter:
    def __init__(self, pw):
        self._pw = pw

    async def start(self):
        return self._pw


class _Resp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class TestAttachHandlerBinding(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved_pages = dict(tools_v15._BROWSER_PAGES)
        self.addCleanup(self._restore)

    def _restore(self):
        tools_v15._BROWSER_PAGES.clear()
        tools_v15._BROWSER_PAGES.update(self._saved_pages)

    async def _attach(self, browser, targets, **kwargs):
        pw = FakePW(browser)
        fake_api = types.SimpleNamespace(
            async_playwright=lambda: _Starter(pw))
        fake_playwright = types.ModuleType("playwright")
        fake_api_mod = types.ModuleType("playwright.async_api")
        fake_api_mod.async_playwright = fake_api.async_playwright
        fake_playwright.async_api = fake_api_mod
        with mock.patch.object(tools_v15, "_playwright_available",
                               lambda: True), \
                mock.patch.dict(sys.modules, {
                    "playwright": fake_playwright,
                    "playwright.async_api": fake_api_mod}), \
                mock.patch("urllib.request.urlopen",
                           lambda *_a, **_k: _Resp(targets)):
            raw = await tools_v15.cdp_attach_target(**kwargs)
        return json.loads(raw), pw

    def _targets(self):
        return [{"id": "T1", "webSocketDebuggerUrl": "ws://x/1",
                 "title": "SnapshotTitle", "url": "http://snapshot/",
                 "type": "page"}]

    async def test_unique_live_match_binds_matching_page(self):
        other = FakePage("Other App", "http://other/")
        wanted = FakePage("My App Main", "http://app/main")
        browser = FakeBrowser([FakeContext([other]), FakeContext([wanted])])
        result, _pw = await self._attach(
            browser, self._targets(), target_id="T1",
            expected_title_substr="my app")
        self.assertTrue(result["ok"], result)
        self.assertRegex(result["page_id"], r"^cdp-\d+-\d+-[0-9a-f]{6}$")
        entry = tools_v15._BROWSER_PAGES[result["page_id"]]
        self.assertIs(entry["page"], wanted)
        self.assertEqual(entry["kind"], "attached")
        self.assertEqual(entry["target_fingerprint"]["target_id"], "T1")

    async def test_zero_live_matches_mismatch_and_nothing_registered(self):
        browser = FakeBrowser([FakeContext([FakePage("Other", "http://o/")])])
        before = set(tools_v15._BROWSER_PAGES)
        result, pw = await self._attach(
            browser, self._targets(), target_id="T1",
            expected_url_substr="app/main")
        self.assertEqual(result["error_name"], "CDP_TARGET_MISMATCH")
        self.assertTrue(pw.stopped)
        self.assertEqual(set(tools_v15._BROWSER_PAGES), before)

    async def test_two_live_matches_ambiguous_with_candidates(self):
        browser = FakeBrowser([FakeContext([
            FakePage("App One", "http://app/1"),
            FakePage("App Two", "http://app/2")])])
        before = set(tools_v15._BROWSER_PAGES)
        result, pw = await self._attach(
            browser, self._targets(), target_id="T1",
            expected_title_substr="app")
        self.assertEqual(result["error_name"], "CDP_TARGET_AMBIGUOUS")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertTrue(pw.stopped)
        self.assertEqual(set(tools_v15._BROWSER_PAGES), before)

    async def test_target_not_listed_is_mismatch_with_reason(self):
        browser = FakeBrowser([FakeContext([FakePage("App", "http://app/")])])
        result, _pw = await self._attach(
            browser, [], target_id="T-missing",
            expected_title_substr="App")
        self.assertEqual(result["error_name"], "CDP_TARGET_MISMATCH")
        self.assertEqual(result["reason"], "target_not_listed")

    async def test_skipped_page_does_not_block_unique_match(self):
        bad = BadPage("x", "http://bad/")
        good = FakePage("Good App", "http://good/")
        browser = FakeBrowser([FakeContext([bad, good])])
        page, _ctx, err, diag = await tools_v15._bind_attach_page_by_expectation(
            browser, "good", "", time.monotonic())
        self.assertIsNone(err)
        self.assertIs(page, good)
        self.assertEqual(diag["skipped_pages"], ["ctx0.page0"])

    async def test_budget_exhaustion_skips_everything(self):
        browser = FakeBrowser([FakeContext([
            FakePage("App", "http://app/"), FakePage("App2", "http://a2/")])])
        page, _ctx, err, diag = await tools_v15._bind_attach_page_by_expectation(
            browser, "app", "", time.monotonic() - 100.0)
        self.assertIsNone(page)
        parsed = json.loads(err)
        self.assertEqual(parsed["error_name"], "CDP_TARGET_MISMATCH")
        self.assertEqual(diag["skipped_pages"], ["ctx0.page0", "ctx0.page1"])
        self.assertEqual(parsed["skipped_pages"], ["ctx0.page0", "ctx0.page1"])


# --------------------------------------------------------------------------
# Attach fallback refusal + connect-timeout shield (plan 5.2(f)/(c))
# --------------------------------------------------------------------------

class FakeStatePW:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class TestAttachFallbackAndTimeout(BridgeCase):
    def setUp(self):
        self._saved_pages = dict(tools_v15._BROWSER_PAGES)
        self.addCleanup(self._restore)

    def _restore(self):
        tools_v15._BROWSER_PAGES.clear()
        tools_v15._BROWSER_PAGES.update(self._saved_pages)

    async def test_fallback_refused_by_default_and_detached(self):
        bridge = self.make_bridge()
        pid = "cdp-1-0-abcdef"
        pw = FakeStatePW()
        tools_v15._BROWSER_PAGES[pid] = {"page": object(), "pw": pw}

        async def handler(**_body):
            return _ok(page_id=pid, attach_via="browser_level_fallback",
                       target_id="T1", title="App", url="http://app/")

        raw = await bridge.submit(
            "cdp_attach_target", handler,
            {"target_id": "T1", "expected_title_substr": "App"},
            agent_id="agent-a")
        result = json.loads(raw)
        self.assertEqual(result["error_name"], "CDP_ATTACH_FALLBACK_REFUSED")
        self.assertTrue(pw.stopped)
        self.assertNotIn(pid, tools_v15._BROWSER_PAGES)
        self.assertIsNone(bridge.get_entry(pid))

    async def test_fallback_opt_in_stands(self):
        bridge = self.make_bridge()
        pid = "cdp-1-0-abcdef"

        async def handler(**_body):
            return _ok(page_id=pid, attach_via="browser_level_fallback",
                       target_id="T1", title="App", url="http://app/")

        raw = await bridge.submit(
            "cdp_attach_target", handler,
            {"target_id": "T1", "expected_title_substr": "App",
             "allow_browser_level_fallback": True},
            agent_id="agent-a")
        self.assertTrue(json.loads(raw)["ok"])
        self.assertIsNotNone(bridge.get_entry(pid))

    async def test_connect_timeout_shielded_detach(self):
        bridge = self.make_bridge(
            deadline_overrides={"cdp_attach_target": 0.2})
        pid = "cdp-1-0-abcdef"
        pw = FakeStatePW()

        async def handler(**_body):
            await asyncio.sleep(0.8)
            tools_v15._BROWSER_PAGES[pid] = {"page": object(), "pw": pw}
            return _ok(page_id=pid, attach_via="page_level_ws",
                       target_id="T1", title="App", url="http://app/")

        raw = await bridge.submit(
            "cdp_attach_target", handler,
            {"target_id": "T1", "expected_title_substr": "App"},
            agent_id="agent-a")
        self.assertEqual(json.loads(raw)["error_name"], "T4_CALL_TIMEOUT")
        for _ in range(40):
            if pid not in tools_v15._BROWSER_PAGES and pw.stopped:
                break
            await asyncio.sleep(0.1)
        self.assertNotIn(pid, tools_v15._BROWSER_PAGES)
        self.assertTrue(pw.stopped)


# --------------------------------------------------------------------------
# Reaper (plan 3.4)
# --------------------------------------------------------------------------

class FakeClosable:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class TestReaper(BridgeCase):
    def setUp(self):
        self._saved_pages = dict(tools_v15._BROWSER_PAGES)
        self.addCleanup(self._restore)

    def _restore(self):
        tools_v15._BROWSER_PAGES.clear()
        tools_v15._BROWSER_PAGES.update(self._saved_pages)

    def _state(self):
        return {"page": object(), "context": FakeClosable(),
                "browser": FakeClosable(), "pw": FakeStatePW()}

    async def test_idle_launched_page_closed(self):
        bridge = self.make_bridge(idle_ttl_s=10.0, abs_ttl_s=100000.0)
        pid = "pg-1-0-abcdef"
        state = self._state()
        tools_v15._BROWSER_PAGES[pid] = state
        entry = bridge.register_entry(pid, kind="launched")
        reaped = await bridge.reap_once(now=entry.last_used_at + 11.0)
        self.assertEqual(reaped, [pid])
        self.assertTrue(state["context"].closed)
        self.assertTrue(state["browser"].closed)
        self.assertTrue(state["pw"].stopped)
        self.assertNotIn(pid, tools_v15._BROWSER_PAGES)
        self.assertIsNone(bridge.get_entry(pid))

    async def test_idle_attached_page_detached_only(self):
        bridge = self.make_bridge(idle_ttl_s=10.0, abs_ttl_s=100000.0)
        pid = "cdp-1-0-abcdef"
        state = self._state()
        tools_v15._BROWSER_PAGES[pid] = state
        entry = bridge.register_entry(pid, kind="attached")
        reaped = await bridge.reap_once(now=entry.last_used_at + 11.0)
        self.assertEqual(reaped, [pid])
        self.assertTrue(state["pw"].stopped)
        self.assertFalse(state["context"].closed)
        self.assertFalse(state["browser"].closed)

    async def test_absolute_cap_reaps_active_page(self):
        bridge = self.make_bridge(idle_ttl_s=100000.0, abs_ttl_s=100.0)
        pid = "pg-1-0-abcdef"
        tools_v15._BROWSER_PAGES[pid] = self._state()
        entry = bridge.register_entry(pid, kind="launched")
        entry.created_at = time.time() - 200.0
        entry.last_used_at = time.time()
        reaped = await bridge.reap_once()
        self.assertEqual(reaped, [pid])

    async def test_in_flight_and_fresh_entries_survive(self):
        bridge = self.make_bridge(idle_ttl_s=10.0, abs_ttl_s=100000.0)
        pid_busy = "pg-1-0-aaaaaa"
        pid_fresh = "pg-1-0-bbbbbb"
        tools_v15._BROWSER_PAGES[pid_busy] = self._state()
        tools_v15._BROWSER_PAGES[pid_fresh] = self._state()
        busy = bridge.register_entry(pid_busy, kind="launched")
        busy.in_flight.add(1)
        fresh = bridge.register_entry(pid_fresh, kind="launched")
        fresh.last_used_at = time.time() + 60.0
        reaped = await bridge.reap_once(now=time.time() + 11.0)
        self.assertEqual(reaped, [])
        self.assertIsNotNone(bridge.get_entry(pid_busy))
        self.assertIsNotNone(bridge.get_entry(pid_fresh))


# --------------------------------------------------------------------------
# close_browser_page (plan 3.5, D-S6 fresh implementation)
# --------------------------------------------------------------------------

class TestCloseBrowserPage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved_pages = dict(tools_v15._BROWSER_PAGES)
        self.addCleanup(self._restore)

    def _restore(self):
        tools_v15._BROWSER_PAGES.clear()
        tools_v15._BROWSER_PAGES.update(self._saved_pages)

    async def test_launched_close(self):
        pid = "pg-1-0-abcdef"
        state = {"page": object(), "context": FakeClosable(),
                 "browser": FakeClosable(), "pw": FakeStatePW(),
                 "kind": "launched"}
        tools_v15._BROWSER_PAGES[pid] = state
        result = json.loads(await tools_v15.close_browser_page(pid))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["closed"])
        self.assertFalse(result["detached"])
        self.assertTrue(state["context"].closed)
        self.assertTrue(state["browser"].closed)
        self.assertTrue(state["pw"].stopped)
        self.assertNotIn(pid, tools_v15._BROWSER_PAGES)

    async def test_attached_detach_only(self):
        pid = "cdp-1-0-abcdef"
        state = {"page": object(), "context": FakeClosable(),
                 "browser": FakeClosable(), "pw": FakeStatePW(),
                 "kind": "attached", "attach_mode": "cdp"}
        tools_v15._BROWSER_PAGES[pid] = state
        result = json.loads(await tools_v15.close_browser_page(pid))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["detached"])
        self.assertFalse(result["closed"])
        self.assertTrue(state["pw"].stopped)
        self.assertFalse(state["context"].closed)
        self.assertFalse(state["browser"].closed)
        self.assertNotIn(pid, tools_v15._BROWSER_PAGES)

    async def test_unknown_and_malformed(self):
        result = json.loads(await tools_v15.close_browser_page("cdp-9-9-ffffff"))
        self.assertEqual(result["error_name"], "PAGE_NOT_FOUND")
        result = json.loads(await tools_v15.close_browser_page("bogus"))
        self.assertEqual(result["error_name"], "PAGE_ID_INVALID")

    def test_fresh_implementation_not_the_dead_helper(self):
        src = inspect.getsource(tools_v15.close_browser_page)
        self.assertNotIn("_close_browser_page(", src)
        self.assertIsNot(tools_v15.close_browser_page,
                         tools_v15._close_browser_page)


# --------------------------------------------------------------------------
# t4_host_status + status snapshot scoping (plan 2)
# --------------------------------------------------------------------------

class TestHostStatus(BridgeCase):
    async def test_snapshot_scoping_and_quarantine_stale(self):
        bridge = self.make_bridge()
        bridge.register_entry("pg-1-0-aaaaaa", owner_agent="agent-a",
                              kind="launched")
        bridge.register_entry("pg-1-0-bbbbbb", owner_agent="agent-b",
                              kind="launched")
        decision = {"page_id": "pg-1-0-aaaaaa", "token": None,
                    "entry_call_seq": 999, "quarantined_seq": 1,
                    "loop_epoch": bridge.loop_epoch, "case": "entry"}
        future = asyncio.run_coroutine_threadsafe(
            bridge.effectuate_quarantine(decision), bridge._loop)
        self.assertEqual(future.result(timeout=10), "quarantine_stale")
        pages = {"pg-1-0-aaaaaa": {"page": object()}, "pg-1-0-bbbbbb": {"page": object()}}
        with mock.patch.dict(tools_v15._BROWSER_PAGES, pages, clear=True):
            snap = bridge.status_snapshot("agent-a")
        self.assertEqual([p["page_id"] for p in snap["pages"]],
                         ["pg-1-0-aaaaaa"])
        self.assertEqual(snap["pages"][0]["kind"], "launched")
        self.assertEqual(snap["other_owners_page_count"], 1)
        self.assertEqual(snap["quarantine_records"][-1]["state"],
                         "quarantine_stale")

    async def test_status_tool_handler(self):
        import code_tools
        with mock.patch.object(code_tools, "_t4_console_state", return_value={"console_locked": False}), \
                mock.patch.object(code_tools, "_t4_process_session_id", return_value=1):
            result = json.loads(await code_tools.t4_host_status(agent_id="agent-a"))
        self.assertTrue(result["ok"])
        for key in ("loop_alive", "loop_epoch", "holder", "pages",
                    "other_owners_page_count", "quarantine_records",
                    "daemon_session_id", "console_locked", "generation"):
            self.assertIn(key, result)

    def test_registration_lockstep(self):
        import code_tools as ct
        from orchestrator.core.registry import validate_registry
        self.assertIn("close_browser_page", ct.TOOL_MAP)
        self.assertEqual(ct.TOOL_PHASES["close_browser_page"], "T4")
        self.assertIn("t4_host_status", ct.TOOL_MAP)
        self.assertEqual(ct.TOOL_PHASES["t4_host_status"], "T1")
        schema_names = {(s.get("function") or {}).get("name")
                        for s in ct.TOOL_SCHEMAS}
        self.assertIn("close_browser_page", schema_names)
        self.assertIn("t4_host_status", schema_names)
        issues = validate_registry(ct.TOOL_MAP, ct.TOOL_SCHEMAS,
                                   ct.TOOL_PHASES)
        self.assertEqual(
            [i for i in issues if "close_browser_page" in i
             or "t4_host_status" in i], [])
        self.assertNotIn("t4_host_status", BRIDGED_TOOLS)
        self.assertIn("close_browser_page", BRIDGED_TOOLS)
        attach_schema = next(
            s["function"] for s in ct.TOOL_SCHEMAS
            if (s.get("function") or {}).get("name") == "cdp_attach_target")
        props = attach_schema["parameters"]["properties"]
        for name in ("expected_title_substr", "expected_url_substr",
                     "allow_browser_level_fallback"):
            self.assertIn(name, props)
        sig = inspect.signature(tools_v15.cdp_attach_target)
        for name in ("expected_title_substr", "expected_url_substr",
                     "allow_browser_level_fallback"):
            self.assertIn(name, sig.parameters)


# --------------------------------------------------------------------------
# Rider regressions (Sprint 1 G3b F-1 / F-2)
# --------------------------------------------------------------------------

class TestRiderF1(BridgeCase):
    async def test_read_timeout_does_not_mark_holder_completing(self):
        bridge = self.make_bridge(deadline_overrides={
            "click": 5.0, "read_page": 0.2})
        started = threading.Event()

        async def slow_click(**_body):
            started.set()
            await asyncio.sleep(2.0)
            return _ok()

        async def slow_read(**_body):
            await asyncio.sleep(1.0)
            return _ok()

        holder_task = asyncio.create_task(
            bridge.submit("click", slow_click, {}, agent_id="agent-a"))
        self.assertTrue(await asyncio.to_thread(started.wait, 5))
        raw = await bridge.submit(
            "read_page", slow_read, {"page_id": "pg-1-0-abcdef"},
            agent_id="agent-b")
        self.assertEqual(json.loads(raw)["error_name"], "T4_CALL_TIMEOUT")
        self.assertIsNotNone(bridge._holder)
        self.assertFalse(bridge._holder["completing"])
        self.assertEqual(json.loads(await holder_task), {"ok": True})

    async def test_holders_own_timeout_marks_completing(self):
        bridge = self.make_bridge(deadline_overrides={"click": 0.2})

        async def slow(**_body):
            await asyncio.sleep(1.0)
            return _ok()

        raw = await bridge.submit("click", slow, {}, agent_id="agent-a")
        self.assertEqual(json.loads(raw)["error_name"], "T4_CALL_TIMEOUT")
        holder = bridge.status_snapshot()["holder"]
        self.assertTrue(holder["completing"])


class TestRiderF2(unittest.TestCase):
    def test_seam_import_failure_answers_unavailable(self):
        import pathlib
        src = pathlib.Path("orchestrator_server.py").read_text(
            encoding="utf-8", errors="replace")
        anchor = src.index("from orchestrator import t4_bridge as _t4_seam")
        region = src[anchor:anchor + 2200]
        self.assertIn("T4_HOST_UNAVAILABLE", region)
        # The old silent-fallback shape (except -> applies=False with no
        # unavailable answer) must not survive in the guard region.
        guard = region.split("if _t4_applies:")[0]
        self.assertIn("_t4_raw = json.dumps", guard)


if __name__ == "__main__":
    unittest.main()
