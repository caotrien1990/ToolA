"""A34 phase 3: pin WHERE an MCP tool call actually executes.

Phase 3 was authorized as "migrate the ~39 in-process wrappers to the proxy".
That premise is FALSE, and this file is the measurement that says so, so that
nobody spends another campaign re-discovering it:

  * ``_mcp_call_text`` (mcp_stdio.py) calls
    ``srv.dispatch_tool_call(..., transport="mcp")``.
  * ``dispatch_tool_call`` (orchestrator_server.py) IS the local-vs-proxy
    split: for ``transport == "mcp"`` it POSTs to the live daemon over HTTP
    for every tool EXCEPT ``tool_names``/``tool_schema`` and the stateful T4
    set.

So the named wrappers already execute in the daemon. Measured live on
2026-09-18: a ``run_ps1_script`` issued through ``call_tool`` reported its
executor PID as the ``/health`` pid (40964) -- the daemon, not the MCP host.
Two cheaper-looking discriminators were tried first and BOTH are
confounded, which is why the PID is the one used here: ``generation`` is
persisted to a shared ``server_generation.json``, and ``ledger_source`` can
read ``memory`` after a DB row is cached. Neither proves which process ran
the tool.

Rewriting those wrappers to ``_mcp_proxy_call`` would therefore not move
execution at all; it would only DROP the two guards ``dispatch_tool_call``
adds on the way (council sender stamping + the duplicate-retry ledger). The
migration is refused on purpose, and
``test_the_council_message_wrapper_keeps_the_dispatcher_transport`` is the
regression that would catch someone doing it anyway.

What IS worth locking is the shape of the split, because the routing decision
is made from the MCP host's OWN ``ct.TOOL_PHASES`` -- the A33 root-cause
pattern -- so a tool quietly joining the exempt set would make the host a
second writer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SERVER = _ROOT / "orchestrator_server.py"
_BRIDGE = _ROOT / "orchestrator" / "server" / "mcp_stdio.py"

# The scopes a proxy-exempt tool is allowed to have. A T4 browser/desktop tool
# legitimately mutates a live page or a process -- that is why it must stay in
# the host process at all (the page session lives there). What it must NEVER do
# is write project files, because the daemon's write ledger would never see it.
# Deliberately a WHITELIST: a new scope (file, source, derived_index, stateful,
# ...) appearing in the exempt set fails this suite instead of sliding through.
_EXEMPT_SCOPES_ALLOWED = {"none", "runtime_ui", "process"}


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _find_def(tree: ast.AST, name: str) -> ast.AST:
    """Locate a def/async def by name anywhere in the tree.

    ``ast.walk`` deliberately: the wrappers under test are NESTED inside
    ``run_mcp_stdio``, so a module-body-only scan would find nothing and the
    assertion would vacuously pass.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found -- renamed or removed")


def _transport_guard(dispatch: ast.AST) -> ast.If:
    """The ``if transport == "mcp" and ...`` statement that owns the split."""
    for node in ast.walk(dispatch):
        if not isinstance(node, ast.If):
            continue
        for cmp_node in ast.walk(node.test):
            if (isinstance(cmp_node, ast.Compare)
                    and isinstance(cmp_node.left, ast.Name)
                    and cmp_node.left.id == "transport"
                    and any(isinstance(op, ast.Eq) for op in cmp_node.ops)
                    and any(isinstance(c, ast.Constant) and c.value == "mcp"
                            for c in cmp_node.comparators)):
                return node
    raise AssertionError(
        "dispatch_tool_call no longer branches on transport == 'mcp' -- the "
        "local-vs-proxy split moved; re-measure which process runs a tool "
        "before trusting any doc that says 'proxied'")


@pytest.fixture(scope="module")
def server_tree() -> ast.AST:
    return ast.parse(_src(_SERVER))


@pytest.fixture(scope="module")
def bridge_src() -> str:
    return _src(_BRIDGE)


def test_the_transport_split_lives_in_dispatch_tool_call(server_tree):
    """The split is a property of the dispatcher, not of each call site."""
    guard = _transport_guard(_find_def(server_tree, "dispatch_tool_call"))
    assert isinstance(guard, ast.If)


def test_the_only_literal_proxy_exemptions_are_the_two_discovery_tools(server_tree):
    """Adding a third hardcoded name to the exempt set must not be quiet.

    tool_names/tool_schema stay local because the bridge answers them from
    /api/tools/* itself (see _mcp_live_discovery); anything else staying local
    is a second-writer decision that deserves a failing test.
    """
    guard = _transport_guard(_find_def(server_tree, "dispatch_tool_call"))
    literals: set[str] = set()
    for node in ast.walk(guard.test):
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    literals.add(elt.value)
    assert literals == {"tool_names", "tool_schema"}, (
        f"proxy-exempt literals changed to {sorted(literals)}")


