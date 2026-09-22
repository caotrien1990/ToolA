"""D1: a TRUNCATED post-commit validation must not read as a full-commit pass.

Root defect (measured 2026-09-22 while tracing the 53.8s post-commit tail): the
git_commit post-hook validates at most `_POST_COMMIT_VALIDATION_CAP` committed files,
but the answer only ever carried `validated` + `all_ok` + `details`. Commit 439a04e
committed 16 files, so 6 were never checked, and the row still read `all_ok: true` --
byte-identical to a commit where every single file passed. That is the same false-OK
shape as a 0-hit search answering ok with no cause: the truncated answer and the
complete one could not be told apart by the CA reading them.

The cap itself is NOT the bug and is deliberately kept. validate_file costs 0.2-2.2s
per file on this HDD (measured per call), a commit is not the place to pay 16 of those,
and 8-way parallelism was measured at only 1.67x end to end. The fix is to stop the
answer from CLAIMING more coverage than it bought.

These tests pin three promises:

  1. an untruncated commit keeps a fixed, minimal shape (no new per-commit tokens),
  2. a truncated commit states the gap -- and is therefore DISTINGUISHABLE from a
     full pass that carries the same `all_ok: true`, which is the whole defect,
  3. `all_ok` keeps meaning "every file that was actually validated passed", so a
     real failure inside the validated slice is still reported as a failure.

Every assertion here is on returned BEHAVIOUR, never on the source text of the
producer.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator_server as osrv  # noqa: E402

_BASE_KEYS = {"committed", "validated", "all_ok", "details"}


def _files(n: int) -> list[str]:
    return ["f%d.py" % i for i in range(n)]


def _rows(n: int, ok: bool = True) -> list[dict]:
    return [{"file": "f%d.py" % i, "ok": ok, "error": "" if ok else "boom"}
            for i in range(n)]


class CapTest(unittest.TestCase):
    def test_the_cap_is_a_named_constant_the_summary_defaults_to(self):
        """Positive control: if the cap were not 10, the 16-file case below is not the
        measured case any more and every number in this file would drift silently."""
        self.assertEqual(osrv._POST_COMMIT_VALIDATION_CAP, 10)

    def test_the_scope_line_names_the_cap_it_was_called_with(self):
        summary = osrv._post_commit_validation_summary(_files(9), _rows(4), cap=4)
        self.assertIn("cap 4", summary["all_ok_scope"])


class UntruncatedTest(unittest.TestCase):
    def test_a_fully_validated_commit_carries_no_extra_keys(self):
        summary = osrv._post_commit_validation_summary(_files(3), _rows(3))
        self.assertEqual(set(summary), _BASE_KEYS)
        self.assertEqual(summary["committed"], 3)
        self.assertEqual(summary["validated"], 3)
        self.assertTrue(summary["all_ok"])
        self.assertEqual(len(summary["details"]), 3)

    def test_a_commit_exactly_at_the_cap_is_not_reported_as_truncated(self):
        """Off-by-one guard: 10 committed and 10 validated is FULL coverage."""
        cap = osrv._POST_COMMIT_VALIDATION_CAP
        summary = osrv._post_commit_validation_summary(_files(cap), _rows(cap))
        self.assertEqual(set(summary), _BASE_KEYS)
        self.assertNotIn("unvalidated", summary)

    def test_an_empty_commit_stays_quiet(self):
        summary = osrv._post_commit_validation_summary([], [])
        self.assertEqual(set(summary), _BASE_KEYS)
        self.assertEqual(summary["committed"], 0)
        self.assertTrue(summary["all_ok"])  # all() of nothing, unchanged behaviour


class TruncatedTest(unittest.TestCase):
    def test_the_measured_case_states_the_gap(self):
        """Commit 439a04e: 16 files committed, 10 validated, all_ok true regardless."""
        summary = osrv._post_commit_validation_summary(_files(16), _rows(10))
        self.assertEqual(summary["committed"], 16)
        self.assertEqual(summary["validated"], 10)
        self.assertEqual(summary["unvalidated"], 6)
        self.assertTrue(summary["all_ok"])
        scope = summary["all_ok_scope"]
        self.assertIn("16", scope)
        self.assertIn("10", scope)
        self.assertIn("6", scope)
        self.assertIn("NOT validated", scope)

    def test_a_truncated_pass_is_distinguishable_from_a_complete_pass(self):
        """THE defect, stated as an inequality.

        Before the fix both calls below returned the same keys with all_ok true, so no
        reader could tell 6 unchecked files from 6 passing ones. If this ever fails,
        the false-OK shape is back even if every other test here is green.
        """
        truncated = osrv._post_commit_validation_summary(_files(16), _rows(10))
        complete = osrv._post_commit_validation_summary(_files(10), _rows(10))
        self.assertTrue(truncated["all_ok"])
        self.assertTrue(complete["all_ok"])
        self.assertNotEqual(set(truncated), set(complete))
        self.assertNotEqual(truncated["committed"], truncated["validated"])
        self.assertEqual(complete["committed"], complete["validated"])

    def test_more_validations_than_committed_never_emits_a_negative_gap(self):
        """Defensive: a caller mismatch must not print 'the remaining -2 were NOT...'."""
        summary = osrv._post_commit_validation_summary(_files(1), _rows(3))
        self.assertEqual(set(summary), _BASE_KEYS)


class AllOkMeaningTest(unittest.TestCase):
    def test_a_failure_inside_the_validated_slice_still_fails(self):
        summary = osrv._post_commit_validation_summary(
            _files(4), _rows(3) + [{"file": "bad.py", "ok": False, "error": "boom"}])
        self.assertFalse(summary["all_ok"])
        self.assertEqual(set(summary), _BASE_KEYS)

    def test_a_row_missing_its_ok_key_counts_as_not_ok(self):
        summary = osrv._post_commit_validation_summary(_files(2), [{"file": "a.py"},
                                                                  {"file": "b.py",
                                                                   "ok": True}])
        self.assertFalse(summary["all_ok"])

    def test_a_truncated_commit_with_a_real_failure_reports_both(self):
        summary = osrv._post_commit_validation_summary(
            _files(16), _rows(9) + [{"file": "bad.py", "ok": False, "error": "boom"}])
        self.assertFalse(summary["all_ok"])
        self.assertEqual(summary["unvalidated"], 6)


if __name__ == "__main__":
    unittest.main()
