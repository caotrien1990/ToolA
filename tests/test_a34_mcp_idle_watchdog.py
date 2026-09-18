"""A34 phase 2: the MCP proxy waits on an IDLE deadline, not a wall clock.

WHY this file exists
--------------------
Phase 1 gave the proxy a bounded, schema-derived timeout. A ceiling is necessary
(an unbounded wait turns a wedged daemon into a wedged CA) but it answers the
wrong question. "This repo is bigger" must never be answered by editing a number
upward: the number that matters is not how long the work has taken, it is how long
the work has been *silent*.

The daemon already publishes that. /api/write_status returns operation_state in
{running, stalled} plus seconds_since_progress for every member of
_MUTATING_TOOLS -- not writes only -- with its own stall window from
ORCHESTRATOR_OPERATION_STALL_SECONDS. So phase 2 added NO endpoint. It made the
client wait in slices and ask.

Each test below pins one property that, if it rotted, would silently give back the
wall-clock behaviour while still looking correct:

  * a progressing operation is never abandoned, no matter how long it runs
  * a stalled one is abandoned EARLY, and reports the daemon's measurement
  * a ledger entry that already went terminal is never turned into a failure
  * no ledger evidence means keep waiting -- absence of proof is not proof
  * the POST is issued exactly once in every path (a replayed mutation is a
    double write, which is the bug this whole design exists to avoid)
  * the operation_id is never minted on this side of the wire
"""

import json
import os
import sys
import time
import urllib.request

import pytest

import orchestrator.server.mcp_stdio as ms

# --- probe payloads the daemon really returns ---------------------------------
_RUNNING = {
    "ok": True, "commit_state": "in_flight", "operation_state": "running",
    "operation_id": "abc123",
    "progress": {"phase": "extract", "completed_units": 7, "total_units": 900,
                 "seconds_since_progress": 3.0},
}
_STALLED = {
    "ok": True, "commit_state": "in_flight", "operation_state": "stalled",
    "operation_id": "abc123",
    "progress": {"phase": "extract", "completed_units": 7,
                 "seconds_since_progress": 999.0},
}
_COMMITTED = {
    "ok": True, "commit_state": "committed", "operation_state": "committed",
    "operation_id": "abc123",
}
_NO_ENTRY = {"ok": False, "error_name": "WRITE_STATUS_UNKNOWN"}

_TARGET = {"project_path": "D:\\repo", "file_path": "a.py"}


class _Resp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _fake_urlopen(status_payload, post_delay=0.0, post_body=None, post_exc=None):
    """A urlopen that tells the POST apart from the status GET and counts both."""
    calls = {"post": 0, "status": 0, "status_urls": []}

    def fake(target, timeout=None):
        if isinstance(target, urllib.request.Request):
            calls["post"] += 1
            if post_delay:
                time.sleep(post_delay)
            if post_exc is not None:
                raise post_exc
            return _Resp(post_body if post_body is not None else {"ok": True})
        calls["status"] += 1
        calls["status_urls"].append(str(target))
        return _Resp(status_payload)

    return fake, calls


@pytest.fixture()
def fast_slices(monkeypatch):
    """Shrink the slice so a real thread handshake still runs in milliseconds."""
    monkeypatch.setattr(ms, "_MCP_PROXY_POLL_SLICE", 0.02)
    monkeypatch.setattr(ms, "_MCP_PROXY_IDLE_ABANDON", 180.0)
    # The floor has to come down too, or max(floor, slice) silently restores the
    # 1s production slice and every fake response lands inside the first join.
    monkeypatch.setattr(ms, "_MCP_PROXY_POLL_SLICE_FLOOR", 0.01)
    return None


def _wait(monkeypatch, fake, payload=None, http_timeout=5.0):
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    req = urllib.request.Request("http://localhost:8083/api/call/cbm_index",
                                 data=b"{}", method="POST")
    return ms._mcp_proxy_wait(req, http_timeout, "cbm_index",
                              payload if payload is not None else _TARGET,
                              "http://localhost:8083")


# --- the core property: slow is not stalled ----------------------------------
def test_a_progressing_operation_is_never_abandoned(monkeypatch, fast_slices):
    """Many slices elapse; the daemon keeps saying 'running'; the wait holds.

    This is the mechanism in one test: timeout -> check still running -> reset.
    If this ever fails, the proxy has gone back to a wall clock and every large
    repo will need its timeout number raised by hand again.
    """
    fake, calls = _fake_urlopen(_RUNNING, post_delay=0.30)
    body = json.loads(_wait(monkeypatch, fake))

    assert body == {"ok": True}
    assert calls["post"] == 1
    # It really did subdivide the wait rather than block once.
    assert calls["status"] >= 2, "the wait never polled -- no watchdog ran"


