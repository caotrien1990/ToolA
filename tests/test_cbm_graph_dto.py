"""cbm.graph/v1 DTO contract tests (CD-P1 bridge, 2026-08-11).

Invariant under test (WHY): the DTO envelope is the frozen contract between
ToolA (structural graph owner) and ToolQ (semantic memory owner). P2/P3 will
consume this envelope for graph-anchored candidate supply. Freezing the shape
NOW prevents drift when P2/P3 land.

Covers:
  - Envelope is versioned (dto == "cbm.graph/v1").
  - Envelope contains symbols, edges, provenance keys.
  - Symbols carry name/kind/file/line_start/line_end/qualified_name.
  - Edges carry source_symbol/target_symbol/kind/file.
  - Provenance carries index_tool, commit_or_mtime, counts.
  - GRAPH_ENABLED=0 returns an empty envelope with error marker (not raise).
  - Additive: normalization never destroys ToolA's internal fields.
"""
import os
os.environ["GRAPH_ENABLED"] = "1"  # must precede orchestrator.graph.config import

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph.indexer import index_project
from orchestrator.graph.dto import DTO_VERSION, normalize_graph_dto

FIX_MOD = '''import os


def alpha():
    return beta()


def beta():
    return 1


class Widget:
    def render(self):
        return alpha()
'''


