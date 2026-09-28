"""F8/F9 (2026-09-28): a busy session write queue is not a refused token store.

``store._read_token_upsert`` wrapped its single-writer call in
``except Exception: return False``, so SessionStoreContentionError -- the A40
"transient, retry after a short backoff" signal -- reached read_file_range as
a plain falsy upsert. With force_reseed=true the CA was then answered
RESEED_FAILED / retryable:false / "Retrying the same call will fail the same
way", which is false for a queue that drains in seconds and is the opposite of
what every other session-store tool answers for the same exception. Without
force_reseed the read said read_token_seeded:false and nothing more -- the same
shape as a read that never tried to seed.

The swallow sat in the PRODUCER, one frame below any handler that could have
classified the exception, so a trace that followed ``_run_session_write``
callers into their handlers never saw this one.

Pinned at the layer each consumer reads:
  * store      -- contention propagates; every other failure still answers False
  * fs         -- the seed reports reason "contention" carrying the A40 fields,
                  asked of ``_session_contention_fields`` (the single classifier)
  * context    -- force_reseed answers the A40 contract verbatim; a plain read
                  stays ok:true but names the refusal in read_token_seed_failure
  * dispatcher -- SESSION_STORE_CONTENTION on read_file_range maps to "back off,
                  then resend the identical payload"

Contention is injected for the token write ONLY (by operation label); every
other session write stays real, so a pass cannot come from some unrelated write
failing the same way.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest import mock

import pytest

import code_tools
import orchestrator_server
import surgical_mapper
from orchestrator.tools.read import fs as read_fs

_store = code_tools._session_store_module
_Contention = _store.SessionStoreContentionError
_REAL_RUN_SESSION_WRITE = _store._run_session_write
_TOKEN_WRITE = "read token upsert"


def _contend_on_token_write(operation, write_fn, *args, **kwargs):
    """Busy queue for the token write only; every other write is real."""
    if operation == _TOKEN_WRITE:
        raise _Contention("simulated writer contention")
    return _REAL_RUN_SESSION_WRITE(operation, write_fn, *args, **kwargs)


def _refuse_token_write(operation, write_fn, *args, **kwargs):
    """A genuine store failure -- not contention -- for the token write only."""
    if operation == _TOKEN_WRITE:
        raise RuntimeError("database disk image is malformed")
    return _REAL_RUN_SESSION_WRITE(operation, write_fn, *args, **kwargs)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh sessions DB + a real project root holding one small file."""
    db_path = tmp_path / "sessions_f8.db"
    monkeypatch.setattr(code_tools, "_SESSIONS_DB", db_path)
    code_tools._sync_session_db_alias()
    code_tools._init_session_db()
    root = tmp_path / "proj_f8"
    root.mkdir()
    (root / ".git").mkdir()
    target = root / "mod.py"
    target.write_text("x = 1\n", encoding="utf-8")
    yield root, target
    monkeypatch.undo()
    code_tools._sync_session_db_alias()


def _read(target: Path, root: Path, **kwargs) -> dict:
    return json.loads(asyncio.run(code_tools.read_file_range(
        str(target), 1, 1, project_path=str(root), **kwargs)))


def _seed(target: Path, root: Path) -> tuple[bool, dict]:
    text = target.read_text(encoding="utf-8")
    return read_fs._seed_read_token_detailed(target, str(root), text, target.stat(), True)


# -- store: the producer ------------------------------------------------------

def test_store_upsert_propagates_contention(project, monkeypatch):
    root, _ = project
    # Live-path positive control: the real writer commits the token.
    assert _store._read_token_upsert(str(root), "mod.py", "h", 1.0, 1) is True
    monkeypatch.setattr(_store, "_run_session_write", _contend_on_token_write)
    with pytest.raises(_Contention):
        _store._read_token_upsert(str(root), "mod.py", "h", 1.0, 1)


def test_store_upsert_still_answers_false_on_other_failures(project, monkeypatch):
    root, _ = project
    monkeypatch.setattr(_store, "_run_session_write", _refuse_token_write)
    assert _store._read_token_upsert(str(root), "mod.py", "h", 1.0, 1) is False


