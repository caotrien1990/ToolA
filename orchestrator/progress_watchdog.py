"""Progress-based watchdog primitives for unbounded explicit repository work."""
from __future__ import annotations

import threading
import time
import sys
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

    def progress(self, phase: str | None = None, *, completed: int | None = None,
                 total: int | None = None, increment: int = 0, detail: str = "") -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise OperationCancelled("operation cancellation requested")
        if phase:
            self.phase = phase
        if completed is not None:
            self.completed_units = int(completed)
        elif increment:
            self.completed_units += int(increment)
        if total is not None:
            self.total_units = int(total)
        self.revision += 1
        self.last_progress_mono = time.monotonic()
        if self.heartbeat is not None:
            self.heartbeat(self.snapshot(detail=detail))

    def checkpoint(self, phase: str | None = None) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise OperationCancelled(f"operation cancelled during {phase or self.phase}")

    def snapshot(self, *, detail: str = "") -> dict:
        now = time.monotonic()
        return {
            "operation_id": self.operation_id,
            "phase": self.phase,
            "completed_units": self.completed_units,
            "total_units": self.total_units,
            "progress_revision": self.revision,
            "seconds_since_progress": round(max(0.0, now - self.last_progress_mono), 3),
            "elapsed_seconds": round(max(0.0, now - self.started_mono), 3),
            "detail": detail,
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
