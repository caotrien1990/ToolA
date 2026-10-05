import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools


def _registered_rule_file() -> str:
    """A rule_file taken from the LIVE agents_registry.json the daemon reads.

    The probe's memory_allow leg writes into <dirname(rule_file)>/projects/
    <slug>/memory/..., and is_agent_memory_target classifies that tree by
    scanning the REAL registry -- a tmp-rooted fake row would be denied, so a
    test agent must borrow a registered home. A row whose rule_file sits
    directly in HOME is skipped: the Matrix refuses to treat HOME\\projects as
    agent memory.
    """
    try:
        reg = json.loads(Path(code_tools._AGENTS_REGISTRY_PATH).read_text(encoding="utf-8"))
        home = Path.home().resolve()
    except Exception:
        return ""
    for rec in (reg.get("agents") or {}).values():
        rf = str((rec or {}).get("rule_file") or "").strip() if isinstance(rec, dict) else ""
        if not rf:
            continue
        try:
            if home.is_relative_to(Path(rf).resolve().parent):
                continue
        except (OSError, ValueError):
            continue
        return rf
    return ""


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
                "hook_file": str(hook_file), "hook_trust_step": trust,
                "rule_file": _registered_rule_file()}

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

    # ---------- zcode (P24: CLI-marketplace route) ----------
    class _ZcodeCliFake:
        """Faithful in-memory stand-in for `node zcode.cjs plugins ...`:
        install/update copy the marketplace plugin source into a cache dir
        keyed by manifest version (as the real CLI does -- update only copies
        on a HIGHER version), and list --json reports hookDetails parsed from
        the INSTALLED copy's hooks/hooks.json. Records every call."""

        def __init__(self, mkt_root: Path, cache_root: Path):
            self.mkt_root = Path(mkt_root)
            self.cache_root = Path(cache_root)
            self.calls = []          # [args list]
            self.installed = False   # toola-gates@toola-local present

        def _plugin_src(self) -> Path:
            return self.mkt_root / "toola-gates"

        def _installed_root(self) -> Path:
            try:
                ver = json.loads((self._plugin_src() / ".zcode-plugin" / "plugin.json")
                                 .read_text(encoding="utf-8")).get("version", "0.0.0")
            except Exception:
                ver = "0.0.0"
            return self.cache_root / "toola-local" / "toola-gates" / str(ver)

        def _list_payload(self):
            if not self.installed:
                return []
            root = self._installed_root()
            hooks_file = root / "hooks" / "hooks.json"
            details = []
            try:
                spec = json.loads(hooks_file.read_text(encoding="utf-8"))
                for event, groups in (spec.get("hooks") or {}).items():
                    for g in (groups or []):
                        for h in (g.get("hooks") or []):
                            details.append({
                                "event": event,
                                "matcher": g.get("matcher"),
                                "runnable": True,
                                "type": h.get("type", "process"),
                                "command": h.get("command", "node"),
                                "args": list(h.get("args") or []),
                            })
            except Exception:
                pass
            try:
                ver = json.loads((root / ".zcode-plugin" / "plugin.json")
                                 .read_text(encoding="utf-8")).get("version", "")
            except Exception:
                ver = ""
            return [{
                "id": "toola-gates@toola-local",
                "enabled": True,
                "rootPath": str(root),
                "version": ver,
                "diagnostics": [],
                "hookDetails": details,
            }]

        def run(self, cli_path, args, timeout=120):
            args = list(args)
            self.calls.append(args)
            if args[:2] == ["list", "--json"]:
                return {"ok": True, "rc": 0,
                        "stdout": json.dumps(self._list_payload()), "stderr": ""}
            if args[:2] == ["marketplace", "add"]:
                return {"ok": True, "rc": 0, "stdout": "marketplace added", "stderr": ""}
            if args and args[0] in ("install", "update"):
                # Copy the plugin source into the cache (the real CLI's install
                # and a version-bumped update both do exactly this).
                dst = self._installed_root()
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(self._plugin_src(), dst)
                self.installed = True
                return {"ok": True, "rc": 0, "stdout": args[0] + " ok", "stderr": ""}
            return {"ok": False, "rc": 2, "stdout": "", "stderr": "unknown cmd"}

    def _zcode_agent(self, mkt_root, hf, cli="C:/fake/zcode.cjs"):
        hd = Path(mkt_root) / "toola-gates" / "hooks"
        return {"id": "zcode", "hook_format": "zcode-plugin", "hooks_dir": str(hd),
                "hook_file": str(hf), "hook_trust_step": "none", "hook_cli": cli}

    def _zcode_setup(self):
        mkt = self.root / "toola-marketplace"
        (mkt / "toola-gates" / "hooks").mkdir(parents=True)
        hf = self.root / "config.json"
        hf.write_text(json.dumps({"plugins": {"suppressedBuiltins": ["ios@x"]}}), encoding="utf-8")
        # The CLI path must EXIST (registration refuses a missing hook_cli);
        # the CLI itself is mocked, so an empty file is enough.
        cli = self.root / "zcode.cjs"
        cli.write_text("// fake zcode cli\n", encoding="utf-8")
        fake = self._ZcodeCliFake(mkt, self.root / "cache")
        patcher = mock.patch.object(code_tools, "_ma_hook_run_zcode_cli", fake.run)
        patcher.start()
        self.addCleanup(patcher.stop)
        return mkt, hf, fake, str(cli)

    def test_zcode_first_install_writes_manifests_and_registers(self):
        mkt, hf, fake, cli = self._zcode_setup()
        ag = self._zcode_agent(mkt, hf, cli=cli)
        r1 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r1["ok"], r1)
        # manifest without a "hooks" key, version starts at 1.0.0
        pm = json.loads((mkt / "toola-gates" / ".zcode-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(pm["version"], "1.0.0")
        self.assertNotIn("hooks", pm)
        mm = json.loads((mkt / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(mm["name"], "toola-local")
        self.assertEqual(mm["plugins"][0]["source"], "./toola-gates")
        # hooks/hooks.json shipped next to the gates in the source plugin
        self.assertTrue((mkt / "toola-gates" / "hooks" / "hooks.json").exists())
        self.assertTrue((mkt / "toola-gates" / "hooks" / "read-gate.js").exists())
        self.assertTrue((mkt / "toola-gates" / "hooks" / "native-write-gate.js").exists())
        binding = json.loads((mkt / "toola-gates" / "hooks" / "hook-agent.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["agent_id"], "zcode")
        # CLI drove marketplace add + install; registration verified from list
        flat = [" ".join(c) for c in fake.calls]
        self.assertTrue(any(c.startswith("marketplace add") for c in flat), flat)
        self.assertIn("install toola-gates@toola-local", flat)
        self.assertTrue(r1["registered_ok"], r1)
        self.assertEqual(r1.get("version"), "1.0.0")
        self.assertTrue(r1.get("version_bumped"))
        self.assertEqual(r1["trust_step"], "none")
        self.assertIn("restart ZCode", r1.get("host_note", ""))
        # config.json untouched by us (the CLI owns enabledPlugins now)
        data = json.loads(hf.read_text(encoding="utf-8"))
        self.assertNotIn("enabledPlugins", data.get("plugins", {}))

    def test_zcode_second_run_idempotent_no_update(self):
        mkt, hf, fake, cli = self._zcode_setup()
        ag = self._zcode_agent(mkt, hf, cli=cli)
        r1 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r1["ok"], r1)
        fake.calls.clear()
        r2 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r2["ok"], r2)
        self.assertFalse(r2["changed"], r2)
        self.assertFalse(r2.get("version_bumped"), r2)
        self.assertEqual(r2.get("version"), "1.0.0")
        flat = [" ".join(c) for c in fake.calls]
        self.assertNotIn("install toola-gates@toola-local", flat)
        self.assertNotIn("update toola-gates@toola-local", flat)
        self.assertNotIn("host_note", r2)

    def test_zcode_gate_change_bumps_patch_and_updates(self):
        mkt, hf, fake, cli = self._zcode_setup()
        ag = self._zcode_agent(mkt, hf, cli=cli)
        r1 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertEqual(r1.get("version"), "1.0.0")
        # change a SOT gate byte -> content change -> bump -> update
        gate = self.repo_hooks / "read-gate.js"
        gate.write_text(gate.read_text(encoding="utf-8") + "\n// touched\n", encoding="utf-8")
        fake.calls.clear()
        r2 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r2["ok"], r2)
        self.assertTrue(r2["changed"], r2)
        self.assertEqual(r2.get("version"), "1.0.1")
        self.assertTrue(r2.get("version_bumped"))
        flat = [" ".join(c) for c in fake.calls]
        self.assertIn("update toola-gates@toola-local", flat)
        self.assertIn("restart ZCode to load toola-gates 1.0.1", r2.get("host_note", ""))

    def test_zcode_missing_hook_cli_is_ok_false(self):
        mkt = self.root / "toola-marketplace"
        (mkt / "toola-gates" / "hooks").mkdir(parents=True)
        hf = self.root / "config.json"
        hf.write_text("{}", encoding="utf-8")
        ag = self._zcode_agent(mkt, hf, cli="")
        r = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertFalse(r["ok"], r)
        self.assertIn("hook_cli", r.get("error", ""))

    def test_zcode_list_missing_hook_is_partial_and_named(self):
        mkt, hf, fake, cli = self._zcode_setup()
        ag = self._zcode_agent(mkt, hf, cli=cli)

        real_run = fake.run

        def run_without_read_gate(cli_path, args, timeout=120):
            res = real_run(cli_path, args, timeout)
            if list(args)[:2] == ["list", "--json"]:
                payload = json.loads(res["stdout"])
                for p in payload:
                    p["hookDetails"] = [h for h in p.get("hookDetails", [])
                                        if "read-gate.js" not in " ".join(h.get("args") or [])]
                res = dict(res, stdout=json.dumps(payload))
            return res

        with mock.patch.object(code_tools, "_ma_hook_run_zcode_cli", run_without_read_gate):
            r = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["registered_ok"], r)
        self.assertEqual(r["coverage"], "partial")
        named = " ".join(r.get("missing_registrations") or [])
        self.assertIn("read-gate.js", named)
        self.assertIn("Read|Grep|Glob|Bash", named)

    def test_zcode_dry_run_plans_without_mutating(self):
        mkt = self.root / "toola-marketplace"
        (mkt / "toola-gates" / "hooks").mkdir(parents=True)
        hf = self.root / "config.json"
        hf.write_text("{}", encoding="utf-8")
        fake = self._ZcodeCliFake(mkt, self.root / "cache")
        with mock.patch.object(code_tools, "_ma_hook_run_zcode_cli", fake.run):
            ag = self._zcode_agent(mkt, hf)
            r = code_tools._ma_write_hook(ag, {}, dry_run=True)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["dry_run"])
        self.assertEqual(r["coverage"], "pending")
        planned = r.get("planned_cli") or []
        self.assertTrue(any(c.startswith("marketplace add") for c in planned), planned)
        self.assertTrue(any("install toola-gates@toola-local" in c for c in planned), planned)
        # only read-only list --json reached the CLI
        for c in fake.calls:
            self.assertEqual(list(c)[:2], ["list", "--json"], fake.calls)
        # nothing written
        self.assertFalse((mkt / "toola-gates" / ".zcode-plugin" / "plugin.json").exists())
        self.assertFalse((mkt / "toola-gates" / "hooks" / "read-gate.js").exists())
        self.assertFalse(fake.installed)

    def test_zcode_legacy_residue_reported_not_deleted(self):
        mkt, hf, fake, cli = self._zcode_setup()
        # P22 residue: enabledPlugins["toola-gates@local"] in config.json + the
        # old cache dir <profile>/cli/plugins/cache/toola-gates. The fake
        # profile root here is self.root (hooks_dir = <root>/toola-marketplace/
        # toola-gates/hooks -> old cache = <root>/cli/plugins/cache/toola-gates).
        hf.write_text(json.dumps({"plugins": {"enabledPlugins": {"toola-gates@local": True}}}), encoding="utf-8")
        old_cache = self.root / "cli" / "plugins" / "cache" / "toola-gates"
        old_cache.mkdir(parents=True)
        ag = self._zcode_agent(mkt, hf, cli=cli)
        r = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r["ok"], r)
        residue = r.get("legacy_residue") or []
        self.assertTrue(any("toola-gates@local" in x for x in residue), residue)
        self.assertTrue(any(str(old_cache) in x for x in residue), residue)
        # reported, NOT deleted
        self.assertTrue(old_cache.is_dir())
        data = json.loads(hf.read_text(encoding="utf-8"))
        self.assertTrue(data["plugins"]["enabledPlugins"].get("toola-gates@local"))

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
        # outside_deny holds whether [A] is up (preflight answers
        # deny_outside_project_mutate) or down (the gate fail-closes).
        self.assertTrue(probe["outside_deny"], probe)
        # memory_allow is the ALLOW leg: it needs [A] up to classify the agent's
        # own auto-memory tree. A down daemon fail-closes the write gate, which
        # is the gate's policy, not a probe defect -- skip only that leg.
        import urllib.request as _ur
        try:
            _ur.urlopen("http://localhost:8083/health", timeout=3)
        except Exception:
            self.skipTest("[A] daemon not reachable; memory_allow needs preflight")
        self.assertTrue(probe["memory_allow"], probe)

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
              "hooks_dir": str(plugins), "hook_file": str(hf), "hook_trust_step": "none",
              "rule_file": _registered_rule_file()}
        r = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["coverage"], "full", r)
        probe = r["probe"]
        self.assertTrue(probe["read_deny"], probe)
        self.assertTrue(probe["read_allow"], probe)
        self.assertTrue(probe["write_deny"], probe)
        self.assertTrue(probe["outside_deny"], probe)
        self.assertTrue(probe["memory_allow"], probe)




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
              "rule_file": _registered_rule_file(),
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

    # ---------- registration dedup + verification (leader P22d) ----------
    def test_codex_merge_registers_two_groups_sharing_one_command(self):
        # RED on HEAD: both codex groups use the SAME adapter command
        # (apply_patch + shell|...), and the old command-only dedup dropped the
        # second group -- measured live: .codex hooks.json carried only
        # apply_patch, Codex read-gating OFF.
        entries = code_tools._ma_hook_entries(
            {"id": "codo", "hook_format": "codex-hooks-json",
             "hooks_dir": "C:/x/.codex/hooks"})
        data = {}
        _d, changed = code_tools._ma_hook_merge_codex(data, entries)
        self.assertTrue(changed)
        pre = data["hooks"]["PreToolUse"]
        matchers = [g.get("matcher") for g in pre]
        self.assertIn("apply_patch", matchers)
        self.assertIn("shell|local_shell|unified_exec|exec", matchers)
        # a second merge adds nothing (idempotent)
        _d2, changed2 = code_tools._ma_hook_merge_codex(data, entries)
        self.assertFalse(changed2)
        # and both entries are still there exactly once
        cmds = [(g.get("matcher"), h["command"]) for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(len(cmds), 2)

    def test_claude_merge_dedup_key_is_matcher_and_command(self):
        # Same defect class: one command under two matchers must register twice.
        entries = {"PreToolUse": [
            {"matcher": "Write", "hooks": [{"type": "command", "command": "node gate.js"}]},
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "node gate.js"}]},
        ]}
        data = {}
        code_tools._ma_hook_merge_claude(data, entries)
        matchers = sorted(g.get("matcher") for g in data["hooks"]["PreToolUse"])
        self.assertEqual(matchers, ["Edit", "Write"])

    def test_registration_verifier_flags_a_missing_entry(self):
        # Build a hook_file that carries only the write group (the live defect),
        # then verify against the full expected entries: registered_ok False and
        # the shell group named in missing_registrations.
        hd = self.root / "h_verify"
        hf = self.root / "hooks.json"
        target = {"id": "codo", "hook_format": "codex-hooks-json",
                  "hooks_dir": str(hd), "hook_file": str(hf)}
        entries = code_tools._ma_hook_entries(target)
        broken = {"hooks": {"PreToolUse": [
            {"matcher": "apply_patch",
             "hooks": [{"type": "command",
                        "command": "node 'C:/x/.codex/hooks/codex-adapter.js'"}]},
        ]}}
        # make the command match the entries' command so only the matcher group
        # is missing
        adapter_cmd = entries["PreToolUse"][0]["hooks"][0]["command"]
        broken["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = adapter_cmd
        hf.write_text(json.dumps(broken), encoding="utf-8")
        check = code_tools._ma_hook_verify_registration(
            "codex-hooks-json", hf, entries, "codex-adapter.js", hd)
        self.assertFalse(check["registered_ok"], check)
        self.assertTrue(any("shell|local_shell" in m for m in check["missing_registrations"]), check)
        # full registration verifies clean
        good = {}
        code_tools._ma_hook_merge_codex(good, entries)
        hf.write_text(json.dumps(good), encoding="utf-8")
        check2 = code_tools._ma_hook_verify_registration(
            "codex-hooks-json", hf, entries, "codex-adapter.js", hd)
        self.assertTrue(check2["registered_ok"], check2)

    def test_sync_hook_reports_registered_ok_false_on_a_partial_host_config(self):
        # End to end: a codex target whose hook_file lost the read group must
        # come back coverage=partial with registered_ok False, never pending_trust.
        hd = self.root / "h_partial"
        hf = self.root / "hooks.json"
        ag = {"id": "codo", "hook_format": "codex-hooks-json",
              "hooks_dir": str(hd), "hook_file": str(hf),
              "hook_trust_step": "codex-hooks-command",
              "rule_file": _registered_rule_file()}
        r1 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        self.assertTrue(r1["registered_ok"], r1)
        # simulate the old-defect host state: strip the shell group, then re-run
        data = json.loads(hf.read_text(encoding="utf-8"))
        data["hooks"]["PreToolUse"] = [g for g in data["hooks"]["PreToolUse"]
                                       if g.get("matcher") == "apply_patch"]
        hf.write_text(json.dumps(data), encoding="utf-8")
        r2 = code_tools._ma_write_hook(ag, {}, dry_run=False)
        # the merge must RE-ADD the dropped group (idempotent repair), so after
        # this run registration is whole again
        self.assertTrue(r2["registered_ok"], r2)
        self.assertEqual(r2["coverage"], "pending_trust", r2)


class TestSyncHookStaysOffTheEventLoop(unittest.TestCase):
    """P22b: manage_agent is async def, but the sync_hook branch spawns ~4 node
    gate probes per target through BLOCKING subprocess.run. Before the fix that
    branch never awaited, so a live sync_hook starved the daemon event loop for
    its whole run -- measured on daemon pid 35244: the write gate's
    /api/native_preflight calls made during sync_hook were only ACCEPTED after
    manage_agent returned, the preflight timed out, and the probe's allow leg
    fail-closed. The fix wraps _ma_write_hook in asyncio.to_thread. This test
    drives manage_agent through the same shape the dispatcher uses (a coroutine
    on a running loop) and requires a concurrent loop task to keep getting
    scheduled well inside the gate's 4s preflight timeout."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.root = root
        repo_hooks = root / "repo" / "hooks"
        (repo_hooks / "adapters").mkdir(parents=True)
        real_hooks = Path(code_tools.__file__).parent / "hooks"
        for gate in ("native-write-gate.js", "read-gate.js"):
            shutil.copyfile(real_hooks / gate, repo_hooks / gate)
        patcher = mock.patch.object(code_tools, "_ma_hooks_repo_dir", return_value=repo_hooks)
        patcher.start()
        self.addCleanup(patcher.stop)
        # a fake registry with one claude-settings agent rooted in tmp
        self.reg = {"agents": {"probeagent": {
            "hook_format": "claude-settings",
            "hooks_dir": str(root / "h_probe"),
            "hook_file": str(root / "settings.json"),
            "hook_trust_step": "none",
        }}}
        reg_patcher = mock.patch.object(code_tools, "_load_agents_registry", return_value=self.reg)
        reg_patcher.start()
        self.addCleanup(reg_patcher.stop)

    def test_event_loop_keeps_turning_during_sync_hook(self):
        import asyncio
        beats = []

        async def heartbeat():
            # scheduled every 50ms; records the gap between beats
            last = asyncio.get_event_loop().time()
            while True:
                await asyncio.sleep(0.05)
                now = asyncio.get_event_loop().time()
                beats.append(now - last)
                last = now

        async def main():
            hb = asyncio.ensure_future(heartbeat())
            try:
                await code_tools.manage_agent(action="sync_hook", target="probeagent", dry_run=False)
            finally:
                hb.cancel()
                try:
                    await hb
                except asyncio.CancelledError:
                    pass

        asyncio.run(main())
        self.assertGreaterEqual(len(beats), 2, "the loop never turned during sync_hook")
        worst = max(beats)
        # the write gate's preflight timeout is 4s; a healthy loop must beat far
        # inside that. 1s is the guard the leader asked for.
        self.assertLess(worst, 1.0,
                        f"event loop starved {worst:.2f}s during sync_hook; "
                        f"the write gate preflight would time out (4s) -> the probe's "
                        f"memory_allow leg fail-closes")


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

    def test_outside_project_apply_patch_denies(self):
        # Policy change 2026-10-05: the write gate RELAYS the backend verdict
        # instead of stepping aside outside a project, and the Matrix answers
        # deny_outside_project_mutate there (write through [A]: write_new_file
        # takes an absolute off-root path). So an apply_patch whose target is
        # outside every project must be DENIED -- and a down daemon fail-closes
        # to the same answer, so the assertion needs no daemon.
        real_gate = Path(code_tools.__file__).parent / "hooks" / "native-write-gate.js"
        shutil.copyfile(real_gate, self.adir / "native-write-gate.js")
        self._read_gate("process.exit(0);\n")
        target = self.work / "outside.txt"
        patch = f"*** Begin Patch\n*** Update File: {target}\n@@\n-a\n+b\n*** End Patch\n"
        rc, out = self._run({"tool_name": "apply_patch", "tool_input": {"patch": patch},
                             "cwd": str(self.work)})
        self.assertTrue(self._denied(out), f"rc={rc} out={out[:300]}")
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
