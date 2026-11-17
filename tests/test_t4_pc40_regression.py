"""PC-40 regression evidence: target child coordinates and flattened browser CDP."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiohttp import web

import tools_v15
from orchestrator.runtime import cdp_browser


def test_active_port_requires_exact_requested_port_and_browser_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = tmp_path / "Google/Chrome/User Data/DevToolsActivePort"
    path.parent.mkdir(parents=True)
    path.write_text("9127\n/devtools/browser/test-guid\n", encoding="utf-8")
    assert cdp_browser.browser_endpoint(9127) == "ws://127.0.0.1:9127/devtools/browser/test-guid"
    assert cdp_browser.browser_endpoint(9223) is None
    path.write_text("9127\n/devtools/page/test-guid\n", encoding="utf-8")
    assert cdp_browser.browser_endpoint(9127) is None


@pytest.mark.parametrize("reject", [False, True])
def test_flattened_socket_routes_reply_and_releases_connection(reject):
    async def scenario():
        seen = []
        closed = asyncio.Event()
        closed_connections = []
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            try:
                async for message in ws:
                    packet = json.loads(message.data)
                    seen.append(packet)
                    await ws.send_json({"method": "Target.targetCreated", "params": {}})
                    if packet["method"] == "Target.getTargets":
                        result = {"targetInfos": [{"targetId": "owned", "type": "page", "title": "Probe", "url": "https://example.test"}]}
                    elif packet["method"] == "Target.attachToTarget":
                        assert packet["params"] == {"targetId": "owned", "flatten": True}
                        if reject:
                            await ws.send_json({"id": packet["id"], "error": {"code": -1, "message": "gone"}})
                            continue
                        result = {"sessionId": "pinned-session"}
                    else:
                        assert packet["sessionId"] == "pinned-session"
                        result = {"result": {"type": "number", "value": 42}}
                    await ws.send_json({"id": packet["id"] + 100, "result": {}})
                    await ws.send_json({"id": packet["id"], "result": result})
            finally:
                closed_connections.append(ws)
                if len(closed_connections) == 2:
                    closed.set()
            return ws
        app = web.Application()
        app.router.add_get("/devtools/browser/probe", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        endpoint = f"ws://127.0.0.1:{port}/devtools/browser/probe"
        try:
            targets = await cdp_browser.browser_targets(endpoint)
            assert targets[0]["id"] == "owned"
            result = await cdp_browser.browser_dispatch(endpoint, "owned", "Runtime.evaluate", {"expression": "6*7"})
            if reject:
                assert result["error"]["message"] == "gone"
                assert len(seen) == 2
            else:
                assert result["result"]["result"]["value"] == 42
                assert len(seen) == 3
            await asyncio.wait_for(closed.wait(), 2)
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


def test_browser_socket_used_by_normal_desktop_cdp_entrypoints(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(cdp_browser, "browser_endpoint", lambda port: "ws://local/browser")
    listing = AsyncMock(return_value=[{"id": "wanted", "type": "page", "title": "PC40", "url": "https://w.test"}])
    dispatcher = AsyncMock(return_value={"result": {}})
    monkeypatch.setattr(cdp_browser, "browser_targets", listing)
    monkeypatch.setattr(cdp_browser, "browser_dispatch", dispatcher)
    forbidden = Mock(side_effect=AssertionError("Browser-only endpoint must bypass /json"))
    monkeypatch.setattr(tools_v15, "_fetch_json_sync", forbidden)
    result = json.loads(asyncio.run(tools_v15.desktop_click(
        cdp_port=9127, title_pattern="PC40", x=20, y=40,
        fallback_to_win32=False, report_dom_evidence=False, dispatch_dom_click=False)))
    assert result["ok"] and result["clicked"]
    assert dispatcher.await_count == 4
    assert all(call.args[1] == "wanted" for call in dispatcher.call_args_list)
    forbidden.assert_not_called()


def test_parent_client_point_converts_to_receiving_child_before_send(monkeypatch):
    from orchestrator.runtime import desktop_verification as dv
    monkeypatch.setattr(dv, "click_checks", lambda *args: {"bounds": {"status": "passed"}})
    messages = []
    transform = []
    def to_screen(hwnd, point):
        transform.append(("to_screen", hwnd, point))
        return point[0] + 100, point[1] + 100
    def to_child(hwnd, point):
        transform.append(("to_child", hwnd, point))
        return point[0] - 100, point[1] - 243
    gui = SimpleNamespace(ChildWindowFromPoint=lambda hwnd, point: 22,
                          ClientToScreen=to_screen, ScreenToClient=to_child,
                          SendMessageTimeout=lambda hwnd, msg, wp, lp, *rest: messages.append((hwnd,msg,lp)) or 1)
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(
        GetCurrentThreadId=lambda: 1, GetLastError=lambda: 0))
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (1, 1)))
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(
        WM_MOUSEACTIVATE=0x21, WM_LBUTTONDOWN=0x201, WM_LBUTTONUP=0x202,
        HTCLIENT=1, MK_LBUTTON=1))
    monkeypatch.setattr(tools_v15, "_pywin32_available", lambda: True)
    result = json.loads(asyncio.run(tools_v15.desktop_click(
        hwnd=11, x=952, y=587, force_win32=True)))
    assert result["clicked"]
    assert transform == [("to_screen",11,(952,587)), ("to_child",22,(1052,687))]
    for hwnd, msg, lp in messages[1:]:
        assert hwnd == 22 and lp & 0xffff == 952 and (lp >> 16) & 0xffff == 444
