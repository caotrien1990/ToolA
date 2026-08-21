"""A full index must drop the rows of files that left the tree.

`index_project` iterated the CANDIDATE SET found on disk and reported
`files_unchanged = len(snapshots) - len(stale)`. `status._stale_count` iterates
the ROWS IN THE files TABLE and counts a row stale when its file is missing.
Those are two different populations, and nothing on the full-index path ever
deleted a row whose file had vanished -- so:

  index #2  status=completed  files_indexed=0  files_unchanged=2
            status.stale_files=1   freshness=fresh
  index #3  files_unchanged=2      status.stale_files=1

Both counters were right about their own population, which is exactly what made
the pair contradictory (the reported symptom: `stale_files: 24` immediately after
a completed index reporting `files_unchanged: 2`). Three consequences, all worse
than the cosmetic mismatch:

  1. the deleted file's nodes stayed in the graph and kept surfacing in
     dead_code / architecture answers as symbols of a file that is gone;
  2. `query._auto_reindex_if_stale` gates on the status count, so a full reindex
     could never clear it -- the reindex re-fires forever without converging;
  3. `freshness` still read "fresh" while `stale_files` was non-zero.

`incremental_index` already called `_delete_file_entries` for its `deleted` /
`renamed` sets; the full path simply never did. The contract was already written
down in test_graph_stale_count_agreement.py::test_a_deleted_file_is_stale --
"or a deletion would never prompt the reindex that drops its nodes" -- and it was
the *drops its nodes* half that was missing.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import status as gstatus
from orchestrator.graph import store as gstore
from orchestrator.graph.indexer import index_project


def _project(tmp_path, monkeypatch, name="prune"):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for fname, body in (("a.py", "def a():\n    return 1\n"),
                        ("b.py", "def b():\n    return 2\n"),
                        ("gone.py", "def gone():\n    return 3\n")):
        (root / fname).write_text(body, encoding="utf-8")
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / (name + "_db")))
    monkeypatch.setenv("ORCHESTRATOR_PARALLEL_INDEX", "off")
    return root


def _counts(root):
    conn = gstore.get_db(str(root), readonly=True)
    try:
        rows = [r["path"] for r in conn.execute("SELECT path FROM files;")]
        gone_nodes = conn.execute(
            "SELECT COUNT(*) c FROM nodes WHERE file_path='gone.py';").fetchone()["c"]
    finally:
        conn.close()
    return rows, gone_nodes


def test_a_full_index_prunes_the_row_of_a_deleted_file(tmp_path, monkeypatch):
    """The files row must go, or status counts it stale forever."""
    root = _project(tmp_path, monkeypatch)
    index_project(str(root), scope="full")
    rows, _ = _counts(root)
    assert "gone.py" in rows

    os.remove(root / "gone.py")
    index_project(str(root), scope="full")

    rows, _ = _counts(root)
    assert "gone.py" not in rows, (
        "a file that left the tree still has a files row -- status._stale_count "
        "will count it stale on every call and no reindex can clear it")


def test_a_full_index_drops_the_nodes_of_a_deleted_file(tmp_path, monkeypatch):
    """Symbols of a deleted file must stop answering graph queries."""
    root = _project(tmp_path, monkeypatch, name="prune_nodes")
    index_project(str(root), scope="full")
    _, gone_nodes = _counts(root)
    assert gone_nodes > 0, "fixture is not exercising the case"

    os.remove(root / "gone.py")
    index_project(str(root), scope="full")

    _, gone_nodes = _counts(root)
    assert gone_nodes == 0, (
        "nodes of a deleted file survived a full index -- they keep surfacing in "
        "dead_code / architecture answers as symbols of a file that is gone")


def test_the_deletion_is_reported_not_silent(tmp_path, monkeypatch):
    """A pruned file is a real change to the graph; the count must say so."""
    root = _project(tmp_path, monkeypatch, name="prune_report")
    index_project(str(root), scope="full")
    os.remove(root / "gone.py")

    res = index_project(str(root), scope="full")

    assert res.get("files_pruned") == 1, (
        "a full index that dropped a file reported nothing -- silent pruning is "
        "how the two counters drifted apart unnoticed in the first place")


def test_status_and_index_agree_after_a_deletion(tmp_path, monkeypatch):
    """The reported contradiction itself: completed + all-unchanged vs stale>0."""
    root = _project(tmp_path, monkeypatch, name="prune_agree")
    index_project(str(root), scope="full")
    os.remove(root / "gone.py")

    res = index_project(str(root), scope="full")
    st = gstatus.graph_status(str(root))

    assert res["status"] == "completed"
    assert st["stale_files"] == 0, (
        "index reported every surviving file unchanged while status still counted "
        "%s stale -- the two halves describe different populations" % st["stale_files"])
    assert res["files_unchanged"] == 2


def test_a_further_full_index_converges(tmp_path, monkeypatch):
    """_auto_reindex_if_stale gates on the status count; it has to reach 0."""
    root = _project(tmp_path, monkeypatch, name="prune_converge")
    index_project(str(root), scope="full")
    os.remove(root / "gone.py")
    index_project(str(root), scope="full")

    res3 = index_project(str(root), scope="full")
    st3 = gstatus.graph_status(str(root))

    assert st3["stale_files"] == 0
    assert res3.get("files_pruned") == 0, "nothing left to prune on the second pass"


def test_freshness_is_not_fresh_while_files_are_stale(tmp_path, monkeypatch):
    """`stale_files: 1` + `freshness: "fresh"` is a self-contradicting report.

    The age branch answered "fresh" for a whole hour regardless of the count, so
    a reader checking one field got the opposite of what the other field said.
    """
    root = _project(tmp_path, monkeypatch, name="prune_fresh")
    index_project(str(root), scope="full")

    # Make one file stale WITHOUT deleting it, so pruning cannot mask the case.
    conn = gstore.get_db(str(root))
    try:
        conn.execute("UPDATE files SET content_hash='deadbeef' WHERE path='a.py';")
        conn.commit()
    finally:
        conn.close()

    st = gstatus.graph_status(str(root))
    assert st["stale_files"] > 0
    assert st["freshness"] != "fresh", (
        "status reported freshness=fresh while %s file(s) were stale"
        % st["stale_files"])
