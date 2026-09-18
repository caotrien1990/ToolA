"""A34: the MCP transport gate must ask the DAEMON what is T4, not its own import.

WHY this file exists
--------------------
B010 (the comment inside ``dispatch_tool_call``) settled that only the daemon's
registry decides what a tool is, because the MCP bridge is a separate, long-lived
process whose ``orchestrator.core.tools`` is whatever was on disk when IT started.
That was applied to the schema gate -- and missed one line above it: the routing
gate ``_mcp_is_stateful_t4_tool`` answered from that same local ``ct.TOOL_PHASES``.

Measured on the live bridge while writing this: ``mcp_generation_matches_daemon``
false, diverged ~5678s. So a T4 tool added or retired since that launch is routed
by a stale answer, silently, in BOTH directions:

  * daemon T4, bridge unaware  -> the tool is proxied over HTTP, and a stateful
    browser/page session is created in the daemon process while the NEXT call for
    the same session goes somewhere else. No error; the session is just gone.
  * bridge T4, daemon retired it -> it keeps executing in-process, outside the
    daemon's lanes and accounting.

The unknown-tool direction is the one that actually bites: the old function
returned False for anything absent from its map, which is exactly what a NEW T4
tool looks like to an old bridge.

Two traps are pinned here on purpose, because both would be silent:

  1. An EMPTY daemon answer must never be believed or cached. ``tier=T4`` returns
     45 names today; zero means the fetch failed, the filter changed, or the
     daemon is mid-restart -- and trusting it reclassifies every stateful T4 tool
     as proxy-able in one step.
  2. The fetch must NOT run inside the daemon. A blocking ``urlopen`` to our own
     ``/api/tools/names`` from the daemon's event loop is a self-deadlock, and it
     would answer nothing new -- in that process the local registry IS the
     daemon's. ``"--mcp" in sys.argv`` is the discriminator already used across
     this repo for exactly that distinction.
"""

import importlib
import inspect
import json
import logging
import sys
import types
import urllib.error
import urllib.request

import pytest

osrv = importlib.import_module("orchestrator_server")


