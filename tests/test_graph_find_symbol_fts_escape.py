"""graph_find_symbol must never let a search term reach FTS5 as query SYNTAX.

Reported from [Q] (ToolQ), which consumes [A]'s graph channel: looking up a
dotted symbol returned ``error`` instead of results. Reproduced here.

The defect was in the *fallback* branch of ``graph_find_symbol``
(``orchestrator/graph/query.py``). The first FTS attempt correctly wrapped the
term in quotes (``f'"{safe}"'``) so FTS5 treats it as a literal phrase. When the
phrase matched nothing, the fallback passed ``query`` RAW into MATCH -- undoing
the escaping it had just done one line above. FTS5 then parsed the user's term:

    'start_orchestrator.bat'  -> fts5: syntax error near "."
    'foo(bar)'                -> fts5: syntax error near "foo"
    ''                        -> fts5: syntax error near ""
    'a-b'                     -> no such column: b     <- read as a COLUMN filter
    'col:val'                 -> no such column: col   <- read as a COLUMN filter

The last two are the nastier pair: they are not syntax errors, they are FTS5
column-scoped queries. A term containing ``:`` or ``-`` silently addressed a
column instead of searching text, so the failure mode was an error message that
named a "column" the caller never mentioned.

These tests pin BEHAVIOUR in both directions:
  * no ordinary term -- however punctuated -- may produce ``error``, and
  * the fallback must still WIDEN the search (prefix match), otherwise it could
    be "fixed" by deleting it, and `routes_read` would stop finding
    `routes_readonly`.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# graph_enabled() is read at call time, but the modules below pin it at import
# in some orders -- set it before importing (see the GRAPH_ENABLED import-order
# family, A13).
os.environ["GRAPH_ENABLED"] = "1"

from orchestrator.graph import query as gq  # noqa: E402
from orchestrator.graph import store as gs  # noqa: E402

# Terms that are perfectly ordinary symbol/file names but are ALSO valid (or
# invalid) FTS5 syntax if passed through unquoted.
HOSTILE_TERMS = [
    "start_orchestrator.bat",   # dot   -> syntax error near "."
    "clm_lib.routes_read",      # dot, the originally reported term
    "foo(bar)",                 # paren -> syntax error near "foo"
    "a-b",                      # dash  -> read as column "b"
    "col:val",                  # colon -> read as column "col"
    "",                         # empty -> syntax error near ""
    '"quoted"',                 # embedded quotes
    "*",                        # bare prefix operator
    "^caret",                   # column-start operator
    "NEAR(a b)",                # an FTS5 function name
    "x OR y",                   # boolean operator
]


class _GraphDBBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fts_escape_", dir="D:\\pytmp_c1608"
                                    if os.path.isdir("D:\\pytmp_c1608") else None)
        gs.init_db(self.tmp)
        conn = gs.get_db(self.tmp)
        gs.upsert_file(conn, path="a.py", content_hash="h1", language="python",
                       size=10, modified_at=1)
        gs.insert_nodes(conn, [
            {"id": "n1", "kind": "function", "name": "routes_read",
             "qualified_name": "clm_lib.routes_read", "file_path": "a.py",
             "language": "python", "start_line": 1, "end_line": 2},
            {"id": "n2", "kind": "function", "name": "routes_readonly",
             "qualified_name": "clm_lib.routes_readonly", "file_path": "a.py",
             "language": "python", "start_line": 5, "end_line": 6},
        ])
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TermNeverReachesFTSAsSyntax(_GraphDBBase):
    def test_no_hostile_term_produces_an_error(self):
        for term in HOSTILE_TERMS:
            with self.subTest(term=term):
                payload = gq.graph_find_symbol(self.tmp, term, limit=5)
                self.assertIsNone(
                    payload.get("error"),
                    f"term {term!r} leaked into FTS5 as query syntax: "
                    f"{payload.get('error')}")

    def test_dotted_term_that_exists_is_found(self):
        # the exact-name path; guards against "fixing" the error by returning
        # an empty result for everything.
        payload = gq.graph_find_symbol(self.tmp, "clm_lib.routes_read", limit=5)
        self.assertIsNone(payload.get("error"))
        self.assertTrue(payload.get("results"),
                        "an indexed dotted symbol must still be findable")

    def test_dotted_miss_is_an_empty_result_not_an_error(self):
        # This is the reported shape: a dotted term that is NOT indexed. Before
        # the fix it returned error; it must now be an honest empty result.
        payload = gq.graph_find_symbol(self.tmp, "start_orchestrator.bat", limit=5)
        self.assertIsNone(payload.get("error"))
        self.assertEqual(payload.get("results"), [])


class FallbackStillWidens(_GraphDBBase):
    def test_prefix_fallback_finds_longer_symbol(self):
        """The fallback must not be deleted, only made safe.

        `routes_read` matches n1 exactly, so to exercise the fallback we need a
        term with NO exact node name and NO exact phrase hit, whose prefix does
        match: `routes_readonl` -> `routes_readonly`.
        """
        payload = gq.graph_find_symbol(self.tmp, "routes_readonl", limit=5)
        self.assertIsNone(payload.get("error"))
        names = {r.get("name") for r in (payload.get("results") or [])}
        self.assertIn("routes_readonly", names,
                      "the widening fallback no longer widens; a prefix that "
                      "should reach routes_readonly returned nothing")

    def test_widening_does_not_fire_when_exact_match_exists(self):
        # exact name wins: `routes_read` must not be diluted into both symbols.
        payload = gq.graph_find_symbol(self.tmp, "routes_read", limit=5)
        self.assertIsNone(payload.get("error"))
        names = [r.get("name") for r in (payload.get("results") or [])]
        self.assertEqual(names, ["routes_read"])


# ---------------------------------------------------------------------------
# The `"` -> `""` escape itself. Found by sabotage: deleting `safe =
# query.replace('"', '""')` entirely left every test above green, because none
# of the hostile terms containing a quote happened to produce a *different*
# result set unescaped -- they just returned 0 either way.
#
# That escape is not cosmetic. Without it a term can close the phrase and inject
# its own FTS5 operators:
#
#     term    zzznotathing" OR "routes_read
#     escaped "zzznotathing"" OR ""routes_read"   -> 0 rows (correct: literal)
#     RAW     "zzznotathing" OR "routes_read"     -> 1 row  (INJECTED an OR)
#
# so a caller could smuggle query syntax through what is documented as a plain
# symbol name. Pin the escape by its observable consequence, not its shape.
# ---------------------------------------------------------------------------
class QuoteEscapePreventsInjection(_GraphDBBase):
    def test_quote_cannot_break_out_of_the_phrase(self):
        # Neither half of this term is an indexed symbol as written; only an
        # injected `OR` could make it match anything.
        payload = gq.graph_find_symbol(
            self.tmp, 'zzznotathing" OR "routes_read', limit=5)
        self.assertIsNone(payload.get("error"))
        self.assertEqual(
            payload.get("results"), [],
            "an embedded quote escaped the phrase and injected FTS5 syntax")

    def test_embedded_quote_does_not_error(self):
        # `a"b` unescaped is an unterminated string; escaped it is just a term.
        payload = gq.graph_find_symbol(self.tmp, 'a"b', limit=5)
        self.assertIsNone(payload.get("error"))


if __name__ == "__main__":
    unittest.main()
