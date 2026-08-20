"""W6: the three dead-code reports must agree about the same symbol.

`cbm_dead_code`, the `orphan_symbols` template and `cbm_cycle_point`'s redundancy
dimension all answer "is this callable dead". Each carried its own copy of the
filter, and the copies drifted: `cycle_point._dead_ratio` said in its docstring
that it mirrored `query.dead_code_candidates` while applying neither the
entry-point nor the framework-dispatch exclusion. Measured on [A]: 3167 dead
reported against 664 actual, 2200 of the 2503 phantom entries being `test_*`
functions -- pytest reaches those by discovery, so they have no inbound edge by
construction and never will. Redundancy read 41% of the codebase as dead where
the true figure was 8.6%.

`config.LIVE_REF_EXISTS_SQL` had already unified the SQL half of the predicate
for exactly this reason. These tests exist because unifying a shared definition
does not stay unified on its own -- something has to fail when a fourth caller
reintroduces its own copy.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import cycle_point as _cycle_point
from orchestrator.graph import liveness
from orchestrator.graph.extractors.python import extract
from orchestrator.graph.indexer import index_project


# --- fixtures ---------------------------------------------------------------

def _db(tmp_path, monkeypatch, name, files):
    """Index `files` into an isolated graph DB and hand back an open conn."""
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / "graph" / name))
    project = tmp_path / name
    project.mkdir(parents=True, exist_ok=True)
    for rel, src in files.items():
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    result = index_project(str(project).replace("\\", "/"), scope="full")
    assert result["status"] == "completed", result
    conn = sqlite3.connect(str(tmp_path / "graph" / name / "graph.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql, binds=()):
    return conn.execute(sql, binds).fetchall()


# --- the drift this module exists to prevent --------------------------------

def test_a_test_function_is_never_counted_dead(tmp_path, monkeypatch):
    """The 2200-entry bug in one line. `test_adds` has no caller anywhere; pytest
    finds it by name. Counting it as dead is what made redundancy report 41%."""
    conn = _db(tmp_path, monkeypatch, "tf", {
        "m.py": "def helper():\n    return 1\n",
        "test_m.py": "from m import helper\n\n\ndef test_adds():\n    assert helper() == 1\n",
    })
    dead = {r["name"] for r in liveness.dead_callables(conn)}
    assert "test_adds" not in dead, sorted(dead)
    conn.close()


def test_dead_ratio_and_dead_code_candidates_count_the_same_symbols(tmp_path, monkeypatch):
    """The regression proper: the number cycle_point divides by must be the
    number the deletion work-list would show."""
    conn = _db(tmp_path, monkeypatch, "agree", {
        "m.py": (
            "def alive():\n    return 1\n\n\n"
            "def _orphan():\n    return 2\n\n\n"
            "def main():\n    return alive()\n\n\n"
            "def test_thing():\n    return 3\n"
        ),
    })
    ratio, dead, total = _cycle_point._dead_ratio(conn)
    listed = liveness.dead_callables(conn, unexported_only=True,
                                    exclude_decorated=True)
    assert dead == len(listed), (dead, [r["name"] for r in listed])
    assert {r["name"] for r in listed} == {"_orphan"}, [r["name"] for r in listed]
    assert ratio == dead / total
    conn.close()


def test_an_entry_point_name_is_excluded_from_every_report(tmp_path, monkeypatch):
    """`main` has no inbound edge by design -- the caller is the shell."""
    conn = _db(tmp_path, monkeypatch, "ep", {
        "m.py": "def main():\n    return 1\n",
    })
    for kwargs in ({}, {"unexported_only": True}, {"exclude_decorated": True},
                   {"unexported_only": True, "exclude_decorated": True}):
        dead = {r["name"] for r in liveness.dead_callables(conn, **kwargs)}
        assert "main" not in dead, (kwargs, sorted(dead))
    conn.close()


def test_a_method_on_a_class_with_an_unseen_base_is_excluded(tmp_path, monkeypatch):
    """A base outside the index dispatches by name at runtime. `do_GET` is the
    canonical shape: nothing in the source calls it, the HTTP server does."""
    conn = _db(tmp_path, monkeypatch, "fw", {
        "m.py": (
            "from http.server import BaseHTTPRequestHandler\n\n\n"
            "class H(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n        return 1\n\n"
            "    def _helper(self):\n        return 2\n"
        ),
    })
    dead = {r["name"] for r in liveness.dead_callables(conn)}
    assert "do_GET" not in dead, sorted(dead)
    # And the private one is NOT rescued: a base cannot dispatch to a name it
    # does not know, so excluding it would hide genuinely dead code.
    assert "_helper" in dead, sorted(dead)
    conn.close()


# --- the two flags are the ONLY legitimate divergence -----------------------

def test_an_exported_symbol_divides_the_two_reports(tmp_path, monkeypatch):
    """`__all__` makes a symbol reachable by import, so the deletion work-list
    skips it while the graph-shape view still reports it."""
    conn = _db(tmp_path, monkeypatch, "exp", {
        "m.py": '__all__ = ["public"]\n\n\ndef public():\n    return 1\n',
    })
    shape = {r["name"] for r in liveness.dead_callables(conn)}
    worklist = {r["name"] for r in
                liveness.dead_callables(conn, unexported_only=True)}
    assert "public" in shape, sorted(shape)
    assert "public" not in worklist, sorted(worklist)
    conn.close()


def test_a_decorated_symbol_divides_the_two_reports(tmp_path, monkeypatch):
    """A decorator can hand the function to a framework that calls it later,
    invisibly -- so the deletion work-list refuses to suggest it."""
    conn = _db(tmp_path, monkeypatch, "dec", {
        "m.py": (
            "def wrap(f):\n    return f\n\n\n"
            "@wrap\ndef decorated():\n    return 1\n"
        ),
    })
    shape = {r["name"] for r in liveness.dead_callables(conn)}
    worklist = {r["name"] for r in
                liveness.dead_callables(conn, exclude_decorated=True)}
    assert "decorated" in shape, sorted(shape)
    assert "decorated" not in worklist, sorted(worklist)
    conn.close()


def test_a_genuinely_dead_helper_survives_every_filter(tmp_path, monkeypatch):
    """The filters must not be so eager that nothing is ever reported: a report
    that always returns empty is as useless as one that lists everything."""
    conn = _db(tmp_path, monkeypatch, "real", {
        "m.py": "def _never_called():\n    return 1\n",
    })
    for kwargs in ({}, {"unexported_only": True, "exclude_decorated": True}):
        dead = {r["name"] for r in liveness.dead_callables(conn, **kwargs)}
        assert "_never_called" in dead, (kwargs, sorted(dead))
    conn.close()


def test_a_called_helper_is_never_reported(tmp_path, monkeypatch):
    conn = _db(tmp_path, monkeypatch, "live", {
        "m.py": "def _helper():\n    return 1\n\n\ndef run_it():\n    return _helper()\n",
    })
    dead = {r["name"] for r in liveness.dead_callables(conn)}
    assert "_helper" not in dead, sorted(dead)
    conn.close()



# --- the extractor-level root fix that made the flag observable --------------

def test_an_all_entry_does_not_keep_its_own_symbol_alive(tmp_path, monkeypatch):
    """`__all__ = ["public"]` is a declaration that the name may be imported. It
    is not a call site and it is not a dispatch registry.

    The W5 `name_literal` harvest took it for one: the string "public" resolved
    back to the `def public` three lines below and emitted a `dynamic_ref`, so
    the symbol read ALIVE on the strength of its own export line, with no caller
    anywhere in the project. Measured on [A] before the fix: 8 callables were
    alive only through a same-file literal, 1 of them purely through its own
    `__all__` entry -- and the flaw silently disabled `unexported_only` for all
    Python, because every export it was supposed to skip was already alive.
    """
    conn = _db(tmp_path, monkeypatch, "allnotref", {
        "m.py": '__all__ = ["public"]\n\n\ndef public():\n    return 1\n',
    })
    lits = _rows(conn, "SELECT name FROM nodes WHERE kind = 'name_literal';")
    assert [r["name"] for r in lits] == [], [r["name"] for r in lits]
    edges = _rows(conn, "SELECT COUNT(*) c FROM edges WHERE kind = 'dynamic_ref';")
    assert edges[0]["c"] == 0, edges[0]["c"]
    conn.close()


def test_a_real_registry_still_resolves_when_the_name_is_also_exported(tmp_path, monkeypatch):
    """The fix must skip the export line WITHOUT losing a genuine registry entry
    for the same name -- that would trade a false-alive for a false-dead, and a
    false-dead in a deletion work-list is the more expensive of the two.

    `handler` appears twice as a literal: once in `__all__`, once in the
    dispatch table. Only the second is a reference, and the `name_literal` node
    must land on the table's line, not on the export list's.
    """
    conn = _db(tmp_path, monkeypatch, "allreg", {
        "m.py": (
            '__all__ = ["handler"]\n\n\n'
            "def handler():\n    return 1\n\n\n"
            'TABLE = {"handler": handler}\n'
        ),
    })
    lits = _rows(conn, "SELECT name, start_line FROM nodes "
                       "WHERE kind = 'name_literal' ORDER BY start_line;")
    assert [(r["name"], r["start_line"]) for r in lits] == [("handler", 8)], \
        [(r["name"], r["start_line"]) for r in lits]
    dead = {r["name"] for r in liveness.dead_callables(conn)}
    assert "handler" not in dead, sorted(dead)
    conn.close()


def test_an_exported_symbol_with_no_reference_reads_dead_in_the_shape_view(tmp_path, monkeypatch):
    """The consequence of the fix, stated as behaviour: with the export line no
    longer manufacturing proof of life, `unexported_only` finally divides the two
    reports in Python the same way it always did in TypeScript, where `export`
    leaves no string behind. Before the fix this flag dropped 220 symbols on [A]
    and every one of them was `.ts`/`.tsx`."""
    conn = _db(tmp_path, monkeypatch, "expdead", {
        "m.py": '__all__ = ["public"]\n\n\ndef public():\n    return 1\n',
    })
    shape = {r["name"] for r in liveness.dead_callables(conn)}
    worklist = {r["name"] for r in
                liveness.dead_callables(conn, unexported_only=True)}
    assert "public" in shape, sorted(shape)
    assert "public" not in worklist, sorted(worklist)
    conn.close()

# --- structural guards ------------------------------------------------------

def test_liveness_is_a_leaf_module():
    """It is imported by `query` (above store/indexer/status) and by
    `cycle_point` (below them). Reaching upward from either would create this
    project's first upward dependency violation -- and degrade the very depth
    metric cycle_point reports."""
    src = (Path(liveness.__file__)).read_text(encoding="utf-8")
    imports = [l.strip() for l in src.splitlines()
               if l.startswith(("import ", "from "))]
    relative = [l for l in imports if l.startswith("from .")]
    assert relative == ["from .config import LIVE_REF_EXISTS_SQL, LIVE_REF_BINDS"], relative


def test_no_module_reimplements_the_filter():
    """A fourth caller writing its own copy is exactly how this drifted. The
    entry-point name set and the `inherits`-to-external query belong to
    liveness.py and must appear nowhere else under orchestrator/graph/."""
    root = Path(liveness.__file__).parent
    offenders = []
    for py in root.rglob("*.py"):
        if py.name == "liveness.py":
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        if '{"main", "run", "cli", "start", "serve", "app"}' in text:
            offenders.append((py.name, "entry-name set"))
        if "e.kind = 'inherits' AND t.kind IN ('external', 'import')" in text:
            offenders.append((py.name, "framework-dispatch query"))
    assert offenders == [], offenders


def test_query_still_exposes_the_helpers_under_their_old_names():
    """Existing call sites in query.py read unchanged; the aliases are load
    bearing, not decoration."""
    from orchestrator.graph import query
    assert query._is_entry_point is liveness.is_entry_point
    assert query._framework_dispatch_methods is liveness.framework_dispatch_methods
    assert query._is_framework_dispatch is liveness.is_framework_dispatch
    assert query._dead_callables is liveness.dead_callables
