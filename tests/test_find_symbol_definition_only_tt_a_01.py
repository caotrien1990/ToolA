"""TT-A-01 regression: find_symbol must return only definitions, not call sites,
comments, or JSON artifacts.

Why: prior fallback (graph-disabled path) scanned every text file with a broad
regex, surfacing `data.json` index artifacts, comment mentions, and call sites
as if they were definitions. The fix filters by source-file extension, drops
comment lines, and tightens patterns so only true definitions match.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.tools.search import find_symbol


class TestFindSymbolDefinitionOnly(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Definition
        (self.root / "mod.py").write_text(
            "def target_fn(x):\n"
            "    return x + 1\n"
            "\n"
            "def caller():\n"
            "    return target_fn(1)  # call site\n",
            encoding="utf-8",
        )
        # JSON artifact (must be ignored)
        (self.root / "data.json").write_text(
            '{"name": "target_fn", "kind": "symbol-index"}\n',
            encoding="utf-8",
        )
        # Comment + call site in another file (must be ignored)
        (self.root / "other.py").write_text(
            "# target_fn is mentioned in a comment\n"
            "import mod\n"
            "result = mod.target_fn(5)\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_definition_only(self) -> None:
        raw = asyncio.run(find_symbol(name="target_fn", project_path=str(self.root)))
        data = json.loads(raw)
        results = data.get("results", [])
        # Exactly one definition
        self.assertEqual(len(results), 1, f"expected 1 result, got {results}")
        entry = results[0]
        self.assertEqual(entry["file"], "mod.py")
        self.assertEqual(entry["line"], 1)
        self.assertIn("def target_fn", entry["match"])

    def test_excludes_json_artifacts(self) -> None:
        raw = asyncio.run(find_symbol(name="target_fn", project_path=str(self.root)))
        data = json.loads(raw)
        files = {r["file"] for r in data.get("results", [])}
        self.assertNotIn("data.json", files)

    def test_excludes_call_sites_and_comments(self) -> None:
        raw = asyncio.run(find_symbol(name="target_fn", project_path=str(self.root)))
        data = json.loads(raw)
        matches = [r["match"] for r in data.get("results", [])]
        for m in matches:
            self.assertNotIn("mod.target_fn(", m, f"call site leaked: {m}")
            self.assertNotIn("target_fn(1)", m, f"call site leaked: {m}")
            self.assertFalse(m.lstrip().startswith("#"), f"comment leaked: {m}")



class TestFindSymbolScopePythonJsOnly(unittest.TestCase):
    """TT-A-01 scope refinement (2026-08-10): find_symbol's live-filesystem
    fallback is honestly scoped to Python/JS-style languages only. The regex
    recognises Python 'def'/'class' and JS/TS 'function'/'const'/'let'/'var'/
    method-shorthand definition forms. It must NOT match Go/Rust/C-family
    definitions (out of scope) and must NOT match visually-identical text
    inside block comments or multiline strings.

    Why: prior extension list (.go/.rs/.c/.cpp/.h/.java/.kt/.rb/.php/...)
    advertised support the regex could not deliver, producing either zero
    hits or stray matches inside block comments. The scope refinement
    removes those extensions and locks the contract in tests.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, name: str) -> list[dict]:
        raw = asyncio.run(find_symbol(name=name, project_path=str(self.root)))
        return json.loads(raw).get("results", [])

    def test_python_def_returned(self) -> None:
        (self.root / "m.py").write_text(
            "def target_fn(x):\n    return x\n", encoding="utf-8"
        )
        results = self._run("target_fn")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["file"], "m.py")
        self.assertIn("def target_fn", results[0]["match"])

    def test_js_function_returned(self) -> None:
        (self.root / "m.js").write_text(
            "function targetFn(x) {\n  return x;\n}\n", encoding="utf-8"
        )
        results = self._run("targetFn")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["file"], "m.js")
        self.assertIn("function targetFn", results[0]["match"])

    def test_go_definition_not_returned(self) -> None:
        # Out-of-scope language: regex does not recognise Go 'func name()'.
        (self.root / "m.go").write_text(
            "package main\n\nfunc targetFn(x int) int {\n  return x\n}\n",
            encoding="utf-8",
        )
        results = self._run("targetFn")
        self.assertEqual(results, [], f"Go definition should be out of scope, got {results}")

    def test_rust_definition_not_returned(self) -> None:
        (self.root / "m.rs").write_text(
            "fn target_fn(x: i32) -> i32 {\n    x\n}\n", encoding="utf-8"
        )
        results = self._run("target_fn")
        self.assertEqual(results, [], f"Rust definition should be out of scope, got {results}")

    def test_c_family_definition_not_returned(self) -> None:
        (self.root / "m.cpp").write_text(
            "int target_fn(int x) {\n    return x;\n}\n", encoding="utf-8"
        )
        results = self._run("target_fn")
        self.assertEqual(results, [], f"C++ definition should be out of scope, got {results}")

    def test_block_comment_interior_not_matched(self) -> None:
        # Line inside a docstring/multiline-string that visually starts with
        # 'def target_fn(' must not be returned.
        (self.root / "m.py").write_text(
            '\"\"\"\nblock-comment interior:\ndef target_fn(x):\n\"\"\"\n\nreal = 1\n',
            encoding="utf-8",
        )
        results = self._run("target_fn")
        self.assertEqual(
            results,
            [],
            f"docstring/multiline-string interior should not match, got {results}",
        )


