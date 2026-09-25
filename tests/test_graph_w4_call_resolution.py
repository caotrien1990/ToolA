"""W4: the two reasons a live callable read as dead.

Both were measured on [A] itself while triaging a 179-entry dead-code list for
`orchestrator/` -- a list that was supposed to be a deletion work-list and was
mostly wrong. The list is 99 entries after these fixes, with nothing newly dead.

1. A call written outside any function was DROPPED, because the extractor took
   its source from an empty function stack and returned early. 171 module-level
   calls in orchestrator/ were invisible, including all 5 `raise SystemExit(
   main())` entry points -- every one of which then reported as dead.

2. A call through an imported module (`store.create_mission(...)`) bound to a
   placeholder `ext:create_mission`, because the extractor sees one file and
   cannot follow the import. 2963 such edges pointed at a name exactly one
   imported module defines; 94 callables were falsely dead, including
   `orchestrator_server.dispatch_tool_call` with 51 real call sites.

The negative tests matter as much as the positive ones: a wrong call edge is
worse than a missing one, because it makes dead code look alive forever.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.config import CBM_SAME_MODULE, graph_db_path
from orchestrator.graph.indexer import index_project


def _index(tmp_path, monkeypatch, name, files):
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / "graph" / name))
    project = tmp_path / name
    project.mkdir()
    for rel, src in files.items():
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src.lstrip(), encoding="utf-8")
    project = str(project).replace("\\", "/")
    result = index_project(project, scope="full")
    assert result["status"] == "completed", result
    return project, result


def _callers_of(project, name):
    """(source_kind, source_qualified_name, confidence) of every call INTO the
    DEFINITION named `name`.

    Excluding `external` AND `import` targets is the whole point. Both node
    kinds carry the bare name in their `name` column: an `ext:<name>`
    placeholder from store._ensure_external_nodes, and the local alias node a
    `from x import name` line creates. A join on name alone counts a call that
    never reached the definition as if it had -- which is exactly what these
    tests exist to tell apart. Use _is_ext() to ask about the placeholder side.
    """
    conn = sqlite3.connect(graph_db_path(project))
    conn.row_factory = sqlite3.Row
    try:
        return [(r["sk"], r["sq"], r["confidence"]) for r in conn.execute(
            "SELECT s.kind sk, s.qualified_name sq, e.confidence FROM edges e "
            "JOIN nodes s ON s.id = e.source JOIN nodes t ON t.id = e.target "
            "WHERE e.kind='calls' AND t.name=? "
            "AND t.kind NOT IN ('external','import');",
            (name,))]
    finally:
        conn.close()


def _is_ext(project, name):
    """True when SOME call edge still points at the ext: placeholder for name."""
    conn = sqlite3.connect(graph_db_path(project))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM edges WHERE kind='calls' AND target=?;",
            ("ext:" + name,)).fetchone()[0]
    finally:
        conn.close()


# --- 1: a call outside a function still has an owner -------------------------

def test_module_level_call_is_attributed_to_the_module(tmp_path, monkeypatch):
    """`raise SystemExit(main())` is the strongest possible evidence of life:
    it runs at import. It used to produce no edge at all."""
    project, _ = _index(tmp_path, monkeypatch, "modcall", {"cli.py": """
def main():
    return 0


raise SystemExit(main())
"""})
    callers = _callers_of(project, "main")
    assert callers, "a module-level call must still produce a call edge"
    assert [c[0] for c in callers] == ["module"], callers


def test_class_body_call_is_attributed_to_the_class(tmp_path, monkeypatch):
    """A call in a class body (a descriptor, a computed default) belongs to the
    class, not to the module -- the innermost enclosing definition owns it."""
    project, _ = _index(tmp_path, monkeypatch, "clscall", {"m.py": """
def compute_default():
    return 1


class C:
    value = compute_default()
