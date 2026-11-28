"""C5: listener supervisor kind-aware probe confirmation.

WHY this file exists
--------------------
On 27/11 the supervisor logged 25 DEAD_CONFIRMED while the daemon kept
serving: the 2s self-probe timed out under load and 3 consecutive timeouts
confirmed a death that never happened (206 LISTENER_SUSPECT all reason=probe,
21 unaided REVIVED vs 3 RECOVERED). C5 classifies each probe failure by KIND
and raises the timeout budget to 5s: a loaded-but-alive accept loop answers
within 5s and resets; a truly wedged accept loop (bound socket, SYN backlog)
manifests as TIMEOUT and must STILL confirm death -- after 6 consecutive
timeouts (~60s), never "timeout never confirms". A refused connection stays
the fast 3-failure death signal, and legacy bool probe fakes keep the old
3-failure threshold.

Each test names the mutant it kills:
- 3 timeouts not dead        -> the old kind-blind 3-failure threshold (the
                                25x load false-positive shape)
- 6 timeouts dead            -> "timeout never confirms death" (breaks true
                                wedge detection, FORBIDDEN by the spec)
- 3 refused dead             -> raising the fast path's threshold too
- timeout x2 + pass resets   -> a pass that does not reset the streak
- timeout x5 + refused x1    -> a threshold evaluated on the FIRST kind of
                                the streak instead of the CURRENT failure's
                                kind (spec pin: count 6 >= 3 -> death)
- legacy bool fake           -> treating a bool probe result as a kind and
                                applying the timeout threshold to it

White-box like test_listener_supervisor_unit: fake monotonic clock, scripted
probe kinds fed through ls._probe_once so the REAL _default_probe wrapper
(kind -> bool adaptation + fam.last_probe_kind store) is exercised. No real
sockets, no live daemon.
"""

import asyncio
import socket
import unittest
from types import SimpleNamespace
from unittest import mock

from orchestrator.lifecycle import listener_supervisor as ls


class KindProbeScript:
    """Scripted ls._probe_once replacement: one kind per call, last repeats."""

    def __init__(self, kinds):
        self.kinds = list(kinds)
        self.calls = 0

    async def __call__(self, address, port, timeout_s):
        i = min(self.calls, len(self.kinds) - 1)
        self.calls += 1
        return ls._ProbeKind(self.kinds[i])


def _make_sup(script=None, probe_fn=None, mode="detect"):
    clock = [1000.0]
    fams = [{"key": "ipv4", "address": "127.0.0.1",
             "address_family": socket.AF_INET,
             "site": SimpleNamespace(), "boot": "bound"}]
    sup = ls.ListenerSupervisor(
        runner=SimpleNamespace(),
        port=18099,
        families=fams,
        mode=mode,
        ready_check=lambda: True,
        stopping_check=lambda: False,
        params=ls.SupervisorParams(),
        probe_fn=probe_fn,
        monotonic=lambda: clock[0],
        wall=lambda: 1_700_000_000.0 + clock[0],
    )
    return sup


