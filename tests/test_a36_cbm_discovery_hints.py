"""A36: CBM-FIRST discovery hints - steer a CA to the INDEX instead of a bulk read.

ToolA is itself a large repo, and the failure it kept producing was a CA "understanding"
it by reading source a thousand lines at a time. A36 changed four surfaces so the
cheapest correct route is also the discoverable one. Each surface is pinned separately
because each is independently reachable by a CA:

  1. orchestrator/graph/testgap.py - cbm_test_gap is VERDICT-FREE. It must SAY so on
     EVERY payload shape that emits pass:false, not only the happy one: a field that
     appears sometimes is itself a trap. The value stays pinned (pass False / score
     None) - three asserts in tests/test_cbm_test_gap_campaign.py own that contract, so
     A36 is disclosure only and must never launder it.
  2. code_tools.TOOL_SCHEMAS - the description is the only thing a CA reads before its
     FIRST call, so the cbm/ocr and read/write descriptions must carry the traps we
     measured rather than leaving a CA to guess.
  3. tools_v15._campaign_routing_prepass - "understand this repo" / "read every file" /
     "who calls this" must route to the index. Measured before A36: "toi can hieu repo
     nay de bat dau" returned restart_orchestrator_via_bat, and "ai goi ham nay" hit the
     no-signal fallback.
  4. examples/SLIM_RULE.txt (+ CORE_RULE.txt) - SLIM_RULE is the file
     manage_agent(action='sync_rule') distributes, so guidance absent from THAT file
     never reaches another CA. It had zero cbm mentions before A36.

Nothing anywhere may RECOMMEND a deprecated tool: CORE_RULE ## DEPRECATED lists
regex_search / find_callers / find_definition_and_usage / read_chunks. Naming one while
saying it is deprecated is fine; routing to one is not.
"""
from __future__ import annotations

import ast
import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEPRECATED_TOOLS = (
    "regex_search",
    "find_callers",
    "find_definition_and_usage",
    "read_chunks",
)


def _tool_description(name: str) -> str:
    """Description as the registry actually exposes it, not as the source spells it."""
    import code_tools

    for entry in code_tools.TOOL_SCHEMAS:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function")
        if isinstance(fn, dict):
            if fn.get("name") == name:
                return str(fn.get("description") or "")
            continue
        if entry.get("name") == name:
            return str(entry.get("description") or "")
    raise AssertionError(
        f"tool {name!r} is not in code_tools.TOOL_SCHEMAS - the hint cannot reach a CA"
    )


def _hint(query: str) -> dict:
    import tools_v15 as v

    raw = asyncio.run(v.tool_decision_hint(task_description=query, verbosity="full"))
    payload = json.loads(raw)
    return payload.get("result", payload)


def _recommended(query: str) -> str:
    return str(_hint(query).get("recommended_tool") or "")


