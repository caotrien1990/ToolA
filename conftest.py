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
import tempfile
import time
from pathlib import Path

# Precedence order matters: FORCE_COLOR beats NO_COLOR in ruff, so drop it.
_COLOUR_FORCING_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "PY_COLORS")

_SESSIONS_DB_ENV = "ORCHESTRATOR_SESSIONS_DB"
_GENERATION_FILE_ENV = "ORCHESTRATOR_GENERATION_FILE"


def pytest_configure(config):  # noqa: ARG001 - pytest hook signature
    for name in _COLOUR_FORCING_VARS:
        os.environ.pop(name, None)
    os.environ["NO_COLOR"] = "1"

    # A53: only fill in a default. An explicit value from the caller wins, so a
    # run that deliberately targets a specific DB is left alone.
    if not os.environ.get(_SESSIONS_DB_ENV):
        run_dir = Path(tempfile.mkdtemp(prefix="orch_pytest_sessions_"))
        os.environ[_SESSIONS_DB_ENV] = str(run_dir / "sessions.db")

    # Same rule as A53: fill in a default only, so a run that deliberately points
    # at a specific generation file is left alone. Children inherit os.environ,
    # which is what makes one assignment here cover servers spawned by Popen.
    if not os.environ.get(_GENERATION_FILE_ENV):
        gen_dir = Path(tempfile.mkdtemp(prefix="orch_pytest_generation_"))
        os.environ[_GENERATION_FILE_ENV] = str(gen_dir / "server_generation.json")


# --- W5: make ignore_cleanup_errors=True actually ignore cleanup errors -------

# Windows releases a closed child's SQLite -shm/-wal handle asynchronously. Six
# attempts over ~1.4s of backoff cleared it in every observed case; the swallow
# afterwards is the documented meaning of the flag, not a new leniency.
_CLEANUP_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)

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