class TestKindAwareConfirmation(unittest.IsolatedAsyncioTestCase):
    async def test_three_consecutive_timeouts_do_not_confirm_death(self):
        # The load false-positive shape (25x on 27/11): 3 timeouts under the
        # old budget confirmed death. With timeout_confirm_failures=6 the
        # family stays suspect and the death ledger stays empty.
        script = KindProbeScript(["timeout"])
        with mock.patch.object(ls, "_probe_once", script):
            sup = _make_sup()
            for _ in range(3):
                await sup._cycle()
        fam = sup.family("ipv4")
        self.assertEqual(fam.state, "suspect")
        self.assertEqual(fam.consec_probe_fail, 3)
        self.assertEqual(fam.last_probe_kind, "timeout")
        self.assertEqual(len(fam.deaths), 0)

    async def test_six_consecutive_timeouts_confirm_death(self):
        # True wedge: a bound socket whose accept loop is stuck answers SYN
        # with backlog timeout forever. Timeout must STILL reach death
        # confirmation -- at 6, not never.
        script = KindProbeScript(["timeout"])
        with mock.patch.object(ls, "_probe_once", script):
            sup = _make_sup()
            for _ in range(5):
                await sup._cycle()
            self.assertEqual(sup.family("ipv4").state, "suspect")
            await sup._cycle()
        fam = sup.family("ipv4")
        self.assertEqual(fam.state, "dead_confirmed")
        self.assertEqual(len(fam.deaths), 1)

    async def test_three_consecutive_refused_confirm_death(self):
        # Refused is the strong death signal: the fast 3-failure path is
        # unchanged by C5.
        script = KindProbeScript(["refused"])
        with mock.patch.object(ls, "_probe_once", script):
            sup = _make_sup()
            for _ in range(2):
                await sup._cycle()
            self.assertEqual(sup.family("ipv4").state, "suspect")
            await sup._cycle()
        fam = sup.family("ipv4")
        self.assertEqual(fam.state, "dead_confirmed")
        self.assertEqual(len(fam.deaths), 1)

    async def test_two_timeouts_then_pass_resets_counters(self):
        # A loaded-but-alive loop answers within the raised budget: the pass
        # resets the streak exactly as today and clears suspect.
        script = KindProbeScript(["timeout", "timeout", "ok"])
        with mock.patch.object(ls, "_probe_once", script):
            sup = _make_sup()
            await sup._cycle()
            await sup._cycle()
            self.assertEqual(sup.family("ipv4").state, "suspect")
            self.assertEqual(sup.family("ipv4").consec_probe_fail, 2)
            await sup._cycle()
        fam = sup.family("ipv4")
        self.assertEqual(fam.state, "ok")
        self.assertEqual(fam.consec_probe_fail, 0)
        self.assertEqual(fam.last_probe_kind, "ok")
        self.assertEqual(len(fam.deaths), 0)

    async def test_timeout_streak_then_refused_uses_current_kind_threshold(self):
        # Spec pin (CHANGE 4): the count spans ALL consecutive failures; the
        # threshold is evaluated per failure using the CURRENT kind.
        # timeout x5 (count 5 < 6, no death) then refused x1 -> count 6 >= 3
        # -> death. Intended: refused is a strong death signal.
        script = KindProbeScript(["timeout"] * 5 + ["refused"])
        with mock.patch.object(ls, "_probe_once", script):
            sup = _make_sup()
            for _ in range(5):
                await sup._cycle()
            self.assertEqual(sup.family("ipv4").state, "suspect")
            self.assertEqual(sup.family("ipv4").consec_probe_fail, 5)
            await sup._cycle()
        fam = sup.family("ipv4")
        self.assertEqual(fam.state, "dead_confirmed")
        self.assertEqual(fam.last_probe_kind, "refused")
        self.assertEqual(len(fam.deaths), 1)

    async def test_legacy_bool_probe_fake_keeps_old_three_failure_threshold(self):
        # Back-compat: test fakes that REPLACE _probe_fn with bool returns
        # keep working -- kind stays None, threshold stays confirm_failures.
        results = [False]

        async def bool_probe(fam):
            return results[0]

        sup = _make_sup(probe_fn=bool_probe)
        for _ in range(2):
            await sup._cycle()
        self.assertEqual(sup.family("ipv4").state, "suspect")
        await sup._cycle()
        fam = sup.family("ipv4")
        self.assertEqual(fam.state, "dead_confirmed")
        self.assertIsNone(fam.last_probe_kind)
        self.assertEqual(len(fam.deaths), 1)

    async def test_refused_streak_then_timeout_uses_current_kind_threshold(self):
        # Kills the first-kind mutant (threshold sticky to the kind that
        # STARTED the streak): refused first -> threshold 3 -> count 3 >= 3
        # -> wrong death. The correct code evaluates the CURRENT failure's
        # kind (timeout -> 6), so no death yet. The existing timeout x5 +
        # refused x1 case does NOT kill this mutant (both die at count 6);
        # only this direction discriminates. Measured in-memory:
        # real=suspect/0 deaths, first-kind-mutant=dead/1.
        script = KindProbeScript(["refused", "refused", "timeout"])
        with mock.patch.object(ls, "_probe_once", script):
            sup = _make_sup()
            for _ in range(3):
                await sup._cycle()
        fam = sup.family("ipv4")
        self.assertEqual(fam.state, "suspect")
        self.assertEqual(fam.consec_probe_fail, 3)
        self.assertEqual(fam.last_probe_kind, "timeout")
        self.assertEqual(len(fam.deaths), 0)


if __name__ == "__main__":
    unittest.main()