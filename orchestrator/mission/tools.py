"""Mission queue HTTP tools (P3b-2).

This module is the tool-facing integration layer over the mission store and
engine. It keeps durable state in ``.orchestrator_cache/missions.db`` and leaves
the state-machine primitives in ``store.py`` / ``engine.py`` as the source of
truth.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import engine, instance_lock, store

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MISSION_DB_PATH = PROJECT_ROOT / ".orchestrator_cache" / "missions.db"
DEFAULT_TIMEOUT_SEC = 6000

ModelCall = Callable[..., Awaitable[dict[str, Any]]]
SendCall = Callable[..., Awaitable[dict[str, Any]]]

_MODEL_CALL: Optional[ModelCall] = None
_SEND_CALL: Optional[SendCall] = None


class ControlledFault(RuntimeError):
    """Test-only deterministic crash injection for mission harnesses."""


def configure_runtime(*, model_call: Optional[ModelCall] = None, send_call: Optional[SendCall] = None) -> None:
    """Inject server-local council functions for tests/live late wiring."""
    global _MODEL_CALL, _SEND_CALL
    if model_call is not None:
        _MODEL_CALL = model_call
    if send_call is not None:
        _SEND_CALL = send_call


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def _err(error_name: str, error: str, **extra: Any) -> str:
    payload = {"ok": False, "error_name": error_name, "error": error}
    payload.update(extra)
    return _json(payload)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_lock() -> Optional[str]:
    if not instance_lock.is_lock_holder():
        return _err("NOT_LOCK_HOLDER", "mission tools require the active orchestrator instance lock")
    return None


def _ensure_db() -> Path:
    store.init_db(MISSION_DB_PATH)
    return MISSION_DB_PATH


def _test_fault(name: str) -> bool:
    return os.environ.get("ORCHESTRATOR_TEST_FAULT") == name


def _parse_json(value: Any, default: Any, field: str) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} must be valid JSON") from exc


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@contextmanager
def _connect(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        conn.close()


def _task_rows(db_path: Path, mission_id: int) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE mission_id = ? ORDER BY seq, id",
            (mission_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _ledger_tail(db_path: Path, mission_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, mission_id, task_id, ts, kind, detail_json
            FROM progress_ledger
            WHERE mission_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (mission_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _has_human_gate_approval(db_path: Path, task_id: int, attempt: int) -> bool:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT detail_json
            FROM progress_ledger
            WHERE task_id = ? AND kind = 'human_gate'
            ORDER BY id DESC
            """,
            (task_id,),
        ).fetchall()
    for row in rows:
        try:
            detail = json.loads(row["detail_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if detail.get("decision") == "approve" and int(detail.get("attempt", -1)) == attempt:
            return True
    return False


def _update_mission_state(db_path: Path, mission_id: int, state: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE missions SET state = ?, updated_at = ? WHERE id = ?",
            (state, _utc_iso(), mission_id),
        )
        conn.commit()


# CASE-1909-B6: the task states that actually carry a lease, mirroring the
# reaper's own scan (it selects dispatching/started/verifying). A heartbeat is
# meaningful only for a task in one of these -- anywhere else there is no lease
# to refresh, so accepting one fabricates a field nothing reads.
_LEASE_BEARING_STATES = frozenset({"dispatching", "started", "verifying"})


def _force_task_state(db_path: Path, task_id: int, state: str, *,
                      bump_version: bool = True, **fields: Any) -> bool:
    allowed = {
        "attempt", "res_summary", "lease_expires_at", "child_pid",
        "started_at", "finished_at",
    }
    # bump_version=False is for a no-op refresh (heartbeat): bumping the optimistic
    # lock on every keep-alive conflicts with an in-flight cas_transition.
    sets = ["state = ?"] + (["version = version + 1"] if bump_version else [])
    vals: list[Any] = [state]
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"cannot set task field {key!r}")
        sets.append(f"{key} = ?")
        vals.append(value)
    vals.append(task_id)
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?",
            vals,
        )
        conn.commit()
        return cur.rowcount == 1


