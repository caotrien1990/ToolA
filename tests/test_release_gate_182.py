import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestReleaseGate182(unittest.TestCase):
    def test_coverage_matrix_has_all_182_ids_and_no_open_todos(self):
        # R3 cleanup: final_fix_coverage_182.md (near-duplicate) was removed;
        # fix_coverage_182.md is the single coverage matrix artifact.
        coverage = (ROOT / "fix_coverage_182.md").read_text(encoding="utf-8")
        ids = [int(match) for match in re.findall(r"^\|\s*(\d+)\s*\|", coverage, re.MULTILINE)]

        self.assertEqual(ids, list(range(1, 183)))
        self.assertNotIn("| TODO |", coverage)
        self.assertNotIn("| DOING |", coverage)
        self.assertNotIn("| DEFERRED |", coverage)
        self.assertIn("| 177 | Backlog restore_from_backup enhancement tracked", coverage)
        self.assertIn("| 180 | Backlog diff_between_backups enhancement tracked", coverage)
        self.assertEqual(coverage.count("| BACKLOG |"), 2)

    def test_baseline_preserves_failure_and_retry_views(self):
        baseline = json.loads((ROOT / "release_gate_182" / "baseline_before_fix.json").read_text(encoding="utf-8-sig"))

        self.assertEqual(baseline["tool_count"], 38)
        self.assertIn("baseline_failure_view", baseline)
        self.assertIn("after_retry_coverage_view", baseline)
        self.assertIsInstance(baseline["tools"], list)
        self.assertEqual(len(baseline["tools"]), 38)

    def test_report_viewer_contains_required_ux_sections(self):
        html = (ROOT / "release_gate_182" / "report_viewer.html").read_text(encoding="utf-8")
        required = [
            "JSON contract renderer, not Markdown parser",
            "Baseline Failure View",
            "After-Retry Coverage View",
            "Tool-by-tool Appendix",
            "Token/Auth Panel",
            "Prompt/Schema Diff",
            "Help Docs",
            "Future Enhancements",
            "restore_from_backup",
            "diff_between_backups",
            "validateContract",
            "severity/type/error_name",
        ]
        for marker in required:
            self.assertIn(marker, html)
        self.assertNotIn("raw token", html.lower())


if __name__ == "__main__":
    unittest.main()
