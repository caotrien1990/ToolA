"""Hidden helpers for [A:S] start_orchestrator.bat — no console windows.

The bat invokes this module via pythonw.exe so console-subsystem children
are never launched as visible processes. Children inherit a hidden console
when one exists; otherwise CREATE_NO_WINDOW is the fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from orchestrator.lifecycle.console_warp import (
    CREATE_NO_WINDOW,
    child_creationflags,
    child_startupinfo,
)


def _startupinfo():
    return child_startupinfo()


def run_hidden(
    argv: list[str],
    *,
    timeout: float | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict = {
        "args": argv,
        "timeout": timeout,
        "check": False,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if os.name == "nt":
        kwargs["creationflags"] = child_creationflags()
        kwargs["startupinfo"] = child_startupinfo()
    return subprocess.run(**kwargs)


_TASK_STATE_MAP = {
    0: "Unknown",
    1: "Disabled",
    2: "Queued",
    3: "Ready",
    4: "Running",
}

_STATUS_ALIASES = {
    "ready": "Ready",
    "running": "Running",
    "disabled": "Disabled",
    "queued": "Queued",
    "sẵn sàng": "Ready",
    "đang chạy": "Running",
    "đã tắt": "Disabled",
    "vô hiệu hóa": "Disabled",
}


def get_listen_pid(port: int) -> int | None:
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if conn.status != "LISTEN" or not conn.laddr:
                continue
            if int(conn.laddr.port) == int(port) and conn.pid:
                return int(conn.pid)
    except Exception:
        pass
    return _listen_pid_netstat(port)


def _listen_pid_netstat(port: int) -> int | None:
    result = run_hidden(["netstat", "-ano", "-p", "TCP"], timeout=15)
    suffix = f":{int(port)}"
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local = parts[1]
        if not (local.endswith(suffix) or local.endswith("]" + suffix)):
            continue
        if parts[3].upper() != "LISTENING":
            continue
        if parts[4].isdigit():
            return int(parts[4])
    return None


def _write_out(path: str | None, text: str) -> None:
    if not path:
        return
    target = Path(path)
    if text:
        target.write_text(text, encoding="utf-8")
        return
    if target.exists():
        target.unlink()


def cmd_get_listen_pid(args: argparse.Namespace) -> int:
    pid = get_listen_pid(args.port)
    _write_out(args.out, str(pid) if pid else "")
    return 0


def _task_state(name: str) -> str:
    """Locale-independent TaskState names match Get-ScheduledTask .State.

    cmd_stop_task previously called Stop-ScheduledTask; now schtasks /end.
    """
    try:
        import win32com.client

        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        task = service.GetFolder("\\").GetTask(name)
        return _TASK_STATE_MAP.get(int(task.State), "Unknown")
    except Exception:
        pass
    result = run_hidden(["schtasks", "/query", "/tn", name, "/fo", "LIST"], timeout=15)
    for line in (result.stdout or "").splitlines():
        raw = line.strip()
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key_l = key.strip().lower()
        if key_l in {"status", "trạng thái", "trang thai"}:
            token = value.strip()
            return _STATUS_ALIASES.get(token.lower(), token)
    return ""


def cmd_wait_task_state(args: argparse.Namespace) -> int:
    wanted = str(args.state)
    deadline = time.monotonic() + float(args.timeout_s)
    while time.monotonic() < deadline:
        if _task_state(args.name) == wanted:
            return 0
        time.sleep(0.5)
    return 1


def cmd_stop_task(args: argparse.Namespace) -> int:
    result = run_hidden(["schtasks", "/end", "/tn", args.name], timeout=20)
    return 0 if result.returncode == 0 else 1


def cmd_schtasks(args: argparse.Namespace) -> int:
    result = run_hidden(
        ["schtasks", args.action, "/tn", args.name],
        timeout=20,
    )
    return 0 if result.returncode == 0 else 1


def _livez_ok(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return None
    if (
        payload.get("ok")
        and payload.get("service") == "project-orchestrator"
        and payload.get("livez_protocol") == 1
    ):
        return payload
    return None


def cmd_wait_livez(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + float(args.timeout_s)
    while time.monotonic() < deadline:
        if _livez_ok(args.url):
            return 0
        time.sleep(1)
    return 1


def cmd_wait_health_gen(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + float(args.timeout_s)
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(args.url, timeout=2) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if (
                payload.get("ok")
                and payload.get("service") == "project-orchestrator"
                and payload.get("generation")
            ):
                print("[OK] Health gen=" + str(payload.get("generation")), flush=True)
                return 0
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(1)
    return 1


def cmd_wait_new_owner(args: argparse.Namespace) -> int:
    before = str(args.before or "")
    deadline = time.monotonic() + float(args.timeout_s)
    while time.monotonic() < deadline:
        payload = _livez_ok(args.livez, timeout=3)
        pid_now = get_listen_pid(args.port)
        if payload and pid_now and (not before or str(pid_now) != before):
            print(f"[OK] instance moi pid={pid_now}", flush=True)
            return 0
        time.sleep(2)
    return 1


def cmd_run_hidden(args: argparse.Namespace) -> int:
    argv = list(args.argv)
    if not argv:
        return 2
    result = run_hidden(argv, timeout=args.timeout_s or None)
    if args.log:
        try:
            with open(args.log, "a", encoding="utf-8") as handle:
                if result.stdout:
                    handle.write(result.stdout)
                    if not result.stdout.endswith("\n"):
                        handle.write("\n")
                if result.stderr:
                    handle.write(result.stderr)
                    if not result.stderr.endswith("\n"):
                        handle.write("\n")
        except OSError:
            pass
    return int(result.returncode)


def cmd_run_wait_hidden(args: argparse.Namespace) -> int:
    argv = list(args.argv)
    if not argv:
        return 2
    kwargs: dict = {"args": argv, "check": False}
    if os.name == "nt":
        kwargs["creationflags"] = child_creationflags()
        kwargs["startupinfo"] = child_startupinfo()
    return int(subprocess.run(**kwargs).returncode)


def cmd_run_ps1(args: argparse.Namespace) -> int:
    argv = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        args.path,
    ]
    result = run_hidden(argv, timeout=args.timeout_s or None)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return int(result.returncode)


def cmd_probe_livez(args: argparse.Namespace) -> int:
    log = Path(args.log) if args.log else None

    def _log(line: str) -> None:
        if log is None:
            return
        try:
            with log.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    deadline = time.monotonic() + float(args.timeout_s)
    iteration = 0
    while time.monotonic() < deadline:
        iteration += 1
        if _livez_ok("http://127.0.0.1:8084/livez"):
            _log(f"[4] livez ok at iter {iteration}")
            _log("[OK] high_il readiness complete; scheduled task retains child ownership")
            return 0
        listening = get_listen_pid(8083) is not None
        if listening:
            _log(f"[4] iter {iteration}: port 8083 listening; service still starting")
        time.sleep(1)
    _log("[4] livez timeout after extended 120-probe gate")
    _log("[FAIL] high_il readiness incomplete; scheduled task still owns child lifetime")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="as_bat_helpers")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("get-listen-pid")
    p.add_argument("port", type=int)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_get_listen_pid)

    p = sub.add_parser("wait-task-state")
    p.add_argument("name")
    p.add_argument("state")
    p.add_argument("--timeout-s", type=float, default=10)
    p.set_defaults(func=cmd_wait_task_state)

    p = sub.add_parser("stop-task")
    p.add_argument("name")
    p.set_defaults(func=cmd_stop_task)

    p = sub.add_parser("schtasks-query")
    p.set_defaults(
        func=cmd_schtasks,
        action="/query",
    )
    p.add_argument("name")

    p = sub.add_parser("schtasks-run")
    p.set_defaults(func=cmd_schtasks, action="/run")
    p.add_argument("name")

    p = sub.add_parser("schtasks-end")
    p.set_defaults(func=cmd_schtasks, action="/end")
    p.add_argument("name")

    p = sub.add_parser("wait-livez")
    p.add_argument("--url", default="http://127.0.0.1:8084/livez")
    p.add_argument("--timeout-s", type=float, default=60)
    p.set_defaults(func=cmd_wait_livez)

    p = sub.add_parser("wait-health-gen")
    p.add_argument("--url", default="http://localhost:8083/health")
    p.add_argument("--timeout-s", type=float, default=10)
    p.set_defaults(func=cmd_wait_health_gen)

    p = sub.add_parser("wait-new-owner")
    p.add_argument("--port", type=int, default=8083)
    p.add_argument("--before", default="")
    p.add_argument("--livez", default="http://127.0.0.1:8084/livez")
    p.add_argument("--timeout-s", type=float, default=90)
    p.set_defaults(func=cmd_wait_new_owner)

    p = sub.add_parser("run-hidden")
    p.add_argument("--log", default="")
    p.add_argument("--timeout-s", type=float, default=0)
    p.add_argument("argv", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run_hidden)

    p = sub.add_parser("run-wait-hidden")
    p.add_argument("argv", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run_wait_hidden)

    p = sub.add_parser("run-ps1")
    p.add_argument("path")
    p.add_argument("--timeout-s", type=float, default=0)
    p.set_defaults(func=cmd_run_ps1)

    p = sub.add_parser("probe-livez")
    p.add_argument("--log", default="")
    p.add_argument("--timeout-s", type=float, default=120)
    p.set_defaults(func=cmd_probe_livez)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    argv_list = list(getattr(args, "argv", []) or [])
    if argv_list and argv_list[0] == "--":
        args.argv = argv_list[1:]
    timeout_s = getattr(args, "timeout_s", 0)
    if timeout_s == 0:
        args.timeout_s = None
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
