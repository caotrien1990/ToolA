"""Repo-wide pytest configuration.

campaign-1608 (A51-b): neutralise terminal-colour environment variables for the
whole test session.

Why this exists. `_py_error_ruff_hint` extracts ruff rule codes out of ruff's
text output with `re.search(rf"(?<![A-Z0-9]){rule}(?![A-Z0-9])", ..., re.IGNORECASE)`.
Under `re.IGNORECASE` the `[A-Z0-9]` class also matches lowercase letters, and
every ANSI SGR escape ends in one (`\x1b[31m` ends in `m`). So a coloured
`\x1b[31mF401` has `m` immediately before `F401`, the negative lookbehind fires,
and EVERY rule match is suppressed -- silently, and toward "clean": `ok` stays
true, `lint_warning` still lists the errors, but `ruff_rules_matched` comes back
empty and `ruff_fix_applicable` false. A caller reads that as "ruff can't fix
anything here".

That is fixed in production two ways (`--color never` on the ruff argv, plus an
SGR strip before matching). This file guards the MEASUREMENT instead: an agent
harness or CI that exports `FORCE_COLOR` leaks it into every child process, which
is how the campaign's own baseline got polluted in the first place (A46: false
reds attributed to the code). Tests that deliberately exercise coloured input
build the escapes themselves and are unaffected.

`NO_COLOR` alone is NOT enough -- ruff honours `FORCE_COLOR` over `NO_COLOR`, so
the offending variables must be removed, not merely counterbalanced.

--------------------------------------------------------------------------- #

campaign-1608 (A53): keep the test run OUT of the production sessions.db.

`orchestrator/session/store.py` resolves its DB path ONCE, at import:

    _SESSIONS_DB = Path(os.environ.get("ORCHESTRATOR_SESSIONS_DB") or
                        (_project_cache_dir(...) / "sessions.db"))

With that variable unset the fallback is the REAL `.orchestrator_cache/sessions.db`
-- the same file the running daemon owns. Any test that reaches the session layer
without redirecting the path therefore writes live rows into production. This is
not hypothetical: measured on 2026-08-17, `pytest tests/` left permanent
`sess-test`, `cross-process` and `agent-a-session` rows behind, and 26 of the 50
test files that touch the session layer had no redirect at all. The damage is
cumulative and invisible -- those writes are exactly the orphan rows that make
the retention sweep (A22) look like it under-deletes.

Pointing the variable at a per-run temp file fixes all of them at once, because
`pytest_configure` runs BEFORE test modules are imported, so the assignment lands
before `store` computes `_SESSIONS_DB`. Files that already isolate themselves
(monkeypatching `store._SESSIONS_DB` + `code_tools._SESSIONS_DB`) keep working --
this only moves the DEFAULT they fall back to.

It deliberately does NOT delete the temp DB afterwards: the OS reclaims the temp
directory, and keeping the file lets a failing run be inspected. It also does not
override an ORCHESTRATOR_SESSIONS_DB the caller set on purpose.

--------------------------------------------------------------------------- #

W5: honour `TemporaryDirectory(ignore_cleanup_errors=True)` on Windows.

`ignore_cleanup_errors=True` does not do what its name promises. In CPython 3.12
(`tempfile.py:895-933`) the `onexc` handler calls `_resetperms(path)` and only
wraps that call in `except FileNotFoundError`. When `_resetperms` -> `os.chmod`
itself raises `PermissionError` -- the exact WinError 5 you get while Windows is
still holding a handle -- it propagates straight out of `cleanup()`, flag or no
flag. Measured directly (mock `os.chmod`/`os.unlink` to raise WinError 5 on one
file): `cleanup()` raises despite `ignore_cleanup_errors=True`.

That turns a passing test into a red one in TEARDOWN. Measured on
`test_task_epoch_cross_process`: every assertion passed, then teardown died on
`durable_audit.db-shm`; the same test passes 3/3 in isolation, and only fails in
a full run where the just-terminated server child has not yet released the WAL
sidecar. ~40 test files use this same spawn-a-server-then-rmtree shape, so
patching the one file that lost the race would just move the flake.

The retry is what the OS actually needs (the handle IS released, a few hundred ms
late) and the final swallow is what the caller already asked for. Tests that did
NOT pass the flag keep raising, exactly as before -- this closes the gap between
the flag and its behaviour, it does not widen it.

--------------------------------------------------------------------------- #

Same shape as A53, second file: keep the test run OUT of the production
generation file.

``orchestrator_server.py`` publishes ``SERVER_GENERATION`` (a fresh uuid4 per
process) to ``.orchestrator_cache/server_generation.json``, and MCP reads that
file to decide whether the daemon it proxies to has restarted. A test that spawns
a real server therefore rewrites the LIVE daemon identity: MCP sees a generation
it does not recognise and refuses calls with MCP_PROXY_FAILED, and once the child
exits the file is left holding a dead pid and an ephemeral port, so the real
daemon stays un-probeable until its next restart.

Measured on 2026-08-20 during a full-suite run: the file on disk read pid 111348
port 51156 -- a process that no longer existed, on a port the daemon never used
-- while the live daemon was pid 209860 on 8083. A CA session took three
consecutive MCP_PROXY_FAILED responses in that window and the writes had in fact
landed, so the failure was purely the identity check, not the write path.

``tests/test_task_epoch_cross_process.py`` was the specific leak and now isolates
the variable itself. This default is the fence, so the next test to spawn a
server cannot reintroduce the bug by forgetting one env var out of six.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

# Precedence order matters: FORCE_COLOR beats NO_COLOR in ruff, so drop it.
_COLOUR_FORCING_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "PY_COLORS")

_SESSIONS_DB_ENV = "ORCHESTRATOR_SESSIONS_DB"
_GENERATION_FILE_ENV = "ORCHESTRATOR_GENERATION_FILE"

# B031: only the directories THIS conftest created with `mkdtemp` below. A value
# supplied by the caller is never recorded and so never removed -- the A53 rule
# that an explicit target is left alone has to hold for cleanup too, or a run
# pointed at a real DB would have its parent deleted underneath it.
#
# Measured before this: every `pytest <module>` invoked without the two env vars
# already set left exactly 2 directories behind, permanently. That is per pytest
# PROCESS, not per test, which is why it hid under the per-module leak counts --
# the B031 guard runs its children from inside pytest, so they inherited both
# vars and skipped the mkdtemp branch entirely. It only surfaced when a child ran
# with a clean environment.
_B031_OWNED_TEMP_DIRS: list[Path] = []


def pytest_configure(config):  # noqa: ARG001 - pytest hook signature
    for name in _COLOUR_FORCING_VARS:
        os.environ.pop(name, None)
    os.environ["NO_COLOR"] = "1"

    # A53: only fill in a default. An explicit value from the caller wins, so a
    # run that deliberately targets a specific DB is left alone.
    if not os.environ.get(_SESSIONS_DB_ENV):
        run_dir = Path(tempfile.mkdtemp(prefix="orch_pytest_sessions_"))
        _B031_OWNED_TEMP_DIRS.append(run_dir)
        os.environ[_SESSIONS_DB_ENV] = str(run_dir / "sessions.db")

    # Same rule as A53: fill in a default only, so a run that deliberately points
    # at a specific generation file is left alone. Children inherit os.environ,
    # which is what makes one assignment here cover servers spawned by Popen.
    if not os.environ.get(_GENERATION_FILE_ENV):
        gen_dir = Path(tempfile.mkdtemp(prefix="orch_pytest_generation_"))
        _B031_OWNED_TEMP_DIRS.append(gen_dir)
        os.environ[_GENERATION_FILE_ENV] = str(gen_dir / "server_generation.json")


# --- STT-27 (B032): release the process-global SessionWriter after every test ---
#
# The SessionWriter is created lazily on first DB write and its drain-loop sqlite
# conn pins the sessions.db file. On Windows, any test that wrote to a temp
# sessions.db leaves the directory behind because rmtree (even with
# ignore_errors=True) silently fails on the pinned file. Per-test tearDown edits
# are one source of leaks but not the only one: a writer can start mid-test (via
# asyncio.to_thread, dispatch, checkpoint) and never reach any single tearDown.
# An autouse hook covers every path unconditionally -- close_writer on a process
# that never opened a writer is a no-op.
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):  # noqa: ARG001
    yield
    from orchestrator.session import store
    store.close_writer()


# --- W5: make ignore_cleanup_errors=True actually ignore cleanup errors -------

# Windows releases a closed child's SQLite -shm/-wal handle asynchronously. Six
# attempts over ~1.4s of backoff cleared it in every observed case; the swallow
# afterwards is the documented meaning of the flag, not a new leniency.
_CLEANUP_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)

def pytest_unconfigure(config):  # noqa: ARG001 - pytest hook signature
    """Remove only what `pytest_configure` created. Runs after all finalisers.

    The sessions.db handle has to be released FIRST. Windows refuses to unlink an
    open file, and `ignore_errors=True` turns that refusal into silence -- the
    directory was simply left behind (measured: sessions.db still 94208 bytes at
    this point, so the handle was live). `store.close_writer` is the existing
    public teardown for exactly this: it closes the writer loop, this thread's
    cached read connection, and gc.collects the ones stranded on dead
    `asyncio.to_thread` workers.

    Imported here rather than at module scope: conftest is imported before the
    repo's own packages are importable in some invocations, and a collection-time
    ImportError here would take the whole session down.
    """
    if _B031_OWNED_TEMP_DIRS:
        try:
            from orchestrator.session import store

            store.close_writer()
        except Exception:  # pragma: no cover - teardown must never fail a run
            pass
    # #34 (2026-09-25): delete ONLY the directories registered above -- a
    # whitelist. The earlier pass also swept every %TEMP% entry missing from a
    # start-of-run snapshot, i.e. "everything except what existed before", and
    # %TEMP% is shared with every process on the machine: a CA's scratch created
    # while the suite ran was deleted with the suite's (measured: a HEAD baseline
    # tree). The same sweep made the B031/B032 guards unable to fail, because it
    # removed the very leftovers they count. A module that leaks is fixed in that
    # module; this hook never cleans up after it.
    # Windows can release the last SQLite WAL handle just after the first pass,
    # so the registered directories get the same bounded retry as W5 below.
    for delay in (0.0, *_CLEANUP_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        for _owned in _B031_OWNED_TEMP_DIRS:
            shutil.rmtree(_owned, ignore_errors=True)
        _B031_OWNED_TEMP_DIRS[:] = [p for p in _B031_OWNED_TEMP_DIRS if p.exists()]
        if not _B031_OWNED_TEMP_DIRS:
            break
    _B031_OWNED_TEMP_DIRS.clear()


_orig_tmpdir_cleanup = tempfile.TemporaryDirectory.cleanup


def _cleanup_retrying(self):
    """Retry a Windows-blocked rmtree, then honour ignore_cleanup_errors."""
    for delay in _CLEANUP_RETRY_DELAYS:
        try:
            return _orig_tmpdir_cleanup(self)
        except OSError:
            time.sleep(delay)
    try:
        return _orig_tmpdir_cleanup(self)
    except OSError:
        # getattr, not attribute access: the private name is a CPython detail.
        if getattr(self, "_ignore_cleanup_errors", False):
            return None
        raise


tempfile.TemporaryDirectory.cleanup = _cleanup_retrying


# --- A54: a stale `pytest-current` symlink must not abort the whole run --------

# Symptom, measured 2026-08-20: every `pytest tests/` on this box died during
# COLLECTION with
#
#     PermissionError: [WinError 5] Access is denied:
#     '...\Temp\pytest-of-ADMIN\pytest-current'
#
# raised from `_pytest/pathlib.py:359 cleanup_dead_symlinks`, which calls
# `path.exists()` inside a plain `for` loop. On Windows a symlink left behind by a
# killed run can be readable-but-not-stat-able (this one dated from 2026-08-17 and
# `ls` on it also returns EACCES), and `Path.exists()` propagates EACCES rather
# than returning False -- so pytest's own temp-dir GARBAGE COLLECTION takes the
# session down before a single test runs.
#
# This was expensive to misdiagnose. The error names a path under
# `%LOCALAPPDATA%\Temp`, and the previous conclusion was "OneDrive is syncing the
# temp dir" -- so every pytest call grew a `--basetemp` pointing at a fixed folder
# on the D: drive, which moved all test I/O onto a HDD and saturated it badly
# enough to make unrelated folders appear locked in Explorer.
# `%LOCALAPPDATA%` is never OneDrive-redirected; the symlink was always the whole
# story. Fixing it here removes the reason to redirect basetemp at all -- note the
# flag is fine and used deliberately elsewhere, always resolved through
# `tempfile.gettempdir()`; it was the hardcoded drive path that did the damage.
#
# Patching rather than deleting the symlink: deleting is a one-off that the next
# killed run recreates, and this module cannot delete anything on a whim. The
# upstream loop is simply missing an except clause, so supply it -- a dead symlink
# that cannot be stat-ed is exactly the case the function is already trying to
# skip.
try:  # pragma: no cover - import guard for a private pytest module
    from _pytest import pathlib as _pytest_pathlib
except Exception:  # pragma: no cover
    _pytest_pathlib = None

if _pytest_pathlib is not None and hasattr(_pytest_pathlib, "cleanup_dead_symlinks"):
    _orig_cleanup_dead_symlinks = _pytest_pathlib.cleanup_dead_symlinks

    def _cleanup_dead_symlinks_tolerantly(root):
        """Skip symlinks whose target cannot be stat-ed instead of aborting.

        Deliberately narrow: only OSError from the scan is swallowed, and only by
        falling back to a per-entry loop that keeps removing the dead links it CAN
        see. A blanket try/except around the original call would silently stop
        collecting garbage the first time one entry was unreadable.
        """
        try:
            return _orig_cleanup_dead_symlinks(root)
        except OSError:
            pass
        try:
            entries = list(root.iterdir())
        except OSError:
            return None
        for entry in entries:
            if not entry.is_symlink():
                continue
            try:
                dead = not entry.exists()
            except OSError:
                # Unreadable target: treat as dead, which is what it is.
                dead = True
            if dead:
                try:
                    entry.unlink()
                except OSError:
                    pass
        return None

    _pytest_pathlib.cleanup_dead_symlinks = _cleanup_dead_symlinks_tolerantly


# --- A42: a source file written DURING a run makes source-reading tests lie ----

# Measured 2026-09-22, and it cost 68 minutes plus 13 red lines that were not bugs.
# A full suite was launched detached; while it ran, production files were edited
# through the orchestrator (orchestrator_server.py, code_tools.py, registry_data.py).
# Seven tests that assert on SOURCE -- inspect.getsource, AST walks over
# mcp_stdio.py, "is this guard still present in the file" checks -- then failed, and
# every one of them passed when re-run afterwards. inspect.getsource reads the file
# from DISK, not the module already imported, so a write landing mid-run shifts line
# numbers under a test whose module was imported minutes earlier.
#
# In the report that failure is indistinguishable from a real regression: same red,
# same assertion text. The only thing separating them is knowing a write happened
# inside the window -- exactly what a CA loses across a compaction, and exactly the
# campaign's own story (work completes, nobody can tell what the result means). So
# the run must say it out loud, next to the failures, instead of relying on memory.
#
# Deliberately observational: nothing is failed, skipped or reordered. A guard that
# could fail the run would let one unlucky edit destroy a 68-minute result instead of
# annotating it. mtime, not a hash: hashing every source file is a second full read
# of the tree on a HDD, and mtime already answers the only question being asked.
_SOURCE_MTIMES_AT_START: dict[str, float] = {}


def _iter_repo_source_files():
    root = Path(__file__).resolve().parent
    yield from root.glob("*.py")
    package = root / "orchestrator"
    if package.is_dir():
        yield from package.rglob("*.py")


def pytest_sessionstart(session):  # noqa: ARG001 - pytest hook signature
    for path in _iter_repo_source_files():
        try:
            _SOURCE_MTIMES_AT_START[str(path)] = path.stat().st_mtime
        except OSError:
            continue


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ARG001
    changed: list[tuple[str, str]] = []
    for path, before in _SOURCE_MTIMES_AT_START.items():
        try:
            after = os.stat(path).st_mtime
        except OSError:
            changed.append((path, "gone"))
            continue
        if after > before:
            changed.append((path, "modified"))
    if not changed:
        return
    root = str(Path(__file__).resolve().parent)
    terminalreporter.write_sep("=", "SOURCE CHANGED WHILE THIS RUN WAS IN FLIGHT")
    terminalreporter.write_line(
        f"{len(changed)} source file(s) changed after collection started. A failure that"
    )
    terminalreporter.write_line(
        "reads source from DISK (inspect.getsource, AST walks, 'guard is present in the"
    )
    terminalreporter.write_line(
        "file' assertions) may be an artefact of that write and NOT a regression --"
    )
    terminalreporter.write_line("re-run those tests alone before believing them.")
    for path, how in changed[:10]:
        shown = path[len(root) + 1:] if path.startswith(root) else path
        terminalreporter.write_line(f"  {how}: {shown}")
    if len(changed) > 10:
        terminalreporter.write_line(f"  ... and {len(changed) - 10} more")
