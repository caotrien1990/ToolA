"""Architectural cycle/level/quality signal over the CBM file dependency graph.

Ported from the sentrux root-cause model (MIT) onto toolA's existing graph DB —
no re-scan, no tree-sitter: every metric reads the `nodes`/`edges` tables the
indexer already produced.

Five root causes, each normalized to [0,1] where 1 is best, aggregated by a
geometric mean so no single dimension can be gamed:

  1. modularity  — Newman Q on the file dependency graph (Newman 2004)
  2. acyclicity  — 1/(1+cycles), cycles via iterative Tarjan SCC (Tarjan 1972)
  3. depth       — 1/(1+depth/8), longest dependency chain (Lakos 1996)
  4. equality    — 1-Gini of the symbol size distribution (Gini 1912)
  5. redundancy  — 1-(dead/total), dead symbols with no inbound call edge

Two of the five differ from the sentrux definition because toolA's extractor
stores no per-function cyclomatic complexity and no function body hash:
`equality` uses symbol line span as the complexity proxy, and `redundancy`
counts dead symbols only (no duplicate detection). Both deviations are reported
in the payload's `metric_notes` so the score is never mistaken for a sentrux
score. All graph algorithms are iterative — a deep dependency chain must not
overflow the Python stack.

Fail-open: every entry point returns a payload dict; nothing raises to callers.
"""
from __future__ import annotations

import time
from collections import deque

from .config import GRAPH_ENABLED
from . import store as _store

# Newman Q is bounded to [-0.5, 1.0]; rescale that range onto [0,1].
_Q_MIN = -0.5
_Q_SPAN = 1.5

# Depth midpoint: a chain of 8 files scores 0.5 (sentrux root_causes.rs).
_DEPTH_MIDPOINT = 8.0

# Geometric-mean floor: a zero dimension would annihilate the product, so each
# factor is clamped up to this before multiplying (sentrux uses the same floor).
_GEO_FLOOR = 0.01

# A call edge below this confidence is a weak suffix guess — not evidence that a
# symbol is alive. Same threshold dead_code_candidates uses in query.py.
_LIVE_CALL_CONFIDENCE = 0.7


# ── graph construction ────────────────────────────────────────────────────────

def _build_file_graph(conn) -> tuple[set[str], dict[str, list[str]], int]:
    """Read file→file dependency edges from the graph DB.

    `imports_module` is the only file-level edge kind the indexer emits: its
    source is an import node inside the importing file and its target is the
    imported module node, so both endpoints need a join back to `nodes` to
    recover file paths. Self-edges and duplicates are dropped — a module
    importing its own package says nothing about the dependency structure.

    Returns (nodes, adjacency, edge_count).
    """
    rows = conn.execute(
        "SELECT DISTINCT s.file_path AS src, t.file_path AS dst "
        "FROM edges e "
        "JOIN nodes s ON s.id = e.source "
        "JOIN nodes t ON t.id = e.target "
        "WHERE e.kind = 'imports_module' AND e.is_external = 0 "
        "  AND s.file_path <> t.file_path;"
    ).fetchall()

    nodes: set[str] = set()
    adj: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for r in rows:
        src, dst = r["src"], r["dst"]
        if not src or not dst or (src, dst) in seen:
            continue
        seen.add((src, dst))
        nodes.add(src)
        nodes.add(dst)
        adj.setdefault(src, []).append(dst)

    # Deterministic neighbour order keeps SCC ids and levels stable across runs.
    for targets in adj.values():
        targets.sort()
    return nodes, adj, len(seen)


# ── 1. cycles: iterative Tarjan SCC ───────────────────────────────────────────

