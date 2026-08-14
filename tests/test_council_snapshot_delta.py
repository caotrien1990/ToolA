from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from orchestrator.council.snapshots import (
    VersionedSnapshotCache,
    read_jsonl_tail,
    read_tail_bytes,
)


class VersionedSnapshotCacheTests(unittest.TestCase):
    def test_unchanged_content_keeps_version_and_returns_not_modified(self):
        cache = VersionedSnapshotCache(ttl_seconds=0, history_limit=8)
        first = cache.get(
            "activity",
            lambda: {"generated_at": "one", "agents": [{"id": "a", "status": "idle"}]},
            generation="gen-a",
        )
        cache.invalidate()
        second = cache.get(
            "activity",
            lambda: {"generated_at": "two", "agents": [{"id": "a", "status": "idle"}]},
            generation="gen-a",
        )
        self.assertEqual(first["version"], second["version"])
        selected = cache.select(
            "activity",
            second,
            since_version=first["version"],
            client_generation="gen-a",
            generation="gen-a",
            collection_field="agents",
            id_field="id",
        )
        self.assertTrue(selected.not_modified)

    def test_delta_contains_only_changed_agents_and_removed_ids(self):
        cache = VersionedSnapshotCache(ttl_seconds=0, history_limit=8)
        first = cache.get(
            "state",
            lambda: {"agents": [
                {"id": "a", "status": "idle"},
                {"id": "b", "status": "idle"},
            ]},
            generation="gen-a",
        )
        cache.invalidate()
        second = cache.get(
            "state",
            lambda: {"agents": [
                {"id": "a", "status": "running"},
                {"id": "c", "status": "idle"},
            ]},
            generation="gen-a",
        )
        selected = cache.select(
            "state",
            second,
            since_version=first["version"],
            client_generation="gen-a",
            generation="gen-a",
            collection_field="agents",
            id_field="id",
        )
        self.assertFalse(selected.not_modified)
        self.assertFalse(selected.payload["full"])
        self.assertTrue(selected.payload["delta"])
        self.assertEqual(
            {item["id"] for item in selected.payload["agents"]},
            {"a", "c"},
        )
        self.assertEqual(selected.payload["removed_ids"], ["b"])

    def test_returned_payload_cannot_mutate_cached_snapshot(self):
        cache = VersionedSnapshotCache(ttl_seconds=10, history_limit=8)
        first = cache.get(
            'state',
            lambda: {'agents': [{'id': 'a', 'status': 'idle'}]},
            generation='g1',
        )
        first['agents'][0]['status'] = 'corrupt'
        second = cache.get(
            'state',
            lambda: {'agents': [{'id': 'a', 'status': 'unused'}]},
            generation='g1',
        )
        self.assertEqual(second['agents'][0]['status'], 'idle')

    def test_generation_mismatch_and_history_miss_fall_back_to_full(self):
        cache = VersionedSnapshotCache(ttl_seconds=0, history_limit=1)
        first = cache.get("state", lambda: {"agents": [{"id": "a", "n": 1}]}, generation="g1")
        cache.invalidate()
        second = cache.get('state', lambda: {'agents': [{'id': 'a', 'n': 2}]}, generation='g1')
        cache.invalidate()
        third = cache.get('state', lambda: {'agents': [{'id': 'a', 'n': 3}]}, generation='g1')

        mismatch = cache.select(
            "state", third, since_version=third["version"],
            client_generation="old", generation="g1",
            collection_field="agents", id_field="id",
        )
        self.assertTrue(mismatch.payload["full"])
        self.assertEqual(mismatch.payload["fallback_reason"], "generation_mismatch")

        evicted = cache.select(
            "state", third, since_version=first["version"],
            client_generation="g1", generation="g1",
            collection_field="agents", id_field="id",
        )
        self.assertTrue(evicted.payload["full"])
        self.assertEqual(evicted.payload["fallback_reason"], "history_miss")
        self.assertNotEqual(second["version"], third["version"])

    def test_concurrent_cold_get_builds_once(self):
        cache = VersionedSnapshotCache(ttl_seconds=10, history_limit=8)
        calls = 0
        lock = threading.Lock()

        def builder():
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.05)
            return {"agents": [{"id": "a", "status": "idle"}]}

        with ThreadPoolExecutor(max_workers=10) as pool:
            rows = list(pool.map(
                lambda _: cache.get("state", builder, generation="g1"),
                range(10),
            ))
        self.assertEqual(calls, 1)
        self.assertEqual(len({row["version"] for row in rows}), 1)

    def test_since_version_without_generation_forces_full_snapshot(self):
        cache = VersionedSnapshotCache(ttl_seconds=10, history_limit=8)
        current = cache.get(
            "state",
            lambda: {"agents": [{"id": "a", "status": "idle"}]},
            generation="g1",
        )
        selected = cache.select(
            "state",
            current,
            since_version=current["version"],
            client_generation="",
            generation="g1",
            collection_field="agents",
            id_field="id",
        )
        self.assertTrue(selected.payload["full"])
        self.assertEqual(
            selected.payload["fallback_reason"], "generation_missing")

    def test_invalidate_prevents_old_inflight_builder_from_overwriting_new(self):
        cache = VersionedSnapshotCache(ttl_seconds=60, history_limit=8)
        old_started = threading.Event()
        release_old = threading.Event()
        new_started = threading.Event()
        release_new = threading.Event()
        new_builds = 0
        build_lock = threading.Lock()

        def old_builder():
            old_started.set()
            self.assertTrue(release_old.wait(timeout=2))
            return {"agents": [{"id": "a", "status": "old"}]}

        def new_builder():
            nonlocal new_builds
            with build_lock:
                new_builds += 1
            new_started.set()
            self.assertTrue(release_new.wait(timeout=2))
            return {"agents": [{"id": "a", "status": "new"}]}

        with ThreadPoolExecutor(max_workers=8) as pool:
            old_future = pool.submit(
                cache.get, "state", old_builder, generation="g1")
            self.assertTrue(old_started.wait(timeout=1))
            cache.invalidate()
            new_futures = [pool.submit(
                cache.get, "state", new_builder, generation="g1")
                for _ in range(6)]
            self.assertTrue(new_started.wait(timeout=1))
            release_new.set()
            new_payloads = [future.result(timeout=2) for future in new_futures]
            release_old.set()
            old_caller_payload = old_future.result(timeout=2)

        # A builder from an invalidated epoch must converge on the new record.
        new_payload = new_payloads[0]
        self.assertEqual(new_payload["agents"][0]["status"], "new")
        self.assertTrue(all(payload == new_payload for payload in new_payloads))
        self.assertEqual(old_caller_payload, new_payload)
        self.assertEqual(new_builds, 1)
        self.assertEqual(cache.status()["epoch"], 1)
        self.assertEqual(cache.status()["builds"], 1)


class TailReaderTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="council-tail-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_read_tail_bytes_seeks_from_end_without_path_read_bytes(self):
        path = self.tempdir / "large.log"
        path.write_bytes(b"x" * 1_000_000 + b"FINAL")
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("full read forbidden")):
            result = read_tail_bytes(path, 64)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.data), 64)
        self.assertTrue(result.data.endswith(b"FINAL"))
        self.assertEqual(result.size, 1_000_005)

    def test_read_jsonl_tail_returns_last_complete_records(self):
        path = self.tempdir / "events.jsonl"
        with path.open("wb") as handle:
            for index in range(200):
                handle.write(json.dumps({"id": index}).encode("utf-8") + b"\n")
        rows = read_jsonl_tail(path, limit=7, max_bytes=256)
        self.assertEqual([row["id"] for row in rows], list(range(193, 200)))


if __name__ == "__main__":
    unittest.main()
