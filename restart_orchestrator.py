#!/usr/bin/env python3
"""Stop Project Orchestrator [A] and prove the runtime is clear before launch."""

from __future__ import annotations

import argparse
import ctypes
import json
import msvcrt
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SERVER = (ROOT / "orchestrator_server.py").resolve()
START_BAT = ROOT / "start_orchestrator.bat"
INSTANCE_LOCK = ROOT / ".orchestrator_cache" / "orchestrator.lock"
DEFAULT_PORT = 8083
DEFAULT_LIVEZ_PORT = 8084


def _pid_exists(pid: int) -> bool:
    """Return False when a process was already removed with its parent tree."""
    if pid <= 0:
        return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


from orchestrator.lifecycle.console_warp import (
    CREATE_NO_WINDOW,
    STARTF_USESHOWWINDOW,
    child_creationflags,
    child_startupinfo,
)

_PYTHON_NAMES = {"python.exe", "pythonw.exe", "python3.12.exe", "pythonw3.12.exe"}


def _startupinfo():
    return child_startupinfo()


def _run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    kwargs: dict = {
        "cwd": str(ROOT),
        "text": True,
        "capture_output": True,
        "timeout": timeout,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = child_creationflags()
        kwargs["startupinfo"] = child_startupinfo()
    return subprocess.run(cmd, **kwargs)


def _sanitize_ps_json(raw: str) -> str:
    """ConvertTo-Json can emit raw C0 controls from Win32 CommandLine strings."""
    return "".join(ch if (ord(ch) >= 32 or ch in "\t\n\r") else " " for ch in raw)


def _ps_json(script: str) -> list:
    """Last-resort PowerShell JSON helper. Prefer the psutil scanners above this."""
    cp = _run([
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script,
    ])
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout or "PowerShell failed").strip())
    out = (cp.stdout or "").strip()
    if not out:
        return []
    try:
        data = json.loads(out, strict=False)
    except json.JSONDecodeError:
        data = json.loads(_sanitize_ps_json(out), strict=False)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, (str, int, float)):
        return [data]
    return data or []


def _processes() -> list[dict]:
    try:
        import psutil
    except Exception:
        return _processes_ps_fallback()
    items: list[dict] = []
    try:
        iterator = psutil.process_iter(["pid", "name", "cmdline"])
    except Exception:
        return _processes_ps_fallback()
    for proc in iterator:
        try:
            info = proc.info
            name = str(info.get("name") or "")
            if name.lower() not in _PYTHON_NAMES:
                continue
            cmdline = info.get("cmdline") or []
            cmd = " ".join(str(part) for part in cmdline)
            cmd = "".join(ch if (ord(ch) >= 32 or ch in "\t\n\r") else " " for ch in cmd)
            items.append({
                "ProcessId": int(info.get("pid") or proc.pid),
                "Name": name,
                "CommandLine": cmd,
            })
        except (psutil.Error, TypeError, ValueError, OSError):
            continue
    return items


def _processes_ps_fallback() -> list[dict]:
    script = r"""
$items = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'python3.12.exe' OR Name = 'pythonw3.12.exe'" |
  ForEach-Object {
    $cmd = [string]$_.CommandLine
    if ($cmd) { $cmd = [regex]::Replace($cmd, '[\x00-\x1F]', ' ') }
    [pscustomobject]@{ ProcessId = $_.ProcessId; Name = $_.Name; CommandLine = $cmd }
  }
@($items) | ConvertTo-Json -Compress -Depth 3
"""
    return _ps_json(script)


def _port_owner_pids(port: int) -> set[int]:
    try:
        import psutil
    except Exception:
        return _port_owner_pids_ps_fallback(port)
    try:
        pids: set[int] = set()
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != "LISTEN" or not conn.laddr:
                continue
            if int(conn.laddr.port) == int(port) and conn.pid:
                pids.add(int(conn.pid))
        return pids
    except Exception:
        return _port_owner_pids_ps_fallback(port)


