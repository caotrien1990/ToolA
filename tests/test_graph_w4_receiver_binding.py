"""W4: a call through an IMPORTED receiver must not hijack a same-named local.

`code_tools._log_app_session` is a five-line re-export wrapper whose entire body
is `return _session_logs_module._log_app_session(...)`. The extractor matched the
call on the bare attribute `_log_app_session`, found the wrapper itself in
`_local_defs`, and emitted a SELF-LOOP -- so the wrapper looked alive (it calls
itself) and `logs._log_app_session`, the function that actually does the work,
sat on the dead list with zero callers. Eight symbols were falsely dead this way.

The second half is the resolver. Dropping the hijacked edge alone would leave the
call external, so the extractor records WHICH import bound the receiver and
`_resolve_imported_calls` follows that module -- transitively, because
`code_tools.write_fix` is itself a re-export of `orchestrator.tools.write`, which
re-exports `core.write_fix`.

The receiver is what makes the resolution honest, and the negative tests below
are the point: the previous all-imports sweep answered `os.walk` with
`tools_v15.walk` and `subprocess.run` with `ExecutorLane.run` -- 62 edges on [A],
every one of them wrong. A wrong call edge is worse than a missing one, because
it makes dead code look alive forever.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.config import graph_db_path
from orchestrator.graph.extractors.python import extract
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
    """(source_qualified_name, target_id) of every call INTO a DEFINITION."""
    conn = sqlite3.connect(graph_db_path(project))
    conn.row_factory = sqlite3.Row
    try:
        return [(r["sq"], r["tid"]) for r in conn.execute(
            "SELECT s.qualified_name sq, t.id tid FROM edges e "
            "JOIN nodes s ON s.id = e.source JOIN nodes t ON t.id = e.target "
            "WHERE e.kind='calls' AND t.name=? "
            "AND t.kind NOT IN ('external','import');", (name,))]
    finally:
        conn.close()


def _ext_edges(source, attr):
    """(target, metadata) of external `calls` edges the extractor emits for attr.

    The fixture keeps its leading newline: node ids carry the line number, so
    stripping it would shift every id and the assertions would stop matching
    what they name.
    """
    r = extract(source, file_path="m.py", project_root=None)
    return [(e["target"], e.get("metadata") or {}) for e in r["edges"]
            if e["kind"] == "calls" and e["target"] == "ext:" + attr]


def _self_loops(source):
    r = extract(source, file_path="m.py", project_root=None)
    return [e for e in r["edges"]
            if e["kind"] == "calls" and e["source"] == e["target"]]


# --- the extractor half: the hijack is gone, the receiver is recorded --------

_WRAPPER = """
import other_mod


def helper(x):
    return other_mod.helper(x)
"""


def test_an_imported_receiver_does_not_bind_to_the_same_named_local():
    """The exact shape of the bug: a wrapper that forwarded to itself."""
    assert _self_loops(_WRAPPER) == [], "the wrapper must not call itself"


def test_the_edge_records_which_import_bound_the_receiver():
    """Dropping the wrong edge is only half a fix -- without the receiver the
    call is simply lost, and the real callee stays dead."""
    edges = _ext_edges(_WRAPPER, "helper")
    assert len(edges) == 1, edges
    recv = edges[0][1].get("recv_import")
    assert recv and ":import:other_mod:" in recv, edges


def test_a_plain_receiver_still_suffix_matches():
    """The behaviour that was always right must not regress: `store.get_db()`
    where `store` is a local object, not an import, still binds to `get_db`."""
    src = """
def get_db():
    return 1


def run(store):
    return store.get_db()
"""
    assert _ext_edges(src, "get_db") == [], "a non-import receiver must resolve locally"


def test_a_receiver_rebound_inside_the_function_records_no_receiver():
    """`os = _FakeOs()` in a test body makes the module-level import irrelevant,
    so naming that module on the edge would point the resolver at the wrong
    file. The suffix match stays suppressed either way -- knowing less about
    the receiver is never a reason to guess harder."""
    src = """
import other_mod