# -- fs: the seed classifies through the A40 SOT ------------------------------

def test_seed_reports_contention_with_the_a40_fields(project, monkeypatch):
    root, target = project
    monkeypatch.setattr(_store, "_run_session_write", _contend_on_token_write)
    seeded, detail = _seed(target, root)
    assert seeded is False
    assert detail["reason"] == "contention", detail
    assert detail["contention"] == code_tools._session_contention_fields(_Contention("x"))
    assert "SessionStoreContentionError" in detail["error"]


def test_seed_keeps_upsert_failed_for_a_non_contention_raise(project, monkeypatch):
    root, target = project
    monkeypatch.setattr(code_tools, "_read_token_upsert",
                        mock.Mock(side_effect=RuntimeError("schema mismatch")))
    seeded, detail = _seed(target, root)
    assert seeded is False
    assert detail["reason"] == "upsert_failed", detail
    assert "contention" not in detail


# -- context: force_reseed ----------------------------------------------------

def test_force_reseed_under_contention_answers_the_a40_contract(project, monkeypatch):
    root, target = project
    monkeypatch.setattr(_store, "_run_session_write", _contend_on_token_write)
    out = _read(target, root, force_reseed=True)
    assert out["ok"] is False
    assert out["error_name"] == "SESSION_STORE_CONTENTION", out
    assert out["retryable"] is True
    assert out["ca_next_action"] == "retry_after_short_backoff"
    assert out["reseed_failure_reason"] == "contention"
    assert "fail the same way" not in out["hint"]
    assert out["read_token_seeded"] is False


def test_force_reseed_commits_once_the_queue_drains(project, monkeypatch):
    # The retry the contention answer promises must actually work.
    root, target = project
    monkeypatch.setattr(_store, "_run_session_write", _contend_on_token_write)
    assert _read(target, root, force_reseed=True)["ok"] is False
    monkeypatch.setattr(_store, "_run_session_write", _REAL_RUN_SESSION_WRITE)
    out = _read(target, root, force_reseed=True)
    assert out["ok"] is True and out["read_token_seeded"] is True, out


def test_force_reseed_genuine_store_failure_stays_non_retryable(project, monkeypatch):
    root, target = project
    monkeypatch.setattr(_store, "_run_session_write", _refuse_token_write)
    out = _read(target, root, force_reseed=True)
    assert out["ok"] is False
    assert out["error_name"] == "RESEED_FAILED", out
    assert out["reseed_failure_reason"] == "upsert_failed"
    assert out["retryable"] is False
    assert out["ca_next_action"] == "inspect_session_store"


# -- context: a plain read ----------------------------------------------------

def test_plain_read_under_contention_names_the_refusal(project, monkeypatch):
    root, target = project
    monkeypatch.setattr(_store, "_run_session_write", _contend_on_token_write)
    out = _read(target, root)
    assert out["ok"] is True
    assert out["read_token_seeded"] is False
    assert out["read_token_seed_failure"] == {
        "reason": "contention", "error_name": "SESSION_STORE_CONTENTION",
        "retryable": True, "ca_next_action": "retry_after_short_backoff"}


def test_plain_read_genuine_store_failure_names_the_refusal(project, monkeypatch):
    root, target = project
    monkeypatch.setattr(_store, "_run_session_write", _refuse_token_write)
    out = _read(target, root)
    assert out["ok"] is True
    assert out["read_token_seeded"] is False
    failure = out["read_token_seed_failure"]
    assert failure["reason"] == "upsert_failed"
    assert failure["retryable"] is False
    assert failure["ca_next_action"] == "inspect_session_store"


def test_clean_read_carries_no_seed_failure(project):
    root, target = project
    out = _read(target, root)
    assert out["ok"] is True and out["read_token_seeded"] is True, out
    assert "read_token_seed_failure" not in out


# -- dispatcher ---------------------------------------------------------------

