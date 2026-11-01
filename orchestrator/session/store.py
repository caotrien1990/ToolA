"""SQLite session store helpers extracted from code_tools."""

from __future__ import annotations

import atexit
import base64
import contextvars
import gc
import hashlib
import json
import logging
import os
import queue
import re
import secrets
import sqlite3
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from contextlib import contextmanager as _contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import path_runtime

from orchestrator import lock_holder_trace  # R5: ORCH_LOCK_HOLDER_TRACE gate


logger = logging.getLogger(__name__)


def _is_unc_path_like(path: str) -> bool:
    text = str(path or "").strip()
    return text.startswith("\\\\") or text.startswith("//")


def _project_cache_dir(project_path: str) -> Path:
    if _is_unc_path_like(project_path):
        raise ValueError(
            "UNC project_path không hỗ trợ project cache auto-infer "
            "(.orchestrator_cache trên SMB sẽ treo). Dùng local project root, "
            "hoặc check_server_path cho UNC.",
        )
    return Path(project_path).resolve() / ".orchestrator_cache"


def _session_project_key(path: str) -> str:
    path_runtime.reject_c0_control_characters(path, field="project_path")
    if _is_unc_path_like(path):
        return str(path).strip()
    p = Path(path).resolve()
    embedded = path_runtime.find_orchestrator_dir(p)
    if embedded and embedded == p and (p.parent / ".orchestrator_cache").exists():
        return str(p.parent)
    return str(p)


_SESSIONS_DB = Path(os.environ.get("ORCHESTRATOR_SESSIONS_DB") or
                    (_project_cache_dir(str(Path(__file__).parents[2])) / "sessions.db"))


_SESSION_DB_BUSY_TIMEOUT_MS = 750
_SESSION_DB_RETRY_BASE_MS = 5
_SESSION_DB_RETRY_MAX_MS = 50
_SESSION_WRITE_QUEUE_SIZE = max(1, int(os.environ.get("ORCH_SESSION_WRITE_QUEUE", "10000")))
_SESSION_WRITE_BATCH_MAX = max(1, int(os.environ.get("ORCH_SESSION_WRITE_BATCH", "256")))
_SESSION_WRITE_BATCH_WINDOW_MS = max(
    0.0, float(os.environ.get("ORCH_SESSION_WRITE_BATCH_WINDOW_MS", "5")),
)
_SESSION_WRITE_STALL_SECONDS = max(
    1.0, float(os.environ.get("ORCH_SESSION_WRITE_STALL_SECONDS", "5")),
)
# R5: a batch item whose write_fn runs longer than this (ms) gets one WARNING
# line ("SLOW_WRITE_FN op=... elapsed_ms=..."). Gated by ORCH_LOCK_HOLDER_TRACE
# (see lock_holder_trace.enabled()): flag OFF -> no timing, no log, ~no cost.
_SLOW_WRITE_FN_MS = 1000.0
# A33: how many consecutive stall_seconds windows a batch may make no observable
# progress before the writer is declared FAILED rather than merely STALLED.
# Watchdog verdicts concern missing progress, independently of the caller wait
# budget. A caller timeout never cancels a transaction that can still commit.
_SESSION_WRITE_WEDGE_MULTIPLIER = max(
    2.0, float(os.environ.get("ORCH_SESSION_WRITE_WEDGE_MULTIPLIER", "6")),
)
_SESSION_DB_INITIALIZED_PATHS: set[str] = set()
_SESSION_INIT_FAILURES: dict[str, tuple[float, str]] = {}
# campaign-1608 (A38): serialize in-process migration so two threads never run
# the DDL against a fresh DB at once (the A27 race), independent of whoever calls
# _init_session_db (tests, code_tools, or _get_session_writer under its own lock).
_SESSION_INIT_LOCK = threading.Lock()
# campaign-1608 (A27): the WAL switch ignores busy_timeout, so it needs its own
# small retry budget. Short on purpose -- the winner of the race enables WAL for
# everyone, so a loser has nothing left to wait for.
_WAL_SWITCH_ATTEMPTS = 4
_WAL_SWITCH_RETRY_MS = 20
# campaign-1608 (A38): the migration DDL carries only PRAGMA busy_timeout, which
# cannot outlast a SECOND process (A28: MCP is a distinct [A] process writing the
# same sessions.db) holding the write lock longer than that budget. Retry the
# whole migration a bounded number of times, mirroring the A27 _enable_wal loop,
# then fail LOUD -- the schema is mandatory and, unlike WAL, cannot degrade.
# _SESSION_DB_RETRY_BASE_MS/_SESSION_DB_RETRY_MAX_MS above bound the backoff.
_SESSION_MIGRATION_ATTEMPTS = 6

# campaign-1608 (A22): sessions.db retention. Keep only the N most-recent days;
# older sessions and every child row that hangs off them are pruned. The default
# is 10 days (user "Anh" chose this). The knob is read LIVE inside
# prune_sessions_older_than -- NOT frozen here at import -- because A13 proved an
# import-time snapshot silently ignores an env change made after the first import
# (test isolation + a runtime `setx` both broke that way). This name only labels
# the default; it is never the live value.
_SESSION_RETENTION_DAYS_DEFAULT = 10
_SESSION_RETENTION_DAYS_ENV = "ORCHESTRATOR_SESSION_RETENTION_DAYS"


class SessionStoreContentionError(RuntimeError):
    """A bounded SQLite/queue wait failed; stage determines safe in-call replay."""

    def __init__(self, message, *, contention_stage="execution"):
        super().__init__(message)
        self.contention_stage = contention_stage


class SessionStoreCommitPendingError(SessionStoreContentionError):
    """Caller wait expired; commit may still complete, so resending is unsafe."""


_SESSION_CALL_DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "session_call_deadline", default=None)
_SESSION_CALL_WAIT_SECONDS = 30.0
# The executor supplies a permit-scoped observer; standalone store calls need none.
_SESSION_WAIT_OBSERVER: contextvars.ContextVar = contextvars.ContextVar(
    "session_wait_observer", default=None)


@_contextmanager
def _session_wait_scope(cause):
    observer = _SESSION_WAIT_OBSERVER.get()
    if observer is not None:
        observer(cause)
    try:
        yield
    finally:
        if observer is not None:
            observer(None)



def _raise_session_read_error(operation: str, exc: BaseException):
    """Only an exhausted SELECT may prove absence; a failed read never does."""
    if isinstance(exc, SessionStoreContentionError):
        raise exc
    if isinstance(exc, sqlite3.OperationalError) and any(
        word in str(exc).lower() for word in ("locked", "busy")
    ):
        raise SessionStoreContentionError(f"{operation}: {exc}") from exc
    raise exc


class _WriteItem:
    # ``busy_timeout_ms`` is the SQLite lock-wait budget (PRAGMA busy_timeout) for
    # this item. Caller wait is bounded separately and expiration reports an
    # unconfirmed commit; it never cancels a slow-but-progressing transaction.
    __slots__ = (
        "operation", "write_fn", "durable", "future", "busy_timeout_ms",
        "cancelled", "completed", "enqueued_at", "principal", "work_class",
    )

    def __init__(self, operation, write_fn, durable: bool, busy_timeout_ms: int):
        self.operation = str(operation)
        self.write_fn = write_fn
        self.durable = bool(durable)
        self.future = Future() if durable else None
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self.cancelled = threading.Event()
        self.completed = False
        self.enqueued_at = time.monotonic()
        self.principal = str(_CALLER_AGENT_ID.get() or "system")
        name = self.operation.lower()
        self.work_class = ("maintenance" if name.startswith(("prune ", "close stale"))
                           else "control" if any(word in name for word in ("checkpoint", "owner", "read token"))
                           else "bulk")


class _ProgressConnection(sqlite3.Connection):
    """Connection subclass permits a cooperative Python progress callback."""


