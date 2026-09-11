"""P3 Mission Queue store (SQLite WAL).

Plan: A_CSLT v3.1 — mission/task/dispatch model with idempotent task keys,
compare-and-set state transitions, append-only progress ledger, and a
dispatch outbox for child-process ack tracking.

GR-13 NOTE: Any schema change to this module MUST be paired with a forward
migration in ``scripts/missions_migrate.py`` (and a rollback path). Bump
``SCHEMA_VERSION`` and add the migration step. Never ALTER an existing
production ``missions.db`` in-place without a migration script.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Union

SCHEMA_VERSION = 1

PathLike = Union[str, Path]

# DDL — single source of truth for the schema. Keep in sync with
# scripts/missions_migrate.py and tests/test_mission_store.py.
_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS missions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'draft'
            CHECK(state IN ('draft','active','paused','blocked','done','failed')),
        task_ledger_json TEXT,
        created_by TEXT,
        room_path TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mission_id INTEGER NOT NULL REFERENCES missions(id),
        seq INTEGER NOT NULL DEFAULT 0,
        title TEXT,
        prompt_artifact_path TEXT,
        output_artifact_path TEXT NOT NULL,
        depends_on_json TEXT DEFAULT '[]',
        agent_hint TEXT,
        model_hint TEXT,
        state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending','ready','dispatching','started','verifying','blocked','done','failed','skipped')),
        attempt INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        retry_policy_json TEXT DEFAULT '{}',
        side_effect_class TEXT NOT NULL DEFAULT 'pure_artifact'
            CHECK(side_effect_class IN ('pure_artifact','mutating','destructive')),
        verify_gate_json TEXT DEFAULT '{}',
        res_summary TEXT,
        lease_expires_at REAL,
        timeout_sec INTEGER,
        hard_cap_s INTEGER,
        task_key TEXT UNIQUE,
        version INTEGER NOT NULL DEFAULT 0,
        child_pid INTEGER,
        started_at TEXT,
        finished_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL REFERENCES tasks(id),
        request_id TEXT,
        wrapper_sha TEXT,
        child_pid INTEGER,
        created_at TEXT NOT NULL,
        acked_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS progress_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mission_id INTEGER,
        task_id INTEGER,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        detail_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _tx(db_path: PathLike) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: PathLike) -> None:
    """Create all tables (idempotent) and set schema_version meta.

    Safe to call on an existing DB — uses CREATE TABLE IF NOT EXISTS.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _tx(db_path) as conn:
        for stmt in _SCHEMA_SQL:
            conn.execute(stmt)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )


