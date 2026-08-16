"""A40: every durable-write tool must report SessionStoreContentionError as
retryable SESSION_STORE_CONTENTION, not as a permanent INTERNAL_ERROR.

WHY this file exists
--------------------
``SessionWriter.submit(durable=True)`` raises ``SessionStoreContentionError``
for EVERY durable write once the write-queue watchdog marks the writer STALLED
(5s) or WEDGED (30s), or the queue budget is exceeded. That is a transient
condition: a plain retry once the queue drains actually commits.

Before A40 the error was translated to a retryable contract in EXACTLY ONE
place -- close_session's dedicated ``except SessionStoreContentionError``.
Every other durable-write tool funnelled the same exception through a generic
``except Exception: return _json_error(str(e))`` (code_tools) or through the
server dispatcher's generic ``except Exception`` (tools_v15 tools). Both drop
the exception type and default to ``retryable=False`` / INTERNAL_ERROR, telling
CA a retryable stall is a permanent internal error. CA gives up and the durable
write is silently lost.

The fix is centralized in ONE classifier, ``code_tools._session_contention_fields``:
  * ``code_tools._json_error`` calls it when handed the exception object, so
    every ``except Exception as e: return _json_error(e)`` tool inherits the
    retryable contract (funnel 1).
  * ``orchestrator_server._session_contention_error_payload`` calls the SAME
    classifier for exceptions that reach the dispatcher's generic handler
    (funnel 2 -- record_error_resolution, record_session_audit_checkpoint).

These tests pin the contract for the FULL set of affected tools, and pin that
the translation stays narrow (a genuine defect must not inherit retryable).

Affected tools traced by following every ``submit(durable=True)`` caller
(store._run_session_write / store._log_session_event) up to its owning tool:

  code_tools funnel (except Exception -> _json_error):
    open_session, close_session, rename_session, log_native_event,
    create_pending_audit, sync_pending_audit, clear_closed_sessions
  server-dispatcher funnel (exception propagates uncaught):
    record_error_resolution, record_session_audit_checkpoint
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import code_tools
import orchestrator_server


_Contention = code_tools._session_store_module.SessionStoreContentionError


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh sessions DB + a real on-disk project dir per test."""
    db_path = tmp_path / "sessions_a40.db"
    monkeypatch.setattr(code_tools, "_SESSIONS_DB", db_path)
    code_tools._sync_session_db_alias()
    code_tools._init_session_db()
    project_dir = tmp_path / "proj_a40"
    project_dir.mkdir()
    yield db_path, str(project_dir)
    monkeypatch.undo()
    code_tools._sync_session_db_alias()


def _open(project_path: str, agent_id: str = "a40-probe") -> None:
    raw = asyncio.run(
        code_tools.open_session(project_path=project_path, agent_id=agent_id)
    )
    assert json.loads(raw).get("ok"), f"setup failed to open a session: {raw[:300]}"


def _boom_contention(*_args, **_kwargs):
    raise _Contention("simulated writer contention")


# ── funnel 1 unit: the shared classifier + _json_error integration ───────────

def test_classifier_returns_none_for_non_contention():
    """The classifier must not widen: only a SessionStoreContentionError maps."""
    assert code_tools._session_contention_fields(RuntimeError("nope")) is None
    assert code_tools._session_contention_fields(ValueError("nope")) is None


def test_classifier_maps_contention_to_retryable_contract():
    fields = code_tools._session_contention_fields(_Contention("stalled"))
    assert fields is not None
    assert fields["error_name"] == "SESSION_STORE_CONTENTION"
    assert fields["retryable"] is True
    assert fields["ca_next_action"]  # tool-agnostic, non-empty


def test_json_error_translates_exception_object():
    result = json.loads(code_tools._json_error(_Contention("stalled")))
    assert result["error_name"] == "SESSION_STORE_CONTENTION"
    assert result["retryable"] is True
    assert result["phase"] == "retryable_transport"


def test_json_error_string_path_unchanged():
    """Passing a plain string keeps the legacy non-retryable default -- the
    change must be a no-op for every existing non-exception caller."""
    result = json.loads(code_tools._json_error("some other failure"))
    assert result["error_name"] == "INTERNAL_ERROR"
    assert result["retryable"] is False


def test_json_error_non_contention_exception_stays_non_retryable():
    result = json.loads(code_tools._json_error(RuntimeError("genuine defect")))
    assert result["error_name"] == "INTERNAL_ERROR"
    assert result["retryable"] is False


def test_json_error_explicit_fields_win_over_classifier():
    """A caller that supplies error_name/retryable keeps them (setdefault)."""
    result = json.loads(code_tools._json_error(
        _Contention("stalled"),
        error_name="CUSTOM", retryable=False))
    assert result["error_name"] == "CUSTOM"
    assert result["retryable"] is False


# ── funnel 1 integration: each affected code_tools tool ──────────────────────

