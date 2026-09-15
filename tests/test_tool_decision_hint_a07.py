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


if __name__ == "__main__":
    unittest.main()