class SessionWriter:
    """Single-process SQLite writer with bounded queue and grouped commits.

    sessions.db is owned by one [A] process. WAL/busy_timeout remain enabled as a
    defensive boundary for accidental cross-process access, but no second writer
    process is supported. Durable submissions wait for SQLite COMMIT under
    WAL + synchronous=NORMAL: process-crash persistence, not an OS/power-loss
    barrier. Fire-and-forget submissions may drop with a warning and metric.
    """

    def __init__(
        self,
        db_path,
        *,
        capacity: int = _SESSION_WRITE_QUEUE_SIZE,
        batch_max: int = _SESSION_WRITE_BATCH_MAX,
        batch_window_ms: float = _SESSION_WRITE_BATCH_WINDOW_MS,
        busy_timeout_ms: int = _SESSION_DB_BUSY_TIMEOUT_MS,
        checkpoint_every_batches: int | None = None,
        watchdog_interval: float = 0.1,
        stall_seconds: float = _SESSION_WRITE_STALL_SECONDS,
        wedge_multiplier: float = _SESSION_WRITE_WEDGE_MULTIPLIER,
        auto_start: bool = True,
    ):
        self.db_path = Path(db_path)
        self.capacity = max(1, int(capacity))
        self.batch_max = min(64, max(1, int(batch_max)))
        self.batch_window_seconds = max(0.0, float(batch_window_ms) / 1000.0)
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self.checkpoint_every_batches = max(1, int(checkpoint_every_batches)) if checkpoint_every_batches is not None else None
        self.watchdog_interval = max(0.01, float(watchdog_interval))
        self.stall_seconds = max(0.1, float(stall_seconds))
        self.wedge_seconds = self.stall_seconds * max(2.0, float(wedge_multiplier))
        self._queue = queue.Queue(maxsize=self.capacity)
        self._lifecycle_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._closing = threading.Event()
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._thread = None
        self._watchdog = None
        self._active_batch_since = 0.0
        self._last_progress_at = time.monotonic()
        self._failed_reason = ""
        self._stalled_reason = ""
        # True only while _failed_reason came from a wedge verdict, which a later
        # commit disproves. Terminal failures (restart impossible, shutdown
        # timeout) leave this False and are never cleared by progress.
        self._failed_is_wedge = False
        self._batch_count = 0
        self._enqueued_total = 0
        self._committed_total = 0
        self._dropped_count = 0
        self._failed_count = 0
        self._drain_conn: sqlite3.Connection | None = None
        self._restart_count = 0
        self._last_checkpoint_at = time.monotonic()
        self._current_item = None
        self._stall_count = 0
        self._wedge_count = 0
        if auto_start:
            self.start()

    @property
    def is_alive(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    @property
    def failed(self) -> bool:
        return bool(self._failed_reason)

    @property
    def enqueued_total(self) -> int:
        with self._metrics_lock:
            return self._enqueued_total

    @property
    def committed_total(self) -> int:
        with self._metrics_lock:
            return self._committed_total

    @property
    def dropped_count(self) -> int:
        with self._metrics_lock:
            return self._dropped_count

    @property
    def restart_count(self) -> int:
        with self._metrics_lock:
            return self._restart_count

    def metrics(self) -> dict:
        # Health runs on the event loop: never wait for either writer lock.
        if not self._queue.mutex.acquire(blocking=False):
            return {"metrics_stale": True, "reason": "queue_snapshot_busy"}
        try:
            depths = {"control": 0, "bulk": 0, "maintenance": 0}
            for item in self._queue.queue:
                depths[item.work_class] += 1
        finally:
            self._queue.mutex.release()
        now = time.monotonic()
        if not self._metrics_lock.acquire(blocking=False):
            return {"metrics_stale": True, "reason": "writer_snapshot_busy"}
        try:
            item = self._current_item
            return {
                "metrics_stale": False,
                "enqueued_total": self._enqueued_total,
                "committed_total": self._committed_total,
                "dropped_count": self._dropped_count,
                "failed_count": self._failed_count,
                "restart_count": self._restart_count,
                "queue_depth": sum(depths.values()), "queue_depth_by_class": depths,
                "failed": bool(self._failed_reason),
                "failed_reason": self._failed_reason or None,
                "stalled": bool(self._stalled_reason),
                "stalled_reason": self._stalled_reason or None,
                "stall_count": self._stall_count, "wedge_count": self._wedge_count,
                "current_operation": item.operation if item else None,
                "current_principal": item.principal if item else None,
                "current_class": item.work_class if item else None,
                "batch_age_ms": max(0.0, now - self._active_batch_since) * 1000 if self._active_batch_since else 0.0,
                "progress_age_ms": max(0.0, now - self._last_progress_at) * 1000,
            }
        finally:
            self._metrics_lock.release()

    def _start_writer_locked(self) -> None:
        if self._closing.is_set():
            raise RuntimeError("SessionWriter is closing")
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._drain_loop,
            name="session-store-writer",
            daemon=True,
        )
        self._thread.start()

    def start(self) -> None:
        with self._lifecycle_lock:
            self._start_writer_locked()
            if not self._watchdog or not self._watchdog.is_alive():
                self._watchdog = threading.Thread(
                    target=self._watchdog_loop,
                    name="session-store-watchdog",
                    daemon=True,
                )
                self._watchdog.start()

    def _record_progress(self) -> int:
        """A completed item/SQLite VM step is progress; age alone is not a wedge."""
        with self._metrics_lock:
            self._last_progress_at = time.monotonic()
            self._stalled_reason = ""
            if self._failed_is_wedge:
                self._failed_reason = ""
                self._failed_is_wedge = False
        return 0

    def _verdict_is_stale(self, batch_since: float | None) -> bool:
        """True when the batch a watchdog verdict judged has already finished.

        Call with _metrics_lock held. The watchdog reads _active_batch_since
        without that lock, so the batch can commit -- and _clear_stalled run --
        between the read and the latch. A verdict latched then is permanent, not
        transient: a stalled or wedged writer admits no new durable submit and no
        production caller submits fire-and-forget, so no later batch would ever
        run to clear it. The drain loop zeroes _active_batch_since BEFORE
        _clear_stalled takes this lock, so a verdict that still sees its own
        batch here is always cleared afterwards.
        """
        return batch_since is not None and self._active_batch_since != batch_since

    def _mark_failed(self, reason: str, *, wedge: bool = False,
                     batch_since: float | None = None) -> None:
        reason = str(reason or "unknown writer failure")[:500]
        should_log = False
        with self._metrics_lock:
            if self._verdict_is_stale(batch_since):
                return
            if wedge and batch_since is not None and (
                time.monotonic() - max(batch_since, self._last_progress_at) <= self.wedge_seconds
            ):
                return
            if not self._failed_reason:
                self._failed_reason = reason
                self._failed_is_wedge = bool(wedge)
                self._wedge_count += int(wedge)
                should_log = True
        if should_log:
            logger.error("SessionWriter FAILED: %s", reason)

    def _mark_stalled(self, reason: str, *, batch_since: float | None = None) -> None:
        reason = str(reason or "unknown writer stall")[:500]
        should_log = False
        with self._metrics_lock:
            if self._verdict_is_stale(batch_since):
                return
            if batch_since is not None and (
                time.monotonic() - max(batch_since, self._last_progress_at) <= self.stall_seconds
            ):
                return
            if not self._stalled_reason:
                self._stalled_reason = reason
                self._stall_count += 1
                should_log = True
        if should_log:
            logger.warning("SessionWriter STALLED: %s", reason)

    def _clear_stalled(self) -> None:
        with self._metrics_lock:
            self._stalled_reason = ""
            # A commit is direct evidence the wedge verdict was wrong, so retract
            # it. A terminal failure is not retracted -- nothing about finishing
            # one batch says a dead-and-unrestartable writer came back.
            if self._failed_is_wedge:
                self._failed_reason = ""
                self._failed_is_wedge = False

    def _restart_dead_writer(self) -> bool:
        with self._lifecycle_lock:
            if self._closing.is_set():
                return False
            if self._thread and self._thread.is_alive():
                return True
            try:
                self._start_writer_locked()
            except Exception as exc:
                self._mark_failed(f"writer restart failed: {exc}")
                return False
            with self._metrics_lock:
                self._restart_count += 1
                self._failed_reason = ""
                self._failed_is_wedge = False
            logger.warning("SessionWriter watchdog restarted a dead writer thread")
            return True

    def _watchdog_loop(self) -> None:
        while not self._closing.wait(self.watchdog_interval):
            thread = self._thread
            if not thread or not thread.is_alive():
                if not self._restart_dead_writer():
                    self._mark_failed("writer thread died and restart was unavailable")
                continue
            active_since = self._active_batch_since
            if not active_since:
                continue
            stuck_for = time.monotonic() - max(active_since, self._last_progress_at)
            if stuck_for > self.wedge_seconds:
                # A33: the batch is not slow, it is WEDGED. Python cannot kill a
                # live thread and a second writer would break single-writer
                # ownership, so the only honest move is to declare the writer
                # failed. In-flight callers get an unconfirmed outcome; this
                # verdict also rejects new work until progress resumes.
                self._mark_failed(
                    f"writer batch made no observable progress for "
                    f"{stuck_for:.2f}s (> {self.wedge_seconds:.2f}s); "
                    "treating the writer as wedged",
                    wedge=True,
                    batch_since=active_since,
                )
            elif stuck_for > self.stall_seconds:
                # Still plausibly just slow: reject NEW durable submits but let
                # in-flight waiters ride it out, since a stall clears the moment
                # the batch commits.
                self._mark_stalled(
                    f"writer batch made no observable progress for more than "
                    f"{self.stall_seconds:.2f}s",
                    batch_since=active_since,
                )

    def submit(
        self,
        operation,
        write_fn,
        *,
        durable: bool,
        timeout_ms: int | None = None,
        wait_timeout: float | None = None,
    ):
        wait_budget = _SESSION_CALL_WAIT_SECONDS if wait_timeout is None else float(wait_timeout)
        if wait_budget < 0:
            raise ValueError("wait_timeout must be non-negative")
        deadline = time.monotonic() + wait_budget
        lane_deadline = _SESSION_CALL_DEADLINE.get()
        if lane_deadline is not None:
            deadline = min(deadline, lane_deadline)
        if self._closing.is_set():
            raise RuntimeError("SessionWriter is closing")
        if self._failed_reason and not self._failed_is_wedge:
            # Terminal: the writer is gone and no retry can help.
            if durable:
                raise RuntimeError(f"SessionWriter failed: {self._failed_reason}")
            with self._metrics_lock:
                self._dropped_count += 1
            logger.warning(
                "dropping fire-and-forget session write %r: writer failed: %s",
                operation, self._failed_reason,
            )
            return None
        if self._failed_reason and self._failed_is_wedge:
            # A33: a wedge verdict releases WAITERS (see the durable wait loop),
            # but the thread is still alive and may yet drain its batch. New work
            # must not be admitted onto a queue nobody is serving -- yet the
            # verdict is an inference from silence, so this is retryable
            # contention, not the terminal RuntimeError above. Keeping the two
            # distinct is what lets _clear_stalled retract the wedge on the next
            # commit instead of leaving the writer dead for the process lifetime.
            if durable:
                raise SessionStoreContentionError(
                    f"SessionWriter appears wedged: {self._failed_reason}", contention_stage="admission",
                )
            with self._metrics_lock:
                self._dropped_count += 1
            logger.warning(
                "dropping fire-and-forget session write %r: writer wedged: %s",
                operation, self._failed_reason,
            )
            return None
        if self._stalled_reason:
            if durable:
                raise SessionStoreContentionError(
                    f"SessionWriter is temporarily stalled: {self._stalled_reason}", contention_stage="admission",
                )
            # Not spooled, on purpose (2026-09-28). No production caller submits
            # fire-and-forget -- all of them leave durable at its True default,
            # pinned by tests/test_session_writer_stall_contract.py -- and a
            # write_fn is a closure, so a spool would hold nothing it could replay.
            # 48h of logs showed 24 STALLED episodes and 0 drops from this branch.
            with self._metrics_lock:
                self._dropped_count += 1
            logger.warning(
                "dropping fire-and-forget session write %r: writer stalled: %s",
                operation, self._stalled_reason,
            )
            return None
        if not self.is_alive and not self._restart_dead_writer():
            if durable:
                raise RuntimeError("SessionWriter is not alive")
            with self._metrics_lock:
                self._dropped_count += 1
            logger.warning("dropping fire-and-forget session write %r: writer dead", operation)
            return None

        # busy_ms bounds two SQLite-level waits only: how long a full queue may
        # block before backpressure, and (later) PRAGMA busy_timeout for lock
        # contention. It is deliberately NOT a wall-clock ceiling on the caller's
        # wait for COMMIT -- see the durable wait loop below for why.
        busy_ms = max(1, int(timeout_ms or self.busy_timeout_ms))
        item = _WriteItem(operation, write_fn, durable, busy_ms)
        try:
            if durable:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SessionStoreContentionError("session caller budget expired before admission", contention_stage="admission")
                with _session_wait_scope("session_store_admission"):
                    self._queue.put(item, timeout=min(busy_ms / 1000.0, remaining))
            else:
                self._queue.put_nowait(item)
        except queue.Full as exc:
            if durable:
                raise SessionStoreContentionError(
                    f"{operation} exceeded session write queue budget ({busy_ms}ms)", contention_stage="admission",
                ) from exc
            with self._metrics_lock:
                self._dropped_count += 1
            logger.warning(
                "dropping fire-and-forget session write %r: queue full (%d)",
                operation, self.capacity,
            )
            return None

        self._idle.clear()
        with self._metrics_lock:
            self._enqueued_total += 1
        if not durable:
            return None

        # Bound caller occupancy without pretending to cancel a COMMIT that may
        # already be running. The writer owns completion after this waiter leaves.
        poll = min(0.25, max(0.02, self.watchdog_interval * 2))
        with _session_wait_scope("session_store_commit"):
            while True:
                remaining = deadline - time.monotonic()
                try:
                    return item.future.result(timeout=max(0.0, min(poll, remaining)))
                except FutureTimeoutError:
                    pass
                if remaining <= 0 or self._failed_reason:
                    reason = (f"SessionWriter failed: {self._failed_reason}"
                              if self._failed_reason else "caller wait deadline expired")
                    raise SessionStoreCommitPendingError(
                        f"{operation}: {reason}; session commit is not yet confirmed. "
                        "Do not resend the write; inspect write_status and persisted state.",
                    )

    def _take_batch(self) -> list[_WriteItem]:
        # One bounded queue remains the owner of all admitted work. Inspect only
        # the head of each principal so scheduling cannot invert its FIFO order.
        with self._queue.not_empty:
            if not self._queue.queue:
                self._queue.not_empty.wait(0.1)
            if not self._queue.queue:
                return []
            deadline = time.monotonic() + self.batch_window_seconds
            while len(self._queue.queue) < self.batch_max:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._stop.is_set():
                    break
                self._queue.not_empty.wait(remaining)
            batch = []
            served = {}
            while self._queue.queue and len(batch) < self.batch_max:
                heads = {}
                for item in self._queue.queue:
                    heads.setdefault(item.principal, item)
                eligible = list(heads.values())
                if len(heads) > 1:
                    eligible = [item for item in eligible if served.get(item.principal, 0) < 32]
                    if not eligible:
                        break
                now = time.monotonic()
                def rank(item):
                    # Aging guarantees bulk/maintenance progress under a steady
                    # control stream. No scheduler can preempt a running callback.
                    aged = now - item.enqueued_at >= 0.25
                    priority = {"control": 0, "bulk": 1, "maintenance": 2}[item.work_class]
                    return (0 if aged else 1, served.get(item.principal, 0),
                            item.enqueued_at if aged else priority, item.enqueued_at)
                chosen = min(eligible, key=rank)
                self._queue.queue.remove(chosen)
                batch.append(chosen)
                served[chosen.principal] = served.get(chosen.principal, 0) + 1
            self._queue.not_full.notify_all()
            return batch

    def _open_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.busy_timeout_ms / 1000.0,
            isolation_level=None,
            factory=_ProgressConnection,
        )
        _configure_sessions_connection(conn, self.busy_timeout_ms)
        conn.set_progress_handler(self._record_progress, 1000)
        conn.progress_heartbeat = (self._record_progress
                                   if os.environ.get("ORCH_SESSION_PROGRESS_HEARTBEAT", "1") != "0"
                                   else lambda: 0)
        return conn

    @staticmethod
    def _is_locked_error(exc: BaseException) -> bool:
        text = str(exc).lower()
        return isinstance(exc, sqlite3.OperationalError) and (
            "locked" in text or "busy" in text
        )

    def _normalized_error(
        self, item: _WriteItem, exc: BaseException, timeout_ms: int,
    ) -> BaseException:
        if self._is_locked_error(exc):
            return SessionStoreContentionError(
                f"{item.operation} exceeded SQLite busy budget ({timeout_ms}ms): {exc}",
            )
        return exc

    def _finish_item(
        self,
        item: _WriteItem,
        *,
        result=None,
        error: BaseException | None = None,
    ) -> None:
        if item.completed:
            return
        item.completed = True
        with self._metrics_lock:
            if error is None:
                self._committed_total += 1
            else:
                self._failed_count += 1
        if item.future is not None and not item.future.done():
            if error is None:
                item.future.set_result(result)
            else:
                item.future.set_exception(error)

    def _process_batch(self, conn: sqlite3.Connection, batch: list[_WriteItem]) -> bool:
        # Keep grouped commits for equal budgets without letting a short-budget
        # tenant reduce a neighbor's lock-wait allowance.
        groups = []
        for item in batch:
            if not groups or groups[-1][0].busy_timeout_ms != item.busy_timeout_ms:
                groups.append([])
            groups[-1].append(item)
        healthy = True
        for group in groups:
            healthy = self._process_batch_group(conn, group) and healthy
        return healthy

    def _process_batch_group(self, conn, batch):
        active = []
        for item in batch:
            # Cancellation is explicit; caller timeout alone never cancels an
            # item or changes the outcome of a transaction already in progress.
            if item.cancelled.is_set():
                self._finish_item(
                    item,
                    error=SessionStoreContentionError(
                        f"{item.operation} aborted before SQLite transaction",
                    ),
                )
            else:
                active.append(item)
        if not active:
            return True

        # PRAGMA busy_timeout bounds SQLite lock-wait only (real cross-process
        # contention), not the caller's total wait. Use the smallest per-item
        # lock-wait budget in the batch so an explicit small budget still applies.
        timeout_ms = max(1, min(item.busy_timeout_ms for item in active))
        conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
        outcomes: dict[_WriteItem, tuple[object, BaseException | None]] = {}
        began = False
        batch_token = lock_holder_trace.set_current_tool(
            ", ".join(dict.fromkeys(item.operation for item in active))[:500])
        try:
            conn.execute("BEGIN IMMEDIATE")
            began = True
            self._record_progress()
            for index, item in enumerate(active):
                self._current_item = item
                savepoint = f"session_item_{index}"
                conn.execute(f"SAVEPOINT {savepoint}")
                item_token = lock_holder_trace.set_current_tool(item.operation)
                try:
                    # R5: flag-gated slow-write trace. The camera blind spot was
                    # a long write_fn with no owner in the log; time each item
                    # and warn when one runs longer than _SLOW_WRITE_FN_MS.
                    _trace_on = lock_holder_trace.enabled()
                    _t0 = time.monotonic() if _trace_on else 0.0
                    result = item.write_fn(conn)
                    if _trace_on:
                        _elapsed_ms = (time.monotonic() - _t0) * 1000.0
                        if _elapsed_ms > _SLOW_WRITE_FN_MS:
                            logger.warning(
                                "SLOW_WRITE_FN op=%s elapsed_ms=%.0f",
                                item.operation,
                                _elapsed_ms,
                            )
                    if item.cancelled.is_set():
                        raise SessionStoreContentionError(
                            f"{item.operation} aborted mid-transaction (writer failed)",
                        )
                except BaseException as exc:
                    cleanup_error = None
                    try:
                        conn.execute(f"ROLLBACK TO {savepoint}")
                    except BaseException as rollback_exc:
                        cleanup_error = rollback_exc
                    try:
                        conn.execute(f"RELEASE {savepoint}")
                    except BaseException as release_exc:
                        cleanup_error = cleanup_error or release_exc
                    if cleanup_error is not None:
                        raise cleanup_error from exc
                    outcomes[item] = (
                        None,
                        self._normalized_error(item, exc, timeout_ms),
                    )
                else:
                    conn.execute(f"RELEASE {savepoint}")
                    outcomes[item] = (result, None)
                finally:
                    lock_holder_trace.reset_current_tool(item_token)
                    self._record_progress()
            conn.execute("COMMIT")
            self._record_progress()
        except BaseException as batch_exc:
            if conn.in_transaction:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    logger.exception("SessionWriter batch rollback failed")
            if not began and self._is_locked_error(batch_exc) and len(active) > 1:
                # No callback has run, so retrying each member cannot duplicate a
                # write. Each tenant gets an independent acquisition attempt.
                # The failed group BEGIN spent the first item's budget. Do not
                # charge it twice; untouched neighbors get their own attempt.
                self._finish_item(active[0], error=self._normalized_error(
                    active[0], batch_exc, timeout_ms))
                for item in active[1:]:
                    self._process_batch_group(conn, [item])
                return False
            for item in active:
                prior = outcomes.get(item)
                error = prior[1] if prior and prior[1] is not None else self._normalized_error(
                    item, batch_exc, timeout_ms,
                )
                self._finish_item(item, error=error)
            return False
        finally:
            lock_holder_trace.reset_current_tool(batch_token)

        for item in active:
            result, error = outcomes[item]
            self._finish_item(item, result=result, error=error)
        self._batch_count += 1
        now = time.monotonic()
        try:
            wal_bytes = Path(str(self.db_path) + "-wal").stat().st_size
        except OSError:
            wal_bytes = 0
        legacy_due = (self.checkpoint_every_batches is not None
                      and self._batch_count % self.checkpoint_every_batches == 0)
        # Checkpoints cannot shrink uncommitted WAL. Check only after COMMIT;
        # time-gating a size-triggered attempt avoids hammering pinned readers.
        size_due = wal_bytes >= 16 * 1024 * 1024 and now - self._last_checkpoint_at >= 1.0
        if legacy_due or now - self._last_checkpoint_at >= 30.0 or size_due:
            self._last_checkpoint_at = now
            try:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:
                logger.exception("SessionWriter passive WAL checkpoint failed")
        return True

    def _drain_loop(self) -> None:
        conn = None
        try:
            while not self._stop.is_set() or not self._queue.empty():
                batch = self._take_batch()
                if not batch:
                    if self._queue.empty():
                        self._idle.set()
                    continue
                self._active_batch_since = time.monotonic()
                self._current_item = batch[0]
                try:
                    if conn is None:
                        conn = self._open_connection()
                        self._drain_conn = conn
                    healthy = self._process_batch(conn, batch)
                    if not healthy:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None
                except BaseException as exc:
                    logger.exception("SessionWriter drain batch failed; loop will continue")
                    for item in batch:
                        self._finish_item(
                            item,
                            error=self._normalized_error(item, exc, item.busy_timeout_ms),
                        )
                    if conn is not None:
                        try:
                            if conn.in_transaction:
                                conn.rollback()
                            conn.close()
                        except Exception:
                            pass
                        conn = None
                finally:
                    for _item in batch:
                        self._queue.task_done()
                    self._active_batch_since = 0.0
                    self._current_item = None
                    # Retire a verdict about a batch that has ended, even if it
                    # rolled back. This is not a forward-progress timestamp.
                    self._clear_stalled()
                    if self._queue.empty():
                        self._idle.set()
        finally:
            if conn is not None:
                try:
                    if conn.in_transaction:
                        conn.rollback()
                    # During forced shutdown (_closing), switch journal_mode
                    # to DELETE so conn.close() doesn't do an implicit WAL
                    # checkpoint — the checkpoint holds file locks that block
                    # rmtree on Windows and can take > 5 s on HDD.
                    if self._closing.is_set():
                        try:
                            conn.execute("PRAGMA journal_mode=DELETE")
                        except Exception:
                            pass
                    else:
                        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    logger.exception("SessionWriter shutdown checkpoint failed")
                finally:
                    conn.close()
                    self._drain_conn = None
            self._active_batch_since = 0.0
            self._idle.set()

    def close(self, timeout: float = 5.0) -> bool:
        if self._closing.is_set():
            thread = self._thread
            return not thread or not thread.is_alive()
        self._closing.set()
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
        alive = bool(thread and thread.is_alive())
        if alive:
            self._mark_failed("writer did not flush before shutdown timeout")
        else:
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                self._finish_item(
                    item, error=RuntimeError("SessionWriter closed before write committed"),
                )
                self._queue.task_done()
        watchdog = self._watchdog
        if watchdog and watchdog.is_alive() and watchdog is not threading.current_thread():
            watchdog.join(timeout=min(0.25, max(0.0, float(timeout))))
        return not alive


