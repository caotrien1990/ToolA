"""File-level stability cascade, coupling, cohesion (P3-S2).

Campaign formulas (Addendum A), intentional Rust divergence:
- module = FILE
- I = Ce/(Ca+Ce); Ca+Ce==0 => I=0
- foundation: I<=0.15 AND Ca>=3 always (isolated never stable)
- cascade: nonempty outgoing set, every target already stable; sync rounds
- cyclic nonfoundation cannot bootstrap itself
- coupling: cross-folder pairs whose dest FILE is not stable / ALL selected
  imports_module pairs (intra included). N=0 => 0 + empty_graph
- cohesion: production only, declared folder groups, distinct directed
  intra-folder imports_module pairs; min(1, E_intra/(n-1)); n<=1 => 1 + trivial_group
"""
from __future__ import annotations

from typing import Any, Iterable

from .analysis_view import _identity_key, _norm_slash, _sorted_display


ALGORITHM_NAME = "stability-v1"
FOUNDATION_I_MAX = 0.15
FOUNDATION_CA_MIN = 3


def _folder_of(path: str) -> str:
    norm = _norm_slash(path)
    if "/" not in norm:
        return "."
    return norm.rsplit("/", 1)[0]


def declared_folder_groups(files: Iterable[str]) -> dict[str, str]:
    """Explicit group id = folder(src). Missing/overlap is the caller's problem."""
    groups: dict[str, str] = {}
    for raw in files:
        display = _norm_slash(raw)
        if not display:
            continue
        groups[_identity_key(display)] = _folder_of(display)
    return groups


