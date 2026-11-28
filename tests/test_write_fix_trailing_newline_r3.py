"""R3 (2026-11-28): write_fix preserves the file's original trailing-newline state.

WHY: measured live (muse1 + ClauLTN), a 5-byte file "x = 1" (no trailing NL)
became 6 bytes after write_fix line 1 -> "x = 2\n". The splice always
terminates the inserted last line (the lone-CR merge contract needs that for
MID-file edits), which silently added a final newline at EOF.

Mutants killed:
- removing the EOF strip: no-NL file gains a newline again (the measured bug)
- stripping unconditionally: files that HAD a final newline lose it, and
  mid-file splices merge lines (the lone-CR campaign regression)
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from orchestrator.tools.write import write_fix  # noqa: E402
from code_tools import generate_project_map  # noqa: E402


def run(coro):
    return json.loads(asyncio.run(coro))


class TrailingNewlineCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="r3_nl_")
        self.dir = Path(self._tmp.name)
        (self.dir / "_seed.py").write_text("seed = 1\n", encoding="utf-8")
        run(generate_project_map(str(self.dir)))

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_newline_file_stays_without_newline(self):
        fp = self.dir / "t.txt"
        fp.write_bytes(b"x = 1")  # 5 bytes, last byte '1'
        res = run(write_fix(str(fp), 1, 1, "x = 2", project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(fp.read_bytes(), b"x = 2")  # still 5 bytes
        self.assertEqual(fp.read_bytes()[-1:], b"2")

    def test_newline_file_keeps_newline(self):
        fp = self.dir / "t.txt"
        fp.write_bytes(b"x = 1\n")
        res = run(write_fix(str(fp), 1, 1, "x = 2", project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(fp.read_bytes(), b"x = 2\n")

    def test_crlf_no_newline_file_stays_without_newline(self):
        fp = self.dir / "t.txt"
        fp.write_bytes(b"a\r\nb")  # CRLF file, no final terminator
        res = run(write_fix(str(fp), 2, 2, "B", project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(fp.read_bytes(), b"a\r\nB")

    def test_midfile_splice_still_terminates_inserted_line(self):
        # The lone-CR merge contract: a no-NL payload replacing a MID line
        # must still get the file's EOL so it cannot merge with the next line.
        fp = self.dir / "t.txt"
        fp.write_bytes(b"a\nb\nc")  # no final NL, but edit is mid-file
        res = run(write_fix(str(fp), 2, 2, "B", project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(fp.read_bytes(), b"a\nB\nc")

    def test_multiline_replacement_at_eof_no_newline(self):
        fp = self.dir / "t.txt"
        fp.write_bytes(b"a\nb")
        res = run(write_fix(str(fp), 2, 2, "B1\nB2", project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(fp.read_bytes(), b"a\nB1\nB2")


if __name__ == "__main__":
    unittest.main()