"""Guard for the ONE place the MCP proxy decides how long it may wait (A34, 2026-09-18).

Three separate defects used to live in that decision, and each has its own test here:

  1. ``_MCP_PROGRESS_TOOLS`` was a hand-written set of four names. A tool that grew
     slow later, or a fifth long tool, was silently capped at the 180s floor -- the
     A31 hand-mirror class, where a table in one module must be remembered every time
     another module changes. Now a tool DECLARES its own budget in its schema and the
     proxy reads it, so the only thing left to pin is that the four remaining names
     are still real tools.
  2. for those four names the proxy passed ``timeout=None`` to urlopen -- an UNBOUNDED
     wait. A wedged daemon then wedged the calling CA, with no way out but killing the
     client. That is the opposite defect from a too-small cap and the worse one,
     because a cap at least fails loud (7-RULE #7).
  3. exactly ONE of the proxied wrappers forwarded its own ``timeout`` argument, so
     ``backup_project`` -- whose schema declares 600 and whose description says "the
     600-second timeout matches the handler default" -- was cut off at 180 and could
     not be told otherwise from the MCP side at all.

Every expectation below is derived from the LIVE registry or from the module's own
AST, never from a number typed into this file: a budget hard-coded in a test is the
same hand mirror one layer down.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import code_tools as ct
import orchestrator_server as srv  # importing it is what lands the late registrations
from orchestrator.server.mcp_stdio import (
    _MCP_PROGRESS_TOOLS,
    _MCP_PROXY_DEFAULT_TIMEOUT,
    _MCP_PROXY_LONG_TIMEOUT,
    _MCP_PROXY_TIMEOUT_MARGIN,
    _MCP_TIMEOUT_DECL_FIELDS,
    _mcp_schema_timeout,
    _mcp_timeout_budget,
)

MCP_STDIO = Path(__file__).resolve().parents[1] / "orchestrator" / "server" / "mcp_stdio.py"
# The parameter names a WRAPPER uses for its own work budget. ``timeout_override`` is
# deliberately absent: that is the proxy's own plumbing parameter, and including it
# would drag _mcp_proxy_call itself into the AST sweep below.
WRAPPER_TIMEOUT_PARAMS = {"timeout", "timeout_sec"}


def _live_names() -> set:
    """Every tool name the live registry declares. Read through the module attribute:
    TOOL_SCHEMAS is REBOUND as later schema batches are appended."""
    return {
        ((envelope or {}).get("function") or {}).get("name")
        for envelope in (getattr(ct, "TOOL_SCHEMAS", None) or [])
    } - {None}


def _declared_seconds(name: str) -> float:
    """What the registry says this tool's own budget is, in seconds; 0.0 if it says
    nothing. Deliberately a second, independent reading of the same source, so a test
    expectation is never a copy of a number."""
    for envelope in (getattr(ct, "TOOL_SCHEMAS", None) or []):
        fn = (envelope or {}).get("function") or {}
        if fn.get("name") != name:
            continue
        props = ((fn.get("parameters") or {}).get("properties") or {})
        for field in _MCP_TIMEOUT_DECL_FIELDS:
            spec = props.get(field)
            if not isinstance(spec, dict):
                continue
            default = spec.get("default")
            if isinstance(default, bool) or not isinstance(default, (int, float)):
                continue
            if default <= 0:
                continue
            return float(default) / 1000.0 if field.endswith("_ms") else float(default)
        return 0.0
    return 0.0


def _wrappers_declaring_a_timeout() -> dict:
    """{function name: forwards_it} for every def that declares a work timeout AND
    calls _mcp_proxy_call. ast.walk reaches nested defs, which matters: every wrapper
    in this module is a closure registered inside another function."""
    tree = ast.parse(MCP_STDIO.read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
        if not (params & WRAPPER_TIMEOUT_PARAMS):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            if not (isinstance(fn, ast.Name) and fn.id == "_mcp_proxy_call"):
                continue
            forwarded = {kw.arg for kw in sub.keywords}
            found[node.name] = found.get(node.name, False) or (
                "timeout_override" in forwarded)
    return found


# ── 1. the hand-written remainder must still be real ─────────────────────────

def test_every_long_class_name_is_a_live_tool():
    """Defect 1's residue. Four names still live in this module by hand; a rename in
    the registry would drop them back to the floor silently, which is precisely how a
    hand mirror rots."""
    live = _live_names()
    assert live, "registry produced no tool names -- positive control failed"
    missing = sorted(n for n in _MCP_PROGRESS_TOOLS if n not in live)
    assert missing == [], (
        "_MCP_PROGRESS_TOOLS names no longer in the registry: %s" % missing)


def test_the_long_class_is_not_empty_and_not_everything():
    """Restraint control: if the set were empty the test above passes vacuously, and if
    it held every tool the ceiling would be the default for everything."""
    assert _MCP_PROGRESS_TOOLS
    assert len(_MCP_PROGRESS_TOOLS) < len(_live_names())


# ── 2. no budget may be unbounded ────────────────────────────────────────────

def test_no_tool_resolves_to_an_unbounded_wait():
    """Defect 2, swept across the whole live surface rather than sampled: urlopen's
    ``timeout=None`` means wait forever."""
    for name in sorted(_live_names()):
        budget = _mcp_timeout_budget(name, None, srv)
        assert budget is not None, "%s resolved to an unbounded wait" % name
        assert isinstance(budget, float) and budget > 0, "%s -> %r" % (name, budget)


def test_the_long_class_gets_the_ceiling_not_the_floor():
    for name in sorted(_MCP_PROGRESS_TOOLS):
        budget = _mcp_timeout_budget(name, None, srv)
        assert budget >= _MCP_PROXY_LONG_TIMEOUT, "%s -> %s" % (name, budget)
    assert _MCP_PROXY_LONG_TIMEOUT > _MCP_PROXY_DEFAULT_TIMEOUT, (
        "a ceiling below the floor would make the long class a downgrade")


# ── 3. the schema is the source of the budget ────────────────────────────────

def test_a_schema_declared_timeout_becomes_the_budget():
    """backup_project is the measured case: schema says 600, the wrapper used to send
    nothing, so the proxy waited 180."""
    declared = _declared_seconds("backup_project")
    assert declared > _MCP_PROXY_DEFAULT_TIMEOUT, (
        "backup_project no longer declares a timeout above the floor (%s) -- this test"
        " would pass vacuously" % declared)
    assert _mcp_schema_timeout("backup_project", srv) == declared
    assert _mcp_timeout_budget("backup_project", None, srv) == pytest.approx(
        declared + _MCP_PROXY_TIMEOUT_MARGIN)


def test_a_millisecond_declaration_is_read_as_milliseconds():
    """T4 tools declare ``timeout_ms``. Treating 30000 as seconds would be an 8-hour
    wait -- the same unit bug the old ``timeout=6100`` proxy branch had."""
    candidate = None
    for envelope in (getattr(ct, "TOOL_SCHEMAS", None) or []):
        fn = (envelope or {}).get("function") or {}
        props = ((fn.get("parameters") or {}).get("properties") or {})
        spec = props.get("timeout_ms")
        if not isinstance(spec, dict) or "timeout" in props:
            continue
        default = spec.get("default")
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            continue
        if default > 0:
            candidate = (fn.get("name"), float(default))
            break
    if candidate is None:
        pytest.skip("no live tool declares a positive timeout_ms default")
    name, ms = candidate
    assert _mcp_schema_timeout(name, srv) == pytest.approx(ms / 1000.0)


def test_a_tool_that_declares_nothing_gets_the_floor():
    assert _declared_seconds("read_file_range") == 0.0
    assert _mcp_timeout_budget("read_file_range", None, srv) == pytest.approx(
        _MCP_PROXY_DEFAULT_TIMEOUT)


def test_the_callers_own_timeout_beats_the_schema():
    big = _MCP_PROXY_DEFAULT_TIMEOUT * 5
    assert _mcp_timeout_budget("run_project_command", big, srv) == pytest.approx(
        big + _MCP_PROXY_TIMEOUT_MARGIN)


def test_a_junk_caller_timeout_falls_back_instead_of_raising():
    """A transport must not die on a bad argument; it degrades to the floor."""
    for junk in ("", "abc", None, 0, -5, False):
        budget = _mcp_timeout_budget("read_file_range", junk, srv)
        assert budget == pytest.approx(_MCP_PROXY_DEFAULT_TIMEOUT), "%r -> %s" % (
            junk, budget)


def test_a_missing_registry_degrades_to_the_floor():
    """Restraint control: a half-loaded srv must not break the transport."""
    assert _mcp_schema_timeout("backup_project", None) == 0.0
    assert _mcp_timeout_budget("backup_project", None, None) == pytest.approx(
        _MCP_PROXY_DEFAULT_TIMEOUT)


# ── 4. the per-tool escape hatch ─────────────────────────────────────────────

def test_a_per_tool_env_override_wins_over_everything(monkeypatch):
    """The knob that makes a slow repo a config change instead of a code change."""
    monkeypatch.setenv("MCP_PROXY_TIMEOUT__CBM_INDEX", "42")
    assert _mcp_timeout_budget("cbm_index", None, srv) == pytest.approx(42.0)
    monkeypatch.setenv("MCP_PROXY_TIMEOUT__BACKUP_PROJECT", "7777")
    assert _mcp_timeout_budget("backup_project", 10, srv) == pytest.approx(7777.0)


def test_an_unparseable_env_override_is_ignored_not_fatal(monkeypatch):
    monkeypatch.setenv("MCP_PROXY_TIMEOUT__READ_FILE_RANGE", "soon")
    assert _mcp_timeout_budget("read_file_range", None, srv) == pytest.approx(
        _MCP_PROXY_DEFAULT_TIMEOUT)


# ── 5. the wiring itself ─────────────────────────────────────────────────────

def test_every_proxied_wrapper_that_declares_a_timeout_forwards_it():
    """Defect 3. This is the anti-rot half: a wrapper added tomorrow that takes a
    ``timeout`` and then drops it fails HERE instead of on a live call against a big
    repo, which is where it was found the first time."""
    found = _wrappers_declaring_a_timeout()
    assert found, "AST sweep found no proxied wrapper declaring a timeout"
    dropped = sorted(n for n, forwards in found.items() if not forwards)
    assert dropped == [], (
        "proxied wrappers declare a timeout but never forward it: %s" % dropped)


@pytest.mark.parametrize("wrapper", [
    "run_project_command", "run_test_profile", "backup_project"])
def test_the_three_measured_wrappers_are_in_the_sweep(wrapper):
    """Positive control for the AST test: if a rename made the sweep miss these, the
    test above would pass on an empty finding."""
    assert _wrappers_declaring_a_timeout().get(wrapper) is True
