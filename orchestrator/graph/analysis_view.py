"""Readonly analysis projection for campaign graph metrics (P2-S1).

One scope/provenance contract shared by later P3+ services.
Does not change cbm_cycle_point legacy fields or scores.
Store is readonly: one read transaction when a DB is used; never
creates a missing DB or parent directory.

Payload version=1, status=ok|partial|unavailable. stale/unknown/corrupt/
missing schema never scores 0 and never passes.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import time
from typing import Any, Callable, Iterable

from .config import EXTRACTOR_VERSION, graph_db_path
from .schema import SCHEMA_VERSION
from . import status as _status
from . import store as _store


ALGORITHM_VERSION = "analysis-view-v1"
PAYLOAD_VERSION = 1
STRICT_MIN = 0.9
OBSERVED_MIN = 0.3
MAX_DISCOVERY_FILES = 10000
MAX_DISCOVERY_BYTES = 100 * 1024 * 1024
MAX_DISCOVERY_SECONDS = 10.0
_PROD_EXCLUDE_PREFIXES = (
    "tests/",
    "research/",
    "backup/",
    "backups/",
    ".orchestrator_cache/",
    "venv/",
    "venv312/",
    ".venv/",
    "__pycache__/",
)
_PROD_SEEDS = ("orchestrator_server.py", "code_tools.py", "tools_v15.py")
_CONFIDENCE_POLICY = {
    "strict_min": STRICT_MIN,
    "observed_min": OBSERVED_MIN,
    "kind": "imports_module",
    "internal_only": True,
    "drop_self": True,
    "dedup_pairs": True,
    "note_confidence_not_probability": True,
    "note_no_resolver_inference_from_0_7": True,
}


def _norm_slash(path: str) -> str:
    return str(path or "").replace("\\", "/").strip("/")


def _identity_key(path: str) -> str:
    return _norm_slash(path).casefold()


def _sorted_display(paths: Iterable[str]) -> list[str]:
    seen: dict[str, str] = {}
    for raw in paths:
        display = _norm_slash(raw)
        if not display:
            continue
        seen.setdefault(_identity_key(display), display)
    return sorted(seen.values(), key=lambda p: p.casefold())


def _included_set_hash(paths: Iterable[str]) -> str:
    payload = "\n".join(_sorted_display(paths))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha256(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _resolve_physical(path: str) -> tuple[str | None, str | None]:
    try:
        physical = os.path.realpath(path)
    except OSError:
        return None, "unresolved_target"
    if not os.path.exists(physical):
        return None, "unresolved_target"
    return physical, None


def _contained(physical: str, root_physical: str) -> bool:
    try:
        common = os.path.commonpath([physical, root_physical])
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(root_physical)


def _rel_display(abs_path: str, root: str) -> str:
    try:
        rel = os.path.relpath(abs_path, root)
    except ValueError:
        return _norm_slash(abs_path)
    return _norm_slash(rel)


def _is_excluded_production(rel: str) -> bool:
    norm = _norm_slash(rel)
    base = os.path.basename(norm)
    if base.endswith(".bak") or base.startswith("_baseline_"):
        return True
    folded = norm.casefold() + "/"
    return any(folded.startswith(p) for p in _PROD_EXCLUDE_PREFIXES)


def _candidate_python_files(
    root: str,
    *,
    checkpoint: Callable[[str], bool] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    rejected: list[dict[str, str]] = []
    found: list[str] = []
    root_physical, err = _resolve_physical(root)
    if root_physical is None:
        return [], [{"path": root, "reason": err or "unresolved_target"}]
    seen_physical: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if checkpoint is not None and checkpoint("walk"):
            raise InterruptedError("analysis_stop")
        keep_dirs: list[str] = []
        for name in dirnames:
            child = os.path.join(dirpath, name)
            rel = _rel_display(child, root)
            if _is_excluded_production(rel + "/"):
                continue
            physical, rerr = _resolve_physical(child)
            if physical is None:
                rejected.append({"path": rel, "reason": rerr or "unresolved_target"})
                continue
            if not _contained(physical, root_physical):
                rejected.append({"path": rel, "reason": "symlink_escape"})
                continue
            key = os.path.normcase(physical)
            if key in seen_physical:
                rejected.append({"path": rel, "reason": "symlink_cycle"})
                continue
            seen_physical.add(key)
            keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in filenames:
            if not name.endswith(".py"):
                continue
            child = os.path.join(dirpath, name)
            rel = _rel_display(child, root)
            if _is_excluded_production(rel):
                continue
            if not (rel.startswith("orchestrator/") or rel in _PROD_SEEDS):
                continue
            physical, rerr = _resolve_physical(child)
            if physical is None:
                rejected.append({"path": rel, "reason": rerr or "unresolved_target"})
                continue
            if not _contained(physical, root_physical):
                rejected.append({"path": rel, "reason": "symlink_escape"})
                continue
            found.append(rel)
    return _sorted_display(found), rejected


def _package_dir(rel_file: str) -> str:
    parent = os.path.dirname(_norm_slash(rel_file))
    return parent


def _module_candidates(rel_mod: str) -> list[str]:
    parts = [p for p in _norm_slash(rel_mod).split("/") if p and p != "."]
    if not parts:
        return []
    base = "/".join(parts)
    return [base + ".py", base + "/__init__.py"]


def _resolve_import(rel_file: str, module: str | None, level: int, root: str) -> list[str]:
    hits: list[str] = []
    if level and level > 0:
        parts = _package_dir(rel_file).split("/") if _package_dir(rel_file) else []
        if level - 1 > len(parts):
            return []
        if level - 1:
            parts = parts[: -(level - 1)]
        prefix = "/".join(p for p in parts if p)
        tail = _norm_slash(module or "")
        target = "/".join(x for x in (prefix, tail) if x)
        for cand in _module_candidates(target) if target else (
            [prefix + "/__init__.py"] if prefix else []
        ):
            abs_c = os.path.join(root, cand.replace("/", os.sep))
            if os.path.isfile(abs_c):
                hits.append(_norm_slash(cand))
        if prefix:
            init = prefix + "/__init__.py"
            abs_init = os.path.join(root, init.replace("/", os.sep))
            if os.path.isfile(abs_init):
                hits.append(init)
        return _sorted_display(hits)
    if not module:
        return []
    for cand in _module_candidates(module.replace(".", "/")):
        abs_c = os.path.join(root, cand.replace("/", os.sep))
        if os.path.isfile(abs_c):
            hits.append(_norm_slash(cand))
    parent = os.path.dirname(_norm_slash(hits[0])) if hits else ""
    if parent:
        init = parent + "/__init__.py"
        abs_init = os.path.join(root, init.replace("/", os.sep))
        if os.path.isfile(abs_init):
            hits.append(init)
    return _sorted_display(hits)


def _dynamic_import_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        label = ""
        if isinstance(func, ast.Name):
            label = func.id
        elif isinstance(func, ast.Attribute):
            label = func.attr
        if label in {"__import__", "import_module"}:
            names.append(label)
    return names


def _static_imports(tree: ast.AST) -> list[tuple[str | None, int]]:
    out: list[tuple[str | None, int]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, 0))
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module, int(node.level or 0)))
    return out
def _db_included_scope(root: str) -> dict[str, str] | None:
    """STT-26: included scope straight from the graph DB files table.

    One readonly SELECT replaces the os.walk + realpath-per-entry candidate
    discovery (measured 81.6s on ToolQ, 549 files, HDD) and the AST-walk
    filter hardcode (rel.startswith("orchestrator/")) that made included=0
    on every non-[A] repo. Returns None (fall back to the walk) when there
    is no DB, the schema is wrong, or the DB yields no production rows.
    """
    try:
        conn = _store.get_db(root, readonly=True)
    except Exception:
        return None
    try:
        if not _schema_ok(conn):
            return None
        rows = conn.execute("SELECT path FROM files;").fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    out: dict[str, str] = {}
    for r in rows:
        rel = _norm_slash(r["path"] if not isinstance(r, tuple) else r[0])
        if not rel or _is_excluded_production(rel):
            continue
        out[_identity_key(rel)] = rel
    return out or None

def finalize_included_scope_v1(
    root: str,
    *,
    candidate_files: Iterable[str] | None = None,
    test_files: Iterable[str] | None = None,
    checkpoint: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """AST import-closure over a candidate set. Never executes Python."""
    t0 = time.monotonic()
    diagnostics: list[dict[str, str]] = []
    blocked_reason = None
    bytes_read = 0
    files_seen = 0
    dynamic: list[str] = []
    unresolved: list[str] = []

    if candidate_files is None:
        # STT-26: graph-DB scope first (one SELECT over the files table).
        # The AST walk below stays the fallback for roots without a DB.
        if checkpoint is not None and checkpoint("db_scope"):
            raise InterruptedError("analysis_stop")
        db_scope = _db_included_scope(root)
        if db_scope is not None:
            tests = _sorted_display(test_files or [])
            included_files = _sorted_display(db_scope.values())
            blocked_reason = (
                "discovery_file_cap"
                if len(included_files) > MAX_DISCOVERY_FILES else None)
            return {
                "status": "blocked" if blocked_reason else "ok",
                "blocked_reason": blocked_reason,
                "included_files": included_files,
                "test_files": tests,
                "included_set_hash": _included_set_hash(included_files),
                "test_set_hash": _included_set_hash(tests),
                "dynamic_import_count": 0,
                "dynamic_import_names": [],
                "unresolved_count": 0,
                "unresolved_names": [],
                "diagnostics": [],
                "bytes_read": 0,
                "files_visited": len(included_files),
                "scope_source": "graph_db",
                "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
            }
        candidates, rejected = _candidate_python_files(root, checkpoint=checkpoint)
        diagnostics.extend(rejected)
    else:
        candidates = _sorted_display(candidate_files)

    included: dict[str, str] = { _identity_key(p): p for p in candidates }
    queue = list(included.values())
    while queue:
        if checkpoint is not None and checkpoint("discover"):
            raise InterruptedError("analysis_stop")
        if time.monotonic() - t0 > MAX_DISCOVERY_SECONDS:
            blocked_reason = "discovery_time_cap"
            break
        if files_seen >= MAX_DISCOVERY_FILES:
            blocked_reason = "discovery_file_cap"
            break
            blocked_reason = "discovery_file_cap"
            break
        rel = queue.pop(0)
        abs_p = os.path.join(root, rel.replace("/", os.sep))
        files_seen += 1
        physical, rerr = _resolve_physical(abs_p)
        if physical is None:
            diagnostics.append({"path": rel, "reason": rerr or "unresolved_target"})
            continue
        root_physical, _ = _resolve_physical(root)
        if root_physical and not _contained(physical, root_physical):
            diagnostics.append({"path": rel, "reason": "symlink_escape"})
            continue
        try:
            with open(physical, "rb") as fh:
                raw = fh.read()
        except OSError:
            diagnostics.append({"path": rel, "reason": "unresolved_target"})
            continue
        bytes_read += len(raw)
        if bytes_read > MAX_DISCOVERY_BYTES:
            blocked_reason = "discovery_byte_cap"
            break
        try:
            tree = ast.parse(raw.decode("utf-8", errors="replace"), filename=rel)
        except SyntaxError:
            diagnostics.append({"path": rel, "reason": "syntax_error"})
            continue
        dynamic.extend(_dynamic_import_names(tree))
        for module, level in _static_imports(tree):
            resolved = _resolve_import(rel, module, level, root)
            if not resolved:
                if module or level:
                    unresolved.append(module or f".{' .' * level}")
                continue
            for hit in resolved:
                if _is_excluded_production(hit):
                    continue
                key = _identity_key(hit)
                if key not in included:
                    included[key] = _norm_slash(hit)
                    queue.append(included[key])

    tests = _sorted_display(test_files or [])
    included_files = _sorted_display(included.values())
    status = "blocked" if blocked_reason else "ok"
    return {
        "status": status,
        "blocked_reason": blocked_reason,
        "included_files": included_files,
        "test_files": tests,
        "included_set_hash": _included_set_hash(included_files),
        "test_set_hash": _included_set_hash(tests),
        "dynamic_import_count": len(dynamic),
        "dynamic_import_names": sorted(set(dynamic)),
        "unresolved_count": len(unresolved),
        "unresolved_names": sorted(set(unresolved)),
        "diagnostics": diagnostics,
        "bytes_read": bytes_read,
        "files_visited": files_seen,
        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
    }


def _unavailable(reason: str, *, root: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "version": PAYLOAD_VERSION,
        "status": "unavailable",
        "reason": reason,
        "pass": False,
        "score": None,
        "included_files": [],
        "test_files": [],
        "strict_edges": [],
        "observed_edges": [],
        "counts": {},
        "metric_notes": [reason],
        "truncated": False,
        "reasons": [reason],
        "provenance": {
            "root": root,
            "included_set_hash": None,
            "fingerprint": None,
            "extractor_version": EXTRACTOR_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "confidence_policy": dict(_CONFIDENCE_POLICY),
            "timestamp_ms": int(time.time() * 1000),
            "schema_version": SCHEMA_VERSION,
        },
        "cache_key": None,
        "elapsed_ms": 0,
    }
    if extra:
        payload.update(extra)
    return payload


def _schema_ok(conn) -> bool:
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    return {"nodes", "edges", "files"}.issubset(names)


def _edge_rows(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT s.file_path AS src, t.file_path AS dst, e.confidence AS confidence "
        "FROM edges e "
        "JOIN nodes s ON s.id = e.source "
        "JOIN nodes t ON t.id = e.target "
        "WHERE e.kind = 'imports_module' AND e.is_external = 0 "
        "ORDER BY s.file_path, t.file_path, e.confidence DESC"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        src = _norm_slash(r["src"] if not isinstance(r, tuple) else r[0])
        dst = _norm_slash(r["dst"] if not isinstance(r, tuple) else r[1])
        conf = float(r["confidence"] if not isinstance(r, tuple) else r[2])
        out.append({"src": src, "dst": dst, "confidence": conf})
    return out


def _project_edges(
    rows: list[dict[str, Any]],
    included_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    dropped_self = 0
    dropped_dup = 0
    dropped_external = 0
    dropped_below = 0
    best: dict[tuple[str, str], float] = {}
    for row in rows:
        src, dst, conf = row["src"], row["dst"], row["confidence"]
        if not src or not dst:
            continue
        src_k, dst_k = _identity_key(src), _identity_key(dst)
        if src_k == dst_k:
            dropped_self += 1
            continue
        if src_k not in included_map or dst_k not in included_map:
            dropped_external += 1
            continue
        pair = (src_k, dst_k)
        if pair in best:
            dropped_dup += 1
            if conf > best[pair]:
                best[pair] = conf
        else:
            best[pair] = conf

    strict: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    for (src_k, dst_k), conf in sorted(best.items()):
        if conf < OBSERVED_MIN:
            dropped_below += 1
            continue
        item = {
            "src": included_map.get(src_k, src_k),
            "dst": included_map.get(dst_k, dst_k),
            "confidence": conf,
        }
        observed.append(item)
        if conf >= STRICT_MIN:
            strict.append(item)
    counts = {
        "pairs_raw": len(rows),
        "pairs_unique": len(best),
        "pairs_strict": len(strict),
        "pairs_observed": len(observed),
        "dropped_self": dropped_self,
        "dropped_duplicate": dropped_dup,
        "dropped_out_of_scope": dropped_external,
        "dropped_below_observed": dropped_below,
    }
    return strict, observed, counts


def _fingerprint(observed: list[dict[str, Any]], file_hashes: list[tuple[str, str]]) -> str:
    blob = {
        "rows": [
            [e["src"], e["dst"], round(float(e["confidence"]), 6)]
            for e in sorted(observed, key=lambda x: (x["src"].casefold(), x["dst"].casefold()))
        ],
        "file_hashes": [[p, h] for p, h in sorted(file_hashes, key=lambda x: x[0].casefold())],
    }
    raw = json.dumps(blob, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_key(identity: dict[str, Any]) -> str:
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_analysis_view(
    project_path: str,
    *,
    included_files: Iterable[str] | None = None,
    test_files: Iterable[str] | None = None,
    finalize_scope: bool = False,
    checkpoint: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Build the readonly analysis projection for one root."""
    t0 = time.monotonic()
    root = os.path.abspath(project_path)
    if checkpoint is not None and checkpoint("view_start"):
        raise InterruptedError("analysis_stop")
    scope_info: dict[str, Any] | None = None
    if included_files is None or finalize_scope:
        scope_info = finalize_included_scope_v1(
            root,
            candidate_files=included_files,
            test_files=test_files,
            checkpoint=checkpoint,
        )
        if scope_info["status"] == "blocked":
            payload = _unavailable(
                "scope_blocked:" + str(scope_info.get("blocked_reason") or "cap"),
                root=root,
                extra={"scope": scope_info},
            )
            payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
            return payload
        included = _sorted_display(scope_info["included_files"])
        tests = _sorted_display(scope_info["test_files"])
    else:
        included = _sorted_display(included_files)
        tests = _sorted_display(test_files or [])

    included_keys = {_identity_key(p): p for p in included}
    isolated = list(included)

    db_path = graph_db_path(root)
    parent = os.path.dirname(db_path)
    parent_existed = os.path.isdir(parent)
    db_existed = os.path.exists(db_path)

    if not db_existed:
        payload = _unavailable("freshness:not_indexed", root=root, extra={"freshness": "not_indexed"})
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        payload["created_missing_db"] = os.path.exists(db_path)
        payload["created_missing_parent"] = os.path.isdir(parent) and not parent_existed
        return payload

    try:
        conn = _store.get_db(root, readonly=True)
    except FileNotFoundError:
        payload = _unavailable("missing_db", root=root)
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        payload["created_missing_db"] = os.path.exists(db_path)
        payload["created_missing_parent"] = os.path.isdir(parent) and not parent_existed
        return payload
    except Exception as exc:
        payload = _unavailable(f"store:{type(exc).__name__}", root=root)
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return payload

    freshness = "unknown"
    status_error = None
    try:
        if not _schema_ok(conn):
            payload = _unavailable("missing_schema", root=root)
            payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
            return payload
        # quick_check, not integrity_check: this gate runs on EVERY view build
        # inside the 2s budget. At reference scale (77MB DB) integrity_check
        # costs ~485ms/call vs ~109ms for quick_check, and under daemon GIL
        # contention that delta alone pushed tiny-scope calls past the deadline
        # (unavailable:deadline at elapsed_ms 2000+ while a fresh process
        # answered the same query in ~950ms). quick_check detects every
        # structural corruption the corrupt-gate must reject (verified on a
        # page-scrambled copy: identical non-ok output); integrity_check only
        # adds index-content cross-checks, which init/migrate already cover.
        ic = conn.execute("PRAGMA quick_check;").fetchone()
        if ic and ic[0] != "ok":
            payload = _unavailable("corrupt", root=root, extra={"freshness": "corrupt"})
            payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
            return payload
        counts_row = _store.status_counts(conn)
        last_run = counts_row.get("last_run") or {}
        try:
            stale_n = _status._stale_count(conn, root)
        except Exception as exc:
            stale_n = None
            status_error = f"stale_count_failed: {type(exc).__name__}"
        finished = last_run.get("finished_at") if last_run else None
        if finished:
            if stale_n == 0:
                freshness = "fresh"
            elif stale_n is None:
                freshness = "unknown"
            else:
                freshness = "stale"
        elif last_run:
            freshness = "indexing"
        else:
            freshness = "not_indexed"
        if freshness in {"unknown", "stale", "corrupt", "error", "not_indexed"}:
            payload = _unavailable(
                f"freshness:{freshness}",
                root=root,
                extra={"freshness": freshness, "status_error": status_error},
            )
            payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
            payload["created_missing_db"] = False
            payload["created_missing_parent"] = False
            return payload
        raw_edges = _edge_rows(conn)
        file_hash_rows = conn.execute(
            "SELECT path, content_hash FROM files ORDER BY path"
        ).fetchall()
    finally:
        conn.close()

    display_included = list(included_keys.values())
    # remap edge paths to included display case
    remapped: list[dict[str, Any]] = []
    for row in raw_edges:
        src = included_keys.get(_identity_key(row["src"]), _norm_slash(row["src"]))
        dst = included_keys.get(_identity_key(row["dst"]), _norm_slash(row["dst"]))
        remapped.append({"src": src, "dst": dst, "confidence": row["confidence"]})

    strict, observed, counts = _project_edges(remapped, included_keys)
    connected: set[str] = set()
    for edge in observed:
        connected.add(_identity_key(edge["src"]))
        connected.add(_identity_key(edge["dst"]))
    isolated = [p for p in display_included if _identity_key(p) not in connected]
    counts["files"] = len(display_included)
    counts["isolated_files"] = len(isolated)
    counts["test_files"] = len(tests)

    hashes: list[tuple[str, str]] = []
    for r in file_hash_rows:
        path = _norm_slash(r["path"] if not isinstance(r, tuple) else r[0])
        digest = r["content_hash"] if not isinstance(r, tuple) else r[1]
        if _identity_key(path) in included_keys:
            hashes.append((included_keys[_identity_key(path)], str(digest or "")))
    for rel in display_included:
        if not any(_identity_key(rel) == _identity_key(p) for p, _ in hashes):
            digest = _file_sha256(os.path.join(root, rel.replace("/", os.sep))) or ""
            hashes.append((rel, digest))

    fp = _fingerprint(observed, hashes)
    included_hash = _included_set_hash(display_included)
    identity = {
        "root": _norm_slash(root),
        "included_set_hash": included_hash,
        "fingerprint": fp,
        "extractor_version": EXTRACTOR_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "confidence_policy": _CONFIDENCE_POLICY,
        "schema_version": SCHEMA_VERSION,
    }
    notes = [
        "confidence_is_not_probability",
        "no_resolver_inference_from_0.7",
        "isolated_files_remain_in_denominator",
    ]
    if scope_info and scope_info.get("unresolved_count"):
        notes.append("unresolved_imports_present")
    status = "ok"
    if scope_info and (scope_info.get("diagnostics") or scope_info.get("unresolved_count")):
        status = "partial"

    payload = {
        "version": PAYLOAD_VERSION,
        "status": status,
        "reason": None,
        "pass": False,
        "score": None,
        "included_files": display_included,
        "test_files": tests,
        "isolated_files": isolated,
        "strict_edges": strict,
        "observed_edges": observed,
        "counts": counts,
        "metric_notes": notes,
        "truncated": False,
        "reasons": [],
        "scope": scope_info,
        "freshness": freshness,
        "provenance": {
            "root": root,
            "included_set_hash": included_hash,
            "fingerprint": fp,
            "extractor_version": EXTRACTOR_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            "confidence_policy": dict(_CONFIDENCE_POLICY),
            "timestamp_ms": int(time.time() * 1000),
            "schema_version": SCHEMA_VERSION,
        },
        "cache_key": _cache_key(identity),
        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
        "created_missing_db": False,
        "created_missing_parent": False,
    }
    return payload


__all__ = [
    "ALGORITHM_VERSION",
    "PAYLOAD_VERSION",
    "STRICT_MIN",
    "OBSERVED_MIN",
    "build_analysis_view",
    "finalize_included_scope_v1",
]
