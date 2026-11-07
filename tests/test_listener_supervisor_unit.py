"""Unit tests for orchestrator.lifecycle.listener_supervisor (plan 4.13
minimum set): the 4.6 transition table row by row, window/backoff/flap
accounting, the two ledgers, defer-vs-give-up boundaries, latch and
revival semantics, kill-switch positions including the unrecognised-value
failsafe, the arming gate, and the loop exception-handler chain.

White-box: tests drive supervisor._cycle() directly against a fake
monotonic clock, fake sites and a scripted probe, so every timing rule is
exercised without real waiting. The harness factory "heals" the probe
when it creates a site, modelling a freshly recreated listener that
actually serves; factory_heals=False models a recreate that does not.
"""

import asyncio
import logging
import os
import socket
import unittest
from types import SimpleNamespace
from unittest import mock

from orchestrator.lifecycle import listener_supervisor as ls


class FakeSite:
    def __init__(self, *, start_error=None, listening=True):
        self.start_error = start_error
        self.listening = listening
        self.started = 0
        self.stopped = 0

    @property
    def _server(self):
        return SimpleNamespace(sockets=[object()] if self.listening else [])

    async def start(self):
        self.started += 1
        if self.start_error is not None:
            raise self.start_error

    async def stop(self):
        self.stopped += 1
        self.listening = False


class ProbeControl:
    def __init__(self):
        self.results = {}  # family key -> bool (default True)
        self.calls = []

    async def __call__(self, fam):
        self.calls.append(fam.key)
        return self.results.get(fam.key, True)


class SupervisorHarness:
    def __init__(self, *, mode="on", params=None, boot="bound"):
        self.clock = [1000.0]
        self.ready = [True]
        self.stopping = [False]
        self.probe = ProbeControl()
        self.factory_sites = []
        self.factory_error = None
        self.factory_heals = True
        self.initial_site = FakeSite()
        fams = [
            {"key": "ipv4", "address": "127.0.0.1",
             "address_family": socket.AF_INET,
             "site": self.initial_site if boot == "bound" else None,
             "boot": boot},
            {"key": "ipv6", "address": "::1",
             "address_family": socket.AF_INET6,
             "site": FakeSite() if boot == "bound" else None,
             "boot": boot},
        ]

        def factory(fam):
            if self.factory_error is not None:
                raise self.factory_error
            site = FakeSite()
            self.factory_sites.append(site)
            if self.factory_heals:
                self.probe.results[fam.key] = True
            return site

        self.factory = factory
        self.sup = ls.ListenerSupervisor(
            runner=SimpleNamespace(),
            port=18099,
            families=fams,
            mode=mode,
            ready_check=lambda: self.ready[0],
            stopping_check=lambda: self.stopping[0],
            params=params or ls.SupervisorParams(
                backoff_s=(5.0, 15.0, 45.0), verify_pass_gap_s=0.0),
            site_factory=factory,
            probe_fn=self.probe,
            monotonic=lambda: self.clock[0],
            wall=lambda: 1_700_000_000.0 + self.clock[0],
        )

    def fam(self, key="ipv4"):
        return self.sup.family(key)

    def advance(self, seconds):
        self.clock[0] += seconds

    async def cycle(self, n=1):
        for _ in range(n):
            await self.sup._cycle()

    async def kill_by_probes(self, key="ipv4"):
        """Drive a healthy family to dead_confirmed via failing probes."""
        self.probe.results[key] = False
        await self.cycle(3)
        assert self.fam(key).state == "dead_confirmed", self.fam(key).state


