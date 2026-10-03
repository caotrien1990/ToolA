"""A03b: ``stale_index: False`` must mean "measured clean", not "did not measure".

The A03 Layer-1 fix asks ``_index_drift()`` whether the index is stale and reports
``stale_index: False`` whenever it answers ``None``. But ``None`` is that helper's
answer to **five** different questions, and only one of them is "clean":

  * no project_path
  * the index was refreshed within ``_DRIFT_SUPPRESS_WINDOW_SEC`` (30s) -- a pure
    NOISE-REDUCTION suppression, deliberately unrelated to whether disk drifted
  * ``cache_delta`` raised
  * ``cache_delta`` returned a non-dict / ``ok:false`` payload
  * genuinely no added/modified/deleted files   <-- the only "clean"

Measured on the fix as delivered, with an external edit made 1s after a
``generate_project_map``::

    _is_index_recently_refreshed -> True
    _index_drift                 -> None
    cache_delta (ground truth)   -> modified: ['target.py'], total_changed: 1
    search_code                  -> count: 0, stale_index: False, inconclusive: None

So during the 30 seconds right after a refresh -- exactly when a CA is most likely
to be mid-edit-loop -- a real symbol is still declared a clean not-found. This is
the same defect A03 set out to kill, surviving inside its own fix, and it is the
same shape as P11b: one sentinel standing in for several distinct causes, printed
as the most reassuring of them.

``stale_index`` therefore has to distinguish "no" from "cannot say", and the
suppress window must not be allowed to answer a staleness question it was never
measuring.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools
import surgical_mapper
from orchestrator.tools import search as search_module


def _clear_delta_futures() -> None:
    try:
        with code_tools._CACHE_DELTA_FUTURES_LOCK:
            code_tools._CACHE_DELTA_FUTURES.clear()
    except Exception:
        pass


class SearchStaleIndexUnknownTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="a03b_")).resolve()
        (self.root / "keep.py").write_text(
            "def keep():\n    return 1\n", encoding="utf-8")
        (self.root / "target.py").write_text(
            "def old_symbol():\n    return 'ORIGINAL'\n", encoding="utf-8")
        surgical_mapper.generate_project_map(str(self.root))
        code_tools._RECENT_REFRESH_TS.clear()
        _clear_delta_futures()

    def tearDown(self) -> None:
        code_tools._RECENT_REFRESH_TS.clear()
        _clear_delta_futures()
        shutil.rmtree(self.root, ignore_errors=True)

    async def _search(self, query: str) -> dict:
        return json.loads(await search_module.search_code(
            query=query, path=str(self.root), project_path=str(self.root),
            mode="text", max_results=20))

    def _edit_outside(self, rel: str, content: str) -> None:
        (self.root / rel).write_text(content, encoding="utf-8")

    async def test_refresh_suppress_window_does_not_certify_a_clean_index(self):
        # The exact measured hole. _mark_index_refreshed is what generate_project_map /
        # refresh_project_map call, so this is the state after any real refresh.
        # P15: the drift is not merely REPORTED anymore -- the bounded reconcile
        # fixes it in place and the re-run search FINDS the edited-in symbol.
        self._edit_outside(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n")
        code_tools._mark_index_refreshed(str(self.root))
        _clear_delta_futures()

        self.assertTrue(code_tools._is_index_recently_refreshed(str(self.root)),
                        "precondition: inside the suppress window")
        payload = await self._search("brand_new_symbol_xyz")

        self.assertGreaterEqual(payload["count"], 1, msg=payload)
        self.assertIs(payload.get("stale_index"), False, msg=payload)
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), True, msg=payload)

    async def test_stale_edit_with_zero_byte_budget_is_still_reported_inside_the_suppress_window(self):
        # Stronger than the test above: not merely "not False" -- when the drift
        # is real and measurable but the reconcile can make NO progress (a zero
        # per-call byte budget fits not even one file), it must still be reported
        # as real (the reconcile must not eat the report). P15 r2 replaced the
        # all-or-nothing count bound with a per-call time/bytes budget; a budget
        # of 0 bytes is the "cannot progress" shape that keeps A03 honest.
        self._edit_outside(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n")
        code_tools._mark_index_refreshed(str(self.root))
        _clear_delta_futures()

        with mock.patch.object(surgical_mapper, "SEARCH_RECONCILE_CALL_BYTES", 0, create=True):
            payload = await self._search("brand_new_symbol_xyz")

        self.assertEqual(payload["count"], 0, msg=payload)
        self.assertIs(payload.get("stale_index"), True, msg=payload)
        self.assertGreaterEqual(
            int(payload.get("stale_files_count") or 0), 1, msg=payload)
        self.assertIs(payload.get("inconclusive"), True, msg=payload)
        rec = payload.get("stale_reconcile")
        self.assertIsInstance(rec, dict, msg=payload)
        self.assertIs(rec.get("applied"), False, msg=payload)
        self.assertEqual(rec.get("reason"), "budget_exhausted", msg=payload)
    async def test_unusable_delta_payload_is_unknown_not_clean(self):
        # cache_delta answering ok:false is "I could not measure", which the old code
        # folded into the same None as "measured, nothing changed".
        async def _bad_delta(_root, *a, **kw):
            return json.dumps({"ok": False, "error": "index missing"})

        with mock.patch.object(code_tools, "cache_delta", _bad_delta):
            payload = await self._search("definitely_absent_token_qqq")

        self.assertNotEqual(
            payload.get("stale_index"), False,
            msg=f"an ok:false delta is not evidence of a clean index: {payload}")
        self.assertIs(payload.get("inconclusive"), True, msg=payload)

    async def test_measured_clean_index_still_reports_a_plain_not_found(self):
        # The regression guard that keeps the fix from degenerating into
        # "always inconclusive", which would be just as useless.
        payload = await self._search("definitely_absent_token_qqq")

        self.assertEqual(payload["count"], 0, msg=payload)
        self.assertIs(payload.get("stale_index"), False, msg=payload)
        self.assertIsNot(payload.get("inconclusive"), True, msg=payload)
        self.assertNotIn("stale_index_error", payload, msg=payload)

    async def test_reason_names_why_the_index_could_not_be_certified(self):
        # "inconclusive: true" without a cause is the generic-message trap P11b hit.
        async def _bad_delta(_root, *a, **kw):
            return json.dumps({"ok": False, "error": "index missing"})

        with mock.patch.object(code_tools, "cache_delta", _bad_delta):
            payload = await self._search("definitely_absent_token_qqq")

        self.assertTrue(payload.get("stale_index_reason"), msg=payload)


if __name__ == "__main__":
    unittest.main()