class TestCbmGraphV1Dto(unittest.TestCase):
    """DTO envelope shape and content contract."""

    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="dto_v1_"))
        (self.tempdir / "mod.py").write_text(FIX_MOD, encoding="utf-8")
        self.proj = str(self.tempdir).replace("\\", "/")
        index_project(self.proj, scope="full")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_envelope_version_and_top_level_keys(self):
        """The envelope MUST carry dto='cbm.graph/v1' and the three required keys."""
        env = normalize_graph_dto(self.proj)
        self.assertEqual(env["dto"], DTO_VERSION)
        self.assertEqual(env["dto"], "cbm.graph/v1")
        self.assertIn("generated_for", env)
        self.assertIn("symbols", env)
        self.assertIn("edges", env)
        self.assertIn("provenance", env)
        self.assertIsInstance(env["symbols"], list)
        self.assertIsInstance(env["edges"], list)
        self.assertIsInstance(env["provenance"], dict)

    def test_envelope_symbols_shape(self):
        """Each symbol must have the six DTO fields."""
        env = normalize_graph_dto(self.proj)
        self.assertGreater(len(env["symbols"]), 0, "expected at least one symbol")
        for sym in env["symbols"]:
            for field in ("name", "kind", "file", "line_start", "line_end",
                          "qualified_name"):
                self.assertIn(field, sym, f"symbol missing field {field}")
            self.assertIsInstance(sym["name"], str)
            self.assertIsInstance(sym["kind"], str)
            self.assertIsInstance(sym["file"], str)
            self.assertIsInstance(sym["line_start"], int)
            self.assertIsInstance(sym["line_end"], int)
            self.assertIsInstance(sym["qualified_name"], str)

    def test_envelope_edges_shape(self):
        """Each edge must have the four DTO fields."""
        env = normalize_graph_dto(self.proj)
        self.assertGreater(len(env["edges"]), 0, "expected at least one edge")
        for edge in env["edges"]:
            for field in ("source_symbol", "target_symbol", "kind", "file"):
                self.assertIn(field, edge, f"edge missing field {field}")
            self.assertIsInstance(edge["source_symbol"], str)
            self.assertIsInstance(edge["target_symbol"], str)
            self.assertIsInstance(edge["kind"], str)
            self.assertIsInstance(edge["file"], str)

    def test_envelope_provenance_shape(self):
        """Provenance must carry index_tool, commit_or_mtime, counts."""
        env = normalize_graph_dto(self.proj)
        prov = env["provenance"]
        self.assertEqual(prov["index_tool"], "cbm_index")
        self.assertIn("commit_or_mtime", prov)
        self.assertIn("counts", prov)
        counts = prov["counts"]
        self.assertIn("symbols", counts)
        self.assertIn("edges", counts)
        self.assertIn("files", counts)
        self.assertIsInstance(counts["symbols"], int)
        self.assertIsInstance(counts["edges"], int)
        self.assertIsInstance(counts["files"], int)
        self.assertEqual(counts["symbols"], len(env["symbols"]))
        self.assertEqual(counts["edges"], len(env["edges"]))

    def test_envelope_known_symbols_present(self):
        """Sanity: known fixture symbols appear in the DTO output."""
        env = normalize_graph_dto(self.proj)
        names = {s["name"] for s in env["symbols"]}
        for expected in ("alpha", "beta", "Widget", "render"):
            self.assertIn(expected, names,
                          f"expected symbol {expected} in DTO")

    def test_envelope_known_edges_present(self):
        """Sanity: at least one calls edge exists in the DTO output."""
        env = normalize_graph_dto(self.proj)
        kinds = {e["kind"] for e in env["edges"]}
        self.assertIn("calls", kinds, "expected at least one calls edge")

    def test_edge_endpoints_carry_qualified_name_anchors(self):
        """CDP1-01: edge source_symbol/target_symbol must be qualified_name
        anchors (e.g. 'mod.alpha'), not raw internal node IDs.

        The fixture (mod.py) defines alpha/beta/Widget; the indexer emits
        nodes whose qualified_name is 'mod.<name>' and edges whose endpoints
        are internal IDs like 'mod.py:function:mod.alpha:4'.  The DTO must
        resolve those IDs to the semantic qualified_name.
        """
        env = normalize_graph_dto(self.proj)
        edges = env["edges"]
        self.assertGreater(len(edges), 0, "expected at least one edge")

        # Collect the set of qualified_names present in the symbols list —
        # any non-external edge endpoint must be in this set.
        symbol_qnames = {s["qualified_name"] for s in env["symbols"]}

        for edge in edges:
            src = edge["source_symbol"]
            tgt = edge["target_symbol"]
            # An endpoint is either a resolved qualified_name (found in
            # symbols) or an external reference (starts with "ext:").
            if not src.startswith("ext:"):
                self.assertIn(src, symbol_qnames,
                              f"edge source_symbol {src!r} not a known "
                              f"qualified_name")
            if not tgt.startswith("ext:"):
                self.assertIn(tgt, symbol_qnames,
                              f"edge target_symbol {tgt!r} not a known "
                              f"qualified_name")

        # Concrete assertion: the fixture's known calls edges must use
        # qualified_name anchors.  alpha calls beta; Widget.render calls alpha.
        calls = [e for e in edges if e["kind"] == "calls"]
        self.assertGreater(len(calls), 0, "expected at least one calls edge")
        call_pairs = {(e["source_symbol"], e["target_symbol"])
                      for e in calls}
        # The module is named 'mod' (from mod.py), so qualified_names are
        # 'mod.alpha', 'mod.beta', 'mod.Widget.render'.
        self.assertIn(("mod.alpha", "mod.beta"), call_pairs,
                      f"expected calls edge ('mod.alpha', 'mod.beta'), "
                      f"got: {call_pairs}")
        self.assertIn(("mod.Widget.render", "mod.alpha"), call_pairs,
                      f"expected calls edge ('mod.Widget.render', 'mod.alpha'), "
                      f"got: {call_pairs}")

    def test_envelope_generated_for_matches_project(self):
        env = normalize_graph_dto(self.proj)
        self.assertEqual(env["generated_for"], self.proj)

    def test_envelope_no_error_on_valid_project(self):
        env = normalize_graph_dto(self.proj)
        self.assertIsNone(env["provenance"]["error"],
                          f"unexpected error: {env['provenance']['error']}")


