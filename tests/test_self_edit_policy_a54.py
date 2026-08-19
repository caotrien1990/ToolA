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
