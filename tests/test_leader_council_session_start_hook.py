import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = PROJECT_ROOT / ".ToolA_Skill" / "skills" / "leader-council-skill" / "hooks" / "session-start"


class LeaderCouncilSessionStartHookTests(unittest.TestCase):
    def _run_hook(self, rule_exists=True):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skill_dir = root / "leader-council-skill"
            references = skill_dir / "references"
            references.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text('skill "quoted" \\ path\nTiếng Việt', encoding="utf-8")
            (references / "dispatch-template.md").write_text("dispatch marker", encoding="utf-8")
            (references / "q-protocol.md").write_text("q protocol marker", encoding="utf-8")
            rule_path = root / "council_rule_leader.md"
            if rule_exists:
                rule_path.write_text('canonical "rule" \\ path\nLuật Việt', encoding="utf-8")
            env = dict(os.environ)
            env["LEADER_COUNCIL_SKILL_DIR"] = str(skill_dir)
            env["LEADER_COUNCIL_RULE_PATH"] = str(rule_path)
            bash_exe = Path(os.environ.get("GIT_BASH_EXE", "C:/Program Files/Git/bin/bash.exe"))
            result = subprocess.run(
                [str(bash_exe), HOOK_PATH.as_posix()], capture_output=True, text=True,
                encoding="utf-8", env=env, check=False,
            )
        return result, json.loads(result.stdout)

    def test_injects_canonical_rule_before_skill_and_references(self):
        result, payload = self._run_hook()
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        output = payload["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        context = output["additionalContext"]
        self.assertLess(context.index("CANONICAL COUNCIL LEADER RULE"), context.index("SKILL.md"))
        normalized = context.replace("\r\n", "\n")
        self.assertIn('canonical "rule" \\ path\nLuật Việt', normalized)
        self.assertIn('skill "quoted" \\ path\nTiếng Việt', normalized)
        self.assertIn("dispatch marker", context)
        self.assertIn("q protocol marker", context)

    def test_missing_canonical_rule_fails_open(self):
        result, payload = self._run_hook(rule_exists=False)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("SKILL.md", context)
        self.assertIn("dispatch marker", context)
        self.assertIn("q protocol marker", context)


if __name__ == "__main__":
    unittest.main()
