import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.server import mcp_stdio


class McpGenerationStampTests(unittest.TestCase):
    def setUp(self):
        mcp_stdio._MCP_SERVER_GENERATION_SEEN = None

    def tearDown(self):
        mcp_stdio._MCP_SERVER_GENERATION_SEEN = None

    def test_generation_change_self_heals_by_default(self):
        """TA-5: a restart must not kill MCP for the rest of the client's life.

        The gate used to latch the first generation and fail every later call with
        MCP_STALE_RELOAD_REQUIRED until a manual client reload. Tools proxy to the
        live daemon over HTTP, so the stamp is diagnostic, not a correctness guard:
        the gate now re-baselines and lets the call through."""
        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-a"))

        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-b"))
        # re-baselined, so the NEW generation is now the quiet steady state
        self.assertEqual(mcp_stdio._MCP_SERVER_GENERATION_SEEN, "gen-b")
        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-b"))

    def test_strict_mode_still_fails_closed(self):
        """CLM_MCP_STRICT_GENERATION=1 restores the hard reload signal."""
        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-a"))

        with mock.patch.dict(os.environ, {"CLM_MCP_STRICT_GENERATION": "1"}):
            stale = mcp_stdio._mcp_generation_gate("gen-b")

        self.assertIsNotNone(stale)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["error_name"], "MCP_STALE_RELOAD_REQUIRED")
        self.assertEqual(stale["hint"], "Reload Claude/MCP client")
        self.assertEqual(stale["generation"], "gen-b")
        self.assertEqual(stale["expected_generation"], "gen-a")
        # strict mode must NOT adopt the new generation
        self.assertEqual(mcp_stdio._MCP_SERVER_GENERATION_SEEN, "gen-a")

    def test_unknown_generation_is_neither_baseline_nor_compared(self):
        self.assertIsNone(mcp_stdio._mcp_generation_gate(None))
        self.assertIsNone(mcp_stdio._MCP_SERVER_GENERATION_SEEN)
        self.assertIsNone(mcp_stdio._mcp_generation_gate("gen-a"))
        self.assertIsNone(mcp_stdio._mcp_generation_gate(None))
        self.assertEqual(mcp_stdio._MCP_SERVER_GENERATION_SEEN, "gen-a")

    def test_add_generation_preserves_json_object_fields(self):
        wrapped = mcp_stdio._mcp_add_generation('{"ok": true, "value": 3}', "gen-a")

        self.assertEqual(
            json.loads(wrapped),
            {"ok": True, "value": 3, "generation": "gen-a"},
        )


def _rpc(server, method, params=None):
    response = server.registry.dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    return response["result"]


def _answer_tool(server, answer):
    def sample(x: str) -> str:
        """Sample."""
        return answer

    server.tool(sample)
    return server


class McpToolResultIsTheTextBlockOnlyTests(unittest.TestCase):
    """Every stdio answer is ONE text block holding the daemon's JSON, and nothing else.

    zeromcp publishes ``outputSchema {result: string}`` for a ``-> str`` tool, then sends
    ``structuredContent: {"result": <str>}`` next to the text. Every [A] wrapper returns
    the daemon's compact JSON as a str, so that structured copy is the same JSON escaped
    inside a JSON string, and Claude Code shows that copy. Measured 2026-09-25: a median
    of 110-172 backslashes per MCP answer, against 0 for the same tool over HTTP. That is
    a token tax paid on every call for a second copy of bytes the text block already has.
    """

    # A backslash and non-ASCII text: an escaped copy would change both.
    ANSWER = '{"ok":true,"path":"C:\\\\a","note":"Tiếng Việt"}'

    def test_zeromcp_itself_still_double_wraps_a_str_tool(self):
        """Positive control for the producer. If this goes RED, zeromcp changed how it
        answers, and the override below should be re-measured (or deleted), not kept on
        faith."""
        from zeromcp import McpServer

        server = _answer_tool(McpServer("control"), self.ANSWER)
        listed = _rpc(server, "tools/list")["tools"][0]
        called = _rpc(server, "tools/call", {"name": "sample", "arguments": {"x": "1"}})

        self.assertIn("outputSchema", listed)
        self.assertEqual(called["structuredContent"], {"result": self.ANSWER})

    def test_the_bridge_server_answers_with_the_text_block_only(self):
        server = _answer_tool(mcp_stdio._mcp_plain_text_server("t"), self.ANSWER)
        listed = _rpc(server, "tools/list")["tools"][0]
        called = _rpc(server, "tools/call", {"name": "sample", "arguments": {"x": "1"}})

        self.assertNotIn("outputSchema", listed)
        self.assertEqual(listed["inputSchema"]["required"], ["x"])
        self.assertEqual(called, {
            "content": [{"type": "text", "text": self.ANSWER}],
            "isError": False,
        })

    def test_run_mcp_stdio_serves_through_the_bridge_server(self):
        """The class above is only worth anything if the real launch uses it."""
        captured = []
        with mock.patch.dict(os.environ), \
                mock.patch.object(mcp_stdio, "_mcp_stdio_concurrent", captured.append):
            os.environ.pop("ORCHESTRATOR_MCP_FULL_SURFACE", None)
            os.environ.pop("ORCHESTRATOR_MCP_PROFILE", None)
            mcp_stdio.run_mcp_stdio()

        self.assertEqual(len(captured), 1, "run_mcp_stdio never reached its stdio pump")
        tools = _rpc(captured[0], "tools/list")["tools"]
        self.assertTrue(tools)
        self.assertEqual([t["name"] for t in tools if "outputSchema" in t], [])


