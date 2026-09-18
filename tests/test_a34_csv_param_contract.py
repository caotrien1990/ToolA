"""A34 W1 - pin the csv/JSON-list coercion contract on every CA-facing list param.

Measured before-state (2026-09-18, live HTTP on :8083): three tools crashed with
``AttributeError: 'list' object has no attribute 'split'`` when a CA sent a JSON
array for a parameter the schema declares as a comma-separated STRING:

  * code_tools.rule_drift_check(checks=[...])            -> crash  (research doc B1)
  * tools_v15.import_graph(file_pattern=[...])           -> crash  (found while fixing B1)
  * tools_v15.ast_query(file_pattern=[...])              -> crash  (same sweep)

while the identical csv string answered ok:true. That asymmetry is the bug: a CA
reading only the schema cannot tell which of the two shapes a given tool accepts,
so it guesses, and one shape of the guess raises.

Two working normalisers already existed (orchestrator/graph/tools._as_path_list and
orchestrator_server._r101_parse_fields), which is why the fix is ONE shared leaf --
orchestrator/core/params.csv_param -- and not a fourth private copy.

This file pins the contract in two directions:

  1. behaviour - for each repaired tool, the csv / JSON-string / real-list forms must
     return the SAME answer, not merely "not raise". A tool that silently selected 0
     files would also "not raise".
  2. shape - an ast scan asserts no CA-facing parameter anywhere in the production
     tree still reaches ``.split(",")`` unguarded. Without the scan, the next tool to
     grow a list param re-introduces the same class and the behaviour tests above stay
     green because they only know about three call sites.

The scan's heuristic is deliberate and documented in _unguarded_csv_splits(): a
``param.split(",")`` is a finding UNLESS the same function also tests
``isinstance(param, ...)`` somewhere. That is what makes the two pre-existing
normalisers pass honestly rather than by being named in an allowlist.
"""

import ast
import asyncio
import json
from pathlib import Path

import pytest

import code_tools as ct
import tools_v15 as tv
from orchestrator.core.params import csv_param

REPO = Path(__file__).resolve().parents[1]

# Not [A]'s production surface: virtualenvs, caches, vendored + runtime trees, and the
# test/doc trees that legitimately demonstrate the broken shape (this file included).
_SKIP_DIRS = {
    ".git", "venv312", "venv_ocr", "node_modules", ".orchestrator_cache",
    "__pycache__", "_iso_campaign", ".council", "tests", "docs", "examples",
}

# The ONE private helper allowed to split a parameter without an isinstance guard.
# _parse_cron_field is fed exclusively by parts[N] of an already-split cron expression
# (orchestrator/council/cron_jobs.py:103-107 are its only callers) and is underscore
# private, so no CA payload ever reaches it. Verified 2026-09-18.
_ALLOWED_UNGUARDED = {"orchestrator/council/cron_jobs.py:_parse_cron_field:expr"}


def _prod_py_files():
    for path in REPO.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.relative_to(REPO).parts):
            continue
        yield path


