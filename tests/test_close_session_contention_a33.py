"""A33 gap 2: close_session must report store contention as RETRYABLE.

WHY this file exists
--------------------
A33 made `_log_session_event` propagate `SessionStoreContentionError` instead
of swallowing it to a null event_id. That is correct at the store layer, but
the A33 worker enumerated only 4 of the 6 production callers. The two it
missed both sit inside `close_session`:

    code_tools.py:11654  (_close_session_by_id)
    code_tools.py:11758  (_close_session_by_project)

Both are nested inside `close_session`'s `try:` whose generic
`except Exception` funnels to `_json_error(str(e))` -- and `_json_error`
defaults to `retryable=False`.

The failure that produces: a *transient* write-queue stall is reported to CA
as INTERNAL_ERROR / retryable=false. CA gives up. But the `UPDATE sessions SET
status='closed'` never ran, so the row stays 'active' forever -- the session is
stranded and nothing will ever close it. Contention is the one error in this
handler that a plain retry actually fixes.

These tests pin the contract:
  1. contention -> error_name=SESSION_STORE_CONTENTION, retryable=true
  2. the session row is untouched (still 'active') so a retry is meaningful
  3. a NON-contention exception still reports as non-retryable INTERNAL_ERROR
     (the dedicated handler must not widen into a catch-all)
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

import code_tools


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh sessions DB + a real on-disk project dir per test."""
    db_path = tmp_path / "sessions_a33.db"
    monkeypatch.setattr(code_tools, "_SESSIONS_DB", db_path)
    code_tools._sync_session_db_alias()
    code_tools._init_session_db()
    project_dir = tmp_path / "proj_a33"
    project_dir.mkdir()
    yield db_path, str(project_dir)
    monkeypatch.undo()
    code_tools._sync_session_db_alias()


def _open(project_path: str) -> None:
    raw = asyncio.run(
        code_tools.open_session(project_path=project_path, agent_id="a33-probe")
    )
    assert json.loads(raw).get("ok"), f"setup failed to open a session: {raw[:300]}"


def _statuses(db_path: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT session_id, status FROM sessions").fetchall()


def test_close_session_reports_store_contention_as_retryable(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contention must reach CA as a retryable SESSION_STORE_CONTENTION.

    Reported non-retryable, CA stops trying and the session is stranded
    'active' forever -- the exact bug this test exists to prevent.
    """
    db_path, project_path = isolated_project
    _open(project_path)

    def boom(*_args, **_kwargs):
        raise code_tools._session_store_module.SessionStoreContentionError(
            "simulated writer contention"
        )

    monkeypatch.setattr(
        code_tools._session_store_module, "_log_session_event", boom
    )
    raw = asyncio.run(
        code_tools.close_session(project_path=project_path, agent_id="a33-probe")
    )
    result = json.loads(raw)

    assert not result.get("ok"), f"contention must fail loudly; got {result}"
    assert result.get("error_name") == "SESSION_STORE_CONTENTION", (
        "contention must carry its own error_name, not collapse into the "
        f"generic INTERNAL_ERROR handler; got {result}"
    )
    assert result.get("retryable") is True, (
        "a transient write-queue stall IS retryable -- telling CA otherwise "
        f"strands the session as 'active' forever; got {result}"
    )
    assert result.get("ca_next_action"), (
        f"CA needs an explicit next action to retry on; got {result}"
    )

    # The whole point of 'retryable': the close did NOT happen, so retrying
    # is meaningful rather than a no-op on an already-closed session.
    rows = _statuses(db_path)
    assert rows and all(status == "active" for _sid, status in rows), (
        f"close must not have partially applied; got {rows}"
    )


def test_close_session_retry_succeeds_once_contention_clears(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry CA is told to make must actually work."""
    db_path, project_path = isolated_project
    _open(project_path)

    original = code_tools._session_store_module._log_session_event
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise code_tools._session_store_module.SessionStoreContentionError(
                "simulated transient contention"
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(
        code_tools._session_store_module, "_log_session_event", flaky
    )

    first = json.loads(
        asyncio.run(
            code_tools.close_session(project_path=project_path, agent_id="a33-probe")
        )
    )
    assert first.get("retryable") is True, first

    second = json.loads(
        asyncio.run(
            code_tools.close_session(project_path=project_path, agent_id="a33-probe")
        )
    )
    assert second.get("ok"), f"the advertised retry must succeed; got {second}"

    rows = _statuses(db_path)
    assert rows and all(status == "closed" for _sid, status in rows), (
        f"after the retry the session must actually be closed; got {rows}"
    )


def test_close_session_non_contention_failure_stays_non_retryable(
    isolated_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contention handler must stay narrow.

    A genuine bug (here: a programming error) is NOT fixed by retrying. If the
    dedicated handler ever widens to catch Exception, CA would be told to loop
    forever on a deterministic failure.
    """
    _db_path, project_path = isolated_project
    _open(project_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated genuine defect")

    monkeypatch.setattr(
        code_tools._session_store_module, "_log_session_event", boom
    )
    result = json.loads(
        asyncio.run(
            code_tools.close_session(project_path=project_path, agent_id="a33-probe")
        )
    )

    assert not result.get("ok"), result
    assert result.get("error_name") != "SESSION_STORE_CONTENTION", (
        "a plain RuntimeError is not contention -- it must not inherit the "
        f"retryable contention verdict; got {result}"
    )
    assert not result.get("retryable"), (
        f"a deterministic defect must not be advertised as retryable; got {result}"
    )
