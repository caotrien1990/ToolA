"""Behavioral checks for the combined T4 contract; live acceptance remains separate."""
import asyncio
import json
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

import tools_v15
from orchestrator import t4_bridge
from orchestrator.runtime import console_guard as cg
from orchestrator.runtime import desktop_text as dt
from orchestrator.runtime import desktop_verification as dv


class Clipboard:
    def __init__(self, text="prior", formats=(13,)):
        self.text, self.formats, self.seq = text, list(formats), 1
        self.empties = 0

    def OpenClipboard(self):
        pass

    def CloseClipboard(self):
        pass

    def EnumClipboardFormats(self, current):
        if not current:
            return self.formats[0] if self.formats else 0
        return next(iter(self.formats[self.formats.index(current) + 1:]), 0)

    def GetClipboardSequenceNumber(self):
        return self.seq

    def GetClipboardData(self, fmt):
        return self.text

    def EmptyClipboard(self):
        self.empties += 1
        self.text, self.formats, self.seq = None, [], self.seq + 1

    def SetClipboardData(self, fmt, value):
        self.text, self.formats, self.seq = value, [fmt], self.seq + 1


def test_clipboard_restores_text_and_preserves_identical_user_copy():
    clip = Clipboard()
    lease = dt.ClipboardLease(clip, "paste")
    lease.install()
    lease.restore()
    assert clip.text == "prior" and lease.report["status"] == "restored"
    lease = dt.ClipboardLease(clip, "paste")
    lease.install()
    clip.SetClipboardData(13, "paste")  # identical text, different user ownership
    lease.restore()
    assert clip.text == "paste" and lease.report["status"] == "user_changed_not_restored"


@pytest.mark.parametrize("formats", [(2,), (13, 49321)])
def test_unknown_formats_abort_before_destroy(formats):
    clip = Clipboard(formats=formats)
    with pytest.raises(dt.ClipboardFailure, match="Unsupported"):
        dt.ClipboardLease(clip, "paste").install()
    assert clip.empties == 0 and clip.text == "prior"


def test_snapshot_failure_has_no_retry_or_paste():
    clip = Clipboard()
    clip.GetClipboardData = Mock(side_effect=RuntimeError("locked"))
    with pytest.raises(dt.ClipboardFailure):
        dt.ClipboardLease(clip, "paste").install()
    assert clip.empties == 0 and clip.GetClipboardData.call_count == 1


@pytest.fixture
def edit_world(monkeypatch):
    gui = SimpleNamespace(GetForegroundWindow=Mock(return_value=17),
                          GetClassName=Mock(return_value="Edit"),
                          GetWindowLong=Mock(return_value=0))
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setattr(dt, "focused_control", lambda hwnd: 18)
    return gui


@pytest.mark.parametrize("value,clear,status", [
    ("Tiếng Việt\r\nĐúng", True, "verified"),
    ("prefix Tiếng Việt\nĐúng suffix", False, "verified"),
    ("different", True, "mismatch"),
])
def test_readback_outcomes_and_newline(edit_world, monkeypatch, value, clear, status):
    monkeypatch.setattr(dt, "edit_length", lambda hwnd: dt._units(value))
    monkeypatch.setattr(dt, "edit_text", lambda hwnd, capacity: value)
    result = dt.verify_text(17, "Tiếng Việt\nĐúng", clear, 18)
    assert result["status"] == status
    assert value not in result.values()  # content is not echoed


def test_long_append_utf16_capacity_and_growth(edit_world, monkeypatch):
    value = "old😀" * 20000 + " mới"
    capacities = []
    monkeypatch.setattr(dt, "edit_length", lambda hwnd: 1)  # stale length races a growing field
    def read(hwnd, capacity):
        capacities.append(capacity)
        return value.encode("utf-16-le")[:2 * (capacity - 1)].decode("utf-16-le", errors="ignore")
    monkeypatch.setattr(dt, "edit_text", read)
    result = dt.verify_text(17, " mới", False, 18)
    assert result["status"] == "not_verified"  # bounded growth never invents mismatch/pass
    assert len(capacities) == 3
    monkeypatch.setattr(dt, "edit_length", lambda hwnd: dt._units(value))
    result = dt.verify_text(17, " mới", False, 18)
    assert result["status"] == "verified" and capacities[-1] == dt._units(value) + 2


