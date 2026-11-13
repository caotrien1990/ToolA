"""Offline regressions for the November T4 claim pack."""

import asyncio
import inspect
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import code_tools
import orchestrator_server as server
import tools_v15
from orchestrator import t4_bridge
from orchestrator.runtime import desktop_input


def call(awaitable):
    return json.loads(asyncio.run(awaitable))


@pytest.mark.parametrize("kwargs,expected_error", [
    ({}, "MISSING_REQUIRED_FIELD"),
    ({"page_id": "pg-one", "processes": ["chrome.exe"]}, "CLEANUP_SCOPE_UNSUPPORTED"),
    ({"page_id": "pg-one", "close_session_after": True}, "CLEANUP_SCOPE_UNSUPPORTED"),
])
def test_cleanup_refuses_unscoped_targets(monkeypatch, kwargs, expected_error):
    close = AsyncMock()
    monkeypatch.setattr(tools_v15, "close_browser_page", close)
    result = call(tools_v15.cleanup_test_session(**kwargs))
    assert not result["ok"] and result["error_name"] == expected_error
    close.assert_not_awaited()


def test_idle_guard_allows_release_of_already_unavailable_page(monkeypatch):
    guard = AsyncMock(return_value=json.dumps({"outcome": "idle"}))
    pw = AsyncMock()
    monkeypatch.setattr(code_tools, "assert_target_idle", guard)
    monkeypatch.setattr(tools_v15, "_BROWSER_PAGES", {"cdp-one": {
        "kind": "attached", "pw": pw, "unavailable": True}})
    result = call(tools_v15.cleanup_test_session(page_id="cdp-one", idle_guard_signals=[{}]))
    guard.assert_awaited_once()
    pw.stop.assert_awaited_once()
    assert result["ok"] and result["detached"] and result["already_unavailable"]
    assert not result["page_closed"]


def test_idle_guard_error_blocks_close(monkeypatch):
    monkeypatch.setattr(code_tools, "assert_target_idle", AsyncMock(side_effect=ValueError("probe")))
    close = AsyncMock()
    monkeypatch.setattr(tools_v15, "close_browser_page", close)
    result = call(tools_v15.cleanup_test_session(page_id="pg-one", idle_guard_signals=[{}]))
    assert result["error_name"] == "CLEANUP_GUARD_FAILED"
    close.assert_not_awaited()


def test_partial_close_resumes_at_the_failed_resource(monkeypatch):
    context, browser, pw = AsyncMock(), AsyncMock(), AsyncMock()
    browser.close.side_effect = [RuntimeError("busy"), None]
    state = {"kind": "launched", "context": context, "browser": browser, "pw": pw}
    pages = {"pg-one": state}
    monkeypatch.setattr(tools_v15, "_BROWSER_PAGES", pages)
    result = call(tools_v15.cleanup_test_session(page_id="pg-one"))
    assert result["error_name"] == "PAGE_CLOSE_FAILED"
    assert pages["pg-one"] is state
    assert call(tools_v15.cleanup_test_session(page_id="pg-one"))["page_closed"]
    context.close.assert_awaited_once()
    pw.stop.assert_awaited_once()
    assert not pages


def test_attached_cleanup_only_detaches(monkeypatch):
    browser, pw = AsyncMock(), AsyncMock()
    monkeypatch.setattr(tools_v15, "_BROWSER_PAGES", {"cdp-one": {
        "kind": "attached", "browser": browser, "pw": pw}})
    result = call(tools_v15.cleanup_test_session(page_id="cdp-one"))
    assert result["detached"] and not result["page_closed"]
    browser.close.assert_not_awaited()
    pw.stop.assert_awaited_once()


def test_lost_browser_is_hidden_until_reaped(monkeypatch):
    bridge = t4_bridge.T4Bridge()
    page, browser, pw = Mock(), Mock(), AsyncMock()
    page.is_closed.return_value = False
    browser.is_connected.return_value = True
    state = {"kind": "attached", "page": page, "browser": browser, "pw": pw}
    monkeypatch.setattr(tools_v15, "_BROWSER_PAGES", {"cdp-one": state})
    try:
        bridge.register_entry("cdp-one", owner_agent="muse1", kind="attached")
        tools_v15._watch_page_lifecycle(state)
        assert len(bridge.status_snapshot("muse1")["pages"]) == 1
        browser.on.call_args.args[1](browser)
        assert bridge.status_snapshot("muse1")["pages"] == []
        assert asyncio.run(bridge.reap_once()) == ["cdp-one"]
        pw.stop.assert_awaited_once()
    finally:
        bridge.stop()


