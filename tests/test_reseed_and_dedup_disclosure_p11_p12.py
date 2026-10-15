"""Campaign 1608 P11 + P12 regression coverage.

P11 — read_file_range(force_reseed=True) called through the `path`/file_path
alias WITHOUT project_path used to return ok:true while silently NOT reseeding
the write-freshness token. That is a dead loop: write_fix fails
FILE_CHANGED_OUTSIDE_ORCHESTRATOR, its own hint tells the CA to reseed with
force_reseed=true, the reseed returns ok:true, the retry fails identically.
The recovery path must either actually reseed (root inferred from the absolute
file_path) or fail loud (ok:false naming exactly what is missing) — never a
silent ok:true no-op.

P12 — the duplicate/response cache returns a prior verdict without
re-evaluating external conditions. When it does, the response must disclose
that it is cached (cached:true + when the verdict was first produced) so a CA
can tell "still wrong" apart from "not re-run". The deterministic write-ledger
operation_id is stable across identical retries; a later send must be identifiable
(operation_attempts>1) so a stable operation_id is not mistaken for a stale cached
object. #36 dropped operation_replayed: it restated that count and read as "not run".
"""

import asyncio
import json
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiohttp.test_utils import make_mocked_request

import code_tools
import orchestrator_server


class P11ForceReseedFailsLoudOrWorksTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="p11_reseed_"))
        self.project_path = self.tempdir
        self.sample_file = self.project_path / "sample.py"
        self.sample_file.write_text(
            "# header\n\ndef foo():\n    return 1\n", encoding="utf-8"
        )

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_force_reseed_without_project_path_actually_reseeds_via_inferred_root(self):
        """P11 core: alias path (file_path only) + force_reseed must reseed.

        generate_project_map plants .orchestrator_cache so find_project_root can
        recover the root from the absolute file_path. Before the fix this
        returned ok:true with read_token_seeded:false and left NO token.
        """
        asyncio.run(code_tools.generate_project_map(str(self.project_path)))

        # Called exactly as P11 reports: absolute file_path, NO project_path.
        result = json.loads(asyncio.run(code_tools.read_file_range(
            str(self.sample_file), 1, 4, force_reseed=True,
        )))

        self.assertTrue(result.get("ok"), msg=result)
        self.assertTrue(
            result.get("read_token_seeded"),
            msg=f"force_reseed via inferred root must seed the token: {result}",
        )
        # The token must actually exist so a following write_fix would pass.
        token = code_tools._read_token_get(str(self.project_path.resolve()), "sample.py")
        self.assertTrue(token and token.get("read_hash"), msg=token)

    def test_force_reseed_that_cannot_resolve_a_root_fails_loud(self):
        """P11 fail-loud: no marker, no project_path -> no inferable root.

        An explicit force_reseed that cannot seed MUST NOT return a silent
        ok:true. It fails loud (ok:false) and names what is missing.
        """
        # No generate_project_map => no .orchestrator_cache marker; temp lives
        # under HOME so find_project_root returns None.
        with mock.patch.object(code_tools, "find_project_root", return_value=None):
            result = json.loads(asyncio.run(code_tools.read_file_range(
                str(self.sample_file), 1, 4, force_reseed=True,
            )))

        self.assertFalse(
            result.get("ok"),
            msg=f"unresolvable force_reseed must fail loud, not silent ok:true: {result}",
        )
        self.assertEqual(result.get("error_name"), "RESEED_FAILED", msg=result)
        self.assertFalse(result.get("read_token_seeded"), msg=result)
        # Names exactly what is missing.
        self.assertIn("project_path", (result.get("hint") or "").lower(), msg=result)

    def test_plain_read_without_force_reseed_is_unaffected(self):
        """Guard: a normal read (no force_reseed, no project_path) still succeeds."""
        result = json.loads(asyncio.run(code_tools.read_file_range(
            str(self.sample_file), 1, 4,
        )))
        self.assertTrue(result.get("ok"), msg=result)
        self.assertIn("def foo", result.get("result", ""), msg=result)


