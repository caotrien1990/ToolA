"""`file_filter` must filter, and the reported engine must be the one that ran.

Measured on the reported repo: `cbm_dead_code` returned count=207 with no filter,
count=207 with `file_filter="5.backend/**/*.js"`, and count=207 with a
deliberately BOGUS glob (`no/such/dir/**/*.js`). Identical for a filter that
matches nothing is the proof the argument is inert rather than merely broad.

Two defects, in one function:

  1. `_cbm_dead_code_dispatch` computes `files` from the glob and then, for any
     non-Python repo, early-returns into `_cbm_dead_code_ast_p0` -- which has no
     parameter to receive it. The filter is accepted, silently discarded, and
     never reported as discarded.
  2. Even on the Python path the glob is hard-filtered with
     `path.endswith(".py")`, so a `*.js` pattern can never match anything. The
     schema said "matching files are passed directly to Vulture/Ruff", which is
     only true for Python paths.

And the naming (reported separately): the schema advertises "Ruff-powered ...
Vulture supplements" while a non-Python repo silently gets `engine:"ast-p0-v1"`,
a different analysis with different semantics. A tool may fall back, but it must
not describe itself as something it is not.

The user's underlying need is the real point: with `scope` limited to full|core
there was no way to exclude a large data directory, which is what made the tool
unusable on their repo.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import tools as gtools


JS_FILES = {
    "backend/api.js": ("function usedByNobody() { return 1; }\n"
                       "module.exports = {};\n"),
    "backend/db.js": "function alsoDead() { return 2; }\nmodule.exports = {};\n",
    "vendor/huge.js": "function vendorDead() { return 3; }\nmodule.exports = {};\n",
}


def _repo(tmp_path, monkeypatch, files, name="dc"):
    root = tmp_path / name
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / (name + "_db")))
    monkeypatch.setenv("ORCHESTRATOR_PARALLEL_INDEX", "off")
    monkeypatch.setenv("CBM_EXTRACT_JS", "1")
    monkeypatch.setenv("CBM_GRAPH_ENABLED", "1")
    from orchestrator.graph.indexer import index_project
    index_project(str(root), scope="full")
    return root


def _dead(root, **kw):
    return gtools._cbm_dead_code_dispatch(str(root), **kw)


def test_a_filter_that_matches_nothing_returns_nothing(tmp_path, monkeypatch):
    """The decisive case: a bogus glob returned the FULL result set."""
    root = _repo(tmp_path, monkeypatch, JS_FILES, name="dc_bogus")
    unfiltered = _dead(root, limit=50)
    assert unfiltered["count"] > 0, "fixture found no dead code to filter"

    bogus = _dead(root, limit=50, file_filter="no/such/dir/**/*.js")
    assert bogus["count"] == 0, (
        "a glob matching zero files returned %s candidates -- file_filter is "
        "inert, not merely broad" % bogus["count"])


def test_a_filter_narrows_the_result(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch, JS_FILES, name="dc_narrow")
    unfiltered = _dead(root, limit=50)
    filtered = _dead(root, limit=50, file_filter="backend/**/*.js")

    assert 0 < filtered["count"] < unfiltered["count"], (
        "filter did not narrow: %s vs %s" % (filtered["count"], unfiltered["count"]))
    for c in filtered["candidates"]:
        fp = str(c.get("file_path") or c.get("file") or "").replace("\\", "/")
        assert fp.startswith("backend/"), fp


def test_a_js_glob_is_not_silently_dropped_for_being_non_python(tmp_path,
                                                                monkeypatch):
    """The `.endswith('.py')` hard-filter made every *.js pattern match zero."""
    root = _repo(tmp_path, monkeypatch, JS_FILES, name="dc_js")
    filtered = _dead(root, limit=50, file_filter="backend/*.js")
    assert filtered["count"] > 0, (
        "a *.js glob over real .js files matched nothing")


def test_the_filter_is_echoed_so_a_caller_can_tell_it_applied(tmp_path,
                                                              monkeypatch):
    """Silent acceptance is how an inert argument goes unnoticed for so long."""
    root = _repo(tmp_path, monkeypatch, JS_FILES, name="dc_echo")
    res = _dead(root, limit=50, file_filter="backend/**/*.js")
    assert res.get("file_filter") == "backend/**/*.js", res.keys()
    assert res.get("file_filter_applied") is True
    assert res.get("files_matched") == 2, res.get("files_matched")


def test_no_filter_reports_that_no_filter_was_applied(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch, JS_FILES, name="dc_nofilter")
    res = _dead(root, limit=50)
    assert res.get("file_filter_applied") is False


def test_the_engine_name_says_what_actually_ran(tmp_path, monkeypatch):
    """`engine:"ast-p0-v1"` under a schema promising Ruff/Vulture misleads.

    The AST engine is a legitimate fallback for a non-Python repo; presenting it
    without saying so is the defect.
    """
    root = _repo(tmp_path, monkeypatch, JS_FILES, name="dc_engine")
    res = _dead(root, limit=50)
    engine = res.get("engine", "")
    assert "ast" in engine, engine
    assert res.get("engine_reason"), (
        "a fallback engine must say why it was chosen, or the schema's "
        "'Ruff-powered' claim silently becomes false")
    assert "python" in res["engine_reason"].lower()
    assert res.get("language_detected") == "javascript", res.get("language_detected")


def test_an_absolute_filter_still_works(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch, JS_FILES, name="dc_abs")
    pattern = str(root / "backend" / "*.js")
    res = _dead(root, limit=50, file_filter=pattern)
    assert res["count"] > 0
    assert res.get("files_matched") == 2


def test_a_python_repo_keeps_the_vulture_ruff_engine(tmp_path, monkeypatch):
    """Regression guard: the Python path must not be rerouted by this fix."""
    files = {
        "pkg/mod.py": "def _never_called():\n    return 1\n",
        "pkg/other.py": "def _also_dead():\n    return 2\n",
    }
    root = _repo(tmp_path, monkeypatch, files, name="dc_py")
    res = _dead(root, limit=50)
    assert res.get("engine") == "vulture+ruff", res.get("engine")
    assert res.get("language_detected") == "python"


def test_a_python_repo_filter_still_narrows(tmp_path, monkeypatch):
    files = {
        "pkg/mod.py": "def _never_called():\n    return 1\n",
        "vendor/big.py": "def _vendor_dead():\n    return 2\n",
    }
    root = _repo(tmp_path, monkeypatch, files, name="dc_py_filter")
    res = _dead(root, limit=50, file_filter="pkg/*.py")
    assert res.get("files_matched") == 1, res.get("files_matched")
    for c in res["candidates"]:
        fp = str(c.get("file_path") or c.get("file") or "").replace("\\", "/")
        assert "vendor" not in fp, fp
