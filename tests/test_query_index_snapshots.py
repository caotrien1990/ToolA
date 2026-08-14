import asyncio
import json
import shutil
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

import code_tools
from orchestrator import search_snapshots


def _count_snapshot_keys(value) -> int:
    if isinstance(value, dict):
        return int("snapshot_id" in value) + sum(
            _count_snapshot_keys(item) for item in value.values()
        )
    if isinstance(value, list):
        return sum(_count_snapshot_keys(item) for item in value)
    return 0


class TestQueryIndexSnapshots(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="query_index_snapshots_"))
        self.sample = self.root / "sample.py"
        self.sample.write_text("VALUE = 'GEN_A'\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _query(self, **kwargs):
        raw = asyncio.run(code_tools.query_index(str(self.root), **kwargs))
        return raw, json.loads(raw)

    def _assert_single_snapshot_key(self, raw: str, payload: dict):
        self.assertEqual(_count_snapshot_keys(payload), 1, msg=payload)
        self.assertEqual(raw.count('"snapshot_id"'), 1, msg=raw)
        self.assertIn("snapshot_id", payload)
        self.assertTrue(all(
            "snapshot_id" not in row for row in payload.get("files", [])
        ))

    def _assert_lease_released(self, snapshot_id: str):
        lease_dir = (
            search_snapshots.snapshot_store_path(self.root)
            / "leases"
            / snapshot_id
        )
        self.assertFalse(
            lease_dir.exists() and any(lease_dir.iterdir()),
            msg=f"reader lease leaked for {snapshot_id}",
        )

    def test_non_authoritative_snapshot_is_rejected_without_refresh(self):
        snapshot_id = search_snapshots.publish_snapshot(self.root, index=None)
        refresh = mock.AsyncMock()
        with mock.patch.object(code_tools, "refresh_project_map", refresh):
            raw, payload = self._query(auto_refresh=False)

        self.assertFalse(payload["ok"], msg=payload)
        self.assertEqual(
            payload["error_name"],
            "SNAPSHOT_INDEX_NOT_AUTHORITATIVE",
        )
        self.assertEqual(payload["category"], "non_authoritative_index")
        self.assertEqual(payload["snapshot_id"], snapshot_id)
        self.assertNotIn("files", payload)
        self._assert_single_snapshot_key(raw, payload)
        refresh.assert_not_awaited()
        self._assert_lease_released(snapshot_id)

    def test_non_authoritative_snapshot_refreshes_and_repins(self):
        original_id = search_snapshots.publish_snapshot(self.root, index=None)

        raw, payload = self._query(auto_refresh=True)

        self.assertTrue(payload["ok"], msg=payload)
        self.assertTrue(payload.get("auto_refreshed"), msg=payload)
        self.assertNotEqual(payload["snapshot_id"], original_id)
        self.assertEqual(
            search_snapshots.current_snapshot_id(self.root),
            payload["snapshot_id"],
        )
        self.assertTrue(any(
            row["path"] == "sample.py" for row in payload["files"]
        ))
        self._assert_single_snapshot_key(raw, payload)
        with search_snapshots.pin_current_snapshot(self.root) as pin:
            self.assertEqual(pin.snapshot_id, payload["snapshot_id"])
            self.assertTrue(pin.manifest["index_authoritative"])
            self.assertIn("sample.py", pin.read_index()["files"])

    def test_success_and_stale_error_emit_one_top_level_snapshot_id(self):
        generated = json.loads(asyncio.run(
            code_tools.generate_project_map(str(self.root))
        ))
        self.assertIn("snapshot_id", generated, msg=generated)

        success_raw, success = self._query(auto_refresh=False)
        self.assertTrue(success["ok"], msg=success)
        self._assert_single_snapshot_key(success_raw, success)

        self.sample.write_text("VALUE = 'GEN_B'\n", encoding="utf-8")
        stale_raw, stale = self._query(auto_refresh=False)
        self.assertFalse(stale["ok"], msg=stale)
        self.assertEqual(stale["error_name"], "STALE_INDEX")
        self.assertEqual(stale["category"], "stale_index")
        self._assert_single_snapshot_key(stale_raw, stale)

    def test_old_pin_remains_readable_through_query_refresh_and_gc(self):
        generated = json.loads(asyncio.run(
            code_tools.generate_project_map(str(self.root))
        ))
        self.assertIn("snapshot_id", generated, msg=generated)
        old_pin = search_snapshots.pin_current_snapshot(self.root)
        old_id = old_pin.snapshot_id
        old_generation = old_pin.generation_path
        try:
            self.sample.write_text("VALUE = 'GEN_B'\n", encoding="utf-8")
            _raw, payload = self._query(auto_refresh=True)
            self.assertTrue(payload["ok"], msg=payload)
            self.assertNotEqual(payload["snapshot_id"], old_id)

            gc_result = search_snapshots.collect_garbage(
                self.root,
                keep_last=0,
            )
            self.assertIn(old_id, gc_result["retained_pinned"])
            self.assertIn(
                b"GEN_A",
                old_pin.read_bytes("sample.py"),
            )
            self.assertTrue(old_generation.exists())
        finally:
            old_pin.release()

        search_snapshots.collect_garbage(self.root, keep_last=0)
        self.assertFalse(old_generation.exists())
        with search_snapshots.pin_current_snapshot(self.root) as current:
            self.assertIn(b"GEN_B", current.read_bytes("sample.py"))

    def test_refresh_json_error_never_falls_back_to_old_index(self):
        snapshot_id = search_snapshots.publish_snapshot(self.root, index=None)
        refresh = mock.AsyncMock(return_value=json.dumps({
            "error": "publish failed",
            "error_name": "SNAPSHOT_PUBLISH_FAILED",
        }))
        with mock.patch.object(code_tools, "refresh_project_map", refresh):
            raw, payload = self._query(auto_refresh=True)

        self.assertFalse(payload["ok"], msg=payload)
        self.assertEqual(payload["error_name"], "AUTO_REFRESH_FAILED")
        self.assertEqual(payload["snapshot_id"], snapshot_id)
        self.assertNotIn("files", payload)
        self._assert_single_snapshot_key(raw, payload)
        refresh.assert_awaited_once()
        self.assertEqual(
            search_snapshots.current_snapshot_id(self.root),
            snapshot_id,
        )
        self._assert_lease_released(snapshot_id)

    def test_snapshot_specific_drift_singleflight_is_generation_scoped(self):
        entered = {"a": threading.Event(), "b": threading.Event()}
        release = {"a": threading.Event(), "b": threading.Event()}
        calls = Counter()
        calls_lock = threading.Lock()

        def slow_delta(_project_path, index_override=None):
            marker = index_override["marker"]
            with calls_lock:
                calls[marker] += 1
            entered[marker].set()
            release[marker].wait(timeout=2.0)
            return json.dumps({"ok": True, "marker": marker})

        async def scenario():
            tasks_a = [asyncio.create_task(
                code_tools._cache_delta_for_snapshot(
                    str(self.root),
                    "snapshot-a",
                    {"marker": "a"},
                )
            ) for _ in range(8)]
            self.assertTrue(await asyncio.to_thread(entered["a"].wait, 1.0))
            tasks_b = [asyncio.create_task(
                code_tools._cache_delta_for_snapshot(
                    str(self.root),
                    "snapshot-b",
                    {"marker": "b"},
                )
            ) for _ in range(8)]
            self.assertTrue(await asyncio.to_thread(entered["b"].wait, 1.0))
            project_key = str(self.root.resolve())
            with code_tools._CACHE_DELTA_FUTURES_LOCK:
                active_keys = set(code_tools._CACHE_DELTA_FUTURES)
            release["a"].set()
            release["b"].set()
            results_a, results_b = await asyncio.gather(
                asyncio.gather(*tasks_a),
                asyncio.gather(*tasks_b),
            )
            await asyncio.sleep(0.05)
            with code_tools._CACHE_DELTA_FUTURES_LOCK:
                leaked = {
                    key for key in code_tools._CACHE_DELTA_FUTURES
                    if key in {
                        (project_key, "snapshot-a"),
                        (project_key, "snapshot-b"),
                    }
                }
            return project_key, active_keys, results_a, results_b, leaked

        try:
            with mock.patch.object(
                code_tools,
                "_cache_delta_sync",
                side_effect=slow_delta,
            ):
                project_key, active_keys, results_a, results_b, leaked = (
                    asyncio.run(scenario())
                )
        finally:
            release["a"].set()
            release["b"].set()

        self.assertEqual(calls, Counter({"a": 1, "b": 1}))
        self.assertIn((project_key, "snapshot-a"), active_keys)
        self.assertIn((project_key, "snapshot-b"), active_keys)
        self.assertEqual(len(set(results_a)), 1)
        self.assertEqual(len(set(results_b)), 1)
        self.assertNotEqual(results_a[0], results_b[0])
        self.assertFalse(leaked)


if __name__ == "__main__":
    unittest.main()