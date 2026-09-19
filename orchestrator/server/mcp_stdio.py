"""MCP stdio server entry point.

Extracted from orchestrator_server.py (the ``--mcp`` branch) so the HTTP daemon and
the MCP stdio surface can evolve independently. ``run_mcp_stdio`` contains the full
zeromcp tool registration + ``_mcp.stdio()`` loop that used to live inline under
``if __name__ == "__main__":``.

Late binding: ``orchestrator_server`` is imported *inside* the function and its
symbols (``ct``, ``dispatch_tool_call``, ``_run_mcp_coro``, ``_tool_names_payload``,
``_tool_schema_payload``) are accessed as attributes at call time, so monkeypatching
on the ``orchestrator_server`` module keeps working.
"""

import asyncio
import functools as _functools
import inspect as _inspect
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as _uuid
from pathlib import Path

_LESSONS_CACHE: list = []
_LESSONS_MTIME: float = 0.0
_MCP_SERVER_GENERATION_SEEN: str | None = None
_MCP_OWNER_CLAIMS: dict[tuple[str, str], str] = {}

# campaign-1608 (A28-P1): the MCP stdio surface was a SECOND [A] process that ran
# stateless tool code IN-PROCESS -- writing straight into sessions.db, which
# ``orchestrator/session/store.py`` documents as owned by exactly one writer. The
# fix moves every stateless block onto a thin HTTP proxy to the long-lived daemon
# on 8083, so the daemon stays the single writer and the single generation owner.
#
# What is deliberately NOT proxied: the stateful T4 seam (call_tool /
# _mcp_call_text -> dispatch_tool_call on the durable _run_mcp_coro loop) keeps
# ``_BROWSER_PAGES`` and its event loop alive across calls; proxying it would drop
# that state on the floor.
_MCP_PROXY_TOKEN: dict[str, str] = {"v": ""}
# A37 #0b: the agent id this host process is serving, learned from whichever call
# first carries one (normally the get_rule_core_text shortcut at bootstrap) and then
# attached as X-Agent-Id to EVERY proxied call. Without it the daemon sees ctx
# "default" for reads whose bootstrap epoch was opened under the real id.
_MCP_AGENT_ID: dict[str, str] = {"v": ""}
_MCP_PROXY_TOKEN_ERRORS = (
    "MUTATION_TOKEN_REQUIRED", "MUTATION_TOKEN_EXPIRED", "TOKEN_REQUIRED",
)
# urlopen(timeout=) is SECONDS, not ms. The old proxy branch in
# orchestrator_server.py used ``timeout=6100`` (~1.7 HOURS) -- almost certainly a
# ms/seconds unit bug. 180s is the FLOOR, not the answer: the per-call budget in
# _mcp_timeout_budget rides on top of it.
_MCP_PROXY_DEFAULT_TIMEOUT = float(os.environ.get("MCP_PROXY_HTTP_TIMEOUT", "180"))
# Margin added to a tool's own ``timeout`` arg so the HTTP wait always outlives the
# work the daemon is doing (otherwise the proxy times out on a call that is still
# legitimately running).
_MCP_PROXY_TIMEOUT_MARGIN = 30.0
# Ceiling for the long class. It replaces an earlier ``http_timeout = None``: an
# unbounded urlopen wait meant a WEDGED daemon hung the calling CA forever with no
# way out but killing the client -- the opposite defect from a 180s cap, and the
# worse one, because a cap at least fails loud (7-RULE #7).
_MCP_PROXY_LONG_TIMEOUT = float(os.environ.get("MCP_PROXY_HTTP_LONG_TIMEOUT", "3600"))
# Tools whose progress is governed server-side rather than by one request/response
# round trip. They get the long ceiling instead of the floor. Do NOT hand-add a tool
# here just because it is slow: declare a ``timeout`` in its schema and
# _mcp_schema_timeout picks it up. A hand mirror of the registry is the drift class
# A31 spent a campaign deleting, and every name below is pinned to a live tool by
# tests/test_a34_mcp_proxy_budget.py so a renamed tool fails loud instead of
# silently dropping back to the floor.
_MCP_PROGRESS_TOOLS = {
    "cbm_index", "cbm_incremental_index",
    "generate_project_map", "refresh_project_map",
}
# The schema fields a tool uses to declare how long its own work may take. Ordered:
# the first one the tool declares wins.
_MCP_TIMEOUT_DECL_FIELDS = ("timeout", "timeout_sec", "timeout_ms")

# --- idle watchdog -----------------------------------------------------------
# The budget above is a CEILING, not a deadline. Raising a number is the wrong
# answer to "this repo is bigger": the question that actually matters is whether
# the daemon is still making progress, and the daemon already answers it --
# /api/write_status returns operation_state in {running, stalled} plus
# seconds_since_progress for every member of _MUTATING_TOOLS (not writes only),
# with its own stall window from ORCHESTRATOR_OPERATION_STALL_SECONDS. So this
# side needs no new endpoint: it waits in slices and asks.
#
# How long each slice of the wait is before we ask the daemon how it is doing.
_MCP_PROXY_POLL_SLICE = float(os.environ.get("MCP_PROXY_POLL_SLICE_SECONDS", "20") or 20)
# A floor, so a misconfigured slice cannot turn the wait into a poll storm against
# the daemon. Named rather than inlined because it is also the knob a test has to
# lower: with a 1s floor a sub-second fake response finishes inside the first join
# and no probe ever runs, which makes a watchdog test pass while measuring nothing.
_MCP_PROXY_POLL_SLICE_FLOOR = 1.0
# The IDLE deadline: how long the daemon may publish NO progress before we stop
# waiting. Deliberately larger than the server's own default stall window (120s)
# so "stalled" has to hold across two independent observers before it decides
# anything -- the label alone is not enough, the number has to agree.
_MCP_PROXY_IDLE_ABANDON = float(
    os.environ.get("MCP_PROXY_IDLE_ABANDON_SECONDS", "180") or 180)
# A status probe is a cheap read; it must never become the thing that hangs.
_MCP_PROXY_STATUS_TIMEOUT = float(
    os.environ.get("MCP_PROXY_STATUS_TIMEOUT_SECONDS", "10") or 10)
# --- liveness watchdog, for the tools the ledger cannot see ------------------
# write_status answers only for _MUTATING_TOOLS, so the idle watchdog above covers
# writes and leaves every SCAN tool -- read_*, cbm_*, ocr_*, settings -- with
# nothing but the wall ceiling. The daemon does publish one fact about itself that
# holds for every tool: GET /health carries its `pid`. So for a read the question
# is not "how long has this taken" (unanswerable -- a big repo is legitimately
# slow) but "is the daemon I POSTed to still serving this call".
#
# Only POSITIVE evidence may end a wait, and there are exactly two kinds:
#   * a DIFFERENT pid -- it restarted, and this request died with the old process;
#   * connection REFUSED -- nothing is listening, so no response can ever arrive.
# A probe that merely times out is NOT evidence: a daemon busy enough to miss
# /health is the normal state of a large scan, and abandoning a live scan is the
# worse of the two defects. Those waits are ended by the finite budget instead,
# which is why the budget must never be None (see _mcp_timeout_budget).
_MCP_PROXY_HEALTH_STRIKES = max(1, int(
    os.environ.get("MCP_PROXY_HEALTH_STRIKES", "2") or 2))


class _MCPProxyAbandoned(Exception):
    """A wait cut short on evidence the daemon published, never on elapsed time.

    Subclasses build their own envelope so BOTH proxy paths -- _mcp_proxy_call here
    and the dispatcher branch in orchestrator_server.py -- answer one contract from
    one source. Two hand-kept copies of an error shape is precisely how those two
    paths drifted apart in the first place: one grew this watchdog while the other
    kept an unbounded urlopen.
    """

    def mcp_proxy_envelope(self, name: str, http_budget: float) -> dict:
        raise NotImplementedError


class _MCPOperationStalled(_MCPProxyAbandoned):
    """The daemon's own ledger reported no progress for longer than the idle budget.

    Carries the evidence, because the caller must report the MEASUREMENT rather
    than a wall clock: a stall is only ever asserted on something the daemon said.
    """

    def __init__(self, status: dict, waited: float, idle_seconds: float,
                 payload: dict | None = None):
        super().__init__(f"no progress for {idle_seconds}s")
        self.status = dict(status or {})
        self.waited = float(waited)
        self.idle_seconds = float(idle_seconds)
        self.payload = dict(payload or {})

    def mcp_proxy_envelope(self, name: str, http_budget: float) -> dict:
        return {
            "ok": False,
            "error_name": "MCP_PROXY_OPERATION_STALLED",
            "tool": name,
            "error": (f"{name} is registered in_flight but published no progress for "
                      f"{self.idle_seconds}s; abandoned the wait after "
                      f"{round(self.waited, 1)}s of a {round(http_budget, 1)}s budget"),
            "commit_state": self.status.get("commit_state") or "unknown",
            "operation_state": "stalled",
            "operation_id": self.status.get("operation_id"),
            "seconds_since_progress": self.idle_seconds,
            "idle_budget_s": _MCP_PROXY_IDLE_ABANDON,
            "waited_s": round(self.waited, 1),
            "http_budget_s": http_budget,
            "progress": self.status.get("progress"),
            "safe_to_retry": False,
            "retryable": False,
            "ca_next_action": "inspect_write_status_before_retry",
            "write_status_url": "/api/write_status?" + urllib.parse.urlencode(
                _mcp_write_status_query(name, self.payload)),
            "hint": ("The wait was cut short by the IDLE deadline, not by elapsed "
                     "time: a long-but-progressing operation is never abandoned, so "
                     "no timeout number needs raising for a large repo. Poll "
                     "write_status for this target until commit_state is terminal; "
                     "do NOT resend this call."),
        }


