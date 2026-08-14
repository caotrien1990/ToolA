"""E-01 regression: workflow_get_current and workflow_status must resolve
the current step through ONE canonical helper.

Why: prior code had two parallel resolution paths:
  - _wf_step_response: cur = state.get("current_step", 1)   (default 1)
  - workflow_status:   cur = (state or {}).get("current_step", 0)   (default 0)
The defaults disagreed, and the unstarted case was classified differently
(error vs started:False). This test pins the contract of the shared
resolver _wf_resolve_current_step: unstarted state -> (0, None);
started state -> (int(current_step), matching step object); and it is
exercised from BOTH the _wf_step_response builder (used by
workflow_get_current) and workflow_status itself.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest

from code_tools import (
    _wf_resolve_current_step,
    _wf_step_response,
    _wf_state_path,
    workflow_get_current,
    workflow_status,
)
from pathlib import Path


class TestCanonicalCurrentStepResolver(unittest.TestCase):
    """Direct contract of the shared resolver."""

    def test_unstarted_state_yields_zero_none(self) -> None:
        cur, step = _wf_resolve_current_step(None, [{"number": 1, "title": "s1"}])
        self.assertEqual(cur, 0)
        self.assertIsNone(step)

    def test_empty_state_yields_zero_none(self) -> None:
        cur, step = _wf_resolve_current_step({}, [{"number": 1, "title": "s1"}])
        self.assertEqual(cur, 0)
        self.assertIsNone(step)

    def test_started_state_returns_declared_step(self) -> None:
        steps = [
            {"number": 1, "title_blocks": [{"title": "first"}]},
            {"number": 2, "title_blocks": [{"title": "second"}]},
            {"number": 3, "title_blocks": [{"title": "third"}]},
        ]
        state = {"current_step": 2, "complete": False}
        cur, step = _wf_resolve_current_step(state, steps)
        self.assertEqual(cur, 2)
        self.assertIsNotNone(step)
        self.assertEqual(step["number"], 2)

    def test_missing_current_step_key_is_unstarted_not_step_one(self) -> None:
        """The prior `_wf_step_response` defaulted to 1. That silently told
        callers they were on step 1 of an unstarted workflow. The shared
        resolver treats absent current_step as unstarted (0)."""
        state = {"complete": False}  # no current_step key
        steps = [{"number": 1, "title_blocks": [{"title": "first"}]}]
        cur, step = _wf_resolve_current_step(state, steps)
        self.assertEqual(cur, 0)
        self.assertIsNone(step)

    def test_out_of_range_step_yields_none_step_obj(self) -> None:
        state = {"current_step": 99, "complete": False}
        steps = [{"number": 1}, {"number": 2}]
        cur, step = _wf_resolve_current_step(state, steps)
        self.assertEqual(cur, 99)
        self.assertIsNone(step)

    def test_non_integer_current_step_coerced_to_zero(self) -> None:
        state = {"current_step": "not-a-number"}
        cur, step = _wf_resolve_current_step(state, [{"number": 1}])
        self.assertEqual(cur, 0)
        self.assertIsNone(step)


class TestStepResponseUsesCanonicalResolver(unittest.TestCase):
    """_wf_step_response (used by workflow_get_current) must agree with the
    resolver on current_step, not silently fall back to 1."""

    def test_step_response_unstarted(self) -> None:
        parsed = {"workflow_title": "W", "steps": [{"number": 1}]}
        out = _wf_step_response(
            slug="w",
            parsed=parsed,
            state={},  # unstarted
            source="global",
            path=Path("w.md"),
        )
        self.assertEqual(out["current_step"], 0,
                         "unstarted workflow must not report step 1")
        self.assertIsNone(out["step"])

    def test_step_response_started(self) -> None:
        parsed = {
            "workflow_title": "W",
            "steps": [{"number": 1, "title_blocks": [{"title": "a"}]},
                      {"number": 2, "title_blocks": [{"title": "b"}]}],
        }
        state = {"current_step": 2, "complete": False}
        out = _wf_step_response(
            slug="w", parsed=parsed, state=state,
            source="global", path=Path("w.md"),
        )
        self.assertEqual(out["current_step"], 2)
        self.assertEqual(out["step"]["number"], 2)

    def test_step_response_matches_resolver(self) -> None:
        """For every state, _wf_step_response's current_step must equal the
        resolver's — the two endpoints can never drift again."""
        parsed = {"steps": [{"number": 1}, {"number": 2}, {"number": 3}]}
        for state in ({}, None, {"current_step": 1}, {"current_step": 3},
                      {"current_step": 99}, {"current_step": "x"}):
            expected_cur, _ = _wf_resolve_current_step(state, parsed["steps"])
            out = _wf_step_response(
                slug="w", parsed=parsed, state=state or {},
                source="global", path=Path("w.md"),
            )
            self.assertEqual(out["current_step"], expected_cur,
                             f"state={state!r}: resolver disagrees with step_response")


