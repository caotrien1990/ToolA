"""One anti-drift reminder, carried by the mutation token itself.

The token IS the destructive capability: a CA that holds it can call delete_file,
run_ps1_script, git_unstage and the rest of the 16. Every other layer that says
"do not delete what was not named" lives somewhere the agent has to go looking --
CORE_RULE, SLIM_RULE, the skills. This one rides on the handout, so it arrives at
the exact moment the power does, and again on the refusal a CA hits when it does
not have it yet.

Two sentences, deliberately: whitelist-only deletion, and fail loud. They are the
two failure modes behind CASE-2008 -- a delete nobody named, and an error that was
worked around instead of reported.

Asserted at all FOUR points a CA can meet the token, because a reminder present at
only some of them is exactly the drift this is meant to catch:

* ``GET /api/token``       -- the handout
* ``GET /api/token/refresh`` -- the renewal a long T4 round uses instead
* ``MUTATION_TOKEN_REQUIRED`` -- the refusal when no token was sent
* ``MUTATION_TOKEN_EXPIRED``  -- the refusal when the TTL lapsed

And on the three rule files, so the same text a CA reads at bootstrap is the text
it gets back over the wire.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

from aiohttp import StreamReader
from aiohttp.test_utils import make_mocked_request

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import orchestrator_server  # noqa: E402

# The two properties, as the substrings a caller can grep for. Kept short on
# purpose: a mini rule that has to be read carefully is one more thing to skim.
_NO_UNNAMED_DELETE = "Never delete a file that was not explicitly named and approved"
_FAIL_LOUD = "Fail loud"


def _body(payload: dict) -> StreamReader:
    loop = asyncio.new_event_loop()
    try:
        reader = StreamReader(protocol=unittest.mock.Mock(), limit=2 ** 16, loop=loop)
        reader.feed_data(json.dumps(payload).encode("utf-8"))
        reader.feed_eof()
        return reader
    finally:
        loop.close()


class TestTokenMiniRule(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._token = orchestrator_server.EXPECTED_TOKEN
        self._created = orchestrator_server.TOKEN_CREATED_AT
        self._auto = orchestrator_server.AUTO_TOKEN_INJECT

    def tearDown(self):
        orchestrator_server.EXPECTED_TOKEN = self._token
        orchestrator_server.TOKEN_CREATED_AT = self._created
        orchestrator_server.AUTO_TOKEN_INJECT = self._auto

    def _ttl_seconds(self) -> float:
        return orchestrator_server.TOKEN_TTL_MINUTES * 60

    def _assert_mini_rule(self, data: dict, where: str) -> None:
        rule = data.get("mini_rule")
        self.assertIsInstance(rule, str, "%s carries no mini_rule: %r" % (where, sorted(data)))
        self.assertIn(_NO_UNNAMED_DELETE, rule, where)
        self.assertIn(_FAIL_LOUD, rule, where)
        # Short enough to actually be read. The whole point is one glance.
        self.assertLess(len(rule), 240, "%s mini_rule has grown into a paragraph" % where)

    async def test_the_handout_carries_it(self):
        """GET /api/token is where a CA acquires the power to delete."""
        orchestrator_server.EXPECTED_TOKEN = "fresh"
        orchestrator_server.TOKEN_CREATED_AT = time.time()
        resp = await orchestrator_server.handle_token(make_mocked_request("GET", "/api/token"))
        data = json.loads(resp.text)
        self.assertEqual(data["token"], "fresh")
        self._assert_mini_rule(data, "/api/token")

    async def test_the_refresh_path_carries_it_too(self):
        """A long T4 round renews rather than re-fetches, and must not lose the rule."""
        orchestrator_server.EXPECTED_TOKEN = "fresh"
        orchestrator_server.TOKEN_CREATED_AT = time.time()
        resp = await orchestrator_server.handle_token_refresh(
            make_mocked_request("GET", "/api/token/refresh"))
        data = json.loads(resp.text)
        self._assert_mini_rule(data, "/api/token/refresh")
        # The pre-existing contract must survive the addition.
        self.assertEqual(data["use_header"], "X-Orchestrator-Token")
        self.assertIn("token", data["do_not_log_fields"])

    async def test_a_rotation_does_not_drop_the_rule(self):
        """An expired token is rotated in-place; the response is rebuilt, so re-assert."""
        orchestrator_server.EXPECTED_TOKEN = "stale"
        orchestrator_server.TOKEN_CREATED_AT = time.time() - (self._ttl_seconds() + 30)
        resp = await orchestrator_server.handle_token(make_mocked_request("GET", "/api/token"))
        data = json.loads(resp.text)
        self.assertNotEqual(data["token"], "stale", "rotation stopped happening")
        self._assert_mini_rule(data, "/api/token after rotation")

    async def _refuse(self, header: str | None, created_at: float) -> dict:
        orchestrator_server.EXPECTED_TOKEN = "good"
        orchestrator_server.TOKEN_CREATED_AT = created_at
        orchestrator_server.AUTO_TOKEN_INJECT = False
        # Two tools are excluded because they take their own non-token paths in the
        # dispatcher and would not reach the token gate.
        tool = sorted(orchestrator_server._TOKEN_REQUIRED_TOOLS
                      - {"run_project_command", "council_message"})[0]
        headers = {"X-Orchestrator-Token": header} if header is not None else {}
        request = make_mocked_request(
            "POST", "/api/call/" + tool, headers=headers,
            payload=_body({}), match_info={"tool_name": tool},
        )
        resp = await orchestrator_server._dispatch_tool_call_request(request)
        self.assertEqual(resp.status, 403)
        return json.loads(resp.text)

    async def test_the_no_token_refusal_carries_it(self):
        """MUTATION_TOKEN_REQUIRED is a CA's first contact with the destructive tier."""
        data = await self._refuse(None, time.time())
        self.assertEqual(data["error_name"], "MUTATION_TOKEN_REQUIRED")
        self._assert_mini_rule(data, "MUTATION_TOKEN_REQUIRED")

    async def test_the_expired_refusal_carries_it(self):
        """The TTL path is a separate return statement, so it needs its own assertion."""
        data = await self._refuse("good", time.time() - (self._ttl_seconds() + 30))
        self.assertEqual(data["error_name"], "MUTATION_TOKEN_EXPIRED")
        self._assert_mini_rule(data, "MUTATION_TOKEN_EXPIRED")

    def test_a_successful_call_is_not_nagged(self):
        """The reminder belongs on the handout and the refusal, not on every response.

        _error_response merges **extra into the payload, so a mini_rule passed at one
        more call site would start appearing on unrelated errors. Bounded on purpose:
        four sites, counted.
        """
        source = (_ROOT / "orchestrator_server.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("mini_rule"), 4, (
            "mini_rule is at %d call sites; it is meant to be exactly 4 "
            "(token, token/refresh, MUTATION_TOKEN_REQUIRED, MUTATION_TOKEN_EXPIRED)"
            % source.count("mini_rule")))

    def test_the_rule_files_say_the_same_thing(self):
        """Wire text and bootstrap text must agree, or the reminder becomes noise.

        All three rule files, because a CA on the slim template never reads
        CORE_RULE.txt and the inline body is the emergency fallback.
        """
        for name in ("CORE_RULE.txt", "SLIM_RULE.txt", "SLIM_RULE_INLINE.txt"):
            text = (_ROOT / "examples" / name).read_text(encoding="utf-8")
            self.assertIn("WHITELIST", text, name)
            self.assertIn("delete ONLY what is named", text, name)


if __name__ == "__main__":
    unittest.main()
