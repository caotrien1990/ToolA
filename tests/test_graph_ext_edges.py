"""ALGO UPGRADE B+E: ext-edge orphan repair / builtin-noise filter + orphan_symbols
pagination (A_Q plan AQ_UPGRADE_PLAN_FINAL, worker KILOC5_ALGO_BE).

Covers:
  B1 — builtin-method noise: ext:get/str/append/join `calls` edges are dropped at
        insert_edges; surviving ext targets get a kind='external' placeholder node
        so JOIN resolvers stop dropping them (orphaned-join -> 0).
  B2 — self-loops: genuine recursion (factorial->factorial) is KEPT and tagged
        metadata.self_loop=true (evidence: 241/241 in-body same-name calls).
  E3 — orphan_symbols: offset pagination + total_count (true population) + limit
        raised to 500; backward compat (calls without offset unchanged).
"""
import os
os.environ["GRAPH_ENABLED"] = "1"  # must precede orchestrator.graph.config import

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import store as store
from orchestrator.graph.indexer import index_project
from orchestrator.graph.query import template_query, graph_find_symbol

# Builtin-method noise: d.get / str() / parts.append / "|".join all degrade to
# ext: targets (extractor emits ext:{name} for unresolved Name/Attribute calls).
# flask_route is an unknown external call -> ext:flask_route SURVIVES the filter.
FIX_NOISE = '''def use_builtins(d):
    items = d.get("k")
    s = str(items)
    parts = []
    parts.append(s)
    joined = "|".join(parts)
    return joined


def mystery_external():
    return flask_route()
'''

# Genuine recursion -> self-loop edge (source==target), kept + tagged.
FIX_REC = '''def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)
'''

# Orphan population for E3 pagination (handle_* excluded as an entry point).
FIX_ORPHANS = '''def orphan_a():
    return 1


def orphan_b():
    return 2


def orphan_c():
    return 3


def handle_event():
    return 4
'''

BUILTIN_NOISE_NAMES = (
    "get", "str", "push", "append", "join", "split", "strip", "items", "keys",
    "values", "pop", "len", "print", "format", "startswith", "endswith", "lower",
    "upper", "replace", "extend", "update", "add", "sort", "int", "float", "bool",
    "list", "dict", "set", "tuple", "isinstance", "hasattr", "getattr", "setattr",
    "repr", "type", "next", "iter", "map", "filter", "zip", "range", "enumerate",
    "sorted", "min", "max", "sum", "abs", "round", "open", "read", "write", "close",
)


