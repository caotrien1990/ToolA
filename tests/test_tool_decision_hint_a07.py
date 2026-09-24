"""A07: a broad stage-1 decision rule must not shadow a tool whose multi-segment
name the query spells out.

The original defect: "audit tool matrix" recommended the session bundle
(open_session / log_native_event / close_session) because a broad rule matched
the single word `audit`, while `audit_tool_matrix` -- a tool the query names in
full -- was only reachable as a candidate.

The guard compares two counts over the SAME raw word set:
    hits    = query words matching the winning rule's regex
    overlap = query words that are segments of the lexical top-1 tool name
and defers to the ranker only when overlap > hits.

This file pins the COMPARISON, not the two queries the fix was written for.
An earlier version of the guard used an absolute `hits == 1` gate; it passed
both of those queries and still failed every longer phrasing of them, because
`hits` grows with sentence length. The boundary tests below are the ones that
separate the two implementations, so they carry the weight here.
"""
from __future__ import annotations

import re
import unittest

import tools_v15 as v


def _measure(query: str) -> tuple[dict | None, int, int, list]:
    """Recompute exactly what tool_decision_hint feeds the guard: same
    lowercase-raw `task`, same `_wt_vn_expand` for the ranker, same
    raw-probe-first rule ordering, so `rule` here is the real `_matched[0]`."""
    task = (query or "").lower().strip()
    lex = v._wt_rank_lexical(v._wt_vn_expand(task), top_k=3)
    words = {w for w in re.findall(r"[a-z0-9_]+", task) if w}
    rule = None
    for probe in (task, v._wt_vn_expand(task)):
        rule = next((r for r in v._DECISION_RULES if re.search(r["re"], probe)), None)
        if rule:
            break
    hits = sum(1 for w in words if rule and re.search(rule["re"], w)) if rule else 0
    overlap = len(words & set(lex[0][1].split("_"))) if lex else 0
    return rule, hits, overlap, lex


class GuardDefersWhenTheQueryNamesTheTool(unittest.TestCase):
    def test_the_original_defect_query(self):
        rule, _h, _o, lex = _measure("audit tool matrix")
        self.assertIsNotNone(rule, "a broad rule must still fire (else no A07)")
        self.assertEqual(lex[0][1], "audit_tool_matrix")
        self.assertTrue(v._a07_should_defer("audit tool matrix", rule, lex))

    def test_word_order_does_not_matter(self):
        rule, _h, _o, lex = _measure("tool matrix audit")
        self.assertTrue(v._a07_should_defer("tool matrix audit", rule, lex))

    def test_longer_phrasing_of_the_same_request_still_defers(self):
        """THE boundary. `for this session` adds a word the SAME broad rule
        matches, so hits goes 1 -> 2 while the query names the tool no less
        completely than before. A `hits == 1` gate reads that extra word as
        rule confidence and hands the query back to the session bundle -- the
        original bug, verbatim, on a phrasing users are at least as likely to
        type as the terse one."""
        for query in ("audit tool matrix for this session",
                      "log the audit tool matrix"):
            with self.subTest(query=query):
                rule, hits, overlap, lex = _measure(query)
                self.assertGreater(
                    hits, 1, "this query must exercise the hits>1 path")
                self.assertEqual(lex[0][1], "audit_tool_matrix")
                self.assertTrue(
                    v._a07_should_defer(query, rule, lex),
                    f"hits={hits} overlap={overlap}: the query spells out the "
                    "tool more completely than it hits the rule")

    def test_repeated_rule_keyword_is_counted_once(self):
        rule, hits, _o, lex = _measure("audit the audit tool matrix")
        self.assertEqual(hits, 1, "distinct words, not occurrences")
        self.assertTrue(v._a07_should_defer("audit the audit tool matrix", rule, lex))


