"""AC-P3-S1: multi-source BFS test reach + no_tests / cycle / out-of-root."""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import store as gstore
from orchestrator.graph.testgap import build_test_gap, compute_test_gap


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


def _seed(tmp_path, monkeypatch, name, files, edges):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    written = {}
    for rel, text in files.items():
        written[rel] = _write(root, rel, text)
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / (name + "_db")))
    monkeypatch.setenv("GRAPH_ENABLED", "1")
    root_s = str(root)
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


class TestTestGapReach:
    def test_test_to_a_to_b_reaches_a_and_b(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "reach",
            {
                "tests/test_a.py": "import a\n",
                "a.py": "import b\n",
                "b.py": "x=1\n",
                "c.py": "y=1\n",
            },
            [
                ("tests/test_a.py", "a.py", 1.0),
                ("a.py", "b.py", 1.0),
            ],
        )
        payload = build_test_gap(
            root,
            included_files=["a.py", "b.py", "c.py"],
            test_files=["tests/test_a.py"],
        )
        assert payload["status"] in {"ok", "partial"}
        assert payload["coverage_ratio"] == 2 / 3
        assert payload["vacuous"] is False
        assert payload["covered_files"] == ["a.py", "b.py"]
        assert payload["uncovered_files"] == ["c.py"]
        assert "c.py" in payload["isolated_uncovered"]
        assert payload["pass"] is False
        for gap in payload["gaps"]:
            assert gap["max_complexity"] is None
            assert gap["rank_key"] == "fan_in"


class TestTestGapDisconnected:
    def test_disconnected_c_is_gap(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "gapc",
            {
                "tests/test_a.py": "import a\n",
                "a.py": "import b\n",
                "b.py": "x=1\n",
                "c.py": "y=1\n",
            },
            [
                ("tests/test_a.py", "a.py", 1.0),
                ("a.py", "b.py", 1.0),
            ],
        )
        payload = build_test_gap(
            root,
            included_files=["a.py", "b.py", "c.py"],
            test_files=["tests/test_a.py"],
        )
        assert payload["uncovered_files"] == ["c.py"]
        assert payload["counts"]["uncovered_files"] == 1
        assert payload["gaps"][0]["file"] == "c.py"
        assert payload["gaps"][0]["fan_in"] == 0


class TestTestGapNoTests:
    def test_no_tests_unavailable_not_zero_ratio(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "notest",
            {"a.py": "x=1\n", "c.py": "y=1\n"},
            [],
        )
        payload = build_test_gap(
            root,
            included_files=["a.py", "c.py"],
            test_files=[],
        )
        assert payload["status"] == "unavailable"
        assert payload["reason"] == "no_tests"
        assert payload["coverage_ratio"] is None
        assert payload["score"] is None
        assert payload["pass"] is False
        assert payload["vacuous"] is False
        assert payload["uncovered_files"] == ["a.py", "c.py"]

    def test_tests_zero_source_vacuous_one(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "vacuous",
            {"tests/test_only.py": "assert True\n"},
            [],
        )
        payload = build_test_gap(
            root,
            included_files=[],
            test_files=["tests/test_only.py"],
        )
        assert payload["status"] == "ok"
        assert payload["coverage_ratio"] == 1.0
        assert payload["vacuous"] is True
        assert payload["uncovered_files"] == []
        assert payload["source_files"] == []


class TestTestGapCycles:
    def test_cycles_terminate_and_cover_loop(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "cycle",
            {
                "tests/test_a.py": "import a\n",
                "a.py": "import b\n",
                "b.py": "import a\n",
            },
            [
                ("tests/test_a.py", "a.py", 1.0),
                ("a.py", "b.py", 1.0),
                ("b.py", "a.py", 1.0),
            ],
        )
        t0 = time.monotonic()
        payload = build_test_gap(
            root,
            included_files=["a.py", "b.py"],
            test_files=["tests/test_a.py"],
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 1.5
        assert payload["status"] in {"ok", "partial"}
        assert payload["covered_files"] == ["a.py", "b.py"]
        assert payload["uncovered_files"] == []
        assert payload["coverage_ratio"] == 1.0


class TestTestGapOutOfRoot:
    def test_out_of_root_rejected(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "oor",
            {"a.py": "x=1\n", "tests/t.py": "import a\n"},
            [("tests/t.py", "a.py", 1.0)],
        )
        payload = build_test_gap(
            root,
            included_files=["a.py", "../secret.py"],
            test_files=["tests/t.py"],
        )
        assert payload["status"] == "unavailable"
        assert payload["reason"] == "out_of_root"
        assert payload["coverage_ratio"] is None
        assert payload["pass"] is False


class TestTestGapNoFakeCc:
    def test_compute_ranks_by_fan_in_complexity_none(self):
        view = {
            "status": "ok",
            "included_files": ["hot.py", "cold.py"],
            "test_files": ["tests/t.py"],
            "isolated_files": ["cold.py"],
            "observed_edges": [
                {"src": "tests/t.py", "dst": "other.py", "confidence": 1.0},
                {"src": "x.py", "dst": "hot.py", "confidence": 1.0},
                {"src": "y.py", "dst": "hot.py", "confidence": 1.0},
            ],
            "provenance": {"root": "mem", "included_set_hash": "h", "fingerprint": "f"},
        }
        payload = compute_test_gap(view)
        assert payload["status"] == "ok"
        assert payload["uncovered_files"] == ["cold.py", "hot.py"]
        assert payload["gaps"][0]["file"] == "hot.py"
        assert payload["gaps"][0]["fan_in"] == 2
        assert payload["gaps"][0]["max_complexity"] is None
        assert payload["gaps"][1]["file"] == "cold.py"
        assert payload["gaps"][1]["fan_in"] == 0
