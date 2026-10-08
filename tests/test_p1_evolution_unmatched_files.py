"""P1: cbm_evolution must not silently drop bad included_files.

An included_files entry that matches nothing in the walked git history is a
caller error, not an empty result: the view goes partial and names the file
in unmatched_files instead of returning ok + empty churn.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.evolution import compute_evolution, hash_author


def _rec(email: str, files: list[str]) -> dict:
    return {
        "hash": "x",
        "date": "2026-09-01T00:00:00Z",
        "email": email,
        "author_id": hash_author(email),
        "files": list(files),
        "rename_in_commit": False,
    }


_RECORDS = [
    _rec("a@x.com", ["src/real.py", "src/other.py"]),
    _rec("a@x.com", ["src/real.py"]),
]


class TestP1UnmatchedIncludedFiles:
    def test_bad_included_file_is_partial_and_named(self):
        out = compute_evolution("", records=_RECORDS, known_files=["khong/tontai_xyz.py"])
        assert out["status"] == "partial"
        assert out["reason"] == "unmatched_included_files"
        assert out["unmatched_files"] == ["khong/tontai_xyz.py"]
        assert out["churn"] == []
        assert "unmatched_included_files_present" in out["metric_notes"]
        assert out["pass"] is True

    def test_good_included_file_stays_ok(self):
        out = compute_evolution("", records=_RECORDS, known_files=["src/real.py"])
        assert out["status"] == "ok"
        assert out["reason"] is None
        assert out["unmatched_files"] == []
        assert len(out["churn"]) == 1
        assert out["churn"][0]["file"] == "src/real.py"

    def test_mixed_names_only_the_bad_file(self):
        out = compute_evolution(
            "", records=_RECORDS, known_files=["src/real.py", "khong/tontai_xyz.py"]
        )
        assert out["status"] == "partial"
        assert out["reason"] == "unmatched_included_files"
        assert out["unmatched_files"] == ["khong/tontai_xyz.py"]
        assert len(out["churn"]) == 1
        assert out["churn"][0]["file"] == "src/real.py"

    def test_no_included_files_no_unmatched_key_pollution(self):
        out = compute_evolution("", records=_RECORDS)
        assert out["status"] == "ok"
        assert out["unmatched_files"] == []
