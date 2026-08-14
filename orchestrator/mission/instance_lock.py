"""Single-instance lock for the orchestrator process (Windows).

B1 fix of plan A_CSLT v3.1.

Used by:
  - orchestrator_server startup  (acquire at boot, refuse to start if locked)
  - mission reaper                (chunk sau — cùng module, không acquire lại)

Strategy: Windows byte-range lock via ``msvcrt.locking(fd, LK_NBLCK, 1)`` on
the first byte of *lock_path*.  Non-blocking only — never ``LK_LOCK`` so a
dead/stale peer cannot wedge the new instance.  On success the lock file is
truncated and a small JSON record (pid / port / start_ts) is written; the
OS file handle is kept open at module scope for the lifetime of the
process so the lock is released automatically when the process dies.

Windows caveat: an exclusive byte-range lock (``LK_NBLCK``) blocks READ
operations from *any* other file handle — even within the same process.
Therefore the JSON holder record is also mirrored to a sidecar file
``<lock_path>.holder`` (not locked) so that other processes and tests can
read pid / port without opening the locked file.
"""

from __future__ import annotations

import json
import msvcrt
import os
import time
from pathlib import Path
from typing import Optional

import psutil

_LOCK_HANDLE: Optional[object] = None
_LOCK_PATH: Optional[str] = None


def _holder_path(lock_path: Path) -> Path:
    """Sidecar JSON file path (not locked, readable by any process)."""
    return lock_path.with_name(lock_path.name + ".holder")


def _read_holder(holder_path: str | Path) -> dict:
    """Read the JSON holder record from the sidecar file (best-effort)."""
    try:
        with open(holder_path, "r", encoding="utf-8") as fh:
            return json.loads(fh.read())
    except (OSError, ValueError):
        return {}


def acquire_instance_lock(
    lock_path: str | Path, port: int, wait_for_stale_sec: float = 0.0,
) -> dict:
    """Try to acquire the single-instance lock at *lock_path*.

    ``wait_for_stale_sec``: restart-race window. During a kill+relaunch
    restart the old holder may still be exiting when the new instance
    starts; the OS byte-lock releases only at full process death. When
    > 0, acquire is retried every 0.5s up to this budget for as long as
    the recorded holder pid is NOT verifiably alive-and-stable — a
    genuinely running second instance is still refused on first check.

    Returns
    -------
    dict
        ``{"ok": True, "holder": True}`` on success (this process now owns
        the lock; the OS handle is kept open at module scope).

        ``{"ok": False, "error_name": "ORCHESTRATOR_ALREADY_RUNNING",
        "holder_pid": <int>, "holder_port": <int>}`` when another live
        process owns the lock.
    """
    global _LOCK_HANDLE, _LOCK_PATH

    lock_path = Path(lock_path)
    holder_path = _holder_path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    def _try_once() -> bool:
        global _LOCK_HANDLE, _LOCK_PATH
        fh = open(lock_path, "a+b")
        fh.seek(0)  # position at byte 0 — msvcrt.locking locks from
                    # current position; "a+b" starts at EOF.
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            return False
        # Success — truncate + write JSON to lock file (per spec) AND
        # mirror to the sidecar holder file (readable while locked).
        record = {"pid": os.getpid(), "port": port, "start_ts": time.time()}
        data = json.dumps(record).encode("utf-8")
        fh.seek(0)
        fh.truncate()
        fh.write(data)
        fh.flush()
        holder_path.write_text(json.dumps(record), encoding="utf-8")
        _LOCK_HANDLE = fh
        _LOCK_PATH = str(lock_path)
        return True

    if _try_once():
        return {"ok": True, "holder": True}

    # Locked by someone — read the sidecar holder record.
    holder = _read_holder(holder_path)
    pid = holder.get("pid")

    def _refused() -> dict:
        h = _read_holder(holder_path)
        return {
            "ok": False,
            "error_name": "ORCHESTRATOR_ALREADY_RUNNING",
            "holder_pid": h.get("pid"),
            "holder_port": h.get("port"),
        }

    if pid is not None and psutil.pid_exists(pid):
        if wait_for_stale_sec <= 0:
            return _refused()
        # Restart race: the holder may be mid-kill (taskkill already issued
        # by the relaunch pipeline). Retry while the budget lasts; if the
        # holder is still alive at the end, it is a real second instance.
        deadline = time.time() + wait_for_stale_sec
        while time.time() < deadline:
            time.sleep(0.5)
            if _try_once():
                return {"ok": True, "holder": True}
        return _refused()

    # Holder pid is dead or JSON corrupt (stale after crash). Windows
    # byte-locks auto-release when the owning process dies, so retry once.
    if _try_once():
        return {"ok": True, "holder": True}

    # Retry failed — a *different* live process grabbed it in between.
    return _refused()


def release_instance_lock() -> None:
    """Release the lock and close the handle (used by tests / clean exit)."""
    global _LOCK_HANDLE, _LOCK_PATH
    if _LOCK_HANDLE is not None:
        try:
            msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            _LOCK_HANDLE.close()
        except OSError:
            pass
        _LOCK_HANDLE = None
        _LOCK_PATH = None


def is_lock_holder() -> bool:
    """True if this process currently owns the lock."""
    return _LOCK_HANDLE is not None
