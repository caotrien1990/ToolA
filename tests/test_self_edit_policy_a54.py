"""A54 — [A] self-edit must classify as upgrade_orchestrator, not write_existing_file.

Regression for the policy conflict: the standing rule says "editing [A] itself:
native edit + log_native_event", but the tool/action map pinned any native write
to write_existing_file (native_policy=forbid), so log_native_event answered
ok:false / is_forbidden_native:true for the very edits the rule mandates.
The Matrix row upgrade_orchestrator=hybrid_upgrade_only already allowed it; only
the classifier ordering was wrong.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.core.ca_policy_matrix import (
    classify_action,
    is_orchestrator_self_target,
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
    """Reads are NOT relaxed: reading [A] through an [A] tool stays correct."""
    r = classify_action(
        tool_name="Read",
        action_type="read",
        target=str(A_ROOT / "code_tools.py"),
        project_path=str(A_ROOT),
        transport="native",
        agent_id="clau",
    )
    assert r["task_class"] == "read_project_file"
    assert r["is_forbidden_native"] is True


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
