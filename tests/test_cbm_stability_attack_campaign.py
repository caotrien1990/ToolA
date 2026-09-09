"""AC-P3-S2: stability cascade + entry reach. Hand-coded oracle."""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import store as gstore
from orchestrator.graph.analysis_view import build_analysis_view
from orchestrator.graph.attack import compute_entry_reach
from orchestrator.graph.stability import (
    FOUNDATION_CA_MIN,
    FOUNDATION_I_MAX,
    compute_stability,
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


def _view(root, files, tests=None):
    return build_analysis_view(
        root,
        included_files=files,
        test_files=tests or [],
        finalize_scope=False,
    )


def _foundation_graph_files():
    # core/f.py: Ca=3, Ce=0, I=0 -> foundation
    # pkg/a.py: imports core/f.py only -> cascade stable
    # pkg/b.py: isolated I=0 Ca=0 -> never stable
    # other/x.py: imports pkg/b.py (unstable dest, cross-folder)
    return {
        "core/f.py": "x=1\n",
        "pkg/a.py": "import f\n",
        "pkg/b.py": "y=1\n",
        "other/x.py": "import b\n",
        "p1.py": "import f\n",
        "p2.py": "import f\n",
        "p3.py": "import f\n",
    }, [
        ("pkg/a.py", "core/f.py", 1.0),
        ("p1.py", "core/f.py", 1.0),
        ("p2.py", "core/f.py", 1.0),
        ("p3.py", "core/f.py", 1.0),
        ("other/x.py", "pkg/b.py", 1.0),
    ]


class TestStabilityPermutation:
    def test_permutation_invariant(self, tmp_path, monkeypatch):
        files, edges = _foundation_graph_files()
        root = _seed(tmp_path, monkeypatch, "perm", files, edges)
        included = list(files)
        first = None
        for order in (
            included,
            list(reversed(included)),
            [included[i] for i in (2, 0, 5, 1, 6, 3, 4)],
        ):
            view = _view(root, order)
            payload = compute_stability(view)
            key = (
                tuple(payload["stable_files"]),
                tuple(payload["foundation_files"]),
                payload["coupling_score"],
                tuple(sorted((g, row["score"]) for g, row in payload["cohesion"].items())),
            )
            if first is None:
                first = key
            else:
                assert key == first


class TestStabilityIBoundary:
    def test_i_boundary_and_fan_in_three(self):
        # Hand oracle on a synthetic view (no DB): Ca/Ce exact.
        # f: Ca=3 Ce=0 I=0 foundation
        # g: Ca=3 Ce=1 I=0.25 not foundation (I>.15)
        # h: Ca=2 Ce=0 I=0 not foundation (Ca<3)
        # iso: Ca=0 Ce=0 I=0 never stable
        view = {
            "status": "ok",
            "included_files": ["core/f.py", "core/g.py", "core/h.py", "iso.py"],
            "test_files": [],
            "observed_edges": [
                {"src": "a.py", "dst": "core/f.py", "confidence": 1.0},
                {"src": "b.py", "dst": "core/f.py", "confidence": 1.0},
                {"src": "c.py", "dst": "core/f.py", "confidence": 1.0},
                {"src": "a.py", "dst": "core/g.py", "confidence": 1.0},
                {"src": "b.py", "dst": "core/g.py", "confidence": 1.0},
                {"src": "c.py", "dst": "core/g.py", "confidence": 1.0},
                {"src": "core/g.py", "dst": "core/f.py", "confidence": 1.0},
                {"src": "a.py", "dst": "core/h.py", "confidence": 1.0},
                {"src": "b.py", "dst": "core/h.py", "confidence": 1.0},
            ],
            "strict_edges": [],
            "provenance": {"root": "mem"},
        }
        # a/b/c are not in included => those inbound edges drop. Rebuild with
        # inbound files inside the universe.
        view["included_files"] = [
            "core/f.py", "core/g.py", "core/h.py", "iso.py",
            "in/a.py", "in/b.py", "in/c.py",
        ]
        view["observed_edges"] = [
            {"src": "in/a.py", "dst": "core/f.py", "confidence": 1.0},
            {"src": "in/b.py", "dst": "core/f.py", "confidence": 1.0},
            {"src": "in/c.py", "dst": "core/f.py", "confidence": 1.0},
            {"src": "in/a.py", "dst": "core/g.py", "confidence": 1.0},
            {"src": "in/b.py", "dst": "core/g.py", "confidence": 1.0},
            {"src": "in/c.py", "dst": "core/g.py", "confidence": 1.0},
            {"src": "core/g.py", "dst": "core/f.py", "confidence": 1.0},
            {"src": "in/a.py", "dst": "core/h.py", "confidence": 1.0},
            {"src": "in/b.py", "dst": "core/h.py", "confidence": 1.0},
        ]
        payload = compute_stability(view)
        by_file = {row["file"]: row for row in payload["files"]}
        assert FOUNDATION_I_MAX == 0.15
        assert FOUNDATION_CA_MIN == 3
        assert by_file["core/f.py"]["ca"] == 4  # a,b,c,g
        assert by_file["core/f.py"]["ce"] == 0
        assert by_file["core/f.py"]["instability"] == 0.0
        assert by_file["core/f.py"]["foundation"] is True
        assert by_file["core/g.py"]["ca"] == 3
        assert by_file["core/g.py"]["ce"] == 1
        assert abs(by_file["core/g.py"]["instability"] - 0.25) < 1e-12
        assert by_file["core/g.py"]["foundation"] is False
        assert by_file["core/g.py"]["stable"] is True  # cascade: only depends on f
        assert by_file["core/h.py"]["ca"] == 2
        assert by_file["core/h.py"]["foundation"] is False
        assert by_file["iso.py"]["ca"] == 0
        assert by_file["iso.py"]["instability"] == 0.0
        assert by_file["iso.py"]["foundation"] is False
        assert by_file["iso.py"]["stable"] is False


class TestStabilityCohesion:
    def test_spanning_n_minus_1_bounded(self):
        # folder pkg/ has 3 files, 2 intra edges = spanning tree => score 1.0
        view = {
            "status": "ok",
            "included_files": ["pkg/a.py", "pkg/b.py", "pkg/c.py", "other/x.py"],
            "test_files": [],
            "observed_edges": [
                {"src": "pkg/a.py", "dst": "pkg/b.py", "confidence": 1.0},
                {"src": "pkg/b.py", "dst": "pkg/c.py", "confidence": 1.0},
                {"src": "pkg/a.py", "dst": "other/x.py", "confidence": 1.0},
            ],
            "provenance": {"root": "mem"},
        }
        payload = compute_stability(view)
        assert payload["cohesion"]["pkg"]["n"] == 3
        assert payload["cohesion"]["pkg"]["intra_pairs"] == 2
        assert payload["cohesion"]["pkg"]["score"] == 1.0
        assert payload["cohesion"]["other"]["n"] == 1
        assert payload["cohesion"]["other"]["score"] == 1.0
        assert payload["cohesion"]["other"]["note"] == "trivial_group"
        # extra intra edge still caps at 1
        view["observed_edges"].append(
            {"src": "pkg/a.py", "dst": "pkg/c.py", "confidence": 1.0}
        )
        payload2 = compute_stability(view)
        assert payload2["cohesion"]["pkg"]["intra_pairs"] == 3
        assert payload2["cohesion"]["pkg"]["score"] == 1.0


class TestEntryReach:
    def test_absent_entry_unknown(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "noentry",
            {"a.py": "x=1\n"},
            [],
        )
        view = _view(root, ["a.py"])
        missing = compute_entry_reach(view, [])
        assert missing["status"] == "unavailable"
        assert missing["reason"] == "unknown"
        assert missing["reachable_files"] == []
        absent = compute_entry_reach(view, ["missing.py"])
        assert absent["status"] == "unavailable"
        assert absent["reason"] == "unknown"

    def test_entry_to_a_excludes_disconnected(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "reach",
            {
                "entry.py": "import a\n",
                "a.py": "x=1\n",
                "c.py": "y=1\n",
            },
            [("entry.py", "a.py", 1.0)],
        )
        view = _view(root, ["entry.py", "a.py", "c.py"])
        payload = compute_entry_reach(view, ["entry.py"])
        assert payload["status"] in {"ok", "partial"}
        assert payload["reachable_files"] == ["a.py", "entry.py"]
        assert payload["disconnected_excluded"] == ["c.py"]
