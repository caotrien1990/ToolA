"""S2-MEM-01 / S2-MEM-02 (Commit C) regression tests: memory_capture and
memory_ingest_path must fail FAST on invalid payloads — at local schema
validation, BEFORE any ToolQ dispatch — instead of hanging >5s waiting on
the network path.

Why: pre-Commit-C, the handlers' minimal checks were thin (truthy / isinstance
only) and used generic error names (MISSING_ARG / BAD_ARG). A malformed
probe (missing required field, wrong type, bad enum, control char in path)
could not be distinguished from a network hang by callers, and there was no
way to assert "no ToolQ dispatch happened". This test pins the invariant:

  1) invalid payload -> structured MISSING_REQUIRED_FIELD / SCHEMA_VIOLATION
     rejection
  2) the rejection returns in <250ms (local code path only — no await on
     _toolq_post)
  3) the ToolQ dispatch counter does NOT increment (zero network dispatch)

Why: the tests prove the fix mechanism (local fail-fast gate before network),
not just its current implementation. A future regression that moves even one
of these validations behind the `_toolq_post` await will fail these tests
loudly.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Local fail-fast ceiling: pure sync validation must complete well under
# 250ms. We use 250ms as a generous bound; on modern hardware the real path
# is <5ms. Catching a regression means someone re-introduced an await or
# blocking call before the schema gate.
FAIL_FAST_BUDGET_SEC = 0.250


class MemoryCaptureFailFastTest(unittest.TestCase):
    """S2-MEM-01: memory_capture must reject invalid payloads locally."""

    @classmethod
    def setUpClass(cls):
        cls.srv = importlib.import_module("orchestrator_server")

    def setUp(self):
        # Reset dispatch counter before each test so we can assert delta == 0.
        for k in list(self.srv._TOOLQ_DISPATCH_COUNT.keys()):
            self.srv._TOOLQ_DISPATCH_COUNT[k] = 0

    def _dispatch_delta(self, key: str) -> int:
        return int(self.srv._TOOLQ_DISPATCH_COUNT.get(key, 0))

    def test_missing_session_id_rejects_fast_no_dispatch(self):
        """Missing session_id -> MISSING_REQUIRED_FIELD, <250ms, no ToolQ."""
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(
                self.srv.memory_capture(turns=[{"user_prompt": "u", "assistant_response": "a"}])
            )
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_name"], "MISSING_REQUIRED_FIELD", payload)
        self.assertIn("session_id", payload["missing_fields"])
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC, f"fail-fast took {elapsed:.3f}s")
        self.assertEqual(self._dispatch_delta("capture"), 0, "no ToolQ dispatch allowed")

    def test_missing_turns_rejects_fast_no_dispatch(self):
        """Missing turns -> MISSING_REQUIRED_FIELD, <250ms, no ToolQ."""
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(self.srv.memory_capture(session_id="s1"))
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_name"], "MISSING_REQUIRED_FIELD", payload)
        self.assertIn("turns", payload["missing_fields"])
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("capture"), 0)

    def test_turns_not_a_list_rejects_fast_no_dispatch(self):
        """turns as a string -> SCHEMA_VIOLATION, <250ms, no ToolQ."""
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(self.srv.memory_capture(session_id="s1", turns="not-a-list"))
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_name"], "SCHEMA_VIOLATION", payload)
        self.assertEqual(payload.get("field"), "turns")
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("capture"), 0)

    def test_turns_item_not_an_object_rejects_fast_no_dispatch(self):
        """turns[i] not an object -> SCHEMA_VIOLATION, <250ms, no ToolQ."""
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(
                self.srv.memory_capture(session_id="s1", turns=["just-a-string"])
            )
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "SCHEMA_VIOLATION", payload)
        self.assertIn("turns[0]", payload.get("field", ""))
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("capture"), 0)

    def test_session_id_wrong_type_rejects_fast_no_dispatch(self):
        """session_id as int -> SCHEMA_VIOLATION (must be a string)."""
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(
                self.srv.memory_capture(
                    session_id=123, turns=[{"user_prompt": "u", "assistant_response": "a"}]
                )
            )
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "SCHEMA_VIOLATION", payload)
        self.assertEqual(payload.get("field"), "session_id")
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("capture"), 0)

    def test_valid_payload_still_dispatches(self):
        """Happy path: valid payload reaches _toolq_post (counter +1) and is bounded."""
        fake = {"ok": True, "captured": 1}
        with mock.patch.object(
            self.srv, "_toolq_post", return_value=fake
        ) as post_mock:
            # Make the patched version still bump the counter the way the real
            # _toolq_post does, so the "valid -> dispatch" invariant is observable.
            async def _bump(*args, **kwargs):
                self.srv._TOOLQ_DISPATCH_COUNT["capture"] += 1
                return fake
            post_mock.side_effect = _bump
            result = asyncio.run(
                self.srv.memory_capture(
                    session_id="s1",
                    turns=[{"user_prompt": "u", "assistant_response": "a"}],
                )
            )
        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "toolq")
        self.assertEqual(self._dispatch_delta("capture"), 1, "valid payload must dispatch")


class MemoryIngestPathFailFastTest(unittest.TestCase):
    """S2-MEM-02: memory_ingest_path must reject invalid payloads locally."""

    @classmethod
    def setUpClass(cls):
        cls.srv = importlib.import_module("orchestrator_server")

    def setUp(self):
        for k in list(self.srv._TOOLQ_DISPATCH_COUNT.keys()):
            self.srv._TOOLQ_DISPATCH_COUNT[k] = 0

    def _dispatch_delta(self, key: str) -> int:
        return int(self.srv._TOOLQ_DISPATCH_COUNT.get(key, 0))

    def test_missing_source_type_rejects_fast_no_dispatch(self):
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(self.srv.memory_ingest_path())
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "MISSING_REQUIRED_FIELD", payload)
        self.assertIn("source_type", payload["missing_fields"])
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("ingest_path"), 0)

    def test_invalid_source_type_enum_rejects_fast_no_dispatch(self):
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(self.srv.memory_ingest_path(source_type="bogus"))
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "SCHEMA_VIOLATION", payload)
        self.assertEqual(payload.get("field"), "source_type")
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("ingest_path"), 0)

    def test_manual_text_missing_content_rejects_fast_no_dispatch(self):
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(self.srv.memory_ingest_path(source_type="manual_text"))
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "MISSING_REQUIRED_FIELD", payload)
        self.assertIn("content", payload["missing_fields"])
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("ingest_path"), 0)

    def test_file_missing_path_rejects_fast_no_dispatch(self):
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(self.srv.memory_ingest_path(source_type="file"))
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "MISSING_REQUIRED_FIELD", payload)
        self.assertIn("path", payload["missing_fields"])
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("ingest_path"), 0)

    def test_folder_missing_path_rejects_fast_no_dispatch(self):
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(self.srv.memory_ingest_path(source_type="folder"))
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "MISSING_REQUIRED_FIELD", payload)
        self.assertIn("path", payload["missing_fields"])
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("ingest_path"), 0)

    def test_path_with_control_char_rejects_fast_no_dispatch(self):
        """Path containing a C0 control char must be rejected locally."""
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(
                self.srv.memory_ingest_path(source_type="file", path="bad\x00path.txt")
            )
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "SCHEMA_VIOLATION", payload)
        self.assertEqual(payload.get("field"), "path")
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("ingest_path"), 0)

    def test_options_wrong_type_rejects_fast_no_dispatch(self):
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(
                self.srv.memory_ingest_path(
                    source_type="manual_text", content="hello", options="not-a-dict"
                )
            )
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "SCHEMA_VIOLATION", payload)
        self.assertEqual(payload.get("field"), "options")
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("ingest_path"), 0)

    def test_max_file_mb_wrong_type_rejects_fast_no_dispatch(self):
        with mock.patch.object(
            self.srv, "_toolq_post", side_effect=AssertionError("must not dispatch")
        ):
            start = time.perf_counter()
            result = asyncio.run(
                self.srv.memory_ingest_path(
                    source_type="manual_text", content="hello", max_file_mb="ten"
                )
            )
            elapsed = time.perf_counter() - start
        payload = json.loads(result)
        self.assertEqual(payload["error_name"], "SCHEMA_VIOLATION", payload)
        self.assertEqual(payload.get("field"), "max_file_mb")
        self.assertLess(elapsed, FAIL_FAST_BUDGET_SEC)
        self.assertEqual(self._dispatch_delta("ingest_path"), 0)

    def test_valid_manual_text_still_dispatches(self):
        fake = {"ok": True, "ingested": 1}
        with mock.patch.object(self.srv, "_toolq_post", return_value=fake) as post_mock:
            async def _bump(*args, **kwargs):
                self.srv._TOOLQ_DISPATCH_COUNT["ingest_path"] += 1
                return fake
            post_mock.side_effect = _bump
            result = asyncio.run(
                self.srv.memory_ingest_path(source_type="manual_text", content="hello")
            )
        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "toolq")
        self.assertEqual(self._dispatch_delta("ingest_path"), 1)

    def test_valid_file_path_still_dispatches(self):
        fake = {"ok": True, "ingested": 1}
        with mock.patch.object(self.srv, "_toolq_post", return_value=fake) as post_mock:
            async def _bump(*args, **kwargs):
                self.srv._TOOLQ_DISPATCH_COUNT["ingest_path"] += 1
                return fake
            post_mock.side_effect = _bump
            result = asyncio.run(
                self.srv.memory_ingest_path(source_type="file", path="D:/tmp/notes.md")
            )
        payload = json.loads(result)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "toolq")
        self.assertEqual(self._dispatch_delta("ingest_path"), 1)


if __name__ == "__main__":
    unittest.main()
