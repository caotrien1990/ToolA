"""Residual reproductions use local ephemeral sockets, never the live daemon."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientSession, web

import code_tools as ct
import orchestrator_server as srv
import tools_upgrade_p5 as p5
from orchestrator.tools.write import core


def test_http_check_can_call_health_on_its_own_event_loop():
    async def scenario():
        app = web.Application()
        async def health(request):
            return web.json_response({"ok": True, "pid": 123})
        async def probe(request):
            return web.Response(text=await p5.http_check(
                f"http://127.0.0.1:{port}/health", timeout=1, text_contains='"pid": 123'),
                content_type="application/json")
        app.router.add_get("/health", health)
        app.router.add_get("/probe", probe)
        runner = web.AppRunner(app)
        await runner.setup()
        server = await asyncio.get_running_loop().create_server(runner.server, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as client:
                async with client.get(f"http://127.0.0.1:{port}/probe") as response:
                    result = await response.json()
            assert result["ok"], result
            assert result["status"] == 200 and result["text_contains_match"]
        finally:
            server.close()
            await server.wait_closed()
            await runner.cleanup()
    asyncio.run(scenario())


@pytest.mark.parametrize("command", [
    'powershell.exe -Command -File "D:/runner.ps1"',
    'cmd /c powershell.exe -NoProfile -Command "-File D:/runner.ps1"',
    'pwsh -c "-File D:/runner.ps1"',
    'powershell -Command\n-File D:/runner.ps1',
])
def test_wrapped_file_switch_refuses_before_spawn(command):
    with patch.object(ct, "run_project_command", AsyncMock()) as spawn:
        result = json.loads(asyncio.run(p5.start_app(command=command)))
    assert result["error_name"] == "INVALID_POWERSHELL_FILE_LAUNCH", result
    spawn.assert_not_called()


@pytest.mark.parametrize("command", [
    'powershell.exe -NoProfile -File "D:/runner.ps1"',
    'powershell -Command "& powershell.exe -File D:/runner.ps1"',
])
def test_valid_script_launch_does_not_claim_restart(command):
    mock = AsyncMock(return_value=json.dumps({"ok": True, "exit_code": 0, "stdout": "already healthy"}))
    with patch.object(ct, "run_project_command", mock):
        result = json.loads(asyncio.run(p5.start_app(command=command)))
    assert result["ok"] and result["restart_verified"] is False
    assert "already-healthy" in result["lifecycle_note"]
    mock.assert_awaited_once()


def test_health_fields_disclose_daemon_uptime_and_unknown_mcp_identity():
    async def scenario():
        with patch.object(srv, "SERVER_READY", True):
            response = await srv.handle_health(SimpleNamespace(
                headers={}, query={"fields": "generation,mcp_generation,uptime_s"}))
        result = json.loads(response.text)
        assert "omitted_unknown_fields" not in result, result
        assert result["uptime_s"] >= 0
        assert result["mcp_generation"] is None
        assert result["field_notes"]["mcp_generation"]
        assert result["generation"] == srv.SERVER_GENERATION
    asyncio.run(scenario())


@pytest.mark.parametrize("banner", ["New banner\n", "New banner\n\n", "# New banner\n"])
def test_cp039_line_seven_banner_cannot_swallow_sections(tmp_path, banner):
    file = tmp_path / "room.md"
    original = "# Room\n\nmeta\nmeta\nmeta\n\nOld banner\n\n## §0\nbody\n## §0b\nbody\n## §1\nbody\n"
    file.write_text(original, encoding="utf-8")
    result = json.loads(asyncio.run(core.write_fix(str(file), 7, 14, banner, str(tmp_path), dry_run=True)))
    assert result["error_name"] == "BANNER_RANGE_CROSSES_SECTION", result
    assert file.read_text(encoding="utf-8") == original


def test_existing_room_pin_warns_on_unrelated_banner_edit(tmp_path):
    file = tmp_path / "room.md"
    original = "# Room\nOld banner\n\n## §9\nroom.md sha256: 588e5b51\n"
    file.write_text(original, encoding="utf-8")
    result = json.loads(asyncio.run(core.write_fix(str(file), 2, 2, "New banner", str(tmp_path), dry_run=True)))
    assert result["ok"]
    assert any("SELF_HASH_PIN" in warning for warning in result.get("warnings", [])), result
    assert file.read_text(encoding="utf-8") == original


def test_external_artifact_hash_is_not_a_self_pin(tmp_path):
    text = "# Room\nAPI api.py sha256: 588e5b51\n"
    assert core._markdown_pin_warnings(tmp_path / "room.md", text, text) == []


def test_http_error_and_timeout_preserve_contract():
    import io
    import urllib.error
    error = urllib.error.HTTPError("http://example.invalid", 400, "bad", {}, io.BytesIO(b"bad json"))
    with patch("urllib.request.urlopen", side_effect=error):
        result = json.loads(asyncio.run(p5.http_check("http://example.invalid", expect_status=400)))
    assert result["ok"] and result["body_preview"] == "bad json"
    with patch("urllib.request.urlopen", side_effect=TimeoutError("test timeout")):
        result = json.loads(asyncio.run(p5.http_check("http://example.invalid")))
    assert result["error_name"] == "HTTP_CHECK_FAILED"
