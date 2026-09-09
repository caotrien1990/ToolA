"""Static test-gap: multi-source BFS from explicit test seeds (P3-S1).

Adapted from the sentrux test-gap BFS idea (MIT) onto toolA's readonly
analysis_view. Not a Rust port, not bitwise parity, no vendored crate:
campaign formulas win. Isolated production stays in the uncovered
denominator. Missing cyclomatic complexity is unavailable — rank by
fan-in, never fabricate CC=1.

Advisory only: import reach is not executed coverage.
"""
from __future__ import annotations

import os
import time
from collections import deque
from typing import Any, Callable, Iterable

from .analysis_view import (
    ALGORITHM_VERSION,
    PAYLOAD_VERSION,
    _CONFIDENCE_POLICY,
    _contained,
    _identity_key,
    _norm_slash,
    _resolve_physical,
    _sorted_display,
    build_analysis_view,
)
from .config import ANALYSIS_TOP_LIST_MAX, EXTRACTOR_VERSION
from .schema import SCHEMA_VERSION


ALGORITHM_NAME = "test-gap-v1"


def _unavailable(reason: str, *, root: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "version": PAYLOAD_VERSION,
        "status": "unavailable",
        "reason": reason,
        "pass": False,
        "score": None,
        "coverage_ratio": None,
        "vacuous": False,
        "source_files": [],
        "test_files": [],
        "covered_files": [],
        "uncovered_files": [],
        "isolated_uncovered": [],
        "gaps": [],
        "per_test": [],
        "counts": {},
        "metric_notes": [reason],
        "truncated": False,
        "reasons": [reason],
        "provenance": {
            "root": root,
            "extractor_version": EXTRACTOR_VERSION,
            "algorithm_version": ALGORITHM_NAME,
            "view_algorithm_version": ALGORITHM_VERSION,
            "schema_version": SCHEMA_VERSION,
            "confidence_policy": dict(_CONFIDENCE_POLICY),
        },
        "elapsed_ms": 0,
    }
    if extra:
        payload.update(extra)
    return payload


def _abspath_under(root: str, rel: str) -> str:
    raw = str(rel or "")
    if os.path.isabs(raw):
        return os.path.abspath(raw)
    return os.path.abspath(os.path.join(root, raw.replace("/", os.sep)))


def reject_out_of_root(root: str, paths: Iterable[str]) -> str | None:
    """Reject any path whose intended or physical target leaves root."""
    root_abs = os.path.abspath(root)
    root_phys, root_err = _resolve_physical(root_abs)
    if root_err or not root_phys:
        return "out_of_root"
    for raw in paths:
        display = _norm_slash(raw)
        if not display:
            continue
        cand = _abspath_under(root_abs, raw)
        if not _contained(cand, root_abs):
            return "out_of_root"
        if os.path.exists(cand):
            phys, err = _resolve_physical(cand)
            if err or not phys or not _contained(phys, root_phys):
                return "out_of_root"
    return None


def _fan_in_map(edges: list[dict[str, Any]], universe: dict[str, str]) -> dict[str, int]:
    counts: dict[str, set[str]] = {k: set() for k in universe}
    for edge in edges:
        src_k = _identity_key(edge["src"])
        dst_k = _identity_key(edge["dst"])
        if dst_k in counts and src_k:
            counts[dst_k].add(src_k)
    return {k: len(v) for k, v in counts.items()}


def _adjacency(edges: list[dict[str, Any]]) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for edge in edges:
        src_k = _identity_key(edge["src"])
        dst_k = _identity_key(edge["dst"])
        if not src_k or not dst_k:
            continue
        bucket = seen.setdefault(src_k, set())
        if dst_k in bucket:
            continue
        bucket.add(dst_k)
        adj.setdefault(src_k, []).append(dst_k)
    for src_k in adj:
        adj[src_k].sort()
    return adj


