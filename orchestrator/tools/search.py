from __future__ import annotations

import asyncio
from bisect import bisect_right
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
from orchestrator.text_lines import split_lines

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

# D2 2026-09-21: MAX_TEXT_RECORD_BYTES above caps the size of ONE record; nothing
# capped the WALL CLOCK of the whole non-regex call. Only mode='regex' self-bounded
# (regex_deadline_seconds, 0.05-15.0), so text/smart reached
# _run_search_singleflight with timeout=None and ran as long as the glob's bytes
# took. Measured on ToolQ (838 files, one query, mode='text', warm): the exact
# filename answered in 0.36s, file_pattern='*.py' in 25.33s -- ~70x for a scope the
# caller widened for free. At 25.33s warm a cold run walks past a 30s client
# timeout, and the abandoned call left only `"POST /api/call/search_code" 200 0` in
# the access log while USAGE_LOG recorded ok=True: "CA says timeout" and "[A] says
# success" were both true and nothing in [A] could reconcile them.
#
# 60.0s is chosen, not tuned: it is 2.4x the measured worst HEALTHY call, so no
# working search starts failing, and it equals executor_lanes._QUEUE_WAIT_MAX_SECONDS
# so the FAIR_ADMISSION deadline derived from it stays inside an already-designed
# range. It is a module constant and NOT a caller parameter on purpose -- see the
# comment at the wait_timeout site for why a knob would be raised in the wrong
# direction. Set SEARCH_TEXT_DEADLINE_SECONDS=0 to disable the bound entirely.
SEARCH_TEXT_DEADLINE_SECONDS = float(
    os.environ.get("SEARCH_TEXT_DEADLINE_SECONDS", "60.0"))

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
# P15: per-root reconcile epoch, stamped when a stale-drift reconcile is
# applied. The singleflight key mixes this in (see the snapshot_token build):
# the republish that follows a reconcile flips snapshot_token, but a call
# that STARTS while the reconcile is mid-publish still reads the pre-publish
# CURRENT and would otherwise join the pre-reconcile future. Written and read
# only on the event-loop thread, so a plain dict needs no lock.
_SEARCH_RECONCILE_EPOCH: dict[str, int] = {}
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
    extend_deadline=None,
) -> dict:
    """Share one producer future while each caller keeps independent cancellation.

    ISS-20: `extend_deadline` is an optional zero-arg callback, consulted once
    and only after `timeout` has already expired. It returns how many extra
    seconds to keep waiting for the SAME shared future (0.0, or no callback at
    all, means give up now -- the historical behaviour). It exists because a
    regex producer's budget is per-phase: the caller can only bound the phase
    it can observe, and the phase that owns the ISS-12 checkpoint must not be
    cut off by the phase-1 clock.
    """
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
        # ISS-20 / #12(b): `timeout` bounds the producer's FIRST phase only. If
        # the producer has meanwhile earned a second clock -- it is running the
        # isolated matcher, which enforces its own deadline and holds the only
        # copy of the ISS-12 checkpoint -- extend_deadline() says how much
        # longer to wait for it. Giving up here instead would abandon a live
        # child and discard the rows it had already found. A producer still in
        # its first phase, or a caller that passed no callback, gets 0.0 and
        # the old behaviour, so the wall-clock bound on a wedged snapshot pin
        # is unchanged.
        extra = 0.0
        if extend_deadline is not None:
            try:
                extra = float(extend_deadline() or 0.0)
            except Exception:
                extra = 0.0
        if extra > 0.0:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(loop_future), timeout=extra,
                )
            except asyncio.TimeoutError:
                pass
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


