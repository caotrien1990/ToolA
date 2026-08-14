"""Mission reaper for stuck dispatch/start/verify states (P3c).

The async loop is intentionally thin; ``scan_once`` holds the recoverability
logic so tests can exercise all actions without starting the server.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import psutil

from . import engine, instance_lock, store

DEFAULT_INTERVAL_SEC = 60
DISPATCH_GRACE_SEC = 120

KillProcessTree = Callable[[int], None]


@contextmanager
def _connect(db_path: store.PathLike):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        conn.close()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _parse_ts(value: str | None) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _hard_cap(task: dict[str, Any]) -> float:
    if task.get("hard_cap_s") is not None:
        return float(task["hard_cap_s"])
    timeout = float(task.get("timeout_sec") or 0)
    if timeout <= 0:
        timeout = 6000.0
    return max(1800.0, min(timeout * 4.0, 14400.0))


def _task_ledger(conn: sqlite3.Connection, task_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, mission_id, task_id, ts, kind, detail_json
        FROM progress_ledger
        WHERE task_id = ?
        ORDER BY id
        """,
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _latest_for_attempt(rows: list[dict[str, Any]], kind: str, attempt: int) -> Optional[dict[str, Any]]:
    marker = f"-a{attempt}-"
    fallback = None
    for row in rows:
        if row.get("kind") != kind:
            continue
        fallback = row
        detail = _parse_json(row.get("detail_json"), {})
        request_id = str(detail.get("request_id") or "")
        if marker in request_id:
            return row
    return fallback


def _latest_heartbeat_ts(rows: list[dict[str, Any]]) -> Optional[float]:
    ts = None
    for row in rows:
        if row.get("kind") == "heartbeat":
            ts = _parse_ts(row.get("ts"))
    return ts


