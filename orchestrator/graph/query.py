"""Bounded graph traversal + graph find_symbol (Phase 1 Step 9).

Provides:
  - graph_find_symbol(name_or_query): FTS5 + exact match lookup (Step 10 reuses this).
  - cbm_trace_path(start_node_id, direction, depth): BFS bounded traversal.

Hard limits (from config, U4): depth 5, node cap 50, timeout 2s, max_files 50,
max_symbols 200. On limit hit: return partial result with truncated=True,
truncation_reason, files_seen, symbols_seen, elapsed_ms. All advisory — fail-open
on timeout/stale/missing (never raise to caller).
"""
import os
import threading
import time
from collections import deque
from typing import Optional

from .config import (
    graph_enabled, GRAPH_CONTEXT_MAX_FILES, GRAPH_CONTEXT_MAX_SYMBOLS,
    GRAPH_CONTEXT_TIMEOUT_S, COUNCIL_CONTEXT_MAX_FILES, COUNCIL_CONTEXT_MAX_SYMBOLS,
    CBM_AUTO_REINDEX_MAX_STALE,
)
from . import store as _store
from .indexer import incremental_index
from .status import _stale_count
from .liveness import (
    is_entry_point as _is_entry_point,
    dead_callables as _dead_callables,
)

DEFAULT_DEPTH = 5

# F013 fix (2026-07-11): one silent reindex at a time. Concurrent scan workers
# each triggered their own incremental_index (BEGIN IMMEDIATE) on graph.db ->
# lock conflicts + duplicated work. Non-blocking: losers skip and read the
# existing (possibly stale) snapshot — reads never wait on a reindex.
_REINDEX_LOCK = threading.Lock()


