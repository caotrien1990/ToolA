"""Sprint 3 tests: dialog targeting (plan 5.2(g) bridge census + handler
bound re-check), class-G executor wiring, CDP fetch offload (D-S2), and
the section 3.4 fence fixtures Sprint 1 left uncovered (real
cross-recycle, late completion).

Fixture-level only: synthetic enumeration entries and fake win32 modules
-- no live desktop actions, no live clicks.

Filed plan: T4_HTTP_PLAN_20261110.md sections 3.4, 5.1, 5.2(g), 6, 9, 10;
Stage-1 synthesis decision D-S2.
"""
import ast
import asyncio
import inspect
import json
import sys
import threading
import time
import types
import unittest
import urllib.request
from unittest import mock

import code_tools
import tools_v15
from orchestrator.t4_bridge import (
    BRIDGED_TOOLS, CLASS_G_TOOLS, T4Bridge, cancel_census_candidates,
    dialog_pin_error, fill_census_candidates,
)
from orchestrator.runtime import desktop_dialog_controls, desktop_dialogs

PID = 4242
OWNER = 50


def _ok(**extra):
    payload = {"ok": True}
    payload.update(extra)
    return json.dumps(payload)


def _win(hwnd, title, pid=PID, class_name="#32770", owner=0, parent=0,
         visible=True):
    """One synthetic desktop_window_list enumeration entry. The
    is_dialog_class flag is deliberately the enumeration's narrow
    (#32770-only) one: the census must read class_name, never the flag."""
    return {"hwnd": hwnd, "title": title, "pid": pid,
            "class_name": class_name, "owner_hwnd": owner,
            "parent_hwnd": parent, "visible": visible,
            "is_dialog_class": class_name == "#32770"}


# ---------------------------------------------------------------------------
# Census predicates (pure, over synthetic enumeration entries)
# ---------------------------------------------------------------------------

class TestCensusPredicates(unittest.TestCase):
    def test_fill_unowned_dialog_class_main_yields_zero(self):
        main = _win(300, "AppMain", owner=0)
        body = {"title_pattern": "AppMain", "owner_pid": PID}
        self.assertEqual(fill_census_candidates([main], body), [])

    def test_fill_owned_dialog_class_main_proceeds(self):
        # Section 10 declared residual: an OWNED dialog-class main
        # satisfies the conjunction exactly as a summoned dialog does.
        main = _win(300, "AppMain", owner=OWNER)
        body = {"title_pattern": "AppMain", "owner_pid": PID}
        self.assertEqual(fill_census_candidates([main], body), [main])

    def test_fill_owned_dialog_class_main_parent_form_proceeds(self):
        main = _win(300, "AppMain", owner=OWNER)
        body = {"title_pattern": "AppMain", "parent_hwnd": OWNER}
        self.assertEqual(fill_census_candidates([main], body), [main])

    def test_fill_unowned_dialog_class_main_parent_form_zero(self):
        main = _win(300, "AppMain", owner=0)
        body = {"title_pattern": "AppMain", "parent_hwnd": OWNER}
        self.assertEqual(fill_census_candidates([main], body), [])

    def test_fill_non_dialog_main_yields_zero_even_owned(self):
        main = _win(300, "AppMain", class_name="CabinetWClass", owner=OWNER)
        body = {"title_pattern": "AppMain", "owner_pid": PID}
        self.assertEqual(fill_census_candidates([main], body), [])

    def test_fill_dialog_class_read_from_class_name_not_flag(self):
        # Class name containing 'dialog' counts under fill's
        # _is_dialog_class semantics even though the enumeration's
        # #32770-only flag says False.
        dlg = _win(400, "Select Folder", class_name="AcmeDialogWindow",
                   owner=OWNER)
        self.assertFalse(dlg["is_dialog_class"])
        body = {"title_pattern": "Select Folder", "owner_pid": PID}
        self.assertEqual(fill_census_candidates([dlg], body), [dlg])

    def test_fill_dialog_plus_main_pattern_selects_dialog(self):
        main = _win(300, "AppMain", class_name="CabinetWClass", owner=0)
        dlg = _win(400, "Select Folder", owner=OWNER)
        body = {"title_pattern": "Select Folder", "owner_pid": PID}
        self.assertEqual(fill_census_candidates([main, dlg], body), [dlg])

    def test_fill_filters_class_pattern_and_excludes(self):
        dlg = _win(400, "Select Folder", owner=OWNER)
        body = {"title_pattern": "Select", "owner_pid": PID,
                "class_name_pattern": "nomatch"}
        self.assertEqual(fill_census_candidates([dlg], body), [])
        body = {"title_pattern": "Select", "owner_pid": PID,
                "exclude_titles": ["folder"]}
        self.assertEqual(fill_census_candidates([dlg], body), [])

    def test_cancel_unowned_dialog_class_main_yields_zero(self):
        main = _win(300, "AppMain", owner=0)
        body = {"title_pattern": "AppMain", "owner_pid": PID}
        self.assertEqual(cancel_census_candidates([main], body), [])

    def test_cancel_owned_dialog_class_main_proceeds(self):
        main = _win(300, "AppMain", owner=OWNER)
        body = {"title_pattern": "AppMain", "owner_pid": PID}
        self.assertEqual(cancel_census_candidates([main], body), [main])

    def test_cancel_owned_dialog_class_main_parent_form_proceeds(self):
        main = _win(300, "AppMain", owner=OWNER)
        body = {"title_pattern": "AppMain", "parent_hwnd": OWNER}
        self.assertEqual(cancel_census_candidates([main], body), [main])

    def test_cancel_non_dialog_titled_window_excluded_by_conjunction(self):
        # Cancel's own matcher accepts any titled window; the conjoined
        # dialog-class test is what excludes this one.
        main = _win(300, "AppMain", class_name="CabinetWClass", owner=OWNER)
        body = {"title_pattern": "AppMain", "owner_pid": PID}
        self.assertEqual(cancel_census_candidates([main], body), [])

    def test_pin_rules(self):
        self.assertEqual(dialog_pin_error("fill_folder_dialog", {}),
                         "missing_owner_pin")
        self.assertEqual(
            dialog_pin_error("cancel_native_dialog", {"owner_pid": PID}),
            "missing_title_pattern")
        self.assertIsNone(dialog_pin_error(
            "fill_folder_dialog",
            {"owner_pid": PID, "title_pattern": "Select"}))
        # The parent_hwnd form is exempt from the title-pattern rule.
        self.assertIsNone(dialog_pin_error(
            "cancel_native_dialog", {"parent_hwnd": OWNER}))