def test_password_never_reads_value_or_length(edit_world, monkeypatch):
    edit_world.GetWindowLong.return_value = 0x20
    length, read = Mock(), Mock()
    monkeypatch.setattr(dt, "edit_length", length)
    monkeypatch.setattr(dt, "edit_text", read)
    assert dt.verify_text(17, "secret", False, 18)["reason"] == "password_control"
    length.assert_not_called()
    read.assert_not_called()


@pytest.mark.parametrize("case,reason", [
    ("dom", "no_native_edit"), ("focus", "foreground_changed"),
    ("child", "focused_control_changed"), ("unreadable", "native_read_unavailable"),
])
def test_inconclusive_readback_names_reason(edit_world, monkeypatch, case, reason):
    if case == "dom":
        edit_world.GetClassName.return_value = "Chrome_RenderWidgetHostHWND"
    if case == "focus":
        edit_world.GetForegroundWindow.return_value = 99
    if case == "child":
        monkeypatch.setattr(dt, "focused_control", lambda hwnd: 19)
    monkeypatch.setattr(dt, "edit_length", Mock(side_effect=RuntimeError()))
    assert dt.verify_text(17, "x", False, 18)["reason"] == reason


@pytest.mark.parametrize("status,enter", [("verified", True), ("mismatch", False), ("not_verified", True)])
def test_restore_and_readback_precede_enter(monkeypatch, status, enter):
    clip = Clipboard()
    monkeypatch.setitem(sys.modules, "win32clipboard", clip)
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(
        VK_CONTROL=17, VK_SHIFT=16, VK_INSERT=45, VK_RETURN=13))
    monkeypatch.setattr(dt, "focused_control", lambda hwnd: 18)
    pauses = []
    monkeypatch.setattr(dt, "time", SimpleNamespace(sleep=pauses.append))
    events = []
    def chord(hwnd, *keys):
        events.append(keys)
        if keys == (13,):
            assert clip.text == "prior" and "readback" in events
    monkeypatch.setattr(dt, "send_chord", chord)
    def verify(*args):
        events.append("readback")
        assert clip.text == "paste"
        return {"status": status, "reason": "no_native_edit" if status == "not_verified" else "text_mismatch"}
    monkeypatch.setattr(dt, "verify_text", verify)
    result = dt.paste_with_readback(17, "paste", clear_first=False, paste_shortcut="auto", submit=True)
    assert ((13,) in events) is enter
    assert result["ok"] is enter
    assert result["delivery_verified"] is False and result["clipboard"]["status"] == "restored"
    assert pauses[:2] == [0.05, 0.05]


@pytest.fixture
def click_world(monkeypatch):
    calls = []
    gui = SimpleNamespace(
        ClientToScreen=lambda hwnd, pt: calls.append("convert") or (pt[0] - 1000, pt[1]),
        WindowFromPoint=lambda pt: calls.append("point") or 20,
        GetAncestor=lambda hwnd, flag: 10,
        IsWindowEnabled=Mock(return_value=True),
        GetWindow=lambda hwnd, flag: 20, GetWindowText=lambda hwnd: "Save dialog")
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(
        GetSystemMetrics=lambda i: {76: -1920, 77: 0, 78: 3840, 79: 1080}[i]))
    return gui, calls


def test_virtual_bounds_negative_coordinates_and_modal_order(click_world):
    gui, calls = click_world
    result = dv.click_checks(10, 10, 10)
    assert result["bounds"]["status"] == "passed" and calls == ["convert", "point"]
    gui.IsWindowEnabled.return_value = False
    assert dv.click_checks(10, 10, 10)["modal"]["dialog_title"] == "Save dialog"
    gui.IsWindowEnabled.return_value = True  # target is itself the enabled dialog
    assert dv.click_checks(20, 10, 10)["modal"]["status"] == "passed"
    assert dv.click_checks(10, 9000, 10)["bounds"]["status"] == "out_of_bounds"


def test_check_failures_report_skip(click_world, monkeypatch):
    gui, _ = click_world
    gui.ClientToScreen = Mock(side_effect=RuntimeError())
    assert dv.click_checks(10, 1, 1)["bounds"]["reason"] == "client_to_screen_unavailable"
    gui.ClientToScreen = lambda hwnd, pt: pt
    gui.WindowFromPoint = Mock(side_effect=RuntimeError())
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(GetSystemMetrics=lambda i: 0))
    checks = dv.click_checks(10, 1, 1)
    assert checks["bounds"]["status"] == checks["modal"]["status"] == "skipped"


