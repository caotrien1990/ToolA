import msvcrt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import restart_orchestrator
from orchestrator.lifecycle import console_warp

class TestRestartOrchestratorProcessSelection(unittest.TestCase):
    def test_mcp_stdio_process_is_not_restart_target(self):
        proc = {
            "ProcessId": 999991,
            "Name": "python.exe",
            "CommandLine": f'"{restart_orchestrator.SERVER}" --mcp',
        }
        self.assertFalse(restart_orchestrator._is_a_process(proc, set()))

    def test_http_server_process_is_restart_target(self):
        proc = {
            "ProcessId": 999992,
            "Name": "pythonw.exe",
            "CommandLine": f'"{restart_orchestrator.SERVER}"',
        }
        self.assertTrue(restart_orchestrator._is_a_process(proc, set()))

    def test_port_owner_python_process_is_restart_target(self):
        proc = {
            "ProcessId": 999993,
            "Name": "python.exe",
            "CommandLine": "python.exe unrelated.py",
        }
        self.assertTrue(restart_orchestrator._is_a_process(proc, {999993}))

    def test_kill_targets_uses_individual_pid_not_tree_kill(self):
        proc = {"ProcessId": 999994, "Name": "pythonw.exe"}
        completed = mock.Mock(returncode=0, stdout="killed", stderr="")
        with mock.patch.object(restart_orchestrator, "_pid_exists", return_value=True), \
             mock.patch.object(restart_orchestrator, "_run", return_value=completed) as run:
            result = restart_orchestrator.kill_targets([proc], dry_run=False)
        command = run.call_args.args[0]
        self.assertEqual(command, ["taskkill.exe", "/F", "/PID", "999994"])
        self.assertNotIn("/T", command)
        self.assertEqual(result[0]["kill_mode"], "pid_only")
        self.assertEqual(result[0]["status"], "killed")


