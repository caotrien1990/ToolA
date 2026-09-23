"""A54 — [A] self-edit must classify as upgrade_orchestrator, not write_existing_file.

Regression for the policy conflict: the standing rule says "editing [A] itself:
native edit + log_native_event", but the tool/action map pinned any native write
to write_existing_file (native_policy=forbid), so log_native_event answered
ok:false / is_forbidden_native:true for the very edits the rule mandates.
The Matrix row upgrade_orchestrator=hybrid_upgrade_only already allowed it; only
the classifier ordering was wrong.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.core import ca_policy_matrix as cpm
from orchestrator.core.ca_policy_matrix import (
    classify_action,
    governed_project_root,
    is_agent_memory_target,
    is_orchestrator_self_target,
    target_scope,
)

A_ROOT = Path(__file__).resolve().parents[1]
OTHER_ROOT = Path("D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/21. ToolQ")


@pytest.mark.parametrize(
    "rel",
    ["orchestrator/session/store.py", "code_tools.py", "orchestrator_server.py"],
)
def test_native_self_edit_is_upgrade_orchestrator(rel):
    r = classify_action(
        tool_name="Edit",
        action_type="write",
        target=str(A_ROOT / rel),
        project_path=str(A_ROOT),
        transport="native",
        agent_id="clau",
    )
    assert r["task_class"] == "upgrade_orchestrator"
    assert r["native_policy"] == "hybrid_upgrade_only"
    assert r["is_forbidden_native"] is False
    assert r["ca_next_action"] != "stop_native_use_a_tool"


def test_native_self_read_still_prefers_a_tool():
    """The self-edit relaxation is for MUTATIONS only: a read of [A] stays
    read_project_file, never upgrade_orchestrator, and read_file_range stays its
    preferred tool.

    Its native_policy is allow_readonly since 2026-09-23 -- the slim rule's Read
    Routing (measured 2026-08-19) allows native read for pure inspection, and the
    Matrix still said forbid, so every inspection read logged as a bypass. What
    stays governed is a read that feeds an EDIT: only read_file_range seeds the
    write_fix read_token, and write_fix enforces that, not this label.
    """
    r = classify_action(
        tool_name="Read",
        action_type="read",
        target=str(A_ROOT / "code_tools.py"),
        project_path=str(A_ROOT),
        transport="native",
        agent_id="clau",
    )
    assert r["task_class"] == "read_project_file"
    assert r["native_policy"] == "allow_readonly"
    assert r["is_forbidden_native"] is False
    assert r["host_gate"]["scope"] == "project"
    assert "read_file_range" in r["preferred_via_a"]


def test_other_project_native_write_still_forbidden():
    """The relaxation is scoped to [A]'s own tree only."""
    r = classify_action(
        tool_name="Edit",
        action_type="write",
        target=str(OTHER_ROOT / "q_agent" / "provider.py"),
        project_path=str(OTHER_ROOT),
        transport="native",
        agent_id="clau",
    )
    assert r["task_class"] == "write_existing_file"
    assert r["is_forbidden_native"] is True


def test_via_a_self_write_unchanged():
    """via_a keeps the ordinary governed write path — no reclassification."""
    r = classify_action(
        tool_name="write_fix",
        action_type="write",
        target=str(A_ROOT / "code_tools.py"),
        project_path=str(A_ROOT),
        transport="via_a",
        agent_id="clau",
    )
    assert r["task_class"] == "write_existing_file"
    assert r["is_forbidden_native"] is False


@pytest.mark.parametrize(
    "target,expected",
    [
        (str(A_ROOT / "code_tools.py"), True),
        (str(A_ROOT / "orchestrator" / "session" / "store.py"), True),
        (str(OTHER_ROOT / "q_agent" / "provider.py"), False),
        ("code_tools.py", False),  # relative: cannot prove it is [A]'s tree
        ("", False),
    ],
)
def test_is_orchestrator_self_target(target, expected):
    assert is_orchestrator_self_target(target) is expected
# --- W5: the self-edit verdict must not depend on WHICH tool spelled the write ---
#
# `_TOOL_TO_CLASS` maps a shell runner to project_command by tool NAME, and that
# mapping used to win before the self-edit branch was ever consulted. Measured
# on orchestrator/graph/store.py: Edit/write -> upgrade_orchestrator (ok:true)
# while Bash/write -> project_command / forbid -> HOST_GATE_DENY. Identical
# mutation, identical target, opposite verdict.

_SHELL_TOOLS = ["Bash", "run_ps1_script", "run_node_script", "run_project_command"]


