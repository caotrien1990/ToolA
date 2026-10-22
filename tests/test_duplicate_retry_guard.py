import asyncio
import io
import json
import logging
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request

import code_tools
import orchestrator_server as srv


def _proxy_loopback(request, timeout=None):
    """Serve the MCP proxy request the way the daemon on the other end does.

    B010 moved the MCP schema verdict to the daemon, so without this the test
    would POST to whatever [A] is listening on 8083 and assert against that
    process's duplicate ledger rather than its own -- passing or failing
    depending on what that server had already seen.

    A canned error envelope is not good enough either. The HTTP validator keeps
    its duplicate entry under a tuple key, while the bridge's _bridge_dedup uses
    a string key, so a canned reply would take a path that cannot see what the
    http leg recorded and the test would assert on the shape of the fake. In
    production both legs land on the same validator. Forwarding the request into
    the real dispatcher reproduces that, and nothing else does.
    """
    body = json.loads(request.data.decode("utf-8"))
    tool = request.full_url.rsplit("/", 1)[-1]
    response = srv._run_mcp_coro(srv._dispatch_tool_call_request(
        srv._InProcessToolRequest(tool, body, headers={}), transport="api"))
    raw = response.text.encode("utf-8")
    if response.status >= 400:
        raise urllib.error.HTTPError(
            request.full_url, response.status, "daemon refused",
            hdrs=None, fp=io.BytesIO(raw))

    class _Response:
        def read(self):
            return raw

    return _Response()


class DuplicateRetryGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        srv._DUPLICATE_FAILURE_STATE.clear()
        srv._DUPLICATE_FIRST_SEEN.clear()

    async def test_second_identical_schema_failure_is_blocked(self):
        """Same payload, http then mcp: the second one is refused as a duplicate.

        Both legs end up at the same validator -- directly for http, through the
        proxy for mcp -- which is what lets the second call recognise the first.
        """
        payload = {"unexpected_field": "x"}
        first = await srv.dispatch_tool_call("workflow_list", payload, transport="http")
        first_body = json.loads(first.text)
        self.assertEqual(first_body["error_name"], "UNSUPPORTED_FIELD")

        with mock.patch.object(urllib.request, "urlopen", _proxy_loopback):
            second = await srv.dispatch_tool_call(
                "workflow_list", payload, transport="mcp")
        second_body = json.loads(second.text)
        self.assertEqual(second_body["error_name"], "DUPLICATE_RETRY_BLOCKED")
        self.assertEqual(second_body["unsupported_fields"], ["unexpected_field"])
        self.assertFalse(second_body["retryable"])



