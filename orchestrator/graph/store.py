"""Graph SQLite store: connection lifecycle + insert/query helpers (Phase 1 Step 5).

Wraps schema.init_schema and provides CRUD used by the indexer (Step 7) and
bounded traversal (Step 9). All writes go through atomic transactions.
"""
import json
import os
import sqlite3
import time
from typing import Any, Iterable

from .config import graph_db_path, EXTRACTOR_VERSION
from . import schema as _schema


def get_db(project_path: str, *, readonly: bool = False) -> sqlite3.Connection:
    """Open (and lazily create) the graph DB for a project.
    Pragmas: WAL, foreign_keys=ON, busy_timeout=15s. Creates parent dir + schema if missing.
    """
    path = graph_db_path(project_path)
    parent = os.path.dirname(path)
    if readonly:
        # CDP1-R02: a readonly open MUST be side-effect-free -- never create the
        # parent dir or the DB. A missing DB in readonly mode raises an actionable
        # error instead of the bare "unable to open database file" (the usual
        # cause is a project_path that is not the indexed root). Non-readonly
        # callers (indexer, init_db, reset_db) keep the lazy-create behavior.
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"graph.db not found at {path} — pass the project root that holds "
                f".orchestrator_cache/graph.db, or run generate_project_map / cbm "
                f"incremental index to build it")
    elif parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    uri = f"file:{path.replace(os.sep, '/')}?mode=ro" if readonly else f"file:{path.replace(os.sep, '/')}"
    conn = sqlite3.connect(uri, uri=True, timeout=15.0)
    conn.row_factory = sqlite3.Row
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=15000;")
        # Lazy schema init on first open.
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes' LIMIT 1;",
        ).fetchone()
        if not exists:
            _schema.init_schema(conn)
        else:
            # An EXISTING db never re-runs init_schema, so a column added to the
            # DDL would never reach it. Additive migrations are cheap (one
            # PRAGMA table_info) and must run on every writable open, or the
            # live 44 MB graph.db stays one schema behind the code forever.
            _schema.migrate(conn)
    return conn


def init_db(project_path: str) -> str:
    """Ensure schema exists; return integrity_check result."""
    conn = get_db(project_path)
    try:
        return _schema.integrity_check(conn)
    finally:
        conn.close()


def integrity_check(project_path: str) -> str:
    """Run integrity_check on the project graph DB (read-only)."""
    conn = get_db(project_path, readonly=True)
    try:
        return _schema.integrity_check(conn)
    finally:
        conn.close()


def reset_db(project_path: str) -> None:
    """Drop all graph tables and recreate schema (for re-index / rollback)."""
    conn = get_db(project_path)
    try:
        conn.executescript(
            "DROP TABLE IF EXISTS graph_nodes_fts;"
            "DROP TABLE IF EXISTS graph_symbols_fts;"
            "DROP TABLE IF EXISTS edges;"
            "DROP TABLE IF EXISTS nodes;"
            "DROP TABLE IF EXISTS files;"
            "DROP TABLE IF EXISTS index_runs;"
            "DROP TABLE IF EXISTS schema_versions;",
        )
        conn.commit()
        _schema.init_schema(conn)
    finally:
        conn.close()


# ─── File tracking ───────────────────────────────────────────────────────────

def upsert_file(conn: sqlite3.Connection, *, path: str, content_hash: str,
                language: str, size: int, modified_at: int, node_count: int = 0,
                errors: list | None = None) -> None:
    conn.execute(
        "INSERT INTO files (path, content_hash, language, size, modified_at, indexed_at, "
        "node_count, errors, extractor_version) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "content_hash=excluded.content_hash, language=excluded.language, size=excluded.size, "
        "modified_at=excluded.modified_at, indexed_at=excluded.indexed_at, "
        "node_count=excluded.node_count, errors=excluded.errors, "
        "extractor_version=excluded.extractor_version;",
        (path, content_hash, language, size, modified_at, int(time.time() * 1000),
         node_count, json.dumps(errors or [], ensure_ascii=False), EXTRACTOR_VERSION),
    )


def stale_files(conn: sqlite3.Connection, candidates: Iterable[dict]) -> list[dict]:
    """Given [{path, content_hash}], return those needing re-extraction.

    A file is stale when its content changed, when it was never indexed, OR
    when it was indexed by a DIFFERENT extractor version. That last clause is
    the fix for a real miss: `content_hash` answers "did the source change",
    which says nothing about the same source now extracting into a new node or
    edge kind. Shipping the W5 extractor left 241 of 250 [A] files frozen at
    the pre-`name_literal` shape through a *full* reindex, because every hash
    still matched. Bumping config.EXTRACTOR_VERSION now forces the re-extract.

    One query instead of one per file: this runs over every candidate on every
    index, and the per-file `file_hash()` round trip was the hot path.
    """
    known = {
        r["path"]: (r["content_hash"], r["extractor_version"])
        for r in conn.execute(
            "SELECT path, content_hash, extractor_version FROM files;")
    }
    stale = []
    for c in candidates:
        prev = known.get(c["path"])
        if prev is None or prev[0] != c["content_hash"] or prev[1] != EXTRACTOR_VERSION:
            stale.append(c)
    return stale


