"""W5 regression pins: import scope (G3), cycle evidence (G2), extractor-version
staleness, and the dynamic_ref self-mention rule.

Every assertion here corresponds to a defect measured on [A] itself, not to a
hypothetical. The comments name the measurement so a future reader can tell a
real invariant from a snapshot of today's numbers.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import cycle_point, schema, store
from orchestrator.graph.config import EXTRACTOR_VERSION, graph_db_path
from orchestrator.graph.indexer import index_project


def _mkproject(tmp_path, name, files):
    project = tmp_path / name
    project.mkdir()
    for rel, src in files.items():
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src.lstrip(), encoding="utf-8")
    return str(project).replace("\\", "/")


def _index(tmp_path, monkeypatch, name, files):
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / "graph" / name))
    project = _mkproject(tmp_path, name, files)
    result = index_project(project, scope="full")
    assert result["status"] == "completed", result
    return project, result


# --- G3: deferred scope must reach the FILE-level edge ------------------------

_TWO_FILES = {
    "a.py": """
import b


def eager():
    return b.thing()
""",
    "b.py": """
def thing():
    return 1


def lazy():
    import a
    return a.eager()
""",
}


def test_imports_module_carries_import_scope(tmp_path, monkeypatch):
    """The extractor knows an import sits inside a function body, but the flag
    used to die on the `imports` edge. Every cycle consumer reads
    `imports_module`, so without it a crash-on-import cycle and a mere
    technical-debt cycle were indistinguishable."""
    project, _ = _index(tmp_path, monkeypatch, "scope", _TWO_FILES)
    conn = sqlite3.connect(graph_db_path(project))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT s.file_path src, t.file_path dst, e.metadata "
            "FROM edges e JOIN nodes s ON s.id=e.source "
            "JOIN nodes t ON t.id=e.target "
            "WHERE e.kind='imports_module' AND s.file_path<>t.file_path"
        ).fetchall()
    finally:
        conn.close()

    assert rows, "no cross-file imports_module edges were built"
    by_pair = {}
    for r in rows:
        meta = json.loads(r["metadata"] or "{}")
        assert "deferred" in meta and "scope" in meta, meta
        by_pair[(Path(r["src"]).name, Path(r["dst"]).name)] = meta

    # a.py imports b at module level; b.py imports a inside lazy().
    assert by_pair[("a.py", "b.py")]["scope"] == "top_level"
    assert by_pair[("b.py", "a.py")]["scope"] == "deferred"
    assert by_pair[("b.py", "a.py")]["deferred"] is True


# --- G2: the cycle report must name the import lines --------------------------

def test_cycle_details_report_import_evidence(tmp_path, monkeypatch):
    """`cycles` used to be a bare list of filenames: true, and useless -- the
    reader still had to grep for the imports holding the loop together."""
    project, _ = _index(tmp_path, monkeypatch, "cyc", _TWO_FILES)
    out = cycle_point.compute_cycle_point(project)

    assert out["cycle_count"] == 1
    # v1 shape preserved: existing callers read cycles[i] as a list of files.
    assert isinstance(out["cycles"][0], list)
    assert all(isinstance(f, str) for f in out["cycles"][0])

    detail = out["cycle_details"][0]
    assert detail["size"] == 2
    assert detail["edge_count"] == 2
    # One direction is top-level -> the loop CAN raise ImportError.
    assert detail["top_level_edges"] == 1
    assert detail["deferred_only"] is False
    assert detail["severity"] == "import_time_risk"
    assert out["import_time_cycles"] == 1
    assert out["deferred_only_cycles"] == 0

    breakable = detail["breakable_at_top_level"]
    assert len(breakable) == 1, "only the import-time edge is breakable"
    assert Path(breakable[0]["src"]).name == "a.py"
    assert breakable[0]["lines"], "an edge with no line number is not evidence"


def test_deferred_only_cycle_is_debt_not_risk(tmp_path, monkeypatch):
    """Both directions deferred: real coupling, but it cannot crash on import.
    This is the distinction the campaign brief called the difference between
    'a crash cycle' and 'a technical-debt cycle'."""
    project, _ = _index(tmp_path, monkeypatch, "cyc_deferred", {
        "a.py": """
def eager():
    import b
    return b.thing()
""",
        "b.py": """
def thing():
    import a
    return a.eager
""",
    })
    out = cycle_point.compute_cycle_point(project)

    assert out["cycle_count"] == 1
    detail = out["cycle_details"][0]
    assert detail["deferred_only"] is True
    assert detail["severity"] == "deferred_debt"
    assert detail["top_level_edges"] == 0
    assert detail["breakable_at_top_level"] == []
    assert out["deferred_only_cycles"] == 1
    assert out["import_time_cycles"] == 0


def test_cycle_evidence_groups_by_line_not_by_bound_name(tmp_path, monkeypatch):
    """`from x import a, b, c` emits one import node per NAME, so the raw edge
    rows repeat the same line three times. The reader wants lines to go edit."""
    project, _ = _index(tmp_path, monkeypatch, "cyc_names", {
        "a.py": """
from b import one, two, three


def use():
    return one, two, three
""",
        "b.py": """
def one():
    return 1


def two():
    return 2


def three():
    return 3


def back():
    import a
    return a.use()
