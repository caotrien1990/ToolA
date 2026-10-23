"""Contract tests for orchestrator.lifecycle.livez_contract (Sprint 1).

Expectations are written as data from the plan's D1 decision tree, the D1b
adapter table, and the D6 deviation table (campaign livez-drift-20261023).
The historical restart_control_service function is not imported here: these
tests pin the NEW contract module; parity against the historical function
lives in the campaign harness (golden), not in the repo suite.
"""
import ast
import unittest
from pathlib import Path

from orchestrator.lifecycle import livez_contract as lc

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_FILE = REPO_ROOT / "orchestrator_server.py"

DEL = object()  # delete the key from BASE

BASE = {
    "ok": True,
    "service": "project-orchestrator",
    "livez_protocol": 2,
    "startup_phase": "booting",
    "boot_age_s": 100.0,
}


def payload(**overrides):
    merged = dict(BASE)
    for key, value in overrides.items():
        if value is DEL:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def verdict(pl, grace=None):
    assessment = lc.classify_livez({"outcome": "ok", "payload": pl})
    return assessment, lc.rcs_skip_verdict(assessment, grace)


# (name, payload, expected_state, expected_detail, expected_(skip, reason))
CASES = [
    # D6: protocol type coercion rows -> INVALID protocol_type
    ("protocol_2.5", payload(livez_protocol=2.5), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_true", payload(livez_protocol=True), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_str2", payload(livez_protocol="2"), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_2.0", payload(livez_protocol=2.0), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_1.0", payload(livez_protocol=1.0), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_null", payload(livez_protocol=None), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_list", payload(livez_protocol=[]), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_dict", payload(livez_protocol={}), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_abc", payload(livez_protocol="abc"), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_zero", payload(livez_protocol=0), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_negative", payload(livez_protocol=-1), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_inf", payload(livez_protocol=float("inf")), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    ("protocol_nan", payload(livez_protocol=float("nan")), lc.INVALID, "protocol_type", (False, "contract_invalid")),
    # D6: protocol missing -> legacy (PRESERVED)
    ("protocol_missing", payload(livez_protocol=DEL), lc.ALIVE_LEGACY, "", (False, "protocol_1_legacy")),
    ("protocol_1", payload(livez_protocol=1), lc.ALIVE_LEGACY, "", (False, "protocol_1_legacy")),
    ("protocol_3_booting", payload(livez_protocol=3), lc.ALIVE_BOOTING, "", (True, "booting_phase=booting_age=100.0s")),
    # D6: ready rows (PRESERVED)
    ("phase_ready", payload(startup_phase="ready"), lc.ALIVE_READY, "", (False, "already_ready")),
    ("phase_ready_degraded", payload(startup_phase="ready_degraded"), lc.ALIVE_READY, "", (False, "already_ready")),
    ("phase_ready_bad_age", payload(startup_phase="ready", boot_age_s="not-a-number"), lc.ALIVE_READY, "", (False, "already_ready")),
    # D6: booting rows (PRESERVED)
    ("phase_booting", payload(), lc.ALIVE_BOOTING, "", (True, "booting_phase=booting_age=100.0s")),
    ("phase_boot_write_sweep", payload(startup_phase="boot_write_sweep", boot_age_s=50.0), lc.ALIVE_BOOTING, "", (True, "booting_phase=boot_write_sweep_age=50.0s")),
    ("phase_unknown", payload(startup_phase="unknown_phase"), lc.ALIVE_BOOTING, "", (True, "booting_phase=unknown_phase_age=100.0s")),
    ("phase_booting_age_599", payload(boot_age_s=599), lc.ALIVE_BOOTING, "", (True, "booting_phase=booting_age=599.0s")),
    ("phase_booting_age_600", payload(boot_age_s=600), lc.ALIVE_BOOTING, "", (False, "boot_grace_expired")),
    ("phase_booting_age_601", payload(boot_age_s=601), lc.ALIVE_BOOTING, "", (False, "boot_grace_expired")),
    # D6: phase missing / empty / wrong type (PRESERVED via MISMATCH or BOOTING)
    ("phase_missing", payload(startup_phase=DEL), lc.MISMATCH, "", (True, "booting_phase=_age=100.0s")),
    ("phase_empty", payload(startup_phase=""), lc.ALIVE_BOOTING, "", (True, "booting_phase=_age=100.0s")),
    ("phase_int5", payload(startup_phase=5), lc.MISMATCH, "", (True, "booting_phase=5_age=100.0s")),
    ("phase_missing_age_expired", payload(startup_phase=DEL, boot_age_s=700.0), lc.MISMATCH, "", (False, "boot_grace_expired")),
    ("phase_missing_age_invalid", payload(startup_phase=DEL, boot_age_s=DEL), lc.MISMATCH, "boot_age_invalid", (False, "invalid_boot_age")),
    # D6: age rows
    ("age_str100", payload(boot_age_s="100"), lc.ALIVE_BOOTING, "", (True, "booting_phase=booting_age=100.0s")),
    ("age_bool", payload(boot_age_s=True), lc.ALIVE_BOOTING, "boot_age_invalid", (False, "invalid_boot_age")),
    ("age_not_a_number", payload(boot_age_s="not-a-number"), lc.ALIVE_BOOTING, "boot_age_invalid", (False, "invalid_boot_age")),
    ("age_missing", payload(boot_age_s=DEL), lc.ALIVE_BOOTING, "boot_age_invalid", (False, "invalid_boot_age")),
    ("age_inf", payload(boot_age_s=float("inf")), lc.ALIVE_BOOTING, "boot_age_invalid", (False, "invalid_boot_age")),
    ("age_str_inf", payload(boot_age_s="inf"), lc.ALIVE_BOOTING, "boot_age_invalid", (False, "invalid_boot_age")),
    ("age_str_nan", payload(boot_age_s="nan"), lc.ALIVE_BOOTING, "boot_age_invalid", (False, "invalid_boot_age")),
    ("age_nan", payload(boot_age_s=float("nan")), lc.ALIVE_BOOTING, "boot_age_invalid", (False, "invalid_boot_age")),
    # D6: service rows (missing PRESERVED, mismatch INTENDED; check precedes protocol)
    ("service_missing_r3_shape", payload(service=DEL), lc.ALIVE_BOOTING, "", (True, "booting_phase=booting_age=100.0s")),
    ("service_mismatch_p2", payload(service="other"), lc.INVALID, "service_mismatch", (False, "contract_invalid")),
    ("service_mismatch_p1_cross", payload(service="other", livez_protocol=1), lc.INVALID, "service_mismatch", (False, "contract_invalid")),
    # D6: ok row -- identity check applies to every combination (INTENDED)
    ("ok_false_p1", payload(ok=False, livez_protocol=1), lc.INVALID, "ok_not_true", (False, "contract_invalid")),
    ("ok_false_booting", payload(ok=False), lc.INVALID, "ok_not_true", (False, "contract_invalid")),
    ("ok_one", payload(ok=1), lc.INVALID, "ok_not_true", (False, "contract_invalid")),
    ("ok_str_true", payload(ok="true"), lc.INVALID, "ok_not_true", (False, "contract_invalid")),
    ("ok_missing", payload(ok=DEL), lc.INVALID, "ok_not_true", (False, "contract_invalid")),
    # D6: stopping rows (valid age INTENDED skip=False; invalid age PRESERVED)
    ("stopping_valid_age", payload(startup_phase="stopping"), lc.ALIVE_STOPPING, "", (False, "boot_grace_expired")),
    ("stopping_invalid_age", payload(startup_phase="stopping", boot_age_s="not-a-number"), lc.ALIVE_STOPPING, "boot_age_invalid", (False, "invalid_boot_age")),
]


class TestClassifyTable(unittest.TestCase):
    def test_d6_table(self):
        for name, pl, state, detail, expected in CASES:
            with self.subTest(case=name):
                assessment, got = verdict(pl)
                self.assertEqual(assessment.state, state)
                self.assertEqual(assessment.detail, detail)
                self.assertEqual(got, expected)

    def test_non_dict_payload(self):
        assessment, got = verdict([1, 2, 3])
        self.assertEqual(assessment.state, lc.INVALID)
        self.assertEqual(assessment.detail, "non_dict")
        self.assertEqual(got, (False, "invalid_payload"))

    def test_unreachable(self):
        assessment = lc.classify_livez({"outcome": "error", "error": "refused"})
        self.assertEqual(assessment.state, lc.UNREACHABLE)
        self.assertEqual(lc.rcs_skip_verdict(assessment), (False, "livez_unreachable"))

    def test_grace_per_call(self):
        # D6: grace theo-call (boot_grace_s=60) is preserved by the adapter.
        assessment, got = verdict(payload(), grace=60.0)
        self.assertEqual(assessment.state, lc.ALIVE_BOOTING)
        self.assertEqual(got, (False, "boot_grace_expired"))

    def test_is_alive_surface(self):
        for state_payload in [payload(), payload(livez_protocol=1),
                              payload(startup_phase="ready"),
                              payload(startup_phase="stopping"),
                              payload(startup_phase=DEL)]:
            assessment, _ = verdict(state_payload)
            self.assertTrue(assessment.is_alive, assessment.state)
        for bad in [payload(ok=False), payload(livez_protocol="2")]:
            assessment, _ = verdict(bad)
            self.assertFalse(assessment.is_alive, assessment.state)
        self.assertFalse(
            lc.classify_livez({"outcome": "error"}).is_alive)


class TestAssessmentFieldPins(unittest.TestCase):
    def test_error_outcome_fields(self):
        a = lc.classify_livez({"outcome": "error", "error": "x"})
        self.assertIsNone(a.protocol)
        self.assertEqual(a.phase, "")
        self.assertIsNone(a.boot_age_s)
        self.assertFalse(a.booting)

    def test_non_dict_fields(self):
        a = lc.classify_livez({"outcome": "ok", "payload": [1]})
        self.assertIsNone(a.protocol)
        self.assertEqual(a.phase, "")
        self.assertIsNone(a.boot_age_s)

    def test_raw_protocol_preserved(self):
        a = lc.classify_livez({"outcome": "ok", "payload": payload(livez_protocol=2.5)})
        self.assertEqual(a.protocol, 2.5)

    def test_phase_formula(self):
        a = lc.classify_livez({"outcome": "ok", "payload": payload(startup_phase=5)})
        self.assertEqual(a.phase, "5")
        a = lc.classify_livez({"outcome": "ok", "payload": payload(startup_phase=DEL)})
        self.assertEqual(a.phase, "")

    def test_age_parse(self):
        a = lc.classify_livez({"outcome": "ok", "payload": payload(boot_age_s="100")})
        self.assertEqual(a.boot_age_s, 100.0)
        a = lc.classify_livez({"outcome": "ok", "payload": payload(boot_age_s=True)})
        self.assertIsNone(a.boot_age_s)

    def test_booting_flag_only_for_booting(self):
        a = lc.classify_livez({"outcome": "ok", "payload": payload()})
        self.assertTrue(a.booting)
        a = lc.classify_livez({"outcome": "ok", "payload": payload(startup_phase=DEL)})
        self.assertEqual(a.state, lc.MISMATCH)
        self.assertFalse(a.booting)  # MISMATCH grace lives in the adapter


def _phase_literals_from_server():
    """String literals assigned to SERVER_STARTUP_PHASE (all branches of
    conditional expressions) plus 'startup_phase' values inside
    _liveness_payload dict literals. The display-only "starting" fallback
    in handle_council_dashboard is outside both scopes by construction."""
    tree = ast.parse(SERVER_FILE.read_text(encoding="utf-8"))
    values = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SERVER_STARTUP_PHASE"
            for t in node.targets
        ):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    values.add(sub.value)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "_liveness_payload"
        ):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    for key, val in zip(sub.keys, sub.values):
                        if isinstance(key, ast.Constant) and key.value == "startup_phase":
                            for s2 in ast.walk(val):
                                if isinstance(s2, ast.Constant) and isinstance(s2.value, str):
                                    values.add(s2.value)
    return values