# ---------------------------------------------------------------------------
# Bridge-level dialog fixtures (census inside the class-G unit)
# ---------------------------------------------------------------------------

class DialogBridgeCase(unittest.IsolatedAsyncioTestCase):
    def make_bridge(self, windows_spec):
        state = {"calls": 0, "threads": []}

        async def enumerator():
            state["calls"] += 1
            state["threads"].append(threading.get_ident())
            wins = (windows_spec(state["calls"]) if callable(windows_spec)
                    else windows_spec)
            return [dict(w) for w in wins]

        bridge = T4Bridge(grace_s=0.2, watchdog_tick_s=60.0,
                          reaper_period_s=3600.0,
                          window_enumerator=enumerator)
        bridge.start(watchdog=False)
        self.addCleanup(bridge.stop)
        return bridge, state

    def recording_handler(self, store):
        async def handler(**body):
            store.append({
                "thread": threading.get_ident(),
                "thread_name": threading.current_thread().name,
                "body": dict(body)})
            return _ok(hwnd_used=body.get("expected_hwnd", 0))
        return handler


class TestBridgeDialogFixtures(DialogBridgeCase):
    async def _submit(self, bridge, tool, handler, body):
        raw = await bridge.submit(tool, handler, body, agent_id="agent-a")
        return json.loads(raw)

    async def test_missing_owner_pin_refuses_both_handlers(self):
        bridge, _state = self.make_bridge([])
        for tool, body in (
                ("fill_folder_dialog",
                 {"title_pattern": "Select", "folder_path": "C:\\x"}),
                ("cancel_native_dialog", {"title_pattern": "Select"})):
            store = []
            result = await self._submit(
                bridge, tool, self.recording_handler(store), body)
            self.assertEqual(result["error_name"],
                             "DIALOG_TARGET_AMBIGUOUS")
            self.assertEqual(result["reason"], "missing_owner_pin")
            self.assertEqual(store, [])  # handler never ran

    async def test_owner_pid_without_title_pattern_refuses_both(self):
        bridge, _state = self.make_bridge([])
        for tool, body in (
                ("fill_folder_dialog",
                 {"owner_pid": PID, "folder_path": "C:\\x"}),
                ("cancel_native_dialog",
                 {"owner_pid": PID, "title_pattern": ""})):
            store = []
            result = await self._submit(
                bridge, tool, self.recording_handler(store), body)
            self.assertEqual(result["reason"], "missing_title_pattern")
            self.assertEqual(store, [])

    async def test_unowned_dialog_class_main_refuses_both(self):
        main = _win(300, "AppMain", owner=0)
        bridge, _state = self.make_bridge([main])
        for tool, extra in (("fill_folder_dialog",
                             {"folder_path": "C:\\x"}),
                            ("cancel_native_dialog", {})):
            store = []
            body = {"title_pattern": "AppMain", "owner_pid": PID, **extra}
            result = await self._submit(
                bridge, tool, self.recording_handler(store), body)
            self.assertEqual(result["error_name"],
                             "DIALOG_TARGET_AMBIGUOUS")
            self.assertEqual(result["reason"], "no_matching_dialog")
            self.assertEqual(
                result["ca_next_action"],
                "enumerate_windows_and_pin_a_real_dialog_then_retry")
            self.assertEqual(store, [])

    async def test_owned_dialog_class_main_proceeds_both_pid_form(self):
        # Section 9 owned-main companion case (declared residual,
        # asserted as the declared disposition, not a safety property).
        main = _win(300, "AppMain", owner=OWNER)
        bridge, _state = self.make_bridge([main])
        for tool, extra in (("fill_folder_dialog",
                             {"folder_path": "C:\\x"}),
                            ("cancel_native_dialog", {})):
            store = []
            body = {"title_pattern": "AppMain", "owner_pid": PID, **extra}
            result = await self._submit(
                bridge, tool, self.recording_handler(store), body)
            self.assertEqual(result, {"ok": True, "hwnd_used": 300})
            self.assertEqual(store[0]["body"]["expected_hwnd"], 300)

    async def test_owned_dialog_class_main_proceeds_both_parent_form(self):
        main = _win(300, "AppMain", owner=OWNER)
        bridge, _state = self.make_bridge([main])
        for tool, extra in (("fill_folder_dialog",
                             {"folder_path": "C:\\x"}),
                            ("cancel_native_dialog", {})):
            store = []
            body = {"title_pattern": "AppMain", "parent_hwnd": OWNER,
                    **extra}
            result = await self._submit(
                bridge, tool, self.recording_handler(store), body)
            self.assertEqual(result, {"ok": True, "hwnd_used": 300})
            self.assertEqual(store[0]["body"]["expected_hwnd"], 300)

    async def test_non_dialog_main_only_refuses_both(self):
        main = _win(300, "AppMain", class_name="CabinetWClass", owner=0)
        bridge, _state = self.make_bridge([main])
        for tool, extra in (("fill_folder_dialog",
                             {"folder_path": "C:\\x"}),
                            ("cancel_native_dialog", {})):
            store = []
            body = {"title_pattern": "AppMain", "owner_pid": PID, **extra}
            result = await self._submit(
                bridge, tool, self.recording_handler(store), body)
            self.assertEqual(result["reason"], "no_matching_dialog")
            self.assertEqual(store, [])

    async def test_main_plus_dialog_proceeds_on_dialog(self):
        main = _win(300, "AppMain", class_name="CabinetWClass", owner=0)
        dlg = _win(400, "Select Folder", owner=OWNER)
        bridge, _state = self.make_bridge([main, dlg])
        store = []
        body = {"title_pattern": "Select Folder", "owner_pid": PID,
                "folder_path": "C:\\x"}
        result = await self._submit(
            bridge, "fill_folder_dialog", self.recording_handler(store),
            body)
        self.assertEqual(result, {"ok": True, "hwnd_used": 400})

    async def test_two_matching_dialogs_refuse_multiple(self):
        dlg1 = _win(400, "Select Folder", owner=OWNER)
        dlg2 = _win(401, "Select Folder", owner=OWNER)
        bridge, _state = self.make_bridge([dlg1, dlg2])
        for tool, extra in (("fill_folder_dialog",
                             {"folder_path": "C:\\x"}),
                            ("cancel_native_dialog", {})):
            store = []
            body = {"title_pattern": "Select Folder", "owner_pid": PID,
                    **extra}
            result = await self._submit(
                bridge, tool, self.recording_handler(store), body)
            self.assertEqual(result["reason"], "multiple_matches")
            self.assertEqual(len(result["candidates"]), 2)
            self.assertEqual(store, [])

    async def test_caller_supplied_expected_hwnd_is_discarded(self):
        dlg = _win(400, "Select Folder", owner=OWNER)
        bridge, _state = self.make_bridge([dlg])
        store = []
        body = {"title_pattern": "Select Folder", "owner_pid": PID,
                "folder_path": "C:\\x", "expected_hwnd": 999999}
        result = await self._submit(
            bridge, "fill_folder_dialog", self.recording_handler(store),
            body)
        self.assertEqual(result, {"ok": True, "hwnd_used": 400})
        self.assertEqual(store[0]["body"]["expected_hwnd"], 400)

    async def test_fill_census_polls_until_dialog_appears(self):
        dlg = _win(400, "Select Folder", owner=OWNER)

        def spec(call_no):
            return [dlg] if call_no >= 3 else []

        bridge, state = self.make_bridge(spec)
        store = []
        body = {"title_pattern": "Select Folder", "owner_pid": PID,
                "folder_path": "C:\\x", "wait_for_dialog_ms": 3000}
        result = await self._submit(
            bridge, "fill_folder_dialog", self.recording_handler(store),
            body)
        self.assertEqual(result, {"ok": True, "hwnd_used": 400})
        self.assertGreaterEqual(state["calls"], 3)

    async def test_fill_census_expiry_refuses_after_wait_bound(self):
        bridge, _state = self.make_bridge([])
        store = []
        body = {"title_pattern": "Select Folder", "owner_pid": PID,
                "folder_path": "C:\\x", "wait_for_dialog_ms": 300}
        started = time.monotonic()
        result = await self._submit(
            bridge, "fill_folder_dialog", self.recording_handler(store),
            body)
        elapsed = time.monotonic() - started
        self.assertEqual(result["reason"], "no_matching_dialog")
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertLess(elapsed, 10.0)
        self.assertEqual(store, [])

    async def test_cancel_census_is_single_pass(self):
        bridge, state = self.make_bridge([])
        store = []
        body = {"title_pattern": "Select", "owner_pid": PID}
        result = await self._submit(
            bridge, "cancel_native_dialog", self.recording_handler(store),
            body)
        self.assertEqual(result["reason"], "no_matching_dialog")
        self.assertEqual(state["calls"], 1)
        self.assertEqual(store, [])

    async def test_class_g_runs_on_dedicated_executor_with_census(self):
        dlg = _win(400, "Select Folder", owner=OWNER)
        bridge, state = self.make_bridge([dlg])
        store = []
        body = {"title_pattern": "Select Folder", "owner_pid": PID,
                "folder_path": "C:\\x"}
        result = await self._submit(
            bridge, "fill_folder_dialog", self.recording_handler(store),
            body)
        self.assertEqual(result, {"ok": True, "hwnd_used": 400})
        call = store[0]
        # Dedicated class-G executor thread (A2-F-6 shape enforcement):
        # not the test thread, not the T4 loop thread.
        self.assertTrue(call["thread_name"].startswith("t4-bridge-g"),
                        call["thread_name"])
        self.assertNotEqual(call["thread"], threading.get_ident())
        self.assertNotEqual(call["thread"], bridge._loop_thread.ident)
        # Census ran inside the same thread-shaped unit.
        self.assertEqual(state["threads"], [call["thread"]])
        # Bridged fill forces auto_try_common_titles off.
        self.assertFalse(call["body"]["auto_try_common_titles"])


