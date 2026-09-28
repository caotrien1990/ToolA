"""Out-of-process watchdog: inactivity warns; restart requires proven liveness loss."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, ".orchestrator_cache", "watchdog.log")
STATE = os.path.join(HERE, ".orchestrator_cache", "watchdog_state.json")
SERVICE_NAME = "project-orchestrator"
LIVEZ_PROTOCOL = 1
HTTP_PORT = int(os.environ.get("ORCHESTRATOR_PORT", "8083"))
LIVEZ_PORT = int(os.environ.get("ORCHESTRATOR_LIVEZ_PORT", "8084"))
HEALTH = "http://127.0.0.1:" + str(HTTP_PORT) + "/health"
LIVEZ = "http://127.0.0.1:" + str(LIVEZ_PORT) + "/livez"
FAILURE_THRESHOLD = 3
FAILURE_INTERVAL_SEC = int(os.environ.get("ORCHESTRATOR_WATCHDOG_FAILURE_INTERVAL_SEC", "10"))
INACTIVITY_WINDOW_SEC = float(os.environ.get("ORCHESTRATOR_WATCHDOG_INACTIVITY_SEC", "60"))
OLDEST_WAIT_DEGRADED_MS = float(os.environ.get("ORCHESTRATOR_WATCHDOG_OLDEST_WAIT_MS", "30000"))
RESTART_TASK = "ToolA_Orchestrator"


def log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512_000:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(datetime.now().isoformat(timespec="seconds") + " " + msg + "\n")
    except Exception:
        pass


def _read_json(url: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if getattr(response, "status", 200) != 200:
            raise RuntimeError("HTTP_" + str(getattr(response, "status", "unknown")))
        return json.loads(response.read().decode("utf-8"))


def _service_ok(payload: dict) -> bool:
    return bool(payload.get("ok")) and payload.get("service") == SERVICE_NAME


def _lane_totals(health: dict) -> dict:
    lanes = health.get("executor_lanes") or []
    return {
        "active": int(health.get("executor_active_total", sum(int(x.get("active", 0)) for x in lanes))),
        "queued": int(health.get("executor_queued_total", sum(int(x.get("queued", 0)) for x in lanes))),
        "capacity": sum(int(x.get("capacity", 0)) for x in lanes),
        "oldest_wait_ms": max([float(x.get("oldest_wait_ms", 0.0)) for x in lanes] or [0.0]),
        "oldest_active_age_ms": float(health.get("executor_oldest_active_age_ms", max([float(x.get("oldest_active_age_ms", 0.0)) for x in lanes] or [0.0]))),
    }


def _classify_health(health: dict) -> tuple[str, str]:
    if not _service_ok(health):
        return "down", "health_not_ready"
    totals = _lane_totals(health)
    accepting = bool(health.get("executor_accepting", True))
    saturated = totals["capacity"] > 0 and totals["active"] + totals["queued"] >= totals["capacity"]
    if not accepting or saturated or totals["oldest_wait_ms"] >= OLDEST_WAIT_DEGRADED_MS:
        return "degraded", "executor_not_accepting_or_saturated"
    return "healthy", "health_ready"


def probe() -> tuple[str, str, str, dict, bool]:
    livez_ok = False
    livez_detail = "invalid_livez"
    generation = ""
    try:
        livez = _read_json(LIVEZ)
        livez_ok = _service_ok(livez) and livez.get("livez_protocol") == LIVEZ_PROTOCOL
        generation = str(livez.get("generation") or "")
        livez_detail = "livez_v1" if livez_ok else "livez_contract_mismatch"
    except Exception as exc:
        livez_detail = "livez_" + type(exc).__name__
    try:
        health = _read_json(HEALTH)
    except Exception as exc:
        return "down", generation, livez_detail + ";health_" + type(exc).__name__, {}, livez_ok
    generation = str(health.get("generation") or generation)
    classification, detail = _classify_health(health)
    if livez_ok and classification == "down":
        classification, detail = "degraded", "livez_alive;" + detail
    return classification, generation, livez_detail + ";" + detail, health, livez_ok


def _progress_fingerprint(health: dict) -> str:
    # Live /health lanes publish only {name, active, queued, accepting}; the
    # monotonic work counters are top-level accounting_* totals. The watchdog's
    # own log line must not count as progress, so no LOG stat here.
    counters = [int(health.get(key, 0)) for key in
                ("accounting_processed_total", "accounting_failed_total", "accounting_rejected_total")]
    return json.dumps([str(health.get("generation") or ""), *counters], separators=(",", ":"))


def _load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def _sample_state(prev: dict, classification: str, generation: str, health: dict, livez_ok: bool) -> dict:
    now = time.time()
    fp = _progress_fingerprint(health)
    if generation and prev.get("generation") and prev.get("generation") != generation:
        prev = {}
    progressed = fp != prev.get("last_fp")
    last_progress_at = now if progressed else float(prev.get("last_progress_at", now))
    totals = _lane_totals(health)
    busy = totals["active"] + totals["queued"] > 0
    inactive = busy and now - last_progress_at >= INACTIVITY_WINDOW_SEC
    inactive_samples = int(prev.get("inactive_samples", 0)) + 1 if inactive else 0
    # A restart needs PROVEN liveness loss, which the inactivity warning can
    # never prove: only "down" samples (livez AND health both failed) accrue
    # here, and any other sample resets the counter.
    down = classification == "down" and not livez_ok
    down_samples = int(prev.get("down_samples", 0)) + 1 if down else 0
    state = {"generation": generation, "last_fp": fp, "last_progress_at": last_progress_at,
             "inactive_samples": inactive_samples, "down_samples": down_samples,
             "last_classification": classification, "ts": now}
    if inactive:
        log("WATCHDOG_INACTIVITY " + json.dumps({"lane":"all", **totals,
            "seconds_since_progress": round(now-last_progress_at, 3),
            "suggestion":"CA inspect tool/log; warning only; no cancel/kill"}, sort_keys=True))
    return state


def main() -> int:
    prev = _load_state()
    for attempt in range(FAILURE_THRESHOLD):
        classification, generation, detail, health, livez_ok = probe()
        state = _sample_state(prev, classification, generation, health, livez_ok)
        _save_state(state)
        prev = state
        if classification != "down" or livez_ok:
            return 0
        if attempt < FAILURE_THRESHOLD - 1:
            time.sleep(FAILURE_INTERVAL_SEC)
    down_n = int(prev.get("down_samples", 0))
    if down_n >= FAILURE_THRESHOLD:
        log(str(down_n) + " consecutive liveness failures -> schtasks /run " + RESTART_TASK)
        try:
            result = subprocess.run(["schtasks", "/run", "/tn", RESTART_TASK], capture_output=True, text=True, timeout=30)
            log("schtasks rc=" + str(result.returncode))
        except Exception as exc:
            log("schtasks run failed: " + str(exc))
        # One firing issues at most ONE restart: zero the accrual so a daemon
        # that stays dead is retried once per later firing (no storm, no latch).
        prev["down_samples"] = 0
        prev["last_restart_at"] = time.time()
        _save_state(prev)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())