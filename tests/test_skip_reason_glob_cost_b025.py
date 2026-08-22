"""B025 -- `_skip_reason` is the hot path of `search_code`, and it re-`fnmatch`es
56 globs with a fresh `.lower()` on every single file.

Anh reported `search_code` as "the one tool still not optimized". Measured
in-process against [A]'s own repo (1261 indexed files, 17.9MB):

    search_code(query="zzz-khong-ton-tai-b023")  ->  953ms, 0 hits
    search_code(query="openActDialog")           -> 2187ms, 0 hits
    search_code(query="function")                ->  125ms, 3 hits

The reported model -- "fewer hits is slower" -- is real and is NOT a defect: a
0-hit answer pays A03's drift probe on purpose (`orchestrator/tools/search.py`
:1155), because a 0-hit over a stale snapshot is inconclusive, not "absent", and
silently degrading there is what once let a real committed symbol be declared a
phantom. The probe is the feature. What it should not do is cost what it costs.

cProfile on the 0-hit path, 2.032s total:

    1.109s  code_tools.py:3639  _cache_delta        <- A03's drift probe, on purpose
    0.900s  surgical_mapper.py:957  _should_scan_file
    0.898s  surgical_mapper.py:328  _skip_reason     <- 4369 calls
    0.776s  surgical_mapper.py:266  _matches_any     <- 8716 calls
    0.747s  surgical_mapper.py:268  <genexpr>        <- 246761 iterations

246761 `fnmatch` calls for 4369 files. `_matches_any` is
`any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)`, so each file
re-lowercases all 56 patterns (11 SECRET_FILE_GLOBS + 45 SKIP_FILE_GLOBS) and
re-parses each glob into a regex that `fnmatch` caches by *pattern string* but
still dispatches per call. The pattern set is a module constant. Nothing about it
changes between files.

This test pins the fix as an EQUIVALENCE, not a speedup, because the risk here is
not slowness -- it is that a faster classifier silently reclassifies a file.
`_skip_reason` decides what gets indexed AND what gets searched, and its return
value is a *reason string* that callers surface. Three things are pinned, and the
last is the one that matters:

  1. every documented verdict is still produced, with its exact string;
  2. the ORDER of the checks is unchanged -- a `.gitignore` is
     "generated_archive_backup_or_internal_ai" and NOT "unsupported_extension",
     even though both tests would fire, because the generated check runs first
     (an earlier candidate fix reordered them and silently relabelled 227 files);
  3. it is measurably faster on a real repo-shaped sample -- asserted as a
     conservative floor, not a target, so a slower machine does not fail the
     suite.

Run: ./venv312/Scripts/python.exe -m pytest tests/test_skip_reason_glob_cost_b025.py -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import surgical_mapper as sm  # noqa: E402


class EveryVerdictSurvives(unittest.TestCase):
    """The reason strings are surfaced to callers; they are contract, not debug text."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="b025_skip_")).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _reason(self, rel: str) -> str | None:
        fp = self.root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        if not fp.exists():
            fp.write_text("x\n", encoding="utf-8")
        return sm._skip_reason(fp, str(self.root))

    def test_a_plain_source_file_is_not_skipped(self) -> None:
        self.assertIsNone(self._reason("keep_b025.py"))

    def test_secret_like_filename(self) -> None:
        self.assertEqual(self._reason(".env"), "secret_like_filename")

    def test_generated_or_junk(self) -> None:
        self.assertEqual(self._reason("thing_b025.py.bak"),
                         "generated_archive_backup_or_internal_ai")

    def test_unsupported_extension(self) -> None:
        self.assertEqual(self._reason("picture_b025.png"), "unsupported_extension")

    def test_ignored_directory_names_the_directory(self) -> None:
        r = self._reason("node_modules/pkg_b025/index.js")
        self.assertTrue(str(r).startswith("ignored_directory:"), r)
        self.assertIn("node_modules", str(r))

    def test_a_dotfile_is_generated_not_unsupported(self) -> None:
        """Order matters and this is the case that proves it.

        A `.gitignore` has no supported extension AND starts with a dot, so both
        the generated check and the extension check would fire. The generated
        check runs FIRST, so the verdict is
        "generated_archive_backup_or_internal_ai". A candidate optimisation that
        hoisted the cheap extension set-lookup to the front relabelled 227 files
        in [A]'s own repo -- .gitignore, orchestrator.log, pyrightconfig.json.bak
        -- from generated to unsupported. Both are "skip", so nothing broke
        loudly; the reason a caller reads just started lying.
        """
        self.assertEqual(self._reason(".gitignore"),
                         "generated_archive_backup_or_internal_ai")
        self.assertEqual(self._reason("orchestrator_b025.log"),
                         "generated_archive_backup_or_internal_ai")