@pytest.mark.parametrize("tool", _SHELL_TOOLS)
@pytest.mark.parametrize("action", ["write", "edit", "patch", "write_existing_file"])
def test_shell_tool_self_edit_matches_edit_tool(tool, action):
    """A file mutation inside [A] is upgrade_orchestrator whatever ran it."""
    r = classify_action(
        tool_name=tool,
        action_type=action,
        target=str(A_ROOT / "orchestrator" / "graph" / "store.py"),
        project_path=str(A_ROOT),
        transport="native",
        agent_id="clau",
    )
    assert r["task_class"] == "upgrade_orchestrator"
    assert r["native_policy"] == "hybrid_upgrade_only"
    assert r["is_forbidden_native"] is False
    assert r["ca_next_action"] != "stop_native_use_a_tool"


@pytest.mark.parametrize("action", ["command", "execute", "test", "read"])
def test_running_a_real_command_inside_A_stays_project_command(action):
    """The yield is scoped to MUTATING actions. Running a command inside [A]'s
    tree is still a command and still has to go through an [A] tool -- this is
    the boundary that keeps the fix from becoming a blanket shell exemption."""
    r = classify_action(
        tool_name="Bash",
        action_type=action,
        target=str(A_ROOT / "orchestrator" / "graph" / "store.py"),
        project_path=str(A_ROOT),
        transport="native",
        agent_id="clau",
    )
    assert r["task_class"] == "project_command"
    assert r["is_forbidden_native"] is True


@pytest.mark.parametrize("tool", _SHELL_TOOLS)
def test_shell_tool_write_outside_A_still_forbidden(tool):
    """Scoped to [A]'s own tree, exactly like the Edit-tool path."""
    r = classify_action(
        tool_name=tool,
        action_type="write",
        target=str(OTHER_ROOT / "q_agent" / "provider.py"),
        project_path=str(OTHER_ROOT),
        transport="native",
        agent_id="clau",
    )
    assert r["task_class"] == "project_command"
    assert r["is_forbidden_native"] is True


# --- 2026-09-23: ONE scope matcher (target_scope) for classify_action + host gate ---
#
# Three preflight defects, one cause: "is this target inside a project?" was
# answered twice, differently, and an empty project_path made EVERY target inside.
#   1. %TEMP%/x.txt with no project_path answered scope=project / deny.
#   2. A native write to the agent's own harness memory was refused through both
#      entry points, so a PreToolUse hook built on the preflight would block the
#      memory the harness MANDATES be written natively.
#   3. A native Read inside a project answered FORBIDDEN_NATIVE (fixed above in
#      test_native_self_read_still_prefers_a_tool).
# The hook sends NO project_path and denies on host_gate.scope == "project" + not
# allowed, so these pin the scope verdict and the allow/deny the hook reads.


def _hook_shaped(tool, action, target, project_path=""):
    return classify_action(
        tool_name=tool,
        action_type=action,
        target=str(target),
        project_path=str(project_path) if project_path else "",
        transport="native",
        agent_id="claultnai",
    )


@pytest.fixture
def bare_dir(tmp_path):
    """A directory under no project -- the shape of %TEMP%."""
    d = tmp_path / "scratch"
    d.mkdir()
    assert governed_project_root(str(d / "x.txt")) is None, (
        "tmp_path itself sits under a project marker; this fixture cannot stand "
        "in for %TEMP%"
    )
    return d