class ExtEdgeBuiltinNoiseSuite(unittest.TestCase):
    """B1 + B2: noise filter, placeholder ext nodes, self-loop tagging."""

    @classmethod
    def setUpClass(cls):
        cls.tempdir = Path(tempfile.mkdtemp(prefix="ext_edge_be_"))
        (cls.tempdir / "noise.py").write_text(FIX_NOISE, encoding="utf-8")
        (cls.tempdir / "rec.py").write_text(FIX_REC, encoding="utf-8")
        cls.proj = str(cls.tempdir).replace("\\", "/")
        cls.run1 = index_project(cls.proj, scope="full")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tempdir, ignore_errors=True)

    def _db(self):
        return store.get_db(self.proj, readonly=True)

    def test_index_completed(self):
        self.assertEqual(self.run1["status"], "completed")
        self.assertEqual(self.run1["files_indexed"], 2)

    def test_no_builtin_noise_calls_edges(self):
        # B1: every builtin-method ext: target must be absent from edges.
        conn = self._db()
        try:
            for name in BUILTIN_NOISE_NAMES:
                n = conn.execute(
                    "SELECT COUNT(*) FROM edges WHERE kind='calls' AND target=?;",
                    (f"ext:{name}",)).fetchone()[0]
                self.assertEqual(n, 0, f"builtin-noise edge ext:{name} should be dropped")
        finally:
            conn.close()

    def test_surviving_ext_placeholder_node_exists_and_joinable(self):
        # B1: ext:flask_route survives (not noise) and has a placeholder node.
        conn = self._db()
        try:
            node = conn.execute(
                "SELECT id, kind FROM nodes WHERE id='ext:flask_route';").fetchone()
            self.assertIsNotNone(node, "placeholder node ext:flask_route missing")
            self.assertEqual(node["kind"], "external")
            # orphaned-join metric: no ext calls edge whose target lacks a node row.
            orphaned = conn.execute(
                "SELECT COUNT(*) FROM edges e WHERE e.kind='calls' AND e.target LIKE 'ext:%' "
                "AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id=e.target);").fetchone()[0]
            self.assertEqual(orphaned, 0, "surviving ext edges must be joinable")
            edge_n = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE kind='calls' AND target='ext:flask_route';"
            ).fetchone()[0]
            self.assertGreaterEqual(edge_n, 1, "ext:flask_route edge was dropped")
        finally:
            conn.close()

    def test_placeholder_node_not_in_symbol_lookup(self):
        # placeholder ext nodes must not shadow real symbols in find_symbol.
        hit = graph_find_symbol(self.proj, "flask_route")
        self.assertEqual(hit["results"], [],
                         "placeholder ext node leaked into symbol lookup")

    def test_self_loop_recursion_kept_and_tagged(self):
        # B2: factorial->factorial self-loop edge kept + metadata.self_loop=true.
        conn = self._db()
        try:
            row = conn.execute(
                "SELECT source, target, metadata FROM edges "
                "WHERE kind='calls' AND source=target "
                "AND source LIKE '%function:rec.factorial%';").fetchone()
            self.assertIsNotNone(row, "recursive self-loop edge missing")
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            self.assertTrue(meta.get("self_loop"), f"self_loop tag missing: {meta}")
        finally:
            conn.close()


class OrphanSymbolsPaginationSuite(unittest.TestCase):
    """E3: offset + total_count + limit 500."""

    @classmethod
    def setUpClass(cls):
        cls.tempdir = Path(tempfile.mkdtemp(prefix="orphan_pag_"))
        (cls.tempdir / "orph.py").write_text(FIX_ORPHANS, encoding="utf-8")
        cls.proj = str(cls.tempdir).replace("\\", "/")
        index_project(cls.proj, scope="full")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tempdir, ignore_errors=True)

    def _names(self, res):
        return {r["name"] for r in res["results"]}

    def test_total_count_true_population(self):
        r = template_query(self.proj, "orphan_symbols", limit=50)
        self.assertIsNone(r["error"])
        # 3 non-entry orphans (orphan_a/b/c); handle_event excluded as entry point.
        self.assertEqual(r["total_count"], 3)
        self.assertEqual(r["count"], 3)
        self.assertEqual(self._names(r), {"orphan_a", "orphan_b", "orphan_c"})

    def test_offset_pagination(self):
        page1 = template_query(self.proj, "orphan_symbols", limit=2, offset=0)
        self.assertEqual(page1["total_count"], 3)
        self.assertEqual(len(page1["results"]), 2)
        self.assertTrue(page1["truncated"], "page1 full + more remain -> truncated")
        page2 = template_query(self.proj, "orphan_symbols", limit=2, offset=2)
        self.assertEqual(page2["total_count"], 3)
        self.assertEqual(len(page2["results"]), 1)
        self.assertFalse(page2["truncated"], "last page -> not truncated")
        # disjoint pages
        self.assertEqual(self._names(page1) & self._names(page2), set())

    def test_backward_compat_no_offset(self):
        # existing callers pass no offset -> offset defaults 0, behaves as before.
        r = template_query(self.proj, "orphan_symbols", limit=50)
        self.assertEqual(r["count"], 3)
        self.assertIn("total_count", r)

    def test_limit_500_accepted(self):
        # limit raised 200 -> 500: 500 accepted, returns all 3 (no clamp-to-200).
        r = template_query(self.proj, "orphan_symbols", limit=500)
        self.assertIsNone(r["error"])
        self.assertEqual(r["total_count"], 3)
        self.assertEqual(len(r["results"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
