"""Changing an extractor without bumping EXTRACTOR_VERSION ships a no-op.

B013 fixed the JS import shape (`./lib/util.js.helper` -> `lib.util.helper`) and
added `require()` extraction. Both live in the extractor, and `stale_files`
re-extracts a file only when its content changed OR its `extractor_version` row
differs from `config.EXTRACTOR_VERSION`. The source did not change, so on every
already-indexed repo a *full* index answered `files_unchanged: 41`,
`dependency_edges: 0` -- the fix silently did not apply.

Measured on the reported repo after the restart that made the new code live:

    cbm_index(scope="full") -> completed, files_unchanged: 41, files_pruned: 0
    cbm_template_query(module_deps) -> 0 rows

store.py's own docstring had already recorded this exact failure once ("Shipping
the W5 extractor left 241 of 250 [A] files frozen at the pre-`name_literal`
shape through a *full* reindex, because every hash still matched"), so the
mechanism was there and the bump was the step that got skipped.

These tests do not check *which* version string is current -- that would need
editing on every legitimate bump. They check the two things that must hold:

  1. the version recorded in a file's row is the version the code reports, so a
     stale row is detectable at all; and
  2. a JS file already indexed under an older version is re-extracted, and
     comes back with the NEW import shape rather than the path-shaped one.

Test 2 is the one that would have caught B013's incomplete rollout: it indexes
with a synthetic old version, asserts the broken shape is what a stale row
holds, then re-indexes under the real version and asserts the shape changed.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import config as gconfig
from orchestrator.graph import store as gstore
from orchestrator.graph import status as gstatus


JS_FILES = {
    "lib/util.js": "export function helper() { return 1; }\n",
    "app.js": "import { helper } from './lib/util.js';\nhelper();\n",
    "cjs/main.js": "const u = require('../lib/util.js');\nmodule.exports = u;\n",
}


def _repo(tmp_path, monkeypatch, name):
    root = tmp_path / name
    for rel, body in JS_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / (name + "_db")))
    monkeypatch.setenv("ORCHESTRATOR_PARALLEL_INDEX", "off")
    monkeypatch.setenv("CBM_EXTRACT_JS", "1")
    monkeypatch.setenv("CBM_GRAPH_ENABLED", "1")
    return root


def _index(root, **kw):
    from orchestrator.graph.indexer import index_project
    return index_project(str(root), scope="full", **kw)


def _rows(root):
    conn = gstore.get_db(str(root))
    return {r["path"]: r["extractor_version"]
            for r in conn.execute("SELECT path, extractor_version FROM files;")}


def _import_names(root):
    conn = gstore.get_db(str(root))
    return sorted(r["qualified_name"] for r in conn.execute(
        "SELECT qualified_name FROM nodes WHERE kind='import';"))


def test_a_row_records_the_version_the_code_reports(tmp_path, monkeypatch):
    """If these two ever disagree, no stale row is ever detectable."""
    root = _repo(tmp_path, monkeypatch, "ev_agree")
    _index(root)
    versions = set(_rows(root).values())
    assert versions == {gconfig.EXTRACTOR_VERSION}, versions
    assert gstatus.EXTRACTOR_VERSION == gconfig.EXTRACTOR_VERSION


def test_an_unchanged_file_is_reindexed_when_the_version_moves(tmp_path,
                                                               monkeypatch):
    """The whole point: same bytes, new extractor, must re-extract."""
    root = _repo(tmp_path, monkeypatch, "ev_bump")
    first = _index(root)
    assert first["files_indexed"] == 3, first

    again = _index(root)
    assert again["files_indexed"] == 0, "same version + same bytes must be a no-op"
    assert again["files_unchanged"] == 3

    monkeypatch.setattr(gstore, "EXTRACTOR_VERSION", "ast-p0-vSYNTH-newer")
    monkeypatch.setattr(gstatus, "EXTRACTOR_VERSION", "ast-p0-vSYNTH-newer")
    bumped = _index(root)
    assert bumped["files_indexed"] == 3, (
        "a version bump left %s files frozen -- an extractor fix would not ship"
        % bumped["files_unchanged"])


def test_the_js_import_shape_travels_with_the_version(tmp_path, monkeypatch):
    """B013's rollout, end to end: old row -> broken shape -> bump -> fixed.

    This is the assertion that would have failed on the day B013 was committed
    without a bump, on any repo whose rows predated it.
    """
    root = _repo(tmp_path, monkeypatch, "ev_shape")
    _index(root)
    fixed = _import_names(root)
    assert any(n.startswith("lib.util") for n in fixed), fixed
    assert not any("/" in n or ".js." in n for n in fixed), (
        "a path-shaped import key survived: %s" % fixed)

    # Simulate rows written by an older extractor: same bytes, older version.
    conn = gstore.get_db(str(root))
    conn.execute("UPDATE files SET extractor_version='ast-p0-vOLD';")
    conn.commit()

    stale = gstore.stale_files(conn, [{"path": p, "content_hash": "irrelevant"}
                                      for p in JS_FILES])
    assert len(stale) == 3, (
        "rows from an older extractor were not stale, so no fix to the "
        "extractor can ever reach an already-indexed repo")

    after = _index(root)
    assert after["files_indexed"] == 3
    assert _import_names(root) == fixed


def test_dependency_edges_come_back_after_a_version_bump(tmp_path, monkeypatch):
    """The reported symptom was `dependency_edges: 0` on a full index."""
    root = _repo(tmp_path, monkeypatch, "ev_edges")
    first = _index(root)
    assert first["dependency_edges"] > 0, first

    conn = gstore.get_db(str(root))
    conn.execute("UPDATE files SET extractor_version='ast-p0-vOLD';")
    conn.commit()

    again = _index(root)
    assert again["files_indexed"] == 3
    assert again["dependency_edges"] > 0, (
        "edges did not come back after re-extraction: %s" % again)


def test_the_version_string_is_not_left_at_the_pre_b013_value(tmp_path,
                                                             monkeypatch):
    """A guard against the specific miss, not against future bumps.

    `v8-all-is-not-a-reference` is the version that shipped BEFORE the JS import
    shape changed. Any later string passes; that one exact string does not,
    because a repo indexed under it holds path-shaped keys.
    """
    assert gconfig.EXTRACTOR_VERSION != "ast-p0-v8-all-is-not-a-reference", (
        "EXTRACTOR_VERSION still names the pre-B013 extractor, so every repo "
        "already indexed keeps its path-shaped import keys and require() calls "
        "unrecorded -- the fix does not ship")
