"""A missing optional identity header must not bypass the proxy timeout budget."""
import asyncio
import json
import urllib.request

import pytest
import orchestrator_server as server
from orchestrator.server import mcp_stdio


@pytest.mark.parametrize("headers", [{}, {"X-Agent-Id": "codo"}, {"X-Council-Agent": "codo"}])
def test_proxy_budget_is_independent_of_identity_headers(monkeypatch, headers):
    monkeypatch.setenv("ORCHESTRATOR_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("MCP_PROXY_TIMEOUT__WRITE_FIX", "0.35")
    waited = []
    fallback = []

    def bounded_wait(request, budget, tool, body, base):
        waited.append((budget, tool))
        raise TimeoutError("controlled timeout")

    def direct_urlopen(request, *, timeout):
        fallback.append(timeout)
        raise TimeoutError("wrong fallback")

    monkeypatch.setattr(mcp_stdio, "_mcp_proxy_wait", bounded_wait)
    monkeypatch.setattr(urllib.request, "urlopen", direct_urlopen)
    response = asyncio.run(server.dispatch_tool_call(
        "write_fix", {"project_path": str(server.Path(__file__).resolve().parents[1]),
                      "file_path": "unused.py", "start_line": 1, "end_line": 1,
                      "new_content": "pass\n"},
        request_meta={"headers": headers}, transport="mcp"))
    result = json.loads(response.text)
    assert response.status == 502, result
    assert waited == [(0.35, "write_fix")]
    assert fallback == []
    assert result["commit_state"] == "unknown"
    assert result["safe_to_retry"] is False
