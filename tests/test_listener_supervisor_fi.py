"""Fault-injection suite for the listener supervisor (plan 4.12) plus the
4.13 boot cases, run against the PRODUCTION supervisor, production probe
and real aiohttp TCPSites on scratch port 18099 (verified free; the live
daemon on 8083 is never touched). The model app mirrors the daemon's
/health contract: 200 {"ok": true} when healthy, 503 while "booting".

Injection closes the family's listening socket object directly (Phase C
method): it reproduces the externally visible end state of the WinError
64 accept death (no LISTEN, fresh connects hang/refuse), not the proactor
failure path itself. Client probes run via asyncio.to_thread (Phase C:
blocking probes on the loop thread deadlock the model).
"""

import asyncio
import gc
import logging
import socket
import statistics
import time
import unittest
from types import SimpleNamespace

import psutil
from aiohttp import web

from orchestrator.lifecycle import listener_supervisor as ls

PORT = 18099


def fresh_probe(host, timeout=2.0):
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    t0 = time.perf_counter()
    try:
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, PORT))
        s.sendall(b"GET /health HTTP/1.0\r\nHost: x\r\n\r\n")
        data = s.recv(200)
        s.close()
        line = data.split(b"\r\n", 1)[0].decode("latin1")
        return {"ok": " 200 " in (" " + line + " "),
                "ms": (time.perf_counter() - t0) * 1000}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__,
                "ms": (time.perf_counter() - t0) * 1000}


class KeepAlive:
    def __init__(self, host):
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.s = socket.socket(fam, socket.SOCK_STREAM)
        self.s.settimeout(3.0)
        self.s.connect((host, PORT))
        self.buf = b""

    def get(self):
        self.s.sendall(b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        while b"\r\n\r\n" not in self.buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("closed-by-peer")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        clen = 0
        for ln in head.split(b"\r\n")[1:]:
            if ln.lower().startswith(b"content-length:"):
                clen = int(ln.split(b":", 1)[1])
        while len(self.buf) < clen:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("closed-body")
            self.buf += chunk
        _body, self.buf = self.buf[:clen], self.buf[clen:]
        return head.split(b"\r\n", 1)[0].decode("latin1")


def kill_family_socket(site):
    """Close the listening socket(s) of one family's site (Phase C)."""
    closed = 0
    srv = getattr(site, "_server", None)
    if srv is not None:
        for sk in list(srv.sockets or []):
            try:
                sk._sock.close()
                closed += 1
            except Exception:
                pass
    return closed


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())

    def has(self, marker):
        return any(marker in m for m in self.messages)


class _ProxySite:
    """Delegates to a real TCPSite so a test can hook stop() (TCPSite
    attributes are read-only in aiohttp 3.14)."""

    def __init__(self, real, on_stop=None):
        self._real = real
        self._on_stop = on_stop

    @property
    def _server(self):
        return getattr(self._real, "_server", None)

    async def start(self):
        await self._real.start()

    async def stop(self):
        await self._real.stop()
        if self._on_stop is not None:
            self._on_stop()


