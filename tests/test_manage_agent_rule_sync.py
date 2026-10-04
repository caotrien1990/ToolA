import hashlib
import json
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


class TestManageAgentSkillSyncBundlesSharedReferences(unittest.TestCase):
    """FIX2 (2026-09-28): a SKILL.md link "references/<name>.md" resolves beside the
    skill OR in the shared .ToolA_Skill/references/ dir -- the skill_reference_missing
    gate in code_tools.py accepts both -- but sync_skill (_ma_write_skill) copied only
    the skill folder, so every shared-dir link dangled in every CA copy. sync_skill now
    bundles the shared files a skill links, and the gate + sync ask ONE matcher
    (_skill_reference_links), so they cannot drift apart again."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.repo = root / "repo"
        self.src = self.repo / ".ToolA_Skill" / "skills" / "demo"
        (self.src / "references").mkdir(parents=True)
        # shared.md only in the shared dir; local.md exists in BOTH (skill-local must
        # win); missing.md exists nowhere; the duplicate link proves per-name dedupe.
        (self.src / "SKILL.md").write_text(
            "# demo\n\nsee references/shared.md and references/local.md and "
            "references/missing.md; shared again references/shared.md\n",
            encoding="utf-8")
        (self.src / "references" / "local.md").write_text("local\n", encoding="utf-8")
        self.shared_dir = self.repo / ".ToolA_Skill" / "references"
        self.shared_dir.mkdir(parents=True)
        (self.shared_dir / "shared.md").write_text("shared\n", encoding="utf-8")
        (self.shared_dir / "local.md").write_text("shared copy must not win\n", encoding="utf-8")
        self.skill_dir = root / "agent_skills"
        self.agent = {"id": "test-agent", "skill_dir": str(self.skill_dir), "skill_format": "folder"}
        # _ma_write_skill resolves its source from code_tools.__file__.
        patcher = mock.patch.object(code_tools, "__file__", str(self.repo / "code_tools.py"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_real_sync_bundles_shared_and_lets_skill_local_win(self):
        result = code_tools._ma_write_skill(self.agent, {}, "demo", dry_run=False)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["match"], result)
        target = self.skill_dir / "demo"
        self.assertEqual((target / "references" / "shared.md").read_bytes(),
                         (self.shared_dir / "shared.md").read_bytes())
        self.assertEqual((target / "references" / "local.md").read_bytes(),
                         (self.src / "references" / "local.md").read_bytes())
        self.assertFalse((target / "references" / "missing.md").exists())
        self.assertEqual(result["files"], 3)
        self.assertEqual(result["shared_references"], ["references/shared.md"])

    def test_dry_run_previews_the_bundled_files_without_writing(self):
        result = code_tools._ma_write_skill(self.agent, {}, "demo", dry_run=True)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["files"], 3)
        self.assertEqual(result["shared_references"], ["references/shared.md"])
        self.assertFalse((self.skill_dir / "demo").exists())

    def test_a_skill_with_no_reference_links_is_unchanged(self):
        plain = self.repo / ".ToolA_Skill" / "skills" / "plain"
        plain.mkdir(parents=True)
        (plain / "SKILL.md").write_text("# plain\n\nno reference links here\n", encoding="utf-8")
        agent = {"id": "test-agent", "skill_dir": str(self.skill_dir), "skill_format": "folder"}

        result = code_tools._ma_write_skill(agent, {}, "plain", dry_run=False)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["match"], result)
        self.assertNotIn("shared_references", result)
        self.assertEqual(code_tools._ma_skill_dir_summary(plain),
                         code_tools._ma_skill_dir_summary(plain, {}))
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["sha256"], code_tools._ma_skill_dir_summary(plain)["sha256"])


class TestManageAgentSyncHook(unittest.TestCase):
    """P22: sync_hook installs + registers the native read/write gates per host
    format. A tmp fake profile per format proves the right shape is written, a
    second run is idempotent (changed:false), unrelated hook entries are
    preserved, and fbuf/hermes (hook_format=none) skip with coverage=none. The
    probe runs the REAL gate files via node."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.root = root
        # Fake repo hooks dir: the real SOT gates + adapters, copied so the sync
        # reads from a controlled source. Reuse the live repo copies -- they are
        # the SOT this feature ships.
        repo_hooks = root / "repo" / "hooks"
        (repo_hooks / "adapters" / "zcode-plugin" / "hooks").mkdir(parents=True)
        real_hooks = Path(code_tools.__file__).parent / "hooks"
        for gate in ("native-write-gate.js", "read-gate.js"):
            shutil.copyfile(real_hooks / gate, repo_hooks / gate)
        for name in ("codex.js", "opencode-gates.js"):
            shutil.copyfile(real_hooks / "adapters" / name, repo_hooks / "adapters" / name)
        shutil.copyfile(real_hooks / "adapters" / "zcode-plugin" / "hooks" / "hooks.json",
                        repo_hooks / "adapters" / "zcode-plugin" / "hooks" / "hooks.json")
        self.repo_hooks = repo_hooks
        patcher = mock.patch.object(code_tools, "_ma_hooks_repo_dir", return_value=repo_hooks)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _agent(self, aid, fmt, hooks_dir, hook_file, trust="none"):
        return {"id": aid, "hook_format": fmt, "hooks_dir": str(hooks_dir),
                "hook_file": str(hook_file), "hook_trust_step": trust}

    # ---------- plan / dry_run ----------
    def test_dry_run_reports_plan_without_writing(self):
        hd = self.root / "h_claude"
        hf = self.root / "settings.json"
        hf.write_text("{}", encoding="utf-8")
        ag = self._agent("clau", "claude-settings", hd, hf)
        result = code_tools._ma_write_hook(ag, {}, dry_run=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["dry_run"])
        self.assertIn("native-write-gate.js", " ".join(result["files_to_write"]))
        self.assertEqual(result["coverage"], "pending")
        self.assertFalse((hd / "native-write-gate.js").exists())

    def test_none_format_skips_with_coverage_none(self):
        for aid in ("fbuf", "hermes"):
            ag = self._agent(aid, "none", "", "")
            result = code_tools._ma_write_hook(ag, {}, dry_run=False)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["coverage"], "none")
            self.assertFalse(result["changed"])

    def test_unknown_format_errors(self):
        ag = self._agent("x", "bogus", self.root / "h", self.root / "f.json")
        result = code_tools._ma_write_hook(ag, {}, dry_run=True)
        self.assertFalse(result["ok"])
        self.assertIn("unknown hook_format", result["error"])

    # ---------- claude-settings ----------
    def test_claude_settings_registers_and_is_idempotent_and_preserves_others(self):
        hd = self.root / "h_claude"
        hf = self.root / "settings.json"
        # an unrelated pre-existing hook entry that must survive byte-for-byte
        hf.write_text(json.dumps({
            "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "node \"keep.js\""}]}]},
            "other_key": {"nested": True},
        }), encoding="utf-8")
        ag = self._agent("clau", "claude-settings", hd, hf)
        r1 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r1["ok"], r1)
        self.assertTrue(r1["changed"])
        self.assertTrue(r1["registered"])
        self.assertTrue((hd / "native-write-gate.js").exists())
        self.assertTrue((hd / "read-gate.js").exists())
        self.assertTrue((hd / "hook-agent.json").exists())
        binding = json.loads((hd / "hook-agent.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["agent_id"], "clau")
        data = json.loads(hf.read_text(encoding="utf-8"))
        # unrelated SessionStart entry preserved
        self.assertEqual(data["hooks"]["SessionStart"][0]["hooks"][0]["command"], 'node "keep.js"')
        self.assertEqual(data["other_key"], {"nested": True})
        # gate entries registered
        pre = data["hooks"]["PreToolUse"]
        cmds = [h["command"] for g in pre for h in g["hooks"]]
        self.assertTrue(any("native-write-gate.js" in c for c in cmds))
        self.assertTrue(any("read-gate.js" in c for c in cmds))
        # second run -> idempotent
        r2 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r2["ok"], r2)
        self.assertFalse(r2["changed"], r2)

    # ---------- codex ----------
    def test_codex_hooks_json_shape_and_idempotent(self):
        hd = self.root / "h_codex"
        hf = self.root / "hooks.json"
        hf.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "node 'keep.js'"}]}]}}), encoding="utf-8")
        ag = self._agent("codo", "codex-hooks-json", hd, hf, trust="codex-hooks-command")
        r1 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r1["ok"], r1)
        self.assertTrue((hd / "codex-adapter.js").exists(), "adapter copied")
        data = json.loads(hf.read_text(encoding="utf-8"))
        self.assertEqual(data["hooks"]["SessionStart"][0]["hooks"][0]["command"], "node 'keep.js'")
        pre = data["hooks"]["PreToolUse"]
        matchers = [g.get("matcher") for g in pre]
        self.assertIn("apply_patch", matchers)
        self.assertEqual(r1["trust_step"], "codex-hooks-command")
        r2 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertFalse(r2["changed"], r2)

    # ---------- opencode ----------
    def test_opencode_plugin_shape_and_idempotent(self):
        hd = self.root / "h_ope"
        hf = self.root / "opencode.json"
        hf.write_text(json.dumps({"plugin": ["./plugins/active-task-guard.js"], "model": "x"}), encoding="utf-8")
        ag = self._agent("opencode", "opencode-plugin", hd, hf)
        r1 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r1["ok"], r1)
        # plugin ships as .mjs (explicit ESM), gates as .cjs (explicit CJS), and
        # the dir carries a {"type":"commonjs"} package.json marker so the
        # ancestor ~/.config/opencode/package.json "type":"module" cannot break
        # the CJS gates (the live-install bug this test now pins).
        self.assertTrue((hd / "toola-gates.mjs").exists())
        self.assertTrue((hd / "native-write-gate.cjs").exists())
        self.assertTrue((hd / "read-gate.cjs").exists())
        pkg = json.loads((hd / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg.get("type"), "commonjs")
        # the installed plugin must point at the .cjs gates
        plugin_src = (hd / "toola-gates.mjs").read_text(encoding="utf-8")
        self.assertIn("native-write-gate.cjs", plugin_src)
        self.assertIn("read-gate.cjs", plugin_src)
        data = json.loads(hf.read_text(encoding="utf-8"))
        self.assertIn("./plugins/active-task-guard.js", data["plugin"], "existing plugin preserved")
        self.assertIn("./plugins/toola-gates.mjs", data["plugin"])
        self.assertEqual(data["model"], "x")
        r2 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertFalse(r2["changed"], r2)

    # ---------- zcode ----------
    def test_zcode_plugin_enables_plugin_and_idempotent(self):
        hd = self.root / "h_zcode"
        hf = self.root / "config.json"
        hf.write_text(json.dumps({"plugins": {"suppressedBuiltins": ["ios@x"], "enabledPlugins": {"example-plugin@zcode-plugins-official": True}}}), encoding="utf-8")
        ag = self._agent("zcode", "zcode-plugin", hd, hf, trust="zcode-plugin-enable")
        r1 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r1["ok"], r1)
        data = json.loads(hf.read_text(encoding="utf-8"))
        self.assertTrue(data["plugins"]["enabledPlugins"].get("toola-gates@local"))
        self.assertTrue(data["plugins"]["enabledPlugins"].get("example-plugin@zcode-plugins-official"))
        self.assertEqual(data["plugins"]["suppressedBuiltins"], ["ios@x"])
        self.assertEqual(r1["trust_step"], "zcode-plugin-enable")
        r2 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertFalse(r2["changed"], r2)

    # ---------- probe with the real gates ----------
    def test_probe_real_gates_deny_and_allow(self):
        hd = self.root / "h_probe"
        ag = self._agent("probeagent", "claude-settings", hd, self.root / "s.json")
        # install so the probe has gate files in hooks_dir
        code_tools._ma_write_hook(ag, {}, dry_run=False)
        probe = code_tools._ma_hook_probe(ag, hd)
        # The probe targets the REAL ToolA repo (resolved from code_tools.__file__,
        # not the patched _ma_hooks_repo_dir): read gate is local + deterministic,
        # so both read probes must hold; write_deny holds whether [A] is up
        # (preflight deny) or down (fail-closed).
        self.assertTrue(probe["read_deny"], probe)
        self.assertTrue(probe["read_allow"], probe)
        self.assertTrue(probe["write_deny"], probe)
        # outside_allow needs [A] up to answer scope=outside_project; a down
        # daemon fail-closes the write gate, which is the gate's policy, not a
        # probe defect -- skip only that leg.
        import urllib.request as _ur
        try:
            _ur.urlopen("http://localhost:8083/health", timeout=3)
        except Exception:
            self.skipTest("[A] daemon not reachable; outside_allow needs preflight")
        self.assertTrue(probe["outside_allow"], probe)

    def test_probe_fixture_under_a_harness_dir_would_exempt_the_read_gate(self):
        # Regression pin for the live-probe defect: the OLD probe put _probe_proj
        # under hooks_dir, which for a real host sits inside a harness profile
        # dir (.claude*/.codex*) that the read gate exempts by design -- the
        # probe passed in tests (tmp root) and failed live. The fixed probe
        # reads the REAL repo file instead; assert it never builds a fixture
        # under hooks_dir.
        hd = self.root / "h_probe2"
        ag = self._agent("probeagent2", "claude-settings", hd, self.root / "s.json")
        code_tools._ma_write_hook(ag, {}, dry_run=False)
        code_tools._ma_hook_probe(ag, hd)
        self.assertFalse((hd / "_probe_proj").exists(),
                         "probe must not gate a fixture inside the (possibly harness-exempt) hooks_dir")

    def test_opencode_install_survives_a_type_module_ancestor_package_json(self):
        # Mirror the live layout: ~/.config/opencode/package.json declares
        # "type":"module", which used to make the installed CJS gates die on
        # require() so the plugin allowed everything. The install must ship the
        # gates as .cjs, the plugin as .mjs, and a {"type":"commonjs"} marker,
        # and the probe must then pass all four booleans through the plugin.
        stage = self.root / "fake_opencode_config"
        stage.mkdir()
        (stage / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
        plugins = stage / "plugins"
        plugins.mkdir()
        hf = stage / "opencode.json"
        hf.write_text(json.dumps({"plugin": ["./plugins/active-task-guard.js"]}), encoding="utf-8")
        (plugins / "active-task-guard.js").write_text("export default async () => ({});\n", encoding="utf-8")
        ag = {"id": "opencode", "hook_format": "opencode-plugin",
              "hooks_dir": str(plugins), "hook_file": str(hf), "hook_trust_step": "none"}
        r = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["coverage"], "full", r)
        probe = r["probe"]
        self.assertTrue(probe["read_deny"], probe)
        self.assertTrue(probe["read_allow"], probe)
        self.assertTrue(probe["write_deny"], probe)
        self.assertTrue(probe["outside_allow"], probe)




    # ---------- multi-target (leader defect 1: codo drives .codex + .codex-ltnai) ----------
    def test_two_target_agent_installs_both_idempotent_per_target_result(self):
        hd1 = self.root / "h_codex_a"
        hf1 = self.root / "hooks_a.json"
        hf1.write_text("{}", encoding="utf-8")
        hd2 = self.root / "h_codex_b"
        hf2 = self.root / "hooks_b.json"
        hf2.write_text("{}", encoding="utf-8")
        ag = {"id": "codo",
              # flat shorthand points at target A; hook_targets wins and adds B.
              "hook_format": "codex-hooks-json", "hooks_dir": str(hd1),
              "hook_file": str(hf1), "hook_trust_step": "codex-hooks-command",
              "hook_targets": [
                  {"hook_file": str(hf1), "hook_format": "codex-hooks-json",
                   "hooks_dir": str(hd1), "hook_trust_step": "codex-hooks-command"},
                  {"hook_file": str(hf2), "hook_format": "codex-hooks-json",
                   "hooks_dir": str(hd2), "hook_trust_step": "codex-hooks-command"},
              ]}
        targets = code_tools._ma_hook_agent_targets(ag)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0]["id"], "codo")
        self.assertEqual(targets[1]["hooks_dir"], str(hd2))

        r1 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r1["ok"], r1)
        self.assertTrue(r1["changed"])
        self.assertEqual(len(r1["targets"]), 2)
        for t in r1["targets"]:
            self.assertTrue(t["ok"], t)
            self.assertTrue(t["changed"], t)
            self.assertEqual(t["coverage"], "pending_trust", t)
        self.assertEqual(r1["coverage"], "pending_trust")
        # both targets installed their own copies + registration
        for hd, hf in ((hd1, hf1), (hd2, hf2)):
            self.assertTrue((hd / "codex-adapter.js").exists())
            self.assertTrue((hd / "hook-agent.json").exists())
            data = json.loads(hf.read_text(encoding="utf-8"))
            matchers = [g.get("matcher") for g in data["hooks"]["PreToolUse"]]
            self.assertIn("apply_patch", matchers)
        # second run -> idempotent everywhere
        r2 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r2["ok"], r2)
        self.assertFalse(r2["changed"], r2)
        for t in r2["targets"]:
            self.assertFalse(t["changed"], t)

    def test_flat_fields_still_work_as_single_target_shorthand(self):
        ag = self._agent("clau", "claude-settings", self.root / "h", self.root / "s.json")
        targets = code_tools._ma_hook_agent_targets(ag)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["hook_format"], "claude-settings")
        self.assertEqual(targets[0]["hook_trust_step"], "none")


