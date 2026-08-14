"""Graph feature-flag and bounded-context config constants.

All graph code paths must consult these named constants so the graph layer can be
disabled atomically and all bounded-context limits revert to safe defaults.
Phase 0 Step 2 - A_Q_devineupgrade_breakdown_plan_2nd.
"""
import os

# --- Master switch ---
# GRAPH_ENABLED=false keeps every existing [A] search flow unchanged.
# Read once at import; rollback = restart orchestrator (or set env=0 before import).
GRAPH_ENABLED = os.environ.get("GRAPH_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")

# --- Per-tool graph participation ---
# A tool opts out even when GRAPH_ENABLED is true via GRAPH_USE_<TOOL>=0.
_DEFAULT_USE_GRAPH = True


def use_graph_for(tool_name: str) -> bool:
    """Return whether a tool may use graph context."""
    if not GRAPH_ENABLED:
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
    "GRAPH_ENABLED", "use_graph_for",
    "GRAPH_CONTEXT_MAX_FILES", "GRAPH_CONTEXT_MAX_SYMBOLS", "GRAPH_CONTEXT_TIMEOUT_S",
    "COUNCIL_CONTEXT_MAX_FILES", "COUNCIL_CONTEXT_MAX_SYMBOLS", "CBM_AUTO_REINDEX_MAX_STALE",
    "INDEX_TIME_BUDGET_CORE_S", "INDEX_TIME_BUDGET_FULL_S", "INDEX_MIN_NODES", "INDEX_MIN_EDGES",
    "CBM_EXACT", "CBM_UNIQUE", "CBM_SAME_MODULE", "CBM_SUFFIX_LOW", "CBM_SUFFIX_HIGH",
    "CBM_LABEL_TO_NUMERIC", "CBM_USE_TREE_SITTER", "CBM_EXTRACT_JS", "graph_db_path",
]
