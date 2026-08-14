"""via_bat must queue a python.exe helper, not a titled cmd.exe wrapper."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from orchestrator.lifecycle import console_warp, via_bat_helper


ROOT = Path(__file__).resolve().parent.parent


class TestViaBatHelper(unittest.TestCase):
    def test_tools_v15_spawns_python_module_not_cmd(self):
        text = (ROOT / "tools_v15.py").read_text(encoding="utf-8")
        start = text.index("async def restart_orchestrator_via_bat")
        end = text.index("async def cancel_pending_restart")
        chunk = text[start:end]
        self.assertIn("orchestrator.lifecycle.via_bat_helper", chunk)
        self.assertIn("python.exe", chunk)
        self.assertIn("start_orchestrator.bat (hidden helper)", chunk)
        self.assertNotIn('["cmd", "/c", str(helper_path)]', chunk)
        self.assertNotIn('["cmd.exe", "/c", str(helper_path)]', chunk)

    def test_run_as_bat_hides_console(self):
        with mock.patch.object(console_warp, "has_console", return_value=False), \
             mock.patch.object(via_bat_helper.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0)
            log = ROOT / ".orchestrator_cache" / "restart_bat_helper.log"
            via_bat_helper.run_as_bat(ROOT / "start_orchestrator.bat", ROOT, log)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs.get("creationflags"), via_bat_helper.CREATE_NO_WINDOW)
        self.assertTrue(kwargs.get("startupinfo").dwFlags & console_warp.STARTF_USESHOWWINDOW)
        argv = kwargs.get("args") or run.call_args.args[0]
        self.assertEqual(argv[0], "cmd.exe")
        self.assertEqual(argv[1], "/c")

    def test_run_as_bat_inherits_hidden_console(self):
        with mock.patch.object(console_warp, "has_console", return_value=True), \
             mock.patch.object(via_bat_helper.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0)
            log = ROOT / ".orchestrator_cache" / "restart_bat_helper.log"
            via_bat_helper.run_as_bat(ROOT / "start_orchestrator.bat", ROOT, log)
        flags = int(run.call_args.kwargs.get("creationflags") or 0)
        self.assertFalse(flags & via_bat_helper.CREATE_NO_WINDOW)
        self.assertFalse(flags & console_warp.CREATE_NEW_CONSOLE)
        self.assertFalse(flags & console_warp.DETACHED_PROCESS)
        argv = run.call_args.kwargs.get("args") or run.call_args.args[0]
        self.assertEqual(argv[0], "cmd.exe")
        self.assertEqual(argv[1], "/c")


if __name__ == "__main__":
    unittest.main()
