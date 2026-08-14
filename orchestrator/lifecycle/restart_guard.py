"""Pure fail-closed restart guard evaluation.

Anh 2026-08-10 (false-alarm hardening): the guard used to wedge a restart on
STALE council/broker state that carried no live worker at all — the "reports
in-flight but nothing is running" false alarm. Two concrete bugs are fixed here
while keeping the fail-closed protection for genuinely live work:

  1. Fake-process alarm (COUNCIL_PID_ALIVE): a council slot's `pid` field is the
     ORCHESTRATOR's own os.getpid() captured at dispatch time (constant for the
     life of an [A] generation). The old code fell back to it to prove worker
     liveness, so every active-state slot looked "alive" as long as [A] itself
     ran. Liveness is now proven ONLY by the real `worker_pid`.
  2. Stale-dispatch wedge (BROKER_DISPATCH_UNMATCHED): an unmatched dispatch from
     a council worker that errored hours ago (no reply, no live owner) is a
     historical FAILURE, not in-flight work. Such a dispatch no longer blocks;
     only a recent dispatch OR one owned by a live worker does. The staleness
     test mirrors tools_v15._force_drain_dispatch_is_stale so the guard and the
     force-drain path agree on exactly which records are drainable.

An active slot whose recorded worker is provably DEAD is likewise treated as
stale (worker crashed without updating its state). A slot in an active state
with NO usable worker_pid stays fail-closed (a just-dispatched worker may not
have published its pid yet); that case remains drainable via force_drain.
"""

from __future__ import annotations

import datetime as _dt
import json
import math as _math
import time as _time
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reasons: tuple[str, ...]
    blocking_guards: tuple[dict[str, object], ...] = ()


def describe_blocking_guards(reasons: Iterable[str]) -> tuple[dict[str, object], ...]:
    """Return stable, actionable metadata for every blocking guard reason."""
    stale = {
        "BUSY_SNAPSHOT_MISSING", "BUSY_SNAPSHOT_INVALID", "BUSY_SNAPSHOT_STALE",
        "BUSY_SNAPSHOT_GENERATION_INVALID", "BUSY_SNAPSHOT_GENERATION_MISMATCH",
        "COUNCIL_STATE_INVALID", "COUNCIL_PID_UNVERIFIED", "BROKER_STATE_INVALID",
        "BROKER_PARSE_FAILED", "BROKER_DISPATCH_UNMATCHED",
    }
    clearing = {
        "ORCHESTRATOR_BUSY": "wait for in-flight work to drain",
        "IN_FLIGHT_WORKERS_ACTIVE": "wait for active workers to finish, or force with explicit allow_force",
        "COUNCIL_SLOT_ACTIVE": "wait for the council worker or force-drain a stale slot",
        "COUNCIL_PID_ALIVE": "wait for or stop the live council worker",
        "BROKER_DISPATCH_UNMATCHED": "force-drain stale unmatched broker dispatches",
    }
    return tuple({
        "name": name,
        "held_by": "restart guard state",
        "is_stale": name in stale,
        "how_to_clear": clearing.get(name, "reload guard state after clearing the condition"),
    } for name in dict.fromkeys(reasons))


