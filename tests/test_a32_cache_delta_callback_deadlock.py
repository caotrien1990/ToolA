"""Pin the cache_delta done-callback self-deadlock (recorded 2026-09-17).

`_CACHE_DELTA_FUTURES_LOCK` is a plain, non-reentrant `threading.Lock`.
`Future.add_done_callback` invokes the callback INLINE on the calling thread when the
future has already finished, and the callback (`_forget_cache_delta_future`) acquires that
same lock -- so registering the callback while still holding the lock wedges the caller
against itself, permanently. A real full-suite run froze exactly there, reached through
`post_write_batch_verify` on the daemon's own write path, and no timeout can rescue a
thread blocked on a lock it owns.

The race window in production is microseconds wide, so every behavioural test here forces
it deterministically with an executor whose futures are already done. Each coroutine runs
on its own watchdog thread rather than on the pytest thread: if the bug ever comes back,
this file FAILS with a message instead of hanging the suite the way the original run hung.
"""

from __future__ import annotations

import ast
import asyncio
import threading
from concurrent.futures import Future
from pathlib import Path

import pytest

import code_tools as ct

WATCHDOG_SECONDS = 10.0
CONTROL_SECONDS = 2.0


class _AlreadyDoneExecutor:
    """An executor whose futures are complete before `submit` returns.

    This is the whole point of the harness: it turns a microsecond race that only shows up
    on a loaded machine into the guaranteed path.
    """

    def __init__(self, result: str = "{}") -> None:
        self.result = result
        self.submissions: list[tuple] = []

    def submit(self, fn, *args, **kwargs) -> Future:
        self.submissions.append((fn, args, kwargs))
        future: Future = Future()
        future.set_result(self.result)
        return future


class _PendingExecutor:
    """An executor that hands back futures nobody has completed yet."""

    def __init__(self) -> None:
        self.submissions: list[tuple] = []
        self.futures: list[Future] = []

    def submit(self, fn, *args, **kwargs) -> Future:
        self.submissions.append((fn, args, kwargs))
        future: Future = Future()
        self.futures.append(future)
        return future


def _run_with_watchdog(coro_factory, seconds: float = WATCHDOG_SECONDS):
    """Run one coroutine on a throwaway thread. Returns (finished, value, error)."""
    box: dict[str, object] = {}

    def _target() -> None:
        try:
            box["value"] = asyncio.run(coro_factory())
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=seconds)
    return (not thread.is_alive()), box.get("value"), box.get("error")


def _callbacks_registered_under_the_lock(source: str) -> list[str]:
    """Functions that call `add_done_callback` inside a `with _CACHE_DELTA_FUTURES_LOCK:`."""
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.With):
                continue
            holds_the_lock = any(
                isinstance(item.context_expr, ast.Name)
                and item.context_expr.id == "_CACHE_DELTA_FUTURES_LOCK"
                for item in inner.items
            )
            if not holds_the_lock:
                continue
            for stmt in inner.body:
                for call in ast.walk(stmt):
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "add_done_callback"
                    ):
                        offenders.append(node.name)
    return sorted(set(offenders))


@pytest.fixture()
def clean_future_registry():
    saved = dict(ct._CACHE_DELTA_FUTURES)
    ct._CACHE_DELTA_FUTURES.clear()
    try:
        yield ct._CACHE_DELTA_FUTURES
    finally:
        ct._CACHE_DELTA_FUTURES.clear()
        ct._CACHE_DELTA_FUTURES.update(saved)


def test_cache_delta_returns_when_the_done_callback_runs_inline(
    tmp_path, monkeypatch, clean_future_registry
):
    executor = _AlreadyDoneExecutor('{"drift": 0}')
    monkeypatch.setattr(ct, "_INDEX_IO_EXECUTOR", executor)

    finished, value, error = _run_with_watchdog(lambda: ct.cache_delta(str(tmp_path)))

    assert finished, (
        "cache_delta never returned: the done-callback was registered while"
        " _CACHE_DELTA_FUTURES_LOCK was still held, so the caller deadlocked on itself"
    )
    assert error is None, f"cache_delta raised instead of returning: {error!r}"
    assert value == '{"drift": 0}'
    assert len(executor.submissions) == 1
    assert clean_future_registry == {}, (
        "the done-callback did not run, so the coalescing entry leaked"
    )


