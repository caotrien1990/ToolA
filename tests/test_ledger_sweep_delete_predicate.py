"""Mock-DB tests for the ledger_sweep outbox DELETE predicate (Item 1,
LEDGER_FOLLOWUPS_CLAIM_OPE_20261121.md).

Exercises the PRODUCTION predicate imported from ledger_sweep.py (outbox_delete_sql),
never a duplicated copy of the SQL, and never opens the real durable_audit.db:
every case runs against a fresh tempfile sqlite database with the production schema.
"""
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ledger_sweep  # noqa: E402

HOUR = 3600.0

SCHEMA = """
CREATE TABLE durable_audit_outbox (
    audit_id TEXT PRIMARY KEY,
    kind TEXT,
    payload_json TEXT,
    state TEXT CHECK (state IN ('pending', 'applied')),
    created_at REAL,
    applied_at REAL,
    attempt_count INTEGER,
    last_error TEXT,
    lease_owner TEXT,
    lease_until REAL,
    next_attempt_at REAL
)
"""


def make_db(tmp_path, rows):
    db = tmp_path / "mock_durable_audit.db"
    con = sqlite3.connect(str(db))
    con.execute(SCHEMA)
    con.executemany(
        "INSERT INTO durable_audit_outbox (audit_id, kind, payload_json, state, created_at) "
        "VALUES (?, 'test', '{}', ?, ?)", rows)
    con.commit()
    return db, con


def run_production_delete(con, cutoff):
    """Run the production chunked DELETE loop exactly as the nightly sweep does."""
    deleted = 0
    while True:
        cur = con.execute(ledger_sweep.outbox_delete_sql(), (cutoff,))
        con.commit()
        n = cur.rowcount or 0
        deleted += n
        if n < ledger_sweep.CHUNK:
            break
    return deleted


def survivors(con):
    return {r[0] for r in con.execute("SELECT audit_id FROM durable_audit_outbox")}


def test_applied_older_than_48h_deleted(tmp_path):
    now = time.time()
    db, con = make_db(tmp_path, [("old-applied", "applied", now - 72 * HOUR)])
    try:
        assert run_production_delete(con, now - 48 * HOUR) == 1
        assert survivors(con) == set()
    finally:
        con.close()


def test_applied_within_48h_kept(tmp_path):
    now = time.time()
    db, con = make_db(tmp_path, [("fresh-applied", "applied", now - 1 * HOUR)])
    try:
        assert run_production_delete(con, now - 48 * HOUR) == 0
        assert survivors(con) == {"fresh-applied"}
    finally:
        con.close()


def test_applied_exactly_at_cutoff_deleted(tmp_path):
    now = time.time()
    cutoff = now - 48 * HOUR
    db, con = make_db(tmp_path, [("boundary-applied", "applied", cutoff)])
    try:
        assert run_production_delete(con, cutoff) == 1  # predicate uses <=
        assert survivors(con) == set()
    finally:
        con.close()


def test_applied_just_inside_48h_kept(tmp_path):
    now = time.time()
    cutoff = now - 48 * HOUR
    db, con = make_db(tmp_path, [("inside-applied", "applied", cutoff + 0.001)])
    try:
        assert run_production_delete(con, cutoff) == 0
        assert survivors(con) == {"inside-applied"}
    finally:
        con.close()


def test_pending_old_kept(tmp_path):
    now = time.time()
    db, con = make_db(tmp_path, [("old-pending", "pending", now - 200 * HOUR)])
    try:
        assert run_production_delete(con, now - 48 * HOUR) == 0  # design K-2
        assert survivors(con) == {"old-pending"}
    finally:
        con.close()


def test_pending_recent_kept(tmp_path):
    now = time.time()
    db, con = make_db(tmp_path, [("fresh-pending", "pending", now - 1 * HOUR)])
    try:
        assert run_production_delete(con, now - 48 * HOUR) == 0
        assert survivors(con) == {"fresh-pending"}
    finally:
        con.close()


def test_null_created_at_kept(tmp_path):
    db, con = make_db(tmp_path, [("null-applied", "applied", None)])
    try:
        assert run_production_delete(con, time.time() - 48 * HOUR) == 0
        assert survivors(con) == {"null-applied"}
    finally:
        con.close()


def test_text_created_at_kept(tmp_path):
    db, con = make_db(tmp_path, [("text-applied", "applied", "2026-11-01T00:00:00")])
    try:
        assert run_production_delete(con, time.time() - 48 * HOUR) == 0  # typeof = 'real' guard
        assert survivors(con) == {"text-applied"}
    finally:
        con.close()


def test_mixed_population_exact_selection(tmp_path):
    now = time.time()
    cutoff = now - 48 * HOUR
    rows = [
        ("del-old", "applied", now - 100 * HOUR),
        ("del-boundary", "applied", cutoff),
        ("keep-fresh", "applied", cutoff + 1.0),
        ("keep-pending-old", "pending", now - 100 * HOUR),
        ("keep-null", "applied", None),
        ("keep-text", "applied", "2026-11-01T00:00:00"),
    ]
    db, con = make_db(tmp_path, rows)
    try:
        assert run_production_delete(con, cutoff) == 2
        assert survivors(con) == {"keep-fresh", "keep-pending-old", "keep-null", "keep-text"}
    finally:
        con.close()


def test_chunking_deletes_all_eligible(tmp_path):
    now = time.time()
    cutoff = now - 48 * HOUR
    n = ledger_sweep.CHUNK + 5
    rows = [("bulk-%05d" % i, "applied", cutoff - (n - i) * HOUR) for i in range(n)]
    db, con = make_db(tmp_path, rows)
    try:
        assert run_production_delete(con, cutoff) == n
        assert survivors(con) == set()
    finally:
        con.close()
