"""A23-P2: the ``fields=`` response filter must not drop unknown keys silently.

Recorded from real use of [A] to fix [A]: a caller asked for
``fields=ok,result,matches,hits`` on ``search_code`` and got back an almost
empty object, because the real key is ``results`` (plural). Nothing said so.
The caller reads that as "the tool found nothing" when in fact the tool found
plenty and the FILTER threw it away.

This is the A17/A09 defect a third time, on a third surface:

    A17  POST body field dropped, caller not told   -> fixed
    A09  GET query param dropped, caller not told   -> fixed
    A23-P2  fields= key dropped, caller not told    <- here

It is the most damaging of the three, because the other two degrade to "your
filter did nothing"; this one degrades to "your data does not exist". A typo in
``fields=`` is indistinguishable from an empty result.

Measured on live [A] (generation 3cf96d818a30) before the fix:

    ?fields=ok,protected_tools   -> {ok, generation, protected_tools}
    ?fields=ok,protected_tool    -> {ok, generation}          <- one typo, gone
    ?fields=totally_bogus_key    -> {ok, generation}
    ?fields=ok,already_fetched,sha256sum (get_rule_core_text)
                                 -> sha256 missing, no warning

That last one matters: CORE_RULE itself instructs every agent to call
``get_rule_core_text`` with an explicit ``fields=`` list. The one surface whose
documented usage REQUIRES ``fields=`` is the one where a typo silently removes
the payload the agent was told to fetch.

The fix reports ``fields_unknown`` (keys asked for that the payload does not
have) alongside the filtered result. These tests pin BEHAVIOUR both ways:
a wrong key is named, and a correct filter stays clean -- a warning that is
always present carries no information (the A17 negative-case discipline).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator_server import _r101_filter

# A payload shaped like a real one: an envelope key, a data key, and the
# plural/singular pair that caused the reported defect.
PAYLOAD = {
    "ok": True,
    "results": ["a", "b"],
    "count": 2,
    "elapsed_ms": 7,
    "generation": "deadbeef",
}


class UnknownFieldIsNamed(unittest.TestCase):
    def test_the_reported_case_result_vs_results(self):
        """The exact term from the report: `result` instead of `results`."""
        out = _r101_filter(dict(PAYLOAD), {"ok", "result"})
        self.assertEqual(out.get("fields_unknown"), ["result"])

    def test_several_unknown_keys_are_all_named_sorted(self):
        out = _r101_filter(dict(PAYLOAD), {"ok", "matches", "hits"})
        self.assertEqual(out.get("fields_unknown"), ["hits", "matches"])

    def test_a_wholly_wrong_filter_is_not_an_empty_result(self):
        """The damaging shape: nothing asked for exists.

        Before the fix this returned only the always-keep envelope, which reads
        as "the tool returned nothing". It must now say why.
        """
        out = _r101_filter(dict(PAYLOAD), {"totally_bogus_key"})
        self.assertEqual(out.get("fields_unknown"), ["totally_bogus_key"])

    def test_case_matters_and_is_reported(self):
        # `Count` is not `count`; the filter is case-sensitive, so a caller who
        # capitalises loses the field. Say so rather than returning it empty.
        out = _r101_filter(dict(PAYLOAD), {"ok", "Count"})
        self.assertEqual(out.get("fields_unknown"), ["Count"])
        self.assertNotIn("count", out)

    def test_partial_typo_keeps_the_good_key_and_names_the_bad_one(self):
        # Serve what was understood, report what was not -- the A09 shape.
        out = _r101_filter(dict(PAYLOAD), {"ok", "results", "hits"})
        self.assertEqual(out["results"], ["a", "b"])
        self.assertEqual(out.get("fields_unknown"), ["hits"])


class CorrectFilterStaysClean(unittest.TestCase):
    """A warning present on every call is a warning nobody reads."""

    def test_exact_filter_has_no_warning(self):
        out = _r101_filter(dict(PAYLOAD), {"ok", "results"})
        self.assertNotIn("fields_unknown", out)
        self.assertEqual(set(out), {"ok", "results"})

    def test_no_filter_at_all_is_untouched(self):
        out = _r101_filter(dict(PAYLOAD), None)
        self.assertEqual(out, PAYLOAD)
        self.assertNotIn("fields_unknown", out)

    def test_empty_filter_is_untouched(self):
        out = _r101_filter(dict(PAYLOAD), set())
        self.assertEqual(out, PAYLOAD)

    def test_always_keep_keys_are_not_reported_as_unknown(self):
        """`error`/`error_name` are force-kept on success payloads that lack
        them. They are a property of the filter, not something the caller
        asked for, so their absence must not be reported."""
        out = _r101_filter(dict(PAYLOAD), {"ok"})
        self.assertNotIn("fields_unknown", out)

    def test_asking_for_an_always_keep_key_that_is_absent_is_not_unknown(self):
        # A caller may legitimately list `error` in fields= on a call that
        # happens to succeed. That is not a typo.
        out = _r101_filter(dict(PAYLOAD), {"ok", "error", "error_name"})
        self.assertNotIn("fields_unknown", out)


class FilteringItselfStillWorks(unittest.TestCase):
    """Guard against 'fixing' the report by weakening the filter."""

    def test_unasked_keys_are_still_dropped(self):
        out = _r101_filter(dict(PAYLOAD), {"ok", "results"})
        self.assertNotIn("elapsed_ms", out)
        self.assertNotIn("count", out)

    def test_ok_is_always_kept_even_when_not_asked_for(self):
        out = _r101_filter(dict(PAYLOAD), {"count"})
        self.assertIn("ok", out)

    def test_non_dict_payload_is_passed_through(self):
        self.assertEqual(_r101_filter(["x"], {"ok"}), ["x"])

    def test_the_input_payload_is_not_mutated(self):
        """The filter builds a new dict; the caller's payload must be intact.

        Same class of defect as A09's shared-payload copy: a report field
        written into the source object leaks into whoever else holds it.
        """
        src = dict(PAYLOAD)
        _r101_filter(src, {"ok", "bogus"})
        self.assertEqual(src, PAYLOAD)
        self.assertNotIn("fields_unknown", src)


if __name__ == "__main__":
    unittest.main()