def _auto_reindex_if_stale(project_path: str, *, budget_s: float = 2.0) -> dict:
    """Hybrid auto-reindex (A_Q demo 2026-07-06): before bounded reads, silently
    reindex ONLY if stale_files > 0 (a file actually changed on disk). Avoids
    wasteful reindex when freshness is time-stale but content is in sync.

    Fail-open: any error -> return {reindexed: 0, skipped, error} and let the
    caller proceed against the existing (possibly stale) graph — never blocks
    the read. Budget guard (P5 fix): the silent reindex is skipped entirely when
    the stale set exceeds CBM_AUTO_REINDEX_MAX_STALE or the stale check already
    consumed budget_s — large change sets must go through cbm_incremental_index.
    """
    out = {"triggered": False, "reindexed": 0, "skipped": 0, "wall_ms": 0.0, "error": None}
    t0 = time.monotonic()
    try:
        conn = _store.get_db(project_path, readonly=True)
        try:
            stale = _stale_count(conn, project_path)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if stale <= 0:
            return out
        if stale > CBM_AUTO_REINDEX_MAX_STALE:
            out["skipped"] = stale
            out["error"] = (f"stale_files={stale} exceeds "
                            f"CBM_AUTO_REINDEX_MAX_STALE={CBM_AUTO_REINDEX_MAX_STALE}")
            return out
        if time.monotonic() - t0 > budget_s:
            out["error"] = "budget_exhausted_before_reindex"
            return out
        if not _REINDEX_LOCK.acquire(blocking=False):
            # F013: another request is already reindexing — serve the current
            # snapshot instead of stacking a second BEGIN IMMEDIATE writer.
            out["skipped"] = stale
            out["error"] = "reindex_in_flight_elsewhere"
            return out
        try:
            out["triggered"] = True
            res = incremental_index(project_path, project_root=project_path)
            out["reindexed"] = int(res.get("reindexed", 0))
            out["skipped"] = int(res.get("skipped", 0))
            if res.get("errors"):
                out["error"] = f"{len(res['errors'])} errors"
        finally:
            _REINDEX_LOCK.release()
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["wall_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return out


def graph_find_symbol(project_path: str, query: str, *, limit: int = 50) -> dict:
    """Look up symbols by exact name or FTS5 MATCH.

    Fail-open: returns {enabled, results, error} — never raises.
    """
    payload = {"enabled": graph_enabled(), "results": [], "error": None, "elapsed_ms": 0}
    t0 = time.monotonic()
    if not graph_enabled():
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return payload
    try:
        conn = _store.get_db(project_path, readonly=True)
        try:
            # try exact name first
            exact = _store.find_nodes_by_name(conn, query, limit=limit)
            if exact:
                payload["results"] = exact
            else:
                # FTS5 MATCH (escape special chars for safety)
                safe = query.replace('"', '""')
                fts = _store.fts_search_symbols(conn, f'"{safe}"', limit=limit)
                if not fts:
                    # Widen: same quoted phrase, plus a prefix match on its last
                    # token, so `routes_read` also finds `routes_readonly`.
                    # This fallback used to pass `query` RAW into MATCH, which
                    # made FTS5 parse the user's term as query syntax: any dot
                    # (`start_orchestrator.bat`), paren, or empty string raised
                    # `fts5: syntax error`, and `a-b` / `col:val` were read as
                    # COLUMN references ("no such column: b"). The whole graph
                    # channel then returned `error` for an ordinary lookup.
                    # Keep the quotes — they are what makes the term data
                    # instead of syntax.
                    fts = _store.fts_search_symbols(conn, f'"{safe}"*', limit=limit)
                payload["results"] = fts
        finally:
            conn.close()
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"
    payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return payload


def cbm_trace_path(project_path: str, start_node_id: str, *,
                   direction: str = "out", depth: int = DEFAULT_DEPTH,
                   max_nodes: int = GRAPH_CONTEXT_MAX_SYMBOLS,
                   max_files: int = GRAPH_CONTEXT_MAX_FILES,
                   timeout_s: float = GRAPH_CONTEXT_TIMEOUT_S) -> dict:
    """Bounded BFS traversal from a start node. Fail-open.

    Returns: {enabled, start, direction, depth, edges, nodes_seen, files_seen,
              symbols_seen, truncated, truncation_reason, elapsed_ms, error}.
    Limits: depth<=5, nodes<=max_nodes, elapsed<=timeout_s. max_files is a SOFT
    cap: crossing it sets truncated/max_files but only the node/timeout limits
    stop the BFS, so files_seen may slightly exceed max_files (fanout overshoot).
    Cycle-safe via a visited set.
    """
    payload = {
        "enabled": graph_enabled(), "start": start_node_id, "direction": direction,
        "depth": depth, "edges": [], "nodes_seen": 0, "files_seen": 0,
        "symbols_seen": 0, "truncated": False, "truncation_reason": None,
        "elapsed_ms": 0, "error": None,
        # B023: three states, not two. None = we never looked (graph disabled,
        # or the DB never opened), False = we looked and the id is absent, True
        # = resolved. A bool here would report "did not look" as "not found",
        # which is what the GRAPH_ENABLED=0 canary exists to prevent.
        "start_node_resolved": None,
    }
    t0 = time.monotonic()
    if not graph_enabled():
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return payload

    # clamp depth to hard cap
    if depth > DEFAULT_DEPTH:
        depth = DEFAULT_DEPTH
        payload["depth"] = depth
        payload["truncated"] = True
        payload["truncation_reason"] = "depth_clamped"

    visited: set[str] = set()
    files_seen: set[str] = set()
    edges_out: list[dict] = []
    queue: deque[tuple[str, int]] = deque()

    try:
        conn = _store.get_db(project_path, readonly=True)
        try:
            # validate start node exists
            start_row = conn.execute(
                "SELECT id, file_path FROM nodes WHERE id=?;", (start_node_id,),
            ).fetchone()
            if not start_row:
                # B023: `truncation_reason` is kept because
                # orchestrator_server.py:12849 branches on this exact string,
                # but it is no longer the ONLY signal -- it names truncation on
                # a payload whose `truncated` is False, so no documented caller
                # guard reads it. `error` stays None on purpose: the three
                # in-process consumers (council_graph_context,
                # architecture_context_pack, template_query's impact_scope)
                # treat a set `error` as a graph failure, and the graph did not
                # fail -- the question was unanswerable as asked. The refusal
                # belongs at the tool boundary, where a caller reads `ok`.
                payload["start_node_resolved"] = False
                payload["truncation_reason"] = "start_node_not_found"
                payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
                return payload
            payload["start_node_resolved"] = True
            visited.add(start_node_id)
            files_seen.add(start_row["file_path"])
            queue.append((start_node_id, 0))

            while queue:
                if time.monotonic() - t0 > timeout_s:
                    payload["truncated"] = True
                    payload["truncation_reason"] = "timeout"
                    break
                if len(visited) >= max_nodes:
                    payload["truncated"] = True
                    payload["truncation_reason"] = "max_symbols"
                    break
                node_id, cur_depth = queue.popleft()
                if cur_depth >= depth:
                    continue
                adj = _store.neighbors(conn, node_id, direction=direction,
                                       limit=max_nodes)
                for e in adj:
                    edges_out.append({
                        "source": e["source"], "target": e["target"],
                        "kind": e["kind"], "confidence": e["confidence"],
                        "cbm_label": e["cbm_label"], "line": e["line"],
                        "depth": cur_depth + 1,
                    })
                    # follow into target/source depending on direction
                    if direction == "in":
                        nxt = e["source"]
                    elif direction == "out":
                        nxt = e["target"]
                    else:  # both: step to whichever end is not the current node
                        nxt = e["source"] if e["target"] == node_id else e["target"]
                    if nxt and nxt not in visited:
                        visited.add(nxt)
                        # resolve node to track files
                        nrow = conn.execute(
                            "SELECT file_path FROM nodes WHERE id=?;", (nxt,),
                        ).fetchone()
                        if nrow:
                            if len(files_seen) >= max_files:
                                payload["truncated"] = True
                                if payload["truncation_reason"] is None:
                                    payload["truncation_reason"] = "max_files"
                                # don't break inner loop; let outer check stop
                            files_seen.add(nrow["file_path"])
                        queue.append((nxt, cur_depth + 1))
        finally:
            conn.close()

        payload["edges"] = edges_out
        payload["nodes_seen"] = len(visited)
        payload["files_seen"] = len(files_seen)
        payload["symbols_seen"] = len(visited)
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"
        payload["truncated"] = True
        if payload["truncation_reason"] is None:
            payload["truncation_reason"] = "exception"
    payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return payload


__all__ = ["graph_find_symbol", "cbm_trace_path", "DEFAULT_DEPTH",
           "find_symbol_as_search_response", "template_query",
           "cbm_get_architecture", "dead_code_candidates", "TEMPLATES",
           "council_graph_context"]


def find_symbol_as_search_response(name: str, project_path: str, *,
                                   context_lines: int = 0,
                                   limit: int = 50) -> Optional[str]:
    """Adapter for Step 10: produce a find_symbol-compatible JSON response from
    the graph, or None to signal the caller to fall back to its existing chain.

    Returns None when: graph disabled, DB missing, no match, or any error.
    On success returns a JSON string shaped like find_symbol's:
      {"results": [{"file","line","match","range_id"}], "count": N,
       "source": "graph"}

    P2-SYM-03 (2026-08-11): the find_symbol(include='def') call site expects
    DEFINITIONS ONLY. graph_find_symbol returns nodes of every kind
    (module/function/method/class/import/variable), so we (a) over-fetch
    candidates by 4x and filter to definition-shaped kinds (function, method,
    class) at collection time — BEFORE shaping entries — and (b) cap the
    emitted results and tag the response with truncation metadata so the
    caller can detect an 849KB-style blowup and stay bounded.
    """
    if not graph_enabled():
        return None
    import json
    # P2-SYM-03: definition-shaped kinds for include='def'. 'module' nodes are
    # file-level containers (not a symbol definition), 'import' nodes are
    # binding sites (a usage of an external name, not a definition of the
    # queried symbol), and 'variable' nodes are assignments (TT-A-01's
    # find_symbol fallback regex deliberately does not surface them as
    # definitions either). Over-fetch candidates 4x so we still hit the cap
    # after kind-filtering.
    _DEF_KINDS = ("function", "method", "class")
    _RESULT_CAP = max(1, min(int(limit or 50), 50))
    _FETCH_CAP = _RESULT_CAP * 4
    fs = graph_find_symbol(project_path, name, limit=_FETCH_CAP)
    if fs.get("error") or not fs.get("results"):
        return None
    candidates = [n for n in fs["results"] if n.get("kind") in _DEF_KINDS]
    if not candidates:
        return None
    truncated = len(candidates) > _RESULT_CAP
    emitted = candidates[:_RESULT_CAP]
    results = []
    for n in emitted:
        entry = {
            "file": n.get("file_path", ""),
            "line": n.get("start_line", 0),
            "match": f"{n.get('kind','symbol')} {n.get('name','')} -> {n.get('qualified_name','')}",
            "range_id": f"{n.get('file_path','')}:{n.get('start_line',0)}",
        }
        if context_lines > 0:
            # P2-SYM-03: bound body context too — a 50-result list of large
            # docstring/signature bodies can also blow past the response
            # budget even when count is small. Truncate body text so the
            # overall response stays bounded.
            body = (n.get("signature") or n.get("docstring") or "")
            if len(body) > 2000:
                body = body[:2000] + "...[truncated]"
            entry["body"] = body
            entry["range_id"] = f"{entry['range_id']}p{context_lines}"
        results.append(entry)
    response = {
        "results": results,
        "count": len(results),
        "total_count": len(candidates),
        "truncated": truncated,
        "source": "graph",
    }
    if truncated:
        response["truncation_reason"] = "result_cap"
        response["result_cap"] = _RESULT_CAP
    return json.dumps(response, ensure_ascii=False)


# ─── Step 15: template graph queries ─────────────────────────────────────────

TEMPLATES = ("callers", "callees", "imports", "imported_by", "orphan_symbols",
             "module_deps", "entry_points", "impact_scope")

# The filter chain that decides "dead" lives in liveness.py -- one definition
# for cbm_dead_code, orphan_symbols and cycle_point's redundancy dimension. It
# was duplicated here and in cycle_point._dead_ratio, and the copies disagreed:
# cycle_point counted 3167 dead against this report's 664, because it applied
# neither the entry-point nor the framework-dispatch exclusion. Imported under
# the old private names so every call site below reads unchanged.


def _node_brief(row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "name": row["name"],
            "qualified_name": row["qualified_name"], "file_path": row["file_path"],
            "start_line": row["start_line"]}


def _resolve_node(conn, project_path: str, *, node_id: str = "", name: str = ""):
    """Resolve node_id (exact) or name (via graph_find_symbol) to a node row."""
    if node_id:
        return conn.execute("SELECT * FROM nodes WHERE id=?;", (node_id,)).fetchone()
    if name:
        hits = graph_find_symbol(project_path, name, limit=1).get("results") or []
        if hits:
            return conn.execute("SELECT * FROM nodes WHERE id=?;",
                                (hits[0]["id"],)).fetchone()
    return None


def template_query(project_path: str, template: str, *, name: str = "",
                   node_id: str = "", module: str = "", depth: int = 2,
                   limit: int = 50, offset: int = 0) -> dict:
    """Named graph queries without full Cypher (Step 15). Fail-open.

    Returns {enabled, template, results, count, total_count, truncated, error, elapsed_ms}.

    total_count is the TRUE PRE-CAP population for every template, and truncated
    is derived from it: truncated iff offset+count < total_count. B021: these two
    used to be set independently, and module_deps answered count=12 truncated=True
    total_count=0 -- the two fields a paginating caller chooses between,
    contradicting each other. impact_scope is the one exception: it answers a
    single synthetic row whose `truncated` reports the TRACE-DEPTH cap, not a row
    count.

    `offset` pages EVERY paginated template, not just orphan_symbols: the
    derivation above reads offset, so a template that ignored it answered page 1
    forever while still claiming a population the caller could not reach.

    Unsupported template -> clear error listing the supported set.
    """
    t0 = time.monotonic()
    payload = {"enabled": graph_enabled(), "template": template, "results": [],
               "count": 0, "total_count": 0, "truncated": False, "error": None, "elapsed_ms": 0}
    if template not in TEMPLATES:
        payload["error"] = (f"unsupported_template: {template!r}; "
                            f"supported: {', '.join(TEMPLATES)}")
        return payload
    if not graph_enabled():
        return payload
    limit = max(1, min(int(limit or 50), 500)); offset = max(0, int(offset or 0))
    try:
        # F003 fix (2026-07-11): template_query used to skip the staleness
        # check that cbm_get_architecture already ran — orphan/dead-code reads
        # silently served a stale graph after files changed on disk.
        payload["auto_reindex"] = {"triggered": False, "reason": "t1_read_only"}
        conn = _store.get_db(project_path, readonly=True)
        try:
            res: list[dict] = []
            # None = "this branch does not know its population". The tail
            # falls back to len(res) rather than inventing a number.
            total: int | None = None
            if template in ("callers", "callees", "impact_scope"):
                node = _resolve_node(conn, project_path, node_id=node_id, name=name)
                if node is None:
                    payload["error"] = "node_not_found: pass name= or node_id="
                    return payload
                if template == "callers":
                    rows = conn.execute(
                        "SELECT n.*, e.confidence AS edge_confidence, e.line AS call_line "
                        "FROM edges e JOIN nodes n ON n.id=e.source "
                        "WHERE e.target=? AND e.kind='calls' "
                        "ORDER BY n.id, e.line LIMIT ? OFFSET ?;",
                        (node["id"], limit, offset)).fetchall()
                    res = [{**_node_brief(r), "confidence": r["edge_confidence"],
                            "call_line": r["call_line"]} for r in rows]
                    total = conn.execute(
                        "SELECT COUNT(*) FROM edges e JOIN nodes n ON n.id=e.source "
                        "WHERE e.target=? AND e.kind='calls';", (node["id"],)).fetchone()[0]
                elif template == "callees":
                    rows = conn.execute(
                        "SELECT e.target, e.confidence, e.line, e.is_external "
                        "FROM edges e WHERE e.source=? AND e.kind='calls' "
                        "ORDER BY e.target, e.line LIMIT ? OFFSET ?;",
                        (node["id"], limit, offset)).fetchall()
                    res = [{"target": r["target"], "confidence": r["confidence"],
                            "line": r["line"], "external": bool(r["is_external"])}
                           for r in rows]
                    total = conn.execute(
                        "SELECT COUNT(*) FROM edges WHERE source=? AND kind='calls';",
                        (node["id"],)).fetchone()[0]
                else:  # impact_scope: inbound closure = blast radius
                    tr = cbm_trace_path(project_path, node["id"], direction="in",
                                        depth=min(depth, DEFAULT_DEPTH))
                    # Single query per edge + None-safe (BUG-1 fix: was 2x conn.execute
                    # per edge with unprotected fetchone()[0]).
                    rows = [conn.execute(
                        "SELECT file_path FROM nodes WHERE id=?;", (e["source"],),
                    ).fetchone() for e in tr["edges"]]
                    files = sorted({r[0] for r in rows if r})
                    res = [{"start": node["id"], "affected_files": files,
                            "affected_symbols": sorted({e["source"] for e in tr["edges"]}),
                            "edges": len(tr["edges"]), "truncated": tr["truncated"],
                            "truncation_reason": tr["truncation_reason"]}]
                    payload["truncated"] = bool(tr["truncated"])
            elif template == "imports":
                target_mod = module or name
                rows = conn.execute(
                    "SELECT * FROM nodes WHERE kind='import' AND file_path IN "
                    "(SELECT file_path FROM nodes WHERE kind='module' AND "
                    " (qualified_name=? OR file_path=?)) "
                    "ORDER BY id LIMIT ? OFFSET ?;",
                    (target_mod, target_mod, limit, offset)).fetchall()
                res = [_node_brief(r) for r in rows]
                total = conn.execute(
                    "SELECT COUNT(*) FROM nodes WHERE kind='import' AND file_path IN "
                    "(SELECT file_path FROM nodes WHERE kind='module' AND "
                    " (qualified_name=? OR file_path=?));",
                    (target_mod, target_mod)).fetchone()[0]
            elif template == "imported_by":
                target_mod = module or name
                rows = conn.execute(
                    "SELECT DISTINCT n.file_path, n.qualified_name, e.line "
                    "FROM edges e JOIN nodes n ON n.id=e.source "
                    "JOIN nodes m ON m.id=e.target "
                    "WHERE e.kind='imports_module' AND m.kind='module' "
                    "AND m.qualified_name=? "
                    "ORDER BY n.file_path, n.qualified_name, e.line "
                    "LIMIT ? OFFSET ?;", (target_mod, limit, offset)).fetchall()
                res = [{"file_path": r["file_path"], "import": r["qualified_name"],
                        "line": r["line"]} for r in rows]
                total = conn.execute(
                    "SELECT COUNT(*) FROM (SELECT DISTINCT n.file_path, n.qualified_name, e.line "
                    "FROM edges e JOIN nodes n ON n.id=e.source "
                    "JOIN nodes m ON m.id=e.target "
                    "WHERE e.kind='imports_module' AND m.kind='module' "
                    "AND m.qualified_name=?);", (target_mod,)).fetchone()[0]
            elif template == "orphan_symbols":
                # E3: true population via total_count + offset pagination (report 2.6).
                # Fetch a generous headroom so the Python entry-point filter still
                # yields a full page after skipping `offset`; total_count reports the
                # true filtered population so callers need no pagination loop.
                # F004 fix (2026-07-11): low-confidence SUFFIX heuristic edges
                # (0.3-0.7) no longer count as real inbound calls — they made
                # genuinely-orphaned symbols invisible (undercount).
                # W5: LIVE_REF_EXISTS_SQL is the one shared liveness predicate,
                # so `dynamic_ref` (name-keyed dispatch) counts here too.
                # W6: and the Python half of the filter -- entry points, plus the
                # public methods a base class outside the index dispatches by
                # name -- now comes from liveness.py, so this view and the
                # dead-code work-list cannot disagree about a symbol again.
                # No `unexported_only` here: an `__all__` export with no caller
                # is exactly the graph shape this template is asked about.
                filtered = _dead_callables(conn)
                total = len(filtered)
                res = [_node_brief(r) for r in filtered[offset:offset + limit]]
            elif template == "module_deps":
                rows = conn.execute(
                    "SELECT DISTINCT s.file_path AS src, m.qualified_name AS dep "
                    "FROM edges e JOIN nodes s ON s.id=e.source "
                    "JOIN nodes m ON m.id=e.target "
                    "WHERE e.kind='imports_module' "
                    "ORDER BY src, dep LIMIT ? OFFSET ?;", (limit, offset)).fetchall()
                res = [{"file": r["src"], "depends_on": r["dep"]} for r in rows]
                # COUNT over the SAME predicate, DISTINCT included: counting the
                # raw edges instead would report a total the LIMIT-ed SELECT can
                # never reach, so `truncated` would stay true on a complete read.
                total = conn.execute(
                    "SELECT COUNT(*) FROM (SELECT DISTINCT s.file_path, m.qualified_name "
                    "FROM edges e JOIN nodes s ON s.id=e.source "
                    "JOIN nodes m ON m.id=e.target "
                    "WHERE e.kind='imports_module');").fetchone()[0]
            elif template == "entry_points":
                rows = conn.execute(
                    "SELECT * FROM nodes WHERE kind IN ('function','method') "
                    "LIMIT 5000;").fetchall()
                # CAN-053: split test_* names / test files out of the default
                # view (codo: 33/50 of the old output were test runners).
                _all_eps = [_node_brief(r) for r in rows
                            if _is_entry_point(r["name"], r["decorators"])]
                _prod_eps = [b for b in _all_eps
                             if not (str(b.get("name", "")).startswith("test_")
                                     or "test" in str(b.get("file_path", "")).lower())]
                res = _prod_eps[offset:offset + limit]
                # The population is the PRODUCTION set: _all_eps includes the
                # test runners this view deliberately drops, so counting those
                # would report a total no page of this template ever serves.
                total = len(_prod_eps)
                payload["test_entrypoints_excluded"] = len(_all_eps) - len(_prod_eps)
            # B021: `total_count` is the PRE-CAP population, and `truncated` is
            # DERIVED from it -- one number, not two fields set independently.
            #
            # Reported: module_deps answered count=12, truncated=True and
            # total_count=0 in the same payload. Those are the two fields a
            # paginating caller chooses between, and they disagreed: total_count
            # said "nothing here", truncated said "keep asking". The docstring
            # made it look intended ("0 otherwise"), so a reader checking whether
            # it was a defect would have closed the report.
            #
            # Every branch above now records its own population in `total` --
            # a COUNT over the same predicate as its LIMIT-ed SELECT -- and the
            # tail derives truncation from it. `total is None` means the branch
            # genuinely does not know, and the honest fallback is len(res):
            # never claim MORE than was shown, because an invented total is the
            # bug being fixed pointing the other way.
            #
            # Offset-aware on purpose: for a paginated template the question is
            # "does another PAGE remain", not "is total > this page" -- the last
            # page of three is complete, not truncated.
            payload["results"] = res
            payload["count"] = len(res)
            payload["total_count"] = len(res) if total is None else int(total)
            if template == "impact_scope":
                # impact_scope answers ONE synthetic row whose own `truncated`
                # comes from the trace depth cap, not from a row count. Deriving
                # it from 1-of-1 would silently report a depth-capped blast
                # radius as complete.
                pass
            else:
                payload["truncated"] = (offset + len(res)) < payload["total_count"]
        finally:
            conn.close()
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"
    payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return payload


# ─── CAN-053 P2: raw graph browse (codo: "no API to browse/filter nodes") ────

def nodes_query(project_path: str, *, kind: str = "", name_prefix: str = "",
                file_contains: str = "", limit: int = 100, offset: int = 0) -> dict:
    """Filter/browse raw graph nodes with a cursor. Fail-open."""
    payload = {"enabled": graph_enabled(), "results": [], "count": 0, "total": 0,
               "limit": limit, "offset": offset, "next_offset": None, "error": None}
    if not graph_enabled():
        return payload
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    try:
        conn = _store.get_db(project_path, readonly=True)
        try:
            where, args = [], []
            if kind:
                where.append("kind=?"); args.append(kind)
            if name_prefix:
                where.append("name LIKE ?"); args.append(name_prefix + "%")
            if file_contains:
                where.append("file_path LIKE ?"); args.append("%" + file_contains + "%")
            w = (" WHERE " + " AND ".join(where)) if where else ""
            payload["total"] = conn.execute("SELECT COUNT(*) FROM nodes" + w + ";", args).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM nodes" + w + " ORDER BY file_path, start_line LIMIT ? OFFSET ?;",
                args + [limit, offset]).fetchall()
            payload["results"] = [_node_brief(r) for r in rows]
            payload["count"] = len(payload["results"])
            if offset + len(rows) < payload["total"]:
                payload["next_offset"] = offset + len(rows)
        finally:
            conn.close()
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"
    return payload


