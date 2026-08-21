"""B022: the drift check and the indexer must ask the same question.

`_cache_delta_sync` walked the tree calling `surgical_mapper._should_scan_file(path)`
with NO `project_root`; the indexer (`surgical_mapper.py:1134`) calls
`_should_scan_file(fp, project_root)` WITH it. `project_root` is what
`_skip_reason` needs to compute `_depth_below_root` for the `data_json_not_source`
rule, so without it the depth is `None` and R82's deliberate conservative default
applies -- `test_unknown_root_keeps_the_file`: "conservative: index it rather than
silently drop it".

That default is right for the INDEXER and inverted for the DRIFT CHECK. Keeping a
file means "there is a new file here", so every deep data-JSON the indexer refuses
was reported as `added` forever:

    measured on [A]'s own repo at generation 177faa8e5a45
      files where the two predicates disagree      : 52
      _cache_delta_sync                            : total_changed=53 added=52
      of those 52 `added`, refused by the indexer   : 52 / 52
      genuinely addable                            : 0

An unbreakable loop: drift reports 52 new files -> refresh -> the indexer refuses
all 52 -> drift reports 52 again. `query_index` gates hard on drift, so it answered
`STALE_INDEX_AFTER_REFRESH` permanently, and once the phantom refresh ate the whole
12s budget it answered `INDEX_QUERY_TIMEOUT` instead -- one cause wearing two faces.

The invariant these tests pin is not "the parameter is passed" but the consequence:
NO file the drift check calls `added` may be a file the indexer refuses.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ORCHESTRATOR_PARALLEL_INDEX", "off")

import code_tools as ct
import surgical_mapper as sm


# `build_index` only ASSEMBLES an index from cache sidecars that already exist --
# on a fresh tmp repo it returns 0 files and writes no INDEX.json, so every test
# below would have measured an empty index rather than the drift rule.
# `generate_project_map` is the entry point that walks, writes the sidecars, and
# publishes INDEX.json. Measured while writing these tests: build_index -> 0 files,
# generate_project_map -> 2 files, total_changed 0.


def _mk(root: Path, rel: str, body: str = "{}") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _repo(root: Path) -> Path:
    """A repo holding exactly the shape that made the loop: deep data JSON.

    R82's own example lives under `qsleep/runs/...`, but `runs` is in SKIP_DIRS so
    the walk never descends into it -- the file has to sit under a directory that
    is NOT skipped for the depth rule to be the thing under test.
    """
    _mk(root, "app.py", "def main():\n    return 1\n")
    _mk(root, "package.json", '{"name":"x"}')          # known config, any depth
    _mk(root, "data.json", '{"shallow":1}')            # shallow -> source by R82
    for i in range(4):
        _mk(root, "outputs/round0/case_%d/result.json" % i, '{"r":%d}' % i)
    _mk(root, "fixtures/deep/a/b/payload.json", '{"p":1}')
    return root


def _delta(root: Path) -> dict:
    return json.loads(ct._cache_delta_sync(str(root)))


def _refused(root: Path, rels) -> list:
    return [r for r in rels if sm._skip_reason(root / r, root) is not None]


class TestDriftAndIndexerAgree:

    def test_no_added_file_is_a_file_the_indexer_refuses(self, tmp_path):
        """The invariant. A file only the drift check counts can never be indexed."""
        root = _repo(tmp_path)
        sm.generate_project_map(str(root))
        d = _delta(root)
        phantom = _refused(root, d["added"])
        assert phantom == [], (
            "the drift check reported %d file(s) as `added` that the indexer "
            "refuses, so a refresh can never clear them: %s -- this is the "
            "STALE_INDEX_AFTER_REFRESH loop" % (len(phantom), phantom[:6]))

    def test_the_walk_admits_nothing_the_indexer_refuses(self, tmp_path):
        """The walk itself, measured against an EMPTY index.

        Not a duplicate of the invariant above. That one asks what drift reports
        against a REAL index, so it only sees files that happen to be missing
        from it; an index that already holds every phantom would hide the defect.
        Here `index_override={"files": {}}` makes `added` the walk's complete
        admit-list, so every file the walk lets through is checked -- the walk is
        the thing that has to ask the indexer's question.

        Deliberately NOT asserted: `_should_scan_file(p) == _should_scan_file(p, root)`.
        Those two must still disagree. R82's no-root default keeps the file on
        purpose (test_data_json_and_reindex_r82.py: "conservative: index it
        rather than silently drop it"), and that default is untouched. The defect
        was never in the mapper -- it was a caller not passing what the mapper
        needs.
        """
        root = _repo(tmp_path)
        admitted = json.loads(
            ct._cache_delta_sync(str(root), index_override={"files": {}}))["added"]
        assert admitted, "the walk admitted nothing at all; the fixture is empty"
        phantom = _refused(root, admitted)
        assert phantom == [], (
            "the drift walk admits %d file(s) the indexer refuses: %s -- the walk "
            "must pass project_root, because _skip_reason needs it for "
            "_depth_below_root" % (len(phantom), phantom[:6]))

    def test_a_fresh_index_is_reported_fresh(self, tmp_path):
        """Index, then immediately ask: total_changed must be 0."""
        root = _repo(tmp_path)
        sm.generate_project_map(str(root))
        d = _delta(root)
        assert d["total_changed"] == 0, (
            "a just-built index reported %d changes (added=%s modified=%s "
            "deleted=%s)" % (d["total_changed"], d["added"], d["modified"],
                             d["deleted"]))

    def test_refreshing_twice_converges(self, tmp_path):
        """The loop, reproduced as a test: drift must not survive a refresh."""
        root = _repo(tmp_path)
        sm.generate_project_map(str(root))
        first = _delta(root)
        sm.generate_project_map(str(root))
        second = _delta(root)
        assert (first["total_changed"], second["total_changed"]) == (0, 0), (
            "drift did not converge across two refreshes: %d then %d -- the "
            "second is what query_index reports as STALE_INDEX_AFTER_REFRESH"
            % (first["total_changed"], second["total_changed"]))

    def test_the_fixture_really_contains_files_the_rule_refuses(self, tmp_path):
        """Guard the guard: a fixture with nothing to refuse proves nothing.

        Without this, shrinking the fixture (or R82's depth threshold moving)
        would leave every test above passing vacuously.
        """
        root = _repo(tmp_path)
        refused = [rel for rel in (
            "outputs/round0/case_0/result.json",
            "fixtures/deep/a/b/payload.json",
        ) if sm._skip_reason(root / rel, root) == "data_json_not_source"]
        assert len(refused) == 2, (
            "the fixture no longer contains deep data-JSON the indexer refuses, "
            "so the invariant tests above cannot see the defect: %s" % refused)
        # and the counterexamples must still be indexable
        for rel in ("app.py", "package.json", "data.json"):
            assert sm._skip_reason(root / rel, root) is None, rel

    def test_a_genuinely_new_source_file_is_still_reported(self, tmp_path):
        """The fix must not silence real drift -- the opposite failure."""
        root = _repo(tmp_path)
        sm.generate_project_map(str(root))
        _mk(root, "added_later.py", "x = 1\n")
        d = _delta(root)
        assert "added_later.py" in d["added"], (
            "a real new source file was not reported as added: %s" % d["added"])


class TestQueryIndexClearsAfterRefresh:

    def test_query_index_does_not_answer_stale_after_a_refresh(self, tmp_path):
        """End-to-end at the layer the real test hit."""
        root = _repo(tmp_path)
        sm.generate_project_map(str(root))
        raw = asyncio.run(ct.query_index(str(root), query="main",
                                         auto_refresh=True))
        r = json.loads(raw)
        assert r.get("error_name") != "STALE_INDEX_AFTER_REFRESH", (
            "query_index still reports STALE_INDEX_AFTER_REFRESH on a freshly "
            "indexed repo; drift=%s" % json.dumps(r.get("drift"))[:300])
        assert r.get("ok") is True, (
            "query_index failed on a fresh index: %s %s"
            % (r.get("error_name"), str(r.get("error"))[:200]))
