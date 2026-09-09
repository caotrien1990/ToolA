"""AC-P4-S2: identical / .02 pin / mismatch / missing / bytes unchanged."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.baseline import compare_baseline, load_baseline


def _snap(**metrics):
    return {
        "provenance": {
            "root": "D:/proj",
            "included_set_hash": "abc",
            "algorithm_version": "analysis-view-v1",
            "extractor_version": "ext-1",
            "schema_version": 8,
            "confidence_policy": {"strict_min": 0.9, "observed_min": 0.3},
        },
        "metrics": {
            "analysis_quality_v1": metrics.get("q", 0.80),
            "coupling_score": metrics.get("c", 0.20),
            "cycles": metrics.get("k", 1),
        },
    }


class TestIdentical:
    def test_identical_no_regression(self):
        snap = _snap()
        out = compare_baseline(snap, copy.deepcopy(snap))
        assert out["pass"] is True
        assert out["identical"] is True
        assert out["regression"] is False
        assert out["degraded"] is False
        assert out["identity_match"] is True


class TestBoundary:
    def test_quality_drop_exactly_point_zero_two_not_degraded(self):
        cur = _snap(q=0.78)
        base = _snap(q=0.80)
        out = compare_baseline(cur, base)
        row = next(c for c in out["checks"] if c["metric"] == "analysis_quality_v1")
        assert abs(row["delta"] + 0.02) < 1e-12
        assert out["degraded"] is False
        assert out["pass"] is True

    def test_quality_drop_just_over_point_zero_two_degraded(self):
        cur = _snap(q=0.779)
        base = _snap(q=0.80)
        out = compare_baseline(cur, base)
        assert out["degraded"] is True
        assert out["regression"] is True
        assert out["pass"] is False

    def test_coupling_rise_over_and_cycles_plus_one(self):
        coup = compare_baseline(_snap(c=0.221), _snap(c=0.20))
        assert coup["degraded"] is True
        cyc = compare_baseline(_snap(k=2), _snap(k=1))
        assert cyc["degraded"] is True
        same_cyc = compare_baseline(_snap(k=1), _snap(k=1))
        assert same_cyc["degraded"] is False


class TestMismatchMalformedMissing:
    def test_root_mismatch_unavailable(self):
        cur = _snap()
        base = _snap()
        base["provenance"]["root"] = "D:/other"
        out = compare_baseline(cur, base)
        assert out["status"] == "unavailable"
        assert out["reason"] == "identity_mismatch"
        assert out["pass"] is False
        assert "root" in out["mismatched_fields"]

    def test_malformed_baseline_unavailable(self):
        out = compare_baseline(_snap(), "{not json")
        assert out["reason"] == "malformed_baseline"
        assert out["pass"] is False

    def test_missing_baseline_unavailable(self):
        out = compare_baseline(_snap(), None)
        assert out["reason"] == "missing_baseline"
        assert out["pass"] is False

    def test_missing_compared_value_unavailable_not_better(self):
        cur = _snap()
        base = _snap()
        del base["metrics"]["analysis_quality_v1"]
        out = compare_baseline(cur, base)
        assert out["reason"] == "unavailable"
        row = next(c for c in out["checks"] if c["metric"] == "analysis_quality_v1")
        assert row["status"] == "unavailable"
        assert out["pass"] is False
        assert out["degraded"] is False


class TestBytesUnchanged:
    def test_baseline_bytes_not_mutated(self):
        raw = json.dumps(_snap(), separators=(",", ":"), ensure_ascii=False)
        before = raw
        out = compare_baseline(_snap(q=0.5), raw)
        assert raw == before
        assert out["baseline_bytes_unchanged"] is True
        loaded = load_baseline(raw)
        assert loaded["status"] == "ok"
