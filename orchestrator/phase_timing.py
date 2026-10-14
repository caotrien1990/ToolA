# -*- coding: utf-8 -*-
"""Phase-0 Step 0.4: per-request phase timestamps (ORCH_PHASE_TIMING).

Step 0.4 of PLAN_TOOLA_1210_FINAL instruments the request lifecycle so the
phase table can answer U2 (where did the 211 s go for E17/E30-style calls,
including RANGE_PAST_EOF).

Contract:
- Everything is behind the env flag ORCH_PHASE_TIMING (default OFF, read once
  at import time).
- Flag OFF: new_recorder() returns None; mark()/add_ms() are no-ops; nothing
  is written anywhere (no log lines, no metric rows). Behaviour is unchanged.
- Flag ON: the dispatcher creates one recorder per request, stamps phase
  boundaries as epoch-second floats, and emits ONE log line per request from
  the outer finally ("PHASE_TIMING ..."). Marks inside tool execution
  (fair_admitted, lane_started, doc_cache_wait_ms, read_token_done) reach the
  recorder through a ContextVar that run_in_executor_lane explicitly
  propagates into the lane worker thread (ThreadPoolExecutor does not
  propagate contextvars on its own).

"received" is stamped at dispatcher entry -- it is the origin timestamp
(E30: NOT _t0, which is reset after the semaphore acquire). "sent" is stamped
at the end of the outer finally, i.e. when the response object is handed back
to aiohttp (not the socket write itself).
"""

from __future__ import annotations

import contextvars
import os
import time

_OFF_VALUES = {"", "0", "off", "false", "no", "none"}

_ENABLED = os.environ.get("ORCH_PHASE_TIMING", "").strip().lower() not in _OFF_VALUES


def enabled() -> bool:
    """True only when the ORCH_PHASE_TIMING flag was ON at import time."""
    return _ENABLED


# The request currently being dispatched on this asyncio task (or lane worker
# thread, where run_in_executor_lane sets it explicitly). The VALUE is the
# recorder dict of the request; the dict object is shared, so marks stamped
# from a worker thread are visible to the dispatching task.
_current: contextvars.ContextVar = contextvars.ContextVar(
    "orch_phase_timing_recorder", default=None)


def new_recorder():
    """One recorder per request. Returns None when the flag is OFF."""
    if not _ENABLED:
        return None
    return {}


def set_current(rec):
    return _current.set(rec)


def reset_current(token) -> None:
    _current.reset(token)


def current():
    return _current.get()


def mark(rec, name: str, value=None) -> None:
    """Stamp a phase boundary (epoch seconds). No-op when rec is None."""
    if rec is None:
        return
    rec[name] = time.time() if value is None else float(value)


def add_ms(rec, name: str, ms: float) -> None:
    """Accumulate a duration in ms into rec[name]. No-op when rec is None."""
    if rec is None:
        return
    rec[name] = rec.get(name, 0.0) + float(ms)


# Phase boundaries, in lifecycle order. "doc_cache_wait_ms" is a duration,
# not a boundary; it is emitted alongside the boundaries. "outbox_staged" is
# an auxiliary mark (not in the plan required list): it isolates the E6
# durable-audit outbox COMMIT wait inside the finally stage.
_MARK_ORDER = (
    "received", "auth_done", "gate_done", "sem_acquired", "ledger_begin_done",
    "cache_lookup_done", "fair_admitted", "lane_started", "read_token_done",
    "tool_done", "post_tool_done", "outbox_staged", "finally_stage_done",
    "sent",
)


def format_line(rec, *, tool: str = "", op_id: str = "", status: str = "") -> str:
    """One greppable log line. Returns empty string when rec is None (flag OFF)."""
    if rec is None:
        return ""
    parts = ["PHASE_TIMING", "op=" + op_id, "tool=" + tool, "status=" + status]
    for name in _MARK_ORDER:
        if name in rec:
            parts.append(name + "=%.3f" % rec[name])
    if "doc_cache_wait_ms" in rec:
        parts.append("doc_cache_wait_ms=%.3f" % rec["doc_cache_wait_ms"])
    return " ".join(parts)