class TestClassGShape(unittest.TestCase):
    def test_composite_is_not_class_g(self):
        self.assertNotIn("click_and_handle_folder_dialog", CLASS_G_TOOLS)
        self.assertNotIn("click_and_handle_folder_dialog", BRIDGED_TOOLS)
        self.assertEqual(CLASS_G_TOOLS, frozenset(
            {"fill_folder_dialog", "cancel_native_dialog"}))

    def test_runtime_handlers_stay_zero_await(self):
        # A2-F-6: the dedicated-executor shape is safe only while both
        # runtime bodies stay zero-await; pin it mechanically.
        for module, name in (
                (desktop_dialogs, "fill_folder_dialog"),
                (desktop_dialog_controls, "cancel_native_dialog")):
            tree = ast.parse(inspect.getsource(module))
            fn = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.AsyncFunctionDef)
                      and n.name == name)
            awaits = [n for n in ast.walk(fn)
                      if isinstance(n, ast.Await)]
            self.assertEqual(awaits, [], name)

    def test_expected_hwnd_declared_on_handlers_and_wrappers(self):
        for fn in (desktop_dialogs.fill_folder_dialog,
                   desktop_dialog_controls.cancel_native_dialog,
                   code_tools.fill_folder_dialog,
                   code_tools.cancel_native_dialog):
            self.assertIn("expected_hwnd",
                          inspect.signature(fn).parameters, fn.__name__)