"""})
    callers = _callers_of(project, "compute_default")
    assert [c[0] for c in callers] == ["class"], callers
    assert callers[0][1].endswith("C"), callers


def test_a_call_inside_a_function_still_belongs_to_that_function(tmp_path, monkeypatch):
    """The fix must not push ownership up: function scope still wins."""
    project, _ = _index(tmp_path, monkeypatch, "fncall", {"m.py": """
def helper():
    return 1


def caller():
    return helper()
"""})
    callers = _callers_of(project, "helper")
    assert [(c[0], c[1]) for c in callers] == [("function", "m.caller")], callers


# --- 2: a call through an imported module binds to the real definition -------

_TWO_MODULES = {
    "store.py": """
def create_mission(x):
    return x
""",
    "svc.py": """
import store


def run():
    return store.create_mission(1)
""",
}


def test_call_through_an_imported_module_binds_to_the_definition(tmp_path, monkeypatch):
    project, _ = _index(tmp_path, monkeypatch, "impcall", _TWO_MODULES)
    callers = _callers_of(project, "create_mission")
    assert callers, "store.create_mission(...) must reach the definition"
    assert callers[0][1] == "svc.run", callers
    # Below a same-file direct call: the receiver could still be rebound locally.
    assert callers[0][2] == CBM_SAME_MODULE, callers
    assert not _is_ext(project, "create_mission"), "placeholder must be rewritten"


def test_from_import_form_binds_too(tmp_path, monkeypatch):
    """`from store import create_mission` is the same binding by another
    spelling -- but it fails for a DIFFERENT reason and needs its own fix.

    The extractor creates an `import` node named `create_mission` and binds the
    call to that alias. The alias is a real node, so the edge never looks
    external and the ext: pass above cannot see it, while the definition still
    has zero callers. Measured on [A]: 3653 call edges sat on an alias.

    This assertion is written against `t.kind != 'external'` AND the caller's
    identity for a reason: an earlier version of this test asserted only that
    SOMETHING named `create_mission` was called, and passed while the call was
    still landing on the alias -- the alias carries the same `name`.
    """
    project, _ = _index(tmp_path, monkeypatch, "fromimp", {
        "store.py": _TWO_MODULES["store.py"],
        "svc.py": """
from store import create_mission


def run():
    return create_mission(1)
""",
    })
    callers = _callers_of(project, "create_mission")
    assert callers, "from-import call must reach the DEFINITION, not the alias"
    assert callers[0][1] == "svc.run", callers


def test_a_name_from_a_module_this_file_never_imported_stays_external(tmp_path, monkeypatch):
    """The narrowness that makes the pass safe. `other.py` defines `secret`,
    but `svc.py` does not import it, so `x.secret()` is NOT evidence that
    other.secret is alive. A project-wide name sweep would bind it and make
    genuinely dead code immortal."""
    project, _ = _index(tmp_path, monkeypatch, "noimport", {
        "other.py": """
def secret():
    return 1
""",
        "svc.py": """
def run(x):
    return x.secret()
""",
    })
    callers = _callers_of(project, "secret")
    assert callers == [], f"must not bind across a module that was never imported: {callers}"
    assert _is_ext(project, "secret"), "it must remain an external placeholder"


def test_an_ambiguous_name_is_left_unresolved(tmp_path, monkeypatch):
    """Two imported modules both define `shutdown`. Picking one would invent a
    caller relationship, so the edge stays a placeholder and the symbols stay
    on the dead list where a human can look at them.

    The name is `shutdown` and not `close` because `close` is in
    store.BUILTIN_NOISE: an `ext:close` edge is dropped at insert as builtin
    noise, so the test would pass for a reason that has nothing to do with
    ambiguity.
    """
    project, _ = _index(tmp_path, monkeypatch, "ambig", {
        "a.py": """
def shutdown():
    return 1
""",
        "b.py": """
def shutdown():
    return 2
""",
        "svc.py": """
import a
import b


