"""Regression coverage for write-ledger finalization in the shared dispatcher.

WHY: a caller timing out, receiving a malformed tool response, or cancelling a
request must never leave the durable write status ambiguous.  Synthetic tools
keep these tests independent from filesystem and session side effects.
"""

import asyncio
import json
import os
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
from orchestrator.fileops import xroot


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

    def _probe_schema(self, **properties):
        real_schema = orchestrator_server._schema_for_tool

        def schema_for(name):
            if name == TOOL_NAME:
                return {"name": TOOL_NAME, "parameters": {"type": "object", "properties": properties}}
            return real_schema(name)

        return unittest.mock.patch.object(orchestrator_server, "_schema_for_tool", schema_for)

    async def test_an_xroot_admitted_write_still_names_the_callers_session(self):
        """#17, measured live 2026-09-25: write_status said session_id "" for a CA that
        HAD opened its session.

        A cross-root write is admitted by the xroot policy with no session required, and
        the dispatcher skipped the session LOOKUP along with the requirement -- so every
        such write reached the ledger anonymous. Admission stays as it is; what the ledger
        names is the caller's OWN open session, and a caller that has none gets "": the
        lookup never opens a session and never borrows another agent's.

        #35: cross-root is read against the CALL's project_path, not from the spelling, so
        the admitted write here lands outside project_path; a target inside it takes the
        session gate however it is spelled (next test).
        """
        orchestrator_server._NO_SESSION_REQUIRED.discard(TOOL_NAME)
        project = Path(self.temp_dir.name) / "repo"
        elsewhere = Path(self.temp_dir.name) / "elsewhere"
        project.mkdir()
        # The target the xroot gate must admit as cross-root: otherwise this pins nothing.
        verdict = xroot.classify_write_target(elsewhere / "mine.py", project)
        self.assertTrue(verdict["allowed"] and verdict["cross_root"], verdict)
        key = code_tools._session_project_key(str(project))
        mine = code_tools._auto_open_session(str(project), {}, agent_id="ledger-agent")
        other = code_tools._auto_open_session(str(project), {}, agent_id="other-agent")
        self.assertNotEqual(mine, other)

        async def xroot_write(project_path: str = "", file_path: str = "") -> str:
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[TOOL_NAME] = xroot_write

        async def ledger_session_of(agent: str, name: str) -> str:
            # One target per caller: the ledger replays an identical payload.
            body = {"project_path": str(project), "file_path": str(elsewhere / name)}
            request = make_mocked_request(
                "POST", f"/api/call/{TOOL_NAME}", headers={"X-Agent-Id": agent},
                payload=_body_reader(body), match_info={"tool_name": TOOL_NAME})
            with self._probe_schema(project_path={"type": "string"}, file_path={"type": "string"}):
                response = await orchestrator_server._dispatch_tool_call_request(request)
            self.assertEqual(response.status, 200, response.text)
            operation_id = self._operation_id(body)
            live = orchestrator_server._WRITE_LEDGER[operation_id]["session_id"]
            durable = orchestrator_server._write_ledger_db_get(operation_id=operation_id)
            self.assertEqual(durable["session_id"], live)
            return live

        self.assertEqual(await ledger_session_of("ledger-agent", "mine.py"), mine)
        self.assertEqual(await ledger_session_of("stranger-agent", "stranger.py"), "")
        self.assertIsNone(code_tools._get_active_session_id(key, "stranger-agent"))

    async def test_an_in_project_target_takes_the_session_gate_however_spelled(self):
        """#35: the dispatcher judged "cross-root" from the SPELLING -- an absolute path,
        measured against [A]'s tree -- so an absolute path into the caller's own project
        skipped the session gate that the same file spelled relative met. The route is
        read from the resolved target against the call's project_path now.
        """
        orchestrator_server._NO_SESSION_REQUIRED.discard(TOOL_NAME)
        project = Path(self.temp_dir.name) / "repo"
        project.mkdir()
        calls = []

        async def probe(project_path: str = "", file_path: str = "") -> str:
            calls.append(file_path)
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[TOOL_NAME] = probe
        for spelling in ("a.py", str(project / "a.py")):
            body = {"project_path": str(project), "file_path": spelling}
            with self._probe_schema(project_path={"type": "string"}, file_path={"type": "string"}), \
                    unittest.mock.patch.object(orchestrator_server, "AUTO_WRITE_MODE", False):
                response = await self._dispatch(body)
            self.assertEqual(response.status, 409, (spelling, response.text))
            self.assertEqual(json.loads(response.text)["error_name"], "ACTIVE_SESSION_REQUIRED", spelling)
        self.assertEqual(calls, [])

    async def test_the_deny_list_meets_every_target_field_a_call_names(self):
        """#35, measured 2026-09-25 in a sandbox: with project_path set to the home
        directory, four of five spellings landed in ``.ssh``, and the dispatcher read only
        the FIRST of path/file_path/dst/target_file -- move_file's ``destination`` never
        met the deny list. Each body names ``.ssh/x`` in a different field. The probe is
        session-EXEMPT: the old deny sites sat behind the session and backup gates, so an
        exempt tool never met them at all.
        """
        home = Path(self.temp_dir.name) / "home"
        (home / ".ssh").mkdir(parents=True)
        calls = []

        async def probe(project_path: str = "", file_path: str = "", path: str = "",
                        destination: str = "", paths: list | None = None,
                        operations: list | None = None) -> str:
            calls.append((file_path, path, destination, paths, operations))
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[TOOL_NAME] = probe
        text = {"type": "string"}
        bodies = {
            "file_path": {"file_path": ".ssh/x"},
            "path": {"path": ".ssh/x"},
            "absolute": {"file_path": str(home / ".ssh" / "x")},
            "destination": {"file_path": "notes.txt", "destination": ".ssh/x"},
            "paths": {"paths": ["notes.txt", ".ssh/x"]},
            "operations": {"operations": [{"file_path": "notes.txt"}, {"file_path": ".ssh/x"}]},
        }
        with unittest.mock.patch.dict(os.environ, {"USERPROFILE": str(home)}), self._probe_schema(
                project_path=text, file_path=text, path=text, destination=text,
                paths={"type": "array", "items": text},
                operations={"type": "array", "items": {"type": "object"}}):
            for label, fields in bodies.items():
                response = await self._dispatch({"project_path": str(home), **fields})
                self.assertEqual(response.status, 403, (label, response.text))
                payload = json.loads(response.text)
                self.assertEqual((payload["error_name"], payload["group"]),
                                 ("XROOT_DENIED", "user_dotfiles"), label)
            self.assertEqual(calls, [])
            # Positive control: the same fields naming allowed targets reach the tool.
            response = await self._dispatch(
                {"project_path": str(home), "file_path": "notes.txt", "destination": "kept.txt"})
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(len(calls), 1)

    async def test_an_index_only_tool_stages_the_hand_edited_sot(self):
        """Measured 2026-09-29: git_stage's ``paths`` were read as write targets (#35), so
        staging .council/council_config.json -- the hand-edited SOT -- met the live_files
        deny and the edit to it could not be committed through [A]. The dispatcher hands
        the tool name to write_targets: an INDEX_ONLY_TOOLS call names no write target,
        and the same body on any other mutating tool is still refused.
        """
        project = Path(self.temp_dir.name) / "repo"
        project.mkdir()
        calls = []

        async def probe(project_path: str = "", paths: list | None = None) -> str:
            calls.append(paths)
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[TOOL_NAME] = probe
        body = {"project_path": str(project), "paths": [".council/council_config.json"]}
        text = {"type": "string"}
        with self._probe_schema(project_path=text, paths={"type": "array", "items": text}):
            response = await self._dispatch(body)
            self.assertEqual(response.status, 403, response.text)
            payload = json.loads(response.text)
            self.assertEqual((payload["error_name"], payload["group"]), ("XROOT_DENIED", "live_files"))
            self.assertEqual(calls, [])
            with unittest.mock.patch.object(xroot, "INDEX_ONLY_TOOLS", frozenset({TOOL_NAME})):
                response = await self._dispatch(body)
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(calls, [[".council/council_config.json"]])

    async def test_an_output_field_meets_the_deny_list_on_a_read_tool(self):
        """#35, measured 2026-09-25: desktop_screenshot / screenshot_page are read tools, so
        the gate never looked at their ``save_path`` and the PNG landed in ``.ssh`` with no
        verdict; backup_project's ``backup_dir`` was read by no gate at all. An OUTPUT field
        is a write target on every tool. A read tool's own ``file_path`` stays a file it
        READS -- that, and an allowed output, are the controls that must still pass.
        """
        orchestrator_server._MUTATING_TOOLS.discard(TOOL_NAME)
        home = Path(self.temp_dir.name) / "home"
        (home / ".ssh").mkdir(parents=True)
        calls = []

        async def probe(project_path: str = "", file_path: str = "", save_path: str = "",
                        backup_dir: str = "") -> str:
            calls.append((file_path, save_path, backup_dir))
            return json.dumps({"ok": True})

        code_tools.TOOL_MAP[TOOL_NAME] = probe
        text = {"type": "string"}
        secret = str(home / ".ssh" / "x.png")
        with unittest.mock.patch.dict(os.environ, {"USERPROFILE": str(home)}), self._probe_schema(
                project_path=text, file_path=text, save_path=text, backup_dir=text):
            for field in ("save_path", "backup_dir"):
                response = await self._dispatch({"project_path": str(home), field: secret})
                self.assertEqual(response.status, 403, (field, response.text))
                payload = json.loads(response.text)
                self.assertEqual((payload["error_name"], payload["group"]),
                                 ("XROOT_DENIED", "user_dotfiles"), field)
            self.assertEqual(calls, [])
            response = await self._dispatch({"project_path": str(home), "file_path": secret})
            self.assertEqual(response.status, 200, response.text)
            response = await self._dispatch({"project_path": str(home), "save_path": str(home / "shot.png")})
            self.assertEqual(response.status, 200, response.text)
        self.assertEqual(calls, [(secret, "", ""), ("", str(home / "shot.png"), "")])

    async def test_an_output_field_never_picks_the_cross_root_route(self):
        """#35: ``cross_root`` lets a mutation skip the session gate because the audit route
        stands in for it, and that is a judgement on the files the tool MUTATES. A call
        that mutates nothing cross-root but puts its output there still meets the session
        gate; the same probe mutating a cross-root file is the control that skips it.
        """
        orchestrator_server._NO_SESSION_REQUIRED.discard(TOOL_NAME)
        project = Path(self.temp_dir.name) / "repo"
        project.mkdir()
        elsewhere = Path(self.temp_dir.name) / "elsewhere"
        calls = []

        async def probe(project_path: str = "", file_path: str = "", save_path: str = "") -> str:
            calls.append((file_path, save_path))
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[TOOL_NAME] = probe
        text = {"type": "string"}
        with self._probe_schema(project_path=text, file_path=text, save_path=text), \
                unittest.mock.patch.object(orchestrator_server, "AUTO_WRITE_MODE", False):
            response = await self._dispatch({"project_path": str(project), "save_path": str(elsewhere / "x.png")})
            self.assertEqual(response.status, 409, response.text)
            self.assertEqual(json.loads(response.text)["error_name"], "ACTIVE_SESSION_REQUIRED")
            self.assertEqual(calls, [])
            response = await self._dispatch({"project_path": str(project), "file_path": str(elsewhere / "y.py")})
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(calls, [(str(elsewhere / "y.py"), "")])

    # ── #36: an identical send while its twin is still running ─────────────────
    #
    # The operation id is f(tool, payload, session), so two identical sends are ONE
    # operation. The probe's default scope is 'none' (a rerun recomputes), which is why
    # test_concurrent_same_operation_never_regresses_committed_state still sees both
    # run; the tests below make it a source writer, the case that applied an edit twice.

    def _probe_scope(self, scope: str):
        old = code_tools._MUTATION_SCOPE_MAP.get(TOOL_NAME)
        code_tools._MUTATION_SCOPE_MAP[TOOL_NAME] = scope

        def restore():
            if old is None:
                code_tools._MUTATION_SCOPE_MAP.pop(TOOL_NAME, None)
            else:
                code_tools._MUTATION_SCOPE_MAP[TOOL_NAME] = old
        self.addCleanup(restore)

    def _held_write(self):
        """A probe that runs until released; `runs` counts how often it STARTED."""
        state = SimpleNamespace(runs=0, started=asyncio.Event(), release=asyncio.Event())

        async def held_write() -> str:
            state.runs += 1
            state.started.set()
            await state.release.wait()
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[TOOL_NAME] = held_write
        return state

    def _seed_row(self, status: str) -> str:
        op = self._operation_id()
        orchestrator_server._WRITE_LEDGER[op] = {
            "operation_id": op, "tool": TOOL_NAME, "status": status, "attempts": 1,
            "previous_status": None, "started_at": 0.0, "updated_at": 0.0,
            "origin_generation": orchestrator_server.SERVER_GENERATION}
        return op

    async def test_an_identical_send_while_its_twin_runs_is_refused_before_it_runs(self):
        """#36: the twin gets 409 OPERATION_IN_FLIGHT naming the running operation, the
        tool runs ONCE, and the row keeps describing the first send -- attempts 1, then
        committed -- instead of being taken over by a send that ran as well."""
        self._probe_scope("file_artifact")
        state = self._held_write()
        op = self._operation_id()
        first = asyncio.create_task(self._dispatch())
        await asyncio.wait_for(state.started.wait(), timeout=2)
        twin = await asyncio.wait_for(self._dispatch(), timeout=2)
        payload = json.loads(twin.text)
        self.assertEqual(
            (twin.status, payload["error_name"], payload["operation_id"], payload["retryable"]),
            (409, "OPERATION_IN_FLIGHT", op, False), twin.text)
        self.assertEqual(payload["write_status_url"], f"/api/write_status?operation_id={op}")
        live = orchestrator_server._WRITE_LEDGER[op]
        self.assertEqual((live["status"], live["attempts"]), ("in_flight", 1))
        state.release.set()
        self.assertEqual((await asyncio.wait_for(first, timeout=2)).status, 200)
        self.assertEqual(state.runs, 1)
        self._assert_ledger_status("committed")
        self.assertEqual(orchestrator_server._WRITE_LEDGER[op]["attempts"], 1)

    async def test_an_identical_send_of_a_non_source_writer_still_runs(self):
        """#36 control: a derived_index rerun recomputes, so its twin is not refused."""
        self._probe_scope("derived_index")
        state = self._held_write()
        first = asyncio.create_task(self._dispatch())
        await asyncio.wait_for(state.started.wait(), timeout=2)
        twin = asyncio.create_task(self._dispatch())
        for _ in range(200):
            if state.runs == 2 or twin.done():
                break
            await asyncio.sleep(0.01)
        state.release.set()
        responses = await asyncio.wait_for(asyncio.gather(first, twin), timeout=2)
        self.assertEqual([r.status for r in responses], [200, 200])
        self.assertEqual(state.runs, 2)

    async def test_a_row_left_in_flight_with_nothing_running_does_not_refuse(self):
        """#36 control: in_flight in the ledger is not "running". An older generation's
        crash, or a finish that failed, leaves such a row with no dispatcher behind it;
        only the cancel event the dispatcher holds for the run means running."""
        self._probe_scope("file_artifact")
        state = self._held_write()
        state.release.set()
        op = self._seed_row("in_flight")
        response = await self._dispatch()
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(state.runs, 1)
        self.assertEqual(orchestrator_server._WRITE_LEDGER[op]["attempts"], 2)

    async def test_a_twin_whose_row_already_committed_does_not_refuse(self):
        """#36 control: after the twin's commit its dispatcher still holds the cancel
        event for a moment, but the edit has landed. A send then is a resend after
        commit -- previous_status says so -- not a concurrent one."""
        self._probe_scope("file_artifact")
        state = self._held_write()
        state.release.set()
        op = self._seed_row("committed")
        orchestrator_server._OPERATION_CANCEL_EVENTS[op] = object()
        self.addCleanup(orchestrator_server._OPERATION_CANCEL_EVENTS.pop, op, None)
        response = await self._dispatch()
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(state.runs, 1)
        self.assertEqual(orchestrator_server._WRITE_LEDGER[op]["previous_status"], "committed")

    async def test_begin_alone_marks_the_operation_live_so_a_twin_is_refused(self):
        """#36 root: refusing a concurrent identical send must not depend on the
        dispatcher registering the cancel event AFTER begin returned. A twin whose begin
        ran in that gap saw the in_flight row without the event and ran as well.
        _write_ledger_begin now registers the liveness marker itself, under the async
        lock every begin shares, so a second begin for the same operation is refused with
        no dispatcher code running between the two -- this test drives begin directly,
        never the dispatcher."""
        self._probe_scope("file_artifact")
        op = orchestrator_server._write_ledger_begin(TOOL_NAME, {}, "")
        self.addCleanup(orchestrator_server._OPERATION_CANCEL_EVENTS.pop, op, None)
        self.assertTrue(op)
        # begin itself marked the operation live -- not a later dispatcher step.
        self.assertIn(op, orchestrator_server._OPERATION_CANCEL_EVENTS)
        self.assertEqual(orchestrator_server._WRITE_LEDGER[op]["status"], "in_flight")
        with self.assertRaises(orchestrator_server.WriteLedgerSendInFlight):
            orchestrator_server._write_ledger_begin(TOOL_NAME, {}, "")
        # the refused twin opened no second run: attempts stays 1.
        self.assertEqual(orchestrator_server._WRITE_LEDGER[op]["attempts"], 1)

if __name__ == "__main__":
    unittest.main()
