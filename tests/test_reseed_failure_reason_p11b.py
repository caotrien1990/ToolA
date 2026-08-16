"""P11b: RESEED_FAILED must name the reason it actually hit.

The P11 fix turned a silent ``ok:true`` no-op into a loud ``RESEED_FAILED``.
Good -- but the message was written for exactly one of the three ways a reseed
can fail, and then printed for all three. Measured on the fix as delivered,
with a VALID ``project_path`` supplied and ``_read_token_upsert`` raising::

    error: "no project_path was supplied and no project root could be inferred"
    hint : "Pass project_path ... together with file_path and force_reseed=true"

Both sentences are false, and the hint instructs the caller to do the thing it
already did. That is the same dead loop P11 set out to kill, one layer up: the
recovery hint sends the CA around a circle. A wrong diagnosis is worse than a
generic one, because the caller believes it.

Three distinct causes, three distinct messages:
  * no_project_root  -- no project_path AND no inferable root (the P11 case)
  * outside_root     -- the file does not live under the project_path given
  * upsert_failed    -- root resolved fine; the token store itself refused
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import code_tools
from orchestrator.tools.read import adaptive as _adaptive

_REAL_GET_DOCUMENT = _adaptive.get_document


def _read(path: Path, **kwargs) -> dict:
    return json.loads(asyncio.run(code_tools.read_file_range(str(path), 1, 1, **kwargs)))


def _unstable_document(*args, **kwargs):
    """Delegate to the real get_document, then report the read as unstable.

    Wrapping beats hand-building a Document: the meta dict and the dataclass
    both carry fields the caller indexes directly (``cache_waited`` among
    them), and a stub that misses one fails for a reason that has nothing to
    do with the branch under test.
    """
    import dataclasses

    doc, meta = _REAL_GET_DOCUMENT(*args, **kwargs)
    return dataclasses.replace(doc, stable=False), meta


class ReseedFailureReasonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="p11b_"))
        (self.tmp / ".git").mkdir()
        self.file = self.tmp / "mod.py"
        self.file.write_text("print(1)\n", encoding="utf-8")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_upsert_failure_does_not_blame_a_missing_project_path(self):
        with mock.patch.object(
            code_tools, "_read_token_upsert", side_effect=RuntimeError("db is locked")
        ):
            result = _read(self.file, project_path=str(self.tmp), force_reseed=True)

        self.assertFalse(result.get("ok"), msg=result)
        self.assertEqual(result.get("error_name"), "RESEED_FAILED", msg=result)
        text = f"{result.get('error', '')} {result.get('hint', '')}".lower()
        self.assertNotIn(
            "no project_path was supplied", text,
            msg=f"blames a missing project_path that WAS supplied: {result}",
        )
        self.assertEqual(result.get("reseed_failure_reason"), "upsert_failed", msg=result)

    def test_upsert_failure_carries_the_underlying_error(self):
        # Without the cause, "the token store refused" is unactionable: a locked
        # DB, a full disk and a schema mismatch all read identically.
        with mock.patch.object(
            code_tools, "_read_token_upsert", side_effect=RuntimeError("db is locked")
        ):
            result = _read(self.file, project_path=str(self.tmp), force_reseed=True)

        self.assertIn("db is locked", str(result.get("reseed_error", "")), msg=result)

    def test_upsert_failure_is_not_advertised_as_retryable_the_same_way(self):
        # The P11 hint says "retry with project_path". Retrying that against a
        # refusing token store loops forever. The next action must differ.
        with mock.patch.object(
            code_tools, "_read_token_upsert", side_effect=RuntimeError("db is locked")
        ):
            result = _read(self.file, project_path=str(self.tmp), force_reseed=True)

        self.assertNotEqual(
            result.get("ca_next_action"), "retry_read_file_range_with_project_path",
            msg=f"tells the caller to redo what it already did: {result}",
        )

    def test_no_inferable_root_still_reports_the_original_p11_reason(self):
        # Regression guard on the case P11 was written for. tempfile lives under
        # HOME, and find_project_root refuses HOME and its ancestors, so with the
        # marker removed there is genuinely no root.
        import shutil

        shutil.rmtree(self.tmp / ".git")
        result = _read(self.file, force_reseed=True)

        self.assertFalse(result.get("ok"), msg=result)
        self.assertEqual(result.get("error_name"), "RESEED_FAILED", msg=result)
        self.assertEqual(result.get("reseed_failure_reason"), "no_project_root", msg=result)
        self.assertIn("project_path", (result.get("hint") or "").lower(), msg=result)

    def test_file_outside_the_resolved_root_is_classified_outside_root(self):
        # Tested against the helper, not through read_file_range. Measured: with
        # an explicit project_path, fo.resolve_project_file rejects the path
        # first ("File path ... is outside project root"), so read_file_range
        # never reaches the reseed. The branch is still worth locking -- the
        # helper is module-level and the guard order is not its contract -- but
        # the first draft of this test drove through read_file_range, accepted
        # "any ok:false", and stayed GREEN under sabotage. A test that cannot
        # fail is not coverage.
        from orchestrator.tools.read.fs import _seed_read_token_detailed

        outside = self.tmp.parent / f"{self.tmp.name}_outside.py"
        outside.write_text("print(2)\n", encoding="utf-8")
        try:
            seeded, detail = _seed_read_token_detailed(
                outside, str(self.tmp), "print(2)\n", outside.stat(), True)

            self.assertFalse(seeded, msg=detail)
            self.assertEqual(detail.get("reason"), "outside_root", msg=detail)
            self.assertEqual(detail.get("root"), str(self.tmp.resolve()), msg=detail)
        finally:
            outside.unlink(missing_ok=True)

    def test_read_file_range_rejects_an_outside_path_before_the_reseed(self):
        # Documents the guard order the test above depends on. If this ever
        # starts returning RESEED_FAILED instead, the test above is testing a
        # branch that read_file_range can now reach -- which is fine, but the
        # comment there would be stale, so pin the current behaviour.
        outside = self.tmp.parent / f"{self.tmp.name}_outside2.py"
        outside.write_text("print(3)\n", encoding="utf-8")
        try:
            result = _read(outside, project_path=str(self.tmp), force_reseed=True)

            self.assertFalse(result.get("ok"), msg=result)
            self.assertIn("outside project root", result.get("error", ""), msg=result)
        finally:
            outside.unlink(missing_ok=True)

    def test_unstable_read_without_force_reseed_does_not_crash(self):
        # Introduced and caught while writing P11b: the reseed block only runs
        # when ``doc.stable or force_reseed``; with both false the later
        # ``if force_reseed and not reseeded`` referenced an unbound name and
        # raised UnboundLocalError at context.py:251. Every P11b test passes
        # force_reseed=True, so none of them touched it -- the long-standing
        # AC-G5 cache test did. The both-false path deserves its own guard here.
        with mock.patch(
            "orchestrator.tools.read.adaptive.get_document",
            side_effect=_unstable_document,
        ):
            result = _read(self.file, project_path=str(self.tmp))

        self.assertTrue(result.get("ok"), msg=result)

    def test_successful_reseed_is_untouched(self):
        result = _read(self.file, project_path=str(self.tmp), force_reseed=True)

        self.assertTrue(result.get("ok"), msg=result)
        self.assertTrue(result.get("read_token_seeded"), msg=result)
        self.assertNotIn("reseed_failure_reason", result, msg=result)


if __name__ == "__main__":
    unittest.main()
