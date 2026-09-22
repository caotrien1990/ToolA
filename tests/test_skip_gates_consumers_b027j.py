"""B027j regression test: the four raw-SKIP_DIRS consumers now go through a gate.

Why this exists (measured 2026-09-22 on ToolA):

D2 gave the GLOBAL default layer glob patterns, but it could only ever be a partial
fix: four walks read `SKIP_DIRS` directly and tested it with a raw `in`, so none of
them saw `.orchestrator_ignore` (B027), the overlay globs (B027h) or the new global
globs (D2). They are four more places answering "is this directory junk?" from a
locally re-derived precedence -- the exact drift that already shipped three times as
B027c, B027d and B027i:

  1. `code_tools.backup_project` backup-zip walk          -> name set + shared matcher
  2. `code_tools.run_test_profile` py_compile rglob fallback -> file gate
  3. `code_tools._scan_folder_size_and_assets` size walk   -> name set + shared matcher
  4. `orchestrator_server._scan_filtered` magika walk      -> directory gate

Each is now routed to the gate that fits it, and this file asserts the SAME question
in every one of them. Routing them is also what allowed `%temp%toolq_tests` and
`pytest_tmp_lintfix` -- one project's directory names carried as every project's
global cost -- to be retired from SKIP_DIRS, so the retirement is pinned here too.

Three defects that were never reported are fixed by the routing, and each is pinned
below because a later "simplification" would bring it straight back:

  * the py_compile fallback tested `fp.parts`, which is ABSOLUTE. A project living
    under a parent folder named `build`, `archive` or `logs` matched on that PARENT
    and compiled NOTHING, answering ok:true over zero files.
  * the backup walk dropped every directory whose name merely STARTS with `.git`,
    so `.github` and `.gitlab` never reached the archive -- a backup silently losing
    the CI workflows it was taken to preserve. `.git` is now matched exactly.
  * the size walk was case-sensitive, so `Venv312` was measured as project content.

Conversely the magika walk KEEPS its own `.startswith('.')` test on top of the gate.
The gate carves out `.council`, and inheriting that carve-out would feed every scan
the whole council transcript tree (thousands of .jsonl records). That is a widening,
not a fix, so it is asserted here -- "route it through the gate" reads like the local
test should have been deleted.

Every exclusion below is match-controlled: something that must SURVIVE is asserted in
the same test, so a gate that skipped everything could not pass.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import code_tools  # noqa: E402
from orchestrator import skip_rules  # noqa: E402

# 0.2 MiB: big enough that folder_size_mb (2 decimals) tells counted from skipped.
BLOB = b"x" * 209_715
MODULE_SRC = (
    "import os\n"
    "\n"
    "\n"
    "def probe(value: int) -> int:\n"
    "    return value + len(os.sep)\n"
)


class RetirementTest(unittest.TestCase):
    """The two ToolQ instance names are out of the global EXACT set for good."""

    def setUp(self):
        skip_rules.clear_project_ignore_cache()

    def test_the_two_toolq_literals_left_skip_dirs(self):
        self.assertNotIn("%temp%toolq_tests", skip_rules.SKIP_DIRS)
        self.assertNotIn("pytest_tmp_lintfix", skip_rules.SKIP_DIRS)
        # `logs` shared that line and belongs to no family: it stays.
        self.assertIn("logs", skip_rules.SKIP_DIRS)

    def test_they_are_still_skipped_by_family_in_the_two_walk_gates(self):
        for name in ("%temp%toolq_tests", "%temp%toolq_tests_2", "pytest_tmp_lintfix"):
            with self.subTest(name=name):
                self.assertTrue(skip_rules.should_skip_dir(name))
                self.assertIsNotNone(skip_rules.ignored_directory_name(["x", name]))

    def test_the_name_set_gate_carries_the_family_instead_of_the_instance(self):
        names = skip_rules.effective_skip_dir_names()
        self.assertIn("%temp%*", names)
        self.assertIn("pytest_tmp*", names)
        self.assertNotIn("%temp%toolq_tests", names)
        self.assertNotIn("pytest_tmp_lintfix", names)

    def test_the_families_do_not_sweep_up_a_neighbour(self):
        """Retiring a literal must not have widened anything."""
        self.assertFalse(skip_rules.should_skip_dir("temp_toolq_tests"))  # no leading %
        self.assertFalse(skip_rules.should_skip_dir("pytest_helpers"))
        # A leading dot is not matched by `pytest_tmp*`, so this exact entry stays.
        self.assertIn(".pytest_tmp", skip_rules.SKIP_DIRS)


class SizeWalkTest(unittest.TestCase):
    """Consumer 3: `_scan_folder_size_and_assets`."""

    def setUp(self):
        skip_rules.clear_project_ignore_cache()
        self.addCleanup(skip_rules.clear_project_ignore_cache)

    def _project(self, junk: str | None = None, overlay: str | None = None) -> Path:
        root = Path(tempfile.mkdtemp(prefix="b027j_size_")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "keep.bin").write_bytes(BLOB)
        if junk:
            (root / junk).mkdir(parents=True)
            (root / junk / "junk.bin").write_bytes(BLOB * 2)
        if overlay is not None:
            (root / ".orchestrator_ignore").write_text(overlay, encoding="utf-8")
        skip_rules.clear_project_ignore_cache()
        return root

    def _mb(self, root: Path) -> float:
        with code_tools._project_size_lock:
            code_tools._project_size_cache.clear()
        return code_tools._scan_folder_size_and_assets(root)["folder_size_mb"]

    def test_positive_control_a_plain_subdirectory_is_counted(self):
        """Without this, every 0.2 below could be a walk that measured nothing."""
        self.assertAlmostEqual(self._mb(self._project(junk="src")), 0.6, places=2)

    def test_a_global_exact_name_is_skipped(self):
        self.assertAlmostEqual(self._mb(self._project(junk="node_modules")), 0.2, places=2)

    def test_a_global_glob_family_is_skipped(self):
        """D2's layer had never reached this walk."""
        self.assertAlmostEqual(
            self._mb(self._project(junk="_iso_campaign_fix277")), 0.2, places=2)

    def test_a_retired_literal_is_still_skipped_here(self):
        """The retirement's blast radius: this walk is why the literals had to stay."""
        self.assertAlmostEqual(
            self._mb(self._project(junk="pytest_tmp_lintfix")), 0.2, places=2)

    def test_case_no_longer_decides_whether_a_venv_counts(self):
        self.assertAlmostEqual(self._mb(self._project(junk="Venv312")), 0.2, places=2)

    def test_the_orchestrator_cache_is_still_skipped_without_the_local_union(self):
        """The removed `| {".orchestrator_cache"}` was redundant since CAN-042."""
        self.assertAlmostEqual(
            self._mb(self._project(junk=".orchestrator_cache")), 0.2, places=2)

    def test_a_project_declared_skip_is_honoured(self):
        root = self._project(junk="2.working", overlay="2.working\n")
        self.assertAlmostEqual(self._mb(root), 0.2, places=2)

    def test_a_project_unskip_beats_the_global_set(self):
        """Proves a GATE, not merely a wider blacklist."""
        root = self._project(junk="archive", overlay="!archive\n")
        self.assertIn("archive", skip_rules.SKIP_DIRS)
        self.assertAlmostEqual(self._mb(root), 0.6, places=2)


class PyCompileDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    """Consumer 2: the py_compile rglob fallback in `run_test_profile`."""

    def setUp(self):
        skip_rules.clear_project_ignore_cache()
        self.addCleanup(skip_rules.clear_project_ignore_cache)

    def _project(self, parent: str | None = None, sub: str | None = None,
                 overlay: str | None = None) -> Path:
        base = Path(tempfile.mkdtemp(prefix="b027j_pyc_")).resolve()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        root = (base / parent / "proj") if parent else (base / "proj")
        target = (root / sub) if sub else root
        target.mkdir(parents=True)
        (target / "mod.py").write_text(MODULE_SRC, encoding="utf-8")
        if overlay is not None:
            (root / ".orchestrator_ignore").write_text(overlay, encoding="utf-8")
        skip_rules.clear_project_ignore_cache()
        return root

    async def _run(self, root: Path) -> dict:
        return json.loads(await code_tools.run_test_profile(str(root), "py_compile", 120))

    def _assert_discovered(self, payload: dict):
        self.assertNotEqual(payload.get("error_name"), "NO_TESTS_FOUND", payload)
        self.assertIn("mod.py", str(payload.get("command")), payload)

    def _assert_not_discovered(self, payload: dict):
        self.assertEqual(payload.get("error_name"), "NO_TESTS_FOUND", payload)

    async def test_positive_control_a_normal_project_discovers_its_module(self):
        self._assert_discovered(await self._run(self._project()))

    async def test_a_parent_folder_named_like_a_junk_dir_no_longer_hides_the_project(self):
        """The absolute-parts defect: `fp.parts` matched the PARENT of the project."""
        for parent in ("build", "archive", "logs", "dist"):
            with self.subTest(parent=parent):
                self._assert_discovered(await self._run(self._project(parent=parent)))

    async def test_a_junk_directory_inside_the_project_is_still_skipped(self):
        self._assert_not_discovered(await self._run(self._project(sub="node_modules")))

    async def test_a_global_glob_family_inside_the_project_is_skipped(self):
        self._assert_not_discovered(
            await self._run(self._project(sub="_iso_campaign_fix277")))

    async def test_a_project_unskip_reaches_this_walk(self):
        self._assert_discovered(await self._run(
            self._project(sub="_iso_campaign_fix277", overlay="!_iso_campaign*\n")))

    async def test_a_dot_directory_is_still_compilable(self):
        """The file gate has no dot rule, and `.github/scripts/*.py` must compile."""
        self._assert_discovered(await self._run(self._project(sub=".github")))


