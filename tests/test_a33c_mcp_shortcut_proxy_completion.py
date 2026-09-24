"""A33-C - every proxy-eligible MCP shortcut reaches the daemon, and the single
exception is pinned AS an exception.

A33 measured the defect (open_session said 257 tools while audit_tool_matrix said
258 on one generation) and gated its CONSEQUENCE at the producer. A33-B removed
the CAUSE for two shortcuts. A33-C finishes the sweep: get_rule_core_text,
begin_task, get_task_checkpoint, manage_agent and log_native_event now go through
_mcp_call_text -> dispatch_tool_call(transport="mcp") -> POST /api/call/<tool>.

bootstrap_session is NOT converted, and that is a measurement rather than an
oversight: it calls ct._bootstrap_with_cached_owner_claim (a private helper, not a
daemon tool) and hands it claim_cache=_MCP_OWNER_CLAIMS, which is state of THIS
host process. The public ct.bootstrap_session takes no claim_cache at all, so
proxying it would silently drop owner-claim caching. This file pins that reason,
so "finishing the job" later has to argue with a failing test, not with a comment.

get_rule_core_text needed one extra decision. Its fetch guard is per-process BY
DESIGN, and _rule_core_agent_key falls back to ORCHESTRATOR_AGENT_ID -- a variable
nothing in this repo sets. Sending an empty agent_id to the daemon would therefore
key the single shared "default" bucket, and a second agent would receive
already_fetched without ever seeing the rule text. The host resolves
COUNCIL_AGENT_ID itself instead, the same pattern A33-B used for open_session.
That collapse is pinned by DRIVING the key function, not by restating the
argument in a comment.

Every gate carries a positive control, and the T4 control is drawn dynamically
from TOOL_PHASES: _mcp_is_stateful_t4_tool keys on TOOL_PHASES, not on the tier
field tool_names reports, so a hardcoded name would rot the moment phases change.

Why source and not runtime: _mcp_call_text is a closure defined inside
run_mcp_stdio and cannot be reached from outside. Source is the only way to pin
what a shortcut body does; dispatch_tool_call is the only way to pin what happens
to the tool name afterwards.
"""

import ast
import asyncio
import importlib
import inspect
import json
import os
import sys
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
code_tools = importlib.import_module("code_tools")
orchestrator_server = importlib.import_module("orchestrator_server")
mcp_stdio = importlib.import_module("orchestrator.server.mcp_stdio")

BRIDGE = "run_mcp_stdio"

PROXIED = ("open_session", "prepare_ca_session", "get_rule_core_text",
           "begin_task", "get_task_checkpoint", "manage_agent",
           "log_native_event")

# The one shortcut that must keep running inside this process. The reason is
# host-local state, not a conversion nobody got around to -- see the docstring.
HOST_ONLY = ("bootstrap_session",)


def _nested_defs(source):
    """Every def/async def in a source blob, by name (first definition wins)."""
    found = {}
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(node.name, node)
    return found


def _bridge_defs():
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


def _keyword_value_names(node, keyword):
    """Every bare name handed to <keyword>= anywhere inside a body."""
    names = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        for kw in sub.keywords:
            if kw.arg == keyword and isinstance(kw.value, ast.Name):
                names.append(kw.value.id)
    return sorted(names)


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


class EveryProxyEligibleShortcutLeavesTheHost(unittest.TestCase):
    def test_every_named_shortcut_is_still_defined_in_the_bridge(self):
        # Positive control for this whole file: an AST gate that silently found
        # nothing would pass every assertion below vacuously.
        defs = _bridge_defs()
        for name in PROXIED + HOST_ONLY:
            self.assertIn(name, defs)

    def test_no_proxied_shortcut_names_srv_ct_any_more(self):
        defs = _bridge_defs()
        for name in PROXIED:
            self.assertEqual(_in_host_registry_calls(defs[name]), [],
                             "%s runs in the MCP host process again" % name)

    def test_each_proxied_shortcut_sends_exactly_its_own_tool_name(self):
        defs = _bridge_defs()
        for name in PROXIED:
            calls = _mcp_call_text_calls(defs[name])
            self.assertEqual(len(calls), 1, "%s: expected one proxy call" % name)
            self.assertEqual(calls[0][0], name)

    def test_payload_keys_are_exactly_the_wrapper_parameters(self):
        # A dropped key is silent: the daemon would just apply its own default,
        # so the shortcut would quietly stop honouring an argument it accepts.
        defs = _bridge_defs()
        for name in PROXIED:
            node = defs[name]
            self.assertEqual(_mcp_call_text_calls(node)[0][1],
                             _param_names(node), name)

    def test_a_shortcut_requires_exactly_what_the_daemon_requires(self):
        """#23: the MCP signature and the registry schema are ONE required list.

        log_native_event defaulted project_path="" in both, and the daemon then
        refused every call that took that default -- the published contract
        invited the one call that always fails. The shortcut also demanded
        `target`, which the daemon has always defaulted. agent_id is the one
        declared difference: the dispatcher fills it from the transport identity
        before the required check runs, so no caller has to send it.
        """
        defs = _bridge_defs()
        for name in PROXIED:
            spec = defs[name].args
            positional = spec.posonlyargs + spec.args
            required = {a.arg for a in positional[:len(positional) - len(spec.defaults)]}
            required |= {a.arg for a, d in zip(spec.kwonlyargs, spec.kw_defaults) if d is None}
            schema = orchestrator_server._tool_schema_payload(name)[0]["tool"]["function"]
            published = set(schema["parameters"].get("required") or []) - {"agent_id"}
            self.assertEqual(required, published, name)

    def test_the_daemon_names_the_field_it_refuses(self):
        """#23: the registry gate checks key PRESENCE, so project_path="" reaches the
        handler -- whose refusal carried no error_name a CA could route on."""
        refused = json.loads(asyncio.run(code_tools.log_native_event(
            action_type="probe", tool_name="probe", project_path="")))
        self.assertEqual(refused.get("error_name"), "MISSING_REQUIRED_FIELD", refused)

    def test_the_only_in_host_registry_call_left_is_the_declared_exception(self):
        # Whole-bridge sweep, not a per-name check: this is what stops a NEW
        # shortcut from being added in the old shape. run_mcp_stdio itself is
        # skipped because walking it necessarily contains its own children.
        defs = _bridge_defs()
        offenders = sorted(name for name, node in defs.items()
                           if name != BRIDGE and _in_host_registry_calls(node))
        self.assertEqual(offenders, sorted(HOST_ONLY))

    def test_detector_flags_the_a33_in_host_shape(self):
        source = ("def outer():\n"
                  "    def manage_agent(a):\n"
                  "        return asyncio.run(srv.ct.manage_agent(a))\n")
        node = _nested_defs(source)["manage_agent"]
        self.assertEqual(_in_host_registry_calls(node), ["manage_agent"])

    def test_detector_ignores_a_proxied_shape(self):
        source = ("def outer():\n"
                  "    def manage_agent(a):\n"
                  "        return _mcp_call_text('manage_agent', {'a': a})\n")
        node = _nested_defs(source)["manage_agent"]
        self.assertEqual(_in_host_registry_calls(node), [])
        self.assertEqual(_mcp_call_text_calls(node), [("manage_agent", {"a"})])


