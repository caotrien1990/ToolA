from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest import mock

import pytest
from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request

import code_tools
import orchestrator_server
import path_runtime
from orchestrator.session import store


def _body(payload):
    loop = asyncio.new_event_loop()
    try:
        reader = StreamReader(protocol=mock.Mock(), limit=2 ** 16, loop=loop)
        reader.feed_data(json.dumps(payload).encode("utf-8"))
        reader.feed_eof()
        return reader
    finally:
        loop.close()


@pytest.mark.parametrize("tool_name,payload,headers", [
    ("open_session", {"session_title": "S5", "project_path": "D:/repo\x00/child"}, {}),
    ("read_file_range", {"project_path": "D:/repo", "file_path": "src/ba\x00d.py"}, {}),
    ("write_fix", {"project_path": "D:/repo", "file_path": "src/ba\x00d.py", "start_line": 1, "end_line": 1, "new_content": "pass"}, {"X-Orchestrator-Token": "s5-token"}),
])
def test_dispatch_rejects_embedded_c0_before_tool_call(tool_name, payload, headers, monkeypatch):
    called = False
    async def should_not_run(**_kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True})
    monkeypatch.setitem(code_tools.TOOL_MAP, tool_name, should_not_run)
    monkeypatch.setattr(orchestrator_server, "EXPECTED_TOKEN", "s5-token")
    monkeypatch.setattr(orchestrator_server, "TOKEN_CREATED_AT", time.time())
    request = make_mocked_request("POST", f"/api/call/{tool_name}", headers=headers, payload=_body(payload), match_info={"tool_name": tool_name})
    response = asyncio.run(orchestrator_server._dispatch_tool_call_request(request))
    result = json.loads(response.text)
    assert response.status == 400
    assert result["error_name"] == "INVALID_PATH_CONTROL_CHAR"
    assert result["control_code"] == "U+0000"
    assert result["offending_field"] in {"project_path", "file_path"}
    assert "INTERNAL_ERROR" not in response.text
    assert "traceback" not in response.text.lower()
    assert "\u0000" not in response.text.lower()
    assert called is False


def test_normalize_preserves_legitimate_paths_and_strips_edge_controls():
    legitimate = "D:/0. TRIEN/1. DỮ LIỆU/project-orchestrator"
    body, applied = orchestrator_server._normalize_body("read_file_range", {"project_path": f"\r\n\t{legitimate}\r\n", "file_path": "src/module.py"})
    assert body["project_path"] == legitimate
    assert body["file_path"] == "src/module.py"
    assert "0. TRIEN" in body["project_path"]
    assert "DỮ LIỆU" in body["project_path"]
    assert body["_normalized_paths"] == [{"key": "project_path", "stripped_control_codes": ["U+0009", "U+000A", "U+000D"]}]
    assert "project_path: edge control chars stripped" in applied


def test_defensive_path_guards_reject_c0_before_pathlib():
    with mock.patch.object(Path, "resolve", side_effect=AssertionError("resolve must not run")):
        with pytest.raises(path_runtime.PathControlCharacterError, match=r"U\+0000"):
            store._session_project_key("D:/repo\x00/child")
        with pytest.raises(path_runtime.PathControlCharacterError, match=r"U\+0000"):
            code_tools.find_project_root("D:/repo\x00/file.py")


def test_session_audit_summary_does_not_reflect_raw_c0(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(store, "_SESSIONS_DB", db_path)
    store._init_session_db()
    session, _ = store._open_or_resume_session(str(project), "S5", "T1")
    event_id = store._log_session_event(
        session["session_id"], "read_file_range", {"file_path": "src/ba\x00d.py"},
        {"ok": False}, 1, "error"
    )
    assert event_id is not None
    with sqlite3.connect(db_path) as connection:
        target = connection.execute(
            "SELECT target FROM events WHERE id=?", (event_id,)
        ).fetchone()[0]
    assert target == "<invalid path 0x0000>"