class BackupWalkTest(unittest.IsolatedAsyncioTestCase):
    """Consumer 1: the backup-zip walk. A backup's job is to lose nothing."""

    def setUp(self):
        skip_rules.clear_project_ignore_cache()
        self.addCleanup(skip_rules.clear_project_ignore_cache)

    async def _archive_names(self) -> set[str]:
        root = Path(tempfile.mkdtemp(prefix="b027j_bak_")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        dest = Path(tempfile.mkdtemp(prefix="b027j_dest_")).resolve()
        self.addCleanup(shutil.rmtree, dest, ignore_errors=True)

        (root / "keep.py").write_text(MODULE_SRC, encoding="utf-8")
        for rel in (
            ".github/workflows/ci.yml",   # must SURVIVE: the `.git` over-match
            ".claude/settings.json",      # must SURVIVE: no blanket dot rule here
            ".git/config",               # must be dropped
            "node_modules/dep.js",        # global exact
            "_iso_campaign_fix277/copy.py",  # global glob
            "pytest_tmp_lintfix/leak.py",    # retired literal, now a family
            "2.working/scratch.txt",         # project-declared skip
            "archive/kept_by_unskip.py",     # global exact, unskipped by overlay
        ):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x\n", encoding="utf-8")
        (root / ".orchestrator_ignore").write_text("2.working\n!archive\n",
                                                   encoding="utf-8")
        skip_rules.clear_project_ignore_cache()

        payload = json.loads(await code_tools.backup_project(
            str(root), backup_dir=str(dest), force=True))
        archive = payload.get("backup_path")
        self.assertTrue(archive, payload)
        with zipfile.ZipFile(archive) as zf:
            return {name.replace("\\", "/") for name in zf.namelist()}

    async def test_the_backup_walk_keeps_what_it_must_and_drops_what_it_must(self):
        names = await self._archive_names()
        # Positive control first: an empty-ish archive would pass every assertNotIn.
        self.assertIn("keep.py", names)
        # The defect this routing exposed: `.startswith(".git")` over-matched.
        self.assertIn(".github/workflows/ci.yml", names)
        self.assertIn(".claude/settings.json", names)
        # A project unskip must reach a backup too.
        self.assertIn("archive/kept_by_unskip.py", names)
        for dropped in (".git/config", "node_modules/dep.js",
                        "_iso_campaign_fix277/copy.py", "pytest_tmp_lintfix/leak.py",
                        "2.working/scratch.txt"):
            with self.subTest(dropped=dropped):
                self.assertNotIn(dropped, names)


def _magika_available() -> bool:
    try:
        import magika  # noqa: F401
    except Exception:
        return False
    return True


@unittest.skipUnless(_magika_available(), "magika not installed")
class MagikaWalkTest(unittest.IsolatedAsyncioTestCase):
    """Consumer 4: `_scan_filtered`, the one walk that KEEPS its local dot test."""

    def setUp(self):
        skip_rules.clear_project_ignore_cache()
        self.addCleanup(skip_rules.clear_project_ignore_cache)

    async def _scanned(self) -> set[str]:
        import orchestrator_server

        root = Path(tempfile.mkdtemp(prefix="b027j_magika_")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "keep.py").write_text(MODULE_SRC, encoding="utf-8")
        for rel in ("node_modules/dep.py", "_iso_campaign_fix277/copy.py",
                    "pytest_tmp_lintfix/leak.py", "2.working/scratch.py",
                    ".council/transcript.py"):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(MODULE_SRC, encoding="utf-8")
        (root / ".orchestrator_ignore").write_text("2.working\n", encoding="utf-8")
        skip_rules.clear_project_ignore_cache()

        rows = await orchestrator_server._scan_filtered(str(root))
        return {str(row.get("path", "")).replace("\\", "/") for row in rows}

    async def test_the_magika_walk_sees_source_and_not_junk(self):
        paths = await self._scanned()
        self.assertIn("keep.py", paths)  # positive control
        for dropped in ("node_modules/dep.py", "_iso_campaign_fix277/copy.py",
                        "pytest_tmp_lintfix/leak.py", "2.working/scratch.py"):
            with self.subTest(dropped=dropped):
                self.assertNotIn(dropped, paths)

    async def test_the_council_carve_out_is_deliberately_not_inherited(self):
        """`should_skip_dir` allows `.council`; this walk must still refuse it.

        Inheriting the carve-out would add the whole transcript tree to every scan.
        """
        self.assertFalse(skip_rules.should_skip_dir(".council"))
        self.assertNotIn(".council/transcript.py", await self._scanned())


if __name__ == "__main__":
    unittest.main()