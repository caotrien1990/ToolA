"""Graph indexer: scan project scope and populate graph DB (Phase 1 Step 7).

Orchestrates: file walk (SKIP_DIRS-aware) -> extractor -> store. Records an
index_runs row per run. Incremental: only re-extracts files whose content_hash
changed. Verify gates (plan line 91): core scope <45s / full <150s, node_count>500,
edge_count>1000, no source mutation, rerun parses 0 unchanged files.
"""
from concurrent.futures import ProcessPoolExecutor
import hashlib
import __main__
import os
import sqlite3
import time
from typing import Optional

from .config import (
    INDEX_TIME_BUDGET_CORE_S, INDEX_TIME_BUDGET_FULL_S,
    INDEX_MIN_NODES, INDEX_MIN_EDGES, CBM_EXTRACT_JS,
)
from . import store as _store
from .extractors.python import extract as _extract_python
from .extractors import javascript as _extractor_js

# Reuse the canonical skip set so the indexer never descends into the same
# dirs the existing [A] search tools skip.
import surgical_mapper  # resolved from project root (code_tools does the same)
SKIP_DIRS = surgical_mapper.SKIP_DIRS

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
                skip_dirs: Optional[set[str]] = None) -> list[str]:
    """Yield absolute file paths under project_root matching exts, honoring SKIP_DIRS."""
    skip = skip_dirs if skip_dirs is not None else SKIP_DIRS
    out: list[str] = []
    root = os.path.normpath(project_root)
    for dirpath, dirnames, filenames in os.walk(root):
        # mutate dirnames in place to prune skipped dirs
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
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
            candidates = _walk_files(core_root, exts=exts) if os.path.isdir(core_root) else []
        else:
            candidates = _walk_files(root, exts=exts)

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

        # BUG-3a: seed cross-file imports_module edges on full index too;
        # otherwise they only appear after the first incremental_index run.
        dep_stats = _build_dependency_edges(conn)
        dep_edges = dep_stats["edges"]
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
            "nodes": counts["nodes"], "edges": counts["edges"],
            "nodes_new": total_nodes, "edges_new": total_edges,
            "dependency_edges": dep_edges,
            "dependency_stats": dep_stats,
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
        candidates = _walk_files(core_root, exts=exts) if os.path.isdir(core_root) else []
    else:
        candidates = _walk_files(root, exts=exts)
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
    unresolved = 0
    self_skipped = 0

    for r in conn.execute(
            "SELECT id, qualified_name, file_path, start_line "
            "FROM nodes WHERE kind='import';"):
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
        conn.execute(
            "INSERT INTO edges (source, target, kind, confidence, cbm_label, "
            "metadata, line, col, is_external) VALUES (?,?,?,?,?,?,?,?,0);",
            (r["id"], target_id, "imports_module",
             0.9 if mode == "absolute" else 0.8, "inferred",
             '{"resolution": "%s"}' % mode, r["start_line"], None))
        added += 1
        by_mode[mode] += 1

    return {
        "edges": added,
        "by_resolution": by_mode,
        "unresolved_imports": unresolved,
        "self_imports_skipped": self_skipped,
        "ambiguous_modules": len(ambiguous),
        "modules_indexed": len(modules),
    }


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