def tarjan_scc(nodes: set[str], adj: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components with more than one member, i.e. real cycles.

    Iterative rather than recursive: a dependency chain deeper than Python's
    recursion limit is a realistic shape in a large codebase and must not crash
    the tool.
    """
    index_of: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    cycles: list[list[str]] = []

    for start in sorted(nodes):
        if start in index_of:
            continue
        # Each frame is (node, next-neighbour-index).
        work: list[list] = [[start, 0]]
        index_of[start] = lowlink[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)

        while work:
            node, ni = work[-1]
            neighbours = adj.get(node, ())
            if ni < len(neighbours):
                work[-1][1] += 1
                nxt = neighbours[ni]
                if nxt not in index_of:
                    index_of[nxt] = lowlink[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append([nxt, 0])
                elif nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[nxt])
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    cycles.append(sorted(component))

    cycles.sort(key=lambda c: (-len(c), c[0]))
    return cycles


# ── 2. levelization: Kahn over the SCC-collapsed DAG ──────────────────────────

def kahn_levels(
    nodes: set[str], adj: dict[str, list[str]], cycles: list[list[str]]
) -> tuple[dict[str, int], int]:
    """Assign each file a dependency level (Lakos 1996).

    Level 0 is a leaf that imports nothing else in the graph; level N imports
    something at level N-1. Cycles are collapsed to a single DAG vertex first,
    so every member of a cycle shares one level and the sort cannot deadlock.
    """
    if not nodes:
        return {}, 0

    scc_of: dict[str, int] = {}
    for cid, members in enumerate(cycles):
        for member in members:
            scc_of[member] = cid
    next_id = len(cycles)
    for node in sorted(nodes):
        if node not in scc_of:
            scc_of[node] = next_id
            next_id += 1

    # Collapsed DAG: edges point from dependent component to dependency.
    out_degree = [0] * next_id
    reverse: list[list[int]] = [[] for _ in range(next_id)]
    seen_pairs: set[tuple[int, int]] = set()
    for src, targets in adj.items():
        a = scc_of[src]
        for dst in targets:
            b = scc_of[dst]
            if a == b or (a, b) in seen_pairs:
                continue
            seen_pairs.add((a, b))
            out_degree[a] += 1
            reverse[b].append(a)

    levels = [0] * next_id
    remaining = list(out_degree)
    queue = deque(c for c in range(next_id) if remaining[c] == 0)
    max_level = 0
    while queue:
        comp = queue.popleft()
        for parent in reverse[comp]:
            candidate = levels[comp] + 1
            if candidate > levels[parent]:
                levels[parent] = candidate
                max_level = max(max_level, candidate)
            remaining[parent] -= 1
            if remaining[parent] == 0:
                queue.append(parent)

    return {node: levels[cid] for node, cid in scc_of.items()}, max_level


def upward_violations(adj: dict[str, list[str]], levels: dict[str, int]) -> int:
    """Count edges pointing from a lower level up to a higher one.

    A healthy layered design only depends downward; an upward edge means the
    layering is broken at that point.
    """
    count = 0
    for src, targets in adj.items():
        src_level = levels.get(src, 0)
        for dst in targets:
            if src_level < levels.get(dst, 0):
                count += 1
    return count


# ── 3. depth: iterative memoized longest path ─────────────────────────────────

def longest_path_depth(nodes: set[str], adj: dict[str, list[str]]) -> int:
    """Longest dependency chain, measured from roots (files nothing imports).

    Back-edges are skipped rather than followed, so a cyclic graph terminates
    with the longest acyclic chain instead of looping forever.
    """
    if not nodes:
        return 0

    has_incoming = {dst for targets in adj.values() for dst in targets}
    seeds = sorted(n for n in nodes if n not in has_incoming) or sorted(nodes)

    memo: dict[str, int] = {}
    node_cap = len(nodes)
    for start in seeds:
        if start in memo:
            continue
        on_path: set[str] = {start}
        work: list[list] = [[start, 0, 0]]  # node, neighbour index, best child
        while work:
            node, ni, best = work[-1]
            neighbours = adj.get(node, ())
            if ni < len(neighbours):
                work[-1][1] += 1
                nxt = neighbours[ni]
                if nxt in memo:
                    work[-1][2] = max(best, min(memo[nxt] + 1, node_cap))
                elif nxt not in on_path:
                    on_path.add(nxt)
                    work.append([nxt, 0, 0])
                continue

            work.pop()
            on_path.discard(node)
            memo[node] = best
            if work:
                work[-1][2] = max(work[-1][2], min(best + 1, node_cap))

    return max((memo.get(s, 0) for s in seeds), default=0)


# ── 4. modularity: Newman Q ───────────────────────────────────────────────────

def _module_of(path: str) -> str:
    """Directory that owns a file — the module boundary for clustering."""
    normalized = path.replace("\\", "/")
    head, sep, _ = normalized.rpartition("/")
    return head if sep else ""


def modularity_q(adj: dict[str, list[str]], nodes: set[str]) -> float:
    """Newman's directed modularity Q over directory-defined modules.

    Q = (1/m) * Σ [A_ij - k_out_i·k_in_j/m] · δ(module_i, module_j)

    Q above 0.3 indicates genuine modular structure; Q at or below 0 means the
    edges cluster no better than a random graph with the same degrees. Returns
    1.0 for an edgeless graph, which is trivially modular.
    """
    edges = [(src, dst) for src, targets in adj.items() for dst in targets]
    m = len(edges)
    if m == 0:
        return 1.0

    k_out: dict[str, int] = {}
    k_in: dict[str, int] = {}
    for src, dst in edges:
        k_out[src] = k_out.get(src, 0) + 1
        k_in[dst] = k_in.get(dst, 0) + 1

    intra = sum(1 for src, dst in edges if _module_of(src) == _module_of(dst))

    mod_out: dict[str, float] = {}
    mod_in: dict[str, float] = {}
    for node in nodes:
        mod = _module_of(node)
        mod_out[mod] = mod_out.get(mod, 0.0) + k_out.get(node, 0)
        mod_in[mod] = mod_in.get(mod, 0.0) + k_in.get(node, 0)

    expected = sum(out * mod_in.get(mod, 0.0) / m for mod, out in mod_out.items())
    q = (intra - expected) / m
    return max(_Q_MIN, min(1.0, q))


# ── 5. distribution helpers ───────────────────────────────────────────────────

def gini(values: list[float]) -> float:
    """Gini coefficient: 0 when every value is equal, 1 when one value holds all.

    Applied to symbol sizes it answers "is the complexity spread across the
    codebase, or concentrated in a few god files?".
    """
    if len(values) <= 1:
        return 0.0
    ordered = sorted(values)
    total = sum(ordered)
    if total <= 0:
        return 0.0
    n = len(ordered)
    numerator = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(ordered))
    return max(0.0, min(1.0, numerator / (n * total)))


def shannon_entropy(values: list[float]) -> float:
    """Shannon entropy normalized to [0,1] by log2(N). Diagnostic only."""
    n = len(values)
    if n <= 1:
        return 0.0
    total = sum(values)
    if total <= 0:
        return 0.0
    from math import log2
    h = -sum((v / total) * log2(v / total) for v in values if v > 0)
    max_h = log2(n)
    return h / max_h if max_h > 0 else 0.0


def _symbol_sizes(conn) -> list[float]:
    """Line span of every function/method/class — the complexity proxy."""
    rows = conn.execute(
        "SELECT start_line, end_line FROM nodes "
        "WHERE kind IN ('function','method','class');"
    ).fetchall()
    return [float(max(1, (r["end_line"] or 0) - (r["start_line"] or 0) + 1)) for r in rows]


def _dead_ratio(conn) -> tuple[float, int, int]:
    """Fraction of non-exported callables with no confident inbound call edge.

    Mirrors the conservative filter in query.dead_code_candidates: exported and
    decorated symbols are excluded because a framework may invoke them, and weak
    suffix-matched call edges do not count as proof of life.

    Returns (ratio, dead_count, total_callables).
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE kind IN ('function','method');"
    ).fetchone()[0]
    if not total:
        return 0.0, 0, 0
    dead = conn.execute(
        "SELECT COUNT(*) FROM nodes n WHERE n.kind IN ('function','method') "
        "AND n.is_exported = 0 AND (n.decorators IS NULL OR n.decorators IN ('','[]')) "
        "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.target = n.id "
        "                AND e.kind = 'calls' AND e.confidence >= ?);",
        (_LIVE_CALL_CONFIDENCE,),
    ).fetchone()[0]
    return min(1.0, dead / total), dead, total


