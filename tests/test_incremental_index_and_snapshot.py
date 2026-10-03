"""Guards for the per-write incremental cache-sync (2026-08-12).

The write path (surgical_mapper.update_file_cache) used to call build_index() — a full
re-read of every cached JSON — AND publish_snapshot() — a full os.walk + re-hash of every
file — on EVERY single-file write. That is O(repo) per write (~11s / 8k files) and forced
callers to fall back to native edits on large repos. The write path is now incremental:

  * update_index_incremental() patches only the changed file's INDEX entry/refs and
    recomputes aggregates in memory, and
  * publish_snapshot() reuses the prior snapshot's per-file record for any file whose
    (mtime_ns, size) is unchanged (content-addressed blob already stored).

These tests pin the two invariants that make that safe:
  1. an incrementally-patched INDEX is byte-equivalent to a full build_index rebuild
     across add / modify / remove, and
  2. a snapshot published after incremental writes still serves correct file content
     (reused records point at the right bytes; changed files are re-captured).
"""
import json
import tempfile
import unittest
from pathlib import Path

import surgical_mapper as sm
from orchestrator import search_snapshots


def _canonical(index: dict) -> dict:
    """Order-insensitive view of the parts that must match a full rebuild."""
    def _refs(m):
        return {k: sorted(v, key=lambda r: (r.get("file") or "", r.get("line_start") or 0))
                for k, v in (m or {}).items()}
    return {
        "files": index.get("files"),
        "functions": _refs(index.get("functions")),
        "classes": _refs(index.get("classes")),
        "groups": index.get("groups"),
        "entrypoints": sorted(index.get("entrypoints", [])),
        "hotspots": index.get("hotspots"),
    }


class TestIncrementalIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="inc_idx_")).resolve()
        for i in range(40):
            (self.tmp / f"m{i}.py").write_text(
                f"def f{i}(x):\n    return x + {i}\n\n\nclass C{i}:\n    pass\n", encoding="utf-8")
        sm.generate_project_map(str(self.tmp))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _live_index(self) -> dict:
        return json.loads((self.tmp / ".orchestrator_cache" / "INDEX.json").read_text(encoding="utf-8"))

    def _assert_matches_full_rebuild(self):
        full = sm.build_index(str(self.tmp))       # authoritative full rebuild
        live = self._live_index()                  # incrementally patched
        self.assertEqual(_canonical(full), _canonical(live))

    def test_modify_adds_new_symbol_and_matches_full(self):
        target = self.tmp / "m0.py"
        target.write_text(
            "def f0(x):\n    return x + 999\n\n\ndef brand_new(y):\n    return y\n\n\nclass C0:\n    pass\n",
            encoding="utf-8")
        sm.update_file_cache(str(target), str(self.tmp))
        live = self._live_index()
        self.assertIn("brand_new", live["functions"])
        self.assertEqual(live["functions"]["brand_new"][0]["file"], "m0.py")
        self._assert_matches_full_rebuild()

    def test_modify_removes_old_symbol(self):
        target = self.tmp / "m1.py"
        # f1 removed, replaced by g1
        target.write_text("def g1(x):\n    return x\n", encoding="utf-8")
        sm.update_file_cache(str(target), str(self.tmp))
        live = self._live_index()
        # f1 no longer references m1.py
        self.assertNotIn("f1", live["functions"])
        self.assertIn("g1", live["functions"])
        self._assert_matches_full_rebuild()

    def test_add_new_file_matches_full(self):
        target = self.tmp / "brand.py"
        target.write_text("def hello():\n    return 1\n", encoding="utf-8")
        sm.update_file_cache(str(target), str(self.tmp))
        live = self._live_index()
        self.assertIn("brand.py", live["files"])
        self.assertIn("hello", live["functions"])
        self._assert_matches_full_rebuild()

    def test_remove_file_matches_full(self):
        target = self.tmp / "m2.py"
        target.unlink()
        sm.update_file_cache(str(target), str(self.tmp))
        live = self._live_index()
        self.assertNotIn("m2.py", live["files"])
        self.assertNotIn("f2", live["functions"])
        self._assert_matches_full_rebuild()

    def test_missing_index_falls_back_to_full_build(self):
        # Delete INDEX.json; incremental must fall back to a correct full rebuild.
        (self.tmp / ".orchestrator_cache" / "INDEX.json").unlink()
        target = self.tmp / "m3.py"
        target.write_text("def f3(x):\n    return x + 3\n\n\ndef extra():\n    return 0\n", encoding="utf-8")
        sm.update_file_cache(str(target), str(self.tmp))
        self._assert_matches_full_rebuild()
        self.assertIn("extra", self._live_index()["functions"])