class DedupeIdentityAndFingerprintTests(unittest.TestCase):
    """Group-3 Area 6a/6b unit pins: one identity helper; the fingerprint
    folds the normalized project key in instead of excluding project_path."""

    def setUp(self):
        srv._DUPLICATE_FAILURE_STATE.clear()
        srv._DUPLICATE_FIRST_SEEN.clear()

    def test_dedupe_identity_format(self):
        self.assertEqual(srv._dedupe_identity("s", "a"), "s:a")
        self.assertEqual(srv._dedupe_identity(None, None), "legacy:legacy")
        self.assertEqual(srv._dedupe_identity("", "x"), "legacy:x")
        self.assertEqual(srv._dedupe_identity("s", ""), "s:legacy")

    def test_cross_project_calls_do_not_collapse(self):
        """Rule F trace: an identical body in two projects is two separate
        first failures; only the repeat inside one project blocks."""
        ident = srv._dedupe_identity("sess", "agent")
        body_a = {"project_path": "D:\\Projects\\Alpha", "file_path": "x.txt"}
        body_b = {"project_path": "D:\\Projects\\Beta", "file_path": "x.txt"}
        self.assertIsNone(srv._duplicate_retry_guard(
            ident, "read_file_range", body_a, "FILE_NOT_FOUND"))
        self.assertIsNone(srv._duplicate_retry_guard(
            ident, "read_file_range", body_b, "FILE_NOT_FOUND"))
        blocked = srv._duplicate_retry_guard(
            ident, "read_file_range", body_a, "FILE_NOT_FOUND")
        self.assertEqual(blocked["error_name"], "DUPLICATE_RETRY_BLOCKED")

    def test_same_project_spelling_variants_still_collapse(self):
        """normpath + lower: case and trailing-separator variants of one
        project are the same project, so the repeat still blocks."""
        ident = srv._dedupe_identity("sess", "agent")
        body_a = {"project_path": "D:\\Projects\\Alpha", "file_path": "x.txt"}
        body_b = {"project_path": "d:\\projects\\alpha\\", "file_path": "x.txt"}
        self.assertIsNone(srv._duplicate_retry_guard(
            ident, "read_file_range", body_a, "FILE_NOT_FOUND"))
        blocked = srv._duplicate_retry_guard(
            ident, "read_file_range", body_b, "FILE_NOT_FOUND")
        self.assertIsNotNone(blocked)

    def test_project_less_calls_share_the_dot_key(self):
        ident = srv._dedupe_identity("sess", "agent")
        self.assertIsNone(srv._duplicate_retry_guard(
            ident, "some_tool", {"q": 1}, "FILE_NOT_FOUND"))
        fps = [k for k in srv._DUPLICATE_FAILURE_STATE if isinstance(k, str)]
        self.assertEqual(len(fps), 1)
        self.assertIn("|.|", fps[0])

    def test_evict_clears_both_dicts_for_the_exact_call_only(self):
        """Area 6d at unit level: eviction removes the exact call's entries
        from state AND first-seen, leaves other calls' entries, and never
        touches the schema gate's tuple keys sharing the state dict."""
        ident = srv._dedupe_identity("sess", "agent")
        body = {"project_path": "D:\\Projects\\Alpha", "file_path": "x.txt"}
        other = {"project_path": "D:\\Projects\\Alpha", "file_path": "y.txt"}
        srv._duplicate_retry_guard(ident, "read_file_range", body, "FILE_NOT_FOUND")
        srv._duplicate_retry_guard(ident, "read_file_range", other, "FILE_NOT_FOUND")
        tuple_key = ("scope", "sha", "UNSUPPORTED_FIELD")
        srv._DUPLICATE_FAILURE_STATE[tuple_key] = 3
        srv._duplicate_retry_evict(ident, "read_file_range", body)
        evicted_fp = srv._dedupe_call_key(ident, "read_file_range", body) + "|FILE_NOT_FOUND"
        kept_fp = srv._dedupe_call_key(ident, "read_file_range", other) + "|FILE_NOT_FOUND"
        self.assertNotIn(evicted_fp, srv._DUPLICATE_FAILURE_STATE)
        self.assertNotIn(evicted_fp, srv._DUPLICATE_FIRST_SEEN)
        self.assertIn(kept_fp, srv._DUPLICATE_FAILURE_STATE)
        self.assertEqual(srv._DUPLICATE_FAILURE_STATE[tuple_key], 3)
        # the evicted call is judged fresh; the kept one still blocks
        self.assertIsNone(srv._duplicate_retry_guard(
            ident, "read_file_range", body, "FILE_NOT_FOUND"))
        self.assertIsNotNone(srv._duplicate_retry_guard(
            ident, "read_file_range", other, "FILE_NOT_FOUND"))


class GateBoundaryIdentityTests(unittest.IsolatedAsyncioTestCase):
    """Area 6a (plan section 3.6): a pre-gate-recorded entry and the boundary
    consult of the same caller live under the identical identity string."""

    def setUp(self):
        srv._DUPLICATE_FAILURE_STATE.clear()
        srv._DUPLICATE_FIRST_SEEN.clear()

    async def test_gate_and_boundary_use_one_identity(self):
        headers = {"X-Session-Id": "g4sess", "X-Agent-Id": "g4agent"}
        gate = await srv._dispatch_tool_call_request(
            srv._InProcessToolRequest("manage_agent", {}, headers=headers),
            transport="api")
        self.assertEqual(json.loads(gate.text)["error_name"],
                         "MISSING_REQUIRED_FIELD")
        root = str(Path(srv.__file__).parent)
        boundary = await srv._dispatch_tool_call_request(
            srv._InProcessToolRequest(
                "read_file_range",
                {"project_path": root,
                 "file_path": "definitely_missing_g4.txt"},
                headers=headers), transport="api")
        self.assertEqual(json.loads(boundary.text)["error_name"],
                         "FILE_NOT_FOUND")
        fps = [k for k in srv._DUPLICATE_FAILURE_STATE
               if isinstance(k, str)]
        self.assertEqual(len(fps), 2)
        identities = {fp.split("|", 1)[0] for fp in fps}
        self.assertEqual(identities, {"g4sess:g4agent"})
        errors = {fp.rsplit("|", 1)[1] for fp in fps}
        self.assertEqual(errors, {"MISSING_REQUIRED_FIELD", "FILE_NOT_FOUND"})


