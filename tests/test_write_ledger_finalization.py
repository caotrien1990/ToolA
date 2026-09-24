"""Regression coverage for write-ledger finalization in the shared dispatcher.

WHY: a caller timing out, receiving a malformed tool response, or cancelling a
request must never leave the durable write status ambiguous.  Synthetic tools
keep these tests independent from filesystem and session side effects.
"""

import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request

import code_tools
import orchestrator_server


TOOL_NAME = "__ledger_finalization_probe__"


def _body_reader(payload: dict) -> StreamReader:
    """Return an aiohttp reader containing one JSON object."""
    loop = asyncio.new_event_loop()
    try:
        reader = StreamReader(
            protocol=unittest.mock.Mock(), limit=2 ** 16, loop=loop,
        )
        reader.feed_data(json.dumps(payload).encode("utf-8"))
        reader.feed_eof()
        return reader
    finally:
        loop.close()


class WriteLedgerFinalizationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise finalization through `_dispatch_tool_call_request`, not helpers."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db = code_tools._SESSIONS_DB
        self.old_tool = code_tools.TOOL_MAP.get(TOOL_NAME)
        self.old_mutating = set(orchestrator_server._MUTATING_TOOLS)
        self.old_no_session = set(orchestrator_server._NO_SESSION_REQUIRED)
        self.old_generation = orchestrator_server.SERVER_GENERATION
        self.old_accounting = orchestrator_server._POST_CALL_ACCOUNTING

        code_tools._SESSIONS_DB = Path(self.temp_dir.name) / "sessions.db"
        code_tools._sync_session_db_alias()
        code_tools._init_session_db()
        orchestrator_server.SERVER_GENERATION = "ledger-test-generation"
        orchestrator_server._WRITE_LEDGER.clear()
        orchestrator_server._WRITE_LEDGER_BY_TARGET.clear()
        orchestrator_server._WRITE_LEDGER_LAST_PURGE = 0.0
        orchestrator_server._DUPLICATE_FAILURE_STATE.clear()
        orchestrator_server._MUTATING_TOOLS.add(TOOL_NAME)
        orchestrator_server._NO_SESSION_REQUIRED.add(TOOL_NAME)
        orchestrator_server._POST_CALL_ACCOUNTING = SimpleNamespace(
            enqueue=lambda _record: True,
        )

    def tearDown(self):
        if self.old_tool is None:
            code_tools.TOOL_MAP.pop(TOOL_NAME, None)
        else:
            code_tools.TOOL_MAP[TOOL_NAME] = self.old_tool
        orchestrator_server._MUTATING_TOOLS.clear()
        orchestrator_server._MUTATING_TOOLS.update(self.old_mutating)
        orchestrator_server._NO_SESSION_REQUIRED.clear()
        orchestrator_server._NO_SESSION_REQUIRED.update(self.old_no_session)
        orchestrator_server._WRITE_LEDGER.clear()
        orchestrator_server._WRITE_LEDGER_BY_TARGET.clear()
        orchestrator_server._WRITE_LEDGER_LAST_PURGE = 0.0
        orchestrator_server._DUPLICATE_FAILURE_STATE.clear()
        orchestrator_server._POST_CALL_ACCOUNTING = self.old_accounting
        orchestrator_server.SERVER_GENERATION = self.old_generation
        code_tools._SESSIONS_DB = self.old_db
        code_tools._sync_session_db_alias()
        # A test that reaches the session gate leaves read connections open on
        # worker threads; Windows cannot remove a sessions.db they still hold.
        code_tools._session_store_module.close_writer()
        self.temp_dir.cleanup()

    async def _dispatch(self, body: dict | None = None):
        request = make_mocked_request(
            "POST",
            f"/api/call/{TOOL_NAME}",
            payload=_body_reader(body or {}),
            match_info={"tool_name": TOOL_NAME},
        )
        return await orchestrator_server._dispatch_tool_call_request(request)

    def _operation_id(self, body: dict | None = None) -> str:
        return orchestrator_server._write_ledger_operation_id(
            TOOL_NAME, body or {}, "",
        )

    def _assert_ledger_status(self, expected: str, body: dict | None = None):
        operation_id = self._operation_id(body)
        live = orchestrator_server._WRITE_LEDGER.get(operation_id)
        durable = orchestrator_server._write_ledger_db_get(operation_id=operation_id)
        self.assertIsNotNone(live, "dispatcher must create the live ledger record")
        self.assertIsNotNone(durable, "dispatcher must persist the ledger record")
        self.assertEqual(live["status"], expected)
        self.assertEqual(durable["status"], expected)

    async def test_successful_mutation_is_committed(self):
        async def successful_write() -> str:
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[TOOL_NAME] = successful_write
        response = await self._dispatch()

        self.assertEqual(response.status, 200)
        self._assert_ledger_status("committed")

    async def test_non_json_result_is_finalized_failed(self):
        async def malformed_write() -> str:
            return "this is not JSON"

        code_tools.TOOL_MAP[TOOL_NAME] = malformed_write
        response = await self._dispatch()

        self.assertEqual(response.status, 422)
        self.assertEqual(json.loads(response.text)["error_name"], "TOOL_RESPONSE_NOT_JSON")
        self._assert_ledger_status("failed")

    async def test_missing_argument_typeerror_is_finalized_failed(self):
        async def requires_argument_after_admission(**_kwargs) -> str:
            # The dispatcher reaches the actual call (and starts the ledger)
            # before a legacy handler reports its missing positional argument.
            raise TypeError("missing 1 required positional argument: 'required_value'")

        code_tools.TOOL_MAP[TOOL_NAME] = requires_argument_after_admission
        response = await self._dispatch()

        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.text)["error_name"], "MISSING_REQUIRED_FIELD")
        self._assert_ledger_status("failed")

    async def test_cancelled_dispatch_is_finalized_failed(self):
        started = asyncio.Event()
        never = asyncio.Event()

        async def blocked_write() -> str:
            started.set()
            await never.wait()
            return json.dumps({"ok": True})

        code_tools.TOOL_MAP[TOOL_NAME] = blocked_write
        task = asyncio.create_task(self._dispatch())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self._assert_ledger_status("failed")

    async def test_concurrent_same_operation_never_regresses_committed_state(self):
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        allow_success = asyncio.Event()
        allow_failure = asyncio.Event()
        invocation = 0

        async def contested_write() -> str:
            nonlocal invocation
            invocation += 1
            if invocation == 1:
                first_started.set()
                await allow_success.wait()
                return json.dumps({"ok": True, "write_performed": True})
            second_started.set()
            await allow_failure.wait()
            raise RuntimeError("second caller failed after first committed")

        code_tools.TOOL_MAP[TOOL_NAME] = contested_write
        first = asyncio.create_task(self._dispatch())
        await asyncio.wait_for(first_started.wait(), timeout=1)
        second = asyncio.create_task(self._dispatch())
        await asyncio.wait_for(second_started.wait(), timeout=1)

        allow_success.set()
        first_response = await asyncio.wait_for(first, timeout=1)
        self.assertEqual(first_response.status, 200)
        self._assert_ledger_status("committed")

        allow_failure.set()
        second_response = await asyncio.wait_for(second, timeout=1)
        self.assertEqual(second_response.status, 500)
        self._assert_ledger_status("committed")

    async def test_the_ledger_records_the_session_the_call_was_admitted_to(self):
        """#17 + #19: write_status must name the session the write was admitted to.

        The ledger stored `_task_session_id`, which is only an X-Session-Id header
        or a body session_id -- empty on every plain call -- so write_status could
        never say whose write it was. And a tool that looked its session up without
        an agent_id got the newest active session of ANY agent. Here a second
        agent's session is the newest one in the project on purpose.
        """
        orchestrator_server._NO_SESSION_REQUIRED.discard(TOOL_NAME)
        project = Path(self.temp_dir.name) / "repo"
        project.mkdir()
        key = code_tools._session_project_key(str(project))
        other = code_tools._auto_open_session(str(project), {}, agent_id="other-agent")
        conn = sqlite3.connect(code_tools._SESSIONS_DB)
        try:
            with conn:  # commits; only close() releases the file
                conn.execute("UPDATE sessions SET started_at=? WHERE session_id=?",
                             (datetime.now().strftime("%Y-%m-%dT23:59:59"), other))
        finally:
            conn.close()
        seen = []

        async def scoped_write(project_path: str = "") -> str:
            # The lookup shape write/core and copy_file use: no agent_id at all.
            seen.append(code_tools._get_active_session_id(key))
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[TOOL_NAME] = scoped_write
        request = make_mocked_request(
            "POST", f"/api/call/{TOOL_NAME}",
            headers={"X-Agent-Id": "ledger-agent"},
            payload=_body_reader({"project_path": str(project)}),
            match_info={"tool_name": TOOL_NAME},
        )
        real_schema = orchestrator_server._schema_for_tool

        def schema_for(name):
            # The probe has no registry entry, and the field gate reads only the
            # registry: publish the one field it takes so the SESSION gate is reached.
            if name == TOOL_NAME:
                return {"name": TOOL_NAME, "parameters": {
                    "type": "object", "properties": {"project_path": {"type": "string"}}}}
            return real_schema(name)

        with unittest.mock.patch.object(orchestrator_server, "_schema_for_tool", schema_for):
            response = await orchestrator_server._dispatch_tool_call_request(request)
        self.assertEqual(response.status, 200, response.text)

        admitted = code_tools._get_active_session_id(key, "ledger-agent")
        self.assertTrue(admitted)
        self.assertNotEqual(admitted, other)
        self.assertEqual(seen, [admitted])
        live = [rec for rec in orchestrator_server._WRITE_LEDGER.values()
                if rec.get("tool") == TOOL_NAME]
        self.assertEqual([rec["session_id"] for rec in live], [admitted])
        durable = orchestrator_server._write_ledger_db_get(
            operation_id=live[0]["operation_id"])
        self.assertEqual(durable["session_id"], admitted)

    async def test_the_ledger_charges_the_dispatch_tail_to_its_own_phase(self):
        """#2: a slow call's ledger must say WHERE the time went, not only duration_ms.

        The tool's own last phase ends when it returns; parsing, auto-chain and hints
        after that are the dispatcher's. Unmarked, they were charged to whatever phase
        the tool entered last.
        """
        from orchestrator.progress_watchdog import current_watchdog

        async def marking_write() -> str:
            current_watchdog().mark("write")
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[TOOL_NAME] = marking_write
        response = await self._dispatch()

        self.assertEqual(response.status, 200)
        progress = orchestrator_server._WRITE_LEDGER[self._operation_id()]["result"]["progress"]
        self.assertEqual(list(progress["phase_ms"]), ["starting", "write", "dispatch_post"])

    async def test_a_tool_that_reports_no_progress_keeps_its_ledger_shape(self):
        async def silent_write() -> str:
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[TOOL_NAME] = silent_write
        response = await self._dispatch()

        self.assertEqual(response.status, 200)
        result = orchestrator_server._WRITE_LEDGER[self._operation_id()]["result"]
        self.assertNotIn("progress", result)


if __name__ == "__main__":
    unittest.main()