def _enable_wal(conn: sqlite3.Connection) -> bool:
    """Switch one connection to WAL, tolerating a contended switch.

    campaign-1608 (A27). ``PRAGMA journal_mode=WAL`` takes an EXCLUSIVE lock on
    the database, and SQLite does NOT apply ``busy_timeout`` to it -- the switch
    fails instantly with "database is locked" instead of waiting. Raising the
    busy budget therefore buys nothing. Two threads entering ``_init_session_db``
    on a fresh sessions.db (open_session calls it per request, and the early-out
    only fires once a live writer exists) made the loser die with a bare
    ``INTERNAL_ERROR: database is locked``, which no contention guard in this
    module wraps because it happens off the serialized writer path.

    Retry briefly, then give up: the FIRST connection to win the race already
    put the database into WAL, and journal mode is a persistent database
    property, so a loser that gives up still ends up on a WAL database. Returns
    whether this connection observed WAL, for callers that want to log it.
    """
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(_WAL_SWITCH_ATTEMPTS):
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            return bool(mode) and str(mode[0]).lower() == "wal"
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            last_exc = exc
            time.sleep(_WAL_SWITCH_RETRY_MS / 1000.0 * (attempt + 1))
    try:
        current = conn.execute("PRAGMA journal_mode").fetchone()
    except sqlite3.OperationalError:
        current = None
    already_wal = bool(current) and str(current[0]).lower() == "wal"
    if not already_wal:
        # Not fatal -- a rollback-journal database still works, just with less
        # read/write concurrency. Say so instead of failing the caller's request.
        logger.warning(
            "sessions.db stayed on journal_mode=%s; WAL switch contended (%s)",
            (current[0] if current else "unknown"), last_exc,
        )
    return already_wal


def _configure_sessions_connection(conn: sqlite3.Connection, busy_timeout_ms: int) -> None:
    timeout_ms = max(1, int(busy_timeout_ms))
    conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
    _enable_wal(conn)
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-16000")
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    conn.execute("PRAGMA journal_size_limit=67108864")


_SESSION_READ_LOCAL = threading.local()
_SESSION_WRITER_SWAP_LOCK = threading.Lock()
# STT-27 (B032): track all open read connections so close_writer() can close them
# across all threads, not just the calling thread. Daemon threads (asyncio_0,
# orchestrator-accounting, Thread-1) hold thread-local conns that survive
# close_writer() otherwise, pinning sessions.db and blocking rmtree on Windows.
# Plain set (sqlite3.Connection is not weakly-referenceable on CPython 3.12).
# Cleanup relies on close_writer() + gc.collect() + _TEARDOWN_COMPLETE flag.
_ALL_READ_CONNECTIONS: set[sqlite3.Connection] = set()
_ALL_READ_CONNECTIONS_LOCK = threading.Lock()
# STT-27 (B032): after close_writer() sets this flag, _session_read_connection()
# returns an in-memory connection instead of opening the real DB. Daemon threads
# (asyncio_0, orchestrator-accounting, Thread-1) can create new read connections
# AFTER close_writer() closes all existing ones, and those new connections pin
# sessions.db and block rmtree. The flag prevents that.
_TEARDOWN_COMPLETE = False
# STT-27 (B032): track the DB path when teardown completes. When a new test starts
# with a different DB path, reset the flag so _session_read_connection() returns
# real connections again.
_TEARDOWN_DB_PATH: str | None = None
# campaign-1608 (A38): the process-wide writer is created LAZILY on first DB use
# by _get_session_writer, not at import. Declared here so ``globals().get`` and
# the ``global`` rebind in _get_session_writer both see a defined name.
_SESSION_WRITER: SessionWriter | None = None


def _shutdown_session_writer() -> None:
    """Close the process-global writer AND drop the reference to it.

    Clearing the global is not tidiness, it is the contract. Two hot paths
    decide what to do by comparing ``_SESSION_WRITER.db_path`` against the
    current ``_SESSIONS_DB``: ``_get_session_writer`` (reuse vs rebuild) and
    ``_init_session_db`` (skip the DDL when a live writer already owns this
    path). Leaving a *closed* writer in the global makes both of them reason
    about a corpse, and against a path that may no longer exist on disk.

    Observed 2026-09-09 in tests/test_orchestrator_server_contract.py: after a
    sessions.db swap, ``_SESSION_WRITER`` still pointed at the previous temp
    directory with ``_closing`` already set. A ``_log_session_event`` racing
    that state submitted into the dead writer and drained into a database with
    no schema -- `sqlite3.OperationalError: no such table: events` -- which
    then held a handle open and failed the TemporaryDirectory cleanup with
    WinError 32. Nothing was wrong with the swap; the stale global was the bug.

    ``None`` is the honest value for "there is no writer". Both callers already
    handle it: they build a fresh one bound to whatever ``_SESSIONS_DB`` says
    now, after running the schema check for that path.
    """
    global _SESSION_WRITER
    writer = globals().get("_SESSION_WRITER")
    if writer is not None:
        try:
            writer.close(timeout=5.0)
        except Exception:
            logger.exception("SessionWriter shutdown failed")
        finally:
            # Drop the reference even if close() raised: a writer we failed to
            # shut down cleanly is the last thing that should stay reachable as
            # the process-global "current" writer.
            _SESSION_WRITER = None


