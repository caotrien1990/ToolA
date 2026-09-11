"""Graph feature-flag and bounded-context config constants.

All graph code paths must consult these named constants so the graph layer can be
disabled atomically and all bounded-context limits revert to safe defaults.
Phase 0 Step 2 - A_Q_devineupgrade_breakdown_plan_2nd.
"""
import os

# --- Master switch ---
# GRAPH_ENABLED=false keeps every existing [A] search flow unchanged.
# A13 fix (2026-08-16): read the env LIVE, never freeze a bool at import.
# Freezing broke test isolation — whichever module imported config first
# (e.g. the GRAPH_ENABLED=0 canary) pinned the flag process-wide, so later
# modules that set GRAPH_ENABLED=1 before import silently ran disabled.


def graph_enabled() -> bool:
    """Return the live GRAPH_ENABLED flag (re-read from the environment).

    Callers MUST call this (or read the module attribute, which PEP 562 routes
    here) at request time rather than snapshotting a bool at import time.
    """
    return os.environ.get("GRAPH_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")

# --- Per-tool graph participation ---
# A tool opts out even when GRAPH_ENABLED is true via GRAPH_USE_<TOOL>=0.
_DEFAULT_USE_GRAPH = True


def use_graph_for(tool_name: str) -> bool:
    """Return whether a tool may use graph context."""
    if not graph_enabled():
        return False
    env_key = "GRAPH_USE_" + tool_name.upper()
    val = os.environ.get(env_key, "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    if val in ("1", "true", "yes", "on"):
        return True
    return _DEFAULT_USE_GRAPH


# --- Bounded-context hard limits (U4 conflict resolution) ---
# No magic numbers inside core implementation - import these constants only.
GRAPH_CONTEXT_MAX_FILES = int(os.environ.get("GRAPH_CONTEXT_MAX_FILES", "50"))
GRAPH_CONTEXT_MAX_SYMBOLS = int(os.environ.get("GRAPH_CONTEXT_MAX_SYMBOLS", "200"))
GRAPH_CONTEXT_TIMEOUT_S = float(os.environ.get("GRAPH_CONTEXT_TIMEOUT_S", "2.0"))
# P2-S2 analysis_service bounds (Addendum B + L1). Latency contract is
# deadline + join grace (2000+250=2250ms), not a 2s hard wall.
ANALYSIS_GRAPH_DEADLINE_MS = int(os.environ.get("ANALYSIS_GRAPH_DEADLINE_MS", "2000"))
ANALYSIS_EVOLUTION_DEADLINE_MS = int(os.environ.get("ANALYSIS_EVOLUTION_DEADLINE_MS", "5000"))
ANALYSIS_JOIN_GRACE_MS = int(os.environ.get("ANALYSIS_JOIN_GRACE_MS", "250"))
ANALYSIS_MAX_ACTIVE_WORKERS = int(os.environ.get("ANALYSIS_MAX_ACTIVE_WORKERS", "2"))
ANALYSIS_CHECK_EVERY_UNITS = int(os.environ.get("ANALYSIS_CHECK_EVERY_UNITS", "64"))
ANALYSIS_RESPONSE_MAX_BYTES = int(os.environ.get("ANALYSIS_RESPONSE_MAX_BYTES", "65536"))
ANALYSIS_TOP_LIST_MAX = int(os.environ.get("ANALYSIS_TOP_LIST_MAX", "50"))
ANALYSIS_QUEUE_ON_FULL = False
COUNCIL_CONTEXT_MAX_FILES = int(os.environ.get("COUNCIL_CONTEXT_MAX_FILES", "10"))
COUNCIL_CONTEXT_MAX_SYMBOLS = int(os.environ.get("COUNCIL_CONTEXT_MAX_SYMBOLS", "50"))
# Hybrid auto-reindex guard: skip silent reindex when the stale set is larger
# than this (budget protection); callers must use cbm_incremental_index instead.
# STT-25: default 50; env override optional: `setx CBM_AUTO_REINDEX_MAX_STALE 100`
CBM_AUTO_REINDEX_MAX_STALE = int(os.environ.get("CBM_AUTO_REINDEX_MAX_STALE", "50"))

# --- Index budget (project-size-aware, P0 gate) ---
INDEX_TIME_BUDGET_CORE_S = 45
INDEX_TIME_BUDGET_FULL_S = 150
INDEX_MIN_NODES = 500
INDEX_MIN_EDGES = 1000

# --- CBM confidence numeric scale (A8 resolved 2026-07-04) ---
# Stored value is numeric; display labels are category-only. Do not store literals.
CBM_EXACT = 1.0        # unambiguous definition/declaration
CBM_UNIQUE = 0.9       # one definition in scope, inferred binding
CBM_SAME_MODULE = 0.7  # same-module heuristic binding
CBM_SUFFIX_LOW = 0.3   # suffix/weak match lower bound
CBM_SUFFIX_HIGH = 0.7  # suffix/weak match upper bound
# STT-7: CBM_SAME_MODULE == CBM_SUFFIX_HIGH == 0.7 → both map to display label
# "dynamic" (see CBM_LABEL_TO_NUMERIC below). The numeric value is correct for
# each edge kind; the display label collision is intentional (categories merged
# at display layer). Splitting would require a new numeric tier (data drift
# risk across existing edges) — left as display-only known collision.

# Display label -> numeric mapping (labels are display-only category).
CBM_LABEL_TO_NUMERIC = {
    "static": CBM_EXACT,
    "inferred": CBM_UNIQUE,
    "dynamic": CBM_SAME_MODULE,
    "unknown": CBM_SUFFIX_LOW,
}

# --- W5: dynamic (string-name) dispatch ---
# A registry that maps a NAME STRING to a callable ("search_code" -> the
# function) leaves no call edge, so every tool it dispatches reads as dead. The
# extractor records identifier-shaped string literals so the indexer can resolve
# them across files, exactly like it already does for import nodes.
# The length floor exists because 3-letter words ("get", "add", "run") are far
# more often prose than a dispatch key; a name carrying an underscore is kept at
# any length because snake_case is already strong evidence of an identifier.
CBM_NAME_LITERAL_MIN_LEN = 6

# --- the single definition of "this callable is alive" ---
# A call edge below this confidence is a weak suffix guess, not evidence of
# life. `dynamic_ref` edges are name-based dispatch resolved by the indexer and
# ARE evidence: they are the only trace a string-keyed registry leaves.
LIVE_CALL_CONFIDENCE = 0.7
LIVE_REF_KINDS: tuple[str, ...] = ("calls", "dynamic_ref")

# Correlated-subquery fragment shared by every dead/orphan detector. It was
# copy-pasted in three places (cycle_point._dead_ratio, query.orphan_symbols,
# query.dead_code_candidates) with the threshold hardcoded in two of them, so a
# new edge kind silently reached only one of the three and the same symbol read
# alive in one report and dead in another. Binds: (*LIVE_REF_KINDS, threshold).
# The alias `n` must be the callable being tested.
LIVE_REF_EXISTS_SQL = (
    "EXISTS (SELECT 1 FROM edges e WHERE e.target = n.id "
    "        AND e.kind IN (" + ",".join("?" * len(LIVE_REF_KINDS)) + ") "
    "        AND e.confidence >= ?)"
)
LIVE_REF_BINDS: tuple = (*LIVE_REF_KINDS, LIVE_CALL_CONFIDENCE)
# --- extractor identity ---
# BUMP THIS whenever the extractor's OUTPUT changes: a new node kind, a new edge
# kind, a changed id scheme, different metadata. `files.content_hash` alone only
# answers "did the source change"; it cannot see that the SAME source now
# extracts differently, so a shipped extractor upgrade left every unmodified
# file frozen at the old shape (measured on [A] after W5 added `name_literal`:
# a full reindex re-extracted 9 of 250 files and the other 241 kept stale
# nodes). `stale_files` compares this too, so bumping it forces a re-extract.
# v3 (W4): calls written outside any function now emit an edge owned by the
# enclosing class or module, so every already-indexed file holds a `calls` set
# that is missing its module-level entries until it is re-extracted.
# v4 (W4): methods are pre-seeded, so `self._later()` written above `def
# _later` now resolves to the method instead of an `ext:` placeholder.
# Lives in config, not status.py, because indexer/store must read it and
# status.py imports store.
# v5 (W4): a callable used as a VALUE (`x or _default`, a dict of handlers, a
# param default) now emits a `calls` edge with metadata.callable_ref, so the
# fallback-default idiom stops reading as dead.
# v6 (W4): `<recv>.<attr>()` where `recv` is an imported name no longer
# suffix-matches a local definition of `<attr>`. The edge carries
# `metadata.recv_import` instead, so the resolver follows THAT module.
# v7 (W4): a reference to a NESTED def (`return guard`, `cb = _run`) resolves
# to that closure instead of being refused as a local rebinding, and a direct
# call to one binds by scope rather than through the flat name map.
# v8 (W6): a string in `__all__` no longer becomes a `name_literal`. It is an
# export declaration, not a reference, and resolving it back to the `def` below
# it made every `__all__` export read ALIVE with no caller anywhere -- the
# symbol was kept alive by its own export line.
# v9 (B013): the JS extractor changed shape. `require()` is now recorded as an
# import instead of a bare call, and a relative specifier is resolved against
# the importing file's directory, so the key is `lib.util.helper` rather than
# the path-shaped `./lib/util.js.helper` -- whose dotted prefixes start with
# `/` and could never match the `lib.util` the indexer builds. Neither change
# touches source bytes, so without this bump every already-indexed repo keeps
# its old rows: a *full* index answered `files_unchanged: 41` /
# `dependency_edges: 0` on the reported repo AFTER the fix was live. This is
# the second time that has happened here (see the W5 note in
# store.stale_files) -- an extractor change and a version bump are one change.
EXTRACTOR_VERSION = "ast-p0-v9-js-imports-are-modules"

# --- P7 Heavy enrich (opt-in, default OFF, stay OFF after install) ---
# Live env read, never frozen at import (same A13 reason as GRAPH_ENABLED).
# Flag-OFF callers must not import hooks/status/parser/git/cache — only these
# cheap readers are allowed on the hot path.


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def enrich_test_enabled() -> bool:
    """CBM_ENRICH_TEST live flag (H2). Default 0."""
    return _env_flag("CBM_ENRICH_TEST")


def enrich_commit_enabled() -> bool:
    """CBM_ENRICH_COMMIT live flag (H3). Default 0."""
    return _env_flag("CBM_ENRICH_COMMIT")


def enrich_council_enabled() -> bool:
    """CBM_ENRICH_COUNCIL live flag (H5). Default 0."""
    return _env_flag("CBM_ENRICH_COUNCIL")


# --- Tree-sitter opt-in (A5 - aspirational, optional) ---
CBM_USE_TREE_SITTER = os.environ.get("CBM_USE_TREE_SITTER", "0").strip().lower() in ("1", "true", "yes", "on")

# --- JS/TS extractor opt-in (Phase 6 Step 23 prototype) ---
# Default OFF: python-only indexing. Rollback = leave/return this to 0.
CBM_EXTRACT_JS = os.environ.get("CBM_EXTRACT_JS", "0").strip().lower() in ("1", "true", "yes", "on")

# --- DB location ---
# Default under .orchestrator_cache; override via env for test isolation.
def graph_db_path(project_path: str) -> str:
    """Return absolute path to graph.db for a project.

    Callers should pass forward-slash paths on Windows (e.g. D:/path/to/project);
    backslash paths can arrive NUL-mangled from upstream escaping (see tools._clean_project_path).
    GRAPH_DB_DIR env var overrides the default .orchestrator_cache location (test isolation).
    """
    base = os.environ.get("GRAPH_DB_DIR", "")
    if base:
        return os.path.join(base, "graph.db")
    return os.path.join(project_path, ".orchestrator_cache", "graph.db")


__all__ = [
    "GRAPH_ENABLED", "graph_enabled", "use_graph_for",
    "GRAPH_CONTEXT_MAX_FILES", "GRAPH_CONTEXT_MAX_SYMBOLS", "GRAPH_CONTEXT_TIMEOUT_S",
    "ANALYSIS_GRAPH_DEADLINE_MS", "ANALYSIS_EVOLUTION_DEADLINE_MS",
    "ANALYSIS_JOIN_GRACE_MS", "ANALYSIS_MAX_ACTIVE_WORKERS",
    "ANALYSIS_CHECK_EVERY_UNITS", "ANALYSIS_RESPONSE_MAX_BYTES",
    "ANALYSIS_TOP_LIST_MAX", "ANALYSIS_QUEUE_ON_FULL",
    "COUNCIL_CONTEXT_MAX_FILES", "COUNCIL_CONTEXT_MAX_SYMBOLS", "CBM_AUTO_REINDEX_MAX_STALE",
    "INDEX_TIME_BUDGET_CORE_S", "INDEX_TIME_BUDGET_FULL_S", "INDEX_MIN_NODES", "INDEX_MIN_EDGES",
    "CBM_EXACT", "CBM_UNIQUE", "CBM_SAME_MODULE", "CBM_SUFFIX_LOW", "CBM_SUFFIX_HIGH",
    "CBM_LABEL_TO_NUMERIC", "CBM_USE_TREE_SITTER", "CBM_EXTRACT_JS", "graph_db_path",
    "CBM_NAME_LITERAL_MIN_LEN", "EXTRACTOR_VERSION",
    "LIVE_CALL_CONFIDENCE", "LIVE_REF_KINDS", "LIVE_REF_EXISTS_SQL", "LIVE_REF_BINDS",
    "enrich_test_enabled", "enrich_commit_enabled", "enrich_council_enabled",
]


def __getattr__(name):
    # PEP 562 back-compat (A13 fix): resolve a lingering ``config.GRAPH_ENABLED``
    # attribute read to the LIVE env value, never a frozen import-time snapshot.
    if name == "GRAPH_ENABLED":
        return graph_enabled()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
