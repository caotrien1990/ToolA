from __future__ import annotations

import asyncio
import concurrent.futures
import fnmatch
import functools
import json
import os
import re
import threading
import time
from pathlib import Path, PurePosixPath

from orchestrator import search_snapshots
from orchestrator.fileops import common as fo
from orchestrator.server.executor_lanes import get_executor_lane
from orchestrator.server.fair_admission import FAIR_ADMISSION

MAX_SEARCH_CONTEXT_LINES = fo.MAX_SEARCH_CONTEXT_LINES
MAX_SEARCH_RESULTS = fo.MAX_SEARCH_RESULTS

# B026 2026-08-22 (Anh/Clau): text mode had NO size budget. mode='regex' has one
# (regex_worker.validate_next_record_budget), so a runaway pattern is capped -- but a
# plain literal search re-scanned EVERY byte of EVERY record with no ceiling at all.
# Measured on dreye_redesign (a data repo: 760 records / 230.9 MB reach the scanner):
# SEVEN minified HTML files >2MB hold 175.6 MB = 76% of the bytes and ~77% of all
# re.search time (2165ms -> 504ms once they are excluded), while holding zero source
# code -- the largest genuine source file in that repo is 0.78 MB. cProfile on the live
# tool agrees: 2.249s of a 3.86s search_code call is `re.Pattern.search`, against only
# 0.448s of blob reads, so this is a SCAN-cost problem, not an I/O one.
#
# The cap lives HERE and nowhere else on purpose:
#   - NOT in _skip_reason / SKIP_DIRS: those feed INDEX.json, and dropping a file there
#     would take away its index entry (and CAN-042 pins that set as a single source).
#     A 25MB page.html that a CA is actively editing must KEEP its index entry.
#   - NOT in should_scan_for_text: index_delta shares it, and a file that vanished from
#     "should be scanned" would distort drift reporting.
# Skipped records are COUNTED and REPORTED back to the caller (`skipped_large_files`),
# never dropped silently, and `max_file_bytes=0` turns the cap off for that one call.
MAX_TEXT_RECORD_BYTES = 2_000_000

_SEARCH_MODE_ALIASES = {
    "plain": "text", "literal": "text", "raw": "text", "exact": "text",
    "string": "text", "fixed": "text", "re": "regex", "pattern": "regex", "regexp": "regex",
    "definition": "symbol", "definitions": "symbol", "def": "symbol",
    "usage": "definition_usage", "usages": "definition_usage", "definition_and_usage": "definition_usage",
    "all": "definition_usage", "caller": "callers", "refs": "callers", "references": "callers",
    "idx": "index", "project_index": "index", "dom": "dom_candidates", "dom_candidate": "dom_candidates",
    "fuzzy": "smart", "or": "smart",
}
_SEARCH_CODE_MODES = ["text", "regex", "symbol", "callers", "definition_usage", "index", "dom_candidates", "smart"]
_SEARCH_SINGLEFLIGHT_LOCK = threading.Lock()
_SEARCH_SINGLEFLIGHT: dict[tuple, concurrent.futures.Future[dict]] = {}
_SEARCH_CLEANUP_GRACE_SECONDS = 0.25


def _snapshot_root_for(base: Path, project_path: str = "") -> Path:
    base_resolved = base.resolve(strict=False)
    if project_path:
        explicit = Path(project_path).resolve()
        if base_resolved.is_relative_to(explicit):
            return explicit
    return base_resolved.parent if base.is_file() else base_resolved


def _search_snapshot_token(root: Path) -> tuple:
    """Return the immutable generation ID used in the singleflight key."""
    snapshot_id = search_snapshots.current_snapshot_id(root)
    if snapshot_id:
        return "snapshot", snapshot_id
    try:
        root_stat = root.stat()
        return "snapshot-missing", root_stat.st_mtime_ns, root_stat.st_size
    except OSError:
        return "snapshot-missing", str(root).casefold()


def _pin_or_create_snapshot(
    root: Path,
    expected_id: str | None = None,
    *,
    deadline_at: float | None = None,
) -> search_snapshots.SnapshotPin:
    try:
        return search_snapshots.pin_current_snapshot(
            root,
            expected_id=expected_id,
            deadline_at=deadline_at,
        )
    except search_snapshots.SnapshotUnavailable:
        if expected_id is not None or search_snapshots.current_snapshot_id(root) is not None:
            raise
        snapshot_id = search_snapshots.publish_snapshot(
            root,
            # B027f: bind the root here too. Unbound, this fallback published a
            # DIFFERENT record set than surgical_mapper._publish_search_snapshot
            # (which R82 bound), so the answer depended on whether anyone had
            # refreshed the map first -- and `!name` never reached search at all.
            include_file=functools.partial(fo.should_scan_for_text, project_root=root),
            # B027: bind root so a project's .orchestrator_ignore reaches the
            # snapshot search_code serves from, not just the os.walk paths below.
            skip_dir=functools.partial(fo.should_skip_dir, project_root=root),
            index=None,
            deadline_at=deadline_at,
        )
        return search_snapshots.pin_current_snapshot(
            root,
            expected_id=snapshot_id,
            deadline_at=deadline_at,
        )


def _remove_search_singleflight(
    key: tuple,
    completed: concurrent.futures.Future[dict],
) -> None:
    with _SEARCH_SINGLEFLIGHT_LOCK:
        if _SEARCH_SINGLEFLIGHT.get(key) is completed:
            _SEARCH_SINGLEFLIGHT.pop(key, None)


def _consume_cancelled_waiter_result(done: asyncio.Future) -> None:
    if not done.cancelled():
        done.exception()


