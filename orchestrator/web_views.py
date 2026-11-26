"""Packaged ToolA pages and the loopback-only Monitor API."""

import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from aiohttp import web

from orchestrator.core.params import csv_param

_LOG = logging.getLogger(__name__)
UI_DIR = Path(__file__).resolve().with_name("web_ui")
_NAV_LINKS = (("main", "/", "Main"), ("council", "/council", "Council"),
              ("monitor", "/monitor", "Monitor"), ("matrix", "/matrixtool", "Matrix"))
_FRONTENDS = {"monitor": "monitor.html", "matrix": "matrix.html"}
_ASSETS = {"theme.css": "text/css", "matrix.js": "text/javascript", "d3.min.js": "text/javascript"}
_QUERY_RULES = {
    "activity": {"since_seq": 1, "boot_id": 1},
    "health": {},
    "graph": {"repo": 1},
    "history": {key: 1 for key in ("from", "to", "agent", "tool", "limit", "q", "before", "before_aid")},
    "evdetail": {"id": 200},
}
_HOST = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?", re.I)
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_MAX_BYTES = 16 * 1024 * 1024
_NO_CACHE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def decorate_page(html, page):
    """Leave each document and its scripts in charge of its own layout."""
    links = []
    for key, href, label in _NAV_LINKS:
        current = ' aria-current="page"' if key == page else ""
        links.append(f'<a href="{href}"{current}>{label}</a>')
    nav = '<nav class="toola-nav" aria-label="ToolA"><span class="toola-nav-mark">TOOL A</span>' + "".join(links) + "</nav>"
    html = re.sub(r"<html\b", f'<html data-toola-page="{page}"', html, count=1)
    html = html.replace('</head>', '<link rel="stylesheet" href="/assets/toola/theme.css">\n</head>', 1)
    if '<!-- TOOLA_NAV -->' in html:
        return html.replace('<!-- TOOLA_NAV -->', nav, 1)
    head_end = html.index("</head>")
    return html[:head_end] + re.sub(r"<body\b[^>]*>", lambda m: m[0] + "\n" + nav,
                                     html[head_end:], count=1)

def host_ok(request):
    """Host must name loopback, and an explicit port must be the port we bound.

    The bound port is read from the accepted socket, never from a constant: the
    packaged tests run on a TestClient whose ephemeral port changes every session,
    so pinning 8083 would fail them all. A request with no real socket
    (make_mocked_request) yields no numeric port; there the hostname alone is what
    can honestly be asserted, so the check stays open rather than inventing a port.
    """
    if not _HOST.fullmatch(request.host):
        return False
    _, _, declared = request.host.rpartition(":")
    if not declared.isdigit():
        return True
    try:
        sockname = request.transport.get_extra_info("sockname")
    except Exception:
        return True
    if (isinstance(sockname, (tuple, list)) and len(sockname) >= 2
            and isinstance(sockname[1], int)):
        return sockname[1] == int(declared)
    return True


def _require_loopback(request):
    # Host is checked even when Origin is absent (e.g. address-bar navigation).
    if (request.remote not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
            or not host_ok(request)
            or request.headers.get("Sec-Fetch-Site") not in {None, "none", "same-origin"}):
        raise web.HTTPForbidden()
    origin = request.headers.get("Origin")
    if origin is not None and origin != f"{request.scheme}://{request.host}":
        raise web.HTTPForbidden()


async def _serve_page(request, page):
    _require_loopback(request)
    html = (UI_DIR / _FRONTENDS[page]).read_text(encoding="utf-8")
    return web.Response(text=decorate_page(html, page), content_type="text/html", headers=_NO_CACHE)


async def monitor_page(request):
    return await _serve_page(request, "monitor")


async def matrix_page(request):
    return await _serve_page(request, "matrix")


async def _asset(request):
    _require_loopback(request)
    name = request.match_info["name"]
    if name not in _ASSETS:
        raise web.HTTPNotFound()
    return web.FileResponse(UI_DIR / name, headers={**_NO_CACHE, "Content-Type": _ASSETS[name]})


def _parameters(request, endpoint):
    counts = dict.fromkeys(_QUERY_RULES[endpoint], 0)
    params = []
    for key, value in request.query.items():
        if key not in counts or len(value) > 512:
            raise web.HTTPBadRequest()
        counts[key] += 1
        if counts[key] > _QUERY_RULES[endpoint][key]:
            raise web.HTTPBadRequest()
        if key == "id" and not _IDENTIFIER.fullmatch(value):
            raise web.HTTPBadRequest()
        if key == "repo" and value not in {"toola", "toolq"}:
            raise web.HTTPBadRequest()
        params.append((key, value))
    return params


def _unavailable(status):
    return web.json_response({"ok": False, "error_name": "MONITOR_BRIDGE_UNAVAILABLE"},
                             status=status, headers=_NO_CACHE)


def _internal():
    """A store fault is not an upstream fault. Naming it MONITOR_BRIDGE_UNAVAILABLE
    sent every SQLite or serialisation error to the operator as a dead bridge, so
    the diagnosis pointed at a service that no longer exists.
    """
    return web.json_response({"ok": False, "error_name": "MONITOR_INTERNAL_ERROR"},
                             status=500, headers=_NO_CACHE)


SECTIONS = ("services", "events", "sessions", "writes", "toolq")
TTL = {"services": 5.0, "events": 2.0, "sessions": 2.0, "writes": 2.0, "toolq": 10.0}
STATE_GRACE = 2.5


def _parse_allowlist(raw):
    allowed = set()
    # A34: the CA-facing csv value goes through csv_param (strip + drop
    # empties, never raises), not a raw .split(",") on the parameter.
    for entry in csv_param(raw):
        parts = entry.split(":")
        if len(parts) != 2:
            _LOG.warning("ignoring malformed allowlist entry: %r" % entry)
            continue
        tool = parts[0].strip()
        field = parts[1].strip()
        if not tool or not field:
            _LOG.warning("ignoring malformed allowlist entry: %r" % entry)
            continue
        allowed.add((tool, field))
    return allowed


def trim(v, n=220):
    if isinstance(v, str): return v if len(v) <= n else v[:n] + "…(" + str(len(v)) + "ch)"
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:12]:
            if k.startswith("_") and k != "_task_agent_id": continue
            out[k] = trim(x, 140)
        return out
    if isinstance(v, list): return [trim(x, 80) for x in v[:6]] + (["…+" + str(len(v) - 6)] if len(v) > 6 else [])
    return v

def iso(ts):
    try: return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except Exception: return None


def _level_reason(name, out):
    rules = []
    if name == "ToolA":
        if out.get("ready") is False:
            rules.append(("warn", "ready false"))
        q = out.get("queue_depth")
        if q is not None and q > 10:
            rules.append(("warn", "queue_depth >10"))
        p = out.get("p95_ms")
        if p is not None and p > 2000:
            rules.append(("warn", "p95_ms >2000"))
        if out.get("ready") is None:
            rules.append(("unknown", "ready thiếu"))
    else:
        st = out.get("status")
        sr = out.get("semantic_ready")
        if st in ("error", "down"):
            rules.append(("crit", "status " + st))
        if sr is False:
            rules.append(("warn", "semantic_ready false"))
        if st == "degraded":
            rules.append(("warn", "status degraded"))
        if sr is None:
            rules.append(("unknown", "semantic_ready thiếu"))
        if st is None or st not in ("ok", "healthy", "degraded", "error", "down"):
            rules.append(("unknown", "status không nhận dạng được: %s" % (st,)))
    if rules:
        level = rules[0][0]
        reason = "; ".join(r for (lv, r) in rules if lv == level)
        return level, reason
    return "ok", ""


def _missing_fields(name, out):
    expected = ["pid", "generation", "uptime_s", "p95_ms", "queue_depth", "executor_active", "accounting_in_flight", "ready"] if name == "ToolA" else ["status", "semantic_ready", "rows"]
    return [k for k in expected if out.get(k) is None]

