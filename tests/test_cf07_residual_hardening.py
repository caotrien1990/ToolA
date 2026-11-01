"""CF-07 residuals: a retry must never replay an uncertain checkpoint commit."""
import asyncio
import json
import threading

from types import SimpleNamespace

import pytest

import code_tools as ct
from orchestrator.session import store


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "_SESSIONS_DB", tmp_path / "sessions.db")
    kwargs = dict(session_id="hardening-owner", agent_id="codo",
                  owner_claim_id="test-owner-claim-0123456789abcdef0123456789abcdef")
    claimed = json.loads(asyncio.run(ct.bootstrap_session(**kwargs)))
    assert claimed["ok"]
    kwargs["owner_capability"] = claimed["owner_capability"]
    begun = json.loads(asyncio.run(ct.begin_task(prompt="initial", **kwargs)))
    assert begun["ok"]
    yield kwargs, begun
    store.close_writer()


def test_committed_bootstrap_bypasses_stalled_admission(owner, monkeypatch):
    kwargs, _ = owner
    monkeypatch.setattr(store, "_run_session_write", lambda *a, **kw: pytest.fail("writer admission"))
    result = json.loads(asyncio.run(ct.bootstrap_session(
        **{k: v for k, v in kwargs.items() if k != "owner_capability"})))
    assert result["ok"]
    assert result["owner_capability"] == kwargs["owner_capability"]
    wrong = dict(kwargs, owner_claim_id="wrong-claim-0123456789abcdef0123456789abcdef")
    wrong.pop("owner_capability")
    result = json.loads(asyncio.run(ct.bootstrap_session(**wrong)))
    assert result["error_name"] == "TASK_OWNER_MISMATCH"


def test_repeated_real_prompt_still_opens_a_new_epoch(owner):
    kwargs, old = owner
    new = json.loads(asyncio.run(ct.begin_task(prompt="initial", **kwargs)))
    assert new["task_epoch"] == old["task_epoch"] + 1


def test_owner_activity_coalesces_only_fresh_valid_envelope(owner, monkeypatch):
    kwargs, begun = owner
    arguments = (kwargs["session_id"], "codo", begun["task_epoch"], begun["prompt_hash"])
    assert ct._record_authenticated_owner_activity(*arguments)["recorded"]
    checkpoint = ct._task_checkpoint_load(kwargs["session_id"])
    with monkeypatch.context() as local:
        local.setattr(store, "_run_session_write", lambda *a, **kw: pytest.fail("redundant writer admission"))
        for _ in range(5):
            assert ct._record_authenticated_owner_activity(*arguments)["reason"] == "activity_coalesced"
        assert ct._record_authenticated_owner_activity(arguments[0], "foreign", *arguments[2:])["reason"] == "owner_identity_mismatch"
    assert ct._task_checkpoint_load(kwargs["session_id"])["owner_heartbeat_count"] == checkpoint["owner_heartbeat_count"]
    monkeypatch.setattr(ct.time, "time", lambda: checkpoint["owner_last_heartbeat_at"] + 31)
    assert ct._record_authenticated_owner_activity(*arguments)["recorded"]


def test_admission_retries_are_bounded_and_commit_once(owner, monkeypatch):
    kwargs, begun = owner
    original = ct._task_checkpoint_update
    attempts = []
    def update(*args, **kw):
        attempts.append(1)
        if len(attempts) < 4:
            raise store._checkpoint_storage_error(store.SessionStoreContentionError(
                "door busy", contention_stage="admission"), kwargs["session_id"])
        return original(*args, **kw)
    monkeypatch.setattr(ct, "_task_checkpoint_update", update)
    result = json.loads(asyncio.run(ct.begin_task(prompt="next", **kwargs)))
    assert result["ok"] and len(attempts) == 4
    assert result["task_epoch"] == begun["task_epoch"] + 1


