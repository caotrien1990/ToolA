"""A44: P23 Sprint 3/4 matrix coverage families and dashboard ratchets.

WHY this file exists: Sprint 3 taught audit_tool_matrix to REPORT doc,
purpose, and error-name phase coverage (15 new summary keys plus the
coverage_detail container), and Sprint 4 turned four of those numbers
into acceptance_dashboard regression gates (ratchets). A report nobody
gates on rots silently, so this file pins the shape structurally:

  (a) the matrix summary carries all 15 new keys;
  (b) drift stays [] on the real audit with the new families present
      (coverage must NEVER enter drift -- see the Sprint 3 design);
  (c) purpose accounting balances against an INDEPENDENT test-side
      scope census over the registry, not against the matrix's own
      arithmetic;
  (d) error accounting balances against a closed system-level error
      set re-derived here, per source, from the same four in-memory
      mechanisms Sprint 3 Step 0 used -- never read out of the
      matrix's detail block;
  (e) the dashboard's targets / actuals / deltas each CONTAIN the
      four new ratchet keys by name (presence, not merely a passing
      all_pass bool, which s.get(..., 0) defaults could fake).

NO assertion here pins the debt literals (undescribed-param, weak-
description, or unmapped-error counts) against the matrix: those
numbers live only as dashboard targets, the ratchet's contract
location, and may only move down as the debt is paid off.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from functools import lru_cache

# Import-lift FIRST: orchestrator_server must be imported before any
# independent count, or the registry surface is the 213-tool
# code_tools-only subset instead of the full 258 (the trap recorded
# at code_tools' audit self-lift; precedent test_a37 line 22).
import orchestrator_server as srv

import code_tools as ct
import tools_v15


NEW_SUMMARY_KEYS = [
    "doc_description_present_count",
    "doc_description_weak_count",
    "doc_params_total",
    "doc_params_described_count",
    "doc_params_undescribed_count",
    "doc_param_examples_registry_count",
    "doc_param_examples_served_count",
    "doc_fields_absent_by_design_count",
    "purpose_eligible_count",
    "purpose_covered_count",
    "purpose_eligible_gap_count",
    "error_phase_map_entries",
    "error_system_names_total",
    "error_system_names_phase_mapped_count",
    "error_system_names_unmapped_count",
]

NEW_TARGET_KEYS = [
    "purpose_eligible_gap_max",
    "doc_params_undescribed_max",
    "doc_weak_description_count_max",
    "error_system_names_unmapped_max",
]

NEW_ACTUAL_KEYS = [
    "purpose_eligible_gap_count",
    "doc_params_undescribed_count",
    "doc_description_weak_count",
    "error_system_names_unmapped_count",
]

NEW_DELTA_KEYS = [
    "purpose_eligible_gap_ok",
    "doc_params_undescribed_ok",
    "doc_weak_description_ok",
    "error_system_names_unmapped_ok",
]


@lru_cache(maxsize=1)
def _matrix() -> dict:
    return ct._audit_tool_matrix_sync("", persist=False)


@lru_cache(maxsize=1)
def _dashboard() -> dict:
    raw = asyncio.run(tools_v15.acceptance_dashboard(project_path=""))
    return json.loads(raw) if isinstance(raw, str) else raw


def _registry_names() -> set:
    names = set()
    for entry in getattr(ct, "TOOL_SCHEMAS", ()) or ():
        fn = entry.get("function") if isinstance(entry, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            names.add(str(fn["name"]))
    return names


def _derived_error_system_names() -> set:
    """Closed system-level error set, re-derived per source.

    The four mechanisms are Sprint 3 Step 0's, implemented here in
    the test -- the expected size is computed, never copied from
    the matrix's summary or its coverage_detail block.
    """
    names = set()
    # Source 1: probe the pure builder for its error_name.
    probe_name = ct._json_error_dict("probe").get("error_name")
    if probe_name:
        names.add(str(probe_name))
    # Source 2: inspect.signature default of tools_v15._err.
    default = inspect.signature(tools_v15._err).parameters["error_name"].default
    if default:
        names.add(str(default))
    # Source 3: class walk of orchestrator.core.errors.
    from orchestrator.core import errors as _errors_mod

    for obj in vars(_errors_mod).values():
        if isinstance(obj, type) and issubclass(obj, BaseException):
            err_name = getattr(obj, "error_name", None)
            if err_name:
                names.add(str(err_name))
    # Source 4: the dispatcher's deterministic set, read off the
    # orchestrator_server module imported at the top of this file.
    det = getattr(srv, "_DETERMINISTIC_TOOLBODY_ERRORS", None)
    if det:
        names.update(str(x) for x in det)
    return names


def test_matrix_summary_contains_all_new_keys():
    summary = _matrix().get("summary") or {}
    missing = [k for k in NEW_SUMMARY_KEYS if k not in summary]
    assert missing == [], f"matrix summary missing new keys: {missing}"


def test_matrix_drift_empty_with_new_families_present():
    matrix = _matrix()
    summary = matrix.get("summary") or {}
    assert all(k in summary for k in NEW_SUMMARY_KEYS)
    assert matrix.get("drift") == []


def test_purpose_accounting_identity():
    summary = _matrix().get("summary") or {}
    eligible_test_side = {
        name
        for name in _registry_names()
        if ct.tool_mutation_scope(name) in ct.NON_SOURCE_MUTATION_SCOPES
    }
    assert len(eligible_test_side) == summary.get("purpose_eligible_count")
    assert (
        summary.get("purpose_covered_count", 0)
        + summary.get("purpose_eligible_gap_count", 0)
        == summary.get("purpose_eligible_count")
    )


def test_error_accounting_identity():
    summary = _matrix().get("summary") or {}
    assert (
        summary.get("error_system_names_phase_mapped_count", 0)
        + summary.get("error_system_names_unmapped_count", 0)
        == summary.get("error_system_names_total")
    )
    assert summary.get("error_system_names_total") == len(
        _derived_error_system_names()
    )


def test_dashboard_contains_new_ratchet_keys():
    dashboard = _dashboard()
    targets = dashboard.get("targets_r29") or {}
    actuals = dashboard.get("actuals") or {}
    deltas = dashboard.get("deltas_vs_targets") or {}
    assert [k for k in NEW_TARGET_KEYS if k not in targets] == []
    assert [k for k in NEW_ACTUAL_KEYS if k not in actuals] == []
    assert [k for k in NEW_DELTA_KEYS if k not in deltas] == []
