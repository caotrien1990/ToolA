"""Graph SQLite schema DDL and initializer (Phase 1 Step 4).

Tables: nodes, edges, files, index_runs.
FTS5:  graph_nodes_fts, graph_symbols_fts (external-content, kept in sync via triggers).
Guarantees: WAL on, foreign_keys on, integrity_check pass, no collision with [Q] rag_chunks_fts.

Design references:
- CG research schema (research/CBM_CG/CG/src/db/schema.sql): external-content FTS5 + trigger sync.
- Plan Step 4 (A_Q_devineupgrade_breakdown_plan_2nd.md line 56-72).
- A3m: [Q] tokenizer is `unicode61 remove_diacritics 2`; graph FTS5 uses the same tokenizer
  for diacritic-insensitive symbol search (Vietnamese-friendly).
- A8: CBM confidence stored as REAL numeric on edges (EXACT=1.0, UNIQUE=0.9,
  SAME_MODULE=0.7, SUFFIX=0.3-0.7). Display labels are category-only, not stored.
"""
import sqlite3

from .config import CBM_EXACT, CBM_UNIQUE, CBM_SAME_MODULE, CBM_SUFFIX_LOW

SCHEMA_VERSION = 1

# ─── Core table DDL ──────────────────────────────────────────────────────────

_DDL_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,           -- function | class | method | variable | module | import | name_literal | external
    name            TEXT NOT NULL,           -- short symbol name
    qualified_name  TEXT NOT NULL,           -- module.path.name
    file_path       TEXT NOT NULL,
    language        TEXT NOT NULL,           -- python | javascript | ...
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    start_column    INTEGER DEFAULT 0,
    end_column      INTEGER DEFAULT 0,
    docstring       TEXT,
    signature       TEXT,
    visibility      TEXT,                    -- public | private | protected
    is_exported     INTEGER DEFAULT 0,
    is_async        INTEGER DEFAULT 0,
    is_static       INTEGER DEFAULT 0,
    is_abstract     INTEGER DEFAULT 0,
    decorators      TEXT,                    -- JSON array
    updated_at      INTEGER NOT NULL
);
"""

_DDL_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,           -- nodes.id
    target          TEXT NOT NULL,           -- nodes.id (or external ref id)
    kind            TEXT NOT NULL,           -- calls | imports | imports_module | defines | inherits | references | dynamic_ref
    confidence      REAL NOT NULL,           -- CBM numeric (A8): EXACT=1.0, UNIQUE=0.9, SAME_MODULE=0.7, SUFFIX=0.3-0.7
    cbm_label       TEXT NOT NULL,           -- display category: static | inferred | dynamic | unknown
    metadata        TEXT,                    -- JSON object (extra context)
    line            INTEGER,                 -- source line where edge originates
    col             INTEGER,
    is_external     INTEGER DEFAULT 0,       -- 1 if target is unresolved/external
    FOREIGN KEY (source) REFERENCES nodes(id) ON DELETE CASCADE
);
"""

_DDL_FILES = """
CREATE TABLE IF NOT EXISTS files (
    path            TEXT PRIMARY KEY,
    content_hash    TEXT NOT NULL,           -- sha256 of file content (incremental index key)
    language        TEXT NOT NULL,
    size            INTEGER NOT NULL,
    modified_at     INTEGER NOT NULL,
    indexed_at      INTEGER NOT NULL,
    node_count      INTEGER DEFAULT 0,
    errors          TEXT,                    -- JSON array of extraction errors
    extractor_version TEXT                   -- config.EXTRACTOR_VERSION at index time
);
"""

_DDL_INDEX_RUNS = """
CREATE TABLE IF NOT EXISTS index_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      INTEGER NOT NULL,
    finished_at     INTEGER,
    scope           TEXT NOT NULL,           -- core | full
    status          TEXT NOT NULL,           -- running | completed | failed | partial
    node_count      INTEGER DEFAULT 0,
    edge_count      INTEGER DEFAULT 0,
    file_count      INTEGER DEFAULT 0,
    duration_s      REAL,
    error           TEXT
);
"""

# ─── FTS5 external-content tables (A3m tokenizer) ───────────────────────────
# graph_nodes_fts: full-text over node name + qualified_name + docstring + signature.
# graph_symbols_fts: lightweight symbol-name index for fast find_symbol lookup.
# Neither collides with [Q] rag_chunks_fts (separate DB file).

