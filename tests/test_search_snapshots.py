from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import search_snapshots as snapshots
from orchestrator.tools import search as search_module
from orchestrator.server import executor_lanes


def _reset_lane_suite() -> None:
    asyncio.run(executor_lanes.shutdown_executor_lanes(drain_timeout=0))
    executor_lanes._reset_executor_lanes_for_tests()


def setUpModule() -> None:
    _reset_lane_suite()


def tearDownModule() -> None:
    _reset_lane_suite()


class TestSearchSnapshotStore(unittest.TestCase):
    """A2 storage contracts: readers see one immutable generation, even during publish."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="search_snapshot_store_"))
        (self.root / "a.py").write_text("GEN_A\n", encoding="utf-8")
        (self.root / "b.py").write_text("GEN_A\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _publish(self) -> str:
        return snapshots.publish_snapshot(
            self.root,
            include_file=lambda path: path.suffix == ".py",
            skip_dir=lambda name: name.startswith("."),
            index=None,
        )

    def _index(self, groups: dict[str, str] | None = None) -> dict:
        groups = groups or {}
        files = {}
        for relative in ("a.py", "b.py"):
            path = self.root / relative
            payload = path.read_bytes()
            stat_result = path.stat()
            files[relative] = {
                "hash": hashlib.md5(payload).hexdigest(),
                "size_bytes": len(payload),
                "mtime": stat_result.st_mtime,
                "group": groups.get(relative, "source"),
            }
        return {"schema_version": 2, "files": files}

    def _read_pair(self) -> tuple[str, str, str]:
        with snapshots.pin_current_snapshot(self.root) as pin:
            return (
                pin.snapshot_id,
                pin.read_bytes("a.py").decode("utf-8").strip(),
                pin.read_bytes("b.py").decode("utf-8").strip(),
            )

    def _make_legacy(self, snapshot_id: str) -> str:
        store = snapshots.snapshot_store_path(self.root)
        generation = store / "generations" / snapshot_id
        manifest = json.loads((generation / "MANIFEST.json").read_text(encoding="utf-8"))
        identity = dict(manifest)
        identity.pop("snapshot_id")
        identity.pop("publication_sequence")
        legacy_id = "v1-" + hashlib.sha256(
            snapshots._canonical_json_bytes(identity)
        ).hexdigest()
        identity["snapshot_id"] = legacy_id
        (generation / "MANIFEST.json").write_bytes(snapshots._canonical_json_bytes(identity))
        (generation / "READY").write_text(legacy_id + "\n", encoding="ascii")
        generation.rename(store / "generations" / legacy_id)
        return legacy_id

    def test_manifest_is_deterministic_and_contains_required_metadata(self) -> None:
        first = self._publish()
        second = self._publish()
        self.assertNotEqual(first, second)

        with snapshots.pin_current_snapshot(self.root) as pin:
            self.assertEqual(pin.snapshot_id, second)
            self.assertEqual(pin.manifest["publication_sequence"], 2)
            records = pin.manifest["files"]
            self.assertEqual([record["path"] for record in records], ["a.py", "b.py"])
            for record in records:
                payload = (self.root / record["path"]).read_bytes()
                self.assertEqual(record["size"], len(payload))
                self.assertIsInstance(record["mtime_ns"], int)
                self.assertEqual(record["sha256"], hashlib.sha256(payload).hexdigest())
                self.assertEqual(record["blob_sha256"], record["sha256"])

    def test_warm_pin_does_not_read_all_snapshot_blobs(self) -> None:
        (self.root / "a.py").unlink()
        (self.root / "b.py").unlink()
        for index in range(1000):
            (self.root / f"warm_{index:04d}.py").write_text("value = 1\n", encoding="utf-8")
        snapshot_id = self._publish()
        blob_reads = 0
        real_read_bytes = Path.read_bytes

        def counted_read(path):
            nonlocal blob_reads
            if "blobs" in path.parts:
                blob_reads += 1
            return real_read_bytes(path)

        with mock.patch.object(Path, "read_bytes", counted_read):
            pin = snapshots.pin_current_snapshot(self.root)
        try:
            self.assertEqual(pin.snapshot_id, snapshot_id)
            self.assertEqual(len(pin.manifest["files"]), 1000)
            self.assertEqual(blob_reads, 0)
        finally:
            pin.release()

    def test_old_generation_stays_readable_until_last_pin_releases(self) -> None:
        expected_a = (self.root / "a.py").read_bytes()
        expected_b = (self.root / "b.py").read_bytes()
        first = self._publish()
        old_pin = snapshots.pin_current_snapshot(self.root)
        old_manifest_bytes = (old_pin.generation_path / "MANIFEST.json").read_bytes()

        (self.root / "a.py").write_text("GEN_B\n", encoding="utf-8")
        (self.root / "b.py").write_text("GEN_B\n", encoding="utf-8")
        second = self._publish()
        self.assertNotEqual(first, second)
        self.assertEqual(old_pin.read_bytes("a.py"), expected_a)
        self.assertEqual(old_pin.read_bytes("b.py"), expected_b)

        snapshots.collect_garbage(self.root, keep_last=1)
        self.assertTrue(old_pin.generation_path.exists())
        self.assertEqual((old_pin.generation_path / "MANIFEST.json").read_bytes(), old_manifest_bytes)

        old_generation = old_pin.generation_path
        old_pin.release()
        snapshots.collect_garbage(self.root, keep_last=1)
        self.assertFalse(old_generation.exists())
        self.assertEqual(self._read_pair(), (second, "GEN_B", "GEN_B"))

    def test_releasing_last_reader_keeps_generation_lease_directory(self) -> None:
        snapshot_id = self._publish()
        lease_dir = (
            snapshots.snapshot_store_path(self.root)
            / "leases"
            / snapshot_id
        )

        with snapshots.pin_current_snapshot(self.root):
            self.assertTrue(lease_dir.is_dir())

        self.assertTrue(lease_dir.is_dir())
        self.assertEqual(list(lease_dir.iterdir()), [])

    def test_gc_prunes_empty_lease_directory_for_retired_generation(self) -> None:
        first = self._publish()
        with snapshots.pin_current_snapshot(self.root):
            pass
        lease_dir = (
            snapshots.snapshot_store_path(self.root)
            / "leases"
            / first
        )
        self.assertTrue(lease_dir.is_dir())

        (self.root / "a.py").write_text("GEN_B\n", encoding="utf-8")
        (self.root / "b.py").write_text("GEN_B\n", encoding="utf-8")
        second = self._publish()
        self.assertNotEqual(first, second)

        result = snapshots.collect_garbage(self.root, keep_last=1)

        self.assertIn(first, result["deleted"])
        self.assertFalse(lease_dir.exists())

    def test_current_replace_failure_preserves_previous_generation(self) -> None:
        first = self._publish()
        (self.root / "a.py").write_text("GEN_B\n", encoding="utf-8")

        with mock.patch.object(
            snapshots,
            "_replace_current_pointer",
            side_effect=PermissionError("simulated sharing violation"),
        ):
            with self.assertRaises(PermissionError):
                self._publish()

        self.assertEqual(snapshots.current_snapshot_id(self.root), first)
        self.assertEqual(self._read_pair(), (first, "GEN_A", "GEN_A"))

    def test_interrupted_staging_recovery_keeps_last_current(self) -> None:
        first = self._publish()
        store = snapshots.snapshot_store_path(self.root)
        abandoned = store / "staging" / "interrupted-build"
        abandoned.mkdir(parents=True)
        (abandoned / "MANIFEST.json.tmp").write_text("{", encoding="utf-8")
        (store / ".CURRENT.partial").write_text("torn", encoding="utf-8")

        recovery = snapshots.recover_snapshot_store(self.root)

        self.assertEqual(recovery["snapshot_id"], first)
        self.assertGreaterEqual(recovery["removed_staging"], 1)
        self.assertFalse(abandoned.exists())
        self.assertEqual(self._read_pair(), (first, "GEN_A", "GEN_A"))

    def test_corrupt_current_uses_publication_sequence_not_mtime_or_content_id(self) -> None:
        first = self._publish()
        (self.root / "a.py").write_text("GEN_B\n", encoding="utf-8")
        (self.root / "b.py").write_text("GEN_B\n", encoding="utf-8")
        second = self._publish()
        store = snapshots.snapshot_store_path(self.root)
        generation_artifacts = {
            path.relative_to(store).as_posix(): path.read_bytes()
            for snapshot_id in (first, second)
            for path in (store / "generations" / snapshot_id).iterdir()
            if path.is_file()
        }
        expected = second
        other = first
        (store / "generations" / expected).touch()
        old_time = 1_000_000_000
        future_time = 4_000_000_000
        os.utime(store / "generations" / expected, (old_time, old_time))
        os.utime(store / "generations" / other, (future_time, future_time))
        (store / "CURRENT").write_text("{corrupt", encoding="utf-8")

        recovery = snapshots.recover_snapshot_store(self.root)

        self.assertTrue(recovery["recovered_current"])
        self.assertEqual(snapshots.RECOVERY_ORDER, "authenticated_publication_sequence")
        self.assertEqual(recovery["snapshot_id"], expected)
        self.assertEqual(
            generation_artifacts,
            {
                path.relative_to(store).as_posix(): path.read_bytes()
                for snapshot_id in (first, second)
                for path in (store / "generations" / snapshot_id).iterdir()
                if path.is_file()
            },
        )

    def test_recovery_rejects_same_size_corrupt_blob_and_uses_valid_generation(self) -> None:
        first = self._publish()
        (self.root / "a.py").write_text("GEN_B\n", encoding="utf-8")
        (self.root / "b.py").write_text("GEN_B\n", encoding="utf-8")
        second = self._publish()
        store = snapshots.snapshot_store_path(self.root)
        corrupt_id = max(first, second)
        valid_id = min(first, second)
        manifest = json.loads(
            (store / "generations" / corrupt_id / "MANIFEST.json").read_text(encoding="utf-8")
        )
        digest = manifest["files"][0]["blob_sha256"]
        blob = snapshots._blob_path(store, digest)
        blob.write_bytes(b"X" * len(blob.read_bytes()))
        (store / "CURRENT").write_text("{corrupt", encoding="utf-8")

        recovery = snapshots.recover_snapshot_store(self.root)

        self.assertEqual(recovery["snapshot_id"], valid_id)
        with snapshots.pin_current_snapshot(self.root) as pin:
            self.assertEqual(pin.snapshot_id, valid_id)

    def test_recovery_preserves_single_valid_legacy_generation(self) -> None:
        legacy_id = self._make_legacy(self._publish())
        store = snapshots.snapshot_store_path(self.root)
        (store / "CURRENT").write_text("{corrupt", encoding="utf-8")

        recovery = snapshots.recover_snapshot_store(self.root)

        self.assertEqual(recovery["snapshot_id"], legacy_id)

    def test_recovery_fails_loud_for_multiple_legacy_generations(self) -> None:
        first = self._make_legacy(self._publish())
        (self.root / "a.py").write_text("GEN_B\n", encoding="utf-8")
        second = self._make_legacy(self._publish())
        self.assertNotEqual(first, second)
        store = snapshots.snapshot_store_path(self.root)
        (store / "CURRENT").write_text("{corrupt", encoding="utf-8")

        with self.assertRaisesRegex(
            snapshots.SnapshotUnrecoverableError,
            "multiple legacy generations",
        ):
            snapshots.recover_snapshot_store(self.root)

    def test_recovery_skips_malformed_candidate_shape(self) -> None:
        valid_id = self._publish()
        store = snapshots.snapshot_store_path(self.root)
        malformed_id = "v1-" + ("f" * 64)
        malformed = store / "generations" / malformed_id
        malformed.mkdir()
        (malformed / "READY").write_text(malformed_id + "\n", encoding="ascii")
        (malformed / "MANIFEST.json").write_text("[]\n", encoding="utf-8")
        (malformed / "INDEX.json").write_text("{}\n", encoding="utf-8")
        (store / "CURRENT").write_text("{corrupt", encoding="utf-8")

        recovery = snapshots.recover_snapshot_store(self.root)

        self.assertEqual(recovery["snapshot_id"], valid_id)

    def test_corrupt_current_without_valid_generation_fails_loud(self) -> None:
        snapshot_id = self._publish()
        store = snapshots.snapshot_store_path(self.root)
        generation = store / "generations" / snapshot_id
        manifest_before = (generation / "MANIFEST.json").read_bytes()
        (generation / "READY").unlink()
        corrupt_current = b"{corrupt"
        (store / "CURRENT").write_bytes(corrupt_current)

        with self.assertRaisesRegex(
            snapshots.SnapshotUnrecoverableError,
            "no valid READY generation",
        ):
            snapshots.recover_snapshot_store(self.root)

        self.assertEqual((store / "CURRENT").read_bytes(), corrupt_current)
        self.assertEqual((generation / "MANIFEST.json").read_bytes(), manifest_before)
        self.assertTrue(generation.is_dir())

    def test_file_that_changes_during_capture_aborts_publish(self) -> None:
        target = self.root / "a.py"
        original_read = snapshots._read_file_bytes
        mutation_count = 0

        # The watchdog parameter is not decoration: `_read_file_bytes` grew it
        # when long captures became progress-governed, and both call sites pass
        # it positionally. A stub that accepts only `path` raises TypeError
        # before the capture-changed path is ever exercised, so this test would
        # report a mock-signature mismatch instead of the invariant it owns.
        def changing_read(path: Path, watchdog=None) -> bytes:
            nonlocal mutation_count
            payload = original_read(path, watchdog)
            if Path(path) == target:
                mutation_count += 1
                target.write_bytes(payload + (b"x" * mutation_count))
            return payload

        with mock.patch.object(snapshots, "_read_file_bytes", side_effect=changing_read):
            with self.assertRaises(snapshots.SnapshotCaptureChanged):
                self._publish()

        self.assertIsNone(snapshots.current_snapshot_id(self.root))

    def test_unicode_casefold_collision_fails_loud(self) -> None:
        records = [
            {"path": "Cafe\u0301.py", "size": 1, "mtime_ns": 1, "sha256": "a" * 64, "blob_sha256": "a" * 64},
            {"path": "CAFÉ.py", "size": 1, "mtime_ns": 1, "sha256": "b" * 64, "blob_sha256": "b" * 64},
        ]
        with self.assertRaises(snapshots.SnapshotPathCollision):
            snapshots._validate_manifest_paths(records)

    def test_fifty_readers_during_refresh_never_mix_generations(self) -> None:
        first = self._publish()
        (self.root / "a.py").write_text("GEN_B\n", encoding="utf-8")
        (self.root / "b.py").write_text("GEN_B\n", encoding="utf-8")

        publish_ready = threading.Event()
        allow_publish = threading.Event()
        real_replace = snapshots._replace_current_pointer

        def delayed_replace(store: Path, snapshot_id: str, manifest_sha256: str) -> None:
            publish_ready.set()
            if not allow_publish.wait(3.0):
                raise TimeoutError("test did not release CURRENT replacement")
            real_replace(store, snapshot_id, manifest_sha256)

        with mock.patch.object(snapshots, "_replace_current_pointer", side_effect=delayed_replace):
            writer = threading.Thread(target=self._publish, daemon=True)
            writer.start()
            self.assertTrue(publish_ready.wait(3.0))
            with concurrent.futures.ThreadPoolExecutor(max_workers=25) as pool:
                before = list(pool.map(lambda _: self._read_pair(), range(25)))
            allow_publish.set()
            writer.join(3.0)
            self.assertFalse(writer.is_alive())

        second = snapshots.current_snapshot_id(self.root)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        with concurrent.futures.ThreadPoolExecutor(max_workers=25) as pool:
            after = list(pool.map(lambda _: self._read_pair(), range(25)))

        outcomes = before + after
        self.assertEqual(len(outcomes), 50)
        self.assertTrue(all(a == b for _, a, b in outcomes), outcomes)
        self.assertEqual({row[0] for row in before}, {first})
        self.assertEqual({row[0] for row in after}, {second})

    def test_pin_reads_index_from_same_generation(self) -> None:
        first_index = self._index({"a.py": "source", "b.py": "source"})
        first = snapshots.publish_snapshot(
            self.root,
            include_file=lambda path: path.suffix == ".py",
            skip_dir=lambda name: name.startswith("."),
            index=first_index,
        )
        old_pin = snapshots.pin_current_snapshot(self.root)
        (self.root / "a.py").write_text("GEN_B\n", encoding="utf-8")
        second_index = self._index({"a.py": "changed", "b.py": "test"})
        second = snapshots.publish_snapshot(
            self.root,
            include_file=lambda path: path.suffix == ".py",
            skip_dir=lambda name: name.startswith("."),
            index=second_index,
        )
        try:
            self.assertNotEqual(first, second)
            self.assertEqual(old_pin.read_index(), first_index)
            with snapshots.pin_current_snapshot(self.root) as new_pin:
                self.assertEqual(new_pin.snapshot_id, second)
                self.assertEqual(new_pin.read_index(), second_index)
        finally:
            old_pin.release()

    def test_index_manifest_mismatch_preserves_current(self) -> None:
        first = self._publish()
        (self.root / "a.py").write_text("GEN_B\n", encoding="utf-8")
        bad_index = self._index()
        bad_index["files"]["a.py"]["hash"] = "0" * 32

        with self.assertRaises(snapshots.SnapshotIntegrityError):
            snapshots.publish_snapshot(
                self.root,
                include_file=lambda path: path.suffix == ".py",
                skip_dir=lambda name: name.startswith("."),
                index=bad_index,
            )

        self.assertEqual(snapshots.current_snapshot_id(self.root), first)
        self.assertEqual(self._read_pair(), (first, "GEN_A", "GEN_A"))

    def test_duplicate_exact_manifest_path_fails_loud(self) -> None:
        record = {"path": "a.py", "size": 1, "mtime_ns": 1, "sha256": "a" * 64, "blob_sha256": "a" * 64}
        with self.assertRaises(snapshots.SnapshotPathCollision):
            snapshots._validate_manifest_paths([dict(record), dict(record)])

    def test_index_hash_is_rechecked_when_pin_reads(self) -> None:
        snapshot_id = snapshots.publish_snapshot(
            self.root,
            include_file=lambda path: path.suffix == ".py",
            skip_dir=lambda name: name.startswith("."),
            index=self._index(),
        )
        with snapshots.pin_current_snapshot(self.root) as pin:
            (pin.generation_path / "INDEX.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(snapshots.SnapshotIntegrityError):
                pin.read_index()
        self.assertEqual(snapshots.current_snapshot_id(self.root), snapshot_id)

    def test_windows_liveness_probe_never_signals_target_process(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            alive, identity = snapshots._process_status(child.pid)
            self.assertTrue(alive)
            self.assertTrue(identity)
            for _ in range(5):
                self.assertTrue(snapshots._pid_is_alive(child.pid, identity))
            self.assertIsNone(child.poll(), "liveness probe terminated the target process")
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)

    def test_recent_incomplete_writer_lock_is_not_stolen(self) -> None:
        self._publish()
        store = snapshots.snapshot_store_path(self.root)
        lock_path = store / "WRITE.lock"
        lock_path.write_bytes(b"{")
        try:
            with self.assertRaises(snapshots.SnapshotWriterBusy):
                with snapshots._writer_lease(store, timeout_seconds=0.05):
                    self.fail("a recent incomplete writer lock was stolen")
            self.assertTrue(lock_path.exists())
        finally:
            lock_path.unlink(missing_ok=True)

    def test_cross_process_pin_blocks_gc_until_child_releases(self) -> None:
        first = self._publish()
        store = snapshots.snapshot_store_path(self.root)
        ready = self.root / "child_ready.txt"
        release = self.root / "child_release.txt"
        child_code = """
