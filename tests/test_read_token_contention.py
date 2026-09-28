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


# -- R2 residuals (2026-09-28): the restore path reports through the ONE matcher,
# and the reseed advice wins the advice key when the cache refresh also failed.
# (b) MEASURED NO: read_file_range(force_reseed=true) does NOT refresh the INDEX
# entry -- the reseed path writes only the session-store read token
# (orchestrator/tools/read/fs.py:170-171 _read_token_upsert via context.py:138);
# the INDEX entry belongs to _update_cache_after_write -> surgical_mapper.
# update_file_cache (code_tools.py:712). Neither step covers the other, so the
# combined advice names BOTH, reseed first (the unsealed token is the hard
# blocker: it refuses the next write_fix; a stale INDEX only misleads reads).
# The seal is monkeypatched at the single seal function (write/core.
# _seal_read_token) and the cache refresh at the single cache call
# (code_tools._update_cache_after_write) -- never at the answer dict.

from orchestrator.tools.write import core as write_core  # noqa: E402


def _counting_seal(results):
    """Patch for write_core._seal_read_token: return pre-set (sealed, kind) per
    call index, repeating the last one. Call order on the write_fix revert path:
    1 = the original write's seal (core.py:1894), 2 = the restore's re-seal
    (core.py:738)."""
    calls = []

    def _seal(fp, project_path=""):
        calls.append(str(fp))
        return results[min(len(calls) - 1, len(results) - 1)]
    return _seal


_KEEP = "def f():\n    return 1\n\n\nvalue = f()\n"


def _keep_file(root: Path) -> Path:
    """A second file whose lines 1-2 replacement removes a still-referenced def:
    the patch passes every PRE-write gate and dies in the POST-write symbol
    check, so the write auto-reverts through _restore_backup_and_reseed -- the
    restore path under test, with no patch on the validation itself."""
    keep = root / "keep.py"
    keep.write_text(_KEEP, encoding="utf-8")
    assert _read(keep, root)["read_token_seeded"] is True
    return keep


def test_restore_seal_miss_is_reported_through_the_one_matcher(project, monkeypatch):
    """Restore path + forced seal failure: the answer must carry
    read_token_sealed:false, the seal error, and the reseed advice -- and the
    advice must SURVIVE whatever the revert branch assigns to ca_next_action
    afterwards (write_fix / the draft _view() update).

    Pins core.py:738-741 (_restore_backup_and_reseed captures the seal into the
    caller-owned seal_out box), 1971-1973 (the symbol-revert passes the box),
    1995-1996 or 2008-2009 (late report, AFTER the branch's own ca_next_action
    assignment and the draft update, with the _seal_out.get("sealed", _sealed)
    fallback) and 710-718 (the ONE _report_seal_miss matcher sets the keys and
    ASSIGNS the advice). Mutant killed: a restore that ignores its seal result
    (pre-fix `_seal_read_token(fp, project_path)` with the tuple dropped) adds
    no key -- read_token_sealed would be missing here. The seal is patched at
    the single seal function only; the store, the INDEX refresh and the answer
    dict stay real."""
    root, target = project
    _writable_project(root, target)
    keep = _keep_file(root)
    monkeypatch.setattr(write_core, "_seal_read_token",
                        _counting_seal([(True, ""), (False, "probe_refused")]))
    out = json.loads(asyncio.run(code_tools.write_fix(
        "keep.py", 1, 2, "x = 1", project_path=str(root))))
    assert out["ok"] is False
    assert out["auto_reverted"] is True, out
    assert keep.read_text(encoding="utf-8") == _KEEP  # the restore landed
    assert out["read_token_sealed"] is False
    assert out["read_token_seal_error"] == "probe_refused"
    assert out["ca_next_action"].startswith("read_file_range(file_path=keep.py")
    assert "force_reseed=true" in out["ca_next_action"]
    # cache refresh succeeded on this path -> no cache_delta step in the advice
    assert "cache_delta" not in out["ca_next_action"]


def test_restore_seal_success_adds_no_seal_keys(project):
    """Restore path + seal success: none of the seal keys may appear -- the
    clean path still pays zero tokens (same contract as F10's
    test_clean_write_carries_no_seal_keys, on the revert route).

    Pins the same lines from the negative side: the report guard at
    core.py:1995/2008 `if not _seal_out.get("sealed", _sealed):` must stay
    False when the restore's re-seal landed, and the branch's normal revert
    advice must survive untouched. No patch at all -- real seal, real store."""
    root, target = project
    _writable_project(root, target)
    keep = _keep_file(root)
    out = json.loads(asyncio.run(code_tools.write_fix(
        "keep.py", 1, 2, "x = 1", project_path=str(root))))
    assert out["ok"] is False
    assert out["auto_reverted"] is True, out
    assert keep.read_text(encoding="utf-8") == _KEEP
    assert "read_token_sealed" not in out
    assert "read_token_seal_error" not in out
    assert "force_reseed" not in out["ca_next_action"]


def test_cache_sync_failure_plus_seal_miss_reseed_advice_wins(project, monkeypatch):
    """cache_delta refresh fails AND the seal misses: the advice key must be the
    reseed-first COMBINED advice (measured NO on (b) -- force_reseed does not
    repair the INDEX, so it cannot simply replace the cache_delta advice),
    while cache_delta's OWN error fields stay intact.

    Pins core.py:2111-2112 (the deferred report runs AFTER the cache_sync
    branch, so it sees cache_synced=False), 713-717 (the combined-advice branch
    keyed on cache_synced is False) and 718 (ASSIGNMENT, not the pre-fix
    setdefault that let the cache branch's advice win). Also pins 2036-2043:
    action_required/fallback_sequence/cache_sync_warning are untouched, and the
    cache branch's own ca_next_action is a setdefault the report overwrites.
    Mutant killed: pre-fix setdefault in _report_seal_miss left
    ca_next_action == "cache_delta" here."""
    root, target = project
    _writable_project(root, target)
    monkeypatch.setattr(code_tools, "_update_cache_after_write", lambda *a, **k: False)
    monkeypatch.setattr(write_core, "_seal_read_token",
                        _counting_seal([(False, "probe_refused")]))
    out = json.loads(asyncio.run(code_tools.write_fix(
        "mod.py", 1, 1, "x = 2", project_path=str(root))))
    assert out["ok"] is True, out
    assert target.read_text(encoding="utf-8") == "x = 2\n"
    assert out["cache_synced"] is False
    assert out["read_token_sealed"] is False
    assert out["read_token_seal_error"] == "probe_refused"
    # reseed-first combined advice wins the advice key ...
    advice = out["ca_next_action"]
    assert advice.startswith("read_file_range(file_path=mod.py")
    assert "force_reseed=true" in advice
    assert "cache_delta" in advice
    assert advice.index("force_reseed") < advice.index("cache_delta")
    # ... while cache_delta's own error fields stay intact
    assert out["action_required"] == "cache_delta"
    assert out["fallback_sequence"] == ["cache_delta", "read_file_range"]
    assert "cache_sync_warning" in out
