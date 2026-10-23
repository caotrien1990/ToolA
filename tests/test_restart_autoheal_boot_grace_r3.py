"""R3 (2026-10-15): auto-heal must NOT kill a daemon that is still booting.

K1: on 2026-10-15 the heal loop killed healthy daemons 5 times mid-boot
(21:37-21:51) because /livez (thread-isolated) answers ~3min before /health
(aiohttp not started yet). The loop saw livez_ok=True + health_alive=False
and concluded "hung".

Fix: the daemon reports startup_phase + boot_age_s on /livez (protocol 2).
If phase is not in {ready, ready_degraded} and boot_age_s < BOOT_GRACE_S
(600s), the healer skips the hung_streak increment and audits
"auto_heal_skip_booting". Protocol 1 daemons keep legacy behavior.
"""
import json
import unittest
from unittest import mock

from orchestrator.lifecycle import restart_control_service as rcs


def _livez_response(payload: dict) -> mock.MagicMock:
    """Build a mock urlopen context manager returning the given payload."""
    body = json.dumps(payload).encode("utf-8")
    resp = mock.MagicMock()
    resp.read.return_value = body
    ctx = mock.MagicMock()
    ctx.__enter__.return_value = resp
    ctx.__exit__.return_value = False
    return ctx


def _payload(*, protocol=2, phase="booting", boot_age_s=100.0):
    return {
        "ok": True,
        "pid": 1234,
        "generation": "abc123",
        "livez_protocol": protocol,
        "startup_phase": phase,
        "boot_age_s": boot_age_s,
    }


class TestSkipHealDuringBoot(unittest.TestCase):
    def _run(self, payload=None, exc=None, **kwargs):
        if exc is not None:
            opener = mock.MagicMock(side_effect=exc)
        else:
            opener = mock.MagicMock(return_value=_livez_response(payload))
        with mock.patch.object(rcs.urllib.request, "urlopen", opener):
            return rcs._should_skip_heal_during_boot("http://127.0.0.1:8084/livez", **kwargs)

    def test_booting_within_grace_skips(self):
        # The core R3 case: booting daemon, well within grace -> skip heal.
        skip, reason = self._run(_payload(phase="booting", boot_age_s=100.0))
        self.assertTrue(skip, f"expected skip, got reason={reason}")
        self.assertIn("booting", reason)

    def test_boot_write_sweep_phase_skips(self):
        # The pre-existing boot-recovery phase also counts as booting.
        skip, reason = self._run(_payload(phase="boot_write_sweep", boot_age_s=50.0))
        self.assertTrue(skip, f"expected skip, got reason={reason}")

    def test_booting_past_grace_does_not_skip(self):
        # Past 600s and still not ready -> treat as hung like before.
        skip, reason = self._run(_payload(phase="booting", boot_age_s=700.0))
        self.assertFalse(skip)
        self.assertEqual(reason, "boot_grace_expired")

    def test_ready_does_not_skip(self):
        skip, reason = self._run(_payload(phase="ready", boot_age_s=100.0))
        self.assertFalse(skip)
        self.assertEqual(reason, "already_ready")

    def test_ready_degraded_does_not_skip(self):
        skip, reason = self._run(_payload(phase="ready_degraded", boot_age_s=100.0))
        self.assertFalse(skip)
        self.assertEqual(reason, "already_ready")

    def test_protocol_1_legacy_does_not_skip(self):
        # Old daemons (protocol 1) have no phase info -> legacy behavior.
        skip, reason = self._run(_payload(protocol=1, phase="booting", boot_age_s=100.0))
        self.assertFalse(skip)
        self.assertEqual(reason, "protocol_1_legacy")

    def test_livez_unreachable_does_not_skip(self):
        skip, reason = self._run(exc=ConnectionRefusedError(10061, "refused"))
        self.assertFalse(skip)
        self.assertEqual(reason, "livez_unreachable")

    def test_invalid_boot_age_does_not_skip(self):
        payload = _payload(phase="booting")
        payload["boot_age_s"] = "not-a-number"
        skip, reason = self._run(payload)
        self.assertFalse(skip)
        self.assertEqual(reason, "invalid_boot_age")

    def test_custom_grace_period(self):
        # Grace is tunable: 100s age with 60s grace -> expired.
        skip, reason = self._run(
            _payload(phase="booting", boot_age_s=100.0), boot_grace_s=60.0)
        self.assertFalse(skip)
        self.assertEqual(reason, "boot_grace_expired")


if __name__ == "__main__":
    unittest.main()
