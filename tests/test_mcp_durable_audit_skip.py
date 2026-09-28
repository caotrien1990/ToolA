"""F22: the MCP host skips durable audit BY DESIGN -- and nothing pinned that.

WHY this file exists
--------------------
Under ``--mcp`` the stdio bridge never opens the durable audit writer, because that
process must never own a session DB (two processes on one SQLite file is the
contention this project already paid for). Only stateful-T4 tools execute inside
the bridge, so they leave just a USAGE_LOG telemetry row; every other tool is
proxied to the daemon, which audits it.

Measured 2026-09-27 over 48h: 12 shutdown WARNINGs read that design as a fault
(F20), a CA reported the missing rows as lost writes, and not one test set
``--mcp`` and asserted the skip. So either real regression would have gone green:

  * the bridge starts opening the audit DB  -> cross-process contention;
  * the bridge stops proxying non-T4 tools  -> their durable rows vanish for real,
    and they vanish SILENTLY, because "no row from MCP" is the expected state.

``durable_audit_disabled_in_mcp`` is the one string the health state, the flush
report, the shutdown log and the three rule files share, so a grep finds all of
them; it is pinned on both the code side and the rule side here.
"""
import asyncio
import importlib
import json
import re
import sys
from pathlib import Path

import pytest

osrv = importlib.import_module("orchestrator_server")
mcp_stdio = importlib.import_module("orchestrator.server.mcp_stdio")

STATE = "durable_audit_disabled_in_mcp"
EXAMPLES = Path(osrv.__file__).resolve().parent / "examples"
RULE_FILES = ("CORE_RULE.txt", "SLIM_RULE.txt", "SLIM_RULE_INLINE.txt")


@pytest.fixture
def as_bridge(monkeypatch):
    """Be the MCP stdio bridge: ``--mcp`` is the one discriminator the code reads."""
    monkeypatch.setattr(sys, "argv", ["orchestrator_server.py", "--mcp"])


def _no_writer(*_args, **_kwargs):
    raise AssertionError("the MCP bridge must never reach the durable audit writer")


def test_the_bridge_never_builds_a_writer(as_bridge, monkeypatch):
    """start=True is what the stage and flush paths pass; even that must build nothing."""
    monkeypatch.setattr(osrv, "_DURABLE_AUDIT", None)
    monkeypatch.setattr(osrv, "DurableAuditWriter", _no_writer)
    assert osrv._get_durable_audit_writer(start=True) is None
    assert osrv._DURABLE_AUDIT is None


def _stage_probe():
    return osrv._stage_durable_session_event(
        "f22-probe", "browser_click", {"project_path": "D:/f22", "page_id": "p1"},
        {"ok": True}, 1, "ok", "", {"agent_id": "probe"}, admitted_session_id="s-f22")


def test_an_in_process_call_stages_no_durable_row(monkeypatch):
    """The probe is LIVE: over HTTP the very same call reaches the writer.

    Without that control a probe that dies early for another reason (no capturable
    session, a skip-listed tool) passes against any code -- which the first version
    of this test did: deleting the --mcp guard left it green.
    """
    reached = []

    class _Recorder:
        def submit(self, _staged_id, _kind, payload):
            reached.append(payload["session_id"])
            return "staged"

    monkeypatch.setattr(osrv, "_get_durable_audit_writer", lambda **_k: _Recorder())
    monkeypatch.setattr(sys, "argv", ["orchestrator_server.py"])
    assert _stage_probe() == "staged"
    assert reached == ["s-f22"]

    monkeypatch.setattr(sys, "argv", ["orchestrator_server.py", "--mcp"])
    monkeypatch.setattr(osrv, "_get_durable_audit_writer", _no_writer)
    assert _stage_probe() is None


def test_health_names_the_disabled_state_and_claims_no_staleness(as_bridge):
    """In the bridge no writer is the design, not a writer that has not started yet."""
    metrics, state = asyncio.run(osrv._durable_metrics_for_health())
    assert state == STATE
    assert metrics["metrics_stale"] is False
    assert metrics["metrics_age_ms"] is None
    assert metrics["worker_alive"] is False


class _IdleLane:
    def flush(self, _timeout):
        return {"ok": True}


def test_the_flush_report_names_the_same_state(as_bridge, monkeypatch):
    """Nothing to drain is a drained lane; the state says WHY there was nothing."""
    monkeypatch.setattr(osrv, "_POST_CALL_ACCOUNTING", _IdleLane())
    report = asyncio.run(osrv._flush_post_call_accounting(0.5))
    assert report["durable_drained"] is True
    assert report["durable_error"] == STATE


def test_a_non_t4_tool_is_proxied_to_the_daemon_and_not_staged_here(
        as_bridge, monkeypatch):
    """This is the half that keeps the gap SMALL: the daemon audits what it serves."""
    sent = []

    def _fake_wait(req, _timeout, name, payload, base):
        sent.append((req.full_url, name, base))
        return json.dumps({"ok": True, "result": "proxied"})

    monkeypatch.setenv("ORCHESTRATOR_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(osrv, "_mcp_is_stateful_t4_tool", lambda _name: False)
    monkeypatch.setattr(osrv, "_stage_durable_session_event", _no_writer)
    monkeypatch.setattr(mcp_stdio, "_mcp_timeout_budget", lambda *_a, **_k: 5.0)
    monkeypatch.setattr(mcp_stdio, "_mcp_proxy_wait", _fake_wait)

    resp = asyncio.run(osrv.dispatch_tool_call("read_file_range", {"file_path": "x.py"}))

    assert resp.status == 200
    assert json.loads(resp.body)["result"] == "proxied"
    assert sent == [("http://127.0.0.1:1/api/call/read_file_range",
                     "read_file_range", "http://127.0.0.1:1")]


def test_the_code_still_uses_the_state_name_the_rules_document():
    src = Path(osrv.__file__).read_text(encoding="utf-8")
    assert len(re.findall(re.escape(f'"{STATE}"'), src)) >= 2, (
        "the health state and the flush report are the two live producers")


@pytest.mark.parametrize("name", RULE_FILES)
def test_every_rule_file_documents_the_gap_exactly_once(name):
    """An undocumented by-design gap reads as a bug to the next CA -- that is F21."""
    text = (EXAMPLES / name).read_text(encoding="utf-8")
    assert len(re.findall(r"(?i)MCP audit gap", text)) == 1
    assert STATE in text
    assert "USAGE_LOG" in text
