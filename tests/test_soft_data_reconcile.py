"""Smoke test for scripts/soft_data_reconcile.py — the read-only tool-surface
bloat / redundancy analyzer.

Guards STRUCTURE + invariants (family-aware redundancy, curated-core protection,
honest usage caveat), not specific counts — those drift as the surface evolves.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

import soft_data_reconcile as sdr  # noqa: E402


class TestSoftDataReconcile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = sdr.build(REPO)

    def test_report_structure(self):
        for key in ("summary", "flags", "redundancy", "records",
                    "param_concepts", "alias_broken_targets", "see_also", "skills"):
            self.assertIn(key, self.report)

    def test_overlap_scan_shapes(self):
        pc = self.report["param_concepts"]
        self.assertIn("conflicts", pc)
        self.assertIn("ambiguous_names", pc)
        self.assertIsInstance(pc["ambiguous_names"], dict)
        # every ambiguous name maps to >=2 distinct canonicals (that is the definition)
        for _k, variants in pc["ambiguous_names"].items():
            self.assertGreaterEqual(len(variants), 2)

    def test_defect_count_consistent(self):
        s = self.report["summary"]
        expected = (len(self.report["alias_broken_targets"])
                    + len(self.report["see_also"]["dangling"]))
        self.assertEqual(s["defect_count"], expected)

    def test_exposed_alias_param_not_a_false_defect(self):
        # Regression guard: find_symbol/regex_search expose a friendly alias param
        # (query) that _ARG_ALIASES remaps to a REAL param (name/pattern). That is
        # intentional, NOT an alias-broken-target defect.
        broken_tools = {b["tool"] for b in self.report["alias_broken_targets"]}
        for t in ("find_symbol", "regex_search", "write_new_file", "find_callers"):
            self.assertNotIn(t, broken_tools,
                             f"{t} wrongly flagged as an alias defect")

    def test_summary_has_honest_caveat(self):
        s = self.report["summary"]
        self.assertGreater(s["total_tools"], 100)
        self.assertIn("by_tier", s)
        # The tiny-window caveat MUST be present so usage=0 is never read as dead.
        self.assertIn("usage_window_note", s)

    def test_records_carry_labels(self):
        recs = self.report["records"]
        self.assertGreater(len(recs), 100)
        sample = next(iter(recs.values()))
        for f in ("name", "tier", "deprecated", "in_curated_core",
                  "usage_count", "desc_len", "see_also_in"):
            self.assertIn(f, sample)

    def test_redundancy_is_cross_family(self):
        for r in self.report["redundancy"]:
            self.assertNotEqual(sdr._family(r["a"]), sdr._family(r["b"]),
                                f"same-family pair leaked: {r['a']} {r['b']}")

    def test_curated_core_never_slim_candidate(self):
        slim = set(self.report["flags"].get("SLIM_CANDIDATE", []))
        for name, r in self.report["records"].items():
            if r["in_curated_core"]:
                self.assertNotIn(name, slim,
                                 f"curated-core tool flagged for slimming: {name}")

    def test_deprecated_not_slim_candidate(self):
        slim = set(self.report["flags"].get("SLIM_CANDIDATE", []))
        for name, r in self.report["records"].items():
            if r["deprecated"]:
                self.assertNotIn(name, slim)


class TestUsageLogRecordEndsAtItsNewlineOnly(unittest.TestCase):
    # USAGE_LOG.jsonl is written with ensure_ascii=False (session/logs.py), so a raw
    # U+2028 / U+0085 can sit INSIDE a record; str.splitlines() cut it there and the
    # call went uncounted.
    def test_usage_counts_a_record_holding_a_line_break(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / ".orchestrator_cache" / "USAGE_LOG.jsonl"
            log.parent.mkdir()
            row = {"tool": "t", "ok": True, "ts": "2026-09-25", "note": "a b\x85c"}
            log.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8", newline="")
            stats = sdr._usage(Path(tmp))
        self.assertEqual(dict(stats["t"]), {"count": 1, "ok": 1, "last": "2026-09-25"})


if __name__ == "__main__":
    unittest.main()