class ModelDaemon:
    """The wiring sequence of plan 4.1/4.9 replicated on the model app:
    derive -> handler -> per-family sites -> register -> supervisor task.
    """

    def __init__(self, *, mode="on", params=None, health_status=200):
        self.mode = mode
        self.params = params or ls.SupervisorParams(
            probe_interval_s=0.5, verify_pass_gap_s=0.2,
            backoff_s=(0.5, 1.0, 2.0))
        self.health_status = [health_status]
        self.ready = [True]
        self.stopping = [False]
        self.logs = LogCapture()
        self.sup = None
        self.task = None
        self.runner = None
        self.factory_calls = []

    async def __aenter__(self):
        logger = logging.getLogger("orchestrator.lifecycle.listener_supervisor")
        logger.addHandler(self.logs)
        logger.setLevel(logging.DEBUG)
        app = web.Application()

        async def health(_req):
            status = self.health_status[0]
            return web.json_response({"ok": status == 200}, status=status)

        app.router.add_get("/health", health)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        addresses, _extras = ls.derive_listener_addresses(PORT)
        self.sup = ls.ListenerSupervisor(
            runner=self.runner, port=PORT, families=[], mode=self.mode,
            ready_check=lambda: self.ready[0],
            stopping_check=lambda: self.stopping[0],
            params=self.params,
            site_factory=self._factory)
        loop = asyncio.get_running_loop()
        ls.install_loop_exception_handler(loop, self.sup)
        for key, addr, fam in addresses:
            site = web.TCPSite(self.runner, addr, PORT)
            entry = {"key": key, "address": addr, "address_family": fam,
                     "site": site, "boot": "bound"}
            try:
                await site.start()
            except Exception:
                entry["site"] = None
                entry["boot"] = "bind_failed"
            self.sup.register_family(entry)
        self.task = asyncio.create_task(self.sup.run())
        return self

    async def __aexit__(self, *exc):
        self.stopping[0] = True
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        if self.runner is not None:
            await self.runner.cleanup()
        logger = logging.getLogger("orchestrator.lifecycle.listener_supervisor")
        logger.removeHandler(self.logs)

    def _factory(self, fam):
        site = web.TCPSite(self.runner, fam.address, PORT)
        self.factory_calls.append(fam.key)
        return site

    def fam(self, key):
        return self.sup.family(key)

    async def wait_state(self, key, states, timeout=20.0):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            if self.fam(key).state in states:
                return True
            await asyncio.sleep(0.1)
        return False


async def probe(host):
    return await asyncio.to_thread(fresh_probe, host)


class BaselineChurn:
    """Pure-aiohttp site churn for the scenario-8 A3 baseline leg: NO
    supervisor. Replays the supervisor's probe-connection pattern per
    cycle (round-7 U-4 design): background `ls._probe_once` streams
    per family; per kill — wait until the background stream has
    registered 3 consecutive failures (plan 4.3's confirmation
    count), 0.2 s backoff (the soak backoff), stop the old site,
    create + start a fresh TCPSite, then 2 dedicated verify probes
    (`ls._probe_once`, 0.05 s apart, both must pass — plan 4.4's
    OBJ-2 shape). The only module utilities used are
    `ls.derive_listener_addresses` and `ls._probe_once` — the
    production address-derivation and probe code paths, exactly as
    in round 6's branch B.
    """

    def __init__(self):
        self.runner = None
        self.sites = {}
        self.addrs = {}
        self.consec_fail = {}
        self.registered = 0
        self.recovered = 0
        self.recreates = {}
        self._ptasks = []

    async def __aenter__(self):
        app = web.Application()

        async def health(_req):
            return web.json_response({"ok": True})

        app.router.add_get("/health", health)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        addresses, _extras = ls.derive_listener_addresses(PORT)
        for key, addr, _fam in addresses:
            site = web.TCPSite(self.runner, addr, PORT)
            await site.start()
            self.sites[key] = site
            self.addrs[key] = addr
            self.consec_fail[key] = 0
            self.recreates[key] = 0
            self._ptasks.append(asyncio.create_task(self._probe_loop(key)))
        return self

    async def __aexit__(self, *exc):
        for t in self._ptasks:
            t.cancel()
        if self.runner is not None:
            await self.runner.cleanup()

    async def _probe_loop(self, key):
        while True:
            ok = await ls._probe_once(self.addrs[key], PORT, 0.7)
            self.consec_fail[key] = 0 if ok else self.consec_fail[key] + 1
            await asyncio.sleep(0.3)

    async def cycle(self, key):
        old = self.sites[key]
        kill_family_socket(old)
        t0 = time.perf_counter()
        confirmed = False
        while time.perf_counter() - t0 < 20:
            if self.consec_fail[key] >= 3:
                confirmed = True
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
        try:
            await old.stop()
        except Exception:
            pass
        site = web.TCPSite(self.runner, self.addrs[key], PORT)
        await site.start()
        self.sites[key] = site
        self.recreates[key] += 1
        first = await ls._probe_once(self.addrs[key], PORT, 0.7)
        await asyncio.sleep(0.05)
        second = await ls._probe_once(self.addrs[key], PORT, 0.7)
        if confirmed:
            self.registered += 1
        if first and second:
            self.recovered += 1
        return confirmed, bool(first and second)

    def census(self):
        total = 0
        for site in self.sites.values():
            srv = getattr(site, "_server", None)
            total += len(srv.sockets or []) if srv else 0
        return total


