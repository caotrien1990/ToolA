import json
from pathlib import Path

from orchestrator.council.route_metrics import (
    UNKNOWN, build_measurement, build_scorecard, parse_dispatch_metadata, validate_artifact,
)

PROMPT = """TASK_ID: T-17
OBJECTIVE_CLASS: backend_audit
RISK_LEVEL: high
DEV_LEVEL: high
LOAD_BUDGET_SOURCE: benchmark_r0
INDEPENDENT_QUESTIONS: 3
FILES_SUBSYSTEMS: 2
EXPECTED_TOOL_CALLS: 7
EXPECTED_WRITES: 1
DEPENDENCY_DEPTH: 2
EXPECTED_END_MARKER: END_CODO_R1_T17
Do the bounded audit.
"""


def _item(tmp_path: Path, artifact: str = ""):
    return {"request_id": "run-1", "target": "codo", "provider": "codex", "model": "gpt-5.6-sol",
            "runtime_key": "codo::gpt-5.6-sol", "text": PROMPT, "artifact_path": artifact,
            "created_at": 10.0, "started_at": 11.0, "measurement_metadata": parse_dispatch_metadata(PROMPT)}


def test_metadata_parser_captures_task_and_five_load_dimensions():
    meta = parse_dispatch_metadata(PROMPT)
    assert meta["task_id"] == "T-17"
    assert meta["objective_class"] == "backend_audit"
    assert meta["risk_level"] == meta["dev_level"] == "high"
    assert [meta[k] for k in ("independent_questions", "files_subsystems", "expected_tool_calls", "expected_writes", "dependency_depth")] == [3, 2, 7, 1, 2]
    assert meta["prompt_bytes"] > 0


def test_missing_metadata_is_unknown_not_inferred():
    meta = parse_dispatch_metadata("fix it")
    assert meta["risk_level"] == UNKNOWN
    assert meta["dev_level"] == UNKNOWN
    assert meta["objective_class"] == UNKNOWN


def test_artifact_states(tmp_path):
    missing = tmp_path / "missing.md"
    assert validate_artifact(str(missing), "END")["artifact_valid"] is False
    zero = tmp_path / "zero.md"; zero.write_text("")
    assert validate_artifact(str(zero), "END")["artifact_nonzero"] is False
    bad = tmp_path / "bad.md"; bad.write_text("content")
    assert validate_artifact(str(bad), "END")["end_marker_present"] is False
    good = tmp_path / "good.md"; good.write_text("content\nEND\n")
    assert validate_artifact(str(good), "END")["artifact_valid"] is True


def test_transport_ok_without_artifact_and_tests_is_not_quality_pass(tmp_path):
    row = build_measurement(_item(tmp_path), {"ok": True, "reply": "DONE: claimed"}, now=20.0)
    assert row["outcome"]["transport_ok"] is True
    assert row["artifact"]["artifact_valid"] == UNKNOWN
    assert row["quality"]["quality_verified"] is False


def test_measurement_links_reply_and_validates_evidence(tmp_path):
    artifact = tmp_path / "res.md"; artifact.write_text("evidence\nEND_CODO_R1_T17\n")
    row = build_measurement(_item(tmp_path, str(artifact)), {"ok": True, "reply": "DONE\npytest: 12 passed", "response_tokens": "123"}, reply_event_id="evt-2", now=20.0)
    assert row["request_id"] == "run-1" and row["reply_event_id"] == "evt-2"
    assert row["quality"]["quality_verified"] is True
    assert row["cost"]["response_tokens"] == 123
    assert row["cost"]["quality_per_cost"] != UNKNOWN


def test_scorecard_exact_bucket_percentiles_and_insufficient_capacity(tmp_path):
    rows = []
    for i, duration in enumerate([1, 2, 3, 4]):
        artifact = tmp_path / f"{i}.md"; artifact.write_text("ok\nEND_CODO_R1_T17\n")
        row = build_measurement(_item(tmp_path, str(artifact)), {"ok": True, "reply": "DONE\npytest: 1 passed"}, now=11 + duration)
        rows.append(row)
    card = build_scorecard(rows, min_samples=5)
    bucket = card["buckets"][0]
    assert bucket["duration_p50_sec"] == 2.0
    assert bucket["duration_p95_sec"] == 4.0
    assert bucket["confidence"] == "insufficient"
    assert bucket["numeric_capacity"] == UNKNOWN


def test_sufficient_homogeneous_samples_get_recommendation_not_capacity_number(tmp_path):
    rows = []
    for i in range(5):
        artifact = tmp_path / f"s{i}.md"; artifact.write_text("ok\nEND_CODO_R1_T17\n")
        rows.append(build_measurement(_item(tmp_path, str(artifact)), {"ok": True, "reply": "DONE\npytest: 2 passed"}, now=15.0 + i))
    bucket = build_scorecard(rows, min_samples=5)["buckets"][0]
    assert bucket["confidence"] == "sufficient"
    assert bucket["recommendation"] == "preferred"
    assert bucket["numeric_capacity"] == "eligible_for_controlled_estimation"
    assert not isinstance(bucket["numeric_capacity"], (int, float))


def test_legacy_like_rows_do_not_crash_scorecard():
    rows = [{"route": {"harness": "kilo", "model": "minimax-m3"}, "outcome": {"transport_ok": True}, "timing": {"duration_sec": 2}} for _ in range(40)]
    card = build_scorecard(rows)
    assert card["measurement_count"] == 40
    assert card["buckets"][0]["confidence"] == "insufficient"


def test_a_row_holding_a_line_break_is_not_dropped(tmp_path):
    # append_jsonl ends a row at "\n" with ensure_ascii=False, so a reply holding
    # U+2028 / U+0085 is written raw; str.splitlines() broke the row there and the
    # measurement was dropped as a JSONDecodeError -- a scorecard one sample short.
    from orchestrator.council.route_metrics import append_measurement, load_measurements
    path = tmp_path / "measurements.jsonl"
    text = "a" + chr(0x2028) + "b" + chr(0x85) + "c"
    append_measurement(path, {"reply": text})
    append_measurement(path, {"n": 2})
    assert load_measurements(path) == [{"reply": text}, {"n": 2}]