class TestPublicEndpointsAgreeOnUnstarted(unittest.TestCase):
    """E-01 v2 (2026-08-10): workflow_get_current and workflow_status must
    resolve identically for an UNSTARTED workflow.

    Why: prior to this fix, workflow_get_current early-exited with
    WORKFLOW_NOT_STARTED (ok:False) while workflow_status returned
    ok:True, started:False, current_step:0. Two public endpoints therefore
    disagreed on the same workflow state — CAs could not poll
    `workflow_get_current` before `workflow_start` without eating an error.
    Both endpoints now route through _wf_resolve_current_step and report
    current_step=0, step/current_step_body=None, started=False.
    """

    WORKFLOW_NAME = "mcp_175_tool_workflow_fixture"

    def setUp(self) -> None:
        # Isolate workflow run-state under a fresh project_path so the test
        # never collides with a real run.
        self._tmp = tempfile.TemporaryDirectory(prefix="e01_unstarted_")
        self.project_path = self._tmp.name
        state_file = _wf_state_path(self.project_path, self.WORKFLOW_NAME)
        if state_file.exists():
            state_file.unlink()

    def tearDown(self) -> None:
        try:
            state_file = _wf_state_path(self.project_path, self.WORKFLOW_NAME)
            if state_file.exists():
                state_file.unlink()
        finally:
            self._tmp.cleanup()

    def test_get_current_returns_unstarted_step_zero_not_error(self) -> None:
        """workflow_get_current on an unstarted workflow must NOT return
        WORKFLOW_NOT_STARTED; it must return ok:True with current_step=0
        and step=None, mirroring workflow_status."""
        body = json.loads(asyncio.run(
            workflow_get_current(self.WORKFLOW_NAME, self.project_path)))
        self.assertTrue(body.get("ok"),
                        f"unstarted get_current must not error: {body}")
        self.assertNotEqual(body.get("error_name"), "WORKFLOW_NOT_STARTED",
                            "WORKFLOW_NOT_STARTED early-exit must be gone")
        self.assertEqual(body.get("current_step"), 0)
        self.assertIsNone(body.get("step"))
        self.assertFalse(body.get("started"),
                         "unstarted workflow must report started=False")

    def test_status_and_get_current_agree_on_unstarted(self) -> None:
        """Both public endpoints must report the SAME current_step and the
        SAME started flag for an unstarted workflow. This is the core E-01
        invariant: no drift between the two readers."""
        status = json.loads(asyncio.run(
            workflow_status(self.WORKFLOW_NAME, self.project_path)))
        current = json.loads(asyncio.run(
            workflow_get_current(self.WORKFLOW_NAME, self.project_path)))
        self.assertTrue(status.get("ok"))
        self.assertTrue(current.get("ok"))
        self.assertEqual(status.get("current_step"),
                         current.get("current_step"),
                         "endpoints drifted on current_step for unstarted")
        self.assertEqual(status.get("current_step"), 0)
        self.assertFalse(status.get("started"))
        self.assertFalse(current.get("started"))
        # Both endpoints must surface the SAME "no current step" sentinel.
        self.assertIsNone(status.get("current_step_body"))
        self.assertIsNone(current.get("step"))
        # Both must give the CA the same next-action hint for unstarted.
        self.assertEqual(status.get("ca_next_action"),
                         current.get("ca_next_action"),
                         "ca_next_action diverged between endpoints")


if __name__ == "__main__":
    unittest.main()