def edges_query(project_path: str, *, kind: str = "", source_contains: str = "",
                target_contains: str = "", limit: int = 100, offset: int = 0) -> dict:
    """Filter/browse raw graph edges with a cursor. Fail-open."""
    payload = {"enabled": graph_enabled(), "results": [], "count": 0, "total": 0,
               "limit": limit, "offset": offset, "next_offset": None, "error": None}
    if not graph_enabled():
        return payload
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    try:
        conn = _store.get_db(project_path, readonly=True)
        try:
            where, args = [], []
            if kind:
                where.append("e.kind=?"); args.append(kind)
            if source_contains:
                where.append("(s.qualified_name LIKE ? OR s.file_path LIKE ?)")
                args += ["%" + source_contains + "%", "%" + source_contains + "%"]
            if target_contains:
                where.append("(t.qualified_name LIKE ? OR t.file_path LIKE ?)")
                args += ["%" + target_contains + "%", "%" + target_contains + "%"]
            w = (" WHERE " + " AND ".join(where)) if where else ""
            base_sql = ("FROM edges e JOIN nodes s ON s.id=e.source "
                        "JOIN nodes t ON t.id=e.target" + w)
            payload["total"] = conn.execute("SELECT COUNT(*) " + base_sql + ";", args).fetchone()[0]
            rows = conn.execute(
                "SELECT e.kind AS ekind, s.qualified_name AS src, s.file_path AS sfile, "
                "t.qualified_name AS dst, t.file_path AS tfile " + base_sql +
                " ORDER BY s.file_path LIMIT ? OFFSET ?;",
                args + [limit, offset]).fetchall()
            payload["results"] = [
                {"kind": r["ekind"], "source": r["src"], "source_file": r["sfile"],
                 "target": r["dst"], "target_file": r["tfile"]} for r in rows]
            payload["count"] = len(payload["results"])
            if offset + len(rows) < payload["total"]:
                payload["next_offset"] = offset + len(rows)
        finally:
            conn.close()
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"
    return payload


