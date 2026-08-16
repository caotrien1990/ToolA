"""SQLite session store helpers extracted from code_tools."""

from __future__ import annotations

import atexit
import base64
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
from datetime import datetime
from pathlib import Path

import path_runtime


logger = logging.getLogger(__name__)


def _is_unc_path_like(path: str) -> bool:
    text = str(path or "").strip()
    return text.startswith("\\\\") or text.startswith("//")


def _project_cache_dir(project_path: str) -> Path:
    if _is_unc_path_like(project_path):
        raise ValueError(
            "UNC project_path không hỗ trợ project cache auto-infer "
            "(.orchestrator_cache trên SMB sẽ treo). Dùng local project root, "
            "hoặc check_server_path cho UNC."
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
    0.0, float(os.environ.get("ORCH_SESSION_WRITE_BATCH_WINDOW_MS", "5"))
)
_SESSION_WRITE_STALL_SECONDS = max(
    1.0, float(os.environ.get("ORCH_SESSION_WRITE_STALL_SECONDS", "5"))
)
_SESSION_DB_INITIALIZED_PATHS: set[str] = set()
# campaign-1608 (A27): the WAL switch ignores busy_timeout, so it needs its own
# small retry budget. Short on purpose -- the winner of the race enables WAL for
# everyone, so a loser has nothing left to wait for.
_WAL_SWITCH_ATTEMPTS = 4
_WAL_SWITCH_RETRY_MS = 20


class SessionStoreContentionError(RuntimeError):
    """A session-store write could not commit within its fixed budget."""


class _WriteItem:
    __slots__ = (
        "operation", "write_fn", "durable", "future", "deadline",
        "cancelled", "completed", "enqueued_at",
    )

    def __init__(self, operation, write_fn, durable: bool, deadline: float | None):
        self.operation = str(operation)
        self.write_fn = write_fn
        self.durable = bool(durable)
        self.future = Future() if durable else None
        self.deadline = deadline
        self.cancelled = threading.Event()
        self.completed = False
        self.enqueued_at = time.monotonic()


