"""CF-18: quota failures disclose the same-principal holder's observed wait."""
import asyncio
import sqlite3
import threading
import time

import pytest
from orchestrator.server import executor_lanes as lanes
from orchestrator.server.fair_admission import (
    PrincipalQuotaExceeded, set_admission_identity, reset_admission_identity,
)
from orchestrator.session import store


async def until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "holder did not reach the expected wait"
        await asyncio.sleep(.005)


@pytest.mark.parametrize("store_blocked", [True, False])
def test_quota_bystander_reports_store_wait_only_when_observed(tmp_path, monkeypatch, store_blocked):
    store.close_writer()
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(store, "_SESSIONS_DB", db)
    writer = store._get_session_writer()
    monkeypatch.setenv("ORCH_FAIR_COMMAND_QUOTA", "1")
    monkeypatch.setenv("ORCH_FAIR_ANONYMOUS_COMMAND_QUOTA", "1")
    monkeypatch.setenv("ORCH_FAIR_ADMISSION_ENABLED", "1")
    blocker = sqlite3.connect(db)
    if store_blocked:
        blocker.execute("BEGIN IMMEDIATE")
    started = threading.Event()
    release = threading.Event()

    def holder():
        started.set()
        if store_blocked:
            return store._run_session_write("cf18-lock-probe", lambda conn: conn.execute(
                "INSERT INTO sessions (session_id, session_title) VALUES ('cf18', 'probe')"
            ).rowcount, busy_timeout_ms=2000)
        release.wait(3)
        return 1

    async def scenario():
        await lanes.shutdown_executor_lanes(drain_timeout=2)
        lanes._reset_executor_lanes_for_tests()
        lane = lanes.get_executor_lane("command")
        lane.queue_wait_timeout = .10
        first = asyncio.create_task(lanes.run_in_executor_lane("command", holder))
        try:
            await until(started.is_set)
            if store_blocked:
                await until(lambda: writer._active_batch_since > 0)
            with pytest.raises(PrincipalQuotaExceeded) as caught:
                await lanes.run_in_executor_lane("command", lambda: pytest.fail("bystander ran"))
            payload = caught.value.as_payload()
            assert payload["error_name"] == "PRINCIPAL_QUOTA_EXCEEDED"
            assert payload["retryable"] is True
            attribution = payload["metrics"]["quota_holders"]
            assert attribution["active"] == 1
            assert attribution["wait_causes"] == (
                {"session_store_commit": 1} if store_blocked else {}
            )
            # A different authenticated principal is not this quota's bystander.
            tokens = set_admission_identity("agent:other")
            try:
                assert await lanes.run_in_executor_lane("command", lambda: 7) == 7
            finally:
                reset_admission_identity(tokens)
        finally:
            blocker.rollback()
            release.set()
            await first
            await lanes.shutdown_executor_lanes(drain_timeout=2)
            lanes._reset_executor_lanes_for_tests()

    try:
        asyncio.run(scenario())
        if store_blocked:
            assert blocker.execute("SELECT COUNT(*) FROM sessions WHERE session_id='cf18'").fetchone()[0] == 1
    finally:
        blocker.close()
        store.close_writer()


def test_wait_attribution_clears_after_failure_and_does_not_cross_principals(monkeypatch):
    monkeypatch.setenv("ORCH_FAIR_ADMISSION_ENABLED", "1")
    monkeypatch.setenv("ORCH_FAIR_COMMAND_QUOTA", "1")
    async def scenario():
        from orchestrator.server.fair_admission import FairAdmissionManager
        manager = FairAdmissionManager()
        first = await manager.acquire("command", deadline=time.monotonic()+1, principal="agent:a")
        second = await manager.acquire("command", deadline=time.monotonic()+1, principal="agent:b")
        try:
            first.set_wait_cause("session_store_commit")
            with pytest.raises(PrincipalQuotaExceeded) as caught:
                await manager.acquire("command", deadline=time.monotonic(), principal="agent:b")
            assert caught.value.as_payload()["metrics"]["quota_holders"]["wait_causes"] == {}
            token = store._SESSION_WAIT_OBSERVER.set(first.set_wait_cause)
            try:
                with pytest.raises(ValueError):
                    with store._session_wait_scope("session_store_commit"):
                        raise ValueError("injected")
            finally:
                store._SESSION_WAIT_OBSERVER.reset(token)
            with pytest.raises(PrincipalQuotaExceeded) as caught:
                await manager.acquire("command", deadline=time.monotonic(), principal="agent:a")
            assert caught.value.as_payload()["metrics"]["quota_holders"]["wait_causes"] == {}
        finally:
            first.release()
            second.release()
        assert manager.status()["permit_count"] == 0
    asyncio.run(scenario())
