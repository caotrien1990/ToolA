"""A03: search_code must not serve a stale snapshot silently.

Root defect: search_code reads an immutable published snapshot and never reconciles
it against disk. A file edited OUTSIDE [A] (native edit / git checkout / IDE) never
enters the snapshot, so a real, committed symbol can be reported as count:0 =
"not found" -- a phantom. This is the exact failure that made a previous subagent
declare `cbm_cycle_point` (a real tool) to be non-existent.

Layer 1 (fail-loud): on count==0 search_code must consult the existing drift signal
(_index_drift / cache_delta) and, when the index is stale, report the result as
INCONCLUSIVE ("khong ket luan duoc"), not "not found":
    stale_index: True
    stale_files_count: N (>=1)
    ca_next_action: "refresh_or_narrow"
    inconclusive: True
If the drift probe itself FAILS, search_code must say so (stale_index_error) and
must NOT claim a clean index.

Layer 2 (root fix): an incremental snapshot publish must re-stat files changed
outside [A] so the served snapshot stops lying.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

import code_tools
import surgical_mapper
from orchestrator.tools import search as search_module


def _build_project(root: Path) -> None:
    """Build a real INDEX + published search snapshot for `root` (like [A] does)."""
    surgical_mapper.generate_project_map(str(root))


def _clear_drift_caches() -> None:
    # cache_delta coalesces per-project futures; the recent-refresh window suppresses
    # drift. Clear both so each test observes a fresh, unsuppressed drift computation.
    try:
        code_tools._RECENT_REFRESH_TS.clear()
    except Exception:
        pass
    try:
        with code_tools._CACHE_DELTA_FUTURES_LOCK:
            code_tools._CACHE_DELTA_FUTURES.clear()
    except Exception:
        pass


class TestSearchStaleIndexA03(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # basetemp lives OUTSIDE the repo so fixtures never inherit the repo ruff.toml.
        self.root = Path(tempfile.mkdtemp(prefix="a03_stale_")).resolve()
        (self.root / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
        (self.root / "target.py").write_text(
            "def old_symbol():\n    return 'ORIGINAL'\n", encoding="utf-8"
        )
        _build_project(self.root)
        _clear_drift_caches()

    def tearDown(self) -> None:
        _clear_drift_caches()
        shutil.rmtree(self.root, ignore_errors=True)

    async def _search(self, query: str, **kw) -> dict:
        raw = await search_module.search_code(
            query=query,
            path=str(self.root),
            project_path=str(self.root),
            mode=kw.pop("mode", "text"),
            max_results=kw.pop("max_results", 20),
            **kw,
        )
        return json.loads(raw)

    def _edit_outside_orchestrator(self, rel: str, content: str) -> None:
        """Simulate a native/IDE/git edit that bypasses [A]'s write pipeline."""
        (self.root / rel).write_text(content, encoding="utf-8")

    # ---- Layer 1 ----------------------------------------------------------

    async def test_phantom_not_found_is_reported_inconclusive_when_index_stale(self) -> None:
        """A committed symbol added OUTSIDE [A] must NOT be reported as a clean 0-hit."""
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n"
        )
        _clear_drift_caches()
        payload = await self._search("brand_new_symbol_xyz")

        self.assertEqual(payload["count"], 0, "snapshot is stale so the new symbol is not in it")
        self.assertTrue(
            payload.get("stale_index") is True,
            f"expected stale_index True, got {payload.get('stale_index')!r}",
        )
        self.assertGreaterEqual(int(payload.get("stale_files_count") or 0), 1)
        self.assertEqual(payload.get("ca_next_action"), "refresh_or_narrow")
        self.assertTrue(
            payload.get("inconclusive") is True,
            "a 0-hit over a stale index must be inconclusive, not 'not found'",
        )

    async def test_clean_index_zero_hit_is_a_real_not_found(self) -> None:
        """When the index IS fresh, a genuine 0-hit must stay a plain not-found."""
        payload = await self._search("definitely_absent_token_qqq")
        self.assertEqual(payload["count"], 0)
        self.assertFalse(
            payload.get("stale_index") is True,
            "fresh index must not raise a false stale warning",
        )
        self.assertIsNot(payload.get("inconclusive"), True)
        self.assertNotIn("stale_index_error", payload)

    async def test_drift_probe_failure_is_reported_not_swallowed(self) -> None:
        """If the drift probe itself fails, do NOT claim a clean index -- say so."""
        # A03b moved the seam from _index_drift to cache_delta: _index_drift folds
        # five distinct situations (including the 30s post-refresh noise-suppression
        # window) into a bare None, and reading that None as "clean" reintroduced the
        # very phantom A03 exists to prevent. Mocking the old seam here would leave a
        # test that can no longer fail, so it follows the seam.
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n"
        )
        _clear_drift_caches()
        with mock.patch.object(
            code_tools, "cache_delta", side_effect=RuntimeError("drift boom")
        ):
            payload = await self._search("brand_new_symbol_xyz")

        self.assertEqual(payload["count"], 0)
        self.assertIn("stale_index_error", payload)
        self.assertNotEqual(
            payload.get("stale_index"), False,
            "a failed drift probe must not be reported as a clean (False) index",
        )
        self.assertTrue(
            payload.get("inconclusive") is True,
            "cannot conclude 'not found' when the staleness probe failed",
        )

    async def test_hits_present_keeps_fast_path_no_stale_probe(self) -> None:
        """count>0 must not pay the drift cost and must not be flagged inconclusive."""
        with mock.patch.object(
            code_tools, "cache_delta", side_effect=AssertionError("drift must not run on hits")
        ):
            payload = await self._search("old_symbol")
        self.assertGreaterEqual(payload["count"], 1)
        self.assertIsNot(payload.get("inconclusive"), True)

    # ---- Layer 2 (documented NOT-DONE; see report) ------------------------

    async def test_incremental_publish_reconciles_external_edit(self) -> None:
        """After ANY incremental publish, a file edited outside [A] must stop being a
        phantom: its new content must appear in the served snapshot."""
        # 1) external edit to target.py that [A] never saw
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n"
        )
        # 2) an unrelated write THROUGH [A] triggers an incremental publish
        surgical_mapper.update_file_cache(str(self.root / "keep.py"), str(self.root))
        _clear_drift_caches()
        # 3) the snapshot must now reflect the external edit
        payload = await self._search("brand_new_symbol_xyz")
        self.assertGreaterEqual(
            payload["count"], 1,
            "incremental publish must re-stat externally-changed files into snapshot",
        )


if __name__ == "__main__":
    unittest.main()
