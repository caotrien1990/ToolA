from __future__ import annotations

import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..storage.jsonl_store import append_jsonl as _append_jsonl
from ..text_lines import split_lines

UNKNOWN = "unknown"
DEFAULT_MIN_SAMPLES = 5
COST_RANKS = {
    "deepseek-v4-pro": 1,
    "ltnai/deepseek-v4-pro": 1,
    "minimax-m3": 2,
    "ltnai/minimax-m3": 2,
    "qwen3.7-max": 3,
    "openai-compatible/qwen3.7-max": 3,
    "kimi-k3": 4,
    "gpt-5.6-sol": 5,
}
_METADATA_KEYS = (
    "TASK_ID", "OBJECTIVE_CLASS", "RISK_LEVEL", "DEV_LEVEL", "LOAD_BUDGET_SOURCE",
    "INDEPENDENT_QUESTIONS", "FILES_SUBSYSTEMS", "EXPECTED_TOOL_CALLS", "EXPECTED_WRITES",
    "DEPENDENCY_DEPTH", "EXPECTED_END_MARKER",
)


def parse_dispatch_metadata(prompt: str) -> dict[str, Any]:
    """Parse explicit leader metadata. Missing values stay unknown; never infer risk/dev."""
    text = str(prompt or "")
    out: dict[str, Any] = {key.lower(): UNKNOWN for key in _METADATA_KEYS}
    for key in _METADATA_KEYS:
        match = re.search(rf"(?im)^\s*{re.escape(key)}\s*:\s*(.+?)\s*$", text)
        if match:
            value = match.group(1).strip()
            out[key.lower()] = value if value else UNKNOWN
    for key in ("independent_questions", "files_subsystems", "expected_tool_calls", "expected_writes", "dependency_depth"):
        value = out[key]
        if value != UNKNOWN:
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                out[key] = UNKNOWN
    out["prompt_bytes"] = len(text.encode("utf-8"))
    return out


def validate_artifact(path: str, expected_end_marker: str = UNKNOWN) -> dict[str, Any]:
    if not path:
        return {"artifact_expected": False, "artifact_exists": False, "artifact_nonzero": False,
                "end_marker_expected": expected_end_marker != UNKNOWN, "end_marker_present": False,
                "artifact_valid": UNKNOWN, "artifact_bytes": 0}
    target = Path(path)
    exists = target.is_file()
    size = target.stat().st_size if exists else 0
    marker_expected = bool(expected_end_marker and expected_end_marker != UNKNOWN)
    marker_present = False
    if exists and size and marker_expected:
        try:
            marker_present = target.read_text(encoding="utf-8", errors="replace").rstrip().endswith(expected_end_marker)
        except OSError:
            marker_present = False
    valid = bool(exists and size and (marker_present if marker_expected else True))
    return {"artifact_expected": True, "artifact_exists": exists, "artifact_nonzero": size > 0,
            "end_marker_expected": marker_expected, "end_marker_present": marker_present,
            "artifact_valid": valid, "artifact_bytes": size}


def _tests_reported(text: str) -> bool:
    return bool(re.search(r"(?im)\b(pytest|tests?\s+(?:pass|passed|green)|\d+\s+passed|npm\s+test|pnpm\s+test)\b", text or ""))


def _done_marker(text: str) -> bool:
    return bool(re.search(r"(?im)^\s*(?:DONE|PASS|COMPLETED|END_[A-Z0-9_]+)\b", text or ""))


