"""Adaptive read APIs: one scope, bounded batch, and graph-first context pack."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from orchestrator.fileops import common as fo
from orchestrator.server.executor_lanes import run_in_executor_lane
from . import adaptive

SMALL_FILE_THRESHOLD = 240
BATCH_MAX_ITEMS = 20
BATCH_MAX_TOKENS = 12000


def _language(path: Path) -> str:
    return {".py": "python", ".js": "js", ".mjs": "js", ".cjs": "js",
            ".ts": "ts", ".tsx": "ts", ".jsx": "js", ".html": "html",
            ".css": "css", ".md": "markdown", ".json": "json"}.get(
                path.suffix.lower(), "text")


def _relative(path: Path, project_path: str | None) -> str:
    if project_path:
        try:
            return str(path.relative_to(Path(project_path).resolve())).replace("\\", "/")
        except ValueError:
            pass
    return str(path)


async def read_file_range(
        file_path: str, start_line: int = 1, end_line: int | None = None,
        project_path: str | None = None, max_lines: int = fo.MAX_READ_LINES,
        force_reseed: bool = False, scope: str = "range", symbol: str = "",
        section: str = "", related_query: str = "", query: str = "",
        token_budget: int = 0, small_file_threshold: int = SMALL_FILE_THRESHOLD,
        include_next_ranges: bool = True, content_hash: str = "",
        force_refresh: bool = False) -> str:
    """Read a line range or structural scope without mutating derived project state."""
    from .fs import _missing_file_response, _seed_read_token_detailed

    try:
        fp = fo.resolve_project_file(project_path, file_path)
    except Exception as exc:
        # Fallback: if path is relative and project_path exists, try join
        if project_path and not os.path.isabs(file_path):
            try:
                fp = (Path(project_path).resolve() / file_path).resolve()
            except Exception:
                return fo.json_error(str(exc),
                                     hint="Use project_path with a relative file_path inside the project.")
        else:
            return fo.json_error(str(exc),
                                 hint="Use project_path with a relative file_path inside the project.")
    if not fp.exists():
        # CAN-053 soft fix: force_reseed=true after a cache miss should not
        # keep serving the same stale FILE_NOT_FOUND. Clear the negative cache
        # entry so the next probe re-resolves from disk instead of tripping the
        # 3-miss circuit breaker on a path that now exists.
        if force_reseed:
            from .fs import _NEG_PATH_CACHE, _MISS_STREAK
            _root_key = str(project_path or '').replace("\\", "/").rstrip("/").lower()
            _rel_key = str(file_path).replace("\\", "/").lower()
            _NEG_PATH_CACHE.pop((_root_key, _rel_key), None)
            _streak = _MISS_STREAK.get(_root_key, [])
            if _streak:
                _MISS_STREAK[_root_key] = []
        return _missing_file_response(fp, file_path, project_path)
    # Use os.path.isdir rather than Path.is_dir: unstable-read tests replace
    # Path.stat with a minimal size/mtime record that intentionally has no st_mode.
    if os.path.isdir(str(fp)):
        return fo.json_error(
            f"read_file_range requires a file, but this path is a directory: {file_path}",
            error_name="PATH_IS_DIRECTORY", type="INVALID_PATH_TYPE",
            severity="ERROR", retryable=False,
            path=str(fp),
            suggested_tools=["list_structure", "get_folder_size"],
            hint="Use list_structure to inspect entries or get_folder_size to measure the directory.",
            ca_next_action="choose_directory_tool")
    if fo.is_binary_file(fp, allow_utf16_text=True):
        try:
            size_bytes = fp.stat().st_size
        except OSError:
            size_bytes = None
        return fo.json_error(
            f"Binary file unsupported by read_file_range: {file_path}",
            error_name="BINARY_FILE_UNSUPPORTED", type="UNSUPPORTED_MEDIA_TYPE",
            severity="ERROR", retryable=False,
            suffix=fp.suffix.lower(), size_bytes=size_bytes,
            suggested_tools=["check_server_path"],
            hint="read_file_range only decodes text. Use check_server_path for file metadata.",
            ca_next_action="inspect_binary_metadata")

    try:
        requested_max = int(max_lines or fo.MAX_READ_LINES)
    except (TypeError, ValueError):
        requested_max = fo.MAX_READ_LINES
    effective_max = max(1, min(requested_max, fo.MAX_READ_LINES_HARD_CAP))
    try:
        requested_budget = max(0, int(token_budget or 0))
    except (TypeError, ValueError):
        requested_budget = 0
    try:
        threshold = max(1, min(int(small_file_threshold or SMALL_FILE_THRESHOLD),
                               fo.MAX_READ_LINES_HARD_CAP))
    except (TypeError, ValueError):
        threshold = SMALL_FILE_THRESHOLD
    related = related_query or query

    def _read() -> dict:
        doc, cache_meta = adaptive.get_document(
            fp, project_path, content_hash=content_hash, force_refresh=force_refresh)
        selection = adaptive.resolve_selection(
            doc, scope=scope, start_line=start_line, end_line=end_line,
            symbol=symbol, section=section, related_query=related)
        if not selection.get("ok"):
            selection.update({
                "file": str(fp), "project_path": str(Path(project_path).resolve())
                if project_path else None, "content_hash": doc.content_hash,
                "cache_hit": cache_meta["cache_hit"],
                "total_lines": len(doc.lines),
                "read_token_seeded": False, "derived_state_touched": False,
            })
            return selection

        total = len(doc.lines)
        stat_after = fp.stat()
        reseeded = False
        reseed_detail: dict = {}
        if doc.stable or force_reseed:
            if force_reseed:
                import logging
                logging.getLogger("[A]").warning(
                    "force_reseed=true file_path=%s caller=read_file_range", str(fp))
            reseeded, reseed_detail = _seed_read_token_detailed(
                fp, project_path, doc.text, stat_after, force_reseed,
                precomputed_hash=doc.content_hash)
        # P11 (campaign-1608) FAIL-LOUD: an EXPLICIT force_reseed that could not
        # seed the token must not return ok:true — that is the silent dead loop
        # where write_fix keeps failing FILE_CHANGED_OUTSIDE_ORCHESTRATOR and its
        # own recovery hint (force_reseed) reports success.
        # P11b: say WHICH of the three failures happened. The first cut printed
        # the no-root message unconditionally, so a token-store failure was
        # reported as "no project_path was supplied" while telling the caller to
        # supply the project_path it had already supplied — P11's dead loop
        # rebuilt one layer up, except here the caller believes the diagnosis.
        if force_reseed and not reseeded:
            reason = reseed_detail.get("reason") or "no_project_root"
            error_name, retryable = "RESEED_FAILED", reason != "upsert_failed"
            if reason == "no_project_root":
                error = ("force_reseed could not seed the write-freshness token: "
                         "no project_path was supplied and no project root could "
                         "be inferred from the file_path.")
                hint = ("Pass project_path (the project root that owns this file) "
                        "together with file_path and force_reseed=true. The token "
                        "is keyed by (project_root, rel_path); without a resolvable "
                        "root there is nothing to key it to.")
                next_action = "retry_read_file_range_with_project_path"
            elif reason == "outside_root":
                error = ("force_reseed could not seed the write-freshness token: "
                         "the file is not inside the project root that was resolved "
                         f"({reseed_detail.get('root') or '?'}).")
                hint = ("Pass the project_path that actually contains this file. "
                        "The token is keyed by (project_root, rel_path), so a file "
                        "outside the root has no key.")
                next_action = "retry_read_file_range_with_correct_project_path"
            elif reason == "contention":
                # F9 (2026-09-28): the one TRANSIENT failure, answered with the A40
                # contract verbatim so the dispatcher attaches the same "back off,
                # then resend the identical payload" retry_sequence it gives every
                # other session-store tool. It used to land in the branch below,
                # whose hint says a retry fails the same way -- false for a queue
                # that drains in seconds.
                contention = reseed_detail["contention"]
                error_name, retryable = contention["error_name"], contention["retryable"]
                error = ("force_reseed could not seed the write-freshness token: the "
                         "session write queue is busy "
                         f"({reseed_detail.get('error') or 'unknown'}).")
                hint = contention["hint"]
                next_action = contention["ca_next_action"]
            else:
                error = ("force_reseed resolved the project root but the read-token "
                         "store refused the write: "
                         f"{reseed_detail.get('error') or 'unknown'}.")
                hint = ("This is NOT a missing project_path — the root resolved "
                        "fine. Retrying the same call will fail the same way. "
                        "Check the session store (sessions.db locked / disk full / "
                        "schema mismatch) before retrying.")
                next_action = "inspect_session_store"
            return {
                "ok": False,
                "error": error,
                "error_name": error_name,
                "reseed_failure_reason": reason,
                "reseed_error": reseed_detail.get("error", ""),
                "file": str(fp),
                "project_path": str(Path(project_path).resolve()) if project_path else None,
                "read_token_seeded": False,
                "content_hash": doc.content_hash,
                "total_lines": total,
                "hint": hint,
                "ca_next_action": next_action,
                "retryable": retryable,
                "derived_state_touched": False,
            }

        requested_scope = (scope or "range").lower()
        if requested_scope == "file_summary":
            summary_budget = requested_budget or 4000
            max_items = max(5, min(40, summary_budget // 60))
            summary = adaptive.file_summary(doc, max_items=max_items)
            result_text = json.dumps(summary, ensure_ascii=False, indent=2)
            while adaptive.estimate_tokens(result_text) > summary_budget and max_items > 5:
                max_items = max(5, max_items // 2)
                summary = adaptive.file_summary(doc, max_items=max_items)
                result_text = json.dumps(summary, ensure_ascii=False, indent=2)
            used_tokens = adaptive.estimate_tokens(result_text)
            next_ranges = adaptive.rank_next_ranges(
                doc, selection, related_query=related, limit=12) if include_next_ranges else []
            response = {
                "ok": True, "result": result_text, "summary": summary,
                "file": str(fp), "project_path": str(Path(project_path).resolve())
                if project_path else None, "file_group": None, "lang": _language(fp),
                "total_lines": total, "shown": "file_summary",
                "range_id": f"{fp.name}:summary", "bounded": True,
                "max_lines": effective_max, "max_lines_cap": fo.MAX_READ_LINES_HARD_CAP,
                "max_lines_default": fo.MAX_READ_LINES,
                "scope_requested": requested_scope, "scope_resolved": "file_summary",
                "selection": selection, "content_hash": doc.content_hash,
                "range_ref": f"{_relative(fp, project_path)}@{doc.content_hash[:12]}:summary",
                "cache_hit": cache_meta["cache_hit"],
                "cache_waited": cache_meta["cache_waited"],
                "estimated_tokens": used_tokens, "token_budget": summary_budget,
                "token_estimator": "utf8_bytes/4", "auto_full_file": False,
                "truncated": used_tokens > summary_budget,
                "next_ranges": next_ranges, "read_token_seeded": reseeded,
                "derived_state_touched": False,
            }
        else:
            scope_start = int(selection["start_line"])
            scope_end = int(selection["end_line"])
            explicit_legacy = (
                requested_scope == "range" and
                (end_line is not None or int(start_line or 1) != 1 or
                 requested_max != fo.MAX_READ_LINES))
            auto_full = bool(
                requested_scope == "range" and not explicit_legacy and
                total <= threshold and total <= fo.MAX_READ_LINES_HARD_CAP)
            line_cap = total if auto_full else effective_max
            available = doc.lines[max(0, scope_start - 1):max(0, scope_end)]
            selected, used_tokens = adaptive.fit_numbered_lines(
                available, scope_start, max_lines=line_cap,
                token_budget=requested_budget)
            shown_start = scope_start
            shown_end = scope_start + len(selected) - 1
            if not selected:
                shown_end = scope_start - 1
            result_text = adaptive.numbered_text(selected, scope_start)
            truncated = shown_end < scope_end
            next_ranges = adaptive.rank_next_ranges(
                doc, selection, related_query=related, limit=12,
                shown_end=shown_end) if include_next_ranges else []
            response = {
                "ok": True, "result": result_text, "file": str(fp),
                "project_path": str(Path(project_path).resolve())
                if project_path else None, "file_group": None, "lang": _language(fp),
                "total_lines": total, "shown": f"{shown_start}-{shown_end}",
                "range_id": f"{fp.name}:{shown_start}p{len(selected)}",
                "bounded": True, "max_lines": effective_max,
                "max_lines_cap": fo.MAX_READ_LINES_HARD_CAP,
                "max_lines_default": fo.MAX_READ_LINES,
                "scope_requested": requested_scope,
                "scope_resolved": selection.get("scope", requested_scope),
                "selection": selection, "content_hash": doc.content_hash,
                "range_ref": (f"{_relative(fp, project_path)}@{doc.content_hash[:12]}"
                              f":L{shown_start}-L{shown_end}"),
                "cache_hit": cache_meta["cache_hit"],
                "cache_waited": cache_meta["cache_waited"],
                "estimated_tokens": used_tokens,
                "token_budget": requested_budget or None,
                "token_estimator": "utf8_bytes/4",
                "auto_full_file": auto_full, "small_file_threshold": threshold,
                "truncated": truncated, "next_ranges": next_ranges,
                "read_token_seeded": reseeded, "derived_state_touched": False,
            }
            if total == 0:
                # A genuinely empty file is a valid read, not an inverted range.
                # Its token still protects the observed empty file from stale writes.
                response.update({
                    "empty_file": True, "shown": "empty_file",
                    "range_id": f"{fp.name}:empty", "range_ref": None,
                    "selection": {**selection, "start_line": None, "end_line": None},
                })
        # F8 (2026-09-28): a read the token store refused is still a good read,
        # but read_token_seeded:false alone reads exactly like a read that never
        # tried to seed (unstable document, no project root). Say which -- only on
        # this path, so a clean read pays nothing.
        seed_reason = reseed_detail.get("reason")
        if seed_reason == "contention":
            contention = reseed_detail["contention"]
            response["read_token_seed_failure"] = {
                "reason": seed_reason, "error_name": contention["error_name"],
                "retryable": contention["retryable"],
                "ca_next_action": contention["ca_next_action"]}
        elif seed_reason == "upsert_failed":
            response["read_token_seed_failure"] = {
                "reason": seed_reason, "error": reseed_detail.get("error", ""),
                "retryable": False, "ca_next_action": "inspect_session_store"}
        if cache_meta["content_hash_mismatch"]:
            response["content_hash_mismatch"] = True
            response["warn"] = "supplied_content_hash_stale"
        if not doc.stable:
            response["read_unstable"] = True
            response["warn"] = "read_unstable=true"
        return response

    return json.dumps(await run_in_executor_lane("read_io", _read), ensure_ascii=False)


def _dedupe_key(request: dict) -> tuple:
    return (str(request.get("file_path", "")).replace("\\", "/").lower(),
            request.get("scope", "range"), int(request.get("start_line", 1) or 1),
            request.get("end_line"), request.get("symbol", ""),
            request.get("section", ""))


async def read_context_batch(project_path: str, requests: list[dict],
                             token_budget: int = 6000, max_items: int = BATCH_MAX_ITEMS,
                             small_file_threshold: int = SMALL_FILE_THRESHOLD,
                             include_next_ranges: bool = True) -> str:
    """Read several files/scopes in one call under one deterministic token budget."""
    if not isinstance(requests, list) or not requests:
        return json.dumps({
            "ok": False, "error_name": "SCHEMA_VIOLATION",
            "error": "requests must be a non-empty array",
            "ca_next_action": "pass_requests_array",
        })
    try:
        limit = max(1, min(int(max_items or BATCH_MAX_ITEMS), BATCH_MAX_ITEMS))
        budget = max(64, min(int(token_budget or 6000), BATCH_MAX_TOKENS))
    except (TypeError, ValueError):
        limit, budget = BATCH_MAX_ITEMS, 6000

    indexed = []
    seen: set[tuple] = set()
    duplicate_count = 0
    for index, raw in enumerate(requests):
        if not isinstance(raw, dict):
            indexed.append((999999, index, {"_invalid": True, "value": raw}))
            continue
        request = dict(raw)
        key = _dedupe_key(request)
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        try:
            priority = int(request.get("priority", 100))
        except (TypeError, ValueError):
            priority = 100
        indexed.append((priority, index, request))
    indexed.sort(key=lambda item: (item[0], item[1]))

    selected_requests = indexed[:limit]
    deferred = [item[2] for item in indexed[limit:]]
    items: list[dict] = []
    next_ranges: list[dict] = []
    used = 0
    for position, (_priority, original_index, request) in enumerate(selected_requests):
        if request.get("_invalid"):
            items.append({"ok": False, "error_name": "SCHEMA_VIOLATION",
                          "error": "request item must be an object",
                          "request_index": original_index})
            continue
        remaining = max(0, budget - used)
        if remaining < 16:
            deferred.append(request)
            continue
        remaining_items = max(1, len(selected_requests) - position)
        item_budget = max(16, remaining // remaining_items)
        allowed = {
            "file_path", "start_line", "end_line", "max_lines", "force_reseed",
            "scope", "symbol", "section", "related_query", "query",
            "content_hash", "force_refresh",
        }
        payload = {key: value for key, value in request.items() if key in allowed}
        payload.update({
            "project_path": project_path,
            "token_budget": item_budget,
            "small_file_threshold": small_file_threshold,
            "include_next_ranges": include_next_ranges,
        })
        if not payload.get("file_path"):
            item = {"ok": False, "error_name": "SCHEMA_VIOLATION",
                    "error": "file_path is required", "request_index": original_index}
        else:
            try:
                item = json.loads(await read_file_range(**payload))
            except TypeError as exc:
                item = {"ok": False, "error_name": "UNSUPPORTED_FIELD",
                        "error": str(exc), "request_index": original_index}
        item["request_index"] = original_index
        item_used = max(0, int(item.get("estimated_tokens", 0) or 0))
        if item_used > remaining:
            item["ok"] = False
            item["error_name"] = "TOKEN_BUDGET_EXCEEDED"
            item["error"] = "Item exceeded remaining batch token budget"
            item["result"] = ""
            item_used = 0
        used += item_used
        items.append(item)
        next_ranges.extend(item.get("next_ranges") or [])

    deduped_next: list[dict] = []
    next_seen: set[tuple] = set()
    for item in next_ranges:
        key = (item.get("file_path"), item.get("start_line"), item.get("end_line"))
        if key in next_seen:
            continue
        next_seen.add(key)
        deduped_next.append(item)

    return json.dumps({
        "ok": True, "items": items, "count": len(items),
        "token_budget": budget, "used_tokens": used,
        "remaining_tokens": max(0, budget - used),
        "within_budget": used <= budget, "token_estimator": "utf8_bytes/4",
        "truncated": bool(deferred), "deferred": deferred,
        "next_ranges": deduped_next[:40], "deduplicated": duplicate_count,
        "cache": adaptive.cache_info(), "derived_state_touched": False,
    }, ensure_ascii=False)


def _graph_nodes(project_path: str, target: str, depth: int,
                 max_files: int, max_nodes: int = 40) -> tuple[list[dict], dict]:
    provenance = {"graph_used": False, "graph_error": None,
                  "graph_truncated": False, "graph_auto_reindex": False}
    if not target:
        return [], provenance
    try:
        from orchestrator.graph.query import cbm_trace_path, graph_find_symbol
        from orchestrator.graph import store as graph_store

        found = graph_find_symbol(project_path, target, limit=max_nodes)
        if found.get("error"):
            provenance["graph_error"] = found["error"]
            return [], provenance
        hits = found.get("results") or []
        if not hits:
            provenance["graph_error"] = "symbol_not_found"
            return [], provenance
        top = next((node for node in hits if node.get("kind") in
                    {"function", "method", "class", "module"}), hits[0])
        trace = cbm_trace_path(
            project_path, top["id"], direction="both",
            depth=max(1, min(int(depth), 5)), max_nodes=max_nodes,
            max_files=max_files)
        node_ids = [top["id"]]
        for edge in trace.get("edges") or []:
            node_ids.extend([edge.get("source"), edge.get("target")])
        node_ids = [node_id for node_id in dict.fromkeys(node_ids) if node_id]
        nodes: list[dict] = []
        conn = graph_store.get_db(project_path, readonly=True)
        try:
            for node_id in node_ids[:max_nodes]:
                row = conn.execute("SELECT * FROM nodes WHERE id=?;", (node_id,)).fetchone()
                if row is None:
                    continue
                nodes.append({
                    "id": row["id"], "kind": row["kind"], "name": row["name"],
                    "qualified_name": row["qualified_name"],
                    "file_path": row["file_path"],
                    "start_line": row["start_line"], "end_line": row["end_line"],
                })
        finally:
            conn.close()
        provenance.update({
            "graph_used": bool(nodes),
            "graph_truncated": bool(trace.get("truncated")),
            "graph_error": trace.get("error"),
            "target_node_id": top.get("id"),
            "nodes_seen": trace.get("nodes_seen", len(nodes)),
        })
        return nodes, provenance
    except Exception as exc:
        provenance["graph_error"] = f"{type(exc).__name__}: {exc}"
        return [], provenance


async def architecture_context_pack(
        project_path: str, symbol: str = "", file_path: str = "",
        query: str = "", depth: int = 2, token_budget: int = 6000,
        max_files: int = 8, max_ranges: int = 20) -> str:
    """Use the existing readonly graph to choose exact structural reads."""
    target = symbol or query
    try:
        files_limit = max(1, min(int(max_files or 8), 20))
        ranges_limit = max(1, min(int(max_ranges or 20), BATCH_MAX_ITEMS))
    except (TypeError, ValueError):
        files_limit, ranges_limit = 8, BATCH_MAX_ITEMS
    nodes, provenance = await asyncio.to_thread(
        _graph_nodes, project_path, target, depth, files_limit)

    requests: list[dict] = []
    ranked_symbols: list[dict] = []
    files_seen: set[str] = set()
    for index, node in enumerate(nodes):
        rel = str(node.get("file_path") or "")
        if not rel or rel in files_seen and len(requests) >= ranges_limit:
            continue
        if rel not in files_seen and len(files_seen) >= files_limit:
            continue
        files_seen.add(rel)
        kind = node.get("kind")
        if kind == "class":
            scope = "class"
        elif kind in {"function", "method"}:
            scope = "function"
        else:
            scope = "file_summary"
        selector = node.get("qualified_name") or node.get("name") or ""
        request = {"file_path": rel, "scope": scope, "priority": index}
        if scope in {"function", "class"}:
            request["symbol"] = selector
            request["start_line"] = int(node.get("start_line") or 1)
        requests.append(request)
        ranked_symbols.append(node)
        if len(requests) >= ranges_limit:
            break

    if file_path and not requests:
        requests.append({"file_path": file_path, "scope": "file_summary", "priority": 0})
        files_seen.add(file_path)
    if not requests:
        return json.dumps({
            "ok": False, "error_name": "ARCHITECTURE_CONTEXT_UNAVAILABLE",
            "error": "No graph seed or file fallback could be resolved",
            "graph_used": False,
            "graph_degraded_reason": provenance.get("graph_error") or "no_seed",
            "recommended_action": "pass_symbol_or_file_path_or_run_cbm_incremental_index",
            "provenance": provenance, "derived_state_touched": False,
        }, ensure_ascii=False)

    batch = json.loads(await read_context_batch(
        project_path, requests, token_budget=token_budget,
        max_items=ranges_limit, include_next_ranges=True))
    failures = [item for item in batch.get("items", []) if not item.get("ok")]
    degraded = provenance.get("graph_error")
    if failures and not degraded:
        degraded = "one_or_more_graph_ranges_did_not_match_current_content"
    return json.dumps({
        "ok": bool(batch.get("ok")) and any(item.get("ok") for item in batch.get("items", [])),  # P18: aggregate ok only when >=1 item read succeeded
        "graph_used": provenance.get("graph_used", False),
        "graph_degraded_reason": degraded,
        "query": query, "symbol": symbol, "depth": max(1, min(int(depth), 5)),
        "ranked_files": list(files_seen),
        "ranked_symbols": ranked_symbols[:ranges_limit],
        "items": batch.get("items", []),
        "next_ranges": batch.get("next_ranges", []),
        "budget": {
            "requested": batch.get("token_budget"),
            "used": batch.get("used_tokens"),
            "remaining": batch.get("remaining_tokens"),
            "within_budget": batch.get("within_budget"),
        },
        "cache": batch.get("cache"), "provenance": provenance,
        "derived_state_touched": False,
    }, ensure_ascii=False)
