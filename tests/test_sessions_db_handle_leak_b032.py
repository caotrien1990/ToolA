# -*- coding: utf-8 -*-
"""B032: `rmtree(ignore_errors=True)` over a live sessions.db is a silent no-op.

B031 removed the nine modules that had no cleanup at all. This is the other half:
modules that DO call `shutil.rmtree(..., ignore_errors=True)` in `tearDown` and
still leak, because on Windows the directory holds an open SQLite handle and
`ignore_errors=True` converts that refusal into silence.

Measured 2026-08-24 -- a full suite in a private TMP, after the B031 fix landed:

    2759 passed   ->   41 directories left behind

Every single one of the 41 contained a `sessions.db`. Per-module attribution:

    test_code_tools_cache.py                             10  (+ _orchestrator_backups)
    test_orchestrator_server_contract.py                 12  (ckpt_*, tmp*, opencode)
    test_record_error_resolution_calling_context_b1.py    6  (b1_*)

The mechanism, measured directly rather than inferred:

    store._init_session_db()            -> rmtree SUCCEEDS (no handle yet)
    store._task_checkpoint_save(...)    -> rmtree FAILS, WinError 32
    store.close_writer()                -> rmtree SUCCEEDS

`_init_session_db` alone holds nothing; the process-wide SessionWriter is created
LAZILY on first write (A38), and it is that drain-loop connection which pins the
file. `store.close_writer()` is the existing public teardown for exactly this: it
closes the writer, this thread's cached read connection, and gc.collects the ones
stranded on dead `asyncio.to_thread` workers.

Why an autouse fixture rather than an edit in each tearDown: the leak is a
property of "this test wrote to a temp sessions.db", not of any one module, and a
per-module fix has to be repeated by every future author who isolates the DB. The
fixture makes the release unconditional and free -- `close_writer` on a process
that never opened a writer is a no-op.

Why this test measures instead of grepping for `close_writer`: a call that is
present but never reached greps clean and still leaks. B029's lesson, again.
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

# The three modules measured as leaking a live-handle directory. Named, not
# globbed: a glob would silently widen into a whole-suite run.
DB_HOLDING_MODULES = (
    "test_code_tools_cache.py",
    "test_orchestrator_server_contract.py",
    "test_record_error_resolution_calling_context_b1.py",
)


def _run_isolated(module_names: tuple[str, ...], sandbox: Path) -> tuple[int, list[str], str]:
    """Run `module_names` with TMP redirected into `sandbox`; return leftovers.

    Identical redirect to the B031 guard, and for the same reason: a child whose
    TMPDIR/TEMP/TMP point at an empty directory writes every unqualified temp
    directory there and nowhere else, so ownership of what remains is certain.
    """
    env = dict(os.environ)
    env["TMPDIR"] = str(sandbox)
    env["TEMP"] = str(sandbox)
    env["TMP"] = str(sandbox)
    env["ORCHESTRATOR_PARALLEL_INDEX"] = "off"
    env["PYTHONIOENCODING"] = "utf-8"
    # Cleared, not inherited: conftest only mkdtemps its defaults when these are
    # unset, so inheriting them would hide conftest's own contribution.
    env.pop("ORCHESTRATOR_SESSIONS_DB", None)
    env.pop("ORCHESTRATOR_GENERATION_FILE", None)

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "-q",
         *[str(ROOT / "tests" / name) for name in module_names]],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
    )
    leftovers = sorted(
        child.name for child in sandbox.iterdir()
        if not child.name.startswith("pytest-of-")
    )
    return proc.returncode, leftovers, (proc.stdout or "") + (proc.stderr or "")


class AnOpenSqliteHandleMustNotSurviveTeardownTest(unittest.TestCase):
    """The mechanism, asserted in-process: this is why the directories remained."""

    def test_a_written_sessions_db_pins_its_directory_until_the_writer_closes(self):
        """Without close_writer the rmtree raises; with it, it succeeds.

        Deliberately NOT using ignore_errors here -- the whole bug is that the
        flag hides this exact OSError.
        """
        import shutil

        import code_tools
        from orchestrator.session import store

        saved_store_db = store._SESSIONS_DB
        saved_tools_db = code_tools._SESSIONS_DB
        root = Path(tempfile.mkdtemp(prefix="b032_probe_"))
        try:
            db = root / "sessions.db"
            store._SESSIONS_DB = db
            code_tools._SESSIONS_DB = db
            code_tools._sync_session_db_alias()
            store._init_session_db()
            # A write, not just an init: the SessionWriter is created lazily on
            # first use (A38), and it is the writer that pins the file.
            store._task_checkpoint_save("b032-probe-session",
                                        {"agent_id": "ClauLTN", "task_epoch": 1})

            with self.assertRaises(OSError, msg="expected WinError 32 while the "
                                   "writer still holds sessions.db"):
                shutil.rmtree(root)

            store.close_writer()
            shutil.rmtree(root)  # must now succeed, and without ignore_errors
            self.assertFalse(root.exists())
        finally:
            store.close_writer()
            store._SESSIONS_DB = saved_store_db
            code_tools._SESSIONS_DB = saved_tools_db
            code_tools._sync_session_db_alias()
            shutil.rmtree(root, ignore_errors=True)


class ModulesThatIsolateTheSessionDbMustNotLeakItsDirectoryTest(unittest.TestCase):
    """The measured direction: run each module and require nothing is left."""

    def test_each_db_holding_module_cleans_up_after_itself(self):
        """Measured before the fix: 10 + 12 + 6 directories, every one with a DB."""
        import shutil

        failures = []
        for name in DB_HOLDING_MODULES:
            sandbox = Path(tempfile.mkdtemp(prefix="b032_one_"))
            try:
                rc, leftovers, output = _run_isolated((name,), sandbox)
                if rc != 0:
                    failures.append("%s: rc=%d\n%s" % (name, rc, output[-1500:]))
                elif leftovers:
                    failures.append("%s: %d left (%r)"
                                    % (name, len(leftovers), leftovers[:6]))
            finally:
                shutil.rmtree(sandbox, ignore_errors=True)
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
