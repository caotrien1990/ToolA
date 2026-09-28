"""Bounded append-only JSONL spool for work a writer could not commit.

The durable audit staging path used to drop a row when SQLite stayed busy past
its retry budget: it incremented a counter and returned, so the payload was gone.
This module parks that payload on disk and replays it later.

The session writer's fire-and-forget drop was examined for the same treatment and
deliberately left out (2026-09-28): no production caller submits fire-and-forget,
and its payload is a ``write_fn`` closure, which no spool can replay. See the
comment at the drop site in orchestrator/session/store.py.

Design constraints learned from the audit staging call site:

* ``append_spool`` is called from inside an ``except`` block, so it must never
  raise. A spool that throws turns a recoverable drop into a crash.
* ``drain_spool`` runs at writer start while the real sink may still be
  unhealthy, so a handler is allowed to refuse a record and have it kept for the
  next attempt.
* A corrupt trailing line is expected, not exceptional: the process can die
  mid-append. One bad line must not strand every good line behind it.
* The file is bounded. An outage that lasts hours must not fill the disk, and
  "oldest dropped" is reported rather than hidden.
"""

from __future__ import annotations

import json
import os
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable

from orchestrator.storage.fs import atomic_write_text

# An outage parks records until the sink recovers. 5000 is ~8x the largest
# observed burst (268 staged rows in one session) and keeps the drain read cheap.
DEFAULT_MAX_RECORDS = 5000


def _ends_mid_line(target: Path) -> bool:
    """True when the file's last byte is not a newline, i.e. an append was cut short.

    Cheap enough for the hot path: one seek and one byte, no read of the spool.
    A missing file or an unreadable one answers False -- append_spool is about to
    fail on it anyway, and guessing True there would prepend a blank line.
    """
    try:
        with open(target, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                return False
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"
    except (FileNotFoundError, OSError):
        return False


def append_spool(
    path: str | os.PathLike[str],
    record: dict[str, Any],
    max_records: int = DEFAULT_MAX_RECORDS,
) -> bool:
    """Park one record. Returns False instead of raising, because callers are handlers.

    The write is flushed and fsynced: the whole point of the spool is to survive
    the crash that a plain buffered append would lose.
    """
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, allow_nan=False, default=str)
        # A process killed mid-append leaves a line with no terminator. Appending
        # straight onto it concatenates THIS record into that torn one, so a single
        # crash would cost two records instead of the one already lost. Measured:
        # {"i": 0} / torn / {"i": 2} read back as [0] with the 2 swallowed.
        prefix = "\n" if _ends_mid_line(target) else ""
        with open(target, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(prefix + line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        return False

    try:
        _trim_spool(path, max_records)
    except Exception:
        # Trimming is hygiene, not correctness: the record is already durable.
        pass
    return True


def read_spool(path: str | os.PathLike[str]) -> tuple[list[dict[str, Any]], int]:
    """Return (records, corrupt_line_count). A torn line is skipped, never fatal."""
    records: list[dict[str, Any]] = []
    corrupt = 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except (JSONDecodeError, ValueError):
                    corrupt += 1
                    continue
                if isinstance(parsed, dict):
                    records.append(parsed)
                else:
                    corrupt += 1
    except FileNotFoundError:
        return [], 0
    except OSError:
        return [], 0
    return records, corrupt


def drain_spool(
    path: str | os.PathLike[str],
    handler: Callable[[dict[str, Any]], bool],
) -> dict[str, int]:
    """Replay every parked record through handler; keep the ones it refuses.

    handler returns True when the record reached its real sink. A False return,
    or a raising handler, keeps the record on disk for the next drain, so a sink
    that is still unhealthy costs nothing but a retry.
    """
    records, corrupt = read_spool(path)
    if not records and corrupt == 0:
        return {"replayed": 0, "kept": 0, "corrupt": 0}

    kept: list[dict[str, Any]] = []
    replayed = 0
    for record in records:
        try:
            consumed = bool(handler(record))
        except Exception:
            consumed = False
        if consumed:
            replayed += 1
        else:
            kept.append(record)

    _rewrite_spool(path, kept)
    return {"replayed": replayed, "kept": len(kept), "corrupt": corrupt}


def spool_depth(path: str | os.PathLike[str]) -> int:
    """Number of parked records, for health publication."""
    records, _ = read_spool(path)
    return len(records)


def _trim_spool(path: str | os.PathLike[str], max_records: int) -> int:
    """Drop the oldest records past max_records. Returns how many were dropped."""
    if max_records <= 0:
        return 0
    records, _ = read_spool(path)
    if len(records) <= max_records:
        return 0
    dropped = len(records) - max_records
    _rewrite_spool(path, records[dropped:])
    return dropped


def _rewrite_spool(path: str | os.PathLike[str], records: list[dict[str, Any]]) -> None:
    """Replace the spool atomically so a crash mid-drain cannot lose kept records."""
    if not records:
        try:
            os.remove(path)
        except (FileNotFoundError, OSError):
            pass
        return
    text = "".join(
        json.dumps(record, ensure_ascii=False, allow_nan=False, default=str) + "\n"
        for record in records
    )
    atomic_write_text(path, text, encoding="utf-8")
