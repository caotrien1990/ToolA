"""R57 regression coverage for the MCP duplicate bridge.

WHY: a deterministic tool-body error must reach the caller once as itself and a
second time as DUPLICATE_RETRY_BLOCKED, whether it arrived in a 2xx envelope or
through the HTTPError branch -- both share one duplicate ledger.

R57 originally also asserted that a schema-invalid payload never reaches the HTTP
proxy, because the bridge screened it locally first. B010 removed that screen:
"invalid" was decided against the bridge process's own registry, which is loaded
once at launch and goes stale the moment the daemon gains a parameter, so fields
the daemon accepts were refused (and whole new tools came back UNKNOWN_TOOL).
The verdict now comes from the daemon. What R57 guards is unchanged in substance
-- the same two statuses in the same order -- it just no longer pins which layer
produced them. See tests/test_mcp_gate_uses_live_schema_b010.py.
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


def test_schema_invalid_mcp_payload_deduplicates(monkeypatch):
    """An unsupported field: refused once, then blocked as a duplicate.

    The daemon is the one that refuses now (B010), so the fake stands in for it
    and answers the way the real validator does -- HTTP 400 carrying
    UNSUPPORTED_FIELD and the current accepted_fields.
    """
    _reset_duplicate_ledger()
    proxy_calls = []

    def fake_urlopen(request, timeout):
        proxy_calls.append((request.full_url, timeout))
        raw = json.dumps(
            {
                "ok": False,
                "error_name": "UNSUPPORTED_FIELD",
                "unsupported_fields": ["unsupported_from_fixture"],
                "accepted_fields": ["project_path", "query"],
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
    assert first_status == 400
    assert first_payload["error_name"] == "UNSUPPORTED_FIELD"
    # The daemon's accepted_fields must survive the bridge, because that is the
    # half of the answer a CA can act on.
    assert "query" in first_payload.get("accepted_fields", [])
    assert second_status == 409
    assert second_payload["error_name"] == "DUPLICATE_RETRY_BLOCKED"
    assert second_payload["original_error_name"] == "UNSUPPORTED_FIELD"
    assert len(proxy_calls) == 2


def test_valid_mcp_payload_uses_proxy_happy_path(monkeypatch):
    _reset_duplicate_ledger()
    proxy_calls = []

    class Response:
        """Stands in for http.client.HTTPResponse, context manager included.

        The proxy opens the POST as ``with urlopen(...) as resp:`` so the socket
        is closed on every path. A fake with only ``read()`` makes the proxy
        raise TypeError and answer 502 -- which looks exactly like a routing
        regression and is only mock infidelity.
        """

        def read(self):
            return b'{"ok": true, "source": "live-http"}'

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

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
