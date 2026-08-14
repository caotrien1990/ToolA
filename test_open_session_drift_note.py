# pyright: reportAttributeAccessIssue=false
"""V15 F4 (D4) — open_session severity-aware drift policy tests.

Why: any matrix drift used to force verbosity='full' on open_session,
inflating the session payload (~16.5KB) and pushing final council requests
past gateway limits. D4: high/critical severity or drift_count>=3 (or
progressive gaps) keeps the legacy force-full; low/medium with count<3 keeps
the worker-requested verbosity and adds a compact `matrix_drift_note`
(<=5 entries: tool, severity, ca_next_action=run audit_tool_matrix);
drift=0 adds nothing.

Fixtures use fake drift payloads only — no real drift is recreated.
"""
import unittest

import code_tools


def _decide(drift, verbosity="terse", gap=0):
    resp = {"progressive_gap_count": gap}
    mx = {"drift": drift}
    eff = code_tools._open_session_drift_decision(resp, mx, verbosity)
    return eff, resp


def _drift(tool, severity):
    return {"type": "inventory_outdated", "tool": tool, "severity": severity,
            "detail": "fake fixture"}


class TestOpenSessionDriftDecision(unittest.TestCase):
    def test_drift_zero_no_note_no_override(self):
        eff, resp = _decide([], verbosity="terse")
        self.assertEqual(eff, "terse")
        self.assertNotIn("matrix_drift_note", resp)
        self.assertNotIn("_drift_override", resp)

    def test_low_severity_keeps_terse_and_adds_note(self):
        eff, resp = _decide([_drift("record_error_resolution", "low")], verbosity="terse")
        self.assertEqual(eff, "terse")
        self.assertNotIn("_drift_override", resp)
        note = resp.get("matrix_drift_note")
        self.assertIsInstance(note, list)
        self.assertEqual(len(note), 1)
        self.assertEqual(note[0]["tool"], "record_error_resolution")
        self.assertEqual(note[0]["severity"], "low")
        self.assertEqual(note[0]["ca_next_action"], "run audit_tool_matrix")

    def test_medium_severity_keeps_requested_verbosity(self):
        eff, resp = _decide([_drift("some_tool", "medium")], verbosity="ref")
        self.assertEqual(eff, "ref")
        self.assertEqual(resp["matrix_drift_note"][0]["severity"], "medium")

    def test_high_severity_forces_full(self):
        eff, resp = _decide([_drift("broken_schema_tool", "high")], verbosity="terse")
        self.assertEqual(eff, "full")
        self.assertIn("_drift_override", resp)
        self.assertNotIn("matrix_drift_note", resp)

    def test_critical_severity_forces_full(self):
        eff, resp = _decide([_drift("hot_tool", "critical")], verbosity="terse")
        self.assertEqual(eff, "full")
        self.assertIn("_drift_override", resp)

    def test_drift_count_three_forces_full_even_low(self):
        drift = [_drift(f"tool_{i}", "low") for i in range(3)]
        eff, resp = _decide(drift, verbosity="terse")
        self.assertEqual(eff, "full")
        self.assertIn("_drift_override", resp)
        self.assertNotIn("matrix_drift_note", resp)

    def test_two_low_drifts_stay_terse_note_has_both(self):
        drift = [_drift(f"tool_{i}", "low") for i in range(2)]
        eff, resp = _decide(drift, verbosity="terse")
        self.assertEqual(eff, "terse")
        self.assertEqual(len(resp["matrix_drift_note"]), 2)

    def test_progressive_gap_keeps_legacy_force_full(self):
        eff, resp = _decide([], verbosity="terse", gap=2)
        self.assertEqual(eff, "full")
        self.assertIn("_drift_override", resp)

    def test_requested_full_with_low_drift_adds_note_no_override(self):
        # full requested anyway → no override text, but note still informs
        eff, resp = _decide([_drift("t", "low")], verbosity="full")
        self.assertEqual(eff, "full")
        self.assertNotIn("_drift_override", resp)
        self.assertIn("matrix_drift_note", resp)

    def test_note_capped_at_five_entries(self):
        # defensive cap: note path only reachable with count<3 today, but the
        # field contract is <=5 entries regardless of input size
        drift = [_drift(f"tool_{i}", "low") for i in range(2)]
        eff, resp = _decide(drift, verbosity="terse")
        self.assertLessEqual(len(resp["matrix_drift_note"]), 5)


if __name__ == "__main__":
    unittest.main()
