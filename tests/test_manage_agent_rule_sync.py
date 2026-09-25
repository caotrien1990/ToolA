import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools


class TestManageAgentMarkdownRuleSync(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.rule_path = Path(self.tempdir.name) / "AGENTS.md"
        self.agent = {
            "id": "test-agent",
            "rule_file": str(self.rule_path),
            "rule_format": "markdown",
            "rule_template": "slim",
        }
        self.source_sha = hashlib.sha256(b"canonical rule").hexdigest()
        self.patches = [
            mock.patch.object(code_tools, "_ma_rule_body", return_value="# Canonical rule\n\nUse [A]."),
            mock.patch.object(
                code_tools,
                "_ma_rule_source_metadata",
                return_value={
                    "rule_source": "SLIM_RULE.txt",
                    "rule_source_path": "test/SLIM_RULE.txt",
                    "rule_source_sha256": self.source_sha,
                },
            ),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_dry_run_reports_hashes_and_bounded_diff_without_write(self):
        self.rule_path.write_text("old\n" + ("x" * 5000), encoding="utf-8")
        original = self.rule_path.read_text(encoding="utf-8")

        with mock.patch.object(code_tools, "_atomic_write_text") as write, \
                mock.patch.object(code_tools, "_ma_backup") as backup:
            result = code_tools._ma_write_rule(self.agent, {}, dry_run=True)

        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["current_hash"], code_tools._sha256_text(original))
        self.assertEqual(len(result["expected_hash"]), 64)
        self.assertLessEqual(len(result["diff_summary"]), code_tools._MA_RULE_DIFF_MAX_CHARS)
        self.assertEqual(self.rule_path.read_text(encoding="utf-8"), original)
        write.assert_not_called()
        backup.assert_not_called()

    def test_unchanged_apply_does_not_backup_or_rewrite(self):
        preview = code_tools._ma_write_rule(self.agent, {}, dry_run=True)
        expected = (
            "# Canonical rule\n\nUse [A].\n\n"
            f"<!-- [A] managed rule | mode=A | template=slim | rule_source_sha256={self.source_sha} | "
            "owned by manage_agent sync_rule/set_mode - do not hand-edit -->\n"
        )
        self.assertEqual(preview["expected_hash"], code_tools._sha256_text(expected))
        self.assertIn(f"rule_source_sha256={self.source_sha}", expected)
        self.assertNotIn("generated=", expected)
        self.rule_path.write_text(expected, encoding="utf-8")

        with mock.patch.object(code_tools, "_atomic_write_text") as write, \
                mock.patch.object(code_tools, "_ma_backup") as backup:
            result = code_tools._ma_write_rule(self.agent, {}, dry_run=False)

        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["backup"], "")
        self.assertEqual(result["current_hash"], result["expected_hash"])
        self.assertEqual(result["diff_summary"], "")
        write.assert_not_called()
        backup.assert_not_called()

    def test_readback_mismatch_restores_backup(self):
        original = "original unmanaged rule\n"
        self.rule_path.write_text(original, encoding="utf-8")

        def corrupt_write(path, text, encoding="utf-8"):
            Path(path).write_text("corrupt readback\n", encoding=encoding)
            return "atomic_replace"

        with mock.patch.object(code_tools, "_atomic_write_text", side_effect=corrupt_write) as write:
            result = code_tools._ma_write_rule(self.agent, {}, dry_run=False)

        self.assertFalse(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["error"], "post-write readback mismatch; rolled back")
        self.assertTrue(result["backup"])
        self.assertTrue(Path(result["backup"]).is_file())
        self.assertEqual(self.rule_path.read_text(encoding="utf-8"), original)
        write.assert_called_once()


