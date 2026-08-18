import json
import unittest

import orchestrator_server as srv


class DuplicateRetryGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        srv._DUPLICATE_FAILURE_STATE.clear()

    async def test_second_identical_schema_failure_is_blocked(self):
        payload = {"unexpected_field": "x"}
        first = await srv.dispatch_tool_call("workflow_list", payload, transport="http")
        first_body = json.loads(first.text)
        self.assertEqual(first_body["error_name"], "UNSUPPORTED_FIELD")

        second = await srv.dispatch_tool_call("workflow_list", payload, transport="mcp")
        second_body = json.loads(second.text)
        self.assertEqual(second_body["error_name"], "DUPLICATE_RETRY_BLOCKED")
        self.assertFalse(second_body["retryable"])


if __name__ == "__main__":
    unittest.main()
