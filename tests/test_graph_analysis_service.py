"""AC-P2-S2: bounded analysis_service executor + cache fixtures."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import analysis_service as svc
from orchestrator.graph import store as gstore
from orchestrator.graph import tools as gtools
from orchestrator.graph.config import (
    ANALYSIS_CHECK_EVERY_UNITS,
    ANALYSIS_EVOLUTION_DEADLINE_MS,
    ANALYSIS_GRAPH_DEADLINE_MS,
    ANALYSIS_JOIN_GRACE_MS,
    ANALYSIS_MAX_ACTIVE_WORKERS,
    ANALYSIS_QUEUE_ON_FULL,
    ANALYSIS_RESPONSE_MAX_BYTES,
    ANALYSIS_TOP_LIST_MAX,
    graph_db_path,
)


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _node(nid: str, kind: str, name: str, file_path: str) -> dict:
    return {
        "id": nid,
        "kind": kind,
        "name": name,
        "qualified_name": name,
        "file_path": file_path,
        "language": "python",
        "start_line": 1,
        "end_line": 1,
    }


_DB_DIRS: dict[str, str] = {}


def _seed(tmp_path, monkeypatch, name, files, edges):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    written = {}
    for rel, text in files.items():
        written[rel] = _write(root, rel, text)
    db_dir = str(tmp_path / (name + "_db"))
    monkeypatch.setenv("GRAPH_DB_DIR", db_dir)
    monkeypatch.setenv("GRAPH_ENABLED", "1")
    root_s = str(root)
    _DB_DIRS[root_s] = db_dir
    conn = gstore.get_db(root_s)
    try:
        nodes = []
        seen = set()
        for src, dst, _conf in edges:
            for path in (src, dst):
                if path in seen:
                    continue
                seen.add(path)
                nodes.append(_node(f"mod:{path}", "module", Path(path).stem, path))
        for i, (src, dst, _conf) in enumerate(edges):
            nodes.append(_node(f"imp:{i}", "import", f"imp{i}", src))
        if not nodes:
            for rel in files:
                nodes.append(_node(f"mod:{rel}", "module", Path(rel).stem, rel))
        gstore.insert_nodes(conn, nodes)
        payload = []
        for i, (src, dst, conf) in enumerate(edges):
            label = "static" if conf >= 1.0 else "inferred" if conf >= 0.9 else "dynamic"
            payload.append({
                "source": f"imp:{i}",
                "target": f"mod:{dst}",
                "kind": "imports_module",
                "confidence": conf,
                "cbm_label": label,
                "line": i + 1,
                "col": 0,
                "is_external": 0,
                "metadata": {},
            })
        if payload:
            gstore.insert_edges(conn, payload)
        now = int(time.time() * 1000)
        for rel, path in written.items():
            raw = path.read_bytes()
            gstore.upsert_file(
                conn,
                path=rel.replace("\\", "/"),
                content_hash=hashlib.sha256(raw).hexdigest(),
                language="python",
                size=len(raw),
                modified_at=now,
                node_count=1,
            )
        run_id = gstore.start_index_run(conn, "full")
        gstore.finish_index_run(
            conn, run_id, status="ok",
            node_count=len(nodes), edge_count=len(payload),
            file_count=len(files), duration_s=0.01,
        )
        conn.commit()
    finally:
        conn.close()
    return root_s


def _run(**kwargs):
    root = kwargs.get("project_path")
    if root and root in _DB_DIRS:
        os.environ["GRAPH_DB_DIR"] = _DB_DIRS[root]
    return asyncio.run(svc.run_analysis_view(**kwargs))


def _wait_idle(timeout_s=2.0):
    deadline = time.monotonic() + timeout_s
    while svc.active_worker_count() > 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    return svc.active_worker_count()


class TestAnalysisServiceL1:
    def test_caps_named_in_l1(self):
        caps = svc.l1_caps()
        assert caps["graph_deadline_ms"] == ANALYSIS_GRAPH_DEADLINE_MS
        assert caps["evolution_deadline_ms"] == ANALYSIS_EVOLUTION_DEADLINE_MS == 5000
        assert caps["join_grace_ms"] == ANALYSIS_JOIN_GRACE_MS
        assert caps["latency_contract_ms"] == (
            ANALYSIS_GRAPH_DEADLINE_MS + ANALYSIS_JOIN_GRACE_MS
        )
        assert caps["max_active_workers"] == ANALYSIS_MAX_ACTIVE_WORKERS == 2
        assert caps["check_every_units"] == ANALYSIS_CHECK_EVERY_UNITS == 64
        assert caps["response_max_bytes"] == ANALYSIS_RESPONSE_MAX_BYTES == 65536
        assert caps["top_list_max"] == ANALYSIS_TOP_LIST_MAX == 50
        assert caps["queue_on_full"] is ANALYSIS_QUEUE_ON_FULL is False
        assert caps["new_threadpool_or_lane"] is False
        assert caps["backend"] == "asyncio.to_thread"


class TestAnalysisServiceTimeout:
    def setup_method(self):
        svc.reset_runtime_state()

    def teardown_method(self):
        svc.reset_runtime_state()

    def test_slow_unit_checkpoints_stop_no_cache(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "slow",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 1.0)],
        )
        seen = {"n": 0, "stopped": False}

        def work_fn(checkpoint, stop, **_kw):
            for _i in range(5000):
                seen["n"] += 1
                if checkpoint("unit"):
                    seen["stopped"] = True
                    return {
                        "status": "ok",
                        "score": 0.99,
                        "pass": True,
                        "cache_key": "must-not-publish",
                    }
                time.sleep(0.01)
            return {"status": "ok", "score": 1.0, "pass": True, "cache_key": "late"}

        t0 = time.monotonic()
        payload = _run(
            project_path=root,
            included_files=["a.py", "b.py"],
            work_fn=work_fn,
            deadline_ms=80,
        )
        elapsed = time.monotonic() - t0
        assert payload["status"] == "unavailable"
        assert payload["pass"] is False
        assert payload["score"] is None
        assert str(payload.get("reason") or "").startswith("unavailable:")
        assert seen["stopped"] or str(payload.get("reason") or "").endswith("deadline")
        assert seen["n"] < 5000
        assert elapsed < 1.5
        assert _wait_idle() == 0
        assert svc.cache_size() == 0

    def test_deadline_worker_idle_within_grace(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "dead",
            {"a.py": "x=1\n"},
            [],
        )

        def work_fn(checkpoint, stop, deadline_mono, **_kw):
            while not stop.is_set() and time.monotonic() < deadline_mono + 0.05:
                if checkpoint("spin"):
                    break
                time.sleep(0.005)
            return {"status": "ok", "score": 1.0, "cache_key": "nope"}

        payload = _run(
            project_path=root,
            included_files=["a.py"],
            work_fn=work_fn,
            deadline_ms=60,
        )
        assert payload["status"] == "unavailable"
        assert payload["score"] is None
        assert _wait_idle(timeout_s=1.0) == 0
        assert svc.cache_size() == 0
        assert svc.is_quarantined() is False


class TestAnalysisServiceDbLock:
    def setup_method(self):
        svc.reset_runtime_state()

    def teardown_method(self):
        svc.reset_runtime_state()

    def test_db_lock_stops_without_publish(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "lock",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 1.0)],
        )
        db = graph_db_path(root)
        holder = sqlite3.connect(db, timeout=0.1)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            def work_fn(checkpoint, stop, project_path, **_kw):
                if checkpoint("before_lock"):
                    return {"status": "ok", "score": 1.0, "cache_key": "no"}
                conn = gstore.get_db(project_path, readonly=False)
                try:
                    if checkpoint("after_open"):
                        return {"status": "ok", "score": 1.0, "cache_key": "no"}
                    conn.execute("BEGIN IMMEDIATE")
                    return {"status": "ok", "score": 1.0, "cache_key": "locked-ok"}
                finally:
                    conn.close()

            payload = _run(
                project_path=root,
                included_files=["a.py", "b.py"],
                work_fn=work_fn,
                deadline_ms=150,
            )
            assert payload["status"] == "unavailable"
            assert payload["pass"] is False
            assert payload["score"] is None
            assert _wait_idle() == 0
            assert svc.cache_size() == 0
        finally:
            holder.rollback()
            holder.close()


class TestAnalysisServiceCancel:
    def setup_method(self):
        svc.reset_runtime_state()

    def teardown_method(self):
        svc.reset_runtime_state()

    def test_client_cancel_stops_and_releases_slot(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "cancel",
            {"a.py": "x=1\n"},
            [],
        )
        started = threading.Event()

        def work_fn(checkpoint, stop, **_kw):
            started.set()
            for _i in range(2000):
                if checkpoint("unit"):
                    return {"status": "ok", "score": 1.0, "cache_key": "no"}
                time.sleep(0.01)
            return {"status": "ok", "score": 1.0, "cache_key": "late"}

        async def _go():
            task = asyncio.create_task(
                svc.run_analysis_view(
                    root,
                    included_files=["a.py"],
                    work_fn=work_fn,
                    deadline_ms=4000,
                )
            )
            deadline = time.monotonic() + 1.0
            while not started.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            task.cancel()
            return await task

        payload = asyncio.run(_go())
        assert payload["status"] == "unavailable"
        assert payload["score"] is None
        assert payload["pass"] is False
        assert _wait_idle() == 0
        assert svc.cache_size() == 0


class TestAnalysisServiceAdmission:
    def setup_method(self):
        svc.reset_runtime_state()

    def teardown_method(self):
        svc.reset_runtime_state()

    def test_third_request_busy_no_queue_no_slot_leak(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "busy",
            {"a.py": "x=1\n"},
            [],
        )
        gate = threading.Event()

        def work_fn(checkpoint, stop, **_kw):
            gate.wait(timeout=2.0)
            if checkpoint("after_gate"):
                return {"status": "unavailable", "score": None}
            return {"status": "ok", "score": None, "cache_key": "held"}

        async def _go():
            t1 = asyncio.create_task(
                svc.run_analysis_view(root, included_files=["a.py"], work_fn=work_fn, deadline_ms=3000)
            )
            t2 = asyncio.create_task(
                svc.run_analysis_view(root, included_files=["a.py"], work_fn=work_fn, deadline_ms=3000)
            )
            deadline = time.monotonic() + 1.0
            while svc.active_worker_count() < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            busy = await svc.run_analysis_view(
                root, included_files=["a.py"], work_fn=work_fn, deadline_ms=3000
            )
            gate.set()
            a, b = await asyncio.gather(t1, t2)
            return busy, a, b

        busy, a, b = asyncio.run(_go())
        assert busy["status"] == "unavailable"
        assert "analysis_busy" in str(busy.get("reason") or "")
        assert _wait_idle() == 0
        assert a["status"] in {"ok", "unavailable"}
        assert b["status"] in {"ok", "unavailable"}


class TestAnalysisServiceCache:
    def setup_method(self):
        svc.reset_runtime_state()

    def teardown_method(self):
        svc.reset_runtime_state()

    def test_distinct_roots_no_cache_bleed(self, tmp_path, monkeypatch):
        r1 = _seed(
            tmp_path, monkeypatch, "rootA",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 1.0)],
        )
        p1 = _run(project_path=r1, included_files=["a.py", "b.py"])
        r2 = _seed(
            tmp_path, monkeypatch, "rootB",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 1.0)],
        )
        p2 = _run(project_path=r2, included_files=["a.py", "b.py"])
        assert p1["status"] in {"ok", "partial"}
        assert p2["status"] in {"ok", "partial"}
        assert p1["cache_key"] != p2["cache_key"]
        assert p1["provenance"]["root"] != p2["provenance"]["root"]
        assert p1.get("cache_hit") is False
        assert p2.get("cache_hit") is False

        again = _run(project_path=r1, included_files=["a.py", "b.py"])
        assert again.get("cache_hit") is True
        assert again["cache_key"] == p1["cache_key"]
        again2 = _run(project_path=r2, included_files=["a.py", "b.py"])
        assert again2.get("cache_hit") is True
        assert again2["cache_key"] == p2["cache_key"]
        assert again["cache_key"] != again2["cache_key"]

    def test_scope_and_hash_change_invalidate(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "inv",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 1.0)],
        )
        first = _run(project_path=root, included_files=["a.py", "b.py"])
        assert first["status"] in {"ok", "partial"}
        hit = _run(project_path=root, included_files=["a.py", "b.py"])
        assert hit.get("cache_hit") is True
        narrowed = _run(project_path=root, included_files=["a.py"])
        assert narrowed.get("cache_hit") is False
        (Path(root) / "a.py").write_bytes(b"import b\n# changed\n")
        hashed = _run(project_path=root, included_files=["a.py", "b.py"])
        assert hashed.get("cache_hit") is False


class TestAnalysisServiceToolsWrapper:
    def test_cycle_point_signature_unchanged(self):
        sig = inspect.signature(gtools.cbm_cycle_point)
        assert "project_path" in sig.parameters
        assert list(sig.parameters)[0] == "project_path"
        src = inspect.getsource(gtools.cbm_cycle_point)
        assert "included_files" not in src
        assert "include_extensions" not in src

    def test_analysis_wrapper_skips_write_lock(self):
        src = inspect.getsource(gtools.cbm_analysis_view)
        assert "_graph_write_lock" not in src
        assert "run_analysis_view" in src

    def test_wrapper_returns_json(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "wrap",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 1.0)],
        )
        raw = asyncio.run(
            gtools.cbm_analysis_view(root, included_files=["a.py", "b.py"])
        )
        body = json.loads(raw)
        assert body["ok"] is True
        assert body["result"]["status"] in {"ok", "partial", "unavailable"}
        assert body["result"]["pass"] is False or body["result"]["status"] != "unavailable"
