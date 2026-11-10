"""Out-of-process watchdog: inactivity warns; restart requires proven liveness loss."""
import importlib.util
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
HTTP_PORT = int(os.environ.get("ORCHESTRATOR_PORT", "8083"))
LIVEZ_PORT = int(os.environ.get("ORCHESTRATOR_LIVEZ_PORT", "8084"))
HEALTH = "http://127.0.0.1:" + str(HTTP_PORT) + "/health"
LIVEZ = "http://127.0.0.1:" + str(LIVEZ_PORT) + "/livez"
FAILURE_THRESHOLD = 3
FAILURE_INTERVAL_SEC = int(os.environ.get("ORCHESTRATOR_WATCHDOG_FAILURE_INTERVAL_SEC", "10"))
INACTIVITY_WINDOW_SEC = float(os.environ.get("ORCHESTRATOR_WATCHDOG_INACTIVITY_SEC", "60"))
OLDEST_WAIT_DEGRADED_MS = float(os.environ.get("ORCHESTRATOR_WATCHDOG_OLDEST_WAIT_MS", "30000"))
RESTART_TASK = "ToolA_Orchestrator"
FIRE_MODE = os.environ.get("ORCHESTRATOR_WATCHDOG_FIRE_MODE", "live")

_contract_mod = None


def log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512_000:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(datetime.now().isoformat(timespec="seconds") + " " + msg + "\n")
    except Exception:
        pass


