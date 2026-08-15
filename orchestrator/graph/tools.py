"""Async tool wrappers for the CBM graph — bridges graph modules to code_tools' HTTP/JSON-string contract (Phase 1 Step 11).

Each wrapper is async, returns a JSON-encoded string (the code_tools convention),
and is fail-open: graph disabled / DB missing / any error returns a structured
payload rather than raising. These are registered as T1 tools via register_tool().
"""
import asyncio
import glob
import json as _json
import os
import subprocess
import sys
import time

from . import cycle_point as _cycle_point
from . import indexer as _indexer
from . import status as _status
from . import query as _query
from .extractors.vulture_ruff import _merge_dedup, scan_ruff, scan_vulture


def _ok(payload: dict) -> str:
    return _json.dumps({"ok": True, "result": payload}, ensure_ascii=False)


def _err(name: str, exc: Exception) -> str:
    return _json.dumps({"ok": False, "error_name": type(exc).__name__,
                        "error": str(exc)}, ensure_ascii=False)


def _clean_project_path(project_path: str) -> tuple[str, str | None]:
    """Normalize a project path at the tool boundary (RISK-3 guard).

    A backslash Windows path can arrive with an embedded NUL when an upstream
    layer unescapes sequences like "D:\\0..." — the original character is
    unrecoverable, so reject with a clear error instead of crashing in
    os.makedirs. Backslashes are normalized to forward slashes.
    """
    if "\x00" in project_path:
        return "", ("project_path contains an embedded NUL character "
                    "(mangled backslash escape) — resend using forward slashes, e.g. D:/path/to/project")
    return project_path.replace("\\", "/"), None



_GRAPH_WRITE_LOCKS: dict = {}


def _graph_write_lock(project_path: str):
    """R50 (2026-08-12): serialize graph WRITES (cbm_index / cbm_incremental_index) per
    project. Concurrent rebuilds contend on the single graph.db writer; under extreme load
    the SQLite busy-timeout is exceeded -> OperationalError ('database is locked', surfaced
    at 100 concurrent workers). One writer at a time (readers stay concurrent via WAL)
    removes the contention. Lazy per-loop asyncio.Lock."""
    lk = _GRAPH_WRITE_LOCKS.get(project_path)
    if lk is None:
        lk = asyncio.Lock()
        _GRAPH_WRITE_LOCKS[project_path] = lk
    return lk
async def cbm_index(project_path: str = "", scope: str = "core", **aliases) -> str:
    """T1: index project scope into the graph DB (nodes/edges/FTS5). Read-only on source."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        async with _graph_write_lock(project_path):
            result = await asyncio.to_thread(
                _indexer.index_project, project_path, scope=scope, project_root=project_path
            )
        return _ok(result)
    except Exception as e:
        return _err("cbm_index", e)


async def cbm_graph_status(project_path: str = "", **aliases) -> str:
    """T1: report graph freshness, counts, WAL state, stale files. Read-only."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    # F-01: a nonexistent project root must NOT fall through to _status.graph_status,
    # which would otherwise report enabled=True + freshness="not_indexed" and trick
    # callers into graph operations under false readiness. Distinguish a missing
    # path (caller bug) from a valid-but-unindexed project (documented fail-open).
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    try:
        result = await asyncio.to_thread(_status.graph_status, project_path)
        return _ok(result)
    except Exception as e:
        return _err("cbm_graph_status", e)


async def cbm_nodes_query(project_path: str = "", kind: str = "", name_prefix: str = "",
                           file_contains: str = "", limit: int = 100, offset: int = 0,
                           **aliases) -> str:
    """T1: browse/filter raw graph nodes (kind/name_prefix/file filter + cursor)."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        result = await asyncio.to_thread(
            _query.nodes_query, project_path, kind=kind, name_prefix=name_prefix,
            file_contains=file_contains, limit=limit, offset=offset)
        return _ok(result)
    except Exception as e:
        return _err("cbm_nodes_query", e)


async def cbm_edges_query(project_path: str = "", kind: str = "", source_contains: str = "",
                          target_contains: str = "", limit: int = 100, offset: int = 0,
                          **aliases) -> str:
    """T1: browse/filter raw graph edges (kind/source/target filter + cursor)."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        result = await asyncio.to_thread(
            _query.edges_query, project_path, kind=kind, source_contains=source_contains,
            target_contains=target_contains, limit=limit, offset=offset)
        return _ok(result)
    except Exception as e:
        return _err("cbm_edges_query", e)


async def cbm_trace_path(project_path: str = "", start_node_id: str = "",
                         direction: str = "out", depth: int = 5, **aliases) -> str:
    """T1: bounded graph traversal from a start node. depth<=5, node cap, timeout 2s. Fail-open."""
    if not project_path or not start_node_id:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path and start_node_id required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        result = await asyncio.to_thread(
            _query.cbm_trace_path, project_path, start_node_id,
            direction=direction, depth=depth
        )
        return _ok(result)
    except Exception as e:
        return _err("cbm_trace_path", e)