def build_measurement(item: dict[str, Any], result: dict[str, Any], *, reply_event_id: str = "", now: float | None = None) -> dict[str, Any]:
    import time
    finished_at = float(now if now is not None else time.time())
    meta = dict(item.get("measurement_metadata") or parse_dispatch_metadata(str(item.get("text") or "")))
    reply = str(result.get("reply") or "")
    marker = str(meta.get("expected_end_marker") or UNKNOWN)
    artifact = validate_artifact(str(item.get("artifact_path") or ""), marker)
    created = float(item.get("created_at") or finished_at)
    started = float(item.get("started_at") or created)
    duration = max(0.0, finished_at - started)
    response_tokens = result.get("response_tokens")
    try:
        response_tokens = int(response_tokens) if response_tokens not in (None, "") else UNKNOWN
    except (TypeError, ValueError):
        response_tokens = UNKNOWN
    transport_ok = bool(result.get("ok"))
    tests_reported = _tests_reported(reply)
    done_marker = _done_marker(reply)
    quality_verified = bool(transport_ok and artifact.get("artifact_valid") is True and tests_reported and done_marker)
    model = str(item.get("model") or UNKNOWN)
    cost_rank = COST_RANKS.get(model.lower(), UNKNOWN)
    output_bytes = len(reply.encode("utf-8"))
    compactness = round(min(1.0, 1200 / max(output_bytes, 1)), 4)
    quality_proxy = round((0.25 * transport_ok) + (0.30 * (artifact.get("artifact_valid") is True)) +
                          (0.25 * tests_reported) + (0.15 * done_marker) + (0.05 * compactness), 4)
    utility = round(quality_proxy / cost_rank, 4) if isinstance(cost_rank, int) and cost_rank > 0 else UNKNOWN
    return {
        "schema_version": 1, "request_id": item.get("request_id", UNKNOWN), "reply_event_id": reply_event_id or UNKNOWN,
        "task_id": meta.get("task_id", UNKNOWN), "objective_class": meta.get("objective_class", UNKNOWN),
        "risk_level": meta.get("risk_level", UNKNOWN), "dev_level": meta.get("dev_level", UNKNOWN),
        "load_budget_source": meta.get("load_budget_source", UNKNOWN),
        "route": {"harness": item.get("target", UNKNOWN), "runtime_key": item.get("runtime_key", UNKNOWN),
                  "provider": item.get("provider", UNKNOWN), "model": model},
        "load": {key: meta.get(key, UNKNOWN) for key in (
            "independent_questions", "files_subsystems", "expected_tool_calls", "expected_writes", "dependency_depth", "prompt_bytes")},
        "timing": {"created_at": created, "started_at": started, "finished_at": finished_at, "duration_sec": round(duration, 3)},
        "outcome": {"transport_ok": transport_ok, "error": str(result.get("error") or "")[:500],
                    "timeout_kind": str(result.get("timeout_kind") or ""), "completion": "completed" if transport_ok else "failed"},
        "artifact": artifact,
        "quality": {"done_marker": done_marker, "tests_reported": tests_reported,
                    "quality_verified": quality_verified, "quality_proxy": quality_proxy,
                    "compactness_ratio": compactness},
        "cost": {"response_tokens": response_tokens, "output_bytes": output_bytes,
                 "cost_rank": cost_rank, "estimated_cost": UNKNOWN, "quality_per_cost": utility},
    }


def append_measurement(path: str | Path, measurement: dict[str, Any]) -> None:
    """Append one measurement under the shared JSONL lock.

    This used a plain ``open(path, "a")``. That is safe for exactly one writer, and
    it is safe today only by accident: the council drain that calls it is serialised
    per agent by ``_COUNCIL_AGENT_QUEUE_DRAINING``. Nothing in the signature says so,
    and the failure mode if a second writer ever arrives is the worst kind --
    measured with 4 processes x 4 threads x 60 appends on the plain form, 280 of 480
    lines survived, one line had its leading brace overwritten, and there were ZERO
    exceptions with exit code 0. Silent loss, so a scorecard built from these rows
    would simply have been quietly wrong.

    ``append_jsonl`` already solves this properly -- a sidecar lock, so a concurrent
    ``read_tail`` is not denied; ``fsync`` outside the critical section; and a
    ``TimeoutError`` instead of a dropped line -- so this routes to it rather than
    growing a second lock implementation.
    """
    _append_jsonl(path, measurement)


def load_measurements(path: str | Path, limit: int = 0) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        return []
    rows = []
    # One row per "\n" (append_jsonl): ensure_ascii=False keeps a raw U+2028/U+0085
    # INSIDE a row, and str.splitlines() dropped it as two undecodable halves.
    for line in split_lines(target.read_text(encoding="utf-8", errors="replace")):
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows[-limit:] if limit and limit > 0 else rows


