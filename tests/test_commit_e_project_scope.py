# Commit E (S3-CONTRACT-01) regression tests.
#
# WHY: get_usage_stats + list_sessions used to silently downgrade a
# zero-payload call to project_path='.' via _PROJECT_PATH_DEFAULTABLE, then
# return whatever sessions/events were reachable from the orchestrator's
# CWD — cross-project data. The fix:
#   1) Removes both tools from _PROJECT_PATH_DEFAULTABLE in
#      orchestrator_server.py (no more silent '.' injection).
#   2) Adds _resolve_session_query_scope() in code_tools.py; both handlers
#      require explicit project_path, canonicalize via _session_project_key
#      (same normalization used when rows are persisted), and fail loud on
#      missing/empty/invalid scope.
#   3) scope='all' (cross-project global) is DEFERRED until an orchestrator
#      token gate + audit event is wired in. It returns
#      GLOBAL_SCOPE_NOT_ENABLED with NO silent fallback to project scope.
#
# These tests pin those invariants at the code_tools handler layer (the
# project-filter predicate). The dispatcher-layer defaultable set is asserted
# via source inspection so the regression cannot return.
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import code_tools


def _make_db(path: Path) -> None:
    """Build a minimal sessions+events DB with rows for projects A and B."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """CREATE TABLE sessions (
                   session_id TEXT PRIMARY KEY,
                   session_title TEXT NOT NULL,
                   project_path TEXT,
                   mode TEXT DEFAULT 'T1',
                   status TEXT DEFAULT 'active',
                   coverage TEXT DEFAULT 'A-only',
                   started_at TEXT,
                   ended_at TEXT,
                   agent_id TEXT
               )"""
        )
        conn.execute(
            """CREATE TABLE events (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   session_id TEXT NOT NULL,
                   tool_name TEXT,
                   action_type TEXT,
                   status TEXT,
                   target TEXT,
                   duration_ms INTEGER,
                   reason TEXT,
                   created_at TEXT
               )"""
        )
        conn.commit()
    finally:
        conn.close()


def _insert_session(db_path: Path, session_id: str, project_path: str,
                    title: str = "t", tool: str = "read_file_range") -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (session_id, session_title, project_path, "
            "mode, status, coverage, started_at, agent_id) "
            "VALUES (?, ?, ?, 'T1', 'closed', 'A-only', ?, ?)",
            (session_id, title, project_path, "2026-08-11T00:00:00", "ag-test"),
        )
        conn.execute(
            "INSERT INTO events (session_id, tool_name, action_type, status, "
            "target, duration_ms, reason, created_at) "
            "VALUES (?, ?, 'read', 'ok', '', 1, '', ?)",
            (session_id, tool, "2026-08-11T00:00:01"),
        )
        conn.commit()
    finally:
        conn.close()


class CommitEProjectScopeTests(unittest.TestCase):
    """Handler-layer regression for S3-CONTRACT-01."""

    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="commit_e_test_"))
        # Two distinct project roots to prove A/B isolation.
        self.project_a = self.tempdir / "proj_a"
        self.project_b = self.tempdir / "proj_b"
        self.project_a.mkdir(parents=True)
        self.project_b.mkdir(parents=True)
        # Seed a fresh sessions DB with one session per project.
        self.db_path = self.tempdir / "sessions.db"
        _make_db(self.db_path)
        key_a = code_tools._session_project_key(str(self.project_a))
        key_b = code_tools._session_project_key(str(self.project_b))
        self.key_a = key_a
        self.key_b = key_b
        _insert_session(self.db_path, "sess-A-1", key_a, title="proj-a-session")
        _insert_session(self.db_path, "sess-B-1", key_b, title="proj-b-session")

        # Redirect code_tools at the temp DB for the duration of the test.
        self._old_sessions_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.db_path
        code_tools._sync_session_db_alias()

    def tearDown(self):
        code_tools._SESSIONS_DB = self._old_sessions_db
        code_tools._sync_session_db_alias()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    # ── 1) zero-payload / missing project_path → MISSING_REQUIRED_FIELD ─────
    def test_list_sessions_missing_project_path_fails_loud(self):
        result = json.loads(asyncio.run(code_tools.list_sessions("")))
        self.assertFalse(result.get("ok"), msg=result)
        self.assertEqual(result.get("error_name"), "MISSING_REQUIRED_FIELD")
        self.assertEqual(result.get("type"), "SCHEMA_VIOLATION")
        self.assertIn("project_path", result.get("missing_fields") or [])
        self.assertFalse(result.get("write_performed", True))

    def test_get_usage_stats_missing_project_path_fails_loud(self):
        result = json.loads(asyncio.run(code_tools.get_usage_stats("")))
        self.assertFalse(result.get("ok"), msg=result)
        self.assertEqual(result.get("error_name"), "MISSING_REQUIRED_FIELD")
        self.assertEqual(result.get("type"), "SCHEMA_VIOLATION")
        self.assertIn("project_path", result.get("missing_fields") or [])
        self.assertFalse(result.get("write_performed", True))

    # ── 2) A/B project isolation on list_sessions ───────────────────────────
    def test_list_sessions_returns_only_project_a_rows(self):
        result = json.loads(asyncio.run(code_tools.list_sessions(str(self.project_a))))
        self.assertTrue(result.get("ok"), msg=result)
        self.assertEqual(result.get("scope"), "project")
        self.assertEqual(result.get("project_path"), self.key_a)
        session_ids = [s["session_id"] for s in result.get("sessions") or []]
        self.assertIn("sess-A-1", session_ids)
        self.assertNotIn("sess-B-1", session_ids,
                         msg=f"cross-project leak: {session_ids}")
        # Pagination totals reflect only the project-filtered rows.
        self.assertEqual(result.get("total"), len(session_ids))

    def test_list_sessions_returns_only_project_b_rows(self):
        result = json.loads(asyncio.run(code_tools.list_sessions(str(self.project_b))))
        self.assertTrue(result.get("ok"), msg=result)
        session_ids = [s["session_id"] for s in result.get("sessions") or []]
        self.assertIn("sess-B-1", session_ids)
        self.assertNotIn("sess-A-1", session_ids,
                         msg=f"cross-project leak: {session_ids}")

    # ── 3) A/B project isolation on get_usage_stats ─────────────────────────
    def test_get_usage_stats_scoped_to_project_a(self):
        # Force the sqlite fallback by NOT creating APP_SESSION_LOG.jsonl in
        # project_a's .orchestrator_cache.
        result = json.loads(asyncio.run(code_tools.get_usage_stats(
            str(self.project_a), include_http_log=False)))
        self.assertTrue(result.get("ok"), msg=result)
        self.assertEqual(result.get("scope"), "project")
        self.assertEqual(result.get("project_path"), self.key_a)
        # All analyzed events must belong to sessions of project A.
        # (No cross-project tool names should appear unless they were also
        # recorded under a project-A session.)
        # We assert indirectly: the project_path surface matches the
        # canonical key for A (not B, not CWD).
        self.assertNotEqual(result.get("project_path"), self.key_b)

    # ── 4) scope='all' fails loud (deferred global) ─────────────────────────
    def test_scope_all_returns_global_not_enabled(self):
        for fn in (code_tools.list_sessions, code_tools.get_usage_stats):
            result = json.loads(asyncio.run(fn(str(self.project_a), scope="all")))
            self.assertFalse(result.get("ok"), msg=(fn.__name__, result))
            self.assertEqual(result.get("error_name"), "GLOBAL_SCOPE_NOT_ENABLED")
            self.assertEqual(result.get("type"), "AUTHORIZATION_ERROR")
            self.assertFalse(result.get("write_performed", True))
            # No silent fallback to project scope.
            self.assertNotEqual(result.get("scope"), "project")

    def test_invalid_scope_fails_loud(self):
        for fn in (code_tools.list_sessions, code_tools.get_usage_stats):
            result = json.loads(asyncio.run(fn(str(self.project_a), scope="everything")))
            self.assertFalse(result.get("ok"), msg=(fn.__name__, result))
            self.assertEqual(result.get("error_name"), "INVALID_SCOPE")
            self.assertFalse(result.get("write_performed", True))

    # ── 5) canonical path normalization (separator/case on Windows) ─────────
    def test_canonical_project_path_matches_forward_slash_input(self):
        # On Windows, 'D:\\foo' and 'D:/foo' must resolve to the same key.
        fwd = str(self.project_a).replace("\\", "/")
        result_fwd = json.loads(asyncio.run(code_tools.list_sessions(fwd)))
        result_bks = json.loads(asyncio.run(code_tools.list_sessions(str(self.project_a))))
        self.assertTrue(result_fwd.get("ok"), msg=result_fwd)
        self.assertEqual(result_fwd.get("project_path"), result_bks.get("project_path"))
        ids_fwd = [s["session_id"] for s in result_fwd["sessions"]]
        self.assertIn("sess-A-1", ids_fwd)

    # ── 6) prefix-collision guard: 'proj_a' must NOT match 'proj_a2' ────────
    def test_no_prefix_collision(self):
        # Create a sibling project whose path is a string-prefix extension of
        # project_a's key; sessions for it must not leak into project_a queries.
        project_a2 = self.tempdir / "proj_a2"
        project_a2.mkdir(parents=True)
        key_a2 = code_tools._session_project_key(str(project_a2))
        _insert_session(self.db_path, "sess-A2-1", key_a2, title="proj-a2-session")

        result = json.loads(asyncio.run(code_tools.list_sessions(str(self.project_a))))
        self.assertTrue(result.get("ok"), msg=result)
        session_ids = [s["session_id"] for s in result.get("sessions") or []]
        self.assertIn("sess-A-1", session_ids)
        self.assertNotIn("sess-A2-1", session_ids,
                         msg=f"prefix-collision leak: {session_ids}")
        self.assertNotIn("sess-B-1", session_ids)

    # ── 7) a log row holding a Unicode line break is read whole ─────────────
    def test_get_usage_stats_reads_a_log_row_holding_a_line_break(self):
        # APP_SESSION_LOG.jsonl is written one json.dumps(ensure_ascii=False) row
        # per "\n", so an error holding U+2028 / U+0085 is written raw.
        # str.splitlines() also broke there, and json.loads of a fragment failed
        # the WHOLE tool with "Failed to read session log".
        text = "a" + chr(0x2028) + "b" + chr(0x85) + "c"
        log = code_tools._project_cache_dir(str(self.project_a)) / "APP_SESSION_LOG.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_bytes("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in (
            {"tool": "write_fix", "ok": False, "error": text},
            {"tool": "read_file_range", "ok": True},
        )).encode("utf-8"))
        result = json.loads(asyncio.run(code_tools.get_usage_stats(
            str(self.project_a), include_http_log=False)))
        self.assertTrue(result.get("ok"), msg=result)
        self.assertEqual(result.get("entries_analyzed"), 2, msg=result)
        self.assertEqual([e["error"] for e in result["recent_errors"]], [text])


class CommitEDispatcherGuardTests(unittest.TestCase):
    """Pin the dispatcher-layer invariant: the two tools are NOT in
    _PROJECT_PATH_DEFAULTABLE, so zero-payload can never silently downgrade to
    project_path='.' again."""

    def test_tools_removed_from_project_path_defaultable(self):
        import orchestrator_server
        self.assertNotIn("list_sessions", orchestrator_server._PROJECT_PATH_DEFAULTABLE)
        self.assertNotIn("get_usage_stats", orchestrator_server._PROJECT_PATH_DEFAULTABLE)

    def test_shared_scope_helper_present_in_code_tools(self):
        self.assertTrue(hasattr(code_tools, "_resolve_session_query_scope"))
        # Returns a (key, err) tuple.
        key, err = code_tools._resolve_session_query_scope(
            "list_sessions", "", "project")
        self.assertEqual(key, "")
        self.assertTrue(err)
        parsed = json.loads(err)
        self.assertEqual(parsed.get("error_name"), "MISSING_REQUIRED_FIELD")


if __name__ == "__main__":
    unittest.main()
