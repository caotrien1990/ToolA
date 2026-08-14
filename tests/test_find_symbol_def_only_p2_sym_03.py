"""P2-SYM-03 regression (2026-08-11): find_symbol(include='def') must return
DEFINITIONS ONLY and stay bounded — no usages/tests leaking through the graph
adapter, and the response must carry truncation metadata when capped.

Why: the live graph adapter (find_symbol_as_search_response in
orchestrator/graph/query.py) was returning every matching node kind
(function/method/class/module/import/variable) unfiltered and unbounded.
An include='def' query for a common name (e.g. 'run') therefore returned
import-binding sites and variable-assignment sites alongside the real
definitions, and — because there was no result cap or truncation tag — the
response grew to ~849KB on a large project (observed live).

The fix (a) filters candidates to definition-shaped kinds (function/method/
class) at COLLECTION time — before any entries are shaped — and (b) caps the
emitted results while tagging the response with truncated/total_count/
truncation_reason so callers can detect a bounded response. Body context
(signature/docstring) is also capped per-entry so a large context_lines
request cannot blow past the response budget.

These tests index a real throwaway project (one definition + an import site
+ a call site) and assert the graph adapter returns ONLY the definition with
the truncation contract intact.
"""
from __future__ import annotations

import os

os.environ["GRAPH_ENABLED"] = "1"

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestFindSymbolDefOnlyP2Sym03(unittest.TestCase):
    """End-to-end: index a project, then call find_symbol_as_search_response
    and assert only definition-shaped kinds are returned with bounding."""

    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = self.root / "proj"
        self.project.mkdir()
        self.graph_dir = self.root / "graphdb"
        self.graph_dir.mkdir()
        # One real definition plus an import site and a call site that all
        # mention the same name. Pre-fix, the import and (for non-Python
        # extractors) module nodes also matched the FTS / exact-name query
        # and were returned alongside the definition.
        (self.project / "mod.py").write_text(
            "def target_fn(x):\n"
            "    return x + 1\n",
            encoding="utf-8",
        )
        (self.project / "user_mod.py").write_text(
            "from mod import target_fn\n"
            "\n"
            "def caller():\n"
            "    return target_fn(41)\n",
            encoding="utf-8",
        )
        # Isolate the graph DB per-test (the indexer caches connections by
        # project path; using GRAPH_DB_DIR forces a fresh file).
        self._old_db_dir = os.environ.get("GRAPH_DB_DIR")
        os.environ["GRAPH_DB_DIR"] = str(self.graph_dir)

        from orchestrator.graph.indexer import index_project
        result = index_project(str(self.project).replace("\\", "/"), scope="full")
        assert result["status"] == "completed", result

    def tearDown(self) -> None:
        if self._old_db_dir is None:
            os.environ.pop("GRAPH_DB_DIR", None)
        else:
            os.environ["GRAPH_DB_DIR"] = self._old_db_dir
        self._tmp.cleanup()

    def test_def_only_kinds_returned(self) -> None:
        from orchestrator.graph.query import find_symbol_as_search_response
        raw = find_symbol_as_search_response(
            "target_fn", str(self.project).replace("\\", "/")
        )
        self.assertIsNotNone(raw, "graph adapter returned None — no results")
        data = json.loads(raw)
        results = data.get("results", [])
        # We indexed ONE function definition + ONE import binding for the
        # same name. The import node (kind='import') must NOT leak into
        # include='def' results — only the function definition.
        self.assertGreaterEqual(len(results), 1, f"no def found: {data}")
        kinds_in_response = set()
        for r in results:
            # Entries don't carry kind directly; match string starts with
            # the kind tag we emit ("<kind> <name> -> <qname>").
            first_token = r.get("match", "").split(" ", 1)[0]
            kinds_in_response.add(first_token)
        # No 'import' or 'module' or 'variable' kinds may leak through.
        self.assertNotIn("import", kinds_in_response,
                         f"import-binding node leaked into include='def': {results}")
        self.assertNotIn("module", kinds_in_response,
                         f"module node leaked into include='def': {results}")
        self.assertNotIn("variable", kinds_in_response,
                         f"variable node leaked into include='def': {results}")
        # All kinds must be definition-shaped.
        self.assertTrue(
            kinds_in_response <= {"function", "method", "class"},
            f"unexpected kinds: {kinds_in_response}",
        )

    def test_response_has_truncation_metadata_keys(self) -> None:
        """The bounded-response contract: even when not truncated, the
        response must expose 'truncated' + 'total_count' so callers can
        reason about response size."""
        from orchestrator.graph.query import find_symbol_as_search_response
        raw = find_symbol_as_search_response(
            "target_fn", str(self.project).replace("\\", "/")
        )
        self.assertIsNotNone(raw)
        data = json.loads(raw)
        self.assertIn("truncated", data)
        self.assertIn("total_count", data)
        self.assertIn("count", data)
        self.assertIsInstance(data["truncated"], bool)
        # When NOT truncated, total_count == count.
        if not data["truncated"]:
            self.assertEqual(data["total_count"], data["count"])

    def test_response_bounded_with_many_defs(self) -> None:
        """Force > result_cap definitions of the same name into the graph
        and assert the adapter caps + tags truncated=True."""
        import time
        from orchestrator.graph.config import graph_db_path
        from orchestrator.graph.query import find_symbol_as_search_response

        db_path = graph_db_path(str(self.project).replace("\\", "/"))
        conn = sqlite3.connect(db_path)
        try:
            # Insert 60 fake 'function' nodes all named 'run'. They need
            # minimal columns to satisfy the SELECT * shape the adapter
            # reads (id, kind, name, qualified_name, file_path, start_line).
            # updated_at is NOT NULL in the schema; fill it with a fresh
            # timestamp so the INSERT is accepted. The graph_nodes_ai
            # trigger propagates each row to graph_symbols_fts so the
            # exact-name / FTS lookups find them.
            cur = conn.execute("PRAGMA table_info(nodes)")
            cols = [r[1] for r in cur.fetchall()]
            now_ms = int(time.time() * 1000)
            for i in range(60):
                nid = f"fake{i}.py:function:fake{i}.run:1"
                row = {c: None for c in cols}
                row.update({
                    "id": nid,
                    "kind": "function",
                    "name": "run",
                    "qualified_name": f"fake{i}.run",
                    "file_path": f"fake{i}.py",
                    "language": "python",
                    "start_line": 1,
                    "end_line": 1,
                    "visibility": "public",
                    "updated_at": now_ms,
                })
                if "docstring" in cols:
                    row["docstring"] = ""
                if "signature" in cols:
                    row["signature"] = ""
                if "decorators" in cols:
                    row["decorators"] = "[]"
                placeholders = ",".join("?" for _ in cols)
                conn.execute(
                    f"INSERT INTO nodes ({','.join(cols)}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
            conn.commit()
        finally:
            conn.close()

        raw = find_symbol_as_search_response(
            "run", str(self.project).replace("\\", "/"), limit=50
        )
        self.assertIsNotNone(raw, "expected graph adapter to return defs for 'run'")
        data = json.loads(raw)
        # Bounded: count must not exceed the cap (50), and truncated=True
        # must be set because total population exceeds the cap.
        self.assertLessEqual(data["count"], 50,
                             f"count exceeds cap: {data['count']}")
        self.assertTrue(data["truncated"],
                        f"expected truncated=True, got: {data}")
        self.assertIn("truncation_reason", data)
        self.assertEqual(data["truncation_reason"], "result_cap")
        # total_count reflects the true population BEFORE capping.
        self.assertGreater(data["total_count"], data["count"])
        # And the serialized response must stay well under the 849KB
        # regression budget — a 50-entry list of single-line matches
        # should be O(KB), not O(MB).
        self.assertLess(len(raw), 200_000,
                        f"response too large: {len(raw)} bytes")

    def test_context_lines_body_bounded(self) -> None:
        """Even with a large context_lines request, each entry's 'body'
        must be capped so the overall response stays bounded."""
        import time
        from orchestrator.graph.config import graph_db_path
        from orchestrator.graph.query import find_symbol_as_search_response

        # Insert a def with an oversized docstring to prove the per-entry
        # body cap works.
        db_path = graph_db_path(str(self.project).replace("\\", "/"))
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute("PRAGMA table_info(nodes)")
            cols = [r[1] for r in cur.fetchall()]
            row = {c: None for c in cols}
            row.update({
                "id": "big.py:function:big.bigdoc:1",
                "kind": "function",
                "name": "bigdoc",
                "qualified_name": "big.bigdoc",
                "file_path": "big.py",
                "language": "python",
                "start_line": 1,
                "end_line": 1,
                "visibility": "public",
                "updated_at": int(time.time() * 1000),
            })
            if "docstring" in cols:
                row["docstring"] = "x" * 50_000
            if "signature" in cols:
                row["signature"] = ""
            if "decorators" in cols:
                row["decorators"] = "[]"
            placeholders = ",".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO nodes ({','.join(cols)}) VALUES ({placeholders})",
                [row[c] for c in cols],
            )
            conn.commit()
        finally:
            conn.close()

        raw = find_symbol_as_search_response(
            "bigdoc", str(self.project).replace("\\", "/"), context_lines=50
        )
        self.assertIsNotNone(raw)
        data = json.loads(raw)
        self.assertEqual(data["count"], 1)
        body = data["results"][0].get("body", "")
        # Per-entry body is capped at ~2000 chars + the truncation tag.
        self.assertLessEqual(len(body), 2100,
                             f"body not capped: {len(body)} chars")


if __name__ == "__main__":
    unittest.main()
