# pyright: reportArgumentType=false, reportMissingImports=false, reportOperatorIssue=false, reportPossiblyUnboundVariable=false, reportReturnType=false, reportUndefinedVariable=false
"""
Project Orchestrator — aiohttp REST Server
Serves the HTML UI and exposes deterministic project tools and log streaming.
Usage:
  python orchestrator_server.py          # HTTP mode (opens browser at localhost:8083)
  python orchestrator_server.py --mcp    # MCP stdio mode
"""
import asyncio
import base64
import concurrent.futures
import contextvars
import copy
import functools
import hashlib
import hmac
import inspect
import json
import logging
import math
import msvcrt
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aiohttp import web

# V15 R60 BOOTSTRAP: defensive __pycache__ invalidation BEFORE local module imports.
# Task Scheduler launch path (install_orchestrator_admin_task.ps1's registered
# action) runs pythonw.exe orchestrator_server.py DIRECTLY — bypassing
# start_orchestrator.ps1's step-4 __pycache__ delete. Stale .pyc can mask
# source edits when mtime detection fails (Windows FAT/NTFS edge cases, COW
# copies, file-watcher debounce, identical mtime preserved by editor). Running
# this BEFORE `import code_tools` guarantees the next imports recompile from
# .py source. Safe to delete: stdlib pycache lives in Python's lib dir, not
# here; only this project's .pyc are affected.
try:
    import shutil as _shutil_r60
    _pycache_r60 = Path(__file__).parent / "__pycache__"
    if _pycache_r60.exists():
        _shutil_r60.rmtree(_pycache_r60, ignore_errors=True)
except Exception as exc:
    logging.getLogger(__name__).warning("__pycache__ cleanup failed: %s", exc)

import code_tools as ct
import path_runtime
from orchestrator.core import errors as _legacy_errors
from orchestrator.council.identity import assert_unique_canonical_ids, canonical_id
from orchestrator.council.route_metrics import (
    append_measurement as _route_append_measurement,
)
from orchestrator.council.route_metrics import (
    build_measurement as _route_build_measurement,
)
from orchestrator.council.route_metrics import (
    parse_dispatch_metadata as _route_parse_dispatch_metadata,
)
from orchestrator.council.snapshots import (
    VersionedSnapshotCache,
    acquire_file_lock_until,
    read_jsonl_tail,
    read_tail_bytes,
)
from orchestrator.fileops.xroot import is_outside_project_root, xroot_check
from orchestrator.lifecycle.request_orchestrator_restart import controller_health
from orchestrator.lifecycle.restart_protocol import (
    DEFAULT_CONTROL_URL,
    BusySnapshotPublisher,
    reap_orphan_busy_snapshots,
)
from orchestrator.server.accounting import PostCallAccountingQueue
from orchestrator.server.audit_writer import (
    SESSION_EVENT_KIND,
    AuditRecord,
    DurableAuditWriter,
)
from orchestrator.server.executor_lanes import (
    ExecutorLaneError,
    executor_lane_status,
    run_in_executor_lane,
    shutdown_executor_lanes,
)
from orchestrator.server.fair_admission import (
    FairAdmissionError,
    reset_admission_identity,
    set_admission_identity,
)
from orchestrator.storage.jsonl_store import append_jsonl as _jsonl_append_locked

# ─── Paths & config ───────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
HTML_FILE = path_runtime.resolve_ui_html(_HERE)
# MCP stdio workers share the tree with the HTTP server; they must not hold
# orchestrator.log or RotatingFileHandler rollover hits WinError 32.
LOG_FILE = _HERE / ("orchestrator.mcp.log" if "--mcp" in sys.argv else "orchestrator.log")
LOG_TAIL_BYTES = int(os.environ.get("ORCHESTRATOR_LOG_TAIL_BYTES", str(1024 * 1024)))
CORE_RULE_FILE = _HERE / "examples" / "CORE_RULE.txt"
SERVER_PATH = str(Path(__file__).resolve())
PORT = int(os.environ.get("ORCHESTRATOR_PORT", "8083"))
SERVICE_NAME = "project-orchestrator"
SERVER_STARTED_AT = time.time()
SERVER_STARTED_MONOTONIC = time.monotonic()
SERVER_GENERATION = uuid.uuid4().hex[:12]
SERVER_GENERATION_FILE = Path(
    os.environ.get("ORCHESTRATOR_GENERATION_FILE")
    or (_HERE / ".orchestrator_cache" / "server_generation.json")
)
LIVEZ_PORT = int(os.environ.get("ORCHESTRATOR_LIVEZ_PORT", "8084"))
LIVEZ_PROTOCOL = 1
# Default ready preserves create_app() contract in tests/embedders. _run_server
# closes the gate before binding HTTP and reopens it after boot recovery.
SERVER_READY = True
SERVER_STARTUP_PHASE = "ready"
SERVER_STOPPING = False
SERVER_STARTUP_DEGRADED = False
SERVER_STARTUP_ERROR = ""
SERVER_STARTUP_RECOVERY: dict = {
    "status": "not_run",
    "duration_ms": 0.0,
    "sweep": {},
}
RESTART_BUSY_SNAPSHOT = _HERE / ".orchestrator_cache" / "restart_busy_snapshot.json"
RESTART_CONTROL_URL = os.environ.get("ORCHESTRATOR_RESTART_CONTROL_URL", DEFAULT_CONTROL_URL)
_RESTART_SNAPSHOT_PUBLISHER: BusySnapshotPublisher | None = None
# A08: last boot-time reaper summary for orphaned busy-snapshot temp files,
# surfaced via /health (fail-loud observability, 7-RULE #7).
_RESTART_SNAPSHOT_REAP: dict = {}
_RESTART_REQUEST_ID = ""


def _liveness_payload() -> dict:
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "pid": os.getpid(),
        "generation": SERVER_GENERATION,
        "started_at": SERVER_STARTED_AT,
        "livez_protocol": LIVEZ_PROTOCOL,
        # A39/PH-5: LIVE thread-liveness of the busy-snapshot publisher, served on
        # the lock-free /livez channel so the out-of-process restart guard can tell
        # "publisher thread blocked but alive" (captured_at frozen, but .alive True
        # -> a transient burst, safe to suppress the stale block) apart from
        # "publisher thread DEAD" (captured_at frozen FOREVER -> must block). This
        # MUST be sourced here rather than stamped into the snapshot: a dead thread
        # cannot stamp its own death. It MUST NOT be read from /health, which calls
        # executor_lane_status()/_LANES_LOCK — the very lock a busy handler holds
        # during the block — and would hang in exactly the window that matters.
        # Thread.is_alive() takes no lock, so this stays responsive during a block.
        "restart_busy_snapshot_alive": bool(
            _RESTART_SNAPSHOT_PUBLISHER and _RESTART_SNAPSHOT_PUBLISHER.alive
        ),
        # A45: alive is necessary but NOT sufficient. A publisher whose os.replace
        # is denied (Windows: a concurrent reader holding the target open) is alive
        # and publishing nothing — captured_at freezes exactly as if the thread had
        # died, while the field above still reads True and the guard suppresses the
        # staleness block. Publishing the last cycle's OUTCOME lets the guard tell
        # "blocked-but-alive, will catch up" (no error, the PH-2 case that must stay
        # suppressible) from "alive but its writes are not landing" (a real frozen
        # reading that must keep blocking). Counters lock only — never the publish
        # lock — so this stays responsive during a block, same as `alive`.
        "restart_busy_snapshot_publish": (
            _RESTART_SNAPSHOT_PUBLISHER.publish_health()
            if _RESTART_SNAPSHOT_PUBLISHER else {}
        ),
    }


class _LivenessHandler(BaseHTTPRequestHandler):
    """Thread-isolated liveness endpoint; never enters the asyncio loop."""

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/livez":
            self.send_error(404)
            return
        payload = json.dumps(_liveness_payload(), separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


def _start_liveness_listener() -> ThreadingHTTPServer:
    """Bind /livez on a daemon OS thread independent of aiohttp resources."""
    server = ThreadingHTTPServer(("127.0.0.1", LIVEZ_PORT), _LivenessHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="orchestrator-livez", daemon=True)
    thread.start()
    return server

# CAN-042: single canonical source — alias the shared set (was a divergent 11-entry
# subset that excluded .svn/.orchestrator_cache/.idea/.vscode).
SKIP_DIRS = ct.SKIP_DIRS
CODE_GROUPS = {'code', 'text'}

# --- Logging -----------------------------------------------------------------
# F7: RotatingFileHandler 50MB×5 replaces plain FileHandler to cap log size.
from logging.handlers import RotatingFileHandler as _RotatingFileHandler


class _SafeRotatingFileHandler(_RotatingFileHandler):
    """Rollover must not crash the HTTP server when another process still has the file."""

    def doRollover(self):
        try:
            super().doRollover()
        except PermissionError:
            pass

    def emit(self, record):
        try:
            super().emit(record)
        except PermissionError:
            self.handleError(record)


_file_handler = _SafeRotatingFileHandler(
    LOG_FILE, maxBytes=50 * 1024 * 1024, backupCount=5, encoding='utf-8', delay=True,
)
_file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))

# F7: separate access log for high-volume dashboard poll routes
# (/api/council/activity, /api/council/state, /api/council/events).
# These account for ~90% of request volume and would swamp the main log.
_ACCESS_LOG_FILE = _HERE / "orchestrator_access.log"
_access_file_handler = _SafeRotatingFileHandler(
    _ACCESS_LOG_FILE, maxBytes=50 * 1024 * 1024, backupCount=5, encoding='utf-8', delay=True,
)
_access_file_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
_access_logger = logging.getLogger("orchestrator.access")
_access_logger.setLevel(logging.INFO)
_access_logger.addHandler(_access_file_handler)
_access_logger.propagate = False  # don't bubble up to root → main log

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, logging.StreamHandler()])
logger = logging.getLogger(__name__)

# F7: route paths that are routed to the access log instead of main log.
_DASHBOARD_POLL_PATHS = frozenset({
    "/api/council/activity",
    "/api/council/state",
    "/api/council/events",
})


class _DashboardAccessLogger(web.AccessLogger):  # type: ignore[reportPrivateImportUsage]
    # F7: dashboard poll requests (~90% volume) go to orchestrator.access,
    # everything else keeps the default aiohttp.access -> main log path.
    def log(self, request, response, time):
        try:
            if request is not None and request.path in _DASHBOARD_POLL_PATHS:
                _access_logger.info(
                    '%s "%s %s" %s %s',
                    request.remote, request.method, request.path_qs,
                    response.status, response.body_length,
                )
                return
        except Exception:
            pass
        super().log(request, response, time)


_HTML_CACHE: str | None = None
_MCP_LOOP: asyncio.AbstractEventLoop | None = None
_MCP_LOOP_THREAD: threading.Thread | None = None
_MCP_LOOP_LOCK = threading.Lock()


def _ensure_mcp_loop() -> asyncio.AbstractEventLoop:
    """Return the persistent MCP event loop used for stateful async tools."""
    global _MCP_LOOP, _MCP_LOOP_THREAD
    with _MCP_LOOP_LOCK:
        if _MCP_LOOP and _MCP_LOOP.is_running():
            return _MCP_LOOP
        loop = asyncio.new_event_loop()

        def _run_loop() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(
            target=_run_loop,
            name="project-orchestrator-mcp-async-loop",
            daemon=True,
        )
        thread.start()
        _MCP_LOOP = loop
        _MCP_LOOP_THREAD = thread
        return loop


def _run_mcp_coro(coro):
    """Run an async MCP tool on one stable loop so Playwright state survives calls."""
    loop = _ensure_mcp_loop()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        raise RuntimeError("_run_mcp_coro cannot block on its own event loop")
    return asyncio.run_coroutine_threadsafe(coro, loop).result()

# --- CORS + auth helpers -----------------------------------------------------

# Allowlist for browser Origin header - localhost-only (port 8083 for fix/upgrade copy)
_ALLOWED_ORIGINS = {
    f'http://localhost:{PORT}',
    f'http://127.0.0.1:{PORT}',
    'http://localhost:8083',
    'http://127.0.0.1:8083',
    # Backward-compatible for older launchers/browser tabs that still use 8082.
    'http://localhost:8082',
    'http://127.0.0.1:8082',
    # 'null' removed: file:// pages must not bypass mutation gate
}

# Tools that change state — require localhost remote + allowlisted Origin (if any).
# run_test_profile is NOT here: it's a validate/test runner (read-only test execution),
# distinct from arbitrary command execution.
# Token gate decoupled from mutating semantics: only genuinely destructive tools need
# the token. Falls back to the full protected set if code_tools predates the split.
# FixA2407: ALIAS the code_tools set, never copy it. Git and cron tools are registered
# dynamically through _register_tool(token_required_set=_TOKEN_REQUIRED_TOOLS), so with a
# copy those names landed only in the server-local set: the published schema (built from
# this set) advertised requires_token=True for git_stage/git_unstage/git_commit while
# code_tools.TOKEN_REQUIRED_TOOLS never learned about them. CORE_RULE lists all three as
# token-required, so the schema was right and code_tools was the stale side — the exact
# disagreement test_can012_api_tools_flags_match_sets kept reporting on git_stage.
_TOKEN_REQUIRED_TOOLS = getattr(ct, "TOKEN_REQUIRED_TOOLS", None)
if not isinstance(_TOKEN_REQUIRED_TOOLS, set):
    # code_tools predates the protected/token split: fall back and publish the set back
    # so both sides still share one object.
    _TOKEN_REQUIRED_TOOLS = set(ct.PROTECTED_TOOL_NAMES)
    ct.TOKEN_REQUIRED_TOOLS = _TOKEN_REQUIRED_TOOLS
# Mutating semantics (origin gate + requires_session) = protected set PLUS every
# token-required tool, so arbitrary-script wrappers (run_ps1_script/run_node_script)
# get full mutating/origin/session semantics even if absent from PROTECTED_TOOL_NAMES.
_MUTATING_TOOLS = set(ct.PROTECTED_TOOL_NAMES) | _TOKEN_REQUIRED_TOOLS

# ─── Mutation token (Fix 9: even local non-Origin requests must auth) ─────────
# Token is generated once per server lifecycle and written to a localhost-readable
# file. Any local process (UI, CLI) reads the file to authenticate; remote
# attackers without filesystem access cannot.
import secrets as _secrets

_TOKEN_FILE = _HERE / ".orchestrator_cache" / "mutation_token.txt"
_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
EXPECTED_TOKEN = _secrets.token_urlsafe(32)
TOKEN_CREATED_AT = time.time()
TOKEN_TTL_MINUTES = int(os.environ.get("TEST_TOKEN_TTL_MINUTES", os.environ.get("ORCHESTRATOR_TOKEN_TTL_MINUTES", "25")))
# V15 R42: default-on auto token injection. Protected tools without a valid
# header use the server token; set ORCHESTRATOR_AUTO_TOKEN=0 for strict mode.
# Toggle at runtime via the auto_token_inject config endpoint.
AUTO_TOKEN_INJECT = os.environ.get('ORCHESTRATOR_AUTO_TOKEN', '1') == '1'
# A-AUTO-UPGRADE (2026-07-12): default-ON auto-write mode. Instead of bouncing
# the CA with ACTIVE_SESSION_REQUIRED / BACKUP_REQUIRED, the gates DO the step
# themselves (auto-open session; run backup_project, which skips if today's
# backup exists). Every auto-step is reported in the tool response. Token gate
# for destructive tools is NOT covered — that stays AUTO_TOKEN_INJECT/manual.
# Opt-out: set ORCHESTRATOR_AUTO_WRITE=0.
AUTO_WRITE_MODE = os.environ.get("ORCHESTRATOR_AUTO_WRITE", "1") == "1"

def _log_session_event_safe(request, tool_name: str, status: str, payload: dict,
                            agent_id: str | None = None) -> None:
    """Log audit events without breaking the handler if session logging fails."""
    try:
        import code_tools as _ct_at
        body_proj = ""
        try:
            body_proj = (request.headers.get("X-Project-Path") or "") if request else ""
        except Exception:
            pass
        norm = _ct_at._session_project_key(body_proj) if body_proj else None
        sid = _ct_at._get_active_session_id(norm, agent_id) if norm else None
        if sid:
            _ct_at._log_session_event(sid, tool_name, payload, {"ok": True}, 0, status, str(payload)[:200])
    except Exception:
        pass
try:
    _TOKEN_FILE.write_text(EXPECTED_TOKEN, encoding="utf-8")
except Exception:
    pass


def _token_is_expired(now: float = None) -> bool:
    """CAN-001: enforce the mutation-token TTL at USE time instead of only
    advertising it in /api/token metadata. True when the current lifecycle token
    is older than TOKEN_TTL_MINUTES. `now` is injectable for deterministic tests
    (no real sleep)."""
    if now is None:
        now = time.time()
    return now > (TOKEN_CREATED_AT + TOKEN_TTL_MINUTES * 60)


_TOKEN_ROTATE_LOCK = threading.Lock()


def _rotate_token() -> str:
    """CAN-001: issue a fresh lifecycle token + reset created_at and rewrite the
    token file, so an expired token is recoverable via GET /api/token without an
    [A] restart. Internal callers read EXPECTED_TOKEN at call time, so they pick
    up the new value automatically.

    W1_C1 fix (2026-07-11): rotation is serialized — two concurrent GET
    /api/token calls used to interleave EXPECTED_TOKEN / TOKEN_CREATED_AT /
    file writes, so a caller could receive a token that was already stale."""
    global EXPECTED_TOKEN, TOKEN_CREATED_AT
    with _TOKEN_ROTATE_LOCK:
        EXPECTED_TOKEN = _secrets.token_urlsafe(32)
        TOKEN_CREATED_AT = time.time()
        try:
            _TOKEN_FILE.write_text(EXPECTED_TOKEN, encoding="utf-8")
        except Exception:
            pass
        return EXPECTED_TOKEN


def _validate_mutation_token(token: str, now: float = None) -> str:
    """CAN-001/CAN-002 single source of truth for mutation-token validation.
    Returns '' when the token is valid AND unexpired, else the error_name:
      - 'MUTATION_TOKEN_REQUIRED' : missing / wrong token
      - 'MUTATION_TOKEN_EXPIRED'  : correct token but past its TTL
    AUTO_TOKEN_INJECT is intentionally NOT consulted here — dev-bypass is the
    caller's concern, so it can never silently mask an expired/mismatched token
    in this check."""
    if token != EXPECTED_TOKEN:
        return "MUTATION_TOKEN_REQUIRED"
    if _token_is_expired(now):
        return "MUTATION_TOKEN_EXPIRED"
    return ""


# ─── HTTP defense layer (CAN-003 SSE auth · CAN-004 rate limit · CAN-006 body) ──
# Body cap is a DoS backstop ONLY — writes are contractually unlimited (2026-07-17
# write-draft upgrade): 128MB default covers any realistic single-call write.
# Tune via env. Rate limit is a runaway/DoS guard, generous by default.
MAX_REQUEST_BODY_BYTES = int(os.environ.get("ORCHESTRATOR_MAX_BODY_BYTES", str(128 * 1024 * 1024)))
_RATE_LIMIT_MAX = int(os.environ.get("ORCHESTRATOR_RATE_LIMIT_MAX", "600"))
_RATE_LIMIT_WINDOW = float(os.environ.get("ORCHESTRATOR_RATE_LIMIT_WINDOW_SEC", "60"))
_rate_buckets: dict = {}
_DASHBOARD_RATE_LIMIT_MAX = int(os.environ.get("ORCHESTRATOR_DASHBOARD_RATE_LIMIT_MAX", "240"))
_DASHBOARD_RATE_LIMIT_WINDOW = float(os.environ.get("ORCHESTRATOR_DASHBOARD_RATE_LIMIT_WINDOW_SEC", "60"))
_dashboard_rate_buckets: dict = {}
_OTHER_RATE_LIMIT_MAX = int(os.environ.get("ORCHESTRATOR_OTHER_RATE_LIMIT_MAX", "1200"))
_OTHER_RATE_LIMIT_WINDOW = float(os.environ.get("ORCHESTRATOR_OTHER_RATE_LIMIT_WINDOW_SEC", "60"))
_other_rate_buckets: dict = {}
_api_call_latency_samples = deque(maxlen=512)
_api_call_latency_window_s = 300.0
_requests_in_flight = 0


def _sse_token_ok(request) -> bool:
    """CAN-003: an SSE subscriber must present a valid, unexpired token. EventSource
    cannot set headers, so the token is accepted via the X-Orchestrator-Token header
    OR a `?token=` query param. Reuses the CAN-001 validator (expiry enforced)."""
    tok = request.headers.get('X-Orchestrator-Token') or request.query.get('token', '')
    return _validate_mutation_token(tok) == ''


def _rate_limit_check(
    request, now: float = None, *, max_requests: int | None = None,
    window_seconds: float | None = None, buckets: dict | None = None,
):
    """CAN-004: token-bucket limiter keyed by client IP. On localhost request.remote
    is always 127.0.0.1, so the bucket is effectively global — exactly the runaway/DoS
    guard intended. Returns (allowed, retry_after_seconds). Disabled when max <= 0.
    `now` injectable for deterministic tests."""
    limit_max = _RATE_LIMIT_MAX if max_requests is None else int(max_requests)
    limit_window = _RATE_LIMIT_WINDOW if window_seconds is None else float(window_seconds)
    bucket = _rate_buckets if buckets is None else buckets
    if limit_max <= 0:
        return True, 0
    if now is None:
        now = time.time()
    key = (getattr(request, "remote", "") or "").split(":")[0] or "local"
    tokens, last = bucket.get(key, (float(limit_max), now))
    tokens = min(float(limit_max),
                 tokens + (now - last) * (limit_max / limit_window))
    if tokens < 1.0:
        bucket[key] = (tokens, now)
        retry = (1.0 - tokens) * (limit_window / limit_max)
        return False, max(1, int(retry) + 1)
    bucket[key] = (tokens - 1.0, now)
    return True, 0


def _record_api_call_latency(ms: float, *, now: float | None = None) -> None:
    if now is None:
        now = time.time()
    _api_call_latency_samples.append((now, float(ms)))


def _api_call_p95_ms(*, now: float | None = None) -> int:
    if now is None:
        now = time.time()
    cutoff = now - _api_call_latency_window_s
    samples = [ms for ts, ms in _api_call_latency_samples if ts >= cutoff]
    if not samples:
        return 0
    samples.sort()
    idx = max(0, math.ceil(len(samples) * 0.95) - 1)
    return int(round(samples[idx]))


@web.middleware
async def _http_defense_middleware(request, handler):
    """Startup gate, body size/rate defense, and load telemetry."""
    global _requests_in_flight
    started = time.time()
    is_api_call = request.path.startswith('/api/call/')
    _requests_in_flight += 1
    try:
        if SERVER_STOPPING and request.path not in {'/health', '/api/health'}:
            return web.json_response(
                {
                    "ok": False,
                    "error": "Orchestrator is stopping and no longer accepts new work",
                    "error_name": "ORCHESTRATOR_STOPPING",
                    "startup_phase": SERVER_STARTUP_PHASE,
                    "generation": SERVER_GENERATION,
                    "retryable": True,
                    "retry_after_seconds": 1,
                    "ca_next_action": "wait_for_new_orchestrator_generation",
                },
                status=503,
                headers=_cors(request.headers.get('Origin'), {'Retry-After': '1'}),
            )
        if not SERVER_READY and request.path not in {'/health', '/api/health', '/council'}:
            return web.json_response(
                {
                    "ok": False,
                    "error": "Orchestrator startup recovery is still running",
                    "error_name": "ORCHESTRATOR_STARTING",
                    "startup_phase": SERVER_STARTUP_PHASE,
                    "livez_protocol": LIVEZ_PROTOCOL,
                    "retry_after_seconds": 1,
                },
                status=503,
                headers=_cors(request.headers.get('Origin'), {'Retry-After': '1'}),
            )
        if request.path in _DASHBOARD_POLL_PATHS:
            allowed, retry_after = _rate_limit_check(
                request,
                max_requests=_DASHBOARD_RATE_LIMIT_MAX,
                window_seconds=_DASHBOARD_RATE_LIMIT_WINDOW,
                buckets=_dashboard_rate_buckets,
            )
            if not allowed:
                return web.json_response(
                    {"ok": False, "error": "Rate limit exceeded for dashboard poll",
                     "error_name": "DASHBOARD_RATE_LIMIT_EXCEEDED"},
                    status=429,
                    headers=_cors(request.headers.get('Origin'), {'Retry-After': str(retry_after)}),
                )
        elif is_api_call:
            cors = _cors(request.headers.get('Origin'))
            tool_name = request.match_info.get('tool_name', '')
            clen = request.content_length
            if clen is not None and clen > MAX_REQUEST_BODY_BYTES:
                return _error_response(
                    tool_name,
                    f"Request body too large ({clen} > {MAX_REQUEST_BODY_BYTES} bytes)",
                    413, cors, {},
                    error_name="REQUEST_BODY_TOO_LARGE",
                    hint="Writes are size-unlimited by contract; this is only the transport DoS "
                         "backstop. Raise env ORCHESTRATOR_MAX_BODY_BYTES for a legitimate "
                         "larger payload — do NOT split the write.",
                )
            allowed, retry_after = _rate_limit_check(request)
            if not allowed:
                resp = _error_response(
                    tool_name,
                    "Rate limit exceeded for /api/call",
                    429, cors, {},
                    error_name="RATE_LIMIT_EXCEEDED",
                    hint=f"Too many tool calls; retry after {retry_after}s. Tune via ORCHESTRATOR_RATE_LIMIT_MAX.",
                )
                try:
                    resp.headers['Retry-After'] = str(retry_after)
                except Exception:
                    pass
                return resp
        else:
            allowed, retry_after = _rate_limit_check(
                request,
                max_requests=_OTHER_RATE_LIMIT_MAX,
                window_seconds=_OTHER_RATE_LIMIT_WINDOW,
                buckets=_other_rate_buckets,
            )
            if not allowed:
                return web.json_response(
                    {"ok": False, "error": "Rate limit exceeded",
                     "error_name": "RATE_LIMIT_EXCEEDED"},
                    status=429,
                    headers=_cors(request.headers.get('Origin'), {'Retry-After': str(retry_after)}),
                )
        return await handler(request)
    finally:
        if is_api_call:
            _record_api_call_latency((time.time() - started) * 1000.0)
        _requests_in_flight = max(0, _requests_in_flight - 1)


# Tools allowed without an active session (bootstrap + read-only utility)
_NO_SESSION_REQUIRED = {
    'open_session', 'prepare_ca_session', 'list_sessions', 'get_session_events',
    'log_native', 'log_native_event', 'create_pending_audit', 'sync_pending_audit',
    'record_error_resolution',
    'get_usage_stats',
    'cache_status', 'workflow_status', 'capability_status',
    # V15 R68: [A] self-lifecycle tools operate on the orchestrator process itself,
    # NOT a project. Their schema accepts no project_path, so the session gate's
    # PROJECT_PATH_REQUIRED was a catch-22 (gate demands project_path the schema
    # rejects). Exempt them like the other session primitives.
    'restart_orchestrator_via_task', 'cancel_pending_restart',
    'restart_orchestrator_via_bat',
    # V15 R69: kill_process (pid/process_name) and desktop_app_focus (hwnd/title)
    # are global OS ops with NO project_path param — same catch-22. _NO_SESSION
    # (not _PROJECT_PATH_DEFAULTABLE) because injecting project_path='.' would
    # instead trip UNSUPPORTED_FIELD on their param-less signatures.
    'kill_process', 'desktop_app_focus',
    # V15 R71: desktop_window_state (maximize/restore/minimize a window by hwnd/title)
    # is a Win32 ShowWindow op on a window — no project. Same PROJECT_PATH_REQUIRED
    # catch-22 (surfaced in v6 Step 18). Exempt like desktop_app_focus.
    'desktop_window_state',
    # V15 R96 (R2 fix) + B5 (2026-08-16): stop_app kills by pid/process_name and has
    # no project_path in its schema, so requiring a project session is a real catch-22
    # (PROJECT_PATH_REQUIRED vs UNSUPPORTED_FIELD) -> keep the session exemption.
    # NOTE: the original R96 claim that stop_app was "already TOKEN-gated in code_tools"
    # was FALSE (the P5 registration block, incl. its token gate, was lost in the
    # 0071641 squash). B5 restored it: start_app + stop_app are now in
    # code_tools.PROTECTED_TOOL_NAMES and TOKEN_REQUIRED_TOOLS (they wrap
    # run_project_command / kill_process in-process, which bypass the HTTP token gate).
    # The session exemption below is the session gate only; the token gate is enforced
    # in code_tools, so this exemption does NOT weaken the token requirement.
    'stop_app',
    # V15 R93: native dialog tools mutate global Win32 UI state, not project files.
    # Their schemas intentionally have no project_path, so requiring a project
    # session creates a PROJECT_PATH_REQUIRED vs UNSUPPORTED_FIELD catch-22.
    'fill_folder_dialog', 'cancel_native_dialog',
    # V15 R76: workflow control ops are global-state (keyed by name) — let the workflow-editor UI
    # Restart/Goto-step/Activate buttons work from the browser without an open CA session.
    'workflow_reset', 'workflow_goto_step', 'workflow_activate',
}
_SKIP_SESSION_LOG = frozenset({
    "get_usage_stats", "list_sessions", "get_session_events",
    "close_session", "log_native_event", "record_error_resolution",
})
_ACCOUNTING_BODY_KEYS = (
    "project_path", "file_path", "path", "command", "command_encoding",
    "query", "name", "start_line", "end_line",
)


def _compact_accounting_body(body: dict) -> dict:
    compact: dict = {}
    for key in _ACCOUNTING_BODY_KEYS:
        if key not in body:
            continue
        value = body.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            compact[key] = value[:4096]
        elif isinstance(value, (int, float, bool)):
            compact[key] = value
        elif isinstance(value, (list, tuple)):
            compact[key] = [str(item)[:512] for item in value[:3]]
        else:
            compact[key] = str(value)[:512]
    return compact


def _compact_accounting_result(result, task_meta: dict) -> dict:
    compact: dict = {}
    if isinstance(result, dict):
        tests = result.get("tests")
        if isinstance(tests, dict):
            compact["tests"] = {"collected": tests.get("collected")}
        for key in ("stdout", "stderr"):
            if result.get(key) is not None:
                compact[key] = str(result.get(key) or "")[-400:]
        validation = result.get("validation")
        if isinstance(validation, dict) and validation.get("warnings"):
            compact["validation"] = {"warnings": True}
        for key in ("warnings", "warning"):
            if result.get(key):
                compact[key] = True
        if result.get("overall_status") is not None:
            compact["overall_status"] = str(result.get("overall_status"))[:80]
        if result.get("queue_wait_ms") is not None:
            compact["queue_wait_ms"] = result.get("queue_wait_ms")
    compact.update({
        "_host_session_id": task_meta.get("host_session_id") or None,
        "_task_epoch": task_meta.get("task_epoch"),
        "_prompt_hash": task_meta.get("prompt_hash") or None,
        "_task_agent_id": task_meta.get("agent_id") or None,
    })
    return compact


def _persist_post_call_accounting(record: dict) -> None:
    """Persist non-durable usage telemetry on the dedicated FIFO worker."""
    tool_name = str(record.get("tool_name") or "")
    body = dict(record.get("body") or {})
    status = str(record.get("status") or "error")
    duration_ms = int(record.get("duration_ms") or 0)
    error_msg = str(record.get("error_msg") or "")[:300]
    try:
        usage_ok = ct._log_usage(tool_name, body, status == "ok", duration_ms, error_msg)
        if usage_ok is False:
            raise RuntimeError("usage_jsonl_write_failed")
    except Exception as exc:
        raise RuntimeError(
            f"usage_jsonl:{type(exc).__name__}:{str(exc)[:160]}"
        ) from exc


def _persist_durable_session_event(record: AuditRecord) -> None:
    """Apply one durable session event; ACK only after the idempotent sink commits."""
    if record.kind != SESSION_EVENT_KIND:
        raise ValueError(f"unsupported durable audit kind: {record.kind!r}")
    payload = record.payload
    if not isinstance(payload, dict):
        raise TypeError("durable session-event payload must be an object")
    session_id = str(payload.get("session_id") or "").strip()
    tool_name = str(payload.get("tool_name") or "").strip()
    if not session_id or not tool_name:
        raise ValueError("durable session-event payload needs session_id and tool_name")
    event_id = ct._log_session_event(
        session_id,
        tool_name,
        dict(payload.get("body") or {}),
        dict(payload.get("result") or {}),
        int(payload.get("duration_ms") or 0),
        str(payload.get("status") or "error"),
        str(payload.get("error_msg") or "")[:300],
        audit_id=record.audit_id,
    )
    if event_id is None:
        raise RuntimeError(
            f"durable session-event sink did not commit audit_id={record.audit_id!r}"
        )


def _durable_audit_db_path() -> Path:
    """Keep the durable-audit outbox in the same process scope as sessions.db."""
    explicit = str(os.environ.get("ORCHESTRATOR_DURABLE_AUDIT_DB") or "").strip()
    if explicit:
        return Path(explicit)
    configured_sessions_db = str(os.environ.get("ORCHESTRATOR_SESSIONS_DB") or "").strip()
    if configured_sessions_db:
        return Path(configured_sessions_db).with_name("durable_audit.db")
    return _HERE / ".orchestrator_cache" / "durable_audit.db"


_DURABLE_AUDIT: DurableAuditWriter | None = None
_DURABLE_AUDIT_LOCK = threading.Lock()


def _get_durable_audit_writer(*, start: bool = False) -> DurableAuditWriter | None:
    """Lazily create the durable writer for HTTP; MCP must never own a session DB."""
    global _DURABLE_AUDIT
    if "--mcp" in sys.argv:
        return None
    writer = _DURABLE_AUDIT
    if writer is None:
        with _DURABLE_AUDIT_LOCK:
            writer = _DURABLE_AUDIT
            if writer is None:
                writer = DurableAuditWriter(
                    _durable_audit_db_path(),
                    _persist_durable_session_event,
                    capacity=max(
                        1, int(os.environ.get("ORCHESTRATOR_DURABLE_AUDIT_CAPACITY", "4096"))
                    ),
                    batch_size=max(
                        1, int(os.environ.get("ORCHESTRATOR_DURABLE_AUDIT_BATCH_SIZE", "64"))
                    ),
                    auto_start=False,
                )
                _DURABLE_AUDIT = writer
    if start and writer is not None:
        start_method = getattr(writer, "start", None)
        if callable(start_method):
            start_method()
    return writer
_DURABLE_METRICS_CACHE_WRITER = None
_DURABLE_METRICS_CACHE: dict = {}
_DURABLE_METRICS_CACHE_AT = 0.0
_DURABLE_METRICS_FUTURE = None
_DURABLE_METRICS_EXECUTOR = None
_DURABLE_METRICS_LOCK = threading.Lock()
_DURABLE_METRICS_TIMEOUT_SECONDS = 0.05
# How long a collected metrics dict stays a valid ANSWER, as opposed to how long
# /health is willing to wait for a new one. Without this the function submitted a
# fresh collection on every single request and then waited 50ms for it, so under
# load the answer was almost always the cached value reported as `metrics_stale`:
# measured on this box with 3 busy threads, 1 of 8 steady-state probes came back
# fresh, and raising the budget to 400ms only moved it to 5 of 10 while pushing p50
# latency from 125ms to 406ms -- the budget was never the problem. The sibling
# `_controller_health_for_health` has had a TTL for exactly this reason
# (`_CONTROLLER_HEALTH_TTL_SECONDS`); this path simply never grew one. 2s is well
# under any watchdog interval, and `metrics_age_ms` already reports the real age, so
# a caller can still tell how old the answer is.
_DURABLE_METRICS_TTL_SECONDS = 2.0
# There is deliberately NO acquisition budget any more: see
# `_durable_writer_for_health`, which does not wait for anything at all.


def _durable_metrics_executor():
    global _DURABLE_METRICS_EXECUTOR
    with _DURABLE_METRICS_LOCK:
        if _DURABLE_METRICS_EXECUTOR is None:
            _DURABLE_METRICS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="durable-health-metrics"
            )
        return _DURABLE_METRICS_EXECUTOR


async def _durable_writer_for_health():
    """Look the durable writer up WITHOUT waiting for anything.

    Two measured failures, one after the other, and the second is why this now
    waits for nothing rather than waiting less.

    (1) The ">120s /health hang while MCP read/write stayed alive" report of
    2026-08-20. This used to call `_get_durable_audit_writer(start=True)` inline.
    `DurableAuditWriter.start()` takes `self._condition`, and `wait_until_idle`
    (the accounting flush and the shutdown drain, both up to 10s) re-acquires that
    condition around every `pending_count()`. Probed with the condition held 2.5s:
    `start()` blocked 2.500s and the loop tick gap went 0.05s -> 2.531s, so every
    route on the loop froze -- which is exactly why /health hung while MCP, a
    separate transport in a separate process, kept answering.

    (2) The first fix moved that call to `asyncio.to_thread` under a 50ms budget.
    It did stop the hang, and it was still wrong: measured on this box with only 3
    busy threads, a COLD `to_thread` cost 3032ms and a warm one 31-187ms, all of it
    thread-pool scheduling rather than contention. Under any real load /health
    would therefore have reported `metrics_stale` permanently -- a watchdog could
    no longer tell a contended audit DB from a busy machine, which is the same
    blind spot as the hang wearing different clothes.

    So the acquisition is not bounded, it is REMOVED. Starting a background worker
    thread is a mutation, and a health probe has no business performing one: the
    three callers that genuinely need a running writer
    (`_stage_durable_session_event`, `_flush_post_call_accounting`, and
    `_post_call_accounting_startup`, which runs at HTTP boot) already pass
    `start=True`. Reading the module global is a plain load with no lock, no
    handoff and no deadline to miss, so contention can no longer be misread as
    absence. Real contention is still caught one step later, where it actually
    lives: `writer.metrics()` takes the same condition and is already bounded off
    the loop by `_durable_metrics_for_health`.
    """
    if "--mcp" in sys.argv:
        return None
    return _DURABLE_AUDIT


async def _durable_metrics_for_health() -> tuple[dict, str | None]:
    """Keep SQLite work off the event loop and bound health latency."""
    global _DURABLE_METRICS_CACHE_WRITER, _DURABLE_METRICS_CACHE
    global _DURABLE_METRICS_CACHE_AT, _DURABLE_METRICS_FUTURE
    writer = await _durable_writer_for_health()
    if writer is None:
        # None is now one fact only: no writer object exists yet. It can no longer
        # mean "contended", because nothing above waits -- and conflating the two
        # was the bug in the previous version of this branch, which reported a busy
        # thread pool as a contended audit DB.
        not_started = "--mcp" not in sys.argv
        stale = dict(_DURABLE_METRICS_CACHE) if not_started else {}
        stale.update({
            "worker_alive": False, "admission_open": False,
            "metrics_stale": bool(not_started),
            "metrics_age_ms": (
                max(0, int((time.monotonic() - _DURABLE_METRICS_CACHE_AT) * 1000))
                if not_started and _DURABLE_METRICS_CACHE_AT else None
            ),
        })
        return (
            stale,
            "durable_audit_not_started"
            if not_started else "durable_audit_disabled_in_mcp",
        )
    if (_DURABLE_METRICS_CACHE_WRITER is not writer
            and _DURABLE_METRICS_FUTURE is not None
            and not _DURABLE_METRICS_FUTURE.done()):
        stale = dict(_DURABLE_METRICS_CACHE)
        stale.update({"metrics_stale": True, "metrics_age_ms": None})
        return stale, "TimeoutError: metrics collection already in flight"
    if _DURABLE_METRICS_CACHE_WRITER is not writer:
        _DURABLE_METRICS_CACHE_WRITER = writer
        _DURABLE_METRICS_CACHE = {}
        _DURABLE_METRICS_CACHE_AT = 0.0
        _DURABLE_METRICS_FUTURE = None

    def _completed(future) -> None:
        global _DURABLE_METRICS_CACHE, _DURABLE_METRICS_CACHE_AT
        global _DURABLE_METRICS_FUTURE
        if _DURABLE_METRICS_FUTURE is not future:
            return
        try:
            metrics = future.result()
            if isinstance(metrics, dict):
                _DURABLE_METRICS_CACHE = dict(metrics)
                _DURABLE_METRICS_CACHE_AT = time.monotonic()
        except Exception:
            pass
        finally:
            if _DURABLE_METRICS_FUTURE is future:
                _DURABLE_METRICS_FUTURE = None

    # A recent answer IS the answer. Refreshing in the background and serving the
    # cached dict keeps /health honest (age is reported) without spending the budget
    # on a thread handoff that Windows schedules in ~16ms quanta.
    cache_age = (
        time.monotonic() - _DURABLE_METRICS_CACHE_AT
        if _DURABLE_METRICS_CACHE_AT else None
    )
    if (_DURABLE_METRICS_CACHE and cache_age is not None
            and cache_age <= _DURABLE_METRICS_TTL_SECONDS):
        # Refresh only once the answer is half-expired. Submitting on every cache
        # hit would still be one collection per request -- off the critical path,
        # but the same per-request thread handoff the TTL exists to remove, and a
        # /health poller would keep the executor permanently busy.
        if (_DURABLE_METRICS_FUTURE is None
                and cache_age >= _DURABLE_METRICS_TTL_SECONDS / 2):
            refresh = _durable_metrics_executor().submit(writer.metrics)
            _DURABLE_METRICS_FUTURE = refresh
            refresh.add_done_callback(_completed)
        fresh = dict(_DURABLE_METRICS_CACHE)
        fresh.update({
            "metrics_stale": False,
            "metrics_age_ms": max(0, int(cache_age * 1000)),
        })
        return fresh, None
    future = _DURABLE_METRICS_FUTURE
    if future is None:
        future = _durable_metrics_executor().submit(writer.metrics)
        _DURABLE_METRICS_FUTURE = future
        future.add_done_callback(_completed)
    deadline = time.monotonic() + _DURABLE_METRICS_TIMEOUT_SECONDS
    while not future.done() and time.monotonic() < deadline:
        await asyncio.sleep(min(0.005, max(0.0, deadline - time.monotonic())))
    try:
        if not future.done():
            # `str(asyncio.TimeoutError())` is the EMPTY string, so the bare raise
            # here reached /health as the literal text "TimeoutError: " -- a field
            # that names an exception class and then says nothing. Measured under
            # load this is the common answer, not the rare one, so it is the text a
            # watchdog reads most often: it has to say what timed out and by how
            # much, or "stale" is indistinguishable from "broken".
            raise asyncio.TimeoutError(
                "durable audit metrics did not return within %dms (Windows sleep "
                "granularity is ~16ms, so a busy box exceeds this budget while the "
                "background collection keeps running); serving the cached value"
                % int(_DURABLE_METRICS_TIMEOUT_SECONDS * 1000)
            )
        metrics = future.result()
    except Exception as exc:
        stale = dict(_DURABLE_METRICS_CACHE)
        stale["metrics_stale"] = True
        stale["metrics_age_ms"] = (
            max(0, int((time.monotonic() - _DURABLE_METRICS_CACHE_AT) * 1000))
            if _DURABLE_METRICS_CACHE_AT else None
        )
        return stale, f"{type(exc).__name__}: {str(exc)[:240]}"
    result = dict(metrics)
    result.update({"metrics_stale": False, "metrics_age_ms": 0})
    return result, None


_CONTROLLER_HEALTH_CACHE: dict = {}
_CONTROLLER_HEALTH_CACHE_AT = 0.0
_CONTROLLER_HEALTH_FUTURE = None
# What the cached answer is an answer ABOUT: (url, probe function). Mirrors
# `_DURABLE_METRICS_CACHE_WRITER`, which invalidates on writer identity for the
# same reason -- a cached value produced by a different probe, or about a
# different URL, is not a stale answer to this question, it is an answer to
# another one. Without this the cache would survive an
# ORCHESTRATOR_RESTART_CONTROL_URL change and keep reporting the old port.
_CONTROLLER_HEALTH_CACHE_KEY = None
_CONTROLLER_HEALTH_TTL_SECONDS = 5.0
# Only the FIRST probe is waited on, and only this long. Steady state -- which is
# every request after the first -- costs nothing at all, which is where the 110ms
# went. Bounding the first call rather than skipping it keeps /health's contract
# intact: the very first response carries a real controller answer (including a
# normalised exception) instead of a placeholder, and a caller cannot tell the
# difference between "never probed" and "probed, came back empty".
_CONTROLLER_HEALTH_FIRST_WAIT_SECONDS = 0.05
# The PROBE's budget, not the caller's. It runs on the executor below and
# /health never waits for it, so this only has to be long enough to let the OS
# finish answering. 0.1s was not, and the reason is not the one the docstring
# below assumed. Measured 2026-09-09 on this host, connecting to a bound-then-
# released 127.0.0.1 port with nothing listening:
#
#     raw connect, timeout 0.1  ->  112ms  TimeoutError
#     raw connect, timeout 0.6  ->  609ms  TimeoutError
#     raw connect, timeout 3.0  -> 2020ms  ConnectionRefusedError WinError 10061
#     via `localhost` (dual stack, ::1 then 127.0.0.1)
#                     timeout 3.0 -> 4074ms ConnectionRefusedError
#
# So the refusal takes ~2.0s here, not ~110ms: Windows retransmits the SYN and
# the RST only surfaces after the retry window. Any budget under that returns
# `URLError: <urlopen error timed out>` with `connection_refused: false`, which
# reads as "listening but hung" about a port with nothing on it at all. The
# number meant to protect /health latency was corrupting /health's answer
# instead, and it protected nothing, because the wait is already off-loop.
#
# 2.5s clears the measured 2.02s and stays under the 5s cache TTL, so a down
# controller costs one sleeping thread for 2.5s per 5s and no /health latency.
# A `localhost` control URL would still exceed this (~4.1s, two address
# families); that case is not silently mislabelled -- `controller_health`
# reports `classification: "connect_timeout"` with `listener_present: None`,
# which says "no answer yet", not "nothing is there".
_CONTROLLER_HEALTH_PROBE_TIMEOUT_SECONDS = 2.5
_CONTROLLER_HEALTH_EXECUTOR = None


def _controller_health_executor():
    """A worker of its own, NOT the durable-metrics one.

    Both probes are single-worker by design -- one in flight is all either needs
    -- but sharing ONE worker between them couples two unrelated timeouts: a
    controller probe stalling on a refused connect would sit in front of the
    queued durable-metrics job, which has a 50ms budget and would then report
    `metrics_stale` for a reason that has nothing to do with SQLite. Two workers
    cost one idle thread and remove the interference entirely.

    This separation is what makes the probe budget affordable. A refused
    loopback connect measures ~2.02s on this host, not the ~110ms this file used
    to claim, so the probe must be allowed ~2.5s to get a truthful answer. It can
    be, precisely because it stalls its own dedicated thread and nothing else's.
    """
    global _CONTROLLER_HEALTH_EXECUTOR
    with _DURABLE_METRICS_LOCK:
        if _CONTROLLER_HEALTH_EXECUTOR is None:
            _CONTROLLER_HEALTH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="restart-controller-probe"
            )
        return _CONTROLLER_HEALTH_EXECUTOR


async def _controller_health_for_health(url: str) -> dict:
    """`controller_health` off the event loop, cached, never blocking /health.

    `controller_health` is a synchronous `urllib.request.urlopen`. Awaiting it
    inline made /health block the whole loop for as long as the socket took --
    and the expensive case is the COMMON one: when the restart controller on
    :8085 is not listening, every single request pays the full refusal latency,
    forever. Moving it off-loop was right and stays right.

    The NUMBER this docstring used to quote was not. It said "Windows takes
    ~110ms to refuse the connection", citing 0.109s of a 0.125s /health. That
    0.109s was the probe's own 0.1s deadline expiring, not the OS answering:
    re-measured 2026-09-09 against a closed 127.0.0.1 port, the refusal needs
    ~2.02s (Windows retransmits the SYN before surfacing the RST), and a 0.1s
    connect reports `TimeoutError` at 112ms every time. Reading that 112ms as
    "the refusal is fast" is what set the probe budget below the refusal and
    made a dead port report as a hung one. See
    `_CONTROLLER_HEALTH_PROBE_TIMEOUT_SECONDS` for the full measurement.

    That is the exact failure `_durable_metrics_for_health` was written to
    prevent for SQLite, and the same shape of fix applies: hand the blocking call
    to the executor, answer from cache, and let the result land later. The
    difference is the TTL. Durable metrics are a queue depth and go stale in
    milliseconds; whether a control port is listening changes only when someone
    restarts the daemon, so a 5s cache costs nothing a reader would notice and
    turns a per-request stall into one background probe per 5 seconds.

    A staleness marker rather than a silent cache: `probe_age_ms` says how old
    the answer is, and `probe_pending` says a refresh is in flight. A health
    endpoint that quietly reports a five-second-old fact as current is how a
    reader concludes the controller is up when it went down four seconds ago.
    """
    global _CONTROLLER_HEALTH_CACHE, _CONTROLLER_HEALTH_CACHE_AT
    global _CONTROLLER_HEALTH_FUTURE, _CONTROLLER_HEALTH_CACHE_KEY
    now = time.monotonic()
    key = (url, controller_health)
    if _CONTROLLER_HEALTH_CACHE_KEY != key:
        _CONTROLLER_HEALTH_CACHE_KEY = key
        _CONTROLLER_HEALTH_CACHE = {}
        _CONTROLLER_HEALTH_CACHE_AT = 0.0
        _CONTROLLER_HEALTH_FUTURE = None
    future = _CONTROLLER_HEALTH_FUTURE
    if future is not None and future.done():
        try:
            result = future.result()
            if isinstance(result, dict):
                _CONTROLLER_HEALTH_CACHE = dict(result)
                _CONTROLLER_HEALTH_CACHE_AT = now
        except Exception as exc:
            _CONTROLLER_HEALTH_CACHE = {
                "configured": True, "reachable": False, "url": url,
                "error_type": type(exc).__name__, "error_message": str(exc)[:240],
            }
            _CONTROLLER_HEALTH_CACHE_AT = now
        future = _CONTROLLER_HEALTH_FUTURE = None
    fresh = (_CONTROLLER_HEALTH_CACHE_AT
             and now - _CONTROLLER_HEALTH_CACHE_AT < _CONTROLLER_HEALTH_TTL_SECONDS)
    if future is None and not fresh:
        future = _controller_health_executor().submit(
            controller_health, url, _CONTROLLER_HEALTH_PROBE_TIMEOUT_SECONDS)
        _CONTROLLER_HEALTH_FUTURE = future
    if not _CONTROLLER_HEALTH_CACHE_AT and future is not None:
        # First probe ever: give it a bounded moment, the same way
        # `_durable_metrics_for_health` bounds its own wait, so a healthy
        # controller answers the first /health truthfully instead of "unknown".
        deadline = time.monotonic() + _CONTROLLER_HEALTH_FIRST_WAIT_SECONDS
        while not future.done() and time.monotonic() < deadline:
            await asyncio.sleep(min(0.005, max(0.0, deadline - time.monotonic())))
        if future.done():
            try:
                result = future.result()
                if isinstance(result, dict):
                    _CONTROLLER_HEALTH_CACHE = dict(result)
                    _CONTROLLER_HEALTH_CACHE_AT = time.monotonic()
            except Exception as exc:
                _CONTROLLER_HEALTH_CACHE = {
                    "configured": True, "reachable": False, "url": url,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:240],
                }
                _CONTROLLER_HEALTH_CACHE_AT = time.monotonic()
            _CONTROLLER_HEALTH_FUTURE = None
    if not _CONTROLLER_HEALTH_CACHE_AT:
        # First ever call: there is nothing to serve from cache, and reporting a
        # controller as unreachable when it was never probed would be a lie in
        # the direction that triggers a restart. Say "not yet known" instead.
        return {"configured": True, "reachable": None, "url": url,
                "probe_pending": True, "probe_age_ms": None}
    out = dict(_CONTROLLER_HEALTH_CACHE)
    out["probe_age_ms"] = max(0, int((now - _CONTROLLER_HEALTH_CACHE_AT) * 1000))
    out["probe_pending"] = _CONTROLLER_HEALTH_FUTURE is not None
    return out


def _durable_metrics_error_is_transient(metrics_error: str | None) -> bool:
    """50ms health-metrics TimeoutError is collection lag, not writer death."""
    if not metrics_error:
        return False
    text = str(metrics_error)
    return text.startswith("TimeoutError") or "already in flight" in text


def _durable_audit_ok_from_metrics(metrics: dict, metrics_error: str | None) -> bool:
    """Health ok when the writer is alive; apply/metrics timeouts stay diagnostic."""
    data = metrics if isinstance(metrics, dict) else {}
    worker_alive = bool(data.get("worker_alive"))
    admission_open = bool(data.get("admission_open", False))
    pending = max(0, int(data.get("pending") or 0))
    last_kind = str(data.get("last_error_kind") or "")
    last_error = data.get("last_error")
    fatal_kinds = {"storage", "schema", "runtime"}
    if last_kind in fatal_kinds and last_error:
        return False
    if _durable_metrics_error_is_transient(metrics_error):
        return worker_alive and admission_open
    apply_timeout = last_kind == "apply_timeout" or (
        isinstance(last_error, str) and "AuditApplyTimeout" in last_error
    )
    if apply_timeout:
        return worker_alive and admission_open
    # A transient sink failure is actionable while rows remain pending. Once
    # the outbox is empty, a later successful drain proves recovery; do not
    # keep health red because the historical sink error is still cached.
    if last_kind == "sink" and pending == 0:
        return metrics_error is None and admission_open and worker_alive
    return (
        metrics_error is None
        and not last_error
        and admission_open
        and worker_alive
    )


def _durable_audit_id(operation_id: str) -> str:
    raw = str(operation_id or "").strip() or uuid.uuid4().hex
    if len(raw) <= 512:
        return raw
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stage_durable_session_event(
    audit_id: str,
    tool_name: str,
    body: dict,
    result: dict,
    duration_ms: int,
    status: str,
    error_msg: str,
    task_meta: dict,
    admitted_session_id: str = "",
):
    """Capture session identity now, then durably stage a replay-safe compact event."""
    if "--mcp" in sys.argv:
        # P2: MCP keeps T4 state local but never resolves session identity or opens a DB.
        return None
    if tool_name in _SKIP_SESSION_LOG:
        return None

    result_dict = dict(result or {}) if isinstance(result, dict) else {}
    session_id = str(
        admitted_session_id or result_dict.get("session_id") or ""
    ).strip()
    project = (
        body.get("project_path")
        or ct.find_project_root(body.get("file_path", body.get("path", "")))
        or _resolve_project_from_page_id(body.get("page_id", ""))
        or _resolve_project_from_session_id(body.get("session_id", ""))
        or ""
    )
    if not session_id and project:
        project_key = ct._session_project_key(project)
        session_id = str(
            ct._get_active_session_id(
                project_key, task_meta.get("agent_id") or None
            )
            or ""
        )
    if not session_id:
        if (
            project
            and tool_name in _MUTATING_TOOLS
            and tool_name not in _NO_SESSION_REQUIRED
        ):
            logger.warning(
                "Mutating tool '%s' completed without a capturable session for project '%s'.",
                tool_name,
                project,
            )
        return None

    payload = {
        "session_id": session_id,
        "tool_name": tool_name,
        "body": _compact_accounting_body(body),
        "result": _compact_accounting_result(result_dict, task_meta),
        "duration_ms": int(duration_ms or 0),
        "status": str(status or "error"),
        "error_msg": str(error_msg or "")[:300],
    }
    writer = _get_durable_audit_writer(start=True)
    if writer is None:
        raise RuntimeError("durable audit writer unavailable outside HTTP daemon")
    return writer.submit(
        _durable_audit_id(audit_id),
        SESSION_EVENT_KIND,
        payload,
    )


_POST_CALL_ACCOUNTING = PostCallAccountingQueue(
    _persist_post_call_accounting,
    capacity=max(1, int(os.environ.get("ORCHESTRATOR_ACCOUNTING_QUEUE_CAPACITY", "4096"))),
    worker_name="orchestrator-accounting",
    logger=logger,
)


async def _flush_post_call_accounting(timeout: float = 10.0) -> dict:
    """Drain durable audit first, then telemetry, within one total deadline."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    durable_error = None
    writer = _get_durable_audit_writer(start=True)
    if writer is None:
        durable_drained = True
        durable_error = "durable_audit_disabled_in_mcp"
    else:
        try:
            durable_drained = await asyncio.to_thread(
                writer.wait_until_idle,
                max(0.0, deadline - time.monotonic()),
            )
        except Exception as exc:
            durable_drained = False
            durable_error = f"{type(exc).__name__}: {str(exc)[:240]}"
    report = await asyncio.to_thread(
        _POST_CALL_ACCOUNTING.flush,
        max(0.0, deadline - time.monotonic()),
    )
    report["durable_drained"] = bool(durable_drained)
    report["durable_error"] = durable_error
    try:
        report["durable_audit"] = (
            writer.metrics() if writer is not None
            else {"worker_alive": False, "admission_open": False}
        )
    except Exception as exc:
        report["durable_audit"] = {
            "metrics_error": f"{type(exc).__name__}: {str(exc)[:240]}"
        }
        report["durable_audit"] = {
            "metrics_error": f"{type(exc).__name__}: {str(exc)[:240]}"
        }
    return report


async def _post_call_accounting_startup(_app) -> None:
    writer = _get_durable_audit_writer(start=True)
    if writer is None:
        logger.critical("Durable audit writer unavailable in HTTP startup")
    if not _POST_CALL_ACCOUNTING.start():
        logger.critical("Post-call accounting lane failed to start")
    if not _POST_CALL_ACCOUNTING.start():
        logger.critical("Post-call accounting lane failed to start")


async def _durable_audit_cleanup(_app) -> None:
    writer = _DURABLE_AUDIT
    if writer is None:
        return
    try:
        drained = await asyncio.to_thread(writer.wait_until_idle, 10.0)
    except Exception as exc:
        logger.critical("Durable audit cleanup failed: %s", exc)
        return
    if not drained:
        try:
            metrics = writer.metrics()
        except Exception:
            metrics = {}
        logger.critical(
            "Durable audit cleanup timed out pending=%s last_error=%s",
            metrics.get("pending"),
            metrics.get("last_error"),
        )


async def _durable_metrics_cleanup(_app, timeout: float = 1.0) -> None:
    """Detach and stop the dedicated metrics lane within a bounded deadline."""
    global _DURABLE_METRICS_EXECUTOR, _DURABLE_METRICS_FUTURE
    global _DURABLE_METRICS_CACHE_WRITER, _DURABLE_METRICS_CACHE
    global _DURABLE_METRICS_CACHE_AT
    with _DURABLE_METRICS_LOCK:
        executor = _DURABLE_METRICS_EXECUTOR
        future = _DURABLE_METRICS_FUTURE
        _DURABLE_METRICS_EXECUTOR = None
        _DURABLE_METRICS_FUTURE = None
        _DURABLE_METRICS_CACHE_WRITER = None
        _DURABLE_METRICS_CACHE = {}
        _DURABLE_METRICS_CACHE_AT = 0.0
    if executor is None:
        return
    if future is not None and not future.done():
        future.cancel()
        try:
            await asyncio.wait_for(
                asyncio.wrap_future(future), max(0.0, float(timeout)))
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            pass
    executor.shutdown(wait=bool(future is None or future.done()), cancel_futures=True)


async def _post_call_accounting_cleanup(_app) -> None:
    report = await asyncio.to_thread(_POST_CALL_ACCOUNTING.close, 10.0)
    if not report.get("drained"):
        logger.critical(
            "Post-call accounting cleanup timed out pending=%s failed=%s rejected=%s",
            report.get("pending"),
            report.get("failed"),
            report.get("rejected"),
        )

def _resolve_project_from_session_id(session_id: str) -> str:
    """V15 R64-D — Resolve project_path from a session_id via sessions DB.

    Used by the session gate at the API boundary so close_session(session_id=...)
    and any other session-id-only call can pass the mutating-tool guard without
    the CA having to also pass project_path. Returns "" when session_id is
    missing/unknown so callers can chain with `or` against other resolvers.
    """
    if not session_id:
        return ""
    try:
        with ct._with_sessions_db() as conn:
            row = conn.execute("SELECT project_path FROM sessions WHERE session_id=? LIMIT 1", (session_id,)).fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return ""


def _resolve_project_from_page_id(page_id: str) -> str:
    """V15 R44 — Resolve project_path from a registered page_id.

    T4 mutating tools (fill_value/click/evaluate_js/...) accept only page_id
    in their body, so the session guard previously could not derive a
    project_path and falsely raised ACTIVE_SESSION_REQUIRED. We pin the
    originating project_path into _BROWSER_PAGES at launch/attach time and
    look it up here so the guard can resolve the active session.

    Returns "" when page_id is missing/unknown so callers can chain with
    `or` against other resolution paths.
    """
    if not page_id:
        return ""
    try:
        import tools_v15 as _tv  # deferred to avoid top-level import cycles
        state = getattr(_tv, "_BROWSER_PAGES", {}).get(page_id) or {}
        return state.get("project_path") or ""
    except Exception as _exc:
        # CAN-045: do not fail SILENTLY on a tools_v15 import/lookup error — that masks
        # a real breakage as a benign "unknown page_id". Log it; still return "" so the
        # caller's resolution chain (find_project_root / session_id) can continue.
        logger.warning("_resolve_project_from_page_id failed for page_id=%r: %r", page_id, _exc)
        return ""


def _cors(request_origin: str | None = None, extra: dict | None = None) -> dict:
    """Echo the requesting Origin if it's in the allowlist; otherwise default to localhost."""
    origin = request_origin if request_origin in _ALLOWED_ORIGINS else 'http://localhost:8083'
    h = {
        'Access-Control-Allow-Origin': origin,
        'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, X-Orchestrator-Token',
        'Vary': 'Origin',
    }
    if extra:
        h.update(extra)
    return h


def _is_local(request) -> bool:
    """Reject anything that didn't come from loopback."""
    remote = (request.remote or '').split(':')[0]
    return remote in {'127.0.0.1', '::1', 'localhost', ''}


def _origin_ok(request) -> bool:
    """If browser sent an Origin header, it must be in the allowlist. CLI/curl OK (no Origin)."""
    origin = request.headers.get('Origin')
    if origin is None:
        return True
    return origin in _ALLOWED_ORIGINS


def _query_int(request, name: str, default: int) -> int:
    """Parse an int query param; fall back to default on missing/invalid."""
    try:
        return int(request.query.get(name, default))
    except (TypeError, ValueError):
        return default


# ─── Deprecated tools (Task 2: tool redundancy cleanup 2026-09-10) ───────────
# Thin wrappers retained for backwards compat. Response includes deprecation hint.
# Agents should migrate: search→search_code/find_symbol, read→read_file_range.
_DEPRECATED_TOOLS: dict[str, str] = {
    "regex_search": "Use search_code(mode='regex', query=...) directly.",
    "find_callers": "Use find_symbol(name=..., include='callers') directly.",
    "find_definition_and_usage": "Use find_symbol(name=..., include='all') directly.",
}

# ─── Argument aliases (Fix 6: schema drift compat) ────────────────────────────
# When agents call with the wrong key name, rename to the canonical one
# instead of failing with a confusing TypeError.
_ARG_ALIASES = {
    # CAN-051: Playwright-style names CAs habitually use for the browser tool.
    'open_browser_page': {'wait_until': 'wait_for', 'timeout': 'timeout_ms'},
    'search_code':       {'pattern': 'query', 'regex': 'query', 'q': 'query',
                           'file_path': 'path',
                           # TA-7 fix (2026-08-18): file_glob/glob are the names CAs
                           # reach for to filter by path; they used to be rejected as
                           # UNSUPPORTED_FIELD at this validation layer. Map them onto
                           # the canonical file_pattern so the filter actually applies.
                           'file_glob': 'file_pattern', 'glob': 'file_pattern'},
    'find_symbol':       {'query': 'name', 'symbol': 'name', 'symbol_name': 'name',
                       'q': 'name',
                       'file_path': 'path'},
    'find_callers':      {'query': 'symbol', 'name': 'symbol', 'q': 'symbol',
                       'symbol_name': 'symbol'},
    # TT-A-03 (2026-08-10): regex_search canonical is `pattern` (see tools_v15.regex_search
    # signature and TOOL_SCHEMAS required=["pattern","project_path"]). Alias direction was
    # inverted ({pattern->query}), which caused _normalize_body to RENAME the canonical
    # `pattern` field into `query`, then S4 rejected the request as MISSING_REQUIRED_FIELD,
    # and the duplicate-retry fingerprint (computed over the post-normalization body) made
    # even a fresh {query:...} retry look identical to the failed {pattern:...} call.
    'regex_search':      {'query': 'pattern', 'regex': 'pattern', 'q': 'pattern'},
    'query_index':       {'filter': 'group', 'category': 'group',
                           'pattern': 'file_pattern', 'glob': 'file_pattern'},
    'read_file_range':   {'path': 'file_path', 'file': 'file_path',
                           'start': 'start_line', 'end': 'end_line',
                           'from_line': 'start_line', 'to_line': 'end_line',
                           'line_start': 'start_line', 'line_end': 'end_line'},
    'get_file_symbols':  {'path': 'file_path', 'file': 'file_path'},
    'validate_file':     {'path': 'file_path', 'file': 'file_path'},
    'validate_symbols':  {'path': 'file_path', 'file': 'file_path'},
    'write_fix':         {'path': 'file_path', 'content': 'new_content',
                           'replacement': 'new_content',
                           'start': 'start_line', 'end': 'end_line',
                           'line_start': 'start_line', 'line_end': 'end_line'},
    'write_fix_html':    {'path': 'file_path', 'content': 'new_content',
                             'start': 'start_line', 'end': 'end_line',
                             'line_start': 'start_line', 'line_end': 'end_line'},
    'write_new_file':    {'file_path': 'path', 'content': 'new_content'},
    'delete_file':       {'path': 'file_path', 'file': 'file_path'},
    'validate_encoding': {'path': 'file_path', 'file': 'file_path'},
    'get_folder_size':   {'project_path': 'folder_path', 'path': 'folder_path'},
    # V15 R19 — alias contract for R16/R17/R18 tools (audit Round 8)
    'tool_decision_hint':           {'task': 'task_description'},
    'optional_tool_probe':          {'tool_names': 'tools', 'names': 'tools'},
    'read_skill':                   {'skill_name': 'name', 'skill': 'name',
                                       'max_chars': 'max_lines'},
    'desktop_window_list':          {'title_contains': 'title_filter',
                                       'pattern': 'title_filter'},
    'run_script':                   {'expression': 'script_inline',
                                       'code': 'script_inline',
                                       'inline': 'script_inline',
                                       'script': 'script_inline'},  # R41 CA habit
    'open_session':                 {'intent': 'session_title',  # R41 CA habit
                                       'title': 'session_title',
                                       'description': 'session_title'},
    # V15 R42 — CA pain report aliases
    'extract_log_section':          {'section': 'section_title'},
    'parse_log_section':            {'section': 'section_title'},
    'fill_folder_dialog':           {'path': 'folder_path'},
    'click_and_checkpoint_loop':    {'click_selector': 'click_selector_str',
                                       'observe_selector': 'watch_selector',
                                       'watch': 'watch_selector'},
    'click_selector':               {'verify_state_after': 'before_after_state'},
    'checkpoint_loop_dom_state':    {'snapshot_fields': 'fields',
                                       'observe_selector': 'selector'},
    # V15 R63: workflow_submit_step — CA habit `step_number`/`note` was rejected
    # as UNSUPPORTED_FIELD. Real fields are `step`/`evidence`. Encountered live
    # when running rawcut_getfootage_sheet_v0 — first call ate a CA retry.
    'workflow_submit_step':         {'step_number': 'step',
                                       'note': 'evidence'},
    'save_log_snapshot':            {'log_selector': 'selector',
                                       'title': 'section_title'},
    # V15 R24: query_dom_candidates — CA habit uses max_results/limit
    'query_dom_candidates':         {'max_results': 'max_items',
                                       'limit': 'max_items'},
    # V15 R25: T4 alias regression (Round 10 audit)
    'launch_electron_with_cdp':     {'port': 'cdp_port',
                                       'wait_ms': 'verify_after_ms',
                                       'timeout_ms': 'verify_after_ms'},
    'list_cdp_targets':             {'port': 'cdp_port'},
    'process_tree_inspect':         {'process_name': 'name_pattern',
                                       'name': 'name_pattern',
                                       'pid': 'root_pid'},
    'cdp_attach_target':            {'port': 'cdp_port'},
    # V15 R30: folder tools — CA habit alias
    'folder_stats':                 {'folder_path': 'path', 'project_path': 'path'},
    'folder_exists':                {'folder_path': 'path'},
    'folder_diff':                  {'folder_a': 'path_a', 'folder_b': 'path_b'},
    # V15 R32: aliases per Round 12 testgf retest
    'query_dom_candidates_r32':     {'kind': 'filter', 'type': 'filter', 'category': 'filter'},  # marker key
    'evaluate_js':                  {'script': 'expression', 'code': 'expression', 'js': 'expression'},
}
# R32: extend existing _ARG_ALIASES entries
_ARG_ALIASES.setdefault('query_dom_candidates', {}).update({
    'kind': 'filter', 'type': 'filter', 'category': 'filter',
})
# cdp_attach_target already has port→cdp_port from R25; no canonical for title_pattern
# Use _HARMLESS_IGNORE_FIELDS below so CA's habit `title_pattern` doesn't 400.
# Also: clean up the synthetic marker key we added in transitional R32 edit.
_ARG_ALIASES.pop('query_dom_candidates_r32', None)

# R44 (2026-08-11) - result-cap synonym family. limit / max_results / max_items /
# top_k all mean 'max number of results' (same unit); a CA should not have to
# remember which name each tool's schema uses. Each tool below has exactly ONE of
# these as a real param (its canonical); the others are added as aliases so any is
# accepted. Verified shadow-free: an alias is only added when it is NOT already a
# real param of that tool, so it never hijacks a distinct field. search_code is
# excluded (bespoke limit compat via _search_code_deprecated_compat). Additive +
# non-breaking; the alias loop renames alias->canonical and canonical always wins.
_ARG_ALIASES.setdefault('ast_query', {}).update({'limit': 'max_results', 'max_items': 'max_results', 'top_k': 'max_results'})
_ARG_ALIASES.setdefault('cbm_dead_code', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('cbm_edges_query', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('cbm_nodes_query', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('cbm_template_query', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('council_cron_job_list', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('council_route_scorecard', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('get_session_events', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('git_log', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('list_projects', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('list_sessions', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('memory_query', {}).update({'max_results': 'top_k', 'max_items': 'top_k'})
_ARG_ALIASES.setdefault('query_dom_candidates', {}).update({'top_k': 'max_items'})
_ARG_ALIASES.setdefault('query_index', {}).update({'limit': 'max_results', 'max_items': 'max_results', 'top_k': 'max_results'})
_ARG_ALIASES.setdefault('query_sqlite', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
_ARG_ALIASES.setdefault('read_context_batch', {}).update({'limit': 'max_items', 'max_results': 'max_items', 'top_k': 'max_items'})
_ARG_ALIASES.setdefault('regex_search', {}).update({'limit': 'max_results', 'max_items': 'max_results', 'top_k': 'max_results'})
_ARG_ALIASES.setdefault('sync_pending_audit', {}).update({'max_results': 'limit', 'max_items': 'limit', 'top_k': 'limit'})
# CA habit: ruff_fix schema field is `path`; file_path/file must remap before S4.
_ARG_ALIASES.setdefault('ruff_fix', {}).update({'file_path': 'path', 'file': 'path'})
# A30-P5: OCR tools. `from`/`to` are the CLI flag names a CA will copy from
# `ocr delegate preview --help`, but `from` is a Python keyword so the handler
# params are from_ref/to_ref. `repo` is OCR's own flag for the repository root,
# which is project_path here. These are habit-remaps of the SAME value, not the
# file/folder/project_path family the R46 comment below refuses to alias.
_ARG_ALIASES.setdefault('ocr_review', {}).update({'from': 'from_ref', 'to': 'to_ref', 'repo': 'project_path'})
_ARG_ALIASES.setdefault('ocr_rule', {}).update({'from': 'from_ref', 'to': 'to_ref', 'repo': 'project_path', 'path': 'paths', 'file': 'paths', 'file_path': 'paths', 'files': 'paths'})
_ARG_ALIASES.setdefault('ocr_scan', {}).update({'repo': 'project_path', 'file': 'path', 'file_path': 'path', 'format': 'output_format'})
# A32-1.3: ocr_map takes the same range args as ocr_review, so it takes the same
# three habit-remaps -- a CA that learned `repo=` on one and got UNSUPPORTED_FIELD
# on the other would be a trap [A] built itself. min_changed_lines is deliberately
# NOT aliased: it is [A]'s own knob, not an OCR flag name, so a wrong guess should
# come back as UNSUPPORTED_FIELD with accepted_fields rather than be silently
# accepted under a name the schema never documents.
_ARG_ALIASES.setdefault('ocr_map', {}).update({'from': 'from_ref', 'to': 'to_ref', 'repo': 'project_path'})
# CA habit: list_structure uses depth/path. max_depth/file_path were 400 UNSUPPORTED_FIELD.
_ARG_ALIASES.setdefault('list_structure', {}).update({
    'max_depth': 'depth',
    'file_path': 'path',
    'folder': 'path',
    'folder_path': 'path',
})

# R46 (2026-08-12) - write-content synonym family. content / new_content / replacement
# all mean the text payload to write; a CA should not have to remember which name each
# write tool uses. Shadow-safe (alias added only when NOT already a real param). Additive,
# non-breaking; canonical wins. Other analyzer param families are intentionally NOT aliased:
# file/file_path vs folder_path vs project_path (folder != file != project root); start
# (byte-or-line in read_chunks); timeout vs timeout_ms (unit); port vs cdp_port (distinct)
# -- aliasing those would MISROUTE values.
_ARG_ALIASES.setdefault('memory_ingest_path', {}).update({'new_content': 'content', 'replacement': 'content'})
_ARG_ALIASES.setdefault('replace_between_anchors', {}).update({'content': 'new_content', 'replacement': 'new_content'})
_ARG_ALIASES.setdefault('write_draft_patch', {}).update({'content': 'new_content', 'replacement': 'new_content'})
_ARG_ALIASES.setdefault('write_fix_html', {}).update({'replacement': 'new_content'})


# V15 R19 — Harmless-ignore registry: fields CA commonly passes by habit but
# the target tool doesn't need. Silently dropped in _normalize_body() instead
# of returning 400 UNSUPPORTED_FIELD. Reduces CA retry overhead from R8 audit.
_DUPLICATE_FAILURE_STATE: dict = {}
# P12 (campaign-1608): fingerprint -> first_seen wall-clock, so a replayed
# DUPLICATE_RETRY_BLOCKED verdict can disclose cached:true + how stale it is.
_DUPLICATE_FIRST_SEEN: dict = {}
# Compatibility alias retained for the anti-drift contract surface.
_ANTI_DRIFT_CALL_STATE = _DUPLICATE_FAILURE_STATE
_DETERMINISTIC_RETRY_CACHE = _DUPLICATE_FAILURE_STATE


def _anti_drift_duplicate_failure(tool_name: str, body: dict, error_name: str,
                                  meta: dict | None = None) -> dict | None:
    """Record deterministic failures within one agent/session/task epoch."""
    meta = meta or {}
    identity = "|".join(str(meta.get(k) or "") for k in
                        ("agent_id", "session_id", "task_epoch"))
    return _duplicate_retry_guard(identity, tool_name, body, error_name)


def _duplicate_retry_guard(agent_id: str, tool_name: str, body: dict, error_name: str) -> dict | None:
    """Return DUPLICATE_RETRY_BLOCKED on 2nd identical deterministic failure, else None (record)."""
    import hashlib as _hl
    if len(_DUPLICATE_FAILURE_STATE) > 4096:
        _DUPLICATE_FAILURE_STATE.clear()  # bounded memory; counter reset is acceptable
    normalized = {k: v for k, v in sorted(body.items()) if k not in ("project_path",)}
    fp = f"{agent_id}|{tool_name}|{_hl.md5(str(normalized).encode()).hexdigest()}|{error_name}"
    now = time.time()
    count = _DUPLICATE_FAILURE_STATE.get(fp, 0) + 1
    _DUPLICATE_FAILURE_STATE[fp] = count
    if count <= 1:
        # P12 (campaign-1608): stamp when this exact verdict was first produced so
        # a later replayed (cached) block can disclose how stale it is.
        if len(_DUPLICATE_FIRST_SEEN) > 4096:
            _DUPLICATE_FIRST_SEEN.clear()
        _DUPLICATE_FIRST_SEEN[fp] = now
        return None  # first occurrence — record only
    first_seen = _DUPLICATE_FIRST_SEEN.get(fp, now)
    return {
        "ok": False,
        "error_name": "DUPLICATE_RETRY_BLOCKED",
        "retryable": False,
        "reason": f"Identical payload already failed with {error_name}. Apply corrected_payload or stop.",
        "fingerprint": fp,
        "retry_count": count,
        # P12 (campaign-1608): this verdict is REPLAYED from dedup state, not a
        # fresh re-evaluation of external conditions. Disclose it so a CA can tell
        # "still wrong" apart from "not re-run" after it changed something.
        "cached": True,
        "first_seen_at": first_seen,
        "seconds_since_first_seen": round(now - first_seen, 3),
    }


# CAN-050: deterministic tool-body failures the guard blocks at the response
# boundary (UNSUPPORTED_FIELD keeps its inline guard at the schema gate).
_DETERMINISTIC_TOOLBODY_ERRORS = {
    "MISSING_REQUIRED_FIELD", "SCHEMA_VIOLATION", "UNSUPPORTED_FIELD",
    "FILE_NOT_FOUND", "PATH_NOT_FOUND", "REGEX_INVALID", "UNKNOWN_TOOL",
    "INVALID_QUERY_ENCODING",
}


def _tool_result_failed(res) -> bool:
    """CAN-050: ledger truth — a result with ok=False / timed_out / non-zero
    exit code is a failure even when the tool set no `error` field
    (run_project_test_script returns ok=false without `error`)."""
    if not isinstance(res, dict):
        return False
    if res.get("error"):
        return True
    if res.get("ok") is False:
        return True
    if res.get("timed_out") is True:
        return True
    _ec = res.get("exit_code")
    if _ec is None:
        _ec = res.get("returncode")
    if isinstance(_ec, int) and _ec != 0:
        return True
    if str(res.get("overall_status") or "").lower() in ("fail", "failed", "error"):
        return True
    return False
# CAN-052 admission control -- bounded concurrency and bounded waiting per
# heavy-tool category.  Dedicated executors own the blocking work; these
# semaphores keep HTTP request admission bounded before a tool reaches its lane.
_TOOL_SEM_LIMITS = {"subprocess": 2, "browser": 1, "read": 8}
_TOOL_SEM_CATEGORY = {
    "run_project_command": "command", "run_project_test_script": "test",
    "run_test_profile": "test", "run_ps1_script": "command",
    "run_node_script": "command", "backup_project": "backup",
    "git_status": "git", "git_stale_check": "git",
    "git_diff": "git", "git_log": "git",
    "git_stage": "git", "git_unstage": "git",
    "git_commit": "git",
    "open_browser_page": "browser", "launch_electron_with_cdp": "browser",
    "read_file_range": "read", "read_context_batch": "read",
    "architecture_context_pack": "read",
}
# CAN-052 backup admission is 2 (not 1): the per-project asyncio.Lock in
# code_tools.backup_project is the authoritative same-project serializer, so a
# second concurrent HTTP call must REACH it to get the immediate
# BACKUP_IN_PROGRESS fast-reject instead of blocking here on a global 1-slot
# gate. Two slots let (a) a same-project call reach the per-project lock and
# (b) a targeted backup (read_io lane) proceed even while a full backup on
# another project holds the first slot. Admission stays bounded: full backups
# serialize on executor lane 'backup' (max_workers=1, max_queue=2) and targeted
# backups run on the wide 'read_io' lane; per-project lock + lane capacity cap
# total in-flight/queued backups. See tests/test_executor_admission.py.
_TOOL_SEM_LIMITS.update({"command": 4, "test": 2, "git": 4, "backup": 2})
_TOOL_QUEUE_LIMITS = {
    "subprocess": 8, "browser": 4, "read": 48,
    "command": 16, "test": 6, "git": 16, "backup": 2,
}
_TOOL_SEMS: dict = {}
_WRITE_PROJECT_SEMS: dict = {}
_SEMAPHORE_HOLDERS: dict[str, dict[str, float]] = {}
_SEMAPHORE_WAITERS: dict[str, int] = {}
_SEMAPHORE_WAITER_STARTED: dict[str, dict[str, float]] = {}
_SEMAPHORE_REJECTED: dict[str, int] = {}
_INFLIGHT_OPERATIONS: dict[str, asyncio.Task] = {}
_OPERATION_CANCEL_EVENTS: dict[str, threading.Event] = {}
_WRITE_SEM_TOOLS = {"write_fix", "write_fix_html", "write_new_file",
                    "write_transaction", "apply_after_dry_run", "delete_file"}


class ToolAdmissionOverloaded(RuntimeError):
    error_name = "TOOL_QUEUE_FULL"

    def __init__(self, category: str, status: dict):
        self.category = category
        self.status = status
        super().__init__(
            f"tool admission queue '{category}' is full "
            f"({status.get('waiters')}/{status.get('queue_capacity')})"
        )

    def as_payload(self) -> dict:
        return {
            "ok": False,
            "error": str(self),
            "reason": str(self),
            "error_name": self.error_name,
            "tool_category": self.category,
            "admission": self.status,
            "retryable": True,
            "retry_after_seconds": 1,
            "ca_next_action": "retry_after_tool_queue_capacity_frees",
        }


def _tool_semaphore(tool_name: str, body: dict):
    """Return the semaphore guarding this tool, or None (unbounded)."""
    if tool_name in _WRITE_SEM_TOOLS:
        key = str(body.get("project_path") or "").replace("\\", "/").rstrip("/").lower()
        sem = _WRITE_PROJECT_SEMS.get(key)
        if sem is None:
            sem = _WRITE_PROJECT_SEMS.setdefault(key, asyncio.Semaphore(1))
        return sem
    cat = _TOOL_SEM_CATEGORY.get(tool_name)
    if not cat:
        return None
    sem = _TOOL_SEMS.get(cat)
    if sem is None:
        limit = max(1, int(os.environ.get(
            f"ORCH_SEM_{cat.upper()}", _TOOL_SEM_LIMITS.get(cat, 4))))
        sem = _TOOL_SEMS.setdefault(cat, asyncio.Semaphore(limit))
    return sem


def _tool_semaphore_category(tool_name: str, body: dict) -> str:
    """Stable observability key for a bounded tool slot."""
    if tool_name in _WRITE_SEM_TOOLS:
        project = str(body.get("project_path") or "").replace("\\", "/").rstrip("/").lower()
        return f"write:{project or '(unknown)'}"
    return str(_TOOL_SEM_CATEGORY.get(tool_name) or "")


def _tool_queue_limit(category: str) -> int:
    base_category = "write" if category.startswith("write:") else category
    default = 16 if base_category == "write" else _TOOL_QUEUE_LIMITS.get(base_category, 16)
    return max(0, int(os.environ.get(f"ORCH_QUEUE_{base_category.upper()}", default)))


def _tool_concurrency_limit(category: str) -> int:
    if category.startswith("write:"):
        return 1
    return max(1, int(os.environ.get(
        f"ORCH_SEM_{category.upper()}", _TOOL_SEM_LIMITS.get(category, 4))))


def _admission_status(category: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    holders = _SEMAPHORE_HOLDERS.get(category) or {}
    wait_started = _SEMAPHORE_WAITER_STARTED.get(category) or {}
    oldest_holder = min(holders.items(), key=lambda item: item[1]) if holders else (None, None)
    oldest_wait = min(wait_started.values()) if wait_started else None
    return {
        "category": category,
        "limit": _tool_concurrency_limit(category),
        "held": len(holders),
        "waiters": int(_SEMAPHORE_WAITERS.get(category, 0)),
        "queue_capacity": _tool_queue_limit(category),
        "queue_rejected": int(_SEMAPHORE_REJECTED.get(category, 0)),
        "holder_op_id": oldest_holder[0],
        "held_for_sec": (
            round(max(0.0, now - oldest_holder[1]), 3)
            if oldest_holder[1] is not None else None
        ),
        "oldest_wait_ms": (
            round(max(0.0, now - oldest_wait) * 1000.0, 3)
            if oldest_wait is not None else 0.0
        ),
    }


async def _acquire_tool_semaphore(tool_name: str, body: dict, operation_id: str):
    """Acquire one bounded slot or reject immediately when its wait queue is full."""
    sem = _tool_semaphore(tool_name, body)
    category = _tool_semaphore_category(tool_name, body)
    if sem is None:
        return None, ""
    waiters = int(_SEMAPHORE_WAITERS.get(category, 0))
    if sem.locked() and waiters >= _tool_queue_limit(category):
        _SEMAPHORE_REJECTED[category] = _SEMAPHORE_REJECTED.get(category, 0) + 1
        raise ToolAdmissionOverloaded(category, _admission_status(category))
    _SEMAPHORE_WAITERS[category] = waiters + 1
    _SEMAPHORE_WAITER_STARTED.setdefault(category, {})[operation_id] = time.time()
    try:
        await sem.acquire()
    finally:
        _SEMAPHORE_WAITERS[category] = max(
            0, _SEMAPHORE_WAITERS.get(category, 1) - 1)
        wait_started = _SEMAPHORE_WAITER_STARTED.get(category) or {}
        wait_started.pop(operation_id, None)
        if not wait_started:
            _SEMAPHORE_WAITER_STARTED.pop(category, None)
    _SEMAPHORE_HOLDERS.setdefault(category, {})[operation_id] = time.time()
    return sem, category


def _release_tool_semaphore(sem, category: str, operation_id: str) -> None:
    """Idempotently release the exact slot owned by operation_id."""
    if sem is None:
        return
    holders = _SEMAPHORE_HOLDERS.get(category) or {}
    if operation_id not in holders:
        return
    holders.pop(operation_id, None)
    if not holders:
        _SEMAPHORE_HOLDERS.pop(category, None)
    sem.release()


def _semaphore_status_rows(now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    categories = (
        set(_TOOL_SEM_LIMITS) | set(_TOOL_SEMS) | set(_SEMAPHORE_HOLDERS)
        | set(_SEMAPHORE_WAITERS) | set(_SEMAPHORE_REJECTED)
    )
    categories.update(f"write:{key or '(unknown)'}" for key in _WRITE_PROJECT_SEMS)
    return [_admission_status(category, now) for category in sorted(categories)]


async def _executor_lanes_cleanup(app) -> None:
    timeout = max(0.0, float(os.environ.get("ORCH_EXECUTOR_DRAIN_TIMEOUT", "10")))
    rows = await shutdown_executor_lanes(drain_timeout=timeout)
    undrained = [row for row in rows if not row.get("drained")]
    if undrained:
        logger.error("executor lane shutdown did not drain: %s", undrained)
    else:
        logger.info("executor lane shutdown drained %s lanes", len(rows))


async def _cancel_and_await_background_tasks(
    tasks: dict[str, asyncio.Task | None],
    *,
    timeout: float = 5.0,
) -> dict[str, dict]:
    """Cancel owned background tasks and observe their terminal state."""
    active = {name: task for name, task in tasks.items() if task is not None}
    for task in active.values():
        if not task.done():
            task.cancel()
    if active:
        done, pending = await asyncio.wait(
            set(active.values()),
            timeout=max(0.0, float(timeout)),
        )
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        for task in pending:
            task.cancel()
    rows: dict[str, dict] = {}
    for name, task in active.items():
        error = ""
        if task.done() and not task.cancelled():
            try:
                error = str(task.exception() or "")[:300]
            except (asyncio.CancelledError, Exception):
                error = ""
        rows[name] = {
            "done": task.done(),
            "cancelled": task.cancelled(),
            "error": error,
        }
    return rows


_RESULT_REF_CACHE: dict = {}
_RESULT_BUDGET_BYTES = max(2048, int(os.environ.get("ORCHESTRATOR_RESULT_MAX_BYTES", "50000")))
_TOOL_CATALOG_DEFAULT_LIMIT = max(10, int(os.environ.get("ORCHESTRATOR_TOOL_CATALOG_LIMIT", "30")))
_TASK_META_FIELDS = {"session_id", "task_epoch", "prompt_hash"}


def _task_metadata(session_id: str = "", agent_id: str = "") -> dict:
    if not session_id:
        return {}
    current = ct._task_checkpoint_load(str(session_id)) or {}
    if agent_id:
        current = (current.get("subtasks") or {}).get(str(agent_id)) or current
    return {k: current[k] for k in ("task_epoch", "prompt_hash") if k in current}


def _task_checkpoint_for_identity(session_id: str, agent_id: str) -> dict:
    """Return one SQLite-authoritative checkpoint snapshot for this identity."""
    if not session_id or not agent_id:
        return {}
    current = ct._task_checkpoint_load(str(session_id)) or {}
    if str(current.get("agent_id") or "") == str(agent_id):
        return current
    subtask = (current.get("subtasks") or {}).get(str(agent_id)) or {}
    return subtask if str(subtask.get("agent_id") or "") == str(agent_id) else {}
def _auth_preamble_sync(
    task_session_id: str,
    task_agent_id: str,
    header_session_id: str,
    header_epoch,
    header_prompt_hash: str,
    task_capability: str,
    transport: str,
    tool_name: str,
    peer_host: str,
) -> tuple[dict, dict, str]:
    """Resolve checkpoint-backed request auth in one worker-thread hop."""
    task_meta = _task_metadata(task_session_id, task_agent_id)
    issued = (
        _task_checkpoint_for_identity(header_session_id, task_agent_id)
        if tool_name == "record_error_resolution"
        else {}
    )
    principal_id = _quota_principal(
        header_session_id,
        task_agent_id,
        header_epoch,
        header_prompt_hash,
        task_capability,
        transport,
        peer_host,
    )
    return task_meta, issued, principal_id


def _anonymous_quota_principal(session_id: str, agent_id: str, peer_host: str) -> str:
    """Derive a stable anonymous quota bucket from the best caller signal."""
    namespace = "session"
    signal = str(session_id or "").strip()
    if not signal:
        namespace = "agent"
        signal = str(agent_id or "").strip()
    if not signal:
        namespace = "peer"
        signal = str(peer_host or "").strip()
    if not signal:
        return "anonymous:localhost"
    material = f"{namespace}:{signal}"
    return "anonymous:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _quota_principal(session_id: str, agent_id: str, epoch, prompt_hash: str,
                     capability: str, transport: str, peer_host: str = "") -> str:
    """Resolve a non-client-chosen quota key from an authoritative checkpoint."""
    issued = _task_checkpoint_for_identity(session_id, agent_id) if session_id and agent_id else {}
    expected = str(issued.get("owner_capability_sha256") or issued.get("delegation_capability_sha256") or "")
    try:
        current_epoch = int(epoch)
    except (TypeError, ValueError):
        current_epoch = -1
    valid = bool(issued and capability and expected and current_epoch == int(issued.get("task_epoch", 0))
                 and prompt_hash == str(issued.get("prompt_hash") or "")
                 and _secrets.compare_digest(hashlib.sha256(capability.encode()).hexdigest(), expected))
    if not valid:
        return _anonymous_quota_principal(session_id, agent_id, peer_host)
    root = ct._task_checkpoint_load(session_id) or {}
    root_identity = str(root.get("owner_identity_id") or "")
    if issued.get("delegation_capability_sha256"):
        material = f"{SERVICE_NAME}|{root_identity}|{expected}"
    else:
        material = f"{SERVICE_NAME}|{root_identity}"
    return "principal:" + hashlib.sha256(material.encode()).hexdigest()

def _task_checkpoint_storage_response(exc, cors: dict, *, write_performed: bool = False):
    """Convert an authoritative checkpoint failure into a loud transport error."""
    return web.json_response({
        "ok": False,
        "error": str(exc),
        "error_name": getattr(exc, "error_name", "TASK_CHECKPOINT_STORAGE_ERROR"),
        "retryable": False,
        "write_performed": write_performed,
        "ca_next_action": "repair_task_checkpoint_storage_then_retry",
    }, status=503, headers=cors)


def _anti_drift_bound_payload(payload: dict, max_bytes: int | None = None) -> dict:
    """Bound a JSON payload and expose a stable continuation reference."""
    shaped = json.loads(_shape_tool_response_text(
        "anti_drift", json.dumps(payload, ensure_ascii=False), max_bytes=max_bytes))
    if shaped.get("truncated"):
        shaped["content_ref"] = shaped.pop("result_ref")
        shaped["ca_next_action"] = "read_bounded_continuation"
    return shaped


_RESULT_REF_MAX_ENTRIES = max(16, int(os.environ.get("ORCHESTRATOR_RESULT_REF_CACHE_MAX", "64")))


def _result_ref_store(ref: str, raw_text: str) -> None:
    """FixA2407 P2-2: keep the bounded-result cache readable AND bounded.

    _RESULT_REF_CACHE used to grow without limit and was never read back. It is now
    served by GET /api/result/{ref}; dicts keep insertion order, so evicting the first
    key drops the oldest ref first.
    """
    _RESULT_REF_CACHE[ref] = raw_text
    while len(_RESULT_REF_CACHE) > _RESULT_REF_MAX_ENTRIES:
        _RESULT_REF_CACHE.pop(next(iter(_RESULT_REF_CACHE)), None)


# ─── FixA2407 P1-1: write ledger ─────────────────────────────────────────────
# A write that times out client-side at 120-300s has usually already committed on the
# server. Without a record the caller cannot tell committed from not_committed, so it
# either retries (duplicate write) or spends a read-back proving what the server knew.
_WRITE_LEDGER: dict = {}            # operation_id -> record (insertion-ordered)
# ISS-01: (project_path, file_path) -> LIST of operation_ids, oldest first.
# This used to hold a single op id. Index/script tools carry file_path="", so
# every non-file-scoped mutating tool on one root shared the key
# (project_path, "") and the newest call silently overwrote the previous one:
# polling write_status by project_path for a cbm_incremental_index returned the
# ledger of an unrelated run_project_command instead -- a different tool's
# commit_state answering "did my index commit?". The list keeps every candidate
# so the lookup can disambiguate or refuse instead of guessing.
_WRITE_LEDGER_BY_TARGET: dict = {}
_WRITE_LEDGER_TARGET_MAX = 20       # candidates kept per target key
_WRITE_LEDGER_MAX = max(50, int(os.environ.get("ORCHESTRATOR_WRITE_LEDGER_MAX", "300")))
_WRITE_LEDGER_TTL_DAYS = max(
    1, min(365, int(os.environ.get("ORCHESTRATOR_WRITE_LEDGER_TTL_DAYS", "7")))
)
_WRITE_LEDGER_LAST_PURGE = 0.0
_WRITE_LEDGER_PROGRESS_LOCK = threading.RLock()
_WRITE_LEDGER_ASYNC_LOCK: asyncio.Lock | None = None
_WRITE_LEDGER_ASYNC_LOCK_LOOP = None


def _write_ledger_async_lock() -> asyncio.Lock:
    """Return a loop-local lock so SQLite ledger work stays ordered off-loop."""
    global _WRITE_LEDGER_ASYNC_LOCK, _WRITE_LEDGER_ASYNC_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _WRITE_LEDGER_ASYNC_LOCK is None or _WRITE_LEDGER_ASYNC_LOCK_LOOP is not loop:
        _WRITE_LEDGER_ASYNC_LOCK = asyncio.Lock()
        _WRITE_LEDGER_ASYNC_LOCK_LOOP = loop
    return _WRITE_LEDGER_ASYNC_LOCK


async def _write_ledger_begin_async(tool_name: str, body: dict, session_id: str) -> str:
    """Persist begin durably without blocking the aiohttp event loop."""
    async with _write_ledger_async_lock():
        return await asyncio.to_thread(_write_ledger_begin, tool_name, body, session_id)


async def _write_ledger_finish_async(op_id: str, result: dict, status: str) -> None:
    """Serialize terminal ledger commits without occupying the event loop."""
    if not op_id:
        return
    async with _write_ledger_async_lock():
        await asyncio.to_thread(_write_ledger_finish, op_id, result, status)


async def _write_ledger_db_get_async(
    operation_id: str = "", target: tuple[str, str] | None = None
):
    """Keep write-status SQLite fallback reads off the event loop too."""
    async with _write_ledger_async_lock():
        return await asyncio.to_thread(_write_ledger_db_get, operation_id, target)


async def _write_ledger_db_list_async(target: tuple[str, str], limit: int = 20):
    """ISS-01: every ledger row for one target, newest first (not just LIMIT 1)."""
    async with _write_ledger_async_lock():
        return await asyncio.to_thread(_write_ledger_db_list, target, limit)

def _write_ledger_target_remember(pp: str, fp: str, op_id: str) -> None:
    """Append op_id to the target's candidate list (most recent last, deduped)."""
    if not (pp or fp) or not op_id:
        return
    key = (pp, fp)
    ids = _WRITE_LEDGER_BY_TARGET.get(key)
    if not isinstance(ids, list):
        # Tolerate a legacy single-string value from an older generation.
        ids = [ids] if isinstance(ids, str) and ids else []
    if op_id in ids:
        ids.remove(op_id)
    ids.append(op_id)
    del ids[:-_WRITE_LEDGER_TARGET_MAX]
    _WRITE_LEDGER_BY_TARGET[key] = ids


def _write_ledger_target_ids(pp: str, fp: str) -> list:
    ids = _WRITE_LEDGER_BY_TARGET.get((pp, fp))
    if isinstance(ids, list):
        return list(ids)
    if isinstance(ids, str) and ids:
        return [ids]
    return []


def _write_ledger_candidate_summary(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("operation_id", "tool", "status", "started_at",
                                    "finished_at", "updated_at")}


def _write_ledger_pick_candidate(records: list, tool_filter: str = "") -> tuple:
    """Resolve a target lookup to ONE record, or report the ambiguity.

    Returns (record | None, candidates, ambiguous). A target with several
    distinct tools is only answered when exactly one candidate is still
    in_flight (that is unambiguously "the operation you are waiting on") or when
    the caller named a tool. Otherwise the caller gets the candidate list and
    must pass operation_id -- silently returning the newest row is what made a
    run_project_command answer for a cbm_incremental_index.
    """
    records = [r for r in records if isinstance(r, dict)]
    if tool_filter:
        records = [r for r in records if str(r.get("tool") or "") == tool_filter]
    if not records:
        return None, [], False
    if len(records) == 1:
        return records[0], [_write_ledger_candidate_summary(records[0])], False
    tools = {str(r.get("tool") or "") for r in records}
    statuses = {str(r.get("status") or "") for r in records}
    if len(tools) == 1 and len(statuses) == 1:
        # Same tool AND every candidate in the same state: recency then only
        # picks WHICH evidence row answers, never WHAT the verdict is.
        # B031: the `len(statuses) == 1` half is new. ISS-01 closed this swap
        # across tool names and left it open inside one -- two write_fix on the
        # same target key, one committed and one failed, were answered with
        # whichever landed last. Querying by project_path+file_path is the
        # DOCUMENTED route when no response ever arrived, so that caller has no
        # operation_id to fall back on and read a verdict belonging to another
        # payload as its own.
        newest = max(records, key=lambda r: float(r.get("updated_at") or 0.0))
        return newest, [_write_ledger_candidate_summary(r) for r in records], False
    in_flight = [r for r in records if str(r.get("status") or "") == "in_flight"]
    candidates = [_write_ledger_candidate_summary(r) for r in records]
    if len(in_flight) == 1:
        return in_flight[0], candidates, False
    return None, candidates, True


def _write_ledger_target(body: dict) -> tuple[str, str]:
    """Normalised (project_path, file_path) key so lookup survives slash/case variance."""
    pp = str((body or {}).get("project_path") or "")
    fp = str((body or {}).get("file_path") or (body or {}).get("path")
             or (body or {}).get("dst") or "")
    pp = os.path.normcase(os.path.normpath(pp)) if pp else ""
    fp = os.path.normcase(fp.replace("/", "\\")) if fp else ""
    return pp, fp


def _write_ledger_operation_id(tool_name: str, body: dict, session_id: str) -> str:
    """Deterministic: an identical retry maps onto the same ledger entry."""
    material = json.dumps({"tool": tool_name, "body": body, "session": str(session_id or "")},
                          ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _write_ledger_db_upsert(rec: dict) -> bool:
    """Persist one allowlisted ledger record without regressing terminal state."""
    try:
        result = rec.get("result") if isinstance(rec.get("result"), dict) else None
        result_json = json.dumps(result, ensure_ascii=False) if result is not None else None
        conn = sqlite3.connect(str(ct._SESSIONS_DB), timeout=0.1)
        try:
            conn.execute("PRAGMA busy_timeout=100")
            conn.execute(
                """INSERT INTO write_operations
                   (operation_id, tool, status, project_path, file_path, session_id,
                    started_at, finished_at, duration_ms, attempts, previous_status,
                    result_json, origin_generation, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(operation_id) DO UPDATE SET
                    tool=excluded.tool, status=excluded.status,
                    project_path=excluded.project_path, file_path=excluded.file_path,
                    session_id=excluded.session_id, started_at=excluded.started_at,
                    finished_at=excluded.finished_at, duration_ms=excluded.duration_ms,
                    attempts=excluded.attempts, previous_status=excluded.previous_status,
                    result_json=excluded.result_json,
                    origin_generation=excluded.origin_generation,
                    updated_at=excluded.updated_at
                   WHERE write_operations.status != 'committed'
                     AND (
                       excluded.status = 'committed'
                       OR excluded.attempts > write_operations.attempts
                       OR (
                         excluded.attempts = write_operations.attempts
                         AND CASE excluded.status
                               WHEN 'in_flight' THEN 1
                               WHEN 'failed' THEN 2
                               ELSE 0
                             END >=
                             CASE write_operations.status
                               WHEN 'in_flight' THEN 1
                               WHEN 'failed' THEN 2
                               ELSE 0
                             END
                       )
                     )""",
                (
                    rec.get("operation_id"), rec.get("tool"), rec.get("status"),
                    rec.get("project_path"), rec.get("file_path"), rec.get("session_id"),
                    rec.get("started_at"), rec.get("finished_at"), rec.get("duration_ms"),
                    rec.get("attempts") or 1, rec.get("previous_status"), result_json,
                    rec.get("origin_generation") or SERVER_GENERATION,
                    rec.get("updated_at") or time.time(),
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("write ledger persistence failed: %s", exc)
        return False


def _write_ledger_db_get(operation_id: str = "", target: tuple[str, str] | None = None):
    try:
        columns = (
            "operation_id, tool, status, project_path, file_path, session_id, "
            "started_at, finished_at, duration_ms, attempts, previous_status, "
            "result_json, origin_generation, updated_at"
        )
        conn = sqlite3.connect(str(ct._SESSIONS_DB), timeout=0.1)
        try:
            conn.execute("PRAGMA busy_timeout=100")
            if operation_id:
                row = conn.execute(
                    f"SELECT {columns} FROM write_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
            elif target is not None:
                row = conn.execute(
                    f"""SELECT {columns} FROM write_operations
                        WHERE project_path=? AND file_path=?
                        ORDER BY updated_at DESC LIMIT 1""",
                    target,
                ).fetchone()
            else:
                row = None
        finally:
            conn.close()
        if not row:
            return None
        keys = [
            "operation_id", "tool", "status", "project_path", "file_path",
            "session_id", "started_at", "finished_at", "duration_ms", "attempts",
            "previous_status", "result_json", "origin_generation", "updated_at",
        ]
        rec = dict(zip(keys, row))
        raw_result = rec.pop("result_json", None)
        try:
            rec["result"] = json.loads(raw_result) if raw_result else None
        except (TypeError, json.JSONDecodeError):
            rec["result"] = None
        return rec
    except Exception as exc:
        logger.warning("write ledger lookup failed: %s", exc)
        return None


def _write_ledger_db_list(target: tuple[str, str], limit: int = 20) -> list:
    """ISS-01: all ledger rows for one target, newest first.

    `_write_ledger_db_get(target=...)` answers with ORDER BY updated_at DESC
    LIMIT 1, which is exactly the silent swap the memory map had: the newest
    mutating tool on the root wins regardless of which tool the caller asked
    about. Callers that need to disambiguate need the whole set.
    """
    try:
        columns = (
            "operation_id, tool, status, project_path, file_path, session_id, "
            "started_at, finished_at, duration_ms, attempts, previous_status, "
            "result_json, origin_generation, updated_at"
        )
        conn = sqlite3.connect(str(ct._SESSIONS_DB), timeout=0.1)
        try:
            conn.execute("PRAGMA busy_timeout=100")
            rows = conn.execute(
                f"""SELECT {columns} FROM write_operations
                    WHERE project_path=? AND file_path=?
                    ORDER BY updated_at DESC LIMIT ?""",
                (target[0], target[1], max(1, int(limit))),
            ).fetchall()
        finally:
            conn.close()
        keys = [
            "operation_id", "tool", "status", "project_path", "file_path",
            "session_id", "started_at", "finished_at", "duration_ms", "attempts",
            "previous_status", "result_json", "origin_generation", "updated_at",
        ]
        out = []
        for row in rows or []:
            rec = dict(zip(keys, row))
            raw_result = rec.pop("result_json", None)
            try:
                rec["result"] = json.loads(raw_result) if raw_result else None
            except (TypeError, json.JSONDecodeError):
                rec["result"] = None
            out.append(rec)
        return out
    except Exception as exc:
        logger.warning("write ledger list failed: %s", exc)
        return []


def _write_ledger_choose(memory_rec: dict | None, disk_rec: dict | None) -> tuple[dict, str]:
    """Prefer irrevocable committed truth, then the newest non-terminal attempt."""
    memory = dict(memory_rec or {})
    disk = dict(disk_rec or {})
    if disk.get("status") == "committed":
        return disk, "sqlite"
    if memory.get("status") == "committed":
        return memory, "memory"
    if not memory:
        return (disk, "sqlite") if disk else ({}, "")
    if not disk:
        return memory, "memory"
    phase_rank = {"in_flight": 1, "failed": 2}
    memory_key = (
        int(memory.get("attempts") or 0),
        phase_rank.get(str(memory.get("status") or ""), 0),
        float(memory.get("updated_at") or 0),
    )
    disk_key = (
        int(disk.get("attempts") or 0),
        phase_rank.get(str(disk.get("status") or ""), 0),
        float(disk.get("updated_at") or 0),
    )
    return (disk, "sqlite") if disk_key >= memory_key else (memory, "memory")

def _write_ledger_purge() -> None:
    """Bound disk history while never deleting current-generation in-flight work."""
    global _WRITE_LEDGER_LAST_PURGE
    now = time.time()
    if now - _WRITE_LEDGER_LAST_PURGE < 60:
        return
    # Rate-limit attempts too: a locked DB must not make every following write
    # spend another busy timeout on retention.
    _WRITE_LEDGER_LAST_PURGE = now
    try:
        cutoff = now - (_WRITE_LEDGER_TTL_DAYS * 86400)
        conn = sqlite3.connect(str(ct._SESSIONS_DB), timeout=0.1)
        try:
            conn.execute("PRAGMA busy_timeout=100")
            conn.execute(
                """DELETE FROM write_operations
                   WHERE updated_at < ?
                     AND (status != 'in_flight' OR origin_generation != ?)""",
                (cutoff, SERVER_GENERATION),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("write ledger retention failed: %s", exc)


def _write_ledger_commit_state(rec: dict) -> str:
    status = str((rec or {}).get("status") or "")
    if status == "in_flight":
        origin = str((rec or {}).get("origin_generation") or "")
        if origin and origin != SERVER_GENERATION:
            return "unknown"
        return "in_flight"
    return {"committed": "committed", "failed": "not_committed"}.get(status, "unknown")


def _write_ledger_begin(tool_name: str, body: dict, session_id: str = "") -> str:
    """Open a ledger entry before a mutating tool runs. Returns "" for read-only tools."""
    try:
        if tool_name not in _MUTATING_TOOLS:
            return ""
        op_id = _write_ledger_operation_id(tool_name, body, session_id)
        pp, fp = _write_ledger_target(body)
        prior, _prior_source = _write_ledger_choose(
            _WRITE_LEDGER.get(op_id),
            _write_ledger_db_get(operation_id=op_id),
        )
        if prior.get("status") == "committed":
            # A deterministic retry cannot make an already-committed operation in-flight.
            _WRITE_LEDGER[op_id] = prior
            _write_ledger_target_remember(pp, fp, op_id)
            return op_id
        now = time.time()
        rec = {
            "operation_id": op_id,
            "tool": tool_name,
            "status": "in_flight",
            "project_path": pp,
            "file_path": fp,
            "session_id": str(session_id or ""),
            "started_at": now,
            "finished_at": None,
            "duration_ms": None,
            "attempts": int(prior.get("attempts") or 0) + 1,
            "previous_status": prior.get("status"),
            "result": None,
            "origin_generation": SERVER_GENERATION,
            "updated_at": now,
        }
        _WRITE_LEDGER[op_id] = rec
        _write_ledger_target_remember(pp, fp, op_id)
        while len(_WRITE_LEDGER) > _WRITE_LEDGER_MAX:
            _WRITE_LEDGER.pop(next(iter(_WRITE_LEDGER)), None)
        if _write_ledger_db_upsert(rec):
            _write_ledger_purge()
        return op_id
    except Exception:
        return ""  # the ledger is an observability aid; it must never break a write


# ISS-10: evidence keys the ledger keeps from a finished tool result. The first
# group is write_fix's (the only family the original list covered); the rest let
# a caller polling write_status see what an index / command / git / session
# mutation actually did instead of a bare {"ok": true}. Kept to small scalars on
# purpose -- this row is persisted per operation and read on every poll.
_WRITE_LEDGER_RESULT_KEYS = (
    # write family
    "ok", "error", "error_name", "write_performed", "applied", "file",
    "replaced_lines", "bytes_written", "new_hash", "file_sha256",
    # derived-index / graph family
    "run_id", "scope", "status", "files_indexed", "files_unchanged",
    "files_total", "nodes", "edges", "nodes_added", "edges_added",
    "stale_files", "duration_s", "cancelled", "partial",
    # command / script family
    "exit_code", "returncode", "executed", "timed_out", "destructive_count",
    # git family
    "commit_hash", "committed", "staged_count", "branch",
    # routing
    "ca_next_action", "operation_id",
)
_WRITE_LEDGER_SCALAR_MAX = 240


def _write_ledger_scalar(value):
    """Coerce one evidence field to something small enough to persist per poll."""
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, str):
        return value if len(value) <= _WRITE_LEDGER_SCALAR_MAX else (
            value[:_WRITE_LEDGER_SCALAR_MAX] + "…[truncated]")
    if isinstance(value, (list, tuple, set)):
        return {"count": len(value)}
    if isinstance(value, dict):
        return {"keys": sorted(str(k) for k in list(value.keys())[:8])}
    text = str(value)
    return text[:_WRITE_LEDGER_SCALAR_MAX]


def _write_ledger_finish(op_id: str, result: dict, status: str) -> None:
    """Close a ledger entry without allowing committed state to regress."""
    try:
        rec = _WRITE_LEDGER.get(op_id) if op_id else None
        if not rec:
            return
        target_status = "committed" if status == "ok" else "failed"
        if rec.get("status") == "committed" and target_status != "committed":
            return
        final_progress = rec.get("progress")
        rec["status"] = target_status
        rec["finished_at"] = time.time()
        rec["updated_at"] = rec["finished_at"]
        rec["duration_ms"] = int((rec["finished_at"] - float(rec["started_at"])) * 1000)
        if isinstance(result, dict):
            summary = {k: _write_ledger_scalar(result.get(k))
                       for k in _WRITE_LEDGER_RESULT_KEYS if k in result}
            validation = result.get("validation")
            if isinstance(validation, dict):
                summary["validation_ok"] = validation.get("ok")
            gates = result.get("meets_gates")
            if isinstance(gates, dict):
                summary["meets_gates"] = {k: bool(v) for k, v in list(gates.items())[:8]}
            if final_progress is not None:
                summary["progress"] = final_progress
            # ISS-10: a summary of only {"ok": true} is what every wrapper tool
            # used to store, because the key list above was written for write_fix
            # and matched nothing else. A caller polling write_status for a
            # cbm_incremental_index then got commit_state=committed with zero
            # evidence of WHAT committed -- indistinguishable from a no-op run.
            # When no evidence key matched, record the result's own top-level key
            # names so the ledger at least says where the evidence lives.
            if set(summary) <= {"ok", "progress"}:
                summary["result_keys"] = sorted(str(k) for k in list(result.keys())[:24])
                summary["summary_note"] = ("no known evidence field on this tool's result; "
                                           "listing top-level keys instead")
            rec["result"] = summary
        _write_ledger_db_upsert(rec)
    except Exception:
        pass


def _write_ledger_heartbeat(op_id: str, progress: dict) -> None:
    """Publish progress; periodically persist it for post-restart recovery."""
    if not op_id:
        return
    with _WRITE_LEDGER_PROGRESS_LOCK:
        rec = _WRITE_LEDGER.get(op_id)
        if not rec or rec.get("status") != "in_flight":
            return
        rec["progress"] = dict(progress or {})
        rec["last_progress_at"] = time.time()
        rec["updated_at"] = rec["last_progress_at"]
        last_persist = float(rec.get("last_progress_persist_at") or 0)
        if rec["last_progress_at"] - last_persist >= 5.0:
            rec["last_progress_persist_at"] = rec["last_progress_at"]
            rec["result"] = {"progress": dict(progress or {})}
            _write_ledger_db_upsert(rec)


def _shape_tool_response_text(tool_name: str, raw: str, max_bytes: int | None = None) -> str:
    """Bound one tool response while preserving status and a stable continuation ref."""
    budget = max(1024, int(max_bytes or _RESULT_BUDGET_BYTES))
    raw_text = raw if isinstance(raw, str) else str(raw)
    if len(raw_text.encode("utf-8")) <= budget:
        return raw_text
    ref = hashlib.sha256(raw_text.encode("utf-8", errors="replace")).hexdigest()
    _result_ref_store(ref, raw_text)
    try:
        source = json.loads(raw_text)
    except Exception:
        source = {"ok": True, "raw": raw_text}
    shaped = {
        "ok": bool(source.get("ok", not source.get("error"))) if isinstance(source, dict) else True,
        "error": source.get("error") if isinstance(source, dict) else None,
        "error_name": source.get("error_name") if isinstance(source, dict) else None,
        "truncated": True,
        "original_bytes": len(raw_text.encode("utf-8")),
        "result_ref": ref,
        # FixA2407 P2-2: the ref is only useful with a documented way to read it back.
        "result_ref_url": f"/api/result/{ref}",
        "read_hint": (f"GET /api/result/{ref}?offset=<byte>&max_bytes=<n> returns the full "
                      "untruncated response in ordered chunks; follow next_offset until null."),
        "excerpt": raw_text[: max(256, budget - 700)],
        "ca_next_action": "read_bounded_result_by_ref_if_more_context_is_required",
    }
    return json.dumps({k: v for k, v in shaped.items() if v is not None}, ensure_ascii=False)


_HARMLESS_IGNORE_FIELDS = {
    # V15 R24: tool_decision_hint accepts harmless context hints (project_path/prompt/idea/scope/hint)
    'tool_decision_hint':       {'project_path', 'prompt', 'idea', 'scope', 'hint'},
    'read_skill':               {'project_path'},
    'optional_tool_probe':      {'project_path'},
    # R43.3: dedup — desktop_screenshot/desktop_window_list/process_tree_inspect/extract_log_section
    # were declared twice; merged into one entry each (set union of both forms).
    'desktop_screenshot':       {'project_path', 'label', 'verbosity'},
    'desktop_window_list':      {'project_path', 'verbosity'},
    'list_projects':            {'project_path'},
    'get_token_meter':          {'project_path'},
    # V15 R24: server_lifecycle_status — CA habit pass project_path; needs port instead
    'server_lifecycle_status':  {'project_path'},
    # V15 R32: cdp_attach_target — CA habit pass title_pattern (no canonical; use list_cdp_targets first)
    'cdp_attach_target':        {'title_pattern', 'title'},
    # V15 R27: verbosity habit — CA passes verbosity to many T4 tools that don't
    # implement it. Silently drop instead of UNSUPPORTED_FIELD.
    'list_cdp_targets':         {'verbosity', 'project_path'},  # R41: project_path harmless
    # V15 R62: check_window_visibility — CA habit pass project_path (only needs hwnd/title_pattern)
    'check_window_visibility':  {'project_path', 'verbosity'},
    'read_console_errors':      {'verbosity', 'project_path'},   # R41
    'read_network_errors':      {'verbosity', 'project_path'},   # R41
    'screenshot_page':          {'verbosity', 'project_path'},   # R41
    # V15 R41 — CA reports passing project_path to T4 tools that don't accept it.
    # Silently drop (consistent with desktop_window_list/list_cdp_targets/list_projects).
    'process_tree_inspect':     {'verbosity', 'project_path', 'filter'},
    'electron_devtools_log':    {'project_path', 'page_id'},  # deprecated R41 — see Deprecation section
    'launch_electron_with_cdp': {'verbosity'},
    'fill_value':               {'verbosity'},
    'click_selector':           {'verbosity'},
    'dom_state_diff':           {'verbosity'},
    # R43.3: extract_log_section merged — was {verbosity} on L270 + {project_path} on L275
    'extract_log_section':      {'verbosity', 'project_path'},
    'invoke_app_function':      {'verbosity'},
    # V15 R42 — pain report driff cleanup
    # R42: folder_stats has alias project_path→path, so DO NOT harmless-drop here
    'folder_dialog_snapshot':   {'project_path'},
    'parse_log_section':        {'project_path'},
    'assert_target_idle':       {'verbosity'},
    'wait_for_idle':            {'verbosity'},
    'bootstrap_session':        set(),
    # R42 — alias_contract_test called internally by acceptance_dashboard with project_path
    'alias_contract_test':      {'project_path', 'verbosity'},
}

def _alias_metadata(tool_name: str) -> dict:
    """Return alias metadata in the Tool Inspector contract shape."""
    return {
        alias: {"maps_to": canonical, "status": "verified"}
        for alias, canonical in _ARG_ALIASES.get(tool_name, {}).items()
    }

# Tools whose project_path defaults to "." when missing (read-only/utility)
_PROJECT_PATH_DEFAULTABLE = {
    'cache_status', 'capability_status', 'workflow_status',
    'get_project_summary', 'cache_delta',
    'next_recommended_action', 'backup_status',
    # Commit E (S3-CONTRACT-01): list_sessions and get_usage_stats were REMOVED
    # from this set. Their zero-payload path silently downgraded to CWD/global
    # data; both handlers now share _resolve_session_query_scope() and require
    # an explicit project_path (or fail with MISSING_REQUIRED_FIELD).
    # V15 R43: T4 runtime_ui / process tools auto-default project_path='.'
    # so session check passes when CA calls without explicit project_path.
    # These tools mutate browser DOM / native UI, not project files.
    'fill_value', 'click', 'fill', 'click_selector',
    'click_and_checkpoint_loop', 'fill_folder_dialog',
    'click_and_handle_folder_dialog', 'cancel_native_dialog',
    'invoke_app_function', 'desktop_click',
    'assert_button_click_changes_state', 'run_script',
    'launch_electron_with_cdp', 'ensure_single_electron_instance',
    'cleanup_test_session',
    'restart_orchestrator_via_task', 'save_log_snapshot',
}

_READONLY_SAFE_PREFIXES = (
    'git log', 'git status', 'git diff', 'git show', 'git branch',
    'git tag', 'git stash list', 'git remote', 'git config --list',
    'ls', 'dir', 'type ', 'echo ',
    'node --version', 'python --version', 'npm --version',
    'python -m py_compile', 'py -m py_compile', 'py_compile', 'pyflakes',
    # A-AUTO-UPGRADE (2026-07-12): pure-read Windows commands so a CA never
    # needs a terminal for inspection work. Anything not on this list (or
    # containing a shell separator) still goes through the full token gate.
    'findstr ', 'where ', 'tree', 'tasklist', 'netstat', 'systeminfo',
    'whoami', 'hostname', 'ver ', 'more ', 'fc ', 'certutil -hashfile',
    'node -v', 'pip list', 'pip show', 'pip --version', 'npm ls',
)
# OpeC7 bughunt F2-F4: read-only flag allowlist per git subcommand so a shared
# verb prefix (git branch/tag/remote) cannot smuggle a mutating flag/positional
# arg (delete/move/create/remove) past the token-free fast-path. Anything not on
# these lists (incl. a positional tag/branch name = create) falls back to the
# token gate — it is not blocked, just no longer token-free.
_GIT_READONLY_SUBFLAGS = {
    'branch': {'-a', '-r', '-v', '-vv', '--list', '--all', '--remotes',
               '--show-current', '--verbose', '--contains', '--merged',
               '--no-merged', '--points-at', '--color', '--no-color'},
    'tag': {'-l', '--list', '-n', '--contains', '--points-at', '--merged',
            '--no-merged', '--sort', '--color', '--no-color'},
    'remote': {'-v', '--verbose'},
}
# CAN-007: `;`, newline/CR (command chaining) and `$(`/backtick (command
# substitution) were missing, so `echo x; rm y` / `echo x\nrm y` passed the
# readonly fast-path and skipped the token gate. Block all shell separators +
# substitution so a chained destructive command can never ride a readonly verb.
_FORBIDDEN_SHELL_TOKENS = ("&&", "||", "&", "|", ">", ">>", "<", ";", "\n", "\r", "`", "$(")


def _is_readonly_command_body(body: dict) -> bool:
    _cmd = body.get('command', [])
    # V15 R47 — shlex.split raises ValueError("No closing quotation") on
    # unbalanced quotes / multiline heredoc payloads. That bubbled up as a
    # 500 Internal Server Error before my R46 PowerShell inline guard could
    # even fire. Safe default on parse failure: treat the command as
    # NOT-readonly so the normal mutation gate (token + session + guard)
    # runs and returns a structured error instead of crashing.
    try:
        _cmd_parts = _cmd if isinstance(_cmd, list) else shlex.split(str(_cmd), posix=False)
    except ValueError:
        return False
    _cmd_parts = [str(part) for part in _cmd_parts]
    # CAN-007: also scan the RAW command for separators that shlex.split consumes
    # (newlines/CR collapse into token boundaries, so a parts-only scan misses them).
    _raw_cmd = _cmd if isinstance(_cmd, str) else ' '.join(str(p) for p in _cmd)
    if any(tok in _raw_cmd for tok in _FORBIDDEN_SHELL_TOKENS):
        return False
    if len(_cmd_parts) >= 2 and _cmd_parts[0].lower() == 'cmd' and _cmd_parts[1].lower() == '/c':
        _cmd_parts = _cmd_parts[2:]
    _cmd_str = ' '.join(_cmd_parts).strip().lower()
    if any(tok in part for part in _cmd_parts for tok in _FORBIDDEN_SHELL_TOKENS):
        return False
    if _cmd_str != 'ver' and not any(_cmd_str.startswith(p) for p in _READONLY_SAFE_PREFIXES):
        return False
    # A-AUTO-UPGRADE hardening (2026-07-12, OpeC7 bughunt F1-F4): a read-only
    # VERB prefix must not smuggle a mutating subcommand/flag past the token
    # gate (e.g. `git branch -D`, `git tag -d`, `git remote remove`). Returning
    # False here is SAFE — it only re-routes the command through the normal
    # token gate, it never blocks a legitimate command outright.
    if len(_cmd_parts) >= 2 and _cmd_parts[0].lower() == 'git':
        _sub = _cmd_parts[1].lower()
        _allow = _GIT_READONLY_SUBFLAGS.get(_sub)
        if _allow is not None:
            for _a in _cmd_parts[2:]:
                if _a.split('=', 1)[0].lower() not in _allow:
                    return False
    # 'ver' is read-only, but startswith('ver') also matched `verify` (cmd
    # write-verify toggle) and `verifier` (kernel driver verifier). The prefix
    # tuple now holds 'ver ' + exact 'ver' so those no longer slip through.
    return True


def _rebase_relative_paths(body: dict, old_root: str, new_root: str) -> None:
    """CAN-048: when the session gate rewrites body['project_path'] from old_root to
    new_root, rebase any RELATIVE file_path/path so it still points to the same
    absolute location (otherwise the relative path silently resolves against the wrong
    root). Mutates body in place; no-op when roots match or the path is absolute."""
    if not old_root or not new_root or old_root == new_root or not isinstance(body, dict):
        return
    for _pk in ("file_path", "path"):
        _pv = body.get(_pk)
        if isinstance(_pv, str) and _pv and not os.path.isabs(_pv):
            try:
                _abs = os.path.normpath(os.path.join(old_root, _pv))
                body[_pk] = os.path.relpath(_abs, new_root)
            except Exception:
                pass


# ─── CAP R3: search_code deprecated-field compatibility table ─────────────────
# Finite, server-side transitional mapping for three legacy fields that agents
# habitually send after S4 registry-first validation rejects them. Evaluated
# BEFORE S4 unsupported-field calculation so canonical fields pass through.
# Removal target: v16.0. Scope: search_code ONLY.
_SEARCH_COMPAT_REMOVAL_VERSION = "v16.0"
_SEARCH_COMPAT_MAX_RESULTS_CAP = 50  # fo.MAX_SEARCH_RESULTS

# Bounded observability counters (tool, field) -> count. Cardinality is fixed
# by the finite table; no payload values in labels.
_COMPAT_USE_TOTAL: dict[tuple[str, str], int] = {}
_COMPAT_AMBIGUOUS_TOTAL: dict[tuple[str, str], int] = {}
_COMPAT_UNKNOWN_TOTAL: dict[tuple[str, str], int] = {}

def _compat_metric_use(tool: str, field: str) -> None:
    key = (tool, field)
    _COMPAT_USE_TOTAL[key] = _COMPAT_USE_TOTAL.get(key, 0) + 1


def _compat_metric_ambiguous(tool: str, field: str) -> None:
    key = (tool, field)
    _COMPAT_AMBIGUOUS_TOTAL[key] = _COMPAT_AMBIGUOUS_TOTAL.get(key, 0) + 1


def _compat_metric_unknown(tool: str, field: str) -> None:
    key = (tool, field)
    _COMPAT_UNKNOWN_TOTAL[key] = _COMPAT_UNKNOWN_TOTAL.get(key, 0) + 1

class _DeprecatedFieldConflict(Exception):
    """Raised when deprecated+canonical fields conflict ambiguously."""
    def __init__(self, field: str, reason: str):
        self.field = field
        self.reason = reason
        super().__init__(f"{field}: {reason}")


def _search_code_deprecated_compat(body: dict) -> tuple[dict, dict]:
    """Normalize deprecated search_code fields to canonical equivalents.

    Returns (body, compat_meta) where compat_meta is empty dict when no
    deprecated fields were present, or contains deprecated_fields,
    canonical_replacements, compatibility_applied, removal_version.

    Raises _DeprecatedFieldConflict on ambiguous/invalid usage.
    """
    deprecated_fields: list[str] = []
    canonical_replacements: dict[str, str] = {}
    tool = "search_code"

    # ── limit -> max_results ──
    if "limit" in body:
        deprecated_fields.append("limit")
        limit_val = body.pop("limit")
        canonical_present = "max_results" in body
        if canonical_present:
            # Both present: accept only if equal
            if body["max_results"] != limit_val:
                _compat_metric_ambiguous(tool, "limit")
                raise _DeprecatedFieldConflict(
                    "limit",
                    f"conflicts with max_results={body['max_results']!r} (limit={limit_val!r})")
            # Equal values: canonicalize (drop limit, keep max_results)
            canonical_replacements["limit"] = "max_results"
            _compat_metric_use(tool, "limit")
        else:
            # Map only when valid integer in canonical range
            if not isinstance(limit_val, int) or isinstance(limit_val, bool):
                _compat_metric_ambiguous(tool, "limit")
                raise _DeprecatedFieldConflict(
                    "limit", f"must be a valid integer, got {type(limit_val).__name__}")
            if limit_val < 1 or limit_val > _SEARCH_COMPAT_MAX_RESULTS_CAP:
                _compat_metric_ambiguous(tool, "limit")
                raise _DeprecatedFieldConflict(
                    "limit",
                    f"must be in range [1, {_SEARCH_COMPAT_MAX_RESULTS_CAP}], got {limit_val}")
            body["max_results"] = limit_val
            canonical_replacements["limit"] = "max_results"
            _compat_metric_use(tool, "limit")

    # ── path_glob -> file_pattern ──
    if "path_glob" in body:
        deprecated_fields.append("path_glob")
        glob_val = body.pop("path_glob")
        canonical_present = "file_pattern" in body
        if canonical_present:
            if body["file_pattern"] != glob_val:
                _compat_metric_ambiguous(tool, "path_glob")
                raise _DeprecatedFieldConflict(
                    "path_glob",
                    f"conflicts with file_pattern={body['file_pattern']!r} (path_glob={glob_val!r})")
            canonical_replacements["path_glob"] = "file_pattern"
            _compat_metric_use(tool, "path_glob")
        else:
            if not isinstance(glob_val, str) or not glob_val.strip():
                _compat_metric_ambiguous(tool, "path_glob")
                raise _DeprecatedFieldConflict(
                    "path_glob", f"must be a non-empty string, got {type(glob_val).__name__}")
            body["file_pattern"] = glob_val
            canonical_replacements["path_glob"] = "file_pattern"
            _compat_metric_use(tool, "path_glob")

    # ── literal_search -> mode ──
    if "literal_search" in body:
        deprecated_fields.append("literal_search")
        ls_val = body.pop("literal_search")
        if not isinstance(ls_val, bool):
            _compat_metric_ambiguous(tool, "literal_search")
            raise _DeprecatedFieldConflict(
                "literal_search", f"must be a boolean, got {type(ls_val).__name__}")
        intended_mode = "text" if ls_val else "regex"
        explicit_mode = body.get("mode", "")
        # Resolve mode aliases for conflict detection
        _mode_alias_map = {
            "plain": "text", "literal": "text", "raw": "text", "exact": "text",
            "string": "text", "fixed": "text",
            "re": "regex", "pattern": "regex", "regexp": "regex",
        }
        resolved_mode = _mode_alias_map.get(explicit_mode, explicit_mode) if explicit_mode else ""
        if resolved_mode and resolved_mode != intended_mode:
            _compat_metric_ambiguous(tool, "literal_search")
            raise _DeprecatedFieldConflict(
                "literal_search",
                f"={ls_val} implies mode={intended_mode!r} but explicit mode={explicit_mode!r} "
                f"(resolved: {resolved_mode!r})")
        # literal_search:true + regex alias in body (pattern/regex keys imply regex)
        if ls_val and ("pattern" in body or "regex" in body):
            _compat_metric_ambiguous(tool, "literal_search")
            raise _DeprecatedFieldConflict(
                "literal_search",
                "=true conflicts with regex-implying alias (pattern/regex) in payload")
        # literal_search:false + explicit text mode
        if not ls_val and resolved_mode == "text":
            _compat_metric_ambiguous(tool, "literal_search")
            raise _DeprecatedFieldConflict(
                "literal_search",
                "=false conflicts with explicit mode='text'")
        # Apply: set mode if not already explicitly set
        if not explicit_mode:
            body["mode"] = intended_mode
        canonical_replacements["literal_search"] = "mode"
        _compat_metric_use(tool, "literal_search")

    if not deprecated_fields:
        return body, {}

    meta = {
        "deprecated_fields": sorted(deprecated_fields),
        "canonical_replacements": canonical_replacements,
        "compatibility_applied": True,
        "removal_version": _SEARCH_COMPAT_REMOVAL_VERSION,
    }
    return body, meta

def _apply_alias_mode_default_post_s4(
    tool_name: str, body: dict, applied: list
) -> list:
    """LIVE-GAP-01 (2026-08-10): inject the internal ``mode`` default for the
    legacy search_code alias tools AFTER the S4 public-schema validation has
    accepted the caller's body.

    Why this helper exists: the registry TOOL_MAP dispatches
    ``query_dom_candidates`` / ``find_callers`` / ``find_definition_and_usage``
    directly to ``search_code``. ``search_code`` branches on a ``mode`` kwarg
    (text|regex|symbol|callers|definition_usage|dom_candidates), so the
    dispatcher must supply a sensible default per alias. The alias's PUBLIC
    schema deliberately does NOT declare a ``mode`` property — it is internal
    delegation metadata, not user input. Injecting the default in
    ``_normalize_body`` (i.e. BEFORE S4) caused S4 to reject the injected
    field as ``UNSUPPORTED_FIELD``, which broke the live find_callers alias
    in 0443b67 and latently broke query_dom_candidates from FixA2407 P1-2.

    Invariants preserved by running post-S4:
      1) A caller-supplied ``mode`` on these aliases is still rejected by S4
         (the public contract does not expose mode), so user intent cannot
         silently reroute the alias to a different search_code branch.
      2) When no caller-supplied ``mode`` was present (the normal case), the
         alias gains the documented mode so search_code routes to the
         correct branch and returns isolated callers/definitions/
         dom_candidates rather than literal-text hits.
    """
    _MODE_DEFAULTS = {
        "query_dom_candidates": "dom_candidates",
        "find_callers": "callers",
        "find_definition_and_usage": "definition_usage",
    }
    default = _MODE_DEFAULTS.get(tool_name)
    if default is None:
        return applied
    if "mode" in body:
        # Caller somehow supplied mode and S4 let it through — preserve it
        # (defensive; in practice S4 rejects it before this point).
        return applied
    body["mode"] = default
    return list(applied) + [
        f"mode defaulted to '{default}' (dispatcher, post-S4)"
    ]


def _normalize_body(tool_name: str, body: dict) -> tuple[dict, list]:
    """Apply alias renames + project_path defaults. Returns (normalized_body, applied_changes).

    V15 R19: harmless-ignore set is applied BEFORE alias map so CA's habit fields
    (e.g. project_path on read_skill) are silently dropped instead of returning
    UNSUPPORTED_FIELD. Each drop is logged in `applied` for audit transparency.
    """
    if not isinstance(body, dict):
        return body, []
    applied = []
    # FixA2407 P1-2 (updated LIVE-GAP-01, 2026-08-10): legacy search aliases
    # (query_dom_candidates, find_callers, find_definition_and_usage) must
    # carry their mode preset when dispatching to search_code. The injection
    # CANNOT live here in _normalize_body because S4 (public-schema field
    # validation) runs AFTER normalize and rejects the injected `mode` as
    # UNSUPPORTED_FIELD — none of these tools declare `mode` in their public
    # schema (it is internal delegation metadata for search_code). The
    # injection therefore runs in the dispatcher right after S4, in the block
    # marked LIVE-GAP-01 below. A caller-supplied `mode` would still be
    # rejected at S4 (correct behavior — the alias's public contract does not
    # expose mode); the dispatcher-side default is what bypasses S4.
    if tool_name == 'find_definition_and_usage' and 'symbol' in body and 'query' not in body:
        body['query'] = body.pop('symbol')
        applied.append('symbol->query')
    # CAN-009: apply alias renames FIRST, so a field that is both an alias source and
    # (incidentally) in the harmless-ignore set is renamed to its canonical key instead
    # of being silently dropped (data loss). Harmless-ignore runs afterwards.
    aliases = _ARG_ALIASES.get(tool_name, {})
    if tool_name == 'search_code' and 'is_regex' in body:
        is_regex = body.pop('is_regex')
        if not body.get('mode'):
            body['mode'] = 'regex' if is_regex else 'text'
        applied.append('is_regex->mode')
    # A15 (2026-08-16): a BOOLEAN `regex` is a FLAG, not a field name — its VALUE
    # selects the mode. Consume it here (pop) so it never reaches the alias loop
    # (`regex`->`query`) and so `regex: false` correctly yields mode='text'
    # instead of being misread as "enable regex" (the old presence-only test
    # blew up with REGEX_INPUT_BUDGET_EXCEEDED / REGEX_INVALID on literal input).
    # A STRING `regex` is left in place: it is a field-name alias for `query`,
    # handled by the presence block + alias loop below.
    if tool_name == 'search_code' and isinstance(body.get('regex'), bool):
        regex_flag = body.pop('regex')
        if not body.get('mode'):
            body['mode'] = 'regex' if regex_flag else 'text'
        applied.append('regex(bool)->mode')
    # Agent-enablement (2026-08-08): a 'pattern'/'regex' caller-supplied field
    # (a STRING field-name alias for `query`) semantically implies a regex
    # search. The original code injected literal_search=False (rejected by S4 as
    # UNSUPPORTED_FIELD). The registry-blessed way is mode='regex'. Only set when
    # caller did not supply an explicit mode. A boolean `regex` was already
    # consumed above, so `regex` here can only be a string field-name alias.
    if tool_name == 'search_code' and not body.get('mode'):
        if 'pattern' in body or 'regex' in body:
            body['mode'] = 'regex'
            applied.append('pattern/regex->mode=regex')
    # CAP R3: deprecated-field compatibility (limit/path_glob/literal_search).
    # Runs BEFORE alias loop and S4 so canonical fields pass validation.
    if tool_name == 'search_code':
        body, _compat_meta = _search_code_deprecated_compat(body)
        if _compat_meta:
            body['_compat_meta'] = _compat_meta
            applied.append(f"compat: {', '.join(sorted(_compat_meta['deprecated_fields']))}")
    for old, new in aliases.items():
        if old in body and new not in body:
            body[new] = body.pop(old)
            applied.append(f"{old}->{new}")
        elif old in body and new in body:
            # Agent-enablement F1 (2026-08-08): canonical+alias both supplied.
            # "Canonical wins" previously left the alias key in body, so S4
            # computed unsupported from remaining keys and returned
            # UNSUPPORTED_FIELD (e.g. find_symbol {name, symbol, project_path}).
            # Consume/drop the duplicate alias — canonical value is preserved.
            body.pop(old)
            applied.append(f"{old} dropped (canonical '{new}' wins)")
            logger.debug(
                "Tool %s: dropped duplicate alias %r (canonical %r present)",
                tool_name, old, new,
            )
    # R19 + CAN-009: drop harmless-ignore fields AFTER aliasing (no 400 retry needed).
    ignore_set = _HARMLESS_IGNORE_FIELDS.get(tool_name, set())
    for k in list(body.keys()):
        if k in ignore_set:
            body.pop(k, None)
            applied.append(f"{k} (ignored - harmless for {tool_name})")
    if tool_name in _PROJECT_PATH_DEFAULTABLE and not body.get('project_path'):
        # V15 R44 — only auto-default project_path when the tool's function
        # signature actually accepts it. R43 added page-bound T4 tools
        # (fill_value/click/...) to DEFAULTABLE so the session guard would
        # pass, but their signatures don't include project_path → the
        # injection caused UNSUPPORTED_FIELD downstream. Session guard now
        # resolves via page_id state (R44), so the auto-default is no
        # longer required for those tools.
        _fn_obj = ct.TOOL_MAP.get(tool_name) if hasattr(ct, "TOOL_MAP") else None
        _accepts_pp = False
        if _fn_obj is not None:
            try:
                _accepts_pp = 'project_path' in inspect.signature(_fn_obj).parameters
            except Exception:
                _accepts_pp = False
        if _accepts_pp:
            body['project_path'] = '.'
            applied.append("project_path defaulted to '.'")
    # V15 R34: Windows path normalization at API boundary
    # Forward slash `/` -> backslash `\` only for UNC-style paths. Drive-letter
    # paths such as C:/repo are valid JSON transport values and must keep their
    # slash style so API normalization does not rewrite caller intent.
    # Audit trail surfaced via _normalized_paths so CA sees what was rewritten.
    for k in list(body.keys()):
        if k not in _PATH_KEYS:
            continue
        v = body[k]
        if not isinstance(v, str):
            continue
        # Compatibility policy: trim C0 controls at path edges (legacy clients
        # may wrap paths in CR/LF/TAB), but never silently join path segments by
        # removing an interior control. Audit only escaped code points so raw C0
        # characters cannot leak into responses or logs.
        original = v
        v = _CONTROL_CHAR_EDGE_RE.sub("", v)
        if v != original:
            body[k] = v
            stripped_codes = sorted({
                f"U+{ord(character):04X}" for character in original
                if ord(character) < 0x20 and character not in v
            })
            body.setdefault("_normalized_paths", []).append(
                {"key": k, "stripped_control_codes": stripped_codes}
            )
            applied.append(f"{k}: edge control chars stripped")
        path_runtime.reject_c0_control_characters(v, field=k)
        # CASE-1919 item 3: a RELATIVE project_path has no base except the DAEMON's
        # own working directory, which is not the caller's -- [A] resolves by
        # absolute project_path, never by the CA's cwd. Measured 2026-09-19:
        # project_path="." returned ok:true / count:1 and said nothing, so the CA
        # could not tell the answer came from another project. Disclose it; do NOT
        # rewrite the value, because the existing multi-base resolver (the one that
        # reports tried_paths) must keep its fallbacks. project_path ONLY: every
        # other path key is resolved against project_path by the handlers, so
        # re-basing them here would break the documented relative-file_path form.
        if k == "project_path" and v and not _looks_like_windows_path(v):
            try:
                _resolved_pp = str(Path(v).resolve())
            except Exception:
                _resolved_pp = ""
            body["_relative_project_path"] = {
                "project_path_was_relative": True,
                "sent": v,
                "resolved_against": os.getcwd(),
                "resolved_candidate": _resolved_pp,
                "ca_next_action": "resend_with_absolute_project_path",
                "why": ("A relative project_path is resolved against the [A] daemon's "
                        "working directory, not yours. This answer may describe a "
                        "different project than the one you meant; pass the absolute "
                        "project root to make it deterministic."),
            }
        if "/" not in v:
            continue
        if not _looks_like_windows_path(v):
            continue
        if re.match(r"^[A-Za-z]:/", v):
            continue
        original = v
        v = v.replace("/", "\\")
        if v.startswith("\\\\"):
            head, tail = v[:2], v[2:]
            while "\\\\" in tail:
                tail = tail.replace("\\\\", "\\")
            v = head + tail
        else:
            while "\\\\" in v:
                v = v.replace("\\\\", "\\")
        if v != original:
            body[k] = v
            body.setdefault("_normalized_paths", []).append(
                {"key": k, "from": original, "to": v}
            )
            applied.append(f"{k}: forward-slash normalized")
    return body, applied


# V15 R34: path-key whitelist + heuristic for Windows-path detection
_PATH_KEYS = {
    "project_path", "file_path", "folder_path", "path", "script_path",
    "source_path", "target_path", "destination", "dest", "src",
    "cwd", "working_dir", "helper_path", "log_path", "artifact_path",
    "path_a", "path_b", "input_path", "output_path",
}

# C0 controls at path edges remain compatibility-trimmed.
# Any C0 left after that trim is rejected before normalization or OS access.
_CONTROL_CHAR_EDGE_RE = re.compile(r"^[\x00-\x1f]+|[\x00-\x1f]+$")

def _looks_like_windows_path(v: str) -> bool:
    """R34: detect drive-letter (`X:`) or UNC (`\\\\server`, `//server`) prefix."""
    if not v or len(v) < 2:
        return False
    if v.startswith("http://") or v.startswith("https://") or v.startswith("ftp://"):
        return False
    if v[1] == ":" and v[0].isalpha():
        return True
    if v.startswith("\\\\") or v.startswith("//"):
        return True
    return False


_PAYLOAD_EXAMPLES = {
    "open_session": {"session_title": "T1 fix", "project_path": "D:/path/to/project"},
    "get_project_summary": {"project_path": "D:/path/to/project"},
    "get_token_meter": {"project_path": "D:/path/to/project"},  # R33: weak schema fix
    # V15 R35 — MD workflow dispenser
    "workflow_list": {"project_path": "D:/path/to/project"},
    "workflow_start": {"name": "rawcut-getfootage-sheet", "project_path": "D:/path/to/project"},
    "workflow_get_current": {"name": "rawcut-getfootage-sheet", "project_path": "D:/path/to/project"},
    "workflow_submit_step": {
        "name": "rawcut-getfootage-sheet",
        "step": 3,
        "status": "done",
        "evidence": "Clicked button, dialog appeared",
        "project_path": "D:/path/to/project",
    },
    "workflow_retry_step": {"name": "rawcut-getfootage-sheet", "project_path": "D:/path/to/project"},
    "workflow_skip_step": {"name": "rawcut-getfootage-sheet", "reason": "manual review", "project_path": "D:/path/to/project"},
    "workflow_reset": {"name": "rawcut-getfootage-sheet", "project_path": "D:/path/to/project"},
    "workflow_status": {"name": "rawcut-getfootage-sheet", "project_path": "D:/path/to/project"},
    "workflow_create_from_ui": {
        "name": "rawcut-getfootage-sheet",
        "body_md": "# title\n\n## Step 1\n### sub\nbody",
        "scope": "global",
        "overwrite": False,
    },
    # V15 R36 — reverse flow + active state
    "workflow_save_md": {
        "name": "rawcut-getfootage-sheet",
        "body_md": "# title\n\n## Step 1\n### sub\nbody",
        "scope": "global",
        "overwrite": False,
    },
    "workflow_read_md": {"name": "rawcut-getfootage-sheet", "project_path": "D:/path/to/project"},
    "workflow_draft_create": {
        "name": "rawcut-getfootage-sheet",
        "body_md": "# title\n\n## Step 1\n### sub\nbody",
        "ttl_sec": 3600,
    },
    "workflow_draft_get": {"token": "<token-from-workflow_draft_create>"},
    "workflow_activate": {"name": "rawcut-getfootage-sheet", "project_path": "D:/path/to/project"},
    "workflow_deactivate": {"project_path": "D:/path/to/project"},
    "workflow_active_get": {"project_path": "D:/path/to/project"},
    # V15 R38 — folder_dialog_snapshot: tool KHÔNG accept project_path (R37 issue report).
    # Provide accurate example so error fallback không mâu thuẫn với schema.
    "folder_dialog_snapshot": {
        "owner_pid": 0,
        "parent_hwnd": 0,
        "title_pattern": "",
        "include_child_controls": True,
    },
    "create_folder": {
        "path": "D:/path/to/output_folder",
        "recursive": True,
    },
    "create_folders": {
        "paths": ["D:/path/to/output_a", "D:/path/to/output_b"],
        "recursive": True,
    },
    # R43.3: removed duplicate `cancel_native_dialog` — superset entry exists later in this dict with _failure_note
    "fill_folder_dialog": {
        "title_pattern": "Select Folder",
        "folder_path": "D:\\\\path\\\\to\\\\target",
        "owner_pid": 0,
        "wait_for_dialog_ms": 3000,
    },
    # V15 R34 — Post-action classifier + R35 universal idle gate
    "classify_get_footage_outcome": {
        "project_path": "D:/path/to/project",
        "since_ts": "2026-05-19T16:00:00",
        "output_folder": "D:/path/to/output",
    },
    "verify_active_electron_source": {
        "page_id": "<page-id from list_cdp_targets>",
        "expected_path": "D:\\\\absolute\\\\path\\\\to\\\\Source\\\\ui\\\\index.html",
        "cdp_port": 9223,
    },
    # V15 R42 — new tool examples
    "bootstrap_session": {"project_path": "D:/path/to/project"},
    "check_server_path": {"path": "\\\\\\\\server\\\\share\\\\folder", "mode": "auto", "timeout_sec": 5},
    "workflow_goto_step": {
        "name": "rawcut_getfootage_sheet_v1",
        "target_step": 10,
        "reason": "V2 replay needs setup from step 10",
        "project_path": "D:/path/to/project",
    },
    "wait_for_idle": {
        "signals": [
            {"kind": "folder_growth_stable", "folder": "D:/path/to/output", "stable_seconds": 3},
        ],
        "stable_seconds": 20,
        "timeout_seconds": 900,
        "check_interval_sec": 30,
    },
    "parse_log_section": {
        "page_id": "cdp-...-0",
        "section_title": "System Error Log",
        "decode_base64": True,
        "parse_nested_json": True,
    },
    "assert_target_idle": {
        "signals": [
            {"kind": "folder_growth_stable", "folder": "D:/path/to/output", "stable_seconds": 3},
            {"kind": "process_cpu_idle", "process_name": "Rawcut.exe", "idle_threshold_pct": 5, "stable_seconds": 3},
        ],
        "window_seconds": 3,
    },
    "query_index": {"project_path": "D:/path/to/project", "file_pattern": "*.py", "max_results": 20},
    "find_symbol": {"project_path": "D:/path/to/project", "name": "function_name", "context_lines": 20},
    "refresh_project_map": {"project_path": "D:/path/to/project"},
    "get_folder_size": {"folder_path": "D:/path/to/project"},
    "log_native": {"native": []},
    "read_file_range": {
        "project_path": "D:/path/to/project",
        "file_path": "relative/file.py",
        "scope": "function",
        "symbol": "ClassName.method_name",
        "token_budget": 2000,
    },
    "read_context_batch": {
        "project_path": "D:/path/to/project",
        "requests": [
            {"file_path": "relative/file.py", "scope": "file_summary", "priority": 1},
            {"file_path": "relative/other.py", "scope": "function", "symbol": "run", "priority": 2},
        ],
        "token_budget": 6000,
    },
    "architecture_context_pack": {
        "project_path": "D:/path/to/project",
        "symbol": "ClassName.method_name",
        "depth": 2,
        "token_budget": 6000,
    },
    # P4: the base64 examples below are canonical - set content_encoding="base64"
    # whenever new_content is multiline or contains backslashes/quotes/non-ASCII text.
    "write_fix": {
        "project_path": "D:/path/to/project",
        "file_path": "relative/file.py",
        "start_line": 10,
        "end_line": 20,
        "new_content": "<base64 utf-8 content>",
        "content_encoding": "base64",
        "dry_run": True,
    },
    "write_fix_html": {
        "project_path": "D:/path/to/project",
        "file_path": "relative/page.html",
        "start_line": 10,
        "end_line": 20,
        "new_content": "<base64 utf-8 content>",
        "content_encoding": "base64",
    },
    "write_transaction": {
        "project_path": "D:/path/to/project",
        "operations": [
            {
                "tool": "write_fix",
                "file_path": "relative/file.py",
                "start_line": 10,
                "end_line": 20,
                "new_content": "<base64 utf-8 content>",
                "content_encoding": "base64",
            }
        ],
        "backup": True,
        "structural_check": True,
    },
    "write_new_file": {
        "project_path": "D:/path/to/project",
        "path": "relative/new_file.py",
        "new_content": "<base64 utf-8 content>",
        "content_encoding": "base64",
        "dry_run": True,
    },
    "apply_after_dry_run": {"project_path": "D:/path/to/project", "dry_run_id": "dry_..."},
    "validate_encoding": {"project_path": "D:/path/to/project", "file_path": "relative/file.py"},
    "validate_file": {"project_path": "D:/path/to/project", "file_path": "relative/file.py"},
    "post_write_batch_verify": {"project_path": "D:/path/to/project", "changed_files": ["relative/file.py"]},
    "cache_delta": {"project_path": "D:/path/to/project"},
    # R43.3 H6: replaced `tool_inspector` probe with `/api/tools?name_contains=` (canonical post-R16.9)
    "optional_tool_probe": {"tools": ["/api/tools?name_contains=", "/api/token_panel", "path_quote_helper"]},
    "create_pending_audit": {
        "project_path": "D:/path/to/project",
        "action_type": "read",
        "tool_name": "Read",
        "instead_of": "read_file_range",
        "failed_error": "Orchestrator unavailable",
    },
    "sync_pending_audit": {"project_path": "D:/path/to/project"},
    # V15 R16 payload examples
    "cdp_attach_target": {
        "websocket_url": "ws://localhost:9223/devtools/browser/<browser-id-from-/json/version>",
        "cdp_port": 9223,
    },
    "fill_value": {
        "page_id": "cdp-...-0",
        "selector": "#manualInput",
        "value": "<base64 of UTF-8 text with backslashes / Unicode>",
        "encoding": "base64",
    },
    "evaluate_js": {
        "page_id": "cdp-...-0",
        "expression": "(()=>{const b=document.getElementById('btnStep1'); return {disabled:b.disabled, bg:getComputedStyle(b).backgroundColor}})()",
    },
    "list_cdp_targets": {"cdp_port": 9223},
    "desktop_window_list": {"title_filter": "Rawcut"},
    "read_console_errors": {"page_id": "cdp-...-0", "level": "error"},
    "check_window_visibility": {"title_pattern": "Rawcut", "match_mode": "contains_case_insensitive"},
    "inspect_project_runtime": {"project_path": "D:/path/to/electron-app"},
    "launch_electron_with_cdp": {
        "project_path": "D:/path/to/electron-app",
        "cdp_port": 9223,
        "verify_after_ms": 6000,
    },
    "dom_state_diff": {
        "page_id": "cdp-...-0",
        "selector": "#btnGetFootage",
        "snapshot_fields": ["disabled", "bg", "color", "className", "visible"],
    },
    "extract_log_section": {
        "page_id": "cdp-...-0",
        "section_title": "System Error Log",
        "area_class": "log-area",
        "max_chars": 2000,
        "tail": True,
    },
    "query_dom_candidates": {
        "page_id": "cdp-...-0",
        "filter": "textareas",
        "max_items": 50,
        "_accepted_filters_doc": ["all", "inputs", "buttons", "textareas", "links", "contenteditable"],
    },
    "describe_page": {
        "page_id": "cdp-...-0",
        "area_class": "log-area",
        "view_id_pattern": "^view-",
    },
    "cleanup_test_session": {
        "project_path": "D:/path/to/electron-app",
        "page_id": "cdp-...-0",
        "processes": ["electron.exe", "chrome.exe"],
    },
    # R16.10: schema examples for R16.9 + R16.10 tools — CA round 5 retro showed
    # these were getting field names wrong (max_chars vs max_lines, etc.)
    "read_skill": {
        "name": "18",
        "max_lines": 400,
    },
    "click_selector": {
        "page_id": "cdp-...-0",
        "selector": "#btnStep1",
        "before_after_state": True,
        "state_fields": ["disabled", "bg", "className"],
        "timeout_ms": 5000,
    },
    "checkpoint_loop_dom_state": {
        "page_id": "cdp-...-0",
        "selector": "#btnStep3",
        "interval_sec": 15,
        "timeout_sec": 90,
        "fields": ["disabled", "bg", "className"],
        "stop_on_change": True,
        "baseline_id": "dom-... (from prior dom_state_diff)",
        "_baseline_alternatives_doc": "Pass baseline_id OR baseline_fields={disabled:true,bg:'rgb(...)'} OR leave empty for fresh-at-t0",
    },
    "click_and_checkpoint_loop": {
        "page_id": "cdp-...-0",
        "click_selector_str": "#btnStep1",
        "watch_selector": "#btnStep3",
        "interval_sec": 15,
        "timeout_sec": 90,
        "fields": ["disabled", "bg"],
        "stop_on_change": True,
        "pre_click_settle_ms": 200,
    },
    "ui_state_summary": {
        "page_id": "cdp-...-0",
        "regions": [
            {"label": "Header", "selector": "#header"},
            {"label": "Logs", "selector": "#systemLogArea"},
            {"label": "Buttons", "selector": "#btnRow"},
        ],
    },
    "checkpoint_loop_dom_state_baseline_fields_example": {
        "page_id": "cdp-...-0",
        "selector": "#btnStep3",
        "baseline_fields": {"disabled": True, "bg": "rgb(150,150,150)", "className": "btn--idle"},
        "interval_sec": 15,
        "timeout_sec": 90,
    },
    "ensure_single_electron_instance": {
        "project_path": "D:/path/to/electron-app",
        "cdp_port": 9223,
        "kill_existing": True,
    },
    # V15 R28 — Weak-schema enrichment (per audit Round 12)
    # R43.3: renamed key — was duplicate `workflow_status` (MD runner version at L416 won),
    # which silently shadowed this R28 enrichment for the T1 registry inspector.
    # Now matches the post-R43.2 rename (workflow_status_registry).
    "workflow_status_registry": {
        "project_path": "D:/path/to/project",
        "_note": "Returns mode/cache_status/drift_warning + R28 stale_decision field for T1/T2/T4 routing.",
    },
    "query_selector": {
        "page_id": "cdp-...-0",
        "selector": "#btnStep1",
        "_visibility_note": "Returns {exists, visible, bbox, text_preview}. Use visible=false to detect hidden elements.",
        "_example_selectors": ["#main", ".btn-primary", "button[aria-label='Submit']"],
    },
    "fill": {
        "page_id": "cdp-...-0",
        "selector": "#input",
        "value": "plain text only",
        "_warning_paths": "DO NOT use for UNC paths / backslash content — JSON strips. Use fill_value(encoding='base64') instead.",
    },
    "read_network_errors": {
        "page_id": "cdp-...-0",
        "_filter_note": "No filter/max_entries fields in current contract; returns all failed+4xx+5xx.",
    },
    "port_owner_inspect": {
        "port": 9223,
        "_example_windows": "Returns {pid, process_name, exe_path}. For Electron CDP port 9223: expect electron.exe or npx electron parent.",
    },
    "detect_stale_server": {
        "port": 3000,
        "expected_bundle_version": "v1.2.3",
        "_decision_note": "stale + drift → BLOCKER for T2 write; warning for T1; restart only with probe evidence for T4 (see skill 16 R28 decision table).",
    },
    "static_asset_cache_check": {
        "url": "http://localhost:3000/main.js",
        "_hint": "Compares HEAD ETag + GET hash. cache_stale=true → consider hard-reload (Ctrl+F5) before assuming code change deployed.",
    },
    "cancel_native_dialog": {
        "title_pattern": "Select Folder",
        "button_pattern": "Cancel|Hủy|Close",
        "_failure_note": "On DIALOG_NOT_FOUND returns visible_windows[] for CA diagnostic (parity with fill_folder_dialog).",
    },
    "assert_text_visible": {
        "page_id": "cdp-...-0",
        "selector": ".status",
        "expected_text": "Complete",
        "timeout_ms": 10000,
        "_timeout_behavior": "Polls until text visible OR timeout. timeout_ms=0 = single check, no wait.",
    },
    "assert_button_click_changes_state": {
        "page_id": "cdp-...-0",
        "selector": "#btnSubmit",
        "watch_fields": ["disabled", "bg", "className"],
        "timeout_ms": 5000,
        "_mutating_note": "R28: protected — clicks button. Use click_and_checkpoint_loop for richer evidence.",
    },
    "get_validation_queue": {
        "project_path": "D:/path/to/project",
        "_lifecycle": "Returns pending dry_run_ids from write_fix/write_new_file/write_fix_html. Use apply_after_dry_run(dry_run_id=...) to commit.",
    },
    "record_ui_flow": {
        "page_id": "cdp-...-0",
        "output_path": "D:/path/to/trace.zip",
        "_status": "EXPERIMENTAL: Playwright tracing stub. Use only when test fixture is set up.",
    },
    "process_tree_inspect": {
        "root_pid": 12345,
        "name_pattern": "electron",
        "_note": "name_pattern is a case-insensitive substring, not regex. Returns ancestor + descendant tree.",
    },
    # V15 R30: protected_tools_status takes NO args — override default placeholder
    "protected_tools_status": {
        "_note": "Takes NO arguments. Pass empty object {}. Returns token-protection status only.",
    },
    # V15 R29: payload examples for 4 remaining weak-schema tools
    "wait_for_selector": {
        "page_id": "cdp-...-0",
        "selector": "#mainPanel",
        "timeout_ms": 10000,
        "_note": "Polls until visible OR timeout. Returns {visible, elapsed_ms}.",
    },
    "restart_server_via_bat_hint": {
        "bat_path": "D:/path/to/project/start_orchestrator.bat",
        "port": 8083,
        "_status": "HINT ONLY — returns command string. Does NOT execute. Mark non-mutating.",
    },
    "electron_devtools_log": {
        "cdp_port": 9223,
        "_note": "Returns CDP /json/list output. Compatible với list_cdp_targets.",
    },
    "find_definition_and_usage": {
        "symbol": "myFunction",
        "project_path": "D:/path/to/project",
        "context_lines": 5,
    },
    "save_log_snapshot": {
        "page_id": "cdp-...-0",
        "section_title": "System Error Log",
        "project_path": "D:/path/to/proj",
        "round_id": "r-1779...",
        "_note": "Returns {path, sha256, length, head_200, tail_200} — cite in final report instead of pasting full log",
    },
    "save_log_snapshot_by_selector": {
        "page_id": "cdp-...-0",
        "selector": "#systemLogArea",
        "project_path": "D:/path/to/proj",
        "round_id": "r-1779...",
        "artifact_label": "systemLog",
    },
    "declare_mission": {
        "mission": "test Electron Rawcut UI: launch, fill manual input, click Step 1, watch Step 3 state, extract log",
        "context_hints": ["electron", "ui", "log", "cdp"],
        "project_path": "D:/path/to/proj",
        "top_skills": 5,
        "top_tools": 20,
    },
    "read_all_logs_auto_artifact": {
        "page_id": "cdp-...-0",
        "include_view_panels": True,
        "auto_artifact": True,
        "project_path": "D:/path/to/proj",
        "round_id": "r-...",
        "auto_artifact_max_bytes": 4096,
    },
    "read_all_logs": {
        "page_id": "cdp-...-0",
        "max_chars": 30000,
        "include_view_panels": True,
        "view_id_pattern": "^view-",
    },
    "read_value": {
        "page_id": "cdp-...-0",
        "selector": "#systemLogArea",
        "encoding": "plain",
        "max_chars": 100000,
    },
    "run_script": {
        "page_id": "cdp-...-0",
        "script_path": "D:/path/to/script.js",
        "_iife_hint": "Script content is eval'd at top level → wrap in IIFE: `(()=>{ ...; return X })()`",
    },
    "run_project_command": {
        "project_path": "D:/path/to/electron-app",
        "command": "npx electron . --remote-debugging-port=9223",
        "background": True,
        "expected_port": 9223,
        "verify_after_launch_ms": 6000,
    },
}

_RUN_TEST_PROFILES = list(getattr(ct, "SUPPORTED_TEST_PROFILES", ["unittest", "pytest", "py_compile_core", "py_compile", "npm_test"]))


def _infer_error_name(tool_name: str, error: str, body: dict | None = None) -> str:
    lower = (error or "").lower()
    body = body if isinstance(body, dict) else {}
    if "permission denied" in lower:
        return "FILE_ACCESS_DENIED"
    if "token" in lower:
        return "MUTATION_TOKEN_REQUIRED"
    if "active session" in lower:
        return "ACTIVE_SESSION_REQUIRED"
    if "session not found" in lower:
        return "SESSION_NOT_FOUND"
    if "backup" in lower:
        return "BACKUP_REQUIRED"
    # R43.3 C2: precedence bug — Python `and` binds tighter than `or`, so previous
    # form `"stale" or ("index" AND "refresh")` matched ANY error containing "stale".
    # Add parens to require both "stale" context OR (index AND refresh) pair.
    if ("stale" in lower and "index" in lower) or ("index" in lower and "refresh" in lower):
        return "STALE_INDEX"
    if "changed outside" in lower or "mtime" in lower or "modified outside" in lower:
        return "FILE_CHANGED_OUTSIDE_ORCHESTRATOR"
    if "profile" in lower and "invalid" in lower:
        return "RUN_TEST_PROFILE_INVALID"
    if "project_path" in lower or "project root" in lower:
        return "PROJECT_PATH_REQUIRED"
    if "dry_run" in body and tool_name in {"write_fix_html"}:
        return "UNSUPPORTED_DRY_RUN"
    if "unexpected keyword argument" in lower or "unsupported" in lower:
        return "UNSUPPORTED_FIELD"
    if "wrong arguments" in lower or "required positional argument" in lower:
        return "WRONG_ARGUMENTS"
    if any(marker in lower for marker in (
        "json decode", "json parse", "json encoding", "invalid json payload",
    )):
        return "WRONG_ARGUMENTS"
    return "TOOL_ERROR"


def _ca_next_action(error_name: str, tool_name: str) -> str:
    return {
        "MUTATION_TOKEN_REQUIRED": "fetch_token_from_get_api_token_and_retry_with_x_orchestrator_token_header",
        "MUTATION_TOKEN_EXPIRED": "fetch_token_from_get_api_token_and_retry_with_x_orchestrator_token_header",
        "STALE_INDEX": "read_file_range_or_cache_delta",
        "BACKUP_REQUIRED": "backup_status",
        "WRONG_ARGUMENTS": "read_api_tools_schema",
        "UNSUPPORTED_FIELD": "remove_unsupported_field",
        "UNSUPPORTED_DRY_RUN": "remove_dry_run",
        "PROJECT_PATH_REQUIRED": "set_project_path_root",
        "RUN_TEST_PROFILE_INVALID": "choose_available_profile",
        "SESSION_NOT_FOUND": "list_sessions",
        "ACTIVE_SESSION_REQUIRED": "open_session",
        "FILE_CHANGED_OUTSIDE_ORCHESTRATOR": "read_file_range",
        "FILE_ACCESS_DENIED": "check_file_permissions_or_run_with_access_to_project",
    }.get(error_name, "inspect_ca_guidance")


def _error_contract_meta(error_name: str, status: int) -> dict:
    mapping = {
        "MUTATION_TOKEN_REQUIRED": ("AUTH_ERROR", "error", True, "FETCH_TOKEN"),
        "MUTATION_TOKEN_EXPIRED": ("AUTH_ERROR", "error", True, "FETCH_TOKEN"),
        "MISSING_REQUIRED_FIELD": ("SCHEMA_VIOLATION", "error", True, "REBUILD_PAYLOAD"),
        "STALE_INDEX": ("STALE_STATE", "ERROR", True, "RE_READ_OR_CACHE_DELTA"),
        "FILE_CHANGED_OUTSIDE_ORCHESTRATOR": ("STALE_STATE", "CRITICAL", True, "RE_READ_AND_RESEED"),
        "BACKUP_REQUIRED": ("SAFETY_GATE", "CRITICAL", True, "CREATE_BACKUP"),
        "ACTIVE_SESSION_REQUIRED": ("SESSION_REQUIRED", "ERROR", True, "OPEN_SESSION"),
        "RUN_TEST_PROFILE_INVALID": ("INVALID_REQUEST", "ERROR", True, "CHOOSE_AVAILABLE_PROFILE"),
        "UNSUPPORTED_DRY_RUN": ("SCHEMA_VIOLATION", "ERROR", True, "REMOVE_FIELD"),
        "UNSUPPORTED_FIELD": ("SCHEMA_VIOLATION", "ERROR", True, "READ_LIVE_SCHEMA"),
        "WRONG_ARGUMENTS": ("SCHEMA_VIOLATION", "ERROR", True, "READ_LIVE_SCHEMA"),
        "BINARY_FILE_UNSUPPORTED": ("UNSUPPORTED_MEDIA_TYPE", "CRITICAL", False, "STOP_AND_REPORT"),
        "PROJECT_PATH_REQUIRED": ("INVALID_REQUEST", "ERROR", True, "SET_PROJECT_PATH"),
        "SESSION_NOT_FOUND": ("SESSION_REQUIRED", "ERROR", True, "LIST_SESSIONS"),
        "FILE_ACCESS_DENIED": ("PERMISSION_ERROR", "error", False, "CHECK_PERMISSIONS"),
    }
    if error_name in mapping:
        err_type, severity, retryable, suggested = mapping[error_name]
    else:
        err_type = "TOOL_ERROR" if status >= 500 else "INVALID_REQUEST"
        severity = "CRITICAL" if status >= 500 else "ERROR"
        retryable = status in {408, 409, 423, 429, 500, 502, 503, 504}
        suggested = "INSPECT_CA_GUIDANCE"
    return {
        "type": err_type,
        "severity": severity,
        "retryable": retryable,
        "suggested_fallback": suggested,
    }


def _retry_sequence(tool_name: str, error_name: str, body: dict | None,
                    *, corrected_payload: dict | None = None) -> list[dict]:
    body = dict(body or {})
    project_path = body.get("project_path") or "D:/path/to/project"
    if tool_name == "read_file_range" and error_name == "INVALID_RANGE":
        return [
            {"action": "choose_intended_range",
             "note": "Choose start_line >= 1 and intended count N >= 1; do not infer N from an invalid end_line or swap bounds."},
            {"action": "retry_with_corrected_range", "tool": "read_file_range",
             "payload_example": _invalid_range_payload_example(body),
             "note": "Example only: replace start_line/max_lines with intended values and omit end_line. Alternatively choose inclusive end_line >= start_line. No force_reseed needed."},
        ]
    if error_name in ("MUTATION_TOKEN_REQUIRED", "MUTATION_TOKEN_EXPIRED"):
        return [
            {"method": "GET", "url": os.environ.get("ORCHESTRATOR_BASE_URL", f"http://localhost:{PORT}") + "/api/token"},
            {"tool": tool_name, "payload": body, "headers": {"X-Orchestrator-Token": "token from GET /api/token"}},
        ]
    if error_name == "MISSING_REQUIRED_FIELD":
        # CASE-1919 item 1: "<required_field>": "<value>" is a placeholder, and a CA
        # that has not been taught this tool cannot expand it. Name the fields from
        # the registry and hand back a payload that already carries them.
        try:
            _mrf_detail = _missing_required_detail(tool_name, "", body)
        except Exception:
            _mrf_detail = {}
        _mrf_payload = dict(_mrf_detail.get("payload_example") or body)
        _mrf_missing = _mrf_detail.get("missing_fields") or []
        for _mrf_name in _mrf_missing:
            _mrf_payload.setdefault(_mrf_name, "<value>")
        return [
            {"action": "add_missing_required_fields", "tool": tool_name,
             "missing_fields": _mrf_missing, "payload": _mrf_payload,
             "note": ("Every name in this payload comes from the tool's registry "
                      "schema; replace the example values with real ones.")},
        ]
    if error_name == "STALE_INDEX":
        file_path = body.get("file_path") or body.get("path") or "relative/file.py"
        return [
            {"tool": "read_file_range", "payload": {"project_path": project_path, "file_path": file_path, "start_line": 1, "end_line": 80, "force_reseed": True}},
            {"tool": "cache_delta", "payload": {"project_path": project_path}},
            {"tool": tool_name, "payload": body},
        ]
    if error_name == "BACKUP_REQUIRED":
        return [
            {"tool": "backup_status", "payload": {"project_path": project_path}},
            {"tool": "backup_project", "payload": {"project_path": project_path, "force": True}},
            {"tool": tool_name, "payload": {**body, "backup_override": True}},
        ]
    if error_name == "ACTIVE_SESSION_REQUIRED":
        return [
            {"tool": "open_session", "payload": {"session_title": "CA session", "project_path": project_path}},
            {"tool": tool_name, "payload": body},
        ]
    if error_name == "FILE_CHANGED_OUTSIDE_ORCHESTRATOR":
        file_path = body.get("file_path") or body.get("path") or "relative/file.py"
        return [
            {"tool": "read_file_range", "payload": {"project_path": project_path, "file_path": file_path, "start_line": body.get("start_line", 1), "end_line": body.get("end_line", 80), "force_reseed": True}},
            {"tool": tool_name, "payload": body},
        ]
    if error_name == "UNSUPPORTED_FIELD":
        # The body the caller sent is NOT a corrected payload. Replaying it verbatim
        # re-sends the rejected field, and the duplicate-failure guard then answers
        # DUPLICATE_RETRY_BLOCKED (measured on manage_agent + project_path), so the
        # "retry once with corrected payload" step could never succeed -- it named a
        # correction while carrying the uncorrected request. The schema gate already
        # builds the one true retry body (alias mapping, then strip) and publishes it
        # as corrected_payload; forward THAT rather than keep a second copy that has
        # to be re-derived here and is wrong whenever it drifts. Fallback to body only
        # for call sites that have no corrected_payload to hand (behaviour unchanged
        # there).
        return [
            {"action": "remove unsupported field(s) per accepted_fields/accepted_aliases"},
            {"tool": tool_name, "payload": dict(corrected_payload or body),
             "note": ("retry once with THIS payload -- it is this response's "
                      "corrected_payload; resending your original body earns "
                      "DUPLICATE_RETRY_BLOCKED")},
        ]
    if error_name == "WRONG_ARGUMENTS":
        return [
            {"action": "read payload_example + accepted_fields"},
            {"tool": tool_name, "payload": body, "note": "rebuild payload, retry once"},
        ]
    if error_name == "UNSUPPORTED_DRY_RUN":
        corrected = dict(body)
        corrected.pop("dry_run", None)
        return [
            {"action": "remove dry_run flag"},
            {"tool": tool_name, "payload": corrected, "note": "retry"},
        ]
    if error_name == "RUN_TEST_PROFILE_INVALID":
        return [
            {"action": "list valid profiles per error detail"},
            {"tool": tool_name, "payload": body, "note": "retry with valid profile"},
        ]
    if error_name == "SESSION_NOT_FOUND":
        return [
            {"tool": "open_session", "payload": {"project_path": project_path}},
            {"tool": tool_name, "payload": body},
        ]
    if error_name == "PROJECT_PATH_REQUIRED":
        return [
            {"action": "add absolute project_path to payload"},
            {"tool": tool_name, "payload": {**body, "project_path": project_path}, "note": "retry"},
        ]
    if error_name == "FILE_ACCESS_DENIED":
        return [
            {"action": "verify path/permissions per error detail"},
            {"action": "do not retry unchanged; report if persists"},
        ]
    if error_name == "SESSION_STORE_CONTENTION":
        # A40 leftover: the payload already said retryable=True and
        # ca_next_action="retry_after_short_backoff", but this mapper had no
        # branch, so the SAME payload also carried retry_sequence=[] -- two
        # fields answering "should I retry?" with opposite answers, and the
        # empty one is the machine-readable one CA follows. Measured before the
        # fix: retryable=True next to retry_sequence=[].
        # The write did NOT commit and the payload is unchanged-safe, so the
        # correct sequence is literally "wait, then send the same call again".
        return [
            {"action": "back off ~250-1000ms; the session write queue was busy and the write did not commit"},
            {"tool": tool_name, "payload": body, "note": "retry the identical payload once the queue drains"},
        ]
    if error_name == "REGEX_SEARCH_TIMEOUT":
        # A04 (same shape as the A40 SESSION_STORE_CONTENTION hole): the payload
        # said retryable=True but this mapper had no branch, so it fell through to
        # return [] -- "retryable" next to zero machine-readable steps. search_code
        # now embeds its own richer retry_sequence, so the dispatcher setdefault
        # keeps that; this branch is the fallback for any path that DOESN'T, so it
        # must never hand back []. A blind re-send of the identical regex would just
        # time out again, so the first step degrades to literal text mode.
        text_retry = {**body, "mode": "text"}
        return [
            {"tool": tool_name, "payload": text_retry,
             "note": "regex hit the wall-clock deadline; retry as a literal text search (no backtracking)"},
            {"action": "or keep mode='regex' but narrow file_pattern / raise regex_deadline_seconds (<= 10s) for a well-behaved pattern"},
        ]
    return []


def _default_hint(tool_name: str, error_name: str) -> str:
    return {
        "MUTATION_TOKEN_REQUIRED": "Call GET /api/token, then retry with X-Orchestrator-Token.",
        "MUTATION_TOKEN_EXPIRED": "Token TTL elapsed. Call GET /api/token to rotate, then retry with X-Orchestrator-Token.",
        "STALE_INDEX": "Call read_file_range(project_path, file_path, force_reseed=true) or cache_delta, then retry. Never refresh_project_map on a large root such as [A:P].",
        "BACKUP_REQUIRED": "Call backup_status; if backup is missing, ask the user and call backup_project before retrying.",
        "WRONG_ARGUMENTS": "Read GET /api/tools for required fields and use the payload_example exactly.",
        "MISSING_REQUIRED_FIELD": "Rebuild the payload using required_fields, accepted_fields, and payload_example.",
        "UNSUPPORTED_FIELD": "Remove unsupported fields and retry with the documented schema.",
        "UNSUPPORTED_DRY_RUN": f"Remove dry_run; {tool_name} does not support dry_run.",
        "PROJECT_PATH_REQUIRED": "Set project_path to the absolute project root; use relative file_path/path inside it.",
        "RUN_TEST_PROFILE_INVALID": f"Use one of: {', '.join(_RUN_TEST_PROFILES)}.",
        "SESSION_NOT_FOUND": "Use list_sessions or pass a known session_id.",
        "ACTIVE_SESSION_REQUIRED": "Call open_session(session_title, project_path), then retry.",
        "FILE_CHANGED_OUTSIDE_ORCHESTRATOR": "R39 recovery: (1) read_file_range(file_path, start, end, force_reseed=true) (2) retry SINGLE write_fix. For multi-hunk same file use write_transaction. Never refresh_project_map on a large root such as [A:P].",
        "FILE_ACCESS_DENIED": "Check file permissions for the workflow state path and retry with project access.",
    }.get(error_name, "Follow ca_guidance.payload_example and retry.")


def _required_field_names(tool_name: str) -> list[str]:
    return list(((_schema_for_tool(tool_name).get("parameters") or {}).get("required") or []))


def _missing_required_error(tool_name: str, body: dict, required_fields: list[str]) -> dict:
    required = list(dict.fromkeys(required_fields))
    accepted = sorted(_tool_required_fields(tool_name) or required or list((body or {}).keys()))
    missing = [field for field in required if field not in (body or {})]
    example = _payload_example_for_tool(tool_name, {"parameters": {"required": required, "properties": {}}})
    if not example:
        example = _PAYLOAD_EXAMPLES.get(tool_name, {})
    return {
        "ok": False,
        "error": f"Missing required field(s) for '{tool_name}': {', '.join(missing)}",
        "error_name": "MISSING_REQUIRED_FIELD",
        "type": "SCHEMA_VIOLATION",
        "severity": "error",
        "retryable": True,
        "reason": f"missing required field(s): {', '.join(missing)}",
        "missing_fields": missing,
        "required_fields": required,
        "accepted_fields": accepted,
        "payload_example": example,
        "ca_next_action": "rebuild_payload_using_required_and_accepted_fields",
        "write_performed": False,
        "applied": False,
    }


def _missing_required_detail(tool_name: str, err_text: str, body: dict | None) -> dict:
    """CASE-1919 item 1: name the field(s) a MISSING_REQUIRED_FIELD is about.

    The registry gate (_missing_required_error) already answers with missing_fields
    and a real example, but a tool body that raises MISSING_REQUIRED_FIELD itself
    never reaches that gate: it arrived with a generic message plus the legacy
    _PAYLOAD_EXAMPLES fallback, which for search_code is {"project_path": ...} --
    an example that omits the very field that was missing. A CA that was not taught
    the tool beforehand then has to GUESS the name. Derive the names from the live
    registry schema, keep whatever the caller already sent, and hand back an example
    that contains the missing names.
    """
    body = dict(body or {})
    required = _required_field_names(tool_name)
    props = list(((_schema_for_tool(tool_name).get("parameters") or {}).get("properties") or {}))
    accepted = sorted(props) if props else sorted(dict.fromkeys(required))
    missing = [f for f in required if body.get(f) in (None, "") and f not in body]
    if not missing:
        missing = [f for f in required if body.get(f) in (None, "")]
    if not missing:
        # The tool raised on a field the registry does not mark required (typically
        # one half of an either/or pair). Fall back to the names the message itself
        # states, intersected with the schema's own property names so free-form
        # prose can never invent a field that does not exist.
        tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", err_text or ""))
        missing = [p for p in props if p in tokens and p not in body]
    try:
        example = dict(_payload_example_for_tool(tool_name, _schema_for_tool(tool_name)) or {})
    except Exception:
        example = {}
    example.update({k: v for k, v in body.items() if not str(k).startswith("_")})
    out = {
        "missing_fields": missing,
        "required_fields": required,
        "accepted_fields": accepted,
        "payload_example": example,
        "ca_next_action": "rebuild_payload_using_required_and_accepted_fields",
    }
    if missing:
        out["error_detail"] = (
            f"{tool_name} needs " + ", ".join(f"'{f}'" for f in missing)
            + " -- payload_example already contains "
            + ("it" if len(missing) == 1 else "them")
            + "; required_fields/accepted_fields list every name this tool takes")
    return out

def _is_mutating_tool(tool_name: str) -> bool:
    return tool_name in _MUTATING_TOOLS


def _requires_session(tool_name: str) -> bool:
    return tool_name in _MUTATING_TOOLS and tool_name not in _NO_SESSION_REQUIRED


_EXAMPLE_PATH_KEYS = {
    "project_path", "file_path", "path", "folder_path", "script_path", "target",
}


def _normalize_example_paths(payload: dict) -> dict:
    """Make an example payload safe to copy verbatim.

    A18 (2026-08-16) FAIL-LOUD (7-RULE #7): a Windows example path is written
    with backslashes, so a missing escape in the source literal turns `\\t`/`\\f`
    into a REAL control character. [A] tells a failing caller to rebuild its
    request from `payload_example`, so the caller then sends a path that this
    same server rejects with INVALID_PATH_CONTROL_CHAR — [A] handing out an
    example it will refuse.

    Recurses into nested dicts and lists: batch tools carry their paths inside
    an array of request objects (e.g. read_context_batch `requests[0].file_path`),
    and a top-level-only sweep silently missed every one of them.
    """
    def clean(key, value):
        if isinstance(value, dict):
            return {k: clean(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(key, v) for v in value]
        if isinstance(value, str) and key in _EXAMPLE_PATH_KEYS:
            # R41: keep forward slashes — backslash paths break JSON when CA
            # copies payload_example verbatim into a JSON body (\p, 	, 
            # become invalid escapes). Forward slashes are valid on Windows
            # and JSON-safe. Only strip control chars.
            if any(ch < " " for ch in value):
                value = "".join(ch for ch in value if ch >= " ")
        return value

    return {key: clean(key, value) for key, value in (payload or {}).items()}


def _schema_for_tool(tool_name: str) -> dict:
    for schema in ct.TOOL_SCHEMAS:
        fn = schema.get("function", {})
        if fn.get("name") == tool_name:
            return fn
    return {}


def _generated_payload_example(tool_name: str, fn_schema: dict) -> dict:
    params = (_schema_for_tool(tool_name) or fn_schema or {}).get("parameters", {}) or {}
    props = params.get("properties") or {}
    required = list(params.get("required") or [])
    if not props:
        # B6: an empty {} reads like "this tool takes no arguments", which may be
        # false (schema empty/unbuilt). Point the caller at the live schema
        # endpoint via a NON-payload-shaped key: an HTTP verb+path cannot be
        # mistaken for a literal request body echoed back as corrected_payload.
        return {"schema_ref": f"GET /api/tools/{tool_name}/schema"}

    def sample(field: str, spec: dict):
        if spec.get("enum"):
            default = spec.get("default")
            return copy.deepcopy(default if default in spec["enum"] else spec["enum"][0])
        if "default" in spec:
            return copy.deepcopy(spec["default"])
        if "const" in spec:
            return copy.deepcopy(spec["const"])
        if field == "project_path":
            return "D:/path/to/project"
        if field in {"file_path", "path"}:
            return "relative/file.py"
        kind = spec.get("type")
        if kind == "boolean":
            return False
        if kind == "integer":
            return int(spec.get("minimum", 1))
        if kind == "number":
            return spec.get("minimum", 1)
        if kind == "array":
            item_spec = spec.get("items") or {}
            return [sample(field, item_spec) for _ in range(int(spec.get("minItems", 0)))]
        if kind == "object":
            child_props = spec.get("properties") or {}
            return {name: sample(name, child_props[name]) for name in spec.get("required", [])}
        minimum = max(1, int(spec.get("minLength", 1)))
        text = "<" + re.sub(r"\s+", " ", str(spec.get("description") or field)).strip()[:80] + ">"
        return text if len(text) >= minimum else text + ("x" * (minimum - len(text)))

    names = required + [
        name for name, spec in props.items()
        if name not in required and (name == "project_path" or "default" in spec)
    ]
    return _normalize_example_paths({name: sample(name, props[name]) for name in names})


# Agent-enablement (2026-08-08): registry-schema-driven payload examples for the
# three tools whose auto-generated examples were either incorrect or unhelpful
# for coding agents scanning a project. Keys are the canonical registry field
# names; values are intent-realistic placeholders, Windows-escape-safe (no
# trailing backslash, no `\t`/`\f` escape ambiguity). Keep in sync with
# registry_data.py whenever the accepted-fields contract changes there.
_PAYLOAD_EXAMPLE_OVERRIDES: dict[str, dict] = {
    "find_symbol": {
        "name": "my_function",
        "project_path": "D:/path/to/project",
    },
    "find_callers": {
        "symbol": "my_function",
        "project_path": "D:/path/to/project",
    },
    "search_code": {
        "query": "pattern to find",
        "project_path": "D:/path/to/project",
        # registry enum is text|regex|symbol|callers|...; "text" is the literal/plain-text
        # mode. Use "regex" for pattern matching. Do NOT use "literal" — not in enum.
        "mode": "text",
    },
}


def _payload_example_for_tool(tool_name: str, fn_schema: dict) -> dict:
    """Return a concrete payload example for the given tool.

    Agent-enablement: tools listed in _PAYLOAD_EXAMPLE_OVERRIDES return the
    curated, registry-accurate example verbatim (canonical field names only).
    All other tools fall back to _generated_payload_example, which derives an
    example from the registry schema, then we strip any `token` noise-field
    (not part of the caller-facing contract for non-auth tools).
    """
    override = _PAYLOAD_EXAMPLE_OVERRIDES.get(tool_name)
    if override is not None:
        # A18: curated overrides bypassed the path sanitiser entirely, so a bad
        # literal here reached the caller untouched. Route them through the same
        # guard — a hand-written example is no more trustworthy than a generated
        # one, and this is the only exit both paths share.
        return _normalize_example_paths(copy.deepcopy(override))
    example=_generated_payload_example(tool_name,fn_schema)
    if tool_name != "workflow_draft_get": example.pop("token",None)
    return example


def _tool_required_fields(tool_name: str) -> list[str]:
    for schema in ct.TOOL_SCHEMAS:
        fn = schema.get("function", {})
        if fn.get("name") == tool_name:
            return list(fn.get("parameters", {}).get("required", []) or [])
    return []

_TOOL_GOTCHAS: dict[str, list[str]] = {
    "write_fix": [
        "start_line/end_line are 1-based and inclusive",
        "content_encoding='base64' required for multiline/JS/HTML/backslash content",
        "FILE_CHANGED_OUTSIDE_ORCHESTRATOR: read_file_range + force_reseed + retry; never refresh_project_map on [A:P]",
        "SYNTAX_ERROR_PRE_WRITE or VALIDATION_FAILED_AUTO_REVERT: use draft_id/failed_parts and write_draft_patch; do not retry the original range blindly",
        "A may auto-ship only after the draft is clean; it must not invent replacement content for ambiguous line drift",
    ],
    "write_fix_html": [
        "Uses internal preview and returns diff_preview; do not pass dry_run unless live schema adds it",
        "content_encoding='base64' always recommended",
        "FILE_CHANGED_OUTSIDE_ORCHESTRATOR: read_file_range + force_reseed + retry; never refresh_project_map on [A:P]",
    ],
    "write_new_file": [
        "Use dry_run=true first, then apply_after_dry_run with the returned dry_run_id",
        "Fails if file already exists unless overwrite=True; prefer write_fix for updates",
    ],
    "run_project_command": [
        # R43.2 C1: merged from previous duplicate at line 1205 (interactive/elevated hints
        # were silently overriding cmd/exit_code/path-spaces hints). Now one canonical list.
        "Windows builtins need cmd /c prefix",
        "Read exit_code - do not infer success from output text alone",
        "Paths with spaces should use: powershell -NoProfile -Command \"Set-Location '2. Rawcut'; node health-check.js\"",
        "interactive=true is for GUI/user-session launches; elevated=true requests ShellExecuteW runas on Windows.",
        "Elevated launches cannot guarantee stdout/stderr capture or PID.",
    ],
    "resolve_project_path": [
        "Use project_path for sessions and file_path for file tools; do not pass file paths as project_path",
    ],
    "path_quote_helper": [
        "Generates command text only; it never executes",
        "Use the returned safe_command with run_project_command",
    ],
    "optional_tool_probe": [
        "Probe only reports availability; it does not call optional endpoints or fetch raw tokens",
        "Optional unavailable means skip/report, not fail the whole workflow",
    ],
    "protected_tools_status": [
        "Does not expose the raw token",
        "run_project_command is protected; run_test_profile is unprotected",
    ],
    "validate_file": [
        "Use lang param to override auto-detect",
        "HTML validation checks tag balance and script blocks",
    ],
    "post_write_batch_verify": [
        "Non-mutating verification bundle for changed_files after a write batch",
        "Runs cache_delta at most once for the batch",
        "validate_symbols unsupported is reported in unsupported_tools, not a batch failure",
    ],
    "rename_session": [
        "Use project_path + new_title. Do not pass session_id unless live schema adds it.",
    ],
    "log_native": [
        "Use native=[] for a clean session. Do not pass project_path.",
    ],
    "log_native_event": [
        "Requires project_path in the current schema",
        "Response includes fallback_count_so_far, session_id, and audit_status",
    ],
    "create_pending_audit": [
        "Use only when native fallback happened while the API was unavailable",
        "Do not store raw tokens, secrets, or file content",
    ],
    "sync_pending_audit": [
        "Syncs pending native fallback audit rows into the active session log",
    ],
    "search_code": [
        "Canonical field is query. pattern is a verified alias mapped to query with regex mode.",
    ],
    "desktop_click": [
        "Response method is always present: cdp or win32_attach.",
        "Win32 path exposes resolved_hwnd, child_hwnd, and resolved_child_hwnd for audit.",
        "fallback_to_win32=false isolates CDP failures without trying Win32 side effects.",
        "Win32 uses SendMessageTimeout; if delivery is not verified, inspect sequence and root_causes.",
    ],
    "desktop_app_focus": [
        "Response method is always present: cdp or win32_attach.",
        "fallback_to_win32=false isolates CDP failures without trying Win32 side effects.",
        "Use match_mode plus matched_title to debug fuzzy CDP target selection.",
    ],
    # R43.2 C1: dup `list_cdp_targets` and `run_project_command` removed here.
    # See canonical entries earlier in this dict (run_project_command above; list_cdp_targets below at the detailed entry).
    "kill_process": [
        "Protected T2 cleanup tool; requires token and active session.",
        "Use pid for exact cleanup; process_name can match multiple processes.",
    ],
    # V15 R16 — KtrSH enrichment from real Electron test gaps
    "cdp_attach_target": [
        "Electron renderer rejects Target.createBrowserContext on page-level WS — V15 R16 auto-falls back to browser-level WS via /json/version; check attach_via field.",
        "For Electron, prefer launch_electron_with_cdp (one-shot launch+attach) over manual list_cdp_targets + attach.",
        "websocket_url from /json/version is browser-level (works for connect_over_cdp + new_context). webSocketDebuggerUrl per page is page-level (works for raw CDP only).",
    ],
    "fill": [
        "JSON transport STRIPS double-backslash (\\\\\\\\ → \\\\ → \\). UNC paths like \\\\server\\share lose backslashes silently.",
        "For UNC / UTF-8 / multi-line content, use fill_value(encoding='base64') instead — decodes inside the browser, preserves every byte.",
        "Always confirm final value via dom_state_diff or evaluate_js read-back when content critical.",
    ],
    "evaluate_js": [
        "Returns the LAST expression value. For multi-statement, wrap in IIFE: (()=>{ ...; return X })()",
        "JSON-stringify selectors via json.dumps when building expressions to avoid quote escape bugs.",
        "For DOM snapshot pattern, prefer dom_state_diff tool (V15 R16) over rolling your own.",
        "For section-by-title log extraction, prefer extract_log_section tool (V15 R16).",
    ],
    "list_cdp_targets": [
        "Read-only CDP target enumeration through [A]; prefer this over native HTTP calls to /json/list.",
        "Returned webSocketDebuggerUrl is PAGE-level. For Playwright connect_over_cdp + new_context use BROWSER-level WS from GET /json/version.",
        "type=page is what you want; type=service_worker / iframe are noise for UI automation.",
    ],
    "desktop_window_list": [
        "Filters by substring (case-insensitive). For exact match, post-filter in CA.",
        "Returns only visible top-level windows. Minimized windows still appear with rect coords near (-32000,-32000); use check_window_visibility to confirm before clicking.",
    ],
    "read_console_errors": [
        "Default level='error' IGNORES warnings (e.g. CSP unsafe-eval). DO NOT retry test on warning-level entries.",
        "severity_summary field counts all levels regardless of filter.",
        "For Electron renderers with custom logging, also check extract_log_section for in-DOM log panels.",
    ],
    "check_window_visibility": [
        "Always call BEFORE desktop_click on potentially minimized/off-screen Electron or background apps.",
        "Suggestion 'restore' means call desktop_app_focus next; 'cdp_fallback' means UIPI likely blocked Win32, switch to CDP path.",
        "Either hwnd OR title_pattern; if both given, hwnd wins.",
    ],
    # V15 R16 — gotchas for new tools
    "inspect_project_runtime": [
        "First call BEFORE any UI launch. Cheap T1 read; tells you which recipe (Electron / Node / native) applies.",
        "has_remote_debugging_in_launcher=false means call launch_electron_with_cdp to inject the port flag automatically.",
    ],
    "launch_electron_with_cdp": [
        "One-shot: detect launcher → inject --remote-debugging-port → run background → fetch /json/version → cdp_attach_target. Returns page_id ready.",
        "Token required (mutating). Always verify response.cdp_port matches what you intended.",
        "If launcher already has --remote-debugging-port, runs as-is without modification.",
        "Use cleanup: run_project_command('taskkill /F /IM electron.exe /T') after test.",
    ],
    "dom_state_diff": [
        "Two-call pattern: (1) baseline snapshot without baseline_id → returns snapshot_id; (2) after action → call again with baseline_id → returns changes.",
        "TTL 10 minutes; baseline_id is tied to the live page/generation — re-snapshot after restart or page replacement.",
        "page_id must be a live pg-... ID from ToolA; numeric PowerShell PID values are invalid caller data.",
        "Default fields: disabled/bg/color/className/visible/text. Add 'value'/'rect' explicitly for inputs/layout.",
    ],
    "extract_log_section": [
        "Finds element with textContent matching section_title (substring OK), walks to closest .log-area ancestor, extracts textarea/pre/.log-body content.",
        "Returns is_empty=true when log area exists but content is whitespace-only.",
        "If SECTION_TITLE_NOT_FOUND, inspect DOM via evaluate_js: Array.from(document.querySelectorAll('.result-title')).map(e=>e.textContent)",
    ],
    "fill_value": [
        "encoding='base64' bypasses JSON backslash strip — REQUIRED for UNC paths and any string with literal backslashes.",
        "Always dispatches input+change events; safe for React/Vue/Electron forms.",
        "Encode in CA: btoa(unescape(encodeURIComponent(text))) — preserves UTF-8.",
    ],
    "bundle_version_check": [
        "marker_regex must contain at least one capture group; the first non-empty capture is returned",
        "VERSION_REGEX_CAPTURE_REQUIRED means correct the regex before retrying",
    ],
    "copy_file": [
        "DST_EXISTS is a non-mutating safety guard",
        "For a deliberate rerun, use a new destination or overwrite=true with backup_existing=true and verify sha256",
    ],
    "run_playwright_test": [
        "Node-only runner: .js/.mjs/.cjs; Python fixtures belong to run_project_test_script(runtime=python)",
        "MODULE_NOT_FOUND is infrastructure; install/use a pre-installed Playwright location or use internal browser T4",
    ],
    "write_draft_patch": [
        "Only patch ranges from failed_parts in the draft; clean draft auto-ships through the original pipeline",
        "The backend cannot infer corrected replacement content from ambiguous line drift",
    ],
}

_TOOL_PROMPT_RULES: dict[str, str] = {
    "write_fix": "Always read_file_range first; use base64 for non-trivial content; on SYNTAX_ERROR_PRE_WRITE use draft_id/failed_parts and never retry a stale range blindly",
    "write_fix_html": "Use base64; read current HTML before writing; review returned diff_preview",
    "write_new_file": "Use dry_run=true first; review diff_preview; then apply_after_dry_run",
    "run_project_command": "Windows only: cmd /c or PowerShell; use interactive/elevated only for GUI launches and verify with expected_port/process",
    "resolve_project_path": "Resolve user paths before open_session when a file path may have been provided",
    "path_quote_helper": "Use for subfolders with spaces before run_project_command",
    "optional_tool_probe": "Use to verify optional helpers and skip/report unavailable helpers",
    "protected_tools_status": "Use to decide whether to fetch a mutation token; do not infer from tier alone",
    "validate_file": "Run after every write batch before cache_delta",
    "post_write_batch_verify": "After write batches, validate files/symbols/encoding and run one cache_delta",
    "rename_session": "Use project_path + new_title for the active session",
    "log_native": "End with log_native(native=[]), never for token refresh",
    "log_native_event": "Log each native fallback inline; do not use for token refresh",
    "create_pending_audit": "Record pending native fallback only when API logging is unavailable",
    "sync_pending_audit": "Call when API is available again before close_session",
    "search_code": "Use query as the canonical field; aliases must be verified first",
    "desktop_click": "Use list_cdp_targets first for Electron/Chromium; set fallback_to_win32=false when isolating CDP failures",
    "desktop_app_focus": "Use list_cdp_targets first; check method/matched_title/final_url in the response",
    "list_cdp_targets": "Read-only CDP target discovery through [A]; no token required",
    "kill_process": "Protected cleanup only; prefer pid over process_name",
    # V15 R16 prompt rules
    "cdp_attach_target": "For Electron use launch_electron_with_cdp; for Chrome use list_cdp_targets → cdp_attach_target(target_id). Check attach_via in response.",
    "fill": "DO NOT use for UNC paths or content with backslashes — JSON strips them. Use fill_value(encoding='base64') instead.",
    "evaluate_js": "Wrap multi-statement in IIFE. Prefer dom_state_diff / extract_log_section for common patterns.",
    "check_window_visibility": "Always before desktop_click on potentially-minimized windows; act on suggestion field.",
    "desktop_window_list": "Use to verify window exists before desktop_app_focus / check_window_visibility.",
    "read_console_errors": "Default level=error ignores warnings; do NOT treat CSP unsafe-eval as test fail.",
    "inspect_project_runtime": "Call FIRST before any UI launch — tells you which recipe (Electron/Node/native) applies.",
    "launch_electron_with_cdp": "Replaces manual 3-step Electron launch. Returns page_id ready for click/fill_value/dom_state_diff.",
    "dom_state_diff": "Use a live pg-... page_id; fresh baseline after restart/page replacement; compare only within the same live generation",
    "extract_log_section": "Use for finding 'System Error Log' / 'Server Response' panels — locates by title text, extracts textarea content.",
    "fill_value": "Transport-safe DOM fill. encoding='base64' for UNC paths / UTF-8 / multi-line.",
    "bundle_version_check": "marker_regex must include a capture group; retry only after correcting the regex",
    "copy_file": "On DST_EXISTS, choose a new disposable destination or use overwrite=true with backup_existing=true once",
    "run_playwright_test": "Node-only .js/.mjs/.cjs; preflight playwright dependency; Python uses run_project_test_script(runtime=python)",
    "write_draft_patch": "Use only draft_id/failed_parts from a write failure; clean draft auto-ships, but A must not invent replacement content",
}
def _invalid_range_payload_example(body: dict | None) -> dict:
    """An illustrative bounded read, never an inferred repair of reversed bounds."""
    body = body or {}
    return {
        "project_path": body.get("project_path") or "D:/path/to/project",
        "file_path": body.get("file_path") or "relative/file.py",
        "scope": "range", "start_line": 1, "max_lines": 40,
    }


def _ca_guidance(tool_name: str, error: str = "", body: dict | None = None,
                 error_name: str | None = None) -> dict:
    """Return compact, actionable guidance for coding agents."""
    error_name = error_name or _infer_error_name(tool_name, error, body)
    # Registry schema is the single owner of remediation examples and requiredness.
    example = _payload_example_for_tool(tool_name, _schema_for_tool(tool_name))
    if tool_name == "read_file_range" and error_name == "INVALID_RANGE":
        example = _invalid_range_payload_example(body)
    required = _tool_required_fields(tool_name)

    _base = os.environ.get("ORCHESTRATOR_BASE_URL", f"http://localhost:{PORT}")
    notes = [
        "Use PowerShell hashtable -> ConvertTo-Json; do not hand-write JSON for multiline content.",
        "Use project_path for the project root. Prefer relative file_path/path inside that root.",
        "For write_fix, write_fix_html, and write_new_file, use content_encoding='base64' when content has newlines, quotes, backslashes, HTML, JS, or large blocks.",
        f"Read schema with GET {_base}/api/tools if a field is unclear.",
    ]
    lower_error = error.lower()
    if "token" in lower_error:
        notes.insert(0, f"Fetch GET {_base}/api/token and send X-Orchestrator-Token on mutating calls.")
    if "backup" in lower_error:
        notes.insert(0, "Call backup_status first; if missing, ask the user before backup_project or use backup_override only after approval.")
    if "session" in lower_error:
        notes.insert(0, "Call open_session(session_title, project_path) before mutating tools.")
    if "json" in lower_error:
        notes.insert(0, "If JSON breaks, base64-encode content and set content_encoding='base64'.")
    if error_name == "STALE_INDEX":
        notes.insert(0, "Re-seed with read_file_range(force_reseed=true) or cache_delta. Never refresh_project_map on a large root such as [A:P].")
    if error_name == "FILE_CHANGED_OUTSIDE_ORCHESTRATOR":
        notes.insert(0, "R39 recovery: 1) read_file_range(file_path, force_reseed=true) 2) retry SINGLE write_fix. For multi-hunk same file use write_transaction. Never refresh_project_map on a large root such as [A:P].")
    if error_name == "UNSUPPORTED_DRY_RUN":
        notes.insert(0, f"Remove dry_run; {tool_name} does not support dry_run.")
    if error_name == "RUN_TEST_PROFILE_INVALID":
        notes.insert(0, f"Use one of the available profiles: {', '.join(_RUN_TEST_PROFILES)}.")

    return {
        "phase": ct.tool_phase(tool_name),
        "phase_label": ct.PHASE_LABELS.get(ct.tool_phase(tool_name), "Session / Audit / Auxiliary"),
        "error_name": error_name,
        "ca_next_action": "correct_range" if tool_name == "read_file_range" and error_name == "INVALID_RANGE" else _ca_next_action(error_name, tool_name),
        "call_pattern": (
            "$body = @{ ... } | ConvertTo-Json -Depth 20\n"
            f"Invoke-RestMethod -Method Post -Uri \"{_base}/api/call/{tool_name}\" "
            "-ContentType \"application/json\" -Body $body"
        ),
        "payload_example": example,
        "required_fields": required,
        "accepted_aliases": _alias_metadata(tool_name),
        "notes": notes,
        "known_gotchas": _TOOL_GOTCHAS.get(tool_name, []),
        "recommended_prompt_rule": _TOOL_PROMPT_RULES.get(tool_name, ""),
        # V15 R40: see_also + suggested_alternative_tool surfaced in CA guidance
        "see_also": getattr(ct, "_R40_SEE_ALSO_MAP", {}).get(tool_name, {}),
        "suggested_alternative_tool": _r40_suggest_alternative(tool_name, error_name),
    }


# V15 R40: per-(tool, error) → alternative tool recommendation
_R40_ERROR_TO_ALTERNATIVE = {
    ("write_fix", "FILE_CHANGED_OUTSIDE_ORCHESTRATOR"): {
        "tool": "write_transaction",
        "reason": "Multi-hunk same file causes line-shift cascade. Use write_transaction (ops sorted bottom-up).",
    },
    ("write_fix", "MULTIPLE_FAILED_RETRIES"): {
        "tool": "write_transaction",
        "reason": "Switch to atomic multi-op batch if same file has ≥2 hunks.",
    },
    ("fill", "JSON_ENCODING_ERROR"): {
        "tool": "fill_value",
        "reason": "fill_value with encoding='base64' handles UNC paths / Unicode / multi-line.",
    },
    ("fill_folder_dialog", "DIALOG_NOT_FOUND"): {
        "tool": "folder_dialog_snapshot",
        "reason": "Discover all dialog candidates first (no filter) to verify owner_pid + class.",
    },
    ("fill_folder_dialog", "DIALOG_AMBIGUOUS"): {
        "tool": "folder_dialog_snapshot",
        "reason": "Use snapshot to inspect candidates, then re-call with correct owner_pid/parent_hwnd.",
    },
    ("click_and_handle_folder_dialog", "NEW_DIALOG_NOT_FOUND"): {
        "tool": "invoke_app_function",
        "reason": "Click did not open native dialog (custom Electron UI?) — invoke handler directly.",
    },
    ("desktop_click", "CDP_DISPATCH_FAILED"): {
        "tool": "desktop_click",
        "reason": "Retry with force_win32=True to bypass CDP and target native window directly.",
    },
    ("search_code", "UNSUPPORTED_FIELD"): {
        "tool": "search_code",
        "reason": "If error mentions mode, check `mode_alias_resolved` field for canonical name then retry.",
    },
    ("run_playwright_test", "MODULE_NOT_FOUND"): {
        "tool": "run_project_command",
        "reason": "Install playwright first: run_project_command(cd test_dir && npm install playwright).",
    },
    ("workflow_submit_step", "STEP_MISMATCH"): {
        "tool": "workflow_get_current",
        "reason": "Re-sync current_step from authoritative state.",
    },
    ("workflow_start", "WORKFLOW_NOT_FOUND"): {
        "tool": "workflow_list",
        "reason": "List available workflows to find correct slug.",
    },
    ("read_file_range", "FILE_NOT_FOUND"): {
        "tool": "query_index",
        "reason": "Use query_index to locate file by pattern.",
    },
    # V15 R41 additions
    ("write_fix", "VALIDATION_FAILED_AUTO_REVERT"): {
        "tool": "write_fix",
        "reason": "Backend auto-restored .bak. Read validation_error_detail, fix new_content syntax/lint issue, retry. NOT a token/auth problem.",
    },
    ("write_transaction", "APPLY_FAILED"): {
        "tool": "write_transaction",
        "reason": "R41 BUG FIX: same-file batch now refreshes cache between ops. If still failing, ensure ops sorted bottom-up (highest start_line first) and split into multiple smaller transactions if >5 ops.",
    },
    ("electron_devtools_log", "ANY"): {
        "tool": "list_cdp_targets",
        "reason": "⚠️ electron_devtools_log deprecated R41. Use list_cdp_targets for target enumeration.",
    },
    # R43.3 M8: common error_name fallbacks across tools
    ("search_code", "PATH_NOT_FOUND"): {
        "tool": "list_structure",
        "reason": "Verify path with list_structure first; pass project_path + relative path if file_path is relative.",
    },
    ("read_file_range", "PATH_NOT_FOUND"): {
        "tool": "list_structure",
        "reason": "Verify file path with list_structure or find_symbol before reading.",
    },
    ("find_symbol", "PATH_NOT_FOUND"): {
        "tool": "list_structure",
        "reason": "Verify project_path exists. find_symbol scans recursively from project root.",
    },
    ("open_session", "PROJECT_PATH_NOT_FOUND"): {
        "tool": "list_projects",
        "reason": "List known projects to verify the correct project_path (absolute, no trailing slash).",
    },
    ("ANY", "PROJECT_PATH_NOT_FOUND"): {
        "tool": "list_projects",
        "reason": "project_path must be absolute and exist. Check spelling and OS path separators.",
    },
    ("run_project_command", "TIMEOUT"): {
        "tool": "run_project_command",
        "reason": "Increase timeout_seconds or run async via background=True. Long-running tasks should use run_project_test_script (saves artifact) instead.",
    },
    ("wait_for_idle", "TIMEOUT"): {
        "tool": "assert_target_idle",
        "reason": "Single-shot assert_target_idle with longer window_seconds; or increase wait_for_idle timeout_seconds.",
    },
    ("ANY", "TIMEOUT"): {
        "tool": "ANY",
        "reason": "Operation exceeded budget. Either: (a) raise timeout, (b) split work into smaller batches, (c) check target process actually running.",
    },
    ("launch_electron_with_cdp", "CDP_UNREACHABLE"): {
        "tool": "ensure_single_electron_instance",
        "reason": "Pre-launch hygiene: kill stale Electron + verify cdp_port free. Then retry launch.",
    },
    ("cdp_attach_target", "CDP_UNREACHABLE"): {
        "tool": "server_lifecycle_status",
        "reason": "Verify CDP port is bound (port_owner_inspect / server_lifecycle_status). If not, relaunch with launch_electron_with_cdp.",
    },
    ("ANY", "CDP_UNREACHABLE"): {
        "tool": "port_owner_inspect",
        "reason": "Confirm port owner is the expected Electron process. Stale orphan may be blocking; consider kill_process + relaunch.",
    },
}

def _r40_suggest_alternative(tool_name: str, error_name: str) -> dict:
    """V15 R40: return {tool, reason} if error has a known cross-tool fix.
    R43.3 M8: falls back to ("ANY", error_name) wildcard if specific (tool, error) missing.
    """
    direct = _R40_ERROR_TO_ALTERNATIVE.get((tool_name, error_name))
    if direct:
        return direct
    # R43.3 M8: generic error fallback (("ANY", error_name) — applies to any tool emitting this error)
    generic = _R40_ERROR_TO_ALTERNATIVE.get(("ANY", error_name))
    if generic:
        return generic
    # see_also fallback: if error is generic and tool has see_also entries,
    # surface first one as a hint (low confidence).
    see_also = getattr(ct, "_R40_SEE_ALSO_MAP", {}).get(tool_name, {})
    if see_also and error_name in {"INTERNAL_ERROR", "TOOL_ERROR", "UNKNOWN"}:
        alt_tool = next(iter(see_also.keys()))
        return {"tool": alt_tool, "reason": f"(low-confidence see_also hint) {see_also[alt_tool]}"}
    return {}


# R101: opt-in minimal-response field filter. Callers pass `fields` (body field for MCP
# call_tool, or ?fields= query for HTTP) = comma-separated keys to keep. Default (no fields)
# returns the FULL envelope (backward compatible). Set per-dispatch via ContextVar so both the
# success path and every _error_response return can filter without rewriting call sites.
_R101_FIELDS = contextvars.ContextVar("_r101_fields", default=None)
# A37 #0b: the agent id of the dispatch in flight, set once in
# _dispatch_tool_call_request and read by _error_response, which is called from dozens of
# sites that never carry identity. Same reason as _R101_FIELDS above: per-dispatch state
# without rewriting every call site. Default "" resolves to bootstrap ctx "default", so a
# route outside the dispatcher re-arms nothing instead of re-arming the wrong CA.
_A37_AGENT_CTX = contextvars.ContextVar("_a37_agent_id", default="")
_R101_ALWAYS_KEEP = {"ok", "error", "error_name"}
# ISS-14: keys a projection must never be able to hide, because dropping them
# turns a correct answer into a dangerous one. `hash_domain` and
# `read_token_seeded` are file_sha256's ONLY defence against its raw-byte digest
# being mistaken for a read_file_range content_hash and pasted into
# write_fix.expected_hash -- the two hash domains are indistinguishable by shape,
# so `fields=ok,sha256` used to hand back a bare 64-hex string with no domain and
# no "this seeds no edit authority" marker. Force-kept, and the rescue is
# disclosed via fields_force_kept so a caller sees why the key survived.
# CASE-1919 item 3 adds project_path_resolution to the same class: it is the only
# signal that a relative project_path was resolved against the DAEMON's cwd rather
# than the caller's, so hiding it turns "wrong project" into "looks correct".
_R101_SAFETY_KEEP = {"hash_domain", "read_token_seeded", "project_path_resolution"}


def _r101_parse_fields(value) -> set:
    if value is None:
        return set()
    items = value.split(",") if isinstance(value, str) else (
        value if isinstance(value, (list, tuple, set)) else [value])
    return {str(i).strip() for i in items if str(i).strip()}


def _r101_split_specs(fields):
    """Split a fields= selector into plain keys and dotted sub-selectors.

    `result.impacted` reads as "keep result, but only its impacted key".
    Returns (plain_keys, {head: {remainder, ...}}).
    """
    plain = set()
    nested = {}
    for raw in fields:
        spec = str(raw).strip()
        if not spec:
            continue
        head, dot, rest = spec.partition(".")
        head = head.strip()
        rest = rest.strip()
        if dot and head and rest:
            nested.setdefault(head, set()).add(rest)
        else:
            plain.add(spec)
    return plain, nested


def _r101_project_nested(value, specs):
    """Project one nested dict. Returns (out, dropped, rescued, unknown).

    Safety keys are rescued at EVERY level, not just the envelope: for an
    _ok()-wrapped tool the hash-domain markers live inside `result`, so a
    nested projection that dropped them would re-open the exact hole ISS-14
    closed at the top level.
    """
    plain, nested = _r101_split_specs(specs)
    keep = plain | set(nested) | _R101_ALWAYS_KEEP | _R101_SAFETY_KEEP
    out = {k: v for k, v in value.items() if k in keep}
    dropped = len(value) - len(out)
    rescued = sorted((set(value) & _R101_SAFETY_KEEP) - plain - set(nested))
    unknown = sorted(plain - set(value) - _R101_ALWAYS_KEEP)
    for head, subs in nested.items():
        if head in plain:
            continue
        inner = out.get(head)
        if not isinstance(inner, dict):
            # Absent, or present but not a dict: either way the sub-selector
            # could not be honoured, and saying so beats a silent bare branch.
            unknown.extend(f"{head}.{s}" for s in sorted(subs))
            continue
        sub_out, sub_dropped, sub_rescued, sub_unknown = _r101_project_nested(
            inner, subs)
        out[head] = sub_out
        dropped += sub_dropped
        rescued.extend(f"{head}.{name}" for name in sub_rescued)
        unknown.extend(f"{head}.{name}" for name in sub_unknown)
    return out, dropped, sorted(set(rescued)), sorted(set(unknown))


_R101_AVAILABLE_CAP = 24


def _r101_available_names(payload, unknown):
    """CASE-1809-E: name the keys that DO exist, per namespace, for every miss.

    A23-P2 already reports WHICH fields= keys were not honoured, which is what
    keeps a projection from reading as an empty result.  It does not say what
    to type instead, so the only way forward was to repeat the SAME call with
    fields= omitted -- the full payload the projection existed to avoid.
    Measured 2026-09-18 on read_file_range: `content` instead of `result`, and
    `total_lines.deeper` aimed at an int, were both answered with nothing but
    "check spelling".

    Bounded on purpose: at most _R101_AVAILABLE_CAP names per namespace, since
    an unbounded key dump is the very cost fields= buys off.  Namespace "" is
    the top level.  A dotted head that exists but is not a dict reports its
    type instead of a list -- no sub-selector can descend into a scalar, and
    that is a different mistake from a misspelled key.
    """
    if not isinstance(payload, dict):
        return {}

    def _bounded(names) -> list:
        names = sorted(str(n) for n in names)
        if len(names) <= _R101_AVAILABLE_CAP:
            return names
        hidden = len(names) - _R101_AVAILABLE_CAP
        return names[:_R101_AVAILABLE_CAP] + ["...+%d more" % hidden]

    spaces = {}
    for key in unknown:
        head, dot, _leaf = str(key).partition(".")
        if not dot or head not in payload:
            # A plain key, or a dotted key whose HEAD is the typo: either way
            # the choice to be made is at the top level.
            spaces.setdefault("", _bounded(payload))
            continue
        inner = payload.get(head)
        if isinstance(inner, dict):
            spaces.setdefault(head, _bounded(inner))
        else:
            spaces.setdefault(head, [
                "<not an object: %s> -- a dotted key cannot descend into it"
                % type(inner).__name__])
    return spaces


_R101_POP_EXEMPT_CACHE = None


def _r101_pop_exempt_tools() -> frozenset:
    """Tools whose `fields` is their OWN parameter, so the dispatcher must not pop it.

    B027, decided 2026-09-18.  The dispatcher pops `fields` out of the request body
    before the UNSUPPORTED_FIELD check, which is what makes the R101 projection
    available on every tool without a single handler declaring it.  Two DOM-state
    tools paid for that: their `fields` is a genuine handler parameter (the DOM-state
    names to capture), so the pop meant the caller's own value never arrived.

    Membership is DERIVED from the schema, never from a name list: the R101 stamper
    marks the property it adds, so an UNMARKED `fields` property is a real parameter.
    Rename or add such a tool and this still answers correctly with no edit here.  A
    tool with no `fields` property at all is NOT exempt -- the selector has to keep
    working there, which is the whole point of the unconditional pop.

    Cached: it is consulted on every dispatch and ct.TOOL_SCHEMAS is a list.  The
    cache fills on first use, by which time import-time stamping has run.
    """
    global _R101_POP_EXEMPT_CACHE
    if _R101_POP_EXEMPT_CACHE is not None:
        return _R101_POP_EXEMPT_CACHE
    try:
        from orchestrator.core.fields_param import FIELDS_PARAM_NAME, R101_MARKER
    except Exception:
        # The marker is the only honest membership test.  Without it, fall back to
        # the old unconditional pop rather than guess at a name.
        _R101_POP_EXEMPT_CACHE = frozenset()
        return _R101_POP_EXEMPT_CACHE
    names = set()
    for schema in getattr(ct, "TOOL_SCHEMAS", None) or ():
        if not isinstance(schema, dict):
            continue
        fn = schema.get("function") or {}
        props = (fn.get("parameters") or {}).get("properties") or {}
        spec = props.get(FIELDS_PARAM_NAME) if isinstance(props, dict) else None
        if isinstance(spec, dict) and spec.get(R101_MARKER) is not True:
            name = fn.get("name")
            if name:
                names.add(name)
    _R101_POP_EXEMPT_CACHE = frozenset(names)
    return _R101_POP_EXEMPT_CACHE

def _r101_filter(payload, fields):
    if not fields or not isinstance(payload, dict):
        return payload
    # A29-P3: honour dotted sub-selectors. Nearly every [A] tool answers
    # through _ok(), so its whole body sits under one `result` key -- with
    # top-level-only projection a caller could keep or drop that key and
    # nothing in between, i.e. no saving at all on exactly the responses that
    # are too fat to read twice.
    plain, nested = _r101_split_specs(set(fields))
    keep = plain | set(nested) | _R101_ALWAYS_KEEP | _R101_SAFETY_KEEP
    out = {k: v for k, v in payload.items() if k in keep}
    dropped = len(payload) - len(out)
    rescued = sorted((set(payload) & _R101_SAFETY_KEEP) - plain - set(nested))
    # A23-P2: name the keys we could not honour. Without this a typo in
    # `fields=` is indistinguishable from an empty result -- `fields=result`
    # instead of `results` returned a bare envelope and read as "the tool
    # found nothing". Same contract as A17 (dropped body field) and A09
    # (ignored query param): report the drop, keep serving what was
    # understood. Exclude _R101_ALWAYS_KEEP: those are force-kept by the
    # filter, so their absence on a success payload is not a caller mistake.
    unknown = sorted(plain - set(payload) - _R101_ALWAYS_KEEP)
    for head, subs in nested.items():
        if head in plain:
            continue  # the caller asked for the whole branch too; wider wins
        inner = out.get(head)
        if not isinstance(inner, dict):
            unknown.extend(f"{head}.{s}" for s in sorted(subs))
            continue
        sub_out, sub_dropped, sub_rescued, sub_unknown = _r101_project_nested(
            inner, subs)
        out[head] = sub_out
        dropped += sub_dropped
        rescued.extend(f"{head}.{name}" for name in sub_rescued)
        unknown.extend(f"{head}.{name}" for name in sub_unknown)
    rescued = sorted(set(rescued))
    unknown = sorted(set(unknown))
    if rescued:
        # Not error_name: this is a successful response whose projection was
        # unsafe, and poisoning error_name would make every ok:true call look
        # failed to existing callers.
        out["response_incomplete"] = "RESPONSE_INCOMPLETE"
        out["fields_force_kept"] = rescued
        out["fields_force_kept_hint"] = (
            "fields= would have hidden these hash-domain markers. Read them: a "
            "raw_file_bytes digest is NOT a read_file_range content_hash and "
            "seeds no edit authority, so it must not be passed as "
            "write_fix.expected_hash. project_path_resolution is force-kept for "
            "the same reason in the path domain: it says the project_path you "
            "sent was relative and names the directory it actually resolved "
            "against, so a projection cannot hide that the answer may come from "
            "a different project.")
    if unknown:
        out["fields_unknown"] = unknown
        out["fields_unknown_hint"] = (
            "These fields= keys are not in this tool's response and were "
            "ignored; the result is NOT empty because of them. Check spelling "
            "(e.g. 'results' not 'result') or omit fields= to see every key. "
            "fields_available names the keys that DO exist per namespace "
            "('' = top level), so the retry needs no second full call; a "
            "dotted key keeps one nested branch, e.g. fields=result.files.")
        # CASE-1809-E: naming the miss is not enough to prevent a retry loop.
        # Measured: `content` instead of `result` on read_file_range, and
        # `total_lines.deeper` aimed at an int, both left the caller with no
        # way forward except repeating the SAME call without fields= -- the
        # full payload the projection existed to avoid.
        out["fields_available"] = _r101_available_names(payload, unknown)
    # ISS-14: say that this response IS a projection -- but only when the
    # projection was already problematic. A23-P2 pinned the negative case
    # (`CorrectFilterStaysClean`): a marker present on every filtered response
    # carries no information, and the whole point of fields= is fewer tokens.
    # So these two keys ride along with a rescue or an unhonoured key, never on
    # a clean subset the caller asked for exactly.
    if dropped > 0 and (rescued or unknown):
        out["response_truncated"] = True
        out["fields_dropped_count"] = dropped
    return out


# A29 (campaign-1608): `fields=` cures a fat response, but nothing in a fat
# response says WHICH keys are worth naming, so a caller cannot project what it
# has not already paid to read once. These two helpers close that loop, and only
# where it can still change something: no selector was passed AND the body is
# genuinely large. A hint on a small or already-projected response is pure noise
# -- the same reasoning as the A23-P2 negative cases above.
_R101_SIZE_HINT_BYTES = 24_000
_R101_SIZE_HINT_TOP = 6
_R101_CHEAP_FIELD_BYTES = 512


def _r101_payload_may_be_fat(payload) -> bool:
    """Cheap structural gate so a small response never pays for a size probe.

    Measuring means serializing, so the precise pass runs only for a payload
    that already looks heavy: a sizeable container or a long string at the top
    level or one nesting level down. A deep-but-narrow payload can slip past --
    the hint is an optimisation, not a contract, and missing one costs nothing.
    """
    def _heavy(value) -> bool:
        if isinstance(value, (list, tuple, set, dict)):
            return len(value) >= 20
        return isinstance(value, str) and len(value) >= 4000

    for value in payload.values():
        if _heavy(value):
            return True
        if isinstance(value, dict) and any(_heavy(v) for v in value.values()):
            return True
    return False


_R101_HINT_EXPAND_MAX_KEYS = 200
_R101_HINT_DOMINANT_PCT = 60


def _r101_json_width(value) -> int:
    """Serialized byte cost of one value, never raising on an odd object."""
    try:
        return len(json.dumps(value, ensure_ascii=False,
                              default=str).encode("utf-8"))
    except Exception:
        return len(str(value).encode("utf-8", "replace"))


def _r101_size_hint(payload, fields):
    """Name the heaviest keys of a large response that was NOT projected."""
    if fields or not isinstance(payload, dict) or not payload:
        return payload
    if "ca_fields_hint" in payload:
        return payload
    if not _r101_payload_may_be_fat(payload):
        return payload
    sizes = []
    total = 0
    for key, value in payload.items():
        width = _r101_json_width(value)
        sizes.append((key, width))
        total += width + len(key) + 4
    if total < _R101_SIZE_HINT_BYTES:
        return payload
    sizes.sort(key=lambda item: (-item[1], item[0]))
    # A29-P3: nearly every [A] tool answers through _ok(), so its whole body
    # sits under one `result` dict. Naming that key is true but useless advice
    # -- `fields=ok,result` saves nothing at all. When a single dict dominates
    # the response, descend one level and name dotted selectors instead, which
    # _r101_filter now honours.
    top_key, top_width = sizes[0]
    top_value = payload.get(top_key)
    detail = []
    if (isinstance(top_value, dict) and top_value
            and len(top_value) <= _R101_HINT_EXPAND_MAX_KEYS
            and top_width * 100 >= total * _R101_HINT_DOMINANT_PCT):
        detail = [(f"{top_key}.{sub}", _r101_json_width(sub_value))
                  for sub, sub_value in top_value.items()]
        detail.sort(key=lambda item: (-item[1], item[0]))
    ranked = detail or sizes
    cheap = [k for k, width in ranked
             if width <= _R101_CHEAP_FIELD_BYTES and k not in _R101_ALWAYS_KEEP]
    if not cheap:
        # Every field is expensive. The lightest ones are still the honest
        # answer: an example that is only "ok" tells the caller nothing.
        cheap = [k for k, _ in ranked[-2:] if k not in _R101_ALWAYS_KEEP]
    out = dict(payload)
    out["response_bytes"] = total
    out["ca_fields_hint"] = {
        "heaviest_fields": [{"field": k, "bytes": width}
                            for k, width in ranked[:_R101_SIZE_HINT_TOP]],
        "fields_example": ",".join(["ok", *cheap[:_R101_SIZE_HINT_TOP]]),
        "nested_projection": bool(detail),
        "note": ("This response was NOT projected. Re-run with fields=<csv> to "
                 "keep only what you need: a dotted key projects INSIDE a "
                 "nested dict (fields=ok,result.a,result.b), ok/error/"
                 "error_name are always kept, and a key this tool does not "
                 "return comes back as fields_unknown instead of being "
                 "dropped silently."),
    }
    # setdefault, never overwrite: the tool's own next action outranks a
    # generic token-economy suggestion.
    out.setdefault("ca_next_action", "repeat_with_fields_filter_when_rereading")
    return out

def _session_contention_error_payload(tool_name: str, exc: BaseException,
                                      body: dict | None = None):
    """A40: build the retryable error body for a session-store contention that
    escaped a tool as an exception (tools_v15 durable writers reach the
    dispatcher's generic ``except`` instead of translating it themselves).

    Delegates the type check + contract to the single shared classifier in
    code_tools (``_session_contention_fields``) so there is exactly one place
    that decides "this is a retryable stall" -- adding a fourth durable tool
    needs no change here. Returns ``None`` for any non-contention exception so
    the caller keeps its default non-retryable handling.
    """
    classify = getattr(ct, "_session_contention_fields", None)
    if classify is None:
        return None
    try:
        fields = classify(exc)
    except Exception:
        return None
    if fields is None:
        return None
    result = {
        "ok": False,
        "error": str(exc),
        "error_name": fields["error_name"],
        "reason": str(exc),
        "retryable": fields["retryable"],
        "ca_next_action": fields["ca_next_action"],
        "hint": fields["hint"],
    }
    result["ca_guidance"] = _ca_guidance(
        tool_name, str(exc), body if isinstance(body, dict) else {},
        result["error_name"])
    # A40 leftover: this hand-built payload skipped retry_sequence entirely,
    # while the sibling path (a tool that RETURNS _json_error instead of
    # raising) goes through the dispatcher's setdefault block and does carry it.
    # Same error_name, same retryable=True, two different contracts depending
    # on whether the tool caught its own exception -- so CA's retry behavior
    # depended on an implementation detail of the handler. Measured before the
    # fix: the raise path had NO retry_sequence key at all.
    result["retry_sequence"] = _retry_sequence(
        tool_name, result["error_name"], body if isinstance(body, dict) else {})
    try:
        result["phase"] = ct._phase_for_error_name(result["error_name"])
    except Exception:
        pass
    return result


def _error_response(tool_name: str, error: str, status: int, headers: dict,
                    body: dict | None = None, **extra) -> web.Response:
    body = body if isinstance(body, dict) else {}
    error_name = extra.pop("error_name", None) or _infer_error_name(tool_name, error, body)
    # legacy taxonomy in orchestrator.core.errors (39 classes, kept for reference)
    hint = extra.pop("hint", None) or _default_hint(tool_name, error_name)
    ca_next_action = extra.pop("ca_next_action", None) or _ca_next_action(error_name, tool_name)
    guidance = _ca_guidance(tool_name, error, body, error_name)
    contract_meta = _error_contract_meta(error_name, status)
    # V15 R49 — surface machine-readable phase taxonomy (policy | schema |
    # session | environment | retryable_transport | runtime) so CA routes
    # by category instead of parsing free-form messages.
    try:
        _phase = ct._phase_for_error_name(error_name) if hasattr(ct, "_phase_for_error_name") else "runtime"
    except Exception:
        _phase = "runtime"
    payload = {
        "ok": False,
        "error": error or error_name,
        "error_name": error_name,
        "reason": error or error_name,
        "phase": _phase,
        **contract_meta,
        "hint": hint,
        "ca_next_action": ca_next_action,
        "payload_example": guidance.get("payload_example", {}),
        "retry_sequence": extra.pop("retry_sequence", _retry_sequence(
            tool_name, error_name, body,
            corrected_payload=extra.get("corrected_payload"))),
        "ca_guidance": guidance,
        **extra,
    }
    if error_name == "UNKNOWN_TOOL":
        discovery = _unknown_tool_payload(tool_name)
        payload.update(discovery)
        payload.pop("available", None)
        # CASE-1919 item 4: two of the three _unknown_tool_payload branches name a
        # SURFACE instead of ranking candidates (`call_tool` -> mcp_dispatcher,
        # `ca_next_tool` -> response_field), so they carry no discovery_url and an
        # empty suggestions list by design. Subscripting them raised KeyError INSIDE
        # the error handler, and aiohttp turned the richest answer [A] has into a
        # bare HTTP 500 with no JSON and no error_name -- measured on
        # POST /api/call/ca_next_tool and /api/call/ca_hint_review. The in-process
        # tests never saw it because they call _unknown_tool_payload directly.
        # Those branches also bring their own payload_example / ca_next_action,
        # which are more specific than the generic discovery ones, so they win.
        _discovery_url = discovery.get("discovery_url") or "/api/tools/names"
        _suggestions = discovery.get("suggestions") or []
        payload["payload_example"] = (discovery.get("payload_example")
                                      or {"discovery_url": _discovery_url})
        payload["ca_guidance"] = {
            "error_name": error_name,
            "ca_next_action": discovery.get("ca_next_action") or "filter_tool_names",
            "call_pattern": discovery.get("http_equivalent") or ("GET " + _discovery_url),
            "payload_example": payload["payload_example"],
            "suggested_alternative_tool": {},
            "see_also": {name: "/api/tools/" + name + "/schema"
                         for name in _suggestions},
        }
        if discovery.get("surface"):
            payload["ca_guidance"]["surface"] = discovery["surface"]
    payload = _r101_filter(payload, _R101_FIELDS.get())
    # A37 #0b: an error means the CA has NOT yet had a working call of this tool, so re-arm
    # its once-per-bootstrap schema push HERE, at the single producer of every
    # dispatcher-level error payload -- not at each gate, which is how the promise rotted in
    # the first place. Stamped AFTER _r101_filter on purpose: a key added before it is pruned
    # for every caller passing fields=, i.e. exactly the disclosure pushed_schema_note
    # promises would vanish for the callers who read most carefully. A disclosure must never
    # break the answer, hence the bare except.
    try:
        if tool_name and ct.bootstrap_forget(
                ct.bootstrap_ctx_id(_A37_AGENT_CTX.get() or ""), tool_name):
            payload["schema_rearmed"] = True
    except Exception:
        pass
    logger.warning(json.dumps({
        "event": "tool_error",
        "tool": tool_name,
        "error_name": error_name,
        "error": str(error)[:200],
        "ts": datetime.now(timezone.utc).isoformat(),
    }))
    return web.json_response(payload, status=status, headers=headers)

# ─── Route handlers ───────────────────────────────────────────────────────────

async def handle_options(request):
    return web.Response(headers=_cors(request.headers.get('Origin')))


async def handle_root(request):
    if not HTML_FILE.exists():
        return web.Response(text=f"UI file not found: {HTML_FILE}", status=404)
    html_content = HTML_FILE.read_text(encoding='utf-8')
    return web.Response(text=html_content, content_type='text/html',
                        charset='utf-8',
                        headers=_cors(request.headers.get('Origin'), {'Cache-Control': 'no-store'}))


async def handle_favicon(request):
    return web.Response(status=204, headers=_cors(request.headers.get('Origin')))


def _council_active_from_config():
    """(_COUNCIL_AGENTS tuple, labels dict) from the SOT .council/council_config.json
    (code_tools._load_council_config) with hardcoded fallback. ONLY roster
    membership/order + labels are config-driven here; provider caps + default models
    stay wired in code below (provider-sensitive — must not be conflated)."""
    fallback_agents = ("clau", "codo", "ope", "pic", "hermes", "kilo", "gro")
    labels = {a: (a[:1].upper() + a[1:] + "C") for a in fallback_agents}
    labels["pic"] = "PiC"     # avoid double-c label "ComcC"
    labels["hermes"] = "HermC"
    labels["gro"] = "GroC"
    canonical_labels = {
        "clau": "ClauC",
        "codo": "CodoC",
        "ope": "OpeC",
        "pic": "PiC",
        "hermes": "HermC",
        "kilo": "KiloC",
        "gro": "GroC",
    }
    labels.update(canonical_labels)
    try:
        cfg = ct._load_council_config(force=True)
        if cfg and isinstance(cfg.get("agents"), dict):
            acfg = cfg["agents"]
            order = cfg.get("roster_order") or list(acfg.keys())
            seen = set(); active = []
            for aid in order:
                m = acfg.get(aid)
                if m and aid not in seen and m.get("enabled", True):
                    active.append(aid); seen.add(aid)
            for aid, m in acfg.items():
                if aid not in seen and m.get("enabled", True):
                    active.append(aid); seen.add(aid)
            for aid, m in acfg.items():
                if m.get("label"):
                    labels[aid] = m["label"]
            labels.update(canonical_labels)
            if active:
                return tuple(active), labels
    except Exception as exc:
        logger.error("council config load failed: %s", exc)
    return fallback_agents, labels


_COUNCIL_AGENTS, _COUNCIL_AGENT_LABELS = _council_active_from_config()
# Dashboard render order. DERIVED from _COUNCIL_AGENTS, which reads roster_order in
# .council/council_config.json (the SOT) and drops any agent with enabled:false.
# B3 was a HAND-COPIED tuple here that omitted "pic", so that agent silently got no
# dashboard card; test_dashboard_order_and_payload_example_b3_b6.py called this
# constant "a hand-maintained duplicate of the active roster". Deriving it makes that
# class of drift impossible: adding, removing or disabling an agent in the config is
# the only edit needed, and retiring one (kilo/pic, 2026-09-20) needs no code change.
_COUNCIL_DASHBOARD_AGENT_ORDER = _COUNCIL_AGENTS


def _council_dashboard_agents() -> tuple[str, ...]:
    available = set(_COUNCIL_AGENTS)
    return tuple(agent for agent in _COUNCIL_DASHBOARD_AGENT_ORDER if agent in available)

# Max length of a council worker reply returned to the caller. Raised from the old
# hardcoded 2000 (which truncated long replies, e.g. multi-row scan reports).
_COUNCIL_REPLY_MAX = 16000
# Issue 2 fix: broker event log must store the FULL request/reply text within a session
# (was hardcoded text[:_COUNCIL_EVENT_TEXT_MAX], which silently dropped long council messages). This is a
# high defensive cap only (avoid pathological multi-MB payloads); normal replies are
# already bounded by _COUNCIL_REPLY_MAX. raw/reasoning stays separately capped.
_COUNCIL_EVENT_TEXT_MAX = 2_000_000


def _council_reply_tail(text: str, limit: int = _COUNCIL_REPLY_MAX) -> str:
    """Bound broker-visible reply text while preserving the newest/final lines."""
    if not text:
        return ""
    text = str(text).strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[-limit:]


def _council_prefixed_reply_tail(prefix: str, text: str) -> str:
    """Return prefix + tail-capped text without exceeding the visible reply cap."""
    if not text:
        return prefix.rstrip()
    remaining = max(0, _COUNCIL_REPLY_MAX - len(prefix))
    return prefix + _council_reply_tail(text, remaining)


# Per-agent provider + mode capability gate.
#   provider      = native provider id used in the context registry
#   broker        = supports stateless one-shot broker messages
#   context       = supports multi-turn provider-native session resume
_COUNCIL_AGENT_CAPS_FALLBACK = {
    "clau": {"provider": "claude",   "broker": True,  "context": False, "enabled": True},
    "ope":  {"provider": "opencode-zen", "broker": True,  "context": False, "enabled": True},
    "codo": {"provider": "ltnai",    "broker": True,  "context": False, "enabled": True},
    "kilo": {"provider": "ltnai", "broker": True,  "context": False, "enabled": True},
    "pic": {"provider": "ltnai", "broker": True,  "context": False, "enabled": True},
    "hermes": {"provider": "opencode-zen", "broker": True,  "context": False, "enabled": True},
    "gro": {"provider": "ltnai", "broker": True,  "context": False, "enabled": True},
}


def _council_caps_from_config(fallback: dict) -> dict:
    """Overlay per-agent caps from council_config.json (SOT) onto the code fallback.
    A NEW worker added purely in config gets default broker-only caps here, so
    roster + caps + dispatch (argv template) are all config-driven — no code edit."""
    caps = {k: dict(v) for k, v in fallback.items()}
    try:
        cfg = ct._load_council_config(force=True) or {}
        for aid, m in (cfg.get("agents") or {}).items():
            if not isinstance(m, dict):
                continue
            c = caps.setdefault(aid, {"provider": str(m.get("provider") or aid),
                                      "broker": True, "context": False, "enabled": True})
            if m.get("provider"):
                c["provider"] = str(m["provider"])
            mc = m.get("caps") or {}
            for key in ("broker", "context", "enabled"):
                if key in mc:
                    c[key] = bool(mc[key])
            if "enabled" in m:
                c["enabled"] = bool(m["enabled"])
    except Exception as exc:
        logger.error("council caps config load failed: %s", exc)
    return caps


_COUNCIL_AGENT_CAPS = _council_caps_from_config(_COUNCIL_AGENT_CAPS_FALLBACK)
_COUNCIL_AGENT_RUNTIME: dict[str, dict] = {}
_COUNCIL_AGENT_RUNTIME_LOCK = threading.Lock()
_COUNCIL_AGENT_QUEUES: dict[str, deque] = {}
# F044 (2026-07-11): sha256 of the text currently RUNNING per runtime_key, so a
# leader whose sync wait timed out cannot stack an identical second run.
_COUNCIL_INFLIGHT_TEXT: dict[str, str] = {}
_COUNCIL_AGENT_QUEUE_LOCK = threading.Lock()
# Serializes duplicate-active admission with append-only dispatch ledger writes.
_COUNCIL_DISPATCH_LEDGER_LOCK = threading.Lock()
_COUNCIL_AGENT_QUEUE_DRAINING: dict[str, bool] = {}
_COUNCIL_PENDING_TASKS: set[asyncio.Task] = set()
_COUNCIL_MAX_QUEUE_PER_AGENT = int(os.environ.get("COUNCIL_MAX_QUEUE_PER_AGENT", "20"))
_COUNCIL_WORKER_MODELS_LOCK = threading.RLock()
# Provider CLIs have a dedicated bounded lane so they cannot starve ordinary
# project tools or create an unbounded ThreadPoolExecutor backlog.
async def _council_run_blocking(func, /, *args):
    """Run one provider CLI call in the isolated council_provider lane."""
    return await run_in_executor_lane("council_provider", func, *args)


def _council_agent_caps(agent: str) -> dict:
    return _COUNCIL_AGENT_CAPS.get(agent, {"provider": agent, "broker": True, "context": False, "enabled": True})


_COUNCIL_DEFAULT_MODELS_FALLBACK = {
    "clau": {"provider": "claude",      "model": "claude-opus-5"},
    "ope":  {"provider": "openai-compatible", "model": "openai-compatible/grok-4.6-build"},
    "codo": {"provider": "codex",        "model": "grok-4.6-build"},
    "kilo": {"provider": "ltnai", "model": "ltnai/glm-5.2"},
    "pic": {"provider": "ltnai", "model": "deepseek-v4-pro"},
    "hermes": {"provider": "opencode-zen", "model": "deepseek-v4-flash-free"},
    "gro": {"provider": "ltnai", "model": "grok-4.6-build"},
}

def _council_default_models_from_config(fallback: dict) -> dict:
    """Per-agent default provider/model from council_config.json (SOT), code fallback
    only fills gaps. worker_models.json still overrides at dispatch time."""
    models = {k: dict(v) for k, v in fallback.items()}
    try:
        cfg = ct._load_council_config() or {}
        for aid, m in (cfg.get("agents") or {}).items():
            if not isinstance(m, dict):
                continue
            mm = models.setdefault(aid, {"provider": "", "model": ""})
            if m.get("provider"):
                mm["provider"] = str(m["provider"])
            if m.get("model"):
                mm["model"] = str(m["model"])
    except Exception as exc:
        logger.error("council default models config load failed: %s", exc)
    return models


_COUNCIL_DEFAULT_MODELS = _council_default_models_from_config(_COUNCIL_DEFAULT_MODELS_FALLBACK)

def _council_effective_model(agent: str, provider_override: str = "", model_override: str = "") -> dict:
    """Deprecated compatibility view of the truthful model-resolution contract."""
    return _council_resolve_model(agent, provider_override, model_override).as_dict()


@dataclass(frozen=True)
class CouncilModelResolution:
    requested_provider: str
    requested_model: str
    resolved_provider: str
    resolved_model: str
    observed_provider: str | None = None
    observed_model: str | None = None
    resolution_status: str = "unverified"
    warnings: tuple[dict, ...] = ()
    fallback_policy: str = "none"

    def as_dict(self) -> dict:
        value = asdict(self)
        value["warnings"] = list(self.warnings)
        value["provider"] = self.resolved_provider
        value["model"] = self.resolved_model
        return value


def _council_requested_model(agent: str, provider_override: str = "", model_override: str = "") -> tuple[str, str]:
    defaults = _COUNCIL_DEFAULT_MODELS.get(agent, {}) or {}
    return _council_worker_model(agent, str(defaults.get("provider") or ""),
                                 str(defaults.get("model") or ""), provider_override, model_override)


def _council_resolve_model(agent: str, provider_override: str = "", model_override: str = "",
                           fallback_policy: str = "none", allowed_fallbacks=None,
                           risk_level: str = "unknown", dev_level: str = "unknown") -> CouncilModelResolution:
    requested_provider, requested_model = _council_requested_model(agent, provider_override, model_override)
    requested_provider, requested_model = str(requested_provider or "").strip(), str(requested_model or "").strip()
    policy = str(fallback_policy or "none").strip().lower()
    valid = _council_valid_models(agent)
    high_risk = str(risk_level).lower() in {"high", "critical"} or str(dev_level).lower() in {"high", "critical", "architecture"}
    if not requested_model:
        return CouncilModelResolution(requested_provider, requested_model, "", "", resolution_status="unavailable",
                                      warnings=({"warning_name": "MODEL_NOT_CONFIGURED"},), fallback_policy=policy)
    if valid is None or requested_model in valid:
        provider = requested_provider or _council_provider_for_model(agent, requested_model) or _council_agent_caps(agent).get("provider", agent)
        return CouncilModelResolution(requested_provider, requested_model, str(provider or ""), requested_model,
                                      resolution_status="unverified", fallback_policy=policy)
    warnings = ({"warning_name": "REQUESTED_MODEL_UNAVAILABLE", "model": requested_model},)
    allowed = [str(x).strip() for x in (allowed_fallbacks or []) if str(x).strip()]
    if policy == "explicit" and not high_risk:
        fallback = next((candidate for candidate in allowed if candidate in valid), "")
        if fallback:
            provider = _council_provider_for_model(agent, fallback) or requested_provider or _council_agent_caps(agent).get("provider", agent)
            warnings += ({"warning_name": "EXPLICIT_MODEL_FALLBACK", "from_model": requested_model, "to_model": fallback},)
            return CouncilModelResolution(requested_provider, requested_model, str(provider or ""), fallback,
                                          resolution_status="explicit_fallback", warnings=warnings, fallback_policy=policy)
    return CouncilModelResolution(requested_provider, requested_model, "", "", resolution_status="unavailable",
                                  warnings=warnings, fallback_policy=policy)


def _council_attest_model(raw: str, request_id: str = "", session_id: str = "") -> tuple[str | None, str | None, str]:
    # Only provider-native final-result envelopes bound to this dispatch may attest identity.
    identities: set[tuple[str, str]] = set()
    expected_request, expected_session = str(request_id or ""), str(session_id or "")
    for line in str(raw or "").splitlines():
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or str(value.get("type") or "") not in {"result", "final_result"}:
            continue
        record_request = str(value.get("request_id") or "")
        record_session = str(value.get("session_id") or "")
        request_matches = bool(expected_request and record_request == expected_request)
        session_matches = bool(expected_session and record_session == expected_session)
        if expected_request and record_request != expected_request:
            continue
        if expected_session and record_session != expected_session:
            continue
        if not (request_matches or session_matches):
            continue
        provider = str(value.get("provider") or "").strip()
        model = str(value.get("model") or "").strip()
        if provider or model:
            identities.add((provider, model))
    if len(identities) > 1:
        return None, None, "COUNCIL_MODEL_ATTESTATION_CONFLICT"
    if not identities:
        return None, None, ""
    provider, model = next(iter(identities))
    return provider or None, model or None, ""


def _council_harness_started(raw: str) -> bool:
    """True when the CLI harness really launched, proven by its own init envelope.

    A launcher that never spawned the exe emits NO structured provider output at
    all -- only the shell's own refusal on stderr. So any provider-native envelope
    is positive proof that the operating system ACCEPTED the argv we built.

    claude emits ``{"type":"system","subtype":"init"}``; codex / opencode / kilo emit
    their own records instead, so key on the generic envelope shape too -- otherwise
    those harnesses keep the false positive this guard exists to remove.
    """
    envelope_keys = ("type", "session_id", "sessionID", "thread_id", "threadId", "messageID")
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            value = json.loads(stripped)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and any(k in value for k in envelope_keys):
            return True
    return False


_COUNCIL_ARGV_REFUSAL_MAX_LINES = 3


def _council_launcher_argv_too_long(raw: str, returncode: int | None = None) -> bool:
    """Detect a REAL argv-too-long launcher failure, not the phrase merely quoted.

    THREE independent scopes, ALL required, because `raw` is stdout+stderr of the
    whole worker run -- every stream-json ``tool_result`` a worker read plus every
    stderr line of processes the worker itself spawned:
      * ``returncode == 0`` is positive, harness-AGNOSTIC proof the argv was accepted:
        a genuine launcher refusal means cmd.exe/CreateProcess never spawned the
        child, so the child cannot possibly have exited 0. Checked FIRST, and unlike
        the init-envelope scope it holds for harnesses that emit no JSON at all.
        ``returncode is None`` (killed / timed out) has no such proof, so it falls
        through to the remaining scopes and keeps the pre-existing behaviour.
      * the harness must never have started (no init envelope). Once claude.exe is
        running, argv was accepted by definition, so a launcher failure is impossible.
      * the phrase must sit on a NON-JSON line, i.e. launcher / cmd.exe stderr, never
        inside a stream-json record describing content the worker merely READ, AND
        the blob must carry no substantive harness output: a real shell refusal is
        emitted ALONE, so more than _COUNCIL_ARGV_REFUSAL_MAX_LINES = 3 non-empty
        lines means a worker actually produced prose and merely mentioned the phrase.
        3 (not 1) leaves headroom for a launcher banner / blank-ish wrapper lines
        around the single refusal, while staying far below any real answer.

    Without this scoping a substring match over the whole blob relabels a SUCCESSFUL
    run as a launcher failure. Observed 2026-08-27 13:24 (request mcp_a5f2657e1404):
    returncode 0, valid init envelope, MCP connected, correct model -- still reported
    COUNCIL_HARNESS_ARGV_TOO_LONG, because the worker was auditing this very file.
    Observed again 2026-08-27 14:48 (run-979141e5, agent gro, grok-4.6-build): the
    harness emitted PLAIN VIETNAMESE TEXT with no JSON line anywhere, so the
    init-envelope scope could never be satisfied, and the answer quoted the phrase on
    a non-JSON line -- both R2 scopes passed on a returncode-0 SUCCESS. Hence scope 1.
    """
    if returncode == 0:
        return False
    if not raw:
        return False
    if _council_harness_started(raw):
        return False
    if len([ln for ln in str(raw).splitlines() if ln.strip()]) > _COUNCIL_ARGV_REFUSAL_MAX_LINES:
        return False
    for line in str(raw).splitlines():
        stripped = line.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                json.loads(stripped)
                continue
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        if "command line is too long" in stripped.lower():
            return True
    return False


def _council_finalize_model_resolution(resolution: CouncilModelResolution, raw: str,
                                       risk_level: str = "unknown", dev_level: str = "unknown",
                                       request_id: str = "", session_id: str = "",
                                       returncode: int | None = None) -> tuple[CouncilModelResolution, str]:
    # A harness that never started emits no attestation at all, which used to be
    # reported as the misleading COUNCIL_MODEL_UNVERIFIED. Name the launcher failure.
    if _council_launcher_argv_too_long(raw, returncode):
        return (replace(resolution, resolution_status="unavailable",
                        warnings=resolution.warnings + ({"warning_name": "HARNESS_ARGV_TOO_LONG"},)),
                "COUNCIL_HARNESS_ARGV_TOO_LONG")
    observed_provider, observed_model, attestation_error = _council_attest_model(raw, request_id, session_id)
    high_risk = str(risk_level).lower() in {"high", "critical"} or str(dev_level).lower() in {"high", "critical", "architecture"}
    if attestation_error:
        final = replace(resolution, resolution_status="unavailable",
                        warnings=resolution.warnings + ({"warning_name": "HARNESS_IDENTITY_CONFLICT"},))
        return final, attestation_error
    provider_mismatch = bool(observed_provider and observed_provider != resolution.resolved_provider)
    model_mismatch = bool(observed_model and observed_model != resolution.resolved_model)
    if provider_mismatch or model_mismatch:
        warning_name = "HARNESS_PROVIDER_SUBSTITUTED" if provider_mismatch and not model_mismatch else "HARNESS_MODEL_SUBSTITUTED"
        final = replace(resolution, observed_provider=observed_provider, observed_model=observed_model,
                        resolution_status="unavailable", warnings=resolution.warnings + ({"warning_name": warning_name},))
        return final, "COUNCIL_MODEL_SUBSTITUTED"
    identity_complete = bool(observed_model and (observed_provider or not resolution.resolved_provider))
    if identity_complete:
        status = "explicit_fallback" if resolution.resolution_status == "explicit_fallback" else "exact"
        return replace(resolution, observed_provider=observed_provider, observed_model=observed_model,
                       resolution_status=status), ""
    final = replace(resolution, observed_provider=observed_provider, observed_model=observed_model,
                    resolution_status="unverified",
                    warnings=resolution.warnings + ({"warning_name": "HARNESS_MODEL_UNVERIFIED"},))
    return final, "COUNCIL_MODEL_UNVERIFIED" if high_risk else ""


def _council_validate_model_setting(agent: str, provider: str, model: str) -> dict:
    if agent not in _COUNCIL_AGENTS:
        return {"ok": False, "error_name": "INVALID_AGENT", "error": "unknown agent", "allowed": list(_COUNCIL_AGENTS)}
    if not model:
        return {"ok": False, "error_name": "MISSING_MODEL", "error": "model required for set", "valid": _council_valid_models(agent)}
    valid = _council_valid_models(agent)
    if valid is not None and model not in valid:
        return {"ok": False, "error_name": "INVALID_MODEL", "error": f"'{model}' not in curated ID_MODELS for {agent}", "valid": valid}
    expected_provider = _council_provider_for_model(agent, model)
    warnings = []
    if expected_provider and provider and provider != expected_provider:
        warnings.append({"warning_name": "PROVIDER_MODEL_MISMATCH", "expected_provider": expected_provider,
                         "received_provider": provider, "model": model})
    return {"ok": True, "provider": provider or expected_provider or _council_agent_caps(agent).get("provider", agent),
            "model": model, "warnings": warnings}

def _council_supports_mode(agent: str, mode: str) -> bool:
    caps = _council_agent_caps(agent)
    if not caps.get("enabled", True):
        return False
    return bool(caps.get(mode, False))


def _council_runtime_file() -> Path:
    return _HERE / ".council" / "runtime_state.json"


# ─── BROKER-RETRY-AFTER-SUCCESS guard (F1+F2+F3) ────────────────────────────────
# A duplicate/retry dispatch of a one-shot worker task, sent right after the worker
# already wrote a valid output artifact and replied ok:true, could falsely FAIL when
# the provider/API is transiently down (incident: DISTILATION_FINAL_PLAN Round 7).
# Fix: treat a complete output artifact as the idempotent source of truth — gate the
# dispatch on artifact completeness (F1) and a filesystem dedup cache (F2), using a
# LAST-line completion detector (F3) so YAML-frontmatter / truncated writes don't
# produce false positives.

_COUNCIL_ARTIFACT_DONE_MARKERS = (
    "BROKER TASK COMPLETE", "STOP:", "CONTINUE:", "DONE", "COMPLETE",
)
# Labels that precede an EXPECTED OUTPUT artifact path in our worker prompts. Only a
# path tied to one of these (or a D:\BR\... path in a prompt that has a write/output
# verb) is treated as an output contract — free-form messages are never gated.
_COUNCIL_ARTIFACT_PATH_LABELS = (
    "write output to", "write outputs", "write output", "write:",
    "full_output", "fix_report", "fix report", "codo_packet", "codo packet",
    "report file", "output file", "report:", "packet:",
)


def _artifact_last_line_complete(path) -> bool:
    """F3: True iff the artifact exists, is non-zero, and its FINAL non-empty stripped
    line starts with a known completion marker. Checking the LAST line (not the first)
    defeats YAML-frontmatter false positives (a line-1 'STOP:') and catches truncated /
    mid-write artifacts whose tail carries no marker."""
    try:
        p = Path(path)
        if not p.exists() or not p.is_file() or p.stat().st_size <= 0:
            return False
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    last = ""
    for line in reversed(text.splitlines()):
        s = line.strip()
        if s:
            last = s
            break
    if not last:
        return False
    up = last.upper()
    return any(up.startswith(m) for m in _COUNCIL_ARTIFACT_DONE_MARKERS)


def _council_extract_artifact_path(text: str):
    """F1 helper: extract an EXPLICIT expected output-artifact path from a worker prompt.
    Returns the LAST labelled .md path (the artifact the worker must WRITE), else a
    D:\\BR\\...\\*.md path only when the prompt also has a write/output verb, else None.
    Conservative by design so prompts without an output contract are never gated."""
    s = str(text or "")
    if not s:
        return None
    labelled = []
    br_paths = []
    for m in re.finditer(r'[A-Za-z]:\\[^\n\r`"\'<>|]*?\.md', s):
        path = m.group(0).strip().rstrip(".,;:)”’")
        ctx = s[max(0, m.start() - 120):m.start()].lower()
        if any(lbl in ctx for lbl in _COUNCIL_ARTIFACT_PATH_LABELS):
            labelled.append(path)
        if path.upper().startswith("D:\\BR\\"):
            br_paths.append(path)
    if labelled:
        return labelled[-1]
    if br_paths and re.search(r'\b(write|output|report|packet|save|produce)\b', s, re.I):
        return br_paths[-1]
    return None


def _artifact_already_complete(target: str, text: str):
    """F1: if the prompt names an explicit output artifact that is ALREADY complete on
    disk, return a short-circuit note dict; else None (caller proceeds to dispatch)."""
    try:
        artifact = _council_extract_artifact_path(text)
        if artifact and _artifact_last_line_complete(artifact):
            return {"artifact": artifact,
                    "note": "BROKER-RETRY-AFTER-SUCCESS guard: artifact already "
                            "complete (idempotent); dispatch skipped"}
    except Exception:
        return None
    return None


def _council_completed_tasks_dir() -> Path:
    return _HERE / ".council" / "completed_tasks"


def _compute_task_key(target: str, text: str, artifact_path: str = "") -> str:
    """F2: stable idempotency key = sha256(target + normalized_text + artifact_path)[:16].
    Including the extracted artifact path avoids free-form collisions (Codo caution)."""
    norm = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    raw = (f"{str(target or '').strip().lower()}\x00{norm}\x00"
           f"{str(artifact_path or '').strip().lower()}")
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _council_completed_task_read(key: str):
    try:
        p = _council_completed_tasks_dir() / f"{key}.json"
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _council_completed_task_write(key: str, payload: dict) -> None:
    try:
        d = _council_completed_tasks_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{key}.json"
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
        _council_dashboard_payload_invalidate()
    except Exception as exc:
        logger.error("council completed task cache write failed: %s", exc)


_COUNCIL_RUNTIME_PERSIST_LOCK = threading.Lock()
_COUNCIL_RUNTIME_FILE_LOCK_TIMEOUT_SECONDS = 1.0
_COUNCIL_RUNTIME_FILE_LOCK_RETRY_SECONDS = 0.01


def _council_runtime_write(agent: str, value: dict, *, reserve: bool = False,
                           expected_request_id: str | None = None,
                           observed_current: dict | None = None) -> str:
    """Write one slot under the process lock, optionally comparing its current owner."""
    value = _council_runtime_enforce_terminal_error(agent, value)
    f = _council_runtime_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = f.with_suffix(f.suffix + ".lock").open("a+b")
    locked = False
    try:
        lock_handle.seek(0)
        if lock_handle.read(1) == b"":
            lock_handle.write(b"0")
            lock_handle.flush()
        lock_handle.seek(0)
        locked = acquire_file_lock_until(
            lock_handle,
            deadline=time.monotonic() + _COUNCIL_RUNTIME_FILE_LOCK_TIMEOUT_SECONDS,
            retry_seconds=_COUNCIL_RUNTIME_FILE_LOCK_RETRY_SECONDS,
        )
        if not locked:
            return "persist_error"
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        if not isinstance(data, dict):
            data = {}
        current = data.get(agent) or {}
        if reserve and current.get("state") == "running" and not _runtime_is_stale(current):
            return "busy"
        if (expected_request_id is not None
                and current.get("request_id", "") != expected_request_id):
            if observed_current is not None:
                observed_current.update(current)
            return "owner_changed"
        data[agent] = dict(value)
        tmp = f.with_name(f"{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, f)
        finally:
            tmp.unlink(missing_ok=True)
        return "ok"
    finally:
        if locked:
            try:
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        lock_handle.close()


def _council_runtime_persist(agent: str, value: dict,
                             expected_request_id: str | None = None) -> bool:
    """Atomically merge one runtime entry if the persisted owner still matches."""
    if not _COUNCIL_RUNTIME_PERSIST_LOCK.acquire(timeout=1.0):
        logger.error("council runtime persist lock timed out")
        return False
    try:
        observed_current = {}
        outcome = _council_runtime_write(
            agent, value, expected_request_id=expected_request_id,
            observed_current=observed_current,
        )
        if outcome == "owner_changed":
            value.clear()
            value.update(observed_current)
            with _COUNCIL_AGENT_RUNTIME_LOCK:
                local = _COUNCIL_AGENT_RUNTIME.get(agent) or {}
                if local.get("request_id", "") == (expected_request_id or ""):
                    _COUNCIL_AGENT_RUNTIME[agent] = dict(observed_current)
            _council_dashboard_payload_invalidate()
            return True
        if outcome != "ok":
            logger.error("council runtime persist file lock timed out")
            return False
        _council_dashboard_payload_invalidate()
        return True
    except Exception as exc:
        logger.error("council runtime persist failed: %s", exc)
        return False
    finally:
        _COUNCIL_RUNTIME_PERSIST_LOCK.release()


_COUNCIL_TERMINAL_ERROR_STATES = {"error", "failed", "cancelled"}


def _council_worker_log_tail(agent: str, max_bytes: int = 200) -> str:
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(agent)).strip("_")[:120]
    path = _HERE / ".council" / f"live_raw_{safe_key}.log"
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read(max_bytes).decode("utf-8", "replace").strip()
    except Exception:
        return ""


def _council_runtime_enforce_terminal_error(agent: str, value: dict) -> dict:
    """Terminal failure records must always explain why they terminated."""
    out = dict(value or {})
    state = str(out.get("state") or "").strip().lower()
    if state in _COUNCIL_TERMINAL_ERROR_STATES and not str(out.get("error") or "").strip():
        tail = _council_worker_log_tail(agent)
        out["error"] = tail or f"{state}: worker ended without an explicit reason"
    return out


_DAEMON_BOOT_TIME = time.time()


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except Exception:
        return True
    if pid <= 0:
        return True
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    except Exception:
        return True  # uncertain -> assume alive, never falsely clear a live run


_ROOM_LOCKS: dict[str, threading.RLock] = {}
_ROOM_LOCKS_GUARD = threading.Lock()
_ROOM_TOKEN_SECRET = os.environ.get("COUNCIL_ROOM_TOKEN_SECRET", "project-orchestrator-room-secret").encode("utf-8")
_ROOM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ROOM_TERMINAL_ERRORS = {"error", "failed", "cancelled", "terminal-error"}


def _room_resolve_dir(room_id: str) -> Path:
    room_id = str(room_id or "")
    if not _ROOM_ID_RE.fullmatch(room_id):
        raise ValueError("ROOM_ID_INVALID")
    root = (_HERE / ".council" / "rooms").resolve()
    path = (root / room_id).resolve()
    if path.parent != root:
        raise ValueError("ROOM_PATH_INVALID")
    return path


def _room_lock(room_id: str) -> threading.RLock:
    with _ROOM_LOCKS_GUARD:
        return _ROOM_LOCKS.setdefault(str(room_id), threading.RLock())


def _room_atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _room_read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _room_token(room_id: str, expiry: float, secret: bytes = _ROOM_TOKEN_SECRET) -> str:
    payload = f"{room_id}.{int(expiry)}".encode("utf-8")
    signature = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    encoded_room = base64.urlsafe_b64encode(room_id.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{encoded_room}.{int(expiry)}.{signature}"


def _room_validate_token(room_id: str, room_token: str, now: float | None = None) -> dict:
    try:
        encoded_room, expiry_text, signature = str(room_token).split(".", 2)
        token_room = base64.urlsafe_b64decode(encoded_room + "=" * (-len(encoded_room) % 4)).decode("utf-8")
        expiry = int(expiry_text)
    except (ValueError, TypeError, UnicodeError):
        raise PermissionError("ROOM_TOKEN_INVALID")
    if token_room != str(room_id) or expiry <= int(time.time() if now is None else now):
        raise PermissionError("ROOM_TOKEN_INVALID")
    expected = _room_token(token_room, expiry).rsplit(".", 1)[1]
    if not hmac.compare_digest(signature, expected):
        raise PermissionError("ROOM_TOKEN_INVALID")
    return {"room_id": token_room, "expires_at": expiry}


def _room_register(room_id: str, room_token: str | None = None, ttl_sec: int = 3600, metadata: dict | None = None) -> dict:
    del room_token
    _room_garbage_collect()
    room_dir = _room_resolve_dir(room_id)
    # B020: ceil, not int(). `_room_validate_token` compares
    # `expiry <= int(time.time())`, so flooring the START of the window while the
    # END is also floored charged the caller for the fraction of the second
    # already elapsed: registering at t=100.95 with ttl_sec=1 gave expires_at=101,
    # and a validate 60 ms later at t=101.01 raised ROOM_TOKEN_INVALID -- a
    # one-second token with an effective life of 0.05s. The lifetime was
    # `ttl_sec - (time.time() % 1)`, decided by where in the wall-clock second the
    # register happened to land, which is why the pinning test passed 10/10 alone
    # and failed inside the slower full suite. Ceiling makes the advertised
    # expires_at always at least ttl_sec away; it can overshoot by under a second,
    # which is the safe direction for an authorization window's own grace.
    expiry = math.ceil(time.time()) + max(1, int(ttl_sec))
    token = _room_token(str(room_id), expiry)
    meta = {"room_id": str(room_id), "room_token": token, "expires_at": expiry, "created_at": time.time()}
    if metadata:
        meta.update(dict(metadata))
        meta.update({"room_id": str(room_id), "room_token": token, "expires_at": expiry})
    with _room_lock(room_id):
        room_dir.mkdir(parents=True, exist_ok=True)
        _room_atomic_json(room_dir / "room_meta.json", meta)
    return {"ok": True, "room_id": str(room_id), "room_token": token, "expires_at": expiry, "room_dir": str(room_dir)}


def _room_append_event(room_id: str, event: dict, room_token: str | None = None) -> dict:
    room_dir = _room_resolve_dir(room_id)
    record = dict(event or {})
    record.setdefault("event_id", uuid.uuid4().hex)
    record.setdefault("created_at", time.time())
    line = (json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with _room_lock(room_id):
        meta = _room_read_json(room_dir / "room_meta.json", {})
        _room_validate_token(room_id, room_token or meta.get("room_token"))
        path = room_dir / "events.jsonl"
        previous = path.read_bytes() if path.exists() else b""
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_bytes(previous + line)
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
    return {"ok": True, "event": record}


def _room_update_status(room_id: str, status: dict | None = None, room_token: str | None = None, **fields) -> dict:
    room_dir = _room_resolve_dir(room_id)
    with _room_lock(room_id):
        meta = _room_read_json(room_dir / "room_meta.json", {})
        _room_validate_token(room_id, room_token or meta.get("room_token"))
        current = _room_read_json(room_dir / "status.json", {})
        current.update(status or {})
        current.update(fields)
        now = time.time()
        current["updated_at"] = now
        current.setdefault("worker_pid", None)
        current.setdefault("task_id", None)
        current.setdefault("artifact_path", None)
        current.setdefault("last_output_at", None)
        if current["last_output_at"] is not None:
            silent_for = max(0.0, now - float(current["last_output_at"]))
            current["silent_for"] = silent_for
            current["silent_for_sec"] = silent_for
            current["suspected_stalled"] = silent_for >= _COUNCIL_STALL_LIMIT_SEC
        else:
            current["silent_for"] = None
            current["silent_for_sec"] = None
            current["suspected_stalled"] = False
        current["worker_alive"] = bool(current["worker_pid"] and _pid_alive(current["worker_pid"]))
        room_dir.mkdir(parents=True, exist_ok=True)
        _room_atomic_json(room_dir / "status.json", current)
    return {"ok": True, "status": current}


def _room_check_duplicate(room_id: str, worker: str, task_id: str, text_sha: str = "", status: str = "pending", room_token: str | None = None) -> dict:
    room_dir = _room_resolve_dir(room_id)
    key = f"{worker}:{task_id}"
    with _room_lock(room_id):
        meta = _room_read_json(room_dir / "room_meta.json", {})
        _room_validate_token(room_id, room_token or meta.get("room_token"))
        index = _room_read_json(room_dir / "index.json", {})
        existing = index.get(key)
        requested_status = str(status).lower()
        if existing and requested_status in _ROOM_TERMINAL_ERRORS:
            existing = dict(existing)
            existing["status"] = status
            existing["updated_at"] = time.time()
            index[key] = existing
            _room_atomic_json(room_dir / "index.json", index)
            return {"ok": True, "dispatched": False, "duplicate": False, "record": existing}
        if existing and str(existing.get("status", "")).lower() not in _ROOM_TERMINAL_ERRORS:
            result = {"ok": True, "dispatched": False, "duplicate": True, "record": existing}
            if text_sha and text_sha != existing.get("text_sha"):
                result["text_sha_mismatch"] = True
            return result
        attempt_no = int(existing.get("attempt_no", 0)) + 1 if existing else 1
        record = {"key": key, "worker": worker, "task_id": task_id, "text_sha": text_sha, "attempt_no": attempt_no, "status": status, "claimed_at": time.time()}
        index[key] = record
        room_dir.mkdir(parents=True, exist_ok=True)
        _room_atomic_json(room_dir / "index.json", index)
    return {"ok": True, "dispatched": True, "duplicate": False, "record": record}


def _room_garbage_collect(room_root: Path | None = None, now: float | None = None) -> dict:
    if room_root is not None and not isinstance(room_root, Path):
        room_root = Path(room_root)
    root = (room_root or (_HERE / ".council" / "rooms")).resolve()
    cutoff = time.time() if now is None else float(now)
    archived = []
    skipped = []
    if not root.exists():
        return {"ok": True, "archived": archived, "skipped": skipped}
    archive_root = root / "archive"
    for room_dir in sorted(root.iterdir(), key=lambda item: item.name):
        if not room_dir.is_dir() or room_dir.name == "archive":
            continue
        with _room_lock(room_dir.name):
            meta = _room_read_json(room_dir / "room_meta.json", {})
            status = _room_read_json(room_dir / "status.json", {})
            active = str(status.get("status", "")).lower() in {"active", "running", "pending"} or bool(status.get("worker_alive"))
            if active or float(meta.get("expires_at", float("inf"))) > cutoff:
                skipped.append(room_dir.name)
                continue
            archive_root.mkdir(parents=True, exist_ok=True)
            target = archive_root / f"{room_dir.name}-{int(cutoff)}"
            if target.exists():
                target = archive_root / f"{room_dir.name}-{int(cutoff)}-{uuid.uuid4().hex[:8]}"
            os.replace(room_dir, target)
            archived.append(room_dir.name)
    return {"ok": True, "archived": archived, "skipped": skipped}
def _runtime_is_stale(entry: dict) -> bool:
    """A 'running' entry is orphaned/stale if the process that owned it is gone, or (legacy
    entries without pid) if it predates this process's boot — e.g. after a power-loss/crash
    that left the state stuck at 'running' (the finish write never happened)."""
    if not entry or entry.get("state") != "running":
        return False
    pid = entry.get("pid")
    if pid:
        if not _pid_alive(pid):
            return True
        # In-process leak guard (2026-07-12): pid here is [A]'s own pid, which is
        # alive as long as the daemon runs — it cannot rescue an entry whose finish
        # write was lost. No legitimate run outlives the 4h dispatch hard cap, so a
        # 'running' entry older than 6h is orphaned state, not a live worker.
        return (time.time() - (entry.get("started_at") or time.time())) > 21600
    return (entry.get("started_at") or 0) < _DAEMON_BOOT_TIME


def _council_runtime_key(agent: str, model: str = "") -> str:
    agent = canonical_id(agent)
    model = canonical_id(model)
    if not model:
        return agent
    safe_model = re.sub(r"[^a-z0-9_.-]+", "_", model).strip("_")[:96]
    return f"{agent}::{safe_model or 'model'}"


def _council_runtime_base_agent(runtime_key: str) -> str:
    return canonical_id(runtime_key).split("::", 1)[0]


def _assert_council_runtime_key_uniqueness() -> None:
    """Validate configured council identities before the HTTP server starts."""
    assert_unique_canonical_ids(_COUNCIL_AGENTS, label="council agent id")
    runtime_keys = [
        _council_runtime_key(agent, _council_effective_model(agent).get("model", ""))
        for agent in _COUNCIL_AGENTS
    ]
    assert_unique_canonical_ids(runtime_keys, label="council runtime key")


_COUNCIL_STALL_LIMIT_SEC = max(120, int(os.environ.get("COUNCIL_STALL_LIMIT_SEC", "900")))
_COUNCIL_MONITOR_INTERVAL_SEC = max(30, int(os.environ.get("COUNCIL_MONITOR_INTERVAL_SEC", "60")))
_COUNCIL_STALL_ALERT_MAX_ENTRIES = max(1, int(os.environ.get("COUNCIL_STALL_ALERT_MAX_ENTRIES", "200")))
_COUNCIL_STALL_ALERT_MAX_BYTES = max(4096, int(os.environ.get("COUNCIL_STALL_ALERT_MAX_BYTES", str(1024 * 1024))))


def _council_cap_stall_alerts(path: Path) -> None:
    """Keep the newest bounded alert records using an atomic replacement."""
    try:
        raw_lines = path.read_bytes().splitlines(keepends=True)
        kept = raw_lines[-_COUNCIL_STALL_ALERT_MAX_ENTRIES:]
        size = sum(len(line) for line in kept)
        while kept and size > _COUNCIL_STALL_ALERT_MAX_BYTES:
            size -= len(kept.pop(0))
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_bytes(b"".join(kept))
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    except Exception:
        logger.exception("failed to cap council stall alerts")


def _council_stall_alert(runtime_key: str, entry: dict, reason: str) -> None:
    """Make a stalled/cancelled worker visible to the leader.

    The old monitor recorded `suspected_stalled` into the state file and stopped there,
    so a worker that stayed alive while producing nothing was never reported and never
    cancelled - the leader kept waiting on a dead dispatch with no signal at all.
    """
    try:
        alert = {
            "ts": time.time(),
            "type": "worker_stalled",
            "runtime_key": runtime_key,
            "agent": entry.get("agent"),
            "provider": entry.get("provider"),
            "model": entry.get("model"),
            "request_id": entry.get("request_id"),
            "worker_pid": entry.get("worker_pid"),
            "silent_for_sec": int(entry.get("silent_for_sec") or 0),
            "reason": reason,
        }
        path = _HERE / ".council" / "stall_alerts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(alert, ensure_ascii=False) + "\n")
        _council_cap_stall_alerts(path)
    except Exception:
        pass
    logger.warning("council worker stalled: %s (%s) silent %ss -> %s",
                   runtime_key, entry.get("model"),
                   int(entry.get("silent_for_sec") or 0), reason)


def _council_preserve_partial_artifact(entry: dict) -> str:
    """Copy <artifact>.partial to a stable sibling before a stalled worker is killed."""
    artifact = str(entry.get("artifact_path") or "").strip()
    if not artifact:
        return ""
    partial = Path(artifact + ".partial")
    if not partial.is_file():
        return ""
    preserved = Path(artifact + ".stalled.partial")
    try:
        preserved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(partial, preserved)
        return str(preserved)
    except Exception as exc:
        logger.warning("failed to preserve council partial artifact %s: %s", partial, exc)
        return ""


def _council_cancel_worker(entry: dict) -> bool:
    """Terminate a stalled worker process tree. Returns True when a kill was issued."""
    pid = entry.get("worker_pid")
    if not pid:
        return False
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                       capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False


async def _council_runtime_monitor(interval_sec: int = 0) -> None:
    """Watch council workers; report and cancel one that has gone silent.

    Two behaviours the previous version lacked:
      * it polled at a 600s floor, so a stall took up to 20 minutes to even notice;
      * it only ever set a flag. It never told the leader and never cancelled, so a
        worker alive but emitting nothing blocked its slot indefinitely.
    Now: poll every COUNCIL_MONITOR_INTERVAL_SEC (default 60), and once a running
    worker's live log has not grown for COUNCIL_STALL_LIMIT_SEC (default 900 = 15 min),
    write a leader-visible alert to .council/stall_alerts.jsonl, kill the process tree,
    and release the slot with a real error string instead of an empty one.
    """
    interval = max(30, int(interval_sec or _COUNCIL_MONITOR_INTERVAL_SEC))
    while True:
        await asyncio.sleep(interval)
        now = time.time()
        try:
            persisted = {}
            runtime_file = _council_runtime_file()
            if runtime_file.exists():
                persisted = json.loads(runtime_file.read_text(encoding="utf-8")) or {}
            for runtime_key, raw_entry in list(persisted.items()):
                entry = dict(raw_entry or {})
                if entry.get("state") != "running":
                    continue
                worker_pid = entry.get("worker_pid")
                worker_alive = bool(worker_pid and _pid_alive(worker_pid))
                entry["last_monitor_at"] = now
                entry["monitor_interval_sec"] = interval
                entry["worker_alive"] = worker_alive
                safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", runtime_key).strip("_")[:120]
                live_path = _HERE / ".council" / f"live_raw_{safe_key}.log"
                output_mtime = live_path.stat().st_mtime if live_path.exists() else 0.0
                previous_mtime = float(entry.get("monitor_output_mtime") or 0.0)
                entry["monitor_output_mtime"] = output_mtime

                # Track how long the log has been silent, not just "did it move since
                # the last poll" - a single missed tick is not a stall.
                if output_mtime > previous_mtime:
                    entry["last_output_at"] = now
                last_output_at = float(entry.get("last_output_at")
                                       or entry.get("started_at") or now)
                silent_for = max(0.0, now - last_output_at)
                entry["silent_for_sec"] = int(silent_for)
                entry["suspected_stalled"] = bool(worker_alive and silent_for >= interval * 2)

                if worker_pid and not worker_alive:
                    entry.update({
                        "state": "error",
                        "finished_at": now,
                        "error": "worker_process_exited",
                        "suspected_stalled": False,
                    })
                elif worker_alive and silent_for >= _COUNCIL_STALL_LIMIT_SEC:
                    _council_stall_alert(runtime_key, entry, "cancelled_after_silence")
                    preserved_partial = _council_preserve_partial_artifact(entry)
                    killed = _council_cancel_worker(entry)
                    entry.update({
                        "state": "error",
                        "finished_at": now,
                        "error": ("worker_stalled_no_output_for_%ss; cancelled=%s"
                                  % (int(silent_for), killed)),
                        "suspected_stalled": False,
                        "cancelled_by_monitor": True,
                        "preserved_partial_artifact": preserved_partial,
                    })
                persisted_ok = await asyncio.to_thread(
                    _council_runtime_persist, runtime_key, entry,
                    entry.get("request_id", ""))
                if persisted_ok:
                    with _COUNCIL_AGENT_RUNTIME_LOCK:
                        _COUNCIL_AGENT_RUNTIME[runtime_key] = dict(entry)
                else:
                    logger.error("runtime monitor state not published for %s", runtime_key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("council runtime monitor failed: %s", exc)
def _council_runtime_start(agent: str, request_id: str, mode: str,
                           base_agent: str = "", provider: str = "", model: str = "",
                           artifact_path: str = "", model_resolution: dict | None = None) -> dict | None | bool:
    reservation_token = uuid.uuid4().hex
    with _COUNCIL_AGENT_RUNTIME_LOCK:
        current = _COUNCIL_AGENT_RUNTIME.get(agent) or {}
        if current.get("state") == "running" and not _runtime_is_stale(current):
            return None
        resolution = dict(model_resolution or _council_resolve_model(base_agent or _council_runtime_base_agent(agent), provider, model).as_dict())
        value = {
            "state": "running", "request_id": request_id, "mode": mode,
            "started_at": time.time(), "finished_at": None, "error": "",
            "pid": os.getpid(), "worker_pid": None,
            "agent": base_agent or _council_runtime_base_agent(agent), "runtime_key": agent,
            "provider": provider or "", "model": model or "", "model_resolution": resolution,
            "artifact_path": artifact_path or "", "reservation_token": reservation_token,
        }
        _COUNCIL_AGENT_RUNTIME[agent] = dict(value)
    try:
        persisted = _council_runtime_write(agent, value, reserve=True)
    except Exception as exc:
        logger.error("council runtime reservation failed: %s", exc)
        persisted = "persist_error"
    if persisted != "ok":
        with _COUNCIL_AGENT_RUNTIME_LOCK:
            owned = _COUNCIL_AGENT_RUNTIME.get(agent) or {}
            if owned.get("reservation_token") == reservation_token:
                _COUNCIL_AGENT_RUNTIME.pop(agent, None)
        return None if persisted == "busy" else False
    _council_dashboard_payload_invalidate()
    return dict(value)


def _council_runtime_set_worker_pid(agent: str, request_id: str, worker_pid: int) -> dict:
    with _COUNCIL_AGENT_RUNTIME_LOCK:
        current = _COUNCIL_AGENT_RUNTIME.get(agent) or {}
        if current.get("request_id") != request_id or current.get("state") != "running":
            return dict(current)
        current["worker_pid"] = int(worker_pid)
        snapshot = dict(current)
    if not _council_runtime_persist(agent, snapshot, request_id):
        snapshot["persist_degraded"] = True
        return snapshot
    with _COUNCIL_AGENT_RUNTIME_LOCK:
        _COUNCIL_AGENT_RUNTIME[agent] = dict(snapshot)
    return snapshot


def _council_runtime_finish(agent: str, request_id: str, ok: bool, error: str = "", model_resolution: dict | None = None) -> dict:
    with _COUNCIL_AGENT_RUNTIME_LOCK:
        current = dict(_COUNCIL_AGENT_RUNTIME.get(agent) or {})
    if current.get("request_id") == request_id:
        _council_cancel_worker(current)
    with _COUNCIL_AGENT_RUNTIME_LOCK:
        current = _COUNCIL_AGENT_RUNTIME.get(agent) or {}
        if current.get("request_id") != request_id or current.get("state") == "stopped":
            return dict(current)
        state = "completed" if ok else ("timeout" if str(error).startswith("timeout") else "error")
        if state in _COUNCIL_TERMINAL_ERROR_STATES and not str(error or "").strip():
            error = _council_worker_log_tail(agent) or f"{state}: worker ended without an explicit reason"
        current.update({"state": state, "finished_at": time.time(), "error": error or "", "worker_alive": False})
        if isinstance(model_resolution, dict) and model_resolution:
            current["model_resolution"] = dict(model_resolution)
        snapshot = dict(current)
    if not _council_runtime_persist(agent, snapshot, request_id):
        snapshot["persist_degraded"] = True
        return snapshot
    with _COUNCIL_AGENT_RUNTIME_LOCK:
        _COUNCIL_AGENT_RUNTIME[agent] = dict(snapshot)
    return snapshot


def _council_runtime_stop(agent: str, reason: str = "stopped by user") -> dict:
    with _COUNCIL_AGENT_RUNTIME_LOCK:
        current = dict(_COUNCIL_AGENT_RUNTIME.get(agent) or {})
        expected_request_id = current.get("request_id", "")
        snapshot = {
            "state": "stopped",
            "request_id": expected_request_id,
            "mode": current.get("mode", ""),
            "started_at": current.get("started_at"),
            "finished_at": time.time(),
            "error": reason or "stopped by user",
            "pid": current.get("pid", os.getpid()),
            "worker_pid": current.get("worker_pid"),
        }
    if not _council_runtime_persist(agent, snapshot, expected_request_id):
        snapshot["persist_degraded"] = True
        return snapshot
    with _COUNCIL_AGENT_RUNTIME_LOCK:
        _COUNCIL_AGENT_RUNTIME[agent] = dict(snapshot)
    return dict(snapshot)
def _council_bool_param(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _council_queue_enabled(data: dict) -> bool:
    queue_value = data.get("queue")
    if queue_value is not None and str(queue_value).strip().lower() in ("0", "false", "no", "off"):
        return False
    return not _council_bool_param(data.get("no_queue"), False)


def _council_schedule_queue_drain(agent: str) -> None:
    with _COUNCIL_AGENT_QUEUE_LOCK:
        if _COUNCIL_AGENT_QUEUE_DRAINING.get(agent):
            return
        _COUNCIL_AGENT_QUEUE_DRAINING[agent] = True
    _drain_coro = None
    _task_created = False
    try:
        asyncio.get_running_loop()
        _drain_coro = _council_queue_drain(agent)
        _t = asyncio.create_task(_drain_coro)
        _task_created = True
        _COUNCIL_PENDING_TASKS.add(_t)
        _t.add_done_callback(_COUNCIL_PENDING_TASKS.discard)
    except Exception as exc:
        # If task creation rejects the already-created coroutine, close it so
        # synchronous callers and scheduler failures do not emit an unawaited
        # coroutine warning.
        if _drain_coro is not None and not _task_created:
            _drain_coro.close()
        # CAN-023: ANY failure to schedule the drain (not just the no-event-loop
        # RuntimeError) MUST reset the draining flag, or the agent's queue jams
        # permanently (flag stuck True, no drain task ever runs). Previously this
        # only caught RuntimeError, so any other exception left DRAINING=True.
        with _COUNCIL_AGENT_QUEUE_LOCK:
            _COUNCIL_AGENT_QUEUE_DRAINING[agent] = False
        logger.error("queue drain for %s failed to schedule: %r", agent, exc)
        raise


def _council_cancel_pending_queue_item(agent: str, item: dict) -> bool:
    with _COUNCIL_AGENT_QUEUE_LOCK:
        if item.get("started"):
            return False
        queue = _COUNCIL_AGENT_QUEUES.get(agent) or deque()
        try:
            queue.remove(item)
            return True
        except ValueError:
            return False


def _council_output_contract_enabled() -> bool:
    return os.environ.get("COUNCIL_OUTPUT_CONTRACT", "1").strip() != "0"
def _council_codo_skill_path_contract() -> str:
    """Pin CodoC to canonical installed skill folders instead of inferred slugs."""
    root = Path.home() / ".agents" / "skills"
    entries = (
        ("write-pipeline", root / "05_write_pipeline" / "SKILL.md"),
        ("testing-validation", root / "09_testing_validation" / "SKILL.md"),
    )
    return "\n".join(f"- {name}: {path}" for name, path in entries if path.is_file())


def _council_assign_artifact_path(target: str) -> str:
    """Pre-assign a unique worker_output artifact path for the OUTPUT CONTRACT so
    the worker writes its full report there via [A] write_new_file instead of
    pasting it (and the prompt echo) back into chat."""
    _art_dir = _HERE / ".council" / "worker_output"
    try:
        _art_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(target))[:40] or "worker"
    return str(_art_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe}_{uuid.uuid4().hex[:8]}.md")


_COUNCIL_NO_CONTRACT_SENTINEL = "[[NO_OUTPUT_CONTRACT]]"


def _council_build_dispatch_prompt(target: str, sender: str, text: str, project_path: str, style: str) -> str:
    raw = str(text or "")
    skip_contract = _COUNCIL_NO_CONTRACT_SENTINEL in raw
    raw = raw.replace(_COUNCIL_NO_CONTRACT_SENTINEL, "")
    prompt_text = raw.strip().replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\n")
    base = f"BROKER TASK: {prompt_text}\nOutput only the answer to BROKER TASK.\n"
    if _council_output_contract_enabled() and not skip_contract:
        art = _council_assign_artifact_path(target)
        if target == "codo":
            skill_paths = _council_codo_skill_path_contract()
            if skill_paths:
                base += (
                    "\n=== CODOC SKILL PATH CONTRACT ===\n"
                    "Read these exact installed paths when the named skill applies; "
                    "do not infer directories from YAML skill names:\n"
                    f"{skill_paths}\n"
                )
        base += (
            "\n=== OUTPUT CONTRACT (HARD) ===\n"
            "Do NOT echo, quote, or restate this prompt. Do NOT paste long output into chat.\n"
            "Write your FULL report/answer to this EXACT file path using the [A] "
            "write_new_file tool (create it):\n"
            f"{art}\n"
            "If you cannot write outside your cwd, bootstrap [A] and use [A] write tools.\n"
            "The task is not successful unless requested source edits are present and this "
            "artifact exists and is non-empty. If either is missing, report "
            "error_name=COUNCIL_OUTPUT_INCOMPLETE; never report success.\n"
            "Then reply in chat with ONLY: 'DONE " + art + "' followed by a <=3 line TLDR. "
            "Nothing else. If the whole answer is trivially short (<=10 lines), you may "
            "reply inline concisely WITHOUT echoing this prompt and skip the file.\n"
        )
    return base


_COUNCIL_STREAM_EVENT_TYPES = (
    "step_start", "step_finish", "step-start", "step-finish",
    "tool_use", "tool_result", "reasoning",
)


def _council_drop_stream_noise_reply(text: str) -> str:
    """Drop a reply that is nothing but a raw stream-event JSON line.

    When an opencode/worker run is cut before emitting a final type=="text" part,
    the reply extractor can fall back to the raw tail and surface a lone
    {"type":"step_start",...} object as the "answer" (seen in the broker UI). That
    is never a real reply. Only fires on a SINGLE-line JSON object whose "type" is a
    known stream event — legitimate plain-text answers are untouched. Returns "" so
    the caller can show a clean marker instead of stream garbage.
    """
    s = (text or "").strip()
    if not (s.startswith("{") and s.endswith("}") and "\n" not in s):
        return text
    try:
        obj = json.loads(s)
    except Exception:
        return text
    if isinstance(obj, dict) and str(obj.get("type", "")).strip().lower() in _COUNCIL_STREAM_EVENT_TYPES:
        return ""
    return text


def _council_broker_visible_reply(text: str) -> str:
    """Return only broker-visible final text; keep raw/reasoning in debug fields."""
    s = _council_sanitize_reply(str(text or "").strip())
    if not s:
        return ""

    marker_pos = -1
    marker_len = 0
    for marker in ("[clean response]", "[broker response]"):
        pos = s.lower().rfind(marker)
        if pos > marker_pos:
            marker_pos = pos
            marker_len = len(marker)
    if marker_pos >= 0:
        tail = s[marker_pos + marker_len:].strip()
        if tail:
            return _council_reply_tail(tail)

    denoised = _council_drop_stream_noise_reply(s)
    if denoised != s and not denoised:
        return ""

    raw_needles = (
        '"type":"step_start"', '"type": "step_start"',
        '"type":"step_finish"', '"type": "step_finish"',
        '"type":"tool_use"', '"type": "tool_use"',
        '"type":"tool_result"', '"type": "tool_result"',
        '"type":"reasoning"', '"type": "reasoning"',
        '"sessionID"', '"messageID"', '"part":',
    )
    if any(n in s for n in raw_needles):
        extracted = _council_extract_log_response(s)
        if extracted and extracted.strip() != s.strip():
            return _council_reply_tail(extracted)
        return ""

    return _council_reply_tail(denoised)


def _council_strip_prompt_echo(reply: str, prompt: str) -> str:
    """F037 (2026-07-11): some provider CLIs replay the dispatched prompt at
    the top of stdout, so the broker reply looked like a prompt echo with the
    real findings pushed past the truncation window. If the reply starts with
    a long prefix of the prompt (>=120 chars), drop the echoed block."""
    r = (reply or "").lstrip()
    p = (prompt or "").strip()
    if len(p) < 120 or not r.startswith(p[:120]):
        return reply
    n = 0
    limit = min(len(r), len(p))
    while n < limit and r[n] == p[n]:
        n += 1
    tail = r[n:].lstrip()
    return tail if tail else reply


def _council_subtract_echo(raw: str, sent_prompt: str) -> tuple[str, str]:
    '''Remove provider prompt replay once, before any CLI-specific parsing.'''
    text = _strip_ansi(str(raw or '')).replace('\r\n', '\n').replace('\r', '\n')
    prompt = _strip_ansi(str(sent_prompt or '')).replace('\r\n', '\n').replace('\r', '\n').strip()
    if not prompt: return text, ''
    stripped = text.lstrip()
    if stripped.startswith(prompt):
        prefix_len = len(text) - len(stripped)
        removed = text[:prefix_len] + stripped[:len(prompt)]
        return stripped[len(prompt):].lstrip('\n'), removed
    prompt_lines = {line.strip() for line in prompt.splitlines() if len(line.strip()) >= 12}
    kept, removed = [], []
    for line in text.splitlines():
        (removed if line.strip() in prompt_lines else kept).append(line)
    return '\n'.join(kept), '\n'.join(removed)


def _council_parse_reply(raw: str, sent_prompt: str, adapter: str = 'generic') -> tuple[str, str, str]:
    '''Authoritative worker-reply contract: reply, reasoning, transport noise.'''
    clean_raw, echo_noise = _council_subtract_echo(raw, sent_prompt)
    adapter = str(adapter or 'generic').strip().lower()
    reasoning = ''
    if adapter == 'claude': reply, reasoning = _council_extract_claude_streamjson(clean_raw)
    elif adapter == 'hermes': reply, reasoning = _council_parse_hermes_chat(clean_raw)
    elif adapter in ('codex', 'codo'):
        # V15 F3 (D2): codex exec has two wire formats. `codex exec --json`
        # emits JSONL events (final answer in item.completed/agent_message) →
        # reuse the generic log extractor. Plain `codex exec` emits the
        # human-readable stream (exec/codex/tokens used markers) → D2 noise
        # filter. Either way an empty result is a deliberate 'no trustworthy
        # final message' signal for F2 — never backfill from raw transcript.
        if _council_looks_like_codex_jsonl(clean_raw):
            reply = _council_extract_log_response(clean_raw)
        else:
            reply = _council_extract_codex_exec(clean_raw)
    else:
        reply = _council_extract_log_response(clean_raw) or _council_reply_tail(clean_raw)
    reply = _council_drop_stream_noise_reply(_council_strip_prompt_echo(reply, sent_prompt)).strip()
    return reply, str(reasoning or '').strip(), echo_noise.strip()


def _council_apply_incomplete_fallback(reply: str, file_clean: str, file_reply: str,
                                       returncode, output_path, raw: str) -> str:
    """V15 F2 (D1) — structured incomplete signal for file-channel workers.

    Applies only when the worker was expected to write a final-message file
    (codex --output-last-message → output_path set). When the worker died
    before a trustworthy final message (returncode != 0 OR file missing/empty)
    AND no clean file content was captured, replace the transcript-derived
    reply with a structured marker: D1 forbids promoting transcript text to a
    final answer in every missing-file_reply case. Happy path (clean file
    content present) returns `reply` unchanged.
    """
    if not output_path:
        return reply
    if not (returncode != 0 or not file_reply):
        return reply
    if file_clean:
        return reply
    tail = _council_extract_codex_exec(raw)[-200:].strip()
    head = (f"COUNCIL_WORKER_INCOMPLETE: exit={returncode}; no final message captured; "
            "hint: retry with smaller context (verbosity terse, fewer tools)")
    return f"{head}\ntail_excerpt: {tail}"


def _council_build_dispatch_reply(target: str, sender: str, request_event: dict, result: dict, mode: str) -> dict:
    raw_reply = _council_strip_prompt_echo(
        result.get("reply") or "", request_event.get("text") or "")
    reply_text = _council_broker_visible_reply(raw_reply) or "[no clean response]"
    # A-1 (2026-07-12): long worker output goes to disk; the reply carries a
    # bounded preview + pointer so the leader never ingests the full artifact
    # through chat context.
    full_output_path = ""
    if len(raw_reply) > 2000:
        try:
            _art_dir = _HERE / ".council" / "worker_output"
            _art_dir.mkdir(parents=True, exist_ok=True)
            _safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{target}_{request_event.get('id', '')}")[:80]
            _art = _art_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{_safe}.txt"
            _art.write_text(raw_reply, encoding="utf-8", errors="replace")
            full_output_path = str(_art)
        except Exception:
            full_output_path = ""
    # Task 2 (2026-07-12): bound the broker-visible reply so a verbose/non-compliant
    # worker never floods the leader's chat context. Full text is on disk (A-1) or in
    # the OUTPUT CONTRACT artifact the worker was told to write.
    _reply_cap = 1500
    if _council_output_contract_enabled() and len(reply_text) > _reply_cap:
        reply_text = reply_text[:_reply_cap].rstrip() + " …[reply truncated by broker]"
    if full_output_path:
        reply_text = f"{reply_text}\n[full_output: {full_output_path}]"
    reply_event = _council_append_event({
        "type": "reply",
        "from": target,
        "to": sender[:40],
        "in_reply_to": request_event["id"],
        "ok": bool(result.get("ok")),
        "returncode": result.get("returncode"),
        "mode": result.get("mode", mode),
        "provider": result.get("provider", ""),
        "model": result.get("model", ""),
        "model_resolution": result.get("model_resolution", {}),
        "error_name": result.get("error_name", ""),
        "session_id": result.get("session_id", ""),
        "context_turns": result.get("context_turns", 0),
        "text": reply_text[:_COUNCIL_EVENT_TEXT_MAX],
        "error": result.get("error", ""),
        "response_tokens": result.get("response_tokens") or _council_extract_response_tokens(
            target, (result.get("reasoning") or "") + "\n" + (result.get("raw_preview") or "")
        ),
        "raw": _council_clean_broker_raw(target, (result.get("reasoning") or result.get("raw_preview") or ""))[:3000],
    })
    return {
        "ok": bool(result.get("ok")), "request": request_event, "reply_event": reply_event,
        "reply_text": reply_text, "mode": result.get("mode", mode),
        "provider": result.get("provider", ""), "model": result.get("model", ""),
        "model_resolution": result.get("model_resolution", {}),
        "error_name": result.get("error_name", ""),
        "session_id": result.get("session_id", ""), "context_turns": result.get("context_turns", 0),
        "raw_preview": result.get("raw_preview", "")[:1200], "error": result.get("error", ""),
        "full_output_path": full_output_path,
    }


def _council_usage_append(item: dict, result: dict) -> None:
    """A-5 (2026-07-12): per-worker usage ledger — one JSONL line per finished
    council run so token/time spend per runtime_key is visible without parsing
    broker logs. Fail-open: a ledger write error never breaks the dispatch."""
    try:
        rec = {
            "ts": time.time(),
            "runtime_key": item.get("runtime_key", ""),
            "target": item.get("target", ""),
            "provider": item.get("provider", ""),
            "model": item.get("model", ""),
            "ok": bool(result.get("ok")),
            "error": str(result.get("error") or "")[:120],
            "timeout_kind": str(result.get("timeout_kind") or ""),
            "duration_sec": round(time.time() - float(item.get("created_at") or time.time()), 1),
            "response_tokens": result.get("response_tokens") or "",
            "request_id": item.get("request_id", ""),
            "task_id": (item.get("measurement_metadata") or {}).get("task_id", "unknown"),
            "objective_class": (item.get("measurement_metadata") or {}).get("objective_class", "unknown"),
            "risk_level": (item.get("measurement_metadata") or {}).get("risk_level", "unknown"),
            "dev_level": (item.get("measurement_metadata") or {}).get("dev_level", "unknown"),
            "prompt_bytes": (item.get("measurement_metadata") or {}).get("prompt_bytes", 0),
        }
        # Was a plain open(..., "a"). Safe today only because the council drain
        # that calls this is serialised per agent by _COUNCIL_AGENT_QUEUE_DRAINING
        # -- nothing in this function says so, and the failure mode if a second
        # writer arrives is silent: probed at 4 processes x 4 threads x 60 appends,
        # a plain append kept 280 of 480 lines and overwrote one line's leading
        # brace, with zero exceptions and exit code 0. _jsonl_append_locked takes a
        # sidecar lock (so a concurrent read_tail is not denied) and fsyncs outside
        # the critical section.
        _jsonl_append_locked(_HERE / ".council" / "usage_log.jsonl", rec)
    except Exception as exc:
        # Still fail-open: a ledger write must never break a dispatch. But it is
        # LOGGED now. The old bare `pass` made a lost usage line indistinguishable
        # from a run that never happened, which is the blind spot that let an
        # unlocked append survive here unnoticed.
        logger.warning("council usage ledger append failed: %s", exc)


def _council_observed_p50_sec(runtime_key: str):
    """Median completed duration for this worker slot, or None without samples."""
    samples = []
    path = _HERE / ".council" / "usage_log.jsonl"
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
            row = json.loads(line)
            if row.get("runtime_key") == runtime_key and row.get("duration_sec") is not None:
                samples.append(float(row["duration_sec"]))
    except Exception:
        return None
    if not samples:
        return None
    samples.sort()
    middle = len(samples) // 2
    median = samples[middle] if len(samples) % 2 else (samples[middle - 1] + samples[middle]) / 2.0
    return round(median, 3)


def _council_queue_timeout_payload(runtime_key: str) -> dict:
    return {
        "observed_p50_sec": _council_observed_p50_sec(runtime_key),
        "worker_state": _council_runtime_status(runtime_key),
        "timeout_semantics": "caller_wait_only",
        "worker_continues_after_timeout": True,
        "cancel_hint": "POST /api/cancel with the request operation_id to cancel the caller task",
    }


def _council_capture_to_q(item: dict, result: dict) -> None:
    """Combo-2 (2026-07-12): close the dispatch→capture loop — after a worker
    finishes OK, POST a compact record to [Q] /capture (writer=A, dedup by
    content_hash on the Q side). Fail-open on a detached daemon thread with a
    15s budget (Q's writer is often busy >10s during distill jobs; the thread
    blocks nothing) so a down/slow [Q] can never stall the council drain."""
    if not result.get("ok"):
        return

    def _post():
        try:
            reply = str(result.get("reply") or "")[:4000]
            if not reply.strip():
                return
            body = json.dumps({
                "writer": "A",
                "session_id": f"council-{time.strftime('%Y%m%d')}",
                "project": "council",
                "turns": [{
                    "user_prompt": f"[council dispatch {item.get('runtime_key', '')}] "
                                   + str(item.get("text") or "")[:1500],
                    "assistant_response": reply,
                }],
            }).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:8082/capture", data=body,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).read(200)
        except Exception:
            pass

    threading.Thread(target=_post, daemon=True, name="council-q-capture").start()


async def _council_queue_drain(agent: str) -> None:
    try:
        while True:
            with _COUNCIL_AGENT_QUEUE_LOCK:
                queue = _COUNCIL_AGENT_QUEUES.get(agent)
                item = queue.popleft() if queue else None
            if not item:
                return
            runtime = _council_runtime_start(
                item["runtime_key"], item["request_id"], item["mode"],
                item["target"], item.get("provider", ""), item.get("model", ""),
                item.get("artifact_path", ""), item.get("model_resolution", {}),
            )
            if runtime is None:
                with _COUNCIL_AGENT_QUEUE_LOCK:
                    _COUNCIL_AGENT_QUEUES.setdefault(agent, deque()).appendleft(item)
                await asyncio.sleep(0.2)
                continue
            if runtime is False:
                future = item.get("future")
                if future and not future.done():
                    future.set_result({
                        "ok": False,
                        "error_name": "COUNCIL_RUNTIME_PERSIST_FAILED",
                        "error": "council runtime reservation could not be persisted",
                    })
                continue
            item["started"] = True
            item["started_at"] = time.time()
            with _COUNCIL_AGENT_QUEUE_LOCK:
                _COUNCIL_INFLIGHT_TEXT[item["runtime_key"]] = hashlib.sha256(
                    item["text"].encode("utf-8")).hexdigest()
            result = {"ok": False, "returncode": None, "reply": "", "error": "", "raw_preview": ""}
            try:
                request_event = _council_append_event({
                    "type": "message",
                    "from": item["sender"][:40],
                    "to": item["target"],
                    "mode": item["mode"],
                    "project_path": item["project_path"],
                    "provider": item.get("provider", ""),
                    "model": item.get("model", ""),
                    "model_resolution": item.get("model_resolution", {}),
                    "request_id": item["request_id"],
                    "task_metadata": item.get("measurement_metadata", {}),
                    "load": {k: item.get("measurement_metadata", {}).get(k, "unknown") for k in (
                        "independent_questions", "files_subsystems", "expected_tool_calls", "expected_writes", "dependency_depth", "prompt_bytes"
                    )},
                    "text": item["text"][:_COUNCIL_EVENT_TEXT_MAX],
                })
                prompt = _council_build_dispatch_prompt(
                    item["target"], item["sender"], item["text"], item["project_path"], item["prompt_style"]
                )
                result = await _council_run_blocking(
                    _council_run_agent, item["target"], prompt, item["timeout_sec"], item["mode"], item["project_path"],
                    item["request_id"], item["runtime_key"], item.get("provider", ""), item.get("model", ""),
                    item.get("model_resolution", {}),
                    item.get("measurement_metadata", {}).get("risk_level", "unknown"),
                    item.get("measurement_metadata", {}).get("dev_level", "unknown"),
                )
                payload = _council_build_dispatch_reply(item["target"], item["sender"], request_event, result, item["mode"])
            except Exception as exc:
                result = {"ok": False, "returncode": None, "reply": "", "error": str(exc), "raw_preview": ""}
                payload = {"ok": False, "error_name": "AGENT_ERROR", "error": str(exc)}
            finally:
                with _COUNCIL_AGENT_QUEUE_LOCK:
                    _COUNCIL_INFLIGHT_TEXT.pop(item["runtime_key"], None)
                _finish_err = str(result.get("error") or "")
                # Forensics (2026-07-12): keep WHICH timer fired (idle / hard_cap /
                # no_output) in the persisted runtime state, not just "timeout".
                if _finish_err == "timeout" and result.get("timeout_kind"):
                    _finish_err = f"timeout:{result.get('timeout_kind')}"
                _council_runtime_finish(
                    item["runtime_key"], item["request_id"], bool(result.get("ok")), _finish_err,
                    model_resolution=result.get("model_resolution") or item.get("model_resolution"))
                _council_usage_append(item, result)
                try:
                    _reply_event_id = str((payload.get("reply_event") or {}).get("id") or "") if isinstance(payload, dict) else ""
                    _measurement = _route_build_measurement(item, result, reply_event_id=_reply_event_id)
                    _route_append_measurement(_HERE / ".council" / "route_measurements.jsonl", _measurement)
                except Exception as _measurement_exc:
                    logger.warning("council route measurement failed: %s", _measurement_exc)
                _council_capture_to_q(item, result)
            future = item.get("future")
            if future and not future.done():
                future.set_result(payload)
    finally:
        with _COUNCIL_AGENT_QUEUE_LOCK:
            _COUNCIL_AGENT_QUEUE_DRAINING[agent] = False
            has_more = bool(_COUNCIL_AGENT_QUEUES.get(agent))
        if has_more:
            _council_schedule_queue_drain(agent)


# ── Combo-3 (2026-07-12): QNight auto-pause during council dispatch ──────────
# Council work (interactive, user-priority) beats QNight background scavenging.
# On every real dispatch [A] signal-pauses QNight (no model kill — QNightJob stops
# at the next cluster boundary) and resumes ONLY its own pause once no council
# work is running or queued. [Q] enforces source semantics: a_council_auto never
# overwrites a manual pause, and only_source-resume never clears one.
_QNIGHT_Q_BASE_URL = os.environ.get("QNIGHT_Q_BASE_URL", "http://127.0.0.1:8082")
_QNIGHT_AUTOPAUSE_SOURCE = "a_council_auto"


def _qnight_autopause_enabled() -> bool:
    # Test isolation (C-C lesson): neither pytest nor unittest may signal LIVE [Q]
    # or race temp-dir teardown with runtime_state.json reads in helper threads.
    explicit_test_mode = os.environ.get("ORCHESTRATOR_TEST_MODE", "").strip() == "1"
    unittest_cli = any("unittest" in str(arg).lower() for arg in sys.argv[:2])
    if os.environ.get("PYTEST_CURRENT_TEST") or explicit_test_mode or unittest_cli:
        return False
    return os.environ.get("QNIGHT_COUNCIL_AUTOPAUSE", "1").strip() != "0"


def _qnight_qjobs_post(path: str, body: dict, timeout: float = 3.0) -> dict:
    import urllib.request as _ur
    req = _ur.Request(_QNIGHT_Q_BASE_URL.rstrip("/") + path,
                      data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(_ur.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))


def _qnight_autopause() -> None:
    """Fail-open: [Q] down/slow must never block or delay a council dispatch."""
    if not _qnight_autopause_enabled():
        return
    try:
        _qnight_qjobs_post("/qjobs/pause", {"source": _QNIGHT_AUTOPAUSE_SOURCE})
    except Exception:
        pass


def _qnight_autoresume_if_idle() -> None:
    """Resume ONLY the auto-pause and ONLY when no council work runs or waits."""
    if not _qnight_autopause_enabled():
        return
    try:
        with _COUNCIL_AGENT_QUEUE_LOCK:
            if any(_COUNCIL_AGENT_QUEUES.get(k) for k in _COUNCIL_AGENT_QUEUES):
                return
        for _aid in _COUNCIL_AGENTS:
            if _council_runtime_status(_aid).get("state") == "running":
                return
        _qnight_qjobs_post("/qjobs/resume", {"only_source": _QNIGHT_AUTOPAUSE_SOURCE})
    except Exception:
        pass


def _qnight_start_background(target) -> bool:
    """Start a QNight helper only when enabled, avoiding test teardown races."""
    if not _qnight_autopause_enabled():
        return False
    threading.Thread(target=target, daemon=True).start()
    return True


def _council_sanitize_request_id(value) -> str:
    """P2 (2026-08-06): normalize a caller-supplied dispatch correlation id.
    Safe charset + bounded length; '' means 'generate one downstream'."""
    return re.sub(r"[^A-Za-z0-9_.:-]+", "", str(value or ""))[:80]


async def _council_enqueue_or_run(
    target: str, sender: str, text: str, timeout_sec: int,
    mode: str = "broker", project_path: str = "", queue: bool = True,
    queue_wait_timeout_sec: int = 6000, prompt_style: str = "tool",
    provider: str = "", model: str = "", request_id: str = "",
    fallback_policy: str = "none", allowed_fallbacks=None,
) -> dict:
    loop = asyncio.get_running_loop()
    metadata = _route_parse_dispatch_metadata(text)
    fallback_policy = str(fallback_policy or "none").strip().lower()
    if not isinstance(allowed_fallbacks, (list, tuple)):
        allowed_fallbacks = []
    resolution = _council_resolve_model(target, provider, model, fallback_policy, allowed_fallbacks,
                                        metadata.get("risk_level", "unknown"), metadata.get("dev_level", "unknown"))
    if resolution.resolution_status == "unavailable":
        return {"ok": False, "error_name": "COUNCIL_MODEL_UNAVAILABLE", "error": "requested model is unavailable",
                "agent": target, **resolution.as_dict()}
    provider, model = resolution.resolved_provider, resolution.resolved_model
    runtime_key = _council_runtime_key(target, model)
    _artifact_path = _council_extract_artifact_path(text)
    _task_key = _compute_task_key(target, text, _artifact_path) if _artifact_path else ""
    if _task_key:
        _cached = _council_completed_task_read(_task_key)
        if _cached and _cached.get("ok"):
            return {"ok": True, "dispatched": False, "cached": True, "task_key": _task_key,
                    "reply_text": _cached.get("reply_text", ""), "error": "", **resolution.as_dict(),
                    "note": "BROKER-RETRY-AFTER-SUCCESS dedup: cached completion returned; no dispatch"}
    item = {
        "request_id": _council_sanitize_request_id(request_id) or f"run-{uuid.uuid4()}",
        "sender": sender[:40], "target": target, "text": text, "mode": mode,
        "project_path": project_path, "timeout_sec": timeout_sec,
        "provider": provider, "model": model, "model_resolution": resolution.as_dict(),
        "runtime_key": runtime_key, "created_at": time.time(), "future": loop.create_future(),
        "started": False, "prompt_style": prompt_style, "artifact_path": _artifact_path or "",
        "measurement_metadata": metadata,
    }
    with _COUNCIL_AGENT_QUEUE_LOCK:
        agent_queue = _COUNCIL_AGENT_QUEUES.setdefault(runtime_key, deque())
        current = _COUNCIL_AGENT_RUNTIME.get(runtime_key) or {}
        busy = (
            bool(agent_queue)
            or _COUNCIL_AGENT_QUEUE_DRAINING.get(runtime_key)
            or (current.get("state") == "running" and not _runtime_is_stale(current))
        )
        # F044 (2026-07-11): duplicate-dispatch guard. A leader whose sync wait
        # timed out re-sends the SAME prompt while run #1 is still in flight
        # (observed queue=6 in the 07-10 scan). Only real task prompts (>=200
        # chars) dedup — short pings/messages may legitimately repeat.
        if len(text) >= 200:
            _text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            _dup_running = (_COUNCIL_INFLIGHT_TEXT.get(runtime_key) == _text_sha
                            and current.get("state") == "running"
                            and not _runtime_is_stale(current))
            _dup_queued = any(
                isinstance(q, dict) and q.get("text") == text for q in agent_queue)
            if _dup_running or _dup_queued:
                return {"ok": True, "dispatched": False, "duplicate_inflight": True,
                        "agent": target, "runtime_key": runtime_key,
                        "note": "identical prompt already " +
                                ("running" if _dup_running else "queued") +
                                " for this agent; NOT re-dispatched (F044 guard). "
                                "Read .council/broker_filtered.jsonl for the original reply."}
        if not queue and busy:
            return {"ok": False, "error_name": "AGENT_BUSY",
                    "error": f"agent {target} model {model or '(default)'} is already working",
                    "agent": target, "provider": provider, "model": model, "runtime_key": runtime_key,
                    "runtime": _council_runtime_status(runtime_key)}
        if len(agent_queue) >= _COUNCIL_MAX_QUEUE_PER_AGENT:
            return {"ok": False, "error_name": "AGENT_QUEUE_FULL", "error": f"agent {target} queue is full",
                    "max_queue_per_agent": _COUNCIL_MAX_QUEUE_PER_AGENT}
        agent_queue.append(item)
    # Council UI order (do NOT re-add a "queued" broker event here): each waiting item
    # already emits exactly ONE "message" event when the drain dispatches it (see
    # _council_queue_drain). Emitting "queued" too made every waiting prompt appear
    # twice in the broker transcript and scrambled the strict prompt->reply->prompt
    # ordering the council UI must preserve. Pending depth is surfaced via the runtime
    # status panel, not as a duplicate chat line.
    # Combo-3: QNight yields to council work (background thread — never blocks dispatch)
    _qnight_start_background(_qnight_autopause)
    _council_schedule_queue_drain(runtime_key)
    try:
        _result = await asyncio.wait_for(asyncio.shield(item["future"]), timeout=max(1, int(queue_wait_timeout_sec or 6000)))
        # BROKER-RETRY-AFTER-SUCCESS (F2): record the completion ONLY when the reply is
        # ok AND the named artifact is genuinely complete on disk (artifact = source of
        # truth). This keeps the cache from masking a needed retry when the worker
        # acked ok but never wrote a valid artifact.
        if (_task_key and isinstance(_result, dict) and _result.get("ok")
                and _artifact_path and _artifact_last_line_complete(_artifact_path)):
            _council_completed_task_write(_task_key, {
                "task_key": _task_key, "target": target, "artifact": _artifact_path,
                "ok": True, "reply_text": _result.get("reply_text", ""), "ts": time.time(),
            })
        return _result
    except asyncio.TimeoutError:
        if _council_cancel_pending_queue_item(runtime_key, item):
            return {"ok": False, "error_name": "QUEUE_WAIT_TIMEOUT", "error": f"timed out waiting for {target} queue",
                    "queue_wait_timeout_sec": queue_wait_timeout_sec,
                    **_council_queue_timeout_payload(runtime_key)}
        return {"ok": False, "error_name": "QUEUE_WAIT_TIMEOUT", "error": f"{target} started but caller timed out",
                "queue_wait_timeout_sec": queue_wait_timeout_sec, "started": True,
                **_council_queue_timeout_payload(runtime_key)}
    except asyncio.CancelledError:
        # Queue items are stored by runtime_key (agent::model), not bare target.
        # If a sync caller is cancelled while waiting, using target here leaves the
        # pending item behind and can make later long dispatches appear to vanish
        # before the broker "message" event is appended.
        _council_cancel_pending_queue_item(runtime_key, item)
        raise
    finally:
        # Combo-3: resume QNight when the council goes idle (only_source-guarded;
        # a sync-timeout with the worker still running is caught by the idle check).
        _qnight_start_background(_qnight_autoresume_if_idle)


def _council_runtime_entry_active(entry: dict) -> bool:
    """True only for a live per-dispatch worker slot, never the [A] daemon PID."""
    if not isinstance(entry, dict):
        return False
    if entry.get("state") != "running" or entry.get("finished_at") is not None:
        return False
    worker_pid = entry.get("worker_pid")
    return bool(worker_pid and _pid_alive(worker_pid))


def _council_runtime_status(agent: str) -> dict:
    default = {"state": "idle", "request_id": "", "mode": "",
               "started_at": None, "finished_at": None, "error": ""}
    with _COUNCIL_AGENT_RUNTIME_LOCK:
        mem = _COUNCIL_AGENT_RUNTIME.get(agent)
        mem = dict(mem) if mem else None
        mem_slots = [dict(v) for k, v in _COUNCIL_AGENT_RUNTIME.items()
                     if str(k).startswith(f"{agent}::") and isinstance(v, dict)]
    file_entry, file_slots = None, []
    try:
        f = _council_runtime_file()
        if f.exists():
            runtime_data = json.loads(f.read_text(encoding="utf-8")) or {}
            fe = runtime_data.get(agent)
            file_entry = fe if isinstance(fe, dict) else None
            file_slots = [dict(v) for k, v in runtime_data.items()
                          if str(k).startswith(f"{agent}::") and isinstance(v, dict)]
    except Exception:
        pass
    def _ts(e):
        return max((e.get("finished_at") or 0), (e.get("started_at") or 0)) if e else -1
    candidates = [c for c in ([mem, file_entry] + mem_slots + file_slots) if c]
    active = [c for c in candidates if _council_runtime_entry_active(c)]
    if active:
        best = dict(max(active, key=_ts))
        best["active_slots"] = [{"runtime_key": c.get("runtime_key", ""),
            "request_id": c.get("request_id", ""), "provider": c.get("provider", ""),
            "model": c.get("model", ""), "worker_pid": c.get("worker_pid"),
            "started_at": c.get("started_at")} for c in active]
        best["worker_alive"] = True
        return best
    terminal = [c for c in candidates if c.get("state") != "running"]
    if terminal:
        return dict(max(terminal, key=_ts))
    out = dict(default)
    if candidates:
        out["error"] = "orphaned runtime slot (worker process not alive)"
    return out
_COUNCIL_METRICS_CACHE: dict[str, tuple[float, dict]] = {}
_COUNCIL_METRICS_TTL_SECONDS = 60.0
_COUNCIL_CODO_METRICS_TTL_SECONDS = 6.0
_COUNCIL_EVENT_LOG = "council_events.jsonl"
_COUNCIL_SESSION_META = "session_meta.json"
_COUNCIL_BROKER_SYSTEM = (
    "Council broker mode. You are a worker agent receiving one direct task. "
    "Your council identity is the broker-injected worker label (for example KiloC, OpeC, codoC), "
    "not any desktop CA identity that may appear in a shared project rules file. "
    "Always use that worker label, never another agent's name. Do not ask what the task is. "
    "Answer the TASK directly, briefly, in Vietnamese with diacritics. "
    "Do not create prompt_*.md or outbox_*.md for this broker message."
)


def _council_read_text(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding='utf-8', errors='replace').strip()
    except Exception as exc:
        return f"[read error: {exc}]"
    return "[missing]"


def _council_latest_block(text: str, kind: str) -> str:
    pattern = re.compile(
        rf"<<{kind}\b[^>]*>>.*?(?:<<END\s+[^>]+>>|<</{kind}>>)",
        re.DOTALL,
    )
    matches = pattern.findall(text or "")
    return matches[-1].strip() if matches else f"[no {kind} block]"


_COUNCIL_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_COUNCIL_VI_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.IGNORECASE,
)


def _council_file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except Exception:
        return 0.0


def _strip_ansi(text: str) -> str:
    return _COUNCIL_ANSI_RE.sub("", text or "")


def _council_newest_agent_log(council_dir: Path, agent: str) -> "Path | None":
    """Newest log matching log_{agent}*.txt by mtime; fallback canonical log_{agent}.txt.

    Worker runs increasingly write per-run logs (e.g. log_ope_vi_sentence_test_2.txt)
    rather than the canonical log_{agent}.txt, so the live fallback must follow the
    newest matching file, not the hardcoded canonical name. Companion *.err.txt logs
    are deprioritised so they don't shadow real stdout output.
    """
    try:
        candidates = [p for p in council_dir.glob(f"log_{agent}*.txt") if p.is_file()]
    except Exception:
        candidates = []
    primary = [p for p in candidates if not p.name.endswith(".err.txt")]
    pool = primary or candidates
    newest: "tuple[float, Path] | None" = None
    for path in pool[:80]:
        mtime = _council_file_mtime(path)
        if newest is None or mtime > newest[0]:
            newest = (mtime, path)
    if newest is not None:
        return newest[1]
    canonical = council_dir / f"log_{agent}.txt"
    return canonical if canonical.exists() else None


def _council_newest_prompt_mtime(council_dir: Path, agent: str) -> float:
    newest = 0.0
    patterns = (
        f"prompt_{agent}_*.md",
        f"prompt_{agent}_*.txt",
        f"run_{agent}_*.bat",
        f"run_{agent}_*.cmd",
        f"run_{agent}_*.ps1",
    )
    for pattern in patterns:
        try:
            paths = [p for p in council_dir.glob(pattern) if p.is_file()]
        except Exception:
            paths = []
        for path in paths[:80]:
            newest = max(newest, _council_file_mtime(path))
    return newest


def _council_decode_log_bytes(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace")
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _council_extract_log_response(log_text: str) -> str:
    """Best-effort extract of the agent's final reply from a raw (forward) log.

    Handles two formats:
      * opencode JSONL: one JSON object per line; answer text may arrive across
        multiple type=="text" objects.
      * cline/plain log: ANSI-coloured stream with [thinking]/tool noise; the final
        answer is the meaningful block after the last thinking/tool marker.
    Output capped at 2000 chars. Expects forward-order text (NOT the reversed tail).
    """
    if not log_text:
        return ""
    if isinstance(log_text, bytes):
        log_text = log_text.decode("utf-8", errors="replace")
    lines = [ln for ln in log_text.splitlines() if ln.strip()]

    # opencode JSONL path.
    text_parts: list[str] = []
    json_seen = False
    for ln in lines:
        s = ln.strip()
        if len(s) > 1_000_000:
            # Pathological single-line JSON (e.g. inline base64 image) — parsing
            # it risks OOM and it is never the worker's final text answer.
            continue
        if not (s.startswith("{") and s.endswith("}")):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        json_seen = True
        if obj.get("type") == "text":
            part = obj.get("part") or {}
            txt = part.get("text")
            if isinstance(txt, str) and txt.strip():
                text_parts.append(txt.strip())
        elif obj.get("type") == "item.completed":
            item = obj.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                if item["text"].strip():
                    text_parts.append(item["text"].strip())
    if json_seen:
        return _council_reply_tail("\n".join(text_parts))

    # cline/plain path.
    clean = _strip_ansi(log_text)
    clean_lines = clean.splitlines()
    noise_prefixes = (
        "[thinking]", "[run_commands]", "[run_command]", "[command]",
        "[tool", "[exec]", "[run]", "[debug]", "[info]",
        "thinking:", "let me ", "since this ", "actually,", "exit_code=",
        # Reasoning-model (deepseek-reasoner / clinC) chain-of-thought that leaks into
        # the reply as plain prose — strip these leading-reasoning lines.
        "wait,", "wait ", "okay,", "okay ", "hmm", "the user", "this seems",
        "this is a", "looking ", "i should", "i need", "i'll ", "i will ",
        "but there", "but the", "so the", "so i", "let me just", "first,",
        "the message", "the task", "i'm a", "i am a", "re-read", "let me re",
    )
    thinking_indexes = [
        idx for idx, line in enumerate(clean_lines)
        if line.strip().lower().startswith(("thinking:", "[thinking]"))
    ]
    if thinking_indexes:
        tail_candidates: list[str] = []
        # Same reasoning/tool-noise strip as the no-thinking path, plus stream
        # markers and tool-result bullets ("- Modified: ...") that are not answers.
        tail_noise = noise_prefixes + (
            "[read_", "[run_", "⎿", "→", "⚙",
            "- modified:", "- created:", "- deleted:", "- read:",
        )
        for raw_line in clean_lines[thinking_indexes[-1] + 1:]:
            line = raw_line.strip()
            low = line.lower()
            if not line:
                continue
            if any(low.startswith(prefix) for prefix in tail_noise):
                continue
            tail_candidates.append(line)
        if tail_candidates:
            return _council_reply_tail("\n".join(tail_candidates))
        return ""

    candidates: list[str] = []
    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()
        if any(low.startswith(p) for p in noise_prefixes):
            continue
        candidates.append(line)
    if not candidates:
        return ""
    return _council_reply_tail("\n".join(candidates))


# V15 F3 (D2) — codex exec transcript noise filter. Full-line regexes with word
# boundaries so legit user content (e.g. "executive summary", "codebase") is
# never swallowed. Digit-only lines cover the "tokens used\n12,345" trailer.
# F3-1 (2026-08-07): the exec/codex transport markers are BARE lines — a line
# holding exactly "exec" or "codex" (case-insensitive). Sentences that merely
# START with those words ("codex apps are useful", "exec plan is valid
# content") are real content and must survive; the old `^\s*(?:exec|codex)\b`
# prefix regex swallowed them and is removed. Every remaining pattern stays
# full-line anchored ($ … $) so no real sentence can match.
_CODEX_EXEC_BARE_MARKERS = frozenset({"exec", "codex"})
_CODEX_EXEC_NOISE_RES = tuple(re.compile(p) for p in (
    r"^\s*succeeded in \d+ms:\s*$",
    r"^\s*tokens used\s*$",
    r"^\s*[\d,\s]*$",
))
_CODEX_EXEC_ERROR_JSON_RE = re.compile(r"^\s*ERROR:\s*(\{.*)$", re.DOTALL)
# `codex exec --json` JSONL lifecycle events (distinct from the plain stream).
_CODEX_JSONL_EVENT_TYPES = frozenset({
    "thread.started", "turn.started", "turn.completed", "turn.failed",
    "item.started", "item.updated", "item.completed", "error",
})


def _council_looks_like_codex_jsonl(text: str) -> bool:
    """True when the transcript carries `codex exec --json` JSONL event lines
    (a parseable single-line object whose "type" is a known codex event)."""
    if not text:
        return False
    for ln in str(text).splitlines():
        s = ln.strip()
        if len(s) > 1_000_000 or not (s.startswith("{") and s.endswith("}")):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict) and str(obj.get("type", "")).strip().lower() in _CODEX_JSONL_EVENT_TYPES:
            return True
    return False


def _council_codex_line_is_noise(line: str) -> bool:
    """D2: True when the whole stripped line is codex-exec transport noise."""
    # F3-1: bare exec/codex section markers only (case-insensitive, whole
    # line) — never prefix-match a sentence that starts with those words.
    if str(line).strip().lower() in _CODEX_EXEC_BARE_MARKERS:
        return True
    return any(rx.match(line) for rx in _CODEX_EXEC_NOISE_RES)


def _council_extract_codex_exec(text: str) -> str:
    """Extract the meaningful answer from a `codex exec` transcript (V15 F3 / D2).

    Noise: exec/codex section markers, "succeeded in Nms:", "ERROR: {…}" dumps,
    "tokens used", digit-only lines, and parseable JSON blocks (single- or
    multi-line, lines starting with '{'/'['). Returns the remaining non-noise
    lines joined (tail-capped), or "" when nothing meaningful remains —
    callers treat "" as 'no trustworthy final message' (F2 signal).
    Never fabricates content.
    """
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    lines = _strip_ansi(str(text)).splitlines()
    kept: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        force_noise_line = False
        err_m = _CODEX_EXEC_ERROR_JSON_RE.match(s)
        if err_m:
            # Gateway error dump: the '{' opens a JSON block on the same line;
            # fall through to the JSON-block skipper with the body only.
            s = err_m.group(1)
            force_noise_line = True
        elif _council_codex_line_is_noise(s):
            i += 1
            continue
        if s[0] in "{[" and len(s) <= 1_000_000:
            buf = s
            parsed_upto = -1
            for j in range(i, min(n, i + 200)):
                if j > i:
                    nxt = lines[j].strip()
                    if len(buf) + len(nxt) > 1_000_000:
                        break
                    buf += "\n" + nxt
                try:
                    json.loads(buf)
                    parsed_upto = j
                    break
                except Exception:
                    continue
            if parsed_upto >= i:
                i = parsed_upto + 1
                continue
            if force_noise_line:
                # Unparseable/truncated ERROR dump — the ERROR line itself is
                # still noise even when its body could not be balanced.
                i += 1
                continue
        elif force_noise_line:
            i += 1
            continue
        kept.append(s)
        i += 1
    return _council_reply_tail("\n".join(kept)).strip()


def _council_clean_direct_log_text(agent: str, log_text: str, lines: int = 40) -> str:
    """Return UI-safe direct log text.

    Raw worker logs are still kept on disk. The dashboard should show the final
    response first and avoid opencode JSONL/ANSI thinking streams by default.
    """
    if not log_text:
        return ""
    extracted = _council_extract_log_response(log_text)
    if extracted:
        return _council_prefixed_reply_tail("[clean response]\n", extracted)

    clean = _strip_ansi(log_text)
    if any(line.strip().lower().startswith(("thinking:", "[thinking]")) for line in clean.splitlines()):
        return ""
    rows: list[str] = []
    noise_needles = (
        '"type":"step_start"', '"type":"step_finish"', '"type":"tool_use"',
        '"type":"reasoning"', '"tokens"', '"sessionID"', "[thinking]",
        "thinking:", "let me ", "since this ", "actually,", "exit_code=",
        "OpenAI Codex", "workdir:", "model:", "provider:", "sandbox:",
        "approval:", "session id:", "token usage:", "WARN ",
    )
    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()
        if line.startswith("{") and line.endswith("}"):
            continue
        if any(n.lower() in low for n in noise_needles):
            continue
        rows.append(line)
    return _council_reply_tail("\n".join(rows[-max(1, min(120, int(lines))):]))


def _council_clean_broker_raw(agent: str, raw_text: str) -> str:
    if not raw_text:
        return ""
    cleaned = _council_clean_direct_log_text(agent, raw_text, 40)
    return cleaned or _council_reply_tail(_strip_ansi(raw_text))


def _council_clean_timeout(raw: str) -> str:
    """On a killed/timed-out agent, avoid surfacing mid-stream garbage.

    A partial run can leave raw opencode JSONL, an echoed broker prompt, or
    half-finished thinking. Return a clean marker unless a genuine plain reply
    survived.
    """
    return "[timeout: no final reply]"


_COUNCIL_GREETING_RE = re.compile(r"^\s*ch[aà]o\s+[^\n,]{0,32},\s*", re.IGNORECASE)
_COUNCIL_MARKER_LEAD_RE = re.compile(
    r"^\s*(?:🔍|✅|❓|⚙️|🛠️|EXC)\s*(?:ANALYZE|VERIFY|ASK|EXECUTE|EXC\s+EXECUTE)\b[^\n]*\n?",
    re.IGNORECASE,
)
_COUNCIL_MARKER_TAIL_RE = re.compile(
    r"\n?\s*(?:🔍|✅|❓|⚙️|🛠️)\s*(?:ANALYZE|VERIFY|ASK|EXC\s+EXECUTE|EXECUTE)\s*$",
    re.IGNORECASE,
)


def _council_sanitize_reply(text: str) -> str:
    """Strip leaked greeting lines and Clau status markers from a broker reply.

    Worker models occasionally prepend "Chào <agent>C," or emit the Clau
    response-contract marker (🔍 ANALYZE / ✅ VERIFY / ❓ ASK / EXC EXECUTE)
    despite the OUTPUT_CONTRACT. We remove that noise but never return empty:
    if stripping would empty the reply, the stripped original is kept.
    """
    if not text:
        return text
    original = text.strip()
    s = original
    s = _COUNCIL_GREETING_RE.sub("", s, count=1).strip()
    for _ in range(3):
        new = _COUNCIL_MARKER_LEAD_RE.sub("", s, count=1).strip()
        if new == s:
            break
        s = new
    s = _COUNCIL_MARKER_TAIL_RE.sub("", s).strip()
    return s or original


def _council_resolve_latest_res(council_dir: Path, agent: str, outbox_text: str) -> str:
    """Pick the freshest response for the UI: outbox RES vs newest-log live response.

    Rules:
      * Latest prompt newer than outbox AND newest log has an extractable reply -> "[live response]".
      * Outbox RES missing/empty (e.g. [no RES block] or <<RES>><</RES>>) -> live reply.
      * Otherwise (outbox RES newer-or-equal and non-empty) -> keep outbox RES.
    """
    latest_res = _council_latest_block(outbox_text, "RES")
    log_path = _council_newest_agent_log(council_dir, agent)
    log_mtime = _council_file_mtime(log_path) if log_path else 0.0
    outbox_mtime = _council_file_mtime(council_dir / f"outbox_{agent}.md")
    prompt_mtime = _council_newest_prompt_mtime(council_dir, agent)

    extracted = ""
    if log_path is not None:
        try:
            extracted = _council_extract_log_response(
                _council_decode_log_bytes(log_path.read_bytes())
            )
        except Exception:
            extracted = ""
    extracted = _council_sanitize_reply(extracted)

    res_body = re.sub(r"<<RES[^>]*>>|<</RES>>", "", latest_res).strip()
    res_empty = latest_res == "[no RES block]" or res_body == ""
    prompt_waiting_for_res = prompt_mtime > outbox_mtime
    # A new active dispatch owns the response panel. Never present the previous
    # dispatch's RES as if it belonged to the currently running request.
    runtime = _council_runtime_status(agent)
    if _council_runtime_entry_active(runtime):
        return "[waiting for current dispatch response]"
    if log_mtime > outbox_mtime and not extracted:
        return "[direct log: no final response]"
    if not res_empty and not prompt_waiting_for_res:
        return latest_res
    if prompt_waiting_for_res and log_mtime >= prompt_mtime and extracted:
        return _council_prefixed_reply_tail("[live response]\n", extracted)
    if res_empty and extracted:
        return _council_prefixed_reply_tail("[live response]\n", extracted)
    return latest_res


def _council_latest_prompt_source(council_dir: Path, agent: str) -> str:
    """Return newest prompt/run dispatch file, bounded by glob/read caps."""
    prompt_patterns = (
        f"prompt_{agent}_*.md",
        f"prompt_{agent}_*.txt",
    )
    run_patterns = (
        f"run_{agent}_*.bat",
        f"run_{agent}_*.cmd",
        f"run_{agent}_*.ps1",
    )

    def newest_for(patterns: tuple[str, ...]) -> tuple[float, Path] | None:
        candidates: list[Path] = []
        for pattern in patterns:
            try:
                candidates.extend(p for p in council_dir.glob(pattern) if p.is_file())
            except Exception:
                continue
        newest: tuple[float, Path] | None = None
        for path in candidates[:40]:
            try:
                mtime = path.stat().st_mtime
            except Exception:
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, path)
        return newest

    newest = newest_for(prompt_patterns) or newest_for(run_patterns)
    if newest is None:
        return ""
    path = newest[1]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    lines = text.splitlines()
    if len(lines) > 240:
        text = "\n".join(lines[-240:])
    if len(text) > 20000:
        text = text[-20000:]
    return f'<<MSG source="{path.name}">\n{text.strip()}\n<<END MSG>>'


def _council_read_json(path: Path) -> dict:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8', errors='replace'))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _council_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _council_session_meta_path(council_dir: Path) -> Path:
    return council_dir / _COUNCIL_SESSION_META


def _council_new_session_id() -> str:
    return f"council-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _council_read_session_meta(council_dir: Path) -> dict:
    path = _council_session_meta_path(council_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _council_write_session_meta(council_dir: Path, meta: dict) -> dict:
    council_dir.mkdir(parents=True, exist_ok=True)
    data = dict(meta)
    data.setdefault("session_id", _council_new_session_id())
    data.setdefault("started_at", _council_now_iso())
    path = _council_session_meta_path(council_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return data


def _council_ensure_session_meta(council_dir: Path) -> dict:
    meta = _council_read_session_meta(council_dir)
    if not meta.get("session_id"):
        meta = _council_write_session_meta(council_dir, meta)
    return meta


def _council_event_path() -> Path:
    return _HERE / ".council" / _COUNCIL_EVENT_LOG


def _council_append_event(event: dict) -> dict:
    path = _council_event_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    event = dict(event)
    event.setdefault("ts", _council_now_iso())
    event.setdefault("mtime", time.time())
    event.setdefault("id", f"evt-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}")
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    _council_dashboard_payload_invalidate()
    return event


def _council_event_sort_time(event: dict) -> float:
    try:
        mtime = event.get("mtime")
        if mtime not in (None, ""):
            return float(mtime)
    except Exception:
        pass
    ts = str(event.get("ts", "")).strip()
    if ts:
        try:
            return __import__("datetime").datetime.fromisoformat(
                ts.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            pass
    return 0.0


def _council_read_events(limit: int = 80) -> list[dict]:
    path = _council_event_path()
    if not path.exists():
        return []
    bounded_limit = max(1, min(500, int(limit)))
    return read_jsonl_tail(path, limit=bounded_limit, max_bytes=2_000_000)


def _council_build_broker_context(target, sender, text, budget_chars=16000, task_id="") -> str:
    # DISABLED (user decision 2026-06-12): auto broker-context injection was a bad idea —
    # it fed prior events (incl. a worker's own replies) back into the prompt, causing
    # weak/strong models alike to mimic/repeat instead of doing the task. Council context
    # is now carried ONLY by the leader embedding it in the prompt (BROKER-ONLY). Returning
    # "" disables injection everywhere; body kept for reference / possible future redesign.
    return ""
    try:
        target = str(target or "").strip()
        sender = str(sender or "").strip()
        task_id = str(task_id or "").strip()
        budget_chars = max(2000, min(20000, int(budget_chars or 16000)))
        events = _council_read_events(500)
        if not events:
            return ""
        by_id = {str(ev.get("id", "")): ev for ev in events if str(ev.get("id", ""))}
        selected_ids: set[str] = set()

        def add_event(ev: dict) -> None:
            eid = str(ev.get("id", ""))
            if eid:
                selected_ids.add(eid)

        # Conservative selector (fixes weak-model mimicry): inject ONLY the few most
        # recent INCOMING messages addressed to this worker (to==target, NOT its own
        # replies — weak models echo their own prior reply verbatim). No broad
        # sender/global chatter (that caused identity-ack repetition). Thread ancestors
        # are still added below via in_reply_to.
        incoming = [ev for ev in events
                    if str(ev.get("to", "")) == target and str(ev.get("type", "")) != "reply"]
        for ev in incoming[-3:]:
            add_event(ev)

        roots = [by_id.get(task_id)] if task_id and by_id.get(task_id) else []
        roots.extend(ev for ev in events if str(ev.get("id", "")) in selected_ids)
        for ev in roots:
            seen: set[str] = set()
            reply_to = str((ev or {}).get("in_reply_to", "")).strip()
            while reply_to and reply_to not in seen:
                seen.add(reply_to)
                parent = by_id.get(reply_to)
                if not parent:
                    break
                add_event(parent)
                reply_to = str(parent.get("in_reply_to", "")).strip()

        selected = [ev for ev in events if str(ev.get("id", "")) in selected_ids]
        if not selected:
            return ""
        selected = sorted(selected, key=_council_event_sort_time)
        recent = selected[-12:]
        older = selected[:-12]

        def clean_preview(value: object, max_len: int = 520) -> str:
            preview = _strip_ansi(str(value or ""))
            preview = " ".join(preview.split())
            if len(preview) > max_len:
                preview = preview[: max_len - 3].rstrip() + "..."
            return preview

        def event_line(ev: dict) -> str:
            ok_value = ev.get("ok", "")
            if ok_value == "":
                err = str(ev.get("error_name") or ev.get("error") or "").strip()
                ok_part = f"ok=error:{clean_preview(err, 80)}" if err else "ok="
            else:
                ok_part = f"ok={bool(ok_value)}"
            return (
                f"- {str(ev.get('ts', ''))} | {str(ev.get('id', ''))} | "
                f"{str(ev.get('from', ''))}->{str(ev.get('to', ''))} | "
                f"{str(ev.get('type', ''))} | {ok_part} | "
                f"reply_to={str(ev.get('in_reply_to', ''))} | {clean_preview(ev.get('text', ''))}"
            )

        status = _council_runtime_status(target) if target else {}
        running = status.get("request_id") if status.get("state") == "running" else "none"
        lines = [
            "[Council broker context - compact]",
            f"session: current",
            f"target: {target}",
            f"sender: {sender}",
            "route_filter: to/from target + sender recent + thread ancestors"
            + ("" if task_id else " + recent global events"),
            "recent_events:",
        ]
        lines.extend(event_line(ev) for ev in recent)
        if older:
            first_id = str(older[0].get("id", ""))
            last_id = str(older[-1].get("id", ""))
            participants = sorted({str(ev.get("from", "")) for ev in older if ev.get("from")})
            lines.extend([
                "older_summary:",
                f"- {len(older)} older routed events omitted (src: {first_id}..{last_id}; from: {', '.join(participants[:8])})",
            ])
        lines.extend([
            "open_controls:",
            f"- paused=false; queue_depth=0; running={running or 'none'}",
            "[End council broker context]",
        ])
        block = "\n".join(lines)
        if len(block) > budget_chars:
            keep = max(0, budget_chars - len("\n[End council broker context]") - 3)
            block = block[:keep].rstrip() + "...\n[End council broker context]"
        return block
    except Exception:
        return ""


def _council_latest_broker_reply(agent: str) -> str:
    event = _council_latest_broker_event(agent)
    if event:
        text = _council_broker_visible_reply(event.get("text", ""))
        if text:
            return "[broker response]\n" + text
    return ""


def _council_latest_broker_raw(agent: str) -> str:
    """Latest raw/reasoning stream from the agent's most recent broker reply."""
    event = _council_latest_broker_event(agent)
    if event:
        return str(event.get("raw", "")).strip()
    return ""


def _council_latest_broker_event(agent: str) -> dict:
    for event in reversed(_council_read_events(120)):
        if event.get("type") == "reply" and event.get("from") == agent:
            return event
    return {}


def _council_extract_claude_streamjson(stdout: str):
    """Return (reply, reasoning) from claude --output-format stream-json output."""
    reply = ""
    thinking_parts: list[str] = []
    usage_str = ""
    for ln in stdout.splitlines():
        s = ln.strip()
        if not (s.startswith("{") and s.endswith("}")):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        t = obj.get("type")
        if t == "result":
            if isinstance(obj.get("result"), str):
                reply = obj["result"].strip()
            _u = obj.get("usage") or {}
            _it = _u.get("input_tokens"); _ot = _u.get("output_tokens")
            _cost = obj.get("total_cost_usd")
            _bits = []
            if _it is not None or _ot is not None:
                _bits.append("tokens in=%s out=%s" % (_it, _ot))
            if _cost is not None:
                _bits.append("cost=$%s" % _cost)
            if _bits:
                usage_str = "[claude usage] " + "  ".join(_bits)
        elif t == "assistant":
            msg = obj.get("message") or {}
            for block in (msg.get("content") or []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text") and not reply:
                    reply = str(block["text"]).strip()
                elif block.get("type") == "thinking" and block.get("thinking"):
                    thinking_parts.append(str(block["thinking"]).strip())
    reasoning = "\n".join(p for p in thinking_parts if p) or usage_str
    return _council_reply_tail(reply), reasoning[:_COUNCIL_REPLY_MAX]


def _council_extract_response_tokens(agent: str, raw_text: str) -> dict:
    """Extract token usage for one worker response from its CLI output."""
    raw_text = raw_text or ""
    candidates: list[dict] = []

    def visit(value):
        if isinstance(value, dict):
            tokens = value.get("tokens")
            if isinstance(tokens, dict):
                candidates.append(tokens)
            usage = value.get("usage")
            if isinstance(usage, dict):
                candidates.append(usage)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for line in raw_text.splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            visit(json.loads(line))
        except Exception:
            continue

    aliases = {
        "input": ("input", "input_tokens", "tokensIn", "promptTokens"),
        "output": ("output", "output_tokens", "tokensOut", "outputTokens"),
        "reasoning": ("reasoning", "reasoning_output_tokens", "tokens_reasoning"),
        "total": ("total", "total_tokens", "tokens_used", "tokensUsed"),
    }
    for candidate in reversed(candidates):
        result = {}
        for target, keys in aliases.items():
            for key in keys:
                if candidate.get(key) not in (None, ""):
                    try:
                        result[target] = int(candidate[key])
                    except Exception:
                        pass
                    break
        if result:
            if "total" not in result:
                result["total"] = sum(result.get(k, 0) for k in ("input", "output", "reasoning"))
            return result

    match = re.search(r"tokens\s+in=([\d,]+)\s+out=([\d,]+)", raw_text, re.IGNORECASE)
    if match:
        input_tokens = int(match.group(1).replace(",", ""))
        output_tokens = int(match.group(2).replace(",", ""))
        return {"input": input_tokens, "output": output_tokens, "total": input_tokens + output_tokens}
    match = re.search(r"tokens\s+used\s*[\r\n:]+\s*([\d,]+)", raw_text, re.IGNORECASE)
    if match:
        return {"total": int(match.group(1).replace(",", ""))}
    return {}


# ─── Context registry (provider-native session ids) ───────────────────────────
# Multi-turn "context" mode keeps a provider-native session id per (agent,
# project_path) so a follow-up turn resumes the same provider thread. We store
# these in .council/context_registry.json — deliberately NOT the [A] sessions.db,
# because these ids belong to the external providers (claude/codex/opencode),
# not to [A]'s own session bookkeeping. A monotonic `generation` counter gates
# validity: clear-session bumps it, which invalidates every stored context so the
# next context-mode turn starts a fresh provider thread.
_COUNCIL_CONTEXT_LOCK = threading.Lock()


def _council_context_registry_path() -> Path:
    return _HERE / ".council" / "context_registry.json"


def _council_load_context_registry() -> dict:
    path = _council_context_registry_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            data.setdefault("generation", 1)
            if not isinstance(data.get("contexts"), dict):
                data["contexts"] = {}
            return data
    except Exception:
        pass
    return {"generation": 1, "contexts": {}}


def _council_save_context_registry(data: dict) -> None:
    path = _council_context_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _council_context_generation() -> int:
    with _COUNCIL_CONTEXT_LOCK:
        return int(_council_load_context_registry().get("generation", 1))


def _council_bump_context_generation() -> int:
    with _COUNCIL_CONTEXT_LOCK:
        data = _council_load_context_registry()
        data["generation"] = int(data.get("generation", 1)) + 1
        # Stale contexts are kept but become invalid (generation mismatch);
        # drop them to keep the file small.
        data["contexts"] = {}
        _council_save_context_registry(data)
        return data["generation"]


def _council_context_key(agent: str, project_path: str) -> str:
    proj = (project_path or "").strip().replace("\\", "/").rstrip("/").lower()
    return f"{agent}|{proj}"


def _council_get_context(agent: str, project_path: str) -> dict | None:
    """Return a live context entry, or None if absent/stale (generation bumped)."""
    with _COUNCIL_CONTEXT_LOCK:
        data = _council_load_context_registry()
        entry = data.get("contexts", {}).get(_council_context_key(agent, project_path))
        if not isinstance(entry, dict):
            return None
        if int(entry.get("generation", 0)) != int(data.get("generation", 1)):
            return None
        if not entry.get("session_id"):
            return None
        return entry


def _council_set_context(agent: str, project_path: str, session_id: str, provider: str,
                         last_prompt: str = "", last_reply: str = "") -> dict:
    with _COUNCIL_CONTEXT_LOCK:
        data = _council_load_context_registry()
        key = _council_context_key(agent, project_path)
        prev = data.get("contexts", {}).get(key) or {}
        same_gen = int(prev.get("generation", 0)) == int(data.get("generation", 1))
        entry = {
            "agent": agent,
            "provider": provider,
            "project_path": project_path or "",
            "session_id": session_id,
            "generation": int(data.get("generation", 1)),
            "turns": (int(prev.get("turns", 0)) + 1) if same_gen else 1,
            "updated": time.time(),
            "last_prompt": last_prompt[:2000] if last_prompt else "",
            "last_reply": last_reply[:2000] if last_reply else "",
        }
        data.setdefault("contexts", {})[key] = entry
        _council_save_context_registry(data)
        return entry


def _council_find_latest_context(agent: str) -> dict | None:
    """Most recently updated live context entry for an agent across all projects.
    Used by the global dashboard, which has no project_path. Generation-gated."""
    with _COUNCIL_CONTEXT_LOCK:
        data = _council_load_context_registry()
        gen = int(data.get("generation", 1))
        live = [
            e for e in data.get("contexts", {}).values()
            if isinstance(e, dict) and e.get("agent") == agent
            and int(e.get("generation", 0)) == gen and e.get("session_id")
        ]
        if not live:
            return None
        return max(live, key=lambda e: float(e.get("updated", 0.0)))


def _council_context_status(agent: str, project_path: str = "") -> dict:
    """Compact per-agent context state for /api/council/state."""
    caps = _council_agent_caps(agent)
    if caps.get("context"):
        entry = _council_get_context(agent, project_path) if project_path else _council_find_latest_context(agent)
    else:
        entry = None
    return {
        "supported": bool(caps.get("context")) and bool(caps.get("enabled", True)),
        "enabled": bool(caps.get("enabled", True)),
        "provider": caps.get("provider", agent),
        "active": bool(entry),
        "session_id": (entry or {}).get("session_id", ""),
        "project_path": (entry or {}).get("project_path", ""),
        "turns": (entry or {}).get("turns", 0),
        "generation": _council_context_generation(),
    }


def _council_extract_codex_thread_id(stdout: str) -> str:
    """Capture thread.started.thread_id from `codex exec --json` JSONL stdout."""
    for ln in stdout.splitlines():
        s = ln.strip()
        if not (s.startswith("{") and s.endswith("}")):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        # Shapes seen: {"type":"thread.started","thread_id":"..."} and nested
        # {"type":"thread.started","thread":{"id":"..."}}.
        if obj.get("type") in ("thread.started", "session.created", "session_configured"):
            tid = obj.get("thread_id") or obj.get("session_id")
            if not tid:
                inner = obj.get("thread") or obj.get("session") or {}
                if isinstance(inner, dict):
                    tid = inner.get("id") or inner.get("thread_id")
            if tid:
                return str(tid)
    return ""


def _council_extract_opencode_session_id(stdout: str) -> str:
    """Capture sessionID from `opencode run --format json` output."""
    for ln in stdout.splitlines():
        s = ln.strip()
        if not (s.startswith("{") and s.endswith("}")):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        stack = [obj]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                for k in ("sessionID", "sessionId", "session_id"):
                    if cur.get(k):
                        return str(cur[k])
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
    return ""


def _council_extract_pi_session_id(stdout: str) -> str:
    """Newest pi session UUID under ~/.pi/projects/<encoded_path>/."""
    try:
        projects_base = Path.home() / ".pi" / "projects"
        if not projects_base.is_dir():
            return ""
        # Find the project directory (encoded path like "c-users-admin")
        project_dirs = [d for d in projects_base.iterdir() if d.is_dir()]
        if not project_dirs:
            return ""
        # Get all .jsonl session files (exclude .checkpoints.jsonl and .meta.json)
        session_files = []
        for pdir in project_dirs:
            for f in pdir.glob("*.jsonl"):
                if not f.name.endswith(".checkpoints.jsonl"):
                    session_files.append(f)
        if not session_files:
            return ""
        # Return the UUID stem of the most recently modified session file
        newest = max(session_files, key=lambda f: f.stat().st_mtime)
        return newest.stem  # UUID without .jsonl extension
    except Exception:
        return ""


def _council_worker_model(agent: str, default_provider: str = "", default_model: str = "",
                          provider_override: str = "", model_override: str = "") -> tuple[str, str]:
    """User-set provider/model override from .council/worker_models.json (UI-editable,
    no restart needed — read at dispatch time). Falls back to the per-agent defaults."""
    prov, model = default_provider, default_model
    try:
        path = _HERE / ".council" / "worker_models.json"
        with _COUNCIL_WORKER_MODELS_LOCK:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    logger.warning("[council] worker_models.json ignored: root is not an object")
                    return prov, model
                entry = data.get(agent)
                if isinstance(entry, dict):
                    # A31: the override file is UI/hand-editable and bypasses the write
                    # tool's _council_validate_model_setting gate, so a garbage entry
                    # (e.g. provider 'agent247' / model 'agent/...') would otherwise be
                    # applied verbatim and silently destroy a working config default.
                    # Validate the candidate the SAME way the write path does; on
                    # rejection keep the incoming defaults instead of the garbage.
                    # (PROVIDER_MODEL_MISMATCH is only a warning -> ok:True, so a valid
                    # curated model is never discarded merely for a provider label.)
                    cand_prov = str(entry.get("provider") or "").strip() or prov
                    cand_model = str(entry.get("model") or "").strip() or model
                    checked = _council_validate_model_setting(
                        agent, cand_prov, cand_model)
                    if checked.get("ok"):
                        prov, model = cand_prov, cand_model
                    else:
                        logger.warning(
                            "[council] worker_models.json override rejected for "
                            "%s (%s: provider=%r model=%r); falling back to config "
                            "default provider=%r model=%r",
                            agent, checked.get("error_name", "INVALID"),
                            cand_prov, cand_model, prov, model)
                elif entry is not None:
                    logger.warning(f"[council] worker_models.json ignored for {agent}: entry is not an object")
    except Exception as exc:
        logger.warning(f"[council] worker_models.json read failed for {agent}: {exc}")
    model_override = str(model_override or "").strip()
    provider_override = str(provider_override or "").strip()
    if model_override:
        model = model_override
        prov = provider_override or _council_provider_for_model(agent, model_override) or prov
    elif provider_override:
        prov = provider_override
    return prov, model

def _council_worker_model_configured(agent: str, model_override: str = "") -> bool:
    """True when dispatch has an explicit/requested model, not a command-builder fallback."""
    if str(model_override or "").strip():
        return True
    defaults = _COUNCIL_DEFAULT_MODELS.get(agent, {}) or {}
    _, model = _council_worker_model(
        agent,
        str(defaults.get("provider") or ""),
        str(defaults.get("model") or ""),
        "",
        "",
    )
    return bool(str(model or "").strip())

def _resolve_cmd_shim(shim_path: str) -> tuple[str, list[str]]:
    """Resolve an npm `.cmd`/`.bat` shim to its REAL target so the worker prompt
    never passes through cmd.exe.

    On Windows, spawning a `.cmd` shim runs it via cmd.exe, which RE-PARSES the
    command line: a prompt containing cmd metacharacters (| & < > ( ) ^) or
    embedded double-quotes desyncs cmd's quote tracking and a token gets executed
    as a command (BatBadBut / CVE-2024-1874; observed: `'mov' is not recognized`
    from a prompt with regex `mp4|mov|mxf`). Resolving to the underlying `.exe` or
    `node <script>` and spawning THAT directly keeps cmd.exe out of the path, so
    Python's argv reaches the program intact — content-agnostic, behavior-preserving
    (this is also what Node 18.20+/20.12+ do, CVE-2024-27980).

    Two npm shim variants are handled:
      A) native exe:   "%dp0%\\...\\x.exe"   %*           -> (x.exe, [])
      B) node script:  "%_prog%"  "%dp0%\\...\\x.(m)js" %* -> (node.exe, [x.js])
    Returns (shim_path, []) unchanged if it is not a parseable shim (safe fallback;
    the original — possibly fragile — path is preserved rather than failing).
    """
    try:
        low = shim_path.lower()
        if not (low.endswith(".cmd") or low.endswith(".bat")):
            return shim_path, []
        p = Path(shim_path)
        if not p.is_file():
            return shim_path, []
        text = p.read_text(encoding="utf-8", errors="replace")
        dp0 = str(p.parent)
        # Variant B FIRST: node-script shims contain a `"%_prog%"` invocation AND a
        # `"%dp0%\\node.exe"` existence probe — so the Variant-A .exe regex would
        # wrongly latch onto that node.exe probe. The `"%_prog%"` marker is unique to
        # node shims, so detect them before the native-exe case.
        m_node = re.search(r'"%_prog%"\s+"%dp0%\\(.+?\.m?js)"', text, re.IGNORECASE)
        if m_node:
            script = os.path.join(dp0, m_node.group(1))
            node_exe = os.path.join(dp0, "node.exe")
            if not os.path.isfile(node_exe):
                node_exe = "node"
            if os.path.isfile(script):
                return node_exe, [script]
        # Variant A: direct native exe.
        m_exe = re.search(r'"%dp0%\\(.+?\.exe)"', text, re.IGNORECASE)
        if m_exe:
            real = os.path.join(dp0, m_exe.group(1))
            if os.path.isfile(real):
                return real, []
    except Exception:
        pass
    return shim_path, []


def _council_codex_executable() -> str:
    """Use the same dedicated LTNAI launcher as the working CodoC CLI."""
    configured = os.environ.get("COUNCIL_CODO_CLI_PATH", "").strip()
    if configured and os.path.isfile(configured):
        return configured
    launcher = Path.home() / "AppData" / "Roaming" / "npm" / "codexltnai.cmd"
    return str(launcher) if launcher.exists() else "codexltnai"


def _council_codo_api_key(env: dict[str, str]) -> str:
    dedicated = str(env.get("COUNCIL_CODO_API_KEY") or "").strip()
    if dedicated:
        return dedicated
    candidates = (
        Path.home() / ".config" / "kilo" / "kilo.jsonc",
        Path.home() / ".config" / "opencode" / "opencode.json",
    )
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            provider = re.search(r'"(?:ltnai|openai-compatible)"\s*:\s*\{', text)
            if not provider:
                continue
            tail = text[provider.end():]
            key = re.search(r'"apiKey"\s*:\s*"([^"\\]+)"', tail)
            base = re.search(r'"baseURL"\s*:\s*"([^"]+)"', tail)
            if key and base and "ltnaidevservices.ltnclaudeproxy.shop" in base.group(1):
                return key.group(1)
        except Exception:
            continue
    generic = str(env.get("API_KEY") or "").strip()
    if generic:
        return generic
    raise RuntimeError(
        "CodoC LTNAI credential is unavailable; set COUNCIL_CODO_API_KEY "
        "or configure the local Kilo/OpenCode LTNAI provider"
    )


def _council_prepare_codo_env(env: dict[str, str]) -> None:
    """Use codexltnai's own configuration; only inject its dedicated API key."""
    env["API_KEY"] = _council_codo_api_key(env)


def _council_claudeltnai_target() -> tuple[str, dict[str, str]]:
    """Resolve the claudeltnai harness to (real executable, env overrides).

    `claudeltnai` is a HAND-WRITTEN npm shim, not an npm-generated one: it only
    exports the LTNAI environment and then CALLs claude.cmd. Two consequences bit
    ClauC in production:
      * `_resolve_cmd_shim` cannot parse it (there is no quoted %dp0% .exe target line), so
        the argv kept going through cmd.exe and hit the 8191-char batch limit --
        every real broker prompt died with the cmd.exe argv-length refusal
      * Resolving it naively to claude.exe DROPS that environment, so ClauC ran as
        vanilla `claude` against the wrong config dir (observed 2026-08-19: hooks
        loaded from ~/.claude instead of ~/.claude-ltnai). Wrong harness, wrong
        account, wrong proxy -- not a cosmetic difference.
    So spawn claude.exe directly AND rebuild the shim's environment in Python.
    Falls back to the shim path with no overrides if the real exe is missing.
    """
    npm_bin = Path.home() / "AppData" / "Roaming" / "npm"
    shim = str(npm_bin / "claudeltnai.cmd")
    exe = npm_bin / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    if not exe.is_file():
        return shim, {}
    return str(exe), {
        "CLAUDE_CONFIG_DIR": str(Path.home() / ".claude-ltnai"),
        "ANTHROPIC_BASE_URL": "https://api.ltnproxy.com",
    }


def _council_ltn_key(env: dict[str, str]) -> str:
    """LTN proxy token, read from the same source the shim uses: LTN_KEY (User)."""
    key = str(env.get("LTN_KEY") or "").strip()
    if key:
        return key
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "[Environment]::GetEnvironmentVariable('LTN_KEY','User')"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _council_prepare_clau_env(env: dict[str, str]) -> None:
    """Recreate claudeltnai.cmd's environment for a direct claude.exe spawn."""
    _, overrides = _council_claudeltnai_target()
    if not overrides:
        return
    env.update(overrides)
    key = _council_ltn_key(env)
    if key:
        env["LTN_KEY"] = key
        env["ANTHROPIC_AUTH_TOKEN"] = key
    # The shim deletes this so the proxy token is the only credential in play;
    # leaving an inherited key here would silently re-target the real Anthropic API.
    env.pop("ANTHROPIC_API_KEY", None)


def _council_agent_command(agent: str, prompt: str, *, mode: str = "broker",
                           resume_id: str = "", new_session_id: str = "",
                           cwd: Path | None = None,
                           provider_override: str = "",
                           model_override: str = "") -> list[str]:
    if cwd is None:
        cwd = _HERE
    npm_bin = Path.home() / "AppData" / "Roaming" / "npm"

    def cli(name: str) -> str:
        cmd = npm_bin / f"{name}.cmd"
        return str(cmd) if cmd.exists() else name

    # ── Plug-in point (hub v2): a worker whose council_config.json entry defines
    # an "argv" list template runs WITHOUT any per-CLI Python branch below.
    # Placeholders: {prompt} {model} {provider} {cwd}. Model/provider resolve
    # config defaults overlaid by .council/worker_models.json (dispatch-time).
    try:
        _cfg_entry = (ct._load_council_config() or {}).get("agents", {}).get(agent) or {}
    except Exception:
        _cfg_entry = {}
    _argv_tpl = _cfg_entry.get("argv")
    if isinstance(_argv_tpl, list) and _argv_tpl:
        prov, model = _council_worker_model(
            agent, str(_cfg_entry.get("provider") or ""), str(_cfg_entry.get("model") or ""),
            provider_override, model_override)
        out = [str(part).replace("{prompt}", prompt).replace("{model}", model)
                        .replace("{provider}", prov).replace("{cwd}", str(cwd))
               for part in _argv_tpl]
        out[0] = cli(out[0])
        return out

    if agent == "clau":
        _, _model = _council_worker_model(
            "clau", "", _COUNCIL_DEFAULT_MODELS.get("clau", {}).get("model") or "claude-opus-5",
            provider_override, model_override)
        # The custom --system-prompt overrides the CLI's default (which normally tells claude its
        # own model id), so inject the model id here or clauC won't know its model in council.
        _clau_sys = _COUNCIL_BROKER_SYSTEM + (
            f" Your underlying model id is '{_model}' (set by the CLI --model flag = source of truth). "
            f"When asked which model/version you are, answer EXACTLY '{_model}'."
        )
        base = [
            # Direct exe + _council_prepare_clau_env(): keeps the claudeltnai
            # identity while keeping cmd.exe (8191-char limit) out of the argv.
            _council_claudeltnai_target()[0], "--print", "--model", _model,
            "--output-format", "stream-json", "--include-partial-messages", "--verbose",
            "--system-prompt", _clau_sys,
            "--dangerously-skip-permissions",
        ]
        if mode == "broker":
            base += ["--no-session-persistence"]
        if mode == "context" and resume_id:
            # Resume an existing claude session id.
            base += ["--resume", resume_id]
        elif mode == "context" and new_session_id:
            # First context turn: pin a caller-generated UUID v4 session id.
            base += ["--session-id", new_session_id]
        base += ["-p", prompt]
        return base
    if agent == "ope":
        _provider, _model = _council_worker_model(
            "ope", "", _COUNCIL_DEFAULT_MODELS.get("ope", {}).get("model") or "openai-compatible/grok-4.6-build",
            provider_override, model_override)
        # Curated IDs may be bare API model IDs; OpenCode CLI requires provider/model.
        # Preserve the selected model and already-qualified IDs.
        if "/" not in _model and _provider:
            _model = f"{'opencode' if _provider == 'opencode-zen' else _provider}/{_model}"
        _ope_thinking = _model.endswith("glm-5.2")
        base = [
            cli("opencode"), "run", "--dir", str(cwd), "--model", _model,
            "--format", "json", "--dangerously-skip-permissions",
        ]
        if _ope_thinking:
            base.insert(-1, "--thinking")
        if mode == "context" and resume_id:
            base += ["--session", resume_id]
        base += [prompt]
        return base
    if agent == "codo":
        # NOTE: _council_run_agent rewrites the trailing prompt element into
        # `--output-last-message <file> -` and feeds the prompt over stdin, so
        # the final element here is a placeholder that gets stripped.
        # `-c model_reasoning_summary="detailed"` makes codex emit a READABLE
        # reasoning summary (the raw reasoning stays encrypted by OpenAI). Only
        # populated when the model actually generates one; harmless otherwise.
        _rsum = ["-c", 'model_reasoning_summary="detailed"',
                 "-c", 'model_reasoning_effort="xhigh"']
        _, _model = _council_worker_model(
            "codo", "codex", _COUNCIL_DEFAULT_MODELS.get("codo", {}).get("model") or "grok-4.6-build",
            provider_override, model_override)
        _route = ["--model", _model]
        _no_nested_sandbox = ["--dangerously-bypass-approvals-and-sandbox"]
        if mode == "context" and resume_id:
            # Council workers already run inside the orchestrator boundary. On Windows,
            # nesting Codex's restricted-token sandbox can fail with WinError 1344 when
            # the parent process is itself constrained. Keep one sandbox boundary only.
            return [_council_codex_executable(), "exec", "resume", resume_id,
                    *_no_nested_sandbox, *_route, *_rsum,
                    "--skip-git-repo-check", "--json", prompt]
        if mode == "context":
            return [_council_codex_executable(), "exec", "--json", *_route, *_rsum,
                    *_no_nested_sandbox, "--skip-git-repo-check", prompt]
        return [_council_codex_executable(), "exec", "--ephemeral", *_route, *_rsum,
                *_no_nested_sandbox, "--skip-git-repo-check", prompt]
    if agent == "pic":
        _prov, _model = _council_worker_model(
            "pic", "ltnai", _COUNCIL_DEFAULT_MODELS.get("pic", {}).get("model") or "deepseek-v4-pro",
            provider_override, model_override)
        return [cli("pi"), "--print", "--no-session", "--approve",
                "--provider", _prov or "ltnai", "--model", _model,
                "--thinking", "high", prompt]
    if agent == "hermes":
        _prov, _model = _council_worker_model("hermes", "", "", provider_override, model_override)
        # `chat -q` (non-interactive single query) renders the linear ┌─ Reasoning ─┐
        # + ╭─ Hermes ─╮ boxes to stdout (parsed by _council_parse_hermes_chat),
        # so reasoning is captured. The top-level `-z` one-shot does NOT emit the
        # reasoning block (it is a TUI/interactive-only render).
        # Keep HermC broker close to Hermes desktop for internet research: web/file
        # alone disables the terminal + skills toolsets that [antibot], Camoufox WSL,
        # and last30days rely on. Do not enable the browser toolset by default; it
        # previously caused Chrome/Camoufox window spam. Terminal + skills are enough
        # for the existing antibot wrappers while browser/DOM stateful work stays on
        # ClauC/Playwright.
        base = [cli("hermes"), "chat", "-q", prompt, "--yolo", "-t", "web,file,terminal,skills,session_search"]
        if _model and _model not in ("", "(cli default)"):
            base += ["-m", _model]
        if _prov and _prov not in ("", "hermes", "(cli default)"):
            base += ["--provider", _prov]
        return base
    if agent == "kilo":
        # Kilo CLI is an OpenCode fork: same `run` subcommand + json format +
        # session-id shape (sessionID). Reuse the opencode parser for reply +
        # session extraction. Provider/model come from council_worker_model.
        # NOTE: kilo.jsonc registers provider id `ltnai` (display name); the
        # `ai_sdk_provider: openai-compatible` is only the internal SDK adapter
        # and must NOT be used as the --model prefix. Kilo --model takes
        # provider/model (e.g. ltnai/glm-5.2) and resolves the adapter itself.
        # --dir is intentionally omitted: kilo's argv parser splits the path on
        # whitespace ("Failed to change directory to D:\0."), so we rely on the
        # subprocess.Popen cwd= that _council_run_agent already sets.
        _prov, _model = _council_worker_model(
            "kilo", "", _COUNCIL_DEFAULT_MODELS.get("kilo", {}).get("model") or "ltnai/glm-5.2",
            provider_override, model_override)
        base = [
            cli("kilo"), "run", "--model", _model,
            "--format", "json", "--dangerously-skip-permissions",
        ]
        if mode == "context" and resume_id:
            base += ["--session", resume_id]
        base += [prompt]
        return base
    raise ValueError(f"unknown agent: {agent}")


def _council_kill_process_tree(pid: int) -> None:
    if pid <= 0:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.kill(pid, 9)
    except Exception as exc:
        # W1_C1 fix: best-effort stays best-effort, but no longer silent —
        # a kill failure leaves a live orphan CLI that operators must see.
        try:
            print(f"[council] kill_process_tree pid={pid} failed: {type(exc).__name__}: {exc}", flush=True)
        except Exception:
            pass


def _council_cleanup_broker_session_async(agent: str, raw: str, prompt: str, started_at: float) -> None:
    """Fire-and-forget wrapper: session cleanup spawns provider CLIs (up to 30s
    each) — running it inline added that latency to EVERY broker reply. The
    reply must return immediately; cleanup is best-effort housekeeping."""
    t = threading.Thread(
        target=_council_cleanup_broker_session, args=(agent, raw, prompt, started_at),
        daemon=True, name=f"council-cleanup-{agent}",
    )
    t.start()


def _council_cleanup_broker_session(agent: str, raw: str, prompt: str, started_at: float) -> bool:
    """Best-effort deletion of provider sessions created by one-shot broker runs."""
    npm_bin = Path.home() / "AppData" / "Roaming" / "npm"

    def cli(name: str) -> str:
        cmd = npm_bin / f"{name}.cmd"
        return str(cmd) if cmd.exists() else name

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        if agent == "ope":
            session_id = _council_extract_opencode_session_id(raw)
            if not session_id:
                return False
            subprocess.run(
                [cli("opencode"), "session", "delete", session_id],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30, creationflags=flags,
            )
            return True
        if agent == "kilo":
            # Kilo is an OpenCode fork: same session id shape + `kilo session delete`.
            session_id = _council_extract_opencode_session_id(raw)
            if not session_id:
                return False
            subprocess.run(
                [cli("kilo"), "session", "delete", session_id],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30, creationflags=flags,
            )
            return True
    except Exception:
        return False
    return False


def _council_parse_hermes_chat(raw: str, sent_prompt: str = "") -> tuple[str, str]:
    """Extract (reply, reasoning) from `hermes chat -q` linear output.

    The answer sits inside the ╭─ ⚕ Hermes ─╮ … ╰─╯ box and the chain-of-thought
    inside the ┌─ Reasoning ─┐ … └─┘ box. Box borders, the 'Query:'/'Initializing'
    preamble and the 'Resume this session…/Session:/Duration:/Messages:' footer are
    dropped. Falls back to a noise-filtered strip if the boxes are absent."""
    text = _strip_ansi(raw or "").replace("\r", "")
    # Subtract the echoed prompt LINE BY LINE before parsing. hermes chat -q takes the
    # task on argv and echoes it back, so the broker reply was arriving with the whole
    # OUTPUT CONTRACT block glued on. Marker matching was not enough: the leak was the
    # MIDDLE of that block ("write_new_file tool (create it):"), not a line that starts
    # with a recognisable marker. Comparing against what we actually sent removes the
    # echo exactly, with no guessing.
    lines = text.split("\n")
    reasoning_lines: list[str] = []
    reply_lines: list[str] = []
    mode = None  # None | 'reason' | 'reply'
    for ln in lines:
        s = ln.strip()
        # Current hermes builds render the section header as a RULE, not a box corner:
        #   "─  ⚕ Hermes  ─────────────..."   and   "─  Reasoning  ────..."
        # The old check only accepted ╭/┌, so the header never matched, mode stayed None,
        # and every dispatch fell through to the permissive fallback - which is how the
        # echoed prompt ended up in the broker reply. Accept the rule form too, and use
        # the LAST header seen so a later Hermes section wins over an earlier one.
        if s.startswith(("┌", "─", "╭")) and "Reasoning" in s:
            mode = "reason"; continue
        if s.startswith(("╭", "┌", "─")) and "Hermes" in s:
            mode = "reply"
            reply_lines.clear()   # discard anything captured before the real answer
            continue
        if s.startswith(("└", "╰")):
            mode = None; continue
        # A bare rule (box dashes only, no label) CLOSES the section. Current hermes
        # builds end the answer block that way instead of with a corner, so without this
        # the session footer stayed inside mode='reply' and came back as the answer.
        if s and set(s) <= set("- \u2014\u2500") and len(s) >= 8:
            mode = None; continue
        if mode == "reply" and s.startswith(("Resume this session", "Session:",
                                             "Duration:", "Messages:")):
            mode = None; continue
        if mode == "reason":
            reasoning_lines.append(ln.rstrip())
        elif mode == "reply":
            inner = ln.rstrip()
            if inner[:4] == "    ":
                inner = inner[4:]
            reply_lines.append(inner)
    reply = "\n".join(reply_lines).strip()
    reasoning = "\n".join(reasoning_lines).strip()
    if not reply:
        # Fallback when the ╭─ Hermes ─╮ box is absent. Modern `hermes chat -q` streams a
        # tool-activity feed of "┊ 📖 read" / "┊ 💻 $ cmd" lines and, because the broker
        # passes the task on argv, the CLI also echoes the prompt back. The old filter
        # only dropped box borders and the session footer, so BOTH leaked into the broker
        # reply - that is the "HermC returns verbose output mixed into the council" report.
        _foot = ("Resume this session", "Session:", "Duration:", "Messages:",
                 "Query:", "Initializing")
        # Markers that only ever appear in the echoed prompt, never in a real answer.
        _echo = ("BROKER TASK", "=== OUTPUT CONTRACT", "Output only the answer to",
                 "Do NOT echo, quote, or restate", "Write your FULL report",
                 "If you cannot write outside your cwd", "Then reply in chat with ONLY")
        body: list[str] = []
        for ln in lines:
            s = ln.strip()
            if not s:
                continue
            if s.startswith(("┌", "└", "╭", "╰", "───", "┊", "│")):
                continue          # box borders and tool-activity feed
            if s.startswith(_foot) or s.startswith(_echo):
                continue
            body.append(ln.rstrip())
        reply = "\n".join(body).strip()
    return _council_reply_tail(reply), reasoning


def _council_communicate_idle(proc, input_text, idle_sec=None, hard_cap_sec=None, live_path=""):
    """Drain a council worker until it exits, without execution timeouts.

    Legacy timeout arguments remain accepted for compatibility but are ignored.
    A separate 600-second monitor observes liveness and never kills a live worker.
    """
    import codecs as _codecs
    import threading as _th
    out_chunks, err_chunks = [], []
    live_fh = None
    live_lock = _th.Lock()
    if live_path:
        try:
            live_fh = open(live_path, "ab", buffering=0)
        except Exception:
            live_fh = None

    def _pump(stream, sink):
        dec = _codecs.getincrementaldecoder("utf-8")("replace")
        try:
            fd = stream.fileno()
        except Exception:
            return
        try:
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                if live_fh is not None:
                    try:
                        with live_lock:
                            live_fh.write(chunk)
                    except Exception:
                        pass
                sink.append(dec.decode(chunk))
        except Exception:
            pass
        finally:
            try:
                tail = dec.decode(b"", final=True)
                if tail:
                    sink.append(tail)
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    threads = []
    for stream, sink in ((proc.stdout, out_chunks), (proc.stderr, err_chunks)):
        if stream is None:
            continue
        thread = _th.Thread(target=_pump, args=(stream, sink), daemon=True)
        thread.start()
        threads.append(thread)
    if input_text is not None and proc.stdin:
        try:
            proc.stdin.write(input_text.encode("utf-8") if isinstance(input_text, str) else input_text)
        except Exception:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
    proc.wait()
    for thread in threads:
        thread.join()
    if live_fh is not None:
        try:
            live_fh.flush()
            live_fh.close()
        except Exception:
            pass
    return ("".join(out_chunks), "".join(err_chunks), False, "")


def _council_resolve_cwd(project_path: str) -> Path:
    """Return *project_path* as a Path when it points to an existing directory,
    otherwise fall back to the orchestrator install directory (_HERE)."""
    if project_path and os.path.isdir(project_path):
        return Path(project_path)
    return _HERE


def _council_run_agent(agent: str, prompt: str, timeout_sec: int = 6000,
                       mode: str = "broker", project_path: str = "",
                       request_id: str = "", runtime_key: str = "",
                       provider_override: str = "", model_override: str = "",
                       model_resolution: dict | None = None,
                       risk_level: str = "unknown", dev_level: str = "unknown") -> dict:
    run_started_at = time.time()
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "UTF-8")
    env.setdefault("PYTHONUTF8", "1")
    npm_bin = str(Path.home() / "AppData" / "Roaming" / "npm")
    env["PATH"] = npm_bin + os.pathsep + env.get("PATH", "")
    # Auto-identity: the worker CLI (and the [A] MCP server it spawns) inherit this.
    # dispatch_tool_call stamps it as the true `sender` for council_message so a
    # worker messaging another worker cannot spoof/omit who it is.
    env["COUNCIL_AGENT_ID"] = agent
    if agent == "codo":
        # This helper is live dispatch setup, not dead configuration scaffolding.
        _council_prepare_codo_env(env)
    if agent == "clau":
        # ClauC runs the claudeltnai harness (~/.claude-ltnai config + LTN proxy),
        # never vanilla `claude`. argv[0] is claude.exe so cmd.exe stays out of the
        # path; that means the shim's environment has to be injected right here.
        _council_prepare_clau_env(env)
    output_path = None
    input_text = None
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    # ── Resolve provider session continuity for context mode ──────────────────
    context_mode = mode == "context" and _council_supports_mode(agent, "context")
    resume_id = ""
    new_session_id = ""
    provider = _council_agent_caps(agent).get("provider", agent)
    if context_mode:
        existing = _council_get_context(agent, project_path)
        if existing:
            resume_id = str(existing.get("session_id") or "")
        elif agent == "clau":
            # claude session id is caller-chosen (UUID v4) on the first turn.
            new_session_id = str(uuid.uuid4())
    try:
        resolved_cwd = _council_resolve_cwd(project_path)
        # Identity reinforcement: always prepend agent identity so the LLM
        # doesn't drift to a different agent name from conversation history.
        _agent_label = _COUNCIL_AGENT_LABELS.get(agent, agent)
        _identity_prefix = (
            f"IDENTITY: You are {_agent_label}. Always refer to yourself as "
            f"{_agent_label}, never as any other agent.\n\n"
        )
        if model_resolution:
            try:
                resolution_values = {
                    key: value for key, value in dict(model_resolution).items()
                    if key in CouncilModelResolution.__dataclass_fields__
                }
                resolution_values["warnings"] = tuple(resolution_values.get("warnings") or ())
                resolution = CouncilModelResolution(**resolution_values)
            except (TypeError, ValueError):
                resolution = _council_resolve_model(agent, provider_override, model_override)
        else:
            resolution = _council_resolve_model(agent, provider_override, model_override)
        if resolution.resolution_status == "unavailable":
            return {"ok": False, "error_name": "COUNCIL_MODEL_UNAVAILABLE", "error": "requested model is unavailable",
                    "reply": "", "raw_preview": "", "provider": "", "model": "",
                    "model_resolution": resolution.as_dict()}
        effective_provider = resolution.resolved_provider
        effective_model = resolution.resolved_model
        effective_prompt = prompt
        cmd = _council_agent_command(
            agent, effective_prompt,
            mode="context" if context_mode else "broker",
            resume_id=resume_id, new_session_id=new_session_id,
            cwd=resolved_cwd,
            provider_override=effective_provider,
            model_override=effective_model,
        )
        if agent == "codo":
            # Preserve the active Codex CLI home/config so council CodoC gets the
            # same built-in tools, MCP/plugin registrations, auth, and sub-agent
            # surface as an interactive Codex CLI launched by this Windows user.
            fd, output_path = tempfile.mkstemp(prefix="council_codo_", suffix=".txt")
            os.close(fd)
            cmd = cmd[:-1] + ["--output-last-message", output_path, "-"]
            input_text = prompt
        # AUTO cmd-shim bypass (Windows): if cmd[0] is an npm .cmd/.bat shim, resolve
        # it to the real .exe / node script so the prompt argv NEVER passes through
        # cmd.exe (which re-parses | & < > ( ) " and corrupts prompts containing code
        # — BatBadBut / CVE-2024-1874). Generic + content-agnostic; no-op for native
        # binaries (e.g. hermes) and falls back to the shim unchanged if unparseable.
        _shim_exe, _shim_prefix = _resolve_cmd_shim(cmd[0])
        if _shim_exe != cmd[0] or _shim_prefix:
            cmd = [_shim_exe] + _shim_prefix + cmd[1:]
        # Budget is now an INACTIVITY window, not an absolute deadline: kill only
        # after `idle_value` s of total silence; `hard_cap_value` is a generous
        # absolute ceiling against a worker that spams output forever.
        idle_value = max(5, min(6000, int(timeout_sec)))
        # Hard cap: absolute ceiling against a worker that streams noise forever
        # (idle timer never fires). Old idle*20/min-12000s allowed up to 33h;
        # clamp to [30min, 4h] — legit council tasks finish well inside that.
        hard_cap_value = min(max(idle_value * 4, 1800), 14400)
        # Live raw capture: truncate a per-agent file and write the EXACT argv +
        # stdin so the dashboard shows the real prompt reaching the CLI (incl. the
        # literal \n the prompt is flattened to) plus all stdout/stderr in real time.
        live_path = ""
        try:
            live_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", runtime_key or agent).strip("_")[:120]
            live_path = str(_HERE / ".council" / f"live_raw_{live_name or agent}.log")
            _hdr = (
                f"=== COUNCIL RUN {request_id or ''} @ {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
                f"[AGENT] {agent}   [MODE] {'context' if context_mode else 'broker'}\n"
                f"[ARGV] {cmd!r}\n"
            )
            if input_text is not None:
                _hdr += f"[STDIN]\n{input_text}\n"
            _hdr += "--- LIVE OUTPUT (raw stdout+stderr, unfiltered) ---\n"
            with open(live_path, "wb") as _lf:
                _lf.write(_hdr.encode("utf-8", "replace"))
        except Exception:
            live_path = ""
        proc = subprocess.Popen(
            cmd,
            cwd=str(resolved_cwd),
            bufsize=0,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL if input_text is None else subprocess.PIPE,
            env=env,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        try:
            if request_id:
                _council_runtime_set_worker_pid(runtime_key or agent, request_id, proc.pid)
            try:
                stdout, stderr, timed_out, timeout_kind = _council_communicate_idle(
                    proc, input_text, idle_value, hard_cap_value, live_path,
                )
            except Exception:
                _council_kill_process_tree(proc.pid)
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                raise
        finally:
            for _pipe in (proc.stdout, proc.stderr):
                try:
                    if _pipe:
                        _pipe.close()
                except Exception:
                    pass
        if timed_out:
            _council_kill_process_tree(proc.pid)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            raw = ((stdout or "") + ("\n" + stderr if stderr else "")).strip()
            if not context_mode:
                _council_cleanup_broker_session_async(agent, raw, prompt, run_started_at)
            final_resolution, _ = _council_finalize_model_resolution(
                resolution, raw, risk_level, dev_level, request_id=request_id,
                session_id=resume_id or new_session_id, returncode=None)
            return {"ok": False, "returncode": None, "reply": _council_clean_timeout(raw),
                    "raw_preview": raw[:4000], "error": "timeout", "timeout_kind": timeout_kind,
                    "provider": final_resolution.resolved_provider, "model": final_resolution.resolved_model,
                    "model_resolution": final_resolution.as_dict()}
        returncode = proc.returncode
        stdout_text = (stdout or "").strip()
        raw = (stdout_text + ("\n" + stderr if stderr else "")).strip()
        file_reply = ""
        if output_path:
            # codex --output-last-message may still be flushing when proc.wait()
            # returns: poll until the size is stable across two 100ms reads
            # (max ~1s) before trusting the content.
            try:
                _of = Path(output_path)
                _prev = -1
                for _ in range(10):
                    _size = _of.stat().st_size if _of.exists() else 0
                    if _size > 0 and _size == _prev:
                        break
                    _prev = _size
                    time.sleep(0.1)
                file_reply = _of.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                file_reply = ""
        reply_source = stdout_text or raw
        adapter = "claude" if agent == "clau" else ("hermes" if agent == "hermes" else agent)
        reply, reasoning_capture, transport_noise = _council_parse_reply(reply_source, prompt, adapter)
        file_clean = ""
        if file_reply:
            file_clean, file_reasoning, file_noise = _council_parse_reply(file_reply, prompt, agent)
            reply = file_clean or reply
            reasoning_capture = file_reasoning or reasoning_capture
            transport_noise = "\n".join(x for x in (transport_noise, file_noise) if x)
        # V15 F2 (D1): file-channel workers (codex) that die before a trustworthy
        # final message get a structured incomplete marker, not transcript noise.
        reply = _council_apply_incomplete_fallback(
            reply, file_clean, file_reply, returncode, output_path, raw)
        reply = reply or "[no reply]"
        if agent == "clau" and not reasoning_capture:
            reasoning_capture = "[claude: no thinking/usage captured]"
        # ── Capture + persist provider-native session id (context mode only) ──
        captured_session_id = ""
        context_turns = 0
        if context_mode and returncode == 0:
            if agent == "clau":
                captured_session_id = resume_id or new_session_id
            elif agent == "ope":
                captured_session_id = _council_extract_opencode_session_id(stdout_text) or resume_id
            elif agent == "codo":
                captured_session_id = _council_extract_codex_thread_id(stdout_text) or resume_id
            elif agent == "kilo":
                captured_session_id = _council_extract_opencode_session_id(stdout_text) or resume_id
            elif agent == "pic":
                captured_session_id = _council_extract_pi_session_id(stdout_text) or resume_id
            else:
                captured_session_id = resume_id
            if captured_session_id:
                try:
                    entry = _council_set_context(
                        agent, project_path, captured_session_id, provider,
                        last_prompt=prompt, last_reply=reply,
                    )
                    context_turns = int(entry.get("turns", 0))
                except Exception:
                    pass
        elif not context_mode:
            _council_cleanup_broker_session_async(agent, raw, prompt, run_started_at)
        final_resolution, model_error = _council_finalize_model_resolution(
            resolution, raw, risk_level, dev_level, request_id=request_id,
            session_id=captured_session_id, returncode=returncode)
        result_ok = returncode == 0 and not model_error
        return {
            "ok": result_ok,
            "returncode": returncode,
            "reply": reply if result_ok else "",
            "reasoning": reasoning_capture if result_ok else "",
            "transport_noise": transport_noise[:_COUNCIL_REPLY_MAX],
            "response_tokens": _council_extract_response_tokens(agent, raw),
            "raw_preview": raw[:4000],
            "mode": "context" if context_mode else "broker",
            "provider": final_resolution.resolved_provider,
            "model": final_resolution.resolved_model,
            "model_resolution": final_resolution.as_dict(),
            "error_name": model_error,
            "error": model_error or "",
            "session_id": captured_session_id if result_ok else "",
            "context_turns": context_turns if result_ok else 0,
        }
    except subprocess.TimeoutExpired as exc:
        try:
            _orphan = locals().get("proc")
            if _orphan is not None and getattr(_orphan, "pid", 0):
                _council_kill_process_tree(_orphan.pid)
        except Exception:
            pass
        raw = ((exc.stdout or "") + (chr(10) + exc.stderr if exc.stderr else "")).strip()
        final_resolution, _ = _council_finalize_model_resolution(
            resolution, raw, risk_level, dev_level, request_id=request_id,
            session_id=locals().get("captured_session_id", ""), returncode=None)
        return {"ok": False, "returncode": None, "reply": _council_clean_timeout(raw),
                "raw_preview": raw[:4000], "error": "timeout",
                "provider": final_resolution.resolved_provider, "model": final_resolution.resolved_model,
                "model_resolution": final_resolution.as_dict()}
    except Exception as exc:
        fallback_resolution = locals().get("resolution")
        resolution_payload = (replace(fallback_resolution, resolution_status="unverified").as_dict()
                              if isinstance(fallback_resolution, CouncilModelResolution) else dict(model_resolution or {}))
        return {"ok": False, "returncode": None, "reply": "", "raw_preview": "", "error": str(exc),
                "model_resolution": resolution_payload}
    finally:
        if output_path:
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass
    return {}


def _council_cached(key: str, fetcher, ttl_seconds: float | None = None) -> dict:
    now = time.monotonic()
    ttl = _COUNCIL_METRICS_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    cached = _COUNCIL_METRICS_CACHE.get(key)
    if cached and now - cached[0] < ttl:
        return cached[1]
    try:
        result = fetcher()
        if not isinstance(result, dict):
            result = {}
    except Exception as exc:
        result = {"error": str(exc)}
    _COUNCIL_METRICS_CACHE[key] = (now, result)
    return result


_COUNCIL_DASHBOARD_PAYLOAD_TTL_SECONDS = float(
    os.environ.get("ORCHESTRATOR_COUNCIL_DASHBOARD_PAYLOAD_TTL_SEC", "1.5")
)
_COUNCIL_DASHBOARD_SNAPSHOT_CACHE = VersionedSnapshotCache(
    ttl_seconds=_COUNCIL_DASHBOARD_PAYLOAD_TTL_SECONDS,
    history_limit=max(4, int(os.environ.get(
        "ORCHESTRATOR_COUNCIL_SNAPSHOT_HISTORY", "64"))),
)
_COUNCIL_PUBLIC_BROKER_EXPORT_LOCK = threading.Lock()
_COUNCIL_PUBLIC_BROKER_EXPORTED_VERSIONS: dict[str, int] = {}
_COUNCIL_AGENT_SUPPORTED_FIELDS = {
    "id", "label", "status", "runtime", "caps", "context", "metrics_html",
    "metrics", "usage", "paused", "paused_at", "paused_reason", "live_log",
    "direct_log", "direct_log_path", "direct_log_mtime", "latest_msg",
    "latest_res", "reasoning", "live_raw", "model",
}
_COUNCIL_ACTIVITY_DEFAULT_FIELDS = {
    "id", "label", "status", "runtime", "paused", "paused_at", "paused_reason",
    "direct_log", "direct_log_path", "direct_log_mtime", "latest_res", "model",
}
_COUNCIL_STATE_DEFAULT_FIELDS = {
    "id", "label", "status", "runtime", "caps", "context", "metrics_html",
    "metrics", "usage", "paused", "paused_at", "paused_reason", "direct_log",
    "direct_log_path", "direct_log_mtime", "live_log", "latest_msg", "latest_res",
    "reasoning",
}


def _council_dashboard_payload_invalidate() -> None:
    _COUNCIL_DASHBOARD_SNAPSHOT_CACHE.invalidate()


def _council_dashboard_cache_key(key: str) -> str:
    return f"{key}:{_HERE}"


def _council_dashboard_source_revision() -> str:
    council_dir = _HERE / ".council"
    paths = [
        _council_runtime_file(),
        council_dir / "broker.jsonl",
        council_dir / "broker_filtered.jsonl",
    ]
    for agent in _COUNCIL_AGENTS:
        paths.extend((
            council_dir / f"log_{agent}.txt",
            council_dir / f"inbox_{agent}.md",
            council_dir / f"outbox_{agent}.md",
        ))
    revision = []
    for path in paths:
        try:
            stat = path.stat()
            revision.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            revision.append((str(path), 0, 0))
    return hashlib.sha256(repr(revision).encode("utf-8")).hexdigest()


def _council_cached_dashboard_payload(
    key: str,
    builder,
    ttl_seconds: float | None = None,
    *,
    fresh: bool = False,
    source_revision: str = "",
) -> dict:
    effective_ttl = (
        _COUNCIL_DASHBOARD_PAYLOAD_TTL_SECONDS
        if ttl_seconds is None else ttl_seconds
    )
    return _COUNCIL_DASHBOARD_SNAPSHOT_CACHE.get(
        _council_dashboard_cache_key(key),
        builder,
        generation=SERVER_GENERATION,
        ttl_seconds=effective_ttl,
        fresh=fresh,
        source_revision=source_revision,
        wait_timeout_seconds=1.0,
    )


def _council_snapshot_query(request) -> tuple[int, str]:
    since_version = max(0, _query_int(request, "since_version", 0))
    client_generation = str(request.query.get("generation", "") or "").strip()
    return since_version, client_generation


def _council_snapshot_headers(request, version: int) -> dict:
    return _cors(request.headers.get("Origin"), {
        "Cache-Control": "no-store, max-age=0",
        "X-Council-Version": str(max(0, int(version or 0))),
        "X-Orchestrator-Generation": SERVER_GENERATION,
    })


def _council_snapshot_response(request, selection, current_version: int):
    headers = _council_snapshot_headers(request, current_version)
    if selection.not_modified:
        return web.Response(status=304, headers=headers)
    return web.json_response(
        selection.payload,
        headers=headers,
        dumps=lambda value: json.dumps(value, ensure_ascii=False),
    )


def _council_parse_fields_param(raw: str, default_fields: set[str]) -> set[str]:
    if not raw:
        return set(default_fields)
    requested = {
        part.strip() for part in str(raw).split(",") if part.strip()
    }
    if not requested:
        return set(default_fields)
    return requested & _COUNCIL_AGENT_SUPPORTED_FIELDS


def _council_filter_agent_payload(payload: dict, fields: set[str], offset: int = 0, limit: int = 0) -> dict:
    out = dict(payload or {})
    agents = list(out.get("agents") or [])
    total = len(agents)
    offset = max(0, int(offset or 0))
    limit = max(0, int(limit or 0))
    if limit > 0:
        agents = agents[offset:offset + limit]
    elif offset > 0:
        agents = agents[offset:]
    keep = {"id", "label"} | fields
    out["agents"] = [
        {key: value for key, value in agent.items() if key in keep}
        for agent in agents if isinstance(agent, dict)
    ]
    out["total_agents"] = total
    out["offset"] = offset
    out["limit"] = limit if limit > 0 else None
    return out


# ─── Paused state (Task 5: play/pause per agent) ──────────────────────────────
# Stored in .council/paused_<agent>.json. The .json carries metadata so the
# UI can show "paused at HH:MM" + reason. Pause = kill + mark; play = clear.
# We DO NOT auto-resume the agent process — the user must relaunch it manually.
def _council_paused_file(council_dir: Path, agent: str) -> Path:
    return council_dir / f"paused_{agent}.json"


def _council_paused_info(council_dir: Path, agent: str) -> dict | None:
    pf = _council_paused_file(council_dir, agent)
    if not pf.exists():
        return None
    try:
        data = json.loads(pf.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _council_mark_paused(council_dir: Path, agent: str, info: dict) -> None:
    pf = _council_paused_file(council_dir, agent)
    pf.write_text(
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _council_clear_paused(council_dir: Path, agent: str) -> bool:
    pf = _council_paused_file(council_dir, agent)
    if pf.exists():
        try:
            pf.unlink()
            return True
        except Exception:
            return False
    return False


_COUNCIL_LAST_PURGE = [0.0]


def _council_purge_old(council_dir: Path, days: int = 7) -> int:
    """TTL auto-cleanup: delete time-stamped council session artifacts older than `days`
    so [A] keeps ~7 days of session data then self-cleans (avoids rubbish). Tooling,
    rules, worker_models.json, inbox/outbox templates are never matched."""
    cutoff = time.time() - days * 86400
    removed = 0
    for g in ("log_*.txt", "prompt_*.md", "run_*.txt", "run_*.bat", "run_*.ps1",
              "_p_*.json", "_payload*.json", "*.tmp", "snapshot_*.png", "backup_orchestrator_*.py"):
        try:
            for path in council_dir.glob(g):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except Exception:
                    pass
        except Exception:
            pass
    return removed


def _council_archive_session(council_dir: Path) -> tuple[str, list[str]]:
    meta = _council_ensure_session_meta(council_dir)
    session_id = str(meta.get("session_id") or _council_new_session_id())
    archive_dir = council_dir / "sessions" / session_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    for pattern in (_COUNCIL_EVENT_LOG, "broker_filtered.jsonl", "log_*.txt"):
        for path in council_dir.glob(pattern):
            if path.is_file():
                shutil.copy2(path, archive_dir / path.name)
                archived.append(path.name)
    ended_meta = dict(meta)
    ended_meta["session_id"] = session_id
    ended_meta["ended_at"] = _council_now_iso()
    _council_write_session_meta(archive_dir, ended_meta)
    return session_id, sorted(set(archived))


def _council_enforce_session_retention(council_dir: Path, keep: int = 3) -> int:
    sessions_dir = council_dir / "sessions"
    if not sessions_dir.exists():
        return 0
    try:
        dirs = [path for path in sessions_dir.iterdir() if path.is_dir()]
    except Exception:
        return 0
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except Exception:
            return 0.0
    dirs.sort(key=_mtime, reverse=True)
    removed = 0
    for path in dirs[max(0, keep):]:
        try:
            shutil.rmtree(path)
            removed += 1
        except Exception:
            pass
    return removed


def _council_clear_session_runtime(council_dir: Path) -> dict:
    """Clear Council UI/session data without touching provider-global histories."""
    council_dir.mkdir(parents=True, exist_ok=True)
    archived_session_id, archived_files = _council_archive_session(council_dir)
    removed_archives = _council_enforce_session_retention(council_dir, 3)
    patterns = (
        _COUNCIL_EVENT_LOG, "broker_filtered.jsonl", "log_*.txt",
        "inbox_*.md", "outbox_*.md",
    )
    files: dict[str, Path] = {}
    for pattern in patterns:
        for path in council_dir.glob(pattern):
            if path.is_file():
                files[str(path.resolve()).lower()] = path
    cleared: list[str] = []
    for path in files.values():
        path.write_text("", encoding="utf-8", newline="\n")
        cleared.append(path.name)
    removed_paused: list[str] = []
    for path in council_dir.glob("paused_*.json"):
        if path.is_file():
            path.unlink()
            removed_paused.append(path.name)
    # Broker-only cleanup: delete worker/system rubbish left by runs + scratch temps
    # so the clear button leaves no dangling files in .council.
    removed_rubbish: list[str] = []
    for rubbish_glob in ("prompt_*.md", "run_*.txt", "_p_*.json", "_payload*.json",
                         "_an*.js", "_corrupt_ref*.py", "*.tmp"):
        for path in council_dir.glob(rubbish_glob):
            if path.is_file():
                try:
                    path.unlink()
                    removed_rubbish.append(path.name)
                except Exception:
                    pass
    _COUNCIL_METRICS_CACHE.clear()
    # Bump the context-registry generation so any stale stored session id is dropped.
    new_generation = _council_bump_context_generation()
    new_meta = _council_write_session_meta(council_dir, {})
    return {
        "ok": True,
        "cleared_files": len(cleared) + len(removed_paused) + len(removed_rubbish),
        "truncated": sorted(cleared),
        "removed_paused": sorted(removed_paused),
        "removed_rubbish": sorted(removed_rubbish),
        "archived_session_id": archived_session_id,
        "archived_files": archived_files,
        "removed_archives": removed_archives,
        "session_id": new_meta.get("session_id", ""),
        "context_generation": new_generation,
        "preserved": ["provider-global sessions"],
    }


# ─── Structured usage payload (Task 3: JS reads numbers directly) ────────────
def _council_usage_struct(agent: str, metrics: dict, paused_info: dict | None) -> dict:
    """Build a structured usage payload for /api/council/state. The JS reads
    this directly to update progress bars / numbers without re-parsing HTML.
    Preserves the 'no fake numbers' rule: any missing field is null +
    available=False so the UI shows "—"."""
    p5h = metrics.get("plan_5h")
    p7d = metrics.get("plan_7d")
    r5h = metrics.get("reset_5h")
    r7d = metrics.get("reset_7d")
    ctx_used = metrics.get("tokens_session")
    ctx_total = metrics.get("context_tokens")
    ctx_pct = None
    if ctx_used is not None and ctx_total not in (None, "", 0):
        try:
            ctx_pct = max(0, min(100, int(round(float(ctx_used) / float(ctx_total) * 100))))
        except Exception:
            ctx_pct = None
    return {
        "agent": agent,
        "source": metrics.get("source"),
        "model": metrics.get("model"),
        "context": {
            "used": ctx_used,
            "total": ctx_total,
            "pct": ctx_pct,
            "available": (ctx_used is not None) or (ctx_total not in (None, "", 0)),
        },
        "plan_5h": {
            "pct": int(round(float(p5h))) if p5h is not None else None,
            "reset": r5h,
            "available": p5h is not None,
        },
        "plan_7d": {
            "pct": int(round(float(p7d))) if p7d is not None else None,
            "reset": r7d,
            "available": p7d is not None,
        },
        "tokens_session": ctx_used,
        "tokens_today": metrics.get("tokens_today"),
        "task_duration": metrics.get("task_duration"),
        "paused": bool(paused_info),
        "paused_at": (paused_info or {}).get("paused_at"),
        "paused_reason": (paused_info or {}).get("reason"),
    }


# ─── Agent PID discovery (Task 5: pause needs to know which PIDs to kill) ─────
def _council_find_agent_pids(agent: str) -> list[int]:
    """Return OS PIDs of processes that match the agent's signature.
    Reuses _COUNCIL_PROC_SIGNATURE logic but returns the actual PIDs (not
    just a set of agent names). Excludes shells/diagnostics to avoid
    self-tripping when the leader's verification command mentions the agent name."""
    needles = [t.lower() for t in _COUNCIL_PROC_SIGNATURE.get(agent, [])]
    if not needles:
        return []
    pids: list[int] = []
    try:
        import psutil
    except Exception:
        return pids
    shell_skip = {"powershell.exe", "pwsh.exe", "cmd.exe", "conhost.exe",
                  "windowsterminal.exe", "bash.exe", "sh.exe", "git.exe"}
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            info = proc.info
            pname = (info.get("name") or "").lower()
            if pname in shell_skip:
                continue
            hay = (pname + " " + " ".join(info.get("cmdline") or [])).lower()
        except Exception:
            continue
        if all(n in hay for n in needles):
            pids.append(int(proc.pid))
    return pids


def _council_http_json(url: str, headers: dict, timeout: int = 2) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(1024 * 1024)
    data = json.loads(raw.decode("utf-8", errors="replace"))
    return data if isinstance(data, dict) else {}


def _council_sum_tokenish(data) -> int | None:
    keys = {
        "tokens_used", "total_tokens", "totaltokens", "totalTokens", "inputTokens",
        "outputTokens", "cacheCreationTokens", "cacheReadTokens", "input_tokens",
        "output_tokens", "cached_input_tokens", "reasoning_output_tokens", "tokensIn",
        "tokensOut", "cacheWrites", "cacheReads", "total",
    }
    total = 0
    found = False
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and isinstance(value, (int, float)):
                total += int(value)
                found = True
            elif isinstance(value, (dict, list)):
                child = _council_sum_tokenish(value)
                if child is not None:
                    total += child
                    found = True
    elif isinstance(data, list):
        for item in data:
            child = _council_sum_tokenish(item)
            if child is not None:
                total += child
                found = True
    return total if found else None


def _council_metric_file(council_dir: Path, agent: str) -> dict:
    data = _council_read_json(council_dir / f"metrics_{agent}.json")
    if not isinstance(data, dict):
        data = {}
    duration = data.get("last_task_seconds", data.get("duration_seconds"))
    tokens = data.get("tokens_used")
    return {
        "duration": duration,
        "tokens": tokens,
        "status": data.get("status"),
        "dispatch_ts": data.get("dispatch_ts"),
        "done_ts": data.get("done_ts"),
    }


# ─── Local usage readers (Task 3: realtime Usage panel) ───────────────────────
# All readers are BOUNDED: file glob capped, lines capped, sqlite row cap.
# All cached in _COUNCIL_METRICS_CACHE via _council_cached(key, fetcher) with 60s TTL.
# No network. No fake numbers: missing field -> None. UI shows "—" for None.
# Per-agent source map matches RES id=012 feasibility research (outbox_ope.md).


def _council_local_clau_usage() -> dict:
    """Local clau usage from ~/.claude/projects/**/*.jsonl. Bounded: 5 newest files,
    last 200 lines each. Plan % NOT available locally; returns plan_available=False
    so the UI marks N/A. Caveat (RES 012): input_tokens in raw JSONL is a streaming
    placeholder (gille.ai benchmark). We sum input+output+cache_create+cache_read
    for a representative total; per-call precision is not promised."""
    def fetch() -> dict:
        result: dict = {
            "source": "claude-projects-jsonl",
            "available": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "tokens_session": 0,
            "sessions_scanned": 0,
            "plan_available": False,
        }
        try:
            base = Path.home() / ".claude" / "projects"
            if not base.exists():
                return result
            files = sorted(
                (p for p in base.glob("**/*.jsonl") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:5]
            for f in files:
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    lines = text.splitlines()[-200:]
                except Exception:
                    continue
                result["sessions_scanned"] += 1
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if entry.get("type") != "assistant":
                        continue
                    usage = (entry.get("message") or {}).get("usage") or {}
                    try:
                        result["input_tokens"] += int(usage.get("input_tokens") or 0)
                        result["output_tokens"] += int(usage.get("output_tokens") or 0)
                        result["cache_creation_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
                        result["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
                    except Exception:
                        pass
            result["tokens_session"] = (
                result["input_tokens"]
                + result["output_tokens"]
                + result["cache_creation_tokens"]
                + result["cache_read_tokens"]
            )
            result["available"] = True
        except Exception as exc:
            result["error"] = str(exc)
        return result
    return _council_cached("clau_local", fetch)


def _council_local_codo_usage() -> dict:
    """Local codo usage from ~/.codex/state_5.sqlite (threads.tokens_used aggregate)
    + last rollout JSONL's last token_count event for model_context_window + rate_limits.
    Bounded: 10 threads DESC, single latest rollout (last 400 lines) for last token_count."""
    def fetch() -> dict:
        result: dict = {
            "source": "codex-state5+rollout",
            "available": False,
            "tokens_session": 0,
            "tokens_today": 0,
            "model": None,
            "model_context_window": None,
            "plan_5h": None,
            "reset_5h": None,
            "plan_7d": None,
            "reset_7d": None,
            "plan_available": False,
            "threads_scanned": 0,
        }
        try:
            db_path = Path.home() / ".codex" / "state_5.sqlite"
            tokens_total = 0
            state_mtime = 0.0
            if db_path.exists():
                try:
                    state_mtime = db_path.stat().st_mtime
                except Exception:
                    state_mtime = 0.0
                try:
                    import sqlite3 as _sq
                    with _sq.connect(str(db_path), timeout=2) as conn:
                        row = conn.execute(
                            "SELECT COALESCE(SUM(tokens_used),0) FROM ("
                            "SELECT tokens_used FROM threads ORDER BY created_at DESC LIMIT 10)"
                        ).fetchone()
                        if row and row[0]:
                            tokens_total = int(row[0])
                        cnt = conn.execute("SELECT COUNT(*) FROM threads").fetchone()
                        result["threads_scanned"] = min(10, int(cnt[0]) if cnt else 0)
                except Exception:
                    pass
            result["tokens_session"] = tokens_total
            result["tokens_today"] = tokens_total

            sessions_dir = Path.home() / ".codex" / "sessions"
            if sessions_dir.exists():
                try:
                    rollouts = sorted(
                        (p for p in sessions_dir.glob("**/rollout-*.jsonl") if p.is_file()),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                except Exception:
                    rollouts = []
                if rollouts:
                    try:
                        rollout = rollouts[0]
                        rollout_mtime = rollout.stat().st_mtime
                        if state_mtime and rollout_mtime < state_mtime:
                            lines = []
                            result["source"] = "codex-state5"
                        else:
                            text = rollout.read_text(encoding="utf-8", errors="replace")
                            lines = text.splitlines()[-400:]
                    except Exception:
                        lines = []
                    for line in reversed(lines):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except Exception:
                            continue
                        if entry.get("type") != "event_msg":
                            continue
                        payload = entry.get("payload") or {}
                        if payload.get("type") != "token_count":
                            continue
                        info = payload.get("info") or {}
                        total_usage = info.get("total_token_usage") or {}
                        total_tokens = total_usage.get("total_tokens")
                        if total_tokens is not None:
                            try:
                                tokens_total = int(total_tokens)
                                result["tokens_session"] = tokens_total
                                result["tokens_today"] = tokens_total
                            except Exception:
                                pass
                        mcw = info.get("model_context_window")
                        if mcw is not None:
                            try:
                                result["model_context_window"] = int(mcw)
                            except Exception:
                                pass
                        rl = payload.get("rate_limits") or {}
                        pri = rl.get("primary") or {}
                        sec = rl.get("secondary") or {}
                        if pri.get("used_percent") is not None:
                            try:
                                result["plan_5h"] = float(pri["used_percent"])
                            except Exception:
                                pass
                            if pri.get("reset_at") is not None:
                                result["reset_5h"] = pri["reset_at"]
                        if sec.get("used_percent") is not None:
                            try:
                                result["plan_7d"] = float(sec["used_percent"])
                            except Exception:
                                pass
                            if sec.get("reset_at") is not None:
                                result["reset_7d"] = sec["reset_at"]
                        if rl.get("plan_type"):
                            result["model"] = str(rl["plan_type"])
                        if result["plan_5h"] is not None or result["plan_7d"] is not None:
                            result["plan_available"] = True
                        break
            result["available"] = True
        except Exception as exc:
            result["error"] = str(exc)
        return result
    return _council_cached("codo_local", fetch, _COUNCIL_CODO_METRICS_TTL_SECONDS)


def _council_local_ope_usage() -> dict:
    """Local ope usage from ~/.local/share/opencode/opencode.db session table.
    Bounded: 10 most recent sessions DESC. NO plan: opencode routes to provider."""
    def fetch() -> dict:
        result: dict = {
            "source": "opencode-db-session",
            "available": False,
            "tokens_input": 0,
            "tokens_output": 0,
            "tokens_reasoning": 0,
            "tokens_cache_read": 0,
            "tokens_cache_write": 0,
            "tokens_session": 0,
            "model": None,
            "sessions_scanned": 0,
            "plan_available": False,
        }
        try:
            db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
            if not db_path.exists():
                return result
            import sqlite3 as _sq
            with _sq.connect(str(db_path), timeout=2) as conn:
                rows = conn.execute(
                    "SELECT COALESCE(tokens_input,0), COALESCE(tokens_output,0),"
                    " COALESCE(tokens_reasoning,0), COALESCE(tokens_cache_read,0),"
                    " COALESCE(tokens_cache_write,0), model, time_updated"
                    " FROM session ORDER BY time_updated DESC LIMIT 10"
                ).fetchall()
                for r in rows:
                    result["tokens_input"] += int(r[0] or 0)
                    result["tokens_output"] += int(r[1] or 0)
                    result["tokens_reasoning"] += int(r[2] or 0)
                    result["tokens_cache_read"] += int(r[3] or 0)
                    result["tokens_cache_write"] += int(r[4] or 0)
                    if not result["model"] and r[5]:
                        result["model"] = str(r[5])
                    result["sessions_scanned"] += 1
            result["tokens_session"] = (
                result["tokens_input"]
                + result["tokens_output"]
                + result["tokens_reasoning"]
                + result["tokens_cache_read"]
                + result["tokens_cache_write"]
            )
            result["available"] = True
        except Exception as exc:
            result["error"] = str(exc)
        return result
    return _council_cached("ope_local", fetch)


def _council_local_retired_vscode_usage() -> dict:
    """Retired VS Code chat usage reader kept only for old debug references.
    Sums result.metadata.promptTokens + outputTokens via regex (the file is a
    structured K/V record store, not flat JSON). Bounded: 10 newest files,
    capped head+tail bytes each. NO plan: external backend plan%
    needs GitHub API call (out of scope for local-only)."""
    def fetch() -> dict:
        result: dict = {
            "source": "retired-vscode-emptyWindowChatSessions",
            "available": False,
            "tokens_session": 0,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "sessions_scanned": 0,
            "plan_available": False,
        }
        try:
            import os as _os
            appdata = _os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
            base = Path(appdata) / "Code" / "User" / "globalStorage" / "emptyWindowChatSessions"
            if not base.exists():
                return result
            files = sorted(
                (p for p in base.glob("*.jsonl") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:10]
            for f in files:
                try:
                    raw = f.read_bytes()
                    if len(raw) > 4_000_000:
                        raw = raw[:2_000_000] + b"\n" + raw[-2_000_000:]
                    text = raw.decode("utf-8", errors="replace")
                    lines = text.splitlines()
                except Exception:
                    continue
                result["sessions_scanned"] += 1
                for line in lines:
                    p_match = re.search(r'"promptTokens"\s*:\s*(\d+)', line)
                    o_match = re.search(r'"outputTokens"\s*:\s*(\d+)', line)
                    if p_match:
                        try:
                            result["prompt_tokens"] += int(p_match.group(1))
                        except Exception:
                            pass
                    if o_match:
                        try:
                            result["output_tokens"] += int(o_match.group(1))
                        except Exception:
                            pass
            result["tokens_session"] = result["prompt_tokens"] + result["output_tokens"]
            result["available"] = True
        except Exception as exc:
            result["error"] = str(exc)
        return result
    return _council_cached("retired_vscode_local", fetch)


# Council liveness via live OS process (cmdline signature) — NOT a manual metrics flag.
# Each CLI agent is a real process; ALL listed substring tokens must appear in name+cmdline (AND).
# clau is fuzzy by nature (process-scan tradeoff the user accepted).
_COUNCIL_PROC_SIGNATURE = {
    "clau": ["claude"],
    "ope":  ["opencode"],
    "codo": ["codex"],
    "pic": ["pi-coding-agent"],
    "hermes": ["hermes"],
    "kilo": ["kilo"],
    "gro": ["grok.exe"],
}


def _council_alive_agents(active_agents=None) -> set:
    """Cmdline fallback restricted to agents that already have an active runtime slot."""
    eligible = set(active_agents or ())
    if not eligible:
        return set()
    alive = set()
    try:
        import psutil
    except Exception:
        return alive
    sigs = {a: [t.lower() for t in _COUNCIL_PROC_SIGNATURE.get(a, ())]
            for a in eligible if a in _COUNCIL_PROC_SIGNATURE}
    shell_skip = {"powershell.exe", "pwsh.exe", "cmd.exe", "conhost.exe",
                  "windowsterminal.exe", "bash.exe", "sh.exe", "git.exe"}
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            info = proc.info
            pname = (info.get("name") or "").lower()
            if pname in shell_skip:
                continue
            hay = (pname + " " + " ".join(info.get("cmdline") or [])).lower()
        except Exception:
            continue
        for agent, needles in sigs.items():
            if agent not in alive and needles and all(token in hay for token in needles):
                alive.add(agent)
    return alive



def _council_direct_log_info(council_dir: Path, agent: str, lines: int = 80) -> dict:
    log_path = _council_newest_agent_log(council_dir, agent)
    if log_path is None:
        return {"text": "", "path": "", "mtime": 0.0}
    max_bytes = max(65_536, min(2_000_000, max(1, int(lines)) * 16_384))
    tail_read = read_tail_bytes(log_path, max_bytes)
    if not tail_read.data and tail_read.size <= 0:
        return {"text": "", "path": log_path.name, "mtime": _council_file_mtime(log_path)}
    text = _council_decode_log_bytes(tail_read.data)
    rows = text.splitlines()
    tail = "\n".join(reversed(rows[-lines:]))
    # Bounded, ANSI-stripped, chronological tail. Used only for display/liveness.
    raw_tail = "\n".join(_strip_ansi(text).splitlines()[-30:]).strip()[:2000]
    return {
        "text": tail,
        "clean_text": _council_clean_direct_log_text(agent, text, lines),
        "raw_tail": raw_tail,
        "path": log_path.name,
        "mtime": tail_read.mtime_ns / 1_000_000_000 if tail_read.mtime_ns else 0.0,
        "size": tail_read.size,
        "tail_truncated": tail_read.truncated,
    }


def _council_direct_log_events(council_dir: Path, limit: int = 40) -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for agent in _COUNCIL_AGENTS:
        try:
            candidates = [p for p in council_dir.glob(f"log_{agent}*.txt") if p.is_file()]
        except Exception:
            candidates = []
        for path in sorted(candidates, key=_council_file_mtime, reverse=True)[:6]:
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            tail = read_tail_bytes(path, 2_000_000)
            if not tail.data:
                continue
            # Dashboard polling must never scale with the full historical log.
            decoded = _council_decode_log_bytes(tail.data)
            rows = [ln for ln in decoded.splitlines() if ln.strip()]
            excerpt = _council_clean_direct_log_text(agent, decoded, 12)
            if not excerpt:
                excerpt = "\n".join(rows[-12:]).strip()
            if not excerpt:
                continue
            mtime = _council_file_mtime(path)
            events.append({
                "type": "direct_log",
                "from": agent,
                "to": "direct-log",
                "text": excerpt[:3000],
                "path": path.name,
                "mtime": mtime,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)) if mtime else _council_now_iso(),
                "id": f"direct-{agent}-{path.stem}-{int(mtime * 1000)}",
            })
    events.sort(key=lambda ev: float(ev.get("mtime") or 0.0))
    return events[-max(0, limit):]


def _council_public_events(council_dir: Path, limit: int = 80) -> list[dict]:
    """Return the same compact, filtered transcript exposed by the broker UI."""
    allowed = {
        "type", "from", "to", "text", "error", "ts", "mtime", "id",
        "in_reply_to", "ok", "returncode", "path",
        "mode", "provider", "model", "session_id", "context_turns",
    }
    combined = _council_read_events(limit) + _council_direct_log_events(council_dir, limit)
    events = sorted(
        combined,
        key=lambda ev: (_council_event_sort_time(ev), str(ev.get("ts", "")), str(ev.get("id", ""))),
    )[-max(0, limit):]
    public = []
    for event in events:
        item = {key: event[key] for key in allowed if key in event}
        if "from" in item:
            item["from_label"] = _council_broker_party_label(item["from"], "from")
        if "to" in item:
            item["to_label"] = _council_broker_party_label(item["to"], "to")
        public.append(item)
    return public


def _council_broker_party_label(value, role: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return "?"
    low = text.lower()
    if role == "from" and low == "clau":
        return "Clau"
    if low in _COUNCIL_AGENT_LABELS:
        return _COUNCIL_AGENT_LABELS.get(low, text)
    if low == "anh":
        return "Anh"
    return text


def _council_public_events_all(council_dir: Path) -> list[dict]:
    """Return the full current-session broker event log with public fields only."""
    allowed = {"ts", "id", "from", "to", "type", "ok", "text", "in_reply_to", "provider", "model"}
    path = council_dir / _COUNCIL_EVENT_LOG
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for line in lines:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            events.append({key: item[key] for key in allowed if key in item})
    return events


_COUNCIL_EXPORT_LOCK = threading.Lock()


def _council_export_public_broker(council_dir: Path, events: list[dict] | None = None) -> Path:
    """Mirror the filtered broker transcript to broker_filtered.jsonl.
    When the caller already built the public event list (e.g. /api/council/events,
    which merges direct_log entries), export THAT list so file and payload match.

    Race-hardened (fix: 37x PermissionError/FileNotFoundError in orchestrator.log):
    the dashboard polls /api/council/events every 3s from the daemon while clauC's
    MCP stdio process may export concurrently. A fixed .tmp name caused rename
    collisions between processes. Now: per-process/thread unique tmp name +
    intra-process lock + bounded retry on the cross-process WinError 5/32 rename;
    a lost retry only skips ONE mirror refresh (next poll rewrites it)."""
    path = council_dir / "broker_filtered.jsonl"
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    if events is None:
        events = _council_public_events_all(council_dir)
    # allow_nan=False: a float('nan') smuggled into an event would serialize as
    # bare NaN (invalid JSON) and silently corrupt the mirror for every consumer.
    try:
        body = "".join(json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
                       for event in events)
    except ValueError:
        body = "".join(json.dumps(_council_json_sanitize(event), ensure_ascii=False, separators=(",", ":")) + "\n"
                       for event in events)
    with _COUNCIL_EXPORT_LOCK:
        try:
            tmp.write_text(body, encoding="utf-8", newline="\n")
            for attempt in range(4):
                try:
                    os.replace(tmp, path)
                    break
                except (PermissionError, FileNotFoundError, OSError):
                    if attempt == 3:
                        # Reader holding the target open (Windows shares without
                        # FILE_SHARE_DELETE) can outlast every retry. The mirror
                        # must not silently stale: degrade to a direct write —
                        # non-atomic, but content is a rebuildable projection.
                        try:
                            path.write_text(body, encoding="utf-8", newline="\n")
                        except Exception:
                            logger.warning("broker export skipped (rename+write contention): %s", path.name)
                        break
                    time.sleep(0.05 * (attempt + 1))
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    return path


def _council_export_public_broker_if_new(
    cache_key: str,
    council_dir: Path,
    events: list[dict],
    version: int,
) -> bool:
    """Write each derived broker view at most once per event snapshot version."""
    snapshot_key = str(cache_key)
    snapshot_version = max(0, int(version or 0))
    with _COUNCIL_PUBLIC_BROKER_EXPORT_LOCK:
        if (
            _COUNCIL_PUBLIC_BROKER_EXPORTED_VERSIONS.get(snapshot_key)
            == snapshot_version
        ):
            return False
        _council_export_public_broker(council_dir, events)
        _COUNCIL_PUBLIC_BROKER_EXPORTED_VERSIONS[snapshot_key] = snapshot_version
        return True


def _council_json_sanitize(obj):
    """Replace NaN/Infinity floats with None so the broker mirror stays valid JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _council_json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_council_json_sanitize(v) for v in obj]
    return obj


def _council_agent_status(agent: str, paused: dict | None = None) -> dict:
    if paused:
        return {"label": "paused", "class": "paused"}
    runtime = _council_runtime_status(agent)
    state = runtime.get("state", "idle")
    labels = {"running": "working", "completed": "completed", "timeout": "timeout", "error": "error", "stopped": "stopped"}
    return {"label": labels.get(state, "idle"), "class": state}


def _council_clau_metrics() -> dict:
    """Usage: local ~/.claude/projects JSONL (per RES 012).
    Plan: cached api.anthropic.com /api/oauth/usage (60s TTL; best-effort).
    UI shows N/A if the plan call fails (network/429 lockout)."""
    local = _council_local_clau_usage()

    def fetch_plan() -> dict:
        cred = _council_read_json(Path.home() / ".claude" / ".credentials.json")
        token = (cred.get(".claudeAiOauth") or cred.get("claudeAiOauth") or {}).get("accessToken")
        if not token:
            return {}
        return _council_http_json(
            "https://api.anthropic.com/api/oauth/usage",
            {
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
        )

    plan = _council_cached("clau_plan", fetch_plan) or {}
    plan_ok = bool(plan.get("five_hour") or plan.get("seven_day"))
    five = plan.get("five_hour") or {}
    seven = plan.get("seven_day") or {}
    return {
        "plan_5h": five.get("utilization") if plan_ok else None,
        "reset_5h": five.get("resets_at") if plan_ok else None,
        "plan_7d": seven.get("utilization") if plan_ok else None,
        "reset_7d": seven.get("resets_at") if plan_ok else None,
        "remaining": None if not plan_ok or five.get("utilization") is None else 100 - float(five.get("utilization")),
        "tokens_session": local.get("tokens_session"),
        "tokens_today": local.get("tokens_session"),
        "context_percent": None,
        "context_tokens": None,
        "model": None,
        "source": local.get("source"),
    }


def _council_codo_metrics() -> dict:
    """Usage + plan + model_context_window from local state_5.sqlite + last rollout
    token_count event (per RES 012). rate_limits already in token_count payload,
    so NO network call. Falls back to N/A only if rollout/scan fails."""
    local = _council_local_codo_usage()
    primary = local.get("plan_5h")
    secondary = local.get("plan_7d")
    return {
        "plan_5h": primary,
        "reset_5h": local.get("reset_5h"),
        "plan_7d": secondary,
        "reset_7d": local.get("reset_7d"),
        "remaining": None if primary is None else 100 - float(primary),
        "tokens_session": local.get("tokens_session"),
        "tokens_today": local.get("tokens_today"),
        "context_percent": None,
        "context_tokens": local.get("model_context_window"),
        "model": local.get("model"),
        "source": local.get("source"),
    }


def _council_secondary_metrics(agent: str) -> dict:
    """ope: dispatch to per-agent local readers (RES 012).
    plan = N/A (no local rate-limit source for ope)."""
    if agent == "ope":
        local = _council_local_ope_usage()
    else:
        local = {"available": False, "tokens_session": None, "source": "unknown"}
    return {
        "plan_5h": None,
        "reset_5h": None,
        "plan_7d": None,
        "reset_7d": None,
        "remaining": None,
        "tokens_session": local.get("tokens_session"),
        "tokens_today": local.get("tokens_today"),
        "context_percent": None,
        "context_tokens": None,
        "model": local.get("model"),
        "source": local.get("source"),
    }


def _council_agent_metrics(council_dir: Path, agent: str) -> dict:
    if agent == "clau":
        metrics = _council_clau_metrics()
    elif agent == "codo":
        metrics = _council_codo_metrics()
    else:
        metrics = _council_secondary_metrics(agent)
    sidecar = _council_metric_file(council_dir, agent)
    if sidecar.get("tokens") not in (None, ""):
        metrics["tokens_session"] = sidecar["tokens"]
    metrics["task_duration"] = sidecar.get("duration")
    return metrics


def _council_int_pct(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        pct = int(round(float(value)))
    except Exception:
        return None
    return max(0, min(100, pct))


def _council_format_tokens(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        n = float(value)
    except Exception:
        return "—"
    if n < 0:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    if n >= 1:
        return f"{int(n)}"
    return f"{n:.1f}"


def _council_format_reset(value) -> str:
    if value is None or value == "":
        return "—"
    try:
        if isinstance(value, (int, float)):
            reset_ts = float(value)
        else:
            text = str(value).strip()
            if text.isdigit():
                reset_ts = float(text)
            else:
                reset_ts = __import__('datetime').datetime.fromisoformat(
                    text.replace("Z", "+00:00")
                ).timestamp()
    except Exception:
        return "—"
    hours = max(0.0, (reset_ts - time.time()) / 3600.0)
    if hours < 1.0 / 60.0:
        return "now"
    if hours >= 48:
        return f"{int(hours / 24)}d"
    if hours >= 1.5:
        return f"{int(round(hours))}h"
    return f"{max(1, int(round(hours)))}h"


def _council_progress_bar(pct: int | None) -> str:
    if pct is None:
        return '<div class="usage-bar empty" aria-label="no data"><div class="usage-bar-fill" style="width:0%"></div></div>'
    return f'<div class="usage-bar" role="progressbar" aria-valuenow="{pct}" aria-valuemin="0" aria-valuemax="100"><div class="usage-bar-fill" style="width:{pct}%"></div></div>'


def _council_metrics_table(agent: str, metrics: dict) -> str:
    esc = __import__("html").escape
    ctx_used = metrics.get("tokens_session")
    ctx_total = metrics.get("context_tokens")
    ctx_pct = _council_int_pct(metrics.get("context_percent"))
    if ctx_pct is None and ctx_used is not None and ctx_total not in (None, "", 0):
        try:
            ctx_pct = max(0, min(100, int(round(float(ctx_used) / float(ctx_total) * 100))))
        except Exception:
            ctx_pct = None
    if ctx_pct is not None and ctx_used is not None and ctx_total not in (None, ""):
        ctx_right = f"{_council_format_tokens(ctx_used)} / {_council_format_tokens(ctx_total)} ({ctx_pct}%)"
    elif ctx_pct is not None and ctx_used is not None:
        ctx_right = f"{_council_format_tokens(ctx_used)} ({ctx_pct}%)"
    elif ctx_used is not None:
        ctx_right = _council_format_tokens(ctx_used)
    else:
        ctx_right = "—"
    ctx_bar = _council_progress_bar(ctx_pct)
    runtime_state = str(metrics.get("runtime_state") or "unknown")
    status_row = ""
    if "runtime_state" in metrics:
        status_row = (
            f'<div class="usage-row"><span class="usage-label">Status</span>'
            f'<span class="usage-value">{esc(runtime_state)}</span></div>'
        )

    p5h = _council_int_pct(metrics.get("plan_5h"))
    r5h = _council_format_reset(metrics.get("reset_5h"))
    p5h_right = f"{p5h}% · resets {r5h}" if p5h is not None else "—"
    p5h_bar = _council_progress_bar(p5h)

    p7d = _council_int_pct(metrics.get("plan_7d"))
    r7d = _council_format_reset(metrics.get("reset_7d"))
    p7d_right = f"{p7d}% · resets {r7d}" if p7d is not None else "—"
    p7d_bar = _council_progress_bar(p7d)

    duration = metrics.get("task_duration")
    if duration in (None, ""):
        effort = "—"
    else:
        try:
            effort = f"{int(round(float(duration)))}s"
        except Exception:
            effort = "—"

    _set_model = _council_worker_model(agent, "", "")[1] or ""
    return (
        f'<div class="usage-card" data-agent="{esc(agent)}">'
        f'{status_row}'
        f'<div class="usage-row"><span class="usage-label">Context window</span><span class="usage-value">{ctx_right}</span></div>'
        f'{ctx_bar}'
        f'<div class="usage-section">Plan usage</div>'
        f'<div class="usage-row"><span class="usage-label">5-hour limit</span><span class="usage-value">{p5h_right}</span></div>'
        f'{p5h_bar}'
        f'<div class="usage-row"><span class="usage-label">Weekly · all models</span><span class="usage-value">{p7d_right}</span></div>'
        f'{p7d_bar}'
        f'<div class="usage-footer"><span class="usage-model" data-field="model">{esc(_set_model)}</span><span class="usage-effort">· {effort}</span></div>'
        f'</div>'
    )


def _council_usage_reasoning_text(agent: str, metrics: dict) -> str:
    label = _COUNCIL_AGENT_LABELS.get(agent, agent)
    lines = [f"[usage {label}]"]
    source = metrics.get("source")
    model = metrics.get("model")
    if source:
        lines.append(f"source: {source}")
    if model:
        lines.append(f"model: {model}")
    tokens = metrics.get("tokens_session")
    today = metrics.get("tokens_today")
    context = metrics.get("context_tokens")
    if tokens is not None:
        if context not in (None, "", 0):
            lines.append(f"tokens_session: {_council_format_tokens(tokens)} / {_council_format_tokens(context)}")
        else:
            lines.append(f"tokens_session: {_council_format_tokens(tokens)}")
    if today is not None:
        lines.append(f"tokens_today: {_council_format_tokens(today)}")
    p5h = _council_int_pct(metrics.get("plan_5h"))
    p7d = _council_int_pct(metrics.get("plan_7d"))
    if p5h is not None:
        lines.append(f"plan_5h: {p5h}% reset {_council_format_reset(metrics.get('reset_5h'))}")
    if p7d is not None:
        lines.append(f"plan_7d: {p7d}% reset {_council_format_reset(metrics.get('reset_7d'))}")
    duration = metrics.get("task_duration")
    if duration not in (None, ""):
        try:
            lines.append(f"task_duration: {int(round(float(duration)))}s")
        except Exception:
            pass
    if len(lines) == 1:
        lines.append("tokens_session: —")
    return "\n".join(lines)


def _council_response_tokens_text(tokens: dict) -> str:
    if not tokens:
        return ""
    lines = ["[response tokens]"]
    for key in ("input", "output", "reasoning", "total"):
        if tokens.get(key) not in (None, ""):
            lines.append(f"{key}: {_council_format_tokens(tokens[key])}")
    return "\n".join(lines)


def _council_compose_reasoning(
    agent: str, metrics: dict, broker_raw: str, direct_clean: str, response_tokens: dict | None = None,
) -> str:
    parts = [_council_usage_reasoning_text(agent, metrics)]
    response_usage = _council_response_tokens_text(response_tokens or {})
    if response_usage:
        parts.append(response_usage)
    raw = _council_clean_broker_raw(agent, broker_raw)
    if raw:
        parts.append("[broker raw]\n" + raw)
    elif direct_clean:
        parts.append("[direct log]\n" + direct_clean)
    return "\n\n".join(parts)[:5000]


def _council_state_payload(fields: set[str] | None = None) -> dict:
    selected = set(fields or _COUNCIL_STATE_DEFAULT_FIELDS)
    return {
        "generated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "agents": _council_build_agent_payloads(selected),
        "context_generation": _council_context_generation(),
    }


_COUNCIL_LIVE_RAW_MAX_BYTES = 240000  # ~30x the original 8 KB so verbose CLIs
# (claude stream-json ~15 KB+ with reasoning/usage/pricing) are shown whole, head
# included, instead of a tail that drops the [ARGV] header + early output.


def _council_live_raw_tail(agent: str, max_bytes: int = _COUNCIL_LIVE_RAW_MAX_BYTES) -> str:
    """Bounded literal tail of the per-agent raw CLI capture."""
    try:
        path = _HERE / ".council" / f"live_raw_{agent}.log"
        tail_read = read_tail_bytes(path, max_bytes)
        if not tail_read.data:
            return ""
        prefix = b"...[truncated]...\n" if tail_read.truncated else b""
        return (prefix + tail_read.data).decode("utf-8", "replace")
    except Exception:
        return ""


def _council_build_agent_payloads(fields: set[str]) -> list[dict]:
    """Build only requested agent fields; expensive logs/metrics stay lazy."""
    council_dir = _HERE / ".council"
    fields = set(fields)
    agents: list[dict] = []
    need_paused = bool(fields & {
        "paused", "paused_at", "paused_reason", "status", "usage",
    })
    need_runtime = bool(fields & {
        "runtime", "status", "metrics", "metrics_html", "usage",
    })
    need_direct = bool(fields & {
        "live_log", "direct_log", "direct_log_path", "direct_log_mtime",
        "latest_res", "reasoning",
    })
    need_broker = bool(fields & {"latest_res", "reasoning"})
    need_metrics = bool(fields & {"metrics", "metrics_html", "usage", "reasoning"})

    for agent in _COUNCIL_AGENTS:
        item = {
            "id": agent,
            "label": _COUNCIL_AGENT_LABELS.get(agent, agent),
        }
        paused_info = _council_paused_info(council_dir, agent) if need_paused else None
        runtime = _council_runtime_status(agent) if need_runtime else {}
        direct_log = (
            _council_direct_log_info(council_dir, agent, 80)
            if need_direct else {}
        )
        direct_clean = str(direct_log.get("clean_text", ""))
        direct_display = direct_clean or str(direct_log.get("raw_tail", ""))

        broker_event = _council_latest_broker_event(agent) if need_broker else {}
        broker_is_fresh = bool(
            broker_event and
            _council_event_sort_time(broker_event)
            >= float(direct_log.get("mtime", 0.0) or 0.0)
        )
        metrics = _council_agent_metrics(council_dir, agent) if need_metrics else {}
        if need_metrics and agent in ("clau", "codo"):
            metrics = dict(metrics)
            metrics["runtime_state"] = runtime.get("state") or "unknown"

        if "status" in fields:
            item["status"] = _council_agent_status(agent, paused_info)
        if "runtime" in fields:
            item["runtime"] = runtime
        if "caps" in fields:
            item["caps"] = _council_agent_caps(agent)
        if "context" in fields:
            item["context"] = _council_context_status(agent)
        if "metrics_html" in fields:
            item["metrics_html"] = _council_metrics_table(agent, metrics)
        if "metrics" in fields:
            item["metrics"] = metrics
        if "usage" in fields:
            item["usage"] = _council_usage_struct(agent, metrics, paused_info)
        if "paused" in fields:
            item["paused"] = bool(paused_info)
        if "paused_at" in fields:
            item["paused_at"] = (paused_info or {}).get("paused_at")
        if "paused_reason" in fields:
            item["paused_reason"] = (paused_info or {}).get("reason")
        if "live_log" in fields:
            item["live_log"] = str(direct_log.get("text", ""))
        if "direct_log" in fields:
            item["direct_log"] = direct_display
        if "direct_log_path" in fields:
            item["direct_log_path"] = direct_log.get("path", "")
        if "direct_log_mtime" in fields:
            item["direct_log_mtime"] = direct_log.get("mtime", 0.0)
        if "latest_msg" in fields:
            inbox_text = _council_read_text(council_dir / f"inbox_{agent}.md")
            latest_msg = _council_latest_block(inbox_text, "MSG")
            latest_prompt = _council_latest_prompt_source(council_dir, agent)
            item["latest_msg"] = latest_prompt or latest_msg
        if "latest_res" in fields:
            outbox_text = _council_read_text(council_dir / f"outbox_{agent}.md")
            latest_res = _council_resolve_latest_res(council_dir, agent, outbox_text)
            broker_res = _council_latest_broker_reply(agent)
            item["latest_res"] = broker_res if broker_res and broker_is_fresh else latest_res
        if "reasoning" in fields:
            item["reasoning"] = _council_compose_reasoning(
                agent,
                metrics,
                _council_latest_broker_raw(agent) if broker_is_fresh else "",
                direct_clean,
                broker_event.get("response_tokens") if broker_is_fresh else {},
            )
        if "live_raw" in fields:
            item["live_raw"] = _council_live_raw_tail(agent)
        if "model" in fields:
            item["model"] = _council_worker_model(agent, "", "")[1]
        agents.append(item)
    return agents


def _council_activity_payload(fields: set[str] | None = None) -> dict:
    selected = set(fields or _COUNCIL_ACTIVITY_DEFAULT_FIELDS)
    return {
        "generated_at": time.strftime('%Y-%m-%d %H:%M:%S'),
        "agents": _council_build_agent_payloads(selected),
    }


async def handle_council_state(request):
    """GET /api/council/state - versioned localhost-only state + usage JSON."""
    cors = _cors(request.headers.get("Origin"))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"error": "blocked"}, status=403, headers=cors)
    fields = _council_parse_fields_param(
        request.query.get("fields", ""), _COUNCIL_STATE_DEFAULT_FIELDS)
    offset = max(0, _query_int(request, "offset", 0))
    limit = max(0, _query_int(request, "limit", 0))
    cache_key = (
        "state:" + ",".join(sorted(fields)) + f":{offset}:{limit}"
    )
    since_version, client_generation = _council_snapshot_query(request)
    source_revision = await asyncio.to_thread(_council_dashboard_source_revision)
    payload = await asyncio.to_thread(
        _council_cached_dashboard_payload,
        cache_key,
        lambda: _council_filter_agent_payload(
            _council_state_payload(fields), fields, offset, limit),
        fresh=since_version > 0,
        source_revision=source_revision,
    )
    selection = await asyncio.to_thread(
        _COUNCIL_DASHBOARD_SNAPSHOT_CACHE.select,
        _council_dashboard_cache_key(cache_key),
        payload,
        since_version=since_version,
        client_generation=client_generation,
        generation=SERVER_GENERATION,
        collection_field="agents",
        id_field="id",
    )
    return _council_snapshot_response(
        request, selection, int(payload.get("version", 0) or 0))


async def handle_council_activity(request):
    """GET /api/council/activity - versioned fast council activity JSON."""
    cors = _cors(request.headers.get("Origin"))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"error": "blocked"}, status=403, headers=cors)
    fields = _council_parse_fields_param(
        request.query.get("fields", ""), _COUNCIL_ACTIVITY_DEFAULT_FIELDS)
    offset = max(0, _query_int(request, "offset", 0))
    limit = max(0, _query_int(request, "limit", 0))
    cache_key = (
        "activity:" + ",".join(sorted(fields)) + f":{offset}:{limit}"
    )
    since_version, client_generation = _council_snapshot_query(request)
    source_revision = await asyncio.to_thread(_council_dashboard_source_revision)
    payload = await asyncio.to_thread(
        _council_cached_dashboard_payload,
        cache_key,
        lambda: _council_filter_agent_payload(
            _council_activity_payload(fields), fields, offset, limit),
        fresh=since_version > 0,
        source_revision=source_revision,
    )
    selection = await asyncio.to_thread(
        _COUNCIL_DASHBOARD_SNAPSHOT_CACHE.select,
        _council_dashboard_cache_key(cache_key),
        payload,
        since_version=since_version,
        client_generation=client_generation,
        generation=SERVER_GENERATION,
        collection_field="agents",
        id_field="id",
    )
    return _council_snapshot_response(
        request, selection, int(payload.get("version", 0) or 0))


async def handle_council_stop(request):
    """POST /api/council/stop  {agent, reason?}  localhost+origin guard.
    Stop interrupts the current worker subprocess, records a terminal
    runtime state, and leaves the agent ready for the next Send. It does not
    pause/disable the worker and it does not require a manual app restart."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response(
            {"ok": False, "error": "blocked", "error_name": "ORIGIN_BLOCKED"},
            status=403, headers=cors,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    agent = str(body.get("agent", ""))
    if agent not in _COUNCIL_AGENTS:
        return web.json_response({
            "ok": False,
            "error": "unknown agent",
            "error_name": "INVALID_AGENT",
            "agent": agent,
            "allowed": list(_COUNCIL_AGENTS),
        }, status=400, headers=cors)
    reason = str(body.get("reason", "user stopped current run from /council UI"))[:200]
    runtime_before = _council_runtime_status(agent)
    worker_pid = runtime_before.get("worker_pid")
    pids: list[int] = []
    try:
        if worker_pid:
            pids.append(int(worker_pid))
    except Exception:
        pids = []
    if not pids:
        pids = _council_find_agent_pids(agent)
    killed: list[int] = []
    failed: list[dict] = []
    for pid in pids:
        try:
            _council_kill_process_tree(int(pid))
            killed.append(int(pid))
        except Exception as exc:
            failed.append({"pid": int(pid), "error": str(exc)})
    council_dir = _HERE / ".council"
    try:
        _council_clear_paused(council_dir, agent)
    except Exception:
        pass
    with _COUNCIL_AGENT_QUEUE_LOCK:
        queued_before = len(_COUNCIL_AGENT_QUEUES.get(agent) or [])
        _COUNCIL_AGENT_QUEUES[agent] = deque()
    runtime_after = _council_runtime_stop(agent, reason)
    return web.json_response({
        "ok": True,
        "agent": agent,
        "state": "stopped",
        "pids_killed": killed,
        "pids_failed": failed,
        "queued_cleared": queued_before,
        "runtime": runtime_after,
        "note": "current run stopped; Send another prompt to start a fresh worker run.",
    }, headers=cors)


async def handle_council_pause(request):
    """Backward-compatible alias for /api/council/stop."""
    return await handle_council_stop(request)


async def handle_council_play(request):
    """POST /api/council/play  {agent}  localhost+origin guard.
    Clears the paused state file. Does NOT auto-launch the agent — caller
    must restart the CLI manually if they want it back. State UI updates on
    next /api/council/state refresh (1s interval)."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response(
            {"ok": False, "error": "blocked", "error_name": "ORIGIN_BLOCKED"},
            status=403, headers=cors,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    agent = str(body.get("agent", ""))
    if agent not in _COUNCIL_AGENTS:
        return web.json_response({
            "ok": False,
            "error": "unknown agent",
            "error_name": "INVALID_AGENT",
            "agent": agent,
            "allowed": list(_COUNCIL_AGENTS),
        }, status=400, headers=cors)
    council_dir = _HERE / ".council"
    cleared = _council_clear_paused(council_dir, agent)
    return web.json_response({
        "ok": True,
        "agent": agent,
        "cleared": cleared,
        "note": "paused state cleared; agent will NOT auto-restart. Restart manually if needed.",
    }, headers=cors)


async def handle_council_clear_session(request):
    """POST /api/council/clear-session - clear Council runtime/UI session data."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response(
            {"ok": False, "error": "blocked", "error_name": "ORIGIN_BLOCKED"},
            status=403, headers=cors,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict) or body.get("confirm") is not True:
        return web.json_response(
            {"ok": False, "error": "confirm=true required", "error_name": "CONFIRMATION_REQUIRED"},
            status=400, headers=cors,
        )
    result = await asyncio.to_thread(_council_clear_session_runtime, _HERE / ".council")
    return web.json_response(result, headers=cors, dumps=lambda x: json.dumps(x, ensure_ascii=False))


async def handle_council_worker_config(request):
    """GET  /api/council/worker-config        -> current {agent:{provider,model}} overrides.
    POST /api/council/worker-config {agent,provider,model} -> set one worker's provider/model.
    Written to .council/worker_models.json; read at dispatch time (no restart)."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"ok": False, "error": "blocked", "error_name": "ORIGIN_BLOCKED"}, status=403, headers=cors)
    path = _HERE / ".council" / "worker_models.json"

    def _load() -> dict:
        try:
            with _COUNCIL_WORKER_MODELS_LOCK:
                d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    if request.method == "GET":
        config = _load()
        resolutions = {name: _council_resolve_model(name).as_dict() for name in _COUNCIL_AGENTS}
        return web.json_response({"ok": True, "config": config, "resolutions": resolutions,
                                  "agents": list(_COUNCIL_AGENTS)}, headers=cors,
                                 dumps=lambda x: json.dumps(x, ensure_ascii=False))
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    agent = str(body.get("agent", "")).strip()
    provider = str(body.get("provider", "")).strip()
    model = str(body.get("model", "")).strip()
    checked = _council_validate_model_setting(agent, provider, model)
    if not checked.get("ok"):
        return web.json_response(checked, status=400, headers=cors)
    provider = str(checked["provider"])
    warnings = list(checked.get("warnings") or [])
    try:
        with _COUNCIL_WORKER_MODELS_LOCK:
            data = _load()
            data[agent] = {"provider": provider, "model": model}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc), "error_name": "WRITE_FAILED"}, status=500, headers=cors)
    response = {"ok": True, "agent": agent, "provider": provider, "model": model,
                "resolution": _council_resolve_model(agent).as_dict(),
                "note": "applied on next dispatch (no restart)"}
    if warnings:
        response["warnings"] = warnings
    return web.json_response(response, headers=cors)


async def handle_council_valid_models(request):
    """GET /api/council/valid-models?agent=<id> - read-only curated model options."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"ok": False, "error": "blocked", "error_name": "ORIGIN_BLOCKED"}, status=403, headers=cors)
    agent = str(request.query.get("agent", "")).strip().lower()
    if agent not in _COUNCIL_AGENTS:
        return web.json_response({"ok": False, "error": "unknown agent", "error_name": "INVALID_AGENT",
                                  "allowed": list(_COUNCIL_AGENTS)}, status=400, headers=cors)
    path = _HERE / ".council" / "worker_models.json"
    current = {}
    try:
        with _COUNCIL_WORKER_MODELS_LOCK:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if isinstance(data, dict) and isinstance(data.get(agent), dict):
            current = data.get(agent) or {}
    except Exception as exc:
        logger.warning(f"[council] worker_models.json read failed for valid-models {agent}: {exc}")
    return web.json_response({
        "ok": True,
        "agent": agent,
        "valid": _council_valid_models(agent) or [],
        "models": _council_valid_model_options(agent),
        "current": current,
    }, headers=cors, dumps=lambda x: json.dumps(x, ensure_ascii=False))


async def handle_council_events(request):
    """GET /api/council/events - versioned compact broker transcript."""
    cors = _cors(request.headers.get("Origin"))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response(
            {"ok": False, "error": "blocked"}, status=403, headers=cors)
    limit = max(1, min(5000, _query_int(request, "limit", 80)))
    council_dir = _HERE / ".council"
    cache_key = f"events:{limit}"
    since_version, client_generation = _council_snapshot_query(request)
    source_revision = await asyncio.to_thread(_council_dashboard_source_revision)
    payload = await asyncio.to_thread(
        _council_cached_dashboard_payload,
        cache_key,
        lambda: {
            "ok": True,
            "events": _council_public_events(council_dir, limit),
        },
        fresh=since_version > 0,
        source_revision=source_revision,
    )
    await asyncio.to_thread(
        _council_export_public_broker_if_new,
        cache_key,
        council_dir,
        list(payload.get("events") or []),
        int(payload.get("version", 0) or 0),
    )
    selection = await asyncio.to_thread(
        _COUNCIL_DASHBOARD_SNAPSHOT_CACHE.select,
        _council_dashboard_cache_key(cache_key),
        payload,
        since_version=since_version,
        client_generation=client_generation,
        generation=SERVER_GENERATION,
        collection_field="events",
        id_field="id",
    )
    return _council_snapshot_response(
        request, selection, int(payload.get("version", 0) or 0))


def _council_preflight_probe(agent: str, timeout_sec: int) -> dict:
    """Cheap one-token health probe through the existing broker dispatch path."""
    started = time.perf_counter()
    resolution = _council_resolve_model(agent)
    if resolution.resolution_status == "unavailable":
        return {"reachable": False, "auth_ok": False, "last_error": "COUNCIL_MODEL_UNAVAILABLE",
                "p50_latency": 0.0, **resolution.as_dict()}
    result = _council_run_agent(agent, "Reply exactly: OK", timeout_sec, "broker", "", "", "",
                                resolution.resolved_provider, resolution.resolved_model)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    error = str(result.get("error") or "")
    raw = " ".join((error, str(result.get("reply") or ""), str(result.get("raw_preview") or ""))).lower()
    auth_ok = not any(token in raw for token in ("auth", "unauthorized", "forbidden", "401", "403", "quota", "exhaust"))
    return {"reachable": bool(result.get("ok")), "auth_ok": bool(result.get("ok")) or auth_ok,
            "last_error": error, "p50_latency": latency_ms, **result.get("model_resolution", resolution.as_dict())}


async def handle_council_preflight(request):
    """POST /api/council/preflight {agents:[...], timeout_sec?:int}."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"ok": False, "error": "blocked", "error_name": "ORIGIN_BLOCKED"}, status=403, headers=cors)
    try:
        body = await request.json()
    except Exception:
        body = {}
    agents = body.get("agents") if isinstance(body, dict) else None
    if not isinstance(agents, list) or not agents:
        return web.json_response({"ok": False, "error": "agents must be a non-empty list", "error_name": "INVALID_AGENTS"}, status=400, headers=cors)
    normalized = [str(agent or "").strip().lower() for agent in agents]
    invalid = [agent for agent in normalized if agent not in _COUNCIL_AGENTS]
    if invalid:
        return web.json_response({"ok": False, "error": "unknown agents", "error_name": "INVALID_AGENT", "invalid": invalid, "allowed": list(_COUNCIL_AGENTS)}, status=400, headers=cors)
    timeout_sec = max(2, min(30, int(body.get("timeout_sec", 8) or 8)))
    probes = await asyncio.gather(*[
        _council_run_blocking(_council_preflight_probe, agent, timeout_sec) for agent in normalized
    ])
    return web.json_response({"ok": True, "agents": dict(zip(normalized, probes))}, headers=cors, dumps=lambda x: json.dumps(x, ensure_ascii=False))


async def handle_council_pick_folder(request):
    """POST /api/council/pick-folder - open native OS folder dialog, return absolute path."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response(
            {"ok": False, "error": "blocked", "error_name": "ORIGIN_BLOCKED"},
            status=403, headers=cors,
        )
    ps_cmd = (
        'Add-Type -AssemblyName System.Windows.Forms; '
        '$f=New-Object System.Windows.Forms.FolderBrowserDialog; '
        'if($f.ShowDialog() -eq "OK"){$f.SelectedPath}'
    )
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ['powershell', '-STA', '-Command', ps_cmd],
            capture_output=True, text=True, timeout=60,
        )
        path = (result.stdout or '').strip()
        if result.returncode == 0 and path:
            return web.json_response({"ok": True, "path": path}, headers=cors)
        return web.json_response({"ok": False, "error": "no folder selected"}, headers=cors)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, headers=cors)


async def handle_council_message(request):
    """POST /api/council/message - brokered agent chat without prompt_*.md files."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"ok": False, "error": "blocked", "error_name": "ORIGIN_BLOCKED"}, status=403, headers=cors)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    # CAN-020/021: authoritative server-side sender resolution (PID-trace beats any
    # declared label; non-worker callers cannot impersonate a worker). See
    # _council_resolve_sender. Drops the legacy 'from' alias after resolving.
    sender = _council_resolve_sender(request, str(body.get("sender", body.get("from", "")) or ""))
    for _alias in ("from", "frm", "from_agent"):
        body.pop(_alias, None)
    target = str(body.get("to", "")).strip()
    text = str(body.get("text", "")).strip()
    timeout_sec = int(body.get("timeout_sec", 6000) or 6000)
    mode = (str(body.get("mode", "broker")).strip().lower() or "broker")
    project_path = str(body.get("project_path", "")).strip()
    provider = str(body.get("provider", "")).strip()
    model = str(body.get("model", "")).strip()
    # TTL auto-cleanup (throttled ~1/hour): keep ~7 days of session artifacts then self-clean.
    if time.time() - _COUNCIL_LAST_PURGE[0] > 3600:
        _COUNCIL_LAST_PURGE[0] = time.time()
        try:
            await asyncio.to_thread(_council_purge_old, _HERE / ".council", 7)
        except Exception:
            pass
    if target not in _COUNCIL_AGENTS:
        return web.json_response({"ok": False, "error": "unknown target", "error_name": "INVALID_AGENT", "allowed": list(_COUNCIL_AGENTS)}, status=400, headers=cors)
    if mode not in ("broker", "context"):
        return web.json_response({"ok": False, "error": "mode must be broker|context", "error_name": "INVALID_MODE"}, status=400, headers=cors)
    caps = _council_agent_caps(target)
    if not caps.get("enabled", True):
        return web.json_response({"ok": False, "error": f"agent {target} is disabled", "error_name": "AGENT_DISABLED"}, status=400, headers=cors)
    # Self-message guard: a worker messaging itself is a no-op self-recursion.
    if str(sender).strip().lower() == target:
        return web.json_response({"ok": False, "error": f"agent '{target}' cannot message itself",
                                  "error_name": "SELF_MESSAGE",
                                  "hint": "Same-identity dispatch must set sender=\"user\". Retry once with sender=\"user\" and keep the original task scope unchanged."}, status=400, headers=cors)
    if not _council_supports_mode(target, mode):
        return web.json_response(
            {"ok": False, "error": f"agent {target} does not support mode={mode}", "error_name": "MODE_NOT_SUPPORTED",
             "supports": {m: _council_supports_mode(target, m) for m in ("broker", "context")}},
            status=400, headers=cors)
    if not text:
        return web.json_response({"ok": False, "error": "text required", "error_name": "MISSING_TEXT"}, status=400, headers=cors)
    try:
        queue_wait_timeout_sec = int(body.get("queue_wait_timeout_sec", 6000) or 6000)
    except Exception:
        queue_wait_timeout_sec = 6000
    queued_result = await _council_enqueue_or_run(
        target, sender[:40], text, timeout_sec, mode, project_path,
        queue=_council_queue_enabled(body), queue_wait_timeout_sec=queue_wait_timeout_sec, prompt_style="json",
        provider=provider, model=model, fallback_policy=body.get("fallback_policy", "none"),
        allowed_fallbacks=body.get("allowed_fallbacks", []),
    )
    if not queued_result.get("ok") and queued_result.get("error_name") in ("AGENT_BUSY", "AGENT_QUEUE_FULL", "QUEUE_WAIT_TIMEOUT"):
        status = {"AGENT_BUSY": 409, "AGENT_QUEUE_FULL": 429, "QUEUE_WAIT_TIMEOUT": 504}[queued_result["error_name"]]
        return web.json_response(queued_result, status=status, headers=cors, dumps=lambda x: json.dumps(x, ensure_ascii=False))
    return web.json_response(
        {"ok": bool(queued_result.get("ok")), "request": queued_result.get("request"),
         "reply": queued_result.get("reply_event"), "mode": queued_result.get("mode", mode),
         "provider": queued_result.get("provider", provider), "model": queued_result.get("model", model),
         "session_id": queued_result.get("session_id", ""), "context_turns": queued_result.get("context_turns", 0),
         "raw_preview": queued_result.get("raw_preview", "")[:1200]},
        headers=cors,
        dumps=lambda x: json.dumps(x, ensure_ascii=False),
    )


_COUNCIL_USER_RECIPIENTS = {"anh", "user", "human", "nguoi dung", "leader", "you"}


def _council_worker_pid_map() -> dict:
    """worker_pid -> agent from the shared runtime file (live runs only)."""
    wmap: dict[int, str] = {}
    try:
        rt = json.loads(_council_runtime_file().read_text(encoding="utf-8"))
        for ag, e in (rt or {}).items():
            wp = (e or {}).get("worker_pid")
            if wp:
                wmap[int(wp)] = ag
    except Exception:
        pass
    return wmap


def _council_pid_to_agent(start_pid: int) -> str:
    """Walk a PID up its ancestry to the first live worker_pid -> agent. "" if none.
    Unspoofable auto-identity: a worker cannot fake its own process ancestry."""
    try:
        import psutil
        wmap = _council_worker_pid_map()
        if not wmap:
            return ""
        p = psutil.Process(int(start_pid))
        for _ in range(15):
            if p.pid in wmap:
                return wmap[p.pid]
            p = p.parent()
            if p is None:
                break
    except Exception:
        return ""
    return ""


def _council_identify_self() -> str:
    """Identify the council worker that owns THIS process (used by the worker's own
    [A] MCP server, which runs council_message locally and lacks COUNCIL_AGENT_ID
    when its parent CLI did not forward the env). Walks os.getpid() up to a worker_pid."""
    return _council_pid_to_agent(os.getpid())


def _council_identify_caller(request) -> str:
    """Identify which worker made this HTTP call by tracing the source TCP
    connection's PID up to a live worker_pid. "" for desktop CA / broker UI / tests."""
    try:
        transport = getattr(request, "transport", None)
        if transport is None:
            return ""
        peer = transport.get_extra_info("peername")
        if not peer or len(peer) < 2:
            return ""
        src_port = int(peer[1])
        import psutil
        caller_pid = None
        for c in psutil.net_connections(kind="tcp"):
            if c.laddr and c.laddr.port == src_port and c.raddr and c.raddr.port == PORT:
                caller_pid = c.pid
                break
        if not caller_pid:
            return ""
        return _council_pid_to_agent(int(caller_pid))
    except Exception:
        return ""


def _council_resolve_sender(request, declared: str) -> str:
    """CAN-020/021: server-side authoritative sender resolution for council_message.

    1) PID-trace the caller's TCP connection to a live worker (_council_identify_caller)
       — unspoofable; a running worker is always identified by its real process.
       Authoritative: a worker can never claim to be another worker.
    2) No worker PID => a desktop CA / dashboard / external localhost caller. Such a
       caller's DECLARED label is honoured ONLY as a leader self-label ('clau'/'codo')
       or a user/leader alias; a WORKER name (ope/pic/hermes/kilo) or any other
       string is normalized to 'user' so a non-worker cannot impersonate a worker
       (CAN-020). The raw X-Council-Agent header is intentionally NOT trusted here (any
       localhost process can set it — PiC review); real workers are covered by (1).
    Both 'sender' and the legacy 'from' alias (CAN-021) feed `declared`."""
    try:
        pid_agent = _council_identify_caller(request) if request is not None else ""
    except Exception:
        pid_agent = ""
    if pid_agent:
        return pid_agent
    d = (declared or "").strip()
    dl = d.lower()
    if dl in {"clau", "codo"} or dl in _COUNCIL_USER_RECIPIENTS:
        return d
    return "user"


def _council_q_context_augment(text: str, q_zones: str = "") -> str:
    """Combo-1 (2026-07-12, opt-in via council_message q_context=true): append a
    compact [Q] memory block to the dispatch prompt so the worker starts with
    relevant memory without loading old sessions. q_zones (csv, e.g. "Z2") narrows
    the lookup — Z2 = rules/skills/tool-catalog for how-to tasks. Fail-open: any
    [Q] problem (down, slow, no hits) returns the text unchanged."""
    try:
        payload = {"agent_id": "A:dispatch", "prompt": text[:800],
                   "top_k": 3, "format": "ca"}
        zones = [z.strip().upper() for z in str(q_zones or "").split(",") if z.strip()]
        if zones:
            payload["zones"] = zones
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:8082/pre_search", data=body,
                                     headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "replace"))
        hits = resp.get("hits") or []
        if not (resp.get("ok") and hits):
            return text
        lines = []
        for h in hits[:3]:
            snip = str(h.get("snippet") or "")[:220]
            lines.append(f"- ({h.get('zone') or '?'} {str(h.get('ts') or '')[:10]}) {snip} [mem {h.get('id')}]")
        return text + "\n\n[Q MEMORY CONTEXT - compact, verify before trusting]\n" + "\n".join(lines)
    except Exception:
        return text



_COUNCIL_BODY_FILE_MAX_BYTES = 256 * 1024
_COUNCIL_BODY_FILE_SUFFIXES = {".md", ".txt"}


def _council_message_source(text=None, body_file=None):
    """Resolve exactly one safe council message source without repairing text."""
    has_text = text is not None and str(text).strip() != ""
    has_file = body_file is not None and str(body_file).strip() != ""
    if has_text == has_file:
        return "", {
            "ok": False,
            "error_name": "MESSAGE_SOURCE_CONFLICT" if has_text else "MESSAGE_BODY_REQUIRED",
            "error": "provide exactly one of text or body_file",
        }
    if has_text:
        resolved = str(text).strip()
    else:
        raw = str(body_file).strip().replace("\\", "/")
        rel = Path(raw)
        if rel.is_absolute() or rel.parts[:2] != (".council", "briefs") or rel.suffix.lower() not in _COUNCIL_BODY_FILE_SUFFIXES:
            return "", {"ok": False, "error_name": "BODY_FILE_OUTSIDE_ALLOWLIST",
                        "error": "body_file must be .council/briefs/*.md or *.txt"}
        lexical = _HERE.joinpath(*rel.parts)
        try:
            cursor = _HERE
            for part in rel.parts:
                cursor = cursor / part
                st = cursor.lstat()
                if cursor.is_symlink() or bool(getattr(st, "st_file_attributes", 0) & 0x400):
                    raise ValueError("symlink or reparse point")
            candidate = lexical.resolve(strict=True)
            root = (_HERE / ".council" / "briefs").resolve(strict=True)
            if os.path.commonpath((str(candidate), str(root))) != str(root) or not candidate.is_file():
                raise ValueError("outside allowlist or not a regular file")
        except (OSError, ValueError):
            return "", {"ok": False, "error_name": "BODY_FILE_OUTSIDE_ALLOWLIST",
                        "error": "body_file is missing, outside allowlist, or uses a symlink/reparse point"}
        size = candidate.stat().st_size
        if size > _COUNCIL_BODY_FILE_MAX_BYTES:
            return "", {"ok": False, "error_name": "BODY_FILE_TOO_LARGE",
                        "error": f"body_file exceeds {_COUNCIL_BODY_FILE_MAX_BYTES} bytes"}
        try:
            resolved = candidate.read_text(encoding="utf-8", errors="strict").strip()
        except UnicodeDecodeError:
            return "", {"ok": False, "error_name": "BODY_FILE_NOT_UTF8",
                        "error": "body_file must be strict UTF-8"}
        if not resolved:
            return "", {"ok": False, "error_name": "MESSAGE_BODY_REQUIRED",
                        "error": "resolved body_file is empty"}
    if ct._r54_detect_mojibake(resolved)[0]:
        return "", {"ok": False, "error_name": "SUSPECTED_MOJIBAKE",
                    "error": "message appears to be UTF-8 decoded with a legacy code page; resend UTF-8 bytes"}
    return resolved, None

async def _council_dispatch_message(sender, target, text, timeout_sec=6000, queue=True, queue_wait_timeout_sec=6000,
                                    provider: str = "", model: str = "", fallback_policy: str = "none",
                                    allowed_fallbacks=None, q_context: bool = False,
                                    q_zones: str = "", async_dispatch: bool = False):
    """Shared council dispatch core: validate -> run agent -> log broker. Returns a plain dict.
    target is a worker id/label -> dispatch + return its reply. target is user/Anh -> just post the
    message to the broker (delivered to the human dashboard / leader); no agent is run."""
    sender = (str(sender or "worker").strip() or "worker")[:40]
    target_raw = str(target or "").strip()
    target = target_raw.lower()
    text = str(text or "").strip()
    if ct._r54_detect_mojibake(text)[0]:
        return {"ok": False, "error_name": "SUSPECTED_MOJIBAKE",
                "error": "message appears to be UTF-8 decoded with a legacy code page; resend UTF-8 bytes"}
    try:
        timeout_sec = int(timeout_sec or 6000)
    except Exception:
        timeout_sec = 6000
    if not text:
        return {"ok": False, "error_name": "MISSING_TEXT", "error": "text required"}
    if target in _COUNCIL_USER_RECIPIENTS:
        _council_append_event({"type": "message", "from": sender, "to": "Anh", "mode": "broker", "project_path": "", "text": text[:_COUNCIL_EVENT_TEXT_MAX]})
        return {"ok": True, "from": sender, "to": "Anh", "delivered": "user", "reply": "",
                "note": "posted to broker; the human/leader sees it in the council dashboard (no agent dispatched)"}
    # Accept label forms too (opeC -> ope, HermC -> hermes, PiC -> pic).
    if target not in _COUNCIL_AGENTS:
        for _aid, _lbl in _COUNCIL_AGENT_LABELS.items():
            if target == _lbl.lower():
                target = _aid
                break
    if target not in _COUNCIL_AGENTS:
        return {"ok": False, "error_name": "INVALID_AGENT", "error": f"unknown target '{target_raw}'",
                "allowed": list(_COUNCIL_AGENTS) + ["user"]}
    if not _council_agent_caps(target).get("enabled", True):
        return {"ok": False, "error_name": "AGENT_DISABLED", "error": f"agent {target} is disabled"}
    # Self-message guard: a worker messaging itself (sender == target) is a no-op
    # self-recursion that only adds queue noise (seen as "ope -> ope"). Reject it.
    if str(sender).strip().lower() == target:
        return {"ok": False, "error_name": "SELF_MESSAGE",
                "error": f"agent '{target}' cannot message itself",
                "hint": "Same-identity dispatch must set sender=\"user\". Retry once with sender=\"user\" and keep the original task scope unchanged."}
    if q_context and text:
        text = await asyncio.to_thread(_council_q_context_augment, text, q_zones)
    # BROKER-RETRY-AFTER-SUCCESS (F1): if this prompt names an explicit output artifact
    # that is ALREADY complete on disk, the task succeeded on a prior dispatch. Treat the
    # artifact as the idempotent source of truth and short-circuit instead of re-running
    # (a duplicate dispatch after success could falsely fail if the provider is down).
    _done_gate = _artifact_already_complete(target, text)
    if _done_gate is not None:
        return {"ok": True, "from": target, "to": sender,
                "dispatched": False, "reply": "", "error": "", "error_name": "",
                "artifact": _done_gate.get("artifact", ""),
                "note": _done_gate.get("note", "")}
    if not _council_worker_model_configured(target, model):
        return {"ok": False, "error_name": "WORKER_MODEL_NOT_SET", "agent": target,
                "ca_next_action": "council_worker_model set+get before dispatch"}
    # Non-blocking worker->worker: a DISPATCHED worker (mid-run) has a short CLI tool
    # timeout (codex ~10s / pi ~15s) << a target's reply, which caused
    # timeouts + retry storms when it block-waited. Hand the dispatch off to the
    # long-lived daemon (survives this possibly-transient MCP-server process) and
    # return an ack now; the reply lands in broker_filtered.jsonl, which workers read
    # (council rule).
    # P2 (2026-08-06): the handoff is no longer running-worker-only. ANY caller
    # (leader parallel fan-out, desktop CA, CLI) may opt in with async_dispatch=True
    # so N parallel dispatches ack immediately instead of each holding its HTTP
    # connection open until the worker replies (headers timeout). Callers that do
    # NOT opt in (and are not a running worker) keep the classic sync reply.
    try:
        _caller_running = (sender in _COUNCIL_AGENTS
                           and _council_runtime_status(sender).get("state") == "running")
    except Exception:
        _caller_running = False
    if (_caller_running or async_dispatch) and target in _COUNCIL_AGENTS:
        _dispatch_id = f"run-{uuid.uuid4()}"
        try:
            _payload = json.dumps({"sender": sender, "to": target, "text": text,
                                   "timeout_sec": timeout_sec, "provider": provider, "model": model,
                                   "fallback_policy": fallback_policy, "allowed_fallbacks": allowed_fallbacks or [],
                                   "request_id": _dispatch_id, "record_ledger": False}).encode("utf-8")
            def _fire():
                import urllib.request as _ur
                _req = _ur.Request(f"http://localhost:{PORT}/api/council/send-async", data=_payload,
                                   headers={"Content-Type": "application/json", "Origin": f"http://localhost:{PORT}",
                                            "X-Orchestrator-Token": EXPECTED_TOKEN})
                return _ur.urlopen(_req, timeout=15).read()
            await asyncio.to_thread(_fire)
            # Relay acknowledgement is the acceptance boundary.
            with _COUNCIL_DISPATCH_LEDGER_LOCK:
                _council_dispatch_record(_dispatch_id, sender, target, _dispatch_id,
                                         queue_wait_timeout_sec, state="accepted")
            return {"ok": True, "from": sender, "to": target, "dispatched": True, "reply": "",
                    "error_name": "", "request_id": _dispatch_id, "dispatch_id": _dispatch_id,
                    "note": "dispatched (non-blocking); read .council/broker_filtered.jsonl for the reply"}
        except Exception as exc:
            _council_reject_dispatch(_dispatch_id, sender, target, queue_wait_timeout_sec, str(exc))
            return {"ok": False, "error_name": "ASYNC_HANDOFF_FAILED", "error": str(exc),
                    "request_id": _dispatch_id, "dispatch_id": _dispatch_id}
    queued_result = await _council_enqueue_or_run(
        target, sender, text, timeout_sec, "broker", "",
        queue=bool(queue), queue_wait_timeout_sec=queue_wait_timeout_sec,
        provider=provider, model=model, fallback_policy=fallback_policy,
        allowed_fallbacks=allowed_fallbacks,
    )
    if queued_result.get("error_name"):
        return queued_result
    _resp = {"ok": bool(queued_result.get("ok")), "from": target, "to": sender,
            "reply": queued_result.get("reply_text", ""), "error": queued_result.get("error", ""),
            "provider": queued_result.get("provider", provider), "model": queued_result.get("model", model),
            "error_name": "" if queued_result.get("ok") else "AGENT_ERROR"}
    # GAP-01/H5: opt-in council graph-context pack, RESPONSE ATTACHMENT only --
    # it is NEVER injected into the worker prompt (Step-21 contract). Keyed by
    # (project_path, context-registry generation) so a bumped generation
    # invalidates it. project_path = the worker's latest live context entry.
    # Fail-open: any miss leaves the dispatch reply byte-for-byte unchanged.
    if os.environ.get("CBM_ENRICH_COUNCIL", "0").strip().lower() in ("1", "true", "yes", "on"):
        try:
            _ctx = _council_find_latest_context(target) or {}
            _proj = str(_ctx.get("project_path") or "").strip()
            if _proj:
                from orchestrator.graph.enrich_producers import (
                    produce_council_pack as _cbm_warm_council,
                )
                _pack = await asyncio.to_thread(_cbm_warm_council, _proj, generation=_council_context_generation())
                if isinstance(_pack, dict) and _pack.get("payload"):
                    _resp["council_pack"] = {"status": "ok", "reason": _pack.get("reason"),
                                             "fingerprint": _pack.get("fingerprint"),
                                             "payload": _pack.get("payload")}
        except Exception:
            pass  # fail-open: no pack attached, the reply still returns intact
    return _resp


from orchestrator.council.cron_jobs import (
    CouncilCronService,
    CronJobError,
    JobNotFound,
    RunConflict,
)

_COUNCIL_CRON_SERVICE = CouncilCronService(
    _HERE / ".council" / "council_cron.db",
    _council_dashboard_agents,
)


def _council_cron_guard(request):
    cors = _cors(request.headers.get("Origin"))
    if not _is_local(request) or not _origin_ok(request):
        return cors, web.json_response(
            {"ok": False, "error_name": "FORBIDDEN", "error": "blocked"},
            status=403,
            headers=cors,
        )
    return cors, None


async def _council_cron_body(request) -> dict:
    try:
        body = await request.json()
    except Exception as exc:
        raise CronJobError("request body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise CronJobError("request body must be a JSON object")
    return body


def _council_cron_error(exc: Exception, cors, *, default_status: int = 400):
    status = 404 if isinstance(exc, JobNotFound) else 409 if isinstance(exc, RunConflict) else default_status
    return web.json_response(
        {
            "ok": False,
            "error_name": getattr(exc, "error_name", "CRON_JOB_ERROR"),
            "error": str(exc),
        },
        status=status,
        headers=cors,
    )


async def _council_cron_dispatch(job: dict) -> dict:
    try: workers = json.loads(str(job.get("workers_json") or "[]"))
    except Exception: workers = []
    if not workers: workers = [str(job.get("worker") or "").lower()]
    async def one(worker):
        worker = str(worker).lower()
        model = str(job.get("worker_model") or "")
        provider = str(job.get("worker_provider") or "")
        if not _council_worker_model_configured(worker, model):
            return {"agent": worker, "ok": False, "error_name": "WORKER_MODEL_NOT_SET", "error": f"worker model not set: {worker}"}
        result = await _council_enqueue_or_run(worker, "cron", str(job.get("prompt") or ""), int(job.get("timeout_sec") or 3600), "broker", "", queue=True, queue_wait_timeout_sec=int(job.get("timeout_sec") or 3600) + 300, prompt_style="tool", provider=provider, model=model)
        return {"agent": worker, "ok": bool(result.get("ok")), "reply_text": str(result.get("reply_text") or result.get("reply") or ""), "error": str(result.get("error") or "")}
    results = list(await asyncio.gather(*(one(worker) for worker in workers)))
    return {"ok": all(item.get("ok") for item in results), "reply_text": (chr(10) * 2).join(f"[{item['agent']}] {item.get('reply_text') or item.get('error')}" for item in results), "worker_results": results, "error": "; ".join(item.get("error") or "" for item in results if not item.get("ok"))}


async def _council_cron_narrate(job: dict, worker_reply: str) -> dict:
    worker = str(job.get("narrator_worker") or job.get("worker") or "").lower()
    model = str(job.get("narrator_model") or "")
    provider = str(job.get("narrator_provider") or "")
    if not _council_worker_model_configured(worker, model):
        return {"ok": False, "error_name": "WORKER_MODEL_NOT_SET", "error": f"narrator model not set: {worker}"}
    instruction = str(job.get("narrator_prompt") or _COUNCIL_NARRATOR_DEFAULT_PROMPT)
    prompt = instruction + chr(10) * 2 + "=== SCHEDULED JOB ===" + chr(10) + str(job.get("name") or job.get("id") or "") + chr(10) + "=== WORKER RESULTS ===" + chr(10) + worker_reply[:30000]
    return await _council_enqueue_or_run(worker, "cron", prompt, min(900, int(job.get("timeout_sec") or 3600)), "broker", "", queue=True, queue_wait_timeout_sec=min(1200, int(job.get("timeout_sec") or 3600) + 300), prompt_style="tool", provider=provider, model=model)


async def _council_cron_startup(app):
    await _COUNCIL_CRON_SERVICE.start(_council_cron_dispatch, _council_cron_narrate)


async def _council_cron_cleanup(app):
    await _COUNCIL_CRON_SERVICE.stop()


async def handle_council_cron_jobs_list(request):
    cors, blocked = _council_cron_guard(request)
    if blocked:
        return blocked
    return web.json_response({"ok": True, "jobs": _COUNCIL_CRON_SERVICE.list_jobs()}, headers=cors)


async def handle_council_cron_jobs_create(request):
    cors, blocked = _council_cron_guard(request)
    if blocked:
        return blocked
    try:
        job = _COUNCIL_CRON_SERVICE.create_job(await _council_cron_body(request))
        return web.json_response({"ok": True, "job": job}, status=201, headers=cors)
    except Exception as exc:
        return _council_cron_error(exc, cors)


async def handle_council_cron_jobs_update(request):
    cors, blocked = _council_cron_guard(request)
    if blocked:
        return blocked
    try:
        job = _COUNCIL_CRON_SERVICE.update_job(request.match_info["job_id"], await _council_cron_body(request))
        return web.json_response({"ok": True, "job": job}, headers=cors)
    except Exception as exc:
        return _council_cron_error(exc, cors)


async def handle_council_cron_jobs_delete(request):
    cors, blocked = _council_cron_guard(request)
    if blocked:
        return blocked
    try:
        job_id = request.match_info["job_id"]
        _COUNCIL_CRON_SERVICE.delete_job(job_id)
        return web.json_response({"ok": True, "deleted": job_id}, headers=cors)
    except Exception as exc:
        return _council_cron_error(exc, cors)


async def handle_council_cron_jobs_trigger(request):
    cors, blocked = _council_cron_guard(request)
    if blocked:
        return blocked
    try:
        run = await _COUNCIL_CRON_SERVICE.trigger_job(request.match_info["job_id"])
        return web.json_response({"ok": True, "run": run}, status=202, headers=cors)
    except Exception as exc:
        return _council_cron_error(exc, cors)


async def handle_council_cron_runs(request):
    cors, blocked = _council_cron_guard(request)
    if blocked:
        return blocked
    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 200))
        job_id = request.match_info.get("job_id") or request.query.get("job_id") or None
        if job_id:
            _COUNCIL_CRON_SERVICE.get_job(job_id)
        runs = _COUNCIL_CRON_SERVICE.list_runs(job_id, limit)
        return web.json_response({"ok": True, "runs": runs}, headers=cors)
    except Exception as exc:
        return _council_cron_error(exc, cors)


async def handle_council_room_register(request):
    """POST /api/council/room/register  {room_id, ttl_sec?, metadata?}  localhost+origin guard.
    STEP 3a: create/refresh a council room; returns room_id + room_token + expires_at.
    Room core helpers (_room_register/_room_validate_token) already exist - HTTP glue only."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response(
            {"ok": False, "error": "blocked", "error_name": "ORIGIN_BLOCKED"},
            status=403, headers=cors,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    room_id = str(body.get("room_id", "") or "")
    if not _ROOM_ID_RE.fullmatch(room_id):
        return web.json_response(
            {"ok": False, "error": "room_id missing or invalid",
             "error_name": "ROOM_PATH_INVALID"},
            status=400, headers=cors,
        )
    try:
        ttl_sec = int(body.get("ttl_sec", 3600) or 3600)
    except (TypeError, ValueError):
        ttl_sec = 3600
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        metadata = None
    try:
        result = await asyncio.to_thread(
            _room_register, room_id, ttl_sec=ttl_sec, metadata=metadata)
    except ValueError as exc:
        name = str(exc) if str(exc) in ("ROOM_ID_INVALID", "ROOM_PATH_INVALID") else "ROOM_PATH_INVALID"
        return web.json_response(
            {"ok": False, "error": name, "error_name": name},
            status=400, headers=cors,
        )
    return web.json_response({
        "ok": True,
        "room_id": result["room_id"],
        "room_token": result["room_token"],
        "expires_at": result["expires_at"],
    }, headers=cors)


async def handle_council_room_status(request):
    """GET /api/council/room/{rid}/status  token via X-Room-Token header or ?token=.
    STEP 3a: read status.json (if any) + light _room_validate_token; returns
    state / worker_alive (recomputed via _pid_alive) / artifact_path.
    Bad id -> ROOM_PATH_INVALID (400); unknown room -> ROOM_NOT_FOUND (404);
    bad/expired token -> ROOM_TOKEN_INVALID (403)."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response(
            {"ok": False, "error": "blocked", "error_name": "ORIGIN_BLOCKED"},
            status=403, headers=cors,
        )
    room_id = str(request.match_info.get("rid") or "")
    try:
        room_dir = _room_resolve_dir(room_id)
    except ValueError:
        return web.json_response(
            {"ok": False, "error": "room id invalid",
             "error_name": "ROOM_PATH_INVALID"},
            status=400, headers=cors,
        )

    def _load():
        meta = _room_read_json(room_dir / "room_meta.json", None)
        status = _room_read_json(room_dir / "status.json", {})
        if not isinstance(status, dict):
            status = {}
        worker_pid = status.get("worker_pid")
        alive = bool(worker_pid and _pid_alive(worker_pid))
        return meta, status, alive

    meta, status, worker_alive = await asyncio.to_thread(_load)
    if not meta:
        return web.json_response(
            {"ok": False, "error": "room not found",
             "error_name": "ROOM_NOT_FOUND"},
            status=404, headers=cors,
        )
    room_token = (request.headers.get("X-Room-Token")
                  or request.query.get("token") or "")
    try:
        _room_validate_token(room_id, room_token)
    except PermissionError:
        return web.json_response(
            {"ok": False, "error": "room token invalid or expired",
             "error_name": "ROOM_TOKEN_INVALID"},
            status=403, headers=cors,
        )
    return web.json_response({
        "ok": True,
        "room_id": room_id,
        "state": status.get("state") or status.get("status") or "registered",
        "worker_alive": worker_alive,
        "artifact_path": status.get("artifact_path"),
        "expires_at": meta.get("expires_at"),
        "task_id": status.get("task_id"),
    }, headers=cors)
from orchestrator import git_tools as _git_tools

# A31-1 (2026-09-17). Bounded so a review can never hold a git call hostage: the MCP
# proxy dies at ~180-195s and ocr_review's own budget is 120s, so 25s is the ceiling
# that keeps a stage/commit answering in git time, not review time.
_OCR_ATTACH_TIMEOUT_S = 25.0
_OCR_ATTACH_FILE_CAP = 20


async def _attach_ocr_review(out, project_path, commit=""):
    """Run the zero-LLM OCR delegate review and attach it to a git payload.

    `ocr_review`'s own docstring is "delegate preview: which changed files are worth
    reviewing. Zero LLM." -- so this spends one subprocess, never a model call. It is
    attached only AFTER the git call already returned, is bounded by `wait_for`, and
    swallows every exception: a stage or a commit must never fail because a review did.
    This is the async layer on purpose -- `git_tools.stage/commit` are sync and run on
    an executor thread, where there is no loop to await into.
    """
    if not isinstance(out, dict) or not out.get("ok"):
        return out
    if not project_path:
        out["ocr_review"] = {"state": "skipped", "reason": "project_path_not_given"}
        return out
    try:
        from orchestrator.tools.ocr_review import ocr_review as _ocr_review
        _kw = {"project_path": project_path}
        if commit:
            _kw["commit"] = commit
        _raw = await asyncio.wait_for(_ocr_review(**_kw), timeout=_OCR_ATTACH_TIMEOUT_S)
        _data = json.loads(_raw) if isinstance(_raw, str) else _raw
    except asyncio.TimeoutError:
        out["ocr_review"] = {"state": "timeout", "timeout_s": _OCR_ATTACH_TIMEOUT_S}
        return out
    except Exception as _exc:
        out["ocr_review"] = {"state": "unavailable", "reason": "%s: %s" % (type(_exc).__name__, _exc)}
        return out
    if not isinstance(_data, dict) or not _data.get("ok"):
        _why = (_data or {}).get("error_name") or (_data or {}).get("error") or "ocr_review_not_ok"
        out["ocr_review"] = {"state": "unavailable", "reason": str(_why)}
        return out
    _block = {"state": "ok"}
    for _k in ("mode", "total_files", "reviewable_count", "excluded_count", "total_insertions", "total_deletions"):
        if _k in _data:
            _block[_k] = _data[_k]
    _files = _data.get("reviewable_files")
    if isinstance(_files, list):
        _block["reviewable_files"] = _files[:_OCR_ATTACH_FILE_CAP]
        # Same contract as the A30-B scan cap: declare the drop ONLY when there was one,
        # so the declaration still means something on the run where it matters.
        if len(_files) > _OCR_ATTACH_FILE_CAP:
            _block["files_omitted"] = len(_files) - _OCR_ATTACH_FILE_CAP
            _block["max_files"] = _OCR_ATTACH_FILE_CAP
    out["ocr_review"] = _block
    return out
async def git_status(project_path=None, include_ignored=False, max_records=100, compact_untracked=True): return json.dumps(await run_in_executor_lane("git", _git_tools.safe_call, _git_tools.status, project_path, include_ignored, max_records, compact_untracked), ensure_ascii=False)
async def git_stale_check(project_path=None, max_commit_age_hours=168, expected_state_hash=""): return json.dumps(await run_in_executor_lane("git", _git_tools.safe_call, _git_tools.stale_check, project_path, max_commit_age_hours, expected_state_hash), ensure_ascii=False)
async def git_diff(project_path=None, scope="worktree", paths=None, context_lines=3, max_chars=50000, check_whitespace=False): return json.dumps(await run_in_executor_lane("git", _git_tools.safe_call, _git_tools.diff, project_path, scope, paths, context_lines, max_chars, check_whitespace), ensure_ascii=False)
async def git_log(project_path=None, limit=20, ref="HEAD"): return json.dumps(await run_in_executor_lane("git", _git_tools.safe_call, _git_tools.log, project_path, limit, ref), ensure_ascii=False)
async def git_stage(project_path=None, paths=None, paths_file="", allow_all=False, expected_state_hash="", file_path=None, ocr=True):
    # R36 alias: CAs habitually send singular `file_path` instead of `paths`.
    # Map it onto the canonical `paths=[file_path]` so the call actually stages
    # the file instead of answering UNSUPPORTED_FIELD.
    if file_path and not paths:
        paths = [file_path]
    out = await run_in_executor_lane("git", _git_tools.safe_call, _git_tools.stage, project_path, paths, paths_file, allow_all, expected_state_hash)
    if ocr:
        # No ref: `ocr_review` answers mode "workspace" and reads the UNCOMMITTED tree,
        # which is exactly the content that was just staged but not yet committed.
        out = await _attach_ocr_review(out, project_path)
    return json.dumps(out, ensure_ascii=False)
async def git_unstage(project_path=None, paths=None, paths_file="", allow_all=False, expected_staged_hash=""): return json.dumps(await run_in_executor_lane("git", _git_tools.safe_call, _git_tools.unstage, project_path, paths, paths_file, allow_all, expected_staged_hash), ensure_ascii=False)
async def git_commit(project_path=None, message=None, expected_staged_hash="", allow_empty=False, no_verify=False, ocr=True):
    out = await run_in_executor_lane("git", _git_tools.safe_call, _git_tools.commit, project_path, message, expected_staged_hash, allow_empty, no_verify)
    if ocr:
        # The commit already exists by the time we get here, so the review is attached
        # to a range that really came into being -- and cannot un-commit it.
        _head = out.get("commit") if isinstance(out, dict) else None
        out = await _attach_ocr_review(out, project_path, commit=str(_head or ""))
    return json.dumps(out, ensure_ascii=False)

from orchestrator.core.registry import register_tool as _register_tool

ct.PROTECTED_TOOL_NAMES.update({"git_stage", "git_unstage", "git_commit"})
_GIT_TOOL_SPECS = [
("git_status",git_status,"T1",False,False,"none",{}, {"project_path":{"type":"string","description":"Absolute Git repository root"},"include_ignored":{"type":"boolean","description":"Include ignored records","default":False},"max_records":{"type":"integer","description":"Cap displayed records (default 100; 0=all displayed rows). Counts/state_hash/staged_hash always cover all files. For every individual path use compact_untracked=false,max_records=0.","default":100},"compact_untracked":{"type":"boolean","description":"Group untracked descendants by top-level directory with untracked_count; presentation only, no ignore rules or file changes. Set false for individual paths.","default":True}},["project_path"]),
("git_stale_check",git_stale_check,"T1",False,False,"none",{}, {"project_path":{"type":"string","description":"Absolute Git repository root"},"max_commit_age_hours":{"type":"number","description":"Old HEAD threshold in hours","default":168},"expected_state_hash":{"type":"string","description":"Prior git_status state_hash for drift detection"}},["project_path"]),
("git_diff",git_diff,"T1",False,False,"none",{"files":"paths"}, {"project_path":{"type":"string","description":"Absolute Git repository root"},"scope":{"type":"string","enum":["worktree","staged","both"],"description":"Diff scope","default":"worktree"},"paths":{"type":"array","items":{"type":"string"},"description":"Relative pathspec allowlist"},"context_lines":{"type":"integer","description":"Unified context lines","default":3},"max_chars":{"type":"integer","description":"Output cap","default":50000},"check_whitespace":{"type":"boolean","description":"Run git diff --check semantics","default":False}},["project_path"]),
("git_log",git_log,"T1",False,False,"none",{}, {"project_path":{"type":"string","description":"Absolute Git repository root"},"limit":{"type":"integer","description":"Maximum commits","default":20},"ref":{"type":"string","description":"Revision reference","default":"HEAD"}},["project_path"]),
("git_stage",git_stage,"T2",True,True,"file_artifact",{"files":"paths"}, {"project_path":{"type":"string","description":"Absolute Git repository root"},"paths":{"type":"array","items":{"type":"string"},"description":"Relative paths to stage"},"paths_file":{"type":"string","description":"UTF-8 newline-delimited relative paths; alternative to paths"},"allow_all":{"type":"boolean","description":"Explicitly stage all changes","default":False},"expected_state_hash":{"type":"string","description":"Optional pre-stage git_status state_hash"}},["project_path"]),
("git_unstage",git_unstage,"T2",True,True,"file_artifact",{"files":"paths"}, {"project_path":{"type":"string","description":"Absolute Git repository root"},"paths":{"type":"array","items":{"type":"string"},"description":"Relative paths to unstage"},"paths_file":{"type":"string","description":"UTF-8 newline-delimited relative paths; alternative to paths"},"allow_all":{"type":"boolean","description":"Explicitly unstage all","default":False},"expected_staged_hash":{"type":"string","description":"Optional prior staged_hash"}},["project_path"]),
("git_commit",git_commit,"T2",True,True,"repository_history",{"msg":"message"}, {"project_path":{"type":"string","description":"Absolute Git repository root"},"message":{"type":"string","description":"Commit message"},"expected_staged_hash":{"type":"string","description":"git_status staged_hash, or \"auto\" to accept whatever is staged right now. Enforced by the git layer, not the schema: omitting it answers EXPECTED_STAGED_HASH_REQUIRED and carries current_staged_hash, so the corrected retry needs no second call."},"allow_empty":{"type":"boolean","description":"Allow empty commit","default":False},"no_verify":{"type":"boolean","description":"Bypass hooks only when explicitly authorized","default":False}},["project_path","message"]),
]
# A31-1: `git_stage`/`git_commit` now really RUN the zero-LLM delegate review and
# attach it as `ocr_review`; the A30-B `ca_hint_review` string stays as the fallback
# for the paths where a review could not run. The prop is declared once here instead
# of inline in the two rows above so its text cannot drift between the two tools --
# and so those two long spec rows stay byte-identical.
_OCR_ATTACH_PROP = {"type": "boolean", "description": "Run the zero-LLM open-code-review delegate pass and attach it as ocr_review. It can never fail the git operation: on error or timeout the key carries state unavailable/timeout instead. Pass false to skip the subprocess.", "default": True}
for _gspec in _GIT_TOOL_SPECS:
    if _gspec[0] in ("git_stage", "git_commit"):
        _gspec[7]["ocr"] = dict(_OCR_ATTACH_PROP)
for _n,_h,_p,_tok,_sess,_scope,_aliases,_props,_req in _GIT_TOOL_SPECS:
    _register_tool(name=_n,handler=_h,phase=_p,tool_map=ct.TOOL_MAP,tool_schemas=ct.TOOL_SCHEMAS,tool_phases=ct.TOOL_PHASES,arg_aliases=_ARG_ALIASES,phase_labels=ct.PHASE_LABELS,aliases=_aliases,schema={"description":f"Governed Git operation: {_n} (argv-only, no shell).","parameters":{"type":"object","properties":_props,"required":_req}},requires_token=_tok,requires_session=_sess,mutation_scope=(None if _scope == "none" else _scope),token_required_set=_TOKEN_REQUIRED_TOOLS,mutating_set=_MUTATING_TOOLS,mutation_scope_map=getattr(ct,"_MUTATION_SCOPE_MAP",None))
import tools_v15 as _v15
from orchestrator.core.registry_data import AUDIT_RECONCILIATION_SCHEMA

_register_tool(
    name="audit_reconciliation_report", handler=_v15.audit_reconciliation_report,
    phase="T1", tool_map=ct.TOOL_MAP, tool_schemas=ct.TOOL_SCHEMAS,
    tool_phases=ct.TOOL_PHASES, arg_aliases=_ARG_ALIASES,
    phase_labels=ct.PHASE_LABELS, schema=AUDIT_RECONCILIATION_SCHEMA,
    requires_token=False, requires_session=False, mutation_scope=None,
    token_required_set=_TOKEN_REQUIRED_TOOLS, mutating_set=_MUTATING_TOOLS,
    mutation_scope_map=getattr(ct, "_MUTATION_SCOPE_MAP", None))

async def council_cron_job_list(job_id=None, limit=50):
    try:
        if job_id: return json.dumps({"ok": True, "job": _COUNCIL_CRON_SERVICE.get_job(str(job_id)), "runs": _COUNCIL_CRON_SERVICE.list_runs(str(job_id), int(limit))}, ensure_ascii=False)
        return json.dumps({"ok": True, "jobs": _COUNCIL_CRON_SERVICE.list_jobs(), "runs": _COUNCIL_CRON_SERVICE.list_runs(None, int(limit))}, ensure_ascii=False)
    except Exception as exc: return json.dumps({"ok": False, "error_name": getattr(exc, "error_name", "CRON_JOB_ERROR"), "error": str(exc)}, ensure_ascii=False)


async def council_cron_job_save(name=None, worker=None, model=None, provider=None, prompt=None, schedule_type="interval", schedule_value="1h", enabled=True, narrator_enabled=False, narrator_worker=None, narrator_model=None, narrator_provider=None, narrator_prompt="", timeout_sec=3600, job_id=None):
    payload = {"name": name, "worker": worker, "workers": [worker] if worker else [], "worker_model": model or "", "worker_provider": provider or "", "prompt": prompt, "schedule_type": schedule_type, "schedule_value": schedule_value, "enabled": bool(enabled), "narrator_enabled": bool(narrator_enabled), "narrator_worker": narrator_worker or worker, "narrator_model": narrator_model or "", "narrator_provider": narrator_provider or "", "narrator_prompt": narrator_prompt or "", "timeout_sec": timeout_sec}
    try:
        job = _COUNCIL_CRON_SERVICE.update_job(str(job_id), payload) if job_id else _COUNCIL_CRON_SERVICE.create_job(payload)
        return json.dumps({"ok": True, "job": job}, ensure_ascii=False)
    except Exception as exc: return json.dumps({"ok": False, "error_name": getattr(exc, "error_name", "CRON_JOB_ERROR"), "error": str(exc)}, ensure_ascii=False)


async def council_cron_job_delete(job_id=None):
    try: _COUNCIL_CRON_SERVICE.delete_job(str(job_id)); return json.dumps({"ok": True, "deleted": str(job_id)}, ensure_ascii=False)
    except Exception as exc: return json.dumps({"ok": False, "error_name": getattr(exc, "error_name", "CRON_JOB_ERROR"), "error": str(exc)}, ensure_ascii=False)


async def council_cron_job_trigger(job_id=None):
    try: return json.dumps({"ok": True, "run": await _COUNCIL_CRON_SERVICE.trigger_job(str(job_id))}, ensure_ascii=False)
    except Exception as exc: return json.dumps({"ok": False, "error_name": getattr(exc, "error_name", "CRON_JOB_ERROR"), "error": str(exc)}, ensure_ascii=False)


from orchestrator.core.registry import register_tool as _register_tool

for _cron_name, _cron_handler, _cron_phase, _cron_desc, _cron_props, _cron_req in [
    ("council_cron_job_list", council_cron_job_list, "T1", "List/get durable council cron jobs and run history.", {"job_id":{"type":"string"},"limit":{"type":"integer","default":50}}, []),
    ("council_cron_job_save", council_cron_job_save, "T3", "Create/update a durable council cron job. Usable by CA/leader/HTTP-only workers. Pins worker+model and optional narrator+model.", {"job_id":{"type":"string"},"name":{"type":"string"},"worker":{"type":"string"},"model":{"type":"string"},"provider":{"type":"string"},"prompt":{"type":"string"},"schedule_type":{"type":"string","description":"interval|cron"},"schedule_value":{"type":"string"},"enabled":{"type":"boolean"},"narrator_enabled":{"type":"boolean"},"narrator_worker":{"type":"string"},"narrator_model":{"type":"string"},"narrator_provider":{"type":"string"},"narrator_prompt":{"type":"string"},"timeout_sec":{"type":"integer"}}, ["name","worker","model","prompt","schedule_type","schedule_value"]),
    ("council_cron_job_delete", council_cron_job_delete, "T3", "Soft-delete a durable council cron job; history remains.", {"job_id":{"type":"string"}}, ["job_id"]),
    ("council_cron_job_trigger", council_cron_job_trigger, "T3", "Trigger a configured council cron job immediately with overlap prevention.", {"job_id":{"type":"string"}}, ["job_id"]),
]:
    _register_tool(name=_cron_name, handler=_cron_handler, phase=_cron_phase, tool_map=ct.TOOL_MAP, tool_schemas=ct.TOOL_SCHEMAS, tool_phases=ct.TOOL_PHASES, arg_aliases=_ARG_ALIASES, phase_labels=ct.PHASE_LABELS, schema={"description":_cron_desc,"parameters":{"type":"object","properties":{key: (value if value.get("description") else dict(value, description=key.replace("_", " "))) for key, value in _cron_props.items()},"required":_cron_req}})


_COUNCIL_NARRATOR_DEFAULT_PROMPT = (
    "Bạn là WORKER TƯỜNG THUẬT của hội đồng (council narrator). KHÔNG echo/lặp lại nội "
    "dung này. Đọc diễn tiến council bên dưới và trả về BẢN TƯỜNG THUẬT có CẤU TRÚC bằng "
    "tiếng Việt, đúng 3 mục theo mẫu (không thêm mục khác, không ghi file):\n"
    "TÓM TẮT: <1-2 câu tổng quan hội đồng đang làm gì>\n"
    "LUỒNG:\n- <ai> → <ai>: <1 câu việc gì> (theo thứ tự thời gian)\n"
    "ĐIỂM TƯỜNG THUẬT:\n- <bullet ngắn về nội dung/tiến độ/kết quả/điểm chặn>"
)


def _council_recent_transcript(max_events: int = 12) -> str:
    """Compact recent council transcript from the broker log — message/reply lines
    only, bounded per line — for the narrator/commentary worker to summarize."""
    p = _HERE / ".council" / "broker_filtered.jsonl"
    if not p.exists():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    out: list[str] = []
    for ln in lines[-(max_events * 3):]:
        try:
            d = json.loads(ln)
        except Exception:
            continue
        typ = d.get("type")
        if typ not in ("message", "reply"):
            continue
        who = str(d.get("from", "?"))
        to = str(d.get("to", "?"))
        txt = " ".join(str(d.get("text", "")).split())[:400]
        tag = "MSG" if typ == "message" else "REPLY"
        out.append(f"[{tag}] {who}->{to}: {txt}")
    return "\n".join(out[-max_events:])


async def handle_council_commentary(request):
    """POST /api/council/commentary {agent, model, provider?, events?} — on-demand
    narrator. Runs the user-chosen worker DIRECTLY (not through the broker queue) so
    the narration and its hidden prompt NEVER enter the council transcript; the reply
    goes only to the commentary side-bar card. Requires an explicit model. Localhost
    + origin guarded. No auto-activation; button-driven only."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"ok": False, "error": "blocked"}, status=403, headers=cors)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    agent = str(body.get("agent", "")).strip().lower()
    if agent not in _COUNCIL_AGENTS:
        return web.json_response({"ok": False, "error_name": "INVALID_AGENT",
                                  "error": "chọn 1 worker đã đăng ký làm người tường thuật",
                                  "allowed": list(_COUNCIL_AGENTS)}, status=400, headers=cors)
    # Require an explicit model (user asked: "đã chọn worker nhưng chưa chọn model").
    model = str(body.get("model", "")).strip()
    provider = str(body.get("provider", "")).strip()
    if not model:
        eff = _council_effective_model(agent)
        model = str(eff.get("model") or "")
        provider = provider or str(eff.get("provider") or "")
    if not model:
        return web.json_response({"ok": False, "error_name": "MODEL_NOT_SELECTED",
                                  "error": "đã chọn worker nhưng chưa chọn model cho người tường thuật"},
                                 status=400, headers=cors)
    valid = _council_valid_models(agent)
    if valid is not None and model not in valid:
        return web.json_response({"ok": False, "error_name": "INVALID_MODEL",
                                  "error": f"model không hợp lệ cho {agent}: {model}", "valid": valid},
                                 status=400, headers=cors)
    provider = provider or _council_provider_for_model(agent, model)
    # Busy guard: never spawn a 2nd CLI for an agent already doing real council work.
    try:
        if _council_runtime_status(agent).get("state") == "running":
            return web.json_response(
                {"ok": False, "error_name": "AGENT_BUSY",
                 "error": f"{agent} đang bận việc council — chọn worker khác để tường thuật"},
                status=409, headers=cors)
    except Exception:
        pass
    try:
        max_events = max(4, min(30, int(body.get("events", 14))))
    except Exception:
        max_events = 14
    transcript = _council_recent_transcript(max_events)
    if not transcript.strip():
        return web.json_response(
            {"ok": True, "agent": agent, "model": model,
             "commentary": "(council chưa có hoạt động nào để tường thuật)"}, headers=cors)
    # User-editable narrator instruction (persisted client-side, sent each click).
    # Falls back to the default 3-section template when empty. The transcript is
    # always appended by the server so the custom prompt is instruction-only.
    instruction = str(body.get("prompt", "")).strip() or _COUNCIL_NARRATOR_DEFAULT_PROMPT
    prompt = (
        instruction
        + "\n=== DIỄN TIẾN COUNCIL (mới nhất ở dưới) ===\n" + transcript
    )
    req_id = f"commentary-{uuid.uuid4().hex[:10]}"
    runtime_key = _council_runtime_key(agent, model)
    try:
        result = await _council_run_blocking(
            _council_run_agent, agent, prompt, 180, "broker", "",
            req_id, runtime_key, provider, model)
        raw = result.get("reply") or ""
        clean = _council_broker_visible_reply(_council_strip_prompt_echo(raw, prompt)) or ""
        commentary = clean.strip() or (result.get("error") or "(không có phản hồi)")
        return web.json_response(
            {"ok": bool(result.get("ok")) and bool(clean.strip()), "agent": agent, "model": model,
             "commentary": commentary, "generated_at": time.strftime("%H:%M:%S")}, headers=cors)
    except Exception as exc:
        return web.json_response({"ok": False, "agent": agent, "model": model,
                                  "error": str(exc)[:200]}, status=500, headers=cors)


async def handle_council_send_async(request):
    """POST /api/council/send-async {sender,to,text,timeout_sec} — fire-and-forget
    worker->worker dispatch that ALWAYS runs on the long-lived daemon event loop, so
    it survives even when the original caller is a worker's transient MCP-server
    process. The reply is logged to the broker (read by the worker); this returns an
    ack immediately. Localhost+origin guarded."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"ok": False, "error": "blocked"}, status=403, headers=cors)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    # CAN-020: trust the relayed sender ONLY for the internal daemon relay (it carries
    # the mutation token); any other caller's declared sender is resolved server-side
    # so /api/council/send-async cannot be used to spoof a worker identity directly.
    if request.headers.get("X-Orchestrator-Token") == EXPECTED_TOKEN:
        sender = (str(body.get("sender", "")).strip() or "worker")[:40]
    else:
        sender = (_council_resolve_sender(request, str(body.get("sender", ""))) or "worker")[:40]
    target = str(body.get("to", "")).strip().lower()
    text, source_error = _council_message_source(body.get("text"), body.get("body_file"))
    if source_error:
        return web.json_response(source_error, status=400, headers=cors)
    try:
        timeout_sec = int(body.get("timeout_sec", 6000) or 6000)
    except Exception:
        timeout_sec = 6000
    # P2 caller correlation id, sanitized before active-duplicate admission.
    _req_id = _council_sanitize_request_id(body.get("request_id", "")) or f"run-{uuid.uuid4()}"
    if target not in _COUNCIL_AGENTS or sender.lower() == target:
        return web.json_response({"ok": False, "error_name": "INVALID_ASYNC_SEND",
                                  "error": "need a valid distinct worker target + message"}, status=400, headers=cors)
    if not _council_worker_model_configured(target, str(body.get("model", "")).strip()):
        return web.json_response({"ok": False, "error_name": "WORKER_MODEL_NOT_SET", "agent": target,
                                  "ca_next_action": "council_worker_model set+get before dispatch"}, status=400, headers=cors)
    # A32(a): resolve the model BEFORE the ack so an unavailable model fails loud here
    # instead of being swallowed inside _bg (WORKER_MODEL_NOT_SET only tests non-emptiness).
    _preack_error = _council_preack_resolution_error(
        target, text, str(body.get("provider", "")).strip(), str(body.get("model", "")).strip())
    if _preack_error:
        return web.json_response(_preack_error, status=400, headers=cors)
    # Trusted internal relay: its caller records acceptance only after this ack.
    record_here = not (request.headers.get("X-Orchestrator-Token") == EXPECTED_TOKEN
                       and body.get("record_ledger") is False)
    if record_here:
        duplicate = _council_accept_dispatch(_req_id, sender, target, timeout_sec)
        if duplicate:
            return web.json_response(duplicate, status=409, headers=cors)
    async def _bg():
        try:
            _result = await _council_enqueue_or_run(
                target, sender, text, timeout_sec, "broker", "", queue=True,
                queue_wait_timeout_sec=timeout_sec,
                provider=str(body.get("provider", "")).strip(), model=str(body.get("model", "")).strip(),
                request_id=_req_id)
            # A32(b): the callee RETURNS terminal errors instead of raising, so the
            # except below never saw them; inspect + persist a terminal record.
            _council_record_async_result(
                _req_id, sender, target, timeout_sec, _result)
        except Exception as exc:
            logger.error("async council dispatch to %s from %s failed: %r",
                         target, sender, exc, exc_info=True)
            _council_reject_dispatch(_req_id, sender, target, timeout_sec,
                                     f"{type(exc).__name__}: {exc}")
    _t = asyncio.create_task(_bg())
    _COUNCIL_PENDING_TASKS.add(_t)
    _t.add_done_callback(_COUNCIL_PENDING_TASKS.discard)
    return web.json_response({"ok": True, "dispatched": True, "from": sender, "to": target,
                              "request_id": _req_id, "dispatch_id": _req_id,
                              "note": "dispatched async; reply appears in .council/broker_filtered.jsonl"}, headers=cors)


async def handle_council_live_raw_clear(request):
    """POST /api/council/live-raw-clear {agent} — truncate that worker's live raw
    capture file so the dashboard panel can be cleared on demand. Localhost+origin."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"ok": False, "error": "blocked"}, status=403, headers=cors)
    try:
        body = await request.json()
    except Exception:
        body = {}
    agent = str((body or {}).get("agent", "")).strip().lower()
    if agent not in _COUNCIL_AGENTS:
        return web.json_response({"ok": False, "error_name": "INVALID_AGENT",
                                  "allowed": list(_COUNCIL_AGENTS)}, status=400, headers=cors)
    try:
        (_HERE / ".council" / f"live_raw_{agent}.log").write_text("", encoding="utf-8")
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc),
                                  "hint": "file may be locked by a live run; retry when idle"},
                                 headers=cors)
    return web.json_response({"ok": True, "agent": agent, "cleared": True}, headers=cors)


async def council_message(sender=None, to=None, text=None, body_file=None, timeout_sec=6000, queue=True, no_queue=False, queue_wait_timeout_sec=6000,
                          provider="", model="", fallback_policy="none", allowed_fallbacks=None,
                          q_context=False, q_zones="", async_dispatch=False):
    """Send one inline or allowlisted file-backed message through the shared dispatch core."""
    resolved_text, source_error = _council_message_source(text, body_file)
    if source_error:
        return json.dumps(source_error, ensure_ascii=False)
    result = await _council_dispatch_message(
        sender or "worker", to or "", resolved_text, timeout_sec,
        queue=_council_queue_enabled({"queue": queue, "no_queue": no_queue}),
        queue_wait_timeout_sec=queue_wait_timeout_sec,
        provider=provider or "", model=model or "",
        fallback_policy=fallback_policy, allowed_fallbacks=allowed_fallbacks,
        q_context=bool(q_context), q_zones=str(q_zones or ""),
        async_dispatch=_council_bool_param(async_dispatch, False),
    )
    return json.dumps(result, ensure_ascii=False)


# Late registration goes through the SINGLE pipeline (ADR R3 §D1.3) — it stamps
# x_phase/x_phase_label and keeps map/schema/phase/alias in lockstep. Direct
# mutation of ct.TOOL_MAP/TOOL_SCHEMAS/TOOL_PHASES outside register_tool is the
# drift bug class that broke the contract tests; do not reintroduce it.
from orchestrator.core.registry import register_tool as _register_tool

_register_tool(
    name="council_message",
    handler=council_message,
    phase="T1",
    tool_map=ct.TOOL_MAP,
    tool_schemas=ct.TOOL_SCHEMAS,
    tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES,
    phase_labels=ct.PHASE_LABELS,
    aliases={"from": "sender", "frm": "sender", "from_agent": "sender",
             "message": "text", "msg": "text", "recipient": "to", "to_agent": "to",
             "async": "async_dispatch", "non_blocking": "async_dispatch"},
    schema={
        "description": "Send a message to another council worker (or the user) and get the reply. "
                       "[A] auto-handles dispatch + broker logging. Structured args only — never run shell/curl/JSON.",
        "parameters": {
            "type": "object",
            "properties": {
                "sender": {"type": "string", "description": f"Your agent id, council namespace: {'|'.join(_COUNCIL_AGENTS)} (alias: from). Rendered from the live .council/council_config.json roster."},
                "to": {"type": "string", "description": f"Recipient: {'|'.join(_COUNCIL_AGENTS)}|user -- rendered from the live .council/council_config.json roster. A council id is NOT an agents_registry.json id: registry 'opencode' routes as 'ope' (its route_id)."},
                "text": {"type": "string", "description": "Inline message; exactly one of text or body_file"},
                "body_file": {"type": "string", "description": "Allowlisted UTF-8 .council/briefs/*.md or *.txt; exactly one of text or body_file"},
                "timeout_sec": {"type": "integer", "default": 6000, "description": "Max wait for the reply"},
                "provider": {"type": "string", "description": "Optional per-dispatch provider override. Prefer leaving blank so [A] derives it from model."},
                "model": {"type": "string", "description": "Optional per-dispatch model override. Do not send a slot field; use agent/provider/model only."},
                "fallback_policy": {"type": "string", "enum": ["none", "explicit"], "default": "none", "description": "Low-risk fallback is disabled unless explicitly declared."},
                "allowed_fallbacks": {"type": "array", "items": {"type": "string"}, "default": [], "description": "Caller allowlist used only with fallback_policy=explicit on non-high-risk routes."},
                "queue": {"type": "boolean", "default": True, "description": "Queue by default when the target is busy; set false for fail-fast AGENT_BUSY"},
                "no_queue": {"type": "boolean", "default": False, "description": "Legacy fail-fast opt-out; true returns AGENT_BUSY instead of enqueueing"},
                "queue_wait_timeout_sec": {"type": "integer", "default": 6000, "description": "Max caller wait while this message is queued"},
                "async_dispatch": {"type": "boolean", "default": False, "description": "P2: true = non-blocking fire-and-forget (acks immediately with request_id; reply lands in broker_filtered.jsonl). Use for leader parallel fan-out to avoid HTTP headers timeout. Aliases: async, non_blocking. Default false keeps the classic sync reply."},
                "q_context": {"type": "boolean", "default": False, "description": "Combo-1 opt-in: [A] pre-fetches 3 compact [Q] memory hits and appends them to the worker prompt (fail-open if [Q] is down)"},
                "q_zones": {"type": "string", "default": "", "description": "Optional csv zone filter for q_context lookup, e.g. 'Z2' (rules/skills/tool-catalog) for how-to tasks; empty = all zones"},
            },
            "required": ["to"],
        },
    },
)


async def council_message_async(sender="user", to=None, text=None, body_file=None, timeout_sec=3600,
                                 provider="", model="", fallback_policy="none", allowed_fallbacks=None,
                                 queue_wait_timeout_sec=0, request_id=""):
    """Non-blocking HTTP council dispatch with shared safe message resolution."""
    target = str(to or "").strip().lower()
    text_val, source_error = _council_message_source(text, body_file)
    if source_error:
        return json.dumps(source_error, ensure_ascii=False)
    sender_val = (str(sender or "").strip() or "user")[:40]
    if target not in _COUNCIL_AGENTS or sender_val.lower() == target:
        return json.dumps({"ok": False, "error_name": "INVALID_ASYNC_SEND",
                           "error": "need a valid distinct worker target + message",
                           "allowed_targets": sorted(_COUNCIL_AGENTS)}, ensure_ascii=False)
    if not _council_worker_model_configured(target, str(model or "").strip()):
        return json.dumps({"ok": False, "error_name": "WORKER_MODEL_NOT_SET", "agent": target,
                           "ca_next_action": "council_worker_model set+get before dispatch"},
                          ensure_ascii=False)
    try:
        wait_sec = int(timeout_sec or 3600)
    except (TypeError, ValueError):
        wait_sec = 3600
    try:
        queue_wait = int(queue_wait_timeout_sec or 0) or wait_sec
    except (TypeError, ValueError):
        queue_wait = wait_sec
    # A32(a): resolve BEFORE the ack (and before the accepted ledger row) so an
    # unavailable model fails loud instead of vanishing inside _bg.
    _preack_error = _council_preack_resolution_error(
        target, text_val, str(provider or "").strip(), str(model or "").strip(),
        fallback_policy, allowed_fallbacks)
    if _preack_error:
        return json.dumps(_preack_error, ensure_ascii=False)
    # P2 correlation id; active reuse is rejected before a second task is scheduled.
    _req_id = _council_sanitize_request_id(request_id) or f"run-{uuid.uuid4()}"
    duplicate = _council_accept_dispatch(_req_id, sender_val, target, queue_wait)
    if duplicate:
        return json.dumps(duplicate, ensure_ascii=False)

    async def _bg():
        try:
            _result = await _council_enqueue_or_run(
                target, sender_val, text_val, wait_sec, "broker", "",
                queue=True, queue_wait_timeout_sec=queue_wait,
                provider=str(provider or "").strip(), model=str(model or "").strip(),
                request_id=_req_id, fallback_policy=fallback_policy,
                allowed_fallbacks=allowed_fallbacks)
            # A32(b): callee returns terminal errors instead of raising — record them.
            _council_record_async_result(
                _req_id, sender_val, target, queue_wait, _result)
        except Exception as exc:
            logger.error("async council dispatch (HTTP tool) to %s from %s failed: %r",
                         target, sender_val, exc, exc_info=True)
            _council_reject_dispatch(_req_id, sender_val, target, queue_wait,
                                     f"{type(exc).__name__}: {exc}")

    _task = asyncio.create_task(_bg())
    _COUNCIL_PENDING_TASKS.add(_task)
    _task.add_done_callback(_COUNCIL_PENDING_TASKS.discard)
    return json.dumps({"ok": True, "dispatched": True, "from": sender_val, "to": target,
                       "request_id": _req_id, "dispatch_id": _req_id,
                       "note": "dispatched async; reply appears in .council/broker_filtered.jsonl - poll council_tracker for progress"},
                      ensure_ascii=False)


_register_tool(
    name="council_message_async",
    handler=council_message_async,
    phase="T1",
    tool_map=ct.TOOL_MAP,
    tool_schemas=ct.TOOL_SCHEMAS,
    tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES,
    phase_labels=ct.PHASE_LABELS,
    aliases={"from": "sender", "frm": "sender", "from_agent": "sender",
             "message": "text", "msg": "text", "recipient": "to", "to_agent": "to"},
    schema={
        "description": "Non-blocking council dispatch (HTTP twin of the MCP council_message_async): "
                       "validates worker+model, dispatches on the daemon loop, returns an ack immediately. "
                       "Reply lands in .council/broker_filtered.jsonl — poll council_tracker. "
                       "Prefer this for multi-worker fan-out; council_message is the sync path.",
        "parameters": {
            "type": "object",
            "properties": {
                "sender": {"type": "string", "description": "Caller id (resolved server-side; alias: from)"},
                "to": {"type": "string", "description": f"Recipient worker, council namespace: {'|'.join(_COUNCIL_AGENTS)} -- rendered from the live .council/council_config.json roster, never a hand-copied list"},
                "text": {"type": "string", "description": "Inline task/message; exactly one of text or body_file"},
                "body_file": {"type": "string", "description": "Allowlisted UTF-8 .council/briefs/*.md or *.txt; exactly one of text or body_file"},
                "timeout_sec": {"type": "integer", "default": 3600, "description": "Worker run timeout"},
                "provider": {"type": "string", "description": "Optional per-dispatch provider override"},
                "model": {"type": "string", "description": "Optional per-dispatch model override"},
                "fallback_policy": {"type": "string", "enum": ["none", "explicit"], "default": "none", "description": "What to do when the requested model is not in the agent's valid set. 'none' fails with REQUESTED_MODEL_UNAVAILABLE; 'explicit' may substitute the first entry of allowed_fallbacks that is valid. Ignored (treated as 'none') when risk_level or dev_level is high/critical."},
                "allowed_fallbacks": {"type": "array", "items": {"type": "string"}, "default": [], "description": "Ordered candidate models the caller pre-approves as substitutes. Only consulted when fallback_policy is 'explicit'; the first valid candidate wins and the ack carries an EXPLICIT_MODEL_FALLBACK warning."},
                "queue_wait_timeout_sec": {"type": "integer", "default": 0, "description": "Max queue wait; 0 = follow timeout_sec"},
                "request_id": {"type": "string", "description": "Optional caller correlation id (echoed in the ack + stamped on the broker message event); auto-generated when empty"},
            },
            "required": ["to"],
        },
    },
)

# ── B4 (2026-08-08) council_status: deterministic dispatch-state resolver + durable record ──
# WHY: council_message_async + council_message(async_dispatch=True) ack immediately with an
# opaque request_id. Callers had NO way to ask "is dispatch run-<uuid> queued / running /
# done / stuck?" without polling the raw broker + runtime logs, which is nondeterministic and
# re-introduced the false-positive "done" lookup (council rule T0 #4: done MUST come from the
# broker reply or runtime terminal, never from an artifact file marker).
# B4 adds a single read-only T1 tool that derives the canonical state by a fixed precedence:
# broker reply (authoritative for ok/fail) -> runtime terminal -> runtime running (pid alive) ->
# queue (ack written, not started) -> in-memory queue (grace) -> recent broker message (grace) ->
# DISPATCH_NOT_FOUND. The durable accepted/queued record closes the restart-gap: the queue
# interval between ack and worker spawn previously left the dispatch invisible after a restart.


_COUNCIL_DISPATCH_LEDGER = "dispatch_ledger.jsonl"


def _council_dispatch_ledger_path() -> Path:
    return _HERE / ".council" / _COUNCIL_DISPATCH_LEDGER


def _council_dispatch_record(dispatch_id: str, sender: str, target: str, request_id: str = "",
                             queue_wait_timeout_sec: int = 0, state: str = "accepted",
                             error: str = "") -> bool:
    """Append one durable lifecycle record after acceptance, or for terminal rejection."""
    try:
        path = _council_dispatch_ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _council_now_iso(), "mtime": time.time(),
            "dispatch_id": str(dispatch_id or ""),
            "request_id": str(request_id or dispatch_id or ""),
            "sender": str(sender or "")[:40], "target": str(target or ""),
            "state": str(state or "accepted"),
            "queue_wait_timeout_sec": int(queue_wait_timeout_sec or 0),
        }
        if error:
            record["error"] = str(error)[:1000]
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return True
    except Exception:
        logger.warning("council dispatch ledger write failed for %s", dispatch_id)
        return False


def _council_dispatch_read_ledger(limit: int = 500) -> list[dict]:
    """Read newest ledger records (bounded). Missing file = empty list."""
    path = _council_dispatch_ledger_path()
    if not path.exists():
        return []
    try:
        return read_jsonl_tail(path, limit=max(1, min(2000, int(limit))), max_bytes=4_000_000)
    except Exception:
        return []


def _council_dispatch_ledger_seen(dispatch_id: str) -> dict:
    """Return the newest ledger record for a dispatch id, or {} when absent."""
    if not dispatch_id:
        return {}
    for rec in reversed(_council_dispatch_read_ledger(500)):
        if isinstance(rec, dict) and str(rec.get("dispatch_id") or "") == dispatch_id:
            return dict(rec)
    return {}


def _council_duplicate_active_error(dispatch_id: str) -> dict | None:
    """Reject correlation ids that already identify non-terminal council work."""
    if not dispatch_id:
        return None
    existing = _council_derive_state(dispatch_id)
    if existing.get("ok") and existing.get("state") in {"dispatching", "working"}:
        return {
            "ok": False, "error_name": "DUPLICATE_ACTIVE_REQUEST_ID",
            "error": f"request_id '{dispatch_id}' already identifies an active dispatch",
            "request_id": dispatch_id, "dispatch_id": dispatch_id,
            "existing_state": existing.get("state"),
            "ca_next_action": "use a new request_id or wait for the active dispatch to finish",
        }
    return None


def _council_accept_dispatch(dispatch_id: str, sender: str, target: str,
                             queue_wait_timeout_sec: int) -> dict | None:
    """Atomically reject an active duplicate or persist one accepted record."""
    with _COUNCIL_DISPATCH_LEDGER_LOCK:
        duplicate = _council_duplicate_active_error(dispatch_id)
        if duplicate:
            return duplicate
        _council_dispatch_record(dispatch_id, sender, target, dispatch_id,
                                 queue_wait_timeout_sec, state="accepted")
    return None


def _council_reject_dispatch(dispatch_id: str, sender: str, target: str,
                             queue_wait_timeout_sec: int, error: str) -> None:
    """Persist terminal evidence for a relay that never crossed acceptance."""
    with _COUNCIL_DISPATCH_LEDGER_LOCK:
        _council_dispatch_record(dispatch_id, sender, target, dispatch_id,
                                 queue_wait_timeout_sec, state="rejected", error=error)


def _council_preack_resolution_error(target: str, text: str, provider: str, model: str,
                                     fallback_policy: str = "none",
                                     allowed_fallbacks=None) -> dict | None:
    """A32(a): resolve the model BEFORE an async ack. Return a real error dict when the
    resolution is unavailable (so a caller never gets ok:true for work that can never
    run), else None. _council_worker_model_configured only tests non-emptiness, so a
    bad id like agent/bogus-model passes it; _council_resolve_model is the gate that
    _council_enqueue_or_run already uses, run early enough to block the ack."""
    metadata = _route_parse_dispatch_metadata(str(text or ""))
    resolution = _council_resolve_model(
        target, str(provider or "").strip(), str(model or "").strip(),
        str(fallback_policy or "none"), allowed_fallbacks,
        metadata.get("risk_level", "unknown"), metadata.get("dev_level", "unknown"))
    if resolution.resolution_status == "unavailable":
        return {"ok": False, "error_name": "COUNCIL_MODEL_UNAVAILABLE",
                "error": "requested model is unavailable", "agent": target,
                **resolution.as_dict()}
    return None


def _council_record_async_result(dispatch_id: str, sender: str, target: str,
                                 queue_wait_timeout_sec: int, result) -> None:
    """A32(b): the async _bg fan-out paths used to discard _council_enqueue_or_run's
    return value. That function RETURNS (does not raise) on terminal statuses
    (COUNCIL_MODEL_UNAVAILABLE / AGENT_BUSY / AGENT_QUEUE_FULL / QUEUE_WAIT_TIMEOUT), so
    the wrapping `except Exception` caught nothing and the failure vanished: no log, no
    ledger terminal row, forever-'dispatching' in council_status. Inspect the dict and
    persist a terminal 'rejected' record so the drop is observable (state 'stuck')."""
    if not isinstance(result, dict) or result.get("ok"):
        return
    error_name = str(result.get("error_name") or "ASYNC_DISPATCH_FAILED")
    error = str(result.get("error") or error_name)
    _council_reject_dispatch(dispatch_id, sender, target, queue_wait_timeout_sec,
                             f"{error_name}: {error}")
    logger.error("async council dispatch %s to %s from %s returned ok:false "
                 "(%s): %s", dispatch_id, target, sender, error_name, error)


def _council_dispatch_files(dispatch_id: str) -> dict:
    """Gather all observable facts for a dispatch id from broker + runtime + ledger + queue.

    Returns a dict with keys:
      dispatch_id, message, reply, runtime, queued_item, ledger, state., reason.,
      has_evidence, last_edit_ts, last_artifact_mtime
    Read-only: no cancellation, no state mutation - the monitor remains cancellation owner."""
    out = {
        "dispatch_id": dispatch_id,
        "message": None,
        "reply": None,
        "runtime": None,
        "queued_item": None,
        "ledger": None,
        "state": None,
        "reason": "",
        "has_evidence": False,
        "last_edit_ts": None,
        "last_artifact_mtime": None,
    }
    if not dispatch_id:
        out["state"] = "error"
        out["reason"] = "dispatch_id required"
        return out

    # 1. Ledger: the durable accepted record written before the ack.
    ledger = _council_dispatch_ledger_seen(dispatch_id)
    if ledger:
        out["ledger"] = ledger
        out["has_evidence"] = True

    # 2. Broker events: newest-first; find the message stamped with this dispatch id and its reply.
    events = list(reversed(_council_read_events(500)))
    message = None
    for ev in events:
        if (isinstance(ev, dict) and ev.get("type") == "message"
                and str(ev.get("request_id") or "") == dispatch_id):
            message = dict(ev)
            break
    reply = None
    if message is not None:
        msg_id = str(message.get("id") or "")
        for ev in events:
            if (isinstance(ev, dict) and ev.get("type") == "reply"
                    and str(ev.get("in_reply_to") or "") == msg_id):
                reply = dict(ev)
                break
    if message is not None:
        out["message"] = message
        out["has_evidence"] = True
    if reply is not None:
        out["reply"] = reply

    # 3. Runtime slot: search persisted per-slot runtime state for a request_id match.
    runtime = None
    try:
        f = _council_runtime_file()
        if f.exists():
            persisted = json.loads(f.read_text(encoding="utf-8")) or {}
            for _k, _v in persisted.items():
                if isinstance(_v, dict) and str(_v.get("request_id") or "") == dispatch_id:
                    _v = dict(_v)
                    _v.setdefault("runtime_key", _k)
                    runtime = _v
                    break
    except Exception:
        pass
    if runtime is not None:
        out["runtime"] = runtime

    # 4. In-memory queue: a not-yet-started item carries this dispatch id.
    queued_item = None
    with _COUNCIL_AGENT_QUEUE_LOCK:
        for _rk, _dq in list(_COUNCIL_AGENT_QUEUES.items()):
            try:
                for _it in _dq:
                    if isinstance(_it, dict) and str(_it.get("request_id") or "") == dispatch_id:
                        queued_item = dict(_it)
                        break
            except Exception:
                continue
            if queued_item is not None:
                break
    if queued_item is not None:
        out["queued_item"] = queued_item

    # 5. last_edit_ts = max(broker message mtime ts, runtime last_output/monitor, artifact mtime).
    last_edit_candidates = []
    if message is not None:
        try:
            last_edit_candidates.append(float(message.get("mtime") or 0.0))
        except Exception:
            pass
    if reply is not None:
        try:
            last_edit_candidates.append(float(reply.get("mtime") or 0.0))
        except Exception:
            pass
    if runtime is not None:
        for fk in ("last_output_at", "last_monitor_at", "finished_at", "started_at"):
            try:
                v = runtime.get(fk)
                if v:
                    last_edit_candidates.append(float(v))
            except Exception:
                pass
    if last_edit_candidates:
        out["last_edit_ts"] = max(last_edit_candidates)

    # 6. Artifact mtime (supporting telemetry only, never the "done" signal).
    artifact_path = ""
    if runtime is not None:
        artifact_path = str(runtime.get("artifact_path") or "").strip()
    if not artifact_path and message is not None:
        artifact_path = _council_extract_artifact_path(str(message.get("text") or "")) or ""
    if artifact_path:
        try:
            ap = Path(artifact_path)
            if ap.exists():
                out["last_artifact_mtime"] = ap.stat().st_mtime
        except Exception:
            pass

    has_ledger = bool(ledger)
    has_message = message is not None
    has_reply = reply is not None
    has_runtime = runtime is not None
    has_queue = queued_item is not None

    # 7. Deterministic precedence.
    if has_reply:
        # Broker reply is authoritative for ok/fail; ok=False -> stuck, ok=True -> done.
        out["state"] = "done" if reply.get("ok") else "stuck"
        out["reason"] = (
            "broker reply ok=%s (in_reply_to=%s)" % (reply.get("ok"), reply.get("in_reply_to"))
        )
    elif has_runtime and runtime.get("state") in _COUNCIL_TERMINAL_ERROR_STATES:
        # A terminal runtime entry is authoritative for a failed dispatch.
        out["state"] = "stuck"
        out["reason"] = "runtime terminal state=%s error=%s" % (
            runtime.get("state"), runtime.get("error") or "")
    elif has_runtime and runtime.get("state") == "completed":
        out["state"] = "done"
        out["reason"] = "runtime completed"
    elif has_runtime and runtime.get("state") == "running":
        # Running worker is authoritative. worker_pid alive -> working;
        # present but dead -> stuck (monitor publishes the flipped state but a live
        # council_status call can see the pre-flip moment). worker_pid None => spawn grace
        # (CLI has not been assigned a pid yet so it may be about to spawn).
        worker_pid = runtime.get("worker_pid")
        if worker_pid is None:
            out["state"] = "dispatching"
            out["reason"] = "runtime running, worker pid not yet assigned (spawn grace)"
        elif _pid_alive(worker_pid):
            out["state"] = "working"
            out["reason"] = "runtime running, worker_pid %s alive" % worker_pid
        else:
            out["state"] = "stuck"
            out["reason"] = "runtime running but worker_pid %s is dead" % worker_pid
    elif has_queue:
        # Durable accepted record + in-memory queue: dispatch acknowledged, not yet started.
        out["state"] = "dispatching"
        out["reason"] = "queued in-memory, waiting for drain"
    elif has_ledger:
        # Accepted-only records cover the restart gap. A relay rejection is
        # terminal evidence and must never look like daemon-accepted work.
        if str(ledger.get("state") or "accepted") in {"rejected", "failed"}:
            out["state"] = "stuck"
            out["reason"] = "dispatch handoff %s: %s" % (
                ledger.get("state"), ledger.get("error") or "acceptance failed")
        else:
            out["state"] = "dispatching"
            # A32(c): do NOT attribute the gap to a restart without restart evidence
            # (same invent-a-cause defect as A28-P3). A restart is proven only when the
            # accepted record was written BEFORE this process booted; otherwise the
            # runtime simply has not published yet (spawn grace) and there is no cause
            # to name. Report gap_cause=None + a non-committal reason in that case.
            _acc_mtime = None
            try:
                _acc_mtime = float(ledger.get("mtime") or 0.0) or None
            except (TypeError, ValueError):
                _acc_mtime = None
            if _acc_mtime is not None and _acc_mtime < SERVER_STARTED_AT:
                out["gap_cause"] = "restart"
                out["reason"] = ("durable accepted (ledger) predates this boot, "
                                 "no runtime yet (restart-gap)")
            else:
                out["gap_cause"] = None
                out["reason"] = ("durable accepted (ledger), no runtime yet "
                                 "(spawn grace)")
    elif has_message:
        # Broker message exists (the worker spawn log marker) but no runtime/reply yet.
        # Recent -> still dispatching; old beyond the monitor stall limit -> stuck (dispatch_state_lost).
        age = None
        try:
            age = time.time() - float(message.get("mtime") or 0.0)
        except Exception:
            age = None
        if age is not None and age >= _COUNCIL_STALL_LIMIT_SEC:
            out["state"] = "stuck"
            out["reason"] = ("broker message exists but no runtime/reply and age %ds exceeds "
                             "COUNCIL_STALL_LIMIT_SEC (dispatch_state_lost)" % int(age))
        else:
            out["state"] = "dispatching"
            out["reason"] = "broker message exists, awaiting runtime (recent)"
    else:
        out["state"] = None
        out["reason"] = ""

    out["has_evidence"] = bool(out["has_evidence"] or has_queue or has_runtime or has_reply or has_message)
    return out


def _council_derive_state(dispatch_id: str) -> dict:
    """Single entrypoint for the council_status resolver + read-only derivation."""
    files = _council_dispatch_files(dispatch_id)
    runtime = files.get("runtime") or {}
    worker_pid = runtime.get("worker_pid")
    worker_pid_alive = None if worker_pid is None else bool(_pid_alive(worker_pid))
    runtime_error = str(runtime.get("error") or "")
    reply = files.get("reply") or {}
    out = {
        "ok": True,
        "dispatch_id": dispatch_id,
        "dispatch_found": False,
        "state": None,
        "worker_pid_alive": worker_pid_alive,
        "error": runtime_error or str(reply.get("error") or ""),
        "stuck_reason": "",
        "reason": files.get("reason") or "",
        "dispatch": {
            "accepted": bool(files.get("ledger") and str(files["ledger"].get("state") or "accepted") == "accepted"),
            "queued": files.get("queued_item") is not None,
            "broker_message": files.get("message"),
            "broker_reply": files.get("reply"),
            "runtime": files.get("runtime"),
        },
        "last_edit_ts": files.get("last_edit_ts"),
        "last_artifact_mtime": files.get("last_artifact_mtime"),
        # A32(c): named only when there is evidence for the cause (restart), else None.
        "gap_cause": files.get("gap_cause"),
    }
    if not files.get("has_evidence"):
        out["ok"] = False
        out["error_name"] = "DISPATCH_NOT_FOUND"
        out["reason"] = "no dispatch evidence for id"
        return out
    out["dispatch_found"] = True
    out["state"] = files.get("state")
    if out["state"] == "stuck":
        out["stuck_reason"] = out["reason"]
    return out


async def council_status(dispatch_id: str = "") -> str:
    """[A] TOOL (T1 read-only) - deterministic dispatch state query.

    Returns the canonical dispatch state: dispatching | working | done | stuck.
    Authoritative order: broker reply -> runtime terminal -> runtime running (pid alive) ->
    queued-record (durable) -> in-memory queue -> recent broker message -> DISPATCH_NOT_FOUND.
    Read-only: never cancels; the runtime monitor remains the sole cancellation owner."""
    dispatch_id = str(dispatch_id or "").strip()
    if not dispatch_id:
        return json.dumps({"ok": False, "error_name": "MISSING_DISPATCH_ID",
                           "error": "dispatch_id required"}, ensure_ascii=False)
    result = await asyncio.to_thread(_council_derive_state, dispatch_id)
    return json.dumps(result, ensure_ascii=False)


_register_tool(
    name="council_status",
    handler=council_status,
    phase="T1",
    tool_map=ct.TOOL_MAP,
    tool_schemas=ct.TOOL_SCHEMAS,
    tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES,
    phase_labels=ct.PHASE_LABELS,
    aliases={"id": "dispatch_id"},
    schema={
        "description": "Deterministic dispatch-state query for one dispatch_id (run-<uuid>).",
        "parameters": {
            "type": "object",
            "properties": {
                "dispatch_id": {"type": "string",
                                "description": "The dispatch correlation id returned by council_message / council_message_async (run-<uuid>)."},
            },
            "required": ["dispatch_id"],
        },
    },
)


# ── Worker model select/display (leader changes a worker's model without DOM) ──
_COUNCIL_ID_MODELS_MAP = {
    "clau": "CLAUDE",
    "codo": "GPT",
    "pic": "PI",
    "ope": "OPENCODE",
    "hermes": "HERMES",
    "kilo": "KILO",
    "gro": "GROK",
    "grok": "GROK",
}


def _council_valid_models(agent: str):
    """Parse .council/ID_MODELS/<PROVIDER>.txt — the curated valid model ids (the line
    after each 'ID model:'). Returns None when the worker has no ID_MODELS file (then
    no validation is enforced)."""
    fname = _COUNCIL_ID_MODELS_MAP.get(agent)
    if not fname:
        return None
    try:
        p = _HERE / ".council" / "ID_MODELS" / f"{fname}.txt"
        if not p.exists():
            return None
        lines = p.read_text(encoding="utf-8").splitlines()
        ids = []
        for i, ln in enumerate(lines):
            if ln.strip().lower().startswith("id model:"):
                for j in range(i + 1, len(lines)):
                    s = lines[j].strip()
                    if s and not (s.startswith("(") or s.startswith("#")):
                        ids.append(s)
                        break
        return ids
    except Exception:
        return None


def _council_provider_for_model(agent: str, model: str) -> str:
    """Return provider that must change together with a curated model id."""
    agent = str(agent or "").strip().lower()
    model = str(model or "").strip()
    if not model:
        return ""
    if agent == "hermes":
        if model == "deepseek-v4-flash-free":
            return "opencode-zen"
        if model == "glm-5.2":
            return "ltnai"
        if model == "kimi-k2.7-code":
            return "ltnai"
        return ""
    if agent == "ope":
        if model.startswith("opencode/"):
            return "opencode-zen"
        if model.startswith("openai-compatible/"):
            return "openai-compatible"
    if agent == "pic":
        return "ltnai"
    if agent == "gro":
        return "ltnai"
    if agent == "clau":
        if model.startswith("claude") or model in {
            "grok-4.6-build", "stealth/ox-alpha", "minimax/minimax-m3",
        }:
            return "claude"
        return ""
    if agent == "codo":
        # Live LTNAI IDs plus leftover GPT spellings. Prefix form kept for old callers.
        if model in {
            "stealth/ox-alpha", "grok-4.6-build", "minimax/minimax-m3",
            "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra",
            "openai/gpt-5.6-sol", "openai/gpt-5.6-luna", "openai/gpt-5.6-terra",
        }:
            return "codex"
        return ""
    if agent == "kilo":
        # Kilo CLI provider id is the display name in kilo.jsonc (ltnai). The
        # `ai_sdk_provider: openai-compatible` field is only the internal backend
        # adapter — NOT a valid --model prefix. Kilo --model takes provider/model
        # (e.g. ltnai/glm-5.2) and resolves the SDK adapter from config itself.
        if model.startswith("ltnai/"):
            return "ltnai"
        return ""
    return ""


def _council_valid_model_options(agent: str) -> list[dict]:
    """Read-only model options for UI/API. No provider/network checks."""
    agent = str(agent or "").strip().lower()
    valid = _council_valid_models(agent) or []
    caps_provider = _council_agent_caps(agent).get("provider", agent)
    options = []
    for model in valid:
        provider = _council_provider_for_model(agent, model) or caps_provider
        options.append({"provider": provider, "model": model})
    return options


async def council_worker_model(agent=None, model=None, provider=None, action=None):
    """[A] TOOL — get / set / list a council worker's model WITHOUT Playwright DOM.
    action: 'get' (current, default), 'set' (validate vs ID_MODELS then write), 'list'
    (curated valid ids). Writes .council/worker_models.json, applied at the NEXT
    dispatch (no restart).     Switchable workers: clau|codo|pic|ope|hermes|kilo.
    Council routing hint: ROUTE RULE SOT = Anh chỉ đạo. Benchmark 2026-07-25
    table is SUSPENDED. Always action='get' after action='set'.
    If PiC cannot read/write a path, stage files under .council/tmp_*; do not
    change model/provider as a path workaround. For full routing rules, load
    leader-council-skill.

    Metadata note (HermC audit S3 reconciled): writes a small config file but is
    INTENTIONALLY ungated (mutating=false, scope=none) — it is a council-control op the
    leader/CA calls without a project session; gating it is unnecessary friction."""
    path = _HERE / ".council" / "worker_models.json"

    def _load() -> dict:
        try:
            with _COUNCIL_WORKER_MODELS_LOCK:
                d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    agent = str(agent or "").strip().lower()
    model = str(model or "").strip()
    act = str(action or "").strip().lower()
    if not act:
        act = "set" if (agent and model) else ("list" if agent else "get")
    cfg = _load()
    if act == "get":
        if agent and agent in _COUNCIL_AGENTS:
            resolution = _council_resolve_model(agent).as_dict()
            return json.dumps({"ok": True, "agent": agent, "current": cfg.get(agent, {}),
                               "resolution": resolution, **resolution,
                               "valid": _council_valid_models(agent)}, ensure_ascii=False)
        resolutions = {name: _council_resolve_model(name).as_dict() for name in _COUNCIL_AGENTS}
        return json.dumps({"ok": True, "config": cfg, "resolutions": resolutions,
                           "agents": list(_COUNCIL_AGENTS)}, ensure_ascii=False)
    if act == "list":
        if agent not in _COUNCIL_AGENTS:
            return json.dumps({"ok": False, "error_name": "INVALID_AGENT", "error": f"unknown agent '{agent}'",
                               "allowed": list(_COUNCIL_AGENTS)}, ensure_ascii=False)
        return json.dumps({"ok": True, "agent": agent, "valid": _council_valid_models(agent),
                           "current": cfg.get(agent, {}), "resolution": _council_resolve_model(agent).as_dict()}, ensure_ascii=False)
    checked = _council_validate_model_setting(agent, str(provider or "").strip(), model)
    if not checked.get("ok"):
        return json.dumps(checked, ensure_ascii=False)
    provider = str(checked["provider"])
    warnings = list(checked.get("warnings") or [])
    try:
        with _COUNCIL_WORKER_MODELS_LOCK:
            cfg = _load()
            cfg[agent] = {"provider": provider, "model": model}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return json.dumps({"ok": False, "error_name": "WRITE_FAILED", "error": str(exc)}, ensure_ascii=False)
    response = {"ok": True, "agent": agent, "set": cfg[agent],
                "resolution": _council_resolve_model(agent).as_dict(),
                "note": "applied at the next dispatch (no restart)"}
    if warnings:
        response["warnings"] = warnings
    return json.dumps(response, ensure_ascii=False)


_register_tool(
    name="council_worker_model",
    handler=council_worker_model,
    phase="T1",
    tool_map=ct.TOOL_MAP,
    tool_schemas=ct.TOOL_SCHEMAS,
    tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES,
    phase_labels=ct.PHASE_LABELS,
    aliases={"worker": "agent", "id": "model", "model_id": "model"},
    schema={
        "description": f"Get/set/list a council worker's model ({'|'.join(_COUNCIL_AGENTS)}) WITHOUT "
                       "the dashboard UI / Playwright DOM. Validates against curated "
                       ".council/ID_MODELS/<PROVIDER>.txt; applies at the next dispatch. "
                       "ROUTE RULE SOT = the user's live directive (Anh). Benchmark 2026-07-25 table SUSPENDED. "
                       "Set, then verify with action='get' before council_message. "
                       "Do not change provider/model to work around path/read/write failures; stage PiC files under "
                       "[A]\\.council\\tmp_* instead. Load leader-council-skill for full council routing rules.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": f"Worker id, council namespace: {'|'.join(_COUNCIL_AGENTS)} (alias: worker). Rendered from the live roster (.council/council_config.json) at import, so it cannot drift; action='list' prints it. A council id is NOT an agents_registry.json id: registry 'opencode' routes as 'ope' (its route_id)."},
                "model": {"type": "string", "description": "Model id to set (alias: id). Must be in curated ID_MODELS. SET only the model Anh named or keep the live pin. Benchmark 2026-07-25 preferred cells are SUSPENDED. Always verify with action=get."},
                "provider": {"type": "string", "description": "Optional override. Normal routing omits provider only when action=get verifies backend/provider mapping; set explicitly for mismatch/debug."},
                "action": {"type": "string", "description": "get (default) | set | list. Recommended: set, then get to verify before dispatch."},
            },
            "required": [],
        },
    },
)


_RESTART_HUNG_SEMAPHORE_SEC = 15.0


def _body_path_hint(body: dict | None) -> str:
    """First path-like field the session/backup gates can resolve a project from.

    W7 (F020, 2026-08-19): "dst"/"src"/"target_file" were missing. copy_file
    declares required=["src","dst"] and no other resolver, so a payload built
    from its schema reached the session gate with nothing to resolve. It only
    appeared to work because an ABSOLUTE dst outside the [A] root trips the
    xroot gate first (_xroot_target, L13403) and skips the session gate
    entirely; a relative dst, or one inside the root, still 400d. Keep the key
    order aligned with _xroot_target so both gates agree on which path wins.
    """
    body = body if isinstance(body, dict) else {}
    for key in ("file_path", "path", "dst", "target_file", "src"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value
    paths = body.get("paths")
    if isinstance(paths, list):
        for item in paths:
            if isinstance(item, str) and item.strip():
                return item
    return ""


def _restart_busy_is_stale_or_hung(busy: dict) -> bool:
    """True when leftover work is stale accounting or hung in-process work.

    Live (fresh) executor/semaphore work still blocks a plain restart.
    Hung leftovers — executor oldest_active_age >= threshold, or every held
    semaphore held_for_sec >= threshold — do NOT block [A:S]: the bat kills
    the process. Council workers stay protected by restart_guard.
    """
    if not busy.get("busy"):
        return True
    hung_sec = float(busy.get("hung_threshold_sec") or _RESTART_HUNG_SEMAPHORE_SEC)
    exec_n = int(busy.get("executor_in_flight") or 0)
    oldest_exec = float(busy.get("oldest_executor_active_sec") or 0.0)
    if exec_n > 0 and oldest_exec < hung_sec:
        return False
    sems = [
        row for row in (busy.get("semaphores") or [])
        if int(row.get("held") or 0) or int(row.get("waiters") or 0)
    ]
    if sems and not all(float(row.get("held_for_sec") or 0) >= hung_sec for row in sems):
        return False
    return True


def _restart_busy_state(*, exclude_current_request: bool = False) -> dict:
    executors = executor_lane_status()
    executor_in_flight = sum(int(row.get("in_flight", 0)) for row in executors)
    oldest_executor_active_sec = 0.0
    for row in executors:
        if int(row.get("in_flight") or 0):
            oldest_executor_active_sec = max(
                oldest_executor_active_sec,
                float(row.get("oldest_active_age_ms") or 0) / 1000.0,
            )
    semaphores = [
        row for row in _semaphore_status_rows()
        if int(row.get("held", 0)) or int(row.get("waiters", 0))
    ]
    accounting = _POST_CALL_ACCOUNTING.status()
    requests_in_flight = max(0, int(_requests_in_flight) - (1 if exclude_current_request else 0))
    accounting_pending = int(accounting.get("pending", 0))
    state = {
        "busy": bool(
            requests_in_flight
            or executor_in_flight
            or accounting_pending
            or semaphores
        ),
        "requests_in_flight": requests_in_flight,
        "executor_in_flight": executor_in_flight,
        "oldest_executor_active_sec": round(oldest_executor_active_sec, 3),
        "accounting_pending": accounting_pending,
        "semaphores": semaphores,
        "hung_threshold_sec": _RESTART_HUNG_SEMAPHORE_SEC,
        "generation": SERVER_GENERATION,
    }
    state["is_stale_or_hung"] = _restart_busy_is_stale_or_hung(state)
    return state
def _restart_busy_snapshot_state() -> dict:
    # R48 (2026-08-12): exclude the in-flight RESTART request itself from the snapshot
    # the controller guard reads. Otherwise a restart always sees requests_in_flight>=1
    # (its own call) and the guard false-blocks with ORCHESTRATOR_BUSY / is_stale=false,
    # forcing needless force_drain and still blocking. Only subtract when a restart id is
    # actually set, so normal-load snapshots are not undercounted.
    state = _restart_busy_state(exclude_current_request=bool(_RESTART_REQUEST_ID))
    state["restart_request_id"] = _RESTART_REQUEST_ID or None
    return state


def _start_restart_snapshot_publisher() -> BusySnapshotPublisher:
    global _RESTART_SNAPSHOT_PUBLISHER, _RESTART_SNAPSHOT_REAP
    # A08: reap orphaned "<name>.tmp.<pid>.*" temps left by hard-killed writers
    # BEFORE the publisher starts producing new ones. Fail-loud: record + log the
    # summary so a silent leak can never hide (7-RULE #7).
    try:
        _RESTART_SNAPSHOT_REAP = reap_orphan_busy_snapshots(RESTART_BUSY_SNAPSHOT)
        if _RESTART_SNAPSHOT_REAP.get("reaped_count") or _RESTART_SNAPSHOT_REAP.get("errors"):
            logger.info(
                "restart busy-snapshot reaper: reaped=%d kept_alive=%d kept_young=%d errors=%s",
                _RESTART_SNAPSHOT_REAP.get("reaped_count", 0),
                len(_RESTART_SNAPSHOT_REAP.get("kept_alive", [])),
                len(_RESTART_SNAPSHOT_REAP.get("kept_young", [])),
                _RESTART_SNAPSHOT_REAP.get("errors", []),
            )
    except Exception as exc:  # never block boot on a cleanup failure
        _RESTART_SNAPSHOT_REAP = {"error": f"{type(exc).__name__}: {exc}"}
        logger.warning("restart busy-snapshot reaper failed: %s", exc)
    publisher = BusySnapshotPublisher(RESTART_BUSY_SNAPSHOT, _restart_busy_snapshot_state)
    publisher.start()
    _RESTART_SNAPSHOT_PUBLISHER = publisher
    return publisher


def _publish_server_generation() -> dict:
    """Atomically publish the HTTP daemon generation for MCP to read without HTTP."""
    if "--mcp" in sys.argv:
        return {"ok": False, "skipped": "mcp_process"}
    target = Path(SERVER_GENERATION_FILE)
    tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}")
    payload = {
        "generation": SERVER_GENERATION,
        "pid": os.getpid(),
        "port": PORT,
        "published_at": time.time(),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
        return {"ok": True, "path": str(target), **payload}
    except Exception as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        return {"ok": False, "path": str(target),
                "error": f"{type(exc).__name__}: {exc}"}


def _stop_restart_snapshot_publisher() -> None:
    global _RESTART_SNAPSHOT_PUBLISHER
    if _RESTART_SNAPSHOT_PUBLISHER is not None:
        _RESTART_SNAPSHOT_PUBLISHER.stop()
        _RESTART_SNAPSHOT_PUBLISHER = None


async def restart_orchestrator_via_bat(
    delay_seconds: int = 2,
    force_drain: bool = False,
    force_drain_reason: str = "",
) -> str:
    """Wire to the authenticated restart controller with explicit stale-state draining."""
    global _RESTART_REQUEST_ID
    busy = _restart_busy_state(exclude_current_request=True)
    blocking_guards = []
    if busy.get("busy"):
        held_by = {key: busy.get(key) for key in (
            "requests_in_flight", "executor_in_flight", "accounting_pending", "semaphores"
        )}
        stale_or_hung = _restart_busy_is_stale_or_hung(busy)
        operator_override = bool(force_drain and str(force_drain_reason or "").strip())
        blocking_guards.append({
            "name": "ORCHESTRATOR_BUSY", "held_by": held_by, "is_stale": stale_or_hung,
            "how_to_clear": (
                "auto: hung/stale work does not block [A:S]"
                if stale_or_hung else
                "use force_drain with a reason to queue [A:S] anyway"
            ),
        })
        if not stale_or_hung and not operator_override:
            return json.dumps({
                "ok": False, "error": "Orchestrator still has work in flight",
                "error_name": "RESTART_DRAIN_REQUIRED", "retryable": True,
                "ca_next_action": "force_drain_stale_state_with_reason",
                "busy_state": busy, "blocking_guards": blocking_guards,
            }, ensure_ascii=False)
    if force_drain and not force_drain_reason.strip():
        return json.dumps({"ok": False, "error_name": "FORCE_DRAIN_REASON_REQUIRED",
                           "blocking_guards": blocking_guards}, ensure_ascii=False)
    import tools_v15 as _tv
    request_id = uuid.uuid4().hex
    _RESTART_REQUEST_ID = request_id
    try:
        if _RESTART_SNAPSHOT_PUBLISHER is not None:
            _RESTART_SNAPSHOT_PUBLISHER.publish_once()
        return await _tv.restart_orchestrator_via_bat(
            delay_seconds=delay_seconds, expected_generation=SERVER_GENERATION,
            controller_url=RESTART_CONTROL_URL, request_id=request_id,
            force_drain=force_drain, force_drain_reason=force_drain_reason,
        )
    finally:
        _RESTART_REQUEST_ID = ""


_register_tool(
    name="restart_orchestrator_via_bat",
    handler=restart_orchestrator_via_bat,
    phase="T2",
    tool_map=ct.TOOL_MAP,
    tool_schemas=ct.TOOL_SCHEMAS,
    tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES,
    phase_labels=ct.PHASE_LABELS,
    aliases={"delay": "delay_seconds", "delay_sec": "delay_seconds"},
    schema={
        "description": "Restart [A] via the canonical [A:S] script (start_orchestrator.bat): spawns a hidden "
                       "helper (CREATE_NO_WINDOW; no console focus-steal) that waits delay_seconds then runs [A:S], which kills the old pythonw and relaunches "
                       "[A] High-IL. Returns immediately with status=restart_queued; poll /health until ok, then "
                       "verify generation/PID changed. TOKEN REQUIRED (X-Orchestrator-Token). "
                       "USAGE: for a normal restart call with NO arguments (or just delay_seconds) — the tool auto-"
                       "targets the current generation; you do NOT pass expected_generation/controller_url/request_id "
                       "(those are internal). STALE leftovers and HUNG in-process work "
                       f"(executor oldest_active_age or semaphore held_for_sec>={_RESTART_HUNG_SEMAPHORE_SEC:.0f}s) "
                       "do NOT block [A:S] — the bat kills the process. "
                       "force_drain+force_drain_reason overrides remaining LIVE (fresh) work and still queues [A:S]. "
                       "This is the CORE_RULE-preferred restart path for CAs; restart_orchestrator_via_task stays as the "
                       "schtasks-based fallback for admin/UIPI recovery. CSLt 2026-07-16: tier T4->T2 (process-lifecycle "
                       "mutation with built-in safety guards: process discovery, port-owner filter, wait loop).",
        "parameters": {
            "type": "object",
            "properties": {
                "delay_seconds": {"type": "integer", "default": 2, "minimum": 1, "maximum": 10, "description": "Helper wait before running [A:S] (aliases: delay, delay_sec)."},
                "force_drain": {"type": "boolean", "default": False, "description": "Override live in-flight work and still queue [A:S]. Hung/stale leftovers do not need this."},
                "force_drain_reason": {"type": "string", "default": "", "description": "Required and non-empty when force_drain=true; records the operator safety justification."},
            },
            "required": [],
            "allOf": [{
                "if": {"properties": {"force_drain": {"const": True}}, "required": ["force_drain"]},
                "then": {"required": ["force_drain_reason"], "properties": {"force_drain_reason": {"type": "string", "minLength": 1}}},
            }],
        },
    },
)


# -- P1 (ToolQ-as-[A]-memory, Bridge): memory proxy tools -----------------
# Fail-soft proxies to ToolQ librarian (:8082). ToolQ down -> degraded reply,
# action continues. Reads (query/recall) + write (capture). forget deferred.
_TOOLQ_BASE = "http://127.0.0.1:8082"


# S2-MEM (Commit C): module-level counter so tests/audit can assert that
# fail-fast local validation rejected the payload BEFORE any ToolQ/network
# dispatch. Incremented only immediately before the actual HTTP dispatch.
_TOOLQ_DISPATCH_COUNT = {"capture": 0, "ingest_path": 0, "query": 0, "cluster": 0}


async def _toolq_post(path, payload, timeout=20, _counter_key=None):
    if _counter_key is not None:
        _TOOLQ_DISPATCH_COUNT[_counter_key] = _TOOLQ_DISPATCH_COUNT.get(_counter_key, 0) + 1
    def _fire():
        req = urllib.request.Request(
            _TOOLQ_BASE + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST")
        return urllib.request.urlopen(req, timeout=timeout).read()
    raw = await asyncio.to_thread(_fire)
    return json.loads(raw.decode("utf-8"))


async def memory_query(query=None, top_k=2, mode="pre_search"):
    """Query [A] long-term memory via ToolQ (/pre_search default | /query). Fail-soft.

    D-06: /pre_search on ToolQ :8082 routinely takes ~10s (FTS+vector), and can
    spike past the prior 20s timeout under load, surfacing as
    MEMORY_UNAVAILABLE while memory_recall_cluster (timeout=30 on a fast
    endpoint) kept working. Align the timeout with memory_recall_cluster (30s)
    and distinguish timeout from other failures so callers can tell a slow
    memory backend apart from a dead one.
    """
    if not query:
        return json.dumps({"ok": False, "error_name": "MISSING_ARG", "error": "query required", "hits": []}, ensure_ascii=False)
    path = "/query" if str(mode) == "query" else "/pre_search"
    try:
        data = await _toolq_post(path, {"query": str(query), "agent_id": "clau", "top_k": int(top_k)}, timeout=30)
        return json.dumps({"ok": True, "source": "toolq", "endpoint": path, "result": data}, ensure_ascii=False)
    except Exception as exc:
        # Distinguish timeout from other network/HTTP errors so callers can
        # decide whether to retry (timeout) or fall through to degraded mode.
        is_timeout = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
        return json.dumps({
            "ok": False,
            "degraded": True,
            "error_name": "MEMORY_TIMEOUT" if is_timeout else "MEMORY_UNAVAILABLE",
            "error": str(exc)[:160],
            "endpoint": path,
            "hits": [],
        }, ensure_ascii=False)


async def memory_recall_cluster(query=None, raw_ids=None, recent_n=None):
    """Recall a grounded memory cluster via ToolQ /machine_cluster_report. Fail-soft."""
    payload = {"agent_id": "clau"}
    if query:
        payload["query"] = str(query)
    if raw_ids:
        payload["raw_ids"] = raw_ids
    if recent_n is not None:
        try:
            payload["recent_n"] = int(recent_n)
        except Exception:
            pass
    if "query" not in payload and "raw_ids" not in payload:
        return json.dumps({"ok": False, "error_name": "MISSING_ARG", "error": "query or raw_ids required"}, ensure_ascii=False)
    try:
        data = await _toolq_post("/machine_cluster_report", payload, timeout=30)
        return json.dumps({"ok": True, "source": "toolq", "result": data}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "degraded": True, "error_name": "MEMORY_UNAVAILABLE", "error": str(exc)[:160]}, ensure_ascii=False)


async def memory_capture(session_id=None, turns=None, project=None):
    """Capture [A] session turns into long-term memory (ToolQ /capture, writer='A', dedup). Fail-soft.

    S2-MEM-01 (Commit C): fail-fast local validation runs BEFORE any ToolQ
    dispatch. Missing required field -> MISSING_REQUIRED_FIELD; type/shape
    violation -> SCHEMA_VIOLATION. Both reject in <250ms with zero ToolQ
    dispatch, so a malformed payload never waits on the network.
    """
    # 1) Required-field presence (fail fast, no ToolQ dispatch).
    missing = []
    if session_id is None or (isinstance(session_id, str) and not session_id.strip()):
        missing.append("session_id")
    if turns is None:
        missing.append("turns")
    if missing:
        return json.dumps({
            "ok": False,
            "error_name": "MISSING_REQUIRED_FIELD",
            "type": "SCHEMA_VIOLATION",
            "error": f"missing required field(s): {', '.join(missing)}",
            "missing_fields": missing,
            "required_fields": ["session_id", "turns"],
            "ca_next_action": "rebuild_payload_using_required_and_accepted_fields",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    # 2) Type/shape validation (still local; no ToolQ dispatch).
    if not isinstance(session_id, str):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "session_id must be a non-empty string",
            "field": "session_id",
            "ca_next_action": "retry_with_string_session_id",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    if not isinstance(turns, list):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "turns must be a list of turn objects",
            "field": "turns",
            "ca_next_action": "retry_with_list_turns",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    for _i, _t in enumerate(turns):
        if not isinstance(_t, dict):
            return json.dumps({
                "ok": False,
                "error_name": "SCHEMA_VIOLATION",
                "type": "SCHEMA_VIOLATION",
                "error": f"turns[{_i}] must be an object with user_prompt/assistant_response",
                "field": f"turns[{_i}]",
                "ca_next_action": "retry_with_turn_objects",
                "write_performed": False,
                "applied": False,
            }, ensure_ascii=False)
    if project is not None and not isinstance(project, str):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "project must be a string when provided",
            "field": "project",
            "ca_next_action": "retry_with_string_project",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    # 3) Local validation passed -> dispatch to ToolQ (counted).
    try:
        data = await _toolq_post("/capture", {"writer": "A", "session_id": str(session_id), "project": project, "turns": turns}, _counter_key="capture")
        return json.dumps({"ok": bool(data.get("ok")), "source": "toolq", "result": data}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "degraded": True, "error_name": "MEMORY_UNAVAILABLE", "error": str(exc)[:160]}, ensure_ascii=False)


def _memory_ingest_path_payload(source_type, zone=None, subzone=None, content=None, path=None, session_id=None, project=None, options=None, max_file_mb=None):
    payload = {"source_type": str(source_type)}
    if zone:
        payload["zone"] = str(zone)
    if subzone:
        payload["subzone"] = str(subzone)
    if source_type == "manual_text":
        payload["content"] = str(content or "")
    elif source_type in ("file", "folder"):
        payload["path"] = str(path or "")
    if session_id:
        payload["session_id"] = str(session_id)
    if project:
        payload["project"] = str(project)
    if options and isinstance(options, dict):
        payload["options"] = dict(options)
        if "max_file_mb" in options:
            payload["max_file_mb"] = options["max_file_mb"]
    if max_file_mb is not None:
        payload["max_file_mb"] = max_file_mb
    return payload


async def memory_ingest_path(source_type=None, zone=None, subzone=None, content=None, path=None, session_id=None, project=None, options=None, max_file_mb=None):
    """P2 bridge: ingest raw content/file path into ToolQ librarian via /ingest_path. Fail-soft.

    S2-MEM-02 (Commit C): fail-fast local validation runs BEFORE any ToolQ
    dispatch. Missing required -> MISSING_REQUIRED_FIELD; enum/type/shape
    violation -> SCHEMA_VIOLATION. Both reject in <250ms with zero ToolQ
    dispatch.
    """
    # 1) Required-field presence (fail fast, no ToolQ dispatch).
    if source_type is None or (isinstance(source_type, str) and not source_type.strip()):
        return json.dumps({
            "ok": False,
            "error_name": "MISSING_REQUIRED_FIELD",
            "type": "SCHEMA_VIOLATION",
            "error": "missing required field(s): source_type",
            "missing_fields": ["source_type"],
            "required_fields": ["source_type"],
            "ca_next_action": "rebuild_payload_using_required_and_accepted_fields",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    # 2) Enum/type validation (still local; no ToolQ dispatch).
    if not isinstance(source_type, str):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "source_type must be a string",
            "field": "source_type",
            "ca_next_action": "retry_with_string_source_type",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    if source_type not in ("manual_text", "file", "folder"):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "source_type must be one of: manual_text, file, folder",
            "field": "source_type",
            "accepted_values": ["manual_text", "file", "folder"],
            "ca_next_action": "retry_with_valid_source_type",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    # 3) Conditional required + shape validation per source_type.
    if source_type == "manual_text":
        if content is None or (isinstance(content, str) and not content.strip()):
            return json.dumps({
                "ok": False,
                "error_name": "MISSING_REQUIRED_FIELD",
                "type": "SCHEMA_VIOLATION",
                "error": "missing required field(s) for source_type=manual_text: content",
                "missing_fields": ["content"],
                "required_fields": ["source_type", "content"],
                "ca_next_action": "provide_content_for_manual_text",
                "write_performed": False,
                "applied": False,
            }, ensure_ascii=False)
        if not isinstance(content, str):
            return json.dumps({
                "ok": False,
                "error_name": "SCHEMA_VIOLATION",
                "type": "SCHEMA_VIOLATION",
                "error": "content must be a string for source_type=manual_text",
                "field": "content",
                "ca_next_action": "retry_with_string_content",
                "write_performed": False,
                "applied": False,
            }, ensure_ascii=False)
    elif source_type in ("file", "folder"):
        if path is None or (isinstance(path, str) and not path.strip()):
            return json.dumps({
                "ok": False,
                "error_name": "MISSING_REQUIRED_FIELD",
                "type": "SCHEMA_VIOLATION",
                "error": f"missing required field(s) for source_type={source_type}: path",
                "missing_fields": ["path"],
                "required_fields": ["source_type", "path"],
                "ca_next_action": f"provide_path_for_{source_type}",
                "write_performed": False,
                "applied": False,
            }, ensure_ascii=False)
        if not isinstance(path, str):
            return json.dumps({
                "ok": False,
                "error_name": "SCHEMA_VIOLATION",
                "type": "SCHEMA_VIOLATION",
                "error": f"path must be a string for source_type={source_type}",
                "field": "path",
                "ca_next_action": "retry_with_string_path",
                "write_performed": False,
                "applied": False,
            }, ensure_ascii=False)
        # Path shape: reject NUL / C0 control chars locally (would otherwise
        # poison the ToolQ request or hang the dispatcher downstream).
        for _ch in path:
            if ord(_ch) < 32:
                return json.dumps({
                    "ok": False,
                    "error_name": "SCHEMA_VIOLATION",
                    "type": "SCHEMA_VIOLATION",
                    "error": "path contains a disallowed control character",
                    "field": "path",
                    "ca_next_action": "strip_or_reencode_path_control_character",
                    "write_performed": False,
                    "applied": False,
                }, ensure_ascii=False)
    # 4) Cross-field type checks (still local).
    if zone is not None and not isinstance(zone, str):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "zone must be a string when provided",
            "field": "zone",
            "ca_next_action": "retry_with_string_zone",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    if subzone is not None and not isinstance(subzone, str):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "subzone must be a string when provided",
            "field": "subzone",
            "ca_next_action": "retry_with_string_subzone",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    if session_id is not None and not isinstance(session_id, str):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "session_id must be a string when provided",
            "field": "session_id",
            "ca_next_action": "retry_with_string_session_id",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    if project is not None and not isinstance(project, str):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "project must be a string when provided",
            "field": "project",
            "ca_next_action": "retry_with_string_project",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    if options is not None and not isinstance(options, dict):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "options must be an object when provided",
            "field": "options",
            "ca_next_action": "retry_with_object_options",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    if max_file_mb is not None and not isinstance(max_file_mb, (int, float)):
        return json.dumps({
            "ok": False,
            "error_name": "SCHEMA_VIOLATION",
            "type": "SCHEMA_VIOLATION",
            "error": "max_file_mb must be a number when provided",
            "field": "max_file_mb",
            "ca_next_action": "retry_with_numeric_max_file_mb",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    # 5) Local validation passed -> dispatch to ToolQ (counted).
    payload = _memory_ingest_path_payload(source_type, zone, subzone, content, path, session_id, project, options, max_file_mb)
    try:
        data = await _toolq_post("/ingest_path", payload, timeout=30, _counter_key="ingest_path")
        return json.dumps({"ok": bool(data.get("ok")), "source": "toolq", "result": data}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "degraded": True, "error_name": "MEMORY_UNAVAILABLE", "error": str(exc)[:160]}, ensure_ascii=False)


_register_tool(
    name="memory_query", handler=memory_query, phase="T1",
    tool_map=ct.TOOL_MAP, tool_schemas=ct.TOOL_SCHEMAS, tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES, phase_labels=ct.PHASE_LABELS,
    aliases={"q": "query", "k": "top_k", "limit": "top_k"},
    schema={
        "description": "Query [A]'s long-term memory (ToolQ librarian :8082). mode=pre_search (gatekept, default) | query (FTS+vector). Fail-soft: returns degraded when memory offline.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search text (alias: q)"},
            "top_k": {"type": "integer", "default": 2, "description": "Max hits 1-10 (alias: k/limit)"},
            "mode": {"type": "string", "description": "pre_search (default) | query"},
        }, "required": ["query"]},
    },
)

_register_tool(
    name="memory_recall_cluster", handler=memory_recall_cluster, phase="T1",
    tool_map=ct.TOOL_MAP, tool_schemas=ct.TOOL_SCHEMAS, tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES, phase_labels=ct.PHASE_LABELS,
    schema={
        "description": "Recall a grounded memory cluster (ToolQ /machine_cluster_report) by query or explicit raw_ids. Fail-soft.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Cluster query text"},
            "raw_ids": {"type": "array", "items": {"type": "integer"}, "description": "Explicit raw_turn ids"},
            "recent_n": {"type": "integer", "description": "Recent-N raw turns (if backend supports)"},
        }, "required": []},
    },
)

_register_tool(
    name="memory_capture", handler=memory_capture, phase="T1",
    tool_map=ct.TOOL_MAP, tool_schemas=ct.TOOL_SCHEMAS, tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES, phase_labels=ct.PHASE_LABELS,
    schema={
        "description": "Capture [A] session turns into long-term memory (ToolQ /capture, writer='A', content-hash dedup). Fail-soft; candidate-only memory write.",
        "parameters": {"type": "object", "properties": {
            "session_id": {"type": "string", "description": "Session identifier"},
            "turns": {"type": "array", "items": {"type": "object"}, "description": "[{user_prompt, assistant_response, turn_index?}]"},
            "project": {"type": "string", "description": "Optional project tag"},
        }, "required": ["session_id", "turns"]},
    },
)

_register_tool(
    name="memory_ingest_path", handler=memory_ingest_path, phase="T1",
    tool_map=ct.TOOL_MAP, tool_schemas=ct.TOOL_SCHEMAS, tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES, phase_labels=ct.PHASE_LABELS,
    schema={
        "description": "P2 bridge: ingest raw content, file path, or folder path into ToolQ librarian (/ingest_path). source_type=manual_text needs content; source_type=file/folder needs path. All take optional zone (Z1|Z2|Z3), subzone (Z1=meta/plan, Z2=rules/skills/schema/aliases/regex, Z3=docs/code/manual), session_id, project, options/max_file_mb. Fail-soft.",
        "parameters": {"type": "object", "properties": {
            "source_type": {"type": "string", "description": "manual_text | file | folder"},
            "zone": {"type": "string", "description": "Memory zone: Z1, Z2, or Z3"},
            "subzone": {"type": "string", "description": "Optional subzone: Z1=meta/plan, Z2=rules/skills/schema/aliases/regex, Z3=docs/code/manual"},
            "content": {"type": "string", "description": "Raw text content (for manual_text)"},
            "path": {"type": "string", "description": "File or folder path to ingest (for file/folder)"},
            "session_id": {"type": "string", "description": "Optional session identifier"},
            "project": {"type": "string", "description": "Optional project tag"},
            "options": {"type": "object", "description": "Arbitrary options dict (e.g. max_file_mb)"},
            "max_file_mb": {"type": "number", "description": "Top-level file size limit in MB; accepts fractional values"},
        }, "required": ["source_type"]},
    },
)


async def mcp_pack_generate(
    action: str = "generate",
    pack_id: str = "toolq",
    ca_id: str = "",
    agent_id: str = "",
    tools_json: str = "",
    catalog_json: str = "",
    base_url: str = "",
    server_name: str = "",
    out_root: str = "",
    out_dir: str = "",
    python_exe: str = "",
    hermes_config: str = "",
    targets_json: str = "",
    dry_run: bool = True,
    delete_files: bool = False,
    keep_wire: bool = False,
    force: bool = False,
) -> str:
    """CSLt P3: generate/install/test/remove MCP pack artifacts (per-CA identity supported)."""
    try:
        from orchestrator.mcp_packs.generator import mcp_pack_generate as _impl
    except Exception as e:
        return json.dumps({"ok": False, "error_name": "IMPORT_MCP_PACK_GENERATOR", "error": str(e)})
    return _impl(
        action=action or "generate",
        pack_id=pack_id or "toolq",
        ca_id=ca_id or "",
        agent_id=agent_id or "",
        tools_json=tools_json or "",
        catalog_json=catalog_json or "",
        base_url=base_url or "",
        server_name=server_name or "",
        out_root=out_root or "",
        out_dir=out_dir or "",
        python_exe=python_exe or "",
        hermes_config=hermes_config or "",
        targets_json=targets_json or "",
        dry_run=bool(dry_run),
        delete_files=bool(delete_files),
        keep_wire=bool(keep_wire),
        force=bool(force),
    )


_register_tool(
    name="mcp_pack_generate",
    handler=mcp_pack_generate,
    phase="T2",
    tool_map=ct.TOOL_MAP,
    tool_schemas=ct.TOOL_SCHEMAS,
    tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES,
    phase_labels=ct.PHASE_LABELS,
    aliases={
        "agent": "agent_id",
        "ca": "ca_id",
        "pack": "pack_id",
        "targets": "targets_json",
        "tools": "tools_json",
        "catalog": "catalog_json",
    },
    schema={
        "description": (
            "CSLt P3: generate MCP pack artifacts (stdio server + hermes_fragment + pack.json). "
            "Actions: generate|list_packs|test_pack|install_hermes|install_targets|"
            "uninstall_hermes|remove_pack|uninstall_targets. "
            "dry_run default true. ca_id/agent_id -> per-CA MCP key (<pack>__<ca>) for isolation. "
            "remove_pack: unwire Hermes (+ optional delete_files rmtree under out_root). "
            "Complementary to manage_agent.sync_mcp (does not replace)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "generate|list_packs|test_pack|install_hermes|install_targets|"
                        "uninstall_hermes|remove_pack|uninstall_targets"
                    ),
                    "default": "generate",
                },
                "pack_id": {
                    "type": "string",
                    "description": "Builtin toolq|council or custom id",
                    "default": "toolq",
                },
                "ca_id": {
                    "type": "string",
                    "description": "CA registry identity for per-CA MCP process",
                },
                "agent_id": {"type": "string", "description": "Alias of ca_id"},
                "tools_json": {
                    "type": "string",
                    "description": "JSON list of tool names for custom pack",
                },
                "catalog_json": {
                    "type": "string",
                    "description": "Full catalog JSON override",
                },
                "base_url": {"type": "string", "description": "Backend base URL override"},
                "server_name": {"type": "string", "description": "MCP server base name"},
                "out_root": {
                    "type": "string",
                    "description": "Output root (default D:/BR/_CSLt_plan/mcp_packs)",
                },
                "out_dir": {"type": "string", "description": "Alias of out_root"},
                "python_exe": {"type": "string", "description": "Python with zeromcp"},
                "hermes_config": {
                    "type": "string",
                    "description": "Hermes config path for install/uninstall",
                },
                "targets_json": {
                    "type": "string",
                    "description": "JSON list of CA targets for install/uninstall_targets",
                },
                "dry_run": {
                    "type": "boolean",
                    "default": True,
                    "description": "Preview only when true (default)",
                },
                "delete_files": {
                    "type": "boolean",
                    "default": False,
                    "description": "remove_pack: also rmtree pack dir under out_root (default false=unwire only)",
                },
                "keep_wire": {
                    "type": "boolean",
                    "default": False,
                    "description": "remove_pack: skip Hermes unwire, only files (if delete_files)",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Allow uninstall of protected keys (project-orchestrator) — not recommended",
                },
            },
            "required": [],
        },
    },
)



async def handle_council_send(request):
    """GET /api/council/send?from=&to=&text=&timeout_sec= — worker-friendly plain-text council messaging.
    ONE curl.exe call, no JSON body, no -d/-H, no shell escaping. Returns the target agent's reply as
    PLAIN TEXT so workers never parse JSON or hand-build escaped payloads. Logged to the broker like POST."""
    cors = _cors(request.headers.get('Origin'))
    ptxt = dict(cors)
    ptxt["Content-Type"] = "text/plain; charset=utf-8"
    if not _is_local(request) or not _origin_ok(request):
        return web.Response(text="blocked", status=403, headers=ptxt)
    mi = request.match_info
    if mi.get("to"):
        # PATH form /api/council/send/<from>/<to>/<text> — no & or ? so PowerShell can't mangle it.
        import urllib.parse as _urlparse
        sender = (mi.get("frm", "worker") or "worker").strip() or "worker"
        target = (mi.get("to", "") or "").strip()
        text = _urlparse.unquote((mi.get("text", "") or "")).replace("+", " ").strip()
        timeout_sec = 6000
        provider = ""
        model = ""
    else:
        q = request.query
        sender = (q.get("from", "worker") or "worker").strip() or "worker"
        target = (q.get("to", "") or "").strip()
        text = (q.get("text", "") or "").strip()
        provider = (q.get("provider", "") or "").strip()
        model = (q.get("model", "") or "").strip()
        try:
            timeout_sec = int(q.get("timeout_sec", 6000) or 6000)
        except Exception:
            timeout_sec = 6000
        queue_params = {"queue": q.get("queue"), "no_queue": q.get("no_queue")}
        try:
            queue_wait_timeout_sec = int(q.get("queue_wait_timeout_sec", 6000) or 6000)
        except Exception:
            queue_wait_timeout_sec = 6000
    # CAN-020/021: resolve sender server-side (GET path/query 'from' is caller-controlled).
    sender = _council_resolve_sender(request, sender)
    if target not in _COUNCIL_AGENTS:
        return web.Response(text=f"ERROR INVALID_AGENT: '{target}' not in {list(_COUNCIL_AGENTS)}", status=400, headers=ptxt)
    if not text:
        return web.Response(text="ERROR MISSING_TEXT: provide ?text=", status=400, headers=ptxt)
    if not _council_agent_caps(target).get("enabled", True):
        return web.Response(text=f"ERROR AGENT_DISABLED: {target}", status=400, headers=ptxt)
    if mi.get("to"):
        queue_params = {}
        queue_wait_timeout_sec = 6000
    # P2 (2026-08-06): opt-in non-blocking dispatch for ANY caller (leader parallel
    # fan-out / CLI workers): ?async=1 acks immediately with a request_id; the reply
    # lands in .council/broker_filtered.jsonl. Default stays sync (backward compat).
    if _council_bool_param(request.query.get("async") or request.query.get("async_dispatch"), False):
        # A32(a): resolve the model BEFORE the ASYNC_DISPATCHED ack so an unavailable
        # model fails loud here rather than vanishing inside _bg_send.
        _preack_error = _council_preack_resolution_error(target, text, provider, model)
        if _preack_error:
            _pe_name = _preack_error["error_name"]
            _pe_msg = _preack_error.get("error", "")
            return web.Response(text=f"ERROR {_pe_name}: {_pe_msg}",
                                status=400, headers=ptxt)
        _async_req_id = f"run-{uuid.uuid4()}"
        _async_sender = sender[:40]

        async def _bg_send():
            try:
                _result = await _council_enqueue_or_run(
                    target, _async_sender, text, timeout_sec, "broker", "",
                    queue=True, queue_wait_timeout_sec=timeout_sec,
                    provider=provider, model=model, request_id=_async_req_id,
                )
                # A32(b): callee returns terminal errors, not raises — record them.
                _council_record_async_result(
                    _async_req_id, _async_sender, target, timeout_sec, _result)
            except Exception as exc:
                logger.error("async GET council dispatch to %s from %s failed: %r",
                             target, sender, exc, exc_info=True)
                _council_reject_dispatch(
                    _async_req_id, _async_sender, target, timeout_sec,
                    f"{type(exc).__name__}: {exc}")

        _t_send = asyncio.create_task(_bg_send())
        _COUNCIL_PENDING_TASKS.add(_t_send)
        _t_send.add_done_callback(_COUNCIL_PENDING_TASKS.discard)
        return web.Response(
            text=f"OK ASYNC_DISPATCHED request_id={_async_req_id} to={target}\n"
                 "Reply lands in .council/broker_filtered.jsonl (poll council_tracker)",
            headers=ptxt,
        )
    queued_result = await _council_enqueue_or_run(
        target, sender[:40], text, timeout_sec, "broker", "",
        queue=_council_queue_enabled(queue_params), queue_wait_timeout_sec=queue_wait_timeout_sec,
        provider=provider, model=model,
    )
    if queued_result.get("error_name") in ("AGENT_BUSY", "AGENT_QUEUE_FULL", "QUEUE_WAIT_TIMEOUT"):
        status = {"AGENT_BUSY": 409, "AGENT_QUEUE_FULL": 429, "QUEUE_WAIT_TIMEOUT": 504}[queued_result["error_name"]]
        return web.Response(text=f"ERROR {queued_result['error_name']}: {queued_result.get('error','')}", status=status, headers=ptxt)
    if not queued_result.get("ok"):
        return web.Response(
            text=f"ERROR AGENT_ERROR: {queued_result.get('error','')}\n{queued_result.get('reply_text','')}",
            status=500, headers=ptxt,
        )
    return web.Response(text=queued_result.get("reply_text") or "(empty reply)", headers=ptxt)


async def handle_council_dashboard(request):
    """GET /council - localhost-only read-only council activity dashboard."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.Response(text="blocked", status=403, headers=cors)

    if not SERVER_READY:
        phase = str(SERVER_STARTUP_PHASE or "starting")
        safe_phase = (
            phase.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        html = (
            "<!DOCTYPE html><html lang=\"en\"><head>"
            "<meta charset=\"utf-8\">"
            "<meta http-equiv=\"refresh\" content=\"2\">"
            "<title>Council starting</title></head><body>"
            f"<p>Council dashboard is starting&hellip; phase={safe_phase}</p>"
            "<p>This page refreshes automatically. Wait for startup_phase=ready.</p>"
            "</body></html>"
        )
        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
            headers=_cors(
                request.headers.get("Origin"),
                {"Cache-Control": "no-store, max-age=0", "Retry-After": "1"},
            ),
        )

    from orchestrator.council.dashboard import render_council_dashboard

    return web.Response(
        text=render_council_dashboard(),
        content_type='text/html',
        charset='utf-8',
        headers=_cors(request.headers.get('Origin'), {'Cache-Control': 'no-store, max-age=0'}),
    )


async def handle_council_glass_dashboard(request):
    """GET /council/glass - Council Liquid Glass secondary theme, read from disk per request."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.Response(text="blocked", status=403, headers=cors)

    glass_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "orchestrator", "council", "glass_dashboard.html",
    )
    try:
        with open(glass_path, "r", encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return web.Response(
            text="glass theme file missing",
            status=404,
            headers=_cors(request.headers.get("Origin"), {"Cache-Control": "no-store, max-age=0"}),
        )
    return web.Response(
        text=html,
        content_type="text/html",
        charset="utf-8",
        headers=_cors(request.headers.get("Origin"), {"Cache-Control": "no-store, max-age=0"}),
    )

_LOG_LINE_RE = __import__('re').compile(
    r'^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[\.,]\d+)?)'
    r'\s+(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)'
    r'\s+(?P<msg>.*)$'
)


def _read_recent_log_lines(log_file: Path | None = None, max_bytes: int | None = None) -> list[str]:
    log_file = log_file or LOG_FILE
    max_bytes = LOG_TAIL_BYTES if max_bytes is None else max_bytes
    if not log_file.exists():
        return []

    size = log_file.stat().st_size
    start = max(0, size - max_bytes)
    with log_file.open('rb') as f:
        f.seek(start)
        if start:
            f.readline()
        data = f.read()
    return data.decode('utf-8', errors='replace').splitlines()


async def handle_logs(request):
    since = request.query.get('since', '')
    entries: list[dict] = []
    raw_lines = 0

    for line in _read_recent_log_lines():
        line = line.rstrip()
        if not line:
            continue
        m = _LOG_LINE_RE.match(line)
        if not m:
            # Preserve raw lines as explicit records so diagnostics
            # can still consume them structurally.
            raw_lines += 1
            entries.append({
                'timestamp': None,
                'level': 'raw',
                'message': line,
            })
            continue
        ts = m.group('ts')
        if since and ts <= since:
            continue
        level_raw = m.group('level').lower()
        level_map = {'warning': 'warning', 'error': 'error', 'critical': 'error'}
        entries.append({
            'timestamp': ts,
            'level': level_map.get(level_raw, 'info'),
            'message': m.group('msg'),
        })

    return web.json_response(
        entries[-100:],
        headers=_cors(request.headers.get('Origin'),
                       extra={
                           'Cache-Control': 'no-store, max-age=0',
                           'X-Log-Tail-Bytes': str(LOG_TAIL_BYTES),
                           'X-Log-Malformed': '0',
                           'X-Log-Raw-Lines': str(raw_lines),
                       })
    )


_HEALTH_LANE_COMPACT_KEYS = ("name", "active", "queued", "accepting")


def _health_query_truthy(query, *names) -> bool:
    getter = getattr(query, "get", None)
    if getter is None:
        return False
    for name in names:
        raw = str(getter(name) or "").strip().lower()
        if raw in {"1", "true", "yes", "full", "verbose", "on"}:
            return True
    return False


def _apply_health_view(payload: dict, request) -> dict:
    """L3: default /health is compact; ?verbose=1 keeps full lanes; ?fields= subsets."""
    query = getattr(request, "query", None)
    verbose = _health_query_truthy(query, "verbose", "full")
    if not verbose:
        lanes = payload.get("executor_lanes") or []
        compact_lanes = []
        for row in lanes:
            if isinstance(row, dict):
                compact_lanes.append({k: row.get(k) for k in _HEALTH_LANE_COMPACT_KEYS})
        payload["executor_lanes"] = compact_lanes
        payload["health_view"] = "compact"
        payload["ca_hint"] = (
            "Default /health is compact (executor_lanes drop fair_admission). "
            "Use ?verbose=1 for full lane rows. "
            "Use ?fields=ok,pid,startup_degraded,durable_audit_ok to subset."
        )
        for key in (
            "tool_compatibility_use_total",
            "tool_compatibility_ambiguous_total",
            "tool_compatibility_unknown_total",
        ):
            if not payload.get(key):
                payload.pop(key, None)
    else:
        payload["health_view"] = "verbose"
    fields = _r101_parse_fields(
        query.get("fields") if query is not None and hasattr(query, "get") else None
    )
    if not fields:
        return payload
    filtered = {k: payload[k] for k in fields if k in payload}
    if "ok" in payload:
        filtered.setdefault("ok", payload["ok"])
    missing = sorted(set(fields) - set(filtered))
    if missing:
        filtered["omitted_unknown_fields"] = missing
    notes = {key: value for key, value in (payload.get("field_notes") or {}).items()
             if key in fields}
    if notes:
        filtered["field_notes"] = notes
    filtered["health_fields_filtered"] = True
    return filtered


async def handle_health(request):
    """GET /health - readiness plus runtime capability contract."""
    """GET /health - readiness plus runtime capability contract."""
    _base = os.environ.get("ORCHESTRATOR_BASE_URL", f"http://localhost:{PORT}")
    ready = bool(SERVER_READY)
    accounting = _POST_CALL_ACCOUNTING.status()
    durable_audit, durable_metrics_error = await _durable_metrics_for_health()
    restart_controller_health = await _controller_health_for_health(
        RESTART_CONTROL_URL)
    executors = executor_lane_status()
    executor_active = sum(int(row.get("active", 0)) for row in executors)
    executor_queued = sum(int(row.get("queued", 0)) for row in executors)
    executor_oldest_active_age_ms = max(
        (float(row.get("oldest_active_age_ms", 0.0)) for row in executors),
        default=0.0,
    )
    durable_audit_ok = _durable_audit_ok_from_metrics(
        durable_audit, durable_metrics_error
    )
    def json_safe(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, BaseException):
            return {"error_type": type(value).__name__, "error_message": str(value)}
        if isinstance(value, dict):
            return {str(key): json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [json_safe(item) for item in value]
        return str(value)

    payload = {
        "ok": ready,
        "ready": ready,
        "startup_phase": SERVER_STARTUP_PHASE,
        "server_stopping": bool(SERVER_STOPPING),
        "startup_degraded": bool(SERVER_STARTUP_DEGRADED),
        "startup_error": SERVER_STARTUP_ERROR or None,
        "startup_recovery": json_safe(dict(SERVER_STARTUP_RECOVERY)),
        "service": SERVICE_NAME,
        "pid": os.getpid(),
        "port": PORT,
        "generation": SERVER_GENERATION,
        "started_at": SERVER_STARTED_AT,
        "uptime_s": round(max(0.0, time.monotonic() - SERVER_STARTED_MONOTONIC), 3),
        "mcp_generation": None,
        "field_notes": {"mcp_generation": (
            "Unavailable on HTTP daemon: each MCP process has its own identity. "
            "Read mcp_generation on that MCP response; generation here identifies the daemon."
        )},
        "livez_protocol": LIVEZ_PROTOCOL,
        "livez_url": f"http://127.0.0.1:{LIVEZ_PORT}/livez",
        # NOT `controller_health(...)` inline: it is a blocking urlopen, and a
        # refused connect to a down :8085 cost 110ms of every /health. See
        # `_controller_health_for_health`.
        "restart_controller": json_safe(restart_controller_health),
        "restart_busy_snapshot_alive": bool(
            _RESTART_SNAPSHOT_PUBLISHER and _RESTART_SNAPSHOT_PUBLISHER.alive
        ),
        "restart_busy_snapshot_reap": json_safe(_RESTART_SNAPSHOT_REAP),
        "base_url_hint": _base,
        "bind_host": os.environ.get("ORCHESTRATOR_HOST", "localhost"),
        "environment_note": "Set ORCHESTRATOR_BASE_URL env var to override default base URL",
        "queue_depth": _requests_in_flight,
        "queue_depth_semantics": "requests_in_flight",
        "p95_ms": _api_call_p95_ms(),
        "executor_active_total": executor_active,
        "executor_queued_total": executor_queued,
        "executor_oldest_active_age_ms": round(executor_oldest_active_age_ms, 3),
        "executor_accepting": all(bool(row.get("accepting")) for row in executors),
        "executor_lanes": executors,
        "accounting_ok": not accounting["degraded"],
        "accounting_queue_depth": accounting["pending"],
        "accounting_queue_depth_semantics": "accepted_pending_including_in_flight",
        "accounting_queue_capacity": accounting["capacity"],
        "accounting_in_flight": accounting["in_flight"],
        "accounting_queue_high_watermark": accounting["high_watermark"],
        "accounting_enqueued_total": accounting["enqueued"],
        "accounting_processed_total": accounting["processed"],
        "accounting_failed_total": accounting["failed"],
        "accounting_rejected_total": accounting["rejected"],
        "accounting_degraded": accounting["degraded"],
        "accounting_last_error": accounting["last_error"],
        "accounting_oldest_age_ms": accounting["oldest_age_ms"],
        "accounting_worker_alive": accounting["worker_alive"],
        "durable_audit_ok": durable_audit_ok,
        "durable_audit_pending": durable_audit.get("pending"),
        "durable_audit_applied": durable_audit.get("applied"),
        "durable_audit_queue_capacity": durable_audit.get("capacity"),
        "durable_audit_staged_total": durable_audit.get("staged_total"),
        "durable_audit_duplicate_total": durable_audit.get("duplicate_total"),
        "durable_audit_applied_total": durable_audit.get("applied_total"),
        "durable_audit_apply_failures": durable_audit.get("apply_failures"),
        "durable_audit_capacity_rejections": durable_audit.get("capacity_rejections"),
        "durable_audit_admission_open": durable_audit.get("admission_open"),
        "durable_audit_worker_alive": durable_audit.get("worker_alive"),
        "durable_audit_active_leases": durable_audit.get("active_leases"),
        "durable_audit_expired_leases": durable_audit.get("expired_leases"),
        "durable_audit_lease_claimed_total": durable_audit.get("lease_claimed_total"),
        "durable_audit_lease_recovered_total": durable_audit.get("lease_recovered_total"),
        "durable_audit_next_retry_at": durable_audit.get("next_retry_at"),
        "durable_audit_metrics_age_ms": durable_audit.get("metrics_age_ms"),
        "durable_audit_metrics_stale": bool(durable_audit.get("metrics_stale")),
        "durable_audit_last_error": durable_metrics_error or durable_audit.get("last_error"),
        # R81: surface auth posture in health so CA knows up-front whether
        # protected (mutating) tools will demand X-Orchestrator-Token.
        "auto_token_inject": bool(AUTO_TOKEN_INJECT),
        "mutation_token_required": (not bool(AUTO_TOKEN_INJECT)),
        "auth_hint": ("protected tools run token-free (AUTO_TOKEN_INJECT=1)"
                      if AUTO_TOKEN_INJECT else
                      "mutating tools need X-Orchestrator-Token; GET /api/token then POST /api/config/auto_token_inject {\"enabled\":true} to run token-free"),
    }
    # CAP R3: deprecated-field compatibility observability (bounded labels: tool+field only)
    payload["tool_compatibility_use_total"] = {
        f"{t}|{f}": c for (t, f), c in sorted(_COMPAT_USE_TOTAL.items())
    }
    payload["tool_compatibility_ambiguous_total"] = {
        f"{t}|{f}": c for (t, f), c in sorted(_COMPAT_AMBIGUOUS_TOTAL.items())
    }
    payload["tool_compatibility_unknown_total"] = {
        f"{t}|{f}": c for (t, f), c in sorted(_COMPAT_UNKNOWN_TOTAL.items())
    }
    extra_headers = None if ready else {"Retry-After": "1"}
    return web.json_response(
        _apply_health_view(payload, request),
        status=200 if ready else 503,
        headers=_cors(request.headers.get('Origin'), extra_headers),
    )


def _assert_get_routes(app: web.Application, required_paths: tuple[str, ...]) -> None:
    """Fail fast if expected GET routes are not registered."""
    get_paths = set()
    for route in app.router.routes():
        if route.method != "GET":
            continue
        resource = getattr(route, "resource", None)
        canonical = getattr(resource, "canonical", None)
        if canonical:
            get_paths.add(canonical)
    missing = [p for p in required_paths if p not in get_paths]
    if missing:
        raise RuntimeError(f"Missing required GET routes: {missing}")


async def handle_config(request):
    config = {
        "mcpServers": {
            "project-orchestrator": {
                "command": sys.executable,
                "args": [SERVER_PATH, "--mcp"],
                "env": {},
            }
        }
    }
    return web.json_response(config, headers=_cors(request.headers.get('Origin')))

# ─── Code Intelligence endpoints ─────────────────────────────────────────────

# A09: the only query params /api/tools/names actually honours. `phase` is the
# documented alias of `tier`. Anything else is surfaced via ignored_query_params
# rather than silently swallowed.
_TOOL_NAMES_KNOWN_QUERY_PARAMS = frozenset({
    "tier", "phase", "name_contains", "category", "limit", "suppress_hints",
})


async def handle_tools_names(request):
    """V15 R16.9 — Ultra-compact /api/tools/names endpoint.

    Returns just {name, tier} pairs (no schema, no description) -- an order of
    magnitude smaller than the full /api/tools payload. Use first to discover what
    exists, then call /api/tools?name_contains=<name> for the few you need.

    Same filters: ?tier=T4, ?phase=T4, ?name_contains=substr.
    """
    tier_filter = request.query.get("tier") or request.query.get("phase") or ""
    name_contains = request.query.get("name_contains") or ""
    category = request.query.get("category") or ""
    try:
        limit = int(request.query.get("limit") or 0)
    except (TypeError, ValueError):
        limit = 0
    suppress_hints = _health_query_truthy(request.query, "suppress_hints")
    payload = _tool_names_payload(tier_filter, name_contains, category, limit,
                                  suppress_hints=suppress_hints)
    # A09: this GET used to swallow unknown query params (curated/all/bounded/…)
    # silently, so `?curated=0` and `?bogus=1` were indistinguishable from the
    # baseline. Mirror the A17 precedent (surface what was dropped, don't swallow)
    # and the POST UNSUPPORTED_FIELD contract: report ignored params instead of
    # pretending they took effect. Only emitted when non-empty so a clean call
    # carries no false signal.
    # set(): request.query is a multidict, so `?curated=0&curated=1` yields the
    # key twice. Report each unknown param once.
    ignored = sorted({
        k for k in request.query.keys()
        if k not in _TOOL_NAMES_KNOWN_QUERY_PARAMS
    })
    if ignored:
        # copy before mutating: _tool_names_payload is shared with the MCP
        # discovery path (orchestrator/server/mcp_stdio.py) and the parity
        # script, so an HTTP-only field must never leak into a payload another
        # caller holds -- today it returns a fresh dict, but that is its
        # implementation detail, not a contract this handler may rely on.
        payload = dict(payload)
        payload["ignored_query_params"] = ignored
        payload["ignored_query_params_hint"] = (
            "These query params are not supported by /api/tools/names and were "
            "ignored. Supported: " + ", ".join(sorted(_TOOL_NAMES_KNOWN_QUERY_PARAMS))
            + f". The unfiltered default is the curated core; pass limit={len(ct.TOOL_MAP)} for the "
            "full catalog."
        )
    return web.json_response(
        payload,
        headers=_cors(request.headers.get('Origin'), extra={"Cache-Control": "no-store, max-age=0"}),
    )


# ─── CBM graph bridge endpoints for [Q] (Phase 1.5 B2) ──────────────────────

def _graph_clean_path(project_path: str) -> tuple:
    """Boundary guard (mirrors orchestrator.graph.tools._clean_project_path):
    reject NUL-mangled backslash paths, normalize to forward slashes (RISK-3)."""
    if "\x00" in project_path:
        return "", ("project_path contains an embedded NUL character "
                    "(mangled backslash escape) — use forward slashes, e.g. D:/path/to/project")
    return project_path.replace("\\", "/"), None


async def handle_graph_query(request):
    """B2: POST /api/graph/query — bounded graph symbol lookup for [Q].

    Body: {project_path, query, limit?}. Always 200 + valid JSON, fail-open:
    graph disabled/missing/error → graph_status metadata + empty results.
    """
    origin = request.headers.get('Origin')
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON body",
                                  "graph_status": "error"}, headers=_cors(origin))
    project_path, path_err = _graph_clean_path(str(data.get("project_path") or ""))
    query = str(data.get("query") or data.get("name") or "")
    if path_err or not project_path or not query:
        return web.json_response({"ok": False, "graph_status": "error",
                                  "error": path_err or "project_path and query required"},
                                 headers=_cors(origin))
    try:
        limit = max(1, min(int(data.get("limit") or 20), 50))
    except (TypeError, ValueError):
        limit = 20
    try:
        from orchestrator.graph.config import GRAPH_CONTEXT_TIMEOUT_S
        from orchestrator.graph.query import graph_find_symbol
        res = await asyncio.wait_for(
            asyncio.to_thread(graph_find_symbol, project_path, query, limit=limit),
            timeout=GRAPH_CONTEXT_TIMEOUT_S + 0.5)
        status = "ok" if res.get("enabled") else "disabled"
        if res.get("error"):
            status = "error"
        return web.json_response({"ok": True, "graph_status": status,
                                  "graph_used": bool(res.get("results")),
                                  "results": res.get("results") or [],
                                  "elapsed_ms": res.get("elapsed_ms", 0),
                                  "error": res.get("error")}, headers=_cors(origin))
    except asyncio.TimeoutError:
        return web.json_response({"ok": True, "graph_status": "timeout", "graph_used": False,
                                  "results": [], "error": "server_timeout"},
                                 headers=_cors(origin))
    except Exception as e:
        return web.json_response({"ok": True, "graph_status": "error", "graph_used": False,
                                  "results": [], "error": f"{type(e).__name__}: {e}"},
                                 headers=_cors(origin))


async def handle_graph_context(request):
    """B2: POST /api/graph/context — bounded advisory context pack for [Q]/council.

    Body: {project_path, name, direction?, depth?}. Symbol lookup + bounded trace
    from the top hit. Always 200 + valid JSON, fail-open with graph_status /
    graph_used / graph_degraded_reason metadata (plan DoD #5).
    """
    origin = request.headers.get('Origin')
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON body",
                                  "graph_status": "error"}, headers=_cors(origin))
    project_path, path_err = _graph_clean_path(str(data.get("project_path") or ""))
    name = str(data.get("name") or data.get("query") or "")
    if path_err or not project_path or not name:
        return web.json_response({"ok": False, "graph_status": "error",
                                  "error": path_err or "project_path and name required"},
                                 headers=_cors(origin))
    direction = str(data.get("direction") or "out")
    try:
        depth = max(0, min(int(data.get("depth") or 2), 5))
    except (TypeError, ValueError):
        depth = 2
    payload = {"ok": True, "graph_status": "ok", "graph_used": False,
               "graph_degraded_reason": None, "symbols": [], "edges": [],
               "truncated": False, "elapsed_ms": 0}
    try:
        from orchestrator.graph.config import (
            COUNCIL_CONTEXT_MAX_SYMBOLS,
            GRAPH_CONTEXT_TIMEOUT_S,
            GRAPH_ENABLED,
        )
        from orchestrator.graph.query import cbm_trace_path, graph_find_symbol
        if not GRAPH_ENABLED:
            payload["graph_status"] = "disabled"
            payload["graph_degraded_reason"] = "GRAPH_ENABLED=0"
            return web.json_response(payload, headers=_cors(origin))
        fs = await asyncio.wait_for(
            asyncio.to_thread(graph_find_symbol, project_path, name,
                              limit=COUNCIL_CONTEXT_MAX_SYMBOLS),
            timeout=GRAPH_CONTEXT_TIMEOUT_S + 0.5)
        if fs.get("error"):
            payload["graph_status"] = "error"
            payload["graph_degraded_reason"] = fs["error"]
            return web.json_response(payload, headers=_cors(origin))
        hits = fs.get("results") or []
        payload["symbols"] = [
            {"id": n.get("id"), "kind": n.get("kind"), "name": n.get("name"),
             "qualified_name": n.get("qualified_name"), "file_path": n.get("file_path"),
             "start_line": n.get("start_line"), "signature": n.get("signature")}
            for n in hits[:COUNCIL_CONTEXT_MAX_SYMBOLS]
        ]
        if hits:
            tr = await asyncio.wait_for(
                asyncio.to_thread(cbm_trace_path, project_path, hits[0]["id"],
                                  direction=direction, depth=depth),
                timeout=GRAPH_CONTEXT_TIMEOUT_S + 0.5)
            payload["edges"] = tr.get("edges") or []
            payload["truncated"] = bool(tr.get("truncated"))
            payload["elapsed_ms"] = tr.get("elapsed_ms", 0)
            if tr.get("error"):
                payload["graph_status"] = "error"
                payload["graph_degraded_reason"] = tr["error"]
            elif tr.get("truncation_reason") == "start_node_not_found":
                payload["graph_degraded_reason"] = "start_node_not_found"
        payload["graph_used"] = bool(payload["symbols"] or payload["edges"])
        if not payload["graph_used"] and payload["graph_status"] == "ok":
            payload["graph_degraded_reason"] = "no_match"
        return web.json_response(payload, headers=_cors(origin))
    except asyncio.TimeoutError:
        payload["graph_status"] = "timeout"
        payload["graph_degraded_reason"] = "server_timeout"
        return web.json_response(payload, headers=_cors(origin))
    except Exception as e:
        payload["graph_status"] = "error"
        payload["graph_degraded_reason"] = f"{type(e).__name__}: {e}"
        return web.json_response(payload, headers=_cors(origin))


async def handle_tools(request):
    """Return schemas for all public tools exposed by TOOL_MAP.

    V15 R16.6: query params `tier` (T1|T2|T3|T4) and `name_contains` (substring)
    filter the response so CA can request smaller subsets.
    V15 R16.8: `phase` accepted as alias of `tier` (CA terminology parity).
    V15 R16.9: see also GET /api/tools/names for ultra-compact 3KB listing.
    """
    tier_filter = (request.query.get("tier") or request.query.get("phase") or "").strip().upper()
    name_contains = (request.query.get("name_contains") or "").strip().lower()
    visible = set(ct.TOOL_MAP.keys())
    schemas = []
    for schema in ct.TOOL_SCHEMAS:
        fn = schema.get("function", {})
        name = fn.get("name")
        if name not in visible:
            continue
        if name_contains and name_contains not in (name or "").lower():
            continue
        if tier_filter and ct.TOOL_PHASES.get(name, "").upper() != tier_filter:
            continue
        out_schema = copy.deepcopy(schema)
        out_fn = out_schema.setdefault("function", {})
        out_fn["payload_example"] = _payload_example_for_tool(name, out_fn)
        out_fn["protected"] = name in ct.PROTECTED_TOOL_NAMES
        out_fn["mutating"] = _is_mutating_tool(name)
        out_fn["requires_token"] = name in _TOKEN_REQUIRED_TOOLS
        # V15 R28 — finer risk scope (file_artifact|runtime_ui|process|audit|none)
        try:
            _scope = ct.tool_mutation_scope(name)
        except Exception:
            _scope = "none"
        out_fn["mutation_scope"] = _scope
        # E-03 (2026-08-10): stateful marker for workflow/mission run-state
        # tools. Distinguishes "writes durable state but is not in the
        # destructive token/session gate" from "truly read-only" so clients
        # stop assuming idempotence on mutation_scope='none' tools.
        out_fn["stateful"] = (_scope == "stateful")
        out_fn["requires_session"] = _requires_session(name)
        schemas.append(out_schema)
    return web.json_response(
        schemas,
        headers=_cors(request.headers.get('Origin'), extra={"Cache-Control": "no-store, max-age=0"}),
    )


def _enriched_tool_schema(name: str):
    """The ONE definition of "the schema a CA gets" for a single tool; None if unknown.

    A37: this body used to live inline inside handle_tool_describe, so the #0 first-use
    schema push had nothing to call -- and hand-rolling a second enrichment next to the
    dispatcher is exactly the drift shape this campaign is closing (same fact, two
    places, one of them going stale). Extracted, not duplicated: the HTTP route and the
    push now return the byte-identical object.
    """
    if name not in ct.TOOL_MAP:
        return None
    for schema in ct.TOOL_SCHEMAS:
        fn = schema.get("function", {})
        if fn.get("name") != name:
            continue
        out_schema = copy.deepcopy(schema)
        out_fn = out_schema.setdefault("function", {})
        out_fn["payload_example"] = _payload_example_for_tool(name, out_fn)
        out_fn["protected"] = name in ct.PROTECTED_TOOL_NAMES
        out_fn["mutating"] = _is_mutating_tool(name)
        out_fn["requires_token"] = name in _TOKEN_REQUIRED_TOOLS
        try:
            out_fn["mutation_scope"] = ct.tool_mutation_scope(name)
        except Exception:
            out_fn["mutation_scope"] = "none"
        out_fn["requires_session"] = _requires_session(name)
        return out_schema
    return None


async def handle_tool_describe(request):
    """R81 — GET /api/tools/{tool_name}/schema : full schema for ONE tool by exact name.

    Convenience over `GET /api/tools?name_contains=` (which substring-matches and may
    return several). Returns the single tool's enriched schema (payload_example,
    protected/mutating/requires_token/requires_session/mutation_scope), or 404 with the
    closest-name suggestions so CA can fix a typo without dumping the full tool catalog
    (see /api/tools/names?limit=250 for the live count — D5 2026-08-11: the doc used to
    hard-code "all 174 tools", which drifted as tools were added/removed).

    The schema itself is built by _enriched_tool_schema, shared with the A37 #0 first-use
    push, so a CA that was handed a schema and a CA that asked for one get the same text.
    """
    name = (request.match_info.get('tool_name') or '').strip()
    if name not in ct.TOOL_MAP:
        return web.json_response(
            _unknown_tool_payload(name), status=404,
            headers=_cors(request.headers.get("Origin")),
        )
    out_schema = _enriched_tool_schema(name)
    if out_schema:
        return web.json_response(
            {"ok": True, "tool": out_schema},
            headers=_cors(request.headers.get('Origin'), extra={"Cache-Control": "no-store, max-age=0"}),
        )
    # Registered in TOOL_MAP but no schema entry (should not happen)
    return web.json_response(
        {"ok": False, "error": f"No schema for '{name}'", "error_name": "SCHEMA_MISSING"},
        status=500, headers=_cors(request.headers.get('Origin')),
    )


async def _dispatch_tool_call_request(request, transport: str = "api"):
    """Shared in-process dispatcher for API and MCP tool calls.

    API passes the real aiohttp request. MCP builds a tiny in-process request
    object and enters here directly, so it does not call back through localhost
    HTTP while still reusing the same validation, guard, and logging path.
    """
    origin = request.headers.get('Origin')
    cors = _cors(origin)
    tool_name = request.match_info.get('tool_name', '')

    # R101: read field selector from HTTP ?fields= now; body `fields` is unioned after parse.
    try:
        _r101_fields = _r101_parse_fields(getattr(request, "query", {}).get("fields"))
    except Exception:
        _r101_fields = set()
    _R101_FIELDS.set(_r101_fields or None)

    # ── Fix 1: localhost-only + Origin allowlist ──
    if not _is_local(request):
        return web.json_response({"error": "Localhost only"}, status=403, headers=cors)
    if not _origin_ok(request):
        return web.json_response(
            {"error": f"Origin '{origin}' not in allowlist"}, status=403, headers=cors
        )

    fn = ct.TOOL_MAP.get(tool_name)
    if fn is None:
        return _error_response(
            tool_name, f"Unknown tool {tool_name!r}", 404, cors, {},
            error_name="UNKNOWN_TOOL",
        )

    # F3: Differentiate between empty body (allowed fallback) and corrupt JSON (400)
    try:
        raw_bytes = await request.read()
    except Exception:
        raw_bytes = b""
    if raw_bytes:
        try:
            decoded_body = raw_bytes.decode("utf-8", errors="replace")
            body = (await asyncio.to_thread(json.loads, decoded_body)
                    if len(raw_bytes) >= 65536 else json.loads(decoded_body))
        except (json.JSONDecodeError, ValueError) as e:
            # Log raw bytes for debugging
            logger.error(f"Invalid JSON body (raw length {len(raw_bytes)}): {raw_bytes[:200]!r}")
            return _error_response(
                tool_name,
                "Invalid JSON body",
                400,
                cors,
                {},
                hint="Use PowerShell $body | ConvertTo-Json -Depth 20; base64-encode multiline content.",
            )
    else:
        body = {}

    # Auto-identity for council messaging over HTTP: workers are HTTP-only and the
    # daemon has no per-worker env, so the worker's helper passes its [A]-assigned
    # identity in the X-Council-Agent header (set from $COUNCIL_AGENT_ID at launch).
    # Force it as the sender so a worker cannot spoof/omit who it is. The MCP path is
    # covered separately by the COUNCIL_AGENT_ID env stamp in dispatch_tool_call().
    if tool_name in ("council_message", "council_message_async") and isinstance(body, dict):
        # CAN-020/021: resolve sender server-side. PID-trace is authoritative; a
        # non-worker caller can only self-label as leader/user, never impersonate a
        # worker, and an arbitrary string normalizes to 'user'. Drops the 'from' alias.
        body["sender"] = _council_resolve_sender(request, body.get("sender") or body.get("from") or "")
        for _alias in ("from", "frm", "from_agent"):
            body.pop(_alias, None)

    # Task-epoch metadata is host transport metadata, never forwarded to tool handlers —
    # EXCEPT session_id when the tool's own signature declares it (begin_task,
    # get_task_checkpoint): popping it unconditionally made valid calls fail with
    # MISSING_REQUIRED_FIELD even though the field was in the body (fixed 2026-07-16).
    # CAN-054: host task envelope — headers take precedence over body fields so
    # domain parameters named session_id (get_session_events target) no longer
    # collide with transport metadata.
    _hdr_sid = str(request.headers.get("X-Session-Id", "") or request.headers.get("X-Task-Session-ID", "") or "")
    _hdr_epoch = request.headers.get("X-Task-Epoch")
    _hdr_ph = str(request.headers.get("X-Prompt-Hash", "") or "")
    _task_session_id = _hdr_sid or (str(body.get("session_id", "") or "") if isinstance(body, dict) else "")
    _task_epoch_raw = _hdr_epoch if _hdr_epoch is not None else (body.pop("task_epoch", None) if isinstance(body, dict) else None)
    _task_prompt_hash = _hdr_ph or (str(body.pop("prompt_hash", "") or "") if isinstance(body, dict) else "")
    # A host may pin the daemon generation it bootstrapped against. Reject a
    # stale request before any handler can run; this is distinct from task epoch.
    _expected_generation = str(
        request.headers.get("X-Orchestrator-Generation", "")
        or (body.pop("generation", "") if isinstance(body, dict) else "")
    )
    if _expected_generation and _expected_generation != SERVER_GENERATION:
        return web.json_response({
            "ok": False,
            "error": "Tool call targets a stale orchestrator generation",
            "error_name": "STALE_GENERATION",
            "retryable": False,
            "generation": SERVER_GENERATION,
            "expected_generation": _expected_generation,
            "ca_next_action": "rebootstrap_and_retry",
        }, status=409, headers=cors)
    if isinstance(body, dict) and "session_id" in body:
        try:
            _sid_fn = ct.TOOL_MAP.get(tool_name)
            _sid_params = set(inspect.signature(_sid_fn).parameters) if _sid_fn else set()
        except (TypeError, ValueError):
            _sid_params = set()
        if "session_id" not in _sid_params:
            body.pop("session_id", None)
    _task_agent_id = str(
        request.headers.get("X-Agent-Id", "")
        or request.headers.get("X-Council-Agent", "")
        or (body.get("agent_id", "") if isinstance(body, dict) else "")
        or os.environ.get("COUNCIL_AGENT_ID", "")
    )
    # A37 #0b: publish the resolved identity for the WHOLE dispatch so _error_response --
    # which never receives an agent id -- can re-arm the bootstrap schema push. The gates
    # that reject on schema (UNSUPPORTED_FIELD, MISSING_REQUIRED_FIELD) return ~1300 lines
    # ABOVE the success-path push block, so without this the "an error re-arms the push"
    # promise in pushed_schema_note was FALSE for the most common CA error class. Measured
    # before the fix: after a bogus_param error the next good call still carried no
    # pushed_schema.
    _A37_AGENT_CTX.set(_task_agent_id)
    if isinstance(body, dict):
        try:
            _agent_fn = ct.TOOL_MAP.get(tool_name)
            _agent_signature = inspect.signature(_agent_fn) if _agent_fn else None
            _agent_param = (_agent_signature.parameters.get("agent_id")
                            if _agent_signature else None)
        except (TypeError, ValueError):
            _agent_param = None
        if _agent_param is None:
            # Transport identity is dispatcher metadata for handlers that do not
            # declare it; never leak an unsupported keyword argument.
            body.pop("agent_id", None)
        else:
            # Invocation, session gating, and event attribution must all use the
            # same canonical header > body > environment identity. Preserve the
            # handler's legacy empty/None convention when no identity exists.
            _empty_agent_id = (
                _agent_param.default
                if _agent_param.default in ("", None)
                else ""
            )
            body["agent_id"] = _task_agent_id or _empty_agent_id
    _task_capability = str(request.headers.get("X-Task-Capability", "") or "")
    _peer_host = str(request.remote or "")
    try:
        _task_meta, _issued, _principal_id = await asyncio.to_thread(
            _auth_preamble_sync,
            _task_session_id,
            _task_agent_id,
            _hdr_sid,
            _hdr_epoch,
            _hdr_ph,
            _task_capability,
            transport,
            tool_name,
            _peer_host,
        )
    except ct.TaskCheckpointStorageError as exc:
        return _task_checkpoint_storage_response(exc, cors)
    # B1: bind BEFORE the branch. Only record_error_resolution takes the branch that
    # assigns it, but the re-attach site after S4 runs for EVERY tool, so leaving it
    # unbound raised UnboundLocalError on all 219 others -- the same trap the P11b
    # `reseeded` initializer exists to prevent.
    _validated_calling_context = None
    if tool_name == "record_error_resolution":
        # X-Session-Id alone is caller-controlled. Grant resolution authority only
        # after all host checkpoint fields match a server-issued checkpoint for the
        # exact session+agent identity. Any payload-supplied context is overwritten.
        _task_capability = str(request.headers.get("X-Task-Capability", "") or "")
        _expected_capability_hash = str(
            _issued.get("owner_capability_sha256")
            or _issued.get("delegation_capability_sha256")
            or ""
        )
        _capability_valid = bool(
            _task_capability and _expected_capability_hash
            and _secrets.compare_digest(
                hashlib.sha256(_task_capability.encode("utf-8")).hexdigest(),
                _expected_capability_hash,
            )
        )
        if not (_hdr_sid and _task_agent_id and _hdr_epoch is not None and _hdr_ph
                and _issued and _capability_valid):
            return web.json_response({
                "ok": False,
                "error": "A valid server-issued host task checkpoint is required",
                "error_name": "SESSION_OWNERSHIP_REQUIRED",
                "retryable": False,
                "write_performed": False,
                "applied": False,
                "ca_next_action": "send_server_issued_task_capability_and_checkpoint_headers",
            }, status=403, headers=cors)
        try:
            _resolution_epoch = int(_hdr_epoch)
        except (TypeError, ValueError):
            _resolution_epoch = -1
        if (_resolution_epoch != int(_issued.get("task_epoch", 0))
                or _hdr_ph != str(_issued.get("prompt_hash") or "")):
            return web.json_response({
                "ok": False,
                "error": "Resolution call belongs to a stale or mismatched host task",
                "error_name": "STALE_TASK_EPOCH",
                "retryable": False,
                "write_performed": False,
                "applied": False,
                "ca_next_action": "reload_active_task_checkpoint",
            }, status=409, headers=cors)
        # B1: the trusted identity must NOT be written into `body` here. S4 (below)
        # validates `body` against the registry properties, and calling_context is
        # deliberately absent from them -- the schema description states transport
        # fields are excluded, and a contract test asserts it. Injecting it here made
        # the dispatcher answer 400 UNSUPPORTED_FIELD naming a field the caller never
        # sent, with ca_next_action "remove_unsupported_field" -- a hint the caller
        # cannot act on, so the retry is identical and escalates to 409
        # DUPLICATE_RETRY_BLOCKED. Every happy-path HTTP call was dead.
        # Stash it across S4 instead (same pattern as _normalized_paths / _compat_meta
        # below) and re-attach after the public payload has been accepted. The security
        # property is unchanged and is what forces the stash: a caller-supplied
        # calling_context is still seen by S4 as an unsupported field and rejected, so
        # only this server-validated value can ever reach the handler.
        _validated_calling_context = {
            "validated": True,
            "session_id": _hdr_sid,
            "agent_id": _task_agent_id,
            "task_epoch": _resolution_epoch,
            "prompt_hash": _hdr_ph,
        }
    _task_epoch_required = bool(
        _task_meta
        and (tool_name in _MUTATING_TOOLS or _tool_semaphore(tool_name, body) is not None)
        and tool_name not in {"begin_task", "get_task_checkpoint"}
    )
    if _task_epoch_required and _task_epoch_raw is None:
        return web.json_response({
            "ok": False,
            "error": "Active task checkpoint requires task_epoch for this tool call",
            "error_name": "TASK_EPOCH_REQUIRED",
            "retryable": False,
            **_task_meta,
            "ca_next_action": "reload_active_task_checkpoint",
        }, status=409, headers=cors)
    if _task_meta and _task_epoch_raw is not None:
        try:
            _task_epoch = int(_task_epoch_raw)
        except (TypeError, ValueError):
            _task_epoch = -1
        if _task_epoch != int(_task_meta.get("task_epoch", 0)):
            return web.json_response({
                "ok": False,
                "error": "Tool call belongs to a superseded user task",
                "error_name": "STALE_TASK_EPOCH",
                "retryable": False,
                **_task_meta,
                "ca_next_action": "stop_old_task_and_reload_active_task_checkpoint",
            }, status=409, headers=cors)
        if _task_prompt_hash and _task_prompt_hash != _task_meta.get("prompt_hash"):
            return web.json_response({
                "ok": False,
                "error": "Prompt hash does not match active task",
                "error_name": "STALE_TASK_EPOCH",
                "retryable": False,
                **_task_meta,
                "ca_next_action": "reload_active_task_checkpoint",
            }, status=409, headers=cors)
    # A valid host task envelope is also owner activity. This keeps the
    # diagnostic truthful for CAs that use ordinary ToolA calls and do not emit
    # a separate owner_heartbeat request. The epoch/prompt checks above are the
    # admission boundary; the helper re-checks root ownership atomically.
    if (
        _task_session_id
        and _task_agent_id
        and _task_meta
        and _task_epoch_raw is not None
        and _task_prompt_hash
        and tool_name not in {"begin_task", "get_task_checkpoint", "owner_heartbeat"}
    ):
        try:
            await asyncio.to_thread(
                ct._record_authenticated_owner_activity,
                _task_session_id,
                _task_agent_id,
                _task_epoch_raw,
                _task_prompt_hash,
                tool_name,
            )
        except ct.TaskCheckpointStorageError as exc:
            return _task_checkpoint_storage_response(
                exc,
                cors,
                write_performed=False,
            )

    # Mutating-tool gate: browser-origin allowlist + token check
    # run_project_command: skip token gate for declared read-only commands
    _is_readonly_rpc = False
    if tool_name == 'run_project_command' and isinstance(body, dict):
        body.pop('readonly', None)
        _is_readonly_rpc = _is_readonly_command_body(body)

    # Origin allowlist applies to ALL mutating tools (browser CSRF guard) — unchanged.
    if tool_name in _MUTATING_TOOLS and not _is_readonly_rpc:
        if origin is not None and origin not in _ALLOWED_ORIGINS:
            return web.json_response(
                {"error": f"Mutating tool '{tool_name}' blocked from origin '{origin}'"},
                status=403, headers=cors
            )
    # Token gate is DECOUPLED: only the genuinely destructive subset demands the token.
    # Other mutating tools keep mutating/requires_session semantics but run token-free.
    if tool_name in _TOKEN_REQUIRED_TOOLS and not _is_readonly_rpc:
        # Even local CLI/non-Origin requests must present the mutation token.
        # CAN-001: validity now includes TTL expiry, not just header equality.
        token = request.headers.get('X-Orchestrator-Token')
        _tok_err = _validate_mutation_token(token)
        if _tok_err:
            # V15 R42 opt-in: auto-inject when ORCHESTRATOR_AUTO_TOKEN=1 (User trust mode).
            # CAN-002: dev-bypass is audit-logged and waives ONLY the token — the origin
            # gate (above) and the session gate (below) stay in force. Default OFF.
            if AUTO_TOKEN_INJECT:
                logger.warning(
                    f"AUTO_TOKEN_INJECT active: tool={tool_name} called with {_tok_err} "
                    f"— injecting server token (audit boundary bypassed by user opt-in)"
                )
                try:
                    # Anh 2026-08-11 (Phase A on-loop audit): _log_session_event_safe
                    # calls ct._log_session_event (durable=True) synchronously, which
                    # pins the event loop on every dispatch. Run it OFF the loop.
                    # Exceptions are still swallowed inside _log_session_event_safe;
                    # return value (None) is unchanged.
                    await asyncio.to_thread(
                        _log_session_event_safe,
                        request, "auto_token_inject", "ok",
                        {"tool": tool_name, "reason": f"AUTO_TOKEN_INJECT=1 ({_tok_err})",
                         "origin": origin or "no-origin"},
                        agent_id=_task_agent_id or None,
                    )
                except Exception:
                    pass
                # Bypass token guard only; continue handler flow.
            elif _tok_err == "MUTATION_TOKEN_EXPIRED":
                return _error_response(
                    tool_name,
                    "Mutation token expired; re-fetch GET /api/token for a fresh token",
                    403,
                    cors,
                    body,
                    error_name="MUTATION_TOKEN_EXPIRED",
                    hint="Token TTL elapsed. Call GET /api/token to rotate to a fresh token, then retry with X-Orchestrator-Token.",
                    reason="mutation token past its TTL",
                    accepted_fields=["X-Orchestrator-Token"],
                    payload_example={"headers": {"X-Orchestrator-Token": "<fresh token from GET /api/token>"}},
                    mini_rule="Never delete a file that was not explicitly named and approved. Fail loud: report the error, never swallow it or work around it silently.",
                    write_performed=False,
                    applied=False,
                )
            else:
                return _error_response(
                    tool_name,
                    "Mutation token required for this tool",
                    403,
                    cors,
                    body,
                    error_name="MUTATION_TOKEN_REQUIRED",
                    hint="Call GET /api/token and send X-Orchestrator-Token with the same request. (Or set env ORCHESTRATOR_AUTO_TOKEN=1 for opt-in auto-inject mode.)",
                    reason="protected tool requires X-Orchestrator-Token header",
                    accepted_fields=["X-Orchestrator-Token"],
                    payload_example={"headers": {"X-Orchestrator-Token": "<token from GET /api/token>"}},
                    mini_rule="Never delete a file that was not explicitly named and approved. Fail loud: report the error, never swallow it or work around it silently.",
                    write_performed=False,
                    applied=False,
                )

    # ── Fix: JSON body must be an object ──
    if not isinstance(body, dict):
        return _error_response(tool_name, "JSON body must be an object", 400, cors, {})

    # ── Fix 6: normalize aliased args + reject embedded path controls ──
    try:
        body, _aliases_applied = _normalize_body(tool_name, body)
    except path_runtime.PathControlCharacterError as exc:
        return _error_response(
            tool_name, str(exc), 400, cors, {},
            error_name="INVALID_PATH_CONTROL_CHAR",
            reason=str(exc),
            hint="Strip the embedded C0 control character or re-encode the path, then retry.",
            ca_next_action="strip_or_reencode_path_control_character",
            offending_field=exc.field,
            control_code=f"U+{exc.codepoint:04X}",
            retryable=False,
        )
    except _DeprecatedFieldConflict as exc:
        # CAP R3: ambiguous deprecated+canonical field conflict.
        _dup = _duplicate_retry_guard(
            f"{_task_session_id or transport}:{_task_agent_id or 'legacy'}",
            tool_name, body, "DEPRECATED_FIELD_CONFLICT")
        if _dup:
            return web.json_response({
                "ok": False,
                "error": "Identical deterministic failure already returned a correction",
                "error_name": "DUPLICATE_RETRY_BLOCKED",
                "retryable": False,
                "deprecated_field": exc.field,
                "ca_next_action": "apply_previous_correction_or_stop_and_report",
                **_task_meta,
            }, status=409, headers=cors)
        return _error_response(
            tool_name,
            f"Ambiguous deprecated field '{exc.field}' for search_code: {exc.reason}",
            400, cors, {},
            error_name="DEPRECATED_FIELD_CONFLICT",
            reason=str(exc),
            hint="Remove the deprecated field or align it with the canonical field.",
            ca_next_action="remove_or_align_deprecated_field",
            deprecated_field=exc.field,
            retryable=False,
            removal_version=_SEARCH_COMPAT_REMOVAL_VERSION,
        )
    if not isinstance(body, dict):
        return _error_response(tool_name, "JSON body must be an object", 400, cors, {})
    if _aliases_applied:
        logger.info(f"Tool {tool_name}: arg aliases applied: {_aliases_applied}")

    # Agent-enablement (2026-08-08): find_callers project_path session fallback.
    # Contract: explicit project_path > active session/task's project > error.
    # We NEVER fall back to "." or CWD — that would silently scope the search to
    # the daemon's working directory and return wrong callers. Resolution order:
    #   1) explicit body.project_path if provided (already normalized above)
    #   2) _task_session_id (header X-Session-Id / body.session_id) -> sessions DB
    #   3) page_id (browser tools) -> _BROWSER_PAGES registry
    # If none resolvable, leave body unchanged — the standard MISSING_REQUIRED_FIELD
    # path at S4 raises the actionable 400 for the caller to recover from.
    if tool_name == 'find_callers':
        if not body.get('project_path'):
            _fc_session_project = (
                _resolve_project_from_session_id(_task_session_id)
                or _resolve_project_from_page_id(str(body.get("page_id", "") or ""))
            )
            if _fc_session_project:
                body['project_path'] = _fc_session_project
                _aliases_applied = list(_aliases_applied) + [
                    f"project_path from session/task fallback={_fc_session_project}"
                ]
        # Agent-enablement F2 (2026-08-08): page_id is a dispatcher-consumed
        # resolver input (used ONLY to derive project_path from the
        # _BROWSER_PAGES registry). It is declared in the find_callers registry
        # schema so S4 no longer rejects it as UNSUPPORTED_FIELD, but it is
        # not a real handler kwarg — strip it before handler invocation so it
        # never reaches search_code/handlers as a false argument.
        body.pop('page_id', None)

    # V15 R34: stash _normalized_paths for response surface, remove from body
    # (it is an audit field, not a tool param; would trigger UNSUPPORTED_FIELD otherwise)
    _r34_normalized_paths = body.pop("_normalized_paths", None)
    # CASE-1919 item 3: same contract for the relative-project_path audit entry --
    # an audit field, never a tool param, so it must leave the body here or it
    # would come back as UNSUPPORTED_FIELD on every tool.
    _rel_project_path = body.pop("_relative_project_path", None)
    # CAP R3: stash compat metadata for response surface, remove from body
    _compat_meta = body.pop("_compat_meta", None)
    # R101: strip `fields` from body BEFORE allowed_params/unsupported so it never trips
    # UNSUPPORTED_FIELD; union with any HTTP ?fields= selector.
    # B027, decided 2026-09-18: that unconditional pop is what makes the projection
    # available on EVERY tool -- and it silently ate the argument of the two DOM-state
    # tools whose `fields` is a real handler parameter (a list of DOM-state names to
    # capture).  They are exempt from the pop, so their own value reaches the handler.
    # The URL selector `?fields=` still projects their response: that one is
    # unambiguous, it was the BODY key that collided.  The exemption is earned by the
    # R101 schema marker, never by a name -- see _r101_pop_exempt_tools.
    if tool_name in _r101_pop_exempt_tools():
        _r101_bf = None
    else:
        _r101_bf = _r101_parse_fields(body.pop("fields", None))
    if _r101_bf:
        _r101_fields = (_r101_fields or set()) | _r101_bf
        _R101_FIELDS.set(_r101_fields or None)
    # A34 W2 P2c: strip `pipeline` BEFORE allowed_params/unsupported, for exactly the reason
    # `fields` is stripped directly above and `page_id` above that -- it is a DISPATCHER-
    # consumed field, never a handler kwarg, so leaving it in body would make the source tool
    # fail on an unexpected keyword argument. It is popped for EVERY tool, not only the two
    # that offer steps: a tool that offers none then answers a report naming what it does
    # offer (an empty list), which tells the caller more than UNSUPPORTED_FIELD would -- the
    # same self-describing choice `fields` makes with fields_unknown. Declared in the
    # read_file_range / search_code registry schemas all the same, because B-01 and B019 are
    # the same lesson twice: a field absent from the registry is a field no CA can discover.
    # Consumed at the response funnel below, where the requested steps actually run.
    _active_pipeline = body.pop("pipeline", None)
    # S4: registry properties are the public contract; signatures are internal.
    _registry_params = _schema_for_tool(tool_name).get("parameters") or {}
    _registry_props = _registry_params.get("properties") or {}
    _alias_sources = set(_ARG_ALIASES.get(tool_name, {}))
    allowed_params = set(_registry_props)
    accepted_fields = allowed_params | _alias_sources
    unsupported = sorted(k for k in body.keys() if k not in allowed_params)
    if unsupported:
        retry_scope = f"{_task_session_id or 'legacy'}:{_task_agent_id or 'legacy'}"
        # CAP R3: count truly-unknown fields per field (bounded cardinality)
        for _uf in unsupported:
            _compat_metric_unknown(tool_name, _uf)
        retry_material = json.dumps({"tool": tool_name, "body": body, "unsupported": unsupported},
                                    ensure_ascii=False, sort_keys=True, default=str)
        retry_key = (str(retry_scope), hashlib.sha256(retry_material.encode("utf-8")).hexdigest(),
                     "UNSUPPORTED_FIELD")
        retry_count = int(_DUPLICATE_FAILURE_STATE.get(retry_key, 0)) + 1
        _DUPLICATE_FAILURE_STATE[retry_key] = retry_count
        if retry_count >= 2:
            return web.json_response({
                "ok": False,
                "error": "Identical deterministic failure already returned a correction",
                "error_name": "DUPLICATE_RETRY_BLOCKED",
                "retryable": False,
                "unsupported_fields": unsupported,
                "accepted_aliases": _alias_metadata(tool_name),
                "ca_next_action": "apply_previous_correction_or_stop_and_report",
                **_task_meta,
            }, status=409, headers=cors)
        err_name = "UNSUPPORTED_DRY_RUN" if "dry_run" in unsupported and tool_name in {"write_fix_html"} else "UNSUPPORTED_FIELD"
        # R19 Phase D — corrected_payload now preserves intent via alias mapping
        # before falling back to registry stub. CA's value is mapped to canonical
        # field name when an alias exists, instead of being stripped silently.
        aliases_for_tool = _ARG_ALIASES.get(tool_name, {})
        ignore_for_tool = _HARMLESS_IGNORE_FIELDS.get(tool_name, set())
        corrected = {}
        alias_remediable = []  # which unsupported fields had alias map available
        for k, v in body.items():
            if k in set(unsupported):
                if k in aliases_for_tool:
                    canonical = aliases_for_tool[k]
                    if canonical not in body and canonical not in corrected:
                        corrected[canonical] = v
                        alias_remediable.append(f"{k}->{canonical}")
                elif k in ignore_for_tool:
                    continue  # silently drop harmless
                # else: strip (no recoverable intent)
            else:
                corrected[k] = v
        # Registry-derived fallback cannot introduce undeclared fields.
        if not corrected:
            corrected = dict(_payload_example_for_tool(tool_name, _schema_for_tool(tool_name)))
            corrected_source = "registry_payload_example_fallback" if corrected else "empty_no_example"
        elif alias_remediable:
            corrected_source = "alias_mapped_intent_preserved"
        else:
            corrected_source = "stripped_unsupported_fields"
        # R19 Phase C — ca_next_action surfaces remediation path explicitly
        ca_next = (
            "retry_with_alias_mapping" if alias_remediable
            else "remove_unsupported_field"
        )
        return _error_response(
            tool_name,
            f"Unsupported field(s) for '{tool_name}': {', '.join(unsupported)}",
            400,
            cors,
            body,
            error_name=err_name,
            unsupported_fields=unsupported,
accepted_fields=sorted(accepted_fields),
            accepted_aliases=_alias_metadata(tool_name),  # R19: always include
            corrected_payload=corrected,
            corrected_payload_source=corrected_source,
            alias_remediable=alias_remediable,
            ca_next_action=ca_next,
        )

    # ── LIVE-GAP-01 (2026-08-10): dispatcher-side mode defaults for the
    # legacy search_code aliases. The registry TOOL_MAP points these tools
    # directly at search_code, but their PUBLIC schemas do not declare a
    # `mode` property — `mode` is internal delegation metadata consumed by
    # search_code's branch dispatch (text|regex|symbol|callers|
    # definition_usage|dom_candidates). Injecting the default BEFORE S4
    # (i.e. inside _normalize_body) makes S4 reject the injected field as
    # UNSUPPORTED_FIELD, which broke the live find_callers alias in
    # 0443b67 and latently broke query_dom_candidates from FixA2407 P1-2.
    # Injecting HERE — after S4 has accepted the public payload — keeps
    # two invariants:
    #   1) A caller-supplied `mode` on these aliases is still rejected by
    #      S4 (it runs against the body BEFORE this block), preserving the
    #      public contract that the alias does not expose mode.
    #   2) When no caller-supplied `mode` was present (the normal case),
    #      the alias gains the documented mode so search_code routes to
    #      the correct branch and returns isolated callers/definitions/
    #      dom_candidates rather than literal-text hits.
    _aliases_applied = _apply_alias_mode_default_post_s4(
        tool_name, body, _aliases_applied
    )

    # B1: re-attach the server-validated identity now that S4 has accepted the
    # public payload. Deliberately AFTER S4 and never before it -- see the stash
    # site above. `_validated_calling_context` is bound only on the branch where
    # every host checkpoint field matched a server-issued checkpoint, so a
    # caller-supplied calling_context cannot reach the handler: S4 has already
    # rejected the request as UNSUPPORTED_FIELD by the time this line runs.
    if _validated_calling_context is not None:
        body["calling_context"] = _validated_calling_context

    required_params = _required_field_names(tool_name)
    missing_required = [name for name in required_params if name not in body]
    if missing_required:
        missing_payload = _missing_required_error(tool_name, body, required_params)
        _dup = _duplicate_retry_guard(
            f"{_task_session_id or transport}:{_task_agent_id or 'legacy'}",
            tool_name, body, "MISSING_REQUIRED_FIELD")
        if _dup:
            missing_payload["original_error_name"] = missing_payload.get("error_name")
            missing_payload.update(_dup)
        if tool_name in _MUTATING_TOOLS:
            missing_payload.setdefault("write_performed", False)
            missing_payload.setdefault("applied", False)
        return web.json_response(missing_payload, status=400, headers=cors)

    # Numeric coercion follows registry types, not handler annotations.
    for _pname, _spec in _registry_props.items():
        if _pname not in body or (_spec or {}).get("type") != "integer": continue
        _v = body[_pname]
        if isinstance(_v, bool):
            return _error_response(tool_name, f"{_pname} must be an integer", 400, cors, body, error_name=f"INVALID_{_pname.upper()}", accepted_fields=sorted(accepted_fields), accepted_aliases=_alias_metadata(tool_name), ca_next_action="retry_with_integer_value")
        if isinstance(_v, int): continue
        if isinstance(_v, str):
            try: body[_pname] = int(_v.strip())
            except ValueError:
                return _error_response(tool_name, f"{_pname} must be an integer", 400, cors, body, error_name=f"INVALID_{_pname.upper()}", accepted_fields=sorted(accepted_fields), accepted_aliases=_alias_metadata(tool_name), ca_next_action="retry_with_integer_value")

    # V15 R11 A.1: refresh_project_map removed — it only regenerates `.orchestrator_cache/INDEX.json`
    # which is a derived cache, not a source file. No backup needed.
    # V15 R79: generate_project_map ALSO removed from the backup gate. Its only outputs are
    # INDEX.json (pure cache), PROJECT_MAP.md and PROJECT_CONTEXT.md — all auto-generated,
    # fully regenerable artifacts, never hand-authored source. On a fresh round/output folder
    # these files do not exist yet, so the first generation is pure-create (non-destructive),
    # yet the old gate forced a full backup_project before the workflow could even build its
    # INDEX for the first write_fix. That was the real residual friction behind the
    # "new project still asks for backup" reports. Regenerating over existing maps is likewise
    # safe (derived docs). => not gated.
    # R91: MCP/HTTP clients sometimes serialize numeric workflow fields as
    # strings. Coerce at the dispatcher boundary so workflow_submit_step can
    # compute STEP_MISMATCH deltas instead of crashing with str-int TypeError.
    if tool_name == "workflow_submit_step":
        if "step" in body:
            try:
                body["step"] = int(body["step"])
            except Exception:
                return _error_response(
                    tool_name,
                    "step must be an integer",
                    400,
                    cors,
                    body,
                    error_name="INVALID_STEP",
accepted_fields=sorted(accepted_fields),
                    accepted_aliases=_alias_metadata(tool_name),
                    ca_next_action="call_workflow_get_current_to_resync",
                )
        if isinstance(body.get("step_range"), list):
            try:
                body["step_range"] = [int(body["step_range"][0]), int(body["step_range"][1])]
            except Exception:
                return _error_response(
                    tool_name,
                    "step_range must be [int, int]",
                    400,
                    cors,
                    body,
                    error_name="INVALID_STEP_RANGE",
accepted_fields=sorted(accepted_fields),
                    accepted_aliases=_alias_metadata(tool_name),
                    ca_next_action="call_workflow_get_current_to_resync",
                )

    _dispatcher_fields = {"backup_override": {"delete_file", "write_fix", "write_fix_html", "write_new_file", "apply_after_dry_run"}}
    _backup_override = bool(body.pop("backup_override", False)) if tool_name in _dispatcher_fields["backup_override"] else False
    _backup_gated_tools = set(_dispatcher_fields["backup_override"])
    # --- C3 DURABLE: HARD DENY for restart-control lifecycle critical files ---
    # These files are REQUIRED for safe restart operation. Deleting them causes
    # RESTART_CONTROL_SECRET_UNAVAILABLE permanently. Reject with a clear error.
    if tool_name == 'delete_file':
        _cf_path = body.get('file_path') or body.get('path') or ''
        try:
            if os.path.isabs(_cf_path):
                _cf_low = os.path.abspath(_cf_path).replace('\\', '/').lower()
                _control_file_patterns = (
                    '/.orchestrator_lifecycle/restart_control.secret',
                    '/.orchestrator_cache/restart_control.secret',  # legacy path
                    '/restart_control_audit.jsonl',
                    '/restart_busy_snapshot.json',
                )
                for _pat in _control_file_patterns:
                    if _cf_low.endswith(_pat):
                        return _error_response(
                            tool_name,
                            f"Deletion of restart-control critical file is denied: {_pat}",
                            403,
                            cors,
                            body,
                            error_name="CONTROL_FILE_DELETE_DENIED",
                            ca_next_action="do_not_delete_restart_control_files",
                        )
        except Exception:
            pass
    # R83: delete_file ephemeral cleanup mode — for one-off temp/derived files
    # (C:/temp, %TEMP%, *.bak_*, .orchestrator_cache) deleting should be ONE call with
    # no project_path and no backup_override. ephemeral=true + a temp-rooted absolute path
    # skips the backup + session gates below. Any other path ignores ephemeral (stays safe).
    _ephemeral_del = False
    if tool_name == 'delete_file' and body.get('ephemeral'):
        _ed_path = body.get('file_path') or body.get('path') or ''
        try:
            if os.path.isabs(_ed_path):
                _low = os.path.abspath(_ed_path).replace('\\', '/').lower()
                _roots = [os.environ.get('TEMP', ''), os.environ.get('TMP', ''),
                          'c:/temp', os.path.join(os.environ.get('SystemRoot', ''), 'Temp')]
                _roots = [r.replace('\\', '/').lower() for r in _roots if r]
                # C3: extend protection to canonical path too
                protected_restart_secret = (
                    _low.endswith('/.orchestrator_cache/restart_control.secret')
                    or _low.endswith('/.orchestrator_lifecycle/restart_control.secret')
                )
                if not protected_restart_secret and (
                    any(_low.startswith(r) for r in _roots)
                    or '.bak_' in _low or '/.orchestrator_cache/' in _low
                ):
                    _ephemeral_del = True
        except Exception:
            _ephemeral_del = False
    # R70: write_new_file creating a NON-existent file is non-destructive -> no backup needed.
    # Only gate write_new_file when it would OVERWRITE an existing file. Removes BACKUP_REQUIRED
    # friction when a workflow writes its first data file into a fresh round/output folder.
    _r70_skip_backup = False
    if tool_name == 'write_new_file':
        try:
            _r70_rel = body.get("path") or body.get("file_path") or ""
            _r70_pp = body.get("project_path") or ""
            _r70_tgt = _r70_rel if (_r70_rel and os.path.isabs(_r70_rel)) else os.path.join(_r70_pp, _r70_rel)
            _r70_exists = (await asyncio.to_thread(os.path.exists, _r70_tgt)
                           if _r70_rel else False)
            if _r70_rel and not _r70_exists:
                _r70_skip_backup = True
        except Exception:
            _r70_skip_backup = False
    _xroot_target = body.get("path") or body.get("file_path") or body.get("dst") or body.get("target_file") or ""
    _xroot_gate_allowed = False
    _xroot_gate_denied = None
    if _xroot_target:
        try:
            _xroot_path = Path(str(_xroot_target))
            if _xroot_path.is_absolute() and is_outside_project_root(_xroot_path, _HERE):
                _xroot_policy = xroot_check(_xroot_path, tool_name, body.get("session_id", ""))
                if _xroot_policy.get("allowed"):
                    _xroot_gate_allowed = True
                else:
                    _xroot_gate_denied = _xroot_policy
        except Exception:
            _xroot_gate_allowed = False
    if (tool_name in _backup_gated_tools and not body.get('dry_run', False)
            and not _r70_skip_backup and not _ephemeral_del
            and not _backup_override):
        if _xroot_gate_denied:
            return _error_response(
                tool_name,
                _xroot_gate_denied.get("reason", "xroot policy denied write"),
                403,
                cors,
                body,
                error_name="XROOT_DENIED",
                group=_xroot_gate_denied.get("group", ""),
                reason=_xroot_gate_denied.get("reason", ""),
            )
        if _xroot_gate_allowed:
            pass
        else:
            _project_for_backup = (
                body.get("project_path")
                or await asyncio.to_thread(ct.find_project_root, _body_path_hint(body))
                or ""
            )
            # F4: Cannot run destructive tools without a resolvable project root
            if not _project_for_backup:
                return _error_response(
                    tool_name,
                    "Unable to determine project root; please provide project_path",
                    400,
                    cors,
                    body,
                    hint="Set project_path to the project root and keep file_path/path relative to it.",
                    error_name="PROJECT_PATH_REQUIRED",
                )
            _backup_status = await asyncio.to_thread(
                ct._backup_manifest_status, _project_for_backup)
            _backup_missing = _backup_status in {"missing", "unknown"}
            if _backup_missing and AUTO_WRITE_MODE:
                # A-AUTO-UPGRADE: run the backup inline instead of bouncing the
                # CA (backup_project itself skips when today's backup exists).
                try:
                    _backup_targets = []
                    for _field in ("file_path", "path", "src", "dst"):
                        _value = body.get(_field)
                        if isinstance(_value, str) and _value.strip():
                            _backup_targets.append(_value)
                    _operations = body.get("operations")
                    if isinstance(_operations, list):
                        for _op in _operations:
                            if isinstance(_op, dict):
                                _value = _op.get("file_path") or _op.get("path")
                                if isinstance(_value, str) and _value.strip():
                                    _backup_targets.append(_value)
                    _ab_raw = await asyncio.to_thread(
                        lambda: asyncio.run(ct.backup_project(
                            _project_for_backup,
                            target_paths=list(_backup_targets) or None,
                        ))
                    )
                    _ab = json.loads(_ab_raw) if isinstance(_ab_raw, str) else (_ab_raw or {})
                except Exception as _ab_exc:
                    _ab = {"ok": False, "error": str(_ab_exc)[:200]}
                if _ab.get("ok"):
                    _backup_missing = False
                    # Anh 2026-08-11 (Phase A on-loop audit): durable write pins
                    # the event loop on every dispatch; run it OFF the loop.
                    await asyncio.to_thread(
                        _log_session_event_safe,
                        request, "auto_write_backup", "ok",
                        {"project": _project_for_backup,
                         "skipped": _ab.get("skipped", False)},
                        agent_id=_task_agent_id or None)
            if _backup_missing:
                return _error_response(
                    tool_name,
                    "Backup required before destructive changes",
                    409,
                    cors,
                    body,
                    action="backup_project",
                    backup_status=_backup_status,
                    error_name="BACKUP_REQUIRED",
                    # R81: give CA the two concrete unblock paths + the exact override field.
                    ca_next_action="backup_project_then_retry OR resend with backup_override=true",
                    remediation=[
                        {"option": "audited", "do": "call backup_project(project_path) once, then retry this call"},
                        {"option": "skip_backup", "do": "resend THIS call with backup_override=true (no project backup; use for tiny/derived/temp files)"},
                    ],
                    accepted_override_field="backup_override",
                    override_payload_example={**{k: body.get(k) for k in ("project_path", "file_path", "path") if k in body}, "backup_override": True},
                )

    # Capture the admitted session once. A concurrent CA may close/switch the active
    # session while the tool runs; durable audit must still target this exact session.
    _admitted_session_id = ""
    # Require explicit session for mutating tools (except bootstrap/session primitives).
    if tool_name in _MUTATING_TOOLS and tool_name not in _NO_SESSION_REQUIRED and not _is_readonly_rpc and not _ephemeral_del and not _xroot_gate_allowed:
        if _xroot_gate_denied:
            return _error_response(
                tool_name,
                _xroot_gate_denied.get("reason", "xroot policy denied write"),
                403,
                cors,
                body,
                error_name="XROOT_DENIED",
                group=_xroot_gate_denied.get("group", ""),
                reason=_xroot_gate_denied.get("reason", ""),
            )
        _project_for_session = (
            body.get("project_path")
            or await asyncio.to_thread(ct.find_project_root, _body_path_hint(body))
            # V15 R44 — T4 page-bound tools (fill_value/click/evaluate_js/...)
            # carry only page_id; resolve project_path via the registered page
            # state pinned at launch_electron_with_cdp / cdp_attach_target time.
            or _resolve_project_from_page_id(body.get("page_id", ""))
            # V15 R64-D — session_id lookup for close_session/rename_session and
            # any future tool that operates on a specific session by id. Without
            # this fallback, close_session(session_id=...) hit PROJECT_PATH_REQUIRED
            # at the gate before reaching the function's session_id branch.
            or await asyncio.to_thread(_resolve_project_from_session_id, body.get("session_id", ""))
            or ""
        )
        # F4: Cannot verify session without a resolvable project root
        if not _project_for_session and not _xroot_gate_allowed:
            # W7 (F020, 2026-08-19): a page-bound T4 tool carries ONLY page_id.
            # When that page_id is unknown/expired the resolution chain above
            # yields "" and this gate used to answer PROJECT_PATH_REQUIRED --
            # which sends the CA to add project_path, whose schema then rejects
            # it as UNSUPPORTED_FIELD (the F020 "deadlock"). The real fault is
            # the page_id, and _get_page() already has the right taxonomy; it
            # just never runs because this gate is upstream of the handler.
            # Answer with the page taxonomy here. The gate still BLOCKS -- only
            # the reason and the recovery path change.
            _w7_page_id = str(body.get("page_id", "") or "")
            if _w7_page_id:
                _w7_wellformed = bool(
                    re.fullmatch(r"(pg|cdp)-[A-Za-z0-9][A-Za-z0-9_-]*", _w7_page_id)
                )
                return _error_response(
                    tool_name,
                    (
                        f"page_id {_w7_page_id!r} is not a live page"
                        if _w7_wellformed else
                        f"invalid page_id: {_w7_page_id!r}; expected a live pg-... ID"
                    ),
                    400,
                    cors,
                    body,
                    hint=(
                        "Open a page first (open_browser_page / cdp_attach_target) and "
                        "use the page_id it returns. Adding project_path will NOT help: "
                        "this tool's schema does not accept it."
                    ),
                    error_name=("PAGE_NOT_FOUND" if _w7_wellformed else "PAGE_ID_INVALID"),
                    ca_next_action="open_browser_page_then_reuse_page_id",
                    retryable=False,
                )
            # W7 (F020, 2026-08-19): some tools legitimately resolve their project
            # from an ALTERNATIVE field -- close_session(session_id=...) is the
            # R64-D flow -- so project_path cannot be marked `required` for them
            # without breaking that flow. Their schema therefore declares no
            # required field at all, and a payload built from `required` ({}) used
            # to answer a flat PROJECT_PATH_REQUIRED that never mentioned the
            # alternative. Name the one-of set instead; this is a genuine missing
            # required field, just one with more than one way to satisfy it.
            _w7_props = ((_schema_for_tool(tool_name) or {}).get("parameters") or {}).get("properties") or {}
            _w7_alts = [f for f in ("project_path", "file_path", "path", "session_id", "page_id")
                        if f in _w7_props]
            if len(_w7_alts) > 1:
                return _error_response(
                    tool_name,
                    "Missing required field(s) for '%s': one of %s"
                    % (tool_name, ", ".join(_w7_alts)),
                    400,
                    cors,
                    body,
                    hint=(
                        "This tool resolves its project from any ONE of these fields. "
                        "Pass project_path for the project root, or the id field that "
                        "already names the target (session_id / page_id)."
                    ),
                    error_name="MISSING_REQUIRED_FIELD",
                    required_one_of=_w7_alts,
                    ca_next_action="rebuild_payload_with_one_of_required_one_of",
                )
            return _error_response(
                tool_name,
                "Unable to determine project root; please provide project_path",
                400,
                cors,
                body,
                hint="Set project_path to the project root and keep file_path/path relative to it.",
                error_name="PROJECT_PATH_REQUIRED",
            )
        # V15 R8 C.1: apply same path resolution as open_session body so subdir/variant
        # paths map to the registered project root. Falls back to raw key if resolution
        # yields a different key with no active session (handles legacy sessions opened
        # before R1).
        _project_key = ct._session_project_key(_project_for_session)
        _session_agent_id = _task_agent_id or None
        _session_id = await asyncio.to_thread(ct._get_active_session_id, _project_key, _session_agent_id)
        if not _session_id:
            try:
                _resolution = await asyncio.to_thread(ct._resolve_project_path_payload, _project_for_session, mode="folder")
                _resolved_root = _resolution.get("project_path") if isinstance(_resolution, dict) else None
            except Exception:
                _resolved_root = None
            if _resolved_root and _resolved_root != _project_for_session:
                _resolved_key = ct._session_project_key(_resolved_root)
                if _resolved_key != _project_key:
                    _session_id = await asyncio.to_thread(ct._get_active_session_id, _resolved_key, _session_agent_id)
                    if _session_id:
                        # Best-effort: rewrite body so downstream tool uses resolved path.
                        # CAN-048: rebase relative file_path/path to the resolved root first.
                        _rebase_relative_paths(body, body.get("project_path"), _resolved_root)
                        body["project_path"] = _resolved_root
                        _project_key = _resolved_key
        # PC1-SESSION (2026-08-11): rename_session is a session-lifecycle tool
        # whose contract ("Closed sessions cannot be renamed") requires an
        # EXISTING active session. Letting AUTO_WRITE auto-open a brand-new
        # "untitled-session-..." and then renaming it would silently satisfy
        # the call while violating the documented contract. Skip auto-open for
        # rename_session so the fallthrough below returns the fail-loud
        # ACTIVE_SESSION_REQUIRED with ca_next_action=open_session.
        _SKIP_AUTO_OPEN_FOR_TOOL = tool_name in ("rename_session",)
        if not _session_id and AUTO_WRITE_MODE and not _SKIP_AUTO_OPEN_FOR_TOOL:
            # A-AUTO-UPGRADE: open the session ourselves instead of bouncing the
            # CA with ACTIVE_SESSION_REQUIRED — _auto_open_session is the same
            # helper log_native_event already uses, and opening a session is
            # idempotent audit bookkeeping, not a mutation.
            try:
                _session_id = await asyncio.to_thread(
                    ct._auto_open_session, _project_for_session, body, agent_id=_session_agent_id)
            except Exception:
                _session_id = None
            if _session_id:
                # Anh 2026-08-11 (Phase A on-loop audit): durable write pins
                # the event loop on every dispatch; run it OFF the loop.
                await asyncio.to_thread(
                    _log_session_event_safe,
                    request, "auto_write_session", "ok",
                    {"project": _project_for_session, "tool": tool_name},
                    agent_id=_task_agent_id or None)
        if not _session_id:
            # V15 R56 — drift-aid: when project_path was resolved via page_id
            # (R44 chain), the page is alive but its project's session was
            # closed (often by midnight stale-session reaper). Surface the
            # EXACT recovery payload so CA doesn't have to guess which path
            # to pass to open_session.
            _r56_page_id = body.get("page_id", "") or ""
            _r56_page_pinned = ""
            _r56_page_alive = False
            try:
                if _r56_page_id:
                    import tools_v15 as _r56_tv
                    _r56_state = getattr(_r56_tv, "_BROWSER_PAGES", {}).get(_r56_page_id) or {}
                    _r56_page_alive = bool(_r56_state)
                    _r56_page_pinned = _r56_state.get("project_path", "") if _r56_state else ""
            except Exception:
                pass
            # List recent active/closed sessions for the project so CA sees
            # whether re-open is needed vs a different project altogether.
            _r56_recent_sessions: list = []
            try:
                _r56_list_raw = await asyncio.to_thread(
                    lambda: asyncio.run(ct.list_sessions(_project_for_session, 5)))
                _r56_list = json.loads(_r56_list_raw) if _r56_list_raw else {}
                _r56_recent_sessions = [
                    {"session_id": s.get("session_id"),
                     "status": s.get("status"),
                     "started_at": s.get("started_at")}
                    for s in (_r56_list.get("sessions") or [])[:5]
                ]
            except Exception:
                pass
            _r56_recovery = {
                "tool": "open_session",
                "args": {
                    "project_path": _project_for_session,
                    "session_title": f"resume_{tool_name}_{int(time.time())}",
                },
                "rationale": (
                    "Session for this project_path is not active. R56: "
                    "project_path above was resolved via "
                    f"{'page_id (R44 chain)' if _r56_page_pinned else 'body.project_path / file_path'}; "
                    f"call open_session with this exact path to resume "
                    "T4-mutating workflow."
                ),
            }
            return _error_response(
                tool_name,
                "Active session required before mutating tools",
                409,
                cors,
                body,
                action="open_session",
                error_name="ACTIVE_SESSION_REQUIRED",
                hint=(
                    "Project_path could be resolved but no active session exists. "
                    "Use `auto_open_session_payload` (R56) for one-shot recovery, "
                    "OR check `recent_sessions_sample` for prior closed sessions to "
                    "decide if you should resume vs start fresh."
                ),
                resolved_project_path=_project_for_session,
                resolved_project_key=_project_key,
                # R56 diagnostic — page-binding context
                page_id_in_body=_r56_page_id,
                page_alive=_r56_page_alive,
                page_pinned_project_path=_r56_page_pinned,
                page_to_session_drift=(
                    bool(_r56_page_pinned)
                    and _r56_page_pinned == _project_for_session
                ),
                # R56 recovery — exact payload to fix
                auto_open_session_payload=_r56_recovery,
                recent_sessions_sample=_r56_recent_sessions,
                ca_next_action=(
                    "call_open_session_with_auto_open_session_payload"
                    if _r56_recovery else "call_open_session_with_project_path"
                ),
            )
        _admitted_session_id = str(_session_id)

    logger.info(f"Tool call: {tool_name}({list(body.keys())})")
    _t0 = time.time()
    _status = "error"
    _result = {}
    _error_msg = ""
    _queue_wait_ms = 0
    _op_id = ""  # FixA2407 P1-1: write-ledger key; stays empty for non-mutating tools
    _request_op_id = str(request.headers.get("X-Operation-ID") or uuid.uuid4().hex)
    _watchdog_token = None
    _admission_tokens = set_admission_identity(_principal_id, control_plane=False)
    _sem = None
    _sem_category = ""
    _current_task = asyncio.current_task()
    if _current_task is not None:
        _INFLIGHT_OPERATIONS[_request_op_id] = _current_task

    try:
        # CAN-052 admission control: bound heavy-tool concurrency (subprocess /
        # browser / bulk read; writes serialize per project) so one burst cannot
        # starve the event loop or disk. Queue wait is measured apart from
        # execution: duration_ms stays pure execution time.
        if _tool_semaphore(tool_name, body) is not None:
            _q0 = time.time()
            _sem, _sem_category = await _acquire_tool_semaphore(
                tool_name, body, _request_op_id)
            _queue_wait_ms = int((time.time() - _q0) * 1000)
            _t0 = time.time()
        # FixA2407 P1-1: record a mutating call BEFORE it runs. The server always knows
        # whether the write landed; the caller that timed out does not.
        _op_id = await _write_ledger_begin_async(tool_name, body, _task_session_id)
        if _op_id:
            from orchestrator.progress_watchdog import ProgressWatchdog, bind_watchdog
            _cancel_event = threading.Event()
            _OPERATION_CANCEL_EVENTS[_op_id] = _cancel_event
            _watchdog_token = bind_watchdog(ProgressWatchdog(
                operation_id=_op_id,
                heartbeat=lambda p: _write_ledger_heartbeat(_op_id, p),
                cancel_event=_cancel_event,
            ))
        try:
            raw = await fn(**body)
        finally:
            _release_tool_semaphore(_sem, _sem_category, _request_op_id)
        try:
            _result = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as _json_exc:
            logger.warning(f"Tool '{tool_name}' returned non-JSON response: {_json_exc}")
            _result = {
                "ok": False,
                "error": "Tool response is not valid JSON",
                "raw": str(raw)[:500],
                "error_name": "TOOL_RESPONSE_NOT_JSON",
                "reason": "Tool response is not valid JSON",
                "hint": "The server tool returned non-JSON; inspect raw and fix the tool implementation.",
                "ca_next_action": "inspect_tool_response",
            }
            _result["ca_guidance"] = _ca_guidance(tool_name, _result["error"], body, _result["error_name"])
            _status = "error"
            _result = _r101_filter(_result, _R101_FIELDS.get())
            return web.json_response(_result, status=422, headers=cors)
        if isinstance(_result, dict) and _queue_wait_ms:
            _result.setdefault("queue_wait_ms", _queue_wait_ms)
        _status = "error" if _tool_result_failed(_result) else "ok"
        # Task 3 auto-chain rule 6: git_commit post-hook — validate committed files
        if tool_name == "git_commit" and _status == "ok" and isinstance(_result, dict):
            try:
                _committed = _result.get("committed_files") or _result.get("committed_paths") or []
                if _committed:
                    _validations = []
                    for _cf in _committed[:10]:
                        _cf_path = _cf if isinstance(_cf, str) else str(_cf)
                        try:
                            import code_tools as _ct_post
                            _val_raw = await _ct_post.validate_file(
                                _cf_path, project_path=str(body.get("project_path") or ""))
                            _val = json.loads(_val_raw) if isinstance(_val_raw, str) else _val_raw
                            _validations.append({
                                "file": _cf_path,
                                "ok": _val.get("ok", False),
                                "error": _val.get("error", ""),
                            })
                        except Exception:
                            _validations.append({"file": _cf_path, "ok": False, "error": "validation_unavailable"})
                    if _validations:
                        _result["post_commit_validation"] = {
                            "validated": len(_validations),
                            "all_ok": all(v["ok"] for v in _validations),
                            "details": _validations,
                        }
            except Exception:
                pass  # non-fatal, don't block commit result
        # Task 4 auto-chain: git_status → git_commit hint when staged files exist
        if tool_name == "git_status" and _status == "ok" and isinstance(_result, dict):
            try:
                _staged = _result.get("staged") or {}
                _staged_count = (
                    _staged.get("count", 0) if isinstance(_staged, dict)
                    else len(_staged)
                ) or int((_result.get("counts") or {}).get("staged") or 0)
                if _staged_count and _staged_count > 0:
                    _result["ca_hint"] = f"{_staged_count} staged file(s) ready — git_commit to commit"
                    _result.setdefault("ca_next_action", "git_commit")
            except Exception:
                pass
        # A31 Q5 auto-chain: git_diff -> the next step in the commit chain, DERIVED from the
        # payload's own `scope` rather than guessed: a worktree diff gets staged, a staged diff
        # gets committed (and git_commit needs the expected_staged_hash that git_status mints).
        # Deliberately the same shape, and the same place, as the git_status hint above so the
        # two cannot drift apart. Reuses the registered `ca_hint` field -- see AUTO_CHAIN_RULES
        # in code_tools.py; no new vocabulary is introduced here.
        if tool_name == "git_diff" and _status == "ok" and isinstance(_result, dict):
            try:
                if str(_result.get("diff") or "").strip():
                    if _result.get("scope") == "staged":
                        _result["ca_hint"] = "staged diff is non-empty - git_status for expected_staged_hash, then git_commit"
                        _result.setdefault("ca_next_action", "git_status then git_commit")
                    else:
                        _result["ca_hint"] = "worktree diff is non-empty - git_stage with explicit paths (never allow_all)"
                        _result.setdefault("ca_next_action", "git_stage")
            except Exception:
                pass
        # A34 W2 P2c: the PASSIVE auto-chain. Measured 2026-09-18: of 258 registered tools,
        # 117 emit a hint from their own handler source and 141 emit none -- and by real
        # ledger traffic the biggest of those gaps is read_file_range, 3478 of 13406 calls.
        # 141 hand-written hints is the rot A31 forbids, so the rows live in ONE table
        # (orchestrator/core/passive_chain.py) applied here, once, for every tool. It reuses
        # the already-registered `ca_hint` plus the free-form `ca_next_action`, so the
        # `ca_next_tool` resolver below derives from whatever the table set and there is no
        # second mechanism to keep in step. It runs BEFORE that resolver for exactly that
        # reason, fires only on an ok payload, and never overwrites a hint a tool wrote itself.
        try:
            from orchestrator.core.passive_chain import apply_passive_chain
            apply_passive_chain(tool_name, _result, _R101_FIELDS.get())
        except Exception:
            pass  # advisory only -- the passive table must never fail a tool that worked
        # A34 W2 P2b: derive `ca_next_tool` -- the CALLABLE half of `ca_next_action`.
        # ca_next_action keeps its documented contract (free-form advisory prose; see the
        # comment above AUTO_CHAIN_RULES in code_tools.py). Measured 2026-09-18 over the
        # production tree: of its 259 distinct literals, 25 name a real tool inside advice
        # ("use_write_fix_or_choose_new_path") and 28 more are the SAME imperative shape
        # while naming no tool at all ("use_partial_then_narrow_regex_scope"), so ONE field
        # cannot tell a CA what is callable. Two can: ca_next_tool present => that tool
        # exists in the live registry, call it; absent => ca_next_action is advice to read.
        # One insertion here covers every tool, ok or error, because `_result` is the parsed
        # payload for all of them, and the resolver re-derives against TOOL_MAP so a tool
        # added or removed never needs an edit here. The decision (including the R101
        # projection rule) lives in the leaf so it is testable without an HTTP request.
        try:
            from orchestrator.core.next_tool import annotate_next_tool
            annotate_next_tool(_result, getattr(ct, "TOOL_MAP", None),
                               tool_name, _R101_FIELDS.get())
        except Exception:
            pass  # advisory only -- a hint that will not resolve must never fail a tool
        # A34 W2 P2c: the ACTIVE auto-chain -- the only half that RUNS anything, and it runs
        # ONLY what this call explicitly asked for via pipeline=[...]. That is what keeps it
        # inside the rule that an auto-chain is "a CA-level recommendation, NOT automatic
        # execution" (examples/CORE_RULE.txt): every step is a pure read whose payload is
        # built from THIS call's own answer, and a read the caller requested is not automatic.
        # Placed AFTER the passive table and the ca_next_tool resolver so the source payload
        # is complete before any step reads it, and BEFORE the ledger for the same reason
        # ca_next_tool is -- the ledger must record what the CA was actually told. The tool
        # semaphore was released in the finally around the await above, so invoking targets
        # here cannot self-deadlock, and the leaf's own cap bounds the fan-out.
        if _active_pipeline and isinstance(_result, dict):
            try:
                from orchestrator.core.active_chain import (
                    PIPELINE_RESPONSE_KEY,
                    execute_active_pipeline,
                    pipeline_key_requested,
                )

                # R101: the payload reaching this funnel is already projected, so a key added
                # now would bypass the caller's token budget. Asked BEFORE anything runs
                # rather than after -- a report the caller projected away must cost zero
                # reads. The rule lives in the leaf so it is testable without an HTTP request.
                if pipeline_key_requested(_R101_FIELDS.get()):

                    async def _invoke_pipeline_step(_target, _payload):
                        """The resolver the leaf is given, so the leaf imports no server module.

                        Same idiom the dispatcher uses on the source tool itself: resolve the
                        name in the live TOOL_MAP, await it, parse the JSON string it returns.
                        A name that is not registered raises, and the leaf records that as
                        this step's own failure instead of losing the steps that did work.
                        """
                        _step_fn = getattr(ct, "TOOL_MAP", {}).get(_target)
                        if _step_fn is None:
                            raise KeyError(_target)
                        return json.loads(await _step_fn(**_payload))

                    _pipeline_report = await execute_active_pipeline(
                        tool_name, _active_pipeline, _result, body, _invoke_pipeline_step)
                    if _pipeline_report is not None:
                        _result[PIPELINE_RESPONSE_KEY] = _pipeline_report
            except Exception:
                pass  # opt-in extra reads must never fail the call that already worked
        await _write_ledger_finish_async(_op_id, _result, _status)  # BK34: SQLite off-loop
        if _status == "error":
            err_text = str(_result.get("error") or _result.get("reason")
                           or ("timed out" if _result.get("timed_out") else "Tool returned ok=false / non-zero exit"))
            err_name = _result.get("error_name") or _infer_error_name(tool_name, err_text, body)
            # CAN-050: block the 2nd identical deterministic tool-body failure.
            if err_name in _DETERMINISTIC_TOOLBODY_ERRORS:
                _dup = _duplicate_retry_guard(
                    f"{_task_session_id or transport}:{_task_agent_id or 'legacy'}",
                    tool_name, body, err_name)
                if _dup:
                    _result["original_error_name"] = err_name
                    _result.update(_dup)
                    err_name = "DUPLICATE_RETRY_BLOCKED"
            _result.setdefault("ok", False)
            _result.setdefault("error_name", err_name)
            _result.setdefault("reason", err_text)
            _result.setdefault("hint", _default_hint(tool_name, err_name))
            _result.setdefault("ca_next_action", _ca_next_action(err_name, tool_name))
            # CASE-1919 item 1: the REGISTRY-driven example, not the legacy
            # _PAYLOAD_EXAMPLES table -- that dict has no search_code entry, so its
            # {"project_path": ...} fallback answered a missing `query` with an
            # example that omits `query`, i.e. it taught a CA nothing it did not
            # already have. _payload_example_for_tool honours the curated overrides
            # first and derives everything else from the live schema.
            try:
                _tb_example = _payload_example_for_tool(tool_name, _schema_for_tool(tool_name))
            except Exception:
                _tb_example = {}
            _result.setdefault("payload_example", _tb_example or _PAYLOAD_EXAMPLES.get(
                tool_name, {"project_path": "D:/path/to/project"}))
            if err_name == "MISSING_REQUIRED_FIELD":
                # A handler-raised MISSING_REQUIRED_FIELD bypasses the registry gate
                # (_missing_required_error), so it carried no missing_fields at all.
                # Name the field(s) in the message itself -- prose a CA has never been
                # taught is indistinguishable from prose about some other field.
                try:
                    _mrf = _missing_required_detail(tool_name, err_text, body)
                except Exception:
                    _mrf = {}
                _mrf_detail = _mrf.pop("error_detail", "")
                _mrf_example = _mrf.pop("payload_example", None) or {}
                for _mrf_key, _mrf_val in _mrf.items():
                    _result.setdefault(_mrf_key, _mrf_val)
                if _mrf_example:
                    # Union, example-wins-per-key: the tool's own example keeps every
                    # value it chose and gains the names it left out.
                    _mrf_prev = _result.get("payload_example")
                    _result["payload_example"] = (
                        {**_mrf_example, **_mrf_prev} if isinstance(_mrf_prev, dict) and _mrf_prev
                        else _mrf_example)
                if _mrf_detail:
                    for _msg_key in ("error", "reason"):
                        _msg_old = str(_result.get(_msg_key) or "")
                        if _mrf_detail not in _msg_old:
                            _result[_msg_key] = (
                                f"{_msg_old} | {_mrf_detail}" if _msg_old else _mrf_detail)
            _result.setdefault("retry_sequence", _retry_sequence(
                tool_name, err_name, body,
                corrected_payload=_result.get("corrected_payload")))
            _result.setdefault("ca_guidance", _ca_guidance(tool_name, err_text, body, err_name))
            if tool_name == "read_file_range" and err_name == "INVALID_RANGE":
                # Error-specific range example wins over the generic function example.
                _range_example = _invalid_range_payload_example(body)
                _result["payload_example"] = _range_example
                _result["ca_guidance"]["payload_example"] = dict(_range_example)
                _result["ca_guidance"]["ca_next_action"] = "correct_range"
            # V15 R49 — phase taxonomy for tool-body errors (path bypasses
            # _error_response which already adds phase). Idempotent: if the
            # tool's own _err/_json_error already set phase, setdefault keeps it.
            try:
                _phase_body = ct._phase_for_error_name(err_name) if hasattr(ct, "_phase_for_error_name") else "runtime"
            except Exception:
                _phase_body = "runtime"
            _result.setdefault("phase", _phase_body)
            # V15 R40: top-level suggested_alternative_tool (so CA sees it without parsing ca_guidance)
            _alt = _r40_suggest_alternative(tool_name, err_name)
            if _alt:
                _result.setdefault("suggested_alternative_tool", _alt)
        # V15 R34: surface boundary-level path normalization audit
        if _r34_normalized_paths and isinstance(_result, dict):
            _result["path_normalized"] = _r34_normalized_paths
        # CASE-1919 item 3 -- a RELATIVE project_path resolves against the DAEMON's
        # own working directory, never the caller's. Measured 2026-09-19:
        # project_path="." answered ok:true with count:1 and disclosed nothing, so a
        # cwd-confused CA reads a successful-looking answer that came from a
        # different project. Behaviour is deliberately unchanged -- the multi-base
        # resolver and its tried_paths stay in charge -- the SILENCE is the defect.
        # Force-kept via _R101_SAFETY_KEEP so a fields= projection cannot hide it.
        if _rel_project_path and isinstance(_result, dict):
            _result["project_path_resolution"] = _rel_project_path
        # CAP R3: surface deprecated-field compatibility metadata on success
        if _compat_meta and isinstance(_result, dict):
            _result["deprecated_fields"] = _compat_meta.get("deprecated_fields", [])
            _result["canonical_replacements"] = _compat_meta.get("canonical_replacements", {})
            _result["compatibility_applied"] = _compat_meta.get("compatibility_applied", False)
            _result["removal_version"] = _compat_meta.get("removal_version", _SEARCH_COMPAT_REMOVAL_VERSION)
        # Task 2 (2026-09-10): surface deprecation hint for wrapper tools.
        if tool_name in _DEPRECATED_TOOLS and isinstance(_result, dict):
            _result["deprecated"] = True
            _result["deprecation_hint"] = _DEPRECATED_TOOLS[tool_name]
        # A15 (2026-08-16) FAIL-LOUD (7-RULE #7): when normalization silently
        # decided an internal `mode` from a boolean flag (regex/is_regex) the
        # caller must be told — otherwise a `regex: false` that got downgraded to
        # text (or vice-versa) is invisible. Surface only the mode-affecting
        # normalizations to keep the payload small.
        #
        # A17 (2026-08-16): also surface DROPPED fields. A caller-supplied field
        # that loses to an existing canonical key (e.g. regex_search receiving
        # both `pattern` and `regex`) was only written to the server log, so the
        # caller never learned its input was discarded. Silently ignoring input
        # is the same 7-RULE #7 violation as silently rewriting it.
        if _aliases_applied and isinstance(_result, dict):
            _mode_norm = [
                a for a in _aliases_applied
                if 'mode' in a or 'dropped' in a
            ]
            if _mode_norm:
                _result.setdefault("normalization_applied", _mode_norm)
        # with ensure_ascii=False so CA sees raw UTF-8 text.
        try:
            _result = _r101_filter(_result, _R101_FIELDS.get())
            # A29: an unprojected fat response is a silent context tax. Name
            # its heaviest keys so the NEXT call can pass fields=. Runs after
            # the filter so a caller who already projected never sees a hint.
            _result = _r101_size_hint(_result, _R101_FIELDS.get())
            # A37 #0 "Lan 1": the FIRST call of a read tool since this CA's bootstrap gets
            # that tool's schema WITH the result; later calls get nothing; an ERROR re-arms
            # it, because an error is the evidence the CA does not really hold the schema.
            #
            # Measured problem: one session fetched ~18 schemas, read_file_range alone 6
            # times. The schema was always available and always had to be asked for -- pure
            # S and t with no C gained. Bootstrap opens the epoch
            # (code_tools.open_bootstrap_epoch); this is where that epoch is spent.
            #
            # WHY here, after _r101_filter and not next to the _DEPRECATED_TOOLS block:
            # the filter would have pruned pushed_schema for any caller who passed fields=,
            # so the epoch would be SPENT on a call that never delivered the schema. A CA
            # cannot project for a key it does not yet know exists, which is the same reason
            # _R101_SAFETY_KEEP exists.
            #
            # Write tools never appear here: they are excluded from the purpose map on
            # purpose and keep fetching their schema every call, because a wrong write costs
            # Correctness, which outranks S and t.
            if isinstance(_result, dict) and tool_name:
                try:
                    _bctx = ct.bootstrap_ctx_id(_task_agent_id or "")
                    if _status == "error":
                        # Re-arm only -- do NOT push into an error payload. The CA is already
                        # being handed retry_sequence + payload_example + corrected_payload;
                        # the schema lands on the corrected retry, where it is actionable.
                        if ct.bootstrap_forget(_bctx, tool_name):
                            _result["schema_rearmed"] = True
                    elif tool_name in ct.bootstrap_push_tools() and ct.bootstrap_first_use(_bctx, tool_name):
                        _pushed_schema = _enriched_tool_schema(tool_name)
                        if _pushed_schema:
                            _result["pushed_schema"] = _pushed_schema
                            _result["pushed_schema_note"] = (
                                "First call of this tool since your bootstrap, so its schema "
                                "arrives WITH the result -- do not spend a call fetching it. "
                                "A later call of the same tool does not repeat this push; an "
                                "error on it re-arms the push (schema_rearmed)."
                            )
                except Exception:
                    pass  # a disclosure must never fail the call that already worked
            # CSLt 2026-07-16: refresh the task metadata AFTER the tool ran and stamp
            # with setdefault so the tool's own response fields win. The old pre-run
            # `_result.update(_task_meta)` overwrote begin_task's fresh task_epoch with
            # the stale pre-call snapshot, so HTTP callers saw epoch N-1 (verified).
            if _task_session_id and isinstance(_result, dict):
                try:
                    refreshed_task_meta = await asyncio.to_thread(
                        _task_metadata, _task_session_id, _task_agent_id
                    )
                except ct.TaskCheckpointStorageError as exc:
                    return _task_checkpoint_storage_response(
                        exc,
                        cors,
                        write_performed=bool(_op_id),
                    )
                for _meta_key, _meta_val in refreshed_task_meta.items():
                    _result.setdefault(_meta_key, _meta_val)
            if isinstance(_result, dict):
                _result.setdefault("generation", SERVER_GENERATION)
                # FixA2407 P1-1: hand the caller the ledger key, so a client-side timeout
                # is resolved with GET /api/write_status?operation_id=... instead of an
                # ambiguous retry or a read-back.
                if _op_id:
                    _result.setdefault("operation_id", _op_id)
                    _result.setdefault("write_status_url",
                                       f"/api/write_status?operation_id={_op_id}")
                    # P12 (campaign-1608): the operation_id is DETERMINISTIC, so an
                    # identical retry maps onto the same ledger entry and returns the
                    # same id. Surface the attempt count so a stable id is not
                    # misread as a stale cached object: operation_replayed=true means
                    # "you have sent this exact payload before", which lets a CA tell
                    # a genuine re-run apart from an unchanged id after it reseeded.
                    _led = _WRITE_LEDGER.get(_op_id)
                    if isinstance(_led, dict):
                        _attempts = int(_led.get("attempts") or 1)
                        _result.setdefault("operation_attempts", _attempts)
                        _result.setdefault("operation_replayed", _attempts > 1)
                        # TA-4 fix (2026-08-18): a REPLAY that now reports
                        # SYNTAX_ERROR_PRE_WRITE / FILE_CHANGED_OUTSIDE_ORCHESTRATOR
                        # is a known false-alarm shape: the FIRST attempt already
                        # shipped the write (server completed, client timed out),
                        # so the deterministic retry recomputes the draft/AST
                        # against the ALREADY-PATCHED file — the old line range no
                        # longer matches and the tool cries "unmatched ')'" on code
                        # that is in fact correct. Tell the caller so it verifies by
                        # read instead of blindly re-patching (which would double-write).
                        if (_attempts > 1 and _result.get("ok") is False
                                and _result.get("error_name") in {
                                    "SYNTAX_ERROR_PRE_WRITE",
                                    "FILE_CHANGED_OUTSIDE_ORCHESTRATOR"}):
                            _result.setdefault("likely_already_applied", True)
                            _result.setdefault(
                                "replay_hint",
                                "This exact payload was sent before; the first "
                                "attempt likely already wrote successfully (client "
                                "timeout, server completed). VERIFY with "
                                "read_file_range before retrying — do NOT blindly "
                                "re-patch, that risks a double-write.")
                            _result.setdefault("ca_next_action", "read_file_range")
            # A37 #4: the R101 projection above ran BEFORE this block stamped
            # generation / operation_id / write_status_url / operation_* and the
            # refreshed task metadata, so fields_unknown could name a key that IS
            # in the very response being returned. Prune by asking the payload,
            # never a hand-maintained list of envelope keys.
            try:
                from orchestrator.core.fields_param import prune_unknown_fields
                prune_unknown_fields(_result)
            except Exception:
                pass  # advisory disclosure only: never fail a good response over it
            _json_body = json.dumps(_result, ensure_ascii=False)
            _json_body = _shape_tool_response_text(tool_name, _json_body)
            return web.Response(text=_json_body, content_type="application/json",
                                  charset="utf-8", headers=cors)
        except Exception:
            return web.json_response(_result, headers=cors,
                                      dumps=lambda x: json.dumps(x, ensure_ascii=False))
    except (ToolAdmissionOverloaded, FairAdmissionError, ExecutorLaneError) as exc:
        _error_msg = str(exc)
        _status = "error"
        await _write_ledger_finish_async(_op_id, {}, "error")
        _result = exc.as_payload()
        _result.setdefault("generation", SERVER_GENERATION)
        if _op_id:
            _result.setdefault("operation_id", _op_id)
            _result.setdefault("write_status_url", f"/api/write_status?operation_id={_op_id}")
        status_code = getattr(exc, "http_status", 503)
        if isinstance(exc, (ToolAdmissionOverloaded, ExecutorLaneError)):
            status_code = 429 if _result.get("error_name") in {"TOOL_QUEUE_FULL", "TOOL_OVERLOADED"} else 503
        response_headers = dict(cors)
        if _result.get("retryable"):
            response_headers["Retry-After"] = str(_result.get("retry_after_seconds") or 1)
        logger.warning("Tool '%s' admission rejected: %s", tool_name, exc)
        return web.json_response(_r101_filter(_result, _R101_FIELDS.get()), status=status_code, headers=response_headers)
    except TypeError as exc:
        _error_msg = str(exc)
        # Surface canonical schema hint for the agent
        canonical = _alias_metadata(tool_name)
        hint = f" (accepted aliases: {canonical})" if canonical else ""
        required_fields = _required_field_names(tool_name)
        if "required positional argument" in _error_msg or "missing" in _error_msg.lower():
            _mre = _missing_required_error(tool_name, body, required_fields)
            _dup = _duplicate_retry_guard(
                f"{_task_session_id or transport}:{_task_agent_id or 'legacy'}",
                tool_name, body, "MISSING_REQUIRED_FIELD")
            if _dup:
                _mre["original_error_name"] = _mre.get("error_name")
                _mre.update(_dup)
            return web.json_response(
                _mre,
                status=400,
                headers=cors,
            )
        return _error_response(
            tool_name,
            f"Wrong arguments for '{tool_name}': {exc}{hint}",
            400,
            cors,
            body,
            error_name="WRONG_ARGUMENTS",
            required_fields=_tool_required_fields(tool_name),
            accepted_aliases=canonical,
        )
    except Exception as exc:
        _error_msg = str(exc)
        await _write_ledger_finish_async(_op_id, {}, "error")  # BK34: crash => not_committed off-loop
        # A40: tools_v15 durable-write tools (record_error_resolution,
        # record_session_audit_checkpoint) let a transient
        # SessionStoreContentionError propagate to this generic handler instead
        # of catching it, so without this it would collapse into a
        # non-retryable INTERNAL_ERROR/TOOL_ERROR -- the same "don't retry a
        # retryable stall" bug A40 fixes in code_tools' _json_error. Reuse the
        # ONE shared classifier (ct._session_contention_fields) rather than
        # duplicating the mapping, so a fourth durable caller can't drift.
        _contention = _session_contention_error_payload(tool_name, exc, body)
        if _contention is not None:
            logger.warning(
                "Tool '%s' hit session-store contention: %s", tool_name, exc)
            _result = _r101_filter(_contention, _R101_FIELDS.get())
            return web.json_response(_result, status=503, headers=cors)
        _result = {
            "ok": False,
            "error": str(exc),
            "error_name": _infer_error_name(tool_name, str(exc), body),
            "reason": str(exc),
        }
        _result["hint"] = _default_hint(tool_name, _result["error_name"])
        _result["ca_next_action"] = _ca_next_action(_result["error_name"], tool_name)
        _result["retry_sequence"] = _retry_sequence(tool_name, _result["error_name"], body)
        _result["ca_guidance"] = _ca_guidance(tool_name, str(exc), body, _result["error_name"])
        logger.error(f"Tool '{tool_name}' error: {exc}")
        _result = _r101_filter(_result, _R101_FIELDS.get())
        return web.json_response(_result, status=500, headers=cors)
    finally:
        # Every begun write must leave in_flight, including non-JSON, TypeError,
        # and CancelledError paths that return/raise before the normal finish call.
        try:
            _pending_ledger = _WRITE_LEDGER.get(_op_id) if _op_id else None
            if _pending_ledger and _pending_ledger.get("status") == "in_flight":
                await _write_ledger_finish_async(
                    _op_id,
                    _result if isinstance(_result, dict) else {},
                    _status,
                )
        except Exception as exc:
            logger.error("write ledger finalization failed for %s: %s", _op_id, exc)
        _release_tool_semaphore(_sem, _sem_category, _request_op_id)
        reset_admission_identity(_admission_tokens)
        if _INFLIGHT_OPERATIONS.get(_request_op_id) is _current_task:
            _INFLIGHT_OPERATIONS.pop(_request_op_id, None)
        if _op_id:
            _OPERATION_CANCEL_EVENTS.pop(_op_id, None)
        if _watchdog_token is not None:
            try:
                from orchestrator.progress_watchdog import reset_watchdog
                reset_watchdog(_watchdog_token)
            except Exception:
                pass
        _ms = int((time.time() - _t0) * 1000)
        # Capture error detail from result body when tool returns ok=False
        # (Fix 16: _log_usage was getting empty error for non-exception fails)
        if _status == "error" and not _error_msg and isinstance(_result, dict):
            _error_msg = (
                str(_result.get("error") or
                    _result.get("stderr") or
                    _result.get("warning") or
                    _result.get("returncode") or "")
            )[:300]
        _accounting_task_meta = {
            "host_session_id": _task_session_id or None,
            "task_epoch": (
                _task_meta.get("task_epoch") if _task_meta else _task_epoch_raw
            ),
            "prompt_hash": (
                _task_meta.get("prompt_hash") if _task_meta else _task_prompt_hash
            ),
            "agent_id": _task_agent_id or None,
        }
        try:
            await asyncio.to_thread(
                _stage_durable_session_event,
                _request_op_id,
                tool_name,
                dict(body),
                _result if isinstance(_result, dict) else {},
                _ms,
                _status,
                _error_msg,
                _accounting_task_meta,
                _admitted_session_id,
            )
        except Exception as exc:
            # The tool has already completed. Preserve its response but fail loud
            # through logs and durable health metrics; never silently drop the row.
            logger.critical(
                "Durable session-event stage failed for tool=%s id=%s: %s",
                tool_name,
                _request_op_id,
                exc,
            )
        try:
            _accounting_accepted = _POST_CALL_ACCOUNTING.enqueue({
                "accounting_id": _request_op_id,
                "tool_name": tool_name,
                "body": _compact_accounting_body(body),
                "result": _compact_accounting_result(_result, _accounting_task_meta),
                "duration_ms": _ms,
                "status": _status,
                "error_msg": _error_msg,
            })
            if not _accounting_accepted:
                logger.critical(
                    "Post-call accounting admission failed for tool=%s id=%s",
                    tool_name,
                    _request_op_id,
                )
        except Exception as exc:
            logger.critical(
                "Post-call accounting enqueue crashed for tool=%s id=%s: %s",
                tool_name,
                _request_op_id,
                exc,
            )


# ─── Session REST + SSE endpoints ────────────────────────────────────────────

class _InProcessToolRequest:
    """Minimal request shape required by _dispatch_tool_call_request for MCP."""

    def __init__(self, tool_name: str, body: dict, headers: dict | None = None):
        self.match_info = {"tool_name": tool_name}
        self.headers = headers or {}
        self.remote = "localhost"
        self._raw_body = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")

    async def read(self) -> bytes:
        return self._raw_body


# CAN-051 scopes: the writers that "stay non-source-mutating and token-free" -- they
# touch [A]'s own index / cache / log, never the caller's files. A37: this set used to be
# spelled out here as a second copy of the vocabulary that code_tools._MUTATION_SCOPE_MAP
# defines, which is the drift shape this campaign is closing -- so it now POINTS at the
# one SOT (code_tools.NON_SOURCE_MUTATION_SCOPES) instead of restating it.
_R102_NON_SOURCE_SCOPES = ct.NON_SOURCE_MUTATION_SCOPES

_R102_CATEGORY_HINT = (
    "Optional category filter: _list, read, write, derived, search, cbm, ocr, git, "
    "desktop, workflow, assert, runtime, session, audit, mcp, t1, t2, t3, t4. "
    "read/write come from the registry mutation_scope, never from the tool name: read "
    "means it cannot change YOUR files, and derived marks the subset that still writes "
    "[A]'s own index or log (cbm_*, query_index, list_structure). categories_available "
    "in the response is the authoritative live list."
)


def _r102_tool_categories(name: str, tier: str = "") -> set[str]:
    low = (name or "").lower()
    cats = set()
    if tier:
        cats.add(tier.lower())
    if low.startswith("git_"):
        cats.add("git")
    if low.startswith(("read_", "list_", "get_")):
        cats.add("read")
    if low.startswith(("write_", "delete_", "apply_")) or low in {"write_transaction", "write_fix_html", "write_new_file"}:
        cats.add("write")
    if low.startswith(("search_", "find_", "regex_", "semantic_", "type_", "query_")):
        cats.add("search")
    if low.startswith(("desktop_", "cdp_", "electron_")) or "window" in low or "selector" in low or "dialog" in low:
        cats.add("desktop")
    if low.startswith("workflow_"):
        cats.add("workflow")
    if low.startswith("assert_"):
        cats.add("assert")
    if low.startswith(("run_", "launch_", "restart_", "server_", "port_", "process_", "kill_", "wait_")):
        cats.add("runtime")
    if "session" in low:
        cats.add("session")
    if "audit" in low or low.startswith(("log_", "sync_", "manage_agent")):
        cats.add("audit")
    if low.startswith(("mcp_", "tool_")) or low in {"call_tool", "get_rule_core_text"}:
        cats.add("mcp")
    # A37 #7 ROOT FIX: this classifier was prefix-only, so the cbm_ and ocr_ families
    # answered no functional category at all and tool_names(category="read") hid every
    # one of them -- measured as the reason a CA never discovers the graph/OCR surface
    # it is told to prefer. read-vs-write is not a naming convention: the registry
    # already knows each tool's mutation_scope, so ask it instead of guessing.
    if low.startswith("cbm_"):
        cats.add("cbm")
    if low.startswith("ocr_"):
        cats.add("ocr")
    try:
        _scope = ct.tool_mutation_scope(name)
    except Exception:
        _scope = ""
    # MEASURED 2026-09-19 on the live daemon, and the reason this is not `== "none"`:
    # cbm_get_architecture answers mutation_scope="derived_index", so a none-only test
    # filed the whole cbm family as write and category="read" STILL hid it -- reopening
    # the very bug five lines above. CAN-051 (code_tools._MUTATION_SCOPE_MAP) declares
    # cache / audit_log / derived_index for writers that "stay non-source-mutating and
    # token-free": they touch [A]'s own index or log, never the caller's source, which
    # is why list_structure (cache) and query_index (derived_index) both sit under
    # "Core read" in CURATED_CORE_TOOLS. So read = "cannot change your files", the
    # question a CA is actually asking -- and the derived write is still disclosed.
    if _scope in _R102_NON_SOURCE_SCOPES:
        cats.add("read")
        if _scope != "none":
            cats.add("derived")
    elif _scope:
        cats.add("write")
    return cats


def _r102_category_names() -> list[str]:
    cats = {"_list"}
    for _name in ct.TOOL_MAP.keys():
        cats.update(_r102_tool_categories(_name, ct.TOOL_PHASES.get(_name, "")))
    return sorted(cats)


def _bound_tool_result(payload: dict, max_bytes: int = 4_000) -> dict:
    """Truncate large tool results and add continuation ref for follow-up reads."""
    import json as _j
    encoded = _j.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) <= max_bytes:
        return payload  # already within budget
    # Truncate string fields proportionally
    overflow = len(encoded) - max_bytes
    bounded = {}
    for k, v in payload.items():
        if isinstance(v, str) and len(v) > 200:
            keep = max(100, len(v) - overflow)
            bounded[k] = v[:keep] + "...[truncated]"
        else:
            bounded[k] = v
    bounded["truncated"] = True
    bounded["original_bytes"] = len(encoded)
    bounded["continuation"] = "Use read_file_range with offset to read remaining content"
    # Verify we're within budget (+ 10% margin)
    final = _j.dumps(bounded, ensure_ascii=False).encode("utf-8")
    if len(final) > max_bytes * 1.1:
        # Aggressive: keep only first field truncated hard
        for k in list(bounded.keys()):
            if isinstance(bounded[k], str) and len(bounded[k]) > 100:
                bounded[k] = bounded[k][:100] + "..."
                break
    return bounded


def _tool_names_payload(tier: str = "", name_contains: str = "", category: str = "",
                        limit: int = 0, suppress_hints: bool = False) -> dict:
    """Compact discovery payload shared by HTTP names endpoint and MCP."""
    tier_filter = (tier or "").strip().upper()
    name_filter = (name_contains or "").strip().lower()
    category_filter = (category or "").strip().lower()
    if category_filter == "_list":
        return {"ok": True, "categories": _r102_category_names(), "hint": _R102_CATEGORY_HINT}
    tools = []
    for name in sorted(ct.TOOL_MAP.keys()):
        tool_tier = ct.TOOL_PHASES.get(name, "")
        if tier_filter and (tool_tier or "").upper() != tier_filter:
            continue
        if name_filter and name_filter not in name.lower():
            continue
        categories = _r102_tool_categories(name, tool_tier)
        if category_filter and category_filter not in categories:
            continue
        tools.append({
            "name": name,
            "tier": tool_tier,
            "category": sorted(categories),
            "protected": name in ct.PROTECTED_TOOL_NAMES,
            "mutating": _is_mutating_tool(name),
            "mutation_scope": ct.tool_mutation_scope(name),
            "requires_session": _requires_session(name),
        })
    total = len(tools)
    try:
        _limit_int = int(limit or 0)
    except (TypeError, ValueError):
        _limit_int = 0
    explicit_filter = bool(tier_filter or name_filter or category_filter or _limit_int > 0)
    curated_default = False
    if explicit_filter:
        cap = max(1, _limit_int or total)
        returned = tools[:cap]
    else:
        # CSLt 2026-07-16: the unfiltered default returns the curated core set
        # (registry_data.CURATED_CORE_TOOLS — same SOT the MCP packs build from)
        # instead of the first N alphabetical names, so CA discovery sees the
        # intended surface. Pass limit=250 (or any filter) for the full catalog.
        try:
            from orchestrator.core.registry_data import (
                CURATED_CORE_TOOLS as _curated_sot,
            )
        except Exception:
            _curated_sot = ()
        _curated_set = set(_curated_sot)
        curated = [t for t in tools if t["name"] in _curated_set]
        curated_default = bool(curated)
        returned = curated if curated else tools[:max(1, _TOOL_CATALOG_DEFAULT_LIMIT)]
    payload = {
        "ok": True,
        "count": len(returned),
        "returned_count": len(returned),
        "total_available": total,
        "bounded": len(returned) < total,
        "curated": curated_default,
        "filtered": explicit_filter,
        "category": category_filter or "",
        "tools": returned,
        "next_query_hint": (
            "Unfiltered /api/tools/names returns curated core only. "
            f"Filter by name_contains/tier/category, or pass limit={total} for the full catalog. "
            "ruff_fix field is path (file_path is an alias)."
        ) if len(returned) < total else "",
        "ca_next_action": "filter_or_use_total_available_as_limit" if len(returned) < total else "",
        "ca_hint": (
            "MUST-KNOW: 1) After a NEW [A] PID/generation, first get_rule_core_text MUST include text "
            "(force=true if already_fetched and text is missing). "
            "2) FILE_CHANGED / STALE_INDEX on a large root such as [A:P] => "
            "read_file_range + project_path + force_reseed, then retry write_fix once; "
            "NEVER refresh_project_map. "
            f"3) /api/tools/names default is curated; use limit={total} for the full catalog. "
            "4) ruff_fix canonical field is path, not file_path. "
            "5) list_structure uses depth/path (max_depth/file_path are aliases). "
            "6) GET /health default is compact; use ?fields= or ?verbose=1 for more."
        ),
        "must_know_hints": [
            "first get_rule_core_text after new process must include text",
            "FILE_CHANGED on [A:P]: read_file_range + force_reseed; never refresh_project_map",
            f"names default is curated; pass limit={total} for full catalog",
            "ruff_fix field is path (file_path alias accepted)",
            "list_structure uses depth not max_depth; file_path aliases to path",
            "GET /health default compact; ?fields= subset, ?verbose=1 full lanes",
        ],
        "hint": _R102_CATEGORY_HINT,
        "categories_available": _r102_category_names(),
    }
    if suppress_hints:
        # A37 #7: the flag used to clear only ca_hint + must_know_hints and left
        # next_query_hint / ca_next_action / hint populated, so a caller that asked
        # for silence still paid for three advisory strings -- the name promised the
        # whole advisory class, so it now clears the whole advisory class and says
        # which keys it emptied (silence must still be auditable).
        for _hint_key in ("ca_hint", "next_query_hint", "ca_next_action", "hint"):
            payload[_hint_key] = ""
        payload["must_know_hints"] = []
        payload["hints_suppressed"] = True
        payload["hints_suppressed_keys"] = [
            "ca_hint", "next_query_hint", "ca_next_action", "hint",
            "must_know_hints",
        ]
    return payload


def _unknown_tool_payload(name: str) -> dict:
    """Bounded discovery shared by GET schema, MCP schema and POST dispatch."""
    from difflib import SequenceMatcher
    from urllib.parse import urlencode

    low = (name or "").strip().lower()[:200]
    if low == "call_tool":
        return {"ok": False, "error_name": "UNKNOWN_TOOL",
                "error": "call_tool is the MCP transport dispatcher, not an HTTP catalog tool",
                "surface": "mcp_dispatcher", "suggestions": [],
                "ca_next_action": "use_mcp_call_tool_or_http_catalog_tool",
                "payload_example": {"name": "read_file_range", "payload": {"file_path": "D:/path/file"}},
                "http_equivalent": "POST /api/call/read_file_range",
                "hint": "Discover names with tool_names and schemas with tool_schema(name=<catalog tool>). Do not invent named MCP tools."}
    # Same class of confusion as `call_tool` above, one layer in: a CA reads an advisory
    # RESPONSE FIELD name out of a payload and calls it. `ca_next_tool` is the measured case
    # (2026-09-18, by a CA on this very campaign) -- its VALUE is a live tool name, so the
    # field name reads exactly like one, and the sibling `ca_policy_metrics` IS a real tool,
    # so the `ca_` prefix carries no signal either way. Similarity ranking answers it with an
    # EMPTY suggestions list, the least useful answer possible. Derived from
    # ct.AUTO_CHAIN_FIELDS so a newly registered chain field is covered with no edit here;
    # ca_next_action is added by hand because the registry deliberately excludes it (free-form
    # prose, not a chain field) while being the field a CA reads FIRST on an error, hence the
    # likeliest of all to be mis-called.
    advisory_fields = set(ct.AUTO_CHAIN_FIELDS) | {"ca_next_action"}
    if low in advisory_fields and low not in ct.TOOL_MAP:
        return {"ok": False, "error_name": "UNKNOWN_TOOL",
                "error": f"{low} is a RESPONSE FIELD on [A] payloads, not a tool",
                "surface": "response_field", "suggestions": [],
                "field_semantics": ("ca_next_tool carries a live tool NAME as its value: call THAT "
                                    "value, never the field name. Every other advisory field "
                                    "carries prose to READ, not a name to call."),
                "ca_next_action": "read_the_field_value_then_call_the_tool_it_names",
                "payload_example": {"name": "<the value of ca_next_tool>",
                                    "payload": {"project_path": "D:/path/to/project"}},
                "advisory_fields": sorted(advisory_fields),
                "hint": ("Advisory fields are EMITTED BY tools, never called. "
                         "Discover real names with tool_names; suggestions are not aliases.")}
    words = set(re.findall(r"[a-z0-9]+", low))
    ranked = []
    for candidate in ct.TOOL_MAP:
        target = candidate.lower()
        parts = set(target.split("_"))
        substring = bool(low and (low in target or target in low))
        overlap = len(words & parts) / max(1, len(words | parts))
        similarity = SequenceMatcher(None, low, target).ratio() if low else 0.0
        if substring or overlap >= 0.5 or (len(low) >= 5 and similarity >= 0.68):
            ranked.append((0 if substring else 1, -overlap, -similarity, candidate))
    suggestions = [item[-1] for item in sorted(ranked)[:8]]
    token = max(sorted(words), key=len, default="")
    discovery_url = "/api/tools/names" + ("?" + urlencode({"name_contains": token}) if token else "")
    return {"ok": False, "error": f"Unknown tool {name!r}",
            "error_name": "UNKNOWN_TOOL", "suggestions": suggestions,
            "hint": "Use GET /api/tools/names or MCP tool_names for discovery; suggestions are not aliases.",
            "ca_next_action": "filter_tool_names", "discovery_url": discovery_url}


def _tool_schema_payload(name: str) -> tuple[dict, int]:
    """Single-tool enriched schema payload shared by HTTP and MCP."""
    name = (name or "").strip()
    if name not in ct.TOOL_MAP:
        return _unknown_tool_payload(name), 404
    for schema in ct.TOOL_SCHEMAS:
        fn = schema.get("function", {})
        if fn.get("name") != name:
            continue
        out_schema = copy.deepcopy(schema)
        out_fn = out_schema.setdefault("function", {})
        out_fn["payload_example"] = _payload_example_for_tool(name, out_fn)
        out_fn["protected"] = name in ct.PROTECTED_TOOL_NAMES
        out_fn["mutating"] = _is_mutating_tool(name)
        out_fn["requires_token"] = name in _TOKEN_REQUIRED_TOOLS
        try:
            out_fn["mutation_scope"] = ct.tool_mutation_scope(name)
        except Exception:
            out_fn["mutation_scope"] = "none"
        out_fn["requires_session"] = _requires_session(name)
        return {"ok": True, "tool": out_schema}, 200
    return {"ok": False, "error": f"No schema for '{name}'", "error_name": "SCHEMA_MISSING"}, 500


def _mcp_proxy_transport_verdict(exc: BaseException) -> tuple[str, bool, str]:
    """Classify a proxy transport failure as (commit_state, safe_to_retry, why).

    The two shapes are not the same answer and must not collapse into one 502. A
    REFUSED connection means the request never reached the daemon, so nothing ran and
    an identical retry is free. A TIMEOUT means the daemon accepted the request and
    may still be running it, so the same retry can double-write. Everything else is
    genuinely undetermined and is reported as such rather than guessed.
    """
    reason = getattr(exc, "reason", None)
    if isinstance(exc, ConnectionRefusedError) or isinstance(reason, ConnectionRefusedError):
        return ("not_committed", True,
                "the connection was refused, so the request never reached the daemon")
    # socket.timeout is TimeoutError on 3.10+; urllib wraps a connect timeout in URLError.
    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
        return ("unknown", False,
                "the proxy timed out; the daemon may still be running this call")
    return ("unknown", False,
            "the proxy failed after the request may already have been sent")


def _mcp_proxy_failure_payload(tool_name: str, body: dict, exc: BaseException) -> dict:
    """The MCP_PROXY_FAILED envelope, carrying enough identity to resolve the write.

    Reported from the field on 2026-08-20: this branch returned exactly three keys
    (`ok`, `error_name`, `error`) at status 502. For a MUTATING tool that is the worst
    available answer -- the daemon may have completed the write and only the response
    was lost -- and the caller got no operation identity, no `new_hash`, and not even
    the `_mcp_proxy` metadata the sibling HTTPError branch attaches. A CA in that
    state can only guess, and guessing "retry" is what double-writes a file.

    The operation_id is deliberately NOT recomputed here. It is
    sha256(tool + body + session) over the body the DISPATCHER sees, and the
    dispatcher pops or rewrites several fields (task_epoch, prompt_hash, fields,
    project_path resolution, agent_id) before it opens the ledger. An id computed on
    this side of the wire would therefore often be a plausible-looking key for an
    entry that does not exist -- worse than no id at all, because a 404 from
    write_status would read as "no write happened". The ledger is also indexed by
    (project_path, file_path) and `handle_write_status` already supports that lookup,
    so the target query is the one that actually answers the question.
    """
    payload = {
        "ok": False,
        "error_name": "MCP_PROXY_FAILED",
        "error": f"MCP HTTP proxy failed for {tool_name}",
        "reason": f"{type(exc).__name__}: {exc}",
        "_mcp_proxy": {"tool": tool_name, "transport": "http", "failed": True},
    }
    if tool_name not in _MUTATING_TOOLS:
        # A read-only tool opens no ledger entry, so a write_status_url here would
        # only earn a 404 and imply a write that never existed. Retrying is free.
        payload["commit_state"] = "not_applicable"
        payload["retryable"] = True
        payload["safe_to_retry"] = True
        payload["ca_next_action"] = "retry_same_call"
        return payload
    commit_state, safe_to_retry, why = _mcp_proxy_transport_verdict(exc)
    payload["commit_state"] = commit_state
    payload["commit_state_reason"] = why
    payload["retryable"] = safe_to_retry
    payload["safe_to_retry"] = safe_to_retry
    if commit_state == "not_committed":
        payload["write_performed"] = False
    _pp = str((body or {}).get("project_path") or "")
    _fp = str((body or {}).get("file_path") or (body or {}).get("path")
              or (body or {}).get("dst") or "")
    if _pp or _fp:
        import urllib.parse as _up
        _query = [(k, v) for k, v in (("project_path", _pp), ("file_path", _fp)) if v]
        payload["write_status_url"] = "/api/write_status?" + _up.urlencode(_query)
        payload["write_status_lookup"] = "target"
    if safe_to_retry:
        payload["ca_next_action"] = "retry_same_call"
        payload["hint"] = ("The daemon was not reachable, so this call did not run. "
                           "Retry the identical payload.")
        payload["fallback_sequence"] = ["retry_same_call"]
        return payload
    payload["ca_next_action"] = (
        "poll_write_status_url_then_read_file_range"
        if payload.get("write_status_url")
        else "read_file_range_to_confirm_before_retry")
    payload["hint"] = (
        ("GET " + payload["write_status_url"] + " to learn whether the write "
         "committed; commit_state=committed means DO NOT retry. ")
        if payload.get("write_status_url") else
        "The outcome is undetermined. ") + (
        "Confirm with read_file_range before retrying -- a blind retry can "
        "double-write.")
    payload["fallback_sequence"] = (
        ["write_status", "read_file_range"] if payload.get("write_status_url")
        else ["read_file_range"])
    return payload


_MCP_T4_TIER_TTL = float(os.environ.get("MCP_T4_TIER_TTL_SECONDS", "300") or 300)
_MCP_T4_TIER_TIMEOUT = float(
    os.environ.get("MCP_T4_TIER_TIMEOUT_SECONDS", "5") or 5)
_MCP_T4_TIER_CACHE: dict = {}
_MCP_T4_TIER_DISAGREED: set = set()


def _mcp_local_t4_phase(tool_name: str) -> bool:
    """T4 according to the registry THIS process imported at its own launch."""
    phases = getattr(ct, "TOOL_PHASES", {}) or {}
    phase = phases.get(str(tool_name)) if isinstance(phases, dict) else None
    if isinstance(phase, (list, tuple, set, frozenset)):
        return any(str(item).strip().upper() == "T4" for item in phase)
    return any(token.strip().upper() == "T4" for token in
               str(phase or "").replace(",", "|").replace("/", "|").split("|"))


def _mcp_daemon_t4_names():
    """The T4 set as the DAEMON sees it, or None when it must not be asked.

    B010 (see the comment inside dispatch_tool_call) settled that only the daemon's
    registry decides what exists -- yet the gate deciding whether to ASK the daemon
    was itself answering from this process's imported map, the very map B010
    documents as possibly older than the daemon's. Measured: a bridge process up
    for 90 minutes reports mcp_generation_matches_daemon=false, so its TOOL_PHASES
    is whatever was on disk at ITS launch. A T4 tool added or retired since then is
    then routed by a stale answer, silently, in both directions: a stateful browser
    tool gets proxied (its page session ends up in the wrong process), or a retired
    one keeps running in-process, outside the daemon's lanes and accounting.

    Cost is one GET per TTL per process. ?tier=T4 reuses a filter /api/tools/names
    already had, so the daemon answers from its registry without scanning anything,
    and none of it ever reaches a CA's context.
    """
    if "--mcp" not in sys.argv:
        # Inside the daemon this would be a blocking call into our own event loop:
        # a self-deadlock, not a measurement. And it would answer nothing new --
        # here the local registry IS the daemon's registry, so returning None is
        # the right answer rather than a fallback.
        return None
    now = time.monotonic()
    cached = _MCP_T4_TIER_CACHE.get("names")
    if cached and (now - float(_MCP_T4_TIER_CACHE.get("at") or 0.0)) < _MCP_T4_TIER_TTL:
        return cached
    base = os.environ.get(
        "ORCHESTRATOR_BASE_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
    try:
        import urllib.request as _tur
        with _tur.urlopen(f"{base}/api/tools/names?tier=T4&fields=ok,tools,count",
                          timeout=_MCP_T4_TIER_TIMEOUT) as _tresp:
            _tparsed = json.loads(_tresp.read().decode("utf-8", "replace"))
        fetched = {str(entry.get("name"))
                   for entry in (_tparsed.get("tools") or [])
                   if isinstance(entry, dict) and entry.get("name")}
    except Exception:
        fetched = set()
    if not fetched:
        # An empty answer is NOT an answer. Caching it would reclassify EVERY
        # stateful T4 tool as proxy-able and move live browser sessions into
        # another process; "unreachable" and "zero results" are the same trap.
        return None
    _MCP_T4_TIER_CACHE["names"] = fetched
    _MCP_T4_TIER_CACHE["at"] = now
    return fetched


def _mcp_is_stateful_t4_tool(tool_name: str) -> bool:
    """Keep stateful T4/browser tools in MCP; route every other tool via HTTP."""
    name = str(tool_name)
    local = _mcp_local_t4_phase(name)
    daemon = _mcp_daemon_t4_names()
    if daemon is None:
        return local
    verdict = name in daemon
    if verdict != local and name not in _MCP_T4_TIER_DISAGREED:
        # Fail loud, once per tool: a routing decision that silently disagrees
        # with the daemon is how a page session goes missing with no error.
        _MCP_T4_TIER_DISAGREED.add(name)
        logger.warning(
            "MCP T4 routing: the daemon says %s for %s, this bridge's imported "
            "registry says %s; following the daemon (B010). Reconnect the MCP "
            "client to resync this bridge.",
            "T4" if verdict else "not T4", name, "T4" if local else "not T4")
    return verdict


async def dispatch_tool_call(tool_name: str, body: dict | None = None,
                             transport: str = "mcp",
                             request_meta: dict | None = None) -> web.StreamResponse:
    """Invoke any registered tool through the shared dispatcher without HTTP."""
    if body is None:
        body = {}
    if not isinstance(body, dict):
        body = {"payload": body}
    request_meta = request_meta or {}
    # Auto-identity (council): when this dispatch runs inside a worker's own [A] MCP
    # process, COUNCIL_AGENT_ID is the dispatching worker (injected at its launch).
    # Force it as the council_message sender so the worker cannot claim to be someone
    # else or omit its name — "send" auto-captures the caller. Runs before the
    # local-vs-proxy split so the stamped body also rides a proxied call.
    if tool_name == "council_message":
        # env (when the CLI forwarded it, e.g. opencode) → else trace THIS MCP
        # process's ancestry to its worker (codex/claude/pi don't forward env).
        _cid = os.environ.get("COUNCIL_AGENT_ID") or _council_identify_self()
        if _cid:
            body = dict(body)
            body["sender"] = _cid
            for _alias in ("from", "frm", "from_agent"):
                body.pop(_alias, None)
    headers = dict(request_meta.get("headers") or {})
    if (transport == "mcp"
            and tool_name not in {"tool_names", "tool_schema"}
            and not _mcp_is_stateful_t4_tool(tool_name)):
        # B010: there is deliberately NO local schema screen here. This code
        # runs inside the MCP stdio bridge, a long-lived process that imported
        # its registry at launch, so `_schema_for_tool` here can be older than
        # the daemon's -- and after TA-5 taught the bridge to re-baseline its
        # generation stamp instead of failing closed, it proxies happily to a
        # daemon whose schema it no longer shares. Screening locally therefore
        # answered UNSUPPORTED_FIELD for fields (and UNKNOWN_TOOL for whole
        # tools) the daemon accepts. The screen used to justify itself by
        # sharing the duplicate-retry ledger, but _bridge_dedup below already
        # applies _duplicate_retry_guard to every _DETERMINISTIC_TOOLBODY_ERRORS
        # verdict, this one included. Only the daemon's registry decides.

        # Keep the whole path on the live HTTP daemon. Both a
        # 2xx error envelope and urllib HTTPError use the same bridge helper,
        # so the duplicate guard cannot be bypassed by HTTP status handling.
        _proxy_base = os.environ.get(
            "ORCHESTRATOR_BASE_URL", f"http://127.0.0.1:{PORT}"
        ).rstrip("/")
        _proxy_session = str(
            headers.get("X-Session-Id")
            or headers.get("X-Task-Session-ID")
            or body.get("session_id")
            or "legacy"
        )
        _proxy_agent = str(
            headers.get("X-Agent-Id")
            or headers.get("X-Council-Agent")
            or body.get("agent_id")
            or os.environ.get("COUNCIL_AGENT_ID")
            or "legacy"
        )
        _proxy_identity = f"{_proxy_session}:{_proxy_agent}"

        def _bridge_dedup(_payload: dict, _status: int) -> tuple[dict, int]:
            if str(_payload.get("error_name") or "") not in _DETERMINISTIC_TOOLBODY_ERRORS:
                return _payload, _status
            _proxy_error_name = str(_payload["error_name"])
            _proxy_dup = _duplicate_retry_guard(
                _proxy_identity, tool_name, body, _proxy_error_name
            )
            if not _proxy_dup:
                return _payload, _status
            _payload["original_error_name"] = _proxy_error_name
            _payload.update(_proxy_dup)
            return _payload, 409

        try:
            import urllib.error as _ue
            import urllib.request as _ur
            _pdata = json.dumps(body if isinstance(body, dict) else {}).encode("utf-8")
            _proxy_headers = {
                "Content-Type": "application/json",
                "Origin": _proxy_base,
            }
            if headers.get("X-Orchestrator-Token"):
                _proxy_headers["X-Orchestrator-Token"] = headers["X-Orchestrator-Token"]
            _preq = _ur.Request(
                f"{_proxy_base}/api/call/{tool_name}", data=_pdata,
                headers=_proxy_headers)
            # This is the proxy path EVERY call_tool takes, and until now it was the
            # one with no watchdog: a flat 180s wall for most tools and -- worse --
            # timeout=None for four index tools, which converts a wedged daemon into a
            # wedged CA forever. mcp_stdio deleted that same footgun on its own side;
            # the 4-name set here was a hand-mirror of _MCP_PROGRESS_TOOLS, i.e. the
            # drift class itself. So borrow both halves from the one module that owns
            # them instead of re-deriving either: the budget (never None, suy from the
            # tool's own schema) and the wait (idle watchdog for writes, /health pid
            # liveness for the scan tools the write ledger cannot see).
            try:
                from orchestrator.server import mcp_stdio as _mcpw
            except Exception:
                _mcpw = None
            if _mcpw is None:
                # Bridge module unavailable: still bounded, never None.
                _timeout = float(os.environ.get("MCP_PROXY_HTTP_LONG_TIMEOUT", "3600"))
                _ptext = await asyncio.to_thread(
                    lambda: _ur.urlopen(_preq, timeout=_timeout).read().decode(
                        "utf-8", "replace"))
            else:
                _timeout = _mcpw._mcp_timeout_budget(
                    tool_name, (body or {}).get("timeout"), sys.modules[__name__])
                _ptext = await asyncio.to_thread(
                    _mcpw._mcp_proxy_wait, _preq, _timeout, tool_name,
                    (body or {}), _proxy_base)
            _proxy_payload = json.loads(_ptext)
            if not isinstance(_proxy_payload, dict):
                _proxy_payload = {
                    "ok": False,
                    "error_name": "MCP_PROXY_INVALID_RESPONSE",
                    "error": f"MCP HTTP proxy returned a non-object response for {tool_name}",
                }
            _proxy_payload, _proxy_status = _bridge_dedup(_proxy_payload, 200)
            return web.json_response(_proxy_payload, status=_proxy_status)
        except _ue.HTTPError as exc:
            _raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
            try:
                _error_payload = json.loads(_raw) if _raw else {}
            except Exception:
                _error_payload = {}
            if not isinstance(_error_payload, dict):
                _error_payload = {}
            _error_payload.setdefault("ok", False)
            _error_payload.setdefault("error_name", "MCP_PROXY_HTTP_ERROR")
            _error_payload.setdefault(
                "error", f"MCP HTTP proxy returned HTTP {exc.code} for {tool_name}"
            )
            _proxy_meta = _error_payload.setdefault("_mcp_proxy", {})
            if isinstance(_proxy_meta, dict):
                _proxy_meta.update({"tool": tool_name, "http_status": exc.code})
            _error_payload, _proxy_status = _bridge_dedup(_error_payload, exc.code)
            return web.json_response(_error_payload, status=_proxy_status)
        except Exception as exc:
            # A watchdog verdict from _mcp_proxy_wait carries its OWN envelope, so the
            # two proxy paths cannot drift into two error contracts for one
            # measurement -- which is exactly how this branch ended up with an
            # unbounded urlopen while the other path grew an idle watchdog. Duck-typed
            # on purpose: no class import, so the check cannot NameError when the lazy
            # import above failed and the fallback socket timeout raised instead.
            _abandon = getattr(exc, "mcp_proxy_envelope", None)
            if callable(_abandon):
                try:
                    _abandon_payload = _abandon(tool_name, _timeout)
                except Exception:
                    _abandon_payload = None
                if isinstance(_abandon_payload, dict):
                    logger.error(
                        "MCP HTTP proxy abandoned tool %s via %s: %s",
                        tool_name, _proxy_base, _abandon_payload.get("error_name"),
                    )
                    _abandon_payload, _abandon_status = _bridge_dedup(
                        _abandon_payload, 200)
                    return web.json_response(_abandon_payload, status=_abandon_status)
            logger.error(
                "MCP HTTP proxy failed for tool %s via %s: %s",
                tool_name, _proxy_base, exc,
            )
            # A bare 3-key 502 left CA unable to tell "never ran" from "ran, response
            # lost" -- see _mcp_proxy_failure_payload for why that mattered and why
            # the operation_id is resolved by target instead of recomputed.
            return web.json_response(
                _mcp_proxy_failure_payload(tool_name, body, exc), status=502)
    if body.get("project_path"):
        headers.setdefault("X-Project-Path", str(body.get("project_path")))
    request = _InProcessToolRequest(tool_name, body, headers=headers)
    return await _dispatch_tool_call_request(request, transport=transport)


async def handle_call(request):
    """Universal tool invocation: POST /api/call/{tool_name}"""
    return await _dispatch_tool_call_request(request, transport="api")


async def handle_list_sessions(request):
    """GET /api/sessions?project_path=...&limit=10"""
    cors = _cors(request.headers.get('Origin'))
    project_path = request.query.get('project_path', '')
    if not project_path:
        return web.json_response({"error": "project_path required"}, status=400, headers=cors)
    limit = _query_int(request, 'limit', 10)
    raw = await ct.list_sessions(project_path, limit)
    return web.json_response(json.loads(raw), headers=cors)


async def handle_clear_closed_sessions(request):
    """DELETE /api/sessions/closed?project_path=..."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"error": "blocked"}, status=403, headers=cors)
    if request.headers.get("X-Orchestrator-Token") != EXPECTED_TOKEN:
        return web.json_response({"ok": False, "error": "Mutation token required"}, status=403, headers=cors)
    project_path = request.query.get('project_path', '')
    if not project_path:
        try:
            body = await request.json()
            project_path = body.get("project_path", "") if isinstance(body, dict) else ""
        except Exception:
            project_path = ""
    if not project_path:
        return web.json_response({"ok": False, "error": "project_path required"}, status=400, headers=cors)
    raw = await ct.clear_closed_sessions(project_path)
    data = json.loads(raw)
    return web.json_response(data, status=200 if data.get("ok") else 400, headers=cors)


async def handle_session_events(request):
    """GET /api/sessions/{session_id}/events?limit=50&offset=0"""
    cors = _cors(request.headers.get('Origin'))
    session_id = request.match_info['session_id']
    limit  = _query_int(request, 'limit', 50)
    offset = _query_int(request, 'offset', 0)
    raw = await ct.get_session_events(session_id, limit, offset)
    return web.json_response(json.loads(raw), headers=cors)


async def handle_session_export(request):
    """GET /api/sessions/{session_id}/export  â†’  full JSON download"""
    cors = _cors(request.headers.get('Origin'))
    session_id = request.match_info['session_id']
    raw = await ct.get_session_events(session_id, limit=10000, offset=0)
    data = json.loads(raw)
    title = data.get("session_title", session_id).replace(" ", "_")[:40]
    filename = f"session_{session_id}_{title}.json"
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, indent=2),
        content_type='application/json',
        headers={**cors, 'Content-Disposition': f'attachment; filename="{filename}"'}
    )


async def handle_session_stream(request):
    """GET /api/session/stream/{session_id}?last_id=0&token=...  â†’  SSE realtime stream"""
    cors = _cors(request.headers.get('Origin'))
    # CAN-003: authenticate BEFORE preparing/streaming any `data:` payload. SSE was
    # previously open to any local subscriber, leaking session events.
    if not _is_local(request) or not _origin_ok(request) or not _sse_token_ok(request):
        return web.json_response(
            {"error": "SSE subscription requires a valid token",
             "error_name": "MUTATION_TOKEN_REQUIRED"},
            status=403, headers=cors)
    session_id = request.match_info['session_id']
    response = web.StreamResponse(headers={
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-store, max-age=0',
        'X-Accel-Buffering': 'no',
        **_cors(request.headers.get('Origin')),
    })
    await response.prepare(request)
    last_id = _query_int(request, 'last_id', 0)
    try:
        while request.transport is not None and not request.transport.is_closing():
            events = ct._get_session_events_since(session_id, last_id)
            for ev in events:
                data = json.dumps(ev, ensure_ascii=False)
                await response.write(f"data: {data}\n\n".encode())
                last_id = ev['id']
            if not events:
                await response.write(b": heartbeat\n\n")
            await asyncio.sleep(1.5)
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    return response


# ─── UI log (Fix 10: unify CA usage log to server-side JSONL) ────────────────

UI_LOG_FILE = _HERE / ".orchestrator_cache" / "ui_log.jsonl"
UI_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
_UI_LOG_MAX = 2000


async def handle_token(request):
    """GET /api/token — return mutation token to browser UI from allowlisted localhost only.
    Origin: null (file://) is explicitly blocked to prevent local-HTML token theft."""
    origin = request.headers.get('Origin')
    cors = _cors(origin)
    if not _is_local(request):
        return web.json_response({"error": "blocked"}, status=403, headers=cors)
    # Require allowlisted origin when Origin header present (blocks file:// pages)
    if origin is not None and origin not in _ALLOWED_ORIGINS:
        return web.json_response(
            {"error": f"Origin '{origin}' not allowed for token endpoint"},
            status=403, headers=cors
        )
    # CAN-001: rotate an expired lifecycle token so callers always receive a
    # usable (unexpired) token without restarting [A].
    if _token_is_expired():
        _rotate_token()
    return web.json_response({
        "token": EXPECTED_TOKEN,
        "sensitive": True,
        "ttl_minutes": TOKEN_TTL_MINUTES,
        "created_at_epoch": TOKEN_CREATED_AT,
        "expires_at_epoch": TOKEN_CREATED_AT + (TOKEN_TTL_MINUTES * 60),
        "refresh_url": "/api/token/refresh",
        "do_not_log_fields": ["token", "X-Orchestrator-Token"],
        "use_header": "X-Orchestrator-Token",
        "ca_next_action": "send_token_in_x_orchestrator_token_header_and_never_log_it",
        "mini_rule": "Never delete a file that was not explicitly named and approved. Fail loud: report the error, never swallow it or work around it silently.",
    }, headers=cors)


async def handle_token_refresh(request):
    """GET /api/token/refresh - renew token metadata without reopening a session.

    The current server lifecycle token is stable; refresh gives CA an audited,
    explicit renewal endpoint and TTL metadata for long T4/Desktop rounds.
    """
    origin = request.headers.get('Origin')
    cors = _cors(origin)
    if not _is_local(request):
        return web.json_response({"error": "blocked"}, status=403, headers=cors)
    if origin is not None and origin not in _ALLOWED_ORIGINS:
        return web.json_response(
            {"error": f"Origin '{origin}' not allowed for token endpoint"},
            status=403,
            headers=cors,
        )
    # CAN-001: an expired token is rotated here too, so an explicit refresh always
    # hands back a fresh, unexpired token.
    if _token_is_expired():
        _rotate_token()
    now = time.time()
    return web.json_response({
        "token": EXPECTED_TOKEN,
        "sensitive": True,
        "ttl_minutes": TOKEN_TTL_MINUTES,
        "created_at_epoch": TOKEN_CREATED_AT,
        "refreshed_at_epoch": now,
        "expires_at_epoch": TOKEN_CREATED_AT + (TOKEN_TTL_MINUTES * 60),
        "do_not_log_fields": ["token", "X-Orchestrator-Token"],
        "use_header": "X-Orchestrator-Token",
        "ca_next_action": "reuse_token_in_x_orchestrator_token_header_and_never_log_it",
        "mini_rule": "Never delete a file that was not explicitly named and approved. Fail loud: report the error, never swallow it or work around it silently.",
        "note": "Token rotates on restart or when its TTL (TOKEN_TTL_MINUTES) lapses; an expired token is auto-rotated on GET /api/token[/refresh]. TEST_TOKEN_TTL_MINUTES documents long-test planning.",
    }, headers=cors)


async def handle_ui_log_post(request):
    """POST /api/ui_log — append a single UI log entry."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"error": "blocked"}, status=403, headers=cors)
    try:
        entry = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400, headers=cors)
    if not isinstance(entry, dict):
        return web.json_response({"error": "entry must be object"}, status=400, headers=cors)
    entry["_server_ts"] = time.time()
    try:
        with UI_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning(f"ui_log write failed: {exc}")
        return web.json_response({"error": str(exc)}, status=500, headers=cors)
    return web.json_response({"ok": True}, headers=cors)


async def handle_matrix_log(request):
    """GET /api/matrix_log?limit=20 — return recent audit_tool_matrix records.

    V15 R13: matrix audit auto-writes .orchestrator_cache/matrix_log.jsonl on
    each open_session / prepare_ca_session. UI Matrix panel reads this endpoint.
    """
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"error": "blocked"}, status=403, headers=cors)
    limit = _query_int(request, 'limit', 20)
    log_path = ct.path_runtime.ORCHESTRATOR_DIR / ".orchestrator_cache" / "matrix_log.jsonl"
    entries = []
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for ln in lines[-limit:]:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    entries.append(json.loads(ln))
                except Exception:
                    pass
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=500, headers=cors)
    # Run a live audit so the panel always shows current drift even if log is stale
    try:
        live = ct._audit_tool_matrix_sync("")
    except Exception:
        live = None
    return web.json_response({
        "ok": True,
        "log_path": str(log_path),
        "history": entries,
        "live": live,
    }, headers=cors)


async def handle_ui_log_get(request):
    """GET /api/ui_log?limit=500 — return recent UI log entries."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"error": "blocked"}, status=403, headers=cors)
    limit = _query_int(request, 'limit', 500)
    entries = []
    if UI_LOG_FILE.exists():
        try:
            lines = UI_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            for ln in lines[-limit:]:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    entries.append(json.loads(ln))
                except Exception:
                    pass
            # Auto-trim if file > _UI_LOG_MAX
            if len(lines) > _UI_LOG_MAX:
                trimmed = "\n".join(lines[-_UI_LOG_MAX:]) + "\n"
                UI_LOG_FILE.write_text(trimmed, encoding="utf-8")
        except Exception as exc:
            logger.warning(f"ui_log read failed: {exc}")
    return web.json_response({"entries": entries, "count": len(entries)}, headers=cors)


async def handle_ui_log_clear(request):
    """DELETE /api/ui_log — clear UI log."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"error": "blocked"}, status=403, headers=cors)
    try:
        if UI_LOG_FILE.exists():
            UI_LOG_FILE.unlink()
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500, headers=cors)
    return web.json_response({"ok": True}, headers=cors)


async def handle_open_core_rule(request):
    """POST /api/open_core_rule - open CORE RULE.txt on the local machine."""
    cors = _cors(request.headers.get('Origin'))
    if not _is_local(request) or not _origin_ok(request):
        return web.json_response({"ok": False, "error": "blocked"}, status=403, headers=cors)
    if not CORE_RULE_FILE.exists():
        return web.json_response(
            {"ok": False, "error": f"File not found: {CORE_RULE_FILE}"},
            status=404,
            headers=cors,
        )
    try:
        if os.name == 'nt' and hasattr(os, 'startfile'):
            os.startfile(str(CORE_RULE_FILE))
        else:
            webbrowser.open(CORE_RULE_FILE.resolve().as_uri())
        return web.json_response({"ok": True, "path": str(CORE_RULE_FILE)}, headers=cors)
    except Exception as exc:
        logger.warning(f"open_core_rule failed: {exc}")
        return web.json_response({"ok": False, "error": str(exc)}, status=500, headers=cors)


# ─── Internal: filtered Magika scan ───────────────────────────────────────────

async def _scan_filtered(project_path: str) -> list[dict]:
    import os as _os

    from magika import Magika

    base = Path(project_path)
    files: list[Path] = []
    for root, dirs, filenames in _os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for fn in filenames:
            files.append(Path(root) / fn)

    def _do_scan():
        m = Magika()
        out = []
        for fp in files:
            try:
                r = m.identify_path(fp)
                group = getattr(r.output, 'group', '') or ''
                if group in CODE_GROUPS:
                    out.append({
                        "path": str(fp.relative_to(base)),
                        "type": getattr(r.output, 'ct_label', 'unknown') or 'unknown',
                        "group": group,
                        "mime": getattr(r.output, 'mime_type', '') or '',
                    })
            except Exception:
                pass
        return out

    return await asyncio.to_thread(_do_scan)

async def handle_token_panel(request):
    """GET /api/token_panel - token auth status for UI panel."""
    cors = _cors(request.headers.get('Origin'))
    prefix = (EXPECTED_TOKEN[:8] + "...") if EXPECTED_TOKEN else ""
    protected = sorted(name for name in ct.TOOL_MAP if name in ct.PROTECTED_TOOL_NAMES)
    unprotected = sorted(name for name in ct.TOOL_MAP if name not in ct.PROTECTED_TOOL_NAMES)
    protected_by_phase = {}
    for name in protected:
        protected_by_phase.setdefault(ct.tool_phase(name), []).append(name)
    protected_tiers = sorted(protected_by_phase)
    mutating_t4 = sorted(
        name for name in protected
        if ct.tool_phase(name) == "T4"
        and name in {"open_browser_page", "click", "fill", "evaluate_js", "desktop_click", "desktop_app_focus"}
    )
    # CAN-016: expose the TRUE origin/session-gated set + scope labels in the UI panel
    # (matches code_tools.protected_tools_status). Legacy field kept for back-compat.
    mutating_tools = sorted(name for name in ct.TOOL_MAP if name in _MUTATING_TOOLS)
    mutation_scopes = {name: ct.tool_mutation_scope(name) for name in sorted(ct.TOOL_MAP)
                       if ct.tool_mutation_scope(name) != "none"}
    panel = {
        "auth_status": "valid" if EXPECTED_TOKEN else "missing",
        "protected_tiers": protected_tiers,
        "protected_by_phase": protected_by_phase,
        "mutating_t4_tools_require_token": mutating_t4,
        "mutating_tools": mutating_tools,
        "mutation_scopes": mutation_scopes,
        "token_required_tools": sorted(_TOKEN_REQUIRED_TOOLS),
        "reuse_policy": "reuse_for_session",
        "max_retries": 2,
        "protected_tools": protected,
        "unprotected_tools": unprotected,
    }
    return web.json_response({
        "token_panel": panel,
        "status": panel["auth_status"],
        "auth_status": panel["auth_status"],
        "token_prefix": prefix,
        "t2_protected": True,
        "t3_protected": True,
        "t4_mutating_protected": bool(mutating_t4),
        "protected_tools": protected,
        "unprotected_tools": unprotected,
        "protected_tiers": panel["protected_tiers"],
        "protected_by_phase": protected_by_phase,
        "mutating_t4_tools_require_token": mutating_t4,
        "reuse_policy": panel["reuse_policy"],
        "max_retries": panel["max_retries"],
        "message": "Protected tools, including mutating T4 browser/desktop tools when listed, require X-Orchestrator-Token. Fetch from GET /api/token.",
    }, headers=cors)


async def handle_command_helper(request):
    """GET /api/command_helper - compact Windows wrapper metadata."""
    cors = _cors(request.headers.get('Origin'))
    project_path = request.query.get("project_path", "")
    subdir = request.query.get("subdir", "2. Rawcut")
    command = request.query.get("command", "node health-check.js")
    shell = request.query.get("shell", "powershell")
    payload = ct._path_quote_helper_payload(project_path, subdir, command, shell)
    payload["wrapper"] = payload["safe_command"]
    payload["cwd_preview"] = subdir
    payload["command_executed_preview"] = command
    payload["reason"] = " ".join(payload.get("warnings") or [])
    return web.json_response(payload, headers=cors)


async def handle_protected_tools_status(request):
    """GET /api/protected_tools_status - compact token protection status."""
    cors = _cors(request.headers.get('Origin'))
    raw = await ct.protected_tools_status()
    return web.json_response(json.loads(raw), headers=cors)


async def handle_tool_inspector(request):
    """GET /api/tool_inspector/{tool_name} — DEPRECATED (R16.9).

    R43.3 H6 reconciliation: skills + alias contract retired this endpoint in
    R16.9 in favor of `GET /api/tools?name_contains=<name>`. The endpoint stays
    LIVE for backwards compat but emits `X-Deprecated: true` plus a deprecation
    field in the JSON body so callers can migrate.
    """
    cors = _cors(request.headers.get('Origin'))
    tool_name = request.match_info.get("tool_name", "")
    if not tool_name:
        tool_name = request.path.rstrip("/").split("/")[-1]
    fn = getattr(ct, "TOOL_MAP", {}).get(tool_name)
    params = (_schema_for_tool(tool_name).get("parameters") or {}) if fn else {}
    allowed = sorted((params.get("properties") or {}).keys())
    required = _tool_required_fields(tool_name) if fn else []
    phase = ct.tool_phase(tool_name) if hasattr(ct, "tool_phase") else None
    requires_token = tool_name in _TOKEN_REQUIRED_TOOLS
    return web.json_response({
        "tool_name": tool_name,
        "tier": phase,
        "protected": requires_token,
        "mutating": _is_mutating_tool(tool_name),
        "requires_token": requires_token,
        "requires_session": _requires_session(tool_name),
        "verified": bool(fn),
        "accepted_fields": allowed,
        "required_fields": required,
        "payload_example": _payload_example_for_tool(tool_name, {"parameters": {"required": allowed, "properties": {}}}),
        "accepted_aliases": _alias_metadata(tool_name),
        "known_gotchas": _TOOL_GOTCHAS.get(tool_name, []),
        "recommended_prompt_rule": _TOOL_PROMPT_RULES.get(tool_name, ""),
        # V15 R40: cross-tool see_also map (from code_tools._R40_SEE_ALSO_MAP)
        "see_also": getattr(ct, "_R40_SEE_ALSO_MAP", {}).get(tool_name, {}),
        # R43.3 H6: deprecation marker
        "deprecated": True,
        "deprecated_since": "R16.9",
        "replacement": "GET /api/tools?name_contains=<name>",
        "deprecation_note": "This endpoint is kept for backwards compat. Skills + alias contract use /api/tools?name_contains=. Migrate before R45.",
    }, headers={**cors, "X-Deprecated": "true", "X-Deprecation-Since": "R16.9"})


# V15 R35 — Workflow editor UI popup (single HTML page, vanilla JS, same port 8083)
_WORKFLOW_EDITOR_HTML = r"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"/>
<title>Workflow Editor</title>
<style>
:root{color-scheme:dark;--bg:#0f1419;--panel:#1c2128;--border:#2d333b;--text:#e6edf3;--muted:#7d8590;--accent:#2f81f7;--accent-hov:#1f6feb;--err:#f85149;--ok:#3fb950}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--text);padding:20px}
h1{font-size:20px;margin:0 0 12px}h2{font-size:14px;margin:16px 0 4px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:12px}
label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
input[type=text],input[type=number],textarea{width:100%;padding:8px 10px;background:#0d1117;border:1px solid var(--border);border-radius:6px;color:var(--text);font-family:inherit;font-size:13px}
textarea{min-height:60px;resize:vertical}
button{background:var(--accent);color:white;border:none;padding:8px 14px;border-radius:6px;cursor:pointer;font-size:13px}
button:hover{background:var(--accent-hov)}button.sec{background:#21262d}button.sec:hover{background:#30363d}
button.danger{background:transparent;color:var(--err);border:1px solid var(--border)}
.step{border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:8px;background:#0d1117}
.step-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.step-head .num{font-weight:bold;font-size:14px}
.block{border-left:2px solid var(--border);padding-left:10px;margin-top:8px}
.row{display:flex;gap:8px;align-items:center;margin-top:8px}
.row > *{flex:0 0 auto}.row .grow{flex:1}
.scope{display:flex;gap:12px;margin:8px 0}
.scope label{display:flex;align-items:center;gap:4px;margin:0;color:var(--text)}
#status{margin-top:12px;padding:10px;border-radius:6px;font-size:12px;display:none}
#status.ok{display:block;background:#0f2e1a;color:var(--ok);border:1px solid var(--ok)}
#status.err{display:block;background:#2e0f0f;color:var(--err);border:1px solid var(--err)}
.muted{color:var(--muted);font-size:11px}
.sig-cfg{margin-top:6px;padding:8px;background:#161b22;border-radius:4px;font-size:12px}
.sig-cfg select{padding:4px 6px;background:#0d1117;color:var(--text);border:1px solid var(--border);border-radius:4px}
</style></head>
<body>
<h1>🛠️ Workflow Editor <span class="muted">— V15 R35</span></h1>

<div class="card">
  <label>Workflow name <span style="color:var(--err)">*</span></label>
  <input id="wfName" type="text" placeholder="rawcut - getfootage sheet"/>
    <p class="muted">Will be slugified into a filename (e.g. "rawcut-getfootage-sheet.md")</p>

    <label style="margin-top:10px" class="muted">Project path (optional, R37 no longer required)</label>
    <input id="projectPath" type="text" placeholder="D:\path\to\project (optional)"/>
    <p class="muted">R37 Y1: all workflows are saved globally at `&lt;[A]&gt;/.ToolA_Skill/workflows/`. Project scope is no longer used.</p>
</div>

<h2>Steps</h2>
<div id="steps"></div>

<div class="row">
  <button onclick="addStep()">+ Add step</button>
    <select id="wfPicker" class="sec" style="min-width:300px;padding:6px"><option value="">— select a workflow to load —</option></select>
  <button class="sec" onclick="loadExisting()">📂 Load selected</button>
  <button class="sec" onclick="refreshWorkflowPicker()" title="Reload workflow list">⟳</button>
  <div class="grow"></div>
  <label style="display:flex;align-items:center;gap:4px;margin:0"><input type="checkbox" id="overwrite"/> overwrite</label>
  <button onclick="saveWorkflow()">💾 Save workflow</button>
</div>

<div class="row" style="margin-top:6px;border-top:1px solid #333;padding-top:8px">
  <strong style="margin-right:6px">Run control:</strong>
    <button class="sec" onclick="wfActivate()" title="Activate the selected workflow">&#9654; Activate</button>
    <button class="sec" onclick="wfRestart()" title="Reset the workflow to step 1 (archive old state)">&#128260; Restart</button>
  <input id="gotoStep" type="number" min="1" placeholder="step#" style="width:74px;padding:6px"/>
    <button class="sec" onclick="wfGoto()" title="Jump the workflow to this step">&#9197; Go to step</button>
    <button class="sec" onclick="wfRefreshStatus()" title="View the current step">&#128065; Status</button>
  <span id="wfCtrlStatus" style="margin-left:8px;color:#9cf"></span>
</div>

<div id="status"></div>

<script>
let stepCount=0;
const stepsEl=document.getElementById('steps');
async function _wfTok(){const r=await fetch('/api/token');return (await r.json()).token;}
async function _wfCall(tool,body){const t=await _wfTok();const r=await fetch('/api/call/'+tool,{method:'POST',headers:{'Content-Type':'application/json','X-Orchestrator-Token':t},body:JSON.stringify(body)});return r.json();}
function _wfName(){const n=(document.getElementById('wfPicker').value||'').trim();if(!n)document.getElementById('wfCtrlStatus').textContent='⚠ select a workflow in the dropdown first';return n;}
async function wfActivate(){const n=_wfName();if(!n)return;const j=await _wfCall('workflow_activate',{name:n});document.getElementById('wfCtrlStatus').textContent='Activate: '+(j.ok?'OK':(j.error||j.error_name||'fail'));}
async function wfRestart(){const n=_wfName();if(!n)return;if(!confirm('Restart workflow \''+n+'\' to step 1 (archive old state)?'))return;const j=await _wfCall('workflow_reset',{name:n});document.getElementById('wfCtrlStatus').textContent='Restart: '+(j.ok?('OK -> step '+(j.current_step||1)):(j.error||j.error_name||'fail'));}
async function wfGoto(){const n=_wfName();if(!n)return;const s=parseInt(document.getElementById('gotoStep').value);if(!s){document.getElementById('wfCtrlStatus').textContent='⚠ enter a step number';return;}const j=await _wfCall('workflow_goto_step',{name:n,target_step:s,reason:'UI goto-step'});document.getElementById('wfCtrlStatus').textContent='Goto '+s+': '+(j.ok?('OK -> step '+(j.current_step||s)):(j.error||j.error_name||'fail'));}
async function wfRefreshStatus(){const n=_wfName();if(!n)return;const j=await _wfCall('workflow_status',{name:n});document.getElementById('wfCtrlStatus').textContent='Step '+(j.current_step||'?')+'/'+(j.total_steps||'?')+(j.complete?' (complete)':'');}

function addStep(insertAfter=null){
  stepCount++;
  const stepNum=stepCount;
  const div=document.createElement('div');
  div.className='step';
  div.dataset.step=stepNum;
  div.innerHTML=`
    <div class="step-head">
      <span class="num">Step <span class="step-display">${stepNum}</span></span>
      <div class="row" style="margin:0">
        <button class="sec" onclick="moveStep(this,-1)">↑</button>
        <button class="sec" onclick="moveStep(this,1)">↓</button>
        <button class="sec" onclick="insertStepAfter(this)">+ Below</button>
        <button class="danger" onclick="removeStep(this)">Remove</button>
      </div>
    </div>
    <div class="blocks"></div>
    <button class="sec" onclick="addBlock(this)" style="margin-top:8px">+ Add title/context</button>
    <div class="sig-cfg">
      <label>Completion signal (optional):</label>
      <select class="sig-kind">
        <option value="">— none —</option>
        <option value="folder_growth_stable">folder_growth_stable</option>
        <option value="process_cpu_idle">process_cpu_idle</option>
        <option value="process_io_idle">process_io_idle</option>
        <option value="log_pattern_match">log_pattern_match</option>
      </select>
    <input type="text" class="sig-params" placeholder="e.g. folder=D:\\out stable_seconds=5" style="margin-top:4px"/>
    </div>
  `;
  if(insertAfter){
    insertAfter.parentNode.insertBefore(div, insertAfter.nextSibling);
  } else {
    stepsEl.appendChild(div);
  }
  addBlock(div.querySelector('.blocks').nextElementSibling);
  renumberSteps();
}

function addBlock(btn){
  const blocks=btn.previousElementSibling;
  const b=document.createElement('div');
  b.className='block';
  b.innerHTML=`
    <label>Title</label>
    <input class="b-title" type="text" placeholder="e.g. Link sheet"/>
    <label style="margin-top:6px">Context</label>
    <textarea class="b-ctx" placeholder="User-provided context — [A] does not transform"></textarea>
    <button class="danger" onclick="this.parentNode.remove()" style="margin-top:4px;padding:4px 8px;font-size:11px">Remove block</button>
  `;
  blocks.appendChild(b);
}

function removeStep(btn){
  btn.closest('.step').remove();
  renumberSteps();
}

function moveStep(btn, dir){
  const s=btn.closest('.step');
  const sib = dir<0 ? s.previousElementSibling : s.nextElementSibling;
  if(sib && sib.classList.contains('step')){
    if(dir<0) stepsEl.insertBefore(s, sib);
    else stepsEl.insertBefore(sib, s);
    renumberSteps();
  }
}

function insertStepAfter(btn){
  const s=btn.closest('.step');
  addStep(s);
}

function renumberSteps(){
  Array.from(stepsEl.querySelectorAll('.step')).forEach((s,i)=>{
    s.querySelector('.step-display').textContent = i+1;
  });
}

function buildMD(){
  const name=document.getElementById('wfName').value.trim();
  if(!name) return null;
  let md=`# ${name}\n`;
  const steps=Array.from(stepsEl.querySelectorAll('.step'));
  steps.forEach((s,i)=>{
    const n=i+1;
    md+=`\n## Step ${n}\n`;
    const blocks=s.querySelectorAll('.block');
    blocks.forEach(b=>{
      const t=b.querySelector('.b-title').value.trim();
      const ctx=b.querySelector('.b-ctx').value.trim();
      if(t){
        md+=`\n### ${t}\n`;
        if(ctx) md+=`${ctx}\n`;
      }
    });
    const sigKind=s.querySelector('.sig-kind').value;
    const sigParams=s.querySelector('.sig-params').value.trim();
    if(sigKind){
      md+=`[completion_signal: kind=${sigKind}${sigParams?' '+sigParams:''}]\n`;
    }
  });
  return md;
}

async function saveWorkflow(){
  const name=document.getElementById('wfName').value.trim();
  const projectPath=document.getElementById('projectPath').value.trim();
  // V15 R37 Y1: scope forced to global; ignore radio
  const scope='global';
  const overwrite=document.getElementById('overwrite').checked;
  const body_md=buildMD();
  const stat=document.getElementById('status');
  if(!name){stat.className='err';stat.textContent='Workflow name required';return;}
  if(!body_md){stat.className='err';stat.textContent='Body MD empty';return;}
  try{
    const tokRes=await fetch('/api/token');
    const tok=(await tokRes.json()).token;
    const res=await fetch('/api/call/workflow_create_from_ui',{
      method:'POST',
      headers:{'Content-Type':'application/json','X-Orchestrator-Token':tok},
      body:JSON.stringify({name,body_md,scope,project_path:projectPath,overwrite})
    });
    const j=await res.json();
    if(j.ok){
      stat.className='ok';
      stat.textContent=`✅ Saved: ${j.path} (${j.step_count} steps, ${j.bytes_written} bytes)`;
    } else {
      stat.className='err';
      stat.textContent=`❌ ${j.error_name||'ERROR'}: ${j.error||JSON.stringify(j)}`;
    }
  }catch(e){
    stat.className='err';
    stat.textContent='Network: '+e.message;
  }
}

// R65: populate the workflow dropdown from workflow_list (no typing required).
async function refreshWorkflowPicker(){
  const projectPath=document.getElementById('projectPath').value.trim();
  const sel=document.getElementById('wfPicker');
  const s=document.getElementById('status');
  try{
    const res=await fetch('/api/call/workflow_list',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({project_path:projectPath})
    });
    const j=await res.json();
    sel.innerHTML='<option value="">— select a workflow to load —</option>';
    const wfs=(j&&j.workflows)||[];
    if(!j.ok || !wfs.length){
      s.className='err';
      s.textContent='No workflows found. Sources: '+((j.sources_scanned||[]).join(', ')||'none');
      return;
    }
    wfs.forEach(w=>{
      const opt=document.createElement('option');
      opt.value=w.name;
      opt.textContent=`${w.name} (${w.source||'?'}, ${w.step_count||0} steps)`;
      sel.appendChild(opt);
    });
    s.className='ok';
    s.textContent=`📋 ${wfs.length} workflow(s) in the dropdown — select one and click 'Load selected'`;
  }catch(e){
    s.className='err'; s.textContent='Picker error: '+e.message;
  }
}

// R65: load the selected workflow's FULL body into the editor (reuses the
// proven loadFromQuery() body path: /api/workflow_md -> populateFromMD).
// Replaces the old stub that only prefilled the name and never loaded steps.
async function loadExisting(){
  const sel=document.getElementById('wfPicker');
  const pick=sel ? sel.value : '';
  const s=document.getElementById('status');
  if(!pick){
    // Picker empty (not yet loaded) — refresh once, then prompt user to pick.
    await refreshWorkflowPicker();
    alert('Select a workflow in the dropdown, then click "Load selected".');
    return;
  }
  const projectPath=document.getElementById('projectPath').value.trim();
  try{
    const q='?name='+encodeURIComponent(pick)+(projectPath?'&project_path='+encodeURIComponent(projectPath):'');
    const r=await fetch('/api/workflow_md'+q);
    const j=await r.json();
    if(j.ok && j.body_md){
      populateFromMD(j.body_md);
      document.getElementById('overwrite').checked=true;
      s.className='ok';
      s.textContent=`📂 Loaded: ${pick} (${j.path||'global'}) — ${j.step_count||'?'} steps. Edit + Save with overwrite.`;
    } else {
      s.className='err';
      s.textContent='Load failed: '+(j.error||'not found');
    }
  }catch(e){
    s.className='err'; s.textContent='Error: '+e.message;
  }
}

// R36: parse and populate from MD text
function populateFromMD(md){
  // clear existing steps
  document.getElementById('steps').innerHTML='';
  stepCount=0;
  // workflow title
  const titleM=md.match(/^#\s+(.+?)\s*$/m);
  if(titleM) document.getElementById('wfName').value=titleM[1].trim();
  // split by ## Step N
  const stepBlocks=md.split(/^##\s+Step\s+\d+\s*$/mi).slice(1);
  if(stepBlocks.length===0){ addStep(); return; }
  stepBlocks.forEach(block=>{
    addStep();
    const lastStep=stepsEl.lastElementChild;
    // remove default empty block
    const blocksContainer=lastStep.querySelector('.blocks');
    blocksContainer.innerHTML='';
    // parse ### subtitle / context blocks
    const subRe=/^###\s+(.+?)\s*$/gm;
    const subs=[...block.matchAll(subRe)];
    if(subs.length===0){
      // step has no ### header; treat whole text as one anonymous block
      addBlock(blocksContainer.nextElementSibling);
      const b=blocksContainer.querySelector('.block');
      if(b) b.querySelector('.b-ctx').value=block.trim();
    } else {
      subs.forEach((m,i)=>{
        const title=m[1].trim();
        const ctxStart=m.index+m[0].length;
        const ctxEnd=(i+1)<subs.length ? subs[i+1].index : block.length;
        let ctx=block.slice(ctxStart, ctxEnd).trim();
        // extract completion_signal tags
        const sigRe=/\[completion_signal:\s*(.+?)\]/gi;
        const sigs=[...ctx.matchAll(sigRe)];
        ctx=ctx.replace(sigRe,'').trim();
        addBlock(blocksContainer.nextElementSibling);
        const allBlocks=blocksContainer.querySelectorAll('.block');
        const b=allBlocks[allBlocks.length-1];
        b.querySelector('.b-title').value=title;
        b.querySelector('.b-ctx').value=ctx;
        if(sigs.length && i===subs.length-1){
          // attach first signal to step-level (UI only supports one per step)
          const raw=sigs[0][1].trim();
          const kindM=raw.match(/kind=(\w+)/);
          if(kindM){
            lastStep.querySelector('.sig-kind').value=kindM[1];
            // remaining params (strip kind=...)
            const params=raw.replace(/kind=\w+\s*/,'').trim();
            lastStep.querySelector('.sig-params').value=params;
          }
        }
      });
    }
  });
  renumberSteps();
}

async function loadFromQuery(){
  const params=new URLSearchParams(location.search);
  const loadName=params.get('load');
  const draftTok=params.get('draft');
  const projParam=params.get('project_path');
  if(projParam) document.getElementById('projectPath').value=projParam;
  if(draftTok){
    try{
      const r=await fetch('/api/workflow_draft?token='+encodeURIComponent(draftTok));
      const j=await r.json();
      if(j.ok && j.body_md){
        populateFromMD(j.body_md);
        document.getElementById('overwrite').checked=true;
        const s=document.getElementById('status');
        s.className='ok';
        s.textContent=`📥 Loaded agent draft: ${j.name} (expires ${j.expires_at})`;
        return;
      }
    }catch(e){console.error(e)}
  }
  if(loadName){
    try{
      const q='?name='+encodeURIComponent(loadName)+(projParam?'&project_path='+encodeURIComponent(projParam):'');
      const r=await fetch('/api/workflow_md'+q);
      const j=await r.json();
      if(j.ok && j.body_md){
        populateFromMD(j.body_md);
        document.getElementById('overwrite').checked=true;
        const s=document.getElementById('status');
        s.className='ok';
        s.textContent=`📂 Loaded existing: ${j.path}`;
        return;
      } else {
        const s=document.getElementById('status');
        s.className='err';
        s.textContent='Load failed: '+(j.error||'not found');
      }
    }catch(e){console.error(e)}
  }
  // default: 1 empty step
  addStep();
}

// init from query (?load=<slug> or ?draft=<token>) or empty
loadFromQuery();
// R65: auto-populate the workflow dropdown on page load so user can click-pick
// instead of typing a name (and re-populate when project path changes).
refreshWorkflowPicker();
document.getElementById('projectPath').addEventListener('change', refreshWorkflowPicker);
</script>
</body></html>"""

async def handle_workflow_editor_ui(request):
    """GET /ui/workflow_editor - R35 single-page UI for workflow MD authoring.
    R36: supports `?load=<slug>&project_path=...` and `?draft=<token>` for prefill.
    """
    cors = _cors(request.headers.get('Origin'))
    return web.Response(text=_WORKFLOW_EDITOR_HTML,
                         content_type="text/html",
                         charset="utf-8",
                         headers=cors)


async def handle_workflow_md_get(request):
    """V15 R36 GET /api/workflow_md?name=<slug>&project_path=<root> — UI editor load existing."""
    cors = _cors(request.headers.get('Origin'))
    name = request.query.get('name', '')
    project_path = request.query.get('project_path', '')
    if not name:
        return web.json_response({"ok": False, "error": "name required"},
                                  status=400, headers=cors)
    raw = await ct.workflow_read_md(name=name, project_path=project_path)
    data = json.loads(raw)
    return web.json_response(data, headers=cors)


async def handle_workflow_draft_get(request):
    """V15 R36 GET /api/workflow_draft?token=<t> — UI editor load agent draft."""
    cors = _cors(request.headers.get('Origin'))
    token = request.query.get('token', '')
    if not token:
        return web.json_response({"ok": False, "error": "token required"},
                                  status=400, headers=cors)
    raw = await ct.workflow_draft_get(token=token)
    data = json.loads(raw)
    return web.json_response(data, headers=cors)


async def handle_workflow_active_get(request):
    """V15 R36 GET /api/workflow_active?project_path=<root> — UI live polling."""
    cors = _cors(request.headers.get('Origin'))
    project_path = request.query.get('project_path', '')
    raw = await ct.workflow_active_get(project_path=project_path)
    data = json.loads(raw)
    return web.json_response(data, headers=cors)


async def handle_auto_token_get(request):
    """V15 R42 GET /api/config/auto_token_inject — read current mode."""
    cors = _cors(request.headers.get('Origin'))
    return web.json_response({
        "ok": True,
        "enabled": bool(AUTO_TOKEN_INJECT),
        "env_var": "ORCHESTRATOR_AUTO_TOKEN",
        "note": ("ON = protected tools skip MUTATION_TOKEN_REQUIRED. "
                 "Audit boundary bypassed. Use only when User trusts CA."),
    }, headers=cors)

async def handle_auto_token_post(request):
    """V15 R42 POST /api/config/auto_token_inject {"enabled": bool} — toggle at runtime."""
    global AUTO_TOKEN_INJECT
    cors = _cors(request.headers.get('Origin'))
    # Toggle requires a VALID, unexpired token (CAN-001/CAN-002): this endpoint is
    # itself protected, and enabling dev-bypass must not be possible with a stale token.
    token = request.headers.get('X-Orchestrator-Token')
    _terr = _validate_mutation_token(token)
    if _terr:
        return web.json_response(
            {"ok": False,
             "error": ("Toggle token expired; re-fetch GET /api/token"
                       if _terr == "MUTATION_TOKEN_EXPIRED"
                       else "Toggle requires X-Orchestrator-Token header"),
             "error_name": _terr},
            status=403, headers=cors,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    new_val = bool(body.get("enabled", False))
    old_val = AUTO_TOKEN_INJECT
    AUTO_TOKEN_INJECT = new_val
    logger.warning(f"AUTO_TOKEN_INJECT toggled: {old_val} -> {new_val} via /api/config endpoint")
    return web.json_response({
        "ok": True,
        "enabled": AUTO_TOKEN_INJECT,
        "previous": old_val,
        "audit_warning": (
            "AUTO_TOKEN_INJECT=true means protected tools no longer require X-Orchestrator-Token. "
            "All such calls logged as auto_token_inject events for audit trail."
        ) if new_val else "Strict mode active.",
    }, headers=cors)


# ─── App factory ──────────────────────────────────────────────────────────────

async def handle_retired_round_endpoint(request):
    cors = _cors(request.headers.get('Origin'))
    return web.json_response(
        {
            "ok": False,
            "error": "Endpoint not found",
            "error_name": "NOT_FOUND",
            "retired": True,
        },
        status=404,
        headers=cors,
    )


# ─── FixA2407 read-back / preflight handlers ─────────────────────────────────

async def handle_result_ref(request):
    """P2-2: read back a bounded tool response by its result_ref.

    _shape_tool_response_text has always cached the full text and advertised
    `result_ref` with ca_next_action=read_bounded_result_by_ref..., but nothing ever
    read _RESULT_REF_CACHE - it was a write-only cache, so the documented recovery path
    did not exist and a CA had to re-run the tool with narrower filters. Byte-offset
    paging keeps each read bounded.
    """
    cors = _cors(request.headers.get('Origin'))
    ref = (request.match_info.get("ref") or "").strip()
    raw = _RESULT_REF_CACHE.get(ref)
    if raw is None:
        return web.json_response({
            "ok": False,
            "error": f"unknown result_ref: {ref}",
            "error_name": "REF_EXPIRED_AFTER_RESTART",
            "type": "SCHEMA_VIOLATION",
            "phase": "schema",
            "severity": "ERROR",
            "retryable": False,
            "reason": "ref was evicted, never existed, or expired when the server restarted",
            "hint": ("Refs live in memory only and expire on every server restart or oldest-first "
                     f"eviction (capacity={_RESULT_REF_MAX_ENTRIES}); re-run the tool for a fresh ref."),
            "cached_refs": len(_RESULT_REF_CACHE),
            "ca_next_action": "rerun_tool_with_narrower_filters",
            "generation": SERVER_GENERATION,
        }, status=404, headers=cors)
    data = raw.encode("utf-8", errors="replace")
    try:
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        offset = 0
    try:
        max_bytes = int(request.query.get("max_bytes", str(_RESULT_BUDGET_BYTES)))
    except ValueError:
        max_bytes = _RESULT_BUDGET_BYTES
    max_bytes = max(1024, min(max_bytes, 2_000_000))
    chunk = data[offset:offset + max_bytes]
    next_offset = offset + len(chunk)
    more = next_offset < len(data)
    return web.json_response({
        "ok": True,
        "result_ref": ref,
        "total_bytes": len(data),
        "offset": offset,
        "returned_bytes": len(chunk),
        "next_offset": next_offset if more else None,
        "truncated": more,
        "content": chunk.decode("utf-8", errors="replace"),
        "ca_next_action": "read_next_offset" if more else "done",
        "generation": SERVER_GENERATION,
    }, headers=cors, dumps=lambda x: json.dumps(x, ensure_ascii=False))


async def handle_write_status(request):
    """P1-1: answer 'did my write actually commit?' after a client-side timeout.

    write_fix / write_transaction routinely finish server-side after the caller has
    already given up at 120-300s. The caller then cannot tell committed from
    not_committed and either retries blindly or burns a read-back. The server always
    knew; now it keeps the answer. Query by operation_id (deterministic for an identical
    payload) or by project_path+file_path for the most recent write to that target.
    """
    cors = _cors(request.headers.get('Origin'))
    op_id = (request.query.get("operation_id") or "").strip()
    rec = _WRITE_LEDGER.get(op_id) if op_id else None
    ledger_source = "memory" if rec is not None else "sqlite"
    if rec is None and op_id:
        rec = await _write_ledger_db_get_async(operation_id=op_id)
    lookup = "operation_id"
    candidates: list = []
    if rec is None:
        pp, fp = _write_ledger_target({
            "project_path": request.query.get("project_path", ""),
            "file_path": request.query.get("file_path", "") or request.query.get("path", ""),
        })
        tool_filter = (request.query.get("tool") or "").strip()
        if pp or fp:
            lookup = "target"
            mem = [_WRITE_LEDGER[i] for i in _write_ledger_target_ids(pp, fp)
                   if i in _WRITE_LEDGER]
            rec, candidates, ambiguous = _write_ledger_pick_candidate(mem, tool_filter)
            if rec is not None or ambiguous:
                ledger_source = "memory"
            else:
                rows = await _write_ledger_db_list_async((pp, fp))
                rec, candidates, ambiguous = _write_ledger_pick_candidate(rows, tool_filter)
                ledger_source = "sqlite"
            if ambiguous:
                # ISS-01: several DIFFERENT tools share this target key and none
                # is uniquely in flight. Answering with the newest row is how a
                # run_project_command ledger became the answer to "did my
                # cbm_incremental_index commit?".
                return web.json_response({
                    "ok": False,
                    "error": ("this project_path/file_path matches several mutating "
                              "operations from different tools"),
                    "error_name": "WRITE_STATUS_AMBIGUOUS_TARGET",
                    "commit_state": "unknown",
                    "operation_state": "unknown",
                    "safe_to_retry": False,
                    "retryable": False,
                    "lookup": "target",
                    "ledger_source": ledger_source,
                    "project_path": pp,
                    "file_path": fp,
                    "candidates": candidates,
                    "tools": sorted({str(c.get("tool") or "") for c in candidates}),
                    "ca_next_action": "retry_with_operation_id",
                    "hint": ("Pass operation_id from the tool response, or narrow with "
                             "&tool=<tool_name>. Index/script tools all record "
                             "file_path=\"\", so one root can hold several unrelated "
                             "operations at once."),
                    "generation": SERVER_GENERATION,
                }, status=409, headers=cors)
    if rec is None:
        return web.json_response({
            "ok": False,
            "error": "no ledger entry for this operation_id / target",
            "error_name": "WRITE_STATUS_UNKNOWN",
            "commit_state": "unknown",
            "retryable": False,
            "reason": ("the entry is older than retention, was never recorded, or the "
                       "tool is not a mutating tool"),
            "hint": ("Pass operation_id from the write response, or project_path + file_path. "
                     "unknown means undetermined - verify with read_file_range before retrying."),
            "ledger_entries": len(_WRITE_LEDGER),
            "ca_next_action": "read_file_range_to_confirm_before_retry",
            "generation": SERVER_GENERATION,
        }, status=404, headers=cors)
    state = _write_ledger_commit_state(rec)
    progress = rec.get("progress")
    if progress is None and isinstance(rec.get("result"), dict):
        progress = rec["result"].get("progress")
    progress_state = state
    if state == "in_flight" and isinstance(progress, dict):
        idle = max(0.0, time.time() - float(rec.get("last_progress_at") or time.time()))
        progress = {**progress, "seconds_since_progress": round(idle, 3)}
        stall_window = max(30.0, float(os.environ.get(
            "ORCHESTRATOR_OPERATION_STALL_SECONDS", "120") or 120))
        progress_state = "stalled" if idle >= stall_window else "running"
    # B031 (#7): "committed" settles a SOURCE write -- resending it would apply the
    # edit twice, so do_not_retry is the right answer there. It is the WRONG answer
    # for a non-source scope (derived_index / cache / audit_log): the caller's
    # product is the RESULT, and _WRITE_LEDGER_RESULT_KEYS keeps small scalars on
    # purpose, so the analysis is not in this row and never will be. Measured on
    # cbm_what_if after a client timeout: commit_state=committed with
    # result={"ok": true, "result_keys": ["ok", "result"], "summary_note": "no known
    # evidence field ..."} -- the ledger correctly says the evidence lives under
    # `result`, and then the advice told the caller not to go get it. That tool's
    # own schema says the simulation "never edits source/DB" and a warm re-run cost
    # 1.6s, so the caller was refused a cheap, side-effect-free replay and left
    # holding neither the analysis nor permission to recompute it. Scope comes from
    # the registry (ct.NON_SOURCE_MUTATION_SCOPES, one SOT), never from the name.
    try:
        rerun_safe = ct.tool_mutation_scope(
            str(rec.get("tool") or "")) in _R102_NON_SOURCE_SCOPES
    except Exception:
        rerun_safe = False  # unknown scope: keep the conservative source-write answer
    return web.json_response({
        "ok": True,
        "lookup": lookup,
        "ledger_source": ledger_source,
        "commit_state": state,
        "operation_state": progress_state,
        "safe_to_retry": (state == "not_committed"
                          or (state == "committed" and rerun_safe)),
        "ca_next_action": {
            "committed": ("rerun_tool_for_result_not_kept_in_ledger" if rerun_safe
                          else "do_not_retry_write_already_committed"),
            "not_committed": "retry_write",
            "in_flight": "wait_and_poll_write_status_do_not_retry",
        }.get(state, "read_file_range_to_confirm_before_retry"),
        **{k: rec.get(k) for k in ("operation_id", "tool", "status", "project_path",
                                   "file_path", "session_id", "started_at", "finished_at",
                                   "duration_ms", "attempts", "result",
                                   "origin_generation", "progress", "last_progress_at")},
        "generation": SERVER_GENERATION,
        "progress": progress,
        # ISS-01: what else shares this target key. Present on every target
        # lookup so a caller can see the swap risk even when one candidate was
        # resolvable, instead of discovering it from a mismatched tool name.
        "candidates": candidates,
    }, headers=cors, dumps=lambda x: json.dumps(x, ensure_ascii=False))


async def write_status(operation_id: str = "", project_path: str = "",
                       file_path: str = "", tool: str = "") -> str:
    """Catalog/MCP access to the exact same write-commit ledger as HTTP GET."""
    from types import SimpleNamespace
    request = SimpleNamespace(headers={}, query={
        "operation_id": operation_id, "project_path": project_path,
        "file_path": file_path, "tool": tool,
    })
    response = await handle_write_status(request)
    return response.text


_register_tool(
    name="write_status", handler=write_status, phase="T1",
    tool_map=ct.TOOL_MAP, tool_schemas=ct.TOOL_SCHEMAS, tool_phases=ct.TOOL_PHASES,
    arg_aliases=_ARG_ALIASES, phase_labels=ct.PHASE_LABELS,
    schema={"description": "Read write commit truth after timeout by operation_id or project_path+file_path. Same ledger as GET /api/write_status; unknown is not safe to replay. Target lookup returns WRITE_STATUS_AMBIGUOUS_TARGET when several tools share the root (index/script tools all record file_path=\"\") - pass operation_id or tool to disambiguate.",
            "parameters": {"type": "object", "properties": {
                "operation_id": {"type": "string", "default": "", "description": "ID returned by the write response. The only lookup that cannot be swapped for another tool's entry."},
                "project_path": {"type": "string", "default": "", "description": "Explicit project root for target lookup."},
                "file_path": {"type": "string", "default": "", "description": "Target file when operation_id is unavailable."},
                "tool": {"type": "string", "default": "", "description": "Narrow a target lookup to one tool name (e.g. cbm_incremental_index) when the root holds several concurrent operations."},
            }, "required": []}},
)


async def handle_semaphores(request):
    """Expose admission and blocking-work lane ownership."""
    rows = _semaphore_status_rows()
    executors = executor_lane_status()
    return web.json_response({
        "ok": True,
        "semaphores": rows,
        "executors": executors,
        "generation": SERVER_GENERATION,
    }, headers=_cors(request.headers.get("Origin")))


async def handle_cancel_operation(request):
    """Cancel one in-flight request by its request/ledger operation id."""
    cors = _cors(request.headers.get("Origin"))
    try:
        body = await request.json()
    except Exception:
        body = {}
    operation_id = str((body or {}).get("operation_id") or "").strip()
    cancel_event = _OPERATION_CANCEL_EVENTS.get(operation_id)
    # For a plain tool call the ledger operation_id IS the request op id, so it
    # must resolve a task on its own; request_operation_id only overrides the
    # two-id case (a write whose ledger id differs from the request that started
    # it). Without this fallback /api/cancel answered OPERATION_NOT_IN_FLIGHT for
    # every asyncio task it existed to cancel.
    request_id = (str((body or {}).get("request_operation_id") or "").strip()
                  or operation_id)
    task = _INFLIGHT_OPERATIONS.get(request_id) if request_id else None
    if not operation_id:
        return web.json_response({"ok": False, "error_name": "OPERATION_ID_REQUIRED",
                                  "error": "operation_id is required"}, status=400, headers=cors)
    if (task is None or task.done()) and cancel_event is None:
        return web.json_response({"ok": False, "operation_id": operation_id,
                                  "cancelled": False, "error_name": "OPERATION_NOT_IN_FLIGHT",
                                  "error": "operation is not in flight"}, status=404, headers=cors)
    if cancel_event is not None:
        cancel_event.set()
    if task is not None and not task.done() and cancel_event is None:
        task.cancel()
    return web.json_response({"ok": True, "operation_id": operation_id, "cancelled": True,
                              "generation": SERVER_GENERATION}, headers=cors)


async def handle_native_preflight(request):
    """P2-1: classify a native operation BEFORE it happens.

    log_native_event returns HOST_GATE_DENY / FORBIDDEN_NATIVE only after the native
    operation already ran, so the policy documents a violation instead of preventing it.
    This runs the same Matrix classifier with no ledger write, so a CA can ask
    'am I allowed to do this natively?' first and route via [A] when the answer is no.
    """
    cors = _cors(request.headers.get('Origin'))
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        from orchestrator.core.ca_policy_matrix import classify_action as _classify
        cls = _classify(
            tool_name=str(body.get("tool_name") or ""),
            action_type=str(body.get("action_type") or ""),
            transport=str(body.get("transport") or "native"),
            scope=str(body.get("scope") or ""),
            target=str(body.get("target") or ""),
            project_path=str(body.get("project_path") or ""),
            agent_id=str(body.get("agent_id") or ""),
        )
    except Exception as exc:
        return web.json_response({
            "ok": False,
            "error": f"preflight classification failed: {exc}",
            "error_name": "PREFLIGHT_UNAVAILABLE",
            "retryable": False,
            "decision": "unknown",
            "hint": "Matrix classifier unavailable; fall back to [A] tools and log_native_event.",
            "ca_next_action": "prefer_via_a_tools",
            "generation": SERVER_GENERATION,
        }, status=503, headers=cors)
    host = cls.get("host_gate") or {}
    host_denied = isinstance(host, dict) and str(host.get("decision") or "").startswith("deny")
    forbidden = bool(cls.get("is_forbidden_native"))
    decision = "deny" if (forbidden or host_denied) else (
        "allow" if cls.get("is_allowed_native") or cls.get("is_via_a") else "review")
    preferred = (cls.get("preferred_via_a") or [])[:5]
    return web.json_response({
        "ok": True,
        "decision": decision,
        "allowed": decision == "allow",
        "audit_status": "not_logged_preflight_only",
        "error_name": ("FORBIDDEN_NATIVE" if forbidden else
                       "HOST_GATE_DENY" if host_denied else None),
        "task_class": cls.get("task_class"),
        "native_policy": cls.get("native_policy"),
        "classification": cls.get("classification"),
        "is_forbidden_native": forbidden,
        "is_allowed_native": cls.get("is_allowed_native"),
        "is_via_a": cls.get("is_via_a"),
        "is_unclassified": cls.get("is_unclassified"),
        "host_gate": host,
        "preferred_via_a": preferred,
        "ca_next_action": (cls.get("ca_next_action") or
                           ("use_preferred_via_a_tools" if decision == "deny" else
                            "proceed_then_log_native_event")),
        "hint": ("Preflight only - nothing was logged. On decision=deny do NOT perform the "
                 "native operation; use preferred_via_a instead."),
        "generation": SERVER_GENERATION,
    }, headers=cors, dumps=lambda x: json.dumps(x, ensure_ascii=False))
def create_app() -> web.Application:
    _assert_council_runtime_key_uniqueness()
    # CAN-006: bound request body size (clean 413 via middleware + hard backstop here).
    # CAN-004: install the rate-limit / body-size middleware for the /api/call surface.
    app = web.Application(
        client_max_size=MAX_REQUEST_BODY_BYTES,
        middlewares=[_http_defense_middleware],
    )
    app.router.add_get('/', handle_root)
    app.router.add_get('/favicon.ico', handle_favicon)
    app.router.add_get('/health', handle_health)
    app.router.add_get('/api/health', handle_health)
    app.router.add_get('/council', handle_council_dashboard)
    app.router.add_get('/council/glass', handle_council_glass_dashboard)
    app.router.add_get('/api/council/activity', handle_council_activity)
    app.router.add_get('/api/council/state', handle_council_state)
    app.router.add_get('/api/council/events', handle_council_events)
    app.router.add_post('/api/council/preflight', handle_council_preflight)
    app.router.add_post('/api/council/message', handle_council_message)
    app.router.add_post('/api/council/send-async', handle_council_send_async)
    app.router.add_post('/api/council/commentary', handle_council_commentary)
    app.router.add_get('/api/council/cron/jobs', handle_council_cron_jobs_list)
    app.router.add_post('/api/council/cron/jobs', handle_council_cron_jobs_create)
    app.router.add_put('/api/council/cron/jobs/{job_id}', handle_council_cron_jobs_update)
    app.router.add_delete('/api/council/cron/jobs/{job_id}', handle_council_cron_jobs_delete)
    app.router.add_post('/api/council/cron/jobs/{job_id}/trigger', handle_council_cron_jobs_trigger)
    app.router.add_get('/api/council/cron/jobs/{job_id}/runs', handle_council_cron_runs)
    app.router.add_get('/api/council/cron/runs', handle_council_cron_runs)
    app.router.add_post('/api/council/room/register', handle_council_room_register)
    app.router.add_get('/api/council/room/{rid}/status', handle_council_room_status)
    app.on_startup.append(_post_call_accounting_startup)
    app.on_startup.append(_council_cron_startup)
    app.on_cleanup.append(_council_cron_cleanup)
    app.on_cleanup.append(_executor_lanes_cleanup)
    app.on_cleanup.append(_durable_metrics_cleanup)
    app.on_cleanup.append(_durable_audit_cleanup)
    app.on_cleanup.append(_post_call_accounting_cleanup)
    app.router.add_post('/api/council/live-raw-clear', handle_council_live_raw_clear)
    app.router.add_get('/api/council/send', handle_council_send)
    app.router.add_get('/api/council/send/{frm}/{to}/{text:.*}', handle_council_send)
    app.router.add_post('/api/council/stop', handle_council_stop)
    app.router.add_post('/api/council/pause', handle_council_pause)
    app.router.add_post('/api/council/play', handle_council_play)
    app.router.add_post('/api/council/clear-session', handle_council_clear_session)
    app.router.add_post('/api/council/pick-folder', handle_council_pick_folder)
    app.router.add_post('/api/council/worker-config', handle_council_worker_config)
    app.router.add_get('/api/council/worker-config', handle_council_worker_config)
    app.router.add_get('/api/council/valid-models', handle_council_valid_models)
    app.router.add_get('/api/logs', handle_logs)
    app.router.add_get('/api/config', handle_config)
    app.router.add_get('/api/tools', handle_tools)
    app.router.add_get('/api/tools/names', handle_tools_names)  # R16.9 ultra-compact
    app.router.add_get('/api/tools/{tool_name}/schema', handle_tool_describe)  # R81 single-tool schema
    app.router.add_post('/api/call/{tool_name}', handle_call)
    # FixA2407: read-back + preflight surfaces reported missing by the CA audit.
    app.router.add_get('/api/result/{ref}', handle_result_ref)          # P2-2 result_ref reader
    app.router.add_get('/api/write_status', handle_write_status)        # P1-1 write commit truth
    app.router.add_get('/api/semaphores', handle_semaphores)
    app.router.add_post('/api/cancel', handle_cancel_operation)
    app.router.add_post('/api/native_preflight', handle_native_preflight)  # P2-1 pre-act gate
    app.router.add_post('/api/graph/query', handle_graph_query)      # Phase 1.5 B2 [Q] bridge
    app.router.add_post('/api/graph/context', handle_graph_context)  # Phase 1.5 B2 [Q] bridge
    # ── Session API ──
    app.router.add_get('/api/sessions',                          handle_list_sessions)
    app.router.add_delete('/api/sessions/closed',                 handle_clear_closed_sessions)
    app.router.add_get('/api/sessions/{session_id}/events',      handle_session_events)
    app.router.add_get('/api/sessions/{session_id}/export',      handle_session_export)
    app.router.add_get('/api/session/stream/{session_id}',       handle_session_stream)
    app.router.add_get('/api/matrix_log',                         handle_matrix_log)
    app.router.add_get('/api/ui_log',                             handle_ui_log_get)
    app.router.add_post('/api/ui_log',                            handle_ui_log_post)
    app.router.add_delete('/api/ui_log',                          handle_ui_log_clear)
    app.router.add_post('/api/open_core_rule',                    handle_open_core_rule)
    app.router.add_get('/api/token',                              handle_token)
    app.router.add_get('/api/token/refresh',                      handle_token_refresh)
    app.router.add_get('/api/token_panel',                        handle_token_panel)
    app.router.add_get('/api/protected_tools_status',              handle_protected_tools_status)
    app.router.add_get('/api/command_helper',                     handle_command_helper)
    app.router.add_get('/api/tool_inspector/{tool_name}',         handle_tool_inspector)
    # V15 R35 — workflow editor UI popup (HTML form panel, same port 8083)
    app.router.add_get('/ui/workflow_editor',                     handle_workflow_editor_ui)
    # V15 R36 — workflow MD/draft REST helpers (UI editor uses these)
    app.router.add_get('/api/workflow_md',                        handle_workflow_md_get)
    app.router.add_get('/api/workflow_draft',                     handle_workflow_draft_get)
    app.router.add_get('/api/workflow_active',                    handle_workflow_active_get)
    # V15 R42 — auto-token toggle (User opt-in trust mode)
    app.router.add_get('/api/config/auto_token_inject',           handle_auto_token_get)
    app.router.add_post('/api/config/auto_token_inject',          handle_auto_token_post)
    app.router.add_route('*', '/ui/' + 'cs' + 'lt_history',       handle_retired_round_endpoint)
    app.router.add_route('*', '/api/' + 'cs' + 'lt_history',      handle_retired_round_endpoint)
    app.router.add_route('*', '/api/' + 'cs' + 'lt/state',        handle_retired_round_endpoint)
    app.router.add_route('OPTIONS', '/{tail:.*}', handle_options)
    _assert_get_routes(app, ('/health', '/api/health', '/council', '/api/council/state'))
    return app

# ─── Entry point ──────────────────────────────────────────────────────────────


async def _run_boot_recovery() -> dict:
    """Run startup recovery behind a hard deadline and expose degraded truth."""
    global SERVER_READY, SERVER_STOPPING, SERVER_STARTUP_PHASE
    global SERVER_STARTUP_DEGRADED, SERVER_STARTUP_ERROR, SERVER_STARTUP_RECOVERY

    from orchestrator.tools.write.core import _boot_write_sweep

    timeout = max(
        0.01,
        float(os.environ.get("ORCHESTRATOR_BOOT_RECOVERY_TIMEOUT_SEC", "30")),
    )
    stop_grace = max(
        0.0,
        float(os.environ.get("ORCHESTRATOR_BOOT_RECOVERY_STOP_GRACE_SEC", "1")),
    )
    started = time.monotonic()
    stop_event = threading.Event()
    deadline = started + timeout
    SERVER_READY = False
    SERVER_STOPPING = False
    SERVER_STARTUP_PHASE = "boot_write_sweep"
    SERVER_STARTUP_DEGRADED = False
    SERVER_STARTUP_ERROR = ""

    task = asyncio.create_task(_boot_write_sweep(
        stop_event=stop_event,
        deadline_monotonic=deadline,
    ))
    status = "ok"
    sweep: dict = {}
    try:
        sweep = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        if sweep.get("ok") is False or (
            sweep.get("aborted") and sweep.get("ok") is not True
        ):
            status = "degraded"
            SERVER_STARTUP_ERROR = str(
                sweep.get("abort_reason") or sweep.get("error") or "boot recovery incomplete"
            )[:300]
    except asyncio.TimeoutError:
        status = "timeout"
        SERVER_STARTUP_ERROR = f"boot write recovery timed out after {timeout:g}s"
        stop_event.set()
        if stop_grace > 0:
            try:
                sweep = await asyncio.wait_for(asyncio.shield(task), timeout=stop_grace)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if sweep.get("ok"):
            status = "ok"
            SERVER_STARTUP_ERROR = ""
    except Exception as exc:
        status = "error"
        SERVER_STARTUP_ERROR = f"{type(exc).__name__}: {str(exc)[:240]}"
        stop_event.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    if status in {"timeout", "degraded"} and sweep.get("ok"):
        status = "ok"
        SERVER_STARTUP_ERROR = ""
    SERVER_STARTUP_DEGRADED = status != "ok"
    SERVER_STARTUP_PHASE = "ready_degraded" if SERVER_STARTUP_DEGRADED else "ready"
    SERVER_READY = True
    result = {
        "status": status,
        "duration_ms": round((time.monotonic() - started) * 1000.0, 1),
        "timeout_seconds": timeout,
        "stop_requested": stop_event.is_set(),
        "sweep": sweep,
    }
    SERVER_STARTUP_RECOVERY = result
    if SERVER_STARTUP_DEGRADED:
        logger.error("boot_write_sweep degraded: %s", result)
    else:
        logger.info("boot_write_sweep complete: %s", result)
    return result


async def _run_session_retention_prune() -> dict:
    """A22: prune sessions.db to the retention window, OFF the startup path.

    WHY here and not inside boot recovery: SERVER_STARTUP_DEGRADED was once tripped
    by boot exceeding 30s, so nothing that touches the DB may block readiness. This
    is scheduled with ``asyncio.create_task`` AFTER ``_run_boot_recovery`` has set
    SERVER_READY=True, and the DELETE runs on the SessionWriter's own thread via
    ``asyncio.to_thread`` -- the event loop is never blocked. Opt-in and fail-soft:
    a prune error is logged, never raised, so a retention hiccup can never take the
    server down. Set ORCHESTRATOR_SESSION_RETENTION_ON_BOOT=0 to skip entirely.

    Chosen over the alternatives (a periodic cron: the repo has no maintenance
    scheduler to hang it on; an [A] tool only: that leaves the DB to grow unbounded
    unless someone remembers to call it). ``prune_sessions_older_than`` is also
    exported so it can additionally be wired as an [A] tool later.
    """
    if os.environ.get("ORCHESTRATOR_SESSION_RETENTION_ON_BOOT", "1").strip().lower() in (
        "0", "false", "no", "off"
    ):
        return {"skipped": "disabled"}
    try:
        from orchestrator.session import store as _session_store

        stats = await asyncio.to_thread(_session_store.prune_sessions_older_than)
        logger.info("session retention prune complete: %s", stats)
        return stats
    except Exception as exc:  # fail-soft: retention must never crash the server
        logger.exception("session retention prune failed: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}


# ---------------------------------------------------------------------------
# .bak archive retention (A38-D, 2026-09-20)
#
# WHY this exists: every write_fix drops a sibling <file>.bak and nothing ever
# collected them -- 88 files / 4.35 MB had piled up across the worktree, one of
# them a 924 KB code_tools.py.bak. The .bak IS the auto-revert source, so a write
# cannot simply delete its own backup; the pile has to be aged out instead.
#
# TWO stages, deliberately separated so no single sweep both CHOOSES and
# DESTROYS (CASE-2008):
#   1. PROMOTE -- a .bak older than the write-ledger TTL is MOVED into
#      _recycle_bin/bak_archive/<relative path>. The threshold is DERIVED from
#      _WRITE_LEDGER_TTL_DAYS rather than invented: once the ledger row is gone,
#      no write_status or auto-revert path can still reach that backup, so it has
#      no rollback authority left to protect. A move loses nothing.
#   2. EXPIRE -- inside _recycle_bin ONLY, an entry older than
#      _BAK_ARCHIVE_TTL_DAYS is deleted, counted from ARCHIVAL (stage 1 stamps
#      the destination) so the two stages can never collapse into one pass.
#      Whitelisted twice: by ROOT (the resolved path must sit under the bin) and
#      by SUFFIX. A symlink/junction is skipped rather than followed, so the
#      sweep cannot be steered out of the bin.
# ---------------------------------------------------------------------------
_BAK_ARCHIVE_DIR = "_recycle_bin"
_BAK_ARCHIVE_SUBDIR = "bak_archive"
_BAK_ARCHIVE_TTL_DAYS = max(
    1, min(365, int(os.environ.get("ORCHESTRATOR_BAK_ARCHIVE_TTL_DAYS", "14")))
)
_BAK_ARCHIVE_SKIP_DIRS = (".git", ".orchestrator_cache", "BACKUP", _BAK_ARCHIVE_DIR)
_BAK_ARCHIVE_EXPIRE_SUFFIXES = (".bak", ".zip", ".tsv")


def _bak_archive_sweep(
    project_root: str, dry_run: bool = True, now: float | None = None
) -> dict:
    """Promote stale .bak files into _recycle_bin, then expire the bin past TTL.

    dry_run defaults to TRUE: a caller that forgets the flag gets a preview, not a
    deletion. The returned lists carry the exact chosen paths, so the preview IS
    the approval list instead of a count the caller has to take on trust.

    ``now`` is injectable so a test can age a file without sleeping 14 days.
    """
    now = time.time() if now is None else float(now)
    root = Path(project_root).resolve()
    bin_root = (root / _BAK_ARCHIVE_DIR).resolve()
    archive_root = bin_root / _BAK_ARCHIVE_SUBDIR
    out: dict = {
        "ok": True,
        "dry_run": bool(dry_run),
        "root": str(root),
        "archive_root": str(archive_root),
        "promote_after_days": _WRITE_LEDGER_TTL_DAYS,
        "expire_after_days": _BAK_ARCHIVE_TTL_DAYS,
        "promoted": [],
        "expired": [],
        "errors": [],
    }
    promote_cutoff = now - (_WRITE_LEDGER_TTL_DAYS * 86400)
    expire_cutoff = now - (_BAK_ARCHIVE_TTL_DAYS * 86400)
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [
                d for d in dirnames
                if d not in _BAK_ARCHIVE_SKIP_DIRS and not d.startswith("venv")
            ]
            for fn in filenames:
                if not fn.endswith(".bak"):
                    continue
                src = Path(dirpath) / fn
                try:
                    if src.is_symlink() or src.stat().st_mtime > promote_cutoff:
                        continue
                    rel = src.relative_to(root)
                    out["promoted"].append(str(rel))
                    if dry_run:
                        continue
                    dst = archive_root / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        # Keep the .bak suffix on a collision rename, or stage 2
                        # would never recognise the file as expirable again.
                        dst = dst.with_name(f"{dst.stem}.{int(now)}{dst.suffix}")
                    shutil.move(str(src), str(dst))
                    # The 14-day clock starts at ARCHIVAL, not at the original
                    # write: shutil.move preserves mtime, so without this stamp a
                    # .bak already older than the expire TTL would be promoted and
                    # deleted inside the SAME sweep -- one pass both choosing and
                    # destroying, which is exactly what stage separation forbids.
                    os.utime(dst, (now, now))
                except Exception as exc:
                    out["errors"].append(f"promote {fn}: {type(exc).__name__}: {exc}")
        if bin_root.is_dir():
            for dirpath, _dirnames, filenames in os.walk(bin_root, followlinks=False):
                for fn in filenames:
                    victim = Path(dirpath) / fn
                    try:
                        if victim.is_symlink():
                            continue
                        if not fn.endswith(_BAK_ARCHIVE_EXPIRE_SUFFIXES):
                            continue
                        real = victim.resolve()
                        if bin_root not in real.parents:
                            out["errors"].append(f"skip out-of-bin path: {real}")
                            continue
                        if real.stat().st_mtime > expire_cutoff:
                            continue
                        out["expired"].append(str(real))
                        if not dry_run:
                            real.unlink()
                    except Exception as exc:
                        out["errors"].append(f"expire {fn}: {type(exc).__name__}: {exc}")
    except Exception as exc:
        out["ok"] = False
        out["errors"].append(f"{type(exc).__name__}: {str(exc)[:240]}")
    out["promoted_count"] = len(out["promoted"])
    out["expired_count"] = len(out["expired"])
    return out


async def _run_bak_archive_retention() -> dict:
    """Run the .bak sweep off the startup path, fail-soft, opt-out by env.

    Mirrors _run_session_retention_prune: scheduled AFTER SERVER_READY, executed
    on a worker thread so a slow disk cannot block the loop, and every error is
    logged instead of raised. Set ORCHESTRATOR_BAK_RETENTION_ON_BOOT=0 to skip.
    """
    if os.environ.get("ORCHESTRATOR_BAK_RETENTION_ON_BOOT", "1").strip().lower() in (
        "0", "false", "no", "off"
    ):
        return {"skipped": "disabled"}
    try:
        stats = await asyncio.to_thread(
            _bak_archive_sweep, str(ct.path_runtime.ORCHESTRATOR_DIR), False
        )
        logger.info(
            "bak archive retention: promoted=%s expired=%s errors=%s",
            stats.get("promoted_count"),
            stats.get("expired_count"),
            len(stats.get("errors") or []),
        )
        return stats
    except Exception as exc:  # fail-soft: retention must never crash the server
        logger.exception("bak archive retention failed: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}

COUNCIL_TAB_ENV = "ORCHESTRATOR_OPEN_COUNCIL_TAB"


def _should_open_council_tab() -> bool:
    """A37: decide whether startup may pop the /council browser tab.

    OPT-IN by design. Returns True only when the launcher explicitly set
    ORCHESTRATOR_OPEN_COUNCIL_TAB=1. Any other value, or the var being absent,
    means "do not open".

    Why opt-in and not opt-out: the server is not only started by the real user
    via start_orchestrator.bat. It is also spawned as a plain subprocess by
    tests and CI — e.g. tests/test_task_epoch_cross_process.py runs a REAL
    server with subprocess.Popen([PYTHON, orchestrator_server.py]) on a random
    free port. With the old unconditional webbrowser.open() that child popped a
    real browser tab (http://localhost:<random>/council) onto the user's desktop;
    when the test tore the server down the tab turned into a dead page and looked
    like agent drift. If a NEW spawn site is added later and its author forgets
    about the browser, opt-in makes the safe outcome (no tab) the default; an
    opt-out design would instead punish that forgetfulness with a surprise tab.
    """
    return os.environ.get(COUNCIL_TAB_ENV) == "1"


def _maybe_open_council_tab() -> bool:
    """Open the /council tab iff opted in. Returns whether it opened."""
    if not _should_open_council_tab():
        return False
    webbrowser.open(f"http://localhost:{PORT}/council")
    return True


async def _run_server():
    global SERVER_READY, SERVER_STOPPING, SERVER_STARTUP_PHASE
    default_executor_workers = max(
        4, min(128, int(os.environ.get("ORCH_DEFAULT_EXECUTOR_WORKERS", "56"))))
    asyncio.get_running_loop().set_default_executor(
        concurrent.futures.ThreadPoolExecutor(
            max_workers=default_executor_workers, thread_name_prefix="orch-default"))
    logger.info("Default asyncio executor workers=%s", default_executor_workers)
    # B1 (A_CSLT v3.1): single-instance guard — acquire BEFORE binding the port.
    # Reaper/mission tools (P3c) must additionally check is_lock_holder().
    from orchestrator.mission.instance_lock import acquire_instance_lock
    _lock_res = acquire_instance_lock(
        Path(os.environ.get("ORCHESTRATOR_INSTANCE_LOCK") or
             (_HERE / ".orchestrator_cache" / "orchestrator.lock")), PORT,
        wait_for_stale_sec=15.0,  # restart race: old holder mid-kill by relaunch pipeline
    )
    if not _lock_res.get("ok"):
        logger.critical(
            "ORCHESTRATOR_ALREADY_RUNNING: holder_pid=%s holder_port=%s — refusing to start",
            _lock_res.get("holder_pid"), _lock_res.get("holder_port"),
        )
        raise SystemExit(3)
    _generation_publish = _publish_server_generation()
    if not _generation_publish.get("ok"):
        logger.error("daemon generation publish failed: %s", _generation_publish)
    livez_server = _start_liveness_listener()
    _start_restart_snapshot_publisher()
    logger.info("Thread-isolated liveness: http://127.0.0.1:%s/livez", LIVEZ_PORT)
    from orchestrator.mission import reaper as _mission_reaper
    from orchestrator.mission.tools import MISSION_DB_PATH as _MISSION_DB_PATH
    _reaper_task = _mission_reaper.start_reaper_task(
        _MISSION_DB_PATH,
        kill_process_tree=_council_kill_process_tree,
    )
    # Pass no interval: the monitor now derives it from COUNCIL_MONITOR_INTERVAL_SEC
    # (default 60s). The old hardcoded 600 meant a stalled worker could go unnoticed for
    # ten minutes ON TOP of the silence window, which is why a dead dispatch looked
    # indistinguishable from a slow one.
    _council_monitor_task = asyncio.create_task(_council_runtime_monitor())
    app = create_app()
    runner = web.AppRunner(app, access_log_class=_DashboardAccessLogger)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', PORT)
    SERVER_READY = False
    SERVER_STOPPING = False
    SERVER_STARTUP_PHASE = "boot_write_sweep"
    await site.start()
    # BROKER-RETRY-AFTER-SUCCESS (F2): ensure the dedup cache dir exists at startup
    # (the cache write helper also mkdirs lazily; this is the documented mkdir-once).
    try:
        _council_completed_tasks_dir().mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    await _run_boot_recovery()
    # A22: sessions.db retention runs AFTER boot recovery so it never counts toward
    # the startup deadline; the task drives DELETEs on the writer thread off-loop.
    _session_retention_task = asyncio.create_task(_run_session_retention_prune())
    # A38-D: same shape for the .bak pile -- promote stale backups into
    # _recycle_bin, expire the bin past its TTL, both off the startup path.
    _bak_retention_task = asyncio.create_task(_run_bak_archive_retention())
    logger.info(f"Orchestrator server: http://localhost:{PORT}")
    logger.info(
        "Runtime paths | app_root=%s | orchestrator_dir=%s | core_rule_path=%s | start_script=%s",
        path_runtime.APP_ROOT,
        path_runtime.ORCHESTRATOR_DIR,
        CORE_RULE_FILE,
        path_runtime.ORCHESTRATOR_DIR / 'start_orchestrator.bat',
    )
    _maybe_open_council_tab()
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Server stopping...")
    finally:
        SERVER_STOPPING = True
        _stop_restart_snapshot_publisher()
        SERVER_READY = False
        SERVER_STARTUP_PHASE = "stopping"
        background = await _cancel_and_await_background_tasks({
            "council_monitor": _council_monitor_task,
            "mission_reaper": _reaper_task,
            "session_retention": _session_retention_task,
        }, timeout=max(0.0, float(os.environ.get("ORCH_BACKGROUND_DRAIN_TIMEOUT", "5"))))
        if not all(row.get("done") for row in background.values()):
            logger.error("background task shutdown incomplete: %s", background)
        try:
            await runner.cleanup()
        finally:
            try:
                if _DURABLE_AUDIT is None:
                    logger.warning("Durable audit not initialized; skipping shutdown")
                    durable_shutdown = None
                else:
                    durable_shutdown = await asyncio.to_thread(
                        _DURABLE_AUDIT.shutdown,
                        10.0,
                        drain=True,
                    )
                if durable_shutdown is not None and not durable_shutdown.ok:
                    logger.critical(
                        "Durable audit process shutdown incomplete pending=%s "
                        "worker_stopped=%s timed_out=%s storage_error=%s",
                        durable_shutdown.pending,
                        durable_shutdown.worker_stopped,
                        durable_shutdown.timed_out,
                        durable_shutdown.storage_error,
                    )
            except Exception:
                logger.exception("Durable audit process shutdown failed")
            finally:
                livez_server.shutdown()
                livez_server.server_close()


if __name__ == "__main__":
    import sys
    _council_ensure_session_meta(_HERE / ".council")
    if '--mcp' in sys.argv:
        from orchestrator.server.mcp_stdio import run_mcp_stdio
        try:
            run_mcp_stdio()
        finally:
            try:
                if _DURABLE_AUDIT is None:
                    logger.warning("Durable audit not initialized; skipping MCP shutdown")
                    _mcp_durable_report = None
                else:
                    _mcp_durable_report = _DURABLE_AUDIT.shutdown(10.0, drain=True)
                if _mcp_durable_report is not None and not _mcp_durable_report.ok:
                    logger.critical(
                        "MCP durable audit cleanup incomplete pending=%s "
                        "worker_stopped=%s timed_out=%s storage_error=%s",
                        _mcp_durable_report.pending,
                        _mcp_durable_report.worker_stopped,
                        _mcp_durable_report.timed_out,
                        _mcp_durable_report.storage_error,
                    )
            except Exception:
                logger.exception("MCP durable audit cleanup failed")
            finally:
                _mcp_accounting_report = _POST_CALL_ACCOUNTING.close(10.0)
                if not _mcp_accounting_report.get("drained"):
                    logger.critical(
                        "MCP accounting cleanup timed out pending=%s",
                        _mcp_accounting_report.get("pending"),
                    )
    else:
        asyncio.run(_run_server())
