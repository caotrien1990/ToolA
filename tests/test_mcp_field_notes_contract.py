"""Field notes: payload bytes, live discovery, and uncertain mutation outcomes."""
import base64
import json
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from orchestrator.server import mcp_stdio as ms
import code_tools


class Response:
    def __init__(self, payload):
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.mark.parametrize("encoding", ["plain", "base64"])
def test_unicode_payload_reaches_daemon_unchanged(monkeypatch, encoding):
    text = "VIỆC KHI LÀM TIẾP\nTiếng Việt: phải, cần, đường dẫn."
    content = base64.b64encode(text.encode()).decode() if encoding == "base64" else text
    payload = {"new_content": content, "content_encoding": encoding}

    def urlopen(request, **kwargs):
        assert json.loads(request.data) == payload
        return Response({"ok": True, "echo": text})

    monkeypatch.setattr(ms.urllib.request, "urlopen", urlopen)
    assert json.loads(ms._mcp_proxy_call("write_fix", payload))["echo"] == text


@pytest.mark.parametrize("exc", [TimeoutError("response lost"),
                                 urllib.error.URLError(TimeoutError("timed out"))])
def test_shortcut_timeout_never_invites_blind_write_retry(monkeypatch, exc):
    def urlopen(*args, **kwargs):
        raise exc

    monkeypatch.setattr(ms.urllib.request, "urlopen", urlopen)
    result = json.loads(ms._mcp_proxy_call("write_fix", {
        "project_path": "D:/project #1", "file_path": "notes & more.md"}))
    assert result["commit_state"] == "unknown"
    assert result["safe_to_retry"] is False
    assert "poll_write_status" in result["ca_next_action"]
    query = parse_qs(urlsplit(result["write_status_url"]).query)
    assert query["file_path"] == ["notes & more.md"]
    assert "write_performed" not in result


def test_discovery_reads_daemon_on_every_call(monkeypatch):
    versions = iter(["old", "new"])

    def urlopen(url, **kwargs):
        assert url.endswith("/api/tools/write_fix/schema")
        return Response({"ok": True, "version": next(versions)})

    monkeypatch.setattr(ms.urllib.request, "urlopen", urlopen)
    assert json.loads(ms._mcp_live_discovery("/api/tools/write_fix/schema"))["version"] == "old"
    assert json.loads(ms._mcp_live_discovery("/api/tools/write_fix/schema"))["version"] == "new"


def test_discovery_unavailable_does_not_serve_stale_local_schema(monkeypatch):
    def urlopen(*args, **kwargs):
        raise ConnectionRefusedError("down")

    monkeypatch.setattr(ms.urllib.request, "urlopen", urlopen)
    result = json.loads(ms._mcp_live_discovery("/api/tools/names", {"name_contains": "cache"}))
    assert result["ok"] is False
    assert result["local_schema_fallback"] is False


@pytest.mark.parametrize("content,stage", [("%%%", "base64_decode"), ("ww==", "utf8_decode")])
def test_invalid_encoding_reports_the_failing_stage(content, stage):
    text, _check, error = code_tools._decode_text_payload(content, "base64")
    assert text is None
    assert error["error_name"] == "INVALID_CONTENT_ENCODING"
    assert error["encoding_error_stage"] == stage
    assert error["write_performed"] is False
    assert "programmatically" in error["hint"]


def test_full_catalog_hint_tracks_live_count():
    import orchestrator_server as srv
    compact = srv._tool_names_payload()
    total = compact["total_available"]
    assert f"limit={total}" in compact["next_query_hint"]
    full = srv._tool_names_payload(limit=total)
    assert full["returned_count"] == total
    assert full["bounded"] is False