def _port_owner_pids_ps_fallback(port: int) -> set[int]:
    script = f"""
$items = Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique
$items | ConvertTo-Json -Compress
"""
    try:
        return {int(x) for x in _ps_json(script) if str(x).strip()}
    except json.JSONDecodeError:
        cp = _run([
            "powershell.exe", "-NoProfile", "-Command",
            f"Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique",
        ])
        return {int(line.strip()) for line in cp.stdout.splitlines() if line.strip().isdigit()}

def _is_a_process(proc: dict, port_pids: set[int]) -> bool:
    pid = int(proc.get("ProcessId") or 0)
    name = str(proc.get("Name") or "").lower()
    cmd = str(proc.get("CommandLine") or "")
    cmd_norm = cmd.lower().replace("/", "\\")
    root_norm = str(ROOT).lower()
    server_norm = str(SERVER).lower()
    if pid == os.getpid():
        return False
    if "--mcp" in cmd_norm:
        return False
    if server_norm in cmd_norm:
        return True
    if "orchestrator_server.py" in cmd_norm and root_norm in cmd_norm:
        return True
    if pid in port_pids and name in {"python.exe", "pythonw.exe", "python3.12.exe", "pythonw3.12.exe"}:
        return True
    if pid in port_pids and root_norm in cmd_norm:
        return True
    return False


def find_targets(port: int) -> list[dict]:
    port_pids = _port_owner_pids(port)
    targets = []
    for proc in _processes():
        try:
            if _is_a_process(proc, port_pids):
                targets.append(proc)
        except Exception:
            continue
    return sorted(targets, key=lambda p: int(p.get("ProcessId") or 0))


def kill_targets(targets: list[dict], dry_run: bool) -> list[dict]:
    """Kill each discovered Python PID without traversing its process tree.

    The detached restart helper is a descendant of the old server. A tree kill
    of that ancestor kills the helper itself before it can launch the High-IL
    task. Every server Python host is already enumerated explicitly, and the
    strict PID/port/lock gate below proves the final stopped state.
    """
    results = []
    for proc in targets:
        pid = int(proc["ProcessId"])
        item = {
            "pid": pid,
            "name": proc.get("Name"),
            "dry_run": dry_run,
            "kill_mode": "pid_only",
        }
        if dry_run:
            item["status"] = "would_kill"
            results.append(item)
            continue
        if not _pid_exists(pid):
            item.update(status="already_gone", returncode=0, stdout="", stderr="removed before individual kill")
            results.append(item)
            continue
        cp = _run(["taskkill.exe", "/F", "/PID", str(pid)], timeout=10)
        taskkill_text = ((cp.stdout or "") + "\n" + (cp.stderr or "")).lower()
        not_found = cp.returncode != 0 and (
            not _pid_exists(pid)
            or "not found" in taskkill_text
            or "no running instance" in taskkill_text
            or "process could not be found" in taskkill_text
        )
        item["status"] = "killed" if cp.returncode == 0 else ("already_gone" if not_found else "kill_failed")
        item["returncode"] = cp.returncode
        item["stdout"] = cp.stdout.strip()[-500:]
        item["stderr"] = cp.stderr.strip()[-500:]
        results.append(item)
    return results


def _instance_lock_is_free(lock_path: Path = INSTANCE_LOCK) -> bool:
    """Probe the real byte lock without trusting or rewriting the holder sidecar."""
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+b")
    except OSError:
        return False
    locked = False
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        locked = True
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        locked = False
        return True
    except OSError:
        return False
    finally:
        if locked:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        fh.close()


def _stopped_state(port: int, livez_port: int) -> dict:
    errors = []
    try:
        remaining = find_targets(port)
    except Exception as exc:
        remaining = []
        errors.append("process_scan:" + type(exc).__name__)
    owners = {}
    for probe_port in (port, livez_port):
        try:
            owners[str(probe_port)] = sorted(_port_owner_pids(probe_port))
        except Exception as exc:
            owners[str(probe_port)] = []
            errors.append(f"port_{probe_port}:" + type(exc).__name__)
    try:
        lock_free = _instance_lock_is_free()
    except Exception as exc:
        lock_free = False
        errors.append("instance_lock:" + type(exc).__name__)
    remaining_pids = sorted(int(p.get("ProcessId") or 0) for p in remaining)
    clear = not errors and not remaining_pids and not any(owners.values()) and lock_free
    return {
        "clear": clear,
        "remaining_pids": remaining_pids,
        "port_owners": owners,
        "lock_free": lock_free,
        "errors": errors,
    }


