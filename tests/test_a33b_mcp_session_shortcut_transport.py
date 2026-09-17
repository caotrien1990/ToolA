"""A33-B - the two MCP session shortcuts must reach the daemon, not run in-host.

A33 measured the defect and gated its CONSEQUENCE at the producer: open_session
reported 257 tools while audit_tool_matrix reported 258 on the SAME generation,
because orchestrator/server/mcp_stdio.py ran the shortcut as
asyncio.run(srv.ct.open_session(...)) -- inside the long-lived MCP host process,
whose registry was imported before ocr_map existed. A33-B removes the CAUSE:
both shortcuts now go through _mcp_call_text -> dispatch_tool_call(
    transport="mcp"), whose non-T4 branch POSTs /api/call/<tool> to the daemon.

Two gates, because either one alone can be satisfied while the defect returns:

1. A source gate (AST over run_mcp_stdio): neither shortcut may name srv.ct
   again, and each must hand its own tool name plus a payload whose keys are
   exactly its own parameter names to _mcp_call_text.
2. A behavioural gate: the proxy branch in dispatch_tool_call applies only to
   non-T4 tools, so marking either shortcut T4 -- or adding its name to the
   exclusion set -- would silently restore in-host execution with no visible
   symptom. That is pinned, not assumed.

Every gate carries a positive control, and the T4 control is drawn dynamically
from TOOL_PHASES rather than hardcoded: _mcp_is_stateful_t4_tool keys on
TOOL_PHASES, NOT on the `tier` field tool_names reports, so a hardcoded name
would rot the moment phases change. A detector that has only ever seen a clean
tree is not yet a detector.

Why _mcp_call_text is not the observation point: it is a closure defined inside
run_mcp_stdio and cannot be reached from outside. Source is therefore the only
way to pin what the shortcut bodies do, and dispatch_tool_call the only way to
pin what happens to the tool name afterwards.
"""

import ast
import importlib
import inspect
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
code_tools = importlib.import_module("code_tools")
orchestrator_server = importlib.import_module("orchestrator_server")
mcp_stdio = importlib.import_module("orchestrator.server.mcp_stdio")

SHORTCUTS = ("open_session", "prepare_ca_session")


def _nested_defs(source):
    """Every def/async def in a source blob, by name (first definition wins)."""
    found = {}
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(node.name, node)
    return found


def _shortcut_nodes():
    return _nested_defs(inspect.getsource(mcp_stdio.run_mcp_stdio))


def _in_host_registry_calls(node):
    """Names reached as srv.ct.<name> -- the exact shape A33 measured."""
    hits = []
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Attribute)
                and sub.value.attr == "ct"
                and isinstance(sub.value.value, ast.Name)
                and sub.value.value.id == "srv"):
            hits.append(sub.attr)
    return sorted(hits)


def _mcp_call_text_calls(node):
    """(tool_name, payload_keys) for every _mcp_call_text call in a body."""
    calls = []
    for sub in ast.walk(node):
        if not (isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "_mcp_call_text"):
            continue
        tool = None
        if sub.args and isinstance(sub.args[0], ast.Constant):
            tool = sub.args[0].value
        keys = set()
        if len(sub.args) > 1 and isinstance(sub.args[1], ast.Dict):
            keys = {k.value for k in sub.args[1].keys
                    if isinstance(k, ast.Constant)}
        calls.append((tool, keys))
    return calls


def _param_names(node):
    spec = node.args
    return {p.arg for p in
            list(spec.posonlyargs) + list(spec.args) + list(spec.kwonlyargs)}


def _dispatch_source():
    return textwrap.dedent(
        inspect.getsource(orchestrator_server.dispatch_tool_call))


def _dispatch_exclusion_set():
    """The literal set of tool names dispatch_tool_call keeps OUT of the proxy."""
    for sub in ast.walk(ast.parse(_dispatch_source())):
        if not (isinstance(sub, ast.Compare)
                and isinstance(sub.left, ast.Name)
                and sub.left.id == "tool_name"):
            continue
        for op, comparator in zip(sub.ops, sub.comparators):
            if isinstance(op, ast.NotIn) and isinstance(comparator, ast.Set):
                return {e.value for e in comparator.elts
                        if isinstance(e, ast.Constant)}
    return None


def _a_real_t4_tool_name():
    """A genuinely-T4 name taken from the same mapping the predicate reads."""
    phases = getattr(code_tools, "TOOL_PHASES", {}) or {}
    names = sorted(name for name, phase in phases.items()
                   if "T4" in str(phase).upper())
    return names[0] if names else None