def helper(x):
    return x


def run():
    other_mod = _Fake()
    return other_mod.helper(1)
"""
    edges = _ext_edges(src, "helper")
    assert len(edges) == 1, edges
    assert "recv_import" not in edges[0][1], edges


# --- the resolver half: the receiver module is followed, transitively --------

def test_a_call_through_an_imported_module_reaches_the_real_definition(tmp_path, monkeypatch):
    project, _ = _index(tmp_path, monkeypatch, "recv", {
        "logs.py": """
def _log_app_session(x):
    return x
""",
        "wrapper.py": """
import logs


def _log_app_session(x):
    return logs._log_app_session(x)
""",
    })
    callers = _callers_of(project, "_log_app_session")
    assert callers, "the call must reach a definition"
    targets = {t for _, t in callers}
    assert targets == {"logs.py:function:logs._log_app_session:1"}, callers


def test_a_re_export_chain_is_followed(tmp_path, monkeypatch):
    """`code_tools.write_fix` is not defined in code_tools -- it is imported
    from a package whose __init__ imports it from core. Stopping at the first
    module would leave 60 of these unresolved on [A]."""
    project, _ = _index(tmp_path, monkeypatch, "chain", {
        "core.py": """
def write_fix(x):
    return x
""",
        "facade.py": """
from core import write_fix
""",
        "caller.py": """
import facade


def run():
    return facade.write_fix(1)
""",
    })
    callers = _callers_of(project, "write_fix")
    assert ("caller.run", "core.py:function:core.write_fix:1") in callers, callers


def test_a_stdlib_receiver_never_binds_to_a_project_definition(tmp_path, monkeypatch):
    """The measured regression this whole fix exists to prevent. `os` is not an
    indexed module, so `os.walk(...)` must stay external -- the old sweep bound
    it to a project function that merely happened to be named `walk`."""
    project, _ = _index(tmp_path, monkeypatch, "stdlib", {
        "helpers.py": """
def walk(x):
    return x
""",
        "caller.py": """
import os
import helpers


def run(p):
    return os.walk(p)
""",
    })
    assert _callers_of(project, "walk") == [], "os.walk must not bind to helpers.walk"


def test_a_receiver_that_does_not_define_the_name_stays_external(tmp_path, monkeypatch):
    """A project module IS the receiver, but does not define the attribute --
    a runtime attribute, a class instance, a dynamically set member. Guessing
    another module here is exactly the wrong-edge failure mode."""
    project, _ = _index(tmp_path, monkeypatch, "nodef", {
        "cfg.py": """
VALUE = 1
""",
        "helpers.py": """
def compute(x):
    return x
""",
        "caller.py": """
import cfg
import helpers


def run():
    return cfg.compute(1)
""",
    })
    assert _callers_of(project, "compute") == [], "must not fall back to helpers.compute"


def test_a_callable_passed_as_a_value_through_a_receiver_resolves(tmp_path, monkeypatch):
    """`asyncio.to_thread(_query.nodes_query, ...)` -- a reference, not a call,
    and cross-file. Both `nodes_query` and `edges_query` were dead because of
    it."""
    project, _ = _index(tmp_path, monkeypatch, "valref", {
        "q.py": """
def nodes_query(x):
    return x
""",
        "caller.py": """
import q


def run(runner):
    return runner(q.nodes_query, 1)
""",
    })
    callers = _callers_of(project, "nodes_query")
    assert callers and callers[0][1] == "q.py:function:q.nodes_query:1", callers


def test_the_receiver_pass_is_idempotent(tmp_path, monkeypatch):
    project, first = _index(tmp_path, monkeypatch, "idem2", {
        "logs.py": """
def emit(x):
    return x
""",
        "wrapper.py": """
import logs


def emit(x):
    return logs.emit(x)
""",
    })
    assert first["imported_call_stats"]["recv_rebound"] >= 1, first
    before = _callers_of(project, "emit")
    again = index_project(project, scope="full")
    assert again["status"] == "completed", again
    assert _callers_of(project, "emit") == before
