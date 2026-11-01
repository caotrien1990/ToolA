"""Crash-safe spool with serialized append/trim and identity-based replay merge.

A parked row is acknowledged only after fsync. Replay never holds the append
lock while calling a sink, and removes only the exact entries it consumed.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Callable

DEFAULT_MAX_RECORDS = 5000
logger = logging.getLogger(__name__)
_ENTRY_ID = "__spool_entry_id"
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def _spool_lock(path):
    """One bounded lock per spool, including across cooperating processes."""
    target = Path(path)
    key = os.path.normcase(os.path.abspath(target))
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=2.0):
        raise TimeoutError("spool lock busy")
    handle = None
    acquired = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = open(str(target) + ".lock", "a+b")
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 2.0
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("spool process lock busy") from None
                time.sleep(0.01)
        yield
    finally:
        if handle is not None:
            try:
                if acquired:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        lock.release()


def _ends_mid_line(target: Path) -> bool:
    try:
        with open(target, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                return False
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"
    except FileNotFoundError:
        return False


def _read_entries(path):
    records, corrupt = [], 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("not an object")
                    records.append(record)
                except (JSONDecodeError, ValueError):
                    corrupt += 1
    except FileNotFoundError:
        pass
    return records, corrupt


def _public(record):
    return {key: value for key, value in record.items() if key != _ENTRY_ID}


def append_spool(path, record: dict[str, Any], max_records=DEFAULT_MAX_RECORDS) -> bool:
    try:
        with _spool_lock(path):
            target = Path(path)
            entry = dict(record)
            entry[_ENTRY_ID] = uuid.uuid4().hex
            line = json.dumps(entry, ensure_ascii=False, allow_nan=False, default=str)
            prefix = "\n" if _ends_mid_line(target) else ""
            with open(target, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(prefix + line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                _trim_spool_unlocked(path, max_records)
            except Exception:
                logger.exception("spool trim failed; appended row remains durable: %s", path)
        return True
    except Exception:
        logger.exception("spool append failed: %s", path)
        return False


def read_spool(path) -> tuple[list[dict[str, Any]], int]:
    if not Path(path).exists():
        return [], 0
    with _spool_lock(path):
        records, corrupt = _read_entries(path)
    return [_public(row) for row in records], corrupt


def drain_spool(path, handler: Callable[[dict[str, Any]], bool], *,
                max_records: int | None = None, max_seconds: float | None = None,
                stop_on_refusal: bool = False) -> dict[str, int]:
    if not Path(path).exists():
        return {"replayed": 0, "kept": 0, "corrupt": 0}
    with _spool_lock(path):
        records, corrupt = _read_entries(path)
        if not records and not corrupt:
            return {"replayed": 0, "kept": 0, "corrupt": 0}
        # Upgrade legacy five-key rows before releasing the lock. Stable entry
        # ids distinguish identical fresh appends from the snapshot being drained.
        upgraded = False
        for row in records:
            if not row.get(_ENTRY_ID):
                row[_ENTRY_ID] = uuid.uuid4().hex
                upgraded = True
        if upgraded:
            _rewrite_spool(path, records)
    deadline = None if max_seconds is None else time.monotonic() + max(0, max_seconds)
    consumed_ids = set()
    for index, record in enumerate(records):
        if max_records is not None and index >= max_records:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
        try:
            consumed = bool(handler(_public(record)))
        except Exception:
            consumed = False
        if consumed:
            consumed_ids.add(record[_ENTRY_ID])
        elif stop_on_refusal:
            break
    with _spool_lock(path):
        current, current_corrupt = _read_entries(path)
        kept = [row for row in current if row.get(_ENTRY_ID) not in consumed_ids]
        _rewrite_spool(path, kept)
    return {"replayed": len(consumed_ids), "kept": len(kept),
            "corrupt": max(corrupt, current_corrupt)}


def spool_depth(path) -> int:
    try:
        return len(read_spool(path)[0])
    except OSError:
        logger.exception("spool depth unavailable: %s", path)
        return 0


def _trim_spool(path, max_records: int) -> int:
    with _spool_lock(path):
        return _trim_spool_unlocked(path, max_records)


def _trim_spool_unlocked(path, max_records):
    if max_records <= 0:
        return 0
    records, _ = _read_entries(path)
    dropped = max(0, len(records) - max_records)
    if dropped:
        _rewrite_spool(path, records[dropped:])
        logger.warning("spool dropped %d oldest records at capacity=%d: %s",
                       dropped, max_records, path)
    return dropped


def _rewrite_spool(path, records):
    target = Path(path)
    if not records:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        return
    temporary = target.with_name("." + target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            for row in records:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