def _launch_tool_names(**env):
    """Run the real run_mcp_stdio() up to its stdio pump and name what it serves."""
    captured = []
    with mock.patch.dict(os.environ), \
            mock.patch.object(mcp_stdio, "_mcp_stdio_concurrent", captured.append):
        os.environ.pop("ORCHESTRATOR_MCP_FULL_SURFACE", None)
        os.environ.pop("ORCHESTRATOR_MCP_PROFILE", None)
        os.environ.update(env)
        mcp_stdio.run_mcp_stdio()
    assert len(captured) == 1, "run_mcp_stdio never reached its stdio pump"
    return {t["name"] for t in _rpc(captured[0], "tools/list")["tools"]}


class McpDefaultSurfaceIsTheBootstrapCallsTests(unittest.TestCase):
    """A plain ``--mcp`` launch serves only the calls a CA makes before anything else works.

    Every tool a host lists costs context on every turn, and HTTP serves the rest without
    that cost. Measured 2026-09-25 over 277 local transcripts: of 21026 MCP calls, 3353
    went to the six shortcuts this launch stopped serving -- 3107 of them discovery
    (tool_names / tool_schema, which GET /api/tools/names and /api/tools/<t>/schema
    answer), the other 246 daemon tools that call_tool still reaches. get_rule_core_text
    and open_session stay first-class because they resolve COUNCIL_AGENT_ID from THIS
    host's env, which the daemon cannot see.
    """

    BOOTSTRAP = {"call_tool", "get_rule_core_text", "open_session"}

    def test_a_plain_launch_serves_the_bootstrap_calls_only(self):
        self.assertEqual(_launch_tool_names(), self.BOOTSTRAP)

    def test_every_dropped_shortcut_but_discovery_is_a_daemon_tool(self):
        """Dropping a shortcut is only safe while call_tool can still reach its tool.

        Discovery is the opposite case, and the rule text says so: tool_names and
        tool_schema are HTTP endpoints, not daemon tools, so call_tool cannot reach them.
        """
        import orchestrator_server as osrv

        routable = {t["name"] for t in osrv._tool_names_payload(limit=5000)["tools"]}
        dropped = {"list_sessions", "log_native_event",
                   "council_message_async", "council_worker_model"}

        self.assertEqual(sorted(dropped - routable), [])
        self.assertEqual(sorted({"tool_names", "tool_schema"} & routable), [])

    def test_the_council_profile_keeps_its_council_calls(self):
        """council_message_async injects lessons in THIS host (_inject_lessons), which
        call_tool does not, so a council-profile leader keeps it first-class."""
        names = _launch_tool_names(ORCHESTRATOR_MCP_PROFILE="council")

        self.assertLessEqual(self.BOOTSTRAP | {
            "council_message_async", "council_worker_model",
            "council_message", "convene_council"}, names)
        self.assertNotIn("tool_names", names)

    def test_every_rule_file_names_the_surface_this_launch_serves(self):
        """The rule text kept listing tool_names/tool_schema as served long after a
        launch pruned them. Each SOT file's sentence is parsed, so a drift either way
        goes red."""
        examples = Path(mcp_stdio.__file__).resolve().parents[2] / "examples"
        for name in ("CORE_RULE.txt", "SLIM_RULE.txt", "SLIM_RULE_INLINE.txt"):
            with self.subTest(name):
                hits = re.findall(r"MCP default surface = ([^.:;]+)",
                                  (examples / name).read_text(encoding="utf-8"))
                self.assertEqual(len(hits), 1, hits)
                self.assertEqual({t.strip(" `") for t in hits[0].split("+")},
                                 _launch_tool_names())


