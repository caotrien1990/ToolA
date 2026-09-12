"""campaign-1608 (A11-b): every gated tool must declare a mutation_scope.

Background. `_MUTATION_SCOPE_MAP` is what `/api/tools` reports as `mutation_scope`,
and `tool_mutation_scope()` defaults to "none" for anything absent from it. "none"
is documented as *read-only*. So a tool that is in the origin/session gate set
(PROTECTED_TOOL_NAMES | TOKEN_REQUIRED_TOOLS) but missing from the map advertises
itself to callers as read-only while actually being the most dangerous kind of
call there is.

That was exactly the state of `restart_orchestrator_via_bat`: token-required,
protected, kills and relaunches the whole daemon -- and `mutation_scope: "none"`
on the live server. Its sibling `restart_orchestrator_via_task` was correctly
tagged "process"; the two differ only in launcher.

Why nothing caught it. `rule_drift_check`'s coverage check skips names that are
not in `TOOL_MAP` (`if name not in tool_keys: continue`), and this tool is
registered from `orchestrator_server.py`, not from `code_tools.py`. Import
`code_tools` alone and the name is simply absent, so the gap reads as "not a tool"
instead of "a tool with a missing scope".

Hence this file imports `orchestrator_server` FIRST -- the same view the live
daemon has. A test that only imports `code_tools` cannot observe this class of
defect at all.
"""

import unittest

import orchestrator_server  # noqa: F401  -- registers server-side tools into ct.TOOL_MAP
import code_tools as ct


def _gate_set():
    return set(ct.PROTECTED_TOOL_NAMES) | set(ct.TOKEN_REQUIRED_TOOLS)


class GatedToolsDeclareMutationScope(unittest.TestCase):
    def test_every_gated_registered_tool_has_a_scope(self):
        """No tool may be gated as mutating yet advertise scope 'none'."""
        missing = sorted(
            name for name in _gate_set()
            if name in ct.TOOL_MAP and ct.tool_mutation_scope(name) == "none"
        )
        self.assertEqual(
            missing, [],
            "gated tools advertising mutation_scope='none' (read-only) to callers: "
            f"{missing}",
        )

    def test_restart_via_bat_is_registered_and_scoped(self):
        """Pin the specific regression, including that the name is a real tool.

        Asserting the scope alone would pass vacuously if the tool disappeared
        from TOOL_MAP, so registration is asserted first.
        """
        self.assertIn("restart_orchestrator_via_bat", ct.TOOL_MAP)
        self.assertEqual(
            ct.tool_mutation_scope("restart_orchestrator_via_bat"), "process",
        )

    def test_both_restart_paths_agree(self):
        """via_bat and via_task differ only in launcher, so scope must match."""
        self.assertEqual(
            ct.tool_mutation_scope("restart_orchestrator_via_bat"),
            ct.tool_mutation_scope("restart_orchestrator_via_task"),
        )

    def test_scope_map_has_no_phantom_entries(self):
        """Guard the other direction: a scope declared for a non-existent tool.

        This is what rule_drift_check flags 'medium ... (phantom)'. With the
        server imported, TOOL_MAP is the full surface, so any leftover here is a
        real stale entry rather than an artefact of a partial import.
        """
        phantom = sorted(n for n in ct._MUTATION_SCOPE_MAP if n not in ct.TOOL_MAP)
        self.assertEqual(phantom, [], f"mutation_scope declared for unknown tools: {phantom}")

    def test_cbm_derived_writers_are_gated_t2(self):
        for name in (
            "cbm_index", "cbm_incremental_index", "cbm_get_architecture",
            "cbm_dead_code", "cbm_test_gap", "cbm_what_if", "cbm_cycle_point",
        ):
            self.assertIn(name, ct.PROTECTED_TOOL_NAMES)
            self.assertEqual(ct.tool_mutation_scope(name), "derived_index")
            self.assertEqual(ct.TOOL_PHASES.get(name), "T2")


if __name__ == "__main__":
    unittest.main()
