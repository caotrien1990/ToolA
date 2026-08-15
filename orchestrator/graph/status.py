"""Graph status tool: expose freshness, counts, and WAL state (Phase 1 Step 8).

Reads the graph DB and the files table to report staleness (files changed since
last index). Fail-open: if the DB is missing or corrupt, returns a disabled payload
rather than raising — callers must never crash on a status probe.
"""
import os
import sqlite3
import time

from .config import graph_enabled, CBM_USE_TREE_SITTER
from . import store as _store

EXTRACTOR_VERSION = "ast-p0-v1"


def _stale_count(conn: sqlite3.Connection, project_root: str) -> int:
    """Count indexed files whose on-disk mtime/content differs (best-effort)."""
    rows = conn.execute("SELECT path, content_hash FROM files;").fetchall()
    import hashlib
    stale = 0
    for r in rows:
        path = r["path"]
        abs_p = os.path.join(project_root, path)
        if not os.path.exists(abs_p):
            stale += 1
            continue
        try:
            with open(abs_p, "rb") as f:
                chash = hashlib.sha256(f.read()).hexdigest()
            if chash != r["content_hash"]:
                stale += 1
        except OSError:
            stale += 1
    return stale


def graph_status(project_path: str) -> dict:
    """Return a status payload for the project graph DB.

    Fields: enabled, db_present, integrity, nodes, edges, files, last_run,
    stale_files, extractor_version, tree_sitter_enabled, wal, freshness,
    elapsed_ms.
    Fail-open: any DB error returns enabled=False + error (no raise).
    """
    t0 = time.monotonic()
    payload = {
        "enabled": graph_enabled(),
        "db_present": False,
        "integrity": "unknown",
        "nodes": 0, "edges": 0, "files": 0,
        "last_run": None, "stale_files": None,
        "extractor_version": EXTRACTOR_VERSION,
        "tree_sitter_enabled": CBM_USE_TREE_SITTER,
        "wal": "unknown",
        "freshness": "disabled",
        "elapsed_ms": 0,
        "error": None,
    }
    if not graph_enabled():
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return payload

    db_path = _store.graph_db_path(project_path)
    payload["db_present"] = os.path.exists(db_path)
    if not payload["db_present"]:
        payload["freshness"] = "not_indexed"
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return payload

    try:
        conn = _store.get_db(project_path, readonly=True)
        try:
            ic = _store.integrity_check(project_path)
            payload["integrity"] = ic
            if ic != "ok":
                payload["freshness"] = "corrupt"
                payload["error"] = f"integrity_check: {ic}"
                return payload
            counts = _store.status_counts(conn)
            payload["nodes"] = counts["nodes"]
            payload["edges"] = counts["edges"]
            payload["files"] = counts["files"]
            payload["last_run"] = counts["last_run"]
            wal = conn.execute("PRAGMA journal_mode;").fetchone()
            payload["wal"] = wal[0] if wal else "unknown"
            # CAN-053: stale-file count FIRST — freshness derives from actual
            # drift. Incremental/hybrid reindex keeps the graph current without
            # touching last_run, so 0 stale files IS fresh regardless of age.
            try:
                payload["stale_files"] = _stale_count(conn, project_path)
            except Exception as e:
                payload["stale_files"] = None
                payload["error"] = f"stale_count_failed: {type(e).__name__}"
            try:
                from .config import CBM_EXTRACT_JS as _ejs
                payload["extract_js_enabled"] = bool(_ejs)
                if not _ejs:
                    payload["coverage_note"] = "python-only (set CBM_EXTRACT_JS=1 for JS/TS; HTML/CSS not indexed)"
            except Exception:
                pass
            if counts["last_run"]:
                fin = counts["last_run"]["finished_at"]
                if fin:
                    age_ms = int(time.time() * 1000) - fin
                    if payload.get("stale_files") == 0:
                        payload["freshness"] = "fresh"
                    else:
                        payload["freshness"] = "fresh" if age_ms < 3_600_000 else "stale"
                else:
                    payload["freshness"] = "indexing"  # run in progress, not stale
            else:
                payload["freshness"] = "not_indexed"
            return payload
        finally:
            conn.close()
    except Exception as e:
        payload["freshness"] = "error"
        payload["error"] = f"{type(e).__name__}: {e}"
        return payload
    finally:
        payload["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)


__all__ = ["graph_status", "EXTRACTOR_VERSION"]
