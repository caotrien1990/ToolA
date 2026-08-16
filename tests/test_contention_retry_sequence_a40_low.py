"""A40 leftover: SESSION_STORE_CONTENTION must not answer "should I retry?" twice
with opposite answers, and must answer the SAME way on both dispatch paths.

Two defects, both measured on the live dispatcher before the fix:

1. `_retry_sequence` had no SESSION_STORE_CONTENTION branch, so it fell through
   to `return []`. The very same payload carried `retryable=True` and
   `ca_next_action="retry_after_short_backoff"` -- the human-readable fields said
   "retry", the machine-readable one said "no steps". CA follows the machine one.

2. The two dispatch paths disagreed. A tool that RETURNS `_json_error(exc)` goes
   through the dispatcher's setdefault block and got a `retry_sequence` key (empty,
   per defect 1). A tool that RAISES goes through
   `_session_contention_error_payload`, a hand-built dict that omitted the key
   ENTIRELY. Same error_name, same retryable=True, different contract -- decided by
   whether the handler happened to catch its own exception.

The regression these guard against is silent: nothing crashes, CA just stops
retrying a write that would have committed, and the write is lost.
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import code_tools  # noqa: E402
import orchestrator_server as osrv  # noqa: E402
from orchestrator.session import store  # noqa: E402


def _dispatch(handler, payload=None):
    """Register an ad-hoc tool, dispatch it, return the parsed payload.

    NOTE: the payload defaults to {} on purpose. An ad-hoc tool registered only
    into TOOL_MAP has no registry schema, so S4 validation treats EVERY field as
    unsupported and the call fails with UNSUPPORTED_FIELD before the handler ever
    runs -- masking whatever this test meant to observe.
    """
    name = "__a40low_" + uuid.uuid4().hex[:8]
    code_tools.TOOL_MAP[name] = handler
    try:
        response = asyncio.run(
            osrv.dispatch_tool_call(name, payload or {}, transport="api"))
        return name, json.loads(response.text)
    finally:
        code_tools.TOOL_MAP.pop(name, None)


async def _raises(**kwargs):
    raise store.SessionStoreContentionError("write queue STALLED (test)")


async def _returns(**kwargs):
    try:
        raise store.SessionStoreContentionError("write queue STALLED (test)")
    except Exception as exc:  # noqa: BLE001 - exercising the _json_error path
        return code_tools._json_error(exc)


async def _genuine_defect(**kwargs):
    raise ValueError("a real bug, must stay non-retryable")


class ContentionRetrySequenceTests(unittest.TestCase):

    def test_mapper_returns_actionable_steps_not_empty(self):
        steps = osrv._retry_sequence(
            "close_session", "SESSION_STORE_CONTENTION", {"project_path": "D:/x"})
        self.assertTrue(steps, "SESSION_STORE_CONTENTION fell through to return []")
        self.assertTrue(
            any(s.get("tool") == "close_session" for s in steps),
            f"no step actually re-invokes the tool: {steps}")

    def test_retry_sequence_replays_the_identical_payload(self):
        body = {"project_path": "D:/x", "session_id": "s-1"}
        steps = osrv._retry_sequence("close_session", "SESSION_STORE_CONTENTION", body)
        replay = [s for s in steps if s.get("tool")]
        self.assertEqual(len(replay), 1, f"expected exactly one replay step: {steps}")
        # The write did not commit, so the retry must NOT mutate the payload.
        self.assertEqual(replay[0]["payload"], body)

    def test_raise_path_carries_a_retry_sequence(self):
        _, payload = _dispatch(_raises)
        self.assertEqual(payload.get("error_name"), "SESSION_STORE_CONTENTION")
        self.assertIs(payload.get("retryable"), True)
        self.assertIn("retry_sequence", payload,
                      "raise path omitted retry_sequence entirely")
        self.assertTrue(payload["retry_sequence"])

    def test_return_path_carries_a_retry_sequence(self):
        _, payload = _dispatch(_returns)
        self.assertEqual(payload.get("error_name"), "SESSION_STORE_CONTENTION")
        self.assertIs(payload.get("retryable"), True)
        self.assertTrue(payload.get("retry_sequence"))

    def test_both_paths_agree_on_the_contract(self):
        """The two paths must not diverge again -- that was defect 2."""
        _, raised = _dispatch(_raises)
        _, returned = _dispatch(_returns)
        for field in ("error_name", "retryable", "ca_next_action", "phase"):
            self.assertEqual(raised.get(field), returned.get(field),
                             f"paths disagree on {field!r}")
        self.assertEqual(len(raised["retry_sequence"]), len(returned["retry_sequence"]))

    def test_retryable_and_retry_sequence_never_contradict(self):
        """The defect in one sentence: retryable=True next to zero steps."""
        for handler in (_raises, _returns):
            with self.subTest(handler=handler.__name__):
                _, payload = _dispatch(handler)
                if payload.get("retryable") is True:
                    self.assertTrue(
                        payload.get("retry_sequence"),
                        "payload says retryable=True but hands CA no steps")

    def test_a_genuine_defect_is_not_widened_into_retryable(self):
        """The fix must not turn every exception into a retryable one."""
        _, payload = _dispatch(_genuine_defect)
        self.assertNotEqual(payload.get("error_name"), "SESSION_STORE_CONTENTION")
        self.assertNotEqual(payload.get("retryable"), True)
        self.assertFalse(payload.get("retry_sequence"))


if __name__ == "__main__":
    unittest.main()