class TestFlagAndParams(unittest.TestCase):
    def test_flag_default_on(self):
        self.assertEqual(ls.parse_recovery_mode({}), "on")

    def test_flag_detect(self):
        self.assertEqual(
            ls.parse_recovery_mode({ls.FLAG_ENV: "detect"}), "detect")

    def test_flag_case_insensitive(self):
        self.assertEqual(ls.parse_recovery_mode({ls.FLAG_ENV: "ON"}), "on")

    def test_flag_unrecognised_fails_safe_to_detect_with_error_log(self):
        with self.assertLogs(level="ERROR"):
            self.assertEqual(
                ls.parse_recovery_mode({ls.FLAG_ENV: "off"}), "detect")
        with self.assertLogs(level="ERROR"):
            self.assertEqual(
                ls.parse_recovery_mode({ls.FLAG_ENV: "banana"}), "detect")

    def test_plan_parameter_defaults(self):
        p = ls.SupervisorParams()
        self.assertEqual(p.probe_interval_s, 5.0)
        self.assertEqual(p.probe_timeout_s, 2.0)
        self.assertEqual(p.confirm_failures, 3)
        self.assertEqual(p.revive_passes, 3)
        self.assertEqual(p.verify_pass_gap_s, 1.0)
        self.assertEqual(p.window_s, 600.0)
        self.assertEqual(p.attempts_per_window, 3)
        self.assertEqual(p.lifetime_cap, 10)
        self.assertEqual(p.backoff_s, (5.0, 15.0, 45.0))
        self.assertEqual(p.flap_window_s, 1800.0)
        self.assertEqual(p.flap_deaths, 3)
        self.assertEqual(p.exhausted_relog_s, 300.0)
        self.assertTrue(p.gate_ledgers)

    def test_derive_first_address_per_family(self):
        result, extras = ls.derive_listener_addresses(18099)
        keys = [k for k, _a, _f in result]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn("ipv4", keys)
        for key, address, fam in result:
            if key == "ipv4":
                self.assertEqual(fam, socket.AF_INET)
            else:
                self.assertEqual(fam, socket.AF_INET6)
        self.assertGreaterEqual(extras, 0)


class TestExceptionHandler(unittest.TestCase):
    def _loop_and_spies(self):
        loop = asyncio.new_event_loop()
        chained = []
        loop.set_exception_handler(lambda l, ctx: chained.append(ctx))
        return loop, chained

    def test_unrelated_context_chains_to_previous(self):
        loop, chained = self._loop_and_spies()
        try:
            noted = []
            sup = SimpleNamespace(
                note_accept_failure=lambda **kw: noted.append(kw))
            ls.install_loop_exception_handler(loop, sup)
            ctx = {"exception": ValueError("nope"), "message": "boom"}
            loop.get_exception_handler()(loop, ctx)
            self.assertEqual(chained, [ctx])
            self.assertEqual(noted, [])
        finally:
            loop.close()

    def test_accept_failure_intercepted_not_chained(self):
        loop, chained = self._loop_and_spies()
        try:
            noted = []
            sup = SimpleNamespace(
                note_accept_failure=lambda **kw: noted.append(kw))
            ls.install_loop_exception_handler(loop, sup)
            err = OSError("The specified network name is no longer available")
            err.winerror = 64
            ctx = {"exception": err,
                   "message": "Task exception was never retrieved"}
            loop.get_exception_handler()(loop, ctx)
            self.assertEqual(chained, [])
            self.assertEqual(len(noted), 1)
        finally:
            loop.close()

    def test_handler_internal_error_falls_back_to_chain(self):
        loop, chained = self._loop_and_spies()
        try:
            def boom(**kw):
                raise RuntimeError("supervisor broken")
            sup = SimpleNamespace(note_accept_failure=boom)
            ls.install_loop_exception_handler(loop, sup)
            err = OSError("x")
            err.winerror = 64
            ctx = {"exception": err, "message": "m"}
            # Must not raise; must chain.
            loop.get_exception_handler()(loop, ctx)
            self.assertEqual(chained, [ctx])
        finally:
            loop.close()

    def test_no_previous_handler_uses_default(self):
        loop = asyncio.new_event_loop()
        try:
            ls.install_loop_exception_handler(loop, None)
            # A non-accept context goes to the default handler (which
            # logs); the call itself must not raise.
            with self.assertLogs(level="ERROR"):
                loop.get_exception_handler()(
                    loop, {"exception": ValueError("v"), "message": "m"})
        finally:
            loop.close()


