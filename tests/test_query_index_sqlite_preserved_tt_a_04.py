"""TT-A-04 regression: refresh_project_map must not attempt to delete SQLite
artifacts under .orchestrator_cache, so query_index(auto_refresh=true) works
while durable_audit.db / sessions.db / missions.db are open in WAL mode.

Why: prior PRESERVED_CACHE_FILES listed sessions.db and graph.db* explicitly
but omitted their -wal/-shm/-journal sidecars and omitted durable_audit.db
and missions.db entirely. On Windows, unlinking a WAL-mode SQLite file held
by another connection raises WinError 32 (sharing violation), which surfaced
as AUTO_REFRESH_FAILED in query_index. The fix makes the boundary
deterministic: ANY file whose basename ends in .db, .db-wal, .db-shm, or
.db-journal under .orchestrator_cache is preserved by suffix match, so new
SQLite stores don't need a manifest update to be safe.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import surgical_mapper


class TestCachePathIsPreservedSqlite(unittest.TestCase):
    """Boundary invariant: every SQLite suffix is preserved by pattern."""

    def test_plain_db_preserved(self) -> None:
        self.assertTrue(surgical_mapper._cache_path_is_preserved("durable_audit.db"))
        self.assertTrue(surgical_mapper._cache_path_is_preserved("sessions.db"))
        self.assertTrue(surgical_mapper._cache_path_is_preserved("graph.db"))
        self.assertTrue(surgical_mapper._cache_path_is_preserved("missions.db"))
        self.assertTrue(surgical_mapper._cache_path_is_preserved("any_future_store.db"))

    def test_wal_shm_journal_preserved(self) -> None:
        for sidecar in (".db-wal", ".db-shm", ".db-journal"):
            self.assertTrue(
                surgical_mapper._cache_path_is_preserved(f"durable_audit{sidecar}"),
                f"sidecar {sidecar} must be preserved",
            )
            self.assertTrue(
                surgical_mapper._cache_path_is_preserved(f"sessions{sidecar}"),
            )
            self.assertTrue(
                surgical_mapper._cache_path_is_preserved(f"missions{sidecar}"),
            )

    def test_nested_db_preserved(self) -> None:
        """A .db under a subdirectory of .orchestrator_cache must also be preserved."""
        self.assertTrue(
            surgical_mapper._cache_path_is_preserved("nested/dir/state.db")
        )
        self.assertTrue(
            surgical_mapper._cache_path_is_preserved("nested/dir/state.db-wal")
        )

    def test_non_sqlite_cache_files_still_purged(self) -> None:
        """Regular cache JSON artifacts (the actual targets of the purge) must
        remain deletable — the invariant is not 'preserve everything'."""
        self.assertFalse(
            surgical_mapper._cache_path_is_preserved("some_source.py.json")
        )
        self.assertFalse(
            surgical_mapper._cache_path_is_preserved("README.md.json")
        )

    def test_non_db_files_preserved_list_still_works(self) -> None:
        """The explicit PRESERVED_CACHE_FILES entries are unaffected."""
        self.assertTrue(
            surgical_mapper._cache_path_is_preserved("WORKFLOW_STATE.json")
        )
        self.assertTrue(
            surgical_mapper._cache_path_is_preserved("orchestrator.lock")
        )


class TestRefreshPreservesOpenSqlite(unittest.TestCase):
    """End-to-end: refresh_project_map succeeds while a .db handle is open."""

    def test_refresh_with_open_durable_audit_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / ".orchestrator_cache"
            cache.mkdir()
            # A source file to be (re)scanned, so refresh has real work to do.
            (root / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
            # A stale cache JSON artifact that SHOULD be purged by refresh.
            stale_cache = cache / "app.py.json"
            stale_cache.write_text("{}", encoding="utf-8")
            # An in-use SQLite store simulating durable_audit.db.
            db_path = cache / "durable_audit.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO t DEFAULT VALUES")
                conn.commit()
                # Sidecars exist now (WAL mode).
                self.assertTrue(db_path.exists())
                # Refresh must not raise, and must not delete the open .db.
                result = surgical_mapper.refresh_project_map(str(root))
                self.assertIsInstance(result, dict)
                self.assertTrue(
                    db_path.exists(),
                    "durable_audit.db was deleted by refresh_project_map",
                )
                # Normal source change still detected: app.py cache artifact regenerated.
                regenerated = cache / "app.py.json"
                self.assertTrue(
                    regenerated.exists() or (cache / "INDEX.json").exists(),
                    "refresh did not produce any cache/index artifacts",
                )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
