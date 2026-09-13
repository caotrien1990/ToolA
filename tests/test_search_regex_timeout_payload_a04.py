"""A04: REGEX_SEARCH_TIMEOUT must hand the caller something to retry WITH.

Before the fix the timeout payload carried ONLY {error, error_name, retryable,
bounded, ca_next_action}. It told the caller "retryable: true,
narrow_or_simplify_regex" but:

1. dropped the original `query` from the response entirely,
2. had no `corrected_payload` / `payload_example` (its REGEX_INVALID sibling has
   `corrected_payload={"query": query, "mode": "text"}` -- the house pattern),
3. `_retry_sequence("search_code", "REGEX_SEARCH_TIMEOUT", ...)` had no branch,
   so it fell through to `[]` -- the exact A40 SESSION_STORE_CONTENTION hole:
   retryable=True next to zero machine-readable steps.

There are TWO code paths that emit this error_name -- the asyncio.TimeoutError
branch and the RegexSearchTimeout sub-branch of the generic handler. Per the A40
"two paths must not diverge" lesson, both must emit the identical actionable
shape; these tests pin both.

NOT in scope (campaign lesson L-x): the deadline NUMBER is not widened here.
regex_deadline_seconds is a real, schema-declared param clamped to [0.05, 10.0]s,
so the payload may *suggest* a larger one, but the tool's own default is left as-is.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import search_regex_worker
from orchestrator.tools import search as search_module
import orchestrator_server as osrv


def _run_timeout(query: str, side_effect, *, root: Path, deadline: float,
                 file_pattern: str = "*") -> dict:
    (root / "sample.txt").write_text("needle\n", encoding="utf-8")
    search_module.search_snapshots.publish_snapshot(
        root, include_file=lambda path: True,
        skip_dir=lambda name: name.startswith("."), index=None,
    )
    with mock.patch.object(search_module, "_run_search_singleflight", side_effect=side_effect):
        raw = asyncio.run(search_module.search_code(
            query=query, path=str(root), mode="regex",
            file_pattern=file_pattern, regex_deadline_seconds=deadline,
        ))
    return json.loads(raw)


async def _blow_up(key, producer, **kwargs):
    raise asyncio.TimeoutError


async def _raise_worker_timeout(key, producer, **kwargs):
    raise search_regex_worker.RegexSearchTimeout("worker timed out (test)")


def _timeout_payload_via_asyncio(query: str, *, deadline: float = 0.05,
                                 file_pattern: str = "*") -> dict:
    """Drive the asyncio.TimeoutError branch by forcing the singleflight to time out."""
    with tempfile.TemporaryDirectory() as tmp:
        return _run_timeout(query, _blow_up, root=Path(tmp),
                            deadline=deadline, file_pattern=file_pattern)


def _timeout_payload_via_worker_exc(query: str, *, deadline: float = 0.20) -> dict:
    """Drive the RegexSearchTimeout sub-branch by raising it from the producer."""
    with tempfile.TemporaryDirectory() as tmp:
        return _run_timeout(query, _raise_worker_timeout, root=Path(tmp), deadline=deadline)


class RegexTimeoutPayloadTests(unittest.TestCase):

    def test_timeout_carries_the_original_query_back(self):
        payload = _timeout_payload_via_asyncio("value_[0-9]+")
        self.assertEqual(payload["error_name"], "REGEX_SEARCH_TIMEOUT")
        # The whole defect: the query used to vanish from the response.
        self.assertEqual(payload.get("query"), "value_[0-9]+")

    def test_timeout_carries_a_corrected_payload_with_text_fallback(self):
        payload = _timeout_payload_via_asyncio("value_[0-9]+")
        corrected = payload.get("corrected_payload")
        self.assertIsInstance(corrected, dict)
        self.assertEqual(corrected.get("query"), "value_[0-9]+")
        # Falling back to a literal search is the concrete "narrow_or_simplify".
        self.assertEqual(corrected.get("mode"), "text")

    def test_timeout_carries_a_payload_example_that_stays_regex(self):
        payload = _timeout_payload_via_asyncio("value_[0-9]+")
        example = payload.get("payload_example")
        self.assertIsInstance(example, dict)
        self.assertEqual(example.get("mode"), "regex")
        # The suggested larger deadline must be a REAL, in-range value.
        dl = example.get("regex_deadline_seconds")
        self.assertIsNotNone(dl)
        self.assertLessEqual(dl, search_regex_worker.MAX_DEADLINE_SECONDS)
        self.assertGreaterEqual(dl, search_regex_worker.MIN_DEADLINE_SECONDS)

    def test_timeout_retry_sequence_is_non_empty_and_replays_the_tool(self):
        payload = _timeout_payload_via_asyncio("value_[0-9]+")
        seq = payload.get("retry_sequence")
        self.assertTrue(seq, "REGEX_SEARCH_TIMEOUT handed back an empty retry_sequence")
        self.assertTrue(
            any(step.get("tool") == "search_code" for step in seq),
            f"no retry step re-invokes search_code: {seq}")

    def test_retryable_and_retry_sequence_never_contradict(self):
        payload = _timeout_payload_via_asyncio("value_[0-9]+")
        if payload.get("retryable") is True:
            self.assertTrue(
                payload.get("retry_sequence"),
                "payload says retryable=True but hands CA no steps")

    def test_first_retry_step_does_not_blindly_replay_the_same_regex(self):
        # A blind re-send of the identical regex would just time out again;
        # the first step must change the shape (mode='text').
        payload = _timeout_payload_via_asyncio("value_[0-9]+")
        first = next(s for s in payload["retry_sequence"] if s.get("tool"))
        self.assertEqual(first["payload"].get("mode"), "text")

    def test_huge_query_is_truncated_with_an_explicit_marker(self):
        # Over the 200-char echo cap but under MAX_PATTERN_CHARS (4096), so it
        # reaches the timeout branch rather than failing the pattern budget first.
        big = "a" * 2000
        payload = _timeout_payload_via_asyncio(big)
        echoed = payload["query"]
        self.assertLess(len(echoed), len(big), "query was echoed unbounded")
        self.assertIn("truncated", echoed)

    def test_both_emit_paths_agree_on_the_contract(self):
        """asyncio.TimeoutError path and RegexSearchTimeout path must not diverge."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _run_timeout("value_[0-9]+", _blow_up, root=root, deadline=0.20)
            b = _run_timeout("value_[0-9]+", _raise_worker_timeout, root=root, deadline=0.20)
        for field in ("error_name", "retryable", "ca_next_action", "query"):
            self.assertEqual(a.get(field), b.get(field),
                             f"the two timeout paths disagree on {field!r}")
        self.assertEqual(a.get("corrected_payload"), b.get("corrected_payload"))
        self.assertEqual(len(a["retry_sequence"]), len(b["retry_sequence"]))

    def test_mapper_has_a_non_empty_branch(self):
        # The dispatcher fallback: _retry_sequence must not return [] for a
        # payload that also says retryable=True (the A40 hole).
        steps = osrv._retry_sequence(
            "search_code", "REGEX_SEARCH_TIMEOUT",
            {"query": "value_[0-9]+", "path": "D:/x", "mode": "regex"})
        self.assertTrue(steps, "REGEX_SEARCH_TIMEOUT fell through to return []")
        self.assertTrue(
            any(s.get("tool") == "search_code" for s in steps),
            f"no mapper step re-invokes search_code: {steps}")

    def test_mapper_step_is_not_a_blind_replay(self):
        """Non-empty is not the bar -- the A40 lesson is about USABLE steps.

        Found by an independent mutant during verification: replacing the
        mapper's `{**body, "mode": "text"}` with `dict(body)` kept the sequence
        non-empty and kept a `search_code` step, so
        test_mapper_has_a_non_empty_branch stayed GREEN -- while the only
        suggested retry was the identical regex that had just timed out. A
        caller obeying it would time out again, forever. Pin the substance:
        every re-invocation the mapper proposes must differ from the payload
        that failed.
        """
        body = {"query": "value_[0-9]+", "path": "D:/x", "mode": "regex"}
        steps = osrv._retry_sequence("search_code", "REGEX_SEARCH_TIMEOUT", dict(body))
        invocations = [s for s in steps if s.get("tool") == "search_code"]
        self.assertTrue(invocations, f"no mapper step re-invokes search_code: {steps}")
        for step in invocations:
            self.assertNotEqual(
                step.get("payload"), body,
                "mapper proposes replaying the exact regex payload that just "
                f"timed out: {step}")
        self.assertEqual(
            invocations[0].get("payload", {}).get("mode"), "text",
            f"first mapper retry must degrade to literal text mode: {invocations[0]}")

    def test_deadline_number_is_not_widened(self):
        # L-x guard: the tool's own default deadline must stay put. This test
        # goes RED if someone "fixes" the timeout by bumping DEFAULT_DEADLINE.
        self.assertEqual(search_regex_worker.DEFAULT_DEADLINE_SECONDS, 8.0)


if __name__ == "__main__":
    unittest.main()
