# [A] Diagnostic Matrix Suite

Read-only diagnostic scripts that give [A] a lens on its OWN health — tool surface,
soft-data, drift, overlap, bloat. Preserved here as a cohesive suite so they can be
**integrated into [A] as native tools later** (see §Integration). None of them ever
edit or remove anything; they emit reports for human review.

## The suite

| Script / tool | Layer it guards | What it finds |
|---|---|---|
| `scripts/sync_tool_matrix.py` | Code ↔ Schema ↔ Skills ↔ Inventory | **Drift**: missing_schema, ghost_schema, skill_ref_missing_tool, inventory_outdated/ghost, obsolete_payload, alias_drift. |
| `scripts/soft_data_reconcile.py` | schema/param · alias · see_also · skill · function · usage | **Overlap**: PARAM_CONCEPT_CONFLICT, AMBIGUOUS_PARAM_NAME, CONFUSABLE_PAIRS, DESC_BLOAT/EMPTY, DEPRECATED, SLIM_CANDIDATE; **DEFECTS**: see_also DANGLING, ALIAS_BROKEN_TARGET. |
| `scripts/gen_alias_contract.py` | alias | Regenerates `docs/ALIAS_CONTRACT.generated.{md,json}` from runtime `_ARG_ALIASES` (SOT). |
| `scripts/precommit_check.bat` | cadence | Runs `sync_tool_matrix --ci` + `soft_data_reconcile --ci`; either DEFECT blocks the commit (advisory findings never block). |

Existing [A] TOOLS already covering adjacent ground (do not duplicate): `audit_tool_matrix`
(drift), `architecture_audit_compact` (compact digest), `acceptance_dashboard` (matrix +
alias_contract_test + schema checks), `cbm_dead_code` (source dead-code/bug scan).

## Run

```
venv312\Scripts\python.exe scripts\sync_tool_matrix.py --ci        # drift gate (exit 1 on drift)
venv312\Scripts\python.exe scripts\soft_data_reconcile.py           # full overlap/bloat report
venv312\Scripts\python.exe scripts\soft_data_reconcile.py --ci      # defect gate (exit 1 on DEFECTS only)
venv312\Scripts\python.exe scripts\soft_data_reconcile.py --json-out report.json
scripts\precommit_check.bat                                         # both gates
```

Each script imports `code_tools` + `orchestrator_server` standalone (late registrations,
no HTTP) — safe to run while [A] is up; used by pre-commit / CI.

## Design guarantees (why the reports are trustworthy)

- **No cry-wolf**: DEFECTS (real bugs) are separated from ADVISORY (style/overlap). Only
  DEFECTS gate `--ci`.
- **Protected surface never flagged**: curated-core, deprecated-on-sunset, and intentional
  tool families / alias-params are labeled, not treated as junk.
- **Family-aware + IDF-weighted redundancy**: shared boilerplate ("governed git argv") and
  same-prefix verb families (git_status/git_commit) do not create false pairs.
- **Usage is a WEAK signal**: the USAGE_LOG window is small; count=0 means "unused recently",
  never "dead" — stated in every report.
- Verified by `tests/test_soft_data_reconcile.py` (9 tests) + `tests/test_alias_contract*.py`.

## Integration into [A] (future — the deliverable to wire when ready)

Goal: make `soft_data_reconcile` callable as a native **T1 read-only tool** (like
`audit_tool_matrix`), so a CA can request a surface-health report in-band.

Steps (mirror `audit_tool_matrix` + the 4-layer registration — see the `soft_data_reconcile.build()`
function which already returns a JSON-ready dict):
1. Add an async wrapper in `code_tools.py` (or `tools_v15.py`) that calls
   `soft_data_reconcile.build(project_path)` via `asyncio.to_thread` (the build imports
   orchestrator_server — keep it OFF the event loop, per the on-loop-hang lesson) and returns
   `json.dumps(report)`.
2. Register across the 4 layers: TOOL_MAP dispatch + TOOL_SCHEMAS entry + TOOL_PHASES `T1`
   + regenerate `tool_surface_current.md` via `sync_tool_matrix.py --generate`; verify
   `--ci` drift 0.
3. Schema: single optional `project_path`; description "T1 READ-ONLY: cross-layer tool-surface
   overlap/bloat/conflict report (advisory)".
4. Keep the CLI + `precommit_check.bat` gate as-is (CI path independent of the tool).

Until then, the scripts are the SOT and are run manually / via the pre-commit gate.
