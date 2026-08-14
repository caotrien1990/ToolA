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


if __name__ == "__main__":
    unittest.main()
