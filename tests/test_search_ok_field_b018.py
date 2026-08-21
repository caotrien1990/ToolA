"""B018 -- `search_code` omitted `ok` on the success path.

Measured against the live daemon at generation 7d870da7e301:

    query="_ACTION_TO_CLASS"      -> count=2  "ok" not in the response
    query="zzz_no_such_symbol"    -> count=0  "ok" not in the response
    query=""                      -> ok=False error_name=MISSING_REQUIRED_FIELD

So `search_code` answers `ok:False` when it fails and answers *nothing* when it
succeeds. Every other tool surveyed answers `ok` on both paths -- read_file_range,
git_status, validate_file, list_sessions, cbm_graph_status, regex_search. A CA
that writes the ordinary guard

    if not resp.get("ok"): ...treat as failure...

therefore reads a SUCCESSFUL search with 2 real hits as a failure, because a
missing key is falsy in exactly the same way `False` is. That is the same defect
family as B012/F021/B017: the contract a caller is told to rely on is enforced in
one place and not honoured in another, and the failing read names something the
system did in fact do.

It is the more dangerous half of that family, because it fails in the SAFE-looking
direction: nothing errors, no gate denies, the results are right there in the
payload -- the caller simply throws them away and goes looking for a bug that
does not exist.

Two things are pinned, and the second is the one that keeps the field honest:

  1. `ok` is present and True on the success path, hits AND zero-hits alike; and
  2. `ok` is still False on every failure path -- adding the key must not become
     "always say ok", or the field stops carrying information.

Run: ./venv312/Scripts/python.exe -m pytest tests/test_search_ok_field_b018.py -v
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import surgical_mapper
from orchestrator.tools import search as search_module


class SearchCodeAnswersOkOnSuccess(unittest.IsolatedAsyncioTestCase):
    """A successful search must say so, not merely fail to say it failed."""

    def setUp(self) -> None:
        # basetemp outside the repo so the fixture never inherits the repo ruff.toml.
        self.root = Path(tempfile.mkdtemp(prefix="b018_ok_")).resolve()
        (self.root / "keep.py").write_text(
            "def findable_symbol_b018():\n    return 1\n", encoding="utf-8")
        surgical_mapper.generate_project_map(str(self.root))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    async def _search(self, query: str, **kw) -> dict:
        raw = await search_module.search_code(
            query=query, path=str(self.root), project_path=str(self.root),
            mode=kw.pop("mode", "text"), max_results=kw.pop("max_results", 20), **kw)
        return json.loads(raw)

    async def test_a_hit_says_ok_true(self) -> None:
        """The exact measured failure: 2 real hits and no `ok` in the payload."""
        p = await self._search("findable_symbol_b018")
        self.assertGreaterEqual(p.get("count", 0), 1, "fixture symbol must be indexed")
        self.assertIn(
            "ok", p,
            "search_code returned %d hits but omitted `ok`, so the ordinary guard "
            "`if not resp.get(\"ok\")` reads a successful search as a failure"
            % p.get("count", 0))
        self.assertIs(p["ok"], True, p.get("ok"))

    async def test_a_clean_zero_hit_says_ok_true(self) -> None:
        """`not found` is a successful answer to a question, not a failure.

        A03 already distinguishes a clean 0-hit from an inconclusive one via
        `stale_index`; `ok` must not be overloaded to mean "found something".
        """
        p = await self._search("zzz_no_such_symbol_b018_qqq")
        self.assertEqual(p.get("count"), 0)
        self.assertIn("ok", p, "a clean zero-hit omitted `ok`")
        self.assertIs(p["ok"], True,
                      "a search that ran and found nothing is ok=True; "
                      "'found nothing' is carried by count/stale_index, not by ok")

    async def test_ok_is_not_the_only_thing_that_changed(self) -> None:
        """The rest of the success payload must be untouched by the fix."""
        p = await self._search("findable_symbol_b018")
        for key in ("results", "count", "mode", "max_results",
                    "max_context_lines", "bounded", "snapshot_id"):
            self.assertIn(key, p, "success payload lost %r" % key)


class OkMustStillBeAbleToBeFalse(unittest.IsolatedAsyncioTestCase):
    """Adding the key must not become 'always say ok'."""

    async def _search(self, **kw) -> dict:
        raw = await search_module.search_code(**kw)
        return json.loads(raw)

    async def test_a_missing_query_is_still_a_failure(self) -> None:
        p = await self._search(query="", path="", project_path="")
        self.assertIs(p.get("ok"), False,
                      "an empty query must still answer ok=False, got %r" % p.get("ok"))
        self.assertTrue(p.get("error_name"),
                        "a failure must still name itself: %r" % p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
