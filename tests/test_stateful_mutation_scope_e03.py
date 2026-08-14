"""E-03 regression: workflow/mission stateful tools must be marked stateful
in metadata (mutation_scope='stateful'), while pure-read workflow/mission
tools remain at scope='none'.

Why: prior _MUTATION_SCOPE_MAP had no entries for workflow_* / mission_*,
so tool_mutation_scope returned 'none' for ALL of them and the published
schema showed mutating:false + mutation_scope:'none' even for tools like
workflow_start (writes workflow_state/<slug>.state.json) and mission_create
(writes missions.db, dispatches work). Read-caller tooling that relied on
mutation_scope='none' as an idempotence signal was mis-led. The fix adds a
'stateful' scope value, tags the writer tools, and surfaces a
`stateful:true` boolean on the schema. Pure reads (workflow_status,
workflow_list, mission_status, mission_next, ...) stay at scope='none'.
"""
from __future__ import annotations

import unittest

import code_tools as ct


STATEFUL_WORKFLOW_WRITERS = (
    "workflow_activate", "workflow_deactivate", "workflow_create_from_ui",
    "workflow_draft_create", "workflow_goto_step", "workflow_reset",
    "workflow_retry_step", "workflow_save_md", "workflow_skip_step",
    "workflow_start", "workflow_submit_step",
)
STATEFUL_MISSION_WRITERS = (
    "mission_create", "mission_dispatch", "mission_gate", "mission_report",
)
PURE_READ_WORKFLOW = (
    "workflow_active_get", "workflow_get_current", "workflow_list",
    "workflow_read_md", "workflow_status", "workflow_status_registry",
    "workflow_draft_get", "workflow_checkpoint",
)
PURE_READ_MISSION = ("mission_next", "mission_status")


class TestStatefulMutationScope(unittest.TestCase):
    def test_workflow_writers_are_stateful(self) -> None:
        for name in STATEFUL_WORKFLOW_WRITERS:
            with self.subTest(tool=name):
                self.assertEqual(
                    ct.tool_mutation_scope(name), "stateful",
                    f"{name} must be tagged mutation_scope='stateful'",
                )

    def test_mission_writers_are_stateful(self) -> None:
        for name in STATEFUL_MISSION_WRITERS:
            with self.subTest(tool=name):
                self.assertEqual(
                    ct.tool_mutation_scope(name), "stateful",
                    f"{name} must be tagged mutation_scope='stateful'",
                )

    def test_pure_read_workflow_tools_stay_scope_none(self) -> None:
        for name in PURE_READ_WORKFLOW:
            with self.subTest(tool=name):
                self.assertEqual(
                    ct.tool_mutation_scope(name), "none",
                    f"{name} is a pure read and must stay scope='none'",
                )

    def test_pure_read_mission_tools_stay_scope_none(self) -> None:
        for name in PURE_READ_MISSION:
            with self.subTest(tool=name):
                self.assertEqual(
                    ct.tool_mutation_scope(name), "none",
                    f"{name} is a pure read and must stay scope='none'",
                )

    def test_stateful_tools_not_promoted_to_protected(self) -> None:
        """E-03 is metadata-only. The destructive origin/session/token gates
        are owned by PROTECTED_TOOL_NAMES / TOKEN_REQUIRED_TOOLS; we do NOT
        silently escalate workflow/mission writers into those gates, which
        would have broken existing CA flows that call them without a token."""
        for name in STATEFUL_WORKFLOW_WRITERS + STATEFUL_MISSION_WRITERS:
            with self.subTest(tool=name):
                self.assertNotIn(
                    name, ct.TOKEN_REQUIRED_TOOLS,
                    f"{name} must not become token-required by E-03",
                )

    def test_every_stateful_tool_is_registered(self) -> None:
        """Guard against typos in _MUTATION_SCOPE_MAP: every entry tagged
        'stateful' must correspond to a real registered tool."""
        for name, scope in ct._MUTATION_SCOPE_MAP.items():
            if scope == "stateful":
                with self.subTest(tool=name):
                    self.assertIn(
                        name, ct.TOOL_MAP,
                        f"mutation_scope='stateful' declared for unknown tool {name}",
                    )


if __name__ == "__main__":
    unittest.main()
