from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from orchestrator import search_snapshots as snapshots
from orchestrator.tools import search as search_module


class TestSearchSnapshotIntegration(unittest.IsolatedAsyncioTestCase):
    """A2 search contracts: one response is backed by exactly one immutable generation."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="search_snapshot_integration_"))
        self._write_generation("GEN_A")
        self.first = self._publish()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_generation(self, tag: str) -> None:
        (self.root / "a.py").write_text(f"marker = '{tag}'\n", encoding="utf-8")
        (self.root / "b.py").write_text(f"marker = '{tag}'\n", encoding="utf-8")

    def _publish(self) -> str:
        return snapshots.publish_snapshot(
            self.root,
            include_file=lambda path: path.suffix == ".py",
            skip_dir=lambda name: name.startswith("."),
            index=None,
        )

    async def _search(self) -> dict:
        raw = await search_module.search_code(
            query="GEN_",
            path=str(self.root),
            mode="text",
            max_results=10,
        )
        return json.loads(raw)

    async def _wait_event(self, event: threading.Event, timeout: float = 3.0) -> None:
        observed = await asyncio.wait_for(asyncio.to_thread(event.wait, timeout), timeout + 0.5)
        self.assertTrue(observed, "timed out waiting for snapshot reader")

    async def test_response_has_one_top_level_snapshot_id(self) -> None:
        raw = await search_module.search_code(
            query="GEN_",
            path=str(self.root),
            mode="text",
            max_results=10,
        )
        payload = json.loads(raw)

        self.assertEqual(payload["snapshot_id"], self.first)
        self.assertEqual(raw.count('"snapshot_id"'), 1)
        self.assertEqual(payload["count"], 2)
        self.assertTrue(all("snapshot_id" not in result for result in payload["results"]))

    async def test_refresh_during_scan_returns_all_old_or_all_new_never_mixed(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        real_read = snapshots.SnapshotPin.read_bytes
        blocked = False
        blocked_lock = threading.Lock()

        def delayed_read(pin: snapshots.SnapshotPin, relative_path: str) -> bytes:
            nonlocal blocked
            should_block = False
            with blocked_lock:
                if pin.snapshot_id == self.first and not blocked:
                    blocked = True
                    should_block = True
            if should_block:
                entered.set()
                if not release.wait(3.0):
                    raise TimeoutError("test did not release pinned search")
            return real_read(pin, relative_path)

        with mock.patch.object(snapshots.SnapshotPin, "read_bytes", delayed_read):
            old_task = asyncio.create_task(self._search())
            await self._wait_event(entered)
            self._write_generation("GEN_B")
            second = await asyncio.to_thread(self._publish)
            release.set()
            old_payload = await asyncio.wait_for(old_task, 4.0)

        new_payload = await self._search()
        self.assertNotEqual(self.first, second)
        self.assertEqual(old_payload["snapshot_id"], self.first)
        self.assertEqual({result["match"] for result in old_payload["results"]}, {"marker = 'GEN_A'"})
        self.assertEqual(new_payload["snapshot_id"], second)
        self.assertEqual({result["match"] for result in new_payload["results"]}, {"marker = 'GEN_B'"})

    async def test_fifty_waiters_survive_refresh_and_twenty_cancellations(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        real_read = snapshots.SnapshotPin.read_bytes
        first_read = True
        read_lock = threading.Lock()

        def delayed_read(pin: snapshots.SnapshotPin, relative_path: str) -> bytes:
            nonlocal first_read
            should_block = False
            with read_lock:
                if pin.snapshot_id == self.first and first_read:
                    first_read = False
                    should_block = True
            if should_block:
                entered.set()
                if not release.wait(3.0):
                    raise TimeoutError("test did not release shared producer")
            return real_read(pin, relative_path)

        with mock.patch.object(snapshots.SnapshotPin, "read_bytes", delayed_read):
            tasks = [asyncio.create_task(self._search()) for _ in range(50)]
            await self._wait_event(entered)
            for task in tasks[:20]:
                task.cancel()
            self._write_generation("GEN_B")
            second = await asyncio.to_thread(self._publish)

            lease_dir = snapshots.snapshot_store_path(self.root) / "leases" / self.first
            self.assertTrue(list(lease_dir.glob("*.lease")), "producer pin released before scan completed")
            release.set()
            outcomes = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                5.0,
            )

        cancelled = [item for item in outcomes if isinstance(item, asyncio.CancelledError)]
        survivors = [item for item in outcomes if isinstance(item, dict)]
        self.assertEqual(len(cancelled), 20)
        self.assertEqual(len(survivors), 30)
        self.assertTrue(all(item["snapshot_id"] == self.first for item in survivors))
        self.assertTrue(all({r["match"] for r in item["results"]} == {"marker = 'GEN_A'"} for item in survivors))
        self.assertNotEqual(self.first, second)
        for _ in range(100):
            if not list(lease_dir.glob("*.lease")):
                break
            await asyncio.sleep(0.01)
        self.assertFalse(list(lease_dir.glob("*.lease")), "producer lease leaked after completion")

    async def test_same_query_different_snapshot_ids_use_distinct_producers(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        real_read = snapshots.SnapshotPin.read_bytes
        seen: list[str] = []
        seen_lock = threading.Lock()
        first_blocked = False

        def tracked_read(pin: snapshots.SnapshotPin, relative_path: str) -> bytes:
            nonlocal first_blocked
            with seen_lock:
                seen.append(pin.snapshot_id)
                should_block = pin.snapshot_id == self.first and not first_blocked
                if should_block:
                    first_blocked = True
            if should_block:
                entered.set()
                if not release.wait(3.0):
                    raise TimeoutError("test did not release old generation")
            return real_read(pin, relative_path)

        with mock.patch.object(snapshots.SnapshotPin, "read_bytes", tracked_read):
            old_task = asyncio.create_task(self._search())
            await self._wait_event(entered)
            self._write_generation("GEN_B")
            second = await asyncio.to_thread(self._publish)
            new_payload = await asyncio.wait_for(self._search(), 3.0)
            release.set()
            old_payload = await asyncio.wait_for(old_task, 4.0)

        self.assertEqual(old_payload["snapshot_id"], self.first)
        self.assertEqual(new_payload["snapshot_id"], second)
        self.assertIn(self.first, seen)
        self.assertIn(second, seen)

    async def test_fifty_duplicate_callers_acquire_one_pin(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        pin_count = 0
        pin_lock = threading.Lock()
        real_pin = search_module._pin_or_create_snapshot
        real_read = snapshots.SnapshotPin.read_bytes

        def counted_pin(*args, **kwargs):
            nonlocal pin_count
            with pin_lock:
                pin_count += 1
            return real_pin(*args, **kwargs)

        def blocked_read(pin, relative_path):
            entered.set()
            if not release.wait(3.0):
                raise TimeoutError("test did not release duplicate producer")
            return real_read(pin, relative_path)

        with mock.patch.object(search_module, "_pin_or_create_snapshot", side_effect=counted_pin), \
             mock.patch.object(snapshots.SnapshotPin, "read_bytes", blocked_read):
            tasks = [asyncio.create_task(self._search()) for _ in range(50)]
            await self._wait_event(entered)
            await asyncio.sleep(0.05)
            self.assertEqual(pin_count, 1)
            release.set()
            payloads = await asyncio.wait_for(asyncio.gather(*tasks), 5.0)

        self.assertEqual(pin_count, 1)
        self.assertTrue(all(payload["snapshot_id"] == self.first for payload in payloads))

    async def test_current_swap_between_token_and_pin_retries_once(self) -> None:
        real_pin = search_module._pin_or_create_snapshot
        pin_calls = 0
        second = ""

        def swap_before_first_pin(root, expected_id=None):
            nonlocal pin_calls, second
            pin_calls += 1
            if pin_calls == 1:
                self._write_generation("GEN_B")
                second = self._publish()
            return real_pin(root, expected_id=expected_id)

        with mock.patch.object(
            search_module, "_pin_or_create_snapshot", side_effect=swap_before_first_pin,
        ):
            payload = await self._search()

        self.assertEqual(pin_calls, 2)
        self.assertNotEqual(second, self.first)
        self.assertEqual(payload["snapshot_id"], second)
        self.assertEqual(
            {result["match"] for result in payload["results"]},
            {"marker = 'GEN_B'"},
        )


if __name__ == "__main__":
    unittest.main()