class TheClassifierAgreesWithItselfOnARealRepo(unittest.TestCase):
    """An equivalence sweep over [A]'s own tree, which is the shape that matters."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = Path(__file__).resolve().parents[1]
        cls.files: list[Path] = []
        for root, _dirs, fns in os.walk(cls.repo):
            for fn in fns:
                cls.files.append(Path(root) / fn)
            if len(cls.files) > 4400:
                break
        cls.files = cls.files[:4400]

    def test_the_sample_is_actually_repo_shaped(self) -> None:
        """A sweep over 3 files proves nothing; fail loud if the walk came up short."""
        self.assertGreater(len(self.files), 500,
                           "walk collected %d files -- too few to be evidence"
                           % len(self.files))

    def test_no_file_changes_verdict(self) -> None:
        """Recomputing twice must agree -- catches state leaking into a memo.

        The first candidate fix memoized the filename-only prefix with
        `lru_cache`. It was correct but bought nothing (4215 of 4400 names are
        distinct), and a memo keyed on the name alone is exactly where a
        path-dependent verdict would leak. This pins determinism whether or not
        a cache is present.
        """
        repo = str(self.repo)
        for fp in self.files:
            a = sm._skip_reason(fp, repo)
            b = sm._skip_reason(fp, repo)
            self.assertEqual(a, b, "non-deterministic verdict for %s" % fp)

    def test_it_is_faster_than_the_naive_glob_loop(self) -> None:
        """A floor, not a target.

        Measured 453ms -> 266ms (1.70x) over this same 4400-file sample on the
        machine that wrote the fix. Asserting 1.70x would make the suite fail on
        slower hardware for no defect, so this asserts only that the fix beats
        the exact loop it replaced -- both timed here, in the same process, on
        the same files. If someone reverts the fix, this is the test that says so.
        """
        import fnmatch

        def naive(file_path: Path) -> bool:
            low = file_path.name.lower()
            return any(fnmatch.fnmatch(low, p.lower())
                       for p in sm.SECRET_FILE_GLOBS) or \
                   any(fnmatch.fnmatch(low, p.lower())
                       for p in sm.SKIP_FILE_GLOBS)

        names = [fp.name for fp in self.files]
        t = time.monotonic()
        for n in names:
            naive(Path(n))
        naive_ms = (time.monotonic() - t) * 1000

        t = time.monotonic()
        for n in names:
            sm._is_secret_file(Path(n)) or sm._is_generated_or_junk(Path(n))
        fixed_ms = (time.monotonic() - t) * 1000

        self.assertLess(
            fixed_ms, naive_ms,
            "the glob classifier is not faster than the loop it replaced: "
            "%.0fms vs naive %.0fms over %d names" % (fixed_ms, naive_ms, len(names)))

    def test_the_glob_sets_still_decide_the_same_names(self) -> None:
        """The regex union must accept exactly what the glob loop accepted."""
        import fnmatch

        for fp in self.files:
            low = fp.name.lower()
            want_secret = any(fnmatch.fnmatch(low, p.lower())
                              for p in sm.SECRET_FILE_GLOBS)
            want_junk = low.startswith(".") or any(
                fnmatch.fnmatch(low, p.lower()) for p in sm.SKIP_FILE_GLOBS)
            self.assertIs(sm._is_secret_file(fp), bool(want_secret),
                          "_is_secret_file disagrees on %r" % fp.name)
            self.assertIs(sm._is_generated_or_junk(fp), bool(want_junk),
                          "_is_generated_or_junk disagrees on %r" % fp.name)


class TheDriftProbeIsNotTheDefect(unittest.TestCase):
    """A03 is load-bearing; speeding up the classifier must not remove it.

    `search_code` pays `cache_delta` ONLY on `count == 0`, on purpose: a 0-hit
    over a possibly-stale snapshot is INCONCLUSIVE, and reporting it as "absent"
    is the defect A03 fixed. This test exists so that "make the 0-hit path fast"
    is never mistaken for "make the 0-hit path skip the probe".
    """

    def test_search_code_still_carries_the_three_state_staleness_field(self) -> None:
        import asyncio
        import json

        from orchestrator.tools import search as S

        repo = str(Path(__file__).resolve().parents[1])
        # 2026-08-22: the sentinel must NOT appear whole in this file. Written as one
        # literal it matched THIS test's own source (search_code scans tests/ too), so
        # the "absent" probe found 1 hit and the assertion below failed on every run --
        # the test was measuring itself, not the 0-hit path it exists to protect.
        absent = "zzz_b025_" + "definitely_not_present" + "_anywhere"
        raw = asyncio.run(S.search_code(
            query=absent, project_path=repo,
            max_results=3))
        d = json.loads(raw)
        self.assertIs(d.get("ok"), True, d)
        self.assertEqual(d.get("count"), 0, d)
        self.assertIn("stale_index", d,
                      "A03's three-state staleness field vanished from a 0-hit "
                      "answer, so 0 hits is indistinguishable from 'absent': %s"
                      % json.dumps(d)[:400])
        self.assertIn(d.get("stale_index"), (True, False, None), d.get("stale_index"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
