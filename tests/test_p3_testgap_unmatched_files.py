"""P3: cbm_test_gap must forward unmatched_files from a partial analysis view.

Regression: compute_test_gap already mapped a partial view to output
status:"partial", but it dropped unmatched_files, so callers could not tell
which included_files failed to match. Now it forwards status:partial +
reason:unmatched_included_files + the unmatched_files list (same convention
as P5/whatif.py and A1/dsm.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.testgap import compute_test_gap


def _view(status: str, unmatched: list) -> dict:
    return {
        "status": status,
        "unmatched_files": list(unmatched),
        "included_files": ["src/a.py", "tests/test_a.py"],
        "test_files": ["tests/test_a.py"],
        "isolated_files": [],
        "observed_edges": [],
        "provenance": {"root": "r"},
    }


def test_partial_view_forwards_unmatched_files():
    out = compute_test_gap(_view("partial", ["nope/missing.py"]))
    assert out["status"] == "partial"
    assert out["reason"] == "unmatched_included_files"
    assert out["unmatched_files"] == ["nope/missing.py"]
    assert "view_partial" in out["metric_notes"]


def test_ok_view_keeps_previous_behavior():
    out = compute_test_gap(_view("ok", []))
    assert out["status"] == "ok"
    assert out["reason"] is None
    assert out["unmatched_files"] == []
    assert "view_partial" not in out["metric_notes"]


def test_no_tests_path_forwards_unmatched_files():
    v = _view("partial", ["nope/missing.py"])
    v["test_files"] = []
    out = compute_test_gap(v)
    assert out["status"] == "unavailable"
    assert out["reason"] == "no_tests"
    assert out["unmatched_files"] == ["nope/missing.py"]