class SessionWriter:
    """Single-process SQLite writer with bounded queue and grouped commits.

    sessions.db is owned by one [A] process. WAL/busy_timeout remain enabled as a
    defensive boundary for accidental cross-process access, but no second writer
    process is supported. Durable submissions wait for COMMIT; fire-and-forget
    submissions are O(1) and may be dropped only with a warning and metric.
    """

    def __init__(
        self,
        db_path,
        *,
        capacity: int = _SESSION_WRITE_QUEUE_SIZE,
        batch_max: int = _SESSION_WRITE_BATCH_MAX,
        batch_window_ms: float = _SESSION_WRITE_BATCH_WINDOW_MS,
        busy_timeout_ms: int = _SESSION_DB_BUSY_TIMEOUT_MS,
        checkpoint_every_batches: int = 64,
        watchdog_interval: float = 0.1,
        stall_seconds: float = _SESSION_WRITE_STALL_SECONDS,
        auto_start: bool = True,
    ):
        self.db_path = Path(db_path)
        self.capacity = max(1, int(capacity))
        self.batch_max = max(1, int(batch_max))
        self.batch_window_seconds = max(0.0, float(batch_window_ms) / 1000.0)
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self.checkpoint_every_batches = max(1, int(checkpoint_every_batches))
        self.watchdog_interval = max(0.01, float(watchdog_interval))
        self.stall_seconds = max(0.1, float(stall_seconds))
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
        self._batch_count = 0
        self._enqueued_total = 0
        self._committed_total = 0
        self._dropped_count = 0
        self._failed_count = 0
        self._restart_count = 0
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
        with self._metrics_lock:
            return {
                "enqueued_total": self._enqueued_total,
                "committed_total": self._committed_total,
                "dropped_count": self._dropped_count,
                "failed_count": self._failed_count,
                "restart_count": self._restart_count,
                "queue_depth": self._queue.qsize(),
                "failed": bool(self._failed_reason),
                "failed_reason": self._failed_reason or None,
                "stalled": bool(self._stalled_reason),
                "stalled_reason": self._stalled_reason or None,
            }

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

    def _mark_failed(self, reason: str) -> None:
        reason = str(reason or "unknown writer failure")[:500]
        should_log = False
        with self._metrics_lock:
            if not self._failed_reason:
                self._failed_reason = reason
                should_log = True
        if should_log:
            logger.error("SessionWriter FAILED: %s", reason)

    def _mark_stalled(self, reason: str) -> None:
        reason = str(reason or "unknown writer stall")[:500]
        should_log = False
        with self._metrics_lock:
            if not self._stalled_reason:
                self._stalled_reason = reason
                should_log = True
        if should_log:
            logger.warning("SessionWriter STALLED: %s", reason)

    def _clear_stalled(self) -> None:
        with self._metrics_lock:
            self._stalled_reason = ""

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
            if active_since and time.monotonic() - active_since > self.stall_seconds:
                # Python cannot safely kill a live thread. Starting a second one would
                # violate single-writer ownership, so fail loud instead.
                self._mark_stalled(
                    f"writer batch made no observable progress for more than "
                    f"{self.stall_seconds:.2f}s"
                )

    def submit(
        self,
        operation,
        write_fn,
        *,
        durable: bool,
        timeout_ms: int | None = None,
    ):
        if self._closing.is_set():
            raise RuntimeError("SessionWriter is closing")
        if self._failed_reason:
            if durable:
                raise RuntimeError(f"SessionWriter failed: {self._failed_reason}")
            with self._metrics_lock:
                self._dropped_count += 1
            logger.warning(
                "dropping fire-and-forget session write %r: writer failed: %s",
                operation, self._failed_reason,
            )
            return None
        if self._stalled_reason:
            if durable:
                raise SessionStoreContentionError(
                    f"SessionWriter is temporarily stalled: {self._stalled_reason}"
                )
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

        budget_ms = max(1, int(timeout_ms or self.busy_timeout_ms))
        deadline = time.monotonic() + budget_ms / 1000.0 if durable else None
        item = _WriteItem(operation, write_fn, durable, deadline)
        try:
            if durable:
                self._queue.put(item, timeout=budget_ms / 1000.0)
            else:
                self._queue.put_nowait(item)
        except queue.Full as exc:
            if durable:
                raise SessionStoreContentionError(
                    f"{operation} exceeded session write queue budget ({budget_ms}ms)"
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

        remaining = max(0.0, deadline - time.monotonic())
        try:
            # Small grace lets the writer publish the SQLite busy error instead of
            # racing the caller-side Future timeout at the exact budget boundary.
            return item.future.result(timeout=remaining + 0.1)
        except FutureTimeoutError as exc:
            item.cancelled.set()
            raise SessionStoreContentionError(
                f"{operation} exceeded session writer budget ({budget_ms}ms)"
            ) from exc

    def _take_batch(self) -> list[_WriteItem]:
        try:
            first = self._queue.get(timeout=0.1)
        except queue.Empty:
            return []
        batch = [first]
        drain_deadline = time.monotonic() + self.batch_window_seconds
        while (
            len(batch) < self.batch_max
            and time.monotonic() <= drain_deadline
        ):
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _open_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        _configure_sessions_connection(conn, self.busy_timeout_ms)
        return conn

    @staticmethod
    def _is_locked_error(exc: BaseException) -> bool:
        text = str(exc).lower()
        return isinstance(exc, sqlite3.OperationalError) and (
            "locked" in text or "busy" in text
        )

    def _normalized_error(
        self, item: _WriteItem, exc: BaseException, timeout_ms: int
    ) -> BaseException:
        if self._is_locked_error(exc):
            return SessionStoreContentionError(
                f"{item.operation} exceeded SQLite busy budget ({timeout_ms}ms): {exc}"
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
        now = time.monotonic()
        active = []
        for item in batch:
            if item.cancelled.is_set() or (
                item.deadline is not None and item.deadline <= now
            ):
                self._finish_item(
                    item,
                    error=SessionStoreContentionError(
                        f"{item.operation} expired before SQLite transaction"
                    ),
                )
            else:
                active.append(item)
        if not active:
            return True

        deadlines = [item.deadline for item in active if item.deadline is not None]
        timeout_ms = self.busy_timeout_ms
        if deadlines:
            timeout_ms = max(
                1, min(int((deadline - time.monotonic()) * 1000) for deadline in deadlines)
            )
        conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
        outcomes: dict[_WriteItem, tuple[object, BaseException | None]] = {}
        try:
            conn.execute("BEGIN IMMEDIATE")
            for index, item in enumerate(active):
                savepoint = f"session_item_{index}"
                conn.execute(f"SAVEPOINT {savepoint}")
                try:
                    result = item.write_fn(conn)
                    if item.cancelled.is_set() or (
                        item.deadline is not None and item.deadline <= time.monotonic()
                    ):
                        raise SessionStoreContentionError(
                            f"{item.operation} exceeded session writer budget"
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
            conn.execute("COMMIT")
        except BaseException as batch_exc:
            if conn.in_transaction:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    logger.exception("SessionWriter batch rollback failed")
            for item in active:
                prior = outcomes.get(item)
                error = prior[1] if prior and prior[1] is not None else self._normalized_error(
                    item, batch_exc, timeout_ms
                )
                self._finish_item(item, error=error)
            return False

        for item in active:
            result, error = outcomes[item]
            self._finish_item(item, result=result, error=error)
        self._batch_count += 1
        if self._batch_count % self.checkpoint_every_batches == 0:
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
                try:
                    if conn is None:
                        conn = self._open_connection()
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
                        budget_ms = self.busy_timeout_ms
                        if item.deadline is not None:
                            budget_ms = max(
                                1, int((item.deadline - item.enqueued_at) * 1000)
                            )
                        self._finish_item(
                            item, error=self._normalized_error(item, exc, budget_ms)
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
                    self._last_progress_at = time.monotonic()
                    self._clear_stalled()
                    if self._queue.empty():
                        self._idle.set()
        finally:
            if conn is not None:
                try:
                    if conn.in_transaction:
                        conn.rollback()
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    logger.exception("SessionWriter shutdown checkpoint failed")
                finally:
                    conn.close()
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
                    item, error=RuntimeError("SessionWriter closed before write committed")
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


def _shutdown_session_writer() -> None:
    writer = globals().get("_SESSION_WRITER")
    if writer is not None:
        try:
            writer.close(timeout=5.0)
        except Exception:
            logger.exception("SessionWriter shutdown failed")


def _get_session_writer() -> SessionWriter:
    global _SESSION_WRITER
    wanted = Path(_SESSIONS_DB)
    writer = globals().get("_SESSION_WRITER")
    if writer is not None and writer.db_path == wanted and not writer._closing.is_set():
        return writer
    with _SESSION_WRITER_SWAP_LOCK:
        writer = globals().get("_SESSION_WRITER")
        if writer is not None and writer.db_path == wanted and not writer._closing.is_set():
            return writer
        if writer is not None:
            writer.close(timeout=5.0)
        if (
            os.path.normcase(os.path.abspath(str(wanted)))
            not in _SESSION_DB_INITIALIZED_PATHS
        ):
            _init_session_db()
        _SESSION_WRITER = SessionWriter(wanted)
        return _SESSION_WRITER


def _session_read_connection() -> sqlite3.Connection:
    _get_session_writer()
    wanted = str(Path(_SESSIONS_DB))
    conn = getattr(_SESSION_READ_LOCAL, "conn", None)
    current_path = getattr(_SESSION_READ_LOCAL, "db_path", None)
    if conn is not None and current_path != wanted:
        try:
            conn.close()
        except Exception:
            pass
        conn = None
    if conn is None:
        conn = sqlite3.connect(
            wanted,
            timeout=_SESSION_DB_BUSY_TIMEOUT_MS / 1000.0,
        )
        conn.execute(f"PRAGMA busy_timeout={_SESSION_DB_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA cache_size=-16000")
        conn.execute("PRAGMA mmap_size=268435456")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA query_only=ON")
        _SESSION_READ_LOCAL.conn = conn
        _SESSION_READ_LOCAL.db_path = wanted
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
    return _get_session_writer().submit(
        operation,
        write_fn,
        durable=durable,
        timeout_ms=busy_timeout_ms,
    )


def _init_session_db():
    """Run idempotent migrations before a writer opens the target database."""
    existing_writer = globals().get("_SESSION_WRITER")
    if (
        existing_writer is not None
        and existing_writer.db_path == Path(_SESSIONS_DB)
        and not existing_writer._closing.is_set()
    ):
        return
    _SESSIONS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_SESSIONS_DB))
    _configure_sessions_connection(conn, _SESSION_DB_BUSY_TIMEOUT_MS)
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
               WHERE status='active' AND agent_id IS NOT NULL"""
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
            "ON events(host_session_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_resolution_target "
            "ON events(resolves_event_id, id) WHERE resolves_event_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_error_scope "
            "ON events(session_id, event_kind, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_correlation "
            "ON events(correlation_type, correlation_key, id) "
            "WHERE correlation_key IS NOT NULL"
        )
        # WHY: durable outbox replay is at-least-once; the sink must make one
        # session event per audit_id even after a crash between sink and ACK.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_events_audit_id "
            "ON events(audit_id) WHERE audit_id IS NOT NULL"
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
            "ON pending_audits(project_path, agent_id, sync_status)"
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
            "ON write_operations(project_path, file_path, updated_at DESC)"
        )
        conn.commit()
        _SESSION_DB_INITIALIZED_PATHS.add(
            os.path.normcase(os.path.abspath(str(_SESSIONS_DB)))
        )
    finally:
        conn.close()


_init_session_db()
_SESSION_WRITER = SessionWriter(_SESSIONS_DB)
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


def _make_natural_log_line(tool_name: str, target: str, range_label: str, duration_ms: int, status: str) -> str:
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


def _close_stale_active_sessions(project_path: str) -> int:
    try:
        norm = _session_project_key(project_path)
        rows = _session_read_connection().execute(
            "SELECT session_id, started_at FROM sessions "
            "WHERE project_path=? AND status='active'",
            (norm,),
        ).fetchall()
        stale_ids = [sid for sid, started_at in rows if not _session_started_today(started_at)]
        if not stale_ids:
            return 0
        now = datetime.now().isoformat(timespec="seconds")

        def _close(conn):
            current = conn.execute(
                "SELECT session_id, started_at FROM sessions "
                "WHERE project_path=? AND status='active'",
                (norm,),
            ).fetchall()
            current_stale = [
                sid for sid, started_at in current if not _session_started_today(started_at)
            ]
            if current_stale:
                conn.executemany(
                    "UPDATE sessions SET status='closed', ended_at=? WHERE session_id=?",
                    [(now, sid) for sid in current_stale],
                )
            return len(current_stale)

        return _run_session_write("close stale active sessions", _close)
    except Exception:
        return 0


_SESSION_EVIDENCE_COLUMNS = (
    "session_id", "session_title", "project_path", "mode", "status",
    "coverage", "started_at", "ended_at", "agent_id",
)


def _get_active_session(project_path: str, agent_id: str | None = None) -> dict | None:
    """Return persisted evidence for the active session in the requested scope.

    Supplying agent_id uses an agent-scoped lookup. Omitting it preserves the
    legacy project-only behavior and returns the latest active project session.
    """
    try:
        norm = _session_project_key(project_path)
        _close_stale_active_sessions(norm)
        sql = (
            f"SELECT {', '.join(_SESSION_EVIDENCE_COLUMNS)} FROM sessions "
            "WHERE project_path=? AND status='active'"
        )
        params: tuple[str, ...] = (norm,)
        if agent_id:
            sql += " AND agent_id=?"
            params += (agent_id,)
        sql += " ORDER BY started_at DESC LIMIT 1"
        row = _session_read_connection().execute(sql, params).fetchone()
        return dict(zip(_SESSION_EVIDENCE_COLUMNS, row)) if row else None
    except Exception:
        return None


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
def _open_or_resume_session(project_path: str, session_title: str, mode: str,
                            agent_id: str | None = None) -> tuple[dict, bool]:
    """Atomically return an active session in scope, creating it if absent."""
    norm = _session_project_key(project_path)
    now = datetime.now().isoformat(timespec="seconds")

    def _open(conn):
        rows = conn.execute(
            "SELECT session_id, started_at FROM sessions "
            "WHERE project_path=? AND status='active'",
            (norm,),
        ).fetchall()
        stale_ids = [sid for sid, started_at in rows if not _session_started_today(started_at)]
        if stale_ids:
            conn.executemany(
                "UPDATE sessions SET status='closed', ended_at=? WHERE session_id=?",
                [(now, sid) for sid in stale_ids],
            )

        sql = (
            "SELECT session_id, session_title, started_at FROM sessions "
            "WHERE project_path=? AND status='active'"
        )
        params: tuple[str, ...] = (norm,)
        if agent_id:
            sql += " AND agent_id=?"
            params += (agent_id,)
        sql += " ORDER BY started_at DESC, session_id DESC LIMIT 1"
        existing = conn.execute(sql, params).fetchone()
        if existing:
            return {
                "session_id": existing[0],
                "session_title": existing[1],
                "started_at": existing[2],
            }, False

        sid = re.sub(r"[^a-z0-9]+", "-", session_title.lower()).strip("-")[:40]
        if agent_id:
            sid = f"{sid}-{_session_id_agent_component(agent_id)}"
        sid = f"{sid}-{_session_id_unique_tail()}"
        conn.execute(
            """INSERT INTO sessions
               (session_id, session_title, project_path, mode, status,
                coverage, started_at, agent_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (sid, session_title, norm, mode, "active", "A-only", now, agent_id),
        )
        return {
            "session_id": sid,
            "session_title": session_title,
            "started_at": now,
        }, True

    return _run_session_write("open or resume session", _open)


def _auto_open_session(project_path: str, body: dict,
                       agent_id: str | None = None) -> str | None:
    try:
        scoped_agent_id = agent_id or body.get("agent_id") or None
        title = f"Untitled Session {datetime.now().strftime('%m/%d %H:%M')}"
        session, _created = _open_or_resume_session(
            project_path, title, "T1", scoped_agent_id
        )
        return session["session_id"]
    except Exception:
        return None


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
    """CAN-052: record a read token through the single writer."""
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
    except Exception:
        return None


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
    return checkpoint


def _task_checkpoint_save(session_key: str, checkpoint: dict) -> bool:
    """Persist a checkpoint and fail loudly if the grouped commit fails."""
    if not isinstance(checkpoint, dict):
        raise TaskCheckpointStorageError(
            "Task checkpoint must be a dictionary",
            error_name="TASK_CHECKPOINT_MALFORMED",
        )

    def _save(conn):
        conn.execute(
            """INSERT INTO task_checkpoints (session_key, payload, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(session_key) DO UPDATE SET
                 payload=excluded.payload, updated_at=excluded.updated_at""",
            (str(session_key), json.dumps(checkpoint, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
        return True

    try:
        return bool(_run_session_write("task checkpoint save", _save, busy_timeout_ms=5000))
    except Exception as exc:
        if isinstance(exc, TaskCheckpointStorageError):
            raise
        raise TaskCheckpointStorageError(
            f"Failed to persist task checkpoint: {exc}"
        ) from exc


def _task_checkpoint_load(session_key: str):
    """Read the authoritative checkpoint from one thread-local read connection."""
    try:
        row = _session_read_connection().execute(
            "SELECT payload FROM task_checkpoints WHERE session_key=?",
            (str(session_key),),
        ).fetchone()
        return _decode_task_checkpoint(str(session_key), row[0] if row else None)
    except Exception as exc:
        if isinstance(exc, TaskCheckpointStorageError):
            raise
        raise TaskCheckpointStorageError(
            f"Failed to load task checkpoint: {exc}"
        ) from exc


def _task_checkpoint_update(session_key: str, update_fn, busy_timeout_ms: int = 5000):
    """Atomically read, decide, and optionally replace one checkpoint row.

    ``update_fn`` receives one authoritative snapshot and returns
    ``(replacement_or_none, result)``. ``None`` rolls back a rejected/read-only
    decision. A replacement is UPSERTed and committed before this function
    returns, so competing processes receive distinct commit-ordered epochs.
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
                error_name="TASK_CHECKPOINT_MALFORMED",
            )
        conn.execute(
            """INSERT INTO task_checkpoints (session_key, payload, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(session_key) DO UPDATE SET
                 payload=excluded.payload, updated_at=excluded.updated_at""",
            (str(session_key), json.dumps(replacement, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")))
        return True

    try:
        committed = _run_session_write(
            "atomic task checkpoint update", _update, busy_timeout_ms=timeout_ms
        )
        if not committed:
            return decision["previous"], decision["result"], False
        return dict(decision["replacement"]), decision["result"], True
    except SessionStoreContentionError as exc:
        raise TaskCheckpointStorageError(
            str(exc), error_name="TASK_CHECKPOINT_CONTENTION") from exc
    except Exception as exc:
        if isinstance(exc, TaskCheckpointStorageError):
            raise
        raise TaskCheckpointStorageError(
            f"Atomic task checkpoint update failed: {exc}") from exc


def _log_session_event(session_id: str, tool_name: str, body: dict, result: dict,
                       duration_ms: int, status: str, error_msg: str = "",
                       audit_id: str = "", durable: bool = True):
    try:
        _, action_type, target_icon = TOOL_ACTION_MAP.get(tool_name, ("tool", "workflow", "tool"))
        if tool_name == "run_project_command" and _run_project_command_is_validation(body):
            action_type, target_icon = "validate", "validate"

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
        evidence_json = json.dumps(evidence, ensure_ascii=False) if evidence is not None else None
        natural = _make_natural_log_line(tool_name, target, range_label, duration_ms, status)
        now = datetime.now().isoformat(timespec="seconds")
        audit_id = str(audit_id or "").strip() or None
        result_json = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else None
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
            verb = "INSERT OR IGNORE" if audit_id else "INSERT"
            cur = conn.execute(
                f"{verb} INTO events ({columns}) VALUES ({','.join('?' for _ in values)})",
                values,
            )
            if cur.rowcount:
                return cur.lastrowid
            existing = conn.execute(
                "SELECT id FROM events WHERE audit_id=?", (audit_id,)
            ).fetchone()
            if not existing:
                raise RuntimeError("audit_id insert was ignored without an existing event")
            return existing[0]

        return _run_session_write(
            "session event persistence", _persist_event, durable=durable
        )
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
        return [dict(zip(keys, row)) for row in rows]
    except Exception:
        return []