# ─── Phase 3 tools (Steps 15-17) ─────────────────────────────────────────────

async def cbm_template_query(project_path: str = "", template: str = "",
                             name: str = "", node_id: str = "", module: str = "",
                             depth: int = 2, limit: int = 50, **aliases) -> str:
    """T1: named graph queries — callers|callees|imports|imported_by|orphan_symbols|module_deps|entry_points|impact_scope."""
    if not project_path or not template:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path and template required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        result = await asyncio.to_thread(
            _query.template_query, project_path, template,
            name=name, node_id=node_id, module=module,
            depth=int(depth or 2), limit=int(limit or 50))
        return _ok(result)
    except Exception as e:
        return _err("cbm_template_query", e)


async def cbm_get_architecture(project_path: str = "", max_files: int = 0,
                               max_symbols: int = 0, timeout_s: float = 0.0,
                               **aliases) -> str:
    """T1: bounded module-level architecture overview with exact file citations. Fail-open."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        # DOCS_GAP-6: optional bound overrides; 0/unset keeps config defaults.
        # P4 verify fix (STT-17): negative values are invalid — floor to 0 so
        # they keep the config default instead of causing immediate truncation
        # (negative int is truthy in Python and would pass through to query.py).
        kwargs = {}
        if max_files and max_files > 0:
            kwargs["max_files"] = int(max_files)
        if max_symbols and max_symbols > 0:
            kwargs["max_symbols"] = int(max_symbols)
        if timeout_s and timeout_s > 0:
            kwargs["timeout_s"] = float(timeout_s)
        result = await asyncio.to_thread(
            _query.cbm_get_architecture, project_path, **kwargs)
        return _ok(result)
    except Exception as e:
        return _err("cbm_get_architecture", e)


async def cbm_dead_code(project_path: str = "", limit: int = 50,
                        scope: str = "full", file_filter: str = "",
                        **aliases) -> str:
    """T1: conservative dead-code candidates with confidence + reason. Advisory only."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        result = await asyncio.to_thread(_cbm_dead_code_dispatch, project_path,
                                         limit=int(limit or 50), scope=scope,
                                         file_filter=file_filter)
        return _ok(result)
    except Exception as e:
        return _err("cbm_dead_code", e)

