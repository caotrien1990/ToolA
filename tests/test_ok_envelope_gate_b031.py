"""B031: an ENFORCED `ok` envelope gate, plus the regex-timeout budget disclosure.

Why a gate and not a third fix. B018 added `ok` to search_code and recorded in a
comment that every other tool already answered `ok`. B024 found cache_status was
not in that survey and fixed it, repeating the same claim. B031 then found TWO
more live sites in the same function family -- find_symbol's live-filesystem
success path and its path-not-found branch -- plus an orphan `INVALID_REGEX`
spelling that no taxonomy, test or remediation chain knew about. Three surveys,
three misses: a one-off read cannot hold this class shut, so it is a test now.

SCOPE, stated out loud because overstating it is exactly what failed twice: this
gate scans the two modules of the find_symbol / search_code family where B018,
B024 and B031 all landed. It does NOT prove the whole repo answers `ok`. Adding
modules to SCANNED is the follow-up; assuming they are clean is the defect.

The second half pins B031's other half: REGEX_SEARCH_TIMEOUT used to print one
number ("hard deadline of 15.000s") for a call that had really run two attempts
under two different deadlines with input and match phases budgeted separately --
measured 26.60 / 26.64 / 26.74 / 26.81 / 27.73s of wall clock. And `timeout_phase`
was stamped on 1 of the 3 shapes that carry this error_name while having zero
test coverage anywhere in tests/, which is how ISS-20 shipped it half-done.
"""
from __future__ import annotations

import ast
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator import search_regex_worker
from orchestrator.tools import search as search_module

REPO_ROOT = Path(__file__).resolve().parents[1]

SCANNED = (
    "orchestrator/tools/search.py",
    "orchestrator/tools/read/fs.py",
)