class TestProbeTransitions(unittest.IsolatedAsyncioTestCase):
    async def test_ok_to_suspect_on_one_failed_probe_armed(self):
        h = SupervisorHarness()
        h.probe.results["ipv4"] = False
        await h.cycle()
        self.assertEqual(h.fam().state, "suspect")
        self.assertEqual(h.fam().suspect_cause, "probe")
        self.assertIsNone(h.fam().reason)

    async def test_suspect_to_ok_on_one_passing_probe(self):
        h = SupervisorHarness()
        h.probe.results["ipv4"] = False
        await h.cycle()
        self.assertEqual(h.fam().state, "suspect")
        h.probe.results["ipv4"] = True
        await h.cycle()
        self.assertEqual(h.fam().state, "ok")

    async def test_arming_gate_failures_never_counted(self):
        h = SupervisorHarness()
        h.ready[0] = False
        h.probe.results["ipv4"] = False
        await h.cycle(6)
        self.assertEqual(h.fam().state, "ok")
        self.assertEqual(h.fam().consec_probe_fail, 0)
        # Arming: counting starts from zero.
        h.ready[0] = True
        await h.cycle(2)
        self.assertEqual(h.fam().state, "suspect")
        await h.cycle()
        self.assertEqual(h.fam().state, "dead_confirmed")

    async def test_death_confirmed_after_three_consecutive_failures(self):
        h = SupervisorHarness()
        h.probe.results["ipv4"] = False
        await h.cycle(2)
        self.assertEqual(h.fam().state, "suspect")
        await h.cycle()
        self.assertEqual(h.fam().state, "dead_confirmed")
        self.assertEqual(len(h.fam().deaths), 1)

    async def test_pass_resets_failure_streak(self):
        h = SupervisorHarness()
        h.probe.results["ipv4"] = False
        await h.cycle(2)
        h.probe.results["ipv4"] = True
        await h.cycle()
        h.probe.results["ipv4"] = False
        await h.cycle(2)
        self.assertEqual(h.fam().state, "suspect")

    async def test_accept_event_marks_both_families_suspect(self):
        h = SupervisorHarness()
        err = OSError("gone")
        err.winerror = 64
        h.sup.note_accept_failure(error=err)
        self.assertEqual(h.fam("ipv4").state, "suspect")
        self.assertEqual(h.fam("ipv6").state, "suspect")
        self.assertEqual(h.fam("ipv4").suspect_cause, "accept_event")
        self.assertIsNone(h.sup.health_snapshot()["ipv4"]["reason"])
        self.assertIsNotNone(h.fam("ipv4").last_accept_fail_at_wall)

    async def test_accept_event_single_family_and_dead_family_untouched(self):
        h = SupervisorHarness()
        await h.kill_by_probes("ipv4")
        h.sup.note_accept_failure(family_key="ipv4", error=OSError("x"))
        self.assertEqual(h.fam("ipv4").state, "dead_confirmed")
        h.sup.note_accept_failure(family_key="ipv6", error=OSError("x"))
        self.assertEqual(h.fam("ipv6").state, "suspect")


class TestRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_attempt_after_backoff_recovers(self):
        h = SupervisorHarness()
        await h.kill_by_probes()
        fam = h.fam()
        # Attempt 1 lands 5 s after confirmed death (plan 4.5): cycles
        # before that must not attempt.
        await h.cycle()
        self.assertEqual(h.factory_sites, [])
        h.advance(4.9)
        await h.cycle()
        self.assertEqual(h.factory_sites, [])
        h.advance(0.2)
        await h.cycle()
        self.assertEqual(fam.state, "ok")
        self.assertEqual(fam.last_recovery_result, "success")
        self.assertEqual(fam.attempts_lifetime, 1)
        self.assertEqual(h.initial_site.stopped, 1)

    async def test_bind_failure_counts_attempt_and_backs_off(self):
        h = SupervisorHarness()
        await h.kill_by_probes()
        fam = h.fam()
        h.factory_error = OSError("address in use")
        h.advance(5.0)
        await h.cycle()
        self.assertEqual(fam.state, "dead_confirmed")
        self.assertEqual(fam.last_recovery_result, "bind_failed")
        self.assertEqual(fam.attempts_lifetime, 1)
        # Next attempt after the 15 s step.
        self.assertAlmostEqual(fam.next_attempt_at - h.clock[0], 15.0)
        h.factory_error = None
        h.advance(15.0)
        await h.cycle()
        self.assertEqual(fam.state, "ok")
        self.assertEqual(fam.attempts_lifetime, 2)

    async def test_verify_failure(self):
        h = SupervisorHarness()
        h.factory_heals = False  # recreated site still does not serve
        await h.kill_by_probes()
        fam = h.fam()
        h.advance(5.0)
        await h.cycle()
        self.assertEqual(fam.state, "dead_confirmed")
        self.assertEqual(fam.last_recovery_result, "verify_failed")
        self.assertEqual(fam.attempts_lifetime, 1)

    async def test_window_exhaustion_defers_then_resumes(self):
        h = SupervisorHarness()
        fam = h.fam()
        await h.kill_by_probes()
        h.factory_error = OSError("in use")
        for wait in (5.0, 15.0, 45.0):
            h.advance(wait)
            await h.cycle()
        self.assertEqual(fam.attempts_lifetime, 3)
        self.assertEqual(fam.state, "dead_deferred")
        self.assertEqual(fam.reason, "window_exhausted")
        # Window still closed: no more attempts.
        h.advance(60.0)
        await h.cycle()
        self.assertEqual(fam.attempts_lifetime, 3)
        # Slide past the 600 s window: re-evaluated per cycle; the
        # family STAYS dead_deferred while the resumed attempt is
        # scheduled after the shortest backoff (plan 4.6: dead_deferred
        # -> recovering via a fresh attempt, no intermediate state).
        h.advance(600.0)
        h.factory_error = None
        await h.cycle()
        self.assertEqual(fam.state, "dead_deferred")
        h.advance(5.0)
        await h.cycle()
        self.assertEqual(fam.state, "ok")
        self.assertEqual(fam.attempts_lifetime, 4)

    async def test_failed_bind_leaves_no_phantom_site(self):
        # Plan 4.4/D2: a site whose start() failed was never started, so
        # it must not become the family's site (the next attempt would
        # otherwise stop a never-started site instead of skipping).
        h = SupervisorHarness()
        fam = h.fam()
        await h.kill_by_probes()
        bad = FakeSite(start_error=OSError("in use"))
        good = FakeSite()

        def fac(f):
            site = bad if not fac.used else good
            fac.used = True
            if site is good:
                h.probe.results[f.key] = True
            return site

        fac.used = False
        h.sup._site_factory = fac
        h.advance(5.0)
        await h.cycle()
        self.assertEqual(fam.last_recovery_result, "bind_failed")
        self.assertIsNone(fam.site)
        h.advance(15.0)
        await h.cycle()
        self.assertEqual(fam.state, "ok")
        self.assertEqual(bad.stopped, 0)
        self.assertIs(fam.site, good)

    async def test_shutdown_abort_consumes_no_budget(self):
        # Plan 4.5/D3: attempts count at bind start; an attempt aborted
        # by the shutdown interlock between stop and recreate never
        # reaches bind and consumes nothing.
        h = SupervisorHarness()
        fam = h.fam()
        await h.kill_by_probes()
        orig_stop = h.initial_site.stop

        async def stop_and_flip():
            await orig_stop()
            h.stopping[0] = True

        h.initial_site.stop = stop_and_flip
        h.advance(5.0)
        await h.cycle()
        self.assertEqual(fam.attempts_lifetime, 0)
        self.assertEqual(len(fam.attempts), 0)
        self.assertEqual(fam.state, "dead_confirmed")

    async def test_latched_reason_survives_suspect_excursion(self):
        # Plan 4.8/D1: while recovery_latched is true, `reason` keeps the
        # give-up reason; suspect excursions use the internal cause only.
        params = ls.SupervisorParams(backoff_s=(0.0, 0.0, 0.0),
                                     verify_pass_gap_s=0.0, lifetime_cap=1)
        h = SupervisorHarness(params=params)
        fam = h.fam()
        h.factory_error = OSError("in use")
        await h.kill_by_probes()
        await h.cycle()
        self.assertEqual(fam.state, "dead_exhausted")
        h.probe.results["ipv4"] = True
        await h.cycle(3)
        self.assertEqual(fam.state, "ok")
        self.assertEqual(fam.reason, "lifetime_cap")
        h.probe.results["ipv4"] = False
        await h.cycle()
        self.assertEqual(fam.state, "suspect")
        self.assertEqual(fam.suspect_cause, "probe")
        snap = h.sup.health_snapshot()
        self.assertEqual(snap["ipv4"]["reason"], "lifetime_cap")
        self.assertTrue(snap["ipv4"]["recovery_latched"])
        h.probe.results["ipv4"] = True
        await h.cycle()
        self.assertEqual(fam.state, "ok")
        self.assertEqual(fam.reason, "lifetime_cap")

    async def test_flap_gives_up_latched(self):
        params = ls.SupervisorParams(backoff_s=(0.0, 0.0, 0.0),
                                     verify_pass_gap_s=0.0)
        h = SupervisorHarness(params=params)
        fam = h.fam()
        for _ in range(2):
            await h.kill_by_probes()
            h.probe.results["ipv4"] = True
            await h.cycle(3)  # unaided revival (3 consecutive passes)
            self.assertEqual(fam.state, "ok")
        # Third confirmed death inside 30 min -> flap give-up at confirm
        # (the state lands directly in dead_exhausted, so drive manually
        # instead of via kill_by_probes, which asserts dead_confirmed).
        h.probe.results["ipv4"] = False
        await h.cycle(3)
        self.assertEqual(fam.state, "dead_exhausted")
        self.assertEqual(fam.reason, "flap")
        self.assertEqual(fam.reason, "flap")
        self.assertTrue(fam.latched)
        # No attempts while latched-dead.
        before = fam.attempts_lifetime
        h.probe.results["ipv4"] = False
        await h.cycle(3)
        self.assertEqual(fam.attempts_lifetime, before)

    async def test_lifetime_cap_gives_up(self):
        params = ls.SupervisorParams(backoff_s=(0.0, 0.0, 0.0),
                                     verify_pass_gap_s=0.0, lifetime_cap=2)
        h = SupervisorHarness(params=params)
        fam = h.fam()
        h.factory_error = OSError("in use")
        await h.kill_by_probes()
        await h.cycle()
        self.assertEqual(fam.attempts_lifetime, 1)
        self.assertEqual(fam.state, "dead_confirmed")
        await h.cycle()
        self.assertEqual(fam.attempts_lifetime, 2)
        self.assertEqual(fam.state, "dead_exhausted")
        self.assertEqual(fam.reason, "lifetime_cap")
        self.assertTrue(fam.latched)

    async def test_latched_revival_retains_latch_and_redeath_is_direct(self):
        params = ls.SupervisorParams(backoff_s=(0.0, 0.0, 0.0),
                                     verify_pass_gap_s=0.0, lifetime_cap=1)
        h = SupervisorHarness(params=params)
        fam = h.fam()
        h.factory_error = OSError("in use")
        await h.kill_by_probes()
        await h.cycle()
        self.assertEqual(fam.state, "dead_exhausted")
        # Unaided revival: 3 consecutive passing probes.
        h.probe.results["ipv4"] = True
        await h.cycle(2)
        self.assertEqual(fam.state, "dead_exhausted")
        await h.cycle()
        self.assertEqual(fam.state, "ok")
        self.assertTrue(fam.latched)
        self.assertEqual(fam.reason, "lifetime_cap")
        # Re-death: straight back to dead_exhausted, zero new attempts.
        before = fam.attempts_lifetime
        h.probe.results["ipv4"] = False
        await h.cycle(3)
        self.assertEqual(fam.state, "dead_exhausted")
        self.assertEqual(fam.attempts_lifetime, before)

    async def test_unaided_revival_from_dead_confirmed(self):
        h = SupervisorHarness()
        fam = h.fam()
        await h.kill_by_probes()
        h.probe.results["ipv4"] = True
        await h.cycle(2)
        self.assertEqual(fam.state, "dead_confirmed")
        await h.cycle()
        self.assertEqual(fam.state, "ok")
        self.assertIsNone(fam.reason)

    async def test_detect_mode_never_attempts(self):
        h = SupervisorHarness(mode="detect")
        fam = h.fam()
        await h.kill_by_probes()
        h.advance(1000.0)
        await h.cycle(3)
        self.assertEqual(fam.state, "dead_confirmed")
        self.assertEqual(fam.attempts_lifetime, 0)
        self.assertEqual(h.factory_sites, [])
        # Revival still honoured in detect.
        h.probe.results["ipv4"] = True
        await h.cycle(3)
        self.assertEqual(fam.state, "ok")

    async def test_boot_bind_failed_family_enters_suspect_and_recovers(self):
        h = SupervisorHarness(boot="bind_failed")
        fam = h.fam()
        self.assertEqual(fam.state, "suspect")
        self.assertEqual(fam.boot, "bind_failed")
        await h.kill_by_probes()
        h.advance(5.0)
        await h.cycle()
        self.assertEqual(fam.state, "ok")
        self.assertEqual(fam.attempts_lifetime, 1)

    async def test_no_attempt_while_unarmed_or_stopping(self):
        h = SupervisorHarness()
        fam = h.fam()
        await h.kill_by_probes()
        h.advance(60.0)
        h.ready[0] = False
        await h.cycle()
        self.assertEqual(h.factory_sites, [])
        h.ready[0] = True
        h.stopping[0] = True
        await h.cycle()
        self.assertEqual(h.factory_sites, [])
        self.assertEqual(fam.state, "dead_confirmed")