def test_disabled_without_popup_does_not_claim_modal(click_world):
    gui, _ = click_world
    gui.IsWindowEnabled = lambda hwnd: False
    gui.GetWindow = lambda hwnd, flag: hwnd
    result = dv.click_checks(10, 1, 1)["modal"]
    assert result["status"] == "warning" and result["modal_disabled"] is None
    assert result["reason"] == "target_disabled_without_popup"

def test_outside_click_never_dispatches(click_world, monkeypatch):
    monkeypatch.setattr(tools_v15, "_pywin32_available", lambda: True)
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace())
    result = json.loads(asyncio.run(tools_v15.desktop_click(hwnd=10, x=9000, y=1, force_win32=True)))
    assert result["error_name"] == "CLICK_OUT_OF_BOUNDS"
    assert result["checks"]["bounds"]["status"] == "out_of_bounds"


def test_cdp_no_invented_geometry_and_same_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(tools_v15, "_cdp_pick_target", AsyncMock(return_value=({"id": "p"}, "")))
    async def dispatch(port, target, method, args):
        calls.append(method)
        return {"result": {}}, ""
    monkeypatch.setattr(tools_v15, "_cdp_dispatch", dispatch)
    result = json.loads(asyncio.run(tools_v15.desktop_click(
        x=10, y=20, report_dom_evidence=False, dispatch_dom_click=False)))
    assert result["clicked"] and calls == ["Page.bringToFront"] + ["Input.dispatchMouseEvent"] * 3
    assert result["checks"]["bounds"]["status"] == result["checks"]["modal"]["status"] == "skipped"


def test_window_bounce_then_recovery_is_retained(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(dv, "time", SimpleNamespace(
        time=lambda: 100 + clock[0], monotonic=lambda: clock[0],
        sleep=lambda delay: clock.__setitem__(0, clock[0] + delay)))
    show = Mock()
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(ShowWindow=show))
    def sample(hwnd):
        # Two transitions inside one second, not merely comparing the endpoints.
        return {"timestamp": 100 + clock[0], "monotonic": clock[0], "exists": True,
                "minimized": not 0.3 <= clock[0] < 0.6, "visible": True,
                "maximized": False, "rect": {"x": 0, "y": 0, "w": 50, "h": 50}}
    monkeypatch.setattr(dv, "window_sample", sample)
    result = dv.observe_window_change(17, "minimize", 6)["observation"]
    assert result["bounced"] and not result["held_through_observation"]
    assert result["delayed_matches_request"] and len(result["transitions"]) == 2
    assert all(t["actor"] == "unknown" for t in result["transitions"])
    show.assert_called_once_with(17, 6)


@pytest.mark.parametrize("state,flags", [
    ("minimize", (True, True, False)), ("maximize", (False, True, True)),
    ("restore", (False, True, False)), ("normal", (False, True, False)),
    ("hide", (False, False, False)), ("show", (True, True, False)),
])
def test_all_state_predicates(state, flags):
    sample = dict(exists=True, minimized=flags[0], visible=flags[1], maximized=flags[2])
    assert dv.matches_request(sample, state, {"minimized": False})
    assert not dv.matches_request({"exists": False}, state, {})


@pytest.mark.parametrize("exists,reason", [(False, "target_disappeared"), (None, "window_sample_unavailable")])
def test_target_disappearance_keeps_observation(monkeypatch, exists, reason):
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(ShowWindow=Mock()))
    before = dict(exists=True, minimized=False, visible=True, maximized=False, rect={},
                  timestamp=0, monotonic=0)
    gone = dict(exists=exists, reason=reason, timestamp=1, monotonic=1)
    monkeypatch.setattr(dv, "time", SimpleNamespace(monotonic=lambda: 0, sleep=lambda delay: None))
    matching = dict(before, minimized=True)
    monkeypatch.setattr(dv, "window_sample", Mock(side_effect=[before, matching, gone]))
    result = dv.observe_window_change(17, "minimize", 6)
    assert not result["ok"] and result["error"] == reason
    assert result["observation"]["delayed"]["exists"] is exists
    assert not result["observation"]["bounced"]

