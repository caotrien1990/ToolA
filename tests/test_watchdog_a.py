import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import watchdog_a


def _health(*, ok=True, service="project-orchestrator", generation="v1",
            lanes=None, accepting=True, active_total=None, queued_total=None):
    payload = {"ok": ok, "service": service, "generation": generation,
               "executor_accepting": accepting}
    if lanes is not None:
        payload["executor_lanes"] = lanes
    if active_total is not None:
        payload["executor_active_total"] = active_total
    if queued_total is not None:
        payload["executor_queued_total"] = queued_total
    return payload


class TestWatchdogProbeContract(unittest.TestCase):
    """probe() returns (classification, generation, detail, health, livez_ok)."""

    def test_livez_v1_and_ready_health_is_healthy(self):
        livez = {"ok": True, "service": "project-orchestrator",
                 "livez_protocol": 1, "generation": "new"}
        health = _health(generation="new", lanes=[{"active": 0, "queued": 0, "capacity": 4}])
        with mock.patch.object(watchdog_a, "_read_json", side_effect=[livez, health]):
            classification, generation, detail, out_health, livez_ok = watchdog_a.probe()
        self.assertEqual(classification, "healthy")
        self.assertEqual(generation, "new")
        self.assertTrue(livez_ok)
        self.assertEqual(out_health, health)
        self.assertIn("livez_v1", detail)
        self.assertIn("health_ready", detail)

    def test_livez_down_but_health_ready_stays_healthy_with_livez_ok_false(self):
        health = _health(generation="old", lanes=[{"active": 0, "queued": 0, "capacity": 4}])
        with mock.patch.object(watchdog_a, "_read_json", side_effect=[OSError("down"), health]):
            classification, generation, detail, out_health, livez_ok = watchdog_a.probe()
        self.assertEqual(classification, "healthy")
        self.assertEqual(generation, "old")
        self.assertFalse(livez_ok)
        self.assertEqual(out_health, health)
        self.assertIn("livez_OSError", detail)

    def test_livez_responds_but_wrong_protocol_is_not_livez_ok(self):
        # livez endpoint answers cleanly but advertises an unsupported protocol:
        # the contract check must reject it (livez_ok False), not accept the reply.
        livez = {"ok": True, "service": "project-orchestrator",
                 "livez_protocol": 2, "generation": "future"}
        health = _health(generation="future", lanes=[{"active": 0, "queued": 0, "capacity": 4}])
        with mock.patch.object(watchdog_a, "_read_json", side_effect=[livez, health]):
            classification, generation, detail, _out_health, livez_ok = watchdog_a.probe()
        self.assertFalse(livez_ok)
        self.assertEqual(classification, "healthy")
        self.assertEqual(generation, "future")
        self.assertIn("livez_contract_mismatch", detail)

    def test_livez_responds_but_wrong_service_is_not_livez_ok(self):
        # A well-formed reply from the wrong service must also fail the contract.
        livez = {"ok": True, "service": "somebody-else",
                 "livez_protocol": 1, "generation": "x"}
        health = _health(lanes=[{"active": 0, "queued": 0, "capacity": 4}])
        with mock.patch.object(watchdog_a, "_read_json", side_effect=[livez, health]):
            _classification, _generation, detail, _out_health, livez_ok = watchdog_a.probe()
        self.assertFalse(livez_ok)
        self.assertIn("livez_contract_mismatch", detail)

    def test_both_endpoints_down_returns_down_empty_health(self):
        with mock.patch.object(watchdog_a, "_read_json", side_effect=[OSError("no livez"), OSError("no health")]):
            classification, _generation, detail, out_health, livez_ok = watchdog_a.probe()
        self.assertEqual(classification, "down")
        self.assertEqual(out_health, {})
        self.assertFalse(livez_ok)
        self.assertIn("health_OSError", detail)

    def test_livez_alive_overrides_health_down_to_degraded(self):
        livez = {"ok": True, "service": "project-orchestrator",
                 "livez_protocol": 1, "generation": "g"}
        health_down = {"ok": False, "service": "project-orchestrator", "generation": "g"}
        with mock.patch.object(watchdog_a, "_read_json", side_effect=[livez, health_down]):
            classification, _generation, detail, _out_health, livez_ok = watchdog_a.probe()
        # health alone classifies as "down"; a live livez must soften it to "degraded".
        self.assertEqual(classification, "degraded")
        self.assertTrue(livez_ok)
        self.assertIn("livez_alive", detail)

    def test_saturated_executor_is_degraded(self):
        health = _health(lanes=[{"active": 4, "queued": 2, "capacity": 4}])
        with mock.patch.object(watchdog_a, "_read_json", side_effect=[OSError("no livez"), health]):
            classification, _generation, _detail, _out_health, livez_ok = watchdog_a.probe()
        self.assertEqual(classification, "degraded")
        self.assertFalse(livez_ok)


