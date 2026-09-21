"""A41 tier 1c: single-flight join in the call cache.

MEASURED DEFECT these tests pin (2026-09-22): a CA abandoned `cbm_dead_code` at 3s, and a
second caller sent the IDENTICAL payload while that computation was STILL RUNNING and paid a
full 7227ms recompute. `lookup` cannot help -- nothing is stored until a call finishes -- so
the waste was invisible: neither call was wrong and no counter anywhere showed it.

Each test names a BEHAVIOUR, not a code shape, because this mechanism can look fully wired
and still never fire -- and a trigger that never fires looks exactly like a condition that
never held.
"""
import os
import threading
import time
from pathlib import Path

import pytest

from orchestrator.core import call_cache

CBM = "cbm_dead_code"  # cacheable AND budgeted: the tool the defect was measured on
BODY = {"project_path": "D:/x", "scope": "full"}


@pytest.fixture(autouse=True)
def _clean_singleflight():
    call_cache.reset_for_tests()
    yield
    call_cache.reset_for_tests()


def test_second_identical_payload_joins_instead_of_recomputing():
    """The defect itself: an identical payload arriving mid-flight must WAIT, not recompute."""
    leader, key = call_cache.begin(CBM, BODY)
    assert leader is True
    assert key
    joiner, joiner_key = call_cache.begin(CBM, dict(BODY))
    assert joiner is False, "a second identical payload led instead of joining"
    assert joiner_key == key, "a joiner waiting on a different key waits on nothing"


def test_a_different_question_never_waits():
    """Single-flight keys on the QUESTION, not the tool name: scope='core' is other work,
    and making it wait would turn an optimisation into an artificial serialisation."""
    leader, key = call_cache.begin(CBM, BODY)
    other_leader, other_key = call_cache.begin(
        CBM, {"project_path": "D:/x", "scope": "core"})
    assert leader is True
    assert other_leader is True, "a different payload was made to wait"
    assert other_key != key


def test_is_inflight_clears_only_on_finish():
    """`finish` is the only release. If a claim outlived it, every later caller would queue
    behind an answer that already exists."""
    _leader, key = call_cache.begin(CBM, BODY)
    assert call_cache.is_inflight(key) is True
    call_cache.finish(key)
    assert call_cache.is_inflight(key) is False
    again, again_key = call_cache.begin(CBM, dict(BODY))
    assert again is True, "after finish the next caller must LEAD, not wait on a dead claim"
    assert again_key == key


def test_release_before_store_wakes_a_joiner_to_a_cache_miss():
    """The near-miss this pins is ORDERING, not existence. Releasing joiners in the
    semaphore `finally` -- before `store` runs ~600 lines later -- leaves the mechanism
    wired and useless: the joiner wakes, `lookup` misses, and it recomputes exactly the
    work it waited for. This test walks the WRONG order deliberately so the miss is on
    the record, then the right one."""
    _leader, key = call_cache.begin(CBM, BODY)
    joiner, joiner_key = call_cache.begin(CBM, dict(BODY))
    assert joiner is False
    call_cache.finish(joiner_key)  # released too early, on purpose
    assert call_cache.lookup(CBM, dict(BODY)) is None, (
        "if this ever hits, the ordering hazard is gone and this test is obsolete")
    stored = call_cache.store(CBM, BODY, {"ok": True, "n": 1}, duration_ms=7227)
    assert stored == key
    hit = call_cache.lookup(CBM, dict(BODY))
    assert hit is not None
    assert hit["result"]["n"] == 1


def test_dispatcher_calls_finish_after_store_not_before():
    """Source anchor, deliberately: the behaviour test above cannot see WHERE the
    dispatcher releases joiners, and that placement IS the mechanism. Anchored by the two
    call-site identities, never by line number, so an unrelated edit cannot fail it."""
    src = (Path(__file__).resolve().parents[1] / "orchestrator_server.py").read_text(
        encoding="utf-8")
    store_at = src.index("_cc.store(")
    finish_at = src.index("_cc_sf.finish(")
    assert finish_at > store_at, (
        "finish() moved ABOVE store(): every joiner would wake to a cache miss")


