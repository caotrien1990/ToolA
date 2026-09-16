"""ISS-12: a regex deadline must not destroy the matches already found.

`_worker` accumulated every match inside the SPAWNED CHILD and pickled the list
only after the whole scan finished. On deadline the parent raised and
`_stop_and_reap` terminated that child, so every file already scanned was thrown
away: the caller received a bare REGEX_SEARCH_TIMEOUT and could not distinguish
"this repo has no such symbol" from "most of the repo was never read". On a large
tree that is the failure Anh named -- a short wall-clock timeout used where a
progress watchdog belongs.

The fix has two halves, and both are pinned here:

1. the child CHECKPOINTS between files (first match immediately, then at most
   every _CHECKPOINT_INTERVAL_SECONDS) with an atomic os.replace, and the parent
   salvages that checkpoint AFTER reaping, onto the RegexSearchTimeout it raises;
2. `_regex_timeout_payload` carries those rows plus honest coverage counters.

Explicitly preserved: ok stays False and error_name stays REGEX_SEARCH_TIMEOUT
(the scan did not finish, and a prefix must never be read as the full result
set), and a timeout with NOTHING salvaged keeps the exact old shape -- an empty
`results` on an unfinished scan would read as a confident "not found", which is
the same lie in the other direction.
"""
from __future__ import annotations

import asyncio
import json
import pickle
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import search_regex_worker as rw
from orchestrator.tools import search as search_module


def _mk_timeout(rows, scanned=0, total=0, age_ms=None):
    err = rw.RegexSearchTimeout("worker timed out (test)")
    err.partial_results = rows
    err.records_scanned = scanned
    err.records_total = total
    err.partial_checkpoint_age_ms = age_ms
    return err


def _payload_from(side_effect, *, root: Path, deadline: float | None) -> dict:
    (root / "sample.txt").write_text("needle\n", encoding="utf-8")
    search_module.search_snapshots.publish_snapshot(
        root, include_file=lambda path: True,
        skip_dir=lambda name: name.startswith("."), index=None,
    )
    with mock.patch.object(search_module, "_run_search_singleflight", side_effect=side_effect):
        raw = asyncio.run(search_module.search_code(
            query="needle", path=str(root), mode="regex",
            regex_deadline_seconds=deadline,
        ))
    return json.loads(raw)


_ROWS = [{"file": "a.txt", "line": 3, "match": "needle", "context_before": "",
          "context_after": ""}]


class WorkerCheckpointTests(unittest.TestCase):
    """The child-side half: real subprocess, real kill, salvaged evidence."""

    def test_partial_survives_the_deadline_kill(self):
        # a.txt matches instantly; b.txt is catastrophic backtracking that no
        # Python-level hook can interrupt, so it burns the whole remaining
        # budget INSIDE one compiled.search() call. That is precisely why the
        # first match is flushed immediately instead of on the interval.
        records = [
            ("a.txt", "a.txt", b"aaaa\n"),
            ("b.txt", "b.txt", b"a" * 40 + b"!\n"),
        ]
        with self.assertRaises(rw.RegexSearchTimeout) as ctx:
            rw.search_records(
                r"^(a+)+$", 0, records,
                max_results=50, context_lines=0, deadline_seconds=3.0,
            )
        err = ctx.exception
        self.assertEqual([row["file"] for row in err.partial_results], ["a.txt"])
        self.assertEqual(err.partial_results[0]["line"], 1)
        self.assertEqual(err.records_scanned, 1)
        self.assertEqual(err.records_total, 2)
        self.assertIsInstance(err.partial_checkpoint_age_ms, int)

    def test_completed_search_is_unaffected(self):
        records = [("a.txt", "a.txt", b"alpha\nneedle\nomega\n")]
        rows = rw.search_records(
            "needle", 0, records,
            max_results=50, context_lines=0, deadline_seconds=5.0,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 2)


