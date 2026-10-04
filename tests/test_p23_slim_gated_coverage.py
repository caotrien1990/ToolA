"""P23 (2026-10-04): 'slim_gated' drops the native read/write HOW-TO prose
because the local PreToolUse hooks (read-gate R1-R3 + native-write-gate) now
enforce it mechanically and name the [A] replacement in the deny reason. That
removal is only safe while every registry rule target rendered from
'slim_gated' belongs to an agent that ALSO declares a hook target with
hook_format != 'none' whose hooks_dir sits in the SAME harness home as the
rule file (a .claude-ltnai rule <-> .claude-ltnai hooks). This test is the
safety rail: it fails the moment a registry row points at the gated template
without hook coverage, and the moment an expected gated agent silently reverts
to the full slim template. Static check over agents_registry.json -- no
network, no daemon, no hook execution.
"""
import unittest
from pathlib import Path

import code_tools


def _load_registry() -> dict:
    from orchestrator.core.ca_policy_matrix import load_agents_registry_file
    return load_agents_registry_file(code_tools._AGENTS_REGISTRY_PATH)


def _norm(p: str) -> str:
    return str(p or "").replace("\\", "/").rstrip("/").lower()


def _rule_targets(ag: dict) -> list:
    """Mirror of code_tools._ma_rule_agent_targets, registry-side: rule_targets
    (list) wins when present and non-empty; flat rule_file/rule_template is the
    single-target shorthand."""
    raw = ag.get("rule_targets")
    if isinstance(raw, list) and raw:
        return [t for t in raw if isinstance(t, dict)]
    return [{"rule_file": ag.get("rule_file") or "",
             "rule_template": ag.get("rule_template") or "slim"}]


def _hook_targets(ag: dict) -> list:
    """Mirror of code_tools._ma_hook_agent_targets, registry-side."""
    raw = ag.get("hook_targets")
    if isinstance(raw, list) and raw:
        return [t for t in raw if isinstance(t, dict)]
    return [{"hook_format": ag.get("hook_format") or "none",
             "hooks_dir": ag.get("hooks_dir") or ""}]


def _rule_home(rule_file: str) -> str:
    # Harness home = the directory holding the rule file (.claude-ltnai, ...).
    return _norm(str(Path(rule_file).parent))


def _hooks_cover_home(hooks_dir: str, home: str) -> bool:
    hd = _norm(hooks_dir)
    if not hd or not home:
        return False
    # Same home directly (home/hooks) or anywhere under it (zcode keeps its
    # hooks deep under the harness home). The trailing slash keeps .claude and
    # .claude-ltnai distinct: '.claude-ltnai/...'.startswith('.claude/') is False.
    return hd == home or hd.startswith(home + "/")


class SlimGatedAlwaysHasHookCoverage(unittest.TestCase):
    def test_every_slim_gated_rule_target_has_a_hook_in_the_same_home(self):
        reg = _load_registry()
        failures = []
        for aid, ag in (reg.get("agents") or {}).items():
            live_hooks = [
                h for h in _hook_targets(ag)
                if (h.get("hook_format") or "none").strip().lower() not in ("", "none")
                and (h.get("hooks_dir") or "").strip()
            ]
            for t in _rule_targets(ag):
                if (t.get("rule_template") or "").strip().lower() != "slim_gated":
                    continue
                rf = (t.get("rule_file") or "").strip()
                if not rf:
                    failures.append((aid, t, "slim_gated target with an empty rule_file"))
                    continue
                home = _rule_home(rf)
                if not any(_hooks_cover_home(h.get("hooks_dir"), home) for h in live_hooks):
                    failures.append((
                        aid, t,
                        "slim_gated without a hook target (hook_format != none) "
                        "in the same harness home -- the gated rule drops prose "
                        "the hooks must enforce"))
        self.assertEqual(failures, [])

    def test_expected_gated_agents_and_targets_stay_gated(self):
        reg = _load_registry()
        agents = reg.get("agents") or {}
        self.assertEqual((agents.get("clau") or {}).get("rule_template"), "slim_gated")
        self.assertEqual((agents.get("claultnai") or {}).get("rule_template"), "slim_gated")
        codo = agents.get("codo") or {}
        by_file = {_norm(t.get("rule_file")): (t.get("rule_template") or "").strip().lower()
                   for t in _rule_targets(codo)}
        self.assertEqual(by_file.get(_norm("C:/Users/ADMIN/.codex-ltnai/AGENTS.md")),
                         "slim_gated")
        # Anh 2026-10-04 scope correction: BOTH codo rule targets are gated;
        # .codex hook coverage comes from codo's .codex hook target.
        self.assertEqual((codo.get("rule_template") or "").strip().lower(), "slim_gated")
        self.assertEqual(by_file.get(_norm("C:/Users/ADMIN/.codex/AGENTS.md")), "slim_gated")

    def test_gated_template_renders_from_its_own_sot(self):
        body = code_tools._ma_rule_body("slim_gated")
        self.assertIn("the deny reason names the [A] replacement", body)
        meta = code_tools._ma_rule_source_metadata("slim_gated")
        self.assertEqual(meta["rule_source"], "SLIM_RULE_GATED.txt")
        self.assertEqual(meta["rule_source_sha256"], code_tools._sha256_text(body))

    def test_rule_targets_iteration_renders_every_target(self):
        """The sync_rule dispatch must render BOTH codo targets, each with its
        own template (flat fields stay the single-target shorthand for agents
        without rule_targets)."""
        ag = {
            "id": "codo",
            "persona": 'Always refer to yourself as "Codo" and address the user as "Anh".',
            "rule_file": "C:/Users/ADMIN/.codex/AGENTS.md",
            "rule_template": "slim",
            "rule_format": "markdown",
            "rule_targets": [
                {"rule_file": "C:/Users/ADMIN/.codex/AGENTS.md", "rule_template": "slim"},
                {"rule_file": "C:/Users/ADMIN/.codex-ltnai/AGENTS.md", "rule_template": "slim_gated"},
            ],
        }
        targets = code_tools._ma_rule_agent_targets(ag)
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0]["rule_template"], "slim")
        self.assertEqual(targets[1]["rule_template"], "slim_gated")
        self.assertNotIn("rule_targets", targets[0])
        # Flat shorthand: an agent without rule_targets yields exactly itself.
        flat = {"id": "clau", "rule_file": "x", "rule_template": "slim_gated"}
        self.assertEqual(code_tools._ma_rule_agent_targets(flat), [flat])


if __name__ == "__main__":
    unittest.main()