class TestFaultInjection(unittest.IsolatedAsyncioTestCase):
    async def test_scenario1_kill_ipv4_recovers_ipv6_uninterrupted(self):
        async with ModelDaemon() as d:
            self.assertTrue((await probe("127.0.0.1"))["ok"])
            self.assertTrue((await probe("::1"))["ok"])
            ka = await asyncio.to_thread(KeepAlive, "::1")
            self.assertIn("200", await asyncio.to_thread(ka.get))
            t_kill = time.perf_counter()
            self.assertGreaterEqual(
                kill_family_socket(d.fam("ipv4").site), 1)
            # IPv6 fresh probes + the established keep-alive must not blink.
            v6_results = []
            recovered = False
            while time.perf_counter() - t_kill < 25:
                v6_results.append((await probe("::1"))["ok"])
                if d.fam("ipv4").state == "ok" and d.fam("ipv4").attempts_lifetime >= 1:
                    recovered = True
                    break
                await asyncio.sleep(0.2)
            self.assertTrue(recovered, "ipv4 did not recover")
            self.assertTrue(all(v6_results), "ipv6 fresh probe failed during ipv4 recovery")
            self.assertIn("200", await asyncio.to_thread(ka.get))
            self.assertTrue((await probe("127.0.0.1"))["ok"])
            self.assertEqual(d.fam("ipv4").last_recovery_result, "success")
            self.assertTrue(d.logs.has("LISTENER_DEAD_CONFIRMED"))
            self.assertTrue(d.logs.has("LISTENER_RECOVERED"))

    async def test_scenario2_kill_ipv6_recovers_ipv4_uninterrupted(self):
        async with ModelDaemon() as d:
            ka = await asyncio.to_thread(KeepAlive, "127.0.0.1")
            self.assertIn("200", await asyncio.to_thread(ka.get))
            self.assertGreaterEqual(kill_family_socket(d.fam("ipv6").site), 1)
            recovered = False
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 25:
                self.assertTrue((await probe("127.0.0.1"))["ok"])
                if d.fam("ipv6").state == "ok" and d.fam("ipv6").attempts_lifetime >= 1:
                    recovered = True
                    break
                await asyncio.sleep(0.2)
            self.assertTrue(recovered, "ipv6 did not recover")
            self.assertIn("200", await asyncio.to_thread(ka.get))
            self.assertTrue((await probe("::1"))["ok"])

    async def test_scenario3_kill_during_recovery_counts_and_continues(self):
        async with ModelDaemon() as d:
            orig_factory = d._factory
            sabotage = [True]

            def factory(fam):
                site = orig_factory(fam)
                if fam.key == "ipv4" and sabotage[0]:
                    sabotage[0] = False
                    real_start = site.start

                    async def start_then_kill():
                        await real_start()
                        kill_family_socket(site)

                    site.start = start_then_kill
                return site

            d.sup._site_factory = factory
            kill_family_socket(d.fam("ipv4").site)
            # First attempt: recreated socket is killed mid-verification.
            self.assertTrue(await d.wait_state(
                "ipv4", ("dead_confirmed",), timeout=25))
            self.assertTrue(await d.wait_state(
                "ipv4", ("ok",), timeout=30))
            fam = d.fam("ipv4")
            self.assertGreaterEqual(fam.attempts_lifetime, 2)
            self.assertFalse(d.task.done(), "supervisor task died")

    async def test_scenario4_window_exhaustion_scaled_defers_and_resumes(self):
        params = ls.SupervisorParams(
            probe_interval_s=0.5, probe_timeout_s=1.0, verify_pass_gap_s=0.1,
            backoff_s=(0.2, 0.2, 0.2), window_s=20.0)
        async with ModelDaemon(params=params) as d:
            def bad_factory(fam):
                raise OSError("in use")

            d.sup._site_factory = bad_factory
            kill_family_socket(d.fam("ipv4").site)
            self.assertTrue(await d.wait_state("ipv4", ("dead_deferred",), timeout=40))
            self.assertEqual(d.fam("ipv4").reason, "window_exhausted")
            self.assertTrue(d.logs.has("LISTENER_RECOVERY_DEFERRED"))
            attempts = d.fam("ipv4").attempts_lifetime
            self.assertEqual(attempts, 3)
            # Window slides open -> recovery resumes and succeeds.
            d.sup._site_factory = d._factory
            self.assertTrue(await d.wait_state("ipv4", ("ok",), timeout=40))

    async def test_scenario4_flap_latches_scaled(self):
        params = ls.SupervisorParams(
            probe_interval_s=0.5, probe_timeout_s=1.0, verify_pass_gap_s=0.1,
            backoff_s=(0.2, 0.2, 0.2), flap_window_s=120.0)
        async with ModelDaemon(params=params) as d:
            for _ in range(3):
                kill_family_socket(d.fam("ipv4").site)
                # Wait for THIS death to register first: state is still
                # "ok" from before the kill until probes confirm, so
                # waiting for "ok" directly would race ahead.
                self.assertTrue(await d.wait_state(
                    "ipv4", ("dead_confirmed", "dead_exhausted"), timeout=25))
                if d.fam("ipv4").latched:
                    break
                self.assertTrue(await d.wait_state("ipv4", ("ok",), timeout=25))
            self.assertEqual(d.fam("ipv4").state, "dead_exhausted")
            self.assertTrue(d.fam("ipv4").latched)
            self.assertEqual(d.fam("ipv4").reason, "flap")
            before = d.fam("ipv4").attempts_lifetime
            await asyncio.sleep(2.0)
            self.assertEqual(d.fam("ipv4").attempts_lifetime, before)

    async def test_scenario5_shutdown_during_suspect(self):
        async with ModelDaemon() as d:
            kill_family_socket(d.fam("ipv4").site)
            self.assertTrue(await d.wait_state("ipv4", ("suspect",), timeout=15))
            d.stopping[0] = True
            await asyncio.wait_for(d.task, timeout=5.0)
            self.assertEqual(d.factory_calls, [])

    async def test_scenario5_shutdown_between_stop_and_recreate(self):
        async with ModelDaemon() as d:
            real_site = d.fam("ipv4").site

            def flip():
                d.stopping[0] = True

            d.fam("ipv4").site = _ProxySite(real_site, on_stop=flip)
            kill_family_socket(d.fam("ipv4").site)
            await asyncio.wait_for(d.task, timeout=30.0)
            self.assertEqual(d.factory_calls, [])
            self.assertFalse((await probe("127.0.0.1"))["ok"])

    async def test_scenario6_slow_boot_never_confirms_before_arming(self):
        async with ModelDaemon(mode="detect", health_status=503) as d:
            d.ready[0] = False
            await asyncio.sleep(3.0)
            self.assertEqual(d.fam("ipv4").state, "ok")
            self.assertEqual(d.fam("ipv4").consec_probe_fail, 0)
            d.ready[0] = True
            self.assertTrue(await d.wait_state("ipv4", ("dead_confirmed",), timeout=15))
            self.assertEqual(d.fam("ipv4").attempts_lifetime, 0)
            d.health_status[0] = 200
            self.assertTrue(await d.wait_state("ipv4", ("ok",), timeout=15))

    async def test_scenario7_detect_mode_reports_but_never_recovers(self):
        async with ModelDaemon(mode="detect") as d:
            kill_family_socket(d.fam("ipv4").site)
            self.assertTrue(await d.wait_state("ipv4", ("dead_confirmed",), timeout=15))
            await asyncio.sleep(2.0)
            self.assertEqual(d.fam("ipv4").attempts_lifetime, 0)
            self.assertEqual(d.factory_calls, [])
            self.assertFalse((await probe("127.0.0.1"))["ok"])
            # Unaided fix: an external site on the address revives it.
            extra = web.TCPSite(d.runner, "127.0.0.1", PORT)
            await extra.start()
            self.assertTrue(await d.wait_state("ipv4", ("ok",), timeout=15))
            await extra.stop()

    async def test_scenario8_soak_a3_baseline_subtracted(self):
        # Soak gate (plan 4.12 as amended by Amendment A3, user decision
        # 2026-11-07): BASELINE-SUBTRACTED boundedness. Why the gate has
        # this shape — the short version (the full record lives in the
        # execution history): the original raw-handle criteria measured
        # TCP churn plus the one pre-allocated accept socket per death
        # that CPython's proactor abandons until GC (round-2
        # mechanism). A1 (settled series vs the warm-up baseline, same
        # numbers) failed on FLAT settled series in both harnesses
        # (+31 pytest / +20 clean). A2 (two-phase boundedness,
        # L100 - L50 <= +5) failed in both harnesses too (+48 pytest /
        # +25 clean): the settled FLOOR itself ratchets with cycle
        # count. Round 6's pre-registered discriminator separated the
        # layers: direct production supervisor +27, pure aiohttp with
        # no supervisor code +19, test harness exonerated — the ratchet
        # is mostly aiohttp/proactor/stdlib machinery. Round 7's U-4
        # experiment then closed the residual: baseline-plain +24 vs
        # the same baseline carrying the supervisor's PROBE PATTERN
        # (wait for 3 consecutive probe failures = plan 4.3
        # confirmation, 0.2 s backoff, stop + recreate, 2 verify
        # probes 0.05 s apart = plan 4.4) +34 — the +10 difference,
        # inside the pre-registered [+5, +11] band, follows the probe
        # pattern: machinery churn the supervisor's probing induces,
        # not retention by the supervisor itself.
        #
        # A3 therefore runs TWO legs back to back in this one process,
        # each under the full A2 protocol (2 phases x 50 kill+recover
        # cycles on alternating families; after each phase: stop
        # killing, 75 s settle (>= one TIME_WAIT lifetime ~60 s per
        # round-2 E1, plus margin), gc.collect() twice, official
        # series of 16 samples 2 s apart, L = median):
        #   leg 1 — the production supervisor (ModelDaemon, as A2);
        #   leg 2 — BaselineChurn: pure aiohttp site churn replaying
        #           the same probe pattern, no supervisor code.
        # The supervisor leg runs first, so any cross-leg GC benefit
        # accrues to the baseline (the conservative direction).
        # PASS iff (L100-L50)_supervisor - (L100-L50)_baseline <= +5,
        # the supervisor leg recovered in 100/100 cycles with neither
        # official series climbing (last - first <= +2) and a final
        # census of exactly 2 listening sockets, and the baseline leg
        # completed its 100 cycles with its own final census of 2.
        # Raw per-cycle samples are printed, not gated.
        # Round-8 harness fix (user-approved; the gate itself is
        # unchanged): each phase prints its raw + settled series as
        # the phase completes, so an abort mid-leg cannot lose
        # already-computed phases (run 1 lost the baseline leg's
        # phase 1 that way), and a shed block between the legs
        # releases leg 1's captured-log weight before leg 2 starts.
        params = ls.SupervisorParams(
            probe_interval_s=0.3, probe_timeout_s=0.7, verify_pass_gap_s=0.05,
            backoff_s=(0.2, 0.2, 0.2), gate_ledgers=False)
        proc = psutil.Process()
        keys = ["ipv4", "ipv6"]

        async def settled_series():
            await asyncio.sleep(75)
            gc.collect()
            gc.collect()
            series = []
            for j in range(16):
                series.append(proc.num_handles())
                if j < 15:
                    await asyncio.sleep(2)
            return series

        # ---- leg 1: production supervisor (A2 protocol) ----
        async with ModelDaemon(params=params) as d:
            cycle_no = [0]

            async def run_phase_sup(cycles):
                raw = []
                for _ in range(cycles):
                    i = cycle_no[0]
                    key = keys[i % 2]
                    kill_family_socket(d.fam(key).site)
                    self.assertTrue(
                        await d.wait_state(
                            key, ("suspect", "dead_confirmed", "recovering"),
                            timeout=20),
                        f"supervisor cycle {i} ({key}) death did not register")
                    self.assertTrue(
                        await d.wait_state(key, ("ok",), timeout=20),
                        f"supervisor cycle {i} ({key}) did not recover")
                    raw.append(proc.num_handles())
                    cycle_no[0] += 1
                return raw

            sup_raw1 = await run_phase_sup(50)
            sup_s1 = await settled_series()
            sup50 = statistics.median(sup_s1)
            print(f"soak A3 supervisor raw phase 1: {sup_raw1}", flush=True)
            print(f"soak A3 supervisor settled phase 1 (L50={sup50}): "
                  f"{sup_s1}", flush=True)
            sup_raw2 = await run_phase_sup(50)
            sup_s2 = await settled_series()
            sup100 = statistics.median(sup_s2)
            print(f"soak A3 supervisor raw phase 2: {sup_raw2}", flush=True)
            print(f"soak A3 supervisor settled phase 2 (L100={sup100}): "
                  f"{sup_s2}", flush=True)
            sup_attempts = {k: d.fam(k).attempts_lifetime for k in keys}
            sup_census = 0
            for k in keys:
                srv = getattr(d.fam(k).site, "_server", None)
                sup_census += len(srv.sockets or []) if srv else 0

        # Harness shed between legs (round-8 harness fix; no gate
        # number or protocol element changes): leg 1's captured
        # supervisor-log strings (the ModelDaemon LogCapture list) and
        # the daemon object graph stay referenced by this test's frame
        # until the method returns. Round 7's run 1 showed the cost:
        # repeated asyncio slow-callback stalls (0.109-0.203 s) inside
        # leg 2's probe loops and a 0.7 s verify probe blown by
        # scheduling latency at baseline cycle 66 — the in-process
        # retention family the round-2 E2 evidence named. Release
        # both here and collect, so leg 2 starts from a collected
        # heap. (pytest's own formatted log buffer is not reachable
        # from the test and is not part of this shed.)
        d.logs.messages.clear()
        d = None
        gc.collect()
        gc.collect()

        # ---- leg 2: pure-aiohttp baseline, supervisor probe pattern ----
        async with BaselineChurn() as b:
            bcycle = [0]

            async def run_phase_base(cycles):
                raw = []
                for _ in range(cycles):
                    i = bcycle[0]
                    key = keys[i % 2]
                    confirmed, verified = await b.cycle(key)
                    self.assertTrue(
                        confirmed,
                        f"baseline cycle {i} ({key}) death not confirmed")
                    self.assertTrue(
                        verified,
                        f"baseline cycle {i} ({key}) verify probes failed")
                    raw.append(proc.num_handles())
                    bcycle[0] += 1
                return raw

            base_raw1 = await run_phase_base(50)
            base_s1 = await settled_series()
            base50 = statistics.median(base_s1)
            print(f"soak A3 baseline raw phase 1: {base_raw1}", flush=True)
            print(f"soak A3 baseline settled phase 1 (L50={base50}): "
                  f"{base_s1}", flush=True)
            base_raw2 = await run_phase_base(50)
            base_s2 = await settled_series()
            base100 = statistics.median(base_s2)
            print(f"soak A3 baseline raw phase 2: {base_raw2}", flush=True)
            print(f"soak A3 baseline settled phase 2 (L100={base100}): "
                  f"{base_s2}", flush=True)
            base_census = b.census()
            base_registered = b.registered
            base_recovered = b.recovered

        # ---- A3 criteria ----
        for k in keys:
            self.assertGreaterEqual(
                sup_attempts[k], 50,
                f"supervisor {k} attempts {sup_attempts[k]} < 50")
        self.assertEqual(sup_census, 2)
        self.assertLessEqual(
            sup_s1[-1] - sup_s1[0], 2,
            f"supervisor phase-1 settled series climbs: {sup_s1}")
        self.assertLessEqual(
            sup_s2[-1] - sup_s2[0], 2,
            f"supervisor phase-2 settled series climbs: {sup_s2}")
        self.assertEqual(base_registered, 100)
        self.assertEqual(base_recovered, 100)
        self.assertEqual(base_census, 2)
        sup_delta = sup100 - sup50
        base_delta = base100 - base50
        print(f"soak A3 deltas: supervisor {sup_delta}, baseline "
              f"{base_delta}, excess {sup_delta - base_delta}")
        self.assertLessEqual(
            sup_delta - base_delta, 5,
            f"supervisor excess over machinery baseline: "
            f"{sup_delta} - {base_delta} = {sup_delta - base_delta} > 5")


