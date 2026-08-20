from __future__ import annotations

import json
import multiprocessing as mp
import threading
from pathlib import Path

from orchestrator.storage.fs import atomic_write_text
from orchestrator.storage.json_store import load_json
from orchestrator.storage.jsonl_store import (
    LOCK_TIMEOUT_SECONDS,
    append_jsonl,
    read_tail,
)


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


# A worker may legitimately spend LOCK_TIMEOUT_SECONDS waiting for the sidecar
# byte on ANY single append, and then pays one fsync per append on top -- measured
# at 125ms on this disk, so 50 appends is ~6s of pure platter time per worker
# before any contention. A join deadline below that sum does not test liveness, it
# tests disk speed: the original 10s was shorter than the module's own documented
# wait budget alone and failed under full-suite load with nothing actually wrong.
#
# Derived from the constant rather than hardcoded, so raising the module's budget
# cannot silently reintroduce a deadline tighter than the contract it checks.
_FSYNC_BUDGET_PER_APPEND = 0.5
_JOIN_TIMEOUT = LOCK_TIMEOUT_SECONDS + 50 * _FSYNC_BUDGET_PER_APPEND


def test_append_jsonl_parallel_process_thread_keeps_all_lines(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    count = 50

    process = mp.Process(target=_append_worker, args=(str(target), "process", count))
    thread = threading.Thread(target=_append_worker, args=(str(target), "thread", count))

    process.start()
    thread.start()
    thread.join(timeout=_JOIN_TIMEOUT)
    process.join(timeout=_JOIN_TIMEOUT)

    # Still asserted, and still meaningful: a lost append or a self-deadlock hangs
    # forever, so the deadline catches it -- it is only the ARBITRARY tightness that
    # was the bug. The line-set assertion below is what proves nothing was lost.
    assert not thread.is_alive(), (
        "the thread worker did not finish within %.0fs" % _JOIN_TIMEOUT)
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
