"""P16/P19 regression: invalid session_id / project_path must not silently return ok:true.

P16 (get_token_meter): a bogus session_id used to yield ok:true + zero counters.
      Now it must return SESSION_NOT_FOUND (mirrors get_session_events).
P19 (get_validation_queue): a bogus project_path used to yield ok:true + pending:[].
      Now it must return PROJECT_PATH_NOT_FOUND (F-01 isdir guard).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

import tools_v15 as tv


def _run(coro):
    return asyncio.run(coro)


# ── P19 ──────────────────────────────────────────────────────────────────
def test_p19_get_validation_queue_bad_path():
    bad = str(Path(tempfile.gettempdir()) / "nope-project-does-not-exist-p19")
    assert not Path(bad).exists()
    out = json.loads(_run(tv.get_validation_queue(bad)))
    assert out["ok"] is False
    assert out["error_name"] == "PROJECT_PATH_NOT_FOUND"
    assert bad in out["error"]


def test_p19_get_validation_queue_good_path_keeps_behavior():
    with tempfile.TemporaryDirectory() as d:
        out = json.loads(_run(tv.get_validation_queue(d)))
        assert out["ok"] is True
        assert isinstance(out["pending"], list)
        assert out["count"] == len(out["pending"])


# ── P16 ──────────────────────────────────────────────────────────────────
def test_p16_get_token_meter_bad_session():
    out = json.loads(_run(tv.get_token_meter(session_id="NOPE-SESSION-DOES-NOT-EXIST-P16")))
    assert out["ok"] is False
    assert out["error_name"] == "SESSION_NOT_FOUND"


def test_p16_get_token_meter_good_session_keeps_counters():
    from code_tools import _SESSIONS_DB
    try:
        conn = sqlite3.connect(str(_SESSIONS_DB))
        try:
            row = conn.execute(
                "SELECT session_id FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return  # no sessions db/table here; bad-session guard is the regression under test
    if not row:
        return
    out = json.loads(_run(tv.get_token_meter(session_id=row[0])))
    assert out["ok"] is True
    assert out["session_id"] == row[0]
    for key in ("calls", "bytes_in_estimate", "bytes_out_estimate", "total_kb_estimate"):
        assert key in out
