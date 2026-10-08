# pyright: reportMissingImports=false, reportOptionalSubscript=false, reportArgumentType=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportPossiblyUnboundVariable=false, reportOptionalMemberAccess=false, reportAssignmentType=false
"""V15 R8 new tool implementations.

Lives in a separate module to keep code_tools.py manageable. All tools here are
imported into code_tools.TOOL_MAP via `from tools_v15 import *` style registration.

Tier prefix conventions:
- T1 code intel (B.1):    regex_search, find_callers, find_definition_and_usage,
                          import_graph, ast_query, git_blame_symbol
                          (semantic_search / type_search retired 2026-08-11 — D3:
                          they were never registered, so the stubs misled agents
                          into calling UNKNOWN_TOOL; use search_code instead)
- T2/T3 management (B.2): write_transaction, session_audit_summary, tool_decision_hint
- T4a browser (B.3):      open_browser_page, read_page, query_selector, click,
                          fill, evaluate_js, read_console_errors,
                          read_network_errors, screenshot_page, wait_for_selector,
                          compare_dom_snapshot
- T4c lifecycle (B.4):    server_lifecycle_status, port_owner_inspect,
                          detect_stale_server, static_asset_cache_check,
                          bundle_version_check, restart_server_via_bat_hint
- T4d desktop (B.5):      desktop_window_list, desktop_app_focus, desktop_click,
                          desktop_screenshot, electron_devtools_log,
                          process_tree_inspect
- T4 test (B.6):          run_playwright_test, record_ui_flow, assert_text_visible,
                          assert_button_click_changes_state, assert_console_no_error
- UI backend (B.7):       get_token_meter, get_skill_state, list_projects,
                          get_validation_queue

All async, returning json.dumps(...) per existing pattern. Lazy-import heavy deps
to avoid cold-start cost when feature not used.

═══════════════════════════════════════════════════════════════════════════════
R43.3 H11 — IMPORT TOPOLOGY DISCIPLINE
═══════════════════════════════════════════════════════════════════════════════
Module dependency chain:
    orchestrator_server.py  →  code_tools.py  →  tools_v15.py  →  ???

tools_v15.py MUST NEVER import `orchestrator_server` or `code_tools` at top level
(would create cyclic load: os→ct→tv→os→...). All cross-module references must be
INSIDE function bodies (lazy import). Use `sys.modules.get('orchestrator_server')`
for soft lookups that work even before the module finishes loading.

Enforced at runtime INSIDE `acceptance_dashboard` (this file), which AST-scans its own
top-level imports and reports `import_topology_violations` + `import_topology_scan_ok`
(R43.3 H11). There is no `import_topology_clean` field: read those two live keys, and
treat scan_ok=false as "the check never ran", never as "the check passed".
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

# A34: stdlib-only leaf under orchestrator.core -- safe at module level here
# because orchestrator/__init__.py and orchestrator/core/__init__.py are inert,
# so this does NOT re-create the ct->tv->os import cycle the docstring forbids.
from orchestrator.core.params import csv_param
from orchestrator.text_lines import split_lines

# V15 R18: schema version stamped on every response so CA can detect drift
# and invalidate cached schema assumptions across [A] updates.
_SCHEMA_VERSION = "v15.r18"

def _safe_detach(current_tid, target_tid):
    try:
        import win32process
        win32process.AttachThreadInput(current_tid, target_tid, False)
    except Exception as exc:
        logging.getLogger(__name__).debug("AttachThreadInput detach failed: %s", exc)


# V15 R18 — per-session mission state (Phase B). Reset on close_session.
# Structure: {session_id: {mission_id, relevant_skills, relevant_tools, declared_at, mission}}
_SESSION_MISSION: dict = {}


# V15 R18 — Verbosity helper (Phase A). Strips verbose educational blocks based
# on CA-chosen mode. Per-call ONLY (no sticky state). Drift detection upstream
# can force `full` mode regardless of CA choice when suspicious pattern detected.
# V15 R22 — per-tool verbose block registries
_VERBOSE_BLOCKS_SESSION_OPEN = (
    "path_resolution_note", "progressive_gaps_hint", "resolved_from",
    "project_path_confirmed", "root_exists",
    # matrix_audit handled separately (compressed, not dropped)
)
_VERBOSE_BLOCKS_MATRIX = (
    "drift", "alias_drift", "progressive_gaps", "coverage",
    "inventory_classification",
)
_VERBOSE_BLOCKS_SKILL_STATE_PER_ENTRY = (
    "path", "path_absolute", "size_bytes",
    "read_file_range_payload", "read_file_range_payload_relative",
)
_VERBOSE_BLOCKS_READ_SKILL = (
    "path_absolute", "total_lines", "shown_lines", "max_lines", "truncated",
)
_VERBOSE_BLOCKS_MISSION = (
    "irrelevant_skills_skip", "recommended_sequence", "caveats",
    "score_floor_explanation", "irrelevant_tools_skip",
)
_VERBOSE_BLOCKS_DECISION_HINT = (
    "rationale", "tools_validation", "payload_example_lookup_hint",
    "confidence", "scope_hint", "risk_hint",
)


def _build_events_window_meta(start_id: int, window_end: int,
                                 start_ts: str | None, end_ts: str | None,
                                 window_source: str) -> dict:
    """V15 R26 — Build events_window dict with marker age detection.

    Adds `marker_age_seconds` and `marker_stale_warning` so CA notices when
    a round-start marker is from a previous task (>30min ago) and should
    be re-issued for the current round.
    """
    from datetime import datetime as _dt
    meta = {
        "start_event_id": start_id,
        "end_event_id": window_end,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "source": window_source,
        "source_meaning": {
            "manual_since_event_id": "CA passed since_event_id explicitly",
            "explicit_round_start_marker": "R16.10: CA called an explicit round-start marker at task begin — window covers full round (most accurate)",
            "last_round_boundary": "Auto-boundary at previous round submit marker (counts events SINCE last submit)",
            "fallback_last_50_events": "No prior round in this session; window is approximate (last ~50 events)",
        }.get(window_source, ""),
    }
    # R26: marker age — detect stale start marker reused across tasks
    if start_ts:
        try:
            start_dt = _dt.fromisoformat(str(start_ts))
            age = (_dt.now() - start_dt).total_seconds()
            meta["marker_age_seconds"] = round(age, 1)
            if age > 1800:  # > 30 minutes
                meta["marker_stale_warning"] = True
                meta["marker_stale_hint"] = (
                    f"Window start marker is {round(age/60, 1)} min old. "
                    "Consider calling the round-start marker again for the current round "
                    "to avoid mixing metrics with previous task."
                )
            else:
                meta["marker_stale_warning"] = False
        except Exception:
            pass
    return meta


def _auto_artifact_if_oversize(response: dict, project_path: str = "",
                                  tool_name: str = "",
                                  round_id: str = "",
                                  max_bytes: int = 4096,
                                  enabled: bool = False) -> dict:
    """V15 R18 Phase C — Auto-write oversize response to artifact file.

    When enabled=True AND serialized response size > max_bytes:
      1. Write full response JSON to <project>/.orchestrator_cache/artifacts/<round_id>/<tool>_response.json
      2. Replace response with minimal essentials + auto_artifact_path + sha256 + length

    Otherwise: return response unchanged.

    CA opt-in via `auto_artifact=true` param on participating tools. Keeps
    response transport small while preserving full evidence on disk.
    """
    if not enabled or not isinstance(response, dict):
        return response
    try:
        serialized = json.dumps(response, ensure_ascii=False)
    except Exception:
        return response
    size = len(serialized.encode("utf-8", errors="replace"))
    if size <= max_bytes:
        return response
    if not project_path:
        # No project context to write to — degrade gracefully
        response["auto_artifact_skipped_reason"] = "project_path missing"
        return response
    try:
        root = Path(project_path).resolve()
        if not root.exists():
            response["auto_artifact_skipped_reason"] = f"project_path not found: {project_path}"
            return response
        rid = round_id or f"r-{int(time.time() * 1000)}"
        artifact_dir = root / ".orchestrator_cache" / "artifacts" / rid
        artifact_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9_]+", "_", tool_name or "tool")[:40] or "tool"
        file_path = artifact_dir / f"{slug}_response.json"
        tmp = file_path.with_suffix(".json.tmp")
        tmp.write_text(serialized, encoding="utf-8", errors="replace")
        tmp.replace(file_path)
        sha = hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()
        rel = str(file_path.relative_to(root)).replace("\\", "/")
        # Build minimal essentials — keep ok flag, core ids, summary counts
        # V15 R26: default ok=True (success path; errors return via _err before _auto_artifact).
        # Fix: response dict at this point hasn't been wrapped by _ok yet, so
        # response.get("ok") returns None → harness would see ok:null and false-FAIL.
        _ok_val = response.get("ok")
        if _ok_val is None:
            _ok_val = True
        essentials = {
            "ok": _ok_val,
            "auto_artifact_path": rel,
            "auto_artifact_path_absolute": str(file_path.resolve()).replace("\\", "/"),
            "auto_artifact_sha256": sha,
            "auto_artifact_sha256_prefix": sha[:16],
            "auto_artifact_length": size,
            "omitted_bytes_saved": size,
            "auto_artifact_note": "Response too large; written to file. Re-read via read_file_range(file_path=<path_absolute>).",
        }
        # Preserve a small set of useful summary fields if present
        for keep in ("page_id", "total", "visible_count", "panels_found",
                      "view_panels_count", "candidate_count", "area_matches"):
            if keep in response:
                essentials[keep] = response[keep]
        return essentials
    except Exception as exc:
        response["auto_artifact_error"] = str(exc)
        return response


def _apply_verbosity(response: dict, mode: str = "full", kind: str = "payload") -> dict:
    """V15 R18 — Strip verbose educational blocks based on CA-chosen mode.

    mode:
      "full"  (default)  — no change, R17 behavior
      "terse"            — replace verbose blocks with 1-line cheat sheet ref
      "ref"              — drop verbose blocks entirely (CA fully knows them)

    kind: which block family to apply ("payload", "error", "session").

    Always preserves core data (round_id, summary, human_summary, backend_actual).
    """
    if mode == "full" or not isinstance(response, dict):
        return response
    if kind == "error":
        guidance = response.get("ca_guidance")
        if isinstance(guidance, dict):
            for k in ("notes", "known_gotchas", "recommended_prompt_rule"):
                guidance.pop(k, None)
        if mode == "terse":
            response["_verbosity_note"] = "terse mode — error guidance trimmed; pass verbosity='full' for full schema notes"
    elif kind == "session":
        for k in ("path_resolution_note", "matrix_audit_verbose", "environment_note"):
            response.pop(k, None)
    elif kind == "session_open":
        # R22: open_session — compress matrix_audit to counts only; drop path notes
        mx = response.get("matrix_audit") or {}
        if isinstance(mx, dict) and mx:
            compressed = {
                "drift_count": mx.get("drift_count", 0),
                "alias_drift_count": mx.get("alias_drift_count", 0),
                "progressive_gap_count": mx.get("progressive_gap_count", 0),
                "alias_drift_severity": mx.get("alias_drift_severity"),
            }
            # A33 (2026-09-17): provenance must survive terse, otherwise a
            # foreign process's gated audit reads as a clean daemon audit.
            # Conditional so a daemon call adds exactly one short key.
            for _k in ("audit_scope", "local_registry_counts", "audit_note"):
                if mx.get(_k) is not None:
                    compressed[_k] = mx[_k]
            response["matrix_audit"] = compressed
        for k in _VERBOSE_BLOCKS_SESSION_OPEN:
            response.pop(k, None)
    elif kind == "matrix_audit":
        # R22: audit_tool_matrix — terse keeps first 3 drift entries + counts,
        # ref drops all arrays
        if mode == "ref":
            for k in _VERBOSE_BLOCKS_MATRIX:
                response.pop(k, None)
        elif mode == "terse":
            if isinstance(response.get("drift"), list):
                response["drift"] = response["drift"][:3]
                response["drift_truncated_at"] = 3
            if isinstance(response.get("alias_drift"), list):
                response["alias_drift"] = response["alias_drift"][:3]
                response["alias_drift_truncated_at"] = 3
            for k in ("coverage", "inventory_classification"):
                response.pop(k, None)
    elif kind == "skill_state":
        # R22: get_skill_state — strip per-entry path metadata from every entry
        skills = response.get("skills_available") or []
        if mode in ("terse", "ref") and isinstance(skills, list):
            response["skills_available"] = [
                {"name": s.get("name")} for s in skills if isinstance(s, dict)
            ]
            response["skills_metadata_stripped"] = True
            response["_metadata_hint"] = (
                "Pass verbosity='full' to get path/size/read_file_range_payload per entry; "
                "or call read_skill(name) directly."
            )
    elif kind == "skill_read":
        # R22: read_skill — keep result + skill_name, drop metadata
        for k in _VERBOSE_BLOCKS_READ_SKILL:
            response.pop(k, None)
    elif kind == "mission":
        # R22: declare_mission — keep relevant_skills + relevant_tools curation output
        for k in _VERBOSE_BLOCKS_MISSION:
            response.pop(k, None)
    elif kind == "decision_hint":
        # R22: tool_decision_hint — keep recommended_* core, drop reasoning blocks
        for k in _VERBOSE_BLOCKS_DECISION_HINT:
            response.pop(k, None)
        if mode == "terse":
            pep = response.get("payload_example_per_tool")
            if isinstance(pep, dict):
                response["payload_example_per_tool_keys"] = sorted(pep.keys())
                response.pop("payload_example_per_tool", None)
    response["verbosity_mode"] = mode
    return response


def _ok(data: dict) -> str:
    """Return successful JSON response.

    V15 R26: defensive against ok=None (from _auto_artifact_if_oversize before R26 fix).
    Treat None as missing → overwrite with True. Genuine ok=False is preserved.
    """
    if "ok" not in data or data.get("ok") is None:
        data["ok"] = True
    # V15 R18: stamp schema version on every successful response
    data["schema_version"] = _SCHEMA_VERSION
    return json.dumps(data, ensure_ascii=False)


def _err(msg: str, error_name: str = "TOOL_ERROR", **extra) -> str:
    """V15 R22: accepts optional `verbosity` (default 'full' — errors should
    be visible). When 'terse', `_apply_verbosity(kind='error')` strips
    ca_guidance.notes / known_gotchas / recommended_prompt_rule.
    """
    body = {"ok": False, "error": msg, "error_name": error_name}
    verbosity = extra.pop("verbosity", "full")
    body.update(extra)
    body["schema_version"] = _SCHEMA_VERSION  # always our version, not caller override
    if verbosity != "full":
        body = _apply_verbosity(body, verbosity, "error")
    return json.dumps(body, ensure_ascii=False)


def _missing_dep(name: str, install: str, **extra) -> str:
    return _err(
        f"Dependency '{name}' not installed",
        error_name="MISSING_DEPENDENCY",
        install=install,
        suggested_fallback="install_then_retry",
        **extra,
    )


# ────────────────────────────────────────────────────────────────────────────
# B.1 — T1 Code intel
# ────────────────────────────────────────────────────────────────────────────

async def regex_search(pattern: str = "", project_path: str = "", path: str = "",
                       file_pattern: str = "*",
                       flags: str = "i", max_results: int = 50,
                       max_context_lines: int = 0,
                       regex_deadline_seconds: float = 5.0, **aliases) -> str:
    """DEPRECATED (Task 2 2026-09-10): use search_code(mode='regex') directly.

    Search files by regex. Convenience alias of search_code(mode='regex').

    flags: 'i' case-insensitive (default), 'm' multiline, 's' dotall, '' none.
    Accepts `query` as alias for `pattern`.

    B019 (2026-08-21): `path` accepted and forwarded. It was missing, so the
    alias was strictly weaker than its target on the one axis its own error
    message names: REGEX_INPUT_BUDGET_EXCEEDED answers
    ca_next_action="narrow_path_or_reduce_regex_input", and passing `path` to
    narrow it answered UNSUPPORTED_FIELD while the identical
    search_code(mode='regex', path=...) answered ok:True. An alias that advises a
    field it does not accept sends the caller in a circle.

    B-01 (2026-08-10): default regex deadline raised from the search-code
    hard default (1.0s) to 5.0s. The live daemon path adds ~1-2s of overhead
    (executor-lane submit, asyncio.to_thread hop, snapshot pin, response
    envelope) on top of the pure-regex work measured in unit tests, so the
    previous 1.0s ceiling fired REGEX_SEARCH_TIMEOUT even for narrow
    ``file_pattern`` queries that complete in ~0.25s when called directly.
    Callers that need a tighter budget can still pass
    ``regex_deadline_seconds`` explicitly; it is forwarded verbatim.
    """
    pattern = pattern or aliases.get("query") or aliases.get("regex") or aliases.get("q") or ""
    # Lazy import to avoid circular at module load
    from code_tools import search_code
    return await search_code(
        query=pattern,
        project_path=project_path,
        path=path,
        file_pattern=file_pattern,
        mode="regex",
        max_results=max_results,
        max_context_lines=max_context_lines,
        regex_deadline_seconds=regex_deadline_seconds,
    )


async def find_callers(symbol: str = "", project_path: str = "", context_lines: int = 0,
                       **aliases) -> str:
    """DEPRECATED (Task 2 2026-09-10): use find_symbol(include='callers') directly.

    Find all caller sites of `symbol` (excludes definition itself).

    V15 R8 B.1: convenience wrapper of find_symbol(include='callers').
    Accepts `query` as alias for `symbol`.
    """
    symbol = symbol or aliases.get("query") or aliases.get("name") or aliases.get("q") or ""
    from code_tools import find_symbol
    return await find_symbol(
        name=symbol,
        project_path=project_path,
        include="callers",
        context_lines=context_lines,
    )


async def find_definition_and_usage(symbol: str, project_path: str,
                                     context_lines: int = 0) -> str:
    """DEPRECATED (Task 2 2026-09-10): use find_symbol(include='all') directly.

    One call: returns {definition, callers, usages} for `symbol`.

    V15 R8 B.1: convenience wrapper of find_symbol(include='all').
    """
    from code_tools import find_symbol
    return await find_symbol(
        name=symbol,
        project_path=project_path,
        include="all",
        context_lines=context_lines,
    )


async def import_graph(project_path: str, file_pattern: str = "*.py,*.js,*.ts",
                       max_files: int = 500) -> str:
    """Scan import statements across project; return adjacency list.

    V15 R8 B.1: regex-based; covers Python (`import X`, `from X import Y`),
    JS/TS (`import ... from 'X'`, `require('X')`).
    Output: { ok, edges: [{from: file, to: 'module_name'}], nodes: [...], file_count }
    """
    base = Path(project_path)
    if not base.exists():
        return _err(f"Path not found: {project_path}", error_name="PATH_NOT_FOUND")

    patterns = csv_param(file_pattern)

    PY_FROM = re.compile(r"^\s*from\s+([\w\.]+)\s+import\s")
    PY_IMPORT = re.compile(r"^\s*import\s+([\w\.]+)")
    JS_FROM = re.compile(r"""(?:from|import)\s+['"]([^'"]+)['"]""")
    JS_REQUIRE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")

    def _scan() -> dict:
        edges = []
        nodes = set()
        scanned = 0
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", "node_modules",
                                                      ".git", "venv", ".orchestrator_cache"}]
            for fn in files:
                if scanned >= max_files:
                    return {"edges": edges, "nodes": list(nodes), "file_count": scanned,
                            "truncated": True}
                from_path = Path(root) / fn
                if not any(from_path.match(p) for p in patterns):
                    continue
                rel = str(from_path.relative_to(base)).replace("\\", "/")
                nodes.add(rel)
                try:
                    txt = from_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                scanned += 1
                ext = from_path.suffix.lower()
                if ext == ".py":
                    for line in txt.splitlines():
                        m = PY_FROM.match(line) or PY_IMPORT.match(line)
                        if m:
                            edges.append({"from": rel, "to": m.group(1)})
                elif ext in (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"):
                    for m in JS_FROM.finditer(txt):
                        edges.append({"from": rel, "to": m.group(1)})
                    for m in JS_REQUIRE.finditer(txt):
                        edges.append({"from": rel, "to": m.group(1)})
        return {"edges": edges, "nodes": list(nodes), "file_count": scanned,
                "truncated": False}

    data = await asyncio.to_thread(_scan)
    # CAN-053 P2 (codo): summary-first — split internal/stdlib/external/test so
    # the payload informs before it floods (raw 69KB edge dumps hit result_ref).
    import sys as _sys_ig
    _stdlib = set(getattr(_sys_ig, "stdlib_module_names", ()) or ())
    _node_stems = {n.rsplit("/", 1)[-1].rsplit(".", 1)[0] for n in data["nodes"]}
    _summary = {"internal": 0, "stdlib": 0, "external": 0, "from_tests": 0}
    _ext_top: dict = {}
    for e in data["edges"]:
        _to_root = str(e.get("to", "")).split(".")[0].strip("./")
        if "test" in str(e.get("from", "")).lower():
            _summary["from_tests"] += 1
        if _to_root in _node_stems or str(e.get("to", "")).startswith("."):
            e["kind"] = "internal"
            _summary["internal"] += 1
        elif _to_root in _stdlib:
            e["kind"] = "stdlib"
            _summary["stdlib"] += 1
        else:
            e["kind"] = "external"
            _summary["external"] += 1
            _ext_top[_to_root] = _ext_top.get(_to_root, 0) + 1
    return _ok({"summary": _summary,
                "top_external": dict(sorted(_ext_top.items(), key=lambda x: -x[1])[:15]),
                "edge_count": len(data["edges"]),
                "node_count": len(data["nodes"]),
                "file_count": data["file_count"],
                "truncated": data.get("truncated", False),
                "edges": data["edges"][:1000],
                "nodes": data["nodes"]})


async def ast_query(pattern: str, project_path: str,
                     file_pattern: str = "*.py", max_results: int = 50) -> str:
    """Python AST-based pattern query.

    V15 R8 B.1: preset patterns (string-based):
    - 'async_function'       — all async def
    - 'class_inheriting'     — all class with bases
    - 'try_bare_except'      — try/except: with no exception type (risky)
    - 'function_returning_dict' — functions whose body has `return {`
    """
    base = Path(project_path)
    if not base.exists():
        return _err(f"Path not found: {project_path}", error_name="PATH_NOT_FOUND")

    pattern_norm = (pattern or "").lower().strip().replace(" ", "_")
    SUPPORTED = {
        "async_function", "class_inheriting", "try_bare_except",
        "function_returning_dict",
    }
    if pattern_norm not in SUPPORTED:
        return _err(
            f"AST pattern not supported: {pattern}",
            error_name="UNSUPPORTED_FIELD",
            accepted_fields=sorted(SUPPORTED),
        )
    # A34: coerce once, outside the walk -- `file_pattern` may arrive as a
    # csv string, a JSON array, or a real list; all three must select the same
    # files (before A34 a list crashed on .split).
    _pats = csv_param(file_pattern)
    def _scan() -> list:
        hits = []
        for root, dirs, files in os.walk(base, onerror=lambda e: None):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", "node_modules",
                                                      ".git", "venv", ".orchestrator_cache"}]
            for fn in files:
                if len(hits) >= max_results:
                    return hits
                fp = Path(root) / fn
                if not any(fp.match(p) for p in _pats):
                    continue
                try:
                    src = fp.read_text(encoding="utf-8-sig", errors="ignore")
                    tree = ast.parse(src)
                except Exception:
                    continue
                rel = str(fp.relative_to(base)).replace("\\", "/")
                for node in ast.walk(tree):
                    if pattern_norm == "async_function" and isinstance(node, ast.AsyncFunctionDef):
                        hits.append({"file": rel, "line": node.lineno, "name": node.name})
                    elif pattern_norm == "class_inheriting" and isinstance(node, ast.ClassDef) and node.bases:
                        base_names = []
                        for b in node.bases:
                            if isinstance(b, ast.Name):
                                base_names.append(b.id)
                            elif isinstance(b, ast.Attribute):
                                base_names.append(b.attr)
                        hits.append({"file": rel, "line": node.lineno, "name": node.name, "bases": base_names})
                    elif pattern_norm == "try_bare_except" and isinstance(node, ast.ExceptHandler) and node.type is None:
                        hits.append({"file": rel, "line": node.lineno, "kind": "bare_except"})
                    elif pattern_norm == "function_returning_dict" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for sub in ast.walk(node):
                            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                                hits.append({"file": rel, "line": node.lineno, "name": node.name})
                                break
                    if len(hits) >= max_results:
                        return hits
        return hits

    hits = await asyncio.to_thread(_scan)
    return _ok({"pattern": pattern_norm, "results": hits, "count": len(hits)})


async def git_blame_symbol(symbol: str, project_path: str,
                            file_pattern: str = "*.py") -> str:
    """Git blame the first definition match of `symbol` across project.

    V15 R8 B.1: uses find_symbol first to locate, then git log -L for that range.
    """
    from code_tools import find_symbol
    raw = await find_symbol(name=symbol, project_path=project_path, include="def")
    fs = json.loads(raw)
    if not fs.get("results"):
        return _err(f"Symbol '{symbol}' not found", error_name="SYMBOL_NOT_FOUND")
    first = fs["results"][0]
    file_rel = first["file"]
    line = first["line"]

    def _git_log() -> dict:
        try:
            # `git log -L START,+10:FILE` shows commits touching that line range
            cmd = ["git", "-C", project_path, "log",
                   f"-L{line},+10:{file_rel}", "--max-count=5", "--no-patch",
                   "--format=%h%x09%an%x09%ad%x09%s", "--date=short"]
            # P8: match git tools standard 60s budget (orchestrator/git_tools.py::_run default timeout=60); 20s was the only outlier (Pattern A).
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                return {"ok": False, "error": r.stderr.strip()[:300]}
            lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
            commits = []
            for ln in lines[:5]:
                parts = ln.split("\t")
                if len(parts) >= 4:
                    commits.append({"hash": parts[0], "author": parts[1],
                                    "date": parts[2], "subject": parts[3]})
            return {"ok": True, "commits": commits}
        except FileNotFoundError:
            return {"ok": False, "error": "git not on PATH"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "git log timeout"}

    data = await asyncio.to_thread(_git_log)
    if not data.get("ok"):
        return _err(data.get("error", "git blame failed"), error_name="GIT_BLAME_FAIL",
                    file=file_rel, line=line)
    return _ok({"symbol": symbol, "file": file_rel, "line": line,
                "commits": data["commits"], "commit_count": len(data["commits"])})


# D3 (2026-08-11): semantic_search and type_search stubs retired.
# They were never registered in TOOL_MAP (unregistered stubs), so calling them
# returned UNKNOWN_TOOL; the docs in skill 15 told agents to use them. Removed
# so the module no longer pretends they are available. Use search_code instead.


# ────────────────────────────────────────────────────────────────────────────
# B.2 — T2/T3 management
# ────────────────────────────────────────────────────────────────────────────

def _r43_structural_check(removed_text: str, added_text: str, file_suffix: str = "") -> dict:
    """V15 R43 P1 #3-B: detect range-incomplete patterns by counting unbalanced
    brace / paren / bracket deltas between removed and added text.

    Returns {balanced: bool, deltas: {char: int}, suspect: str, hint: str}.
    A non-zero net delta means CA's range probably didn't cover full block.
    """
    pairs = [("{", "}"), ("(", ")"), ("[", "]")]
    deltas = {}
    suspect = ""
    for op_o, cl in pairs:
        r_o, r_c = removed_text.count(op_o), removed_text.count(cl)
        a_o, a_c = added_text.count(op_o), added_text.count(cl)
        net = (r_o - r_c) - (a_o - a_c)
        deltas[f"{op_o}{cl}"] = net
        if net != 0 and not suspect:
            suspect = f"{op_o}{cl}"
    balanced = all(v == 0 for v in deltas.values())
    hint = ""
    if not balanced:
        hint = (
            f"Range may be incomplete: net {suspect} delta = {deltas[suspect]}. "
            f"Either start_line/end_line don't cover the full block, or new_content is "
            f"missing closing delimiter(s). Re-check the range covers the entire function/block, "
            f"or use replace_between_anchors mode (anchor-based patch)."
        )
    return {
        "structural_balanced": balanced,
        "delimiter_deltas": deltas,
        "suspect_delimiter": suspect,
        "structural_hint": hint,
    }


async def _r43_replace_between_anchors(
    file_path: str,
    start_anchor: str,
    end_anchor: str,
    new_content: str,
    project_path: str = "",
    dry_run: bool = False,
    content_encoding: str = "none",
    end_anchor_inclusive: bool = False,
    start_anchor_is_regex: bool = False,
    end_anchor_is_regex: bool = False,
) -> str:
    """V15 R43 P1 #3-B: anchor-based replacement. Resolves text anchors to line
    numbers then delegates to write_fix. Eliminates brittleness of line-number
    patches for long functions where small earlier edits shift lines.

    Anchors match the FIRST line containing the substring (or regex match).
    end_anchor_inclusive: when True, replace through end_anchor line; when False,
    end_anchor line is preserved (replace up to line before it).
    """
    import re as _re_anchor

    from code_tools import _resolve_project_file as _resolve_anchor
    from code_tools import write_fix as _wf_anchor
    try:
        fp = _resolve_anchor(project_path, file_path)
    except Exception as exc:
        return _err(f"anchor resolve failed: {exc}", error_name="PATH_RESOLVE_FAILED")
    if not fp.exists():
        return _err(f"File not found: {file_path}", error_name="FILE_NOT_FOUND")
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return _err(f"read failed: {exc}", error_name="READ_FAILED")
    # The line numbers write_fix counts (text_lines.split_lines): str.splitlines()
    # also broke at a form feed or U+2028, so the anchor landed below its real line.
    lines = split_lines(text)
    start_line = 0
    end_line = 0
    for i, ln in enumerate(lines, start=1):
        if start_line == 0:
            match = (
                bool(_re_anchor.search(start_anchor, ln)) if start_anchor_is_regex
                else (start_anchor in ln)
            )
            if match:
                start_line = i
                continue
        if start_line and end_line == 0:
            match = (
                bool(_re_anchor.search(end_anchor, ln)) if end_anchor_is_regex
                else (end_anchor in ln)
            )
            if match:
                end_line = i if end_anchor_inclusive else (i - 1)
                break
    if start_line == 0:
        return _err(
            f"start_anchor not found: {start_anchor!r}",
            error_name="ANCHOR_NOT_FOUND",
            which="start_anchor",
            file_path=str(fp),
            ca_next_action="search_code_first_to_find_unique_anchor_string",
        )
    if end_line == 0 or end_line < start_line:
        return _err(
            f"end_anchor not found after start_line {start_line}: {end_anchor!r}",
            error_name="ANCHOR_NOT_FOUND",
            which="end_anchor",
            start_line_resolved=start_line,
            file_path=str(fp),
            ca_next_action="search_code_for_end_anchor_or_widen_anchor_pattern",
        )
    # Delegate to write_fix
    return await _wf_anchor(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        new_content=new_content,
        project_path=project_path,
        dry_run=dry_run,
        content_encoding=content_encoding,
    )


async def replace_between_anchors(
    file_path: str,
    start_anchor: str,
    end_anchor: str,
    new_content: str,
    project_path: str = "",
    dry_run: bool = False,
    content_encoding: str = "none",
    end_anchor_inclusive: bool = False,
    start_anchor_is_regex: bool = False,
    end_anchor_is_regex: bool = False,
) -> str:
    """V15 R43 P1 #3-B T2: anchor-based file patch. Resolves text anchors to
    line numbers and delegates to write_fix. Eliminates line-number brittleness
    in long functions / files with shifting line counts.

    Token-protected (write op). Requires INDEX.json (same as write_fix).
    """
    return await _r43_replace_between_anchors(
        file_path=file_path,
        start_anchor=start_anchor,
        end_anchor=end_anchor,
        new_content=new_content,
        project_path=project_path,
        dry_run=dry_run,
        content_encoding=content_encoding,
        end_anchor_inclusive=end_anchor_inclusive,
        start_anchor_is_regex=start_anchor_is_regex,
        end_anchor_is_regex=end_anchor_is_regex,
    )


async def write_transaction(operations: list, project_path: str = "",
                              backup: bool = True,
                              structural_check: bool = True) -> str:
    """Atomic multi-file write batch.

    V15 R8 B.2: each op = {file_path, start_line, end_line, new_content, content_encoding?}.
    V15 R13.4 fix #T3.2: op may include `tool: "write_new_file"` to create new files
    in same batch. Default tool is "write_fix" (line-replacement on existing file).
    V15 R43 P1 #3-B: op may include `tool: "replace_between_anchors"` with
    {start_anchor, end_anchor, new_content} for anchor-based patches.
    V15 R43 P1 #3-B: structural_check=True (default) detects range-incomplete
    patterns in dry_run phase via brace/paren/bracket delta count — surfaces
    STRUCTURAL_IMBALANCE warning before write.

    Steps: dry_run all → structural check → if any fail → abort. backup_project →
    apply all → validate batch → if any validate fail → revert all .bak.
    """
    from code_tools import (
        backup_project,
        post_write_batch_verify,
        write_fix,
        write_new_file,
    )
    if not isinstance(operations, list) or not operations:
        return _err("operations must be non-empty list", error_name="MISSING_REQUIRED_FIELD")

    def _op_tool(op):
        """Return canonical tool name for this op. Default = write_fix."""
        t = (op.get("tool") or "").strip().lower()
        if t in {"write_new_file", "new_file", "create"}:
            return "write_new_file"
        if t in {"replace_between_anchors", "anchors", "anchor"}:
            return "replace_between_anchors"
        # Implicit: presence of start_anchor + end_anchor switches mode
        if op.get("start_anchor") and op.get("end_anchor"):
            return "replace_between_anchors"
        return "write_fix"

    def _operation_target(op):
        from orchestrator.fileops.xroot import classify_write_target

        effective_root = Path(op.get("project_path") or project_path).resolve()
        if not effective_root.exists():
            raise ValueError(f"Operation project root not found: {effective_root}")
        raw_path = Path(op["file_path"])
        target = raw_path.resolve() if raw_path.is_absolute() else (effective_root / raw_path).resolve()
        # #35: the deny list is read before any operation runs, for every target;
        # being inside the operation's root grants nothing (see classify_write_target).
        verdict = classify_write_target(target, effective_root, "write_transaction", "")
        if not verdict["allowed"]:
            raise ValueError(
                f"XROOT_DENIED: {verdict['policy'].get('reason', 'xroot policy denied write')}"
            )
        if verdict["cross_root"]:
            return effective_root, target, None
        return effective_root, target, str(target.relative_to(effective_root)).replace("\\", "/")

    async def _dispatch(op, dry_run: bool):
        """Run write_fix / write_new_file / replace_between_anchors depending on op.tool."""
        sub_proj = op.get("project_path") or project_path
        tool = _op_tool(op)
        if tool == "write_new_file":
            return await write_new_file(
                path=op["file_path"],
                new_content=op.get("new_content", ""),
                project_path=sub_proj,
                dry_run=dry_run,
                content_encoding=op.get("content_encoding", "none"),
                overwrite=op.get("overwrite", False),
            )
        if tool == "replace_between_anchors":
            return await _r43_replace_between_anchors(
                file_path=op["file_path"],
                start_anchor=op.get("start_anchor", ""),
                end_anchor=op.get("end_anchor", ""),
                new_content=op.get("new_content", ""),
                project_path=sub_proj,
                dry_run=dry_run,
                content_encoding=op.get("content_encoding", "none"),
                end_anchor_inclusive=op.get("end_anchor_inclusive", False),
                start_anchor_is_regex=op.get("start_anchor_is_regex", False),
                end_anchor_is_regex=op.get("end_anchor_is_regex", False),
            )
        return await write_fix(
            file_path=op["file_path"],
            start_line=op.get("start_line", 1),
            end_line=op.get("end_line", 1),
            new_content=op.get("new_content", ""),
            project_path=sub_proj,
            dry_run=dry_run,
            content_encoding=op.get("content_encoding", "none"),
            expected_hash=op.get("expected_hash", ""),
        )

    # Phase 1: dry_run all
    dry_results = []
    structural_warnings = []
    resolved_operations = []
    for i, op in enumerate(operations):
        if not isinstance(op, dict) or "file_path" not in op:
            return _err(f"operation[{i}] missing file_path", error_name="SCHEMA_VIOLATION",
                        index=i)
        try:
            resolved_operations.append(_operation_target(op))
        except ValueError as exc:
            msg = str(exc)
            ename = "XROOT_DENIED" if msg.startswith("XROOT_DENIED") else "INVALID_OPERATION_TARGET"
            return _err(msg, error_name=ename, index=i)
        raw = await _dispatch(op, dry_run=True)
        sub = json.loads(raw)
        dry_results.append(sub)
        if not sub.get("ok"):
            return _err(
                f"dry_run failed at operation[{i}] (tool={_op_tool(op)}): {sub.get('error', 'unknown')}",
                error_name="DRY_RUN_FAILED",
                index=i,
                dry_run_results=dry_results,
            )
        # R43 P1 #3-B: structural check on each dry_run preview
        if structural_check and _op_tool(op) in {"write_fix", "replace_between_anchors"}:
            try:
                preview = sub.get("preview") or {}
                removed = "\n".join(preview.get("lines_removed", []) or [])
                added = "\n".join(preview.get("lines_added", []) or [])
                if removed or added:
                    suffix = Path(op["file_path"]).suffix.lower()
                    chk = _r43_structural_check(removed, added, suffix)
                    if not chk["structural_balanced"]:
                        structural_warnings.append({
                            "operation_index": i,
                            "file_path": op["file_path"],
                            **chk,
                        })
            except Exception:
                pass
    # CAN-053 (codo P1): delimiter-delta imbalance is a HEURISTIC — the write
    # pipeline already syntax-validates the full candidate and auto-reverts on
    # failure. Surface as warning instead of aborting the whole batch.
    if structural_warnings:
        for _w in structural_warnings:
            _w.setdefault("severity", "warning")
            _w.setdefault("note", "heuristic only — full-candidate validation still gates the write")

    # Phase 2: backup
    backup_info = []
    if backup:
        try:
            backup_groups = {}
            for effective_root, target, relative in resolved_operations:
                if relative is None:
                    continue
                if target.exists():
                    backup_groups.setdefault(str(effective_root), []).append(relative)
            for root_label, targets in backup_groups.items():
                result = json.loads(await backup_project(
                    project_path=root_label,
                    force=True,
                    target_paths=targets,
                ))
                if not result.get("ok"):
                    return _err(
                        f"backup_project failed for {root_label}: {result.get('error', 'unknown')}",
                        error_name="BACKUP_FAILED",
                        backup_results=backup_info + [result],
                    )
                backup_info.append(result)
        except Exception as exc:
            return _err(f"backup_project failed: {exc}", error_name="BACKUP_FAILED")

    snapshots = {}
    snapshot_roots = {}
    for op_root, target, relative in resolved_operations:
        key = str(target)
        if key not in snapshots:
            snapshots[key] = target.read_bytes() if target.exists() else None
            snapshot_roots[key] = str(op_root) if relative is not None else ""

    import code_tools as _ct_r41
    from orchestrator.tools.write.core import _seal_read_token

    def _rollback(reason):
        restored = []
        rollback_errors = []
        for target_label, original in snapshots.items():
            target = Path(target_label)
            try:
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    _ct_r41._atomic_write_bytes(target, original)
                _ct_r41._update_cache_after_write(str(target), snapshot_roots.get(target_label) or None)
                # BUG-01b: reseed read token after rollback so the restored content does not
                # trigger a stale FILE_CHANGED -- through the one producer the guard reads.
                if original is not None:
                    _seal_read_token(target, snapshot_roots.get(target_label, ""))
                restored.append(target_label)
            except Exception as exc:
                rollback_errors.append({"file": target_label, "error": str(exc)})
        verified = []
        for target_label, original in snapshots.items():
            target = Path(target_label)
            matches = (not target.exists()) if original is None else (
                target.exists() and target.read_bytes() == original
            )
            verified.append({"file": target_label, "restored": matches})
        return {
            "reason": reason,
            "restored_files": restored,
            "rollback_errors": rollback_errors,
            "rollback_verified": all(item["restored"] for item in verified),
            "rollback_verification": verified,
        }

    # Phase 3: apply all
    # Hash-chain: feed each op's new_hash into the next op on the same
    # resolved absolute target as expected_hash. Explicit expected_hash
    # is never overridden. Cache refresh uses the resolved target path.
    apply_results = []
    changed_files = []
    last_hash = {}
    for i, op in enumerate(operations):
        _root, target, _rel = resolved_operations[i]
        key = str(target)
        fpath = op.get("file_path", "")
        op2 = dict(op)
        if not op2.get("expected_hash") and key in last_hash:
            op2["expected_hash"] = last_hash[key]
        raw = await _dispatch(op2, dry_run=False)
        sub = json.loads(raw)
        apply_results.append(sub)
        if sub.get("ok"):
            changed_files.append(fpath)
            if sub.get("new_hash"):
                last_hash[key] = sub["new_hash"]
            try:
                _ct_r41._update_cache_after_write(key)
            except Exception:
                pass
        else:
            rollback = _rollback("apply_failed")
            return _err(
                f"apply failed at operation[{i}] (tool={_op_tool(op)}): {sub.get('error', 'unknown')}",
                error_name="BATCH_VERIFY_FAILED_AUTO_REVERT",
                index=i,
                successful_so_far=i,
                apply_results=apply_results,
                auto_reverted=True,
                **rollback,
                backup=backup_info,
                same_file_batch_hint=(
                    "Multi-op same file: apply loop auto-chains expected_hash "
                    "from the prior op's new_hash on the resolved target. "
                    "Explicit expected_hash is never overridden. Sort bottom-up "
                    "to avoid line-shift content errors (not FILE_CHANGED)."
                ),
            )
    # Phase 4: batch validate
    validation_groups = {}
    for effective_root, _target, relative in resolved_operations:
        if relative is None:
            continue
        validation_groups.setdefault(str(effective_root), []).append(relative)
    validation_results = []
    for root_label, files in validation_groups.items():
        val_raw = await post_write_batch_verify(
            project_path=root_label,
            changed_files=list(dict.fromkeys(files)),
            run_validate_symbols=True,
            run_cache_delta=True,
        )
        validation_results.append({"project_path": root_label, **json.loads(val_raw)})
    validation_ok = all(item.get("ok") for item in validation_results)
    val = validation_results[0] if len(validation_results) == 1 else {
        "ok": validation_ok,
        "overall_status": "pass" if validation_ok else "fail",
        "groups": validation_results,
    }
    if not validation_ok:
        rollback = _rollback("validation_failed")
        return _err(
            "Batch verification failed; transaction rolled back.",
            error_name="BATCH_VERIFY_FAILED_AUTO_REVERT",
            apply_results=apply_results,
            validation=val,
            auto_reverted=True,
            **rollback,
            backup=backup_info,
        )

    return _ok({
        "transaction_id": f"txn-{int(time.time())}",
        "operations_count": len(operations),
        "applied_count": len(changed_files),
        "changed_files": changed_files,
        "apply_results": apply_results,
        "validation": val,
        "backup": backup_info,
        "structural_warnings": structural_warnings,
    })

async def audit_reconciliation_report(project_path: str = "", target_session_id: str = "", session_id: str = "", agent_id: str = "") -> str:
    """Read-only reconciliation using explicit or correlation-bound evidence."""
    import json as j
    import re
    import sqlite3

    import code_tools as ct
    ct._sync_session_db_alias()
    wanted = str(target_session_id or session_id or "").strip()
    if not wanted and not project_path:
        return _err("project_path or target_session_id required", error_name="MISSING_REQUIRED_FIELD")
    def dec(raw):
        try: return j.loads(raw) if raw else None
        except (TypeError, ValueError): return None
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield str(key).lower(), item
                yield from walk(item)
        elif isinstance(value, list):
            for item in value: yield from walk(item)
    def links(source, recovery):
        ops = ([{"event_id": recovery[0], "tool": recovery[1], "correlation_type": recovery[2], "correlation_key": recovery[3]}] if recovery else [])
        tests, commits = [], []
        for evidence in (source, dec(recovery[4]) if recovery else None):
            for key, value in walk(evidence):
                if key in {"commit", "commit_sha", "sha"} and isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{7,40}", value.strip()): commits.append(value.strip())
                elif key in {"test", "test_name", "test_profile", "suite"} and value: tests.append({"name": str(value)})
                elif key in {"operation_event_id", "event_id"} and isinstance(value, int): ops.append({"event_id": value})
        return {"operation": ops or "not_verified", "test": tests or "not_verified", "commit": commits or "not_verified"}
    def query():
        with sqlite3.connect(str(ct._SESSIONS_DB)) as db:
            if wanted:
                selected = db.execute("SELECT session_id FROM sessions WHERE session_id=?", (wanted,)).fetchone(); candidates = []
            else:
                norm = ct._session_project_key(project_path)
                rows = db.execute("SELECT session_id FROM sessions WHERE project_path=? AND status='active' AND (?='' OR agent_id=?) ORDER BY started_at DESC, session_id DESC", (norm, agent_id, agent_id)).fetchall()
                selected = rows[0] if len(rows) == 1 else None; candidates = [r[0] for r in rows]
            if not selected:
                return {"error": f"Session not found: {wanted}" if wanted else "Active session scope is ambiguous or missing", "error_name": "SESSION_NOT_FOUND" if wanted else "SESSION_SCOPE_AMBIGUOUS", "session_candidates": candidates}
            sid = selected[0]
            rows = db.execute("SELECT id,tool_name,IFNULL(target,''),IFNULL(reason,''),IFNULL(error_name,''),IFNULL(correlation_type,''),IFNULL(correlation_key,''),evidence_json,host_session_id,task_epoch,prompt_hash FROM events WHERE session_id=? AND status='error' AND IFNULL(event_kind,'error')!='resolution' ORDER BY id", (sid,)).fetchall()
            out=[]; buckets={k:0 for k in ("expected","recovered","still_open","infrastructure")}
            markers=("no such table","connection refused","timeout","database is locked","backend down","service unavailable","broken pipe","outbox")
            for eid,tool,target,reason,ename,ctype,ckey,raw,host,epoch,phash in rows:
                state=db.execute("SELECT resolution_kind FROM events WHERE session_id=? AND resolves_event_id=? AND event_kind='resolution' ORDER BY id DESC LIMIT 1",(sid,eid)).fetchone()
                if state and state[0] in {"recovered","expected_failure"}: continue
                evidence=dec(raw); expected=isinstance(evidence,dict) and (evidence.get("expected_failure") is True or evidence.get("disposition")=="expected_failure")
                recovery=(db.execute("SELECT id,tool_name,correlation_type,correlation_key,evidence_json FROM events WHERE session_id=? AND id>? AND status='ok' AND IFNULL(event_kind,'')!='resolution' AND correlation_type=? AND correlation_key=? ORDER BY id LIMIT 1",(sid,eid,ctype,ckey)).fetchone() if ctype and ckey else None)
                infra=any(m in f"{ename} {reason}".lower() for m in markers)
                if expected: cls,disp,conf,kind,why="expected","expected_failure","high","expected_failure","explicit expected-failure evidence"
                elif recovery: cls,disp,conf,kind,why=("infrastructure" if infra else "recovered"),"recovered","high","recovered","later success has identical non-empty correlation"
                elif infra: cls,disp,conf,kind,why="infrastructure","still_open","medium",None,"infrastructure cause; recovery not verified"
                else: cls,disp,conf,kind,why="still_open","still_open","low",None,"no explicit resolution or strong recovery evidence"
                buckets[cls]+=1
                item={"event_id":eid,"session_id":sid,"tool":tool,"operation":target or tool,"task":{"host_session_id":host or "not_verified","task_epoch":epoch if epoch is not None else "not_verified","prompt_hash":phash or "not_verified","correlation_type":ctype or "not_verified","correlation_key":ckey or "not_verified"},"classification":cls,"cause_category":"infrastructure" if infra else "operation","disposition":disp,"evidence_links":links(evidence,recovery),"confidence":conf,"reason":why,"suggested_resolution_call":None}
                if kind:
                    item["suggested_resolution_call"]={"tool":"record_error_resolution","arguments":{"target_session_id":sid,"resolves_event_id":eid,"resolution_kind":kind,"correlation_type":ctype,"correlation_key":ckey,"evidence":{"source":"audit_reconciliation_report","operation_event_id":recovery[0] if recovery else eid}}}
                out.append(item)
            return {"session_id":sid,"read_only":True,"input_open_errors":len(out),"bucket_counts":buckets,"accounted_errors":sum(buckets.values()),"accounting_ok":len(out)==sum(buckets.values())==len({e["event_id"] for e in out}),"errors":out}
    result=await asyncio.to_thread(query)
    if result.get("error"): return _err(result["error"],error_name=result["error_name"],session_candidates=result.get("session_candidates",[]),ca_next_action="pass_exact_target_session_id")
    return _ok(result)


async def session_audit_summary(project_path: str = "", session_id: str = "",
                                target_session_id: str = "", agent_id: str = "") -> str:
    """V15 R8 B.2: aggregate session state into one structured summary.

    Returns {tools_used, mutations, validations, errors, native_fallback_count,
             pending_audit_count, open_risks, next_recommended_action}.
    CAN-054: pass target_session_id to audit a session other than the host
    transport session (session_id stays accepted for back-compat).
    """
    if target_session_id:
        session_id = target_session_id
    import sqlite3

    from code_tools import _SESSIONS_DB, _session_project_key

    if not session_id and not project_path:
        return _err("project_path or session_id required",
                    error_name="MISSING_REQUIRED_FIELD")

    norm = _session_project_key(project_path) if project_path else None

    def _resolve_scope() -> dict:
        conn = sqlite3.connect(str(_SESSIONS_DB))
        try:
            if session_id:
                row = conn.execute(
                    "SELECT session_id, project_path, agent_id FROM sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if not row:
                    return {"error": f"Session not found: {session_id}",
                            "error_name": "SESSION_NOT_FOUND"}
                return {"session_id": row[0], "project_path": row[1], "agent_id": row[2]}
            sql = (
                "SELECT session_id, project_path, agent_id FROM sessions "
                "WHERE project_path=? AND status='active'"
            )
            params: tuple = (norm,)
            if agent_id:
                sql += " AND agent_id=?"
                params = (norm, agent_id)
            sql += " ORDER BY started_at DESC, session_id DESC"
            rows = conn.execute(sql, params).fetchall()
            if not rows:
                return {"error": "No active session for project_path; pass session_id",
                        "error_name": "SESSION_NOT_FOUND"}
            if len(rows) > 1 and not agent_id:
                return {
                    "error": "Multiple active agent sessions exist; pass agent_id or target_session_id",
                    "error_name": "SESSION_SCOPE_AMBIGUOUS",
                    "session_candidates": [
                        {"session_id": row[0], "agent_id": row[2]} for row in rows[:20]
                    ],
                }
            row = rows[0]
            return {"session_id": row[0], "project_path": row[1], "agent_id": row[2]}
        finally:
            conn.close()

    resolved = await asyncio.to_thread(_resolve_scope)
    if resolved.get("error"):
        return _err(
            resolved["error"],
            error_name=resolved.get("error_name", "SESSION_NOT_FOUND"),
            session_candidates=resolved.get("session_candidates", []),
            ca_next_action=(
                "pass_agent_id_or_target_session_id"
                if resolved.get("error_name") == "SESSION_SCOPE_AMBIGUOUS"
                else "list_sessions"
            ),
        )
    sid = resolved["session_id"]
    norm = _session_project_key(resolved.get("project_path") or project_path)
    scoped_agent = resolved.get("agent_id") or agent_id or ""

    def _query() -> dict:
        conn = sqlite3.connect(str(_SESSIONS_DB))
        try:
            # Expand from the selected project session to every host task observed
            # in that session, then include sibling agent sessions from those tasks.
            scope = """(
                session_id=? OR host_session_id IN (
                    SELECT DISTINCT host_session_id FROM events
                    WHERE session_id=? AND host_session_id IS NOT NULL
                      AND host_session_id!=''
                )
            )"""
            scope_params = (sid, sid)
            tools_rows = conn.execute(
                f"SELECT tool_name, COUNT(*) FROM events WHERE {scope} GROUP BY tool_name "
                "ORDER BY 2 DESC", scope_params).fetchall()
            mut_count = conn.execute(
                f"SELECT COUNT(*) FROM events WHERE {scope} AND "
                "action_type IN ('write','workflow')",
                scope_params).fetchone()[0]
            val_count = conn.execute(
                f"""SELECT COUNT(*) FROM events WHERE {scope} AND (
                    action_type='validate' OR tool_name IN (
                        'run_project_test_script', 'run_test_profile',
                        'validate_file', 'validate_symbols', 'validate_encoding',
                        'post_write_batch_verify', 'validate'
                    )
                )""", scope_params).fetchone()[0]
            err_count = conn.execute(
                f"SELECT COUNT(*) FROM events WHERE {scope} AND status='error'",
                scope_params).fetchone()[0]
            native_count = conn.execute(
                f"SELECT COUNT(*) FROM events WHERE {scope} AND tool_origin='native'",
                scope_params).fetchone()[0]
            errors = conn.execute(
                f"SELECT tool_name, reason, created_at FROM events WHERE {scope} "
                "AND status='error' ORDER BY id DESC LIMIT 5", scope_params).fetchall()
            err_rows = conn.execute(
                """SELECT id, tool_name, IFNULL(target,''), IFNULL(reason,''),
                          IFNULL(error_owner,''), IFNULL(error_name,'')
                   FROM events WHERE """ + scope +
                " AND status='error' AND IFNULL(event_kind,'error')!='resolution' ORDER BY id",
                scope_params,
            ).fetchall()
            _CALLER_MARKERS = ("missing", "unsupported", "not found", "invalid regex",
                               "required", "unknown tool", "schema", "path not found",
                               "wrong arguments", "duplicate")
            recovered = 0
            expected_failures = 0
            caller_errors = 0
            backend_errors = 0
            owner_counts = {}
            open_list = []
            legacy_candidates = []
            for _eid, _tname, _tgt, _reason, _owner, _error_name in err_rows:
                owner = _owner or (
                    "caller" if any(marker in _reason.lower() for marker in _CALLER_MARKERS)
                    else "backend"
                )
                owner_counts[owner] = owner_counts.get(owner, 0) + 1
                if owner == "caller":
                    caller_errors += 1
                elif owner == "backend":
                    backend_errors += 1
                latest = conn.execute(
                    """SELECT resolution_kind, id FROM events
                       WHERE resolves_event_id=? AND event_kind='resolution'
                       ORDER BY id DESC LIMIT 1""",
                    (_eid,),
                ).fetchone()
                state = latest[0] if latest else "open"
                if state == "recovered":
                    recovered += 1
                elif state == "expected_failure":
                    expected_failures += 1
                elif len(open_list) < 20:
                    open_list.append({"id": _eid, "tool": _tname, "target": _tgt,
                                      "error_owner": owner, "error_name": _error_name or None,
                                      "resolution_state": state})
                # Compatibility matcher is advisory evidence only. It never changes state.
                candidate = conn.execute(
                    f"SELECT id FROM events WHERE {scope} AND tool_name=? AND "
                    "IFNULL(target,'')=? AND status='ok' AND id>? LIMIT 1",
                    (*scope_params, _tname, _tgt, _eid),
                ).fetchone()
                if candidate and len(legacy_candidates) < 20:
                    legacy_candidates.append({"error_event_id": _eid,
                                              "candidate_event_id": candidate[0],
                                              "tool": _tname, "target": _tgt})
            pending_project = conn.execute(
                "SELECT COUNT(*) FROM pending_audits WHERE project_path=? "
                "AND sync_status='pending'", (norm,)).fetchone()[0]
            legacy_pending = conn.execute(
                "SELECT COUNT(*) FROM pending_audits WHERE project_path=? "
                "AND sync_status='pending' AND agent_id IS NULL", (norm,)).fetchone()[0]
            if scoped_agent:
                scoped_pending = conn.execute(
                    "SELECT COUNT(*) FROM pending_audits WHERE project_path=? "
                    "AND sync_status='pending' AND agent_id=?",
                    (norm, scoped_agent)).fetchone()[0]
            else:
                scoped_pending = legacy_pending
            return {
                "tools_used": [{"name": r[0], "count": r[1]} for r in tools_rows],
                "mutations": mut_count,
                "validations": val_count,
                "errors": err_count,
                "recovered_errors": recovered,
                "expected_failure_errors": expected_failures,
                "open_errors": len(err_rows) - recovered - expected_failures,
                "open_error_samples": open_list,
                "error_owner_counts": owner_counts,
                "legacy_resolution_candidates": legacy_candidates,
                # Backward-compatible flattened owner fields.
                "caller_errors": caller_errors,
                "backend_errors": backend_errors,
                "native_fallback_count": native_count,
                "pending_audit_count": scoped_pending,
                "pending_audit_project_total": pending_project,
                "legacy_pending_audit_count": legacy_pending,
                "recent_errors": [
                    {"tool": e[0], "reason": (e[1] or "")[:200], "at": e[2]} for e in errors
                ],
            }
        finally:
            conn.close()

    data = await asyncio.to_thread(_query)
    open_risks = []
    if data.get("open_errors"):
        open_risks.append(
            f"{data['open_errors']} error event(s) still unresolved "
            f"({data['errors']} total, {data.get('recovered_errors', 0)} recovered)")
    if data["native_fallback_count"] > 5:
        open_risks.append(f"high native_fallback_count={data['native_fallback_count']}")
    if data["pending_audit_count"] > 0:
        open_risks.append(f"pending_audit_count={data['pending_audit_count']} — call sync_pending_audit")
    if data["legacy_pending_audit_count"] > 0:
        open_risks.append(
            f"legacy_pending_audit_count={data['legacy_pending_audit_count']} — "
            "sync only with target_session_id + include_legacy=true"
        )
    next_action = "close_session" if not open_risks else "investigate_open_risks_first"
    return _ok({
        "session_id": sid,
        "agent_id": scoped_agent or None,
        "audit_scope": "project_session_plus_matching_host_task",
        **data,
        "open_risks": open_risks,
        "next_recommended_action": next_action,
    })


async def record_session_audit_checkpoint(project_path: str = "", session_id: str = "",
                                           note: str = "") -> str:
    """CAN-053 T3 (protected): persist an audit checkpoint EVENT — the mutating
    half of the old session_audit_summary. The summary itself is now pure T1."""
    summary_raw = await session_audit_summary(project_path=project_path, session_id=session_id)
    try:
        summary = json.loads(summary_raw)
    except Exception:
        return summary_raw
    if summary.get("error"):
        return summary_raw
    sid = summary.get("session_id") or session_id
    from code_tools import _log_session_event
    compact = {k: summary.get(k) for k in (
        "mutations", "validations", "errors", "recovered_errors", "open_errors",
        "caller_errors", "backend_errors", "native_fallback_count") if k in summary}
    # Anh 2026-08-11 (Phase A on-loop audit): _log_session_event goes through the
    # SessionWriter and blocks on a durable-commit Future; run it OFF the loop.
    ev_id = await asyncio.to_thread(
        _log_session_event, sid, "session_audit_checkpoint", {"note": (note or "")[:200]},
        {"ok": True, "checkpoint": compact}, 0, "ok", "")
    return _ok({"session_id": sid, "checkpoint_event_id": ev_id,
                "checkpoint": compact, "note": (note or "")[:200]})


# N2 (2026-09-24): `tool` names ONE callable tool and `also` the companions of the
# same intent. The composite strings 18 of these 26 rows carried ("find_symbol(include=
# all) OR search_code(mode=smart)", "Edit + log_native_event") came back verbatim as
# `recommended_tool`, which a CA reads as a name to call. How to call a tool is its
# schema's job; a row states which tool fits.
_DECISION_RULES = [
     # R56 semantic routing: schema/gotcha-specific hints must precede broad rules
     {"re": r"\b(bundle.?version|marker.?regex|capture.?group)\b",
      "tier": "T4c", "tool": "bundle_version_check",
      "skill": "16_server_lifecycle",
      "why": "Use schema-guided capture-group validation before retry."},
     {"re": r"\b(page.?id|baseline|dom.?state|stale.?baseline)\b",
      "tier": "T4", "tool": "dom_state_diff",
      "also": ("checkpoint_loop_dom_state",),
      "skill": "18_runtime_dom_T4",
      "why": "Require live pg-* page IDs and a fresh baseline after restart/page replacement."},
     {"re": r"\b(playwright|python.?fixture|node.?fixture|module.?not.?found)\b",
      "tier": "T2 + T4", "tool": "run_playwright_test",
      "also": ("run_project_test_script",),
      "skill": "09_testing_validation",
      "why": "Match runner to fixture language and check the dependency preflight."},
     {"re": r"\b(copy.?file|dst.?exists|overwrite|backup.?existing)\b",
      "tier": "T2", "tool": "copy_file",
      "skill": "05_write_pipeline",
      "why": "Use a new destination or overwrite=true with backup_existing=true after DST_EXISTS."},
     {"re": r"\b(draft.?patch|line.?drift|syntax.?pre.?write)\b",
      "tier": "T2", "tool": "write_draft_patch",
      "skill": "05_write_pipeline",
      "why": "Use draft_id and deterministic replacement content; never retry a stale range blindly."},
     {"re": r"\b(utf.?8|base64|btoa|powershell)\b",
      "tier": "T2", "tool": "write_new_file",
      "also": ("run_project_test_script",),
      "skill": "05_write_pipeline + 08_command_windows",
      "why": "Encode UTF-8 explicitly; do not assume btoa exists in the host runtime."},
    # --- W0 (SYNTRUX-AU1/AU2, AUTO_USE_MATRIX muc 10): CBM quality tools — cụ thể hơn CAN-053 nên phải đứng trước ---
    {"re": r"\b(circular.?(dep\w*|import\w*|ref\w*|dependenc\w*)|cyclic\w*|\bcycles\b|import.?cycles?|vong.?phu.?thuoc|vong.?lap.?phu.?thuoc|tarjan|kosaraju|\bscc\b|leveliz\w*|upward.?violat\w*|quality.?signal|chat.?luong.?kien.?truc|kien.?truc.?chat.?luong|codebase.?health|arch\w*health)\b",
     "tier": "T2", "tool": "cbm_cycle_point",
     "skill": "15_search_code_intel",
     "why": "graph-first: cycles (Tarjan SCC), levelization, geometric-mean quality_signal — 1 call cbm_cycle_point trên graph.db"},
    {"re": r"\b(dead.?code|dead.?call\w*|uncalled\w*|unused.?(code|func\w*|method\w*|symbol|class|callable\w*)|code.?chet|ham.?chet|khong.?(duoc|ai).?(su.?dung|goi))\b",
     "tier": "T2", "tool": "cbm_dead_code",
     "skill": "15_search_code_intel",
     "why": "graph-first: unreferenced callables + framework-dispatch suppression trong 1 call cbm_dead_code"},
     # --- H1 2026-10-08: EOL conversion must NOT route to write_fix (write_fix auto-preserves EOL per contract). Full-file rewrite via write_new_file. ---
     {"re": r"\b(eol|lf|crlf|line.?ending|newline)s?\b.{0,50}\b(convert|change|chuyen|doi)\b|\b(convert|change|chuyen|doi)\b.{0,50}\b(eol|lf|crlf|line.?ending|newline)s?\b",
      "tier": "T2", "tool": "write_new_file",
      "skill": "05_write_pipeline",
      "why": "EOL conversion needs full-file rewrite; write_fix auto-preserves EOL per its contract."},
     # --- W0b (AU3/AU4): fix route nhầm của lexical fallback (probe D/F 07/09) ---
     {"re": r"(?!\b.*\bno code changes\b)\b(sua|fix|edit|chinh.?sua)\b[^.]{0,40}\b(file|tap.?tin|ham|function|method|class|loi|bug|dong|line|code)\b",
     "tier": "T2", "tool": "write_fix",
     "skill": "05_write_pipeline",
     "why": "edit existing file = write_fix (line-range edit); draft_patch chỉ dành cho draft proposal"},
    # STT-27: source-lookup guard — "search screenshot function in source" is a
    # code-search intent, not a UI snapshot. Mirrors the _source_lookup
    # suppression in _campaign_routing_prepass: when a find/search verb AND a
    # source-context word co-occur anywhere in the query, skip this rule so the
    # find/search rule below wins. Two anchored lookaheads at ^ = AND.
    {"re": r"^(?!(?=.*\b(?:find|search|tim|lookup)\b)(?=.*\b(?:source|code|function|symbol|trong)\b))\b(chup.{0,15}(man.?hinh|screen)|screenshot|screen.?capture|anh.?man.?hinh)\b",
     "tier": "T4a", "tool": "desktop_screenshot",
     "skill": "00_skill_router",
     "why": "UI snapshot = desktop_screenshot (disk path); không phải DOM tools"},
    # CAN-053 (codo P1): architecture/dependency/entrypoint/blast-radius intents
    # must hit CBM graph tools BEFORE broad find/search rules shadow them.
    {"re": r"\b(architecture|kien.?truc|module.?dep\w*|dependenc\w+|phu.?thuoc|entry.?points?|blast.?radius|impact.?scope|data.?flow|component.?map|nam.*kien.?truc|so.?do.*(module|he.?thong))\b",
     "tier": "T1", "tool": "cbm_get_architecture",
     "also": ("cbm_template_query", "cbm_graph_status", "cbm_trace_path"),
     "skill": "15_search_code_intel",
     "why": "CAN-053 graph-first: CBM tools answer architecture/dependency/entrypoint/impact questions in one bounded call; raw find/search cannot."},
    # R20: click+watch combo — recommend atomic macro instead of separate calls
    {"re": r"\b(click).*\b(watch|poll|wait|monitor|state|change|toggle|transition)\b",
     "tier": "T4", "tool": "click_and_checkpoint_loop",
     "also": ("click_selector", "checkpoint_loop_dom_state"),
     "skill": "18_runtime_dom_T4",
     "why": "R16.10 atomic macro: baseline pre-click + click + poll until state change. Saves 3+ round-trips vs manual sequence."},
    # R20: log/error capture — recommend artifact mode for S/L (placed BEFORE generic DOM rules)
    {"re": r"\b(extract|read|get|capture|save|fetch|dump).*\b(log|error|system.?error|trace|stack)\b",
     "tier": "T4 + T2", "tool": "save_log_snapshot",
     "also": ("extract_log_section", "read_value"),
     "skill": "17_session_audit + 18_runtime_dom_T4",
     "why": "R17 save_log_snapshot writes log to .orchestrator_cache/artifacts/<round_id>/<slug>.txt; final cites path+sha256+head/tail_200 instead of pasting 8K log → ~90% S/L saving."},
    {"re": r"\b(log|error|system.?error|trace|stack).*\b(extract|read|get|capture|save|fetch|dump)\b",
     "tier": "T4 + T2", "tool": "save_log_snapshot",
     "also": ("extract_log_section", "read_value"),
     "skill": "17_session_audit + 18_runtime_dom_T4",
     "why": "R17 save_log_snapshot writes log to artifacts; cite path+sha256 instead of pasting 8K log → ~90% S/L saving."},
    # R16.8: Electron/CDP-specific rule comes FIRST (more specific than generic UI rule)
    # R43.3 H8: removed `rawcut` (project-specific) — generic terms (electron/cdp/9223/launch app) are sufficient
    {"re": r"\b(electron|cdp|remote.?debugging|9223|launch.*app|attach.*electron)\b",
     "tier": "T4", "tool": "launch_electron_with_cdp",
     "also": ("cdp_attach_target", "click", "fill_value", "read_value"),
     "skill": "22_electron_automation + 18_runtime_dom_T4",
     "why": "Electron renderer rejects page-level WS; use browser-level WS fallback via launch_electron_with_cdp."},
    {"re": r"\b(textarea|read.*log|extract.*log|view.?panel|button.*state|fill.*input|dom.*diff)\b",
     "tier": "T4", "tool": "read_all_logs",
     "also": ("read_value", "extract_log_section", "fill_value", "dom_state_diff"),
     "skill": "18_runtime_dom_T4",
     "why": "R16+ DOM helpers replace evaluate_js boilerplate — saves 60-80% context."},
    {"re": r"\b(ui|dom|browser|render|click|button|css)\b",
     "tier": "T4", "tool": "open_browser_page",
     "also": ("launch_electron_with_cdp", "click", "read_page", "query_selector"),
     "skill": "18_runtime_dom_T4",
     "why": "UI/runtime visible only at runtime. Prefer Electron-specific tools when target is Electron app."},
    {"re": r"\b(port|server|stale|cache|admin|restart|pid|host)\b",
     "tier": "T4c", "tool": "server_lifecycle_status",
     "also": ("port_owner_inspect",),
     "skill": "16_server_lifecycle",
     "why": "2+ failed code cycles + unchanged runtime → check lifecycle first."},
    {"re": r"\b(auth|token|password|credential|secret|delete|migration|schema)\b",
     "tier": "T2 [A]", "tool": "write_fix",
     "also": ("apply_after_dry_run",),
     "skill": "05_write_pipeline + 06_token_auth",
    "why": "Critical path: governance > speed."},
    {"re": r"\b(callers|usage|find|search|lookup|where is|symbol)\b",
     "tier": "T1", "tool": "find_symbol",
     "also": ("search_code",),
     "skill": "15_search_code_intel",
     "why": "Read-only; indexed lookup saves context vs grep."},
    {"re": r"\b(session|audit|handoff|close|summary|log)\b",
     "tier": "T3", "tool": "open_session",
     "also": ("log_native_event", "close_session"),
     "skill": "17_session_audit",
     "why": "Audit trail required for governance."},
    # R43.3 M7: missing coverage — folder dialog, workflow dispenser, invoke_app_function
    {"re": r"\b(folder.?dialog|file.?dialog|pick.?folder|browse.?folder|select.?folder|native.?dialog)\b",
     "tier": "T4", "tool": "fill_folder_dialog",
     "also": ("folder_dialog_snapshot", "click_and_handle_folder_dialog", "cancel_native_dialog"),
     "skill": "18_runtime_dom_T4",
     "why": "Native picker dialogs need owner_pid + dialog class filter. Snapshot first if ambiguous."},
    {"re": r"\b(workflow|step.?\d|next.?step|advance.?step|workflow.?dispenser|step.?manifest)\b",
     "tier": "T1+T3", "tool": "workflow_list",
     "also": ("workflow_start", "workflow_get_current", "workflow_submit_step", "workflow_goto_step"),
     "skill": "26_workflow_ingestion",
     "why": "MD-driven workflow runner (R35+) is the canonical multi-step orchestrator."},
    {"re": r"\b(invoke.?app|call.?renderer|call.?electron.?api|app.?function|window\.app\.|executejs.?app)\b",
     "tier": "T4", "tool": "invoke_app_function",
     "skill": "22_electron_automation",
     "why": "Direct renderer-side function call when click doesn't open expected dialog (custom Electron UI)."},
    {"re": r"\b(refactor|across.*files|migrate|atomic|rollback|multi.?file|batch)\b",
     "tier": "T2 [A]", "tool": "write_transaction",
     "also": ("post_write_batch_verify",),
     "skill": "05_write_pipeline",
     "why": "Multi-file: [A] rollback essential."},
    {"re": r"\b(typo|comment|css|color|1 line|small|tweak|simple)\b",
     "tier": "T2", "tool": "write_fix",
     "skill": "05_write_pipeline",
     "why": "A small edit is still one write_fix region: it carries the read_token, the "
            "backup and the auto-verify that a native edit skips."},
]


def _rule_names(rule: dict) -> list:
    """The callable tools a decision row names: `tool` first, then `also` (N2)."""
    return list(dict.fromkeys([rule["tool"], *rule.get("also", ())]))


_WT_VN_HINTS = (
    # (regex on diacritics-folded Vietnamese intent, English tool-vocabulary to append)
    (r"sua|chinh|patch|thay the|viet lai|vá", "edit fix write replace patch line range file"),
    (r"tao file|file moi|viet file", "create new file write"),
    (r"doc file|xem noi dung|doc dong", "read file range content"),
    (r"tim|quet|scan|dinh vi|dem", "search find grep locate symbol code"),
    (r"thu muc|cau truc|cay ", "folder structure tree list project"),
    (r"phien lam viec|mo phien|session", "session open close list"),
    (r"hoi dong|worker|gui tin|nhan tin|dieu phoi|dispatch", "council message dispatch worker model convene"),
    (r"nhiem vu dai|mission", "mission create dispatch status gate"),
    (r"sao luu|backup", "backup project status"),
    (r"khoi dong lai|restart", "restart orchestrator lifecycle server bat"),
    (r"man hinh|chup anh|screenshot|giao dien|cua so", "screenshot desktop page describe window"),
    (r"kiem tra|xac minh|verify|validate", "validate verify check symbols file"),
    (r"xoa file|xoa thu muc", "delete remove file"),
    (r"chay lenh|thuc thi|run |test", "run execute test script command project"),
    (r"rule|skill|dang ky|thao bo|agent config", "rule skill agent register unregister manage sync mode"),
    (r"token|quyen|bao mat", "token protected auth status"),
    (r"tri nho|bo nho|memory|ghi nho", "memory recall capture librarian"),
    (r"loi |error|log|nhat ky", "error log console read all"),
    (r"do thi|graph|phu thuoc", "graph import callers architecture"),
)


def _wt_vn_fold(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.replace("đ", "d").replace("Đ", "D").casefold()


def _wt_vn_expand(task: str) -> str:
    """Fold Vietnamese diacritics and append English tool-vocabulary for every
    matched Vietnamese intent phrase, so both the EN decision rules and the
    lexical ranker work on Vietnamese task descriptions."""
    folded = _wt_vn_fold(task)
    extra = [en for vn_pat, en in _WT_VN_HINTS if re.search(vn_pat, folded)]
    return folded + ((" " + " ".join(extra)) if extra else "")


def _wt_campaign_fold_raw(text: str) -> str:
    """R2 C: lowercase accent-folded RAW (d-stroke -> d) + whitespace normalize.
    No vocabulary expansion -- expanded tokens must not create new intent.
    """
    return re.sub(r"\s+", " ", _wt_vn_fold(text)).strip()


def _campaign_quality_has_code_context(folded: str) -> bool:
    return bool(re.search(
        r"\b(architecture|kien[ _-]?truc|code|cbm|commit|dependenc|"
        r"module|graph|scc|cycle|import)\b",
        folded or "",
    ))


def _campaign_suppress_write_fix(folded: str) -> bool:
    """R2 rule-7 exclusion: report/prose/wording/bao cao is not a code edit.
    H3 2026-10-08: explicit 'no code changes' also suppresses write_fix."""
    if re.search(r"\b(sua|fix|edit|chinh[ _-]?sua)\b", folded or "") and re.search(
        r"\b(report|prose|wording|bao[ _-]?cao)\b", folded or ""):
        return True
    # H3: task explicitly says no code changes — write_fix is wrong
    if re.search(r"\bno code changes\b", folded or ""):
        return True
    return False


def _campaign_routing_prepass(folded_raw: str, live_tools: set) -> list:
    """R2 C ordered semantic rules on folded RAW, before expand/CAN-053.

    First match is the campaign primary; later matches are multi-intent
    alternates. A rule is omitted when its exclusion fires.
    cbm_quality_gate is claimed only when already registered (P4+).
    """
    if not folded_raw:
        return []
    live = live_tools or set()
    out = []

    def _add(tool, tier, skill, why):
        out.append({
            "re": "",
            "tier": tier,
            "tool": tool,
            "skill": skill,
            "why": why,
        })

    if re.search(
        r"\b(draft[ _-]?patch|syntax[ _-]?pre[ _-]?write|draft[ _-]?syntax)\b",
        folded_raw,
    ):
        _add("write_draft_patch", "T2", "05_write_pipeline",
             "R2 C1: draft-patch / syntax-pre-write repair, not a general edit.")
    if re.search(
        r"\b(run[ _-]?tests?|chay[ _-]?test|pytest|unittest)\b",
        folded_raw,
    ):
        _add("run_project_test_script", "T2", "09_testing_validation",
             "R2 C2: run-tests / chay-test / pytest / unittest.")
    if re.search(
        r"\b(tao|create|new)\b.{0,60}\b(file|module)\b|\bnew[ _-]?file\b",
        folded_raw,
    ):
        _add("write_new_file", "T2", "05_write_pipeline",
             "R2 C3: create/new file or module, not a line edit.")
    if (re.search(r"\bcbm_quality_gate\b", folded_raw)
            and "cbm_quality_gate" in live):
        _add("cbm_quality_gate", "T1", "15_search_code_intel",
             "R2 C4: explicit cbm_quality_gate after P4 registration.")
    if (
        (
            re.search(r"\bcbm_what_if\b", folded_raw)
            or re.search(
                r"\b(what[ _-]?if|gia[ _-]?lap|break[ _-]?cycle)\b",
                folded_raw,
            )
        )
        and "cbm_what_if" in live
    ):
        _add("cbm_what_if", "T2", "15_search_code_intel",
             "P5 H1: explicit what-if / gia lap / break cycle after registration.")
    if (
        (
            re.search(r"\bcbm_evolution\b", folded_raw)
            or re.search(
                r"\b(evolution|churn|co[ _-]?change|bus[ _-]?factor|"
                r"lich[ _-]?su[ _-]?git|git[ _-]?history)\b",
                folded_raw,
            )
        )
        and "cbm_evolution" in live
    ):
        _add("cbm_evolution", "T1", "15_search_code_intel",
             "P6 H1: explicit evolution / churn / bus-factor after registration.")
    if (
        (
            re.search(r"\bcbm_dsm\b", folded_raw)
            or re.search(
                r"\b(dsm|design[ _-]?structure[ _-]?matrix|"
                r"propagation[ _-]?cost|ma[ _-]?tran[ _-]?phu[ _-]?thuoc)\b",
                folded_raw,
            )
        )
        and "cbm_dsm" in live
    ):
        _add("cbm_dsm", "T1", "15_search_code_intel",
             "P6 H1: explicit dsm / design-structure-matrix after registration.")
    if (
        (
            re.search(r"\bcbm_test_gap\b", folded_raw)
            or re.search(
                r"\b(test[ _-]?gap|uncovered[ _-]?(files?|modules?)|"
                r"khoang[ _-]?trong[ _-]?test)\b",
                folded_raw,
            )
        )
        and "cbm_test_gap" in live
    ):
        _add("cbm_test_gap", "T2", "15_search_code_intel",
             "P3 H1: explicit test-gap / uncovered files; run-tests still wins first.")
    if re.search(
        r"\b(dead[ _-]?code|dead[ _-]?callables?|"
        r"unused[ _-]?(code|functions?|methods?)|"
        r"ham[ _-]?chet|code[ _-]?chet)\b",
        folded_raw,
    ):
        _add("cbm_dead_code", "T2", "15_search_code_intel",
             "R2 C5: dead code / ham chet, before CAN-053 and find/search.")
    _strong_cycle = re.search(
        r"\b(circular[ _-]?(dependency|dependencies|imports?)|"
        r"cyclic|cycles?|scc|tarjan|kosaraju)\b",
        folded_raw,
    )
    _generic_quality = re.search(r"\b(quality|chat[ _-]?luong)\b", folded_raw)
    if _strong_cycle or (
        _generic_quality
        and _campaign_quality_has_code_context(folded_raw)
    ):
        _add("cbm_cycle_point", "T2", "15_search_code_intel",
             "R2 C6: cycle / architecture quality, before CAN-053.")
    if (
        re.search(
            r"\b(sua|fix|edit|chinh[ _-]?sua)\b.{0,60}"
            r"\b(ham|function|method|class|code|file|line|dong)\b",
            folded_raw,
        )
        and not _campaign_suppress_write_fix(folded_raw)
    ):
        _add("write_fix", "T2", "05_write_pipeline",
             "R2 C7: targeted code/file edit; draft/test/create win first.")
    if re.search(
        r"\b(chup.{0,15}man[ _-]?hinh|screenshot|screen[ _-]?capture)\b",
        folded_raw,
    ):
        _source_lookup = (
            re.search(r"\b(find|search|tim|lookup)\b", folded_raw)
            and re.search(
                r"\b(source|code|function|symbol|trong)\b",
                folded_raw,
            )
        )
        if not _source_lookup:
            _add("desktop_screenshot", "T4", "18_runtime_dom_T4",
                 "R2 C8: chup man hinh / screenshot, not a source snapshot.")
    # N2 (2026-09-24): graph.db MAINTENANCE had no route. "build or refresh the cbm
    # code graph index" fell to CAN-053 (expansion reads `graph` as architecture) and
    # got read-only graph tools, so the step that makes a graph answer true never
    # surfaced. The noun must be the cbm graph: a bare `index` is the search_code
    # index (STALE_INDEX -> cache_delta), and a bare `graph` counts only with a
    # maintenance-only verb, so "build the dependency graph" stays a structure question.
    _cbm_graph = (r"(cbm|graph[ ._-]?db|(code|call)[ _-]?graph|graph[ _-]?index|"
                  r"do[ _-]?thi[ _-]?code)")
    _any_graph = r"(cbm|graph|do[ _-]?thi)"
    _maintain = (r"(rebuild|re[ _-]?build|refresh|re[ _-]?index|reindex|regenerate|"
                 r"resync|update|cap[ _-]?nhat|tao[ _-]?lai|lam[ _-]?moi|index[ _-]?lai)")
    _build = r"(build|sync|index|xay[ _-]?dung|dong[ _-]?bo)"
    _why_incremental = ("N2: brings graph.db up to date from source hashes; on an empty "
                        "graph every file counts as added, so it is also the first build.")
    if (
        re.search(r"\bcbm_(incremental_)?index\b", folded_raw)
        or re.search(rf"\b{_maintain}\b[^.,;]{{0,30}}\b{_any_graph}\b", folded_raw)
        or re.search(rf"\b{_build}\b[^.,;]{{0,30}}\b{_cbm_graph}\b", folded_raw)
        or re.search(rf"\b{_cbm_graph}\b[^.,;]{{0,12}}\b({_maintain}|{_build})\b",
                     folded_raw)
    ):
        if "cbm_incremental_index" in live:
            _add("cbm_incremental_index", "T2", "15_search_code_intel", _why_incremental)
        if "cbm_index" in live:
            _add("cbm_index", "T2", "15_search_code_intel",
                 "N2: full (re)index of a chosen scope into graph.db.")
    _fresh = (r"(stale|fresh|freshness|outdated|out[ _-]?of[ _-]?date|up[ _-]?to[ _-]?date|"
              r"loi[ _-]?thoi|moi[ _-]?nhat)")
    if (
        re.search(rf"\b{_fresh}\b[^.,;]{{0,30}}\b{_any_graph}\b", folded_raw)
        or re.search(rf"\b{_any_graph}\b[^.,;]{{0,30}}\b{_fresh}\b", folded_raw)
    ):
        if "cbm_graph_status" in live:
            _add("cbm_graph_status", "T1", "15_search_code_intel",
                 "N2: reports whether graph.db is current against the source, before "
                 "a graph answer is trusted.")
        if "cbm_incremental_index" in live:
            _add("cbm_incremental_index", "T2", "15_search_code_intel", _why_incremental)
    # A36 (2026-09-19): three LOWEST-priority rows, appended after every existing
    # rule so they can never shadow a campaign primary - they only catch the
    # phrasings that used to fall through to a bulk read. The gap they close:
    # a CA facing a LARGE repo (ToolA itself is one) says "I need to understand
    # this repo" and nothing routed it to the graph, so it read source by the
    # thousand lines. CSLt order = structure from the index first, then the
    # smallest read that answers the question.
    if (
        re.search(
            r"\b(hieu|nam|onboard|lam[ _-]?quen|tim[ _-]?hieu|overview|"
            r"understand|understanding|familiar|familiarise|familiarize|grasp|"
            r"orient|tong[ _-]?quan|walk[ _-]?through|where[ _-]?to[ _-]?start)\b",
            folded_raw,
        )
        and re.search(
            r"\b(repo|repository|codebase|code[ _-]?base|project|du[ _-]?an|"
            r"source|he[ _-]?thong)\b",
            folded_raw,
        )
        and "cbm_get_architecture" in live
    ):
        _add("cbm_get_architecture", "T2", "15_search_code_intel",
             "R2 C9: understand/onboard a repo - module graph + entrypoints from "
             "the index, before any bulk read (CSLt: less context).")
    if (
        re.search(
            r"\b(doc[ _-]?het|doc[ _-]?toan[ _-]?bo|"
            r"read[ _-]?(the[ _-]?)?(all|every|whole|entire)|"
            r"quet[ _-]?toan[ _-]?bo|scan[ _-]?(the[ _-]?)?(all|whole|entire)|"
            r"liet[ _-]?ke[ _-]?het|dump)\b",
            folded_raw,
        )
        and re.search(
            r"\b(file|files|code|repo|codebase|module|source|thu[ _-]?muc|"
            r"folder|directory)\b",
            folded_raw,
        )
        and "cbm_graph_status" in live
    ):
        _add("cbm_graph_status", "T1", "15_search_code_intel",
             "R2 C10: whole-repo read/scan request - check index freshness, then "
             "cbm_get_architecture / cbm_dsm answer structure WITHOUT reading "
             "source; bounded read_file_range only on what they name.")
    if re.search(
        r"\b(ai[ _-]?goi|who[ _-]?calls?|callers?|noi[ _-]?goi|call[ _-]?sites?|"
        r"duoc[ _-]?goi[ _-]?o[ _-]?dau)\b",
        folded_raw,
    ):
        _add("search_code", "T1", "15_search_code_intel",
             "R2 C11: who-calls / ai goi -> search_code(mode='callers'), NOT the "
             "deprecated find_callers. It sees instance receivers (self.fn() / "
             "obj.fn()), so it is the cross-check before trusting a cbm_dead_code "
             "'no callers' verdict - the cbm graph resolves only imported-alias "
             "edges, and an empty cbm list means unresolved, never unused.")
    return out


def _deprecated_tool_names() -> frozenset:
    """Tools the server itself marks deprecated (orchestrator_server._DEPRECATED_TOOLS;
    code_tools flags its own through the schema's `deprecated` key). Machine-readable
    marks only, never description prose. Measured 2026-09-24: "find callers of foo"
    offered the deprecated alias find_callers as an alternate, because the ranker
    scores a name segment the query repeats."""
    try:
        from orchestrator_server import _DEPRECATED_TOOLS as _dep
    except Exception:
        return frozenset()
    return frozenset(_dep)


def _wt_rank_lexical(task_expanded: str, top_k: int = 3) -> list:
    """Score every live tool by token overlap between the (VN-expanded) intent
    and the tool's name + description from the live TOOL_SCHEMAS — no hardcoded
    catalog, so new tools are rankable the moment they are registered."""
    try:
        import code_tools as _ct
        schemas = getattr(_ct, "TOOL_SCHEMAS", [])
        phases = getattr(_ct, "TOOL_PHASES", {})
    except Exception as _e:
        # Fail loud (was silent return []): A07 guard depends on this ranker.
        import logging as _lg
        _lg.getLogger(__name__).warning("_wt_rank_lexical: code_tools import failed: %r", _e)
        return []
    tokens = {w for w in re.findall(r"[a-z0-9_]+", task_expanded) if len(w) >= 3}
    if not tokens:
        return []
    scored = []
    deprecated = _deprecated_tool_names()
    for sch in schemas:
        fn = sch.get("function", {})
        name = fn.get("name") or ""
        if not name or fn.get("deprecated") or name in deprecated:
            continue
        name_words = set(name.split("_"))
        desc = _wt_vn_fold(str(fn.get("description") or "")[:400])
        score = 0.0
        for tok in tokens:
            if tok == name:
                score += 5
            elif tok in name_words:
                score += 3
            elif tok in name:
                score += 1.5
            if tok in desc:
                score += 1
        if score > 0:
            scored.append((score, name, fn, phases.get(name, "T1")))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[:top_k]


def _a07_should_defer(raw_task: str, rule: dict, lex_ranked: list) -> bool:
    """A07 stage-1/stage-2 boundary: a broad decision rule that fired on a
    SINGLE standalone query word must not shadow a tool whose multi-segment
    name the query spells out (e.g. `audit` alone winning over
    `audit_tool_matrix` for the query "audit tool matrix").

    Defer to the lexical ranker only when the ranker's top-1 tool is named
    MORE SPECIFICALLY by the query than the winning rule is:

      hits    = distinct RAW query words matching the winning rule's regex
      overlap = distinct RAW query words that are underscore segments of the
                lexical top-1 tool name
      defer  <=> overlap > hits

    Both sides are counted over the SAME word set, so this is a like-for-like
    comparison of evidence, and it is scale-free: neither side can be inflated
    by the user simply writing a longer sentence.

    An absolute `hits == 1` gate cannot express that. It reads a query's LENGTH
    as rule confidence: "audit tool matrix" scores hits=1 (defers, correct) but
    "audit tool matrix for this session" scores hits=2 on the very same rule
    -- the extra word `session` matches the same broad session regex -- so the
    guard refuses and the original A07 bug returns verbatim on the longer
    phrasing. `overlap > hits` keeps deferring there (3 > 2) because the query
    still spells out `audit_tool_matrix` more completely than it hits the rule.

    RAW (not the VN-expanded text) is deliberate on the overlap side: VN
    expansion injects tool-vocabulary the user never typed, which would
    inflate the overlap and mis-fire on queries like "open a work session".

    Genuinely thick rule matches are unaffected -- they raise `hits` on the
    same scale: "session audit handoff" and "close session summary" both reach
    hits=3 against overlap=2 and stay with stage 1, as does the bare "audit"
    (1 vs 1: a tie is not evidence of a better match, so the rule keeps it).
    """
    words = {w for w in re.findall(r"[a-z0-9_]+", raw_task or "") if w}
    hits = sum(1 for w in words if re.search(rule["re"], w))
    if not hits or not lex_ranked:
        return False
    top_name = lex_ranked[0][1]
    return len(words & set(top_name.split("_"))) > hits


def _a07_name_set(names) -> set:
    """Tool names as a set, from a space-joined string or any iterable of names.
    Exact membership: a substring test counts `write_fix` as shown when only
    `write_fix_html` is."""
    if isinstance(names, str):
        return set(names.split())
    return {str(n) for n in (names or ()) if n}


def _a07_named_tool_alternate(raw_task: str, lex_ranked: list, shown) -> list:
    """A07, second reader. `_a07_should_defer` only rescues the case where ONE
    rule fired; when two or more fire, `_matched[1:3]` fills `alternates` with
    rule siblings and the escape hatch never runs -- so a tool the query names
    outright can vanish from the answer entirely.

    "audit tool matrix and log the session" is that case: `audit` and `log` are
    two genuine intents, so keeping the session bundle primary is correct, but
    `audit_tool_matrix` -- three of whose three name segments the user typed --
    appeared in neither the primary nor the alternates. Deferring would be
    wrong there; staying silent about the named tool is also wrong.

    Returns at most one entry: the lexical top-1, only when the RAW query names
    at least 2 of its segments and it is not already visible in `shown`.
    """
    if not lex_ranked:
        return []
    words = {w for w in re.findall(r"[a-z0-9_]+", raw_task or "") if w}
    name = lex_ranked[0][1]
    if len(words & set(name.split("_"))) < 2 or name in _a07_name_set(shown):
        return []
    return _a07_alternates(lex_ranked[:1], exclude="", limit=1)


def _a07_alternates(lex_ranked: list, exclude, limit: int = 2) -> list:
    """A07 escape hatch: even a correct stage-1 primary must surface a second
    option. When the rule-based `_matched[1:3]` slice is empty (only one rule
    fired), fill `alternates` from the lexical ranker so the caller always has
    a fallback. Tools already named in the primary recommendation are skipped."""
    ex = _a07_name_set(exclude)
    out = []
    for _score, name, fn, tier in lex_ranked:
        if name in ex:
            continue
        why = re.sub(r"\s+", " ", str(fn.get("description") or ""))[:120]
        out.append({"tool": name, "tier": tier,
                    "why": why or "lexical rank over live TOOL_SCHEMAS"})
        if len(out) >= limit:
            break
    return out


def _wt_q_z2_refs(task: str) -> list:
    """Ask [Q] Z2 (compact) for memory about which tool fits — fail-open, 3s.
    Returns [] when [Q] is down/slow/empty so the local rank stands alone."""
    try:
        import urllib.request as _ur
        body = json.dumps({"agent_id": "A:tool_hint", "query": str(task)[:300],
                           "top_k": 3, "zones": ["Z2"], "format": "ca"}).encode("utf-8")
        req = _ur.Request("http://127.0.0.1:8082/query", data=body,
                          headers={"Content-Type": "application/json"})
        resp = json.loads(_ur.urlopen(req, timeout=3).read().decode("utf-8", "replace"))
        return (resp.get("hits") or [])[:3] if resp.get("ok") else []
    except Exception:
        return []


async def tool_decision_hint(task_description: str = "", scope_hint: str = "",
                              risk_hint: str = "", task: str = "",
                              verbosity: str = "full") -> str:
    """V15 R8 B.2: server-side rule engine matching task description → tier+skill.

    V15 R16.6: accepts both `task_description` (canonical) and `task` (alias).
    V15 R22: `verbosity` ∈ {full|terse|ref} — terse drops rationale + tools_validation
    + payload_example schemas; saves ~550 tokens/call. Default full (back-compat).
    Mirrors UI Decision Hint panel (V15 R7).
    """
    effective = (task_description or task or "").lower().strip()
    if not effective:
        return _err("task_description required (alias `task` also accepted)",
                    error_name="MISSING_REQUIRED_FIELD",
                    accepted_fields=["task_description", "task", "scope_hint", "risk_hint"],
                    accepted_aliases={"task": "task_description"})
    task = effective
    # R16.9: live TOOL_MAP cross-check — flag any rule-mentioned tool name
    # that doesn't exist in current build (drift detector for decision rules)
    try:
        import code_tools as _ct
        live_tools = set(_ct.TOOL_MAP.keys())
    except Exception:
        live_tools = set()

    def _validate_tools(names: list) -> dict:
        # N2: rows name callable tools, so this is membership. The CAN-053 regex
        # over a prose string read `(artifact)` / `mode='ref'` as tool ids.
        checked = [n for n in dict.fromkeys(names) if n]
        missing = [n for n in checked if live_tools and n not in live_tools]
        present = [n for n in checked if n in live_tools]
        return {"checked": checked, "present": present, "missing": missing,
                "inconclusive": not checked}

    # R16.10: per-tool payload examples — CA needs field schema, not just names
    def _examples_for(tool_names: list) -> dict:
        try:
            from orchestrator_server import _PAYLOAD_EXAMPLES as _PE
        except Exception:
            _PE = {}
        out = {}
        for tn in tool_names:
            ex = _PE.get(tn)
            if ex is not None:
                # shallow copy to avoid mutation
                out[tn] = {k: v for k, v in ex.items() if not k.startswith("_")}
            elif tn in live_tools:
                # Fallback: try to introspect required fields from TOOL_SCHEMAS
                try:
                    import code_tools as _ct2
                    for s in getattr(_ct2, "TOOL_SCHEMAS", []):
                        fn = s.get("function", {})
                        if fn.get("name") == tn:
                            req = fn.get("parameters", {}).get("required", []) or []
                            props = fn.get("parameters", {}).get("properties", {})
                            stub = {}
                            for r in req:
                                p = props.get(r, {})
                                if p.get("type") == "string":
                                    stub[r] = p.get("default", "<value>")
                                elif p.get("type") == "integer":
                                    stub[r] = p.get("default", 0)
                                elif p.get("type") == "boolean":
                                    stub[r] = p.get("default", False)
                                else:
                                    stub[r] = p.get("default", "<value>")
                            if stub:
                                out[tn] = stub
                            break
                except Exception:
                    pass
        return out

    # R2 C: semantic prepass on lowercase accent-folded RAW before
    # vocabulary expansion and before CAN-053 / find/search. Do not inject
    # these regexes into _DECISION_RULES -- a raw broad match can beat a
    # folded specific match. A07 must not revoke a confirmed campaign primary.
    folded_raw = _wt_campaign_fold_raw(task)
    _campaign = _campaign_routing_prepass(folded_raw, live_tools)
    # Exact LOOK intents must never fall through to lexical write-tool matches.
    look_tool = ""
    if not _campaign:
        if re.search(r"\b(?:sha256|file_sha256|file hash|file digest)\b", folded_raw):
            look_tool = "file_sha256"
        elif re.search(r"\b(?:write_status|write status|write timeout|commit state)\b", folded_raw):
            look_tool = "write_status"
    if look_tool and look_tool in live_tools:
        example = ({"file_path": "D:/path/file", "expected_sha256": ""}
                   if look_tool == "file_sha256" else {"operation_id": "<from write response>"})
        return _ok(_apply_verbosity({
            "recommended_tier": "T1", "recommended_tool": look_tool,
            "all_recommended_tools_live": True,
            "suggestion": {"tool": look_tool, "tier": "T1", "payload_example": example,
                           "reason": "Read-only evidence without executing a script or editing files."},
        }, verbosity, "decision_hint"))
    recycle_intent = (
        re.search(r"\b(?:recycle|restart|relaunch|khoi dong lai)\b", folded_raw)
        and re.search(r"\b(?:process|app|application|server|service|tien trinh|ung dung|orchestrator|toola)\b", folded_raw)
    )
    if recycle_intent and not _campaign:
        own_server = bool(re.search(r"\b(?:orchestrator|toola)\b", folded_raw))
        steps = [
            {"tool": "server_lifecycle_status", "action": "Inspect the target port and confirm its owner."},
        ]
        if own_server:
            steps.append({"tool": "restart_orchestrator_via_bat",
                          "action": "Use the dedicated orchestrator restart after explicit restart authorization."})
        else:
            steps.extend([
                {"tool": "stop_app", "action": "Stop only the inspected and explicitly approved PID."},
                {"tool": "start_app", "action": "Launch the intended command with expected_port or health_url readiness."},
            ])
        steps.append({"tool": "server_lifecycle_status",
                      "action": "Reinspect after the scan-cache TTL; verify a new owner PID and application readiness. For orchestrator also verify a new /health generation."})
        missing = sorted({step["tool"] for step in steps} - live_tools)
        if missing:
            return _err("Lifecycle recipe unavailable in this catalog.",
                        error_name="RECIPE_TOOL_UNAVAILABLE", missing_tools=missing,
                        ca_next_action="inspect_live_catalog")
        resp = {
            "recommended_tier": "T4", "recommended_tool": "server_lifecycle_status",
            "recommended_skill": "16_server_lifecycle",
            "recipe": {"name": "restart_orchestrator" if own_server else "recycle_application",
                       "advisory_only": True, "steps": steps,
                       "execution_note": "This hint executes nothing. Stop/restart needs explicit authorization for the identified target; never infer a PID or launch command."},
            "suggestion": {"tool": "server_lifecycle_status", "tier": "T4",
                           "payload_example": _examples_for(["server_lifecycle_status"]).get("server_lifecycle_status", {}),
                           "reason": "Recycling requires inspection, replacement and verification, not just termination."},
            "all_recommended_tools_live": True,
        }
        return _ok(_apply_verbosity(resp, verbosity, "decision_hint"))
    # The campaign prepass above retains priority for explicit test/audit intents.
    task_expanded = _wt_vn_expand(task)
    _matched = []
    _seen_tools = set()
    _campaign_primary = False
    for rule in _campaign:
        _key = tuple(_rule_names(rule))
        if _key not in _seen_tools:
            _seen_tools.add(_key)
            _matched.append(rule)
            _campaign_primary = True
    # WT-2026-07-12: legacy rules still run on raw then VN-expanded text.
    # CAN-053 multi-intent: remaining matches become alternates so a broad
    # find/search rule cannot silently shadow architecture intent.
    for probe in (task, task_expanded):
        for rule in _DECISION_RULES:
            # H3 2026-10-08: never recommend write_fix when task explicitly says no code changes
            if rule.get("tool") == "write_fix" and "no code changes" in probe:
                continue
            if re.search(rule["re"], probe):
                _key = tuple(_rule_names(rule))
                if _key not in _seen_tools:
                    _seen_tools.add(_key)
                    _matched.append(rule)
    # A07: compute the lexical rank once — it feeds three decisions: the
    # stage-1/stage-2 defer check, the always-present escape hatch, and stage 2
    # itself. Ranker is pure/read-only so computing it eagerly is cheap.
    _lex_ranked = _wt_rank_lexical(task_expanded, top_k=3)
    # A07: a broad rule that fired on a single query word must not shadow a
    # tool whose multi-segment name the query spells out. When that happens,
    # drop stage 1 entirely and let the lexical ranker answer.
    # R2: confirmed campaign (R1) primary is not A07-deferrable.
    if (
        _matched
        and not _campaign_primary
        and _a07_should_defer(task, _matched[0], _lex_ranked)
    ):
        _matched = []
    if _matched:
        rule = _matched[0]
        _names = _rule_names(rule)
        validation = _validate_tools(_names)
        examples = _examples_for(validation["present"])
        # A07 escape hatch: prefer distinct sibling rules; if only one rule
        # fired there are none, so fall back to lexical candidates so the
        # caller always sees a second option. A sibling is shown by its first
        # tool the answer does not already name.
        _shown = set(_names)
        _alternates = []
        for r in _matched[1:]:
            _alt = next((n for n in _rule_names(r) if n not in _shown), "")
            if _alt:
                _shown.add(_alt)
                _alternates.append({"tool": _alt, "tier": r["tier"], "why": r["why"]})
                if len(_alternates) == 2:
                    break
        if not _alternates:
            _alternates = _a07_alternates(_lex_ranked, exclude=_names)
        else:
            # Two+ rules fired, so the siblings above are non-empty and the hatch
            # did not run. A tool the query names outright must still be
            # reachable -- see _a07_named_tool_alternate.
            _alternates = _alternates + _a07_named_tool_alternate(
                task, _lex_ranked, _shown)
        resp = {
            "recommended_tier": rule["tier"],
            "recommended_tool": rule["tool"],
            "recommended_skill": rule["skill"],
            "rationale": rule["why"],
            "scope_hint": scope_hint,
            "risk_hint": risk_hint,
            "confidence": 0.85,
            # R16.9: tools_validation flags drift between rules and live TOOL_MAP
            "tools_validation": validation,
            "all_recommended_tools_live": (None if validation.get("inconclusive")
                                           else (not validation["missing"])),
            # R16.10: paste-ready payload schemas per recommended tool
            "payload_example_per_tool": examples,
            "payload_example_lookup_hint": "Each key = tool name; value = working payload. Adjust placeholders before calling.",
            "alternates": _alternates,
        }
        resp["suggestion"] = {
            "tool": rule["tool"],
            "tier": rule["tier"],
            "payload_example": examples.get(rule["tool"], {}),
            "reason": rule["why"],
        }
        return _ok(_apply_verbosity(resp, verbosity, "decision_hint"))
    # (VN-aware) instead of the old flat 0.4 find_symbol/search_code default,
    # and enrich with [Q] Z2 memory refs (fail-open).
    ranked = _lex_ranked
    q_refs = _wt_q_z2_refs(task_description or task)
    if ranked:
        q_text = " ".join(_wt_vn_fold(str(h.get("snippet") or "")) for h in q_refs)
        if q_text:
            boosted = []
            for score, name, fn, tier in ranked:
                if re.search(rf"\btool {re.escape(name)}\b", q_text) or f" {name} " in q_text:
                    score += 4
                boosted.append((score, name, fn, tier))
            boosted.sort(key=lambda x: (-x[0], x[1]))
            ranked = boosted
        top_names = [name for _, name, _, _ in ranked]
        candidates = [{
            "tool": name,
            "tier": tier,
            "score": round(score, 1),
            "description": re.sub(r"\s+", " ", str(fn.get("description") or ""))[:160],
            "see_also": fn.get("see_also"),
        } for score, name, fn, tier in ranked]
        resp = {
            "recommended_tier": ranked[0][3],
            "recommended_tool": ranked[0][1],
            "recommended_skill": "00_skill_router",
            "rationale": "Lexical rank over live TOOL_SCHEMAS (VN-aware)"
                         + (" + [Q] Z2 memory boost" if q_refs else ""),
            "confidence": min(0.8, 0.45 + 0.05 * ranked[0][0]),
            "candidates": candidates,
            "payload_example_per_tool": _examples_for(top_names[:2]),
            "q_memory_refs": [{"id": h.get("id"), "ts": h.get("ts"),
                               "snippet": str(h.get("snippet") or "")[:160]} for h in q_refs],
            "next_step": "Pick a candidate then GET /api/tools/<tool>/schema for the full schema.",
        }
        resp["suggestion"] = {
            "tool": ranked[0][1], "tier": ranked[0][3],
            "payload_example": (_examples_for([ranked[0][1]]) or {}).get(ranked[0][1], {}),
            "reason": resp["rationale"],
        }
        return _ok(_apply_verbosity(resp, verbosity, "decision_hint"))
    return _ok(_apply_verbosity({
        "recommended_tier": "T1 (default)",
        "recommended_tool": "search_code",
        "recommended_skill": "00_skill_router",
        "rationale": "No specific signal in task; default to read-only analysis first.",
        "confidence": 0.4,
        # CSLt 2026-07-16 (upgrade #4): machine-readable suggestion block —
        # {tool, tier, payload_example, reason} — so a CA can act without parsing prose.
        "suggestion": {"tool": "search_code", "tier": "T1",
                       "payload_example": {"project_path": "D:/path/to/project",
                                           "query": "<keyword>", "mode": "auto"},
                       "reason": "No specific signal in task; start with read-only code search."},
        "q_memory_refs": [{"id": h.get("id"), "snippet": str(h.get("snippet") or "")[:160]} for h in q_refs],
    }, verbosity, "decision_hint"))


async def progressive_matrix_check(project_path: str = "",
                                   mode: str = "summary",
                                   tool_name: str = "",
                                   step_id: str = "") -> str:
    """Read-only certification snapshot for workflow -> schema -> UI alignment.

    This is intentionally a small harness, not a replacement for runtime tests.
    It catches static drift early and points CA to the next focused runtime proof.
    """
    from pathlib import Path

    try:
        import code_tools as ct
        import path_runtime

        app_root = Path(project_path).resolve() if project_path else Path(path_runtime.APP_ROOT)
        orchestrator_dir = Path(path_runtime.ORCHESTRATOR_DIR)
        # R21 fix: use path_runtime.resolve_ui_html() — UI is at orchestrator.parent/UI/,
        # not project_path/UI. Previously this caused 4 false-positive progressive gaps.
        try:
            ui_file = path_runtime.resolve_ui_html(orchestrator_dir)
        except Exception:
            ui_file = app_root / "UI" / "8. AGENT TOOL - READ WRITE TEST_UI.html"

        schema_by_name = {}
        for schema in getattr(ct, "TOOL_SCHEMAS", []):
            fn = schema.get("function", {})
            name = fn.get("name")
            if name:
                schema_by_name[name] = fn

        public_tools = sorted(getattr(ct, "TOOL_MAP", {}).keys())
        missing_schema = [name for name in public_tools if name not in schema_by_name]
        missing_phase = []
        phase_mismatch = []
        for name in public_tools:
            fn = schema_by_name.get(name, {})
            expected = ct.tool_phase(name)
            actual = fn.get("x_phase")
            if not actual:
                missing_phase.append(name)
            elif actual != expected:
                phase_mismatch.append({"tool": name, "expected": expected, "actual": actual})

        workflow_docs = {}
        for tier in ("T1", "T2", "T3", "T4"):
            p = orchestrator_dir / f"WORKFLOW_{tier}.md"
            exists = p.exists()
            text = p.read_text(encoding="utf-8", errors="replace") if exists else ""
            workflow_docs[tier] = {
                "exists": exists,
                "step_markers": text.count("## Step "),
                "mentions_guardrail": "Guardrail" in text,
            }

        ui_text = ui_file.read_text(encoding="utf-8", errors="replace") if ui_file.exists() else ""
        ui_checks = {
            "exists": ui_file.exists(),
            "t4_tab": "data-mode=\"T4\"" in ui_text or "data-mode='T4'" in ui_text,
        }

        gaps = []
        if missing_schema:
            gaps.append({"area": "schema", "detail": "public tools missing schemas", "tools": missing_schema})
        if missing_phase:
            gaps.append({"area": "schema", "detail": "schemas missing x_phase", "tools": missing_phase})
        if phase_mismatch:
            gaps.append({"area": "schema", "detail": "schema phase differs from backend phase", "tools": phase_mismatch})
        for tier, info in workflow_docs.items():
            if not info["exists"]:
                gaps.append({"area": "workflow_docs", "detail": f"WORKFLOW_{tier}.md missing"})
            elif info["step_markers"] < 2:
                gaps.append({"area": "workflow_docs", "detail": f"WORKFLOW_{tier}.md is not step-sized"})
        for key, value in ui_checks.items():
            if key != "exists" and not value:
                gaps.append({"area": "ui", "detail": f"{key} missing"})
        selected_tool = {}
        if tool_name:
            fn = schema_by_name.get(tool_name, {})
            selected_tool = {
                "name": tool_name,
                "phase": fn.get("x_phase") or ct.tool_phase(tool_name),
                "phase_label": fn.get("x_phase_label"),
                "has_schema": bool(fn),
                "schema_required": fn.get("parameters", {}).get("required", []),
            }

        matrix_rows = [
            {"area": "workflow", "owner": "WORKFLOW_T1.md", "guardrail": "read-only first"},
            {"area": "workflow", "owner": "WORKFLOW_T2.md", "guardrail": "write only after trigger + validate/cache"},
            {"area": "workflow", "owner": "WORKFLOW_T3.md", "guardrail": "session/audit only"},
            {"area": "workflow", "owner": "WORKFLOW_T4.md", "guardrail": "inspect read-first; runtime mutation needs trigger"},
            {"area": "ui", "owner": str(ui_file), "guardrail": "special actions must map to a normal tool owner"},
        ]

        _matrix_out = {
            "ok_to_ship_static_matrix": not gaps,
            "mode": mode,
            "step_id": step_id,
            "selected_tool": selected_tool,
            "checks": {
                "public_tool_count": len(public_tools),
                "workflow_docs": workflow_docs,
                "schema": {
                    "missing_schema_count": len(missing_schema),
                    "missing_phase_count": len(missing_phase),
                    "phase_mismatch_count": len(phase_mismatch),
                },
                "ui": ui_checks,
            },
            "gaps": gaps,
            "matrix_rows": matrix_rows,
            "runtime_test_note": "Static matrix checks cannot prove browser/server behavior; run T4 runtime harnesses for touched surfaces.",
        }
        # A39: ok_to_ship_static_matrix=False already said "do not ship" and then said
        # nothing about what to do, so the gap list was a dead end. Gated on gaps, so the
        # ship-ready path -- the one a CA hits most -- carries no hint key at all.
        #
        # The next tool is DERIVED from the gap's own area instead of being fixed, because
        # the two kinds of gap want different tools: a schema gap is a registry-vs-backend
        # disagreement (audit_tool_matrix is the tool that pairs them), while a
        # workflow_docs or ui gap is a missing or too-short FILE, where reading the target
        # is the honest first step. One generic "go fix it" would be advice neither case
        # asked for. Stamped before _ok() deliberately: _ok only stamps ok/schema_version
        # and prunes nothing, so there is no pre-filter key loss here (unlike the
        # verbosity-filtered matrix_audit path in code_tools).
        if gaps:
            _areas = sorted({str(g.get("area") or "?") for g in gaps})
            _nxt = "audit_tool_matrix" if "schema" in _areas else "read_file_range"
            _matrix_out["ca_hint"] = (
                "%d gap(s) block shipping (area: %s); %s is the next step -- gaps[].tools or "
                "gaps[].detail names the exact target"
                % (len(gaps), ", ".join(_areas), _nxt))
            _matrix_out.setdefault("ca_next_action", _nxt)
        return _ok(_matrix_out)
    except Exception as exc:
        return _err(f"progressive_matrix_check failed: {exc}", error_name="MATRIX_CHECK_FAILED")


# ────────────────────────────────────────────────────────────────────────────
# B.3 — T4a Browser (Playwright)
# ────────────────────────────────────────────────────────────────────────────

_BROWSER_PAGES: dict = {}  # {page_id: {"page": Page, "context": Context,
                            #            "browser": Browser, "console": list,
                            #            "network_errors": list, "created_at": ts}}


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


async def inspect_project_runtime(project_path: str) -> str:
    """V15 R16 — Pre-flight introspection. Detect launcher type, ports, scripts,
    dependencies so CA can pick the right launch/attach recipe BEFORE failing.

    Returns:
      {launcher_type, launcher_path, entry_file, cdp_default_port,
       has_remote_debugging_in_launcher, npm_scripts, recent_logs_paths,
       package_dependencies_highlight}
    """
    import json as _json
    import re as _re
    root = Path(project_path).resolve()
    if not root.exists():
        return _err(f"Project path not found: {project_path}",
                    error_name="PROJECT_PATH_NOT_FOUND")
    result = {
        "ok": True,
        "project_path": str(root),
        "launcher_type": "unknown",
        "launcher_path": None,
        "entry_file": None,
        "cdp_default_port": None,
        "has_remote_debugging_in_launcher": False,
        "npm_scripts": {},
        "recent_logs_paths": [],
        "package_dependencies_highlight": {},
        "remote_debugging_port_detected": None,
    }
    # Scan top-level .bat / .cmd / .ps1 launchers.
    # V15 R67: score candidates by ACTUAL electron launch command, NOT a loose
    # `"electron" in text` substring. Root cause of the GetLink retry bug: run.bat
    # only MENTIONS "Electron" in an echo/comment line ("mo file run_electron_ui.bat")
    # while actually running `python sheet_scanner.py`. The old substring match
    # falsely picked it over the real launcher start_getlink.bat (`npx electron .`).
    launcher_candidates = []
    for ext in ("*.bat", "*.cmd", "*.ps1"):
        launcher_candidates.extend(sorted(root.glob(ext)))

    def _r67_launcher_command_body(text_raw: str) -> str:
        """Return only the executable command lines (strip echo/REM/::/# comments)."""
        cmd_lines = []
        for raw in text_raw.splitlines():
            s = raw.strip()
            if not s:
                continue
            low = s.lower()
            # comments/echo + ps1 '#' comments + Write-* output do NOT count as
            # launch commands (else a Write-Host "Electron UI" / echo mention or a
            # process-name string would be misread as launching electron).
            if low.startswith(("echo", "@echo", "rem", "::", "#", "title", "color", "write-")):
                continue
            cmd_lines.append(low)
        return "\n".join(cmd_lines)

    def _r67_classify(body: str):
        # electron LAUNCH command — must INVOKE electron with an app arg (".", a
        # path sep, a quote, or a flag). This rejects process-name references like
        # name="electron.exe" / Stop-Process electron, and UI text "Electron UI".
        electron_cmd = bool(
            _re.search(r"npx\s+(?:--[\w-]+\s+)*electron\b", body)
            or _re.search(r"(?:^|[\s&|(>`])electron(?:\.cmd)?\s+[-.\"'\\/]", body, _re.MULTILINE)
            or _re.search(r"node_modules[\\/](?:\.bin[\\/])?electron(?:\.cmd)?[\"']?\s+[-.\"'\\/]", body)
        )
        npm_start = bool(_re.search(r"npm\s+(?:run\s+[\w:.-]+|start)\b", body))
        python_cmd = bool(
            _re.search(r"(?:^|[\s&|(])(?:python|pythonw|py)\b", body, _re.MULTILINE)
            or _re.search(r"\.py\b", body)
        )
        # process-management / kill / cleanup scripts are NOT launchers
        kill_util = bool(_re.search(r"stop-process|taskkill|get-ciminstance|get-process\b", body))
        has_rdp = "--remote-debugging-port" in body
        return electron_cmd, npm_start, python_cmd, kill_util, has_rdp

    scored = []
    for lp in launcher_candidates:
        try:
            text = lp.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            continue
        body = _r67_launcher_command_body(text)
        electron_cmd, npm_start, python_cmd, kill_util, has_rdp = _r67_classify(body)
        if kill_util and not electron_cmd:
            continue  # process-management/kill script (e.g. kill_getlink_electron.ps1)
        if not (electron_cmd or npm_start):
            continue  # not an electron launcher (e.g. run.bat = python sheet_scanner.py)
        score = 0
        if electron_cmd:
            score += 100
        if has_rdp:
            score += 40
        if npm_start and not electron_cmd:
            score += 30  # weaker: npm start -> electron resolved via package.json
        name = lp.name.lower()
        if "electron" in name:
            score += 15
        if name.startswith(("start", "launch")) or "run_electron" in name:
            score += 8
        if python_cmd:
            score -= 50  # mixed / python-leaning launcher
        score -= min(len(body.splitlines()), 20)  # prefer the cleanest launcher
        scored.append((score, lp, has_rdp, body))

    chosen_launcher = None
    if scored:
        scored.sort(key=lambda x: (-x[0], str(x[1])))
        score0, chosen_launcher, has_rdp0, body0 = scored[0]
        result["launcher_type"] = "electron-bat" if chosen_launcher.suffix.lower() in {".bat", ".cmd"} else "electron-ps1"
        result["has_remote_debugging_in_launcher"] = has_rdp0
        m = _re.search(r"--remote-debugging-port[=\s]+(\d+)", body0)
        if m:
            result["remote_debugging_port_detected"] = int(m.group(1))
        result["launcher_candidates_considered"] = [
            {"path": str(p), "score": sc} for sc, p, _h, _b in scored[:5]
        ]
    if chosen_launcher:
        result["launcher_path"] = str(chosen_launcher)
    # package.json
    pkg = root / "package.json"
    if pkg.exists():
        try:
            pj = _json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
            result["npm_scripts"] = pj.get("scripts", {})
            deps = {**(pj.get("dependencies") or {}), **(pj.get("devDependencies") or {})}
            highlight_keys = ["electron", "playwright", "chromium", "puppeteer", "react", "vue", "vite"]
            result["package_dependencies_highlight"] = {
                k: deps[k] for k in highlight_keys if k in deps
            }
            result["entry_file"] = pj.get("main") or pj.get("module")
            if result["launcher_type"] == "unknown" and "electron" in deps:
                result["launcher_type"] = "electron-npm-script"
        except Exception:
            pass
    # Default cdp_port for electron
    if "electron" in result["launcher_type"]:
        result["cdp_default_port"] = result.get("remote_debugging_port_detected") or 9223
    # V15 R16.2 — electron install precondition probe.
    # Walk all .bat files looking for `cd /d "..."` then check the resolved cwd
    # for node_modules/electron. If launcher is electron-npm-script, check root.
    result["electron_installed"] = None
    result["install_command"] = None
    if "electron" in result["launcher_type"]:
        # Candidate dirs to probe: bat-resolved-cwd, root
        candidate_cwds = [root]
        if chosen_launcher:
            try:
                bat_text2 = chosen_launcher.read_text(encoding="utf-8", errors="ignore")
                cur = chosen_launcher.parent
                for line in bat_text2.splitlines():
                    ll = line.strip()
                    m = _re.match(r"^\s*(cd|pushd)\s+(/d\s+)?\"?([^\"]+)\"?\s*$", ll, _re.IGNORECASE)
                    if m:
                        p = m.group(3).strip().replace("%~dp0", str(chosen_launcher.parent))
                        np = Path(p) if Path(p).is_absolute() else (cur / p)
                        if np.exists():
                            cur = np.resolve()
                if cur != chosen_launcher.parent:
                    candidate_cwds.insert(0, cur)
            except Exception:
                pass
        for cwd in candidate_cwds:
            em = cwd / "node_modules" / "electron"
            if em.exists():
                result["electron_installed"] = True
                result["electron_path"] = str(em)
                break
        if result["electron_installed"] is None:
            result["electron_installed"] = False
            # Prefer launcher-resolved cwd for install hint
            install_dir = str(candidate_cwds[0])
            result["install_command"] = f'cd /d "{install_dir}" && npm install electron'
            result["install_note"] = (
                "Electron not in node_modules. launch_electron_with_cdp will auto-inject "
                "`--yes` to npx, but first-run will download ~150MB which may exceed background "
                "verify_after_ms. Pre-install once to avoid CDP_UNREACHABLE."
            )
    # recent logs (top 5 by mtime)
    logs = []
    for pat in ("**/logs/*.log", "**/*.log"):
        try:
            logs.extend(root.glob(pat))
        except Exception:
            pass
    if logs:
        logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        result["recent_logs_paths"] = [str(p) for p in logs[:5]]
    # CA next action hint
    if result["launcher_type"].startswith("electron"):
        if result["has_remote_debugging_in_launcher"]:
            result["ca_next_action"] = "launcher already has --remote-debugging-port; run launcher then cdp_attach_target"
        else:
            result["ca_next_action"] = "call launch_electron_with_cdp(launcher_path) to inject port flag automatically"
    else:
        result["ca_next_action"] = "use run_project_command (and check_window_visibility for Win32 path)"
    return _ok(result)


async def record_error_resolution(target_session_id: str, resolves_event_id: int,
                                  resolution_kind: str, calling_context: dict | None = None,
                                  error_owner: str = "", evidence=None,
                                  correlation_type: str = "",
                                  correlation_key: str = "") -> str:
    """Append a resolution only when the transport host owns the target scope."""
    import code_tools as _ct
    from orchestrator.session import store as _store

    _ct._sync_session_db_alias()
    trusted_context = calling_context if isinstance(calling_context, dict) else {}
    caller = str(trusted_context.get("session_id") or "").strip()
    target = str(target_session_id or "").strip()

    def _authorized() -> tuple[bool, str]:
        if not caller or trusted_context.get("validated") is not True:
            return False, "validated host task checkpoint context is required"
        with _store._with_sessions_db() as conn:
            if not conn.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (target,),
            ).fetchone():
                return False, f"Session not found: {target}"
            if caller == target:
                return True, "same_session"
            correlated = conn.execute(
                """SELECT 1 FROM events
                   WHERE session_id=? AND id=? AND host_session_id=? LIMIT 1""",
                (target, int(resolves_event_id), caller),
            ).fetchone()
            return (True, "host_task_correlation") if correlated else (
                False, "target session is not owned or correlated with the calling host session"
            )

    authorized, authority = await asyncio.to_thread(_authorized)
    if not authorized:
        return _err(
            authority,
            error_name="SESSION_OWNERSHIP_REQUIRED",
            target_session_id=target,
            calling_session_id=caller or None,
            write_performed=False,
            applied=False,
            ca_next_action="use_same_host_session_or_an_authorized_host_task_correlation",
        )
    try:
        resolution_event_id = await asyncio.to_thread(
            _store._record_error_resolution,
            target,
            resolves_event_id,
            resolution_kind,
            error_owner=error_owner,
            evidence=evidence,
            correlation_type=correlation_type,
            correlation_key=correlation_key,
        )
    except (TypeError, ValueError) as exc:
        return _err(
            str(exc),
            error_name="INVALID_ERROR_RESOLUTION",
            accepted_resolution_kinds=["recovered", "expected_failure", "reopened"],
            ca_next_action="fix_exact_session_error_reference_and_retry",
        )
    return _ok({
        "target_session_id": target,
        "calling_session_id": caller,
        "authority": authority,
        "resolves_event_id": int(resolves_event_id),
        "resolution_event_id": resolution_event_id,
        "resolution_kind": str(resolution_kind).strip().lower(),
    })


# V15 R34: app-specific usage profiles for inspect_project_usage enrichment
_R34_APP_USAGE_PROFILE = {
    "rawcut": {
        "name": "Rawcut",
        "input_modes": ["manual_text_input", "sheet_paste_input"],
        "key_buttons": {
            "btnGetFootageManual": {
                "purpose": "trigger get-footage cho manual text input (#txtPath)",
                "modal_chain": ["folder_picker"],
            },
            "btnGetFootageSheet": {
                "purpose": "trigger get-footage cho sheet data (đã paste)",
                "modal_chain": ["folder_picker"],
            },
            "btnStep3": {
                "purpose": "next-step navigation — KHÔNG phải get-footage button",
                "modal_chain": [],
            },
            "output_folder_input": {"purpose": "đích copy footage", "modal_chain": []},
        },
        "common_pitfalls": [
            "Forward slash trong folder_path → 'The folder name is not valid' (R34 boundary auto-fix khử lỗi này)",
            "Source UNC path (\\\\server\\share) đôi khi access denied",
            "test4 = manual mode; testgf = sheet mode — KHÔNG chạy nhầm giữa 2",
        ],
        "recommended_post_action": (
            "After get-footage trigger, ALWAYS call classify_get_footage_outcome("
            "project_path, since_ts, output_folder) to verify outcome — "
            "don't infer from button state alone."
        ),
    },
}

def _r34_detect_app(project_path: str) -> str:
    """R34: cheap app-name detector from project_path basename / known files."""
    try:
        from pathlib import Path as _PathR34
        p = _PathR34(project_path)
        name = p.name.lower()
        if "rawcut" in name or "raw_cut" in name or "raw-cut" in name:
            return "rawcut"
        # check for sentinel files
        for sent in ("Rawcut.exe", "rawcut.exe", "RAWCUT.exe"):
            if (p / sent).exists():
                return "rawcut"
    except Exception:
        pass
    return ""


async def inspect_project_usage(project_path: str) -> str:
    """V15 R27 T1 — Workflow-level project profile beyond runtime detect.

    Extends `inspect_project_runtime` with regex-derived intel about UI fields,
    buttons, runtime functions, and dangerous controls. Used after `open_session`
    to guide CA's recommended workflow without needing multiple search_code calls.

    Returns:
      {primary_ui_fields, primary_buttons, runtime_functions, dangerous_controls,
       recommended_workflow_hint, scanned_files}.
    """
    import re as _re
    root = Path(project_path).resolve()
    if not root.exists() or not root.is_dir():
        return _err(f"project_path invalid: {project_path}",
                    error_name="PROJECT_PATH_INVALID")
    # V15 R28/R29: set-based collector with priority-name first + size cap + skip minified.
    # R29: SKIP_SUBSTR expanded to filter Python virtualenv / site-packages dependency noise.
    # Bug fix: html-scan and js-scan are independent (each finds its own folder).
    SKIP_SUBSTR = (
        "node_modules", ".git", "dist", "build", "__pycache__", ".min.js",
        # V15 R29: Python venv + site-packages dependency noise
        "\\venv\\", "\\venv312\\", "\\.venv\\", "\\site-packages\\",
        "/venv/", "/venv312/", "/.venv/", "/site-packages/",
    )
    PRIORITY_NAMES = {"main.js", "preload.js", "renderer.js", "app.js", "index.js", "core.js"}
    MAX_FILE_BYTES = 1_000_000  # 1 MB cap — skip minified bundles
    scan_html: list = []
    js_collected: set = set()
    # HTML scan: find first sub with HTMLs
    for sub in ("Source/ui", "ui", "Source", "."):
        p = root / sub
        if not p.exists() or not p.is_dir():
            continue
        try:
            for f in p.rglob("*.html"):
                if any(s in str(f).lower() for s in SKIP_SUBSTR):
                    continue
                scan_html.append(f)
                if len(scan_html) >= 5:
                    break
        except Exception:
            pass
        if scan_html:
            break
    # JS scan: independent — search Source/preload, Source, ., capturing all eligible
    # R29: extend sub-folder list for diverse project layouts (Rawcut + others)
    for sub in ("Source", "src", "lib", "app", "renderer", "main", "."):
        p = root / sub
        if not p.exists() or not p.is_dir():
            continue
        try:
            for f in p.rglob("*.js"):
                sp = str(f).lower()
                if any(s in sp for s in SKIP_SUBSTR):
                    continue
                try:
                    if f.stat().st_size > MAX_FILE_BYTES:
                        continue
                except Exception:
                    continue
                js_collected.add(f)
        except Exception:
            pass
        if js_collected:
            break
    # Sort: priority names first, others by mtime desc; cap 20
    priority_js = [f for f in js_collected if f.name in PRIORITY_NAMES]
    other_js = sorted([f for f in js_collected if f.name not in PRIORITY_NAMES],
                       key=lambda x: x.stat().st_mtime, reverse=True)
    scan_js = (priority_js + other_js)[:20]
    primary_ui_fields = []
    primary_buttons = []
    runtime_functions = []  # R28: [{name, file, line, pattern_kind}]
    event_bindings = []     # R28 NEW: [{selector, handler, file, line}]
    dangerous_controls = []
    ipc_handlers = []  # R27.1: Electron IPC handlers (ipcMain.handle / contextBridge.exposeInMainWorld)
    DANGEROUS_RE = _re.compile(r"^(output|reset|delete|wipe|clear|format|kill)$", _re.IGNORECASE)
    # Parse HTML for inputs/buttons
    for html in scan_html[:3]:
        try:
            text = html.read_text(encoding="utf-8", errors="replace")[:200000]
        except Exception:
            continue
        # Input/textarea fields with id
        for m in _re.finditer(
            r'<(input|textarea|select)[^>]*id\s*=\s*[\'"]([^\'"]+)[\'"][^>]*(?:type\s*=\s*[\'"]([^\'"]+)[\'"])?',
            text, _re.IGNORECASE
        ):
            tag = m.group(1)
            fid = m.group(2)
            ftype = (m.group(3) or "").lower() if tag.lower() == "input" else tag
            purpose_hint = ""
            window = text[max(0, m.start() - 80):m.start()]
            label_match = _re.search(r'<label[^>]*>([^<]{1,60})</label>', window, _re.IGNORECASE | _re.DOTALL)
            if label_match:
                purpose_hint = label_match.group(1).strip()
            primary_ui_fields.append({
                "selector": "#" + fid,
                "tag": tag.lower(),
                "type": ftype,
                "purpose": purpose_hint or "",
            })
            if len(primary_ui_fields) >= 30:
                break
        # Buttons
        for m in _re.finditer(
            r'<button[^>]*id\s*=\s*[\'"]([^\'"]+)[\'"][^>]*>([^<]*)</button>',
            text, _re.IGNORECASE | _re.DOTALL
        ):
            bid = m.group(1)
            label = (m.group(2) or "").strip()[:60]
            entry = {"selector": "#" + bid, "label": label, "event_handler_hint": ""}
            primary_buttons.append(entry)
            if DANGEROUS_RE.search(label.strip()) or DANGEROUS_RE.search(bid):
                dangerous_controls.append({"selector": "#" + bid, "label": label,
                                            "reason": "Name matches dangerous pattern"})
            if len(primary_buttons) >= 30:
                break
    # V15 R28: parse JS with the _FUNC_PATTERNS list below + source evidence
    # (file+line+kind); the pattern count is the length of that list, not a comment.
    # R28 fix: allow indented declarations (drop strict ^ anchor) via \b boundary
    _FUNC_PATTERNS = [
        (r'(?:^|\s)(?:async\s+)?function\s+(\w{3,60})\s*\(', "function_decl"),
        (r'(?:^|\s)(?:const|let|var)\s+(\w{3,60})\s*=\s*(?:async\s*)?(?:function|\()', "arrow_or_async_const"),
        (r'(?:window|globalThis)\s*\.\s*(\w{3,60})\s*=\s*(?:async\s*)?(?:function|\()', "global_assign"),
        (r'module\.exports\s*\.\s*(\w{3,60})\s*=\s*(?:async\s*)?\(', "commonjs_export"),
        (r'\bexports\s*\.\s*(\w{3,60})\s*=\s*(?:async\s*)?\(', "commonjs_export"),
    ]
    btn_handler_hints: dict = {}
    seen_func_names: set = set()
    for js in scan_js:
        try:
            text = js.read_text(encoding="utf-8", errors="replace")[:300000]
        except Exception:
            continue
        try:
            rel_file = str(js.relative_to(root)).replace("\\", "/")
        except Exception:
            rel_file = str(js)
        lines_arr = text.split("\n")
        for line_no, line in enumerate(lines_arr, 1):
            # Function detection
            for pat, kind in _FUNC_PATTERNS:
                for m in _re.finditer(pat, line):
                    name = m.group(1)
                    if name and name not in seen_func_names and len(runtime_functions) < 200:
                        seen_func_names.add(name)
                        runtime_functions.append({
                            "name": name,
                            "file": rel_file,
                            "line": line_no,
                            "pattern_kind": kind,
                        })
            # Event binding: getElementById("X").addEventListener("click", Y)
            for m in _re.finditer(
                r'getElementById\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)\s*\.\s*addEventListener\s*\(\s*[\'"](\w+)[\'"]\s*,\s*([\w.]+)',
                line
            ):
                event_bindings.append({
                    "selector": "#" + m.group(1),
                    "event": m.group(2),
                    "handler": m.group(3),
                    "file": rel_file,
                    "line": line_no,
                })
                btn_handler_hints.setdefault(m.group(1), m.group(3))
            # jQuery-style: $('#X').on('click', Y)
            for m in _re.finditer(
                r'[\'"]#([^\'"]+)[\'"]\s*\)\s*\.\s*(?:on|addEventListener)\s*\(\s*[\'"](\w+)[\'"]?\s*,?\s*([\w.]+)',
                line
            ):
                event_bindings.append({
                    "selector": "#" + m.group(1),
                    "event": m.group(2),
                    "handler": m.group(3),
                    "file": rel_file,
                    "line": line_no,
                })
                btn_handler_hints.setdefault(m.group(1), m.group(3))
            if len(event_bindings) >= 50:
                break
        # R27.1: detect Electron IPC handlers
        # 1) ipcMain.handle('channel', handler) / ipcMain.on('channel', handler)
        for m in _re.finditer(
            r'ipcMain\s*\.\s*(handle|on)\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*(?:async\s*)?(?:\([^)]*\)\s*=>|function\s*\w*\s*\([^)]*\)|(\w[\w.]*))',
            text
        ):
            method = m.group(1)
            channel = m.group(2)
            named_handler = m.group(3) or ""
            ipc_handlers.append({
                "kind": "ipcMain." + method,
                "channel": channel,
                "handler_name": named_handler or "<inline>",
                "file": str(js).replace("\\", "/"),
                "invoke_via": f"window.api.{channel}(...)" if "preload" in js.name.lower() else f"ipcRenderer.invoke('{channel}', ...)",
            })
        # 2) contextBridge.exposeInMainWorld('api', { foo: () => ..., bar: ... })
        for m in _re.finditer(
            r'contextBridge\s*\.\s*exposeInMainWorld\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*\{([^{}]{0,2000})\}',
            text, _re.DOTALL
        ):
            world = m.group(1)
            body = m.group(2)
            for sm in _re.finditer(r'(\w[\w]{1,40})\s*:\s*(?:async\s*)?\(', body):
                fname = sm.group(1)
                ipc_handlers.append({
                    "kind": "contextBridge.exposeInMainWorld",
                    "world": world,
                    "function_name": fname,
                    "invoke_via": f"window.{world}.{fname}(...)",
                    "file": str(js).replace("\\", "/"),
                })
                if len(ipc_handlers) >= 50:
                    break
    # V15 R30: scan inline <script> blocks in HTML files for function definitions.
    # Rawcut keeps `runGetFootageToTarget` inline in Source/ui/index.html — was missed by JS-only scan.
    inline_script_funcs_found = 0
    _SCRIPT_BLOCK_RE = _re.compile(
        r'<script\b[^>]*>(.*?)</script>',
        _re.DOTALL | _re.IGNORECASE,
    )
    for html in scan_html[:5]:
        try:
            html_text = html.read_text(encoding="utf-8", errors="replace")[:600000]
        except Exception:
            continue
        try:
            rel_html = str(html.relative_to(root)).replace("\\", "/")
        except Exception:
            rel_html = str(html)
        # Find each <script> block; compute its starting line for evidence
        for sb in _SCRIPT_BLOCK_RE.finditer(html_text):
            block = sb.group(1) or ""
            block_start_line = html_text.count("\n", 0, sb.start()) + 1
            for line_offset, bline in enumerate(block.split("\n")):
                line_no = block_start_line + line_offset
                for pat, kind in _FUNC_PATTERNS:
                    for m in _re.finditer(pat, bline):
                        name = m.group(1)
                        if name and name not in seen_func_names and len(runtime_functions) < 200:
                            seen_func_names.add(name)
                            runtime_functions.append({
                                "name": name,
                                "file": rel_html,
                                "line": line_no,
                                "pattern_kind": kind + "_inline_html",
                            })
                            inline_script_funcs_found += 1
                # Event bindings in inline script too
                for m in _re.finditer(
                    r'getElementById\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)\s*\.\s*addEventListener\s*\(\s*[\'"](\w+)[\'"]\s*,\s*([\w.]+)',
                    bline
                ):
                    event_bindings.append({
                        "selector": "#" + m.group(1),
                        "event": m.group(2),
                        "handler": m.group(3),
                        "file": rel_html,
                        "line": line_no,
                    })
                    btn_handler_hints.setdefault(m.group(1), m.group(3))
                if len(event_bindings) >= 50:
                    break
    # Fill button handler hints
    for btn in primary_buttons:
        sel_id = btn["selector"].lstrip("#")
        if sel_id in btn_handler_hints:
            btn["event_handler_hint"] = btn_handler_hints[sel_id]
    return _ok({
        "project_path": str(root),
        "primary_ui_fields": primary_ui_fields,
        "primary_buttons": primary_buttons,
        # V15 R28/R30: runtime_functions now objects with source evidence (file+line+pattern_kind)
        # R30: includes inline <script> blocks from HTML (pattern_kind suffix `_inline_html`)
        "runtime_functions": runtime_functions,
        "runtime_function_count": len(runtime_functions),
        "inline_script_funcs_found": inline_script_funcs_found,
        # V15 R28 NEW: event_bindings for #selector → handler with file/line evidence
        "event_bindings": event_bindings,
        "event_binding_count": len(event_bindings),
        "dangerous_controls": dangerous_controls,
        # R27.1: surface Electron IPC handlers for invoke_app_function routing
        "ipc_handlers": ipc_handlers,
        "ipc_handler_count": len(ipc_handlers),
        "scanned_html_files": [str(p) for p in scan_html[:5]],
        "scanned_js_files": [str(p) for p in scan_js[:20]],
        "scanned_js_count": len(scan_js),
        "recommended_workflow_hint": (
            "After fill_value/click sequence: if a button opens a native dialog, "
            "prefer invoke_app_function(<handler_name>, [args]) over native picker "
            "control when the handler exists in runtime_functions list (R28: includes file+line evidence). "
            "For Electron IPC routes, use ipc_handlers[].invoke_via expression. "
            "For button → handler mapping, see event_bindings[]."
        ),
        # V15 R34: app-specific usage profile (constant — no scan cost)
        "app_usage_profile": _R34_APP_USAGE_PROFILE.get(_r34_detect_app(project_path), None),
        "ca_next_action": (
            "use_primary_ui_fields_and_runtime_functions_for_workflow"
            if primary_ui_fields or runtime_functions or ipc_handlers
            else "fallback_to_search_code_for_deeper_discovery"
        ),
    })


async def invoke_app_function(page_id: str, function_name: str,
                                args: list | None = None,
                                poll_until_global: str = "",
                                poll_interval_ms: int = 500,
                                timeout_ms: int = 30000) -> str:
    """V15 R27 T4 — Structured invoke of a top-level page function with arg
    serialization + optional completion polling on a global flag.

    Use case (testgf): invoke_app_function(page_id, "runGetFootageToTarget",
    ["D:/target"], poll_until_global="__codexRawcutCopyDone").

    Returns: {ok, function_name, args_count, return_value, polled_global,
              polled_value, elapsed_ms, polled_timeout_reached}.
    """
    import asyncio as _aio
    state, err = await _get_page(page_id)
    if err:
        return err
    args_list = args or []
    # Validate function_name (simple JS identifier)
    import re as _re_n
    if not _re_n.match(r"^[A-Za-z_$][\w$]*$", function_name or ""):
        return _err(f"Invalid function_name: {function_name}",
                    error_name="INVALID_FUNCTION_NAME",
                    payload_example={"function_name": "runGetFootageToTarget",
                                      "args": ["D:/target"]})
    try:
        args_js = json.dumps(args_list, ensure_ascii=False)
    except Exception as exc:
        return _err(f"args serialize failed: {exc}",
                    error_name="ARGS_SERIALIZE_FAILED")
    expr = (
        "(async () => {"
        f"  if (typeof {function_name} !== 'function') {{"
        f"    return {{__not_a_function: true, type: typeof {function_name}}};"
        "  }"
        f"  const args = {args_js};"
        f"  const v = await Promise.resolve({function_name}.apply(null, args));"
        "  return {result: v};"
        "})()"
    )
    t0 = time.time()
    try:
        invocation_result = await state["page"].evaluate(expr)
    except Exception as exc:
        return _err(f"invoke_app_function eval failed: {exc}",
                    error_name="INVOKE_EVAL_FAILED",
                    function_name=function_name)
    if isinstance(invocation_result, dict) and invocation_result.get("__not_a_function"):
        return _err(f"global '{function_name}' is not a function (type: {invocation_result.get('type')})",
                    error_name="FUNCTION_NOT_FOUND",
                    function_name=function_name,
                    ca_next_action="call_inspect_project_usage_to_find_correct_handler")
    return_value = invocation_result.get("result") if isinstance(invocation_result, dict) else invocation_result
    polled_value = None
    polled_timeout_reached = False
    if poll_until_global:
        if not _re_n.match(r"^[A-Za-z_$][\w$]*$", poll_until_global):
            return _err(f"Invalid poll_until_global: {poll_until_global}",
                        error_name="INVALID_POLL_GLOBAL_NAME")
        deadline = time.time() + max(0.1, timeout_ms / 1000.0)
        interval = max(0.1, poll_interval_ms / 1000.0)
        while time.time() < deadline:
            try:
                cur = await state["page"].evaluate(f"(window['{poll_until_global}'])")
            except Exception:
                cur = None
            if cur:
                polled_value = cur
                break
            await _aio.sleep(interval)
        else:
            polled_timeout_reached = True
    elapsed_ms = int((time.time() - t0) * 1000)
    return _ok({
        "function_name": function_name,
        "args_count": len(args_list),
        "return_value": return_value,
        "polled_global": poll_until_global,
        "polled_value": polled_value,
        "polled_timeout_reached": polled_timeout_reached,
        "elapsed_ms": elapsed_ms,
    })


async def launch_electron_with_cdp(launcher_path: str = "", project_path: str = "",
                                     cdp_port: int = 9223,
                                     verify_after_ms: int = 6000) -> str:
    """V15 R16 — One-shot Electron launch with CDP port injection + auto-attach.

    Steps:
      1. If launcher_path empty, call inspect_project_runtime(project_path)
         to find an electron-bat / electron-ps1 launcher.
      2. Read launcher; replace 'electron .' / 'npx electron .' with
         '... --remote-debugging-port=<cdp_port>' equivalent.
      3. Run via run_project_command(background=True, expected_port, verify_after_launch_ms).
      4. After verify, fetch /json/version → call cdp_attach_target(websocket_url).
      5. Return {attach_id, page_id, cdp_port, target_title}.

    Token required (mutating).
    """
    import json as _json
    import re as _re
    import urllib.request as _ur

    from code_tools import run_project_command
    chosen = launcher_path
    # 1. resolve launcher
    if not chosen:
        if not project_path:
            return _err("Provide launcher_path or project_path",
                        error_name="MISSING_REQUIRED_FIELD",
                        missing_fields=["launcher_path", "project_path"])
        inspect = _json.loads(await inspect_project_runtime(project_path))
        chosen = inspect.get("launcher_path") if isinstance(inspect, dict) else None
        if not chosen:
            return _err(f"Could not find Electron launcher in {project_path}",
                        error_name="ELECTRON_LAUNCHER_NOT_FOUND")
    lp = Path(chosen)
    if not lp.exists():
        return _err(f"launcher not found: {chosen}", error_name="LAUNCHER_NOT_FOUND")
    proj_dir = str(lp.parent.resolve())

    # 2. Build command. We DO NOT modify the bat file; we construct an inline
    # command that mirrors the launcher's intent + appends the port flag.
    try:
        bat_text = lp.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return _err(f"cannot read launcher: {exc}", error_name="LAUNCHER_READ_FAILED")

    # V15 R16.2 — walk ALL `cd /d` / `cd "..."` / `pushd` lines, accumulate cwd.
    # Stop at first electron-invocation line so subsequent cd's don't affect launch cwd.
    # %~dp0 substitutes to launcher's directory.
    bat_dir = lp.parent.resolve()
    current_cwd = bat_dir
    cd_chain: list[str] = []
    pushd_stack: list[Path] = []
    for line in bat_text.splitlines():
        ll = line.strip()
        lll = ll.lower()
        if lll.startswith("rem") or lll.startswith("::") or not ll:
            continue
        # Stop on electron line — cwd at THIS point is what matters
        if "electron" in lll and not lll.startswith("cd") and not lll.startswith("pushd"):
            break
        # popd → restore
        if lll.startswith("popd"):
            if pushd_stack:
                current_cwd = pushd_stack.pop()
                cd_chain.append(f"popd → {current_cwd}")
            continue
        # cd or pushd
        m = _re.match(r"^\s*(cd|pushd)\s+(/d\s+)?\"?([^\"]+)\"?\s*$", ll, _re.IGNORECASE)
        if not m:
            continue
        verb = m.group(1).lower()
        cd_path = m.group(3).strip()
        cd_path = cd_path.replace("%~dp0", str(bat_dir)).replace("%CD%", str(current_cwd))
        if Path(cd_path).is_absolute():
            new_cwd = Path(cd_path).resolve()
        else:
            new_cwd = (current_cwd / cd_path).resolve()
        if not new_cwd.exists():
            cd_chain.append(f"{verb} '{cd_path}' → SKIPPED (path missing)")
            continue
        if verb == "pushd":
            pushd_stack.append(current_cwd)
        current_cwd = new_cwd
        cd_chain.append(f"{verb} → {current_cwd}")
    if current_cwd != bat_dir:
        proj_dir = str(current_cwd)

    # Look for an electron invocation line
    electron_line = None
    for line in bat_text.splitlines():
        ll = line.strip().lower()
        if "electron" in ll and not ll.startswith("rem") and not ll.startswith("::") and not ll.startswith("cd"):
            electron_line = line.strip()
            break
    # R72: launcher may invoke electron INDIRECTLY via `npm start` / `npm run <name>`
    # (e.g. start_text-diff-tool.bat -> `call npm start`; package.json scripts.start="electron .").
    # Resolve via package.json scripts; if the resolved script runs electron, synthesize an npx-electron line.
    if not electron_line:
        _npm_name = None
        for _l in bat_text.splitlines():
            _ls = _l.strip().lower()
            if _ls.startswith('rem') or _ls.startswith('::'):
                continue
            _mm = _re.search(r"\bnpm\s+(?:run\s+([\w:.\-]+)|(start))\b", _l, _re.IGNORECASE)
            if _mm:
                _npm_name = _mm.group(1) or 'start'
                break
        if _npm_name:
            try:
                _pj = _json.loads((Path(proj_dir) / 'package.json').read_text(encoding='utf-8', errors='ignore'))
                _script = str((_pj.get('scripts') or {}).get(_npm_name) or '')
            except Exception:
                _script = ''
            if 'electron' in _script.lower():
                electron_line = 'npx ' + _script  # npx resolves local node_modules/.bin/electron; port flag appended below
    if not electron_line:
        return _err("No electron command in launcher (and no npm script resolved to electron)",
                    error_name="ELECTRON_COMMAND_NOT_FOUND",
                    launcher_path=str(lp))

    # V15 R16.1 — pre-install check: if `npx electron` and no node_modules/electron
    # → npm install will trigger an interactive 'Ok to proceed?' prompt in background
    # → process hangs. Detect + add `--yes`.
    needs_install = False
    electron_modules_dir = Path(proj_dir) / "node_modules" / "electron"
    if "npx" in electron_line.lower() and not electron_modules_dir.exists():
        needs_install = True

    if "--remote-debugging-port" in electron_line:
        cmd = f'"{str(lp)}"'  # already has port; run launcher as-is
    else:
        body = electron_line
        body = _re.sub(r"^\s*(call|start\s+/[a-z0-9]+|cmd\s+/c)\s+", "", body, flags=_re.IGNORECASE)
        # V15 R16.1 — auto-inject `--yes` for `npx <pkg>` to skip install confirm prompt.
        # Without `--yes`, npx prompts 'Ok to proceed?' → background process hangs.
        body = _re.sub(r"^\s*npx\s+(?!--yes\b|-y\b)", "npx --yes ", body, flags=_re.IGNORECASE)
        cmd = f"{body} --remote-debugging-port={cdp_port}"

    if needs_install:
        return _err(
            "Electron not installed in node_modules; npx would prompt 'Ok to proceed' and hang background process. "
            "Pre-install before launching: cd into target dir and run `npm install electron` once.",
            error_name="ELECTRON_NOT_INSTALLED",
            ca_next_action="ask_user_to_npm_install_electron_in_target_dir",
            target_dir=str(proj_dir),
            install_command=f'cd /d "{proj_dir}" && npm install electron',
            note="V15 R16.1: --yes will be auto-injected on retry, but first install primes the cache and avoids long network wait inside detached process.",
        )
    # 3. Launch background
    raw = await run_project_command(
        project_path=proj_dir, command=cmd,
        shell_mode="auto", background=True,
        expected_port=cdp_port, verify_after_launch_ms=verify_after_ms,
    )
    try:
        rc = _json.loads(raw)
    except Exception:
        rc = {"ok": False, "error": "run_project_command non-json", "raw": raw[:300]}
    if not rc.get("ok") and rc.get("error_name") not in {None, ""}:
        return _err(f"launch failed: {rc.get('error')}",
                    error_name=rc.get("error_name", "ELECTRON_LAUNCH_FAILED"),
                    rc=rc)
    # 4. Browser-level WS via /json/version
    # V15 R62: requested cdp_port may NOT match where the app actually listens
    # (Electron main.js can override via app.commandLine.appendSwitch which
    # supersedes our --remote-debugging-port=<n> command-line flag, or the
    # launcher .bat hardcodes a different port). On primary-port fail, scan
    # common alternates so we surface the ACTUAL port instead of an opaque
    # "unreachable" — saves a fix-test-restart round trip.
    # V15 R63: added 3098 — Tr_Rawcut_Bot/1.getlink (Sheet Scanner UI) hardcodes
    # that port via app.commandLine.appendSwitch in its main.js (confirmed live
    # via rawcut_getfootage_sheet_v0 workflow). Ordered by observed frequency.
    _R62_FALLBACK_PORTS = [9223, 9222, 3098, 3089, 8315, 9221, 9224]
    ws_url = ""
    browser_name = ""
    primary_err = None
    try:
        with _ur.urlopen(f"http://localhost:{cdp_port}/json/version", timeout=3) as resp:
            ver = _json.loads(resp.read().decode("utf-8"))
        ws_url = ver.get("webSocketDebuggerUrl") or ""
        browser_name = ver.get("Browser", "")
    except Exception as exc:
        primary_err = str(exc)
        # R62 fallback: scan common ports for an actual responder
        detected_port = 0
        detected_ws = ""
        detected_browser = ""
        scan_results = []
        for _alt in _R62_FALLBACK_PORTS:
            if _alt == cdp_port:
                scan_results.append({"port": _alt, "status": "primary_attempted", "ok": False})
                continue
            try:
                with _ur.urlopen(f"http://localhost:{_alt}/json/version", timeout=1) as r2:
                    v2 = _json.loads(r2.read().decode("utf-8"))
                _ws = v2.get("webSocketDebuggerUrl") or ""
                if _ws:
                    detected_port = _alt
                    detected_ws = _ws
                    detected_browser = v2.get("Browser", "")
                    scan_results.append({"port": _alt, "status": "responding", "ok": True, "browser": detected_browser})
                    break
                scan_results.append({"port": _alt, "status": "no_websocket", "ok": False})
            except Exception as _e:
                scan_results.append({"port": _alt, "status": "unreachable", "ok": False})
        if detected_port:
            # Surface the real port so CA can retry with cdp_port=<detected> instead of guessing
            return _err(
                f"CDP /json/version unreachable on requested port {cdp_port}, but port {detected_port} IS responding. "
                f"Likely cause: Electron main.js called app.commandLine.appendSwitch('remote-debugging-port', '{detected_port}') "
                f"which overrides the --remote-debugging-port={cdp_port} flag we injected. Retry launch_electron_with_cdp with cdp_port={detected_port}.",
                error_name="CDP_PORT_MISMATCH",
                cdp_port=cdp_port,
                detected_port=detected_port,
                detected_browser=detected_browser,
                detected_websocket_url=detected_ws,
                fallback_scan=scan_results,
                primary_error=primary_err,
                ca_next_action=f"retry_with_cdp_port={detected_port}",
                rc=rc,
            )
        return _err(f"CDP /json/version unreachable on port {cdp_port}: {primary_err}. Fallback scan of {_R62_FALLBACK_PORTS} found no responder.",
                    error_name="CDP_UNREACHABLE_AFTER_LAUNCH",
                    cdp_port=cdp_port,
                    fallback_scan=scan_results,
                    rc=rc)
    if not ws_url:
        return _err("CDP did not expose webSocketDebuggerUrl",
                    error_name="CDP_NO_WEBSOCKET", cdp_port=cdp_port)
    # 5. Attach (V15 R44: pass project_path so the registered page state
    # carries it for downstream session-guard resolution).
    attach_raw = await cdp_attach_target(websocket_url=ws_url, cdp_port=cdp_port,
                                            project_path=project_path or proj_dir)
    try:
        attach = _json.loads(attach_raw)
    except Exception:
        return _err("cdp_attach_target returned non-json",
                    error_name="ATTACH_PARSE_FAILED")
    if not attach.get("ok"):
        return _err(f"attach failed after launch: {attach.get('error')}",
                    error_name=attach.get("error_name", "CDP_ATTACH_FAILED"),
                    launch_rc=rc, attach=attach)
    # V15 R44 — defensive pin in case cdp_attach_target predates the
    # project_path param (e.g. monkey-patched). Idempotent.
    _aid = attach.get("attach_id") or attach.get("page_id")
    if _aid and _aid in _BROWSER_PAGES:
        _BROWSER_PAGES[_aid].setdefault("project_path", project_path or proj_dir)
    return _ok({
        "attach_id": attach.get("attach_id"),
        "page_id": attach.get("page_id"),
        "cdp_port": cdp_port,
        "target_title": attach.get("title", ""),
        "url": attach.get("url", ""),
        "attach_via": attach.get("attach_via"),
        "browser": browser_name,
        "launcher_path": str(lp),
        "launch_cwd": proj_dir,
        "cd_chain": cd_chain,
        "command_executed": cmd,
        "launch_pid": rc.get("pid") or rc.get("background_pid"),
        "usage_hint": "Pass `page_id` to click/fill_value/dom_state_diff/extract_log_section.",
    })


# ─── R16 DOM helpers ────────────────────────────────────────────────────────

_DOM_SNAPSHOTS: dict = {}  # {snapshot_id: {fields, ts, page_id, selector}}
_DOM_SNAPSHOT_TTL_S = 600


def _dom_snapshot_js(selector: str, fields: list) -> str:
    """Build a Playwright-evaluatable JS expression for snapshot."""
    field_set = set(fields)
    # Always safe-fields: disabled, bg, color, className, visible, text
    return (
        "(() => {"
        f"  const el = document.querySelector({json.dumps(selector)});"
        "  if (!el) return {found: false};"
        "  const cs = getComputedStyle(el);"
        "  return {"
        "    found: true,"
        + ("    disabled: !!el.disabled," if "disabled" in field_set else "")
        + ("    bg: cs.backgroundColor," if "bg" in field_set else "")
        + ("    color: cs.color," if "color" in field_set else "")
        + ("    className: el.className || ''," if "className" in field_set else "")
        + ("    visible: el.offsetParent !== null,"
           if "visible" in field_set else "")
        + ("    text: (el.textContent||'').slice(0,200)," if "text" in field_set else "")
        + ("    value: ('value' in el ? String(el.value).slice(0,200) : null),"
           if "value" in field_set else "")
        + ("    boundingClientRect: el.getBoundingClientRect().toJSON(),"
           if "rect" in field_set else "")
        + "  };"
        "})()"
    )


async def dom_state_diff(page_id: str, selector: str,
                          baseline_id: str = "",
                          snapshot_fields: list | None = None) -> str:
    """V15 R16 — DOM state snapshot + delta comparison.

    Mode 1 (no baseline_id): take fresh snapshot → return snapshot_id (cached TTL 10min).
    Mode 2 (baseline_id set): take fresh snapshot → compare with cached → return diff.

    Use case: detect button state/color change after action without polling raw DOM.
    """
    if snapshot_fields is None:
        snapshot_fields = ["disabled", "bg", "color", "className", "visible", "text"]
    state, err = await _get_page(page_id)
    if err:
        return err
    # Prune expired
    now = time.time()
    for sid in list(_DOM_SNAPSHOTS.keys()):
        if now - _DOM_SNAPSHOTS[sid]["ts"] > _DOM_SNAPSHOT_TTL_S:
            del _DOM_SNAPSHOTS[sid]
    expr = _dom_snapshot_js(selector, snapshot_fields)
    try:
        snap = await state["page"].evaluate(expr)
    except Exception as exc:
        return _err(f"snapshot failed: {exc}", error_name="DOM_SNAPSHOT_FAILED")
    if not snap or not snap.get("found"):
        return _err(f"selector not found: {selector}",
                    error_name="SELECTOR_NOT_FOUND",
                    selector=selector)
    snap.pop("found", None)
    snap_ts = datetime.now().isoformat(timespec="seconds")
    if not baseline_id:
        sid = f"dom-{int(time.time() * 1000)}-{len(_DOM_SNAPSHOTS)}"
        _DOM_SNAPSHOTS[sid] = {
            "fields": snap, "ts": now, "page_id": page_id, "selector": selector,
            "snap_ts": snap_ts,
        }
        return _ok({
            "snapshot_id": sid, "page_id": page_id, "selector": selector,
            # V15 R79: expose the captured field map under BOTH "fields" (canonical) and
            # "snapshot" (ergonomic alias). CA repeatedly read result.snapshot.value and got
            # undefined because the data only lived under "fields"; the alias removes that
            # trial-and-error. Supported snapshot_fields include: disabled, bg, color,
            # className, visible, text, value, rect.
            "snap_ts": snap_ts, "fields": snap, "snapshot": snap, "mode": "baseline",
            "usage_hint": ("Read element state from result['fields'] (alias: result['snapshot']). "
                           "Call again with baseline_id=<snapshot_id> to detect changes."),
        })
    # Comparison mode
    base = _DOM_SNAPSHOTS.get(baseline_id)
    if not base:
        return _err(f"baseline_id not found or expired: {baseline_id}",
                    error_name="BASELINE_EXPIRED",
                    ttl_seconds=_DOM_SNAPSHOT_TTL_S,
                    ca_next_action="re_snapshot_then_compare")
    changes: dict = {}
    for k in set(list(snap.keys()) + list(base["fields"].keys())):
        before = base["fields"].get(k)
        after = snap.get(k)
        if before != after:
            changes[k] = {"before": before, "after": after}
    return _ok({
        "page_id": page_id, "selector": selector,
        "baseline_id": baseline_id,
        "baseline_ts": base["snap_ts"],
        "now_ts": snap_ts,
        "changed": bool(changes),
        "changed_fields": list(changes.keys()),
        "changes": changes,
        "current_fields": snap,
        "mode": "diff",
    })


_LOG_BASELINE_CACHE: dict = {}  # {sha_prefix: content}; rolling, max 50
_LOG_BASELINE_TTL_S = 600


def _log_baseline_cache_set(content: str) -> str:
    """V15 R27 — Cache full content keyed by sha256[:16]; rolling 50 entries."""
    if not content:
        return ""
    import hashlib as _hl
    sha = _hl.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
    if sha in _LOG_BASELINE_CACHE:
        return sha
    if len(_LOG_BASELINE_CACHE) >= 50:
        # Drop oldest (FIFO)
        try:
            oldest = next(iter(_LOG_BASELINE_CACHE))
            _LOG_BASELINE_CACHE.pop(oldest, None)
        except Exception:
            pass
    _LOG_BASELINE_CACHE[sha] = content
    return sha


def _log_baseline_cache_get(sha_prefix: str):
    if not sha_prefix:
        return None
    return _LOG_BASELINE_CACHE.get(sha_prefix.lower()[:16])


async def extract_log_section(page_id: str, section_title: str,
                                area_class: str = "log-area",
                                body_selector: str = "",
                                pick_strategy: str = "smart",
                                max_chars: int = 0,
                                tail: bool = False,
                                mode: str = "full",
                                baseline_sha256: str = "") -> str:
    """V15 R16.2 — Extract text from a DOM section identified by its title heading.

    Finds an element whose textContent matches section_title (typically a
    span.result-title or h2/h3), walks up to closest ancestor with class
    `area_class`, then extracts textarea/pre/.log-body content.

    pick_strategy (R16.2):
      "smart" (default): if multiple bodies in the area, prefer in order:
         (1) element with [data-log] attribute
         (2) element with id matching *logArea* / *LogArea* / *log_area*
         (3) longest content-length body
         (4) first match
      "longest": always pick longest content
      "first": legacy behavior (V15 R16 default)

    Response always includes `alternative_bodies` array so CA can re-call
    with explicit body_selector if smart pick was wrong.

    R16.7: `max_chars` (default 0 = no cap) + `tail` (default False = head).
    When set, content is truncated to max_chars; response carries
    `original_length`, `truncated`, `truncate_mode`. Use tail=True for log
    streams (most recent at end). Consistent with read_value/read_all_logs.

    R16.9: `mode` ∈ {"full", "ref", "preview"}.
      "full" (default) = current behavior, returns content
      "ref" = returns sha256 hash + length + 200-char preview only (no content),
              ideal for "does log have new errors?" cheap polling
      "preview" = returns first 200 + last 200 chars, length, hash; saves 95% tokens
                  vs full while preserving error signatures
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    body_sel = body_selector or "textarea, pre, .log-body, .log-content, [data-log]"
    title_js = json.dumps(section_title)
    area_js = json.dumps(area_class)
    body_js = json.dumps(body_sel)
    # V15 R16.2 — collect ALL bodies per area (not just first); CA tool picks best
    expr = (
        "(() => {"
        "  const all = Array.from(document.querySelectorAll('span,h1,h2,h3,h4,.result-title,.section-title,[data-section-title]'));"
        f"  const matches = all.filter(e => (e.textContent||'').trim() === {title_js} || (e.textContent||'').includes({title_js}));"
        "  if (matches.length === 0) return {found_title: false};"
        "  const candidates = [];"
        "  for (const t of matches) {"
        f"    const area = t.closest('.' + {area_js});"
        "    if (!area) continue;"
        f"    const bodies = Array.from(area.querySelectorAll({body_js}));"
        "    if (bodies.length === 0) continue;"
        "    const bodyMetas = bodies.map(b => {"
        "      const v = ('value' in b ? b.value : b.textContent) || '';"
        "      const sel = b.id ? ('#' + b.id) : (b.tagName.toLowerCase() + (b.className ? '.' + String(b.className).split(/\\s+/).filter(Boolean).join('.') : ''));"
        "      return {"
        "        body_tag: b.tagName,"
        "        body_id: b.id || '',"
        "        body_class_list: Array.from(b.classList || []),"
        "        body_has_data_log: b.hasAttribute('data-log'),"
        "        body_value: v,"
        "        body_length: v.length,"
        "        body_selector_hint: sel,"
        "      };"
        "    });"
        "    candidates.push({"
        "      title_text: (t.textContent||'').slice(0,200),"
        "      area_tag: area.tagName,"
        "      area_class_list: Array.from(area.classList),"
        "      bodies: bodyMetas,"
        "    });"
        "  }"
        "  return {found_title: true, candidate_count: matches.length, area_matches: candidates.length, candidates: candidates.slice(0,3)};"
        "})()"
    )
    try:
        data = await state["page"].evaluate(expr)
    except Exception as exc:
        return _err(f"extract failed: {exc}",
                    error_name="DOM_EVAL_FAILED",
                    section_title=section_title)
    if not data.get("found_title"):
        return _err(f"No element with title text '{section_title}'",
                    error_name="SECTION_TITLE_NOT_FOUND",
                    section_title=section_title,
                    ca_next_action="inspect_dom_with_evaluate_js_or_query_selector")
    if data.get("area_matches", 0) == 0:
        return _err(f"Title found but no '.{area_class}' ancestor",
                    error_name="LOG_AREA_NOT_FOUND",
                    section_title=section_title,
                    area_class=area_class,
                    candidate_count=data.get("candidate_count"))

    # V15 R16.2 — pick best body within FIRST matched area
    cand = data["candidates"][0]
    bodies = cand.get("bodies", []) or []
    if not bodies:
        return _err("No body element in matched area",
                    error_name="LOG_BODY_NOT_FOUND",
                    section_title=section_title)

    def _pick(strat: str):
        if strat == "first":
            return bodies[0], "first"
        if strat == "longest":
            return max(bodies, key=lambda b: b.get("body_length", 0)), "longest_content"
        # smart
        # (1) data-log attr
        with_data_log = [b for b in bodies if b.get("body_has_data_log")]
        if with_data_log:
            return with_data_log[0], "data_log_attr"
        # (2) id matching *logarea* / *log_area* (case-insensitive)
        import re as _re_pick
        id_match = [b for b in bodies if _re_pick.search(r"log.?area", b.get("body_id", ""), _re_pick.IGNORECASE)]
        if id_match:
            return id_match[0], "id_matches_logarea"
        # (3) longest content
        non_empty = [b for b in bodies if b.get("body_length", 0) > 0]
        if non_empty:
            picked = max(non_empty, key=lambda b: b.get("body_length", 0))
            return picked, "longest_content"
        # (4) first
        return bodies[0], "first_fallback"

    picked, pick_reason = _pick(pick_strategy)
    body_value = picked.get("body_value", "")
    original_length = len(body_value)
    # R16.9: content_ref mode — compute hash + preview WITHOUT returning full content
    import hashlib as _hl
    content_sha = _hl.sha256(body_value.encode("utf-8", errors="replace")).hexdigest()[:16] if body_value else ""
    head_200 = body_value[:200]
    tail_200 = body_value[-200:] if original_length > 200 else ""
    mode_norm = (mode or "full").lower()
    truncated = False
    # V15 R27: log delta since baseline (when baseline_sha256 matches a cached snapshot)
    new_content_since_baseline = None
    baseline_matched = False
    if baseline_sha256:
        cached = _log_baseline_cache_get(baseline_sha256)
        if cached is not None:
            baseline_matched = True
            # Find common prefix length to determine "new tail"
            common_len = 0
            shorter = min(len(cached), original_length)
            full_body = body_value  # full unmodified original
            for i in range(shorter):
                if cached[i] != full_body[i]:
                    break
                common_len = i + 1
            new_content_since_baseline = full_body[common_len:]
    # R27: cache the FULL current content for next-round baseline (before any mode trim)
    current_sha = _log_baseline_cache_set(body_value)
    if mode_norm == "ref":
        body_value = ""  # discard, return only metadata
    elif mode_norm == "preview":
        body_value = head_200 + ("\n...[truncated " + str(max(0, original_length - 400)) + " chars]...\n" + tail_200 if original_length > 400 else "")
        truncated = original_length > 400
    elif max_chars and original_length > max_chars:
        body_value = body_value[-max_chars:] if tail else body_value[:max_chars]
        truncated = True

    # Alternative bodies for CA inspection (omit picked one, omit content but keep length)
    alternatives = []
    for b in bodies:
        if b is picked:
            continue
        alternatives.append({
            "body_tag": b.get("body_tag"),
            "body_id": b.get("body_id"),
            "body_class_list": b.get("body_class_list"),
            "body_length": b.get("body_length"),
            "body_selector_hint": b.get("body_selector_hint"),
            "tail_60": (b.get("body_value") or "")[-60:],
        })

    return _ok({
        "page_id": page_id,
        "section_title": section_title,
        "area_class": area_class,
        "body_selector_used": body_sel,
        "pick_strategy": pick_strategy,
        "pick_reason": pick_reason,
        "content": body_value,
        "content_length": len(body_value),
        "original_length": original_length,
        "truncated": truncated,
        "truncate_mode": ("tail" if tail else "head") if truncated else "",
        "max_chars": max_chars,
        "mode": mode_norm,
        "content_sha256_prefix": content_sha,
        "head_200": head_200,
        "tail_200": tail_200,
        # V15 R24: explicit ref-mode flags so CA doesn't misread content:"" as empty
        "content_omitted": (mode_norm == "ref"),
        "ref_only": (mode_norm == "ref"),
        "is_empty": (original_length == 0),
        # V15 R27: log delta since baseline
        "current_baseline_sha256_prefix": current_sha,
        "baseline_sha256_input": baseline_sha256,
        "baseline_matched": baseline_matched,
        "new_content_since_baseline": new_content_since_baseline,
        "new_content_length": len(new_content_since_baseline) if new_content_since_baseline else 0,
        "candidate_count": data["candidate_count"],
        "area_matches": data["area_matches"],
        "bodies_in_area": len(bodies),
        "selected_candidate_meta": {
            "title_text": cand.get("title_text"),
            "area_tag": cand.get("area_tag"),
            "area_class_list": cand.get("area_class_list"),
            "body_tag": picked.get("body_tag"),
            "body_id": picked.get("body_id"),
            "body_selector_hint": picked.get("body_selector_hint"),
        },
        "alternative_bodies": alternatives,
        "other_candidates": data["candidates"][1:],
    })


async def read_all_logs(page_id: str, panels: list | None = None,
                          area_class: str = "log-area",
                          encoding: str = "plain", max_chars: int = 30000,
                          include_view_panels: bool = False,
                          view_id_pattern: str = "^view-",
                          auto_artifact: bool = False,
                          project_path: str = "",
                          round_id: str = "",
                          auto_artifact_max_bytes: int = 4096,
                          tail: bool = False) -> str:
    """V15 R16.4 — Aggregate read for multiple log panels in ONE call.

    Default panels = ["System Error Log", "User Error & Help Panel",
                      "Agent Task Log", "AI Log Analysis Panel"].

    Each panel is extracted via the same logic as extract_log_section (smart pick,
    prefer id_matches_logarea > longest > first). Returns a dict keyed by panel
    title with content/length/is_empty per panel.

    Saves N round-trips when CA needs to dump all log views in one shot.
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    if encoding not in {"plain", "base64"}:
        return _err(f"encoding must be plain|base64, got '{encoding}'",
                    error_name="UNSUPPORTED_FIELD",
                    accepted_encodings=["plain", "base64"])
    default_panels = [
        "System Error Log", "User Error & Help Panel",
        "Agent Task Log", "AI Log Analysis Panel",
    ]
    titles = panels if panels else default_panels
    if not isinstance(titles, list):
        return _err("panels must be a list of title strings",
                    error_name="UNSUPPORTED_FIELD",
                    accepted_fields=["page_id", "panels", "area_class", "encoding", "max_chars"])
    import json as _json
    titles_js = _json.dumps(titles)
    area_js = json.dumps(area_class)
    # Single batched evaluate to find each panel + best body
    expr = (
        "((titles, areaClass) => {"
        "  const results = {};"
        "  for (const title of titles) {"
        "    const elems = Array.from(document.querySelectorAll('span,h1,h2,h3,h4,.result-title,.section-title,[data-section-title]'));"
        "    const matched = elems.filter(e => (e.textContent||'').trim() === title || (e.textContent||'').includes(title));"
        "    if (matched.length === 0) { results[title] = {found:false}; continue; }"
        "    const t = matched[0];"
        "    const area = t.closest('.' + areaClass);"
        "    if (!area) { results[title] = {found_title:true, area_found:false}; continue; }"
        "    const bodies = Array.from(area.querySelectorAll('textarea, pre, .log-body, .log-content, [data-log]'));"
        "    if (bodies.length === 0) { results[title] = {found_title:true, area_found:true, body_found:false}; continue; }"
        "    let picked = null; let pickReason = '';"
        "    const withDataLog = bodies.find(b => b.hasAttribute('data-log'));"
        "    if (withDataLog) { picked = withDataLog; pickReason = 'data_log_attr'; }"
        "    if (!picked) {"
        "      const idMatch = bodies.find(b => /log.?area/i.test(b.id || ''));"
        "      if (idMatch) { picked = idMatch; pickReason = 'id_matches_logarea'; }"
        "    }"
        "    if (!picked) {"
        "      let bestLen = -1;"
        "      for (const b of bodies) {"
        "        const v = ('value' in b ? b.value : b.textContent) || '';"
        "        if (v.length > bestLen) { bestLen = v.length; picked = b; pickReason = 'longest_content'; }"
        "      }"
        "    }"
        "    const v = ('value' in picked ? picked.value : picked.textContent) || '';"
        "    results[title] = {"
        "      found_title: true, area_found: true, body_found: true,"
        "      pick_reason: pickReason,"
        "      body_tag: picked.tagName, body_id: picked.id || '',"
        "      length: v.length, content: v,"
        "    };"
        "  }"
        "  return results;"
        f"}})({titles_js}, {area_js})"
    )
    try:
        data = await state["page"].evaluate(expr)
    except Exception as exc:
        return _err(f"read_all_logs eval failed: {exc}",
                    error_name="DOM_EVAL_FAILED")
    import base64 as _b64
    out = {}
    for title in titles:
        entry = data.get(title) or {"found_title": False}
        content = entry.get("content") or ""
        truncated = False
        if max_chars and len(content) > max_chars:
            # V15 R30: tail=true returns last max_chars instead of first (log streams)
            content = content[-max_chars:] if tail else content[:max_chars]
            truncated = True
        if encoding == "base64" and entry.get("body_found"):
            content_out = _b64.b64encode(content.encode("utf-8")).decode("ascii")
        else:
            content_out = content
        # V15 R16.6 — hint when title found but no body (CA1 was confused)
        hint = None
        if entry.get("found_title") and not entry.get("area_found"):
            hint = f"Title '{title}' found but no '.{area_class}' ancestor — section uses different layout"
        elif entry.get("area_found") and not entry.get("body_found"):
            hint = "Section exists but contains no textarea/pre/.log-body — likely uses div+textContent. Try evaluate_js inspection or describe_page tool."
        out[title] = {
            "found_title": entry.get("found_title", False),
            "area_found": entry.get("area_found", False),
            "body_found": entry.get("body_found", False),
            "pick_reason": entry.get("pick_reason"),
            "body_tag": entry.get("body_tag"),
            "body_id": entry.get("body_id"),
            "length": entry.get("length", 0),
            "is_empty": entry.get("body_found", False) and (entry.get("length", 0) == 0 or (entry.get("content") or "").strip() == ""),
            "truncated": truncated,
            "content": content_out,
            "hint": hint,
        }

    # V15 R16.6 — view-* panel enumeration
    view_panels: dict = {}
    if include_view_panels:
        pat_js = json.dumps(view_id_pattern)
        view_expr = (
            "((pat) => {"
            "  const rx = new RegExp(pat);"
            "  const tas = Array.from(document.querySelectorAll('textarea'));"
            "  const matched = tas.filter(t => rx.test(t.id || ''));"
            "  const out = {};"
            "  for (const t of matched) {"
            "    const v = t.value || '';"
            "    out[t.id] = {"
            "      tag: t.tagName, id: t.id, length: v.length,"
            "      visible: t.offsetParent !== null,"
            "      content: v,"
            "    };"
            "  }"
            "  return out;"
            f"}})({pat_js})"
        )
        try:
            raw = await state["page"].evaluate(view_expr)
        except Exception:
            raw = {}
        for vid, vmeta in (raw or {}).items():
            content = vmeta.get("content") or ""
            truncated = False
            if max_chars and len(content) > max_chars:
                content = content[:max_chars]
                truncated = True
            if encoding == "base64":
                content_out = _b64.b64encode(content.encode("utf-8")).decode("ascii")
            else:
                content_out = content
            view_panels[vid] = {
                "tag": vmeta.get("tag"),
                "id": vid,
                "length": vmeta.get("length", 0),
                "visible": vmeta.get("visible", False),
                "is_empty": vmeta.get("length", 0) == 0,
                "truncated": truncated,
                "content": content_out,
            }

    _response = {
        "page_id": page_id,
        "encoding": encoding,
        "area_class": area_class,
        "panels_requested": titles,
        "panels_found": sum(1 for r in out.values() if r["found_title"]),
        "panels_with_content": sum(1 for r in out.values() if r["body_found"] and not r["is_empty"]),
        "max_chars": max_chars,
        "results": out,
        "view_panels_included": bool(include_view_panels),
        "view_panels_count": len(view_panels),
        "view_panels": view_panels,
    }
    # R18 Phase C — auto-artifact when response oversize and CA opted in
    _response = _auto_artifact_if_oversize(_response, project_path=project_path,
                                            tool_name="read_all_logs",
                                            round_id=round_id,
                                            max_bytes=auto_artifact_max_bytes,
                                            enabled=auto_artifact)
    return _ok(_response)


async def query_dom_candidates(page_id: str, filter: str = "all",
                                 max_items: int = 200,
                                 auto_artifact: bool = False,
                                 project_path: str = "",
                                 round_id: str = "",
                                 auto_artifact_max_bytes: int = 4096) -> str:
    """V15 R16.6 — Enumerate DOM input/button/textarea/etc candidates with metadata.

    Replaces CA's evaluate_js enumeration boilerplate. Returns visible elements
    with {tag, id, name, placeholder, type, value_length, text_preview, visible,
    selector_hint, classes}.

    filter ∈ {"all", "inputs", "buttons", "textareas", "links", "contenteditable"}.
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    accepted = {"all", "inputs", "buttons", "textareas", "links", "contenteditable"}
    if filter not in accepted:
        return _err(f"filter must be one of {sorted(accepted)}",
                    error_name="UNSUPPORTED_FIELD",
                    accepted_filters=sorted(accepted))
    sel_map = {
        "all": "input, button, textarea, [contenteditable=\"true\"], a[href], select",
        "inputs": "input",
        "buttons": "button, input[type=button], input[type=submit]",
        "textareas": "textarea",
        "links": "a[href]",
        "contenteditable": "[contenteditable=\"true\"]",
    }
    sel = sel_map[filter]
    sel_js = json.dumps(sel)
    max_js = int(max_items)
    expr = (
        "(() => {"
        f"  const elems = Array.from(document.querySelectorAll({sel_js})).slice(0, {max_js});"
        "  return elems.map(el => {"
        "    const has_value = ('value' in el);"
        "    const v = has_value ? String(el.value || '') : '';"
        "    const txt = (el.textContent || '').trim();"
        "    const classList = Array.from(el.classList || []);"
        "    return {"
        "      tag: el.tagName,"
        "      id: el.id || '',"
        "      name: el.name || '',"
        "      type: el.type || '',"
        "      placeholder: el.placeholder || '',"
        "      has_value_property: has_value,"
        "      value_length: v.length,"
        "      text_preview: txt.slice(0, 100),"
        "      visible: el.offsetParent !== null,"
        "      classes: classList.slice(0, 6),"
        "      selector_hint: el.id ? ('#' + el.id) : (el.tagName.toLowerCase() + (classList[0] ? '.' + classList[0] : '')),"
        "      disabled: !!el.disabled,"
        "    };"
        "  });"
        "})()"
    )
    try:
        items = await state["page"].evaluate(expr)
    except Exception as exc:
        return _err(f"query_dom_candidates eval failed: {exc}",
                    error_name="DOM_EVAL_FAILED")
    items = items or []
    visible_only = [it for it in items if it.get("visible")]
    by_tag: dict = {}
    for it in items:
        t = it.get("tag", "?")
        by_tag[t] = by_tag.get(t, 0) + 1
    _response = {
        "page_id": page_id,
        "filter": filter,
        "total": len(items),
        "visible_count": len(visible_only),
        "by_tag_count": by_tag,
        "items": items,
    }
    _response = _auto_artifact_if_oversize(_response, project_path=project_path,
                                            tool_name="query_dom_candidates",
                                            round_id=round_id,
                                            max_bytes=auto_artifact_max_bytes,
                                            enabled=auto_artifact)
    return _ok(_response)


async def describe_page(page_id: str,
                         area_class: str = "log-area",
                         view_id_pattern: str = "^view-",
                         auto_artifact: bool = False,
                         project_path: str = "",
                         round_id: str = "",
                         auto_artifact_max_bytes: int = 4096) -> str:
    """V15 R16.6 — DOM structure map: lists all sections (titled containers)
    + view-* panels + a high-level summary. One call replaces the
    extract_log_section trial-and-error loop CA had to do.

    Returns:
      sections: [{title, area_class_list, body_count, body_ids, body_tags}]
      view_panels: [{id, length, visible}]
      summary: {section_count, view_panel_count, total_textareas, total_inputs, total_buttons}
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    area_js = json.dumps(area_class)
    pat_js = json.dumps(view_id_pattern)
    expr = (
        "((areaClass, viewPat) => {"
        "  const rxView = new RegExp(viewPat);"
        "  const titleNodes = Array.from(document.querySelectorAll("
        "    'span,h1,h2,h3,h4,.result-title,.section-title,[data-section-title]'));"
        "  const seen = new Set();"
        "  const sections = [];"
        "  for (const t of titleNodes) {"
        "    const txt = (t.textContent || '').trim();"
        "    if (!txt || txt.length > 100) continue;"
        "    const area = t.closest('.' + areaClass);"
        "    if (!area) continue;"
        "    const areaKey = area;"
        "    if (seen.has(areaKey)) continue;"
        "    seen.add(areaKey);"
        "    const bodies = Array.from(area.querySelectorAll('textarea, pre, .log-body, .log-content, [data-log]'));"
        "    sections.push({"
        "      title: txt.slice(0, 150),"
        "      area_tag: area.tagName,"
        "      area_class_list: Array.from(area.classList || []),"
        "      body_count: bodies.length,"
        "      body_ids: bodies.map(b => b.id || ''),"
        "      body_tags: bodies.map(b => b.tagName),"
        "      body_lengths: bodies.map(b => (('value' in b ? b.value : b.textContent) || '').length),"
        "    });"
        "  }"
        "  const viewTas = Array.from(document.querySelectorAll('textarea')).filter(t => rxView.test(t.id || ''));"
        "  const viewPanels = viewTas.map(t => {"
        "    const v = t.value || '';"
        "    return { id: t.id, length: v.length, visible: t.offsetParent !== null };"
        "  });"
        "  const totals = {"
        "    section_count: sections.length,"
        "    view_panel_count: viewPanels.length,"
        "    total_textareas: document.querySelectorAll('textarea').length,"
        "    total_inputs: document.querySelectorAll('input').length,"
        "    total_buttons: document.querySelectorAll('button').length,"
        "  };"
        "  return { sections, view_panels: viewPanels, summary: totals };"
        f"}})({area_js}, {pat_js})"
    )
    try:
        data = await state["page"].evaluate(expr)
    except Exception as exc:
        return _err(f"describe_page eval failed: {exc}",
                    error_name="DOM_EVAL_FAILED")
    _response = {
        "page_id": page_id,
        "area_class": area_class,
        "view_id_pattern": view_id_pattern,
        "sections": data.get("sections") or [],
        "view_panels": data.get("view_panels") or [],
        "summary": data.get("summary") or {},
        "usage_hint": (
            "Pick a section.title → extract_log_section. "
            "Pick view_panels.id → read_value(selector='#<id>'). "
            "For input fill use query_dom_candidates(filter='inputs')."
        ),
    }
    _response = _auto_artifact_if_oversize(_response, project_path=project_path,
                                            tool_name="describe_page",
                                            round_id=round_id,
                                            max_bytes=auto_artifact_max_bytes,
                                            enabled=auto_artifact)
    return _ok(_response)


async def cleanup_test_session(project_path: str = "",
                                 page_id: str = "",
                                 processes: list | None = None,
                                 close_session_after: bool = True,
                                 idle_guard_signals: list | None = None,
                                 idle_guard_stable_seconds: int = 0,
                                 idle_guard_force: bool = False) -> str:
    """V15 R16.6 — 1-call test wrap-up:
      1. Close CDP page (if page_id given) — close browser session in _BROWSER_PAGES
      2. taskkill listed processes (default: electron.exe + chrome.exe)
      3. close_session for project_path (if close_session_after)

    Token required. Returns per-step success.

    R43 P0 #6A — idle guard:
      Pass idle_guard_signals=[{kind,...}, ...] to refuse cleanup while target is
      still busy (copy in progress, process CPU active). Returns CLEANUP_BLOCKED_TARGET_BUSY
      without killing anything. Bypass with idle_guard_force=True (CA opts-in for
      forced cleanup despite busy signals — must explain in reply).
      idle_guard_stable_seconds: if >0, override per-signal stable_seconds.
    """
    import subprocess as _sp
    if processes is None:
        processes = ["electron.exe", "chrome.exe"]
    result: dict = {
        "ok": True,
        "page_closed": False,
        "processes_killed": [],
        "processes_failed": [],
        "session_closed": False,
    }
    # R43 P0 #6A: idle guard — refuse cleanup if target still busy
    if idle_guard_signals and not idle_guard_force:
        try:
            from code_tools import (
                assert_target_idle as _ati_fn,  # R43 fix: not in tools_v15 scope
            )
            _sigs = list(idle_guard_signals)
            if idle_guard_stable_seconds and isinstance(idle_guard_stable_seconds, int):
                for _s in _sigs:
                    if isinstance(_s, dict):
                        _s["stable_seconds"] = idle_guard_stable_seconds
            _guard_raw = await _ati_fn(signals=_sigs, window_seconds=3)
            _guard = json.loads(_guard_raw)
            _outcome = _guard.get("outcome", "unknown")
            if _outcome != "idle":
                return _err(
                    f"Cleanup blocked: target not idle (outcome={_outcome}). "
                    "Copy/process still busy — would lose completion evidence.",
                    error_name="CLEANUP_BLOCKED_TARGET_BUSY",
                    outcome=_outcome,
                    guard_detail=_guard,
                    ca_next_action="wait_for_idle_then_retry_cleanup_or_force",
                    bypass_hint=(
                        "If you intentionally want to force cleanup despite busy "
                        "target (e.g. test asserted failure), pass idle_guard_force=true "
                        "AND note in your reply to User that cleanup was forced."
                    ),
                    retry_with={
                        "tool": "wait_for_idle",
                        "args": {"signals": _sigs, "stable_seconds": 5, "timeout_seconds": 120}
                    },
                )
        except Exception as _guard_exc:
            result["idle_guard_error"] = str(_guard_exc)
            result["idle_guard_skipped"] = True
    if idle_guard_force and idle_guard_signals:
        result["idle_guard_forced"] = True
    # 1. close page if provided
    if page_id:
        state = _BROWSER_PAGES.get(page_id)
        if state:
            try:
                pw = state.get("pw")
                browser = state.get("browser")
                try:
                    if browser:
                        await browser.close()
                except Exception:
                    pass
                try:
                    if pw:
                        await pw.stop()
                except Exception:
                    pass
                _BROWSER_PAGES.pop(page_id, None)
                result["page_closed"] = True
            except Exception as exc:
                result["page_close_error"] = str(exc)
    # 2. taskkill processes
    for proc_name in processes:
        try:
            r = _sp.run(["taskkill", "/F", "/IM", proc_name, "/T"],
                        capture_output=True, text=True, timeout=10)
            if r.returncode == 0 or "not found" in (r.stderr or "").lower() or "not running" in (r.stdout or "").lower():
                result["processes_killed"].append(proc_name)
            else:
                result["processes_failed"].append({"name": proc_name, "rc": r.returncode, "stderr": (r.stderr or "")[:200]})
        except Exception as exc:
            result["processes_failed"].append({"name": proc_name, "error": str(exc)})
    # 3. close session
    if close_session_after and project_path:
        try:
            import json as _json

            from code_tools import close_session as _close
            raw = await _close(project_path=project_path)
            try:
                cs = _json.loads(raw)
                result["session_closed"] = bool(cs.get("ok"))
                result["session_close_response"] = {
                    "session_id": cs.get("session_id"),
                    "duration_minutes": cs.get("duration_minutes"),
                    "error_name": cs.get("error_name"),
                }
            except Exception:
                result["session_close_response_raw"] = raw[:300]
        except Exception as exc:
            result["session_close_error"] = str(exc)
    return _ok(result)


async def click_selector(page_id: str, selector: str,
                          before_after_state: bool = True,
                          state_fields: list | None = None,
                          timeout_ms: int = 5000) -> str:
    """V15 R16.9 — Richer wrapper around click(). Snapshots target element state
    before + after click so CA gets enabled/disabled/visible/class/bg deltas
    in 1 call. Replaces CA pattern of dom_state_diff → click → dom_state_diff.

    `before_after_state=False` falls back to plain click() behavior.
    `state_fields` default: ["disabled","visible","bg","color","className","value_length"].
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    fields = state_fields or ["disabled", "visible", "bg", "color", "className", "value_length"]
    sel_js = json.dumps(selector)
    fields_js = json.dumps(fields)
    snap_expr = (
        "((sel, fields) => {"
        "  const el = document.querySelector(sel);"
        "  if (!el) return {found: false};"
        "  const cs = getComputedStyle(el);"
        "  const out = {found: true};"
        "  for (const f of fields) {"
        "    if (f === 'disabled') out.disabled = !!el.disabled;"
        "    else if (f === 'visible') out.visible = el.offsetParent !== null;"
        "    else if (f === 'bg') out.bg = cs.backgroundColor;"
        "    else if (f === 'color') out.color = cs.color;"
        "    else if (f === 'className') out.className = el.className || '';"
        "    else if (f === 'value_length') out.value_length = ('value' in el ? String(el.value || '').length : (el.textContent || '').length);"
        "    else if (f === 'text_preview') out.text_preview = (el.textContent || '').trim().slice(0, 100);"
        "  }"
        "  return out;"
        f"}})({sel_js}, {fields_js})"
    )
    before = None
    if before_after_state:
        try:
            before = await state["page"].evaluate(snap_expr)
        except Exception:
            before = None
    try:
        await state["page"].locator(selector).first.click(timeout=timeout_ms)
        clicked = True
        click_err = None
    except Exception as exc:
        clicked = False
        click_err = str(exc)
    after = None
    if before_after_state:
        try:
            after = await state["page"].evaluate(snap_expr)
        except Exception:
            after = None
    changed_fields = []
    if before and after and before.get("found") and after.get("found"):
        for f in fields:
            if before.get(f) != after.get(f):
                changed_fields.append({"field": f, "before": before.get(f), "after": after.get(f)})
    if not clicked:
        return _err(click_err or "click failed", error_name="CLICK_FAILED",
                    page_id=page_id, selector=selector,
                    before_state=before, after_state=after)
    return _ok({
        "page_id": page_id,
        "selector": selector,
        "clicked": True,
        "before_state": before,
        "after_state": after,
        "changed_fields": changed_fields,
        "any_change": bool(changed_fields),
        "alias_for": "click",
    })


async def read_skill(name: str = "", max_lines: int = 400,
                      verbosity: str = "full") -> str:
    """V15 R16.9 — Read a skill body by short name (no project_path needed).

    Resolves `<orchestrator>/.ToolA_Skill/skills/<name>/SKILL.md` and returns
    its content with line numbers. Eliminates CA's confusion over which
    project_path/file_path combo to use for reading skills.

    Accepts: full name like "18_runtime_dom_T4", or partial like "18" / "runtime_dom".

    V15 R22: `verbosity` ∈ {full|terse|ref} — terse drops metadata
    (path_absolute, total_lines, shown_lines, max_lines, truncated). Keeps
    result + skill_name (core data). Saves ~300 tokens/call.
    """
    if not name:
        return _err("name required (e.g. '18_runtime_dom_T4' or '18')",
                    error_name="MISSING_REQUIRED_FIELD",
                    accepted_fields=["name", "max_lines"])
    # V15 R24: well-known router aliases — CA prompts often say "SKILLS router"
    _ROUTER_ALIASES = {"skills", "skills.md", "router", "skill_router", "00", "00_skill_router"}
    if name.lower().strip() in _ROUTER_ALIASES:
        name = "00_skill_router"
    try:
        import path_runtime
        a_p = Path(path_runtime.ORCHESTRATOR_DIR)
    except Exception:
        a_p = Path(__file__).parent
    skills_dir = a_p / ".ToolA_Skill" / "skills"
    if not skills_dir.exists():
        return _err(f"skills dir not found: {skills_dir}",
                    error_name="SKILLS_DIR_MISSING")
    # Match: exact, prefix (e.g. "18"), or substring
    needle = name.lower()
    candidates = sorted(skills_dir.iterdir())
    match = None
    for folder in candidates:
        if folder.name.lower() == needle:
            match = folder
            break
    if not match:
        for folder in candidates:
            fn_l = folder.name.lower()
            if fn_l.startswith(needle) or needle in fn_l:
                match = folder
                break
    if not match:
        available = [f.name for f in candidates if (f / "SKILL.md").exists()]
        return _err(f"skill '{name}' not found",
                    error_name="SKILL_NOT_FOUND",
                    available=available,
                    hint="Pass exact folder name or short prefix (e.g. '18' for '18_runtime_dom_T4').")
    sm = match / "SKILL.md"
    if not sm.exists():
        return _err(f"SKILL.md missing in {match.name}",
                    error_name="SKILL_BODY_MISSING")
    try:
        raw = split_lines(sm.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return _err(f"read failed: {exc}", error_name="SKILL_READ_FAILED")
    total = len(raw)
    cap = max(1, min(int(max_lines or 400), 2000))
    shown = raw[:cap]
    numbered = "\n".join(f"{i+1}: {ln}" for i, ln in enumerate(shown))
    return _ok(_apply_verbosity({
        "skill_name": match.name,
        "path_absolute": str(sm.resolve()).replace("\\", "/"),
        "result": numbered,
        "total_lines": total,
        "shown_lines": len(shown),
        "truncated": total > len(shown),
        "max_lines": cap,
    }, verbosity, "skill_read"))


async def checkpoint_loop_dom_state(page_id: str, selector: str,
                                      interval_sec: float = 15.0,
                                      timeout_sec: float = 90.0,
                                      fields: list | None = None,
                                      stop_on_change: bool = True,
                                      baseline_id: str = "",
                                      baseline_fields: dict | None = None) -> str:
    """V15 R16.9 / R16.10 — Server-side polling loop on top of dom_state_diff.

    R16.10: baseline source priority:
      1. `baseline_id` (snapshot_id from prior dom_state_diff, TTL 10 min)
      2. `baseline_fields` (CA passes raw state dict — escape hatch)
      3. fallback: re-snapshot at t=0 (legacy R16.9 behavior — back-compat)

    Why this matters: CA pattern was "click → call loop" but loop's t=0 snapshot
    captured AFTER click landed → state already changed → false timeout. Now CA
    can capture baseline with dom_state_diff BEFORE click, then pass baseline_id.

    Polls every `interval_sec` until either:
      (a) any field changes vs baseline (when stop_on_change=True) → returns early
      (b) timeout_sec elapses → returns last snapshot + changed=False

    Returns: {baseline, baseline_source, final_state, changed, changed_fields, polls, elapsed_sec}.
    """
    import asyncio as _aio
    state, err = await _get_page(page_id)
    if err:
        return err
    fields = fields or ["disabled", "visible", "bg", "color", "className", "value_length"]
    sel_js = json.dumps(selector)
    fields_js = json.dumps(fields)
    expr = (
        "((sel, fields) => {"
        "  const el = document.querySelector(sel);"
        "  if (!el) return {found: false};"
        "  const cs = getComputedStyle(el);"
        "  const out = {found: true};"
        "  for (const f of fields) {"
        "    if (f === 'disabled') out.disabled = !!el.disabled;"
        "    else if (f === 'visible') out.visible = el.offsetParent !== null;"
        "    else if (f === 'bg') out.bg = cs.backgroundColor;"
        "    else if (f === 'color') out.color = cs.color;"
        "    else if (f === 'className') out.className = el.className || '';"
        "    else if (f === 'value_length') out.value_length = ('value' in el ? String(el.value || '').length : (el.textContent || '').length);"
        "  }"
        "  return out;"
        f"}})({sel_js}, {fields_js})"
    )
    interval = max(0.5, float(interval_sec))
    timeout = max(interval, float(timeout_sec))
    t0 = time.time()
    # R16.10: resolve baseline from explicit source if provided
    baseline_source = ""
    baseline = None
    if baseline_id:
        cached = _DOM_SNAPSHOTS.get(baseline_id)
        if not cached:
            return _err(f"baseline_id not found or expired: {baseline_id}",
                        error_name="BASELINE_EXPIRED",
                        ttl_seconds=_DOM_SNAPSHOT_TTL_S,
                        ca_next_action="re_snapshot_via_dom_state_diff_then_retry")
        baseline = dict(cached["fields"])
        baseline["found"] = True
        baseline_source = f"baseline_id={baseline_id}"
    elif baseline_fields is not None:
        baseline = dict(baseline_fields)
        baseline.setdefault("found", True)
        baseline_source = "baseline_fields_inline"
    else:
        baseline = await state["page"].evaluate(expr)
        baseline_source = "fresh_snapshot_at_t0"
    if not baseline.get("found"):
        return _err(f"selector not found: {selector}",
                    error_name="SELECTOR_NOT_FOUND")
    polls = 0
    last = baseline
    while True:
        elapsed = time.time() - t0
        if elapsed >= timeout:
            break
        await _aio.sleep(interval)
        polls += 1
        try:
            current = await state["page"].evaluate(expr)
        except Exception as exc:
            return _err(f"poll {polls} eval failed: {exc}",
                        error_name="POLL_EVAL_FAILED",
                        polls=polls, elapsed_sec=time.time() - t0,
                        baseline=baseline)
        last = current
        diff = []
        for f in fields:
            if baseline.get(f) != current.get(f):
                diff.append({"field": f, "before": baseline.get(f), "after": current.get(f)})
        if diff and stop_on_change:
            return _ok({
                "page_id": page_id, "selector": selector,
                "baseline": baseline, "baseline_source": baseline_source,
                "final_state": current,
                "changed": True, "changed_fields": diff,
                "polls": polls, "elapsed_sec": round(time.time() - t0, 2),
                "stop_reason": "field_change_detected",
            })
    return _ok({
        "page_id": page_id, "selector": selector,
        "baseline": baseline, "baseline_source": baseline_source,
        "final_state": last,
        "changed": False, "changed_fields": [],
        "polls": polls, "elapsed_sec": round(time.time() - t0, 2),
        "stop_reason": "timeout",
    })


async def ensure_single_electron_instance(project_path: str = "",
                                            cdp_port: int = 9223,
                                            kill_existing: bool = True) -> str:
    """V15 R16.9 — Pre-launch hygiene: kill any existing electron.exe (and free
    CDP port) BEFORE launching a fresh instance. Prevents 'multi-window confusion'
    where stale Rawcut from previous round is still running.

    Returns: {existing_pids, killed, port_was_busy, port_free_after}.

    Pair with launch_electron_with_cdp:
      ensure_single_electron_instance(project_path=...) → launch_electron_with_cdp(...)
    """
    import socket as _socket
    import subprocess as _sp
    result: dict = {
        "ok": True,
        "existing_pids": [],
        "killed": False,
        "port_was_busy": False,
        "port_free_after": False,
    }
    # 1. Find existing electron.exe PIDs
    try:
        r = _sp.run(["tasklist", "/FI", "IMAGENAME eq electron.exe", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=8)
        for line in (r.stdout or "").splitlines():
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 2 and parts[0].lower() == "electron.exe":
                try:
                    result["existing_pids"].append(int(parts[1]))
                except ValueError:
                    pass
    except Exception as exc:
        result["tasklist_error"] = str(exc)
    # 2. Probe CDP port
    def _port_busy(port: int) -> bool:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            return s.connect_ex(("127.0.0.1", int(port))) == 0
        except Exception:
            return False
        finally:
            s.close()
    result["port_was_busy"] = _port_busy(cdp_port)
    # 3. Kill if requested AND something to kill
    if kill_existing and (result["existing_pids"] or result["port_was_busy"]):
        try:
            kr = _sp.run(["taskkill", "/F", "/IM", "electron.exe", "/T"],
                         capture_output=True, text=True, timeout=10)
            result["killed"] = kr.returncode in (0, 128)  # 128 = not found, also fine
            result["taskkill_rc"] = kr.returncode
            result["taskkill_stdout"] = (kr.stdout or "")[:200]
        except Exception as exc:
            result["taskkill_error"] = str(exc)
            result["killed"] = False
        # Wait briefly for port release
        import asyncio as _aio
        await _aio.sleep(0.8)
        result["port_free_after"] = not _port_busy(cdp_port)
    else:
        result["port_free_after"] = not result["port_was_busy"]
    result["project_path"] = project_path
    result["cdp_port"] = cdp_port
    result["next_step_hint"] = "Call launch_electron_with_cdp(project_path, cdp_port) now."
    return _ok(result)


async def save_log_snapshot(page_id: str, section_title: str = "",
                              project_path: str = "",
                              round_id: str = "",
                              area_class: str = "log-area",
                              body_selector: str = "",
                              pick_strategy: str = "smart",
                              selector: str = "",
                              artifact_label: str = "") -> str:
    """V15 R17 — Save log content to a project artifact file. Returns path + checksum
    + preview INSTEAD of the full content — enabling final-report token savings of
    40-50% per round when User accepts artifact reference instead of full paste.

    Two input modes:
      (a) `section_title` (+optional area_class) — uses extract_log_section logic
      (b) `selector` — direct read of element.value/textContent (e.g. '#systemLogArea')

    Output location: <project>/.orchestrator_cache/artifacts/<round_id_or_ts>/<slug>.txt
    where slug = artifact_label OR section_title OR selector (sanitized).

    Returns: {ok, path, path_absolute, length, lines, sha256, head_200, tail_200,
              round_id, artifact_dir, project_path}

    Token math: 8 KB log → ~1500 tokens. Artifact ref = path + checksum + preview = ~150 tokens.
    Net saving per round: ~1350 tokens (~90% reduction on log token sink).
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    if not section_title and not selector:
        return _err("Provide section_title OR selector",
                    error_name="MISSING_REQUIRED_FIELD",
                    accepted_fields=["page_id", "section_title", "selector",
                                      "project_path", "round_id", "area_class",
                                      "body_selector", "pick_strategy", "artifact_label"])
    # 1. Extract content
    content = ""
    extract_meta: dict = {}
    if selector:
        sel_js = json.dumps(selector)
        expr = (
            "(() => {"
            f"  const el = document.querySelector({sel_js});"
            "  if (!el) return {found: false};"
            "  return {found: true, value: ('value' in el ? String(el.value || '') : (el.textContent || ''))};"
            "})()"
        )
        try:
            data = await state["page"].evaluate(expr)
        except Exception as exc:
            return _err(f"selector eval failed: {exc}",
                        error_name="DOM_EVAL_FAILED", selector=selector)
        if not data or not data.get("found"):
            return _err(f"selector not found: {selector}",
                        error_name="SELECTOR_NOT_FOUND", selector=selector)
        content = data.get("value", "")
        extract_meta = {"mode": "selector", "selector": selector}
    else:
        # Reuse extract_log_section logic via direct call
        raw = await extract_log_section(
            page_id=page_id,
            section_title=section_title,
            area_class=area_class,
            body_selector=body_selector,
            pick_strategy=pick_strategy,
            mode="full",
        )
        try:
            ext = json.loads(raw)
        except Exception:
            return _err("extract_log_section returned malformed response",
                        error_name="EXTRACT_FAILED", raw=raw[:300])
        if not ext.get("ok"):
            return raw  # propagate extract error verbatim
        content = ext.get("content", "")
        extract_meta = {
            "mode": "section_title",
            "section_title": section_title,
            "area_class": area_class,
            "body_selector_used": ext.get("body_selector_used"),
            "pick_reason": ext.get("pick_reason"),
        }
    # 2. Resolve artifact directory
    if not project_path:
        return _err("project_path required for artifact write",
                    error_name="MISSING_REQUIRED_FIELD",
                    hint="Pass project_path of the project that owns this artifact.")
    root = Path(project_path).resolve()
    if not root.exists():
        return _err(f"project_path not found: {project_path}",
                    error_name="PROJECT_NOT_FOUND")
    rid = round_id or f"r-{int(time.time() * 1000)}"
    artifact_dir = root / ".orchestrator_cache" / "artifacts" / rid
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return _err(f"failed to create artifact dir: {exc}",
                    error_name="ARTIFACT_DIR_CREATE_FAILED",
                    artifact_dir=str(artifact_dir))
    # 3. Build filename (sanitize)
    import re as _re_slug
    raw_label = artifact_label or section_title or (selector or "log")
    slug = _re_slug.sub(r"[^A-Za-z0-9_.-]+", "_", raw_label).strip("_") or "log"
    slug = slug[:60]
    file_path = artifact_dir / f"{slug}.txt"
    # 4. Write atomically
    try:
        tmp = file_path.with_suffix(".txt.tmp")
        tmp.write_text(content, encoding="utf-8", errors="replace")
        tmp.replace(file_path)
    except Exception as exc:
        return _err(f"failed to write artifact: {exc}",
                    error_name="ARTIFACT_WRITE_FAILED",
                    file_path=str(file_path))
    # 5. Compute checksum + preview
    import hashlib as _hl
    sha = _hl.sha256(content.encode("utf-8", errors="replace")).hexdigest()
    length = len(content)
    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    head_200 = content[:200]
    tail_200 = content[-200:] if length > 200 else ""
    rel_path = str(file_path.relative_to(root)).replace("\\", "/")
    return _ok({
        "round_id": rid,
        "path": rel_path,
        "path_absolute": str(file_path.resolve()).replace("\\", "/"),
        "project_path": str(root).replace("\\", "/"),
        "artifact_dir": str(artifact_dir.relative_to(root)).replace("\\", "/"),
        "length": length,
        "lines": line_count,
        "sha256": sha,
        "sha256_prefix": sha[:16],
        "head_200": head_200,
        "tail_200": tail_200,
        "extract_meta": extract_meta,
        "usage_hint": "Cite {path, sha256_prefix, length, lines} in final report instead of pasting full content. Re-fetch from path if User asks for full content.",
        "audit_note": "Artifact written under .orchestrator_cache/artifacts/. Excluded from project tree by convention; safe to delete.",
    })


async def click_and_checkpoint_loop(page_id: str,
                                      click_selector_str: str,
                                      watch_selector: str = "",
                                      interval_sec: float = 15.0,
                                      timeout_sec: float = 90.0,
                                      fields: list | None = None,
                                      stop_on_change: bool = True,
                                      pre_click_settle_ms: int = 200) -> str:
    """V15 R16.10 — Atomic click + checkpoint loop. Snapshots `watch_selector`
    BEFORE click (correct baseline timing), clicks `click_selector_str`, then
    polls `watch_selector` until state changes OR timeout.

    Why this exists: CA pattern of (dom_state_diff → click → checkpoint_loop)
    has 3 round-trips and a baseline timing pitfall — baseline can be captured
    BEFORE settle, OR the loop's internal t=0 baseline captures AFTER click
    landed. This tool guarantees the right ordering server-side.

    `watch_selector` defaults to `click_selector_str` (watch the clicked element itself).
    """
    import asyncio as _aio
    state, err = await _get_page(page_id)
    if err:
        return err
    fields = fields or ["disabled", "visible", "bg", "color", "className", "value_length"]
    watch_sel = watch_selector or click_selector_str
    watch_js = json.dumps(watch_sel)
    fields_js = json.dumps(fields)
    expr = (
        "((sel, fields) => {"
        "  const el = document.querySelector(sel);"
        "  if (!el) return {found: false};"
        "  const cs = getComputedStyle(el);"
        "  const out = {found: true};"
        "  for (const f of fields) {"
        "    if (f === 'disabled') out.disabled = !!el.disabled;"
        "    else if (f === 'visible') out.visible = el.offsetParent !== null;"
        "    else if (f === 'bg') out.bg = cs.backgroundColor;"
        "    else if (f === 'color') out.color = cs.color;"
        "    else if (f === 'className') out.className = el.className || '';"
        "    else if (f === 'value_length') out.value_length = ('value' in el ? String(el.value || '').length : (el.textContent || '').length);"
        "  }"
        "  return out;"
        f"}})({watch_js}, {fields_js})"
    )
    # 1. Capture baseline BEFORE click
    try:
        baseline = await state["page"].evaluate(expr)
    except Exception as exc:
        return _err(f"baseline snapshot failed: {exc}",
                    error_name="DOM_SNAPSHOT_FAILED")
    if not baseline.get("found"):
        return _err(f"watch_selector not found: {watch_sel}",
                    error_name="SELECTOR_NOT_FOUND",
                    watch_selector=watch_sel)
    if pre_click_settle_ms > 0:
        await _aio.sleep(max(0, pre_click_settle_ms) / 1000.0)
    # 2. Perform click on click_selector_str
    try:
        await state["page"].locator(click_selector_str).first.click(timeout=5000)
        clicked = True
        click_err = None
    except Exception as exc:
        clicked = False
        click_err = str(exc)
    if not clicked:
        return _err(click_err or "click failed",
                    error_name="CLICK_FAILED",
                    click_selector=click_selector_str,
                    baseline=baseline)
    # 3. Poll watch_selector until change or timeout
    interval = max(0.5, float(interval_sec))
    timeout = max(interval, float(timeout_sec))
    t0 = time.time()
    polls = 0
    last = baseline
    while True:
        elapsed = time.time() - t0
        if elapsed >= timeout:
            break
        await _aio.sleep(interval)
        polls += 1
        try:
            current = await state["page"].evaluate(expr)
        except Exception as exc:
            return _err(f"poll {polls} eval failed: {exc}",
                        error_name="POLL_EVAL_FAILED",
                        polls=polls, elapsed_sec=time.time() - t0,
                        baseline=baseline)
        last = current
        diff = []
        for f in fields:
            if baseline.get(f) != current.get(f):
                diff.append({"field": f, "before": baseline.get(f), "after": current.get(f)})
        if diff and stop_on_change:
            return _ok({
                "page_id": page_id,
                "click_selector": click_selector_str,
                "watch_selector": watch_sel,
                "clicked": True,
                "baseline": baseline,
                "baseline_source": "captured_pre_click",
                "final_state": current,
                "changed": True,
                "changed_fields": diff,
                "polls": polls,
                "elapsed_sec": round(time.time() - t0, 2),
                "stop_reason": "field_change_detected",
            })
    return _ok({
        "page_id": page_id,
        "click_selector": click_selector_str,
        "watch_selector": watch_sel,
        "clicked": True,
        "baseline": baseline,
        "baseline_source": "captured_pre_click",
        "final_state": last,
        "changed": False,
        "changed_fields": [],
        "polls": polls,
        "elapsed_sec": round(time.time() - t0, 2),
        "stop_reason": "timeout",
    })


async def ui_state_summary(page_id: str, regions: list | None = None) -> str:
    """V15 R16.10 — Structured text-only state summary per UI region.

    NOT a screenshot — returns visibility/state/text_preview/classes per region.
    Pure text output keeps token cost low (vs base64 image: 99% saving).

    Each region = {label, selector}. Returns:
      summary: {label: {visible, text_preview, value_preview, classes, value_length,
                        has_error_class, disabled, bg, rect}}

    Default regions (if empty): all [data-region] OR top-level <section> with id.
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    regs = regions or []
    regs_js = json.dumps(regs)
    expr = (
        "((regs) => {"
        "  let useDefault = !regs || regs.length === 0;"
        "  let workRegions = regs;"
        "  if (useDefault) {"
        "    const candidates = Array.from(document.querySelectorAll('[data-region], section[id]'));"
        "    workRegions = candidates.slice(0, 30).map(el => ({"
        "      label: el.getAttribute('data-region') || el.id || el.tagName.toLowerCase(),"
        "      selector: '#' + (el.id || ''),"
        "    })).filter(r => r.selector && r.selector !== '#');"
        "  }"
        "  const summary = {};"
        "  const missing = [];"
        "  for (const r of workRegions) {"
        "    const el = document.querySelector(r.selector);"
        "    if (!el) { missing.push(r.label || r.selector); continue; }"
        "    const cs = getComputedStyle(el);"
        "    const rect = el.getBoundingClientRect();"
        "    const txt = (el.textContent || '').trim();"
        "    const v = ('value' in el) ? String(el.value || '') : '';"
        "    const classes = Array.from(el.classList || []);"
        "    summary[r.label] = {"
        "      selector: r.selector,"
        "      visible: el.offsetParent !== null,"
        "      disabled: !!el.disabled,"
        "      text_length: txt.length,"
        "      text_preview: txt.slice(0, 200),"
        "      value_length: v.length,"
        "      value_preview: v.slice(0, 200),"
        "      classes: classes.slice(0, 8),"
        "      has_error_class: classes.some(c => /error|fail|invalid|danger/i.test(c)),"
        "      bg: cs.backgroundColor,"
        "      rect: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)},"
        "    };"
        "  }"
        "  return {summary, total_regions: Object.keys(summary).length, missing_selectors: missing, used_default: useDefault};"
        f"}})({regs_js})"
    )
    try:
        data = await state["page"].evaluate(expr)
    except Exception as exc:
        # V15 R24: detect stale CDP target → actionable hint
        msg = str(exc).lower()
        stale = ("target page" in msg or "target closed" in msg
                 or "session closed" in msg or "browser has been closed" in msg
                 or "connection closed" in msg)
        return _err(f"ui_state_summary eval failed: {exc}",
                    error_name="DOM_EVAL_FAILED",
                    stale_page_id=stale,
                    page_id=page_id,
                    ca_next_action=("list_cdp_targets_then_reattach" if stale
                                     else "verify_selectors_or_page_id"))
    out = data or {}
    out["page_id"] = page_id
    out["regions_input"] = regs
    return _ok(out)


# V15 R18 Phase B — mission curation tokens. Module-level stopwords for keyword scoring.
_MISSION_STOPWORDS = {
    "a", "an", "and", "or", "the", "to", "of", "in", "on", "at", "for", "with",
    "by", "from", "as", "is", "are", "be", "was", "were", "this", "that", "it",
    "i", "we", "you", "they", "do", "does", "did", "have", "has", "had", "will",
    "would", "should", "can", "could", "may", "might", "test", "use", "via",
    "khi", "thì", "là", "có", "của", "và", "hoặc", "với", "trong", "đã", "không",
    "cần", "phải", "cho", "bằng", "thay", "ra", "đến", "từ", "theo", "như",
}


def _tokenize_for_mission(text: str) -> list:
    """Lowercase + split non-alpha-numeric, drop stopwords + short tokens."""
    if not text:
        return []
    norm = re.sub(r"[^A-Za-z0-9_]+", " ", text.lower())
    tokens = [t for t in norm.split() if len(t) >= 2 and t not in _MISSION_STOPWORDS]
    return tokens


async def declare_mission(mission: str = "",
                            context_hints: list | None = None,
                            project_path: str = "",
                            top_skills: int = 5,
                            top_tools: int = 20,
                            verbosity: str = "full") -> str:
    """V15 R18 Phase B — Mission curation. CA declares the task at session start;
    [A] returns curated skill + tool subset so CA reads ~3-5 skills instead of all 23.

    Inputs:
      mission: free-form task description ("test Electron Rawcut UI ...")
      context_hints: list of keyword anchors ["electron", "ui", "log"]
      project_path: active session project root (state pinned to session)
      top_skills / top_tools: cap response size

    Returns: relevant_skills + relevant_tools (ranked by keyword overlap) +
      irrelevant_skills_skip + recommended_sequence + mission_id.

    State stored in _SESSION_MISSION[session_id] until close_session.
    """
    if not mission and not (context_hints or []):
        return _err("Provide mission text OR context_hints",
                    error_name="MISSING_REQUIRED_FIELD",
                    accepted_fields=["mission", "context_hints", "project_path",
                                      "top_skills", "top_tools"])
    # Build query tokens
    hints = context_hints or []
    raw_text = f"{mission} {' '.join(hints)}".strip()
    query_tokens = set(_tokenize_for_mission(raw_text))
    # V15 R28: phrase-first matching for Ktr/tool-map missions.
    # Tokenizer splits "tool map" → ["tool", "map"] which loses phrase intent.
    # Phrase pre-match boosts skill 21 + 00 + audit tools directly.
    _KTR_AUDIT_PHRASES = (
        "tool map", "tool matrix", "tool inventory", "tool coverage",
        "architecture audit", "audit drift", "schema hint", "alias drift",
        "skill coverage", "4 ktr", "4 lớp", "ktrb", "ktrsk", "ktrsh", "ktrin",
        "đồng bộ tool", "đồng bộ skill", "rà tài liệu", "kiểm tra sync",
        "kiểm toàn", "kiểm tra toàn", "kiểm tool", "test tool",
        "test toàn bộ tool", "kiểm toàn bộ tool",
    )
    mission_lower = (raw_text or "").lower()
    ktr_audit_boost = any(p in mission_lower for p in _KTR_AUDIT_PHRASES)
    if ktr_audit_boost:
        # Force-include the audit tokens even if stopword strip removed them
        query_tokens |= {"matrix", "audit", "tool", "skill", "ktr", "schema"}
    if not query_tokens:
        return _err("Mission text yields no usable tokens after stopword strip",
                    error_name="MISSION_TOO_SHORT",
                    raw_text=raw_text,
                    hint="Pass more specific mission text or context_hints")
    # Resolve orchestrator root for skills
    try:
        import path_runtime
        a_p = Path(path_runtime.ORCHESTRATOR_DIR)
    except Exception:
        a_p = Path(__file__).parent
    skills_dir = a_p / ".ToolA_Skill" / "skills"
    # Score skills
    skill_scores = []
    if skills_dir.exists():
        for folder in sorted(skills_dir.iterdir()):
            sm = folder / "SKILL.md"
            if not sm.exists():
                continue
            try:
                head = sm.read_text(encoding="utf-8", errors="replace")[:2000]
            except Exception:
                continue
            doc_tokens = _tokenize_for_mission(folder.name + " " + head)
            doc_set = set(doc_tokens)
            if not doc_set:
                continue
            overlap = query_tokens & doc_set
            if not overlap:
                continue
            # Normalize: overlap / sqrt(|doc| * |query|) — cosine-ish
            denom = max(1, (len(doc_set) * len(query_tokens)) ** 0.5)
            score = len(overlap) / denom
            skill_scores.append({
                "name": folder.name,
                "path": str(sm.relative_to(a_p)).replace("\\", "/"),
                "score": round(score, 3),
                "matched_tokens": sorted(overlap)[:10],
            })
    # V15 R28: force-boost audit-relevant skills when phrase match detected
    if ktr_audit_boost:
        for s in skill_scores:
            if s["name"] in ("21_tool_matrix", "00_skill_router", "07_error_schema_hints"):
                s["score"] = max(s.get("score", 0), 0.80)
                s.setdefault("matched_tokens", []).append("[R28_phrase_boost]")
        # Inject if not in skill_scores list (low-keyword overlap skills)
        existing_names = {s["name"] for s in skill_scores}
        for forced_name in ("21_tool_matrix", "00_skill_router"):
            if forced_name not in existing_names:
                # Try locate folder
                folder = skills_dir / forced_name if skills_dir.exists() else None
                if folder and (folder / "SKILL.md").exists():
                    skill_scores.append({
                        "name": forced_name,
                        "path": str((folder / "SKILL.md").relative_to(a_p)).replace("\\", "/"),
                        "score": 0.85,
                        "matched_tokens": ["[R28_phrase_force_inject]"],
                    })
    skill_scores.sort(key=lambda x: x["score"], reverse=True)
    relevant_skills = [s for s in skill_scores if s["score"] >= 0.15][:top_skills]
    relevant_skill_names = {s["name"] for s in relevant_skills}
    all_skill_names = {f.name for f in skills_dir.iterdir() if (f / "SKILL.md").exists()} if skills_dir.exists() else set()
    irrelevant_skills_skip = sorted(all_skill_names - relevant_skill_names)
    # Score tools — use code_tools.TOOL_SCHEMAS descriptions
    tool_scores = []
    try:
        import code_tools as _ct
        for schema in getattr(_ct, "TOOL_SCHEMAS", []):
            fn = schema.get("function", {})
            tname = fn.get("name", "")
            if not tname or tname not in _ct.TOOL_MAP:
                continue
            tdesc = fn.get("description", "") or ""
            tier = _ct.TOOL_PHASES.get(tname, "T?")
            tok = set(_tokenize_for_mission(tname + " " + tdesc))
            overlap = query_tokens & tok
            if not overlap:
                continue
            denom = max(1, (len(tok) * len(query_tokens)) ** 0.5)
            score = len(overlap) / denom
            tool_scores.append({
                "name": tname,
                "tier": tier,
                "score": round(score, 3),
                "matched_tokens": sorted(overlap)[:8],
            })
    except Exception:
        pass
    # V15 R28: force-boost audit tools when phrase match detected
    if ktr_audit_boost:
        _AUDIT_TOOL_NAMES = ("audit_tool_matrix", "architecture_audit_compact",
                              "progressive_matrix_check", "alias_contract_test")
        existing_tool_names = {t["name"] for t in tool_scores}
        for t in tool_scores:
            if t["name"] in _AUDIT_TOOL_NAMES:
                t["score"] = max(t.get("score", 0), 0.80)
                t.setdefault("matched_tokens", []).append("[R28_phrase_boost]")
        for forced_tool in _AUDIT_TOOL_NAMES:
            if forced_tool not in existing_tool_names:
                try:
                    tier = _ct.TOOL_PHASES.get(forced_tool, "T?")
                    tool_scores.append({
                        "name": forced_tool,
                        "tier": tier,
                        "score": 0.85,
                        "matched_tokens": ["[R28_phrase_force_inject]"],
                    })
                except Exception:
                    pass
    tool_scores.sort(key=lambda x: x["score"], reverse=True)
    relevant_tools = [t for t in tool_scores if t["score"] >= 0.1][:top_tools]
    # Estimate tokens saved by skipping full read of irrelevant skills
    # ~ each skill ~3KB ~ ~1000 tokens; skipping 18 of 23 = ~18000 tokens
    saved_skill_count = len(irrelevant_skills_skip)
    tokens_saved_estimate = saved_skill_count * 1000  # rough
    # Generate mission_id + pin to active session
    mission_id = f"m-{int(time.time() * 1000)}"
    sid = ""
    if project_path:
        try:
            from code_tools import _get_active_session_id, _session_project_key
            norm = _session_project_key(project_path)
            # Anh 2026-08-11 (Phase A on-loop audit): _get_active_session_id is a
            # SQLite read; run it OFF the loop.
            sid = (await asyncio.to_thread(_get_active_session_id, norm)) or ""
        except Exception:
            pass
    if sid:
        _SESSION_MISSION[sid] = {
            "mission_id": mission_id,
            "mission": mission,
            "context_hints": hints,
            "relevant_skills": [s["name"] for s in relevant_skills],
            "relevant_tools": [t["name"] for t in relevant_tools],
            "declared_at": datetime.now().isoformat(timespec="seconds"),
        }
    # Build a generic recommended sequence based on relevant tools (heuristic)
    seq = []
    tool_set = {t["name"] for t in relevant_tools}
    sequence_template = [
        ("read_skill", "read_skill(name=<top relevant skill>)"),
        ("ensure_single_electron_instance", "ensure_single_electron_instance(project_path, cdp_port=9223)"),
        ("inspect_project_runtime", "inspect_project_runtime(project_path)"),
        ("launch_electron_with_cdp", "launch_electron_with_cdp(project_path, cdp_port=9223)"),
        ("query_dom_candidates", "query_dom_candidates(page_id, filter='all')"),
        ("describe_page", "describe_page(page_id)"),
        ("fill_value", "fill_value(page_id, selector, value, encoding='base64')"),
        ("click_and_checkpoint_loop", "click_and_checkpoint_loop(page_id, click_selector_str, watch_selector)"),
        ("save_log_snapshot", "save_log_snapshot(page_id, section_title, project_path, round_id)"),
    ]
    for tname, hint in sequence_template:
        if tname in tool_set:
            seq.append(hint)
    return _ok(_apply_verbosity({
        "mission_id": mission_id,
        "mission": mission,
        "context_hints": hints,
        "session_id": sid,
        "relevant_skills": relevant_skills,
        "relevant_tools": relevant_tools,
        "irrelevant_skills_skip": irrelevant_skills_skip,
        "recommended_sequence": seq,
        "tokens_saved_estimate": tokens_saved_estimate,
        "query_tokens_used": sorted(query_tokens),
        "scoring_method": "cosine_keyword_overlap_v1",
        "next_step_hint": "Read only relevant_skills via read_skill(name=...). Call recommended_sequence tools in order. State persists until close_session.",
        "caveats": [
            "Heuristic keyword scoring — not semantic. Pass more specific mission text + hints for better accuracy.",
            "score_floor: skills<0.15 + tools<0.10 omitted. Bump top_skills/top_tools if you need wider net.",
            "State reset on close_session.",
        ],
    }, verbosity, "mission"))


async def alias_contract_test(tool_filter: str = "",
                                 include_http_smoke: bool = False,
                                 http_sample: list | None = None) -> str:
    """V15 R20/R21 — Regression smoke for the alias contract.

    Default: calls orchestrator_server._normalize_body() in-process for every
    registered alias and harmless field. PASS = legacy disappears, canonical
    present with value preserved. Fast, deterministic.

    R21: when `include_http_smoke=True`, ALSO runs HTTP-level smoke for a
    sample of historically-failing cases (or `http_sample` overrides the list).
    Verifies end-to-end /api/call/{tool} accepts legacy fields. Uses thread
    pool so it doesn't block the asyncio loop.
    """
    try:
        import sys as _sys
        srv = _sys.modules.get("orchestrator_server") or _sys.modules.get("__main__")
        if not srv or not hasattr(srv, "_ARG_ALIASES"):
            return _err("Cannot access _ARG_ALIASES registry",
                        error_name="REGISTRY_UNAVAILABLE",
                        probed=["orchestrator_server", "__main__"])
        arg_aliases = getattr(srv, "_ARG_ALIASES", {}) or {}
        harmless = getattr(srv, "_HARMLESS_IGNORE_FIELDS", {}) or {}
        _normalize = getattr(srv, "_normalize_body", None)
        if _normalize is None:
            return _err("_normalize_body unavailable",
                        error_name="NORMALIZER_UNAVAILABLE")
    except Exception as exc:
        return _err(f"setup error: {exc}", error_name="SETUP_FAILED")
    details = []
    passed = failed = 0
    # Alias probes
    for tname, alias_map in sorted(arg_aliases.items()):
        if tool_filter and tool_filter not in tname:
            continue
        for legacy, canonical in sorted(alias_map.items()):
            # Build probe body with ONLY the legacy field
            probe = {legacy: "probe_value"}
            try:
                normalized, applied = _normalize(tname, dict(probe))
            except Exception as exc:
                details.append({
                    "tool": tname, "legacy": legacy, "canonical": canonical,
                    "status": "NORMALIZER_ERROR", "error": str(exc),
                })
                failed += 1
                continue
            # PASS criteria: legacy gone AND canonical present with the value
            legacy_gone = legacy not in normalized
            canonical_present = canonical in normalized
            value_preserved = normalized.get(canonical) == "probe_value"
            if legacy_gone and canonical_present and value_preserved:
                details.append({
                    "tool": tname, "legacy": legacy, "canonical": canonical,
                    "status": "PASS",
                    "applied": applied,
                })
                passed += 1
            else:
                details.append({
                    "tool": tname, "legacy": legacy, "canonical": canonical,
                    "status": "FAIL",
                    "legacy_gone": legacy_gone,
                    "canonical_present": canonical_present,
                    "value_preserved": value_preserved,
                    "normalized": normalized,
                    "applied": applied,
                })
                failed += 1
    # Harmless probes
    harmless_details = []
    h_pass = h_fail = 0
    for tname, fields in sorted(harmless.items()):
        if tool_filter and tool_filter not in tname:
            continue
        for f in sorted(fields):
            probe = {f: "probe_value"}
            try:
                normalized, applied = _normalize(tname, dict(probe))
            except Exception as exc:
                harmless_details.append({"tool": tname, "field": f,
                                          "status": "NORMALIZER_ERROR", "error": str(exc)})
                h_fail += 1
                continue
            if f not in normalized:
                harmless_details.append({"tool": tname, "field": f,
                                          "status": "PASS", "applied": applied})
                h_pass += 1
            else:
                harmless_details.append({"tool": tname, "field": f,
                                          "status": "FAIL_field_still_present",
                                          "normalized": normalized})
                h_fail += 1
    # R21: optional HTTP-level smoke for historically failing cases
    http_results = []
    http_pass = http_fail = 0
    if include_http_smoke:
        # Sample of cases that previously failed live (audit Round 8 + Round 9)
        default_sample = [
            {"tool": "tool_decision_hint", "payload": {"task_description": "click button", "project_path": "/tmp"}},
            {"tool": "optional_tool_probe", "payload": {"tool_names": ["x"]}},
            {"tool": "optional_tool_probe", "payload": {"tools": [], "project_path": "/tmp"}},
            {"tool": "read_skill", "payload": {"skill_name": "00", "project_path": "/tmp"}},
            {"tool": "read_skill", "payload": {"name": "00", "max_chars": 100}},
            {"tool": "desktop_window_list", "payload": {"title_contains": "NoMatch_xyz"}},
            {"tool": "desktop_window_list", "payload": {"title_filter": "NoMatch_xyz", "project_path": "/tmp"}},
        ]
        sample = http_sample or default_sample
        base_url = os.environ.get("ORCHESTRATOR_BASE_URL", "http://localhost:8083")

        def _http_probe(tname, payload):
            import urllib.error as _ue
            import urllib.request as _ur
            try:
                req = _ur.Request(
                    f"{base_url}/api/call/{tname}",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with _ur.urlopen(req, timeout=10) as resp:
                        return json.loads(resp.read().decode("utf-8", errors="replace"))
                except _ue.HTTPError as he:
                    try:
                        return json.loads(he.read().decode("utf-8", errors="replace"))
                    except Exception:
                        return {"error_name": f"HTTP_{he.code}", "raw": True}
            except Exception as exc:
                return {"error_name": "PROBE_ERROR", "error": str(exc)}

        for case in sample:
            tname = case.get("tool", "")
            payload = case.get("payload") or {}
            if tool_filter and tool_filter not in tname:
                continue
            body_resp = await asyncio.to_thread(_http_probe, tname, payload)
            err_name = body_resp.get("error_name")
            # PASS = not UNSUPPORTED_FIELD AND not WRONG_ARGUMENTS for legacy field
            # Acceptable: downstream errors like PAGE_NOT_FOUND, MISSING_REQUIRED_FIELD, ok=true
            is_unsupported_alias = (
                err_name == "UNSUPPORTED_FIELD"
                and any(
                    f in (body_resp.get("unsupported_fields") or [])
                    for f in payload.keys()
                )
            )
            status = "PASS" if not is_unsupported_alias else "FAIL"
            if status == "PASS":
                http_pass += 1
            else:
                http_fail += 1
            http_results.append({
                "tool": tname,
                "payload_keys": sorted(payload.keys()),
                "status": status,
                "response_error_name": err_name,
                "unsupported_fields": body_resp.get("unsupported_fields") or [],
            })
    intact_inprocess = (failed == 0 and h_fail == 0)
    intact_http = (http_fail == 0) if include_http_smoke else None
    return _ok({
        "alias_tests": {"tested": passed + failed, "passed": passed, "failed": failed,
                         "details": details[:30],
                         "details_truncated": len(details) > 30},
        "harmless_tests": {"tested": h_pass + h_fail, "passed": h_pass, "failed": h_fail,
                            "details": harmless_details},
        "http_smoke": {
            "ran": include_http_smoke,
            "tested": http_pass + http_fail,
            "passed": http_pass,
            "failed": http_fail,
            "results": http_results,
        } if include_http_smoke else {"ran": False, "hint": "Pass include_http_smoke=true to add live HTTP probes for historically failing alias cases."},
        "summary": {
            "total_tested": passed + failed + h_pass + h_fail + (http_pass + http_fail if include_http_smoke else 0),
            "total_passed": passed + h_pass + (http_pass if include_http_smoke else 0),
            "total_failed": failed + h_fail + (http_fail if include_http_smoke else 0),
            "alias_contract_intact": intact_inprocess and (intact_http is not False),
            "intact_inprocess": intact_inprocess,
            "intact_http": intact_http,
        },
        "test_mode": "in_process_normalize" + ("+http_smoke" if include_http_smoke else ""),
        "ca_next_action": ("no_action_needed" if (failed == 0 and h_fail == 0 and (http_fail == 0))
                            else "review_failed_aliases"),
    })


async def architecture_audit_compact(project_path: str = "") -> str:
    """V15 R20 — One-shot 4-architecture (KtrB/KtrSK/KtrSH/KtrI) audit in compact form.

    Combines: matrix audit + alias drift + progressive gaps + inventory classification.
    Returns ~30-line digest instead of full audit_tool_matrix dump. Token cost ~500
    vs ~5000 for raw matrix.
    """
    try:
        from code_tools import _audit_tool_matrix_sync
        raw = _audit_tool_matrix_sync(project_path)
    except Exception as exc:
        return _err(f"matrix audit failed: {exc}", error_name="MATRIX_AUDIT_FAILED")
    s = raw.get("summary", {}) or {}
    alias_drift = raw.get("alias_drift", []) or []
    prog = raw.get("progressive_gaps", []) or []
    # KtrB: backend code/tool count + drift presence
    KtrB = {
        "status": "ok" if s.get("drift_count", 0) == 0 else "drift_present",
        "code_tools": s.get("code_tools"),
        "schema_tools": s.get("schema_tools"),
        "drift_count": s.get("drift_count", 0),
        "note": "Backend tool registry intact" if s.get("drift_count") == 0 else "Tool presence drift detected — review drift[]",
    }
    # KtrSK: skill alias hygiene
    # R21: aggregate top offending files for drill-down
    _file_counts = {}
    for d in alias_drift:
        _file_counts[d.get("file", "?")] = _file_counts.get(d.get("file", "?"), 0) + 1
    top_files = sorted(_file_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    KtrSK = {
        "status": ("ok" if not alias_drift else
                   "medium" if s.get("alias_drift_severity") == "medium" else "low"),
        "skill_folders": s.get("skill_folders"),
        "skill_tools": s.get("skill_tools"),
        "alias_drift_count": s.get("alias_drift_count", 0),
        "alias_drift_severity": s.get("alias_drift_severity"),
        "top_offending_files": [{"file": f, "count": c} for f, c in top_files],
        "note": ("All skill docs aligned" if not alias_drift else
                  f"{s.get('alias_drift_count')} legacy field/term mentions — drill into top_offending_files[]"),
    }
    # KtrSH: schema hint health — proxy via alias contract availability
    try:
        contract_raw = await alias_contract_test(tool_filter="")
        contract = json.loads(contract_raw)
        contract_intact = contract.get("summary", {}).get("alias_contract_intact", False)
        contract_pass = contract.get("summary", {}).get("total_passed", 0)
        contract_fail = contract.get("summary", {}).get("total_failed", 0)
    except Exception:
        contract_intact = None
        contract_pass = contract_fail = -1
    KtrSH = {
        "status": "ok" if contract_intact else ("fail" if contract_intact is False else "unknown"),
        "alias_contract_intact": contract_intact,
        "alias_contract_pass": contract_pass,
        "alias_contract_fail": contract_fail,
        "note": "Schema hint layer verified via alias_contract_test" if contract_intact else
                 "Schema hint layer has failed alias normalization — see alias_contract_test",
    }
    # KtrI: inventory hygiene + progressive gaps + R21 finer extras classification
    KtrI = {
        "status": "ok" if s.get("progressive_gap_count", 0) == 0 else "gaps_present",
        "inventory_tools": s.get("inventory_tools"),
        "inventory_active": s.get("inventory_active_count"),
        "inventory_retired": s.get("inventory_retired_count"),
        "inventory_extra": s.get("inventory_extra_count"),
        # R21: extras finer breakdown
        "inventory_extra_breakdown": {
            "endpoints": s.get("inventory_extra_endpoints_count", 0),
            "internal": s.get("inventory_extra_internal_count", 0),
            "planned_stubs": s.get("inventory_extra_planned_count", 0),
            "doc_only": s.get("inventory_extra_doc_only_count", 0),
            "unknown": s.get("inventory_extra_unknown_count", 0),
        },
        "progressive_gap_count": s.get("progressive_gap_count", 0),
        "progressive_gaps_detail": prog[:10],
        "note": "Inventory + progressive matrix clean" if not prog else
                 f"{len(prog)} UI progressive gates missing — review progressive_gaps_detail[]",
    }
    overall_status = (
        "ok" if all(k["status"] == "ok" for k in (KtrB, KtrSK, KtrSH, KtrI)) else
        "review_needed"
    )
    return _ok({
        "project_path": project_path,
        "generated_at": raw.get("generated_at"),
        "overall_status": overall_status,
        "KtrB_backend": KtrB,
        "KtrSK_skills": KtrSK,
        "KtrSH_schema_hint": KtrSH,
        "KtrI_inventory": KtrI,
        "ca_next_action": raw.get("ca_next_action"),
        "drill_down_tools": [
            "audit_tool_matrix(project_path) — full drift + alias_drift + progressive_gaps detail",
            "alias_contract_test() — full alias regression smoke",
            "progressive_matrix_check(project_path) — UI gate detail",
        ],
    })


async def mcp_surface_diff(project_path: str = "",
                           client_tool_names: list | None = None,
                           include_details: bool = False) -> str:
    """Compare backend tool surfaces and optional client-visible MCP names."""
    import json
    import urllib.request
    from pathlib import Path

    try:
        import code_tools as ct
        import path_runtime

        root = Path(project_path).resolve() if project_path else Path(path_runtime.ORCHESTRATOR_DIR)
        tool_map = set(ct.TOOL_MAP.keys())
        schema_names = {
            item.get("function", {}).get("name")
            for item in ct.TOOL_SCHEMAS
            if item.get("function", {}).get("name")
        }
        phase_resolved = {
            name
            for name in tool_map
            if getattr(ct, "tool_phase", lambda _name: "")(name)
        }

        api_tools = set()
        api_error = None
        api_source = "http_api"
        try:
            with urllib.request.urlopen("http://localhost:8083/api/tools", timeout=5) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
            seq = raw if isinstance(raw, list) else raw.get("tools", [])
            api_tools = {
                item.get("function", {}).get("name")
                for item in seq
                if isinstance(item, dict) and item.get("function", {}).get("name")
            }
        except Exception as exc:
            if "timed out" in str(exc).lower():
                # When called through /api/call/mcp_surface_diff, the local HTTP
                # server can be busy handling this request. In that in-process
                # case /api/tools is generated from TOOL_SCHEMAS in the same
                # process, so schema_names is the authoritative equivalent.
                api_tools = set(schema_names)
                api_source = "in_process_schema_equivalent"
            else:
                api_error = str(exc)

        client_tools = set(client_tool_names or [])
        compare_client = bool(client_tool_names)
        diff = {
            "tool_map_minus_schema": sorted(tool_map - schema_names),
            "schema_minus_tool_map": sorted(schema_names - tool_map),
            "tool_map_minus_api": sorted(tool_map - api_tools) if api_tools else [],
            "api_minus_tool_map": sorted(api_tools - tool_map),
        }
        if compare_client:
            diff["tool_map_minus_client"] = sorted(tool_map - client_tools)
            diff["client_minus_tool_map"] = sorted(client_tools - tool_map)

        counts = {
            "tool_map": len(tool_map),
            "schemas": len(schema_names),
            "phase_resolved": len(phase_resolved),
            "api_tools": len(api_tools),
            "client_tools": len(client_tools) if compare_client else None,
        }
        diff_counts = {k: len(v) for k, v in diff.items()}
        drift_count = sum(diff_counts.values()) + (1 if api_error else 0)
        response = {
            "ok": True,
            "project_path": str(root),
            "counts": counts,
            "diff_counts": diff_counts,
            "drift_count": drift_count,
            "api_error": api_error,
            "api_source": api_source,
            "surfaces_aligned": drift_count == 0,
            "client_comparison": compare_client,
            "metadata_diagnostics": {
                "unresolved_phase_count": len(tool_map - phase_resolved),
                "unresolved_phase": sorted(tool_map - phase_resolved)[:20],
            },
            "ca_next_action": "no_action_needed" if drift_count == 0 else "review_surface_diff",
            "mcp_host_note": (
                "Backend cannot see the agent host's loaded MCP wrapper list unless "
                "the caller passes client_tool_names from tool_names()."
            ),
        }
        response["diff"] = diff if include_details or drift_count else {
            k: v[:10] for k, v in diff.items() if v
        }
        return _ok(response)
    except Exception as exc:
        return _err(f"mcp_surface_diff failed: {exc}", error_name="MCP_SURFACE_DIFF_FAILED")


async def acceptance_dashboard(project_path: str = "",
                                  probe_path: str = "") -> str:
    """V15 R29 — Single-call compact dashboard for R28/R29 acceptance metrics.

    Aggregates 10+ metrics in one response so CA audits don't need 10 separate
    tool calls. ~500 tokens vs ~5000 for full audit_tool_matrix + probes.

    Wraps:
      - _audit_tool_matrix_sync (drift / alias_drift / progressive / inventory class)
      - alias_contract_test in-process (alias normalize regression)
      - inspect_project_usage on `probe_path` (Rawcut scan health, if provided)
      - schema scan for weak-hint count + protected metadata mismatch
      - mutation_scope consistency check
    """
    try:
        from code_tools import _audit_tool_matrix_sync
        raw_matrix = _audit_tool_matrix_sync(project_path)
    except Exception as exc:
        return _err(f"matrix audit failed: {exc}", error_name="DASHBOARD_MATRIX_FAILED")
    s = raw_matrix.get("summary", {}) or {}

    # Alias contract regression — call in-process (no HTTP)
    try:
        ac_raw = await alias_contract_test(tool_filter="", include_http_smoke=False)
        ac = json.loads(ac_raw)
        ac_summary = ac.get("summary", {}) or {}
        alias_contract_intact = bool(ac_summary.get("alias_contract_intact"))
        alias_pass = int(ac_summary.get("total_passed", 0) or 0)
        alias_fail = int(ac_summary.get("total_failed", 0) or 0)
    except Exception:
        alias_contract_intact = None
        alias_pass = alias_fail = -1

    # Schema scan: weak-hint count + mutation_scope consistency + protected mismatch
    weak_schema_count = 0
    weak_schema_examples: list = []
    mutation_scope_mismatch = []
    try:
        import code_tools as _ct
        schemas = getattr(_ct, "TOOL_SCHEMAS", []) or []
        protected_set = set(getattr(_ct, "PROTECTED_TOOL_NAMES", set()) or set())
        scope_map = getattr(_ct, "_MUTATION_SCOPE_MAP", {}) or {}
        # V15 R29: pull _PAYLOAD_EXAMPLES from orchestrator_server to count enrichment
        try:
            import sys as _sys
            _srv = _sys.modules.get("orchestrator_server") or _sys.modules.get("__main__")
            payload_examples = getattr(_srv, "_PAYLOAD_EXAMPLES", {}) or {}
        except Exception:
            payload_examples = {}
        for sch in schemas:
            fn = sch.get("function", {}) or {}
            name = fn.get("name", "")
            if not name:
                continue
            desc = fn.get("description", "") or ""
            # V15 R29: Weak = short desc AND no enrichment (payload_example absent).
            # Skip protected tools (they have token/contract gate as guidance).
            has_example = name in payload_examples
            if (len(desc.strip()) < 40
                and not has_example
                and name not in protected_set):
                weak_schema_count += 1
                if len(weak_schema_examples) < 5:
                    weak_schema_examples.append({"tool": name, "desc_len": len(desc),
                                                  "has_payload_example": has_example})
            # Protected metadata mismatch: tool in PROTECTED but description says non-mutating.
            # DELIBERATELY NOT CHECKED HERE -- it is handled via the /api/tools metadata response,
            # not the schema. A38-B RETIRED the counter this block used to feed: the report was
            # publishing `protected_mismatch_count` from len(mutation_scope_mismatch), a DIFFERENT
            # measurement, so `protected_mismatch_ok` and `mutation_scope_ok` were one boolean
            # wearing two names, and `protected_mismatch_count_max` was a target for a quantity
            # nothing ever measured. The local, the target, the actual and the delta are all gone.
            # Do not re-add a counter here to "restore a missing metric": either implement the real
            # check, or leave the honest gap. Never wire it to the mutation_scope list again.
        # mutation_scope consistency: every PROTECTED tool should have a scope entry
        for pn in protected_set:
            if pn in _ct.TOOL_MAP and pn not in scope_map:
                mutation_scope_mismatch.append({"tool": pn, "issue": "protected_but_no_scope"})
        # Also: every scoped tool should be either protected or audit/none scope
        for tn, scp in scope_map.items():
            if tn not in _ct.TOOL_MAP:
                continue
            if scp in ("file_artifact", "runtime_ui", "process") and tn not in protected_set:
                mutation_scope_mismatch.append({"tool": tn, "issue": f"scope_{scp}_but_not_protected"})
    except Exception:
        pass

    # Optional: Rawcut/usage probe
    rawcut_usage_scan_ok = None
    rawcut_runtime_function_count = 0
    rawcut_runtime_function_sample: list = []
    rawcut_event_binding_count = 0
    if probe_path:
        try:
            ipu_raw = await inspect_project_usage(project_path=probe_path)
            ipu = json.loads(ipu_raw)
            if ipu.get("ok"):
                rawcut_runtime_function_count = int(ipu.get("runtime_function_count", 0) or 0)
                rawcut_event_binding_count = int(ipu.get("event_binding_count", 0) or 0)
                rawcut_usage_scan_ok = rawcut_runtime_function_count > 0
                rfs = ipu.get("runtime_functions") or []
                rawcut_runtime_function_sample = [
                    {"name": f.get("name"), "file": f.get("file"), "line": f.get("line")}
                    for f in rfs[:5] if isinstance(f, dict)
                ]
        except Exception:
            rawcut_usage_scan_ok = False

    # R43.2: duplicate key detector — runtime dict merge silently overwrites on key
    # collision (Python literal semantics). Scan source files via AST to catch
    # `_TOOL_GOTCHAS`, `_ENGLISH_TOOL_DESCRIPTIONS`, `TOOL_MAP`, etc.
    # R43.3 fixes:
    #   (#1) removed phantom `_TOOL_DESCRIPTIONS` from orchestrator_server scan
    #        (real dict is _ENGLISH_TOOL_DESCRIPTIONS in code_tools.py).
    #   (#2) removed broad `except Exception: pass` — failures now surface to
    #        `duplicate_key_scanner_diagnostics` for visibility (no silent zeros).
    #   (#3) handle ast.AnnAssign too (e.g. `_TOOL_GOTCHAS: dict[...] = {...}`).
    #        Previous Assign-only check missed _TOOL_GOTCHAS entirely → false negative.
    duplicate_key_findings: list = []
    duplicate_key_scanner_diagnostics: dict = {
        "files_scanned": [],
        "files_missing": [],
        "parse_errors": [],
        "dicts_seen": {},  # {dict_name: file_path}
        "dicts_missing": [],
        "scan_ok": False,
    }
    try:
        import ast as _ast
        _scan_targets = [
            # R43.3 fix #1: dropped phantom _TOOL_DESCRIPTIONS
            ("orchestrator_server.py", ["_TOOL_GOTCHAS",
                                          "_PAYLOAD_EXAMPLES", "_ARG_ALIASES",
                                          "_HARMLESS_IGNORE_FIELDS"]),
            ("code_tools.py", ["TOOL_MAP", "_MUTATION_SCOPE_MAP",
                                "_ENGLISH_TOOL_DESCRIPTIONS"]),
        ]
        _self_dir = Path(__file__).parent
        _all_targets = set()
        for _fn, _dn in _scan_targets:
            _all_targets.update(_dn)
        for _fname, _dict_names in _scan_targets:
            _fp = _self_dir / _fname
            if not _fp.exists():
                duplicate_key_scanner_diagnostics["files_missing"].append(_fname)
                continue
            try:
                # R43.3 fix #2b: utf-8-sig strips BOM (U+FEFF) which crashes ast.parse.
                # Both source files have BOM — old silent-except detector returned
                # count=0 as false negative because of this. Discovered via new diagnostics.
                _tree = _ast.parse(_fp.read_text(encoding="utf-8-sig"))
            except Exception as _parse_exc:
                duplicate_key_scanner_diagnostics["parse_errors"].append({
                    "file": _fname, "error": str(_parse_exc)[:200]
                })
                continue
            duplicate_key_scanner_diagnostics["files_scanned"].append(_fname)
            for _node in _ast.walk(_tree):
                # R43.3 fix #3: handle both Assign and AnnAssign
                _value_node = None
                _target_names: set = set()
                if isinstance(_node, _ast.Assign):
                    _target_names = {t.id for t in _node.targets if isinstance(t, _ast.Name)}
                    _value_node = _node.value
                elif isinstance(_node, _ast.AnnAssign):
                    if isinstance(_node.target, _ast.Name) and _node.value is not None:
                        _target_names = {_node.target.id}
                        _value_node = _node.value
                else:
                    continue
                _scan_match = _target_names & set(_dict_names)
                if not _scan_match:
                    continue
                if not isinstance(_value_node, _ast.Dict):
                    continue
                # Record dict was successfully located
                for _dname in _scan_match:
                    duplicate_key_scanner_diagnostics["dicts_seen"][_dname] = _fname
                _key_to_lines: dict = {}
                for _k in _value_node.keys:
                    if isinstance(_k, _ast.Constant) and isinstance(_k.value, str):
                        _key_to_lines.setdefault(_k.value, []).append(_k.lineno)
                for _key, _lines in _key_to_lines.items():
                    if len(_lines) > 1:
                        duplicate_key_findings.append({
                            "file": _fname,
                            "dict": list(_scan_match)[0],
                            "key": _key,
                            "occurrences": _lines,
                            "winner_line": _lines[-1],
                            "shadowed_lines": _lines[:-1],
                        })
        # Flag dicts not located (helps spot phantoms in future)
        _located = set(duplicate_key_scanner_diagnostics["dicts_seen"].keys())
        duplicate_key_scanner_diagnostics["dicts_missing"] = sorted(_all_targets - _located)
        # R43.3 fix #2: scan_ok requires no missing files, no parse errors, all targets located
        duplicate_key_scanner_diagnostics["scan_ok"] = (
            not duplicate_key_scanner_diagnostics["files_missing"]
            and not duplicate_key_scanner_diagnostics["parse_errors"]
            and not duplicate_key_scanner_diagnostics["dicts_missing"]
        )
    except Exception as _outer_exc:
        # R43.3 fix #2: surface outer failure instead of silent pass
        duplicate_key_scanner_diagnostics["fatal_error"] = str(_outer_exc)[:300]
        duplicate_key_scanner_diagnostics["scan_ok"] = False

    # R43.3 H11: import topology check — tools_v15.py must NOT have top-level
    # `import orchestrator_server` or `import code_tools` (creates cyclic load).
    # Lazy imports (inside functions) are OK.
    import_topology_violations: list = []
    import_topology_scan_ok = False
    try:
        import ast as _ast2
        _tv_path = Path(__file__)  # tools_v15.py — the very file we're in
        _tv_tree = _ast2.parse(_tv_path.read_text(encoding="utf-8-sig"))
        _FORBIDDEN_AT_TOP = {"orchestrator_server", "code_tools"}
        for _stmt in _tv_tree.body:  # top-level only (NOT walk)
            if isinstance(_stmt, _ast2.Import):
                for _alias in _stmt.names:
                    if _alias.name in _FORBIDDEN_AT_TOP:
                        import_topology_violations.append({
                            "line": _stmt.lineno,
                            "module": _alias.name,
                            "form": f"import {_alias.name}",
                        })
            elif isinstance(_stmt, _ast2.ImportFrom):
                if _stmt.module in _FORBIDDEN_AT_TOP:
                    import_topology_violations.append({
                        "line": _stmt.lineno,
                        "module": _stmt.module,
                        "form": f"from {_stmt.module} import ...",
                    })
            # Also check inside top-level Try (line 10169-style pattern in code_tools)
            elif isinstance(_stmt, _ast2.Try):
                for _inner in _stmt.body:
                    if isinstance(_inner, _ast2.Import):
                        for _alias in _inner.names:
                            if _alias.name in _FORBIDDEN_AT_TOP:
                                import_topology_violations.append({
                                    "line": _inner.lineno,
                                    "module": _alias.name,
                                    "form": f"top-level try: import {_alias.name}",
                                })
                    elif isinstance(_inner, _ast2.ImportFrom) and _inner.module in _FORBIDDEN_AT_TOP:
                        import_topology_violations.append({
                            "line": _inner.lineno,
                            "module": _inner.module,
                            "form": f"top-level try: from {_inner.module} import ...",
                        })
        import_topology_scan_ok = True
    except Exception as _topo_exc:
        import_topology_violations.append({"scan_error": str(_topo_exc)[:200]})

    # R28/R29 targets vs actual
    targets = {
        "drift_count": 0,
        "alias_drift_count_max": 5,
        "weak_schema_count_max": 3,
        "mutation_scope_consistent": True,
        "alias_contract_intact": True,
        "duplicate_key_count_max": 0,  # R43.2
    }
    actuals = {
        "drift_count": s.get("drift_count", 0),
        "alias_drift_count": s.get("alias_drift_count", 0),
        "weak_schema_count": weak_schema_count,
        "mutation_scope_consistent": (len(mutation_scope_mismatch) == 0),
        "alias_contract_intact": alias_contract_intact,
        "duplicate_key_count": len(duplicate_key_findings),  # R43.2
        "import_topology_violation_count": len(import_topology_violations),  # R43.3 H11
    }
    deltas = {
        "drift_ok": actuals["drift_count"] == 0,
        "alias_drift_ok": actuals["alias_drift_count"] <= targets["alias_drift_count_max"],
        "weak_schema_ok": actuals["weak_schema_count"] <= targets["weak_schema_count_max"],
        "mutation_scope_ok": actuals["mutation_scope_consistent"],
        "alias_contract_ok": actuals["alias_contract_intact"] is True,
        "duplicate_key_ok": actuals["duplicate_key_count"] == 0,  # R43.2
        # R43.3 fix #2: detector must actually run successfully — silent failure can no longer mask dup keys
        "duplicate_key_scanner_ok": bool(duplicate_key_scanner_diagnostics.get("scan_ok")),
        # R43.3 H11: no top-level os/ct imports in tools_v15.py
        "import_topology_ok": (import_topology_scan_ok
                                and actuals["import_topology_violation_count"] == 0),
    }
    all_pass = all(deltas.values())

    return _ok({
        "project_path": project_path,
        "generated_at": raw_matrix.get("generated_at"),
        "all_pass": all_pass,
        "targets_r29": targets,
        "actuals": actuals,
        "deltas_vs_targets": deltas,
        # Detailed sub-metrics
        "matrix_summary": {
            "code_tools": s.get("code_tools"),
            "schema_tools": s.get("schema_tools"),
            "skill_tools": s.get("skill_tools"),
            "inventory_tools": s.get("inventory_tools"),
            "alias_drift_severity": s.get("alias_drift_severity"),
            "progressive_gap_count": s.get("progressive_gap_count", 0),
            "inventory_extra_endpoints_count": s.get("inventory_extra_endpoints_count"),
            "inventory_extra_internal_count": s.get("inventory_extra_internal_count"),
            "inventory_extra_planned_count": s.get("inventory_extra_planned_count"),
            "inventory_extra_doc_only_count": s.get("inventory_extra_doc_only_count"),
            "inventory_extra_unknown_count": s.get("inventory_extra_unknown_count"),
        },
        "alias_contract": {
            "intact": alias_contract_intact,
            "pass": alias_pass,
            "fail": alias_fail,
        },
        "weak_schema": {
            "count": weak_schema_count,
            "examples": weak_schema_examples,
        },
        "mutation_scope": {
            "consistent": (len(mutation_scope_mismatch) == 0),
            "mismatches": mutation_scope_mismatch[:5],
        },
        # R43.2: surface duplicate-key findings + R43.3 scanner diagnostics
        "duplicate_keys": {
            "count": len(duplicate_key_findings),
            "findings": duplicate_key_findings[:10],
            "scanner_diagnostics": duplicate_key_scanner_diagnostics,  # R43.3 fix #2
        },
        # R43.3 H11: import topology safeguard
        "import_topology": {
            "scan_ok": import_topology_scan_ok,
            "violation_count": len(import_topology_violations),
            "violations": import_topology_violations[:10],
            "rule": "tools_v15.py must not import orchestrator_server / code_tools at top level (cyclic load)",
        },
        "rawcut_usage_probe": ({
            "ok": rawcut_usage_scan_ok,
            "runtime_function_count": rawcut_runtime_function_count,
            "event_binding_count": rawcut_event_binding_count,
            "runtime_function_sample": rawcut_runtime_function_sample,
        } if probe_path else None),
        "drill_down_hint": (
            "All targets met — ship clean." if all_pass else
            "Targets not all met. Use audit_tool_matrix / alias_contract_test / inspect_project_usage for drill-down."
        ),
        "ca_next_action": "no_action_needed" if all_pass else "review_failed_targets",
    })


async def run_script(page_id: str, script_path: str = "",
                      script_inline: str = "",
                      timeout_ms: int = 10000) -> str:
    """V15 R16.4 — Escape-free JS executor. Reads JS expression from a `.js`
    file (preferred) or `script_inline`, evaluates in browser page, returns
    result. Bypasses shell/JSON quote-escape pain.

    Use cases:
    - CA needs to set textarea with UNC path / multi-line / Unicode
    - CA writes multi-statement DOM script that has nested quotes/backslashes
    - Anything where inline `evaluate_js` would have escape issues

    Either script_path OR script_inline required. script_path takes precedence.

    File contents MUST be a single JS expression returning a value (wrap
    multi-statement in IIFE: `(()=>{...; return X})()`).
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    if not script_path and not script_inline:
        return _err("Provide script_path OR script_inline",
                    error_name="MISSING_REQUIRED_FIELD",
                    missing_fields=["script_path", "script_inline"])
    if script_path:
        sp = Path(script_path)
        if not sp.exists() or not sp.is_file():
            return _err(f"script_path not found: {script_path}",
                        error_name="SCRIPT_FILE_NOT_FOUND")
        try:
            expression = sp.read_text(encoding="utf-8", errors="strict")
        except Exception as exc:
            return _err(f"cannot read script: {exc}",
                        error_name="SCRIPT_READ_FAILED")
    else:
        expression = script_inline
    if not expression.strip():
        return _err("script is empty",
                    error_name="EMPTY_SCRIPT")
    import asyncio as _aio
    try:
        try:
            result = await _aio.wait_for(
                state["page"].evaluate(expression),
                timeout=max(0.5, timeout_ms / 1000.0)
            )
        except _aio.TimeoutError:
            return _err(
                f"run_script timed out after {timeout_ms} ms",
                error_name="EVAL_TIMEOUT",
                timeout_ms=timeout_ms,
                source=("file" if script_path else "inline"),
                script_path=script_path or None,
                preview=expression[:160],
            )
        return _ok({
            "page_id": page_id,
            "source": "file" if script_path else "inline",
            "script_path": script_path or None,
            "script_length": len(expression),
            "timeout_ms": timeout_ms,
            "result": result,
        })
    except Exception as exc:
        msg = str(exc)
        # V15 R16.6 — detect common JS errors → specific error_name + actionable hint
        err_name = "EVAL_FAILED"
        hint = None
        if "Illegal return statement" in msg or "return statement outside" in msg:
            err_name = "ILLEGAL_TOP_LEVEL_RETURN"
            hint = (
                "Script content is eval'd at top level — `return` is illegal. "
                "Wrap your script in an IIFE: `(()=>{ ...your code...; return X })()` "
                "or `(async ()=>{ ...; return X })()` for async work. "
                "See skill 18 Recipe `run_script` template."
            )
        elif "is not defined" in msg:
            err_name = "EVAL_UNDEFINED_REFERENCE"
            hint = "ReferenceError: variable/function not defined. Check spelling or required globals."
        elif "Unexpected token" in msg or "SyntaxError" in msg:
            err_name = "EVAL_SYNTAX_ERROR"
            hint = "JS syntax error. Inspect `preview` field for the offending region."
        return _err(msg, error_name=err_name,
                    source=("file" if script_path else "inline"),
                    script_path=script_path or None,
                    preview=expression[:160],
                    hint=hint,
                    ca_next_action=("wrap_in_iife_then_retry" if err_name == "ILLEGAL_TOP_LEVEL_RETURN" else "inspect_preview_and_fix"))


async def read_value(page_id: str, selector: str,
                       encoding: str = "plain", max_chars: int = 100000) -> str:
    """V15 R16.3 — Symmetric counterpart to fill_value: read element.value (or
    textContent for non-input elements) safely.

    `encoding="plain"` (default): return content as-is.
    `encoding="base64"`: return base64(UTF-8) for transport-safe round-trip of
                          backslash / Unicode / multi-line content.

    Use when you need the CURRENT VALUE of a textarea/input/contenteditable
    (or any element). Avoids evaluate_js boilerplate + JSON escape pitfall.

    Compare with extract_log_section: that tool is title-driven (find by heading
    text). read_value is selector-driven (you know the element id/CSS path).
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    if encoding not in {"plain", "base64"}:
        return _err(f"encoding must be plain|base64, got '{encoding}'",
                    error_name="UNSUPPORTED_FIELD",
                    accepted_encodings=["plain", "base64"])
    try:
        expr = (
            "(() => {"
            f"  const el = document.querySelector({json.dumps(selector)});"
            "  if (!el) return {found: false};"
            "  const has_value = ('value' in el);"
            "  const text = has_value ? String(el.value || '') : (el.textContent || '');"
            "  return {"
            "    found: true,"
            "    has_value_property: has_value,"
            "    tag: el.tagName,"
            "    id: el.id || '',"
            "    text: text,"
            "    length: text.length,"
            "  };"
            "})()"
        )
        data = await state["page"].evaluate(expr)
        if not data.get("found"):
            return _err(f"selector not found: {selector}",
                        error_name="SELECTOR_NOT_FOUND",
                        selector=selector)
        text = data.get("text") or ""
        truncated = False
        if max_chars and len(text) > max_chars:
            text = text[:max_chars]
            truncated = True
        if encoding == "base64":
            import base64 as _b64
            payload = _b64.b64encode(text.encode("utf-8")).decode("ascii")
        else:
            payload = text
        return _ok({
            "page_id": page_id,
            "selector": selector,
            "encoding": encoding,
            "tag": data.get("tag"),
            "id": data.get("id"),
            "has_value_property": data.get("has_value_property"),
            "length": data.get("length"),
            "truncated": truncated,
            "max_chars": max_chars,
            "content": payload,
            "tail_60": text[-60:],
        })
    except Exception as exc:
        return _err(f"read_value failed: {exc}",
                    error_name="DOM_READ_FAILED",
                    selector=selector)


async def fill_value(page_id: str, selector: str, value: str,
                      encoding: str = "plain", verify: bool = True) -> str:
    """V15 R16 — Transport-safe DOM fill. Avoids JSON backslash strip pitfall.

    encoding="plain": value passed as-is via Playwright .fill().
    encoding="base64": value decoded inside the browser using atob + TextDecoder,
                       preserving UNC paths, UTF-8, multi-line.

    V15 R16.2 verify=True (default): after write, read element value back, compute
    expected length from input, return matches_intent + drift detail. Detects
    JSON-escape backslash strip silently mangling content.

    Always dispatches input + change events.
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    if encoding not in {"plain", "base64"}:
        return _err(f"encoding must be plain|base64, got '{encoding}'",
                    error_name="UNSUPPORTED_FIELD",
                    accepted_encodings=["plain", "base64"])
    try:
        page = state["page"]
        if encoding == "plain":
            await page.locator(selector).first.fill(value, timeout=5000)
            length_written = len(value)
            tail = value[-40:]
        else:
            # base64 mode: decode in browser to preserve every byte
            expr = (
                "(() => {"
                f"  const el = document.querySelector({json.dumps(selector)});"
                "  if (!el) return {found: false};"
                f"  const b = {json.dumps(value)};"
                "  const bytes = Uint8Array.from(atob(b), c => c.charCodeAt(0));"
                "  const text = new TextDecoder('utf-8').decode(bytes);"
                "  el.focus();"
                "  if ('value' in el) {"
                "    el.value = text;"
                "  } else {"
                "    el.textContent = text;"
                "  }"
                "  el.dispatchEvent(new Event('input', {bubbles:true}));"
                "  el.dispatchEvent(new Event('change', {bubbles:true}));"
                "  el.blur();"
                "  const written = ('value' in el ? el.value : el.textContent) || '';"
                "  return {found: true, length: written.length, tail: written.slice(-40)};"
                "})()"
            )
            data = await page.evaluate(expr)
            if not data.get("found"):
                return _err(f"selector not found: {selector}",
                            error_name="SELECTOR_NOT_FOUND")
            length_written = data["length"]
            tail = data["tail"]
        # V15 R16.2 — post-write verify
        expected_length = None
        matches_intent = None
        verify_drift = None
        if verify:
            try:
                if encoding == "base64":
                    import base64 as _b64
                    expected_text = _b64.b64decode(value).decode("utf-8", errors="replace")
                else:
                    expected_text = value
                expected_length = len(expected_text)
                # Read element value back
                read_expr = (
                    "(() => {"
                    f"  const el = document.querySelector({json.dumps(selector)});"
                    "  if (!el) return null;"
                    "  return ('value' in el ? el.value : el.textContent) || '';"
                    "})()"
                )
                actual = await page.evaluate(read_expr)
                actual = actual or ""
                matches_intent = (actual == expected_text)
                if not matches_intent:
                    verify_drift = {
                        "expected_length": expected_length,
                        "actual_length": len(actual),
                        "first_40_expected": expected_text[:40],
                        "first_40_actual": actual[:40],
                        "tail_40_expected": expected_text[-40:],
                        "tail_40_actual": actual[-40:],
                    }
            except Exception as exc:
                verify_drift = {"verify_error": str(exc)}
                matches_intent = None
        return _ok({
            "page_id": page_id, "selector": selector,
            "encoding": encoding,
            "length_written": length_written,
            "tail_40": tail,
            "filled": True,
            "verified": bool(verify),
            "matches_intent": matches_intent,
            "expected_length": expected_length,
            "verify_drift": verify_drift,
        })
    except Exception as exc:
        return _err(f"fill_value failed: {exc}",
                    error_name="FILL_FAILED",
                    selector=selector, encoding=encoding,
                    transparency_hint=(
                        "R42: if you switch encoding strategy "
                        "(e.g. base64 → plain after this failure), MUST mention "
                        "the change in your reply to User — encoding affects "
                        "fidelity of UNC paths / multi-line / Unicode content."
                    ),
                    base64_encoding_note=(
                        "[A] backend decode via atob(value) in browser. "
                        "Node.js encoder: Buffer.from(text,'utf8').toString('base64'). "
                        "Do NOT use btoa(unescape(encodeURIComponent(...))) — that is "
                        "browser-only JS and won't run in Node. If Buffer.from still "
                        "fails (atob InvalidCharacterError), the value contains stray "
                        "whitespace/newlines — strip first."
                    ),
                    ca_next_action="check_base64_encoding_node_buffer_then_retry_or_fallback_plain_and_report")


async def cdp_attach_target(target_id: str = "", cdp_port: int = 9223,
                              websocket_url: str = "",
                              project_path: str = "") -> str:
    """V15 R15 P2.6 — Attach Playwright to an existing CDP target (e.g. Electron
    renderer process already exposing remote-debugging-port).

    Modes:
    - target_id  → resolve websocket_url from list_cdp_targets, then connect_over_cdp
    - websocket_url → connect directly (skip target lookup)

    Returns `attach_id` (== page_id) that can be passed to click / fill /
    evaluate_js / read_page / query_selector exactly like a launched page.
    Registers entry in _BROWSER_PAGES so existing tools work unchanged.

    The attach is stateful for the lifetime of the orchestrator process.
    Use desktop_*  tools as fallback when CDP unavailable.
    """
    if not _playwright_available():
        return _missing_dep("playwright",
                            "pip install playwright && playwright install chromium")
    if not target_id and not websocket_url:
        return _err("Provide ONE of: target_id (from list_cdp_targets) OR websocket_url (from /json/version). You do NOT need both.",
                    error_name="MISSING_REQUIRED_FIELD",
                    missing_fields=["target_id", "websocket_url"],
                    one_of=["target_id", "websocket_url"],
                    hint="R66: call list_cdp_targets(cdp_port) first, then pass that target's id as target_id. For Electron browser-level attach, pass websocket_url from /json/version instead.",
                    ca_next_action="call_list_cdp_targets_then_retry_with_one_target_id")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return _missing_dep("playwright", str(exc))

    # Resolve websocket_url from target_id if needed
    ws_url = websocket_url or ""
    target_meta = {}
    if not ws_url:
        import json as _json
        import urllib.request as _ur
        try:
            with _ur.urlopen(f"http://localhost:{cdp_port}/json/list", timeout=3) as resp:
                targets = _json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return _err(f"Cannot list CDP targets on port {cdp_port}: {exc}",
                        error_name="CDP_UNREACHABLE", cdp_port=cdp_port,
                        diagnostic_hint=(
                            "R28: distinguish runtime_absent vs ui_inaccessible. "
                            "Call desktop_window_list — if NO matching app window → runtime_absent "
                            "(call launch_electron_with_cdp). If window exists → ui_inaccessible "
                            "(wrong port or CDP flag missing)."
                        ),
                        ca_next_action="check_desktop_window_list_to_distinguish_runtime_absent_vs_inaccessible")
        match = next((t for t in targets if t.get("id") == target_id), None)
        if not match:
            return _err(f"target_id '{target_id}' not in CDP target list on port {cdp_port}",
                        error_name="CDP_TARGET_NOT_FOUND",
                        available_target_ids=[t.get("id") for t in targets[:10]])
        ws_url = match.get("webSocketDebuggerUrl") or ""
        target_meta = {
            "title": match.get("title", ""),
            "url": match.get("url", ""),
            "type": match.get("type", ""),
        }
        if not ws_url:
            return _err(f"target '{target_id}' has no webSocketDebuggerUrl",
                        error_name="CDP_NO_WEBSOCKET")

    # V15 R16 fix: Electron renderer rejects Target.createBrowserContext when
    # attaching via page-level WS. Auto-fallback to browser-level WS from
    # /json/version when we detect this specific failure.
    import json as _json2
    import urllib.request as _ur2
    pw = await async_playwright().start()
    attach_via = "page_level_ws"
    attempted_urls = [ws_url]
    last_exc = None
    browser = None
    for _attempt in range(2):
        try:
            browser = await pw.chromium.connect_over_cdp(ws_url)
            # Probe a context to detect Electron rejection EARLY
            _ctxs = browser.contexts
            if not (_ctxs and _ctxs[0].pages):
                # Need new context — this is the line that fails on Electron
                try:
                    await browser.new_context()
                except Exception as ctx_exc:
                    raise ctx_exc
            break  # success
        except Exception as exc:
            last_exc = exc
            err_text = str(exc)
            is_electron_reject = (
                "createBrowserContext" in err_text and "Not allowed" in err_text
            )
            if is_electron_reject and _attempt == 0:
                # Re-fetch browser-level WS from /json/version
                try:
                    with _ur2.urlopen(f"http://localhost:{cdp_port}/json/version", timeout=3) as resp:
                        ver = _json2.loads(resp.read().decode("utf-8"))
                    fallback_ws = ver.get("webSocketDebuggerUrl") or ""
                    if fallback_ws and fallback_ws != ws_url:
                        ws_url = fallback_ws
                        attach_via = "browser_level_fallback"
                        attempted_urls.append(ws_url)
                        try: await browser.close()
                        except Exception: pass
                        browser = None
                        continue  # retry connect with browser-level URL
                except Exception:
                    pass
            # R73: some Electron builds reject Playwright connect_over_cdp entirely
            # ("Browser context management is not supported" / Browser.setDownloadBehavior).
            # This is a Playwright<->legacy-Electron incompatibility, not transient — do not retry CDP.
            is_legacy_electron = ('setDownloadBehavior' in err_text) or ('context management is not supported' in err_text.lower())
            # Either non-Electron error or fallback unavailable -> bail
            try: await pw.stop()
            except Exception: pass
            if is_legacy_electron:
                return _err("This Electron build rejects Playwright connect_over_cdp (Browser.setDownloadBehavior unsupported) — its DOM cannot be driven via cdp_attach_target/fill_value/click.",
                            error_name="ELECTRON_CDP_LEGACY_UNSUPPORTED",
                            websocket_url=ws_url, attempted_urls=attempted_urls,
                            ca_next_action="use_headless_backend_or_file_bridge_not_cdp",
                            fallback_guidance=("Do NOT retry CDP. Use a headless path: (1) if the app ships a CLI (e.g. text-diff-tool compare-cli.js) run it via run_project_command and read stdout; (2) or a file bridge the app polls (like the CEP/Premiere ade bridge); (3) or desktop_* window/click tools for coarse UI."))
            return _err(f"cdp_attach_target failed: {exc}",
                        error_name="CDP_ATTACH_FAILED",
                        websocket_url=ws_url,
                        attempted_urls=attempted_urls,
                        attach_via=attach_via,
                        hint=("Page-level WS rejected Target.createBrowserContext (Electron). "
                              "Tried browser-level fallback automatically.") if is_electron_reject else "")
    if browser is None:
        try: await pw.stop()
        except Exception: pass
        return _err(f"cdp_attach_target failed after fallback: {last_exc}",
                    error_name="CDP_ATTACH_FAILED",
                    attempted_urls=attempted_urls)

    try:
        contexts = browser.contexts
        if contexts and contexts[0].pages:
            page = contexts[0].pages[0]
            context = contexts[0]
        else:
            context = await browser.new_context()
            page = await context.new_page()
        attach_id = f"cdp-{int(time.time() * 1000)}-{len(_BROWSER_PAGES)}"
        console_log: list = []
        network_errors: list = []
        page.on("console", lambda msg: console_log.append({
            "type": msg.type, "text": msg.text[:500],
            "at": datetime.now().isoformat(timespec="seconds")
        }))
        page.on("requestfailed", lambda req: network_errors.append({
            "url": req.url[:300], "method": req.method,
            "failure": (req.failure or "")[:200],
            "at": datetime.now().isoformat(timespec="seconds")
        }))
        _BROWSER_PAGES[attach_id] = {
            "page": page, "context": context, "browser": browser, "pw": pw,
            "console": console_log, "network_errors": network_errors,
            "created_at": time.time(),
            "attach_mode": "cdp",
            "target_id": target_id,
            "websocket_url": ws_url,
            "cdp_port": cdp_port,
            "attach_via": attach_via,
            # V15 R44 — pin originating project_path for session-guard resolution.
            "project_path": project_path or "",
        }
        title = ""
        try:
            title = await page.title()
        except Exception:
            pass
        return _ok({
            "attach_id": attach_id,
            "page_id": attach_id,
            "target_id": target_id,
            "websocket_url": ws_url,
            "attempted_urls": attempted_urls,
            "attach_via": attach_via,
            "title": title or target_meta.get("title", ""),
            "url": page.url or target_meta.get("url", ""),
            "type": target_meta.get("type", "page"),
            "attach_mode": "cdp",
            "cdp_port": cdp_port,
            "usage_hint": "Pass `page_id` (= attach_id) to click/fill/evaluate_js/read_page/query_selector.",
        })
    except Exception as exc:
        try: await pw.stop()
        except Exception: pass
        return _err(f"cdp_attach_target post-connect failed: {exc}",
                    error_name="CDP_ATTACH_FAILED",
                    websocket_url=ws_url,
                    attach_via=attach_via)


async def open_browser_page(url: str, viewport_w: int = 1280, viewport_h: int = 800,
                             headless: bool = True, wait_for: str = "load",
                             attach_id: str = "", project_path: str = "",
                             timeout_ms: int = 20000) -> str:
    """V15 R8 B.3: launch Chromium, open URL, return page_id.
    V15 R15 P2.6: if attach_id provided, reuse already-attached CDP session
    instead of launching fresh — for Electron auto-attach flows.
    CAN-051: wait_for = load | domcontentloaded | networkidle | commit (default
    load); timeout_ms per navigation (default 20000). Polling apps should pass
    wait_for='domcontentloaded'. Aliases: wait_until->wait_for, timeout->timeout_ms.
    """
    # V15 R15: attach_id passthrough → reuse existing CDP-attached page
    if attach_id and attach_id in _BROWSER_PAGES:
        state = _BROWSER_PAGES[attach_id]
        try:
            if project_path:
                state.setdefault("project_path", project_path)
            await state["page"].goto(url, wait_until=wait_for, timeout=timeout_ms)
            title = await state["page"].title()
            return _ok({"page_id": attach_id, "attach_id": attach_id,
                        "url": state["page"].url, "title": title,
                        "reused_cdp_attach": True})
        except Exception as exc:
            return _err(f"navigation on attach_id={attach_id} failed: {exc}",
                        error_name="CDP_NAVIGATE_FAILED")
    elif attach_id:
        return _err(f"attach_id '{attach_id}' not registered. Call cdp_attach_target first.",
                    error_name="CDP_ATTACH_EXPIRED",
                    ca_next_action="recall_cdp_attach_target")
    if not _playwright_available():
        return _missing_dep("playwright",
                            "pip install playwright && playwright install chromium")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return _missing_dep("playwright", str(exc))

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": viewport_w, "height": viewport_h})
        page = await context.new_page()
        page_id = f"pg-{int(time.time() * 1000)}-{len(_BROWSER_PAGES)}"
        console_log: list = []
        network_errors: list = []
        page.on("console", lambda msg: console_log.append({
            "type": msg.type, "text": msg.text[:500],
            "at": datetime.now().isoformat(timespec="seconds")
        }))
        page.on("requestfailed", lambda req: network_errors.append({
            "url": req.url[:300], "method": req.method,
            "failure": (req.failure or "")[:200],
            "at": datetime.now().isoformat(timespec="seconds")
        }))
        page.on("response", lambda res: network_errors.append({
            "url": res.url[:300], "status": res.status, "at": datetime.now().isoformat(timespec="seconds")
        }) if res.status >= 400 else None)
        await page.goto(url, wait_until=wait_for, timeout=timeout_ms)
        title = await page.title()
        _BROWSER_PAGES[page_id] = {
            "page": page, "context": context, "browser": browser, "pw": pw,
            "console": console_log, "network_errors": network_errors,
            "created_at": time.time(),
            # V15 R44 — pin originating project_path for session-guard resolution.
            "project_path": project_path or "",
        }
        return _ok({"page_id": page_id, "url": page.url, "title": title})
    except Exception as exc:
        try:
            await pw.stop()
        except Exception:
            pass
        _msg = str(exc)
        if "imeout" in _msg:
            return _err(
                f"open_browser_page timed out after {timeout_ms}ms waiting for '{wait_for}': {exc}",
                error_name="BROWSER_NAV_TIMEOUT",
                retryable=True,
                corrected_payload={"url": url, "wait_for": "domcontentloaded",
                                   "timeout_ms": max(int(timeout_ms), 30000)},
                hint="Polling/SPA pages may never reach 'networkidle' and can be slow to 'load'. Retry with wait_for='domcontentloaded' and/or a larger timeout_ms, then poll DOM state explicitly.",
                ca_next_action="retry_with_wait_for_domcontentloaded_or_larger_timeout_ms",
            )
        return _err(f"open_browser_page failed: {exc}", error_name="BROWSER_LAUNCH_FAILED")


async def _get_page(page_id: str):
    if not isinstance(page_id, str) or not re.fullmatch(r"pg-[A-Za-z0-9][A-Za-z0-9_-]*", page_id):
        return None, _err(
            f"invalid page_id: {page_id!r}; expected a live pg-... ID",
            error_name="PAGE_ID_INVALID",
            ca_next_action="use_page_id_from_open_browser_page_or_cdp_attach_target",
        )
    state = _BROWSER_PAGES.get(page_id)
    if not state:
        return None, _err(f"page_id not found: {page_id}", error_name="PAGE_NOT_FOUND")
    return state, None


async def read_page(page_id: str, selector: str = "body", max_chars: int = 5000) -> str:
    state, err = await _get_page(page_id)
    if err:
        return err
    try:
        page = state["page"]
        el = page.locator(selector).first
        txt = (await el.text_content() or "")[:max_chars]
        return _ok({"page_id": page_id, "selector": selector, "text": txt,
                    "length": len(txt), "truncated": len(txt) >= max_chars})
    except Exception as exc:
        return _err(str(exc), error_name="DOM_READ_FAILED")


async def query_selector(page_id: str, selector: str) -> str:
    state, err = await _get_page(page_id)
    if err:
        return err
    try:
        page = state["page"]
        loc = page.locator(selector).first
        count = await page.locator(selector).count()
        if count == 0:
            return _ok({"page_id": page_id, "selector": selector, "exists": False})
        visible = await loc.is_visible()
        bounds = await loc.bounding_box()
        return _ok({"page_id": page_id, "selector": selector, "exists": True,
                    "count": count, "visible": visible, "bbox": bounds})
    except Exception as exc:
        return _err(str(exc), error_name="SELECTOR_FAILED")


async def click(page_id: str, selector: str) -> str:
    state, err = await _get_page(page_id)
    if err:
        return err
    try:
        await state["page"].locator(selector).first.click(timeout=5000)
        return _ok({"page_id": page_id, "selector": selector, "clicked": True})
    except Exception as exc:
        return _err(str(exc), error_name="CLICK_FAILED")


async def fill(page_id: str, selector: str, value: str) -> str:
    state, err = await _get_page(page_id)
    if err:
        return err
    try:
        await state["page"].locator(selector).first.fill(value, timeout=5000)
        return _ok({"page_id": page_id, "selector": selector, "filled": True})
    except Exception as exc:
        return _err(str(exc), error_name="FILL_FAILED")


async def evaluate_js(page_id: str, expression: str,
                       timeout_ms: int = 8000) -> str:
    """V15 R8 / R16.3: evaluate JS in browser page.

    R16.3: optional `timeout_ms` (default 8000) — protects against infinite loops
    in user expression. Errors return `error_name=EVAL_FAILED` with stack trace
    + line number when extractable. Returns {result} on success.
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    try:
        # Playwright page.evaluate accepts no native timeout; wrap with asyncio
        import asyncio as _aio
        try:
            result = await _aio.wait_for(
                state["page"].evaluate(expression),
                timeout=max(0.5, timeout_ms / 1000.0)
            )
        except _aio.TimeoutError:
            return _err(
                f"evaluate_js timed out after {timeout_ms} ms (possible infinite loop or pending Promise)",
                error_name="EVAL_TIMEOUT",
                timeout_ms=timeout_ms,
                expression_preview=(expression or "")[:120],
                ca_next_action="rewrite_expression_with_explicit_timeout_or_await",
            )
        return _ok({"page_id": page_id, "result": result})
    except Exception as exc:
        # Extract stack + line from Playwright "Error in JavaScript" style messages
        msg = str(exc)
        import re as _re
        stack_match = _re.search(r"(at\s+(?:\S+\s+\()?(?:eval|<anonymous>):(\d+):(\d+))", msg)
        line_no = int(stack_match.group(2)) if stack_match else None
        col_no = int(stack_match.group(3)) if stack_match else None
        return _err(msg, error_name="EVAL_FAILED",
                    expression_preview=(expression or "")[:120],
                    error_line=line_no, error_column=col_no,
                    ca_next_action="inspect_expression_at_line_for_syntax_or_undefined_reference")


async def read_console_errors(page_id: str, level: str = "error") -> str:
    """V15 R8: read browser console messages for a page.
    V15 R15 P2.8: `level` accepts {error, warning, info, all}. Default 'error'
    ignores warnings (e.g. CSP unsafe-eval) so CA doesn't fail tests on benign
    warnings. Response always includes `severity_summary` counts across ALL
    levels regardless of filter.
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    accepted_levels = {"error", "warning", "info", "all"}
    if level not in accepted_levels:
        return _err(f"Unsupported level '{level}'. Use one of: {sorted(accepted_levels)}",
                    error_name="UNSUPPORTED_FIELD",
                    accepted_levels=sorted(accepted_levels))
    all_msgs = state["console"]
    # severity_summary across full buffer (independent of filter)
    severity_summary = {
        "error_count": sum(1 for m in all_msgs if m.get("type") == "error"),
        "warning_count": sum(1 for m in all_msgs if m.get("type") in ("warning", "warn")),
        "info_count": sum(1 for m in all_msgs if m.get("type") in ("info", "log", "debug")),
        "total": len(all_msgs),
    }
    if level == "all":
        msgs = all_msgs
    elif level == "warning":
        msgs = [m for m in all_msgs if m.get("type") in ("warning", "warn")]
    elif level == "info":
        msgs = [m for m in all_msgs if m.get("type") in ("info", "log", "debug")]
    else:  # error
        msgs = [m for m in all_msgs if m.get("type") == "error"]
    return _ok({
        "page_id": page_id,
        "level": level,
        "console": msgs[-50:],
        "total_filtered": len(msgs),
        "severity_summary": severity_summary,
        "accepted_levels": sorted(accepted_levels),
    })


async def read_network_errors(page_id: str) -> str:
    state, err = await _get_page(page_id)
    if err:
        return err
    errors = state["network_errors"]
    return _ok({"page_id": page_id, "errors": errors[-50:], "total": len(errors)})


async def screenshot_page(page_id: str, full_page: bool = False,
                           save_path: str = "") -> str:
    state, err = await _get_page(page_id)
    if err:
        return err
    try:
        if not save_path:
            ts = int(time.time() * 1000)
            cache_dir = Path(__file__).parent / ".orchestrator_cache" / "screenshots"
            cache_dir.mkdir(parents=True, exist_ok=True)
            save_path = str(cache_dir / f"{page_id}_{ts}.png")
        await state["page"].screenshot(path=save_path, full_page=full_page)
        size = Path(save_path).stat().st_size if Path(save_path).exists() else 0
        return _ok({"page_id": page_id, "path": save_path, "bytes": size,
                    "full_page": full_page})
    except Exception as exc:
        return _err(str(exc), error_name="SCREENSHOT_FAILED")


async def wait_for_selector(page_id: str, selector: str,
                              timeout_ms: int = 5000, state_str: str = "visible") -> str:
    state, err = await _get_page(page_id)
    if err:
        return err
    try:
        await state["page"].locator(selector).first.wait_for(
            state=state_str, timeout=timeout_ms)
        return _ok({"page_id": page_id, "selector": selector, "matched": True})
    except Exception as exc:
        return _err(str(exc), error_name="WAIT_TIMEOUT", matched=False)


async def compare_dom_snapshot(page_id_a: str, page_id_b: str,
                                 selector: str = "body") -> str:
    state_a, err = await _get_page(page_id_a)
    if err:
        return err
    state_b, err = await _get_page(page_id_b)
    if err:
        return err
    try:
        text_a = (await state_a["page"].locator(selector).first.text_content() or "")
        text_b = (await state_b["page"].locator(selector).first.text_content() or "")
        import difflib
        diff = list(difflib.unified_diff(
            text_a.splitlines(), text_b.splitlines(),
            fromfile="A", tofile="B", lineterm="", n=2))
        return _ok({"identical": text_a == text_b,
                    "diff": "\n".join(diff[:200]),
                    "diff_lines": len(diff)})
    except Exception as exc:
        return _err(str(exc), error_name="DOM_COMPARE_FAILED")


async def _close_browser_page(page_id: str) -> None:
    state = _BROWSER_PAGES.pop(page_id, None)
    if not state:
        return
    try:
        await state["context"].close()
        await state["browser"].close()
        await state["pw"].stop()
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────────────────
# B.4 — T4c Server lifecycle (psutil)
# ────────────────────────────────────────────────────────────────────────────

def _psutil_available() -> bool:
    try:
        import psutil  # noqa: F401
        return True
    except ImportError:
        return False


_PSUTIL_PORT_SCAN_CACHE: dict[int, tuple[float, dict]] = {}
_PSUTIL_PORT_SCAN_TTL_SECONDS = float(os.environ.get("ORCHESTRATOR_PSUTIL_PORT_SCAN_TTL_SEC", "3.0"))


async def server_lifecycle_status(port: int = 0) -> str:
    """V15 R8 B.4: psutil-based port owner inspection (fail-safe with timeout).

    V15 R24: explicit missing-port hint — suggests `inspect_project_runtime` first
    to discover the port instead of guessing.
    """
    if not port or int(port) <= 0:
        return _err("port is required (positive integer)",
                    error_name="MISSING_REQUIRED_FIELD",
                    accepted_fields=["port"],
                    payload_example={"port": 9223},
                    ca_next_action="call_inspect_project_runtime_first_if_unknown",
                    hint=("Port unknown? Call inspect_project_runtime(project_path) "
                           "first — it returns the expected port for Electron/Node "
                           "launchers. Then pass that port here."))
    if not _psutil_available():
        return _missing_dep("psutil", "pip install psutil")
    import psutil
    port = int(port)
    now = time.monotonic()
    cached = _PSUTIL_PORT_SCAN_CACHE.get(port)
    if cached and now - cached[0] < _PSUTIL_PORT_SCAN_TTL_SECONDS:
        data = dict(cached[1])
        data["cache_hit"] = True
        return _ok({"port": port, **data})

    def _describe(conn) -> dict:
        laddr_ip = str(getattr(conn.laddr, "ip", "") or "")
        base = {"listening": True, "pid": conn.pid,
                "family": "ipv6" if ":" in laddr_ip else "ipv4",
                "laddr": f"{laddr_ip}:{port}"}
        if not conn.pid:
            return base
        try:
            p = psutil.Process(conn.pid)
            # Use as_dict for batched safe info gathering
            info_raw = p.as_dict(
                attrs=["name", "cmdline", "username"],
                ad_value=None,
            )
            info = dict(base)
            info.update({
                "process_name": info_raw.get("name"),
                "cmdline": " ".join((info_raw.get("cmdline") or [])[:5]),
                "username": info_raw.get("username"),
            })
            # Skip can_kill probe - suspend/resume can hang on Windows without admin.
            # Infer from username comparison instead.
            try:
                current_user = psutil.Process(os.getpid()).username()
                info["can_kill"] = (info_raw.get("username") == current_user)
                info["requires_admin"] = not info["can_kill"]
            except Exception:
                info["can_kill"] = None
                info["requires_admin"] = None
            return info
        except psutil.NoSuchProcess:
            return {**base, "error": "process gone"}
        except psutil.AccessDenied:
            return {**base,
                    "process_name": None,
                    "requires_admin": True,
                    "can_kill": False,
                    "note": "AccessDenied on Process; likely admin/elevated owner"}

    def _scan() -> dict:
        # FixA2407 P1-3: scan tcp4 THEN tcp6. [A] itself binds ::1 only, so the old
        # tcp4-only scan answered listening:false for port 8083 while /health returned
        # 200 - the lifecycle probe contradicted the live server and could not be
        # trusted about [A]. tcp4 stays first (cheaper, far more common); tcp6 costs a
        # second pass only when the port is genuinely absent from IPv4.
        scanned: list[str] = []
        try:
            for kind in ("tcp4", "tcp6"):
                try:
                    connections = psutil.net_connections(kind=kind)
                except (psutil.AccessDenied, PermissionError):
                    # Fallback: skip network scan, just try Process probe via /proc-like lookup
                    return {"listening": None, "pid": None,
                            "note": "AccessDenied for net_connections; admin needed for full scan",
                            "requires_admin": True, "families_scanned": scanned}
                except ValueError:
                    continue  # address family unsupported on this platform build
                scanned.append(kind)
                for conn in connections:
                    if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN":
                        return {**_describe(conn), "families_scanned": scanned}
            return {"listening": False, "pid": None, "families_scanned": scanned}
        except Exception as exc:
            return {"listening": None, "pid": None, "error": str(exc)[:200],
                    "families_scanned": scanned}

    # Bound the scan with a hard timeout via thread + asyncio.wait_for
    try:
        data = await asyncio.wait_for(asyncio.to_thread(_scan), timeout=8.0)
    except asyncio.TimeoutError:
        return _err("psutil scan timeout (>8s)", error_name="LIFECYCLE_TIMEOUT", port=port)
    _PSUTIL_PORT_SCAN_CACHE[port] = (time.monotonic(), dict(data))
    if data.get("listening"):
        data.setdefault("recommended_action", "no_action_needed" if data.get("can_kill") else
                        "ask_user_end_via_task_manager")
    return _ok({"port": port, **data})


async def port_owner_inspect(port: int) -> str:
    """Compact output from the shared lifecycle scan/cache, not a cheaper scan.

    Success: port, listening (true/false/null), pid (integer/null),
    process_name (string/null), plus the standard response envelope.
    listening=null means unknown, not a confirmed free port. Errors pass through.
    """
    raw = await server_lifecycle_status(port)
    data = json.loads(raw)
    if not data.get("ok"):
        return raw
    out = {"port": port, "listening": data.get("listening"), "pid": data.get("pid"),
           "process_name": data.get("process_name")}
    return _ok(out)


async def detect_stale_server(port: int, expected_bundle_version: str = "") -> str:
    """V15 R8 B.4: probe / and check bundle marker."""
    import urllib.request
    url = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            text = resp.read(8192).decode("utf-8", errors="ignore")
            etag = resp.headers.get("ETag", "")
            last_mod = resp.headers.get("Last-Modified", "")
        if expected_bundle_version:
            stale = expected_bundle_version not in text
            return _ok({"port": port, "url": url, "stale": stale,
                        "expected": expected_bundle_version,
                        "etag": etag, "last_modified": last_mod})
        return _ok({"port": port, "url": url, "etag": etag, "last_modified": last_mod,
                    "content_preview": text[:200]})
    except Exception as exc:
        return _err(f"probe failed: {exc}", error_name="PROBE_FAILED", port=port)


async def static_asset_cache_check(url: str) -> str:
    """V15 R8 B.4 (R9 fix): HEAD + 2 GETs to detect cache-stale assets.

    Loopback guard: if URL targets the [A] orchestrator's own port (8083) and the
    aiohttp event loop is handling the request, synchronous urlopen would deadlock
    because the single-threaded event loop is busy serving us. Detect own-port and
    return guidance instead of blocking.

    Runs in a thread (asyncio.to_thread) so external URLs do not block the loop.
    """
    import urllib.parse
    import urllib.request

    # Loopback guard
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        own_port = int(os.environ.get("ORCHESTRATOR_PORT", "8083"))
        if host in ("localhost", "127.0.0.1", "::1") and (parsed.port or own_port) == own_port:
            return _err(
                f"Loopback to own port {own_port} would deadlock the event loop",
                error_name="LOOPBACK_NOT_SUPPORTED",
                url=url,
                hint="static_asset_cache_check is designed for app/static URLs (e.g., http://127.0.0.1:3099/index.html), not the orchestrator itself. To inspect [A] assets, use detect_stale_server(port=8083) instead.",
            )
    except Exception:
        pass

    def _do() -> dict:
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=5) as r:
                head_headers = dict(r.headers)
            with urllib.request.urlopen(url, timeout=5) as r:
                body1 = r.read()
            with urllib.request.urlopen(url, timeout=5) as r:
                body2 = r.read()
            h1 = hashlib.sha256(body1).hexdigest()[:16]
            h2 = hashlib.sha256(body2).hexdigest()[:16]
            return {
                "ok": True,
                "url": url,
                "cache_control": head_headers.get("Cache-Control", ""),
                "etag": head_headers.get("ETag", ""),
                "last_modified": head_headers.get("Last-Modified", ""),
                "size": len(body1),
                "hash_fetch1": h1,
                "hash_fetch2": h2,
                "stable": h1 == h2,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "error_name": "CACHE_CHECK_FAILED",
                    "url": url}
    try:
        data = await asyncio.wait_for(asyncio.to_thread(_do), timeout=20.0)
    except asyncio.TimeoutError:
        return _err("HEAD/GET exceeded 20s", error_name="TIMEOUT", url=url)
    return json.dumps(data, ensure_ascii=False)


async def bundle_version_check(html_url: str, marker_regex: str = r'(?:name="version" content="([^"]+)"|\?v=([^"&\s]+))') -> str:
    """V15 R8 B.4: extract build/version marker from served HTML."""
    import urllib.request
    try:
        compiled = re.compile(marker_regex)
    except (re.error, TypeError) as exc:
        return _err(
            f"invalid marker_regex: {exc}",
            error_name="VERSION_REGEX_INVALID",
            ca_next_action="provide_a_valid_regex_with_a_capture_group",
        )
    if compiled.groups < 1:
        return _err(
            "marker_regex must contain at least one capture group around the version value",
            error_name="VERSION_REGEX_CAPTURE_REQUIRED",
            ca_next_action="add_a_capture_group_to_marker_regex",
        )
    try:
        with urllib.request.urlopen(html_url, timeout=5) as r:
            text = r.read(50000).decode("utf-8", errors="ignore")
        m = compiled.search(text)
        captures = m.groups() if m else ()
        version = next((value for value in captures if value), None)
        return _ok({"url": html_url, "version_found": version, "matched": bool(m)})
    except Exception as exc:
        return _err(str(exc), error_name="VERSION_CHECK_FAILED", url=html_url)


async def restart_server_via_bat_hint(bat_path: str, port: int = 0) -> str:
    """V15 R8 B.4: returns structured hint, does NOT execute."""
    current = None
    if port:
        raw = await port_owner_inspect(port)
        try:
            current = json.loads(raw)
        except Exception:
            current = None
    # [A]'s own launcher answers with the one [A] restart path, so the hint
    # can never route a CA into a native bat launch of the daemon.
    try:
        is_self = (os.path.normcase(str(Path(bat_path).resolve()))
                   == os.path.normcase(str(Path(__file__).resolve().parent / "start_orchestrator.bat")))
    except Exception:
        is_self = False
    return _ok({
        "bat_path": bat_path,
        "current": current,
        "restart_command": ("call restart_orchestrator_via_bat(delay_seconds=2)" if is_self
                            else f"Run \"{bat_path}\""),
        "post_check": "Verify with a fresh API call (for [A]: GET /health shows a new pid AND a changed generation).",
        "note": "Hint only, nothing executed. [A] itself restarts only via restart_orchestrator_via_bat.",
    })


# ────────────────────────────────────────────────────────────────────────────
# B.5 — T4d Desktop / Electron (pywin32 / pillow)
# ────────────────────────────────────────────────────────────────────────────

def _pywin32_available() -> bool:
    try:
        import win32gui  # noqa: F401
        return True
    except ImportError:
        return False


async def desktop_window_list(title_filter: str = "",
                                 owner_pid: int = 0,
                                 include_child_controls: bool = False,
                                 include_invisible: bool = False) -> str:
    """V15 R8 B.5 / R31: enumerate top-level windows with rich metadata.

    R31: returns hwnd, pid, process_name, class_name, parent_hwnd, owner_hwnd,
    visible, enabled, ex_styles (modal/topmost/tool). Optional include_child_controls
    walks children to list edit/button/combo handles.

    `owner_pid` filter: only windows owned by that PID (folder-dialog owner discovery).
    `include_invisible`: include hidden windows (rare, but useful for dialog dispatch).
    """
    if not _pywin32_available():
        return _missing_dep("pywin32", "pip install pywin32")
    import win32con
    import win32gui
    import win32process

    def _get_proc_name(pid: int) -> str:
        try:
            import psutil as _ps
            return _ps.Process(pid).name()
        except Exception:
            return ""

    def _walk_children(hwnd):
        ctrls = []
        def _w(child, _):
            try:
                cls = win32gui.GetClassName(child) or ""
                txt = win32gui.GetWindowText(child) or ""
                if cls or txt:
                    ctrls.append({
                        "hwnd": child,
                        "class": cls,
                        "text": txt[:80],
                        "visible": bool(win32gui.IsWindowVisible(child)),
                    })
            except Exception:
                pass
            return True
        try:
            win32gui.EnumChildWindows(hwnd, _w, None)
        except Exception:
            pass
        return ctrls[:40]

    def _enum() -> list:
        wins: list = []
        def callback(hwnd, _):
            try:
                visible = bool(win32gui.IsWindowVisible(hwnd))
                if not include_invisible and not visible:
                    return True
                title = win32gui.GetWindowText(hwnd) or ""
                if title_filter and title_filter.lower() not in title.lower():
                    return True
                if not title and not include_invisible:
                    return True
                # PID / process
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    pid = 0
                if owner_pid and pid != owner_pid:
                    return True
                # Class, parent, owner
                try:
                    cls = win32gui.GetClassName(hwnd) or ""
                except Exception:
                    cls = ""
                try:
                    parent = win32gui.GetParent(hwnd) or 0
                except Exception:
                    parent = 0
                try:
                    owner = win32gui.GetWindow(hwnd, 4) or 0  # GW_OWNER
                except Exception:
                    owner = 0
                try:
                    enabled = bool(win32gui.IsWindowEnabled(hwnd))
                except Exception:
                    enabled = True
                # Extended styles
                try:
                    ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) or 0
                except Exception:
                    ex_style = 0
                is_topmost = bool(ex_style & win32con.WS_EX_TOPMOST)
                is_toolwin = bool(ex_style & win32con.WS_EX_TOOLWINDOW)
                rect = win32gui.GetWindowRect(hwnd)
                entry = {
                    "hwnd": hwnd,
                    "title": title[:200],
                    "pid": pid,
                    "process_name": _get_proc_name(pid) if pid else "",
                    "class_name": cls,
                    "parent_hwnd": parent,
                    "owner_hwnd": owner,
                    "visible": visible,
                    "enabled": enabled,
                    "is_topmost": is_topmost,
                    "is_toolwindow": is_toolwin,
                    "is_dialog_class": cls == "#32770",
                    "rect": {"x": rect[0], "y": rect[1],
                              "w": rect[2] - rect[0], "h": rect[3] - rect[1]},
                }
                if include_child_controls:
                    entry["child_controls"] = _walk_children(hwnd)
                wins.append(entry)
            except Exception:
                pass
        win32gui.EnumWindows(callback, None)
        return wins
    wins = await asyncio.to_thread(_enum)
    return _ok({
        "count": len(wins),
        "windows": wins[:50],
        "filters": {"title_filter": title_filter, "owner_pid": owner_pid,
                     "include_child_controls": include_child_controls,
                     "include_invisible": include_invisible},
    })


async def check_window_visibility(hwnd: int = 0, title_pattern: str = "",
                                    match_mode: str = "contains_case_insensitive") -> str:
    """V15 R15 P2.9 — Pre-action visibility check for T4 UI automation.

    Returns concrete visibility state + actionable suggestion so CA can decide:
    - "ok"            → safe to click/fill
    - "restore"       → call desktop_app_focus to bring on-screen
    - "focus"         → window visible but background; bring to front
    - "cdp_fallback"  → Win32 likely UIPI-blocked; use CDP path instead

    Either `hwnd` or `title_pattern` must be provided. If title_pattern, the
    first visible window matching wins.
    """
    import re as _re

    import win32gui
    import win32process

    def _resolve_hwnd() -> tuple[int, str]:
        if hwnd and win32gui.IsWindow(hwnd):
            return hwnd, win32gui.GetWindowText(hwnd) or ""
        if not title_pattern:
            return 0, ""
        # Search top-level windows
        found = [0, ""]
        def _cb(h, _):
            t = win32gui.GetWindowText(h) or ""
            if not t:
                return
            ok = False
            tl, pl = t.lower(), title_pattern.lower()
            if match_mode == "exact":
                ok = (t == title_pattern)
            elif match_mode == "contains":
                ok = (title_pattern in t)
            elif match_mode == "case_insensitive":
                ok = (tl == pl)
            elif match_mode == "regex":
                try: ok = bool(_re.search(title_pattern, t))
                except Exception: ok = False
            else:  # contains_case_insensitive default
                ok = (pl in tl)
            if ok and not found[0]:
                found[0] = h
                found[1] = t
        win32gui.EnumWindows(_cb, None)
        return found[0], found[1]

    def _check():
        target_hwnd, title = _resolve_hwnd()
        if not target_hwnd:
            return {
                "ok": False,
                "error_name": "WINDOW_NOT_FOUND",
                "hwnd": 0,
                "title_pattern": title_pattern,
                "suggestion": "ok",  # nothing to act on
                "ca_next_action": "verify_app_launched_via_run_project_command",
            }
        try:
            visible = bool(win32gui.IsWindowVisible(target_hwnd))
            minimized = bool(win32gui.IsIconic(target_hwnd))
            rect_t = win32gui.GetWindowRect(target_hwnd)
            rect = {
                "x": rect_t[0], "y": rect_t[1],
                "w": rect_t[2] - rect_t[0], "h": rect_t[3] - rect_t[1],
            }
            off_screen = (rect["x"] < -1000 or rect["y"] < -1000)
            try:
                _, pid = win32process.GetWindowThreadProcessId(target_hwnd)
            except Exception:
                pid = 0
            # Suggestion logic
            if minimized:
                suggestion = "restore"
            elif off_screen:
                suggestion = "restore"
            elif not visible:
                suggestion = "restore"
            else:
                suggestion = "ok"
            # Note: integrity_level_hint requires win32security; left as null hint for now
            return {
                "ok": True,
                "hwnd": target_hwnd,
                "title": title[:200],
                "pid": pid,
                "rect": rect,
                "visible": visible and not minimized and not off_screen,
                "minimized": minimized,
                "off_screen": off_screen,
                "is_window_visible_raw": visible,
                "suggestion": suggestion,
                "ca_next_action": (
                    "call_desktop_app_focus_to_restore" if suggestion == "restore"
                    else "call_desktop_app_focus_to_foreground" if suggestion == "focus"
                    else "safe_to_click_or_fill"
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "error_name": "WIN32_API_ERROR"}

    data = await asyncio.to_thread(_check)
    if data.get("ok"):
        return _ok(data)
    return _err(data.get("error", data.get("error_name", "check failed")),
                error_name=data.get("error_name", "WIN32_API_ERROR"))


# V15 R12 Round B — CDP-first desktop driver for Electron/Chromium
# Win32 message-level input is silently dropped by Chromium engine. Use CDP
# Input.dispatchMouseEvent on remote-debugging port for Electron apps. Fallback to
# Win32 SendInput + AttachThreadInput + ChildWindowFromPoint for non-Chromium
# native windows.

_DEFAULT_CDP_PORT = 9223


def _match_text(value: str, pattern: str, match_mode: str = "contains_case_insensitive") -> bool:
    import re
    value = value or ""
    pattern = pattern or ""
    mode = (match_mode or "contains_case_insensitive").lower().strip()
    if not pattern:
        return True
    if mode in {"case_insensitive", "contains_case_insensitive", "contains_ci", "ci"}:
        return pattern.lower() in value.lower()
    if mode == "contains":
        return pattern in value
    if mode == "exact":
        return value == pattern
    if mode == "regex":
        try:
            return re.search(pattern, value) is not None
        except re.error:
            return False
    return pattern.lower() in value.lower()


async def _cdp_pick_target(port: int, prefer_title_substr: str = "",
                           match_mode: str = "contains_case_insensitive",
                           expected_url: str = "",
                           wait_for_navigation: bool = False,
                           wait_timeout_ms: int = 5000):
    """Returns (target_dict or None, error_str or None)."""
    import time as _time
    import urllib.request as _ur

    first_candidate = None
    last_error = ""
    deadline = _time.time() + (max(wait_timeout_ms, 0) / 1000.0 if wait_for_navigation else 0)
    while True:
        try:
            with _ur.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as resp:
                targets = json.loads(resp.read())
        except Exception as exc:
            return None, f"CDP unreachable on port {port}: {exc}"
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            return None, "no CDP page targets"
        if prefer_title_substr:
            match = next((t for t in pages if _match_text(t.get("title") or "", prefer_title_substr, match_mode)), None)
            if not match:
                last_error = f"CDP target title not found: {prefer_title_substr}"
        else:
            match = pages[0]
        if match and first_candidate is None:
            first_candidate = dict(match)
        if match:
            url = match.get("url") or ""
            if not expected_url or _match_text(url, expected_url, "contains_case_insensitive"):
                match = dict(match)
                match["initial_url"] = (first_candidate or match).get("url") or ""
                match["final_url"] = url
                match["matched_title"] = match.get("title") or ""
                return match, None
            last_error = f"CDP target URL not ready: expected {expected_url}, actual {url}"
        if not wait_for_navigation or _time.time() >= deadline:
            return None, last_error or "CDP target not found"
        await asyncio.sleep(0.2)


async def list_cdp_targets(cdp_port: int = _DEFAULT_CDP_PORT) -> str:
    """T4 read-only: list Chromium DevTools Protocol targets through [A]."""
    import urllib.request as _ur

    try:
        with _ur.urlopen(f"http://127.0.0.1:{int(cdp_port)}/json/list", timeout=3) as resp:
            targets = json.loads(resp.read())
        compact = []
        for target in targets:
            compact.append({
                "title": (target.get("title") or "")[:200],
                "url": (target.get("url") or "")[:500],
                "type": target.get("type") or "",
                "id": target.get("id") or "",
                "webSocketDebuggerUrl": target.get("webSocketDebuggerUrl") or "",
            })
        return _ok({
            "cdp_port": int(cdp_port),
            "target_count": len(compact),
            "targets": compact,
        })
    except Exception as exc:
        return _err(f"CDP target list unavailable on port {cdp_port}: {exc}",
                    error_name="CDP_UNREACHABLE",
                    cdp_port=cdp_port,
                    diagnostic_hint=(
                        "R28: distinguish runtime_absent vs ui_inaccessible. "
                        "Call desktop_window_list — if NO matching app window → runtime_absent "
                        "(call launch_electron_with_cdp). If window exists → ui_inaccessible "
                        "(wrong port or CDP flag missing)."
                    ),
                    ca_next_action="check_desktop_window_list_to_distinguish_runtime_absent_vs_inaccessible")


async def _cdp_dispatch(port: int, target_id: str, method: str, params: dict):
    """Open a single-call CDP session, send one method, close. Returns (response, error)."""
    # Use websockets via stdlib? Python stdlib has no WS client; need raw socket impl
    # or external lib. Easiest: use a tiny WS handshake + frame writer/reader.
    import base64 as _b64
    import os as _os
    import socket
    import struct as _st
    import urllib.parse as _up

    # find ws URL for target
    import urllib.request as _ur
    try:
        with _ur.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
            tgts = json.loads(r.read())
    except Exception as exc:
        return None, f"CDP /json fetch failed: {exc}"
    tgt = next((t for t in tgts if t.get("id") == target_id), None)
    if not tgt:
        return None, f"CDP target_id {target_id} not found"
    ws_url = tgt.get("webSocketDebuggerUrl") or ""
    if not ws_url.startswith("ws://"):
        return None, f"invalid ws_url: {ws_url}"
    p = _up.urlparse(ws_url)
    host, ws_port, path = p.hostname, p.port or 80, (p.path or "/") + (("?" + p.query) if p.query else "")
    key = _b64.b64encode(_os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{ws_port}\r\nUpgrade: websocket\r\n"
           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    def _do() -> tuple:
        s = socket.create_connection((host, ws_port), timeout=5)
        try:
            s.sendall(req.encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    return None, "WS handshake EOF"
                buf += chunk
            # Now write 1 text frame (masked) with the CDP command
            payload = json.dumps({"id": 1, "method": method, "params": params}).encode()
            mask = _os.urandom(4)
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            # FIN=1, opcode=text(0x1)
            frame_head = bytes([0x81])
            if len(payload) < 126:
                frame_head += bytes([0x80 | len(payload)])
            elif len(payload) < 65536:
                frame_head += bytes([0x80 | 126]) + _st.pack(">H", len(payload))
            else:
                frame_head += bytes([0x80 | 127]) + _st.pack(">Q", len(payload))
            s.sendall(frame_head + mask + masked)
            # Read 1 response frame
            s.settimeout(5)
            hdr = s.recv(2)
            if len(hdr) < 2:
                return None, "WS frame EOF"
            length = hdr[1] & 0x7F
            if length == 126:
                length = _st.unpack(">H", s.recv(2))[0]
            elif length == 127:
                length = _st.unpack(">Q", s.recv(8))[0]
            data = b""
            while len(data) < length:
                chunk = s.recv(min(8192, length - len(data)))
                if not chunk:
                    break
                data += chunk
            return json.loads(data.decode("utf-8", errors="replace")), None
        finally:
            try:
                s.close()
            except OSError as exc:
                logging.getLogger(__name__).debug("socket close failed: %s", exc)
    try:
        result, err = await asyncio.wait_for(asyncio.to_thread(_do), timeout=8.0)
        return result, err
    except asyncio.TimeoutError:
        return None, "CDP dispatch timeout"


async def restart_orchestrator_via_task(delay_seconds: int = 2) -> str:
    """V15 R13.2 — Restart [A] via Windows Task Scheduler (High IL, no UAC).

    Self-restart problem: the tool runs INSIDE the orchestrator process; if it
    kills its own server first, the schtasks /run step never executes and the
    HTTP response never returns to caller. Fix: spawn a DETACHED helper .cmd
    that waits, kills the old PID, then triggers schtasks /run. Tool returns
    immediately with `status=restart_queued`; caller polls /health separately.

    Requires `install_orchestrator_admin_task.ps1` to have been run once with
    admin to register the 'ToolA_Orchestrator' task (RunLevel=Highest).

    Args:
        delay_seconds: how long the helper waits before killing the orchestrator
            (gives this HTTP response time to flush). Clamped to [1, 10].

    Returns:
        {ok:true, status:"restart_queued", poll_health_after_seconds: N,
         expected_health_url: "...", helper_path: "..."} on success.
        Or {ok:false, error_name:"INSTALL_TASK_REQUIRED"|...} on setup error.
    """
    import json as _json
    import subprocess as _sp
    from pathlib import Path as _Path
    task_name = "ToolA_Orchestrator"
    delay = max(1, min(int(delay_seconds), 10))

    # Step 1: verify task exists (cheap, doesn't kill anything)
    try:
        q = _sp.run(["schtasks", "/query", "/tn", task_name],
                    capture_output=True, text=True, timeout=10)
        if q.returncode != 0:
            return _json.dumps({
                "ok": False,
                "error_name": "INSTALL_TASK_REQUIRED",
                "ca_next_action": "ask_user_run_install_script_with_admin_once",
                "remediation": "Right-click install_orchestrator_admin_task.ps1 > Run with PowerShell (admin). One-time setup; afterwards this tool works without UAC.",
                "task_name": task_name,
                "schtasks_stderr": q.stderr[:200] if q.stderr else "",
            }, ensure_ascii=False)
    except Exception as exc:
        return _json.dumps({"ok": False, "error_name": "SCHTASKS_UNAVAILABLE", "detail": str(exc)}, ensure_ascii=False)

    # Step 2: write helper .cmd that runs detached, AFTER this response returns
    try:
        cache_dir = _Path(__file__).parent / ".orchestrator_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        helper_path = cache_dir / "restart_helper.cmd"
        log_path = cache_dir / "restart_helper.log"
        # ping -n produces ~1s delays without needing console; portable on Win
        # Use ping_count = delay + 1 because first ping returns immediately
        helper_content = (
            "@echo off\r\n"
            "REM V15 R13.2 — detached restart helper. Auto-generated.\r\n"
            f"echo [%date% %time%] helper start delay={delay}s >> \"{log_path}\"\r\n"
            f"ping 127.0.0.1 -n {delay + 1} >nul\r\n"
            f"echo [%date% %time%] schtasks /end (cross-elevation safe) >> \"{log_path}\"\r\n"
            f"schtasks /end /tn \"{task_name}\" >> \"{log_path}\" 2>&1\r\n"
            "echo [%date% %time%] fallback Stop-Process on port 8083 >> "
            f"\"{log_path}\"\r\n"
            "powershell -NoProfile -Command "
            "\"Get-NetTCPConnection -LocalPort 8083 -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty OwningProcess -Unique | "
            "ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }\"\r\n"
            "ping 127.0.0.1 -n 3 >nul\r\n"
            f"echo [%date% %time%] schtasks /run /tn {task_name} >> \"{log_path}\"\r\n"
            f"schtasks /run /tn \"{task_name}\" >> \"{log_path}\" 2>&1\r\n"
            f"echo [%date% %time%] helper done rc=%ERRORLEVEL% >> \"{log_path}\"\r\n"
            "exit /b 0\r\n"
        )
        helper_path.write_text(helper_content, encoding="utf-8")
    except Exception as exc:
        return _json.dumps({"ok": False, "error_name": "HELPER_WRITE_FAILED", "detail": str(exc)}, ensure_ascii=False)

    # Step 3: spawn helper DETACHED so it survives our death
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        # Popen with these flags + close_fds so the child has no inherited handles to us
        _sp.Popen(
            ["cmd", "/c", str(helper_path)],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
            stdin=_sp.DEVNULL,
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
            close_fds=True,
            cwd=str(cache_dir),
        )
    except Exception as exc:
        return _json.dumps({"ok": False, "error_name": "HELPER_SPAWN_FAILED", "detail": str(exc)}, ensure_ascii=False)

    return _json.dumps({
        "ok": True,
        "status": "restart_queued",
        "task_name": task_name,
        "helper_path": str(helper_path),
        "helper_log": str(log_path),
        "delay_before_kill_seconds": delay,
        "poll_health_after_seconds": delay + 6,
        "expected_health_url": "http://localhost:8083/health",
        "integrity_level_after": "High (via Task Scheduler RunLevel=Highest)",
        "uipi_note": "After health returns ok, desktop_click/desktop_app_focus can drive admin-context windows.",
        "ca_next_action": "wait_delay_then_poll_health_until_ok",
    }, ensure_ascii=False)


_FORCE_DRAIN_DISPATCH_STALE_SECONDS = 600.0


def _force_drain_event_ts(event) -> float | None:
    """Parse the broker event `ts` field back to epoch seconds.

    Broker events stamp `ts` as local-time ISO via _council_now_iso()
    ("%Y-%m-%dT%H:%M:%S"). Legacy events may carry `time.time()` floats.
    Returns None when unparseable OR NON-FINITE (nan / inf) — callers must
    treat None as NOT stale (fail-closed). Non-finite values cannot be
    compared meaningfully against `now`, so they must never be accepted as
    evidence that a dispatch is provably stale.
    """
    import datetime as _dt
    import math as _math
    import time as _time

    def _finite(value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if _math.isnan(v) or _math.isinf(v):
            return None
        return v

    raw = event.get("ts")
    if isinstance(raw, bool):
        # bool is a subclass of int but is never a valid epoch timestamp.
        return None
    if isinstance(raw, (int, float)):
        return _finite(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                parsed = _dt.datetime.strptime(text, fmt)
                return _finite(_time.mktime(parsed.timetuple()))
            except ValueError:
                continue
        return _finite(text)
    return None


def _force_drain_dispatch_is_stale(event, now: float, *, stale_after_seconds: float, pid_alive) -> bool:
    """Return True only when an unmatched dispatch is PROVABLY stale.

    Conservative by default: returns False (preserve) unless ALL of:
      1. Age bound: the dispatch ts is parseable, finite, AND older than
         stale_after_seconds.
      2. No live owner: every PID-bearing field present on the event parses
         as a positive int AND pid_alive() confirms the process is dead.
    Missing/invalid ts preserves the dispatch (fail-closed to avoid terminalizing
    fresh work). PID evidence that cannot be parsed as a positive int makes the
    dispatch UNVERIFIED — preserve it as well; we cannot prove the worker is
    gone. Only absent/empty OR valid-int-and-confirmed-dead counts toward
    staleness.
    """
    ts = _force_drain_event_ts(event)
    if ts is None:
        return False
    if ts > now or (now - ts) < stale_after_seconds:
        return False
    for key in ("worker_pid", "lease_pid", "owner_pid"):
        pid = event.get(key)
        if pid in (None, ""):
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            # Invalid PID-shaped evidence: cannot prove the owner is dead.
            # Treat as UNVERIFIED → preserve the dispatch (fail-closed).
            return False
        if pid_int <= 0:
            # Non-positive PID is not a real process owner either.
            return False
        try:
            if pid_alive(pid_int):
                return False
        except (TypeError, ValueError, OSError):
            # pid_alive itself rejected the value — treat as unverifiable,
            # fail-closed.
            return False
    return True


def _force_drain_restart_state(root, reason: str, *, now: float | None = None,
                              stale_after_seconds: float = _FORCE_DRAIN_DISPATCH_STALE_SECONDS) -> dict:
    """Close stale council slots and unmatched broker dispatches, never live workers.

    Transactional order: PARSE both files first. Only after both validate do we
    mutate. Council state is rewritten atomically (tmp file + os.replace).
    """
    import json as _json
    import os as _os
    import time as _time
    try:
        import psutil as _psutil
        pid_alive = _psutil.pid_exists
    except Exception:  # pragma: no cover - psutil is part of the runtime
        def pid_alive(_pid):  # type: ignore[return-value]
            return False
    if now is None:
        now = _time.time()

    council = root / ".council"
    runtime_path = council / "runtime_state.json"
    broker_path = council / "broker_filtered.jsonl"

    # ---- Phase 1: PARSE council state (no mutation yet). ----
    state = None
    rows = []
    pending_council_mutations = []
    closed_slots = []
    if runtime_path.exists():
        try:
            state = _json.loads(runtime_path.read_text(encoding="utf-8"))
        except (_json.JSONDecodeError, OSError, UnicodeError) as exc:
            raise RuntimeError("FORCE_DRAIN_INVALID_COUNCIL_STATE") from exc
        rows = list(state.values()) if isinstance(state, dict) else state
        if not isinstance(rows, list):
            raise RuntimeError("FORCE_DRAIN_INVALID_COUNCIL_STATE")
        for row in rows:
            if not isinstance(row, dict) or str(row.get("state", row.get("status", ""))).lower() not in {"running", "starting", "queued"}:
                continue
            pid = row.get("worker_pid") or row.get("pid")
            if pid:
                try:
                    if pid_alive(int(pid)):
                        raise RuntimeError("FORCE_DRAIN_LIVE_COUNCIL_WORKER")
                except (TypeError, ValueError, OSError):
                    pass
            closed_id = row.get("id") or pid or "unknown"
            pending_council_mutations.append((row, closed_id))

    # ---- Phase 2: PARSE broker state (no mutation yet). ----
    # Any parse failure aborts BEFORE any mutation is applied.
    broker_lines = []
    if broker_path.exists():
        try:
            # One record per "\n" -- the writer's contract (ensure_ascii=False keeps a raw
            # U+2028 inside a record, which str.splitlines() would cut in two).
            broker_lines = split_lines(broker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise RuntimeError("FORCE_DRAIN_BROKER_UNREADABLE") from exc
    dispatches, replies = {}, set()
    for line in broker_lines:
        if not line.strip():
            continue
        try:
            event = _json.loads(line)
        except (_json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("FORCE_DRAIN_BROKER_PARSE_FAILED") from exc
        if not isinstance(event, dict):
            raise RuntimeError("FORCE_DRAIN_BROKER_PARSE_FAILED")
        if event.get("type") in {"message", "dispatch"} and event.get("id"):
            dispatches[event["id"]] = event
        elif event.get("type") == "reply" and event.get("in_reply_to"):
            replies.add(event["in_reply_to"])
    unmatched_ids = set(dispatches) - replies

    # ---- Phase 3: decide which unmatched dispatches are PROVABLY stale. ----
    closed_dispatches = sorted(
        dispatch_id for dispatch_id in unmatched_ids
        if _force_drain_dispatch_is_stale(
            dispatches[dispatch_id], now,
            stale_after_seconds=stale_after_seconds,
            pid_alive=pid_alive,
        )
    )

    # ---- Phase 4: transactional two-file commit. ----
    # Build the post-mutation bytes for BOTH files in memory first. Then write
    # both to sibling temp files. Only after both temp writes succeed do we
    # os.replace them into place. If anything fails before BOTH replaces are
    # done, restore the council file from its pre-mutation bytes so the pair
    # never ends up half-committed (council mutated while broker append failed).
    council_pre_bytes = runtime_path.read_bytes() if runtime_path.exists() else None
    broker_pre_bytes = broker_path.read_bytes() if broker_path.exists() else None

    new_council_bytes: bytes | None = None
    if pending_council_mutations and state is not None:
        for row, closed_id in pending_council_mutations:
            row["state"] = row["status"] = "stopped"
            row["force_drain_reason"] = reason
            row["force_drained_at"] = now
            closed_slots.append(closed_id)
        new_council_bytes = _json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")

    new_broker_bytes: bytes | None = None
    if closed_dispatches:
        appended_lines = []
        for dispatch_id in closed_dispatches:
            event = {"type": "reply", "id": f"force-drain-{dispatch_id}",
                     "in_reply_to": dispatch_id, "ok": False,
                     "error": "force-drained stale dispatch", "force_drain_reason": reason,
                     "ts": now}
            appended_lines.append(_json.dumps(event, separators=(",", ":")))
        appended_blob = ("\n".join(appended_lines) + "\n").encode("utf-8")
        prefix = broker_pre_bytes or b""
        # Preserve line-record semantics: if the prior file does not end with
        # a newline, terminate the last existing line before appending replies
        # so each reply remains parseable as its own JSONL record.
        if prefix and not prefix.endswith(b"\n"):
            prefix = prefix + b"\n"
        new_broker_bytes = prefix + appended_blob

    # Stage both files. If either temp write fails, roll back any council tmp
    # we created and abort without touching either live file.
    council_tmp = runtime_path.with_name(runtime_path.name + ".force-drain.tmp") if new_council_bytes is not None else None
    broker_tmp = broker_path.with_name(broker_path.name + ".force-drain.tmp") if new_broker_bytes is not None else None
    try:
        if council_tmp is not None:
            council_tmp.write_bytes(new_council_bytes)
        if broker_tmp is not None:
            broker_tmp.write_bytes(new_broker_bytes)
        # Both staged successfully — commit both atomically.
        if council_tmp is not None:
            _os.replace(council_tmp, runtime_path)
        if broker_tmp is not None:
            _os.replace(broker_tmp, broker_path)
    except Exception:
        # Best-effort rollback: if the council live file was already replaced
        # but the broker replace has not yet completed, restore the council
        # file from its pre-mutation bytes so the pair stays consistent.
        try:
            if council_tmp is not None and council_tmp.exists():
                council_tmp.unlink()
        except OSError:
            pass
        try:
            if broker_tmp is not None and broker_tmp.exists():
                broker_tmp.unlink()
        except OSError:
            pass
        try:
            if new_council_bytes is not None and council_pre_bytes is not None:
                runtime_path.write_bytes(council_pre_bytes)
            if new_broker_bytes is not None and broker_pre_bytes is not None:
                broker_path.write_bytes(broker_pre_bytes)
        except OSError:
            pass
        raise
    return {"closed_slots": closed_slots, "closed_dispatches": closed_dispatches, "reason": reason}


async def restart_orchestrator_via_bat(
    delay_seconds: int = 2,
    expected_generation: str = "",
    controller_url: str = "http://127.0.0.1:8085/restart",
    request_id: str = "",
    force_drain: bool = False,
    force_drain_reason: str = "",
) -> str:
    """Restart [A] via the canonical [A:S] script (start_orchestrator.bat) only.

    This tool never POSTs :8085. The bat owns Path A (CLI 8085), Path B
    (ToolA_ForceRestart), Path C (/end + /run), and Path D (HTTP loop guard).
    Pre-bat guard runs when expected_generation is set or council files exist
    so empty temp dirs used by helper-contract tests still spawn the bat.

    Args:
        delay_seconds: helper wait before invoking [A:S] (response flush window).
            Clamped to [1, 10].
        controller_url: unused compatibility argument; bat Path A talks to :8085.
    """
    import json as _json
    import subprocess as _sp
    from pathlib import Path as _Path

    delay = max(1, min(int(delay_seconds), 10))
    root = _Path(__file__).parent
    from orchestrator.lifecycle.restart_protocol import (
        canonical_secret_path,
        migrate_legacy_secret,
    )
    secret_path = canonical_secret_path(root)
    _migrated = await asyncio.to_thread(migrate_legacy_secret, root)
    if _migrated is not None:
        secret_path = _migrated
    del secret_path, controller_url
    force_result = None
    if force_drain:
        if not force_drain_reason.strip():
            return _json.dumps({"ok": False, "error_name": "FORCE_DRAIN_REASON_REQUIRED"})
        try:
            force_result = _force_drain_restart_state(root, force_drain_reason.strip())
        except RuntimeError as exc:
            return _json.dumps({"ok": False, "error_name": str(exc)})
        except Exception as exc:
            return _json.dumps({"ok": False, "error_name": "FORCE_DRAIN_FAILED", "detail": type(exc).__name__})

    council_dir = root / ".council"
    council_present = (
        (council_dir / "runtime_state.json").exists()
        or (council_dir / "broker_filtered.jsonl").exists()
    )
    if expected_generation or council_present:
        try:
            from orchestrator.lifecycle.restart_control_service import build_guard
            guard = build_guard(root, expected_generation or "", request_id, max_age=3600.0)
            decision = await asyncio.to_thread(guard)
        except Exception as exc:
            return _json.dumps({
                "ok": False,
                "error_name": "RESTART_GUARD_UNAVAILABLE",
                "detail": type(exc).__name__,
            }, ensure_ascii=False)
        if not decision.allowed:
            from orchestrator.lifecycle.restart_guard import is_retryable_stale_block
            # A39: a block whose ONLY reason is a stale busy-snapshot *reading*
            # (nothing actually busy) is a transient false alarm — the freshest
            # snapshot is trustworthy on a retry. Tell the CA the truth: call
            # again. The old contract said "reload guard state after clearing the
            # condition" for a condition that did not exist.
            retryable = is_retryable_stale_block(decision.reasons)
            response = {
                "ok": False,
                "error_name": "RESTART_GUARD_BLOCKED",
                "reasons": list(decision.reasons),
                "blocking_guards": [dict(g) for g in decision.blocking_guards],
                "mechanism": "pre_bat_guard",
                "retryable": retryable,
            }
            if retryable:
                response["ca_next_action"] = "retry_restart_call"
            return _json.dumps(response, ensure_ascii=False)

    bat_path = root / "start_orchestrator.bat"
    if not bat_path.exists():
        return _json.dumps({
            "ok": False,
            "error_name": "A_S_BAT_NOT_FOUND",
            "expected_path": str(bat_path),
            "ca_next_action": "verify [A:S] path: [A:P]\\project-orchestrator\\start_orchestrator.bat",
        }, ensure_ascii=False)

    try:
        cache_dir = root / ".orchestrator_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        log_path = cache_dir / "restart_bat_helper.log"
        helper_path = root / "orchestrator" / "lifecycle" / "via_bat_helper.py"
    except Exception as exc:
        return _json.dumps({"ok": False, "error_name": "HELPER_WRITE_FAILED", "detail": str(exc)}, ensure_ascii=False)

    try:
        # Helper is hidden (CREATE_NO_WINDOW fallback inside via_bat_helper).
        # Spawn console-subsystem python.exe so the helper can AllocConsole
        # and hide it; children inherit that one console. Do not OR
        # DETACHED_PROCESS with CREATE_NO_WINDOW (Win32 rejects that combination).
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        STARTF_USESHOWWINDOW = 0x00000001
        import sys as _sys
        py = root / "venv312" / "Scripts" / "python.exe"
        if not py.is_file():
            sibling = _Path(_sys.executable).with_name("python.exe")
            py = sibling if sibling.is_file() else _Path(_sys.executable)
        startupinfo = None
        if _sys.platform == "win32":
            startupinfo = _sp.STARTUPINFO()
            startupinfo.dwFlags |= STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        _sp.Popen(
            [
                str(py),
                "-m",
                "orchestrator.lifecycle.via_bat_helper",
                "--delay",
                str(delay),
                "--bat",
                str(bat_path),
                "--log",
                str(log_path),
                "--root",
                str(root),
            ],
            creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
            startupinfo=startupinfo,
            stdin=_sp.DEVNULL,
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
            close_fds=True,
            cwd=str(root),
        )
    except Exception as exc:
        return _json.dumps({"ok": False, "error_name": "HELPER_SPAWN_FAILED", "detail": str(exc)}, ensure_ascii=False)

    payload = {
        "ok": True,
        "status": "restart_queued",
        "mechanism": "[A:S] start_orchestrator.bat (hidden helper)",
        "always_bat": True,
        "controller_fallback": False,
        "helper_path": str(helper_path),
        "helper_log": str(log_path),
        "delay_before_restart_seconds": delay,
        "poll_health_after_seconds": delay + 8,
        "expected_health_url": "http://localhost:8083/health",
        "ca_next_action": "wait_delay_then_poll_health_until_ok_then_verify_pid_changed",
    }
    if force_result is not None:
        payload["force_drain"] = force_result
    return _json.dumps(payload, ensure_ascii=False)




async def cancel_pending_restart() -> str:
    """V15 R14 — Cancel a pending restart helper.

    `restart_orchestrator_via_task` used to spawn `restart_helper.cmd`.
    `restart_orchestrator_via_bat` now queues `via_bat_helper` (pythonw).
    If the User changes mind during the delay window, this tool kills any
    process whose command-line references those helper markers.

    Returns:
        {ok:true, killed:[...PIDs], helper_path:..., note:...}
        {ok:false, error_name:"NO_PENDING_RESTART"} when nothing matches.
    """
    import json as _json
    from pathlib import Path as _Path
    root = _Path(__file__).parent
    helper_path = root / "orchestrator" / "lifecycle" / "via_bat_helper.py"
    markers = ("restart_helper.cmd", "restart_bat_helper.cmd", "via_bat_helper")

    killed = []
    try:
        import psutil
    except Exception as exc:
        return _json.dumps({
            "ok": False,
            "error_name": "PS_KILL_FAILED",
            "detail": str(exc),
        }, ensure_ascii=False)

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(str(part) for part in (proc.info.get("cmdline") or [])).lower()
        except Exception:
            continue
        if not any(marker in cmd for marker in markers):
            continue
        try:
            pid = int(proc.info.get("pid") or 0)
            if pid <= 0:
                continue
            psutil.Process(pid).kill()
            killed.append(pid)
        except (psutil.Error, OSError, ValueError):
            continue

    if not killed:
        return _json.dumps({
            "ok": True,
            "killed": [],
            "helper_path": str(helper_path),
            "note": "No active helper process found. Either the restart already completed, or no restart was queued.",
        }, ensure_ascii=False)

    return _json.dumps({
        "ok": True,
        "killed": killed,
        "killed_count": len(killed),
        "helper_path": str(helper_path),
        "note": "Pending restart helper killed. Orchestrator stays running on current PID.",
        "ca_next_action": "no_action_needed",
    }, ensure_ascii=False)


async def desktop_app_focus(hwnd: int = 0, title_pattern: str = "",
                              cdp_port: int = _DEFAULT_CDP_PORT,
                              match_mode: str = "contains_case_insensitive",
                              fallback_to_win32: bool = True,
                              expected_url: str = "",
                              wait_for_navigation: bool = False,
                              project_path: str = "") -> str:
    """V15 R12 Round B: CDP-first focus for Electron/Chromium; Win32 fallback for native.

    Strategy:
      1. If cdp_port is reachable and a page target matches title_pattern → Page.bringToFront
      2. Otherwise resolve hwnd (by title_pattern via desktop_window_list) →
         AttachThreadInput + SetForegroundWindow
    """
    cdp_attempted = True
    root_causes = []
    # Try CDP first
    target, err = await _cdp_pick_target(
        cdp_port,
        prefer_title_substr=title_pattern or "",
        match_mode=match_mode,
        expected_url=expected_url,
        wait_for_navigation=wait_for_navigation,
    )
    if target:
        result, derr = await _cdp_dispatch(cdp_port, target["id"], "Page.bringToFront", {})
        if result and not result.get("error"):
            return _ok({
                "method": "cdp",
                "cdp_port": cdp_port,
                "target_id": target["id"],
                "target_url": (target.get("url") or "")[:200],
                "initial_url": (target.get("initial_url") or target.get("url") or "")[:200],
                "final_url": (target.get("final_url") or target.get("url") or "")[:200],
                "matched_title": (target.get("matched_title") or target.get("title") or "")[:200],
                "match_mode": match_mode,
                "bring_to_front_sent": True,
                "dispatch_results": [{"method": "Page.bringToFront", "result": result}],
                "focused": True,
            })
        root_causes.append("CDP_BRING_TO_FRONT_FAILED")
        err = derr or json.dumps((result or {}).get("error", result), ensure_ascii=False)
    else:
        root_causes.append("CDP_UNREACHABLE" if err and "unreachable" in err.lower() else "CDP_TARGET_NOT_FOUND")
        # CDP available but bringToFront failed → fall through to Win32
    if not fallback_to_win32:
        return _err("desktop interaction failed and Win32 fallback is disabled",
                    error_name="DESKTOP_INTERACTION_FAILED",
                    root_causes=root_causes,
                    fallback_attempted=False,
                    cdp_attempted=cdp_attempted,
                    cdp_error=err,
                    match_mode=match_mode,
                    expected_url=expected_url)
    # Win32 fallback
    if not _pywin32_available():
        return _err("desktop interaction failed: pywin32 missing",
                    error_name="DESKTOP_INTERACTION_FAILED",
                    root_causes=[*root_causes, "PYWIN32_MISSING"],
                    fallback_attempted=False,
                    cdp_attempted=cdp_attempted,
                    cdp_error=err,
                    remediation="pip install pywin32")
    import win32api
    import win32con
    import win32gui
    import win32process
    target_hwnd = hwnd
    resolved_from = "hwnd" if hwnd else ""
    window_match_count = 0
    if not target_hwnd and title_pattern:
        raw = await desktop_window_list(title_filter=title_pattern)
        wins = [
            w for w in json.loads(raw).get("windows", [])
            if _match_text(w.get("title") or "", title_pattern, match_mode)
        ]
        window_match_count = len(wins)
        if wins:
            target_hwnd = wins[0]["hwnd"]
            resolved_from = "title_pattern"
        else:
            return _err(
                f"No desktop window matched title_pattern: {title_pattern}",
                error_name="WINDOW_NOT_FOUND",
                title_pattern=title_pattern,
                matched_title="",
                match_mode=match_mode,
                window_match_count=0,
                cdp_attempted=cdp_attempted,
                cdp_error=err,
            )
    if not target_hwnd:
        return _err("hwnd or title_pattern required", error_name="MISSING_REQUIRED_FIELD")

    def _focus() -> dict:
        try:
            # AttachThreadInput to bypass SetForegroundWindow restrictions
            current_tid = win32api.GetCurrentThreadId()
            target_tid, _ = win32process.GetWindowThreadProcessId(target_hwnd)
            attached = False
            attach_attempted = bool(target_tid and target_tid != current_tid)
            if target_tid and target_tid != current_tid:
                try:
                    win32process.AttachThreadInput(current_tid, target_tid, True)
                    attached = True
                except Exception:
                    pass
            try:
                # V15 R15 P2.5/7: pre-check + always restore if minimized OR off-screen.
                # Off-screen detection: Windows uses (-32000, -32000) for minimized apps
                # or extremely negative coords for hidden windows.
                rect_before_tuple = win32gui.GetWindowRect(target_hwnd)
                rect_before = {
                    "x": rect_before_tuple[0], "y": rect_before_tuple[1],
                    "w": rect_before_tuple[2] - rect_before_tuple[0],
                    "h": rect_before_tuple[3] - rect_before_tuple[1],
                }
                was_minimized = bool(win32gui.IsIconic(target_hwnd))
                was_off_screen = (rect_before["x"] < -1000 or rect_before["y"] < -1000)
                if was_minimized or was_off_screen:
                    win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
                    # SWP_NOSIZE + SWP_SHOWWINDOW: bring to top without resizing, ensure shown
                    SWP_NOSIZE = 0x0001
                    SWP_SHOWWINDOW = 0x0040
                    HWND_TOP = 0
                    try:
                        win32gui.SetWindowPos(target_hwnd, HWND_TOP, 0, 0, 0, 0,
                                              SWP_NOSIZE | SWP_SHOWWINDOW)
                    except Exception:
                        pass
                win32gui.SetForegroundWindow(target_hwnd)
                win32gui.BringWindowToTop(target_hwnd)
                # Post-action verify
                rect_after_tuple = win32gui.GetWindowRect(target_hwnd)
                rect_after = {
                    "x": rect_after_tuple[0], "y": rect_after_tuple[1],
                    "w": rect_after_tuple[2] - rect_after_tuple[0],
                    "h": rect_after_tuple[3] - rect_after_tuple[1],
                }
                still_off_screen = (rect_after["x"] < -1000 or rect_after["y"] < -1000)
                if still_off_screen:
                    visibility_state = "off_screen"
                    uipi_warning = ("Window stayed off-screen post-restore; "
                                    "integrity level mismatch likely. restart_orchestrator_via_bat "
                                    "relaunches [A] at High IL (needs Anh's go-ahead).")
                elif was_minimized or was_off_screen:
                    visibility_state = "restored"
                    uipi_warning = None
                else:
                    visibility_state = "visible"
                    uipi_warning = None
                resp = {
                    "ok": True,
                    "method": "win32_attach",
                    "hwnd": target_hwnd,
                    "resolved_from": resolved_from or "unknown",
                    "title_pattern": title_pattern,
                    "matched_title": "",
                    "match_mode": match_mode,
                    "window_match_count": window_match_count,
                    "attach_thread_input": {
                        "attempted": attach_attempted,
                        "attached": attached,
                        "current_tid": current_tid,
                        "target_tid": target_tid,
                    },
                    "focused": True,
                    "rect_before": rect_before,
                    "rect_after": rect_after,
                    "visibility_state": visibility_state,
                    "was_minimized": was_minimized,
                    "was_off_screen": was_off_screen,
                }
                if uipi_warning:
                    resp["uipi_warning"] = uipi_warning
                return resp
            finally:
                if attached:
                    _safe_detach(current_tid, target_tid)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "error_name": "WIN32_API_ERROR"}

    data = await asyncio.to_thread(_focus)
    if data.get("ok"):
        return _ok(data)
    return _err(data.get("error", "focus failed"),
                error_name="DESKTOP_INTERACTION_FAILED",
                root_causes=[*root_causes, data.get("error_name", "WIN32_API_ERROR")],
                fallback_attempted=True,
                cdp_attempted=cdp_attempted,
                cdp_fallback_note=err or "CDP attempted but failed",
                hwnd=target_hwnd,
                resolved_from=resolved_from or "unknown",
                title_pattern=title_pattern,
                matched_title="",
                match_mode=match_mode,
                window_match_count=window_match_count,
                # R83: focus is BEST-EFFORT — Win32 SetForegroundWindow is blocked by UIPI for
                # many windows (Adobe Premiere, elevated apps) and there is no CDP for them.
                # CA was retrying this in a loop. Make the non-fatal contract explicit so it does NOT.
                non_fatal=True,
                ca_next_action="do_not_retry_focus",
                hint=("Window focus FAILED but this is NON-FATAL and you should NOT retry it. "
                      "If your flow drives the app via the file-bridge or a CLI (ADE cut, desktop-backend, "
                      "compare-cli) the window does NOT need focus — PROCEED. For a merely VISIBLE window use "
                      "desktop_window_state(state='maximize', title_pattern=...) which works under UIPI. "
                      "Only Electron apps launched with --remote-debugging-port=9223 can be truly focused via CDP."))


async def desktop_send_text(text: str,
                            hwnd: int = 0,
                            title_pattern: str = "",
                            match_mode: str = "contains_case_insensitive",
                            submit: bool = True,
                            method: str = "clipboard",
                            pre_focus_delay_ms: int = 250,
                            require_focus: bool = True,
                            click_focus: bool = False,
                            click_x: int = 0,
                            click_y: int = 0,
                            project_path: str = "") -> str:
    """V15 R92 T4d: type/paste text into a desktop window (council file-bridge poke).

    Focuses the target window (Win32 AttachThreadInput + SetForegroundWindow, restores if
    minimized), then delivers `text` via clipboard paste (Ctrl+V) — reliable for
    Unicode/Vietnamese into Electron (Chromium) inputs that ignore WM_SETTEXT. Optionally
    presses Enter (submit=True). Resolve target by hwnd OR title_pattern (first match).

    Intended for SHORT single-line pokes (e.g. 'doc inbox_codo.md MSG moi nhat'); keep heavy
    content in the .council bridge files, not in keystrokes. Focus is best-effort (UIPI);
    response reports focused_foreground so caller can verify the paste landed.
    """
    if not text:
        return _err("text is required", error_name="MISSING_REQUIRED_FIELD")
    if not _pywin32_available():
        return _err("desktop_send_text needs pywin32",
                    error_name="DESKTOP_INTERACTION_FAILED", remediation="pip install pywin32")
    import time as _time

    import win32api
    import win32clipboard
    import win32con
    import win32gui
    import win32process

    target_hwnd = hwnd
    resolved_from = "hwnd" if hwnd else ""
    if not target_hwnd and title_pattern:
        raw = await desktop_window_list(title_filter=title_pattern)
        wins = [
            w for w in json.loads(raw).get("windows", [])
            if _match_text(w.get("title") or "", title_pattern, match_mode)
        ]
        if wins:
            target_hwnd = wins[0]["hwnd"]
            resolved_from = "title_pattern"
        else:
            return _err(f"No desktop window matched title_pattern: {title_pattern}",
                        error_name="WINDOW_NOT_FOUND", title_pattern=title_pattern,
                        match_mode=match_mode)
    if not target_hwnd:
        return _err("hwnd or title_pattern required", error_name="MISSING_REQUIRED_FIELD")

    def _set_clipboard(s: str) -> None:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, s)
        finally:
            win32clipboard.CloseClipboard()

    def _send() -> dict:
        try:
            current_tid = win32api.GetCurrentThreadId()
            target_tid, _ = win32process.GetWindowThreadProcessId(target_hwnd)
            # Attach to BOTH target thread and the CURRENT foreground thread to defeat the
            # Windows foreground-lock; an ALT key tap additionally unlocks SetForegroundWindow
            # for our (background) calling thread.
            fg0 = win32gui.GetForegroundWindow()
            fg0_tid = 0
            if fg0 and fg0 != target_hwnd:
                try: fg0_tid, _ = win32process.GetWindowThreadProcessId(fg0)
                except Exception: fg0_tid = 0
            attached = []
            for _tid in (target_tid, fg0_tid):
                if _tid and _tid != current_tid and _tid not in attached:
                    try:
                        win32process.AttachThreadInput(current_tid, _tid, True)
                        attached.append(_tid)
                    except Exception:
                        pass
            try:
                if win32gui.IsIconic(target_hwnd):
                    win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
                # NOTE: do NOT tap ALT to unlock SetForegroundWindow — apps with a native
                # menu bar (e.g. Codex: File/Edit/View) interpret ALT as menu-activation and
                # steal focus from the input box, so Ctrl+V lands on the menu. The dual
                # AttachThreadInput above is sufficient to grant foreground rights.
                _per = max(0, pre_focus_delay_ms) / 1000.0 / 3 + 0.05
                fg = fg0
                for _ in range(3):
                    try:
                        win32gui.SetForegroundWindow(target_hwnd)
                        win32gui.BringWindowToTop(target_hwnd)
                    except Exception:
                        pass
                    _time.sleep(_per)
                    fg = win32gui.GetForegroundWindow()
                    if fg == target_hwnd:
                        break
                focused = (fg == target_hwnd)
                if require_focus and not focused:
                    return {"ok": False,
                            "error": f"could not bring target window to foreground (fg_hwnd={fg}); aborted before keystrokes to avoid wrong-window delivery",
                            "error_name": "FOCUS_NOT_ACHIEVED",
                            "hwnd": target_hwnd, "fg_hwnd": fg,
                            "resolved_from": resolved_from or "unknown"}
                # Click into the input field to give the inner textarea keyboard focus.
                # Bringing an Electron window to the foreground does NOT focus its DOM input;
                # after a finished turn the caret leaves the box, so Ctrl+V would no-op. The
                # click runs WHILE attached to the target thread (delivered as real input).
                _click_pt = None
                if click_focus:
                    try:
                        _l, _t, _r, _b = win32gui.GetWindowRect(target_hwnd)
                        _cx = click_x if click_x else (_l + (_r - _l) // 2)
                        _cy = click_y if click_y else (_b - 95)
                        win32api.SetCursorPos((_cx, _cy))
                        _time.sleep(0.05)
                        win32api.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
                        win32api.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
                        _click_pt = [_cx, _cy]
                        _time.sleep(0.12)
                    except Exception:
                        _click_pt = "click_failed"
                steps = []
                if method == "clipboard":
                    _set_clipboard(text)
                    _time.sleep(0.05)
                    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                    win32api.keybd_event(0x56, 0, 0, 0)            # 'V'
                    win32api.keybd_event(0x56, 0, win32con.KEYEVENTF_KEYUP, 0)
                    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                    steps.append("ctrl_v")
                else:
                    return {"ok": False, "error": f"unknown method: {method}",
                            "error_name": "BAD_METHOD"}
                if submit:
                    _time.sleep(0.10)
                    win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
                    win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
                    steps.append("enter")
                return {
                    "ok": True,
                    "method": method,
                    "hwnd": target_hwnd,
                    "resolved_from": resolved_from or "unknown",
                    "focused_foreground": focused,
                    "fg_hwnd": fg,
                    "submit": submit,
                    "steps": steps,
                    "text_len": len(text),
                    "clicked": _click_pt,
                }
            finally:
                for _tid in attached:
                    try: win32process.AttachThreadInput(current_tid, _tid, False)
                    except Exception: pass
        except Exception as exc:
            return {"ok": False, "error": str(exc), "error_name": "WIN32_API_ERROR"}

    data = await asyncio.to_thread(_send)
    if data.get("ok"):
        return _ok(data)
    return _err(data.get("error", "send_text failed"),
                error_name=data.get("error_name", "DESKTOP_INTERACTION_FAILED"),
                hwnd=target_hwnd, resolved_from=resolved_from or "unknown",
                non_fatal=True, ca_next_action="do_not_retry_focus")


async def desktop_click(hwnd: int = 0, x: int = 0, y: int = 0,
                          cdp_port: int = _DEFAULT_CDP_PORT,
                          title_pattern: str = "",
                          match_mode: str = "contains_case_insensitive",
                          fallback_to_win32: bool = True,
                          expected_url: str = "",
                          wait_for_navigation: bool = False,
                          force_win32: bool = False,
                          project_path: str = "",
                          dispatch_dom_click: bool = True,
                          report_dom_evidence: bool = True,
                          target_selector: str = "") -> str:
    """V15 R12 Round B: CDP-first click for Electron/Chromium; Win32 fallback.

    Coordinates:
      - CDP path: x/y are VIEWPORT coordinates (relative to page).
      - Win32 path: x/y are CLIENT coordinates (relative to window).

    Strategy:
      1. If cdp_port + page target available → Input.dispatchMouseEvent (mousePressed + mouseReleased)
         This works for Electron because Chromium handles its own input pipeline.
      2. Fallback Win32: ChildWindowFromPoint resolves real child HWND under (x,y);
         AttachThreadInput; synchronous SendMessage + activate sequence
         (WM_NCHITTEST → WM_MOUSEACTIVATE → WM_LBUTTONDOWN/UP).

    V15 R33: `force_win32=True` SKIPS CDP entirely — use when targeting a NATIVE Win32
    window (system modal, MessageBox, native folder dialog) where CDP would otherwise
    hijack focus and click the wrong page. Required for hwnd-targeted system dialogs.
    """
    def _as_int(value, field: str, default: int | None = None):
        if value is None or value == "":
            if default is not None:
                return default, None
            return None, _err(
                f"{field} must be an integer",
                error_name="WRONG_ARGUMENTS",
                field=field,
                expected_type="integer",
                received_type=type(value).__name__,
            )
        try:
            return int(value), None
        except (TypeError, ValueError):
            return None, _err(
                f"{field} must be an integer",
                error_name="WRONG_ARGUMENTS",
                field=field,
                expected_type="integer",
                received_type=type(value).__name__,
                received_value=str(value)[:80],
            )

    hwnd, arg_err = _as_int(hwnd, "hwnd", 0)
    if arg_err:
        return arg_err
    x, arg_err = _as_int(x, "x")
    if arg_err:
        return arg_err
    y, arg_err = _as_int(y, "y")
    if arg_err:
        return arg_err
    cdp_port, arg_err = _as_int(cdp_port, "cdp_port", _DEFAULT_CDP_PORT)
    if arg_err:
        return arg_err

    cdp_attempted = not force_win32
    root_causes = []
    target = None
    err = "force_win32=True bypasses CDP" if force_win32 else ""
    # R33: skip CDP entirely in force_win32 mode (avoid hijacking native modal click)
    if force_win32:
        root_causes.append("FORCE_WIN32_REQUESTED")
        if not fallback_to_win32:
            return _err("force_win32=True requires fallback_to_win32=True",
                        error_name="DESKTOP_INTERACTION_FAILED",
                        root_causes=[*root_causes, "FALLBACK_DISABLED"],
                        fallback_attempted=False,
                        cdp_attempted=False)
    else:
        # CDP path first
        target, err = await _cdp_pick_target(
            cdp_port,
            prefer_title_substr=title_pattern or "",
            match_mode=match_mode,
            expected_url=expected_url,
            wait_for_navigation=wait_for_navigation,
        )
    if target:
        # Activate (focus) page first per CDP best practice
        bring, berr = await _cdp_dispatch(cdp_port, target["id"], "Page.bringToFront", {})
        # V15 R59: mouseMoved BEFORE press establishes hover state.
        # Frameworks using pointerenter/mouseover handlers (Svelte, SolidJS, Vue
        # transition) often gate clicks on a prior hover signal. Without this,
        # the press lands but downstream pointer/click chain stays unarmed.
        moved, merr = await _cdp_dispatch(cdp_port, target["id"], "Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y, "button": "none", "buttons": 0,
        })
        up, uerr = None, None
        down, derr = await _cdp_dispatch(cdp_port, target["id"], "Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y, "button": "left",
            "clickCount": 1, "buttons": 1,
        })
        if down and not down.get("error"):
            up, uerr = await _cdp_dispatch(cdp_port, target["id"], "Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": x, "y": y, "button": "left",
                "clickCount": 1, "buttons": 0,
            })
            if up and not up.get("error"):
                # V15 R59: gather DOM evidence at click point so CA can diagnose
                # "clicked but #result unchanged" cases. Returns the actual
                # element at (x,y) — tag/id/class/rect/disabled/text — to verify
                # the click hit the intended target.
                dom_evidence = None
                if report_dom_evidence:
                    ev_js = (
                        "(()=>{const el=document.elementFromPoint(" + str(x) + "," + str(y) + ");"
                        "if(!el)return{found:false};"
                        "const r=el.getBoundingClientRect();const cs=getComputedStyle(el);"
                        "return{found:true,tag:el.tagName,id:el.id||'',cls:String(el.className||''),"
                        "name:el.getAttribute('name')||'',type:el.getAttribute('type')||'',"
                        "disabled:!!el.disabled,ariaDisabled:el.getAttribute('aria-disabled')||'',"
                        "outerHTML:(el.outerHTML||'').slice(0,300),"
                        "rect:{x:r.x,y:r.y,w:r.width,h:r.height},"
                        "display:cs.display,visibility:cs.visibility,pointerEvents:cs.pointerEvents,"
                        "textContent:(el.textContent||'').slice(0,120)};})()"
                    )
                    ev_res, _ev_err = await _cdp_dispatch(cdp_port, target["id"], "Runtime.evaluate", {
                        "expression": ev_js, "returnByValue": True, "awaitPromise": False,
                    })
                    # V15 R61 FIX: CDP envelope is {result:{result:{type,value}}} —
                    # outer "result" is the message envelope, inner "result" is
                    # Runtime.evaluate's return wrapper, "value" holds the JS value
                    # when returnByValue=true. R59 missed one level of indirection
                    # so dom_evidence was always None → field never appeared.
                    if ev_res and not ev_res.get("error"):
                        dom_evidence = (((ev_res.get("result") or {}).get("result")) or {}).get("value")
                        if dom_evidence is None:
                            # Defensive: surface raw envelope when shape unexpected
                            dom_evidence = {"_raw_envelope": ev_res.get("result"), "_note": "Runtime.evaluate succeeded but value key missing — check returnByValue support"}

                # V15 R59: synthetic DOM .click() fallback — covers the case where
                # Input.dispatchMouseEvent fires mouseDown/mouseUp but the framework
                # listens via event delegation, capture-phase pointer events, or
                # ignores synthesized OS-level mouse events (some Electron apps
                # require this). el.click() invokes the DOM's own click() method
                # which fires synthetic click events that bubble normally.
                #
                # V15 R64-A: target_selector PRECISION mode — when CA passes
                # target_selector, ALWAYS prefer querySelector(target_selector).click()
                # over elementFromPoint(x,y).click(). Solves the "bbox center hits
                # DIV overlay/tooltip instead of intended BUTTON" drift seen live
                # on Tr_Rawcut_Bot Sheet Scanner UI when a hint <div> visually
                # overlapped the Scan button. Coord-based dispatch (mouseMoved/
                # Pressed/Released) still fires for raw-event handlers; the DOM
                # .click() now hits the SEMANTIC target, not whatever is on top.
                dom_synthetic_click = None
                if dispatch_dom_click:
                    if target_selector:
                        # R64-A precision path: click by selector, not coord
                        _sel_js = json.dumps(target_selector)
                        dc_js = (
                            "(()=>{const el=document.querySelector(" + _sel_js + ");"
                            "if(!el)return{found:false,used:'selector',selector:" + _sel_js + "};"
                            "try{el.click();return{found:true,used:'selector',selector:" + _sel_js + ","
                            "tag:el.tagName,id:el.id||'',cls:String(el.className||''),"
                            "textContent:(el.textContent||'').slice(0,120),"
                            "clicked_via:'el.click()@selector'};}"
                            "catch(e){return{found:true,used:'selector',error:String(e)};}})()"
                        )
                    else:
                        # Pre-R64 behavior: elementFromPoint(x,y).click()
                        dc_js = (
                            "(()=>{const el=document.elementFromPoint(" + str(x) + "," + str(y) + ");"
                            "if(!el)return{found:false,used:'elementFromPoint'};"
                            "try{el.click();return{found:true,used:'elementFromPoint',tag:el.tagName,id:el.id||'',clicked_via:'el.click()@xy'};}"
                            "catch(e){return{found:true,used:'elementFromPoint',error:String(e)};}})()"
                        )
                    dc_res, _dc_err = await _cdp_dispatch(cdp_port, target["id"], "Runtime.evaluate", {
                        "expression": dc_js, "returnByValue": True, "awaitPromise": False,
                    })
                    # V15 R61 FIX: same nesting issue as dom_evidence — CDP envelope
                    # is {result:{result:{type,value}}}. Was returning None silently.
                    if dc_res and not dc_res.get("error"):
                        dom_synthetic_click = (((dc_res.get("result") or {}).get("result")) or {}).get("value")
                        if dom_synthetic_click is None:
                            dom_synthetic_click = {"_raw_envelope": dc_res.get("result"), "_note": "Runtime.evaluate succeeded but value key missing"}

                _events_list = [
                    "Page.bringToFront",
                    "Input.dispatchMouseEvent:mouseMoved",
                    "Input.dispatchMouseEvent:mousePressed",
                    "Input.dispatchMouseEvent:mouseReleased",
                ]
                if dom_synthetic_click is not None:
                    _events_list.append("Runtime.evaluate:el.click()")

                resp = {
                    "method": "cdp",
                    "cdp_port": cdp_port,
                    "target_id": target["id"],
                    "target_url": (target.get("url") or "")[:200],
                    "initial_url": (target.get("initial_url") or target.get("url") or "")[:200],
                    "final_url": (target.get("final_url") or target.get("url") or "")[:200],
                    "matched_title": (target.get("matched_title") or target.get("title") or "")[:200],
                    "match_mode": match_mode,
                    "bring_to_front_sent": bool(bring and not bring.get("error")),
                    "events_dispatched": _events_list,
                    "dispatch_results": [
                        {"method": "Page.bringToFront", "result": bring, "error": berr},
                        {"method": "Input.dispatchMouseEvent", "type": "mouseMoved", "result": moved, "error": merr},
                        {"method": "Input.dispatchMouseEvent", "type": "mousePressed", "result": down, "error": derr},
                        {"method": "Input.dispatchMouseEvent", "type": "mouseReleased", "result": up, "error": uerr},
                    ],
                    "x": x, "y": y,
                    "clicked": True,
                }
                if dom_evidence is not None:
                    resp["dom_evidence_at_xy"] = dom_evidence
                if dom_synthetic_click is not None:
                    resp["dom_synthetic_click"] = dom_synthetic_click
                return _ok(resp)
        root_causes.append("CDP_DISPATCH_FAILED")
        err = derr or uerr or berr or json.dumps((down or up or bring or {}).get("error", down or up or bring), ensure_ascii=False)
    else:
        root_causes.append("CDP_UNREACHABLE" if err and "unreachable" in err.lower() else "CDP_TARGET_NOT_FOUND")
        # Fall through to Win32
    if not fallback_to_win32:
        return _err("desktop interaction failed and Win32 fallback is disabled",
                    error_name="DESKTOP_INTERACTION_FAILED",
                    root_causes=root_causes,
                    fallback_attempted=False,
                    cdp_attempted=cdp_attempted,
                    cdp_note=err,
                    match_mode=match_mode,
                    expected_url=expected_url)
    # Win32 fallback (best-effort with HWND resolution)
    if not _pywin32_available():
        return _err("desktop interaction failed: pywin32 missing",
                    error_name="DESKTOP_INTERACTION_FAILED",
                    root_causes=[*root_causes, "PYWIN32_MISSING"],
                    fallback_attempted=False,
                    cdp_attempted=cdp_attempted,
                    cdp_note=err,
                    remediation="pip install pywin32")
    import win32api
    import win32con
    import win32gui
    import win32process
    target_hwnd = hwnd
    resolved_from = "hwnd" if hwnd else ""
    window_match_count = 0
    if not target_hwnd and title_pattern:
        raw = await desktop_window_list(title_filter=title_pattern)
        wins = [
            w for w in json.loads(raw).get("windows", [])
            if _match_text(w.get("title") or "", title_pattern, match_mode)
        ]
        window_match_count = len(wins)
        if wins:
            target_hwnd = wins[0]["hwnd"]
            resolved_from = "title_pattern"
        else:
            return _err(
                f"No desktop window matched title_pattern: {title_pattern}",
                error_name="WINDOW_NOT_FOUND",
                title_pattern=title_pattern,
                matched_title="",
                match_mode=match_mode,
                window_match_count=0,
                cdp_attempted=cdp_attempted,
                cdp_error=err,
            )
    if not target_hwnd:
        return _err("hwnd or title_pattern required, OR CDP unreachable",
                    error_name="MISSING_REQUIRED_FIELD",
                    title_pattern=title_pattern,
                    matched_title="",
                    match_mode=match_mode,
                    window_match_count=window_match_count,
                    cdp_attempted=cdp_attempted,
                    cdp_note=err or "no CDP target found")

    def _click() -> dict:
        try:
            # Resolve actual child window under (x,y) — important for native windows
            try:
                child = win32gui.ChildWindowFromPoint(target_hwnd, (x, y))
                if child:
                    target = child
                else:
                    target = target_hwnd
            except Exception:
                target = target_hwnd

            # AttachThreadInput so input is delivered as if from focused app
            current_tid = win32api.GetCurrentThreadId()
            target_tid, target_pid = win32process.GetWindowThreadProcessId(target)
            attached = False
            attach_attempted = bool(target_tid and target_tid != current_tid)

            def _integrity_evidence(pid: int) -> dict:
                evidence = {
                    "target_pid": pid or 0,
                    "available": False,
                    "is_ui_access_enabled": None,
                    "target_integrity_level": "",
                    "current_integrity_level": "",
                    "target_higher_than_orchestrator": None,
                    "warning": "",
                }
                try:
                    import os as _os

                    import win32security
                    process_query = getattr(win32con, "PROCESS_QUERY_LIMITED_INFORMATION", 0x1000)
                    token_query = getattr(win32security, "TOKEN_QUERY", 0x0008)
                    levels = [
                        (0x00001000, "low"),
                        (0x00002000, "medium"),
                        (0x00003000, "high"),
                        (0x00004000, "system"),
                    ]

                    def _level_for(proc_pid: int) -> tuple[int, str]:
                        proc_handle = win32api.OpenProcess(process_query, False, int(proc_pid))
                        token = win32security.OpenProcessToken(proc_handle, token_query)
                        sid_and_attrs = win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel)
                        sid = sid_and_attrs[0] if isinstance(sid_and_attrs, tuple) else sid_and_attrs
                        count = win32security.GetSidSubAuthorityCount(sid)
                        rid = win32security.GetSidSubAuthority(sid, count - 1)
                        label = next((name for value, name in reversed(levels) if rid >= value), f"rid:{rid}")
                        return int(rid), label

                    def _ui_access_for(proc_pid: int):
                        try:
                            proc_handle = win32api.OpenProcess(process_query, False, int(proc_pid))
                            token = win32security.OpenProcessToken(proc_handle, token_query)
                            return bool(win32security.GetTokenInformation(token, win32security.TokenUIAccess))
                        except Exception:
                            return None

                    current_rid, current_label = _level_for(_os.getpid())
                    target_rid, target_label = _level_for(pid)
                    target_higher = target_rid > current_rid
                    evidence.update({
                        "available": True,
                        "is_ui_access_enabled": _ui_access_for(_os.getpid()),
                        "target_integrity_level": target_label,
                        "current_integrity_level": current_label,
                        "target_higher_than_orchestrator": target_higher,
                        "warning": (
                            "Target process appears to run at a higher integrity level; Windows UIPI may block synthetic input."
                            if target_higher else ""
                        ),
                    })
                except Exception as exc:
                    evidence["warning"] = f"integrity evidence unavailable: {exc}"
                return evidence

            integrity = _integrity_evidence(target_pid)
            if target_tid and target_tid != current_tid:
                try:
                    win32process.AttachThreadInput(current_tid, target_tid, True)
                    attached = True
                except Exception:
                    pass
            try:
                l_param = (y << 16) | (x & 0xFFFF)
                # Send the canonical sequence
                sequence = []
                try:
                    if hasattr(win32gui, "SendMessageTimeout"):
                        mouse_activate = win32gui.SendMessageTimeout(
                            target,
                            win32con.WM_MOUSEACTIVATE,
                            target_hwnd,
                            (win32con.WM_LBUTTONDOWN << 16) | win32con.HTCLIENT,
                            getattr(win32con, "SMTO_NORMAL", 0),
                            2000,
                        )
                        mouse_api = "SendMessageTimeout"
                    else:
                        mouse_activate = win32gui.SendMessage(
                            target,
                            win32con.WM_MOUSEACTIVATE,
                            target_hwnd,
                            (win32con.WM_LBUTTONDOWN << 16) | win32con.HTCLIENT,
                        )
                        mouse_api = "SendMessage"
                    sequence.append({"message": "WM_MOUSEACTIVATE", "sent": True, "delivery_verified": True, "api": mouse_api, "result": mouse_activate, "last_error": 0})
                except Exception as exc:
                    sequence.append({"message": "WM_MOUSEACTIVATE", "sent": False, "delivery_verified": False, "api": "SendMessageTimeout" if hasattr(win32gui, "SendMessageTimeout") else "SendMessage", "error": str(exc), "last_error": getattr(win32api, "GetLastError", lambda: 0)()})

                def _send_message(message_name: str, message_id: int, w_param: int) -> dict:
                    try:
                        if hasattr(win32api, "SetLastError"):
                            win32api.SetLastError(0)
                    except Exception:
                        pass
                    try:
                        timeout_ms = 2000
                        if hasattr(win32gui, "SendMessageTimeout"):
                            smto_normal = getattr(win32con, "SMTO_NORMAL", 0)
                            result = win32gui.SendMessageTimeout(
                                target, message_id, w_param, l_param, smto_normal, timeout_ms
                            )
                            api_name = "SendMessageTimeout"
                        else:
                            result = win32gui.SendMessage(target, message_id, w_param, l_param)
                            api_name = "SendMessage"
                        last_error = getattr(win32api, "GetLastError", lambda: 0)()
                        return {
                            "message": message_name,
                            "sent": True,
                            "delivery_verified": True,
                            "api": api_name,
                            "result": result,
                            "last_error": last_error,
                        }
                    except Exception as exc:
                        return {
                            "message": message_name,
                            "sent": False,
                            "delivery_verified": False,
                            "api": "SendMessageTimeout" if hasattr(win32gui, "SendMessageTimeout") else "SendMessage",
                            "error": str(exc),
                            "last_error": getattr(win32api, "GetLastError", lambda: 0)(),
                        }

                sequence.append(_send_message("WM_LBUTTONDOWN", win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON))
                sequence.append(_send_message("WM_LBUTTONUP", win32con.WM_LBUTTONUP, 0))
                clicked = all(item.get("sent") is True and item.get("delivery_verified") is not False for item in sequence)
                if not clicked:
                    return {
                        "ok": False,
                        "error": "Win32 SendMessage click sequence failed",
                        "error_name": "WIN32_SEND_MESSAGE_FAILED",
                        "legacy_error_name": "WIN32_POST_MESSAGE_FAILED",
                        "method": "win32_attach",
                        "message_api": "SendMessageTimeout" if hasattr(win32gui, "SendMessageTimeout") else "SendMessage",
                        "resolved_hwnd": target_hwnd,
                        "child_hwnd": target if target != target_hwnd else 0,
                        "resolved_child_hwnd": target if target != target_hwnd else 0,
                        "hwnd": target,
                        "resolved_from": resolved_from or "unknown",
                        "title_pattern": title_pattern,
                        "matched_title": "",
                        "match_mode": match_mode,
                        "window_match_count": window_match_count,
                        "integrity": integrity,
                        "uipi_warning": integrity.get("warning", ""),
                        "sequence": sequence,
                        "root_causes": [
                            item["message"]
                            for item in sequence
                            if item.get("sent") is not True
                        ],
                    }
                return {
                    "ok": True,
                    "method": "win32_attach",
                    "message_api": "SendMessageTimeout" if hasattr(win32gui, "SendMessageTimeout") else "SendMessage",
                    "resolved_hwnd": target_hwnd,
                    "child_hwnd": target if target != target_hwnd else 0,
                    "resolved_child_hwnd": target if target != target_hwnd else 0,
                    "hwnd": target,
                    "resolved_from": resolved_from or "unknown",
                    "title_pattern": title_pattern,
                    "matched_title": "",
                    "match_mode": match_mode,
                    "window_match_count": window_match_count,
                    "integrity": integrity,
                    "uipi_warning": integrity.get("warning", ""),
                    "attach_thread_input": {
                        "attempted": attach_attempted,
                        "attached": attached,
                        "current_tid": current_tid,
                        "target_tid": target_tid,
                    },
                    "sequence": sequence,
                    "x": x,
                    "y": y,
                    "clicked": clicked,
                }
            finally:
                if attached:
                    _safe_detach(current_tid, target_tid)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "error_name": "WIN32_API_ERROR"}

    data = await asyncio.to_thread(_click)
    if data.get("ok"):
        return _ok(data)
    return _err(data.get("error", "click failed"), error_name="DESKTOP_INTERACTION_FAILED",
                root_causes=[
                    item for item in [
                        *root_causes,
                        data.get("error_name", "WIN32_API_ERROR"),
                        data.get("legacy_error_name", ""),
                        *data.get("root_causes", []),
                    ] if item
                ],
                fallback_attempted=True,
                cdp_attempted=cdp_attempted,
                cdp_note=err or "CDP attempted but failed",
                method=data.get("method", "win32_attach"),
                message_api=data.get("message_api", ""),
                hwnd=target_hwnd,
                resolved_hwnd=data.get("resolved_hwnd", target_hwnd),
                child_hwnd=data.get("child_hwnd", 0),
                resolved_child_hwnd=data.get("resolved_child_hwnd", data.get("child_hwnd", 0)),
                resolved_from=resolved_from or "unknown",
                title_pattern=title_pattern,
                matched_title="",
                match_mode=match_mode,
                window_match_count=window_match_count,
                sequence=data.get("sequence", []),
                hint="For Electron, launch app with --remote-debugging-port=9223 to enable CDP click path.")


async def desktop_window_state(state: str,
                                  hwnd: int = 0,
                                  title_pattern: str = "",
                                  match_mode: str = "contains_case_insensitive",
                                  project_path: str = "") -> str:
    """V15 R58 T4d: Win32 ShowWindow primitive for reliable window-state changes.

    Bypasses click-on-titlebar drift. Clicking the maximize button at fixed
    coordinates is unreliable: DPI scaling, Electron frame theme, and OS
    chrome variations move the hit area. ShowWindow(hwnd, SW_*) is the
    official Windows API for state changes and never misses.

    States → Win32 SW constants:
      maximize → SW_MAXIMIZE (3)
      restore  → SW_RESTORE  (9)   — un-minimize OR un-maximize
      minimize → SW_MINIMIZE (6)
      normal   → SW_NORMAL   (1)   — show in normal size/position
      hide     → SW_HIDE     (0)
      show     → SW_SHOW     (5)
    """
    try:
        # The two suppressed imports below are never called in this function: they exist so
        # that an incomplete pywin32 install raises ImportError at this gate instead of
        # failing deeper in. ruff declines to fix an unused import inside an availability
        # probe, so the intent is declared inline rather than left as a standing finding.
        import win32api  # noqa: F401
        import win32con
        import win32gui
        import win32process  # noqa: F401
    except ImportError:
        return _missing_dep("pywin32", "pip install pywin32")

    state_map = {
        "maximize": getattr(win32con, "SW_MAXIMIZE", 3),
        "restore":  getattr(win32con, "SW_RESTORE",  9),
        "minimize": getattr(win32con, "SW_MINIMIZE", 6),
        "normal":   getattr(win32con, "SW_NORMAL",   1),
        "hide":     getattr(win32con, "SW_HIDE",     0),
        "show":     getattr(win32con, "SW_SHOW",     5),
    }
    if state not in state_map:
        return _err(f"Unsupported state: {state}",
                    error_name="UNSUPPORTED_FIELD",
                    accepted_states=list(state_map.keys()))
    sw_const = state_map[state]

    target_hwnd = int(hwnd) if hwnd else 0
    resolved_from = "hwnd_arg" if target_hwnd else ""
    window_match_count = 0

    if not target_hwnd and title_pattern:
        import re as _re_dws
        def _title_match(_t: str, _pat: str, _mode: str) -> bool:
            if _mode == "exact":               return _t == _pat
            if _mode == "contains":            return _pat in _t
            if _mode == "regex":
                try: return bool(_re_dws.search(_pat, _t))
                except Exception: return False
            if _mode == "case_insensitive":    return _t.lower() == _pat.lower()
            return _pat.lower() in _t.lower()  # contains_case_insensitive (default)

        candidates = []
        def _enum_cb(_hw, _):
            if not win32gui.IsWindowVisible(_hw):
                return
            try:
                title = win32gui.GetWindowText(_hw) or ""
            except Exception:
                return
            if _title_match(title, title_pattern, match_mode):
                candidates.append({"hwnd": _hw, "title": title})

        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception as exc:
            return _err(f"window enumeration failed: {exc}",
                        error_name="WIN32_API_ERROR")
        window_match_count = len(candidates)
        if not candidates:
            return _err(f"No window matched title_pattern={title_pattern!r}",
                        error_name="WINDOW_NOT_FOUND",
                        title_pattern=title_pattern,
                        match_mode=match_mode)
        target_hwnd = candidates[0]["hwnd"]
        resolved_from = "title_pattern"

    if not target_hwnd:
        return _err("Need hwnd or title_pattern",
                    error_name="WRONG_ARGUMENTS")

    def _apply() -> dict:
        try:
            rect_before_tuple = win32gui.GetWindowRect(target_hwnd)
            rect_before = {
                "x": rect_before_tuple[0], "y": rect_before_tuple[1],
                "w": rect_before_tuple[2] - rect_before_tuple[0],
                "h": rect_before_tuple[3] - rect_before_tuple[1],
            }
            was_minimized = bool(win32gui.IsIconic(target_hwnd))
            was_visible = bool(win32gui.IsWindowVisible(target_hwnd))
            prev_state = (
                "minimized" if was_minimized
                else "hidden" if not was_visible
                else "shown"
            )
            # SW_RESTORE on a NOT minimized window also un-maximizes — desired semantics.
            win32gui.ShowWindow(target_hwnd, sw_const)
            # For maximize, also bring to foreground so it becomes the active window.
            if state == "maximize":
                try:
                    win32gui.SetForegroundWindow(target_hwnd)
                except Exception:
                    pass  # SetForegroundWindow can fail under UIPI; not critical
            rect_after_tuple = win32gui.GetWindowRect(target_hwnd)
            rect_after = {
                "x": rect_after_tuple[0], "y": rect_after_tuple[1],
                "w": rect_after_tuple[2] - rect_after_tuple[0],
                "h": rect_after_tuple[3] - rect_after_tuple[1],
            }
            is_visible_after = bool(win32gui.IsWindowVisible(target_hwnd))
            is_minimized_after = bool(win32gui.IsIconic(target_hwnd))
            return {
                "ok": True,
                "method": "win32_show_window",
                "state_requested": state,
                "sw_const": sw_const,
                "hwnd": target_hwnd,
                "resolved_from": resolved_from or "unknown",
                "title_pattern": title_pattern,
                "match_mode": match_mode,
                "window_match_count": window_match_count,
                "rect_before": rect_before,
                "rect_after": rect_after,
                "rect_changed": rect_before != rect_after,
                "was_minimized": was_minimized,
                "was_visible": was_visible,
                "prev_state": prev_state,
                "is_visible_after": is_visible_after,
                "is_minimized_after": is_minimized_after,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "error_name": "WIN32_API_ERROR"}

    data = await asyncio.to_thread(_apply)
    if data.get("ok"):
        return _ok(data)
    return _err(data.get("error", "show_window failed"),
                error_name=data.get("error_name", "DESKTOP_INTERACTION_FAILED"),
                hwnd=target_hwnd,
                state_requested=state,
                resolved_from=resolved_from or "unknown")


def _desktop_screenshot_capture(hwnd: int, save_path: str, monitor: int) -> str:
    """Blocking screen capture (win32 enum + PIL.ImageGrab + save).

    Anh 2026-08-10: runs on a worker thread (asyncio.to_thread) — the capture of a
    full virtual desktop can take seconds and previously blocked the [A] event loop
    (observed /health p95 ~46s during a single desktop_screenshot).
    """
    from PIL import ImageGrab
    try:
        bbox = None
        mons = []
        if _pywin32_available():
            try:
                import win32api
                for _hmon, _hdc, _rect in win32api.EnumDisplayMonitors():
                    info = win32api.GetMonitorInfo(_hmon)
                    r = info.get("Monitor", _rect)
                    mons.append({"left": r[0], "top": r[1], "right": r[2], "bottom": r[3],
                                 "primary": bool(info.get("Flags", 0) & 1)})
                mons.sort(key=lambda m: (m["left"], m["top"]))
            except Exception:
                mons = []
        selected = None
        if hwnd and _pywin32_available():
            import win32gui
            bbox = win32gui.GetWindowRect(hwnd)
        elif monitor and monitor > 0:
            if not mons:
                return _err("monitor enumeration unavailable (pywin32 missing)",
                            error_name="MONITOR_ENUM_UNAVAILABLE")
            if monitor < 1 or monitor > len(mons):
                return _err(f"monitor {monitor} out of range (1..{len(mons)})",
                            error_name="MONITOR_OUT_OF_RANGE", monitors=mons)
            m = mons[monitor - 1]
            bbox = (m["left"], m["top"], m["right"], m["bottom"])
            selected = monitor
        if not save_path:
            cache_dir = Path(__file__).parent / ".orchestrator_cache" / "screenshots"
            cache_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time() * 1000)
            save_path = str(cache_dir / f"desktop_{ts}.png")
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
        except TypeError:
            img = ImageGrab.grab(bbox=bbox)
        img.save(save_path)
        captured = "window" if hwnd else (f"monitor_{selected}" if selected else "all_monitors_virtual")
        return _ok({"hwnd": hwnd, "monitor": selected, "monitor_count": len(mons),
                    "monitors": mons, "captured": captured,
                    "path": save_path, "size": img.size,
                    "bytes": Path(save_path).stat().st_size})
    except Exception as exc:
        return _err(str(exc), error_name="DESKTOP_SCREENSHOT_FAILED")


# D-02 (S2, HIGH): hard upper bound for a single screenshot capture. A full
# virtual-desktop ImageGrab across several monitors can take seconds; if the
# underlying driver stalls we must not keep the worker thread (and the caller)
# waiting forever — return a structured timeout error instead.
_DESKTOP_SCREENSHOT_TIMEOUT_SEC = 20.0


async def desktop_screenshot(hwnd: int = 0, save_path: str = "", monitor: int = 0,
                             all_monitors: bool = True) -> str:
    """V15 R8 B.5 + multimon: PIL.ImageGrab screenshot.

    - hwnd>0    : capture that window's region (virtual coords, spans all monitors).
    - monitor>0 : capture a single monitor by 1-based index (sorted left->right).
    - else      : capture the FULL virtual desktop across ALL monitors.
    Always returns monitors[] so the caller can pick an index next time.

    The blocking capture is offloaded via asyncio.to_thread so it never stalls the
    event loop (see _desktop_screenshot_capture), and it is additionally wrapped
    in asyncio.wait_for so a hung capture cannot leave the caller waiting past
    _DESKTOP_SCREENSHOT_TIMEOUT_SEC (D-02).
    """
    try:
        from PIL import ImageGrab  # noqa: F401 — fail fast with a clear dep error
    except ImportError:
        return _missing_dep("pillow", "pip install pillow")
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_desktop_screenshot_capture, hwnd, save_path, monitor),
            timeout=_DESKTOP_SCREENSHOT_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        return _err(
            f"desktop_screenshot timed out after {_DESKTOP_SCREENSHOT_TIMEOUT_SEC:.0f}s",
            error_name="DESKTOP_SCREENSHOT_TIMEOUT",
            timeout_sec=_DESKTOP_SCREENSHOT_TIMEOUT_SEC,
        )


async def electron_devtools_log(cdp_port: int = 9223, max_messages: int = 50) -> str:
    """V15 R8 B.5: list CDP page targets at port.

    ⚠️ DEPRECATED V15 R41: this tool is mislabeled — it returns *targets*, not
    *log messages*. The live replacement is `list_cdp_targets` (same `cdp_port`
    arg, same enumeration semantics). For real console logs use
    `cdp_attach_target` to get a page_id, then `read_console_errors(page_id,
    level=...)`. Sunset target: V16 / R42+.
    """
    import urllib.request
    deprecation = {
        "deprecated": True,
        "deprecated_since": "V15 R41",
        "sunset": "R42+",
        "replacement": "list_cdp_targets (target enumeration); read_console_errors (console messages)",
        "migration_hint": (
            "Drop-in for enumeration: replace electron_devtools_log(cdp_port=...) with "
            "list_cdp_targets(cdp_port=...). For console messages: "
            "1) list_cdp_targets → pick target_id; "
            "2) cdp_attach_target(target_id=...) → page_id; "
            "3) read_console_errors(page_id=..., level='error')."
        ),
    }
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=3) as r:
            targets = json.loads(r.read())
        return _ok({
            "cdp_port": cdp_port,
            "targets": [
                {"type": t.get("type"), "url": (t.get("url") or "")[:200],
                 "title": (t.get("title") or "")[:120], "id": t.get("id")}
                for t in targets[:max_messages]
            ],
            "target_count": len(targets),
            **deprecation,
        })
    except Exception as exc:
        return _err(f"CDP unreachable on port {cdp_port}: {exc}",
                    error_name="CDP_UNREACHABLE", **deprecation)


async def process_tree_inspect(root_pid: int = 0, name_pattern: str = "",
                                 max_depth: int = 3) -> str:
    """V15 R8 B.5: psutil process tree from root_pid or name/cmdline substring."""
    if not _psutil_available():
        return _missing_dep("psutil", "pip install psutil")
    import psutil

    def _walk(pid: int, depth: int = 0) -> dict:
        try:
            p = psutil.Process(pid)
            children = []
            if depth < max_depth:
                for child in p.children():
                    children.append(_walk(child.pid, depth + 1))
            return {
                "pid": pid, "name": p.name(),
                "cmdline": " ".join(p.cmdline()[:3]),
                "memory_mb": round(p.memory_info().rss / 1024 / 1024, 1),
                "children": children,
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
            return {"pid": pid, "error": str(exc)}

    if root_pid:
        tree = await asyncio.to_thread(_walk, root_pid)
        return _ok({"root_pid": root_pid, "tree": tree})

    if not name_pattern:
        return _err(
            "root_pid or name_pattern required", error_name="MISSING_REQUIRED_FIELD",
            accepted_fields=["root_pid", "name_pattern", "max_depth"],
            payload_examples=[{"root_pid": 1234, "max_depth": 3},
                              {"name_pattern": "server.py", "max_depth": 3}],
            ca_next_action="pass_root_pid_or_name_pattern",
        )

    def _by_name() -> list:
        matches = []
        needle = name_pattern.casefold()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = p.info.get("name") or ""
                cmdline = " ".join(p.info.get("cmdline") or [])
                if needle in name.casefold() or needle in cmdline.casefold():
                    matches.append(p.info["pid"])
                    if len(matches) >= 10:
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return [_walk(pid) for pid in matches[:10]]

    trees = await asyncio.to_thread(_by_name)
    return _ok({"name_pattern": name_pattern, "trees": trees, "count": len(trees)})


# ────────────────────────────────────────────────────────────────────────────
# B.6 — T4 Test / Assertion
# ────────────────────────────────────────────────────────────────────────────

async def run_playwright_test(test_script_path: str, project_path: str = "",
                                timeout_sec: int = 300) -> str:
    """V15 R8 B.6: execute a Node Playwright/CDP script, with language and dependency preflight."""
    fp = Path(test_script_path)
    if not fp.exists():
        return _err(f"script not found: {test_script_path}", error_name="PATH_NOT_FOUND")
    if fp.suffix.lower() not in {".js", ".mjs", ".cjs"}:
        return _err(
            f"run_playwright_test is Node-only; received {fp.suffix or 'no extension'}",
            error_name="RUNNER_LANGUAGE_MISMATCH",
            ca_next_action="use_run_project_test_script",
            suggested_alternative_tool={
                "tool": "run_project_test_script",
                "reason": "Use runtime='python' for Python fixtures; run_playwright_test accepts Node scripts only.",
            },
        )
    roots = [fp.parent]
    if project_path:
        roots.append(Path(project_path))
    roots = list(dict.fromkeys(roots))
    module_names = ("playwright", "playwright-core")
    module_ready = any(
        (root / "node_modules" / module_name).exists()
        for root in roots
        for module_name in module_names
    )
    if not module_ready:
        return _err(
            "Playwright module not found in the script/project dependency roots",
            error_name="MODULE_NOT_FOUND",
            ca_next_action="install_playwright_or_use_internal_browser",
            suggested_alternative_tool={
                "tool": "run_project_command",
                "reason": "Install playwright in the test directory, or use ToolA's internal browser T4 path.",
            },
            module_roots_checked=[str(root) for root in roots[:8]],
        )
    try:
        r = subprocess.run(
            ["node", str(fp)], capture_output=True, text=True,
            timeout=timeout_sec, cwd=project_path or fp.parent,
        )
        return _ok({
            "script": str(fp),
            "exit_code": r.returncode,
            "stdout": r.stdout[-5000:],
            "stderr": r.stderr[-5000:],
            "timed_out": False,
        })
    except subprocess.TimeoutExpired:
        return _err("script timed out", error_name="TIMEOUT", timed_out=True)
    except Exception as exc:
        return _err(str(exc), error_name="EXEC_FAILED")


async def record_ui_flow(page_id: str, output_path: str = "") -> str:
    """V15 R8 B.6: Playwright tracing API placeholder.

    Note: full tracing requires start_tracing on context before page actions.
    This tool registers intent; actual trace start should happen at open_browser_page
    (planned R+1 extension).
    """
    state, err = await _get_page(page_id)
    if err:
        return err
    return _ok({
        "page_id": page_id,
        "tracing_supported": True,
        "feature_status": "preview_stub",
        "note": "Full trace.zip requires --record-trace flag at context creation; "
                "currently returns intent acknowledgement. R+1 will extend "
                "open_browser_page with record_trace=True option.",
        "suggested_alternative": "screenshot_page + read_console_errors for step-by-step capture",
    })


async def assert_text_visible(page_id: str, text: str, timeout_ms: int = 2000) -> str:
    """V15 R8 B.6: wait for `text` to be visible on page."""
    state, err = await _get_page(page_id)
    if err:
        return err
    try:
        loc = state["page"].locator(f"text={text}").first
        await loc.wait_for(state="visible", timeout=timeout_ms)
        return _ok({"page_id": page_id, "text": text, "visible": True})
    except Exception as exc:
        return _err(str(exc), error_name="ASSERTION_FAILED", visible=False)


async def assert_button_click_changes_state(page_id: str, button_selector: str,
                                              observed_selector: str,
                                              timeout_ms: int = 3000) -> str:
    """V15 R8 B.6: snapshot observed_selector → click button → snapshot again → diff."""
    state, err = await _get_page(page_id)
    if err:
        return err
    try:
        page = state["page"]
        before = await page.locator(observed_selector).first.text_content() or ""
        await page.locator(button_selector).first.click(timeout=timeout_ms)
        await page.wait_for_timeout(500)
        after = await page.locator(observed_selector).first.text_content() or ""
        changed = before != after
        return _ok({
            "page_id": page_id, "button": button_selector,
            "observed": observed_selector,
            "changed": changed,
            "before": before[:200], "after": after[:200],
        })
    except Exception as exc:
        return _err(str(exc), error_name="ASSERTION_FAILED")


async def assert_console_no_error(page_id: str) -> str:
    """V15 R8 B.6: return ok=true only if no console.error in log."""
    state, err = await _get_page(page_id)
    if err:
        return err
    errors = [m for m in state["console"] if m["type"] == "error"]
    return _ok({
        "page_id": page_id,
        "error_count": len(errors),
        "ok": len(errors) == 0,
        "samples": errors[:5],
    })


# ────────────────────────────────────────────────────────────────────────────
# B.7 — UI backend
# ────────────────────────────────────────────────────────────────────────────

async def get_token_meter(session_id: str = "", project_path: str = "") -> str:
    """V15 R8 B.7: sum byte counters from events table for active session.

    Returns approximate cumulative bytes_in / bytes_out / call count.
    """
    import sqlite3

    from code_tools import _SESSIONS_DB, _get_active_session_id, _session_project_key

    sid = session_id
    if not sid and project_path:
        norm = _session_project_key(project_path)
        # Anh 2026-08-11 (Phase A on-loop audit): the SQLite read (and the
        # _get_active_session_id fallback below) pin the event loop when called
        # synchronously. Run the whole resolution OFF the loop in one to_thread
        # (read-only; no durability or ordering change). Returns a dict so the
        # ambiguous case is explicit.
        def _resolve_active_session():
            conn = sqlite3.connect(str(_SESSIONS_DB))
            try:
                rows = conn.execute(
                    "SELECT session_id, session_title, started_at FROM sessions WHERE project_path=? AND status='active' ORDER BY started_at DESC",
                    (norm,)
                ).fetchall()
            finally:
                conn.close()
            if len(rows) > 1:
                return {"ambiguous": True, "rows": rows, "sid": None}
            if rows:
                return {"ambiguous": False, "rows": rows, "sid": rows[0][0]}
            return {"ambiguous": False, "rows": rows, "sid": _get_active_session_id(norm)}
        _res = await asyncio.to_thread(_resolve_active_session)
        if _res["ambiguous"]:
            return _ok({
                "session_id": None,
                "ambiguous": True,
                "active_session_count": len(_res["rows"]),
                "candidates": [
                    {"session_id": r[0], "session_title": r[1], "started_at": r[2]}
                    for r in _res["rows"][:10]
                ],
                "note": "multiple active sessions for project_path; pass session_id explicitly",
            })
        sid = _res["sid"]
    if not sid:
        return _ok({"session_id": None, "calls": 0, "bytes_in_estimate": 0,
                    "bytes_out_estimate": 0, "note": "no active session"})

    def _q() -> dict:
        conn = sqlite3.connect(str(_SESSIONS_DB))
        try:
            # P16: a bogus session_id used to yield ok:true + zero counters.
            # Verify the session row exists first (mirrors get_session_events).
            sess = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (sid,)
            ).fetchone()
            if not sess:
                return {"_session_not_found": True}
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE session_id=?", (sid,)
            ).fetchone()[0]
            # Estimate: avg 1500 bytes per call (conservative)
            return {"calls": count, "bytes_in_estimate": count * 1200,
                    "bytes_out_estimate": count * 300}
        finally:
            conn.close()

    data = await asyncio.to_thread(_q)
    if data.get("_session_not_found"):
        return _err(f"Session not found: {sid}",
                    error_name="SESSION_NOT_FOUND",
                    hint="Call list_sessions(project_path) and retry with a valid session_id.",
                    ca_next_action="list_sessions")
    total_kb = round((data["bytes_in_estimate"] + data["bytes_out_estimate"]) / 1024, 1)
    return _ok({
        "session_id": sid, **data, "total_kb_estimate": total_kb,
        "note": "Estimated from event count. Live byte metering requires middleware "
                "instrumentation (planned R+1).",
    })


_SKILL_STATE_CACHE: dict = {}


async def get_skill_state(session_id: str = "", project_path: str = "",
                           verbosity: str = "full") -> str:
    """V15 R8 B.7: return skills marked loaded in current session.

    Currently a heuristic: lists SKILL.md files present in .ToolA_Skill/skills/.
    True per-session tracking requires middleware updating cache on each call's
    recommended_skill field (planned R+1).
    """
    # Resolve [A:P] via path_runtime when available
    try:
        import path_runtime
        a_p = Path(path_runtime.ORCHESTRATOR_DIR)
    except Exception:
        a_p = Path(__file__).parent
    skills_dir = a_p / ".ToolA_Skill" / "skills"
    a_p_str = str(a_p.resolve()).replace("\\", "/")
    skills: list = []
    if skills_dir.exists():
        for folder in sorted(skills_dir.iterdir()):
            sm = folder / "SKILL.md"
            if sm.exists():
                rel = str(sm.relative_to(a_p)).replace("\\", "/")
                abs_p = str(sm.resolve()).replace("\\", "/")
                skills.append({
                    "name": folder.name,
                    "path": rel,
                    "path_absolute": abs_p,
                    "size_bytes": sm.stat().st_size,
                    # R16.8: paste-ready payload for read_file_range — eliminates manual prepend
                    "read_file_range_payload": {
                        "file_path": abs_p,
                        "start_line": 1,
                        "end_line": 200,
                    },
                    "read_file_range_payload_relative": {
                        "project_path": a_p_str,
                        "file_path": rel,
                        "start_line": 1,
                        "end_line": 200,
                    },
                })
    return _ok(_apply_verbosity({
        "session_id": session_id, "skills_available": skills,
        "count": len(skills),
        "orchestrator_root": a_p_str,
        "note": "Lists available skills. Per-session 'loaded' tracking R+1 pending.",
        "usage_hint": "R16.8: copy `read_file_range_payload` directly — absolute path mode (no project_path needed). Or use `read_file_range_payload_relative` if you prefer the project-rooted form.",
    }, verbosity, "skill_state"))


async def list_projects(limit: int = 26) -> str:
    """V15 R8 B.7: list distinct project_paths from sessions DB; map to [Ab]..[Az]."""
    import sqlite3

    from code_tools import _SESSIONS_DB
    def _q() -> list:
        conn = sqlite3.connect(str(_SESSIONS_DB))
        try:
            rows = conn.execute(
                "SELECT project_path, COUNT(*) as cnt, MAX(started_at) as latest "
                "FROM sessions GROUP BY project_path ORDER BY latest DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [{"project_path": r[0], "session_count": r[1], "latest": r[2]}
                    for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    data = await asyncio.to_thread(_q)
    slots = ["[Ab]", "[Ac]", "[Ad]", "[Ae]", "[Af]", "[Ag]", "[Ah]", "[Ai]", "[Aj]", "[Ak]",
             "[Al]", "[Am]", "[An]", "[Ao]", "[Ap]", "[Aq]", "[Ar]", "[As]", "[At]", "[Au]",
             "[Av]", "[Aw]", "[Ax]", "[Ay]", "[Az]"]
    for i, p in enumerate(data):
        p["slot"] = slots[i] if i < len(slots) else f"[Az+{i - len(slots) + 1}]"
    return _ok({"projects": data, "count": len(data)})


async def get_validation_queue(project_path: str) -> str:
    """V15 R8 B.7: list pending dry_run_ids from _dry_run_store (restored R42 post-patch)."""
    # P19: reject invalid project_path upfront -- without this, a wrong path
    # silently returned ok:true + pending:[] (indistinguishable from "no queue").
    root = Path(project_path).resolve()
    if not root.is_dir():
        return _err(f"Project path not found: {project_path}",
                    error_name="PROJECT_PATH_NOT_FOUND")
    try:
        from code_tools import _DRY_RUN_STORE
    except ImportError:
        try:
            from code_tools import _dry_run_store as _DRY_RUN_STORE
        except ImportError:
            _DRY_RUN_STORE = {}
    pending = []
    for k, v in (_DRY_RUN_STORE or {}).items():
        if isinstance(v, dict) and v.get("project_path") == project_path:
            pending.append({
                "dry_run_id": k,
                "tool": v.get("tool", ""),
                "file_path": v.get("file_path", ""),
                "created_at": v.get("created_at", ""),
            })
    return _ok({"project_path": project_path, "pending": pending, "count": len(pending)})



# ---------------------------------------------------------------------------
# CSLt A-first governance runtime (Matrix request-time classifier + metrics)
# ---------------------------------------------------------------------------

async def classify_ca_action(
    tool_name: str = "",
    action_type: str = "",
    transport: str = "native",
    scope: str = "",
    target: str = "",
    project_path: str = "",
    agent_id: str = "",
) -> str:
    """T1: classify one action against .council/CA_TOOL_POLICY_MATRIX.json (CSLt)."""
    try:
        from orchestrator.core.ca_policy_matrix import classify_action
        return _ok(classify_action(
            tool_name=tool_name,
            action_type=action_type,
            transport=transport,
            scope=scope,
            target=target,
            project_path=project_path,
            agent_id=agent_id,
        ))
    except Exception as e:
        return _err(str(e), error_name="INTERNAL_ERROR")


async def ca_policy_metrics(
    project_path: str = "",
    session_id: str = "",
    agent_id: str = "",
    time_window_hours: float = 24.0,
) -> str:
    """T3: aggregate Matrix bypass counters from sessions.db events (CSLt metrics)."""
    try:
        from code_tools import _SESSIONS_DB
        from orchestrator.core.ca_policy_matrix import query_session_metrics
        result = query_session_metrics(
            _SESSIONS_DB,
            session_id=session_id or "",
            project_path=project_path or "",
            agent_id=agent_id or "",
            time_window_hours=float(time_window_hours) if time_window_hours is not None else None,
        )
        if result.get("ok"):
            return _ok(result)
        return _err(
            result.get("error") or "metrics failed",
            error_name=result.get("error_name") or "INTERNAL_ERROR",
            **{k: v for k, v in result.items() if k not in ("ok", "error", "error_name")},
        )
    except Exception as e:
        return _err(str(e), error_name="INTERNAL_ERROR")


# AUDIT-KEEP(hermes), Anh 2026-09-29: the tool name and the "hermes" default are historical
# labels; the gate serves every CA and no decision reads agent_id (see
# ca_policy_matrix.hermes_host_gate). Harmless -- no action.
async def hermes_host_gate_check(
    agent_id: str = "hermes",
    project_path: str = "",
    target: str = "",
    scope: str = "",
    tool_name: str = "",
    action_type: str = "",
    transport: str = "native",
) -> str:
    """T1: Hermes/peer host gate by project vs outside-project scope (CSLt axis 3)."""
    try:
        from orchestrator.core.ca_policy_matrix import classify_action
        c = classify_action(
            tool_name=tool_name,
            action_type=action_type,
            transport=transport,
            scope=scope,
            target=target,
            project_path=project_path,
            agent_id=agent_id or "hermes",
        )
        hard_stop = bool(c.get("preflight_hard_stop") or c.get("is_forbidden_native"))
        host = c.get("host_gate") or {}
        if not hard_stop and isinstance(host, dict) and host.get("allowed") is False:
            hard_stop = True
        preferred = list(c.get("preferred_via_a") or [])[:12]
        err_name = c.get("error_name")
        if hard_stop and not err_name:
            if isinstance(host, dict) and str(host.get("decision") or "").startswith("deny"):
                err_name = "HOST_GATE_DENY"
            else:
                err_name = "FORBIDDEN_NATIVE"
        payload = {
            "host_gate": host,
            "classification": {
                "task_class": c.get("task_class"),
                "native_policy": c.get("native_policy"),
                "is_forbidden_native": c.get("is_forbidden_native"),
                "is_allowed_native": c.get("is_allowed_native"),
                "is_via_a": c.get("is_via_a"),
                "ca_next_action": c.get("ca_next_action"),
            },
            "north_star": c.get("north_star"),
            "preferred_via_a": preferred,
            "preflight_hard_stop": hard_stop,
            "ca_next_action": c.get("ca_next_action"),
        }
        if hard_stop:
            payload["error_name"] = err_name
            payload["severity"] = "error"
            payload["retryable"] = False
            payload["reason"] = (
                f"preflight hard-stop: use preferred_via_a={preferred}; "
                f"do not execute native write"
            )
            return _ok(payload)
        return _ok(payload)
    except Exception as e:
        return _err(str(e), error_name="INTERNAL_ERROR")


async def normalize_ca_registration(dry_run: bool = True) -> str:
    """T3: set mode=A + transport_policy machine-checkable on all agents_registry.json rows."""
    try:
        from pathlib import Path

        from code_tools import _AGENTS_REGISTRY_PATH, _atomic_write_json
        from orchestrator.core.ca_policy_matrix import (
            load_agents_registry_file,
            normalize_registry,
        )
        path = Path(_AGENTS_REGISTRY_PATH)
        if not path.exists():
            return _err(f"registry not found: {path}", error_name="FILE_NOT_FOUND")
        # A31: BOM-safe decode. Fail-loud is intentional and REQUIRED here — this
        # is a read-modify-write path; a fail-safe {} on corrupt input would
        # normalize an empty registry and overwrite every agent.
        reg = load_agents_registry_file(path)
        new_reg, changes = normalize_registry(reg)
        if dry_run:
            return _ok({
                "dry_run": True,
                "changes": changes,
                "agent_count": len(new_reg.get("agents") or {}),
                "preview": {
                    aid: {
                        "mode": rec.get("mode"),
                        "transport_policy": rec.get("transport_policy"),
                    }
                    for aid, rec in (new_reg.get("agents") or {}).items()
                },
                "ca_next_action": "call_normalize_ca_registration_with_dry_run_false_to_apply",
            })
        bak = path.with_suffix(path.suffix + ".bak_cslt")
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        _atomic_write_json(path, new_reg, indent=2)
        return _ok({
            "dry_run": False,
            "applied": True,
            "changes": changes,
            "backup_path": str(bak),
            "registry_path": str(path),
            "agent_count": len(new_reg.get("agents") or {}),
        })
    except Exception as e:
        return _err(str(e), error_name="INTERNAL_ERROR")
