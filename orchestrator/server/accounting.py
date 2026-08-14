"""Bounded, ordered post-call accounting isolated from asyncio request loops."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Any, Callable

_SENTINEL = object()


class PostCallAccountingQueue:
    """Process-global FIFO lane for blocking accounting sinks.

    The queue is loop-neutral so HTTP, MCP, and test event loops can share one
    ordered writer without binding futures or locks to a particular loop.
    """

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], None],
        *,
        capacity: int = 4096,
        worker_name: str = "post-call-accounting",
        logger=None,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._handler = handler
        self._capacity = int(capacity)
        self._worker_name = worker_name
        self._logger = logger
        self._queue: queue.Queue = queue.Queue(maxsize=self._capacity)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._worker: threading.Thread | None = None
        self._accepting = False
        self._closing = False
        self._closed = False
        self._in_flight = 0
        self._pending_times: deque[float] = deque()
        self._next_sequence = 1
        self._enqueued_total = 0
        self._processed_total = 0
        self._succeeded_total = 0
        self._failed_total = 0
        self._discarded_total = 0
        self._rejected_total = 0
        self._high_watermark = 0
        self._degraded = False
        self._last_error: dict[str, Any] | None = None
        self._terminal_failure: dict[str, Any] | None = None
        self._worker_restarts = 0
        self._worker_fatal_errors = 0
        self._last_worker_fatal: dict[str, Any] | None = None

    def start(self) -> bool:
        """Start or lazily restart the dedicated worker."""
        with self._condition:
            if self._worker is not None and self._worker.is_alive():
                return self._accepting and not self._closing
            if self._closing or self._terminal_failure is not None:
                return False
            self._accepting = True
            self._closed = False
            self._worker = threading.Thread(
                target=self._run,
                name=self._worker_name,
                daemon=True,
            )
            self._worker.start()
            return True

    def _reject(self, reason: str, record: dict[str, Any]) -> None:
        with self._condition:
            self._rejected_total += 1
            self._degraded = True
            self._last_error = {
                "kind": "enqueue_rejected",
                "reason": reason,
                "accounting_id": str(record.get("accounting_id") or ""),
            }
            self._condition.notify_all()
        if self._logger is not None:
            self._logger.critical(
                "Post-call accounting rejected record id=%s reason=%s",
                record.get("accounting_id"),
                reason,
            )

    def enqueue(self, record: dict[str, Any]) -> bool:
        """Admit one record without blocking the caller or its event loop."""
        if not self.start():
            with self._condition:
                terminal = self._terminal_failure is not None
            self._reject("lane_terminal" if terminal else "lane_not_accepting", record)
            return False
        enqueued_at = time.monotonic()
        with self._condition:
            if not self._accepting or self._closing:
                self._reject("lane_closing", record)
                return False
            sequence = self._next_sequence
            self._next_sequence += 1
            item = dict(record)
            item["accounting_sequence"] = sequence
            try:
                self._queue.put_nowait((enqueued_at, item))
            except queue.Full:
                self._reject("queue_full", item)
                return False
            self._pending_times.append(enqueued_at)
            self._enqueued_total += 1
            self._high_watermark = max(self._high_watermark, len(self._pending_times))
            self._condition.notify_all()
            return True

    def _run(self) -> None:
        while True:
            queued_item = self._queue.get()
            if queued_item is _SENTINEL:
                self._queue.task_done()
                break
            _enqueued_at, record = queued_item
            with self._condition:
                self._in_flight = 1
                self._condition.notify_all()
            failure: BaseException | None = None
            fatal = False
            terminal = False
            try:
                self._handler(record)
            except Exception as exc:  # worker boundary must survive a bad sink
                failure = exc
                if self._logger is not None:
                    self._logger.error(
                        "Post-call accounting failed id=%s sequence=%s: %s",
                        record.get("accounting_id"),
                        record.get("accounting_sequence"),
                        exc,
                    )
            except (SystemExit, KeyboardInterrupt) as exc:
                # Process-control exceptions permanently close this lane.
                failure = exc
                terminal = True
                if self._logger is not None:
                    self._logger.critical(
                        "Post-call accounting worker terminated id=%s sequence=%s: %s",
                        record.get("accounting_id"),
                        record.get("accounting_sequence"),
                        exc,
                    )
            except BaseException as exc:
                failure = exc
                fatal = True
                if self._logger is not None:
                    self._logger.critical(
                        "Post-call accounting worker died id=%s sequence=%s: %s",
                        record.get("accounting_id"),
                        record.get("accounting_sequence"),
                        exc,
                        exc_info=True,
                    )
            finally:
                self._queue.task_done()
                with self._condition:
                    self._processed_total += 1
                    if failure is None:
                        self._succeeded_total += 1
                    else:
                        self._failed_total += 1
                        self._degraded = True
                        self._last_error = {
                            "kind": type(failure).__name__,
                            "reason": str(failure)[:300],
                            "accounting_id": str(record.get("accounting_id") or ""),
                            "sequence": record.get("accounting_sequence"),
                        }
                    self._in_flight = 0
                    if self._pending_times:
                        self._pending_times.popleft()
                    if terminal:
                        self._accepting = False
                        self._closed = True
                        discarded = 0
                        while True:
                            try:
                                abandoned = self._queue.get_nowait()
                            except queue.Empty:
                                break
                            self._queue.task_done()
                            if abandoned is not _SENTINEL:
                                discarded += 1
                        for _ in range(discarded):
                            if self._pending_times:
                                self._pending_times.popleft()
                        self._processed_total += discarded
                        self._failed_total += discarded
                        self._discarded_total += discarded
                        self._terminal_failure = {
                            **dict(self._last_error or {}),
                            "discarded": discarded,
                        }
                    if fatal:
                        self._worker_fatal_errors += 1
                        self._last_worker_fatal = dict(self._last_error or {})
                        if not self._closing:
                            self._worker_restarts += 1
                            self._worker = threading.Thread(
                                target=self._run,
                                name=self._worker_name,
                                daemon=True,
                            )
                            self._worker.start()
                    self._condition.notify_all()
            if terminal or fatal:
                return
        with self._condition:
            self._accepting = False
            self._closed = True
            self._condition.notify_all()

    def _status_locked(self) -> dict[str, Any]:
        pending = len(self._pending_times)
        oldest_age_ms = 0
        if self._pending_times:
            oldest_age_ms = max(0, int((time.monotonic() - self._pending_times[0]) * 1000))
        worker_alive = bool(self._worker is not None and self._worker.is_alive())
        return {
            "capacity": self._capacity,
            "queued": max(0, pending - self._in_flight),
            "in_flight": self._in_flight,
            "pending": pending,
            "high_watermark": self._high_watermark,
            "enqueued": self._enqueued_total,
            "processed": self._processed_total,
            "succeeded": self._succeeded_total,
            "failed": self._failed_total,
            "discarded": self._discarded_total,
            "rejected": self._rejected_total,
            "degraded": self._degraded,
            "last_error": dict(self._last_error) if self._last_error else None,
            "terminal_failure": (
                dict(self._terminal_failure) if self._terminal_failure else None
            ),
            "worker_restarts": self._worker_restarts,
            "worker_fatal_errors": self._worker_fatal_errors,
            "last_worker_fatal": (
                dict(self._last_worker_fatal) if self._last_worker_fatal else None
            ),
            "oldest_age_ms": oldest_age_ms,
            "accepting": self._accepting,
            "worker_alive": worker_alive,
            "closed": self._closed,
        }

    def status(self) -> dict[str, Any]:
        with self._condition:
            return self._status_locked()

    def flush(self, timeout: float = 10.0) -> dict[str, Any]:
        """Wait off-loop until all accepted records reach a terminal state."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while self._pending_times or (
                self._terminal_failure is not None
                and self._worker is not None
                and self._worker.is_alive()
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    report = self._status_locked()
                    report["drained"] = False
                    report["timed_out"] = True
                    return report
                self._condition.wait(min(remaining, 0.01))
            report = self._status_locked()
            report["drained"] = True
            report["timed_out"] = False
            return report

    def close(self, timeout: float = 10.0) -> dict[str, Any]:
        """Stop admission and drain accepted work within a bounded deadline."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            self._closing = True
            self._accepting = False
            worker = self._worker
        report = self.flush(max(0.0, deadline - time.monotonic()))
        terminal = report["terminal_failure"] is not None
        if report["drained"] and not terminal and worker is not None and worker.is_alive():
            try:
                self._queue.put_nowait(_SENTINEL)
            except queue.Full:
                report["drained"] = False
                report["timed_out"] = True
            if report["drained"]:
                worker.join(max(0.0, deadline - time.monotonic()))
        with self._condition:
            worker_alive = bool(worker is not None and worker.is_alive())
            if worker_alive or not report["drained"]:
                self._degraded = True
                if self._terminal_failure is None:
                    self._last_error = {
                        "kind": "shutdown_timeout",
                        "reason": "accounting lane did not drain before cleanup deadline",
                    }
            self._closing = False
            self._closed = not worker_alive
            final = self._status_locked()
            final["drained"] = bool(report["drained"] and not worker_alive)
            final["timed_out"] = not final["drained"]
            return final