class TheSourceNoLongerReachesTheHostRegistry(unittest.TestCase):
    def test_both_shortcuts_are_still_found_in_the_bridge(self):
        # Positive control for every other test in this class: an AST gate that
        # silently found nothing would pass all of them vacuously.
        nodes = _shortcut_nodes()
        for name in SHORTCUTS:
            self.assertIn(name, nodes)

    def test_neither_shortcut_names_srv_ct_any_more(self):
        nodes = _shortcut_nodes()
        for name in SHORTCUTS:
            self.assertEqual(_in_host_registry_calls(nodes[name]), [],
                             "%s runs in the MCP host process again" % name)

    def test_each_shortcut_proxies_exactly_its_own_tool_name(self):
        nodes = _shortcut_nodes()
        for name in SHORTCUTS:
            calls = _mcp_call_text_calls(nodes[name])
            self.assertEqual(len(calls), 1, "%s: expected one proxy call" % name)
            self.assertEqual(calls[0][0], name)

    def test_payload_keys_are_exactly_the_wrapper_parameters(self):
        # A dropped key is silent: the daemon would just use its own default.
        nodes = _shortcut_nodes()
        for name in SHORTCUTS:
            node = nodes[name]
            keys = _mcp_call_text_calls(node)[0][1]
            self.assertEqual(keys, _param_names(node))

    def test_the_host_env_agent_id_still_rides_the_payload(self):
        # COUNCIL_AGENT_ID lives in THIS host's env, not the daemon's, so it has
        # to be resolved here or the daemon can never see it.
        nodes = _shortcut_nodes()
        for name in SHORTCUTS:
            self.assertIn("COUNCIL_AGENT_ID", ast.unparse(nodes[name]))

    def test_detector_flags_the_a33_in_host_shape(self):
        source = ("def outer():\n"
                  "    def open_session(a):\n"
                  "        return asyncio.run(srv.ct.open_session(a))\n")
        node = _nested_defs(source)["open_session"]
        self.assertEqual(_in_host_registry_calls(node), ["open_session"])

    def test_detector_ignores_a_proxied_shape(self):
        source = ("def outer():\n"
                  "    def open_session(a):\n"
                  "        return _mcp_call_text('open_session', {'a': a})\n")
        node = _nested_defs(source)["open_session"]
        self.assertEqual(_in_host_registry_calls(node), [])
        self.assertEqual(_mcp_call_text_calls(node), [("open_session", {"a"})])

    def test_detector_is_not_fooled_by_a_bare_ct_attribute(self):
        # Negative control: only srv.ct.<name> is the defect, not any .ct.
        source = ("def outer():\n"
                  "    def open_session(a):\n"
                  "        return other.ct.open_session(a)\n")
        node = _nested_defs(source)["open_session"]
        self.assertEqual(_in_host_registry_calls(node), [])


class TheProxyBranchMustKeepApplyingToBothShortcuts(unittest.TestCase):
    def test_neither_shortcut_is_marked_t4(self):
        # Measured when A33-B landed: open_session T1, prepare_ca_session T3.
        for name in SHORTCUTS:
            self.assertFalse(
                orchestrator_server._mcp_is_stateful_t4_tool(name),
                "%s became T4 -- the shortcut silently runs in-host again" % name)

    def test_a_real_t4_tool_is_still_recognised(self):
        # Positive control: a predicate that answered False for everything would
        # pass the test above while proving nothing.
        name = _a_real_t4_tool_name()
        self.assertIsNotNone(name, "TOOL_PHASES declares no T4 tool at all")
        self.assertTrue(orchestrator_server._mcp_is_stateful_t4_tool(name))

    def test_the_exclusion_set_we_read_is_the_right_one(self):
        # Positive control for the AST extraction: tool_names is excluded on
        # purpose (the bridge answers it locally).
        excluded = _dispatch_exclusion_set()
        self.assertIsNotNone(excluded, "could not locate the exclusion set")
        self.assertIn("tool_names", excluded)

    def test_neither_shortcut_is_excluded_from_the_proxy(self):
        excluded = _dispatch_exclusion_set() or set()
        for name in SHORTCUTS:
            self.assertNotIn(name, excluded)

    def test_the_proxy_branch_is_gated_on_the_t4_predicate(self):
        # Ties the two assertions above to the branch they are about: if the
        # condition stopped consulting the predicate, the T4 test would be
        # measuring something that no longer decides transport.
        source = _dispatch_source()
        self.assertIn("_mcp_is_stateful_t4_tool", source)
        self.assertIn("transport", source)


if __name__ == "__main__":
    unittest.main()
