import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import watchdog_a


def _health(*, ok=True, service="project-orchestrator", generation="v1",
            lanes=None, accepting=True, active_total=None, queued_total=None,
            processed_total=0, failed_total=0, rejected_total=0):
    """LIVE /health shape: lane rows carry only {name, active, queued,
    accepting}; the monotonic work counters are top-level accounting_* totals.
    Probe-contract tests that need lane keys live /health does NOT publish
    (capacity, oldest_wait_ms) pass an explicit `lanes` list; the top-level
    totals then default to the lane sums, like _lane_totals' own fallback."""
    if lanes is None:
        lanes = [{"name": "test", "active": active_total or 0,
                  "queued": queued_total or 0, "accepting": accepting}]
    if active_total is None:
        active_total = sum(int(x.get("active", 0)) for x in lanes)
    if queued_total is None:
        queued_total = sum(int(x.get("queued", 0)) for x in lanes)
    return {"ok": ok, "service": service, "generation": generation,
            "executor_accepting": accepting,
            "executor_active_total": active_total,
            "executor_queued_total": queued_total,
            "executor_lanes": lanes,
            "accounting_processed_total": processed_total,
            "accounting_failed_total": failed_total,
            "accounting_rejected_total": rejected_total}


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
    """Real-sequence tests: the REAL probe() runs against patched _read_json,
    the REAL log()/state write to tmp paths; nothing fabricates a probe tuple."""

    def _paths(self, tempdir: str):
        return (str(Path(tempdir) / "watchdog_state.json"),
                str(Path(tempdir) / "does_not_exist.log"))

    def _livez(self, generation="g1"):
        return {"ok": True, "service": "project-orchestrator",
                "livez_protocol": watchdog_a.LIVEZ_PROTOCOL, "generation": generation}

    def test_dead_daemon_restarts_exactly_once(self):
        """Pins D1 (unreachable restart): a failed health probe returns
        health={}, so busy=False and inactive_samples reset to 0 on EVERY down
        probe -- the old inactive_samples gate never fired schtasks for a dead
        daemon. With both endpoints refusing, ONE main() from EMPTY state must
        issue exactly one schtasks /run, then persist down_samples=0 and
        last_restart_at so the same firing cannot restart twice."""
        completed = subprocess.CompletedProcess([], 0, "ok", "")
        with tempfile.TemporaryDirectory() as tempdir:
            state_path, log_path = self._paths(tempdir)
            with mock.patch.object(watchdog_a, "STATE", state_path), \
                 mock.patch.object(watchdog_a, "LOG", log_path), \
                 mock.patch.object(watchdog_a, "_read_json",
                                   side_effect=ConnectionRefusedError("dead")), \
                 mock.patch.object(watchdog_a.time, "sleep"), \
                 mock.patch.object(watchdog_a.subprocess, "run", return_value=completed) as run:
                rc = watchdog_a.main()
            saved = json.loads(Path(state_path).read_text(encoding="utf-8"))
        self.assertEqual(rc, 1)
        run.assert_called_once_with(["schtasks", "/run", "/tn", watchdog_a.RESTART_TASK],
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(saved["down_samples"], 0)
        self.assertTrue(saved["last_restart_at"] > 0)
        self.assertEqual(saved["last_classification"], "down")

    def test_second_dead_firing_restarts_again(self):
        """Pins the no-latch half of D1: a daemon that stays dead must be
        retried once per later firing. The post-restart zeroing must not latch
        the counter shut -- firing 2 issues exactly one MORE restart (2 total)."""
        completed = subprocess.CompletedProcess([], 0, "ok", "")
        with tempfile.TemporaryDirectory() as tempdir:
            state_path, log_path = self._paths(tempdir)
            with mock.patch.object(watchdog_a, "STATE", state_path), \
                 mock.patch.object(watchdog_a, "LOG", log_path), \
                 mock.patch.object(watchdog_a, "_read_json",
                                   side_effect=ConnectionRefusedError("dead")), \
                 mock.patch.object(watchdog_a.time, "sleep"), \
                 mock.patch.object(watchdog_a.subprocess, "run", return_value=completed) as run:
                rc1 = watchdog_a.main()
                rc2 = watchdog_a.main()
            saved = json.loads(Path(state_path).read_text(encoding="utf-8"))
        self.assertEqual((rc1, rc2), (1, 1))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(saved["down_samples"], 0)

    def test_recovery_inside_firing_does_not_restart(self):
        """Pins the gate's reset side: two down samples then a healthy reply
        INSIDE one firing must give 0 restarts, and the healthy sample resets
        down_samples to 0 -- recovery is not punished by the earlier failures."""
        livez = self._livez()
        health = _health(generation="g1", active_total=0, queued_total=0)
        replies = [ConnectionRefusedError("dead")] * 4 + [livez, health]
        with tempfile.TemporaryDirectory() as tempdir:
            state_path, log_path = self._paths(tempdir)
            with mock.patch.object(watchdog_a, "STATE", state_path), \
                 mock.patch.object(watchdog_a, "LOG", log_path), \
                 mock.patch.object(watchdog_a, "_read_json", side_effect=replies), \
                 mock.patch.object(watchdog_a.time, "sleep"), \
                 mock.patch.object(watchdog_a.subprocess, "run") as run:
                rc = watchdog_a.main()
            saved = json.loads(Path(state_path).read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        run.assert_not_called()
        self.assertEqual(saved["down_samples"], 0)
        self.assertEqual(saved["last_classification"], "healthy")

    def test_livez_alive_health_failing_never_restarts(self):
        """Pins the docstring invariant: inactivity/degradation only WARNS; a
        restart requires PROVEN liveness loss (/livez AND /health both fail).
        A hung loop reads health as failing (the endpoint refuses, so probe()
        returns classification "down" early), but livez is alive, so livez_ok
        is True: main() short-circuits on the FIRST sample (one probe pair, no
        sleep, no restart) and down_samples never accrues because a "down"
        classification alone is not liveness loss while livez still answers."""
        livez = self._livez("g9")
        replies = [livez, OSError("health hung")]
        with tempfile.TemporaryDirectory() as tempdir:
            state_path, log_path = self._paths(tempdir)
            with mock.patch.object(watchdog_a, "STATE", state_path), \
                 mock.patch.object(watchdog_a, "LOG", log_path), \
                 mock.patch.object(watchdog_a, "_read_json", side_effect=replies) as read_mock, \
                 mock.patch.object(watchdog_a.time, "sleep") as sleep_mock, \
                 mock.patch.object(watchdog_a.subprocess, "run") as run:
                rc = watchdog_a.main()
            saved = json.loads(Path(state_path).read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        run.assert_not_called()
        sleep_mock.assert_not_called()
        self.assertEqual(read_mock.call_count, 2)
        self.assertEqual(saved["down_samples"], 0)
        self.assertEqual(saved["last_classification"], "down")

    def test_daemon_restarted_mid_firing_keeps_state_apart(self):
        """Pins the generation-reset branch under the REAL probe: when [A]
        restarts mid-loop the generation flips, wiping accrued sample state so
        the watchdog never acts on the OLD instance's samples. A dead first
        sample followed by a NEW-generation healthy reply gives 0 restarts and
        down_samples 0."""
        livez = self._livez("g2")
        health = _health(generation="g2", active_total=0, queued_total=0)
        replies = [ConnectionRefusedError("dead"), ConnectionRefusedError("dead"),
                   livez, health]
        with tempfile.TemporaryDirectory() as tempdir:
            state_path, log_path = self._paths(tempdir)
            with mock.patch.object(watchdog_a, "STATE", state_path), \
                 mock.patch.object(watchdog_a, "LOG", log_path), \
                 mock.patch.object(watchdog_a, "_read_json", side_effect=replies), \
                 mock.patch.object(watchdog_a.time, "sleep"), \
                 mock.patch.object(watchdog_a.subprocess, "run") as run:
                rc = watchdog_a.main()
            saved = json.loads(Path(state_path).read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        run.assert_not_called()
        self.assertEqual(saved["generation"], "g2")
        self.assertEqual(saved["down_samples"], 0)


if __name__ == "__main__":
    unittest.main()
