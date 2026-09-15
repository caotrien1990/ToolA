"""UX51: session identity, explicit title changes and safe replacement."""
import asyncio
import json
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import code_tools as ct
from orchestrator.session import store
from orchestrator.tools.write import draft


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "_SESSIONS_DB", tmp_path / "sessions.db")
    ct._init_session_db()
    monkeypatch.setattr(ct, "_matrix_audit_open_session_summary", lambda *a: {})
    monkeypatch.setattr(ct, "_r43_project_switch_context", lambda *a: {})
    monkeypatch.setattr(draft, "_DRAFTS", {})
    path = tmp_path / "project"
    path.mkdir()
    yield str(path)
    store.close_writer()
    monkeypatch.undo()
    ct._sync_session_db_alias()


def opened(project, title="original", **kwargs):
    return json.loads(asyncio.run(ct.open_session(title, project, agent_id="ux51", **kwargs)))


def rows(project):
    with ct._with_sessions_db() as conn:
        return conn.execute("SELECT session_id, session_title, status, mode, ended_at FROM sessions WHERE project_path=?", (project,)).fetchall()


def test_explicit_folder_does_not_climb_to_parent_marker(project, monkeypatch):
    monkeypatch.setattr(ct, "find_project_root", lambda p: str(Path(project).parent))
    result = opened(project)
    assert result["project_path"] == project
    assert ct._get_active_session_id(project, agent_id="ux51") == result["session_id"]


def test_default_resume_keeps_title_mode_and_one_active(project):
    first = opened(project, mode="T2")
    result = opened(project, "requested", mode="T1")
    assert result["session_id"] == first["session_id"]
    assert result["session_title"] == "original"
    assert result["session_title_mismatch"] is True
    assert result["effective_mode"] == "T2"
    assert len(rows(project)) == 1


@pytest.mark.parametrize("verbosity", ["full", "terse", "ref"])
def test_opt_in_rename_preserves_identity_events_mode(project, verbosity):
    first = opened(project, mode="T2")
    ct._log_session_event(first["session_id"], "read_file_range", {}, {"ok": True}, 1, "ok")
    with ct._with_sessions_db() as conn:
        before = conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (first["session_id"],)).fetchone()[0]
    result = opened(project, "requested", apply_requested_title=True, verbosity=verbosity)
    assert result["session_id"] == first["session_id"]
    assert (result["old_title"], result["new_title"], result["renamed"]) == ("original", "requested", True)
    assert result["effective_mode"] == "T2"
    assert rows(project)[0][1] == "requested"
    with ct._with_sessions_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (first["session_id"],)).fetchone()[0] == before


@pytest.mark.parametrize("verbosity", ["full", "terse", "ref"])
def test_rotation_closes_before_create_and_discloses(project, verbosity):
    first = opened(project)
    result = opened(project, "replacement", force_new_session=True, verbosity=verbosity)
    assert result["closed_session_id"] == first["session_id"]
    assert result["session_id"] != first["session_id"]
    records = rows(project)
    assert sum(row[2] == "active" for row in records) == 1
    assert next(row for row in records if row[0] == first["session_id"])[4]


@pytest.mark.parametrize("kind", ["operation", "audit", "draft"])
def test_rotation_refuses_pending_work_without_closing(project, kind):
    first = opened(project)
    if kind == "operation":
        ct._run_session_write("test operation", lambda c: c.execute("INSERT INTO write_operations(operation_id,tool,status,project_path,session_id,started_at,origin_generation,updated_at) VALUES (?,?,?,?,?,?,?,?)", ("pending", "write_fix", "in_flight", project, first["session_id"], time.time(), "test", time.time())))
    elif kind == "audit":
        ct._run_session_write("test audit", lambda c: c.execute("INSERT INTO pending_audits(pending_audit_id,project_path,session_id,sync_status) VALUES (?,?,?,?)", ("pending", project, first["session_id"], "pending")))
    else:
        draft._DRAFTS["pending"] = {"project_path": project, "expires_at_epoch": time.time()+60}
    result = opened(project, "replacement", force_new_session=True)
    assert result["error_name"] == "SESSION_ROTATION_BUSY"
    assert len(rows(project)) == 1 and rows(project)[0][2] == "active"


