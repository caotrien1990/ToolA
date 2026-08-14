"""Direct pythonw High-IL launcher — no cmd.exe, no start /b.

Task Scheduler action is:
  pythonw.exe -m orchestrator.lifecycle.high_il_launch
  pythonw.exe -m orchestrator.lifecycle.high_il_launch --force

The launcher process is the scheduled-task job owner. It stop-gates with
CREATE_NO_WINDOW, probes /livez in a daemon thread (no untitled start /b),
then waits on pythonw orchestrator_server.py without DETACHED_PROCESS or
CREATE_BREAKAWAY_FROM_JOB so schtasks /end still owns the server.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from orchestrator.lifecycle.as_bat_helpers import (
    CREATE_NO_WINDOW,
    _startupinfo,
    cmd_probe_livez,
    run_hidden,
)
from orchestrator.lifecycle.console_warp import (
    child_creationflags,
    child_startupinfo,
    ensure_hidden_console,
)


DETACHED_PROCESS = 0x00000008
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

HIGH_IL_ENV = {
    "ORCH_SEM_READ": "48",
    "ORCH_SEM_COMMAND": "24",
    "ORCH_SEM_GIT": "16",
    "ORCH_SEM_SUBPROCESS": "16",
    "ORCH_SEM_TEST": "6",
    "ORCH_SEM_BROWSER": "2",
    "ORCH_SEM_BACKUP": "4",
    "ORCH_GLOBAL_CAP": "96",
    "ORCH_FAIR_ANONYMOUS_QUOTA": "32",
    "ORCH_FAIR_MAX_WAITERS_PER_PRINCIPAL": "256",
    "ORCH_FAIR_READ_IO_QUOTA": "16",
    "ORCH_FAIR_SEARCH_QUOTA": "12",
    "ORCH_FAIR_COMMAND_QUOTA": "8",
    "ORCH_FAIR_GIT_QUOTA": "8",
    "ORCH_FAIR_TEST_QUOTA": "4",
    "ORCH_FAIR_BACKUP_QUOTA": "2",
    "ORCH_FAIR_COUNCIL_PROVIDER_QUOTA": "8",
    "ORCH_FAIR_ANONYMOUS_READ_IO_QUOTA": "32",
    "ORCH_FAIR_ANONYMOUS_SEARCH_QUOTA": "24",
    "ORCH_FAIR_ANONYMOUS_COMMAND_QUOTA": "16",
    "ORCH_FAIR_ANONYMOUS_GIT_QUOTA": "12",
    "ORCH_FAIR_ANONYMOUS_TEST_QUOTA": "6",
    "ORCH_FAIR_ANONYMOUS_BACKUP_QUOTA": "4",
    "ORCH_FAIR_ANONYMOUS_COUNCIL_PROVIDER_QUOTA": "16",
    "ORCH_EXEC_READ_IO_QUEUE_WAIT_SECONDS": "15",
    "ORCH_EXEC_SEARCH_QUEUE_WAIT_SECONDS": "15",
    "ORCH_EXEC_COMMAND_QUEUE_WAIT_SECONDS": "20",
    "ORCH_EXEC_GIT_QUEUE_WAIT_SECONDS": "20",
    "ORCH_EXEC_TEST_QUEUE_WAIT_SECONDS": "30",
    "ORCH_EXEC_BACKUP_QUEUE_WAIT_SECONDS": "30",
    "ORCH_EXEC_COUNCIL_PROVIDER_QUEUE_WAIT_SECONDS": "30",
    "ORCH_EXEC_READ_IO_WORKERS": "48",
    "ORCH_EXEC_READ_IO_QUEUE": "192",
    "ORCH_EXEC_SEARCH_WORKERS": "32",
    "ORCH_EXEC_SEARCH_QUEUE": "128",
    "ORCH_EXEC_COMMAND_WORKERS": "24",
    "ORCH_EXEC_COMMAND_QUEUE": "96",
    "ORCH_EXEC_GIT_WORKERS": "20",
    "ORCH_EXEC_GIT_QUEUE": "80",
    "ORCH_EXEC_TEST_WORKERS": "8",
    "ORCH_EXEC_TEST_QUEUE": "24",
    "ORCH_EXEC_COUNCIL_PROVIDER_WORKERS": "24",
    "ORCH_EXEC_COUNCIL_PROVIDER_QUEUE": "96",
    "ORCH_EXEC_BACKUP_WORKERS": "4",
    "ORCH_EXEC_BACKUP_QUEUE": "12",
    "ORCH_DEFAULT_EXECUTOR_WORKERS": "56",
}


def root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def apply_env(root: Path) -> None:
    for key, value in HIGH_IL_ENV.items():
        os.environ[key] = value
    os.chdir(root)


def py_exes(root: Path) -> tuple[Path, Path]:
    py = root / "venv312" / "Scripts" / "python.exe"
    pyw = root / "venv312" / "Scripts" / "pythonw.exe"
    if not py.is_file():
        py = Path("python.exe")
    if not pyw.is_file():
        pyw = Path("pythonw.exe")
    return py, pyw


def append_log(log: Path, line: str) -> None:
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def wipe_pycache(root: Path) -> None:
    target = root / "__pycache__"
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


def stop_gate(root: Path, pyw: Path, log: Path, clear_timeout: int) -> int:
    script = root / "restart_orchestrator.py"
    argv = [str(pyw), str(script), "--clear-timeout", str(clear_timeout)]
    try:
        result = run_hidden(argv, timeout=float(clear_timeout) + 40.0)
    except subprocess.TimeoutExpired:
        append_log(log, "[FAIL] stop gate timeout")
        return 1
    if result.stdout:
        append_log(log, result.stdout.rstrip("\n"))
    if result.stderr:
        append_log(log, result.stderr.rstrip("\n"))
    return int(result.returncode)


def start_probe(log: Path) -> None:
    args = argparse.Namespace(log=str(log), timeout_s=120)
    thread = threading.Thread(
        target=cmd_probe_livez,
        args=(args,),
        name="probe-livez",
        daemon=True,
    )
    thread.start()


def launch_server(root: Path, pyw: Path) -> int:
    kwargs: dict = {
        "args": [str(pyw), str(root / "orchestrator_server.py")],
        "cwd": str(root),
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = child_creationflags(0)
        kwargs["startupinfo"] = child_startupinfo()
    return int(subprocess.run(**kwargs).returncode)


def run_orchestrator() -> int:
    ensure_hidden_console()
    root = root_dir()
    apply_env(root)
    log = root / ".orchestrator_cache" / "high_il_start.log"
    append_log(log, f"===== {datetime.now().isoformat(timespec='seconds')} high_il_launch start =====")
    wipe_pycache(root)
    _py, pyw = py_exes(root)
    append_log(log, f"[2] restart_orchestrator.py strict stop via {pyw}")
    kill_rc = stop_gate(root, pyw, log, 20)
    append_log(log, f"[2] stop_gate_rc={kill_rc}")
    if kill_rc != 0:
        append_log(log, f"[FAIL] strict stop gate blocked launch rc={kill_rc}")
        return kill_rc
    append_log(log, "[3] launch scheduler-owned pythonw orchestrator_server.py")
    start_probe(log)
    server_rc = launch_server(root, pyw)
    append_log(log, f"[5] scheduler-owned server exited rc={server_rc}")
    return server_rc


def run_force() -> int:
    ensure_hidden_console()
    root = root_dir()
    os.chdir(root)
    log = root / ".orchestrator_cache" / "force_restart.log"
    append_log(log, f"===== {datetime.now().isoformat(timespec='seconds')} force_restart =====")
    _py, pyw = py_exes(root)
    append_log(log, "[1] High-IL restart_orchestrator.py --clear-timeout 25")
    kill_rc = stop_gate(root, pyw, log, 25)
    append_log(log, f"[1] stop_gate_rc={kill_rc}")
    if kill_rc != 0:
        append_log(log, f"[FAIL] stop gate blocked ForceRestart rc={kill_rc}")
        return kill_rc
    append_log(log, "[2] schtasks /run ToolA_Orchestrator")
    try:
        result = run_hidden(["schtasks", "/run", "/tn", "ToolA_Orchestrator"], timeout=20)
    except subprocess.TimeoutExpired:
        append_log(log, "[FAIL] schtasks /run timeout")
        return 1
    append_log(log, f"[2] run_rc={result.returncode}")
    return int(result.returncode)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--force" in args:
        return run_force()
    return run_orchestrator()


if __name__ == "__main__":
    raise SystemExit(main())
