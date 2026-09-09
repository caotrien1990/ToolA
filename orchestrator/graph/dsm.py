"""Bounded DSM: ordering + sparse page + propagation + mutual clusters (P6-S2).

Adapted from the sentrux DSM idea (MIT) onto campaign formulas.
Not a Rust port, not bitwise parity, no vendored crate.

Exact propagation only for N<=500. Above that a deterministic sample
(step through ordered files, force-include highest out-degree). 2001 nodes
=> truncated (keep lowest+highest 1000 by level). Page <=100x100.
Never emit a dense 4M-cell JSON. UTF-8 labels stay intact.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Iterable

from .analysis_view import _identity_key, _norm_slash, _sorted_display
from .rules import compute_sccs


ALGORITHM_NAME = "dsm-v1"
PAYLOAD_VERSION = 1
MAX_NODES = 2000
MAX_PAGE = 100
EXACT_PROPAGATION = 500
_NOTES = [
    "campaign_formulas",
    "sparse_page_only",
    "exact_propagation_le_500",
    "mutual_edge_clusters",
]


def _unavailable(reason: str, *, notes: list[str] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": PAYLOAD_VERSION,
        "status": "unavailable",
        "reason": reason,
        "pass": False,
        "files": [],
        "size": 0,
        "total_files": 0,
        "edge_count": 0,
        "density": None,
        "propagation_cost": None,
        "propagation_mode": None,
        "above_diagonal": 0,
        "below_diagonal": 0,
        "same_level": 0,
        "clusters": [],
        "page": {"row_start": 0, "col_start": 0, "rows": 0, "cols": 0, "cells": []},
        "levels": {},
        "truncated": False,
        "sampled": False,
        "metric_notes": list(_NOTES) + list(notes or []),
        "provenance": {"algorithm_version": ALGORITHM_NAME},
    }
    if extra:
        payload.update(extra)
    return payload


def _as_pairs(edges: Iterable[Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in edges or []:
        if isinstance(raw, dict):
            src = raw.get("src") or raw.get("from") or raw.get("from_file")
            dst = raw.get("dst") or raw.get("to") or raw.get("to_file")
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            src, dst = raw[0], raw[1]
        else:
            continue
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        a, b = _norm_slash(src), _norm_slash(dst)
        if not a or not b or _identity_key(a) == _identity_key(b):
            continue
        item = (_identity_key(a), _identity_key(b))
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def compute_levels(nodes: list[str], pairs: list[tuple[str, str]]) -> dict[str, int]:
    """Level = longest path in the SCC condensation toward sinks.

    Isolated / sink files are 0. a->b->c => c=0, b=1, a=2.
    Cycle members share one condensation node so they get the same level.
    """
    if not nodes:
        return {}
    comps = compute_sccs(nodes, pairs)
    if not comps:
        return {n: 0 for n in nodes}
    index = {node: i for i, comp in enumerate(comps) for node in comp}
    dag: dict[int, set[int]] = {i: set() for i in range(len(comps))}
    for src, dst in pairs:
        a, b = index.get(src), index.get(dst)
        if a is None or b is None or a == b:
            continue
        dag[a].add(b)
    # Iterative longest-path toward sinks (avoids RecursionError on long chains).
    memo: dict[int, int] = {}
    visiting: set[int] = set()
    stack: list[tuple[int, bool]] = [(i, False) for i in range(len(comps) - 1, -1, -1)]
    while stack:
        i, seen = stack.pop()
        if i in memo:
            continue
        if seen:
            kids = dag[i]
            memo[i] = 0 if not kids else 1 + max(memo[j] for j in kids)
            visiting.discard(i)
            continue
        if i in visiting:
            memo[i] = 0
            continue
        visiting.add(i)
        stack.append((i, True))
        for j in dag[i]:
            if j not in memo:
                stack.append((j, False))

    levels: dict[str, int] = {}
    for i, comp in enumerate(comps):
        lv = memo.get(i, 0)
        for node in comp:
            levels[node] = lv
    for n in nodes:
        levels.setdefault(n, 0)
    return levels


def _order_files(display: dict[str, str], levels: dict[str, int]) -> list[str]:
    keys = list(display)
    keys.sort(key=lambda k: (levels.get(k, 0), display[k].casefold(), display[k]))
    return [display[k] for k in keys]


def _truncate_extremes(ordered: list[str]) -> tuple[list[str], bool]:
    if len(ordered) <= MAX_NODES:
        return ordered, False
    half = MAX_NODES // 2
    return ordered[:half] + ordered[-half:], True


def _page_cells(
    files: list[str],
    key_of: dict[str, str],
    adj: dict[str, set[str]],
    row_start: int,
    col_start: int,
    rows: int,
    cols: int,
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for ri in range(row_start, min(row_start + rows, len(files))):
        src_key = key_of[files[ri]]
        outs = adj.get(src_key, set())
        if not outs:
            continue
        for ci in range(col_start, min(col_start + cols, len(files))):
            if ri == ci:
                continue
            if key_of[files[ci]] in outs:
                cells.append({"r": ri, "c": ci, "src": files[ri], "dst": files[ci]})
    return cells


def _propagation(
    files: list[str],
    key_of: dict[str, str],
    adj: dict[str, set[str]],
) -> tuple[float, str, bool]:
    n = len(files)
    if n <= 1:
        return 0.0, "exact", False
    if n <= EXACT_PROPAGATION:
        sample = list(range(n))
        mode = "exact"
        sampled = False
    else:
        step = n / EXACT_PROPAGATION
        sample = [int(i * step) for i in range(EXACT_PROPAGATION)]
        degrees = [len(adj.get(key_of[f], set())) for f in files]
        max_idx = max(range(n), key=lambda i: (degrees[i], -i))
        if max_idx not in sample:
            sample[-1] = max_idx
        sample = sorted(set(sample))
        mode = "sampled"
        sampled = True

    total = 0
    for start in sample:
        start_key = key_of[files[start]]
        seen = {start_key}
        q = deque([start_key])
        reach = 0
        while q:
            cur = q.popleft()
            for nxt in adj.get(cur, set()):
                if nxt in seen:
                    continue
                seen.add(nxt)
                reach += 1
                q.append(nxt)
        total += reach
    cost = total / (len(sample) * (n - 1))
    return cost, mode, sampled


def _clusters(
    files: list[str],
    key_of: dict[str, str],
    adj: dict[str, set[str]],
    levels: dict[str, int],
) -> list[dict[str, Any]]:
    keys = [key_of[f] for f in files]
    key_set = set(keys)
    undirected: dict[str, set[str]] = {k: set() for k in keys}
    mutual = 0
    for a in keys:
        for b in adj.get(a, set()):
            if b not in key_set or b <= a:
                continue
            if a in adj.get(b, set()):
                undirected[a].add(b)
                undirected[b].add(a)
                mutual += 1
    if mutual == 0:
        return []
    seen: set[str] = set()
    groups: list[list[str]] = []
    for start in keys:
        if start in seen or not undirected[start]:
            continue
        comp = []
        q = deque([start])
        seen.add(start)
        while q:
            cur = q.popleft()
            comp.append(cur)
            for nxt in sorted(undirected[cur]):
                if nxt in seen:
                    continue
                seen.add(nxt)
                q.append(nxt)
        if len(comp) >= 2:
            groups.append(comp)
    display = {key_of[f]: f for f in files}
    out = []
    for group in groups:
        names = _sorted_display(display[k] for k in group)
        internal = 0
        gset = set(group)
        for a in group:
            for b in adj.get(a, set()):
                if b in gset:
                    internal += 1
        out.append(
            {
                "files": names,
                "internal_edges": internal,
                "level": min(levels.get(k, 0) for k in group),
            }
        )
    out.sort(key=lambda c: (-int(c["internal_edges"]), [f.casefold() for f in c["files"]]))
    return out


def build_dsm(
    nodes: Iterable[str] | None = None,
    edges: Iterable[Any] | None = None,
    *,
    row_start: int = 0,
    col_start: int = 0,
    page_rows: int = MAX_PAGE,
    page_cols: int = MAX_PAGE,
    view: dict[str, Any] | None = None,
    edge_kind: str = "observed",
) -> dict[str, Any]:
    """Public kernel. Sparse page only — never a dense N×N matrix."""
    if view is not None:
        if not isinstance(view, dict) or view.get("status") not in {"ok", "partial"}:
            return _unavailable("unavailable_view")
        nodes = view.get("included_files") or []
        key = "strict_edges" if edge_kind == "strict" else "observed_edges"
        edges = view.get(key) or []
    display: dict[str, str] = {}
    for raw in nodes or []:
        path = _norm_slash(raw)
        if not path:
            continue
        display.setdefault(_identity_key(path), path)
    pairs = _as_pairs(edges or [])
    for a, b in pairs:
        display.setdefault(a, a)
        display.setdefault(b, b)
    if not display:
        empty = _unavailable("empty")
        empty["status"] = "ok"
        empty["reason"] = None
        empty["pass"] = True
        empty["propagation_cost"] = 0.0
        empty["propagation_mode"] = "exact"
        empty["density"] = 0.0
        return empty

    keys = list(display)
    levels = compute_levels(keys, pairs)
    ordered = _order_files(display, levels)
    total_files = len(ordered)
    ordered, truncated = _truncate_extremes(ordered)
    kept_keys = {_identity_key(f) for f in ordered}
    key_of = {_identity_key(f): _identity_key(f) for f in ordered}
    # map display -> identity
    key_of = {f: _identity_key(f) for f in ordered}

    adj: dict[str, set[str]] = {k: set() for k in kept_keys}
    edge_count = 0
    above = below = same = 0
    for src, dst in pairs:
        if src not in kept_keys or dst not in kept_keys or src == dst:
            continue
        if dst in adj[src]:
            continue
        adj[src].add(dst)
        edge_count += 1
        ls, ld = levels.get(src, 0), levels.get(dst, 0)
        if ls > ld:
            below += 1
        elif ls < ld:
            above += 1
        else:
            same += 1

    size = len(ordered)
    possible = size * (size - 1) if size > 1 else 1
    density = edge_count / possible if size > 1 else 0.0
    cost, mode, sampled = _propagation(ordered, key_of, adj)
    clusters = _clusters(ordered, key_of, adj, levels)

    try:
        rs = max(0, int(row_start))
        cs = max(0, int(col_start))
        pr = min(MAX_PAGE, max(0, int(page_rows)))
        pc = min(MAX_PAGE, max(0, int(page_cols)))
    except (TypeError, ValueError):
        return _unavailable("invalid_page")
    if pr * pc > MAX_PAGE * MAX_PAGE:
        return _unavailable("cap_page")
    cells = _page_cells(ordered, key_of, adj, rs, cs, pr, pc)

    notes = list(_NOTES)
    if truncated:
        notes.append("truncated_2000")
    if sampled:
        notes.append("propagation_sampled")

    status = "ok"
    reason = None
    if truncated or sampled:
        status = "partial"
        reason = "truncated" if truncated else "sampled"

    return {
        "version": PAYLOAD_VERSION,
        "status": status,
        "reason": reason,
        "pass": True,
        "files": ordered,
        "size": size,
        "total_files": total_files,
        "edge_count": edge_count,
        "density": density,
        "propagation_cost": cost,
        "propagation_mode": mode,
        "above_diagonal": above,
        "below_diagonal": below,
        "same_level": same,
        "clusters": clusters,
        "page": {
            "row_start": rs,
            "col_start": cs,
            "rows": min(pr, max(0, size - rs)),
            "cols": min(pc, max(0, size - cs)),
            "cells": cells,
        },
        "levels": {display[k]: levels.get(k, 0) for k in kept_keys if k in display},
        "truncated": truncated,
        "sampled": sampled,
        "metric_notes": notes,
        "provenance": {"algorithm_version": ALGORITHM_NAME},
    }
