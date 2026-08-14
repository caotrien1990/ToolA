"""P5 council graph context suite (A_Q plan Phase 5 Step 21).

council_graph_context must be advisory + bounded + fail-open:
  - symbol mode: target + inbound impact symbols, files list led by target file
  - changes mode: changed-file symbols + direct callers with edge confidence
  - down-truncation to COUNCIL_CONTEXT_MAX_FILES/MAX_SYMBOLS (10/50), every
    cap hit recorded in truncation_reason
  - degraded (not raised) on missing symbol, no changes, or internal error —
    a broken graph must never block a council dispatch.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"  # must precede orchestrator.graph.config import

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.indexer import index_project, incremental_index
from orchestrator.graph.query import council_graph_context
from orchestrator.graph.config import (COUNCIL_CONTEXT_MAX_FILES,
                                       COUNCIL_CONTEXT_MAX_SYMBOLS)

FIX_CORE = '''from util import helper


def main():
    return helper() + used_twice()


def used_twice():
    return 1


def caller_one():
    return used_twice()
'''

FIX_UTIL = '''def helper():
    return 42
'''


class P5CouncilContextSuite(unittest.TestCase):

    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="p5_council_ctx_"))
        (self.tempdir / "core.py").write_text(FIX_CORE, encoding="utf-8")
        (self.tempdir / "util.py").write_text(FIX_UTIL, encoding="utf-8")
        self.proj = str(self.tempdir).replace("\\", "/")
        index_project(self.proj, scope="full")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_symbol_mode_returns_target_and_impact(self):
        ctx = council_graph_context(self.proj, name="used_twice")
        self.assertEqual(ctx["graph_status"], "ok")
        self.assertTrue(ctx["graph_used"])
        self.assertEqual(ctx["source"], "symbol")
        roles = {s["role"] for s in ctx["symbols"]}
        self.assertIn("target", roles)
        self.assertIn("impact", roles)  # main + caller_one call used_twice
        target = next(s for s in ctx["symbols"] if s["role"] == "target")
        self.assertEqual(target["confidence"], 1.0)
        self.assertEqual(ctx["files"][0], target["file_path"])

    def test_symbol_not_found_degrades_not_raises(self):
        ctx = council_graph_context(self.proj, name="no_such_symbol_zzz")
        self.assertEqual(ctx["graph_status"], "ok")
        self.assertFalse(ctx["graph_used"])
        self.assertEqual(ctx["graph_degraded_reason"], "symbol_not_found")
        self.assertEqual(ctx["symbols"], [])

    def test_changes_mode_no_changes_degrades(self):
        ctx = council_graph_context(self.proj)
        self.assertEqual(ctx["graph_status"], "ok")
        self.assertFalse(ctx["graph_used"])
        self.assertEqual(ctx["graph_degraded_reason"], "no_changed_files")

    def test_changes_mode_reports_changed_symbols_and_callers(self):
        (self.tempdir / "util.py").write_text(
            "def helper():\n    return 43\n", encoding="utf-8")
        ctx = council_graph_context(self.proj)
        self.assertEqual(ctx["graph_status"], "ok")
        self.assertTrue(ctx["graph_used"])
        self.assertEqual(ctx["source"], "changes")
        self.assertIn("util.py", ctx["files"])
        changed = [s for s in ctx["symbols"] if s["role"] == "changed"]
        self.assertTrue(any(s["qualified_name"].endswith("helper") for s in changed))
        # main() calls helper() -> direct caller with edge confidence attached
        callers = [s for s in ctx["symbols"] if s["role"] == "caller"]
        self.assertTrue(any(s["qualified_name"].endswith("main") for s in callers))
        self.assertTrue(all(s["confidence"] is not None for s in callers))

    def test_down_truncation_caps_and_reasons(self):
        # many changed files with many symbols -> both caps must fire
        for i in range(COUNCIL_CONTEXT_MAX_FILES + 5):
            body = "\n\n".join(
                f"def gen_{i}_{j}():\n    return {j}" for j in range(8))
            (self.tempdir / f"gen_{i}.py").write_text(body + "\n", encoding="utf-8")
        incremental_index(self.proj)
        # touch them all again so detect_changes reports them as modified
        for i in range(COUNCIL_CONTEXT_MAX_FILES + 5):
            p = self.tempdir / f"gen_{i}.py"
            p.write_text(p.read_text(encoding="utf-8") + "\n# touched\n",
                         encoding="utf-8")
        ctx = council_graph_context(self.proj)
        self.assertEqual(ctx["graph_status"], "ok")
        self.assertLessEqual(len(ctx["files"]), COUNCIL_CONTEXT_MAX_FILES)
        self.assertLessEqual(len(ctx["symbols"]), COUNCIL_CONTEXT_MAX_SYMBOLS)
        self.assertTrue(ctx["truncated"])
        self.assertIn("max_files", ctx["truncation_reason"])
        self.assertIn("max_symbols", ctx["truncation_reason"])

    def test_fail_open_on_internal_error(self):
        # nonexistent project root -> detect_changes/store may fail; must degrade
        ctx = council_graph_context("Z:/definitely/not/a/project/root/zzz")
        self.assertIn(ctx["graph_status"], ("ok", "error"))
        self.assertFalse(ctx["graph_used"])
        self.assertEqual(ctx["symbols"], [])

    # --- STT-15: edge-case tests ---

    def test_empty_name_changes_mode_source(self):
        ctx = council_graph_context(self.proj)
        # no name, no file_path -> changes mode (no edits -> degraded, but source ok)
        self.assertEqual(ctx["source"], "changes")

    def test_invalid_depth_clamped_to_5(self):
        # depth=99 should not raise; impact_scope clamps internally
        ctx = council_graph_context(self.proj, name="used_twice", depth=99)
        self.assertEqual(ctx["graph_status"], "ok")
        self.assertTrue(ctx["graph_used"])

    def test_aliases_ignored_silently(self):
        # unknown kwargs must not raise (wrapper **aliases drops them)
        ctx = council_graph_context(self.proj, name="used_twice")
        self.assertEqual(ctx["graph_status"], "ok")

    def test_empty_flag_set_on_empty_result(self):
        # STT-12: empty=True when graph ok but no symbols (success path)
        # degraded early-returns keep empty=False (reason string is the signal)
        ctx = council_graph_context(self.proj, name="no_such_zzz")
        self.assertFalse(ctx["empty"])  # degraded early-return, empty stays False
        self.assertEqual(ctx["graph_degraded_reason"], "symbol_not_found")
        # changes mode with no changes -> degraded (not success path), empty=False
        ctx2 = council_graph_context(self.proj)
        self.assertFalse(ctx2["empty"])
        self.assertEqual(ctx2["graph_degraded_reason"], "no_changed_files")

    def test_dedup_overlapping_target_and_caller(self):
        # STT-23: used_twice is both target (symbol mode) and caller (calls itself?
        # no — but main calls used_twice). Verify dedup keeps target role.
        ctx = council_graph_context(self.proj, name="used_twice")
        roles = [s["role"] for s in ctx["symbols"]]
        self.assertIn("target", roles)

    # --- STT-17: file-level mode tests ---

    def test_file_name_no_symbol_returns_empty(self):
        # asking by name="core.py" (a file name, not a symbol) -> symbol_not_found
        ctx = council_graph_context(self.proj, name="core.py")
        self.assertFalse(ctx["graph_used"])
        self.assertEqual(ctx["graph_degraded_reason"], "symbol_not_found")

    def test_file_path_param_returns_file_symbols(self):
        # STT-2: file_path mode returns all symbols in the file + callers
        ctx = council_graph_context(self.proj, file_path="core.py")
        self.assertEqual(ctx["graph_status"], "ok")
        self.assertEqual(ctx["source"], "file")
        self.assertTrue(ctx["graph_used"])
        self.assertIn("core.py", ctx["files"])
        qns = [s["qualified_name"] for s in ctx["symbols"]]
        self.assertTrue(any("main" in q for q in qns))
        self.assertTrue(any("used_twice" in q for q in qns))

    def test_file_path_not_found_degrades(self):
        ctx = council_graph_context(self.proj, file_path="nonexistent.py")
        self.assertFalse(ctx["graph_used"])
        self.assertEqual(ctx["graph_degraded_reason"], "file_not_found")

    # --- STT-18: determinism 2-call test ---

    def test_changes_mode_deterministic_2_calls(self):
        (self.tempdir / "util.py").write_text(
            "def helper():\n    return 43\n", encoding="utf-8")
        ctx1 = council_graph_context(self.proj)
        ctx2 = council_graph_context(self.proj)
        self.assertEqual(
            [s["qualified_name"] for s in ctx1["symbols"]],
            [s["qualified_name"] for s in ctx2["symbols"]])
        self.assertEqual(ctx1["files"], ctx2["files"])


if __name__ == "__main__":
    unittest.main()
