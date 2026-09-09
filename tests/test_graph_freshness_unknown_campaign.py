"""AC-P1-S1: stale_count exception + recent index => freshness unknown.

Public surface is cbm_graph_status. Count failure must not become false-fresh
when last_run is <1h. Exception type only in error; no path/SQL/traceback.
True fresh (stale_n==0) stays fresh. Nonzero stale stays stale.
"""
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import store as gstore
from orchestrator.graph import status as gstatus
from orchestrator.graph.indexer import index_project
from orchestrator.graph.tools import cbm_graph_status


def _indexed(tmp_path, monkeypatch, name, files):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / (name + "_db")))
    monkeypatch.setenv("GRAPH_ENABLED", "1")
    index_project(str(root), scope="full")
    return str(root)


def _public_status(root):
    raw = asyncio.run(cbm_graph_status(project_path=root))
    payload = json.loads(raw)
    assert payload.get("ok") is True, payload
    return payload["result"]


def test_count_error_recent_index_is_unknown_not_fresh(tmp_path, monkeypatch):
    root = _indexed(tmp_path, monkeypatch, "recent_err", {
        "m.py": "def helper():\n    return 1\n",
    })
    conn = gstore.get_db(root, readonly=True)
    try:
        last = conn.execute(
            "SELECT finished_at FROM index_runs ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        assert last is not None
        finished_at = last[0] if not isinstance(last, sqlite3.Row) else last["finished_at"]
        if finished_at is None:
            finished_at = int(time.time() * 1000)
        age_ms = int(time.time() * 1000) - int(finished_at)
        assert age_ms < 3_600_000, age_ms
    finally:
        conn.close()

    def _boom(_conn, _root):
        raise sqlite3.OperationalError("campaign-forced stale_count failure")

    monkeypatch.setattr(gstatus, "_stale_count", _boom)
    result = _public_status(root)
    assert result["freshness"] == "unknown"
    assert result["stale_files"] is None
    assert result["error"] == "stale_count_failed: OperationalError"
    assert "campaign-forced" not in (result["error"] or "")
    assert root.replace("\\", "/").lower() not in (result["error"] or "").lower()
    assert "SELECT" not in (result["error"] or "")
    assert "Traceback" not in (result["error"] or "")
    assert result["integrity"] == "ok"
    assert result["last_run"] is not None
    assert result["files"] >= 1


def test_true_fresh_stays_fresh(tmp_path, monkeypatch):
    root = _indexed(tmp_path, monkeypatch, "true_fresh", {
        "m.py": "def helper():\n    return 1\n",
    })
    result = _public_status(root)
    assert result["freshness"] == "fresh"
    assert result["stale_files"] == 0
    assert result.get("error") in (None, "")


def test_nonzero_stale_stays_stale(tmp_path, monkeypatch):
    root = _indexed(tmp_path, monkeypatch, "stale_ok", {
        "m.py": "def helper():\n    return 1\n",
    })
    Path(root, "m.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    result = _public_status(root)
    assert result["freshness"] == "stale"
    assert result["stale_files"] == 1
    assert result.get("error") in (None, "")
