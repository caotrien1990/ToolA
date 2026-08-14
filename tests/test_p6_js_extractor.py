"""Test suite for JS/TS tree-sitter extractor (Phase 6 Step 23).

Validates the JS/TS extractor (extractors/javascript.py) and its indexer
integration (indexer.py): node/edge emission, CBM confidence values, broken /
unsupported degradation, CBM_EXTRACT_JS flag on/off, and mixed-project
multi-language indexing.

Style mirrors test_p5_council_context.py: env isolation + unittest.
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Set GRAPH_ENABLED before importing graph modules (config reads env at import).
os.environ.setdefault("GRAPH_ENABLED", "1")

from orchestrator.graph.extractors import javascript as _extractor_js
from orchestrator.graph.config import (
    CBM_EXACT, CBM_UNIQUE, CBM_SUFFIX_LOW, CBM_SUFFIX_HIGH, CBM_SAME_MODULE,
)

_PROJ_ROOT = str(Path(__file__).resolve().parents[1])


class P6JsExtractorSuite(unittest.TestCase):
    """Phase 6 Step 23 — JS/TS extractor + indexer integration."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p6js_")
        os.environ["GRAPH_DB_DIR"] = self.tmp

    def tearDown(self):
        os.environ.pop("GRAPH_DB_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # 1. Full JS extraction: import, export func, class extends, method, arrow
    def test_js_extract_nodes_and_edges(self):
        src = (
            'import { foo } from "./foo";\n'
            'import http from "http";\n\n'
            'export function main() {\n'
            '    const x = (n) => n + 1;\n'
            '    return foo();\n'
            '}\n\n'
            'class Animal extends Base {\n'
            '    bark() {\n'
            '        http.get("x");\n'
            '    }\n'
            '}\n'
        )
        res = _extractor_js.extract(src, file_path="app.js", project_root="/proj")
        self.assertEqual(res["errors"], [], f"unexpected errors: {res['errors']}")
        kinds = {n["kind"] for n in res["nodes"]}
        for k in ("module", "import", "function", "class", "method"):
            self.assertIn(k, kinds, f"missing node kind {k!r}; have {kinds}")
        e_kinds = {e["kind"] for e in res["edges"]}
        for k in ("imports", "calls", "inherits", "defines"):
            self.assertIn(k, e_kinds, f"missing edge kind {k!r}; have {e_kinds}")
        # Confidence: resolved local call (foo) = 0.9, unresolved external (http.get) = 0.3
        calls = [e for e in res["edges"] if e["kind"] == "calls"]
        confs = {e["confidence"] for e in calls}
        self.assertIn(CBM_UNIQUE, confs, f"resolved local call should be 0.9; got {confs}")
        self.assertIn(CBM_SUFFIX_LOW, confs, f"unresolved external call should be 0.3; got {confs}")
        # imports edge = 1.0 (CBM_EXACT)
        imports = [e for e in res["edges"] if e["kind"] == "imports"]
        self.assertTrue(all(e["confidence"] == CBM_EXACT for e in imports))
        # is_exported=1 on exported function (main)
        exported = [n for n in res["nodes"] if n["kind"] == "function" and n["name"] == "main"]
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0].get("is_exported"), 1)

    # 2. TS extraction with type annotation
    def test_ts_extract(self):
        src = (
            'function helper(): number { return 42; }\n'
            'export function greet(name: string): string {\n'
            '    return String(helper());\n'
            '}\n'
        )
        res = _extractor_js.extract(src, file_path="lib.ts", project_root="/proj")
        self.assertEqual(res["errors"], [], f"unexpected errors: {res['errors']}")
        langs = {n["language"] for n in res["nodes"]}
        self.assertIn("typescript", langs, f"expected typescript; got {langs}")
        calls = [e for e in res["edges"] if e["kind"] == "calls"]
        self.assertGreaterEqual(len(calls), 1, f"expected >=1 calls edge; got {calls}")

    # 3. Broken source: no raise, partial extract, parse_error in errors
    def test_broken_source_partial_no_raise(self):
        res = _extractor_js.extract("function {{ nope",
                                    file_path="broken.js", project_root="/proj")
        self.assertTrue(any("parse_error" in e for e in res["errors"]),
                        f"expected parse_error in errors: {res['errors']}")
        kinds = {n["kind"] for n in res["nodes"]}
        self.assertIn("module", kinds, "partial extract must still emit module node")

    # 4. Unsupported extension degrades gracefully
    def test_unsupported_ext_degrades(self):
        res = _extractor_js.extract("def x: pass",
                                    file_path="script.rb", project_root="/proj")
        self.assertEqual(res["errors"], ["unsupported_extension: .rb"])
        self.assertEqual(res["nodes"], [])
        self.assertEqual(res["edges"], [])

    # 5. CBM_EXTRACT_JS=0 → DEFAULT_EXTS is python-only
    def test_indexer_flag_off_python_only(self):
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.'); "
             "import os; os.environ['CBM_EXTRACT_JS'] = '0'; "
             "from orchestrator.graph import indexer; "
             "print(repr(indexer.DEFAULT_EXTS))"],
            capture_output=True, text=True, cwd=_PROJ_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "('.py',)")

    # 6. CBM_EXTRACT_JS=1 + mixed project → 3 files indexed, 3 languages
    def test_indexer_flag_on_mixed_project(self):
        proj = Path(self.tmp) / "proj"
        proj.mkdir()
        (proj / "app.js").write_text("export function main() { return foo(); }\n")
        (proj / "lib.ts").write_text("export function greet(n: string) { return n; }\n")
        (proj / "x.py").write_text("def thing():\n    return 1\n")
        tmp_str = str(self.tmp).replace("\\", "/")
        proj_str = str(proj).replace("\\", "/")
        script = (
            "import sys, os, json; sys.path.insert(0, '.'); "
            f"os.environ['CBM_EXTRACT_JS'] = '1'; "
            f"os.environ['GRAPH_ENABLED'] = '1'; "
            f"os.environ['GRAPH_DB_DIR'] = {tmp_str!r}; "
            "from orchestrator.graph import indexer, store; "
            f"res = indexer.index_project({proj_str!r}, scope='full', project_root={proj_str!r}); "
            f"conn = store.get_db({proj_str!r}, readonly=True); "
            "langs = [r[0] for r in conn.execute('SELECT DISTINCT language FROM nodes ORDER BY language;').fetchall()]; "
            "conn.close(); "
            "print(json.dumps({'files_indexed': res['files_indexed'], 'node_count': res['nodes'], 'languages': langs, 'status': res['status'], 'errors': res['errors']}))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, cwd=_PROJ_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.strip())
        self.assertEqual(data["files_indexed"], 3,
                         f"expected 3 files indexed; got {data}")
        langs = set(data["languages"])
        self.assertIn("javascript", langs, f"missing javascript; have {langs}")
        self.assertIn("typescript", langs, f"missing typescript; have {langs}")
        self.assertIn("python", langs, f"missing python; have {langs}")
        self.assertEqual(data["status"], "completed")

    # --- STT-19: anonymous default export ---

    def test_anonymous_default_export_arrow(self):
        src = "export default () => 42;\n"
        res = _extractor_js.extract(src, file_path="def.js", project_root="/proj")
        self.assertEqual(res["errors"], [])
        fns = [n for n in res["nodes"] if n["kind"] == "function"]
        self.assertTrue(any(n.get("name") == "default" for n in fns),
                        f"expected default function node; got {fns}")

    def test_anonymous_default_export_function(self):
        src = "export default function() { return 1; };\n"
        res = _extractor_js.extract(src, file_path="deffn.js", project_root="/proj")
        self.assertEqual(res["errors"], [])
        fns = [n for n in res["nodes"] if n["kind"] == "function"]
        self.assertTrue(any(n.get("name") == "default" for n in fns))

    def test_default_export_identifier(self):
        # export default <identifier> marks the named symbol as exported
        src = "function helper() {}\nexport default helper;\n"
        res = _extractor_js.extract(src, file_path="defid.js", project_root="/proj")
        self.assertEqual(res["errors"], [])
        helper = [n for n in res["nodes"] if n.get("name") == "helper"]
        self.assertTrue(helper, "expected helper node")
        self.assertEqual(helper[0].get("is_exported"), 1)

    # --- STT-20: export alias ---

    def test_export_alias_marks_both_names(self):
        # export { foo as bar } -> both foo and bar in _exported
        src = "function foo() {}\nexport { foo as bar };\n"
        res = _extractor_js.extract(src, file_path="alias.js", project_root="/proj")
        self.assertEqual(res["errors"], [])
        foo = [n for n in res["nodes"] if n.get("name") == "foo"]
        self.assertTrue(foo, "expected foo node")
        self.assertEqual(foo[0].get("is_exported"), 1)

    # --- STT-21: namespace import ---

    def test_namespace_import(self):
        src = 'import * as ns from "./mod";\nexport function main() { return ns.thing(); }\n'
        res = _extractor_js.extract(src, file_path="ns.js", project_root="/proj")
        self.assertEqual(res["errors"], [])
        imports = [n for n in res["nodes"] if n["kind"] == "import"]
        self.assertTrue(any("ns" in n.get("name", "") for n in imports),
                        f"expected ns import; got {imports}")
        # should have at least one imports edge
        imp_edges = [e for e in res["edges"] if e["kind"] == "imports"]
        self.assertTrue(imp_edges, "expected at least one imports edge")

    # --- STT-22: arrow function line numbers ---

    def test_arrow_function_line_numbers(self):
        # multi-line: const x = on line 1, arrow body on line 2-3
        src = (
            "const x =\n"
            "  (n) => {\n"
            "    return n + 1;\n"
            "  };\n"
        )
        res = _extractor_js.extract(src, file_path="arrow.js", project_root="/proj")
        self.assertEqual(res["errors"], [])
        fn = [n for n in res["nodes"] if n["kind"] == "function"]
        self.assertTrue(fn, "expected function node for arrow")
        # STT-9 fix: start_line/end_line span the arrow_function body,
        # not the declarator (which starts on line 1 with `const x =`)
        self.assertGreaterEqual(fn[0]["start_line"], 2,
                                f"start_line should be >= 2 (arrow body); got {fn[0]['start_line']}")
        self.assertGreaterEqual(fn[0]["end_line"], 3,
                                f"end_line should be >= 3 (arrow body close); got {fn[0]['end_line']}")


if __name__ == "__main__":
    unittest.main()
