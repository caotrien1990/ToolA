"""/health must not block the event loop on the restart-controller probe.

`controller_health` is a synchronous `urllib.request.urlopen`, and it was awaited
inline from `handle_health`. The expensive case is the COMMON one: when the
controller on :8085 is not listening, the connect takes ~110ms to fail on
Windows -- every request, indefinitely. Measured before the fix: 0.109s of a
0.125s /health, 87% of the handler spent connecting to a port that was down.

That also broke `test_health_metrics_does_not_block_event_loop`, which asserts
/health returns in under 0.15s. It had been failing for the right reason and
pointing at the wrong variable: the durable-metrics timeout it exercises was
working correctly, and the blocking call was somewhere else in the same handler.
A latency assertion that fires on the wrong cause is still a latency assertion
that fired -- these tests pin the cause so the next one cannot hide.
"""
import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orchestrator_server as srv


def _reset():
    """Drain before clearing. A probe left in flight outlives the `mock.patch`
    that installed it, so the NEXT test's first submit queues behind a stale
    0.4s job on a single-worker executor and its own probe never runs -- which is
    exactly how this file first failed, with `calls == 0`."""
    future = srv._CONTROLLER_HEALTH_FUTURE
    if future is not None:
        try:
            future.result(timeout=2.0)
        except Exception:
            pass
    srv._CONTROLLER_HEALTH_CACHE = {}
    srv._CONTROLLER_HEALTH_CACHE_AT = 0.0
    srv._CONTROLLER_HEALTH_FUTURE = None
    srv._CONTROLLER_HEALTH_CACHE_KEY = None


class ControllerHealthOffLoopTests(unittest.TestCase):
    def setUp(self):
        _reset()
        self.addCleanup(_reset)

    def test_a_slow_probe_does_not_delay_the_response(self):
        """The regression proper. A probe far slower than any real connect must
        cost the caller nothing, because the caller never waits for it."""
        calls = []

        def slow(url, timeout=1.0, **kw):
            calls.append(url)
            time.sleep(0.4)
            return {"configured": True, "reachable": True, "url": url}

        with mock.patch.object(srv, "controller_health", slow):
            started = time.monotonic()
            out = asyncio.run(srv._controller_health_for_health("http://x/restart"))
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.1, elapsed)
        # First call has nothing cached: it must say so rather than claim the
        # controller is down, because "unreachable" is the answer that triggers
        # a restart and inventing it would be the expensive direction to be
        # wrong in.
        self.assertIsNone(out["reachable"])
        self.assertTrue(out["probe_pending"])
        self.assertIsNone(out["probe_age_ms"])

    def test_the_result_lands_on_a_later_call(self):
        """Off-loop is only useful if the answer eventually arrives."""
        def quick(url, timeout=1.0, **kw):
            return {"configured": True, "reachable": True, "url": url}

        with mock.patch.object(srv, "controller_health", quick):
            asyncio.run(srv._controller_health_for_health("http://x/restart"))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                out = asyncio.run(srv._controller_health_for_health("http://x/restart"))
                if out.get("reachable") is True:
                    break
                time.sleep(0.02)
        self.assertTrue(out["reachable"], out)
        self.assertIsNotNone(out["probe_age_ms"])
        self.assertFalse(out["probe_pending"])

    def test_repeated_calls_share_one_probe(self):
        """A per-request probe is what made this expensive. Within the TTL the
        blocking call must run once, not once per caller."""
        calls = []

        def counting(url, timeout=1.0, **kw):
            calls.append(url)
            return {"configured": True, "reachable": True, "url": url}

        async def many():
            return [await srv._controller_health_for_health("http://x/restart")
                    for _ in range(12)]

        with mock.patch.object(srv, "controller_health", counting):
            asyncio.run(many())
            time.sleep(0.1)
            asyncio.run(srv._controller_health_for_health("http://x/restart"))
        self.assertEqual(len(calls), 1, calls)

    def test_a_raising_probe_is_reported_not_swallowed(self):
        """An exception in the executor must become a visible unreachable, not a
        cache that stays empty and re-probes forever."""
        def boom(url, timeout=1.0, **kw):
            raise OSError("synthetic probe failure")

        with mock.patch.object(srv, "controller_health", boom):
            asyncio.run(srv._controller_health_for_health("http://x/restart"))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                out = asyncio.run(srv._controller_health_for_health("http://x/restart"))
                if out.get("reachable") is False:
                    break
                time.sleep(0.02)
        self.assertFalse(out["reachable"], out)
        self.assertEqual(out["error_type"], "OSError")
        self.assertIn("synthetic probe failure", out["error_message"])

    def test_health_reports_the_probe_age(self):
        """A five-second-old fact presented as current is how a reader concludes
        the controller is up when it went down four seconds ago."""
        def quick(url, timeout=1.0, **kw):
            return {"configured": True, "reachable": True, "url": url}

        with mock.patch.object(srv, "controller_health", quick):
            asyncio.run(srv._controller_health_for_health("http://x/restart"))
            time.sleep(0.15)
            out = asyncio.run(srv._controller_health_for_health("http://x/restart"))
        self.assertIsNotNone(out["probe_age_ms"])
        self.assertGreaterEqual(out["probe_age_ms"], 0)

    def test_a_different_url_is_not_answered_from_the_old_cache(self):
        """A cached answer about :8085 is not a stale answer about :9085, it is an
        answer to a different question. Without keying the cache on the URL, an
        `ORCHESTRATOR_RESTART_CONTROL_URL` change would keep reporting the old
        port as long as the TTL held -- and it also let one test's warm cache
        satisfy the next test's probe, which is how the contract test for
        exception normalisation started passing alone and failing in company."""
        seen = []

        def probe(url, timeout=1.0, **kw):
            seen.append(url)
            return {"configured": True, "reachable": True, "url": url}

        with mock.patch.object(srv, "controller_health", probe):
            asyncio.run(srv._controller_health_for_health("http://a/restart"))
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not seen:
                time.sleep(0.02)
                asyncio.run(srv._controller_health_for_health("http://a/restart"))
            out = asyncio.run(srv._controller_health_for_health("http://b/restart"))
        self.assertIn("http://a/restart", seen)
        # The second URL must NOT be served the first one's answer.
        self.assertNotEqual(out.get("url"), "http://a/restart")

    def test_handle_health_stays_under_the_latency_budget(self):
        """End to end, through the real handler, with the probe as slow as a
        refused connect on a down port."""
        def slow(url, timeout=1.0, **kw):
            time.sleep(0.4)
            return {"configured": True, "reachable": False, "url": url}

        async def probe():
            started = time.monotonic()
            response = await srv.handle_health(SimpleNamespace(headers={}))
            return time.monotonic() - started, json.loads(response.text)

        with mock.patch.object(srv, "controller_health", slow):
            elapsed, payload = asyncio.run(probe())
        self.assertLess(elapsed, 0.15, elapsed)
        self.assertIn("restart_controller", payload)


if __name__ == "__main__":
    unittest.main()
