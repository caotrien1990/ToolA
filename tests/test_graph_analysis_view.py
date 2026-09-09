"""AC-P2-S1: readonly analysis_view projection fixtures."""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import store as gstore
from orchestrator.graph import status as gstatus
from orchestrator.graph.analysis_view import (
    OBSERVED_MIN,
    STRICT_MIN,
    build_analysis_view,
    finalize_included_scope_v1,
)
from orchestrator.graph.config import graph_db_path


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _hash_bytes(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


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


class TestAnalysisViewRoots:
    def test_explicit_roots_keep_sorted_identity(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "roots",
            {"b.py": "x=1\n", "a.py": "import b\n"},
            [("a.py", "b.py", 1.0)],
        )
        payload = _view(root, ["B.py", "a.py"])
        assert payload["status"] in {"ok", "partial"}
        assert payload["included_files"] == ["a.py", "B.py"] or payload["included_files"] == ["a.py", "b.py"]
        assert payload["provenance"]["included_set_hash"]
        other = _seed(
            tmp_path, monkeypatch, "roots2",
            {"only.py": "x=1\n"},
            [],
        )
        p2 = _view(other, ["only.py"])
        assert p2["cache_key"] != payload["cache_key"]
        assert p2["provenance"]["root"] != payload["provenance"]["root"]


class TestAnalysisViewSymlinkEscape:
    def test_symlink_escape_rejected_not_traversed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GRAPH_ENABLED", "1")
        outside = tmp_path / "outside"
        outside.mkdir()
        _write(outside, "secret.py", "LEAK=1\n")
        root = tmp_path / "proj"
        root.mkdir()
        _write(root, "orchestrator/ok.py", "x=1\n")
        _write(root, "orchestrator_server.py", "import orchestrator.ok\n")
        _write(root, "code_tools.py", "x=1\n")
        _write(root, "tools_v15.py", "x=1\n")
        leak = root / "orchestrator" / "leak"
        linked = False
        try:
            leak.symlink_to(outside, target_is_directory=True)
            linked = True
        except OSError:
            pass
        if not linked:
            def _forced(path: str):
                if path.replace("\\", "/").endswith("orchestrator/escape.py"):
                    return str(outside / "secret.py"), None
                try:
                    return os.path.realpath(path), None
                except OSError:
                    return None, "unresolved_target"
            _write(root, "orchestrator/escape.py", "x=1\n")
            monkeypatch.setattr(
                "orchestrator.graph.analysis_view._resolve_physical", _forced
            )
        scope = finalize_included_scope_v1(str(root))
        rels = [p.replace("\\", "/") for p in scope["included_files"]]
        assert "orchestrator/ok.py" in rels
        assert not any(p.endswith("secret.py") for p in rels)
        reasons = [d.get("reason") for d in scope["diagnostics"]]
        assert "symlink_escape" in reasons or "unresolved_target" in reasons


class TestAnalysisViewIsolated:
    def test_isolated_file_stays_in_denominator(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "iso",
            {"a.py": "import b\n", "b.py": "x=1\n", "lonely.py": "x=1\n"},
            [("a.py", "b.py", 1.0)],
        )
        payload = _view(root, ["a.py", "b.py", "lonely.py"])
        assert payload["status"] != "unavailable"
        assert "lonely.py" in payload["included_files"]
        assert "lonely.py" in payload["isolated_files"]
        assert payload["counts"]["files"] == 3
        assert payload["counts"]["isolated_files"] == 1


class TestAnalysisViewDuplicateAndSelf:
    def test_duplicate_pairs_dedup_keep_max_confidence(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "dup",
            {"a.py": "import b\nimport b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 0.3), ("a.py", "b.py", 0.9)],
        )
        payload = _view(root, ["a.py", "b.py"])
        pairs = [(e["src"], e["dst"]) for e in payload["observed_edges"]]
        assert pairs.count(("a.py", "b.py")) == 1
        edge = payload["observed_edges"][0]
        assert edge["confidence"] == 0.9
        assert payload["counts"]["dropped_duplicate"] >= 1

    def test_self_edges_dropped(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "self",
            {"a.py": "from . import a\n", "b.py": "x=1\n"},
            [("a.py", "a.py", 1.0), ("a.py", "b.py", 1.0)],
        )
        payload = _view(root, ["a.py", "b.py"])
        pairs = {(e["src"], e["dst"]) for e in payload["observed_edges"]}
        assert ("a.py", "a.py") not in pairs
        assert ("a.py", "b.py") in pairs
        assert payload["counts"]["dropped_self"] >= 1


class TestAnalysisViewConfidenceBuckets:
    def test_confidence_0_3_observed_only(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "c03",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 0.3)],
        )
        payload = _view(root, ["a.py", "b.py"])
        assert payload["observed_edges"][0]["confidence"] == 0.3
        assert payload["strict_edges"] == []
        assert OBSERVED_MIN == 0.3

    def test_confidence_0_7_observed_not_resolver(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "c07",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 0.7)],
        )
        payload = _view(root, ["a.py", "b.py"])
        edge = payload["observed_edges"][0]
        assert edge["confidence"] == 0.7
        assert payload["strict_edges"] == []
        assert "resolver" not in edge
        policy = payload["provenance"]["confidence_policy"]
        assert policy["note_no_resolver_inference_from_0_7"] is True
        assert policy["note_confidence_not_probability"] is True

    def test_confidence_0_9_strict_and_observed(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "c09",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 0.9)],
        )
        payload = _view(root, ["a.py", "b.py"])
        assert payload["observed_edges"][0]["confidence"] == 0.9
        assert payload["strict_edges"][0]["confidence"] == 0.9
        assert STRICT_MIN == 0.9

    def test_confidence_1_0_exact_strict(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "c10",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 1.0)],
        )
        payload = _view(root, ["a.py", "b.py"])
        assert payload["observed_edges"][0]["confidence"] == 1.0
        assert payload["strict_edges"][0]["confidence"] == 1.0


