import io
import json
import unittest
import urllib.error
import urllib.request
from unittest import mock

import orchestrator_server as srv


def _proxy_loopback(request, timeout=None):
    """Serve the MCP proxy request the way the daemon on the other end does.

    B010 moved the MCP schema verdict to the daemon, so without this the test
    would POST to whatever [A] is listening on 8083 and assert against that
    process's duplicate ledger rather than its own -- passing or failing
    depending on what that server had already seen.

    A canned error envelope is not good enough either. The HTTP validator keeps
    its duplicate entry under a tuple key, while the bridge's _bridge_dedup uses
    a string key, so a canned reply would take a path that cannot see what the
    http leg recorded and the test would assert on the shape of the fake. In
    production both legs land on the same validator. Forwarding the request into
    the real dispatcher reproduces that, and nothing else does.
    """
    body = json.loads(request.data.decode("utf-8"))
    tool = request.full_url.rsplit("/", 1)[-1]
    response = srv._run_mcp_coro(srv._dispatch_tool_call_request(
        srv._InProcessToolRequest(tool, body, headers={}), transport="api"))
    raw = response.text.encode("utf-8")
    if response.status >= 400:
        raise urllib.error.HTTPError(
            request.full_url, response.status, "daemon refused",
            hdrs=None, fp=io.BytesIO(raw))

    class _Response:
        def read(self):
            return raw

    return _Response()


class DuplicateRetryGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        srv._DUPLICATE_FAILURE_STATE.clear()
        srv._DUPLICATE_FIRST_SEEN.clear()

    async def test_second_identical_schema_failure_is_blocked(self):
        """Same payload, http then mcp: the second one is refused as a duplicate.

        Both legs end up at the same validator -- directly for http, through the
        proxy for mcp -- which is what lets the second call recognise the first.
        """
        payload = {"unexpected_field": "x"}
        first = await srv.dispatch_tool_call("workflow_list", payload, transport="http")
        first_body = json.loads(first.text)
        self.assertEqual(first_body["error_name"], "UNSUPPORTED_FIELD")

        with mock.patch.object(urllib.request, "urlopen", _proxy_loopback):
            second = await srv.dispatch_tool_call(
                "workflow_list", payload, transport="mcp")
        second_body = json.loads(second.text)
        self.assertEqual(second_body["error_name"], "DUPLICATE_RETRY_BLOCKED")
        self.assertEqual(second_body["unsupported_fields"], ["unexpected_field"])
        self.assertFalse(second_body["retryable"])


if __name__ == "__main__":
    unittest.main()