def close_writer() -> None:
    """Release every process-global sessions.db handle for a clean teardown.

    Public teardown helper so tests (and any caller swapping ``_SESSIONS_DB`` to
    a temp path) do not have to reach for the private ``_shutdown_session_writer``
    name or know the read-connection internals. It:

    1. Closes the SessionWriter drain-loop connection.
    2. Closes the read connection cached in this thread's ``_SESSION_READ_LOCAL``.
    3. Forces a ``gc.collect()`` so read connections cached on dead
       ``asyncio.to_thread`` worker threads (whose thread-locals are only released
       on GC) are finalized too.

    Step 3 matters on Windows: without it, an open sqlite handle on a now-dead
    worker thread blocks ``shutil.rmtree`` of a TemporaryDirectory with
    WinError 32 even after the writer and the main-thread read conn are closed.
    """
    # Save writer ref before _shutdown_session_writer clears the global.
    writer_ref = globals().get("_SESSION_WRITER")
    _shutdown_session_writer()
    # Explicitly close the writer's drain-loop connection if still open
    # (the drain-loop thread may not exit within its join timeout on Windows).
    if writer_ref is not None:
        drain = getattr(writer_ref, "_drain_conn", None)
        if drain is not None:
            try:
                # STT-27 (B032): interrupt any pending SQLite operation
                # (e.g. wal_checkpoint) so the handle releases on Windows.
                drain.interrupt()
            except Exception:
                pass
            try:
                drain.close()
            except Exception:
                pass
            writer_ref._drain_conn = None
    read_local = globals().get("_SESSION_READ_LOCAL")
    if read_local is not None:
        conn = getattr(read_local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            read_local.conn = None
            read_local.db_path = None
    # Explicitly close every tracked read connection (plain set, not WeakSet,
    # because sqlite3.Connection is not weakly-referenceable on CPython 3.12).
    # This covers connections cached on dead asyncio.to_thread worker threads
    # that gc.collect() alone may not finalize in time on Windows.
    with _ALL_READ_CONNECTIONS_LOCK:
        for tracked_conn in list(_ALL_READ_CONNECTIONS):
            try:
                tracked_conn.close()
            except Exception:
                pass
        _ALL_READ_CONNECTIONS.clear()
    # Also collect any remaining thread-local connections via gc.
    gc.collect()
    # STT-27 (B032): mark teardown complete so daemon threads that create NEW
    # read connections after this point get in-memory connections instead of
    # pinning sessions.db. Track the DB path so we can reset the flag when a
    # new test starts with a different DB.
    global _TEARDOWN_COMPLETE, _TEARDOWN_DB_PATH
    _TEARDOWN_COMPLETE = True
    _TEARDOWN_DB_PATH = str(Path(_SESSIONS_DB))


def _get_session_writer() -> SessionWriter:
    global _SESSION_WRITER, _TEARDOWN_COMPLETE
    wanted = Path(_SESSIONS_DB)
    writer = globals().get("_SESSION_WRITER")
    if writer is not None and writer.db_path == wanted and not writer._closing.is_set():
        return writer
    with _SESSION_WRITER_SWAP_LOCK:
        writer = globals().get("_SESSION_WRITER")
        if writer is not None and writer.db_path == wanted and not writer._closing.is_set():
            return writer
        if writer is not None and not writer.close(timeout=5.0):
            # close() has already requested shutdown. Keep the slot occupied
            # until its drain thread really exits; never create a second writer.
            raise SessionStoreContentionError(
                "Previous SessionWriter has not stopped; replacement refused",
                contention_stage="admission")
        if (
            os.path.normcase(os.path.abspath(str(wanted)))
            not in _SESSION_DB_INITIALIZED_PATHS
        ):
            _init_session_db()
        _SESSION_WRITER = SessionWriter(wanted)
        # STT-27 (B032): reset teardown flag when creating a new writer, so
        # _session_read_connection() returns real connections again.
        _TEARDOWN_COMPLETE = False
        return _SESSION_WRITER


def _session_read_connection() -> sqlite3.Connection:
    """Return a sqlite3 connection to the sessions database.

    Thread-local: each thread gets its own connection. If teardown has completed
    (close_writer() was called) and no new writer has been created, returns an
    in-memory connection instead of opening the real DB — this prevents daemon
    threads from creating new handles that pin sessions.db after teardown and
    block rmtree.
    """
    global _TEARDOWN_COMPLETE
    if _TEARDOWN_COMPLETE:
        wanted_key = os.path.normcase(os.path.abspath(str(_SESSIONS_DB)))
        teardown_key = (
            os.path.normcase(os.path.abspath(_TEARDOWN_DB_PATH))
            if _TEARDOWN_DB_PATH is not None else wanted_key
        )
        writer = globals().get("_SESSION_WRITER")
        resumed_writer = (
            writer is not None and not writer._closing.is_set()
            and os.path.normcase(os.path.abspath(str(writer.db_path))) == wanted_key
        )
        # A late reader is not permission to reopen the DB we just released.
        # A different DB or an explicitly created writer starts a new lifecycle.
        if wanted_key != teardown_key or resumed_writer:
            _TEARDOWN_COMPLETE = False
        else:
            return sqlite3.connect(":memory:")

    _get_session_writer()
    wanted = str(Path(_SESSIONS_DB))
    conn = getattr(_SESSION_READ_LOCAL, "conn", None)
    current_path = getattr(_SESSION_READ_LOCAL, "db_path", None)
    if conn is not None and current_path != wanted:
        try:
            conn.close()
        except Exception:
            pass
        # STT-27 (B032): remove from tracking set before discarding.
        with _ALL_READ_CONNECTIONS_LOCK:
            _ALL_READ_CONNECTIONS.discard(conn)
        conn = None
    if conn is None:
        # check_same_thread=False: connections tracked in _ALL_READ_CONNECTIONS
        # may be closed from the main thread during teardown (close_writer),
        # even though they were created on asyncio worker threads.
        conn = sqlite3.connect(
            wanted,
            timeout=_SESSION_DB_BUSY_TIMEOUT_MS / 1000.0,
            check_same_thread=False,
        )
        conn.execute(f"PRAGMA busy_timeout={_SESSION_DB_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA cache_size=-16000")
        conn.execute("PRAGMA mmap_size=268435456")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA query_only=ON")
        _SESSION_READ_LOCAL.conn = conn
        _SESSION_READ_LOCAL.db_path = wanted
        # STT-27 (B032): track this connection so close_writer() can close it
        # even if it lives on a daemon thread that outlives the calling thread.
        with _ALL_READ_CONNECTIONS_LOCK:
            _ALL_READ_CONNECTIONS.add(conn)
    return conn


@_contextmanager
def _with_sessions_db(*, busy_timeout_ms: int = _SESSION_DB_BUSY_TIMEOUT_MS):
    """Legacy scoped read helper; all writes must use ``_run_session_write``."""
    _get_session_writer()
    timeout_ms = max(1, int(busy_timeout_ms))
    conn = sqlite3.connect(str(_SESSIONS_DB), timeout=timeout_ms / 1000.0)
    conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
    conn.execute("PRAGMA cache_size=-16000")
    conn.execute("PRAGMA mmap_size=268435456")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA query_only=ON")
    try:
        yield conn
    finally:
        if conn.in_transaction:
            try:
                conn.rollback()
            except Exception:
                logger.exception("session database rollback failed while closing connection")
        conn.close()


def _run_session_write(
    operation: str,
    write_fn,
    *,
    busy_timeout_ms: int = _SESSION_DB_BUSY_TIMEOUT_MS,
    durable: bool = True,
):
    """Submit one write to the process-wide writer; durable calls wait for COMMIT."""
    with _session_wait_scope("session_store_init"):
        writer = _get_session_writer()
    return writer.submit(
        operation,
        write_fn,
        durable=durable,
        timeout_ms=busy_timeout_ms,
    )


def _apply_session_schema(conn: sqlite3.Connection) -> None:
    """Run every idempotent migration statement against ``conn`` and COMMIT.

    campaign-1608 (A38): split out of ``_init_session_db`` so the whole schema
    pass can be RETRIED on a lock-contended connection. Every statement here is
    idempotent (``CREATE ... IF NOT EXISTS`` / ``ALTER`` guarded on duplicate
    column), so re-running a partially applied migration is safe.
    """
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id    TEXT PRIMARY KEY,
                session_title TEXT NOT NULL,
                project_path  TEXT,
                mode          TEXT DEFAULT 'T1',
                status        TEXT DEFAULT 'active',
                coverage      TEXT DEFAULT 'A-only',
                started_at    TEXT,
                ended_at      TEXT,
                agent_id      TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id       TEXT NOT NULL,
                tool_name        TEXT,
                tool_origin      TEXT DEFAULT 'A',
                action_type      TEXT,
                status           TEXT,
                target           TEXT,
                range_label      TEXT,
                duration_ms      INTEGER,
                reason           TEXT,
                natural_log_line TEXT,
                created_at       TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        """)
        # Session identity migration (idempotent): legacy rows remain unscoped.
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN agent_id TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        # Task 2 migration: retain the deterministically latest scoped active row
        # (started_at, then session_id), close older duplicates without deleting
        # history, and enforce one active row per non-null agent scope. NULL agent
        # rows intentionally remain outside the partial unique index.
        migration_now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """UPDATE sessions AS loser
               SET status='closed', ended_at=COALESCE(ended_at, ?)
               WHERE loser.status='active' AND loser.agent_id IS NOT NULL
                 AND EXISTS (
                     SELECT 1 FROM sessions AS winner
                     WHERE winner.status='active'
                       AND winner.agent_id=loser.agent_id
                       AND winner.project_path=loser.project_path
                       AND (
                           COALESCE(winner.started_at, '') > COALESCE(loser.started_at, '')
                           OR (
                               COALESCE(winner.started_at, '') = COALESCE(loser.started_at, '')
                               AND winner.session_id > loser.session_id
                           )
                       )
                 )""",
            (migration_now,),
        )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_active_project_agent
               ON sessions(project_path, agent_id)
               WHERE status='active' AND agent_id IS NOT NULL""",
        )
        # Explicit error-resolution metadata. Resolution rows are append-only and
        # point to one exact error event; derived state always uses the latest row.
        for _mig in (
                "ALTER TABLE events ADD COLUMN outcome TEXT",
                "ALTER TABLE events ADD COLUMN queue_wait_ms INTEGER",
                "ALTER TABLE events ADD COLUMN host_session_id TEXT",
                "ALTER TABLE events ADD COLUMN task_epoch INTEGER",
                "ALTER TABLE events ADD COLUMN prompt_hash TEXT",
                "ALTER TABLE events ADD COLUMN agent_id TEXT",
                "ALTER TABLE events ADD COLUMN audit_id TEXT",
                "ALTER TABLE events ADD COLUMN result_json TEXT",
                "ALTER TABLE events ADD COLUMN event_kind TEXT",
                "ALTER TABLE events ADD COLUMN resolves_event_id INTEGER",
                "ALTER TABLE events ADD COLUMN resolution_kind TEXT",
                "ALTER TABLE events ADD COLUMN error_owner TEXT",
                "ALTER TABLE events ADD COLUMN error_name TEXT",
                "ALTER TABLE events ADD COLUMN correlation_type TEXT",
                "ALTER TABLE events ADD COLUMN correlation_key TEXT",
                "ALTER TABLE events ADD COLUMN evidence_json TEXT"):
            try:
                conn.execute(_mig)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_host_session "
            "ON events(host_session_id, id)",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_resolution_target "
            "ON events(resolves_event_id, id) WHERE resolves_event_id IS NOT NULL",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_error_scope "
            "ON events(session_id, event_kind, id)",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_correlation "
            "ON events(correlation_type, correlation_key, id) "
            "WHERE correlation_key IS NOT NULL",
        )
        # WHY: durable outbox replay is at-least-once; the sink must make one
        # session event per audit_id even after a crash between sink and ACK.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_events_audit_id "
            "ON events(audit_id) WHERE audit_id IS NOT NULL",
        )
        # CAN-052: read-token table — pure-read seeding target (read_file_range
        # no longer rewrites INDEX.json / PROJECT_MAP on every read).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS read_tokens (
                project_key      TEXT NOT NULL,
                rel_path         TEXT NOT NULL,
                read_hash        TEXT,
                read_mtime       REAL,
                read_size_bytes  INTEGER,
                updated_at       TEXT,
                PRIMARY KEY (project_key, rel_path)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_checkpoints (
                session_key TEXT PRIMARY KEY,
                payload     TEXT,
                updated_at  TEXT
            )
        """)
        # campaign-1608 (A27): redundant with _configure_sessions_connection above,
        # kept because older databases reached this point without it. Routed through
        # the shared helper so a contended switch retries and logs instead of being
        # swallowed by a bare `except: pass`.
        _enable_wal(conn)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_audits (
                pending_audit_id TEXT PRIMARY KEY,
                project_path      TEXT NOT NULL,
                action_type       TEXT,
                tool_name         TEXT,
                instead_of        TEXT,
                failed_error      TEXT,
                target            TEXT,
                reason            TEXT,
                sync_status       TEXT DEFAULT 'pending',
                session_id        TEXT,
                created_at        TEXT,
                synced_at         TEXT,
                agent_id          TEXT
            )
        """)
        # Task 2 migration: legacy pending rows remain unscoped (NULL), while
        # newly created rows can be isolated by the same agent identity as sessions.
        try:
            conn.execute("ALTER TABLE pending_audits ADD COLUMN agent_id TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_pending_audits_project_agent_status "
            "ON pending_audits(project_path, agent_id, sync_status)",
        )
        # Persistent write-status ledger. Only bounded metadata/result summaries
        # are stored; request bodies, source content, scripts and tokens are not.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS write_operations (
                operation_id      TEXT PRIMARY KEY,
                tool              TEXT NOT NULL,
                status            TEXT NOT NULL,
                project_path      TEXT,
                file_path         TEXT,
                session_id        TEXT,
                started_at        REAL NOT NULL,
                finished_at       REAL,
                duration_ms       INTEGER,
                attempts          INTEGER NOT NULL DEFAULT 1,
                previous_status   TEXT,
                result_json       TEXT,
                origin_generation TEXT NOT NULL,
                updated_at        REAL NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_write_operations_target_updated "
            "ON write_operations(project_path, file_path, updated_at DESC)",
        )
        # Retention predicates need leading indexes, including cascade lookups.
        for statement in (
            "CREATE INDEX IF NOT EXISTS ix_sessions_started ON sessions(started_at, session_id)",
            "CREATE INDEX IF NOT EXISTS ix_pending_audits_session ON pending_audits(session_id)",
            "CREATE INDEX IF NOT EXISTS ix_write_operations_session ON write_operations(session_id)",
            "CREATE INDEX IF NOT EXISTS ix_write_operations_updated ON write_operations(updated_at)",
            "CREATE INDEX IF NOT EXISTS ix_read_tokens_updated ON read_tokens(updated_at)",
        ):
            conn.execute(statement)
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            try:
                conn.rollback()
            except Exception:
                logger.exception("session schema rollback failed")
        raise