_SCHEMA_TOOLA_REQUIRED = ("tool_name", "session_id", "status")
_SCHEMA_TOOLA_EXPECTED = ("duration_ms", "_task_agent_id")
_SCHEMA_TOOLQ_RETRIEVAL = ("id", "query", "rank", "used", "ts")
_SCHEMA_TOOLQ_DISTILL = ("id", "started_at", "finished_at", "raw_seen", "points_created", "mode")
_SCHEMA_HEALTH_TOOLA_REQUIRED = ("pid", "generation", "uptime_s", "p95_ms", "queue_depth", "executor_active_total", "accounting_in_flight", "ready", "restart_controller")
# v20 (2026-11-19 Anh lenh): tach REQUIRED (missing-check) khoi KNOWN (unknown-check).
# KNOWN = 82 key LIVE do truc tiep /health ToolA ngay 19/11 + "_down" (marker noi bo).
# Truoc day 1 tuple 9 key vua missing vua unknown -> 73 key hop le bi bao drift.
_SCHEMA_HEALTH_TOOLA_KNOWN = frozenset(_SCHEMA_HEALTH_TOOLA_REQUIRED) | frozenset((
    "session_writer", "ok", "startup_phase", "server_stopping", "startup_degraded", "startup_error", "startup_recovery",
    "service", "port", "started_at", "mcp_generation", "field_notes", "livez_protocol", "livez_url",
    "restart_busy_snapshot_alive", "restart_busy_snapshot_reap", "base_url_hint", "bind_host", "environment_note",
    "queue_depth_semantics", "executor_queued_total", "executor_oldest_active_age_ms", "executor_accepting", "executor_lanes",
    "accounting_ok", "accounting_queue_depth", "accounting_queue_depth_semantics", "accounting_queue_capacity",
    "accounting_queue_high_watermark", "accounting_enqueued_total", "accounting_processed_total", "accounting_failed_total",
    "accounting_rejected_total", "accounting_degraded", "accounting_last_error", "accounting_oldest_age_ms", "accounting_worker_alive",
    "call_cache_entries", "call_cache_inflight", "call_cache_joins_waited_total", "call_cache_joins_waited_semantics",
    "durable_audit_ok", "durable_audit_pending", "durable_audit_applied", "durable_audit_queue_capacity",
    "durable_audit_staged_total", "durable_audit_duplicate_total", "durable_audit_applied_total", "durable_audit_apply_failures",
    "durable_audit_capacity_rejections", "durable_audit_admission_open", "durable_audit_worker_alive", "durable_audit_active_leases",
    "durable_audit_expired_leases", "durable_audit_lease_claimed_total", "durable_audit_lease_recovered_total",
    "durable_audit_next_retry_at", "durable_audit_metrics_age_ms", "durable_audit_metrics_stale", "durable_audit_last_error",
    "durable_audit_storage_busy_failures", "durable_audit_busy_retries", "durable_audit_id_conflicts_total",
    "durable_audit_spool_depth", "durable_audit_busy_timeout_ms", "durable_audit_db_retry_attempts",
    "auto_token_inject", "mutation_token_required", "auth_hint", "tool_compatibility_unknown_total",
    "listener", "health_view", "ca_hint", "_down",
))
_SCHEMA_HEALTH_TOOLQ_REQUIRED = ("status", "semantic_ready")
_SCHEMA_HEALTH_TOOLQ_KNOWN = frozenset(("ok", "status", "service", "pid", "port", "uptime_s", "semantic_ready", "semantic_error", "semantic_snapshot", "queue", "distill", "version", "_down"))


def _ts_ok(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v)
            return True
        except Exception:
            pass
        try:
            datetime.fromisoformat(v[:-1] + "+00:00" if v.endswith("Z") else v)
            return True
        except Exception:
            return False
    return False


def _toola_extra(h):
    d = {"pid": h.get("pid"), "generation": h.get("generation"), "uptime_s": h.get("uptime_s"),
         "p95_ms": h.get("p95_ms"), "queue_depth": h.get("queue_depth"), "executor_active": h.get("executor_active_total"),
         "accounting_in_flight": h.get("accounting_in_flight"), "ready": h.get("ready")}
    rc = h.get("restart_controller")
    if rc is not None:
        d["restart_controller"] = rc
    return d


_B64_CLEAN_RE = re.compile(r"\s+")
_B64_BODY_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def _decode_b64_text(s):
    if not isinstance(s, str):
        return s
    clean = _B64_CLEAN_RE.sub("", s)
    if len(clean) < 16 or len(clean) > 65536 or not _B64_BODY_RE.match(clean):
        return s
    if len(clean) % 4 == 0:
        try:
            raw = base64.b64decode(clean, validate=True)
            txt = raw.decode("utf-8")
            if base64.b64encode(raw).decode("ascii") == clean:
                return txt
        except Exception:
            pass
    # v20 salvage (do offline 19/11/2026): ToolA audit outbox luu stdout da bi
    # cat thanh 400 ky tu CUOI cua chuoi base64 (mat dau, giu padding '=') ->
    # fragment khong hop le (len%4 != 0), khong decode nguyen duoc. Thu drop
    # 1-3 ky tu dau de ve boundary 4; phan con lai decode ra DUNG cuoi text
    # goc (preview tail). Chi salvage khi co padding '=' (dau hieu b64 that,
    # tranh hex/ngau nhien); bo replacement char o dau neu cat mid-UTF8.
    if clean.endswith("="):
        for drop in (1, 2, 3):
            frag = clean[drop:]
            if len(frag) % 4 != 0:
                continue
            try:
                raw = base64.b64decode(frag, validate=True)
                txt = raw.decode("utf-8", errors="replace")
            except Exception:
                continue
            txt = txt.lstrip("\ufffd")
            if txt and sum(ch.isprintable() or ch.isspace() for ch in txt) >= 0.9 * len(txt):
                return txt
    return s


def _event_tgt(body, rich):
    """V47 (C16-06, R16): MOT helper _tgt cho feed/pending/history — cung event
    hien cung dich tren moi be mat. Thu tu uu tien GIU NGUYEN cua feed cu
    (file_path > path > query > project_path); rr.target cua events chi la
    fallback khi body khong co truong nao (history tung uu tien target truoc
    -> lech 236/240 id). Cat 64 ky tu nhu cu."""
    _b = body or {}
    v = _b.get("file_path") or _b.get("path") or _b.get("query") or _b.get("project_path") or ""
    if not v and rich:
        v = rich.get("target") or ""
    return str(v)[:64]


def _tq_rollup(perr):
    failing = [p for p in ("retrievals", "distill_runs") if perr.get(p) is not None]
    if not failing:
        return None
    order = {"retrievals": 0, "distill_runs": 1}
    worst = max(failing, key=lambda p: ((perr[p] or {}).get("at") or "", order[p]))
    return perr[worst]


def _err(code, msg):
    return {"code": code, "message": str(msg)[:140], "at": datetime.now().isoformat(timespec="seconds")}


def _section_state(built_at, age, error, ttl):
    if built_at is None or age is None:
        return "never"
    if age > 300:
        return "expired"
    if error is not None or age > ttl + STATE_GRACE:
        return "stale"
    return "fresh"


def _stable_event(e):
    return {"id": str(e.get("id")), "state": e.get("state"), "ts": e.get("ts"), "epoch": e.get("epoch"), "tool": e.get("tool"), "session": e.get("session"), "agent": e.get("agent"), "status": e.get("status"), "dur_ms": e.get("dur_ms"), "err": e.get("err"), "tgt": e.get("tgt")}


def _dclip(v, n=200):
    if isinstance(v, str):
        s = v
    else:
        try:
            s = json.dumps(v, ensure_ascii=False, default=str)
        except Exception:
            s = str(v)
    return s if len(s) <= n else s[:n] + "\u2026"


def _detail_args(body):
    out = {}
    for k, v in (body or {}).items():
        if isinstance(v, (dict, list)):
            try:
                v = json.dumps(v, ensure_ascii=False, default=str)
            except Exception:
                v = str(v)
        out[k] = _dclip(v, 160)
    return out


