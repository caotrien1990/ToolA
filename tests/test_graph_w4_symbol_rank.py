"""W4: a name lookup must return the DEFINITION, not the import alias.

`from util import helper` creates an `import` node named `helper` in the
importing file, alongside the `function` node named `helper` in util.py. Both
are real, searchable symbols -- unlike the `external`/`name_literal` rows, which
store.NON_SYMBOL_KINDS already filters out -- and both answer a lookup for
"helper".

`find_nodes_by_name` had no ORDER BY, so `LIMIT 1` returned whichever row sqlite
reached first. `query._resolve_node` asks for exactly limit=1 and treats the
answer as THE node for callers/callees/impact, so resolving to the alias reports
an empty caller list for a function that has call sites -- a symbol with 30 real
references reads as "nothing calls this".

Measured on [A]'s own graph.db: 584 names have both a definition and a
binding-site row, and 370 of them -- nearly two in three -- resolved to the
non-definition. It stayed invisible while the extractor ALSO bound the call edge
to the alias: the wrong lookup and the wrong edge agreed. Fixing the edge (W4
_resolve_imported_calls part 2) is what made the ranking bug observable.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.config import graph_db_path
from orchestrator.graph.indexer import index_project
from orchestrator.graph.query import graph_find_symbol, template_query

_FILES = {
    "util.py": """
def helper():
    return 1
""",
    "core.py": """
from util import helper


def main():
    return helper()
""",
}


def _index(tmp_path, monkeypatch, name, files):
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / "graph" / name))
    project = tmp_path / name
    project.mkdir()
    for rel, s in files.items():
        (project / rel).write_text(s.lstrip(), encoding="utf-8")
    project = str(project).replace("\\", "/")
    result = index_project(project, scope="full")
    assert result["status"] == "completed", result
    return project


def test_a_definition_outranks_an_import_alias_of_the_same_name(tmp_path, monkeypatch):
    project = _index(tmp_path, monkeypatch, "rank", _FILES)
    hits = graph_find_symbol(project, "helper").get("results") or []
    kinds = [h["kind"] for h in hits]
    assert "function" in kinds and "import" in kinds, kinds
    assert kinds[0] == "function", f"the definition must come first: {kinds}"


def test_the_alias_is_still_returned_not_filtered_out(tmp_path, monkeypatch):
    """The fix is an ORDER BY, not a WHERE. A caller asking for every node named
    `helper` must still see the binding site -- ranking it last is enough, and
    dropping it would break anyone using the lookup to find import sites."""
    project = _index(tmp_path, monkeypatch, "rankkeep", _FILES)
    hits = graph_find_symbol(project, "helper").get("results") or []
    assert any(h["kind"] == "import" for h in hits), hits


def test_callers_resolves_through_the_definition(tmp_path, monkeypatch):
    """The behaviour the ranking exists for: template_query('callers') goes
    through _resolve_node(limit=1). This is the assertion that was failing in
    test_p1_graph_advanced_suite."""
    project = _index(tmp_path, monkeypatch, "rankcallers", _FILES)
    res = template_query(project, "callers", name="helper")
    names = [r["name"] for r in (res.get("results") or [])]
    assert "main" in names, res


def test_a_name_only_an_alias_defines_still_resolves(tmp_path, monkeypatch):
    """Ranking must not become a filter by accident. `json` is imported here and
    defined nowhere in the project; the lookup must still find the alias rather
    than return nothing."""
    project = _index(tmp_path, monkeypatch, "rankonly", {
        "core.py": """
import json


def main():
    return json.dumps({})
""",
    })
    hits = graph_find_symbol(project, "json").get("results") or []
    assert hits, "a name with no definition must still resolve to its alias"


def test_the_order_is_stable_across_two_indexes(tmp_path, monkeypatch):
    """file_path/start_line break the tie, so the answer does not depend on
    which file the walker happened to reach first."""
    project = _index(tmp_path, monkeypatch, "rankstable", _FILES)
    first = [h["id"] for h in graph_find_symbol(project, "helper")["results"]]
    assert index_project(project, scope="full")["status"] == "completed"
    assert [h["id"] for h in graph_find_symbol(project, "helper")["results"]] == first


def test_the_fts_fallback_path_ranks_the_same_way(tmp_path, monkeypatch):
    """graph_find_symbol falls back to FTS when the exact-name lookup misses.
    Both paths feed the same first-result consumers, so a caller must not get a
    definition from one and an alias from the other."""
    project = _index(tmp_path, monkeypatch, "rankfts", _FILES)
    # a prefix that no exact name matches, forcing the FTS branch
    hits = graph_find_symbol(project, "helpe").get("results") or []
    assert hits, "prefix fallback must find helper"
    defs = [i for i, h in enumerate(hits) if h["kind"] == "function"]
    aliases = [i for i, h in enumerate(hits) if h["kind"] == "import"]
    if defs and aliases:
        assert min(defs) < min(aliases), [h["kind"] for h in hits]
