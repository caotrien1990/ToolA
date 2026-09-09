"""Immutable baseline compare (P4-S2).

Readonly. No auto save. Compares raw unrounded metric values with
tolerance 1e-9. Default degraded when quality drops >.02, coupling
rises >.02, or cycles increase. Missing compared value => unavailable,
not better. Root/scope/version/policy must match or the compare is
unavailable. A projected scoped score must not replace a legacy score.
"""
from __future__ import annotations

import json
import math
from typing import Any

from .rules import ALGORITHM_NAME as RULES_ALGO, QUALITY_VERSION


ALGORITHM_NAME = "baseline-v1"
PAYLOAD_VERSION = 1
EPS = 1e-9
QUALITY_DELTA = 0.02
COUPLING_DELTA = 0.02
REQUIRED_IDENTITY = (
    "root",
    "included_set_hash",
    "algorithm_version",
    "extractor_version",
    "schema_version",
    "confidence_policy",
)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    num = float(value)
    if not math.isfinite(num):
        return None
    return num


def _norm_policy(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _identity_of(blob: dict[str, Any]) -> dict[str, Any]:
    raw_prov = blob.get("provenance")
    raw_ident = blob.get("identity")
    prov: dict[str, Any] = raw_prov if isinstance(raw_prov, dict) else {}
    ident: dict[str, Any] = raw_ident if isinstance(raw_ident, dict) else {}
    out: dict[str, Any] = {}
    for key in REQUIRED_IDENTITY:
        if key in ident:
            out[key] = ident.get(key)
        elif key in blob:
            out[key] = blob.get(key)
        else:
            out[key] = prov.get(key)
    return out


def _metrics_of(blob: dict[str, Any]) -> dict[str, Any]:
    raw = blob.get("metrics")
    if isinstance(raw, dict):
        return raw
    return {
        "analysis_quality_v1": blob.get("analysis_quality_v1", blob.get("quality")),
        "coupling_score": blob.get("coupling_score", blob.get("coupling")),
        "cycles": blob.get("cycles"),
    }


def _legacy_present(blob: dict[str, Any]) -> bool:
    return "quality_signal" in blob or "legacy_quality_signal" in blob


def load_baseline(raw: str | bytes | dict[str, Any]) -> dict[str, Any]:
    """Parse a caller-chosen baseline document. Does not write."""
    if isinstance(raw, dict):
        if not raw:
            return {"status": "unavailable", "reason": "missing_baseline", "pass": False}
        return {"status": "ok", "reason": None, "document": raw}
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if not text.strip():
        return {"status": "unavailable", "reason": "missing_baseline", "pass": False}
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return {"status": "unavailable", "reason": "malformed_baseline", "pass": False}
    if not isinstance(doc, dict):
        return {"status": "unavailable", "reason": "malformed_baseline", "pass": False}
    return {"status": "ok", "reason": None, "document": doc}


def compare_baseline(
    current: dict[str, Any],
    baseline_raw: str | bytes | dict[str, Any] | None,
    *,
    quality_delta: float = QUALITY_DELTA,
    coupling_delta: float = COUPLING_DELTA,
) -> dict[str, Any]:
    """Compare a current snapshot against a caller-chosen baseline."""
    notes = [
        "raw_unrounded",
        "tolerance_1e-9",
        "quality_drop_gt_0.02_degraded",
        "coupling_rise_gt_0.02_degraded",
        "cycles_increase_degraded",
        "missing_compared_value_unavailable",
        "no_auto_save",
        "no_legacy_score_replace",
    ]
    if baseline_raw is None:
        loaded = {"status": "unavailable", "reason": "missing_baseline", "pass": False}
    else:
        loaded = load_baseline(baseline_raw)
    if loaded.get("status") != "ok":
        return {
            "version": PAYLOAD_VERSION,
            "status": "unavailable",
            "reason": loaded.get("reason") or "missing_baseline",
            "pass": False,
            "regression": False,
            "degraded": False,
            "identity_match": False,
            "deltas": {},
            "metric_notes": notes + [str(loaded.get("reason") or "missing_baseline")],
            "provenance": {"algorithm_version": ALGORITHM_NAME, "quality_version": QUALITY_VERSION},
            "baseline_bytes_unchanged": True,
        }

    baseline = loaded["document"]
    cur_id = _identity_of(current)
    base_id = _identity_of(baseline)
    mismatches = []
    for key in REQUIRED_IDENTITY:
        if _norm_policy(cur_id.get(key)) != _norm_policy(base_id.get(key)):
            mismatches.append(key)
    if mismatches:
        return {
            "version": PAYLOAD_VERSION,
            "status": "unavailable",
            "reason": "identity_mismatch",
            "pass": False,
            "regression": False,
            "degraded": False,
            "identity_match": False,
            "mismatched_fields": mismatches,
            "deltas": {},
            "metric_notes": notes + ["identity_mismatch"],
            "provenance": {"algorithm_version": ALGORITHM_NAME, "quality_version": QUALITY_VERSION},
            "baseline_bytes_unchanged": True,
        }

    if _legacy_present(current) and current.get("analysis_quality_v1") == current.get("quality_signal"):
        return {
            "version": PAYLOAD_VERSION,
            "status": "unavailable",
            "reason": "legacy_score_replaced",
            "pass": False,
            "regression": False,
            "degraded": False,
            "identity_match": True,
            "deltas": {},
            "metric_notes": notes + ["legacy_score_replaced"],
            "provenance": {"algorithm_version": ALGORITHM_NAME},
            "baseline_bytes_unchanged": True,
        }

    cur_m = _metrics_of(current)
    base_m = _metrics_of(baseline)
    checks: list[dict[str, Any]] = []
    degraded = False
    unavailable = False

    def add(name: str, cur_v: Any, base_v: Any, *, worse: str) -> None:
        nonlocal degraded, unavailable
        c = _as_float(cur_v)
        b = _as_float(base_v)
        if c is None or b is None:
            unavailable = True
            checks.append({
                "metric": name,
                "status": "unavailable",
                "current": cur_v,
                "baseline": base_v,
                "delta": None,
                "reason": "missing_compared_value",
            })
            return
        delta = c - b
        if abs(delta) <= EPS:
            status = "identical"
        elif worse == "drop" and (b - c) > quality_delta + EPS:
            status = "degraded"
            degraded = True
        elif worse == "rise" and (c - b) > coupling_delta + EPS:
            status = "degraded"
            degraded = True
        elif worse == "int_rise" and (c - b) > EPS:
            status = "degraded"
            degraded = True
        else:
            status = "ok"
        checks.append({
            "metric": name,
            "status": status,
            "current": c,
            "baseline": b,
            "delta": delta,
            "reason": None,
        })

    add("analysis_quality_v1", cur_m.get("analysis_quality_v1"), base_m.get("analysis_quality_v1"), worse="drop")
    add("coupling_score", cur_m.get("coupling_score"), base_m.get("coupling_score"), worse="rise")
    add("cycles", cur_m.get("cycles"), base_m.get("cycles"), worse="int_rise")

    if unavailable:
        aggregate = "unavailable"
    elif degraded:
        aggregate = "degraded"
    else:
        aggregate = "passed"
    identical = all(c["status"] == "identical" for c in checks) and not unavailable
    return {
        "version": PAYLOAD_VERSION,
        "status": "ok" if aggregate == "passed" else "unavailable" if aggregate == "unavailable" else "ok",
        "reason": None if aggregate == "passed" else aggregate,
        "pass": aggregate == "passed",
        "regression": degraded,
        "degraded": degraded,
        "identical": identical,
        "identity_match": True,
        "checks": checks,
        "deltas": {c["metric"]: c["delta"] for c in checks},
        "metric_notes": notes,
        "provenance": {
            "algorithm_version": ALGORITHM_NAME,
            "quality_version": QUALITY_VERSION,
            "rules_algorithm": RULES_ALGO,
            "root": cur_id.get("root"),
            "included_set_hash": cur_id.get("included_set_hash"),
        },
        "baseline_bytes_unchanged": True,
    }


__all__ = [
    "ALGORITHM_NAME",
    "COUPLING_DELTA",
    "QUALITY_DELTA",
    "compare_baseline",
    "load_baseline",
]