class _MonitorStore:
    """Monitor snapshot and query logic adapted from 63. MONITOR/bridge.py.

    One executor thread updates the store and owns its SQLite connections.
    Health reads the last published snapshot on the event loop.
    """

    def __init__(self):
        root = UI_DIR.parents[1]
        cache = Path(os.environ.get("MONITOR_TOOLA_CACHE") or root / ".orchestrator_cache")
        sessions_override = os.environ.get("ORCHESTRATOR_SESSIONS_DB", "").strip()
        self.sessions_db = Path(sessions_override or cache / "sessions.db")
        self.audit_db = Path(os.environ.get("ORCHESTRATOR_DURABLE_AUDIT_DB", "").strip()
                             or self.sessions_db.with_name("durable_audit.db"))
        self.registry = Path(os.environ.get("MONITOR_TOOLA_REGISTRY") or cache.parent / "agents_registry.json")
        self.toolq_db = Path(os.environ.get("MONITOR_TOOLQ_DB") or root.parent.parent / "21. ToolQ" / "librarian.db")
        graph_dir = Path(os.environ.get("MONITOR_GRAPH_DIR") or root.parent.parent / "55. GRAPHITY" / "graphs")
        self.graphs = {repo: graph_dir / repo / "graphify-out" / "graph.json" for repo in ("toola", "toolq")}
        self.toola_health = f"http://localhost:{int(os.environ.get('ORCHESTRATOR_PORT', '8083'))}/health"
        self.toolq_health = "http://127.0.0.1:8082/health"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.allowlist = _parse_allowlist(os.environ.get("MONITOR_SCHEMA_ALLOWLIST", ""))
        self.connections = {}
        self.health_cache = {"t": 0.0, "a": None, "q": None}
        self.graph_cache = {}
        self.schema = {"events": [], "toolq": [], "services": {"ToolA": [], "ToolQ": []}}
        self.schema_stats = {}
        self.sources_last_ok = {}
        self.boot_id = f"{os.getpid()}-{time.time_ns()}"
        self.sequence = 0
        self.cache = {name: {"data": None, "built_at": None, "error": None,
                             "counter": 0, "last_ok_at": None} for name in SECTIONS}
        self.last_attempt = {}
        self.poller_errors = {"consecutive": 0, "total": 0, "last_error": None, "last_error_at": None}
        self.error_state = {"section_error": dict.fromkeys(SECTIONS), "build_counter": dict.fromkeys(SECTIONS, 0),
                            "poller_errors": dict(self.poller_errors), "toolq_part_errors": {}}
        self.pending = {"list": [], "count": 0, "oldest": None}
        self.journal = {}
        self.sequence_times = {}
        self.previous = {"feed": {}}
        self.detail_cache = {}
        self.snapshot = {"data": self._empty_shape()}

    def rows(self, db, sql, args=()):
        try:
            connection = self.connections.get(db)
            if connection is None:
                connection = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
                # CONN-AUDIT: a new production connect must be finally-managed.
                # The cache entry is long-lived (closed by close() and by the
                # error path below); this finally closes the setup-failure case.
                registered = False
                try:
                    connection.execute("PRAGMA query_only=ON")
                    connection.row_factory = sqlite3.Row
                    self.connections[db] = connection
                    registered = True
                finally:
                    if not registered:
                        connection.close()
            return [dict(row) for row in connection.execute(sql, args)]
        except (sqlite3.Error, OSError) as exc:
            connection = self.connections.pop(db, None)
            if connection is not None:
                connection.close()
            return [{"_error": str(exc)[:140]}]

    def close(self):
        for connection in self.connections.values():
            connection.close()
        self.connections.clear()

    def get_json(self, url):
        try:
            with self.opener.open(url, timeout=1.5) as response:
                value = json.load(response)
            if isinstance(value, dict):
                return value
            return {"_down": True, "_err": "invalid response"}
        except (OSError, ValueError) as exc:
            return {"_down": True, "_err": type(exc).__name__}

    def tick(self, sections=SECTIONS):
        first_error = None
        updated = False
        for name in sections:
            now = time.time()
            attempt = time.monotonic()
            if attempt - self.last_attempt.get(name, -math.inf) < TTL[name]:
                continue
            self.last_attempt[name] = attempt
            updated = True
            slot = self.cache[name]
            if name == "toolq":
                try:
                    data, errors = self.toolq_activity()
                except Exception as exc:
                    data = {}
                    errors = dict.fromkeys(("retrievals", "distill_runs"), _err("exception", exc))
                parts = slot.setdefault("parts", {})
                merged = dict(slot["data"] or {})
                for part, error in errors.items():
                    previous = parts.get(part, {})
                    parts[part] = {"built_at": previous.get("built_at") if error else now, "error": error}
                    if error is None:
                        merged[part] = data[part]
                    else:
                        merged.setdefault(part, None)
                times = [part["built_at"] for part in parts.values() if part["built_at"] is not None]
                slot.update(data=merged, built_at=min(times) if times else None)
                error = _tq_rollup(errors)
            else:
                data, error = self._build_section(name)
                if error is None:
                    slot.update(data=data, built_at=now)
            if error is None:
                slot["last_ok_at"] = datetime.now().isoformat(timespec="seconds")
            elif first_error is None:
                first_error = error
            slot.update(error=error, counter=slot["counter"] + 1)
        if not updated:
            return
        if first_error is None:
            self.poller_errors["consecutive"] = 0
        else:
            self.poller_errors["consecutive"] += 1
            self.poller_errors["total"] += 1
            self.poller_errors["last_error"] = first_error
            self.poller_errors["last_error_at"] = datetime.now().isoformat(timespec="seconds")
        self.error_state = {
            "section_error": {name: slot["error"] for name, slot in self.cache.items()},
            "build_counter": {name: slot["counter"] for name, slot in self.cache.items()},
            "poller_errors": dict(self.poller_errors),
            "toolq_part_errors": {part: slot["error"] for part, slot in self.cache["toolq"].get("parts", {}).items()},
        }
        self.sequence += 1
        snapshot = self.build_snapshot()
        self._record_journal(snapshot)
        # Publish data and health metadata together; readers never see a partial tick.
        self.snapshot = {"data": snapshot, "last_error": self.poller_errors["last_error"]}

    def dispatch(self, path, params):
        status = 200
        query = dict(params)
        if path == "activity":
            # Refresh due feed data on the first request after the idle interval.
            self.tick(("events", "sessions"))
            payload = self._activity_payload({key: value for key, value in params if value})
        elif path == "health":
            payload = self._health_payload()
        elif path == "graph":
            payload = self.file_graph(query.get("repo", "toola"))
        elif path == "evdetail":
            payload = self.event_details([value for _, value in params])
        elif path == "history":
            def number(key, cast, default):
                try:
                    value = cast(query.get(key, ""))
                    return value if math.isfinite(value) else default
                except (ValueError, OverflowError):
                    return default
            payload = self.history_events(
                number("from", float, 0.0), number("to", float, 0.0) or time.time(),
                query.get("agent") or None, query.get("tool") or None, number("limit", int, 500),
                q=query.get("q") or None, before=query.get("before") or None,
                before_aid=query.get("before_aid") or None,
            )
        else:
            status, payload = self._event_detail(path.removeprefix("events/"))
        return status, json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _schema_note(self, audit_id, p):
        if audit_id in self.schema_stats:
            return
        res = p.get("result")
        res_d = res if isinstance(res, dict) else {}
        tool = p.get("tool_name")
        if not isinstance(tool, str) or not tool:
            tool = "unknown"
        self.schema_stats[audit_id] = (tool, p, res_d)


    def _warn(self, field, scope, kind, n):
        if (scope, field) in self.allowlist:
            return None
        return {"field": field, "scope": scope, "kind": kind, "sample_count": n}


    def _toola_row_warnings(self, stats):
        by_tool = {}
        for tool, p, res_d in stats.values():
            by_tool.setdefault(tool, []).append((p, res_d))
        out = []
        for tool, rows in by_tool.items():
            n = len(rows)
            if n < 5:
                continue
            miss = {"tool_name": 0, "session_id": 0, "status": 0, "duration_ms": 0, "_task_agent_id": 0}
            typ = {"duration_ms": 0}
            for p, res_d in rows:
                v = p.get("tool_name")
                if not isinstance(v, str) or not v:
                    miss["tool_name"] += 1
                if p.get("session_id") is None:
                    miss["session_id"] += 1
                if p.get("status") is None:
                    miss["status"] += 1
                if "duration_ms" not in p:
                    miss["duration_ms"] += 1
                elif p.get("duration_ms") is not None and (isinstance(p.get("duration_ms"), bool) or not isinstance(p.get("duration_ms"), (int, float))):
                    typ["duration_ms"] += 1
                if res_d.get("_task_agent_id") is None:
                    miss["_task_agent_id"] += 1
            for f, c in miss.items():
                if c == n:
                    w = self._warn(f, tool, "missing", n)
                    if w:
                        out.append(w)
            for f, c in typ.items():
                if c == n:
                    w = self._warn(f, tool, "type", n)
                    if w:
                        out.append(w)
        return out


    def _toolq_row_warnings(self, data):
        out = []
        for table, fields in (("retrievals", _SCHEMA_TOOLQ_RETRIEVAL), ("distill_runs", _SCHEMA_TOOLQ_DISTILL)):
            rows = (data or {}).get(table)
            if not isinstance(rows, list):
                continue
            n = len(rows)
            if n < 5:
                continue
            for f in fields:
                miss = 0
                typ = 0
                for r in rows:
                    if not isinstance(r, dict):
                        miss += 1
                        continue
                    if r.get(f) is None:
                        miss += 1
                    elif f == "ts" and not _ts_ok(r.get("ts")):
                        typ += 1
                if miss == n:
                    w = self._warn(f, "toolq", "missing", n)
                elif typ == n:
                    w = self._warn(f, "toolq", "type", n)
                else:
                    w = None
                if w is not None:
                    out.append(w)
        best = {}
        for w in out:
            k = (w["field"], w["scope"], w["kind"])
            if k not in best or w["sample_count"] > best[k]["sample_count"]:
                best[k] = w
        return list(best.values())


    def _health_warnings(self, a, q):
        out = {"ToolA": [], "ToolQ": []}
        if isinstance(a, dict) and not a.get("_down"):
            for f in _SCHEMA_HEALTH_TOOLA_REQUIRED:
                if a.get(f) is None:
                    w = self._warn(f, "ToolA", "missing", 1)
                    if w:
                        out["ToolA"].append(w)
            # v20 (2026-11-19 Anh lenh): unknown gom 1 warn/service (truoc: 1 warn/key -> 75 warn
            # ToolA + 14 ToolQ ngap chip). ALLOWLIST van hieu luc per-key: loc truoc khi gom.
            unk = [k for k in a if k not in _SCHEMA_HEALTH_TOOLA_KNOWN and ("ToolA", k) not in self.allowlist]
            if unk:
                label = "unknown_keys(" + ",".join(unk[:3]) + (" +%d" % (len(unk) - 3) if len(unk) > 3 else "") + ")"
                w = self._warn(label, "ToolA", "unknown", len(unk))
                if w:
                    out["ToolA"].append(w)
        if isinstance(q, dict) and not q.get("_down"):
            for f in _SCHEMA_HEALTH_TOOLQ_REQUIRED:
                if q.get(f) is None:
                    w = self._warn(f, "ToolQ", "missing", 1)
                    if w:
                        out["ToolQ"].append(w)
            snap = q.get("semantic_snapshot")
            if not isinstance(snap, dict) or snap.get("rows") is None:
                w = self._warn("semantic_snapshot.rows", "ToolQ", "missing", 1)
                if w:
                    out["ToolQ"].append(w)
            unk = [k for k in q if k not in _SCHEMA_HEALTH_TOOLQ_KNOWN and ("ToolQ", k) not in self.allowlist]
            if unk:
                label = "unknown_keys(" + ",".join(unk[:3]) + (" +%d" % (len(unk) - 3) if len(unk) > 3 else "") + ")"
                w = self._warn(label, "ToolQ", "unknown", len(unk))
                if w:
                    out["ToolQ"].append(w)
        return out


    def services(self):
        now = time.time()
        if now - self.health_cache["t"] > 2:
            self.health_cache["a"] = self.get_json(self.toola_health); self.health_cache["q"] = self.get_json(self.toolq_health); self.health_cache["t"] = now
        a = self.health_cache["a"] or {}; q = self.health_cache["q"] or {}
        self.schema["services"] = self._health_warnings(a, q)
        def svc(name, h, extra):
            if h.get("_down"): return {"name": name, "up": False, "level": "crit", "reason": "health không trả lời"}
            out = {"name": name, "up": True}; out.update(extra(h))
            out["level"], out["reason"] = _level_reason(name, out)
            out["missing_fields"] = _missing_fields(name, out)
            return out
        return [
            svc("ToolA", a, _toola_extra),
            svc("ToolQ", q, lambda h: {"status": h.get("status"), "semantic_ready": h.get("semantic_ready"),
                 "rows": (h.get("semantic_snapshot") or {}).get("rows")}),
        ]


    def _build_sources(self, slots):
        now_iso = datetime.now().isoformat(timespec="seconds")
        out = {}
        _wmap = {"toola_audit": self.schema["events"], "toolq": self.schema["toolq"]}
        for sname, sec in (("toola_audit", "events"), ("toola_sessions", "sessions"), ("toola_writes", "writes"), ("toolq", "toolq")):
            slot = slots[sec]
            err = slot["error"]
            out[sname] = {"ok": err is None, "error": (err or {}).get("message") if err else None, "last_ok_at": slot["last_ok_at"], "warnings": list(_wmap.get(sname, []))}
        sdict = {}
        svcs = slots["services"]["data"] or []
        for e in svcs:
            up = bool(e.get("up"))
            if up:
                self.sources_last_ok[e["name"]] = now_iso
            sdict[e["name"]] = {"ok": up, "error": None if up else "health kh\u00f4ng tr\u1ea3 l\u1eddi", "last_ok_at": self.sources_last_ok.get(e["name"]), "warnings": list(self.schema["services"].get(e["name"], []))}
        out["services"] = sdict
        return out


    def feed_events(self):
        ev = []
        self.schema_stats.clear()
        for state, lim in (("applied", 260), ("pending", 30)):
            rs = self.rows(self.audit_db, "SELECT audit_id, payload_json, state, created_at FROM durable_audit_outbox WHERE state=? ORDER BY created_at DESC, audit_id DESC LIMIT " + str(lim), (state,))
            for r in rs:
                if "_error" in r:
                    return None, _err("_error", r["_error"])
            for r in rs:
                try: p = json.loads(r["payload_json"])
                except Exception: continue
                res = p.get("result") or {}
                # v20 (2026-11-19 Anh lenh): decode base64 stdout/stderr TRUOC tail/trim (UI_AUDIT #7/#8).
                # stdout ToolA thuong la base64 cua text UTF-8; cat 400 ky tu cuoi chuoi base64
                # thi UI (V49) khong decode duoc -> preview rac. Copy res, khong dong payload goc.
                if isinstance(res, dict) and (res.get("stdout") or res.get("stderr")):
                    res = dict(res)
                    if res.get("stdout"):
                        res["stdout"] = _decode_b64_text(str(res["stdout"]))
                    if res.get("stderr"):
                        res["stderr"] = _decode_b64_text(str(res["stderr"]))
                self._schema_note(r["audit_id"], p)
                _tail = (str(res.get("stdout") or "") + str(res.get("stderr") or ""))[-400:]
                _b = p.get("body") or {}
                _tgt = _event_tgt(_b, None)  # V47: helper chung
                ev.append({"id": str(r["audit_id"]), "state": r["state"], "ts": iso(r["created_at"]), "epoch": r["created_at"],
                    "tool": p.get("tool_name"), "session": str(p.get("session_id") or ""),
                    "agent": res.get("_task_agent_id") or "unknown", "status": p.get("status"), "dur_ms": p.get("duration_ms"),
                    "err": (p.get("error_msg") or "")[:160], "tgt": _tgt or None,
                    "body": trim(_b), "result": trim(res), "out": _tail or None})
        ev.sort(key=lambda x: -(x.get("epoch") or 0))
        for i, e in enumerate(ev):
            if i >= 40:
                e.pop("body", None); e.pop("result", None); e.pop("out", None)
        ev = ev[:240]
        # B1: pending key (oldest-first, full id, display fields only; full payload via detail).
        prs = self.rows(self.audit_db, "SELECT audit_id, payload_json, state, created_at FROM durable_audit_outbox WHERE state=? ORDER BY created_at ASC, audit_id ASC LIMIT 100", ("pending",))
        for r in prs:
            if "_error" in r:
                return None, _err("_error", r["_error"])
        plist = []
        for r in prs:
            try: p = json.loads(r["payload_json"])
            except Exception: continue
            res = p.get("result") or {}
            self._schema_note(r["audit_id"], p)
            _b = p.get("body") or {}
            _tgt = _event_tgt(_b, None)  # V47: helper chung
            plist.append({"id": str(r["audit_id"]), "state": r["state"], "ts": iso(r["created_at"]), "epoch": r["created_at"],
                "tool": p.get("tool_name"), "session": str(p.get("session_id") or ""),
                "agent": res.get("_task_agent_id") or "unknown", "status": p.get("status"), "dur_ms": p.get("duration_ms"),
                "err": (p.get("error_msg") or "")[:160], "tgt": _tgt or None})
        ars = self.rows(self.audit_db, "SELECT COUNT(*), MIN(created_at) FROM durable_audit_outbox WHERE state=?", ("pending",))
        for r in ars:
            if "_error" in r:
                return None, _err("_error", r["_error"])
        count = 0
        oldest = None
        if ars:
            vals = list(ars[0].values())
            count = vals[0] if len(vals) > 0 else 0
            oldest = iso(vals[1]) if len(vals) > 1 and vals[1] is not None else None
        self.pending["list"] = plist
        self.pending["count"] = count
        self.pending["oldest"] = oldest
        _served = {x["id"] for x in ev} | {x["id"] for x in plist}
        self.schema["events"] = self._toola_row_warnings({k: v for k, v in self.schema_stats.items() if k in _served})
        return ev, None

    def registered_agents(self):
        """v19 (2026-11-18 Anh lenh): doc agents_registry.json (SOT vinh vien) -> list agent id da dang ky.
        Read-only, fail-soft -> []. Cho monitor hien DU CA dang ky ke ca idle (zcode bi prune khoi sessions 10 ngay)."""
        try:
            with open(self.registry, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
            agents = data.get("agents") or {}
            return sorted(str(k) for k in agents.keys())
        except Exception as e:
            _LOG.warning("registered_agents read fail: %s" % str(e)[:120])
            return []


    def active_sessions(self):
        # v18 (2026-11-17 Anh lenh + audit cheo che;o muse2+vito, root cause confirmed):
        #   LIMIT 30 top-recent -> anonymous adopted (council direct-*) lot top. Fix: roster
        #   top <=3 moi agent_id, GIU closed (opencode chi co session closed 11/14).
        rs = self.rows(self.sessions_db, """SELECT session_id, agent_id, session_title, mode, status, started_at FROM (
            SELECT session_id, agent_id, session_title, mode, status, started_at,
                   ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY started_at DESC) AS rn
            FROM sessions
            WHERE agent_id IS NOT NULL
        )
        WHERE rn <= 3
        ORDER BY started_at DESC""")
        for r in rs:
            if "_error" in r:
                return None, _err("_error", r["_error"])
        # V43 (C15-04): tra DU session_id/agent_id/title — cat hien thi la viec FE.
        return [dict(r) for r in rs], None

    def recent_writes(self):
        rs = self.rows(self.sessions_db, "SELECT tool, status, project_path, file_path, duration_ms, started_at FROM write_operations ORDER BY started_at DESC LIMIT 50")
        for r in rs:
            if "_error" in r:
                return None, _err("_error", r["_error"])
        return [{**r, "file_path": str(r.get("file_path") or "")[-90:]} for r in rs], None


    def toolq_activity(self):
        out_data = {}
        out_err = {}
        try:
            ret = self.rows(self.toolq_db, "SELECT id, substr(query,1,140) AS query, rank, used, ts FROM retrieval_log ORDER BY id DESC LIMIT 60")
        except Exception as e:
            ret = [{"_error": str(e)[:140]}]
        for r in ret:
            if "_error" in r:
                out_data["retrievals"] = None
                out_err["retrievals"] = _err("_error", r["_error"])
                break
        else:
            out_data["retrievals"] = ret
            out_err["retrievals"] = None
        try:
            runs = self.rows(self.toolq_db, "SELECT id, started_at, finished_at, raw_seen, points_created, mode FROM distill_runs ORDER BY id DESC LIMIT 8")
        except Exception as e:
            runs = [{"_error": str(e)[:140]}]
        for r in runs:
            if "_error" in r:
                out_data["distill_runs"] = None
                out_err["distill_runs"] = _err("_error", r["_error"])
                break
        else:
            out_data["distill_runs"] = runs
            out_err["distill_runs"] = None
        self.schema["toolq"] = self._toolq_row_warnings(out_data)
        return out_data, out_err


    def file_graph(self, repo, refresh=False):
        """File-level graph with mtime/size invalidation + hash-checked refresh.
        Cache entry: {data, mtime_ns, size, sha256, error}. Metadata updates ONLY
        on successful parse+build; failures keep old cache + old metadata and set
        graph_error {message, at}. No-cache failure returns exactly
        {error, graph_error, nodes: [], links: [], repo}. Cached responses add
        graph_mtime (stored mtime_ns) and graph_error iff set."""
        entry = self.graph_cache.get(repo)
        path = self.graphs[repo]
        try:
            st = os.stat(path)
        except FileNotFoundError:
            err = {"message": "file missing", "at": datetime.now().isoformat(timespec="seconds")}
            if entry is None:
                return {"error": "file missing", "graph_error": err, "nodes": [], "links": [], "repo": repo}
            self.graph_cache[repo] = dict(entry, error=err)
            out = dict(entry["data"])
            out["graph_mtime"] = entry["mtime_ns"]
            out["graph_error"] = err
            return out
        except OSError as e:
            err = {"message": str(e)[:140], "at": datetime.now().isoformat(timespec="seconds")}
            if entry is None:
                return {"error": str(e)[:140], "graph_error": err, "nodes": [], "links": [], "repo": repo}
            self.graph_cache[repo] = dict(entry, error=err)
            out = dict(entry["data"])
            out["graph_mtime"] = entry["mtime_ns"]
            out["graph_error"] = err
            return out
        raw = None
        if refresh and entry is not None:
            try:
                raw = Path(path).read_bytes()
            except OSError as e:
                err = {"message": str(e)[:140], "at": datetime.now().isoformat(timespec="seconds")}
                self.graph_cache[repo] = dict(entry, error=err)
                out = dict(entry["data"])
                out["graph_mtime"] = entry["mtime_ns"]
                out["graph_error"] = err
                return out
            if hashlib.sha256(raw).hexdigest() == entry["sha256"]:
                # Bytes equal the last-good content, so any stored error is stale.
                self.graph_cache[repo] = dict(entry, error=None)
                out = dict(entry["data"])
                out["graph_mtime"] = entry["mtime_ns"]
                return out
        if entry is not None and not refresh and st.st_mtime_ns == entry["mtime_ns"] and st.st_size == entry["size"]:
            out = dict(entry["data"])
            out["graph_mtime"] = entry["mtime_ns"]
            if entry["error"] is not None:
                out["graph_error"] = entry["error"]
            return out
        try:
            if raw is None:
                raw = Path(path).read_bytes()
            g = json.loads(raw)
            sym2file, files = {}, {}
            for n in g.get("nodes", []):
                sf = n.get("source_file")
                if not sf or n.get("file_type") != "code":
                    continue
                sym2file[n["id"]] = sf
                d = files.setdefault(sf, {"id": "F:" + sf, "file": sf,
                    "label": sf.replace("\\", "/").split("/")[-1], "symbols": 0, "communities": {}})
                d["symbols"] += 1
                d["communities"][str(n.get("community"))] = d["communities"].get(str(n.get("community")), 0) + 1
            links_out = {}
            for l in g.get("links", []):
                if l.get("relation") not in ("calls", "imports"):
                    continue
                sf, tf = sym2file.get(l.get("source")), sym2file.get(l.get("target"))
                if not sf or not tf or sf == tf:
                    continue
                key = (sf, tf) if sf < tf else (tf, sf)
                links_out[key] = links_out.get(key, 0) + 1
            deg = {}
            for (a, b), w in links_out.items():
                deg[a] = deg.get(a, 0) + w; deg[b] = deg.get(b, 0) + w
            hubs = sorted(deg.items(), key=lambda kv: -kv[1])[:12]
            hub_ids = {"F:" + f for f, _ in hubs}
            out = {"repo": repo, "total_symbols": len(sym2file), "nodes": list(files.values()),
                   "hubs": [{"id": "F:" + f, "deg": d} for f, d in hubs],
                   "links": [{"source": "F:" + k[0], "target": "F:" + k[1], "weight": w, "hub": (k[0] in {f for f, _ in hubs} or k[1] in {f for f, _ in hubs})} for k, w in links_out.items()]}
            for n in out["nodes"]:
                if n["id"] in hub_ids:
                    n["hub"] = deg.get(n["file"], 0)
        except Exception as e:
            err = {"message": str(e)[:140], "at": datetime.now().isoformat(timespec="seconds")}
            if entry is None:
                return {"error": str(e)[:140], "graph_error": err, "nodes": [], "links": [], "repo": repo}
            self.graph_cache[repo] = dict(entry, error=err)
            out = dict(entry["data"])
            out["graph_mtime"] = entry["mtime_ns"]
            out["graph_error"] = err
            return out
        self.graph_cache[repo] = {"data": out, "mtime_ns": st.st_mtime_ns, "size": st.st_size, "sha256": hashlib.sha256(raw).hexdigest(), "error": None}
        resp = dict(out)
        resp["graph_mtime"] = st.st_mtime_ns
        return resp


    def _build_section(self, name):
        try:
            if name == "services":
                return self.services(), None
            if name == "events":
                return self.feed_events()
            if name == "sessions":
                return self.active_sessions()
            if name == "writes":
                return self.recent_writes()
            return self.toolq_activity()
        except Exception as e:
            return None, _err("exception", e)


    def _empty_shape(self):
        return {"now": None, "boot_id": self.boot_id, "snapshot_seq": 0, "built_at_epoch": None,
                "section_built_at": {n: None for n in SECTIONS},
                "services": None, "events": None, "sessions": None, "writes": None, "toolq": None,
                "sources": {}, "section_age_s": {n: None for n in SECTIONS}, "snapshot_age_s": None,
                "section_state": {n: "never" for n in SECTIONS},
                "section_error": {n: None for n in SECTIONS},
                "section_errors": {n: None for n in SECTIONS},
                "toolq_parts": {"retrievals": None, "distill_runs": None},
                "pending": [], "pending_count": 0, "pending_oldest": None,
                "poller_errors": dict(self.poller_errors), "build_counter": {n: 0 for n in SECTIONS}}


    def _resolve_cursor(self, params, snap):
        boot = params.get("boot_id")
        if boot is not None and boot != self.boot_id:
            return "gap", None
        since = params.get("since_seq")
        if since is not None:
            try:
                seq = int(since)
            except (TypeError, ValueError):
                return "full", None
            cur = snap.get("snapshot_seq") or 0
            if seq > cur:
                return "gap", None
            if self.journal:
                start = min(self.journal)
                if seq < start:
                    return "gap", None
            return "delta", seq
        return "full", None


    def _activity_payload(self, params=None):
        params = params or {}
        d = self.snapshot.get("data") or self._empty_shape()
        err = {"section_error": dict(self.error_state["section_error"]),
               "build_counter": dict(self.error_state["build_counter"]),
               "poller_errors": dict(self.error_state["poller_errors"]),
               "toolq_part_errors": dict(self.error_state.get("toolq_part_errors") or {})}
        tq_built = dict(d.get("toolq_parts") or {})
        out = dict(d)
        out["registered_agents"] = self.registered_agents()
        now = time.time()
        built = d.get("built_at_epoch")
        out["snapshot_age_s"] = (now - built) if built is not None else None
        ages = {}
        states = {}
        for n in SECTIONS:
            b = (d.get("section_built_at") or {}).get(n)
            a = (now - b) if b is not None else None
            ages[n] = a
            states[n] = _section_state(b, a, err["section_error"].get(n), TTL[n])
        sev = {"fresh": 0, "stale": 1, "expired": 2, "never": 3}
        tp = {}
        best = "fresh"
        for p in ("retrievals", "distill_runs"):
            pb = tq_built.get(p)
            pa = (now - pb) if pb is not None else None
            perr = (err["toolq_part_errors"] or {}).get(p)
            st = _section_state(pb, pa, perr, TTL["toolq"])
            tp[p] = {"built_at": pb, "state": st}
            if sev[st] > sev[best]:
                best = st
        states["toolq"] = best
        out["section_age_s"] = ages
        out["section_state"] = states
        out["section_error"] = err["section_error"]
        out["build_counter"] = err["build_counter"]
        out["poller_errors"] = err["poller_errors"]
        sec_errs = dict(err["section_error"])
        tq_errs = err["toolq_part_errors"] or {}
        if tq_errs.get("retrievals") is None and tq_errs.get("distill_runs") is None:
            sec_errs["toolq"] = None
        else:
            sec_errs["toolq"] = {"retrievals": tq_errs.get("retrievals"), "distill_runs": tq_errs.get("distill_runs")}
        out["section_errors"] = sec_errs
        out["toolq_parts"] = tp
        if all((d.get(n) is None) for n in SECTIONS) and (d.get("snapshot_seq") or 0) > 0:
            out["events"] = []
        out["delta"] = False
        out["truncated"] = False
        mode, dseq = self._resolve_cursor(params, d)
        if mode == "gap":
            out["truncated"] = True
        elif mode == "delta":
            client_time = self.sequence_times.get(dseq)
            delta_events = []
            cur_seq = d.get("snapshot_seq") or 0
            for s in range(dseq + 1, cur_seq + 1):
                delta_events.extend(self.journal.get(s) or [])
            delta = {"delta": True, "boot_id": self.boot_id, "snapshot_seq": d.get("snapshot_seq") or 0, "since_seq": dseq, "truncated": False, "events": delta_events, "now": out.get("now"), "built_at_epoch": d.get("built_at_epoch"), "snapshot_age_s": out.get("snapshot_age_s"), "section_age_s": out.get("section_age_s"), "section_state": out.get("section_state"), "section_errors": out.get("section_errors"), "section_built_at": d.get("section_built_at") or {}, "toolq_parts": out.get("toolq_parts"), "sources": d.get("sources") or {}, "pending_count": d.get("pending_count"), "pending_oldest": d.get("pending_oldest")}
            for n in ("services", "sessions", "writes"):
                b = (d.get("section_built_at") or {}).get(n)
                if b is not None and (client_time is None or b > client_time):
                    delta[n] = d.get(n)
            tq_parts = out.get("toolq_parts") or {}
            if any((tq_parts.get(p) or {}).get("built_at") is not None and (client_time is None or (tq_parts.get(p) or {}).get("built_at") > client_time) for p in ("retrievals", "distill_runs")):
                delta["toolq"] = d.get("toolq")
            delta["registered_agents"] = out.get("registered_agents")
            return delta
        return out


    def _health_payload(self):
        published = self.snapshot
        data = published["data"]
        now = time.time()
        built = data.get("built_at_epoch")
        return {"up": True, "snapshot_age_s": (now - built) if built is not None else None,
                "last_build_at": built, "last_error": published.get("last_error"),
                "sources": data.get("sources") or {}, "pid": os.getpid(), "boot_id": self.boot_id}

    def build_snapshot(self):
        slots = dict(self.cache)
        now_ep = time.time()
        has_data = any(slots[n]["data"] is not None for n in SECTIONS)
        tqp = slots["toolq"].get("parts") or {}
        return {"now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "boot_id": self.boot_id,
                "snapshot_seq": self.sequence, "built_at_epoch": (now_ep if has_data else None),
                "section_built_at": {n: slots[n]["built_at"] for n in SECTIONS},
                "toolq_parts": {p: (tqp.get(p) or {}).get("built_at") for p in ("retrievals", "distill_runs")},
                "services": slots["services"]["data"], "events": slots["events"]["data"],
                "sessions": slots["sessions"]["data"], "writes": slots["writes"]["data"],
                "toolq": slots["toolq"]["data"], "sources": self._build_sources(slots), "pending": list(self.pending["list"]), "pending_count": self.pending["count"], "pending_oldest": self.pending["oldest"]}


    def _record_journal(self, snap):
        seq = snap.get("snapshot_seq") or 0
        if snap.get("events") is None:
            self.journal[seq] = []
            self.sequence_times[seq] = snap.get("built_at_epoch")
            cutoff = seq - 300
            for s in [k for k in self.journal if k <= cutoff]:
                del self.journal[s]
            for s in [k for k in self.sequence_times if k <= cutoff]:
                del self.sequence_times[s]
            return
        wire_by_id = {}
        cur_feed = {}
        for e in (snap.get("events") or []):
            wire_by_id[str(e.get("id"))] = e
            cur_feed[str(e.get("id"))] = _stable_event(e)
        changed = []
        for eid, ev in cur_feed.items():
            if self.previous["feed"].get(eid) != ev:
                changed.append(wire_by_id[eid])
        self.journal[seq] = changed
        self.sequence_times[seq] = snap.get("built_at_epoch")
        self.previous["feed"] = cur_feed
        cutoff = seq - 300
        for s in [k for k in self.journal if k <= cutoff]:
            del self.journal[s]
        for s in [k for k in self.sequence_times if k <= cutoff]:
            del self.sequence_times[s]


    def _event_detail(self, ev_id):
        try:
            rs = self.rows(self.audit_db, "SELECT audit_id, payload_json, state, created_at FROM durable_audit_outbox WHERE audit_id=? ORDER BY created_at ASC LIMIT 1", (ev_id,))
            if not rs:
                return 404, {"error": "not_found"}
            r = rs[0]
            if "_error" in r:
                return 500, {"error": "internal"}
            raw = r.get("payload_json") or ""
            raw_bytes = raw.encode("utf-8")
            base = {"id": str(r["audit_id"]), "state": r["state"], "created_at": iso(r["created_at"])}
            if len(raw_bytes) > 512 * 1024:
                base.update({"truncated": True, "full_size": len(raw_bytes), "payload_text": raw_bytes[:512 * 1024].decode("utf-8", errors="replace")})
                return 200, base
            try:
                payload = json.loads(raw)
            except Exception:
                base.update({"payload_error": "unparseable", "payload_text": raw_bytes[:512 * 1024].decode("utf-8", errors="replace") if len(raw_bytes) > 512 * 1024 else raw})
                return 200, base
            base["payload"] = payload
            return 200, base
        except Exception:
            return 500, {"error": "internal"}


    def history_events(self, from_ts, to_ts, agent=None, tool=None, limit=500, q=None, before=None, before_aid=None):
        """v17: tra log CU theo khoang thoi gian (epoch giay) + loc agent/tool.
        Dung cho FE: chon gio -> xem lai log cu (ngoai 240 event cua feed live).
        v24 (2026-11-20 Anh lenh tra no BE R15/R16):
          V45 (C15-11): predicate agent/tool DAY XUONG SQL enrich — ung vien khong
            khop khong vuot ranh gioi DB->Python; vong chunk dung som khi du trang.
          V44 (C15-07): q = tim chuoi con (case-insensitive) tren err + out + result
            cua payload; loi da troi khoi feed live tim lai duoc bang thong diep.
          V43 (C15-04): session tra DU id (khong [:40]) — cat hien thi la viec FE.
          V47 (C16-06): _tgt dung helper chung _event_tgt voi feed/pending."""
        try:
            lim = int(limit)
        except Exception:
            lim = 500
        lim = max(1, min(lim, 2000))
        try:
            f = float(from_ts or 0)
        except Exception:
            f = 0.0
        try:
            t = float(to_ts or 0) or time.time()
        except Exception:
            t = time.time()
        q = str(q or "").strip().lower()
        # v25: cursor trang — (created_at, audit_id) DESC exclusive; before_aid tie-break epoch trung
        try:
            bef = float(before) if before not in (None, "") else None
        except Exception:
            bef = None
        bef_aid = str(before_aid) if before_aid not in (None, "") else None
        # v19b: dung covering index (state, created_at, audit_id) -> 1500 dong trong 0.013s
        # thay vi full-scan 800k dong (treo >40s). Chi tai payload cho dong thuc su tra ve.
        cand = []
        err = None
        cur_sql = ""
        cur_args = []
        if bef is not None:
            if bef_aid is not None:
                cur_sql = " AND (created_at<? OR (created_at=? AND audit_id<?))"
                cur_args = [bef, bef, bef_aid]
            else:
                cur_sql = " AND created_at<?"
                cur_args = [bef]
        for st in ("applied", "pending"):
            rs = self.rows(self.audit_db, "SELECT audit_id, state, created_at FROM durable_audit_outbox "
                                  "WHERE state=? AND created_at>=? AND created_at<=?" + cur_sql + " "
                                  "ORDER BY created_at DESC, audit_id DESC LIMIT " + str(lim * 3), tuple([st, f, t] + cur_args))
            for r in rs:
                if "_error" in r:
                    err = r["_error"]
                    continue
                cand.append(r)
        cand.sort(key=lambda x: str(x.get("audit_id") or ""), reverse=True)
        cand.sort(key=lambda x: -(x.get("created_at") or 0))  # v25: (epoch DESC, aid DESC) khop SQL + cursor
        cand = cand[:lim * 3]
        picked = []
        pay_cache = {}
        for i in range(0, len(cand), 200):
            chunk = cand[i:i + 200]
            ids = [str(c["audit_id"]) for c in chunk]
            ph = ",".join("?" * len(ids))
            rich = {}
            # V45: predicate pushdown — agent/tool loc ngay trong SQL enrich.
            sql = ("SELECT audit_id, agent_id, tool_name, outcome, error_name, target, range_label, status, duration_ms, session_id "
                   "FROM events WHERE audit_id IN (" + ph + ")")
            args = list(ids)
            if agent:
                if str(agent) == "unknown":
                    sql += " AND (agent_id IS NULL OR agent_id='')"
                else:
                    sql += " AND agent_id=?"; args.append(str(agent))
            if tool:
                sql += " AND tool_name=?"; args.append(str(tool))
            try:
                rs = self.rows(self.sessions_db, sql, tuple(args))
                for r in rs:
                    if "_error" in r:
                        continue
                    rich[str(r["audit_id"])] = r
            except Exception:
                pass
            # V44: q can payload (err/out/result nam trong payload_json) — tai theo
            # chunk va cache lai cho khau lap dong ben duoi (khong query trung).
            if q:
                try:
                    prs = self.rows(self.audit_db, "SELECT audit_id, payload_json FROM durable_audit_outbox WHERE audit_id IN (" + ph + ")", tuple(ids))
                    for r in prs:
                        if "_error" in r:
                            continue
                        pay_cache[str(r["audit_id"])] = r.get("payload_json")
                except Exception:
                    pass
            for c in chunk:
                aid = str(c["audit_id"])
                rr = rich.get(aid) or {}
                # Guard Python giu nguyen semantics cu (dong vang mat trong events
                # van bi loc khi agent/tool set; 'unknown' = agent_id rong/NULL).
                if agent:
                    if str(agent) == "unknown":
                        if str(rr.get("agent_id") or "") != "":
                            continue
                    elif str(rr.get("agent_id") or "") != str(agent):
                        continue
                if tool and str(rr.get("tool_name") or "") != str(tool):
                    continue
                if q:
                    try:
                        pq = json.loads(pay_cache.get(aid) or "{}")
                    except Exception:
                        pq = {}
                    resq = pq.get("result")
                    so = se = ""
                    if isinstance(resq, dict):
                        so = _decode_b64_text(str(resq.get("stdout") or ""))
                        se = _decode_b64_text(str(resq.get("stderr") or ""))
                    try:
                        rq = json.dumps(resq, ensure_ascii=False) if resq is not None else ""
                    except Exception:
                        rq = ""
                    hay = (str(pq.get("error_msg") or "") + " " + so + " " + se + " " + rq).lower()
                    if q not in hay:
                        continue
                picked.append((c, rr))
                if len(picked) >= lim:
                    break
            if len(picked) >= lim:
                break
        out = []
        if picked:
            ids = [str(c["audit_id"]) for c, _ in picked]
            pay = dict(pay_cache)
            missing = [x for x in ids if x not in pay]
            if missing:
                ph = ",".join("?" * len(missing))
                try:
                    rs = self.rows(self.audit_db, "SELECT audit_id, payload_json FROM durable_audit_outbox WHERE audit_id IN (" + ph + ")", tuple(missing))
                    for r in rs:
                        if "_error" in r:
                            continue
                        pay[str(r["audit_id"])] = r.get("payload_json")
                except Exception:
                    pass
            for c, rr in picked:
                aid = str(c["audit_id"])
                try:
                    p = json.loads(pay.get(aid) or "{}")
                except Exception:
                    p = {}
                res = p.get("result") or {}
                _b = p.get("body") or {}
                _tgt = _event_tgt(_b, rr)  # V47: helper chung voi feed/pending
                # v20 (19/11/2026): decode base64 TRUOC tail — dong bo voi feed_events
                # (L417-424). Truoc day site nay tail thang b64 tho -> preview rac.
                if isinstance(res, dict) and (res.get("stdout") or res.get("stderr")):
                    res = dict(res)
                    if res.get("stdout"):
                        res["stdout"] = _decode_b64_text(str(res["stdout"]))
                    if res.get("stderr"):
                        res["stderr"] = _decode_b64_text(str(res["stderr"]))
                _tail = (str(res.get("stdout") or "") + str(res.get("stderr") or ""))[-400:]
                out.append({"id": aid, "state": c.get("state"), "ts": iso(c.get("created_at")), "epoch": c.get("created_at"),
                            "tool": rr.get("tool_name") or p.get("tool_name"),
                            "session": str(rr.get("session_id") or p.get("session_id") or ""),
                            "agent": rr.get("agent_id") or res.get("_task_agent_id") or "unknown",
                            "status": rr.get("status") or p.get("status"),
                            "dur_ms": rr.get("duration_ms") or p.get("duration_ms"),
                            "outcome": rr.get("outcome"), "error_name": rr.get("error_name"),
                            "range": rr.get("range_label"),
                            "err": (p.get("error_msg") or "")[:160], "tgt": _tgt or None, "out": _tail or None})
        out.sort(key=lambda x: str(x.get("id") or ""), reverse=True)
        out.sort(key=lambda x: -(x.get("epoch") or 0))  # v25: stable 2-pass = (epoch DESC, audit_id DESC) khop cursor
        # v25: cursor trang ke tiep = (epoch, audit_id) dong cuoi; has_more theo lim
        return {"events": out, "count": len(out), "from": f, "to": t, "agent": agent, "tool": tool,
                "q": q or None, "limit": lim, "error": err,
                    "has_more": len(out) >= lim,
                "next_before": (out[-1].get("epoch") if out else None),
                "next_before_aid": (str(out[-1].get("id")) if out else None)}


    def event_details(self, ids):
        """v19: output day du cho MOI tool (args + stdout/stderr + result + outcome).
        Nguon: sessions.db.events (co target/range/outcome/error_name/agent_id) JOIN
        durable_audit_outbox (co body/result goc). Read-only, cache theo audit_id."""
        ids = [str(i) for i in (ids or []) if str(i)][:200]
        out = {}
        need = []
        for i in ids:
            if i in self.detail_cache:
                out[i] = self.detail_cache[i]
            else:
                need.append(i)
        if need:
            ph = ",".join("?" * len(need))
            rich = {}
            try:
                rs = self.rows(self.sessions_db, "SELECT audit_id,tool_name,target,range_label,outcome,error_name,agent_id,status,duration_ms,queue_wait_ms,natural_log_line,result_json FROM events WHERE audit_id IN (" + ph + ")", tuple(need))
                for r in rs:
                    if "_error" in r:
                        continue
                    rich[str(r["audit_id"])] = r
            except Exception:
                pass
            pay = {}
            try:
                rs2 = self.rows(self.audit_db, "SELECT audit_id,payload_json FROM durable_audit_outbox WHERE audit_id IN (" + ph + ")", tuple(need))
                for r in rs2:
                    if "_error" in r:
                        continue
                    try:
                        p = json.loads(r.get("payload_json") or "{}")
                    except Exception:
                        p = {}
                    pay[str(r["audit_id"])] = p
            except Exception:
                pass
            for aid in need:
                p = pay.get(aid) or {}
                rr = rich.get(aid)
                res = p.get("result") if isinstance(p.get("result"), dict) else {}
                if not res and rr is not None:
                    try:
                        res = json.loads(rr.get("result_json") or "{}")
                    except Exception:
                        res = {}
                if not isinstance(res, dict):
                    res = {}
                body = p.get("body") if isinstance(p.get("body"), dict) else {}
                so = res.get("stdout") if isinstance(res.get("stdout"), str) else ""
                se = res.get("stderr") if isinstance(res.get("stderr"), str) else ""
                result = {}
                for k, v in res.items():
                    if str(k).startswith("_"):
                        continue
                    if k in ("stdout", "stderr"):
                        continue
                    result[k] = _dclip(v, 200)
                d = {
                    "id": aid,
                    "tool": (rr["tool_name"] if rr is not None else None) or p.get("tool_name"),
                    "agent": (rr["agent_id"] if rr is not None else None) or res.get("_task_agent_id"),
                    "status": p.get("status") or (rr["status"] if rr is not None else None),
                    "outcome": (rr["outcome"] if rr is not None else None),
                    "error_name": (rr["error_name"] if rr is not None else None) or res.get("error_name"),
                    "error_msg": p.get("error_msg") or "",
                    "target": _dclip(rr["target"], 220) if (rr is not None and rr["target"]) else None,
                    "range": (rr["range_label"] if rr is not None else None),
                    "dur_ms": (rr["duration_ms"] if rr is not None else None) or p.get("duration_ms"),
                    "qwait_ms": (rr["queue_wait_ms"] if rr is not None else None) or res.get("queue_wait_ms"),
                    "args": _detail_args(body),
                    "result": result,
                    "stdout": so,
                    "stderr": se,
                }
                out[aid] = d
                self.detail_cache[aid] = d
            if len(self.detail_cache) > 4000:
                self.detail_cache.clear()
        for i in ids:
            if i in self.detail_cache and i not in out:
                out[i] = self.detail_cache[i]
        return {"details": out, "count": len(out)}


class _MonitorRuntime:
    def __init__(self):
        self.store = _MonitorStore()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="toola-monitor")
        self.wake = asyncio.Event()
        self.stopping = False
        self.last_activity = asyncio.get_running_loop().time()
        self.request_slots = asyncio.Semaphore(12)
        self.poller = asyncio.create_task(self.poll(), name="toola-monitor-poller")

    async def run(self, func, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, func, *args)

    async def poll(self):
        loop = asyncio.get_running_loop()
        while not self.stopping:
            self.wake.clear()
            try:
                await self.run(self.store.tick)
            except Exception:
                _LOG.exception("Monitor snapshot refresh failed")
            if self.stopping:
                break
            delay = 1 if loop.time() - self.last_activity < 60 else 10
            try:
                await asyncio.wait_for(self.wake.wait(), delay)
            except asyncio.TimeoutError:
                pass

    async def close(self):
        self.stopping = True
        self.wake.set()
        try:
            await self.poller
        finally:
            try:
                await self.run(self.store.close)
            finally:
                await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)