# The HTTP schema validates these before dispatch even though the Python
# defaults support direct/internal calls. Keep the exceptions exact.
HTTP_REQUIRED = {
    "cdp_attach_target": {"target_id"},  # HTTP binds a listed target + title/URL.
    "cleanup_test_session": {"page_id"},  # Cleanup needs an owned page.
    "desktop_click": {"x", "y"},  # A selector still dispatches coordinate events.
    "fill_folder_dialog": {"folder_path"},  # A caller must name the requested path.
    "open_browser_page": {"project_path"},
    "desktop_send_text": {"project_path"},
    "save_log_snapshot": {"project_path"},
}


@pytest.mark.parametrize("name", sorted(t4_bridge.BRIDGED_TOOLS | t4_bridge.DISCOVERY_TOOLS))
def test_registry_exposes_handler_parameters(name):
    from orchestrator.runtime import desktop_dialogs

    schema = server._enriched_tool_schema(name)["function"]["parameters"]
    handler = code_tools.TOOL_MAP[name]
    if name == "query_dom_candidates":
        # search_code(mode="dom_candidates") forwards the public alias to this handler.
        handler = tools_v15.query_dom_candidates
    params = inspect.signature(handler).parameters
    fields = {key for key, spec in schema["properties"].items()
              if key != "definition_version" and not spec.get("x_r101_projection")}
    assert fields == set(params)
    required = {key for key, p in params.items() if p.default is inspect.Parameter.empty}
    assert set(schema.get("required", [])) == required | HTTP_REQUIRED.get(name, set())
    if name == "fill_folder_dialog":
        # TOOL_MAP's code_tools wrapper is the public signature. It delegates
        # these same arguments to the desktop_dialogs implementation.
        runtime = inspect.signature(desktop_dialogs.fill_folder_dialog).parameters
        assert {k: p.default for k, p in params.items()} == {k: p.default for k, p in runtime.items()}


def test_http_catalog_and_guidance_recommend_routable_tools():
    import re
    from aiohttp.test_utils import make_mocked_request

    response = asyncio.run(server.handle_tools(make_mocked_request("GET", "/api/tools")))
    rows = [row["function"] for row in json.loads(response.text)]
    assert {row["name"] for row in rows} >= t4_bridge.DISCOVERY_TOOLS
    excluded = t4_bridge.NON_ROUTED_CONSUMERS
    for rule in tools_v15._DECISION_RULES:
        assert not (set(tools_v15._rule_names(rule)) & excluded), rule
    for name, error in server._R40_ERROR_TO_ALTERNATIVE:
        alternative = server._ca_guidance(name, error_name=error)["suggested_alternative_tool"]
        assert alternative.get("tool") not in excluded, (name, error, alternative)
    for row in rows:
        name = row["name"]
        guidance = server._ca_guidance(name)
        for surface in (row, guidance):
            assert not (set(surface.get("see_also", {})) & excluded), name
        # Excluded tools can document their own MCP behavior. HTTP callers
        # must be able to follow recommendations made by the supported tools.
        if name in excluded:
            continue
        prose = [row["description"], *(p.get("description", "") for p in
                 row["parameters"].get("properties", {}).values()),
                 *row.get("see_also", {}).values(), *guidance["known_gotchas"], guidance["recommended_prompt_rule"]]
        for line in prose:
            for target in excluded:
                if re.search(r"\b" + re.escape(target) + r"\b", line):
                    assert "MCP" in line, (name, target, line)


def test_keyup_when_foreground_moves_mid_chord(monkeypatch):
    events = []
    api = SimpleNamespace(MapVirtualKey=lambda key, mode: key + 1,
                          keybd_event=lambda *args: events.append(args))
    monkeypatch.setitem(sys.modules, "win32api", api)
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(
        VK_INSERT=45, KEYEVENTF_EXTENDEDKEY=1, KEYEVENTF_KEYUP=2))
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(
        GetForegroundWindow=Mock(side_effect=[17, 99])))
    monkeypatch.setattr(desktop_input.time, "sleep", lambda delay: events.append(delay))
    with pytest.raises(RuntimeError, match="foreground"):
        desktop_input.send_chord(17, 16, 45)
    assert events == [(16, 17, 0, 0), 0.045, (16, 17, 2, 0), 0.045]


