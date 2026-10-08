"""TOML constraint gate over a readonly analysis view (P4-S1).

Adapted from the sentrux rules-gate idea (MIT) onto campaign formulas.
Not a Rust port, not bitwise parity, no vendored crate.

13 recognized keys. Unknown keys / malformed thresholds = invalid_config
for the whole request. Missing required input = unavailable, never pass.
max_cc and no_god_files are always unavailable in v1 (keys stay valid).
No eval / no import of rule code.
"""
from __future__ import annotations

import math
import re
import tomllib
from typing import Any

from .analysis_view import _identity_key, _norm_slash, _sorted_display
from .stability import compute_stability


ALGORITHM_NAME = "rules-v1"
QUALITY_VERSION = "analysis_quality_v1"
PAYLOAD_VERSION = 1
MAX_TOML_BYTES = 64 * 1024
MAX_RULES = 128
EPS = 1e-9

CONSTRAINT_KEYS = (
    "min_quality",
    "min_modularity",
    "min_acyclicity",
    "min_depth",
    "min_equality",
    "min_redundancy",
    "max_coupling_score",
    "max_cycles",
    "max_cc",
    "max_file_lines",
    "max_fn_lines",
    "no_god_files",
    "max_upward_violations",
)
_KNOWN = frozenset(CONSTRAINT_KEYS)
_MIN_UNIT = frozenset({
    "min_quality",
    "min_modularity",
    "min_acyclicity",
    "min_depth",
    "min_equality",
    "min_redundancy",
    "max_coupling_score",
})
_MAX_INT = frozenset({
    "max_cycles",
    "max_cc",
    "max_file_lines",
    "max_fn_lines",
    "max_upward_violations",
})
_QUALITY_FACTORS = (
    "modularity",
    "acyclicity",
    "depth",
    "equality",
    "redundancy",
)
_ALLOWED_TOP = frozenset({"constraints", "layers", "boundaries"})


def _cfg_error(message: str, *, notes: list[str] | None = None) -> dict[str, Any]:
    extra = [message]
    if notes:
        extra.extend(notes)
    return {
        "version": PAYLOAD_VERSION,
        "status": "unavailable",
        "reason": "invalid_config",
        "pass": False,
        "aggregate": "invalid_config",
        "checks": [],
        "counts": {"passed": 0, "violated": 0, "unavailable": 0, "evaluated": 0},
        "metric_notes": extra,
        "truncated": False,
        "reasons": ["invalid_config", message],
        "provenance": {"algorithm_version": ALGORITHM_NAME, "quality_version": QUALITY_VERSION},
        "config": None,
    }


