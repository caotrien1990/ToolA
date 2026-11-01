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
  boundaries as epoch-second floats, and emits a log line from the outer
  finally ("PHASE_TIMING ...") ONLY when the request total reaches 2.0 s, or
  on a 1% random baseline sample (D3 log-volume throttle; see should_emit()).
  Marks inside tool execution
  (fair_admitted, lane_started, doc_cache_wait_ms, read_token_done) reach the
  recorder through a ContextVar that run_in_executor_lane explicitly
  propagates into the lane worker thread (ThreadPoolExecutor does not
  propagate contextvars on its own).
- Boot: install() is the boot hook; the single INFO boot line
  "PHASE_TIMING enabled (threshold 2.0s, sample 1%)" is emitted there -- never
  at import time -- so a grepped boot line proves the flag is ACTIVE, not just
  that the module was loaded.

"received" is stamped at dispatcher entry -- it is the origin timestamp
(E30: NOT _t0, which is reset after the semaphore acquire). "sent" is stamped
at the end of the outer finally, i.e. when the response object is handed back
to aiohttp (not the socket write itself).
"""

from __future__ import annotations

import contextvars
import logging
import os
import random
import threading
import time

_OFF_VALUES = {"", "0", "off", "false", "no", "none"}

_ENABLED = os.environ.get("ORCH_PHASE_TIMING", "").strip().lower() not in _OFF_VALUES

logger = logging.getLogger(__name__)

# D3 (G0 amendment A'): per-request log throttle. One PHASE_TIMING line costs
# ~495 bytes; live sees ~170k calls/day, so a line per call would add
# ~85 MB/day and exhaust the 50MBx5 rotation before the 72h storm window ends.
_LOG_THRESHOLD_S = 2.0  # emit a phase line only when request total reaches this
_SAMPLE_RATE = 0.01      # ... plus a 1% random baseline sample

# install() idempotence guard (same pattern as lock_holder_trace).
_INSTALL_LOCK = threading.Lock()
_installed = False


def enabled() -> bool:
    """True only when the ORCH_PHASE_TIMING flag was ON at import time."""
    return _ENABLED


def install() -> bool:
    """Boot hook. No-op when the flag is OFF. Idempotent.

    The single INFO boot line proving ORCH_PHASE_TIMING is ACTIVE at boot is
    emitted HERE -- never at module import time. A3(5) greps the live log for
    "PHASE_TIMING enabled"; an import-time line could be emitted while the
    flag is OFF (false PASS), so the line must stay inside this flag-gated
    branch. Nothing at import time logs anything.
    """
    global _installed
    if not _ENABLED:
        return False
    with _INSTALL_LOCK:
        if _installed:
            return True
        _installed = True
    logger.info("PHASE_TIMING enabled (threshold 2.0s, sample 1%)")
    return True


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


def total_seconds(rec) -> float:
    """Request total in seconds (sent - received); 0.0 when unavailable."""
    if not rec:
        return 0.0
    try:
        sent = rec.get("sent")
        received = rec.get("received")
        if sent is None or received is None:
            return 0.0
        return float(sent) - float(received)
    except (TypeError, ValueError):
        return 0.0


def _sampled(seed=None) -> bool:
    """1% baseline sample. seed=None (default) -> module-random (production);
    a fixed seed -> deterministic draws (tests only)."""
    rng = random.Random(seed) if seed is not None else random
    return rng.random() < _SAMPLE_RATE


def should_emit(rec, seed=None) -> bool:
    """Throttle decision: True when total >= threshold, or on the 1% sample.
    False when rec is None (flag OFF). Production calls pass seed=None."""
    if rec is None:
        return False
    if total_seconds(rec) >= _LOG_THRESHOLD_S:
        return True
    return _sampled(seed)


def format_line(rec, *, tool: str = "", op_id: str = "", status: str = "",
                seed=None) -> str:
    """One greppable log line. Returns empty string when rec is None (flag OFF)
    or when the throttle drops this line (total < 2.0s and not in the 1%
    baseline sample). seed is test-only (deterministic sampling)."""
    if not should_emit(rec, seed=seed):
        return ""
    parts = ["PHASE_TIMING", "op=" + op_id, "tool=" + tool, "status=" + status]
    for name in _MARK_ORDER:
        if name in rec:
            parts.append(name + "=%.3f" % rec[name])
    if "doc_cache_wait_ms" in rec:
        parts.append("doc_cache_wait_ms=%.3f" % rec["doc_cache_wait_ms"])
    return " ".join(parts)