# ---------------------------------------------------------------------------
# Handler bound re-check (real runtime handlers, fake win32 world)
# ---------------------------------------------------------------------------

class _FakeWorld:
    """A tiny synthetic window manager behind fake win32 modules."""

    def __init__(self):
        self.windows = {}
        self.sent = []
        self.posted = []
        self.enum_calls = 0
        self.enum_script = None

    def add(self, hwnd, title, cls, pid=PID, parent=0, owner=0,
            children=()):
        self.windows[hwnd] = {
            "title": title, "cls": cls, "pid": pid, "parent": parent,
            "owner": owner, "visible": True, "children": list(children),
            "child": parent != 0}
        return hwnd

    def _ancestor_dialog(self, hwnd):
        seen = hwnd
        while self.windows.get(seen, {}).get("parent"):
            seen = self.windows[seen]["parent"]
        return seen

    def modules(self):
        world = self
        gui = types.ModuleType("win32gui")
        con = types.ModuleType("win32con")
        api = types.ModuleType("win32api")
        proc = types.ModuleType("win32process")
        con.WM_SETTEXT = 0x000C
        con.WM_KEYDOWN = 0x0100
        con.WM_KEYUP = 0x0101
        con.WM_CLOSE = 0x0010
        con.BM_CLICK = 0x00F5

        def _text(h):
            return world.windows.get(h, {}).get("title", "")

        def _cls(h):
            return world.windows.get(h, {}).get("cls", "")

        def _enum(cb, _):
            world.enum_calls += 1
            if world.enum_script is not None:
                world.enum_script(world.enum_calls)
            for hwnd, w in list(world.windows.items()):
                if w["child"]:
                    continue
                if cb(hwnd, None) is False:
                    break
            return None

        def _enum_children(hwnd, cb, _):
            for child in list(
                    world.windows.get(hwnd, {}).get("children", [])):
                cb(child, None)
            return None

        def _post(hwnd, msg, _w, _l):
            world.posted.append((hwnd, msg))
            if msg == con.BM_CLICK:
                dlg = world._ancestor_dialog(hwnd)
                if dlg in world.windows:
                    world.windows[dlg]["visible"] = False
            return 0

        gui.GetWindowText = _text
        gui.GetClassName = _cls
        gui.IsWindowVisible = lambda h: bool(
            world.windows.get(h, {}).get("visible"))
        gui.IsWindow = lambda h: h in world.windows
        gui.GetParent = lambda h: world.windows.get(h, {}).get(
            "parent", 0)
        gui.GetWindow = lambda h, _c: world.windows.get(h, {}).get(
            "owner", 0)
        gui.EnumWindows = _enum
        gui.EnumChildWindows = _enum_children
        def send_message(hwnd, msg, _w, value):
            world.sent.append((hwnd, msg, value))
            if msg == con.WM_SETTEXT:
                world.windows[hwnd]["title"] = value
            return 1

        gui.SendMessage = send_message
        gui.SetForegroundWindow = lambda h: True
        api.PostMessage = _post
        proc.GetWindowThreadProcessId = lambda h: (
            0, world.windows.get(h, {}).get("pid", 0))
        desktop_input = types.ModuleType("orchestrator.runtime.desktop_input")
        desktop_input.edit_text = lambda hwnd, capacity: _text(hwnd)[:capacity - 1]
        desktop_input.send_chord = mock.Mock()
        return {"win32gui": gui, "win32con": con, "win32api": api,
                "win32process": proc, "orchestrator.runtime.desktop_input": desktop_input}


