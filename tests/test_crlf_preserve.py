# -*- coding: utf-8 -*-
"""CRLF line-ending preservation (2026-07-17 upgrade).

WHY: write_fix used to read via read_text() (universal newlines), silently
rewriting a file's \\r\\n -> \\n on every edit. That corrupts .bat/.ps1 files
(which REQUIRE CRLF) — it broke Q_S.bat. These pin the fix: edits keep the
file's existing ending, new batch/PS files get CRLF, and LF files stay LF.
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from orchestrator.tools.write import write_fix, write_new_file  # noqa: E402


def run(coro):
    return json.loads(asyncio.run(coro))


def eols(fp):
    raw = Path(fp).read_bytes()
    crlf = raw.count(b"\r\n")
    lone_lf = raw.count(b"\n") - crlf
    return crlf, lone_lf


class CRLFCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a_crlf_")
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_fix_preserves_crlf_on_bat(self):
        fp = self.dir / "script.bat"
        fp.write_bytes(b"@echo off\r\necho old\r\nexit /b 0\r\n")  # CRLF
        res = run(write_fix(str(fp), 2, 2, "echo new\n"))          # LF new_content
        self.assertTrue(res.get("ok"), res)
        crlf, lone = eols(fp)
        self.assertGreater(crlf, 0)
        self.assertEqual(lone, 0)                                  # no lone LF introduced
        self.assertIn("echo new", fp.read_text(encoding="utf-8"))

    def test_write_new_file_forces_crlf_for_batch(self):
        for ext in ("bat", "cmd", "ps1"):
            fp = self.dir / f"new.{ext}"
            res = run(write_new_file(str(fp), "line1\nline2\n"))   # LF payload
            self.assertTrue(res.get("ok"), res)
            crlf, lone = eols(fp)
            self.assertGreater(crlf, 0, ext)
            self.assertEqual(lone, 0, ext)                         # all CRLF

    def test_lf_python_stays_lf(self):
        fp = self.dir / "m.py"
        fp.write_bytes(b"x = 1\ny = 2\n")                          # LF
        res = run(write_fix(str(fp), 1, 1, "x = 99\n"))
        self.assertTrue(res.get("ok"), res)
        crlf, lone = eols(fp)
        self.assertEqual(crlf, 0)                                  # stays LF (no regression)
        self.assertGreater(lone, 0)

    def test_new_py_stays_lf(self):
        fp = self.dir / "n.py"
        res = run(write_new_file(str(fp), "a = 1\nb = 2\n"))       # LF payload, .py
        self.assertTrue(res.get("ok"), res)
        crlf, _lone = eols(fp)
        self.assertEqual(crlf, 0)                                  # .py NOT forced to CRLF


if __name__ == "__main__":
    unittest.main()