def _load_contract():
    """Load the shared /livez contract module by path, anchored at HERE.

    The watchdog runs out-of-process (scheduled task, plain script), so the
    contract is loaded from orchestrator/lifecycle/livez_contract.py
    relative to this file, inside main() after log() is available. Any load
    failure raises here; main() turns it into WATCHDOG_CONTRACT_UNAVAILABLE
    and never fires blind.
    """
    global _contract_mod
    if _contract_mod is None:
        path = os.path.join(HERE, "orchestrator", "lifecycle", "livez_contract.py")
        spec = importlib.util.spec_from_file_location("watchdog_livez_contract", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["watchdog_livez_contract"] = mod
        spec.loader.exec_module(mod)
        _contract_mod = mod
    return _contract_mod


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
    # PNT-14 (disposition: keep + note): the capacity / oldest_wait_ms
    # branches below are defensive surface pinned by test contract
    # (test_watchdog_a.py, test_watchdog_inactivity.py). Live /health lanes
    # publish only {name, active, queued, accepting} (verified at P0.6), so
    # on the live shape those branches simply never trigger; they stay.
    if not _service_ok(health):
        return "down", "health_not_ready"
    totals = _lane_totals(health)
    accepting = bool(health.get("executor_accepting", True))
    saturated = totals["capacity"] > 0 and totals["active"] + totals["queued"] >= totals["capacity"]
    if not accepting or saturated or totals["oldest_wait_ms"] >= OLDEST_WAIT_DEGRADED_MS:
        return "degraded", "executor_not_accepting_or_saturated"
    return "healthy", "health_ready"


def _livez_anchor(assessment) -> str:
    # Anchor table (plan D3): the livez component of probe()'s detail.
    state = assessment.state
    if state in ("INVALID", "MISMATCH"):
        return "livez_contract_mismatch"
    if state == "ALIVE_LEGACY":
        return "livez_v1"
    return "livez_alive"


def _probe_full():
    """probe() plus the internals the decision loop needs.

    Returns (classification, generation, detail, health, livez_ok,
    assessment, health_classification). The first five are exactly the
    probe() contract; health_classification is _classify_health's verdict
    on the health payload BEFORE the livez rescue softens it ("down" when
    the health fetch itself failed, i.e. health == {}).
    """
    contract = _load_contract()
    livez_ok = False
    generation = ""
    assessment = None
    try:
        livez = _read_json(LIVEZ)
        assessment = contract.classify_livez(contract.fetch_result_ok(livez))
        livez_ok = bool(assessment.is_alive)
        if isinstance(livez, dict):
            generation = str(livez.get("generation") or "")
        livez_detail = _livez_anchor(assessment)
    except Exception as exc:
        assessment = contract.classify_livez(contract.fetch_result_error(exc))
        livez_detail = "livez_" + type(exc).__name__
    try:
        health = _read_json(HEALTH)
    except Exception as exc:
        return "down", generation, livez_detail + ";health_" + type(exc).__name__, {}, livez_ok, assessment, "down"
    if not isinstance(health, dict):
        health = {}
    generation = str(health.get("generation") or generation)
    classification, detail = _classify_health(health)
    health_classification = classification
    if livez_ok and classification == "down":
        classification, detail = "degraded", "livez_alive;" + detail
    return classification, generation, livez_detail + ";" + detail, health, livez_ok, assessment, health_classification


def probe() -> tuple[str, str, str, dict, bool]:
    classification, generation, detail, health, livez_ok, _assessment, _hc = _probe_full()
    return classification, generation, detail, health, livez_ok


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


def _ensure_state_keys(state: dict) -> dict:
    # Missing/corrupt/old-shape state: the new keys are initialized (empty
    # first-seen maps fill with "now" on the first sample that observes a
    # generation) and the init is logged -- never a crash.
    if any(k not in state for k in ("contract_streak", "first_seen_gen", "first_seen_gen_phase", "samples")):
        state.setdefault("contract_streak", 0)
        state.setdefault("first_seen_gen", {})
        state.setdefault("first_seen_gen_phase", {})
        state.setdefault("samples", [])
        log("STATE_FIRST_SEEN_INIT")
    return state


def _prune_first_seen(mapping: dict, current_generation: str, now_ms: int, grace_s: float, phase_map: bool) -> dict:
    kept = {}
    for key, ts in mapping.items():
        gen = key.rsplit("|", 1)[0] if phase_map else key
        if gen != current_generation and now_ms - int(ts) > grace_s * 1000:
            continue
        kept[key] = ts
    if len(kept) > 8:
        kept = dict(sorted(kept.items(), key=lambda kv: int(kv[1]))[-8:])
    return kept


def _suppressed(generation: str, first_seen_gen: dict, now_ms: int, grace_s: float) -> bool:
    # Suppression applies only when the generation is known and its
    # first-seen is younger than the boot grace.
    if not generation:
        return False
    first = first_seen_gen.get(generation)
    if first is None:
        return False
    return now_ms - int(first) < grace_s * 1000


def _in_grace(assessment, generation: str, first_seen_gen: dict, first_seen_gen_phase: dict, now_ms: int, grace_s: float) -> bool:
    # A valid payload boot_age_s decides on its own; only an invalid/missing
    # age falls back to the wall clock: the (generation, phase) first-seen
    # window, hard-capped by the generation's own first-seen + grace (phase
    # rotation never stacks windows).
    if assessment.boot_age_s is not None:
        return assessment.boot_age_s < grace_s
    if not generation:
        return False
    gen_first = first_seen_gen.get(generation)
    if gen_first is None or now_ms - int(gen_first) >= grace_s * 1000:
        return False
    phase_first = first_seen_gen_phase.get(generation + "|" + assessment.phase, gen_first)
    return now_ms - int(phase_first) < grace_s * 1000


def _apply_sample(prev: dict, classification: str, generation: str, detail: str, health: dict,
                  assessment, health_classification: str, grace_s: float):
    """Fold one sample into the persistent state (plan D3 streak table).

    Returns (state, dead) where dead is the DEAD_SAMPLE predicate --
    (livez UNREACHABLE) and (health classified "down") -- the only sample
    shape that lets the decision loop take another sample in this run.
    Suppression and grace only change a sample's streak EFFECT
    (accrue / freeze / reset / none), never the predicate.
    """
    now = time.time()
    now_ms = int(now * 1000)
    work = prev
    if generation and prev.get("generation") and prev.get("generation") != generation:
        # Generation changed: liveness evidence resets (the historical
        # :121-122 wipe), but the first-seen maps and the contract streak
        # survive it -- they describe generations/phases, not the old
        # instance's samples.
        work = {k: prev[k] for k in ("contract_streak", "first_seen_gen", "first_seen_gen_phase", "samples") if k in prev}
    fp = _progress_fingerprint(health)
    progressed = fp != work.get("last_fp")
    last_progress_at = now if progressed else float(work.get("last_progress_at", now))
    totals = _lane_totals(health)
    busy = totals["active"] + totals["queued"] > 0
    inactive = busy and now - last_progress_at >= INACTIVITY_WINDOW_SEC
    inactive_samples = int(work.get("inactive_samples", 0)) + 1 if inactive else 0
    # First-seen law (pin): recorded set-if-absent the first time a
    # generation is observed in ANY sample (from the livez payload or the
    # health fallback), for the generation and for each (generation, phase).
    first_seen_gen = dict(work.get("first_seen_gen") or {})
    first_seen_gen_phase = dict(work.get("first_seen_gen_phase") or {})
    if generation:
        if generation not in first_seen_gen:
            first_seen_gen[generation] = now_ms
        phase_key = generation + "|" + assessment.phase
        if phase_key not in first_seen_gen_phase:
            first_seen_gen_phase[phase_key] = now_ms
    first_seen_gen = _prune_first_seen(first_seen_gen, generation, now_ms, grace_s, False)
    first_seen_gen_phase = _prune_first_seen(first_seen_gen_phase, generation, now_ms, grace_s, True)
    down_prev = int(work.get("down_samples", 0))
    streak_prev = int(work.get("contract_streak", 0))
    state_name = assessment.state
    dead = state_name == "UNREACHABLE" and health_classification == "down"
    if state_name == "INVALID":
        # Contract garbage is never death evidence: down_samples is kept,
        # the contract streak accrues and an alarm is logged.
        down_samples = down_prev
        contract_streak = streak_prev + 1
        effect = "none"
        log("WATCHDOG_CONTRACT_ALARM " + json.dumps(
            {"state": state_name, "contract_detail": assessment.detail,
             "probe_detail": detail, "contract_streak": contract_streak}, sort_keys=True))
    elif state_name in ("ALIVE_BOOTING", "MISMATCH"):
        contract_streak = 0
        if _in_grace(assessment, generation, first_seen_gen, first_seen_gen_phase, now_ms, grace_s):
            down_samples, effect = down_prev, "freeze"
        elif health_classification == "down":
            down_samples, effect = down_prev + 1, "accrue"
        else:
            down_samples, effect = 0, "reset"
    elif state_name == "ALIVE_STOPPING":
        contract_streak = 0
        if health_classification == "down":
            down_samples, effect = down_prev + 1, "accrue"
        else:
            down_samples, effect = 0, "reset"
    elif state_name == "UNREACHABLE":
        contract_streak = 0
        if health_classification == "down":
            if _suppressed(generation, first_seen_gen, now_ms, grace_s):
                down_samples, effect = down_prev, "freeze"
            else:
                down_samples, effect = down_prev + 1, "accrue"
        else:
            down_samples, effect = 0, "reset"
    else:  # ALIVE_READY / ALIVE_LEGACY
        contract_streak = 0
        down_samples, effect = 0, "reset"
    samples = list(work.get("samples") or [])
    samples.append({"ts": now, "state": state_name, "detail": detail, "phase": assessment.phase,
                    "boot_age_s": assessment.boot_age_s,
                    "health_classification": health_classification, "streak_effect": effect})
    state = {"generation": generation, "last_fp": fp, "last_progress_at": last_progress_at,
             "inactive_samples": inactive_samples, "down_samples": down_samples,
             "last_classification": classification, "ts": now,
             "contract_streak": contract_streak, "first_seen_gen": first_seen_gen,
             "first_seen_gen_phase": first_seen_gen_phase, "samples": samples[-20:]}
    if inactive:
        log("WATCHDOG_INACTIVITY " + json.dumps({"lane": "all", **totals,
            "seconds_since_progress": round(now - last_progress_at, 3),
            "suggestion": "CA inspect tool/log; warning only; no cancel/kill"}, sort_keys=True))
    return state, dead


def _fire(state: dict) -> int:
    down_n = int(state.get("down_samples", 0))
    # The firing log carries the probe detail of exactly the last
    # FAILURE_THRESHOLD accrue samples that built the deciding counter
    # (PNT-11: the detail used to be discarded here).
    accrue = [s for s in state.get("samples", []) if s.get("streak_effect") == "accrue"][-FAILURE_THRESHOLD:]
    evidence = json.dumps([{"state": s.get("state"), "detail": s.get("detail")} for s in accrue], sort_keys=True)
    if FIRE_MODE == "dry-run":
        log("WOULD_FIRE " + str(down_n) + " consecutive liveness failures -> schtasks /run " + RESTART_TASK + " evidence=" + evidence)
    else:
        log(str(down_n) + " consecutive liveness failures -> schtasks /run " + RESTART_TASK + " evidence=" + evidence)
        try:
            result = subprocess.run(["schtasks", "/run", "/tn", RESTART_TASK], capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log("schtasks rc=" + str(result.returncode))
        except Exception as exc:
            log("schtasks run failed: " + str(exc))
    # One firing issues at most ONE restart: zero the accrual so a daemon
    # that stays dead is retried once per later firing (no storm, no latch).
    state["down_samples"] = 0
    state["last_restart_at"] = time.time()
    _save_state(state)
    return 1


def main() -> int:
    try:
        contract = _load_contract()
    except Exception as exc:
        log("WATCHDOG_CONTRACT_UNAVAILABLE " + type(exc).__name__ + ": " + str(exc))
        return 0
    grace_s = float(contract.BOOT_GRACE_S)
    prev = _ensure_state_keys(_load_state())
    for attempt in range(FAILURE_THRESHOLD):
        classification, generation, detail, health, livez_ok, assessment, health_classification = _probe_full()
        state, dead = _apply_sample(prev, classification, generation, detail, health,
                                    assessment, health_classification, grace_s)
        _save_state(state)
        prev = state
        # Fire check comes FIRST (before any continue/exit decision): a
        # sample that completes the threshold fires in this same run even
        # when its own shape ends the run.
        if int(prev.get("down_samples", 0)) >= FAILURE_THRESHOLD:
            return _fire(prev)
        if not dead:
            return 0
        if attempt < FAILURE_THRESHOLD - 1:
            time.sleep(FAILURE_INTERVAL_SEC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