class TestStrictStoppedState(unittest.TestCase):
    def test_all_clear_requires_processes_both_ports_and_byte_lock(self):
        with mock.patch.object(restart_orchestrator, "find_targets", return_value=[]), \
             mock.patch.object(restart_orchestrator, "_port_owner_pids", return_value=set()), \
             mock.patch.object(restart_orchestrator, "_instance_lock_is_free", return_value=True):
            state = restart_orchestrator._stopped_state(8083, 8084)
        self.assertTrue(state["clear"], state)

    def test_each_lingering_runtime_signal_blocks_clear(self):
        cases = (
            ([{"ProcessId": 7}], set(), set(), True),
            ([], {7}, set(), True),
            ([], set(), {8}, True),
            ([], set(), set(), False),
        )
        for processes, http_owners, livez_owners, lock_free in cases:
            with self.subTest(processes=processes, http=http_owners, livez=livez_owners, lock=lock_free), \
                 mock.patch.object(restart_orchestrator, "find_targets", return_value=processes), \
                 mock.patch.object(
                     restart_orchestrator,
                     "_port_owner_pids",
                     side_effect=lambda port, h=http_owners, l=livez_owners: h if port == 8083 else l,
                 ), \
                 mock.patch.object(restart_orchestrator, "_instance_lock_is_free", return_value=lock_free):
                state = restart_orchestrator._stopped_state(8083, 8084)
            self.assertFalse(state["clear"], state)

    def test_wait_gate_requires_two_consecutive_clear_samples(self):
        clear = {
            "clear": True,
            "remaining_pids": [],
            "port_owners": {"8083": [], "8084": []},
            "lock_free": True,
            "errors": [],
        }
        with mock.patch.object(restart_orchestrator, "_stopped_state", side_effect=[clear, clear]) as probe:
            state = restart_orchestrator.wait_for_stopped_state(
                8083, 8084, timeout_s=1, poll_s=0, stable_samples=2,
            )
        self.assertTrue(state["clear"])
        self.assertEqual(state["stable_clear_samples"], 2)
        self.assertEqual(probe.call_count, 2)

    def test_instance_lock_probe_detects_hold_and_releases_its_own_probe(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "orchestrator.lock"
            path.write_bytes(b"x")
            self.assertTrue(restart_orchestrator._instance_lock_is_free(path))
            with path.open("r+b") as held:
                held.seek(0)
                msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    self.assertFalse(restart_orchestrator._instance_lock_is_free(path))
                finally:
                    held.seek(0)
                    msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
            self.assertTrue(restart_orchestrator._instance_lock_is_free(path))


class TestRestartScriptContracts(unittest.TestCase):
    def test_task_end_is_external_only_and_stop_gates_precede_launch(self):
        root = restart_orchestrator.ROOT
        external = (root / "start_orchestrator.bat").read_text(encoding="utf-8").lower()
        high_il = (root / "start_orchestrator_high_il.cmd").read_text(encoding="utf-8").lower()
        helper = (root / "restart_orchestrator.py").read_text(encoding="utf-8").lower()
        task_end = "sch" + "tasks /end"
        executable_end = [
            line for line in external.splitlines()
            if line.strip().startswith(task_end)
        ]
        self.assertEqual(len(executable_end), 0)
        helper_end = [
            line for line in external.splitlines()
            if "schtasks-end" in line and "as_bat_helpers" in line
        ]
        self.assertEqual(len(helper_end), 1)
        high_il_exec_end = [
            line for line in high_il.splitlines()
            if line.strip().startswith(task_end)
        ]
        helper_exec_end = [
            line for line in helper.splitlines()
            if line.strip().startswith(task_end)
        ]
        self.assertEqual(high_il_exec_end, [])
        self.assertEqual(helper_exec_end, [])
        self.assertNotIn("if errorlevel 9", external)
        self.assertLess(
            external.index("restart_orchestrator.py\" --clear-timeout 20"),
            external.index('schtasks-run "%task_orch%"'),
        )
        self.assertLess(
            high_il.index("strict stop gate blocked launch"),
            high_il.index("launch scheduler-owned pythonw"),
        )
        self.assertIn("livez_protocol", high_il)
        self.assertNotIn('start "toola_orchestrator" /wait', high_il)
        self.assertNotIn('start "toola_readinessprobe"', high_il)
        self.assertIn('"%pyw%" "%root%orchestrator_server.py"', high_il)
        self.assertIn("--probe-readiness", high_il)
        self.assertIn("retains child ownership", high_il)
        self.assertIn(":after_ready", external)
        self.assertIn("ignorenew", external)
        self.assertIn("toola_forcerestart", external)
        self.assertIn("request_orchestrator_restart", external)
        install = (root / "install_orchestrator_admin_task.ps1").read_text(
            encoding="utf-8"
        ).lower()
        self.assertIn("stopexisting", install)
        self.assertIn("toola_forcerestart", install)
        self.assertIn("-hidden", install)
        self.assertIn("high_il_launch", install)
        self.assertNotIn("cmd.exe /c", install)
        self.assertNotIn("run-wait-hidden", install)

    def test_start_orchestrator_hides_console(self):
        src = Path(restart_orchestrator.__file__).read_text(encoding="utf-8")
        self.assertIn("CREATE_NO_WINDOW", src)
        self.assertNotIn("CREATE_NEW_CONSOLE", src)
        with mock.patch.object(console_warp, "has_console", return_value=False), \
             mock.patch.object(restart_orchestrator.subprocess, "Popen") as popen:
            rc = restart_orchestrator.start_orchestrator()
        self.assertEqual(rc, 0)
        flags = popen.call_args.kwargs["creationflags"]
        self.assertTrue(flags & 0x08000000)
        self.assertFalse(flags & 0x00000010)

    def test_install_contract_documents_scheduler_owned_server_lifetime(self):
        script = (restart_orchestrator.ROOT / "install_orchestrator_admin_task.ps1").read_text(
            encoding="utf-8"
        ).lower()
        self.assertNotIn("start /wait", script)
        self.assertIn("authoritatively stops it", script)
        self.assertIn("param([switch]$noninteractive)", script)
        self.assertIn("if (-not $noninteractive) { read-host", script)
        self.assertIn("toola_forcerestart", script)
        self.assertIn("high_il_launch --force", script)
        controller = (restart_orchestrator.ROOT / "start_restart_control_high_il.cmd").read_text(
            encoding="utf-8"
        ).lower()
        self.assertNotIn(" /wait ", controller)
        self.assertIn("pythonw", controller)

    def test_run_hides_console_subsystem_children(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(console_warp, "has_console", return_value=False), \
             mock.patch.object(restart_orchestrator.subprocess, "run", return_value=completed) as run:
            restart_orchestrator._run(["taskkill.exe", "/F", "/PID", "1"])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs.get("creationflags"), restart_orchestrator.CREATE_NO_WINDOW)
        self.assertTrue(kwargs.get("startupinfo").dwFlags & restart_orchestrator.STARTF_USESHOWWINDOW)

    def test_run_inherits_when_console_present(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(console_warp, "has_console", return_value=True), \
             mock.patch.object(restart_orchestrator.subprocess, "run", return_value=completed) as run:
            restart_orchestrator._run(["taskkill.exe", "/F", "/PID", "1"])
        flags = int(run.call_args.kwargs.get("creationflags") or 0)
        self.assertFalse(flags & restart_orchestrator.CREATE_NO_WINDOW)

    def test_processes_uses_psutil_not_powershell(self):
        proc = mock.Mock()
        proc.info = {
            "pid": 4242,
            "name": "pythonw.exe",
            "cmdline": [r"C:\venv\pythonw.exe", str(restart_orchestrator.SERVER)],
        }
        fake = mock.Mock()
        fake.process_iter.return_value = [proc]
        fake.Error = Exception
        with mock.patch.dict("sys.modules", {"psutil": fake}), \
             mock.patch.object(restart_orchestrator, "_run") as run:
            items = restart_orchestrator._processes()
        run.assert_not_called()
        self.assertEqual(items[0]["ProcessId"], 4242)
        self.assertEqual(items[0]["Name"], "pythonw.exe")

    def test_port_owner_uses_psutil_not_powershell(self):
        conn = mock.Mock()
        conn.status = "LISTEN"
        conn.laddr = mock.Mock(port=8083)
        conn.pid = 77
        fake = mock.Mock()
        fake.net_connections.return_value = [conn]
        with mock.patch.dict("sys.modules", {"psutil": fake}), \
             mock.patch.object(restart_orchestrator, "_run") as run:
            pids = restart_orchestrator._port_owner_pids(8083)
        run.assert_not_called()
        self.assertEqual(pids, {77})


if __name__ == "__main__":
    unittest.main()
