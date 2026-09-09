"""Entry-reach BFS on the same file universe as analysis_view (P3-S2).

Absent entry => unknown, never a fake empty surface.
Reach follows observed (or requested) imports_module edges.
Disconnected production stays out of the reachable set.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from .analysis_view import (
    _identity_key,
    _sorted_display,
)
from .testgap import reject_out_of_root


ALGORITHM_NAME = "entry-reach-v1"


def _adjacency(edges: list[dict[str, Any]]) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for edge in edges:
        src_k = _identity_key(edge.get("src") or "")
        dst_k = _identity_key(edge.get("dst") or "")
        if not src_k or not dst_k:
            continue
        bucket = seen.setdefault(src_k, set())
        if dst_k in bucket:
            continue
        bucket.add(dst_k)
        adj.setdefault(src_k, []).append(dst_k)
    for src in adj:
        adj[src].sort()
    return adj


def compute_entry_reach(
    view: dict[str, Any],
    entry_files: Iterable[str] | None,
    *,
    edge_kind: str = "observed",
) -> dict[str, Any]:
    root = str((view.get("provenance") or {}).get("root") or "")
    entries = _sorted_display(entry_files or [])
    if not entries:
        return {
            "status": "unavailable",
            "reason": "unknown",
            "pass": False,
            "entry_files": [],
            "reachable_files": [],
            "disconnected_excluded": _sorted_display(view.get("included_files") or []),
            "metric_notes": ["absent_entry_unknown"],
            "provenance": {"root": root, "algorithm_version": ALGORITHM_NAME},
        }

    if view.get("status") == "unavailable":
        return {
            "status": "unavailable",
            "reason": view.get("reason") or "unavailable",
            "pass": False,
            "entry_files": entries,
            "reachable_files": [],
            "disconnected_excluded": [],
            "metric_notes": [str(view.get("reason") or "unavailable")],
            "provenance": {"root": root, "algorithm_version": ALGORITHM_NAME},
        }

    probe = reject_out_of_root(root, entries) if root else None
    if probe:
        return {
            "status": "unavailable",
            "reason": probe,
            "pass": False,
            "entry_files": entries,
            "reachable_files": [],
            "disconnected_excluded": [],
            "metric_notes": [probe],
            "provenance": {"root": root, "algorithm_version": ALGORITHM_NAME},
        }

    included = _sorted_display(view.get("included_files") or [])
    universe = {_identity_key(p): p for p in included}
    missing = [p for p in entries if _identity_key(p) not in universe]
    if missing:
        return {
            "status": "unavailable",
            "reason": "unknown",
            "pass": False,
            "entry_files": entries,
            "reachable_files": [],
            "disconnected_excluded": included,
            "metric_notes": ["absent_entry_unknown"],
            "missing_entries": missing,
            "provenance": {"root": root, "algorithm_version": ALGORITHM_NAME},
        }

    edges_key = "strict_edges" if edge_kind == "strict" else "observed_edges"
    adj = _adjacency(list(view.get(edges_key) or []))
    visited: set[str] = set()
    queue: deque[str] = deque()
    for entry in entries:
        key = _identity_key(entry)
        if key in visited:
            continue
        visited.add(key)
        queue.append(key)
    while queue:
        node = queue.popleft()
        for target in adj.get(node, ()):
            if target in universe and target not in visited:
                visited.add(target)
                queue.append(target)

    reachable = _sorted_display(universe[k] for k in visited if k in universe)
    excluded = [p for p in included if _identity_key(p) not in visited]
    return {
        "status": "ok" if view.get("status") in {"ok", "partial"} else str(view.get("status")),
        "reason": None,
        "pass": False,
        "entry_files": entries,
        "reachable_files": reachable,
        "disconnected_excluded": excluded,
        "metric_notes": [
            "same_file_universe_as_view",
            "disconnected_excluded_from_reach",
            "not_signature_break_analysis",
        ],
        "counts": {
            "entries": len(entries),
            "reachable": len(reachable),
            "excluded": len(excluded),
        },
        "provenance": {
            "root": root,
            "algorithm_version": ALGORITHM_NAME,
            "edge_kind": edge_kind,
            "included_set_hash": (view.get("provenance") or {}).get("included_set_hash"),
        },
    }


__all__ = ["ALGORITHM_NAME", "compute_entry_reach"]