def _percentile(values: list[float], percentile: float) -> float | str:
    if not values:
        return UNKNOWN
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 3)


def build_scorecard(rows: Iterable[dict[str, Any]], min_samples: int = DEFAULT_MIN_SAMPLES) -> dict[str, Any]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        route = row.get("route") or {}
        key = (str(route.get("harness", UNKNOWN)), str(route.get("model", UNKNOWN)),
               str(row.get("objective_class", UNKNOWN)), str(row.get("risk_level", UNKNOWN)),
               str(row.get("dev_level", UNKNOWN)))
        groups[key].append(row)
    buckets = []
    for key, samples in sorted(groups.items()):
        count = len(samples)
        durations = [float(d) for s in samples
                     if isinstance((d := (s.get("timing") or {}).get("duration_sec")), (int, float))]
        completion = [bool((s.get("outcome") or {}).get("transport_ok")) for s in samples]
        timeouts = [bool((s.get("outcome") or {}).get("timeout_kind")) for s in samples]
        artifact_expected = [s for s in samples if (s.get("artifact") or {}).get("artifact_expected")]
        artifacts_valid = [bool((s.get("artifact") or {}).get("artifact_valid")) for s in artifact_expected]
        quality = [float((s.get("quality") or {}).get("quality_proxy", 0)) for s in samples]
        sufficient = count >= max(1, int(min_samples)) and UNKNOWN not in key[2:]
        rate = lambda vals: round(sum(vals) / len(vals), 4) if vals else UNKNOWN
        completion_rate = rate(completion)
        quality_mean = round(statistics.fmean(quality), 4) if quality else UNKNOWN
        recommendation = "insufficient_evidence"
        if sufficient and isinstance(completion_rate, float) and isinstance(quality_mean, float):
            recommendation = "preferred" if completion_rate >= 0.9 and quality_mean >= 0.75 else (
                "conditional" if completion_rate >= 0.7 and quality_mean >= 0.55 else "avoid")
        buckets.append({
            "harness": key[0], "model": key[1], "objective_class": key[2], "risk_level": key[3], "dev_level": key[4],
            "samples": count, "completion_rate": completion_rate, "timeout_rate": rate(timeouts),
            "artifact_valid_rate": rate(artifacts_valid), "duration_p50_sec": _percentile(durations, 0.5),
            "duration_p95_sec": _percentile(durations, 0.95), "quality_proxy_mean": quality_mean,
            "confidence": "sufficient" if sufficient else "insufficient", "recommendation": recommendation,
            "numeric_capacity": "eligible_for_controlled_estimation" if sufficient else UNKNOWN,
        })
    return {"schema_version": 1, "measurement_count": sum(len(v) for v in groups.values()),
            "min_homogeneous_samples": int(min_samples), "buckets": buckets,
            "capacity_policy": "Never publish numeric task capacity from heterogeneous or insufficient samples."}


def write_scorecard_files(scorecard: dict[str, Any], json_path: str | Path, markdown_path: str | Path) -> None:
    jp, mp = Path(json_path), Path(markdown_path)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Council route scorecard", "", f"Measurements: {scorecard.get('measurement_count', 0)}",
             f"Minimum homogeneous samples: {scorecard.get('min_homogeneous_samples', DEFAULT_MIN_SAMPLES)}", "",
             "| Harness | Model | Objective | Risk | Dev | N | Complete | Timeout | Artifact | p50 | p95 | Quality | Confidence | Recommendation | Capacity |",
             "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|"]
    for b in scorecard.get("buckets", []):
        lines.append("| " + " | ".join(str(b.get(k, UNKNOWN)) for k in (
            "harness", "model", "objective_class", "risk_level", "dev_level", "samples", "completion_rate",
            "timeout_rate", "artifact_valid_rate", "duration_p50_sec", "duration_p95_sec", "quality_proxy_mean",
            "confidence", "recommendation", "numeric_capacity")) + " |")
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")