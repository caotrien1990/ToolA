import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.tools.search import search_code


class TestSearchToolMode(unittest.IsolatedAsyncioTestCase):
    """Mode-matrix coverage for search_code (mode axis directly).

    Distinct from test_search_tool_alias_compat.py, which covers legacy
    TOOL_MAP alias names. Here we exercise the `mode` parameter of the
    async search_code callable against a small tempdir fixture.
    """

    def _write_tree(self, root):
        (root / "mod_a.py").write_text(
            "def target_fn(x):\n"
            "    return x + 1\n",
            encoding="utf-8",
        )
        (root / "mod_b.py").write_text(
            "from mod_a import target_fn\n"
            "\n"
            "def caller():\n"
            "    return target_fn(5)\n",
            encoding="utf-8",
        )

    async def test_text_mode_finds_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_tree(root)
            raw = await search_code(query="target_fn", path=str(root), mode="text")
            d = json.loads(raw)
            self.assertIsInstance(d, dict)
            self.assertGreaterEqual(d.get("count", 0), 1)
            self.assertTrue(
                any("target_fn" in r.get("match", "") for r in d.get("results", [])),
                msg="text mode did not surface target_fn",
            )

    async def test_regex_mode_finds_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_tree(root)
            raw = await search_code(query=r"def \w+\(", path=str(root), mode="regex")
            d = json.loads(raw)
            self.assertIsInstance(d, dict)
            self.assertGreaterEqual(d.get("count", 0), 1)
            self.assertEqual(d.get("mode"), "regex")

    async def test_unsupported_mode_returns_unsupported_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_tree(root)
            raw = await search_code(query="x", path=str(root), mode="zzz")
            d = json.loads(raw)
            self.assertIsInstance(d, dict)
            self.assertFalse(d.get("ok", True))
            self.assertEqual(d.get("error_name"), "UNSUPPORTED_FIELD")
            self.assertIn("accepted_modes", d)

    async def test_symbol_mode_graceful_without_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_tree(root)
            raw = await search_code(query="target_fn", path=str(root), mode="symbol")
            d = json.loads(raw)
            self.assertIsInstance(d, dict)
            self.assertTrue("ok" in d or "results" in d)

    async def test_callers_mode_graceful_without_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_tree(root)
            raw = await search_code(query="target_fn", path=str(root), mode="callers")
            d = json.loads(raw)
            self.assertIsInstance(d, dict)
            self.assertTrue("ok" in d or "results" in d or "callers" in d)

    async def test_definition_usage_mode_graceful_without_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_tree(root)
            raw = await search_code(
                query="target_fn", path=str(root), mode="definition_usage"
            )
            d = json.loads(raw)
            self.assertIsInstance(d, dict)
            self.assertTrue("ok" in d or "results" in d)

    async def test_index_mode_missing_project_path(self):
        raw = await search_code(query="target_fn", mode="index")
        d = json.loads(raw)
        self.assertIsInstance(d, dict)
        self.assertFalse(d.get("ok", True))
        self.assertEqual(d.get("error_name"), "MISSING_REQUIRED_FIELD")

    async def test_dom_candidates_missing_page_id(self):
        raw = await search_code(mode="dom_candidates")
        d = json.loads(raw)
        self.assertIsInstance(d, dict)
        self.assertFalse(d.get("ok", True))
        self.assertEqual(d.get("error_name"), "MISSING_REQUIRED_FIELD")

    async def test_every_mode_returns_json_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_tree(root)
            pp = str(root)
            cases = [
                {"query": "target_fn", "path": pp, "mode": "text"},
                {"query": r"target_", "path": pp, "mode": "regex"},
                {"query": "target_fn", "path": pp, "mode": "symbol"},
                {"query": "target_fn", "path": pp, "mode": "callers"},
                {"query": "target_fn", "path": pp, "mode": "definition_usage"},
                {"query": "target_fn", "path": pp, "mode": "smart"},
                {"query": "target_fn", "mode": "index"},
                {"mode": "dom_candidates"},
                {"query": "x", "path": pp, "mode": "zzz"},
                {"query": "x", "path": pp, "mode": "base64"},
            ]
            for kwargs in cases:
                raw = await search_code(**kwargs)
                self.assertIsInstance(raw, str)
                d = json.loads(raw)
                self.assertIsInstance(d, dict)


if __name__ == "__main__":
    unittest.main()