class TestAnalysisViewUnknownNeverPass:
    def test_unknown_freshness_unavailable_never_pass(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "unk",
            {"a.py": "x=1\n"},
            [],
        )

        def _boom(_conn, _root):
            raise RuntimeError("forced-unknown")

        monkeypatch.setattr(gstatus, "_stale_count", _boom)
        payload = _view(root, ["a.py"])
        assert payload["status"] == "unavailable"
        assert payload["pass"] is False
        assert payload["score"] is None
        assert str(payload.get("reason") or "").startswith("freshness:unknown")

    def test_missing_db_unavailable_no_create(self, tmp_path, monkeypatch):
        ghost = tmp_path / "ghost_db"
        monkeypatch.setenv("GRAPH_DB_DIR", str(ghost))
        monkeypatch.setenv("GRAPH_ENABLED", "1")
        root = tmp_path / "empty_root"
        root.mkdir()
        payload = _view(str(root), ["a.py"])
        assert payload["status"] == "unavailable"
        assert payload["pass"] is False
        assert payload["score"] is None
        assert not ghost.exists()
        assert payload.get("created_missing_db") in (False, None) or not os.path.exists(
            graph_db_path(str(root))
        )
        assert not (root / ".orchestrator_cache").exists()


class TestAnalysisViewReadonlyHash:
    def test_before_after_db_hash_equal_without_writer(self, tmp_path, monkeypatch):
        root = _seed(
            tmp_path, monkeypatch, "hash",
            {"a.py": "import b\n", "b.py": "x=1\n"},
            [("a.py", "b.py", 1.0)],
        )
        db = graph_db_path(root)
        before = _hash_bytes(db)
        payload = _view(root, ["a.py", "b.py"])
        after = _hash_bytes(db)
        assert before == after
        assert payload["status"] != "unavailable"
        assert payload.get("created_missing_db") is False
