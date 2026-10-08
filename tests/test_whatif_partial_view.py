"""P5: cbm_what_if must forward a partial analysis view, never hardcode status ok.

Regression: simulate_sequence used to return status:"ok"/pass:True even when the
input analysis view was partial (unmatched included_files), silently swallowing
the partial view. Now it forwards status:partial + unmatched_files.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.whatif import simulate_sequence


def _view(status: str, unmatched: list) -> dict:
    return {
        "status": status,
        "unmatched_files": list(unmatched),
        "included_files": ["a.py", "b.py"],
        "observed_edges": [],
        "strict_edges": [],
        "provenance": {"root": "r", "included_set_hash": "h"},
    }


def test_partial_view_yields_partial_status_not_ok():
    out = simulate_sequence(_view("partial", ["nope/missing.py"]), [])
    assert out["status"] == "partial"
    assert out["pass"] is False
    assert out["reason"] == "unmatched_included_files"
    assert out["unmatched_files"] == ["nope/missing.py"]
    assert "view_partial" in out["metric_notes"]
    # simulation still ran on the matched files
    assert out["applied"] is True


def test_ok_view_keeps_previous_behavior():
    out = simulate_sequence(_view("ok", []), [])
    assert out["status"] == "ok"
    assert out["pass"] is True
    assert out["reason"] is None
    assert out["unmatched_files"] == []
    assert "view_partial" not in out["metric_notes"]
