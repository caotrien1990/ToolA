"""A30: the periodic full-refresh must NOT crash an unrelated innocent write, and
it must RECONCILE external edits — not merely 'not crash'.

Root defect (reproduced): update_file_cache patches the INDEX for ONLY the file it
just wrote (O(changed)), but every _SNAPSHOT_FULL_REFRESH_EVERY-th write forces an
authoritative full-walk snapshot publish (changed_paths=None). The full walk re-captures
the on-disk content of EVERY file, while the INDEX still reflects only the single write.
So if ANY file was edited OUTSIDE [A] (native/git/IDE), the full-walk publish raised
    SnapshotIntegrityError: INDEX/content hash mismatch: <externally-edited file>
inside _validate_index_against_records — killing a completely innocent write to an
unrelated file. And crucially: the periodic full-walk is the ONE publish path with NO
self-heal fallback (_publish_search_snapshot only retries with a full walk when
changed_paths is not None; here it is None), so the mismatch propagates as a crash.

Fix (option a): before the authoritative full walk, reconcile the INDEX from disk
(_reindex_from_disk), so INDEX and re-captured content agree by construction. The failure
mode this replaces was a LOUD crash; the fix must keep the result LOUD-CORRECT — the
published snapshot must be index_authoritative AND actually contain the external edit.
Swallowing the mismatch (silent non-reconcile) is explicitly NOT acceptable and is
pinned against by test_periodic_refresh_is_not_a_silent_swallow.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import surgical_mapper as sm
from orchestrator import search_snapshots


def _drive_counter_to_next_full_refresh(root: Path) -> None:
    """Set the per-project write counter so the NEXT write is the full-refresh multiple."""
    key = os.path.normcase(str(root.resolve()))
    sm._SNAPSHOT_WRITE_COUNTER[key] = sm._SNAPSHOT_FULL_REFRESH_EVERY - 1


class TestPeriodicRefreshA30(unittest.TestCase):
    def setUp(self) -> None:
        # basetemp OUTSIDE the repo so fixtures never inherit the repo ruff.toml/cache.
        self.root = Path(tempfile.mkdtemp(prefix="a30_periodic_")).resolve()
        (self.root / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
        (self.root / "target.py").write_text(
            "def old_symbol():\n    return 'ORIGINAL'\n", encoding="utf-8"
        )
        sm.generate_project_map(str(self.root))

    def tearDown(self) -> None:
        sm._SNAPSHOT_WRITE_COUNTER.pop(os.path.normcase(str(self.root)), None)
        shutil.rmtree(self.root, ignore_errors=True)

    def _edit_outside_orchestrator(self, rel: str, content: str) -> None:
        (self.root / rel).write_text(content, encoding="utf-8")

    # ---- the crash itself ------------------------------------------------

    def test_innocent_write_on_full_refresh_does_not_crash(self) -> None:
        """The regression from step 1: an external edit to target.py must not make the
        50th (full-refresh) write to the UNRELATED keep.py die with SnapshotIntegrityError."""
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW-AND-LONGER'\n"
        )
        _drive_counter_to_next_full_refresh(self.root)
        # Must NOT raise. (Before the fix this raised
        # SnapshotIntegrityError: INDEX/content hash mismatch: target.py.)
        meta = sm.update_file_cache(str(self.root / "keep.py"), str(self.root))
        self.assertIn("snapshot_id", meta)

    # ---- the CORRECT behaviour (not just 'no crash') ---------------------

    def test_full_refresh_reconciles_external_edit_into_snapshot(self) -> None:
        """After the full-refresh publish, the externally-edited content must be SERVED."""
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n"
        )
        _drive_counter_to_next_full_refresh(self.root)
        sm.update_file_cache(str(self.root / "keep.py"), str(self.root))

        with search_snapshots.pin_current_snapshot(self.root) as pin:
            served = pin.read_bytes("target.py").decode("utf-8")
        self.assertIn(
            "brand_new_symbol_xyz", served,
            "full-refresh must re-capture the externally-edited file's NEW content",
        )
        self.assertNotIn("old_symbol", served)

    def test_periodic_refresh_is_not_a_silent_swallow(self) -> None:
        """The published snapshot after a full refresh must be index_authoritative AND its
        INDEX must AGREE with the manifest content hashes. A fix that merely swallowed the
        mismatch (or published a non-authoritative / stale INDEX) fails HERE. This is the
        anti-silent-success gate: 'did not crash' is not enough."""
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n"
        )
        _drive_counter_to_next_full_refresh(self.root)
        sm.update_file_cache(str(self.root / "keep.py"), str(self.root))

        with search_snapshots.pin_current_snapshot(self.root) as pin:
            self.assertIs(
                pin.manifest.get("index_authoritative"), True,
                "full-refresh must publish an AUTHORITATIVE index, not degrade silently",
            )
            index = pin.read_index()
            records = {r["path"]: r for r in pin.manifest["files"]}
            for rel, info in index["files"].items():
                rec = records[rel]
                self.assertIn(
                    str(info.get("hash") or ""), {rec["index_hash"], rec["sha256"]},
                    f"INDEX/content hash must agree for {rel} (no stale/swallowed entry)",
                )

    def test_full_refresh_reconciles_external_delete(self) -> None:
        """An external DELETE must also be reconciled by the full refresh: the deleted file
        must drop out of the served snapshot (not linger as a phantom record)."""
        self._edit_outside_orchestrator("extra.py", "def gone():\n    return 0\n")
        # index extra.py through [A] so it is in the snapshot to begin with
        sm.update_file_cache(str(self.root / "extra.py"), str(self.root))
        (self.root / "extra.py").unlink()  # external delete, [A] never told

        _drive_counter_to_next_full_refresh(self.root)
        sm.update_file_cache(str(self.root / "keep.py"), str(self.root))

        with search_snapshots.pin_current_snapshot(self.root) as pin:
            paths = {r["path"] for r in pin.manifest["files"]}
        self.assertNotIn("extra.py", paths)

    def test_fast_path_write_still_incremental(self) -> None:
        """A non-refresh (fast-path) write must NOT trigger a full reconcile: it keeps the
        O(changed) behaviour and does not re-capture unrelated external edits."""
        self._edit_outside_orchestrator(
            "target.py", "def brand_new_symbol_xyz():\n    return 'NEW'\n"
        )
        # counter is well away from the multiple -> fast path
        key = os.path.normcase(str(self.root.resolve()))
        sm._SNAPSHOT_WRITE_COUNTER[key] = 1
        sm.update_file_cache(str(self.root / "keep.py"), str(self.root))
        with search_snapshots.pin_current_snapshot(self.root) as pin:
            served = pin.read_bytes("target.py").decode("utf-8")
        # fast path is O(changed): it must NOT have re-captured target.py
        self.assertIn("old_symbol", served)


if __name__ == "__main__":
    unittest.main()
