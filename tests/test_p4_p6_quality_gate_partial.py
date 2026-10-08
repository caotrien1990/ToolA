"""P4: cbm_quality_gate must keep a partial view partial (never map to ok).
P6: cbm_index must reject a nonexistent project_path upfront (F-01).

Regression: _snapshot_from_view mapped view "partial" -> "ok" (silent clean
gate on a bad path); evaluate_rules collapsed partial to "ok" via its
aggregate-only status expression; the compare branch inherited both.
cbm_index had no isdir guard, creating junk .orchestrator_cache/ on bad paths.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.rules import evaluate_rules
from orchestrator.graph.tools import _snapshot_from_view, cbm_index


def _view(status="ok", unmatched=(), reason=None):
    return {
        "status": status,
        "reason": reason,
        "unmatched_files": list(unmatched),
        "freshness": "fresh",
        "included_files": ["a.py", "b.py"],
        "test_files": [],
        "observed_edges": [],
        "strict_edges": [],
        "isolated_files": [],
        "truncated": False,
        "provenance": {
            "root": "",
            "included_set_hash": "fix",
            "algorithm_version": "analysis-view-v1",
        },
    }


EMPTY_TOML = "[constraints]\n"


def test_p4_snapshot_keeps_partial_and_forwards_unmatched():
    out = _snapshot_from_view(
        _view("partial", ["nope/missing.py"], "unmatched_included_files"), {}
    )
    assert out["status"] == "partial"
    assert out["reason"] == "unmatched_included_files"
    assert out["unmatched_files"] == ["nope/missing.py"]


def test_p4_snapshot_ok_view_unchanged():
    out = _snapshot_from_view(_view("ok", []), {})
    assert out["status"] == "ok"
    assert out["unmatched_files"] == []


def test_p4_rules_partial_view_stays_partial():
    out = evaluate_rules(
        _view("partial", ["nope/missing.py"], "unmatched_included_files"),
        EMPTY_TOML,
    )
    assert out["status"] == "partial"
    assert out["pass"] is False
    assert out["reason"] == "unmatched_included_files"
    assert out["unmatched_files"] == ["nope/missing.py"]
    assert "view_partial" in out["metric_notes"]


def test_p4_rules_ok_view_unchanged():
    out = evaluate_rules(_view("ok", []), EMPTY_TOML)
    assert out["status"] == "ok"
    assert out["pass"] is True
    assert out["reason"] is None
    assert out["unmatched_files"] == []
    assert "view_partial" not in out["metric_notes"]


def test_p6_index_bad_path_fails_loud_and_creates_no_junk():
    bad = "D:/khong/tontai_p6_xyz"
    raw = asyncio.run(cbm_index(project_path=bad))
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error_name"] == "INVALID_PROJECT_PATH"
    assert not os.path.exists(os.path.join(bad, ".orchestrator_cache"))
    assert not os.path.exists(bad)
