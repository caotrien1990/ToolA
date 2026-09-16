# -*- coding: utf-8 -*-
"""Write-draft loop tests (2026-07-17 upgrade).

WHY: pins Anh's new write contract —
- no size ceiling in the tool path (one huge write lands in one call),
- validation failure opens a draft with LINE-ACCURATE failed parts instead of
  forcing the CA to resend everything,
- write_draft_patch fixes only the failed range; the draft re-audits and
  ships through the original pipeline the moment it is clean,
- a target that changed under a pending draft refuses to ship (STALE_TARGET),
- abort discards without touching the target.
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for p in (str(_ROOT),):
    if p not in sys.path:
        sys.path.insert(0, p)

from orchestrator.tools.write import (write_draft_abort, write_draft_patch,  # noqa: E402
                                      write_draft_status, write_fix, write_new_file)
from orchestrator.tools.write import draft as draft_mod                       # noqa: E402
from code_tools import generate_project_map                                    # noqa: E402


def run(coro):
    return json.loads(asyncio.run(coro))


class DraftLoopCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a_draft_test_")
        self.dir = Path(self._tmp.name)
        # Initialise the temp dir as a real project so the writes below get an
        # explicit, valid project_path and NEVER climb parents of %TEMP% to infer
        # a root (that path reaches HOME, where a junk marker can hijack it).
        # A seed file must exist BEFORE generate_project_map so an INDEX.json is
        # actually written (an empty dir produces no INDEX, and the write guard
        # then rejects with 'INDEX not found').
        (self.dir / "_seed.py").write_text("seed = 1\n", encoding="utf-8")
        run(generate_project_map(str(self.dir)))
        with draft_mod._LOCK:
            draft_mod._DRAFTS.clear()

    def tearDown(self):
        self._tmp.cleanup()

    def _mkfile(self, name, text):
        fp = self.dir / name
        fp.write_text(text, encoding="utf-8")
        return fp

    # ── contract 1: unlimited size straight-through ─────────────────────────
    def test_huge_write_lands_in_one_call(self):
        # project_path is passed EXPLICITLY so the tool never infers the root by
        # climbing parents of a %TEMP% path (which reaches HOME, where a stray
        # junk marker can hijack inference and hang the snapshot publish).
        fp = self._mkfile("big.py", "x = 0\n")
        body = "\n".join(f"def f_{i}():\n    return {i}\n" for i in range(2500))
        res = run(write_fix(str(fp), 1, 1, body, project_path=str(self.dir)))
        self.assertTrue(res.get("ok"), res)
        self.assertGreaterEqual(res.get("new_lines_count", 0), 5000)

    # ── contract 2: failure opens a line-accurate draft, target untouched ───
    # The assertions live in a helper because contract 3 reuses the draft they build.
    # A test method must NOT return a value -- unittest deprecated that in 3.12 and
    # says it will become an error, so returning `res` from the test itself would
    # break this file on a future Python instead of merely warning.
    def _open_draft_with_bad_python(self):
        fp = self._mkfile("mod.py", "a = 1\nb = 2\nc = 3\n")
        original = fp.read_text(encoding="utf-8")
        bad = "def broken(:\n    pass\n\ngood = 1\n"
        res = run(write_fix(str(fp), 2, 2, bad, project_path=str(self.dir)))
        self.assertFalse(res.get("ok"))
        self.assertTrue(res.get("draft"))
        self.assertTrue(res.get("draft_id"))
        self.assertEqual(fp.read_text(encoding="utf-8"), original)  # never dirtied
        parts = res.get("failed_parts") or []
        self.assertTrue(parts, res)
        err = parts[0]["errors"][0]
        self.assertEqual(err["line"], 2)          # draft line of 'def broken(:'
        self.assertIn(">>2:", parts[0]["excerpt"])
        return res

    def test_bad_python_opens_draft_with_line(self):
        self._open_draft_with_bad_python()

    # ── contract 3: patch only the failed range; clean -> auto-ship ─────────
    def test_patch_wrong_then_right_then_ship(self):
        res = self._open_draft_with_bad_python()
        draft_id = res["draft_id"]
        err_line = res["failed_parts"][0]["errors"][0]["line"]
        still = run(write_draft_patch(draft_id, err_line, err_line, "def broken(:\n"))
        self.assertFalse(still.get("ok"))
        self.assertEqual(still.get("error_name"), "DRAFT_STILL_FAILING")
        self.assertTrue(still.get("failed_parts"))
        shipped = run(write_draft_patch(draft_id, err_line, err_line, "def broken():\n"))
        self.assertTrue(shipped.get("ok"), shipped)
        self.assertTrue(shipped.get("shipped"))
        fp = Path(shipped["file"])
        text = fp.read_text(encoding="utf-8")
        self.assertIn("def broken():", text)
        self.assertIn("good = 1", text)
        self.assertIn("a = 1", text)              # untouched context preserved
        gone = run(write_draft_status(draft_id))
        self.assertEqual(gone.get("error_name"), "DRAFT_NOT_FOUND")

    def test_write_new_file_draft_flow(self):
        target = self.dir / "cfg.json"
        res = run(write_new_file(str(target), '{"a": 1,\n "b": ,\n "c": 3}\n', project_path=str(self.dir)))
        self.assertFalse(res.get("ok"))
        self.assertTrue(res.get("draft_id"), res)
        self.assertFalse(target.exists())
        err_line = res["failed_parts"][0]["errors"][0]["line"]
        self.assertEqual(err_line, 2)
        shipped = run(write_draft_patch(res["draft_id"], 2, 2, ' "b": 2,\n'))
        self.assertTrue(shipped.get("ok"), shipped)
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["b"], 2)

    # ── contract 4: stale target refuses to ship ────────────────────────────
    def test_stale_target_blocks_ship(self):
        fp = self._mkfile("stale.py", "v = 1\n")
        res = run(write_fix(str(fp), 1, 1, "def broken(:\n", project_path=str(self.dir)))
        draft_id = res["draft_id"]
        fp.write_text("v = 999  # changed outside\n", encoding="utf-8")
        blocked = run(write_draft_patch(draft_id, 1, 1, "def fixed():\n    return 1\n"))
        self.assertFalse(blocked.get("ok"))
        self.assertEqual(blocked.get("error_name"), "STALE_TARGET")
        self.assertTrue(blocked.get("draft_kept"))
        self.assertIn("999", fp.read_text(encoding="utf-8"))  # external edit intact

    # ── contract 5: abort + status listing ──────────────────────────────────
    def test_abort_and_listing(self):
        fp = self._mkfile("ab.py", "z = 1\n")
        res = run(write_fix(str(fp), 1, 1, "def broken(:\n", project_path=str(self.dir)))
        listing = run(write_draft_status(""))
        self.assertEqual(listing["count"], 1)
        out = run(write_draft_abort(res["draft_id"]))
        self.assertTrue(out.get("aborted"))
        listing = run(write_draft_status(""))
        self.assertEqual(listing["count"], 0)
        self.assertEqual(fp.read_text(encoding="utf-8"), "z = 1\n")

    # ── contract 6: revert branches also carry a draft ──────────────────────
    def test_deep_validation_failure_attaches_draft(self):
        # JSON deep-validate runs in layer 1/2; a syntactically-plausible but
        # invalid payload must still open a draft rather than a bare error.
        target = self.dir / "deep.json"
        res = run(write_new_file(str(target), '{"x": [1, 2,\n]}\nEXTRA', project_path=str(self.dir)))
        self.assertFalse(res.get("ok"))
        self.assertTrue(res.get("draft_id"), res)


if __name__ == "__main__":
    unittest.main()