def _init_session_db():
    """Run idempotent migrations before a writer opens the target database.

    Lazy + idempotent + thread-safe (A38):

    * No longer runs at import time. The module-level call was an import-time
      side effect: merely ``import``-ing this module (a test, a script, or a
      SECOND [A] process -- A28 confirmed MCP is one) touched sessions.db, and
      the migration DDL below carries only ``PRAGMA busy_timeout``. A second
      process holding the write lock past that budget made the *import itself*
      raise ``sqlite3.OperationalError: database is locked`` (repro: line ~866).
    * ``_get_session_writer`` already calls this lazily under
      ``_SESSION_WRITER_SWAP_LOCK`` before creating the writer, so every runtime
      DB access still initializes the schema on first use -- behavior for
      existing callers is unchanged.
    * Serialized by ``_SESSION_INIT_LOCK`` so two threads never run the DDL on a
      fresh DB at once (the A27 in-process race), and short-circuited by
      ``_SESSION_DB_INITIALIZED_PATHS`` so repeat calls are O(1).
    * The whole schema pass is retried on lock contention (A28: a second process
      can hold the write lock), mirroring the A27 ``_enable_wal`` loop, then
      fails LOUD -- the schema is mandatory and, unlike WAL, cannot degrade.
    """
    existing_writer = globals().get("_SESSION_WRITER")
    if (
        existing_writer is not None
        and existing_writer.db_path == Path(_SESSIONS_DB)
        and not existing_writer._closing.is_set()
    ):
        return
    db_key = os.path.normcase(os.path.abspath(str(_SESSIONS_DB)))
    if db_key in _SESSION_DB_INITIALIZED_PATHS:
        return
    with _SESSION_INIT_LOCK:
        # Re-check under the lock: another thread may have finished the migration
        # while we waited, so we neither redo the DDL nor re-enter the retry loop.
        if db_key in _SESSION_DB_INITIALIZED_PATHS:
            return
        failed_until, failure = _SESSION_INIT_FAILURES.get(db_key, (0.0, ""))
        if time.monotonic() < failed_until:
            raise SessionStoreContentionError(failure)
        _SESSIONS_DB.parent.mkdir(parents=True, exist_ok=True)
        last_exc: sqlite3.OperationalError | None = None
        for attempt in range(_SESSION_MIGRATION_ATTEMPTS):
            conn = sqlite3.connect(str(_SESSIONS_DB))
            try:
                _configure_sessions_connection(conn, _SESSION_DB_BUSY_TIMEOUT_MS)
                _apply_session_schema(conn)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                last_exc = exc
                delay_ms = min(
                    _SESSION_DB_RETRY_MAX_MS,
                    _SESSION_DB_RETRY_BASE_MS * (2 ** attempt),
                )
                time.sleep(delay_ms / 1000.0)
                continue
            else:
                _SESSION_INIT_FAILURES.pop(db_key, None)
                _SESSION_DB_INITIALIZED_PATHS.add(db_key)
                return
            finally:
                conn.close()
        # Exhausted the retry budget under a still-held lock. The schema is
        # mandatory, so surface it LOUD instead of leaving a half-checked DB.
        failure = (f"session schema migration could not acquire the database lock after "
                   f"{_SESSION_MIGRATION_ATTEMPTS} attempts: {last_exc}")
        _SESSION_INIT_FAILURES[db_key] = (time.monotonic() + 2.0, failure)
        raise SessionStoreContentionError(failure)


# --------------------------------------------------------------------------- #
# campaign-1608 (A22): sessions.db retention.
# --------------------------------------------------------------------------- #
def _session_retention_days() -> int:
    """Return the LIVE retention window in days (re-read from the environment).

    A13-style live read: the value is fetched from ``os.environ`` on every call,
    never snapshotted at import. An unset / blank / non-integer / non-positive
    value falls back to the 10-day default (the user-chosen window). Callers must
    NOT cache this across calls.
    """
    raw = os.environ.get(_SESSION_RETENTION_DAYS_ENV, "")
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return _SESSION_RETENTION_DAYS_DEFAULT
    if val <= 0:
        # A zero/negative window would mean "prune everything up to now", a foot-gun
        # for a destructive op; treat it as "misconfigured" and use the safe default.
        return _SESSION_RETENTION_DAYS_DEFAULT
    return val


def _retention_cutoffs(days: int) -> tuple[str, float]:
    """Return ``(cutoff_iso, cutoff_epoch)`` for a retention window of ``days``.

    THE central defence for the A22 REAL-vs-TEXT trap. sessions.db mixes two
    timestamp encodings in the SAME database:

      * TEXT ISO-8601 (``sessions.started_at``, ``events.created_at``,
        ``read_tokens.updated_at``, ``task_checkpoints.updated_at``,
        ``pending_audits.created_at``)
      * REAL epoch seconds (``write_operations.started_at`` / ``updated_at``)

    SQLite compares across storage classes by class rank (REAL < TEXT always),
    so comparing a REAL epoch column against an ISO *string* silently matches
    EVERY row -- a retention query written that way would wipe the whole
    ``write_operations`` table. Every caller MUST take the epoch cutoff from this
    helper for REAL columns and the ISO cutoff for TEXT columns; returning both
    from one place makes the correct pairing the path of least resistance.
    """
    cutoff_dt = datetime.now() - timedelta(days=max(0, int(days)))
    return cutoff_dt.isoformat(), cutoff_dt.timestamp()


def prune_sessions_older_than(
    days: int | None = None,
    *,
    prune_orphans: bool = False,
    busy_timeout_ms: int = 30000,
    chunk_size: int = 500,
) -> dict:
    """Prune bounded transactions, yielding the writer between committed chunks.

    Deleted rows themselves are the durable cursor: restart selects the oldest
    remaining key. A large session's children are chunked too; its parent is
    removed only in the transaction that observes every child table empty.
    REAL ledger timestamps use the epoch cutoff; other tables use ISO text.
    Undated sessions and pre-existing orphans are retained unless opted in.
    No VACUUM or live-database maintenance is implied by importing this module.
    """
    window = _session_retention_days() if days is None else max(0, int(days))
    cutoff_iso, cutoff_epoch = _retention_cutoffs(window)
    limit = min(2000, max(1, int(chunk_size)))
    children = (("events", "session_id"), ("pending_audits", "session_id"),
                ("task_checkpoints", "session_key"), ("write_operations", "session_id"))
    stats = {name: 0 for name in ("sessions", "events", "pending_audits", "task_checkpoints",
                                 "write_operations", "read_tokens")}
    chunks = 0

    def _cascade_chunk(conn):
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE started_at IS NOT NULL "
            "AND started_at != '' AND started_at < ? ORDER BY started_at, session_id LIMIT 1",
            (cutoff_iso,)).fetchone()
        if row is None:
            return None
        key = row[0]
        counts = {}
        for table, column in children:
            counts[table] = conn.execute(
                f"DELETE FROM {table} WHERE rowid IN "
                f"(SELECT rowid FROM {table} WHERE {column}=? LIMIT ?)", (key, limit)).rowcount
        # Another process cannot insert a child between this check and DELETE:
        # both execute under this writer item's BEGIN IMMEDIATE transaction.
        empty = all(conn.execute(f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1",
                                 (key,)).fetchone() is None for table, column in children)
        counts["sessions"] = conn.execute(
            "DELETE FROM sessions WHERE session_id=?", (key,)).rowcount if empty else 0
        return counts

    while True:
        counts = _run_session_write("prune sessions retention chunk", _cascade_chunk,
                                    busy_timeout_ms=busy_timeout_ms)
        if counts is None:
            break
        chunks += 1
        for table, count in counts.items():
            stats[table] += count

    predicates = [
        ("write_operations", "write_operations", "updated_at < ?", (cutoff_epoch,)),
        ("read_tokens", "read_tokens", "updated_at IS NOT NULL AND updated_at != '' AND updated_at < ?", (cutoff_iso,)),
    ]
    if prune_orphans:
        for table, column in children:
            predicates.append((table, "orphans_" + table,
                               f"{column} IS NOT NULL AND {column} != '' AND "
                               f"{column} NOT IN (SELECT session_id FROM sessions)", ()))
    for table, counter, predicate, parameters in predicates:
        stats.setdefault(counter, 0)
        def _age_chunk(conn):
            return conn.execute(f"DELETE FROM {table} WHERE rowid IN "
                                f"(SELECT rowid FROM {table} WHERE {predicate} LIMIT ?)",
                                (*parameters, limit)).rowcount
        while True:
            count = _run_session_write("prune sessions retention chunk", _age_chunk,
                                       busy_timeout_ms=busy_timeout_ms)
            stats[counter] += count
            if count == 0:
                break
            chunks += 1
    return {"retention_days": window, "cutoff_iso": cutoff_iso,
            "cutoff_epoch": cutoff_epoch, "orphans_pruned": bool(prune_orphans),
            "committed_chunks": chunks, **stats}


def vacuum_sessions_db(*, busy_timeout_ms: int = 60000) -> dict:
    """VACUUM sessions.db to reclaim space after a large prune (opt-in, separate).

    VACUUM cannot run inside a transaction, so it CANNOT go through the writer's
    BEGIN IMMEDIATE batch; it opens its own short-lived connection instead. It
    takes an EXCLUSIVE lock and rewrites the whole file, so it is deliberately a
    distinct, caller-invoked step -- never folded into
    :func:`prune_sessions_older_than`. Best run when no writes are in flight.
    """
    _get_session_writer()  # ensure schema exists / path is initialized
    timeout_ms = max(1, int(busy_timeout_ms))
    conn = sqlite3.connect(str(_SESSIONS_DB), timeout=timeout_ms / 1000.0)
    try:
        conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
        conn.execute("VACUUM")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


