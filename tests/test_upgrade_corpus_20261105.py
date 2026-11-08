"""Guard corpus/evaluation separation and provenance of the release gate."""
import asyncio
import importlib.util
import json
from pathlib import Path

from orchestrator.core import tool_retrieval as retrieval


def evaluation_module():
    path = Path(__file__).resolve().parents[1] / "bin/evaluate_tool_retrieval.py"
    spec = importlib.util.spec_from_file_location("retrieval_evaluation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_corpus_samples_never_enter_index_and_cache_invalidates(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus.json"
    entry = {"name": "probe", "triggers": ["triggerneedle"], "samples": ["holdoutneedle"]}
    corpus.write_text(json.dumps({"probe": entry}), encoding="utf-8")
    monkeypatch.setattr(retrieval, "_CORPUS_PATH", corpus)
    schemas = [{"function": {"name": "probe", "description": "ordinary"}}]
    assert retrieval.rank_tools("triggerneedle", schemas, {})[0]["tool"] == "probe"
    assert retrieval.rank_tools("holdoutneedle", schemas, {}) == []
    entry["triggers"] = ["replacementneedle"]
    corpus.write_text(json.dumps({"probe": entry}), encoding="utf-8")
    assert retrieval.rank_tools("triggerneedle", schemas, {}) == []
    assert retrieval.rank_tools("replacementneedle", schemas, {})[0]["tool"] == "probe"


def test_curated_or_duplicate_audit_provenance_cannot_pass_release(monkeypatch):
    module = evaluation_module()
    async def old(*args, **kwargs):
        return '{"recommended_tool": "read_file_range"}'
    monkeypatch.setattr(module.hints, "_legacy_decision_hint", old)
    monkeypatch.setattr(module.hints, "_rank_hint", lambda *args: [{"tool": "read_file_range"}])
    rows = [{"task": f"task {i}", "expected_tools": ["read_file_range"],
             "source_type": "curated", "source_id": str(i)} for i in range(500)]
    report = asyncio.run(module.evaluate(rows))
    assert report["candidate_recall_at_8"] == 1
    assert not report["release_pass"] and report["embedding_condition"] is None
    for row in rows:
        row.update(source_type="audit", audit_id="same-audit-row")
    assert not asyncio.run(module.evaluate(rows))["release_pass"]


def test_pack_loads_samples_as_curated_not_audit():
    module = evaluation_module()
    rows = module.load_tasks(retrieval._CORPUS_PATH, curated=True)
    assert len({tool for row in rows for tool in row["expected_tools"]}) == 34
    assert all(row["source_type"] == "curated" and "audit_id" not in row for row in rows)


def test_unicode_separators_inside_jsonl_strings_are_not_record_boundaries(tmp_path):
    module = evaluation_module()
    path = tmp_path / "tasks.jsonl"
    task = "read file\u0085range"
    row = {"task": task, "expected_tools": ["read_file_range"],
           "audit_id": "unicode-row", "label_source": "fixture"}
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    assert module.load_tasks(path)[0]["task"] == task
    hint = {"audit_id": "unicode-hint", "response": {"ca_hint": task}}
    path.write_text(json.dumps(hint, ensure_ascii=False) + "\n", encoding="utf-8")
    assert module.sample_hints(path)["samples"][0]["response"]["ca_hint"] == task