def _coerce_ts(value: object) -> float | None:
    """Best-effort parse of a broker/slot timestamp into epoch seconds.

    Accepts a finite epoch int/float, or the local-time ISO stamps the broker
    writes ("%Y-%m-%dT%H:%M:%S" and the microsecond variant). Booleans and
    non-finite / unparseable values return None (caller treats None age as
    "not provably old"). Mirrors tools_v15._force_drain_event_ts so the guard and
    the force-drain path agree on which records are provably stale.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return None if (_math.isnan(v) or _math.isinf(v)) else v
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return _time.mktime(_dt.datetime.strptime(text, fmt).timetuple())
            except ValueError:
                continue
    return None


def _worker_liveness(record: Mapping[str, object], pid_alive: Callable[[int], bool]) -> str:
    """Classify the liveness of a council slot / broker dispatch owner.

    Returns "alive" (a REAL worker process is confirmed running), "dead" (a
    worker_pid was recorded and is confirmed gone), or "unknown" (no usable
    worker_pid, or pid_alive rejected the value).

    Only `worker_pid` counts. The slot/dispatch `pid` field is the ORCHESTRATOR's
    own os.getpid() and must NEVER prove worker liveness (the fake-process alarm).
    """
    worker_pid = record.get("worker_pid")
    if worker_pid in (None, ""):
        return "unknown"
    try:
        pid_int = int(worker_pid)
    except (TypeError, ValueError):
        return "unknown"
    if pid_int <= 0:
        return "unknown"
    try:
        return "alive" if pid_alive(pid_int) else "dead"
    except (TypeError, ValueError, OSError):
        return "unknown"


def _dispatch_provably_stale(
    event: Mapping[str, object], now: float, *,
    stale_after_seconds: float, pid_alive: Callable[[int], bool],
) -> bool:
    """Return True only when an unmatched dispatch is PROVABLY stale.

    Conservative (returns False = preserve/block) unless BOTH hold:
      1. Age bound: `ts` is parseable, finite, not in the future, AND older than
         stale_after_seconds.
      2. No live owner: every PID-bearing field present parses as a positive int
         AND pid_alive() confirms the process is dead.
    Missing/invalid ts or unparseable PID evidence preserves the dispatch. Mirrors
    tools_v15._force_drain_dispatch_is_stale so guard-block and force-drain agree.
    """
    ts = _coerce_ts(event.get("ts"))
    if ts is None or ts > now or (now - ts) < stale_after_seconds:
        return False
    for key in ("worker_pid", "lease_pid", "owner_pid"):
        pid = event.get(key)
        if pid in (None, ""):
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            return False
        if pid_int <= 0:
            return False
        try:
            if pid_alive(pid_int):
                return False
        except (TypeError, ValueError, OSError):
            return False
    return True


def evaluate_restart_guard(
    *,
    busy_snapshot: Mapping[str, object] | None,
    council_slots: Iterable[Mapping[str, object]],
    broker_lines: Iterable[str],
    now: float,
    snapshot_max_age_seconds: float,
    pid_alive: Callable[[int], bool],
    expected_generation: str,
    expected_request_id: str = "",
    allow_force: bool = False,
    dispatch_stale_seconds: float = 600.0,
) -> GuardDecision:
    reasons: list[str] = []
    if not isinstance(busy_snapshot, Mapping):
        reasons.append("BUSY_SNAPSHOT_MISSING")
    else:
        captured_at = busy_snapshot.get("captured_at")
        if not isinstance(captured_at, (int, float)):
            reasons.append("BUSY_SNAPSHOT_INVALID")
        elif captured_at > now or now - float(captured_at) > snapshot_max_age_seconds:
            reasons.append("BUSY_SNAPSHOT_STALE")
        matching_restart_request = bool(
            expected_request_id
            and busy_snapshot.get("restart_request_id") == expected_request_id
            and busy_snapshot.get("requests_in_flight") == 1
            and not busy_snapshot.get("executor_in_flight")
            and not busy_snapshot.get("accounting_pending")
            and not busy_snapshot.get("semaphores")
        )
        hung_or_stale = bool(busy_snapshot.get("is_stale_or_hung"))
        if (
            busy_snapshot.get("busy") is not False
            and not matching_restart_request
            and not hung_or_stale
        ):
            reasons.append("ORCHESTRATOR_BUSY")
        generation = busy_snapshot.get("generation")
        if not isinstance(generation, str) or not generation:
            reasons.append("BUSY_SNAPSHOT_GENERATION_INVALID")
        elif generation != expected_generation:
            reasons.append("BUSY_SNAPSHOT_GENERATION_MISMATCH")

    try:
        slots = list(council_slots)
    except Exception:
        slots = []
        reasons.append("COUNCIL_STATE_INVALID")
    active_states = {"running", "starting", "queued"}
    for slot in slots:
        if not isinstance(slot, Mapping):
            reasons.append("COUNCIL_STATE_INVALID")
            continue
        state = str(slot.get("state", slot.get("status", ""))).lower()
        if state not in active_states:
            # Terminal slots (completed/error/timeout/stopped) never block.
            continue
        liveness = _worker_liveness(slot, pid_alive)
        if liveness == "dead":
            # Active state but the recorded worker process is provably gone: the
            # slot is stale (worker crashed without updating state). Do NOT block
            # — this was the "reports in-flight but no worker is running" false
            # alarm. force_drain / the terminal-state writer tidies it later.
            continue
        # Live worker OR liveness-unknown (no recorded worker_pid): protect the
        # slot fail-closed. A just-dispatched worker may not have published its
        # pid yet; a stale no-pid slot stays drainable via force_drain.
        reasons.append("COUNCIL_SLOT_ACTIVE")
        if liveness == "alive":
            reasons.append("COUNCIL_PID_ALIVE")

    # --- C3 DURABLE: worker-protection hard gate ---
    # A restart MUST NOT silently kill active workers. When executor_in_flight > 1
    # (meaning at least one OTHER worker is running besides the restart requester),
    # reject unless explicitly forced with allow_force=True. Hung leftovers
    # (is_stale_or_hung) are in-process stalls that [A:S] is designed to kill.
    if not allow_force and isinstance(busy_snapshot, Mapping):
        _executor_count = busy_snapshot.get("executor_in_flight")
        if (
            isinstance(_executor_count, (int, float))
            and int(_executor_count) > 1
            and not busy_snapshot.get("is_stale_or_hung")
        ):
            reasons.append("IN_FLIGHT_WORKERS_ACTIVE")

    dispatches: dict[str, Mapping[str, object]] = {}
    replies: set[str] = set()
    try:
        lines = list(broker_lines)
    except Exception:
        lines = []
        reasons.append("BROKER_STATE_INVALID")
    for line in lines:
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("event is not an object")
            event_type = str(event.get("type", ""))
            if event_type in {"message", "dispatch"}:
                event_id = event.get("id")
                if not isinstance(event_id, str) or not event_id:
                    raise ValueError("dispatch id missing")
                dispatches[event_id] = event
            elif event_type == "reply":
                reply_to = event.get("in_reply_to")
                if not isinstance(reply_to, str) or not reply_to:
                    raise ValueError("reply target missing")
                replies.add(reply_to)
        except (json.JSONDecodeError, TypeError, ValueError):
            reasons.append("BROKER_PARSE_FAILED")
    # An unmatched dispatch only blocks when it is plausibly still in flight:
    # recent, or owned by a live worker. A dispatch that is PROVABLY stale (old
    # AND no live owner) is a historical council failure — it must never wedge
    # the restart guard forever.
    for dispatch_id in (set(dispatches) - replies):
        event = dispatches[dispatch_id]
        if not isinstance(event, Mapping) or not _dispatch_provably_stale(
            event, now, stale_after_seconds=dispatch_stale_seconds, pid_alive=pid_alive
        ):
            reasons.append("BROKER_DISPATCH_UNMATCHED")
    return GuardDecision(not reasons, tuple(dict.fromkeys(reasons)), describe_blocking_guards(reasons))
