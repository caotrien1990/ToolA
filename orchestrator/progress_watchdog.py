"""Progress-based watchdog primitives for unbounded explicit repository work."""
from __future__ import annotations

import sys
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable


class OperationCancelled(RuntimeError):
    pass


@dataclass
class ProgressWatchdog:
    operation_id: str = ""
    heartbeat: Callable[[dict], None] | None = None
    cancel_event: threading.Event | None = None
    phase: str = "starting"
    completed_units: int = 0
    total_units: int | None = None
    revision: int = 0
    started_mono: float = field(default_factory=time.monotonic)
    last_progress_mono: float = field(default_factory=time.monotonic)
    # Wall time per phase in ms, cumulative per name. #2: a 5-line write_fix took
    # 323602ms and its ledger said only duration_ms, so "contention between two calls
    # or validation on the HDD" stayed unknown -- nothing said where the time went.
    phase_ms: dict[str, float] = field(default_factory=dict)
    phase_started_mono: float = field(default_factory=time.monotonic)

    def progress(self, phase: str | None = None, *, completed: int | None = None,
                 total: int | None = None, increment: int = 0, detail: str = "") -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise OperationCancelled("operation cancellation requested")
        self._enter_phase(phase)
        if completed is not None:
            self.completed_units = int(completed)
        elif increment:
            self.completed_units += int(increment)
        if total is not None:
            self.total_units = int(total)
        self._publish(detail)

    def mark(self, phase: str, *, detail: str = "") -> None:
        """Enter `phase` for timing and liveness only.

        Unlike progress() this is NOT a cancellation point: a write marks its phases
        after the file is committed and before it is validated, and a cancel raised
        there would leave a committed file that nothing validated or reverted.
        """
        self._enter_phase(phase)
        self._publish(detail)

    def _enter_phase(self, phase: str | None) -> None:
        if not phase or phase == self.phase:
            return
        now = time.monotonic()
        self.phase_ms[self.phase] = (self.phase_ms.get(self.phase, 0.0)
                                     + (now - self.phase_started_mono) * 1000.0)
        self.phase = phase
        self.phase_started_mono = now
        # A phase owns its OWN units. Before this reset a new phase inherited the
        # previous phase's counters, and a phase that only ever passes increment=
        # never overwrote the stale total -- measured 2026-09-22 as a published
        # snapshot of completed_units=1021 / total_units=16 (snapshot_gc_blobs
        # counting blobs against a 16 left behind by snapshot_gc_generations).
        # 1021/16 is worse than no total at all: it reads as a finished phase 64x
        # over budget, so a CA sizing a client timeout off it abandons a call that
        # is in fact progressing. total_units=None is the honest "this phase has
        # not declared a total" -- the same value a fresh watchdog publishes, so
        # every consumer already handles it. An explicit completed=/total= below
        # still wins, which is why the reset runs BEFORE them.
        self.completed_units = 0
        self.total_units = None

    def _publish(self, detail: str) -> None:
        self.revision += 1
        self.last_progress_mono = time.monotonic()
        if self.heartbeat is not None:
            self.heartbeat(self.snapshot(detail=detail))

    def checkpoint(self, phase: str | None = None) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise OperationCancelled(f"operation cancelled during {phase or self.phase}")

    def snapshot(self, *, detail: str = "") -> dict:
        now = time.monotonic()
        # The open phase is charged up to NOW, so a snapshot taken when the operation
        # ends closes its last phase instead of dropping it.
        phase_ms = dict(self.phase_ms)
        phase_ms[self.phase] = (phase_ms.get(self.phase, 0.0)
                                + (now - self.phase_started_mono) * 1000.0)
        return {
            "operation_id": self.operation_id,
            "phase": self.phase,
            "completed_units": self.completed_units,
            "total_units": self.total_units,
            "progress_revision": self.revision,
            "seconds_since_progress": round(max(0.0, now - self.last_progress_mono), 3),
            "elapsed_seconds": round(max(0.0, now - self.started_mono), 3),
            "detail": detail,
            "phase_ms": {name: round(ms, 1) for name, ms in phase_ms.items()},
        }


_CURRENT: ContextVar[ProgressWatchdog | None] = ContextVar(
    "orchestrator_progress_watchdog", default=None)


def bind_watchdog(watchdog: ProgressWatchdog):
    return _CURRENT.set(watchdog)


def reset_watchdog(token) -> None:
    _CURRENT.reset(token)


def current_watchdog() -> ProgressWatchdog | None:
    return _CURRENT.get()


def ledger_heartbeat(operation_id: str, progress: dict) -> None:
    server = sys.modules.get("__main__") or sys.modules.get("orchestrator_server")
    callback = getattr(server, "_write_ledger_heartbeat", None)
    if callback is not None:
        callback(operation_id, progress)


__all__ = ["OperationCancelled", "ProgressWatchdog", "bind_watchdog",
           "reset_watchdog", "current_watchdog", "ledger_heartbeat"]