@pytest.fixture
def fake_agent_home(tmp_path, monkeypatch):
    """An agent registry whose rule_file lives in tmp_path/agenthome."""
    home = tmp_path / "agenthome"
    (home / "projects" / "slug" / "memory").mkdir(parents=True)
    reg = tmp_path / "agents_registry.json"
    reg.write_text(
        json.dumps({"agents": {"x": {"rule_file": str(home / "CLAUDE.md")}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cpm, "_AGENT_REGISTRY_PATH", reg)
    return home


def test_undeclared_target_under_no_project_is_outside(bare_dir):
    """Defect 1. A write there is not governed by [A], so the hook must let it
    through: scope=outside_project. The outside gate still refuses an unapproved
    native mutation -- that is the outside rule, not a project deny."""
    r = _hook_shaped("Write", "write", bare_dir / "x.txt")
    assert r["host_gate"]["scope"] == "outside_project"
    assert r["host_gate"]["decision"] == "deny_outside_project_mutate"


def test_undeclared_read_under_no_project_is_readonly_probe(bare_dir):
    r = _hook_shaped("Read", "read", bare_dir / "x.txt")
    assert r["task_class"] == "outside_project_os_read"
    assert r["host_gate"]["allowed"] is True


def test_orchestrator_cache_alone_does_not_make_a_project(tmp_path):
    """[A] writes .orchestrator_cache wherever it operates (measured in %TEMP%,
    the memory dir, the agent home, the app parent folder). Counting it as a
    project marker would turn all of those back into scope=project."""
    d = tmp_path / "touched_by_a"
    (d / ".orchestrator_cache").mkdir(parents=True)
    assert governed_project_root(str(d / "f.txt")) is None
    assert target_scope(target=str(d / "f.txt")) == "outside_project"


def test_real_marker_makes_a_project_even_undeclared(tmp_path):
    """The target's OWN root decides when nothing is declared, and a new file
    that does not exist yet is still inside its project."""
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    target = proj / "pkg" / "new_module.py"
    assert governed_project_root(str(target)) == proj.resolve()
    r = _hook_shaped("Write", "write", target)
    assert r["host_gate"]["scope"] == "project"
    assert r["host_gate"]["allowed"] is False
    assert r["error_name"] == "HOST_GATE_DENY"


def test_declared_project_path_covers_a_not_yet_existing_file(tmp_path):
    proj = tmp_path / "declared_only"
    proj.mkdir()
    target = proj / "sub" / "brand_new.py"
    assert target_scope(target=str(target), project_path=str(proj)) == "project"


def test_write_into_a_different_project_than_declared_is_still_governed():
    """Declaring [A] as project_path must not make a ToolQ file outside."""
    if not (OTHER_ROOT / ".git").exists():
        pytest.skip("ToolQ checkout not present on this machine")
    r = _hook_shaped("Edit", "write", OTHER_ROOT / "q_agent" / "provider.py", A_ROOT)
    assert r["host_gate"]["scope"] == "project"
    assert r["is_forbidden_native"] is True


@pytest.mark.parametrize("target", ["", "relative/file.py"])
def test_unprovable_target_defaults_to_governed(target):
    """Governance is the default: what cannot be located is treated as inside."""
    assert target_scope(target=target) == "project"


def test_self_edit_is_upgrade_orchestrator_without_declared_project():
    """The hook shape: [A]'s own tree keeps its authorized native path even
    though no project_path is sent."""
    r = _hook_shaped("Edit", "write", A_ROOT / "code_tools.py")
    assert r["task_class"] == "upgrade_orchestrator"
    assert r["host_gate"]["allowed"] is True


def test_agent_memory_write_is_allowed_native(fake_agent_home):
    """Defect 2."""
    target = fake_agent_home / "projects" / "slug" / "memory" / "note.md"
    assert is_agent_memory_target(str(target)) is True
    r = _hook_shaped("Write", "write", target)
    assert r["task_class"] == "agent_memory_write"
    assert r["native_policy"] == "allow_native"
    assert r["host_gate"]["decision"] == "allow_native"
    assert r["host_gate"]["allowed"] is True
    assert r["error_name"] is None


def test_memory_exception_is_scoped_to_the_memory_dir(fake_agent_home):
    """A sibling of memory/ in the same agent home is NOT memory."""
    target = fake_agent_home / "projects" / "slug" / "transcript.jsonl"
    assert is_agent_memory_target(str(target)) is False
    r = _hook_shaped("Write", "write", target)
    assert r["task_class"] != "agent_memory_write"
    assert r["host_gate"]["allowed"] is False


def test_home_level_rule_file_does_not_make_home_projects_memory(tmp_path, monkeypatch):
    """One registered agent keeps its rule file directly in HOME. Its dirname is
    HOME, so without the guard HOME/projects/<x>/memory would be exempt."""
    reg = tmp_path / "agents_registry.json"
    reg.write_text(
        json.dumps({"agents": {"h": {"rule_file": str(Path.home() / ".AGENTS.md")}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cpm, "_AGENT_REGISTRY_PATH", reg)
    target = Path.home() / "projects" / "x" / "memory" / "a.md"
    assert is_agent_memory_target(str(target)) is False


def test_memory_read_stays_a_plain_readonly_probe(fake_agent_home):
    target = fake_agent_home / "projects" / "slug" / "memory" / "note.md"
    r = _hook_shaped("Read", "read", target)
    assert r["task_class"] == "outside_project_os_read"
    assert r["host_gate"]["allowed"] is True


@pytest.mark.parametrize("tool", ["MultiEdit", "NotebookEdit"])
def test_multiedit_and_notebookedit_are_file_writes(tool):
    """The host's other two mutation tools. Unmapped, they fell to `unknown`:
    the same forbid by accident, with no audit class to log under."""
    r = _hook_shaped(tool, "write", OTHER_ROOT / "q_agent" / "provider.py", OTHER_ROOT)
    assert r["task_class"] == "write_existing_file"
    assert r["host_gate"]["allowed"] is False
