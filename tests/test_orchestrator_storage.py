from __future__ import annotations

import json
import multiprocessing as mp
import threading
from pathlib import Path

from orchestrator.storage.fs import atomic_write_text
from orchestrator.storage.json_store import load_json
from orchestrator.storage.jsonl_store import append_jsonl, read_tail


def _append_worker(path: str, prefix: str, count: int) -> None:
    for index in range(count):
        append_jsonl(path, {"prefix": prefix, "index": index})


def test_atomic_write_text_creates_file_with_content(tmp_path: Path) -> None:
    target = tmp_path / "state" / "broker.txt"

    atomic_write_text(target, "nội dung")

    assert target.read_text(encoding="utf-8") == "nội dung"
    assert not list(target.parent.glob("*.tmp"))


def test_load_json_returns_default_for_corrupt_file(tmp_path: Path) -> None:
    target = tmp_path / "broken.json"
    target.write_text("{not-json", encoding="utf-8")

    assert load_json(target, default={"ok": False}) == {"ok": False}
    assert load_json(tmp_path / "missing.json", default=[]) == []


def test_append_jsonl_parallel_process_thread_keeps_all_lines(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    count = 50

    process = mp.Process(target=_append_worker, args=(str(target), "process", count))
    thread = threading.Thread(target=_append_worker, args=(str(target), "thread", count))

    process.start()
    thread.start()
    thread.join(timeout=10)
    process.join(timeout=10)

    assert not thread.is_alive()
    assert process.exitcode == 0

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == count * 2
    events = [json.loads(line) for line in lines]
    assert {(event["prefix"], event["index"]) for event in events} == {
        (prefix, index) for prefix in ("process", "thread") for index in range(count)
    }


def test_read_tail_returns_limit_in_file_order(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    for index in range(20):
        append_jsonl(target, {"index": index})

    assert read_tail(target, limit=5) == [{"index": index} for index in range(15, 20)]
    assert read_tail(target, limit=0) == []
