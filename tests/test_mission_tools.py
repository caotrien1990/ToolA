from __future__ import annotations

import json
import sqlite3
import sys
import asyncio
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.mission import engine
from orchestrator.mission import tools as mission_tools
from orchestrator.mission import store


def _decode(payload: str) -> dict:
    return json.loads(payload)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def mission_env(tmp_path, monkeypatch):
    db = tmp_path / "missions.db"
    store.init_db(db)
    monkeypatch.setattr(mission_tools, "MISSION_DB_PATH", db)
    monkeypatch.setattr(mission_tools.instance_lock, "is_lock_holder", lambda: True)
    mission_tools.configure_runtime(
        model_call=lambda **_: _async_result({"ok": True, "current": {"provider": "mock", "model": "mock-model"}}),
        send_call=lambda **_: _async_result({"ok": True, "dispatched": True, "child_pid": 4321}),
    )
    return db


async def _async_result(value):
    return value


def test_tools_crud_dispatch_report_status_gate_flow(mission_env):
    tasks_json = json.dumps([
        {
            "title": "first",
            "agent_hint": "ope",
            "output_artifact_path": "out/first.md",
            "verify_gate_json": {"type": "file_exists"},
        }
    ])
    created = _decode(_run(mission_tools.mission_create("mission", tasks_json, created_by="pytest")))
    assert created["ok"] is True
    mission_id = created["mission_id"]
    task_id = created["task_ids"][0]

    nxt = _decode(_run(mission_tools.mission_next(mission_id)))
    assert nxt["task"]["id"] == task_id

    dispatched = _decode(_run(mission_tools.mission_dispatch(task_id)))
    assert dispatched["ok"] is True
    assert dispatched["child_pid"] == 4321
    assert store.get_task(mission_env, task_id)["state"] == "started"

    heartbeat = _decode(_run(mission_tools.mission_report(
        task_id,
        engine.make_attempt_token(task_id, 0),
        kind="heartbeat",
        summary="alive",
    )))
    assert heartbeat["ok"] is True
    assert heartbeat["kind"] == "heartbeat"

    artifact = mission_env.parent / "first.md"
    artifact.write_text("done", encoding="utf-8")
    final = _decode(_run(mission_tools.mission_report(
        task_id,
        engine.make_attempt_token(task_id, 0),
        res_path=str(artifact),
        summary="done",
    )))
    assert final["ok"] is True
    assert final["state"] == "done"

    status = _decode(_run(mission_tools.mission_status(mission_id, verbosity="full")))
    assert status["ok"] is True
    assert status["tasks"][0]["state"] == "done"
    assert any(row["kind"] == "final_done" for row in status["ledger_tail"])

    gated = store.create_task(mission_env, mission_id, title="gate", output_artifact_path="out/gate.md")["task_id"]
    with sqlite3.connect(str(mission_env)) as conn:
        conn.execute("UPDATE tasks SET state = 'failed' WHERE id = ?", (gated,))
        conn.commit()
    approved = _decode(_run(mission_tools.mission_gate(gated, "approve", "ok")))
    assert approved["ok"] is True
    assert approved["state"] == "pending"


def test_worker_model_not_set_errors_without_hint_and_autosets_with_hint(mission_env):
    calls: list[dict] = []

    async def model_call(**kwargs):
        calls.append(kwargs)
        if kwargs.get("action") == "set":
            return {"ok": True, "set": {"provider": "mock", "model": kwargs["model"]}}
        if any(call.get("action") == "set" for call in calls):
            return {"ok": True, "current": {"provider": "mock", "model": "auto-model"}}
        return {"ok": True, "current": {}}

    mission_tools.configure_runtime(model_call=model_call, send_call=lambda **_: _async_result({"ok": True}))
    mid = store.create_mission(mission_env, "m")
    no_hint = store.create_task(mission_env, mid, agent_hint="ope", output_artifact_path="out/no.md")["task_id"]
    error = _decode(_run(mission_tools.mission_dispatch(no_hint)))
    assert error["error_name"] == "WORKER_MODEL_NOT_SET"

    with_hint = store.create_task(
        mission_env,
        mid,
        title="with hint",
        agent_hint="ope",
        model_hint="auto-model",
        output_artifact_path="out/with.md",
    )["task_id"]
    ok = _decode(_run(mission_tools.mission_dispatch(with_hint)))
    assert ok["ok"] is True
    assert any(call.get("action") == "set" and call.get("model") == "auto-model" for call in calls)


