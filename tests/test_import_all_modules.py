"""Every module in `orchestrator/` must import on its own.

The graph says [A] has 278 deferred imports -- `import x` written inside a
function body rather than at module top level. Most are deliberate: a deferred
import is the standard way to break an import cycle, and W5 taught the indexer
to tell the two apart precisely so the 18-file SCC could be worked on safely.

But a deferred import is also the one place a broken dependency can hide
indefinitely. A top-level `import` fails the moment anything touches the
module; an import inside a rarely-taken branch fails the first time a user
walks that branch, in production. 103 of the 278 are not guarded by
try/except and not inside a `TYPE_CHECKING` block, so nothing in the suite
proves the module they name is importable at all.

This test is the cheapest possible proof that the package is internally
consistent: import all of it, in one process, and let any ImportError,
circular-import RuntimeError, or import-time exception surface as a failure
naming the module. It costs about a second and it is the test that would have
caught a cycle break that removed a symbol someone still imported.

Deliberately imports the whole package rather than a curated list -- a list
goes stale the moment a module is added, which is exactly when this matters.
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_SKIP_DIRS = {"__pycache__", ".orchestrator_cache", ".git", "venv312", "node_modules"}


def _module_names() -> list[str]:
    """Dotted names for every .py file under orchestrator/, packages included."""
    out: set[str] = set()
    pkg = _ROOT / "orchestrator"
    for dirpath, dirnames, filenames in os.walk(pkg):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            rel = (Path(dirpath) / fn).relative_to(_ROOT)
            name = ".".join(rel.with_suffix("").parts)
            if name.endswith(".__init__"):
                name = name[: -len(".__init__")]
            out.add(name)
    return sorted(out)


_MODULES = _module_names()


def test_the_walk_actually_found_the_package():
    """A guard on the guard: a broken walk would make every case below vacuous."""
    assert len(_MODULES) > 50, f"only found {len(_MODULES)} modules: {_MODULES[:5]}"
    assert "orchestrator.graph.indexer" in _MODULES


@pytest.mark.parametrize("name", _MODULES)
def test_module_imports_standalone(name):
    """One case per module, so a failure names the module instead of the walk.

    Import errors from a DEPENDENCY are just as real as ones from this file:
    if `orchestrator.a` cannot be imported because `orchestrator.b` raises,
    both are broken from a caller's point of view.
    """
    try:
        importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 -- the failure message is the product
        pytest.fail(f"{name} does not import: {type(exc).__name__}: {exc}")
