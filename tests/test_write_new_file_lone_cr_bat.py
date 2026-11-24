# -*- coding: utf-8 -*-
"""write_new_file bat/cmd/ps1 lone-CR regression (2026-11-24, task CORE2214).

WHY: the bat/cmd/ps1 branch forces CRLF via
    new_content.replace('\r\n','\n').replace('\n','\r\n')
which folds CRLF and LF but leaves a lone '\r' untouched. A payload line
ending in a bare '\r' (a caller that split a CRLF file on '\n' keeps a
trailing '\r' per line; mixed-ending editor buffers do the same) is written
to disk with a bare CR: cmd/PowerShell see a merged or unpredictably split
line. Same rootcause as the write_fix merge bug fixed at core.py:1705
(commit adda45a); this pins the sibling site in write_new_file (core.py:2218).

These pin the fix: every line end in a bat/cmd/ps1 payload becomes CRLF and
no lone CR survives. Controls pin that pure-LF/pure-CRLF payloads keep
working, that other extensions are NOT touched by this branch, and that no
trailing EOL is appended to an unterminated last line (existing
write_new_file behavior, unchanged by this fix).
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from orchestrator.tools.write import write_new_file  # noqa: E402
from code_tools import generate_project_map  # noqa: E402


def run(coro):
    return json.loads(asyncio.run(coro))


def lone_cr(fp):
    raw = Path(fp).read_bytes()
    return raw.count(b"\r") - raw.count(b"\r\n")


class LoneCrBatCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a_lonecr_bat_")
        self.dir = Path(self._tmp.name)
        (self.dir / "_seed.py").write_text("seed = 1\n", encoding="utf-8")
        run(generate_project_map(str(self.dir)))

    def tearDown(self):
        self._tmp.cleanup()

    def _new(self, name, content):
        fp = self.dir / name
        res = run(write_new_file(path=str(fp), content=content,
                                 project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        return fp

    def test_bat_internal_lone_cr_becomes_crlf(self):
        fp = self._new("t.bat", "echo a\recho b\r\n")
        self.assertEqual(fp.read_bytes(), b"echo a\r\necho b\r\n")
        self.assertEqual(lone_cr(fp), 0)

    def test_ps1_internal_lone_cr_becomes_crlf(self):
        fp = self._new("t.ps1", "Write-Host a\rWrite-Host b\n")
        self.assertEqual(fp.read_bytes(), b"Write-Host a\r\nWrite-Host b\r\n")
        self.assertEqual(lone_cr(fp), 0)

    def test_cmd_mixed_endings_trailing_lone_cr(self):
        fp = self._new("t.cmd", "a\r\nb\rc\r")
        self.assertEqual(fp.read_bytes(), b"a\r\nb\r\nc\r\n")
        self.assertEqual(lone_cr(fp), 0)

    def test_control_bat_pure_lf_still_forced_crlf(self):
        fp = self._new("c1.bat", "a\nb\n")
        self.assertEqual(fp.read_bytes(), b"a\r\nb\r\n")

    def test_control_bat_pure_crlf_unchanged(self):
        fp = self._new("c2.bat", "a\r\nb\r\n")
        self.assertEqual(fp.read_bytes(), b"a\r\nb\r\n")

    def test_control_txt_lone_cr_not_touched_by_bat_branch(self):
        fp = self._new("c3.txt", "a\rb\n")
        self.assertEqual(fp.read_bytes(), b"a\rb\n")

    def test_control_bat_unterminated_last_line_no_eol_appended(self):
        fp = self._new("c4.bat", "a\nb")
        self.assertEqual(fp.read_bytes(), b"a\r\nb")


if __name__ == "__main__":
    unittest.main()
