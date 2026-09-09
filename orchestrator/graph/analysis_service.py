"""Bounded analysis_view runtime: cache + cooperative workers (P2-S2).

Addendum B: reuse asyncio.to_thread default executor. No new threadpool
or lane. Admission max 2, queue_on_full=false. wait_for is not a kill.
Slot releases only after the worker thread actually exits.
Latency contract = graph_deadline_ms + join_grace_ms (2.25s), not a
2s hard wall.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Iterable

from .analysis_view import (
    ALGORITHM_VERSION,
    PAYLOAD_VERSION,
    _CONFIDENCE_POLICY,
    _included_set_hash,
    _sorted_display,
    build_analysis_view,
)
from .config import (
    ANALYSIS_CHECK_EVERY_UNITS,
    ANALYSIS_EVOLUTION_DEADLINE_MS,
    ANALYSIS_GRAPH_DEADLINE_MS,
    ANALYSIS_JOIN_GRACE_MS,
    ANALYSIS_MAX_ACTIVE_WORKERS,
    ANALYSIS_QUEUE_ON_FULL,
    ANALYSIS_RESPONSE_MAX_BYTES,
    ANALYSIS_TOP_LIST_MAX,
    EXTRACTOR_VERSION,
    graph_db_path,
)
from .schema import SCHEMA_VERSION
from . import store as _store


WorkFn = Callable[..., dict[str, Any]]

_state_lock = threading.Lock()
_active_workers = 0
_quarantine = False
_cache: dict[str, dict[str, Any]] = {}
_supervised: set[asyncio.Task] = set()
_tls = threading.local()
_wrap_lock = threading.Lock()
_get_db_wrapped = False
_orig_get_db = _store.get_db


def l1_caps() -> dict[str, Any]:
    """Named L1 budgets. Tests assert these; no silent truncate here."""
    return {
        "graph_deadline_ms": ANALYSIS_GRAPH_DEADLINE_MS,
        "evolution_deadline_ms": ANALYSIS_EVOLUTION_DEADLINE_MS,
        "join_grace_ms": ANALYSIS_JOIN_GRACE_MS,
        "latency_contract_ms": ANALYSIS_GRAPH_DEADLINE_MS + ANALYSIS_JOIN_GRACE_MS,
        "max_active_workers": ANALYSIS_MAX_ACTIVE_WORKERS,
        "check_every_units": ANALYSIS_CHECK_EVERY_UNITS,
        "response_max_bytes": ANALYSIS_RESPONSE_MAX_BYTES,
        "top_list_max": ANALYSIS_TOP_LIST_MAX,
        "queue_on_full": ANALYSIS_QUEUE_ON_FULL,
        "new_threadpool_or_lane": False,
        "backend": "asyncio.to_thread",
    }


def active_worker_count() -> int:
    with _state_lock:
        return _active_workers


def is_quarantined() -> bool:
    with _state_lock:
        return _quarantine


def cache_size() -> int:
    with _state_lock:
        return len(_cache)


def cache_snapshot_keys() -> list[str]:
    with _state_lock:
        return sorted(_cache.keys())


def reset_runtime_state(*, wait_s: float = 2.0) -> None:
    """Test helper: wait for workers, then clear cache/quarantine. Does not kill."""
    deadline = time.monotonic() + max(0.0, wait_s)
    while active_worker_count() > 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    with _state_lock:
        _cache.clear()
        global _quarantine
        _quarantine = False


def _unavailable(reason: str, *, root: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "version": PAYLOAD_VERSION,
        "status": "unavailable",
        "reason": reason,
        "pass": False,
        "score": None,
        "included_files": [],
        "test_files": [],
        "isolated_files": [],
        "strict_edges": [],
        "observed_edges": [],
        "counts": {},
        "metric_notes": [reason],
        "truncated": False,
        "reasons": [reason],
        "cache_key": None,
        "cache_hit": False,
        "elapsed_ms": 0,
        "provenance": {
            "root": root,
            "extractor_version": EXTRACTOR_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "schema_version": SCHEMA_VERSION,
            "latency_contract_ms": ANALYSIS_GRAPH_DEADLINE_MS + ANALYSIS_JOIN_GRACE_MS,
        },
    }
    if extra:
        payload.update(extra)
    return payload


def _request_key(root: str, included: list[str], tests: list[str]) -> str:
    ident = {
        "root": os.path.abspath(root).replace("\\", "/"),
        "db_path": graph_db_path(root).replace("\\", "/"),
        "included_set_hash": _included_set_hash(included),
        "test_set_hash": _included_set_hash(tests),
        "extractor_version": EXTRACTOR_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "confidence_policy": _CONFIDENCE_POLICY,
        "schema_version": SCHEMA_VERSION,
    }
    raw = json.dumps(ident, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_snapshot(root: str, included: list[str], checkpoint: Callable[[str], bool] | None = None) -> str:
    parts: list[str] = []
    for rel in _sorted_display(included):
        if checkpoint is not None and checkpoint("snapshot"):
            raise InterruptedError("analysis_stop")
        path = os.path.join(root, rel.replace("/", os.sep))
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            digest = ""
        parts.append(rel + "=" + digest)
    db = graph_db_path(root)
    try:
        st = os.stat(db)
        parts.append("db:%d:%d" % (st.st_mtime_ns, st.st_size))
    except OSError:
        parts.append("db:missing")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _cache_get(request_key: str, snapshot: str) -> dict[str, Any] | None:
    with _state_lock:
        row = _cache.get(request_key)
        if not row:
            return None
        if row.get("snapshot") != snapshot:
            del _cache[request_key]
            return None
        payload = dict(row["payload"])
    payload["cache_hit"] = True
    return payload


def _cache_put(request_key: str, snapshot: str, payload: dict[str, Any]) -> None:
    stored = dict(payload)
    stored["cache_hit"] = False
    with _state_lock:
        _cache[request_key] = {"snapshot": snapshot, "payload": stored}


def _try_admit() -> str | None:
    global _active_workers
    with _state_lock:
        if _quarantine:
            return "unavailable:analysis_busy"
        if _active_workers >= ANALYSIS_MAX_ACTIVE_WORKERS:
            return "unavailable:analysis_busy"
        _active_workers += 1
        return None


def _release_slot() -> None:
    global _active_workers
    with _state_lock:
        if _active_workers > 0:
            _active_workers -= 1


def _set_quarantine(value: bool) -> None:
    global _quarantine
    with _state_lock:
        _quarantine = value


def _checkpoint_factory(stop: threading.Event, deadline_mono: float) -> Callable[[str], bool]:
    units = {"n": 0}

    def checkpoint(_phase: str = "unit") -> bool:
        units["n"] += 1
        if stop.is_set() or time.monotonic() >= deadline_mono:
            stop.set()
            return True
        if units["n"] % max(1, ANALYSIS_CHECK_EVERY_UNITS) == 0:
            if stop.is_set() or time.monotonic() >= deadline_mono:
                stop.set()
                return True
        return False

    return checkpoint


def _tls_stop() -> threading.Event | None:
    return getattr(_tls, "stop", None)


def _tls_deadline() -> float | None:
    return getattr(_tls, "deadline", None)


def _bind_tls(stop: threading.Event, deadline_mono: float) -> None:
    _tls.stop = stop
    _tls.deadline = deadline_mono


def _clear_tls() -> None:
    _tls.stop = None
    _tls.deadline = None


def _cooperative_get_db(project_path: str, *, readonly: bool = False) -> sqlite3.Connection:
    stop = _tls_stop()
    deadline = _tls_deadline()
    if stop is not None and (stop.is_set() or (deadline is not None and time.monotonic() >= deadline)):
        raise InterruptedError("analysis_stop")
    conn = _orig_get_db(project_path, readonly=readonly)
    if stop is None or deadline is None:
        return conn
    remain = max(1, int((deadline - time.monotonic()) * 1000))
    try:
        conn.execute("PRAGMA busy_timeout=%d;" % remain)
    except sqlite3.Error:
        pass

    def progress() -> int:
        if stop.is_set() or time.monotonic() >= deadline:
            return 1
        return 0

    conn.set_progress_handler(progress, max(1, ANALYSIS_CHECK_EVERY_UNITS))
    return conn


def _ensure_get_db_wrap() -> None:
    global _get_db_wrapped
    with _wrap_lock:
        if _get_db_wrapped:
            return
        _store.get_db = _cooperative_get_db  # type: ignore[method-assign]
        _get_db_wrapped = True


def _worker(
    project_path: str,
    included_files: list[str] | None,
    test_files: list[str] | None,
    finalize_scope: bool,
    stop: threading.Event,
    deadline_mono: float,
    work_fn: WorkFn | None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    root = os.path.abspath(project_path)
    checkpoint = _checkpoint_factory(stop, deadline_mono)

    def stopped(reason: str) -> dict[str, Any]:
        extra = {"elapsed_ms": round((time.monotonic() - t0) * 1000, 1)}
        if time.monotonic() >= deadline_mono:
            reason = "unavailable:deadline"
        return _unavailable(reason, root=root, extra=extra)

    if checkpoint("start"):
        return stopped("unavailable:stopped")

    included = _sorted_display(included_files or []) if included_files is not None else None
    tests = _sorted_display(test_files or [])
    request_key = None
    snapshot = None
    # Callback closures carry tool-specific parameters absent from the view cache key.
    if work_fn is None and not finalize_scope and included is not None and checkpoint("before_snapshot") is False:
        request_key = _request_key(root, included, tests)
        snapshot = _file_snapshot(root, included, checkpoint=checkpoint)
        hit = _cache_get(request_key, snapshot)
        if hit is not None:
            hit["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
            return hit
    if checkpoint("after_snapshot"):
        return stopped("unavailable:stopped")

    _ensure_get_db_wrap()
    _bind_tls(stop, deadline_mono)
    try:
        if checkpoint("before_io"):
            return stopped("unavailable:stopped")
        if work_fn is not None:
            payload = work_fn(
                checkpoint=checkpoint,
                stop=stop,
                deadline_mono=deadline_mono,
                project_path=root,
                included_files=included_files,
                test_files=test_files,
                finalize_scope=finalize_scope,
            )
        else:
            payload = build_analysis_view(
                root,
                included_files=included_files,
                test_files=test_files,
                finalize_scope=finalize_scope,
                checkpoint=checkpoint,
            )
        if checkpoint("after_io") or stop.is_set() or time.monotonic() >= deadline_mono:
            return stopped("unavailable:stopped")
        if not isinstance(payload, dict):
            return _unavailable("store:TypeError", root=root)
        payload = dict(payload)
        payload["cache_hit"] = False
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        status = payload.get("status")
        if request_key is not None and snapshot is not None and status in {"ok", "partial"}:
            _cache_put(request_key, snapshot, payload)
        return payload
    except InterruptedError:
        return stopped("unavailable:stopped")
    except sqlite3.OperationalError as exc:
        name = type(exc).__name__
        msg = str(exc).lower()
        if stop.is_set() or "interrupt" in msg or "locked" in msg or "busy" in msg:
            return stopped("unavailable:stopped")
        return _unavailable("store:%s" % name, root=root)
    except Exception as exc:
        if stop.is_set() or time.monotonic() >= deadline_mono:
            return stopped("unavailable:stopped")
        return _unavailable("store:%s" % type(exc).__name__, root=root)
    finally:
        _clear_tls()


async def _supervise_until_exit(task: asyncio.Task, released: list[bool]) -> None:
    try:
        await task
    except Exception:
        pass
    finally:
        if not released[0]:
            released[0] = True
            _release_slot()
        _supervised.discard(task)
        if not _supervised:
            _set_quarantine(False)


async def run_analysis_view(
    project_path: str,
    *,
    included_files: Iterable[str] | None = None,
    test_files: Iterable[str] | None = None,
    finalize_scope: bool = False,
    work_fn: WorkFn | None = None,
    deadline_ms: int | None = None,
) -> dict[str, Any]:
    """Async owner: admit, to_thread, deadline+grace, never cancel the worker."""
    root = os.path.abspath(project_path or "")
    included_list = list(included_files) if included_files is not None else None
    test_list = list(test_files) if test_files is not None else None
    budget = ANALYSIS_GRAPH_DEADLINE_MS if deadline_ms is None else int(deadline_ms)
    if budget < 1:
        budget = 1

    busy = _try_admit()
    if busy:
        return _unavailable(busy, root=root)

    stop = threading.Event()
    deadline_mono = time.monotonic() + (budget / 1000.0)
    released = [False]

    worker_task = asyncio.create_task(
        asyncio.to_thread(
            _worker,
            root,
            included_list,
            test_list,
            finalize_scope,
            stop,
            deadline_mono,
            work_fn,
        )
    )
    _supervised.add(worker_task)

    async def _wait_grace() -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(
                asyncio.shield(worker_task),
                timeout=ANALYSIS_JOIN_GRACE_MS / 1000.0,
            )
        except asyncio.TimeoutError:
            return None

    def _release_now() -> None:
        if not released[0]:
            released[0] = True
            _release_slot()
            _supervised.discard(worker_task)

    try:
        try:
            result = await asyncio.wait_for(
                asyncio.shield(worker_task),
                timeout=budget / 1000.0,
            )
        except asyncio.TimeoutError:
            stop.set()
            result = await _wait_grace()
            if result is None:
                _set_quarantine(True)
                asyncio.create_task(_supervise_until_exit(worker_task, released))
                return _unavailable(
                    "unavailable:worker_stop_unconfirmed",
                    root=root,
                    extra={"latency_contract_ms": budget + ANALYSIS_JOIN_GRACE_MS},
                )
        _release_now()
        return result
    except asyncio.CancelledError:
        stop.set()
        result = await _wait_grace()
        if result is None:
            _set_quarantine(True)
            asyncio.create_task(_supervise_until_exit(worker_task, released))
            return _unavailable("unavailable:worker_stop_unconfirmed", root=root)
        _release_now()
        if isinstance(result, dict) and result.get("status") == "unavailable":
            return result
        return _unavailable("unavailable:stopped", root=root)


__all__ = [
    "run_analysis_view",
    "l1_caps",
    "active_worker_count",
    "is_quarantined",
    "cache_size",
    "cache_snapshot_keys",
    "reset_runtime_state",
]
