"""A41 faithful probe (NOT a pytest test).

Unlike tests/_a39_probe.py (toy lock + holder threads that wait 20-25s before
ever contending, and writes to a temp dir), this probe:
  * drives the REAL busy-snapshot callback (_restart_busy_snapshot_state), which
    acquires the REAL shared locks (_LANES_LOCK, accounting._condition);
  * measures whether that callback is actually blocked while a request-handler
    holds _LANES_LOCK for seconds (the PH-2 hypothesis);
  * measures publish-cycle delay in THREE configs so each root cause's OWN
    contribution is visible, on the REAL repo drive (D, slow) by default.

Configs:
  (a) baseline      : real publish_once (callback + fsync + os.replace)
  (b) no_fsync      : drop stream.flush()+os.fsync (keep os.replace atomicity)
  (c) no_fsync_lock : (b) + callback reads counters under lock then releases
                      before serialize (already the shape; measured to confirm)

Run:
  ./venv312/Scripts/python.exe tests/_a41_probe.py contention <hold_sec>
  ./venv312/Scripts/python.exe tests/_a41_probe.py cycles <config> <seconds> <dir>
    config = a | b | c ; dir = D (repo drive) | C (fast drive) | <path>
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import orchestrator.server.executor_lanes as el  # noqa: E402
import orchestrator_server as srv  # noqa: E402
from orchestrator.lifecycle.restart_protocol import BusySnapshotPublisher  # noqa: E402


# ----- config (a/b/c) publishers, each a faithful copy of publish_once -----
def _write_payload(path: Path, payload: dict, *, do_fsync: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}"
    )
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            if do_fsync:
                os.fsync(stream.fileno())
        os.replace(temporary, path)  # atomicity preserved in ALL configs
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class Probe(BusySnapshotPublisher):
    def __init__(self, *a, do_fsync=True, **k):
        super().__init__(*a, **k)
        self.do_fsync = do_fsync
        self.rows: list[dict] = []
        self._last = None

    def publish_once(self) -> None:
        with self._publish_lock:
            t0 = time.perf_counter()
            gap = None if self._last is None else (t0 - self._last)
            self._last = t0
            payload = dict(self.snapshot())          # REAL callback, real locks
            t1 = time.perf_counter()
            payload["captured_at"] = time.time()
            _write_payload(self.path, payload, do_fsync=self.do_fsync)
            t2 = time.perf_counter()
            self.rows.append({"gap": gap, "snapshot": t1 - t0, "io": t2 - t1})


def _stats(rows, key):
    vals = [r[key] for r in rows if r[key] is not None]
    if not vals:
        return f"{key:9s} n/a"
    s = sorted(vals)
    return (f"{key:9s} n={len(vals):4d} p50={s[len(s)//2]*1000:8.2f}ms "
            f"p99={s[min(len(s)-1,int(len(s)*0.99))]*1000:8.2f}ms "
            f"max={max(vals)*1000:9.2f}ms")


def _resolve_dir(arg: str) -> Path:
    if arg == "D":
        return ROOT / ".a41_probe_tmp"          # repo is on D (slow drive)
    if arg == "C":
        return Path(tempfile.gettempdir()) / "a41_probe"   # basetemp constraint dir is C
    return Path(arg)


# ----- 1) lock-contention: is the real callback blocked by a held _LANES_LOCK? --
def contention(hold_sec: float) -> None:
    ready = threading.Event()
    release = threading.Event()

    def holder():
        with el._LANES_LOCK:      # exactly the lock executor_lane_status() needs
            ready.set()
            release.wait(hold_sec + 5)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    ready.wait(2)
    # time the REAL callback while the handler holds _LANES_LOCK
    c0 = time.perf_counter()
    state = srv._restart_busy_snapshot_state()
    c1 = time.perf_counter()
    blocked_ms = (c1 - c0) * 1000.0
    release.set()
    t.join(2)
    # baseline: callback with nothing holding the lock
    b0 = time.perf_counter()
    srv._restart_busy_snapshot_state()
    b1 = time.perf_counter()
    print(f"=== contention: handler holds _LANES_LOCK for ~{hold_sec}s ===")
    print(f"  callback WHILE lock held : {blocked_ms:9.2f} ms")
    print(f"  callback lock free       : {(b1-b0)*1000:9.2f} ms")
    print(f"  busy={state.get('busy')} keys={sorted(state)[:6]}...")
    verdict = "BLOCKED (callback waits on _LANES_LOCK)" if blocked_ms > hold_sec * 500 \
        else "NOT blocked (callback returned despite held lock)"
    print(f"  VERDICT: {verdict}")


# ----- 2) publish-cycle delay per config, on the chosen drive -----
def cycles(config: str, seconds: float, dirarg: str) -> None:
    base = _resolve_dir(dirarg)
    base.mkdir(parents=True, exist_ok=True)
    do_fsync = (config == "a")
    path = base / f"busy_{config}.json"
    pub = Probe(path, srv._restart_busy_snapshot_state, interval=0.5, do_fsync=do_fsync)
    pub.start()
    time.sleep(seconds)
    pub.stop()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    fsync_note = "fsync ON" if do_fsync else "fsync OFF"
    print(f"=== cycles config={config} ({fsync_note}) drive={dirarg} path={path} "
          f"seconds={seconds} ===")
    for key in ("io", "snapshot", "gap"):
        print("  " + _stats(pub.rows, key))
    late = [r for r in pub.rows if (r["gap"] or 0) > 1.0]
    print(f"  cycles with gap>1s: {len(late)}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "contention"
    if mode == "contention":
        contention(float(sys.argv[2]) if len(sys.argv) > 2 else 4.0)
    elif mode == "cycles":
        cfg = sys.argv[2] if len(sys.argv) > 2 else "a"
        secs = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
        d = sys.argv[4] if len(sys.argv) > 4 else "D"
        cycles(cfg, secs, d)
