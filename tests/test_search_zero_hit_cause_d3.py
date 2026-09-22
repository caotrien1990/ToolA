"""D3: a 0-hit search_code must name WHICH of its two causes fired.

Root defect (2026-09-22): search_code answered ok:true / results:[] identically for
two unrelated situations --

  (a) the glob admitted files and none of them contained the query -> a real absence
  (b) the glob admitted NO file at all -> the query never ran against anything

-- and then attached whichever guess happened to fire. Measured on ToolQ:

    search_code(query='clm_qsleep_session',
                file_pattern='tests/test_qsleep_session_pause_54.py')

answered ok:true with results:[] and no cause, so the caller had to GUESS. One CA
guessed right ('that test file does not exist yet'); the identical payload reads just
as well as 'the code is missing', which sends the next CA off to rewrite code that was
never broken. Worse, the advisories that DID fire named the wrong remedy: the
stale-index probe said refresh_or_narrow (an O(repo) reindex cannot make a typo'd path
appear) and the multi-word tip blamed the query shape (the query was fine).

Fix: the producer counts the files the glob ADMITS, and cause (b) is reported
directly. Reporting it also SKIPS the drift probe, so the honest answer is the cheap
one -- that saving is asserted here too, because an advisory that costs a full repo
hash on every typo is a regression even when its text is correct.

Every zero asserted below is POSITIVE-CONTROLLED: the same fixture, same query and a
glob that DOES match are asserted to return hits, so a green test can never be a
broken fixture reporting an empty index.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import code_tools
import surgical_mapper
from orchestrator.tools import search as search_module


def _build_project(root: Path) -> None:
    surgical_mapper.generate_project_map(str(root))


def _clear_drift_caches() -> None:
    try:
        code_tools._RECENT_REFRESH_TS.clear()
    except Exception:
        pass
    try:
        with code_tools._CACHE_DELTA_FUTURES_LOCK:
            code_tools._CACHE_DELTA_FUTURES.clear()
    except Exception:
        pass


class TestSearchZeroHitCauseD3(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # basetemp lives OUTSIDE the repo so fixtures never inherit the repo ruff.toml.
        self.root = Path(tempfile.mkdtemp(prefix="d3_zero_cause_")).resolve()
        (self.root / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
        (self.root / "target.py").write_text(
            "def alpha_beta_marker():\n    return 'PRESENT'\n", encoding="utf-8"
        )
        _build_project(self.root)
        _clear_drift_caches()

    def tearDown(self) -> None:
        _clear_drift_caches()
        shutil.rmtree(self.root, ignore_errors=True)

    async def _search(self, query: str, **kw) -> dict:
        raw = await search_module.search_code(
            query=query,
            path=str(self.root),
            project_path=str(self.root),
            mode=kw.pop("mode", "text"),
            max_results=kw.pop("max_results", 20),
            **kw,
        )
        return json.loads(raw)

    # ---- positive controls: the fixture really is searchable ----------------

    async def test_positive_control_matching_glob_finds_the_symbol(self) -> None:
        """Without this, every zero below could be an empty index instead of a cause."""
        payload = await self._search("alpha_beta_marker", file_pattern="target.py")
        self.assertGreaterEqual(payload["count"], 1)
        self.assertNotIn(
            "file_pattern_matched_files", payload,
            "the zero-file cause must never be attached to a hit-bearing answer",
        )

    async def test_real_absence_inside_matched_files_keeps_the_old_path(self) -> None:
        """Glob admits a real file, query genuinely absent -> cause (a), untouched."""
        payload = await self._search("definitely_absent_token_qqq", file_pattern="target.py")
        self.assertEqual(payload["count"], 0)
        self.assertNotIn(
            "file_pattern_matched_files", payload,
            "the glob DID admit target.py, so blaming the glob would be a false cause",
        )
        # The A03 staleness lane still owns this case and still measures it.
        self.assertIn("stale_index", payload)

    # ---- the defect ---------------------------------------------------------

    async def test_glob_matching_no_file_names_its_own_cause(self) -> None:
        """Cause (b) must be stated, not left for the caller to guess."""
        payload = await self._search(
            "alpha_beta_marker", file_pattern="tests/no_such_file_zzz.py"
        )
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload.get("file_pattern_matched_files"), 0)
        self.assertGreaterEqual(int(payload.get("indexed_files_scanned") or 0), 1)
        self.assertIs(payload.get("inconclusive"), True)
        self.assertEqual(payload.get("ca_next_action"), "fix_file_pattern_or_drop_it")
        tip = str(payload.get("tip") or "")
        self.assertIn("no_such_file_zzz.py", tip, "the tip must echo the failing pattern")
        self.assertIn("file_pattern", tip)

    async def test_drift_probe_is_skipped_when_the_glob_admits_nothing(self) -> None:
        """The cheap answer is part of the fix: no O(repo) hash for a typo'd path."""
        payload = await self._search(
            "alpha_beta_marker", file_pattern="tests/no_such_file_zzz.py"
        )
        self.assertNotIn(
            "stale_index", payload,
            "a nonexistent path is deterministic -- hashing the repo cannot change it",
        )
        self.assertNotIn("stale_files_count", payload)
        self.assertNotIn("stale_index_reason", payload)

    async def test_multi_word_tip_never_overwrites_the_measured_cause(self) -> None:
        """A multi-word query must not relabel a glob that admitted nothing.

        This is the exact mis-attribution that shipped: the query-shape guess fired on
        a query that was never the problem, and 'try regex OR-split' is unreachable
        advice when no file was read at all.
        """
        payload = await self._search(
            "alpha beta marker", file_pattern="tests/no_such_file_zzz.py"
        )
        self.assertEqual(payload.get("file_pattern_matched_files"), 0)
        tip = str(payload.get("tip") or "")
        self.assertNotIn("multi-word", tip)
        self.assertNotEqual(payload.get("ca_next_action"), "retry_with_regex_or_symbol_mode")

    async def test_multi_word_tip_still_fires_on_a_genuine_absence(self) -> None:
        """The guard must narrow the old tip, not delete it."""
        payload = await self._search("absent_aaa absent_bbb", file_pattern="target.py")
        self.assertEqual(payload["count"], 0)
        self.assertNotIn("file_pattern_matched_files", payload)
        self.assertIn("multi-word", str(payload.get("tip") or ""))


if __name__ == "__main__":
    unittest.main()
