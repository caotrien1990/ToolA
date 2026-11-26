"""2DEC cleanup 2: the INSERT / spool-recovery branch must self-ensure the
write-ledger schema, exactly as the SELECT branch (db_get / db_list) already does.

WHY this file exists. CI-2DEC (commit 91244b1) added _ensure_write_ledger_schema()
before the SELECT in _write_ledger_db_get / _write_ledger_db_list, but
_write_ledger_db_upsert_once -- the single funnel for EVERY ledger INSERT (direct
persist, _write_ledger_persist_terminal, spool drain, memory-gap replay) -- connected
and INSERTed without ensuring the schema. On a fresh / uninitialized sessions DB
(in-process dispatch, out-of-project xroot target, cross-drive TEMP where
find_project_root resolves no project -- the same scenarios the SELECT fix names)
the INSERT raised "no such table: write_operations", was swallowed by the broad
except, returned False, and the row fell back to the spool. The spool-recovery loop
then replayed through the SAME un-ensured INSERT, so the spool could never drain
into sqlite until some unrelated SELECT happened to run ensure -- a permanent
durability gap. The fix extends the same idempotent, thread-safe ensure call to the
INSERT funnel; these tests fail before it and pass after.

Each test names the mutant it kills:
- fresh_db_upsert_once        -> INSERT funnel without ensure (the shipped gap)
- fresh_db_upsert_wrapper     -> spool-drain / memory-gap entry without ensure
- fresh_db_spool_drain        -> the recovery replay path end-to-end on a fresh DB

No mocks: every test points the real code at a real, genuinely uninitialized sqlite
file and asserts the real persisted row. The DB path is repointed with the same
monkeypatch-of-alias pattern the existing ledger tests (cf07, canonical_target,
lazy_init_a38) use; the code under test is never stubbed.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import code_tools as ct
import orchestrator_server as srv
from orchestrator.session import store
from orchestrator.storage.spool import append_spool, drain_spool, spool_depth


def _rec(op_id: str) -> dict:
    now = time.time()
    return {
        "operation_id": op_id, "tool": "write_fix", "status": "committed",
        "project_path": r"d:\repo", "file_path": r"pkg\mod.py",
        "session_id": "sess-2dec", "started_at": now, "finished_at": now,
        "duration_ms": 1, "attempts": 1, "previous_status": None,
        "result": {"ok": True}, "origin_generation": srv.SERVER_GENERATION,
        "updated_at": now,
    }


class InsertSelfEnsureSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "fresh_sessions.db"
        self._old_ct_db = ct._SESSIONS_DB
        self._db_key = os.path.normcase(os.path.abspath(str(self.db)))
        store.close_writer()
        ct._SESSIONS_DB = self.db
        ct._sync_session_db_alias()
        # Force the genuine fresh-DB state: the init short-circuit cache and any
        # cached failure must not claim this path is already initialized.
        store._SESSION_DB_INITIALIZED_PATHS.discard(self._db_key)
        store._SESSION_INIT_FAILURES.pop(self._db_key, None)

    def tearDown(self):
        store.close_writer()
        ct._SESSIONS_DB = self._old_ct_db
        ct._sync_session_db_alias()
        store._SESSION_DB_INITIALIZED_PATHS.discard(self._db_key)
        store._SESSION_INIT_FAILURES.pop(self._db_key, None)
        self.temp_dir.cleanup()

    def _table_names(self):
        with sqlite3.connect(str(self.db)) as conn:
            return {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    def _count(self, op_id):
        with sqlite3.connect(str(self.db)) as conn:
            return conn.execute(
                "SELECT count(*) FROM write_operations WHERE operation_id=?",
                (op_id,)).fetchone()[0]

    def test_fresh_db_upsert_once_self_ensures(self):
        self.assertNotIn("write_operations", self._table_names())
        ok = srv._write_ledger_db_upsert_once(_rec("op-once"))
        self.assertTrue(ok, "INSERT on a fresh DB must self-ensure the schema and succeed")
        self.assertIn("write_operations", self._table_names())
        self.assertEqual(self._count("op-once"), 1)

    def test_fresh_db_upsert_wrapper_self_ensures(self):
        # _write_ledger_db_upsert is the spool-drain / memory-gap replay entry.
        self.assertNotIn("write_operations", self._table_names())
        ok = srv._write_ledger_db_upsert(_rec("op-retry"))
        self.assertTrue(ok, "the retry wrapper (spool-drain entry) must self-ensure on a fresh DB")
        self.assertEqual(self._count("op-retry"), 1)

    def test_fresh_db_spool_drain_replays_into_sqlite(self):
        # Mirror _write_ledger_schedule_recovery exactly: a real spool file drained
        # through the same lambda the recovery thread uses, on a fresh DB.
        self.assertNotIn("write_operations", self._table_names())
        spool = srv._write_ledger_spool_path(str(self.db))
        self.assertTrue(append_spool(spool, _rec("op-spool"), max_records=0))
        self.assertEqual(spool_depth(spool), 1)
        result = drain_spool(
            spool, lambda rec: srv._write_ledger_db_upsert(rec, str(self.db)),
            max_records=32, max_seconds=1, stop_on_refusal=True)
        self.assertEqual(result["replayed"], 1, "the spooled row must replay into sqlite")
        self.assertEqual(spool_depth(spool), 0, "the spool must drain, not retain the row")
        self.assertEqual(self._count("op-spool"), 1)


if __name__ == "__main__":
    unittest.main()