class TestFindSymbolTTA01v2(unittest.TestCase):
    """TT-A-01 v2 regression tests (2026-08-10): fixes for high-dev review.

    Why these exist:
    - (a) Previously, a def line that opens a docstring on the same line
      was incorrectly suppressed because the triple-quote opener check
      ran BEFORE the definition regex check, and `continue` on the
      opener line discarded the valid definition. The fix runs the
      definition check first and only skips when the string opener
      precedes the definition match.
    - (b) Previously, JS/C block-comment state was not tracked across
      lines; a method-shaped line inside `/* ... */` interior could
      match. The fix adds block-comment state tracking for JS-family
      files (enter on `/*`, exit on `*/`).
    - (c) Previously, when both single-triple and double-triple quote
      markers appeared on the same line, the first marker found by
      loop order won regardless of character position. The fix picks
      the earliest by character index, matching Python tokenisation.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, name: str) -> list[dict]:
        raw = asyncio.run(find_symbol(name=name, project_path=str(self.root)))
        return json.loads(raw).get("results", [])

    # (a) def-before-string-opener false negative -------------------------------
    def test_def_before_docstring_opener_is_returned(self) -> None:
        # A real `def` line that happens to OPEN a docstring on the same
        # line must still be returned. The v1 code suppressed this valid
        # definition because the triple-quote opener triggered `continue`
        # before the definition regex was consulted.
        (self.root / "m.py").write_text(
            "def target_fn(x):\n"
            "    \"\"\"Summary line.\n"
            "    continues\n"
            "    \"\"\"\n"
            "    return x + 1\n",
            encoding="utf-8",
        )
        results = self._run("target_fn")
        self.assertEqual(len(results), 1, f"valid def suppressed: {results}")
        self.assertEqual(results[0]["file"], "m.py")
        self.assertEqual(results[0]["line"], 1)
        self.assertIn("def target_fn", results[0]["match"])

    def test_def_with_same_line_docstring_opener_kept(self) -> None:
        # Same-line def that opens AND closes a docstring on one line
        # must still be returned (the string does not persist).
        (self.root / "m.py").write_text(
            "def target_fn(x): \"\"\"inline doc\"\"\"\n"
            "    return x\n",
            encoding="utf-8",
        )
        results = self._run("target_fn")
        self.assertEqual(len(results), 1, f"same-line-doc def lost: {results}")
        self.assertIn("def target_fn", results[0]["match"])

    # (b) JS block-comment interior is not matched ------------------------------
    def test_js_block_comment_interior_not_matched(self) -> None:
        # A method-shaped line inside a multi-line /* ... */ block comment
        # must NOT be returned. v1 only filtered lines that themselves
        # STARTED with /* or *, not lines inside a persistent block.
        (self.root / "m.js").write_text(
            "/*\n"
            " * Multiline block comment.\n"
            " * targetFn(x) { return 1; }\n"
            " */\n"
            "function unrelated() {}\n",
            encoding="utf-8",
        )
        results = self._run("targetFn")
        self.assertEqual(
            results,
            [],
            f"block-comment interior leaked as definition: {results}",
        )

    def test_js_block_comment_tail_definition_still_not_matched(self) -> None:
        # A definition on a line AFTER the closing `*/` IS real and must
        # be returned (state properly exited). A definition INSIDE the
        # block must NOT.
        (self.root / "m.js").write_text(
            "/* start\n"
            "   targetFn() {} \n"
            "*/\n"
            "function otherName() {}\n",
            encoding="utf-8",
        )
        # Search for the interior symbol (must NOT match).
        results_in = self._run("targetFn")
        self.assertEqual(
            results_in, [],
            f"block-comment interior leaked: {results_in}",
        )
        # Search for the post-block symbol (must match).
        results_out = self._run("otherName")
        self.assertEqual(len(results_out), 1, f"post-block def lost: {results_out}")

    # (c) earliest delimiter wins ------------------------------------------------
    def test_earliest_triple_delimiter_wins(self) -> None:
        # When both single-triple and double-triple markers appear on the
        # same line, the EARLIEST character position determines which
        # string state is entered (matching Python tokenisation). v1 used
        # loop order so double-triple always won regardless of position.
        (self.root / "m.py").write_text(
            "\'\'\' then \"\"\" on same line\n"
            "def target_fn(x):\n"
            "\'\'\'\n"
            "real = 1\n",
            encoding="utf-8",
        )
        results = self._run("target_fn")
        # The earliest opener is single-triple at position 0; the def on
        # line 2 is inside that string, so must NOT be returned.
        self.assertEqual(
            results,
            [],
            f"earliest-delimiter rule violated: {results}",
        )

    def test_earliest_delimiter_double_before_single(self) -> None:
        # Inverse case: double-triple appears FIRST (earlier position) and
        # single-triple second. The double-triple string persists; a def
        # on the next line inside that string must NOT match.
        (self.root / "m.py").write_text(
            "\"\"\" opener first, then \'\'\' later\n"
            "def target_fn(x):\n"
            "\"\"\"\n"
            "real = 1\n",
            encoding="utf-8",
        )
        results = self._run("target_fn")
        self.assertEqual(
            results,
            [],
            f"earliest-delimiter rule violated (double-first): {results}",
        )


class TestFindSymbolTTA01v3(unittest.TestCase):
    """TT-A-01 v3 regression tests (2026-08-10): round-3 edge-case fixes.

    Why these exist:
    - (a) Block-comment state was lost when a definition occurred BEFORE a
      persistent `/*` on the same line: the def was returned via the
      fall-through branch, but `in_block_comment` was never enabled, so a
      later method-shaped occurrence of the SAME name inside that block
      leaked as a second definition. The fix returns the def via
      fall-through AND enters block-comment state for subsequent lines.
    - (b) `_earliest_triple` independently discarded any marker that had a
      later same-type delimiter, then picked the earliest remaining. That
      wrongly treated a DIFFERENT triple marker inside an already-closed
      string as a persistent opener (e.g. the ''' inside
      \"\"\"content containing ''' text\"\"\"). The fix implements lexical
      left-to-right scanning: the FIRST delimiter opens the string, only
      its matching closer ends it, and other marker types in between are
      string content.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, name: str) -> list[dict]:
        raw = asyncio.run(find_symbol(name=name, project_path=str(self.root)))
        return json.loads(raw).get("results", [])

    # (a) def before persistent /* keeps block-comment state -------------------
    def test_def_before_block_comment_opener_keeps_state(self) -> None:
        # Line 1: real def `function targetFn() {}` followed by a persistent
        # `/*` opener. Line 2: the SAME name inside the block comment must
        # NOT leak as a second definition. v2 returned the def on line 1 but
        # lost block-comment state, letting line 2 leak.
        (self.root / "m.js").write_text(
            "function targetFn() {} /*\n"
            "targetFn() {}\n"
            "*/\n",
            encoding="utf-8",
        )
        results = self._run("targetFn")
        self.assertEqual(
            len(results), 1,
            f"interior same-name line leaked as second definition: {results}",
        )
        self.assertEqual(results[0]["line"], 1)
        self.assertIn("function targetFn", results[0]["match"])

    # (b) different triple marker inside closed string is content -------------
    def test_triple_marker_inside_closed_string_is_content(self) -> None:
        # Line 1: `x = """content containing ''' text"""` — the `"""` opens
        # AND closes on line 1; the contained `'''` is string content, NOT
        # a persistent opener. The real `def target_fn` on line 2 must be
        # returned. v2's per-marker discard logic skipped the closed `"""`
        # pair and treated `'''` as a persistent opener, suppressing the def.
        (self.root / "m.py").write_text(
            "x = \"\"\"content containing \'\'\' text\"\"\"\n"
            "def target_fn():\n"
            "    pass\n",
            encoding="utf-8",
        )
        results = self._run("target_fn")
        self.assertEqual(
            len(results), 1,
            f"real def suppressed by phantom triple opener: {results}",
        )
        self.assertEqual(results[0]["line"], 2)
        self.assertIn("def target_fn", results[0]["match"])



