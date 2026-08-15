"""GRAPH_ENABLED=0 canary (CD-P1 bridge, 2026-08-11).

Invariant under test (WHY): GRAPH_ENABLED=0 (the default) MUST keep every
existing [A] flow byte-identical to its pre-graph degraded payload. Turning
the flag ON must only enable graph-enhanced flows; turning it OFF must never
break, raise, or change the shape of what callers already receive.

This is the canary: if a future change accidentally makes a graph tool raise
or return a different payload shape when GRAPH_ENABLED=0, this test fails.

Covers:
  - graph_find_symbol with GRAPH_ENABLED=0 returns {enabled: False, results: []}.
  - cbm_trace_path with GRAPH_ENABLED=0 returns {enabled: False, edges: []}.
  - template_query with GRAPH_ENABLED=0 returns {enabled: False, results: []}.
  - cbm_get_architecture with GRAPH_ENABLED=0 returns {enabled: False, modules: []}.
  - dead_code_candidates with GRAPH_ENABLED=0 returns {enabled: False, candidates: []}.
  - council_graph_context with GRAPH_ENABLED=0 returns {graph_status: "disabled"}.
  - None of these calls raise.
  - The DTO envelope still normalizes correctly (empty symbols/edges).
"""
import os

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.query import (  # noqa: E402
    graph_find_symbol, cbm_trace_path, template_query,
    cbm_get_architecture, dead_code_candidates, council_graph_context,
)
from orchestrator.graph.dto import normalize_graph_dto  # noqa: E402


class TestGraphEnabledOffCanary(unittest.TestCase):
    """GRAPH_ENABLED=0: every graph tool returns the safe degraded payload.

    A13 fix (2026-08-16): the graph layer now reads GRAPH_ENABLED live from the
    environment at call time (orchestrator.graph.config.graph_enabled), so the
    flag is no longer frozen at import. This canary therefore drives the flag
    per-test via os.environ (set 0 in setUp, restore in tearDown) instead of
    poisoning the shared process env at import or monkeypatching a frozen module
    constant. The env write is scoped to each test, so import order no longer
    changes what any other test module observes.
    """

    def setUp(self):
        self._saved = os.environ.get("GRAPH_ENABLED")
        os.environ["GRAPH_ENABLED"] = "0"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("GRAPH_ENABLED", None)
        else:
            os.environ["GRAPH_ENABLED"] = self._saved

    def test_graph_find_symbol_disabled(self):
        result = graph_find_symbol("D:/nonexistent", "anything")
        self.assertFalse(result["enabled"])
        self.assertEqual(result["results"], [])
        self.assertIsNone(result["error"])

    def test_cbm_trace_path_disabled(self):
        result = cbm_trace_path("D:/nonexistent", "any:node:id")
        self.assertFalse(result["enabled"])
        self.assertEqual(result["edges"], [])
        self.assertEqual(result["nodes_seen"], 0)
        self.assertIsNone(result["error"])

    def test_template_query_disabled(self):
        result = template_query("D:/nonexistent", "callers", name="foo")
        self.assertFalse(result["enabled"])
        self.assertEqual(result["results"], [])
        self.assertIsNone(result["error"])

    def test_cbm_get_architecture_disabled(self):
        result = cbm_get_architecture("D:/nonexistent")
        self.assertFalse(result["enabled"])
        self.assertEqual(result["modules"], [])
        self.assertEqual(result["entry_points"], [])
        self.assertIsNone(result["error"])

    def test_dead_code_candidates_disabled(self):
        result = dead_code_candidates("D:/nonexistent")
        self.assertFalse(result["enabled"])
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["error"])

    def test_council_graph_context_disabled(self):
        result = council_graph_context("D:/nonexistent", name="foo")
        self.assertEqual(result["graph_status"], "disabled")
        self.assertFalse(result["graph_used"])
        self.assertEqual(result["graph_degraded_reason"], "GRAPH_ENABLED=0")
        self.assertEqual(result["symbols"], [])
        self.assertEqual(result["files"], [])

    def test_dto_envelope_disabled(self):
        """DTO normalizer still works with GRAPH_ENABLED=0 (empty envelope)."""
        env = normalize_graph_dto("D:/nonexistent")
        self.assertEqual(env["dto"], "cbm.graph/v1")
        self.assertEqual(env["symbols"], [])
        self.assertEqual(env["edges"], [])
        self.assertFalse(env["provenance"]["graph_enabled"])

    def test_no_call_raises(self):
        """Table-driven: every disabled-mode graph function must return its
        degraded envelope shape without raising.

        Each entry is (callable, kwargs, expected_checker) where
        expected_checker(result) asserts the degraded payload shape.
        """
        cases = [
            (graph_find_symbol,
             {"project_path": "D:/nonexistent", "query": "anything"},
             lambda r: (self.assertFalse(r["enabled"]),
                        self.assertEqual(r["results"], []),
                        self.assertIsNone(r["error"]))),
            (cbm_trace_path,
             {"project_path": "D:/nonexistent",
              "start_node_id": "any:node:id"},
             lambda r: (self.assertFalse(r["enabled"]),
                        self.assertEqual(r["edges"], []),
                        self.assertEqual(r["nodes_seen"], 0),
                        self.assertIsNone(r["error"]))),
            (template_query,
             {"project_path": "D:/nonexistent",
              "template": "callers", "name": "foo"},
             lambda r: (self.assertFalse(r["enabled"]),
                        self.assertEqual(r["results"], []),
                        self.assertIsNone(r["error"]))),
            (cbm_get_architecture,
             {"project_path": "D:/nonexistent"},
             lambda r: (self.assertFalse(r["enabled"]),
                        self.assertEqual(r["modules"], []),
                        self.assertEqual(r["entry_points"], []),
                        self.assertIsNone(r["error"]))),
            (dead_code_candidates,
             {"project_path": "D:/nonexistent"},
             lambda r: (self.assertFalse(r["enabled"]),
                        self.assertEqual(r["candidates"], []),
                        self.assertIsNone(r["error"]))),
            (council_graph_context,
             {"project_path": "D:/nonexistent", "name": "foo"},
             lambda r: (self.assertEqual(r["graph_status"], "disabled"),
                        self.assertFalse(r["graph_used"]),
                        self.assertEqual(r["symbols"], []),
                        self.assertEqual(r["files"], []))),
        ]
        for fn, kwargs, check in cases:
            with self.subTest(fn=fn.__name__):
                result = fn(**kwargs)  # must not raise
                check(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
