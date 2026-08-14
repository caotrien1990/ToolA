import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import watchdog_a


class TestWatchdogProbeContract(unittest.TestCase):
    def test_protocol_v1_livez_is_healthy(self):
        livez = {
            "ok": True,
            "service": "project-orchestrator",
            "livez_protocol": 1,
            "generation": "new",
        }
        with mock.patch.object(watchdog_a, "_read_json", return_value=livez) as read:
            state, generation, detail = watchdog_a.probe()
        self.assertEqual((state, generation, detail), ("healthy", "new", "livez_v1"))
        self.assertEqual(read.call_count, 1)

    def test_failed_livez_with_legacy_health_is_accepted(self):
        legacy = {"ok": True, "service": "project-orchestrator", "generation": "old"}
        with mock.patch.object(watchdog_a, "_read_json", side_effect=[OSError("down"), legacy]):
            state, generation, _ = watchdog_a.probe()
        self.assertEqual(state, "legacy_alive")
        self.assertEqual(generation, "old")

    def test_capable_health_with_failed_livez_is_failure(self):
        capable = {
            "ok": True,
            "service": "project-orchestrator",
            "livez_protocol": 1,
            "generation": "v1",
        }
        with mock.patch.object(watchdog_a, "_read_json", side_effect=[OSError("down"), capable]):
            state, generation, _ = watchdog_a.probe()
        self.assertEqual(state, "capable_broken")
        self.assertEqual(generation, "v1")

    def test_unknown_newer_protocol_fails_open_to_healthy_health(self):
        unknown = {
            "ok": True,
            "service": "project-orchestrator",
            "livez_protocol": 2,
            "generation": "future",
        }
        with mock.patch.object(watchdog_a, "_read_json", side_effect=[unknown, unknown]):
            state, generation, _ = watchdog_a.probe()
        self.assertEqual(state, "unsupported_alive")
        self.assertEqual(generation, "future")


class TestWatchdogDecisionLoop(unittest.TestCase):
    def _state_path(self, tempdir: str) -> str:
        return str(Path(tempdir) / "watchdog_state.json")

    def test_three_capable_failures_restart_exactly_once(self):
        samples = [("capable_broken", "v1", "livez_down;health_v1")] * 3
        completed = subprocess.CompletedProcess([], 0, "ok", "")
        with tempfile.TemporaryDirectory() as tempdir, \
             mock.patch.object(watchdog_a, "STATE", self._state_path(tempdir)), \
             mock.patch.object(watchdog_a, "probe", side_effect=samples), \
             mock.patch.object(watchdog_a.time, "sleep"), \
             mock.patch.object(watchdog_a, "log"), \
             mock.patch.object(watchdog_a.subprocess, "run", return_value=completed) as run:
            rc = watchdog_a.main()
        self.assertEqual(rc, 1)
        run.assert_called_once()

    def test_failure_then_recovery_does_not_restart(self):
        samples = [
            ("down", "", "both_down"),
            ("healthy", "v2", "livez_v1"),
        ]
        with tempfile.TemporaryDirectory() as tempdir, \
             mock.patch.object(watchdog_a, "STATE", self._state_path(tempdir)), \
             mock.patch.object(watchdog_a, "probe", side_effect=samples), \
             mock.patch.object(watchdog_a.time, "sleep"), \
             mock.patch.object(watchdog_a, "log"), \
             mock.patch.object(watchdog_a.subprocess, "run") as run:
            rc = watchdog_a.main()
        self.assertEqual(rc, 0)
        run.assert_not_called()

    def test_legacy_server_never_enters_restart_channel(self):
        with tempfile.TemporaryDirectory() as tempdir, \
             mock.patch.object(watchdog_a, "STATE", self._state_path(tempdir)), \
             mock.patch.object(watchdog_a, "probe", return_value=("legacy_alive", "old", "legacy_health")), \
             mock.patch.object(watchdog_a, "log"), \
             mock.patch.object(watchdog_a.subprocess, "run") as run:
            rc = watchdog_a.main()
        self.assertEqual(rc, 0)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()