async def _call_model_tool(agent: str, *, action: str = "get", model: str = "", provider: str = "") -> dict[str, Any]:
    if _MODEL_CALL is not None:
        return await _MODEL_CALL(agent=agent, action=action, model=model, provider=provider)

    def _http() -> dict[str, Any]:
        body = {"agent": agent, "action": action}
        if model:
            body["model"] = model
        if provider:
            body["provider"] = provider
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:8083/api/call/council_worker_model",
            data=data,
            headers={"Content-Type": "application/json", "Origin": "http://localhost:8083"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return await asyncio.to_thread(_http)


async def _send_async(sender: str, to: str, text: str, timeout_sec: int, provider: str, model: str) -> dict[str, Any]:
    if _SEND_CALL is not None:
        return await _SEND_CALL(
            sender=sender, to=to, text=text, timeout_sec=timeout_sec,
            provider=provider, model=model,
        )

    def _http() -> dict[str, Any]:
        body = {
            "sender": sender,
            "to": to,
            "text": text,
            "timeout_sec": timeout_sec,
            "provider": provider,
            "model": model,
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:8083/api/council/send-async",
            data=data,
            headers={"Content-Type": "application/json", "Origin": "http://localhost:8083"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return await asyncio.to_thread(_http)


async def mission_create(title=None, tasks_json=None, task_ledger_json=None, room_path=None, created_by=None):
    lock_error = _require_lock()
    if lock_error:
        return lock_error
    try:
        tasks = _parse_json(tasks_json, [], "tasks_json")
        if not isinstance(tasks, list) or not tasks:
            return _err("INVALID_TASKS_JSON", "tasks_json must be a non-empty JSON array")
        ledger = _parse_json(task_ledger_json, {}, "task_ledger_json")
        missing = [i for i, task in enumerate(tasks) if not isinstance(task, dict) or not task.get("output_artifact_path")]
        if missing:
            return _err("MISSING_OUTPUT_ARTIFACT_PATH", "each task requires output_artifact_path", indexes=missing)
        db_path = _ensure_db()
        mission_id = store.create_mission(
            db_path,
            str(title or "").strip() or "Untitled mission",
            task_ledger_json=_dump_json(ledger),
            created_by=str(created_by or ""),
            room_path=str(room_path or ""),
        )
        task_ids: list[int] = []
        created_flags: list[bool] = []
        for seq, task in enumerate(tasks):
            depends = task.get("depends_on_json", task.get("depends_on", []))
            verify_gate = task.get("verify_gate_json", task.get("verify_gate", {}))
            retry_policy = task.get("retry_policy_json", task.get("retry_policy", {}))
            result = store.create_task(
                db_path,
                mission_id,
                seq=int(task.get("seq", seq)),
                title=task.get("title"),
                prompt_artifact_path=task.get("prompt_artifact_path"),
                output_artifact_path=task["output_artifact_path"],
                depends_on_json=_dump_json(depends),
                agent_hint=task.get("agent_hint"),
                model_hint=task.get("model_hint"),
                max_attempts=int(task.get("max_attempts", 3)),
                retry_policy_json=_dump_json(retry_policy),
                side_effect_class=task.get("side_effect_class", "pure_artifact"),
                verify_gate_json=_dump_json(verify_gate),
                timeout_sec=task.get("timeout_sec"),
                hard_cap_s=task.get("hard_cap_s"),
                task_key=task.get("task_key"),
            )
            task_ids.append(int(result["task_id"]))
            created_flags.append(bool(result["created"]))
        _update_mission_state(db_path, mission_id, "active")
        store.append_ledger(db_path, mission_id, None, "mission_create", _dump_json({"task_ids": task_ids}))
        return _json({"ok": True, "mission_id": mission_id, "task_ids": task_ids, "created_flags": created_flags})
    except Exception as exc:
        return _err("MISSION_CREATE_FAILED", str(exc))


async def mission_next(mission_id=None):
    lock_error = _require_lock()
    if lock_error:
        return lock_error
    try:
        db_path = _ensure_db()
        mission = store.get_mission(db_path, int(mission_id))
        if not mission:
            return _err("MISSION_NOT_FOUND", f"mission {mission_id} not found")
        rows = _task_rows(db_path, int(mission_id))
        task = engine.next_ready_task(rows)
        return _json({"ok": True, "task": task})
    except Exception as exc:
        return _err("MISSION_NEXT_FAILED", str(exc))


async def mission_dispatch(task_id=None):
    lock_error = _require_lock()
    if lock_error:
        return lock_error
    try:
        db_path = _ensure_db()
        task = store.get_task(db_path, int(task_id))
        if not task:
            return _err("TASK_NOT_FOUND", f"task {task_id} not found")
        agent = str(task.get("agent_hint") or "codo").strip().lower()
        model_hint = str(task.get("model_hint") or "").strip()
        state = str(task["state"])
        attempt = int(task["attempt"])
        if task.get("side_effect_class") == "destructive" and not _has_human_gate_approval(db_path, int(task["id"]), attempt):
            if state in {"pending", "ready"}:
                ok = store.cas_transition(db_path, int(task["id"]), state, "blocked", int(task["version"]))
                if not ok:
                    return _err("CAS_CONFLICT", "task changed before destructive human gate block")
            elif state != "blocked":
                return _err("INVALID_TASK_STATE", f"cannot dispatch task in state {state}", state=state)
            store.append_ledger(db_path, int(task["mission_id"]), int(task["id"]), "human_gate_needed", _dump_json({
                "attempt": attempt,
                "from_state": state,
                "reason": "destructive task requires mission_gate approve before dispatch",
            }))
            return _err(
                "NEEDS_HUMAN_GATE",
                "destructive task requires human approval before dispatch",
                task_id=int(task["id"]),
                hint="mission_gate(task_id, approve) roi dispatch lai",
            )
        current = await _call_model_tool(agent, action="get")
        current_model = ((current.get("current") or {}).get("model") or "").strip()
        current_provider = ((current.get("current") or {}).get("provider") or "").strip()
        if not current_model:
            if not model_hint:
                return _err("WORKER_MODEL_NOT_SET", "worker model must be set before mission_dispatch", agent=agent)
            set_result = await _call_model_tool(agent, action="set", model=model_hint)
            if not set_result.get("ok"):
                return _json(set_result)
            current = await _call_model_tool(agent, action="get")
            current_model = ((current.get("current") or {}).get("model") or model_hint).strip()
            current_provider = ((current.get("current") or {}).get("provider") or "").strip()
        wrapper = engine.build_wrapper(
            task_id=int(task["id"]),
            attempt=int(task["attempt"]),
            title=str(task.get("title") or ""),
            prompt_artifact_path=str(task.get("prompt_artifact_path") or ""),
            output_artifact_path=str(task.get("output_artifact_path") or ""),
            agent_label=f"{agent.capitalize()}C",
        )
        wrapper_sha = hashlib.sha256(wrapper.encode("utf-8")).hexdigest()
        request_id = f"mission-{task['id']}-a{task['attempt']}-{int(time.time() * 1000)}"
        outbox_id = store.add_outbox(db_path, int(task["id"]), request_id, wrapper_sha)
        store.append_ledger(db_path, int(task["mission_id"]), int(task["id"]), "dispatch_intent", _dump_json({
            "agent": agent,
            "model": current_model,
            "outbox_id": outbox_id,
            "request_id": request_id,
            "wrapper_sha": wrapper_sha,
        }))
        if state == "pending":
            ok = store.cas_transition(db_path, int(task["id"]), "pending", "dispatching", int(task["version"]))
        elif state == "ready":
            ok = store.cas_transition(db_path, int(task["id"]), "ready", "dispatching", int(task["version"]))
        else:
            return _err("INVALID_TASK_STATE", f"cannot dispatch task in state {state}", state=state)
        if not ok:
            return _err("CAS_CONFLICT", "task changed before dispatch")
        if _test_fault("after_outbox_before_send"):
            raise ControlledFault("after_outbox_before_send")
        send_result = await _send_async(
            sender="user",
            to=agent,
            text=wrapper,
            timeout_sec=int(task.get("timeout_sec") or DEFAULT_TIMEOUT_SEC),
            provider=current_provider,
            model=current_model,
        )
        if not send_result.get("ok"):
            return _json(send_result)
        fresh = store.get_task(db_path, int(task["id"])) or task
        lease = time.time() + float(task.get("hard_cap_s") or task.get("timeout_sec") or DEFAULT_TIMEOUT_SEC)
        child_pid = send_result.get("child_pid")
        store.cas_transition(
            db_path,
            int(task["id"]),
            "dispatching",
            "started",
            int(fresh["version"]),
            lease_expires_at=lease,
            child_pid=child_pid,
            started_at=_utc_iso(),
        )
        if child_pid is not None:
            store.set_outbox_child_pid(db_path, outbox_id, int(child_pid))
        store.append_ledger(db_path, int(task["mission_id"]), int(task["id"]), "dispatch_started", _dump_json({
            "outbox_id": outbox_id,
            "child_pid": child_pid,
            "note": "child_pid unavailable from send-async runtime" if child_pid is None else "",
        }))
        return _json({"ok": True, "task_id": int(task["id"]), "outbox_id": outbox_id, "dispatched": True, "child_pid": child_pid})
    except Exception as exc:
        return _err("MISSION_DISPATCH_FAILED", str(exc))


async def mission_report(task_id=None, attempt_token=None, res_path=None, summary=None, kind="final"):
    lock_error = _require_lock()
    if lock_error:
        return lock_error
    try:
        db_path = _ensure_db()
        task = store.get_task(db_path, int(task_id))
        if not task:
            return _err("TASK_NOT_FOUND", f"task {task_id} not found")
        report_kind = str(kind or "final").strip().lower()
        if report_kind == "heartbeat":
            # CASE-1909-B6: a heartbeat only EXTENDS a lease -- it never advances
            # state, because it re-writes str(task["state"]) unchanged. Accepting one
            # for a task nobody dispatched wrote a lease onto a row the reaper never
            # scans (it selects dispatching/started/verifying only), so a run of
            # heartbeats left the task pending and read exactly like a stuck state
            # machine. Refuse instead, and name the call that DOES advance it.
            if str(task["state"]) not in _LEASE_BEARING_STATES:
                return _err("HEARTBEAT_NOT_LEASED",
                            f"task {task['id']} is in state {task['state']!r}; a heartbeat "
                            "refreshes an existing lease and cannot create one",
                            task_id=int(task["id"]), state=str(task["state"]),
                            lease_bearing_states=sorted(_LEASE_BEARING_STATES),
                            ca_next_action="call_mission_dispatch_first")
            lease = time.time() + float(task.get("hard_cap_s") or task.get("timeout_sec") or DEFAULT_TIMEOUT_SEC)
            # bump_version=False: refreshing a lease must not invalidate the CAS
            # version an in-flight mission_dispatch is already holding.
            _force_task_state(db_path, int(task["id"]), str(task["state"]),
                              bump_version=False, lease_expires_at=lease)
            store.append_ledger(db_path, int(task["mission_id"]), int(task["id"]), "heartbeat", _dump_json({"summary": summary or ""}))
            return _json({"ok": True, "task_id": int(task["id"]), "kind": "heartbeat", "lease_expires_at": lease})
        parsed = engine.parse_attempt_token(str(attempt_token or ""))
        if not parsed or parsed[0] != int(task["id"]):
            return _err("INVALID_ATTEMPT_TOKEN", "attempt_token must contain mission_attempt=<task_id>:<attempt>")
        _, token_attempt = parsed
        if engine.is_stale_attempt(token_attempt, int(task["attempt"])):
            store.append_ledger(db_path, int(task["mission_id"]), int(task["id"]), "stale_attempt_rejected", _dump_json({
                "token_attempt": token_attempt,
                "current_attempt": int(task["attempt"]),
            }))
            return _err("STALE_ATTEMPT", "stale attempt report rejected")
        if report_kind == "final" and _test_fault("mid_partial_artifact"):
            raise ControlledFault("mid_partial_artifact")

        gate = _parse_json(task.get("verify_gate_json"), {}, "verify_gate_json")
        output_path = str(task.get("output_artifact_path") or "")
        pass_gate = True
        gate_type = str((gate or {}).get("type") or "").strip()
        if gate_type == "file_exists":
            pass_gate = Path(res_path or output_path).exists()
        elif gate_type in {"artifact_complete", "artifact-complete"}:
            end_marker = str((gate or {}).get("end_marker") or f"END_TASK_{task['id']}_A{task['attempt']}")
            pass_gate = engine.artifact_complete(res_path or output_path, end_marker)
        if pass_gate:
            fresh = store.get_task(db_path, int(task["id"])) or task
            if fresh["state"] != "verifying":
                store.cas_transition(db_path, int(task["id"]), str(fresh["state"]), "verifying", int(fresh["version"]))
                fresh = store.get_task(db_path, int(task["id"])) or fresh
            store.cas_transition(
                db_path,
                int(task["id"]),
                "verifying",
                "done",
                int(fresh["version"]),
                res_summary=str(summary or ""),
                finished_at=_utc_iso(),
            )
            store.append_ledger(db_path, int(task["mission_id"]), int(task["id"]), "final_done", _dump_json({"summary": summary or ""}))
            return _json({"ok": True, "task_id": int(task["id"]), "state": "done", "artifact_first": bool(output_path)})

        next_attempt = int(task["attempt"]) + 1
        if next_attempt < int(task["max_attempts"]):
            _force_task_state(db_path, int(task["id"]), "pending", attempt=next_attempt, res_summary=str(summary or ""))
            new_state = "pending"
        else:
            _force_task_state(db_path, int(task["id"]), "failed", res_summary=str(summary or ""), finished_at=_utc_iso())
            new_state = "failed"
        store.append_ledger(db_path, int(task["mission_id"]), int(task["id"]), "final_failed", _dump_json({"summary": summary or "", "next_state": new_state}))
        return _json({"ok": True, "task_id": int(task["id"]), "state": new_state, "gate_passed": False})
    except Exception as exc:
        return _err("MISSION_REPORT_FAILED", str(exc))


async def mission_status(mission_id=None, verbosity="summary"):
    lock_error = _require_lock()
    if lock_error:
        return lock_error
    try:
        db_path = _ensure_db()
        mission = store.get_mission(db_path, int(mission_id))
        if not mission:
            return _err("MISSION_NOT_FOUND", f"mission {mission_id} not found")
        tasks = _task_rows(db_path, int(mission_id))
        compact = [
            {
                "id": t["id"],
                "seq": t["seq"],
                "title": t["title"],
                "agent_hint": t["agent_hint"],
                "state": t["state"],
                "attempt": t["attempt"],
                "max_attempts": t["max_attempts"],
                "output_artifact_path": t["output_artifact_path"],
            }
            for t in tasks
        ]
        payload = {"ok": True, "mission": mission, "tasks": compact}
        if str(verbosity or "summary") == "full":
            payload["ledger_tail"] = _ledger_tail(db_path, int(mission_id), 50)
        return _json(payload)
    except Exception as exc:
        return _err("MISSION_STATUS_FAILED", str(exc))


async def mission_gate(task_id=None, decision=None, reason=None):
    lock_error = _require_lock()
    if lock_error:
        return lock_error
    try:
        db_path = _ensure_db()
        task = store.get_task(db_path, int(task_id))
        if not task:
            return _err("TASK_NOT_FOUND", f"task {task_id} not found")
        dec = str(decision or "").strip().lower()
        if dec not in {"approve", "reject"}:
            return _err("INVALID_DECISION", "decision must be approve or reject")
        target_state = "pending" if dec == "approve" else "skipped"
        if str(task["state"]) == "blocked":
            ok = store.cas_transition(db_path, int(task["id"]), "blocked", target_state, int(task["version"]))
        else:
            ok = _force_task_state(db_path, int(task["id"]), target_state)
        store.append_ledger(db_path, int(task["mission_id"]), int(task["id"]), "human_gate", _dump_json({
            "decision": dec,
            "attempt": int(task["attempt"]),
            "reason": reason or "",
            "from_state": task["state"],
            "to_state": target_state,
        }))
        return _json({"ok": bool(ok), "task_id": int(task["id"]), "state": target_state})
    except Exception as exc:
        return _err("MISSION_GATE_FAILED", str(exc))


async def leader_bootstrap():
    lock_error = _require_lock()
    if lock_error:
        return lock_error
    try:
        db_path = _ensure_db()
        rule_sha = None
        roster = None
        worker_models = None
        q_hint = None
        config_path = PROJECT_ROOT / ".council" / "council_config.json"
        models_path = PROJECT_ROOT / ".council" / "worker_models.json"
        if config_path.exists():
            # utf-8-sig: council_config.json may carry a UTF-8 BOM
            cfg = json.loads(config_path.read_text(encoding="utf-8-sig"))
            roster = cfg.get("agents") or cfg.get("roster") or cfg
        if models_path.exists():
            worker_models = json.loads(models_path.read_text(encoding="utf-8-sig"))
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM missions WHERE state IN ('active','blocked') ORDER BY id",
            ).fetchall()
        try:
            def _q() -> Any:
                req = urllib.request.Request("http://127.0.0.1:8082/health")
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    return {"health": json.loads(resp.read().decode("utf-8")), "pre_search_hint": "Q up; call Q pre_search before broad scan when old memory may matter"}
            q_hint = await asyncio.to_thread(_q)
        except Exception:
            q_hint = None
        return _json({
            "ok": True,
            "rule_sha": rule_sha,
            "roster": roster,
            "worker_models": worker_models,
            "active_missions": [dict(r) for r in rows],
            "q_pre_search_hint": q_hint,
        })
    except Exception as exc:
        return _err("LEADER_BOOTSTRAP_FAILED", str(exc))