# ─── Node / edge insert (batch, within one txn) ──────────────────────────────

def insert_nodes(conn: sqlite3.Connection, nodes: list[dict]) -> int:
    if not nodes:
        return 0
    now = int(time.time() * 1000)
    rows = []
    for n in nodes:
        rows.append((
            n["id"], n["kind"], n["name"], n["qualified_name"], n["file_path"],
            n["language"], n["start_line"], n["end_line"],
            n.get("start_column", 0), n.get("end_column", 0),
            n.get("docstring"), n.get("signature"), n.get("visibility"),
            int(n.get("is_exported", 0)), int(n.get("is_async", 0)),
            int(n.get("is_static", 0)), int(n.get("is_abstract", 0)),
            json.dumps(n.get("decorators", []), ensure_ascii=False), now,
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO nodes "
        "(id, kind, name, qualified_name, file_path, language, start_line, end_line, "
        "start_column, end_column, docstring, signature, visibility, is_exported, is_async, "
        "is_static, is_abstract, decorators, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);",
        rows,
    )
    return len(rows)



# ─── Edge-quality filter (ALGO UPGRADE B): builtin-method noise + ext placeholders ──
# ext: call targets whose suffix matches a Python builtin / common method carry no
# cross-symbol signal (report 2.4: ext:get/str/push/append/join dominate the 0.3
# bucket). Dropping at this single edge-persistence chokepoint keeps both indexer
# paths clean without touching extractors.
#
# KNOWN LIMITATION, measured 2026-08-20 -- the note that used to stand here said
# "ext:get == the builtin get, never an indexed user def". That is not true. This
# project defines 13 of these names itself (add, append, close, format, get, join,
# open, read, replace, set, sum, update, write), and a call to one of them is
# dropped here before any resolver sees it. Three real call sites are lost that
# way, all `<singleton>.close()`-shaped, so `FairAdmissionManager.close`,
# `PostCallAccountingQueue.close` and `TaskSchedulerAdapter.end` read as dead
# despite having live callers.
#
# The filter stays as it is, because every cheaper rule was measured and is
# worse than the disease:
#   * exempting all 13 names keeps 8165 attribute calls, of which only 285
#     resolve at all, and spot-checking those shows them binding `f.read()` and
#     `resp.read()` to `_InProcessToolRequest.read` -- wrong edges, which are
#     worse than missing ones because they make dead code look alive forever;
#   * deferring the drop until after _resolve_imported_calls adds exactly those
#     285 wrong edges, for the same reason.
# The only precise rule -- resolve the receiver when it is a module-level
# singleton of a project class -- is right (32 call sites, 0 ambiguous) but
# needs a cross-file pass that this per-file chokepoint cannot host, and would
# recover 3 entries. Left as a documented false-positive instead of a fragile
# pass; the three symbols above are known-live and must not be deleted.
BUILTIN_NOISE: frozenset[str] = frozenset({
    "get", "str", "push", "append", "join", "split", "strip", "items", "keys",
    "values", "pop", "len", "print", "format", "startswith", "endswith", "lower",
    "upper", "replace", "extend", "update", "add", "sort", "int", "float", "bool",
    "list", "dict", "set", "tuple", "isinstance", "hasattr", "getattr", "setattr",
    "repr", "type", "next", "iter", "map", "filter", "zip", "range", "enumerate",
    "sorted", "min", "max", "sum", "abs", "round", "open", "read", "write", "close",
})

EXTERNAL_KIND = "external"
_EXTERNAL_FILE = "<external>"
NAME_LITERAL_KIND = "name_literal"

# Node kinds that exist only as JOIN anchors for the graph and must never be
# returned by a symbol lookup: `external` is an unresolved call target, and
# `name_literal` (W5) is a string that happens to spell an identifier. Both
# carry a `name` column, so without this filter both would shadow real defs in
# find_symbol / FTS results and inflate the project-map DTO.
NON_SYMBOL_KINDS: tuple[str, ...] = (EXTERNAL_KIND, NAME_LITERAL_KIND)
# Bind placeholders for the tuple above, so adding a kind widens every
# symbol-lookup filter instead of silently leaking the new kind through a
# hardcoded "(?,?)".
_NON_SYMBOL_SQL = ",".join("?" * len(NON_SYMBOL_KINDS))

# A name lookup must return the DEFINITION first. `helper` can name a function
# in util.py AND the `from util import helper` alias node in core.py -- both are
# real, searchable symbols (unlike NON_SYMBOL_KINDS, which are filtered out
# entirely), and both carry name='helper'. Without an ORDER BY, `LIMIT 1` picked
# whichever row sqlite reached first, i.e. rowid order, i.e. indexing order.
#
# That is not a cosmetic ordering preference. `query._resolve_node` asks for
# limit=1 and uses whatever comes back as THE node for callers/callees/impact:
# resolving to an alias returns an empty caller list for a function with call
# sites, which reads as "nothing calls this". Measured on [A]'s own graph.db:
# 584 names have both a definition and a binding-site row, and 370 of them --
# nearly two in three -- resolved to the non-definition.
#
# The rank is an ordering, not a filter: an alias-only name (a re-export this
# project never defines) still resolves, it just sorts after any real
# definition. file_path/start_line break ties so the answer is deterministic
# across reindexes rather than dependent on which file happened to be walked
# first.
def _symbol_rank_sql(alias: str = "") -> str:
    """ORDER BY fragment ranking definitions first. `alias` prefixes the columns
    for the FTS join, where `nodes` is joined as `n`."""
    a = f"{alias}." if alias else ""
    return (f"CASE {a}kind WHEN 'function' THEN 0 WHEN 'method' THEN 0 "
            f"WHEN 'class' THEN 0 WHEN 'variable' THEN 1 WHEN 'module' THEN 2 "
            f"ELSE 3 END, {a}file_path, {a}start_line")


_SYMBOL_RANK_SQL = _symbol_rank_sql()
_SYMBOL_RANK_SQL_N = _symbol_rank_sql("n")


def _ext_target_name(target: str) -> "str | None":
    """Return the bare name for an `ext:<name>` target, else None."""
    if target and target.startswith("ext:"):
        return target[4:]
    return None


def _ensure_external_nodes(conn: sqlite3.Connection, targets: set[str]) -> int:
    """Idempotently create placeholder `kind='external'` node rows for ext targets
    so JOIN-based resolvers (lsp_resolver._load_candidates) and the orphaned-join
    metric can resolve them (report 2.3: all ext edges dropped because node ids
    missing). INSERT OR IGNORE keeps this safe across per-file reindexes — the
    placeholder has file_path='<external>' so delete_nodes_for_file never drops it.
    """
    if not targets:
        return 0
    now = int(time.time() * 1000)
    rows = []
    for tgt in targets:
        name = _ext_target_name(tgt) or tgt
        rows.append((
            tgt, EXTERNAL_KIND, name, tgt, _EXTERNAL_FILE, "unknown",
            0, 0, 0, 0, None, None, None, 0, 0, 0, 0, "[]", now,
        ))
    conn.executemany(
        "INSERT OR IGNORE INTO nodes "
        "(id, kind, name, qualified_name, file_path, language, start_line, end_line, "
        "start_column, end_column, docstring, signature, visibility, is_exported, is_async, "
        "is_static, is_abstract, decorators, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);",
        rows,
    )
    return len(rows)


def insert_edges(conn: sqlite3.Connection, edges: list[dict]) -> int:
    """Persist edges with ALGO UPGRADE B edge-quality gating:

    1. BUILTIN_NOISE: `calls` edges whose target is `ext:<builtin>` are dropped —
       unresolved builtin/method calls with no cross-symbol signal.
    2. ext placeholder: every surviving `ext:<name>` target gets a placeholder
       `kind='external'` node (idempotent) so JOIN resolvers stop dropping them
       (orphaned-join -> 0).
    3. self-loops: `source==target` edges are KEPT (241/241 are genuine in-body
       same-name calls — verified; recursion signal must not be lost) but tagged
       `metadata.self_loop=true` so the data-quality smell is queryable.

    Returns the number of edges actually inserted (post-filter) so callers report
    honest `edges_new` counts.
    """
    if not edges:
        return 0
    kept: list[tuple] = []
    ext_targets: set[str] = set()
    for e in edges:
        target = e["target"]
        kind = e["kind"]
        # B1: drop builtin-method noise on unresolved external calls.
        if kind == "calls":
            ename = _ext_target_name(target)
            if ename is not None and ename in BUILTIN_NOISE:
                continue
        # B2: tag self-loops (kept — genuine recursion; evidence in RES report).
        meta = e.get("metadata") or {}
        if e["source"] == target:
            if not isinstance(meta, dict):
                meta = {"_raw": meta}
            meta = dict(meta)
            meta["self_loop"] = True
        # collect surviving ext targets for placeholder node creation.
        if _ext_target_name(target) is not None:
            ext_targets.add(target)
        kept.append((
            e["source"], target, kind, float(e["confidence"]),
            e["cbm_label"], json.dumps(meta, ensure_ascii=False),
            e.get("line"), e.get("col"), int(e.get("is_external", 0)),
        ))
    _ensure_external_nodes(conn, ext_targets)
    if kept:
        conn.executemany(
            "INSERT INTO edges (source, target, kind, confidence, cbm_label, metadata, line, col, is_external) "
            "VALUES (?,?,?,?,?,?,?,?,?);",
            kept,
        )
    return len(kept)


def delete_nodes_for_file(conn: sqlite3.Connection, file_path: str) -> int:
    """Remove nodes (and cascade edges via FK) for a file before re-inserting."""
    cur = conn.execute("DELETE FROM nodes WHERE file_path=?", (file_path,))
    return cur.rowcount


# ─── Queries (bounded traversal helpers, Step 9) ─────────────────────────────


def find_nodes_by_name(conn: sqlite3.Connection, name: str, limit: int = 50) -> list[dict]:
    # NON_SYMBOL_KINDS: ext placeholders and W5 name_literal rows are join
    # targets, not searchable symbols - must not shadow real defs in
    # graph_find_symbol name lookups.
    rows = conn.execute(
        f"SELECT * FROM nodes WHERE name=? AND kind NOT IN ({_NON_SYMBOL_SQL}) "
        f"ORDER BY {_SYMBOL_RANK_SQL} LIMIT ?;",
        (name, *NON_SYMBOL_KINDS, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def fts_search_symbols(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[dict]:
    """FTS5 MATCH over graph_symbols_fts. Returns matching nodes (excludes the
    NON_SYMBOL_KINDS placeholder rows so they never shadow real symbols).

    Ordered by _SYMBOL_RANK_SQL for the same reason find_nodes_by_name is: this
    is the fallback path of the SAME lookup, and a caller that takes the first
    result must not get a definition from one path and an alias from the other.
    """
    rows = conn.execute(
        "SELECT n.* FROM graph_symbols_fts f JOIN nodes n ON n.rowid = f.rowid "
        f"WHERE graph_symbols_fts MATCH ? AND n.kind NOT IN ({_NON_SYMBOL_SQL}) "
        f"ORDER BY {_SYMBOL_RANK_SQL_N} LIMIT ?;",
        (query, *NON_SYMBOL_KINDS, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def neighbors(conn: sqlite3.Connection, node_id: str, *, direction: str = "out",
              kinds: tuple[str, ...] | None = None, limit: int = 50) -> list[dict]:
    """Return edges adjacent to node_id. direction = out | in | both."""
    if direction not in ("out", "in", "both"):
        raise ValueError("direction must be out|in|both")
    conds = []
    params: list[Any] = []
    if direction in ("out", "both"):
        conds.append("source = ?")
        params.append(node_id)
    if direction in ("in", "both"):
        conds.append("target = ?")
        params.append(node_id)
    where = " OR ".join(conds)
    if kinds:
        ph = ",".join("?" for _ in kinds)
        where = f"({where}) AND kind IN ({ph})"
        params.extend(kinds)
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM edges WHERE {where} LIMIT ?;", params,
    ).fetchall()
    return [dict(r) for r in rows]


def start_index_run(conn: sqlite3.Connection, scope: str) -> int:
    now = int(time.time() * 1000)
    cur = conn.execute(
        "INSERT INTO index_runs (started_at, scope, status) VALUES (?, ?, 'running');",
        (now, scope),
    )
    return cur.lastrowid


def finish_index_run(conn: sqlite3.Connection, run_id: int, *, status: str,
                     node_count: int, edge_count: int, file_count: int,
                     duration_s: float, error: str | None = None) -> None:
    conn.execute(
        "UPDATE index_runs SET finished_at=?, status=?, node_count=?, edge_count=?, "
        "file_count=?, duration_s=?, error=? WHERE id=?;",
        (int(time.time() * 1000), status, node_count, edge_count, file_count,
         duration_s, error, run_id),
    )


def status_counts(conn: sqlite3.Connection) -> dict:
    n = conn.execute("SELECT COUNT(*) FROM nodes;").fetchone()[0]
    e = conn.execute("SELECT COUNT(*) FROM edges;").fetchone()[0]
    f = conn.execute("SELECT COUNT(*) FROM files;").fetchone()[0]
    last = conn.execute(
        "SELECT id, started_at, finished_at, scope, status, node_count, edge_count, "
        "file_count, duration_s FROM index_runs ORDER BY id DESC LIMIT 1;",
    ).fetchone()
    return {
        "nodes": n, "edges": e, "files": f,
        "last_run": dict(last) if last else None,
    }


__all__ = [
    "get_db", "init_db", "integrity_check", "reset_db",
    "upsert_file", "stale_files",
    "insert_nodes", "insert_edges", "delete_nodes_for_file",
    "find_nodes_by_name", "fts_search_symbols", "neighbors",
    "start_index_run", "finish_index_run", "status_counts",
]