def test_dispatcher_maps_read_contention_to_resend_identical():
    body = {"file_path": "D:/p/mod.py", "start_line": 1, "end_line": 1,
            "force_reseed": True, "project_path": "D:/p"}
    seq = orchestrator_server._retry_sequence(
        "read_file_range", "SESSION_STORE_CONTENTION", body)
    resend = [step for step in seq if step.get("tool") == "read_file_range"]
    assert resend and resend[0]["payload"] == body, seq


# -- F10 (2026-09-28): the write side names an unsealed token ----------------
# _seal_read_token hid every failure behind `except Exception: return False` and
# all call sites ignored the bool, so under store contention write_fix /
# write_new_file committed the bytes but never sealed the token: the CA's next
# write_fix was refused FILE_CHANGED_OUTSIDE_ORCHESTRATOR, blaming "outside" for
# [A]'s own write with no trace of the real cause. Contention is injected at the
# same single writer operation as the read-side tests above; the seed read, the
# INDEX and every other session write stay real.

def _writable_project(root: Path, target: Path) -> None:
    """Minimal INDEX + a real seeded read, so write_fix passes its gates."""
    surgical_mapper.ensure_minimal_index(str(root))
    assert _read(target, root)["read_token_seeded"] is True


def test_write_fix_seal_contention_names_the_miss(project, monkeypatch):
    """A committed write_fix whose seal hit contention must say so: ok:true, the
    new bytes on disk, read_token_sealed:false, SESSION_STORE_CONTENTION, and the
    force_reseed recovery in ca_next_action."""
    root, target = project
    _writable_project(root, target)
    monkeypatch.setattr(_store, "_run_session_write", _contend_on_token_write)
    out = json.loads(asyncio.run(code_tools.write_fix(
        "mod.py", 1, 1, "x = 2", project_path=str(root))))
    assert out["ok"] is True, out
    assert target.read_text(encoding="utf-8") == "x = 2\n"
    assert out["read_token_sealed"] is False
    assert out["read_token_seal_error"] == "SESSION_STORE_CONTENTION"
    assert "force_reseed" in out["ca_next_action"]


def test_write_new_file_seal_contention_names_the_miss(project, monkeypatch):
    """Same contract on write_new_file: the file exists, the seal did not land."""
    root, _ = project
    surgical_mapper.ensure_minimal_index(str(root))
    monkeypatch.setattr(_store, "_run_session_write", _contend_on_token_write)
    out = json.loads(asyncio.run(code_tools.write_new_file(
        "fresh.py", new_content="y = 1\n", project_path=str(root))))
    assert out["ok"] is True, out
    assert (root / "fresh.py").read_text(encoding="utf-8") == "y = 1\n"
    assert out["read_token_sealed"] is False
    assert out["read_token_seal_error"] == "SESSION_STORE_CONTENTION"
    assert "force_reseed" in out["ca_next_action"]


def test_clean_write_carries_no_seal_keys(project):
    """Zero-cost path: a write whose seal landed grows no key at all."""
    root, target = project
    _writable_project(root, target)
    out = json.loads(asyncio.run(code_tools.write_fix(
        "mod.py", 1, 1, "x = 2", project_path=str(root))))
    assert out["ok"] is True, out
    assert "read_token_sealed" not in out
    assert "read_token_seal_error" not in out


class _SchemaMismatch(RuntimeError):
    """Stand-in for a genuine defect inside the token store."""


def test_write_fix_seal_defect_names_the_exception_class(project, monkeypatch):
    """A non-contention raise must be reported by its class name -- not as
    contention -- and must still not fail the already-committed write."""
    root, target = project
    _writable_project(root, target)
    monkeypatch.setattr(code_tools, "_read_token_upsert",
                        mock.Mock(side_effect=_SchemaMismatch("schema mismatch")))
    out = json.loads(asyncio.run(code_tools.write_fix(
        "mod.py", 1, 1, "x = 2", project_path=str(root))))
    assert out["ok"] is True, out
    assert target.read_text(encoding="utf-8") == "x = 2\n"
    assert out["read_token_sealed"] is False
    assert out["read_token_seal_error"] == "_SchemaMismatch"
    assert "force_reseed" in out["ca_next_action"]
