"""Staleness has one definition, and both halves must agree on it.

`store.stale_files` (what the indexer acts on) counted a file stale when its
content changed, when it was never indexed, OR when it was indexed by a different
EXTRACTOR_VERSION. `status._stale_count` (what every report and, critically,
`query._auto_reindex_if_stale` reads) applied only the first clause.

Measured on live [A] immediately after the v7 -> v8 bump: `_stale_count` returned
5 while all 700 rows in `files` still carried v7. The status probe therefore
called the database fresh when it no longer matched the extractor, and because
`_auto_reindex_if_stale` gates on that count, the bump could never trigger the
reindex it exists to force -- the DB would have stayed at the old node/edge shape
indefinitely.

This is the same class of defect W6 fixed for the dead-callable predicate, and
the same one `store.stale_files` documents from W5. It came back through the
other half of the pair, which is the argument for pinning it with a test rather
than a comment.
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import config as gconfig
from orchestrator.graph import status as gstatus
from orchestrator.graph import store as gstore
from orchestrator.graph.indexer import index_project


def _indexed(tmp_path, monkeypatch, name, files):
    """Index a tiny project and hand back (root, conn)."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / (name + "_db")))
    index_project(str(root), scope="full")
    conn = gstore.get_db(str(root), readonly=True)
    conn.row_factory = sqlite3.Row
    return str(root), conn


def test_a_freshly_indexed_project_is_not_stale(tmp_path, monkeypatch):
    """The baseline. Without it, a count that always returns len(files) would
    pass every other test in this file."""
    root, conn = _indexed(tmp_path, monkeypatch, "fresh", {
        "m.py": "def helper():\n    return 1\n",
    })
    try:
        assert gstatus._stale_count(conn, root) == 0
    finally:
        conn.close()


def test_a_changed_file_is_stale(tmp_path, monkeypatch):
    """The clause that already worked, kept under test so the version clause
    cannot be added by deleting it."""
    root, conn = _indexed(tmp_path, monkeypatch, "changed", {
        "m.py": "def helper():\n    return 1\n",
    })
    try:
        (Path(root) / "m.py").write_text(
            "def helper():\n    return 2\n", encoding="utf-8")
        assert gstatus._stale_count(conn, root) == 1
    finally:
        conn.close()


def test_same_size_same_mtime_content_change_is_stale(tmp_path, monkeypatch):
    root, conn = _indexed(tmp_path, monkeypatch, "same_meta", {
        "m.py": "VALUE = 'aa'\n",
    })
    target = Path(root) / "m.py"
    before = target.stat()
    try:
        target.write_text("VALUE = 'bb'\n", encoding="utf-8")
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert target.stat().st_size == before.st_size
        assert gstatus._stale_count(conn, root) == 1
    finally:
        conn.close()


def test_a_version_bump_makes_every_file_stale(tmp_path, monkeypatch):
    """The regression proper: content unchanged, extractor different.

    Measured on [A] before the fix, this returned 0 for 700 v7 rows under a v8
    extractor.
    """
    root, conn = _indexed(tmp_path, monkeypatch, "bumped", {
        "a.py": "def one():\n    return 1\n",
        "b.py": "def two():\n    return 2\n",
    })
    try:
        assert gstatus._stale_count(conn, root) == 0
        monkeypatch.setattr(gstatus, "EXTRACTOR_VERSION", "ast-p0-vNEXT-synthetic")
        assert gstatus._stale_count(conn, root) == 2
    finally:
        conn.close()


def test_the_two_implementations_agree_after_a_bump(tmp_path, monkeypatch):
    """The property that actually matters: `store.stale_files` is what the
    indexer re-extracts, `_stale_count` is what decides whether it ever runs. A
    disagreement means one of them is unreachable."""
    root, conn = _indexed(tmp_path, monkeypatch, "agree", {
        "a.py": "def one():\n    return 1\n",
        "b.py": "def two():\n    return 2\n",
        "c.py": "def three():\n    return 3\n",
    })
    try:
        rows = conn.execute("SELECT path, content_hash FROM files;").fetchall()
        candidates = [{"path": r["path"], "content_hash": r["content_hash"]}
                      for r in rows]

        assert gstatus._stale_count(conn, root) == 0
        assert gstore.stale_files(conn, candidates) == []

        monkeypatch.setattr(gstatus, "EXTRACTOR_VERSION", "ast-p0-vNEXT-synthetic")
        monkeypatch.setattr(gstore, "EXTRACTOR_VERSION", "ast-p0-vNEXT-synthetic")

        by_status = gstatus._stale_count(conn, root)
        by_store = len(gstore.stale_files(conn, candidates))
        assert by_status == by_store == len(candidates), (by_status, by_store)
    finally:
        conn.close()


def test_a_deleted_file_is_stale(tmp_path, monkeypatch):
    """An indexed row with no file on disk must count, or a deletion would never
    prompt the reindex that drops its nodes."""
    root, conn = _indexed(tmp_path, monkeypatch, "deleted", {
        "a.py": "def one():\n    return 1\n",
        "b.py": "def two():\n    return 2\n",
    })
    try:
        os.remove(Path(root) / "b.py")
        assert gstatus._stale_count(conn, root) == 1
    finally:
        conn.close()


def test_status_reads_the_version_from_config(tmp_path, monkeypatch):
    """status.py re-exports EXTRACTOR_VERSION for backward compatibility; if the
    re-export ever drifts from config, the comparison above silently stops
    matching what the indexer stamps."""
    assert gstatus.EXTRACTOR_VERSION == gconfig.EXTRACTOR_VERSION
    assert gstore.EXTRACTOR_VERSION == gconfig.EXTRACTOR_VERSION
