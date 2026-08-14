import json
import unittest
from pathlib import Path

from orchestrator.core.ca_policy_matrix import (
    agent_transport_policy,
    normalize_agent_record,
    open_session_policy_payload,
)


ROOT = Path(__file__).resolve().parents[1]


class TestAgentTransportPolicy(unittest.TestCase):
    def test_explicit_http_only_overrides_desktop_default_normalization(self):
        record = {
            "id": "zcode",
            "mode": "A",
            "agent_transport_policy": "http_only",
            # A stale legacy/default value must not override the explicit runtime field.
            "transport_policy": "a_first_mcp_t4_ok",
        }

        normalized = normalize_agent_record("zcode", record)

        self.assertEqual(agent_transport_policy("zcode", record), "http_only")
        self.assertEqual(normalized["agent_transport_policy"], "http_only")
        self.assertEqual(normalized["transport_policy"], "http_only")
        self.assertTrue(normalized["governance"]["http_only"])

    def test_zcode_persona_registry_and_session_payload_agree_on_http_only(self):
        registry = json.loads(
            (ROOT / "agents_registry.json").read_text(encoding="utf-8")
        )
        zcode = registry["agents"]["zcode"]

        self.assertIn("HTTP API", zcode["persona"])
        self.assertIn("do not use MCP", zcode["persona"])
        self.assertEqual(zcode["agent_transport_policy"], "http_only")
        self.assertEqual(zcode["transport_policy"], "http_only")
        self.assertTrue(zcode["governance"]["http_only"])
        self.assertEqual(
            open_session_policy_payload(agent_id="zcode")["agent_transport_policy"],
            "http_only",
        )
        # MCP-pack provisioning metadata may coexist; it does not authorize runtime MCP.
        self.assertTrue(zcode["mcp_file"])


if __name__ == "__main__":
    unittest.main()
