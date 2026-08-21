"""B024 -- `cache_status` omits `ok` on BOTH paths, which B018 believed it had swept.

B018 fixed `search_code` and recorded, as part of the finding, that "every other
tool surveyed answers `ok` on both paths". That survey listed read_file_range,
git_status, validate_file, list_sessions, cbm_graph_status, regex_search -- and
did not include `cache_status`. Measured against the live daemon at generation
229018dbfe86, over HTTP, on the success path:

    cache_status(project_path=<[A]'s own repo>)
      -> keys: cache_age_hours, cache_exists, cache_files, generation,
               index_exists, last_updated, last_updated_iso, message, status
      -> "ok" NOT in the response

and the same survey re-run correctly (reading `ok` at the top of the HTTP
envelope rather than descending into `result`, which for read_file_range is a
`str`) found `cache_status` to be the ONLY remaining tool of the set missing it:
git_status, read_file_range, cache_delta, cbm_trace_path and query_index all
answer `ok`.

This is B018's defect, in B018's own blind spot. The ordinary caller guard

    if not resp.get("ok"): ...treat as failure...

reads a perfectly good cache report as a failure, because a missing key is falsy
in exactly the same way `False` is. It fails in the safe-looking direction:
nothing errors, no gate denies, the answer is right there in the payload -- the
caller throws it away.

The failure path is worse and is the second half of this bug. `cache_status`
answers its own exception as `{"error": str(exc)}`: no `ok:false`, and no
`error_name`, so a caller cannot distinguish "the cache is fine" from "the check
itself blew up" by any single field. CORE_RULE tells CAs to trust `error_name`;
this path never sets one.

Three things are pinned, and the third is what keeps the field honest:

  1. `ok` is present and True on the success path;
  2. the failure path answers `ok:false` AND names itself with `error_name`; and
  3. `ok` is not merely bolted on -- the rest of the success payload is
     unchanged, and `status` still carries fresh-vs-stale, because `ok` must not
     be overloaded into "the cache is fresh". A stale cache is a successful
     answer to the question that was asked.

Run: ./venv312/Scripts/python.exe -m pytest tests/test_cache_status_ok_field_b024.py -v
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import code_tools as ct
import surgical_mapper


class CacheStatusAnswersOkOnSuccess(unittest.IsolatedAsyncioTestCase):
    """A cache report that ran must say it ran."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="b024_ok_")).resolve()
        (self.root / "keep.py").write_text(
            "def findable_symbol_b024():\n    return 1\n", encoding="utf-8")
        surgical_mapper.generate_project_map(str(self.root))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    async def _status(self) -> dict:
        return json.loads(await ct.cache_status(str(self.root)))

    async def test_a_successful_report_says_ok_true(self) -> None:
        """The exact measured failure: a full cache report with no `ok` in it."""
        p = await self._status()
        self.assertIn(
            "ok", p,
            "cache_status returned a full report (%s) but omitted `ok`, so the "
            "ordinary guard `if not resp.get(\"ok\")` reads it as a failure"
            % sorted(p)[:8])
        self.assertIs(p["ok"], True, p.get("ok"))

    async def test_ok_does_not_mean_the_cache_is_fresh(self) -> None:
        """`ok` answers "did the check run", not "is the cache clean".

        Overloading `ok` into freshness would make a stale cache indistinguishable
        from a broken check -- the same conflation B018 warned about for zero-hit
        searches. Staleness has its own fields: `status`, `stale_warning`,
        `drift_warning`.
        """
        (self.root / "appeared_after_the_map.py").write_text(
            "def drifted_b024():\n    return 2\n", encoding="utf-8")
        p = await self._status()
        self.assertIs(p.get("ok"), True,
                      "a drifted cache is still a successful report; drift is "
                      "carried by status/drift_warning, not by ok")
        self.assertEqual(p.get("status"), "stale",
                         "the drift this test creates must still be reported: %r"
                         % p.get("status"))
        self.assertTrue(p.get("drift_warning"), p)

    async def test_the_rest_of_the_payload_is_untouched(self) -> None:
        """Adding one key must not reshape the report.

        Deliberately not asserted: `generation`. It appears on the live daemon's
        own repo but not on a fresh tmp fixture, so pinning it here would assert
        the environment rather than the fix -- the first cut of this test did
        exactly that and failed on a correct payload.
        """
        p = await self._status()
        for key in ("cache_exists", "index_exists", "status", "message",
                    "last_updated_iso", "cache_files"):
            self.assertIn(key, p, "success payload lost %r" % key)


class CacheStatusFailureNamesItself(unittest.IsolatedAsyncioTestCase):
    """Adding the key must not become 'always say ok'."""

    async def test_a_failure_answers_ok_false_and_names_itself(self) -> None:
        """The `except` branch answered `{"error": ...}` with no ok and no error_name.

        A caller told by CORE_RULE to trust `error_name` had nothing to read, and
        a caller guarding on `ok` could not tell this apart from a clean report.
        """
        boom = RuntimeError("b024 synthetic cache failure")
        original = surgical_mapper.get_cache_status

        def explode(_project_path):
            raise boom

        surgical_mapper.get_cache_status = explode
        try:
            p = json.loads(await ct.cache_status("D:/nonexistent_b024"))
        finally:
            surgical_mapper.get_cache_status = original

        self.assertIs(p.get("ok"), False,
                      "a failed cache check must answer ok=False, got %r" % p.get("ok"))
        self.assertTrue(p.get("error_name"),
                        "a failure must name itself so a CA can branch on it: %r" % p)
        self.assertIn("b024 synthetic cache failure", str(p.get("error", "")),
                      "the failure must still say what went wrong: %r" % p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
