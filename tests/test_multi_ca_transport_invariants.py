"""Regression coverage for multi-CA transport and ledger invariants."""

import json
import sqlite3
import tempfile
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from orchestrator.server.audit_writer import DurableAuditWriter

import code_tools
import orchestrator_server


class TestMultiCATransportInvariants(unittest.IsolatedAsyncioTestCase):
    def _use_temp_sessions_db(self, tmpdir):
        previous = code_tools._SESSIONS_DB
        code_tools._SESSIONS_DB = Path(tmpdir) / "sessions.db"
        code_tools._init_session_db()
        return previous

    @staticmethod
    def _restore_sessions_db(previous):
        code_tools._SESSIONS_DB = previous
        code_tools._sync_session_db_alias()

    @staticmethod
    @contextmanager
    def _registered_tool_schema(tool_name, properties, required=None):
        """Publish a registry schema for an ad-hoc probe tool.

        S4 field validation derives `allowed_params` from the REGISTRY schema
        (`_schema_for_tool` -> `ct.TOOL_SCHEMAS`), never from the raw handler
        signature. A probe registered only in `TOOL_MAP` therefore has empty
        `properties`, so every field it is called with (e.g. `project_path`)
        trips UNSUPPORTED_FIELD before the handler runs. Registering the same
        public contract a real tool would carry lets S4 accept the field and the
        handler execute. Removed in teardown so the global registry stays clean.
        """
        schema = {
            "type": "function",
            "function": {
                "name": tool_name,
                "parameters": {
                    "type": "object",
                    "properties": dict(properties),
                    "required": list(required or []),
                },
            },
        }
        code_tools.TOOL_SCHEMAS.append(schema)
        try:
            yield schema
        finally:
            try:
                code_tools.TOOL_SCHEMAS.remove(schema)
            except ValueError:
                pass

    @staticmethod
    @contextmanager
    def _use_local_durable_writer(tmpdir):
        previous = orchestrator_server._DURABLE_AUDIT
        code_tools._sync_session_db_alias()
        writer = DurableAuditWriter(
            Path(tmpdir) / "durable-audit.db",
            orchestrator_server._persist_durable_session_event,
            poll_interval_s=0.01,
        )
        orchestrator_server._DURABLE_AUDIT = writer
        try:
            yield writer
        finally:
            writer.shutdown(timeout=3.0)
            orchestrator_server._DURABLE_AUDIT = previous

    async def test_http_agent_header_isolates_gate_and_durable_metadata(self):
        tool_name = "__multi_ca_agent_probe_" + uuid.uuid4().hex

        async def probe(project_path):
            return json.dumps({"ok": True, "project_path": project_path})

        old_db = None
        old_auto = orchestrator_server.AUTO_WRITE_MODE
        code_tools.TOOL_MAP[tool_name] = probe
        orchestrator_server._MUTATING_TOOLS.add(tool_name)
        orchestrator_server.AUTO_WRITE_MODE = False
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
                self._registered_tool_schema(
                    tool_name, {"project_path": {"type": "string"}}):
            old_db = self._use_temp_sessions_db(tmp)
            try:
                with self._use_local_durable_writer(tmp) as writer:
                    project = str(Path(tmp) / "repo")
                    Path(project).mkdir()
                    session_a = code_tools._auto_open_session(project, {"agent_id": "agent-a"})
                    session_b = code_tools._auto_open_session(project, {"agent_id": "agent-b"})

                    canonical = await orchestrator_server.dispatch_tool_call(
                        tool_name, {"project_path": project}, transport="api",
                        request_meta={"headers": {
                            "X-Agent-Id": "agent-a",
                            "X-Operation-ID": "agent-a-" + uuid.uuid4().hex,
                        }},
                    )
                    fallback = await orchestrator_server.dispatch_tool_call(
                        tool_name, {"project_path": project}, transport="api",
                        request_meta={"headers": {
                            "X-Council-Agent": "agent-b",
                            "X-Operation-ID": "agent-b-" + uuid.uuid4().hex,
                        }},
                    )
                    self.assertTrue(json.loads(canonical.text).get("ok"))
                    self.assertTrue(json.loads(fallback.text).get("ok"))
                    await orchestrator_server._flush_post_call_accounting(timeout=3.0)
                    self.assertTrue(writer.wait_until_idle(3.0))

                    with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                        rows = conn.execute(
                            "SELECT session_id, result_json FROM events WHERE tool_name=? ORDER BY id",
                            (tool_name,),
                        ).fetchall()
                    self.assertEqual({row[0] for row in rows}, {session_a, session_b})
                    self.assertEqual(
                        {json.loads(row[1]).get("_task_agent_id") for row in rows},
                        {"agent-a", "agent-b"},
                    )
            finally:
                self._restore_sessions_db(old_db)
                orchestrator_server.AUTO_WRITE_MODE = old_auto
                orchestrator_server._MUTATING_TOOLS.discard(tool_name)
                code_tools.TOOL_MAP.pop(tool_name, None)

    @staticmethod
    def _seed_task_checkpoint(session_id, task_epoch, prompt_hash):
        """Seed the SQLite-authoritative checkpoint the epoch gate actually reads.

        The task-epoch gate in orchestrator_server (`if _task_meta and
        _task_epoch_raw is not None`) resolves `_task_meta` from
        `_task_checkpoint_load` (SQLite `task_checkpoints` table), which is the
        sole store of task checkpoints. We must write that authoritative row via
        the same commit-ordered updater production code uses; nothing else the
        gate reads is populated by any in-memory shortcut.
        """
        def _updater(_previous):
            return (
                {
                    "session_key": str(session_id),
                    "agent_id": "",
                    "task_epoch": int(task_epoch),
                    "prompt_hash": str(prompt_hash),
                },
                True,
            )
        _checkpoint, _result, wrote = code_tools._task_checkpoint_update(
            str(session_id), _updater
        )
        assert wrote, f"failed to seed checkpoint for {session_id!r}"

    async def test_canonical_session_id_header_validates_epoch_and_stale_fails_loud(self):
        tool_name = "__multi_ca_epoch_probe_" + uuid.uuid4().hex
        calls = []

        async def probe():
            calls.append("called")
            return json.dumps({"ok": True})

        canonical_sid = "canonical-" + uuid.uuid4().hex
        legacy_sid = "legacy-" + uuid.uuid4().hex
        old_db = None
        code_tools.TOOL_MAP[tool_name] = probe
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            old_db = self._use_temp_sessions_db(tmp)
            try:
                self._seed_task_checkpoint(canonical_sid, 12, "canonical-hash")
                self._seed_task_checkpoint(legacy_sid, 8, "legacy-hash")
                canonical = await orchestrator_server.dispatch_tool_call(
                    tool_name, {}, transport="api",
                    request_meta={"headers": {
                        "X-Session-Id": canonical_sid,
                        "X-Task-Epoch": "12",
                        "X-Prompt-Hash": "canonical-hash",
                    }},
                )
                legacy = await orchestrator_server.dispatch_tool_call(
                    tool_name, {}, transport="api",
                    request_meta={"headers": {
                        "X-Task-Session-ID": legacy_sid,
                        "X-Task-Epoch": "8",
                        "X-Prompt-Hash": "legacy-hash",
                    }},
                )
                stale = await orchestrator_server.dispatch_tool_call(
                    tool_name, {}, transport="api",
                    request_meta={"headers": {
                        "X-Session-Id": canonical_sid,
                        "X-Task-Epoch": "11",
                        "X-Prompt-Hash": "canonical-hash",
                    }},
                )
                self.assertTrue(json.loads(canonical.text).get("ok"))
                self.assertTrue(json.loads(legacy.text).get("ok"))
                stale_payload = json.loads(stale.text)
                self.assertFalse(stale_payload.get("ok"), stale_payload)
                self.assertTrue(stale_payload.get("error_name"), stale_payload)
                self.assertEqual(calls, ["called", "called"])
            finally:
                self._restore_sessions_db(old_db)
                code_tools.TOOL_MAP.pop(tool_name, None)

    async def test_mutation_event_uses_session_captured_at_admission(self):
        tool_name = "__multi_ca_admission_probe_" + uuid.uuid4().hex
        replacement_session = None

        async def probe(project_path):
            nonlocal replacement_session
            with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                conn.execute(
                    "UPDATE sessions SET status='closed', ended_at=? WHERE session_id=?",
                    (time.time(), admitted_session),
                )
                conn.commit()
            replacement_session = code_tools._auto_open_session(
                project_path, {"agent_id": "agent-a"}
            )
            return json.dumps({"ok": True})

        old_db = None
        old_auto = orchestrator_server.AUTO_WRITE_MODE
        code_tools.TOOL_MAP[tool_name] = probe
        orchestrator_server._MUTATING_TOOLS.add(tool_name)
        orchestrator_server.AUTO_WRITE_MODE = False
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
                self._registered_tool_schema(
                    tool_name, {"project_path": {"type": "string"}}):
            old_db = self._use_temp_sessions_db(tmp)
            try:
                with self._use_local_durable_writer(tmp) as writer:
                    project = str(Path(tmp) / "repo")
                    Path(project).mkdir()
                    admitted_session = code_tools._auto_open_session(
                        project, {"agent_id": "agent-a"}
                    )
                    response = await orchestrator_server.dispatch_tool_call(
                        tool_name, {"project_path": project}, transport="api",
                        request_meta={"headers": {
                            "X-Agent-Id": "agent-a",
                            "X-Operation-ID": "admission-" + uuid.uuid4().hex,
                        }},
                    )
                    self.assertTrue(json.loads(response.text).get("ok"))
                    self.assertTrue(replacement_session)
                    self.assertNotEqual(replacement_session, admitted_session)
                    await orchestrator_server._flush_post_call_accounting(timeout=3.0)
                    self.assertTrue(writer.wait_until_idle(3.0))

                    with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                        rows = conn.execute(
                            "SELECT session_id FROM events WHERE tool_name=?", (tool_name,)
                        ).fetchall()
                    self.assertEqual([row[0] for row in rows], [admitted_session])
            finally:
                self._restore_sessions_db(old_db)
                orchestrator_server.AUTO_WRITE_MODE = old_auto
                orchestrator_server._MUTATING_TOOLS.discard(tool_name)
                code_tools.TOOL_MAP.pop(tool_name, None)

    def test_committed_row_wins_higher_attempt_memory_and_db_records(self):
        old_db = None
        old_ledger = dict(orchestrator_server._WRITE_LEDGER)
        old_targets = dict(orchestrator_server._WRITE_LEDGER_BY_TARGET)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            old_db = self._use_temp_sessions_db(tmp)
            now = time.time()
            op_id = "ledger-" + uuid.uuid4().hex
            committed = {
                "operation_id": op_id, "tool": "write_fix", "status": "committed",
                "project_path": "C:/repo", "file_path": "target.py",
                "session_id": "session-a", "started_at": now, "finished_at": now,
                "duration_ms": 1, "attempts": 1, "previous_status": "in_flight",
                "result": {"ok": True, "winner": "committed"},
                "origin_generation": "test", "updated_at": now,
            }
            try:
                self.assertTrue(orchestrator_server._write_ledger_db_upsert(committed))
                for status, attempts in (("in_flight", 9), ("failed", 10)):
                    challenger = dict(committed)
                    challenger.update({
                        "status": status, "attempts": attempts,
                        "previous_status": "committed",
                        "result": {"ok": False, "challenger": status},
                        "updated_at": now + attempts,
                    })
                    self.assertTrue(orchestrator_server._write_ledger_db_upsert(challenger))
                orchestrator_server._WRITE_LEDGER[op_id] = dict(committed)
                orchestrator_server._write_ledger_finish(
                    op_id, {"ok": False, "error_name": "TEST_FAILURE"}, "error"
                )
                with sqlite3.connect(str(code_tools._SESSIONS_DB)) as conn:
                    row = conn.execute(
                        "SELECT status, attempts, result_json FROM write_operations "
                        "WHERE operation_id=?", (op_id,)
                    ).fetchone()
                self.assertEqual(row[:2], ("committed", 1))
                self.assertEqual(json.loads(row[2])["winner"], "committed")
                self.assertEqual(orchestrator_server._WRITE_LEDGER[op_id]["status"], "committed")
            finally:
                orchestrator_server._WRITE_LEDGER.clear()
                orchestrator_server._WRITE_LEDGER.update(old_ledger)
                orchestrator_server._WRITE_LEDGER_BY_TARGET.clear()
                orchestrator_server._WRITE_LEDGER_BY_TARGET.update(old_targets)
                self._restore_sessions_db(old_db)

    def test_begin_reconciles_stale_memory_to_committed_sqlite_record(self):
        old_db = None
        old_ledger = dict(orchestrator_server._WRITE_LEDGER)
        old_targets = dict(orchestrator_server._WRITE_LEDGER_BY_TARGET)
        tool_name = "__multi_ca_ledger_probe_" + uuid.uuid4().hex
        body = {"project_path": "C:/repo", "file_path": "target.py"}
        session_id = "session-a"
        code_tools.TOOL_MAP[tool_name] = lambda **_kwargs: json.dumps({"ok": True})
        orchestrator_server._MUTATING_TOOLS.add(tool_name)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            old_db = self._use_temp_sessions_db(tmp)
            op_id = orchestrator_server._write_ledger_operation_id(
                tool_name, body, session_id
            )
            now = time.time()
            committed = {
                "operation_id": op_id, "tool": tool_name, "status": "committed",
                "project_path": "c:\\repo", "file_path": "target.py",
                "session_id": session_id, "started_at": now, "finished_at": now,
                "duration_ms": 1, "attempts": 1, "previous_status": "in_flight",
                "result": {"ok": True}, "origin_generation": "test", "updated_at": now,
            }
            stale_memory = dict(committed)
            stale_memory.update({"status": "failed", "attempts": 99, "result": {"ok": False}})
            try:
                self.assertTrue(orchestrator_server._write_ledger_db_upsert(committed))
                orchestrator_server._WRITE_LEDGER[op_id] = stale_memory
                begun = orchestrator_server._write_ledger_begin(tool_name, body, session_id)
                self.assertEqual(begun, op_id)
                self.assertEqual(orchestrator_server._WRITE_LEDGER[op_id]["status"], "committed")
                self.assertEqual(orchestrator_server._WRITE_LEDGER[op_id]["attempts"], 1)
            finally:
                orchestrator_server._WRITE_LEDGER.clear()
                orchestrator_server._WRITE_LEDGER.update(old_ledger)
                orchestrator_server._WRITE_LEDGER_BY_TARGET.clear()
                orchestrator_server._WRITE_LEDGER_BY_TARGET.update(old_targets)
                self._restore_sessions_db(old_db)
                orchestrator_server._MUTATING_TOOLS.discard(tool_name)
                code_tools.TOOL_MAP.pop(tool_name, None)
