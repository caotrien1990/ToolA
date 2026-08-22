"""B027 regression test: per-project ignore via `.orchestrator_ignore`.

Why this exists (measured 2026-08-22, verified against real code):

`SKIP_DIRS` is a single global set shared by every project [A] touches. There was
NO way to tell [A] "this one repo has a huge non-source tree", so the only lever
anybody had was to append that repo's directory name to the global set. That is
how `runs`, `queue`, `archive`, `_archive`, `backups`, `staging`, `.codex`,
`.venv_bge_m3` and `.pytest_tmp` got in -- each was a real, measured decision
(R82: 31608 files scanned, 31079 of them .json runtime records, ~591s of analyze,
so INDEX.json never got written), but the cost lands on EVERY project. A repo
with a legitimate `build/` or `dist/` source directory cannot opt out, and a repo
with a 230 MB `2.working/` tree cannot opt in without taking those names global.

The fix keeps the global set exactly as it is and adds a per-project overlay read
from `<project_root>/.orchestrator_ignore`. `should_skip_dir(name)` keeps its
one-argument form -- 9 call sites pass only a name -- and gains an OPTIONAL
`project_root`. Callers that have a root (every os.walk site does) pass it and
get the overlay; callers that do not behave exactly as before.

Both directions are supported on purpose, because the defect had two halves:
  - `name` / `dir/` adds a directory to the skip set for this project only.
  - `!name` REMOVES a global entry for this project only, which is the half a
    global set can never express. A JS repo whose real source lives in `build/`
    was unreachable before this.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import skip_rules  # noqa: E402


class ProjectIgnoreParseTest(unittest.TestCase):
    def setUp(self):
        skip_rules.clear_project_ignore_cache()

    def _write(self, body: str) -> Path:
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="b027_"))
        (root / ".orchestrator_ignore").write_text(body, encoding="utf-8")
        return root

    def test_a_named_directory_is_skipped_for_this_project_only(self):
        """The whole point: one repo opts in without touching the global set."""
        root = self._write("2.working\n1.footage\n")
        self.assertTrue(skip_rules.should_skip_dir("2.working", root))
        self.assertTrue(skip_rules.should_skip_dir("1.footage", root))
        # Same names, no overlay -> unchanged global behaviour.
        self.assertFalse(skip_rules.should_skip_dir("2.working"))
        self.assertFalse(skip_rules.should_skip_dir("1.footage"))
        # And the global set itself must not have been mutated.
        self.assertNotIn("2.working", skip_rules.SKIP_DIRS)

    def test_bang_prefix_unskips_a_global_entry(self):
        """The half a global set cannot express: a repo whose source is in build/."""
        self.assertTrue(skip_rules.should_skip_dir("build"))
        root = self._write("!build\n")
        self.assertFalse(skip_rules.should_skip_dir("build", root))
        # Other global entries are untouched by the negation.
        self.assertTrue(skip_rules.should_skip_dir("node_modules", root))

    def test_a_bang_entry_can_reach_a_dot_directory(self):
        """`.venv_bge_m3` is skipped twice over (SKIP_DIRS *and* the dot rule), so an
        un-skip has to beat both or the escape hatch does not work on dot names."""
        root = self._write("!.venv_bge_m3\n")
        self.assertFalse(skip_rules.should_skip_dir(".venv_bge_m3", root))

    def test_comments_blank_lines_and_trailing_slashes_are_tolerated(self):
        root = self._write(
            "# a comment\n"
            "\n"
            "   \n"
            "BIN/\n"
            "  spaced  \n"
            "; not a comment character, this is a literal name\n"
        )
        self.assertTrue(skip_rules.should_skip_dir("BIN", root))
        self.assertTrue(skip_rules.should_skip_dir("spaced", root))
        self.assertFalse(skip_rules.should_skip_dir("# a comment", root))

    def test_matching_is_case_insensitive_like_the_global_set(self):
        root = self._write("2.WORKING\n")
        self.assertTrue(skip_rules.should_skip_dir("2.working", root))
        self.assertTrue(skip_rules.should_skip_dir("2.Working", root))

    def test_a_missing_file_is_not_an_error_and_changes_nothing(self):
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="b027_none_"))
        self.assertFalse(skip_rules.should_skip_dir("2.working", root))
        self.assertTrue(skip_rules.should_skip_dir("node_modules", root))
        self.assertEqual(skip_rules.load_project_ignore(root), (frozenset(), frozenset()))

    def test_an_unreadable_or_binary_file_fails_open_not_shut(self):
        """A broken ignore file must not start skipping (or un-skipping) at random.

        Failing SHUT here would silently drop directories from the index, which is
        the CASE-2008 shape: quiet data loss. Failing OPEN degrades to the global
        set, which is the behaviour that shipped for months.
        """
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="b027_bin_"))
        (root / ".orchestrator_ignore").write_bytes(b"\xff\xfe\x00\x00garbage\x00")
        skips, unskips = skip_rules.load_project_ignore(root)
        self.assertIsInstance(skips, frozenset)
        self.assertIsInstance(unskips, frozenset)
        self.assertTrue(skip_rules.should_skip_dir("node_modules", root))

    def test_dot_dirs_searchable_still_wins(self):
        """`.council` is the SOT tree; an ignore file must not be able to hide it,
        because every agent's transcript lives there and a hidden .council reads as
        'the council never spoke'."""
        root = self._write(".council\n")
        self.assertFalse(skip_rules.should_skip_dir(".council", root))

    def test_a_relative_root_resolves(self):
        root = self._write("2.working\n")
        cwd = os.getcwd()
        try:
            os.chdir(root)
            self.assertTrue(skip_rules.should_skip_dir("2.working", "."))
        finally:
            os.chdir(cwd)


class ProjectIgnoreCacheTest(unittest.TestCase):
    def setUp(self):
        skip_rules.clear_project_ignore_cache()

    def test_the_file_is_not_re_read_on_every_directory(self):
        """A walk calls should_skip_dir once per directory -- tens of thousands of
        times on a big repo. Reading a file each time would hand B027 the very cost
        R82 was fixing. The parse is cached per (root, mtime, size)."""
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="b027_cache_"))
        target = root / ".orchestrator_ignore"
        target.write_text("2.working\n", encoding="utf-8")

        reads = {"n": 0}
        real_read = Path.read_text

        def counting_read(self, *a, **kw):
            if self.name == ".orchestrator_ignore":
                reads["n"] += 1
            return real_read(self, *a, **kw)

        Path.read_text = counting_read  # type: ignore[assignment]
        try:
            for _ in range(50):
                skip_rules.should_skip_dir("2.working", root)
        finally:
            Path.read_text = real_read  # type: ignore[assignment]
        self.assertEqual(reads["n"], 1, "ignore file must be parsed once, not per call")

    def test_an_edit_is_picked_up_without_a_restart(self):
        """Cache keyed on content stat, not on 'seen this root once'. A CA that adds
        a directory to .orchestrator_ignore must not have to restart [A] -- that was
        the shape of the stale-snapshot trap already documented in A03."""
        import tempfile
        import time
        root = Path(tempfile.mkdtemp(prefix="b027_edit_"))
        target = root / ".orchestrator_ignore"
        target.write_text("aaa\n", encoding="utf-8")
        self.assertTrue(skip_rules.should_skip_dir("aaa", root))
        self.assertFalse(skip_rules.should_skip_dir("bbb", root))
        time.sleep(0.01)
        target.write_text("bbb\n", encoding="utf-8")
        self.assertTrue(skip_rules.should_skip_dir("bbb", root),
                        "an edited ignore file must take effect without a restart")
        self.assertFalse(skip_rules.should_skip_dir("aaa", root))


class GlobalContractTest(unittest.TestCase):
    """CAN-042 pins SKIP_DIRS as a single source. B027 must not break that."""

    def test_surgical_mapper_still_shares_the_identical_set(self):
        import surgical_mapper
        self.assertIs(surgical_mapper.SKIP_DIRS, skip_rules.SKIP_DIRS)

    def test_one_argument_calls_behave_exactly_as_before(self):
        """9 call sites pass only a name. Their behaviour must be byte-identical."""
        for name, expected in [
            ("node_modules", True), ("__pycache__", True), ("runs", True),
            ("archive", True), (".git", True), (".hidden", True),
            (".council", False), ("src", False), ("orchestrator", False),
            ("2.working", False),
        ]:
            with self.subTest(name=name):
                self.assertEqual(skip_rules.should_skip_dir(name), expected)

    def test_re_exported_helper_accepts_the_optional_root(self):
        """`surgical_mapper.should_skip_dir` and `fileops.common.should_skip_dir` are
        the names the walk sites actually call, so the overlay has to reach through
        both wrappers -- not just the leaf module."""
        import tempfile
        import surgical_mapper
        from orchestrator.fileops import common as fo
        root = Path(tempfile.mkdtemp(prefix="b027_reexport_"))
        (root / ".orchestrator_ignore").write_text("2.working\n", encoding="utf-8")
        skip_rules.clear_project_ignore_cache()
        self.assertTrue(surgical_mapper.should_skip_dir("2.working", root))
        self.assertTrue(fo.should_skip_dir("2.working", root))
        self.assertFalse(surgical_mapper.should_skip_dir("2.working"))


class WalkIntegrationTest(unittest.TestCase):
    """The parse being right is not the fix; the WALK has to honour it."""

    def setUp(self):
        skip_rules.clear_project_ignore_cache()

    def test_generate_project_map_skips_an_ignored_tree(self):
        import tempfile
        import surgical_mapper
        root = Path(tempfile.mkdtemp(prefix="b027_walk_"))
        (root / "src").mkdir()
        (root / "src" / "real.py").write_text("def real():\n    return 1\n", encoding="utf-8")
        heavy = root / "2.working"
        heavy.mkdir()
        (heavy / "gen.py").write_text("def gen():\n    return 2\n", encoding="utf-8")

        # Without the overlay both files are in scope.
        scanned = [
            fp for fp in root.rglob("*.py")
            if surgical_mapper._should_scan_file(fp, root)
            and not any(surgical_mapper.should_skip_dir(p, root) for p in fp.relative_to(root).parts[:-1])
        ]
        self.assertEqual(len(scanned), 2, [str(p) for p in scanned])

        (root / ".orchestrator_ignore").write_text("2.working\n", encoding="utf-8")
        skip_rules.clear_project_ignore_cache()
        scanned = [
            fp for fp in root.rglob("*.py")
            if surgical_mapper._should_scan_file(fp, root)
            and not any(surgical_mapper.should_skip_dir(p, root) for p in fp.relative_to(root).parts[:-1])
        ]
        self.assertEqual([p.name for p in scanned], ["real.py"])


class WalkSitesPassTheRootTest(unittest.TestCase):
    """Parsing the overlay correctly is worth nothing if no walk passes a root.

    That gap is the whole reason B027 needed more than a leaf-module change: the
    five os.walk sites and the two publish_snapshot calls each had a root in
    scope but were calling the one-argument form. These assertions are source
    assertions on purpose -- an integration test that walks a real tree per site
    would take seconds and still miss a site nobody wired.
    """

    def test_every_walk_site_forwards_a_root(self):
        expected = {
            "orchestrator/fileops/common.py": "should_skip_dir(d, root)",
            "orchestrator/tools/search.py": "fo.should_skip_dir(d, base)",
            "surgical_mapper.py": "should_skip_dir(d, project_root)",
            "code_tools.py": "surgical_mapper.should_skip_dir(d, root)",
        }
        for rel, needle in expected.items():
            with self.subTest(file=rel):
                body = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
                self.assertIn(needle, body,
                              f"{rel} must forward a project root to should_skip_dir")

    def test_no_walk_site_still_uses_the_bare_one_argument_form(self):
        """A regression guard with teeth: if someone adds a new os.walk and calls
        the one-argument form, the overlay silently does nothing for that path --
        exactly the failure mode B027 exists to remove."""
        import re as _re
        bare = _re.compile(r"should_skip_dir\((?:d|d2|entry\.name|dirs?)\)")
        for rel in ["orchestrator/fileops/common.py", "orchestrator/tools/search.py",
                    "surgical_mapper.py", "code_tools.py"]:
            with self.subTest(file=rel):
                body = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
                self.assertIsNone(bare.search(body),
                                  f"{rel} has a bare should_skip_dir(d) walk call")

    def test_both_publish_snapshot_calls_bind_the_root(self):
        """The snapshot is what search_code actually serves. If its walk disagrees
        with the index scan about which directories exist,
        _validate_index_against_records raises on the set mismatch."""
        for rel in ["surgical_mapper.py", "orchestrator/tools/search.py"]:
            with self.subTest(file=rel):
                body = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
                self.assertIn("functools.partial", body)
                self.assertNotIn("skip_dir=should_skip_dir,", body)
                self.assertNotIn("skip_dir=fo.should_skip_dir,", body)


if __name__ == "__main__":
    unittest.main()
