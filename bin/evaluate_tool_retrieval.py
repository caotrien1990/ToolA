"""Offline Recall@8 measurement with separate audit and curated-data provenance.

Audit JSONL rows: {"task": "...", "expected_tools": ["..."], "audit_id": "...",
                  "label_source": "reviewer or pre-existing standard"}
Use --curated-corpus for tool_corpus.json samples (never a release dataset).
Only 500 unique, independently labelled audit tasks can pass the release gate.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import code_tools as ct  # noqa: E402
import orchestrator_server  # noqa: E402,F401 -- register the effective server tools
import tools_v15 as hints  # noqa: E402
from orchestrator.core.tool_retrieval import _CORPUS_PATH, tokens  # noqa: E402


def load_tasks(path, curated=False):
    path = Path(path)
    if curated:
        corpus = json.loads(path.read_text(encoding="utf-8-sig"))
        source_rows = [
            {"task": task, "expected_tools": [name], "source_type": "curated",
             "source_id": f"{name}:sample:{index}", "label_source": str(path)}
            for name, entry in corpus.items() if name != "_meta"
            for index, task in enumerate(entry["samples"])
        ]
    else:
        source_rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()
                       if line.strip()]
    tasks = []
    seen = set()
    for number, row in enumerate(source_rows, 1):
        kind = row.get("source_type", "audit")
        provenance = row.get("audit_id") if kind == "audit" else row.get("source_id")
        if kind not in {"audit", "curated"} or not provenance or not row.get("label_source"):
            raise ValueError(f"row {number} needs independent labels and source provenance")
        if not isinstance(row.get("task"), str) or not row["task"].strip():
            raise ValueError(f"row {number} needs non-empty task text")
        if not isinstance(row.get("expected_tools"), list) or not row["expected_tools"] or not all(
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
    audit_ids = set()
    for row in rows:
        old = json.loads(await hints._legacy_decision_hint(row["task"], use_memory=False))
        new = hints._rank_hint(row["task"], old)
        expected = set(row["expected_tools"])
        baseline += len(expected.intersection(old_candidates(old))) / len(expected)
        recall = len(expected.intersection(r["tool"] for r in new)) / len(expected)
        candidate += recall
        kind = row.get("source_type", "audit")
        identity = row.get("audit_id") if kind == "audit" else row.get("source_id")
        if kind == "audit" and identity:
            audit_ids.add(identity)
        if recall < 1:
            misses.append({"source_type": kind, "source_id": identity, "task": row["task"],
                           "expected": sorted(expected), "ranked": [r["tool"] for r in new]})
    count = len(rows)
    baseline = baseline / count if count else None
    candidate = candidate / count if count else None
    enough = count >= 500 and len(audit_ids) == count
    regression = baseline - candidate if count else None
    return {"tasks": count, "unique_audit_tasks": len(audit_ids), "minimum_release_tasks": 500,
            "baseline_recall_at_8": baseline, "candidate_recall_at_8": candidate,
            "regression": regression,
            "release_pass": enough and regression <= 0.02 and candidate >= 0.95,
            "embedding_condition": (candidate < 0.95 if enough else None),
            "status": "measured" if enough else "insufficient_independent_audit_tasks",
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--sample-hints", action="store_true")
    mode.add_argument("--curated-corpus", action="store_true")
    args = parser.parse_args()
    source = Path(args.dataset)
    if args.sample_hints:
        report = sample_hints(source)
        passed = report["count"] == 50
    else:
        rows = load_tasks(source, curated=args.curated_corpus)
        report = asyncio.run(evaluate(rows))
        corpus = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
        indexed_phrases = {tuple(tokens(t)) for name, entry in corpus.items() if name != "_meta"
                           for t in entry["triggers"]}
        report["exact_trigger_overlap"] = sum(tuple(tokens(row["task"])) in indexed_phrases
                                              for row in rows)
        report["corpus_samples_indexed"] = False
        passed = report["release_pass"]
    report["input_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    report["trigger_corpus_sha256"] = hashlib.sha256(_CORPUS_PATH.read_bytes()).hexdigest()
    schema_bytes = json.dumps(ct.TOOL_SCHEMAS, sort_keys=True, ensure_ascii=False).encode()
    report["schema_corpus_sha256"] = hashlib.sha256(schema_bytes).hexdigest()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