class TestHandlerBoundRecheck(unittest.IsolatedAsyncioTestCase):
    def _dialog_world(self, button_label="Select Folder"):
        world = _FakeWorld()
        world.add(101, "", "Edit", parent=100)
        world.add(102, button_label, "Button", parent=100)
        world.add(100, "Select Folder", "#32770", owner=OWNER,
                  children=[101, 102])
        return world

    async def test_fill_bound_recheck_passes_and_acts(self):
        world = self._dialog_world()
        with mock.patch.dict(sys.modules, world.modules()):
            raw = await desktop_dialogs.fill_folder_dialog(
                title_pattern="Select Folder", folder_path="C:\\Windows",
                owner_pid=PID, auto_try_common_titles=False,
                expected_hwnd=100)
        result = json.loads(raw)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["hwnd_used"], 100)
        self.assertTrue(any(m == 0x000C for _h, m, _l in world.sent))

    async def test_fill_bound_recheck_refuses_injected_dialog(self):
        world = self._dialog_world()

        def script(call_no):
            if call_no == 2:  # the re-check enumeration
                world.add(103, "", "Edit", parent=200)
                world.add(104, "Select Folder", "Button", parent=200)
                world.add(200, "Select Folder", "#32770", owner=OWNER,
                          children=[103, 104])

        world.enum_script = script
        with mock.patch.dict(sys.modules, world.modules()):
            raw = await desktop_dialogs.fill_folder_dialog(
                title_pattern="Select Folder", folder_path="C:\\Windows",
                owner_pid=PID, auto_try_common_titles=False,
                expected_hwnd=100)
        result = json.loads(raw)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_name"], "DIALOG_TARGET_AMBIGUOUS")
        self.assertEqual(result["reason"], "dialog_target_changed")
        self.assertEqual(world.sent, [])    # nothing typed
        self.assertEqual(world.posted, [])  # nothing clicked

    async def test_cancel_bound_recheck_passes_and_acts(self):
        world = self._dialog_world(button_label="Cancel")
        with mock.patch.dict(sys.modules, world.modules()):
            raw = await desktop_dialog_controls.cancel_native_dialog(
                title_pattern="Select", owner_pid=PID, expected_hwnd=100)
        result = json.loads(raw)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dialog_closed"])
        self.assertTrue(any(m == 0x00F5 for _h, m in world.posted))

    async def test_cancel_bound_recheck_refuses_vanished_dialog(self):
        world = self._dialog_world(button_label="Cancel")

        def script(call_no):
            if call_no == 2:  # the re-check enumeration
                world.windows.pop(100, None)

        world.enum_script = script
        with mock.patch.dict(sys.modules, world.modules()):
            raw = await desktop_dialog_controls.cancel_native_dialog(
                title_pattern="Select", owner_pid=PID, expected_hwnd=100)
        result = json.loads(raw)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_name"], "DIALOG_TARGET_AMBIGUOUS")
        self.assertEqual(result["reason"], "dialog_target_changed")
        self.assertEqual(world.posted, [])  # nothing clicked or closed

    async def test_fill_bound_refuses_unowned_dialog_enumerated_first(self):
        # F-1 (G3b): a conjunction-FAILING bare match (unowned #32770)
        # enumerated BEFORE the bound dialog is picked by the bare
        # predicate; the re-check set alone still equals [expected_hwnd],
        # so only the acted-hwnd comparison can refuse. Bound => act
        # only on the bound hwnd.
        world = _FakeWorld()
        world.add(501, "", "Edit", parent=500)
        world.add(502, "Select Folder", "Button", parent=500)
        world.add(500, "Select Folder", "#32770", owner=0,
                  children=[501, 502])
        world.add(401, "", "Edit", parent=400)
        world.add(402, "Select Folder", "Button", parent=400)
        world.add(400, "Select Folder", "#32770", owner=OWNER,
                  children=[401, 402])
        with mock.patch.dict(sys.modules, world.modules()):
            raw = await desktop_dialogs.fill_folder_dialog(
                title_pattern="Select Folder", folder_path="C:\\Windows",
                owner_pid=PID, auto_try_common_titles=False,
                expected_hwnd=400)
        result = json.loads(raw)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_name"], "DIALOG_TARGET_AMBIGUOUS")
        self.assertEqual(result["reason"], "dialog_target_changed")
        # No WM_SETTEXT reached the wrong window's edit control (501),
        # and nothing was typed or posted anywhere.
        self.assertFalse(any(h == 501 for h, _m, _l in world.sent))
        self.assertEqual(world.sent, [])
        self.assertEqual(world.posted, [])

    async def test_cancel_bound_refuses_non_dialog_enumerated_first(self):
        # F-1 (G3b): a titled NON-dialog window (fails the conjunction's
        # dialog-class test) enumerated BEFORE the bound dialog is the
        # bare predicate's first match; the re-check set alone still
        # equals [expected_hwnd], so only the acted-hwnd comparison can
        # refuse.
        world = _FakeWorld()
        world.add(502, "Cancel", "Button", parent=500)
        world.add(500, "Select Folder", "CabinetWClass", owner=OWNER,
                  children=[502])
        world.add(402, "Cancel", "Button", parent=400)
        world.add(400, "Select Folder", "#32770", owner=OWNER,
                  children=[402])
        with mock.patch.dict(sys.modules, world.modules()):
            raw = await desktop_dialog_controls.cancel_native_dialog(
                title_pattern="Select", owner_pid=PID, expected_hwnd=400)
        result = json.loads(raw)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_name"], "DIALOG_TARGET_AMBIGUOUS")
        self.assertEqual(result["reason"], "dialog_target_changed")
        # No BM_CLICK / WM_CLOSE reached the wrong window (500/502).
        self.assertFalse(any(h in (500, 502) for h, _m in world.posted))
        self.assertEqual(world.posted, [])

    async def test_fill_unbound_proceeds_on_owned_dialog_class_main(self):
        # Handler-level companion to the section 10 residual: without a
        # bridge binding (MCP path), the shipped predicate acts on an
        # owned dialog-class main window.
        world = _FakeWorld()
        world.add(301, "", "Edit", parent=300)
        world.add(302, "OK", "Button", parent=300)
        world.add(300, "AppMain", "#32770", owner=OWNER,
                  children=[301, 302])
        with mock.patch.dict(sys.modules, world.modules()):
            raw = await desktop_dialogs.fill_folder_dialog(
                title_pattern="AppMain", folder_path="C:\\Windows",
                owner_pid=PID, auto_try_common_titles=False)
        result = json.loads(raw)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["hwnd_used"], 300)

    async def test_cancel_unbound_proceeds_on_owned_dialog_class_main(self):
        world = _FakeWorld()
        world.add(302, "Cancel", "Button", parent=300)
        world.add(300, "AppMain", "#32770", owner=OWNER, children=[302])
        with mock.patch.dict(sys.modules, world.modules()):
            raw = await desktop_dialog_controls.cancel_native_dialog(
                title_pattern="AppMain", owner_pid=PID)
        result = json.loads(raw)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["hwnd"], 300)


