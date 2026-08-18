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
"""

import os
import tempfile
from pathlib import Path

# Precedence order matters: FORCE_COLOR beats NO_COLOR in ruff, so drop it.
_COLOUR_FORCING_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "PY_COLORS")

_SESSIONS_DB_ENV = "ORCHESTRATOR_SESSIONS_DB"


def pytest_configure(config):  # noqa: ARG001 - pytest hook signature
    for name in _COLOUR_FORCING_VARS:
        os.environ.pop(name, None)
    os.environ["NO_COLOR"] = "1"

    # A53: only fill in a default. An explicit value from the caller wins, so a
    # run that deliberately targets a specific DB is left alone.
    if not os.environ.get(_SESSIONS_DB_ENV):
        run_dir = Path(tempfile.mkdtemp(prefix="orch_pytest_sessions_"))
        os.environ[_SESSIONS_DB_ENV] = str(run_dir / "sessions.db")
