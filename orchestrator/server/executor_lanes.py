"""Bounded, observable executor lanes for blocking orchestrator work.

Each lane owns its ThreadPoolExecutor so a saturated workload cannot consume the
asyncio default executor used by unrelated request paths.  Capacity includes
running plus queued work; overflow is rejected immediately instead of creating
an unbounded ThreadPoolExecutor queue.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable
from orchestrator.server.fair_admission import FAIR_ADMISSION

class ExecutorLaneError(RuntimeError):
    error_name = "EXECUTOR_LANE_ERROR"
    rejection_reason = "lane_error"

    def __init__(self, lane: str, message: str, status: dict[str, Any]):
        super().__init__(message)
        self.lane = lane
        self.status = status

    def as_payload(self) -> dict[str, Any]:
        overloaded = isinstance(self, ExecutorLaneOverloaded)
        retry_after = max(1, int(float(self.status.get("queue_wait_timeout_seconds", 1)) + 0.999)) if overloaded else None
        return {
            "ok": False,
            "error": str(self),
            "reason": self.rejection_reason,
            "error_name": self.error_name,
            "lane": self.lane,
            "metrics": self.status,
            "executor_lane": self.lane,
            "executor": self.status,
            "retryable": overloaded,
            "retry_after_seconds": retry_after,
            "ca_next_action": (
                "wait_for_retry_after_then_retry_with_backoff; do_not_retry_immediately_or_unchanged"
                if overloaded else "wait_for_orchestrator_restart"
            ),
        }


class ExecutorLaneOverloaded(ExecutorLaneError):
    error_name = "TOOL_OVERLOADED"
    rejection_reason = "lane_full_at_submission"


class ExecutorLaneQueueWaitExceeded(ExecutorLaneOverloaded):
    error_name = "LANE_QUEUE_WAIT_EXCEEDED"
    rejection_reason = "queue_wait_budget_exceeded"


class ExecutorLaneClosed(ExecutorLaneError):
    error_name = "EXECUTOR_SHUTTING_DOWN"
    rejection_reason = "lane_closed"


class ExecutorLane:
    """One bounded ThreadPoolExecutor with thread-safe lifecycle metrics."""

    def __init__(self, name: str, *, max_workers: int, max_queue: int, queue_wait_timeout: float = 5.0):
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if max_queue < 0:
            raise ValueError("max_queue must be >= 0")
        if queue_wait_timeout <= 0:
            raise ValueError("queue_wait_timeout must be > 0")
        self.name = str(name)
        self.max_workers = int(max_workers)
        self.max_queue = int(max_queue)
        self.capacity = self.max_workers + self.max_queue
        self.queue_wait_timeout = float(queue_wait_timeout)
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=f"orch-{self.name}")
        self._lock = threading.RLock()
        self._accepting = True
        self._shutdown_called = False
        self._active = self._queued = self._submitted = self._started = 0
        self._completed = self._failed = self._rejected = 0
        self._cancelled = self._caller_cancelled = self._high_watermark = 0
        self._total_queue_wait_ms = self._max_queue_wait_ms = 0.0
        self._queue_wait_observations = 0
        self._total_service_time_ms = self._max_service_time_ms = 0.0
        self._rejection_counts = {"lane_full_at_submission": 0, "queue_wait_budget_exceeded": 0, "caller_cancelled": 0, "lane_closed": 0}
        self._pending_since: dict[str, float] = {}
        self._active_since: dict[str, float] = {}
        self._futures: dict[str, Future] = {}

    @property
    def accepting(self) -> bool:
        with self._lock:
            return self._accepting

    def _status_locked(self) -> dict[str, Any]:
        now = time.monotonic()
        oldest_wait_ms = max(0.0, (now - min(self._pending_since.values())) * 1000.0) if self._pending_since else 0.0
        oldest_active_age_ms = max(0.0, (now - min(self._active_since.values())) * 1000.0) if self._active_since else 0.0
        return {
            "name": self.name, "max_workers": self.max_workers, "max_queue": self.max_queue,
            "capacity": self.capacity, "queue_wait_timeout_seconds": self.queue_wait_timeout,
            "active": self._active, "queued": self._queued, "in_flight": self._active + self._queued,
            "submitted": self._submitted, "started": self._started, "completed": self._completed,
            "failed": self._failed, "rejected": self._rejected, "rejection_counts": dict(self._rejection_counts),
            "cancelled": self._cancelled, "caller_cancelled": self._caller_cancelled,
            "high_watermark": self._high_watermark, "oldest_wait_ms": round(oldest_wait_ms, 3),
            "oldest_active_age_ms": round(oldest_active_age_ms, 3), "max_queue_wait_ms": round(self._max_queue_wait_ms, 3),
            "avg_queue_wait_ms": round(self._total_queue_wait_ms / self._queue_wait_observations, 3) if self._queue_wait_observations else 0.0,
            "max_service_time_ms": round(self._max_service_time_ms, 3),
            "avg_service_time_ms": round(self._total_service_time_ms / self._completed, 3) if self._completed else 0.0,
            "accepting": self._accepting, "shutdown_called": self._shutdown_called,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def _invoke(self, work_id: str, submitted_at: float, func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        started_at = time.monotonic()
        with self._lock:
            if work_id not in self._pending_since:
                raise ExecutorLaneQueueWaitExceeded(self.name, f"executor lane '{self.name}' queue wait budget expired", self._status_locked())
            self._pending_since.pop(work_id, None)
            self._active_since[work_id] = started_at
            self._queued = max(0, self._queued - 1)
            self._active += 1
            self._started += 1
            wait_ms = max(0.0, (started_at - submitted_at) * 1000.0)
            self._total_queue_wait_ms += wait_ms
            self._queue_wait_observations += 1
            self._max_queue_wait_ms = max(self._max_queue_wait_ms, wait_ms)
        failed = False
        try:
            return func(*args, **kwargs)
        except BaseException:
            failed = True
            raise
        finally:
            finished_at = time.monotonic()
            with self._lock:
                self._active_since.pop(work_id, None)
                self._active = max(0, self._active - 1)
                self._completed += 1
                service_ms = max(0.0, (finished_at - started_at) * 1000.0)
                self._total_service_time_ms += service_ms
                self._max_service_time_ms = max(self._max_service_time_ms, service_ms)
                if failed:
                    self._failed += 1

    def _future_done(self, work_id: str, future: Future) -> None:
        with self._lock:
            self._futures.pop(work_id, None)
            if future.cancelled() and work_id in self._pending_since:
                self._pending_since.pop(work_id, None)
                self._queued = max(0, self._queued - 1)
                self._completed += 1
                self._cancelled += 1

    def _expire_queued(self, work_id: str, future: Future) -> ExecutorLaneQueueWaitExceeded | None:
        with self._lock:
            if work_id not in self._pending_since:
                return None
            wait_ms = max(0.0, (time.monotonic() - self._pending_since[work_id]) * 1000.0)
            self._total_queue_wait_ms += wait_ms
            self._queue_wait_observations += 1
            self._max_queue_wait_ms = max(self._max_queue_wait_ms, wait_ms)
            self._rejected += 1
            self._rejection_counts["queue_wait_budget_exceeded"] += 1
            self._pending_since.pop(work_id, None)
            self._queued = max(0, self._queued - 1)
            error = ExecutorLaneQueueWaitExceeded(self.name, f"executor lane '{self.name}' did not start within {self.queue_wait_timeout:.3f}s queue wait budget", self._status_locked())
        future.cancel()
        return error

    def _submit(self, func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Future, str]:
        submitted_at = time.monotonic()
        work_id = uuid.uuid4().hex
        with self._lock:
            if not self._accepting:
                self._rejected += 1
                self._rejection_counts["lane_closed"] += 1
                raise ExecutorLaneClosed(self.name, f"executor lane '{self.name}' is shutting down", self._status_locked())
            if self._active + self._queued >= self.capacity:
                self._rejected += 1
                self._rejection_counts["lane_full_at_submission"] += 1
                raise ExecutorLaneOverloaded(self.name, f"executor lane '{self.name}' capacity {self.capacity} is full", self._status_locked())
            self._queued += 1
            self._submitted += 1
            self._pending_since[work_id] = submitted_at
            self._high_watermark = max(self._high_watermark, self._active + self._queued)
            try:
                future = self._executor.submit(self._invoke, work_id, submitted_at, func, args, kwargs)
            except BaseException:
                self._queued = max(0, self._queued - 1)
                self._submitted = max(0, self._submitted - 1)
                self._pending_since.pop(work_id, None)
                raise
            self._futures[work_id] = future
            future.add_done_callback(lambda done, wid=work_id: self._future_done(wid, done))
            return future, work_id

    async def run(self, func: Callable[..., Any], /, *args: Any, queue_wait_deadline: float | None = None,
                  completion_callback=None, **kwargs: Any) -> Any:
        future, work_id = self._submit(func, args, kwargs)
        if completion_callback is not None:
            future.add_done_callback(completion_callback)
        wrapped = asyncio.wrap_future(future)
        deadline_at = queue_wait_deadline or (time.monotonic() + self.queue_wait_timeout)
        deadline = asyncio.create_task(asyncio.sleep(max(0.0, deadline_at - time.monotonic())))
        try:
            done, _ = await asyncio.wait({wrapped, deadline}, return_when=asyncio.FIRST_COMPLETED)
            if wrapped in done:
                return await wrapped
            error = self._expire_queued(work_id, future)
            if error is not None:
                raise error
            return await wrapped
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            with self._lock:
                shutdown_cancelled = (self._shutdown_called and future.cancelled()
                                      and (current_task is None or current_task.cancelling() == 0))
                if shutdown_cancelled:
                    error = ExecutorLaneClosed(self.name,
                        f"executor lane '{self.name}' cancelled queued work during shutdown", self._status_locked())
                else:
                    self._caller_cancelled += 1
                    self._rejection_counts["caller_cancelled"] += 1
                    error = None
            if error is not None:
                raise error
            future.cancel()
            raise
        finally:
            deadline.cancel()
    def submit(self, func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        """Submit work while preserving bounded admission and lane metrics."""
        future, _ = self._submit(func, args, kwargs)
        return future

    async def close(self, *, drain_timeout: float = 10.0) -> dict[str, Any]:
        with self._lock:
            self._accepting = False
        deadline = time.monotonic() + max(0.0, float(drain_timeout))
        while True:
            with self._lock:
                drained = self._active == 0 and self._queued == 0
            if drained or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.01)

        with self._lock:
            if not self._shutdown_called:
                self._shutdown_called = True
                shutdown_now = True
            else:
                shutdown_now = False
        if shutdown_now:
            self._executor.shutdown(wait=drained, cancel_futures=not drained)
        result = self.status()
        result["drained"] = drained
        result["drain_timeout_seconds"] = max(0.0, float(drain_timeout))
        return result


_LANE_DEFAULTS: dict[str, tuple[int, int]] = {
    "read_io": (12, 48), "search": (8, 24), "command": (4, 16), "test": (2, 6),
    "git": (4, 16), "backup": (1, 2), "council_provider": (8, 32),
}
_LANE_QUEUE_WAIT_DEFAULTS = {
    "read_io": 2.0, "search": 2.0, "command": 5.0, "test": 10.0,
    "git": 5.0, "backup": 5.0, "council_provider": 10.0,
}
_QUEUE_WAIT_MIN_SECONDS = 0.05
_QUEUE_WAIT_MAX_SECONDS = 60.0
_LANES: dict[str, ExecutorLane] = {}
_LANES_LOCK = threading.RLock()
_LANES_CLOSING = False


def _lane_config(name: str) -> tuple[int, int, float]:
    default_workers, default_queue = _LANE_DEFAULTS[name]
    env_name = name.upper()
    workers = max(1, int(os.environ.get(f"ORCH_EXEC_{env_name}_WORKERS", default_workers)))
    queue_size = max(0, int(os.environ.get(f"ORCH_EXEC_{env_name}_QUEUE", default_queue)))
    raw_wait = float(os.environ.get(f"ORCH_EXEC_{env_name}_QUEUE_WAIT_SECONDS", _LANE_QUEUE_WAIT_DEFAULTS[name]))
    queue_wait_timeout = min(_QUEUE_WAIT_MAX_SECONDS, max(_QUEUE_WAIT_MIN_SECONDS, raw_wait))
    return workers, queue_size, queue_wait_timeout


def _idle_lane_status(name: str, *, registry_closing: bool) -> dict[str, Any]:
    """Describe an uncreated lane without allocating a ThreadPoolExecutor."""
    workers, queue_size, queue_wait_timeout = _lane_config(name)
    return {
        "name": name, "max_workers": workers, "max_queue": queue_size,
        "capacity": workers + queue_size, "queue_wait_timeout_seconds": queue_wait_timeout,
        "active": 0, "queued": 0, "in_flight": 0, "submitted": 0, "started": 0,
        "completed": 0, "failed": 0, "rejected": 0,
        "rejection_counts": {"lane_full_at_submission": 0, "queue_wait_budget_exceeded": 0, "caller_cancelled": 0, "lane_closed": 0},
        "cancelled": 0, "caller_cancelled": 0, "high_watermark": 0,
        "oldest_wait_ms": 0.0, "oldest_active_age_ms": 0.0,
        "max_queue_wait_ms": 0.0, "avg_queue_wait_ms": 0.0,
        "max_service_time_ms": 0.0, "avg_service_time_ms": 0.0,
        "accepting": not registry_closing, "shutdown_called": False,
        "registry_closing": registry_closing,
    }


def _registry_lane_status(
    name: str,
    lane: ExecutorLane | None,
    *,
    registry_closing: bool,
) -> dict[str, Any]:
    row = lane.status() if lane is not None else _idle_lane_status(
        name,
        registry_closing=registry_closing,
    )
    row["registry_closing"] = registry_closing
    return row


def _registry_closed_error(name: str) -> ExecutorLaneClosed:
    lane = _LANES.get(name)
    status = _registry_lane_status(name, lane, registry_closing=True)
    return ExecutorLaneClosed(
        name,
        f"executor lane registry is shutting down; lane '{name}' is closed",
        status,
    )


def get_executor_lane(name: str) -> ExecutorLane:
    if name not in _LANE_DEFAULTS:
        raise KeyError(f"unknown executor lane: {name}")
    with _LANES_LOCK:
        if _LANES_CLOSING:
            raise _registry_closed_error(name)
        lane = _LANES.get(name)
        if lane is None or not lane.accepting:
            workers, queue_size, queue_wait_timeout = _lane_config(name)
            lane = ExecutorLane(name, max_workers=workers, max_queue=queue_size, queue_wait_timeout=queue_wait_timeout)
            _LANES[name] = lane
        return lane


async def run_in_executor_lane(
    name: str,
    func: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    lane = get_executor_lane(name)
    deadline = time.monotonic() + lane.queue_wait_timeout
    permit = await FAIR_ADMISSION.acquire(name, deadline=deadline)
    try:
        return await lane.run(func, *args, queue_wait_deadline=deadline,
                              completion_callback=lambda _done: permit.release(), **kwargs)
    except BaseException:
        # No Future means no completion callback. Idempotence covers all races.
        permit.release()
        raise


def executor_lane_status() -> list[dict[str, Any]]:
    with _LANES_LOCK:
        lanes = {name: _LANES.get(name) for name in _LANE_DEFAULTS}
        registry_closing = _LANES_CLOSING
    rows = [_registry_lane_status(name, lanes[name], registry_closing=registry_closing)
            for name in _LANE_DEFAULTS]
    fair = FAIR_ADMISSION.status()
    for row in rows:
        row["fair_admission"] = fair
    return rows


async def shutdown_executor_lanes(*, drain_timeout: float = 10.0) -> list[dict[str, Any]]:
    global _LANES_CLOSING
    with _LANES_LOCK:
        _LANES_CLOSING = True
        FAIR_ADMISSION.close()
        lanes = list(_LANES.values())
    if not lanes:
        return []
    results = await asyncio.gather(
        *(lane.close(drain_timeout=drain_timeout) for lane in lanes),
        return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    for lane, result in zip(lanes, results):
        if isinstance(result, BaseException):
            row = lane.status()
            row.update({"drained": False, "shutdown_error": str(result)})
            rows.append(row)
        else:
            rows.append(result)
    return rows


def _reset_executor_lanes_for_tests() -> None:
    """Explicit test-only reset; never discard a live or unclosed executor."""
    global _LANES_CLOSING
    with _LANES_LOCK:
        unsafe = []
        for name, lane in _LANES.items():
            status = lane.status()
            if status["accepting"] or status["in_flight"] or not status["shutdown_called"]:
                unsafe.append(name)
        if unsafe:
            raise RuntimeError(
                "cannot reset executor lane registry before shutdown drains: "
                + ", ".join(sorted(unsafe))
            )
        _LANES.clear()
        _LANES_CLOSING = False
        FAIR_ADMISSION.reset_for_tests()