def test_expired_failure_and_capacity_are_scoped(monkeypatch):
    monkeypatch.setattr(server, "_DUPLICATE_FAILURE_STATE", {})
    monkeypatch.setattr(server, "_DUPLICATE_FIRST_SEEN", {})
    monkeypatch.setattr(server, "_DUPLICATE_LIMIT", 2)
    now = Mock(return_value=1000.0)
    monkeypatch.setattr(server.time, "time", now)

    def guard(agent):
        return server._duplicate_retry_guard(agent, "read_page", {}, "BAD")

    assert guard("a") is None and guard("b") is None and guard("c") is None
    assert guard("b")["original_error_name"] == "BAD"
    now.return_value += server._DUPLICATE_TTL_SECONDS
    assert guard("b") is None


@pytest.fixture
def desktop_world(monkeypatch):
    gui = SimpleNamespace(
        GetWindowText=lambda hwnd: "Target title", IsWindow=lambda hwnd: True,
        GetWindowRect=lambda hwnd: (0, 0, 800, 600), IsIconic=lambda hwnd: False,
        GetForegroundWindow=Mock(return_value=17), SetForegroundWindow=Mock(),
        BringWindowToTop=Mock(), PeekMessage=Mock())
    proc = SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (hwnd + 10, 101),
                           AttachThreadInput=Mock())
    api = SimpleNamespace(GetCurrentThreadId=lambda: 1, MapVirtualKey=lambda key, mode: key + 1,
                          keybd_event=Mock())
    con = SimpleNamespace(VK_CONTROL=17, VK_SHIFT=16, VK_INSERT=45, VK_RETURN=13,
                          KEYEVENTF_KEYUP=2, KEYEVENTF_EXTENDEDKEY=1, CF_UNICODETEXT=13)
    clip = SimpleNamespace(OpenClipboard=Mock(), CloseClipboard=Mock(),
                           EmptyClipboard=Mock(), SetClipboardData=Mock())
    for name, value in (("win32gui", gui), ("win32process", proc), ("win32api", api),
                        ("win32con", con), ("win32clipboard", clip)):
        monkeypatch.setitem(sys.modules, name, value)
    monkeypatch.setattr(tools_v15, "_pywin32_available", lambda: True)
    monkeypatch.setattr(desktop_input.time, "sleep", lambda delay: None)
    return gui, proc, api, clip


@pytest.mark.parametrize("foreground", [17, 88])
def test_focus_reports_measured_foreground(desktop_world, foreground, monkeypatch):
    gui, proc, _, _ = desktop_world
    gui.GetForegroundWindow.return_value = foreground
    pick = AsyncMock()
    monkeypatch.setattr(tools_v15, "_cdp_pick_target", pick)
    result = call(tools_v15.desktop_app_focus(hwnd=17))
    assert result["ok"] is (foreground == 17)
    assert result["focused"] is (foreground == 17)
    assert result["matched_title"] == "Target title"
    assert result["window_match_count"] == 1
    assert gui.PeekMessage.called
    pick.assert_not_awaited()
    attached = [x.args[1] for x in proc.AttachThreadInput.call_args_list if x.args[2]]
    assert 27 in attached
    if foreground != 17:
        assert foreground + 10 in attached


def test_text_dispatch_uses_scancodes_and_reports_unverified_delivery(desktop_world):
    _, proc, api, clip = desktop_world
    proc.AttachThreadInput.side_effect = RuntimeError("attach unavailable")
    result = call(tools_v15.desktop_send_text("Tiếng Việt", hwnd=17, clear_first=True))
    assert result["ok"] and not result["delivery_verified"]
    assert result["matched_title"] == "Target title"
    assert result["attach_errors"]
    assert result["steps"] == ["ctrl_a", "shift_insert", "enter"]
    assert all(x.args[1] > 0 for x in api.keybd_event.call_args_list)
    clip.SetClipboardData.assert_called_once_with(13, "Tiếng Việt")


def test_focus_failure_reports_title_selected_by_pattern(desktop_world, monkeypatch):
    gui, _, _, _ = desktop_world
    gui.GetForegroundWindow.return_value = 99
    gui.GetWindowText = Mock(return_value="Editor — Target title")
    monkeypatch.setattr(tools_v15, "_cdp_pick_target", AsyncMock(return_value=(None, "")))
    monkeypatch.setattr(tools_v15, "desktop_window_list", AsyncMock(return_value=json.dumps({
        "windows": [{"hwnd": 17, "title": "Editor — Target title"}]})))
    result = call(tools_v15.desktop_app_focus(title_pattern="target"))
    assert not result["ok"]
    assert result["matched_title"] == "Editor — Target title"
    assert result["window_match_count"] == 1


