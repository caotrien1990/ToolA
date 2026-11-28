"""C3: write-ledger begin retry budget + contention diagnostics.

WHY this file exists
--------------------
The observed CI/watcher contention shape fails the ledger INSERT 4 times and
then succeeds. The old _write_ledger_db_upsert budget was 3 attempts, so that
shape rejected a begin that a 5-attempt budget admits. C3 raises the budget to
5 (backoff 0.1/0.2/0.3/0.4s, total sleep <= 1.0s) and, on final failure, the
begin site must still raise SessionStoreContentionError with the SAME prefix
plus attempts / total_wait_s / last_error diagnostics -- and a rejected begin
must leave NO partial write (no write_operations row, no _WRITE_LEDGER entry).

Each test names the mutant it kills:
- sustained contention 4x then success -> old range(3) budget (returns False
  on the observed shape; must return True now)
- permanent contention -> a begin that swallows the failure, drops the message
  prefix, omits the diagnostics, or inserts the memory row before persisting
- backoff bounded -> a retry loop whose sleeps exceed the 1.0s budget

Isolation: every test points ct._SESSIONS_DB at a tmp_path DB (same
monkeypatch-of-alias pattern as test_write_ledger_insert_schema_2dec) and fakes
contention by intercepting ONLY the once-layer's sqlite3.connect(timeout=2.0)
call, so schema init and verification reads use the real sqlite. The live
.orchestrator_cache/sessions.db is never opened for writing.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

import orchestrator_server as server
from orchestrator.session import store


def _rec(op_id: str) -> dict:
    now = time.time()
    return {
        "operation_id": op_id, "tool": "write_fix", "status": "committed",
        "project_path": r"d:\repo", "file_path": r"pkg\mod.py",
        "session_id": "sess-c3", "started_at": now, "finished_at": now,
        "duration_ms": 1, "attempts": 1, "previous_status": None,
        "result": {"ok": True}, "origin_generation": server.SERVER_GENERATION,
        "updated_at": now,
    }


@pytest.fixture
def ledger_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh tmp sessions DB + isolated ledger maps; never the live DB."""
    db = tmp_path / "sessions_c3.db"
    key = os.path.normcase(os.path.abspath(str(db)))
    store.close_writer()
    monkeypatch.setattr(server.ct, "_SESSIONS_DB", db)
    server.ct._sync_session_db_alias()
    store._SESSION_DB_INITIALIZED_PATHS.discard(key)
    store._SESSION_INIT_FAILURES.pop(key, None)
    monkeypatch.setattr(server, "_WRITE_LEDGER", {})
    monkeypatch.setattr(server, "_WRITE_LEDGER_MEMORY_GAPS", {})
    monkeypatch.setattr(server, "_OPERATION_CANCEL_EVENTS", {})
    monkeypatch.setattr(server, "_write_ledger_schedule_recovery", lambda: None)
    # Pre-create the schema on the tmp DB so contention is faked at the ledger
    # INSERT connect only, not at schema init.
    server._ensure_write_ledger_schema()
    yield db
    store.close_writer()
    monkeypatch.undo()
    server.ct._sync_session_db_alias()
    store._SESSION_DB_INITIALIZED_PATHS.discard(key)
    store._SESSION_INIT_FAILURES.pop(key, None)


def _fake_ledger_contention(monkeypatch: pytest.MonkeyPatch, fail_times):
    """Raise 'database is locked' on the first fail_times ledger connects.

    The once-layer is the only ledger code that connects with timeout=2.0;
    filtering on that keyword pins the fake to the layer under test and leaves
    schema init (different timeout) and test verification reads untouched.
    fail_times=None means permanent contention.
    """
    real_connect = sqlite3.connect
    calls = {"n": 0}

    def flaky_connect(*args, **kwargs):
        if kwargs.get("timeout") == 2.0:
            calls["n"] += 1
            if fail_times is None or calls["n"] <= fail_times:
                raise sqlite3.OperationalError("database is locked")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(server.sqlite3, "connect", flaky_connect)
    return calls


def _row_count(db: Path, op_id: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT count(*) FROM write_operations WHERE operation_id=?",
            (op_id,)).fetchone()[0]
    finally:
        conn.close()


def test_sustained_contention_four_failures_then_success(ledger_db, monkeypatch):
    """The observed CI/watcher shape: 4 locked attempts, 5th succeeds.

    Kills the old range(3) budget: it returned False (begin rejected) on a
    contention episode the store actually survives. The 5-attempt budget must
    return True and the row must be persisted exactly once.
    """
    db = ledger_db
    sleeps: list[float] = []
    monkeypatch.setattr(server.time, "sleep", lambda s: sleeps.append(s))
    calls = _fake_ledger_contention(monkeypatch, fail_times=4)

    ok = server._write_ledger_db_upsert(_rec("op-c3-sustained"))

    assert ok is True, "4 failures then success must be admitted by the 5-attempt budget"
    assert calls["n"] == 5, f"expected exactly 5 ledger connect attempts, got {calls['n']}"
    assert _row_count(db, "op-c3-sustained") == 1
    assert len(sleeps) == 4, "4 failures before success means 4 backoff sleeps"


