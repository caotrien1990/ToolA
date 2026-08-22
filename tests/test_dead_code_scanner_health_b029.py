# -*- coding: utf-8 -*-
"""B029: the two dead-code scanners lost their findings and the payload never said so.

Three defects in one module, found by measuring `cbm_dead_code` scope=full on ToolA
and getting `count=0` from a repo with thousands of real lint hits.

(a) ANSI. `scan_ruff` never passed `--color never`, and the daemon inherits
    `FORCE_COLOR` from whatever launched it (measured =3). Ruff then paints its
    output even into a pipe, and `_RUFF_RE` matches none of it. Measured: 5803 lines
    emitted, `_parse_ruff_output` returned 0; with the variable stripped, 5801.
    This is the SAME defect A24 fixed for `ruff_fix` in `graph/tools.py` -- and the
    B018 lesson applies: that survey only proved things about the sites it
    enumerated, and this one was not in it. `--color never` wins over FORCE_COLOR;
    NO_COLOR does not, which is why the env is also scrubbed.

(b) Silence. A scanner that dies only calls `warnings.warn`, which reaches nobody
    over HTTP, while the payload keeps claiming `engine="vulture+ruff"` and
    `engine_reason="Vulture + Ruff both apply"` with `error=None`. Measured on the
    live daemon: `sources` was 100% ruff, vulture had contributed nothing, and no
    field said so. Same class as B028b -- a payload that misdescribes itself is a
    defect even when the fix elsewhere is correct.

(c) Timeout. `scan_vulture` ran with `timeout=30`; vulture needs 56s measured on
    ToolA full scope, so it timed out on every full run -- silently, per (b).

The engine string must now name what actually ran. Promising a scanner that
contributed nothing is the part that made a real `count=0` unreadable.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.graph.extractors import vulture_ruff as vr  # noqa: E402


class _Captured:
    """Records the argv/env/timeout a scanner would have used, and answers for it."""

    def __init__(self, stdout: str = "", returncode: int = 0, raises=None):
        self.stdout = stdout
        self.returncode = returncode
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")

    @property
    def argv(self) -> list[str]:
        return self.calls[-1]["argv"]

    @property
    def env(self) -> dict:
        return self.calls[-1].get("env") or {}

    @property
    def timeout(self):
        return self.calls[-1].get("timeout")


class RuffOutputSurvivesAColouredEnvironmentTest(unittest.TestCase):
    """(a) The parse must not depend on what env the daemon happened to inherit."""

    def test_color_never_is_passed(self):
        cap = _Captured()
        with unittest.mock.patch.object(vr.subprocess, "run", cap):
            vr.scan_ruff(str(ROOT))
        self.assertIn("--color", cap.argv)
        self.assertEqual(cap.argv[cap.argv.index("--color") + 1], "never")

    def test_force_color_is_stripped_from_the_child_env(self):
        cap = _Captured()
        with unittest.mock.patch.dict("os.environ", {"FORCE_COLOR": "3"}, clear=False), \
             unittest.mock.patch.object(vr.subprocess, "run", cap):
            vr.scan_ruff(str(ROOT))
        self.assertNotIn("FORCE_COLOR", cap.env)

    def test_the_child_env_is_not_emptied(self):
        """Scrubbing must be surgical: ruff still needs PATH and friends.

        Asserting merely that the dict is non-empty was too weak to catch the
        over-correction -- `env = {}` plus the NO_COLOR the scrubber adds is
        already truthy. A named inherited variable is the real check."""
        import os as _os
        cap = _Captured()
        with unittest.mock.patch.object(vr.subprocess, "run", cap):
            vr.scan_ruff(str(ROOT))
        inherited = [k for k in _os.environ
                     if k not in vr._COLOUR_ENV_VARS and k != "NO_COLOR"]
        self.assertTrue(inherited, "parent env has nothing to inherit; test is moot")
        missing = [k for k in inherited if k not in cap.env]
        self.assertEqual(missing, [],
                         "an emptied env would break the child, not fix it")

    def test_vulture_gets_the_same_clean_env(self):
        cap = _Captured()
        with unittest.mock.patch.dict("os.environ", {"FORCE_COLOR": "3"}, clear=False), \
             unittest.mock.patch.object(vr.subprocess, "run", cap):
            vr.scan_vulture(str(ROOT))
        self.assertNotIn("FORCE_COLOR", cap.env)

    def test_an_ansi_painted_line_still_parses(self):
        """Belt and braces: even if some future env paints anyway, do not return 0.

        This is a real line measured from ruff under FORCE_COLOR=3."""
        painted = ("\x1b[1m.council\\x.py\x1b[0m\x1b[36m:\x1b[0m13\x1b[36m:\x1b[0m1"
                   "\x1b[36m:\x1b[0m \x1b[1m\x1b[31mT201\x1b[0m `print` found")
        got = vr._parse_ruff_output(painted)
        self.assertEqual(len(got), 1, "ANSI must not cost the finding")
        self.assertEqual(got[0]["rule"], "T201")
        self.assertEqual(got[0]["line"], 13)


class AFailedScannerReachesThePayloadTest(unittest.TestCase):
    """(b) A scanner that contributed nothing must not be advertised as running."""

    def test_a_timeout_is_recorded_in_the_status(self):
        status: dict = {}
        cap = _Captured(raises=subprocess.TimeoutExpired(["vulture"], 30))
        with unittest.mock.patch.object(vr.subprocess, "run", cap):
            out = vr.scan_vulture(str(ROOT), status=status)
        self.assertEqual(out, [], "fail open: the other scanner still serves")
        self.assertFalse(status.get("ok"))
        self.assertEqual(status.get("scanner"), "vulture")
        self.assertIn("timeout", str(status.get("reason", "")).lower())

    def test_a_bad_exit_code_is_recorded(self):
        status: dict = {}
        cap = _Captured(stdout="", returncode=2)
        with unittest.mock.patch.object(vr.subprocess, "run", cap):
            vr.scan_ruff(str(ROOT), status=status)
        self.assertFalse(status.get("ok"))
        self.assertEqual(status.get("scanner"), "ruff")

    def test_a_successful_scan_records_ok(self):
        status: dict = {}
        cap = _Captured(stdout="", returncode=0)
        with unittest.mock.patch.object(vr.subprocess, "run", cap):
            vr.scan_ruff(str(ROOT), status=status)
        self.assertTrue(status.get("ok"))
        self.assertIsNone(status.get("reason"))

    def test_status_is_optional(self):
        """Every existing caller passes no status and must keep working."""
        cap = _Captured(stdout="", returncode=0)
        with unittest.mock.patch.object(vr.subprocess, "run", cap):
            self.assertEqual(vr.scan_ruff(str(ROOT)), [])


class TheEngineDoesNotPromiseWhatItDidNotRunTest(unittest.TestCase):
    """(b) at the payload layer, which is the only layer a caller can see."""

    def _dispatch(self, *, vulture_ok: bool, ruff_ok: bool) -> dict:
        from orchestrator.graph import tools as gt

        def fake_vulture(path, min_conf=60, files=None, status=None):
            if status is not None:
                status.update({"ok": vulture_ok, "scanner": "vulture",
                               "reason": None if vulture_ok else "timeout after 180s"})
            return []

        def fake_ruff(path, rules=None, files=None, status=None):
            if status is not None:
                status.update({"ok": ruff_ok, "scanner": "ruff",
                               "reason": None if ruff_ok else "exit 2"})
            return []

        with unittest.mock.patch.object(gt, "scan_vulture", fake_vulture), \
             unittest.mock.patch.object(gt, "scan_ruff", fake_ruff), \
             unittest.mock.patch.object(gt, "_detect_majority_language",
                                        lambda p: "python"):
            return gt._cbm_dead_code_dispatch(str(ROOT), limit=5, scope="core")

    def test_both_ok_keeps_the_full_engine_name(self):
        out = self._dispatch(vulture_ok=True, ruff_ok=True)
        self.assertEqual(out["engine"], "vulture+ruff")
        self.assertEqual(out.get("scanners_failed"), [])

    def test_a_dead_vulture_is_named_in_the_payload(self):
        out = self._dispatch(vulture_ok=False, ruff_ok=True)
        self.assertIn("vulture", [f.get("scanner") for f in out["scanners_failed"]])
        self.assertNotIn("vulture", out["engine"],
                         "the engine must not claim a scanner that contributed nothing")
        self.assertIn("ruff", out["engine"])
        self.assertIn("vulture", out["engine_reason"].lower())

    def test_a_dead_ruff_is_named_in_the_payload(self):
        out = self._dispatch(vulture_ok=True, ruff_ok=False)
        self.assertIn("ruff", [f.get("scanner") for f in out["scanners_failed"]])
        self.assertEqual(out["engine"], "vulture")

    def test_both_dead_says_so_and_sets_error(self):
        out = self._dispatch(vulture_ok=False, ruff_ok=False)
        self.assertEqual(len(out["scanners_failed"]), 2)
        self.assertIsNotNone(out["error"],
                             "count=0 with every scanner dead is an error, not a result")

    def test_the_failure_reason_is_carried_not_just_the_name(self):
        out = self._dispatch(vulture_ok=False, ruff_ok=True)
        failed = [f for f in out["scanners_failed"] if f.get("scanner") == "vulture"][0]
        self.assertTrue(failed.get("reason"), "a name with no reason is not diagnosable")


class VultureGetsLongEnoughToFinishTest(unittest.TestCase):
    """(c) 30s against a measured 56s need is a guaranteed silent timeout."""

    def test_the_default_timeout_clears_the_measured_need(self):
        cap = _Captured()
        with unittest.mock.patch.object(vr.subprocess, "run", cap):
            vr.scan_vulture(str(ROOT))
        self.assertIsNotNone(cap.timeout, "a timeout is still required")
        self.assertGreaterEqual(float(cap.timeout), 120.0,
                                "vulture measured 56s on ToolA full scope")

    def test_ruff_keeps_a_timeout_too(self):
        cap = _Captured()
        with unittest.mock.patch.object(vr.subprocess, "run", cap):
            vr.scan_ruff(str(ROOT))
        self.assertIsNotNone(cap.timeout, "no timeout at all would hang the daemon")

    def test_the_timeout_is_configurable(self):
        cap = _Captured()
        with unittest.mock.patch.object(vr, "_SCAN_TIMEOUT_S", 7), \
             unittest.mock.patch.object(vr.subprocess, "run", cap):
            vr.scan_vulture(str(ROOT))
        self.assertEqual(cap.timeout, 7)


if __name__ == "__main__":
    unittest.main()
