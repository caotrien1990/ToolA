"""campaign-1608: A02's boot-sweep ledger vs A30's periodic full refresh.

The two fixes were built independently and they touch the same directory. A02
stores its write-ahead ledger (write_backups.jsonl) and its one-time seed marker
(.write_backups.seeded) in .orchestrator_cache/. A30 made every periodic full
refresh call surgical_mapper._reindex_from_disk, which WIPES that directory
except for names in PRESERVED_CACHE_FILES / PRESERVED_CACHE_DIRS / SQLite
artifacts. Neither A02 name is preserved today.

Measured, not assumed: after _reindex_from_disk both files are gone.

THE INVARIANT (what these tests pin) is not "both are wiped" -- it is that the
ledger and the marker SHARE ONE FATE. Three of the four possible configurations
are acceptable-or-current, and exactly one is a data-loss bug:

  both wiped (today)        -> ledger empty AND marker gone -> the next boot
                               re-seeds from the index and re-discovers every
                               legacy .bak. Correct; it just pays the O(index)
                               seed again (which is exactly the cost A02 set out
                               to remove -- so A02's cost claim is "once per full
                               refresh", NOT "once ever"; asserted below).
  both preserved            -> ledger and marker both survive; the seed stays
                               done and the ledger still lists every pending
                               .bak. Also correct, and strictly cheaper. A future
                               change may legitimately move here, so these tests
                               must NOT block it.
  ledger wiped, marker kept -> the seed never re-runs, so a .bak written before
                               the refresh is ORPHANED and never restored.
                               Silent DATA LOSS. This is the one to catch.
  ledger kept, marker wiped -> the seed re-runs over the whole index while the
                               ledger already lists the same backups: duplicated
                               work and re-restore of backups the seed handled.
                               Not data loss, but incoherent -- also caught.

The dangerous variant is one line away: adding ".write_backups.seeded" alone to
PRESERVED_CACHE_FILES looks like an obvious tidy-up ("the marker is runtime
state, don't delete it"). These tests exist so that edit turns the suite red
instead of quietly orphaning backups -- while leaving the safe both-preserved
refactor green.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import surgical_mapper as sm
from orchestrator.tools.write import core as write_core


class TestLedgerVsPeriodicRefresh(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="a02_a30_")).resolve()
        (self.root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
        sm.generate_project_map(str(self.root))
        self.cache = self.root / sm.CACHE_DIR_NAME
        self.ledger = self.cache / write_core._WRITE_BACKUP_LEDGER_NAME
        self.marker = self.cache / write_core._WRITE_BACKUP_SEED_MARKER

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _plant(self) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text('{"target":"a.py","bak":"a.py.bak"}\n', encoding="utf-8")
        self.marker.write_text("done\n", encoding="utf-8")

    def _assert_shared_fate(self, where: str) -> None:
        led, mark = self.ledger.exists(), self.marker.exists()
        if led == mark:
            return
        if mark:  # marker survived alone -- the data-loss case
            self.fail(
                f"{where}: seed marker survived while the ledger was wiped -> the "
                "one-time seed will never re-run and any .bak recorded before the "
                "refresh is orphaned and never restored (DATA LOSS). If preserving "
                "the marker was intentional, preserve the ledger too."
            )
        self.fail(
            f"{where}: ledger survived while the seed marker was wiped -> the "
            "O(index) seed re-runs over backups the ledger already lists, "
            "duplicating work and re-restoring handled backups. Preserve both or "
            "neither."
        )

    # ---- the fate they must share ----------------------------------------

    def test_full_refresh_keeps_ledger_and_marker_in_step(self) -> None:
        """Either both survive a full refresh or neither does. Preserving exactly
        one is the bug; this is deliberately agnostic about WHICH way, so the safe
        both-preserved refactor stays green."""
        self._plant()
        self.assertTrue(self.ledger.exists())
        self.assertTrue(self.marker.exists())

        sm._reindex_from_disk(self.root)

        self._assert_shared_fate("_reindex_from_disk")

    def test_preserve_guard_treats_them_identically(self) -> None:
        """Stated directly against the guard so it holds no matter how the wipe
        loop is refactored -- and so a one-sided PRESERVED_CACHE_FILES edit fails
        here even if no wipe ever runs in the test."""
        ledger_kept = sm._cache_path_is_preserved(write_core._WRITE_BACKUP_LEDGER_NAME)
        marker_kept = sm._cache_path_is_preserved(write_core._WRITE_BACKUP_SEED_MARKER)
        self.assertEqual(
            ledger_kept,
            marker_kept,
            "A02's ledger and seed marker must share one fate across a cache wipe: "
            f"ledger_preserved={ledger_kept} marker_preserved={marker_kept}. "
            "Preserving the marker alone orphans pending backups (data loss); "
            "preserving the ledger alone re-restores backups the seed already "
            "handled. Preserve both, or neither.",
        )

    def test_generate_project_map_keeps_them_in_step_too(self) -> None:
        """The periodic path is not the only caller -- generate_project_map
        delegates to the same helper, so the same coupling must hold there."""
        self._plant()
        sm.generate_project_map(str(self.root))
        self._assert_shared_fate("generate_project_map")

    # ---- the cost consequence of today's choice, stated honestly ---------

    def test_wiping_the_marker_forces_the_expensive_seed_again(self) -> None:
        """A02's premise is that the O(index) seed is paid at most once. With
        today's wipe-both behaviour a full refresh resets that, so the real claim
        is 'once per full refresh', NOT 'once ever'. Pinned so the docstring and
        reality cannot drift apart -- but skipped rather than failed if the repo
        later moves to preserving both, which removes the cost entirely."""
        self._plant()
        sm._reindex_from_disk(self.root)
        if self.marker.exists():
            self.assertTrue(
                self.ledger.exists(),
                "marker preserved without its ledger -- covered by the shared-fate "
                "tests; not re-asserted here",
            )
            self.skipTest(
                "repo now preserves the seed marker across a full refresh, so the "
                "seed is no longer re-paid; nothing to pin here"
            )
        # marker gone => _boot_sweep_scan's `if not seed_marker_present` is true
        # again => the index seed runs on the next boot.
        self.assertFalse(
            self.ledger.exists(),
            "ledger survived a refresh that destroyed its marker",
        )


if __name__ == "__main__":
    unittest.main()
