"""P1 advanced graph suite (A_Q plan Phase 3 Steps 14-17).

Step 14: incremental_index — edit/delete/rename fixtures update the DB, stale
rows removed (orphan=0), <10s wall, full-reindex fallback on failure.
Step 15: 8 template queries + unsupported-template error.
Step 16: cbm_get_architecture cites exact files + early-exit metadata.
Step 17: dead-code candidates exclude entry points/callbacks, cap confidence.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"  # must precede orchestrator.graph.config import

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import store as store
from orchestrator.graph.indexer import (index_project, incremental_index,
                                        detect_changes, compute_file_hash)
from orchestrator.graph.query import (template_query, cbm_get_architecture,
                                      dead_code_candidates, graph_find_symbol,
                                      TEMPLATES)

FIX_CORE = '''from util import helper


def main():
    return helper() + used_twice()


def used_twice():
    return 1


def never_called_public():
    return 2


def _never_called_private():
    return 3


def handle_request():
    return used_twice()
'''

FIX_UTIL = '''def helper():
    return 42
'''


def _orphans(proj):
    conn = store.get_db(proj, readonly=True)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM edges e LEFT JOIN nodes n ON e.target=n.id "
            "WHERE n.id IS NULL AND e.target NOT LIKE 'ext:%';").fetchone()[0]
    finally:
        conn.close()


class P1AdvancedGraphSuite(unittest.TestCase):

    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="p1_graph_adv_"))
        (self.tempdir / "core.py").write_text(FIX_CORE, encoding="utf-8")
        (self.tempdir / "util.py").write_text(FIX_UTIL, encoding="utf-8")
        self.proj = str(self.tempdir).replace("\\", "/")
        index_project(self.proj, scope="full")
        # dependency edges are built by incremental passes; seed one no-op pass
        incremental_index(self.proj)

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    # ── Step 14 ──
    def test_incremental_noop_skips_everything(self):
        r = incremental_index(self.proj)
        self.assertEqual(r["status"], "completed")
        self.assertFalse(r["fallback"])
        self.assertEqual(r["reindexed"], 0)
        self.assertEqual(r["skipped"], 2)
        self.assertLess(r["wall_ms"], 10_000)

    def test_incremental_edit_updates_and_removes_stale(self):
        (self.tempdir / "core.py").write_text(
            FIX_CORE.replace("def used_twice():", "def renamed_fn():")
                    .replace("used_twice()", "renamed_fn()"), encoding="utf-8")
        r = incremental_index(self.proj)
        self.assertEqual(r["reindexed"], 1)
        self.assertLess(r["wall_ms"], 10_000, "single-file edit must be <10s")
        self.assertEqual(r["orphan_after"], 0)
        self.assertEqual(graph_find_symbol(self.proj, "used_twice")["results"], [],
                         "stale node must be removed")
        self.assertTrue(graph_find_symbol(self.proj, "renamed_fn")["results"])

    def test_incremental_delete_file(self):
        (self.tempdir / "util.py").unlink()
        r = incremental_index(self.proj)
        self.assertEqual(r["deleted"], 1)
        self.assertEqual(r["orphan_after"], 0)
        # the function node dies with util.py; core.py's import NODE legitimately
        # remains (its source line still exists in core.py)
        fn_hits = [h for h in graph_find_symbol(self.proj, "helper")["results"]
                   if h["kind"] == "function"]
        self.assertEqual(fn_hits, [])

    def test_incremental_rename_is_delete_plus_add(self):
        (self.tempdir / "util.py").rename(self.tempdir / "util_renamed.py")
        r = incremental_index(self.proj)
        self.assertEqual(r["renamed"], [("util.py", "util_renamed.py")])
        fn_hits = [h for h in graph_find_symbol(self.proj, "helper")["results"]
                   if h["kind"] == "function"]
        self.assertTrue(fn_hits and fn_hits[0]["file_path"] == "util_renamed.py",
                        f"function must live under the new path: {fn_hits}")

    def test_detect_changes_hash_authoritative(self):
        # same-size same-mtime-ish edit still detected via sha256
        p = self.tempdir / "util.py"
        p.write_text("def helper():\n    return 43\n", encoding="utf-8")
        ch = detect_changes(self.proj)
        self.assertEqual(ch["modified"], ["util.py"])
        self.assertEqual(compute_file_hash(str(p)),
                         compute_file_hash(str(p)), "hash must be deterministic")

    def test_incremental_fallback_on_failure(self):
        r = incremental_index(self.proj, scope="full",
                              project_root=self.proj + "\x00bad")
        self.assertTrue(r["fallback"])
        self.assertIn("fallback_reason", r)
        self.assertEqual(_orphans(self.proj), 0)

    def test_dependency_edges_cross_file(self):
        conn = store.get_db(self.proj, readonly=True)
        try:
            dep = conn.execute(
                "SELECT COUNT(*) FROM edges WHERE kind='imports_module';").fetchone()[0]
        finally:
            conn.close()
        self.assertGreaterEqual(dep, 1, "core.py imports util -> imports_module edge")

    # ── Step 15 ──
    def test_template_callers_callees(self):
        callers = template_query(self.proj, "callers", name="helper")
        self.assertIsNone(callers["error"])
        self.assertTrue(any(r["name"] == "main" for r in callers["results"]))
        callees = template_query(self.proj, "callees", name="main")
        self.assertIsNone(callees["error"])
        self.assertGreaterEqual(callees["count"], 2)

    def test_template_imports_imported_by_module_deps(self):
        imports = template_query(self.proj, "imports", module="core")
        self.assertTrue(any(r["qualified_name"] == "util.helper"
                            or r["name"] == "helper" for r in imports["results"]))
        imported_by = template_query(self.proj, "imported_by", module="util")
        self.assertTrue(any(r["file_path"] == "core.py" for r in imported_by["results"]))
        deps = template_query(self.proj, "module_deps")
        self.assertTrue(any(r["file"] == "core.py" and r["depends_on"] == "util"
                            for r in deps["results"]))

    def test_template_orphans_entry_points_impact(self):
        orphans = template_query(self.proj, "orphan_symbols")
        names = {r["name"] for r in orphans["results"]}
        self.assertIn("never_called_public", names)
        self.assertNotIn("handle_request", names, "entry heuristics must exclude")
        eps = template_query(self.proj, "entry_points")
        ep_names = {r["name"] for r in eps["results"]}
        self.assertIn("main", ep_names)
        self.assertIn("handle_request", ep_names)
        impact = template_query(self.proj, "impact_scope", name="helper", depth=3)
        self.assertIsNone(impact["error"])
        self.assertIn("core.py", impact["results"][0]["affected_files"])

    def test_template_unsupported_clear_error(self):
        r = template_query(self.proj, "does_not_exist")
        self.assertIsNotNone(r["error"])
        self.assertIn("unsupported_template", r["error"])
        for t in TEMPLATES:
            self.assertIn(t, r["error"])

    # ── Step 16 ──
    def test_architecture_cites_files_and_bounds(self):
        arch = cbm_get_architecture(self.proj)
        self.assertIsNone(arch["error"])
        files = {m["file"] for m in arch["modules"]}
        self.assertEqual(files, {"core.py", "util.py"})
        self.assertTrue(any(m["module"] == "core" and "util" in m["depends_on"]
                            for m in arch["modules"]))
        self.assertFalse(arch["truncated"])

    def test_architecture_early_exit_metadata(self):
        arch = cbm_get_architecture(self.proj, max_files=1)
        self.assertTrue(arch["truncated"])
        self.assertEqual(arch["truncation_reason"], "max_files")
        self.assertEqual(arch["files_seen"], 1)
        arch2 = cbm_get_architecture(self.proj, timeout_s=-1.0)
        self.assertTrue(arch2["truncated"])
        self.assertEqual(arch2["truncation_reason"], "timeout")

    # ── Step 17 ──
    def test_dead_code_conservative(self):
        dc = dead_code_candidates(self.proj)
        self.assertIsNone(dc["error"])
        by_name = {c["name"]: c for c in dc["candidates"]}
        self.assertIn("never_called_public", by_name)
        self.assertIn("_never_called_private", by_name)
        self.assertNotIn("handle_request", by_name, "entry point must be excluded")
        self.assertNotIn("main", by_name)
        self.assertLessEqual(by_name["never_called_public"]["confidence"], 0.3,
                             "public candidate must stay low confidence")
        self.assertEqual(by_name["_never_called_private"]["confidence"], 0.7)
        self.assertTrue(all(c.get("reason") for c in dc["candidates"]))

    # ── BUG-1 regression: impact_scope None-safe on leaf/empty edges ──
    def test_impact_scope_none_safe_on_leaf(self):
        # leaf node with 0 inbound call edges → tr["edges"] empty → files set
        # comprehension must not raise (was TypeError on None[0] before fix).
        impact = template_query(self.proj, "impact_scope", name="never_called_public",
                                depth=2)
        self.assertIsNone(impact["error"])
        self.assertEqual(impact["results"][0]["affected_files"], [])
        self.assertEqual(impact["results"][0]["edges"], 0)

    # ── BUG-2 regression: __all__ exports excluded from dead_code ──
    def test_dead_code_excludes_all_exports(self):
        # standalone fixture: api.py declares __all__ = ["public_api_func"];
        # the exported symbol has 0 inbound call edges but must NOT be flagged
        # as dead code because is_exported=1 (extractor captures __all__).
        td = Path(tempfile.mkdtemp(prefix="p1_bug2_"))
        try:
            (td / "api.py").write_text(
                '__all__ = ["public_api_func"]\n\n\n'
                'def public_api_func():\n    return 1\n',
                encoding="utf-8")
            proj = str(td).replace("\\", "/")
            index_project(proj, scope="full")
            dc = dead_code_candidates(proj)
            self.assertIsNone(dc["error"])
            names = {c["name"] for c in dc["candidates"]}
            self.assertNotIn("public_api_func", names,
                             "__all__ export must be excluded from dead_code")
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
