"""AC-P4-S1: 13 keys, unknown invalid, missing CC unavailable, threshold/layer/glob."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.rules import (
    CONSTRAINT_KEYS,
    evaluate_rules,
    parse_rules_toml,
    recognized_keys,
)


def _view(files, edges=None, **extra):
    payload = {
        "status": "ok",
        "freshness": "fresh",
        "included_files": list(files),
        "test_files": extra.pop("test_files", []),
        "observed_edges": [
            {"src": s, "dst": d, "confidence": 1.0} for s, d in (edges or [])
        ],
        "strict_edges": [
            {"src": s, "dst": d, "confidence": 1.0} for s, d in (edges or [])
        ],
        "isolated_files": [],
        "truncated": False,
        "provenance": {"root": "", "included_set_hash": "fix", "algorithm_version": "analysis-view-v1"},
    }
    payload.update(extra)
    return payload


def _factors(mod=0.8, acy=1.0, depth=0.8, eq=0.8, red=0.8):
    return {
        "quality_factors": {
            "modularity": mod,
            "acyclicity": acy,
            "depth": depth,
            "equality": eq,
            "redundancy": red,
        }
    }


THIRTEEN = """
[constraints]
min_quality = 0.5
min_modularity = 0.1
min_acyclicity = 0.0
min_depth = 0.0
min_equality = 0.0
min_redundancy = 0.0
max_coupling_score = 1.0
max_cycles = 10
max_cc = 20
max_file_lines = 500
max_fn_lines = 80
no_god_files = true
max_upward_violations = 0
"""


class TestKeysRecognized:
    def test_thirteen_keys_recognized(self):
        assert recognized_keys() == CONSTRAINT_KEYS
        assert len(CONSTRAINT_KEYS) == 13
        parsed = parse_rules_toml(THIRTEEN)
        assert parsed["status"] == "ok"
        assert parsed["present_keys"] == sorted(CONSTRAINT_KEYS)
        assert set(parsed["constraints"]) == set(CONSTRAINT_KEYS)


class TestUnknownKeyInvalid:
    def test_unknown_key_invalid_whole_request(self):
        parsed = parse_rules_toml("[constraints]\nmax_foo = 1\n")
        assert parsed["reason"] == "invalid_config"
        assert parsed["pass"] is False
        assert "max_foo" in parsed["metric_notes"]
        out = evaluate_rules(_view(["a.py"]), "[constraints]\nmax_foo = 1\n")
        assert out["reason"] == "invalid_config"
        assert out["counts"]["evaluated"] == 0


class TestMaxCcUnavailable:
    def test_missing_cc_unavailable_never_pass(self):
        out = evaluate_rules(_view(["a.py"]), "[constraints]\nmax_cc = 10\n")
        assert out["pass"] is False
        assert out["aggregate"] == "unavailable"
        row = out["checks"][0]
        assert row["key"] == "max_cc"
        assert row["status"] == "unavailable"
        assert row["reason"] == "missing_cc"
        assert row["pass"] is False


class TestNoGodUnavailable:
    def test_god_files_always_unavailable_v1(self):
        out = evaluate_rules(_view(["a.py"]), "[constraints]\nno_god_files = true\n")
        assert out["pass"] is False
        assert out["checks"][0]["status"] == "unavailable"
        assert out["checks"][0]["reason"] == "god_detector_undefined"


class TestQualityThreshold:
    def test_min_quality_pass_and_violate(self):
        view = _view(["a.py"])
        ok = evaluate_rules(view, "[constraints]\nmin_quality = 0.8\n", metrics={
            **_factors(),
            "analysis_quality_v1": 0.9,
        })
        assert ok["aggregate"] == "passed"
        assert ok["pass"] is True
        assert ok["checks"][0]["actual"] == 0.9
        bad = evaluate_rules(view, "[constraints]\nmin_quality = 0.8\n", metrics={
            **_factors(),
            "analysis_quality_v1": 0.7,
        })
        assert bad["aggregate"] == "violated"
        assert bad["pass"] is False

    def test_min_quality_70_invalid_no_autoconvert(self):
        parsed = parse_rules_toml("[constraints]\nmin_quality = 70\n")
        assert parsed["reason"] == "invalid_config"
        assert "threshold_out_of_unit" in parsed["metric_notes"]

    def test_missing_factors_unavailable(self):
        out = evaluate_rules(_view(["a.py"]), "[constraints]\nmin_quality = 0.5\n", metrics={
            "analysis_quality_v1": 0.9,
        })
        assert out["checks"][0]["status"] == "unavailable"
        assert out["pass"] is False


class TestLayerFixtures:
    def test_low_depends_on_high_is_upward(self):
        toml = """