# ── aggregation ───────────────────────────────────────────────────────────────

def root_cause_scores(
    *, modularity_raw: float, cycle_count: int, max_depth: int,
    complexity_gini: float, redundancy_ratio: float,
) -> tuple[dict[str, float], float]:
    """Normalize the five raw measures to [0,1] and take their geometric mean.

    The geometric mean is the point of the model: one collapsed dimension drags
    the whole score down, so a codebase cannot trade cycles away for modularity.
    """
    scores = {
        "modularity": max(0.0, min(1.0, (modularity_raw - _Q_MIN) / _Q_SPAN)),
        "acyclicity": 1.0 / (1.0 + cycle_count),
        "depth": 1.0 / (1.0 + max_depth / _DEPTH_MIDPOINT),
        "equality": max(0.0, min(1.0, 1.0 - complexity_gini)),
        "redundancy": max(0.0, min(1.0, 1.0 - redundancy_ratio)),
    }
    product = 1.0
    for value in scores.values():
        product *= max(value, _GEO_FLOOR)
    return scores, product ** (1.0 / len(scores))


# ── public entry point ────────────────────────────────────────────────────────

METRIC_NOTES = {
    "equality": "proxy: Gini over symbol line span — the extractor stores no "
                "per-function cyclomatic complexity",
    "redundancy": "partial: dead symbols only — no function body hashing, so "
                  "duplicate code is not counted",
    "scope": "file-level dependency graph built from imports_module edges",
}