def wait_for_stopped_state(
    port: int,
    livez_port: int = DEFAULT_LIVEZ_PORT,
    timeout_s: float = 20.0,
    poll_s: float = 0.5,
    stable_samples: int = 2,
) -> dict:
    """Require consecutive strict-clear samples to close process-exit TOCTOU races."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    consecutive = 0
    last = {"clear": False, "remaining_pids": [], "port_owners": {}, "lock_free": False, "errors": ["not_probed"]}
    while True:
        last = dict(_stopped_state(port, livez_port))
        consecutive = consecutive + 1 if last.get("clear") else 0
        last["stable_clear_samples"] = consecutive
        if consecutive >= max(1, stable_samples):
            return last
        if time.monotonic() >= deadline:
            return last
        time.sleep(max(0.0, poll_s))


def wait_until_clear(port: int, timeout_s: float = 20.0) -> bool:
    """Backward-compatible boolean wrapper around the strict stopped-state gate."""
    return bool(wait_for_stopped_state(port, timeout_s=timeout_s).get("clear"))


def start_orchestrator() -> int:
    if not START_BAT.exists():
        print(json.dumps({"ok": False, "error": "START_SCRIPT_MISSING", "path": str(START_BAT)}))
        return 2
    subprocess.Popen(
        ["cmd.exe", "/c", str(START_BAT)],
        cwd=str(ROOT),
        creationflags=child_creationflags(),
        startupinfo=child_startupinfo(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop Project Orchestrator [A] and verify strict clear state.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--livez-port", type=int, default=DEFAULT_LIVEZ_PORT)
    parser.add_argument("--clear-timeout", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--start", action="store_true", help="Start [A] via start_orchestrator.bat after strict clear.")
    args = parser.parse_args()

    log_path = ROOT / ".orchestrator_cache" / "restart_orchestrator_last.json"
    discovery_error = ""
    try:
        targets = find_targets(args.port)
    except Exception as exc:
        targets = []
        discovery_error = type(exc).__name__ + ": " + str(exc)
    kill_results = kill_targets(targets, args.dry_run)
    if args.dry_run:
        stopped = {
            "clear": True, "dry_run": True, "remaining_pids": [],
            "port_owners": {}, "lock_free": True, "errors": [], "stable_clear_samples": 0,
        }
    else:
        stopped = wait_for_stopped_state(
            args.port, args.livez_port, timeout_s=args.clear_timeout,
        )
    if discovery_error:
        stopped.setdefault("errors", []).append("initial_process_scan:" + discovery_error)
        stopped["clear"] = False

    started = False
    start_rc = 0
    if args.start and stopped.get("clear") and not args.dry_run:
        start_rc = start_orchestrator()
        started = start_rc == 0

    hard_fail = (not stopped.get("clear")) or (start_rc != 0)
    payload = {
        "ok": not hard_fail,
        "root": str(ROOT),
        "server": str(SERVER),
        "port": args.port,
        "livez_port": args.livez_port,
        "target_count": len(targets),
        "results": kill_results,
        "clear_after": bool(stopped.get("clear")),
        "remaining_pids": stopped.get("remaining_pids", []),
        "port_owners": stopped.get("port_owners", {}),
        "lock_free": bool(stopped.get("lock_free")),
        "stable_clear_samples": stopped.get("stable_clear_samples", 0),
        "stop_errors": stopped.get("errors", []),
        "started": started,
        "hard_fail": hard_fail,
        "note": "launch is permitted only after PIDs, 8083, 8084, and the instance byte-lock are clear",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