def test_console_detector_ignores_other_session(monkeypatch):
    monkeypatch.setattr(cg, "active_console_session", lambda: 1)
    monkeypatch.setattr(cg, "process_session", lambda pid: {1: 1, 2: 2}[pid])
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(process_iter=lambda attrs: [
        SimpleNamespace(info={"pid": 1, "name": "explorer.exe"}),
        SimpleNamespace(info={"pid": 2, "name": "logonui.exe"})]))
    assert cg.console_state()["console_locked"] is False
    monkeypatch.setattr(cg, "process_session", lambda pid: 1)
    assert cg.console_state()["console_locked"] is True


@pytest.mark.parametrize("tool", sorted(cg.BLOCKED_TOOLS))
def test_locked_bridge_refuses_before_handler(tool, monkeypatch):
    monkeypatch.setattr(cg, "console_state", lambda: {"console_locked": True})
    bridge = t4_bridge.T4Bridge()
    bridge.start(watchdog=False)
    handler = AsyncMock(return_value='{"ok":true}')
    try:
        result = json.loads(asyncio.run(bridge.submit(tool, handler, {}, agent_id="codo")))
        assert result["error_name"] == "T4_SCREEN_LOCKED"
        assert tool in result["blocked_subset"] and "read_page" in result["unaffected"]
        handler.assert_not_called()
    finally:
        bridge.stop()


def test_locked_page_tool_still_runs(monkeypatch):
    monkeypatch.setattr(cg, "console_state", lambda: {"console_locked": True})
    bridge = t4_bridge.T4Bridge()
    bridge.start(watchdog=False)
    try:
        result = json.loads(asyncio.run(bridge.submit("read_page", AsyncMock(return_value='{"ok":true}'), {})))
        assert result["ok"]
    finally:
        bridge.stop()


@pytest.mark.parametrize("locked_after", [True, False])
def test_capture_rechecks_before_writing_even_uniform_frame(tmp_path, monkeypatch, locked_after):
    monkeypatch.setattr(tools_v15, "_pywin32_available", lambda: False)
    monkeypatch.setattr(cg, "console_state", Mock(side_effect=[
        {"console_locked": False}, {"console_locked": locked_after}]))
    from PIL import ImageGrab
    monkeypatch.setattr(ImageGrab, "grab", lambda **kw: Image.new("RGB", (32, 32), "black"))
    output = tmp_path / "capture.png"
    token = cg.CAPTURE_GUARD.set(True)
    try:
        result = json.loads(tools_v15._desktop_screenshot_capture(0, str(output), 0))
    finally:
        cg.CAPTURE_GUARD.reset(token)
    assert output.exists() is (not locked_after)
    assert result["uniform_frame"]
    if locked_after:
        assert result["error_name"] == "T4_SCREEN_LOCKED" and result["reason"] == "blank_frame"
    else:
        assert result["ok"]


@pytest.mark.parametrize("grace,expect_busy", [(5.0, False), (0.05, True)])
def test_observation_lock_contention_is_bounded(monkeypatch, grace, expect_busy):
    monkeypatch.setattr(cg, "console_state", lambda: {"console_locked": False})
    # Use the actual observation loop and real one-second wait, but fake only OS reads.
    def sample(hwnd):
        return dict(timestamp=time.time(), monotonic=time.monotonic(), exists=True,
                    minimized=True, visible=True, maximized=False, rect={})
    monkeypatch.setattr(dv, "window_sample", sample)
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(ShowWindow=Mock()))
    started = threading.Event()
    bridge = t4_bridge.T4Bridge(grace_s=grace)
    bridge.start(watchdog=False)
    async def state(**kwargs):
        started.set()
        return json.dumps(await asyncio.to_thread(dv.observe_window_change, 17, "minimize", 6))
    async def scenario():
        first = asyncio.create_task(bridge.submit("desktop_window_state", state, {}))
        assert await asyncio.to_thread(started.wait, 3)
        start = time.monotonic()
        second = json.loads(await bridge.submit("click", AsyncMock(return_value='{"ok":true}'), {}))
        elapsed = time.monotonic() - start
        initial = json.loads(await first)
        assert initial["observation"]["held_through_observation"]
        assert initial["observation"]["elapsed_seconds"] >= 1.0
        if expect_busy:
            assert second["error_name"] == "T4_BUSY" and elapsed < 0.8
        else:
            assert second["ok"] and 0.8 < elapsed < 5.0
    try:
        asyncio.run(scenario())
    finally:
        bridge.stop()


