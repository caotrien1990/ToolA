import json
import os
import unittest
from unittest import mock

from orchestrator.server import mcp_stdio


class McpGenerationStampTests(unittest.TestCase):
    def setUp(self):
        mcp_stdio._MCP_SERVER_GENERATION_SEEN = None

    def tearDown(self):
        mcp_stdio._MCP_SERVER_GENERATION_SEEN = None

    def test_generation_change_self_heals_by_default(self):
        """TA-5: a restart must not kill MCP for the rest of the client's life.

        The gate used to latch the first generation and fail every later call with
        MCP_STALE_RELOAD_REQUIRED until a manual client reload. Tools proxy to the
        live daemon over HTTP, so the stamp is diagnostic, not a correctness guard:
        the gate now re-baselines and lets the call through."""
        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-a"))

        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-b"))
        # re-baselined, so the NEW generation is now the quiet steady state
        self.assertEqual(mcp_stdio._MCP_SERVER_GENERATION_SEEN, "gen-b")
        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-b"))

    def test_strict_mode_still_fails_closed(self):
        """CLM_MCP_STRICT_GENERATION=1 restores the hard reload signal."""
        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-a"))

        with mock.patch.dict(os.environ, {"CLM_MCP_STRICT_GENERATION": "1"}):
            stale = mcp_stdio._mcp_generation_gate("gen-b")

        self.assertIsNotNone(stale)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["error_name"], "MCP_STALE_RELOAD_REQUIRED")
        self.assertEqual(stale["hint"], "Reload Claude/MCP client")
        self.assertEqual(stale["generation"], "gen-b")
        self.assertEqual(stale["expected_generation"], "gen-a")
        # strict mode must NOT adopt the new generation
        self.assertEqual(mcp_stdio._MCP_SERVER_GENERATION_SEEN, "gen-a")

    def test_unknown_generation_is_neither_baseline_nor_compared(self):
        self.assertIsNone(mcp_stdio._mcp_generation_gate(None))
        self.assertIsNone(mcp_stdio._MCP_SERVER_GENERATION_SEEN)
        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-a"))
        self.assertIsNone(mcp_stdio._mcp_generation_gate(None))
        self.assertEqual(mcp_stdio._MCP_SERVER_GENERATION_SEEN, "gen-a")

    def test_add_generation_preserves_json_object_fields(self):
        wrapped = mcp_stdio._mcp_add_generation('{"ok": true, "value": 3}', "gen-a")

        self.assertEqual(
            json.loads(wrapped),
            {"ok": True, "value": 3, "generation": "gen-a"},
        )


if __name__ == "__main__":
    unittest.main()
