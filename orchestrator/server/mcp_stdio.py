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
import urllib.request

_LESSONS_CACHE: list = []
_LESSONS_MTIME: float = 0.0
_MCP_SERVER_GENERATION_SEEN: str | None = None
_MCP_OWNER_CLAIMS: dict[tuple[str, str], str] = {}


def _mcp_current_server_generation(srv) -> str | None:
    """Read the live HTTP daemon generation stamp for MCP stale-session checks."""
    port = getattr(srv, "PORT", 8083)
    base_url = os.environ.get("ORCHESTRATOR_BASE_URL", f"http://localhost:{port}").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        generation = payload.get("generation")
        return str(generation) if generation is not None else None
    except Exception:
        generation = getattr(srv, "SERVER_GENERATION", None)
        return str(generation) if generation is not None else None


def _mcp_generation_gate(current_generation: str | None) -> dict | None:
    """Remember first generation; return a stale-session error if it changes."""
    global _MCP_SERVER_GENERATION_SEEN
    if current_generation is None:
        return None
    if _MCP_SERVER_GENERATION_SEEN is None:
        _MCP_SERVER_GENERATION_SEEN = current_generation
        return None
    if _MCP_SERVER_GENERATION_SEEN != current_generation:
        return {
            "ok": False,
            "error_name": "MCP_STALE_RELOAD_REQUIRED",
            "hint": "Reload Claude/MCP client",
            "generation": current_generation,
            "expected_generation": _MCP_SERVER_GENERATION_SEEN,
        }
    return None


def _mcp_add_generation(text: str, generation: str | None) -> str:
    """Add generation to JSON-object MCP responses without changing other fields."""
    try:
        payload = json.loads(text)
    except Exception:
        return text
    if isinstance(payload, dict):
        payload.setdefault("generation", generation)
        return json.dumps(payload, ensure_ascii=False)
    return text