class GuardKeepsThickRuleMatches(unittest.TestCase):
    """The reverse regression. Deferring is only correct when the ranker has
    MORE evidence than the rule; these queries are what the rules exist for."""

    def test_multi_keyword_session_queries_stay_with_stage_one(self):
        for query in ("session audit handoff", "close session summary"):
            with self.subTest(query=query):
                rule, hits, overlap, lex = _measure(query)
                self.assertGreaterEqual(hits, overlap)
                self.assertFalse(v._a07_should_defer(query, rule, lex))

    def test_bare_keyword_stays_with_stage_one(self):
        """"audit" alone: hits=1, overlap=1. A tie is not evidence that the
        ranker knows better, and the user has named no tool -- exactly the
        case the broad rule is for."""
        rule, hits, overlap, lex = _measure("audit")
        self.assertEqual((hits, overlap), (1, 1))
        self.assertFalse(v._a07_should_defer("audit", rule, lex))

    def test_expanded_vocabulary_cannot_inflate_the_overlap(self):
        """A lexical candidate may contain expansion-only words absent from RAW.

        Fix the candidate: live description changes must not turn this unit
        test into an assertion about unrelated lexical ranking.
        """
        rule = {"re": r"session"}
        lex = [(10.0, "workflow_session", {}, "T3")]
        self.assertFalse(v._a07_should_defer("open a work session", rule, lex))
        self.assertTrue(v._a07_should_defer("open workflow session", rule, lex))


class GuardIsScaleFree(unittest.TestCase):
    """Neither side may be inflated by sentence length alone. This is the
    property `hits == 1` lacked, stated directly rather than through examples."""

    def test_padding_a_deferring_query_with_filler_does_not_flip_it(self):
        """Filler words carrying no new intent must not revoke a deferral.
        `for this session` and a repeated `session` both raise hits without
        adding anything the user wants done -- the deferral has to survive."""
        base = "audit tool matrix"
        rule, _h, _o, lex = _measure(base)
        self.assertTrue(v._a07_should_defer(base, rule, lex))
        for pad in ("for this session", "session session", "please"):
            query = f"{base} {pad}"
            with self.subTest(pad=pad):
                r2, hits2, _o2, lex2 = _measure(query)
                self.assertEqual(lex2[0][1], "audit_tool_matrix")
                self.assertTrue(
                    v._a07_should_defer(query, r2, lex2),
                    f"hits={hits2}: filler must not revoke the deferral")

    def test_a_second_real_intent_does_revoke_the_deferral(self):
        """The other side of the same scale, and the reason it is a ratio and
        not a "longer query always defers" rule. `and log the session` adds a
        genuine second intent (`log`), taking hits to 3 against overlap 3. The
        query no longer names one tool better than it names the rule's job, so
        stage 1 correctly keeps the primary -- see the alternates test below
        for how the named tool stays reachable anyway."""
        query = "audit tool matrix and log the session"
        rule, hits, overlap, lex = _measure(query)
        self.assertEqual((hits, overlap), (3, 3))
        self.assertFalse(v._a07_should_defer(query, rule, lex))

    def test_no_rule_match_never_defers(self):
        """Guard is only ever consulted with a rule that fired; if hits is 0
        there is nothing to defer FROM, and returning True would let the
        ranker override a rule it was never compared against."""
        fake = {"re": r"\bzzzznomatch\b", "tool": "irrelevant"}
        _r, _h, _o, lex = _measure("audit tool matrix")
        self.assertFalse(v._a07_should_defer("audit tool matrix", fake, lex))

    def test_empty_ranker_never_defers(self):
        rule, _h, _o, _lex = _measure("audit tool matrix")
        self.assertFalse(v._a07_should_defer("audit tool matrix", rule, []))


class AlternatesAlwaysOfferASecondOption(unittest.TestCase):
    def test_alternates_exclude_the_primary(self):
        _r, _h, _o, lex = _measure("audit")
        alts = v._a07_alternates(lex, exclude=lex[0][1])
        self.assertTrue(alts, "a correct stage-1 primary still needs a fallback")
        self.assertNotIn(lex[0][1], [a["tool"] for a in alts])

    def test_alternates_are_bounded_and_described(self):
        _r, _h, _o, lex = _measure("audit")
        alts = v._a07_alternates(lex, exclude="", limit=2)
        self.assertLessEqual(len(alts), 2)
        for alt in alts:
            self.assertTrue(alt["tool"])
            self.assertTrue(alt["why"], "an alternate with no reason is noise")