import sys, time
from pathlib import Path
from orchestrator import search_snapshots as snapshots
root = Path(sys.argv[1])
ready = Path(sys.argv[2])
release = Path(sys.argv[3])
pin = snapshots.pin_current_snapshot(root)
ready.write_text(pin.snapshot_id, encoding='utf-8')
deadline = time.time() + 10
while not release.exists() and time.time() < deadline:
    time.sleep(0.01)
if not release.exists():
    raise SystemExit(3)
pin.read_index()
pin.release()
"""
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(self.root), str(ready), str(release)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for _ in range(500):
                if ready.exists() or child.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertTrue(ready.exists(), child.stderr.read() if child.poll() is not None else "child pin timeout")
            self.assertEqual(ready.read_text(encoding="utf-8"), first)

            (self.root / "a.py").write_text("GEN_B\n", encoding="utf-8")
            (self.root / "b.py").write_text("GEN_B\n", encoding="utf-8")
            second = self._publish()
            self.assertNotEqual(first, second)
            snapshots.collect_garbage(self.root, keep_last=1)
            old_generation = store / "generations" / first
            self.assertTrue(old_generation.exists(), "GC deleted a generation pinned by another process")

            release.write_text("release", encoding="utf-8")
            stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, msg=f"stdout={stdout}\nstderr={stderr}")
            snapshots.collect_garbage(self.root, keep_last=1)
            self.assertFalse(old_generation.exists())
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)

    def test_text_search_stops_reading_snapshot_blobs_at_max_results(self) -> None:
        (self.root / "a.py").write_text("needle\n", encoding="utf-8")
        (self.root / "b.py").write_text("needle\n", encoding="utf-8")
        for index in range(8):
            (self.root / f"match_{index}.py").write_text("needle\n", encoding="utf-8")
        self._publish()
        read_count = 0
        real_read = snapshots.SnapshotPin.read_bytes

        def counted_read(pin, relative_path):
            nonlocal read_count
            read_count += 1
            return real_read(pin, relative_path)

        with mock.patch.object(snapshots.SnapshotPin, "read_bytes", counted_read):
            payload = json.loads(asyncio.run(search_module.search_code(
                query="needle",
                path=str(self.root),
                mode="smart",
                max_results=2,
            )))
        self.assertEqual(payload["count"], 2)
        self.assertEqual(read_count, 2)

    def test_streamed_smart_search_skips_excluded_record_before_valid_text(self) -> None:
        (self.root / "a.py").unlink()
        (self.root / "b.py").unlink()
        (self.root / "a.png").write_bytes(b"needle\n")
        (self.root / "b.py").write_text("needle\n", encoding="utf-8")
        snapshots.publish_snapshot(
            self.root, include_file=lambda path: True,
            skip_dir=lambda name: name.startswith("."), index=None,
        )
        read_paths = []
        real_read = snapshots.SnapshotPin.read_bytes

        def tracked_read(pin, relative_path):
            read_paths.append(relative_path)
            return real_read(pin, relative_path)

        with mock.patch.object(snapshots.SnapshotPin, "read_bytes", tracked_read):
            payload = json.loads(asyncio.run(search_module.search_code(
                query="needle", path=str(self.root), mode="smart", max_results=1,
            )))
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["file"], "b.py")
        self.assertEqual(read_paths, ["b.py"])


if __name__ == "__main__":
    unittest.main()
