"""cbm_cycle_point contract tests — cycles, levels, and the quality signal.

Invariant under test (WHY): the tool grades architecture, so a wrong grade is
worse than no grade. Each test pins one claim the payload makes:

  - A real import cycle is reported as a cycle, and an acyclic chain is not.
  - Levels respect dependency direction (a dependency ranks below its dependent).
  - The geometric mean actually punishes a collapsed dimension, which is the
    whole reason the aggregate is a product and not an average.
  - Deep chains do not overflow the stack — the algorithms are iterative and a
    500-file chain is a realistic shape.
  - GRAPH_ENABLED=0 returns a payload, never an exception.

Run: ./venv312/Scripts/python.exe -m unittest tests/test_cbm_cycle_point.py -v
"""
import os
os.environ["GRAPH_ENABLED"] = "1"  # must precede orchestrator.graph.config import

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.indexer import index_project
from orchestrator.graph import cycle_point as cp

# a <-> b is a genuine two-file import cycle; c depends on b but nothing
# depends on c, so c is a root and b sits below it.
FIX_A = '''import b


def alpha():
    return b.beta()
'''

FIX_B = '''import a


def beta():
    return 2
'''

FIX_C = '''import b


def gamma():
    return b.beta()
'''


class TestPureAlgorithms(unittest.TestCase):
    """Graph algorithms on hand-built adjacency — no DB, no indexer."""

    def test_two_node_cycle_is_detected(self):
        nodes = {"a.py", "b.py"}
        adj = {"a.py": ["b.py"], "b.py": ["a.py"]}
        cycles = cp.tarjan_scc(nodes, adj)
        self.assertEqual(cycles, [["a.py", "b.py"]])

    def test_acyclic_chain_reports_no_cycle(self):
        nodes = {"a.py", "b.py", "c.py"}
        adj = {"a.py": ["b.py"], "b.py": ["c.py"]}
        self.assertEqual(cp.tarjan_scc(nodes, adj), [])

    def test_self_loop_is_not_a_cycle(self):
        # A single-member SCC is not a dependency cycle worth reporting; the
        # file-graph builder drops self-edges, and Tarjan must agree.
        nodes = {"a.py"}
        adj = {"a.py": ["a.py"]}
        self.assertEqual(cp.tarjan_scc(nodes, adj), [])

    def test_levels_rank_dependency_below_dependent(self):
        # a imports b imports c: c is a leaf (level 0), a is highest.
        nodes = {"a.py", "b.py", "c.py"}
        adj = {"a.py": ["b.py"], "b.py": ["c.py"]}
        levels, max_level = cp.kahn_levels(nodes, adj, [])
        self.assertEqual(levels["c.py"], 0)
        self.assertLess(levels["c.py"], levels["b.py"])
        self.assertLess(levels["b.py"], levels["a.py"])
        self.assertEqual(max_level, 2)

    def test_cycle_members_share_one_level(self):
        nodes = {"a.py", "b.py", "c.py"}
        adj = {"a.py": ["b.py"], "b.py": ["a.py"], "c.py": ["a.py"]}
        cycles = cp.tarjan_scc(nodes, adj)
        levels, _ = cp.kahn_levels(nodes, adj, cycles)
        self.assertEqual(levels["a.py"], levels["b.py"])

    def test_longest_path_counts_chain_length(self):
        nodes = {"a.py", "b.py", "c.py", "d.py"}
        adj = {"a.py": ["b.py"], "b.py": ["c.py"], "c.py": ["d.py"]}
        self.assertEqual(cp.longest_path_depth(nodes, adj), 3)

    def test_deep_chain_does_not_overflow(self):
        # 500 files deep: recursion would exceed Python's default limit, so this
        # asserts the iterative implementation, not just the number.
        n = 500
        names = [f"f{i}.py" for i in range(n)]
        nodes = set(names)
        adj = {names[i]: [names[i + 1]] for i in range(n - 1)}
        self.assertEqual(cp.longest_path_depth(nodes, adj), n - 1)
        self.assertEqual(cp.tarjan_scc(nodes, adj), [])

    def test_upward_violation_counts_only_wrong_direction(self):
        adj = {"a.py": ["b.py"]}
        self.assertEqual(cp.upward_violations(adj, {"a.py": 1, "b.py": 0}), 0)
        self.assertEqual(cp.upward_violations(adj, {"a.py": 0, "b.py": 1}), 1)

    def test_gini_spans_equal_to_concentrated(self):
        self.assertAlmostEqual(cp.gini([10.0, 10.0, 10.0, 10.0]), 0.0, places=6)
        self.assertGreater(cp.gini([0.0, 0.0, 0.0, 100.0]), 0.6)

    def test_modularity_of_edgeless_graph_is_one(self):
        self.assertEqual(cp.modularity_q({}, set()), 1.0)

    def test_modularity_prefers_intra_module_edges(self):
        # Same directory on both sides of every edge = maximally clustered.
        clustered = cp.modularity_q(
            {"pkg/a.py": ["pkg/b.py"], "pkg/b.py": ["pkg/c.py"]},
            {"pkg/a.py", "pkg/b.py", "pkg/c.py"},
        )
        scattered = cp.modularity_q(
            {"one/a.py": ["two/b.py"], "two/b.py": ["three/c.py"]},
            {"one/a.py", "two/b.py", "three/c.py"},
        )
        self.assertGreater(clustered, scattered)

    def test_geometric_mean_punishes_one_collapsed_dimension(self):
        healthy, healthy_signal = cp.root_cause_scores(
            modularity_raw=0.5, cycle_count=0, max_depth=4,
            complexity_gini=0.2, redundancy_ratio=0.1)
        self.assertEqual(healthy["acyclicity"], 1.0)
        self.assertGreater(healthy_signal, 0.6)

        # Same codebase, but 40 cycles: acyclicity collapses and the aggregate
        # must follow it down — an average would hide this.
        _, cyclic_signal = cp.root_cause_scores(
            modularity_raw=0.5, cycle_count=40, max_depth=4,
            complexity_gini=0.2, redundancy_ratio=0.1)
        self.assertLess(cyclic_signal, healthy_signal / 2)

    def test_scores_stay_in_unit_range(self):
        scores, signal = cp.root_cause_scores(
            modularity_raw=-0.5, cycle_count=999, max_depth=999,
            complexity_gini=1.0, redundancy_ratio=1.0)
        for name, value in scores.items():
            self.assertGreaterEqual(value, 0.0, name)
            self.assertLessEqual(value, 1.0, name)
        self.assertGreaterEqual(signal, 0.0)
        self.assertLessEqual(signal, 1.0)