def test_ownerless_redaction_read_and_lifecycle_contract(monkeypatch):
    bridge = t4_bridge.T4Bridge()
    monkeypatch.setattr(tools_v15, "_BROWSER_PAGES", {
        "pg-unowned": {"page": object()}, "pg-a": {"page": object()}, "pg-b": {"page": object()}})
    try:
        bridge.register_entry("pg-unowned")
        bridge.register_entry("pg-a", owner_agent="a")
        bridge.register_entry("pg-b", owner_agent="b")
        for caller in ("a", "b"):
            snap = bridge.status_snapshot(caller)
            assert [page["page_id"] for page in snap["pages"]] == ["pg-" + caller]
            assert snap["other_owners_page_count"] == 2
            assert bridge._preflight_entries("read_page", ["pg-unowned"], caller) is None
            for name in ("close_browser_page", "cleanup_test_session", "open_browser_page"):
                unowned = bridge._preflight_entries(name, ["pg-unowned"], caller)
                absent = bridge._preflight_entries(name, ["pg-missing"], caller)
                assert unowned["error_name"] == absent["error_name"] == "PAGE_NOT_FOUND"
    finally:
        bridge.stop()


def test_console_unknown_is_not_unlocked(monkeypatch):
    monkeypatch.setattr(cg, "active_console_session", Mock(side_effect=OSError()))
    state = cg.console_state()
    assert state["console_locked"] is None
    assert cg.refusal(state)["error_name"] == "T4_SCREEN_STATE_UNKNOWN"


def test_lock_checked_after_driver_wait(monkeypatch):
    locked = [False]
    monkeypatch.setattr(cg, "console_state", lambda: {"console_locked": locked[0]})
    bridge = t4_bridge.T4Bridge(grace_s=2)
    bridge.start(watchdog=False)
    started, release = threading.Event(), threading.Event()
    async def first(**kwargs):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        return '{"ok":true}'
    second = AsyncMock(return_value='{"ok":true}')
    async def scenario():
        holder = asyncio.create_task(bridge.submit("click", first, {}))
        assert await asyncio.to_thread(started.wait, 2)
        queued = asyncio.create_task(bridge.submit("desktop_send_text", second, {}))
        await asyncio.sleep(0.03)
        locked[0] = True
        release.set()
        await holder
        return json.loads(await queued)
    try:
        assert asyncio.run(scenario())["error_name"] == "T4_SCREEN_LOCKED"
        second.assert_not_called()
    finally:
        release.set()
        bridge.stop()


def test_clipboard_install_failure_reports_prior_restoration():
    clip = Clipboard()
    original = clip.SetClipboardData
    def setter(fmt, value):
        if value == "paste":
            raise RuntimeError("allocation failure")
        original(fmt, value)
    clip.SetClipboardData = setter
    lease = dt.ClipboardLease(clip, "paste")
    with pytest.raises(dt.ClipboardFailure):
        lease.install()
    assert clip.text == "prior"
    assert lease.report["status"] == "install_failed_prior_restored"


@pytest.mark.parametrize("modal", [False, True])
def test_win32_checks_do_not_mask_dispatch_outcomes(click_world, monkeypatch, modal):
    gui, _ = click_world
    effects = []
    gui.IsWindowEnabled.return_value = not modal
    gui.ChildWindowFromPoint = lambda hwnd, point: hwnd
    gui.SendMessageTimeout = lambda hwnd, message, *args: effects.append(message) or 1
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (1, 1)))
    api = sys.modules["win32api"]
    api.GetCurrentThreadId = lambda: 1
    api.GetLastError = lambda: 0
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(
        WM_MOUSEACTIVATE=0x21, WM_LBUTTONDOWN=0x201, WM_LBUTTONUP=0x202,
        HTCLIENT=1, MK_LBUTTON=1))
    monkeypatch.setattr(tools_v15, "_pywin32_available", lambda: True)
    result = json.loads(asyncio.run(tools_v15.desktop_click(hwnd=10, x=10, y=10, force_win32=True)))
    assert result["ok"] and result["clicked"]
    assert effects == [0x21, 0x201, 0x202]
    assert result["checks"]["modal"]["status"] == ("warning" if modal else "passed")