class TestFindSymbolIndexSourceP19(unittest.TestCase):
    """P19 (2026-10-03): find_symbol must answer from a SYMBOL index, never
    from a whole-repo text scan.

    Root defect fixed here: the find_symbol TOOL was registered as
    ('find_symbol', 'search_code') with no dispatcher mode default, so every
    call ran search_code mode='text' -- a whole-repo snapshot text scan that
    returned call sites next to the definition, and whose 0-hit path paid the
    A03 whole-repo cache_delta probe plus the P15 reconcile (measured 250s on
    49. CUA, 4128 indexed files, 223 natively-drifted files).

    Contract locked by these tests:
      1) A real INDEX.json symbol map answers definitions directly
         (source='index'); the text-lane producer (snapshot pin) is never
         touched.
      2) A 0-hit over a CLEAN index is honest (stale_index False) and reads
         NO file content (no hash, no source read) -- the index already
         certifies the symbol set.
      3) A 0-hit over a STALE index scans ONLY the drifted files (the symbol
         answer's whole need), finds a natively-added definition, and reports
         stale_index True -- no whole-repo reconcile.
      4) Python methods are findable from the symbol index (they were only
         reachable through the O(repo) regex walk before P19).
      5) Latency guard: a 0-hit on a 3000-file project with a fresh symbol
         index performs ZERO full-file content reads.
    """

    def setUp(self) -> None:
        import os as _os
        self._os = _os
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # The graph adapter must not intercept these tests: they pin the
        # INDEX.json source chain. GRAPH_ENABLED is process-global and the
        # graph test suites set it, so force it off and restore after.
        self._old_graph = _os.environ.get("GRAPH_ENABLED")
        _os.environ["GRAPH_ENABLED"] = "0"

    def tearDown(self) -> None:
        if self._old_graph is None:
            self._os.environ.pop("GRAPH_ENABLED", None)
        else:
            self._os.environ["GRAPH_ENABLED"] = self._old_graph
        self._tmp.cleanup()

    def _write_project(self) -> None:
        (self.root / "mod.py").write_text(
            "def target_fn(x):\n"
            "    return x + 1\n"
            "\n"
            "class Widget:\n"
            "    def hello(self):\n"
            "        return 'hi'\n",
            encoding="utf-8",
        )
        (self.root / "user_mod.py").write_text(
            "from mod import target_fn\n"
            "\n"
            "def caller():\n"
            "    return target_fn(41)\n",
            encoding="utf-8",
        )

    def _build_index(self) -> None:
        import surgical_mapper
        surgical_mapper.generate_project_map(str(self.root))

    def _find(self, name: str) -> dict:
        raw = asyncio.run(find_symbol(name=name, project_path=str(self.root)))
        return json.loads(raw)

    def test_index_source_definitions_only_no_text_scan(self) -> None:
        """INDEX-backed answer: defs only, source='index', and the text-lane
        producer (snapshot pin) is never invoked."""
        self._write_project()
        self._build_index()


        def _pin_forbidden(*a, **k):
            raise AssertionError(
                "text-lane snapshot producer reached from find_symbol (P19)")

        import orchestrator.tools.search as search_mod
        orig = search_mod.search_snapshots.pin_current_snapshot
        search_mod.search_snapshots.pin_current_snapshot = _pin_forbidden
        try:
            data = self._find("target_fn")
        finally:
            search_mod.search_snapshots.pin_current_snapshot = orig
        results = data.get("results", [])
        self.assertEqual(len(results), 1, f"expected 1 def, got {results}")
        self.assertEqual(results[0]["file"], "mod.py")
        self.assertEqual(results[0]["line"], 1)
        self.assertIn("def target_fn", results[0]["match"])
        self.assertEqual(data.get("source"), "index",
                         f"expected source='index', got {data.get('source')!r}")
        self.assertNotEqual(data.get("mode"), "text",
                            "find_symbol must never answer from the text lane")
        # The call-site line in user_mod.py must not appear.
        for r in results:
            self.assertNotIn("target_fn(41)", r.get("match", ""),
                             f"call site leaked: {r}")

    def test_zero_hit_clean_index_is_honest_and_reads_nothing(self) -> None:
        """0-hit over a clean INDEX: stale_index False, count 0, and not a
        single file-content read (the stat-gated hash must never fire)."""
        self._write_project()
        self._build_index()
        import surgical_mapper

        def _hash_forbidden(*a, **k):
            raise AssertionError(
                "full-file hash read on a clean-index 0-hit (P19)")

        orig = surgical_mapper.get_file_hash
        surgical_mapper.get_file_hash = _hash_forbidden
        try:
            data = self._find("zz_no_such_symbol")
        finally:
            surgical_mapper.get_file_hash = orig
        self.assertEqual(data.get("count"), 0, f"phantom hit: {data}")
        self.assertEqual(data.get("results"), [], f"phantom hit: {data}")
        self.assertIs(data.get("stale_index"), False,
                      f"clean-index 0-hit must report stale_index False: {data}")

    def test_zero_hit_stale_index_scans_only_drifted_files(self) -> None:
        """Native drift: a file added AFTER the index holds the definition.
        find_symbol must find it by scanning ONLY the drifted set, and must
        report stale_index True -- honest, without a whole-repo reconcile."""
        self._write_project()
        self._build_index()
        # Native write (no cache update): the drift the probe must see.
        (self.root / "native_new.py").write_text(
            "def native_added_fn():\n"
            "    return 7\n",
            encoding="utf-8",
        )
        data = self._find("native_added_fn")
        results = data.get("results", [])
        self.assertEqual(len(results), 1,
                         f"drifted-file def not found: {data}")
        self.assertEqual(results[0]["file"], "native_new.py")
        self.assertEqual(results[0]["line"], 1)
        self.assertIs(data.get("stale_index"), True,
                      f"drifted 0-hit-turned-hit must report stale_index True: {data}")

    def test_method_found_from_symbol_index(self) -> None:
        """Python methods must resolve from the symbol index too. Before P19
        the INDEX functions/classes maps never registered methods, so a bare
        method name forced the O(repo) regex walk (or missed entirely)."""
        self._write_project()
        self._build_index()
        data = self._find("hello")
        results = data.get("results", [])
        self.assertEqual(len(results), 1, f"method def not found: {data}")
        self.assertEqual(results[0]["file"], "mod.py")
        self.assertEqual(results[0]["line"], 5)
        self.assertIn("def hello", results[0]["match"])

    def test_latency_guard_zero_hit_3000_files_no_full_read(self) -> None:
        """Latency guard: 0-hit on a 3000-file project with a fresh symbol
        index performs ZERO full-file content reads (hash or source)."""
        for i in range(3000):
            (self.root / f"f{i:04d}.py").write_text(
                f"def fn_{i:04d}():\n    return {i}\n", encoding="utf-8"
            )
        self._build_index()
        import surgical_mapper
        calls = {"hash": 0}
        orig_hash = surgical_mapper.get_file_hash

        def _counting_hash(*a, **k):
            calls["hash"] += 1
            return orig_hash(*a, **k)

        surgical_mapper.get_file_hash = _counting_hash
        try:
            data = self._find("zz_no_such_symbol")
        finally:
            surgical_mapper.get_file_hash = orig_hash
        self.assertEqual(data.get("count"), 0, f"phantom hit: {data}")
        self.assertIs(data.get("stale_index"), False, f"not clean: {data}")
        self.assertEqual(
            calls["hash"], 0,
            f"0-hit on a fresh index read file content {calls['hash']} times (P19)",
        )