_DDL_GRAPH_NODES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS graph_nodes_fts USING fts5(
    id UNINDEXED,
    name,
    qualified_name,
    docstring,
    signature,
    content='nodes',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
"""

_DDL_GRAPH_SYMBOLS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS graph_symbols_fts USING fts5(
    id UNINDEXED,
    name,
    qualified_name,
    content='nodes',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
"""

# ─── Triggers: keep FTS5 in sync with nodes ─────────────────────────────────

_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS graph_nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO graph_nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);
    INSERT INTO graph_symbols_fts(rowid, id, name, qualified_name)
    VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name);
END;

CREATE TRIGGER IF NOT EXISTS graph_nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO graph_nodes_fts(graph_nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);
    INSERT INTO graph_symbols_fts(graph_symbols_fts, rowid, id, name, qualified_name)
    VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name);
END;

CREATE TRIGGER IF NOT EXISTS graph_nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO graph_nodes_fts(graph_nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);
    INSERT INTO graph_nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);
    INSERT INTO graph_symbols_fts(graph_symbols_fts, rowid, id, name, qualified_name)
    VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name);
    INSERT INTO graph_symbols_fts(rowid, id, name, qualified_name)
    VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name);
END;
"""

# ─── Indexes (composite covers narrow scans, per CG pattern) ────────────────

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_file_path ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);
CREATE INDEX IF NOT EXISTS idx_nodes_file_line ON nodes(file_path, start_line);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target, kind);
CREATE INDEX IF NOT EXISTS idx_edges_confidence ON edges(confidence);
CREATE INDEX IF NOT EXISTS idx_files_language ON files(language);
CREATE INDEX IF NOT EXISTS idx_files_indexed_at ON files(indexed_at);
CREATE INDEX IF NOT EXISTS idx_index_runs_status ON index_runs(status);
"""

_ALL_DDL = [_DDL_NODES, _DDL_EDGES, _DDL_FILES, _DDL_INDEX_RUNS,
            _DDL_GRAPH_NODES_FTS, _DDL_GRAPH_SYMBOLS_FTS, _TRIGGERS, _INDEXES]


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Additive column migrations for DBs created before the current schema.

    `CREATE TABLE IF NOT EXISTS` is a no-op on an existing table, so a new
    column in the DDL never reaches a live DB -- and the live [A] graph.db is
    44 MB, far past the point where dropping it is acceptable. Same additive
    ALTER pattern as orchestrator/session/store.py and server/audit_writer.py.
    Guarded by PRAGMA table_info rather than a try/except so an unrelated
    OperationalError is not swallowed.

    Public because store.get_db must call it on every writable open of an
    ALREADY-initialised db, where init_schema never runs. Returns the columns
    it added (empty when the db was already current) so callers can log.
    """
    applied = []
    have = {r[1] for r in conn.execute("PRAGMA table_info(files);")}
    if have and "extractor_version" not in have:
        conn.execute("ALTER TABLE files ADD COLUMN extractor_version TEXT;")
        applied.append("files.extractor_version")
    if applied:
        conn.commit()
    return applied


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all graph tables, FTS5, triggers, and indexes on the given connection.
    Idempotent (IF NOT EXISTS + additive ALTERs). Caller is responsible for
    PRAGMA WAL + foreign_keys.
    """
    conn.execute("PRAGMA foreign_keys = ON;")
    cur = conn.cursor()
    for ddl in _ALL_DDL:
        cur.executescript(ddl)
    migrate(conn)
    # Record schema version (lightweight, mirrors CG schema_versions pattern).
    cur.execute(
        "CREATE TABLE IF NOT EXISTS schema_versions ("
        "version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL, description TEXT);"
    )
    cur.execute(
        "INSERT OR IGNORE INTO schema_versions (version, applied_at, description) "
        "VALUES (?, strftime('%s','now') * 1000, ?);",
        (SCHEMA_VERSION, "Initial graph schema (nodes/edges/files/index_runs + graph FTS5)"),
    )
    conn.commit()


def integrity_check(conn: sqlite3.Connection) -> str:
    """Run PRAGMA integrity_check and return the result string."""
    row = conn.execute("PRAGMA integrity_check;").fetchone()
    return row[0] if row else "unknown"


__all__ = ["SCHEMA_VERSION", "init_schema", "migrate", "integrity_check",
           "CBM_EXACT", "CBM_UNIQUE", "CBM_SAME_MODULE", "CBM_SUFFIX_LOW"]
