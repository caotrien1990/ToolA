"""Evidence-oriented regression for the HOLD/room tool friction report."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import code_tools as ct
import orchestrator_server as srv
import tools_upgrade_p5 as p5
import tools_v15 as v15
from orchestrator.tools.write import core


def run(coro):
    return json.loads(asyncio.run(coro))


def test_hash_is_raw_binary_and_comparison_is_explicit(tmp_path):
    file = tmp_path / "pin.bin"
    content = b"\xef\xbb\xbfline\r\n\x00\xff"
    file.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    result = run(p5.file_sha256(str(file), expected_sha256=expected))
    assert result["sha256"] == expected and result["matches_expected"]
    assert result["hash_domain"] == "raw_file_bytes"
    assert result["read_token_seeded"] is False
    assert run(p5.file_sha256(str(file), expected_sha256="0" * 64))["matches_expected"] is False
    assert run(p5.file_sha256("pin.bin"))["error_name"] == "PROJECT_PATH_REQUIRED"


@pytest.mark.parametrize("name", ["file_sha256", "write_status"])
def test_read_tools_are_discoverable_and_nonmutating(name):
    payload, status = srv._tool_schema_payload(name)
    assert status == 200
    fn = payload["tool"]["function"]
    assert not fn["mutating"] and not fn["requires_token"] and not fn["requires_session"]
    assert ct.TOOL_PHASES[name] == "T1"


@pytest.mark.parametrize("query, expected", [("sha256 compare", "file_sha256"),
                                            ("poll write_status after timeout", "write_status")])
def test_look_hint_never_routes_to_write(query, expected):
    result = run(v15.tool_decision_hint(query, verbosity="terse"))
    assert result["recommended_tool"] == expected
    assert result["recommended_tier"] == "T1"


def test_dispatcher_schema_error_explains_transport():
    payload, status = srv._tool_schema_payload("call_tool")
    assert status == 404 and payload["error_name"] == "UNKNOWN_TOOL"
    assert payload["surface"] == "mcp_dispatcher"
    assert payload["payload_example"]["name"] == "read_file_range"


def test_write_status_uses_same_handler_and_preserves_unknown():
    handler = AsyncMock(return_value=SimpleNamespace(text=json.dumps({
        "ok": False, "error_name": "WRITE_STATUS_UNKNOWN", "commit_state": "unknown"})))
    with patch.object(srv, "handle_write_status", handler):
        result = run(srv.write_status(operation_id="missing"))
    assert result["commit_state"] == "unknown"
    assert handler.call_args.args[0].query["operation_id"] == "missing"


def test_http_post_can_send_empty_json_without_rewriting_endpoint():
    class Response:
        status = 200
        headers = {"Content-Type": "application/json"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, count):
            return b'{}'
    with patch("urllib.request.urlopen", return_value=Response()) as send:
        result = run(p5.http_check("http://localhost/ops_progress", method="POST", json_body={}))
    request = send.call_args.args[0]
    assert request.data == b"{}" and request.method == "POST"
    assert request.full_url.endswith("/ops_progress")
    assert result["request_body_sent"] and result["request_body_bytes"] == 2


def test_bad_powershell_launch_is_rejected_before_spawn():
    with patch.object(ct, "run_project_command", AsyncMock()) as spawn:
        result = run(p5.start_app(command='powershell.exe -Command -File "D:/runner.ps1"'))
    assert result["error_name"] == "INVALID_POWERSHELL_FILE_LAUNCH"
    spawn.assert_not_called()


def test_banner_range_cannot_consume_sections(tmp_path):
    file = tmp_path / "room.md"
    text = "# Room\n\nBanner\n\n## Section zero\nbody\n## Section one\n"
    file.write_text(text, encoding="utf-8")
    result = run(core.write_fix(str(file), 3, 6, "New banner", str(tmp_path), dry_run=True))
    assert result["error_name"] == "BANNER_RANGE_CROSSES_SECTION"
    assert file.read_text(encoding="utf-8") == text
    preview = run(core.write_fix(str(file), 3, 3, "New banner", str(tmp_path), dry_run=True))
    assert preview["ok"]


def test_self_pin_warning_uses_original_byte_hash():
    original = "# Room\r\nold revision\r\n"
    sha = hashlib.sha256(original.encode()).hexdigest()
    warnings = core._markdown_pin_warnings(Path("room.md"), original, "Pinned: " + sha[:8])
    assert warnings and "SELF_HASH_PIN_STALE_AFTER_WRITE" in warnings[0]
    assert core._markdown_pin_warnings(Path("room.md"), original, "Other pin: deadbeef") == []


def test_pinned_ruff_apply_refuses_before_process_or_backup(tmp_path):
    from orchestrator.graph import tools
    file = tmp_path / "hold.py"
    file.write_text("import unused\n", encoding="utf-8")
    with patch.object(tools.subprocess, "run") as spawn:
        result = run(tools.ruff_fix(str(tmp_path), "hold.py", apply=True, preserve_hash=True))
    assert result["error_name"] == "HASH_PINNED_TARGET" and not result["applied"]
    spawn.assert_not_called()
    assert file.read_text() == "import unused\n"