class TestBootCases(unittest.IsolatedAsyncioTestCase):
    async def test_boot_both_families_bound(self):
        async with ModelDaemon() as d:
            snap = d.sup.health_snapshot()
            self.assertEqual(snap["ipv4"]["boot"], "bound")
            self.assertEqual(snap["ipv6"]["boot"], "bound")
            self.assertEqual(snap["ipv4"]["state"], "ok")
            self.assertEqual(snap["ipv6"]["state"], "ok")

    async def test_boot_one_family_bind_failed_enters_machinery(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        blocker.bind(("127.0.0.1", PORT))
        blocker.listen(1)
        try:
            async with ModelDaemon() as d:
                fam = d.fam("ipv4")
                self.assertEqual(fam.boot, "bind_failed")
                self.assertEqual(fam.state, "suspect")
                snap = d.sup.health_snapshot()
                self.assertEqual(snap["ipv4"]["boot"], "bind_failed")
                # The other family serves; the daemon (model) is up.
                self.assertTrue((await probe("::1"))["ok"])
                # Free the port: the machinery late-binds the family.
                blocker.close()
                self.assertTrue(await d.wait_state("ipv4", ("ok",), timeout=25))
        finally:
            try:
                blocker.close()
            except Exception:
                pass


class TestWindowExhaustionUnscaled(unittest.IsolatedAsyncioTestCase):
    async def test_real_ten_minute_window(self):
        """Plan 4.12 scenario 4: ONE unscaled run with the real 600 s
        window. Three fast failed attempts spend the window; the family
        defers; recovery resumes only when the window actually slides.
        This test legitimately takes ~10 minutes."""
        params = ls.SupervisorParams(
            probe_interval_s=0.5, probe_timeout_s=1.0, verify_pass_gap_s=0.1,
            backoff_s=(0.2, 0.2, 0.2))  # window_s stays the real 600.0
        async with ModelDaemon(params=params) as d:
            def bad_factory(fam):
                raise OSError("in use")

            d.sup._site_factory = bad_factory
            kill_family_socket(d.fam("ipv4").site)
            self.assertTrue(await d.wait_state("ipv4", ("dead_deferred",), timeout=30))
            d.sup._site_factory = d._factory
            self.assertTrue(
                await d.wait_state("ipv4", ("ok",), timeout=700),
                "recovery did not resume after the real window slid")


if __name__ == "__main__":
    unittest.main()
