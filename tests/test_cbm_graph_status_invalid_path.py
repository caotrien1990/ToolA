"""Regression test for F-01: cbm_graph_status must reject a nonexistent project
path with INVALID_PROJECT_PATH instead of falling through to graph_status and
reporting enabled=True + freshness="not_indexed" (which tricks callers into
graph operations under false readiness).

Invariant under test (WHY): a missing project root is a caller bug, NOT a
valid-but-unindexed project. The two must produce distinguishable responses so
callers can fail loud on bad input while still enjoying the documented
fail-open contract for real projects that simply lack a graph DB.
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["GRAPH_ENABLED"] = "1"  # must precede orchestrator.graph.config import

from orchestrator.graph.tools import cbm_graph_status  # noqa: E402


class TestCbmGraphStatusInvalidPath(unittest.TestCase):
    def test_nonexistent_path_returns_invalid_project_path(self):
        """F-01: nonexistent project_path -> ok=False, error_name=INVALID_PROJECT_PATH."""
        bogus = "D:/__tooltest_nonexistent__"
        # Guard the guard: if the box happens to actually have this dir, skip
        # rather than false-fail (defensive, matches the bug report's example).
        if os.path.isdir(bogus):
            self.skipTest(f"bogus path unexpectedly exists: {bogus}")
        raw = asyncio.run(cbm_graph_status(project_path=bogus))
        payload = json.loads(raw)
        self.assertFalse(payload.get("ok"), f"expected ok=False, got: {payload}")
        self.assertEqual(
            payload.get("error_name"),
            "INVALID_PROJECT_PATH",
            f"expected INVALID_PROJECT_PATH, got: {payload}",
        )
        self.assertIn("project_path", payload.get("error", ""))

    def test_valid_unindexed_project_still_fail_open(self):
        """Valid project root with no graph DB -> ok=True, freshness='not_indexed'.

        Documents that the F-01 fix does NOT change the documented fail-open
        contract for a real, existing project that simply has not been indexed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            raw = asyncio.run(cbm_graph_status(project_path=tmp))
            payload = json.loads(raw)
            self.assertTrue(payload.get("ok"), f"expected ok=True, got: {payload}")
            result = payload.get("result") or {}
            self.assertEqual(
                result.get("freshness"),
                "not_indexed",
                f"expected freshness='not_indexed' for unindexed valid project, got: {result}",
            )
            # enabled reflects GRAPH_ENABLED; the fix must not flip it for a valid path.
            self.assertTrue(result.get("enabled"), f"expected enabled=True (GRAPH_ENABLED=1), got: {result}")
            self.assertIsNone(result.get("error"), f"expected error=None for valid path, got: {result}")

    def test_empty_path_still_schema_violation(self):
        """Pre-existing SCHEMA_VIOLATION branch must remain unchanged."""
        raw = asyncio.run(cbm_graph_status(project_path=""))
        payload = json.loads(raw)
        self.assertFalse(payload.get("ok"))
        self.assertEqual(payload.get("error_name"), "SCHEMA_VIOLATION")


if __name__ == "__main__":
    unittest.main()
