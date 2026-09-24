# -*- coding: utf-8 -*-
"""B031: nine test modules leak a `tempfile.mkdtemp()` directory per test, forever.

Measured 2026-08-23, on %TEMP% (`C:\\Users\\ADMIN\\AppData\\Local\\Temp`):

    b02* campaign dirs      3046 dirs,   3,322,170 bytes
    C:\\w7tmp                 259 roots, 227,200,767 bytes
    D:\\w7tmp                 598 roots, 833,725,483 bytes

Those were deleted. The count came straight back: one full suite regrew 49 `b02*`
directories, 48 of them non-empty. So the 3046 were not history -- they are the
accumulated output of a live leak, one run at a time.

Attributing it needed a private TMP, because %TEMP% is shared with every other
tool on the machine and cannot tell whose garbage is whose. Running the nine
modules with `TMPDIR`/`TEMP`/`TMP` pointed at an empty directory gave the number
directly:

    119 passed in 6.00s   ->   99 leftover directories

`tempfile.mkdtemp()` is the whole cause: unlike `TemporaryDirectory`, it has no
context manager and no finaliser, so the directory outlives the process by
contract. 45 of the repo's 54 mkdtemp-using test modules already pair it with
`addCleanup`/`rmtree`; these nine did not.

Why a test and not a one-time `rmtree`: deleting the 3046 dirs fixed nothing --
they regrew on the very next suite. Only a check that FAILS when a module leaks
keeps the next test author from adding number ten. This file measures the real
thing -- it runs the suite in a sandboxed TMP and counts what is left behind --
rather than grepping for the string `addCleanup`, because a call that is present
but never reached (wrong scope, early return, exception before it) greps clean and
still leaks. B029's lesson at the temp-directory layer.

Note on `pytest-of-ADMIN`: it grew to 5613 files / 53,538,849 bytes but is NOT in
scope here. pytest garbage-collects its own basetemp, keeping the last three runs;
measured after a full suite it held exactly `pytest-0/1/2` plus the `pytest-current`
symlink. That is bounded by design, and A54 already pins that the stale symlink
must be tolerated rather than removed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The nine modules measured as leaking. Named explicitly, not globbed: a glob would
# silently start covering new modules and turn a targeted contract into a slow
# whole-suite run.
LEAK_SUSPECTS = (
    "test_onloop_session_offload.py",
    "test_p4_soft_data.py",
    "test_project_ignore_b027.py",
    "test_project_ignore_file_gate_b027c.py",
    "test_project_ignore_graph_walk_b027d.py",
    "test_project_ignore_search_gate_b027f.py",
    "test_project_ignore_snapshot_b027b.py",
    "test_search_deprecated_field_compat.py",
    "test_usage_log_tmp_orphan_b030.py",
)


def _run_isolated(module_names: tuple[str, ...], sandbox: Path) -> tuple[int, list[str], str]:
    """Run `module_names` with TMP redirected into `sandbox`; return leftovers.

    The redirect is the entire method. `tempfile.mkdtemp()` with no `dir=` resolves
    its target through `tempfile.gettempdir()`, which reads TMPDIR/TEMP/TMP at call
    time -- so a child process with those pointed at an empty directory writes every
    unqualified temp directory there and nowhere else. Anything still present when
    the child exits was leaked by the child, with no ambiguity about ownership.

    pytest's own basetemp is excluded from the count: it is bounded (three runs) and
    cleaned by pytest itself, so counting it would report a leak that is not one.
    """
    env = dict(os.environ)
    env["TMPDIR"] = str(sandbox)
    env["TEMP"] = str(sandbox)
    env["TMP"] = str(sandbox)
    env["ORCHESTRATOR_PARALLEL_INDEX"] = "off"
    env["PYTHONIOENCODING"] = "utf-8"

    # These two MUST be cleared, not inherited. conftest.pys pytest_configure
    # only mkdtemps a default when they are unset, so a child that inherits them
    # from this pytest process skips that branch -- and the guard would then be
    # blind to conftests own leak. Measured: 0 leftovers with them inherited,
    # 2 leftovers (orch_pytest_sessions_*, orch_pytest_generation_*) without.
    # Clearing them is also what a developer running one module by hand does.
    env.pop("ORCHESTRATOR_SESSIONS_DB", None)
    env.pop("ORCHESTRATOR_GENERATION_FILE", None)

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "-q",
         *[str(ROOT / "tests" / name) for name in module_names]],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    leftovers = sorted(
        child.name for child in sandbox.iterdir()
        if not child.name.startswith("pytest-of-")
    )
    return proc.returncode, leftovers, (proc.stdout or "") + (proc.stderr or "")


class TheSuiteMustNotLeaveTempDirectoriesBehindTest(unittest.TestCase):
    """One measurement, run once: the nine modules together, in a private TMP."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox = Path(tempfile.mkdtemp(prefix="b031_sandbox_"))
        cls.rc, cls.leftovers, cls.output = _run_isolated(LEAK_SUSPECTS, cls.sandbox)

    @classmethod
    def tearDownClass(cls):
        # This file may not leak either. Its own sandbox goes, whatever the verdict.
        import shutil
        shutil.rmtree(cls.sandbox, ignore_errors=True)

    def test_the_suspect_modules_still_pass(self):
        """A cleanup that breaks the tests it cleans up after is not a fix."""
        self.assertEqual(self.rc, 0, self.output[-3000:])

    def test_nothing_is_left_in_the_private_temp(self):
        """Measured before the fix: 99 directories left by 119 tests."""
        self.assertEqual(
            self.leftovers, [],
            "%d temp directories leaked; first 15: %r"
            % (len(self.leftovers), self.leftovers[:15]))


