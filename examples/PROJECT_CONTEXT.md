# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\examples`
- Generated: 2026-10-08T11:37:15
- Files indexed: 10

## First Actions

1. `cache_status` -> `get_project_summary` -> (if has_session_log) `get_usage_stats`
2. Navigate: `find_symbol(name)` -> lay range_id -> `read_file_range` (<=200 lines)
3. KHONG doc `.orchestrator_cache/INDEX.json` raw -- dung tool query
4. Sau write: `validate_file` -> `cache_delta`

## Search Routing

| Goal | Tool | Use search_code when |
|------|------|----------------------|
| Find function/class def | `find_symbol` | find_symbol misses |
| Filter files by group/LOC | `query_index` | -- |
| Find callers/imports | `search_code` | always valid |
| Regex pattern search | `search_code` | always valid |

Guardrail: NOT first tool in session -- NOT replacing find_symbol for defs -- escape `.()+-` in regex

## Groups

- `doc`: 3 files, 120 LOC
- `script`: 1 files, 150 LOC
- `source`: 1 files, 26 LOC
- `ui`: 1 files, 5 LOC
- `unknown`: 4 files, 244 LOC

## Highest Value Files

- `mcp_175_tool_rag.html` | group `ui` | lang `html` | LOC 5 | weight 10: metadata_first: search or read narrow ranges
- `_extract.js` | group `source` | lang `javascript` | LOC 26 | weight 5: metadata_first: search or read narrow ranges
- `CORE_RULE.txt` | group `unknown` | lang `text` | LOC 95 | weight 4: metadata_first: search or read narrow ranges
- `api/write_fix_dry_run.ps1` | group `script` | lang `powershell` | LOC 150 | weight 3: metadata_first: search or read narrow ranges
- `SLIM_RULE.txt` | group `unknown` | lang `text` | LOC 75 | weight 2: metadata_first: search or read narrow ranges
- `SLIM_RULE_GATED.txt` | group `unknown` | lang `text` | LOC 73 | weight 2: metadata_first: search or read narrow ranges
- `SLIM_RULE_INLINE.txt` | group `unknown` | lang `text` | LOC 1 | weight 1: metadata_first: search or read narrow ranges
- `tooling/repo_vs_runtime_routing.md` | group `doc` | lang `markdown` | LOC 6 | weight 1: small_context: full read acceptable if needed
- `reports/coverage_report_contract.md` | group `doc` | lang `markdown` | LOC 107 | weight 1: small_context: full read acceptable if needed
- `tooling/tool_tiers.md` | group `doc` | lang `markdown` | LOC 7 | weight 1: small_context: full read acceptable if needed

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

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