atexit.register(_shutdown_session_writer)


TOOL_ACTION_MAP = {
    "read_file_range": ("📄", "read", "👁️"),
    "find_symbol": ("🎯", "read", "👁️"),
    "search_code": ("🔍", "read", "👁️"),
    "query_index": ("🗺️", "read", "👁️"),
    "get_project_summary": ("🗺️", "read", "👁️"),
    "get_file_symbols": ("📄", "read", "👁️"),
    "list_structure": ("📁", "read", "👁️"),
    "write_fix": ("✏️", "write", "⌨️"),
    "write_fix_html": ("🌐", "write", "⌨️"),
    "write_new_file": ("✏️", "write", "⌨️"),
    "delete_file": ("🗑️", "write", "⌨️"),
    "run_project_command": ("▶️", "execute", "🔧"),
    "run_project_test_script": ("🧪", "validate", "✔"),
    "run_test_profile": ("🧪", "validate", "✔"),
    "validate_file": ("✅", "validate", "✔"),
    "validate_symbols": ("✅", "validate", "✔"),
    "post_write_batch_verify": ("✅", "validate", "✔"),
    "cache_delta": ("🗃️", "cache", "👁️"),
    "cache_status": ("🗃️", "cache", "👁️"),
    "generate_project_map": ("🗃️", "cache", "🔧"),
    "refresh_project_map": ("🗃️", "cache", "🔧"),
    "update_file_cache": ("🗃️", "cache", "🔧"),
    "backup_project": ("💾", "backup", "💾"),
    "backup_status": ("💾", "backup", "💾"),
    "prepare_ca_session": ("🧭", "workflow", "🔧"),
    "set_workflow_mode": ("🧭", "workflow", "🔧"),
    "workflow_status": ("🧭", "workflow", "👁️"),
    "workflow_status_registry": ("🧭", "workflow", "👁️"),
    "next_recommended_action": ("🧭", "workflow", "🔧"),
    "open_session": ("🧭", "workflow", "🔧"),
    "close_session": ("🧭", "workflow", "🔧"),
    "log_native_event": ("🔗", "workflow", "🔧"),
    "log_native": ("🔗", "workflow", "🔧"),
    "create_pending_audit": ("🔗", "workflow", "🔧"),
    "sync_pending_audit": ("🔗", "workflow", "🔧"),
    "optional_tool_probe": ("🔎", "read", "👁️"),
}


_VALIDATION_COMMAND_MARKERS = (
    " -m pytest ", " -m unittest ", " -m py_compile ", " -m compileall ",
    " pytest ", " unittest ", " py_compile ", " compileall ",
    " npm test ", " npm run test ", " pnpm test ", " pnpm run test ",
    " yarn test ", " yarn run test ", " npx jest ", " npx vitest ",
    " go test ", " cargo test ", " dotnet test ", " mvn test ",
    " gradle test ", " gradlew test ",
)


def _run_project_command_is_validation(body: dict) -> bool:
    """Recognize explicit test/compile commands without relabeling every process run."""
    raw = body.get("command", "")
    if isinstance(raw, (list, tuple)):
        raw = " ".join(str(part) for part in raw)
    else:
        raw = str(raw or "")
    if str(body.get("command_encoding") or "plain").lower() == "base64":
        try:
            raw = base64.b64decode(raw, validate=True).decode("utf-8", "replace")
        except (ValueError, TypeError):
            return False
    normalized = " ".join(raw.lower().split())
    padded = f" {normalized} "
    return any(marker in padded for marker in _VALIDATION_COMMAND_MARKERS)


def _make_natural_log_line(tool_name: str, target: str, range_label: str, duration_ms: int, _status: str) -> str:
    tool_icon, _, _ = TOOL_ACTION_MAP.get(tool_name, ("🔧", "workflow", "🔧"))
    _, action_type, target_icon = TOOL_ACTION_MAP.get(tool_name, ("🔧", "workflow", "🔧"))
    parts = [f"{tool_icon} {tool_name}"]
    if target:
        parts.append(f"{target_icon} {target}")
    if range_label:
        parts.append(f"🧷 {range_label}")
    parts.append(f"⏱️ {duration_ms}ms")
    return " | ".join(parts)


def _session_started_today(started_at: str) -> bool:
    if not started_at:
        return True
    try:
        return datetime.fromisoformat(str(started_at)).date() == datetime.now().date()
    except Exception:
        return True


def _session_active_today(conn, session_id: str) -> bool:
    """CASE-1909-H2: a session USED today is never stale, whatever calendar day it
    was opened on. The calendar-only test closed live sessions across midnight --
    measured: two other agents' active sessions swept by a third agent's open, all
    three carrying an identical ended_at. Activity decides, not the date."""
    try:
        row = conn.execute(
            "SELECT 1 FROM events WHERE session_id=? AND created_at>=? LIMIT 1",
            (session_id, datetime.now().date().isoformat()),
        ).fetchone()
        return row is not None
    except Exception:
        # Fail-safe: a session we cannot measure is never closed.
        return True


def _close_stale_active_sessions(project_path: str) -> int:
    try:
        norm = _session_project_key(project_path)
        cursor = ""
        total = 0
        while True:
            rows = _session_read_connection().execute(
                "SELECT session_id, started_at FROM sessions "
                "WHERE project_path=? AND status='active' AND session_id>? "
                "ORDER BY session_id LIMIT 100", (norm, cursor)).fetchall()
            if not rows:
                return total
            cursor = rows[-1][0]
            stale_ids = tuple(sid for sid, started in rows if not _session_started_today(started))
            if not stale_ids:
                continue

            def _close(conn):
                current = conn.execute(
                    "SELECT session_id, started_at FROM sessions WHERE status='active' "
                    f"AND session_id IN ({','.join('?' for _ in stale_ids)})", stale_ids).fetchall()
                current_stale = [sid for sid, started in current
                                 if not _session_started_today(started)
                                 and not _session_active_today(conn, sid)]
                if current_stale:
                    now = datetime.now().isoformat(timespec="seconds")
                    conn.executemany(
                        "UPDATE sessions SET status='closed', ended_at=? WHERE session_id=?",
                        [(now, sid) for sid in current_stale])
                return len(current_stale)

            total += _run_session_write("close stale active sessions", _close)
    except Exception as exc:
        _raise_session_read_error("stale session lookup", exc)


_SESSION_EVIDENCE_COLUMNS = (
    "session_id", "session_title", "project_path", "mode", "status",
    "coverage", "started_at", "ended_at", "agent_id",
)


# #17/#19: the agent the dispatch in flight runs as. The dispatcher sets it once per
# request (orchestrator_server._A37_AGENT_CTX IS this object), so a lookup that passes
# no agent_id still answers for its own caller. It copies into tasks and
# asyncio.to_thread, NOT into a bare ThreadPoolExecutor.submit: a lookup on an
# executor lane sees "" and is anonymous.
_CALLER_AGENT_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_a37_agent_id", default="")


def _session_owner_clause(agent_id: str | None) -> tuple[str | None, str, tuple[str, ...]]:
    """Resolve whose session a lookup means, as (owner, sql, params).

    ONE rule for open and lookup. CASE-1909-H2 made an anonymous open resume only
    an anonymous session, while the lookup kept adopting the newest active session
    of ANY agent (#17/#19): write_fix, copy_file and mission pinning look up without
    an agent_id, so one CA's work landed in another CA's session. The owner is the
    explicit agent_id, else the dispatch's caller, else nobody -- and nobody matches
    only a session that has no agent either.
    """
    owner = agent_id or _CALLER_AGENT_ID.get() or None
    if owner:
        return owner, " AND agent_id=?", (owner,)
    return None, " AND agent_id IS NULL", ()


def _get_unique_caller_session() -> str:
    """Resolve legacy project-less logging only when ownership is unambiguous."""
    _owner, owner_sql, owner_params = _session_owner_clause(None)
    try:
        rows = _session_read_connection().execute(
            "SELECT session_id FROM sessions WHERE status='active'" + owner_sql
            + " LIMIT 2", owner_params,
        ).fetchall()
    except Exception as exc:
        _raise_session_read_error("native log session lookup", exc)
    if len(rows) != 1:
        raise ValueError("log_native needs exactly one active session for this caller; "
                         "use log_native_event with project_path for an explicit target")
    return str(rows[0][0])
def _get_active_session(project_path: str, agent_id: str | None = None) -> dict | None:
    """Return persisted evidence for the caller's active session in this project.

    An omitted agent_id means the caller the dispatch runs as (_session_owner_clause),
    never "whichever agent opened last".
    """
    try:
        norm = _session_project_key(project_path)
        _close_stale_active_sessions(norm)
        _owner, owner_sql, owner_params = _session_owner_clause(agent_id)
        sql = (
            f"SELECT {', '.join(_SESSION_EVIDENCE_COLUMNS)} FROM sessions "
            "WHERE project_path=? AND status='active'" + owner_sql
            + " ORDER BY started_at DESC, session_id DESC LIMIT 1"
        )
        row = _session_read_connection().execute(sql, (norm, *owner_params)).fetchone()
        return dict(zip(_SESSION_EVIDENCE_COLUMNS, row, strict=True)) if row else None
    except Exception as exc:
        _raise_session_read_error("active session lookup", exc)


def _get_active_session_id(project_path: str, agent_id: str | None = None) -> str | None:
    evidence = _get_active_session(project_path, agent_id=agent_id)
    return evidence["session_id"] if evidence else None