class AttachPartialTests(unittest.TestCase):
    """_attach_partial must read a checkpoint, and refuse anything it cannot trust."""

    def _salvage(self, record) -> rw.RegexSearchTimeout:
        err = rw.RegexSearchTimeout("t")
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "out.bin")
            with open(path, "wb") as fh:
                pickle.dump(record, fh)
            rw._attach_partial(err, path)
        return err

    def test_partial_record_is_salvaged_with_counters(self):
        err = self._salvage(("partial", _ROWS, 7, 42, time.time()))
        self.assertEqual(err.partial_results, _ROWS)
        self.assertEqual(err.records_scanned, 7)
        self.assertEqual(err.records_total, 42)
        self.assertLess(err.partial_checkpoint_age_ms, 60_000)

    def test_final_ok_record_is_salvaged_too(self):
        # A child that finished in the same instant the parent declared the
        # deadline: throwing its COMPLETE result set away would be this exact bug.
        err = self._salvage(("ok", _ROWS))
        self.assertEqual(err.partial_results, _ROWS)

    def test_garbage_leaves_the_error_untouched(self):
        for record in (("error", "boom"), ("partial", "not-a-list", 1, 2, 3.0), "nope"):
            err = self._salvage(record)
            self.assertEqual(err.partial_results, [], repr(record))

    def test_missing_file_is_not_a_second_error(self):
        err = rw.RegexSearchTimeout("t")
        rw._attach_partial(err, str(Path(tempfile.gettempdir()) / "iss12-absent.bin"))
        self.assertEqual(err.partial_results, [])


class TimeoutPayloadTests(unittest.TestCase):
    """The response half: partials reach the caller through BOTH timeout paths."""

    def _assert_partial_shape(self, payload: dict):
        # The verdict does NOT soften: the scan did not complete.
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error_name"], "REGEX_SEARCH_TIMEOUT")
        self.assertEqual(payload["results"], _ROWS)
        self.assertEqual(payload["count"], 1)
        self.assertIs(payload["partial"], True)
        self.assertIs(payload["truncated_by_deadline"], True)
        self.assertEqual(payload["partial_files_scanned"], 7)
        self.assertEqual(payload["partial_files_total"], 42)
        self.assertEqual(payload["partial_files_remaining"], 35)
        self.assertEqual(payload["ca_next_action"], "use_partial_then_narrow_regex_scope")
        self.assertIn("prove", payload["partial_hint"])
        # A04 contract survives: still actionable, not just informative.
        self.assertTrue(payload["retry_sequence"])
        self.assertIn("payload_example", payload)

    def test_worker_timeout_path_carries_partials(self):
        async def raise_worker_timeout(key, producer, **kwargs):
            raise _mk_timeout(_ROWS, scanned=7, total=42, age_ms=12)

        with tempfile.TemporaryDirectory() as tmp:
            payload = _payload_from(raise_worker_timeout, root=Path(tmp), deadline=0.05)
        self._assert_partial_shape(payload)

    def test_asyncio_timeout_path_reuses_the_salvaged_partials(self):
        # The R39 adaptive retry re-raises a BRAND NEW asyncio.TimeoutError, so
        # the payload must come from the held instance, not from the exception
        # in hand -- otherwise the retry silently discards attempt 1's matches.
        calls: list[int] = []

        async def worker_then_asyncio(key, producer, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise _mk_timeout(_ROWS, scanned=7, total=42, age_ms=12)
            raise asyncio.TimeoutError

        with tempfile.TemporaryDirectory() as tmp:
            payload = _payload_from(worker_then_asyncio, root=Path(tmp), deadline=None)
        self.assertEqual(len(calls), 2)
        self._assert_partial_shape(payload)

    def test_no_partials_keeps_the_old_shape(self):
        # Pins the ReDoS contract: those patterns match nothing, so nothing is
        # added and the existing budget tests keep their exact expectations.
        async def raise_bare(key, producer, **kwargs):
            raise _mk_timeout([])

        with tempfile.TemporaryDirectory() as tmp:
            payload = _payload_from(raise_bare, root=Path(tmp), deadline=0.05)
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error_name"], "REGEX_SEARCH_TIMEOUT")
        self.assertEqual(payload["ca_next_action"], "narrow_or_simplify_regex")
        for key in ("results", "count", "partial", "truncated_by_deadline",
                    "partial_files_scanned", "partial_hint"):
            self.assertNotIn(key, payload)


if __name__ == "__main__":
    unittest.main()
