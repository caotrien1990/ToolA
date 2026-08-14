from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from aiohttp.test_utils import make_mocked_request

import orchestrator_server
from orchestrator.council.snapshots import VersionedSnapshotCache


class CouncilSnapshotHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_cache = (
            orchestrator_server._COUNCIL_DASHBOARD_SNAPSHOT_CACHE)
        orchestrator_server._COUNCIL_DASHBOARD_SNAPSHOT_CACHE = (
            VersionedSnapshotCache(ttl_seconds=0, history_limit=8))
        orchestrator_server._COUNCIL_PUBLIC_BROKER_EXPORTED_VERSIONS.clear()

    def tearDown(self):
        orchestrator_server._COUNCIL_DASHBOARD_SNAPSHOT_CACHE = self.old_cache
        orchestrator_server._COUNCIL_PUBLIC_BROKER_EXPORTED_VERSIONS.clear()

    async def test_state_full_304_delta_and_generation_fallback(self):
        status = {"value": "idle"}
        seen_fields = []

        def build(fields):
            seen_fields.append(set(fields or ()))
            return {
                "generated_at": "now",
                "agents": [{
                    "id": "codo",
                    "label": "CodoC",
                    "status": {"label": status["value"]},
                }],
                "context_generation": "ctx",
            }

        path = "/api/council/state?fields=status,unknown"
        with mock.patch.object(
            orchestrator_server, "_council_state_payload", side_effect=build
        ):
            first = await orchestrator_server.handle_council_state(
                make_mocked_request("GET", path))
            initial = json.loads(first.text)
            version = initial["version"]
            generation = initial["generation"]

            same = await orchestrator_server.handle_council_state(
                make_mocked_request(
                    "GET",
                    path + f"&since_version={version}&generation={generation}",
                )
            )

            status["value"] = "running"
            orchestrator_server._council_dashboard_payload_invalidate()
            changed = await orchestrator_server.handle_council_state(
                make_mocked_request(
                    "GET",
                    path + f"&since_version={version}&generation={generation}",
                )
            )
            delta = json.loads(changed.text)

            missing_generation = await orchestrator_server.handle_council_state(
                make_mocked_request(
                    "GET",
                    path + f"&since_version={delta['version']}",
                )
            )
            fallback = json.loads(missing_generation.text)

        self.assertTrue(initial["full"])
        self.assertEqual(initial["fallback_reason"], "initial")
        self.assertEqual(first.headers["X-Council-Version"], str(version))
        self.assertEqual(
            first.headers["X-Orchestrator-Generation"], generation)
        self.assertEqual(same.status, 304)
        self.assertEqual(same.headers["X-Council-Version"], str(version))
        self.assertTrue(delta["delta"])
        self.assertFalse(delta["full"])
        self.assertEqual(
            delta["agents"][0]["status"]["label"], "running")
        self.assertTrue(fallback["full"])
        self.assertEqual(
            fallback["fallback_reason"], "generation_missing")
        self.assertTrue(seen_fields)
        self.assertTrue(all(fields == {"status"} for fields in seen_fields))

    async def test_events_export_once_per_version_and_return_delta(self):
        events = [{
            "id": "evt-1",
            "type": "message",
            "text": "one",
            "mtime": 1.0,
        }]
        exported = mock.Mock(return_value=Path("broker_filtered.jsonl"))

        def build(_council_dir, _limit):
            return [dict(item) for item in events]

        with (
            mock.patch.object(
                orchestrator_server, "_council_public_events",
                side_effect=build,
            ),
            mock.patch.object(
                orchestrator_server, "_council_export_public_broker",
                exported,
            ),
        ):
            first = await orchestrator_server.handle_council_events(
                make_mocked_request("GET", "/api/council/events?limit=40"))
            initial = json.loads(first.text)
            version = initial["version"]
            generation = initial["generation"]

            same = await orchestrator_server.handle_council_events(
                make_mocked_request(
                    "GET",
                    "/api/council/events?limit=40"
                    f"&since_version={version}&generation={generation}",
                )
            )
            events[0]["text"] = "two"
            orchestrator_server._council_dashboard_payload_invalidate()
            changed = await orchestrator_server.handle_council_events(
                make_mocked_request(
                    "GET",
                    "/api/council/events?limit=40"
                    f"&since_version={version}&generation={generation}",
                )
            )

        delta = json.loads(changed.text)
        self.assertEqual(same.status, 304)
        self.assertTrue(delta["delta"])
        self.assertEqual(delta["events"][0]["text"], "two")
        self.assertEqual(exported.call_count, 2)

    async def test_conditional_request_fresh_builds_before_304(self):
        status = {"value": "idle"}

        def build(_fields):
            return {
                "agents": [{"id": "codo", "status": status["value"]}],
            }

        path = "/api/council/state?fields=status"
        with (
            mock.patch.object(
                orchestrator_server, "_council_state_payload", side_effect=build
            ),
            mock.patch.object(
                orchestrator_server, "_council_dashboard_source_revision",
                side_effect=["rev-1", "rev-2"],
            ),
        ):
            first = await orchestrator_server.handle_council_state(
                make_mocked_request("GET", path))
            initial = json.loads(first.text)
            status["value"] = "running"
            changed = await orchestrator_server.handle_council_state(
                make_mocked_request(
                    "GET",
                    path + f"&since_version={initial['version']}"
                    f"&generation={initial['generation']}",
                )
            )

        payload = json.loads(changed.text)
        self.assertEqual(changed.status, 200)
        self.assertTrue(payload["delta"])
        self.assertEqual(payload["agents"][0]["status"], "running")

    def test_fresh_waiters_share_build_and_wait_is_bounded(self):
        cache = VersionedSnapshotCache(ttl_seconds=30)
        builds = 0
        release = threading.Event()

        def build():
            nonlocal builds
            builds += 1
            release.wait(1.0)
            return {"agents": [{"id": "codo", "status": "idle"}]}

        results = []
        threads = [threading.Thread(target=lambda: results.append(cache.get(
            "state", build, generation="g", fresh=True,
            source_revision="rev-1", wait_timeout_seconds=0.1,
        ))) for _ in range(8)]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        time.sleep(0.15)
        release.set()
        for thread in threads:
            thread.join(1.0)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(builds, 1)
        self.assertEqual(len(results), 8)
        self.assertTrue(any(row.get("_freshness_timeout") for row in results))

    def test_source_revision_avoids_poll_rebuild_but_mutation_rebuilds(self):
        cache = VersionedSnapshotCache(ttl_seconds=30)
        builds = 0

        def build():
            nonlocal builds
            builds += 1
            return {"agents": [{"id": "codo", "build": builds}]}

        first = cache.get("state", build, generation="g", fresh=True,
                          source_revision="rev-1")
        same = cache.get("state", build, generation="g", fresh=True,
                         source_revision="rev-1")
        changed = cache.get("state", build, generation="g", fresh=True,
                            source_revision="rev-2")

        self.assertEqual(first["version"], same["version"])
        self.assertEqual(builds, 2)
        self.assertGreater(changed["version"], same["version"])

    def test_runtime_persist_replace_exhaustion_is_loud_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_file = Path(tmp) / "runtime.json"
            previous = {"other": {"state": "running"}}
            runtime_file.write_text(json.dumps(previous), encoding="utf-8")
            locked = PermissionError(32, "file in use")
            with (
                mock.patch.object(
                    orchestrator_server, "_council_runtime_file",
                    return_value=runtime_file,
                ),
                mock.patch.object(
                    orchestrator_server.os, "replace", side_effect=locked,
                ) as replace,
                mock.patch.object(orchestrator_server.time, "sleep"),
                mock.patch.object(
                    orchestrator_server, "_council_dashboard_payload_invalidate"
                ) as invalidate,
            ):
                persisted = orchestrator_server._council_runtime_persist(
                    "codo", {"state": "idle"})

            self.assertFalse(persisted)
            self.assertEqual(replace.call_count, 1)
            self.assertEqual(
                json.loads(runtime_file.read_text(encoding="utf-8")), previous)
            invalidate.assert_not_called()

    def test_runtime_persist_success_invalidates_local_dashboard_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_file = Path(tmp) / "runtime.json"
            with (
                mock.patch.object(
                    orchestrator_server, "_council_runtime_file",
                    return_value=runtime_file,
                ),
                mock.patch.object(
                    orchestrator_server, "_council_dashboard_payload_invalidate"
                ) as invalidate,
            ):
                persisted = orchestrator_server._council_runtime_persist(
                    "codo", {"state": "idle"})

            self.assertTrue(persisted)
            invalidate.assert_called_once_with()

    def test_runtime_start_failure_does_not_publish_local_state(self):
        agent = "persist-failure-probe"
        orchestrator_server._COUNCIL_AGENT_RUNTIME.pop(agent, None)
        with mock.patch.object(
            orchestrator_server, "_council_runtime_write", return_value="persist_error"
        ):
            value = orchestrator_server._council_runtime_start(
                agent, "request-1", "broker")
        self.assertIs(value, False)
        self.assertNotIn(agent, orchestrator_server._COUNCIL_AGENT_RUNTIME)

    def test_runtime_start_reserves_once_and_rolls_back_only_its_token(self):
        agent = "atomic-reservation-probe"
        orchestrator_server._COUNCIL_AGENT_RUNTIME.pop(agent, None)
        entered = threading.Event()
        release = threading.Event()

        def slow_write(*args, **kwargs):
            entered.set()
            release.wait(1.0)
            return "persist_error"

        first = []
        with mock.patch.object(orchestrator_server, "_council_runtime_write",
                               side_effect=slow_write):
            thread = threading.Thread(target=lambda: first.append(
                orchestrator_server._council_runtime_start(agent, "r1", "broker")))
            thread.start()
            self.assertTrue(entered.wait(1.0))
            second = orchestrator_server._council_runtime_start(agent, "r2", "broker")
            release.set()
            thread.join(1.0)
        self.assertIsNone(second)
        self.assertEqual(first, [False])
        self.assertNotIn(agent, orchestrator_server._COUNCIL_AGENT_RUNTIME)

    def test_runtime_file_lock_retries_until_release_and_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "runtime.lock"
            owner = lock_path.open("a+b")
            waiter = lock_path.open("a+b")
            owner.write(b"0")
            owner.flush()
            owner.seek(0)
            waiter.seek(0)
            orchestrator_server.msvcrt.locking(
                owner.fileno(), orchestrator_server.msvcrt.LK_NBLCK, 1)
            try:
                def release_owner():
                    time.sleep(0.05)
                    orchestrator_server.msvcrt.locking(
                        owner.fileno(), orchestrator_server.msvcrt.LK_UNLCK, 1)

                released = threading.Thread(target=release_owner)
                released.start()
                self.assertTrue(orchestrator_server.acquire_file_lock_until(
                    waiter, deadline=time.monotonic() + 0.5, retry_seconds=0.005))
                orchestrator_server.msvcrt.locking(
                    waiter.fileno(), orchestrator_server.msvcrt.LK_UNLCK, 1)
                released.join(1.0)
                orchestrator_server.msvcrt.locking(
                    owner.fileno(), orchestrator_server.msvcrt.LK_NBLCK, 1)
                started = time.monotonic()
                self.assertFalse(orchestrator_server.acquire_file_lock_until(
                    waiter, deadline=started + 0.05, retry_seconds=0.005))
                self.assertGreaterEqual(time.monotonic() - started, 0.04)
            finally:
                try:
                    orchestrator_server.msvcrt.locking(
                        owner.fileno(), orchestrator_server.msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                owner.close()
                waiter.close()

    def test_runtime_persist_cross_process_simulation_preserves_siblings(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_file = Path(tmp) / "runtime.json"
            barrier = threading.Barrier(3)
            outcomes = []

            def persist(agent):
                barrier.wait(timeout=2.0)
                outcomes.append(orchestrator_server._council_runtime_persist(
                    agent, {"state": "running", "request_id": agent}))

            with mock.patch.object(
                orchestrator_server, "_council_runtime_file",
                return_value=runtime_file,
            ):
                threads = [threading.Thread(target=persist, args=(agent,))
                           for agent in ("codo", "ope")]
                for thread in threads:
                    thread.start()
                barrier.wait(timeout=2.0)
                for thread in threads:
                    thread.join(timeout=2.0)

            data = json.loads(runtime_file.read_text(encoding="utf-8"))
            self.assertEqual(outcomes, [True, True])
            self.assertEqual(set(data), {"codo", "ope"})

    def test_late_finish_cannot_replace_cross_process_reservation(self):
        agent = "late-finish-cas-probe"
        local_a = {"state": "running", "request_id": "request-a", "worker_pid": None}
        reserved_b = {"state": "running", "request_id": "request-b",
                      "reservation_token": "token-b"}
        with tempfile.TemporaryDirectory() as tmp:
            runtime_file = Path(tmp) / "runtime.json"
            runtime_file.write_text(json.dumps({agent: reserved_b}), encoding="utf-8")
            orchestrator_server._COUNCIL_AGENT_RUNTIME[agent] = dict(local_a)
            try:
                with mock.patch.object(orchestrator_server, "_council_runtime_file",
                                       return_value=runtime_file):
                    result = orchestrator_server._council_runtime_finish(
                        agent, "request-a", True)

                persisted = json.loads(runtime_file.read_text(encoding="utf-8"))[agent]
                self.assertEqual(persisted, reserved_b)
                self.assertEqual(result, reserved_b)
                self.assertEqual(
                    orchestrator_server._COUNCIL_AGENT_RUNTIME[agent], reserved_b)
                self.assertNotIn("persist_degraded", result)
            finally:
                orchestrator_server._COUNCIL_AGENT_RUNTIME.pop(agent, None)

    def test_status_only_builder_skips_expensive_logs_and_metrics(self):
        with (
            mock.patch.object(
                orchestrator_server, "_council_paused_info",
                return_value=None,
            ),
            mock.patch.object(
                orchestrator_server, "_council_runtime_status",
                return_value={"state": "idle"},
            ),
            mock.patch.object(
                orchestrator_server, "_council_direct_log_info"
            ) as direct_log,
            mock.patch.object(
                orchestrator_server, "_council_agent_metrics"
            ) as metrics,
            mock.patch.object(
                orchestrator_server, "_council_read_text"
            ) as read_text,
            mock.patch.object(
                orchestrator_server, "_council_live_raw_tail"
            ) as live_raw,
        ):
            agents = orchestrator_server._council_build_agent_payloads(
                {"status"})

        self.assertTrue(agents)
        self.assertTrue(all(
            set(agent) == {"id", "label", "status"} for agent in agents))
        direct_log.assert_not_called()
        metrics.assert_not_called()
        read_text.assert_not_called()
        live_raw.assert_not_called()
        self.assertEqual(
            orchestrator_server._council_parse_fields_param(
                "status,unknown,status", {"runtime"}),
            {"status"},
        )

    def test_direct_log_events_never_read_whole_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            council_dir = Path(tmp)
            log_path = council_dir / "log_codo.txt"
            log_path.write_bytes(b"x" * 2_100_000 + b"\nFINAL")
            with mock.patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("whole-file read forbidden"),
            ):
                events = orchestrator_server._council_direct_log_events(
                    council_dir, limit=10)

        self.assertTrue(events)
        self.assertLessEqual(len(events[-1]["text"]), 3000)
        self.assertTrue(events[-1]["id"].startswith("direct-codo-"))


if __name__ == "__main__":
    unittest.main()