def _session_id_agent_component(agent_id: str) -> str:
    """Return a collision-free, readable id component for one agent.

    campaign-1608 (A26). The old code was a bare slug: lowercase, replace every
    non-[a-z0-9] run with "-", truncate to 16. That is lossy, and the loss is
    not hypothetical -- 'agent/a ..' and 'agent\\a ..' both collapse to
    "agent-a", so two DIFFERENT agents minted the SAME session_id and the second
    INSERT died on the primary key.

    Keep the slug for humans (operators grep these ids in logs) but append a
    short digest of the RAW agent_id, so distinct agents can never collide no
    matter how the slug mangles them. Deterministic: the same agent always maps
    to the same component, which is what resume relies on.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (agent_id or "").lower()).strip("-")[:16].strip("-")
    digest = hashlib.blake2s((agent_id or "").encode("utf-8"), digest_size=4).hexdigest()
    return f"{slug}-{digest}" if slug else digest


def _session_id_unique_tail() -> str:
    """Return the uniqueness suffix for a freshly minted session_id.

    campaign-1608 (A26). This used to be datetime.now().strftime("%m%d%H%M%S%f").
    On Windows datetime.now() has a ~1ms resolution, so %f is zero-padding, not
    a real microsecond count: 2000 samples taken back-to-back produced only 4
    distinct values, one of them 741 times. Two agents opening inside the same
    millisecond therefore agreed on the entire suffix.

    Serializing the writes does not fix this -- the id is minted by US, before
    it reaches the writer thread -- so the suffix must carry its own entropy
    rather than borrow it from the clock.
    """
    return secrets.token_hex(6)
def _set_session_mode(conn, session_id: str, new_mode: str):
    """Shared writer-only workflow-mode update; never re-mode a closed session.

    ISS-13: `sessions.mode` was write-once at open time. open_session on resume
    answered `mode_ignored_on_resume: true` and pointed the caller at
    set_workflow_mode -- but set_workflow_mode only rewrote WORKFLOW_STATE.json,
    so the persisted session row kept the mode it was born with. The two records
    then disagreed permanently, and the T1/T2 gate reads the SESSION row: a
    caller told "call set_workflow_mode to switch to T2" stayed on T1 forever
    with no error anywhere. This closes the loop the hint already promised.
    """
    row = conn.execute(
        "SELECT mode FROM sessions WHERE session_id=? AND status='active'",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE sessions SET mode=? WHERE session_id=? AND status='active'",
        (new_mode, session_id),
    )
    return {"old_mode": row[0], "new_mode": new_mode, "mode_changed": row[0] != new_mode}


def _rename_session_title(conn, session_id: str, new_title: str):
    """Shared writer-only title update; never rename a closed session."""
    row = conn.execute(
        "SELECT session_title FROM sessions WHERE session_id=? AND status='active'",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE sessions SET session_title=? WHERE session_id=? AND status='active'",
        (new_title, session_id),
    )
    return {"old_title": row[0], "new_title": new_title, "renamed": row[0] != new_title}


def _session_rotation_blockers(conn, project_path: str, session_id: str):
    """Check durable work and project-scoped drafts inside the writer transaction."""
    from orchestrator.tools.write import draft

    operations = conn.execute(
        "SELECT operation_id FROM write_operations WHERE status='in_flight' "
        "AND (session_id=? OR project_path=?)",
        (session_id, project_path),
    ).fetchall()
    pending = conn.execute(
        "SELECT pending_audit_id FROM pending_audits "
        "WHERE sync_status='pending' AND (session_id=? OR project_path=?)",
        (session_id, project_path),
    ).fetchall()
    with draft._LOCK:
        drafts = [key for key, item in draft._DRAFTS.items()
                  if item.get("expires_at_epoch", 0) > time.time()
                  and _session_project_key(item.get("project_path") or
                                           str(Path(item["target_file"]).parent)) == project_path]
    return {"in_flight_operation_ids": [row[0] for row in operations],
            "pending_audit_ids": [row[0] for row in pending], "draft_ids": drafts}


def _open_or_resume_session(project_path: str, session_title: str, mode: str,
                            agent_id: str | None = None, *,
                            force_new_session: bool = False,
                            apply_requested_title: bool = False) -> tuple[dict, bool]:
    """Serialize resume, opt-in rename, or close-then-create in one transaction."""
    norm = _session_project_key(project_path)
    # Resolved HERE, not inside _open: _open may run on the session writer thread,
    # which does not carry the dispatch's context.
    agent_id, owner_sql, owner_params = _session_owner_clause(agent_id)
    now = datetime.now().isoformat(timespec="seconds")

    def _open(conn):
        rows = conn.execute(
            "SELECT session_id, started_at FROM sessions "
            "WHERE project_path=? AND status='active'", (norm,),
        ).fetchall()
        # CASE-1909-H2: this sweep is project-wide, so it closes OTHER agents'
        # sessions as well -- it must therefore never fire on a session that is
        # still in use. A date-only test closed two different agents' live sessions
        # when a third agent opened one just after midnight; measured as an
        # identical ended_at on all of them. Activity decides, not the calendar.
        stale_ids = [sid for sid, started_at in rows
                     if not _session_started_today(started_at)
                     and not _session_active_today(conn, sid)]
        if stale_ids:
            conn.executemany(
                "UPDATE sessions SET status='closed', ended_at=? WHERE session_id=?",
                [(now, sid) for sid in stale_ids],
            )
        # CASE-1909-H2: an unscoped lookup ADOPTS whichever agent's session sorts
        # first and then writes this caller's events into it. The owner clause is
        # shared with _get_active_session, so resume and lookup cannot disagree.
        sql = ("SELECT session_id, session_title, started_at, mode FROM sessions "
               "WHERE project_path=? AND status='active'" + owner_sql
               + " ORDER BY started_at DESC, session_id DESC LIMIT 1")
        existing = conn.execute(sql, (norm, *owner_params)).fetchone()
        closed_session_id = None
        if existing and force_new_session:
            blockers = _session_rotation_blockers(conn, norm, existing[0])
            if any(blockers.values()):
                return {"ok": False, "error_name": "SESSION_ROTATION_BUSY",
                        "error": "Finish pending work before replacing this session.",
                        "session_id": existing[0], "blockers": blockers,
                        "retryable": True, "ca_next_action": "finish_pending_work_then_retry"}, False
            conn.execute("UPDATE sessions SET status='closed', ended_at=? WHERE session_id=?",
                         (now, existing[0]))
            closed_session_id = existing[0]
        elif existing:
            result = {"session_id": existing[0], "session_title": existing[1],
                      "started_at": existing[2], "mode": existing[3]}
            if apply_requested_title:
                result.update(_rename_session_title(conn, existing[0], session_title))
                result["session_title"] = session_title
            return result, False

        sid = re.sub(r"[^a-z0-9]+", "-", session_title.lower()).strip("-")[:40]
        if agent_id:
            sid = f"{sid}-{_session_id_agent_component(agent_id)}"
        sid = f"{sid}-{_session_id_unique_tail()}"
        conn.execute(
            """INSERT INTO sessions
               (session_id, session_title, project_path, mode, status,
                coverage, started_at, agent_id) VALUES (?,?,?,?,?,?,?,?)""",
            (sid, session_title, norm, mode, "active", "A-only", now, agent_id),
        )
        result = {"session_id": sid, "session_title": session_title,
                  "started_at": now, "mode": mode}
        if closed_session_id:
            result["closed_session_id"] = closed_session_id
        return result, True

    return _run_session_write("open or resume session", _open)


def _auto_open_session(project_path: str, body: dict,
                       agent_id: str | None = None) -> str | None:
    try:
        scoped_agent_id = agent_id or body.get("agent_id") or None
        title = f"Untitled Session {datetime.now().strftime('%m/%d %H:%M')}"
        session, _created = _open_or_resume_session(
            project_path, title, "T1", scoped_agent_id,
        )
        return session["session_id"]
    except Exception as exc:
        _raise_session_read_error("auto open session", exc)


def _derive_outcome(status: str, result) -> str:
    """CAN-050 — 4-value ledger outcome: pass | fail | warning | inconclusive.

    `status` already folds transport + tool-body failure (ok=false / timed_out /
    exit_code!=0 at the dispatcher), so any non-ok status is a fail. On ok,
    downgrade to warning/inconclusive from result evidence (validation warnings,
    'no tests ran').
    """
    try:
        if status != "ok":
            return "fail"
        if isinstance(result, dict):
            tests = result.get("tests")
            if isinstance(tests, dict) and tests and int(tests.get("collected") or 0) == 0:
                return "inconclusive"
            if result.get("stdout") is not None or result.get("stderr") is not None:
                blob = (str(result.get("stdout") or "")[-400:] + " " +
                        str(result.get("stderr") or "")[-400:]).lower()
                if "no tests ran" in blob or "collected 0 items" in blob:
                    return "inconclusive"
            val = result.get("validation")
            if (result.get("warnings") or result.get("warning")
                    or (isinstance(val, dict) and val.get("warnings"))
                    or str(result.get("overall_status") or "").lower() == "warning"):
                return "warning"
    except Exception:
        pass
    return "pass"


def _norm_project_key(project_key: str) -> str:
    return str(project_key or "").replace("\\", "/").rstrip("/").lower()


def _read_token_upsert(project_key: str, rel_path: str, read_hash: str,
                       read_mtime: float, read_size_bytes: int) -> bool:
    """CAN-052: record a read token through the single writer.

    Contention raises instead of answering False (F8, 2026-09-28). The swallow
    turned a STALLED/WEDGED write queue -- transient: a retry commits -- into
    "the token store refused the write", and read_file_range then told the CA a
    retry would fail the same way. Every other failure still answers False.
    """
    try:
        def _upsert(conn):
            conn.execute(
                """INSERT INTO read_tokens
                   (project_key, rel_path, read_hash, read_mtime, read_size_bytes, updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(project_key, rel_path) DO UPDATE SET
                     read_hash=excluded.read_hash, read_mtime=excluded.read_mtime,
                     read_size_bytes=excluded.read_size_bytes, updated_at=excluded.updated_at""",
                (_norm_project_key(project_key), rel_path.replace("\\", "/"), read_hash,
                 read_mtime, read_size_bytes, datetime.now().isoformat(timespec="seconds")),
            )
            return True

        return bool(_run_session_write("read token upsert", _upsert))
    except SessionStoreContentionError:
        raise
    except Exception:
        return False


def _read_token_get(project_key: str, rel_path: str):
    """CAN-052: fetch a read token; None when absent (gate falls back to INDEX)."""
    try:
        row = _session_read_connection().execute(
            "SELECT read_hash, read_mtime, read_size_bytes FROM read_tokens "
            "WHERE project_key=? AND rel_path=?",
            (_norm_project_key(project_key), rel_path.replace("\\", "/")),
        ).fetchone()
        if not row:
            return None
        return {"read_hash": row[0], "read_mtime": row[1], "read_size_bytes": row[2]}
    except Exception as exc:
        _raise_session_read_error("read token lookup", exc)


class TaskCheckpointStorageError(RuntimeError):
    """Authoritative task-checkpoint storage failed or contained invalid data."""

    def __init__(self, message: str, *, error_name: str = "TASK_CHECKPOINT_STORAGE_ERROR"):
        super().__init__(message)
        self.error_name = error_name


def _task_checkpoint_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_checkpoints (
            session_key TEXT PRIMARY KEY,
            payload     TEXT,
            updated_at  TEXT
        )
    """)


_SESSION_ADOPTED_STATUS = "adopted"


def _ensure_session_row(conn, session_key: str) -> bool:
    """Create a placeholder `sessions` row for a caller-minted session_key.

    Anh 2026-08-19 (orphan-row leak): `bootstrap_session`/`begin_task`/
    `owner_heartbeat` accept an ARBITRARY caller-chosen ``session_id`` and write
    task_checkpoints + events under it without ever going through
    :func:`_open_or_resume_session` -- the only place that INSERTs into
    ``sessions``. With ``PRAGMA foreign_keys`` off nothing rejected those rows,
    so the child tables accumulated parentless rows (measured 2026-08-19:
    1045/1058 task_checkpoints, 2391/7943 events). Two distinct sources were
    separated: historical test pollution (fixed by A53 conftest redirect) and
    this one, which was still leaking (46 new orphan events on 2026-08-18).

    The placeholder is written with status ``adopted`` -- NOT ``active`` -- on
    purpose: every "which session am I in" lookup in the codebase filters
    ``status='active'``, so an ``active`` placeholder would let a synthetic
    task-checkpoint session hijack `_auto_open_session` scope resolution and
    `logs.py` native-event attribution. ``adopted`` is invisible to those
    queries, is still pruned by age like any other session, and is NOT removed
    by `clear_closed_sessions` (which only targets ``closed``).

    Runs inside the caller's existing write transaction -- no extra commit.
    Set ``ORCH_ADOPT_ORPHAN_SESSIONS=0`` to restore the old leaky behaviour.

    Returns True when a row was created.
    """
    key = str(session_key or "").strip()
    if not key:
        return False
    if os.environ.get("ORCH_ADOPT_ORPHAN_SESSIONS", "1").strip().lower() in {"0", "false", "no"}:
        return False
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO sessions
               (session_id, session_title, project_path, mode, status,
                coverage, started_at, agent_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (key, f"Adopted task session {key}"[:120], "", "T3",
             _SESSION_ADOPTED_STATUS, "A-only",
             datetime.now().isoformat(timespec="seconds"), None),
        )
        return bool(cur.rowcount)
    except Exception:
        # Never let bookkeeping break the checkpoint write it is protecting.
        logger.exception("failed to adopt session row for %r", key)
        return False


def _session_payload_json(value, *, session_key: str, checkpoint: bool = False, heartbeat=None):
    """Bound encoded payloads before they can occupy the writer queue."""
    pieces = []
    size = 0
    for index, piece in enumerate(json.JSONEncoder(ensure_ascii=False).iterencode(value)):
        if heartbeat is not None and index % 256 == 0:
            heartbeat()
        size += len(piece.encode("utf-8"))
        if size > 256 * 1024:
            message = f"Session payload exceeds 262144 UTF-8 bytes for {session_key!r}"
            if checkpoint:
                raise TaskCheckpointStorageError(message, error_name="TASK_CHECKPOINT_TOO_LARGE")
            return json.dumps({"truncated": True, "reason": "SESSION_EVENT_PAYLOAD_TOO_LARGE",
                               "limit_bytes": 256 * 1024, "session_key": str(session_key)[:256]})
        pieces.append(piece)
    return "".join(pieces)


def _checkpoint_storage_error(exc, session_key):
    if isinstance(exc, TaskCheckpointStorageError):
        return exc
    if isinstance(exc, SessionStoreCommitPendingError):
        error = TaskCheckpointStorageError(
            f"Checkpoint {session_key!r}: {exc}", error_name="TASK_CHECKPOINT_COMMIT_PENDING")
    elif isinstance(exc, SessionStoreContentionError) or SessionWriter._is_locked_error(exc):
        error = TaskCheckpointStorageError(
            f"Checkpoint {session_key!r}: {exc}", error_name="TASK_CHECKPOINT_CONTENTION")
    else:
        return TaskCheckpointStorageError(f"Checkpoint {session_key!r}: {exc}")
    error.contention_stage = getattr(exc, "contention_stage", "execution")
    return error


