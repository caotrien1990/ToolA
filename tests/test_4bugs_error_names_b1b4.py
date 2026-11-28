"""B1-B4 (2026-11-28): error names match the fact; non-ASCII validator errors survive.

WHY this file exists
--------------------
Four measured defects (sessions.db ids in the fix prompt):
- B1 write_fix on a missing file answered error_name=TOOL_ERROR while the message
  said "File not found" (event 3463640). The dispatcher stamps TOOL_ERROR on an
  unnamed error; the write path must NAME it FILE_NOT_FOUND.
- B3 validate_file on a missing file answered VALIDATION_FAILED / "fix syntax"
  (event 3464146) for a file that was never read. Same naming defect.
- B4 node --check spawned with text=True and NO encoding: on Windows the stderr
  decode used cp1252; a Vietnamese line in the Node error raised inside
  subprocess, stderr became None, and .strip() crashed -- the auto-revert reason
  became "'NoneType' object has no attribute 'strip'" instead of the real
  SyntaxError (events 3463728/3463781/3463789/3464195 on monitor.html).
- B2 regex input-phase timeout already carried files/bytes counters; it must also
  state the match outcome (zero, matching never started) so an empty answer is
  not misread as "no matches in the files read" (event 3463707).

Each test kills the measured mutant:
- B1/B3: dropping error_name (dispatcher TOOL_ERROR fallback) or keeping
  VALIDATION_FAILED on the missing-file branch.
- B4: removing encoding="utf-8", errors="replace" from the node spawn (the
  cp1252 decode crash) or removing the (r.stderr or "") guard.
- B2: dropping the input-phase match-outcome disclosure.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

import code_tools
from orchestrator.tools import search as search_module
from orchestrator.tools.write import core as write_core


def _run(coro):
    return json.loads(asyncio.run(coro))


# ---- B1: write_fix missing file ------------------------------------------------

def test_write_fix_missing_file_names_file_not_found(tmp_path):
    out = _run(write_core.write_fix(str(tmp_path / "nope-missing-b1.py"), 1, 1, "x\n"))
    assert out.get("ok") is not True
    assert out.get("error_name") == "FILE_NOT_FOUND", out
    assert "File not found" in out.get("error", "")


def test_write_fix_html_missing_file_names_file_not_found(tmp_path):
    out = _run(code_tools.write_fix_html(str(tmp_path / "nope-missing-b1.html"), 1, 1, "x\n"))
    assert out.get("ok") is False
    assert out.get("error_name") == "FILE_NOT_FOUND", out
    assert "File not found" in out.get("error", "")


# ---- B3: validate_file missing file --------------------------------------------

def test_validate_file_missing_file_names_file_not_found(tmp_path):
    out = _run(code_tools.validate_file(str(tmp_path / "nope-missing-b3.html")))
    assert out.get("ok") is False
    assert out.get("error_name") == "FILE_NOT_FOUND", out
    assert out.get("ca_next_action") == "check_file_path", out
    assert "File not found" in out.get("error", "")


# ---- B4: non-ASCII Node syntax error survives ----------------------------------

VN_BAD_JS = "var x = ; // d\u00f2ng ti\u1ebfng Vi\u1ec7t: \u0111\u00e2y l\u00e0 l\u1ed7i c\u00fa ph\u00e1p\n"


def test_js_validator_keeps_real_error_on_vietnamese_line(tmp_path):
    fp = tmp_path / "vn_bad.js"
    fp.write_text(VN_BAD_JS, encoding="utf-8")
    out = _run(code_tools.validate_file(str(fp), lang="js"))
    assert out.get("ok") is False, out
    err = out.get("error", "")
    assert "NoneType" not in err, f"the cp1252-decode crash is back: {err!r}"
    assert "SyntaxError" in err, f"real Node error lost: {err!r}"


def test_html_validator_keeps_real_error_on_vietnamese_line(tmp_path):
    fp = tmp_path / "vn_bad.html"
    fp.write_text(
        "<!doctype html><html><body><script>\n" + VN_BAD_JS + "</script></body></html>\n",
        encoding="utf-8")
    out = _run(code_tools.validate_file(str(fp), lang="html"))
    assert out.get("ok") is False, out
    err = out.get("error", "")
    assert "NoneType" not in err, f"the cp1252-decode crash is back: {err!r}"
    assert "SyntaxError" in err, f"real Node error lost: {err!r}"


def test_js_validator_stderr_none_guard(tmp_path, monkeypatch):
    # Even if a future spawn shape loses stderr entirely, the branch must not
    # crash with NoneType -- it must still report a failure (fail loud, no crash).
    fp = tmp_path / "guard.js"
    fp.write_text("var x = 1;\n", encoding="utf-8")
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        r = real_run(cmd, *a, **kw)
        if isinstance(cmd, list) and cmd and "node" in str(cmd[0]):
            class _R:
                returncode = 1
                stdout = r.stdout
                stderr = None
            return _R()
        return r

    monkeypatch.setattr(code_tools.subprocess, "run", fake_run)
    out = _run(code_tools.validate_file(str(fp), lang="js"))
    assert out.get("ok") is False, out
    assert "NoneType" not in out.get("error", ""), out


# ---- B2: input-phase timeout states the match outcome ---------------------------

class _InputPhaseSource:
    timeout_phase = "input"
    input_files_read = 38
    input_bytes_read = 1954957


def test_input_phase_timeout_states_zero_matches_and_reason():
    resp = search_module._attach_regex_input_phase({}, _InputPhaseSource())
    assert resp["timeout_phase"] == "input"
    assert resp["input_files_read"] == 38
    assert resp["input_bytes_read"] == 1954957
    assert "matching never started" in resp["error"]
    assert resp["input_phase_matches_found"] == 0
    assert "NOT evidence" in resp["input_phase_matches_reason"]
    # ISS-12 ownership: an input-phase death must NOT dress up partial results.
    assert "partial" not in resp and "results" not in resp