class _MCPDaemonGone(_MCPProxyAbandoned):
    """The daemon holding this in-flight call is provably not serving it any more.

    The watchdog for every tool that records NO ledger entry -- the scan class. It
    reports which of the two positive verdicts fired, never a wall clock.
    """

    def __init__(self, reason: str, waited: float, strikes: int = 0,
                 pid_first=None, pid_now=None, payload: dict | None = None):
        super().__init__(f"daemon {reason}")
        self.reason = str(reason)
        self.waited = float(waited)
        self.strikes = int(strikes)
        self.pid_first = pid_first
        self.pid_now = pid_now
        self.payload = dict(payload or {})

    def mcp_proxy_envelope(self, name: str, http_budget: float) -> dict:
        if self.reason == "restarted":
            error_name = "MCP_PROXY_DAEMON_RESTARTED"
            detail = (f"the daemon restarted mid-call (pid {self.pid_first} -> "
                      f"{self.pid_now}), so this request died with the old process")
        else:
            error_name = "MCP_PROXY_DAEMON_UNREACHABLE"
            detail = (f"/health refused the connection {self.strikes} times running; "
                      f"nothing is listening, so no response can arrive")
        return {
            "ok": False,
            "error_name": error_name,
            "tool": name,
            "error": (f"abandoned {name} after {round(self.waited, 1)}s of a "
                      f"{round(http_budget, 1)}s budget: {detail}"),
            "commit_state": "unknown",
            "operation_state": self.reason,
            "daemon_pid_first_seen": self.pid_first,
            "daemon_pid_now": self.pid_now,
            "health_refusals": self.strikes,
            "health_refusal_budget": _MCP_PROXY_HEALTH_STRIKES,
            "waited_s": round(self.waited, 1),
            "http_budget_s": http_budget,
            "safe_to_retry": False,
            "retryable": False,
            "ca_next_action": "inspect_write_status_before_retry",
            "write_status_url": "/api/write_status?" + urllib.parse.urlencode(
                _mcp_write_status_query(name, self.payload)),
            "hint": ("This is a LIVENESS verdict, not a timeout: the wait stopped "
                     "because the daemon provably stopped serving this call, so no "
                     "number needs raising. A read/scan tool is safe to call again "
                     "once [A] answers; for a write, poll write_status for this "
                     "target FIRST -- a lost response never proves the write did "
                     "not land."),
        }


def _mcp_write_status_query(name: str, payload: dict) -> list:
    """The (project_path, file_path, tool) target key for a write_status lookup.

    The operation_id is deliberately NOT recomputed here. orchestrator_server's
    _mcp_proxy_failure_payload documents why: the id is sha256 over the body the
    DISPATCHER sees, after it rewrites task_epoch / prompt_hash / fields /
    project_path / agent_id, so an id minted on this side of the wire would be a
    plausible-looking key for an entry that does not exist -- and a 404 for it
    would read as "no write happened". The target lookup is the supported route,
    narrowed by &tool= because index and script tools all record file_path="", so
    one project root can hold several unrelated operations at once.
    """
    body = payload or {}
    pp = str(body.get("project_path") or "")
    fp = str(body.get("file_path") or body.get("path") or body.get("dst") or "")
    return [(k, v) for k, v in (("project_path", pp), ("file_path", fp),
                                ("tool", str(name or ""))) if v]