def _latest_outbox(conn: sqlite3.Connection, task_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        """
        SELECT * FROM dispatch_outbox
        WHERE task_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _child_alive(pid: Any) -> Optional[bool]:
    if pid in (None, ""):
        return None
    try:
        return bool(psutil.pid_exists(int(pid)))
    except (TypeError, ValueError):
        return None


def _artifact_stale(task: dict[str, Any], now: float, reference_ts: Optional[float]) -> bool:
    hard_cap = _hard_cap(task)
    path = str(task.get("output_artifact_path") or "")
    p = Path(path)
    if p.exists():
        try:
            return (now - p.stat().st_mtime) > hard_cap
        except OSError:
            return False
    if reference_ts is None:
        return False
    return (now - reference_ts) > hard_cap


def _default_kill_process_tree(pid: int) -> None:
    try:
        from orchestrator_server import _council_kill_process_tree

        _council_kill_process_tree(pid)
    except Exception:
        pass


def _block_or_fail(db_path: store.PathLike, task: dict[str, Any], reason: str) -> str:
    task_id = int(task["id"])
    mission_id = int(task["mission_id"])
    state = str(task["state"])
    version = int(task["version"])
    side_effect = str(task.get("side_effect_class") or "pure_artifact")
    retry_policy = _parse_json(task.get("retry_policy_json"), {})
    next_attempt = int(task.get("attempt") or 0) + 1
    max_attempts = int(task.get("max_attempts") or 1)

    if side_effect in {"mutating", "destructive"} or retry_policy.get("non_retryable"):
        ok = store.cas_transition(db_path, task_id, state, "blocked", version)
        kind = "human_gate_needed"
        new_state = "blocked"
    elif next_attempt > max_attempts:
        ok = store.cas_transition(
            db_path, task_id, state, "failed", version, finished_at=datetime.now(timezone.utc).isoformat()
        )
        kind = "reaper_failed"
        new_state = "failed"
    else:
        ok = store.cas_transition(
            db_path,
            task_id,
            state,
            "pending",
            version,
            attempt=next_attempt,
            lease_expires_at=None,
            child_pid=None,
        )
        kind = "requeue_by_reaper"
        new_state = "pending"

    store.append_ledger(
        db_path,
        mission_id,
        task_id,
        kind if ok else "reaper_cas_conflict",
        _dump({"reason": reason, "from_state": state, "to_state": new_state if ok else state}),
    )
    return new_state if ok else "cas_conflict"


def _requeue_verify(db_path: store.PathLike, task: dict[str, Any], now: float) -> str:
    task_id = int(task["id"])
    mission_id = int(task["mission_id"])
    gate = _parse_json(task.get("verify_gate_json"), {})
    gate_type = str((gate or {}).get("type") or "").strip()
    output_path = str(task.get("output_artifact_path") or "")
    passed = False
    if gate_type == "file_exists":
        passed = Path(output_path).exists()
    elif gate_type in {"artifact_complete", "artifact-complete"}:
        end_marker = str((gate or {}).get("end_marker") or f"END_TASK_{task_id}_A{task.get('attempt', 0)}")
        passed = engine.artifact_complete(output_path, end_marker)

    if passed:
        ok = store.cas_transition(
            db_path,
            task_id,
            "verifying",
            "done",
            int(task["version"]),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        store.append_ledger(
            db_path,
            mission_id,
            task_id,
            "requeue_verify_done" if ok else "reaper_cas_conflict",
            _dump({"gate_type": gate_type}),
        )
        return "done" if ok else "cas_conflict"

    lease = now + _hard_cap(task)
    ok = store.cas_transition(
        db_path,
        task_id,
        "verifying",
        "verifying",
        int(task["version"]),
        lease_expires_at=lease,
    )
    store.append_ledger(
        db_path,
        mission_id,
        task_id,
        "requeue_verify" if ok else "reaper_cas_conflict",
        _dump({"gate_type": gate_type, "lease_expires_at": lease}),
    )
    return "verifying" if ok else "cas_conflict"


def scan_once(
    db_path: store.PathLike,
    *,
    now: Optional[float] = None,
    kill_process_tree: Optional[KillProcessTree] = None,
) -> dict[str, Any]:
    """Scan active task states once and apply reaper transitions."""
    if not instance_lock.is_lock_holder():
        return {"ok": True, "skipped": "not_lock_holder", "actions": []}
    now = time.time() if now is None else float(now)
    actions: list[dict[str, Any]] = []
    kill_process_tree = kill_process_tree or _default_kill_process_tree

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE state IN ('dispatching','started','verifying')
            ORDER BY id
            """
        ).fetchall()
        tasks = [dict(r) for r in rows]

    for task in tasks:
        task_id = int(task["id"])
        attempt = int(task.get("attempt") or 0)
        with _connect(db_path) as conn:
            ledger = _task_ledger(conn, task_id)
            outbox = _latest_outbox(conn, task_id)

        intent = _latest_for_attempt(ledger, "dispatch_intent", attempt)
        started = _latest_for_attempt(ledger, "dispatch_started", attempt)
        intent_ts = _parse_ts(intent.get("ts") if intent else None)
        hb_ts = _latest_heartbeat_ts(ledger)
        hard_cap = _hard_cap(task)
        lease = task.get("lease_expires_at")
        child_pid = task.get("child_pid") or (outbox or {}).get("child_pid")
        child_alive = _child_alive(child_pid)
        signals = {
            "dispatch_started": started is not None,
            "grace_expired": bool(intent_ts is not None and (now - intent_ts) > DISPATCH_GRACE_SEC),
            "lease_expired": bool(lease is not None and now > float(lease)),
            "artifact_stale": _artifact_stale(task, now, intent_ts or _parse_ts(task.get("started_at"))),
            "heartbeat_recent": bool(hb_ts is not None and (now - hb_ts) <= (hard_cap / 3.0)),
            "child_pid_alive": child_alive,
        }
        action = engine.classify_for_reaper(
            state=str(task["state"]),
            side_effect_class=str(task.get("side_effect_class") or "pure_artifact"),
            **signals,
        )
        if action == "none":
            continue
        if action == "kill_then_requeue" and child_alive and child_pid is not None:
            kill_process_tree(int(child_pid))
            new_state = _block_or_fail(db_path, task, action)
        elif action in {"requeue", "blocked_gate"}:
            new_state = _block_or_fail(db_path, task, action)
        elif action == "requeue_verify":
            new_state = _requeue_verify(db_path, task, now)
        else:
            new_state = "unknown"
        actions.append({"task_id": task_id, "action": action, "new_state": new_state, "signals": signals})

    return {"ok": True, "actions": actions}


async def reaper_loop(
    db_path: store.PathLike,
    *,
    interval_sec: float = DEFAULT_INTERVAL_SEC,
    kill_process_tree: Optional[KillProcessTree] = None,
) -> None:
    import logging
    _log = logging.getLogger(__name__)
    while True:
        try:
            if instance_lock.is_lock_holder():
                scan_once(db_path, kill_process_tree=kill_process_tree)
        except Exception:
            # fail-loud: a silently dying reaper defeats crash-recovery (G2/G3)
            _log.warning("mission reaper scan_once failed", exc_info=True)
        await asyncio.sleep(interval_sec)


def start_reaper_task(
    db_path: store.PathLike,
    *,
    interval_sec: float = DEFAULT_INTERVAL_SEC,
    kill_process_tree: Optional[KillProcessTree] = None,
) -> asyncio.Task:
    store.init_db(db_path)
    return asyncio.create_task(
        reaper_loop(db_path, interval_sec=interval_sec, kill_process_tree=kill_process_tree),
        name="mission-reaper",
    )
