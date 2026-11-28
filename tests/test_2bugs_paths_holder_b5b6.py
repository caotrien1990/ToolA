"""B5+B6 (2026-11-28): name the mismatched paths; name a lock-holder candidate honestly.

WHY this file exists
--------------------
- B5 (event 3465533): DRY_RUN_PROJECT_MISMATCH said only "dry_run target is
  outside project_path" -- the CA could not see WHICH file landed outside WHICH
  project. The containment block is correct and must keep refusing; the fix
  only adds dry_run_target + project_path to the error.
- B6 (events 3465431/3460931/3460921/3457033/3456677, all ~6.9-7.0s):
  SESSION_STORE_CONTENTION carried retry diagnostics but never named a
  lock-holder. SQLite has no API that discloses the holder of a busy lock, so
  the only honest candidate is this process's own in-flight ledger rows on the
  same project; an external holder must be reported as not identifiable, never
  guessed. NO retry/backoff change (C3 already owns that).

Mutants each test kills:
- B5: dropping either path key, or weakening the refusal (ok:true / no raise).
- B6: dropping the holder clause entirely; fabricating a holder when no
  in-flight op exists; naming an op from a DIFFERENT project; crashing the
  begin path when the ledger map is hostile.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import code_tools as ct
import orchestrator_server as server
from orchestrator.tools.write import core as write_core


def _run(coro):
    return json.loads(asyncio.run(coro))


# ---- B5 ------------------------------------------------------------------

def test_dry_run_project_mismatch_names_both_paths(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x\n", encoding="utf-8")
    dry_id = "dry-b5-probe"
    # The dry-run store is a FILE under the project cache (code_tools.py:1337),
    # and apply_after_dry_run takes project_path FIRST. expires_at_epoch is the
    # field the expiry gate reads (:2617).
    dry_path = ct._dry_run_store_path(str(project), dry_id)
    dry_path.parent.mkdir(parents=True, exist_ok=True)
    dry_path.write_text(json.dumps({
        "tool": "write_fix",
        "target_file": str(outside),
        "target_file_hash": ct._sha256_file(outside),
        "payload_hash": "h",
        "payload": {},
        "project_path": str(project),
        "expires_at_epoch": time.time() + 300,
    }), encoding="utf-8")
    try:
        out = _run(write_core.apply_after_dry_run(str(project), dry_id))
    finally:
        dry_path.unlink(missing_ok=True)
    assert out.get("ok") is not True, out
    assert out.get("error_name") == "DRY_RUN_PROJECT_MISMATCH", out
    assert out.get("dry_run_target") == str(outside.resolve()), out
    assert out.get("project_path") == str(project.resolve()), out
    # containment unchanged: the outside file was NOT written
    assert outside.read_text(encoding="utf-8") == "x\n"


# ---- B6 ------------------------------------------------------------------

def _begin_raises(monkeypatch, tmp_path, ledger_entries):
    monkeypatch.setattr(server, "_WRITE_LEDGER", dict(ledger_entries))
    monkeypatch.setattr(server, "_write_ledger_db_upsert", lambda rec, db_path=None: False)
    monkeypatch.setattr(server, "_write_ledger_db_get", lambda **kw: None)
    monkeypatch.setattr(server, "_WRITE_LEDGER_UPSERT_DIAG", type("D", (), {})())
    body = {"project_path": str(tmp_path), "file_path": "x.py"}
    with pytest.raises(ct._session_store_module.SessionStoreContentionError) as ei:
        server._write_ledger_begin("write_fix", body, "sess-b6")
    return str(ei.value)


def test_contention_names_inflight_candidate_with_age(tmp_path, monkeypatch):
    now = time.time()
    entries = {
        "op-old": {"operation_id": "op-old", "status": "in_flight", "tool": "run_ps1_script",
                    "session_id": "sess-holder", "project_path": str(tmp_path),
                    "started_at": now - 12.0},
        "op-new": {"operation_id": "op-new", "status": "in_flight", "tool": "write_fix",
                    "session_id": "sess-other", "project_path": str(tmp_path),
                    "started_at": now - 1.0},
        "op-done": {"operation_id": "op-done", "status": "committed", "tool": "write_fix",
                     "session_id": "s", "project_path": str(tmp_path), "started_at": now - 99},
    }
    msg = _begin_raises(monkeypatch, tmp_path, entries)
    assert msg.startswith("write ledger begin could not be persisted; tool was not started")
    assert "lock_holder_candidate=op-old" in msg, msg
    assert "tool=run_ps1_script" in msg and "session=sess-holder" in msg, msg
    assert "held_s=12" in msg, msg
    assert "not proof" in msg, msg


def test_contention_without_candidate_says_not_identifiable(tmp_path, monkeypatch):
    msg = _begin_raises(monkeypatch, tmp_path, {})
    assert "lock_holder=unknown" in msg, msg
    assert "does not disclose" in msg, msg
    assert "lock_holder_candidate=" not in msg, msg


def test_contention_ignores_other_project_and_committed(tmp_path, monkeypatch):
    now = time.time()
    entries = {
        "op-elsewhere": {"operation_id": "op-elsewhere", "status": "in_flight",
                          "tool": "write_fix", "session_id": "s",
                          "project_path": str(tmp_path / "other"), "started_at": now - 50},
    }
    msg = _begin_raises(monkeypatch, tmp_path, entries)
    assert "lock_holder_candidate=" not in msg, msg
    assert "lock_holder=unknown" in msg, msg


def test_contention_hostile_row_does_not_crash_begin(tmp_path, monkeypatch):
    # A hostile ROW (not a hostile map -- begin itself reads the map earlier at
    # :3394, so a map without .get would crash before the holder probe and that
    # would be a different defect). One row whose fields explode must not take
    # the begin path down: the probe is best-effort and falls back to unknown.
    class _HostileRow(dict):
        def get(self, key, default=None):
            if key == "status":
                return "in_flight"
            raise RuntimeError("boom")
    entries = {"op-bad": _HostileRow()}
    monkeypatch.setattr(server, "_WRITE_LEDGER", entries)
    monkeypatch.setattr(server, "_write_ledger_db_upsert", lambda rec, db_path=None: False)
    monkeypatch.setattr(server, "_write_ledger_db_get", lambda **kw: None)
    body = {"project_path": str(tmp_path), "file_path": "x.py"}
    with pytest.raises(ct._session_store_module.SessionStoreContentionError) as ei:
        server._write_ledger_begin("write_fix", body, "sess-b6")
    msg = str(ei.value)
    assert msg.startswith("write ledger begin could not be persisted; tool was not started")
    assert "lock_holder=unknown" in msg, msg
