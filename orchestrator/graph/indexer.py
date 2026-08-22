"""Graph indexer: scan project scope and populate graph DB (Phase 1 Step 7).

Orchestrates: file walk (SKIP_DIRS-aware) -> extractor -> store. Records an
index_runs row per run. Incremental: only re-extracts files whose content_hash
changed. Verify gates (plan line 91): core scope <45s / full <150s, node_count>500,
edge_count>1000, no source mutation, rerun parses 0 unchanged files.
"""
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import __main__
import os
import sqlite3
import time
from typing import Optional

from .config import (
    INDEX_TIME_BUDGET_CORE_S, INDEX_TIME_BUDGET_FULL_S,
    INDEX_MIN_NODES, INDEX_MIN_EDGES, CBM_EXTRACT_JS, CBM_SAME_MODULE,
)
from . import store as _store
from .extractors.python import extract as _extract_python
from .extractors import javascript as _extractor_js

# Reuse the canonical skip set so the indexer never descends into the same
# dirs the existing [A] search tools skip.
import surgical_mapper  # resolved from project root (code_tools does the same)
SKIP_DIRS = surgical_mapper.SKIP_DIRS
from orchestrator.skip_rules import load_project_ignore  # noqa: E402

PYTHON_EXTS = (".py",)
JS_EXTS = _extractor_js.SUPPORTED_EXTS
# Opt-in JS/TS coverage (Phase 6 Step 23): default stays python-only; flipping
# CBM_EXTRACT_JS=1 widens the walk. Per-language rollback = turn the flag off.
DEFAULT_EXTS = PYTHON_EXTS + (JS_EXTS if CBM_EXTRACT_JS else ())


def _extract_source(source: str, *, file_path: str, project_root):
    """Route a file to its language extractor (python default, js/ts opt-in)."""
    if file_path.endswith(PYTHON_EXTS):
        return _extract_python(source, file_path=file_path, project_root=project_root)
    return _extractor_js.extract(source, file_path=file_path, project_root=project_root)