def _glob_to_re(pattern: str) -> re.Pattern[str]:
    norm = _norm_slash(pattern)
    out: list[str] = ["^"]
    i = 0
    n = len(norm)
    while i < n:
        if norm.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if norm.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        ch = norm[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def glob_match(path: str, pattern: str) -> bool:
    """Match a normalized slash path against a glob. No filesystem expansion."""
    return _glob_to_re(pattern).match(_norm_slash(path)) is not None


def recognized_keys() -> tuple[str, ...]:
    return CONSTRAINT_KEYS


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    num = float(value)
    if not math.isfinite(num):
        return None
    return num


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def parse_rules_toml(raw: str | bytes) -> dict[str, Any]:
    """Parse and validate a rules document. Does not evaluate a view."""
    if isinstance(raw, str):
        data = raw.encode("utf-8")
        text = raw
    else:
        data = raw
        text = raw.decode("utf-8")
    if len(data) > MAX_TOML_BYTES:
        return _cfg_error("toml_too_large", notes=[f"bytes={len(data)}"])
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return _cfg_error("toml_parse_error", notes=[type(exc).__name__])
    if not isinstance(doc, dict):
        return _cfg_error("toml_root_not_table")
    unknown_top = sorted(k for k in doc if k not in _ALLOWED_TOP)
    if unknown_top:
        return _cfg_error("unknown_top_level", notes=unknown_top)

    constraints_raw = doc.get("constraints", {})
    if constraints_raw is None:
        constraints_raw = {}
    if not isinstance(constraints_raw, dict):
        return _cfg_error("constraints_not_table")
    unknown = sorted(k for k in constraints_raw if k not in _KNOWN)
    if unknown:
        return _cfg_error("unknown_keys", notes=unknown)

    constraints: dict[str, Any] = {}
    for key, value in constraints_raw.items():
        if key in _MIN_UNIT:
            if not _is_num(value):
                return _cfg_error("malformed_threshold", notes=[key])
            num = float(value)
            if num < 0.0 - EPS or num > 1.0 + EPS:
                return _cfg_error("threshold_out_of_unit", notes=[key, str(value)])
            constraints[key] = num
        elif key in _MAX_INT:
            if not _is_num(value) or float(value) < 0 or float(value) != int(float(value)):
                return _cfg_error("malformed_threshold", notes=[key])
            constraints[key] = int(value)
        elif key == "no_god_files":
            flag = _as_bool(value)
            if flag is None:
                return _cfg_error("malformed_threshold", notes=[key])
            constraints[key] = flag
        else:
            return _cfg_error("unknown_keys", notes=[key])

    layers_raw = doc.get("layers") or []
    if not isinstance(layers_raw, list):
        return _cfg_error("layers_not_array")
    layers: list[dict[str, Any]] = []
    for idx, row in enumerate(layers_raw):
        if not isinstance(row, dict):
            return _cfg_error("layer_not_table", notes=[str(idx)])
        name = str(row.get("name") or "").strip()
        order = row.get("order")
        globs = row.get("globs") or []
        order_n = _as_float(order)
        if not name or order_n is None or order_n != int(order_n):
            return _cfg_error("malformed_layer", notes=[str(idx)])
        if not isinstance(globs, list) or not globs or not all(isinstance(g, str) and g.strip() for g in globs):
            return _cfg_error("malformed_layer_globs", notes=[name])
        layers.append({
            "name": name,
            "order": int(order_n),
            "globs": [_norm_slash(g) for g in globs],
        })

    bounds_raw = doc.get("boundaries") or []
    if not isinstance(bounds_raw, list):
        return _cfg_error("boundaries_not_array")
    boundaries: list[dict[str, Any]] = []
    for idx, row in enumerate(bounds_raw):
        if not isinstance(row, dict):
            return _cfg_error("boundary_not_table", notes=[str(idx)])
        frm = row.get("from")
        to = row.get("to")
        if not isinstance(frm, str) or not frm.strip() or not isinstance(to, str) or not to.strip():
            return _cfg_error("malformed_boundary", notes=[str(idx)])
        boundaries.append({
            "from": _norm_slash(frm),
            "to": _norm_slash(to),
            "reason": str(row.get("reason") or "denied"),
        })

    rule_count = len(constraints) + len(layers) + len(boundaries)
    if rule_count > MAX_RULES:
        return _cfg_error("too_many_rules", notes=[str(rule_count)])

    return {
        "status": "ok",
        "reason": None,
        "constraints": constraints,
        "layers": layers,
        "boundaries": boundaries,
        "rule_count": rule_count,
        "recognized_keys": list(CONSTRAINT_KEYS),
        "present_keys": sorted(constraints),
    }


def _pair_edges(view: dict[str, Any], *, edge_kind: str = "observed") -> list[tuple[str, str]]:
    key = "strict_edges" if edge_kind == "strict" else "observed_edges"
    universe = {_identity_key(p): _norm_slash(p) for p in (view.get("included_files") or [])}
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


def compute_sccs(nodes: list[str], pairs: list[tuple[str, str]]) -> list[list[str]]:
    """Iterative Kosaraju. Returns SCCs as lists of identity keys."""
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    radj: dict[str, list[str]] = {n: [] for n in nodes}
    for src, dst in pairs:
        if src in adj and dst in adj:
            adj[src].append(dst)
            radj[dst].append(src)
    seen: set[str] = set()
    order: list[str] = []
    for start in nodes:
        if start in seen:
            continue
        stack = [(start, 0)]
        while stack:
            node, state = stack.pop()
            if state == 0:
                if node in seen:
                    continue
                seen.add(node)
                stack.append((node, 1))
                for nxt in adj[node]:
                    if nxt not in seen:
                        stack.append((nxt, 0))
            else:
                order.append(node)
    assigned: set[str] = set()
    comps: list[list[str]] = []
    for start in reversed(order):
        if start in assigned:
            continue
        comp: list[str] = []
        stack = [start]
        assigned.add(start)
        while stack:
            node = stack.pop()
            comp.append(node)
            for nxt in radj[node]:
                if nxt not in assigned:
                    assigned.add(nxt)
                    stack.append(nxt)
        comps.append(comp)
    return comps


def cycle_group_count(nodes: list[str], pairs: list[tuple[str, str]]) -> int:
    """SCCs of size >= 2. Self-loops are dropped by the view, so size-1 is acyclic."""
    return sum(1 for comp in compute_sccs(nodes, pairs) if len(comp) >= 2)


def max_depth_of(nodes: list[str], pairs: list[tuple[str, str]]) -> int:
    """Longest path in the SCC condensation DAG (edges between components)."""
    comps = compute_sccs(nodes, pairs)
    if not comps:
        return 0
    index = {node: i for i, comp in enumerate(comps) for node in comp}
    dag: dict[int, set[int]] = {i: set() for i in range(len(comps))}
    for src, dst in pairs:
        a, b = index.get(src), index.get(dst)
        if a is None or b is None or a == b:
            continue
        dag[a].add(b)
    memo: dict[int, int] = {}

    def depth(i: int) -> int:
        if i in memo:
            return memo[i]
        kids = dag[i]
        memo[i] = 0 if not kids else 1 + max(depth(j) for j in kids)
        return memo[i]

    return max(depth(i) for i in dag) if dag else 0


def _geo_mean(values: list[float]) -> float:
    if any(v <= 0.0 for v in values):
        return 0.0
    acc = 1.0
    for v in values:
        acc *= v
    return acc ** (1.0 / len(values))


def _check(
    key: str,
    status: str,
    *,
    actual: Any = None,
    threshold: Any = None,
    reason: str | None = None,
    files: list[str] | None = None,
) -> dict[str, Any]:
    passed = status == "passed"
    return {
        "key": key,
        "status": status,
        "pass": passed,
        "actual": actual,
        "threshold": threshold,
        "reason": reason,
        "files": files or [],
    }


def _min_cmp(actual: float, threshold: float) -> str:
    return "passed" if actual + EPS >= threshold else "violated"


def _max_cmp(actual: float, threshold: float) -> str:
    return "passed" if actual <= threshold + EPS else "violated"


def _proxy(metrics: dict[str, Any], name: str) -> tuple[float | None, str | None]:
    raw = metrics.get(name)
    if isinstance(raw, dict):
        value = raw.get("value")
        proxy = str(raw.get("proxy") or "").strip()
        version = str(raw.get("version") or "").strip()
        num = _as_float(value)
        if not proxy or not version or num is None:
            return None, "proxy_unlabelled"
        if num < 0.0 - EPS or num > 1.0 + EPS:
            return None, "proxy_out_of_unit"
        return num, None
    if _is_num(raw):
        return None, "proxy_unlabelled"
    return None, "missing_input"


def _assign_layers(
    files: list[str],
    layers: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    assigned: dict[str, dict[str, Any]] = {}
    for path in files:
        hits: list[dict[str, Any]] = []
        for layer in layers:
            if any(glob_match(path, g) for g in layer["globs"]):
                hits.append(layer)
        if not hits:
            return None, "unknown_layer_match"
        orders = {h["order"] for h in hits}
        names = {h["name"] for h in hits}
        if len(orders) > 1 or len(names) > 1:
            return None, "ambiguous_layer_match"
        assigned[_identity_key(path)] = hits[0]
    return assigned, None


def evaluate_rules(
    view: dict[str, Any],
    toml_text: str | bytes,
    *,
    metrics: dict[str, Any] | None = None,
    edge_kind: str = "observed",
) -> dict[str, Any]:
    """Evaluate a parsed rules document against one analysis view."""
    parsed = parse_rules_toml(toml_text)
    if parsed.get("reason") == "invalid_config":
        return parsed
    metrics = dict(metrics or {})
    notes = [
        "campaign_formulas",
        "intentional_rust_divergence",
        "max_cc_unavailable_v1",
        "no_god_files_unavailable_v1",
        "no_legacy_score_change",
    ]
    root = str((view.get("provenance") or {}).get("root") or "")
    provenance = {
        "root": root,
        "algorithm_version": ALGORITHM_NAME,
        "quality_version": QUALITY_VERSION,
        "edge_kind": edge_kind,
        "included_set_hash": (view.get("provenance") or {}).get("included_set_hash"),
    }

    if view.get("status") == "unavailable":
        return {
            "version": PAYLOAD_VERSION,
            "status": "unavailable",
            "reason": view.get("reason") or "unavailable",
            "pass": False,
            "aggregate": "unavailable",
            "checks": [],
            "counts": {"passed": 0, "violated": 0, "unavailable": 0, "evaluated": 0},
            "metric_notes": notes + [str(view.get("reason") or "unavailable")],
            "truncated": bool(view.get("truncated")),
            "reasons": [str(view.get("reason") or "unavailable")],
            "provenance": provenance,
            "config": {
                "present_keys": parsed["present_keys"],
                "rule_count": parsed["rule_count"],
            },
        }

    included = _sorted_display(view.get("included_files") or [])
    tests = {_identity_key(p) for p in (view.get("test_files") or [])}
    production = [p for p in included if _identity_key(p) not in tests]
    nodes = [_identity_key(p) for p in production]
    pairs = _pair_edges(view, edge_kind=edge_kind)
    cycles = cycle_group_count(nodes, pairs)
    depth = max_depth_of(nodes, pairs)
    truncated = bool(view.get("truncated"))
    freshness = str(view.get("freshness") or (view.get("provenance") or {}).get("freshness") or "")
    checks: list[dict[str, Any]] = []
    constraints: dict[str, Any] = parsed["constraints"]

    if "min_quality" in constraints:
        factors_raw = metrics.get("quality_factors")
        factors: dict[str, float] = {}
        missing = False
        if not isinstance(factors_raw, dict):
            missing = True
        else:
            for name in _QUALITY_FACTORS:
                val = factors_raw.get(name)
                if not _is_num(val) or float(val) < 0.0 - EPS or float(val) > 1.0 + EPS:
                    missing = True
                    break
                factors[name] = float(val)
        if missing:
            checks.append(_check("min_quality", "unavailable", threshold=constraints["min_quality"], reason="missing_quality_factors"))
        elif not (view.get("provenance") or {}):
            checks.append(_check("min_quality", "unavailable", threshold=constraints["min_quality"], reason="missing_provenance"))
        elif freshness and freshness not in {"fresh", ""}:
            checks.append(_check("min_quality", "unavailable", threshold=constraints["min_quality"], reason="freshness:" + freshness))
        else:
            provided = metrics.get("analysis_quality_v1")
            if provided is None:
                actual = _geo_mean([factors[n] for n in _QUALITY_FACTORS])
            elif not _is_num(provided) or float(provided) < 0.0 - EPS or float(provided) > 1.0 + EPS:
                checks.append(_check("min_quality", "unavailable", threshold=constraints["min_quality"], reason="quality_out_of_unit"))
                actual = None
            else:
                actual = float(provided)
            if actual is not None:
                checks.append(_check(
                    "min_quality",
                    _min_cmp(actual, constraints["min_quality"]),
                    actual=actual,
                    threshold=constraints["min_quality"],
                ))

    if "min_modularity" in constraints:
        raw = metrics.get("modularity")
        if not _is_num(raw) or float(raw) < 0.0 - EPS or float(raw) > 1.0 + EPS:
            checks.append(_check("min_modularity", "unavailable", threshold=constraints["min_modularity"], reason="missing_modularity"))
        else:
            actual = float(raw)
            checks.append(_check("min_modularity", _min_cmp(actual, constraints["min_modularity"]), actual=actual, threshold=constraints["min_modularity"]))

    if "min_acyclicity" in constraints:
        if truncated:
            checks.append(_check("min_acyclicity", "unavailable", threshold=constraints["min_acyclicity"], reason="truncated"))
        else:
            actual = 1.0 if cycles == 0 else 0.0
            checks.append(_check(
                "min_acyclicity",
                _min_cmp(actual, constraints["min_acyclicity"]),
                actual=actual,
                threshold=constraints["min_acyclicity"],
                reason="acyclicity_v1_binary",
            ))

    if "min_depth" in constraints:
        raw = metrics.get("depth")
        if isinstance(raw, dict):
            value, err = _proxy({"depth": raw}, "depth")
            if err:
                checks.append(_check("min_depth", "unavailable", threshold=constraints["min_depth"], reason=err))
            else:
                checks.append(_check("min_depth", _min_cmp(value or 0.0, constraints["min_depth"]), actual=value, threshold=constraints["min_depth"]))
        elif _is_num(raw) and 0.0 - EPS <= float(raw) <= 1.0 + EPS:
            actual = float(raw)
            checks.append(_check("min_depth", _min_cmp(actual, constraints["min_depth"]), actual=actual, threshold=constraints["min_depth"]))
        else:
            checks.append(_check("min_depth", "unavailable", threshold=constraints["min_depth"], reason="depth_incomplete"))

    if "min_equality" in constraints:
        value, err = _proxy(metrics, "equality")
        if err:
            checks.append(_check("min_equality", "unavailable", threshold=constraints["min_equality"], reason=err))
        else:
            checks.append(_check("min_equality", _min_cmp(value or 0.0, constraints["min_equality"]), actual=value, threshold=constraints["min_equality"]))

    if "min_redundancy" in constraints:
        value, err = _proxy(metrics, "redundancy")
        if err:
            checks.append(_check("min_redundancy", "unavailable", threshold=constraints["min_redundancy"], reason=err))
        else:
            checks.append(_check("min_redundancy", _min_cmp(value or 0.0, constraints["min_redundancy"]), actual=value, threshold=constraints["min_redundancy"]))

    if "max_coupling_score" in constraints:
        stab = compute_stability(view, edge_kind=edge_kind)
        score = stab.get("coupling_score")
        if score is None or stab.get("status") == "unavailable":
            checks.append(_check(
                "max_coupling_score",
                "unavailable",
                threshold=constraints["max_coupling_score"],
                reason=str(stab.get("reason") or "missing_grouping"),
            ))
        else:
            checks.append(_check(
                "max_coupling_score",
                _max_cmp(float(score), constraints["max_coupling_score"]),
                actual=float(score),
                threshold=constraints["max_coupling_score"],
            ))

    if "max_cycles" in constraints:
        if truncated:
            checks.append(_check("max_cycles", "unavailable", threshold=constraints["max_cycles"], reason="count_incomplete"))
        else:
            checks.append(_check("max_cycles", _max_cmp(float(cycles), float(constraints["max_cycles"])), actual=cycles, threshold=constraints["max_cycles"]))

    if "max_cc" in constraints:
        checks.append(_check("max_cc", "unavailable", threshold=constraints["max_cc"], reason="missing_cc"))

    if "max_file_lines" in constraints:
        counts = metrics.get("file_line_counts")
        if not isinstance(counts, dict):
            checks.append(_check("max_file_lines", "unavailable", threshold=constraints["max_file_lines"], reason="missing_file_line_counts"))
        else:
            mapped = {_identity_key(k): int(v) for k, v in counts.items() if _is_num(v) and float(v) == int(float(v)) and float(v) >= 0}
            missing_files = [p for p in production if _identity_key(p) not in mapped]
            if missing_files:
                checks.append(_check(
                    "max_file_lines",
                    "unavailable",
                    threshold=constraints["max_file_lines"],
                    reason="missing_or_stale_file_lines",
                    files=_sorted_display(missing_files),
                ))
            else:
                offenders = [p for p in production if mapped[_identity_key(p)] > constraints["max_file_lines"]]
                status = "violated" if offenders else "passed"
                peak = max((mapped[_identity_key(p)] for p in production), default=0)
                checks.append(_check(
                    "max_file_lines",
                    status,
                    actual=peak,
                    threshold=constraints["max_file_lines"],
                    files=_sorted_display(offenders),
                ))

    if "max_fn_lines" in constraints:
        spans = metrics.get("fn_spans")
        if not isinstance(spans, list) or not spans:
            checks.append(_check("max_fn_lines", "unavailable", threshold=constraints["max_fn_lines"], reason="missing_fn_spans"))
        else:
            bad = False
            offenders: list[str] = []
            peak = 0
            for span in spans:
                if not isinstance(span, dict):
                    bad = True
                    break
                start = span.get("start_line")
                end = span.get("end_line")
                name = str(span.get("name") or span.get("file") or "")
                if not _is_num(start) or not _is_num(end) or int(end) < int(start):
                    bad = True
                    break
                length = int(end) - int(start) + 1
                peak = max(peak, length)
                if length > constraints["max_fn_lines"]:
                    offenders.append(name)
            if bad:
                checks.append(_check("max_fn_lines", "unavailable", threshold=constraints["max_fn_lines"], reason="invalid_fn_spans"))
            else:
                checks.append(_check(
                    "max_fn_lines",
                    "violated" if offenders else "passed",
                    actual=peak,
                    threshold=constraints["max_fn_lines"],
                    files=offenders,
                ))

    if "no_god_files" in constraints:
        checks.append(_check("no_god_files", "unavailable", threshold=constraints["no_god_files"], reason="god_detector_undefined"))

    if "max_upward_violations" in constraints:
        if not parsed["layers"]:
            checks.append(_check(
                "max_upward_violations",
                "unavailable",
                threshold=constraints["max_upward_violations"],
                reason="missing_layers",
            ))
        else:
            assigned, err = _assign_layers(production, parsed["layers"])
            if err or assigned is None:
                return _cfg_error(err or "unknown_layer_match")
            upward: list[str] = []
            for src, dst in pairs:
                a = assigned.get(src)
                b = assigned.get(dst)
                if a is None or b is None:
                    return _cfg_error("unknown_layer_match")
                if a["order"] < b["order"]:
                    upward.append(f"{a['name']}->{b['name']}")
            checks.append(_check(
                "max_upward_violations",
                _max_cmp(float(len(upward)), float(constraints["max_upward_violations"])),
                actual=len(upward),
                threshold=constraints["max_upward_violations"],
                files=upward,
            ))

    for idx, bound in enumerate(parsed["boundaries"]):
        hits: list[str] = []
        for src, dst in pairs:
            src_p = next((p for p in production if _identity_key(p) == src), src)
            dst_p = next((p for p in production if _identity_key(p) == dst), dst)
            if glob_match(src_p, bound["from"]) and glob_match(dst_p, bound["to"]):
                hits.append(f"{src_p}->{dst_p}")
        checks.append(_check(
            f"boundary:{idx}",
            "violated" if hits else "passed",
            actual=len(hits),
            threshold=0,
            reason=bound["reason"],
            files=hits,
        ))

    passed_n = sum(1 for c in checks if c["status"] == "passed")
    violated_n = sum(1 for c in checks if c["status"] == "violated")
    unavail_n = sum(1 for c in checks if c["status"] == "unavailable")
    if violated_n:
        aggregate = "violated"
    elif unavail_n:
        aggregate = "unavailable"
    else:
        aggregate = "passed"
    # P4: a partial analysis view must stay partial in the gate output --
    # never collapsed to "ok". Forward unmatched_files for traceability.
    view_partial = view.get("status") == "partial"
    view_reason = str(view.get("reason") or "unmatched_included_files")
    if view_partial:
        gate_status = "partial"
        gate_reason = view_reason
        gate_pass = False
        gate_reasons = [view_reason]
    else:
        gate_status = "ok" if aggregate == "passed" else "unavailable" if aggregate == "unavailable" else "ok"
        gate_reason = None if aggregate == "passed" else aggregate
        gate_pass = aggregate == "passed"
        gate_reasons = [] if aggregate == "passed" else [aggregate]
    return {
        "version": PAYLOAD_VERSION,
        "status": gate_status,
        "reason": gate_reason,
        "pass": gate_pass,
        "aggregate": aggregate,
        "checks": checks,
        "counts": {
            "passed": passed_n,
            "violated": violated_n,
            "unavailable": unavail_n,
            "evaluated": len(checks),
        },
        "cycles": cycles,
        "max_depth": depth,
        "metric_notes": notes + (["view_partial"] if view_partial else []),
        "truncated": truncated,
        "reasons": gate_reasons,
        "unmatched_files": list(view.get("unmatched_files") or []),
        "provenance": provenance,
        "config": {
            "present_keys": parsed["present_keys"],
            "rule_count": parsed["rule_count"],
            "recognized_keys": list(CONSTRAINT_KEYS),
        },
    }


__all__ = [
    "ALGORITHM_NAME",
    "CONSTRAINT_KEYS",
    "MAX_RULES",
    "MAX_TOML_BYTES",
    "QUALITY_VERSION",
    "compute_sccs",
    "cycle_group_count",
    "evaluate_rules",
    "glob_match",
    "max_depth_of",
    "parse_rules_toml",
    "recognized_keys",
]
