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
import json
import os
import secrets
import uuid as _uuid
import urllib.error
import urllib.request
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
_MCP_PROXY_TOKEN_ERRORS = (
    "MUTATION_TOKEN_REQUIRED", "MUTATION_TOKEN_EXPIRED", "TOKEN_REQUIRED",
)
# urlopen(timeout=) is SECONDS, not ms. The old proxy branch in
# orchestrator_server.py used ``timeout=6100`` (~1.7 HOURS) -- almost certainly a
# ms/seconds unit bug. 180s is a sane default; per-call overrides ride on top for
# the genuinely long tools (backup_project, run_project_command, reindex).
_MCP_PROXY_DEFAULT_TIMEOUT = float(os.environ.get("MCP_PROXY_HTTP_TIMEOUT", "180"))
# Margin added to a tool's own ``timeout`` arg so the HTTP wait always outlives the
# work the daemon is doing (otherwise the proxy times out on a call that is still
# legitimately running).
_MCP_PROXY_TIMEOUT_MARGIN = 30.0


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
    if timeout_override is not None:
        http_timeout = max(_MCP_PROXY_DEFAULT_TIMEOUT,
                           float(timeout_override) + _MCP_PROXY_TIMEOUT_MARGIN)
    else:
        http_timeout = _MCP_PROXY_DEFAULT_TIMEOUT
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
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
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
        return json.dumps({
            "ok": False,
            "error_name": "MCP_PROXY_UNREACHABLE",
            "tool": name,
            "error": f"{type(exc).__name__}: {exc}",
            "base_url": base,
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
            sys.stderr.write(
                f"[mcp_stdio] daemon generation changed {previous} -> "
                f"{current_generation}; re-baselined (self-heal, calls proxy to live daemon)\n",
            )
        except Exception:
            pass
        return None
    return None


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
            return json.dumps(payload, ensure_ascii=False)
        payload.setdefault("generation", generation.get("generation"))
        payload.setdefault("generation_source", generation.get("source"))
        if generation.get("probe_error"):
            payload.setdefault("generation_probe_error", generation["probe_error"])
        if generation.get("source") != "daemon":
            payload.setdefault("mcp_generation", generation.get("local_generation"))
        return json.dumps(payload, ensure_ascii=False)
    payload.setdefault("generation", generation)
    return json.dumps(payload, ensure_ascii=False)

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

        # ── T3: Session ────────────────────────────────────────────
        @_mcp.tool
        def open_session(session_title: str = "", project_path: str = ".", mode: str = "T1",
                         agent_id: str = "") -> str:
            """Open or resume an agent-scoped project session."""
            resolved_agent_id = ((agent_id or "").strip()
                                 or (os.environ.get("COUNCIL_AGENT_ID") or "").strip())
            return _mcp_guard_response(lambda: asyncio.run(srv.ct.open_session(
                session_title=session_title, project_path=project_path, mode=mode,
                agent_id=resolved_agent_id)))

        @_mcp.tool
        def log_native_event(action_type: str, tool_name: str, target: str,
                              duration_ms: int = 0, status: str = "ok",
                              reason: str = "", project_path: str = "") -> str:
            """Log one realtime native event into the session. Call immediately after native fallback for Rule C."""
            return _mcp_guard_response(lambda: asyncio.run(srv.ct.log_native_event(
                action_type, tool_name, target, duration_ms, status, reason, project_path)))


        @_mcp.tool
        def manage_agent(action: str, agent_id: str = "", target: str = "all",
                         persona: str = "", rule_file: str = "", rule_format: str = "markdown",
                         rule_template: str = "slim", mcp_file: str = "", mcp_format: str = "json",
                         mcp_key: str = "project-orchestrator", dry_run: bool = False,
                         mode: str = "", cleanup: bool = True) -> str:
            """R86: CA lifecycle — register/unregister agents, set_mode A|native (default A = [A]-first), bulk-write global-rule/MCP config. Preserves persona line."""
            return _mcp_guard_response(lambda: asyncio.run(srv.ct.manage_agent(
                action, agent_id, target, persona, rule_file, rule_format, rule_template,
                mcp_file, mcp_format, mcp_key, dry_run, mode=mode, cleanup=cleanup)))

        @_mcp.tool
        def prepare_ca_session(project_path: str, mode: str = "T1", idea: str = "",
                               agent_id: str = "", host_session_id: str = "") -> str:
            """Prepare, open/resume, and bootstrap an agent-scoped CA session."""
            canonical_agent_id = agent_id or os.environ.get("COUNCIL_AGENT_ID", "")
            return _mcp_guard_response(lambda: asyncio.run(srv.ct.prepare_ca_session(
                project_path=project_path, mode=mode, idea=idea,
                agent_id=canonical_agent_id, host_session_id=host_session_id)))

        @_mcp.tool
        def get_rule_core_text(agent_id: str = "", force: bool = False) -> str:
            """Return canonical CORE_RULE text and metadata, guarded per agent."""
            return _mcp_guard_response(lambda: asyncio.run(
                srv.ct.get_rule_core_text(agent_id=agent_id, force=force)))

        @_mcp.tool
        def bootstrap_session(project_path: str = "", session_id: str = "",
                              agent_id: str = "", owner_claim_id: str = "") -> str:
            """Return the session-scoped canonical lifecycle bootstrap."""
            claim_key = (str(session_id).strip(), str(agent_id).strip())
            claim_id = owner_claim_id or _MCP_OWNER_CLAIMS.get(claim_key)
            if not claim_id:
                claim_id = secrets.token_urlsafe(32)
                _MCP_OWNER_CLAIMS[claim_key] = claim_id
            return _mcp_guard_response(lambda: asyncio.run(
                srv.ct.bootstrap_session(
                    project_path, session_id, agent_id, claim_id)))

        @_mcp.tool
        def begin_task(session_id: str, prompt: str, agent_id: str = "",
                       max_chars: int = 2000, owner_capability: str = "",
                       delegation_capability: str = "",
                       delegate_agent_id: str = "", owner_claim_id: str = "") -> str:
            """Create the authoritative task epoch for a real user prompt."""
            return _mcp_guard_response(lambda: asyncio.run(
                srv.ct.begin_task(
                    session_id, prompt, agent_id, max_chars,
                    owner_capability, delegation_capability, delegate_agent_id,
                    owner_claim_id,
                )))

        @_mcp.tool
        def get_task_checkpoint(session_id: str) -> str:
            """Return the active task checkpoint for compaction/recovery."""
            return _mcp_guard_response(lambda: asyncio.run(
                srv.ct.get_task_checkpoint(session_id)))

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
            return _mcp_guard_response(lambda: _mcp_response_text(
                srv._run_mcp_coro(srv.dispatch_tool_call(name, payload or {}, transport="mcp"))))

        @_mcp.tool
        def tool_names(tier: str = "", name_contains: str = "", category: str = "") -> str:
            """Compact MCP discovery. Filter by tier/name/category; call tool_schema before call_tool."""
            return _mcp_guard_response(
                lambda: json.dumps(srv._tool_names_payload(tier, name_contains, category), ensure_ascii=False))

        @_mcp.tool
        def tool_schema(name: str) -> str:
            """Return enriched schema for exactly one [A] tool."""
            def _produce() -> str:
                payload, _status = srv._tool_schema_payload(name)
                return json.dumps(payload, ensure_ascii=False)
            return _mcp_guard_response(_produce)

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
            return _mcp_call_text("run_test_profile", {
                "project_path": project_path, "profile": profile, "timeout": timeout,
            })

        # ── T2: Safety ──────────────────────────────────────────────
        @_mcp.tool
        def backup_project(project_path: str, backup_dir: str = None, force: bool = False, keep_last_n: int = 2) -> str:
            """Create a zip backup of the project."""
            return _mcp_call_text("backup_project", {
                "project_path": project_path, "backup_dir": backup_dir,
                "force": force, "keep_last_n": keep_last_n,
            })

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
            hints = []
            if context_hints:
                try:
                    parsed = json.loads(context_hints)
                    if isinstance(parsed, list):
                        hints = [str(x) for x in parsed]
                    elif parsed:
                        hints = [str(parsed)]
                except Exception:
                    hints = [h.strip() for h in context_hints.split(",") if h.strip()]
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