def test_send_keeps_resolved_title_if_window_disappears(desktop_world, monkeypatch):
    gui, _, _, _ = desktop_world
    gui.GetWindowText = Mock(side_effect=OSError("window closed"))
    monkeypatch.setattr(tools_v15, "desktop_window_list", AsyncMock(return_value=json.dumps({
        "windows": [{"hwnd": 17, "title": "Editor — Target title"}]})))
    result = call(tools_v15.desktop_send_text("abc", title_pattern="target"))
    assert result["error_name"] == "WIN32_API_ERROR"
    assert result["matched_title"] == "Editor — Target title"


def test_text_dispatch_refuses_lost_foreground(desktop_world):
    gui, _, api, clip = desktop_world
    gui.GetForegroundWindow.return_value = 99
    result = call(tools_v15.desktop_send_text("abc", hwnd=17))
    assert result["error_name"] == "FOCUS_NOT_ACHIEVED"
    assert result["matched_title"] == "Target title" and result["hwnd"] == 17
    api.keybd_event.assert_not_called()
    clip.SetClipboardData.assert_not_called()


@pytest.mark.parametrize("readback_matches", [True, False])
def test_dialog_enter_close_and_readback(monkeypatch, readback_matches):
    from test_t4_dialog_cdp import _FakeWorld, PID, OWNER
    from orchestrator.runtime import desktop_dialogs

    world = _FakeWorld()
    world.add(101, "", "Edit", parent=100)
    world.add(100, "Select Folder", "#32770", owner=OWNER, children=[101])
    modules = world.modules()
    posted = modules["win32api"].PostMessage

    def enter_closes(hwnd, msg, key, value):
        posted(hwnd, msg, key, value)
        if msg == 0x0100 and key == 13:
            world.windows[100]["visible"] = False

    modules["win32api"].PostMessage = enter_closes
    if not readback_matches:
        modules["orchestrator.runtime.desktop_input"].edit_text = lambda *_: "wrong"
    for key, value in modules.items():
        monkeypatch.setitem(sys.modules, key, value)
    result = call(desktop_dialogs.fill_folder_dialog(
        title_pattern="Select Folder", folder_path="C:/Windows", owner_pid=PID,
        auto_try_common_titles=False, expected_hwnd=100, timeout_ms=1))
    assert result["ok"] is readback_matches
    if readback_matches:
        assert not result["button_clicked"]
        assert result["verdict"] == "DIALOG_CLOSED_PATH_ENTERED"
        assert not result["selection_verified"]
    else:
        assert result["error_name"] == "DIALOG_EDIT_SETTEXT_FAILED"
        assert not world.posted


def test_unknown_cleanup_owner_and_missing_legacy_entry_are_refused(monkeypatch):
    monkeypatch.setattr(tools_v15, "_BROWSER_PAGES", {"pg-owned": {"page": object()}})
    bridge = t4_bridge.T4Bridge()
    try:
        assert bridge._preflight_entries("cleanup_test_session", ["pg-owned"], "a")
        bridge.register_entry("pg-owned", owner_agent="a")
        assert bridge._preflight_entries("cleanup_test_session", ["pg-owned"], "b")
        assert bridge._preflight_entries("cleanup_test_session", ["pg-owned"], "")
        assert bridge._preflight_entries("cleanup_test_session", ["pg-owned"], "a") is None
    finally:
        bridge.stop()


def test_status_hides_unowned_pages_and_internal_fences(monkeypatch):
    bridge = t4_bridge.T4Bridge()
    monkeypatch.setattr(tools_v15, "_BROWSER_PAGES", {
        "pg-owned": {"page": object()}, "pg-unowned": {"page": object()}})
    try:
        bridge.register_entry("pg-owned", owner_agent="a")
        bridge.register_entry("pg-unowned")
        bridge._holder = {"token": 99, "agent": "a", "tool": "click", "acquired_at": 0}
        bridge.quarantine_records.append({"token": 99, "page_id": "pg-owned", "state": "quarantined"})
        snap = bridge.status_snapshot("b")
        assert not snap["pages"] and snap["other_owners_page_count"] == 2
        assert "token" not in snap["holder"] and "agent" not in snap["holder"]
        assert snap["quarantine_records"] == [{"state": "quarantined"}]
        assert len(bridge.status_snapshot("a")["pages"]) == 1
    finally:
        bridge.stop()