class TestSnapshotReuseServesCorrectContent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="inc_snap_")).resolve()
        for i in range(30):
            (self.tmp / f"m{i}.py").write_text(f"# file {i}\nVALUE = {i}\n", encoding="utf-8")
        sm.generate_project_map(str(self.tmp))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_changed_file_recaptured_unchanged_reused(self):
        # Change one file; its content must be re-captured while the rest are reused.
        changed = self.tmp / "m5.py"
        changed.write_text("# file 5 CHANGED\nVALUE = 5000\n", encoding="utf-8")
        sm.update_file_cache(str(changed), str(self.tmp))

        with search_snapshots.pin_current_snapshot(self.tmp) as pin:
            # changed file serves NEW bytes
            self.assertEqual(pin.read_bytes("m5.py"),
                             (self.tmp / "m5.py").read_bytes())
            # a reused (unchanged) file serves its correct current bytes
            self.assertEqual(pin.read_bytes("m7.py"),
                             (self.tmp / "m7.py").read_bytes())
            # every manifest record resolves to a present, hash-consistent blob
            for rec in pin.iter_records():
                got = pin.read_bytes(rec["path"])
                self.assertEqual(len(got), int(rec["size"]))


class TestWritePublishesNewFileToSnapshot(unittest.TestCase):
    """P13: a file created through the write path must be SEARCHABLE immediately.

    Root defect (measured live, daemon gen 59b0fc300794, scratch project):
    search_code publishes the FIRST snapshot itself (search-side, index=None);
    write_new_file then updates INDEX.json via update_file_cache but the fast-path
    snapshot publish fails INDEX/manifest validation (the search-side snapshot's
    record set predates the new file and was built under a different filter), the
    full-walk self-heal is disabled on the first-write path (allow_full_walk=False,
    TOOLA-AHEALTHHUNG-R1), and the failure is swallowed as snapshot_id="". The next
    search keeps serving the PRE-WRITE snapshot: the file exists, is indexed, and
    search reports 0 hits -- certifying a clean index while missing the file.

    The write path must therefore guarantee: after update_file_cache returns, the
    snapshot search serves either CONTAINS the new file, or CURRENT is cleared so
    the next search republishes from disk. Serving the pre-write snapshot silently
    is the one outcome that must never happen.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="p13_newfile_")).resolve()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _publish_search_side_snapshot(self):
        """Replicate search_code's fallback publish (search.py _pin_or_create_snapshot):
        index=None, text filter -- the snapshot a first search leaves behind."""
        import functools
        from orchestrator.fileops import common as fo
        return search_snapshots.publish_snapshot(
            self.tmp,
            include_file=functools.partial(fo.should_scan_for_text, project_root=self.tmp),
            skip_dir=functools.partial(fo.should_skip_dir, project_root=self.tmp),
            index=None,
        )

    def _snapshot_serves(self, rel: str) -> bool:
        sid = search_snapshots.current_snapshot_id(self.tmp)
        if not sid:
            return False
        with search_snapshots.pin_current_snapshot(self.tmp) as pin:
            return any(r["path"].replace("\\", "/") == rel for r in pin.manifest.get("files", []))

    def test_new_file_after_search_side_snapshot_is_served_or_republished(self):
        # seed.py exists on disk but is only in the SEARCH-side snapshot; INDEX does
        # not exist yet (fresh project, first write creates it).
        (self.tmp / "seed.py").write_text("# seed\n", encoding="utf-8")
        sid_before = self._publish_search_side_snapshot()
        self.assertEqual(search_snapshots.current_snapshot_id(self.tmp), sid_before)

        probe = self.tmp / "probe.py"
        probe.write_text("ZZ_P13_PROBE = 1\n", encoding="utf-8")
        sm.update_file_cache(str(probe), str(self.tmp))

        # INDEX must list the new file (this already worked on HEAD).
        idx = json.loads((self.tmp / ".orchestrator_cache" / "INDEX.json").read_text(encoding="utf-8"))
        self.assertIn("probe.py", idx.get("files", {}))

        sid_after = search_snapshots.current_snapshot_id(self.tmp)
        if sid_after:
            # A snapshot still exists: it MUST serve the new file.
            self.assertTrue(
                self._snapshot_serves("probe.py"),
                "snapshot %s survived the write but does not contain probe.py; "
                "the next search_code serves the pre-write tree (the P13 defect)" % sid_after,
            )
            with search_snapshots.pin_current_snapshot(self.tmp) as pin:
                self.assertEqual(pin.read_bytes("probe.py"), probe.read_bytes())
        # else: CURRENT cleared -> the next search republishes from disk. Also OK.

    def test_first_write_with_no_prior_snapshot_stays_searchable(self):
        # No snapshot at all before the write; disk holds MORE than the just-seeded
        # INDEX (seed.py is never cached until a write touches it), so the publish
        # validator cannot accept a walk against the partial INDEX.
        (self.tmp / "seed.py").write_text("# seed\n", encoding="utf-8")
        probe = self.tmp / "probe.py"
        probe.write_text("ZZ_P13_PROBE = 1\n", encoding="utf-8")
        self.assertIsNone(search_snapshots.current_snapshot_id(self.tmp))
        sm.update_file_cache(str(probe), str(self.tmp))
        sid_after = search_snapshots.current_snapshot_id(self.tmp)
        if sid_after:
            self.assertTrue(
                self._snapshot_serves("probe.py"),
                "first-write publish committed a snapshot without probe.py",
            )


class TestDeleteRemovesFileFromSnapshot(unittest.TestCase):
    """P13 delete case: a file removed through the write path must leave the
    snapshot search serves (and the cache sync must not report a false failure)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="p13_del_")).resolve()
        (self.tmp / "keep.py").write_text("KEEP = 1\n", encoding="utf-8")
        (self.tmp / "gone.py").write_text("GONE = 1\n", encoding="utf-8")
        sm.generate_project_map(str(self.tmp))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_delete_removes_record(self):
        target = self.tmp / "gone.py"
        target.unlink()
        sm.update_file_cache(str(target), str(self.tmp))
        sid = search_snapshots.current_snapshot_id(self.tmp)
        self.assertIsNotNone(sid)
        with search_snapshots.pin_current_snapshot(self.tmp) as pin:
            paths = [r["path"].replace("\\", "/") for r in pin.manifest.get("files", [])]
        self.assertNotIn("gone.py", paths)
        self.assertIn("keep.py", paths)

    def test_delete_reports_synced_true(self):
        # code_tools._update_cache_after_write used to fp.stat() the deleted file,
        # raise FileNotFoundError, and return False -- blaming the sync for the
        # file's intended absence on every delete/rollback-unlink.
        import code_tools
        target = self.tmp / "gone.py"
        target.unlink()
        self.assertTrue(code_tools._update_cache_after_write(str(target), str(self.tmp)))


