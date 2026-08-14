import json
import unittest

from orchestrator.server import mcp_stdio


class McpGenerationStampTests(unittest.TestCase):
    def setUp(self):
        mcp_stdio._MCP_SERVER_GENERATION_SEEN = None

    def tearDown(self):
        mcp_stdio._MCP_SERVER_GENERATION_SEEN = None

    def test_generation_change_returns_reload_required_error(self):
        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-a"))

        stale = mcp_stdio._mcp_generation_gate("gen-b")

        self.assertIsNotNone(stale)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["error_name"], "MCP_STALE_RELOAD_REQUIRED")
        self.assertEqual(stale["hint"], "Reload Claude/MCP client")
        self.assertEqual(stale["generation"], "gen-b")
        self.assertEqual(stale["expected_generation"], "gen-a")

    def test_add_generation_preserves_json_object_fields(self):
        wrapped = mcp_stdio._mcp_add_generation('{"ok": true, "value": 3}', "gen-a")

        self.assertEqual(
            json.loads(wrapped),
            {"ok": True, "value": 3, "generation": "gen-a"},
        )


if __name__ == "__main__":
    unittest.main()