def _load_lessons(srv) -> list:
    """Load LESSONS_LOG.md rows (cached by mtime). Returns list of (keyword, fix)."""
    global _LESSONS_CACHE, _LESSONS_MTIME
    import time as _time
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
            generation = _mcp_current_server_generation(srv)
            stale = _mcp_generation_gate(generation)
            if stale is not None:
                return json.dumps(stale, ensure_ascii=False)
            return _mcp_add_generation(producer(), generation)

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
            return asyncio.run(srv.ct.next_recommended_action(project_path))
        # ── T1: Read / Understand ───────────────────────────────────
        @_mcp.tool
        def get_project_summary(project_path: str) -> str:
            """Quick overview from INDEX: groups, hotspots, and entrypoints."""
            return asyncio.run(srv.ct.get_project_summary(project_path))

        @_mcp.tool
        def query_index(project_path: str, query: str = "", group: str = None, min_complexity: int = None,
                        min_loc: int = None, file_pattern: str = None) -> str:
            """Filter files in INDEX by group, complexity, LOC, or pattern."""
            return asyncio.run(srv.ct.query_index(
                project_path, query=query, group=group, min_complexity=min_complexity,
                min_loc=min_loc, file_pattern=file_pattern,
            ))

        @_mcp.tool
        def find_symbol(name: str, path: str = "", project_path: str = "", context_lines: int = 0) -> str:
            """Find function, class, or variable definitions in the codebase."""
            return asyncio.run(srv.ct.find_symbol(name, path=path, project_path=project_path, context_lines=context_lines))

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
            return asyncio.run(srv.ct.read_file_range(
                file_path, start_line, end_line, project_path, max_lines,
                force_reseed, scope, symbol, section, related_query, query,
                token_budget, small_file_threshold, include_next_ranges,
                content_hash, force_refresh,
            ))

        @_mcp.tool
        def read_context_batch(project_path: str, requests: list,
                               token_budget: int = 6000,
                               max_items: int = 20,
                               small_file_threshold: int = 240,
                               include_next_ranges: bool = True) -> str:
            """Read multiple structural scopes under one total token budget."""
            return asyncio.run(srv.ct.read_context_batch(
                project_path, requests, token_budget, max_items,
                small_file_threshold, include_next_ranges,
            ))

        @_mcp.tool
        def architecture_context_pack(project_path: str, symbol: str = "",
                                      file_path: str = "", query: str = "",
                                      depth: int = 2, token_budget: int = 6000,
                                      max_files: int = 8,
                                      max_ranges: int = 20) -> str:
            """Build a graph-first readonly context pack without reindexing."""
            return asyncio.run(srv.ct.architecture_context_pack(
                project_path, symbol, file_path, query, depth, token_budget,
                max_files, max_ranges,
            ))

        @_mcp.tool
        def search_code(query: str, path: str = "",
                        project_path: str = "", file_pattern: str = "*") -> str:
            """Run a regex search across the codebase."""
            return asyncio.run(srv.ct.search_code(query, path, project_path, file_pattern))

        @_mcp.tool
        def get_file_symbols(file_path: str, project_path: str = "") -> str:
            """List all functions, classes, and variables in one file."""
            return asyncio.run(srv.ct.get_file_symbols(file_path, project_path))

        # ── T2: Write / Change ──────────────────────────────────────
        @_mcp.tool
        def write_fix(file_path: str, start_line: int,
                      end_line: int, new_content: str,
                      project_path: str = "", dry_run: bool = False,
                      content_encoding: str = "none") -> str:
            """Edit a code range by line number. Set dry_run=True to preview before writing."""
            return asyncio.run(srv.ct.write_fix(file_path, start_line,
                                            end_line, new_content, project_path,
                                            dry_run, content_encoding))

        @_mcp.tool
        def write_fix_html(file_path: str, start_line: int, end_line: int, new_content: str,
                            project_path: str = "", content_encoding: str = "none",
                            force: bool = False) -> str:
            """Edit an .html file with automatic delimiter checks. Set force=True to bypass the check."""
            return asyncio.run(srv.ct.write_fix_html(file_path, start_line, end_line, new_content,
                                                  project_path, content_encoding, force))

        @_mcp.tool
        def write_new_file(path: str, new_content: str = "", project_path: str = "",
                           content_encoding: str = "plain", dry_run: bool = False,
                           overwrite: bool = False) -> str:
            """Create a new file. Set dry_run=True to preview before applying."""
            # R43.3 C1: was passing "" as content + new_content as 7th positional.
            # Function had fallback `if new_content is None: new_content = content` —
            # worked by accident. Cleaner: use keyword args.
            return asyncio.run(srv.ct.write_new_file(
                path=path,
                content=new_content,
                project_path=project_path,
                content_encoding=content_encoding,
                dry_run=dry_run,
                overwrite=overwrite,
            ))

        @_mcp.tool
        def apply_after_dry_run(project_path: str, dry_run_id: str,
                                confirm_hash: str = "",
                                confirm_warnings: bool = False) -> str:
            """Apply a previously previewed payload by dry_run_id."""
            return asyncio.run(srv.ct.apply_after_dry_run(project_path, dry_run_id, confirm_hash, confirm_warnings))

        @_mcp.tool
        def delete_file(file_path: str, project_path: str = "") -> str:
            """Delete a file. This cannot be undone."""
            return asyncio.run(srv.ct.delete_file(file_path, project_path))

        # ── T2: Validate / Verify ───────────────────────────────────
        @_mcp.tool
        def validate_file(file_path: str, lang: str = "auto", project_path: str = "") -> str:
            """Validate file syntax (Python/JS/HTML). project_path resolves relative file_path."""
            return asyncio.run(srv.ct.validate_file(file_path, lang, project_path))

        @_mcp.tool
        def validate_encoding(file_path: str, project_path: str = "",
                              expected_encoding: str = "utf-8",
                              language_hint: str = "") -> str:
            """Check UTF-8 encoding and mojibake issues after writing."""
            return asyncio.run(srv.ct.validate_encoding(file_path, project_path, expected_encoding, language_hint))

        @_mcp.tool
        def validate_symbols(file_path: str, project_path: str = "") -> str:
            """Post-write check: validate syntax and compare symbols against INDEX to catch missing definitions."""
            return asyncio.run(srv.ct.validate_symbols(file_path, project_path))

        @_mcp.tool
        def cache_delta(project_path: str) -> str:
            """Compare the filesystem against INDEX to detect modified, added, or deleted files."""
            return asyncio.run(srv.ct.cache_delta(project_path))

        @_mcp.tool
        def run_project_command(project_path: str, command, timeout: int = 120) -> str:
            """Run a bounded command in the project working directory."""
            return asyncio.run(srv.ct.run_project_command(project_path, command, timeout))

        @_mcp.tool
        def run_test_profile(project_path: str, profile: str = "unittest", timeout: int = 120) -> str:
            """Run a predefined test profile (unittest/pytest). Automatically detects the tests directory."""
            return asyncio.run(srv.ct.run_test_profile(project_path, profile, timeout))

        # ── T2: Safety ──────────────────────────────────────────────
        @_mcp.tool
        def backup_project(project_path: str, backup_dir: str = None, force: bool = False, keep_last_n: int = 2) -> str:
            """Create a zip backup of the projesrv.ct."""
            return asyncio.run(srv.ct.backup_project(project_path, backup_dir, force=force, keep_last_n=keep_last_n))

        # ── T3: Session management ──────────────────────────────────
        @_mcp.tool
        def close_session(project_path: str) -> str:
            """Close the active session and save a summary."""
            return asyncio.run(srv.ct.close_session(project_path))

        @_mcp.tool
        def rename_session(project_path: str, new_title: str) -> str:
            """Rename the active session."""
            return asyncio.run(srv.ct.rename_session(project_path, new_title))

        @_mcp.tool
        def list_sessions(project_path: str, limit: int = 10) -> str:
            """List the most recent sessions for the projesrv.ct."""
            return _mcp_guard_response(lambda: asyncio.run(srv.ct.list_sessions(project_path, limit)))

        @_mcp.tool
        def get_session_events(session_id: str = "", limit: int = 50, offset: int = 0,
                                project_path: str = "") -> str:
            """Read events from a session. project_path auto-resolves the active session when session_id is empty."""
            return asyncio.run(srv.ct.get_session_events(session_id, limit, offset, project_path))

        # ── T3: Workflow ────────────────────────────────────────────
        @_mcp.tool
        def workflow_status_registry(project_path: str) -> str:
            """Read workflow registry state (R43.2 C2: renamed from workflow_status to disambiguate from the R35 MD workflow)."""
            return asyncio.run(srv.ct.workflow_status_registry(project_path))

        @_mcp.tool
        def set_workflow_mode(project_path: str, mode: str) -> str:
            """Update the workflow mode (ANALYZE/VERIFY/EXECUTE)."""
            return asyncio.run(srv.ct.set_workflow_mode(project_path, mode))

        # ── T2: Cache / Index ───────────────────────────────────────
        @_mcp.tool
        def cache_status(project_path: str) -> str:
            """Check the project cache status."""
            return asyncio.run(srv.ct.cache_status(project_path))

        @_mcp.tool
        def generate_project_map(project_path: str) -> str:
            """Generate the project cache and INDEX.json."""
            return asyncio.run(srv.ct.generate_project_map(project_path))

        @_mcp.tool
        def refresh_project_map(project_path: str) -> str:
            """Refresh the cache and rebuild INDEX.json."""
            return asyncio.run(srv.ct.refresh_project_map(project_path))

        @_mcp.tool
        def backup_status(project_path: str) -> str:
            """Check the backup manifest."""
            return asyncio.run(srv.ct.backup_status(project_path))

        @_mcp.tool
        def list_structure(path: str = "", depth: int = 2, save_output: bool = True,
                           project_path: str = "", strict: bool = False) -> str:
            """List the project folder tree. project_path is an alias of path."""
            return asyncio.run(srv.ct.list_structure(path, depth, save_output, project_path, strict=strict))

        # ── T2: Build context ─────────────────────────────────────────────
        @_mcp.tool
        def create_benchmark_request(project_path: str, idea: str = "") -> str:
            """Create BENCHMARK_REQUEST.md without crawling."""
            return asyncio.run(srv.ct.create_benchmark_request(project_path, idea))

        @_mcp.tool
        def create_build_context(project_path: str, idea: str = "", approved_inputs: str = "") -> str:
            """Create BUILD_CONTEXT.md from user-approved input."""
            return asyncio.run(srv.ct.create_build_context(project_path, idea, approved_inputs))

        @_mcp.tool
        def get_usage_stats(project_path: str, last_n: int = 200,
                             include_http_log: bool = False) -> str:
            """APP_SESSION_LOG.jsonl + SQLite events (project-scoped). E-01: include_http_log is tombstoned — explicit True fails loud with GLOBAL_SCOPE_NOT_ENABLED."""
            return asyncio.run(srv.ct.get_usage_stats(project_path, last_n, include_http_log))

        @_mcp.tool
        def capability_status(project_path: str = "") -> str:
            """Bao capability parser/indexer hien co."""
            return asyncio.run(srv.ct.capability_status(project_path))

        @_mcp.tool
        def suggest_missing_capabilities(project_path: str = "") -> str:
            """Goi y thu vien parser can cai them."""
            return asyncio.run(srv.ct.suggest_missing_capabilities(project_path))

        # ── Council dispatch (leader non-blocking) ───────────────────────
        # Leader (Claude Desktop via MCP) dispatches workers without blocking the
        # stdio pipe. Returns {job_id, status} immediately; poll with
        # get_toolcall_result(job_id). Avoids the 6000s block on the leader path.
        @_mcp.tool
        def council_message_async(sender: str = "user", to: str = "", text: str = "",
                                  body_file: str = "", timeout_sec: int = 3600,
                                  provider: str = "", model: str = "") -> str:
            """Non-blocking council dispatch. Returns job_id immediately.

            Leader uses this to fan out multiple workers in parallel without
            holding the MCP stdio pipe. Poll the reply with get_toolcall_result.
            Pitfalls from .council/LESSONS_LOG.md are auto-injected when the
            task text matches known keywords (drift guard, no rule bloat).
            """
            def _dispatch() -> str:
                # Auto-inject lessons: grep LESSONS_LOG.md for keyword matches in text.
                # job_id must be generated BEFORE the payload/branching below; the
                # body_file refactor accidentally dropped it, leaving the json.dumps
                # calls below with `NameError: name 'job_id' is not defined`.
                import uuid as _uuid
                job_id = f"mcp_{_uuid.uuid4().hex[:12]}"
                payload = {"sender": sender, "to": to,
                           "timeout_sec": timeout_sec, "provider": provider, "model": model}
                if body_file:
                    payload["body_file"] = body_file
                else:
                    payload["text"] = _inject_lessons(srv, text)
                # Hand off to the long-lived daemon via the async relay endpoint.
                # The daemon runs the worker; the reply lands in broker_filtered.jsonl.
                def _fire():
                    import urllib.request as _ur
                    body = json.dumps(payload).encode("utf-8")
                    port = getattr(srv, "PORT", 8083)
                    token = getattr(srv, "EXPECTED_TOKEN", "")
                    req = _ur.Request(
                        f"http://localhost:{port}/api/council/send-async",
                        data=body,
                        headers={"Content-Type": "application/json",
                                 "Origin": f"http://localhost:{port}",
                                 "X-Orchestrator-Token": token})
                    return _ur.urlopen(req, timeout=15).read().decode("utf-8", "replace")
                try:
                    raw = asyncio.run(asyncio.to_thread(_fire))
                    resp = json.loads(raw)
                    return json.dumps({"ok": True, "job_id": job_id,
                                       "status": "dispatched", "target": to,
                                       "dispatch": resp}, ensure_ascii=False)
                except Exception as exc:
                    return json.dumps({"ok": False, "job_id": job_id,
                                       "status": "failed", "error": str(exc)[:200]},
                                      ensure_ascii=False)
            return _mcp_guard_response(_dispatch)

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