class NamedToolStaysReachableWhenDeferralIsWrong(unittest.TestCase):
    """A07's second reader. `_a07_should_defer` only rescues the single-rule
    case; when two rules fire, `_matched[1:3]` fills alternates with rule
    siblings and the escape hatch never runs. A tool the user named outright
    then disappears from the whole answer -- primary AND alternates -- which is
    the original A07 complaint in a place the deferral fix cannot reach.

    Asserted end-to-end through the public tool, because the defect is in how
    the two alternate sources are combined, not inside either helper."""

    def _hint(self, query: str) -> dict:
        import asyncio
        import json
        raw = asyncio.run(v.tool_decision_hint(task_description=query,
                                               verbosity="full"))
        payload = json.loads(raw)
        return payload.get("result", payload)

    def test_multi_intent_query_still_surfaces_the_named_tool(self):
        result = self._hint("audit tool matrix and log the session")
        alts = [a.get("tool") for a in (result.get("alternates") or [])]
        self.assertIn("audit_tool_matrix", alts)
        self.assertGreater(len(alts), 1,
                           "the rule sibling must not be displaced, only joined")

    def test_named_tool_is_not_duplicated_when_already_primary(self):
        result = self._hint("audit tool matrix")
        primary = str(result.get("recommended_tool") or "")
        alts = [a.get("tool") for a in (result.get("alternates") or [])]
        self.assertNotIn(primary, alts)

    def test_helper_adds_nothing_when_the_query_names_no_tool(self):
        _r, _h, _o, lex = _measure("audit")
        self.assertEqual(v._a07_named_tool_alternate("audit", lex, ""), [],
                         "one shared word is not the user naming a tool")

    def test_helper_adds_nothing_when_the_tool_is_already_shown(self):
        _r, _h, _o, lex = _measure("audit tool matrix")
        self.assertEqual(
            v._a07_named_tool_alternate("audit tool matrix", lex,
                                        "audit_tool_matrix"), [])

    def test_helper_adds_at_most_one(self):
        _r, _h, _o, lex = _measure("audit tool matrix and log the session")
        self.assertLessEqual(
            len(v._a07_named_tool_alternate(
                "audit tool matrix and log the session", lex, "open_session")), 1)


# ── N2 (2026-09-24): an answer names tools a CA can call ─────────────────────


def _n2_answer(query: str) -> dict:
    import asyncio
    import json
    raw = asyncio.run(v.tool_decision_hint(task_description=query,
                                           verbosity="full"))
    payload = json.loads(raw)
    return payload.get("result", payload)


def _n2_offered(result: dict) -> list:
    """Every tool name an answer puts in front of a CA as something to call."""
    return [result.get("recommended_tool"),
            (result.get("suggestion") or {}).get("tool"),
            *[a.get("tool") for a in result.get("alternates") or []],
            *[c.get("tool") for c in result.get("candidates") or []]]


def _n2_registry() -> tuple[set, set, set]:
    """(live, server-marked deprecated, schema-flagged deprecated), read from the
    registries themselves and not through the helper under test, so a helper
    that stops reading one of the two marks goes RED here."""
    import code_tools as ct
    from orchestrator_server import _DEPRECATED_TOOLS
    flagged = {s["function"]["name"] for s in ct.TOOL_SCHEMAS
               if s.get("function", {}).get("deprecated")}
    return set(ct.TOOL_MAP), set(_DEPRECATED_TOOLS), flagged


