"""A34 phase 2b: the watchdog now covers the proxy path EVERY call_tool takes.

WHY this file exists
--------------------
Phase 2 gave _mcp_proxy_call an idle watchdog. Measurement afterwards showed that
wrapper is reached by only a handful of named tools: `call_tool` -- and therefore
every scan tool a CA actually runs -- goes _mcp_call_text -> dispatch_tool_call
-> the proxy branch inside orchestrator_server.py. That branch had:

  * no watchdog and no polling at all, one bare urlopen; and
  * `timeout=None` for four index tools, i.e. an UNBOUNDED wall-clock wait.

Unbounded is the worse of the two defects. A cap at least fails loud (7-RULE #7);
`None` converts a wedged daemon into a wedged CA with no exit. mcp_stdio had
already deleted that exact footgun on its own side, and the four-name set in the
dispatcher was a HAND-MIRROR of _MCP_PROGRESS_TOOLS -- the same drift class that
let one path grow a watchdog while the other kept the hole.

So the fix shares, it does not copy: the dispatcher borrows _mcp_timeout_budget
and _mcp_proxy_wait, and both paths render errors from ONE envelope carried by the
exception itself. These tests pin that, plus the part that is genuinely new.

The new part is the scan class. write_status only tracks _MUTATING_TOOLS, so for
read_*, cbm_*, ocr_* and settings the ledger answers "no entry" forever and the
idle watchdog had nothing to measure. The one fact the daemon publishes about
itself for every tool is GET /health -> pid, so for a read the question becomes
"is the daemon I POSTed to still serving this call".

That question may only be answered on POSITIVE evidence, and the tests below
exist mostly to keep it that way:

  * a DIFFERENT pid abandons (restart -- this request died with the old process)
  * connection REFUSED K times running abandons (nothing is listening)
  * a probe that times out, errors, or returns garbage decides NOTHING -- a daemon
    too busy to answer /health is the NORMAL state of a large scan, and killing a
    live scan is worse than waiting out the finite budget
  * when the ledger already answered, no health probe is paid for at all
"""

import json
import pathlib
import time
import types
import urllib.error
import urllib.request

import pytest

import orchestrator.server.mcp_stdio as ms

_TARGET = {"project_path": "D:\\repo", "file_path": "a.py"}
_SERVER = pathlib.Path(__file__).resolve().parents[1] / "orchestrator_server.py"

_RUNNING = {
    "ok": True, "commit_state": "in_flight", "operation_state": "running",
    "operation_id": "abc123",
    "progress": {"phase": "extract", "seconds_since_progress": 3.0},
}
_STALLED = {
    "ok": True, "commit_state": "in_flight", "operation_state": "stalled",
    "operation_id": "abc123",
    "progress": {"phase": "extract", "seconds_since_progress": 999.0},
}


class _Resp:
    def __init__(self, payload):
        self._data = (payload if isinstance(payload, bytes)
                      else json.dumps(payload).encode("utf-8"))

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _fake_urlopen(ledger=None, health=None, health_seq=None, post_delay=0.0,
                  post_body=None, post_exc=None):
    """A urlopen that tells THREE callers apart: the POST, the ledger, /health.

    The existing phase-2 harness only had to split two. Counting the health probe
    separately is the whole point here: for a scan tool the ledger is silent, so
    "was /health asked at all" is the difference between a watchdog and a wall.
    """
    calls = {"post": 0, "ledger": 0, "health": 0, "health_urls": []}
    seq = list(health_seq or [])

    def fake(target, timeout=None):
        if isinstance(target, urllib.request.Request):
            calls["post"] += 1
            if post_delay:
                time.sleep(post_delay)
            if post_exc is not None:
                raise post_exc
            return _Resp(post_body if post_body is not None else {"ok": True})
        url = str(target)
        if "/api/write_status" in url:
            calls["ledger"] += 1
            if ledger is None:
                # What the daemon really answers for a read-only tool: no entry.
                raise urllib.error.HTTPError(url, 404, "not found", {}, None)
            return _Resp(ledger)
        calls["health"] += 1
        calls["health_urls"].append(url)
        item = seq.pop(0) if seq else health
        if isinstance(item, BaseException):
            raise item
        return _Resp(item if item is not None else {"ok": True, "pid": 4242})

    return fake, calls


@pytest.fixture()
def fast_slices(monkeypatch):
    monkeypatch.setattr(ms, "_MCP_PROXY_POLL_SLICE", 0.02)
    monkeypatch.setattr(ms, "_MCP_PROXY_IDLE_ABANDON", 180.0)
    monkeypatch.setattr(ms, "_MCP_PROXY_POLL_SLICE_FLOOR", 0.01)
    return None