_MONITOR = web.AppKey("toola_monitor", _MonitorRuntime)


async def _monitor_read(request):
    _require_loopback(request)
    if "event_id" in request.match_info:
        event_id = request.match_info["event_id"]
        if request.query or not _IDENTIFIER.fullmatch(event_id):
            raise web.HTTPBadRequest()
        path, params = f"events/{event_id}", []
    else:
        path = request.match_info["endpoint"]
        if path not in _QUERY_RULES:
            raise web.HTTPNotFound()
        params = _parameters(request, path)
    monitor = request.app[_MONITOR]
    if path == "activity":
        now = asyncio.get_running_loop().time()
        if now - monitor.last_activity >= 60:
            monitor.wake.set()
        monitor.last_activity = now
    try:
        if path == "health":
            # Health reads the last complete snapshot even while the worker is busy.
            status, body = monitor.store.dispatch(path, params)
        else:
            async with asyncio.timeout(4):
                async with monitor.request_slots:
                    status, body = await monitor.run(monitor.store.dispatch, path, params)
        if status == 500:
            return _internal()
        if status not in {200, 400, 404} or len(body) > _MAX_BYTES:
            return _unavailable(502)
        return web.Response(body=body, status=status, headers=_NO_CACHE,
                            content_type="application/json", charset="utf-8")
    except asyncio.TimeoutError:
        return _unavailable(504)
    except Exception:
        _LOG.exception("Monitor request failed for %s", path.split("/")[0])
        return _internal()


async def _monitor_client(app):
    monitor = app[_MONITOR] = _MonitorRuntime()
    try:
        yield
    finally:
        await monitor.close()


def register_web_routes(app):
    app.router.add_get("/monitor", monitor_page)
    app.router.add_get("/matrixtool", matrix_page)
    app.router.add_get("/assets/toola/{name}", _asset)
    app.router.add_get("/api/monitor/events/{event_id}", _monitor_read, allow_head=False)
    app.router.add_get("/api/monitor/{endpoint}", _monitor_read, allow_head=False)
    app.cleanup_ctx.append(_monitor_client)