def test_cache_delta_for_snapshot_returns_when_the_done_callback_runs_inline(
    tmp_path, monkeypatch, clean_future_registry
):
    executor = _AlreadyDoneExecutor('{"snapshot": true}')
    monkeypatch.setattr(ct, "_INDEX_IO_EXECUTOR", executor)

    finished, value, error = _run_with_watchdog(
        lambda: ct._cache_delta_for_snapshot(str(tmp_path), "snap-a", {"files": {}})
    )

    assert finished, (
        "_cache_delta_for_snapshot never returned: same inline-callback deadlock as"
        " cache_delta, on the snapshot-scoped singleflight key"
    )
    assert error is None, f"_cache_delta_for_snapshot raised instead of returning: {error!r}"
    assert value == '{"snapshot": true}'
    assert len(executor.submissions) == 1
    assert clean_future_registry == {}


def test_the_watchdog_catches_a_callback_registered_while_the_lock_is_held():
    """Positive control: the harness must fail on the bug it claims to detect."""
    lock = threading.Lock()

    def _take_the_lock(_done: Future) -> None:
        with lock:
            pass

    async def buggy() -> str:
        with lock:
            future: Future = Future()
            future.set_result("x")
            future.add_done_callback(_take_the_lock)
        return "reached the end"

    finished, value, error = _run_with_watchdog(buggy, seconds=CONTROL_SECONDS)

    assert not finished, (
        "the control did NOT deadlock, so a green result from the tests above proves"
        " nothing -- check that add_done_callback still runs inline on a done future"
    )
    assert value is None
    assert error is None


def test_cache_delta_still_coalesces_concurrent_callers(
    tmp_path, monkeypatch, clean_future_registry
):
    """Hoisting the callback must not cost the singleflight guarantee."""
    executor = _PendingExecutor()
    monkeypatch.setattr(ct, "_INDEX_IO_EXECUTOR", executor)
    project = str(tmp_path)

    async def two_callers():
        first = asyncio.ensure_future(ct.cache_delta(project))
        second = asyncio.ensure_future(ct.cache_delta(project))
        await asyncio.sleep(0.05)
        assert len(executor.submissions) == 1, (
            f"expected one submit for one project key, got {len(executor.submissions)}"
        )
        executor.futures[0].set_result('{"shared": true}')
        return await asyncio.gather(first, second)

    finished, value, error = _run_with_watchdog(two_callers)

    assert finished, "the coalescing path hung"
    assert error is None, f"the coalescing path raised: {error!r}"
    assert value == ['{"shared": true}', '{"shared": true}']
    assert clean_future_registry == {}


def test_neither_cache_delta_call_site_registers_the_callback_under_the_lock():
    source = Path(ct.__file__).read_text(encoding="utf-8")
    assert _callbacks_registered_under_the_lock(source) == []


def test_the_source_gate_flags_the_shape_it_is_meant_to_flag():
    """Positive control for the structural gate: it must see the original bug."""
    buggy_source = (
        "async def cache_delta(project_path):\n"
        "    with _CACHE_DELTA_FUTURES_LOCK:\n"
        "        future = _CACHE_DELTA_FUTURES.get(project_path)\n"
        "        if future is None:\n"
        "            future = _INDEX_IO_EXECUTOR.submit(_cache_delta_sync, project_path)\n"
        "            _CACHE_DELTA_FUTURES[project_path] = future\n"
        "            future.add_done_callback(lambda done: None)\n"
        "    return future\n"
    )
    assert _callbacks_registered_under_the_lock(buggy_source) == ["cache_delta"]