def _generated_pack_server(template_name, pack_id, relay_name, answer):
    """Exec one generated pack against the real zeromcp and return the server it runs.

    The relay (``_http`` / ``_call``) is replaced so nothing touches the network, and
    ``stdio`` is replaced so the server is captured instead of pumping stdin."""
    from zeromcp import McpServer

    from orchestrator.mcp_packs import generator

    template = getattr(generator, template_name)
    ns = {"__name__": "pack"}
    exec(template(generator._resolve_catalog(pack_id), {"ca_id": "t"}), ns)
    ns[relay_name] = lambda *_a, **_k: answer
    servers = []
    with mock.patch.object(McpServer, "stdio", lambda self, *_a, **_k: servers.append(self)):
        ns["run"]()
    return servers[0]


class GeneratedPackAnswersWithTheTextBlockOnlyTests(unittest.TestCase):
    """orchestrator/mcp_packs/generator.py writes stand-alone MCP servers, and those ran a
    plain zeromcp McpServer: every pack answer still carried the structuredContent copy
    the bridge stopped sending in c25f035. A pack now embeds the bridge's own
    _mcp_plain_text_server source, so there is one override and no second copy to drift.
    """

    ANSWER = McpToolResultIsTheTextBlockOnlyTests.ANSWER
    # (template, builtin pack, relay the tool answers through, tool, arguments)
    PACKS = (
        ("_server_template_http_relay", "toolq", "_http",
         "pack_call", {"method": "GET", "path": "/health"}),
        ("_server_template_orchestrator_call", "council", "_call",
         "call_tool", {"name": "council_status", "payload_json": "{}"}),
    )

    @staticmethod
    def _source(template, pack_id):
        from orchestrator.mcp_packs import generator

        return getattr(generator, template)(generator._resolve_catalog(pack_id), {"ca_id": "t"})

    def test_every_builtin_pack_answers_with_the_text_block_only(self):
        for template, pack_id, relay, tool, arguments in self.PACKS:
            with self.subTest(pack_id):
                server = _generated_pack_server(template, pack_id, relay, self.ANSWER)
                listed = _rpc(server, "tools/list")["tools"]
                self.assertTrue(listed)
                self.assertEqual([t["name"] for t in listed if "outputSchema" in t], [])
                called = _rpc(server, "tools/call", {"name": tool, "arguments": arguments})
                self.assertEqual(called, {
                    "content": [{"type": "text", "text": self.ANSWER}],
                    "isError": False,
                })

    def test_a_pack_embeds_the_bridge_override_rather_than_a_copy(self):
        """The source is read from the bridge at generation time, so a fix to the
        override reaches the next generated pack with no second edit."""
        import inspect

        override = inspect.getsource(mcp_stdio._mcp_plain_text_server)
        for template, pack_id, *_ in self.PACKS:
            with self.subTest(pack_id):
                self.assertEqual(self._source(template, pack_id).count(override), 1)

    def test_a_pack_without_zeromcp_still_exits_2(self):
        for template, pack_id, *_ in self.PACKS:
            with self.subTest(pack_id):
                ns = {"__name__": "pack"}
                exec(self._source(template, pack_id), ns)
                with mock.patch.dict("sys.modules", {"zeromcp": None}), \
                        mock.patch("sys.stderr"), \
                        self.assertRaises(SystemExit) as caught:
                    ns["run"]()
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
