# E-01 regression tests — get_usage_stats process-global escape hatches.
#
# WHY: get_usage_stats had two process-global escape hatches that leaked
# cross-project/root/UI/HTTP data under a project-scoped call:
#   1) include_global_usage=True appended the orchestrator-root
#      USAGE_LOG.jsonl AND the process-wide ui_log.jsonl to a project-scoped
#      result, WITHOUT any scope='all' gate.
#   2) include_http_log=True tailed the process-wide orchestrator.log (HTTP
#      traffic for EVERY project served by this process) and attached it as
#      http_stats.
# Both are process-global reads with no auth/audit. E-01 remediation
# (purpose-approved Option 2):
#   - Both flags become TOMBSTONES: kept in the signature so legacy keyword
#     callers do not TypeError, but explicit use fails loud with
#     GLOBAL_SCOPE_NOT_ENABLED (AUTHORIZATION_ERROR) — not silently ignored,
#     not a generic schema error.
#   - The global/UI/HTTP aggregation branches are REMOVED from project-scoped
#     execution; project queries return only canonical project entries.
#   - Both properties are REMOVED from the advertised dynamic schema.
#   - Re-enable only via a future token-gated + audited global op.
#
# These tests pin those invariants with distinguishable project + global
# fixtures so a leak would be visible by name.
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import code_tools


def _make_db(path: Path) -> None:
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