def test_missing_page_retry_supplies_the_field():
    recipe = server._retry_sequence("query_dom_candidates", "MISSING_REQUIRED_FIELD", {})
    assert recipe[0]["missing_fields"] == ["page_id"]
    assert "page_id" in recipe[0]["payload"]


def test_query_candidates_runs_on_the_bridge_through_registry(monkeypatch):
    bridge = t4_bridge.T4Bridge()
    seen = []
    async def evaluate(expression, *args):
        seen.append(__import__("threading").get_ident())
        return [{"tag": "input"}]
    page = SimpleNamespace(evaluate=evaluate)
    monkeypatch.setattr(tools_v15, "_BROWSER_PAGES", {"pg-owned": {"page": page}})
    try:
        bridge.start()
        bridge.register_entry("pg-owned", owner_agent="a")
        body = {"page_id": "pg-owned"}
        server._apply_alias_mode_default_post_s4("query_dom_candidates", body, [])
        result = call(bridge.submit("query_dom_candidates",
                      code_tools.TOOL_MAP["query_dom_candidates"], body, agent_id="a"))
        assert result["ok"], result
        assert seen == [bridge._loop_thread.ident]
    finally:
        bridge.stop()


def test_missing_path_counters_are_caller_scoped_and_expire(monkeypatch):
    from pathlib import Path
    from orchestrator.session.store import _CALLER_AGENT_ID
    from orchestrator.tools.read import fs

    monkeypatch.setattr(fs, "_NEG_PATH_CACHE", {})
    monkeypatch.setattr(fs, "_MISS_STREAK", {})
    clock = Mock(return_value=1000.0)
    monkeypatch.setattr(server.time, "time", clock)
    token = _CALLER_AGENT_ID.set("muse1")
    try:
        def missing():
            return json.loads(fs._missing_file_response(Path("absent.txt"), "absent.txt", None))
        assert missing()["probe_count"] == 1
        assert missing()["probe_count"] == 2
        _CALLER_AGENT_ID.set("muse2")
        assert missing()["probe_count"] == 1
        _CALLER_AGENT_ID.set("muse1")
        clock.return_value += fs._NEG_TTL_S + 1
        result = missing()
        assert result["probe_count"] == 1 and not result["repeat_probe"]
        assert "circuit_breaker" not in result
    finally:
        _CALLER_AGENT_ID.reset(token)


def test_modern_picker_reads_the_address_before_enter(monkeypatch):
    from test_t4_dialog_cdp import _FakeWorld, PID, OWNER
    from orchestrator.runtime import desktop_dialogs

    world = _FakeWorld()
    world.add(100, "Select Folder", "#32770", owner=OWNER)
    modules = world.modules()
    con = modules["win32con"]
    con.VK_CONTROL, con.VK_RETURN = 17, 13
    chords = []

    def chord(hwnd, *keys):
        chords.append(keys)
        if keys == (17, ord("L")):
            world.add(101, "", "Edit", parent=100)
            world.windows[100]["children"] = [101]
        elif keys == (13,):
            assert world.windows[101]["title"] == "C:\\Windows"
            world.windows[100]["visible"] = False

    modules["orchestrator.runtime.desktop_input"].send_chord = chord
    for name, value in modules.items():
        monkeypatch.setitem(sys.modules, name, value)
    result = call(desktop_dialogs.fill_folder_dialog(
        title_pattern="Select Folder", folder_path="C:/Windows", owner_pid=PID,
        auto_try_common_titles=False, expected_hwnd=100, timeout_ms=1))
    assert result["ok"] and result["folder_path_set"]
    assert result["picker_route"] == "modern_address_edit_enter"
    assert chords == [(17, ord("L")), (13,)]


def test_http_status_body_cannot_select_another_owner(monkeypatch):
    called = []

    async def status(agent_id=""):
        called.append(agent_id)
        return json.dumps({"ok": True, "pages": []})

    monkeypatch.setitem(code_tools.TOOL_MAP, "t4_host_status", status)
    response = asyncio.run(server.dispatch_tool_call(
        "t4_host_status", {"agent_id": "other"}, transport="http"))
    assert json.loads(response.text)["ok"]
    assert called == [""]
