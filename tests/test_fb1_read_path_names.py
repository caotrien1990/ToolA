"""F-B1: read_file_range must name outside-root rejections, not INTERNAL_ERROR.

Root cause (converged): the guard raises a bare ValueError; handler
read_file_range (orchestrator/tools/read/context.py:145-157) caught it and
returned fo.json_error with NO error_name at either return site, so the
json_error setdefault stamped INTERNAL_ERROR.

Decided contract (DECISION_FB1_CONTRACT.md; PLAN.md Finding F-B1):

| Caught exception | error_name | type | retryable | ca_next_action |
| ValueError, "outside project"/"escapes project root" | PATH_OUTSIDE_PROJECT | PERMISSION_POLICY | false (default) | default |
| ValueError, "unc" | UNC_PATH_UNSUPPORTED | INVALID_REQUEST | false (default) | default |
| ValueError, neither predicate | PATH_RESOLVE_FAILED | INVALID_REQUEST | false (default) | default |
| Non-ValueError | INTERNAL_ERROR (default) | INTERNAL_ERROR (default) | false (default) | default |

Message text and hint stay byte-identical in all rows. Classification is
gated on isinstance(exc, ValueError), lowercases once, tests
outside/escapes BEFORE unc, and is one shared computation consumed at both
returns; the default row passes NO error_name/type kwargs (None is
forbidden: json_error has no None-filtering).

Invocation route (PLAN.md SS Tests): the handler is async and returns a JSON
string; drive it with json.loads(asyncio.run(code_tools.read_file_range(...)))
(the tests/test_reseed_failure_reason_p11b.py:39-40 pattern). The guard is
monkeypatched at orchestrator.fileops.common.resolve_project_file (the module
object context.py binds as `fo`). No parametrization, no subTest.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import code_tools
from orchestrator.fileops import common as fo_common
from orchestrator.tools.write import core as write_core

HINT = "Use project_path with a relative file_path inside the project."
DEFAULT_ACTION = "inspect_error_context_or_report_backend_issue"

MSG_UNC_PROJECT = "UNC project_path is unsupported for project guard"
MSG_UNC_FILE = "UNC file_path with project_path cannot be guarded relative to root"
MSG_OUTSIDE = "File path 'C:\\outside\\x.py' is outside project root 'C:\\proj'."
MSG_ESCAPES = "File path escapes project root: ..\\..\\x.py"
MSG_STRAY = "synthetic stray resolve failure 7f3a"
MSG_BOTH = "File path 'x' is outside project root 'y'. UNC path involved"

ABS_PATH = "C:\\fb1-absolute-probe\\probe.py"
PROJECT = str(Path(__file__).resolve().parents[1])


def _read(file_path, project_path, **kwargs):
    return json.loads(asyncio.run(code_tools.read_file_range(
        file_path, start_line=1, end_line=None, project_path=project_path, **kwargs)))


def _assert_six(test, resp, *, error_name, error_text, type_):
    """All six contract fields of one error row."""
    test.assertFalse(resp.get("ok"), msg=resp)
    test.assertEqual(resp.get("error_name"), error_name, msg=resp)
    test.assertEqual(resp.get("error"), error_text, msg=resp)
    test.assertEqual(resp.get("type"), type_, msg=resp)
    test.assertIs(resp.get("retryable"), False, msg=resp)
    test.assertEqual(resp.get("ca_next_action"), DEFAULT_ACTION, msg=resp)
    test.assertEqual(resp.get("hint"), HINT, msg=resp)


def _guard_raising(exc):
    return mock.patch.object(fo_common, "resolve_project_file", side_effect=exc)


class ParityTests(unittest.TestCase):
    def test_t1_handler_agrees_with_write_mapper_on_name_and_type(self):
        # Parity is on (name, type) ONLY: the write mapper's two PREDICATE
        # branches stamp retryable=True + a write action (its else branch
        # passes name + type only, the rest falling to _json_error
        # defaults), while the read side keeps retryable=false + the
        # default action per the decided contract. Feed RAW guard
        # messages (uppercase UNC intact); lowercasing happens INSIDE
        # each classifier. Never pre-lowercase inputs.
        messages = [MSG_UNC_PROJECT, MSG_UNC_FILE, MSG_OUTSIDE,
                    MSG_ESCAPES, MSG_STRAY, MSG_BOTH]
        for message in messages:
            with _guard_raising(ValueError(message)):
                got = _read(ABS_PATH, PROJECT)
            want = json.loads(write_core._json_path_resolve_error(
                code_tools, ValueError(message)))
            self.assertEqual(
                (got.get("error_name"), got.get("type")),
                (want.get("error_name"), want.get("type")),
                msg="message=%r handler=%r mapper=%r" % (message, got, want))


class MirrorTests(unittest.TestCase):
    def test_t2_row1_outside_project(self):
        with _guard_raising(ValueError(MSG_OUTSIDE)):
            resp = _read(ABS_PATH, PROJECT)
        _assert_six(self, resp, error_name="PATH_OUTSIDE_PROJECT",
                    error_text=MSG_OUTSIDE, type_="PERMISSION_POLICY")

    def test_t2_row2_unc(self):
        with _guard_raising(ValueError(MSG_UNC_FILE)):
            resp = _read(ABS_PATH, PROJECT)
        _assert_six(self, resp, error_name="UNC_PATH_UNSUPPORTED",
                    error_text=MSG_UNC_FILE, type_="INVALID_REQUEST")

    def test_t2_row3_else_branch(self):
        with _guard_raising(ValueError(MSG_STRAY)):
            resp = _read(ABS_PATH, PROJECT)
        _assert_six(self, resp, error_name="PATH_RESOLVE_FAILED",
                    error_text=MSG_STRAY, type_="INVALID_REQUEST")

    def test_t2_row4_non_value_error_keeps_default(self):
        with _guard_raising(RuntimeError(MSG_OUTSIDE)):
            resp = _read(ABS_PATH, PROJECT)
        _assert_six(self, resp, error_name="INTERNAL_ERROR",
                    error_text=MSG_OUTSIDE, type_="INTERNAL_ERROR")


class Site153Tests(unittest.TestCase):
    # :153-site test (T2b). Relative file_path containing NUL + project_path
    # set + mocked guard: the fallback re-join raises on the NUL, so
    # execution reaches the :153 fallback-failure return, which returns the
    # ORIGINAL (mocked) message. UNIT-ONLY: the HTTP boundary rejects
    # control chars, so no live :153 probe exists.
    NUL_PATH = "a\x00b"

    def _check(self, message, error_name, type_):
        with _guard_raising(ValueError(message)):
            resp = _read(self.NUL_PATH, PROJECT)
        _assert_six(self, resp, error_name=error_name,
                    error_text=message, type_=type_)

    def test_t2b_site153_outside(self):
        self._check(MSG_OUTSIDE, "PATH_OUTSIDE_PROJECT", "PERMISSION_POLICY")

    def test_t2b_site153_unc(self):
        self._check(MSG_UNC_FILE, "UNC_PATH_UNSUPPORTED", "INVALID_REQUEST")

    def test_t2b_site153_else_branch(self):
        self._check(MSG_STRAY, "PATH_RESOLVE_FAILED", "INVALID_REQUEST")


class GatingTests(unittest.TestCase):
    def test_t3_non_value_error_with_path_like_message_keeps_default(self):
        # A RuntimeError whose message embeds BOTH predicates must still
        # keep the INTERNAL_ERROR default: classification is gated on
        # isinstance(exc, ValueError).
        message = "UNC path 'x' is outside project root 'y'"
        with _guard_raising(RuntimeError(message)):
            resp = _read(ABS_PATH, PROJECT)
        _assert_six(self, resp, error_name="INTERNAL_ERROR",
                    error_text=message, type_="INTERNAL_ERROR")


_MISSING = object()


class FallbackKeepTests(unittest.TestCase):
    def test_t4_fallback_pin_unchanged(self):
        # Pins the Step 0a measurement UNCHANGED post-fix under the Step 0a
        # COMPARISON RULE (field-wise over the stable field sets, .get()
        # absence semantics, missing-file branch predicate). Pinned values
        # copied as literals from exec-fb1/records/fallback-pin.md; no
        # pin/fixture file lives inside the repo. Byte-identity is
        # FORBIDDEN as a criterion here (fall-through responses are
        # stateful by construction).
        temp = os.environ.get("TEMP") or tempfile.gettempdir()
        fixture = Path(temp) / "fb1-fallback-fixture"
        marker = fixture / "marker.txt"
        if marker.is_file():
            content = marker.read_text(encoding="utf-8")
            self.assertEqual(content, "fb1 fallback fixture marker\n",
                             msg="fixture marker content changed: %r" % content)
        else:
            fixture.mkdir(parents=True, exist_ok=True)
            marker.write_text("fb1 fallback fixture marker\n", encoding="utf-8")
        file_path = "..\\fb1-fallback-missing-20261112.txt"
        target = (fixture / file_path).resolve()
        self.assertFalse(target.exists(),
                         msg="escaped target must stay absent: %s" % target)
        resp = _read(file_path, str(fixture))
        expected = {
            "ok": False,
            "error": "File not found: ..\\fb1-fallback-missing-20261112.txt",
            "error_name": "FILE_NOT_FOUND",
            "type": _MISSING,
            "retryable": _MISSING,
            "ca_next_action": "check_fuzzy_suggestions_or_list_structure",
            "hint": _MISSING,
        }
        if resp.get("probe_count") == 1 and "circuit_breaker" not in resp:
            for field, want in expected.items():
                self.assertEqual(resp.get(field, _MISSING), want,
                                 msg="field %s: %r" % (field, resp))
        else:
            # Reduced comparison per the missing-file branch predicate.
            self.assertEqual(resp.get("error_name"), "FILE_NOT_FOUND", msg=resp)
            self.assertEqual(resp.get("error"), expected["error"], msg=resp)


class ControlTests(unittest.TestCase):
    # T5 controls: all five legs are BOTH unit-tested here AND live-probed
    # in P-C. Tmp project fixtures under pytest tmp paths (tempfile
    # mkdtemp under %TEMP%); no production path is written.
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fb1_t5_"))
        (self.tmp / "mod.py").write_text("print(1)\n", encoding="utf-8")
        (self.tmp / "subdir").mkdir()
        (self.tmp / "blob.bin").write_bytes(b"\x00\xff" * 256)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_t5_in_root_read_ok(self):
        resp = _read("mod.py", str(self.tmp))
        self.assertTrue(resp.get("ok"), msg=resp)

    def test_t5_file_not_found(self):
        resp = _read("nope-missing-xyz.py", str(self.tmp))
        self.assertFalse(resp.get("ok"), msg=resp)
        self.assertEqual(resp.get("error_name"), "FILE_NOT_FOUND", msg=resp)
        self.assertEqual(resp.get("error"), "File not found: nope-missing-xyz.py",
                         msg=resp)

    def test_t5_directory(self):
        resp = _read("subdir", str(self.tmp))
        self.assertFalse(resp.get("ok"), msg=resp)
        self.assertEqual(resp.get("error_name"), "PATH_IS_DIRECTORY", msg=resp)
        self.assertEqual(
            resp.get("error"),
            "read_file_range requires a file, but this path is a directory: subdir",
            msg=resp)

    def test_t5_binary(self):
        resp = _read("blob.bin", str(self.tmp))
        self.assertFalse(resp.get("ok"), msg=resp)
        self.assertEqual(resp.get("error_name"), "BINARY_FILE_UNSUPPORTED", msg=resp)
        self.assertEqual(resp.get("error"),
                         "Binary file unsupported by read_file_range: blob.bin",
                         msg=resp)

    def test_t5_ranges_gate(self):
        # Named ranges-gate input (same for T5 and P-C): ranges=[] with
        # default legacy params against an existing in-root file. Pair
        # recorded from the pre-fix run at Step 0: (RANGES_EMPTY,
        # "ranges[] must contain at least one range.") with
        # ORCHESTRATOR_READ_RANGES_ENABLED unset (ranges ON). Post-fix /
        # live must reproduce the recorded pair exactly; a flag-state
        # difference is adjudicated, never silently absorbed.
        flag = (os.environ.get("ORCHESTRATOR_READ_RANGES_ENABLED") or "").strip().lower()
        self.assertNotIn(flag, ("0", "false"),
                         msg="ranges flag state differs from the Step 0 "
                             "recording (unset/ON): %r" % flag)
        resp = _read("mod.py", str(self.tmp), ranges=[])
        self.assertFalse(resp.get("ok"), msg=resp)
        self.assertEqual(resp.get("error_name"), "RANGES_EMPTY", msg=resp)
        self.assertEqual(resp.get("error"),
                         "ranges[] must contain at least one range.", msg=resp)


if __name__ == "__main__":
    unittest.main()
