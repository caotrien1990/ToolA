"""P13: council_route_scorecard must not silently report 0 measurements for a
wrong project_path, and must not materialize `.council/route_scorecard.*` junk
under a bogus path.

Root cause: `load_measurements` returned [] for any missing file (hiding a bad
project_path as "zero measurements"), and `write_scorecard_files` ran
`mkdir(parents=True)` which created the whole `.council/` chain under the bogus
path before writing the scorecard files.

The fix (orchestrator/council/route_metrics.py): both functions now treat a
missing project root (<root>/.council/<file> layout) as a wrong path and raise
FileNotFoundError naming the root, instead of silent [] / silent mkdir.
A valid root with no measurements file yet still returns [] as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.council import route_metrics as rm


BAD_ROOT = Path("D:/khong/tontai_xyz_p13")


def test_load_measurements_bad_root_raises():
    with pytest.raises(FileNotFoundError, match="P13"):
        rm.load_measurements(BAD_ROOT / ".council" / "route_measurements.jsonl")


def test_load_measurements_valid_root_no_file_returns_empty(tmp_path):
    # Valid project root, no measurements file yet -> [] as before (no behavior change).
    assert rm.load_measurements(tmp_path / ".council" / "route_measurements.jsonl") == []


def test_write_scorecard_files_bad_root_refuses_and_creates_nothing():
    jp = BAD_ROOT / ".council" / "route_scorecard.json"
    mp = BAD_ROOT / ".council" / "route_scorecard.md"
    with pytest.raises(FileNotFoundError, match="P13"):
        rm.write_scorecard_files({"measurement_count": 0}, jp, mp)
    assert not BAD_ROOT.exists(), "junk dirs/files must not be created under a bogus path"


def test_write_scorecard_files_valid_root_writes(tmp_path):
    jp = tmp_path / ".council" / "route_scorecard.json"
    mp = tmp_path / ".council" / "route_scorecard.md"
    rm.write_scorecard_files({"measurement_count": 0, "buckets": []}, jp, mp)
    assert jp.is_file() and mp.is_file()