def _insert_event_session(db_path: Path, session_id: str, project_path: str,
                          tool: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (session_id, session_title, project_path, "
            "mode, status, coverage, started_at, agent_id) "
            "VALUES (?, ?, ?, 'T1', 'closed', 'A-only', ?, ?)",
            (session_id, "t", project_path, "2026-08-11T00:00:00", "ag-test"),
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


class E01GlobalEscapeHatchTests(unittest.TestCase):
    """E-01: include_global_usage / include_http_log tombstones + no leak."""

    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="e01_test_"))
        self.project_a = self.tempdir / "proj_a"
        self.project_b = self.tempdir / "proj_b"
        self.project_a.mkdir(parents=True)
        self.project_b.mkdir(parents=True)

        self.db_path = self.tempdir / "sessions.db"
        _make_db(self.db_path)
        self.key_a = code_tools._session_project_key(str(self.project_a))
        self.key_b = code_tools._session_project_key(str(self.project_b))
        # Distinguishable per-project tool names so any cross-project row is
        # visible by name in the aggregated top_tools.
        _insert_event_session(self.db_path, "sess-A-1", self.key_a,
                              tool="PROJECT_A_ONLY_TOOL")
        _insert_event_session(self.db_path, "sess-B-1", self.key_b,
                              tool="PROJECT_B_ONLY_TOOL")

        # Distinguishable PROCESS-GLOBAL fixtures that must never leak into a
        # project-scoped result. These live OUTSIDE both project roots.
        # 1) root USAGE_LOG.jsonl (what include_global_usage used to read)
        self.fake_root = self.tempdir / "fake_orch_root"
        (self.fake_root / ".orchestrator_cache").mkdir(parents=True)
        (self.fake_root / ".orchestrator_cache" / "USAGE_LOG.jsonl").write_text(
            json.dumps({"tool": "GLOBAL_ROOT_USAGE_TOOL", "ok": True, "ms": 1,
                        "file": "", "error": "", "ts": "2026-08-11T00:00:02"})
            + "\n",
            encoding="utf-8",
        )
        # 2) process-wide ui_log.jsonl (what include_global_usage also appended)
        self.ui_log_dir = self.tempdir / "ui_cache"
        self.ui_log_dir.mkdir(parents=True)
        (self.ui_log_dir / "ui_log.jsonl").write_text(
            json.dumps({"tool": "GLOBAL_UI_LOG_TOOL", "status": "success",
                        "ms": 1, "file": "", "ts": "2026-08-11T00:00:03"})
            + "\n",
            encoding="utf-8",
        )
        # 3) process-wide orchestrator.log HTTP tail (what include_http_log read)
        self.http_log = self.tempdir / "orchestrator.log"
        self.http_log.write_text(
            '2026-08-11 00:00:04 POST /api/call/GLOBAL_HTTP_LOG_TOOL HTTP/1.1 200 OK\n',
            encoding="utf-8",
        )

        # Redirect code_tools at the temp DB.
        self._old_sessions_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.db_path
        code_tools._sync_session_db_alias()

    def tearDown(self):
        code_tools._SESSIONS_DB = self._old_sessions_db
        code_tools._sync_session_db_alias()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _top_tool_names(self, result):
        return {t.get("tool") for t in (result.get("top_tools") or [])}

    # ── 1) explicit include_global_usage=True -> GLOBAL_SCOPE_NOT_ENABLED ────
    def test_include_global_usage_true_fails_loud(self):
        result = json.loads(asyncio.run(code_tools.get_usage_stats(
            str(self.project_a), include_global_usage=True)))
        self.assertFalse(result.get("ok"), msg=result)
        self.assertEqual(result.get("error_name"), "GLOBAL_SCOPE_NOT_ENABLED")
        self.assertEqual(result.get("type"), "AUTHORIZATION_ERROR")
        self.assertFalse(result.get("write_performed", True))
        # Not silently downgraded to a project result.
        self.assertNotEqual(result.get("scope"), "project")

    # ── 2) explicit include_http_log=True -> GLOBAL_SCOPE_NOT_ENABLED ────────
    def test_include_http_log_true_fails_loud(self):
        result = json.loads(asyncio.run(code_tools.get_usage_stats(
            str(self.project_a), include_http_log=True)))
        self.assertFalse(result.get("ok"), msg=result)
        self.assertEqual(result.get("error_name"), "GLOBAL_SCOPE_NOT_ENABLED")
        self.assertEqual(result.get("type"), "AUTHORIZATION_ERROR")
        self.assertFalse(result.get("write_performed", True))
        self.assertNotEqual(result.get("scope"), "project")

    # ── 3) default project-scoped query returns ONLY canonical project entries
    def test_project_scoped_query_has_no_global_or_foreign_leak(self):
        result = json.loads(asyncio.run(code_tools.get_usage_stats(
            str(self.project_a))))
        self.assertTrue(result.get("ok"), msg=result)
        self.assertEqual(result.get("scope"), "project")
        self.assertEqual(result.get("project_path"), self.key_a)
        names = self._top_tool_names(result)
        self.assertIn("PROJECT_A_ONLY_TOOL", names)
        # No foreign-project rows.
        self.assertNotIn("PROJECT_B_ONLY_TOOL", names,
                         msg=f"cross-project leak: {names}")
        # No root/global/UI/HTTP rows by name.
        self.assertNotIn("GLOBAL_ROOT_USAGE_TOOL", names,
                         msg=f"root usage leak: {names}")
        self.assertNotIn("GLOBAL_UI_LOG_TOOL", names,
                         msg=f"ui log leak: {names}")
        # http_stats surface is gone entirely (HTTP tail removed).
        self.assertNotIn("http_stats", result,
                         msg=f"http_stats leak: {result}")
        self.assertNotIn("GLOBAL_HTTP_LOG_TOOL", json.dumps(result),
                         msg="HTTP log content leaked into project result")

    # ── 4) even with global fixtures present + both flags explicitly False,
    #       the result is still project-only (no silent global branch). ───────
    def test_flags_false_still_project_only(self):
        result = json.loads(asyncio.run(code_tools.get_usage_stats(
            str(self.project_a),
            include_global_usage=False,
            include_http_log=False)))
        self.assertTrue(result.get("ok"), msg=result)
        names = self._top_tool_names(result)
        self.assertIn("PROJECT_A_ONLY_TOOL", names)
        self.assertNotIn("GLOBAL_ROOT_USAGE_TOOL", names)
        self.assertNotIn("GLOBAL_UI_LOG_TOOL", names)
        self.assertNotIn("http_stats", result)

    # ── 5) empty-project surface no longer carries http_stats ────────────────
    def test_no_project_log_surface_has_no_http_stats(self):
        empty_proj = self.tempdir / "proj_empty"
        empty_proj.mkdir(parents=True)
        result = json.loads(asyncio.run(code_tools.get_usage_stats(
            str(empty_proj))))
        self.assertTrue(result.get("ok"), msg=result)
        self.assertEqual(result.get("status"), "no_project_log")
        self.assertEqual(result.get("scope"), "project")
        self.assertNotIn("http_stats", result)


class E01SchemaTombstoneTests(unittest.TestCase):
    """E-01: both escape-hatch properties are removed from the advertised schema."""

    def _usage_schema_props(self):
        for schema in code_tools.TOOL_SCHEMAS:
            fn = schema.get("function", {})
            if fn.get("name") == "get_usage_stats":
                return fn.get("parameters", {}).get("properties", {})
        self.fail("get_usage_stats schema not found in TOOL_SCHEMAS")

    def test_schema_does_not_advertise_include_global_usage(self):
        props = self._usage_schema_props()
        self.assertNotIn("include_global_usage", props,
                         msg=f"schema still advertises tombstone: {sorted(props)}")

    def test_schema_does_not_advertise_include_http_log(self):
        props = self._usage_schema_props()
        self.assertNotIn("include_http_log", props,
                         msg=f"schema still advertises tombstone: {sorted(props)}")

    def test_schema_still_advertises_core_params(self):
        props = self._usage_schema_props()
        # The project-scope contract params must remain advertised.
        self.assertIn("project_path", props)
        self.assertIn("scope", props)
        self.assertIn("last_n", props)


if __name__ == "__main__":
    unittest.main()