class TestDefLaneEnvelopeGateP19b(unittest.TestCase):
    """P19b (2026-10-03): ONE envelope for every find_symbol def-lane exit.

    The live probe on the P19 build measured three splits by source:
    graph hits carried NO `ok` key; a 0-hit over a natively-drifted project
    fell to the regex walk and answered {"ok":true,"results":[]} with NO
    stale_index and NO source -- a silent "not there" over a stale index
    (A03 forbids it). These tests pin the finalizer contract: every def-lane
    0-hit exit carries stale_index (bool|None) + stale_index_reason, and the
    graph-source response carries ok:true.
    """

    def setUp(self) -> None:
        import os as _os
        self._os = _os
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._old_graph = _os.environ.get("GRAPH_ENABLED")
        _os.environ["GRAPH_ENABLED"] = "0"

    def tearDown(self) -> None:
        if self._old_graph is None:
            self._os.environ.pop("GRAPH_ENABLED", None)
        else:
            self._os.environ["GRAPH_ENABLED"] = self._old_graph
        self._tmp.cleanup()

    def _find(self, name: str) -> dict:
        raw = asyncio.run(find_symbol(name=name, project_path=str(self.root)))
        return json.loads(raw)

    def _assert_honest_zero_hit(self, data: dict, want_stale, want_reason: str) -> None:
        self.assertTrue(data.get("ok"), f"0-hit not ok: {data}")
        self.assertEqual(data.get("count"), 0, f"phantom hit: {data}")
        self.assertIn("source", data, f"0-hit missing source: {data}")
        self.assertIn("stale_index", data,
                      f"0-hit missing stale_index -- reads as silent 'not there': {data}")
        self.assertIs(data.get("stale_index"), want_stale, data)
        self.assertEqual(data.get("stale_index_reason"), want_reason, data)

    def test_zero_hit_index_clean_exit(self) -> None:
        # Exit 1: INDEX exists, delta ran, no drift -> False / index_clean.
        (self.root / "mod.py").write_text("def f():\n    return 1\n",
                                          encoding="utf-8")
        import surgical_mapper
        surgical_mapper.generate_project_map(str(self.root))
        data = self._find("zz_absent_p19b")
        self._assert_honest_zero_hit(data, False, "index_clean")
        self.assertEqual(data.get("source"), "index", data)

    def test_zero_hit_native_drift_exit(self) -> None:
        # Exit 2: INDEX exists but files drifted natively (walk exit) ->
        # True / native_drift. RED on the pre-finalizer P19 build: this exit
        # answered {"ok":true,"results":[],"count":0} with no stale_index,
        # no source (the live CUA 0-hit shape).
        (self.root / "mod.py").write_text("def f():\n    return 1\n",
                                          encoding="utf-8")
        import surgical_mapper
        surgical_mapper.generate_project_map(str(self.root))
        (self.root / "drifted.py").write_text("def g():\n    return 2\n",
                                              encoding="utf-8")
        data = self._find("zz_absent_p19b")
        self._assert_honest_zero_hit(data, True, "native_drift")
        self.assertEqual(data.get("source"), "walk", data)

    def test_zero_hit_no_index_exit(self) -> None:
        # Exit 3: no INDEX.json at all -> None / index_unavailable (the regex
        # walk answered alone; nothing to be stale against).
        (self.root / "mod.py").write_text("def f():\n    return 1\n",
                                          encoding="utf-8")
        data = self._find("zz_absent_p19b")
        self._assert_honest_zero_hit(data, None, "index_unavailable")
        self.assertEqual(data.get("source"), "walk", data)

    def test_graph_source_response_carries_ok(self) -> None:
        # Exit 4: the graph adapter builds {"results","count","source"} only;
        # the finalizer injects ok. RED pre-fix: body had no `ok` key, and a
        # caller guard `if not resp.get('ok')` read a SUCCESS as failure.
        from orchestrator.graph import config as graph_config

        fake = json.dumps({
            "results": [{"file": "m.py", "line": 1,
                         "match": "function f -> f", "range_id": "m.py:1"}],
            "count": 1,
            "source": "graph",
        })
        orig_use = graph_config.use_graph_for
        graph_config.use_graph_for = lambda _tool: True
        self._os.environ["GRAPH_ENABLED"] = "1"
        import orchestrator.graph.query as gq
        orig_fn = gq.find_symbol_as_search_response
        gq.find_symbol_as_search_response = lambda *a, **k: fake
        try:
            data = self._find("f")
        finally:
            gq.find_symbol_as_search_response = orig_fn
            graph_config.use_graph_for = orig_use
        self.assertIs(data.get("ok"), True,
                      f"graph-source success missing ok: {data}")
        self.assertEqual(data.get("source"), "graph", data)
        self.assertEqual(data.get("count"), 1, data)

    def test_def_lane_probes_never_run_on_the_loop_thread(self) -> None:
        """P19c guard: index_delta / load_index must run OFF the event loop.

        The first P19b finalizer called fo.index_delta synchronously inside
        the async _find_symbol_unbounded -- on CUA that is a full-tree walk +
        stat + MD5 of drifted files on the loop thread, freezing every other
        CA call and /health for seconds per 0-hit (the livez_ok_health_hung
        class ensure_fresh_index was fixed for). RED on that build: the delta
        and load_index threads equalled the loop thread.
        """
        import threading
        import orchestrator.fileops.common as fo
        import orchestrator.tools.search as search_mod
        import surgical_mapper

        (self.root / "mod.py").write_text("def f():\n    return 1\n",
                                          encoding="utf-8")
        surgical_mapper.generate_project_map(str(self.root))
        # Native drift, so the 0-hit walk exit reaches the finalizer's delta.
        (self.root / "drifted.py").write_text("def g():\n    return 2\n",
                                              encoding="utf-8")

        calls: list[tuple[str, object]] = []
        orig_delta = fo._cached_index_delta
        orig_load = fo.load_index

        def _rec_delta(*a, **k):
            calls.append(("delta", threading.current_thread()))
            return orig_delta(*a, **k)

        def _rec_load(*a, **k):
            calls.append(("load", threading.current_thread()))
            return orig_load(*a, **k)

        loop_thread: list[object] = []

        async def _drive() -> dict:
            loop_thread.append(threading.current_thread())
            fo._cached_index_delta = _rec_delta
            fo.load_index = _rec_load
            try:
                raw = await search_mod.find_symbol(
                    name="zz_absent_p19c", project_path=str(self.root))
            finally:
                fo._cached_index_delta = orig_delta
                fo.load_index = orig_load
            return json.loads(raw)

        data = asyncio.run(_drive())
        self.assertEqual(data.get("count"), 0, f"phantom hit: {data}")
        kinds = {k for k, _ in calls}
        self.assertIn("delta", kinds, f"delta probe never ran: {data}")
        self.assertIn("load", kinds, f"load_index probe never ran: {data}")
        for kind, thr in calls:
            self.assertIsNot(
                thr, loop_thread[0],
                f"{kind} ran on the event-loop thread -- a 0-hit freezes the "
                f"loop for every concurrent caller (P19c)")


if __name__ == "__main__":
    unittest.main()