async def _find_symbol_unbounded(name: str = "", path: str = "", project_path: str = "",
                                 context_lines: int = 0, include: str = "def",
                                 name_encoding: str = "plain",
                                 include_impact: bool = False,
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
        # D2 completion: this self-call targets the IMPL, not the bounded public
        # find_symbol wrapper. Two reasons, both about not inventing a new failure
        # mode: (1) the outer wrapper already caps total wall clock, so a second
        # nested wait_for buys nothing; (2) if the inner call timed out it would
        # return a SEARCH_TIMEOUT dict into base_raw, and the code below reads
        # base['results'] -- it would silently degrade to "symbol not found"
        # instead of reporting the timeout. One bound at the boundary.
        base_raw = await _find_symbol_unbounded(
            name=name, path=path, project_path=project_path,
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
        # B028: this was the ONE unshaped exit in find_symbol. The two
        # MISSING_REQUIRED_FIELD returns just above carry ok / error_name / type
        # / retryable / payload_example, and orchestrator_server ALREADY
        # registers a ("find_symbol", "PATH_NOT_FOUND") remediation chain. With
        # no `error_name` that chain could never fire, and with no `ok` no CA
        # error check saw a failure at all -- the error was silent on BOTH
        # channels. Shape mirrors read/fs.py's PATH_NOT_FOUND so one tool family
        # answers one way. Note `path` here has already absorbed project_path
        # (see the assignment above), so there is no second candidate to report.
        return json.dumps({
            "ok": False,
            "error": f"Path not found: {path}",
            "error_name": "PATH_NOT_FOUND",
            "retryable": False,
            "path": str(base),
            "hint": "Pass an absolute path, or pass project_path pointing at the "
                    "project root.",
            "ca_next_action": "verify_path_or_pass_project_path_with_relative_path",
        }, ensure_ascii=False)
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
                    lines = split_lines(fp.read_text(encoding="utf-8", errors="ignore"))
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
    # B028: `ok` belongs at the PRODUCER, not at the return. Between here and
    # the return this dict is only ever ENRICHED (stale warning, callers/usages,
    # include= reshape) and never turned into an error, so True holds for every
    # exit below -- including the `callers` reshape that pops results/count.
    # Why it was missing: the graph-adapter path above returns what
    # find_symbol_as_search_response built, and that already carries `ok`. Only
    # this live-filesystem fallback did not, so the three modes that reach it
    # (symbol / callers / definition_usage) answered a SUCCESS with no `ok` --
    # and every CA error check reads a missing `ok` as FAILURE. B018 fixed this
    # class in search_code, B024 in cache_status; this is the third site, which
    # is why B029 adds a test gate instead of a third one-line patch.
    response: dict = {"ok": True, "results": results, "count": len(results)}
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
                        lines2 = split_lines(fp2.read_text(encoding="utf-8", errors="ignore"))
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
            # Kept on purpose (#24): the `mode` schema description publishes this
            # shape, so a caller knows the key before it guesses fields=results.
            response.pop("results", None)
            response.pop("count", None)
            response["include"] = "callers"

    return json.dumps(response, ensure_ascii=False)


# D2 completion, 2026-09-21: find_symbol was the LAST unbounded search lane.
#
# The first D2 pass bounded only mode='text'|'smart', because it put the budget in
# _run_search_singleflight -- and mode='symbol'|'callers'|'definition_usage' never
# reach it: search_code returns early with `return await find_symbol(...)`. Three
# modes therefore kept the exact defect D2 was opened for (a scope-driven walk with
# no wall clock), which is why "only the approved point" is the wrong unit of scope:
# the DEFECT is "a search lane with no bound", and it had four instances.
#
# Root, not symptom: the bound goes AROUND the producer, not at the search_code
# call site. Patching :1086 would have left the DIRECT find_symbol tool and its
# find_callers / find_definition_and_usage aliases unbounded -- three more doors
# into the same walk, and the next reader would have had to rediscover that. Here
# every caller, whichever door it came through, shares ONE wall clock and ONE
# constant (SEARCH_TEXT_DEADLINE_SECONDS, the same one the text lane uses).
#
# The remedy this lane reports is `path`, never file_pattern: find_symbol has no
# file_pattern parameter, so _text_timeout_payload is called with
# scope_field='path' and cannot advertise a field the tool would reject.
async def find_symbol(name: str = "", path: str = "", project_path: str = "",
                      context_lines: int = 0, include: str = "def",
                      name_encoding: str = "plain", include_impact: bool = False,
                      **aliases) -> str:
    call = _find_symbol_unbounded(
        name=name, path=path, project_path=project_path,
        context_lines=context_lines, include=include,
        name_encoding=name_encoding, include_impact=include_impact, **aliases)
    if SEARCH_TEXT_DEADLINE_SECONDS <= 0:
        # Env-disabled budget (0 / negative). Kept as an explicit escape hatch so an
        # operator can measure the unbounded cost on purpose; it is NOT the default.
        return await call
    try:
        return await asyncio.wait_for(call, timeout=SEARCH_TEXT_DEADLINE_SECONDS)
    except asyncio.TimeoutError:
        # Report the mode the CALLER asked for, derived from include, so the payload
        # names the mode that appears in its own retry payload rather than "symbol"
        # for all three.
        mode = {"def": "symbol", "callers": "callers",
                "all": "definition_usage"}.get((include or "def").strip().lower(),
                                               "symbol")
        return json.dumps(
            _text_timeout_payload(name, mode, SEARCH_TEXT_DEADLINE_SECONDS,
                                  path, project_path, "", scope_field="path"),
            ensure_ascii=False)


# One docstring, one source: the wrapper must not grow a second description that
# can drift from the implementation's.
find_symbol.__doc__ = _find_symbol_unbounded.__doc__

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


# ISS-20: slack the asyncio wait gets on top of the producer's two phases
# (input scan, then matching). That wait must never be the thing that fires
# first: it abandons the child and throws away the ISS-12 checkpoint, which is
# the only copy of the matches found so far. It is a wedge backstop, not the
# budget -- each phase enforces its own.
_REGEX_MATCH_PHASE_SLACK = 2.0


def _attach_regex_input_phase(resp: dict, partial_source: object | None) -> dict:
    """ISS-20 / B030: name the phase that ran out of budget instead of guessing.

    A regex search spends its budget in two phases -- reading the candidate
    files off disk, then matching them in the isolated child -- and only the
    second one has ever seen the pattern. The payload said
    `narrow_or_simplify_regex` either way, so a repo on a slow disk was told to
    rewrite a regex that had never been compiled against its data, when the
    real answer was "scan fewer files or raise the deadline".

    B030: ISS-20 shipped that disclosure on the INPUT shape only, and
    REGEX_SEARCH_TIMEOUT has THREE. The match-phase RegexSearchTimeout already
    carries `timeout_phase` (the producer stamps it off the shared progress
    dict) and the synthetic asyncio.TimeoutError carries nothing at all, so two
    of the three shapes dropped the single fact that decides between narrowing
    the input and simplifying the pattern. Every shape now states a phase.
    Where nothing was observed it says so in `timeout_phase_unknown_reason`
    instead of picking one: the counters that would back a claim are 0, and
    guessing here would be the same confident-empty-answer lie the input branch
    refuses to tell. The input branch keeps sole ownership of the rewritten
    error/hint/ca_next_action, because it is the only phase whose remedy
    differs -- the other two are correctly told to narrow or simplify.

    `partial_*` keys are deliberately NOT set here; ISS-12 owns those. An
    input-phase death spawned no child, so there is no scanned prefix of a
    result set to hand back, and dressing 0 matches up as a partial would be
    the same confident-empty-answer lie that fix exists to prevent.
    """
    phase = str(getattr(partial_source, "timeout_phase", "") or "")
    if phase != "input":
        resp["timeout_phase"] = phase or "unknown"
        if not phase:
            resp["timeout_phase_unknown_reason"] = (
                "the caller's own wait fired before the worker reported a phase, "
                "and no file-read counter had advanced -- neither input nor match "
                "can be claimed from what was actually observed"
            )
        return resp
    files_read = int(getattr(partial_source, "input_files_read", 0) or 0)
    bytes_read = int(getattr(partial_source, "input_bytes_read", 0) or 0)
    resp["timeout_phase"] = "input"
    resp["input_files_read"] = files_read
    resp["input_bytes_read"] = bytes_read
    resp["error"] = (
        "regex search ran out of budget while READING input after "
        f"{files_read} file(s) / {bytes_read} byte(s) -- matching never started"
    )
    resp["hint"] = (
        "The deadline was spent reading candidate files off disk, so the "
        "pattern was never run against them and rewriting it will not help. "
        "Narrow the FILE scope (path= or file_pattern=) or raise "
        "regex_deadline_seconds."
    )
    resp["ca_next_action"] = "narrow_file_scope_or_raise_regex_deadline"
    return resp


def _text_timeout_payload(query: str, mode: str, budget_seconds: float,
                          path: str, project_path: str, file_pattern: str,
                          scope_field: str = "file_pattern") -> dict:
    """Build the SEARCH_TIMEOUT payload for every non-regex lane (D2).

    Kept separate from ``_regex_timeout_payload`` on purpose: that one talks
    about backtracking, per-phase clocks and ``regex_deadline_seconds``, none of
    which exist on these lanes. A text/smart/symbol search that runs long is a
    SCOPE problem -- the cost is bytes walked -- so the only remedy offered here
    is a narrower scope. No deadline knob is advertised, because raising one buys
    a bigger bill rather than an answer.

    ``scope_field`` is why this is ONE builder and not two. The text/smart lane
    narrows with ``file_pattern``; the symbol/callers/definition_usage lane runs
    through ``find_symbol``, which has NO ``file_pattern`` parameter at all and
    narrows with ``path``. Naming a field the tool would reject is the same
    dishonesty as inventing a value, and a second near-duplicate builder is how
    two payloads drift apart (the A40 lesson: one producer, or they diverge). So
    the caller declares which field it actually accepts, and the payload never
    advertises the other one.

    There is deliberately NO ``corrected_payload``: the narrowing is a decision
    only the caller can make (which file did it actually mean?), and inventing
    one would hand the CA fabricated authority to resend verbatim.
    """
    q_echo = _regex_timeout_query_echo(query)
    loc = project_path or path
    loc_key = "project_path" if project_path else "path"
    scope_txt = file_pattern if (file_pattern and file_pattern != "*") else "*"
    by_pattern = scope_field == "file_pattern"
    example: dict = {"query": q_echo, "mode": mode}
    if loc:
        example[loc_key] = loc
    if by_pattern:
        example["file_pattern"] = "REPLACE_WITH_ONE_FILENAME_OR_NARROWER_GLOB"
        scope_echo = "file_pattern='%s'" % scope_txt
        remedy = ("Narrow the scope: name the exact file in file_pattern, or pass "
                  "path=<subdir>. No timeout parameter helps here -- the wall clock "
                  "tracks the bytes the glob admits, so only a smaller glob is "
                  "cheaper, and a language-wide glob like '*.py' is not a narrowing.")
        note = ("substitute the real filename or subdirectory for the placeholder -- "
                "there is no deadline knob on this lane and widening the glob is the "
                "expensive direction")
    else:
        example["path"] = "REPLACE_WITH_THE_SUBDIR_THAT_HOLDS_THE_SYMBOL"
        scope_echo = "path='%s'" % (path or project_path or "<repo root>")
        remedy = ("Narrow the scope: pass path=<subdir> so the symbol walk starts "
                  "below the repo root. This lane has NO file_pattern parameter and "
                  "no timeout parameter -- path is the only narrowing it accepts.")
        note = ("substitute the subdirectory that actually holds the symbol -- this "
                "lane accepts path, NOT file_pattern, and there is no deadline knob "
                "on it")
    out = {
        "ok": False,
        "error": ("mode='%s' search exceeded its %.1fs wall-clock budget at %s. "
                  "This is a SCOPE cost, not a hung tool: the same query measured "
                  "0.36s against one exact filename and 25.33s against '*.py' on an "
                  "838-file repo (~70x, warm)."
                  % (mode, float(budget_seconds), scope_echo)),
        "error_name": "SEARCH_TIMEOUT",
        "wall_clock_budget_seconds": round(float(budget_seconds), 3),
        "retryable": True,
        "bounded": True,
        "mode": mode,
        "query": q_echo,
        "accepted_narrowing_fields": (["file_pattern", "path"] if by_pattern
                                      else ["path"]),
        "ca_next_action": ("narrow_file_pattern_or_path_then_retry" if by_pattern
                           else "narrow_path_then_retry"),
        "payload_example": example,
        "retry_sequence": [
            {"tool": "search_code", "payload": example, "note": note},
        ],
        "hint": remedy,
    }
    if by_pattern:
        # Only echoed on the lane that HAS the field. On the symbol lane a
        # file_pattern key would read as "this is what you passed / what you may
        # pass", and both are false there.
        out["file_pattern"] = scope_txt
    return out


def _regex_timeout_payload(query: str, effective_regex_deadline: float | None,
                           path: str, project_path: str, file_pattern: str,
                           partial_source: object | None = None,
                           attempts: int = 1,
                           initial_deadline: float | None = None) -> dict:
    """Build the one shared REGEX_SEARCH_TIMEOUT payload.

    B031 (#4): ``effective_regex_deadline`` is the deadline of the LAST attempt, not
    the budget the call actually spent, for two independent reasons. R39 doubles it
    in place on the first timeout when the caller passed no explicit
    ``regex_deadline_seconds`` (8.0 -> min(16.0, MAX 15.0) = 15.0), and ISS-20 gives
    the input phase and the match phase of EACH attempt a separate clock. So the old
    message -- "regex search exceeded hard deadline of 15.000s", one number and no
    mention of either multiplier -- understated the measured wall clock
    (26.60 / 26.64 / 26.74 / 26.81 / 27.73s) by more than 1.7x, and a CA that sized
    its own client timeout from that number would have called a live search hung.
    ``attempts`` / ``initial_deadline`` must come from the caller because the retry
    destroys the pre-doubling value; with them ``error`` states the budget really
    spent, and ``wall_clock_budget_seconds`` carries it as a number to compute with.
    """
    from orchestrator import search_regex_worker as _rw
    q_echo = _regex_timeout_query_echo(query)
    if effective_regex_deadline is not None:
        deadline_txt = f"{effective_regex_deadline:.3f}"
        wider = round(min(effective_regex_deadline * 2.0, _rw.MAX_DEADLINE_SECONDS), 3)
    else:
        deadline_txt = "default"
        wider = _rw.MAX_DEADLINE_SECONDS
    attempts_n = attempts if isinstance(attempts, int) and attempts >= 1 else 1
    first_deadline = initial_deadline if initial_deadline is not None else effective_regex_deadline
    if first_deadline is None or effective_regex_deadline is None:
        wall_budget = None
    else:
        # attempt 1 ran under first_deadline, every retry under the doubled one, and
        # ISS-20 bounds input and match SEPARATELY inside each -- hence the x2.
        wall_budget = round((float(first_deadline)
                             + float(effective_regex_deadline) * (attempts_n - 1)) * 2.0, 3)
    first_txt = f"{float(first_deadline):.3f}" if first_deadline is not None else "default"
    if attempts_n > 1:
        error_txt = (f"regex search exhausted {attempts_n} attempts: the first under a "
                     f"{first_txt}s deadline, then the R39 adaptive retry under "
                     f"{deadline_txt}s. Input phase and match phase are budgeted "
                     f"separately inside each attempt, so a client timeout must allow "
                     f"~{wall_budget}s worst case -- not {deadline_txt}s (measured "
                     f"typical for the 8.0->15.0 default pair: 26.6-27.7s)")
    elif wall_budget is not None:
        error_txt = (f"regex search exceeded its {deadline_txt}s deadline on attempt 1 of 1. "
                     f"Input phase and match phase are budgeted separately, so a client "
                     f"timeout must allow ~{wall_budget}s worst case -- not {deadline_txt}s")
    else:
        error_txt = f"regex search exceeded hard deadline of {deadline_txt}s"
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
    resp = _attach_regex_partials({
        "ok": False,
        "error": error_txt,
        "wall_clock_budget_seconds": wall_budget,
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
    return _attach_regex_input_phase(resp, partial_source)


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
    # D7 2026-09-21: the fallback here used to be "*.py", so a caller that had
    # passed no file_pattern got {"file_pattern": "*.py"} back as corrected_payload
    # -- i.e. "resend this as-is". On a same-language repo that glob is very nearly
    # "*" (measured 25.33s vs 0.36s for the exact filename, ~70x), so the remedy
    # cost about as much as the refusal it was answering. This is the 4th instance
    # of ONE producer bug, alongside the SEARCH_TOO_BROAD payload and both
    # _narrow_warn strings: [A] naming a language-wide glob as the narrowing. The
    # bug is the class, not any single dict -- a grep for one guard's error_name
    # found two of the four.
    by_glob: dict = {"query": q_echo, "mode": "regex"}
    if loc:
        by_glob[loc_key] = loc
    _had_glob = bool(file_pattern) and file_pattern != "*"
    by_glob["file_pattern"] = (
        file_pattern if _had_glob
        else "<one filename, or a glob narrower than a whole language>")

    out = {
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
        "payload_example": by_path,
        "accepted_narrowing_fields": ["path", "file_pattern"],
        "retry_sequence": [
            {"tool": "search_code", "payload": by_glob,
             "note": ("narrow the input by glob -- fewer files read, same tree in scope"
                      if _had_glob else
                      "narrow the input by glob -- replace the placeholder with the "
                      "file you actually mean; a whole-language glob is not a narrowing")},
            {"tool": "search_code", "payload": by_path,
             "note": "or scope to a subdirectory; `path` and `file_pattern` compose"},
        ],
        "hint": "The regex never ran: the candidate bytes exceeded the ceiling "
                "before matching started. Shrink the input with `file_pattern` "
                "and/or `path` -- both are accepted by search_code and by "
                "regex_search. Raising the ceiling is not a caller-side option.",
        "ca_next_action": "narrow_path_or_reduce_regex_input",
    }
    # corrected_payload means "sendable as-is". That is honest only when the caller
    # already had a real glob worth keeping; with a placeholder inside it would be
    # the same fabricated authority D7 removed elsewhere. retry_sequence[0] carries
    # the shape either way, and when both are present they stay the identical dict
    # -- the DUPLICATE_RETRY_BLOCKED invariant (corrected_payload must equal the
    # first retry step, never disagree with it).
    if _had_glob:
        out["corrected_payload"] = by_glob
    return out


# REGEX_GUARD state (2026-10-02, Anh-approved REGEX_ROOT_CAUSE items 2+3+4):
# per-(project_path, path, query) memory of regex deadline errors. In-memory BY
# DESIGN: a daemon restart clears it, which is the correct reset.
_REGEX_DEADLINE_ERROR_STATE: dict = {}
_REGEX_DEADLINE_STATE_LOCK = threading.Lock()


def _regex_scope_is_narrowed(path: str, file_pattern: str) -> bool:
    """REGEX_GUARD D3: one predicate for 'is this scan narrow enough to skip the
    deadline guards'. Narrow == the caller gave a subdirectory `path` OR a
    `file_pattern` naming ONE file. The narrow-hint rule (~_narrow_warn) defines
    the real narrowings as exactly those two and says a language-wide glob such
    as '*.py' is NOT a narrowing -- so a glob metachar (* ? [) disqualifies the
    pattern, while one literal filename counts as narrowed."""
    if path:
        return True
    fp = (file_pattern or "").strip()
    if not fp or fp == "*":
        return False
    return not any(ch in fp for ch in "*?[")


def _regex_deadline_state_key(project_path: str, path: str, file_pattern: str, query: str) -> tuple:
    # D1: the key must see file_pattern -- a retry narrowed ONLY by file_pattern
    # is a CHANGED call, not the same-params retry the guard exists to block.
    return ((project_path or "").casefold(), (path or "").casefold(),
            (file_pattern or "*").casefold(), (query or ""))


def _note_regex_deadline_error(query: str, project_path: str, path: str,
                               file_pattern: str, deadline, raw_deadline, resp: dict) -> None:
    key = _regex_deadline_state_key(project_path, path, file_pattern, query)
    with _REGEX_DEADLINE_STATE_LOCK:
        st = _REGEX_DEADLINE_ERROR_STATE.setdefault(key, {"count": 0})
        st["count"] = int(st.get("count", 0)) + 1
        st["deadline"] = float(deadline) if deadline is not None else None
        st["raw"] = repr(raw_deadline)
        count = st["count"]
    if count >= 2:
        resp["strategy_hint"] = (
            f"{count} deadline errors in a row on this scope+query: raising the "
            "deadline is not landing either. Switch strategy -- narrow 'path' to a "
            "subdirectory or one file, use search_code mode='text'/'smart', or query "
            "the index (find_symbol/query_index) instead of scanning.")
        resp["ca_next_action"] = "switch_search_strategy_or_tool"


def _clear_regex_deadline_state(project_path: str, path: str, file_pattern: str, query: str) -> None:
    # A SUCCESS proves the scope is searchable again, so it must clear the timeout
    # memory for that (project, path, query) across EVERY file_pattern: the note may
    # have been written at file_pattern='*' (the un-narrowed call that timed out)
    # while the success comes back narrowed -- the two keys differ only in the
    # pattern, and clearing only the exact one would leave the memory stuck.
    pp = (project_path or "").casefold()
    pa = (path or "").casefold()
    qq = query or ""
    with _REGEX_DEADLINE_STATE_LOCK:
        for key in [k for k in _REGEX_DEADLINE_ERROR_STATE
                    if k[0] == pp and k[1] == pa and k[3] == qq]:
            _REGEX_DEADLINE_ERROR_STATE.pop(key, None)

async def _search_once_after_reconcile(env: dict) -> str:
    """P15: re-run search_code ONCE after a stale-drift reconcile, then merge.

    Extracted so the reconcile + re-search exists exactly once. The recursive
    call passes ``_skip_stale_reconcile=True`` (popped from ``**aliases`` like
    ``_snapshot_retry``, so it never enters the public schema): the re-search
    skips BOTH the reconcile gate and, on a second 0-hit, the O(repo) drift
    probe -- the probe JUST measured and the reconcile either fixed the drift
    or reported why not, so re-probing could only loop or re-pay the cost. A
    second 0-hit is therefore answered from the reconcile outcome: found
    (count>=1), a clean not-found (delete or query truly absent), or the A03
    honest inconclusive (reconcile reported a residue it could not fix).
    ``env`` is the caller's ``locals()`` -- the fresh singleflight key takes
    the new snapshot id, so the fresh result can never be confused with the
    stale run's."""
    response = json.loads(await search_code(
        query=env["query"],
        path=env["path"],
        project_path=env["project_path"],
        file_pattern=env["file_pattern"],
        literal_search=env["literal_search"],
        mode=env["mode"],
        query_encoding="plain",
        max_context_lines=env["max_context_lines"],
        max_results=env["max_results"],
        flags=env["flags"],
        force=env["force"],
        include_impact=env["include_impact"],
        regex_deadline_seconds=env["regex_deadline_seconds"],
        _snapshot_retry=env["snapshot_retry"],
        _skip_stale_reconcile=True,
        **env["aliases"],
    ))
    rec_report = env["rec_report"]
    response["stale_reconcile"] = rec_report
    residue = int(rec_report.get("residue_count") or 0)
    if response.get("count") and not residue:
        # Found on the fresh snapshot and the reconcile left nothing behind:
        # the earlier 0-hit was the staleness.
        response.pop("inconclusive", None)
        response.pop("ca_next_action", None)
        response.pop("tip", None)
        response["stale_index"] = False
        return json.dumps(response, ensure_ascii=False)
    if residue:
        # P15 r2: the reconcile made progress but could not apply EVERY drifted
        # file in this call's budget, so this answer -- empty or not -- still
        # cannot be certified clean (the residue may hold the hit). Keep A03
        # honest. The next action is a plain RETRY: the probe re-measures, the
        # reconcile resumes from the residue (never redoes processed files) and
        # converges. "refresh_or_narrow" would be wrong advice here --
        # refresh_project_map is forbidden on a large root, and the retry IS
        # the fix.
        response["stale_index"] = True
        response["inconclusive"] = True
        response["ca_next_action"] = "retry_search_continues_reconcile"
    else:
        # Clean re-search: a real absence (or a native delete the reconcile
        # applied). Not inconclusive.
        response["stale_index"] = False
        response.pop("inconclusive", None)
        response.pop("ca_next_action", None)
    return json.dumps(response, ensure_ascii=False)


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
    # P15: internal one-shot re-search flag (never in the public schema;
    # popped from **aliases exactly like _snapshot_retry above).
    _skip_stale_reconcile = bool(aliases.pop("_skip_stale_reconcile", False))

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
    # B031 (#4): R39 below DOUBLES effective_regex_deadline IN PLACE, so by the time a
    # timeout payload is built the value the call STARTED from no longer exists and the
    # attempt count is nowhere recorded. The payload then printed the final number alone
    # and called it "the hard deadline" -- measured 15.000s printed against a 26.6-27.7s
    # wall clock. These two survive the doubling so the payload can state the real budget.
    _regex_initial_deadline = None
    _regex_attempts_used = 1
    _narrow_warn = ""
    _regex_deadline_floor_applied = False  # REGEX_GUARD A4: set when the 2.0s floor applies
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
        # REGEX_GUARD (2026-10-02, Anh-approved REGEX_ROOT_CAUSE items 2+3+4):
        # measured 01/10 on 172 regex deadline errors: 159 were whole-project
        # scans (no 'path' narrowing), 127 of them at 0.05-0.08s, and 105 blind
        # same-params retries never changed the outcome (bench acc 0.508).
        # A2/A4: an unscoped scan below 0.5s is refused outright; 0.5-2.0s is
        # raised to a 2.0s floor. A3/retry policy: an unchanged retry of a
        # scope+query+deadline that just timed out is refused until a
        # changed-params call succeeds and clears the memory.
        _regex_state_key = _regex_deadline_state_key(project_path, path, file_pattern, query)
        with _REGEX_DEADLINE_STATE_LOCK:
            _regex_prev_fail = dict(_REGEX_DEADLINE_ERROR_STATE.get(_regex_state_key) or {})
        if (_regex_prev_fail.get("count", 0) >= 1
                and _regex_prev_fail.get("raw") == repr(regex_deadline_seconds)):
            # D2: keep the caller's scope fields, and propose a deadline the OTHER
            # guard accepts -- an unscoped suggestion below the 2.0s floor would be
            # refused by REGEX_DEADLINE_SCOPE_REJECTED, making corrected_payload a
            # contract that contradicts itself.
            _same_floor = 2.0 if not _regex_scope_is_narrowed(path, file_pattern) else 0.0
            _same_fix = {"query": query, "mode": "regex",
                         "regex_deadline_seconds": round(max(_same_floor, min(
                             effective_regex_deadline * 2.0,
                             regex_worker.MAX_DEADLINE_SECONDS)), 3)}
            if project_path:
                _same_fix["project_path"] = project_path
            if path:
                _same_fix["path"] = path
            if file_pattern and file_pattern != "*":
                _same_fix["file_pattern"] = file_pattern
            return json.dumps({
                "ok": False,
                "error": (f"regex scan on this exact scope+query already timed out "
                          f"{_regex_prev_fail['count']}x at the same "
                          f"{effective_regex_deadline:.3f}s deadline; retrying unchanged "
                          f"has no signal (measured acc 0.508 = chance). Change "
                          f"regex_deadline_seconds, narrow 'path', or switch tool."),
                "error_name": "REGEX_RETRY_SAME_PARAMS_BLOCKED",
                "retryable": True,
                "bounded": True,
                "corrected_payload": _same_fix,
                "ca_next_action": "change_regex_deadline_or_narrow_path_or_use_search_code_text",
            }, ensure_ascii=False)
        # D3: "unscoped" means the caller gave NEITHER a subdirectory `path` NOR a
        # one-file `file_pattern`. The narrow-hint rule above (~_narrow_warn) names
        # those exact two as the real narrowings and says a language-wide glob like
        # '*.py' is NOT one, so a one-file pattern must count as narrowed here.
        if not _regex_scope_is_narrowed(path, file_pattern) and effective_regex_deadline < 0.5:
            _scope_fix = {"query": query, "mode": "regex", "regex_deadline_seconds": 2.0}
            if project_path:
                _scope_fix["project_path"] = project_path
            if path:
                _scope_fix["path"] = path
            if file_pattern and file_pattern != "*":
                _scope_fix["file_pattern"] = file_pattern
            return json.dumps({
                "ok": False,
                "error": (f"regex deadline {effective_regex_deadline:.3f}s is hopeless for a "
                          f"whole-project scan (no 'path' narrowing): measured 159/172 "
                          f"deadline errors were exactly this shape. Raise "
                          f"regex_deadline_seconds to >=2.0 or pass a narrower 'path'."),
                "error_name": "REGEX_DEADLINE_SCOPE_REJECTED",
                "retryable": True,
                "bounded": True,
                "corrected_payload": _scope_fix,
                "ca_next_action": "raise_regex_deadline_or_narrow_path_or_use_search_code_text",
            }, ensure_ascii=False)
        if not _regex_scope_is_narrowed(path, file_pattern) and effective_regex_deadline < 2.0:
            effective_regex_deadline = 2.0
            _regex_deadline_floor_applied = True
        _regex_initial_deadline = effective_regex_deadline
        regex_deadline_at = search_started_at + effective_regex_deadline
        # Task 3 auto-chain fix: proactive narrow hint for regex on root.
        # D7 2026-09-21: this used to say "Add file_pattern='*.py'". On a repo that is
        # mostly one language that glob is very nearly '*' -- measured 25.33s vs 0.36s
        # for the exact filename on 838 files (~70x) -- so the hint pointed AT the
        # expensive scope while claiming to narrow. Same producer bug as the
        # SEARCH_TOO_BROAD payload below: never name a language-wide glob as the
        # remedy. The two narrowings that actually cut bytes are ONE filename or a
        # subdirectory, so those are what the text asks for now.
        if (not file_pattern or file_pattern == "*") and not path:
            _narrow_warn = (
                "Regex on the full repo may time out. Narrow it with "
                "file_pattern='<the one filename you mean>' or path='<subdir>/'. "
                "A language-wide glob such as '*.py' is NOT a narrowing here."
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
        # D7 2026-09-21: this payload USED to answer
        #   corrected_payload={"query": _q_sel, "file_pattern": "*.py", "force": True}
        # with a hint whose last sentence was "Pass force=true to run anyway."
        # Both halves were defects, and together they made the guard self-defeating:
        #   1. On a same-language repo "*.py" is not a narrowing -- it is very nearly
        #      "*". Measured on ToolQ (838 files, one query, mode='text', warm): the
        #      exact filename answered in 0.36s, "*.py" in 25.33s, ~70x. So [A] handed
        #      back a scope as expensive as the one it had just refused.
        #   2. force=True DISABLES this very guard. The standing CA contract is "use
        #      corrected_payload and retry once", so a COMPLIANT CA retried with
        #      force=True and the refusal protected nothing.
        # This is the producer of the sleep.py case: the CA did not invent
        # file_pattern="*.py", it copied what this dict told it to send. Per truy gốc,
        # không vá ngọn the fix is to stop teaching the wrong payload -- not to add a
        # field that explains afterwards why the retry was slow.
        #
        # There is deliberately NO corrected_payload now: the remedy is a more
        # selective QUERY or a scope only the caller knows, so a ready-to-send dict
        # would be fabricated authority. payload_example carries placeholders that
        # CANNOT be resent verbatim, which is the honest shape here -- same reasoning
        # as _text_timeout_payload.
        _broad_loc = project_path or path
        _broad_example: dict = {
            "query": "REPLACE_WITH_A_DISTINCTIVE_STRING_OF_3_CHARS_OR_MORE",
            "mode": mode,
        }
        if _broad_loc:
            _broad_example["project_path" if project_path else "path"] = _broad_loc
        _broad_example["file_pattern"] = "REPLACE_WITH_ONE_FILENAME_OR_A_SUBDIR_GLOB"
        return json.dumps({
            "ok": False,
            "error": f"SEARCH_TOO_BROAD: query {_q_sel!r} would scan every file with near-zero selectivity",
            "error_name": "SEARCH_TOO_BROAD",
            "retryable": True,
            "hint": "The QUERY is the problem, not the budget: under 3 chars against "
                    "file_pattern='*' has near-zero selectivity. Make the query "
                    "distinctive, or name the ONE file you actually mean, or use "
                    "query_index / project_context_pack when the goal is an "
                    "inventory rather than a match. '*.py' is NOT a narrowing on a "
                    "same-language repo -- measured ~70x the cost of naming the "
                    "file. force=true only disables this check and pays the "
                    "full-tree scan; it is not the corrected payload.",
            "ca_next_action": "narrow_query_or_use_query_index",
            "accepted_narrowing_fields": ["query", "file_pattern", "path"],
            "payload_example": _broad_example,
            "retry_sequence": [
                {"tool": "search_code", "payload": _broad_example,
                 "note": "BOTH placeholders must be replaced -- this payload is not "
                         "sendable as-is, and widening the glob is the expensive "
                         "direction"},
            ],
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
            # B028: one condition, one name -- and `ok` on the error path too.
            # REGEX_INVALID is the REGISTERED spelling: the error taxonomy in
            # orchestrator_server, the worker's RegexSearchInvalid.error_name,
            # the sibling branch below (same user-visible failure, reached via
            # the regex worker instead of this local compile), and
            # test_search_regex_budget all use it. INVALID_REGEX existed at THIS
            # ONE site and nowhere else in the repo, so a CA matching the
            # registered name never saw this branch at all -- the pattern-compile
            # failure for mode='text'/'smart' was silently unclassifiable.
            return json.dumps({
                "ok": False,
                "error": f"Invalid regex: {exc}",
                "error_name": "REGEX_INVALID",
                "retryable": True,
                "bounded": True,
                "corrected_payload": {"query": query, "mode": "text"},
                "hint": "Pattern fails to compile. If you meant a literal/symbol, "
                        "resend with mode='text'; otherwise escape the metacharacters.",
                "ca_next_action": "resend_with_mode_text_or_escaped_regex",
            }, ensure_ascii=False)
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

    # ISS-20: which phase is spending the budget, and how far it got. "input"
    # = pin + read candidate files off disk + line budget; "match" = the
    # isolated matcher. Only the second has ever seen the pattern, and the
    # timeout payload used to report the second no matter which one actually
    # died. It lives HERE, in the caller's frame, and not in the producer's,
    # because the AWAITING side has to read the phase too -- that is how it
    # decides whether the match clock has been earned. A plain dict rather
    # than nonlocal ints because _regex_remaining is called before the read
    # counters below exist.
    _regex_progress: dict = {"phase": "input", "files_read": 0, "bytes_read": 0}

    def _regex_match_extension() -> float:
        """ISS-20 / #12(b): the match phase's clock, only once it is earned.

        Returns 0.0 while the producer is still pinning or reading input, so
        the singleflight wait expires at the caller's deadline exactly as it
        did before: no child has spawned, so abandoning it loses nothing. Once
        the child IS matching, letting that wait fire first would abandon it
        and take the ISS-12 checkpoint -- the only copy of the matches found
        so far -- down with it, which is the precise loss this fix removes.
        Read at call time, so the R39 retry's doubled deadline applies without
        re-wiring anything.
        """
        if mode != "regex" or _regex_progress.get("phase") != "match":
            return 0.0
        return effective_regex_deadline + _REGEX_MATCH_PHASE_SLACK

    def _regex_input_phase_source(exc: object | None = None) -> object | None:
        """ISS-20: let the AWAITING side disclose an input-phase death too.

        The producer tags the phase onto the RegexSearchTimeout it raises, but at
        a tight deadline the caller's own wait is what usually fires first: a
        read already issued to a slow disk is not interruptible, so
        `_regex_remaining` never reaches its next checkpoint and what the caller
        is left holding is a synthetic asyncio.TimeoutError with nothing on it.
        Measured live on this repo at regex_deadline_seconds=0.3: the payload
        still said `narrow_or_simplify_regex` about a pattern that had never
        been compiled -- the exact defect ISS-20 exists to remove, surviving in
        the one path that actually runs. The progress dict is shared with the
        producer, so the same disclosure is available from here.

        Only claimed when the counters can back it: at least one file really
        read, and no matches in hand. A producer wedged before its first read,
        and every test that raises the worker exception directly (no producer
        runs, so the counters stay 0), keep their exact previous shape rather
        than being handed a phase nobody observed.
        """
        if mode != "regex" or regex_worker is None:
            return exc
        if _regex_progress.get("phase") != "input":
            return exc
        if not (_regex_progress.get("files_read") or _regex_progress.get("bytes_read")):
            return exc
        if getattr(exc, "partial_results", None):
            return exc
        src = exc
        if src is None:
            src = regex_worker.RegexSearchTimeout(
                f"regex search exceeded hard deadline of {effective_regex_deadline:.3f}s",
            )
        if not getattr(src, "timeout_phase", None):
            src.timeout_phase = "input"
            src.input_files_read = int(_regex_progress.get("files_read") or 0)
            src.input_bytes_read = int(_regex_progress.get("bytes_read") or 0)
        return src
    def _search_snapshot(
        expected_snapshot_id: str | None,
        producer_deadline_at: float | None,
    ) -> dict:
        def _regex_remaining() -> float:
            remaining = producer_deadline_at - time.monotonic()
            if remaining < regex_worker.MIN_DEADLINE_SECONDS:
                exc = regex_worker.RegexSearchTimeout(
                    f"regex search exceeded hard deadline of {effective_regex_deadline:.3f}s",
                )
                exc.timeout_phase = _regex_progress["phase"]
                exc.input_files_read = int(_regex_progress["files_read"])
                exc.input_bytes_read = int(_regex_progress["bytes_read"])
                raise exc
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
                    # ISS-20: this is now the ONLY line-budget check in the live
                    # path (search_records is called with
                    # records_pre_validated=True), and it still runs before any
                    # payload can reach a child process.
                    regex_worker.validate_line_budget(payload)
                    regex_record_count += 1
                    regex_total_bytes = next_total
                    # ISS-20: publish coverage so a death in THIS phase can say
                    # how far it actually got instead of blaming the pattern.
                    _regex_progress["files_read"] = regex_record_count
                    _regex_progress["bytes_read"] = regex_total_bytes
                    _regex_remaining()
                records.append((relative_path, label, payload))

            results: list[dict] = []
            skipped_large: list[dict] = []
            skipped_large_total = 0   # dem THAT; skipped_large chi giu 20 vi du dau

            # P14 D1: a re.escape'd text/smart pattern can match across a line
            # break only when the raw query itself carries one; then the scan
            # below must keep the old per-line semantics (no cross-line hits).
            _pattern_may_span_lines = (chr(10) in query) or (chr(13) in query)

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
                text = payload.decode("utf-8", errors="ignore")
                # P14 D1: one C-level finditer per record instead of a Python-
                # level pat.search per line (944412 calls, 0.35 s in-process, far
                # worse under daemon GIL contention), and split_lines only for
                # files WITH a hit. text/smart patterns are re.escape literals:
                # a match spans a line break only when the query itself contains
                # one, and that case falls back to the per-line loop so
                # cross-line semantics stay identical to the pre-P14 code.
                if not _pattern_may_span_lines:
                    hit_offsets = None
                    last_line = 0
                    for m in pat.finditer(text):
                        if hit_offsets is None:
                            lines_kept = split_lines(text, keepends=True)
                            hit_offsets = []
                            _pos = 0
                            for _ln in lines_kept:
                                hit_offsets.append(_pos)
                                _pos += len(_ln)
                            lines = [ln.rstrip("\r\n") for ln in lines_kept]
                        i = bisect_right(hit_offsets, m.start())
                        if i == last_line:
                            continue
                        last_line = i
                        line = lines[i - 1]
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
                    return
                lines = split_lines(text)
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

            # D3 2026-09-22: count what the glob ADMITS, not only what matched. The
            # filter already visits every record, so both counters are free, and
            # without them a 0-hit answer cannot tell the caller whether the query
            # was absent from real files or whether the glob admitted no file at all.
            pattern_admitted = 0
            records_seen = 0
            for record in pin.iter_records(base_resolved):
                records_seen += 1
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
                pattern_admitted += 1
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
                # ISS-20 / #12(b): the MATCH phase gets its own full deadline.
                # It used to inherit whatever was left after reading every
                # candidate file off disk, so on this repo (151 files, HDD) the
                # remainder fell below MIN_DEADLINE_SECONDS and the child was
                # never spawned: no checkpoint, no partial rows, and a payload
                # that blamed a pattern which had never been compiled against
                # the data. regex_deadline_seconds now bounds each phase -- the
                # input scan above, the matching here -- so the number a caller
                # passes is the number the matcher actually gets.
                _regex_progress["phase"] = "match"
                results = regex_worker.search_records(
                    pattern,
                    re_flags,
                    records,
                    max_results=effective_max_results,
                    context_lines=effective_context,
                    deadline_seconds=effective_regex_deadline,
                    records_pre_validated=True,
                )
            payload_out = {"results": results, "snapshot_id": pin.snapshot_id}
            if not results:
                # P13: a 0-hit must never certify a clean index while the SNAPSHOT
                # this search served predates INDEX. The A03 probe compares INDEX
                # against DISK only, so a post-write publish failure (the
                # fresh-project first-write path leaves CURRENT at the pre-write
                # snapshot) read as clean while the new file was missing from the
                # served tree. Compare the served manifest's record set against
                # the LIVE INDEX.json HERE, where the pin is still held, gated on
                # empty results so the hot path pays nothing. Non-authoritative
                # manifests (search-side publish, index=None) skip the probe:
                # their record set legitimately differs from INDEX by filter.
                try:
                    manifest = pin.manifest
                    if manifest.get("index_authoritative"):
                        live_index_path = (Path(snapshot_root)
                                           / search_snapshots.CACHE_DIR_NAME
                                           / search_snapshots.INDEX_FILENAME)
                        live_index = json.loads(live_index_path.read_text(encoding="utf-8"))
                        manifest_paths = {
                            str(record.get("path") or "").replace("\\", "/").casefold()
                            for record in manifest.get("files", [])
                        }
                        missing = sorted(
                            str(rel).replace("\\", "/")
                            for rel in (live_index.get("files") or {})
                            if str(rel).replace("\\", "/").casefold() not in manifest_paths
                        )
                        if missing:
                            payload_out["snapshot_missing_indexed_files"] = len(missing)
                            payload_out["snapshot_missing_indexed_sample"] = missing[:5]
                except Exception:
                    # A probe failure must never fail a search that already ran.
                    pass
            # D3: the two counters the 0-hit branch needs to tell "your glob matched
            # nothing" apart from "the text really is absent". They are only
            # MEANINGFUL when results is empty: the scan loop breaks at
            # effective_max_results, so on a hit-bearing answer these are partial by
            # design and the consumer must not read them there.
            payload_out["pattern_admitted_files"] = pattern_admitted
            payload_out["records_scanned"] = records_seen
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
            # P15: the token alone cannot key the singleflight cache -- the
            # reconcile republishes the snapshot asynchronously (off the event
            # loop), so a SAME-scope call that starts while the reconcile is
            # mid-publish reads the PRE-publish CURRENT and would otherwise join
            # the pre-reconcile future, serving the stale hit it was meant to
            # correct (measured: native-delete test re-served a deleted file's
            # hit). The reconcile stamps a per-root epoch at apply time; the
            # key reads it here (event-loop thread, no lock needed), so the
            # post-reconcile call can never coalesce with a pre-reconcile one.
            _recon_epoch = _SEARCH_RECONCILE_EPOCH.get(str(snapshot_root).casefold(), 0)
            snapshot_token = snapshot_token + ("recon", _recon_epoch)
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
            if mode != "regex" and SEARCH_TEXT_DEADLINE_SECONDS > 0:
                # D2: only mode='regex' ever self-bounded; text/smart passed
                # timeout=None and ran as long as the bytes took. The budget is
                # deliberately NOT a caller parameter -- a knob gets raised on
                # retry, and widening is the expensive direction (the cost is
                # bytes scanned, so a bigger deadline buys a bigger bill, never an
                # answer). It sits far above every measured healthy call (25.33s
                # worst on 838 files) so it can only fire on a pathological scope,
                # and it stays inside the lane's own documented queue-wait ceiling
                # (_QUEUE_WAIT_MAX_SECONDS = 60.0) so the FAIR_ADMISSION deadline
                # derived from it at _run_search_singleflight:152 is not a new
                # regime. Ops can still move it with SEARCH_TEXT_DEADLINE_SECONDS.
                wait_timeout = max(
                    0.05,
                    search_started_at + SEARCH_TEXT_DEADLINE_SECONDS - time.monotonic())
            if mode == "regex":
                # ISS-20: the producer runs TWO separately-clocked phases --
                # input (pin + read candidate files + line budget) and matching
                # (a fresh effective_regex_deadline started inside
                # search_records). This wait is handed ONLY the input bound: a
                # producer wedged BEFORE the matcher exists (slow snapshot pin,
                # held state lock) has no checkpoint to salvage, so it must
                # still be abandoned at exactly the caller's number --
                # tests/test_search_regex_budget.py pins that, and it is the
                # only wall-clock guarantee left while nothing has spawned. The
                # match phase's own clock is granted on demand instead, through
                # extend_deadline, so a phase that never reached the matcher
                # can never spend it.
                input_wait = regex_deadline_at - time.monotonic()
                if input_wait <= 0:
                    raise regex_worker.RegexSearchTimeout(
                        f"regex search exceeded hard deadline of {effective_regex_deadline:.3f}s",
                    )
                wait_timeout = input_wait
                producer_deadline_at = regex_deadline_at
                _regex_progress.update(
                    {"phase": "input", "files_read": 0, "bytes_read": 0},
                )
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
                        extend_deadline=_regex_match_extension,
                    )
                    break  # success — exit retry loop
                except asyncio.TimeoutError:
                    if (_regex_retried or mode != "regex"
                            or regex_deadline_seconds is not None):
                        raise
                    _regex_retried = True
                    _regex_attempts_used = 2
                    effective_regex_deadline = min(
                        effective_regex_deadline * 2.0,
                        regex_worker.MAX_DEADLINE_SECONDS,
                    )
                    regex_deadline_at = search_started_at + effective_regex_deadline
                    wait_timeout = regex_deadline_at - time.monotonic()
                    producer_deadline_at = regex_deadline_at
                    _regex_progress.update(
                        {"phase": "input", "files_read": 0, "bytes_read": 0},
                    )
                    search_key = (
                        snapshot_token,
                        str(base_resolved).casefold(), pattern, re_flags, file_pattern,
                        mode, effective_regex_deadline,
                        effective_context, effective_max_results,
                        text_record_cap,
                    )
                    # D7 2026-09-21: third copy of the same wrong remedy (see the
                    # first _narrow_warn above). Found only by a comment-aware source
                    # sweep -- a plain grep for the guard's error_name had missed it,
                    # which is why the fix is stated as a CLASS: never name a
                    # language-wide glob as the narrowing.
                    _narrow_warn = (
                        "Regex on full repo timed out at initial deadline; "
                        "retrying with extended deadline. Narrow it with "
                        "file_pattern='<the one filename you mean>' or "
                        "path='<subdir>/' -- a language-wide glob such as '*.py' "
                        "is NOT a narrowing on a same-language repo."
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
                    # B031: the payload builder needs the attempt count; effective_regex_deadline
                    # is about to be overwritten and the pre-doubling value would be lost.
                    _regex_attempts_used = 2
                    effective_regex_deadline = min(
                        effective_regex_deadline * 2.0,
                        regex_worker.MAX_DEADLINE_SECONDS,
                    )
                    regex_deadline_at = search_started_at + effective_regex_deadline
                    wait_timeout = regex_deadline_at - time.monotonic()
                    producer_deadline_at = regex_deadline_at
                    _regex_progress.update(
                        {"phase": "input", "files_read": 0, "bytes_read": 0},
                    )
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
            # P15: a SnapshotUnavailable retry is a NEW search, not the
            # reconcile re-run -- let it measure drift and reconcile again.
            _skip_stale_reconcile=False,
            **aliases,
        )
    except asyncio.TimeoutError:
        if mode != "regex":
            # D2: the non-regex lanes reached _run_search_singleflight with
            # timeout=None -- genuinely UNBOUNDED. Measured 2026-09-21 on ToolQ
            # (838 files, same query, mode='text', warm): file_pattern with the
            # exact filename = 0.36s, file_pattern='*.py' = 25.33s, ~70x. The
            # wide form ran until the CLIENT gave up, leaving `"POST
            # /api/call/search_code" 200 0` in the access log against an ok=True
            # USAGE_LOG row -- both sides honest, neither reconcilable. Bounding
            # it here makes the blowup the TOOL's own answer, and names the scope
            # as the cause instead of leaving the CA to guess it hung.
            return json.dumps(
                _text_timeout_payload(query, mode, SEARCH_TEXT_DEADLINE_SECONDS,
                                      path, project_path, file_pattern),
                ensure_ascii=False)
        # ISS-12: _last_regex_timeout, not the asyncio.TimeoutError we are
        # holding -- that one is a synthetic re-raise and carries no matches.
        _timeout_resp = _regex_timeout_payload(query, effective_regex_deadline, path, project_path, file_pattern,
                                               partial_source=_regex_input_phase_source(_last_regex_timeout),
                                               attempts=_regex_attempts_used,
                                               initial_deadline=_regex_initial_deadline)
        _note_regex_deadline_error(query, project_path, path, file_pattern, effective_regex_deadline,
                                   regex_deadline_seconds, _timeout_resp)
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
                                              partial_source=_regex_input_phase_source(exc),
                                              attempts=_regex_attempts_used,
                                               initial_deadline=_regex_initial_deadline)
            _note_regex_deadline_error(query, project_path, path, file_pattern, effective_regex_deadline,
                                       regex_deadline_seconds, _rt_resp)
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
    if mode == "regex":
        _clear_regex_deadline_state(project_path, path, file_pattern, query)
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
    if mode == "regex" and _regex_deadline_floor_applied:
        response["regex_deadline_floor_applied"] = 2.0
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
    # D3 2026-09-22: a 0-hit answer has TWO completely different causes and this
    # function used to conflate them. Either the glob admitted files and none of
    # them contained the query (a real absence, modulo staleness), or the glob
    # admitted NOTHING -- a typo'd path, a file not created yet, a glob aimed at a
    # tree the indexer never took -- in which case the query never ran at all.
    # The second cause is DETERMINISTIC and it outranks every guess below it: the
    # drift probe hashes the whole repo and still cannot make a nonexistent path
    # appear, and the multi-word tip blames a query that was never the problem.
    # Measured on ToolQ: search_code(query='clm_qsleep_session',
    # file_pattern='tests/test_qsleep_session_pause_54.py') answered ok:true /
    # results:[] with no cause at all, so one CA guessed "the file does not exist"
    # (right) while the same payload reads equally well as "the code is missing"
    # (wrong, and expensive). Answering the real cause here also SKIPS the O(repo)
    # probe, so the honest answer is the cheap one -- this is not a new advisory
    # field bolted on to explain a caller mistake, it replaces two advisories that
    # were actively naming the wrong cause.
    # P15: drop hits whose file no longer exists on disk BEFORE any verdict
    # logic runs. A native delete never republishes the snapshot, so the
    # singleflight cache can keep serving a pre-delete payload (its key only
    # changes on republish); without this filter the deleted file's hit is
    # re-served forever and the honest-0 reconcile below never fires. This is
    # O(hits) disk stats, bounded by effective_max_results -- not a walk.
    # Phantoms are dropped from BOTH `results` and the response so count stays
    # consistent; an emptied set falls through to the 0-hit probe, where the
    # reconcile removes the deleted file from INDEX+snapshot and the re-search
    # answers the honest clean 0.
    if results:
        _kept = []
        for _hit in results:
            if not isinstance(_hit, dict):
                _kept.append(_hit)
                continue
            _rel = str(_hit.get("file") or "")
            try:
                _abs = (base_resolved if base_is_file
                        else (base_resolved / _rel))
                if _abs.is_file():
                    _kept.append(_hit)
            except Exception:
                _kept.append(_hit)  # a stat failure must not erase a real hit
        if len(_kept) != len(results):
            results = _kept
            response["results"] = results
            response["count"] = len(results)
    _fp_scoped = bool(file_pattern) and file_pattern != "*"
    _fp_admitted = producer_payload.get("pattern_admitted_files")
    if not results and _fp_scoped and _fp_admitted == 0:
        _scanned = int(producer_payload.get("records_scanned") or 0)
        response["file_pattern_matched_files"] = 0
        response["indexed_files_scanned"] = _scanned
        response["inconclusive"] = True
        if _scanned:
            response["ca_next_action"] = "fix_file_pattern_or_drop_it"
            response["tip"] = (
                "0 hits because file_pattern=%r matched NONE of the %d indexed "
                "files, so the query never ran against anything. This is NOT "
                "'the text is absent' and NOT index drift. Check the path for a "
                "typo, confirm the file exists AND is code (this index holds code "
                "only -- .txt/.md/.log are never admitted), or drop file_pattern "
                "to search the whole scope." % (file_pattern, _scanned)
            )
        else:
            # Nothing at all under this scope: file_pattern is not the only suspect.
            response["ca_next_action"] = "check_project_path_then_refresh_project_map"
            response["tip"] = (
                "0 hits and the index holds NO file under this scope, so "
                "file_pattern is not the only suspect: verify project_path/path "
                "points at the intended repo, then refresh_project_map."
            )
    elif not results and not _skip_stale_reconcile:
        # P15: the post-reconcile re-search skips this probe -- the probe JUST
        # measured this scope and the reconcile either fixed the drift or said
        # why not, so re-probing could only re-pay the scoped-hash cost (or, on
        # a reconcile that reported a residue, re-measure a drift the caller
        # already sees in stale_reconcile). The re-search's 0-hit verdict is
        # answered from the reconcile outcome in _search_once_after_reconcile.
        drift_root = project_path or str(snapshot_root)
        # P10 (2026-10-02): the probe certifies the hit set THIS search could
        # have served. A scoped search (path=...) therefore probes only the
        # indexed files + walk under that path -- drift anywhere else cannot
        # change this 0-hit answer, and on a large root hashing the whole repo
        # to certify one directory cost 14.6s in-process (168-245s via a loaded
        # daemon) on EVERY 0-hit. stale_index_scope says what was measured, so
        # a scoped stale_index:False never reads as a whole-repo clean bill.
        drift_scope = ""
        try:
            if (base_resolved != snapshot_root
                    and base_resolved.is_relative_to(snapshot_root)):
                drift_scope = base_resolved.relative_to(snapshot_root).as_posix()
        except Exception:
            drift_scope = ""
        # P13: cache_delta compares INDEX against DISK only. When the write path
        # advanced INDEX but the SERVED snapshot was never republished (the
        # fresh-project publish failure), INDEX == disk and that probe certifies
        # "clean" while this search actually served a PRE-WRITE tree. The
        # producer already compared the served manifest against INDEX (only on
        # this empty-results path): a 0-hit over a snapshot that lacks an
        # indexed file is inconclusive, never stale_index False.
        _snap_missing = producer_payload.get("snapshot_missing_indexed_files")
        if _snap_missing:
            response["stale_index"] = True
            response["stale_index_reason"] = "snapshot_behind_index"
            response["snapshot_missing_indexed_files"] = _snap_missing
            _snap_sample = producer_payload.get("snapshot_missing_indexed_sample")
            if _snap_sample:
                response["snapshot_missing_indexed_sample"] = _snap_sample
            response["inconclusive"] = True
            response["ca_next_action"] = "refresh_or_narrow"
        else:
            stale_reason = ""
            delta = None
            try:
                import code_tools
                if not drift_root:
                    stale_reason = "no_project_path"
                else:
                    raw_delta = json.loads(await code_tools.cache_delta(drift_root, scope=drift_scope))
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
        if drift_scope:
            response["stale_index_scope"] = drift_scope
        # P15 (2026-10-03, r2): a 0-hit whose scoped probe MEASURED drift no
        # longer stops at the honest INCONCLUSIVE report -- the report cannot
        # answer the question the caller asked, and refresh_project_map on a
        # large root is forbidden by rule. Reconcile the measured drift through
        # the existing per-file machinery, BUDGETED by time/bytes per call --
        # r1's fixed file-count gate measured live as permanently unanswerable
        # on a project where agents write natively all the time (49. CUA:
        # drift 223 > bound 200 -> drift_above_bound forever). Now: reconcile
        # what fits (deletions first, then searched-scope first, smallest
        # first), republish, re-search ONCE; a residue keeps the honest
        # inconclusive and the NEXT call continues from it (the probe
        # re-measures, so processed files are never redone). Two routes by
        # index shape: an INDEX exists -> reconcile_stale_scope_drift (INDEX is
        # authoritative); no INDEX -> reconcile_search_snapshot_drift (the
        # search-side snapshot IS the only index: re-measure MANIFEST-vs-DISK --
        # cache_delta is blind with no INDEX and reports every file as "added"
        # forever -- and republish the snapshot incrementally; never build an
        # INDEX inside a search call). The work runs on the search lane
        # executor (never the event loop) and never walks the repo for the
        # INDEX route (TOOLA-AHEALTHHUNG-R1).
        rec_report = None
        if not _skip_stale_reconcile and not results and response.get("stale_index") is True:
            import surgical_mapper as _sm
            _stale_reason = response.get("stale_index_reason")
            _has_index = (Path(snapshot_root) / search_snapshots.CACHE_DIR_NAME
                          / search_snapshots.INDEX_FILENAME).is_file()
            if not _has_index:
                # No-INDEX route: the helper re-measures drift against the
                # served manifest itself, so no delta sourcing happens here.
                _recon_budget = max(
                    1.0,
                    search_started_at + SEARCH_TEXT_DEADLINE_SECONDS
                    - time.monotonic())
                _permit = None
                try:
                    _permit = await FAIR_ADMISSION.acquire(
                        "search",
                        deadline=time.monotonic() + _recon_budget)
                    rec_report = await get_executor_lane("search").run(
                        _sm.reconcile_search_snapshot_drift,
                        str(snapshot_root),
                        scope=drift_scope or "",
                        deadline_at=time.monotonic() + _recon_budget,
                        queue_wait_deadline=time.monotonic() + _recon_budget)
                except Exception as _recon_exc:
                    rec_report = {"applied": False,
                                  "reason": f"{type(_recon_exc).__name__}: {_recon_exc}"}
                finally:
                    if _permit is not None:
                        _permit.release()
                if (isinstance(rec_report, dict)
                        and rec_report.get("reason") == "snapshot_clean"):
                    # The served snapshot ALREADY matches disk: the drift report
                    # was a phantom of the INDEX-less probe (every file reads as
                    # "added"), so this 0-hit is a REAL not-found. Flip to clean
                    # and do NOT re-search (nothing was republished).
                    response["stale_index"] = False
                    response.pop("stale_index_reason", None)
                    response.pop("stale_files_count", None)
                    response.pop("inconclusive", None)
                    response.pop("ca_next_action", None)
            else:
                if _stale_reason == "drift_detected" and delta is not None and stale_count:
                    _scope_added = delta.get("added") or []
                    _scope_modified = delta.get("modified") or []
                    _scope_deleted = delta.get("deleted") or []
                elif _stale_reason == "snapshot_behind_index":
                    # P15: cache_delta compares INDEX against DISK only, so it is
                    # blind here -- INDEX == disk while the SERVED snapshot predates
                    # a write (the fresh-project publish failure). The drift set is
                    # INDEX files the served manifest lacks (the producer already
                    # counted them); treat each as "modified" so the reconcile
                    # re-caches it and republishes. `delta` is UNBOUND on this
                    # branch, so the drift set must be sourced here, never from it.
                    _scope_added = []
                    _scope_deleted = []
                    _scope_modified = []
                    try:
                        _pin = search_snapshots.pin_current_snapshot(str(snapshot_root))
                        try:
                            _manifest_paths = {
                                str(r.get("path") or "").replace("\\", "/").casefold()
                                for r in _pin.manifest.get("files", [])
                            }
                        finally:
                            _pin.release()
                        _live_index = json.loads(
                            (Path(snapshot_root) / search_snapshots.CACHE_DIR_NAME
                             / search_snapshots.INDEX_FILENAME).read_text(encoding="utf-8"))
                        _scope_modified = sorted(
                            str(rel).replace("\\", "/")
                            for rel in (_live_index.get("files") or {})
                            if str(rel).replace("\\", "/").casefold() not in _manifest_paths
                        )
                    except Exception as _behind_exc:
                        rec_report = {"applied": False,
                                      "reason": f"behind_index_probe_failed: {type(_behind_exc).__name__}: {_behind_exc}"}
                        _scope_modified = []
                else:
                    _scope_added = []
                    _scope_modified = []
                    _scope_deleted = []
                if rec_report is None:
                    _scope_drift = (len(_scope_added) + len(_scope_modified)
                                    + len(_scope_deleted))
                    if _scope_drift:
                        _recon_budget = max(
                            1.0,
                            search_started_at + SEARCH_TEXT_DEADLINE_SECONDS
                            - time.monotonic())
                        _permit = None
                        try:
                            _permit = await FAIR_ADMISSION.acquire(
                                "search",
                                deadline=time.monotonic() + _recon_budget)
                            rec_report = await get_executor_lane("search").run(
                                _sm.reconcile_stale_scope_drift,
                                str(snapshot_root),
                                _scope_added, _scope_modified, _scope_deleted,
                                deadline_at=time.monotonic() + _recon_budget,
                                priority_scope=drift_scope or "",
                                queue_wait_deadline=time.monotonic() + _recon_budget)
                        except Exception as _recon_exc:
                            rec_report = {"applied": False,
                                          "reason": f"{type(_recon_exc).__name__}: {_recon_exc}"}
                        finally:
                            if _permit is not None:
                                _permit.release()
        if rec_report is not None:
            response["stale_reconcile"] = rec_report
            if rec_report.get("applied"):
                # Stamp BEFORE the re-search: a concurrent same-scope call that
                # starts now reads the pre-publish CURRENT, and only this epoch
                # bump keeps it from joining the pre-reconcile singleflight
                # future (snapshot_token has not flipped yet -- publish is async).
                _ekey = str(snapshot_root).casefold()
                _SEARCH_RECONCILE_EPOCH[_ekey] = _SEARCH_RECONCILE_EPOCH.get(_ekey, 0) + 1
                return await _search_once_after_reconcile(locals())
    # D3: never let the query-shape guess overwrite a measured cause. When the glob
    # admitted zero files the query shape is irrelevant, and this tip used to fire
    # anyway and send the caller off to rewrite a perfectly good query.
    if (not results and mode == "text" and len(query.split()) > 1
            and "file_pattern_matched_files" not in response):
        response.setdefault("tip", "0 hits for text multi-word query. Try regex "
                                   "OR-split or search_code(mode='symbol').")
        response.setdefault("ca_next_action", "retry_with_regex_or_symbol_mode")
    # Task 3 auto-chain rule 5: hint for read_file_range when search found file+line
    if results and mode in {"text", "regex", "smart"}:
        _has_line_hits = any(r.get("line") or r.get("line_number") for r in results if isinstance(r, dict))
        if _has_line_hits:
            response["ca_hint"] = "read_file_range available for detailed view of matched files"
    return json.dumps(response, ensure_ascii=False)
