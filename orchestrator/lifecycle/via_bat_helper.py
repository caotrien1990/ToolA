"""Hidden python helper: delay, then invoke official [A:S] without a titled cmd wrapper."""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

from orchestrator.lifecycle.console_warp import (
    CREATE_NO_WINDOW,
    child_creationflags,
    child_startupinfo,
    ensure_hidden_console,
)


def run_as_bat(bat: Path, root: Path, log: Path) -> int:
    kwargs: dict = {
        "args": ["cmd.exe", "/c", str(bat)],
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = child_creationflags(0)
        kwargs["startupinfo"] = child_startupinfo()
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write("run [A:S] start_orchestrator.bat\n")
            kwargs["stdout"] = handle
            kwargs["stderr"] = subprocess.STDOUT
            result = subprocess.run(**kwargs)
            handle.write(f"helper done rc={result.returncode}\n")
            return int(result.returncode)
    except OSError:
        kwargs.pop("stdout", None)
        kwargs.pop("stderr", None)
        return int(subprocess.run(**kwargs).returncode)


def _read_new_pid() -> "int | None":
    """Read the new :8083 owner PID from the PIDFILE the bat maintains."""
    pidfile = Path(os.environ.get("TEMP", "")) / "toola_as_listen_pid.txt"
    try:
        text = pidfile.read_text(encoding="utf-8").strip()
        pid = int(text.split()[0])
        return pid if pid > 0 else None
    except (OSError, ValueError, IndexError):
        return None


def _write_restart_status(root: Path, bat_rc: int) -> None:
    """Write .orchestrator_cache/restart_status.json (OVERWRITTEN each restart).

    Sprint 4 (Point 5): makes restart failures visible instead of silent.
    The bat already verifies the new instance (WAIT_NEW_PID, PROVE_LIVE,
    PID_AFTER != PID_BEFORE); rc==0 means it proved a new PID owns :8083.
    Callers poll this file (via read_file_range) instead of waiting blind.
    """
    import datetime
    import json
    status_path = root / ".orchestrator_cache" / "restart_status.json"
    payload: dict = {
        "bat_rc": bat_rc,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if bat_rc == 0:
        payload["status"] = "ok"
        new_pid = _read_new_pid()
        if new_pid is not None:
            payload["pid"] = new_pid
    else:
        payload["status"] = "failed"
        payload["reason"] = (
            f"start_orchestrator.bat exited rc={bat_rc} "
            "(see .orchestrator_cache/restart_bat_helper.log)"
        )
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(status_path)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="via_bat_helper")
    parser.add_argument("--delay", type=int, default=2)
    parser.add_argument("--bat", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--root", default="")
    args = parser.parse_args(argv)
    delay = max(1, min(int(args.delay), 10))
    bat = Path(args.bat)
    log = Path(args.log)
    root = Path(args.root) if args.root else bat.parent
    os.environ["TOOLA_NO_PAUSE"] = "1"
    os.environ["TOOLA_FROM_HTTP_HELPER"] = "1"
    os.environ["TOOLA_AS_LOG"] = str(log)
    ensure_hidden_console()
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"helper start delay={delay}s\n")
    except OSError:
        pass
    time.sleep(delay)
    rc = run_as_bat(bat, root, log)
    _write_restart_status(root, rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