def _param_names(fn):
    args = fn.args
    names = {a.arg for a in (args.posonlyargs + args.args + args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def _unguarded_csv_splits(tree, rel):
    """Yield 'rel:func:param' for every param that reaches .split(",") with no guard.

    Both shapes the codebase actually used are matched: ``param.split(",")`` and the
    ``(param or "").split(",")`` form that hid the rule_drift_check crash for months --
    a Name-only matcher reports that file clean.
    """
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = _param_names(fn)
        if not params:
            continue
        guarded = {
            node.args[0].id
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and node.args
            and isinstance(node.args[0], ast.Name)
        }
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "split"):
                continue
            if not (node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == ","):
                continue
            recv = node.func.value
            name = None
            if isinstance(recv, ast.Name):
                name = recv.id
            elif (isinstance(recv, ast.BoolOp) and isinstance(recv.op, ast.Or)
                  and isinstance(recv.values[0], ast.Name)):
                name = recv.values[0].id
            if name and name in params and name not in guarded:
                yield f"{rel}:{fn.name}:{name}"


# ---------------------------------------------------------------- csv_param unit

@pytest.mark.parametrize("value", [
    "a,b",
    " a , b ",
    "a,,b",
    '["a","b"]',
    '[ "a" , "b" ]',
    ["a", "b"],
    ("a", "b"),
    ["a", " b "],
])
def test_csv_param_every_accepted_shape_yields_the_same_list(value):
    assert csv_param(value) == ["a", "b"]


def test_csv_param_set_is_sorted_so_callers_get_a_stable_order():
    # A set has no order; returning it unsorted would make a tool's output depend on
    # PYTHONHASHSEED, which is exactly the kind of flake a guard test must not allow.
    assert csv_param({"b", "a"}) == ["a", "b"]
    assert csv_param(frozenset({"b", "a"})) == ["a", "b"]


@pytest.mark.parametrize("value", [None, "", "   ", [], (), ",", " , "])
def test_csv_param_empty_inputs_collapse_to_empty_list(value):
    assert csv_param(value) == []


def test_csv_param_lower_and_keep_empty_flags():
    assert csv_param("Tool_Names,AGENT_List", lower=True) == ["tool_names", "agent_list"]
    assert csv_param("a,,b", keep_empty=True) == ["a", "", "b"]


def test_csv_param_non_string_scalar_is_wrapped_not_iterated():
    # str(5) must not become ['5'] via iteration of a string; and a bare scalar is a
    # legitimate single-value payload.
    assert csv_param(5) == ["5"]


def test_csv_param_malformed_json_array_falls_back_to_csv():
    # '[a,b]' is not valid JSON. Falling back to the csv branch keeps the tool usable
    # instead of raising ValueError at the boundary.
    assert csv_param("[a,b]") == ["[a", "b]"]


# ------------------------------------------------------- repaired tools behaviour

def _drift(checks):
    return json.loads(asyncio.run(ct.rule_drift_check(checks=checks)))


def test_rule_drift_check_list_and_json_string_match_the_csv_form():
    csv = _drift("tool_names")
    assert csv.get("ok") is True
    for form in (["tool_names"], '["tool_names"]', "Tool_Names"):
        got = _drift(form)
        assert got.get("ok") is True, form
        assert got.get("drift_count") == csv.get("drift_count"), form
        assert ({d["check"] for d in got.get("drifts", [])}
                == {d["check"] for d in csv.get("drifts", [])}), form


def test_rule_drift_check_empty_checks_still_selects_every_check():
    # The `or {default}` branch: an empty payload must not mean "check nothing".
    narrow = _drift("tool_names")
    every = _drift("")
    assert every.get("ok") is True
    assert len({d["check"] for d in every.get("drifts", [])}) >= 1
    assert every.get("drift_count") >= narrow.get("drift_count")


@pytest.fixture
def mini_project(tmp_path):
    (tmp_path / "a.py").write_text("import os\n\n\nasync def f():\n    return {}\n",
                                   encoding="utf-8")
    (tmp_path / "b.js").write_text("import x from 'y';\n", encoding="utf-8")
    return tmp_path


def test_import_graph_list_and_json_string_match_the_csv_form(mini_project):
    csv = json.loads(asyncio.run(tv.import_graph(str(mini_project),
                                                 file_pattern="*.py,*.js")))
    assert csv.get("ok") is True
    # Not just "ok": the csv form must really have selected BOTH files, or the
    # equivalence below would be comparing two empty scans.
    assert csv.get("file_count") == 2
    for form in (["*.py", "*.js"], '["*.py","*.js"]'):
        got = json.loads(asyncio.run(tv.import_graph(str(mini_project),
                                                     file_pattern=form)))
        assert got.get("ok") is True, form
        assert got.get("file_count") == csv.get("file_count"), form
        assert sorted(map(json.dumps, got.get("edges", []))) == \
            sorted(map(json.dumps, csv.get("edges", []))), form


def test_ast_query_list_and_json_string_match_the_csv_form(mini_project):
    def run(form):
        return json.loads(asyncio.run(tv.ast_query("async_function",
                                                   str(mini_project),
                                                   file_pattern=form)))
    csv = run("*.py")
    assert csv.get("ok") is True
    csv_hits = csv.get("results") or csv.get("matches") or []
    assert len(csv_hits) == 1  # positive control: the pattern really matched
    for form in (["*.py"], '["*.py"]'):
        got = run(form)
        assert got.get("ok") is True, form
        hits = got.get("results") or got.get("matches") or []
        assert len(hits) == len(csv_hits), form


# ------------------------------------------------------------------- shape gates

def test_no_ca_facing_param_reaches_csv_split_unguarded():
    found = set()
    for path in _prod_py_files():
        rel = path.relative_to(REPO).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        found.update(_unguarded_csv_splits(tree, rel))
    assert found <= _ALLOWED_UNGUARDED, (
        "new unguarded csv split(s) - route the value through "
        f"orchestrator.core.params.csv_param: {sorted(found - _ALLOWED_UNGUARDED)}")


def test_the_scanner_itself_catches_both_broken_shapes():
    # Without this, test_no_ca_facing_param_reaches_csv_split_unguarded could pass
    # vacuously the moment the matcher stops recognising a shape.
    src = (
        "def plain(items):\n"
        "    return items.split(',')\n"
        "def bool_or(checks):\n"
        "    return (checks or '').split(',')\n"
        "def guarded(fields):\n"
        "    if isinstance(fields, str):\n"
        "        return fields.split(',')\n"
        "    return list(fields)\n"
        "def not_a_param(x):\n"
        "    local = 'a,b'\n"
        "    return local.split(',')\n"
    )
    got = set(_unguarded_csv_splits(ast.parse(src), "synthetic.py"))
    assert got == {"synthetic.py:plain:items", "synthetic.py:bool_or:checks"}


def test_the_allowlisted_helper_is_private_and_has_no_ca_payload_caller():
    # An allowlist entry is only honest while its justification holds: if
    # _parse_cron_field ever goes public or gains an external caller, this fails and
    # the entry has to be re-earned.
    cron = REPO / "orchestrator" / "council" / "cron_jobs.py"
    for entry in _ALLOWED_UNGUARDED:
        rel, func, _param = entry.split(":")
        assert func.startswith("_"), entry
        assert (REPO / rel).exists(), entry
    callers = [
        p.relative_to(REPO).as_posix()
        for p in _prod_py_files()
        if p != cron and "_parse_cron_field" in p.read_text(encoding="utf-8")
    ]
    assert callers == [], f"_parse_cron_field escaped its module: {callers}"


def test_declare_mission_mcp_wrapper_delegates_to_csv_param():
    # The MCP wrapper is not importable in a plain pytest process (it registers tools
    # against a live zeromcp host), so it is pinned by source. Both halves matter: the
    # new call must be present AND the old crash line must be gone.
    src = (REPO / "orchestrator" / "server" / "mcp_stdio.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fns = [n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
           and n.name == "declare_mission"]
    assert len(fns) == 1
    body = ast.get_source_segment(src, fns[0]) or ""
    assert "csv_param(context_hints)" in body
    assert "context_hints.split(" not in body