class TestHealthSnapshot(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_shape(self):
        h = SupervisorHarness()
        snap = h.sup.health_snapshot()
        self.assertEqual(set(snap),
                         {"recovery", "armed", "supervisor", "ipv4", "ipv6"})
        self.assertEqual(snap["recovery"], "on")
        self.assertTrue(snap["armed"])
        self.assertEqual(set(snap["supervisor"]), {"last_cycle_at"})
        expected = {"state", "address", "since", "boot", "last_accept_fail_at",
                    "last_error", "last_recovery_result", "reason",
                    "attempts_window", "attempts_lifetime", "deaths_window",
                    "recovery_latched"}
        self.assertEqual(set(snap["ipv4"]), expected)
        self.assertEqual(snap["ipv4"]["state"], "ok")
        self.assertEqual(snap["ipv4"]["address"], "127.0.0.1")
        self.assertEqual(snap["ipv4"]["boot"], "bound")

    def test_fallback_snapshot_without_supervisor(self):
        ls.set_active_supervisor(None)
        with mock.patch.dict(os.environ, {}, clear=True):
            snap = ls.health_listener_snapshot()
        self.assertEqual(snap["recovery"], "on")
        self.assertFalse(snap["armed"])
        self.assertEqual(snap["supervisor"], {"last_cycle_at": None})
        self.assertNotIn("ipv4", snap)


class TestRunLoop(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeat_updates_and_loop_stops(self):
        h = SupervisorHarness(params=ls.SupervisorParams(
            probe_interval_s=0.05, verify_pass_gap_s=0.0))
        task = asyncio.create_task(h.sup.run())
        await asyncio.sleep(0.22)
        snap = h.sup.health_snapshot()
        self.assertIsNotNone(snap["supervisor"]["last_cycle_at"])
        h.stopping[0] = True
        await asyncio.wait_for(task, timeout=2.0)


if __name__ == "__main__":
    unittest.main()
