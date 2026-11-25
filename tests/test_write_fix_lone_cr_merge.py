# -*- coding: utf-8 -*-
"""write_fix lone-CR merge regression (2026-11-24 non-prod stdin campaign).

WHY: a caller that reads a CRLF file by splitting on "\n" holds lines with a
trailing bare "\r". Sending such a line back as new_content made write_fix
splice it in WITHOUT the file's "\n": the edited line ended in a lone "\r" and
merged with the next physical line (22 of 34 files in the measured campaign).
Python still parsed the result (\r is whitespace), so syntax validation passed
and the damage was invisible until git diff showed whole-file rewrites.

Rootcause: _build() normalized only "\r\n" -> "\n" in new_content, leaving a
lone "\r" as a complete line end (split_lines), and the unterminated-last-line
guard then skipped appending the file's _eol.

These pin the fix: a lone "\r" in the payload is a line END, normalized to the
file's dominant ending; no merge, no lone CR left behind. .txt targets keep
syntax validation out of the measurement (the bug is EOL mechanics).
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

from orchestrator.tools.write import write_fix  # noqa: E402
from orchestrator.text_lines import split_lines  # noqa: E402
from code_tools import generate_project_map  # noqa: E402


def run(coro):
    return json.loads(asyncio.run(coro))


def lone_cr(fp):
    raw = Path(fp).read_bytes()
    return raw.count(b"\r") - raw.count(b"\r\n")


class LoneCrMergeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a_lonecr_")
        self.dir = Path(self._tmp.name)
        (self.dir / "_seed.py").write_text("seed = 1\n", encoding="utf-8")
        run(generate_project_map(str(self.dir)))

    def tearDown(self):
        self._tmp.cleanup()

    def test_crlf_payload_ending_lone_cr_does_not_merge(self):
        fp = self.dir / "t.txt"
        fp.write_bytes(b"a\r\nb\r\nc\r\n")
        res = run(write_fix(str(fp), 2, 2, "B\r", project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(fp.read_bytes(), b"a\r\nB\r\nc\r\n")
        self.assertEqual(lone_cr(fp), 0)
        self.assertEqual(len(split_lines(fp.read_text(encoding="utf-8"))), 3)

    def test_crlf_payload_with_internal_lone_cr_normalizes_each_line(self):
        fp = self.dir / "t.txt"
        fp.write_bytes(b"a\r\nb\r\nc\r\n")
        res = run(write_fix(str(fp), 2, 2, "B1\rB2\r", project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(fp.read_bytes(), b"a\r\nB1\r\nB2\r\nc\r\n")
        self.assertEqual(lone_cr(fp), 0)

    def test_lf_payload_ending_lone_cr_does_not_merge(self):
        fp = self.dir / "t.txt"
        fp.write_bytes(b"a\nb\nc\n")
        res = run(write_fix(str(fp), 2, 2, "B\r", project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(fp.read_bytes(), b"a\nB\nc\n")
        self.assertEqual(lone_cr(fp), 0)

    def test_control_crlf_payload_still_correct(self):
        fp = self.dir / "t.txt"
        fp.write_bytes(b"a\r\nb\r\nc\r\n")
        res = run(write_fix(str(fp), 2, 2, "B\r\n", project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(fp.read_bytes(), b"a\r\nB\r\nc\r\n")

    def test_control_unterminated_payload_gets_file_eol(self):
        fp = self.dir / "t.txt"
        fp.write_bytes(b"a\r\nb\r\nc\r\n")
        res = run(write_fix(str(fp), 2, 2, "B", project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(fp.read_bytes(), b"a\r\nB\r\nc\r\n")
        self.assertEqual(lone_cr(fp), 0)


if __name__ == "__main__":
    unittest.main()
