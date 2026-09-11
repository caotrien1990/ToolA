"""Bounded RAM what-if over a cloned analysis_view (P5-S1).

Adapted from the sentrux what-if idea (MIT) onto campaign formulas.
Not a Rust port, not bitwise parity, no vendored crate, no I/O/DB/source mutation.

Validate the WHOLE sequence on a private clone first. Invalid step => reject
the entire request (no partial publish, no cache). Recompute only NEW scoped
algorithms (cycles/depth/stability), never legacy compute_cycle_point.

break_cycle: choose the SCC group by sorted tuple of normalized file paths;
inside the group pick the candidate edge with smallest target fan-in, then
source path, then target path (lexical normalized). Remove one edge / op.
Heuristic, not minimum-cut.

Caps: 20 ops / 2000 nodes / 50000 pairs / 2s. Over budget => unavailable.
"""
from __future__ import annotations

import copy
import time
from typing import Any

from .analysis_view import _identity_key, _norm_slash, _sorted_display
from .rules import cycle_group_count, compute_sccs, max_depth_of
from .stability import compute_stability


ALGORITHM_NAME = "whatif-v1"
PAYLOAD_VERSION = 1
MAX_OPS = 20
MAX_NODES = 2000
MAX_PAIRS = 50000
DEADLINE_S = 2.0
OPS = ("move_file", "add_edge", "remove_edge", "remove_file", "break_cycle")


def _unavailable(reason: str, *, notes: list[str] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "version": PAYLOAD_VERSION,
        "status": "unavailable",
        "reason": reason,
        "pass": False,
        "applied": False,
        "ops_applied": 0,
        "before": None,
        "after": None,
        "diff": {},
        "steps": [],
        "metric_notes": ["campaign_formulas", "ram_clone_only", "no_legacy_recompute"] + list(notes or []),
        "truncated": False,
        "provenance": {"algorithm_version": ALGORITHM_NAME},
        "original_unchanged": True,
    }
    if extra:
        payload.update(extra)
    return payload


