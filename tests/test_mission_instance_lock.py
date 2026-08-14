import json
import os
import subprocess
import sys

# Project root must be importable — same pattern as other tests in this
# package that do `from orchestrator...` directly.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from orchestrator.mission.instance_lock import (
    acquire_instance_lock,
    is_lock_holder,
    release_instance_lock,
)

_SUBPROC_SNIPPET = (
    "import json,sys;sys.path.insert(0,{root!r});"
    "from orchestrator.mission.instance_lock import acquire_instance_lock;"
    "r=acquire_instance_lock({lock!r},{port});"
    "print(json.dumps(r))"
)


def _holder_path(lock_path):
    """Sidecar holder file path (mirror of module internal)."""
    from pathlib import Path

    p = Path(lock_path)
    return p.with_name(p.name + ".holder")


def test_acquire_ok(tmp_path):
    lock = tmp_path / "inst.lock"
    res = acquire_instance_lock(lock, 8083)
    assert res == {"ok": True, "holder": True}
    assert is_lock_holder() is True
    # Read holder record from sidecar file (lock file itself is not
    # readable while the exclusive byte-lock is held on Windows).
    data = json.loads(_holder_path(lock).read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert data["port"] == 8083
    release_instance_lock()


def test_second_process_blocked(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lock = tmp_path / "inst.lock"
    # Acquire in this process first.
    res = acquire_instance_lock(lock, 8083)
    assert res["ok"] is True
    # Try from a *different* process.
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            _SUBPROC_SNIPPET.format(
                root=root, lock=str(lock), port=9090
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    sub = json.loads(out.stdout.strip())
    assert sub["ok"] is False
    assert sub["error_name"] == "ORCHESTRATOR_ALREADY_RUNNING"
    assert sub["holder_pid"] == os.getpid()
    assert sub["holder_port"] == 8083
    release_instance_lock()


def test_stale_pid_overwritten(tmp_path):
    lock = tmp_path / "inst.lock"
    holder = _holder_path(lock)
    # Write a stale holder record with a dead pid and NO actual lock held.
    holder.write_text(
        json.dumps({"pid": 999999, "port": 1234, "start_ts": 0}),
        encoding="utf-8",
    )
    res = acquire_instance_lock(lock, 8083)
    assert res == {"ok": True, "holder": True}
    data = json.loads(holder.read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert data["port"] == 8083
    release_instance_lock()


def test_release_then_acquire_ok(tmp_path):
    lock = tmp_path / "inst.lock"
    assert acquire_instance_lock(lock, 8083)["ok"] is True
    assert is_lock_holder() is True
    release_instance_lock()
    assert is_lock_holder() is False
    # Same process can re-acquire after release.
    assert acquire_instance_lock(lock, 8083)["ok"] is True
    assert is_lock_holder() is True
    release_instance_lock()