class DecisionRowsNameCallableTools(unittest.TestCase):
    """N2. A row's `tool` came back verbatim as `recommended_tool`, and 18 of
    the 26 rows carried prose there -- "find_symbol(include=all) OR
    search_code(mode=smart)", "Edit + log_native_event". A CA reads that field
    as a name to call. Which tool fits is the row's job; how to call it is the
    schema's."""

    def test_every_row_names_identifiers_only(self):
        for i, rule in enumerate(v._DECISION_RULES):
            for name in [rule["tool"], *rule.get("also", ())]:
                with self.subTest(row=i, name=name):
                    self.assertRegex(name, r"^[a-z][a-z0-9_]*$")

    def test_every_row_names_live_tools_that_are_not_deprecated(self):
        live, marked, flagged = _n2_registry()
        for i, rule in enumerate(v._DECISION_RULES):
            for name in [rule["tool"], *rule.get("also", ())]:
                with self.subTest(row=i, name=name):
                    self.assertIn(name, live)
                    self.assertNotIn(name, marked | flagged)

    def test_rule_names_is_the_primary_then_each_companion_once(self):
        self.assertEqual(v._rule_names({"tool": "a", "also": ("b", "a", "c")}),
                         ["a", "b", "c"])
        self.assertEqual(v._rule_names({"tool": "a"}), ["a"])

    def test_validation_checks_the_row_names_not_prose_tokens(self):
        """The CAN-053 regex read `(artifact)` and `mode='ref'` inside the
        prose as tool ids, so `all_recommended_tools_live` was False on a row
        whose every tool was live. The same names bound the alternates: a
        companion the primary row already names is not a second option."""
        live, _m, _f = _n2_registry()
        for query in ("pick a folder in the folder dialog", "save log snapshot",
                      "find symbol foo", "diff the dom state",
                      # one row fired, so the alternates come from the ranker
                      "find symbol foo with search code",
                      # two rows fired, and the query spells out a companion
                      "folder dialog snapshot then a small tweak"):
            with self.subTest(query=query):
                result = _n2_answer(query)
                checked = result["tools_validation"]["checked"]
                alts = [a["tool"] for a in result.get("alternates") or []]
                self.assertEqual(checked[0], result["recommended_tool"])
                self.assertGreater(len(checked), 1,
                                   "a row with companions is checked whole")
                self.assertLessEqual(set(checked), live)
                self.assertEqual(result["tools_validation"]["missing"], [])
                self.assertIs(result["all_recommended_tools_live"], True)
                self.assertLessEqual(set(result["payload_example_per_tool"]),
                                     set(checked))
                self.assertFalse(set(checked) & set(alts))
                self.assertEqual(len(alts), len(set(alts)))

    def test_a_plainer_row_firing_first_keeps_the_companion_reachable(self):
        """"fix the auth token bug" fires the plain write_fix row before the
        auth row. Both name write_fix, so a dedupe keyed on the primary alone
        drops the auth row -- and with it the dry-run preview an auth change
        wants. Two rows are the same only when they name the same tools."""
        result = _n2_answer("fix the auth token bug in file x")
        self.assertEqual(result["recommended_tool"], "write_fix")
        offered = set(result["tools_validation"]["checked"]) | {
            a["tool"] for a in result.get("alternates") or []}
        self.assertIn("apply_after_dry_run", offered)


class AnswersOnlyNameCallableTools(unittest.TestCase):
    """N2, end to end: every path of the answer -- campaign prepass, rule,
    recipe, lexical and the no-signal default -- names tools a CA can call.
    The default path answered "find_symbol / search_code" while its own
    suggestion said search_code."""

    QUERIES = (
        "build or refresh the cbm code graph index", "who calls foo",
        "find callers of foo", "fix a typo in the css",
        "audit tool matrix and log the session", "understand this repo",
        "read all files in the repo", "screenshot the app", "zzz qqq",
        "click the button and watch the state", "restart the server port",
        "submit the workflow step", "refactor across many files",
        "pick a folder in the folder dialog",
    )

    def test_every_offered_name_is_live_and_not_deprecated(self):
        live, marked, flagged = _n2_registry()
        for query in self.QUERIES:
            result = _n2_answer(query)
            for name in _n2_offered(result):
                with self.subTest(query=query, name=name):
                    self.assertIn(name, live)
                    self.assertNotIn(name, marked | flagged)

    def test_the_recommended_tool_is_the_suggested_tool(self):
        for query in self.QUERIES:
            with self.subTest(query=query):
                result = _n2_answer(query)
                self.assertEqual(result["recommended_tool"],
                                 result["suggestion"]["tool"])


