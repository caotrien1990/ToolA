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

2026-09-24, the same defect one layer out: the per-call envelope (generation, task
epoch, operation_id, ...) was stamped after the projection, so fields=ok answered
keys nobody asked for, and an unprojected call wrote them into the record -- the
record the cache then shared across agents.  The envelope now goes through the
projection, and the cache keeps the text the tool returned.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request

import code_tools
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

    def test_an_unprojected_call_returns_a_copy_and_records_the_answer(self):
        full = _tool_answer()
        out = srv._r101_dispatch_result(full)
        assert out == full
        assert out is not full, (
            "the wire copy is decorated after this point (pushed_schema, the size "
            "hint); sharing the object writes one caller's stamps into the record")
        assert srv._R101_DISPATCH_FULL.get() is full

    def test_a_non_dict_result_records_nothing(self):
        srv._R101_FIELDS.set({"ok"})
        assert srv._r101_dispatch_result("not a dict") == "not a dict"
        assert srv._R101_DISPATCH_FULL.get() is None, (
            "recording a non-dict would make the dispatch fallback unreachable")


def _envelope(**overrides) -> dict:
    """What the dispatcher says about one call: the measured replay shape."""
    envelope = {
        "generation": "gen-of-this-call",
        "task_epoch": 7,
        "operation_id": "op-1",
        "write_status_url": "/api/write_status?operation_id=op-1",
        "operation_attempts": 2,
        # #36 (2026-09-25): no operation_replayed -- it restated operation_attempts > 1.
        "likely_already_applied": True,
        "replay_hint": "the first attempt likely already wrote; verify by read",
    }
    envelope.update(overrides)
    return envelope


class TestTheEnvelopeIsAPerCallView:
    """R101 (2026-09-24): the envelope goes THROUGH the projection, never into the record.

    Stamped after the projection, fields=ok answered seven keys nobody asked for on
    every call, and an unprojected call wrote them into the recorded answer.
    """

    def test_unprojected_the_view_carries_it_and_the_record_does_not(self):
        full = {"ok": True, "callers_count": 1}
        out = srv._r101_dispatch_result(full, _envelope())
        assert out["operation_id"] == "op-1" and out["task_epoch"] == 7
        assert srv._R101_DISPATCH_FULL.get() == {"ok": True, "callers_count": 1}, (
            "the record is the tool's answer; the envelope describes one call only")

    def test_the_tools_own_key_wins_a_collision(self):
        out = srv._r101_dispatch_result(_tool_answer(), _envelope())
        assert out["generation"] == _tool_answer()["generation"]

    def test_a_projection_drops_what_only_describes_the_call(self):
        srv._R101_FIELDS.set({"ok", "callers_count"})
        out = srv._r101_dispatch_result({"ok": False, "callers_count": 0}, _envelope())
        for key in ("generation", "task_epoch", "operation_id", "write_status_url",
                    "operation_attempts"):
            assert key not in out, key

    def test_a_projection_keeps_what_warns(self):
        """A caller cannot ask for a warning it does not know is coming."""
        srv._R101_FIELDS.set({"ok"})
        out = srv._r101_dispatch_result({"ok": False}, _envelope(contention_note="queued",
                                                                operation_replayed=True))
        assert "operation_replayed" not in out, "#36: a send count is not a warning"
        assert out["likely_already_applied"] is True
        assert out["replay_hint"] and out["contention_note"] == "queued"

    def test_a_value_that_warns_of_nothing_is_dropped(self):
        srv._R101_FIELDS.set({"ok"})
        out = srv._r101_dispatch_result({"ok": True}, _envelope(
            likely_already_applied=False, replay_hint="", contention_note=""))
        assert set(out) == {"ok"}, out

    def test_a_named_envelope_key_is_kept_like_any_other(self):
        srv._R101_FIELDS.set({"ok", "operation_id"})
        out = srv._r101_dispatch_result({"ok": True}, _envelope())
        assert out["operation_id"] == "op-1"
        assert "fields_unknown" not in out, out

    def test_the_tools_own_value_is_not_overridden_by_a_warning(self):
        srv._R101_FIELDS.set({"ok"})
        out = srv._r101_dispatch_result({"ok": True, "likely_already_applied": False},
                                        _envelope())
        assert "likely_already_applied" not in out, (
            "the tool answered this key itself; the projection dropped it, and the "
            "envelope must not resurrect a value the tool contradicted")


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

    def test_the_tools_json_text_is_stored_as_its_answer(self):
        """The dispatcher hands store() the text the tool returned, not a dict."""
        assert call_cache.store(self.TOOL, self.BODY, json.dumps(_tool_answer()), self.MS)
        assert call_cache.lookup(self.TOOL, self.BODY)["result"] == _tool_answer()

    def test_text_that_is_not_a_good_answer_is_not_stored(self):
        for text in ("not json", json.dumps([1, 2]), json.dumps({"ok": False})):
            assert call_cache.store(self.TOOL, self.BODY, text, self.MS) == "", text
        assert call_cache.lookup(self.TOOL, self.BODY) is None


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

    def test_the_ledger_reads_the_full_answer(self):
        anchor = _SERVER_SRC.index("_result_full = _R101_DISPATCH_FULL.get()")
        ledger_at = _SERVER_SRC.index("_write_ledger_finish_async(", anchor)
        assert "_result_full" in _SERVER_SRC[ledger_at:ledger_at + 220], (
            "write_status after a client timeout would replay a trimmed answer")