class TestCbmGraphV1DtoDisabled(unittest.TestCase):
    """GRAPH_ENABLED=0 must return a valid but empty envelope (never raise)."""

    def test_disabled_returns_empty_envelope(self):
        # We need to test with GRAPH_ENABLED=0. Since the config module reads
        # the env at import time, we patch the imported constant directly.
        import orchestrator.graph.dto as dto_mod
        original = dto_mod.GRAPH_ENABLED
        try:
            dto_mod.GRAPH_ENABLED = False
            env = dto_mod.normalize_graph_dto("D:/nonexistent")
            self.assertEqual(env["dto"], "cbm.graph/v1")
            self.assertEqual(env["symbols"], [])
            self.assertEqual(env["edges"], [])
            self.assertEqual(env["provenance"]["error"], "GRAPH_ENABLED=0")
            self.assertFalse(env["provenance"]["graph_enabled"])
        finally:
            dto_mod.GRAPH_ENABLED = original

    def test_disabled_include_disabled_false(self):
        import orchestrator.graph.dto as dto_mod
        original = dto_mod.GRAPH_ENABLED
        try:
            dto_mod.GRAPH_ENABLED = False
            env = dto_mod.normalize_graph_dto("D:/nonexistent",
                                               include_disabled=False)
            self.assertEqual(env["dto"], "cbm.graph/v1")
            self.assertEqual(env["symbols"], [])
            self.assertEqual(env["edges"], [])
            # include_disabled=False: no error marker, just empty
            self.assertIsNone(env["provenance"]["error"])
        finally:
            dto_mod.GRAPH_ENABLED = original


class TestReadonlyGetDbSideEffectFree(unittest.TestCase):
    """CDP1-R02: get_db(readonly=True) MUST NOT create the parent directory.

    WHY: a "read-only" call must be side-effect-free.  Before the fix the
    helper ran ``os.makedirs(parent, exist_ok=True)`` unconditionally, so
    an enabled-graph DTO request against a project without
    ``.orchestrator_cache`` created a directory on disk before raising
    ``FileNotFoundError``.  Non-readonly callers (indexer, init_db,
    reset_db) keep the lazy-creation behavior unchanged.
    """

    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="dto_ro_noside_"))
        # Point at a project root that does NOT contain .orchestrator_cache.
        self.proj = str(self.tempdir / "no_such_project").replace("\\", "/")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _tree_snapshot(self) -> set:
        """Snapshot every dir/file under tempdir so we can detect changes."""
        return {p.relative_to(self.tempdir) for p in self.tempdir.rglob("*")}

    def test_readonly_get_db_does_not_create_parent_dir(self):
        from orchestrator.graph import store
        before = self._tree_snapshot()
        with self.assertRaises(FileNotFoundError):
            store.get_db(self.proj, readonly=True)
        after = self._tree_snapshot()
        self.assertEqual(before, after,
                         f"readonly get_db mutated the filesystem: "
                         f"added={sorted(str(p) for p in after - before)}")

    def test_normalize_graph_dto_missing_db_does_not_create_dirs(self):
        """End-to-end: DTO normalize on a project with no graph.db must
        return the fail-open envelope AND leave the filesystem unchanged."""
        before = self._tree_snapshot()
        env = normalize_graph_dto(self.proj)
        after = self._tree_snapshot()
        self.assertEqual(env["dto"], DTO_VERSION)
        self.assertEqual(env["symbols"], [])
        self.assertEqual(env["edges"], [])
        # Fail-open: the FileNotFoundError from get_db is captured, not raised.
        self.assertIsNotNone(env["provenance"]["error"])
        self.assertIn("FileNotFoundError", env["provenance"]["error"])
        self.assertEqual(before, after,
                         f"normalize_graph_dto mutated the filesystem: "
                         f"added={sorted(str(p) for p in after - before)}")

    def test_non_readonly_get_db_still_creates_parent_dir(self):
        """Guard the OTHER direction: the non-readonly path keeps its
        lazy-create behavior (indexer/init_db depend on it)."""
        from orchestrator.graph import store
        conn = store.get_db(self.proj, readonly=False)
        try:
            # Parent dir now exists.
            self.assertTrue((Path(self.proj) / ".orchestrator_cache").is_dir())
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
