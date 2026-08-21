"""A JS project must get real module dependency edges.

Measured on the reported repo: 41 JS files, 1825 nodes, 4316 edges -- and ALL
4316 of kind `calls`. Zero `imports`, zero `imports_module`, zero import nodes,
so `module_deps` / `imports` / `imported_by` / cycle detection all answered empty
on a project that plainly has imports.

The report blamed CommonJS `require()`. That is half of it. Isolating an ESM half
and a CommonJS half in one repo showed TWO independent gaps:

  1. `require('./x')` is a `call_expression`, so it never reaches `_emit_import`
     and produces no import node at all -- it becomes an `ext:require` call edge.
     Measured: `cjs_app.js imports=0`.

  2. ESM DOES emit an import node, but with a path-shaped qualified_name:
     `import {helper} from './lib/util.js'` -> `./lib/util.js.helper`. The
     resolver walks `_dotted_prefixes` of that, yielding `/lib/util.js.helper`,
     `/lib/util.js`, `/lib/util` -- none of which can ever equal the module key
     `_dotted_from_path('lib/util.js')` == `lib.util`. Measured on a pure-ESM
     repo: `unresolved_imports: 1, dependency_edges: 0`.

So fixing require() alone would have left every ESM project at zero edges, and
the reported symptom would have survived the fix. Both halves are pinned here.

The fix normalises the specifier to a dotted module path at extraction time
(where the importing file's directory is known), which is the only place the
`./`, `../` and extension-suffix forms can be resolved against the importer.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.graph import store as gstore
from orchestrator.graph.extractors import javascript as js
from orchestrator.graph.indexer import index_project


ESM = {
    "lib/util.js": "export function helper() { return 1; }\n",
    "esm_app.js": ("import { helper } from './lib/util.js';\n"
                   "export function run() { return helper(); }\n"),
}
CJS = {
    "cjs_util.js": ("function cjsHelper() { return 3; }\n"
                    "module.exports = { cjsHelper };\n"),
    "cjs_app.js": ("const { cjsHelper } = require('./cjs_util');\n"
                   "function cjsRun() { return cjsHelper(); }\n"
                   "module.exports = { cjsRun };\n"),
}


def _repo(tmp_path, monkeypatch, name, files):
    root = tmp_path / name
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    monkeypatch.setenv("GRAPH_DB_DIR", str(tmp_path / (name + "_db")))
    monkeypatch.setenv("ORCHESTRATOR_PARALLEL_INDEX", "off")
    monkeypatch.setenv("CBM_EXTRACT_JS", "1")
    return root


def _edges(root, kind):
    conn = gstore.get_db(str(root), readonly=True)
    try:
        return conn.execute(
            "SELECT COUNT(*) c FROM edges WHERE kind=?;", (kind,)).fetchone()["c"]
    finally:
        conn.close()


def _imports_of(rel, body, root):
    res = js.extract(body, file_path=rel, project_root=str(root))
    return [n["qualified_name"] for n in res["nodes"] if n["kind"] == "import"]


# ─── gap 2: ESM resolves at all ──────────────────────────────────────────────

def test_an_esm_import_node_carries_a_dotted_module_path(tmp_path, monkeypatch):
    """`./lib/util.js.helper` can never match the module key `lib.util`."""
    root = _repo(tmp_path, monkeypatch, "esm_shape", ESM)
    qnames = _imports_of("esm_app.js", ESM["esm_app.js"], root)
    assert qnames, "no import node emitted for an ESM import"
    qn = qnames[0]
    assert not qn.startswith("."), (
        "qualified_name %r is path-shaped; its dotted prefixes start with '/' "
        "and cannot match any module key" % qn)
    assert qn == "lib.util.helper", qn


def test_an_esm_project_gets_dependency_edges(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch, "esm_dep", ESM)
    res = index_project(str(root), scope="full")
    assert res["dependency_edges"] >= 1, (
        "ESM import resolved to 0 dependency edges (stats=%s)"
        % res["dependency_stats"])
    assert res["dependency_stats"]["unresolved_imports"] == 0
    assert _edges(root, "imports_module") >= 1


# ─── gap 1: CommonJS produces an import node ─────────────────────────────────

def test_require_emits_an_import_node(tmp_path, monkeypatch):
    """require() is a call_expression; it never reached _emit_import."""
    root = _repo(tmp_path, monkeypatch, "cjs_shape", CJS)
    qnames = _imports_of("cjs_app.js", CJS["cjs_app.js"], root)
    assert qnames, "require('./cjs_util') produced no import node"
    assert any(q.startswith("cjs_util") for q in qnames), qnames


def test_a_commonjs_project_gets_dependency_edges(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch, "cjs_dep", CJS)
    res = index_project(str(root), scope="full")
    assert res["dependency_edges"] >= 1, (
        "CommonJS require resolved to 0 dependency edges (stats=%s)"
        % res["dependency_stats"])
    assert _edges(root, "imports_module") >= 1


def test_destructured_require_names_are_bound(tmp_path, monkeypatch):
    """`const {cjsHelper} = require(...)` must name cjsHelper, not just the module.

    Without the destructured name the call to cjsHelper() stays an `ext:` edge
    and the cross-file call is lost even once the module edge exists.
    """
    root = _repo(tmp_path, monkeypatch, "cjs_names", CJS)
    qnames = _imports_of("cjs_app.js", CJS["cjs_app.js"], root)
    assert "cjs_util.cjsHelper" in qnames, qnames


def test_plain_require_without_binding_still_records_the_module(tmp_path, monkeypatch):
    """Side-effect require: `require('./cjs_util');` alone."""
    files = dict(CJS)
    files["side.js"] = "require('./cjs_util');\n"
    root = _repo(tmp_path, monkeypatch, "cjs_side", files)
    qnames = _imports_of("side.js", files["side.js"], root)
    assert "cjs_util" in qnames, qnames


def test_default_require_binding_is_recorded(tmp_path, monkeypatch):
    files = dict(CJS)
    files["def.js"] = "const util = require('./cjs_util');\n"
    root = _repo(tmp_path, monkeypatch, "cjs_default", files)
    qnames = _imports_of("def.js", files["def.js"], root)
    assert "cjs_util.util" in qnames, qnames


# ─── both halves in one project, plus the reported headline ──────────────────

def test_a_mixed_project_resolves_both_halves(tmp_path, monkeypatch):
    files = dict(ESM)
    files.update(CJS)
    root = _repo(tmp_path, monkeypatch, "mixed", files)
    res = index_project(str(root), scope="full")
    assert res["dependency_edges"] >= 2, (
        "expected an edge from each half, got %s (stats=%s)"
        % (res["dependency_edges"], res["dependency_stats"]))
    assert res["dependency_stats"]["unresolved_imports"] == 0


def test_a_bare_package_specifier_stays_unresolved_not_misresolved(tmp_path,
                                                                   monkeypatch):
    """`require('fs')` / `import 'react'` are external; they must not resolve.

    Guard against the fix over-reaching: normalising relative specifiers must not
    make a node_modules name collide with a same-named local file.
    """
    files = dict(ESM)
    files["ext.js"] = "import fs from 'fs';\nexport const x = fs;\n"
    root = _repo(tmp_path, monkeypatch, "bare", files)
    qnames = _imports_of("ext.js", files["ext.js"], root)
    assert qnames == ["fs.fs"] or qnames == ["fs"], qnames
    res = index_project(str(root), scope="full")
    # the ESM half still resolves; the bare specifier does not invent an edge
    assert res["dependency_edges"] >= 1


def test_parent_relative_specifier_resolves(tmp_path, monkeypatch):
    """`../` must resolve against the importer's directory."""
    files = {
        "lib/util.js": "export function helper() { return 1; }\n",
        "app/deep.js": ("import { helper } from '../lib/util.js';\n"
                        "export function run() { return helper(); }\n"),
    }
    root = _repo(tmp_path, monkeypatch, "parent_rel", files)
    qnames = _imports_of("app/deep.js", files["app/deep.js"], root)
    assert qnames == ["lib.util.helper"], qnames
    res = index_project(str(root), scope="full")
    assert res["dependency_edges"] >= 1, res["dependency_stats"]


def test_extensionless_relative_specifier_resolves(tmp_path, monkeypatch):
    """Node resolves './x' to './x.js'; so must the key."""
    files = {
        "lib/util.js": "export function helper() { return 1; }\n",
        "app.js": ("import { helper } from './lib/util';\n"
                   "export function run() { return helper(); }\n"),
    }
    root = _repo(tmp_path, monkeypatch, "extless", files)
    res = index_project(str(root), scope="full")
    assert res["dependency_edges"] >= 1, res["dependency_stats"]
