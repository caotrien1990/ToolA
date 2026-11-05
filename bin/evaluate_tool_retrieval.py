"""Offline Recall@8 gate. Inputs must be independently labelled audit tasks.

JSONL rows: {"task": "...", "expected_tools": ["..."], "audit_id": "...",
             "label_source": "reviewer or pre-existing standard"}
Run from the repo with: python bin/evaluate_tool_retrieval.py dataset.jsonl
A small fixture may exercise the harness but cannot pass its release gate.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import code_tools as ct
import tools_v15 as hints


def load_tasks(path):
    tasks = []
    seen = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not all(row.get(k) for k in ("task", "expected_tools", "audit_id", "label_source")):
            raise ValueError(f"row {number} needs task, independent labels and audit provenance")
        if not isinstance(row["expected_tools"], list) or not all(
                isinstance(name, str) and name in ct.TOOL_MAP for name in row["expected_tools"]):
            raise ValueError(f"row {number} references missing tools or malformed labels")
        normalized = " ".join(row["task"].casefold().split())
        if normalized not in seen:
            seen.add(normalized)
            tasks.append(row)
    return tasks


def old_candidates(response):
    names = [response.get("recommended_tool")]
    for field in ("alternates", "candidates"):
        names.extend(row.get("tool") for row in response.get(field, ()))
    return list(dict.fromkeys(name for name in names if name))[:8]


async def evaluate(rows):
    baseline = 0
    candidate = 0
    misses = []
    for row in rows:
        old = json.loads(await hints._legacy_decision_hint(row["task"], use_memory=False))
        new = hints._rank_hint(row["task"], old)
        expected = set(row["expected_tools"])
        baseline += len(expected.intersection(old_candidates(old))) / len(expected)
        recall = len(expected.intersection(r["tool"] for r in new)) / len(expected)
        candidate += recall
        if recall < 1:
            misses.append({"audit_id": row["audit_id"], "expected": sorted(expected),
                           "ranked": [r["tool"] for r in new]})
    count = len(rows)
    baseline = baseline / count if count else None
    candidate = candidate / count if count else None
    enough = count >= 500
    regression = baseline - candidate if count else None
    return {"tasks": count, "minimum_release_tasks": 500, "baseline_recall_at_8": baseline,
            "candidate_recall_at_8": candidate, "regression": regression,
            "release_pass": enough and regression <= 0.02 and candidate >= 0.95,
            "embedding_condition": (candidate < 0.95 if enough else None),
            "status": "measured" if enough else "insufficient_independent_tasks",
            "misses": misses}


def sample_hints(path):
    # Selection uses provenance only. A human supplies usefulness verdicts.
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]
    eligible = [r for r in rows if r.get("audit_id") and isinstance(r.get("response"), dict)
                and any(r["response"].get(k) for k in ct.AUTO_CHAIN_FIELDS)]
    unique = {row["audit_id"]: row for row in eligible}
    selected = sorted(unique.values(), key=lambda r: hashlib.sha256(
        str(r["audit_id"]).encode()).hexdigest())[:50]
    return {"count": len(selected), "required": 50, "review_required": True,
            "samples": [{"audit_id": r["audit_id"], "tool": r.get("tool"),
                         "response": r["response"], "usefulness": None} for r in selected]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument("--sample-hints", action="store_true")
    args = parser.parse_args()
    source = Path(args.dataset)
    if args.sample_hints:
        report = sample_hints(source)
        passed = report["count"] == 50
    else:
        report = asyncio.run(evaluate(load_tasks(source)))
        passed = report["release_pass"]
    report["input_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    corpus = json.dumps(ct.TOOL_SCHEMAS, sort_keys=True, ensure_ascii=False).encode()
    report["schema_corpus_sha256"] = hashlib.sha256(corpus).hexdigest()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
