"""as_bat_helpers must hide every console-subsystem child."""
from __future__ import annotations

import argparse
import unittest
from unittest import mock

from orchestrator.lifecycle import as_bat_helpers, console_warp


class TestAsBatHelpersHidden(unittest.TestCase):
    def test_run_hidden_uses_create_no_window(self):
        with mock.patch.object(console_warp, "has_console", return_value=False), \
             mock.patch.object(as_bat_helpers.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            as_bat_helpers.run_hidden(["schtasks", "/query", "/tn", "ToolA_Orchestrator"], timeout=5)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs.get("creationflags"), as_bat_helpers.CREATE_NO_WINDOW)
        self.assertTrue(kwargs.get("startupinfo").dwFlags & as_bat_helpers.STARTF_USESHOWWINDOW)

    def test_run_hidden_inherits_when_console_present(self):
        with mock.patch.object(console_warp, "has_console", return_value=True), \
             mock.patch.object(as_bat_helpers.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            as_bat_helpers.run_hidden(["schtasks", "/query", "/tn", "ToolA_Orchestrator"], timeout=5)
        flags = int(run.call_args.kwargs.get("creationflags") or 0)
        self.assertFalse(flags & as_bat_helpers.CREATE_NO_WINDOW)
        self.assertFalse(flags & console_warp.CREATE_NEW_CONSOLE)
        self.assertFalse(flags & console_warp.DETACHED_PROCESS)

    def test_run_wait_hidden_has_no_stdio_pipes(self):
        with mock.patch.object(console_warp, "has_console", return_value=False), \
             mock.patch.object(as_bat_helpers.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0)
            as_bat_helpers.cmd_run_wait_hidden(
                mock.Mock(argv=["cmd.exe", "/c", "start_orchestrator_high_il.cmd"])
            )
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs.get("creationflags"), as_bat_helpers.CREATE_NO_WINDOW)
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)

    def test_livez_contract_requires_triplet(self):
        src = as_bat_helpers.__file__
        text = open(src, encoding="utf-8").read()
        self.assertIn("livez_protocol", text)
        self.assertIn("project-orchestrator", text)
        self.assertIn("CREATE_NO_WINDOW", text)
        self.assertNotIn("WindowStyle Hidden", text)
        self.assertNotIn("Get-NetTCPConnection", text)

    def test_stop_task_uses_schtasks_not_powershell(self):
        with mock.patch.object(as_bat_helpers, "run_hidden") as hidden:
            hidden.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            rc = as_bat_helpers.cmd_stop_task(argparse.Namespace(name="ToolA_Orchestrator"))
        self.assertEqual(rc, 0)
        argv = hidden.call_args.args[0]
        self.assertEqual(argv, ["schtasks", "/end", "/tn", "ToolA_Orchestrator"])

    def test_wait_task_state_uses_task_state_helper(self):
        with mock.patch.object(as_bat_helpers, "_task_state", return_value="Ready"):
            rc = as_bat_helpers.cmd_wait_task_state(
                argparse.Namespace(name="ToolA_Orchestrator", state="Ready", timeout_s=1)
            )
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