# ─── Step 16: bounded architecture summary ───────────────────────────────────

def cbm_get_architecture(project_path: str, *,
                         max_files: int = GRAPH_CONTEXT_MAX_FILES,
                         max_symbols: int = GRAPH_CONTEXT_MAX_SYMBOLS,
                         timeout_s: float = GRAPH_CONTEXT_TIMEOUT_S) -> dict:
    """Bounded module-level architecture overview (Step 16). Fail-open.

    Cites exact files; early-exits with truncated=True + truncation_reason +
    files_seen/symbols_seen/elapsed_ms when any bound is hit.
    """
    t0 = time.monotonic()
    payload = {"enabled": graph_enabled(), "modules": [], "entry_points": [],
               "files_seen": 0, "symbols_seen": 0, "truncated": False,
               "truncation_reason": None, "error": None, "elapsed_ms": 0,
               "auto_reindex": None}
    if not graph_enabled():
        return payload
    try:
        # Hybrid auto-reindex: refresh graph only if a file actually changed.
        payload["auto_reindex"] = {"triggered": False, "reason": "wrapper_governs_refresh"}
        conn = _store.get_db(project_path, readonly=True)
        try:
            mods = conn.execute(
                "SELECT m.id, m.qualified_name, m.file_path, "
                "(SELECT COUNT(*) FROM nodes k WHERE k.file_path=m.file_path) AS symbols "
                "FROM nodes m WHERE m.kind='module' ORDER BY symbols DESC;").fetchall()
            symbols_seen = 0
            # CAN-053 (codo P1): stratified overview — the old largest-module-first
            # walk with a post-add cap check returned exactly ONE hotspot module.
            # Cap counted symbols PER MODULE so the overview keeps breadth.
            _mod_total = len(mods)
            _per_module_cap = max(5, int(max_symbols / max(1, min(_mod_total, max_files))))
            payload["modules_total"] = _mod_total
            payload["per_module_symbol_cap"] = _per_module_cap
            for i, m in enumerate(mods):
                if time.monotonic() - t0 > timeout_s:
                    payload["truncated"] = True
                    payload["truncation_reason"] = "timeout"
                    break
                if i >= max_files:
                    payload["truncated"] = True
                    payload["truncation_reason"] = "max_files"
                    break
                if symbols_seen >= max_symbols:
                    payload["truncated"] = True
                    payload["truncation_reason"] = "max_symbols"
                    break
                deps = [r[0] for r in conn.execute(
                    "SELECT DISTINCT t.qualified_name FROM edges e "
                    "JOIN nodes s ON s.id=e.source JOIN nodes t ON t.id=e.target "
                    "WHERE e.kind='imports_module' AND s.file_path=? LIMIT 10;",
                    (m["file_path"],))]
                _counted = min(int(m["symbols"] or 0), _per_module_cap)
                payload["modules"].append({
                    "module": m["qualified_name"], "file": m["file_path"],
                    "symbols": m["symbols"], "symbols_counted": _counted,
                    "depends_on": deps})
                symbols_seen += _counted
            payload["files_seen"] = len(payload["modules"])
            payload["symbols_seen"] = symbols_seen
            ep = template_query(project_path, "entry_points", limit=20)
            _eps = ep.get("results") or []
            # CAN-053: runtime view — test runners are not runtime entry points.
            payload["entry_points"] = [
                {"name": r["name"], "file": r["file_path"], "line": r["start_line"]}
                for r in _eps
                if not (str(r.get("name", "")).startswith("test_")
                        or "test" in str(r.get("file_path", "")).lower())][:20]
            payload["entry_points_excluded_tests"] = len(_eps) - len(payload["entry_points"])
        finally:
            conn.close()
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"
    payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return payload