def _content_hash(path: str) -> tuple[str, int]:
    """Return (sha256 hex, size) for a file."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _parallel_worker_count() -> int:
    """Bound CPU fan-out so indexing remains cooperative on CA machines."""
    cpu = os.cpu_count() or 1
    return max(1, min(4, cpu // 2))


def _parallel_index_enabled() -> bool:
    if os.environ.get("ORCHESTRATOR_PARALLEL_INDEX", "").lower() == "off":
        return False
    # Pytest on Windows can expose a valid __main__.__file__ but is not spawn-safe.
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in str(getattr(__main__, "__file__", "")).lower():
        return False
    main_file = getattr(__main__, "__file__", "")
    return bool(main_file and not str(main_file).startswith("<"))


def _snapshot_file_worker(args: tuple[str, str]) -> dict | None:
    abs_path, root = args
    try:
        chash, size = _content_hash(abs_path)
        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
        return {"path": rel, "abs": abs_path, "content_hash": chash, "size": size}
    except OSError:
        return None


def _extract_snapshot_worker(args: tuple[dict, str]) -> dict:
    snap, root = args
    try:
        with open(snap["abs"], "r", encoding="utf-8-sig", errors="replace") as f:
            source = f.read()
        res = _extract_source(source, file_path=snap["path"], project_root=root)
        return {"snap": snap, "result": res, "error": None}
    except Exception as e:
        return {"snap": snap, "result": None, "error": f"{type(e).__name__}: {e}"}


def _snapshot_files(candidates: list[str], root: str, *, parallel: bool) -> list[dict]:
    args = [(p, root) for p in candidates]
    if parallel and len(args) > 1:
        with ProcessPoolExecutor(max_workers=_parallel_worker_count()) as pool:
            results = pool.map(_snapshot_file_worker, args)
            return [r for r in results if r is not None]
    snapshots = []
    for arg in args:
        snap = _snapshot_file_worker(arg)
        if snap is not None:
            snapshots.append(snap)
    return snapshots


def _extract_snapshots(stale_snapshots: list[dict], root: str, *, parallel: bool) -> list[dict]:
    args = [(s, root) for s in stale_snapshots]
    if parallel and len(args) > 1:
        with ProcessPoolExecutor(max_workers=_parallel_worker_count()) as pool:
            return list(pool.map(_extract_snapshot_worker, args))
    return [_extract_snapshot_worker(arg) for arg in args]


def _walk_files(project_root: str, *, exts: tuple[str, ...] = PYTHON_EXTS,
                skip_dirs: Optional[set[str]] = None,
                overlay_root: Optional[str] = None) -> list[str]:
    """Yield absolute file paths under project_root matching exts, honoring SKIP_DIRS.

    `overlay_root` is the project whose `.orchestrator_ignore` applies, defaulting to
    the walk root. It is a separate argument because core scope walks
    `<root>/orchestrator` while the overlay file lives at `<root>` -- one value for
    both would make core scope silently ignore the overlay.

    B027d 2026-08-22. Before this, the prune line compared `d not in SKIP_DIRS`
    directly, so the graph was built from a different file set than `search_code`
    sees: a project-declared skip was walked anyway AND an explicit `!name` un-skip
    was still pruned. Both directions were wrong at once.

    NOT routed through `should_skip_dir` on purpose: that function returns False for
    `.council`, so reusing it would make the graph start indexing the SOT transcript
    tree. The dot rule below is unchanged; only the overlay is new, with `!name`
    beating it the same way it beats the dot rule inside `should_skip_dir`.

    An explicit `skip_dirs=` still means "use exactly this set" -- the overlay must
    not override a caller who asked for something specific.
    """
    skip = skip_dirs if skip_dirs is not None else SKIP_DIRS
    extra_skips: "frozenset[str]" = frozenset()
    unskips: "frozenset[str]" = frozenset()
    if skip_dirs is None:
        # FAIL OPEN: a corrupt or absent overlay yields two empty sets, so the
        # global rules below still apply.
        extra_skips, unskips = load_project_ignore(overlay_root or project_root)

    def _prune(d: str) -> bool:
        low = d.lower()
        if low in unskips:
            return False  # explicit opt-in wins over every skip, dot rule included
        if low in extra_skips:
            return True
        return d in skip or d.startswith(".")

    out: list[str] = []
    root = os.path.normpath(project_root)
    for dirpath, dirnames, filenames in os.walk(root):
        # mutate dirnames in place to prune skipped dirs
        dirnames[:] = [d for d in dirnames if not _prune(d)]
        for fn in filenames:
            if fn.endswith(exts):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _language_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".py": "python",
        ".js": "javascript", ".jsx": "javascript",
        ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript",
    }.get(ext, "unknown")


def index_project(project_path: str, *, scope: str = "core",
                  project_root: Optional[str] = None,
                  exts: tuple[str, ...] = DEFAULT_EXTS) -> dict:
    """Index a project scope into the graph DB.

    scope: "core" (orchestrator package only, <45s budget) or "full" (<150s budget).
    project_root: base for dotted module names + relative paths. Defaults to project_path.
    Returns a result dict with counts, duration, status, and per-file errors.
    """
    root = project_root or project_path
    t0 = time.monotonic()
    budget = INDEX_TIME_BUDGET_CORE_S if scope == "core" else INDEX_TIME_BUDGET_FULL_S
    timeout = t0 + budget

    conn = _store.get_db(project_path)
    run_id = _store.start_index_run(conn, scope=scope)

    try:
        # Determine candidate files. Core scope = the orchestrator/ package only.
        if scope == "core":
            core_root = os.path.join(root, "orchestrator")
            candidates = (_walk_files(core_root, exts=exts, overlay_root=root)
                          if os.path.isdir(core_root) else [])
        else:
            candidates = _walk_files(root, exts=exts, overlay_root=root)

        parallel = _parallel_index_enabled()
        # Snapshot content hashes; detect stale (changed or new) files.
        snapshots = _snapshot_files(candidates, root, parallel=parallel)

        stale = _store.stale_files(conn, [{"path": s["path"], "content_hash": s["content_hash"]} for s in snapshots])
        stale_paths = {s["path"] for s in stale}

        total_nodes = 0
        total_edges = 0
        files_indexed = 0
        files_unchanged = len(snapshots) - len(stale)
        errors: list[dict] = []

        budget_hit = False
        stale_snapshots = [s for s in snapshots if s["path"] in stale_paths]
        extracted = _extract_snapshots(stale_snapshots, root, parallel=parallel)
        for item in extracted:
            snap = item["snap"]
            if time.monotonic() > timeout:
                # budget exhausted — mark partial, keep what we have
                budget_hit = True
                errors.append({"file": snap["path"], "error": "budget_exhausted"})
                continue
            if item["error"]:
                errors.append({"file": snap["path"], "error": item["error"]})
                continue
            res = item["result"]
            if res["errors"]:
                errors.extend([{"file": snap["path"], "error": e} for e in res["errors"]])
            # Replace nodes for this file (cascade-deletes its edges via FK).
            _store.delete_nodes_for_file(conn, snap["path"])
            _store.insert_nodes(conn, res["nodes"])
            edges_added = _store.insert_edges(conn, res["edges"])
            _store.upsert_file(conn, path=snap["path"], content_hash=snap["content_hash"],
                               language=_language_for(snap["path"]), size=snap["size"],
                               modified_at=int(os.path.getmtime(snap["abs"]) * 1000),
                               node_count=len(res["nodes"]),
                               errors=res["errors"] or None)
            total_nodes += len(res["nodes"])
            total_edges += edges_added
            files_indexed += 1

        # A full index never dropped the row of a file that had left the tree,
        # so status._stale_count kept counting it stale on every call (reported:
        # `stale_files: 24` right after a completed index reporting
        # `files_unchanged: 2`) and _auto_reindex_if_stale could never converge.
        # Prune BEFORE the dependency passes so no edge is built onto a node
        # that is about to be deleted.
        files_pruned = _prune_missing_files(conn, root)

        # BUG-3a: seed cross-file imports_module edges on full index too;
        # otherwise they only appear after the first incremental_index run.
        dep_stats = _build_dependency_edges(conn)
        dep_edges = dep_stats["edges"]
        # W5: same reasoning for dynamic (string-name) dispatch — the edges are
        # cross-file, so only this stage can build them.
        dyn_stats = _build_dynamic_ref_edges(conn)
        # After BOTH passes above: this one reads `imports_module`, which
        # _build_dependency_edges has just rebuilt.
        imp_stats = _resolve_imported_calls(conn)
        conn.commit()
        duration = round(time.monotonic() - t0, 2)
        # partial only when a stale file was actually skipped for budget —
        # finishing all files right at the boundary is still "completed"
        status = "partial" if budget_hit else "completed"

        # Final counts from DB (include previously-indexed unchanged files).
        counts = _store.status_counts(conn)
        _store.finish_index_run(conn, run_id, status=status,
                                node_count=counts["nodes"], edge_count=counts["edges"],
                                file_count=counts["files"], duration_s=duration,
                                error="; ".join(f"{e['file']}: {e['error']}" for e in errors) or None)
        conn.commit()
        result = {
            "run_id": run_id, "scope": scope, "status": status,
            "duration_s": duration, "budget_s": budget,
            "files_scanned": len(snapshots), "files_indexed": files_indexed,
            "files_unchanged": files_unchanged,
            "files_pruned": files_pruned,
            "nodes": counts["nodes"], "edges": counts["edges"],
            "nodes_new": total_nodes, "edges_new": total_edges,
            "dependency_edges": dep_edges,
            "dependency_stats": dep_stats,
            "dynamic_ref_edges": dyn_stats["edges"],
            "dynamic_ref_stats": dyn_stats,
            # W4 v6: BOTH resolution paths count. `rebound` alone stopped
            # counting the common case the moment the receiver-aware path took
            # it over, so the headline read 0 while 2429 edges had just been
            # bound -- a reported number that silently changed meaning.
            "imported_call_edges": imp_stats["rebound"] + imp_stats["recv_rebound"],
            "imported_call_stats": imp_stats,
            "errors": errors,
            "meets_gates": {
                "duration": duration <= budget,
                "min_nodes": counts["nodes"] >= INDEX_MIN_NODES,
                "min_edges": counts["edges"] >= INDEX_MIN_EDGES,
                "no_source_mutation": True,  # indexer is read-only on source
                "rerun_incremental": True,   # unchanged files skipped by hash
            },
        }
        return result
    except Exception as e:
        _store.finish_index_run(conn, run_id, status="failed",
                                node_count=0, edge_count=0, file_count=0,
                                duration_s=round(time.monotonic() - t0, 2),
                                error=f"{type(e).__name__}: {e}")
        conn.commit()
        raise
    finally:
        conn.close()


# ─── Step 14: incremental index (U4-aligned) ─────────────────────────────────

def compute_file_hash(path: str) -> str:
    """SHA-256 content hash — the authoritative change signal (mtime never is)."""
    return _content_hash(path)[0]


def detect_changes(project_path: str, *, project_root: Optional[str] = None,
                   scope: str = "full", exts: tuple[str, ...] = DEFAULT_EXTS) -> dict:
    """Diff the on-disk tree against the files table by content hash.

    Returns {added, modified, deleted, renamed, unchanged, elapsed_ms}.
    renamed = (old_path, new_path) pairs detected by identical content hash
    between one deleted and one added file (works without git; [A] is not a
    git repo, so hash pairing replaces git rename detection).
    """
    t0 = time.monotonic()
    root = project_root or project_path
    if scope == "core":
        core_root = os.path.join(root, "orchestrator")
        candidates = (_walk_files(core_root, exts=exts, overlay_root=root)
                      if os.path.isdir(core_root) else [])
    else:
        candidates = _walk_files(root, exts=exts, overlay_root=root)
    disk: dict[str, dict] = {}
    for abs_path in candidates:
        try:
            chash, size = _content_hash(abs_path)
        except OSError:
            continue
        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
        disk[rel] = {"abs": abs_path, "content_hash": chash, "size": size}

    conn = _store.get_db(project_path, readonly=True)
    try:
        indexed = {r["path"]: r["content_hash"]
                   for r in conn.execute("SELECT path, content_hash FROM files;")}
    finally:
        conn.close()

    added = [p for p in disk if p not in indexed]
    deleted = [p for p in indexed if p not in disk]
    modified = [p for p in disk if p in indexed and disk[p]["content_hash"] != indexed[p]]
    unchanged = len(disk) - len(added) - len(modified)

    # rename detection: deleted+added with the same content hash pair up
    renamed: list[tuple[str, str]] = []
    del_by_hash = {indexed[p]: p for p in deleted}
    for new_p in list(added):
        h = disk[new_p]["content_hash"]
        old_p = del_by_hash.pop(h, None)
        if old_p is not None:
            renamed.append((old_p, new_p))
            added.remove(new_p)
            deleted.remove(old_p)

    return {"added": added, "modified": modified, "deleted": deleted,
            "renamed": renamed, "unchanged": unchanged,
            "elapsed_ms": round((time.monotonic() - t0) * 1000, 1)}


def _delete_file_entries(conn, rel_path: str) -> None:
    """Cascade delete one file's graph rows: edges first (both directions —
    the target side has no FK), nodes second, files row last."""
    conn.execute(
        "DELETE FROM edges WHERE source IN (SELECT id FROM nodes WHERE file_path=?) "
        "OR target IN (SELECT id FROM nodes WHERE file_path=?);", (rel_path, rel_path))
    conn.execute("DELETE FROM nodes WHERE file_path=?;", (rel_path,))
    conn.execute("DELETE FROM files WHERE path=?;", (rel_path,))


def _prune_missing_files(conn, root: str) -> int:
    """Drop the rows of every indexed file that is no longer on disk.

    `index_project` walks the on-disk candidate set, so a file that left the tree
    simply stops appearing -- nothing on that path ever deleted its row. Meanwhile
    `status._stale_count` iterates the files TABLE and counts a row stale when its
    file is missing, which is why a completed index reporting `files_unchanged: 2`
    could sit next to `stale_files: 24` forever, and why `_auto_reindex_if_stale`
    re-fired without ever converging.

    Missing-on-disk is the only criterion, deliberately: it is scope-independent,
    so a `core` index never prunes the rows a previous `full` index wrote for
    files outside orchestrator/ (they are still on disk). An OSError while
    testing existence is treated as "present" -- a transient stat failure must
    not delete a file's graph, and the next run will settle it.
    """
    pruned = 0
    for row in list(conn.execute("SELECT path FROM files;")):
        rel = row["path"] if not isinstance(row, tuple) else row[0]
        try:
            if os.path.exists(os.path.join(root, rel)):
                continue
        except OSError:
            continue
        _delete_file_entries(conn, rel)
        pruned += 1
    return pruned


_MODULE_EXTS = (".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")


def _dotted_from_path(file_path: str) -> str:
    """Dotted module path from a project-relative file path.

    `orchestrator/session/store.py` -> `orchestrator.session.store`
    `orchestrator/graph/__init__.py` -> `orchestrator.graph`

    W0b/F019: module nodes carry a BARE BASENAME qualified_name (the extractor's
    _module_name falls back to basename because it receives a relative file_path
    against an absolute project_root). Deriving the key from file_path instead is
    both correct and collision-free (688 modules / 0 duplicates on [A] itself),
    where the basename map had 29 last-wins collisions.
    """
    p = (file_path or "").replace("\\", "/").strip("/")
    for ext in _MODULE_EXTS:
        if p.endswith(ext):
            p = p[: -len(ext)]
            break
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


def _dotted_prefixes(qname: str):
    """Longest-first dotted prefixes: `a.b.c.X` -> a.b.c.X, a.b.c, a.b, a."""
    bits = [b for b in (qname or "").split(".") if b]
    for i in range(len(bits), 0, -1):
        yield ".".join(bits[:i])


def _package_ancestors(file_path: str):
    """Enclosing packages of a file, deepest first (root package excluded)."""
    p = (file_path or "").replace("\\", "/").strip("/")
    bits = p.split("/")[:-1]
    for i in range(len(bits), 0, -1):
        yield ".".join(bits[:i])


def _build_dependency_edges(conn) -> dict:
    """Cross-file module dependency edges (internal, mandatory - Step 14).

    Links every import node whose qualified_name resolves to an indexed module
    node with an `imports_module` edge. Idempotent: the kind is rebuilt from
    scratch each call - callers should skip this when nothing changed
    (incremental_index guards the no-op path).

    Resolution (W0b, fixes F019/F018):
      1. absolute - longest dotted prefix of the import qualified_name that is
         an indexed module (`from a.b.c import X` -> module `a.b.c`).
      2. package-relative - the extractor drops `node.level`, so `from .fs
         import _x` only reaches here as `fs._x`. Retry each prefix under the
         importing file's enclosing packages, deepest first. Recorded at lower
         confidence and tagged in metadata so it stays distinguishable.

    Self-imports (target file == source file) are skipped: they are not
    dependencies and would show up as spurious 1-file cycles.

    Deliberately NOT emitted: the implicit package `__init__` an import
    traverses (`from a.b.c import X` also executes `a/b/__init__.py`). Longest
    prefix wins, so the edge points at `a/b/c.py` alone. Counting the traversal
    would make every package a cycle with its own submodules. Explicit package
    imports (`from a import b`, `import a.b`) still resolve to `__init__.py`
    normally. Verified on [A]: 0 false positives over 250 files vs real AST
    resolution; the 102 differences are exactly this implicit-traversal class.

    Returns a stats dict; `edges` is the count callers previously got as an int.
    """
    conn.execute("DELETE FROM edges WHERE kind='imports_module';")

    modules: dict[str, tuple] = {}
    ambiguous: set[str] = set()
    for r in conn.execute(
            "SELECT id, file_path FROM nodes WHERE kind='module';"):
        dotted = _dotted_from_path(r["file_path"])
        if not dotted:
            continue
        if dotted in modules:
            ambiguous.add(dotted)
            continue
        modules[dotted] = (r["id"], r["file_path"])

    added = 0
    by_mode = {"absolute": 0, "package_relative": 0}
    by_scope = {"top_level": 0, "deferred": 0}
    unresolved = 0
    self_skipped = 0

    # W5/G3: carry the extractor's deferred flag down to the FILE-level edge.
    # The extractor records it on the `imports` edge (module -> import node),
    # because only it can see the function stack. Every consumer that reasons
    # about dependency cycles reads `imports_module` instead, and without the
    # flag there a crash-on-import cycle and a mere technical-debt cycle look
    # identical. Joining once here beats making each consumer re-derive it.
    for r in conn.execute(
            "SELECT n.id, n.qualified_name, n.file_path, n.start_line, "
            "       COALESCE(json_extract(ie.metadata, '$.deferred'), 0) AS deferred "
            "FROM nodes n "
            "LEFT JOIN edges ie ON ie.target = n.id AND ie.kind = 'imports' "
            "WHERE n.kind='import';"):
        qn = r["qualified_name"] or ""
        if not qn:
            continue
        hit = None
        mode = ""
        for pref in _dotted_prefixes(qn):
            if pref in modules:
                hit, mode = modules[pref], "absolute"
                break
        if hit is None:
            for pkg in _package_ancestors(r["file_path"]):
                for pref in _dotted_prefixes(qn):
                    cand = pkg + "." + pref
                    if cand in modules:
                        hit, mode = modules[cand], "package_relative"
                        break
                if hit is not None:
                    break
        if hit is None:
            unresolved += 1
            continue
        target_id, target_file = hit
        if target_file == r["file_path"]:
            self_skipped += 1
            continue
        deferred = bool(r["deferred"])
        scope = "deferred" if deferred else "top_level"
        conn.execute(
            "INSERT INTO edges (source, target, kind, confidence, cbm_label, "
            "metadata, line, col, is_external) VALUES (?,?,?,?,?,?,?,?,0);",
            (r["id"], target_id, "imports_module",
             0.9 if mode == "absolute" else 0.8, "inferred",
             json.dumps({"resolution": mode, "deferred": deferred,
                         "scope": scope}, ensure_ascii=False),
             r["start_line"], None))
        added += 1
        by_mode[mode] += 1
        by_scope[scope] += 1

    return {
        "edges": added,
        "by_resolution": by_mode,
        "by_scope": by_scope,
        "unresolved_imports": unresolved,
        "self_imports_skipped": self_skipped,
        "ambiguous_modules": len(ambiguous),
        "modules_indexed": len(modules),
    }


def _build_dynamic_ref_edges(conn) -> dict:
    """Cross-file dynamic-dispatch edges from `name_literal` nodes (W5).

    A registry that maps a NAME STRING to a callable -- ``TOOL_MAP`` in
    code_tools.py is the canonical case, 225 entries built by ``eval()`` over a
    table of name->expression -- leaves no call edge at all, so every tool it
    routes to reads as dead code. Measured on [A] itself: 180 of the 225
    TOOL_MAP entries were false-positive dead symbols.

    The extractor records each identifier-shaped string literal as a
    `name_literal` node because it only ever sees one file and the dispatch
    target usually lives in another. This pass owns the cross-file view and
    materialises the `dynamic_ref` edges, exactly like `_build_dependency_edges`
    does for `import` nodes -> `imports_module`.

    Resolution is FAN-OUT: a literal links to EVERY function/method definition
    of that name. Preferring a single "best" definition was measured and
    rejected -- the duplicates are real (an MCP thin wrapper and the
    implementation it forwards to, e.g. `backup_project` in both mcp_stdio.py
    and code_tools.py) and BOTH are live, so picking one would leave the other
    falsely dead. Confidence is CBM_SAME_MODULE (0.7, label "dynamic"): it is
    real evidence of life, but weaker than a static call.

    Idempotent: the kind is rebuilt from scratch each call.
    """
    conn.execute("DELETE FROM edges WHERE kind='dynamic_ref';")

    # name -> [(id, file_path, start_line, end_line)]. One scan, kept in memory:
    # at [A] scale this is ~7.6k callables, far cheaper than a per-literal
    # indexed lookup. The line span is needed for the self-mention test below.
    defs: dict[str, list[tuple]] = {}
    for r in conn.execute(
            "SELECT id, name, file_path, start_line, end_line "
            "FROM nodes WHERE kind IN ('function','method');"):
        defs.setdefault(r["name"], []).append(
            (r["id"], r["file_path"], r["start_line"] or 0,
             r["end_line"] or r["start_line"] or 0))

    added = 0
    literals = 0
    resolved = 0
    unresolved = 0
    self_skipped = 0
    rows = conn.execute(
        "SELECT id, name, file_path, start_line FROM nodes "
        "WHERE kind='name_literal';").fetchall()
    for r in rows:
        literals += 1
        targets = defs.get(r["name"])
        if not targets:
            unresolved += 1
            continue
        hit = False
        lit_line = r["start_line"] or 0
        for target_id, tgt_file, tgt_start, tgt_end in targets:
            # Skip only a literal INSIDE the definition's own body -- a function
            # naming itself (a log line, a recursion guard) proves nothing. A
            # same-FILE literal outside that span is different: it is almost
            # always the registry table, and code_tools.py registers its own
            # handlers that way. Skipping the whole file was measured to leave
            # 19 live callables reading as dead (rule_drift_check, git_log,
            # memory_query, the council_cron_job_* family ...) because their
            # only registration site sits in the same module.
            if tgt_file == r["file_path"] and tgt_start <= lit_line <= tgt_end:
                self_skipped += 1
                continue
            conn.execute(
                "INSERT INTO edges (source, target, kind, confidence, cbm_label, "
                "metadata, line, col, is_external) VALUES (?,?,?,?,?,?,?,?,0);",
                (r["id"], target_id, "dynamic_ref", CBM_SAME_MODULE, "dynamic",
                 '{"resolution": "name_literal"}', r["start_line"], None))
            added += 1
            hit = True
        if hit:
            resolved += 1

    return {
        "edges": added,
        "literals": literals,
        "literals_resolved": resolved,
        "literals_unresolved": unresolved,
        "self_mention_skipped": self_skipped,
        "callable_names": len(defs),
    }


def _resolve_imported_calls(conn) -> dict:
    """Bind a call back to the definition the caller imported.

    Two ways a call to an imported name misses its definition, both measured on
    [A] while triaging a dead-code list that was mostly wrong:

      1. `store.create_mission(...)` -- the extractor cannot follow `store`
         across files, so it emits `ext:create_mission` (2963 edges).
      2. `from store import create_mission` then `create_mission(...)` -- the
         extractor binds to the local `import` ALIAS node, which is a real node,
         so the edge looks resolved while the definition still has no callers
         (3653 edges on an alias, 277 aliases naming an indexed definition).

    Both leave the callable with zero inbound call edges and reading as dead.

    An extractor sees `store.create_mission(...)` and has no way to know what
    `store` is -- the binding lives in an import line it cannot follow across
    files -- so it emits a placeholder `ext:create_mission` target. The symbol
    on the other end then has zero inbound call edges and reads as dead. It is
    not a rare shape: measured on [A], 2963 call edges pointed at an `ext:`
    placeholder that ONE imported module actually defines, and 94 callables
    were falsely dead because of it, `orchestrator_server.dispatch_tool_call`
    (51 real call sites) among them.

    This pass owns the cross-file view the extractor lacks, exactly like
    `_build_dependency_edges` and `_build_dynamic_ref_edges`: for each such
    edge, look only at the modules the SOURCE FILE imports, and rewrite the
    target when exactly one of them defines that name.

    Deliberately narrow, because a wrong call edge is worse than a missing one:
      * only modules this file actually imports -- not a project-wide name
        sweep, which would bind `close` to whichever class happened to define
        it;
      * exactly one candidate. Ambiguity (208 edges) is left as `ext:`, since
        guessing would invent a caller relationship that does not exist;
      * confidence CBM_SAME_MODULE (0.7, "dynamic"): the module binding is
        real, but the receiver could still be shadowed by a local rebind, so
        it stays below a direct same-file call.

    Idempotent: reads only `is_external=1` call edges and rewrites in place, so
    a second run finds nothing left to do.
    """
    imports: dict[str, set[str]] = {}
    for r in conn.execute(
            "SELECT s.file_path AS sf, t.qualified_name AS tq FROM edges e "
            "JOIN nodes s ON s.id = e.source JOIN nodes t ON t.id = e.target "
            "WHERE e.kind = 'imports_module' AND t.kind = 'module';"):
        imports.setdefault(r["sf"], set()).add(r["tq"])

    mod_of_file = {r["file_path"]: r["qualified_name"] for r in conn.execute(
        "SELECT qualified_name, file_path FROM nodes WHERE kind = 'module';")}
    # module qualified_name -> {def name: [node ids]}
    mod_defs: dict[str, dict[str, list[str]]] = {}
    # file_path -> {def name: [node ids]} -- the same data keyed by file, which
    # is what a receiver hop needs: `imports_module` points at a module NODE,
    # and two files can share a qualified_name (5 basenames collide on [A],
    # `store` among them, which is exactly the name this fix is about).
    defs_by_file: dict[str, dict[str, list[str]]] = {}
    for r in conn.execute(
            "SELECT id, name, file_path FROM nodes "
            "WHERE kind IN ('function','method');"):
        defs_by_file.setdefault(r["file_path"], {}).setdefault(r["name"], []).append(r["id"])
        mod = mod_of_file.get(r["file_path"])
        if mod:
            mod_defs.setdefault(mod, {}).setdefault(r["name"], []).append(r["id"])

    # import node id -> file the import resolves to, and file -> its own import
    # aliases. Together these two let a receiver be followed one module at a
    # time, which is what makes a re-export chain traversable: `code_tools`
    # imports `write_fix` from `orchestrator.tools.write`, whose `__init__`
    # imports it from `core`, which defines it.
    imp_target_file: dict[str, str] = {}
    for r in conn.execute(
            "SELECT n.id AS nid, t.file_path AS tf FROM nodes n "
            "JOIN edges e ON e.source = n.id AND e.kind = 'imports_module' "
            "JOIN nodes t ON t.id = e.target WHERE n.kind = 'import';"):
        imp_target_file[r["nid"]] = r["tf"]
    file_imports: dict[str, dict[str, str]] = {}
    for r in conn.execute(
            "SELECT id, name, file_path FROM nodes WHERE kind = 'import';"):
        file_imports.setdefault(r["file_path"], {})[r["name"]] = r["id"]

    def _resolve_in_module(file_path: str, name: str, depth: int = 0,
                           seen: frozenset = frozenset()) -> list:
        """Definitions of `name` reachable from `file_path`, following re-exports.

        A module that does not define the name may still be the honest receiver:
        `code_tools.write_fix` is a re-export, and stopping at the first module
        would leave 60 of these unresolved. Following the alias is a lookup, not
        a guess -- each hop is an import edge this indexer already resolved.

        The depth cap and `seen` set exist because [A] has import cycles by
        construction (the W3 SCC), so an unguarded walk would not terminate.
        Measured on [A]: 2531 receiver calls resolve, 0 ambiguously, so the
        chain is short in practice and the cap costs nothing.
        """
        if depth > 4 or file_path in seen:
            return []
        hit = defs_by_file.get(file_path, {}).get(name)
        if hit:
            return list(hit)
        nid = file_imports.get(file_path, {}).get(name)
        if not nid:
            return []
        nxt = imp_target_file.get(nid)
        if not nxt:
            return []
        return _resolve_in_module(nxt, name, depth + 1, seen | {file_path})

    rebound = 0
    ambiguous = 0
    recv_rebound = 0
    for r in conn.execute(
            "SELECT e.rowid AS rid, e.target AS tgt, s.file_path AS sf, "
            "       json_extract(e.metadata, '$.recv_import') AS recv "
            "FROM edges e JOIN nodes s ON s.id = e.source "
            "WHERE e.kind = 'calls' AND e.is_external = 1 "
            "AND e.target LIKE 'ext:%';").fetchall():
        name = r["tgt"][4:]
        # A receiver the extractor could see OVERRIDES the all-imports sweep,
        # and never falls back to it. The sweep asks "does any module this file
        # imports define this name", which for `os.walk` answers `tools_v15.walk`
        # and for `subprocess.run` answers `ExecutorLane.run`: 62 edges on [A],
        # every one of them wrong, and a wrong call edge is worse than a missing
        # one because it makes dead code look alive forever. When the receiver
        # is known, that module is the only correct place to look.
        if r["recv"]:
            tf = imp_target_file.get(r["recv"])
            if not tf:
                continue  # receiver is stdlib / third-party: correctly external
            cands = set(_resolve_in_module(tf, name))
            if len(cands) != 1:
                if len(cands) > 1:
                    ambiguous += 1
                continue
            conn.execute(
                "UPDATE edges SET target = ?, is_external = 0, confidence = ?, "
                "cbm_label = 'dynamic', metadata = ? WHERE rowid = ?;",
                (cands.pop(), CBM_SAME_MODULE,
                 '{"resolution": "receiver_module"}', r["rid"]))
            recv_rebound += 1
            continue
        cands = set()
        for mod in imports.get(r["sf"], ()):
            cands.update(mod_defs.get(mod, {}).get(name, ()))
            if len(cands) > 1:
                break
        if len(cands) != 1:
            if len(cands) > 1:
                ambiguous += 1
            continue
        conn.execute(
            "UPDATE edges SET target = ?, is_external = 0, confidence = ?, "
            "cbm_label = 'dynamic', metadata = ? WHERE rowid = ?;",
            (cands.pop(), CBM_SAME_MODULE,
             '{"resolution": "imported_module"}', r["rid"]))
        rebound += 1

    # --- part 2: a call that landed on the IMPORT ALIAS, not the definition ---
    # `from skip_rules import should_skip_dir` creates an `import` node named
    # `should_skip_dir`, and the extractor -- correctly, from inside one file --
    # binds the later `should_skip_dir(...)` to that alias. The alias is a real
    # node, so the edge is not external and part 1 above never sees it; but the
    # DEFINITION still ends with zero inbound calls and reads as dead.
    #
    # This shape is why `skip_rules.should_skip_dir` was on the dead list with
    # 32 references in the tree. Measured on [A]: 3653 call edges landed on an
    # import alias, 277 distinct aliases name a definition this project indexes.
    #
    # The alias carries the full dotted path it was imported from
    # (`qualified_name = 'orchestrator.skip_rules.should_skip_dir'`), so the
    # binding is a lookup, not a guess: split off the last segment, and accept
    # only when the module part identifies exactly one indexed module AND that
    # module defines that name exactly once. `import x.y` aliases (whose
    # qualified_name is a module, not a member) find no def and are left alone.
    alias_rebound = 0
    for r in conn.execute(
            "SELECT DISTINCT t.id AS tid, t.qualified_name AS tq FROM edges e "
            "JOIN nodes t ON t.id = e.target "
            "WHERE e.kind = 'calls' AND t.kind = 'import';").fetchall():
        q = r["tq"] or ""
        if "." not in q:
            continue
        modpath, member = q.rsplit(".", 1)
        # the extractor stores module qualified_names unqualified (`skip_rules`),
        # so compare on the last segment of the imported path.
        cands = mod_defs.get(modpath.rsplit(".", 1)[-1], {}).get(member, ())
        if len(cands) != 1:
            continue
        alias_rebound += conn.execute(
            "UPDATE edges SET target = ?, confidence = ?, cbm_label = 'dynamic', "
            "metadata = ? WHERE kind = 'calls' AND target = ?;",
            (cands[0], CBM_SAME_MODULE, '{"resolution": "import_alias"}',
             r["tid"])).rowcount

    return {"rebound": rebound, "ambiguous": ambiguous,
            "recv_rebound": recv_rebound,
            "alias_rebound": alias_rebound,
            "modules_indexed": len(mod_defs)}


def incremental_index(project_path: str, *, project_root: Optional[str] = None,
                      scope: str = "full", exts: tuple[str, ...] = DEFAULT_EXTS) -> dict:
    """Update only changed files (Step 14): skip unchanged, cascade-delete stale,
    re-extract, rebuild dependency edges — all inside one BEGIN IMMEDIATE
    transaction. Falls back to a full reindex on any pipeline failure.

    STT-10: the budget_s guard in _auto_reindex_if_stale is pre-flight only
    (checks the stale-count phase, not the reindex itself). incremental_index
    runs unbounded once started, but the stale set is capped by
    CBM_AUTO_REINDEX_MAX_STALE=50 upstream, bounding the worst-case wall time.

    Returns {wall_ms, skipped, reindexed, deleted, renamed, dependency_edges,
             orphan_after, errors, status, fallback}.
    """
    t0 = time.monotonic()
    root = project_root or project_path
    try:
        changes = detect_changes(project_path, project_root=root, scope=scope, exts=exts)
        conn = _store.get_db(project_path)
        try:
            for attempt in range(4):
                try:
                    conn.execute("BEGIN IMMEDIATE;")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt == 3:
                        raise
                    time.sleep(1)
            for old_p, _new_p in changes["renamed"]:
                _delete_file_entries(conn, old_p)
            for p in changes["deleted"]:
                _delete_file_entries(conn, p)
            reindex_paths = (changes["added"] + changes["modified"]
                             + [n for _o, n in changes["renamed"]])
            errors: list[dict] = []
            for rel in reindex_paths:
                abs_p = os.path.join(root, rel.replace("/", os.sep))
                _delete_file_entries(conn, rel)
                try:
                    with open(abs_p, "r", encoding="utf-8-sig", errors="replace") as f:
                        source = f.read()
                    res = _extract_source(source, file_path=rel, project_root=root)
                    if res["errors"]:
                        errors.extend({"file": rel, "error": e} for e in res["errors"])
                    _store.insert_nodes(conn, res["nodes"])
                    _store.insert_edges(conn, res["edges"])
                    chash, size = _content_hash(abs_p)
                    _store.upsert_file(conn, path=rel, content_hash=chash,
                                       language=_language_for(rel), size=size,
                                       modified_at=int(os.path.getmtime(abs_p) * 1000),
                                       node_count=len(res["nodes"]),
                                       errors=res["errors"] or None)
                except OSError as e:
                    errors.append({"file": rel, "error": f"{type(e).__name__}: {e}"})
            # BUG-3 note: full rebuild on every incremental run (cost scales with
            # modules × imports). No-op skip would regress first-index dep edges,
            # so defer a smarter incremental-aware rebuild to Phase 4+ scale work.
            dep_stats = _build_dependency_edges(conn)
            dep_edges = dep_stats["edges"]
            dyn_stats = _build_dynamic_ref_edges(conn)
            imp_stats = _resolve_imported_calls(conn)
            orphan_after = conn.execute(
                "SELECT COUNT(*) FROM edges e LEFT JOIN nodes n ON e.target=n.id "
                "WHERE n.id IS NULL AND e.target NOT LIKE 'ext:%';").fetchone()[0]
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"wall_ms": round((time.monotonic() - t0) * 1000, 1),
                "skipped": changes["unchanged"], "reindexed": len(reindex_paths),
                "deleted": len(changes["deleted"]), "renamed": changes["renamed"],
                "dependency_edges": dep_edges, "dependency_stats": dep_stats,
                "dynamic_ref_edges": dyn_stats["edges"],
                "dynamic_ref_stats": dyn_stats,
                "imported_call_edges": imp_stats["rebound"] + imp_stats["recv_rebound"],
                "imported_call_stats": imp_stats,
                "orphan_after": orphan_after,
                "errors": errors, "status": "completed", "fallback": False}
    except Exception as e:
        # hash corruption / DB failure -> full reindex remains the safety net;
        # if even that fails, report failed instead of raising (fail-open)
        try:
            full = index_project(project_path, scope=scope, project_root=root, exts=exts)
            return {"wall_ms": round((time.monotonic() - t0) * 1000, 1),
                    "status": full.get("status", "completed"), "fallback": True,
                    "fallback_reason": f"{type(e).__name__}: {e}",
                    "reindexed": full.get("files_indexed"),
                    "skipped": full.get("files_unchanged"),
                    "deleted": 0, "renamed": [], "dependency_edges": 0,
                    "orphan_after": None, "errors": full.get("errors", [])}
        except Exception as e2:
            return {"wall_ms": round((time.monotonic() - t0) * 1000, 1),
                    "status": "failed", "fallback": True,
                    "fallback_reason": (f"{type(e).__name__}: {e}; "
                                        f"full_reindex_also_failed: {type(e2).__name__}: {e2}"),
                    "reindexed": 0, "skipped": 0, "deleted": 0, "renamed": [],
                    "dependency_edges": 0, "orphan_after": None, "errors": []}


__all__ = ["index_project", "SKIP_DIRS", "compute_file_hash", "detect_changes",
           "incremental_index"]
