"""B027i / D2 regression test: the GLOBAL default skip layer takes GLOB patterns.

Why this exists, measured 2026-09-22 on ToolQ -- a project that ships NO
`.orchestrator_ignore` at all:

`search_code` there still answered out of `_iso_campaign/fix277/*.py`, a full source
COPY holding 304 .py files, so every symbol inside came back as a duplicate hit on the
real file. B027 gave every project an overlay and B027h gave that overlay globs, but a
project shipping no overlay got neither: all three gates in `skip_rules` consulted the
GLOBAL defaults by plain exact membership, so [A]'s own defaults could name an INSTANCE
of a junk tree and never the FAMILY it belongs to. That asymmetry is the D2 defect, and
it had already been paid for twice inside this repo:

  * ToolQ instance names were written into the global `SKIP_DIRS` --
    `%temp%toolq_tests`, `pytest_tmp_lintfix` -- because the families `%temp%*` and
    `pytest_tmp*` were unsayable. One project's directory names became every project's
    cost, which is the exact thing B027 was built to end.
  * `graph/analysis_view.py` grew a SECOND private authority on the same question,
    carrying `_backup`, `_iso_campaign/` and `%temp%` under the comment "(indexer
    SKIP_DIRS does not know them)". That comment was the defect in one line. D2 retires
    the list; `AnalysisViewDelegationTest` below pins the delegation.

A third, separate live defect surfaced while fixing this and is pinned here too:
`indexer._walk_files._prune` tested BOTH overlay sets with a raw `in`, so even a
project that DID ship an overlay glob had it honoured by search and by the file gate
but silently dropped by the graph walk -- the B027c/B027d "the two walks disagree"
failure a third time.

Every skip asserted below is NON-MATCH-CONTROLLED: a sibling name from the same
neighbourhood (`_iso_utils`, `backup_notes`, `temp_files`) is asserted to stay
walkable, so a green run can never be a pattern that quietly matches everything. That
direction matters more here than for an overlay: widening a GLOBAL skip drops
directories out of the index under a success exit code, the CASE-2008 shape.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import skip_rules  # noqa: E402
from orchestrator.graph import analysis_view, indexer  # noqa: E402

# One real INSTANCE per shipped family, spelled the way a walk lowercases it. None of
# these is an exact `SKIP_DIRS` entry, which is the whole point: before B027i every one
# of them was walked, indexed and searched.
ISO = "_iso_campaign_fix277"
BACKUP = "_backup_stabilize_20260901_1423"
TEMP = "%temp%pytest_of_admin"
PYTEST_TMP = "pytest_tmp_ruff_probe"
FAMILY_INSTANCES = (ISO, BACKUP, TEMP, PYTEST_TMP)

# Names from the same neighbourhood that could plausibly BE source. If a pattern ever
# widens onto one of these, the index silently loses real code.
NEAR_MISSES = ("_iso_utils", "backup_notes", "temp_files", "pytest_fixtures")


class GlobalGlobBase(unittest.TestCase):
    def setUp(self):
        skip_rules.clear_project_ignore_cache()

    def _write(self, body: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix="d2_glob_"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".orchestrator_ignore").write_text(body, encoding="utf-8")
        skip_rules.clear_project_ignore_cache()
        return root


class GlobalGlobDefaultsTest(GlobalGlobBase):
    def test_every_family_is_skipped_with_no_overlay_at_all(self):
        """The D2 case: no overlay, no project_root, and the junk tree still goes.

        The one-argument form is asserted on purpose. Unlike the overlay -- which the
        nine pre-B027 call sites must keep ignoring -- these patterns are DEFAULTS, so
        they have to fire for a caller that knows nothing about a project root.
        """
        for name in FAMILY_INSTANCES:
            with self.subTest(name=name):
                self.assertTrue(skip_rules.should_skip_dir(name))

    def test_no_family_pattern_reaches_a_plausible_source_name(self):
        """The fail-shut direction, which is the expensive one for a GLOBAL layer."""
        for name in NEAR_MISSES:
            with self.subTest(name=name):
                self.assertFalse(skip_rules.should_skip_dir(name))
                self.assertIsNone(skip_rules.ignored_directory_name([name, "mod.py"]))

    def test_the_file_gate_agrees_with_the_directory_gate(self):
        """Gate 2. B027c exists because these two disagreed; they must not again."""
        for name in FAMILY_INSTANCES:
            with self.subTest(name=name):
                self.assertEqual(
                    skip_rules.ignored_directory_name(["src", name, "mod.py"]), name)

    def test_the_name_set_gate_carries_the_patterns_through(self):
        """Gate 3 feeds an fnmatch-based subprocess `--exclude`, so a PATTERN works.

        `_vulture_exclude` wraps each entry as `*/entry/*`; passing `_iso_campaign*`
        straight through is what makes the dead-code advisory see the same tree the
        other two gates already skip. A consumer needing exact names only must ask
        per directory -- that contract is written at `effective_skip_dir_names`.
        """
        names = skip_rules.effective_skip_dir_names()
        for pattern in skip_rules.SKIP_DIR_GLOBS:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, names)
        # Strictly additive: the exact set is still the fast path and still present.
        self.assertIn("node_modules", names)
        self.assertIn("venv312", names)

    def test_the_two_toolq_literals_stay_skipped_either_way(self):
        """Behaviour, not membership -- so this survives the cleanup it is waiting on.

        `%temp%toolq_tests` and `pytest_tmp_lintfix` are still exact `SKIP_DIRS`
        entries because four consumers (`code_tools` backup-zip / size / rglob walks
        and the `orchestrator_server` magika walk) test that set by raw membership and
        were never gate-aware. Once those are routed through a gate the literals can
        go, and their families cover them -- asserting the ANSWER rather than the
        entry is what lets that happen without editing this test.
        """
        self.assertTrue(skip_rules.should_skip_dir("%temp%toolq_tests"))
        self.assertTrue(skip_rules.should_skip_dir("pytest_tmp_lintfix"))

    def test_the_exact_and_dot_rules_are_untouched(self):
        self.assertTrue(skip_rules.should_skip_dir("node_modules"))
        self.assertTrue(skip_rules.should_skip_dir(".hidden"))
        self.assertFalse(skip_rules.should_skip_dir("orchestrator"))
        self.assertFalse(skip_rules.should_skip_dir("2.working"))

    def test_a_dotted_pytest_temp_root_is_not_caught_by_the_family(self):
        """`.pytest_tmp` keeps its own exact entry: `pytest_tmp*` has no leading dot.

        Asserting the reason, not just the answer -- the dot rule would hide a broken
        pattern here, so the pattern is measured directly.
        """
        self.assertFalse(
            skip_rules.name_matches_ignore(".pytest_tmp", skip_rules.SKIP_DIR_GLOBS))
        self.assertTrue(skip_rules.should_skip_dir(".pytest_tmp"))


class PrecedenceTest(GlobalGlobBase):
    def test_an_unskip_glob_beats_the_global_glob_layer_in_every_gate(self):
        """A project that disagrees with a default keeps the last word, as with SKIP_DIRS."""
        root = self._write("!_iso_*\n")
        self.assertFalse(skip_rules.should_skip_dir(ISO, root))
        self.assertIsNone(skip_rules.ignored_directory_name([ISO, "mod.py"], root))
        self.assertNotIn("_iso_campaign*", skip_rules.effective_skip_dir_names(root))
        # Precision: the un-skip is scoped, so the other families stay skipped.
        self.assertTrue(skip_rules.should_skip_dir(BACKUP, root))

    def test_an_exact_unskip_also_beats_the_glob_layer(self):
        root = self._write("!%s\n" % ISO)
        self.assertFalse(skip_rules.should_skip_dir(ISO, root))
        # A sibling instance of the SAME family is still skipped: an exact un-skip
        # must not be read as un-skipping the pattern that matched it.
        self.assertTrue(skip_rules.should_skip_dir("_iso_campaign_fix999", root))

    def test_no_global_glob_can_hide_the_council_tree(self):
        """.council is the SOT transcript tree; no layer may reach it."""
        self.assertFalse(skip_rules.should_skip_dir(".council"))
        self.assertIsNone(skip_rules.ignored_directory_name([".council", "x.jsonl"]))
        self.assertNotIn(".council", skip_rules.effective_skip_dir_names())

    def test_an_unreadable_overlay_still_fails_open_onto_the_glob_layer(self):
        """FAILS OPEN means degrade to the global rules -- including the new ones."""
        root = Path(tempfile.mkdtemp(prefix="d2_glob_"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".orchestrator_ignore").mkdir()  # stat() ok, read_text raises
        skip_rules.clear_project_ignore_cache()
        self.assertTrue(skip_rules.should_skip_dir(ISO, root))
        self.assertFalse(skip_rules.should_skip_dir("_iso_utils", root))


class GraphWalkTest(unittest.TestCase):
    """`_walk_files` must answer the same question as the three gates."""

    def setUp(self):
        skip_rules.clear_project_ignore_cache()
        self.root = Path(tempfile.mkdtemp(prefix="d2_walk_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "keep.py").write_text("x = 1\n", encoding="utf-8")

    def _tree(self, dirname: str, filename: str) -> None:
        d = self.root / dirname
        d.mkdir()
        (d / filename).write_text("x = 1\n", encoding="utf-8")

    def _overlay(self, body: str) -> None:
        (self.root / ".orchestrator_ignore").write_text(body, encoding="utf-8")
        skip_rules.clear_project_ignore_cache()

    def _walked(self, **kw) -> set:
        return {Path(p).name for p in indexer._walk_files(str(self.root), **kw)}

    def test_positive_control_the_walk_sees_a_plain_tree(self):
        """Without this, every absence asserted below could be an empty walk."""
        self._tree("pkg", "mod.py")
        walked = self._walked()
        self.assertIn("keep.py", walked)
        self.assertIn("mod.py", walked)

    def test_a_global_glob_prunes_the_tree_from_the_graph_walk(self):
        self._tree(ISO, "iso.py")
        self._tree("_iso_utils", "util.py")
        walked = self._walked()
        self.assertIn("keep.py", walked)
        self.assertNotIn("iso.py", walked)
        self.assertIn("util.py", walked,
                      "a global glob must not prune a plausible source name")

    def test_an_explicit_skip_dirs_still_means_exactly_that_set(self):
        """The globs are DEFAULTS. A caller who names a set gets that set, unchanged."""
        self._tree(ISO, "iso.py")
        walked = self._walked(skip_dirs=set())
        self.assertIn("iso.py", walked)
        self.assertIn("keep.py", walked)

    def test_an_overlay_glob_finally_reaches_the_graph_walk(self):
        """The B027h defect: this walk tested the overlay sets with a raw `in`.

        The pattern below is the one B027h shipped for ToolQ's dated backup trees, so
        it was already honoured by `should_skip_dir` and by `ignored_directory_name`
        while the graph kept indexing the snapshot -- two file sets, one repo.
        """
        self._overlay("backup_*t*z\n")
        self._tree("backup_prebaseurl_20260804t032924z", "snap.py")
        self._tree("backup_notes", "notes.py")
        walked = self._walked()
        self.assertNotIn("snap.py", walked)
        self.assertIn("notes.py", walked)
        self.assertIn("keep.py", walked)

    def test_an_overlay_unskip_glob_beats_the_global_layer_in_the_walk(self):
        self._overlay("!_iso_*\n")
        self._tree(ISO, "iso.py")
        self.assertIn("iso.py", self._walked())

    def test_an_exact_overlay_entry_still_prunes_the_walk(self):
        """Strictly additive here too: B027d's own case must still pass."""
        self._overlay("2.working\n")
        self._tree("2.working", "old.py")
        walked = self._walked()
        self.assertNotIn("old.py", walked)
        self.assertIn("keep.py", walked)


