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
operation_id is stable across identical retries; a replay must be identifiable
(operation_attempts>1 / operation_replayed) so a stable operation_id is not
mistaken for a stale cached object.
"""

import asyncio
import json
import shutil
import tempfile
import unittest
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

    async def _dispatch(self, body: dict):
        request = make_mocked_request(
            "POST",
            f"/api/call/{TOOL_NAME}",
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
        self.assertFalse(first.get("operation_replayed"), msg=first)

        second = json.loads((await self._dispatch(dict(body))).text)
        self.assertFalse(second.get("ok"), msg=second)
        # Same deterministic operation_id proves it is the same payload...
        self.assertEqual(first.get("operation_id"), second.get("operation_id"), msg=second)
        # ...and the replay is disclosed, so a stable id is not read as "cached".
        self.assertEqual(second.get("operation_attempts"), 2, msg=second)
        self.assertTrue(second.get("operation_replayed"), msg=second)


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
