"""AC-P6-S2: chain3 3/6=.5; disconnected 0; 501 sampled; 2001 truncated; UTF8 labels."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.dsm import (
    EXACT_PROPAGATION,
    MAX_NODES,
    MAX_PAGE,
    build_dsm,
    compute_levels,
)


class TestChainPropagation:
    def test_chain3_is_half(self):
        out = build_dsm(["a.py", "b.py", "c.py"], [("a.py", "b.py"), ("b.py", "c.py")])
        assert out["status"] == "ok"
        assert out["propagation_mode"] == "exact"
        assert out["propagation_cost"] == 0.5
        assert out["size"] == 3
        assert out["edge_count"] == 2
        # sinks first: c, b, a
        assert out["files"][0] == "c.py"
        assert out["levels"]["c.py"] == 0
        assert out["levels"]["b.py"] == 1
        assert out["levels"]["a.py"] == 2
        assert out["below_diagonal"] == 2
        assert out["above_diagonal"] == 0


class TestDisconnected:
    def test_disconnected_propagation_zero(self):
        out = build_dsm(["a.py", "b.py"], [])
        assert out["propagation_cost"] == 0.0
        assert out["edge_count"] == 0
        assert out["clusters"] == []


class TestSampled501:
    def test_501_is_sampled_not_exact(self):
        n = EXACT_PROPAGATION + 1
        files = [f"f{i:04d}.py" for i in range(n)]
        edges = [(files[i], files[i + 1]) for i in range(n - 1)]
        out = build_dsm(files, edges)
        assert out["size"] == n
        assert out["sampled"] is True
        assert out["propagation_mode"] == "sampled"
        assert out["status"] == "partial"
        assert out["reason"] == "sampled"
        assert "propagation_sampled" in out["metric_notes"]
        assert out["page"]["rows"] <= MAX_PAGE
        assert out["page"]["cols"] <= MAX_PAGE
        # sparse: never a dense 501x501
        assert len(out["page"]["cells"]) <= MAX_PAGE * MAX_PAGE
        assert "matrix" not in out


class TestTruncate2001:
    def test_2001_truncated_keeps_extremes(self):
        n = MAX_NODES + 1
        files = [f"n{i:04d}.py" for i in range(n)]
        edges = [(files[i], files[i + 1]) for i in range(n - 1)]
        out = build_dsm(files, edges)
        assert out["truncated"] is True
        assert out["size"] == MAX_NODES
        assert out["total_files"] == n
        assert out["status"] == "partial"
        assert out["reason"] == "truncated"
        # lowest-level sink + a highest-level source survive
        assert files[-1] in out["files"]
        assert files[0] in out["files"]
        assert files[MAX_NODES // 2] not in out["files"]


class TestUtf8Labels:
    def test_unicode_labels_intact(self):
        out = build_dsm(["café.py", "b.py"], [("café.py", "b.py")])
        assert "café.py" in out["files"]
        assert out["levels"]["café.py"] == 1
        assert any(c["src"] == "café.py" for c in out["page"]["cells"])


class TestMutualClusters:
    def test_one_way_is_not_a_cluster(self):
        out = build_dsm(["a.py", "b.py"], [("a.py", "b.py")])
        assert out["clusters"] == []

    def test_mutual_edge_is_cluster(self):
        out = build_dsm(["a.py", "b.py", "c.py"], [("a.py", "b.py"), ("b.py", "a.py")])
        assert len(out["clusters"]) == 1
        assert set(out["clusters"][0]["files"]) == {"a.py", "b.py"}
        assert out["clusters"][0]["internal_edges"] == 2


class TestPageCap:
    def test_page_never_exceeds_100(self):
        files = [f"p{i:03d}.py" for i in range(150)]
        edges = [(files[0], files[i]) for i in range(1, 150)]
        out = build_dsm(files, edges, page_rows=1000, page_cols=1000)
        assert out["page"]["rows"] <= MAX_PAGE
        assert out["page"]["cols"] <= MAX_PAGE
        assert len(out["page"]["cells"]) <= MAX_PAGE * MAX_PAGE


class TestLevelHelper:
    def test_compute_levels_chain(self):
        lv = compute_levels(["a.py", "b.py", "c.py"], [("a.py", "b.py"), ("b.py", "c.py")])
        assert lv["c.py"] == 0
        assert lv["b.py"] == 1
        assert lv["a.py"] == 2