async def _run_search_singleflight(
    key: tuple,
    producer,
    *,
    follower_release=None,
    owner_transfer=None,
    owner_done=None,
    timeout: float | None = None,
) -> dict:
    """Share one producer future while each caller keeps independent cancellation."""
    permit = None
    with _SEARCH_SINGLEFLIGHT_LOCK:
        shared_future = _SEARCH_SINGLEFLIGHT.get(key)
    if shared_future is None:
        lane = get_executor_lane("search")
        deadline = time.monotonic() + (timeout if timeout is not None else lane.queue_wait_timeout)
        permit = await FAIR_ADMISSION.acquire("search", deadline=deadline)
        with _SEARCH_SINGLEFLIGHT_LOCK:
            shared_future = _SEARCH_SINGLEFLIGHT.get(key)
            if shared_future is None:
                try:
                    shared_future = lane.submit(producer)
                except BaseException:
                    permit.release()
                    raise
                _SEARCH_SINGLEFLIGHT[key] = shared_future
                created = True
            else:
                permit.release()
                permit = None
                created = False
    else:
        created = False

    if not created and follower_release is not None:
        follower_release()
    elif created and owner_transfer is not None:
        owner_transfer()

    if created:
        def _producer_done(completed) -> None:
            _remove_search_singleflight(key, completed)
            if permit is not None:
                permit.release()
            if owner_done is not None:
                owner_done()

        shared_future.add_done_callback(_producer_done)

    loop_future = asyncio.wrap_future(shared_future)
    try:
        waiter = asyncio.shield(loop_future)
        if timeout is not None:
            return await asyncio.wait_for(waiter, timeout=max(0.001, timeout))
        return await waiter
    except asyncio.TimeoutError:
        try:
            await asyncio.wait_for(
                asyncio.shield(loop_future),
                timeout=_SEARCH_CLEANUP_GRACE_SECONDS,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
        loop_future.add_done_callback(_consume_cancelled_waiter_result)
        raise
    except asyncio.CancelledError:
        loop_future.add_done_callback(_consume_cancelled_waiter_result)
        raise


async def find_symbol(name: str = "", path: str = "", project_path: str = "",
                      context_lines: int = 0, include: str = "def",
                      name_encoding: str = "plain", include_impact: bool = False,
                      **aliases) -> str:
    """Find a symbol's DEFINITION in Python/JS-style source files.

    TT-A-01 scope contract (2026-08-10): the live-filesystem fallback only
    scans ``.py .pyi .js .jsx .ts .tsx .mjs .cjs .vue .svelte`` and only
    recognises Python ``def``/``class`` and JS/TS ``function``/``const``/
    ``let``/``var``/method-shorthand definition forms. It does NOT support
    Go/Rust/C-family/Java/Kotlin/Scala/Ruby/PHP/Swift/Lua/R/ObjC/shell
    definition grammar. For other languages or broad reference search use
    ``find_definition_and_usage`` or ``search_code`` instead.
    """
    name = name or aliases.get("query") or aliases.get("symbol") or aliases.get("symbol_name") or ""
    if name and name_encoding and name_encoding != "plain":
        decoded_name, _, decode_err = fo.decode_text_payload(name, name_encoding)
        if decode_err:
            decode_err.setdefault("error_name", "INVALID_NAME_ENCODING")
            return json.dumps(decode_err, ensure_ascii=False)
        if decoded_name is not None:
            name = decoded_name
    path = project_path or path or aliases.get("file_path", "")
    if include_impact and name:
        # CAN-053 P2 opt-in: run the base lookup, then attach bounded graph
        # impact_scope for the symbol (fail-open, graph-disabled tolerated).
        base_raw = await find_symbol(name=name, path=path, project_path=project_path,
                                     context_lines=context_lines, include=include,
                                     name_encoding="plain")
        try:
            base = json.loads(base_raw)
            if base.get("results"):
                from orchestrator.graph.query import template_query as _tq
                imp = _tq(project_path or path, "impact_scope", name=name, depth=2, limit=25)
                base["impact_scope"] = {k: imp.get(k) for k in
                                        ("results", "count", "truncated", "error")}
                return json.dumps(base, ensure_ascii=False)
        except Exception:
            pass
        return base_raw
    if not name:
        return json.dumps({
            "ok": False,
            "error": "Missing required field(s) for 'find_symbol': name",
            "error_name": "MISSING_REQUIRED_FIELD",
            "type": "SCHEMA_VIOLATION",
            "severity": "error",
            "retryable": True,
            "missing_fields": ["name"],
            "required_fields": ["name"],
            "accepted_fields": ["query", "name", "symbol", "symbol_name", "path", "project_path", "file_path", "context_lines"],
            "payload_example": {"project_path": "D:/path/to/project", "query": "function_name", "context_lines": 20},
            "ca_next_action": "rebuild_payload_using_required_and_accepted_fields",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    if not path:
        return json.dumps({
            "ok": False,
            "error": "Missing required field(s) for 'find_symbol': path or project_path",
            "error_name": "MISSING_REQUIRED_FIELD",
            "type": "SCHEMA_VIOLATION",
            "severity": "error",
            "retryable": True,
            "missing_fields": ["path", "project_path"],
            "required_fields": ["path", "project_path"],
            "accepted_fields": ["query", "name", "symbol", "symbol_name", "path", "project_path", "file_path", "context_lines"],
            "payload_example": {"project_path": "D:/path/to/project", "query": "function_name", "context_lines": 20},
            "ca_next_action": "rebuild_payload_using_required_and_accepted_fields",
            "write_performed": False,
            "applied": False,
        }, ensure_ascii=False)
    base = Path(path)
    if not base.exists():
        return json.dumps({"error": f"Path not found: {path}"})
    project_root = str(base) if base.is_dir() else (fo.find_project_root(str(base)) or str(base.parent))
    stale = await fo.ensure_fresh_index(project_root)
    stale_info = json.loads(stale) if stale else None
    # B-02 (2026-08-10): gate the definition-only graph adapter on include='def'.
    # The graph adapter (find_symbol_as_search_response) returns ONLY the
    # definition-shaped `results` array; it has no callers/usages payload.
    # Previously the early `return graph_resp` fired for every include value,
    # so find_callers (include='callers') returned the definition list back
    # to the caller with no callers array, no include tag, and no isolation
    # of executable call sites — exactly what B-02 observed live (mixed
    # definition + registry-string + archived-copy hits, no callers field).
    # Now: the graph adapter only runs when include='def' (default), and
    # callers/usage/all fall through to the live scanner below which has the
    # TT-A-02 isolation logic (pop results, attach callers[], drop non-source
    # artifacts, filter comment lines, exclude should_skip_dir trees).
    inc = (include or "def").lower().strip()
    try:
        from orchestrator.graph.config import use_graph_for
        from orchestrator.graph.query import find_symbol_as_search_response
        if inc == "def" and use_graph_for("find_symbol"):
            # Anh 2026-08-10: the graph adapter queries the symbol graph DB; run it
            # off the event loop so include='def' find_symbol/search never pins the
            # loop (was an on-loop blocker alongside index_delta).
            graph_resp = await asyncio.to_thread(
                find_symbol_as_search_response, name, project_root, context_lines=context_lines,
            )
            if graph_resp is not None:
                if stale_info:
                    graph_data = json.loads(graph_resp)
                    graph_data.update({
                        "warning": "INDEX stale; returned live filesystem search results",
                        "category": "stale_index_live_fallback",
                        "drift": stale_info.get("drift"),
                    })
                    return json.dumps(graph_data, ensure_ascii=False)
                return graph_resp
    except Exception:
        pass

    escaped = re.escape(name)
    # TT-A-01 fix (2026-08-10): definition-only patterns. Removed the catch-all
    # `export .* \b{name}\b` branch which matched re-exports and any export
    # statement containing the identifier (call-site-shaped in JS/TS barrels),
    # and tightened the method-shorthand branch so it cannot match a call site
    # like `result = mod.name(5)` (requires leading identifier/keyword, not
    # attribute access or assignment target).
    patterns = re.compile(
        rf"^\s*(async\s+)?def\s+{escaped}\s*\("
        rf"|^\s*class\s+{escaped}\s*[\(:]"
        rf"|^\s*(export\s+)?(async\s+)?function\s+{escaped}\s*\("
        rf"|^\s*(export\s+)?(const|let|var)\s+{escaped}\s*[=:(]"
        rf"|^\s*(export\s+)?(async\s+|static\s+|get\s+|set\s+)*{escaped}\s*\([^)]*\)\s*\{{"
        rf"|^\s*{escaped}\s*:\s*(async\s+)?function",
    )

    # TT-A-01 fix: definitions live in source files only. JSON artifacts,
    # INDEX.json snapshots, cache DBs, logs, markdown, and text dumps cannot
    # host a definition and previously produced call-site-shaped noise.
    #
    # TT-A-01 scope refinement (2026-08-10): honestly scope this fallback to
    # Python/JS-style languages whose definition grammar the regex above
    # actually recognizes ('def', 'class', 'function', 'const/let/var', and
    # method shorthand). Removed Go/Rust/C-family/Java/Kotlin/Scala/Ruby/PHP/
    # Swift/Lua/R/ObjC/shell because the regex does NOT match their definition
    # forms (e.g. 'func name()', 'fn name()', 'type name func()', 'def name;
    # end', etc.) and previously produced either zero hits (false advertisement)
    # or stray matches inside block comments / multiline strings. For other
    # languages or broad reference search, callers should use
    # find_definition_and_usage or search_code instead.
    _DEF_SOURCE_EXTS = {
        ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        ".vue", ".svelte",
    }

    def _is_def_source_file(fp: Path) -> bool:
        if fp.suffix.lower() not in _DEF_SOURCE_EXTS:
            return False
        # Belt-and-braces: skip cache/artifact directories even if a source-
        # looking extension leaks in (e.g. snapshot .py copies).
        parts = {p.lower() for p in fp.parts}
        if ".orchestrator_cache" in parts or "__pycache__" in parts:
            return False
        return True

    def _is_comment_or_string_line(line: str) -> bool:
        s = line.lstrip()
        if not s:
            return False
        # Python / shell / ruby
        if s.startswith("#"):
            return True
        # JS/TS/C-style single-line
        if s.startswith("//"):
            return True
        # Block-comment openers
        if s.startswith("/*") or s.startswith("*"):
            return True
        return False

    def _search() -> list[dict]:
        results: list[dict] = []
        for root, dirs, files in os.walk(base, onerror=lambda e: None):
            dirs[:] = [d for d in dirs if not fo.should_skip_dir(d, base)]
            for fn in files:
                fp = Path(root) / fn
                if not _is_def_source_file(fp):
                    continue
                # B027f: bound, matching the dirs[:] filter above.
                if not fo.should_scan_for_text(fp, base):
                    continue
                try:
                    lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
                except Exception:
                    continue
                # TT-A-01 scope refinement: track Python triple-quoted string
                # state per file so a definition-shaped line inside a
                # docstring/multiline string is NOT returned. Line-based scan
                # cannot use a real tokenizer here (kept lightweight);
                # triple-quote toggling is a conservative approximation: a
                # lone opener flips state; same-line open+close nets zero.
                in_triple: str | None = None
                # TT-A-01 v2 (2026-08-10): also track C-style block-comment
                # state for JS/TS/Vue/Svelte so a method-shaped line inside
                # a /* ... */ interior is not returned. Line-based: enter on
                # "/*", exit on "*/"; same-line open+close nets zero.
                # Documented limitation: this does not handle nested block
                # comments or comment markers inside string literals, which
                # matches the conservative approximation used for Python
                # triple-quoted strings.
                in_block_comment: bool = False
                # C-style block-comment tracking is only meaningful for
                # JS-family files; Python has no /* */ comments.
                _JS_FAMILY = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}
                track_block_comment = fp.suffix.lower() in _JS_FAMILY
                for i, line in enumerate(lines, 1):
                    if in_triple is not None:
                        # Inside a triple-quoted string; only the matching
                        # closing delimiter can end it.
                        if in_triple in line:
                            in_triple = None
                        continue
                    if in_block_comment:
                        # Inside a C-style block comment; only "*/" ends it.
                        if "*/" in line:
                            in_block_comment = False
                            # Tail of the line after "*/" could host real code
                            # but is rare; conservative: skip the line.
                        continue
                    # TT-A-01 v2 fix #2 (round 2): detect C-style block-comment
                    # opener BEFORE the generic comment early-continue. The
                    # opener line itself often starts with "/*" which
                    # `_is_comment_or_string_line` would skip; that prevented
                    # the state from ever being set and let interior lines
                    # (which do NOT start with "/*" or "*") leak through.
                    if track_block_comment:
                        bc_idx = line.find("/*")
                        if bc_idx != -1:
                            # Same-line open+close -> not persistent; fall
                            # through to normal def handling for the tail.
                            if line.find("*/", bc_idx + 2) == -1:
                                # Persistent opener; check whether a definition
                                # match on this line comes BEFORE the opener.
                                m_pre = patterns.search(line)
                                if m_pre is not None and m_pre.start() < bc_idx:
                                    # Definition text before "/*" — real def
                                    # followed by a persistent comment opener
                                    # (rare). Return the def via fall-through
                                    # AND still enter block-comment state so
                                    # subsequent lines inside the block are
                                    # not leaked as definitions (TT-A-01 v3).
                                    in_block_comment = True
                                else:
                                    in_block_comment = True
                                    continue
                    if _is_comment_or_string_line(line):
                        continue
                    # TT-A-01 v2 fix #1 + #3: locate triple-quote openers by
                    # EARLIEST character position (matches Python
                    # tokenisation); and run the definition check FIRST so a
                    # valid `def name(): """docstring"""` line is still
                    # returned when the definition regex matches text that
                    # precedes the string opener.
                    def _earliest_triple(text: str) -> tuple[int, str | None]:
                        # TT-A-01 v3 fix: lexical left-to-right scan. The
                        # FIRST triple-quote delimiter of any type opens the
                        # string; only its matching closer can end it. A
                        # different marker type between opener and closer is
                        # string content, not a delimiter (e.g. the ''' inside
                        # """content containing ''' text""" must be ignored).
                        # After a same-line close, resume scanning the rest of
                        # the line for a later persistent opener. Return
                        # (index, marker) of the persistent opener, or
                        # (-1, None) when no unclosed opener exists.
                        pos = 0
                        while True:
                            best_idx = -1
                            best_marker: str | None = None
                            for marker in (chr(34) * 3, chr(39) * 3):
                                idx = text.find(marker, pos)
                                if idx == -1:
                                    continue
                                if best_idx == -1 or idx < best_idx:
                                    best_idx = idx
                                    best_marker = marker
                            if best_idx == -1 or best_marker is None:
                                return -1, None
                            closer = text.find(best_marker, best_idx + 3)
                            if closer == -1:
                                # No matching closer on this line: the first
                                # delimiter is a persistent opener.
                                return best_idx, best_marker
                            # String opened AND closed on this line; resume
                            # the scan after the closer.
                            pos = closer + 3

                    triple_idx, triple_marker = _earliest_triple(line)
                    m_def = patterns.search(line)
                    if m_def is not None:
                        # If a persistent triple-quote opener exists AND it
                        # starts BEFORE the definition match, the definition
                        # text is actually inside a string continuation —
                        # skip it. Otherwise this is a real definition even
                        # if a docstring opener appears later on the line.
                        if triple_idx != -1 and triple_idx < m_def.start():
                            in_triple = triple_marker
                            continue
                        entry = {"file": str(fp.relative_to(base)), "line": i, "match": line.strip(), "range_id": f"{fp.name}:{i}"}
                        if context_lines > 0:
                            body_lines = lines[i - 1:i - 1 + context_lines]
                            entry["body"] = "\n".join(body_lines)
                            entry["range_id"] = f"{fp.name}:{i}p{context_lines}"
                        results.append(entry)
                        if len(results) >= 20:
                            return results
                        # A definition may itself OPEN a docstring on the same
                        # line (e.g. `def f(): """summary`). Even though the
                        # definition was returned, the string state must be
                        # tracked for subsequent lines.
                        if triple_idx != -1:
                            in_triple = triple_marker
                        continue
                    # No definition match: enter triple-quote state if a
                    # persistent opener is present.
                    if triple_idx != -1:
                        in_triple = triple_marker
                        continue
        return results

    results = await asyncio.to_thread(_search)
    response: dict = {"results": results, "count": len(results)}
    if stale_info:
        response.update({
            "warning": "INDEX stale; returned live filesystem search results",
            "category": "stale_index_live_fallback",
            "drift": stale_info.get("drift"),
        })

    # inc was already normalized above (B-02 moved it next to the graph
    # adapter gate so the definition-only shortcut only fires for include='def').
    if inc in ("callers", "usage", "all"):
        # TT-A-02 (2026-08-10): exclude lines that are obviously not executable
        # call sites — full-line Python (#) and JS/TS (//) comments. A comment
        # mentioning `name(` is documentation, not a caller, and previously
        # polluted find_callers results. This is deliberately conservative:
        # we only strip lines whose first non-whitespace char is a comment
        # marker, so trailing inline comments on real code still count.
        _COMMENT_LINE = re.compile(r"^\s*(#|//)")
        # TT-A-02: also drop hits in non-source artifacts even when the file
        # extension slips past should_scan_for_text (e.g. .json/.md added by
        # text_file_extensions()). A call edge cannot live in data/docs.
        _NON_SOURCE_EXTS = {".json", ".md", ".txt", ".log", ".yaml", ".yml",
                            ".xml", ".csv", ".html", ".css"}

        def _scan(pat: re.Pattern[str], cap: int = 50) -> list[dict]:
            hits: list[dict] = []
            def_lines = {(r["file"], r["line"]) for r in results}
            for root2, dirs2, files2 in os.walk(base, onerror=lambda e: None):
                dirs2[:] = [d for d in dirs2 if not fo.should_skip_dir(d, base)]
                for fn2 in files2:
                    fp2 = Path(root2) / fn2
                    if fp2.suffix.lower() in _NON_SOURCE_EXTS:
                        continue
                    # B027f: bound, matching the dirs2[:] filter above.
                    if not fo.should_scan_for_text(fp2, base):
                        continue
                    try:
                        lines2 = fp2.read_text(encoding="utf-8", errors="ignore").splitlines()
                    except Exception:
                        continue
                    rel2 = str(fp2.relative_to(base))
                    for i2, line2 in enumerate(lines2, 1):
                        if (rel2, i2) in def_lines:
                            continue
                        if _COMMENT_LINE.match(line2):
                            continue
                        if pat.search(line2):
                            hits.append({"file": rel2, "line": i2, "match": line2.strip()[:200], "range_id": f"{fp2.name}:{i2}"})
                            if len(hits) >= cap:
                                return hits
            return hits

        if inc in ("callers", "all"):
            callers_pat = re.compile(rf"\b{re.escape(name)}\s*\(")
            response["callers"] = await asyncio.to_thread(_scan, callers_pat, 50)
            response["callers_count"] = len(response["callers"])
        if inc in ("usage", "all"):
            usage_pat = re.compile(rf"\b{re.escape(name)}\b")
            response["usages"] = await asyncio.to_thread(_scan, usage_pat, 100)
            response["usages_count"] = len(response["usages"])
        if inc == "all":
            response["definition"] = results
            response["include"] = "all"
        elif inc == "callers":
            # TT-A-02: when the caller explicitly asked for include='callers',
            # the response shape must isolate executable call sites. Carrying
            # `results` (the definition list) over from the base lookup made
            # find_callers indistinguishable from find_symbol(include='def')
            # plus a callers array — the defining node itself was surfaced
            # as a top-level result. Drop it so the response is callers-only.
            response.pop("results", None)
            response.pop("count", None)
            response["include"] = "callers"

    return json.dumps(response, ensure_ascii=False)


# A04: the REGEX_SEARCH_TIMEOUT payload used to carry ONLY {error, error_name,
# retryable, bounded, ca_next_action} -- it told the caller "retryable: true,
# narrow_or_simplify_regex" but handed back NOTHING to retry with: the original
# query was dropped, there was no corrected_payload/payload_example, and
# _retry_sequence had no branch so it fell through to [] (the exact A40 hole:
# retryable=True next to zero machine-readable steps). Two code paths raise this
# same error_name -- the asyncio.TimeoutError branch and the RegexSearchTimeout
# sub-branch of the generic handler -- so, per the A40 lesson, BOTH must emit the
# identical actionable shape. This single builder is that shape; a divergence
# between the two paths can't recur if neither hand-builds the dict.
#
# Bounded (token discipline): the echoed query is truncated with an explicit
# marker; the caller still holds its own original query, so corrected_payload
# only needs to signal the SHAPE change (mode='text'). regex_deadline_seconds is
# a REAL parameter clamped to [MIN, MAX]=[0.05, 10.0]s -- suggesting a larger one
# is NOT inventing a field. Per campaign lesson L-x the number itself is left
# untouched here; widening a load-scaled deadline just moves the threshold.
_TIMEOUT_QUERY_ECHO_MAX = 200


def _regex_timeout_query_echo(query: str) -> str:
    q = query or ""
    if len(q) > _TIMEOUT_QUERY_ECHO_MAX:
        return q[:_TIMEOUT_QUERY_ECHO_MAX] + f"...<truncated {len(q) - _TIMEOUT_QUERY_ECHO_MAX} chars>"
    return q


def _attach_regex_partials(resp: dict, partial_source: object | None) -> dict:
    """ISS-12: carry the work the deadline interrupted, instead of burning it.

    The worker accumulated its matches inside a spawned child and pickled them
    only after the WHOLE scan finished, so a deadline on a large repo killed the
    child and answered a bare REGEX_SEARCH_TIMEOUT -- every file already scanned
    was thrown away and the caller had no way to tell "nothing matches" from
    "we never got there". The worker now checkpoints between files and
    _run_isolated salvages the last checkpoint after reaping, so a timeout can
    report a whole-file PREFIX of the scan.

    Deliberately unchanged: ok stays False and error_name stays
    REGEX_SEARCH_TIMEOUT. The scan did NOT complete, and a partial hit list must
    never be mistaken for the full one -- that is the B012/F021 shape (nothing
    errors, results are right there, the caller trusts them). Nothing is added
    at all when no match was salvaged: an empty `results` on a scan that never
    finished would read as a confident "not found", which is the same lie in the
    other direction (this is also why the ReDoS budget tests keep their exact
    old shape -- those patterns match nothing).
    """
    rows = list(getattr(partial_source, "partial_results", None) or [])
    if not rows:
        return resp
    scanned = int(getattr(partial_source, "records_scanned", 0) or 0)
    total = int(getattr(partial_source, "records_total", 0) or 0)
    resp["results"] = rows
    resp["count"] = len(rows)
    resp["partial"] = True
    resp["truncated_by_deadline"] = True
    resp["partial_files_scanned"] = scanned
    resp["partial_files_total"] = total
    if total > 0 and 0 <= scanned <= total:
        resp["partial_files_remaining"] = total - scanned
    age_ms = getattr(partial_source, "partial_checkpoint_age_ms", None)
    if isinstance(age_ms, int):
        resp["partial_checkpoint_age_ms"] = age_ms
    resp["partial_hint"] = (
        f"These {len(rows)} match(es) are a COMPLETE prefix of the scan "
        f"({scanned} of {total or 'unknown'} candidate files), not the full "
        "result set: the remaining files were never read, so absence of a "
        "symbol here proves nothing. Re-run with the narrowing payload above "
        "to finish the scan."
    )
    resp["ca_next_action"] = "use_partial_then_narrow_regex_scope"
    return resp


def _regex_timeout_payload(query: str, effective_regex_deadline: float | None,
                           path: str, project_path: str, file_pattern: str,
                           partial_source: object | None = None) -> dict:
    from orchestrator import search_regex_worker as _rw
    q_echo = _regex_timeout_query_echo(query)
    if effective_regex_deadline is not None:
        deadline_txt = f"{effective_regex_deadline:.3f}"
        wider = round(min(effective_regex_deadline * 2.0, _rw.MAX_DEADLINE_SECONDS), 3)
    else:
        deadline_txt = "default"
        wider = _rw.MAX_DEADLINE_SECONDS
    loc = project_path or path
    loc_key = "project_path" if project_path else "path"
    corrected: dict = {"query": q_echo, "mode": "text"}
    if loc:
        corrected[loc_key] = loc
    if file_pattern and file_pattern != "*":
        corrected["file_pattern"] = file_pattern
    payload_example: dict = {"query": q_echo, "mode": "regex", "regex_deadline_seconds": wider}
    if loc:
        payload_example[loc_key] = loc
    if file_pattern and file_pattern != "*":
        payload_example["file_pattern"] = file_pattern
    return _attach_regex_partials({
        "ok": False,
        "error": f"regex search exceeded hard deadline of {deadline_txt}s",
        "error_name": "REGEX_SEARCH_TIMEOUT",
        "retryable": True,
        "bounded": True,
        "query": q_echo,
        "corrected_payload": corrected,
        "payload_example": payload_example,
        "retry_sequence": [
            {"tool": "search_code", "payload": corrected,
             "note": "fall back to literal text mode -- no backtracking, no wall-clock blowup"},
            {"tool": "search_code", "payload": payload_example,
             "note": f"or keep regex but narrow file_pattern; a larger explicit "
                     f"regex_deadline_seconds (<= {_rw.MAX_DEADLINE_SECONDS}s) only "
                     f"helps a genuinely well-behaved pattern"},
        ],
        "hint": "Regex hit the wall-clock deadline (catastrophic backtracking or too "
                "much input). Resend with mode='text' for a literal search, and/or add "
                "a file_pattern to shrink the input.",
        "ca_next_action": "narrow_or_simplify_regex",
    }, partial_source)


def _regex_budget_payload(query: str, path: str, project_path: str,
                          file_pattern: str, detail: str = "") -> dict:
    """B019: the BUDGET refusal, made actionable.

    The old branch answered ok:False + error_name +
    ca_next_action="narrow_path_or_reduce_regex_input" and nothing else, so the
    generic envelope filled payload_example with {"project_path": ...} -- the
    same unscoped call that had just failed. Worse, the advice named `path`,
    which `regex_search` did not accept at all until this same fix. A refusal a
    caller cannot act on costs exactly as much as a wrong answer and teaches
    nothing.

    The 16 MB ceiling itself stays: it is a real, named limit and refusing loudly
    is correct. Only the guidance changes -- it now names the two fields that
    genuinely shrink the input (`path`, `file_pattern`) and hands back concrete
    payloads, the same shape _regex_timeout_payload already used for its sibling.
    """
    from orchestrator import search_regex_worker as _rw
    q_echo = _regex_timeout_query_echo(query)
    loc = project_path or path
    loc_key = "project_path" if project_path else "path"

    # Narrow by directory: the field the old ca_next_action already advised.
    by_path: dict = {"query": q_echo, "mode": "regex"}
    if loc:
        by_path[loc_key] = loc
    by_path["path"] = (path or "<a subdirectory of the repo, not its root>")

    # Narrow by glob: works even when the whole tree is genuinely in scope.
    by_glob: dict = {"query": q_echo, "mode": "regex"}
    if loc:
        by_glob[loc_key] = loc
    by_glob["file_pattern"] = file_pattern if file_pattern and file_pattern != "*" else "*.py"

    return {
        "ok": False,
        # The worker raises this same error_name for two different ceilings
        # (MAX_RECORDS and MAX_INPUT_BYTES), so carry ITS message rather than
        # restating one of them here -- a builder that names the wrong ceiling is
        # the same dishonesty in a smaller place.
        "error": detail or "regex input exceeded a worker budget",
        "error_name": "REGEX_INPUT_BUDGET_EXCEEDED",
        "retryable": True,
        "bounded": True,
        "query": q_echo,
        "input_budget_bytes": _rw.MAX_INPUT_BYTES,
        "input_budget_records": _rw.MAX_RECORDS,
        "corrected_payload": by_glob,
        "payload_example": by_path,
        "accepted_narrowing_fields": ["path", "file_pattern"],
        "retry_sequence": [
            {"tool": "search_code", "payload": by_glob,
             "note": "narrow the input by glob -- fewer files read, same tree in scope"},
            {"tool": "search_code", "payload": by_path,
             "note": "or scope to a subdirectory; `path` and `file_pattern` compose"},
        ],
        "hint": "The regex never ran: the candidate bytes exceeded the ceiling "
                "before matching started. Shrink the input with `file_pattern` "
                "and/or `path` -- both are accepted by search_code and by "
                "regex_search. Raising the ceiling is not a caller-side option.",
        "ca_next_action": "narrow_path_or_reduce_regex_input",
    }


async def search_code(query: str = "", path: str = "", project_path: str = "",
                      file_pattern: str = "*", literal_search: bool = True,
                      mode: str = "text", query_encoding: str = "plain",
                      max_context_lines: int = MAX_SEARCH_CONTEXT_LINES,
                      max_results: int = MAX_SEARCH_RESULTS,
                      flags: str = "i", force: bool = False,
                      include_impact: bool = False, regex_deadline_seconds: float | None = None, **aliases) -> str:
    import time

    search_started_at = time.monotonic()
    snapshot_retry = int(aliases.pop("_snapshot_retry", 0) or 0)

    # TA-7 fix (2026-08-18): file_glob is a natural name callers reach for; it used
    # to fall into **aliases unmapped, so a file_glob="*.py" filter silently did
    # nothing (or tripped UNSUPPORTED_FIELD under strict validation). Map the
    # common file-filter aliases onto file_pattern when the caller left it default.
    if (not file_pattern or file_pattern == "*"):
        _fp_alias = aliases.get("file_glob") or aliases.get("glob") or aliases.get("pattern_file")
        if _fp_alias:
            file_pattern = str(_fp_alias)

    alias_pattern = aliases.get("pattern") or aliases.get("regex")
    alias_forced_regex = False
    if not query and alias_pattern:
        query = alias_pattern
        literal_search = False
        alias_forced_regex = True
    query = query or aliases.get("q") or aliases.get("name") or aliases.get("symbol") or aliases.get("symbol_name") or ""
    path = path or aliases.get("file_path", "")
    _path = path or project_path

    decoded_query, _, decode_err = fo.decode_text_payload(query, query_encoding)
    if decode_err:
        decode_err.setdefault("error_name", "INVALID_QUERY_ENCODING")
        return json.dumps(decode_err, ensure_ascii=False)
    if decoded_query is not None:
        query = decoded_query

    raw_mode = (mode or ("text" if literal_search else "regex")).lower().strip()
    if alias_forced_regex and raw_mode == "text":
        raw_mode = "regex"
    # CSLt 2026-07-16 (upgrade #1): mode='auto' detects intent from the query —
    # bare identifier -> symbol lookup, anything else -> literal text search. And
    # _SEARCH_MODE_ALIASES is now actually applied: it was only advertised in the
    # UNSUPPORTED_MODE payload before, so documented aliases like mode='literal'
    # were rejected instead of mapping to their canonical mode.
    if raw_mode == "auto":
        raw_mode = "symbol" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", (query or "").strip()) else "text"
    mode = _SEARCH_MODE_ALIASES.get(raw_mode, raw_mode)
    if mode == "base64":
        return fo.json_error(
            "search_code: mode='base64' was removed. Use query_encoding='base64' instead.",
            error_name="SCHEMA_VIOLATION",
            type="SCHEMA_VIOLATION",
            retryable=False,
            ca_next_action="resend_with_query_encoding_base64_and_mode_text_or_regex",
            accepted_modes=_SEARCH_CODE_MODES,
            accepted_encodings=["plain", "base64"],
        )

    if mode in {"symbol", "callers", "definition_usage"}:
        if not query:
            return json.dumps({"ok": False, "error": "Missing query", "error_name": "MISSING_REQUIRED_FIELD", "accepted_fields": ["query", "name", "symbol", "symbol_name", "project_path", "path", "mode"]}, ensure_ascii=False)
        include = {"symbol": "def", "callers": "callers", "definition_usage": "all"}[mode]
        return await find_symbol(name=query, path=path, project_path=project_path, context_lines=max_context_lines, include=include, include_impact=include_impact)

    if mode == "index":
        if not project_path and not path:
            return json.dumps({"ok": False, "error": "Missing project_path", "error_name": "MISSING_REQUIRED_FIELD", "accepted_fields": ["project_path", "query", "group", "file_pattern", "max_results", "mode"]}, ensure_ascii=False)
        from code_tools import query_index
        return await query_index(project_path=project_path or path, query=query, group=aliases.get("group"), min_complexity=aliases.get("min_complexity"), min_loc=aliases.get("min_loc"), file_pattern=file_pattern or aliases.get("glob") or aliases.get("pattern"), max_results=max_results, auto_refresh=aliases.get("auto_refresh", True))

    if mode == "dom_candidates":
        page_id = aliases.get("page_id") or aliases.get("page") or ""
        if not page_id:
            return json.dumps({"ok": False, "error": "Missing page_id", "error_name": "MISSING_REQUIRED_FIELD", "accepted_fields": ["page_id", "filter", "max_items", "mode"]}, ensure_ascii=False)
        from tools_v15 import query_dom_candidates
        return await query_dom_candidates(page_id=page_id, filter=aliases.get("filter") or query or "all", max_items=aliases.get("max_items", aliases.get("limit", aliases.get("max_results", max_results))), auto_artifact=aliases.get("auto_artifact", False), project_path=project_path, round_id=aliases.get("round_id", ""), auto_artifact_max_bytes=aliases.get("auto_artifact_max_bytes", 4096))

    if mode not in {"text", "regex", "smart"}:
        return json.dumps({"ok": False, "error": f"UNSUPPORTED_MODE: {mode}", "error_name": "UNSUPPORTED_FIELD", "accepted_modes": _SEARCH_CODE_MODES, "mode_aliases": _SEARCH_MODE_ALIASES}, ensure_ascii=False)
    if not query:
        return json.dumps({"ok": False, "error": "Missing query", "error_name": "MISSING_REQUIRED_FIELD", "accepted_fields": ["query", "pattern", "regex", "q", "project_path", "path", "mode"]}, ensure_ascii=False)
    if not _path:
        return json.dumps({"ok": False, "error": "Missing path - pass 'path' or 'project_path'", "error_name": "MISSING_REQUIRED_FIELD"}, ensure_ascii=False)

    regex_worker = None
    effective_regex_deadline = None
    _narrow_warn = ""
    # ISS-12: the RegexSearchTimeout instance that carries the salvaged partial
    # matches. It has to be held here because the R39 adaptive-retry loop below
    # re-raises a BRAND NEW asyncio.TimeoutError when both attempts run out of
    # budget -- the original exception, and with it every match already found,
    # would otherwise be dropped on the floor before any handler sees it.
    _last_regex_timeout = None
    if mode == "regex":
        from orchestrator import search_regex_worker as regex_worker

        effective_regex_deadline = regex_worker.normalize_deadline_seconds(
            regex_deadline_seconds
            if regex_deadline_seconds is not None
            else regex_worker.DEFAULT_DEADLINE_SECONDS,
        )
        regex_deadline_at = search_started_at + effective_regex_deadline
        # Task 3 auto-chain fix: proactive narrow hint for regex on root
        if (not file_pattern or file_pattern == "*") and not path:
            _narrow_warn = (
                "Regex on full repo may timeout. "
                "Add file_pattern='*.py' or path='orchestrator/' to narrow."
            )
        else:
            _narrow_warn = ""
        try:
            regex_worker.validate_pattern_budget(query)
        except regex_worker.RegexSearchBudgetExceeded as exc:
            return json.dumps({
                "ok": False,
                "error": str(exc),
                "error_name": exc.error_name,
                "retryable": True,
                "bounded": True,
                "ca_next_action": "shorten_regex_pattern",
            }, ensure_ascii=False)

    # CAN-053 P2: selectivity estimator — catch-all patterns walk the whole
    # tree for near-zero information. Refuse with guidance unless force=true.
    _q_sel = (query or "").strip()
    _broad = (
        _q_sel in {".", ".*", ".+", "*", ".?"}
        or (mode in {"text", "smart"} and len(_q_sel) < 3 and (file_pattern or "*") == "*")
    )
    if _broad and not force:
        return json.dumps({
            "ok": False,
            "error": f"SEARCH_TOO_BROAD: query {_q_sel!r} would scan every file with near-zero selectivity",
            "error_name": "SEARCH_TOO_BROAD",
            "retryable": True,
            "hint": "Narrow the query (>=3 chars), add file_pattern, or use query_index(file_pattern=...) / project_context_pack for inventory. Pass force=true to run anyway.",
            "ca_next_action": "narrow_query_or_use_query_index",
            "corrected_payload": {"query": _q_sel, "file_pattern": "*.py", "force": True},
        }, ensure_ascii=False)
    base = Path(_path)
    if path and project_path and not base.is_absolute():
        candidate = Path(project_path) / path
        if candidate.exists():
            base = candidate
        else:
            candidate2 = Path(project_path) / path.lstrip("./").lstrip(".\\")
            if candidate2.exists():
                base = candidate2
    if not base.exists():
        return json.dumps({"ok": False, "error": f"Path not found: {_path}", "error_name": "PATH_NOT_FOUND", "tried_paths": [str(Path(_path)), str(Path(project_path) / path) if (project_path and path) else None], "ca_next_action": "verify_path_or_pass_project_path_with_relative_path", "hint": "Pass path as absolute OR pair (project_path, path) where path is relative to project root."}, ensure_ascii=False)

    if mode == "smart":
        raw_tokens = [t for t in re.split(r"[\s,;:|/\\\"'`()\[\]{}]+", query) if len(t) >= 2]
        pattern = re.escape(query.strip()) if len(raw_tokens) <= 1 else "|".join(re.escape(t) for t in raw_tokens)
    elif mode == "text":
        pattern = re.escape(query)
    else:
        pattern = query
    re_flags = 0
    if "i" in (flags or ""):
        re_flags |= re.IGNORECASE
    if "m" in (flags or ""):
        re_flags |= re.MULTILINE
    if "s" in (flags or ""):
        re_flags |= re.DOTALL
    pat = None
    if mode != "regex":
        try:
            pat = re.compile(pattern, re_flags)
        except re.error as exc:
            return json.dumps({"error": f"Invalid regex: {exc}", "error_name": "INVALID_REGEX"})
    try:
        effective_context = max(0, min(int(max_context_lines), 5))
    except (TypeError, ValueError):
        effective_context = MAX_SEARCH_CONTEXT_LINES
    try:
        effective_max_results = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
    except (TypeError, ValueError):
        effective_max_results = MAX_SEARCH_RESULTS
    # B026: 0 / negative / None -> cap off (scan everything, the pre-B026 behaviour).
    _cap_raw = aliases.get("max_file_bytes", aliases.get("max_record_bytes", MAX_TEXT_RECORD_BYTES))
    try:
        text_record_cap = max(0, int(_cap_raw))
    except (TypeError, ValueError):
        text_record_cap = MAX_TEXT_RECORD_BYTES

    snapshot_root = _snapshot_root_for(base, project_path)
    base_resolved = base.resolve()
    base_is_file = base.is_file()
    if not base_resolved.is_relative_to(snapshot_root):
        return json.dumps({
            "ok": False,
            "error": f"Search path escapes snapshot root: {base_resolved}",
            "error_name": "PATH_OUTSIDE_PROJECT",
            "snapshot_root": str(snapshot_root),
        }, ensure_ascii=False)

    def _search_snapshot(
        expected_snapshot_id: str | None,
        producer_deadline_at: float | None,
    ) -> dict:
        def _regex_remaining() -> float:
            remaining = producer_deadline_at - time.monotonic()
            if remaining < regex_worker.MIN_DEADLINE_SECONDS:
                raise regex_worker.RegexSearchTimeout(
                    f"regex search exceeded hard deadline of {effective_regex_deadline:.3f}s",
                )
            return remaining

        if mode == "regex":
            _regex_remaining()
        try:
            if mode == "regex":
                pin = _pin_or_create_snapshot(
                    snapshot_root,
                    expected_id=expected_snapshot_id,
                    deadline_at=producer_deadline_at,
                )
            else:
                pin = _pin_or_create_snapshot(
                    snapshot_root,
                    expected_id=expected_snapshot_id,
                )
        except search_snapshots.SnapshotDeadlineExceeded as exc:
            raise regex_worker.RegexSearchTimeout(
                f"regex search exceeded hard deadline of {effective_regex_deadline:.3f}s",
            ) from exc
        if mode == "regex":
            _regex_remaining()

        try:
            if mode == "regex":
                regex_worker.preflight_pattern(
                    pattern,
                    re_flags,
                    deadline_seconds=_regex_remaining(),
                )
                _regex_remaining()

            records: list[tuple[str, str, bytes]] = []
            base_relative = base_resolved.relative_to(snapshot_root).as_posix()
            base_prefix = (
                None
                if base_relative in {"", "."} or base_is_file
                else PurePosixPath(base_relative)
            )
            regex_record_count = 0
            regex_total_bytes = 0

            def _append_record(
                relative_path: str,
                label: str,
                expected_size: int,
            ) -> None:
                nonlocal regex_record_count, regex_total_bytes
                virtual_path = snapshot_root.joinpath(*PurePosixPath(relative_path).parts)
                # B027f: THIS is the gate a caller's result actually comes out of.
                # Measured: with the publish site already bound (via
                # refresh_project_map) the snapshot held build/made.py and
                # search_code still refused it, on a snapshot it did not
                # republish. Fixing the publish alone would have looked correct
                # in the diff and left the tool answering exactly as wrong.
                if not fo.should_scan_for_text(virtual_path, snapshot_root):
                    return
                if mode == "regex":
                    _regex_remaining()
                    regex_worker.validate_next_record_budget(
                        regex_record_count,
                        regex_total_bytes,
                        expected_size,
                    )
                payload = pin.read_bytes(relative_path)
                if mode == "regex":
                    next_total = regex_worker.validate_next_record_budget(
                        regex_record_count,
                        regex_total_bytes,
                        len(payload),
                    )
                    regex_worker.validate_line_budget(payload)
                    regex_record_count += 1
                    regex_total_bytes = next_total
                    _regex_remaining()
                records.append((relative_path, label, payload))

            results: list[dict] = []
            skipped_large: list[dict] = []
            skipped_large_total = 0   # dem THAT; skipped_large chi giu 20 vi du dau

            def _search_text_record(relative_path: str, label: str, size: int) -> None:
                virtual_path = snapshot_root.joinpath(*PurePosixPath(relative_path).parts)
                # B027f: bound for the same reason as _append_record above.
                if not fo.should_scan_for_text(virtual_path, snapshot_root):
                    return
                # B026: a record past the cap is reported, not silently dropped, so the
                # caller can see WHAT was left out and re-run with max_file_bytes=0.
                if text_record_cap and size > text_record_cap:
                    nonlocal skipped_large_total
                    skipped_large_total += 1
                    if len(skipped_large) < 20:
                        skipped_large.append({"file": label, "size_bytes": size})
                    return
                payload = pin.read_bytes(relative_path)
                lines = payload.decode("utf-8", errors="ignore").splitlines()
                for i, line in enumerate(lines, 1):
                    if pat.search(line):
                        before = ""
                        after = ""
                        if effective_context:
                            before = "\n".join(
                                ln.strip()
                                for ln in lines[max(0, i - 1 - effective_context):i - 1]
                            )
                            after = "\n".join(
                                ln.strip()
                                for ln in lines[i:min(len(lines), i + effective_context)]
                            )
                        results.append({
                            "file": label,
                            "line": i,
                            "match": line.strip(),
                            "context_before": before,
                            "context_after": after,
                        })
                        if len(results) >= effective_max_results:
                            return

            for record in pin.iter_records(base_resolved):
                relative_path = str(record["path"])
                relative = PurePosixPath(relative_path)
                if not base_is_file and file_pattern != "*":
                    pattern_norm = str(file_pattern).replace("\\", "/")
                    relative_norm = relative.as_posix()
                    # A slash makes this a project-root-relative pattern. Do not
                    # suffix-match it: "bin/x.py" must not also admit
                    # "archive/copy/bin/x.py". Slash-free patterns retain the
                    # long-standing basename behavior.
                    pattern_matches = (
                        fnmatch.fnmatch(relative_norm, pattern_norm)
                        if "/" in pattern_norm
                        else fnmatch.fnmatch(relative.name, pattern_norm)
                    )
                    if not pattern_matches:
                        continue
                if base_is_file:
                    label = base.name
                elif base_prefix is None:
                    label = relative.as_posix()
                else:
                    label = relative.relative_to(base_prefix).as_posix()
                if mode == "regex":
                    _append_record(relative_path, label, int(record["size"]))
                else:
                    _search_text_record(relative_path, label, int(record["size"]))
                    if len(results) >= effective_max_results:
                        break

            if mode == "regex":
                results = regex_worker.search_records(
                    pattern,
                    re_flags,
                    records,
                    max_results=effective_max_results,
                    context_lines=effective_context,
                    deadline_seconds=_regex_remaining(),
                )
            payload_out = {"results": results, "snapshot_id": pin.snapshot_id}
            if skipped_large:
                payload_out["skipped_large_files"] = skipped_large
                payload_out["skipped_large_count"] = skipped_large_total
                payload_out["skipped_large_listed"] = len(skipped_large)
                payload_out["skipped_large_hint"] = (
                    f"{skipped_large_total} file lon hon max_file_bytes={text_record_cap} "
                    "da bi bo qua trong mode='text'. Dat max_file_bytes=0 de quet ca chung."
                )
            return payload_out
        finally:
            pin.release()

    try:
        for pin_attempt in range(2):
            snapshot_token = _search_snapshot_token(snapshot_root)
            expected_snapshot_id = (
                str(snapshot_token[1]) if snapshot_token[0] == "snapshot" else None
            )
            search_key = (
                snapshot_token,
                str(base_resolved).casefold(), pattern, re_flags, file_pattern,
                mode, effective_regex_deadline,
                effective_context, effective_max_results,
                text_record_cap,   # B026: khac cap = khac ket qua, khong duoc dung chung
            )
            wait_timeout = None
            producer_deadline_at = None
            if mode == "regex":
                wait_timeout = regex_deadline_at - time.monotonic()
                if wait_timeout <= 0:
                    raise regex_worker.RegexSearchTimeout(
                        f"regex search exceeded hard deadline of {effective_regex_deadline:.3f}s",
                    )
                producer_deadline_at = regex_deadline_at
            # R39: adaptive retry — if first regex attempt times out, retry
            # with extended deadline (up to MAX_DEADLINE_SECONDS). Prevents
            # one-shot timeout on full-repo scans where subprocess overhead
            # (pickle + spawn) is significant.
            _regex_retried = False
            for _rt_attempt in range(2):
                try:
                    producer_payload = await _run_search_singleflight(
                        search_key,
                        lambda: _search_snapshot(expected_snapshot_id, producer_deadline_at),
                        owner_transfer=lambda: None,
                        owner_done=lambda: None,
                        timeout=wait_timeout,
                    )
                    break  # success — exit retry loop
                except asyncio.TimeoutError:
                    if (_regex_retried or mode != "regex"
                            or regex_deadline_seconds is not None):
                        raise
                    _regex_retried = True
                    effective_regex_deadline = min(
                        effective_regex_deadline * 2.0,
                        regex_worker.MAX_DEADLINE_SECONDS,
                    )
                    regex_deadline_at = search_started_at + effective_regex_deadline
                    wait_timeout = regex_deadline_at - time.monotonic()
                    producer_deadline_at = regex_deadline_at
                    search_key = (
                        snapshot_token,
                        str(base_resolved).casefold(), pattern, re_flags, file_pattern,
                        mode, effective_regex_deadline,
                        effective_context, effective_max_results,
                        text_record_cap,
                    )
                    _narrow_warn = (
                        "Regex on full repo timed out at initial deadline; "
                        "retrying with extended deadline. "
                        "Add file_pattern='*.py' or path='orchestrator/' to narrow."
                    )
                except Exception as exc:
                    if (regex_worker is not None
                            and isinstance(exc, regex_worker.RegexSearchTimeout)):
                        # ISS-12: keep it BEFORE the re-raise decision -- both
                        # the `raise` here and the loop's else-branch discard
                        # this instance, and its partial_results are the only
                        # copy that survived the child being killed.
                        _last_regex_timeout = exc
                    if (regex_worker is None
                            or not isinstance(exc, regex_worker.RegexSearchTimeout)
                            or _regex_retried
                            or regex_deadline_seconds is not None):
                        raise
                    _regex_retried = True
                    effective_regex_deadline = min(
                        effective_regex_deadline * 2.0,
                        regex_worker.MAX_DEADLINE_SECONDS,
                    )
                    regex_deadline_at = search_started_at + effective_regex_deadline
                    wait_timeout = regex_deadline_at - time.monotonic()
                    producer_deadline_at = regex_deadline_at
            else:
                # inner loop exhausted without break — both attempts timed out.
                # Re-raise as asyncio.TimeoutError to enter the outer handler.
                raise asyncio.TimeoutError(
                    f"regex search exceeded hard deadline of {effective_regex_deadline:.3f}s"
                )
            break  # success — exit outer snapshot loop
        else:  # pragma: no cover - loop either returns or raises
            raise search_snapshots.SnapshotUnavailable("snapshot changed during pin")
    except search_snapshots.SnapshotUnavailable:
        if (snapshot_retry >= 1 or pin_attempt
                or _search_snapshot_token(snapshot_root) == snapshot_token):
            raise
        return await search_code(
            query=query,
            path=path,
            project_path=project_path,
            file_pattern=file_pattern,
            literal_search=literal_search,
            mode=mode,
            query_encoding="plain",
            max_context_lines=max_context_lines,
            max_results=max_results,
            flags=flags,
            force=force,
            include_impact=include_impact,
            regex_deadline_seconds=regex_deadline_seconds,
            _snapshot_retry=snapshot_retry + 1,
            **aliases,
        )
    except asyncio.TimeoutError:
        # ISS-12: _last_regex_timeout, not the asyncio.TimeoutError we are
        # holding -- that one is a synthetic re-raise and carries no matches.
        _timeout_resp = _regex_timeout_payload(query, effective_regex_deadline, path, project_path, file_pattern,
                                              partial_source=_last_regex_timeout)
        if _narrow_warn:
            _timeout_resp["ca_chain_hint"] = _narrow_warn
            _timeout_resp["ca_chain_next"] = "search_code with file_pattern or path"
        return json.dumps(_timeout_resp, ensure_ascii=False)
    except Exception as exc:
        if regex_worker is not None and isinstance(exc, regex_worker.RegexSearchInvalid):
            return json.dumps({
                "ok": False,
                "error": f"Invalid regex: {exc}",
                "error_name": "REGEX_INVALID",
                "retryable": True,
                "corrected_payload": {"query": query, "mode": "text"},
                "hint": "Pattern fails to compile. If you meant a literal/symbol, resend with mode='text'; otherwise escape the metacharacters.",
                "ca_next_action": "resend_with_mode_text_or_escaped_regex",
                "bounded": True,
            }, ensure_ascii=False)
        # A04: RegexSearchTimeout raised from the worker carries the SAME
        # error_name ("REGEX_SEARCH_TIMEOUT") as the asyncio.TimeoutError branch
        # above. If it fell through to the generic RegexSearchError handler it
        # would emit the OLD degenerate shape -- two paths, one error_name,
        # divergent contracts (exactly A40 defect 2). Route it to the one shared
        # builder so the caller always gets query + corrected_payload + retry_sequence.
        if regex_worker is not None and isinstance(exc, regex_worker.RegexSearchTimeout):
            _rt_resp = _regex_timeout_payload(query, effective_regex_deadline, path, project_path, file_pattern,
                                              partial_source=exc)
            if _narrow_warn:
                _rt_resp["ca_chain_hint"] = _narrow_warn
                _rt_resp["ca_chain_next"] = "search_code with file_pattern or path"
            return json.dumps(_rt_resp, ensure_ascii=False)
        if regex_worker is not None and isinstance(exc, regex_worker.RegexSearchError):
            retryable = isinstance(
                exc,
                (regex_worker.RegexSearchTimeout, regex_worker.RegexSearchBudgetExceeded),
            )
            # B019: BUDGET goes through its own builder, for the same reason the
            # TimeoutError branch above does -- one error_name must not have two
            # contracts. The bare shape below could only carry a next_action, and
            # a next_action that names a field the caller cannot pass is advice
            # in a circle.
            if isinstance(exc, regex_worker.RegexSearchBudgetExceeded):
                return json.dumps(
                    _regex_budget_payload(query, path, project_path, file_pattern,
                                          detail=str(exc)),
                    ensure_ascii=False,
                )
            if isinstance(exc, regex_worker.RegexSearchTimeout):
                next_action = "narrow_or_simplify_regex"
            else:
                next_action = "inspect_regex_worker_failure"
            return json.dumps({
                "ok": False,
                "error": str(exc),
                "error_name": exc.error_name,
                "retryable": retryable,
                "bounded": True,
                "ca_next_action": next_action,
            }, ensure_ascii=False)
        raise
    results = producer_payload["results"]
    response = {
        # B018: `ok` on the SUCCESS path too. Every failure branch above answers
        # ok:False, and this branch answered nothing at all -- so the ordinary
        # caller guard `if not resp.get("ok")` read a search with real hits as a
        # failure, a missing key being falsy exactly like False. This is the
        # safe-looking direction of the B012/F021/B017 family: nothing errors, the
        # results are right there, and the caller throws them away. A search that
        # RAN is ok=True; "found nothing" is carried by count/stale_index, never
        # by ok.
        "ok": True,
        "results": results,
        "count": len(results),
        "mode": mode,
        "max_results": effective_max_results,
        "max_context_lines": effective_context,
        "bounded": True,
        "snapshot_id": producer_payload["snapshot_id"],
    }
    # Task 3 auto-chain fix: inject regex narrow warning into response
    if mode == "regex" and _narrow_warn:
        response["ca_chain_hint"] = _narrow_warn
        response["ca_chain_next"] = "search_code with file_pattern or path"
    # B026: carry the cap report OUT. This matters most on count==0: the A03 rule below
    # says a 0-hit must never silently read as "not there", and a hit that lived inside a
    # capped file is exactly that shape. The caller sees what was left out and how to
    # get it back, instead of a confident empty answer.
    if producer_payload.get("skipped_large_files"):
        response["skipped_large_files"] = producer_payload["skipped_large_files"]
        response["skipped_large_count"] = producer_payload["skipped_large_count"]
        response["skipped_large_listed"] = producer_payload["skipped_large_listed"]
        response["skipped_large_hint"] = producer_payload["skipped_large_hint"]
        response["max_file_bytes"] = text_record_cap
    # A03: search_code serves an immutable published snapshot that is NOT reconciled
    # against disk for files changed OUTSIDE [A] (native edit / git checkout / IDE).
    # A 0-hit over a stale snapshot is therefore INCONCLUSIVE ("khong ket luan duoc"),
    # not "not found" -- silently degrading here is what let a real committed symbol be
    # declared a phantom. The drift probe hashes every indexed file (O(repo)), so it is
    # paid ONLY on count==0 (the exact defect case); the hot count>0 path is untouched.
    #
    # A03b: ask cache_delta DIRECTLY rather than via _index_drift(). That helper
    # collapses five different situations into a bare None -- no project_path, inside
    # the 30s post-refresh NOISE-SUPPRESSION window, cache_delta raised, cache_delta
    # returned ok:false, and genuinely-clean -- and only the last one means clean.
    # Measured: an external edit 1s after generate_project_map gave
    # _index_drift -> None while cache_delta reported modified:['target.py'], so
    # search_code certified a clean index over an edit it could see. The suppress
    # window exists to cut response noise; it was never a staleness measurement, and
    # it must not be allowed to answer as one. Three states, never two:
    #   stale_index=True   -- measured, drift found
    #   stale_index=False  -- measured, clean
    #   stale_index=None   -- NOT measured; inconclusive + stale_index_reason says why
    if not results:
        drift_root = project_path or str(snapshot_root)
        stale_reason = ""
        delta = None
        try:
            import code_tools
            if not drift_root:
                stale_reason = "no_project_path"
            else:
                raw_delta = json.loads(await code_tools.cache_delta(drift_root))
                if not isinstance(raw_delta, dict) or not raw_delta.get("ok"):
                    stale_reason = "delta_unavailable"
                    response["stale_index_error"] = str(
                        (raw_delta or {}).get("error")
                        or "cache_delta returned ok:false")
                else:
                    delta = raw_delta
        except Exception as exc:
            # Fail loud: a broken staleness probe must NOT be reported as a clean index.
            stale_reason = "probe_failed"
            response["stale_index_error"] = str(exc)
        if delta is None:
            response["stale_index"] = None
            response["stale_index_reason"] = stale_reason or "probe_failed"
            response["inconclusive"] = True
            response["ca_next_action"] = "refresh_or_narrow"
        else:
            stale_count = (len(delta.get("added") or [])
                           + len(delta.get("modified") or [])
                           + len(delta.get("deleted") or []))
            if stale_count:
                response["stale_index"] = True
                response["stale_files_count"] = stale_count
                response["stale_index_reason"] = "drift_detected"
                response["inconclusive"] = True
                response["ca_next_action"] = "refresh_or_narrow"
            else:
                response["stale_index"] = False
    if not results and mode == "text" and len(query.split()) > 1:
        response.setdefault("tip", "0 hits for text multi-word query. Try regex "
                                   "OR-split or search_code(mode='symbol').")
        response.setdefault("ca_next_action", "retry_with_regex_or_symbol_mode")
    # Task 3 auto-chain rule 5: hint for read_file_range when search found file+line
    if results and mode in {"text", "regex", "smart"}:
        _has_line_hits = any(r.get("line") or r.get("line_number") for r in results if isinstance(r, dict))
        if _has_line_hits:
            response["ca_hint"] = "read_file_range available for detailed view of matched files"
    return json.dumps(response, ensure_ascii=False)