[constraints]
max_upward_violations = 0

[[layers]]
name = "core"
order = 1
globs = ["core/**"]

[[layers]]
name = "app"
order = 2
globs = ["app/**"]
"""
        view = _view(["core/a.py", "app/b.py"], [("core/a.py", "app/b.py")])
        out = evaluate_rules(view, toml)
        assert out["aggregate"] == "violated"
        assert out["checks"][0]["actual"] == 1

    def test_same_layer_and_high_to_low_allowed(self):
        toml = """
[constraints]
max_upward_violations = 0

[[layers]]
name = "core"
order = 1
globs = ["core/**"]

[[layers]]
name = "app"
order = 2
globs = ["app/**"]
"""
        same = evaluate_rules(_view(["core/a.py", "core/c.py"], [("core/a.py", "core/c.py")]), toml)
        assert same["aggregate"] == "passed"
        down = evaluate_rules(_view(["core/a.py", "app/b.py"], [("app/b.py", "core/a.py")]), toml)
        assert down["aggregate"] == "passed"

    def test_ambiguous_layer_is_config_error(self):
        toml = """
[constraints]
max_upward_violations = 0

[[layers]]
name = "core"
order = 1
globs = ["**"]

[[layers]]
name = "app"
order = 2
globs = ["app/**"]
"""
        out = evaluate_rules(_view(["app/b.py"]), toml)
        assert out["reason"] == "invalid_config"
        assert "ambiguous_layer_match" in out["metric_notes"]


class TestBoundaryGlob:
    def test_deny_pair_matches_and_misses(self):
        toml = """
[[boundaries]]
from = "app/**"
to = "infra/**"
reason = "app_may_not_import_infra"
"""
        hit = evaluate_rules(
            _view(["app/a.py", "infra/b.py"], [("app/a.py", "infra/b.py")]),
            toml,
        )
        assert hit["aggregate"] == "violated"
        assert hit["checks"][0]["reason"] == "app_may_not_import_infra"
        miss = evaluate_rules(
            _view(["app/a.py", "core/c.py"], [("app/a.py", "core/c.py")]),
            toml,
        )
        assert miss["aggregate"] == "passed"


class TestFileLinesAndCycles:
    def test_file_line_threshold(self):
        view = _view(["a.py", "b.py"])
        ok = evaluate_rules(view, "[constraints]\nmax_file_lines = 10\n", metrics={
            "file_line_counts": {"a.py": 4, "b.py": 10},
        })
        assert ok["aggregate"] == "passed"
        bad = evaluate_rules(view, "[constraints]\nmax_file_lines = 10\n", metrics={
            "file_line_counts": {"a.py": 4, "b.py": 11},
        })
        assert bad["aggregate"] == "violated"
        assert "b.py" in bad["checks"][0]["files"]
        missing = evaluate_rules(view, "[constraints]\nmax_file_lines = 10\n", metrics={
            "file_line_counts": {"a.py": 4},
        })
        assert missing["checks"][0]["status"] == "unavailable"

    def test_cycle_count_from_view(self):
        cyclic = _view(["a.py", "b.py"], [("a.py", "b.py"), ("b.py", "a.py")])
        out = evaluate_rules(cyclic, "[constraints]\nmax_cycles = 0\n")
        assert out["cycles"] == 1
        assert out["aggregate"] == "violated"
        acyclic = evaluate_rules(_view(["a.py", "b.py"], [("a.py", "b.py")]), "[constraints]\nmax_cycles = 0\n")
        assert acyclic["cycles"] == 0
        assert acyclic["aggregate"] == "passed"


class TestAggregatePriority:
    def test_violation_outranks_unavailable_and_counts_stay(self):
        out = evaluate_rules(
            _view(["a.py"]),
            "[constraints]\nmax_cc = 10\nmax_cycles = 0\nmin_quality = 0.9\n",
            metrics={**_factors(), "analysis_quality_v1": 0.1},
        )
        assert out["aggregate"] == "violated"
        assert out["counts"]["violated"] == 1
        assert out["counts"]["unavailable"] == 1
        assert out["counts"]["passed"] == 1
        assert out["counts"]["evaluated"] == 3
        assert out["pass"] is False