""",
    })
    out = cycle_point.compute_cycle_point(project)
    detail = out["cycle_details"][0]
    edge = next(e for e in detail["edges"] if Path(e["src"]).name == "a.py")
    assert edge["import_count"] == 1, f"one import LINE, not one per name: {edge}"
    assert edge["top_level_count"] == 1
    assert len(edge["lines"]) == 1


# --- extractor identity: content_hash alone cannot see a new extractor --------

def test_extractor_version_change_makes_every_file_stale(tmp_path, monkeypatch):
    """The bug that nearly shipped: `stale_files` compared only content_hash,
    so a *full* reindex after the extractor gained a new node kind re-extracted
    9 of 250 [A] files and left 241 frozen at the old shape. Every measurement
    taken on top of that index would have been wrong."""
    project, _ = _index(tmp_path, monkeypatch, "stale",
                        {"m.py": "def f():\n    return 1\n"})
    conn = store.get_db(project)
    try:
        cands = [{"path": r["path"], "content_hash": r["content_hash"]}
                 for r in conn.execute("SELECT path, content_hash FROM files")]
        assert cands, "nothing was recorded in files"
        # Unchanged source, current extractor -> nothing to do.
        assert store.stale_files(conn, cands) == []
        # Same source, different extractor -> everything must be re-extracted.
        monkeypatch.setattr(store, "EXTRACTOR_VERSION", EXTRACTOR_VERSION + "-next")
        assert len(store.stale_files(conn, cands)) == len(cands)
    finally:
        conn.close()


def test_upsert_file_records_the_extractor_version(tmp_path, monkeypatch):
    project, _ = _index(tmp_path, monkeypatch, "stampver",
                        {"m.py": "def f():\n    return 1\n"})
    conn = sqlite3.connect(graph_db_path(project))
    try:
        versions = {r[0] for r in conn.execute("SELECT extractor_version FROM files")}
    finally:
        conn.close()
    assert versions == {EXTRACTOR_VERSION}


def test_migrate_adds_the_column_to_a_preexisting_db(tmp_path):
    """CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so a new
    column never reaches a live DB. The live [A] graph.db is 44 MB -- dropping
    it is not an option, so the ALTER has to be there and has to be idempotent.
    """
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    try:
        # A db created before the column existed.
        conn.execute(
            "CREATE TABLE files (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL, "
            "language TEXT NOT NULL, size INTEGER NOT NULL, modified_at INTEGER NOT NULL, "
            "indexed_at INTEGER NOT NULL, node_count INTEGER DEFAULT 0, errors TEXT);")
        conn.commit()
        assert schema.migrate(conn) == ["files.extractor_version"]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(files);")}
        assert "extractor_version" in cols
        # Idempotent: running it again must be a silent no-op, not an error.
        assert schema.migrate(conn) == []
    finally:
        conn.close()


# --- dynamic_ref: skip a self-MENTION, not the whole file ---------------------

def test_same_file_registry_entry_keeps_its_handler_alive(tmp_path, monkeypatch):
    """A module that registers its own handlers by name is the common case, not
    the exception: code_tools.py does it for rule_drift_check, git_log,
    memory_query and the council_cron_job_* family. Skipping every same-file
    literal left 19 live callables reading as dead."""
    project, _ = _index(tmp_path, monkeypatch, "selfreg", {"reg.py": """
def rule_drift_check():
    return 1


TOOL_MAP = {}
TOOL_MAP["rule_drift_check"] = rule_drift_check
"""})
    conn = sqlite3.connect(graph_db_path(project))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM edges e JOIN nodes t ON t.id=e.target "
            "WHERE e.kind='dynamic_ref' AND t.name='rule_drift_check'").fetchone()[0]
    finally:
        conn.close()
    assert n == 1, "a same-file registry entry is evidence of life"


def test_a_function_naming_itself_is_not_evidence(tmp_path, monkeypatch):
    """The rule the file-wide skip was reaching for, stated precisely: a log
    line inside a function does not make that function live."""
    project, _ = _index(tmp_path, monkeypatch, "selfmention", {"log.py": """
def some_helper():
    print("some_helper starting")
    return 1
"""})
    conn = sqlite3.connect(graph_db_path(project))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM edges e JOIN nodes t ON t.id=e.target "
            "WHERE e.kind='dynamic_ref' AND t.name='some_helper'").fetchone()[0]
    finally:
        conn.close()
    assert n == 0, "a function mentioning its own name proves nothing"


# --- F017: `count` is the population, not the page size -----------------------

def test_dead_code_count_is_the_population_not_the_page(tmp_path, monkeypatch):
    """`count` used to be min(len(candidates), limit) -- a project with 900 hits
    and the default limit answered 'count: 50'."""
    from orchestrator.graph import query

    files = {f"m{i}.py": f"def _unused_{i}():\n    return {i}\n" for i in range(8)}
    project, _ = _index(tmp_path, monkeypatch, "f017", files)

    full = query.dead_code_candidates(project, limit=1000)
    assert full["count"] >= 8, full
    paged = query.dead_code_candidates(project, limit=3)
    assert paged["count"] == full["count"], "count must not shrink with the page"
    assert paged["returned_count"] == 3
    assert len(paged["candidates"]) == 3
    assert paged["truncated"] is True
    assert paged["limit_applied"] == 3