def _clone_view(view: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(view)


def _edge_kind_key(edge_kind: str) -> str:
    return "strict_edges" if edge_kind == "strict" else "observed_edges"


def _as_op(raw: Any) -> dict[str, Any] | str:
    if not isinstance(raw, dict):
        return "op_not_object"
    op = str(raw.get("op") or raw.get("action") or "").strip()
    if op not in OPS:
        return "unknown_op"
    out = {"op": op}
    if op == "move_file":
        old = raw.get("from") or raw.get("old_path") or raw.get("src")
        new = raw.get("to") or raw.get("new_path") or raw.get("dst")
        if not isinstance(old, str) or not old.strip() or not isinstance(new, str) or not new.strip():
            return "invalid_id"
        out["from"] = _norm_slash(old)
        out["to"] = _norm_slash(new)
        if not out["from"] or not out["to"] or _identity_key(out["from"]) == _identity_key(out["to"]):
            return "invalid_id"
    elif op == "add_edge" or op == "remove_edge":
        src = raw.get("from") or raw.get("src")
        dst = raw.get("to") or raw.get("dst")
        if not isinstance(src, str) or not src.strip() or not isinstance(dst, str) or not dst.strip():
            return "invalid_id"
        out["from"] = _norm_slash(src)
        out["to"] = _norm_slash(dst)
        if not out["from"] or not out["to"]:
            return "invalid_id"
    elif op == "remove_file":
        path = raw.get("path") or raw.get("file")
        if not isinstance(path, str) or not path.strip():
            return "invalid_id"
        out["path"] = _norm_slash(path)
    elif op == "break_cycle":
        files = raw.get("files")
        if files is None:
            out["files"] = None
        elif isinstance(files, list) and all(isinstance(x, str) and x.strip() for x in files):
            out["files"] = _sorted_display(files)
        else:
            return "invalid_id"
    return out


def _universe(view: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in view.get("included_files") or []:
        display = _norm_slash(raw)
        if display:
            out.setdefault(_identity_key(display), display)
    return out


def _has_file(view: dict[str, Any], path: str) -> bool:
    return _identity_key(path) in _universe(view)


def _pair_list(view: dict[str, Any], *, edge_kind: str) -> list[tuple[str, str]]:
    key = _edge_kind_key(edge_kind)
    universe = _universe(view)
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for edge in view.get(key) or []:
        src = _identity_key(edge.get("src") or "")
        dst = _identity_key(edge.get("dst") or "")
        if src not in universe or dst not in universe or src == dst:
            continue
        item = (src, dst)
        if item in seen:
            continue
        seen.add(item)
        pairs.append(item)
    return pairs


def _rewrite_path(value: Any, old_k: str, new_display: str) -> Any:
    if isinstance(value, str) and _identity_key(value) == old_k:
        return new_display
    return value


def _rewrite_edge_list(edges: list[Any], old_k: str, new_display: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        row = dict(edge)
        row["src"] = _rewrite_path(row.get("src"), old_k, new_display)
        row["dst"] = _rewrite_path(row.get("dst"), old_k, new_display)
        out.append(row)
    return out


def _drop_file_from_list(values: list[Any], path_k: str) -> list[str]:
    return [v for v in values or [] if _identity_key(str(v)) != path_k]


def _drop_edges_touching(edges: list[Any], path_k: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        src = _identity_key(edge.get("src") or "")
        dst = _identity_key(edge.get("dst") or "")
        if src == path_k or dst == path_k:
            continue
        out.append(dict(edge))
    return out


def _drop_edge(edges: list[Any], src_k: str, dst_k: str) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    removed = 0
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        if _identity_key(edge.get("src") or "") == src_k and _identity_key(edge.get("dst") or "") == dst_k:
            removed += 1
            continue
        out.append(dict(edge))
    return out, removed


def _add_edge(edges: list[Any], src: str, dst: str) -> list[dict[str, Any]]:
    out = [dict(e) for e in (edges or []) if isinstance(e, dict)]
    src_k = _identity_key(src)
    dst_k = _identity_key(dst)
    for edge in out:
        if _identity_key(edge.get("src") or "") == src_k and _identity_key(edge.get("dst") or "") == dst_k:
            return out
    out.append({"src": src, "dst": dst, "confidence": 1.0})
    return out


def _fan_in(pairs: list[tuple[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _src, dst in pairs:
        counts[dst] = counts.get(dst, 0) + 1
    return counts


def choose_cycle_group(nodes: list[str], pairs: list[tuple[str, str]], wanted: list[str] | None) -> list[str] | None:
    """Pick one SCC size>=2. Explicit files must be a subset of that group."""
    comps = [sorted(c) for c in compute_sccs(nodes, pairs) if len(c) >= 2]
    if not comps:
        return None
    if wanted:
        want = {_identity_key(p) for p in wanted}
        hits = [c for c in comps if want.issubset(set(c))]
        if len(hits) != 1:
            return None
        return hits[0]
    comps.sort(key=lambda c: tuple(c))
    return comps[0]


def choose_break_edge(_nodes: list[str], pairs: list[tuple[str, str]], group: list[str]) -> tuple[str, str] | None:
    group_set = set(group)
    fan = _fan_in(pairs)
    candidates = [(src, dst) for src, dst in pairs if src in group_set and dst in group_set]
    if not candidates:
        return None
    candidates.sort(key=lambda e: (fan.get(e[1], 0), e[0], e[1]))
    return candidates[0]


def _metrics(view: dict[str, Any], *, edge_kind: str) -> dict[str, Any]:
    universe = _universe(view)
    nodes = list(universe.keys())
    pairs = _pair_list(view, edge_kind=edge_kind)
    stab = compute_stability(view, edge_kind=edge_kind)
    return {
        "nodes": len(nodes),
        "pairs": len(pairs),
        "cycles": cycle_group_count(nodes, pairs),
        "max_depth": max_depth_of(nodes, pairs),
        "coupling_score": stab.get("coupling_score"),
        "stability_status": stab.get("status"),
    }


def _apply_one(clone: dict[str, Any], op: dict[str, Any], *, edge_kind: str) -> dict[str, Any] | str:
    kind = op["op"]
    if kind == "move_file":
        old = op["from"]
        new = op["to"]
        old_k = _identity_key(old)
        if not _has_file(clone, old):
            return "invalid_id"
        if _has_file(clone, new) and _identity_key(new) != old_k:
            return "invalid_id"
        clone["included_files"] = [
            new if _identity_key(str(p)) == old_k else p for p in (clone.get("included_files") or [])
        ]
        clone["test_files"] = [
            new if _identity_key(str(p)) == old_k else p for p in (clone.get("test_files") or [])
        ]
        clone["isolated_files"] = [
            new if _identity_key(str(p)) == old_k else p for p in (clone.get("isolated_files") or [])
        ]
        clone["observed_edges"] = _rewrite_edge_list(list(clone.get("observed_edges") or []), old_k, new)
        clone["strict_edges"] = _rewrite_edge_list(list(clone.get("strict_edges") or []), old_k, new)
        return {"op": "move_file", "from": old, "to": new}
    if kind == "add_edge":
        src, dst = op["from"], op["to"]
        if not _has_file(clone, src) or not _has_file(clone, dst):
            return "invalid_id"
        if _identity_key(src) == _identity_key(dst):
            return "invalid_id"
        clone["observed_edges"] = _add_edge(list(clone.get("observed_edges") or []), src, dst)
        clone["strict_edges"] = _add_edge(list(clone.get("strict_edges") or []), src, dst)
        return {"op": "add_edge", "from": src, "to": dst}
    if kind == "remove_edge":
        src, dst = op["from"], op["to"]
        src_k, dst_k = _identity_key(src), _identity_key(dst)
        obs, n_obs = _drop_edge(list(clone.get("observed_edges") or []), src_k, dst_k)
        strict, n_strict = _drop_edge(list(clone.get("strict_edges") or []), src_k, dst_k)
        if n_obs == 0 and n_strict == 0:
            return "invalid_id"
        clone["observed_edges"] = obs
        clone["strict_edges"] = strict
        return {"op": "remove_edge", "from": src, "to": dst}
    if kind == "remove_file":
        path = op["path"]
        path_k = _identity_key(path)
        if not _has_file(clone, path):
            return "invalid_id"
        clone["included_files"] = _drop_file_from_list(list(clone.get("included_files") or []), path_k)
        clone["test_files"] = _drop_file_from_list(list(clone.get("test_files") or []), path_k)
        clone["isolated_files"] = _drop_file_from_list(list(clone.get("isolated_files") or []), path_k)
        clone["observed_edges"] = _drop_edges_touching(list(clone.get("observed_edges") or []), path_k)
        clone["strict_edges"] = _drop_edges_touching(list(clone.get("strict_edges") or []), path_k)
        return {"op": "remove_file", "path": path}
    if kind == "break_cycle":
        universe = _universe(clone)
        nodes = list(universe.keys())
        pairs = _pair_list(clone, edge_kind=edge_kind)
        group = choose_cycle_group(nodes, pairs, op.get("files"))
        if not group:
            return "invalid_id"
        picked = choose_break_edge(nodes, pairs, group)
        if not picked:
            return "invalid_id"
        src_k, dst_k = picked
        src = universe.get(src_k, src_k)
        dst = universe.get(dst_k, dst_k)
        clone["observed_edges"], _ = _drop_edge(list(clone.get("observed_edges") or []), src_k, dst_k)
        clone["strict_edges"], _ = _drop_edge(list(clone.get("strict_edges") or []), src_k, dst_k)
        return {
            "op": "break_cycle",
            "from": src,
            "to": dst,
            "group": [universe.get(n, n) for n in group],
            "heuristic": "min_target_fan_in_then_src_then_dst",
        }
    return "unknown_op"


def simulate_sequence(
    view: dict[str, Any],
    ops: list[Any] | None,
    *,
    edge_kind: str = "observed",
    now: float | None = None,
) -> dict[str, Any]:
    """Apply ops on a private clone. Original view is never mutated."""
    started = time.monotonic() if now is None else now
    if not isinstance(view, dict) or view.get("status") == "unavailable":
        return _unavailable(str((view or {}).get("reason") or "unavailable_view"))
    raw_ops = list(ops or [])
    if len(raw_ops) > MAX_OPS:
        return _unavailable("cap_ops", notes=[f"ops={len(raw_ops)}"], extra={"cap": MAX_OPS})
    parsed: list[dict[str, Any]] = []
    for raw in raw_ops:
        item = _as_op(raw)
        if isinstance(item, str):
            return _unavailable(item, extra={"ops_applied": 0, "applied": False})
        parsed.append(item)

    universe = _universe(view)
    pairs0 = _pair_list(view, edge_kind=edge_kind)
    if len(universe) > MAX_NODES:
        return _unavailable("cap_nodes", notes=[f"nodes={len(universe)}"], extra={"cap": MAX_NODES})
    if len(pairs0) > MAX_PAIRS:
        return _unavailable("cap_pairs", notes=[f"pairs={len(pairs0)}"], extra={"cap": MAX_PAIRS})

    original_fingerprint = {
        "included_files": list(view.get("included_files") or []),
        "observed_edges": copy.deepcopy(view.get("observed_edges") or []),
        "strict_edges": copy.deepcopy(view.get("strict_edges") or []),
    }
    clone = _clone_view(view)
    steps: list[dict[str, Any]] = []
    for op in parsed:
        if time.monotonic() - started > DEADLINE_S:
            return _unavailable("deadline", extra={"ops_applied": 0, "applied": False})
        applied = _apply_one(clone, op, edge_kind=edge_kind)
        if isinstance(applied, str):
            return _unavailable(applied, extra={"ops_applied": 0, "applied": False, "failed_op": op})
        steps.append(applied)
        nodes_n = len(_universe(clone))
        pairs_n = len(_pair_list(clone, edge_kind=edge_kind))
        if nodes_n > MAX_NODES:
            return _unavailable("cap_nodes", extra={"ops_applied": 0, "applied": False})
        if pairs_n > MAX_PAIRS:
            return _unavailable("cap_pairs", extra={"ops_applied": 0, "applied": False})
        if time.monotonic() - started > DEADLINE_S:
            return _unavailable("deadline", extra={"ops_applied": 0, "applied": False})

    before = _metrics(view, edge_kind=edge_kind)
    after = _metrics(clone, edge_kind=edge_kind)
    unchanged = (
        list(view.get("included_files") or []) == original_fingerprint["included_files"]
        and view.get("observed_edges") == original_fingerprint["observed_edges"]
        and view.get("strict_edges") == original_fingerprint["strict_edges"]
    )
    return {
        "version": PAYLOAD_VERSION,
        "status": "ok",
        "reason": None,
        "pass": True,
        "applied": True,
        "ops_applied": len(steps),
        "steps": steps,
        "before": before,
        "after": after,
        "diff": {
            "cycles": after["cycles"] - before["cycles"],
            "max_depth": after["max_depth"] - before["max_depth"],
            "nodes": after["nodes"] - before["nodes"],
            "pairs": after["pairs"] - before["pairs"],
        },
        "clone": {
            "included_files": list(clone.get("included_files") or []),
            "observed_edges": list(clone.get("observed_edges") or []),
            "strict_edges": list(clone.get("strict_edges") or []),
        },
        "metric_notes": [
            "campaign_formulas",
            "ram_clone_only",
            "no_legacy_recompute",
            "break_cycle_heuristic_not_mincut",
        ],
        "truncated": False,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "provenance": {
            "algorithm_version": ALGORITHM_NAME,
            "edge_kind": edge_kind,
            "root": (view.get("provenance") or {}).get("root"),
            "included_set_hash": (view.get("provenance") or {}).get("included_set_hash"),
        },
        "original_unchanged": unchanged,
        "caps": {"ops": MAX_OPS, "nodes": MAX_NODES, "pairs": MAX_PAIRS, "deadline_s": DEADLINE_S},
    }