def _bfs_reach(
    seeds: list[str],
    adj: dict[str, list[str]],
    production: dict[str, str],
    *,
    limit: int | None = None,
) -> tuple[list[str], bool]:
    visited: set[str] = set()
    covered: list[str] = []
    covered_keys: set[str] = set()
    truncated = False
    queue: deque[str] = deque()
    for seed in seeds:
        key = _identity_key(seed)
        if not key or key in visited:
            continue
        visited.add(key)
        queue.append(key)
    while queue:
        node = queue.popleft()
        if node in production and node not in covered_keys:
            if limit is not None and len(covered) >= limit:
                truncated = True
            else:
                covered.append(production[node])
                covered_keys.add(node)
        for target in adj.get(node, ()):
            if target in visited:
                continue
            visited.add(target)
            queue.append(target)
    return _sorted_display(covered), truncated


def _rank_gaps(
    uncovered: list[str],
    fan_in: dict[str, int],
    production: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in uncovered:
        key = _identity_key(path)
        rows.append(
            {
                "file": production.get(key, path),
                "fan_in": int(fan_in.get(key, 0)),
                "max_complexity": None,
                "rank_key": "fan_in",
            }
        )
    rows.sort(key=lambda r: (-int(r["fan_in"]), str(r["file"]).casefold()))
    return rows


def compute_test_gap(
    view: dict[str, Any],
    *,
    per_test_maps: bool = False,
    top_list_max: int | None = None,
) -> dict[str, Any]:
    """Compute test-gap over an analysis_view payload. Pure; no store I/O."""
    t0 = time.monotonic()
    root = str((view.get("provenance") or {}).get("root") or "")
    status = view.get("status")
    if status == "unavailable":
        extra = {
            "uncovered_files": list(view.get("included_files") or []),
            "test_files": list(view.get("test_files") or []),
        }
        return _unavailable(str(view.get("reason") or "unavailable"), root=root, extra=extra)

    included = _sorted_display(view.get("included_files") or [])
    tests = _sorted_display(view.get("test_files") or [])
    test_keys = {_identity_key(p): p for p in tests}
    production = {
        _identity_key(p): p for p in included if _identity_key(p) not in test_keys
    }
    source_files = _sorted_display(production.values())

    notes = [
        "advisory_import_reach_not_executed_coverage",
        "max_complexity_unavailable_rank_by_fan_in",
        "no_cc_fabrication",
        "isolated_production_stays_uncovered",
    ]
    isolated_view = [
        p
        for p in (view.get("isolated_files") or [])
        if _identity_key(p) in production
    ]

    if not tests:
        payload = _unavailable(
            "no_tests",
            root=root,
            extra={
                "source_files": source_files,
                "test_files": [],
                "uncovered_files": source_files,
                "isolated_uncovered": _sorted_display(isolated_view),
                "coverage_ratio": None,
                "vacuous": False,
                "metric_notes": notes + ["no_tests"],
            },
        )
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return payload

    if not source_files:
        payload = {
            "version": PAYLOAD_VERSION,
            "status": "ok",
            "reason": None,
            "pass": False,
            "score": None,
            "coverage_ratio": 1.0,
            "vacuous": True,
            "source_files": [],
            "test_files": tests,
            "covered_files": [],
            "uncovered_files": [],
            "isolated_uncovered": [],
            "gaps": [],
            "per_test": [],
            "counts": {
                "source_files": 0,
                "test_files": len(tests),
                "covered_files": 0,
                "uncovered_files": 0,
            },
            "metric_notes": notes + ["vacuous_no_source"],
            "truncated": False,
            "reasons": [],
            "provenance": {
                "root": root,
                "extractor_version": EXTRACTOR_VERSION,
                "algorithm_version": ALGORITHM_NAME,
                "view_algorithm_version": ALGORITHM_VERSION,
                "schema_version": SCHEMA_VERSION,
                "included_set_hash": (view.get("provenance") or {}).get("included_set_hash"),
                "fingerprint": (view.get("provenance") or {}).get("fingerprint"),
                "confidence_policy": dict(_CONFIDENCE_POLICY),
            },
            "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
        }
        return payload

    edges = list(view.get("observed_edges") or [])
    adj = _adjacency(edges)
    covered, _ = _bfs_reach(tests, adj, production)
    covered_keys = {_identity_key(p) for p in covered}
    uncovered = [p for p in source_files if _identity_key(p) not in covered_keys]
    isolated_uncovered = [
        p for p in _sorted_display(isolated_view) if _identity_key(p) not in covered_keys
    ]
    fan_in = _fan_in_map(edges, production)
    cap = ANALYSIS_TOP_LIST_MAX if top_list_max is None else int(top_list_max)
    if cap < 1:
        cap = 1
    ranked = _rank_gaps(uncovered, fan_in, production)
    truncated = len(ranked) > cap
    gaps = ranked[:cap]

    per_test: list[dict[str, Any]] = []
    if per_test_maps:
        for test_path in tests:
            covers, cut = _bfs_reach([test_path], adj, production, limit=cap)
            if len(covers) > cap:
                covers = covers[:cap]
                cut = True
            per_test.append(
                {
                    "test_file": test_path,
                    "covers": covers,
                    "truncated": bool(cut or len(covers) >= cap and cut),
                }
            )
            truncated = truncated or bool(cut)

    ratio = len(covered) / float(len(source_files))
    out_status = "ok"
    if status == "partial":
        out_status = "partial"
        notes.append("view_partial")

    return {
        "version": PAYLOAD_VERSION,
        "status": out_status,
        "reason": None,
        "pass": False,
        "score": None,
        "coverage_ratio": ratio,
        "vacuous": False,
        "source_files": source_files,
        "test_files": tests,
        "covered_files": covered,
        "uncovered_files": uncovered,
        "isolated_uncovered": isolated_uncovered,
        "gaps": gaps,
        "per_test": per_test,
        "counts": {
            "source_files": len(source_files),
            "test_files": len(tests),
            "covered_files": len(covered),
            "uncovered_files": len(uncovered),
            "isolated_uncovered": len(isolated_uncovered),
        },
        "metric_notes": notes,
        "truncated": truncated,
        "reasons": [],
        "provenance": {
            "root": root,
            "extractor_version": EXTRACTOR_VERSION,
            "algorithm_version": ALGORITHM_NAME,
            "view_algorithm_version": ALGORITHM_VERSION,
            "schema_version": SCHEMA_VERSION,
            "included_set_hash": (view.get("provenance") or {}).get("included_set_hash"),
            "fingerprint": (view.get("provenance") or {}).get("fingerprint"),
            "confidence_policy": dict(_CONFIDENCE_POLICY),
        },
        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
    }


def build_test_gap(
    project_path: str,
    *,
    included_files: Iterable[str] | None = None,
    test_files: Iterable[str] | None = None,
    per_test_maps: bool = False,
    top_list_max: int | None = None,
    checkpoint: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Load the readonly view (tests unioned into the edge universe) then compute."""
    root = os.path.abspath(project_path or "")
    included = _sorted_display(included_files or [])
    tests = _sorted_display(test_files or [])
    probe = reject_out_of_root(root, list(included) + list(tests))
    if probe:
        return _unavailable(probe, root=root)
    if checkpoint is not None and checkpoint("test_gap"):
        raise InterruptedError("analysis_stop")

    universe = _sorted_display(list(included) + list(tests))
    view = build_analysis_view(
        root,
        included_files=universe,
        test_files=tests,
        finalize_scope=False,
        checkpoint=checkpoint,
    )
    # Restore production-only included list for the metric denominator.
    if isinstance(view, dict) and view.get("status") != "unavailable":
        view = dict(view)
        view["included_files"] = included
        view["test_files"] = tests
        isolated = []
        included_keys = {_identity_key(p) for p in included}
        test_keys = {_identity_key(p) for p in tests}
        connected: set[str] = set()
        for edge in view.get("observed_edges") or []:
            connected.add(_identity_key(edge["src"]))
            connected.add(_identity_key(edge["dst"]))
        for path in included:
            key = _identity_key(path)
            if key in included_keys and key not in test_keys and key not in connected:
                isolated.append(path)
        view["isolated_files"] = isolated
    return compute_test_gap(view, per_test_maps=per_test_maps, top_list_max=top_list_max)


__all__ = [
    "ALGORITHM_NAME",
    "build_test_gap",
    "compute_test_gap",
    "reject_out_of_root",
]