class TestCyclePointOnIndexedProject(unittest.TestCase):
    """End-to-end against a real indexed project with a planted cycle."""

    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="cycle_point_"))
        (self.tempdir / "a.py").write_text(FIX_A, encoding="utf-8")
        (self.tempdir / "b.py").write_text(FIX_B, encoding="utf-8")
        (self.tempdir / "c.py").write_text(FIX_C, encoding="utf-8")
        self.proj = str(self.tempdir).replace("\\", "/")
        index_project(self.proj, scope="full")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_payload_contract(self):
        out = cp.compute_cycle_point(self.proj)
        self.assertTrue(out["enabled"])
        self.assertIsNone(out["error"])
        for key in ("cycles", "cycle_count", "levels", "max_level", "max_depth",
                    "upward_violations", "quality_signal", "root_causes", "raw",
                    "metric_notes", "files_analyzed", "edges_analyzed", "elapsed_ms"):
            self.assertIn(key, out)

    def test_planted_cycle_is_found(self):
        out = cp.compute_cycle_point(self.proj)
        self.assertGreaterEqual(out["cycle_count"], 1)
        members = {Path(f).name for cycle in out["cycles"] for f in cycle}
        self.assertIn("a.py", members)
        self.assertIn("b.py", members)

    def test_quality_signal_is_a_unit_score(self):
        out = cp.compute_cycle_point(self.proj)
        self.assertIsNotNone(out["quality_signal"])
        self.assertGreaterEqual(out["quality_signal"], 0.0)
        self.assertLessEqual(out["quality_signal"], 1.0)
        self.assertEqual(
            set(out["root_causes"]),
            {"modularity", "acyclicity", "depth", "equality", "redundancy"},
        )

    def test_proxy_metrics_are_declared(self):
        # The score is not a sentrux score; the payload has to say so.
        notes = cp.compute_cycle_point(self.proj)["metric_notes"]
        self.assertIn("equality", notes)
        self.assertIn("redundancy", notes)

    def test_read_only_leaves_the_tree_untouched(self):
        before = sorted(p.name for p in self.tempdir.iterdir())
        cp.compute_cycle_point(self.proj)
        self.assertEqual(before, sorted(p.name for p in self.tempdir.iterdir()))


class TestFailOpen(unittest.TestCase):
    """A disabled graph or a missing DB reports the reason instead of raising."""

    def test_disabled_graph_returns_payload(self):
        original = cp.GRAPH_ENABLED
        cp.GRAPH_ENABLED = False
        try:
            out = cp.compute_cycle_point("D:/does/not/matter")
            self.assertFalse(out["enabled"])
            self.assertIsNone(out["quality_signal"])
            self.assertEqual(out["cycle_count"], 0)
        finally:
            cp.GRAPH_ENABLED = original

    def test_missing_db_reports_error_without_raising(self):
        empty = Path(tempfile.mkdtemp(prefix="cycle_point_empty_"))
        try:
            out = cp.compute_cycle_point(str(empty).replace("\\", "/"))
            self.assertIsNotNone(out["error"])
            self.assertIsNone(out["quality_signal"])
        finally:
            shutil.rmtree(empty, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
