"""Hints other CAs must see for hidden [A] restart."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class TestHiddenRestartHints(unittest.TestCase):
    def test_core_rule_hidden_restart_contract(self):
        text = (ROOT / "examples" / "CORE_RULE.txt").read_text(encoding="utf-8")
        self.assertIn("restart_orchestrator_via_bat", text)
        self.assertIn("delay_seconds=2", text)
        self.assertIn("do NOT pass expected_generation", text)
        self.assertIn("CREATE_NO_WINDOW", text)

    def test_slim_rule_hidden_restart_contract(self):
        text = (ROOT / "examples" / "SLIM_RULE.txt").read_text(encoding="utf-8")
        self.assertIn("restart_orchestrator_via_bat", text)
        self.assertIn("delay_seconds=2", text)
        self.assertIn("CREATE_NO_WINDOW", text)
        self.assertIn("do NOT pass expected_generation", text)

    def test_slim_inline_points_at_hidden_helper(self):
        text = (ROOT / "examples" / "SLIM_RULE_INLINE.txt").read_text(encoding="utf-8")
        self.assertIn("restart_orchestrator_via_bat", text)
        self.assertIn("CREATE_NO_WINDOW", text)

    def test_lifecycle_skill_hidden_helper(self):
        text = (
            ROOT / ".ToolA_Skill" / "skills" / "16_server_lifecycle" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE_NO_WINDOW", text)
        self.assertIn("hidden helper", text)
        self.assertIn("delay_seconds=2", text)
        self.assertIn("Do **NOT** pass `expected_generation`", text)
        self.assertNotIn("spawns a detached helper", text)

    def test_command_windows_points_at_hidden_restart(self):
        text = (
            ROOT / ".ToolA_Skill" / "skills" / "08_command_windows" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("restart_orchestrator_via_bat", text)
        self.assertIn("CREATE_NO_WINDOW", text)

    def test_schema_description_is_hidden_not_detached(self):
        text = (ROOT / "orchestrator_server.py").read_text(encoding="utf-8")
        self.assertIn("CREATE_NO_WINDOW", text)
        self.assertIn("no console focus-steal", text)
        self.assertNotIn("spawns a detached", text)

    def test_queued_mechanism_says_hidden_helper(self):
        text = (ROOT / "tools_v15.py").read_text(encoding="utf-8")
        self.assertIn("start_orchestrator.bat (hidden helper)", text)
        self.assertNotIn("start_orchestrator.bat (detached helper)", text)


if __name__ == "__main__":
    unittest.main()