class GraphMaintenanceRoutesToTheIndexTools(unittest.TestCase):
    """N2. "build or refresh the cbm code graph index" had no route: expansion
    reads `graph` as architecture, so the answer was read-only graph tools and
    the step that makes a graph answer true -- bringing graph.db up to date --
    never surfaced. cbm_incremental_index leads because it diffs source hashes
    and, on an empty graph, counts every file as added, so it is also the first
    build; cbm_index (a full re-index of a chosen scope) stays one step away."""

    MAINTAIN = ("build or refresh the cbm code graph index", "refresh the code graph",
                "reindex the project graph", "build cbm index",
                "run cbm_incremental_index", "cap nhat index cbm",
                "tao lai do thi code", "build the code graph", "code graph refresh")
    FRESHNESS = ("is the cbm graph stale", "graph is stale, rebuild it",
                 "check freshness of the cbm graph",
                 "do thi code da moi nhat chua")
    INDEX_TOOLS = frozenset({"cbm_index", "cbm_incremental_index"})

    def test_maintenance_leads_with_the_incremental_index(self):
        for query in self.MAINTAIN:
            with self.subTest(query=query):
                result = _n2_answer(query)
                self.assertEqual(result["recommended_tool"], "cbm_incremental_index")
                self.assertIn("cbm_index",
                              [a["tool"] for a in result.get("alternates") or []])

    def test_a_freshness_question_reads_the_status_before_a_rebuild(self):
        for query in self.FRESHNESS:
            with self.subTest(query=query):
                result = _n2_answer(query)
                self.assertEqual(result["recommended_tool"], "cbm_graph_status")
                self.assertIn("cbm_incremental_index",
                              [a["tool"] for a in result.get("alternates") or []])

    def test_other_indexes_and_graphs_do_not_route_to_graph_db(self):
        """A bare `index` is the search_code index (STALE_INDEX -> cache_delta),
        and building a dependency graph is a structure question."""
        for query in ("refresh the search index", "build the search index",
                      "build the dependency graph"):
            with self.subTest(query=query):
                self.assertNotIn(_n2_answer(query)["recommended_tool"],
                                 self.INDEX_TOOLS)


class DeprecatedAliasesAreNeverOffered(unittest.TestCase):
    """N2. "find callers of foo" offered find_callers, a deprecated alias of
    search_code(mode=callers), because the ranker scores a name segment the
    query repeats. The server marks deprecation in two machine-readable places
    -- orchestrator_server._DEPRECATED_TOOLS and a schema's `deprecated` key --
    and the ranker has to honour both."""

    def test_the_measured_queries_offer_no_alias(self):
        _live, marked, flagged = _n2_registry()
        for query in ("find callers of foo", "find definition and usage of foo",
                      "regex search for foo"):
            with self.subTest(query=query):
                self.assertFalse(set(_n2_offered(_n2_answer(query))) & (marked | flagged))

    def test_the_ranker_skips_a_marked_tool_even_when_the_query_is_its_name(self):
        _live, marked, flagged = _n2_registry()
        self.assertTrue(marked and flagged - marked,
                        "each mark needs a tool it alone carries, or it is unmeasured")
        for name in sorted(marked | flagged):
            with self.subTest(name=name):
                ranked = v._wt_rank_lexical(f"{name.replace('_', ' ')} {name}", top_k=50)
                self.assertTrue(ranked, "the query must still rank something")
                self.assertNotIn(name, [r[1] for r in ranked])


class ShownNamesMatchExactly(unittest.TestCase):
    """N2. "Already shown" was a substring test on a joined string, so
    `write_fix` counted as shown when only `write_fix_html` was and dropped out
    of the alternates. A row now names several tools, so what is shown is a set
    of names and membership is exact."""

    LEX = ((10.0, "write_fix", {"description": "edit one region"}, "T2"),)

    def test_a_longer_name_does_not_hide_its_prefix(self):
        self.assertEqual(
            [a["tool"] for a in v._a07_alternates(self.LEX, exclude="write_fix_html")],
            ["write_fix"])
        self.assertEqual(
            [a["tool"] for a in v._a07_named_tool_alternate(
                "write fix now", self.LEX, "write_fix_html")],
            ["write_fix"])

    def test_a_shown_name_is_excluded_from_a_string_or_a_collection(self):
        for shown in ("write_fix", "open_session write_fix", ["write_fix"], {"write_fix"}):
            with self.subTest(shown=shown):
                self.assertEqual(v._a07_alternates(self.LEX, exclude=shown), [])
                self.assertEqual(
                    v._a07_named_tool_alternate("write fix now", self.LEX, shown), [])


if __name__ == "__main__":
    unittest.main()
