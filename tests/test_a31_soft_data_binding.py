"""A31-Q1: the enforcement that was missing -- a test that binds a file ON DISK to
the RUNTIME that file claims to describe.

Every soft-data drift the A31 survey found has one root cause: nothing fails when
a committed artifact stops matching the registry it documents. The tool inventory
sat 3 tools behind for three campaigns and the suite stayed green. `docs/
ALIAS_CONTRACT.generated.*` sat 8 aliases and 1 tool behind for the same reason --
and its own test could not catch that, because it regenerates into `tmp_path` and
compares the temp copy against the runtime. That verifies the GENERATOR. It never
reads the file anybody actually opens.

So these tests deliberately read the real paths in the working tree. That makes
them the only tests in the suite that can fail because of something nobody edited
in Python. When one does fail, regenerate the artifact -- do not relax the assert,
which is exactly how the drift got to three campaigns deep.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import code_tools as ct  # noqa: E402
from scripts.gen_alias_contract import (  # noqa: E402
    JSON_NAME,
    MD_NAME,
    _alias_count,
    _load_runtime_aliases,
    _markdown,
)

_DOCS = _ROOT / "docs"


def _drift_report(drift: list) -> str:
    """Name the offenders in the failure message.

    A bare `3 != 0` sends the reader back to the audit to find out which three,
    which is the part that costs time.
    """
    if not drift:
        return ""
    lines = []
    for row in drift:
        subject = row.get("tool") or row.get("skill") or row.get("file") or "?"
        lines.append("  {0} [{1}] {2}".format(row.get("type"), row.get("severity"), subject))
    return "\n" + "\n".join(sorted(lines))


class InventoryMatchesTheRuntimeTest(unittest.TestCase):
    """`.ToolA_Skill/references/tool_surface_current.md` is the inventory SOT.

    The audit at `code_tools._audit_tool_matrix_sync` already COMPUTED this parity
    on every `open_session`; the only thing missing was something that fails on it.
    Calling it unmocked is the whole point -- a fake registry would pass forever.
    """

    @classmethod
    def setUpClass(cls):
        # persist=False: an assertion must never append a row to matrix_log.jsonl.
        # The audit self-lifts `orchestrator_server` when absent, so the late
        # register_tool callsites are counted here the same as in the daemon.
        cls.audit = ct._audit_tool_matrix_sync(project_path=str(_ROOT), persist=False)

    def test_the_audit_ran_and_wrote_nothing(self):
        self.assertTrue(self.audit.get("ok"), self.audit.get("error"))
        self.assertIs(self.audit["persisted"], False)

    def test_the_schema_registry_matches_the_tool_map(self):
        s = self.audit["summary"]
        self.assertEqual(s["schema_tools"], s["code_tools"])

    def test_the_inventory_covers_every_live_tool(self):
        s = self.audit["summary"]
        self.assertEqual(
            s["inventory_active_count"], s["code_tools"],
            "tool_surface_current.md lists {0} of the {1} live tools; run "
            "`python scripts/sync_tool_matrix.py --generate`".format(
                s["inventory_active_count"], s["code_tools"]),
        )

    def test_the_inventory_lists_nothing_that_is_not_a_tool(self):
        # The inventory is harvested with a backtick regex, so any prose that slips
        # into the generated file arrives here as a phantom tool.
        s = self.audit["summary"]
        self.assertEqual(s["inventory_extra_count"], 0)
        self.assertEqual(s["inventory_retired_count"], 0)

    def test_no_drift_of_any_type_is_tolerated(self):
        """No drift of any severity is tolerated -- the backlog is closed.

        Until 2026-09-17 this test pinned seven `skill_reference_missing` rows
        by identity, because they named four documents somebody wanted written
        and deleting the links would have erased the only record of that. All
        four are now resolved, so the allowance is gone and `low` must be as
        empty as `high` and `medium`: two documents were written
        (`.ToolA_Skill/references/high_il_restart_pitfalls.md` and
        `cross-protocol-tool-replay.md`), and the four lines pointing at the
        two documents that must NOT exist -- a policy matrix has exactly one
        source of truth, `.council/CA_TOOL_POLICY_MATRIX.json`, echoed live by
        `open_session.cslt_policy` -- were repointed at that SOT instead.
        A new dangling `references/*.md` link therefore fails here outright,
        with no named-backlog escape hatch left to add a row to.
        """
        drift = self.audit["drift"]
        self.assertEqual(drift, [], "matrix drift:" + _drift_report(drift))

    def test_the_audit_still_declares_its_blind_spot(self):
        # drift_count == 0 says the RUNTIME is self-consistent, not that the .py on
        # disk matches the runtime. A green bar here must not be read as the latter.
        self.assertEqual(self.audit["scope"], "runtime_only")


class GeneratedAliasContractIsBoundToDiskTest(unittest.TestCase):
    """The committed `docs/ALIAS_CONTRACT.generated.*`, not a temp regeneration."""

    def setUp(self):
        self.aliases = _load_runtime_aliases()

    def test_the_committed_json_matches_the_runtime(self):
        on_disk = json.loads((_DOCS / JSON_NAME).read_text(encoding="utf-8"))
        self.assertEqual(
            on_disk, self.aliases,
            "docs/" + JSON_NAME + " is stale; run `python scripts/gen_alias_contract.py`",
        )

    def test_the_committed_markdown_matches_the_generator_byte_for_byte(self):
        on_disk = (_DOCS / MD_NAME).read_text(encoding="utf-8")
        self.assertEqual(
            on_disk, _markdown(self.aliases),
            "docs/" + MD_NAME + " is stale; run `python scripts/gen_alias_contract.py`",
        )

    def test_the_counts_in_the_markdown_prose_match_its_own_table(self):
        # Independent of the runtime: a hand-edit that adds a row without touching
        # the two summary lines is caught here even if the generator is never run.
        text = (_DOCS / MD_NAME).read_text(encoding="utf-8")
        rows = [ln for ln in text.splitlines() if ln.startswith("| `")]
        tools = {ln.split("`")[1] for ln in rows}
        self.assertIn("- Tools with aliases: {0}".format(len(tools)), text)
        self.assertIn("- Total aliases: {0}".format(len(rows)), text)
        self.assertEqual(len(rows), _alias_count(self.aliases))


if __name__ == "__main__":
    unittest.main()