# ---------------------------------------------------------------------------
# CDP fetch offload (D-S2): no urlopen on the event-loop thread
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestCdpOffload(unittest.IsolatedAsyncioTestCase):
    def _patch_urlopen(self, payload=None, exc=None):
        record = {"threads": [], "urls": [], "timeouts": []}

        def fake_urlopen(url, timeout=None):
            record["threads"].append(threading.get_ident())
            record["urls"].append(url)
            record["timeouts"].append(timeout)
            if exc is not None:
                raise exc
            return _FakeResp(payload)

        return record, mock.patch.object(
            urllib.request, "urlopen", fake_urlopen)

    def _assert_off_loop(self, record):
        self.assertTrue(record["threads"])
        for ident in record["threads"]:
            self.assertNotEqual(ident, threading.get_ident())
        self.assertTrue(all(t == 3 for t in record["timeouts"]),
                        record["timeouts"])

    async def test_list_cdp_targets_fetch_off_loop(self):
        payload = [{"id": "t1", "title": "App", "url": "http://x/",
                    "type": "page", "webSocketDebuggerUrl": "ws://h/"}]
        record, patcher = self._patch_urlopen(payload)
        with patcher:
            raw = await tools_v15.list_cdp_targets(9223)
        result = json.loads(raw)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["target_count"], 1)
        self._assert_off_loop(record)

    async def test_cdp_pick_target_fetch_off_loop(self):
        payload = [{"id": "t1", "title": "My App", "url": "http://x/",
                    "type": "page"}]
        record, patcher = self._patch_urlopen(payload)
        with patcher:
            match, err = await tools_v15._cdp_pick_target(
                9223, prefer_title_substr="My App")
        self.assertIsNone(err)
        self.assertEqual(match["matched_title"], "My App")
        self._assert_off_loop(record)

    async def test_cdp_pick_target_poll_fetches_all_off_loop(self):
        payload = [{"id": "t1", "title": "Other", "url": "http://x/",
                    "type": "page"}]
        record, patcher = self._patch_urlopen(payload)
        with patcher:
            match, err = await tools_v15._cdp_pick_target(
                9223, prefer_title_substr="My App",
                wait_for_navigation=True, wait_timeout_ms=400)
        self.assertIsNone(match)
        self.assertIn("not found", err)
        self.assertGreaterEqual(len(record["threads"]), 2)
        self._assert_off_loop(record)

    async def test_cdp_dispatch_fetch_off_loop(self):
        payload = [{"id": "other", "title": "X", "type": "page"}]
        record, patcher = self._patch_urlopen(payload)
        with patcher:
            result, err = await tools_v15._cdp_dispatch(
                9223, "missing-id", "Page.bringToFront", {})
        self.assertIsNone(result)
        self.assertIn("not found", err)
        self._assert_off_loop(record)

    async def test_cdp_attach_list_fetch_off_loop(self):
        if not tools_v15._playwright_available():
            self.skipTest("playwright not available in this environment")
        record, patcher = self._patch_urlopen(
            exc=OSError("connection refused"))
        with patcher:
            raw = await tools_v15.cdp_attach_target(
                target_id="t1", expected_title_substr="App")
        result = json.loads(raw)
        self.assertEqual(result["error_name"], "CDP_UNREACHABLE")
        self._assert_off_loop(record)

    def test_bridged_fetchers_route_via_sync_helper(self):
        for fn in (tools_v15._cdp_pick_target, tools_v15._cdp_dispatch,
                   tools_v15.list_cdp_targets,
                   tools_v15.cdp_attach_target):
            src = inspect.getsource(fn)
            self.assertIn("_fetch_json_sync", src, fn.__name__)
            self.assertIn("asyncio.to_thread", src, fn.__name__)
            self.assertNotIn("urlopen", src, fn.__name__)
        self.assertFalse(
            inspect.iscoroutinefunction(tools_v15._fetch_json_sync))
        self.assertIn("urlopen",
                      inspect.getsource(tools_v15._fetch_json_sync))


