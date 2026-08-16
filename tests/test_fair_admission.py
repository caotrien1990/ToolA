import asyncio
import os
import time
import unittest
from unittest import mock

from orchestrator.server.fair_admission import (
    FAIR_ADMISSION,
    FairAdmissionManager,
    GlobalAdmissionExceeded,
    PrincipalQuotaExceeded,
    PrincipalWaiterLimit,
    reset_admission_identity,
    set_admission_identity,
)
from orchestrator.server.executor_lanes import run_in_executor_lane, shutdown_executor_lanes
import orchestrator.server.executor_lanes as lanes


class FairAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await shutdown_executor_lanes(drain_timeout=0)
        lanes._reset_executor_lanes_for_tests()
        self.manager = FairAdmissionManager()
        self.env = mock.patch.dict(os.environ, {
            "ORCH_FAIR_ADMISSION_ENABLED": "1", "ORCH_GLOBAL_CAP_ENABLED": "1",
            "ORCH_FAIR_READ_IO_QUOTA": "2", "ORCH_FAIR_ANONYMOUS_QUOTA": "1",
            "ORCH_FAIR_MAX_WAITERS_PER_PRINCIPAL": "1", "ORCH_GLOBAL_CAP": "4"}, clear=False)
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await shutdown_executor_lanes(drain_timeout=0)
        lanes._reset_executor_lanes_for_tests()

    async def test_quota_allows_one_waiter_then_429(self):
        deadline = time.monotonic() + .2
        first = await self.manager.acquire("read_io", deadline=deadline, principal="p")
        second = await self.manager.acquire("read_io", deadline=deadline, principal="p")
        waiter = asyncio.create_task(self.manager.acquire("read_io", deadline=deadline, principal="p"))
        await asyncio.sleep(.02)
        with self.assertRaises(PrincipalWaiterLimit):
            await self.manager.acquire("read_io", deadline=deadline, principal="p")
        first.release()
        third = await waiter
        second.release(); third.release()
        self.assertEqual(self.manager.status()["permit_count"], 0)

    async def test_configured_waiter_budget_queues_parallel_callers(self):
        env = {
            "ORCH_FAIR_READ_IO_QUOTA": "1",
            "ORCH_FAIR_ANONYMOUS_QUOTA": "1",
            "ORCH_FAIR_MAX_WAITERS_PER_PRINCIPAL": "3",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            first = await self.manager.acquire(
                "read_io", deadline=time.monotonic()+1, principal="anonymous:localhost")
            waiters = [asyncio.create_task(self.manager.acquire(
                "read_io", deadline=time.monotonic()+1, principal="anonymous:localhost"))
                for _ in range(3)]
            await asyncio.sleep(.02)
            status = self.manager.status()
            self.assertEqual(status["principal_waiting"]["anonymous:localhost|read_io"], 3)
            with self.assertRaises(PrincipalWaiterLimit):
                await self.manager.acquire(
                    "read_io", deadline=time.monotonic()+1, principal="anonymous:localhost")
            first.release()
            for completed in asyncio.as_completed(waiters):
                permit = await completed
                permit.release()
        self.assertEqual(self.manager.status()["permit_count"], 0)

    async def test_anonymous_lane_quota_override_is_independent(self):
        with mock.patch.dict(os.environ, {
            "ORCH_FAIR_READ_IO_QUOTA": "2",
            "ORCH_FAIR_ANONYMOUS_QUOTA": "1",
            "ORCH_FAIR_ANONYMOUS_READ_IO_QUOTA": "6",
        }, clear=False):
            self.assertEqual(self.manager.quota("read_io", "principal:owner"), 2)
            self.assertEqual(self.manager.quota("read_io", "anonymous:localhost"), 6)

    async def test_quota_timeout_releases_waiter(self):
        first = await self.manager.acquire("read_io", deadline=time.monotonic()+.2, principal="anonymous:localhost")
        with self.assertRaises(PrincipalQuotaExceeded):
            await self.manager.acquire("read_io", deadline=time.monotonic()+.03, principal="anonymous:localhost")
        first.release()
        self.assertFalse(self.manager.status()["principal_waiting"])

    async def test_global_cap_and_control_plane_exemption(self):
        with mock.patch.dict(os.environ, {"ORCH_GLOBAL_CAP": "1"}, clear=False):
            first = await self.manager.acquire("read_io", deadline=time.monotonic()+.2, principal="p1")
            exempt = await self.manager.acquire("read_io", deadline=time.monotonic(), principal="server", control_plane=True)
            with self.assertRaises(GlobalAdmissionExceeded):
                await self.manager.acquire("read_io", deadline=time.monotonic()+.02, principal="p2")
            self.assertEqual(self.manager.status()["global_admitted"], 1)
            self.assertTrue(exempt.release()); self.assertFalse(exempt.release())
            first.release()

    async def test_cancel_and_double_release_do_not_leak(self):
        first = await self.manager.acquire("read_io", deadline=time.monotonic()+.2, principal="anonymous:localhost")
        waiting = asyncio.create_task(self.manager.acquire("read_io", deadline=time.monotonic()+1, principal="anonymous:localhost"))
        await asyncio.sleep(.02); waiting.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiting
        self.assertTrue(first.release()); self.assertFalse(first.release())
        status = self.manager.status()
        self.assertEqual(status["global_admitted"], 0); self.assertEqual(status["permit_count"], 0)

    async def test_idle_map_eviction(self):
        permit = await self.manager.acquire("read_io", deadline=time.monotonic()+.1, principal="p")
        permit.release()
        self.assertEqual(self.manager.evict_idle(0), 1)
        self.assertEqual(self.manager.status()["principal_cardinality"], 0)

    async def test_disabled_enforcement_keeps_metrics(self):
        with mock.patch.dict(os.environ, {"ORCH_FAIR_ADMISSION_ENABLED":"0", "ORCH_GLOBAL_CAP_ENABLED":"0"}, clear=False):
            permits = [await self.manager.acquire("read_io", deadline=time.monotonic(), principal="p") for _ in range(5)]
            self.assertEqual(self.manager.status()["global_admitted"], 5)
            for permit in permits: permit.release()

    async def test_high_intensity_four_principals_respect_quota_and_global(self):
        peaks = {f"p{i}": 0 for i in range(4)}; active = dict.fromkeys(peaks, 0); global_active = 0; global_peak = 0
        lock = asyncio.Lock()
        async def work(principal):
            nonlocal global_active, global_peak
            permit = await self.manager.acquire("read_io", deadline=time.monotonic()+1, principal=principal)
            try:
                async with lock:
                    active[principal] += 1; global_active += 1
                    peaks[principal] = max(peaks[principal], active[principal]); global_peak = max(global_peak, global_active)
                await asyncio.sleep(.01)
            finally:
                async with lock: active[principal] -= 1; global_active -= 1
                permit.release()
        await asyncio.gather(*(work(p) for p in peaks for _ in range(3)))
        self.assertTrue(all(peak <= 2 for peak in peaks.values())); self.assertLessEqual(global_peak, 4)
        self.assertTrue(all(peak >= 1 for peak in peaks.values()))

    async def test_executor_failure_releases_permit(self):
        tokens = set_admission_identity("principal:test")
        try:
            with self.assertRaisesRegex(RuntimeError, "boom"):
                await run_in_executor_lane("read_io", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            self.assertEqual(FAIR_ADMISSION.status()["permit_count"], 0)
        finally:
            reset_admission_identity(tokens)


    async def test_executor_cancellation_releases_permit(self):
        import threading
        tokens = set_admission_identity("principal:cancel")
        started = threading.Event()
        def block():
            started.set()
            import time as _time
            _time.sleep(.1)
        task = asyncio.create_task(run_in_executor_lane("read_io", block))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(.005)
        self.assertTrue(started.is_set())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(.12)
        self.assertEqual(FAIR_ADMISSION.status()["permit_count"], 0)
        reset_admission_identity(tokens)


class QuotaIdentityTests(unittest.TestCase):
    def test_anonymous_identities_isolate_different_sessions(self):
        import orchestrator_server as server
        with mock.patch.object(server, "_task_checkpoint_for_identity", return_value={}):
            first = server._quota_principal("s1", "a1", 1, "h", "bad", "api")
            second = server._quota_principal("s2", "a2", 1, "h", "bad", "api")
        self.assertTrue(first.startswith("anonymous:"))
        self.assertTrue(second.startswith("anonymous:"))
        self.assertNotEqual(first, second)

    def test_anonymous_identity_prefers_session_then_agent_then_peer(self):
        import orchestrator_server as server
        with mock.patch.object(server, "_task_checkpoint_for_identity", return_value={}):
            session_first = server._quota_principal("s", "a1", 1, "h", "bad", "api", "10.0.0.1")
            same_session = server._quota_principal("s", "a2", 1, "h", "bad", "api", "10.0.0.2")
            agent_only = server._quota_principal("", "a1", 1, "h", "bad", "api", "10.0.0.1")
            peer_only = server._quota_principal("", "", 1, "h", "bad", "api", "10.0.0.1")
            fallback = server._quota_principal("", "", 1, "h", "bad", "api")
        self.assertEqual(session_first, same_session)
        self.assertEqual(fallback, "anonymous:localhost")
        self.assertEqual(len({session_first, agent_only, peer_only}), 3)

    def test_root_identity_survives_epoch_renewal(self):
        import hashlib
        import orchestrator_server as server
        capability = "capability"
        issued = {"task_epoch": 2, "prompt_hash": "h2",
                  "owner_capability_sha256": hashlib.sha256(capability.encode()).hexdigest()}
        with mock.patch.object(server, "_task_checkpoint_for_identity", return_value=issued),              mock.patch.object(server.ct, "_task_checkpoint_load", return_value={"owner_identity_id":"root"}):
            first = server._quota_principal("s", "owner", 2, "h2", capability, "api")
            issued.update(task_epoch=3, prompt_hash="h3")
            second = server._quota_principal("s", "owner", 3, "h3", capability, "api")
        self.assertEqual(first, second); self.assertTrue(first.startswith("principal:"))


def _clean_orch_env():
    kept = {k: v for k, v in os.environ.items() if not k.startswith("ORCH_")}
    return mock.patch.dict(os.environ, kept, clear=True)


class LaneQuotaDefaultTests(unittest.TestCase):
    """Per-lane default quota contract (guards council_provider serialization)."""

    def test_lane_default_quotas_are_declared_per_lane(self):
        manager = FairAdmissionManager()
        lanes_under_test = (
            "read_io", "search", "command", "test", "git", "backup",
            "council_provider")
        with _clean_orch_env():
            observed = {
                lane: manager.quota(lane, "agent:owner")
                for lane in lanes_under_test}
        # Dict-equality: adding a lane without a quota decision fails here.
        self.assertEqual(observed, {
            "read_io": 2, "search": 2, "command": 1, "test": 1,
            "git": 1, "backup": 1, "council_provider": 8})

    def test_council_provider_default_matches_lane_and_launcher_sources(self):
        from orchestrator.server.executor_lanes import _LANE_DEFAULTS
        from orchestrator.lifecycle.high_il_launch import HIGH_IL_ENV
        manager = FairAdmissionManager()
        with _clean_orch_env():
            self.assertEqual(
                manager.quota("council_provider", "agent:owner"), 8)
        self.assertEqual(_LANE_DEFAULTS["council_provider"][0], 8)
        self.assertEqual(
            int(HIGH_IL_ENV["ORCH_FAIR_COUNCIL_PROVIDER_QUOTA"]), 8)

    def test_unknown_lane_falls_back_to_one(self):
        manager = FairAdmissionManager()
        with _clean_orch_env():
            self.assertEqual(manager.quota("no_such_lane", "agent:owner"), 1)

    def test_env_override_still_wins_for_council_provider(self):
        manager = FairAdmissionManager()
        override = {"ORCH_FAIR_COUNCIL_PROVIDER_QUOTA": "3"}
        with _clean_orch_env(), mock.patch.dict(os.environ, override):
            self.assertEqual(
                manager.quota("council_provider", "agent:owner"), 3)

    def test_anonymous_clamp_still_applies_to_council_provider(self):
        manager = FairAdmissionManager()
        env = {
            "ORCH_FAIR_COUNCIL_PROVIDER_QUOTA": "8",
            "ORCH_FAIR_ANONYMOUS_QUOTA": "2"}
        with _clean_orch_env(), mock.patch.dict(os.environ, env):
            self.assertEqual(
                manager.quota("council_provider", "agent:owner"), 8)
            self.assertEqual(
                manager.quota("council_provider", "anonymous:localhost"), 2)


class CouncilProviderConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """N>1 council_provider acquisitions must be genuinely simultaneous."""

    async def test_default_council_provider_admits_many_concurrent(self):
        manager = FairAdmissionManager()
        with _clean_orch_env():
            n = 6  # < 8 default quota and < 32 default global cap
            barrier = asyncio.Event()
            peak = 0
            active = 0
            lock = asyncio.Lock()

            async def work():
                nonlocal peak, active
                permit = await manager.acquire(
                    "council_provider",
                    deadline=time.monotonic() + 1,
                    principal="agent:leader")
                async with lock:
                    active += 1
                    peak = max(peak, active)
                    if active >= n:
                        barrier.set()
                await asyncio.wait_for(barrier.wait(), timeout=1)
                async with lock:
                    active -= 1
                permit.release()

            await asyncio.gather(*(work() for _ in range(n)))
        # If council_provider were serialized (quota 1) the barrier would
        # never reach n and asyncio.wait_for would raise before this line.
        self.assertEqual(peak, n)


if __name__ == "__main__":
    unittest.main()
