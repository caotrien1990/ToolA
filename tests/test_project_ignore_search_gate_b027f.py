# -*- coding: utf-8 -*-
"""B027f: the FILE filter `search_code` serves through never learned about the overlay.

Anh's independent function-layer verification (2026-08-23) named the site:
`orchestrator/fileops/common.py` -- `should_scan_for_text(file_path)` has no
`project_root` parameter, while `should_skip_dir` three lines above it does, and
`orchestrator/tools/search.py` passes the unbound function to `publish_snapshot`.
His conclusion -- `!name` reaches INDEX.json and `list_structure` but not
`search_code`, so index and snapshot disagree -- is exactly what measures out:

    should_skip_dir('build', root)          False   <- B027 opened the directory
    _skip_reason(build/made.py, root)       None    <- B027c opened the file
    should_scan_for_text(build/made.py)     False   <- this gate closed it again
    search_code(NEEDLE_B027F)               1 hit, ['src/a.py']

Measuring which of the four unbound call sites DECIDES moves the culprit off the
line he named, which is why this file exists instead of a one-word patch:

    snapshot published by refresh_project_map (BOUND)  ['build/made.py', 'src/a.py']
    search_code over that same snapshot                1 hit, ['src/a.py']
    snapshot id unchanged by the search                True

So `search.py:95` is real but not sufficient. The record read-back
(`_append_record` / `_search_text_record`) re-asks the SAME unbound question per
record, and that is the gate a caller's result actually comes out of. A fix at the
publish site alone would have looked correct in the diff, passed a snapshot-level
test, and left `search_code` answering exactly as wrong as before -- the B027c/d/e
lesson at a fifth layer.

A SECOND defect sits in the same call, in the opposite direction, and predates
B027 entirely: R82 bound `project_root` at the refresh publish so deep data-JSON
is judged by depth, but the search-first fallback publish is unbound, where depth
is unknown and the file is KEPT. Same project, same query, two answers depending
on whether anyone happened to refresh first:

    search-first    2 hits  ['outputs/round0/case01/result.json', 'src/a.py']
    refresh-first   1 hit   ['src/a.py']

Direction note (B027e's rule): for `list_structure` an overlay may only REVEAL,
never HIDE, because a listing that omits a real directory is CASE-2008's shape.
Search is the opposite by design -- hiding produced records is the entire reason
the overlay exists (ToolQ's `runs`/`queue`/`archive`) and B027b already pins that
an ignored directory is absent from the snapshot. So here both directions bind.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import surgical_mapper  # noqa: E402
from orchestrator import search_snapshots  # noqa: E402
from orchestrator.fileops import common as fo  # noqa: E402
from orchestrator.tools.search import find_symbol, search_code  # noqa: E402


# B031: a module-level factory has no `self`, so cleanup cannot be `addCleanup`.
# Every root it hands out is recorded here and removed by `tearDownModule`. Measured
# before this: 17 directories left in %TEMP% by one run of this module alone.
_B031_TEMP_ROOTS: list[Path] = []


def tearDownModule() -> None:
    for _root in _B031_TEMP_ROOTS:
        shutil.rmtree(_root, ignore_errors=True)
    _B031_TEMP_ROOTS.clear()



def _unskip_project(prefix: str, needle: str = "NEEDLE_B027F") -> Path:
    """`build` is a GLOBAL skip; the overlay un-skips it for this project only."""
    root = Path(tempfile.mkdtemp(prefix=prefix))
    _B031_TEMP_ROOTS.append(root)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text(needle + " = 1\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "made.py").write_text(needle + " = 2\n", encoding="utf-8")
    (root / ".orchestrator_ignore").write_text("!build\n", encoding="utf-8")
    return root


def _data_json_project(prefix: str) -> Path:
    """A .json record three directories deep -- past DATA_JSON_MAX_DEPTH = 2."""
    root = Path(tempfile.mkdtemp(prefix=prefix))
    _B031_TEMP_ROOTS.append(root)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("NEEDLE_DJ = 1\n", encoding="utf-8")
    deep = root / "outputs" / "round0" / "case01"
    deep.mkdir(parents=True)
    (deep / "result.json").write_text('{"x": "NEEDLE_DJ"}\n', encoding="utf-8")
    return root


def _hits(root: Path, needle: str) -> list[str]:
    payload = json.loads(asyncio.run(
        search_code(query=needle, project_path=str(root), mode="text")))
    assert payload.get("error_name") is None, payload
    return sorted(
        str(item.get("file", "")).replace("\\", "/")
        for item in (payload.get("results") or []))


def _snapshot_records(root: Path) -> list[str]:
    pin = search_snapshots.pin_current_snapshot(
        root, expected_id=search_snapshots.current_snapshot_id(root))
    try:
        return sorted(
            str(rec.get("path") if isinstance(rec, dict) else getattr(rec, "path", rec))
            for rec in pin.iter_records())
    finally:
        pin.release()


class TheFileFilterTakesAProjectRootTest(unittest.TestCase):
    """Signature parity with `should_skip_dir`, which is three lines above it."""

    def test_should_scan_for_text_accepts_a_project_root(self):
        params = list(inspect.signature(fo.should_scan_for_text).parameters)
        self.assertIn("project_root", params,
                      "the directory gate next to it has taken project_root since B027")

    def test_the_root_is_optional_so_pre_b027f_callers_keep_working(self):
        sig = inspect.signature(fo.should_scan_for_text)
        self.assertIs(sig.parameters["project_root"].default, None)

    def test_the_root_is_forwarded_not_swallowed(self):
        root = _unskip_project("b027f_fwd_")
        made = root / "build" / "made.py"
        self.assertFalse(fo.should_scan_for_text(made),
                         "global rules alone must still skip a build/ file")
        self.assertTrue(fo.should_scan_for_text(made, root),
                        "with the root, `!build` has to reach this gate too")

    def test_it_stays_a_thin_forward_to_the_shared_rule(self):
        """Two implementations of one rule is how B027c happened. Same answer, always."""
        root = _unskip_project("b027f_thin_")
        for candidate in (root / "src" / "a.py", root / "build" / "made.py"):
            self.assertEqual(
                fo.should_scan_for_text(candidate, root),
                surgical_mapper._should_scan_file(candidate, root),
                candidate)


class TheUnskipReachesSearchCodeTest(unittest.TestCase):
    """The end-to-end case Anh's e2e_b027.py has and every earlier B027 test lacks.

    B027 stopped at `should_skip_dir`, B027b at the snapshot record list, B027c at
    `_skip_reason`, B027d at the graph walk, B027e at the structure listing. None of
    them ran a query through `search_code`, which is the tool the overlay was asked
    for in the first place."""

    def test_an_unskipped_directory_is_searchable(self):
        root = _unskip_project("b027f_e2e_")
        self.assertEqual(_hits(root, "NEEDLE_B027F"),
                         ["build/made.py", "src/a.py"])

    def test_without_the_overlay_the_same_tree_hides_it(self):
        root = _unskip_project("b027f_noov_")
        (root / ".orchestrator_ignore").unlink()
        self.assertEqual(_hits(root, "NEEDLE_B027F"), ["src/a.py"])

    def test_find_symbol_agrees_with_search_code(self):
        """find_symbol walks the tree itself instead of reading the snapshot, so it
        is a separate unbound call site with the same defect."""
        root = _unskip_project("b027f_sym_", needle="widget_b027f")
        (root / "src" / "a.py").write_text(
            "def widget_b027f():\n    return 1\n", encoding="utf-8")
        (root / "build" / "made.py").write_text(
            "def widget_b027f():\n    return 2\n", encoding="utf-8")
        payload = json.loads(asyncio.run(
            find_symbol(name="widget_b027f", project_path=str(root))))
        files = sorted(
            str(item.get("file", "")).replace("\\", "/")
            for item in (payload.get("results") or payload.get("definitions") or []))
        self.assertEqual(files, ["build/made.py", "src/a.py"], payload)

    def test_find_callers_agrees_too(self):
        """A third walk in the same function. `include="callers"` runs `_scan`, which
        is a SEPARATE loop from the definition walk and asks the file gate its own
        question -- so binding one and not the other leaves a caller who searched for
        usages a different tree from a caller who searched for definitions.

        Added after mutation-verify: reverting the `_scan` binding left all 13 other
        tests green (M6 survived), which means the fix was right and the test set had
        a hole exactly where the two loops differ."""
        root = _unskip_project("b027f_callers_", needle="widget_b027f")
        (root / "src" / "a.py").write_text(
            "def widget_b027f():\n    return 1\n", encoding="utf-8")
        (root / "build" / "made.py").write_text(
            "from src.a import widget_b027f\n\nwidget_b027f()\n", encoding="utf-8")
        payload = json.loads(asyncio.run(find_symbol(
            name="widget_b027f", project_path=str(root), include="callers")))
        files = sorted({
            str(item.get("file", "")).replace("\\", "/")
            for item in (payload.get("callers") or [])})
        self.assertIn("build/made.py", files, payload)

    def test_the_index_and_the_search_answer_the_same_question(self):
        """Anh's framing: index and snapshot must not disagree. Measured before the
        fix -- INDEX.json ['build/made.py', 'src/a.py'] vs search_code ['src/a.py']."""
        root = _unskip_project("b027f_agree_")
        surgical_mapper.generate_project_map(str(root))
        index = json.loads(
            (root / ".orchestrator_cache" / "INDEX.json").read_text(encoding="utf-8"))
        indexed = sorted(str(k).replace("\\", "/") for k in (index.get("files") or {}))
        self.assertEqual(indexed, _hits(root, "NEEDLE_B027F"))


class TheReadBackIsTheGateThatDecidesTest(unittest.TestCase):
    """Fixing the publish site alone leaves `search_code` answering as wrong as before.

    This is the measurement that moved the culprit off `search.py:95`: with the BOUND
    publish already in place (via refresh_project_map), the snapshot holds
    build/made.py and search_code still refuses it, on a snapshot it did not
    republish. Pins the per-record filter, so a future 'tidy up the duplicate call'
    cannot quietly re-open it."""

    def test_a_record_present_in_the_snapshot_is_returned(self):
        root = _unskip_project("b027f_read_")
        surgical_mapper.generate_project_map(str(root))
        before = search_snapshots.current_snapshot_id(root)
        self.assertIn("build/made.py", _snapshot_records(root),
                      "the bound publish must have kept it -- otherwise this test "
                      "is measuring the publish site, not the read-back")
        self.assertIn("build/made.py", _hits(root, "NEEDLE_B027F"))
        self.assertEqual(search_snapshots.current_snapshot_id(root), before,
                         "the search must not have republished; the read-back is "
                         "what this test is about")


    def test_regex_mode_answers_the_same_as_text_mode(self):
        """`_append_record` (regex) and `_search_text_record` (text) are two separate
        per-record filters with the SAME unbound call. Mutation-verify could not tell
        them apart until this test existed -- the two lines are byte-identical, so a
        revert of either had to be measured through the mode that reaches it."""
        root = _unskip_project("b027f_regex_")
        payload = json.loads(asyncio.run(search_code(
            query="NEEDLE_B027F", project_path=str(root), mode="regex",
            literal_search=False)))
        self.assertIsNone(payload.get("error_name"), payload)
        files = sorted(
            str(item.get("file", "")).replace("\\", "/")
            for item in (payload.get("results") or []))
        self.assertEqual(files, ["build/made.py", "src/a.py"], payload)


class TheTwoPublishPathsAgreeTest(unittest.TestCase):
    """`search.py` and `surgical_mapper` publish the same snapshot two ways. When
    only one binds the root, the answer depends on which ran first -- an invisible
    order dependence, and the INDEX/manifest mismatch R82's own comment warns about."""

    def test_the_unskip_case_does_not_depend_on_who_published(self):
        search_first = _unskip_project("b027f_ord_a_")
        refresh_first = _unskip_project("b027f_ord_b_")
        surgical_mapper.generate_project_map(str(refresh_first))
        self.assertEqual(_hits(search_first, "NEEDLE_B027F"),
                         _hits(refresh_first, "NEEDLE_B027F"))

    def test_the_data_json_rule_does_not_depend_on_who_published(self):
        """The pre-B027 half. Measured: search-first kept
        outputs/round0/case01/result.json, refresh-first dropped it."""
        search_first = _data_json_project("b027f_dj_a_")
        refresh_first = _data_json_project("b027f_dj_b_")
        surgical_mapper.generate_project_map(str(refresh_first))
        self.assertEqual(_hits(search_first, "NEEDLE_DJ"),
                         _hits(refresh_first, "NEEDLE_DJ"))

    def test_a_deep_data_json_is_not_searchable_either_way(self):
        """Names the direction, so 'they agree' cannot be satisfied by both being wrong."""
        for prefix in ("b027f_dj_c_", "b027f_dj_d_"):
            root = _data_json_project(prefix)
            if prefix.endswith("d_"):
                surgical_mapper.generate_project_map(str(root))
            self.assertEqual(_hits(root, "NEEDLE_DJ"), ["src/a.py"], prefix)

    def test_the_bound_partial_is_what_carries_the_root(self):
        """A mutant that drops `project_root=` leaves both functions perfectly correct
        in isolation and B027f completely dead on search."""
        root = _unskip_project("b027f_partial_")
        bound = functools.partial(fo.should_scan_for_text, project_root=root)
        made = root / "build" / "made.py"
        self.assertTrue(bound(made))
        self.assertFalse(fo.should_scan_for_text(made))


class TheDeltaWalkAsksTheSameQuestionTest(unittest.TestCase):
    """`index_delta` decides what counts as added/deleted. Bound directory gate plus
    unbound file gate means an un-skipped file is walked and then dropped, so it never
    shows as `added` and the index never learns it exists."""

    def test_an_unskipped_file_shows_up_as_added(self):
        root = _unskip_project("b027f_delta_")
        surgical_mapper.generate_project_map(str(root))
        (root / "build" / "extra.py").write_text("NEEDLE_B027F = 3\n", encoding="utf-8")
        delta = fo.index_delta(str(root))
        self.assertIsNotNone(delta)
        self.assertIn("build/extra.py", delta.get("added") or [], delta)


if __name__ == "__main__":
    unittest.main()