# ─── Step 17: conservative dead-code candidates ──────────────────────────────

def dead_code_candidates(project_path: str, *, limit: int = 50,
                         rel_files: "set[str] | None" = None) -> dict:
    """Suggest unused-code candidates WITHOUT false certainty (Step 17).

    Conservative: entry points, dunder/callback-ish names, decorated
    symbols, and `__all__`-exported symbols (is_exported=1) are excluded;
    remaining public symbols cap at LOW confidence because external/dynamic
    callers are invisible to the graph. Never a basis for source edits on
    its own (plan rollback note).

    F017: `count` is the TRUE population, not the page size. It used to be
    len(candidates[:limit]), so a project with 3796 dead symbols and the
    default limit reported "count: 50" -- a number that looked like a finding
    and was really the limit. `returned_count` is the page.

    rel_files: project-relative paths (forward slashes) to restrict the report
    to. This engine had no such parameter, so `cbm_dead_code`'s `file_filter`
    resolved a glob and then dropped it on the floor for every non-Python repo --
    a bogus glob and no glob at all returned the same 207 candidates. An empty
    SET means "the filter matched nothing" and correctly yields no candidates;
    None means "no filter".
    """
    t0 = time.monotonic()
    payload = {"enabled": graph_enabled(), "candidates": [], "count": 0,
               "returned_count": 0, "truncated": False, "limit_applied": 0,
               "error": None, "elapsed_ms": 0,
               "disclaimer": "advisory only — dynamic/external callers are invisible; "
                             "never delete code from this list without manual verify"}
    if not graph_enabled():
        return payload
    limit = max(1, min(int(limit or 50), 200))
    payload["limit_applied"] = limit
    try:
        # F003 fix (2026-07-11): same staleness guard as cbm_get_architecture.
        payload["auto_reindex"] = {"triggered": False, "reason": "wrapper_governs_refresh"}
        conn = _store.get_db(project_path, readonly=True)
        try:
            # F004 fix: ignore low-confidence heuristic call edges (< 0.7).
            # W5: LIVE_REF_EXISTS_SQL also accepts `dynamic_ref`, so a
            # string-keyed registry no longer buries its whole dispatch table
            # in this list (measured: 180 of 225 code_tools.TOOL_MAP entries).
            # W6: the whole filter chain is one call into liveness.py. The two
            # flags are this report's only genuine divergence from
            # orphan_symbols: a symbol in `__all__` is reachable by import, and
            # a decorated one may be handed to a framework that calls it later.
            # Everything else -- entry points, framework dispatch -- is shared,
            # because a symbol that is one of those is not dead under any
            # reading, and a report that says otherwise is what drifted before.
            out = []
            for r in _dead_callables(conn, unexported_only=True,
                                     exclude_decorated=True):
                if rel_files is not None:
                    fp = (r["file_path"] or "").replace("\\", "/")
                    if fp not in rel_files:
                        continue
                private = r["name"].startswith("_")
                conf = 0.7 if private else 0.3
                reason = ("private, 0 inbound call edges" if private else
                          "public, 0 inbound call edges — external callers possible")
                out.append({**_node_brief(r), "confidence": conf, "reason": reason})
            out.sort(key=lambda c: (-c["confidence"], c["file_path"], c["start_line"]))
            payload["candidates"] = out[:limit]
            payload["count"] = len(out)
            payload["returned_count"] = len(payload["candidates"])
            payload["truncated"] = len(out) > limit
        finally:
            conn.close()
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"
    payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return payload