def test_a_dead_leader_does_not_wedge_the_key(monkeypatch):
    """A leader killed mid-call never reaches `finish`. Without reclaim, that one claim
    would make every later caller of the same question wait the full ceiling, forever,
    until the process restarts -- a cache turning into an outage."""
    leader, key = call_cache.begin(CBM, BODY)
    assert leader is True
    monkeypatch.setattr(call_cache, "CALL_SINGLEFLIGHT_WAIT_S", 0.05)
    time.sleep(0.06)
    reclaimed, reclaimed_key = call_cache.begin(CBM, dict(BODY))
    assert reclaimed is True, "a stale claim wedged the key"
    assert reclaimed_key == key


def test_wait_ceiling_is_derived_from_the_call_budget():
    """Tier 1a guarantees a budgeted leader answers within CALL_BUDGET_S, so a joiner that
    waited longer could only be waiting on a leader already gone. Two independently-set
    numbers would drift apart silently; this one is derived."""
    expected = float(
        os.environ.get("ORCHESTRATOR_SINGLEFLIGHT_WAIT_S") or call_cache.CALL_BUDGET_S)
    assert call_cache.CALL_SINGLEFLIGHT_WAIT_S == expected


def test_join_whitelist_is_inherited_from_the_cache_not_widened():
    """A joiner receives the LEADER's answer, so only a tool already safe to serve from
    cache may join. `read_file_range` is the proof it must stay narrow: it seeds the
    write_fix read_token, so a joiner handed someone else's read would hold no edit
    authority at all."""
    for tool in sorted(call_cache.CACHEABLE_TOOLS):
        assert call_cache.begin(tool, {"p": tool})[1], tool
        call_cache.reset_for_tests()
    for tool in ("read_file_range", "write_fix", "git_commit"):
        assert call_cache.begin(tool, {"p": tool}) == (True, ""), tool
        assert call_cache.cache_status()["inflight"] == 0, tool


def test_joins_waited_counts_decisions_to_wait_not_joins_served():
    """The counter ships with its own semantics string because a number whose meaning has
    to be guessed already burned this campaign once (cbm_test_gap's pass/score). A leader
    that fails, or finishes under CALL_CACHE_MIN_MS, leaves its joiner to compute anyway --
    so this counts the DECISION to wait, and says so in-band."""
    call_cache.begin(CBM, BODY)
    call_cache.begin(CBM, dict(BODY))
    call_cache.begin(CBM, dict(BODY))
    status = call_cache.cache_status()
    assert status["joins_waited"] == 2
    assert status["inflight"] == 1
    assert "not joins that were then served" in status["joins_waited_semantics"]
    assert status["singleflight_wait_s"] == call_cache.CALL_SINGLEFLIGHT_WAIT_S


def test_exactly_one_leader_when_many_callers_arrive_at_once():
    """Multi-CA is the whole point: 12 callers firing the identical payload in the same
    instant must produce ONE computation and 11 waiters. A non-atomic claim shows up here
    as 2+ leaders, which is the original defect back again under a new name."""
    results = []
    gate = threading.Barrier(12)

    def claim():
        gate.wait(timeout=10)
        results.append(call_cache.begin(CBM, dict(BODY)))

    threads = [threading.Thread(target=claim) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(results) == 12
    leaders = [r for r in results if r[0] is True]
    assert len(leaders) == 1, "%d leaders: the claim is not atomic" % len(leaders)
    assert call_cache.cache_status()["joins_waited"] == 11


def test_reset_for_tests_clears_the_claim_dict_and_the_counter():
    """Leaked single-flight state does not read as leaked state: the NEXT test becomes a
    joiner waiting the full ceiling on a leader that no longer exists, which reads as a
    hang in an unrelated test rather than as a dirty fixture."""
    call_cache.begin(CBM, BODY)
    call_cache.begin(CBM, dict(BODY))
    assert call_cache.cache_status()["inflight"] == 1
    call_cache.reset_for_tests()
    status = call_cache.cache_status()
    assert status["inflight"] == 0
    assert status["joins_waited"] == 0