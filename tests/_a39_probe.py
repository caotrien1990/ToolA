"""A39 Step 1 instrumentation harness (NOT a pytest test).

Drives BusySnapshotPublisher in-process and records, per publish cycle:
  (a) snapshot() callback time
  (b) json+flush+fsync time
  (c) os.replace time
  (gap) wall time between consecutive loop iterations (thread starvation)

Run: ./venv312/Scripts/python.exe tests/_a39_probe.py <scenario> <seconds>
scenarios: idle | lockcontend | gilburn | fsyncstorm
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.lifecycle.restart_protocol import BusySnapshotPublisher  # noqa: E402


class Instrumented(BusySnapshotPublisher):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rows: list[dict] = []
        self._last_start: float | None = None

    def publish_once(self) -> None:  # mirror body, timing each phase
        with self._publish_lock:
            t0 = time.perf_counter()
            gap = None if self._last_start is None else (t0 - self._last_start)
            self._last_start = t0
            payload = dict(self.snapshot())
            t1 = time.perf_counter()
            payload["captured_at"] = time.time()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            import secrets
            temporary = self.path.with_name(
                f"{self.path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}"
            )
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    json.dump(payload, stream, separators=(",", ":"), sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                t2 = time.perf_counter()
                os.replace(temporary, self.path)
                t3 = time.perf_counter()
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            self.rows.append({
                "gap": gap,
                "snapshot": t1 - t0,
                "io": t2 - t1,
                "replace": t3 - t2,
                "total": t3 - t0,
            })


def summarize(rows: list[dict]) -> None:
    def stats(key: str) -> str:
        vals = [r[key] for r in rows if r[key] is not None]
        if not vals:
            return f"{key}: n/a"
        vals_sorted = sorted(vals)
        p50 = vals_sorted[len(vals_sorted) // 2]
        p99 = vals_sorted[int(len(vals_sorted) * 0.99)]
        return (f"{key:9s} n={len(vals):4d} p50={p50*1000:8.2f}ms "
                f"p99={p99*1000:8.2f}ms max={max(vals)*1000:9.2f}ms")

    for key in ("gap", "snapshot", "io", "replace", "total"):
        print("  " + stats(key))
    # A "burst" = any cycle whose age-at-next-read could exceed 3.0s.
    # The reader sees staleness = time since last successful publish. The worst
    # staleness the publisher can inflict on a reader is (interval + gap + total)
    # for the cycle that was late. Report cycles where gap or total is large.
    big = [r for r in rows if (r["gap"] or 0) > 1.0 or r["total"] > 1.0]
    print(f"  cycles with gap>1s or total>1s: {len(big)}")
    for r in big[:20]:
        print(f"    gap={ (r['gap'] or 0)*1000:9.1f}ms snapshot={r['snapshot']*1000:8.1f}ms "
              f"io={r['io']*1000:8.1f}ms replace={r['replace']*1000:8.1f}ms")


# ---- snapshot callback that mimics the real one's lock dependency ----
_shared_lock = threading.Lock()


def realistic_snapshot() -> dict:
    # The real callback grabs _LANES_LOCK, accounting._condition, semaphore dicts.
    # Model that as a single lock acquisition + a little dict work.
    with _shared_lock:
        return {"busy": False, "requests_in_flight": 0, "executor_in_flight": 0,
                "accounting_pending": 0, "semaphores": [], "generation": "gen-a"}


def run(scenario: str, seconds: float) -> None:
    stop = threading.Event()
    workers: list[threading.Thread] = []

    if scenario == "lockcontend":
        # A busy request handler holds the snapshot's lock for ~4s bursts,
        # every ~30s, exactly the shape of executor_lane_status contending.
        def holder():
            while not stop.is_set():
                stop.wait(25)
                if stop.is_set():
                    break
                with _shared_lock:
                    time.sleep(4.0)  # simulate a handler holding the lane lock
        t = threading.Thread(target=holder, daemon=True)
        t.start(); workers.append(t)

    elif scenario == "gilburn":
        # CPU-bound python threads that hold the GIL in bursts, starving the
        # publisher thread's scheduling.
        def burner():
            while not stop.is_set():
                stop.wait(20)
                if stop.is_set():
                    break
                end = time.perf_counter() + 4.0
                x = 0
                while time.perf_counter() < end:
                    x += 1  # pure-python GIL-bound spin
        for _ in range(4):
            t = threading.Thread(target=burner, daemon=True)
            t.start(); workers.append(t)

    elif scenario == "fsyncstorm":
        def storm():
            buf = b"x" * (2 * 1024 * 1024)
            i = 0
            while not stop.is_set():
                p = Path(tempfile.gettempdir()) / f"_a39storm_{os.getpid()}_{threading.get_ident()}_{i}"
                try:
                    with p.open("wb") as f:
                        f.write(buf); f.flush(); os.fsync(f.fileno())
                    p.unlink()
                except OSError:
                    pass
                i += 1
        for _ in range(8):
            t = threading.Thread(target=storm, daemon=True)
            t.start(); workers.append(t)

    with tempfile.TemporaryDirectory() as tmp:
        pub = Instrumented(Path(tmp) / "busy.json", realistic_snapshot, interval=0.5)
        pub.start()
        time.sleep(seconds)
        pub.stop()
        stop.set()
        for t in workers:
            t.join(timeout=6)
        print(f"=== scenario={scenario} seconds={seconds} ===")
        summarize(pub.rows)


if __name__ == "__main__":
    scen = sys.argv[1] if len(sys.argv) > 1 else "idle"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    run(scen, secs)
