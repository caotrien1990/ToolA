# pyright: reportPrivateUsage=false
"""A52 verification layer: the drift decision must be WIRED IN, BOUNDED and LIVE.

`test_open_session_drift_note.py` (repo root, 10 tests) proves the policy
function computes the right answer. It calls `_open_session_drift_decision`
directly with hand-built dicts, so three surfaces stay unguarded -- each was
confirmed by a surviving mutant against that suite:

  Z1  drop the `[:max_entries]` slice   -> note becomes UNBOUNDED. Its own cap
      test only feeds 2 drifts ("note path only reachable with count<3 today"),
      so <=5 holds trivially. A52 exists to shrink the open_session payload; an
      unbounded note re-creates the bloat the fix was written to remove.
  Z2  freeze the env knobs at import    -> no test touches ORCHESTRATOR_DRIFT_*,
      so all 10 stay green. This is the exact defect A13 was just fixed for in
      orchestrator/graph/config.py: a value frozen at import that env can no
      longer move. The helper's own docstring promises a LIVE read.
  Z3  revert ONE of the two call sites  -> no test calls open_session, so a
      half-applied fix is invisible and the new-session path keeps forcing
      'full' on a single low drift.

These tests therefore pin BEHAVIOUR AT THE BOUNDARY, not the shape of the
helper: what open_session actually returns, what the env actually controls, and
that the cap actually binds when it is asked to.

Placed in tests/ deliberately: A52 was invisible for ten days precisely because
its test file sat at the repo root, outside the `pytest tests/` scope everyone
runs (lesson L-s).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools
from orchestrator.session import store


def _drift(tool: str, severity: str) -> dict:
    return {"type": "inventory_outdated", "tool": tool, "severity": severity,
            "detail": "fixture"}


def _mx(drift: list[dict]) -> dict:
    """Shape returned by _matrix_audit_open_session_summary."""
    return {"drift": drift, "summary": {"drift_count": len(drift)}}


class TestDriftNoteIsBounded(unittest.TestCase):
    """Z1: the <=max_entries cap must actually bind."""

    def test_note_is_capped_when_more_drifts_than_the_cap_are_present(self):
        # 12 low drifts. The count-threshold would normally force 'full', so
        # raise it out of the way to reach the note path with a large input --
        # this is the only way to make the cap observable at all.
        drift = [_drift(f"tool_{i}", "low") for i in range(12)]
        resp: dict = {"progressive_gap_count": 0}
        with mock.patch.dict(
            code_tools.os.environ,
            {"ORCHESTRATOR_DRIFT_FORCE_FULL_COUNT": "99"},
            clear=False,
        ):
            eff = code_tools._open_session_drift_decision(resp, _mx(drift), "terse")

        self.assertEqual(eff, "terse", msg=resp)
        note = resp.get("matrix_drift_note")
        self.assertIsNotNone(note, msg="note path not reached; fixture is wrong")
        self.assertLessEqual(
            len(note), 5,
            msg=f"matrix_drift_note carried {len(note)} entries for 12 drifts. The "
                "documented contract is <=5 and the entire point of A52 is to stop "
                "open_session payload bloat -- an unbounded note re-creates it.",
        )

    def test_cap_is_configurable_and_still_binds(self):
        drift = [_drift(f"tool_{i}", "low") for i in range(12)]
        resp: dict = {"progressive_gap_count": 0}
        with mock.patch.dict(
            code_tools.os.environ,
            {"ORCHESTRATOR_DRIFT_FORCE_FULL_COUNT": "99",
             "ORCHESTRATOR_DRIFT_NOTE_MAX_ENTRIES": "2"},
            clear=False,
        ):
            code_tools._open_session_drift_decision(resp, _mx(drift), "terse")
        self.assertEqual(len(resp["matrix_drift_note"]), 2, msg=resp)


class TestThresholdsAreReadLive(unittest.TestCase):
    """Z2: A13 pattern -- env must move the behaviour AFTER import."""

    def test_severity_set_is_read_live_not_frozen_at_import(self):
        one_medium = [_drift("t", "medium")]

        # default: medium is NOT a force-full severity
        resp_a: dict = {"progressive_gap_count": 0}
        self.assertEqual(
            code_tools._open_session_drift_decision(resp_a, _mx(one_medium), "terse"),
            "terse", msg=resp_a,
        )

        # env promotes medium -> must force full on the very next call
        resp_b: dict = {"progressive_gap_count": 0}
        with mock.patch.dict(
            code_tools.os.environ,
            {"ORCHESTRATOR_DRIFT_FORCE_SEVERITIES": "medium,high,critical"},
            clear=False,
        ):
            eff = code_tools._open_session_drift_decision(resp_b, _mx(one_medium), "terse")
        self.assertEqual(
            eff, "full",
            msg="ORCHESTRATOR_DRIFT_FORCE_SEVERITIES did not take effect after import "
                "-- the severity set was frozen at import time, the exact defect A13 "
                f"fixed in orchestrator/graph/config.py. resp={resp_b}",
        )

    def test_count_threshold_is_read_live(self):
        two_low = [_drift(f"t{i}", "low") for i in range(2)]
        resp: dict = {"progressive_gap_count": 0}
        with mock.patch.dict(
            code_tools.os.environ,
            {"ORCHESTRATOR_DRIFT_FORCE_FULL_COUNT": "2"},
            clear=False,
        ):
            eff = code_tools._open_session_drift_decision(resp, _mx(two_low), "terse")
        self.assertEqual(
            eff, "full",
            msg=f"count threshold not read live from env. resp={resp}",
        )


class TestBothOpenSessionPathsUseTheDecision(unittest.TestCase):
    """Z3: BOTH call sites (resume + new session) must share one policy."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="a52_wiring_")).resolve()
        # These tests drive the REAL open_session, which persists a row in
        # sessions.db. Without this redirect the module-level _SESSIONS_DB still
        # points at .orchestrator_cache/sessions.db and every run leaves a
        # permanent `a52-*` session in the PRODUCTION database (measured: +3
        # sessions per run). Bind both the store and the code_tools alias, then
        # close the process-global writer so it re-binds to this temp path.
        self._db = self.root / "sessions_a52.db"
        self._prev_store_db = store._SESSIONS_DB
        self._prev_ct_db = code_tools._SESSIONS_DB
        store._SESSIONS_DB = self._db
        code_tools._SESSIONS_DB = self._db
        store.close_writer()
        store._init_session_db()
        (self.root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
        asyncio.run(code_tools.generate_project_map(str(self.root)))

    def tearDown(self) -> None:
        # Release the temp DB handle BEFORE rmtree: on Windows an open sqlite
        # handle makes the directory undeletable (WinError 32).
        store.close_writer()
        store._SESSIONS_DB = self._prev_store_db
        code_tools._SESSIONS_DB = self._prev_ct_db
        store._SESSION_DB_INITIALIZED_PATHS.discard(
            os.path.normcase(os.path.abspath(str(self._db)))
        )
        shutil.rmtree(self.root, ignore_errors=True)

    def _open(self, title: str) -> dict:
        """open_session with a single LOW drift injected: under D4 this must NOT
        force 'full'. Under the legacy snippet it always does."""
        with mock.patch.object(
            code_tools, "_matrix_audit_open_session_summary",
            return_value=_mx([_drift("record_error_resolution", "low")]),
        ):
            return json.loads(asyncio.run(code_tools.open_session(
                session_title=title, project_path=str(self.root), verbosity="terse",
            )))

    def test_new_session_path_does_not_force_full_on_a_single_low_drift(self):
        resp = self._open("a52-new")
        self.assertTrue(resp.get("ok"), msg=resp)
        self.assertNotIn(
            "_drift_override", resp,
            msg="the NEW-session branch of open_session still force-escalates to "
                "'full' on one low drift -- it is not routed through "
                "_open_session_drift_decision, so the fix is only half applied. "
                f"resp keys={sorted(resp)}",
        )

    def test_resume_session_path_does_not_force_full_on_a_single_low_drift(self):
        self._open("a52-resume")          # creates
        resp = self._open("a52-resume")   # resumes the same session
        self.assertTrue(resp.get("ok"), msg=resp)
        self.assertNotIn(
            "_drift_override", resp,
            msg="the RESUME branch of open_session still force-escalates to 'full' "
                "on one low drift -- not routed through the shared decision. "
                f"resp keys={sorted(resp)}",
        )

    def test_high_severity_still_escalates_through_open_session(self):
        """The counterpart: D4 must NOT have disabled escalation entirely. A
        mutant that always returns the requested verbosity would pass both tests
        above; this one kills it."""
        with mock.patch.object(
            code_tools, "_matrix_audit_open_session_summary",
            return_value=_mx([_drift("broken_schema_tool", "high")]),
        ):
            resp = json.loads(asyncio.run(code_tools.open_session(
                session_title="a52-high", project_path=str(self.root),
                verbosity="terse",
            )))
        self.assertTrue(resp.get("ok"), msg=resp)
        self.assertIn(
            "_drift_override", resp,
            msg="a HIGH-severity drift no longer escalates open_session to 'full' -- "
                f"D4 must narrow the escalation, not remove it. resp keys={sorted(resp)}",
        )


if __name__ == "__main__":
    unittest.main()
