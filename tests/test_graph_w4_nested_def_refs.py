"""W4: a reference to a NESTED def is a reference, not a local rebinding.

`restart_control_service.build_guard` ends with `return guard`, where `guard` is
the closure it just defined. The extractor refused that reference: `_scope_binds`
recorded every name a function scope binds, and a nested `def` binds its own
name, so the closure was indistinguishable from `guard = 3`. No edge was emitted
at all, and `guard` -- the decision function the restart gate calls on every
cycle -- sat on the dead list. Measured on [A]: 903 references blocked this way.

The fix cannot be "stop blocking": 147 names in 61 files are defined more than
once in the same file (`_run` four times in code_tools.py), and `_local_defs` is
keyed by bare name, so relaxing without scope would bind a closure reference to
whichever same-named def happened to be registered last. Scope is precisely the
information that separates them, so the extractor now carries the nested defs of
each enclosing scope and resolves innermost-outward -- stopping at the first
scope that binds the name for any OTHER reason, because there the assignment is
what the name means and an edge would be a wrong one.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.extractors.python import extract


def _calls(source):
    """(source_id, target_id) of every `calls` edge, fixture kept verbatim.

    No .lstrip(): node ids carry the line number, so stripping the leading
    newline would shift every id and the assertions would stop naming what
    they mean.
    """
    r = extract(source, file_path="m.py", project_root=None)
    return [(e["source"], e["target"]) for e in r["edges"] if e["kind"] == "calls"]


# --- the shape the bug was found in -----------------------------------------

def test_a_returned_closure_is_a_reference_to_that_closure():
    """`build_guard` ends in `return guard`. That is the only reference the
    closure has, and it is what keeps it alive."""
    edges = _calls("""
def build():
    def guard():
        return 1
    return guard
""")
    assert ("m.py:function:m.build:2", "m.py:function:m.guard:3") in edges, edges


def test_a_closure_called_where_it_was_defined_binds_to_itself():
    edges = _calls("""
def build():
    def guard():
        return 1
    return guard()
""")
    assert edges == [("m.py:function:m.build:2", "m.py:function:m.guard:3")], edges


def test_a_closure_inside_a_method_is_owned_by_the_method():
    edges = _calls("""
class C:
    def build(self):
        def cb():
            return 1
        return cb
""")
    assert edges == [("m.py:method:m.C.build:3", "m.py:method:m.C.cb:4")], edges


def test_a_closure_defined_below_its_use_still_resolves():
    """Source order is not what decides whether a callable is alive -- the
    scope's defs are seeded before the body is walked, exactly as module-level
    defs already were."""
    edges = _calls("""
def build():
    handlers = {"a": inner}

    def inner():
        return 1
    return handlers
""")
    assert ("m.py:function:m.build:2", "m.py:function:m.inner:5") in edges, edges


# --- the negatives that make the relaxation safe ----------------------------

def test_two_closures_sharing_a_name_do_not_cross_bind():
    """The reason this is resolved by scope and not by relaxing the flat name
    map: `_run` is defined four times in code_tools.py alone. Each reference
    must reach the definition in ITS OWN scope."""
    edges = _calls("""
def a():
    def _run():
        return 1
    return _run


def b():
    def _run():
        return 2
    return _run
""")
    assert sorted(edges) == [
        ("m.py:function:m.a:2", "m.py:function:m._run:3"),
        ("m.py:function:m.b:8", "m.py:function:m._run:9"),
    ], edges


def test_a_local_variable_sharing_a_name_still_blocks_the_reference():
    """The behaviour the old check existed for. Of 318 bare references to a
    name the file defines, 190 are a local that merely shares it."""
    edges = _calls("""
def helper():
    return 0


def run():
    helper = 3
    return helper
""")
    assert edges == [], edges


def test_a_nested_def_later_reassigned_stays_blocked():
    """Both a def AND an assignment bind the name, so which one a reference
    means depends on where it runs. Refusing costs a missing edge; guessing
    costs a wrong one, and a wrong call edge makes dead code look alive
    forever."""
    edges = _calls("""
def helper():
    return 0


def f(flag):
    def helper():
        return 1
    if flag:
        helper = None
    return helper
""")
    assert edges == [], edges


def test_a_def_nested_two_levels_down_is_not_visible_to_the_outer_scope():
    """`deep` is declared inside `mid`, so the name means nothing in `outer`.
    Recording it would invent an edge that the language itself rejects."""
    edges = _calls("""
def outer():
    def mid():
        def deep():
            return 1
        return deep
    return deep
""")
    assert edges == [("m.py:function:m.mid:3", "m.py:function:m.deep:4")], edges


def test_a_def_inside_a_branch_belongs_to_the_enclosing_scope():
    """Same scope is not the same as same block: a `def` written under `if` or
    `try` is still a binding of the function that contains it."""
    edges = _calls("""
def build(flag):
    if flag:
        def cb():
            return 1
    else:
        def cb():
            return 2
    return cb
""")
    assert edges, "a def written inside a branch must still be referencable"
    assert all(s == "m.py:function:m.build:2" for s, _ in edges), edges


def test_a_module_level_def_is_still_reachable_from_inside_a_function():
    """The pre-existing path must not regress: a scope that declares nothing
    named `helper` falls through to the module-level definition."""
    edges = _calls("""
def helper():
    return 0


def run():
    return helper()
""")
    assert edges == [("m.py:function:m.run:6", "m.py:function:m.helper:2")], edges
