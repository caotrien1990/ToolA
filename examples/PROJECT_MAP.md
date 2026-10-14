# Project Map

- Schema: 2
- Generated: 2026-10-08T13:11:41
- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\examples`
- Files indexed: 10

## Groups

- `doc`: 3 files, 120 LOC
- `script`: 1 files, 150 LOC
- `source`: 1 files, 26 LOC
- `ui`: 1 files, 5 LOC
- `unknown`: 4 files, 244 LOC

## Entrypoints


## Hotspots

- `mcp_175_tool_rag.html`: complexity 569, 5 LOC, group `ui`
- `CORE_RULE.txt`: complexity 60, 95 LOC, group `unknown`
- `SLIM_RULE.txt`: complexity 29, 75 LOC, group `unknown`
- `api/write_fix_dry_run.ps1`: complexity 23, 150 LOC, group `script`
- `SLIM_RULE_GATED.txt`: complexity 20, 73 LOC, group `unknown`
- `SLIM_RULE_INLINE.txt`: complexity 9, 1 LOC, group `unknown`
- `tooling/repo_vs_runtime_routing.md`: complexity 6, 6 LOC, group `doc`
- `reports/coverage_report_contract.md`: complexity 4, 107 LOC, group `doc`
- `tooling/tool_tiers.md`: complexity 2, 7 LOC, group `doc`
- `_extract.js`: complexity 0, 26 LOC, group `source`

## Agent Usage

- Start with `.orchestrator_cache/INDEX.json` for routing.
- Prefer `PROJECT_CONTEXT.md` for a compact route guide before opening source.
- Use function/class refs to call `read_file_range` only on the needed line span.
- Use per-file cache JSON for imports, purpose, and symbol details before opening source.