def test_a_stalled_operation_abandons_early_with_the_measurement(monkeypatch,
                                                                 fast_slices):
    """Abandon is asserted on the daemon's number, and long before the ceiling."""
    fake, calls = _fake_urlopen(_STALLED, post_delay=1.0)
    started = time.monotonic()
    with pytest.raises(ms._MCPOperationStalled) as caught:
        _wait(monkeypatch, fake, http_timeout=30.0)
    elapsed = time.monotonic() - started

    assert caught.value.idle_seconds == 999.0
    assert caught.value.status["operation_id"] == "abc123"
    # Positive control: EARLY. A stall that only surfaces at the ceiling is the
    # wall-clock behaviour wearing the new name.
    assert elapsed < 30.0 and caught.value.waited < 30.0
    assert calls["post"] == 1


def test_a_stall_needs_the_number_not_just_the_label(monkeypatch, fast_slices):
    """operation_state='stalled' alone must not abandon anything.

    Two independent observers have to agree: the daemon's own label AND the
    seconds it reports, measured against this side's idle budget. The server
    labels at 120s by default while this side abandons at 180s, so the window
    between them must keep waiting.
    """
    labelled_but_fresh = {**_STALLED,
                          "progress": {"seconds_since_progress": 130.0}}
    fake, calls = _fake_urlopen(labelled_but_fresh, post_delay=0.15)
    body = json.loads(_wait(monkeypatch, fake))

    assert body == {"ok": True}, "abandoned on the label alone"
    assert calls["post"] == 1


def test_a_terminal_ledger_entry_is_not_turned_into_a_failure(monkeypatch,
                                                              fast_slices):
    """If the ledger says committed, the response is in flight -- do not invent a
    failure for work that demonstrably finished."""
    fake, calls = _fake_urlopen(_COMMITTED, post_delay=0.15)
    body = json.loads(_wait(monkeypatch, fake))

    assert body == {"ok": True}
    assert calls["post"] == 1


def test_no_ledger_evidence_falls_back_to_the_ceiling(monkeypatch, fast_slices):
    """A 404 means 'read-only tool / no entry', NOT 'stopped'.

    Absence of evidence must preserve the phase-1 behaviour exactly: wait out the
    ceiling. Treating an empty probe as a stall would abandon every read-only
    proxied call after one slice.
    """
    fake, calls = _fake_urlopen(_NO_ENTRY, post_delay=0.15)
    body = json.loads(_wait(monkeypatch, fake))

    assert body == {"ok": True}
    assert calls["post"] == 1


def test_a_probe_failure_never_decides_the_call(monkeypatch, fast_slices):
    """The probe is an observability aid; if IT breaks, the call is unaffected."""
    def exploding(target, timeout=None):
        if isinstance(target, urllib.request.Request):
            time.sleep(0.15)
            return _Resp({"ok": True})
        raise OSError("status endpoint down")

    assert json.loads(_wait(monkeypatch, exploding)) == {"ok": True}


def test_the_post_is_never_reissued(monkeypatch, fast_slices):
    """Across every branch the POST goes out exactly once.

    A urllib socket timeout abandons its response irrecoverably, so a short
    timeout plus a retry would REPLAY a mutation. That is the double-write this
    design exists to prevent, so it gets its own pin.
    """
    for status, delay, expect_raise in ((_RUNNING, 0.15, False),
                                        (_STALLED, 0.40, True),
                                        (_COMMITTED, 0.15, False),
                                        (_NO_ENTRY, 0.15, False)):
        fake, calls = _fake_urlopen(status, post_delay=delay)
        if expect_raise:
            with pytest.raises(ms._MCPOperationStalled):
                _wait(monkeypatch, fake, http_timeout=20.0)
        else:
            _wait(monkeypatch, fake)
        assert calls["post"] == 1, f"POST re-issued for {status!r}"


# --- the query key -----------------------------------------------------------
def test_the_operation_id_is_never_minted_client_side():
    """The id is sha256 over the body the DISPATCHER sees, after it rewrites
    task_epoch / prompt_hash / fields / project_path / agent_id. An id computed
    here would be a plausible key for an entry that does not exist, and its 404
    would read as 'no write happened' -- worse than no id at all.
    """
    keys = [k for k, _ in ms._mcp_write_status_query("cbm_index", _TARGET)]
    assert "operation_id" not in keys
    assert "project_path" in keys
    # &tool= is the documented disambiguator: index and script tools all record
    # file_path="", so one root can hold several unrelated operations at once.
    assert "tool" in keys


def test_alternate_target_field_names_are_honoured():
    got = dict(ms._mcp_write_status_query("copy_file",
                                          {"project_path": "D:\\r", "dst": "b.py"}))
    assert got["file_path"] == "b.py"


def test_a_payload_without_a_project_never_probes(monkeypatch):
    """No project_path means no target key, so no bogus URL is built at all."""
    def forbidden(*_a, **_k):
        raise AssertionError("probed with no project_path")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert ms._mcp_operation_probe("http://localhost:8083", "cbm_index", {}) == {}


# --- constants: the positive control ----------------------------------------
def test_the_poll_slice_actually_subdivides_the_ceiling():
    """If the slice ever grows past the ceiling the watchdog degrades to a single
    blocking wait -- silently, still passing every behavioural test above except
    this one."""
    assert 0 < ms._MCP_PROXY_POLL_SLICE < ms._MCP_PROXY_DEFAULT_TIMEOUT
    assert ms._MCP_PROXY_POLL_SLICE < ms._MCP_PROXY_LONG_TIMEOUT