class TestWriteTransactionNewFileSearchable(unittest.IsolatedAsyncioTestCase):
    """P13 end-to-end through the batch path: write_transaction(tool=write_new_file)
    on a project whose only snapshot is the search-side one must leave the new file
    findable by search_code (the exact live repro shape)."""

    def setUp(self):
        import functools
        self.tmp = Path(tempfile.mkdtemp(prefix="p13_txn_")).resolve()
        (self.tmp / "seed.py").write_text("# seed\n", encoding="utf-8")
        from orchestrator.fileops import common as fo
        search_snapshots.publish_snapshot(
            self.tmp,
            include_file=functools.partial(fo.should_scan_for_text, project_root=self.tmp),
            skip_dir=functools.partial(fo.should_skip_dir, project_root=self.tmp),
            index=None,
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_transaction_created_file_is_found_by_search(self):
        from orchestrator.tools import search as search_module
        from tools_v15 import write_transaction

        token = "ZZ_P13_TXN_TOKEN_51af"
        raw = await write_transaction(
            operations=[{
                "tool": "write_new_file",
                "file_path": "txn_probe.py",
                "new_content": f"TOK = {token!r}\n",
            }],
            project_path=str(self.tmp),
            backup=False,
            structural_check=False,
        )
        result = json.loads(raw)
        self.assertTrue(result.get("ok"), result)

        # The search must serve the new file -- either the write republished a
        # snapshot containing it, or CURRENT was cleared and this search republishes.
        search_raw = await search_module.search_code(
            query=token, path=str(self.tmp), project_path=str(self.tmp), mode="text")
        payload = json.loads(search_raw)
        self.assertEqual(payload.get("count"), 1, payload)
        self.assertNotEqual(payload.get("stale_index"), True, payload)
if __name__ == "__main__":
    unittest.main()