def _assert_retryable_contention(result: dict, tool: str) -> None:
    assert not result.get("ok"), f"{tool}: contention must fail loudly; got {result}"
    assert result.get("error_name") == "SESSION_STORE_CONTENTION", (
        f"{tool}: contention must carry SESSION_STORE_CONTENTION, not collapse "
        f"into generic INTERNAL_ERROR; got {result}"
    )
    assert result.get("retryable") is True, (
        f"{tool}: a transient write-queue stall IS retryable; got {result}"
    )
    assert result.get("ca_next_action"), (
        f"{tool}: CA needs an explicit next action to retry on; got {result}"
    )


def test_open_session_reports_contention_as_retryable(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db_path, project_path = isolated_project
    # open_session -> _open_or_resume_session -> _run_session_write
    monkeypatch.setattr(
        code_tools._session_store_module, "_run_session_write", _boom_contention)
    raw = asyncio.run(
        code_tools.open_session(project_path=project_path, agent_id="a40-open"))
    _assert_retryable_contention(json.loads(raw), "open_session")


def test_close_session_reports_contention_as_retryable(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db_path, project_path = isolated_project
    _open(project_path)
    monkeypatch.setattr(
        code_tools._session_store_module, "_log_session_event", _boom_contention)
    raw = asyncio.run(
        code_tools.close_session(project_path=project_path, agent_id="a40-probe"))
    _assert_retryable_contention(json.loads(raw), "close_session")


def test_rename_session_reports_contention_as_retryable(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db_path, project_path = isolated_project
    _open(project_path)
    # rename_session reads with _with_sessions_db then _run_session_write
    monkeypatch.setattr(
        code_tools._session_store_module, "_run_session_write", _boom_contention)
    raw = asyncio.run(code_tools.rename_session(
        project_path=project_path, new_title="renamed", agent_id="a40-probe"))
    _assert_retryable_contention(json.loads(raw), "rename_session")


def test_log_native_event_reports_contention_as_retryable(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db_path, project_path = isolated_project
    _open(project_path)
    monkeypatch.setattr(
        code_tools._session_store_module, "_run_session_write", _boom_contention)
    raw = asyncio.run(code_tools.log_native_event(
        action_type="edit", tool_name="raw_fs", target="x.py",
        project_path=project_path, agent_id="a40-probe"))
    _assert_retryable_contention(json.loads(raw), "log_native_event")


def test_create_pending_audit_reports_contention_as_retryable(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db_path, project_path = isolated_project
    monkeypatch.setattr(
        code_tools._session_store_module, "_run_session_write", _boom_contention)
    raw = asyncio.run(code_tools.create_pending_audit(
        project_path=project_path, action_type="edit", tool_name="raw_fs"))
    _assert_retryable_contention(json.loads(raw), "create_pending_audit")


def test_sync_pending_audit_reports_contention_as_retryable(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db_path, project_path = isolated_project
    _open(project_path)
    monkeypatch.setattr(
        code_tools._session_store_module, "_run_session_write", _boom_contention)
    raw = asyncio.run(code_tools.sync_pending_audit(
        project_path=project_path, agent_id="a40-probe"))
    _assert_retryable_contention(json.loads(raw), "sync_pending_audit")


def test_clear_closed_sessions_reports_contention_as_retryable(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db_path, project_path = isolated_project
    _open(project_path)
    monkeypatch.setattr(
        code_tools._session_store_module, "_run_session_write", _boom_contention)
    raw = asyncio.run(code_tools.clear_closed_sessions(project_path=project_path))
    _assert_retryable_contention(json.loads(raw), "clear_closed_sessions")


def test_code_tools_non_contention_stays_non_retryable(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contention translation must stay narrow: a genuine defect must not
    inherit the retryable verdict via the shared _json_error path."""
    _db_path, project_path = isolated_project
    _open(project_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated genuine defect")

    monkeypatch.setattr(
        code_tools._session_store_module, "_log_session_event", boom)
    result = json.loads(asyncio.run(
        code_tools.close_session(project_path=project_path, agent_id="a40-probe")))
    assert not result.get("ok"), result
    assert result.get("error_name") != "SESSION_STORE_CONTENTION", result
    assert not result.get("retryable"), result


# ── funnel 2: server dispatcher generic handler (tools_v15 tools) ────────────

def test_server_payload_translates_contention():
    """record_error_resolution / record_session_audit_checkpoint let contention
    propagate to the dispatcher's generic except. The shared server helper must
    translate it to the retryable contract using the SAME classifier."""
    payload = orchestrator_server._session_contention_error_payload(
        "record_error_resolution", _Contention("stalled"), {"x": 1})
    assert payload is not None
    assert payload["error_name"] == "SESSION_STORE_CONTENTION"
    assert payload["retryable"] is True
    assert payload["ca_next_action"]
    assert payload["phase"] == "retryable_transport"


def test_server_payload_none_for_non_contention():
    assert orchestrator_server._session_contention_error_payload(
        "record_error_resolution", RuntimeError("defect"), {}) is None
    assert orchestrator_server._session_contention_error_payload(
        "record_session_audit_checkpoint", ValueError("bad"), {}) is None
