"""W4: a method the graph cannot prove is uncalled must not read as dead.

`_Extractor.visit_Call` has zero inbound call edges and never will -- nothing
calls it by name. `ast.NodeVisitor.generic_visit` builds the name from the AST
node type at runtime, so the call site does not exist in any source file. Same
shape for `do_GET` on a BaseHTTPRequestHandler subclass.

Measured on [A]: 16 of the 92 `orchestrator/` dead entries were this, and every
one of them is load-bearing. A dead-code list is a deletion work-list; an entry
that is always wrong teaches the reader to distrust the entire list.

The negative tests are the important half. The rule keys on a base the graph
cannot see INSIDE, so it must not swallow a method on a project-local base
(whose call sites ARE indexed) or a private method (a base cannot dispatch a
name it does not know).
"""
import os
os.environ["GRAPH_ENABLED"] = "1"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.indexer import index_project
from orchestrator.graph.query import dead_code_candidates, template_query


def _index(tmp_path, monkeypatch, name, files):
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / "graph" / name))
    project = tmp_path / name
    project.mkdir()
    for rel, src_text in files.items():
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src_text.lstrip(), encoding="utf-8")
    project = str(project).replace("\\", "/")
    result = index_project(project, scope="full")
    assert result["status"] == "completed", result
    return project


def _dead_names(project):
    r = dead_code_candidates(project, limit=200)
    assert r["error"] is None, r["error"]
    return {c["name"] for c in r["candidates"]}


def _orphan_names(project):
    r = template_query(project, "orphan_symbols", limit=500)
    assert r["error"] is None, r["error"]
    return {c["name"] for c in r["results"]}


_VISITOR = {"v.py": """
import ast


class Walker(ast.NodeVisitor):
    def visit_Call(self, node):
        return node

    def _unused_helper(self):
        return 1
"""}


def test_a_visitor_hook_is_not_reported_dead(tmp_path, monkeypatch):
    """The measured case: NodeVisitor dispatches by node type, never by name."""
    project = _index(tmp_path, monkeypatch, "visitor", _VISITOR)
    assert "visit_Call" not in _dead_names(project)


def test_the_same_symbol_is_excluded_from_orphan_symbols_too(tmp_path, monkeypatch):
    """Both detectors share the rule, so they cannot disagree about one symbol
    -- the exact failure LIVE_REF_EXISTS_SQL was introduced to end in W5."""
    project = _index(tmp_path, monkeypatch, "visitor_orphan", _VISITOR)
    assert "visit_Call" not in _orphan_names(project)


def test_a_private_method_on_the_same_class_is_still_dead(tmp_path, monkeypatch):
    """The rule must stay narrow. A base cannot dispatch to a name it does not
    know, so `_unused_helper` is exactly as dead as it looks -- if this stops
    failing to be excluded, the rule has become a blanket amnesty for any class
    with an unindexed base (measured: 337 symbols hidden instead of 316)."""
    project = _index(tmp_path, monkeypatch, "private", _VISITOR)
    assert "_unused_helper" in _dead_names(project)


def test_a_method_on_a_project_local_base_is_still_dead(tmp_path, monkeypatch):
    """The other half of the narrowness: when the base IS indexed, any dispatch
    through it is a call site the graph can see, so silence really is silence."""
    project = _index(tmp_path, monkeypatch, "localbase", {"m.py": """
class Base:
    pass


class Child(Base):
    def never_called(self):
        return 1
"""})
    assert "never_called" in _dead_names(project)


def test_a_plain_function_is_unaffected(tmp_path, monkeypatch):
    """No class, no inherits edge, no exemption -- the common case must not move."""
    project = _index(tmp_path, monkeypatch, "plainfn", {"m.py": """
def orphaned():
    return 1
"""})
    assert "orphaned" in _dead_names(project)