def _wait(monkeypatch, fake, payload=None, http_timeout=5.0, name="read_file_range"):
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    req = urllib.request.Request(f"http://localhost:8083/api/call/{name}",
                                 data=b"{}", method="POST")
    return ms._mcp_proxy_wait(req, http_timeout, name,
                              payload if payload is not None else _TARGET,
                              "http://localhost:8083")


# --- the dispatcher no longer owns a second, weaker copy of the policy --------
def _proxy_block():
    """The dispatcher's proxy branch, located by CONTENT rather than line number."""
    src = _SERVER.read_text(encoding="utf-8")
    start = src.index('f"{_proxy_base}/api/call/{tool_name}"')
    end = src.index("_proxy_payload = json.loads(", start)
    return src[start:end]


def _proxy_code():
    """The same branch with whole-line comments removed.

    Measured need, not a precaution: the first draft of this file asserted
    `"timeout=None" not in block` and failed on the COMMENT that explains why
    timeout=None was deleted. A rot pin that cannot tell code from prose ABOUT the
    code leaves only bad exits -- delete the explanation, or loosen the pin -- so
    it strips instead.
    """
    return "\n".join(line for line in _proxy_block().splitlines()
                     if not line.lstrip().startswith("#"))


def test_the_block_this_file_asserts_on_is_actually_found():
    """Positive control. Most assertions below are `not in`, and `not in` on an
    empty string passes for the wrong reason -- a renamed marker, or a stripper
    that ate the body, would turn this file green while the unbounded urlopen sat
    there untouched."""
    block = _proxy_block()
    assert "asyncio.to_thread" in block
    assert 200 < len(block) < 4000
    code = _proxy_code()
    assert "asyncio.to_thread" in code
    assert "_mcp_proxy_wait" in code


def test_no_tool_gets_an_unbounded_wait_from_the_dispatcher():
    """`timeout=None` is not a long timeout, it is the absence of one."""
    code = _proxy_code()
    assert "timeout=None" not in code
    assert "None if tool_name in" not in code
    assert "_timeout = None" not in code


def test_the_hand_mirrored_progress_tool_set_is_gone():
    """Four tool names copied next to _MCP_PROGRESS_TOOLS is the drift itself.

    Whoever adds the fifth progress tool will add it in mcp_stdio and never think
    to look here, so the copy must not exist to be forgotten.
    """
    code = _proxy_code()
    for name in ("cbm_index", "cbm_incremental_index", "generate_project_map",
                 "refresh_project_map"):
        assert f'"{name}"' not in code, f"{name} is mirrored in the dispatcher again"
    assert "MCP_PROXY_HTTP_TIMEOUT" not in code


def test_the_dispatcher_borrows_the_budget_and_the_wait():
    block = _proxy_block()
    assert "_mcp_timeout_budget" in block
    assert "_mcp_proxy_wait" in block


def test_the_import_fallback_is_still_bounded():
    """If the bridge module cannot be imported the call must still have a ceiling:
    degrading to `None` would reintroduce exactly the defect being removed."""
    block = _proxy_block()
    assert "MCP_PROXY_HTTP_LONG_TIMEOUT" in block
    assert "float(os.environ.get(\"MCP_PROXY_HTTP_LONG_TIMEOUT\"" in block


def test_the_dispatcher_renders_a_watchdog_verdict_before_the_generic_502():
    """A watchdog verdict must not be flattened into MCP_PROXY_FAILED.

    The envelope is checked duck-typed (getattr) on purpose: an `except
    _mcpw._MCPOperationStalled` clause would evaluate _mcpw at exception time and
    NameError in precisely the case it is there to report.
    """
    src = _SERVER.read_text(encoding="utf-8")
    anchor = src.index('_abandon = getattr(exc, "mcp_proxy_envelope", None)')
    tail = src[anchor:anchor + 1400]
    assert "web.json_response" in tail
    assert "_mcp_proxy_failure_payload(tool_name, body, exc)" in tail, (
        "the generic 502 must remain the FALLBACK, after the envelope check")
    assert "_mcpw._MCPOperationStalled" not in src


# --- /health as a liveness oracle: positive evidence only ---------------------
def test_the_health_probe_reads_a_live_pid(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *_a, **_k: _Resp({"ok": True, "pid": 9001}))
    payload, verdict = ms._mcp_health_probe("http://localhost:8083")
    assert verdict == "alive"
    assert payload["pid"] == 9001


def test_the_health_probe_asks_for_four_keys_not_a_whole_health_dump(monkeypatch):
    """This runs once per wait slice on every long call. /health returns ~69 keys;
    the watchdog needs one. Token discipline is not optional on a hot path."""
    seen = {}

    def fake(target, timeout=None):
        seen["url"] = str(target)
        return _Resp({"ok": True, "pid": 1})

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    ms._mcp_health_probe("http://localhost:8083")
    assert "fields=" in seen["url"]
    assert "pid" in seen["url"]