class TestCodexAdapterFailPolicy(unittest.TestCase):
    """P22 leader defect 2: the codex adapter must stay fail-CLOSED for writes
    (unparseable apply_patch, gate crash/timeout) and fail-OPEN for shell reads.
    These tests drive the REAL repo adapter with node; the gates are replaced by
    tiny stubs where a timeout or crash must be simulated."""

    @classmethod
    def setUpClass(cls):
        cls.adapter = Path(code_tools.__file__).parent / "hooks" / "adapters" / "codex.js"
        if not cls.adapter.is_file():
            raise unittest.SkipTest("repo adapter missing")
        cls.repo_root = Path(code_tools.__file__).parent

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.work = Path(self.tempdir.name)
        # copy the adapter into the sandbox and stub the two gates it spawns
        self.adir = self.work / "hooks"
        self.adir.mkdir()
        shutil.copyfile(self.adapter, self.adir / "codex.js")
        self.state = self.work / "state"
        self.state.mkdir()

    def _write_gate(self, body):
        (self.adir / "native-write-gate.js").write_text(body, encoding="utf-8")

    def _read_gate(self, body):
        (self.adir / "read-gate.js").write_text(body, encoding="utf-8")

    def _run(self, payload, extra_env=None):
        import subprocess
        env = dict(os.environ)
        env["ORCHESTRATOR_AGENT_ID"] = "codo"
        env["NATIVE_WRITE_GATE_DIR"] = str(self.state)
        env["NATIVE_READ_GATE_DIR"] = str(self.state)
        if extra_env:
            env.update(extra_env)
        r = subprocess.run(["node", str(self.adir / "codex.js")],
                           input=json.dumps(payload), capture_output=True, text=True,
                           env=env, timeout=30)
        return r.returncode, (r.stdout or "").strip()

    @staticmethod
    def _denied(out):
        return '"permissionDecision"' in out and '"deny"' in out

    def test_unparseable_apply_patch_denies(self):
        # Real write gate from the repo SOT: it fail-closes on an empty file_path
        # without needing [A]. Adapter must relay (or emit) a deny, never allow.
        real_gate = Path(code_tools.__file__).parent / "hooks" / "native-write-gate.js"
        shutil.copyfile(real_gate, self.adir / "native-write-gate.js")
        self._read_gate("process.exit(0);\n")
        rc, out = self._run({"tool_name": "apply_patch",
                             "tool_input": {"patch": "garbage with no file marker"},
                             "cwd": str(self.work)})
        self.assertTrue(self._denied(out), f"rc={rc} out={out[:300]}")

    def test_gate_timeout_on_write_denies(self):
        self._write_gate("setTimeout(()=>{}, 60000);\n")  # sleeper
        self._read_gate("process.exit(0);\n")
        rc, out = self._run(
            {"tool_name": "apply_patch",
             "tool_input": {"patch": "*** Begin Patch\n*** Update File: x.txt\n@@\n-a\n+b\n*** End Patch\n"},
             "cwd": str(self.work)},
            extra_env={"TOOLA_GATE_TIMEOUT_MS": "500"})
        self.assertTrue(self._denied(out), f"rc={rc} out={out[:300]}")

    def test_gate_timeout_on_shell_allows(self):
        self._write_gate("process.exit(0);\n")
        self._read_gate("setTimeout(()=>{}, 60000);\n")  # sleeper
        rc, out = self._run({"tool_name": "shell", "tool_input": {"command": "ls"},
                             "cwd": str(self.work)},
                            extra_env={"TOOLA_GATE_TIMEOUT_MS": "500"})
        self.assertFalse(self._denied(out), f"rc={rc} out={out[:300]}")
        self.assertEqual(rc, 0)

    def test_project_apply_patch_denies_via_preflight_or_failclosed(self):
        # Target inside the ToolA repo: [A] up -> preflight scope=project -> deny;
        # [A] down -> the gate fail-closes. Either way the answer must be deny.
        real_gate = Path(code_tools.__file__).parent / "hooks" / "native-write-gate.js"
        shutil.copyfile(real_gate, self.adir / "native-write-gate.js")
        self._read_gate("process.exit(0);\n")
        target = self.repo_root / "_adapter_probe_never_created.py"
        patch = f"*** Begin Patch\n*** Update File: {target}\n@@\n-a\n+b\n*** End Patch\n"
        rc, out = self._run({"tool_name": "apply_patch", "tool_input": {"patch": patch},
                             "cwd": str(self.repo_root)})
        self.assertTrue(self._denied(out), f"rc={rc} out={out[:300]}")
        self.assertFalse(target.exists())

    def test_outside_project_apply_patch_allows(self):
        # Needs [A] up (preflight scope=outside_project -> gate allows). Skip when
        # the daemon is down rather than assert against a fail-closed outage.
        import urllib.request as _ur
        try:
            _ur.urlopen("http://localhost:8083/health", timeout=3)
        except Exception:
            self.skipTest("[A] daemon not reachable")
        real_gate = Path(code_tools.__file__).parent / "hooks" / "native-write-gate.js"
        shutil.copyfile(real_gate, self.adir / "native-write-gate.js")
        self._read_gate("process.exit(0);\n")
        target = self.work / "outside.txt"
        patch = f"*** Begin Patch\n*** Update File: {target}\n@@\n-a\n+b\n*** End Patch\n"
        rc, out = self._run({"tool_name": "apply_patch", "tool_input": {"patch": patch},
                             "cwd": str(self.work)})
        self.assertFalse(self._denied(out), f"rc={rc} out={out[:300]}")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
