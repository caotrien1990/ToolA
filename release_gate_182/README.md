# Release-gate 182 historical fixtures

P-50 disposition (2026-11-02): **fixtures by design**, permanent contract
fixtures (option a in CODO_5POINT_PACK_20261102.md). This directory is not a
release approval gate. No generator or current audit measurements are claimed.
The contract tests must run from tracked files in a clean checkout. New release
approval requires separately produced, independently verified run evidence.

These files preserve the legacy test contract; they are not a new run of ToolA
and do not certify the current tools or the 2026-11-01 release.

- baseline_before_fix.json and report_viewer.html were recovered without
  inventing results from the existing 8. ToolA/Trash Bin archive.
- ../fix_coverage_182.md matches the historical tracked coverage matrix
  (deleted in a5afa84), recovered from the live working-tree copy.
- The baseline's tool_1..tool_38 rows and captured statuses are legacy fixture
  data, not independently verified measurements. The HTML is the historical
  minimal contract fixture, not a complete interactive reporting product.
- Current release evidence belongs in the final-sweep validation report and
  Muse's independent full-suite report.

Only these named artifacts are unignored. Other generated release directories
remain ignored. Keeping the fixtures with their tests makes a clean checkout
reproducible; it does not turn placeholder data into release evidence.
