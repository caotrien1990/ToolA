"""P4 soft-data tools — read-only file/data inspection.

Covers query_sqlite / file_profile / sample_data_file / read_chunks behavior,
the read-only write-rejection guard, and the 4-layer registration (TOOL_MAP +
TOOL_SCHEMAS + TOOL_PHASES tier T1). Added when P4 was wired into code_tools.
"""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest

import tools_upgrade_p4 as p4


def _run(coro):
    return asyncio.run(coro)


class TestQuerySqlite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany("INSERT INTO items (name) VALUES (?)",
                         [("a",), ("b",), ("c",)])
        conn.commit()
        conn.close()

    def test_schema_listing_when_empty_sql(self):
        r = json.loads(_run(p4.query_sqlite(db_path=self.db)))
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"], "schema")
        self.assertIn("items", [t["name"] for t in r["tables"]])

    def test_bounded_select(self):
        r = json.loads(_run(p4.query_sqlite(db_path=self.db, sql="SELECT * FROM items")))
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"], "rows")
        self.assertEqual(r["row_count"], 3)

    def test_limit_applied(self):
        r = json.loads(_run(p4.query_sqlite(db_path=self.db,
                                            sql="SELECT * FROM items", limit=2)))
        self.assertTrue(r["ok"])
        self.assertEqual(r["row_count"], 2)

    def test_write_query_rejected(self):
        r = json.loads(_run(p4.query_sqlite(db_path=self.db, sql="DELETE FROM items")))
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_name"], "WRITE_QUERY_REJECTED")

    def test_write_keyword_inside_rejected(self):
        r = json.loads(_run(p4.query_sqlite(db_path=self.db,
                                            sql="SELECT 1; DROP TABLE items")))
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_name"], "WRITE_QUERY_REJECTED")

    def test_missing_db(self):
        r = json.loads(_run(p4.query_sqlite(db_path=self.db + ".nope")))
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_name"], "PATH_NOT_FOUND")

    # -- B009 (2026-08-20): lock contention is a wait, not a failure ---------

    def _hold_write_lock(self):
        """Open an EXCLUSIVE write transaction on self.db and keep it open."""
        holder = sqlite3.connect(self.db, timeout=1)
        holder.execute("BEGIN EXCLUSIVE")
        holder.execute("INSERT INTO items (name) VALUES ('held')")
        return holder

    def test_locked_db_is_retryable_not_a_generic_error(self):
        """A busy writer must answer SQLITE_DATABASE_LOCKED + retryable:true.

        busy_timeout is shrunk to 50ms so the test measures the classification,
        not the 20s production wait.
        """
        original = p4._SQLITE_BUSY_TIMEOUT_MS
        holder = self._hold_write_lock()
        try:
            p4._SQLITE_BUSY_TIMEOUT_MS = 50
            r = json.loads(_run(p4.query_sqlite(db_path=self.db,
                                                sql="SELECT * FROM items")))
        finally:
            p4._SQLITE_BUSY_TIMEOUT_MS = original
            holder.rollback()
            holder.close()
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["error_name"], "SQLITE_DATABASE_LOCKED", r)
        self.assertTrue(r["retryable"], r)
        self.assertEqual(r["busy_timeout_ms"], 50, r)
        self.assertIn("ca_next_action", r)

    def test_busy_timeout_constant_drives_the_effective_wait(self):
        """The constant must reach connect() and show up as the live busy_timeout.

        Asserting on the *effect* (what the connection reports) rather than on a
        line of code: an earlier version of this fix also issued an explicit
        PRAGMA, and a mutant that deleted it survived every test -- because
        connect(timeout=N) already installs busy_timeout=N*1000. Measure the
        wait, not the decoration.
        """
        seen = {}
        real_connect = sqlite3.connect

        def spy(*a, **kw):
            seen["timeout"] = kw.get("timeout")
            return real_connect(*a, **kw)

        original = p4._SQLITE_BUSY_TIMEOUT_MS
        p4._SQLITE_BUSY_TIMEOUT_MS = 7000
        p4.sqlite3.connect = spy
        try:
            r = json.loads(_run(p4.query_sqlite(db_path=self.db,
                                                sql="PRAGMA busy_timeout")))
        finally:
            p4.sqlite3.connect = real_connect
            p4._SQLITE_BUSY_TIMEOUT_MS = original
        self.assertTrue(r["ok"], r)
        self.assertEqual(seen.get("timeout"), 7.0,
                         "connect() did not receive the constant: %r" % (seen,))
        self.assertEqual(r["rows"][0]["timeout"], 7000,
                         "the effective busy_timeout is not the constant: %r"
                         % (r["rows"],))

    def test_non_lock_operational_error_stays_a_hard_error(self):
        """Only lock/busy text is retryable; a bad table name must not be."""
        r = json.loads(_run(p4.query_sqlite(db_path=self.db,
                                            sql="SELECT * FROM no_such_table")))
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["error_name"], "QUERY_SQLITE_ERROR", r)
        self.assertFalse(r["retryable"], r)


class TestFileProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_text_profile(self):
        p = os.path.join(self.tmp, "a.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write("x = 1\ny = 2\n")
        r = json.loads(_run(p4.file_profile(file_path=p)))
        self.assertTrue(r["ok"])
        self.assertFalse(r["is_binary"])
        self.assertEqual(r["language"], "python")
        self.assertEqual(r["line_count"], 2)

    def test_binary_profile(self):
        p = os.path.join(self.tmp, "b.bin")
        with open(p, "wb") as f:
            f.write(b"\x00\x01\x02\x00abc")
        r = json.loads(_run(p4.file_profile(file_path=p)))
        self.assertTrue(r["ok"])
        self.assertTrue(r["is_binary"])

    def test_directory_rejected(self):
        r = json.loads(_run(p4.file_profile(file_path=self.tmp)))
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_name"], "IS_DIRECTORY")


class TestSampleDataFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv = os.path.join(self.tmp, "d.csv")
        with open(self.csv, "w", encoding="utf-8") as f:
            f.write("col1,col2\n" + "".join(f"{i},v{i}\n" for i in range(20)))

    def test_head(self):
        r = json.loads(_run(p4.sample_data_file(file_path=self.csv, n=3, mode="head")))
        self.assertTrue(r["ok"])
        self.assertEqual(r["returned"], 3)
        self.assertIsNotNone(r["parsed"])  # csv parsed

    def test_tail(self):
        r = json.loads(_run(p4.sample_data_file(file_path=self.csv, n=2, mode="tail")))
        self.assertTrue(r["ok"])
        self.assertEqual(r["returned"], 2)

    def test_missing(self):
        r = json.loads(_run(p4.sample_data_file(file_path=self.csv + ".x")))
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_name"], "PATH_NOT_FOUND")


class TestReadChunks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.f = os.path.join(self.tmp, "big.txt")
        with open(self.f, "w", encoding="utf-8") as fh:
            fh.write("".join(f"line{i}\n" for i in range(100)))

    def test_line_chunk_bounded(self):
        r = json.loads(_run(p4.read_chunks(file_path=self.f, by="line", start=0, count=10)))
        self.assertTrue(r["ok"])
        self.assertEqual(r["returned_lines"], 10)
        self.assertTrue(r["has_more"])
        self.assertEqual(r["lines"][0], "line0")

    def test_line_chunk_offset(self):
        r = json.loads(_run(p4.read_chunks(file_path=self.f, by="line", start=95, count=10)))
        self.assertTrue(r["ok"])
        self.assertEqual(r["returned_lines"], 5)
        self.assertFalse(r["has_more"])

    def test_byte_chunk(self):
        r = json.loads(_run(p4.read_chunks(file_path=self.f, by="byte", start=0, count=6)))
        self.assertTrue(r["ok"])
        self.assertEqual(r["returned_bytes"], 6)
        self.assertTrue(r["has_more"])


class TestP4Registration(unittest.TestCase):
    NAMES = ("query_sqlite", "file_profile", "sample_data_file", "read_chunks")

    def test_registered_all_layers(self):
        import code_tools as ct
        schema_names = {s.get("function", {}).get("name") for s in ct.TOOL_SCHEMAS}
        for name in self.NAMES:
            self.assertIn(name, ct.TOOL_MAP, f"{name} missing from TOOL_MAP")
            self.assertTrue(callable(ct.TOOL_MAP[name]))
            self.assertEqual(ct.TOOL_PHASES.get(name), "T1", f"{name} tier != T1")
            self.assertIn(name, schema_names, f"{name} missing schema")

    def test_read_only_not_protected(self):
        import code_tools as ct
        for name in self.NAMES:
            self.assertNotIn(name, ct.PROTECTED_TOOL_NAMES,
                             f"{name} should be unprotected T1 read")


if __name__ == "__main__":
    unittest.main()