class TestManageAgentSkillSyncSkipsWriteBackups(unittest.TestCase):
    """sync_skill copied the source folder verbatim, so the SKILL.md.bak that write_fix
    leaves next to the source travelled into every agent's skill folder, and the
    folder summary counted it as skill content."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.src = root / "repo" / ".ToolA_Skill" / "skills" / "demo"
        self.src.mkdir(parents=True)
        (self.src / "SKILL.md").write_text("# demo\n", encoding="utf-8")
        (self.src / "ref.md").write_text("ref\n", encoding="utf-8")
        (self.src / "SKILL.md.bak").write_text("# rollback copy\n", encoding="utf-8")
        self.skill_dir = root / "agent_skills"
        self.agent = {"id": "test-agent", "skill_dir": str(self.skill_dir), "skill_format": "folder"}
        # _ma_write_skill resolves its source from code_tools.__file__.
        patcher = mock.patch.object(code_tools, "__file__", str(root / "repo" / "code_tools.py"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_copy_leaves_the_write_backup_behind(self):
        result = code_tools._ma_write_skill(self.agent, {}, "demo", dry_run=False)

        self.assertTrue(result["ok"], result)
        self.assertEqual(sorted(p.name for p in (self.skill_dir / "demo").iterdir()), ["SKILL.md", "ref.md"])
        self.assertEqual(result["files"], 2)
        self.assertTrue(result["match"])

    def test_a_backup_already_deployed_does_not_read_as_drift(self):
        # Copies synced before the fix still carry SKILL.md.bak, while a clean clone of the
        # source has none: the two must still compare equal, because only content is the skill.
        deployed = self.skill_dir / "demo"
        deployed.mkdir(parents=True)
        for name in ("SKILL.md", "ref.md", "SKILL.md.bak"):
            shutil.copyfile(self.src / name, deployed / name)
        (self.src / "SKILL.md.bak").unlink()

        self.assertEqual(code_tools._ma_skill_dir_summary(deployed), code_tools._ma_skill_dir_summary(self.src))


class TestManageAgentRegistryPathsMeetTheDenyList(unittest.TestCase):
    """#35: register / update store any path, and sync_rule / sync_mcp / sync_skill then
    wrote it with no deny-list verdict -- the dispatcher's gate sees only the call's own
    body. Each write site is refused on its own, a dry run included so its preview is
    the truth; the same call with an ordinary path is the positive control."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.home = root / "home"
        src = root / "repo" / ".ToolA_Skill" / "skills" / "demo"
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text("# demo\n", encoding="utf-8")
        for patcher in (
            mock.patch.dict(os.environ, {"USERPROFILE": str(self.home)}),
            mock.patch.object(code_tools, "__file__", str(root / "repo" / "code_tools.py")),
            mock.patch.object(code_tools, "_ma_rule_body", return_value="# Canonical rule\n"),
            mock.patch.object(code_tools, "_ma_rule_source_metadata", return_value={
                "rule_source": "SLIM_RULE.txt", "rule_source_path": "t", "rule_source_sha256": "0" * 64}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _assert_refused(self, result, target):
        self.assertFalse(result["ok"], result)
        self.assertEqual((result.get("error_name"), result.get("group")), ("XROOT_DENIED", "user_dotfiles"), result)
        self.assertFalse(target.exists(), target)

    def test_sync_rule_refuses_a_rule_file_in_a_credential_directory(self):
        target = self.home / ".ssh" / "AGENTS.md"
        for dry_run in (True, False):
            self._assert_refused(code_tools._ma_write_rule({"id": "t", "rule_file": str(target)}, {}, dry_run), target)
        control = code_tools._ma_write_rule({"id": "t", "rule_file": str(self.home / "AGENTS.md")}, {}, True)
        self.assertTrue(control["ok"], control)

    def test_sync_mcp_refuses_an_mcp_file_in_a_credential_directory(self):
        target = self.home / ".aws" / "mcp.json"
        reg = {"mcp_command": "python", "mcp_args_server": "server.py"}
        for dry_run in (True, False):
            self._assert_refused(code_tools._ma_write_mcp({"id": "t", "mcp_file": str(target)}, reg, dry_run), target)
        control = code_tools._ma_write_mcp({"id": "t", "mcp_file": str(self.home / "mcp.json")}, reg, True)
        self.assertTrue(control["ok"], control)

    def test_sync_skill_refuses_a_skill_dir_in_a_credential_directory(self):
        skill_dir = self.home / ".azure" / "skills"
        for dry_run in (True, False):
            result = code_tools._ma_write_skill({"id": "t", "skill_dir": str(skill_dir)}, {}, "demo", dry_run)
            self._assert_refused(result, skill_dir)
        control = code_tools._ma_write_skill({"id": "t", "skill_dir": str(self.home / "skills")}, {}, "demo", True)
        self.assertTrue(control["ok"], control)

    def test_sync_skill_adapter_refuses_a_rule_file_in_a_credential_directory(self):
        target = self.home / ".ssh" / "AGENTS.md"
        agent = {"id": "t", "rule_file": str(target), "skill_format": "agents_md_ref"}
        for dry_run in (True, False):
            result = code_tools._ma_write_skill(agent, {}, "demo", dry_run, explicit_target=True)
            self._assert_refused(result, target)
        control = code_tools._ma_write_skill({**agent, "rule_file": str(self.home / "AGENTS.md")}, {}, "demo",
                                             True, explicit_target=True)
        self.assertTrue(control["ok"], control)


if __name__ == "__main__":
    unittest.main()