# ─── Step 21: bounded advisory council graph context pack ────────────────────

def council_graph_context(project_path: str, *, name: str = "",
                          depth: int = 2, file_path: str = "") -> dict:
    """Bounded advisory graph context pack for council dispatches (Step 21).

    The leader explicitly embeds this block in a worker prompt — it is never
    auto-injected and never blocks dispatch (_council_build_broker_context
    stays disabled). Sources:
      - name given  -> symbol lookup + inbound blast radius (impact_scope).
      - name omitted -> detect_changes(): symbols defined in changed files
        plus their direct callers (1-hop inbound, edge confidence attached).
    Down-truncates from graph traversal bounds (50/200) to council bounds
    COUNCIL_CONTEXT_MAX_FILES/COUNCIL_CONTEXT_MAX_SYMBOLS (10/50); every cap
    hit is recorded in truncation_reason ("+"-joined when multiple).
    Fail-open: any internal error degrades to graph_status="error" with empty
    context — never raises.
    """
    t0 = time.monotonic()
    # STT-2: file_path mode takes precedence over changes mode (but not symbol
    # mode) when name is empty and file_path is provided.
    if name:
        _source_mode = "symbol"
    elif file_path:
        _source_mode = "file"
    else:
        _source_mode = "changes"
    payload = {"graph_status": "disabled", "graph_used": False,
               "graph_degraded_reason": None,
               "source": _source_mode,
               "files": [], "symbols": [], "truncated": False,
               "truncation_reason": None, "empty": False,
               "budget": {"max_files": COUNCIL_CONTEXT_MAX_FILES,
                          "max_symbols": COUNCIL_CONTEXT_MAX_SYMBOLS},
               "elapsed_ms": 0}
    if not graph_enabled():
        payload["graph_degraded_reason"] = "GRAPH_ENABLED=0"
        return payload
    try:
        symbols: list = []
        files: list = []
        reasons: list = []
        if name:
            # (STT-5 redesign): refresh stale index BEFORE reading in symbol mode.
            # Safe here because symbol mode does not use detect_changes (no stale
            # state to preserve). Changes mode below intentionally skips this.
            # Council attachments are pure T1 reads; no hidden graph rewrite.
            hits = graph_find_symbol(
                project_path, name,
                limit=COUNCIL_CONTEXT_MAX_SYMBOLS).get("results") or []
            if not hits:
                payload["graph_status"] = "ok"
                payload["graph_degraded_reason"] = "symbol_not_found"
                payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
                return payload
            # prefer definition nodes over import references as the target
            top = next((h for h in hits
                        if h.get("kind") in ("function", "method", "class", "module")),
                       hits[0])
            scope = template_query(project_path, "impact_scope",
                                   node_id=top["id"], depth=depth)
            res = (scope.get("results") or [{}])[0] or {}
            files = [top.get("file_path")] + [
                f for f in (res.get("affected_files") or [])
                if f != top.get("file_path")]
            files = [f for f in files if f]
            symbols.append({"qualified_name": top.get("qualified_name"),
                            "kind": top.get("kind"),
                            "file_path": top.get("file_path"),
                            "start_line": top.get("start_line"),
                            "confidence": 1.0, "role": "target"})
            conn = _store.get_db(project_path, readonly=True)
            try:
                for sid in (res.get("affected_symbols") or []):
                    row = conn.execute("SELECT * FROM nodes WHERE id=?;",
                                       (sid,)).fetchone()
                    if row is None:
                        continue
                    symbols.append({"qualified_name": row["qualified_name"],
                                    "kind": row["kind"],
                                    "file_path": row["file_path"],
                                    "start_line": row["start_line"],
                                    "confidence": None, "role": "impact"})
            finally:
                conn.close()
            if scope.get("truncated"):
                reasons.append("impact_scope_truncated")
        elif file_path:
            # STT-2: file-level context — all function/method/class symbols in the
            # given file, plus their 1-hop direct callers (mirrors changes-mode
            # caller resolution but scoped to one file instead of changed set).
            # STT-2a: normalize file_path — indexed paths use forward slashes;
            # Windows backslash or absolute path would silently fail to match.
            fp_norm = file_path.replace("\\", "/")
            proj_abs = os.path.abspath(project_path).replace("\\", "/")
            if fp_norm.startswith(proj_abs + "/"):
                fp_norm = fp_norm[len(proj_abs) + 1:]
            conn = _store.get_db(project_path, readonly=True)
            try:
                file_syms: list = []
                for row in conn.execute(
                        "SELECT * FROM nodes WHERE file_path=? AND "
                        "kind IN ('function','method','class') "
                        "ORDER BY start_line;", (fp_norm,)):
                    file_syms.append((row["id"], row["qualified_name"]))
                    symbols.append({"qualified_name": row["qualified_name"],
                                    "kind": row["kind"],
                                    "file_path": row["file_path"],
                                    "start_line": row["start_line"],
                                    "confidence": 1.0, "role": "target"})
                if not symbols:
                    payload["graph_status"] = "ok"
                    payload["graph_degraded_reason"] = "file_not_found"
                    payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
                    return payload
                files = [fp_norm]
                for cid, cqn in sorted(file_syms[:COUNCIL_CONTEXT_MAX_SYMBOLS],
                                       key=lambda s: s[1]):
                    for row in conn.execute(
                            "SELECT n.*, e.confidence AS edge_confidence "
                            "FROM edges e JOIN nodes n ON n.id=e.source "
                            "WHERE e.kind='calls' AND (e.target=? OR e.target IN "
                            "(SELECT id FROM nodes WHERE kind='import' "
                            " AND qualified_name=?)) "
                            "ORDER BY n.file_path, n.start_line LIMIT ?;",
                            (cid, cqn, COUNCIL_CONTEXT_MAX_SYMBOLS)):
                        symbols.append({"qualified_name": row["qualified_name"],
                                        "kind": row["kind"],
                                        "file_path": row["file_path"],
                                        "start_line": row["start_line"],
                                        "confidence": row["edge_confidence"],
                                        "role": "caller"})
            finally:
                conn.close()
        else:
            from . import indexer as _indexer  # lazy: avoid import-order coupling
            changes = _indexer.detect_changes(project_path)
            changed = (list(changes.get("added") or [])
                       + list(changes.get("modified") or [])
                       + [n for _o, n in (changes.get("renamed") or [])])
            if not changed:
                payload["graph_status"] = "ok"
                payload["graph_degraded_reason"] = "no_changed_files"
                payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
                return payload
            files = changed
            conn = _store.get_db(project_path, readonly=True)
            try:
                changed_syms: list = []
                # Step 21 fix (STT-6): deterministic file order before truncation
                # (detect_changes uses os.walk which is filesystem-order dependent)
                changed_sorted = sorted(changed[:COUNCIL_CONTEXT_MAX_FILES])
                for rel in changed_sorted:
                    for row in conn.execute(
                            "SELECT * FROM nodes WHERE file_path=? AND "
                            "kind IN ('function','method','class') "
                            "ORDER BY start_line;", (rel,)):
                        changed_syms.append((row["id"], row["qualified_name"]))
                        symbols.append({"qualified_name": row["qualified_name"],
                                        "kind": row["kind"],
                                        "file_path": row["file_path"],
                                        "start_line": row["start_line"],
                                        "confidence": 1.0, "role": "changed"})
                for cid, cqn in sorted(changed_syms[:COUNCIL_CONTEXT_MAX_SYMBOLS],
                                       key=lambda s: s[1]):
                    # cross-module calls resolve to the caller-side import node,
                    # not the target function node — bridge via qualified_name.
                    for row in conn.execute(
                            "SELECT n.*, e.confidence AS edge_confidence "
                            "FROM edges e JOIN nodes n ON n.id=e.source "
                            "WHERE e.kind='calls' AND (e.target=? OR e.target IN "
                            "(SELECT id FROM nodes WHERE kind='import' "
                            " AND qualified_name=?)) "
                            "ORDER BY n.file_path, n.start_line LIMIT ?;",
                            (cid, cqn, COUNCIL_CONTEXT_MAX_SYMBOLS)):
                        symbols.append({"qualified_name": row["qualified_name"],
                                        "kind": row["kind"],
                                        "file_path": row["file_path"],
                                        "start_line": row["start_line"],
                                        "confidence": row["edge_confidence"],
                                        "role": "caller"})
            finally:
                conn.close()
        # dedup (first occurrence wins: target/changed roles precede callers).
        # STT-23: when a symbol appears as both target/changed AND caller/impact,
        # only the first entry (target/changed) survives — the caller evidence
        # for the overlapping symbol is intentionally dropped to avoid doubling.
        # This is by design; if caller confidence is needed for a target symbol,
        # request it separately via symbol mode + trace_path.
        seen: set = set()
        deduped: list = []
        for s in symbols:
            key = (s["qualified_name"], s["file_path"], s["start_line"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(s)
        symbols = deduped
        # down-truncate to council caps; record every cap hit (P4 KiloC7
        # SUSPECT-3 lesson: report ALL bounds hit, not just the first)
        if len(files) > COUNCIL_CONTEXT_MAX_FILES:
            files = files[:COUNCIL_CONTEXT_MAX_FILES]
            reasons.append("max_files")
        if len(symbols) > COUNCIL_CONTEXT_MAX_SYMBOLS:
            symbols = symbols[:COUNCIL_CONTEXT_MAX_SYMBOLS]
            reasons.append("max_symbols")
        payload["files"] = files
        payload["symbols"] = symbols
        payload["graph_status"] = "ok"
        payload["graph_used"] = bool(symbols)
        # STT-12: explicit empty signal — callers check `empty` instead of
        # parsing the reason string. True when graph ok but no symbols found.
        payload["empty"] = (not symbols and payload["graph_status"] == "ok")
        if not symbols and payload["source"] == "changes":
            # changed files exist but none has indexed symbols — typically
            # added/renamed files not yet incremental-indexed (ComC3 EC-3)
            payload["graph_degraded_reason"] = "no_indexed_symbols_in_changed_files"
        payload["truncated"] = bool(reasons)
        payload["truncation_reason"] = "+".join(reasons) or None
    except Exception as e:
        # fail-open: degraded context must never block a council dispatch
        payload["graph_status"] = "error"
        payload["graph_used"] = False
        payload["graph_degraded_reason"] = f"{type(e).__name__}: {e}"
        payload["files"] = []
        payload["symbols"] = []
    payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return payload