def test_permanent_contention_begin_raises_with_diagnostics_and_no_partial_write(
    ledger_db, monkeypatch
):
    """Always-locked store: upsert False, begin raises, nothing partial persists.

    Kills: a begin that starts the tool anyway, a message that drops the pinned
    prefix or the attempts/total_wait_s/last_error diagnostics, and an ordering
    that inserts _WRITE_LEDGER[op_id] before the durable row exists.
    """
    db = ledger_db
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    # db_get must not turn the fake connect failure into a READ contention error
    # before the upsert path runs (same mock as test_cf_high_risk_ledger cf02).
    monkeypatch.setattr(server, "_write_ledger_db_get", lambda **kw: None)
    _fake_ledger_contention(monkeypatch, fail_times=None)

    assert server._write_ledger_db_upsert(_rec("op-c3-perm")) is False

    body = {"project_path": str(db.parent), "file_path": "x.py"}
    op_id = server._write_ledger_operation_id("write_fix", body, "sess-c3")
    prefix = "write ledger begin could not be persisted; tool was not started"
    with pytest.raises(server.ct._session_store_module.SessionStoreContentionError) as excinfo:
        server._write_ledger_begin("write_fix", body, "sess-c3")

    msg = str(excinfo.value)
    assert msg.startswith(prefix), f"pinned prefix must be kept; got {msg!r}"
    assert "attempts=5" in msg, f"diagnostics must name the attempt count; got {msg!r}"
    assert "total_wait_s=" in msg, f"diagnostics must name total wait; got {msg!r}"
    assert "last_error=OperationalError" in msg, (
        f"diagnostics must name the last exception class; got {msg!r}"
    )
    detail = msg[len(prefix):].lstrip("; ")
    assert 0 < len(detail) <= 200, f"appended diagnostics must be bounded ~200 chars; got {len(detail)}"

    # Rejected begin: NO partial write anywhere.
    assert op_id not in server._WRITE_LEDGER, "memory row must not exist for a rejected begin"
    assert server._WRITE_LEDGER == {}
    assert _row_count(db, op_id) == 0, "no write_operations row may persist for a rejected begin"


def test_backoff_bounded_total_sleep_at_most_one_second(ledger_db, monkeypatch):
    """5 attempts, 4 gaps: sleeps must be 0.1/0.2/0.3/0.4 and total <= 1.0s.

    Kills a retry loop whose backoff grows past the bounded budget (the begin
    wait is dominated by the unchanged 2.0s connect / 2000ms busy timeouts; the
    sleep budget itself must stay <= 1.0s).
    """
    sleeps: list[float] = []
    monkeypatch.setattr(server.time, "sleep", lambda s: sleeps.append(s))
    _fake_ledger_contention(monkeypatch, fail_times=None)

    ok = server._write_ledger_db_upsert(_rec("op-c3-backoff"))

    assert ok is False
    assert sleeps == pytest.approx([0.1, 0.2, 0.3, 0.4]), (
        f"backoff must be 0.1*(attempt+1) between the 5 attempts; got {sleeps}"
    )
    assert sum(sleeps) <= 1.0, f"total sleep across 5 attempts must be <= 1.0s; got {sum(sleeps)}"


def test_diagnostics_keep_per_attempt_elapsed(ledger_db, monkeypatch):
    """Permanent contention: the thread-local diagnostics must keep ALL 5
    per-attempt entries (attempt, elapsed_s, error) after the call returns, and
    the begin message must carry last_elapsed_s of the final attempt.

    Kills: a finally that overwrites the summary and drops the final attempt's
    elapsed / the per-attempt history -- the caller then cannot tell a fast
    refusal from a full 2s busy-timeout burn per attempt.
    """
    db = ledger_db
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    monkeypatch.setattr(server, "_write_ledger_db_get", lambda **kw: None)
    _fake_ledger_contention(monkeypatch, fail_times=None)

    assert server._write_ledger_db_upsert(_rec("op-c3-diag")) is False

    diag = server._WRITE_LEDGER_UPSERT_DIAG
    history = getattr(diag, "attempts", None)
    assert isinstance(history, list) and len(history) == 5, (
        f"per-attempt history must survive the call with 5 entries; got {history!r}"
    )
    for i, entry in enumerate(history, start=1):
        assert entry["attempt"] == i, f"entry {i} must name its attempt; got {entry!r}"
        assert "elapsed_s" in entry and isinstance(entry["elapsed_s"], float), (
            f"entry {i} must carry its measured elapsed; got {entry!r}"
        )
        assert entry["error"] == "OperationalError", (
            f"entry {i} must name the exception class; got {entry!r}"
        )
    last = getattr(diag, "last", None) or {}
    assert last.get("attempts") == 5
    assert last.get("last_error") == "OperationalError"
    assert last.get("last_elapsed_s") == history[-1]["elapsed_s"], (
        "summary last_elapsed_s must be the FINAL attempt's elapsed, not dropped by finally"
    )

    body = {"project_path": str(db.parent), "file_path": "diag.py"}
    with pytest.raises(server.ct._session_store_module.SessionStoreContentionError) as excinfo:
        server._write_ledger_begin("write_fix", body, "sess-c3")
    msg = str(excinfo.value)
    assert "last_elapsed_s=" in msg, f"begin diagnostics must carry last_elapsed_s; got {msg!r}"
