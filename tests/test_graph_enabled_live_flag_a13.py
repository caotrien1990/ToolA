"""A13 regression: GRAPH_ENABLED must be a LIVE env read, never a frozen
import-time snapshot.

WHY (root cause): orchestrator.graph.config used to bind
``GRAPH_ENABLED = os.environ.get("GRAPH_ENABLED", ...)`` once at import, and
every consumer copied that bool via ``from .config import GRAPH_ENABLED``.
Whichever test module imported config first froze the flag process-wide. The
GRAPH_ENABLED=0 canary imported it disabled during collection, so 46 tests
across 7 modules (test_p0/test_p1/test_p5/test_graph_ext/test_find_symbol/
test_cbm_cycle_point/test_cbm_graph_status) that set GRAPH_ENABLED=1 before
their own import silently ran against a disabled graph and failed — but only
when the canary happened to be collected first.

The fix makes the flag live: config.graph_enabled() re-reads os.environ on every
call, config.GRAPH_ENABLED resolves through PEP 562 __getattr__ to the same live
read, and every graph consumer calls graph_enabled() at request time.

These tests assert the mechanism directly (flipping os.environ changes what the
graph layer reports on the very next call, with no reload/monkeypatch), plus an
end-to-end subprocess that reproduces the exact import order that used to drop 46
tests and proves it now stays green. If the freeze regresses, both fail.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import config as _config
from orchestrator.graph.query import graph_find_symbol, template_query
from orchestrator.graph.dto import normalize_graph_dto
from orchestrator.graph.cycle_point import compute_cycle_point

_PROJ_ROOT = str(Path(__file__).resolve().parents[1])


class GraphEnabledLiveFlagA13(unittest.TestCase):
    """The flag is read live from the env on every call, not frozen at import."""

    def setUp(self):
        self._saved = os.environ.get("GRAPH_ENABLED")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("GRAPH_ENABLED", None)
        else:
            os.environ["GRAPH_ENABLED"] = self._saved

    def test_config_graph_enabled_follows_env_within_one_process(self):
        os.environ["GRAPH_ENABLED"] = "1"
        self.assertTrue(_config.graph_enabled())
        self.assertTrue(_config.GRAPH_ENABLED)  # PEP 562 attribute stays live
        os.environ["GRAPH_ENABLED"] = "0"
        self.assertFalse(_config.graph_enabled())
        self.assertFalse(_config.GRAPH_ENABLED)

    def test_query_layer_reflects_live_flag_without_reimport(self):
        # Same imported callables, opposite results, driven only by the env.
        os.environ["GRAPH_ENABLED"] = "0"
        self.assertFalse(graph_find_symbol("D:/nonexistent", "x")["enabled"])
        self.assertFalse(template_query("D:/nonexistent", "callers",
                                        name="x")["enabled"])
        os.environ["GRAPH_ENABLED"] = "1"
        # Enabled + a nonexistent project => enabled True, fail-open (no raise).
        self.assertTrue(graph_find_symbol("D:/nonexistent", "x")["enabled"])
        self.assertTrue(template_query("D:/nonexistent", "callers",
                                       name="x")["enabled"])

    def test_dto_and_cycle_point_reflect_live_flag(self):
        os.environ["GRAPH_ENABLED"] = "0"
        self.assertFalse(
            normalize_graph_dto("D:/nonexistent")["provenance"]["graph_enabled"])
        self.assertFalse(compute_cycle_point("D:/nonexistent")["enabled"])
        os.environ["GRAPH_ENABLED"] = "1"
        self.assertTrue(
            normalize_graph_dto("D:/nonexistent")["provenance"]["graph_enabled"])
        self.assertTrue(compute_cycle_point("D:/nonexistent")["enabled"])

    def test_import_order_that_used_to_drop_46_tests_is_now_green(self):
        """End-to-end: import the GRAPH_ENABLED=0 canary FIRST, then run the
        modules that need it enabled, in one pytest process. Before the fix this
        order dropped 46 tests; it must now pass cleanly."""
        ordered = [
            "tests/test_cbm_graph_enabled_canary.py",
            "tests/test_cbm_cycle_point.py",
            "tests/test_cbm_graph_dto.py",
            "tests/test_cbm_graph_status_invalid_path.py",
            "tests/test_extractor_callable_refs.py",
            "tests/test_find_symbol_def_only_p2_sym_03.py",
            "tests/test_graph_ext_edges.py",
            "tests/test_p0_graph_fixture_suite.py",
            "tests/test_p1_graph_advanced_suite.py",
            "tests/test_p5_council_context.py",
            "tests/test_p6_js_extractor.py",
        ]
        # Do not inherit a poisoned GRAPH_ENABLED from the parent process.
        child_env = dict(os.environ)
        child_env.pop("GRAPH_ENABLED", None)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *ordered,
             "-p", "no:randomly", "-q",
             "--basetemp=.orchestrator_cache/_a13_child_tmp"],
            cwd=_PROJ_ROOT, env=child_env,
            capture_output=True, text=True, timeout=600,
        )
        self.assertEqual(
            proc.returncode, 0,
            msg=("forced canary-first order must be green after the A13 fix.\n"
                 f"STDOUT tail:\n{proc.stdout[-2000:]}\n"
                 f"STDERR tail:\n{proc.stderr[-1000:]}"))
        self.assertNotIn(" failed", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