class TempDirectoriesAreRemovedNotJustRegisteredTest(unittest.TestCase):
    """The per-module direction, so a failure names the culprit instead of a count.

    Splitting this from the aggregate matters for diagnosis: the test above tells you
    the suite leaks, this one tells you WHICH module, and a module that starts leaking
    later fails here by name.
    """

    def test_each_module_cleans_up_after_itself(self):
        failures = []
        for name in LEAK_SUSPECTS:
            sandbox = Path(tempfile.mkdtemp(prefix="b031_one_"))
            try:
                rc, leftovers, output = _run_isolated((name,), sandbox)
                if rc != 0:
                    # Carry the tail of the output: a module that fails here is
                    # usually failing for a reason unrelated to temp dirs, and a
                    # bare rc tells the next reader nothing.
                    failures.append("%s: rc=%d\n%s" % (name, rc, output[-1500:]))
                elif leftovers:
                    failures.append("%s: %d left (%r)"
                                    % (name, len(leftovers), leftovers[:4]))
            finally:
                import shutil
                shutil.rmtree(sandbox, ignore_errors=True)
        self.assertEqual(failures, [], "\n".join(failures))


# #34: the probe a child run executes. Skipped everywhere else -- it acts only when
# the parent below names the entries to create through the environment.
_B031_PROBE_ENV = "B031_CONFTEST_PROBE_NAMES"


class ConftestTempProbe(unittest.TestCase):
    """Child-only: create what another process, and a leaking test, would create."""

    def test_probe_creates_a_foreign_file_and_a_leaked_directory(self):
        names = os.environ.get(_B031_PROBE_ENV, "")
        if not names:
            self.skipTest("child-only probe; run by ConftestDeletesOnlyWhatItCreatedTest")
        foreign, leaked_prefix = names.split("|", 1)
        # A file another process drops into %TEMP% while the suite runs ...
        (Path(tempfile.gettempdir()) / foreign).write_text("foreign", encoding="utf-8")
        # ... and a directory a test forgets to remove: the B031 leak shape.
        tempfile.mkdtemp(prefix=leaked_prefix)


class ConftestDeletesOnlyWhatItCreatedTest(unittest.TestCase):
    """#34: conftest's teardown removes its own registered directories and nothing else.

    Measured before the fix: `pytest_unconfigure` removed every %TEMP% entry that was
    missing from a start-of-run snapshot, so BOTH entries the probe makes were deleted.
    The foreign file is how a CA's scratch disappeared mid-run; the leaked directory is
    why B031/B032 could not fail -- the sweep removed the very leftovers they count.
    """

    @classmethod
    def setUpClass(cls):
        import uuid
        from unittest import mock

        cls.sandbox = Path(tempfile.mkdtemp(prefix="b031_probe_"))
        tag = uuid.uuid4().hex[:8]
        cls.foreign = f"zz_foreign_{tag}.txt"
        cls.leaked_prefix = f"b031_leak_probe_{tag}_"
        node = "test_temp_dir_leak_b031.py::ConftestTempProbe"
        with mock.patch.dict(os.environ,
                             {_B031_PROBE_ENV: f"{cls.foreign}|{cls.leaked_prefix}"}):
            cls.rc, cls.leftovers, cls.output = _run_isolated((node,), cls.sandbox)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.sandbox, ignore_errors=True)

    def test_the_probe_ran(self):
        self.assertEqual(self.rc, 0, self.output[-3000:])
        self.assertIn("1 passed", self.output, self.output[-3000:])

    def test_a_foreign_temp_entry_survives_the_run(self):
        self.assertIn(self.foreign, self.leftovers)

    def test_a_leaking_test_stays_visible_to_the_guard(self):
        self.assertTrue(any(n.startswith(self.leaked_prefix) for n in self.leftovers),
                        self.leftovers)

    def test_conftest_still_removes_its_own_directories(self):
        own = [n for n in self.leftovers if n.startswith("orch_pytest_")]
        self.assertEqual(own, [], own)


if __name__ == "__main__":
    unittest.main()
