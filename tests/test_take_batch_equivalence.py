"""S4 (checkpoint-contention Stage 2): _take_batch equivalence + cost.

The selection loop used to rebuild the per-principal heads map by scanning
the whole queue on EVERY pick and remove each chosen item with an O(N)
deque.remove -- O(queue x batch) under the queue mutex, paid against a
full 10,000-item queue during contention episodes and their drain. The fix
builds per-principal deques once and removes the batch in a single pass.
These tests pin that the selection OUTPUT stays bit-identical to the old
algorithm (a test-local reference copy, below) and that the cost at a full
queue drops by at least 5x.

Writers are built with batch_window_ms=0 and the clock is frozen with
mock.patch on time.monotonic (the exact function store.py reads): with a
default window, the frozen clock would never let the batch-window wait
see its deadline pass. Drains never call _take_batch on an empty queue,
so the frozen clock never reaches a real condition wait.
"""
from __future__ import annotations

import collections
import random
import time
from unittest import mock

from orchestrator.session import store


FROZEN_NOW = 10_000.0


def _reference_take_batch(items, batch_max, now):
    """Verbatim copy of the pre-fix selection loop, on a list copy."""
    queue = collections.deque(items)
    batch = []
    served = {}
    while queue and len(batch) < batch_max:
        heads = {}
        for item in queue:
            heads.setdefault(item.principal, item)
        eligible = list(heads.values())
        if len(heads) > 1:
            eligible = [item for item in eligible if served.get(item.principal, 0) < 32]
            if not eligible:
                break
        def rank(item):
            aged = now - item.enqueued_at >= 0.25
            priority = {"control": 0, "bulk": 1, "maintenance": 2}[item.work_class]
            return (0 if aged else 1, served.get(item.principal, 0),
                    item.enqueued_at if aged else priority, item.enqueued_at)
        chosen = min(eligible, key=rank)
        queue.remove(chosen)
        batch.append(chosen)
        served[chosen.principal] = served.get(chosen.principal, 0) + 1
    return batch, list(queue)


def _make_item(principal, work_class, age, tag):
    item = store._WriteItem(f"op {tag}", lambda conn: None, False, 750)
    item.principal = principal
    item.work_class = work_class
    item.enqueued_at = FROZEN_NOW - age
    return item


def _make_writer(tmp_path, items, batch_max):
    writer = store.SessionWriter(
        tmp_path / "take_batch.db", batch_max=batch_max,
        batch_window_ms=0, auto_start=False)
    for item in items:
        writer._queue.queue.append(item)
    return writer


def _assert_equivalent(tmp_path, items, batch_max):
    remaining = list(items)
    expected = []
    while remaining:
        batch, remaining = _reference_take_batch(remaining, batch_max, FROZEN_NOW)
        if not batch:
            break
        expected.append(batch)
    writer = _make_writer(tmp_path, list(items), batch_max)
    actual = []
    with mock.patch("time.monotonic", lambda: FROZEN_NOW):
        while writer._queue.queue:
            actual.append(writer._take_batch())
    assert actual == expected
    assert list(writer._queue.queue) == remaining


def test_equivalence_mixed_principals_classes_ages(tmp_path):
    items = []
    classes = ("control", "bulk", "maintenance")
    ages = (0.0, 0.1, 0.25, 0.3, 2.0)
    for i in range(60):
        items.append(_make_item(f"p{i % 3}", classes[i % 3], ages[i % 5], i))
    _assert_equivalent(tmp_path, items, 64)


def test_equivalence_batch_cap_splits_identically(tmp_path):
    items = [_make_item(f"p{i % 4}", ("control", "bulk")[i % 2], 0.1 + (i % 7) * 0.05, i)
             for i in range(100)]
    _assert_equivalent(tmp_path, items, 64)
    items = [_make_item(f"p{i % 4}", ("control", "bulk")[i % 2], 0.1 + (i % 7) * 0.05, i)
             for i in range(100)]
    _assert_equivalent(tmp_path, items, 7)


def test_equivalence_per_principal_32_cap(tmp_path):
    items = []
    classes = ("control", "bulk", "maintenance")
    for i in range(40):
        items.append(_make_item("alpha", classes[i % 3], 0.1, f"a{i}"))
        items.append(_make_item("beta", classes[(i + 1) % 3], 0.1, f"b{i}"))
    _assert_equivalent(tmp_path, items, 64)


def test_equivalence_single_principal_is_strict_fifo(tmp_path):
    items = [_make_item("solo", ("maintenance", "control", "bulk")[i % 3], (0.0, 3.0)[i % 2], i)
             for i in range(50)]
    writer = _make_writer(tmp_path, list(items), 64)
    with mock.patch("time.monotonic", lambda: FROZEN_NOW):
        batch = writer._take_batch()
    assert batch == items  # only the head is ever eligible -> strict FIFO


def test_equivalence_randomized_queues(tmp_path):
    rng = random.Random(20261109)
    classes = ("control", "bulk", "maintenance")
    ages = (0.0, 0.1, 0.24, 0.25, 0.26, 1.0)
    items = [_make_item(f"p{rng.randrange(4)}", rng.choice(classes), rng.choice(ages), i)
             for i in range(300)]
    _assert_equivalent(tmp_path, items, 64)





def test_take_batch_cost_at_full_queue(tmp_path):
    principals = [f"p{i}" for i in range(5)]
    classes = ("control", "bulk", "maintenance")

    def build_items():
        items = []
        for i in range(10_000):
            item = store._WriteItem(f"op {i}", lambda conn: None, False, 750)
            item.principal = principals[i % 5]
            item.work_class = classes[i % 3]
            item.enqueued_at = time.monotonic()
            items.append(item)
        return items

    def run_reference():
        remaining = build_items()
        start = time.perf_counter()
        for _ in range(5):
            _, remaining = _reference_take_batch(remaining, 64, time.monotonic())
        return time.perf_counter() - start

    def run_real():
        writer = _make_writer(tmp_path, build_items(), 64)
        start = time.perf_counter()
        for _ in range(5):
            writer._take_batch()
        return time.perf_counter() - start

    # Time ONLY the selection calls (5 consecutive batches from the full
    # queue): the identical queue rebuild is setup shared by both sides --
    # timing it too would swamp the selection delta (measured: the rebuild
    # alone costs about as much as the old selection). Rounds interleave
    # the two sides and keep each side's minimum, so a load spike on this
    # shared host cannot systematically favour one side.
    ref_t = real_t = None
    for _ in range(3):
        r = run_reference()
        ref_t = r if ref_t is None else min(ref_t, r)
        r = run_real()
        real_t = r if real_t is None else min(real_t, r)
    assert real_t <= ref_t / 5, (
        f"_take_batch cost did not drop enough: real {real_t:.4f}s "
        f"vs reference {ref_t:.4f}s (need >= 5x)")
