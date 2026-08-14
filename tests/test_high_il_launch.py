"""pythonw-direct High-IL launch: no cmd.exe, no start /b, no job breakaway."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from orchestrator.lifecycle import high_il_launch
from orchestrator.lifecycle.as_bat_helpers import CREATE_NO_WINDOW


ROOT = Path(__file__).resolve().parent.parent


class TestHighIlLaunch(unittest.TestCase):
    def test_env_ports_high_il_cmd_semaphores(self):
        self.assertEqual(high_il_launch.HIGH_IL_ENV["ORCH_SEM_READ"], "48")
        self.assertEqual(high_il_launch.HIGH_IL_ENV["ORCH_GLOBAL_CAP"], "96")
        self.assertEqual(high_il_launch.HIGH_IL_ENV["ORCH_EXEC_READ_IO_WORKERS"], "48")
        self.assertEqual(high_il_launch.HIGH_IL_ENV["ORCH_DEFAULT_EXECUTOR_WORKERS"], "56")

    def test_apply_env_sets_vars(self):
        with mock.patch.object(high_il_launch.os, "chdir") as chdir, \
             mock.patch.dict(high_il_launch.os.environ, {}, clear=False):
            high_il_launch.apply_env(ROOT)
            self.assertEqual(high_il_launch.os.environ["ORCH_SEM_READ"], "48")
        chdir.assert_called_once_with(ROOT)

    def test_launch_server_hides_and_stays_in_job(self):
        with mock.patch("orchestrator.lifecycle.console_warp.has_console", return_value=False), \
             mock.patch.object(high_il_launch.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0)
            rc = high_il_launch.launch_server(ROOT, Path("pythonw.exe"))
        self.assertEqual(rc, 0)
        kwargs = run.call_args.kwargs
        flags = int(kwargs.get("creationflags") or 0)
        self.assertEqual(flags, CREATE_NO_WINDOW)
        self.assertFalse(flags & high_il_launch.DETACHED_PROCESS)
        self.assertFalse(flags & high_il_launch.CREATE_BREAKAWAY_FROM_JOB)
        self.assertNotIn("cmd.exe", kwargs.get("args") or run.call_args.args[0])

    def test_source_has_no_cmd_or_start_b(self):
        text = Path(high_il_launch.__file__).read_text(encoding="utf-8")
        self.assertNotIn("cmd.exe /c", text)
        self.assertNotIn('["cmd.exe"', text)
        self.assertNotIn('start "" /b', text)
        self.assertNotIn("os.execv", text)
        self.assertIn("CREATE_BREAKAWAY_FROM_JOB =", text)

    def test_install_action_is_pythonw_direct(self):
        install = (ROOT / "install_orchestrator_admin_task.ps1").read_text(encoding="utf-8")
        self.assertIn("orchestrator.lifecycle.high_il_launch", install)
        self.assertIn("high_il_launch --force", install)
        self.assertIn("restart_control_service --port 8085", install)
        self.assertNotIn("cmd.exe /c", install)
        self.assertNotIn("run-wait-hidden", install)
        self.assertNotIn("Unregister-ScheduledTask -TaskName $forceName", install)
        self.assertNotIn("Unregister-ScheduledTask -TaskName $controlName", install)

    def test_high_il_cmd_dropped_untitled_start(self):
        high_il = (ROOT / "start_orchestrator_high_il.cmd").read_text(encoding="utf-8")
        self.assertNotIn('start "" /b', high_il)
        self.assertNotIn("start \"\" /b", high_il)


if __name__ == "__main__":
    unittest.main()
