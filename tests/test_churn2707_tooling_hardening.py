import asyncio
import base64
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import code_tools
import orchestrator_server
import tools_v15
from orchestrator.fileops import common
from orchestrator.session import store as session_store
from orchestrator.tools.read.context import read_file_range


class FilesystemAndCouncilHardeningTests(unittest.TestCase):
    def test_folder_size_prunes_by_default_but_can_scan_all_and_direct_git(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_bytes(b"123")
            (root / ".git" / "objects").mkdir(parents=True)
            (root / ".git" / "objects" / "pack").write_bytes(b"12345")

            safe = json.loads(asyncio.run(code_tools.get_folder_size(str(root))))
            full = json.loads(asyncio.run(
                code_tools.get_folder_size(str(root), include_skipped=True)))
            direct_git = json.loads(asyncio.run(
                code_tools.get_folder_size(str(root / ".git"))))

            self.assertEqual(safe["total_files"], 1)
            self.assertEqual(safe["skipped_dir_count"], 1)
            self.assertEqual(safe["scan_scope"], "safe_default")
            self.assertEqual(full["total_files"], 2)
            self.assertEqual(full["total_bytes"], 8)
            self.assertEqual(direct_git["total_files"], 1)
            self.assertEqual(direct_git["total_bytes"], 5)

    def test_read_file_range_reports_directory_and_binary_with_actionable_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "folder").mkdir()
            binary_path = root / "archive.zip"
            binary_path.write_bytes(b"PK\x03\x04\x00payload")

            directory = json.loads(asyncio.run(read_file_range(
                "folder", project_path=str(root))))
            binary = json.loads(asyncio.run(read_file_range(
                "archive.zip", project_path=str(root))))

            self.assertEqual(directory["error_name"], "PATH_IS_DIRECTORY")
            self.assertIn("list_structure", directory["suggested_tools"])
            self.assertEqual(binary["error_name"], "BINARY_FILE_UNSUPPORTED")
            self.assertEqual(binary["size_bytes"], binary_path.stat().st_size)
            self.assertIn("check_server_path", binary["suggested_tools"])

    def test_binary_helpers_read_only_a_bounded_stream_sample(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.bin"
            path.write_bytes(b"\x00" + b"x" * 8192)
            with mock.patch.object(
                    Path, "read_bytes", side_effect=AssertionError("whole-file read")):
                self.assertTrue(common.is_binary_file(path, sample_size=32))
                self.assertEqual(common.detect_text_encoding(path, sample_size=32), "utf-8")

    def test_python_test_artifact_imports_module_from_project_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "helper_module.py").write_text(
                "VALUE = 'root-import-ok'\n", encoding="utf-8")
            result = json.loads(asyncio.run(code_tools.run_project_test_script(
                project_path=str(root),
                script_content="from helper_module import VALUE\nprint(VALUE)\n",
                runtime="python",
                python_exe=sys.executable,
                timeout=30,
            )))
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("root-import-ok", result["stdout"])

    def test_unittest_context_does_not_create_qnight_background_thread(self):
        with mock.patch.object(sys, "argv", ["python -m unittest", "tests"]):
            with mock.patch.object(orchestrator_server.threading, "Thread") as thread_cls:
                started = orchestrator_server._qnight_start_background(lambda: None)
        self.assertFalse(started)
        thread_cls.assert_not_called()

    def test_project_command_classifies_explicit_test_commands_only(self):
        pytest_command = (
            r"venv312\Scripts\python.exe -W ignore -m pytest -q tests"
        )
        encoded = base64.b64encode(pytest_command.encode("utf-8")).decode("ascii")
        self.assertTrue(session_store._run_project_command_is_validation(
            {"command": pytest_command}))
        self.assertTrue(session_store._run_project_command_is_validation(
            {"command": encoded, "command_encoding": "base64"}))
        self.assertTrue(session_store._run_project_command_is_validation(
            {"command": "npm run test -- --runInBand"}))
        self.assertFalse(session_store._run_project_command_is_validation(
            {"command": "python scripts/build_release.py"}))
        self.assertFalse(session_store._run_project_command_is_validation(
            {"command": "not-base64", "command_encoding": "base64"}))


class SessionAuditHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.old_db = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = self.root / "sessions.db"
        code_tools._sync_session_db_alias()
        code_tools._init_session_db()
        self.project = code_tools._session_project_key(str(self.root / "project"))
        Path(self.project).mkdir(parents=True, exist_ok=True)
        def _seed_sessions(conn):
            conn.executemany(
                """INSERT INTO sessions
                   (session_id, session_title, project_path, mode, status,
                    coverage, started_at, agent_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    ("agent-a-session", "Agent A", self.project, "T2", "active",
                     "A-only", "2026-07-28T10:00:00", "agent-a"),
                    ("agent-b-session", "Agent B", self.project, "T2", "active",
                     "A-only", "2026-07-28T10:00:01", "agent-b"),
                ],
            )

        session_store._run_session_write("test seed sessions", _seed_sessions)

    def tearDown(self):
        read_conn = getattr(session_store._SESSION_READ_LOCAL, "conn", None)
        if read_conn is not None:
            read_conn.close()
            session_store._SESSION_READ_LOCAL.conn = None
            session_store._SESSION_READ_LOCAL.db_path = None
        session_store._shutdown_session_writer()
        code_tools._SESSIONS_DB = self.old_db
        code_tools._sync_session_db_alias()
        self.temp_dir.cleanup()

    @staticmethod
    def _event_result(agent):
        return {
            "ok": True,
            "_host_session_id": "round-host",
            "_task_epoch": 7,
            "_prompt_hash": "abc123",
            "_task_agent_id": agent,
        }

    def test_round_scope_aggregates_agents_and_classifies_tests_as_validation(self):
        code_tools._log_session_event(
            "agent-a-session", "run_project_test_script", {},
            self._event_result("agent-a"),
            10, "ok")
        code_tools._log_session_event(
            "agent-b-session", "validate_file", {}, self._event_result("agent-b"),
            11, "ok")

        summary = json.loads(asyncio.run(tools_v15.session_audit_summary(
            target_session_id="agent-a-session")))
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(summary["validations"], 2)
        self.assertEqual(summary["agent_id"], "agent-a")
        self.assertEqual(
            summary["audit_scope"], "project_session_plus_matching_host_task")
        tool_counts = {row["name"]: row["count"] for row in summary["tools_used"]}
        self.assertEqual(tool_counts["run_project_test_script"], 1)
        self.assertEqual(tool_counts["validate_file"], 1)

        events = json.loads(asyncio.run(code_tools.get_session_events(
            target_session_id="agent-b-session")))
        self.assertEqual(events["agent_id"], "agent-b")
        self.assertEqual(events["events"][0]["host_session_id"], "round-host")
        self.assertEqual(events["events"][0]["task_epoch"], 7)

    def test_project_only_scope_is_ambiguous_with_multiple_agents(self):
        result = json.loads(asyncio.run(
            tools_v15.session_audit_summary(project_path=self.project)))
        self.assertEqual(result["error_name"], "SESSION_SCOPE_AMBIGUOUS")
        self.assertEqual(len(result["session_candidates"]), 2)

    def test_legacy_pending_requires_explicit_targeted_import_for_agent_session(self):
        pending_id = "legacy-pending"
        session_store._run_session_write(
            "test seed legacy audit",
            lambda conn: conn.execute(
                """INSERT INTO pending_audits
                   (pending_audit_id, project_path, action_type, tool_name,
                    target, reason, sync_status, created_at, agent_id)
                   VALUES (?,?,?,?,?,?,?,?,NULL)""",
                (pending_id, self.project, "write", "native_edit",
                 "old.py", "legacy row", "pending", "2026-07-15T00:00:00"),
            ),
        )

        before = json.loads(asyncio.run(tools_v15.session_audit_summary(
            target_session_id="agent-a-session")))
        self.assertEqual(before["pending_audit_count"], 0)
        self.assertEqual(before["legacy_pending_audit_count"], 1)

        skipped = json.loads(asyncio.run(code_tools.sync_pending_audit(
            self.project, target_session_id="agent-a-session", agent_id="agent-a")))
        self.assertEqual(skipped["synced"], [])
        self.assertEqual(skipped["legacy_pending_remaining"], 1)

        imported = json.loads(asyncio.run(code_tools.sync_pending_audit(
            self.project, target_session_id="agent-a-session", agent_id="agent-a",
            include_legacy=True)))
        self.assertEqual(imported["synced"], [pending_id])
        self.assertEqual(imported["legacy_pending_remaining"], 0)

    def test_sync_rejects_agent_mismatch_for_explicit_target_session(self):
        mismatch = json.loads(asyncio.run(code_tools.sync_pending_audit(
            self.project, target_session_id="agent-a-session", agent_id="agent-b")))
        self.assertEqual(mismatch["error_name"], "SESSION_AGENT_MISMATCH")

    def test_sync_without_agent_cannot_import_legacy_rows(self):
        created = json.loads(asyncio.run(code_tools.create_pending_audit(
            self.project, "read", "legacy-unscoped",
        )))
        self.assertTrue(created["ok"])
        rejected = json.loads(asyncio.run(code_tools.sync_pending_audit(self.project)))
        self.assertEqual(rejected["error_name"], "AUDIT_AGENT_SCOPE_REQUIRED")
        with code_tools._with_sessions_db() as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM pending_audits "
                "WHERE pending_audit_id=? AND agent_id IS NULL AND sync_status='pending'",
                (created["pending_audit_id"],),
            ).fetchone()[0]
        self.assertEqual(remaining, 1)

    def test_list_sessions_exposes_agent_identity(self):
        result = json.loads(asyncio.run(code_tools.list_sessions(self.project)))
        agents = {row["agent_id"] for row in result["sessions"]}
        self.assertEqual(agents, {"agent-a", "agent-b"})


class StaticSchemaContractTests(unittest.TestCase):
    def test_new_parameters_are_present_in_transport_schemas(self):
        schemas = {
            item["function"]["name"]: item["function"]["parameters"]["properties"]
            for item in code_tools.TOOL_SCHEMAS
        }
        expected = {
            "get_folder_size": {"include_skipped"},
            "sync_pending_audit": {"target_session_id", "include_legacy"},
            "get_session_events": {"agent_id"},
            "session_audit_summary": {"agent_id"},
        }
        for tool_name, fields in expected.items():
            self.assertTrue(
                fields.issubset(schemas[tool_name]),
                f"{tool_name} missing {fields - set(schemas[tool_name])}",
            )


class PersistentWriteLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db = code_tools._SESSIONS_DB
        self.old_generation = orchestrator_server.SERVER_GENERATION
        code_tools._SESSIONS_DB = Path(self.temp_dir.name) / "sessions.db"
        code_tools._sync_session_db_alias()
        code_tools._init_session_db()
        orchestrator_server.SERVER_GENERATION = "generation-a"
        orchestrator_server._WRITE_LEDGER.clear()
        orchestrator_server._WRITE_LEDGER_BY_TARGET.clear()
        orchestrator_server._WRITE_LEDGER_LAST_PURGE = 0.0

    def tearDown(self):
        orchestrator_server._WRITE_LEDGER.clear()
        orchestrator_server._WRITE_LEDGER_BY_TARGET.clear()
        orchestrator_server._WRITE_LEDGER_LAST_PURGE = 0.0
        orchestrator_server.SERVER_GENERATION = self.old_generation
        code_tools._SESSIONS_DB = self.old_db
        code_tools._sync_session_db_alias()
        self.temp_dir.cleanup()

    def test_committed_status_survives_memory_loss_without_storing_source(self):
        secret = "DO_NOT_PERSIST_THIS_SOURCE"
        body = {
            "project_path": r"D:\project",
            "file_path": "app.py",
            "new_content": secret,
        }
        operation_id = orchestrator_server._write_ledger_begin(
            "write_fix", body, "round-host")
        orchestrator_server._write_ledger_finish(
            operation_id,
            {"ok": True, "write_performed": True, "new_hash": "deadbeef"},
            "ok",
        )
        orchestrator_server._WRITE_LEDGER.clear()
        orchestrator_server._WRITE_LEDGER_BY_TARGET.clear()

        restored = orchestrator_server._write_ledger_db_get(
            operation_id=operation_id)
        self.assertEqual(restored["status"], "committed")
        self.assertEqual(
            orchestrator_server._write_ledger_commit_state(restored), "committed")
        self.assertEqual(restored["origin_generation"], "generation-a")
        by_target = orchestrator_server._write_ledger_db_get(
            target=orchestrator_server._write_ledger_target(body))
        self.assertEqual(by_target["operation_id"], operation_id)

        with code_tools._with_sessions_db() as conn:
            raw = conn.execute(
                "SELECT result_json FROM write_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()[0]
        self.assertNotIn(secret, raw)

    def test_old_generation_in_flight_is_unknown_and_not_safe_to_assume_failed(self):
        operation_id = orchestrator_server._write_ledger_begin(
            "write_fix",
            {"project_path": r"D:\project", "file_path": "pending.py"},
            "round-host",
        )
        orchestrator_server._WRITE_LEDGER.clear()
        orchestrator_server.SERVER_GENERATION = "generation-b"
        restored = orchestrator_server._write_ledger_db_get(
            operation_id=operation_id)
        self.assertEqual(restored["status"], "in_flight")
        self.assertEqual(
            orchestrator_server._write_ledger_commit_state(restored), "unknown")

    def test_locked_sqlite_fails_open_quickly_instead_of_blocking_write(self):
        blocker = sqlite3.connect(str(code_tools._SESSIONS_DB), timeout=1)
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            started = time.perf_counter()
            operation_id = orchestrator_server._write_ledger_begin(
                "write_fix",
                {"project_path": r"D:\project", "file_path": "locked.py"},
                "round-host",
            )
            elapsed = time.perf_counter() - started
        finally:
            blocker.rollback()
            blocker.close()
        self.assertTrue(operation_id)
        self.assertLess(elapsed, 2.0)

    def test_async_write_ledger_wrapper_keeps_event_loop_responsive(self):
        import threading

        started = threading.Event()
        release = threading.Event()

        def blocking_begin(*_args):
            started.set()
            release.wait(.5)
            return "op-id"

        async def scenario():
            loop = asyncio.get_running_loop()
            loop.call_later(.02, release.set)
            tick_started = time.perf_counter()
            task = asyncio.create_task(orchestrator_server._write_ledger_begin_async(
                "write_fix", {"project_path": r"D:\project"}, "round-host"))
            while not started.is_set():
                await asyncio.sleep(.001)
            await asyncio.sleep(.03)
            return await task, time.perf_counter() - tick_started

        with mock.patch.object(
                orchestrator_server, "_write_ledger_begin", side_effect=blocking_begin):
            operation_id, elapsed = asyncio.run(scenario())
        self.assertEqual(operation_id, "op-id")
        self.assertLess(elapsed, .2, "SQLite ledger work blocked the event loop")
if __name__ == "__main__":
    unittest.main()