class BridgeGenerationScopeTests(unittest.TestCase):
    """Area 6c (plan section 3.6): bridge verdicts are scoped by the judged
    payload's generation, and the gen0 latch warns once per absence
    episode, re-arming when a generation-bearing payload returns."""

    def setUp(self):
        srv._DUPLICATE_FAILURE_STATE.clear()
        srv._DUPLICATE_FIRST_SEEN.clear()
        srv._BRIDGE_GEN0_EPISODE = False

    def tearDown(self):
        srv._BRIDGE_GEN0_EPISODE = False

    def _dispatch_mcp(self, generation):
        def fake_urlopen(request, timeout=None):
            payload = {"ok": False, "error_name": "FILE_NOT_FOUND",
                       "error": "gone"}
            if generation is not None:
                payload["generation"] = generation
            raw = json.dumps(payload).encode("utf-8")
            raise urllib.error.HTTPError(
                request.full_url, 400, "err", hdrs=None, fp=io.BytesIO(raw))

        body = {"query": "g4-generation-scope", "session_id": "g4bridge"}
        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            return asyncio.run(srv.dispatch_tool_call(
                "search_code", dict(body), transport="mcp",
                request_meta={"headers": {"X-Agent-Id": "g4agent"}}))

    def test_generation_scope_resets_bridge_verdicts(self):
        """Rule F/epoch trace: G1 blocks on repeat; after the daemon
        restarts (G2) the same failure is fresh, then blocks under G2."""
        first = self._dispatch_mcp("G1")
        self.assertEqual(first.status, 400)
        self.assertEqual(json.loads(first.text)["error_name"], "FILE_NOT_FOUND")
        second = self._dispatch_mcp("G1")
        self.assertEqual(second.status, 409)
        self.assertEqual(json.loads(second.text)["error_name"],
                         "DUPLICATE_RETRY_BLOCKED")
        third = self._dispatch_mcp("G2")
        self.assertEqual(third.status, 400)
        self.assertEqual(json.loads(third.text)["error_name"], "FILE_NOT_FOUND")
        fourth = self._dispatch_mcp("G2")
        self.assertEqual(fourth.status, 409)
        self.assertEqual(json.loads(fourth.text)["error_name"],
                         "DUPLICATE_RETRY_BLOCKED")

    def test_gen0_latch_warns_once_per_episode(self):
        messages = []

        class _Collector(logging.Handler):
            def emit(self, record):
                messages.append(record.getMessage())

        handler = _Collector()
        srv.logger.addHandler(handler)
        try:
            self._dispatch_mcp(None)   # episode opens: warning 1
            self._dispatch_mcp(None)   # same episode: no new warning
            self._dispatch_mcp("G9")   # generation returns: latch re-arms
            self._dispatch_mcp(None)   # new episode: warning 2
        finally:
            srv.logger.removeHandler(handler)
        gen0_warnings = [m for m in messages if "gen0" in m]
        self.assertEqual(len(gen0_warnings), 2)


def _body_reader(payload: dict) -> StreamReader:
    """Return an aiohttp reader containing one JSON object."""
    loop = asyncio.new_event_loop()
    try:
        reader = StreamReader(
            protocol=mock.Mock(), limit=2 ** 16, loop=loop,
        )
        reader.feed_data(json.dumps(payload).encode("utf-8"))
        reader.feed_eof()
        return reader
    finally:
        loop.close()


