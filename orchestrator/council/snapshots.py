"""Versioned council dashboard snapshots and bounded tail readers."""
from __future__ import annotations

import copy
import hashlib
import json
import msvcrt
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_VOLATILE_KEYS = {
    "generated_at", "version", "generation", "full", "delta",
    "fallback_reason", "removed_ids",
}


def acquire_file_lock_until(handle, *, deadline: float,
                            retry_seconds: float = 0.01) -> bool:
    """Acquire one Windows lock byte with bounded retry until an explicit deadline."""
    delay = max(0.001, float(retry_seconds))
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            remaining = float(deadline) - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(delay, remaining))


@dataclass(frozen=True)
class SnapshotSelection:
    payload: dict[str, Any]
    not_modified: bool = False


@dataclass(frozen=True)
class TailRead:
    data: bytes
    size: int
    mtime_ns: int
    truncated: bool


@dataclass
class _SnapshotRecord:
    version: int
    fingerprint: str
    payload: dict[str, Any]
    built_at: float
    source_revision: str


class VersionedSnapshotCache:
    """One thread-safe cache for state, activity, and event snapshot variants."""

    def __init__(self, *, ttl_seconds: float = 1.5, history_limit: int = 32):
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.history_limit = max(1, int(history_limit))
        self._lock = threading.Condition(threading.RLock())
        self._records: dict[str, deque[_SnapshotRecord]] = {}
        self._building: dict[str, object] = {}
        self._epoch = 0
        self._version = 0
        self._builds = 0
        self._hits = 0
        self._stale_hits = 0
        self._waiters = 0

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        stable = {
            key: value for key, value in payload.items()
            if key not in _VOLATILE_KEYS
        }
        raw = json.dumps(
            stable,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def invalidate(self) -> None:
        with self._lock:
            self._epoch += 1
            # Retire old-epoch ownership immediately. Its builder may finish, but
            # the token check prevents it from publishing or clearing a new slot.
            self._building.clear()
            # Keep version history so clients can receive a delta after a
            # known mutation, but force the next caller to rebuild synchronously.
            for history in self._records.values():
                if history:
                    history[-1].built_at = float('-inf')
            self._lock.notify_all()

    def _build_and_store(
        self,
        cache_key: str,
        builder: Callable[[], dict[str, Any]],
        generation: str,
        build_token: object,
        build_epoch: int,
        source_revision: str,
    ) -> dict[str, Any] | None:
        try:
            built = builder()
            if not isinstance(built, dict):
                built = {}
        except Exception as exc:
            built = {"error": str(exc)}
        fingerprint = self._fingerprint(built)
        built_at = time.monotonic()

        with self._lock:
            owns_slot = self._building.get(cache_key) is build_token
            if self._epoch != build_epoch or not owns_slot:
                if owns_slot:
                    self._building.pop(cache_key, None)
                self._lock.notify_all()
                history = self._records.get(cache_key)
                if history:
                    self._hits += 1
                    return copy.deepcopy(history[-1].payload)
                return None

            history = self._records.setdefault(
                cache_key, deque(maxlen=self.history_limit))
            previous = history[-1] if history else None
            if previous and previous.fingerprint == fingerprint:
                version = previous.version
                payload: dict[str, Any] = copy.deepcopy(built)
                payload.update({
                    "version": version,
                    "generation": generation,
                })
                history[-1] = _SnapshotRecord(
                    version, fingerprint, payload, built_at, source_revision)
            else:
                self._version += 1
                version = self._version
                payload = copy.deepcopy(built)
                payload.update({
                    "version": version,
                    "generation": generation,
                })
                history.append(_SnapshotRecord(
                    version, fingerprint, payload, built_at, source_revision))
            self._builds += 1
            self._building.pop(cache_key, None)
            self._lock.notify_all()
            return copy.deepcopy(payload)

    def get(
        self,
        key: str,
        builder: Callable[[], dict[str, Any]],
        *,
        generation: str,
        ttl_seconds: float | None = None,
        fresh: bool = False,
        source_revision: str = "",
        wait_timeout_seconds: float = 1.0,
    ) -> dict[str, Any]:
        cache_key = str(key)
        ttl = (
            self.ttl_seconds
            if ttl_seconds is None
            else max(0.0, float(ttl_seconds))
        )

        wait_deadline = time.monotonic() + max(0.01, float(wait_timeout_seconds))
        while True:
            now = time.monotonic()
            stale_payload: dict[str, Any] | None = None
            with self._lock:
                history = self._records.get(cache_key)
                current = history[-1] if history else None
                force_refresh = (
                    current is not None
                    and current.built_at == float('-inf')
                )
                if (
                    current
                    and not force_refresh
                    and now - current.built_at < ttl
                    and (not fresh or (
                        bool(source_revision)
                        and current.source_revision == source_revision
                    ))
                ):
                    self._hits += 1
                    return copy.deepcopy(current.payload)

                observed_builder = self._building.get(cache_key)
                if observed_builder is not None:
                    if current is not None and not force_refresh and not fresh:
                        self._stale_hits += 1
                        return copy.deepcopy(current.payload)
                    self._waiters += 1
                    try:
                        while self._building.get(cache_key) is observed_builder:
                            remaining = wait_deadline - time.monotonic()
                            if remaining <= 0:
                                if current is not None:
                                    payload = copy.deepcopy(current.payload)
                                else:
                                    payload = {"version": 0, "generation": generation}
                                payload["_freshness_timeout"] = True
                                return payload
                            self._lock.wait(timeout=min(0.1, remaining))
                    finally:
                        self._waiters = max(0, self._waiters - 1)
                    continue

                build_token = object()
                build_epoch = self._epoch
                self._building[cache_key] = build_token
                if current is not None and not force_refresh and not fresh:
                    self._stale_hits += 1
                    stale_payload = copy.deepcopy(current.payload)

            if stale_payload is not None:
                refresh = threading.Thread(
                    target=self._build_and_store,
                    args=(
                        cache_key,
                        builder,
                        generation,
                        build_token,
                        build_epoch,
                        source_revision,
                    ),
                    name=f"council-snapshot-{abs(hash(cache_key)) % 10000}",
                    daemon=True,
                )
                try:
                    refresh.start()
                except RuntimeError:
                    result = self._build_and_store(
                        cache_key,
                        builder,
                        generation,
                        build_token,
                        build_epoch,
                        source_revision,
                    )
                    if result is None:
                        continue
                    return result
                return stale_payload

            result = self._build_and_store(
                cache_key,
                builder,
                generation,
                build_token,
                build_epoch,
                source_revision,
            )
            if result is not None:
                return result

    def select(
        self,
        key: str,
        current: dict[str, Any],
        *,
        since_version: int = 0,
        client_generation: str = "",
        generation: str,
        collection_field: str,
        id_field: str,
    ) -> SnapshotSelection:
        payload = copy.deepcopy(current)
        current_version = int(payload.get("version", 0) or 0)
        freshness_timeout = bool(payload.pop("_freshness_timeout", False))

        def full(reason: str) -> SnapshotSelection:
            out = copy.deepcopy(payload)
            out.update({
                "generation": generation,
                "version": current_version,
                "full": True,
                "delta": False,
            })
            if reason:
                out["fallback_reason"] = reason
            return SnapshotSelection(out)

        if freshness_timeout:
            return full("freshness_timeout")
        if int(since_version or 0) <= 0:
            return full("initial")
        if not client_generation:
            return full("generation_missing")
        if client_generation != generation:
            return full("generation_mismatch")
        if int(since_version) == current_version:
            return SnapshotSelection({}, not_modified=True)

        with self._lock:
            history = list(self._records.get(str(key)) or ())
        previous = next(
            (record for record in history if record.version == int(since_version)),
            None,
        )
        if previous is None:
            return full("history_miss")

        old_items = {
            str(item.get(id_field)): item
            for item in previous.payload.get(collection_field, [])
            if isinstance(item, dict) and item.get(id_field) is not None
        }
        new_items = {
            str(item.get(id_field)): item
            for item in payload.get(collection_field, [])
            if isinstance(item, dict) and item.get(id_field) is not None
        }
        changed = [
            copy.deepcopy(item)
            for ident, item in new_items.items()
            if old_items.get(ident) != item
        ]
        removed = sorted(set(old_items) - set(new_items))
        out = {
            key_name: copy.deepcopy(value)
            for key_name, value in payload.items()
            if key_name != collection_field
        }
        out.update({
            collection_field: changed,
            "removed_ids": removed,
            "generation": generation,
            "version": current_version,
            "full": False,
            "delta": True,
        })
        return SnapshotSelection(out)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "keys": len(self._records),
                "version": self._version,
                "epoch": self._epoch,
                "building": sorted(self._building),
                "builds": self._builds,
                "hits": self._hits,
                "stale_hits": self._stale_hits,
                "waiters": self._waiters,
                "history_limit": self.history_limit,
                "ttl_seconds": self.ttl_seconds,
            }


