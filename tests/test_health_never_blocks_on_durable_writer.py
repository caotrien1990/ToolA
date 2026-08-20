"""/health must not freeze the event loop while acquiring the durable writer.

Reported from the field on 2026-08-20: `GET /health` hung for >120s (curl timeout)
while MCP read/write kept working. That asymmetry is the tell -- MCP is a separate
transport in a separate process, so "one HTTP route dead, MCP alive" means the
aiohttp event loop stopped, not that the daemon died.

`handle_health` has two awaits, `_durable_metrics_for_health` and
`_controller_health_for_health`, both already bounded at 50ms after earlier
incidents. The hang was in neither. It was the line before them:
`_get_durable_audit_writer(start=True)` is a plain synchronous call, and
`DurableAuditWriter.start()` acquires `self._condition`. `wait_until_idle` (used by
the accounting flush and the shutdown drain, both with 10s budgets) re-acquires
that same condition around every `pending_count()`, so a busy audit DB holds it
repeatedly -- and the acquisition in /health had no deadline of any kind.

Measured before the fix, with the condition held 2.5s: `start()` blocked 2.500s and
the loop's heartbeat gap went from 0.05s to 2.531s. Not "slow /health" -- every
route on that loop stalled for as long as the condition was held.

These tests pin the property (bounded, loop stays live, and the timeout is reported
rather than swallowed) instead of the mechanism, so the guard survives a rewrite of
how the writer is acquired.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import orchestrator_server as srv  # noqa: E402
from orchestrator.server.audit_writer import DurableAuditWriter  # noqa: E402

# The condition is held far longer than any budget in the health path, so a test
# that finishes fast can only have done so by giving up rather than by waiting.
_HOLD_SECONDS = 3.0
_HEARTBEAT_INTERVAL = 0.05


@pytest.fixture()
def contended_writer(tmp_path, monkeypatch):
    """A started writer whose `_condition` is held by another thread."""
    writer = DurableAuditWriter(
        str(tmp_path / "audit.db"), lambda *a, **k: True, auto_start=False
    )
    writer.start()
    monkeypatch.setattr(srv, "_DURABLE_AUDIT", writer, raising=False)

    held = threading.Event()
    done = threading.Event()

    def hold_condition() -> None:
        with writer._condition:
            held.set()
            done.wait(_HOLD_SECONDS)

    thread = threading.Thread(target=hold_condition, daemon=True)
    thread.start()
    assert held.wait(timeout=5), "the holder thread never acquired the condition"
    try:
        yield writer
    finally:
        done.set()
        thread.join(timeout=5)
        try:
            writer.shutdown(2.0, drain=False)
        except Exception:
            pass


def test_health_metrics_give_up_instead_of_waiting_for_a_held_condition(
    contended_writer,
) -> None:
    async def probe() -> tuple[float, dict, str | None]:
        started = time.monotonic()
        metrics, error = await srv._durable_metrics_for_health()
        return time.monotonic() - started, metrics, error

    elapsed, metrics, error = asyncio.run(probe())
    assert elapsed < 1.0, (
        "acquiring the durable writer waited %.3fs on a condition held %.1fs; "
        "/health has no deadline on that acquisition" % (elapsed, _HOLD_SECONDS)
    )
    # Giving up must be VISIBLE. A fast answer that claims fresh metrics would be
    # worse than the hang, because a watchdog could not tell the difference.
    assert metrics.get("metrics_stale") is True
    assert error and "Timeout" in error, (
        "the contended acquisition must be reported, got %r" % (error,)
    )


def test_the_event_loop_keeps_running_while_the_condition_is_held(
    contended_writer,
) -> None:
    """The real symptom was a frozen loop, so assert on the loop, not on latency.

    A heartbeat task samples the clock every 50ms. If the health path blocks the
    loop, the gap between two samples grows to the length of the block -- that gap
    is what made every other route hang too.
    """

    async def probe() -> float:
        ticks: list[float] = []

        async def heartbeat() -> None:
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(_HEARTBEAT_INTERVAL)

        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.2)
        await srv._durable_metrics_for_health()
        # Tick at least once more AFTER the call, or the gap it caused is never
        # recorded -- the first version of this probe got a false pass that way.
        await asyncio.sleep(0.3)
        beat.cancel()
        assert len(ticks) >= 3, "the heartbeat did not run often enough to judge"
        return max(b - a for a, b in zip(ticks, ticks[1:]))

    worst_gap = asyncio.run(probe())
    assert worst_gap < 1.0, (
        "the event loop stalled %.3fs while the durable condition was held %.1fs; "
        "every route on that loop stalls with it" % (worst_gap, _HOLD_SECONDS)
    )


def test_the_health_path_never_starts_the_writer_thread() -> None:
    """Starting a worker thread is a mutation, and a probe must not perform one.

    The first fix bounded `_get_durable_audit_writer(start=True)` at 50ms instead
    of dropping it, and that budget was itself the second bug: measured with 3 busy
    threads, a COLD `asyncio.to_thread` cost 3032ms and warm ones 31-187ms, so
    /health would have reported `metrics_stale` forever under load and a watchdog
    could not have told a contended audit DB from a busy machine.

    Asserted on behaviour, not on the absence of a constant: a writer whose
    `start` would raise is handed to the health path, which must still answer.
    """
    class _StartWouldBeAMutation:
        def __init__(self) -> None:
            self.started = 0

        def start(self):  # pragma: no cover - must never be reached
            self.started += 1
            raise AssertionError("/health started the durable audit worker")

        def metrics(self):
            return {"worker_alive": True, "admission_open": True}

    probe = _StartWouldBeAMutation()
    original = srv._DURABLE_AUDIT
    srv._DURABLE_AUDIT = probe
    try:
        metrics, error = asyncio.run(srv._durable_metrics_for_health())
    finally:
        srv._DURABLE_AUDIT = original
    assert probe.started == 0, "the health path called start() on the writer"
    assert error is None, "an already-running writer reported %r" % (error,)
    assert metrics.get("worker_alive") is True


def test_health_does_not_wait_when_no_writer_exists_yet() -> None:
    """An absent writer must be reported instantly, and NOT as contention.

    Before HTTP boot runs `_post_call_accounting_startup` there is no writer object
    at all. That is a different fact from "the condition is held", and the previous
    version returned `TimeoutError: durable writer acquisition contended` for both
    -- which is precisely how a busy thread pool came to look like a busy audit DB.
    """
    original = srv._DURABLE_AUDIT
    srv._DURABLE_AUDIT = None
    try:
        started = time.monotonic()
        metrics, error = asyncio.run(srv._durable_metrics_for_health())
        elapsed = time.monotonic() - started
    finally:
        srv._DURABLE_AUDIT = original
    assert elapsed < 0.5, "reporting an absent writer took %.3fs" % elapsed
    assert error == "durable_audit_not_started", (
        "an absent writer was reported as %r; contention and not-yet-built must "
        "stay distinguishable" % (error,)
    )
    assert metrics.get("worker_alive") is False


def test_an_uncontended_writer_is_still_returned_normally(tmp_path, monkeypatch) -> None:
    """The bound must not cost /health its real answer in the normal case.

    Asserted over repeated probes rather than one, because the FIRST probe legitimately
    misses: `_durable_metrics_for_health` submits the collection to a background worker
    and waits only 50ms, and `asyncio.sleep(0.005)` on Windows actually sleeps ~16ms, so
    a cold submit under load routinely needs a second probe. One-shot assertions on
    `error is None` therefore flake -- this test did, in a combined run, and the flake
    was the thing that exposed the real defect: the 50ms budget was being spent on
    thread-pool scheduling, not on SQLite.

    What must hold is that a healthy writer eventually yields fresh metrics, and that a
    miss is reported as a stale-cache read rather than as an error about the writer.
    """
    writer = DurableAuditWriter(
        str(tmp_path / "audit.db"), lambda *a, **k: True, auto_start=False
    )
    writer.start()
    monkeypatch.setattr(srv, "_DURABLE_AUDIT", writer, raising=False)
    monkeypatch.setattr(srv, "_DURABLE_METRICS_FUTURE", None, raising=False)
    monkeypatch.setattr(srv, "_DURABLE_METRICS_CACHE_WRITER", None, raising=False)

    async def probe_until_fresh() -> tuple[dict, str | None, int]:
        last: tuple[dict, str | None] = ({}, "never ran")
        for attempt in range(40):
            metrics, error = await srv._durable_metrics_for_health()
            last = (metrics, error)
            if error is None:
                return metrics, error, attempt
            await asyncio.sleep(0.05)
        return last[0], last[1], 40

    try:
        metrics, error, attempts = asyncio.run(probe_until_fresh())
    finally:
        writer.shutdown(2.0, drain=False)
    assert error is None, (
        "an uncontended writer never produced fresh metrics in %d probes; last "
        "error %r" % (attempts, error)
    )
    assert metrics.get("metrics_stale") is False
    assert metrics.get("worker_alive") is True


def test_a_metrics_miss_says_what_timed_out(tmp_path, monkeypatch) -> None:
    """The timeout text is what a watchdog reads, so it must not be empty.

    `str(asyncio.TimeoutError())` is the empty string, so the bare raise in the
    metrics path surfaced as the literal "TimeoutError: " -- a field naming an
    exception class and saying nothing else. Under load that is the COMMON answer
    (measured 7 of 8 steady-state probes on this box), not a rare one.
    """

    class _NeverFinishes:
        def metrics(self):
            time.sleep(30)
            return {}

    monkeypatch.setattr(srv, "_DURABLE_AUDIT", _NeverFinishes(), raising=False)
    monkeypatch.setattr(srv, "_DURABLE_METRICS_FUTURE", None, raising=False)
    monkeypatch.setattr(srv, "_DURABLE_METRICS_CACHE_WRITER", None, raising=False)
    monkeypatch.setattr(srv, "_DURABLE_METRICS_EXECUTOR", None, raising=False)
    try:
        metrics, error = asyncio.run(srv._durable_metrics_for_health())
    finally:
        srv._DURABLE_METRICS_EXECUTOR = None
        srv._DURABLE_METRICS_FUTURE = None
    assert metrics.get("metrics_stale") is True
    assert error and error.startswith("TimeoutError"), error
    assert len(error) > len("TimeoutError: ") + 20, (
        "the timeout carries no message: %r" % (error,)
    )
    assert "ms" in error, "the timeout does not say what budget was missed: %r" % (error,)


def test_a_recent_answer_is_served_without_a_new_thread_handoff(
    tmp_path, monkeypatch
) -> None:
    """A fresh-enough metrics dict must be returned as fresh, not re-collected.

    This function used to submit a new collection on EVERY request and then wait
    50ms for it. Under load that made the cached value the usual answer while
    labelling it `metrics_stale`: measured with 3 busy threads, 1 of 8 steady-state
    probes came back fresh, and raising the budget to 400ms only reached 5 of 10
    while pushing p50 latency from 125ms to 406ms -- proving the budget was never
    the problem, the missing TTL was. The sibling controller probe has had one all
    along (`_CONTROLLER_HEALTH_TTL_SECONDS`).

    Counted in `metrics()` calls rather than in milliseconds, so the test does not
    depend on how the box happens to schedule threads.
    """

    class _CountingWriter:
        def __init__(self) -> None:
            self.calls = 0

        def metrics(self):
            self.calls += 1
            return {"worker_alive": True, "admission_open": True, "pending": 0}

    writer = _CountingWriter()
    monkeypatch.setattr(srv, "_DURABLE_AUDIT", writer, raising=False)
    monkeypatch.setattr(srv, "_DURABLE_METRICS_FUTURE", None, raising=False)
    monkeypatch.setattr(srv, "_DURABLE_METRICS_CACHE_WRITER", None, raising=False)
    monkeypatch.setattr(srv, "_DURABLE_METRICS_CACHE", {}, raising=False)
    monkeypatch.setattr(srv, "_DURABLE_METRICS_CACHE_AT", 0.0, raising=False)

    async def probe_repeatedly() -> list[tuple[dict, str | None]]:
        out = []
        for _ in range(12):
            out.append(await srv._durable_metrics_for_health())
            await asyncio.sleep(0.02)
        return out

    results = probe_repeatedly and asyncio.run(probe_repeatedly())
    fresh = [r for r in results if r[1] is None]
    assert len(fresh) >= 8, (
        "only %d of %d probes reported fresh metrics; the TTL is not being used"
        % (len(fresh), len(results))
    )
    assert all(m.get("worker_alive") is True for m, _ in fresh)
    # 12 probes inside a 2s TTL must not cost 12 collections.
    assert writer.calls <= 4, (
        "%d metrics() collections for 12 probes -- each request is still paying for "
        "its own thread handoff" % writer.calls
    )


def test_a_cached_answer_still_reports_its_real_age(tmp_path, monkeypatch) -> None:
    """Serving a cached dict is only honest if the age travels with it.

    The TTL is what stops /health paying a thread handoff per request; `metrics_age_ms`
    is what stops that becoming a lie. A watchdog must be able to see it is looking at
    a 1.9s-old answer even though `metrics_stale` is False.
    """

    class _Writer:
        def metrics(self):
            return {"worker_alive": True, "admission_open": True}

    writer = _Writer()
    monkeypatch.setattr(srv, "_DURABLE_AUDIT", writer, raising=False)
    monkeypatch.setattr(srv, "_DURABLE_METRICS_FUTURE", None, raising=False)
    # The cache must be attributed to THIS writer. `_durable_metrics_for_health`
    # discards a cache belonging to a different writer object, and rightly so: a
    # value collected from another writer is not a stale answer to this question,
    # it is an answer to a different one.
    monkeypatch.setattr(srv, "_DURABLE_METRICS_CACHE_WRITER", writer, raising=False)
    monkeypatch.setattr(
        srv, "_DURABLE_METRICS_CACHE", {"worker_alive": True}, raising=False
    )
    # Deliberately just inside the TTL: the answer is usable, and its age must show.
    aged = srv._DURABLE_METRICS_TTL_SECONDS * 0.9
    monkeypatch.setattr(
        srv, "_DURABLE_METRICS_CACHE_AT", time.monotonic() - aged, raising=False
    )
    metrics, error = asyncio.run(srv._durable_metrics_for_health())
    assert error is None
    assert metrics.get("metrics_stale") is False
    age = metrics.get("metrics_age_ms")
    assert isinstance(age, int) and age >= int(aged * 1000) - 50, (
        "a %.1fs-old cached answer reported age %r" % (aged, age)
    )


def test_the_ttl_is_short_enough_that_health_cannot_lag_a_watchdog() -> None:
    """A TTL longer than a watchdog interval would hide a real outage.

    The point of the cache is to stop paying a thread handoff per request, not to
    let /health keep describing a writer that died. The controller probe uses 5s;
    this one must be no looser than that.
    """
    ttl = srv._DURABLE_METRICS_TTL_SECONDS
    assert 0 < ttl <= srv._CONTROLLER_HEALTH_TTL_SECONDS, (
        "durable metrics TTL is %rs; it must stay within the controller probe's %rs"
        % (ttl, srv._CONTROLLER_HEALTH_TTL_SECONDS)
    )