def test_the_stateful_t4_predicate_is_the_other_exemption(server_tree):
    """The T4 carve-out is a named predicate, not an inline list to drift."""
    guard = _transport_guard(_find_def(server_tree, "dispatch_tool_call"))
    called = {n.func.id for n in ast.walk(guard.test)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_mcp_is_stateful_t4_tool" in called


def test_no_proxy_exempt_tool_writes_project_files():
    """The invariant that makes the exemption safe, checked against the registry.

    This is the one assertion that would catch a REAL regression rather than a
    rename: if a file-writing tool is ever tagged T4, it would execute in the
    MCP host and the daemon's write ledger would never see the write -- the
    A33 second-writer failure, back again.
    """
    ct = pytest.importorskip("code_tools")
    phases = getattr(ct, "TOOL_PHASES", {}) or {}
    assert phases, "TOOL_PHASES empty -- registry did not build, test is vacuous"

    def _is_t4(name: str) -> bool:
        phase = phases.get(name)
        if isinstance(phase, (list, tuple, set, frozenset)):
            return any(str(i).strip().upper() == "T4" for i in phase)
        return any(tok.strip().upper() == "T4" for tok in
                   str(phase or "").replace(",", "|").replace("/", "|").split("|"))

    exempt = {n for n in getattr(ct, "TOOL_MAP", {}) if _is_t4(n)}
    exempt |= {"tool_names", "tool_schema"}
    assert len(exempt) > 20, f"only {len(exempt)} exempt tools -- phases not loaded"

    offenders = {n: ct.tool_mutation_scope(n) for n in sorted(exempt)
                 if ct.tool_mutation_scope(n) not in _EXEMPT_SCOPES_ALLOWED}
    assert not offenders, (
        "proxy-exempt tools with a non-runtime mutation scope -- these would "
        f"write from the MCP host process, unseen by the daemon ledger: {offenders}")


def test_council_identity_is_stamped_before_the_split(server_tree):
    """Order matters: the sender must be stamped so it rides a PROXIED call too.

    COUNCIL_AGENT_ID lives in the MCP host's env, not the daemon's, and
    _council_identify_self() traces THIS process's ancestry. Stamp after the
    split and a proxied council_message would be attributed to whatever the
    daemon's ancestry looks like.
    """
    dispatch = _find_def(server_tree, "dispatch_tool_call")
    guard = _transport_guard(dispatch)
    stamp_lines = [
        node.lineno for node in ast.walk(dispatch)
        if isinstance(node, ast.If)
        for cmp_node in ast.walk(node.test)
        if (isinstance(cmp_node, ast.Compare)
            and isinstance(cmp_node.left, ast.Name)
            and cmp_node.left.id == "tool_name"
            and any(isinstance(c, ast.Constant) and c.value == "council_message"
                    for c in cmp_node.comparators))
    ]
    assert stamp_lines, "the council_message sender stamp disappeared"
    assert min(stamp_lines) < guard.lineno, (
        "the council sender stamp moved AFTER the local-vs-proxy split; a "
        "proxied council_message would now be stamped in the daemon")


def test_the_named_wrappers_reach_the_daemon_through_the_dispatcher(bridge_src):
    """_mcp_call_text is daemon transport, not in-process execution.

    Pinned by AST rather than by name because _mcp_call_text is a CLOSURE over
    ``srv`` inside run_mcp_stdio (A33-C): there is no importable symbol to
    assert on.
    """
    call_text = _find_def(ast.parse(bridge_src), "_mcp_call_text")
    dispatched = [
        n for n in ast.walk(call_text)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "dispatch_tool_call"
    ]
    assert dispatched, "_mcp_call_text stopped using dispatch_tool_call"
    transports = {kw.value.value for call in dispatched for kw in call.keywords
                  if kw.arg == "transport" and isinstance(kw.value, ast.Constant)}
    assert transports == {"mcp"}, (
        f"transport kwarg is now {transports}; the proxy branch in "
        "dispatch_tool_call only fires for 'mcp'")


def test_the_council_message_wrapper_keeps_the_dispatcher_transport(bridge_src):
    """Refuse the tempting 'migrate every wrapper to _mcp_proxy_call' rewrite.

    _mcp_proxy_call POSTs straight to /api/call/<tool>, so the stamping at the
    top of dispatch_tool_call would then run in the DAEMON -- where
    COUNCIL_AGENT_ID is absent and the ancestry is wrong. council_message must
    keep going through the dispatcher from the host.
    """
    wrapper = _find_def(ast.parse(bridge_src), "council_message")
    body = ast.get_source_segment(bridge_src, wrapper) or ""
    assert "_mcp_call_text" in body, (
        "council_message no longer routes via _mcp_call_text -- the sender "
        "identity would be resolved in the daemon process")
    assert "_mcp_proxy_call" not in body, (
        "council_message was migrated to the raw proxy; that drops the "
        "host-side sender stamp and the duplicate-retry guard")
