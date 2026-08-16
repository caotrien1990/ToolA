"""A38: _init_session_db must be LAZY (no import-time DB touch), thread-safe, and
its migration must survive a lock held by a SECOND process.

Background. Before A38 the module ran ``_init_session_db()`` at import time
(store.py bottom). Merely importing the module -- in a test, a script, or a
second [A] process (A28 confirmed MCP is a distinct process writing the same
sessions.db) -- ran the migration DDL, which carries only ``PRAGMA busy_timeout``
and no retry. A second process holding the write lock past that budget made the
*import itself* raise ``sqlite3.OperationalError: database is locked``.

A27 (its sibling) fixed only the WAL switch. The migration statements after it
still had no busy guard, and they now run under a lock that a real second
process can hold arbitrarily long -- so a bounded retry (mirroring A27's
``_enable_wal`` loop) plus a LOUD failure at the end is the fix.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from orchestrator.session import store  # noqa: E402


def _reset_writer() -> None:
    writer = getattr(store, "_SESSION_WRITER", None)
    if writer is not None:
        try:
            writer.close()
        except Exception:
            pass
        store._SESSION_WRITER = None


def _forget(db: Path) -> None:
    store._SESSION_DB_INITIALIZED_PATHS.discard(
        os.path.normcase(os.path.abspath(str(db)))
    )


class ImportIsSideEffectFree(unittest.TestCase):
    """Importing store must NOT create/touch sessions.db (the A38 root cause)."""

    def test_import_does_not_initialize_a_database(self):
        # A fresh interpreter with a never-created DB path. If import still ran
        # the migration, the file would exist afterwards.
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "import_only.db"
            env = dict(os.environ)
            env["ORCHESTRATOR_SESSIONS_DB"] = str(db)
            code = (
                "import sys; sys.path.insert(0, r'%s');"
                "import orchestrator.session.store as s;"
                "import os;"
                "print('DBEXISTS', os.path.exists(r'%s'));"
                "print('WRITER', s._SESSION_WRITER is None)"
            ) % (str(REPO), str(db))
            out = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, env=env, timeout=60,
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("DBEXISTS False", out.stdout)
            self.assertIn("WRITER True", out.stdout)

    def test_importing_while_a_second_process_holds_the_lock_does_not_raise(self):
        # THE regression: reproduce a second [A] process holding the write lock,
        # then import store in a fresh interpreter. Pre-A38 this raised
        # OperationalError at import time (store.py migration DDL).
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "sessions.db"
            ready = Path(tmp) / "ready.flag"
            env = dict(os.environ)
            env["ORCHESTRATOR_SESSIONS_DB"] = str(db)
            holder = subprocess.Popen(
                [sys.executable, "-c", _HOLDER_SRC, str(db), str(ready), "3"],
                env=env,
            )
            try:
                for _ in range(200):
                    if ready.exists():
                        break
                    time.sleep(0.05)
                self.assertTrue(ready.exists(), "holder never acquired the lock")
                code = (
                    "import sys; sys.path.insert(0, r'%s');"
                    "import orchestrator.session.store;"
                    "print('IMPORT_OK')"
                ) % str(REPO)
                out = subprocess.run(
                    [sys.executable, "-c", code],
                    capture_output=True, text=True, env=env, timeout=60,
                )
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertIn("IMPORT_OK", out.stdout)
            finally:
                holder.wait(timeout=30)


class ConcurrentInit(unittest.TestCase):
    """Two threads entering _init_session_db on a fresh DB must both succeed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = store._SESSIONS_DB
        _reset_writer()

    def tearDown(self):
        _reset_writer()
        store._SESSIONS_DB = self._old_db
        try:
            self._tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_two_threads_init_the_same_fresh_db(self):
        # Model the real race: two threads reach _init_session_db at the same
        # instant on a fresh DB with no writer yet. Neither may raise, and the
        # schema must be complete exactly once.
        for round_index in range(8):
            db = Path(self._tmp.name) / f"race_{round_index}.db"
            store._SESSIONS_DB = db
            _forget(db)
            _reset_writer()
            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def init_once(_i):
                try:
                    barrier.wait(timeout=10)
                    store._init_session_db()
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(init_once, range(2)))
            self.assertEqual(errors, [], f"round {round_index}: {errors}")

            conn = sqlite3.connect(str(db))
            try:
                names = {
                    r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                conn.close()
            for table in ("sessions", "events", "task_checkpoints",
                          "pending_audits", "write_operations"):
                self.assertIn(table, names, f"round {round_index}")
            _reset_writer()

    def test_second_call_is_a_noop_after_first_init(self):
        db = Path(self._tmp.name) / "idem.db"
        store._SESSIONS_DB = db
        _forget(db)
        _reset_writer()
        store._init_session_db()
        self.assertIn(
            os.path.normcase(os.path.abspath(str(db))),
            store._SESSION_DB_INITIALIZED_PATHS,
        )
        # Second call must short-circuit -- prove it does not reconnect by
        # spying on sqlite3.connect at the module attribute the code reads.
        calls = {"n": 0}
        real_connect = store.sqlite3.connect

        def counting_connect(*a, **k):
            calls["n"] += 1
            return real_connect(*a, **k)

        store.sqlite3.connect = counting_connect
        try:
            store._init_session_db()
        finally:
            store.sqlite3.connect = real_connect
        self.assertEqual(calls["n"], 0, "second init reconnected to the DB")


class MigrationLockRetry(unittest.TestCase):
    """The migration must retry a contended lock and, if it never frees, fail LOUD."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = store._SESSIONS_DB
        _reset_writer()

    def tearDown(self):
        _reset_writer()
        store._SESSIONS_DB = self._old_db
        try:
            self._tmp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_transient_second_process_lock_is_ridden_out(self):
        # A second process holds the lock briefly, then releases; the retry loop
        # must wait it out and complete the schema rather than dying.
        db = Path(self._tmp.name) / "transient.db"
        ready = Path(self._tmp.name) / "ready_t.flag"
        store._SESSIONS_DB = db
        _forget(db)
        env = dict(os.environ)
        env["ORCHESTRATOR_SESSIONS_DB"] = str(db)
        holder = subprocess.Popen(
            [sys.executable, "-c", _HOLDER_SRC, str(db), str(ready), "0.4"],
            env=env,
        )
        try:
            for _ in range(200):
                if ready.exists():
                    break
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "holder never acquired the lock")
            store._init_session_db()  # must NOT raise
        finally:
            holder.wait(timeout=30)
        conn = sqlite3.connect(str(db))
        try:
            names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()
        self.assertIn("write_operations", names)

    def test_lock_held_past_one_busy_budget_needs_more_than_one_attempt(self):
        # Sabotage M8 exposed this gap: a lock held UNDER one PRAGMA busy_timeout
        # (750ms) is ridden out by a single attempt, so a 1-shot migration passes
        # and the retry loop is never proven. Hold the lock ~1.4s -- longer than
        # one busy budget, shorter than the whole retry budget -- so the FIRST
        # attempt must fail on its busy_timeout and only a RETRY can complete the
        # schema. A single-attempt (no-retry) implementation raises here.
        db = Path(self._tmp.name) / "multi.db"
        ready = Path(self._tmp.name) / "ready_m.flag"
        store._SESSIONS_DB = db
        _forget(db)
        env = dict(os.environ)
        env["ORCHESTRATOR_SESSIONS_DB"] = str(db)
        holder = subprocess.Popen(
            [sys.executable, "-c", _HOLDER_SRC, str(db), str(ready), "1.4"],
            env=env,
        )
        try:
            for _ in range(200):
                if ready.exists():
                    break
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "holder never acquired the lock")
            store._init_session_db()  # must NOT raise -- retry rides it out
        finally:
            holder.wait(timeout=30)
        conn = sqlite3.connect(str(db))
        try:
            names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            conn.close()
        self.assertIn("write_operations", names)

    def test_sustained_lock_fails_loud_with_contention_error(self):
        # A lock held past the whole retry budget must surface a typed, LOUD
        # error -- never a silently half-migrated DB and never a bare
        # OperationalError the callers cannot classify.
        db = Path(self._tmp.name) / "stuck.db"
        ready = Path(self._tmp.name) / "ready_s.flag"
        store._SESSIONS_DB = db
        _forget(db)
        env = dict(os.environ)
        env["ORCHESTRATOR_SESSIONS_DB"] = str(db)
        holder = subprocess.Popen(
            [sys.executable, "-c", _HOLDER_SRC, str(db), str(ready), "15"],
            env=env,
        )
        try:
            for _ in range(200):
                if ready.exists():
                    break
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "holder never acquired the lock")
            with self.assertRaises(store.SessionStoreContentionError):
                store._init_session_db()
            # And it must not have recorded the path as initialized.
            self.assertNotIn(
                os.path.normcase(os.path.abspath(str(db))),
                store._SESSION_DB_INITIALIZED_PATHS,
            )
        finally:
            holder.terminate()
            try:
                holder.wait(timeout=30)
            except Exception:
                holder.kill()


# A second-process lock holder. Takes and HOLDS a write lock (BEGIN IMMEDIATE)
# on the target DB, signals readiness via a flag file, then sleeps N seconds.
_HOLDER_SRC = r"""
import sqlite3, sys, time
db, ready, hold = sys.argv[1], sys.argv[2], float(sys.argv[3])
c = sqlite3.connect(db, timeout=30)
c.execute("PRAGMA journal_mode=WAL")
c.execute("CREATE TABLE IF NOT EXISTS probe(x INTEGER)")
c.commit()
c.execute("BEGIN IMMEDIATE")
c.execute("INSERT INTO probe(x) VALUES (1)")
open(ready, "w").write("locked")
time.sleep(hold)
c.rollback(); c.close()
"""


if __name__ == "__main__":
    unittest.main()