@pytest.mark.parametrize("exc,expected", [
    (urllib.error.HTTPError("u", 500, "boom", {}, None), "alive"),
    (urllib.error.HTTPError("u", 404, "nope", {}, None), "alive"),
    (ConnectionRefusedError(10061, "refused"), "refused"),
    (urllib.error.URLError(ConnectionRefusedError(10061, "refused")), "refused"),
    (urllib.error.URLError("timed out"), "unclear"),
    (TimeoutError("timed out"), "unclear"),
    (OSError("winerror 64"), "unclear"),
])
def test_the_health_probe_classifies_every_failure(monkeypatch, exc, expected):
    """A 4xx/5xx is ALIVE: a process that answers is a process that is running,
    and liveness, not correctness, is the question. Everything ambiguous is
    "unclear", which decides nothing."""
    def fake(*_a, **_k):
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert ms._mcp_health_probe("http://localhost:8083")[1] == expected


def test_garbage_from_health_is_unclear_not_a_verdict(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *_a, **_k: _Resp(b"<html>proxy error</html>"))
    assert ms._mcp_health_probe("http://localhost:8083")[1] == "unclear"


# --- the scan class: what the ledger cannot see -------------------------------
def test_a_scan_tool_with_no_ledger_entry_is_now_probed_at_all(monkeypatch,
                                                               fast_slices):
    """THE new behaviour. read_*/cbm_*/ocr_*/settings record nothing in
    write_status, so before this change nothing was ever asked about them and the
    wait simply ran to the ceiling. A healthy daemon must still never be
    abandoned -- the probe runs, the answer is "alive", the scan finishes."""
    fake, calls = _fake_urlopen(health={"ok": True, "pid": 100}, post_delay=0.25)
    out = json.loads(_wait(monkeypatch, fake))
    assert out == {"ok": True}
    assert calls["post"] == 1
    assert calls["health"] >= 2, "the scan class is back to an unwatched wall"


def test_a_health_probe_failure_never_decides_the_call(monkeypatch, fast_slices):
    """A daemon busy enough to miss /health is what a big scan LOOKS like.

    If this ever fails, the watchdog has started killing live scans on absence of
    evidence -- the one failure mode worse than waiting out the budget.
    """
    fake, calls = _fake_urlopen(
        health_seq=[TimeoutError("busy")] * 6, health=TimeoutError("busy"),
        post_delay=0.25)
    assert json.loads(_wait(monkeypatch, fake)) == {"ok": True}
    assert calls["health"] >= 2


def test_a_single_refusal_never_abandons(monkeypatch, fast_slices):
    """One refused connection is what a restart looks like MID-restart, and the
    call it would kill may be about to be served by the new process."""
    fake, calls = _fake_urlopen(
        health_seq=[ConnectionRefusedError(10061, "refused")],
        health={"ok": True, "pid": 100}, post_delay=0.25)
    assert json.loads(_wait(monkeypatch, fake)) == {"ok": True}
    assert calls["health"] >= 2


def test_consecutive_refusals_abandon_with_a_liveness_verdict(monkeypatch,
                                                              fast_slices):
    fake, calls = _fake_urlopen(
        health=ConnectionRefusedError(10061, "refused"), post_delay=1.5)
    with pytest.raises(ms._MCPDaemonGone) as caught:
        _wait(monkeypatch, fake)
    env = caught.value.mcp_proxy_envelope("read_file_range", 5.0)
    assert env["error_name"] == "MCP_PROXY_DAEMON_UNREACHABLE"
    assert env["health_refusals"] >= ms._MCP_PROXY_HEALTH_STRIKES
    assert calls["post"] == 1, "the POST is never reissued"


def test_a_changed_pid_abandons_as_a_restart(monkeypatch, fast_slices):
    """The one unconfoundable discriminator. A different pid means this request
    died with the old process, so waiting out the budget is pure dead time."""
    fake, _calls = _fake_urlopen(
        health_seq=[{"ok": True, "pid": 100}, {"ok": True, "pid": 100}],
        health={"ok": True, "pid": 777}, post_delay=1.5)
    with pytest.raises(ms._MCPDaemonGone) as caught:
        _wait(monkeypatch, fake)
    env = caught.value.mcp_proxy_envelope("read_file_range", 5.0)
    assert env["error_name"] == "MCP_PROXY_DAEMON_RESTARTED"
    assert (env["daemon_pid_first_seen"], env["daemon_pid_now"]) == (100, 777)


def test_a_health_payload_without_a_pid_decides_nothing(monkeypatch, fast_slices):
    """An older daemon, or a fields= projection that drops pid, must not read as
    a restart. No pid is no evidence."""
    fake, _calls = _fake_urlopen(health={"ok": True}, post_delay=0.25)
    assert json.loads(_wait(monkeypatch, fake)) == {"ok": True}