def run(which):
    return which.shutdown()
""",
    })
    assert _callers_of(project, "shutdown") == [], "ambiguous name must not bind"
    assert _is_ext(project, "shutdown")


def test_the_pass_is_idempotent(tmp_path, monkeypatch):
    """A second index over unchanged sources must not duplicate or re-rewrite."""
    project, first = _index(tmp_path, monkeypatch, "idem", _TWO_MODULES)
    assert first["imported_call_edges"] >= 1, first
    before = _callers_of(project, "create_mission")
    again = index_project(project, scope="full")
    assert again["status"] == "completed", again
    assert _callers_of(project, "create_mission") == before


# --- 3: a call to a method defined LATER in the same class ------------------

def test_a_forward_self_call_binds_to_the_method(tmp_path, monkeypatch):
    """`self._later()` written above `def _later` is the same call as one
    written below it, and must produce the same edge.

    The extractor registers a method when it VISITS it, so source order decided
    whether a call resolved. Measured on [A]: 302 `self.<method>()` calls to a
    method of the same class, 20 of them forward references -- which is why
    five private methods of `DurableAuditWriter` were on the dead list while
    `_initialize_schema_once` calls four of them on consecutive lines.
    """
    project, _ = _index(tmp_path, monkeypatch, "fwdself", {"m.py": """
class C:
    def setup(self):
        self._later()

    def _later(self):
        return 1
"""})
    callers = _callers_of(project, "_later")
    assert callers, "a forward self-call must reach the method"
    assert callers[0][1] == "m.C.setup", callers


def test_a_backward_self_call_still_binds(tmp_path, monkeypatch):
    """The direction that already worked must keep working -- the pre-seed uses
    setdefault, so a name the visitor later registers is not clobbered."""
    project, _ = _index(tmp_path, monkeypatch, "backself", {"m.py": """
class C:
    def _earlier(self):
        return 1

    def setup(self):
        self._earlier()
"""})
    callers = _callers_of(project, "_earlier")
    assert callers and callers[0][1] == "m.C.setup", callers


def test_a_method_nobody_calls_is_still_uncalled(tmp_path, monkeypatch):
    """Pre-seeding a method must not invent a caller for it. If this ever
    starts finding an edge, the seed has begun binding names it should not and
    every unused method in the project has silently gone immortal."""
    project, _ = _index(tmp_path, monkeypatch, "nocall", {"m.py": """
class C:
    def setup(self):
        return 1

    def never_called(self):
        return 2
"""})
    assert _callers_of(project, "never_called") == []


# --- #32: the jedi re-check reads the line the extractor numbered -------------

def test_lsp_recheck_reads_the_call_line_the_extractor_numbered(tmp_path, monkeypatch):
    """The extractor numbers a call line the way ast does -- "\\n" only -- and
    lsp_resolver looked that number up in source.splitlines(), which also breaks
    at a form feed. One FF above the call moved the lookup a line up, the callee
    was not on it, and the edge read `unresolved`: a call jedi can confirm never
    reached jedi."""
    import pytest
    pytest.importorskip("jedi")
    from orchestrator.graph.lsp_resolver import resolve_apply, resolve_benchmark
    project, _ = _index(tmp_path, monkeypatch, "lspff", {
        "b.py": "def target():\n    return 1\n",
        "a.py": "import b\n# a\x0cb\n\n\ndef caller():\n    return b.target()\n",
    })
    out = resolve_benchmark(project)
    assert (out["sample"], out["confirmed"], out["errors"]) == (1, 1, []), out
    # resolve_apply keeps its own copy of the lookup. dry_run writes nothing and
    # reports applied=0 by design, so the confirmed edge shows in applied_edges.
    out = resolve_apply(project, dry_run=True)
    assert (out["sample"], out["confirmed"], len(out["applied_edges"]), out["applied"],
            out["errors"]) == (1, 1, 1, 0, []), out