class TestCbmTestGapIsVerdictFree(unittest.TestCase):
    """pass:false + score:null + empty reasons[] read to a CA as a FAILED coverage gate.
    It is not one: this tool computes no verdict on any path."""

    SRC = ROOT / "orchestrator" / "graph" / "testgap.py"

    def _pass_dicts(self) -> list[dict]:
        tree = ast.parse(self.SRC.read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            pairs = {
                k.value: val
                for k, val in zip(node.keys, node.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if "pass" in pairs:
                found.append(pairs)
        return found

    def test_three_payload_shapes_emit_pass_and_all_three_disclose_semantics(self) -> None:
        shapes = self._pass_dicts()
        self.assertEqual(
            3, len(shapes),
            "expected exactly 3 payload shapes carrying a 'pass' key (success, vacuous, "
            f"_unavailable); found {len(shapes)}. A NEW shape must disclose "
            "pass_semantics too - a key that appears only sometimes is the trap this "
            "test exists to prevent.",
        )
        for pairs in shapes:
            self.assertIn(
                "pass_semantics", pairs,
                "a payload emits pass:false without pass_semantics; a CA reading it has "
                "no way to tell 'no verdict computed' from 'coverage failed'",
            )
            node = pairs["pass_semantics"]
            self.assertIsInstance(
                node, ast.Name,
                "pass_semantics must reference the shared PASS_SEMANTICS constant, not "
                "an inlined string that can drift per shape",
            )
            self.assertEqual("PASS_SEMANTICS", node.id)

    def test_the_pinned_contract_value_is_untouched(self) -> None:
        for pairs in self._pass_dicts():
            self.assertIsInstance(pairs["pass"], ast.Constant)
            self.assertIs(
                False, pairs["pass"].value,
                "pass must stay False: tests/test_cbm_test_gap_campaign.py asserts it "
                "three times, and flipping it to make the tool 'look green' would be "
                "laundering a contract, not fixing a bug",
            )
            self.assertIn("score", pairs)
            self.assertIsInstance(pairs["score"], ast.Constant)
            self.assertIsNone(pairs["score"].value)

    def test_the_disclosure_names_the_misread_and_the_real_signal(self) -> None:
        from orchestrator.graph import testgap

        text = testgap.PASS_SEMANTICS
        for needle in ("ALWAYS False", "ALWAYS None", "NO VERDICT"):
            self.assertIn(needle, text, f"PASS_SEMANTICS must state {needle!r}")
        for needle in ("coverage_ratio", "uncovered_files", "run_project_test_script"):
            self.assertIn(
                needle, text,
                f"PASS_SEMANTICS must point at the real signal ({needle}); telling a CA "
                "what the field is NOT, without saying what to read instead, just moves "
                "the guessing",
            )

    def test_the_unavailable_shape_carries_it_at_runtime(self) -> None:
        from orchestrator.graph import testgap

        payload = testgap._unavailable("GRAPH_MISSING")
        self.assertIs(False, payload["pass"])
        self.assertIsNone(payload["score"])
        self.assertEqual(testgap.PASS_SEMANTICS, payload["pass_semantics"])


class TestDescriptionsRouteToTheIndex(unittest.TestCase):
    """The read/write tools are what a CA reaches for first on an unfamiliar repo, so
    the cbm route has to be inside THEIR descriptions - not only in the cbm ones a CA
    has no reason to open yet."""

    READ_WRITE_TOOLS = (
        "read_file_range",
        "read_context_batch",
        "search_code",
        "write_fix",
    )
    EDITED_TOOLS = READ_WRITE_TOOLS + (
        "cbm_test_gap",
        "ocr_map",
        "ocr_scan",
        "ocr_review",
    )

    def test_every_read_write_description_names_a_cbm_tool(self) -> None:
        for name in self.READ_WRITE_TOOLS:
            desc = _tool_description(name)
            self.assertIn(
                "cbm_", desc,
                f"{name} does not mention the cbm family, so a CA on a large repo has "
                "nothing telling it to ask the index before reading source",
            )

    def test_read_file_range_teaches_the_cslt_order(self) -> None:
        desc = _tool_description("read_file_range")
        for needle in ("cbm_graph_status", "cbm_get_architecture", "cbm_dsm"):
            self.assertIn(needle, desc)
        self.assertIn("read_token", desc)

    def test_search_code_states_the_direction_of_the_callers_caveat(self) -> None:
        desc = _tool_description("search_code")
        self.assertIn(
            "instance receivers", desc,
            "mode=callers is the scan that DOES see self.fn() / obj.fn(); it is the "
            "cross-check, not the resolution-limited side",
        )
        self.assertIn("cbm_dead_code", desc)

    def test_write_fix_keeps_the_three_shape_traps(self) -> None:
        desc = _tool_description("write_fix")
        self.assertIn("BOTTOM-UP", desc)
        self.assertIn("expected_hash", desc)
        self.assertIn("cbm_what_if", desc)

    def test_cbm_test_gap_description_says_it_emits_no_verdict(self) -> None:
        desc = _tool_description("cbm_test_gap")
        self.assertIn("VERDICT-FREE", desc)
        self.assertIn("pass_semantics", desc)
        self.assertIn("coverage_ratio", desc)

    def test_ocr_descriptions_chain_into_the_graph_and_disclose_scope(self) -> None:
        ocr_map = _tool_description("ocr_map")
        self.assertIn("cbm_what_if", ocr_map)
        self.assertIn("cbm_dsm", ocr_map)
        scan = _tool_description("ocr_scan")
        self.assertIn("ca_hint_scope", scan)
        review = _tool_description("ocr_review")
        self.assertIn("mode=workspace", review)
        self.assertIn("mode=commit", review)

    def test_no_edited_description_routes_to_a_deprecated_tool(self) -> None:
        for name in self.EDITED_TOOLS:
            desc = _tool_description(name)
            for dead in DEPRECATED_TOOLS:
                if dead in desc:
                    self.assertIn(
                        "DEPRECATED", desc,
                        f"{name} names {dead} without marking it DEPRECATED, so the "
                        "description reads as a recommendation",
                    )


class TestRoutingSendsDiscoveryToTheIndex(unittest.TestCase):
    """Three lowest-priority rows, appended after every existing rule so they can never
    shadow a campaign primary - they only catch phrasings that used to fall through."""

    UNDERSTAND = (
        "toi can hieu repo nay de bat dau",
        "nam tong quan du an",
        "I need to understand this codebase",
        "give me an overview of the repository",
    )
    BULK_READ = (
        "doc het cac file trong repo",
        "read all files in the codebase",
        "scan the whole project source",
    )
    WHO_CALLS = (
        "ai goi ham nay",
        "who calls this function",
        "tim call sites cua ham",
    )

    def test_understanding_a_repo_routes_to_the_module_graph(self) -> None:
        for q in self.UNDERSTAND:
            self.assertIn(
                "cbm_get_architecture", _recommended(q),
                f"{q!r} must route to the module graph; before A36 this phrasing fell "
                "through to the legacy scorer, which answered "
                "restart_orchestrator_via_bat on 'bat dau'",
            )

    def test_a_whole_repo_read_routes_to_index_freshness_first(self) -> None:
        for q in self.BULK_READ:
            self.assertIn(
                "cbm_graph_status", _recommended(q),
                f"{q!r} must be answered from the index, not by reading every file",
            )

    def test_who_calls_routes_to_search_code_not_the_deprecated_alias(self) -> None:
        for q in self.WHO_CALLS:
            rec = _recommended(q)
            self.assertIn("search_code", rec, f"{q!r} must route to search_code")
            self.assertNotIn(
                "find_callers", rec,
                "find_callers is DEPRECATED per CORE_RULE; mode='callers' is the live "
                "route",
            )

    def test_the_new_rows_do_not_shadow_an_existing_campaign_primary(self) -> None:
        for q, expected in (
            ("tim ham chet khong duoc goi", "cbm_dead_code"),
            ("sua ham trong file billing.py", "write_fix"),
            ("sua file xong chay test", "run_project_test_script"),
        ):
            self.assertIn(
                expected, _recommended(q),
                f"{q!r} lost its campaign primary to an A36 row; the new rules are "
                "appended LAST precisely so this cannot happen",
            )


class TestRuleFilesTeachCbmToOtherCAs(unittest.TestCase):
    """SLIM_RULE.txt is what manage_agent(sync_rule) distributes. A rule that lives only
    in CORE_RULE reaches a CA after a get_rule_core_text fetch; a rule that lives in
    NEITHER reaches nobody."""

    SLIM = ROOT / "examples" / "SLIM_RULE.txt"
    CORE = ROOT / "examples" / "CORE_RULE.txt"

    def test_slim_rule_carries_the_cbm_first_route(self) -> None:
        text = self.SLIM.read_text(encoding="utf-8")
        self.assertIn("CBM-FIRST", text)
        for needle in (
            "cbm_graph_status",
            "cbm_get_architecture",
            "cbm_dsm",
            "cbm_trace_path",
            "ocr_map",
        ):
            self.assertIn(
                needle, text,
                f"SLIM_RULE must name {needle}; before A36 this file had ZERO cbm "
                "mentions, so no synced CA was ever told the index exists",
            )

    def test_slim_rule_carries_both_measured_traps(self) -> None:
        text = self.SLIM.read_text(encoding="utf-8")
        self.assertIn("pass_semantics", text)
        self.assertIn("VERDICT-FREE", text)
        self.assertIn("mode='callers'", text)
        self.assertIn("RESOLVED", text)

    # SLIM_RULE's Read Routing line names regex_search inside a MEASURED FAILURE
    # report ("regex_search hard-fails at its 5s deadline"). That is the opposite of a
    # recommendation, so a line qualifies by marking the tool DEPRECATED or by saying
    # it fails - what must never happen is a bare mention that reads as advice.
    NOT_A_RECOMMENDATION = ("hard-fails", "hard fails", "DEPRECATED")

    def test_slim_rule_names_the_deprecated_aliases_only_as_deprecated(self) -> None:
        for n, line in enumerate(self.SLIM.read_text(encoding="utf-8").splitlines(), 1):
            hits = [d for d in DEPRECATED_TOOLS if d in line]
            if not hits:
                continue
            self.assertTrue(
                any(marker in line for marker in self.NOT_A_RECOMMENDATION),
                f"SLIM_RULE:{n} names {hits} without marking it DEPRECATED and without "
                "reporting that it fails, so the line reads as a recommendation",
            )

    def test_core_rule_read_routing_carries_the_same_route(self) -> None:
        text = self.CORE.read_text(encoding="utf-8")
        self.assertIn("CBM-FIRST", text)
        self.assertIn("cbm INDEX to UNDERSTAND", text)
        self.assertIn("cbm_get_architecture", text)
        self.assertIn("pass_semantics", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