def _pair_sets(edges: list[dict[str, Any]], universe: dict[str, str]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for edge in edges:
        src_k = _identity_key(edge.get("src") or "")
        dst_k = _identity_key(edge.get("dst") or "")
        if src_k not in universe or dst_k not in universe:
            continue
        if src_k == dst_k:
            continue
        key = (src_k, dst_k)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


def _ca_ce(pairs: list[tuple[str, str]], universe: dict[str, str]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {
        k: {"ca": set(), "ce": set()} for k in universe
    }
    for src, dst in pairs:
        stats[dst]["ca"].add(src)
        stats[src]["ce"].add(dst)
    out: dict[str, dict[str, Any]] = {}
    for key, display in universe.items():
        ca = len(stats[key]["ca"])
        ce = len(stats[key]["ce"])
        denom = ca + ce
        instability = 0.0 if denom == 0 else ce / float(denom)
        out[key] = {
            "file": display,
            "ca": ca,
            "ce": ce,
            "instability": instability,
            "outgoing": sorted(stats[key]["ce"]),
        }
    return out


def _foundation(stats: dict[str, dict[str, Any]]) -> set[str]:
    seeds: set[str] = set()
    for key, row in stats.items():
        if row["instability"] <= FOUNDATION_I_MAX and row["ca"] >= FOUNDATION_CA_MIN:
            seeds.add(key)
    return seeds


def _cascade(stats: dict[str, dict[str, Any]], seeds: set[str]) -> tuple[set[str], int]:
    stable = set(seeds)
    rounds = 0
    while True:
        added: set[str] = set()
        for key, row in stats.items():
            if key in stable:
                continue
            outgoing = list(row["outgoing"])
            if not outgoing:
                continue
            if all(target in stable for target in outgoing):
                added.add(key)
        if not added:
            break
        stable.update(added)
        rounds += 1
    return stable, rounds


def compute_stability(
    view: dict[str, Any],
    *,
    folder_groups: dict[str, str] | None = None,
    edge_kind: str = "observed",
) -> dict[str, Any]:
    """Pure metric over an analysis_view. Isolated files never become stable."""
    root = str((view.get("provenance") or {}).get("root") or "")
    if view.get("status") == "unavailable":
        return {
            "status": "unavailable",
            "reason": view.get("reason") or "unavailable",
            "pass": False,
            "stable_files": [],
            "foundation_files": [],
            "coupling_score": None,
            "cohesion": {},
            "metric_notes": [str(view.get("reason") or "unavailable")],
            "provenance": {"root": root, "algorithm_version": ALGORITHM_NAME, "edge_kind": edge_kind},
        }

    included = _sorted_display(view.get("included_files") or [])
    tests = {_identity_key(p) for p in (view.get("test_files") or [])}
    production = { _identity_key(p): p for p in included if _identity_key(p) not in tests }
    if folder_groups is None:
        groups = declared_folder_groups(production.values())
    else:
        groups = {}
        seen_group: dict[str, str] = {}
        for raw, group in folder_groups.items():
            key = _identity_key(raw)
            g = str(group or "")
            if not g:
                return {
                    "status": "unavailable",
                    "reason": "config_ambiguous",
                    "pass": False,
                    "stable_files": [],
                    "foundation_files": [],
                    "coupling_score": None,
                    "cohesion": {},
                    "metric_notes": ["config_ambiguous"],
                    "provenance": {"root": root, "algorithm_version": ALGORITHM_NAME},
                }
            prev = seen_group.get(key)
            if prev is not None and prev != g:
                return {
                    "status": "unavailable",
                    "reason": "config_ambiguous",
                    "pass": False,
                    "stable_files": [],
                    "foundation_files": [],
                    "coupling_score": None,
                    "cohesion": {},
                    "metric_notes": ["config_ambiguous"],
                    "provenance": {"root": root, "algorithm_version": ALGORITHM_NAME},
                }
            seen_group[key] = g
            groups[key] = g
        for key in production:
            if key not in groups:
                return {
                    "status": "unavailable",
                    "reason": "config_ambiguous",
                    "pass": False,
                    "stable_files": [],
                    "foundation_files": [],
                    "coupling_score": None,
                    "cohesion": {},
                    "metric_notes": ["config_ambiguous"],
                    "provenance": {"root": root, "algorithm_version": ALGORITHM_NAME},
                }

    edges_key = "strict_edges" if edge_kind == "strict" else "observed_edges"
    edges = list(view.get(edges_key) or [])
    pairs = _pair_sets(edges, production)
    stats = _ca_ce(pairs, production)
    foundation = _foundation(stats)
    stable, rounds = _cascade(stats, foundation)

    notes = [
        "file_level_modules",
        "isolated_never_stable",
        "cascade_requires_nonempty_outgoing",
        "intentional_rust_divergence",
        "no_legacy_score_change",
    ]

    denom = len(pairs)
    if denom == 0:
        coupling = 0.0
        notes.append("empty_graph")
        numerator = 0
    else:
        numerator = 0
        for src, dst in pairs:
            if groups.get(src) != groups.get(dst) and dst not in stable:
                numerator += 1
        coupling = numerator / float(denom)

    by_group: dict[str, list[str]] = {}
    for key, display in production.items():
        by_group.setdefault(groups[key], []).append(display)

    cohesion: dict[str, Any] = {}
    intra_pairs: dict[str, int] = {g: 0 for g in by_group}
    for src, dst in pairs:
        gs, gd = groups.get(src), groups.get(dst)
        if gs and gs == gd:
            intra_pairs[gs] = intra_pairs.get(gs, 0) + 1
    for group, files in by_group.items():
        n = len(files)
        e_intra = intra_pairs.get(group, 0)
        if n <= 1:
            cohesion[group] = {
                "n": n,
                "intra_pairs": e_intra,
                "score": 1.0,
                "note": "trivial_group",
            }
        else:
            cohesion[group] = {
                "n": n,
                "intra_pairs": e_intra,
                "score": min(1.0, e_intra / float(n - 1)),
                "note": None,
            }

    files_detail = []
    for key in sorted(production, key=lambda k: production[k].casefold()):
        row = stats[key]
        files_detail.append(
            {
                "file": production[key],
                "folder": groups[key],
                "ca": row["ca"],
                "ce": row["ce"],
                "instability": row["instability"],
                "foundation": key in foundation,
                "stable": key in stable,
            }
        )

    return {
        "status": "ok" if view.get("status") in {"ok", "partial"} else str(view.get("status")),
        "reason": None,
        "pass": False,
        "stable_files": _sorted_display(production[k] for k in stable),
        "foundation_files": _sorted_display(production[k] for k in foundation),
        "unstable_files": _sorted_display(production[k] for k in production if k not in stable),
        "files": files_detail,
        "coupling_score": coupling,
        "coupling_numerator": numerator,
        "coupling_denominator": denom,
        "cohesion": cohesion,
        "cascade_rounds": rounds,
        "edge_kind": edge_kind,
        "metric_notes": notes,
        "counts": {
            "production_files": len(production),
            "stable": len(stable),
            "foundation": len(foundation),
            "pairs": denom,
        },
        "provenance": {
            "root": root,
            "algorithm_version": ALGORITHM_NAME,
            "edge_kind": edge_kind,
            "folder_groups": {production[k]: groups[k] for k in production},
            "foundation_i_max": FOUNDATION_I_MAX,
            "foundation_ca_min": FOUNDATION_CA_MIN,
            "included_set_hash": (view.get("provenance") or {}).get("included_set_hash"),
        },
    }


def compare_strict_observed(view: dict[str, Any]) -> dict[str, Any]:
    """Two independent values; do not force them inside an arbitrary delta."""
    observed = compute_stability(view, edge_kind="observed")
    strict = compute_stability(view, edge_kind="strict")
    return {
        "observed": {
            "stable_count": len(observed.get("stable_files") or []),
            "coupling_score": observed.get("coupling_score"),
        },
        "strict": {
            "stable_count": len(strict.get("stable_files") or []),
            "coupling_score": strict.get("coupling_score"),
        },
        "same": (
            set(observed.get("stable_files") or []) == set(strict.get("stable_files") or [])
            and observed.get("coupling_score") == strict.get("coupling_score")
        ),
    }


__all__ = [
    "ALGORITHM_NAME",
    "FOUNDATION_CA_MIN",
    "FOUNDATION_I_MAX",
    "compare_strict_observed",
    "compute_stability",
    "declared_folder_groups",
]
