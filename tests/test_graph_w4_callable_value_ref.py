"""W4: a callable used as a VALUE keeps it alive, and must leave an edge.

`skip_dir = skip_dir or _default_skip_dir` is the fallback-default idiom, and it
is the ONLY reference `search_snapshots._default_skip_dir` has. Same for
`reaper._default_kill_process_tree`. Neither is an ast.Call with the callable as
`func`, so `_emit_call` never saw them and both sat on the dead list -- a
deletion work-list -- while being load-bearing.

The extractor already had `_emit_callable_ref_from_expr` for exactly this shape,
but it was wired only to call arguments/keywords and module-level assignment
RHS. The general position -- anywhere a bare name is read -- was missing.

The negative tests carry the weight. Measured on [A], of 318 bare references to
a name the same file defines, 190 are a LOCAL variable that merely shares the
name. Binding those would create more wrong edges than right ones, and a wrong
call edge is worse than a missing one: it makes dead code look alive forever.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.extractors.python import extract


def _calls(source):
    """{(source_id, target_id)} of every `calls` edge the extractor emits.

    The leading newline of each fixture is KEPT deliberately: node ids carry the
    line number, so stripping it would shift every id by one and the assertions
    below would silently stop matching what they name.
    """
    r = extract(source, file_path="m.py", project_root=None)
    return {(e["source"], e["target"]) for e in r["edges"] if e["kind"] == "calls"}


def test_the_fallback_default_idiom_produces_an_edge():
    """`x = x or _default` -- the shape that put two live callables on the
    dead list."""
    edges = _calls("""
def _default_skip_dir(p):
    return False


def scan(root, skip_dir=None):
    skip_dir = skip_dir or _default_skip_dir
    return skip_dir(root)
""")
    assert any(s.endswith("m.scan:6") and t.endswith("m._default_skip_dir:2")
               for s, t in edges), edges


def test_a_callable_in_a_dict_of_handlers_produces_an_edge():
    """A dispatch table built from real function objects (not name strings --
    that is W5's name_literal path)."""
    edges = _calls("""
def handle_a():
    return 1


HANDLERS = {"a": handle_a}
""")
    assert any(t.endswith("m.handle_a:2") for _, t in edges), edges


def test_a_callable_as_a_parameter_default_produces_an_edge():
    edges = _calls("""
def _default_kill(pid):
    return None


def reap(pid, kill=_default_kill):
    return kill(pid)
""")
    assert any(t.endswith("m._default_kill:2") for _, t in edges), edges


def test_a_callable_in_a_list_produces_an_edge():
    edges = _calls("""
def step_one():
    return 1


PIPELINE = [step_one]
""")
    assert any(t.endswith("m.step_one:2") for _, t in edges), edges


def test_a_local_variable_that_shares_the_name_does_not_bind():
    """The 190-of-318 case. `handle_a = 5` inside a function is a local int,
    not the module-level function, and must NOT produce an edge."""
    edges = _calls("""
def handle_a():
    return 1


def shadowing():
    handle_a = 5
    return handle_a
""")
    assert not any(s.endswith("m.shadowing:6") and t.endswith("m.handle_a:2")
                   for s, t in edges), edges


def test_a_parameter_that_shares_the_name_does_not_bind():
    """A parameter is a local binding exactly like an assignment is."""
    edges = _calls("""
def cleanup():
    return 1


def run(cleanup):
    return cleanup
""")
    assert not any(s.endswith("m.run:6") and t.endswith("m.cleanup:2")
                   for s, t in edges), edges


def test_an_import_that_shares_the_name_does_not_bind():
    """`from x import helper` inside a function rebinds the name to something
    from another module."""
    edges = _calls("""
def helper():
    return 1


def run():
    from other import helper
    return helper
""")
    assert not any(s.endswith("m.run:6") and t.endswith("m.helper:2")
                   for s, t in edges), edges


def test_an_ordinary_call_still_emits_exactly_one_edge():
    """visit_Name must not double-count a name that is already a call's func.
    Two edges for one call site would inflate every fan-in count in the graph."""
    edges = [(s, t) for s, t in _calls("""
def helper():
    return 1


def run():
    return helper()
""") if t.endswith("m.helper:2")]
    assert len(edges) == 1, edges


def test_a_callable_passed_as_an_argument_still_emits_exactly_one_edge():
    """The argument position was ALREADY handled by _emit_callable_ref_from_expr.
    visit_Name must not emit a second edge for the same reference."""
    edges = [(s, t) for s, t in _calls("""
def work():
    return 1


def run(pool):
    return pool.submit(work)
""") if t.endswith("m.work:2")]
    assert len(edges) == 1, edges


def test_a_module_level_assignment_still_emits_exactly_one_edge():
    """`FN = helper` at module level was already handled, with the VARIABLE as
    the edge source. visit_Name must not add a module-sourced duplicate."""
    edges = [(s, t) for s, t in _calls("""
def helper():
    return 1


FN = helper
""") if t.endswith("m.helper:2")]
    assert len(edges) == 1, edges


def test_a_bare_name_that_is_not_a_callable_produces_nothing():
    """A module-level constant read is not a call reference."""
    edges = _calls("""
LIMIT = 5


def run():
    return LIMIT
""")
    assert not any("LIMIT" in t for _, t in edges), edges
