"""A test run must not rewrite the live daemon's generation file.

`orchestrator_server.py` publishes `SERVER_GENERATION` -- a fresh `uuid4` per
process -- into `.orchestrator_cache/server_generation.json`, and MCP reads that
file to decide whether the daemon it proxies to has restarted. So any test that
starts a real server hands the live daemon a new identity, and MCP begins
refusing calls with MCP_PROXY_FAILED even though the writes themselves land.

Observed on 2026-08-20 while the full suite was running: the file held pid 111348
on port 51156 -- a dead process, on a port the daemon never used -- while the
daemon was pid 209860 on 8083. That state persists after the child exits, so the
real daemon stays un-probeable until its next restart.

Two layers are pinned here, deliberately, because they fail differently:

* the `conftest.py` default is the fence -- it makes the leak impossible for a
  future test that forgets the variable, which is exactly how this one was
  written;
* `test_task_epoch_cross_process.py` isolating it explicitly is the local fix --
  it keeps working if the fence is ever moved, or if the module is driven by
  something other than this repo's `pytest_configure`.

The first test asserts the property that matters rather than the mechanism: the
production cache file is not the one a spawned server would publish into.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_PRODUCTION_FILE = _ROOT / ".orchestrator_cache" / "server_generation.json"


def test_the_run_is_not_pointed_at_the_production_generation_file() -> None:
    """Whatever a spawned server publishes, it must not be this file."""
    value = os.environ.get("ORCHESTRATOR_GENERATION_FILE")
    assert value, (
        "ORCHESTRATOR_GENERATION_FILE is unset, so orchestrator_server would "
        "publish into %s -- the file MCP reads to detect a daemon restart"
        % _PRODUCTION_FILE
    )
    assert Path(value).resolve() != _PRODUCTION_FILE.resolve(), (
        "the test run points at the production generation file"
    )


def test_conftest_only_defaults_the_variable_and_never_overrides_it() -> None:
    """An explicit value from the caller has to win, as it does for A53's DB path.

    Asserted against the source rather than by re-running pytest in a subprocess:
    the behaviour is a single `if not os.environ.get(...)`, and reading it is both
    cheaper and less flaky than spawning an interpreter to observe it.
    """
    source = (_ROOT / "conftest.py").read_text(encoding="utf-8")
    # The bookkeeping lines between mkdtemp and the assignment are deliberately
    # tolerated: B031 records the directory there so `pytest_unconfigure` can
    # remove it, and pinning byte-adjacency would forbid any such line while
    # pinning nothing extra. What must hold is that the assignment is INSIDE the
    # `if not os.environ.get(...)` guard, so the intervening pattern excludes any
    # line that touches os.environ.
    guard = re.search(
        r"if not os\.environ\.get\(_GENERATION_FILE_ENV\):\s*\n"
        r"\s*gen_dir = Path\(tempfile\.mkdtemp\([^)]*\)\)\s*\n"
        r"(?:[^\n]*\n)*?"
        r"\s*os\.environ\[_GENERATION_FILE_ENV\] = ",
        source,
    )
    assert guard, "conftest must DEFAULT the generation file, not assign it outright"

    # And the guard must be the ONLY writer: an unconditional assignment anywhere
    # else would override a caller's explicit value, which is the actual bug this
    # module was written for. Two occurrences = the guarded one plus the env read.
    assert source.count("os.environ[_GENERATION_FILE_ENV] =") == 1, (
        "conftest assigns the generation file in more than one place"
    )


def test_the_server_spawning_test_isolates_the_variable_itself() -> None:
    """Belt and braces: the test that actually starts a daemon sets it explicitly.

    This exists because the fence lives in `pytest_configure`; a copy-pasted
    `env.update(...)` in a new test, or a runner that drives the module directly,
    would not get it.
    """
    source = (_HERE / "test_task_epoch_cross_process.py").read_text(encoding="utf-8")
    assert "ORCHESTRATOR_GENERATION_FILE=" in source, (
        "the cross-process test spawns a real orchestrator_server; it must point "
        "the generation file at its own tmpdir"
    )
    # And it must land in the per-test tmpdir, not anywhere shared.
    assert re.search(r"ORCHESTRATOR_GENERATION_FILE=str\(self\.root\s*/", source), (
        "the isolated generation file must live under the test's own root"
    )
