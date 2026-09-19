"""CASE-1909 backend fixes: session sweep (H2), fuzzy suggestions (B5),
heartbeat lease guard (B6).

Each test names the mechanism it pins, because all three defects were reported
as behaviour ("sessions vanish", "suggestions are empty", "the task is stuck")
and only the mechanism explains why the obvious reading was wrong.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import code_tools
from orchestrator.mission import store as mission_store
from orchestrator.mission import tools as mission_tools
from orchestrator.session import store as session_store
from orchestrator.tools.read import fs as read_fs


def _decode(payload: str) -> dict:
    return json.loads(payload)


# ---------------------------------------------------------------------------
# CASE-1909-H2 -- the day-rollover sweep is project-wide and agent-blind
# ---------------------------------------------------------------------------


@pytest.fixture
def session_db(tmp_path, monkeypatch):
    session_store.close_writer()
    db_path = tmp_path / "sessions.db"
    monkeypatch.setattr(session_store, "_SESSIONS_DB", db_path)
    session_store._init_session_db()
    yield db_path
    session_store.close_writer()


def _open_session(project: Path, agent_id: str | None) -> str:
    info, _created = session_store._open_or_resume_session(
        str(project), "case1909", "T1", agent_id)
    return info["session_id"]


def _backdate(db_path: Path, session_id: str, days: int) -> str:
    stamp = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE sessions SET started_at=? WHERE session_id=?",
                     (stamp, session_id))
    return stamp


def _add_event(db_path: Path, session_id: str, created_at: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO events (session_id, tool_name, action_type, status, created_at) "
            "VALUES (?,?,?,?,?)",
            (session_id, "read_file_range", "read", "ok", created_at),
        )


def _status(db_path: Path, session_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT status FROM sessions WHERE session_id=?",
                           (session_id,)).fetchone()[0]


def test_session_used_today_survives_the_rollover_sweep(session_db, tmp_path):
    # WHY: the sweep closes OTHER agents' rows too, so the calendar alone must
    # never decide. Activity today means the session is in use, whatever day it
    # was opened on.
    project = tmp_path / "proj"
    project.mkdir()
    sid = _open_session(project, "clau")
    _backdate(session_db, sid, 1)
    _add_event(session_db, sid, datetime.now().isoformat(timespec="seconds"))

    assert session_store._close_stale_active_sessions(str(project)) == 0
    assert _status(session_db, sid) == "active"


def test_session_idle_today_is_still_swept(session_db, tmp_path):
    # Positive control for the guard above: without activity today the sweep MUST
    # still close. Otherwise the test above would also pass on a sweep that never
    # fires at all.
    project = tmp_path / "proj"
    project.mkdir()
    sid = _open_session(project, "clau")
    stamp = _backdate(session_db, sid, 1)
    _add_event(session_db, sid, stamp)

    assert session_store._close_stale_active_sessions(str(project)) == 1
    assert _status(session_db, sid) == "closed"


def test_another_agents_open_does_not_close_a_live_session(session_db, tmp_path):
    # WHY: measured defect -- a third agent opening a session just after midnight
    # closed two other agents' live sessions, all carrying an identical ended_at.
    project = tmp_path / "proj"
    project.mkdir()
    victim = _open_session(project, "clau")
    _backdate(session_db, victim, 1)
    _add_event(session_db, victim, datetime.now().isoformat(timespec="seconds"))

    newcomer = _open_session(project, "codo")

    assert newcomer != victim
    assert _status(session_db, victim) == "active"


def test_anonymous_open_never_adopts_a_scoped_session(session_db, tmp_path):
    # WHY: the unscoped lookup resumed whichever agent's session sorted first and
    # then wrote this caller's events into it. An anonymous open may only ever
    # resume an anonymous session.
    project = tmp_path / "proj"
    project.mkdir()
    scoped = _open_session(project, "clau")

    anonymous = _open_session(project, None)

    assert anonymous != scoped
    assert _status(session_db, scoped) == "active"
    with sqlite3.connect(session_db) as conn:
        assert conn.execute("SELECT agent_id FROM sessions WHERE session_id=?",
                            (anonymous,)).fetchone() == (None,)


def test_anonymous_open_still_resumes_the_anonymous_session(session_db, tmp_path):
    # The other half of the same rule: scoping the lookup must not break resume
    # for the caller that legitimately has no agent_id.
    project = tmp_path / "proj"
    project.mkdir()
    first = _open_session(project, None)

    assert _open_session(project, None) == first


# ---------------------------------------------------------------------------
# CASE-1909-B5 -- a markdown-escaped path, and what the code index cannot hold
# ---------------------------------------------------------------------------

_ESCAPED = "app\\_core\\_helpers\\_util.py"
_REAL = "src/app_core_helpers_util.py"


@pytest.fixture(autouse=True)
def _clear_probe_caches():
    read_fs._NEG_PATH_CACHE.clear()
    read_fs._MISS_STREAK.clear()
    yield
    read_fs._NEG_PATH_CACHE.clear()
    read_fs._MISS_STREAK.clear()


def _miss(file_path: str, project_path: str) -> dict:
    return _decode(read_fs._missing_file_response(
        Path(file_path), file_path, project_path))


def test_markdown_escaped_path_still_matches_the_real_file(monkeypatch, tmp_path):
    # WHY: replace("\\", "/") turns `a\_b.py` into SEPARATORS, so basename()
    # degenerates to the last escaped fragment (`_util.py`) and difflib then runs
    # on rubbish -- it scores 0.5 against the real name and falls under the
    # cutoff. Match the de-escaped whole path as well.
    monkeypatch.setattr(code_tools, "_load_index",
                        lambda _root: {"files": {_REAL: {}}})

    out = _miss(_ESCAPED, str(tmp_path))

    assert out["error_name"] == "FILE_NOT_FOUND"
    assert out["fuzzy_suggestions"] == [_REAL]
    assert out["markdown_escape_detected"] is True
    assert out["unescaped_path_candidate"] == "app_core_helpers_util.py"
    assert out["ca_next_action"] == "retry_with_unescaped_path"


def test_plain_path_carries_no_markdown_escape_disclosure(monkeypatch, tmp_path):
    # Positive control: the escape keys must appear only for an escaped path, or
    # they carry no information.
    monkeypatch.setattr(code_tools, "_load_index",
                        lambda _root: {"files": {_REAL: {}}})

    out = _miss("src/app_core_helpers_utl.py", str(tmp_path))

    assert "markdown_escape_detected" not in out
    assert "unescaped_path_candidate" not in out
    assert out["fuzzy_suggestions"] == [_REAL]


def test_empty_suggestions_disclose_the_code_only_index(monkeypatch, tmp_path):
    # WHY: the index holds CODE only, so a .md/.txt/.log target can never appear
    # in fuzzy_suggestions. An empty list read as "the file is absent" is the
    # measurement trap this key exists to close.
    monkeypatch.setattr(code_tools, "_load_index", lambda _root: {"files": {}})

    out = _miss("docs/notes.md", str(tmp_path))

    assert out["fuzzy_suggestions"] == []
    assert out["fuzzy_suggestions_source"] == "code_index"
    assert "NOT evidence" in out["fuzzy_suggestions_limitation"]


# ---------------------------------------------------------------------------
# CASE-1909-B6 -- a heartbeat refreshes a lease, it cannot create one
# ---------------------------------------------------------------------------


async def _async_result(value):
    return value


@pytest.fixture
def mission_env(tmp_path, monkeypatch):
    db = tmp_path / "missions.db"
    mission_store.init_db(db)
    monkeypatch.setattr(mission_tools, "MISSION_DB_PATH", db)
    monkeypatch.setattr(mission_tools.instance_lock, "is_lock_holder", lambda: True)
    mission_tools.configure_runtime(
        model_call=lambda **_: _async_result(
            {"ok": True, "current": {"provider": "mock", "model": "mock-model"}}),
        send_call=lambda **_: _async_result(
            {"ok": True, "dispatched": True, "child_pid": 4321}),
    )
    return db


def _create_task(mission_env) -> int:
    tasks_json = json.dumps([{
        "title": "case1909",
        "agent_hint": "ope",
        "output_artifact_path": "out/case1909.md",
        "verify_gate_json": {"type": "file_exists"},
    }])
    created = _decode(asyncio.run(mission_tools.mission_create(
        "case1909", tasks_json, created_by="pytest")))
    assert created["ok"] is True
    return created["task_ids"][0]


def test_heartbeat_on_an_undispatched_task_is_refused(mission_env):
    # WHY: a heartbeat re-writes the state unchanged, so accepting one for a
    # pending task wrote a lease onto a row the reaper never scans (it selects
    # dispatching/started/verifying only). The task then sat pending forever and
    # read exactly like a stuck state machine.
    task_id = _create_task(mission_env)

    out = _decode(asyncio.run(mission_tools.mission_report(
        task_id, None, kind="heartbeat", summary="alive")))

    assert out["ok"] is False
    assert out["error_name"] == "HEARTBEAT_NOT_LEASED"
    assert out["state"] == "pending"
    assert out["lease_bearing_states"] == ["dispatching", "started", "verifying"]
    assert out["ca_next_action"] == "call_mission_dispatch_first"
    task = mission_store.get_task(mission_env, task_id)
    assert task["state"] == "pending"
    assert not task["lease_expires_at"]


def test_heartbeat_refreshes_the_lease_without_bumping_the_version(mission_env):
    # WHY: version is the optimistic lock a concurrent cas_transition holds. A
    # keep-alive that bumps it makes every in-flight dispatch lose its CAS.
    task_id = _create_task(mission_env)
    assert _decode(asyncio.run(mission_tools.mission_dispatch(task_id)))["ok"] is True
    before = mission_store.get_task(mission_env, task_id)
    assert before["state"] == "started"

    out = _decode(asyncio.run(mission_tools.mission_report(
        task_id, None, kind="heartbeat", summary="alive")))

    assert out["ok"] is True
    after = mission_store.get_task(mission_env, task_id)
    assert after["version"] == before["version"]
    assert float(after["lease_expires_at"]) >= float(before["lease_expires_at"])
