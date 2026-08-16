"""A27: concurrent _init_session_db must not die on 'database is locked'.

Found while validating the A26 fix. Once session_id collisions were gone, the
same concurrent test still failed ~1 run in 10 with::

    File "orchestrator/session/store.py", line 586, in _configure_sessions_connection
        conn.execute("PRAGMA journal_mode=WAL")
    sqlite3.OperationalError: database is locked

Two facts make this reachable:

1. ``PRAGMA journal_mode=WAL`` needs an EXCLUSIVE lock on the database, and
   SQLite does NOT apply ``busy_timeout`` to the journal-mode switch -- it
   fails immediately rather than waiting. So a 750ms (or 5000ms) busy budget
   buys nothing here.
2. ``_init_session_db`` is entered concurrently. ``open_session`` calls it on
   every request, and its early-out only skips when a live ``_SESSION_WRITER``
   already points at the wanted path. On a fresh database two threads both see
   "no writer yet" and both run the migration.

The failure escapes every contention guard the module has: it happens on a
direct ``sqlite3.connect``, not on the serialized writer, so it is never
wrapped in ``SessionStoreContentionError`` -- the caller sees a bare
``INTERNAL_ERROR: database is locked``.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.session import store  # noqa: E402


def _close_writer() -> None:
    writer = getattr(store, "_SESSION_WRITER", None)
    if writer is not None:
        try:
            writer.close()
        except Exception:
            pass
        store._SESSION_WRITER = None


class ConfigureConnectionTests(unittest.TestCase):
    """_configure_sessions_connection must survive a contended WAL switch."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = Path(self._tmp.name) / "wal_a27.db"
        self._db.parent.mkdir(parents=True, exist_ok=True)
        # Materialize the file in rollback-journal mode so the WAL switch is a
        # real transition rather than a no-op on an already-WAL database.
        seed = sqlite3.connect(str(self._db))
        seed.execute("PRAGMA journal_mode=DELETE")
        seed.execute("CREATE TABLE IF NOT EXISTS probe (x INTEGER)")
        seed.commit()
        seed.close()

    def tearDown(self):
        try:
            self._tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_wal_switch_blocked_by_a_reader_does_not_raise(self):
        # A live reader holds a SHARED lock, so the EXCLUSIVE lock the WAL
        # switch needs is unavailable. The old code raised here.
        blocker = sqlite3.connect(str(self._db))
        blocker.execute("BEGIN")
        blocker.execute("SELECT * FROM probe").fetchall()
        try:
            conn = sqlite3.connect(str(self._db), timeout=0.25)
            try:
                store._configure_sessions_connection(conn, 250)
            finally:
                conn.close()
        finally:
            blocker.rollback()
            blocker.close()

    def test_wal_is_still_enabled_when_uncontended(self):
        # Degrading gracefully must not mean silently skipping WAL on the happy
        # path -- WAL is what lets readers run during a write.
        conn = sqlite3.connect(str(self._db))
        try:
            store._configure_sessions_connection(conn, 750)
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(mode.lower(), "wal")

    def test_other_pragmas_are_applied_even_if_wal_is_contended(self):
        blocker = sqlite3.connect(str(self._db))
        blocker.execute("BEGIN")
        blocker.execute("SELECT * FROM probe").fetchall()
        try:
            conn = sqlite3.connect(str(self._db), timeout=0.25)
            try:
                store._configure_sessions_connection(conn, 1234)
                busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                temp_store = conn.execute("PRAGMA temp_store").fetchone()[0]
            finally:
                conn.close()
        finally:
            blocker.rollback()
            blocker.close()
        self.assertEqual(busy, 1234)
        self.assertEqual(temp_store, 2)  # 2 == MEMORY


class ConcurrentInitTests(unittest.TestCase):
    """The real failure: several threads initializing one fresh database."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = store._SESSIONS_DB
        _close_writer()

    def tearDown(self):
        _close_writer()
        store._SESSIONS_DB = self._old_db
        try:
            self._tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_concurrent_init_on_a_fresh_db_never_raises(self):
        for round_index in range(12):
            db = Path(self._tmp.name) / f"fresh_{round_index}.db"
            store._SESSIONS_DB = db
            store._SESSION_DB_INITIALIZED_PATHS.discard(
                __import__("os").path.normcase(__import__("os").path.abspath(str(db)))
            )
            _close_writer()
            barrier = threading.Barrier(4)

            def init_once(_i):
                barrier.wait(timeout=10)
                store._init_session_db()
                return True

            with ThreadPoolExecutor(max_workers=4) as pool:
                # .result() re-raises; a bare OperationalError fails the test.
                results = [f for f in pool.map(init_once, range(4))]
            self.assertTrue(all(results))
            _close_writer()

    def test_schema_is_complete_after_concurrent_init(self):
        # Tolerating the lock must not leave a half-migrated database behind.
        db = Path(self._tmp.name) / "schema.db"
        store._SESSIONS_DB = db
        barrier = threading.Barrier(4)

        def init_once(_i):
            barrier.wait(timeout=10)
            store._init_session_db()

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(init_once, range(4)))

        conn = sqlite3.connect(str(db))
        try:
            names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()
        for table in ("sessions", "events", "task_checkpoints", "pending_audits"):
            self.assertIn(table, names)


if __name__ == "__main__":
    unittest.main()
