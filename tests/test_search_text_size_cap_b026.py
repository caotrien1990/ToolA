"""B026 -- `mode='text'` in `search_code` had NO size budget of any kind.

`mode='regex'` has one (`regex_worker.validate_next_record_budget`), so a runaway
pattern is capped. A plain literal search had none: it re-scanned every byte of
every record, forever, at whatever the repo happened to weigh.

Measured on `dreye_redesign`, a builder repo that keeps site DATA next to its
source (760 records / 230.9 MB reach the scanner):

    SEVEN minified HTML files >2MB  ->  175.6 MB  = 76% of the bytes
    the other 753 files            ->   55.3 MB
    genuine source in that repo     ->   74 files / 0.6 MB   (largest: 0.78 MB)

    re.search over all 760 records  ->  2165 ms
    re.search over the 753 small    ->   504 ms      (-77%)

cProfile of the live tool agreed: of a 3.86s `search_code` call, 2.249s was
`re.Pattern.search` against only 0.448s of snapshot blob reads. It is a SCAN-cost
problem, not an I/O one -- so trimming what gets scanned is the fix, and skipping
the read would not have been.

Live daemon, paired alternating runs (machine under load, so medians of 5):

    cap on (2 MB)   1338 ms
    cap off (=0)    3387 ms      -60%

WHERE the cap lives is the whole design, and these tests pin it:
  * NOT in `_skip_reason` / `SKIP_DIRS` -- those feed INDEX.json, and CAN-042 pins
    that set as a single source. A 25MB page.html a CA is actively editing must
    KEEP its index entry or write_fix loses its target.
  * NOT in `should_scan_for_text` -- `index_delta` shares it, and a file that
    vanished from "should be scanned" would distort drift reporting.
  * Skipped records are COUNTED and REPORTED (`skipped_large_files`), never
    dropped silently -- a hit hiding inside a capped file must not come back as a
    confident 0-hit, which is the same failure A03 exists to prevent.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.tools import search as S  # noqa: E402


NEEDLE = "b026_" + "needle_marker"


def _repo_with_one_big_and_one_small(tmp: Path) -> Path:
    root = tmp / "repo"
    (root / "src").mkdir(parents=True)
    # small: real source, holds the needle
    (root / "src" / "small.js").write_text(
        "function ok(){ return '%s'; }\n" % NEEDLE, encoding="utf-8"
    )
    # big: >2MB of padding, also holds the needle on its own line
    pad = "x" * 4000
    lines = [pad] * 700 + ["const marker = '%s';" % NEEDLE] + [pad] * 100
    (root / "src" / "big.html").write_text("\n".join(lines), encoding="utf-8")
    assert (root / "src" / "big.html").stat().st_size > S.MAX_TEXT_RECORD_BYTES
    assert (root / "src" / "small.js").stat().st_size < S.MAX_TEXT_RECORD_BYTES
    return root


def _search(root: Path, **kw) -> dict:
    return json.loads(asyncio.run(
        S.search_code(query=NEEDLE, project_path=str(root), max_results=50, **kw)
    ))


class TheCapTrimsScanCostWithoutHidingAnything(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo_with_one_big_and_one_small(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_default_cap_is_two_megabytes(self) -> None:
        self.assertEqual(S.MAX_TEXT_RECORD_BYTES, 2_000_000)

    def test_a_record_over_the_cap_is_skipped_and_the_small_one_is_not(self) -> None:
        d = _search(self.root)
        self.assertIs(d.get("ok"), True, d)
        files = {r["file"] for r in d.get("results", [])}
        self.assertTrue(any(f.endswith("small.js") for f in files),
                        "the small source file must still be searched: %s" % files)
        self.assertFalse(any(f.endswith("big.html") for f in files),
                         "the >2MB record should not have been scanned: %s" % files)

    def test_the_skip_is_reported_never_silent(self) -> None:
        d = _search(self.root)
        self.assertEqual(d.get("skipped_large_count"), 1, d)
        listed = d.get("skipped_large_files") or []
        self.assertEqual(len(listed), 1, listed)
        self.assertTrue(listed[0]["file"].endswith("big.html"), listed)
        self.assertGreater(listed[0]["size_bytes"], S.MAX_TEXT_RECORD_BYTES, listed)
        self.assertIn("max_file_bytes", d.get("skipped_large_hint", ""),
                      "the hint must name the knob that turns the cap off")

    def test_max_file_bytes_zero_turns_the_cap_off(self) -> None:
        d = _search(self.root, max_file_bytes=0)
        files = {r["file"] for r in d.get("results", [])}
        self.assertTrue(any(f.endswith("big.html") for f in files),
                        "max_file_bytes=0 must scan everything again: %s" % files)
        self.assertNotIn("skipped_large_count", d, d)

    def test_a_repo_with_no_large_file_is_completely_unaffected(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "repo"
            (root / "src").mkdir(parents=True)
            (root / "src" / "a.py").write_text("v = '%s'\n" % NEEDLE, encoding="utf-8")
            capped = _search(root)
            uncapped = _search(root, max_file_bytes=0)
        self.assertEqual(capped.get("results"), uncapped.get("results"))
        self.assertNotIn("skipped_large_count", capped, capped)

    def test_the_count_is_the_true_total_not_the_length_of_the_sample(self) -> None:
        """`skipped_large_files` lists at most 20; the COUNT must not stop at 20."""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "repo"
            (root / "src").mkdir(parents=True)
            body = ("y" * 4000 + "\n") * 600          # ~2.4 MB each
            for i in range(23):
                (root / "src" / ("big%02d.html" % i)).write_text(body, encoding="utf-8")
            (root / "src" / "s.js").write_text("'%s'\n" % NEEDLE, encoding="utf-8")
            d = _search(root)
        self.assertEqual(d.get("skipped_large_count"), 23, d.get("skipped_large_count"))
        self.assertEqual(d.get("skipped_large_listed"), 20, d.get("skipped_large_listed"))


class TheKnobMustBeReachableFromOutside(unittest.TestCase):
    """B-01 (2026-08-10) taught this exact lesson twelve days earlier: a parameter
    the backend understands is still UNSUPPORTED_FIELD until the registry declares
    it. B026 shipped without the registry line and every HTTP caller got
    `unsupported_fields: ["max_file_bytes"]` -- an escape hatch nobody can reach is
    not an escape hatch. This test is the guard that was missing then."""

    def test_the_registry_declares_max_file_bytes_for_search_code(self) -> None:
        from orchestrator.core import registry_data

        blob = json.dumps(registry_data.__dict__, default=str)
        self.assertIn("max_file_bytes", blob,
                      "registry_data does not declare max_file_bytes, so the HTTP "
                      "dispatcher will answer UNSUPPORTED_FIELD and the cap becomes "
                      "unescapable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