def test_rotation_insert_failure_rolls_back_close(project, monkeypatch):
    first = opened(project)
    tail = first["session_id"].rsplit("-", 1)[1]
    monkeypatch.setattr(store, "_session_id_unique_tail", lambda: tail)
    result = opened(project, force_new_session=True)
    assert result["ok"] is False
    assert len(rows(project)) == 1 and rows(project)[0][2] == "active"


def test_concurrent_rotations_leave_exactly_one_active(project):
    opened(project)
    def rotate(i):
        return store._open_or_resume_session(project, str(i), "T1", "ux51", force_new_session=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(rotate, range(12)))
    assert all(created for _, created in results)
    assert len({row["session_id"] for row, _ in results}) == 12
    assert sum(row[2] == "active" for row in rows(project)) == 1


@pytest.mark.parametrize("options,error", [
    ({"force_new_session": True, "apply_requested_title": True}, "CONFLICTING_SESSION_OPTIONS"),
    ({"force_new_session": "false"}, "INVALID_SESSION_OPTIONS"),
    ({"apply_requested_title": 1}, "INVALID_SESSION_OPTIONS"),
])
def test_invalid_options_do_not_mutate(project, options, error):
    first = opened(project)
    result = opened(project, "replacement", **options)
    assert result["error_name"] == error
    assert rows(project)[0][0] == first["session_id"]
    assert len(rows(project)) == 1


def test_schema_exposes_opt_in_false_defaults():
    from orchestrator.core.registry_data import EXTRA_TOOL_SCHEMAS_R43
    schema = next(item["function"] for item in EXTRA_TOOL_SCHEMAS_R43 if item["function"]["name"] == "open_session")
    for name in ("force_new_session", "apply_requested_title"):
        assert schema["parameters"]["properties"][name]["default"] is False
    assert "rename_session" in schema["description"]
    public = next(item["function"] for item in ct.TOOL_SCHEMAS if item["function"]["name"] == "open_session")
    assert "force_new_session" in public["description"]
    assert "preserves title and mode" in public["description"]


def test_other_agent_is_not_closed_or_renamed(project):
    other = store._open_or_resume_session(project, "other", "T2", "other")[0]
    opened(project)
    opened(project, "renamed", apply_requested_title=True)
    opened(project, "replacement", force_new_session=True)
    record = next(row for row in rows(project) if row[0] == other["session_id"])
    assert record[1:4] == ("other", "active", "T2")


def test_omitted_agent_and_blank_title_refused_before_mutation(project):
    opened(project)
    result = json.loads(asyncio.run(ct.open_session("new", project, force_new_session=True)))
    assert result["error_name"] == "AGENT_ID_REQUIRED"
    assert opened(project, " ", apply_requested_title=True)["error_name"] == "SESSION_TITLE_REQUIRED"
    assert len(rows(project)) == 1


def test_explicit_false_flags_keep_default_behavior(project):
    first = opened(project)
    result = opened(project, "new", force_new_session=False, apply_requested_title=False)
    assert result["session_id"] == first["session_id"]
    assert result["session_title"] == "original"
    assert result["session_title_mismatch"] is True


def test_stale_calendar_session_is_closed_before_creation(project):
    first = opened(project)
    ct._run_session_write("age test session", lambda c: c.execute(
        "UPDATE sessions SET started_at=? WHERE session_id=?", ("2000-01-01T00:00:00", first["session_id"])))
    result = opened(project, "today")
    assert result["session_id"] != first["session_id"]
    assert sum(row[2] == "active" for row in rows(project)) == 1


def test_shared_rename_writer_refuses_closed_session(project):
    first = opened(project)
    opened(project, "replacement", force_new_session=True)
    result = ct._run_session_write("rename closed probe", lambda c: store._rename_session_title(c, first["session_id"], "forbidden"))
    assert result is None
    assert next(row for row in rows(project) if row[0] == first["session_id"])[1] == "original"