def test_the_idle_budget_is_looser_than_the_daemons_own_stall_window():
    """Mirror of the server expression in handle_write_status. This side must be
    the more patient of the two observers, or 'stalled' would be decided by one
    label with no confirmation margin.
    """
    server_window = max(30.0, float(
        os.environ.get("ORCHESTRATOR_OPERATION_STALL_SECONDS", "120") or 120))
    assert ms._MCP_PROXY_IDLE_ABANDON > server_window


def test_the_status_probe_is_bounded():
    assert 0 < ms._MCP_PROXY_STATUS_TIMEOUT <= 30.0


def test_the_slice_floor_prevents_a_poll_storm():
    """The floor is what keeps a misconfigured slice from hammering the daemon.

    It is also the reason this file patches it: at the production 1s floor a
    sub-second fake response finishes inside the first join, so no probe runs and
    a watchdog test would pass while measuring nothing at all. That failure mode
    cost four red tests to find, so it is pinned rather than remembered.
    """
    assert 0 < ms._MCP_PROXY_POLL_SLICE_FLOOR <= ms._MCP_PROXY_POLL_SLICE


# --- part B: a lost response is not an unknown outcome -----------------------
class _StubSrv:
    PORT = 8083

    @staticmethod
    def _mcp_proxy_failure_payload(tool_name, body, exc):
        return {"ok": False, "error_name": "MCP_PROXY_FAILED",
                "commit_state": "unknown", "safe_to_retry": False,
                "_mcp_proxy": {"tool": tool_name}}


@pytest.fixture()
def stub_main(monkeypatch):
    monkeypatch.setitem(sys.modules, "__main__", _StubSrv())
    return None


def test_a_lost_response_reports_the_measured_commit_state(monkeypatch,
                                                           fast_slices, stub_main):
    """The ceiling loses the RESPONSE; it does not make the OUTCOME unknown.

    Without this, a CA that times out on a write is told commit_state=unknown and
    has to guess whether to retry -- and a wrong guess writes the file twice.
    """
    fake, _calls = _fake_urlopen(_COMMITTED, post_exc=TimeoutError("timed out"))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = json.loads(ms._mcp_proxy_call("write_fix", _TARGET, timeout_override=1.0))

    assert out["commit_state"] == "committed"
    assert out["commit_state_source"] == "write_status_ledger"
    assert out["safe_to_retry"] is False
    assert out["ca_next_action"] == "do_not_retry_write_already_committed"
    assert out["operation_id"] == "abc123"


def test_a_lost_response_with_nothing_committed_is_safe_to_retry(monkeypatch,
                                                                 fast_slices,
                                                                 stub_main):
    fake, _calls = _fake_urlopen({"ok": True, "commit_state": "not_committed",
                                  "operation_id": "zz"},
                                 post_exc=TimeoutError("timed out"))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = json.loads(ms._mcp_proxy_call("write_fix", _TARGET, timeout_override=1.0))

    assert out["commit_state"] == "not_committed"
    assert out["safe_to_retry"] is True
    assert out["ca_next_action"] == "retry_write"


def test_an_unknown_that_stays_unknown_is_left_honest(monkeypatch, fast_slices,
                                                      stub_main):
    """No ledger entry -> keep 'unknown'. Never invent a verdict to look tidy."""
    fake, _calls = _fake_urlopen(_NO_ENTRY, post_exc=TimeoutError("timed out"))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = json.loads(ms._mcp_proxy_call("write_fix", _TARGET, timeout_override=1.0))

    assert out["commit_state"] == "unknown"
    assert "commit_state_source" not in out
    assert out["safe_to_retry"] is False


def test_an_unreachable_daemon_is_not_re_diagnosed_by_a_probe(monkeypatch,
                                                             fast_slices,
                                                             stub_main):
    """Connection refused is already a confident verdict; probing a dead daemon
    would only spend the probe timeout to learn nothing."""
    fake, calls = _fake_urlopen(_COMMITTED,
                                post_exc=ConnectionRefusedError("refused"))
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = json.loads(ms._mcp_proxy_call("write_fix", _TARGET, timeout_override=1.0))

    assert out["error_name"] == "MCP_PROXY_UNREACHABLE"
    assert calls["status"] == 0


def test_the_stall_envelope_tells_a_ca_exactly_what_to_do(monkeypatch,
                                                          fast_slices, stub_main):
    """The stall answer must never read as 'retry' -- the operation is still
    registered in_flight, and replaying it is the double write."""
    fake, calls = _fake_urlopen(_STALLED, post_delay=1.0)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = json.loads(ms._mcp_proxy_call("cbm_index", _TARGET, timeout_override=60.0))

    assert out["error_name"] == "MCP_PROXY_OPERATION_STALLED"
    assert out["seconds_since_progress"] == 999.0
    assert out["operation_state"] == "stalled"
    assert out["safe_to_retry"] is False
    assert out["retryable"] is False
    assert out["ca_next_action"] == "inspect_write_status_before_retry"
    assert "write_status" in out["write_status_url"]
    assert out["waited_s"] < out["http_budget_s"]
    assert calls["post"] == 1
