"""ISS-07: after a daemon restart the two generation stamps diverge silently.

`generation` follows the live daemon; `mcp_generation` stays whatever THIS MCP
host process minted at its own import and cannot be re-minted without a client
reconnect. That is correct by design -- every tool call proxies to the live
daemon over HTTP -- but the payload never said so, so a reader comparing the two
fields could not tell "correct by design" from "the MCP bridge is running dead
code". Both readings were available and only one was true.

Three parts are pinned here:

1. `_mcp_generation_disclosure` answers the comparison directly
   (`mcp_generation_matches_daemon`), and on a mismatch says how long the
   divergence has been observed plus what still holds;
2. `_mcp_generation_gate` logs the re-baseline. That log line existed before but
   never reached stderr: `sys` was not imported in this module, so the branch
   raised NameError straight into a bare `except Exception: pass`. The self-heal
   worked and its only evidence did not, which is why a restart looked like
   nothing had happened.
3. 2026-09-24: the fix for (1) over-shot. The note is prose a reader needs once,
   yet it rode every answer for the whole life of a diverged client, and
   `fields=` did not strip it or its sibling stamps. The note now ships once per
   (daemon, host) pair, and under `fields=` the host adds none of its advisory
   stamps unless named. The fail-loud stamps (a failed probe, a tool-supplied
   `generation`) are never projected away: without them a `generation` the
   caller holds would read as the daemon's when it is not.
"""
from __future__ import annotations

import ast
import io
import json
from pathlib import Path

import pytest

from orchestrator.server import mcp_stdio


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    monkeypatch.setattr(mcp_stdio, "_MCP_SERVER_GENERATION_SEEN", None, raising=False)
    # clear(), not pop("since"): the dict also remembers which pair was already
    # noted, and that leaking between tests would hide the note from the next one.
    mcp_stdio._MCP_GEN_DIVERGED_SINCE.clear()
    monkeypatch.delenv("CLM_MCP_STRICT_GENERATION", raising=False)
    yield
    mcp_stdio._MCP_GEN_DIVERGED_SINCE.clear()


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


# --------------------------------------------------------------------------
# once per divergence, and `fields=` is respected (2026-09-24)
# --------------------------------------------------------------------------

_ADVISORY = ("generation_source", "mcp_generation", "mcp_generation_matches_daemon",
             "mcp_generation_diverged_s", "mcp_generation_note")


def _probe():
    return {"source": "daemon", "generation": "daemon-gen", "local_generation": "host-gen"}


def _stamp(body: dict, fields=None, probe=None) -> dict:
    return json.loads(mcp_stdio._mcp_add_generation(
        json.dumps(body), probe or _probe(), fields))


def test_the_note_ships_once_per_divergence_pair():
    first: dict = {}
    mcp_stdio._mcp_generation_disclosure(first, "d2", "d1")
    second: dict = {}
    mcp_stdio._mcp_generation_disclosure(second, "d2", "d1")
    assert "mcp_generation_note" in first
    # The same prose again on every answer is the cost this change removes.
    assert "mcp_generation_note" not in second
    # The verdict and the clock still ride along: short, and the clock moves.
    assert second["mcp_generation_matches_daemon"] is False
    assert "mcp_generation_diverged_s" in second


def test_a_further_daemon_restart_is_a_new_pair_and_is_noted_again():
    mcp_stdio._mcp_generation_disclosure({}, "d2", "d1")
    again: dict = {}
    mcp_stdio._mcp_generation_disclosure(again, "d3", "d1")
    assert "d3" in again["mcp_generation_note"]


def test_a_projected_answer_gets_no_advisory_host_stamps():
    out = _stamp({"ok": True, "generation": "host-gen"}, "ok,result")
    # The daemon's R101 filter already cut the body to what was asked for; the
    # host stamping afterwards must not grow it back.
    for key in _ADVISORY:
        assert key not in out, key
    # `generation` was already in the body (the dispatcher stamps it), so it is
    # corrected to the daemon's, not added.
    assert out["generation"] == "daemon-gen"


def test_a_projection_does_not_spend_the_one_copy_of_the_note():
    _stamp({"ok": True, "generation": "host-gen"}, "ok")
    out = _stamp({"ok": True, "generation": "host-gen"})
    assert "mcp_generation_note" in out


@pytest.mark.parametrize("fields", [
    "ok, mcp_generation_note ,mcp_generation",
    '["ok", "mcp_generation_note", "mcp_generation"]',
    ["ok", "mcp_generation_note", "mcp_generation"],
])
def test_a_named_host_stamp_survives_the_projection(fields):
    out = _stamp({"ok": True, "generation": "host-gen"}, fields)
    assert "mcp_generation_note" in out
    assert out["mcp_generation"] == "host-gen"
    assert "generation_source" not in out


def test_a_dotted_spec_names_its_head_and_no_projection_means_none():
    assert mcp_stdio._mcp_requested_keys(" result.files , ok") == {"result", "ok"}
    assert mcp_stdio._mcp_requested_keys('["ok", "result.x"]') == {"ok", "result"}
    for unprojected in (None, "", "  ", []):
        assert mcp_stdio._mcp_requested_keys(unprojected) is None, unprojected


def test_a_tool_supplied_generation_keeps_its_label_under_projection():
    out = _stamp({"ok": True, "generation": "tool-set"}, "ok")
    # Not the daemon's stamp: dropping the label would let the caller read it
    # as the daemon's.
    assert out["generation"] == "tool-set"
    assert out["generation_source"] == "tool_supplied"
    assert out["daemon_generation"] == "daemon-gen"


def test_a_failed_probe_keeps_its_stamps_under_projection():
    probe = {"source": "unreachable", "generation": None,
             "probe_error": "URLError: connection refused", "local_generation": "host-gen"}
    out = _stamp({"ok": True}, "ok", probe)
    assert out["generation_source"] == "unreachable"
    assert out["generation_probe_error"] == "URLError: connection refused"


def _nested_def(tree: ast.AST, name: str) -> ast.AST:
    # ast.walk: both wrappers are closures inside run_mcp_stdio.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found -- renamed or removed")


def _calls(node: ast.AST, callee: str) -> list:
    return [c for c in ast.walk(node)
            if isinstance(c, ast.Call) and getattr(c.func, "id", None) == callee]


def test_the_projection_reaches_the_stamping_step():
    """Pinned by AST because the two wrappers are closures (the a34 convention).

    Every function above can be right while the live answer still carries every
    stamp, if the call's `fields` never reaches _mcp_add_generation.
    """
    tree = ast.parse(Path(mcp_stdio.__file__).read_text(encoding="utf-8"))
    stamping = _calls(_nested_def(tree, "_mcp_guard_response"), "_mcp_add_generation")
    assert stamping, "_mcp_guard_response no longer stamps"
    for call in stamping:
        assert len(call.args) == 3 and getattr(call.args[2], "id", None) == "fields", (
            "_mcp_guard_response stamps without the call's projection")
    guarded = _calls(_nested_def(tree, "_mcp_call_text"), "_mcp_guard_response")
    assert guarded, "_mcp_call_text no longer goes through _mcp_guard_response"
    for call in guarded:
        assert len(call.args) + len(call.keywords) == 2, (
            "_mcp_call_text drops the payload's fields before stamping")
