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
"""

import os

# Precedence order matters: FORCE_COLOR beats NO_COLOR in ruff, so drop it.
_COLOUR_FORCING_VARS = ("FORCE_COLOR", "CLICOLOR_FORCE", "PY_COLORS")


def pytest_configure(config):  # noqa: ARG001 - pytest hook signature
    for name in _COLOUR_FORCING_VARS:
        os.environ.pop(name, None)
    os.environ["NO_COLOR"] = "1"