# --- the ledger still wins, and costs nothing extra --------------------------
def test_a_progressing_ledger_entry_pays_for_no_health_probe(monkeypatch,
                                                             fast_slices):
    """When the daemon already answered "running", asking again is waste."""
    fake, calls = _fake_urlopen(ledger=_RUNNING, post_delay=0.25)
    assert json.loads(_wait(monkeypatch, fake, name="cbm_index")) == {"ok": True}
    assert calls["ledger"] >= 2
    assert calls["health"] == 0


def test_the_stall_path_still_belongs_to_the_ledger(monkeypatch, fast_slices):
    fake, calls = _fake_urlopen(ledger=_STALLED, post_delay=1.5)
    with pytest.raises(ms._MCPOperationStalled) as caught:
        _wait(monkeypatch, fake, name="cbm_index")
    assert caught.value.idle_seconds == 999.0
    assert calls["health"] == 0


# --- one envelope contract, two proxy paths ----------------------------------
def test_both_verdicts_are_one_family_with_one_renderer():
    """The dispatcher duck-types on mcp_proxy_envelope, so a third verdict class
    that forgets to implement it would answer a generic 502 instead."""
    assert issubclass(ms._MCPOperationStalled, ms._MCPProxyAbandoned)
    assert issubclass(ms._MCPDaemonGone, ms._MCPProxyAbandoned)
    with pytest.raises(NotImplementedError):
        ms._MCPProxyAbandoned("x").mcp_proxy_envelope("t", 1.0)


def test_the_stall_envelope_keeps_every_key_a_ca_was_promised():
    """Moving the dict into a method must not quietly drop a field the other
    proxy path's own tests pin."""
    env = ms._MCPOperationStalled(_STALLED, 12.0, 999.0,
                                 payload=_TARGET).mcp_proxy_envelope("cbm_index",
                                                                     3600.0)
    for key in ("error_name", "operation_state", "seconds_since_progress",
                "idle_budget_s", "waited_s", "http_budget_s", "progress",
                "operation_id", "safe_to_retry", "retryable", "ca_next_action",
                "write_status_url", "hint", "commit_state", "tool", "error"):
        assert key in env, key
    assert env["error_name"] == "MCP_PROXY_OPERATION_STALLED"
    assert env["waited_s"] == 12.0
    assert env["http_budget_s"] == 3600.0


@pytest.mark.parametrize("exc", [
    ms._MCPOperationStalled(_STALLED, 5.0, 999.0, payload=_TARGET),
    ms._MCPDaemonGone("restarted", 5.0, pid_first=1, pid_now=2, payload=_TARGET),
    ms._MCPDaemonGone("unreachable", 5.0, strikes=2, payload=_TARGET),
])
def test_no_verdict_ever_advertises_a_safe_retry(exc):
    """Every one of these leaves the daemon-side work in an UNKNOWN state. A
    retryable flag here would turn a lost response into a double write."""
    env = exc.mcp_proxy_envelope("write_fix", 180.0)
    assert env["ok"] is False
    assert env["safe_to_retry"] is False
    assert env["retryable"] is False
    assert env["ca_next_action"] == "inspect_write_status_before_retry"
    assert env["write_status_url"].startswith("/api/write_status?")
    assert "operation_id=" not in env["write_status_url"], (
        "an id minted on this side of the wire is a plausible key for an entry "
        "that does not exist")


def test_a_verdict_without_a_project_still_names_the_tool():
    env = ms._MCPDaemonGone("unreachable", 1.0, strikes=2,
                            payload={}).mcp_proxy_envelope("ocr_scan", 180.0)
    assert "tool=ocr_scan" in env["write_status_url"]


def test_one_refusal_is_never_enough_by_construction():
    """A budget of 1 would make a single refused socket -- the normal appearance
    of a restart in progress -- kill a live call."""
    assert isinstance(ms._MCP_PROXY_HEALTH_STRIKES, int)
    assert ms._MCP_PROXY_HEALTH_STRIKES >= 2


def test_the_budget_is_never_none_for_any_tool():
    """The four names the dispatcher used to hand `None` are the ones that matter
    most here: they are the long index scans."""
    srv = types.SimpleNamespace(ct=types.SimpleNamespace(TOOL_SCHEMAS=[]))
    for name in ("cbm_index", "cbm_incremental_index", "generate_project_map",
                 "refresh_project_map", "read_file_range", "ocr_scan",
                 "settings_get"):
        budget = ms._mcp_timeout_budget(name, None, srv)
        assert isinstance(budget, (int, float)) and not isinstance(budget, bool)
        assert budget > 0, name