class TestPhaseVocabulary(unittest.TestCase):
    def test_phase_vocabulary_closed(self):
        values = _phase_literals_from_server()
        self.assertTrue(values, "scan found no SERVER_STARTUP_PHASE assignments")
        self.assertIn("booting", values)  # sanity: scan really sees assignments
        allowed = set(lc.READY_PHASES) | set(lc.NONREADY_PHASES)
        self.assertLessEqual(values, allowed,
                             f"phase literals outside contract vocabulary: {values - allowed}")
        self.assertNotIn("starting", values)  # display-only, out of scope

    def test_stopping_gets_no_grace(self):
        assessment, got = verdict(payload(startup_phase="stopping"))
        self.assertFalse(assessment.booting)
        self.assertFalse(got[0])


class TestHugeIntBootAge(unittest.TestCase):
    """LOW-fix B-1: a boot_age_s integer too large for float() is an
    invalid age (None) per D1, never an OverflowError out of classify."""

    def test_huge_positive_int_boot_age_is_invalid_not_raised(self):
        assessment, got = verdict(payload(boot_age_s=10**400))
        self.assertEqual(assessment.state, lc.ALIVE_BOOTING)
        self.assertIsNone(assessment.boot_age_s)
        self.assertEqual(assessment.detail, "boot_age_invalid")
        self.assertEqual(got, (False, "invalid_boot_age"))

    def test_huge_negative_int_boot_age_is_invalid_not_raised(self):
        assessment, got = verdict(payload(boot_age_s=-(10**400)))
        self.assertEqual(assessment.state, lc.ALIVE_BOOTING)
        self.assertIsNone(assessment.boot_age_s)
        self.assertEqual(assessment.detail, "boot_age_invalid")
        self.assertEqual(got, (False, "invalid_boot_age"))


if __name__ == "__main__":
    unittest.main()