def _literal_json_envelopes(src: str) -> list[tuple[int, list[str]]]:
    """Every `return json.dumps({<literal dict>})` in src, as (lineno, keys).

    A dict built elsewhere and returned by name is deliberately skipped: the
    static half cannot follow it, and saying so here is cheaper than a gate that
    quietly matches nothing. The runtime half below covers those paths.
    """
    found: list[tuple[int, list[str]]] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if not (isinstance(func, ast.Attribute) and func.attr == "dumps"):
            continue
        if not node.value.args or not isinstance(node.value.args[0], ast.Dict):
            continue
        keys = [k.value for k in node.value.args[0].keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        found.append((node.lineno, keys))
    return found


class TestOkEnvelopeStaticGate(unittest.TestCase):
    def test_scanner_detects_a_missing_ok(self):
        # Positive control FIRST. A scanner that silently matches nothing passes
        # forever and reads as proof of a clean repo -- the measured A40 trap.
        hits = _literal_json_envelopes(
            "import json\ndef f():\n    return json.dumps({'error': 'x'})\n")
        self.assertEqual(1, len(hits), "scanner failed to see an obvious envelope")
        self.assertNotIn("ok", hits[0][1])
        ok_hits = _literal_json_envelopes(
            "import json\ndef g():\n    return json.dumps({'ok': True})\n")
        self.assertIn("ok", ok_hits[0][1])

    def test_scanned_modules_have_no_ok_less_literal_envelope(self):
        offenders: list[str] = []
        scanned_total = 0
        for rel in SCANNED:
            target = REPO_ROOT / rel
            self.assertTrue(target.exists(), f"SCANNED names a missing module: {rel}")
            hits = _literal_json_envelopes(target.read_text(encoding="utf-8"))
            scanned_total += len(hits)
            offenders.extend(f"{rel}:{lineno} keys={keys}"
                             for lineno, keys in hits if "ok" not in keys)
        # Guard the guard: if the parse ever yields nothing, the assertion below
        # is vacuously true and the gate is dead weight.
        self.assertGreater(scanned_total, 5,
                           "too few envelopes found -- the scan is not reaching the code")
        self.assertEqual([], offenders,
                         "json.dumps envelope without `ok`: a caller guard written as "
                         "`if not resp.get('ok')` reads it as a failure (or, on a "
                         "success path, reads success as failure)")


class TestFindSymbolRuntimeEnvelope(unittest.TestCase):
    """The two live B031 sites, exercised end to end rather than read."""

    def test_live_scanner_success_answers_ok_true(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "probe_mod.py").write_text(
                "def probe_target_b031():\n    return 1\n", encoding="utf-8")
            raw = asyncio.run(search_module.find_symbol(
                name="probe_target_b031", project_path=str(root)))
        body = json.loads(raw)
        self.assertIn("ok", body, "a SUCCESSFUL find_symbol still read as a failure")
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(body.get("count", 0), 1)

    def test_path_not_found_answers_ok_false_and_a_registered_error_name(self):
        with tempfile.TemporaryDirectory() as td:
            missing = str(Path(td) / "absent_dir_b031")
            raw = asyncio.run(search_module.find_symbol(name="x", project_path=missing))
        body = json.loads(raw)
        self.assertIs(False, body.get("ok"))
        # orchestrator_server registers a ("find_symbol", "PATH_NOT_FOUND") chain;
        # with no error_name that chain could never fire.
        self.assertEqual("PATH_NOT_FOUND", body.get("error_name"))

    def test_missing_location_still_answers_ok(self):
        body = json.loads(asyncio.run(search_module.find_symbol(name="x")))
        self.assertIn("ok", body)
        self.assertEqual("MISSING_REQUIRED_FIELD", body.get("error_name"))


class _PhaseSource:
    def __init__(self, phase: str = "", files: int = 0, byts: int = 0):
        self.timeout_phase = phase
        self.input_files_read = files
        self.input_bytes_read = byts


class TestRegexTimeoutBudgetDisclosure(unittest.TestCase):
    @staticmethod
    def _payload(final, attempts, initial):
        return search_module._regex_timeout_payload(
            "a+b", final, "", "C:/proj", "*.py",
            attempts=attempts, initial_deadline=initial)

    def test_one_attempt_states_the_two_phase_budget(self):
        body = self._payload(8.0, 1, 8.0)
        self.assertEqual(16.0, body["wall_clock_budget_seconds"])
        self.assertIn("16.0", body["error"])
        self.assertIn("worst case", body["error"])

    def test_retried_call_names_both_deadlines_and_the_real_budget(self):
        body = self._payload(15.0, 2, 8.0)
        self.assertEqual(46.0, body["wall_clock_budget_seconds"])
        for fragment in ("2 attempts", "8.000s", "15.000s", "46.0", "26.6-27.7s"):
            self.assertIn(fragment, body["error"])
        self.assertNotEqual("regex search exceeded hard deadline of 15.000s",
                            body["error"])

    def test_unknown_budget_invents_no_number(self):
        # The honest third branch: with no deadline known there is no budget to
        # state, and a plausible-looking number here would be worse than silence.
        body = self._payload(None, 1, None)
        self.assertIsNone(body["wall_clock_budget_seconds"],
                          "a wall-clock budget was invented for an unknown deadline")
        self.assertIn("hard deadline of default", body["error"])
        self.assertNotIn("worst case", body["error"])

    def test_all_three_timeout_shapes_state_a_phase(self):
        match_shape = search_module._attach_regex_input_phase({}, _PhaseSource("match"))
        self.assertEqual("match", match_shape["timeout_phase"])
        self.assertNotIn("timeout_phase_unknown_reason", match_shape)

        blind_shape = search_module._attach_regex_input_phase({}, None)
        self.assertEqual("unknown", blind_shape["timeout_phase"])
        self.assertIn("timeout_phase_unknown_reason", blind_shape)

        input_shape = search_module._attach_regex_input_phase(
            {}, _PhaseSource("input", 12, 3456))
        self.assertEqual("input", input_shape["timeout_phase"])
        self.assertEqual(12, input_shape["input_files_read"])
        self.assertEqual(3456, input_shape["input_bytes_read"])
        self.assertIn("matching never started", input_shape["error"])
        self.assertEqual("narrow_file_scope_or_raise_regex_deadline",
                         input_shape["ca_next_action"])


class TestRegexDeadlineSchemaTracksCode(unittest.TestCase):
    """The published default/range had drifted BOTH ways from the constants."""

    def test_registry_publishes_the_real_default_and_ceiling(self):
        from orchestrator.core import registry_data

        source = Path(registry_data.__file__).read_text(encoding="utf-8")
        published = [line for line in source.splitlines()
                     if "regex_deadline_seconds" in line and "description" in line]
        self.assertEqual(1, len(published),
                         "more than one publishing site -- they will drift apart")
        line = published[0]
        self.assertIn(f"\"default\": {search_regex_worker.DEFAULT_DEADLINE_SECONDS}", line)
        self.assertIn(str(search_regex_worker.MAX_DEADLINE_SECONDS), line)
        self.assertIn(str(search_regex_worker.MIN_DEADLINE_SECONDS), line)
        self.assertNotIn("0.05-10", line)

    def test_normalize_clamps_to_the_published_bounds(self):
        self.assertEqual(search_regex_worker.MAX_DEADLINE_SECONDS,
                         search_regex_worker.normalize_deadline_seconds(9999.0))
        self.assertEqual(search_regex_worker.MIN_DEADLINE_SECONDS,
                         search_regex_worker.normalize_deadline_seconds(0.0001))
        self.assertEqual(search_regex_worker.DEFAULT_DEADLINE_SECONDS,
                         search_regex_worker.normalize_deadline_seconds(None))


if __name__ == "__main__":
    unittest.main()