class TestWatchdogDecisionLoop(unittest.TestCase):
    def _paths(self, tempdir: str):
        return (str(Path(tempdir) / "watchdog_state.json"),
                str(Path(tempdir) / "does_not_exist.log"))

    def _busy_health(self):
        return _health(lanes=[{"active": 1, "queued": 0, "capacity": 4}], active_total=1)

    def test_liveness_lost_and_stalled_restarts_exactly_once(self):
        """down + not livez_ok, held busy with no progress past the inactivity
        window across FAILURE_THRESHOLD samples, must trigger exactly one restart."""
        busy = self._busy_health()
        samples = [("down", "v1", "livez_down;health_down", busy, False)] * watchdog_a.FAILURE_THRESHOLD
        completed = subprocess.CompletedProcess([], 0, "ok", "")
        with tempfile.TemporaryDirectory() as tempdir:
            state_path, log_path = self._paths(tempdir)
            with mock.patch.object(watchdog_a, "STATE", state_path), \
                 mock.patch.object(watchdog_a, "LOG", log_path):
                # Seed state so the inactivity accounting counts these samples as stalled:
                # same generation (no reset), matching fingerprint (no progress),
                # and a last_progress_at far in the past.
                seed_fp = watchdog_a._progress_fingerprint(busy)
                Path(state_path).write_text(json.dumps({
                    "generation": "v1",
                    "last_fp": seed_fp,
                    "last_progress_at": time.time() - 10_000,
                    "inactive_samples": 0,
                }), encoding="utf-8")
                with mock.patch.object(watchdog_a, "probe", side_effect=samples), \
                     mock.patch.object(watchdog_a.time, "sleep"), \
                     mock.patch.object(watchdog_a, "log"), \
                     mock.patch.object(watchdog_a.subprocess, "run", return_value=completed) as run:
                    rc = watchdog_a.main()
        self.assertEqual(rc, 1)
        run.assert_called_once()

    def test_liveness_lost_but_not_stalled_does_not_restart(self):
        """down + not livez_ok but executor idle (not busy) => inactivity never
        accrues => the restart gate stays shut."""
        idle = _health(lanes=[{"active": 0, "queued": 0, "capacity": 4}])
        samples = [("down", "v1", "livez_down;health_down", idle, False)] * watchdog_a.FAILURE_THRESHOLD
        with tempfile.TemporaryDirectory() as tempdir:
            state_path, log_path = self._paths(tempdir)
            with mock.patch.object(watchdog_a, "STATE", state_path), \
                 mock.patch.object(watchdog_a, "LOG", log_path), \
                 mock.patch.object(watchdog_a, "probe", side_effect=samples), \
                 mock.patch.object(watchdog_a.time, "sleep"), \
                 mock.patch.object(watchdog_a, "log"), \
                 mock.patch.object(watchdog_a.subprocess, "run") as run:
                rc = watchdog_a.main()
        self.assertEqual(rc, 0)
        run.assert_not_called()

    def test_livez_ok_short_circuits_restart_even_when_down(self):
        """Classification 'down' but livez_ok True must never reach the restart
        channel: liveness is proven, so main exits 0 on the first sample."""
        busy = self._busy_health()
        samples = [("down", "v1", "livez_alive;health_down", busy, True)] * watchdog_a.FAILURE_THRESHOLD
        with tempfile.TemporaryDirectory() as tempdir:
            state_path, log_path = self._paths(tempdir)
            with mock.patch.object(watchdog_a, "STATE", state_path), \
                 mock.patch.object(watchdog_a, "LOG", log_path), \
                 mock.patch.object(watchdog_a, "probe", side_effect=samples), \
                 mock.patch.object(watchdog_a.time, "sleep"), \
                 mock.patch.object(watchdog_a, "log"), \
                 mock.patch.object(watchdog_a.subprocess, "run") as run:
                rc = watchdog_a.main()
        self.assertEqual(rc, 0)
        run.assert_not_called()

    def test_failure_then_recovery_does_not_restart(self):
        samples = [
            ("down", "", "both_down", {}, False),
            ("healthy", "v2", "livez_v1;health_ready", self._busy_health(), True),
        ]
        with tempfile.TemporaryDirectory() as tempdir:
            state_path, log_path = self._paths(tempdir)
            with mock.patch.object(watchdog_a, "STATE", state_path), \
                 mock.patch.object(watchdog_a, "LOG", log_path), \
                 mock.patch.object(watchdog_a, "probe", side_effect=samples), \
                 mock.patch.object(watchdog_a.time, "sleep"), \
                 mock.patch.object(watchdog_a, "log"), \
                 mock.patch.object(watchdog_a.subprocess, "run") as run:
                rc = watchdog_a.main()
        self.assertEqual(rc, 0)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