def test_stale_attempt_report_is_rejected(mission_env):
    mid = store.create_mission(mission_env, "m")
    task_id = store.create_task(mission_env, mid, output_artifact_path="out/t.md", attempt=1)["task_id"]
    result = _decode(_run(mission_tools.mission_report(
        task_id,
        engine.make_attempt_token(task_id, 0),
        summary="old",
    )))
    assert result["error_name"] == "STALE_ATTEMPT"

    status = _decode(_run(mission_tools.mission_status(mid, verbosity="full")))
    assert any(row["kind"] == "stale_attempt_rejected" for row in status["ledger_tail"])


def test_artifact_first_completes_without_res_summary(mission_env):
    artifact = mission_env.parent / "artifact.md"
    mid = store.create_mission(mission_env, "m")
    task_id = store.create_task(
        mission_env,
        mid,
        output_artifact_path=str(artifact),
        verify_gate_json=json.dumps({"type": "artifact_complete", "end_marker": "END_OK"}),
    )["task_id"]
    artifact.write_text("work\nEND_OK\n", encoding="utf-8")
    result = _decode(_run(mission_tools.mission_report(
        task_id,
        engine.make_attempt_token(task_id, 0),
    )))
    assert result["ok"] is True
    assert result["state"] == "done"
    assert store.get_task(mission_env, task_id)["state"] == "done"


def test_tools_refuse_when_not_lock_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(mission_tools, "MISSION_DB_PATH", tmp_path / "missions.db")
    monkeypatch.setattr(mission_tools.instance_lock, "is_lock_holder", lambda: False)
    result = _decode(_run(mission_tools.mission_status(1)))
    assert result["error_name"] == "NOT_LOCK_HOLDER"


def test_ac_m1_resume_from_status_and_next_only(mission_env):
    tasks_json = json.dumps([
        {"title": "one", "output_artifact_path": "out/one.md"},
        {"title": "two", "output_artifact_path": "out/two.md", "depends_on": []},
        {"title": "three", "output_artifact_path": "out/three.md", "depends_on": []},
    ])
    created = _decode(_run(mission_tools.mission_create("resume", tasks_json)))
    mid = created["mission_id"]
    for tid in created["task_ids"]:
        status = _decode(_run(mission_tools.mission_status(mid)))
        assert any(t["id"] == tid for t in status["tasks"])
        nxt = _decode(_run(mission_tools.mission_next(mid)))
        assert nxt["task"]["id"] == tid
        done = _decode(_run(mission_tools.mission_report(
            tid,
            engine.make_attempt_token(tid, 0),
            summary=f"done {tid}",
        )))
        assert done["state"] == "done"
    final = _decode(_run(mission_tools.mission_status(mid)))
    assert {t["state"] for t in final["tasks"]} == {"done"}


def test_ac_m2a_synthetic_gate_and_retry_flow(mission_env):
    artifacts = [mission_env.parent / f"t{i}.md" for i in range(5)]
    tasks = [
        {"title": "a", "agent_hint": "ope", "output_artifact_path": str(artifacts[0])},
        {"title": "b", "agent_hint": "herm", "output_artifact_path": str(artifacts[1])},
        {"title": "gate", "agent_hint": "ope", "output_artifact_path": str(artifacts[2]), "side_effect_class": "mutating"},
        {
            "title": "retry",
            "agent_hint": "herm",
            "output_artifact_path": str(artifacts[3]),
            "verify_gate_json": {"type": "artifact_complete", "end_marker": "END_RETRY"},
            "max_attempts": 2,
        },
        {"title": "tail", "agent_hint": "ope", "output_artifact_path": str(artifacts[4]), "depends_on": []},
    ]
    created = _decode(_run(mission_tools.mission_create("synthetic", json.dumps(tasks))))
    mid = created["mission_id"]
    t0, t1, gated, retry, tail = created["task_ids"]

    assert _decode(_run(mission_tools.mission_dispatch(t0)))["ok"] is True
    assert _decode(_run(mission_tools.mission_dispatch(t1)))["ok"] is True
    assert _decode(_run(mission_tools.mission_report(t0, engine.make_attempt_token(t0, 0), summary="ok")))["state"] == "done"
    assert _decode(_run(mission_tools.mission_report(t1, engine.make_attempt_token(t1, 0), summary="ok")))["state"] == "done"

    with sqlite3.connect(str(mission_env)) as conn:
        conn.execute("UPDATE tasks SET state='blocked' WHERE id=?", (gated,))
        conn.commit()
    assert _decode(_run(mission_tools.mission_gate(gated, "approve", "ok")))["state"] == "pending"
    assert _decode(_run(mission_tools.mission_report(gated, engine.make_attempt_token(gated, 0), summary="approved")))["state"] == "done"

    fail = _decode(_run(mission_tools.mission_report(retry, engine.make_attempt_token(retry, 0), summary="missing marker")))
    assert fail["state"] == "pending"
    artifacts[3].write_text("continued\nEND_RETRY\n", encoding="utf-8")
    assert _decode(_run(mission_tools.mission_report(retry, engine.make_attempt_token(retry, 1))))["state"] == "done"
    assert _decode(_run(mission_tools.mission_report(tail, engine.make_attempt_token(tail, 0), summary="tail")))["state"] == "done"

    status = _decode(_run(mission_tools.mission_status(mid)))
    assert {task["state"] for task in status["tasks"]} == {"done"}


