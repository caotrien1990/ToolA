"""AC-P3-S3: registry/schema parity + labelled unavailable + legacy signature."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import code_tools
from orchestrator.graph.attack import compute_entry_reach
from orchestrator.graph.cycle_point import compute_cycle_point
from orchestrator.graph.testgap import compute_test_gap
from orchestrator.graph import tools as gtools


def _schema_props(name: str) -> set[str]:
    for row in code_tools.TOOL_SCHEMAS:
        fn = row.get("function") or {}
        if fn.get("name") == name:
            params = fn.get("parameters") or {}
            return set((params.get("properties") or {}))
    return set()


def _schema_row(name: str) -> dict:
    for row in code_tools.TOOL_SCHEMAS:
        fn = row.get("function") or {}
        if fn.get("name") == name:
            return fn
    return {}


class TestRegistryParityP3:
    def test_cbm_test_gap_registered_and_schema_matches_handler(self):
        assert "cbm_test_gap" in code_tools.TOOL_MAP
        assert code_tools.TOOL_PHASES.get("cbm_test_gap") == "T2"
        handler = code_tools.TOOL_MAP["cbm_test_gap"]
        sig = inspect.signature(handler)
        handler_params = {
            p.name
            for p in sig.parameters.values()
            if p.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
        }
        props = _schema_props("cbm_test_gap")
        assert props == handler_params
        row = _schema_row("cbm_test_gap")
        params = row.get("parameters") or {}
        arch = (params.get("properties") or {}).get("include_architecture") or {}
        assert arch.get("default") is False
        assert "include_extensions" not in props
        assert "project_path" in (params.get("required") or [])

    def test_legacy_cycle_point_schema_and_signature_unchanged(self):
        assert "cbm_cycle_point" in code_tools.TOOL_MAP
        props = _schema_props("cbm_cycle_point")
        assert props == {"project_path"}
        assert "include_extensions" not in props
        assert "included_files" not in props
        assert "include_architecture" not in props
        sig = inspect.signature(gtools.cbm_cycle_point)
        assert list(sig.parameters)[0] == "project_path"
        src = inspect.getsource(gtools.cbm_cycle_point)
        assert "include_extensions" not in src
        assert "included_files" not in src
        core_src = inspect.getsource(compute_cycle_point)
        assert "include_extensions" not in core_src.split("Campaign P3", 1)[0]


class TestLegacyDisabledMissing:
    def test_disabled_graph_keeps_legacy_shape(self, monkeypatch):
        monkeypatch.setenv("GRAPH_ENABLED", "0")
        payload = compute_cycle_point(str(Path(__file__).resolve().parents[1]))
        assert payload["enabled"] is False
        assert payload["quality_signal"] is None
        assert payload["error"] is None
        assert payload["cycles"] == []
        assert payload["files_analyzed"] == 0
        assert "include_extensions" not in payload
        assert "architecture" not in payload

    def test_missing_db_does_not_change_legacy_fields(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GRAPH_ENABLED", "1")
        monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / "no_db"))
        root = tmp_path / "proj"
        root.mkdir()
        payload = compute_cycle_point(str(root))
        assert payload["enabled"] is True
        assert payload["quality_signal"] is None
        assert payload["error"]
        assert payload["cycles"] == []
        assert "architecture" not in payload


class TestLabels:
    def test_no_tests_labelled_not_zero(self):
        view = {
            "status": "ok",
            "included_files": ["a.py", "c.py"],
            "test_files": [],
            "isolated_files": ["a.py", "c.py"],
            "observed_edges": [],
            "provenance": {"root": "mem"},
        }
        payload = compute_test_gap(view)
        assert payload["status"] == "unavailable"
        assert payload["reason"] == "no_tests"
        assert payload["coverage_ratio"] is None
        assert payload["pass"] is False

    def test_absent_entry_unknown(self):
        view = {
            "status": "ok",
            "included_files": ["a.py"],
            "observed_edges": [],
            "provenance": {"root": ""},
        }
        payload = compute_entry_reach(view, ["missing.py"])
        assert payload["status"] == "unavailable"
        assert payload["reason"] == "unknown"
        assert "absent_entry_unknown" in payload["metric_notes"]


class TestArchitectureOptIn:
    def test_default_omits_architecture_key(self):
        src = inspect.getsource(gtools.cbm_test_gap)
        assert "include_architecture" in src
        assert "want_arch" in src
        sig = inspect.signature(gtools.cbm_test_gap)
        assert sig.parameters["include_architecture"].default is False


class TestRegistryParityP4:
    def test_cbm_quality_gate_registered_and_schema_matches_handler(self):
        assert "cbm_quality_gate" in code_tools.TOOL_MAP
        assert code_tools.TOOL_PHASES.get("cbm_quality_gate") == "T1"
        handler = code_tools.TOOL_MAP["cbm_quality_gate"]
        sig = inspect.signature(handler)
        handler_params = {
            p.name
            for p in sig.parameters.values()
            if p.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
        }
        props = _schema_props("cbm_quality_gate")
        assert props == handler_params
        row = _schema_row("cbm_quality_gate")
        params = row.get("parameters") or {}
        mode = (params.get("properties") or {}).get("mode") or {}
        assert mode.get("default") == "rules"
        assert set(mode.get("enum") or []) == {"rules", "compare", "snapshot"}
        assert "include_extensions" not in props
        assert "project_path" in (params.get("required") or [])

    def test_legacy_cycle_point_still_project_path_only(self):
        props = _schema_props("cbm_cycle_point")
        assert props == {"project_path"}


class TestNoHiddenT1Write:
    def test_quality_gate_source_never_writes(self):
        src = inspect.getsource(gtools.cbm_quality_gate)
        snap_src = inspect.getsource(gtools._snapshot_from_view)
        assert "wrote" in src
        assert src.count("wrote") >= 2
        assert "write_new_file" not in src
        assert "write_fix" not in src
        assert "Path.write" not in src
        assert "snapshot_t1_no_write" in snap_src
        assert "overwrite=false" in snap_src.lower() or "overwrite=false" in src.lower() or "persist_hint" in snap_src


class TestCountsReconcile:
    def test_passed_violated_unavailable_sum_to_evaluated(self):
        from orchestrator.graph.rules import evaluate_rules

        view = {
            "status": "ok",
            "freshness": "fresh",
            "included_files": ["a.py"],
            "test_files": [],
            "observed_edges": [],
            "truncated": False,
            "provenance": {"root": "", "included_set_hash": "x"},
        }
        out = evaluate_rules(
            view,
            "[constraints]\nmax_cc = 10\nmax_cycles = 0\nmin_quality = 0.9\n",
            metrics={
                "analysis_quality_v1": 0.1,
                "quality_factors": {
                    "modularity": 0.8,
                    "acyclicity": 1.0,
                    "depth": 0.8,
                    "equality": 0.8,
                    "redundancy": 0.8,
                },
            },
        )
        counts = out["counts"]
        assert counts["passed"] + counts["violated"] + counts["unavailable"] == counts["evaluated"]
        assert counts["evaluated"] == len(out["checks"])
        assert counts["violated"] >= 1
        assert counts["unavailable"] >= 1


class TestRegistryParityP5:
    def test_cbm_what_if_registered_and_schema_matches_handler(self):
        assert "cbm_what_if" in code_tools.TOOL_MAP
        assert code_tools.TOOL_PHASES.get("cbm_what_if") == "T2"
        handler = code_tools.TOOL_MAP["cbm_what_if"]
        sig = inspect.signature(handler)
        handler_params = {
            p.name
            for p in sig.parameters.values()
            if p.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
        }
        props = _schema_props("cbm_what_if")
        assert props == handler_params
        row = _schema_row("cbm_what_if")
        params = row.get("parameters") or {}
        kind = (params.get("properties") or {}).get("edge_kind") or {}
        assert kind.get("default") == "observed"
        assert set(kind.get("enum") or []) == {"observed", "strict"}
        assert "include_extensions" not in props
        assert "project_path" in (params.get("required") or [])
        assert "ops" in props

    def test_legacy_cycle_point_still_project_path_only(self):
        props = _schema_props("cbm_cycle_point")
        assert props == {"project_path"}


class TestNoHiddenT1WriteP5:
    def test_what_if_source_never_writes(self):
        from orchestrator.graph import whatif as _whatif

        src = inspect.getsource(gtools.cbm_what_if)
        kernel = inspect.getsource(_whatif.simulate_sequence)
        assert "write_new_file" not in src
        assert "write_fix" not in src
        assert "Path.write" not in src
        assert "write_new_file" not in kernel
        assert "write_fix" not in kernel
        assert "Path.write" not in kernel
        assert "get_db" not in src
        assert "wrote" in src


class TestStructuredCapDeadline:
    def test_invalid_cap_deadline_structured_not_truncated(self):
        from orchestrator.graph.whatif import MAX_OPS, simulate_sequence

        view = {
            "status": "ok",
            "included_files": ["a.py"],
            "observed_edges": [],
            "strict_edges": [],
            "provenance": {"root": ""},
        }
        over = simulate_sequence(
            view, [{"op": "remove_file", "path": "a.py"}] * (MAX_OPS + 1)
        )
        assert over["status"] == "unavailable"
        assert over["reason"] == "cap_ops"
        assert over["ops_applied"] == 0
        assert over["truncated"] is False
        bad = simulate_sequence(view, [{"op": "remove_file", "path": "missing.py"}])
        assert bad["status"] == "unavailable"
        assert bad["reason"] == "invalid_id"
        assert bad["ops_applied"] == 0
        assert bad["truncated"] is False
        late = simulate_sequence(view, [{"op": "remove_file", "path": "a.py"}], now=0.0)
        assert late["status"] == "unavailable"
        assert late["reason"] == "deadline"
        assert late["ops_applied"] == 0
        assert late["truncated"] is False


class TestRegistryParityP6:
    def test_cbm_evolution_registered_and_schema_matches_handler(self):
        assert "cbm_evolution" in code_tools.TOOL_MAP
        assert code_tools.TOOL_PHASES.get("cbm_evolution") == "T1"
        handler = code_tools.TOOL_MAP["cbm_evolution"]
        sig = inspect.signature(handler)
        handler_params = {
            p.name
            for p in sig.parameters.values()
            if p.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
        }
        props = _schema_props("cbm_evolution")
        assert props == handler_params
        row = _schema_row("cbm_evolution")
        params = row.get("parameters") or {}
        days = (params.get("properties") or {}).get("lookback_days") or {}
        assert days.get("default") == 90
        assert "include_extensions" not in props
        assert "project_path" in (params.get("required") or [])
        assert "complexity_map" in props

    def test_cbm_dsm_registered_and_schema_matches_handler(self):
        assert "cbm_dsm" in code_tools.TOOL_MAP
        assert code_tools.TOOL_PHASES.get("cbm_dsm") == "T1"
        handler = code_tools.TOOL_MAP["cbm_dsm"]
        sig = inspect.signature(handler)
        handler_params = {
            p.name
            for p in sig.parameters.values()
            if p.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
        }
        props = _schema_props("cbm_dsm")
        assert props == handler_params
        row = _schema_row("cbm_dsm")
        params = row.get("parameters") or {}
        kind = (params.get("properties") or {}).get("edge_kind") or {}
        assert kind.get("default") == "observed"
        assert set(kind.get("enum") or []) == {"observed", "strict"}
        assert "include_extensions" not in props
        assert "project_path" in (params.get("required") or [])
        page = (params.get("properties") or {}).get("page_rows") or {}
        assert page.get("default") == 100

    def test_legacy_cycle_point_still_project_path_only(self):
        props = _schema_props("cbm_cycle_point")
        assert props == {"project_path"}


class TestNoHiddenT1WriteP6:
    def test_evolution_and_dsm_source_never_writes(self):
        from orchestrator.graph import evolution as _evo
        from orchestrator.graph import dsm as _dsm_mod

        evo_src = inspect.getsource(gtools.cbm_evolution)
        dsm_src = inspect.getsource(gtools.cbm_dsm)
        evo_k = inspect.getsource(_evo.compute_evolution)
        dsm_k = inspect.getsource(_dsm_mod.build_dsm)
        for blob in (evo_src, dsm_src, evo_k, dsm_k):
            assert "write_new_file" not in blob
            assert "write_fix" not in blob
            assert "Path.write" not in blob
        assert "get_db" not in evo_src
        assert "get_db" not in dsm_src
        assert "wrote" in evo_src
        assert "wrote" in dsm_src
        assert "shell=True" not in evo_k
        assert "git_stage" not in evo_src
        assert "git_diff" not in evo_src
        walk = inspect.getsource(_evo.walk_git_history)
        assert "shell=False" in walk or "shell=False" in inspect.getsource(_evo._run_git)
