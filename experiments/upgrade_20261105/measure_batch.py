"""Measure pilot wire payloads on captured real calls without provider billing."""
import argparse
import asyncio
from collections import deque
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".orchestrator_cache/upgrade-measure-deps"))
from experiments.upgrade_20261105.batch_pilot import run_batch  # noqa: E402
import tiktoken  # noqa: E402


def capture(database):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    rows = connection.execute(
        "select audit_id,payload_json from durable_audit_outbox "
        "where kind='session_event' order by rowid desc limit 2000"
    ).fetchall()
    connection.close()
    chosen = []
    for audit_id, text in reversed(rows):
        payload = json.loads(text)
        body = payload.get("body") or {}
        result = payload.get("result")
        if (payload.get("tool_name") == "read_file_range" and body.get("file_path")
                and isinstance(result, dict) and isinstance(result.get("result"), str)):
            chosen.append({"audit_id": audit_id, "tool": "read_file_range",
                           "payload": body, "response": result})
    return chosen[-12:]


async def measure(rows):
    if not rows:
        return {"status": "no_complete_captures", "calls": 0, "integration_approved": False}
    calls = [{"tool": row["tool"], "payload": row["payload"]} for row in rows]
    replay = deque(rows)
    async def dispatch(tool, body):
        row = replay.popleft()
        assert tool == row["tool"] and body == row["payload"]
        return row["response"]
    batched = await run_batch(calls, dispatch)
    singles = [row["response"] for row in rows]
    assert singles == batched["results"]
    def text(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Count serialized tool payloads. Chat framing, repeated model context and
    # billed tokens need a provider-run experiment and are deliberately excluded.
    tokenizer = tiktoken.get_encoding("o200k_base")
    def count(value):
        return len(tokenizer.encode(text(value)))
    before = sum(count(call) + count(answer) for call, answer in zip(calls, singles))
    after = count({"calls": calls}) + count(batched)
    return {"status": "captured_payload_measurement", "calls": len(rows),
            "capture_ids": [row.get("capture_id", row.get("audit_id")) for row in rows], "tokenizer": "tiktoken-0.12.0/o200k_base",
            "single_payload_tokens": before, "batch_payload_tokens": after,
            "payload_saving_ratio": (before - after) / before, "output_equal": True,
            "billed_token_saving": None, "integration_approved": False,
            "capture_sha256": hashlib.sha256(text(rows).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("--capture", help="Existing complete capture JSON file instead of database extraction")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    rows = (json.loads(Path(args.capture).read_text(encoding="utf-8")) if args.capture
            else capture(args.database))
    report = asyncio.run(measure(rows))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "captured_payload_measurement" else 2


if __name__ == "__main__":
    raise SystemExit(main())
