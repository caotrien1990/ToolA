import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools

class FakeProcess:
    def __init__(self, timeout=False, stdout="stdout", stderr="stderr"):
        self.pid, self.returncode, self.timeout, self.calls = 43210, 0, timeout, 0
        self.stdout_value, self.stderr_value = stdout, stderr
    def communicate(self, timeout=None):
        self.calls += 1
        if self.timeout and self.calls == 1:
            raise subprocess.TimeoutExpired("runner", timeout)
        if self.timeout: self.returncode = 1
        return self.stdout_value, self.stderr_value
    def poll(self): return self.returncode
    def kill(self): self.returncode = -9

class CommandTransportC2Tests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="command_transport_c2_")
        self.project = Path(self.tempdir.name)
    def tearDown(self): self.tempdir.cleanup()

    def test_git_pager_and_streams_are_lossless(self):
        fake = FakeProcess(stdout="commit abc123\n", stderr="git warning\n")
        with mock.patch.object(code_tools.subprocess, "Popen", return_value=fake) as popen:
            result = json.loads(asyncio.run(code_tools.run_project_command(str(self.project), ["git", "log", "-1"], shell_mode="exec")))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stdout"], "commit abc123\n")
        self.assertEqual(result["stderr"], "git warning\n")
        self.assertEqual(popen.call_args.kwargs["env"]["GIT_PAGER"], "cat")

    def test_temp_expansion_and_process_group_remain_enabled(self):
        fake = FakeProcess()
        with mock.patch.object(code_tools.subprocess, "Popen", return_value=fake) as popen:
            result = json.loads(asyncio.run(code_tools.run_project_command(str(self.project), r"pytest --basetemp=%TEMP%\c2", shell_mode="cmd")))
        self.assertTrue(result["ok"], result)
        self.assertNotIn("%TEMP%", popen.call_args.args[0].upper())
        self.assertTrue(popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else popen.call_args.kwargs["start_new_session"])

    def test_command_timeout_invokes_taskkill_tree_and_keeps_stderr(self):
        fake = FakeProcess(timeout=True, stdout="final stdout", stderr="final stderr")
        taskkill = mock.Mock(return_value=mock.Mock(returncode=0, stdout="SUCCESS", stderr=""))
        with mock.patch.object(code_tools.subprocess, "Popen", return_value=fake), mock.patch.object(code_tools.subprocess, "run", taskkill), mock.patch.object(code_tools.sys, "platform", "win32"):
            result = json.loads(asyncio.run(code_tools.run_project_command(str(self.project), ["python", "hung.py"], shell_mode="exec", timeout=1)))
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["stderr"], "final stderr")
        taskkill.assert_called_once_with(["taskkill", "/PID", str(fake.pid), "/T", "/F"], capture_output=True, text=True, timeout=10)

    def test_script_runner_uses_shared_tree_timeout_and_clean_env(self):
        fake = FakeProcess(timeout=True, stdout="script stdout", stderr="script stderr")
        selected = {"ok": True, "executable": sys.executable}
        communicate = mock.Mock(return_value=("script stdout", "script stderr", True, {"method": "taskkill"}))
        with mock.patch.dict(code_tools.os.environ, {}, clear=True), mock.patch.object(code_tools, "_select_python_interpreter", return_value=selected), mock.patch.object(code_tools.subprocess, "Popen", return_value=fake) as popen, mock.patch.object(code_tools, "_communicate_with_tree_timeout", communicate):
            result = json.loads(asyncio.run(code_tools.run_project_test_script(str(self.project), "print(1)", runtime="python", timeout=1)))
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["stderr"], "script stderr")
        self.assertNotIn("GIT_PAGER", popen.call_args.kwargs["env"])
        communicate.assert_called_once()

    def test_nul_suppression_is_not_log_redirection(self):
        source = Path(code_tools.__file__).read_text(encoding="utf-8")
        self.assertIn("nul_suppression", source)
        self.assertIn(r"d*>{1,2}\s*nul\b", source)
        fake = FakeProcess(stdout="ok\n", stderr="")
        with mock.patch.object(code_tools.subprocess, "Popen", return_value=fake):
            result = json.loads(asyncio.run(code_tools.run_project_command(
                str(self.project), "dir 2>nul", shell_mode="cmd",
            )))
        self.assertNotEqual(result.get("error_name"), "REDIRECTION_NOT_EFFECTIVE")
        self.assertTrue(result.get("nul_suppression") or not result.get("redirection_requested"))
        executed = result.get("command_executed") or result.get("command")
        blob = executed if isinstance(executed, str) else " ".join(str(x) for x in (executed or []))
        self.assertIn("2>nul", blob)
        self.assertNotIn("2^>nul", blob)

    def test_cmd_escape_preserves_stream_suppression_operators(self):
        fake = FakeProcess(stdout="ok\n", stderr="")
        with mock.patch.object(code_tools.subprocess, "Popen", return_value=fake) as popen:
            result = json.loads(asyncio.run(code_tools.run_project_command(
                str(self.project), "echo ok 2>nul", shell_mode="cmd",
            )))
        launched = popen.call_args.args[0]
        launched_s = launched if isinstance(launched, str) else " ".join(str(x) for x in launched)
        self.assertIn("2>nul", launched_s)
        self.assertNotIn("2^>nul", launched_s)
        self.assertTrue(result.get("nul_suppression"))
        self.assertNotEqual(result.get("error_name"), "REDIRECTION_NOT_EFFECTIVE")

    def test_cmd_escape_still_escapes_file_redirect(self):
        fake = FakeProcess(stdout="hi\n", stderr="")
        with mock.patch.object(code_tools.subprocess, "Popen", return_value=fake) as popen:
            json.loads(asyncio.run(code_tools.run_project_command(
                str(self.project), "echo hi > log.txt", shell_mode="cmd",
            )))
        launched = popen.call_args.args[0]
        launched_s = launched if isinstance(launched, str) else " ".join(str(x) for x in launched)
        self.assertIn("^>", launched_s)

    def test_auto_does_not_treat_ps1_argument_as_script_file(self):
        fake = FakeProcess(stdout="runner.ps1\n", stderr="")
        with mock.patch.object(code_tools.subprocess, "Popen", return_value=fake) as popen:
            result = json.loads(asyncio.run(code_tools.run_project_command(
                str(self.project), "findstr /n /i pattern runner.ps1", shell_mode="auto",
            )))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["shell_mode_resolved"], "cmd")
        launched = popen.call_args.args[0]
        launched_s = launched if isinstance(launched, str) else " ".join(str(x) for x in launched)
        self.assertIn("cmd", launched_s.lower())
        self.assertNotIn("-File", launched_s)

    def test_explicit_powershell_uses_command_for_ps1_argument(self):
        fake = FakeProcess(stdout="runner.ps1\n", stderr="")
        with mock.patch.object(code_tools.subprocess, "Popen", return_value=fake) as popen:
            result = json.loads(asyncio.run(code_tools.run_project_command(
                str(self.project), "findstr /n /i pattern runner.ps1", shell_mode="powershell",
            )))
        self.assertTrue(result["ok"], result)
        launched = popen.call_args.args[0]
        launched_s = launched if isinstance(launched, str) else " ".join(str(x) for x in launched)
        self.assertIn("-Command", launched_s)
        self.assertNotIn("-File", launched_s)

if __name__ == "__main__": unittest.main()
