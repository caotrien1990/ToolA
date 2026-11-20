"""D8: section 3.2 freshness attachment -- per-tier tests (v12 section 3.2
BY REFERENCE; plan r8 section D8).

Every graph-consuming cbm answer carries the freshness sample it was produced under.
Scope note (implementer clarification, 2026-11-20): the v12 §3.2 sentence is a blanket statement ("every cbm response"); it does not scope D8 to graph-consuming tools, and no sentence in §3.2 (or in r8, which carries §3.2 by reference) states such a scope. cbm_evolution reads git history live, not the graph, so no freshness sample exists for it; attaching a graph-freshness label would misdescribe the data. 13 of 17 cbm_* tools are wired; cbm_evolution is deliberately not wired.
Legs per tier, all in-process (direct handler calls on a throwaway
3-file project indexed once per module -- the frozen P0-4 fixture is
never written):

- tier-3 raw-query (5 tools): success leg carries the three label keys
  ONLY (freshness_label / status_error / stale_scan) -- no sample keys;
  the label comes from a floor-budgeted `graph_status` read.
- tier-2 helper-gated: success + refusal (freshness_unknown,
  stale_scan_incomplete, auto-indexed) + handler-error legs.
- tier-1 view-only / view-gated: success + view-refusal + no-sample
  handler-error legs.
- dual-gated: success (agree), disagree (helper fresh + view stale),
  auto-indexed exclusion (helper excluded, view alone, disagree False).
- unit tests: the STEP 4 outcome mapping, the view-sample shapes, the
  no-sample branch, attach-if-absent merge.

The label is a disclosure, never a refusal (v12 section 3.1 retained):
no test here asserts a refusal where the pre-D8 code answered.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import analysis_view as _av
from orchestrator.graph import tools as _tools

FRESH_HELPER = {"auto_indexed": False, "reason": "fresh"}
UNKNOWN_HELPER = {"auto_indexed": False, "reason": "freshness_unknown"}
SCAN_HELPER = {
    "auto_indexed": False,
    "reason": "stale_scan_incomplete",
    "stale_scan": {"complete": False, "stale_files": 2},
    "freshness": "stale",
}
AUTOINDEX_HELPER = {"auto_indexed": True, "reason": "stale", "index_result": {}}
REFUSAL_VIEW = {
    "status": "unavailable",
    "reason": "freshness:stale",
    "freshness": "stale",
    "stale_scan": {"complete": True, "stale_files": 1},
}
STALE_OK_VIEW = {
    "status": "ok",
    "reason": None,
    "unmatched_files": [],
    "freshness": "stale",
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

B_PY = (
    "def helper():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def worker():\n"
    "    return helper()\n"
)
A_PY = "import b\n\n\ndef run():\n    return b.worker()\n"
C_PY = "import b\n\n\ndef main():\n    return b.helper()\n"


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("d8proj")
    (root / "b.py").write_text(B_PY, encoding="utf-8")
    (root / "a.py").write_text(A_PY, encoding="utf-8")
    (root / "c.py").write_text(C_PY, encoding="utf-8")
    raw = asyncio.run(_tools.cbm_index(project_path=str(root), scope="full"))
    payload = json.loads(raw)
    assert payload["ok"] is True, payload
    st = json.loads(asyncio.run(_tools.cbm_graph_status(project_path=str(root))))
    assert st["result"]["freshness"] == "fresh", st
    return str(root)


def _run(coro):
    return json.loads(asyncio.run(coro))


def _stub_helper(monkeypatch, payload):
    async def _fake(project_path):
        return dict(payload)

    monkeypatch.setattr(_tools, "_ensure_graph_fresh", _fake)


def _stub_view(monkeypatch, payload):
    def _fake(*args, **kwargs):
        return dict(payload)

    monkeypatch.setattr(_av, "build_analysis_view", _fake)


def _assert_label(result, *, label, status_error=None, stale_scan=None):
    assert result["freshness_label"] == label, result
    assert result["status_error"] == status_error, result
    assert result["stale_scan"] == stale_scan, result


def _assert_no_sample_keys(result):
    assert "helper_sample" not in result, result
    assert "view_sample" not in result, result
    assert "samples_disagree" not in result, result


# ─── Tier-3: raw-query (5 tools) ────────────────────────────────────────────

def test_t3_nodes_success(project):
    out = _run(_tools.cbm_nodes_query(project_path=project, kind="function", limit=5))
    assert out["ok"] is True, out
    res = out["result"]
    assert len(res["results"]) >= 1, res
    _assert_label(res, label="fresh", stale_scan=res["stale_scan"])
    assert isinstance(res["stale_scan"], dict) and res["stale_scan"]["complete"] is True
    _assert_no_sample_keys(res)


def test_t3_edges_success(project):
    out = _run(_tools.cbm_edges_query(project_path=project, kind="calls", limit=5))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh", stale_scan=res["stale_scan"])
    assert isinstance(res["stale_scan"], dict) and res["stale_scan"]["complete"] is True
    _assert_no_sample_keys(res)


def test_t3_template_success(project):
    out = _run(_tools.cbm_template_query(
        project_path=project, template="callers", name="helper", limit=5))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh", stale_scan=res["stale_scan"])
    assert isinstance(res["stale_scan"], dict) and res["stale_scan"]["complete"] is True
    _assert_no_sample_keys(res)


def test_t3_trace_success(project):
    tpl = _run(_tools.cbm_template_query(
        project_path=project, template="callers", name="helper", limit=5))
    items = tpl["result"].get("results") or tpl["result"].get("rows") or []
    assert items, tpl
    node_id = items[0].get("id") or items[0].get("node_id")
    assert node_id, items[0]
    out = _run(_tools.cbm_trace_path(project_path=project, start_node_id=node_id, depth=2))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh", stale_scan=res["stale_scan"])
    assert isinstance(res["stale_scan"], dict) and res["stale_scan"]["complete"] is True
    _assert_no_sample_keys(res)


def test_t3_council_success(project):
    out = _run(_tools.cbm_council_context(project_path=project, name="helper", depth=2))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh", stale_scan=res["stale_scan"])
    assert isinstance(res["stale_scan"], dict) and res["stale_scan"]["complete"] is True
    _assert_no_sample_keys(res)


def test_t3_error_bad_int_carries_status_sample(project):
    out = _run(_tools.cbm_template_query(
        project_path=project, template="callers", name="helper", depth="not-a-number"))
    assert out["ok"] is False, out
    assert out["error_name"] == "ValueError", out
    _assert_label(out, label="fresh", stale_scan=out["stale_scan"])
    assert isinstance(out["stale_scan"], dict) and out["stale_scan"]["complete"] is True


def test_t3_trace_b023_carries_label(project):
    out = _run(_tools.cbm_trace_path(
        project_path=project, start_node_id="no/such.py:function:nope:1", depth=2))
    assert out["ok"] is False, out
    assert out["error_name"] == "START_NODE_NOT_FOUND", out
    _assert_label(out, label="fresh", stale_scan=out["stale_scan"])
    assert isinstance(out["stale_scan"], dict) and out["stale_scan"]["complete"] is True


# ─── Tier-2: helper-gated ──────────────────────────────────────────────────

def test_t2_architecture_success(project):
    out = _run(_tools.cbm_get_architecture(project_path=project))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh")
    assert res["helper_sample"] == FRESH_HELPER, res
    assert "view_sample" not in res and "samples_disagree" not in res


def test_t2_dead_code_success(project):
    out = _run(_tools.cbm_dead_code(project_path=project, limit=5))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh")
    assert res["helper_sample"] == FRESH_HELPER, res
    assert "view_sample" not in res and "samples_disagree" not in res


def test_t2_cycle_point_success(project):
    out = _run(_tools.cbm_cycle_point(project_path=project))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh")
    assert res["helper_sample"] == FRESH_HELPER, res
    assert "view_sample" not in res and "samples_disagree" not in res


def test_t2_refusal_unknown(project, monkeypatch):
    _stub_helper(monkeypatch, UNKNOWN_HELPER)
    out = _run(_tools.cbm_get_architecture(project_path=project))
    assert out["ok"] is True, out
    res = out["result"]
    assert res["status"] == "unavailable", res
    assert res["reason"] == "freshness:freshness_unknown", res
    _assert_label(res, label="unknown", status_error="freshness_unknown")
    assert res["helper_sample"] == UNKNOWN_HELPER, res


def test_t2_refusal_stale_scan_incomplete(project, monkeypatch):
    _stub_helper(monkeypatch, SCAN_HELPER)
    out = _run(_tools.cbm_dead_code(project_path=project, limit=5))
    assert out["ok"] is True, out
    res = out["result"]
    assert res["status"] == "unavailable", res
    assert res["reason"] == "freshness:stale_scan_incomplete", res
    _assert_label(res, label="unknown", status_error="stale_scan_incomplete",
                  stale_scan={"complete": False, "stale_files": 2})
    # The carried `freshness` value is RECORDED verbatim, never compared.
    assert res["helper_sample"] == SCAN_HELPER, res


def test_t2_auto_indexed_helper_only_is_unknown(project, monkeypatch):
    _stub_helper(monkeypatch, AUTOINDEX_HELPER)
    out = _run(_tools.cbm_cycle_point(project_path=project))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="unknown")
    assert res["helper_sample"] == AUTOINDEX_HELPER, res
    assert "view_sample" not in res and "samples_disagree" not in res


def test_t2_error_architecture_carries_helper_sample(project, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("boom-d8")

    monkeypatch.setattr(_tools._query, "cbm_get_architecture", _boom)
    out = _run(_tools.cbm_get_architecture(project_path=project))
    assert out["ok"] is False, out
    assert out["error_name"] == "RuntimeError", out
    _assert_label(out, label="fresh")
    assert out["helper_sample"] == FRESH_HELPER, out


# ─── Tier-1: view-only / view-gated ────────────────────────────────────────

def test_t1_dsm_success(project):
    out = _run(_tools.cbm_dsm(project_path=project, page_rows=5, page_cols=5))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh")
    assert res["view_sample"] == {"value": "fresh"}, res
    assert "helper_sample" not in res and "samples_disagree" not in res


def test_t1_quality_gate_snapshot_success(project):
    out = _run(_tools.cbm_quality_gate(project_path=project, mode="snapshot"))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh")
    assert res["view_sample"] == {"value": "fresh"}, res
    assert "helper_sample" not in res and "samples_disagree" not in res


def test_t1_analysis_view_success(project):
    out = _run(_tools.cbm_analysis_view(project_path=project))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh")
    assert res["view_sample"] == {"value": "fresh"}, res
    assert "helper_sample" not in res and "samples_disagree" not in res


def test_t1_quality_gate_view_refusal(project, monkeypatch):
    _stub_view(monkeypatch, REFUSAL_VIEW)
    out = _run(_tools.cbm_quality_gate(project_path=project, mode="snapshot"))
    assert out["ok"] is True, out
    res = out["result"]
    assert res["status"] == "unavailable", res
    _assert_label(res, label="stale",
                  stale_scan={"complete": True, "stale_files": 1})
    assert res["view_sample"] == {"value": "stale", "reason": "freshness:stale"}, res
    assert "helper_sample" not in res and "samples_disagree" not in res


def test_t1_analysis_view_refusal(project, monkeypatch):
    async def _fake_run(*args, **kwargs):
        return dict(REFUSAL_VIEW)

    monkeypatch.setattr(_tools._analysis_service, "run_analysis_view", _fake_run)
    out = _run(_tools.cbm_analysis_view(project_path=project))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="stale",
                  stale_scan={"complete": True, "stale_files": 1})
    assert res["view_sample"] == {"value": "stale", "reason": "freshness:stale"}, res


def test_t1_analysis_view_error_no_sample(project, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("boom-d8")

    monkeypatch.setattr(_tools._analysis_service, "run_analysis_view", _boom)
    out = _run(_tools.cbm_analysis_view(project_path=project))
    assert out["ok"] is False, out
    assert out["error_name"] == "RuntimeError", out
    _assert_label(out, label="unknown", status_error="no_sample")


# ─── Dual-gated ────────────────────────────────────────────────────────────

def test_dual_test_gap_success_agree(project):
    out = _run(_tools.cbm_test_gap(project_path=project, include_architecture=True))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh")
    assert res["helper_sample"] == FRESH_HELPER, res
    assert res["view_sample"] == {"value": "fresh"}, res
    assert res["samples_disagree"] is False, res


def test_dual_test_gap_no_arch_helper_only(project):
    # want_arch=False builds no view: the attachment degrades honestly
    # to helper-only (view_sample attaches only where a view sample
    # exists).
    out = _run(_tools.cbm_test_gap(project_path=project, include_architecture=False))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh")
    assert res["helper_sample"] == FRESH_HELPER, res
    assert "view_sample" not in res and "samples_disagree" not in res


def test_dual_what_if_success_agree(project):
    out = _run(_tools.cbm_what_if(project_path=project, ops=[]))
    assert out["ok"] is True, out
    res = out["result"]
    _assert_label(res, label="fresh")
    assert res["helper_sample"] == FRESH_HELPER, res
    assert res["view_sample"] == {"value": "fresh"}, res
    assert res["samples_disagree"] is False, res


def test_dual_disagree_helper_fresh_view_stale(project, monkeypatch):
    _stub_view(monkeypatch, STALE_OK_VIEW)
    out = _run(_tools.cbm_what_if(project_path=project, ops=[]))
    assert out["ok"] is True, out
    res = out["result"]
    # Most conservative of {fresh, stale} is stale; the comparison uses
    # `value` only, never the reason string.
    _assert_label(res, label="stale")
    assert res["helper_sample"] == FRESH_HELPER, res
    assert res["view_sample"] == {"value": "stale"}, res
    assert res["samples_disagree"] is True, res


def test_dual_excluded_helper_test_gap(project, monkeypatch):
    _stub_helper(monkeypatch, AUTOINDEX_HELPER)
    out = _run(_tools.cbm_test_gap(project_path=project, include_architecture=True))
    assert out["ok"] is True, out
    res = out["result"]
    # Auto-indexed helper is EXCLUDED: the operative label comes from the
    # post-index view alone; `samples_disagree` False means "no
    # comparison", NOT "samples agree".
    _assert_label(res, label="fresh")
    assert res["helper_sample"] == AUTOINDEX_HELPER, res
    assert res["view_sample"] == {"value": "fresh"}, res
    assert res["samples_disagree"] is False, res


# ─── Unit: mapping / shapes / merge ────────────────────────────────────────

def test_unit_map_helper_sample():
    assert _tools._map_helper_sample(FRESH_HELPER, dual_gated=False) == ("fresh", False)
    assert _tools._map_helper_sample(FRESH_HELPER, dual_gated=True) == ("fresh", False)
    assert _tools._map_helper_sample(AUTOINDEX_HELPER, dual_gated=True) == (None, True)
    assert _tools._map_helper_sample(AUTOINDEX_HELPER, dual_gated=False) == ("unknown", False)
    assert _tools._map_helper_sample(UNKNOWN_HELPER, dual_gated=False) == ("unknown", False)
    assert _tools._map_helper_sample(UNKNOWN_HELPER, dual_gated=True) == ("unknown", False)


def test_unit_view_sample_shapes():
    sample, err, scan = _tools._view_sample_from_payload(
        {"status": "ok", "freshness": "fresh"})
    assert sample == {"value": "fresh"} and err is None and scan is None
    sample, err, scan = _tools._view_sample_from_payload(REFUSAL_VIEW)
    assert sample == {"value": "stale", "reason": "freshness:stale"}
    assert err is None and scan == {"complete": True, "stale_files": 1}
    sample, err, scan = _tools._view_sample_from_payload(
        {"status": "unavailable", "reason": "missing_db"})
    assert sample == {"value": "unknown", "reason": "missing_db"}
    assert err is None and scan is None
    sample, err, scan = _tools._view_sample_from_payload(
        {"status": "unavailable", "reason": "unavailable:busy"})
    assert sample is None and err is None and scan is None
    assert _tools._view_sample_from_payload(None) == (None, None, None)
    assert _tools._view_sample_from_payload("not-a-dict") == (None, None, None)


def test_unit_attachment_no_sample():
    attach = _tools._freshness_attachment()
    assert attach == {"freshness_label": "unknown", "status_error": "no_sample",
                      "stale_scan": None}


def test_unit_attach_if_absent_and_passthrough():
    payload = {"freshness_label": "kept", "result": 1}
    out = _tools._attach_freshness(payload, helper_sample=FRESH_HELPER)
    assert out["freshness_label"] == "kept"
    assert out["helper_sample"] == FRESH_HELPER
    assert _tools._attach_freshness("not-a-dict", helper_sample=FRESH_HELPER) == "not-a-dict"


def test_unit_rank_unmapped_is_unknown():
    assert _tools._freshness_rank("bogus") == _tools._freshness_rank("unknown") == 3
    assert _tools._freshness_rank("stale") < _tools._freshness_rank("fresh")
    assert _tools._freshness_rank("corrupt") < _tools._freshness_rank("stale")