def _mcp_operation_probe(base: str, name: str, payload: dict) -> dict:
    """Ask the daemon whether this operation is still moving. {} means "no evidence".

    Every failure mode collapses to {}: a 404 (no ledger entry, i.e. a read-only
    tool), a 409 (ambiguous target), malformed JSON, an unreachable daemon. A probe
    is an observability aid and must never be the thing that decides the fate of a
    call that may still be running -- no evidence means keep waiting, not give up.
    """
    query = _mcp_write_status_query(name, payload)
    if not any(k == "project_path" for k, _ in query):
        return {}
    url = f"{base}/api/write_status?" + urllib.parse.urlencode(query)
    try:
        with urllib.request.urlopen(url, timeout=_MCP_PROXY_STATUS_TIMEOUT) as resp:
            parsed = json.loads(resp.read().decode("utf-8", "replace"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _mcp_health_probe(base: str) -> tuple:
    """Is the daemon still there? Returns (payload, verdict).

    verdict is one of:
      "alive"    -- it answered. Even a 4xx/5xx counts: a process that returns a
                    status is a process that is running, and liveness (not
                    correctness) is the only question being asked here.
      "refused"  -- nothing is listening on the port. The one unambiguous "gone".
      "unclear"  -- a timeout, a DNS error, garbage JSON. Decides NOTHING, because
                    a daemon too busy to answer /health is the normal state of a
                    large scan and killing that scan is the worse defect.

    The projection keeps this the cheapest call the daemon serves: it runs once per
    wait slice on every long call, and all it needs back is the pid.
    """
    url = f"{base}/health?fields=ok,pid,generation,executor_active_total"
    try:
        with urllib.request.urlopen(url, timeout=_MCP_PROXY_STATUS_TIMEOUT) as resp:
            parsed = json.loads(resp.read().decode("utf-8", "replace"))
        return (parsed if isinstance(parsed, dict) else {}), "alive"
    except urllib.error.HTTPError:
        return {}, "alive"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        return {}, ("refused" if isinstance(reason, ConnectionRefusedError)
                    else "unclear")
    except ConnectionRefusedError:
        return {}, "refused"
    except Exception:
        return {}, "unclear"


def _mcp_proxy_wait(req, http_timeout: float, name: str, payload: dict, base: str) -> str:
    """Wait for one proxied response on an IDLE deadline instead of a wall clock.

    The POST runs on a daemon thread and is NEVER re-issued: a urllib socket
    timeout abandons its response irrecoverably, so "short timeout + retry" would
    replay a mutation. The thread keeps the full ceiling as its socket timeout, so
    nothing regresses; this loop only makes it possible to stop waiting EARLY, and
    only on evidence the daemon itself published.

    Raises _MCPOperationStalled when the daemon's ledger reports a stall, and
    _MCPDaemonGone when the daemon it was POSTed to is provably no longer serving
    this call; otherwise raises whatever urlopen raised, so the existing failure
    branches keep owning it. Both carry their own envelope so either proxy path
    reports the same contract.
    """
    slice_s = max(_MCP_PROXY_POLL_SLICE_FLOOR, _MCP_PROXY_POLL_SLICE)
    if _MCP_PROXY_IDLE_ABANDON <= 0 or slice_s >= http_timeout:
        # Nothing a probe could change: one plain bounded wait.
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            return resp.read().decode("utf-8", "replace")

    box: dict = {}

    def _run() -> None:
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                box["body"] = resp.read().decode("utf-8", "replace")
        except BaseException as exc:  # re-raised on the caller's thread, never swallowed
            box["exc"] = exc

    worker = threading.Thread(target=_run, name=f"mcp-proxy-{name}", daemon=True)
    worker.start()
    started = time.monotonic()
    pid_seen: dict = {}
    refusals = 0
    while True:
        worker.join(slice_s)
        if not worker.is_alive():
            break
        waited = time.monotonic() - started
        if waited >= http_timeout:
            # Let the socket timeout own this failure; give it a moment to surface.
            worker.join(5.0)
            break
        status = _mcp_operation_probe(base, name, payload)
        if (status.get("ok")
                and str(status.get("commit_state") or "") == "in_flight"):
            idle = status.get("progress")
            idle_s = idle.get("seconds_since_progress") if isinstance(idle, dict) else None
            if (str(status.get("operation_state") or "") == "stalled"
                    and isinstance(idle_s, (int, float)) and not isinstance(idle_s, bool)
                    and float(idle_s) >= _MCP_PROXY_IDLE_ABANDON):
                raise _MCPOperationStalled(status, waited, float(idle_s),
                                           payload=payload)
            # Still running: a long-but-progressing operation is never abandoned, and
            # it costs no health probe either -- the ledger already answered.
            continue
        # Everything else lands here: a TERMINAL ledger entry (committed /
        # not_committed -- the body is on its way back, and work the ledger says
        # finished must never be turned into a failure) and, far more often, NO entry
        # at all, because write_status only tracks _MUTATING_TOOLS. That second case
        # is every scan tool -- read_*, cbm_*, ocr_*, settings -- which until now had
        # no watchdog whatsoever on this side: absence of evidence waited out the
        # entire ceiling. So ask the one question that IS answerable for a read, and
        # answer it only on positive evidence (see _mcp_health_probe): a probe that
        # merely times out proves nothing, so it decides nothing and the finite
        # budget stays the backstop for it.
        health, verdict = _mcp_health_probe(base)
        if verdict == "refused":
            refusals += 1
            if refusals >= _MCP_PROXY_HEALTH_STRIKES:
                raise _MCPDaemonGone("unreachable", waited, strikes=refusals,
                                     pid_first=pid_seen.get("v"), payload=payload)
            continue
        refusals = 0
        if verdict != "alive":
            continue
        pid_now = health.get("pid")
        if not isinstance(pid_now, int) or isinstance(pid_now, bool):
            continue
        if "v" not in pid_seen:
            # Baselined at the FIRST probe, not at the POST: a restart inside the
            # opening slice is not detected. That is one slice of blindness bought
            # for zero probes on the short calls that are the vast majority.
            pid_seen["v"] = pid_now
        elif pid_now != pid_seen["v"]:
            raise _MCPDaemonGone("restarted", waited, pid_first=pid_seen["v"],
                                 pid_now=pid_now, payload=payload)

    if "body" in box:
        return box["body"]
    exc = box.get("exc")
    if exc is not None:
        raise exc
    raise TimeoutError(
        f"{name} produced neither a response nor an error within {http_timeout}s")


def _mcp_schema_timeout(name: str, srv=None) -> float:
    """The budget a tool DECLARES for itself, in seconds; 0.0 when it declares none.

    Read from the live registry, never from a table in this module: whatever
    ``registry_data`` says a tool's ``timeout`` default is becomes the proxy's HTTP
    budget for it. That is the whole point -- ``backup_project`` (600) and
    ``run_test_profile`` (120) need no entry anywhere here, and a tool added
    tomorrow with ``timeout: 900`` is wired the moment it is registered.

    ``TOOL_SCHEMAS`` is a LIST of OpenAI-style envelopes
    (``{"type": "function", "function": {"name", "parameters"}}``), NOT a dict keyed
    by name, and it is REBOUND as later schema batches are appended -- so it is read
    through the module attribute on every call and never captured at import time.
    """
    try:
        ct = getattr(srv, "ct", None)
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
                if not isinstance(default, (int, float)) or isinstance(default, bool):
                    continue
                if default <= 0:
                    continue
                return float(default) / 1000.0 if field.endswith("_ms") else float(default)
            return 0.0
    except Exception:
        # A half-loaded or malformed registry must not break the transport; the
        # caller falls back to the floor.
        return 0.0
    return 0.0


def _mcp_timeout_budget(name: str, timeout_override: float | None = None,
                        srv=None) -> float:
    """Resolve ONE per-call HTTP budget in seconds. The single place this is decided.

    Precedence, most specific first:
      1. ``MCP_PROXY_TIMEOUT__<TOOL>`` -- the per-tool escape hatch for a repo that
         turns out slower than any default, with no code change at all. It is the only
         ABSOLUTE knob: it returns, so it can also LOWER a budget.
      2. the caller's own ``timeout`` argument, + margin.
      3. the timeout the tool DECLARES in its schema, + margin.
      4. the long ceiling, for the server-side-progress class.
      5. ``MCP_PROXY_HTTP_TIMEOUT`` -- the floor, re-read HERE on every call.

    Never returns None. An unbounded wait is not a budget: it converts a wedged
    daemon into a wedged CA.

    A37: the floor is re-read from the environment on EVERY call because
    ``_MCP_PROXY_DEFAULT_TIMEOUT`` freezes at IMPORT, and this module is imported long
    before any caller can set the knob -- so a documented environment variable was
    silently inert for every process that set it after startup, which is all of them.
    Measured: ``tests/test_mcp_proxy_failure_identity`` sets it to 0.35 and then waited
    the whole frozen 180s ceiling against a black-hole socket, three times over. The
    floor keeps its MAX semantics (it never lowers the long class); a test or operator
    that needs an absolute cap uses knob 1.
    """
    raw = os.environ.get("MCP_PROXY_TIMEOUT__" + name.upper())
    if raw:
        try:
            explicit = float(raw)
        except (TypeError, ValueError):
            explicit = 0.0
        if explicit > 0:
            return explicit
    budget = _MCP_PROXY_DEFAULT_TIMEOUT
    env_floor = os.environ.get("MCP_PROXY_HTTP_TIMEOUT")
    if env_floor:
        try:
            parsed_floor = float(env_floor)
        except (TypeError, ValueError):
            parsed_floor = 0.0
        if parsed_floor > 0:
            budget = parsed_floor
    if name in _MCP_PROGRESS_TOOLS:
        budget = max(budget, _MCP_PROXY_LONG_TIMEOUT)
    declared = 0.0
    if timeout_override is not None:
        try:
            declared = float(timeout_override)
        except (TypeError, ValueError):
            declared = 0.0
    if declared <= 0:
        declared = _mcp_schema_timeout(name, srv)
    if declared > 0:
        budget = max(budget, declared + _MCP_PROXY_TIMEOUT_MARGIN)
    return budget


def _mcp_proxy_base_url(srv) -> str:
    port = getattr(srv, "PORT", 8083)
    return os.environ.get(
        "ORCHESTRATOR_BASE_URL", f"http://127.0.0.1:{port}",
    ).rstrip("/")


def _mcp_proxy_fetch_token(srv) -> str:
    """Fetch the DAEMON's mutation token via GET /api/token.

    Critical: the MCP process mints its OWN ``EXPECTED_TOKEN`` at import that the
    daemon does not recognise, so a proxied mutation must carry the DAEMON's token
    -- never ``srv.EXPECTED_TOKEN``. (That confusion is the latent bug still living
    in council_message_async's _fire().) The token stays inside this process; it is
    never returned to the CA.
    """
    base = _mcp_proxy_base_url(srv)
    try:
        with urllib.request.urlopen(f"{base}/api/token", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace")) or {}
        _MCP_PROXY_TOKEN["v"] = data.get("token") or ""
    except Exception:
        _MCP_PROXY_TOKEN["v"] = ""
    return _MCP_PROXY_TOKEN["v"]


def _mcp_proxy_call(name: str, payload: dict | None = None,
                    timeout_override: float | None = None,
                    _retried: bool = False) -> str:
    """POST one stateless tool call to the daemon's /api/call/<name> and return JSON text.

    Fail-loud contract (A28-P1 requirement): connection or HTTP failures return a
    JSON string with ``ok:false`` and a specific ``error_name`` -- NEVER a silent
    fall-through to in-process execution (that is the ``except Exception: pass``
    anti-pattern in the old orchestrator_server proxy branch, which reintroduced a
    second writer exactly when the daemon was unreachable).

    The daemon stamps the response with its OWN ``generation`` (orchestrator_server
    line ~13662), so proxied blocks need no ``_mcp_guard_response`` wrapper: the
    stamp they carry is already the authoritative one, which is the whole point.
    """
    import sys
    srv = sys.modules.get("__main__")
    if srv is None or not hasattr(srv, "PORT"):
        try:
            import orchestrator_server as srv  # type: ignore
        except Exception:
            srv = None
    payload = payload or {}
    base = _mcp_proxy_base_url(srv)
    url = f"{base}/api/call/{name}"
    http_timeout = _mcp_timeout_budget(name, timeout_override, srv)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Origin must be in the daemon's allowlist or mutating tools are 403'd.
        "Origin": base if base.startswith("http") else "http://localhost:8083",
    }
    if _MCP_PROXY_TOKEN["v"]:
        headers["X-Orchestrator-Token"] = _MCP_PROXY_TOKEN["v"]
    # The old proxy branch FORGOT X-Project-Path, so project-scoped mutation gates
    # could 403. Forward it whenever the payload names a project.
    if isinstance(payload, dict) and payload.get("project_path"):
        headers["X-Project-Path"] = str(payload["project_path"])
    # A37 #0b: carry identity on EVERY proxied call, not only the ones whose schema
    # happens to declare agent_id. The daemon keys the bootstrap epoch (and the
    # rule-core fetch-once guard) by agent id, so a host that sent it ONLY on
    # get_rule_core_text opened the epoch as "claultnai" and then read every file as
    # "default": the Lan-1 schema push stayed armed for an identity that never called
    # again -- inert for exactly the desktop CA it was built for. One host process
    # serves one CA, so remembering the last id seen is the correct scope.
    _agent_id = ""
    if isinstance(payload, dict):
        _agent_id = str(payload.get("agent_id") or "").strip()
    if _agent_id:
        _MCP_AGENT_ID["v"] = _agent_id
    _agent_id = _agent_id or _MCP_AGENT_ID["v"] or str(
        os.environ.get("COUNCIL_AGENT_ID") or "").strip()
    if _agent_id:
        headers["X-Agent-Id"] = _agent_id
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        body = _mcp_proxy_wait(req, http_timeout, name, payload, base)
    except _MCPProxyAbandoned as abandoned:
        # Fail loud with the MEASUREMENT rather than a wall clock. The envelope is
        # built BY THE EXCEPTION so the dispatcher's proxy branch in
        # orchestrator_server.py answers the same shape from the same source: two
        # hand-kept copies of one error contract is exactly how these two proxy
        # paths drifted apart, one growing a watchdog while the other kept an
        # unbounded urlopen.
        return json.dumps(abandoned.mcp_proxy_envelope(name, http_timeout),
                          ensure_ascii=False)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {}
        # One token retry, mirroring the generated pack client.
        if (not _retried) and exc.code in (401, 403) and \
                str(parsed.get("error_name", "")) in _MCP_PROXY_TOKEN_ERRORS:
            if _mcp_proxy_fetch_token(srv):
                return _mcp_proxy_call(name, payload, timeout_override, _retried=True)
        if isinstance(parsed, dict) and parsed:
            parsed.setdefault("ok", False)
            parsed.setdefault("error_name", "MCP_PROXY_HTTP_ERROR")
            parsed.setdefault("_mcp_proxy", {})
            if isinstance(parsed["_mcp_proxy"], dict):
                parsed["_mcp_proxy"].update({"tool": name, "http_status": exc.code})
            return json.dumps(parsed, ensure_ascii=False)
        return json.dumps({
            "ok": False,
            "error_name": "MCP_PROXY_HTTP_ERROR",
            "tool": name,
            "http_status": exc.code,
            "body": raw[:2000],
            "hint": "Daemon returned an HTTP error for a proxied tool call.",
        }, ensure_ascii=False)
    except Exception as exc:
        # Match dispatcher recovery: timeout can lose only the response after
        # commit. Never turn that into an invitation to replay a mutation.
        failure_builder = getattr(srv, "_mcp_proxy_failure_payload", None)
        if failure_builder is not None:
            failure = failure_builder(name, payload, exc)
            failure["tool"] = name
            failure["base_url"] = base
            reason = getattr(exc, "reason", None)
            unreachable = (isinstance(exc, ConnectionRefusedError)
                           or isinstance(reason, ConnectionRefusedError))
            if unreachable:
                failure["error_name"] = "MCP_PROXY_UNREACHABLE"
            elif str(failure.get("commit_state") or "") == "unknown":
                # The ceiling was reached, so the RESPONSE is lost -- but a lost
                # response is not an unknown outcome. Ask the daemon's own ledger
                # once and answer with what it measured, so a CA is not left
                # guessing whether the mutation landed. Only "unknown" is filled in:
                # a confident transport verdict is never second-guessed, and a probe
                # that finds no entry leaves the honest "unknown" standing rather
                # than inventing a verdict.
                probe = _mcp_operation_probe(base, name, payload)
                measured = str(probe.get("commit_state") or "") if probe.get("ok") else ""
                if measured:
                    failure["commit_state"] = measured
                    failure["commit_state_source"] = "write_status_ledger"
                    failure["operation_state"] = probe.get("operation_state")
                    failure["operation_id"] = probe.get("operation_id")
                    failure["progress"] = probe.get("progress")
                    failure["safe_to_retry"] = measured == "not_committed"
                    failure["ca_next_action"] = {
                        "committed": "do_not_retry_write_already_committed",
                        "not_committed": "retry_write",
                        "in_flight": "wait_and_poll_write_status_do_not_retry",
                    }.get(measured, "inspect_write_status_before_retry")
            return json.dumps(failure, ensure_ascii=False)
        return json.dumps({
            "ok": False,
            "error_name": "MCP_PROXY_UNREACHABLE",
            "tool": name,
            "error": f"{type(exc).__name__}: {exc}",
            "base_url": base,
            "commit_state": "unknown", "safe_to_retry": False,
            "ca_next_action": "inspect_write_status_before_retry",
            "hint": "The [A] daemon on 8083 could not be reached; MCP does NOT fall "
                    "back to in-process execution (single-writer invariant).",
        }, ensure_ascii=False)
    # Success body is the tool's own JSON dict, already daemon-generation-stamped.
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and (not _retried) and \
                str(parsed.get("error_name", "")) in _MCP_PROXY_TOKEN_ERRORS:
            if _mcp_proxy_fetch_token(srv):
                return _mcp_proxy_call(name, payload, timeout_override, _retried=True)
    except Exception:
        pass
    return body


# A37 #1b: the one place a named MCP tool's `fields=` value lives between the wrapper
# that accepted it and the call that has to send it. A thread-local (not a global) because
# zeromcp may serve concurrent calls, and each must send its OWN projection or a caller
# could silently receive another caller's fields= -- a correctness bug, not a token bug.
_MCP_FIELDS_LOCAL = threading.local()


def _mcp_fields_current() -> str:
    """The `fields=` value the MCP tool call on this thread arrived with, else "".

    Empty string means "the caller did not project", which must behave exactly like the
    pre-A37 bridge: send no `fields` at all and let the daemon answer full-fat.
    """
    try:
        return str(getattr(_MCP_FIELDS_LOCAL, "value", "") or "")
    except Exception:
        return ""


def _mcp_tool_fields_decorator(raw_tool):
    """Wrap zeromcp's ``@_mcp.tool`` so EVERY registered tool accepts ``fields=``.

    `fields=` is accepted on every [A] tool over HTTP, but none of the 62 named MCP
    wrappers ever declared it -- so on this surface the daemon's own "pass fields= next
    time" size hint was advice a caller physically could not take, and every answer came
    back full-fat. Anh's requirement for A37 was that HTTP and MCP be usable EQUALLY.

    Done here, once, rather than in 62 hand-edited signatures: duplicating one fact across
    62 places is exactly how this bridge drifted from the daemon before, and a wrapper
    added tomorrow inherits `fields=` with nobody remembering to add it.

    FAIL-SAFE by construction: if zeromcp builds its JSON schema from something other than
    ``__signature__``, the parameter is simply not advertised and every existing call still
    works unchanged (``fields`` defaults to ""). The failure mode is "the feature is not
    exposed", never "a call breaks" -- which also means only a live MCP client, after a
    reconnect, can confirm the exposure.
    """
    def _tool(fn):
        wrapped_sig = _inspect.signature(fn)
        if "fields" in wrapped_sig.parameters:
            return raw_tool(fn)

        @_functools.wraps(fn)
        def _fields_aware(*args, fields: str = "", **kwargs):
            previous = getattr(_MCP_FIELDS_LOCAL, "value", "")
            _MCP_FIELDS_LOCAL.value = str(fields or "")
            try:
                return fn(*args, **kwargs)
            finally:
                _MCP_FIELDS_LOCAL.value = previous

        params = [p for p in wrapped_sig.parameters.values()
                  if p.kind is not _inspect.Parameter.VAR_KEYWORD]
        params.append(_inspect.Parameter(
            "fields", _inspect.Parameter.KEYWORD_ONLY, default="", annotation=str))
        # __signature__ is assigned AFTER functools.wraps on purpose: wraps copies
        # __wrapped__, and inspect.signature follows that back to fn -- which would hide
        # the parameter just added. An explicit __signature__ outranks __wrapped__.
        _fields_aware.__signature__ = wrapped_sig.replace(parameters=params)
        _fields_aware.__annotations__ = {
            **dict(getattr(fn, "__annotations__", {}) or {}), "fields": str}
        return raw_tool(_fields_aware)

    return _tool


def _mcp_live_discovery(endpoint: str, query: dict | None = None) -> str:
    """Discovery must share the daemon registry used by proxied calls."""
    import sys
    srv = sys.modules.get("__main__")
    if srv is None or not hasattr(srv, "PORT"):
        import orchestrator_server as srv
    url = _mcp_proxy_base_url(srv) + endpoint
    query = dict(query or {})
    # The discovery routes are HTTP GETs, so a projection rides the QUERY STRING here,
    # not a JSON body -- and the discovery payloads (`/api/tools/names`, a full schema)
    # are among the largest [A] returns, which is exactly where `fields=` pays. Same
    # precedence as _mcp_call_text: an explicit value already in the query WINS.
    _fields_now = _mcp_fields_current()
    if _fields_now and not query.get("fields"):
        query["fields"] = _fields_now
    if query:
        encoded = {}
        for k, v in query.items():
            if v is None or v == "":
                continue
            if isinstance(v, bool):
                encoded[k] = "true" if v else "false"
            else:
                encoded[k] = v
        if encoded:
            url += "?" + urllib.parse.urlencode(encoded)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        if body:
            return body
        return json.dumps({"ok": False, "error_name": "MCP_DISCOVERY_HTTP_ERROR",
                           "http_status": exc.code, "ca_next_action": "inspect_daemon_discovery"})
    except Exception as exc:
        return json.dumps({"ok": False, "error_name": "MCP_DISCOVERY_UNAVAILABLE",
                           "error": f"{type(exc).__name__}: {exc}",
                           "ca_next_action": "inspect_daemon_discovery",
                           "local_schema_fallback": False})


def _mcp_generation_file_path(srv) -> Path | None:
    configured = getattr(srv, "SERVER_GENERATION_FILE", None)
    if configured:
        return Path(configured)
    # Test doubles and alternate MCP hosts may not own the daemon's published
    # stamp. Do not infer a live project-relative file from their cwd.
    return None


def _mcp_probe_generation(srv) -> dict:
    """Read the daemon generation file first, with HTTP as compatibility fallback."""
    port = getattr(srv, "PORT", 8083)
    local = getattr(srv, "SERVER_GENERATION", None)
    local = str(local) if local is not None else None
    generation_file = _mcp_generation_file_path(srv)
    file_error = "generation file not configured"

    if generation_file is not None:
        try:
            published = json.loads(generation_file.read_text(encoding="utf-8"))
            generation = published.get("generation") if isinstance(published, dict) else None
            if generation:
                return {
                    "generation": str(generation),
                    "source": "daemon_file",
                    "probe_error": "",
                    "probe_url": f"file://{generation_file}",
                    "generation_file": str(generation_file),
                    "local_generation": local,
                    "daemon_pid": published.get("pid"),
                    "daemon_port": published.get("port"),
                }
            file_error = "generation file has no usable generation field"
        except Exception as exc:
            file_error = f"{type(exc).__name__}: {exc}"

    base_url = os.environ.get(
        "ORCHESTRATOR_BASE_URL", f"http://localhost:{port}",
    ).rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        generation = payload.get("generation")
    except Exception as exc:
        return {
            "generation": None,
            "source": "unreachable",
            "probe_error": f"generation_file: {file_error}; http: {type(exc).__name__}: {exc}",
            "probe_url": f"{base_url}/health",
            "generation_file": str(generation_file) if generation_file else None,
            "local_generation": local,
        }
    if generation is None:
        return {
            "generation": None,
            "source": "no_generation_field",
            "probe_error": f"generation_file: {file_error}; daemon /health returned no generation field",
            "probe_url": f"{base_url}/health",
            "generation_file": str(generation_file) if generation_file else None,
            "local_generation": local,
        }
    return {
        "generation": str(generation),
        "source": "daemon",
        "probe_error": "",
        "probe_url": f"{base_url}/health",
        "generation_file": str(generation_file) if generation_file else None,
        "local_generation": local,
    }

def _mcp_current_server_generation(srv) -> str | None:
    """Daemon generation stamp, or None when the daemon could not be reached."""
    return _mcp_probe_generation(srv)["generation"]


def _mcp_generation_gate(current_generation: str | None) -> dict | None:
    """Track the daemon generation and SELF-HEAL across a restart.

    ``None`` means "could not determine" and must neither become the remembered
    baseline nor be compared against it: recording it would make the next real
    stamp look like a restart, and comparing it would mask one.

    TA-5 fix (2026-08-18): the old gate latched the FIRST generation into a module
    global and returned MCP_STALE_RELOAD_REQUIRED forever once the daemon restarted
    — killing every MCP tool for the whole client-process lifetime until a manual
    reload. That was needless: read/write/validate tools now proxy over HTTP to the
    daemon (``_mcp_proxy_call``), so a call always reaches the LIVE daemon
    regardless of which generation this stdio bridge first saw. The generation
    stamp is diagnostic, not a correctness guard. So on a generation change we
    RE-BASELINE to the live value and let the call through (logging the transition
    once), instead of failing shut. Set CLM_MCP_STRICT_GENERATION=1 to restore the
    old fail-closed behaviour if a client ever needs the hard reload signal.
    """
    global _MCP_SERVER_GENERATION_SEEN
    if current_generation is None:
        return None
    if _MCP_SERVER_GENERATION_SEEN is None:
        _MCP_SERVER_GENERATION_SEEN = current_generation
        return None
    if _MCP_SERVER_GENERATION_SEEN != current_generation:
        previous = _MCP_SERVER_GENERATION_SEEN
        if os.environ.get("CLM_MCP_STRICT_GENERATION", "").strip() == "1":
            return {
                "ok": False,
                "error_name": "MCP_STALE_RELOAD_REQUIRED",
                "hint": "Reload Claude/MCP client",
                "generation": current_generation,
                "expected_generation": previous,
            }
        # Self-heal: adopt the live generation so subsequent calls pass cleanly.
        _MCP_SERVER_GENERATION_SEEN = current_generation
        try:
            # ISS-07: `sys` was never imported in this module, so this branch
            # raised NameError into the bare `except Exception: pass` below and
            # the ONE line documenting a daemon restart was never written. The
            # self-heal worked; its only evidence did not.
            sys.stderr.write(
                f"[mcp_stdio] daemon generation changed {previous} -> "
                f"{current_generation}; re-baselined (self-heal, calls proxy to live daemon)\n",
            )
        except Exception:
            pass
        return None
    return None


_MCP_GEN_DIVERGED_SINCE: dict = {}


def _mcp_generation_disclosure(payload: dict, daemon_gen, local_gen) -> None:
    """State the daemon-vs-MCP-host relationship instead of leaving it inferable.

    ISS-07: after every daemon restart the two stamps diverge permanently for the
    lifetime of the MCP client process -- `generation` moves to the new daemon,
    `mcp_generation` stays whatever this host process minted at import. That is
    by design (tool calls proxy over HTTP, so they always reach the live daemon),
    but the payload never SAID so: a reader comparing the two fields saw a silent
    mismatch and could not tell "correct by design" from "the MCP bridge is
    running dead code". Both readings were available and only one was true.

    So name it. `mcp_generation_matches_daemon` answers the comparison directly,
    and on a mismatch the note says what still holds (results come from the live
    daemon) and what does not (this host's own bridge code is whatever it
    imported), plus how long the divergence has been observed.
    """
    if not daemon_gen or not local_gen:
        payload.setdefault("mcp_generation_matches_daemon", None)
        return
    matches = str(daemon_gen) == str(local_gen)
    payload.setdefault("mcp_generation_matches_daemon", matches)
    if matches:
        _MCP_GEN_DIVERGED_SINCE.pop("since", None)
        return
    since = _MCP_GEN_DIVERGED_SINCE.setdefault("since", time.time())
    payload.setdefault("mcp_generation_diverged_s", round(time.time() - since, 1))
    payload.setdefault("mcp_generation_note", (
        f"daemon is at generation {daemon_gen}; this MCP host process minted "
        f"{local_gen} at its own import and cannot re-mint without a client "
        "reconnect. Tool results are NOT stale: every call proxies to the live "
        "daemon over HTTP. Reconnect the MCP client only when the bridge code "
        "itself (orchestrator/server/mcp_stdio.py or the tool registry it loads) "
        "changed — a plain daemon restart does not require it."))


def _mcp_dump(payload: dict) -> str:
    """Serialise an MCP response after pruning fields_unknown of late-stamped keys.

    A37 #4, MCP half. The daemon's R101 projection ran before ANY of the envelope
    stamps existed, and this host adds four more of its own (generation_source,
    mcp_generation, mcp_generation_matches_daemon/diverged_s/note) after the fact,
    so `fields=ok,mcp_generation` used to come back naming mcp_generation as a key
    the response does not have -- while carrying it. Every exit of
    _mcp_add_generation goes through here so a new stamp cannot reopen the hole.
    """
    try:
        from orchestrator.core.fields_param import prune_unknown_fields
        prune_unknown_fields(payload)
    except Exception:
        pass  # advisory disclosure only: never fail a good response over it
    return json.dumps(payload, ensure_ascii=False)


def _mcp_add_generation(text: str, generation) -> str:
    """Add generation to JSON-object MCP responses without changing other fields.

    ``generation`` is either a probe dict from ``_mcp_probe_generation`` or a
    bare string (back-compat). When the probe failed, the response says so
    explicitly instead of carrying a stamp that would be read as the daemon's.
    """
    try:
        payload = json.loads(text)
    except Exception:
        return text
    if not isinstance(payload, dict):
        return text
    if isinstance(generation, dict):
        if str(generation.get("source") or "").startswith("daemon"):
            # A43-P1: the daemon stamp is authoritative and MUST win.
            # dispatch_tool_call (orchestrator_server.py) pre-stamps the payload
            # with THIS (MCP) process's SERVER_GENERATION via setdefault, so by
            # the time the wrapper runs ``generation`` already carries the MCP
            # stamp. A plain setdefault here would leave that MCP stamp in place
            # while ``generation_source`` proudly says "daemon" -- two adjacent
            # fields telling two different stories, the wrong one asserting.
            # So replace it, but only when the existing value is the MCP's own
            # local stamp (the dispatch auto-stamp): a generation a tool set
            # deliberately to something else is still respected.
            daemon_gen = generation.get("generation")
            local_gen = generation.get("local_generation")
            # `daemon_gen` in the tuple: a payload that ALREADY carries the daemon
            # stamp agrees with the probe, so it is a daemon stamp -- labelling it
            # "tool_supplied" would be a second wrong label in the other direction.
            if payload.get("generation") in (None, local_gen, daemon_gen):
                payload["generation"] = daemon_gen
                payload.setdefault("generation_source", "daemon")
            else:
                # A43-P1 follow-up: keeping a tool's deliberate value is right,
                # but then `generation` is NOT the daemon's and saying "daemon"
                # would rebuild the exact contradiction this fix removes -- the
                # wrong field asserting. Name the situation instead, and expose
                # the daemon stamp under its own key so nothing is hidden.
                payload.setdefault("generation_source", "tool_supplied")
                payload.setdefault("daemon_generation", daemon_gen)
            # Always disclose which code actually executed the call, so a reader
            # can tell "daemon is at gen X" apart from "MCP host is running Y".
            payload.setdefault("mcp_generation", local_gen)
            _mcp_generation_disclosure(payload, daemon_gen, local_gen)
            return _mcp_dump(payload)
        payload.setdefault("generation", generation.get("generation"))
        payload.setdefault("generation_source", generation.get("source"))
        if generation.get("probe_error"):
            payload.setdefault("generation_probe_error", generation["probe_error"])
        if generation.get("source") != "daemon":
            payload.setdefault("mcp_generation", generation.get("local_generation"))
        return _mcp_dump(payload)
    payload.setdefault("generation", generation)
    return _mcp_dump(payload)

def _load_lessons(srv) -> list:
    """Load LESSONS_LOG.md rows (cached by mtime). Returns list of (keyword, fix)."""
    global _LESSONS_CACHE, _LESSONS_MTIME
    try:
        path = srv._HERE / ".council" / "LESSONS_LOG.md"
        if not path.exists():
            return []
        mtime = path.stat().st_mtime
        if mtime != _LESSONS_MTIME and _LESSONS_CACHE:
            _LESSONS_MTIME = mtime
            _LESSONS_CACHE = []
        if _LESSONS_CACHE:
            return _LESSONS_CACHE
        rows = []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("|") or "keyword" in line or "---" in line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4 and parts[1]:
                    rows.append((parts[1].lower(), parts[3] if len(parts) > 3 else parts[2]))
        _LESSONS_CACHE = rows
        _LESSONS_MTIME = mtime
        return rows
    except Exception:
        return []


def _inject_lessons(srv, text: str) -> str:
    """Append matching pitfalls from LESSONS_LOG.md to the dispatch text."""
    try:
        rows = _load_lessons(srv)
        if not rows or not text:
            return text
        low = (text or "").lower()
        hits = [(kw, fix) for kw, fix in rows if kw and kw in low]
        if not hits:
            return text
        # Dedup + cap at 5.
        seen = set()
        unique = []
        for kw, fix in hits:
            if kw not in seen:
                seen.add(kw)
                unique.append(f"- [{kw}] {fix}")
        if not unique:
            return text
        return text + "\n\nKNOWN PITFALLS (auto-recall from LESSONS_LOG.md):\n" + "\n".join(unique[:5])
    except Exception:
        return text


def run_mcp_stdio() -> None:
    """Run the zeromcp MCP stdio server (the former ``--mcp`` body).

    Double-import guard (registry drift fix): when launched as ``python
    orchestrator_server.py --mcp``, the module is loaded as ``__main__`` and
    its module-level ``_register_tool`` calls have already executed. Importing
    ``orchestrator_server`` again here would re-execute that body and raise
    ``RegistryDriftError`` (council_message already registered). Reuse the
    already-loaded ``__main__`` module from ``sys.modules`` instead.
    """
    import sys
    srv = sys.modules.get("__main__")
    if srv is None or not hasattr(srv, "ct"):
        import orchestrator_server as srv  # fallback: imported as a normal module

    # ── MCP stdio mode ─────────────────────────────────────────────────
    try:
        from zeromcp import McpServer

        _mcp = McpServer("project-orchestrator")

        # A37 #1b ROOT FIX -- every named MCP tool gains `fields=` from ONE place.
        # The surgery itself lives MODULE-LEVEL in _mcp_tool_fields_decorator so it can be
        # tested without starting the stdio loop (a decorator defined in here would only be
        # reachable by running the server). Rebinding _mcp.tool means a wrapper added
        # tomorrow gets `fields=` for free and nobody can forget it -- the alternative was
        # hand-editing 62 @_mcp.tool signatures, which is precisely the duplication that let
        # this bridge drift from the daemon before (A33: the bridge's own registry answering
        # as if it were the daemon's).
        try:
            _mcp.tool = _mcp_tool_fields_decorator(_mcp.tool)
        except Exception:
            # A bridge object that refuses the rebinding keeps its original decorator.
            # Degrading to "no fields on named tools" is the status quo, not a regression.
            pass
        # ── T3: Session ────────────────────────────────────────────
        @_mcp.tool
        def open_session(session_title: str = "", project_path: str = ".", mode: str = "T1",
                         agent_id: str = "", force_new_session: bool = False,
                         apply_requested_title: bool = False) -> str:
            """Open or resume an agent-scoped project session."""
            resolved_agent_id = ((agent_id or "").strip()
                                 or (os.environ.get("COUNCIL_AGENT_ID") or "").strip())
            # A33-B: daemon-only transport -- see the long note on prepare_ca_session
            # below for why srv.ct here was the A33 root cause. The agent id is resolved
            # HERE on purpose: COUNCIL_AGENT_ID lives in THIS host's env, not the
            # daemon's, so it has to ride the payload.
            return _mcp_call_text("open_session", {
                "session_title": session_title, "project_path": project_path,
                "mode": mode, "agent_id": resolved_agent_id,
                "force_new_session": force_new_session,
                "apply_requested_title": apply_requested_title,
            })

        @_mcp.tool
        def log_native_event(action_type: str, tool_name: str, target: str,
                              duration_ms: int = 0, status: str = "ok",
                              reason: str = "", project_path: str = "") -> str:
            """Log one realtime native event into the session. Call immediately after native fallback for Rule C."""
            # A33-C: daemon-only transport. ct.log_native_event takes 16 params; this
            # wrapper exposes the first 7, so the payload names exactly those 7 and the
            # daemon keeps its own defaults for the rest.
            return _mcp_call_text("log_native_event", {
                "action_type": action_type, "tool_name": tool_name, "target": target,
                "duration_ms": duration_ms, "status": status, "reason": reason,
                "project_path": project_path,
            })


        @_mcp.tool
        def manage_agent(action: str, agent_id: str = "", target: str = "all",
                         persona: str = "", rule_file: str = "", rule_format: str = "markdown",
                         rule_template: str = "slim", mcp_file: str = "", mcp_format: str = "json",
                         mcp_key: str = "project-orchestrator", dry_run: bool = False,
                         mode: str = "", cleanup: bool = True) -> str:
            """R86: CA lifecycle — register/unregister agents, set_mode A|native (default A = [A]-first), bulk-write global-rule/MCP config. Preserves persona line."""
            # A33-C: daemon-only transport. The old call handed 11 POSITIONAL args to a
            # ct.manage_agent that takes 16, whose params 12-14 are
            # skill_name/skill_dir/skill_format -- only the current ordering kept that
            # correct. Keys, not positions.
            return _mcp_call_text("manage_agent", {
                "action": action, "agent_id": agent_id, "target": target,
                "persona": persona, "rule_file": rule_file, "rule_format": rule_format,
                "rule_template": rule_template, "mcp_file": mcp_file,
                "mcp_format": mcp_format, "mcp_key": mcp_key, "dry_run": dry_run,
                "mode": mode, "cleanup": cleanup,
            })

        @_mcp.tool
        def prepare_ca_session(project_path: str, mode: str = "T1", idea: str = "",
                               agent_id: str = "", host_session_id: str = "",
                               owner_claim_id: str = "") -> str:
            """Prepare, open/resume, and bootstrap an agent-scoped CA session."""
            canonical_agent_id = agent_id or os.environ.get("COUNCIL_AGENT_ID", "")
            # A33-B: reach the DAEMON, never srv.ct here. asyncio.run(srv.ct.<fn>()) ran
            # the tool inside this long-lived MCP host process, so a host that imported
            # its registry before a tool existed compared its own in-RAM registry with
            # the daemon's on-disk inventory while the envelope still carried the
            # daemon's generation -- that is how generation stopped proving runtime
            # (A33 root cause, closed at the producer by the provenance gate).
            # _mcp_call_text goes through srv.dispatch_tool_call(transport="mcp"), whose
            # non-T4 branch (orchestrator_server.py:15595) POSTs /api/call/<tool> to the
            # live daemon. Measured: prepare_ca_session is T3, open_session T1 -- neither
            # is T4, so both really leave this process. Do NOT "simplify" either one back
            # to srv.ct: that reintroduces a second registry answering as if it were the
            # daemon.
            return _mcp_call_text("prepare_ca_session", {
                "project_path": project_path, "mode": mode, "idea": idea,
                "agent_id": canonical_agent_id, "host_session_id": host_session_id,
                "owner_claim_id": owner_claim_id,
            })

        @_mcp.tool
        def get_rule_core_text(agent_id: str = "", force: bool = False) -> str:
            """Return canonical CORE_RULE text and metadata, guarded per agent."""
            # A33-C: daemon-only transport, and the agent id is resolved HERE for the
            # same reason as open_session. Measured: the per-agent fetch guard
            # (_RULE_CORE_FETCH_CACHE, code_tools.py:2472) is a MODULE GLOBAL keyed by
            # ORCHESTRATOR_AGENT_ID -- a var nothing in this repo ever sets -- so an
            # empty agent_id keys "default". Running here gave every host its own
            # private "default"; running in the daemon makes that bucket shared, and a
            # second agent would get already_fetched without ever receiving the rule
            # text. COUNCIL_AGENT_ID (orchestrator_server.py:8329 really does set it per
            # worker) keeps workers apart instead of collapsing them onto one bucket.
            return _mcp_call_text("get_rule_core_text", {
                "agent_id": ((agent_id or "").strip()
                             or (os.environ.get("COUNCIL_AGENT_ID") or "").strip()),
                "force": force,
            })

        @_mcp.tool
        def bootstrap_session(project_path: str = "", session_id: str = "",
                              agent_id: str = "", owner_claim_id: str = "",
                              verbosity: str = "terse", include_discovery: bool = False) -> str:
            """Return the session-scoped canonical lifecycle bootstrap."""
            return _mcp_guard_response(lambda: asyncio.run(
                srv.ct._bootstrap_with_cached_owner_claim(
                    project_path, session_id, agent_id, owner_claim_id,
                    claim_cache=_MCP_OWNER_CLAIMS, verbosity=verbosity,
                    include_discovery=include_discovery)))

        @_mcp.tool
        def begin_task(session_id: str, prompt: str, agent_id: str = "",
                       max_chars: int = 2000, owner_capability: str = "",
                       delegation_capability: str = "",
                       delegate_agent_id: str = "", owner_claim_id: str = "") -> str:
            """Create the authoritative task epoch for a real user prompt."""
            # A33-C: daemon-only transport. The old call handed 8 POSITIONAL args to
            # ct.begin_task; a keyword payload cannot shift silently if that signature
            # ever grows a parameter in the middle.
            return _mcp_call_text("begin_task", {
                "session_id": session_id, "prompt": prompt, "agent_id": agent_id,
                "max_chars": max_chars, "owner_capability": owner_capability,
                "delegation_capability": delegation_capability,
                "delegate_agent_id": delegate_agent_id, "owner_claim_id": owner_claim_id,
            })

        @_mcp.tool
        def get_task_checkpoint(session_id: str) -> str:
            """Return the active task checkpoint for compaction/recovery."""
            # A33-C: daemon-only transport -- see the long note on prepare_ca_session.
            return _mcp_call_text("get_task_checkpoint", {"session_id": session_id})

        def _mcp_response_text(resp) -> str:
            text = getattr(resp, "text", None)
            if text is not None:
                return text
            body = getattr(resp, "body", b"")
            if isinstance(body, bytes):
                return body.decode("utf-8", errors="replace")
            return str(body)

        def _mcp_guard_response(producer) -> str:
            # A28-P3: keep the whole probe result, not just the stamp. The
            # gate still takes a bare generation (None = do not compare), but
            # the response has to disclose where the stamp came from.
            probe = _mcp_probe_generation(srv)
            stale = _mcp_generation_gate(probe["generation"])
            if stale is not None:
                return json.dumps(stale, ensure_ascii=False)
            return _mcp_add_generation(producer(), probe)

        def _mcp_call_text(name: str, payload: dict | None = None) -> str:
            # A37 #1b: HTTP and MCP have to be usable EQUALLY. `fields=` is accepted on
            # EVERY [A] tool over HTTP, but none of the named MCP wrappers below ever
            # declared it -- so on this surface the daemon's own "pass fields= next time"
            # size hint was advice a caller physically could not take, and every answer came
            # back full-fat. The value rides a thread-local that the _mcp.tool decorator
            # sets (ONE place -- see _mcp_tool_with_fields) instead of 62 hand-edited
            # signatures: hand-duplicating one fact across 62 wrappers is exactly how this
            # bridge drifted from the daemon before.
            # An explicit payload["fields"] WINS -- call_tool(payload={...,"fields":...})
            # is the caller being specific, and must not be overridden by the wrapper arg.
            payload = dict(payload or {})
            _fields_now = _mcp_fields_current()
            if _fields_now and not payload.get("fields"):
                payload["fields"] = _fields_now
            return _mcp_guard_response(lambda: _mcp_response_text(
                srv._run_mcp_coro(srv.dispatch_tool_call(name, payload, transport="mcp"))))

        @_mcp.tool
        def tool_names(tier: str = "", name_contains: str = "", category: str = "",
                       limit: int = 0, suppress_hints: bool = False) -> str:
            """Compact MCP discovery. Filter by tier/name/category; call tool_schema before call_tool."""
            return _mcp_live_discovery("/api/tools/names", {
                "tier": tier, "name_contains": name_contains, "category": category,
                "limit": limit, "suppress_hints": suppress_hints})

        @_mcp.tool
        def tool_schema(name: str) -> str:
            """Return enriched schema for exactly one [A] tool."""
            return _mcp_live_discovery("/api/tools/" + urllib.parse.quote(name, safe="") + "/schema")

        @_mcp.tool
        def call_tool(name: str, payload: dict = None) -> str:
            """Call any [A] tool by name through the shared backend dispatcher."""
            if payload is None:
                payload = {}
            if isinstance(payload, str):
                payload = json.loads(payload) if payload.strip() else {}
            if not isinstance(payload, dict):
                return _mcp_guard_response(lambda: json.dumps({
                    "ok": False,
                    "error": "payload must be an object",
                    "error_name": "WRONG_ARGUMENTS",
                    "hint": "Pass payload as a JSON object/dict.",
                }, ensure_ascii=False))
            return _mcp_call_text(name, payload)

        @_mcp.tool
        def next_recommended_action(project_path: str) -> str:
            """Suggest next step based on current workflow state."""
            return _mcp_proxy_call("next_recommended_action", {"project_path": project_path})
        # ── T1: Read / Understand ───────────────────────────────────
        @_mcp.tool
        def get_project_summary(project_path: str) -> str:
            """Quick overview from INDEX: groups, hotspots, and entrypoints."""
            return _mcp_proxy_call("get_project_summary", {"project_path": project_path})

        @_mcp.tool
        def query_index(project_path: str, query: str = "", group: str = None, min_complexity: int = None,
                        min_loc: int = None, file_pattern: str = None) -> str:
            """Filter files in INDEX by group, complexity, LOC, or pattern."""
            return _mcp_proxy_call("query_index", {
                "project_path": project_path, "query": query, "group": group,
                "min_complexity": min_complexity, "min_loc": min_loc,
                "file_pattern": file_pattern,
            })

        @_mcp.tool
        def find_symbol(name: str, path: str = "", project_path: str = "", context_lines: int = 0) -> str:
            """Find function, class, or variable definitions in the codebase."""
            return _mcp_proxy_call("find_symbol", {
                "name": name, "path": path, "project_path": project_path,
                "context_lines": context_lines,
            })

        @_mcp.tool
        def read_file_range(file_path: str, start_line: int = 1,
                            end_line: int = None, project_path: str = None,
                            max_lines: int = 200, force_reseed: bool = False,
                            scope: str = "range", symbol: str = "",
                            section: str = "", related_query: str = "",
                            query: str = "", token_budget: int = 0,
                            small_file_threshold: int = 240,
                            include_next_ranges: bool = True,
                            content_hash: str = "",
                            force_refresh: bool = False) -> str:
            """Read one bounded line range or structural scope from a file."""
            return _mcp_proxy_call("read_file_range", {
                "file_path": file_path, "start_line": start_line,
                "end_line": end_line, "project_path": project_path,
                "max_lines": max_lines, "force_reseed": force_reseed,
                "scope": scope, "symbol": symbol, "section": section,
                "related_query": related_query, "query": query,
                "token_budget": token_budget,
                "small_file_threshold": small_file_threshold,
                "include_next_ranges": include_next_ranges,
                "content_hash": content_hash, "force_refresh": force_refresh,
            })

        @_mcp.tool
        def read_context_batch(project_path: str, requests: list,
                               token_budget: int = 6000,
                               max_items: int = 20,
                               small_file_threshold: int = 240,
                               include_next_ranges: bool = True) -> str:
            """Read multiple structural scopes under one total token budget."""
            return _mcp_proxy_call("read_context_batch", {
                "project_path": project_path, "requests": requests,
                "token_budget": token_budget, "max_items": max_items,
                "small_file_threshold": small_file_threshold,
                "include_next_ranges": include_next_ranges,
            })

        @_mcp.tool
        def architecture_context_pack(project_path: str, symbol: str = "",
                                      file_path: str = "", query: str = "",
                                      depth: int = 2, token_budget: int = 6000,
                                      max_files: int = 8,
                                      max_ranges: int = 20) -> str:
            """Build a graph-first readonly context pack without reindexing."""
            return _mcp_proxy_call("architecture_context_pack", {
                "project_path": project_path, "symbol": symbol,
                "file_path": file_path, "query": query, "depth": depth,
                "token_budget": token_budget, "max_files": max_files,
                "max_ranges": max_ranges,
            })

        @_mcp.tool
        def search_code(query: str, path: str = "",
                        project_path: str = "", file_pattern: str = "*") -> str:
            """Run a regex search across the codebase."""
            return _mcp_proxy_call("search_code", {
                "query": query, "path": path, "project_path": project_path,
                "file_pattern": file_pattern,
            })

        @_mcp.tool
        def get_file_symbols(file_path: str, project_path: str = "") -> str:
            """List all functions, classes, and variables in one file."""
            return _mcp_proxy_call("get_file_symbols", {
                "file_path": file_path, "project_path": project_path,
            })

        # ── T2: Write / Change ──────────────────────────────────────
        @_mcp.tool
        def write_fix(file_path: str, start_line: int,
                      end_line: int, new_content: str,
                      project_path: str = "", dry_run: bool = False,
                      content_encoding: str = "none") -> str:
            """Edit a code range by line number. Set dry_run=True to preview before writing."""
            return _mcp_proxy_call("write_fix", {
                "file_path": file_path, "start_line": start_line,
                "end_line": end_line, "new_content": new_content,
                "project_path": project_path, "dry_run": dry_run,
                "content_encoding": content_encoding,
            })

        @_mcp.tool
        def write_fix_html(file_path: str, start_line: int, end_line: int, new_content: str,
                            project_path: str = "", content_encoding: str = "none",
                            force: bool = False) -> str:
            """Edit an .html file with automatic delimiter checks. Set force=True to bypass the check."""
            return _mcp_proxy_call("write_fix_html", {
                "file_path": file_path, "start_line": start_line,
                "end_line": end_line, "new_content": new_content,
                "project_path": project_path, "content_encoding": content_encoding,
                "force": force,
            })

        @_mcp.tool
        def write_new_file(path: str, new_content: str = "", project_path: str = "",
                           content_encoding: str = "plain", dry_run: bool = False,
                           overwrite: bool = False) -> str:
            """Create a new file. Set dry_run=True to preview before applying."""
            # The daemon registry accepts ``new_content`` directly (alias of
            # ``content``); forward it verbatim so behaviour is unchanged.
            return _mcp_proxy_call("write_new_file", {
                "path": path,
                "new_content": new_content,
                "project_path": project_path,
                "content_encoding": content_encoding,
                "dry_run": dry_run,
                "overwrite": overwrite,
            })

        @_mcp.tool
        def apply_after_dry_run(project_path: str, dry_run_id: str,
                                confirm_hash: str = "",
                                confirm_warnings: bool = False) -> str:
            """Apply a previously previewed payload by dry_run_id."""
            return _mcp_proxy_call("apply_after_dry_run", {
                "project_path": project_path, "dry_run_id": dry_run_id,
                "confirm_hash": confirm_hash, "confirm_warnings": confirm_warnings,
            })

        @_mcp.tool
        def delete_file(file_path: str, project_path: str = "") -> str:
            """Delete a file. This cannot be undone."""
            return _mcp_proxy_call("delete_file", {
                "file_path": file_path, "project_path": project_path,
            })

        # ── T2: Validate / Verify ───────────────────────────────────
        @_mcp.tool
        def validate_file(file_path: str, lang: str = "auto", project_path: str = "") -> str:
            """Validate file syntax (Python/JS/HTML). project_path resolves relative file_path."""
            return _mcp_proxy_call("validate_file", {
                "file_path": file_path, "lang": lang, "project_path": project_path,
            })

        @_mcp.tool
        def validate_encoding(file_path: str, project_path: str = "",
                              expected_encoding: str = "utf-8",
                              language_hint: str = "") -> str:
            """Check UTF-8 encoding and mojibake issues after writing."""
            return _mcp_proxy_call("validate_encoding", {
                "file_path": file_path, "project_path": project_path,
                "expected_encoding": expected_encoding,
                "language_hint": language_hint,
            })

        @_mcp.tool
        def validate_symbols(file_path: str, project_path: str = "") -> str:
            """Post-write check: validate syntax and compare symbols against INDEX to catch missing definitions."""
            return _mcp_proxy_call("validate_symbols", {
                "file_path": file_path, "project_path": project_path,
            })

        @_mcp.tool
        def cache_delta(project_path: str) -> str:
            """Compare the filesystem against INDEX to detect modified, added, or deleted files."""
            return _mcp_proxy_call("cache_delta", {"project_path": project_path})

        @_mcp.tool
        def run_project_command(project_path: str, command, timeout: int = 120) -> str:
            """Run a bounded command in the project working directory."""
            return _mcp_proxy_call("run_project_command", {
                "project_path": project_path, "command": command, "timeout": timeout,
            }, timeout_override=timeout)

        @_mcp.tool
        def run_test_profile(project_path: str, profile: str = "unittest", timeout: int = 120) -> str:
            """Run a predefined test profile (unittest/pytest). Automatically detects the tests directory."""
            # Proxied + forwarded. The wrapper already declared a ``timeout`` and then
            # threw it away, so a profile run the caller sized at 600s was still cut
            # off at the 180s floor -- and it ran in the MCP host process instead of
            # the daemon that owns the test runtime.
            return _mcp_proxy_call("run_test_profile", {
                "project_path": project_path, "profile": profile, "timeout": timeout,
            }, timeout_override=timeout)

        # ── T2: Safety ──────────────────────────────────────────────
        @_mcp.tool
        def backup_project(project_path: str, backup_dir: str = None, force: bool = False,
                           keep_last_n: int = 2, timeout: int = 600) -> str:
            """Create a zip backup of the project."""
            # Proxied, not dispatched in-process: a backup WRITES a zip and the daemon
            # is the single writer. ``timeout`` is the same 600 the schema declares
            # (registry_data backup_project) -- the old wrapper accepted no timeout at
            # all, so a big-repo backup died at the 180s floor while its own schema
            # said it may take 600. Declaring it and forwarding it is the wiring.
            return _mcp_proxy_call("backup_project", {
                "project_path": project_path, "backup_dir": backup_dir,
                "force": force, "keep_last_n": keep_last_n, "timeout": timeout,
            }, timeout_override=timeout)

        # ── T3: Session management ──────────────────────────────────
        @_mcp.tool
        def close_session(project_path: str) -> str:
            """Close the active session and save a summary."""
            return _mcp_call_text("close_session", {"project_path": project_path})

        @_mcp.tool
        def rename_session(project_path: str, new_title: str) -> str:
            """Rename the active session."""
            return _mcp_call_text("rename_session", {
                "project_path": project_path, "new_title": new_title,
            })

        @_mcp.tool
        def list_sessions(project_path: str, limit: int = 10) -> str:
            """List the most recent sessions for the project."""
            return _mcp_call_text("list_sessions", {
                "project_path": project_path, "limit": limit,
            })

        @_mcp.tool
        def get_session_events(session_id: str = "", limit: int = 50, offset: int = 0,
                                project_path: str = "") -> str:
            """Read events from a session. project_path auto-resolves the active session when session_id is empty."""
            return _mcp_call_text("get_session_events", {
                "session_id": session_id, "limit": limit, "offset": offset,
                "project_path": project_path,
            })

        # ── T3: Workflow ────────────────────────────────────────────
        @_mcp.tool
        def workflow_status_registry(project_path: str) -> str:
            """Read workflow registry state (R43.2 C2: renamed from workflow_status to disambiguate from the R35 MD workflow)."""
            return _mcp_call_text("workflow_status_registry", {
                "project_path": project_path,
            })

        @_mcp.tool
        def set_workflow_mode(project_path: str, mode: str) -> str:
            """Update the workflow mode (ANALYZE/VERIFY/EXECUTE)."""
            return _mcp_call_text("set_workflow_mode", {
                "project_path": project_path, "mode": mode,
            })

        # ── T2: Cache / Index ───────────────────────────────────────
        @_mcp.tool
        def cache_status(project_path: str) -> str:
            """Check the project cache status."""
            return _mcp_call_text("cache_status", {"project_path": project_path})

        @_mcp.tool
        def generate_project_map(project_path: str) -> str:
            """Generate the project cache and INDEX.json."""
            return _mcp_call_text("generate_project_map", {"project_path": project_path})

        @_mcp.tool
        def refresh_project_map(project_path: str) -> str:
            """Refresh the cache and rebuild INDEX.json."""
            return _mcp_call_text("refresh_project_map", {"project_path": project_path})

        @_mcp.tool
        def backup_status(project_path: str) -> str:
            """Check the backup manifest."""
            return _mcp_call_text("backup_status", {"project_path": project_path})

        @_mcp.tool
        def list_structure(path: str = "", depth: int = 2, save_output: bool = True,
                           project_path: str = "", strict: bool = False) -> str:
            """List the project folder tree. project_path is an alias of path."""
            return _mcp_call_text("list_structure", {
                "path": path, "depth": depth, "save_output": save_output,
                "project_path": project_path, "strict": strict,
            })

        # ── T2: Build context ─────────────────────────────────────────────
        @_mcp.tool
        def create_benchmark_request(project_path: str, idea: str = "") -> str:
            """Create BENCHMARK_REQUEST.md without crawling."""
            return _mcp_call_text("create_benchmark_request", {
                "project_path": project_path, "idea": idea,
            })

        @_mcp.tool
        def create_build_context(project_path: str, idea: str = "", approved_inputs: str = "") -> str:
            """Create BUILD_CONTEXT.md from user-approved input."""
            return _mcp_call_text("create_build_context", {
                "project_path": project_path, "idea": idea,
                "approved_inputs": approved_inputs,
            })

        @_mcp.tool
        def get_usage_stats(project_path: str, last_n: int = 200,
                            include_http_log: bool = False) -> str:
            """APP_SESSION_LOG.jsonl + SQLite events (project-scoped)."""
            return _mcp_call_text("get_usage_stats", {
                "project_path": project_path, "last_n": last_n,
                "include_http_log": include_http_log,
            })

        @_mcp.tool
        def capability_status(project_path: str = "") -> str:
            """Bao capability parser/indexer hien co."""
            return _mcp_call_text("capability_status", {"project_path": project_path})

        @_mcp.tool
        def suggest_missing_capabilities(project_path: str = "") -> str:
            """Goi y thu vien parser can cai them."""
            return _mcp_call_text("suggest_missing_capabilities", {
                "project_path": project_path,
            })

        # ── Council dispatch (leader non-blocking) ───────────────────────
        # Leader (Claude Desktop via MCP) dispatches workers without blocking the
        # stdio pipe. Returns {job_id, status} immediately; poll with
        # get_toolcall_result(job_id). Avoids the 6000s block on the leader path.
        @_mcp.tool
        def council_message_async(sender: str = "user", to: str = "", text: str = "",
                                  body_file: str = "", timeout_sec: int = 3600,
                                  provider: str = "", model: str = "") -> str:
            """Non-blocking council dispatch through the authoritative HTTP daemon."""
            def _dispatch() -> str:
                job_id = f"mcp_{_uuid.uuid4().hex[:12]}"
                payload = {"sender": sender, "to": to, "timeout_sec": timeout_sec,
                           "provider": provider, "model": model,
                           "request_id": job_id}
                if body_file:
                    payload["body_file"] = body_file
                else:
                    payload["text"] = _inject_lessons(srv, text)
                return _mcp_call_text("council_message_async", payload)

            return _dispatch()

        @_mcp.tool
        def council_worker_model(agent: str = "", model: str = "",
                                 provider: str = "", action: str = "get") -> str:
            """Get or set a council worker's model. action=get|set.

            Set+get before council_message_async to route the right model.
            """
            return _mcp_call_text("council_worker_model",
                                  {"agent": agent, "model": model,
                                   "provider": provider, "action": action})

        # ── P1 CSLt: first-class council/mission pack (miss-path only) ─────
        # Prefer council_message_async for multi-worker fan-out (non-blocking
        # stdio). council_message is the sync path for short replies only.
        @_mcp.tool
        def council_message(sender: str = "user", to: str = "", text: str = "",
                            body_file: str = "", timeout_sec: int = 6000,
                            provider: str = "", model: str = "", queue: bool = True,
                            no_queue: bool = False, queue_wait_timeout_sec: int = 6000,
                            q_context: bool = False, q_zones: str = "") -> str:
            """Sync council dispatch; exactly one of text or body_file."""
            payload = {
                "sender": sender, "to": to, "timeout_sec": timeout_sec,
                "provider": provider, "model": model, "queue": queue,
                "no_queue": no_queue, "queue_wait_timeout_sec": queue_wait_timeout_sec,
                "q_context": q_context, "q_zones": q_zones,
            }
            if body_file:
                payload["body_file"] = body_file
            else:
                payload["text"] = _inject_lessons(srv, text)
            return _mcp_call_text("council_message", payload)

        @_mcp.tool
        def convene_council(project_path: str = "") -> str:
            """Activate the council (trigger: bat hoi dong Tr). Only field: project_path.

            Load leader-council-skill before any council coordination/dispatch.
            """
            return _mcp_call_text("convene_council", {"project_path": project_path})

        @_mcp.tool
        def council_tracker(open_file: bool = False, tail_lines: int = 80,
                            return_full: bool = False, save_output: bool = True) -> str:
            """Collect/read council live snapshot (broker dispatches + replies).

            Call FIRST when investigating council/broker/worker-dispatch issues.
            """
            return _mcp_call_text("council_tracker", {
                "open_file": open_file, "tail_lines": tail_lines,
                "return_full": return_full, "save_output": save_output,
            })

        @_mcp.tool
        def leader_bootstrap() -> str:
            """One-call leader bootstrap: roster, worker models, active missions, Q hint."""
            return _mcp_call_text("leader_bootstrap", {})

        @_mcp.tool
        def mission_status(mission_id: int = 0, verbosity: str = "summary") -> str:
            """Compact mission/task status. verbosity=full includes progress_ledger tail."""
            return _mcp_call_text("mission_status", {
                "mission_id": mission_id, "verbosity": verbosity,
            })

        @_mcp.tool
        def mission_next(mission_id: int = 0) -> str:
            """Next dependency-ready pending task for a mission, or null."""
            return _mcp_call_text("mission_next", {"mission_id": mission_id})

        @_mcp.tool
        def mission_dispatch(task_id: int = 0) -> str:
            """Verify/set worker model, write outbox/ledger, send via council send-async."""
            return _mcp_call_text("mission_dispatch", {"task_id": task_id})

        @_mcp.tool
        def mission_gate(task_id: int = 0, decision: str = "approve",
                         reason: str = "") -> str:
            """Human-gate: approve->pending, reject->skipped; appends human_gate ledger."""
            return _mcp_call_text("mission_gate", {
                "task_id": task_id, "decision": decision, "reason": reason,
            })

        @_mcp.tool
        def mission_create(title: str = "", tasks_json: str = "",
                           task_ledger_json: str = "{}", room_path: str = "",
                           created_by: str = "") -> str:
            """Create durable mission + idempotent tasks (each needs output_artifact_path)."""
            return _mcp_call_text("mission_create", {
                "title": title, "tasks_json": tasks_json,
                "task_ledger_json": task_ledger_json, "room_path": room_path,
                "created_by": created_by,
            })

        @_mcp.tool
        def mission_report(task_id: int = 0, attempt_token: str = "",
                           res_path: str = "", summary: str = "",
                           kind: str = "final") -> str:
            """Heartbeat or final report for a mission task (artifact-first gate)."""
            return _mcp_call_text("mission_report", {
                "task_id": task_id, "attempt_token": attempt_token,
                "res_path": res_path, "summary": summary, "kind": kind,
            })

        @_mcp.tool
        def cbm_council_context(project_path: str = "", name: str = "",
                                file_path: str = "", depth: int = 2) -> str:
            """Bounded advisory graph context pack for council dispatch (T1 fail-open)."""
            payload = {"project_path": project_path, "depth": depth}
            if name:
                payload["name"] = name
            if file_path:
                payload["file_path"] = file_path
            return _mcp_call_text("cbm_council_context", payload)

        @_mcp.tool
        def declare_mission(mission: str = "", context_hints: str = "",
                            project_path: str = "", top_skills: int = 5,
                            top_tools: int = 20, verbosity: str = "full") -> str:
            """Curate skill+tool subset for active session (T1). hints=csv or JSON list."""
            # A34: csv / JSON-list / real-list coercion now lives in ONE place.
            # Imported lazily because this module deliberately keeps no top-level
            # `orchestrator.*` import (see the late-binding note in the docstring).
            from orchestrator.core.params import csv_param

            hints = csv_param(context_hints)
            return _mcp_call_text("declare_mission", {
                "mission": mission, "context_hints": hints,
                "project_path": project_path, "top_skills": top_skills,
                "top_tools": top_tools, "verbosity": verbosity,
            })

        # Surface prune profiles (FULL=1 bypasses all pruning — Herm live default).
        # - default (FULL off): core-9 only; rest via call_tool
        # - ORCHESTRATOR_MCP_PROFILE=council: core-9 U first-class council/mission pack
        _full = os.environ.get("ORCHESTRATOR_MCP_FULL_SURFACE", "0") == "1"
        if not _full:
            _core_mcp_tools = {
                "get_rule_core_text",
                "open_session",
                "tool_names",
                "tool_schema",
                "call_tool",
                "log_native_event",
                "list_sessions",
                "council_message_async",
                "council_worker_model",
            }
            _profile = (os.environ.get("ORCHESTRATOR_MCP_PROFILE", "") or "").strip().lower()
            if _profile == "council":
                _core_mcp_tools |= {
                    "council_message",
                    "convene_council",
                    "council_tracker",
                    "leader_bootstrap",
                    "mission_status",
                    "mission_next",
                    "mission_dispatch",
                    "mission_gate",
                    "mission_create",
                    "mission_report",
                    "cbm_council_context",
                    "declare_mission",
                }
            try:
                _mcp.tools.methods = {
                    name: func for name, func in _mcp.tools.methods.items()
                    if name in _core_mcp_tools
                }
            except Exception:
                pass

        _mcp.stdio()

    except ImportError:
        print("zeromcp not installed. Run: pip install zeromcp")
    except Exception as exc:
        print(f"MCP error: {exc}")
