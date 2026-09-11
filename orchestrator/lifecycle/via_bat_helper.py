"""Hidden python helper: delay, then invoke official [A:S] without a titled cmd wrapper."""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

from orchestrator.lifecycle.console_warp import (
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
    run_as_bat(bat, root, log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
