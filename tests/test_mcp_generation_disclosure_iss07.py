"""ISS-07: after a daemon restart the two generation stamps diverge silently.

`generation` follows the live daemon; `mcp_generation` stays whatever THIS MCP
host process minted at its own import and cannot be re-minted without a client
reconnect. That is correct by design -- every tool call proxies to the live
daemon over HTTP -- but the payload never said so, so a reader comparing the two
fields could not tell "correct by design" from "the MCP bridge is running dead
code". Both readings were available and only one was true.

Two halves are pinned here:

1. `_mcp_generation_disclosure` answers the comparison directly
   (`mcp_generation_matches_daemon`), and on a mismatch says how long the
   divergence has been observed plus what still holds;
2. `_mcp_generation_gate` logs the re-baseline. That log line existed before but
   never reached stderr: `sys` was not imported in this module, so the branch
   raised NameError straight into a bare `except Exception: pass`. The self-heal
   worked and its only evidence did not, which is why a restart looked like
   nothing had happened.
"""
from __future__ import annotations

import io
import json

import pytest

from orchestrator.server import mcp_stdio


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    monkeypatch.setattr(mcp_stdio, "_MCP_SERVER_GENERATION_SEEN", None, raising=False)
    mcp_stdio._MCP_GEN_DIVERGED_SINCE.pop("since", None)
    monkeypatch.delenv("CLM_MCP_STRICT_GENERATION", raising=False)
    yield
    mcp_stdio._MCP_GEN_DIVERGED_SINCE.pop("since", None)


# --------------------------------------------------------------------------
# the disclosure
# --------------------------------------------------------------------------

def test_matching_stamps_are_stated_not_left_inferable():
    payload: dict = {}
    mcp_stdio._mcp_generation_disclosure(payload, "abc123", "abc123")
    assert payload["mcp_generation_matches_daemon"] is True
    # Nothing to explain when they agree.
    assert "mcp_generation_note" not in payload
    assert "mcp_generation_diverged_s" not in payload


def test_divergence_is_named_and_explained():
    payload: dict = {}
    mcp_stdio._mcp_generation_disclosure(payload, "new-daemon", "old-host")
    assert payload["mcp_generation_matches_daemon"] is False
    assert isinstance(payload["mcp_generation_diverged_s"], float)
    note = payload["mcp_generation_note"]
    # The two things a reader must not get wrong: results are live, and a plain
    # daemon restart is NOT a reason to reconnect the client.
    assert "NOT stale" in note
    assert "does not require it" in note
    assert "new-daemon" in note and "old-host" in note


def test_divergence_duration_accumulates_across_calls(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(mcp_stdio.time, "time", lambda: clock["t"])
    first: dict = {}
    mcp_stdio._mcp_generation_disclosure(first, "d2", "d1")
    clock["t"] = 1042.5
    second: dict = {}
    mcp_stdio._mcp_generation_disclosure(second, "d2", "d1")
    assert first["mcp_generation_diverged_s"] == 0.0
    # "how long has this been true", not "how long since the last call".
    assert second["mcp_generation_diverged_s"] == 42.5


def test_agreement_clears_the_divergence_clock():
    diverged: dict = {}
    mcp_stdio._mcp_generation_disclosure(diverged, "d2", "d1")
    assert "since" in mcp_stdio._MCP_GEN_DIVERGED_SINCE
    mcp_stdio._mcp_generation_disclosure({}, "same", "same")
    assert "since" not in mcp_stdio._MCP_GEN_DIVERGED_SINCE


def test_unknown_stamp_answers_unknown_instead_of_false():
    # A failed probe is not a mismatch. Reporting False would invent a
    # divergence that was never observed.
    for daemon_gen, local_gen in ((None, "x"), ("x", None), ("", "x"), ("x", "")):
        payload: dict = {}
        mcp_stdio._mcp_generation_disclosure(payload, daemon_gen, local_gen)
        assert payload["mcp_generation_matches_daemon"] is None
        assert "mcp_generation_note" not in payload


def test_a_caller_supplied_verdict_is_not_overwritten():
    payload = {"mcp_generation_matches_daemon": "preset"}
    mcp_stdio._mcp_generation_disclosure(payload, "d2", "d1")
    assert payload["mcp_generation_matches_daemon"] == "preset"


def test_daemon_stamped_response_carries_the_disclosure_end_to_end():
    text = json.dumps({"ok": True, "generation": "host-gen"})
    out = json.loads(mcp_stdio._mcp_add_generation(
        text, {"source": "daemon", "generation": "daemon-gen",
               "local_generation": "host-gen"}))
    assert out["generation"] == "daemon-gen"
    assert out["generation_source"] == "daemon"
    assert out["mcp_generation"] == "host-gen"
    assert out["mcp_generation_matches_daemon"] is False
    assert "mcp_generation_note" in out


# --------------------------------------------------------------------------
# the gate's log line
# --------------------------------------------------------------------------

def test_restart_is_logged_and_re_baselined(monkeypatch):
    buffer = io.StringIO()
    monkeypatch.setattr(mcp_stdio.sys, "stderr", buffer)
    monkeypatch.setattr(mcp_stdio, "_MCP_SERVER_GENERATION_SEEN", "gen-old", raising=False)
    assert mcp_stdio._mcp_generation_gate("gen-new") is None
    # Self-heal, so later calls pass cleanly...
    assert mcp_stdio._MCP_SERVER_GENERATION_SEEN == "gen-new"
    # ...and the transition is now actually visible. Before the fix this branch
    # raised NameError on `sys` and the buffer stayed empty.
    written = buffer.getvalue()
    assert "gen-old" in written and "gen-new" in written
    assert "re-baselined" in written


def test_first_stamp_is_adopted_silently(monkeypatch):
    buffer = io.StringIO()
    monkeypatch.setattr(mcp_stdio.sys, "stderr", buffer)
    assert mcp_stdio._mcp_generation_gate("gen-first") is None
    assert mcp_stdio._MCP_SERVER_GENERATION_SEEN == "gen-first"
    assert buffer.getvalue() == ""


def test_unreachable_daemon_never_becomes_the_baseline(monkeypatch):
    monkeypatch.setattr(mcp_stdio, "_MCP_SERVER_GENERATION_SEEN", "gen-old", raising=False)
    assert mcp_stdio._mcp_generation_gate(None) is None
    # Recording None would make the NEXT real stamp look like a restart, and
    # comparing against it would mask one.
    assert mcp_stdio._MCP_SERVER_GENERATION_SEEN == "gen-old"


def test_strict_mode_still_fails_shut(monkeypatch):
    monkeypatch.setenv("CLM_MCP_STRICT_GENERATION", "1")
    monkeypatch.setattr(mcp_stdio, "_MCP_SERVER_GENERATION_SEEN", "gen-old", raising=False)
    result = mcp_stdio._mcp_generation_gate("gen-new")
    assert result["ok"] is False
    assert result["error_name"] == "MCP_STALE_RELOAD_REQUIRED"
    assert result["expected_generation"] == "gen-old"
    # A refusal must not re-baseline: the client has to reload.
    assert mcp_stdio._MCP_SERVER_GENERATION_SEEN == "gen-old"