class AnalysisViewDelegationTest(unittest.TestCase):
    """The second authority on "is this junk" is retired, and coverage got WIDER."""

    def test_the_three_junk_prefixes_are_gone_from_the_scope_tuple(self):
        for gone in ("_backup", "_iso_campaign/", "%temp%"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, analysis_view._PROD_EXCLUDE_PREFIXES)

    def test_the_remaining_entries_are_still_production_scope_policy(self):
        """`tests/` and `research/` are real source -- they are simply not production."""
        for kept in ("tests/", "research/", "venv312/"):
            with self.subTest(kept=kept):
                self.assertIn(kept, analysis_view._PROD_EXCLUDE_PREFIXES)
        self.assertTrue(analysis_view._is_excluded_production("tests/test_x.py"))

    def test_the_junk_trees_are_still_excluded_through_skip_rules(self):
        for dirname in (BACKUP, ISO, TEMP):
            with self.subTest(dirname=dirname):
                self.assertTrue(
                    analysis_view._is_excluded_production(dirname + "/mod.py"))
                # The directory call shape used at the DSM boundary.
                self.assertTrue(analysis_view._is_excluded_production(dirname + "/"))

    def test_a_nested_junk_tree_is_now_excluded_too(self):
        """The retired prefixes only ever anchored at the repo root.

        This is the case the band-aid could not express and the reason the delegation
        is not merely a refactor: `orchestrator/graph/_iso_campaign/...` was counted as
        production before.
        """
        self.assertTrue(
            analysis_view._is_excluded_production("orchestrator/graph/" + ISO + "/x.py"))

    def test_real_production_source_is_untouched(self):
        for rel in ("orchestrator_server.py", "code_tools.py",
                    "orchestrator/graph/analysis_view.py",
                    "orchestrator/_iso_utils/helper.py"):
            with self.subTest(rel=rel):
                self.assertFalse(analysis_view._is_excluded_production(rel))

    def test_the_bak_and_baseline_rules_are_untouched(self):
        self.assertTrue(analysis_view._is_excluded_production("code_tools.py.bak"))
        self.assertTrue(analysis_view._is_excluded_production("_baseline_run.py"))


if __name__ == "__main__":
    unittest.main()