class _Resp:
    """Minimal context-manager stand-in for urlopen's return."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _tier_payload(names):
    return {"ok": True, "count": len(names),
            "tools": [{"name": n, "tier": "T4"} for n in names]}


@pytest.fixture(autouse=True)
def clean_tier_cache():
    """Module-level cache + one-shot log set would leak across tests otherwise."""
    osrv._MCP_T4_TIER_CACHE.clear()
    osrv._MCP_T4_TIER_DISAGREED.clear()
    yield
    osrv._MCP_T4_TIER_CACHE.clear()
    osrv._MCP_T4_TIER_DISAGREED.clear()


@pytest.fixture
def as_bridge(monkeypatch):
    """Pretend to be the MCP bridge process, which is the only one that fetches."""
    monkeypatch.setattr(sys, "argv", ["orchestrator_server.py", "--mcp"])


@pytest.fixture
def fetches(monkeypatch):
    """Record every tier fetch and serve a scripted answer."""
    ledger = {"urls": [], "answers": [], "exc": None}

    def _fake(url, timeout=None):
        ledger["urls"].append((str(url), timeout))
        if ledger["exc"] is not None:
            raise ledger["exc"]
        answer = (ledger["answers"].pop(0)
                  if ledger["answers"] else _tier_payload(["browser_click"]))
        return _Resp(answer)

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    return ledger


# --------------------------------------------------------------------------
# _mcp_local_t4_phase: the OLD logic, which must survive untouched
# --------------------------------------------------------------------------

@pytest.mark.parametrize("phase,expected", [
    ("T4", True),
    ("t4", True),
    (" T4 ", True),
    ("T2|T4", True),
    ("T2,T4", True),
    ("T2/T4", True),
    (["T1", "T4"], True),
    (("T4",), True),
    ({"T4"}, True),
    (frozenset({"T4"}), True),
    ("T1", False),
    ("T40", False),
    ("", False),
    (None, False),
    (["T1", "T2"], False),
])
def test_local_phase_parsing_is_preserved(monkeypatch, phase, expected):
    """Extracting the helper must not change one verdict it used to give."""
    monkeypatch.setattr(osrv.ct, "TOOL_PHASES", {"some_tool": phase},
                        raising=False)
    assert osrv._mcp_local_t4_phase("some_tool") is expected


def test_local_phase_says_false_for_an_unknown_tool(monkeypatch):
    """This False is the whole bug: a NEW T4 tool looks exactly like this."""
    monkeypatch.setattr(osrv.ct, "TOOL_PHASES", {}, raising=False)
    assert osrv._mcp_local_t4_phase("a_tool_added_after_this_bridge_started") is False


def test_local_phase_survives_a_non_dict_registry(monkeypatch):
    monkeypatch.setattr(osrv.ct, "TOOL_PHASES", ["not", "a", "dict"], raising=False)
    assert osrv._mcp_local_t4_phase("browser_click") is False


# --------------------------------------------------------------------------
# _mcp_daemon_t4_names: who may ask, and what counts as an answer
# --------------------------------------------------------------------------

def test_the_daemon_never_asks_itself(fetches):
    """Under pytest there is no --mcp, which is also the daemon's own condition.

    A blocking GET to our own loop is a self-deadlock, so the guard is not a
    performance choice. Pinned by the fetch ledger, not by the return value:
    None could also mean 'asked and failed'.
    """
    assert "--mcp" not in sys.argv
    assert osrv._mcp_daemon_t4_names() is None
    assert fetches["urls"] == []


def test_the_bridge_does_ask(as_bridge, fetches):
    """Positive control for the guard above -- otherwise it proves nothing."""
    names = osrv._mcp_daemon_t4_names()
    assert names == {"browser_click"}
    assert len(fetches["urls"]) == 1
    url, timeout = fetches["urls"][0]
    assert "tier=T4" in url
    assert "/api/tools/names" in url
    assert timeout == osrv._MCP_T4_TIER_TIMEOUT


def test_the_fetch_projects_fields_and_is_bounded(as_bridge, fetches):
    """45 names is small, but an unbounded call inside a routing decision is not."""
    osrv._mcp_daemon_t4_names()
    url, timeout = fetches["urls"][0]
    assert "fields=" in url
    assert isinstance(timeout, float) and 0 < timeout <= 30


def test_an_empty_answer_is_not_an_answer(as_bridge, fetches):
    """Zero T4 tools would silently reclassify all 45 as proxy-able."""
    fetches["answers"] = [{"ok": True, "count": 0, "tools": []}]
    assert osrv._mcp_daemon_t4_names() is None
    assert "names" not in osrv._MCP_T4_TIER_CACHE


@pytest.mark.parametrize("exc", [
    urllib.error.URLError("refused"),
    urllib.error.HTTPError("u", 500, "boom", {}, None),
    OSError("socket"),
    TimeoutError("slow"),
])
def test_a_failed_fetch_decides_nothing_and_caches_nothing(as_bridge, fetches, exc):
    fetches["exc"] = exc
    assert osrv._mcp_daemon_t4_names() is None
    assert "names" not in osrv._MCP_T4_TIER_CACHE


def test_garbage_json_decides_nothing(as_bridge, monkeypatch):
    class _Junk(_Resp):
        def __init__(self):
            self._body = b"<html>not json</html>"

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda url, timeout=None: _Junk())
    assert osrv._mcp_daemon_t4_names() is None


def test_malformed_entries_are_skipped_not_fatal(as_bridge, fetches):
    fetches["answers"] = [{"ok": True, "tools": [
        {"name": "browser_click"}, "a bare string", {"no_name": 1}, {"name": ""},
    ]}]
    assert osrv._mcp_daemon_t4_names() == {"browser_click"}


def test_the_answer_is_cached_within_its_ttl(as_bridge, fetches):
    """One GET per TTL per process is the whole cost of this fix."""
    first = osrv._mcp_daemon_t4_names()
    second = osrv._mcp_daemon_t4_names()
    assert first == second == {"browser_click"}
    assert len(fetches["urls"]) == 1


def test_the_cache_expires(as_bridge, fetches, monkeypatch):
    fetches["answers"] = [_tier_payload(["browser_click"]),
                          _tier_payload(["browser_click", "browser_new_tab"])]
    assert osrv._mcp_daemon_t4_names() == {"browser_click"}
    monkeypatch.setitem(
        osrv._MCP_T4_TIER_CACHE, "at",
        float(osrv._MCP_T4_TIER_CACHE["at"]) - (osrv._MCP_T4_TIER_TTL + 1.0))
    assert osrv._mcp_daemon_t4_names() == {"browser_click", "browser_new_tab"}
    assert len(fetches["urls"]) == 2


def test_the_ttl_is_a_real_window(as_bridge):
    """A 0 TTL would put a GET on every routing decision; a huge one never resyncs."""
    assert 30.0 <= osrv._MCP_T4_TIER_TTL <= 3600.0


# --------------------------------------------------------------------------
# _mcp_is_stateful_t4_tool: the daemon wins when it can be asked
# --------------------------------------------------------------------------

def test_the_daemon_overrides_a_bridge_that_never_heard_of_the_tool(
        as_bridge, fetches, monkeypatch):
    """The live failure mode: new T4 tool, old bridge, page session misrouted."""
    monkeypatch.setattr(osrv.ct, "TOOL_PHASES", {}, raising=False)
    fetches["answers"] = [_tier_payload(["browser_brand_new_tool"])]
    assert osrv._mcp_local_t4_phase("browser_brand_new_tool") is False
    assert osrv._mcp_is_stateful_t4_tool("browser_brand_new_tool") is True


def test_the_daemon_also_overrides_a_retired_tool(as_bridge, fetches, monkeypatch):
    """Drift in the other direction is the same defect class, not a special case."""
    monkeypatch.setattr(osrv.ct, "TOOL_PHASES", {"browser_gone": "T4"},
                        raising=False)
    fetches["answers"] = [_tier_payload(["browser_click"])]
    assert osrv._mcp_local_t4_phase("browser_gone") is True
    assert osrv._mcp_is_stateful_t4_tool("browser_gone") is False


def test_an_unreachable_daemon_falls_back_to_local(as_bridge, fetches, monkeypatch):
    """Losing the daemon must not reroute every T4 tool -- that would be worse."""
    monkeypatch.setattr(osrv.ct, "TOOL_PHASES", {"browser_click": "T4"},
                        raising=False)
    fetches["exc"] = urllib.error.URLError("refused")
    assert osrv._mcp_is_stateful_t4_tool("browser_click") is True
    assert osrv._mcp_is_stateful_t4_tool("read_file_range") is False


def test_inside_the_daemon_the_local_map_is_the_answer(fetches, monkeypatch):
    """Same verdicts as before this change, for every caller without --mcp."""
    monkeypatch.setattr(osrv.ct, "TOOL_PHASES",
                        {"browser_click": "T4", "read_file_range": "T1"},
                        raising=False)
    assert osrv._mcp_is_stateful_t4_tool("browser_click") is True
    assert osrv._mcp_is_stateful_t4_tool("read_file_range") is False
    assert osrv._mcp_is_stateful_t4_tool("never_seen") is False
    assert fetches["urls"] == []


def test_agreement_is_silent(as_bridge, fetches, monkeypatch, caplog):
    monkeypatch.setattr(osrv.ct, "TOOL_PHASES", {"browser_click": "T4"},
                        raising=False)
    fetches["answers"] = [_tier_payload(["browser_click"])]
    with caplog.at_level(logging.WARNING):
        assert osrv._mcp_is_stateful_t4_tool("browser_click") is True
    assert "T4 routing" not in caplog.text


def test_disagreement_is_logged_exactly_once_per_tool(
        as_bridge, fetches, monkeypatch, caplog):
    """Fail loud, but a per-call warning on a hot path is its own outage."""
    monkeypatch.setattr(osrv.ct, "TOOL_PHASES", {}, raising=False)
    fetches["answers"] = [_tier_payload(["browser_a", "browser_b"])]
    with caplog.at_level(logging.WARNING):
        for _ in range(4):
            osrv._mcp_is_stateful_t4_tool("browser_a")
        osrv._mcp_is_stateful_t4_tool("browser_b")
    assert caplog.text.count("browser_a") == 1
    assert caplog.text.count("browser_b") == 1


def test_the_warning_says_what_to_do_about_it(as_bridge, fetches, monkeypatch,
                                              caplog):
    monkeypatch.setattr(osrv.ct, "TOOL_PHASES", {}, raising=False)
    fetches["answers"] = [_tier_payload(["browser_a"])]
    with caplog.at_level(logging.WARNING):
        osrv._mcp_is_stateful_t4_tool("browser_a")
    assert "reconnect" in caplog.text.lower()


# --------------------------------------------------------------------------
# source pins
# --------------------------------------------------------------------------

def test_the_gate_no_longer_reads_tool_phases_directly():
    """If the parsing creeps back inline, the daemon stops being authoritative."""
    src = inspect.getsource(osrv._mcp_is_stateful_t4_tool)
    assert "TOOL_PHASES" not in src
    assert "_mcp_daemon_t4_names" in src
    assert "_mcp_local_t4_phase" in src


def test_the_self_deadlock_guard_is_present_in_source():
    src = inspect.getsource(osrv._mcp_daemon_t4_names)
    assert '"--mcp" not in sys.argv' in src


def test_the_routing_gate_is_still_what_dispatch_calls():
    """A34 transport-split tests pin this name; keep the call site itself pinned."""
    src = inspect.getsource(osrv.dispatch_tool_call)
    assert "_mcp_is_stateful_t4_tool(tool_name)" in src


def test_the_helpers_are_callable_with_a_bare_namespace():
    """No hidden dependency on server state -- this is a pure routing decision."""
    assert isinstance(osrv._MCP_T4_TIER_CACHE, dict)
    assert isinstance(osrv._MCP_T4_TIER_DISAGREED, set)
    assert isinstance(types.SimpleNamespace(), object)