class DedupeDispatchHarnessTests(unittest.IsolatedAsyncioTestCase):
    """Dispatch-level Area 6 pins over a synthetic mutating tool (the
    test_write_ledger_finalization harness shape): plan section 3.3 (a
    relabel never suppresses execution) and section 3.6 (evict-on-success
    sequence; the ledger row records the relabeled verdict)."""

    TOOL = "__dedupe_probe__"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db = code_tools._SESSIONS_DB
        self.old_tool = code_tools.TOOL_MAP.get(self.TOOL)
        self.old_mutating = set(srv._MUTATING_TOOLS)
        self.old_no_session = set(srv._NO_SESSION_REQUIRED)
        self.old_accounting = srv._POST_CALL_ACCOUNTING
        code_tools._SESSIONS_DB = Path(self.temp_dir.name) / "sessions.db"
        code_tools._sync_session_db_alias()
        code_tools._init_session_db()
        srv._WRITE_LEDGER.clear()
        srv._WRITE_LEDGER_BY_TARGET.clear()
        srv._WRITE_LEDGER_LAST_PURGE = 0.0
        srv._DUPLICATE_FAILURE_STATE.clear()
        srv._DUPLICATE_FIRST_SEEN.clear()
        srv._MUTATING_TOOLS.add(self.TOOL)
        srv._NO_SESSION_REQUIRED.add(self.TOOL)
        srv._POST_CALL_ACCOUNTING = SimpleNamespace(
            enqueue=lambda _record: True,
        )
        self.calls = 0
        self.mode = "fail"

        async def probe(**_kwargs) -> str:
            self.calls += 1
            if self.mode == "fail":
                return json.dumps({"ok": False, "error": "probe failure",
                                   "error_name": "FILE_NOT_FOUND"})
            return json.dumps({"ok": True, "write_performed": True})

        code_tools.TOOL_MAP[self.TOOL] = probe

    def tearDown(self):
        if self.old_tool is None:
            code_tools.TOOL_MAP.pop(self.TOOL, None)
        else:
            code_tools.TOOL_MAP[self.TOOL] = self.old_tool
        srv._MUTATING_TOOLS.clear()
        srv._MUTATING_TOOLS.update(self.old_mutating)
        srv._NO_SESSION_REQUIRED.clear()
        srv._NO_SESSION_REQUIRED.update(self.old_no_session)
        srv._WRITE_LEDGER.clear()
        srv._WRITE_LEDGER_BY_TARGET.clear()
        srv._WRITE_LEDGER_LAST_PURGE = 0.0
        srv._DUPLICATE_FAILURE_STATE.clear()
        srv._DUPLICATE_FIRST_SEEN.clear()
        srv._POST_CALL_ACCOUNTING = self.old_accounting
        code_tools._SESSIONS_DB = self.old_db
        code_tools._sync_session_db_alias()
        code_tools._session_store_module.close_writer()
        self.temp_dir.cleanup()

    async def _dispatch(self):
        request = make_mocked_request(
            "POST",
            f"/api/call/{self.TOOL}",
            payload=_body_reader({}),
            match_info={"tool_name": self.TOOL},
        )
        return await srv._dispatch_tool_call_request(request)

    async def test_relabel_never_suppresses_execution(self):
        """Section 3.3 (A9): the second identical call's verdict is
        relabeled DUPLICATE_RETRY_BLOCKED, but the handler still ran --
        dedupe is post-execution bookkeeping, never a gate."""
        first = await self._dispatch()
        self.assertEqual(json.loads(first.text)["error_name"],
                         "FILE_NOT_FOUND")
        second = await self._dispatch()
        self.assertEqual(json.loads(second.text)["error_name"],
                         "DUPLICATE_RETRY_BLOCKED")
        self.assertEqual(self.calls, 2)

    async def test_evict_on_success_sequence(self):
        """Area 6d trace: fail, succeed (evicts), fail again -- the second
        failure is judged fresh; only its own repeat blocks."""
        first = await self._dispatch()
        self.assertEqual(json.loads(first.text)["error_name"],
                         "FILE_NOT_FOUND")
        self.mode = "ok"
        success = await self._dispatch()
        self.assertTrue(json.loads(success.text)["ok"])
        self.mode = "fail"
        third = await self._dispatch()
        self.assertEqual(json.loads(third.text)["error_name"],
                         "FILE_NOT_FOUND")
        fourth = await self._dispatch()
        self.assertEqual(json.loads(fourth.text)["error_name"],
                         "DUPLICATE_RETRY_BLOCKED")
        self.assertEqual(self.calls, 4)

    async def test_ledger_row_records_the_relabeled_verdict(self):
        """Area 6e (plan section 3.6): the write-ledger row for the second
        call records the verdict the client received -- the relabeled
        error_name -- because the finish now runs after finalization."""
        await self._dispatch()
        second = await self._dispatch()
        payload = json.loads(second.text)
        self.assertEqual(payload["error_name"], "DUPLICATE_RETRY_BLOCKED")
        self.assertEqual(payload["original_error_name"], "FILE_NOT_FOUND")
        op_id = srv._write_ledger_operation_id(self.TOOL, {}, "")
        row = srv._WRITE_LEDGER[op_id]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["result"]["error_name"],
                         "DUPLICATE_RETRY_BLOCKED")
        durable = srv._write_ledger_db_get(operation_id=op_id)
        self.assertEqual(durable["status"], "failed")


if __name__ == "__main__":
    unittest.main()
