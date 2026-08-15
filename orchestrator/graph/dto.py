"""cbm.graph/v1 DTO — stable, version-pinned envelope for graph query results.

CD-P1 bridge (2026-08-11): ToolA owns the structural code graph. This module
freezes a canonical JSON shape that ToolQ adapters (and future P2/P3 consumers)
can rely on, without ever needing to re-parse AST or rebuild the graph.

Design contract:
  - The envelope is ADDITIVE: it wraps the raw query result, never destroys
    ToolA's own fields.
  - The version string "cbm.graph/v1" is the contract. Any breaking change
    requires a new version string.
  - The normalizer reads directly from the graph store (nodes/edges tables)
    and from the status payload, so it always reflects what ToolA actually
    produced — no re-derivation.
  - GRAPH_ENABLED=0 (default) returns the envelope with empty symbols/edges
    and a provenance marker, so callers can distinguish "no graph" from
    "graph disabled".

Edge endpoint convention (source_symbol / target_symbol):
  - When the endpoint node exists in the graph store, the DTO emits the
    node's qualified_name (e.g. "mod.alpha") — a stable semantic anchor.
  - When the endpoint is external (not present in the nodes table), the DTO
    emits the raw ID unchanged, which carries the "ext:" prefix assigned by
    the extractor (e.g. "ext:some_module").  This is the documented stable
    external form; consumers can detect it by the "ext:" prefix.
"""
import time

from .config import graph_enabled
from . import store as _store
from .status import graph_status, EXTRACTOR_VERSION

DTO_VERSION = "cbm.graph/v1"


def _safe_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _node_to_symbol(row: dict) -> dict:
    """Map a graph nodes-table row to the DTO symbol shape."""
    return {
        "name": row.get("name", ""),
        "kind": row.get("kind", ""),
        "file": row.get("file_path", ""),
        "line_start": _safe_int(row.get("start_line")),
        "line_end": _safe_int(row.get("end_line")),
        "qualified_name": row.get("qualified_name", ""),
    }


def _edge_to_dto(row: dict) -> dict:
    """Map a resolved edges-table row to the DTO edge shape.

    The SQL query in normalize_graph_dto already resolves source/target
    node IDs to qualified_name anchors via LEFT JOIN on the nodes table.
    Unresolvable (external) endpoints keep their raw "ext:" prefixed ID.
    """
    return {
        "source_symbol": row.get("source_symbol", ""),
        "target_symbol": row.get("target_symbol", ""),
        "kind": row.get("kind", ""),
        "file": row.get("file", ""),
    }


def normalize_graph_dto(project_path: str, *, include_disabled: bool = True) -> dict:
    """Build a cbm.graph/v1 envelope from the project's graph DB.

    Reads nodes + edges directly from the store and wraps them in the
    versioned envelope with provenance. Fail-open: on any DB error returns
    the envelope with empty lists and provenance.error set.

    Parameters:
        project_path: project root that holds .orchestrator_cache/graph.db.
        include_disabled: when False and GRAPH_ENABLED=0, return a minimal
            envelope immediately (skip DB open entirely).

    Returns:
        dict matching the cbm.graph/v1 contract.
    """
    t0 = time.monotonic()
    envelope = {
        "dto": DTO_VERSION,
        "generated_for": project_path,
        "symbols": [],
        "edges": [],
        "provenance": {
            "index_tool": "cbm_index",
            "commit_or_mtime": "",
            "extractor_version": EXTRACTOR_VERSION,
            "counts": {"symbols": 0, "edges": 0, "files": 0},
            "graph_enabled": graph_enabled(),
            "elapsed_ms": 0,
            "error": None,
        },
    }

    if not graph_enabled() and not include_disabled:
        envelope["provenance"]["elapsed_ms"] = round(
            (time.monotonic() - t0) * 1000, 1)
        return envelope

    if not graph_enabled():
        # Graph disabled: return empty envelope with provenance marker.
        envelope["provenance"]["error"] = "GRAPH_ENABLED=0"
        envelope["provenance"]["elapsed_ms"] = round(
            (time.monotonic() - t0) * 1000, 1)
        return envelope

    try:
        conn = _store.get_db(project_path, readonly=True)
        try:
            # Symbols: all non-external nodes.
            rows = conn.execute(
                "SELECT name, kind, file_path, start_line, end_line, "
                "qualified_name FROM nodes WHERE kind != 'external' "
                "ORDER BY file_path, start_line;"
            ).fetchall()
            envelope["symbols"] = [_node_to_symbol(dict(r)) for r in rows]

            # Edges: resolve each endpoint's qualified_name via the nodes
            # table so the DTO emits semantic anchors, not raw internal IDs.
            # External targets (not in nodes) keep their raw "ext:" prefixed ID.
            edge_rows = conn.execute(
                "SELECT "
                "COALESCE(s.qualified_name, e.source) AS source_symbol, "
                "COALESCE(t.qualified_name, e.target) AS target_symbol, "
                "e.kind, "
                "COALESCE(s.file_path, '') AS file "
                "FROM edges e "
                "LEFT JOIN nodes s ON s.id = e.source "
                "LEFT JOIN nodes t ON t.id = e.target "
                "ORDER BY source_symbol, target_symbol;"
            ).fetchall()
            envelope["edges"] = [_edge_to_dto(dict(r)) for r in edge_rows]

            # Provenance: pull from status + last_run.
            st = graph_status(project_path)
            last_run = st.get("last_run") or {}
            envelope["provenance"]["counts"] = {
                "symbols": len(envelope["symbols"]),
                "edges": len(envelope["edges"]),
                "files": st.get("files", 0),
            }
            finished = last_run.get("finished_at")
            if finished:
                envelope["provenance"]["commit_or_mtime"] = str(finished)
        finally:
            conn.close()
    except Exception as exc:
        envelope["provenance"]["error"] = f"{type(exc).__name__}: {exc}"

    envelope["provenance"]["elapsed_ms"] = round(
        (time.monotonic() - t0) * 1000, 1)
    return envelope


__all__ = ["DTO_VERSION", "normalize_graph_dto"]
