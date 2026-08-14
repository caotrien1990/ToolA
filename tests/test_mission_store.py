"""Tests for orchestrator.mission.store (P3a-2).

Uses pytest ``tmp_path`` so each test gets a fresh DB. No real
``missions.db`` is created in ``.orchestrator_cache``.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# Make repo root importable when running from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.mission import store
from orchestrator.mission.store import (
    SCHEMA_VERSION,
    ack_outbox,
    add_outbox,
    append_ledger,
    cas_transition,
    compute_task_key,
    create_mission,
    create_task,
    get_mission,
    get_schema_version,
    get_task,
    init_db,
    set_outbox_child_pid,
)

_MIGRATE_SCRIPT = _REPO_ROOT / "scripts" / "missions_migrate.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db(tmp_path: Path) -> Path:
    return tmp_path / "missions.db"


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

def test_init_creates_all_tables_wal_and_schema_version(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    sv = get_schema_version(db)
    conn.close()
    assert tables >= {"missions", "tasks", "dispatch_outbox", "progress_ledger", "meta"}
    assert journal.lower() == "wal"
    assert sv == SCHEMA_VERSION == 1


def test_init_is_idempotent(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    init_db(db)  # second call must not error
    assert get_schema_version(db) == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# create_mission / create_task round-trip
# ---------------------------------------------------------------------------

def test_create_mission_and_task_roundtrip(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    mid = create_mission(db, "demo mission", created_by="hermc5")
    assert isinstance(mid, int) and mid > 0

    res = create_task(
        db,
        mid,
        title="build widget",
        agent_hint="clau",
        output_artifact_path="out/widget.json",
        prompt_artifact_path="in/prompt.md",
    )
    assert res["created"] is True
    tid = res["task_id"]
    assert tid > 0

    task = get_task(db, tid)
    assert task is not None
    assert task["mission_id"] == mid
    assert task["title"] == "build widget"
    assert task["state"] == "pending"
    assert task["version"] == 0
    assert task["task_key"] is not None and len(task["task_key"]) == 16

    mission = get_mission(db, mid)
    assert mission is not None
    assert mission["title"] == "demo mission"
    assert mission["state"] == "draft"


def test_create_task_missing_output_artifact_raises(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    mid = create_mission(db, "m")
    with pytest.raises(ValueError):
        create_task(db, mid, title="no output")


# ---------------------------------------------------------------------------
# task_key idempotency
# ---------------------------------------------------------------------------

def test_task_key_collision_returns_existing_id_not_created(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    mid = create_mission(db, "m")
    r1 = create_task(
        db, mid, title="t", agent_hint="ope", output_artifact_path="a/out.json"
    )
    r2 = create_task(
        db, mid, title="t", agent_hint="ope", output_artifact_path="a/out.json"
    )
    assert r1["created"] is True
    assert r2["created"] is False
    assert r1["task_id"] == r2["task_id"]


def test_compute_task_key_deterministic_and_case_insensitive():
    k1 = compute_task_key("Clau", "Build", "OUT/x.json")
    k2 = compute_task_key("clau", "Build", "out/x.json")
    assert k1 == k2
    assert len(k1) == 16
    # Different artifact -> different key
    k3 = compute_task_key("clau", "Build", "out/y.json")
    assert k3 != k1


# ---------------------------------------------------------------------------
# cas_transition
# ---------------------------------------------------------------------------

def test_cas_transition_correct_version_succeeds_and_increments(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    mid = create_mission(db, "m")
    tid = create_task(db, mid, output_artifact_path="o.json")["task_id"]
    assert get_task(db, tid)["version"] == 0

    ok = cas_transition(db, tid, "pending", "ready", 0)
    assert ok is True
    t = get_task(db, tid)
    assert t["state"] == "ready"
    assert t["version"] == 1


def test_cas_transition_wrong_version_fails(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    mid = create_mission(db, "m")
    tid = create_task(db, mid, output_artifact_path="o.json")["task_id"]
    ok = cas_transition(db, tid, "pending", "ready", 99)
    assert ok is False
    # state unchanged
    assert get_task(db, tid)["state"] == "pending"


def test_cas_transition_wrong_state_fails(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    mid = create_mission(db, "m")
    tid = create_task(db, mid, output_artifact_path="o.json")["task_id"]
    ok = cas_transition(db, tid, "active", "ready", 0)  # state is 'pending', not 'active'
    assert ok is False
    assert get_task(db, tid)["state"] == "pending"


def test_cas_transition_with_set_fields(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    mid = create_mission(db, "m")
    tid = create_task(db, mid, output_artifact_path="o.json")["task_id"]
    ok = cas_transition(db, tid, "pending", "started", 0, child_pid=12345, started_at="2026-07-09T00:00:00+00:00")
    assert ok is True
    t = get_task(db, tid)
    assert t["child_pid"] == 12345
    assert t["started_at"] == "2026-07-09T00:00:00+00:00"
    assert t["version"] == 1


# ---------------------------------------------------------------------------
# append_ledger
# ---------------------------------------------------------------------------

def test_append_ledger_roundtrip(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    mid = create_mission(db, "m")
    tid = create_task(db, mid, output_artifact_path="o.json")["task_id"]
    lid = append_ledger(db, mid, tid, "dispatch_started", '{"pid": 42}')
    assert lid > 0
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM progress_ledger WHERE id = ?", (lid,)).fetchone()
    conn.close()
    assert row["kind"] == "dispatch_started"
    assert row["task_id"] == tid
    assert row["mission_id"] == mid
    assert '"pid": 42' in row["detail_json"]


# ---------------------------------------------------------------------------
# dispatch_outbox
# ---------------------------------------------------------------------------

def test_outbox_add_pid_ack_roundtrip(tmp_path):
    db = _db(tmp_path)
    init_db(db)
    mid = create_mission(db, "m")
    tid = create_task(db, mid, output_artifact_path="o.json")["task_id"]
    oid = add_outbox(db, tid, "req-1", "sha-abc")
    assert oid > 0

    set_outbox_child_pid(db, oid, 999)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM dispatch_outbox WHERE id = ?", (oid,)).fetchone()
    conn.close()
    assert row["child_pid"] == 999
    assert row["acked_at"] is None

    ack_outbox(db, oid)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT acked_at FROM dispatch_outbox WHERE id = ?", (oid,)).fetchone()
    conn.close()
    assert row["acked_at"] is not None


# ---------------------------------------------------------------------------
# migrate script (forward + rollback)
# ---------------------------------------------------------------------------

def test_migrate_forward_then_rollback(tmp_path):
    db = tmp_path / "migrate.db"
    # Forward via import.
    from scripts.missions_migrate import main as migrate_main
    rc = migrate_main([str(db)])
    out = ""  # main prints to stdout; we check rc + file state
    assert rc == 0
    assert db.exists()
    assert get_schema_version(db) == SCHEMA_VERSION

    # Rollback via subprocess to exercise the real CLI path.
    proc = subprocess.run(
        [sys.executable, str(_MIGRATE_SCRIPT), str(db), "--rollback"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    import json as _json
    payload = _json.loads(proc.stdout.strip())
    assert payload["ok"] is True
    bak = db.with_suffix(db.suffix + ".rollback_bak")
    assert bak.exists()
    assert not db.exists()


def test_migrate_forward_is_idempotent(tmp_path):
    db = tmp_path / "mig2.db"
    from scripts.missions_migrate import main as migrate_main
    assert migrate_main([str(db)]) == 0
    assert migrate_main([str(db)]) == 0
    assert get_schema_version(db) == SCHEMA_VERSION
