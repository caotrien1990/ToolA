"""A43: a fields= projection must never become what [A] STORES.

dispatch_tool_call answers with a projected copy of the tool's result, and its
`finally` fills two durable things from that same local name: the write ledger
(so write_status can replay a client-timed-out write) and the A41 result cache
(so an identical payload is not recomputed).  Every return path reassigned the
name to the projected copy first, so both recorded the VIEW, not the answer.

Measured 2026-09-22 against the live daemon, three reproductions in a row:
  1. search_code(mode='callers', ...) with fields=ok,count,results -> fields_unknown.
     A genuine caller mistake: the keys are callers / callers_count.
  2. the SAME payload retried with the CORRECT fields=ok,callers,callers_count ->
     fields_unknown again, now naming cache_hit / cache_age_s / cache_note as the
     only available keys, because the cache held the projection of call #1.
  3. a fresh payload, same mode and same field names, resolved normally.
So a CA reading answer #2 is told its spelling is wrong when the spelling is
right and the data is there.  ok:true with zero rows is the worst shape [A] can
answer -- it reads as "nothing found" about a file that exists, which is exactly
how this surfaced in a worker report.

The fix is one producer, _r101_dispatch_result: it records the full answer and
returns the projection.  These tests pin the producer, the cache round-trip that
was actually broken, and -- through a negative control -- that the pre-fix shape
really does fail here, so a green run is evidence rather than decoration.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import orchestrator_server as srv
from orchestrator.core import call_cache

_SERVER_SRC = Path(srv.__file__).read_text(encoding="utf-8", errors="ignore")


def _tool_answer() -> dict:
    """The measured shape of a search_code(mode='callers') answer, trimmed."""
    return {
        "ok": True,
        "callers": [{"file": "a.py", "line": 12}],
        "callers_count": 1,
        "generation": "fd0e5475c933",
        "scanned_files": 1060,
        "elapsed_ms": 1199,
    }


@pytest.fixture(autouse=True)
def _clean_projection_context():
    """Both ContextVars and the cache are process-global; a leak reads as a pass."""
    srv._R101_FIELDS.set(None)
    srv._R101_DISPATCH_FULL.set(None)
    call_cache._ENTRIES.clear()
    yield
    srv._R101_FIELDS.set(None)
    srv._R101_DISPATCH_FULL.set(None)
    call_cache._ENTRIES.clear()


class TestTheProducerRecordsTheAnswer:
    def test_the_wire_copy_is_projected_and_the_answer_is_recorded(self):
        srv._R101_FIELDS.set({"ok"})
        full = _tool_answer()
        projected = srv._r101_dispatch_result(full)
        assert projected.get("ok") is True
        assert "callers" not in projected, "the wire copy must still be projected"
        recorded = srv._R101_DISPATCH_FULL.get()
        assert recorded is full, "the recorded object must BE the tool's own answer"
        assert recorded["callers_count"] == 1

    def test_an_unprojected_call_records_the_same_answer(self):
        full = _tool_answer()
        out = srv._r101_dispatch_result(full)
        assert out is full
        assert srv._R101_DISPATCH_FULL.get() is full

    def test_a_non_dict_result_records_nothing(self):
        srv._R101_FIELDS.set({"ok"})
        assert srv._r101_dispatch_result("not a dict") == "not a dict"
        assert srv._R101_DISPATCH_FULL.get() is None, (
            "recording a non-dict would make the dispatch fallback unreachable")


class TestTheCacheKeepsTheAnswerNotTheView:
    TOOL = "search_code"
    BODY = {"project_path": "D:/repo", "query": "clm_qsleep_session",
            "mode": "callers"}
    MS = 5000

    def test_the_measurement_is_possible_at_all(self):
        """Fail loud rather than pass vacuously if either gate changed."""
        assert call_cache.is_cacheable(self.TOOL), (
            "search_code is the tool the bug was measured on; if it stopped being "
            "cacheable the two tests below would pass while measuring nothing")
        assert self.MS >= call_cache.CALL_CACHE_MIN_MS

    def test_a_second_caller_asking_other_keys_gets_the_data(self):
        srv._R101_FIELDS.set({"ok", "count", "results"})   # caller #1, WRONG keys
        srv._r101_dispatch_result(_tool_answer())
        stored = call_cache.store(self.TOOL, self.BODY,
                                  srv._R101_DISPATCH_FULL.get(), self.MS)
        assert stored, "nothing was cached, so the round-trip is untested"
        hit = call_cache.lookup(self.TOOL, self.BODY)
        assert hit is not None
        srv._R101_FIELDS.set({"ok", "callers", "callers_count"})  # caller #2, right
        served = srv._r101_dispatch_result(dict(hit["result"]))
        assert served["callers_count"] == 1
        assert served["callers"]
        assert "fields_unknown" not in served, (
            "the reported false-OK: correct keys answered as a spelling mistake")

    def test_negative_control_the_pre_fix_shape_still_reproduces_the_bug(self):
        """Storing the projection must FAIL the check above, or it proves nothing."""
        srv._R101_FIELDS.set({"ok", "count", "results"})
        projected = srv._r101_dispatch_result(_tool_answer())
        assert call_cache.store(self.TOOL, self.BODY, projected, self.MS)
        hit = call_cache.lookup(self.TOOL, self.BODY)
        assert hit is not None
        srv._R101_FIELDS.set({"ok", "callers", "callers_count"})
        served = srv._r101_dispatch_result(dict(hit["result"]))
        assert "callers" not in served
        assert "fields_unknown" in served
        assert served.get("ok") is True, (
            "and it answers ok:true, which is what made this silent")


class TestNoDispatchPathCanStoreTheProjectionAgain:
    def test_no_return_path_reassigns_result_to_its_own_projection(self):
        assert "_result = _r101_filter(" not in _SERVER_SRC, (
            "that reassignment IS the defect: the finally below reads the same name")
        assert _SERVER_SRC.count("_r101_dispatch_result(") >= 5, (
            "one definition plus the four dispatch return paths")

    def test_exactly_one_site_records_the_full_answer(self):
        assert _SERVER_SRC.count("_R101_DISPATCH_FULL.set(") == 1, (
            "_error_response projects through _r101_filter too; a second setter "
            "would overwrite the tool answer with an error payload")

    def test_the_ledger_and_the_cache_both_read_the_full_answer(self):
        anchor = _SERVER_SRC.index("_result_full = _R101_DISPATCH_FULL.get()")
        ledger_at = _SERVER_SRC.index("_write_ledger_finish_async(", anchor)
        assert "_result_full" in _SERVER_SRC[ledger_at:ledger_at + 220], (
            "write_status after a client timeout would replay a trimmed answer")
        store_at = _SERVER_SRC.index("_cc.store(", anchor)
        assert "_result_full" in _SERVER_SRC[store_at:store_at + 220]