# ---------------------------------------------------------------------------
# Section 3.4 fence fixtures not covered by the Sprint 1 suite
# ---------------------------------------------------------------------------

class TestFenceAdditions(unittest.IsolatedAsyncioTestCase):
    def make_bridge(self, **kw):
        kw.setdefault("grace_s", 0.2)
        kw.setdefault("watchdog_tick_s", 60.0)
        kw.setdefault("reaper_period_s", 3600.0)
        bridge = T4Bridge(**kw)
        bridge.start(watchdog=False)
        self.addCleanup(bridge.stop)
        return bridge

    def effectuate(self, bridge, decision):
        future = asyncio.run_coroutine_threadsafe(
            bridge.effectuate_quarantine(decision), bridge._loop)
        return future.result(timeout=10)

    async def test_cross_recycle_decision_stale_with_coincidental_seq(self):
        bridge = self.make_bridge()
        bridge.register_entry("pg-1", owner_agent="agent-a")
        entry = bridge.get_entry("pg-1")
        entry.call_seq = 1
        entry.in_flight.add(1)
        token1 = bridge._next_token()
        with bridge._state_lock:
            bridge._holder = {"token": token1, "agent": "agent-a",
                              "tool": "click", "acquired_at": 0.0,
                              "completing": True}
        decision = {"token": token1, "page_id": "pg-1",
                    "quarantined_seq": 1, "entry_call_seq": 1,
                    "loop_epoch": bridge.loop_epoch, "case": "entry"}
        old_epoch = bridge.loop_epoch
        # Real recycle: kill the loop thread, recreate (epoch bumps).
        bridge._loop.call_soon_threadsafe(bridge._loop.stop)
        bridge._loop_thread.join(timeout=5)
        bridge.ensure_loop()
        self.assertEqual(bridge.loop_epoch, old_epoch + 1)
        # A new entry whose call_seq coincidentally equals the recorded
        # value, and a new holder: tokens are bridge-global, never
        # rebased across recycles, so token2 cannot equal token1.
        bridge.register_entry("pg-2", owner_agent="agent-a")
        new_entry = bridge.get_entry("pg-2")
        new_entry.call_seq = 1
        new_entry.in_flight.add(1)
        token2 = bridge._next_token()
        self.assertNotEqual(token1, token2)
        with bridge._state_lock:
            bridge._holder = {"token": token2, "agent": "agent-a",
                              "tool": "click", "acquired_at": 0.0,
                              "completing": False}
        outcome = await asyncio.to_thread(
            self.effectuate, bridge, decision)
        self.assertEqual(outcome, "quarantine_stale")
        self.assertIsNotNone(bridge.get_entry("pg-1"))
        self.assertIsNotNone(bridge.get_entry("pg-2"))
        self.assertEqual(bridge._holder["token"], token2)
        self.assertEqual(
            bridge.status_snapshot()["quarantine_records"][-1]["state"],
            "quarantine_stale")

    async def test_late_completion_makes_decision_a_noop(self):
        bridge = self.make_bridge()
        bridge.register_entry("pg-1", owner_agent="agent-a")
        entry = bridge.get_entry("pg-1")
        entry.call_seq = 1
        entry.in_flight.add(1)
        token1 = bridge._next_token()
        with bridge._state_lock:
            bridge._holder = {"token": token1, "agent": "agent-a",
                              "tool": "click", "acquired_at": 0.0,
                              "completing": True}
        decision = {"token": token1, "page_id": "pg-1",
                    "quarantined_seq": 1, "entry_call_seq": 1,
                    "loop_epoch": bridge.loop_epoch, "case": "entry"}
        # The wedged call completes normally first: its own release wins.
        released = await asyncio.to_thread(
            lambda: asyncio.run_coroutine_threadsafe(
                bridge._release_lock(token1), bridge._loop
            ).result(timeout=10))
        self.assertTrue(released)
        entry.in_flight.discard(1)
        # The next driver acquires under a fresh token.
        token2 = bridge._next_token()
        with bridge._state_lock:
            bridge._holder = {"token": token2, "agent": "agent-b",
                              "tool": "click", "acquired_at": 0.0,
                              "completing": False}
        outcome = await asyncio.to_thread(
            self.effectuate, bridge, decision)
        self.assertEqual(outcome, "quarantine_stale")
        self.assertIsNotNone(bridge.get_entry("pg-1"))
        self.assertEqual(bridge._holder["token"], token2)


if __name__ == "__main__":
    unittest.main()
