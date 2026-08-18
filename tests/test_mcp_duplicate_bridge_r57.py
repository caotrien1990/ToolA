"""R57 regression coverage for the MCP duplicate bridge.

WHY: schema-invalid MCP calls must use the local schema gate, while valid
payloads must use the live HTTP proxy. Deterministic errors from either the
2xx envelope or HTTPError branch must share the same duplicate ledger.
"""

import asyncio
import io
import json
import urllib.error
import urllib.request

import orchestrator_server as server


def _payload(response):
    return response.status, json.loads(response.text)


def _reset_duplicate_ledger():
    server._DUPLICATE_FAILURE_STATE.clear()
    server._DUPLICATE_FIRST_SEEN.clear()


def test_schema_invalid_mcp_payload_stays_local_and_deduplicates(monkeypatch):
    _reset_duplicate_ledger()
    proxy_calls = []

    def unexpected_proxy(*args, **kwargs):
        proxy_calls.append((args, kwargs))
        raise AssertionError("schema-invalid MCP payload must not reach HTTP proxy")

    monkeypatch.setattr(urllib.request, "urlopen", unexpected_proxy)
    body = {
        "query": "r57-local-schema",
        "unsupported_from_fixture": "no-side-effect",
        "session_id": "r57-local-schema",
    }
    request_meta = {"headers": {"X-Agent-Id": "codo"}}

    first = asyncio.run(
        server.dispatch_tool_call(
            "search_code", dict(body), transport="mcp", request_meta=request_meta
        )
    )
    second = asyncio.run(
        server.dispatch_tool_call(
            "search_code", dict(body), transport="mcp", request_meta=request_meta
        )
    )

    first_status, first_payload = _payload(first)
    second_status, second_payload = _payload(second)
    assert proxy_calls == []
    assert first_status == 400
    assert first_payload["error_name"] == "UNSUPPORTED_FIELD"
    assert second_status == 409
    assert second_payload["error_name"] == "DUPLICATE_RETRY_BLOCKED"


def test_valid_mcp_payload_uses_proxy_happy_path(monkeypatch):
    _reset_duplicate_ledger()
    proxy_calls = []

    class Response:
        def read(self):
            return b'{"ok": true, "source": "live-http"}'

    def fake_urlopen(request, timeout):
        proxy_calls.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    response = asyncio.run(
        server.dispatch_tool_call(
            "search_code",
            {
                "query": "r57-happy",
                "session_id": "r57-happy",
            },
            transport="mcp",
            request_meta={"headers": {"X-Agent-Id": "codo"}},
        )
    )

    status, payload = _payload(response)
    assert status == 200
    assert payload == {"ok": True, "source": "live-http"}
    assert len(proxy_calls) == 1
    assert proxy_calls[0][0].endswith("/api/call/search_code")


def test_proxy_http_error_branch_uses_same_duplicate_guard(monkeypatch):
    _reset_duplicate_ledger()
    proxy_calls = []

    def fake_urlopen(request, timeout):
        proxy_calls.append((request.full_url, timeout))
        raw = json.dumps(
            {
                "ok": False,
                "error_name": "MISSING_REQUIRED_FIELD",
                "missing_fields": ["query"],
            }
        ).encode("utf-8")
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "schema failure",
            hdrs=None,
            fp=io.BytesIO(raw),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    body = {
        "query": "r57-http-error",
        "session_id": "r57-http-error",
    }
    request_meta = {"headers": {"X-Agent-Id": "codo"}}

    first = asyncio.run(
        server.dispatch_tool_call(
            "search_code", dict(body), transport="mcp", request_meta=request_meta
        )
    )
    second = asyncio.run(
        server.dispatch_tool_call(
            "search_code", dict(body), transport="mcp", request_meta=request_meta
        )
    )

    first_status, first_payload = _payload(first)
    second_status, second_payload = _payload(second)
    assert first_status == 400
    assert first_payload["error_name"] == "MISSING_REQUIRED_FIELD"
    assert second_status == 409
    assert second_payload["error_name"] == "DUPLICATE_RETRY_BLOCKED"
    assert second_payload["original_error_name"] == "MISSING_REQUIRED_FIELD"
    assert len(proxy_calls) == 2