def _outbox_count(db_path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM dispatch_outbox").fetchone()[0])


def test_destructive_dispatch_requires_human_gate_for_current_attempt(mission_env):
    mid = store.create_mission(mission_env, "destructive gate")
    task_id = store.create_task(
        mission_env,
        mid,
        output_artifact_path="out/destructive.md",
        side_effect_class="destructive",
    )["task_id"]
    before = _outbox_count(mission_env)

    blocked = _decode(_run(mission_tools.mission_dispatch(task_id)))

    assert blocked["ok"] is False
    assert blocked["error_name"] == "NEEDS_HUMAN_GATE"
    assert blocked["task_id"] == task_id
    assert store.get_task(mission_env, task_id)["state"] == "blocked"
    assert _outbox_count(mission_env) == before
    status = _decode(_run(mission_tools.mission_status(mid, verbosity="full")))
    assert any(row["kind"] == "human_gate_needed" for row in status["ledger_tail"])

    approved = _decode(_run(mission_tools.mission_gate(task_id, "approve", "ok")))
    assert approved["ok"] is True
    assert approved["state"] == "pending"
    approved_task = store.get_task(mission_env, task_id)
    dispatched = _decode(_run(mission_tools.mission_dispatch(task_id)))

    assert dispatched["ok"] is True
    assert dispatched["outbox_id"]
    assert _outbox_count(mission_env) == before + 1
    assert store.get_task(mission_env, task_id)["state"] == "started"
    status = _decode(_run(mission_tools.mission_status(mid, verbosity="full")))
    gate_rows = [row for row in status["ledger_tail"] if row["kind"] == "human_gate"]
    assert any(
        json.loads(row["detail_json"])["decision"] == "approve"
        and json.loads(row["detail_json"])["attempt"] == approved_task["attempt"]
        for row in gate_rows
    )


def test_non_destructive_dispatch_does_not_require_human_gate(mission_env):
    mid = store.create_mission(mission_env, "non destructive")
    pure_id = store.create_task(
        mission_env,
        mid,
        output_artifact_path="out/pure.md",
        side_effect_class="pure_artifact",
    )["task_id"]
    mutating_id = store.create_task(
        mission_env,
        mid,
        output_artifact_path="out/mutating.md",
        side_effect_class="mutating",
    )["task_id"]

    assert _decode(_run(mission_tools.mission_dispatch(pure_id)))["ok"] is True
    assert _decode(_run(mission_tools.mission_dispatch(mutating_id)))["ok"] is True
    assert store.get_task(mission_env, pure_id)["state"] == "started"
    assert store.get_task(mission_env, mutating_id)["state"] == "started"
    assert _outbox_count(mission_env) == 2


def test_human_gate_reject_keeps_skipped_behavior(mission_env):
    mid = store.create_mission(mission_env, "reject")
    task_id = store.create_task(
        mission_env,
        mid,
        output_artifact_path="out/reject.md",
        side_effect_class="destructive",
    )["task_id"]

    assert _decode(_run(mission_tools.mission_dispatch(task_id)))["error_name"] == "NEEDS_HUMAN_GATE"
    rejected = _decode(_run(mission_tools.mission_gate(task_id, "reject", "no")))

    assert rejected["ok"] is True
    assert rejected["state"] == "skipped"
    assert store.get_task(mission_env, task_id)["state"] == "skipped"
