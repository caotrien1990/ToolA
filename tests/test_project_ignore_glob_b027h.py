"""B027h regression test: GLOB patterns in `.orchestrator_ignore`.

Why this exists (measured 2026-09-22 on ToolQ):

B027 gave every project an overlay, but that overlay could only name a directory
EXACTLY -- `_parse_project_ignore` stored a lowercased literal and all three gates
asked `name in patterns`. That left the most common junk shape unsayable: a dated
backup tree. ToolQ carries `q_agent/state/backup_prebaseurl_20260804T032924Z` and
`qsleep/ledger/backup_modelpath_20260804T033537Z` under two different parents, plus
the `_backup_stabilize_*` family, and every one of those names is fresh on every
run. A project could never keep up by listing them, so the only lever left was
appending them to the GLOBAL `SKIP_DIRS` -- exactly the failure B027 was built to
end. The docstring of `effective_skip_dir_names` already wrote that tree as the
glob `_backup_*T*Z`, so the design intended globs; only the matcher was missing.

The fix adds ONE matcher, `name_matches_ignore`, that all three gates call. These
tests pin three separate promises:

  1. a glob entry skips a dated tree in ALL THREE gates, not just the first one,
  2. the change is STRICTLY ADDITIVE -- exact membership is still tested first, so
     an entry carrying no metacharacter (including one whose name contains `[`)
     answers byte-for-byte as it did before B027h,
  3. no glob, not even `*`, can hide `.council`, and a `!` line still wins.
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

# The two real ToolQ names this feature exists for, lowercased the way a walk sees
# them. Kept verbatim rather than simplified so a later edit cannot quietly weaken
# the case down to a name that a much dumber matcher would also pass.
TOOLQ_DATED = "backup_prebaseurl_20260804t032924z"
TOOLQ_DATED_2 = "backup_modelpath_20260804t033537z"


class GlobOverlayBase(unittest.TestCase):
    def setUp(self):
        skip_rules.clear_project_ignore_cache()

    def _write(self, body: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix="b027h_"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".orchestrator_ignore").write_text(body, encoding="utf-8")
        skip_rules.clear_project_ignore_cache()
        return root


class GlobSkipTest(GlobOverlayBase):
    def test_positive_control_the_dated_tree_is_not_skipped_without_an_overlay(self):
        """If this ever fails, every other assert in this file measures nothing."""
        self.assertFalse(skip_rules.should_skip_dir(TOOLQ_DATED))
        self.assertIsNone(skip_rules.ignored_directory_name([TOOLQ_DATED]))
        self.assertNotIn(TOOLQ_DATED, skip_rules.effective_skip_dir_names())

    def test_glob_skips_the_dated_backup_tree_in_the_directory_gate(self):
        root = self._write("backup_*t*z\n")
        self.assertTrue(skip_rules.should_skip_dir(TOOLQ_DATED, root))
        self.assertTrue(skip_rules.should_skip_dir(TOOLQ_DATED_2, root))
        # A sibling that is NOT a dated backup must still be walked: a pattern that
        # matched everything would pass the two asserts above for the wrong reason.
        self.assertFalse(skip_rules.should_skip_dir("backup_notes", root))

    def test_glob_skips_the_dated_backup_tree_in_the_file_gate(self):
        """Gate 2 must agree with gate 1 -- B027c exists because they disagreed."""
        root = self._write("backup_*t*z\n")
        self.assertEqual(
            skip_rules.ignored_directory_name(["q_agent", "state", TOOLQ_DATED], root),
            TOOLQ_DATED)
        self.assertIsNone(
            skip_rules.ignored_directory_name(["q_agent", "state", "backup_notes"],
                                              root))

    def test_glob_reaches_the_name_set_gate_for_a_subprocess_exclude(self):
        """Gate 3 carries the PATTERN through: vulture's --exclude is fnmatch too.

        `_vulture_exclude` wraps every entry as `*/entry/*`, so a glob entry arrives
        as a working exclusion and the dead-code advisory finally sees the overlay.
        """
        root = self._write("backup_*t*z\n_iso_*\n")
        names = skip_rules.effective_skip_dir_names(root)
        self.assertIn("backup_*t*z", names)
        self.assertIn("_iso_*", names)
        # The overlay only ADDS here; the global set is untouched.
        self.assertIn("node_modules", names)

    def test_case_is_irrelevant_because_both_sides_are_lowered(self):
        root = self._write("BACKUP_*T*Z\n")
        self.assertTrue(
            skip_rules.should_skip_dir("Backup_PreBaseUrl_20260804T032924Z", root))


class StrictlyAdditiveTest(GlobOverlayBase):
    def test_an_exact_entry_still_matches_exactly(self):
        root = self._write("2.working\n")
        self.assertTrue(skip_rules.should_skip_dir("2.working", root))
        self.assertFalse(skip_rules.should_skip_dir("2.working.old", root))

    def test_a_bracket_in_a_name_is_still_a_name_not_a_character_class(self):
        """The one way glob support could have broken an overlay that already works.

        This FAILED on the first implementation and is why `_GLOB_META` looks the way
        it does. With `[` counted as a metacharacter, the entry `foo[1]` was read as
        an fnmatch character class and skipped `foo1` too -- skipping MORE than the
        project declared, the fail-shut direction the module's FAILS OPEN note
        rejects. Note that "test exact membership first" does NOT fix this on its own:
        exact-first saves the identical name and still widens onto every other name
        the class happens to match. The actual fix is that `_GLOB_META` holds only
        `*` and `?`, which Windows forbids inside a name, so a glob pattern can never
        collide with a directory that really exists.
        """
        root = self._write("foo[1]\n")
        self.assertTrue(skip_rules.should_skip_dir("foo[1]", root))
        self.assertFalse(skip_rules.should_skip_dir("foo1", root))

    def test_a_character_class_still_works_when_the_pattern_carries_a_star(self):
        """The capability is not lost, only made unambiguous by the `*`."""
        root = self._write("backup_[0-9]*\n")
        self.assertTrue(skip_rules.should_skip_dir("backup_2026_run", root))
        self.assertFalse(skip_rules.should_skip_dir("backup_notes", root))

    def test_one_argument_calls_still_ignore_every_overlay(self):
        """The nine pre-B027 call sites must not change answer because of a glob."""
        self._write("backup_*t*z\n*\n")
        self.assertFalse(skip_rules.should_skip_dir(TOOLQ_DATED))
        self.assertTrue(skip_rules.should_skip_dir("node_modules"))
        self.assertTrue(skip_rules.should_skip_dir(".hidden"))

    def test_an_unreadable_overlay_still_fails_open(self):
        """FAILS OPEN: degrade to the global set, never start skipping."""
        root = Path(tempfile.mkdtemp(prefix="b027h_"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        # A DIRECTORY where the overlay file belongs: stat() succeeds, read_text
        # raises, and the answer must fall back rather than guess.
        (root / ".orchestrator_ignore").mkdir()
        skip_rules.clear_project_ignore_cache()
        self.assertFalse(skip_rules.should_skip_dir(TOOLQ_DATED, root))
        self.assertTrue(skip_rules.should_skip_dir("node_modules", root))


class UnskipAndCouncilTest(GlobOverlayBase):
    def test_an_unskip_glob_beats_a_skip_glob(self):
        root = self._write("backup_*\n!backup_keep*\n")
        self.assertTrue(skip_rules.should_skip_dir(TOOLQ_DATED, root))
        self.assertFalse(skip_rules.should_skip_dir("backup_keep_me", root))

    def test_an_unskip_glob_removes_a_global_entry_from_every_gate(self):
        """The half a global set can never express, now reachable by pattern."""
        root = self._write("!arch*\n")
        self.assertIn("archive", skip_rules.SKIP_DIRS)
        self.assertFalse(skip_rules.should_skip_dir("archive", root))
        self.assertIsNone(skip_rules.ignored_directory_name(["archive", "x.py"], root))
        names = skip_rules.effective_skip_dir_names(root)
        self.assertNotIn("archive", names)
        # Precision: `arch*` does not match `_archive`, so that one stays skipped.
        self.assertTrue(skip_rules.should_skip_dir("_archive", root))
        self.assertIn("_archive", names)

    def test_an_unskip_glob_beats_the_leading_dot_rule(self):
        root = self._write("!.venv_*\n")
        self.assertIn(".venv_bge_m3", skip_rules.SKIP_DIRS)
        self.assertFalse(skip_rules.should_skip_dir(".venv_bge_m3", root))

    def test_no_glob_can_hide_the_council_tree(self):
        """`.council` is the SOT transcript tree; a pattern must not reach it."""
        for body in ("*\n", ".coun*\n", ".council\n", "?council\n"):
            with self.subTest(body=body):
                root = self._write(body)
                self.assertFalse(skip_rules.should_skip_dir(".council", root))
                self.assertIsNone(skip_rules.ignored_directory_name([".council"], root))
                self.assertNotIn(".council",
                                 skip_rules.effective_skip_dir_names(root))

    def test_the_matcher_accepts_a_plain_set_not_only_a_frozenset(self):
        """It is the shared entry point, so it must not depend on the caller's type."""
        self.assertTrue(skip_rules.name_matches_ignore("backup_x_z", {"backup_*z"}))
        self.assertFalse(skip_rules.name_matches_ignore("backup_x", {"backup_*z"}))
        self.assertFalse(skip_rules.name_matches_ignore("anything", set()))
        self.assertTrue(skip_rules.name_matches_ignore("exact", frozenset({"exact"})))


if __name__ == "__main__":
    unittest.main()