def _decode_task_checkpoint(session_key: str, payload: str | None):
    if not payload:
        return None
    try:
        checkpoint = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise TaskCheckpointStorageError(
            f"Malformed task checkpoint payload for {session_key!r}: {exc}",
            error_name="TASK_CHECKPOINT_MALFORMED",
        ) from exc
    if not isinstance(checkpoint, dict):
        raise TaskCheckpointStorageError(
            f"Task checkpoint payload for {session_key!r} is not an object",
            error_name="TASK_CHECKPOINT_MALFORMED",
        )
    # Keep corrupt authority in place and fail closed: an empty replacement
    # would silently discard the owner commitment and monotonic task epoch.
    return checkpoint


def _task_checkpoint_save(session_key: str, checkpoint: dict) -> bool:
    """Wait for SQLite COMMIT (WAL/NORMAL), not an OS/power-loss durability barrier."""
    if not isinstance(checkpoint, dict):
        raise TaskCheckpointStorageError(
            "Task checkpoint must be a dictionary", error_name="TASK_CHECKPOINT_MALFORMED")
    payload = _session_payload_json(checkpoint, session_key=session_key, checkpoint=True)

    def _save(conn):
        row = conn.execute("SELECT payload FROM task_checkpoints WHERE session_key=?",
                           (str(session_key),)).fetchone()
        if row and row[0] == payload:
            return True
        _ensure_session_row(conn, session_key)
        conn.execute(
            """INSERT INTO task_checkpoints (session_key, payload, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(session_key) DO UPDATE SET
                 payload=excluded.payload, updated_at=excluded.updated_at""",
            (str(session_key), payload, datetime.now().isoformat(timespec="seconds")))
        return True

    try:
        row = _session_read_connection().execute(
            "SELECT payload FROM task_checkpoints WHERE session_key=?", (str(session_key),)).fetchone()
        if row and row[0] == payload:
            return True
        return bool(_run_session_write("task checkpoint save", _save, busy_timeout_ms=5000))
    except Exception as exc:
        raise _checkpoint_storage_error(exc, session_key) from exc


def _task_checkpoint_load(session_key: str):
    """Read the authoritative checkpoint from one thread-local read connection."""
    return _task_checkpoint_load_with_meta(session_key)[0]


def _task_checkpoint_load_with_meta(session_key: str):
    """Read checkpoint payload and its committed updated_at timestamp."""
    try:
        row = _session_read_connection().execute(
            "SELECT payload, updated_at FROM task_checkpoints WHERE session_key=?",
            (str(session_key),),
        ).fetchone()
        checkpoint = _decode_task_checkpoint(str(session_key), row[0] if row else None)
        return checkpoint, str(row[1] or "") if row else ""
    except Exception as exc:
        raise _checkpoint_storage_error(exc, session_key) from exc


def _task_checkpoint_update(session_key: str, update_fn, busy_timeout_ms: int = 5000,
                            *, read_fastpath: bool = False):
    """Read, decide, and optionally replace one checkpoint row atomically.

    Audited pure decision closures may opt into a read-only answer against an
    already committed owner. Any replacement is re-decided inside the writer.
    COMMIT with WAL/NORMAL survives process crash, not every OS/power failure.
    """
    timeout_ms = max(1, int(busy_timeout_ms))
    decision = {}

    def _update(conn):
        row = conn.execute(
            "SELECT payload FROM task_checkpoints WHERE session_key=?",
            (str(session_key),)).fetchone()
        previous = _decode_task_checkpoint(str(session_key), row[0] if row else None)
        replacement, result = update_fn(dict(previous) if previous else {})
        decision.update(previous=previous, result=result, replacement=replacement)
        if replacement is None:
            return False
        if not isinstance(replacement, dict):
            raise TaskCheckpointStorageError(
                "Task checkpoint updater returned a non-object replacement",
                error_name="TASK_CHECKPOINT_MALFORMED")
        payload = _session_payload_json(replacement, session_key=session_key, checkpoint=True, heartbeat=getattr(conn, "progress_heartbeat", None))
        _ensure_session_row(conn, session_key)
        conn.execute(
            """INSERT INTO task_checkpoints (session_key, payload, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(session_key) DO UPDATE SET
                 payload=excluded.payload, updated_at=excluded.updated_at""",
            (str(session_key), payload, datetime.now().isoformat(timespec="seconds")))
        return True

    try:
        if read_fastpath and os.environ.get("ORCH_TASK_CHECKPOINT_READ_FASTPATH", "1") != "0":
            previous, _ = _task_checkpoint_load_with_meta(session_key)
            if (previous and previous.get("owner_claim_status") == "committed"
                    and previous.get("owner_capability_sha256")):
                replacement, result = update_fn(dict(previous))
                if replacement is None:
                    return previous, result, False
        committed = _run_session_write(
            "atomic task checkpoint update", _update, busy_timeout_ms=timeout_ms)
        if not committed:
            return decision["previous"], decision["result"], False
        return dict(decision["replacement"]), decision["result"], True
    except Exception as exc:
        raise _checkpoint_storage_error(exc, session_key) from exc


_SESSION_EVENT_WARNING_LOCK = threading.Lock()
_SESSION_EVENT_CONTENTION_COUNT = 0


def _note_session_event_contention(failed):
    global _SESSION_EVENT_CONTENTION_COUNT
    with _SESSION_EVENT_WARNING_LOCK:
        if failed:
            _SESSION_EVENT_CONTENTION_COUNT += 1
            if _SESSION_EVENT_CONTENTION_COUNT == 1:
                logger.warning("session event persistence hit store contention; propagating; further warnings latched")
        elif _SESSION_EVENT_CONTENTION_COUNT:
            logger.warning("session event persistence recovered; suppressed_contention_warnings=%d",
                           _SESSION_EVENT_CONTENTION_COUNT - 1)
            _SESSION_EVENT_CONTENTION_COUNT = 0


def _log_session_event(session_id: str, tool_name: str, body: dict, result: dict,
                       duration_ms: int, status: str, error_msg: str = "",
                       audit_id: str = "", durable: bool = True):
    try:
        _, action_type, _ = TOOL_ACTION_MAP.get(tool_name, ("tool", "workflow", "tool"))
        if tool_name == "run_project_command" and _run_project_command_is_validation(body):
            action_type = "validate"

        def _summarize_target(raw):
            if raw is None:
                return ""
            if isinstance(raw, list):
                return " ".join(str(x) for x in raw[:3])
            if isinstance(raw, dict):
                return ""
            value = str(raw)
            try:
                path_runtime.reject_c0_control_characters(value)
            except path_runtime.PathControlCharacterError as exc:
                return f"<invalid path 0x{exc.codepoint:04x}>"
            if "/" in value or "\\" in value:
                try:
                    return Path(value).name
                except Exception:
                    pass
            return value

        target = ""
        for key in ("file_path", "path", "command", "query", "name", "project_path"):
            if body.get(key):
                target = _summarize_target(body[key])
                if target:
                    break
        target = target[:120]
        start, end = body.get("start_line"), body.get("end_line")
        range_label = f"{start}p{end}" if start and end else (f"L{start}" if start else "")
        reason = error_msg[:300] if status != "ok" else ""
        outcome = _derive_outcome(status, result)
        metadata = result if isinstance(result, dict) else {}
        qw = metadata.get("queue_wait_ms")
        host_session_id = metadata.get("_host_session_id")
        task_epoch = metadata.get("_task_epoch")
        prompt_hash = metadata.get("_prompt_hash")
        agent_id = metadata.get("_task_agent_id")
        try:
            task_epoch = int(task_epoch) if task_epoch not in (None, "") else None
        except (TypeError, ValueError):
            task_epoch = None
        event_kind = str(metadata.get("event_kind") or ("error" if status == "error" else "tool_result"))
        error_owner = metadata.get("error_owner")
        error_name = metadata.get("error_name")
        correlation_type = metadata.get("correlation_type")
        correlation_key = metadata.get("correlation_key")
        evidence = metadata.get("evidence")
        evidence_json = _session_payload_json(evidence, session_key=session_id) if evidence is not None else None
        natural = _make_natural_log_line(tool_name, target, range_label, duration_ms, status)
        now = datetime.now().isoformat(timespec="seconds")
        audit_id = str(audit_id or "").strip() or None
        result_json = _session_payload_json(result, session_key=session_id) if isinstance(result, dict) else None
        columns = """session_id, tool_name, tool_origin, action_type, status,
            target, range_label, duration_ms, reason, natural_log_line, created_at,
            outcome, queue_wait_ms, host_session_id, task_epoch, prompt_hash, agent_id,
            audit_id, result_json, event_kind, error_owner, error_name,
            correlation_type, correlation_key, evidence_json"""
        values = (session_id, tool_name, "A", action_type, status, target, range_label,
                  duration_ms, reason, natural, now, outcome, qw, host_session_id or None,
                  task_epoch, prompt_hash or None, agent_id or None, audit_id, result_json,
                  event_kind, error_owner, error_name, correlation_type, correlation_key,
                  evidence_json)
        def _persist_event(conn):
            # Anh 2026-08-19: adopt the parent row first so an event logged
            # against a caller-minted session_id (begin_task / get_task_checkpoint
            # / owner_heartbeat never call _open_or_resume_session) does not
            # become an orphan. See _ensure_session_row.
            _ensure_session_row(conn, session_id)
            verb = "INSERT OR IGNORE" if audit_id else "INSERT"
            cur = conn.execute(
                f"{verb} INTO events ({columns}) VALUES ({','.join('?' for _ in values)})",
                values,
            )
            if cur.rowcount:
                return cur.lastrowid
            existing = conn.execute(
                "SELECT id FROM events WHERE audit_id=?", (audit_id,),
            ).fetchone()
            if not existing:
                raise RuntimeError("audit_id insert was ignored without an existing event")
            return existing[0]

        event_id = _run_session_write(
            "session event persistence", _persist_event, durable=durable,
        )
        if event_id is not None:
            _note_session_event_contention(False)
        return event_id
    except SessionStoreContentionError:
        _note_session_event_contention(True)
        raise
    except Exception:
        logger.exception(
            "session event persistence failed for session=%r tool=%r audit_id=%r",
            session_id, tool_name, audit_id,
        )
    return None


def _record_error_resolution(session_id: str, resolves_event_id: int,
                             resolution_kind: str, *, error_owner: str = "",
                             evidence=None, correlation_type: str = "",
                             correlation_key: str = "") -> int:
    """Append an explicit state transition for one exact in-scope error event."""
    kind = str(resolution_kind or "").strip().lower()
    if kind not in {"recovered", "expected_failure", "reopened"}:
        raise ValueError("resolution_kind must be recovered, expected_failure, or reopened")
    try:
        target_id = int(resolves_event_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("resolves_event_id must be an integer") from exc
    evidence_json = json.dumps(evidence, ensure_ascii=False) if evidence is not None else None
    now = datetime.now().isoformat(timespec="seconds")

    def _record(conn):
        source = conn.execute(
            """SELECT session_id, status, event_kind, error_owner, error_name,
                      correlation_type, correlation_key
               FROM events WHERE id=?""",
            (target_id,),
        ).fetchone()
        if not source:
            raise ValueError(f"error event not found: {target_id}")
        if source[0] != session_id:
            raise ValueError("resolution target belongs to a different session")
        if source[1] != "error" or source[2] == "resolution":
            raise ValueError("resolution target must be an error event")
        cur = conn.execute(
            """INSERT INTO events
               (session_id, tool_name, tool_origin, action_type, status, reason,
                natural_log_line, created_at, event_kind, resolves_event_id,
                resolution_kind, error_owner, error_name, correlation_type,
                correlation_key, evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session_id, "record_error_resolution", "A", "audit", "ok", kind,
             f"error {target_id} marked {kind}", now, "resolution", target_id,
             kind, error_owner or source[3], source[4],
             correlation_type or source[5], correlation_key or source[6], evidence_json),
        )
        return cur.lastrowid

    return _run_session_write("record error resolution", _record)


def _get_session_events_since(session_id: str, last_id: int) -> list[dict]:
    try:
        rows = _session_read_connection().execute(
            """SELECT id, tool_name, tool_origin, action_type, status, target,
                       range_label, duration_ms, reason, natural_log_line, created_at, outcome,
                       host_session_id, task_epoch, prompt_hash, agent_id
               FROM events WHERE session_id=? AND id>? ORDER BY id ASC LIMIT 50""",
            (session_id, last_id),
        ).fetchall()
        keys = ["id", "tool_name", "tool_origin", "action_type", "status", "target",
                "range_label", "duration_ms", "reason", "natural_log_line", "created_at", "outcome",
                "host_session_id", "task_epoch", "prompt_hash", "agent_id"]
        return [dict(zip(keys, row, strict=True)) for row in rows]
    except Exception:
        return []
