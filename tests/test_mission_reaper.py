from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.mission import engine, reaper, store
from orchestrator.mission import tools as mission_tools


def _decode(payload: str) -> dict:
    return json.loads(payload)


def _run(coro):
    return asyncio.run(coro)


async def _async_result(value):
    return value


def _db(tmp_path, monkeypatch):
    db = tmp_path / "missions.db"
    store.init_db(db)
    monkeypatch.setattr(reaper.instance_lock, "is_lock_holder", lambda: True)
    monkeypatch.setattr(mission_tools, "MISSION_DB_PATH", db)
    monkeypatch.setattr(mission_tools.instance_lock, "is_lock_holder", lambda: True)
    mission_tools.configure_runtime(
        model_call=lambda **_: _async_result({"ok": True, "current": {"provider": "mock", "model": "mock"}}),
        send_call=lambda **_: _async_result({"ok": True, "child_pid": 9876}),
    )
    return db


def _old_ledger(db, task_id, kind, seconds=1000):
    old = time.time() - seconds
    old_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(old))
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "UPDATE progress_ledger SET ts = ? WHERE task_id = ? AND kind = ?",
            (old_iso, task_id, kind),
        )
        conn.commit()
    return old


def _task(db, state="dispatching", **fields):
    mid = store.create_mission(db, "m")
    tid = store.create_task(
        db,
        mid,
        output_artifact_path=fields.pop("output_artifact_path", str(Path(db).parent / f"{state}.md")),
        state=state,
        **fields,
    )["task_id"]
    store.append_ledger(
        db,
        mid,
        tid,
        "dispatch_intent",
        json.dumps({"request_id": f"mission-{tid}-a{fields.get('attempt', 0)}-1"}),
    )
    return mid, tid


def test_reaper_requeues_stuck_dispatch_once(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    _, tid = _task(db, state="dispatching", max_attempts=3)
    _old_ledger(db, tid, "dispatch_intent", 200)
    result = reaper.scan_once(db, now=time.time())
    task = store.get_task(db, tid)
    assert result["actions"][0]["action"] == "requeue"
    assert task["state"] == "pending"
    assert task["attempt"] == 1


def test_reaper_kills_then_requeues_pure_artifact(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    _, tid = _task(db, state="dispatching", child_pid=1234)
    _old_ledger(db, tid, "dispatch_intent", 200)
    monkeypatch.setattr(reaper.psutil, "pid_exists", lambda pid: pid == 1234)
    killed = []
    result = reaper.scan_once(db, now=time.time(), kill_process_tree=lambda pid: killed.append(pid))
    assert result["actions"][0]["action"] == "kill_then_requeue"
    assert killed == [1234]
    assert store.get_task(db, tid)["state"] == "pending"


def test_reaper_blocks_mutating_or_destructive_task(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    _, tid = _task(db, state="started", side_effect_class="destructive", lease_expires_at=time.time() - 1, hard_cap_s=1)
    _old_ledger(db, tid, "dispatch_intent", 5)
    result = reaper.scan_once(db, now=time.time() + 5)
    assert result["actions"][0]["new_state"] == "blocked"
    assert store.get_task(db, tid)["state"] == "blocked"


def test_reaper_max_attempts_goes_failed(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    _, tid = _task(db, state="dispatching", attempt=2, max_attempts=2)
    _old_ledger(db, tid, "dispatch_intent", 200)
    result = reaper.scan_once(db, now=time.time())
    assert result["actions"][0]["new_state"] == "failed"
    assert store.get_task(db, tid)["state"] == "failed"


def test_reaper_verify_pass_marks_done(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    artifact = tmp_path / "done.md"
    artifact.write_text("x\nEND_OK\n", encoding="utf-8")
    _, tid = _task(
        db,
        state="verifying",
        output_artifact_path=str(artifact),
        verify_gate_json=json.dumps({"type": "artifact_complete", "end_marker": "END_OK"}),
        lease_expires_at=time.time() - 1,
    )
    result = reaper.scan_once(db, now=time.time())
    assert result["actions"][0]["action"] == "requeue_verify"
    assert store.get_task(db, tid)["state"] == "done"


def test_reaper_verify_fail_extends_lease_without_dispatch(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    _, tid = _task(
        db,
        state="verifying",
        verify_gate_json=json.dumps({"type": "artifact_complete", "end_marker": "END_OK"}),
        lease_expires_at=time.time() - 1,
        hard_cap_s=10,
    )
    result = reaper.scan_once(db, now=time.time())
    task = store.get_task(db, tid)
    assert result["actions"][0]["new_state"] == "verifying"
    assert task["state"] == "verifying"
    assert task["lease_expires_at"] > time.time()


def test_reaper_scan_skips_without_lock(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    monkeypatch.setattr(reaper.instance_lock, "is_lock_holder", lambda: False)
    assert reaper.scan_once(db)["skipped"] == "not_lock_holder"


def test_start_reaper_task_initializes_database_before_loop(tmp_path, monkeypatch):
    db = tmp_path / "fresh" / "missions.db"
    monkeypatch.setattr(reaper.instance_lock, "is_lock_holder", lambda: False)

    async def exercise():
        task = reaper.start_reaper_task(db, interval_sec=3600)
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _run(exercise())

    with sqlite3.connect(str(db)) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"missions", "tasks", "progress_ledger", "dispatch_outbox"} <= tables


def test_ac_m6_fault_after_outbox_before_send_requeues_once(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    artifact = tmp_path / "one.md"
    mid = store.create_mission(db, "m")
    tid = store.create_task(db, mid, output_artifact_path=str(artifact))["task_id"]
    monkeypatch.setenv("ORCHESTRATOR_TEST_FAULT", "after_outbox_before_send")
    fault = _decode(_run(mission_tools.mission_dispatch(tid)))
    assert fault["error_name"] == "MISSION_DISPATCH_FAILED"
    assert store.get_task(db, tid)["state"] == "dispatching"
    _old_ledger(db, tid, "dispatch_intent", 200)
    monkeypatch.delenv("ORCHESTRATOR_TEST_FAULT", raising=False)
    result = reaper.scan_once(db, now=time.time())
    assert result["actions"][0]["new_state"] == "pending"
    assert store.get_task(db, tid)["attempt"] == 1
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM dispatch_outbox WHERE task_id=?", (tid,)).fetchone()[0] == 1


def test_ac_m7_partial_artifact_resume_and_destructive_blocks(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    artifact = tmp_path / "partial.md"
    artifact.write_text("partial\nEND_OK\n", encoding="utf-8")
    mid = store.create_mission(db, "m")
    tid = store.create_task(
        db,
        mid,
        output_artifact_path=str(artifact),
        verify_gate_json=json.dumps({"type": "artifact_complete", "end_marker": "END_OK"}),
    )["task_id"]
    monkeypatch.setenv("ORCHESTRATOR_TEST_FAULT", "mid_partial_artifact")
    fault = _decode(_run(mission_tools.mission_report(tid, engine.make_attempt_token(tid, 0))))
    assert fault["error_name"] == "MISSION_REPORT_FAILED"
    monkeypatch.delenv("ORCHESTRATOR_TEST_FAULT", raising=False)
    resumed = _decode(_run(mission_tools.mission_report(tid, engine.make_attempt_token(tid, 0))))
    assert resumed["state"] == "done"

    _, destructive = _task(db, state="started", side_effect_class="destructive", lease_expires_at=time.time() - 1, hard_cap_s=1)
    _old_ledger(db, destructive, "dispatch_intent", 5)
    reaper.scan_once(db, now=time.time() + 5)
    assert store.get_task(db, destructive)["state"] == "blocked"