def read_tail_bytes(path: Path, max_bytes: int) -> TailRead:
    """Read at most max_bytes from the end of path without a whole-file read."""
    target = Path(path)
    cap = max(1, int(max_bytes))
    try:
        stat = target.stat()
    except OSError:
        return TailRead(b"", 0, 0, False)
    size = int(stat.st_size)
    start = max(0, size - cap)
    try:
        with target.open("rb") as handle:
            handle.seek(start)
            data = handle.read(cap)
    except OSError:
        return TailRead(b"", size, int(getattr(stat, "st_mtime_ns", 0)), start > 0)
    return TailRead(
        data=data,
        size=size,
        mtime_ns=int(getattr(stat, "st_mtime_ns", 0)),
        truncated=start > 0,
    )


def read_jsonl_tail(path: Path, *, limit: int, max_bytes: int = 2_000_000) -> list[dict]:
    """Parse only the bounded tail of an append-only JSONL file."""
    wanted = max(1, min(5000, int(limit)))
    tail = read_tail_bytes(Path(path), max_bytes)
    if not tail.data:
        return []
    lines = tail.data.splitlines()
    if tail.truncated and lines:
        lines = lines[1:]
    rows: list[dict] = []
    for raw in lines[-wanted:]:
        try:
            item = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows[-wanted:]
