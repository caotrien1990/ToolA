import importlib
import json
import threading
import time

from orchestrator.server.executor_lanes import ExecutorLane


def test_oldest_active_age_grows_then_resets(monkeypatch):
    lane = ExecutorLane("age", max_workers=1, max_queue=0)
    gate = threading.Event()
    clock = {"now": 10.0}
    monkeypatch.setattr("orchestrator.server.executor_lanes.time.monotonic", lambda: clock["now"])
    future = lane.submit(lambda: gate.wait())
    for _ in range(100):
        if lane.status()["active"]:
            break
        time.sleep(0.001)
    clock["now"] = 12.5
    assert lane.status()["oldest_active_age_ms"] == 2500.0
    gate.set(); future.result(timeout=2)
    assert lane.status()["oldest_active_age_ms"] == 0.0
    lane._executor.shutdown(wait=True)


def _health(**kw):
    """Mirrors the LIVE /health shape: executor_lanes rows carry only
    {name, active, queued, accepting} and the monotonic work counters are the
    top-level accounting_* totals. Live lanes publish no capacity, so tests
    that pin the degraded path pass capacity/oldest_wait_ms explicitly."""
    a = kw.get("executor_active_total", 1); q = kw.get("executor_queued_total", 0)
    acc = kw.get("executor_accepting", True)
    lane = {"name": "test", "active": a, "queued": q, "accepting": acc}
    for k in ("capacity", "oldest_wait_ms", "oldest_active_age_ms"):
        if k in kw:
            lane[k] = kw[k]
    return {"ok": True, "service": "project-orchestrator",
            "generation": kw.get("generation", "g1"), "executor_accepting": acc,
            "executor_active_total": a, "executor_queued_total": q,
            "executor_oldest_active_age_ms": kw.get("executor_oldest_active_age_ms", 0.0),
            "executor_lanes": [lane],
            "accounting_processed_total": kw.get("accounting_processed_total", 0),
            "accounting_failed_total": kw.get("accounting_failed_total", 0),
            "accounting_rejected_total": kw.get("accounting_rejected_total", 0)}


def _run(monkeypatch, tmp_path, health, now=100.0, livez=None):
    """Drive ONE real main(): the REAL probe() runs against a patched
    _read_json (livez+health), the REAL log()/state write to tmp_path, the
    clock is pinned to `now`, and subprocess.run is recorded. State persists in
    tmp_path across calls so repeated firings accrue like production."""
    import watchdog_a
    wd = importlib.reload(watchdog_a)
    monkeypatch.setattr(wd, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(wd, "LOG", str(tmp_path / "watchdog.log"))
    monkeypatch.setattr(wd, "INACTIVITY_WINDOW_SEC", 10.0)
    monkeypatch.setattr(wd, "FAILURE_INTERVAL_SEC", 0)
    monkeypatch.setattr(wd.time, "time", lambda: now)
    if livez is None:
        livez = {"ok": True, "service": "project-orchestrator",
                 "livez_protocol": wd.LIVEZ_PROTOCOL,
                 "generation": health.get("generation", "g1")}
    replies = [livez, health]
    monkeypatch.setattr(wd, "_read_json", lambda *a, **k: replies.pop(0))
    calls = {"restart": 0}
    class R: returncode = 0; stdout = ""; stderr = ""
    def _cmd(*a, **k):
        calls["restart"] += 1; return R()
    monkeypatch.setattr(wd.subprocess, "run", _cmd)
    rc = wd.main()
    p = tmp_path / "state.json"; l = tmp_path / "watchdog.log"
    return rc, calls, json.loads(p.read_text()) if p.exists() else {}, l.read_text() if l.exists() else ""


def test_queue_full_livez_healthy_degraded_no_restart(monkeypatch, tmp_path):
    """A saturated/oldest-wait daemon still answers livez, so it is degraded,
    not down: inactivity only WARNS and a restart needs proven liveness loss."""
    h = _health(executor_queued_total=1, capacity=2, oldest_wait_ms=40000)
    rc, calls, state, _ = _run(monkeypatch, tmp_path, h)
    assert rc == 0 and calls["restart"] == 0 and state["last_classification"] == "degraded"


def test_busy_inactive_accumulates_despite_own_log(monkeypatch, tmp_path):
    """Pins D2 (self-log counted as progress): a busy daemon whose published
    accounting counters stay constant must accrue inactive_samples 1,2,3 across
    firings. The REAL log() appends WATCHDOG_INACTIVITY to the tmp LOG each
    time; because the fingerprint no longer stats LOG, that own line is NOT
    misread as progress and the counter never drops back."""
    h = _health(executor_oldest_active_age_ms=20000)
    counts = []
    for now in (100.0, 200.0, 300.0, 400.0):
        rc, calls, state, text = _run(monkeypatch, tmp_path, h, now=now)
        assert rc == 0 and calls["restart"] == 0
        counts.append(state["inactive_samples"])
    assert counts == [0, 1, 2, 3]
    assert "WATCHDOG_INACTIVITY" in text and "oldest_active_age_ms" in text


def test_accounting_progress_resets_inactivity(monkeypatch, tmp_path):
    """Pins D3 (dead lane counters): an incremented top-level
    accounting_processed_total is real progress, so a stalled accrual resets to
    0 on the next firing -- the fingerprint reads the counters live /health
    actually emits, not lane keys (completed/failed/rejected) that are always
    absent from the published lanes."""
    h1 = _health(executor_oldest_active_age_ms=20000, accounting_processed_total=5)
    _run(monkeypatch, tmp_path, h1, now=100.0)
    _, _, s2, _ = _run(monkeypatch, tmp_path, h1, now=200.0)   # constant -> accrues
    assert s2["inactive_samples"] == 1
    h2 = _health(executor_oldest_active_age_ms=20000, accounting_processed_total=6)
    _, _, s3, _ = _run(monkeypatch, tmp_path, h2, now=300.0)   # progress -> resets
    assert s3["inactive_samples"] == 0 and s3["last_progress_at"] == 300.0