class TestTheDispatcherCachesTheToolsOwnAnswer:
    """The round-trip through _dispatch_tool_call_request itself (2026-09-24).

    The cache key ignores agent_id on purpose, so one entry serves every CA, and a
    hit re-enters the dispatcher at json.loads(raw) to be stamped for ITS caller.
    Whatever the entry already carries wins that setdefault, so an entry holding the
    first caller's generation or task epoch hands it to the second -- which is what
    an unprojected call stored until this date: the answer with every stamp in it.
    The probe is marked deprecated so the dispatcher decorates it BEFORE the
    projection too; that decoration is the dispatcher's, not the tool's, and must
    not be stored either.
    """
    TOOL = "__a43_cache_probe__"
    ANSWER = {"ok": True, "callers": [{"file": "a.py", "line": 12}], "callers_count": 1}

    @pytest.fixture(autouse=True)
    def _probe(self, monkeypatch, tmp_path):
        self.calls = 0

        async def probe(**_kwargs) -> str:
            self.calls += 1
            return json.dumps(self.ANSWER)

        old_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = tmp_path / "sessions.db"
        code_tools._sync_session_db_alias()
        code_tools._init_session_db()
        monkeypatch.setitem(code_tools.TOOL_MAP, self.TOOL, probe)
        monkeypatch.setitem(srv._DEPRECATED_TOOLS, self.TOOL, "a43 probe")
        monkeypatch.setattr(call_cache, "CACHEABLE_TOOLS",
                            call_cache.CACHEABLE_TOOLS | {self.TOOL})
        monkeypatch.setattr(call_cache, "CALL_CACHE_MIN_MS", 0)
        monkeypatch.setattr(srv, "_POST_CALL_ACCOUNTING",
                            SimpleNamespace(enqueue=lambda _record: True))
        srv._NO_SESSION_REQUIRED.add(self.TOOL)
        try:
            yield
        finally:
            srv._NO_SESSION_REQUIRED.discard(self.TOOL)
            code_tools._SESSIONS_DB = old_db
            code_tools._sync_session_db_alias()

    async def _dispatch(self, query: str = "") -> dict:
        reader = StreamReader(protocol=mock.Mock(), limit=2 ** 16,
                              loop=asyncio.get_running_loop())
        reader.feed_data(b"{}")  # the probe declares no schema: any field is refused
        reader.feed_eof()
        request = make_mocked_request("POST", f"/api/call/{self.TOOL}{query}",
                                      payload=reader, match_info={"tool_name": self.TOOL})
        return json.loads((await srv._dispatch_tool_call_request(request)).text)

    def test_the_entry_is_the_answer_and_a_hit_is_stamped_for_its_own_caller(self, monkeypatch):
        async def scenario():
            monkeypatch.setattr(srv, "SERVER_GENERATION", "gen-first")
            first = await self._dispatch()
            assert first["generation"] == "gen-first", first
            assert first.get("deprecated") is True, (
                "the probe must be decorated before the projection, or this test "
                "cannot tell the tool's answer from the dispatcher's view: %r" % first)
            [entry] = call_cache._ENTRIES.values()
            assert entry["result"] == self.ANSWER, (
                "stored beyond the tool's answer, and it outlives the call it "
                "described: %r" % entry["result"])
            stored_at = entry["stored_at"]

            monkeypatch.setattr(srv, "SERVER_GENERATION", "gen-second")
            second = await self._dispatch()
            assert second.get("cache_hit") is True, second
            assert second["generation"] == "gen-second", (
                "the second caller was handed the first caller's stamp")
            assert second["callers_count"] == 1 and second.get("deprecated") is True
            [entry] = call_cache._ENTRIES.values()
            assert entry["stored_at"] == stored_at, (
                "a hit computed nothing; storing it again resets the entry's age")

            projected = await self._dispatch("?fields=ok,callers")
            assert projected.get("callers"), projected
            assert "generation" not in projected, projected
            assert "fields_unknown" not in projected, projected

        asyncio.run(scenario())
        assert self.calls == 1, "the two later calls must be served by the cache"

    def test_a_projected_hit_still_says_it_is_a_cached_answer(self):
        """P5: the cache stamps were written INTO the text, so they were ordinary keys.

        A hit answered cache_hit / cache_age_s / cache_note as if the tool had, and
        fields= -- which exists for exactly these fat cached reads -- dropped all
        three: a minutes-old answer read as a fresh measurement. They are the
        dispatcher's word about THIS call, so they ride the envelope, and the note
        that the answer is not fresh survives a projection that asked for none.
        """
        async def scenario():
            await self._dispatch()
            return await self._dispatch(), await self._dispatch("?fields=ok,callers")

        plain, projected = asyncio.run(scenario())
        assert self.calls == 1, "both later calls must be cache hits"
        assert plain.get("cache_hit") is True and "cache_age_s" in plain, plain
        assert "result cache" in plain.get("cache_note", ""), plain
        assert projected.get("callers"), projected
        assert "result cache" in projected.get("cache_note", ""), (
            "a projected hit reads exactly like a fresh answer: %r" % projected)
        for key in ("cache_hit", "cache_age_s", "fields_unknown", "response_incomplete"):
            assert key not in projected, (key, projected)
