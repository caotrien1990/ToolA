"""P0 graph fixture suite (A_Q plan Phase 2 Step 12).

Covers plan verify gates: schema DDL/integrity/reserved-name, python extractor
(function/class/import/call/ambiguous/confidence), indexer full + rerun-unchanged
+ broken-file fail-open, graph_find_symbol (exact/FTS/ambiguous/unknown),
cbm_trace_path (known path, cycle-safe, no-path, not-found, truncation, timeout),
graph_status (counts match DB, WAL, stale detection). Dynamic tempdir fixtures.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"  # must precede orchestrator.graph.config import

import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import store as store
from orchestrator.graph.extractors.python import extract as py_extract
from orchestrator.graph.indexer import index_project
from orchestrator.graph.status import graph_status
from orchestrator.graph.query import graph_find_symbol, cbm_trace_path

FIX_MOD_A = '''import os
from mod_b import gamma


def alpha():
    return beta() + gamma()


def beta():
    return 1


def dupe():
    return "a"


class Widget:
    def render(self):
        return alpha()
'''

FIX_MOD_B = '''import mod_a


def gamma():
    return 2


def delta():
    return gamma()


def dupe():
    return "b"


def isolated_leaf():
    return 42


class Base:
    pass


class Child(Base):
    pass


class ExtChild(dict):
    pass
'''

ALLOWED_CBM = {1.0, 0.9, 0.7}
ALLOWED_LABELS = {"static", "inferred", "dynamic", "unknown"}


class P0GraphFixtureSuite(unittest.TestCase):
    """Shared fixture project indexed once; read-only assertions per test."""

    @classmethod
    def setUpClass(cls):
        cls.tempdir = Path(tempfile.mkdtemp(prefix="p0_graph_fix_"))
        (cls.tempdir / "mod_a.py").write_text(FIX_MOD_A, encoding="utf-8")
        (cls.tempdir / "mod_b.py").write_text(FIX_MOD_B, encoding="utf-8")
        cls.proj = str(cls.tempdir).replace("\\", "/")
        cls.run1 = index_project(cls.proj, scope="full")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tempdir, ignore_errors=True)

    # ── Step 4/5: schema + store ──
    def test_schema_ddl_integrity_wal_reserved_name(self):
        self.assertEqual(store.integrity_check(self.proj), "ok")
        conn = store.get_db(self.proj, readonly=True)
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view');")}
            for required in ("nodes", "edges", "files", "index_runs"):
                self.assertIn(required, names)
            # graph FTS tables must not collide with reserved [Q] name
            self.assertNotIn("rag_chunks_fts", names)
            self.assertTrue(any("fts" in n for n in names),
                            f"expected a graph FTS table, got {names}")
            wal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(wal.lower(), "wal")
        finally:
            conn.close()

    # ── Step 6: extractor ──
    def test_extractor_function_class_import_call(self):
        res = py_extract(FIX_MOD_A, file_path="mod_a.py", project_root=self.proj)
        self.assertEqual(res["errors"], [])
        names = {n["name"] for n in res["nodes"]}
        for expected in ("alpha", "beta", "dupe", "Widget", "render"):
            self.assertIn(expected, names)
        kinds = {n["name"]: n["kind"] for n in res["nodes"]}
        self.assertEqual(kinds["Widget"], "class")
        self.assertEqual(kinds["alpha"], "function")
        edge_kinds = {e["kind"] for e in res["edges"]}
        self.assertIn("calls", edge_kinds)
        self.assertIn("imports", edge_kinds)
        for e in res["edges"]:
            c = e["confidence"]
            ok = c in ALLOWED_CBM or 0.3 <= c <= 0.7
            self.assertTrue(ok, f"confidence {c} outside CBM scale: {e}")
            self.assertIn(e["cbm_label"], ALLOWED_LABELS)
        # alpha -> beta call edge must exist
        self.assertTrue(any(e["kind"] == "calls" and e["target"].endswith("beta")
                            or e["kind"] == "calls" and "beta" in str(e["target"])
                            for e in res["edges"]),
                        "missing alpha->beta call edge")

    def test_extractor_inherits_resolved_and_external(self):
        res = py_extract(FIX_MOD_B, file_path="mod_b.py", project_root=self.proj)
        inh = [e for e in res["edges"] if e["kind"] == "inherits"]
        self.assertEqual(len(inh), 2)
        by_src = {e["source"]: e for e in inh}
        child = next(e for s, e in by_src.items() if ":Child" in s or "Child" in s)
        self.assertTrue(child["target"].startswith("mod_b.py:class:"),
                        f"local base must resolve to node id, got {child['target']}")
        extchild = next(e for s, e in by_src.items() if "ExtChild" in s)
        self.assertEqual(extchild["target"], "ext:dict")

    def test_extractor_forward_reference_call_resolved(self):
        src = ("def caller():\n"
               "    return helper()\n\n"
               "def helper():\n"
               "    return 1\n")
        res = py_extract(src, file_path="fwd.py", project_root=self.proj)
        calls = [e for e in res["edges"] if e["kind"] == "calls"]
        self.assertTrue(calls, "missing forward-reference call edge")
        self.assertTrue(calls[0]["target"].startswith("fwd.py:function:"),
                        f"forward ref degraded to ext: {calls[0]['target']}")

    def test_extractor_inherits_ignores_function_shadow(self):
        src = ("def Shadow():\n"
               "    return 1\n\n"
               "class Kid(Shadow):\n"
               "    pass\n")
        res = py_extract(src, file_path="shadow.py", project_root=self.proj)
        inh = [e for e in res["edges"] if e["kind"] == "inherits"]
        self.assertEqual(len(inh), 1)
        self.assertEqual(inh[0]["target"], "ext:Shadow",
                         "function must not capture an inherits edge")

    def test_extractor_no_duplicate_edges(self):
        src = ("def f():\n"
               "    return g(1) + g(2) + g(3)\n\n"
               "def g(x):\n"
               "    return x\n")
        res = py_extract(src, file_path="dup_fix.py", project_root=self.proj)
        keys = [(e["source"], e["target"], e["kind"], e.get("line")) for e in res["edges"]]
        self.assertEqual(len(keys), len(set(keys)), f"duplicate edges: {keys}")

    # ── Step 7: indexer ──
    def test_indexer_full_run_counts_and_gates(self):
        r = self.run1
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["files_indexed"], 2)
        self.assertGreater(r["nodes"], 0)
        self.assertGreater(r["edges"], 0)
        self.assertTrue(r["meets_gates"]["duration"])
        self.assertTrue(r["meets_gates"]["no_source_mutation"])
        # source files untouched (no mutation)
        self.assertEqual((self.tempdir / "mod_a.py").read_text(encoding="utf-8"), FIX_MOD_A)

    def test_indexer_rerun_parses_zero_unchanged(self):
        r2 = index_project(self.proj, scope="full")
        self.assertEqual(r2["files_indexed"], 0)
        self.assertEqual(r2["files_unchanged"], 2)
        self.assertEqual(r2["nodes"], self.run1["nodes"])
        self.assertEqual(r2["edges"], self.run1["edges"])

    def test_indexer_broken_file_fail_open(self):
        broken = Path(tempfile.mkdtemp(prefix="p0_graph_broken_"))
        try:
            (broken / "good.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
            (broken / "broken.py").write_text("def broken(:\n    ???\n", encoding="utf-8")
            r = index_project(str(broken).replace("\\", "/"), scope="full")
            self.assertEqual(r["status"], "completed")
            self.assertGreaterEqual(len(r["errors"]), 1)
            hits = graph_find_symbol(str(broken).replace("\\", "/"), "ok")
            self.assertEqual(len(hits["results"]), 1)
        finally:
            shutil.rmtree(broken, ignore_errors=True)

    def test_indexer_parallel_deterministic_counts(self):
        proj = Path(tempfile.mkdtemp(prefix="p0_graph_parallel_"))
        try:
            for i in range(4):
                (proj / f"mod_{i}.py").write_text(
                    f"def f_{i}():\n    return {i}\n", encoding="utf-8"
                )
            root = str(proj).replace("\\", "/")
            with mock.patch.dict(os.environ, {"ORCHESTRATOR_PARALLEL_INDEX": "on"}, clear=False):
                r1 = index_project(root, scope="full")
                store.reset_db(root)
                r2 = index_project(root, scope="full")
            self.assertEqual((r1["files_indexed"], r1["nodes"], r1["edges"]),
                             (r2["files_indexed"], r2["nodes"], r2["edges"]))
            self.assertEqual(r1["files_indexed"], 4)
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_indexer_parallel_kill_switch_uses_serial_path(self):
        proj = Path(tempfile.mkdtemp(prefix="p0_graph_serial_"))
        try:
            for i in range(3):
                (proj / f"serial_{i}.py").write_text(
                    f"def serial_{i}():\n    return {i}\n", encoding="utf-8"
                )
            root = str(proj).replace("\\", "/")
            with mock.patch.dict(os.environ, {"ORCHESTRATOR_PARALLEL_INDEX": "off"}, clear=False), \
                 mock.patch("orchestrator.graph.indexer.ProcessPoolExecutor",
                            side_effect=AssertionError("parallel path used")):
                r = index_project(root, scope="full")
            self.assertEqual(r["files_indexed"], 3)
            self.assertEqual(r["status"], "completed")
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    # ── Step 9/10: find_symbol ──
    def test_find_symbol_exact_ambiguous_unknown(self):
        hit = graph_find_symbol(self.proj, "alpha")
        self.assertIsNone(hit["error"])
        self.assertGreaterEqual(len(hit["results"]), 1)
        self.assertEqual(hit["results"][0]["file_path"], "mod_a.py")
        amb = graph_find_symbol(self.proj, "dupe")
        self.assertGreaterEqual(len(amb["results"]), 2,
                                "ambiguous symbol must return all candidates")
        none = graph_find_symbol(self.proj, "zzz_not_here")
        self.assertEqual(none["results"], [])
        self.assertIsNone(none["error"])

    # ── Step 9: trace_path ──
    def _node_id(self, name, file_path=None):
        hits = graph_find_symbol(self.proj, name)["results"]
        if file_path:
            hits = [h for h in hits if h["file_path"] == file_path]
        self.assertTrue(hits, f"fixture symbol {name} not found")
        return hits[0]["id"]

    def test_trace_known_path_out(self):
        tr = cbm_trace_path(self.proj, self._node_id("alpha"), direction="out", depth=3)
        self.assertIsNone(tr["error"])
        self.assertTrue(any("beta" in str(e["target"]) for e in tr["edges"]),
                        f"alpha out-trace missing beta: {tr['edges']}")

    def test_trace_inbound_callers(self):
        tr = cbm_trace_path(self.proj, self._node_id("gamma", "mod_b.py"),
                            direction="in", depth=2)
        self.assertIsNone(tr["error"])
        self.assertTrue(any("delta" in str(e["source"]) or "alpha" in str(e["source"])
                            for e in tr["edges"]),
                        f"gamma in-trace missing callers: {tr['edges']}")

    def test_trace_cycle_safe_import_cycle(self):
        # mod_a imports mod_b, mod_b imports mod_a — BFS must terminate
        tr = cbm_trace_path(self.proj, self._node_id("alpha"), direction="both", depth=5)
        self.assertIsNone(tr["error"])
        self.assertLessEqual(tr["nodes_seen"], 200)

    def test_trace_no_path_leaf(self):
        tr = cbm_trace_path(self.proj, self._node_id("isolated_leaf"), direction="out", depth=3)
        self.assertIsNone(tr["error"])
        self.assertEqual([e for e in tr["edges"] if e["kind"] == "calls"], [])

    def test_trace_start_node_not_found(self):
        tr = cbm_trace_path(self.proj, "no/such.py:function:x.y:1")
        self.assertEqual(tr["truncation_reason"], "start_node_not_found")
        self.assertEqual(tr["edges"], [])

    def test_trace_truncation_max_nodes(self):
        tr = cbm_trace_path(self.proj, self._node_id("alpha"), direction="both",
                            depth=5, max_nodes=1)
        self.assertTrue(tr["truncated"])
        self.assertEqual(tr["truncation_reason"], "max_symbols")

    def test_trace_timeout_exhausted_budget(self):
        # timeout_s=-1 makes elapsed>budget true on the first loop check —
        # deterministic on Windows where monotonic() can tick coarsely (0.0s
        # elapsed is NOT > 0.0s budget, so timeout_s=0 was flaky)
        tr = cbm_trace_path(self.proj, self._node_id("alpha"), direction="both",
                            depth=5, timeout_s=-1.0)
        self.assertTrue(tr["truncated"])
        self.assertEqual(tr["truncation_reason"], "timeout")

    # ── Step 8: status ──
    def test_status_matches_db_and_wal(self):
        st = graph_status(self.proj)
        self.assertTrue(st["enabled"])
        self.assertTrue(st["db_present"])
        self.assertEqual(st["integrity"], "ok")
        self.assertEqual(st["wal"], "wal")
        conn = store.get_db(self.proj, readonly=True)
        try:
            counts = store.status_counts(conn)
        finally:
            conn.close()
        self.assertEqual(st["nodes"], counts["nodes"])
        self.assertEqual(st["edges"], counts["edges"])
        self.assertEqual(st["files"], counts["files"])

    def test_status_stale_after_touch(self):
        target = self.tempdir / "mod_b.py"
        original = target.read_text(encoding="utf-8")
        try:
            target.write_text(original + "\n# touched\n", encoding="utf-8")
            st = graph_status(self.proj)
            self.assertGreaterEqual(st["stale_files"], 1)
        finally:
            target.write_text(original, encoding="utf-8")
            index_project(self.proj, scope="full")  # restore graph freshness


if __name__ == "__main__":
    unittest.main(verbosity=2)