class P12DuplicateGuardDisclosesCacheTests(unittest.TestCase):
    def setUp(self):
        orchestrator_server._DUPLICATE_FAILURE_STATE.clear()

    def tearDown(self):
        orchestrator_server._DUPLICATE_FAILURE_STATE.clear()

    def test_duplicate_retry_guard_discloses_cached_and_first_seen(self):
        """P12: the blocked (cached) verdict must announce itself as cached."""
        agent, tool, body, err = "sess:agentA", "write_fix", {"a": 1}, "SCHEMA_VIOLATION"

        first = orchestrator_server._duplicate_retry_guard(agent, tool, body, err)
        self.assertIsNone(first, "first identical failure records only, returns None")

        blocked = orchestrator_server._duplicate_retry_guard(agent, tool, body, err)
        self.assertIsNotNone(blocked, "second identical failure must return a verdict")
        self.assertEqual(blocked.get("error_name"), "DUPLICATE_RETRY_BLOCKED", msg=blocked)
        # Disclosure: this is a cached verdict, not a fresh re-evaluation.
        self.assertTrue(blocked.get("cached"), msg=blocked)
        self.assertIn("first_seen_at", blocked, msg=blocked)
        self.assertIn("seconds_since_first_seen", blocked, msg=blocked)
        self.assertIsInstance(blocked["seconds_since_first_seen"], (int, float), msg=blocked)


TOOL_NAME = "__p12_replay_probe__"


