"""R45 (2026-08-11): the auto-heal watchdog must heal ONLY on a genuine event-loop
deadlock (/health gives NO HTTP response at all), never on a slow-but-alive or
degraded (ok=false / 503) reply. Counting a responsive-but-degraded or a
freshly-restarted (warming-up) instance as 'hung' caused restart churn — a
just-healed process being healed again (observed 2026-08-11: 26c037->b7188d->570109).

Locks the _health_is_responsive semantics + the conservative tuning defaults.
"""
import inspect
import unittest
import urllib.error

from orchestrator.lifecycle import restart_control_service as rcs


class _Resp:
    def __init__(self, body=b'{"ok": true}'):
        self._b = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


class TestHealthResponsive(unittest.TestCase):
    def test_ok_200_is_responsive(self):
        self.assertTrue(rcs._health_is_responsive(
            "http://x", 1.0, opener=lambda *a, **k: _Resp(b'{"ok": true}')))

    def test_degraded_ok_false_is_still_responsive(self):
        # ok=false = a subsystem is degraded but the LOOP answered -> alive, not a
        # deadlock. Restarting would only disrupt work; must NOT count as hung.
        self.assertTrue(rcs._health_is_responsive(
            "http://x", 1.0, opener=lambda *a, **k: _Resp(b'{"ok": false}')))

    def test_http_error_status_is_responsive(self):
        def opener(*a, **k):
            raise urllib.error.HTTPError("http://x", 503, "unavailable", {}, None)
        self.assertTrue(rcs._health_is_responsive("http://x", 1.0, opener=opener))

    def test_timeout_is_not_responsive(self):
        def opener(*a, **k):
            raise urllib.error.URLError("timed out")
        self.assertFalse(rcs._health_is_responsive("http://x", 1.0, opener=opener))

    def test_connection_refused_is_not_responsive(self):
        def opener(*a, **k):
            raise ConnectionRefusedError(10061, "refused")
        self.assertFalse(rcs._health_is_responsive("http://x", 1.0, opener=opener))


class TestAutoHealTuning(unittest.TestCase):
    def test_conservative_defaults(self):
        d = {k: v.default for k, v in inspect.signature(rcs._auto_heal_monitor).parameters.items()}
        # R45 hardening: higher streak + timeout + a warmup grace so a busy/degraded
        # or freshly-restarted loop is never mistaken for a deadlock.
        self.assertGreaterEqual(d["hung_threshold"], 5)
        self.assertGreaterEqual(d["health_timeout"], 10.0)
        self.assertGreaterEqual(d["warmup_grace_s"], 30.0)


class TestAutoHealInflightDefer(unittest.TestCase):
    def test_fresh_busy_snapshot_defers_kill(self):
        now = 1_000_000.0
        snap = {
            "busy": True,
            "requests_in_flight": 1,
            "executor_in_flight": 0,
            "accounting_pending": 0,
            "captured_at": now - 2.0,
        }
        defer, why = rcs._should_defer_auto_heal(snap, now=now)
        self.assertTrue(defer)
        self.assertEqual(why, "inflight")

    def test_stale_snapshot_does_not_defer(self):
        now = 1_000_000.0
        snap = {
            "busy": True,
            "requests_in_flight": 1,
            "captured_at": now - 120.0,
        }
        defer, why = rcs._should_defer_auto_heal(snap, now=now)
        self.assertFalse(defer)
        self.assertEqual(why, "snapshot_stale")

    def test_idle_fresh_snapshot_does_not_defer(self):
        now = 1_000_000.0
        snap = {
            "busy": False,
            "requests_in_flight": 0,
            "executor_in_flight": 0,
            "accounting_pending": 0,
            "captured_at": now - 1.0,
        }
        defer, why = rcs._should_defer_auto_heal(snap, now=now)
        self.assertFalse(defer)
        self.assertEqual(why, "not_busy")

    def test_missing_snapshot_does_not_defer(self):
        defer, why = rcs._should_defer_auto_heal(None)
        self.assertFalse(defer)
        self.assertEqual(why, "no_snapshot")

    def test_defer_cap_reopens_heal(self):
        now = 1_000_000.0
        snap = {
            "busy": True,
            "requests_in_flight": 1,
            "captured_at": now - 1.0,
        }
        defer, why = rcs._should_defer_auto_heal(snap, now=now, defer_streak=20)
        self.assertFalse(defer)
        self.assertEqual(why, "defer_cap")


if __name__ == "__main__":
    unittest.main()