class TheBootstrapShortcutIsDeliberatelyNotProxied(unittest.TestCase):
    def test_it_still_reaches_the_private_helper_in_this_process(self):
        node = _bridge_defs()["bootstrap_session"]
        self.assertEqual(_in_host_registry_calls(node),
                         ["_bootstrap_with_cached_owner_claim"])

    def test_it_hands_over_the_host_local_claim_cache(self):
        # This argument IS the reason the shortcut stays here.
        node = _bridge_defs()["bootstrap_session"]
        self.assertEqual(_keyword_value_names(node, "claim_cache"),
                         ["_MCP_OWNER_CLAIMS"])

    def test_the_claim_cache_is_state_of_this_process(self):
        self.assertIsInstance(mcp_stdio._MCP_OWNER_CLAIMS, dict)

    def test_the_public_daemon_tool_cannot_carry_that_cache(self):
        # The measured reason, restated as a gate: if ct.bootstrap_session ever
        # grows a claim_cache parameter, this fails and the exception should be
        # reconsidered rather than left standing on a stale measurement.
        params = inspect.signature(code_tools.bootstrap_session).parameters
        self.assertIn("owner_claim_id", params)
        self.assertNotIn("claim_cache", params)


class TheRuleCoreGuardMustNotCollapseOntoOneBucket(unittest.TestCase):
    def test_the_host_resolves_the_agent_id_itself(self):
        # COUNCIL_AGENT_ID lives in THIS host's env, not the daemon's.
        node = _bridge_defs()["get_rule_core_text"]
        self.assertIn("COUNCIL_AGENT_ID", ast.unparse(node))

    def test_an_empty_agent_id_keys_the_shared_default_bucket(self):
        # Driven, not asserted in prose: this is why sending "" would be a
        # correctness regression once the cache lives in one daemon process.
        with mock.patch.dict(os.environ, {"ORCHESTRATOR_AGENT_ID": ""}):
            self.assertEqual(code_tools._rule_core_agent_key(""), "default")
            self.assertEqual(code_tools._rule_core_agent_key("ClauC"), "ClauC")

    def test_the_guard_cache_is_a_module_global(self):
        # Module-level dict == per-process == whose process it runs in matters.
        self.assertIsInstance(code_tools._RULE_CORE_FETCH_CACHE, dict)


class TheProxyBranchStillCoversAllOfThem(unittest.TestCase):
    def test_none_of_them_is_marked_t4(self):
        # The proxy branch applies to non-T4 tools only, so marking any of these
        # T4 would restore in-host execution with no visible symptom.
        for name in PROXIED:
            self.assertFalse(
                orchestrator_server._mcp_is_stateful_t4_tool(name),
                "%s became T4 -- it would silently run in-host again" % name)

    def test_a_real_t4_tool_is_still_recognised(self):
        # Positive control: a predicate answering False for everything would
        # pass the test above while proving nothing.
        name = _a_real_t4_tool_name()
        self.assertIsNotNone(name, "TOOL_PHASES declares no T4 tool at all")
        self.assertTrue(orchestrator_server._mcp_is_stateful_t4_tool(name))

    def test_none_of_them_is_in_the_exclusion_set(self):
        excluded = _dispatch_exclusion_set() or set()
        for name in PROXIED:
            self.assertNotIn(name, excluded)

    def test_the_exclusion_set_we_read_is_the_right_one(self):
        # Positive control for the AST extraction: tool_names is excluded on
        # purpose, because the bridge answers it locally.
        excluded = _dispatch_exclusion_set()
        self.assertIsNotNone(excluded, "could not locate the exclusion set")
        self.assertIn("tool_names", excluded)


if __name__ == "__main__":
    unittest.main()
