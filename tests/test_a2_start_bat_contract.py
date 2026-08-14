"""A2 contract test: start_orchestrator.bat is the single restart entry (A+B+C).

Static invariants. Live behavior is verified separately by the restart real-test.
Do not weaken these assertions without an explicit architecture change.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "start_orchestrator.bat"
HELPER = ROOT / "orchestrator" / "lifecycle" / "as_bat_helpers.py"


def _lower_lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.lower().splitlines()]


def _first_index(lines: list[str], needle: str) -> int:
    needle = needle.lower()
    for i, ln in enumerate(lines):
        if needle in ln:
            return i
    return -1


class TestA2BatContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = BAT.read_text(encoding="utf-8").lower()
        cls.lines = _lower_lines(cls.text)
        cls.helper = HELPER.read_text(encoding="utf-8").lower()

    # ---------- F4: TOOLA_NO_PAUSE + no browser ----------
    def test_f4_toolano_pause_set_before_any_launch(self):
        set_line = _first_index(self.lines, 'set "toola_no_pause=1"')
        self.assertGreaterEqual(set_line, 0, "TOOLA_NO_PAUSE=1 must be set")
        inline_idx = _first_index(self.lines, ":inline")
        self.assertGreaterEqual(inline_idx, 0)
        self.assertLess(set_line, inline_idx, "TOOLA_NO_PAUSE must be set before INLINE label")

    def test_f4_no_browser_launch_on_success(self):
        self.assertNotIn("start-process 'http://localhost:8083/council'", self.text)
        self.assertNotIn("start-process \"http://localhost:8083/council\"", self.text)
        self.assertNotRegex(self.text, r"start-process\s+['\"]http")

    # ---------- F5: livez is the authoritative readiness ----------
    def test_f5_livez_polled_before_health_generation(self):
        livez_idx = _first_index(self.lines, "127.0.0.1:8084/livez")
        health_gen_idx = _first_index(self.lines, "http://localhost:8083/health")
        self.assertGreaterEqual(livez_idx, 0, "livez endpoint missing")
        self.assertGreaterEqual(health_gen_idx, 0, "health endpoint missing")
        self.assertLess(livez_idx, health_gen_idx, "livez must be polled before /health")

    def test_f5_livez_contract_triplet(self):
        self.assertIn("127.0.0.1:8084/livez", self.text)
        self.assertIn("livez_protocol", self.helper)
        self.assertIn("project-orchestrator", self.helper)
        self.assertIn('payload.get("ok")', self.helper)

    # ---------- A+B+C architecture: no RestartControl quiesce ----------
    def test_no_restart_control_quiesce_or_rearm(self):
        self.assertNotIn("disable-scheduledtask", self.text)
        self.assertNotIn("enable-scheduledtask", self.text)
        self.assertNotIn(":rearm_supervisor", self.text)
        self.assertNotIn("call :rearm_supervisor", self.text)

    def test_path_a_cli_8085_before_force_restart(self):
        cli_idx = _first_index(self.lines, "request_orchestrator_restart")
        force_run_idx = _first_index(self.lines, 'schtasks-run "%task_force%"')
        self.assertGreaterEqual(cli_idx, 0, "Path A CLI missing")
        self.assertGreaterEqual(force_run_idx, 0, "Path B ToolA_ForceRestart /run missing")
        self.assertLess(cli_idx, force_run_idx)
        self.assertIn("127.0.0.1:8085", self.text)

    def test_path_a_guard_blocked_is_exit_13(self):
        self.assertIn(":fail_guard", self.text)
        self.assertIn("exit /b 13", self.text)
        self.assertIn("restart_guard_blocked", self.text)

    def test_path_b_force_restart_falls_through_to_path_c(self):
        path_b = _first_index(self.lines, ":path_b")
        path_c = _first_index(self.lines, ":path_c")
        wait_idx = _first_index(self.lines, ":wait_new_pid")
        self.assertGreaterEqual(path_b, 0)
        self.assertGreaterEqual(path_c, 0)
        self.assertGreaterEqual(wait_idx, 0)
        self.assertLess(path_b, path_c)
        self.assertIn('schtasks-run "%task_force%"', self.text)

    def test_f2_hung_task_ready_timeout_does_not_fail_task(self):
        ready_idx = _first_index(self.lines, "tro ve ready")
        self.assertGreaterEqual(ready_idx, 0)
        after_ready_idx = _first_index(self.lines, ":after_ready")
        self.assertGreaterEqual(after_ready_idx, 0, ":AFTER_READY label missing")
        block = "\n".join(self.lines[ready_idx:after_ready_idx + 1])
        self.assertIn("khong fail-closed", block)
        self.assertIn("goto after_ready", block)
        self.assertNotIn("goto fail_task", block)
        self.assertIn("ignorenew", self.text)
        self.assertIn("stop-task", self.text)
        self.assertIn("stop-scheduledtask", self.helper)

    def test_f2_locale_independent_taskstate_polling(self):
        self.assertIn("wait-task-state", self.text)
        self.assertIn("get-scheduledtask", self.helper)
        self.assertIn(".state", self.helper)
        self.assertNotIn("/fo csv", self.text)
        self.assertNotIn("/fo list", self.text)

    def test_f2_run_retries_bounded(self):
        self.assertIn(":run_retry", self.text)
        self.assertRegex(self.text, r"if\s+!?try!?\s+gtr\s+3\s+goto\s+run_give_up")

    def test_f2_medium_il_http_fallback_on_run_give_up(self):
        give_idx = _first_index(self.lines, ":run_give_up")
        done_idx = _first_index(self.lines, ":run_done")
        self.assertGreaterEqual(give_idx, 0)
        self.assertGreaterEqual(done_idx, 0)
        block = "\n".join(self.lines[give_idx:done_idx])
        self.assertIn("toola_from_http_helper", block)
        self.assertIn("restart_orchestrator_via_bat", block)
        self.assertIn("as_medium_il_ignorenew", block)
        self.assertIn("goto fail_task", block)
        self.assertIn("goto run_done", block)

    def test_f3_no_goto_inline_after_task_branch(self):
        task_installed_marker = "phat hien task '%task_orch%'"
        task_installed_idx = _first_index(self.lines, task_installed_marker)
        inline_label_idx = _first_index(self.lines, ":inline")
        self.assertGreaterEqual(task_installed_idx, 0)
        block = "\n".join(self.lines[task_installed_idx:inline_label_idx])
        for ln in block.splitlines():
            stripped = ln.strip()
            self.assertFalse(re.fullmatch(r"goto\s+inline\b", stripped), f"forbidden 'goto INLINE' in task branch: {ln!r}")

    def test_f3_fail_task_label_exists_without_rearm(self):
        fail_idx = _first_index(self.lines, ":fail_task")
        self.assertGreaterEqual(fail_idx, 0)
        inline_idx = _first_index(self.lines, ":inline")
        fail_block = "\n".join(self.lines[fail_idx:inline_idx])
        self.assertNotIn("call :rearm_supervisor", fail_block)
        self.assertIn("exit /b 11", fail_block)

    def test_keep_strict_stop_gate_precedes_orchestrator_run(self):
        stop_gate_idx = _first_index(self.lines, "--clear-timeout 20")
        orch_run_idx = _first_index(self.lines, 'schtasks-run "%task_orch%"')
        self.assertGreaterEqual(stop_gate_idx, 0)
        self.assertGreaterEqual(orch_run_idx, 0)
        self.assertLess(stop_gate_idx, orch_run_idx, "stop gate must precede ToolA_Orchestrator /run")

    def test_keep_external_schtasks_end_count_one(self):
        executable_end = [
            ln for ln in self.lines
            if "schtasks-end" in ln and "as_bat_helpers" in ln
        ]
        self.assertEqual(len(executable_end), 1)
        self.assertFalse(any(ln.strip().startswith("schtasks /end") for ln in self.lines))

    def test_keep_inline_invokes_stop_gate_before_ps1(self):
        inline_idx = _first_index(self.lines, ":inline")
        inline_block = "\n".join(self.lines[inline_idx:])
        self.assertIn("restart_orchestrator.py", inline_block)
        self.assertIn("--clear-timeout 20", inline_block)
        self.assertIn("run-ps1", inline_block)

    def test_bat_does_not_spawn_console_children(self):
        self.assertNotIn("powershell.exe", self.text)
        self.assertNotIn("python.exe -m", self.text)
        self.assertIn("pythonw.exe", self.text)
        self.assertIn("as_bat_helpers", self.text)
        self.assertIn("create_no_window", self.helper)

    def test_keep_no_if_errorlevel_9_artifact(self):
        self.assertNotIn("if errorlevel 9", self.text)
        self.assertNotIn("exit /b 9", self.text)


if __name__ == "__main__":
    unittest.main()