def compute_cycle_point(project_path: str) -> dict:
    """Cycles, levelization, and the aggregate quality signal for a project.

    Read-only on both the source tree and the graph DB. Fail-open: a disabled
    graph, a missing DB, or any query error returns a payload carrying the
    reason rather than raising.
    """
    t0 = time.monotonic()
    payload: dict = {
        "enabled": GRAPH_ENABLED,
        "cycles": [], "cycle_count": 0, "largest_cycle": 0,
        "levels": {}, "max_level": 0, "max_depth": 0, "upward_violations": 0,
        "quality_signal": None, "root_causes": {}, "raw": {},
        "metric_notes": METRIC_NOTES,
        "files_analyzed": 0, "edges_analyzed": 0,
        "error": None, "elapsed_ms": 0,
    }
    if not GRAPH_ENABLED:
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return payload

    try:
        conn = _store.get_db(project_path, readonly=True)
        try:
            nodes, adj, edge_count = _build_file_graph(conn)
            payload["files_analyzed"] = len(nodes)
            payload["edges_analyzed"] = edge_count

            cycles = tarjan_scc(nodes, adj)
            levels, max_level = kahn_levels(nodes, adj, cycles)
            max_depth = longest_path_depth(nodes, adj)

            payload["cycles"] = cycles
            payload["cycle_count"] = len(cycles)
            payload["largest_cycle"] = max((len(c) for c in cycles), default=0)
            payload["levels"] = levels
            payload["max_level"] = max_level
            payload["max_depth"] = max_depth
            payload["upward_violations"] = upward_violations(adj, levels)

            q = modularity_q(adj, nodes)
            sizes = _symbol_sizes(conn)
            size_gini = gini(sizes)
            redundancy_ratio, dead_count, total_callables = _dead_ratio(conn)

            scores, signal = root_cause_scores(
                modularity_raw=q, cycle_count=len(cycles), max_depth=max_depth,
                complexity_gini=size_gini, redundancy_ratio=redundancy_ratio,
            )
            payload["root_causes"] = {k: round(v, 4) for k, v in scores.items()}
            payload["quality_signal"] = round(signal, 4)
            payload["raw"] = {
                "modularity_q": round(q, 4),
                "complexity_gini": round(size_gini, 4),
                "size_entropy": round(shannon_entropy(sizes), 4),
                "redundancy_ratio": round(redundancy_ratio, 4),
                "dead_symbols": dead_count,
                "total_callables": total_callables,
                "symbols_measured": len(sizes),
            }
        finally:
            conn.close()
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"

    payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return payload


__all__ = [
    "compute_cycle_point", "tarjan_scc", "kahn_levels", "longest_path_depth",
    "upward_violations", "modularity_q", "gini", "shannon_entropy",
    "root_cause_scores", "METRIC_NOTES",
]