@pytest.mark.parametrize("name,stage,retryable", [
    ("TASK_CHECKPOINT_CONTENTION", "execution", True),
    ("TASK_CHECKPOINT_COMMIT_PENDING", "execution", False),
    ("TASK_CHECKPOINT_MALFORMED", "execution", False),
])
def test_handlers_never_replay_execution_or_corruption(owner, monkeypatch, name, stage, retryable):
    kwargs, _ = owner
    attempts = []
    def fail(*args, **kw):
        attempts.append(1)
        exc = store.TaskCheckpointStorageError("injected", error_name=name)
        exc.contention_stage = stage
        raise exc
    monkeypatch.setattr(ct, "_task_checkpoint_update", fail)
    result = json.loads(asyncio.run(ct.begin_task(prompt="next", **kwargs)))
    assert result["error_name"] == name and result["retryable"] is retryable
    assert len(attempts) == 1
    if "PENDING" in name:
        assert result["commit_state"] == "unknown" and result["safe_to_retry"] is False


def test_checkpoint_pending_keeps_callback_owned_by_writer(owner, monkeypatch):
    kwargs, begun = owner
    release = threading.Event()
    started = threading.Event()
    def update(previous):
        started.set()
        assert release.wait(2)
        previous["task_epoch"] += 1
        return previous, "done"
    monkeypatch.setattr(store, "_SESSION_CALL_WAIT_SECONDS", 0.05)
    try:
        with pytest.raises(store.TaskCheckpointStorageError) as caught:
            store._task_checkpoint_update(kwargs["session_id"], update)
        assert started.is_set()
        assert caught.value.error_name == "TASK_CHECKPOINT_COMMIT_PENDING"
    finally:
        release.set()
    assert store._get_session_writer().submit("test barrier", lambda conn: True, durable=True, wait_timeout=2)
    assert ct._task_checkpoint_load(kwargs["session_id"])["task_epoch"] == begun["task_epoch"] + 1


def test_checkpoint_cap_and_corruption_preserve_authority(owner):
    kwargs, _ = owner
    key = kwargs["session_id"]
    previous = ct._task_checkpoint_load(key)
    with pytest.raises(store.TaskCheckpointStorageError, match=key) as caught:
        ct._task_checkpoint_save(key, dict(previous, large="x" * 262145))
    assert caught.value.error_name == "TASK_CHECKPOINT_TOO_LARGE"
    assert ct._task_checkpoint_load(key) == previous
    store._run_session_write("corrupt fixture", lambda conn: conn.execute(
        "UPDATE task_checkpoints SET payload=? WHERE session_key=?", ("{broken", key)).rowcount)
    with pytest.raises(store.TaskCheckpointStorageError) as caught:
        ct._task_checkpoint_load(key)
    assert caught.value.error_name == "TASK_CHECKPOINT_MALFORMED"
    assert store._session_read_connection().execute(
        "SELECT payload FROM task_checkpoints WHERE session_key=?", (key,)).fetchone()[0] == "{broken"


def test_unchanged_save_never_enters_writer(owner, monkeypatch):
    kwargs, _ = owner
    key = kwargs["session_id"]
    checkpoint, stamp = ct._task_checkpoint_load_with_meta(key)
    monkeypatch.setattr(store, "_run_session_write", lambda *a, **kw: pytest.fail("identical save"))
    assert ct._task_checkpoint_save(key, checkpoint)
    assert ct._task_checkpoint_load_with_meta(key)[1] == stamp


def test_swap_refuses_second_writer_if_old_thread_cannot_stop(tmp_path, monkeypatch):
    old = SimpleNamespace(db_path=tmp_path / "old.db", _closing=threading.Event(), close=lambda **kw: False)
    monkeypatch.setattr(store, "_SESSION_WRITER", old)
    monkeypatch.setattr(store, "_SESSIONS_DB", tmp_path / "new.db")
    monkeypatch.setattr(store, "SessionWriter", lambda *a, **kw: pytest.fail("second writer"))
    with pytest.raises(store.SessionStoreContentionError, match="replacement refused"):
        store._get_session_writer()
    assert store._SESSION_WRITER is old