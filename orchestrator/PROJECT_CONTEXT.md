# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\orchestrator`
- Generated: 2026-10-14T12:00:13
- Files indexed: 102

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

- `source`: 101 files, 40388 LOC
- `ui`: 1 files, 62 LOC

## Highest Value Files

- `tools/write/core.py` | group `source` | lang `python` | LOC 2191 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tools/search.py` | group `source` | lang `python` | LOC 2055 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `session/store.py` | group `source` | lang `python` | LOC 1942 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `search_snapshots.py` | group `source` | lang `python` | LOC 1312 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `graph/tools.py` | group `source` | lang `python` | LOC 1204 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `server/mcp_stdio.py` | group `source` | lang `python` | LOC 1604 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `server/audit_writer.py` | group `source` | lang `python` | LOC 1472 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `core/ca_policy_matrix.py` | group `source` | lang `python` | LOC 900 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tools/ocr_review.py` | group `source` | lang `python` | LOC 757 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `lifecycle/restart_control_service.py` | group `source` | lang `python` | LOC 843 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `mcp_packs/generator.py` | group `source` | lang `python` | LOC 1100 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `graph/indexer.py` | group `source` | lang `python` | LOC 983 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `graph/query.py` | group `source` | lang `python` | LOC 883 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `graph/rules.py` | group `source` | lang `python` | LOC 684 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `graph/analysis_view.py` | group `source` | lang `python` | LOC 724 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `graph/extractors/python.py` | group `source` | lang `python` | LOC 626 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `graph/evolution.py` | group `source` | lang `python` | LOC 612 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `core/call_cache.py` | group `source` | lang `python` | LOC 624 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `graph/extractors/javascript.py` | group `source` | lang `python` | LOC 558 | weight 13: large_file: use symbols, search, or bounded ranges before full read
- `mission/tools.py` | group `source` | lang `python` | LOC 517 | weight 13: large_file: use symbols, search, or bounded ranges before full read

## Entrypoints


## Hotspots

- `tools/write/core.py`: complexity 526, 2191 LOC, group `source`
- `tools/search.py`: complexity 521, 2055 LOC, group `source`
- `session/store.py`: complexity 341, 1942 LOC, group `source`
- `search_snapshots.py`: complexity 310, 1312 LOC, group `source`
- `graph/tools.py`: complexity 275, 1204 LOC, group `source`
- `server/mcp_stdio.py`: complexity 263, 1604 LOC, group `source`
- `council/glass_dashboard.html`: complexity 236, 62 LOC, group `ui`
- `server/audit_writer.py`: complexity 231, 1472 LOC, group `source`
- `core/ca_policy_matrix.py`: complexity 191, 900 LOC, group `source`
- `tools/ocr_review.py`: complexity 184, 757 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