class P12OperationReplayDisclosureTests(unittest.IsolatedAsyncioTestCase):
    """An identical failing mutating retry has a stable operation_id; the
    response must reveal it is a replay (operation_attempts>1) so the caller
    does not mistake a stable id for a stale cached object."""

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
        orchestrator_server.SERVER_GENERATION = "p12-replay-generation"
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
        # P12: close SessionWriter before temp_dir.cleanup() or Windows raises
        # WinError 32 (sessions.db still held open by the writer).
        from orchestrator.session import store
        store.close_writer()
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
        self.temp_dir.cleanup()

    async def _dispatch(self, body: dict, query: str = ""):
        request = make_mocked_request(
            "POST",
            f"/api/call/{TOOL_NAME}{query}",
            payload=_body_reader(body),
            match_info={"tool_name": TOOL_NAME},
        )
        return await orchestrator_server._dispatch_tool_call_request(request)

    async def test_identical_failing_retry_is_marked_as_replay(self):
        # A non-deterministic error_name so _duplicate_retry_guard does NOT fire;
        # this isolates the operation_id/ledger replay disclosure.
        async def always_fails(**_kwargs) -> str:
            return json.dumps({"ok": False, "error": "still stale",
                               "error_name": "FILE_CHANGED_OUTSIDE_ORCHESTRATOR"})

        code_tools.TOOL_MAP[TOOL_NAME] = always_fails
        # Empty body: the synthetic probe declares no schema, so any field would
        # trip UNSUPPORTED_FIELD at S4. An empty body still yields a stable,
        # deterministic operation_id — exactly what makes an identical retry a
        # replay — which is what this test isolates.
        body: dict = {}

        first = json.loads((await self._dispatch(dict(body))).text)
        self.assertFalse(first.get("ok"), msg=first)
        self.assertEqual(first.get("operation_attempts"), 1, msg=first)
        self.assertNotIn("operation_replayed", first, msg=first)

        second = json.loads((await self._dispatch(dict(body))).text)
        self.assertFalse(second.get("ok"), msg=second)
        # Same deterministic operation_id proves it is the same payload...
        self.assertEqual(first.get("operation_id"), second.get("operation_id"), msg=second)
        # ...and the count says it is a later send, so a stable id is not read as
        # "cached". #36: the count is the whole fact -- no operation_replayed beside it.
        self.assertEqual(second.get("operation_attempts"), 2, msg=second)
        self.assertNotIn("operation_replayed", second, msg=second)

    def _with_scope(self, scope: str = "file_artifact"):
        # The replay flag speaks only for tools that write source (#9): a synthetic
        # probe has no mutation_scope, and "none" is a non-source scope.
        old = code_tools._MUTATION_SCOPE_MAP.get(TOOL_NAME)
        code_tools._MUTATION_SCOPE_MAP[TOOL_NAME] = scope

        def restore():
            if old is None:
                code_tools._MUTATION_SCOPE_MAP.pop(TOOL_NAME, None)
            else:
                code_tools._MUTATION_SCOPE_MAP[TOOL_NAME] = old
        self.addCleanup(restore)

    def _commits_then_fails(self):
        sends = []

        async def probe(**_kwargs) -> str:
            sends.append(1)
            if len(sends) == 1:
                return json.dumps({"ok": True, "new_hash": "h1"})
            return json.dumps({"ok": False, "error": "still stale",
                               "error_name": "FILE_CHANGED_OUTSIDE_ORCHESTRATOR"})

        code_tools.TOOL_MAP[TOOL_NAME] = probe

    async def test_a_projected_replay_still_warns_and_drops_the_rest(self):
        """fields=ok keeps what changes the next step and drops what only describes the call.

        2026-09-24: the envelope used to be stamped after the projection, so fields=ok
        answered generation / operation_id / write_status_url / operation_attempts --
        keys nobody asked for, on every call. Moving it through the projection must not
        hide the replay warning: a caller cannot ask for a key it does not know is coming,
        and this one says "an earlier send of this payload already wrote".
        """
        self._with_scope()
        self._commits_then_fails()
        info_only = ("generation", "operation_id", "write_status_url", "operation_attempts")

        first = json.loads((await self._dispatch({}, "?fields=ok")).text)
        self.assertIs(first.get("ok"), True, msg=first)
        # A first attempt is not a replay, and a false flag warns of nothing.
        self.assertNotIn("operation_replayed", first, msg=first)
        for key in info_only:
            self.assertNotIn(key, first, msg=first)

        second = json.loads((await self._dispatch({}, "?fields=ok")).text)
        self.assertIs(second.get("ok"), False, msg=second)
        # #36: attempts > 1 is not a warning, so no operation_replayed is rescued.
        self.assertNotIn("operation_replayed", second, msg=second)
        self.assertIs(second.get("likely_already_applied"), True, msg=second)
        self.assertIn("read_file_range", second.get("replay_hint", ""), msg=second)
        for key in info_only:
            self.assertNotIn(key, second, msg=second)

        # Naming an envelope key keeps it, like any other key. Every send is counted,
        # and the commit of the first send stays a warning on every later failure.
        third = json.loads((await self._dispatch({}, "?fields=ok,operation_attempts")).text)
        self.assertEqual(third.get("operation_attempts"), 3, msg=third)
        self.assertIs(third.get("likely_already_applied"), True, msg=third)
        self.assertNotIn("fields_unknown", third, msg=third)

    async def test_a_replay_whose_sends_all_failed_is_not_called_applied(self):
        """#9, measured 2026-09-24: two sends that BOTH failed answered
        likely_already_applied:true while /api/write_status for the same
        operation said not_committed + safe_to_retry. The ledger is the one
        answer; nothing was committed, so nothing is claimed."""
        self._with_scope()

        async def always_fails(**_kwargs) -> str:
            return json.dumps({"ok": False, "error": "still stale",
                               "error_name": "FILE_CHANGED_OUTSIDE_ORCHESTRATOR"})

        code_tools.TOOL_MAP[TOOL_NAME] = always_fails
        await self._dispatch({})
        second = json.loads((await self._dispatch({})).text)
        self.assertEqual(second.get("operation_attempts"), 2, msg=second)
        self.assertNotIn("likely_already_applied", second, msg=second)
        self.assertNotIn("replay_hint", second, msg=second)
        led = orchestrator_server._WRITE_LEDGER[second["operation_id"]]
        self.assertEqual(orchestrator_server._write_ledger_commit_state(led),
                         "not_committed", msg=led)

    async def _write_status(self, op_id: str) -> dict:
        request = make_mocked_request("GET", f"/api/write_status?operation_id={op_id}")
        return json.loads((await orchestrator_server.handle_write_status(request)).text)

    async def test_a_committed_rerun_of_a_derived_index_is_not_a_double_write(self):
        """A derived-index rebuild after a committed run re-derives; it cannot
        double-write source, so the flag would send the CA to verify nothing."""
        self._with_scope("derived_index")
        self._commits_then_fails()
        await self._dispatch({})
        second = json.loads((await self._dispatch({})).text)
        self.assertEqual(second.get("operation_attempts"), 2, msg=second)
        self.assertNotIn("likely_already_applied", second, msg=second)
        # write_status asks the same helper, so it gives the same answer: rerun.
        status = await self._write_status(second["operation_id"])
        self.assertEqual((status["commit_state"], status["previous_status"]),
                         ("not_committed", "committed"), msg=status)
        self.assertIs(status["safe_to_retry"], True, msg=status)
        self.assertEqual(status["ca_next_action"], "retry_write", msg=status)

    async def test_a_committed_operation_resent_runs_and_the_row_describes_that_send(self):
        """#36, measured 2026-09-25: restart_orchestrator_via_bat re-sent with the
        payload of a 09-20 restart RAN, yet its ledger row stayed the 09-20 row --
        status committed, started_at 09-20, origin_generation of the dead daemon --
        and the answer said operation_replayed:true, which reads as "did not run".
        The row must describe the send that just ran, on disk as in memory."""
        self._with_scope()
        sends = []

        async def always_commits(**_kwargs) -> str:
            sends.append(1)
            return json.dumps({"ok": True, "new_hash": f"h{len(sends)}"})

        code_tools.TOOL_MAP[TOOL_NAME] = always_commits
        first = json.loads((await self._dispatch({})).text)
        op_id = first["operation_id"]
        first_row = dict(orchestrator_server._WRITE_LEDGER[op_id])
        orchestrator_server.SERVER_GENERATION = "p12-replay-generation-after-restart"
        second = json.loads((await self._dispatch({})).text)

        self.assertEqual(len(sends), 2)
        self.assertEqual(second.get("operation_id"), op_id, msg=second)
        self.assertEqual(second.get("operation_attempts"), 2, msg=second)
        self.assertNotIn("operation_replayed", second, msg=second)
        self.assertNotIn("likely_already_applied", second, msg=second)
        for label, row in (
                ("memory", orchestrator_server._WRITE_LEDGER[op_id]),
                ("sqlite", orchestrator_server._write_ledger_db_get(operation_id=op_id))):
            self.assertEqual((row["status"], row["attempts"], row["previous_status"]),
                             ("committed", 2, "committed"), msg=(label, row))
            self.assertEqual(row["origin_generation"],
                             "p12-replay-generation-after-restart", msg=(label, row))
            self.assertGreater(row["started_at"], first_row["started_at"], msg=(label, row))
            self.assertEqual(row["result"]["new_hash"], "h2", msg=(label, row))

    async def test_write_status_after_commit_then_fail_does_not_invite_a_resend(self):
        """#36: the row is the LATEST send, so commit-then-fail reads not_committed --
        true of that send, and still no reason to resend source: the first send's
        edit already landed. The envelope and write_status must say the same thing."""
        self._with_scope()
        self._commits_then_fails()
        await self._dispatch({})
        second = json.loads((await self._dispatch({})).text)
        third = json.loads((await self._dispatch({})).text)
        # previous_status is sticky: a later failure does not erase the commit.
        for answer in (second, third):
            self.assertIs(answer.get("likely_already_applied"), True, msg=answer)
            self.assertEqual(answer.get("ca_next_action"), "read_file_range", msg=answer)
        status = await self._write_status(third["operation_id"])
        self.assertEqual((status["commit_state"], status["previous_status"],
                          status["attempts"]), ("not_committed", "committed", 3), msg=status)
        self.assertIs(status["safe_to_retry"], False, msg=status)
        self.assertEqual(status["ca_next_action"],
                         "read_file_range_to_confirm_before_retry", msg=status)

    async def test_a_newer_send_on_disk_beats_an_older_committed_row(self):
        """#36: _write_ledger_choose used to return ANY committed record first, so a
        committed row outranked every later send and begin counted from it."""
        committed = {"operation_id": "op-36", "tool": TOOL_NAME, "status": "committed",
                     "attempts": 1, "updated_at": 1.0}
        newer = dict(committed, status="failed", attempts=2, previous_status="committed",
                     updated_at=2.0)
        for memory, disk, winner in ((committed, newer, "sqlite"),
                                     (newer, committed, "memory")):
            chosen, source = orchestrator_server._write_ledger_choose(memory, disk)
            self.assertEqual((source, chosen["attempts"]), (winner, 2))
        # Equal attempts: a terminal phase beats in_flight, committed beats failed.
        racing = dict(committed, status="in_flight")
        self.assertEqual(orchestrator_server._write_ledger_choose(racing, committed)[1],
                         "sqlite")
        self.assertEqual(orchestrator_server._write_ledger_choose(
            committed, dict(committed, status="failed"))[1], "memory")


def _body_reader(payload: dict):
    from aiohttp import StreamReader
    loop = asyncio.new_event_loop()
    try:
        reader = StreamReader(protocol=mock.Mock(), limit=2 ** 16, loop=loop)
        reader.feed_data(json.dumps(payload).encode("utf-8"))
        reader.feed_eof()
        return reader
    finally:
        loop.close()


if __name__ == "__main__":
    unittest.main()