async def ruff_fix(project_path: str = "", path: str = ".", apply: bool = False,
                   unsafe_fixes: bool = False, **aliases) -> str:
    """Preview or apply Ruff fixes without accepting arbitrary command arguments."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    root = os.path.realpath(project_path)
    target = os.path.realpath(os.path.join(root, path or "."))
    try:
        if os.path.commonpath([root, target]) != root:
            raise ValueError("path must stay inside project_path")
    except ValueError:
        return _json.dumps({"ok": False, "error_name": "PATH_OUTSIDE_PROJECT",
                            "error": "path must stay inside project_path"}, ensure_ascii=False)
    if not os.path.exists(target):
        return _json.dumps({"ok": False, "error_name": "FILE_NOT_FOUND",
                            "error": f"path not found: {path}"}, ensure_ascii=False)

    def _snapshot(candidate: str):
        files = [candidate] if os.path.isfile(candidate) else []
        if not files:
            ignored = {".git", ".venv", "venv", "venv312", "__pycache__"}
            for base, dirs, names in os.walk(candidate):
                dirs[:] = [directory for directory in dirs if directory not in ignored]
                files.extend(os.path.join(base, name) for name in names if name.endswith(".py"))
        return {file: (stat.st_size, stat.st_mtime_ns) for file in files for stat in [os.stat(file)]}

    before = _snapshot(target) if apply else None
    command = [sys.executable, "-m", "ruff", "check", target, "--fix"]
    if not apply:
        command.append("--diff")
    if unsafe_fixes:
        command.append("--unsafe-fixes")
    completed = await asyncio.to_thread(
        subprocess.run, command, cwd=root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, check=False,
    )
    output = completed.stdout or ""
    changed = bool(output.strip()) if not apply else before != _snapshot(target)
    return _ok({
        "mode": "apply" if apply else "preview",
        "applied": bool(apply and completed.returncode in (0, 1)),
        "unsafe_fixes": bool(unsafe_fixes),
        "path": os.path.relpath(target, root).replace("\\", "/"),
        "diff": output if not apply else "",
        "stdout": output if apply else "",
        "stderr": completed.stderr or "",
        "exit_code": completed.returncode,
        "changed": changed,
    })


def _detect_majority_language(project_path: str) -> str:
    py_count = 0
    js_ts_count = 0
    ignored_dirs = {".git", ".venv", "venv", "venv312", "__pycache__", "node_modules"}
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for filename in files:
            if filename.endswith(".py"):
                py_count += 1
            elif filename.endswith((".js", ".jsx", ".ts", ".tsx")):
                js_ts_count += 1
    return "python" if py_count > js_ts_count else "other"


def _cbm_dead_code_ast_p0(project_path: str, *, limit: int = 50) -> dict:
    result = _query.dead_code_candidates(project_path, limit=limit)
    result["engine"] = "ast-p0-v1"
    return result


def _cbm_dead_code_dispatch(project_path: str, *, limit: int = 50,
                            scope: str = "full", file_filter: str = "") -> dict:
    scan_root = os.path.join(project_path, "orchestrator") if scope == "core" else project_path
    files: list[str] | None = None
    if file_filter:
        pattern = file_filter
        if not os.path.isabs(pattern):
            pattern = os.path.join(scan_root, pattern)
        files = [
            path for path in glob.glob(pattern, recursive=True)
            if os.path.isfile(path) and path.endswith(".py")
        ]
    if _detect_majority_language(scan_root) != "python":
        return _cbm_dead_code_ast_p0(scan_root, limit=limit)

    t0 = time.monotonic()
    limit = max(1, min(int(limit or 50), 200))
    if files == []:
        candidates = []
    else:
        v = scan_vulture(scan_root, min_conf=60, files=files)
        r = scan_ruff(scan_root, files=files)
        candidates = _merge_dedup(v + r)
    candidates.sort(
        key=lambda item: (
            -float(item.get("confidence", 0.0)),
            str(item.get("file_path") or item.get("file") or ""),
            int(item.get("start_line") or item.get("line") or 0),
            str(item.get("symbol") or item.get("name") or ""),
        )
    )
    categories = {}
    for c in candidates:
        cat = c.get("category", "lint")
        if cat not in categories:
            categories[cat] = {"count": 0, "candidates": []}
        categories[cat]["count"] += 1
        if len(categories[cat]["candidates"]) < limit:
            categories[cat]["candidates"].append(c)
    out = {
        "enabled": True,
        "candidates": candidates[:limit],
        "count": min(len(candidates), limit),
        "categories": categories,
        "error": None,
        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
        "disclaimer": "advisory only — Ruff findings require manual verify before action",
        "engine": "vulture+ruff",
    }
    return out


async def cbm_incremental_index(project_path: str = "", scope: str = "full",
                                **aliases) -> str:
    """T1: incremental graph update — reindex only changed/added/deleted/renamed files."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        async with _graph_write_lock(project_path):
            result = await asyncio.to_thread(
                _indexer.incremental_index, project_path, scope=scope,
                project_root=project_path)
        return _ok(result)
    except Exception as e:
        return _err("cbm_incremental_index", e)


async def cbm_cycle_point(project_path: str = "", **aliases) -> str:
    """T1: dependency cycles + levelization + geometric-mean quality signal. Fail-open."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    # Same F-01 guard as cbm_graph_status: a nonexistent root is a caller bug and
    # must not be reported as a valid-but-unindexed project.
    if not os.path.isdir(project_path):
        return _json.dumps({"ok": False, "error_name": "INVALID_PROJECT_PATH",
                            "error": f"project_path is not an existing directory: {project_path}"},
                           ensure_ascii=False)
    try:
        result = await asyncio.to_thread(_cycle_point.compute_cycle_point, project_path)
        return _ok(result)
    except Exception as e:
        return _err("cbm_cycle_point", e)


async def cbm_council_context(project_path: str = "", name: str = "",
                              depth: int = 2, file_path: str = "",
                              **aliases) -> str:
    """T1: bounded advisory council graph context pack (Step 21). Fail-open;
    the leader embeds the block in worker prompts explicitly — never auto-injected.

    STT-2: file_path param adds file-level context mode (all symbols in the file
    + direct callers). Takes precedence over changes mode when name is empty."""
    if not project_path:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": "project_path required"}, ensure_ascii=False)
    project_path, path_err = _clean_project_path(project_path)
    if path_err:
        return _json.dumps({"ok": False, "error_name": "SCHEMA_VIOLATION",
                            "error": path_err}, ensure_ascii=False)
    try:
        result = await asyncio.to_thread(_query.council_graph_context, project_path,
                                         name=name, depth=int(depth or 2),
                                         file_path=file_path)
        return _ok(result)
    except Exception as e:
        return _err("cbm_council_context", e)


__all__ = ["cbm_index", "cbm_graph_status", "cbm_trace_path",
           "cbm_template_query", "cbm_get_architecture", "cbm_dead_code",
           "cbm_incremental_index", "cbm_council_context", "cbm_cycle_point"]