def compute_task_key(target: str, norm_text: str, artifact_path: str) -> str:
    """Stable 16-hex task key (compatible with [A] _compute_task_key pattern).

    Hash inputs are lower-cased + NUL-joined so the key is insensitive to
    case-only differences in agent hint / artifact path.
    """
    raw = (
        (target or "").lower()
        + "\x00"
        + (norm_text or "")
        + "\x00"
        + (artifact_path or "").lower()
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def create_mission(
    db_path: PathLike,
    title: str,
    task_ledger_json: str = "{}",
    created_by: str = "",
    room_path: str = "",
) -> int:
    """Insert a mission in 'draft' state, return its id."""
    now = _utc_iso()
    with _tx(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO missions(title, task_ledger_json, created_by, room_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title, task_ledger_json, created_by, room_path, now, now),
        )
        return int(cur.lastrowid)


def create_task(db_path: PathLike, mission_id: int, **fields: Any) -> Dict[str, Any]:
    """Idempotently insert a task.

    Required field: ``output_artifact_path`` (raises ValueError if missing).
    If ``task_key`` is not supplied it is computed from
    ``target=agent_hint``, ``norm_text=title+prompt_artifact_path``,
    ``artifact=output_artifact_path``.

    Returns ``{"task_id": int, "created": bool}``. If a task with the same
    ``task_key`` already exists, returns the existing id with
    ``created=False`` — no row is mutated.
    """
    if not fields.get("output_artifact_path"):
        raise ValueError("create_task requires output_artifact_path")

    # Compute task_key if not provided.
    if not fields.get("task_key"):
        fields["task_key"] = compute_task_key(
            target=str(fields.get("agent_hint") or ""),
            norm_text=str(fields.get("title") or "") + str(fields.get("prompt_artifact_path") or ""),
            artifact_path=str(fields.get("output_artifact_path") or ""),
        )

    with _tx(db_path) as conn:
        # Idempotent insert within the same tx.
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO tasks (
                mission_id, seq, title, prompt_artifact_path, output_artifact_path,
                depends_on_json, agent_hint, model_hint, state, attempt, max_attempts,
                retry_policy_json, side_effect_class, verify_gate_json, res_summary,
                lease_expires_at, timeout_sec, hard_cap_s, task_key, version,
                child_pid, started_at, finished_at
            ) VALUES (
                :mission_id, :seq, :title, :prompt_artifact_path, :output_artifact_path,
                :depends_on_json, :agent_hint, :model_hint, :state, :attempt, :max_attempts,
                :retry_policy_json, :side_effect_class, :verify_gate_json, :res_summary,
                :lease_expires_at, :timeout_sec, :hard_cap_s, :task_key, :version,
                :child_pid, :started_at, :finished_at
            )
            """,
            {
                "mission_id": mission_id,
                "seq": fields.get("seq", 0),
                "title": fields.get("title"),
                "prompt_artifact_path": fields.get("prompt_artifact_path"),
                "output_artifact_path": fields["output_artifact_path"],
                "depends_on_json": fields.get("depends_on_json", "[]"),
                "agent_hint": fields.get("agent_hint"),
                "model_hint": fields.get("model_hint"),
                "state": fields.get("state", "pending"),
                "attempt": fields.get("attempt", 0),
                "max_attempts": fields.get("max_attempts", 3),
                "retry_policy_json": fields.get("retry_policy_json", "{}"),
                "side_effect_class": fields.get("side_effect_class", "pure_artifact"),
                "verify_gate_json": fields.get("verify_gate_json", "{}"),
                "res_summary": fields.get("res_summary"),
                "lease_expires_at": fields.get("lease_expires_at"),
                "timeout_sec": fields.get("timeout_sec"),
                "hard_cap_s": fields.get("hard_cap_s"),
                "task_key": fields["task_key"],
                "version": fields.get("version", 0),
                "child_pid": fields.get("child_pid"),
                "started_at": fields.get("started_at"),
                "finished_at": fields.get("finished_at"),
            },
        )
        row = conn.execute(
            "SELECT id FROM tasks WHERE task_key = ?",
            (fields["task_key"],),
        ).fetchone()
        task_id = int(row["id"])
        # INSERT OR IGNORE: rowcount is 1 when a new row was inserted,
        # 0 when the task_key already existed (row ignored).
        created = cur.rowcount == 1

    return {"task_id": task_id, "created": created}


def cas_transition(
    db_path: PathLike,
    task_id: int,
    from_state: str,
    to_state: str,
    expect_version: int,
    **set_fields: Any,
) -> bool:
    """Atomic compare-and-set state transition.

    In a single transaction: UPDATE tasks SET state=to_state, version=version+1,
    plus any extra columns in ``set_fields``, WHERE id=? AND state=? AND version=?.
    Returns True iff exactly one row was updated.
    """
    allowed = {
        "title", "seq", "prompt_artifact_path", "output_artifact_path",
        "depends_on_json", "agent_hint", "model_hint", "attempt", "max_attempts",
        "retry_policy_json", "side_effect_class", "verify_gate_json", "res_summary",
        "lease_expires_at", "timeout_sec", "hard_cap_s", "child_pid",
        "started_at", "finished_at",
    }
    extra_cols = []
    extra_vals: list = []
    for k, v in set_fields.items():
        if k not in allowed:
            raise ValueError(f"cas_transition: cannot set field {k!r}")
        extra_cols.append(f"{k} = ?")
        extra_vals.append(v)

    with _tx(db_path) as conn:
        sql = (
            "UPDATE tasks SET state = ?, version = version + 1"
            + (", " + ", ".join(extra_cols) if extra_cols else "")
            + " WHERE id = ? AND state = ? AND version = ?"
        )
        cur = conn.execute(
            sql,
            [to_state, *extra_vals, task_id, from_state, expect_version],
        )
        return cur.rowcount == 1


def append_ledger(
    db_path: PathLike,
    mission_id: Optional[int],
    task_id: Optional[int],
    kind: str,
    detail_json: str = "{}",
) -> int:
    """Append a row to the append-only progress_ledger. Returns ledger id."""
    now = _utc_iso()
    with _tx(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO progress_ledger(mission_id, task_id, ts, kind, detail_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (mission_id, task_id, now, kind, detail_json),
        )
        return int(cur.lastrowid)


def add_outbox(db_path: PathLike, task_id: int, request_id: str, wrapper_sha: str) -> int:
    """Create a dispatch_outbox row. Returns outbox id."""
    now = _utc_iso()
    with _tx(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO dispatch_outbox(task_id, request_id, wrapper_sha, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, request_id, wrapper_sha, now),
        )
        return int(cur.lastrowid)


def set_outbox_child_pid(db_path: PathLike, outbox_id: int, pid: int) -> None:
    with _tx(db_path) as conn:
        conn.execute(
            "UPDATE dispatch_outbox SET child_pid = ? WHERE id = ?",
            (pid, outbox_id),
        )


def ack_outbox(db_path: PathLike, outbox_id: int) -> None:
    now = _utc_iso()
    with _tx(db_path) as conn:
        conn.execute(
            "UPDATE dispatch_outbox SET acked_at = ? WHERE id = ?",
            (now, outbox_id),
        )


def get_task(db_path: PathLike, task_id: int) -> Optional[Dict[str, Any]]:
    with _tx(db_path) as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row is not None else None


def get_mission(db_path: PathLike, mission_id: int) -> Optional[Dict[str, Any]]:
    with _tx(db_path) as conn:
        row = conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
        return dict(row) if row is not None else None


def get_schema_version(db_path: PathLike) -> int:
    with _tx(db_path) as conn:
        # Guard: meta table may not exist yet on an uninitialized db.
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'",
        ).fetchone()
        if exists is None:
            return 0
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", ("schema_version",),
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (ValueError, TypeError):
            return 0
