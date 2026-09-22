# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\orchestrator`
- Generated: 2026-09-22T17:10:54
- Files indexed: 101

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

- `source`: 100 files, 38722 LOC
- `ui`: 1 files, 62 LOC

## Highest Value Files

- `tools/write/core.py` | group `source` | lang `python` | LOC 1995 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `session/store.py` | group `source` | lang `python` | LOC 1908 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tools/search.py` | group `source` | lang `python` | LOC 1480 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `search_snapshots.py` | group `source` | lang `python` | LOC 1304 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `graph/tools.py` | group `source` | lang `python` | LOC 1149 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `server/mcp_stdio.py` | group `source` | lang `python` | LOC 1520 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tools/ocr_review.py` | group `source` | lang `python` | LOC 753 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `server/audit_writer.py` | group `source` | lang `python` | LOC 1089 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `core/ca_policy_matrix.py` | group `source` | lang `python` | LOC 843 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `lifecycle/restart_control_service.py` | group `source` | lang `python` | LOC 842 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `mcp_packs/generator.py` | group `source` | lang `python` | LOC 1090 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `graph/query.py` | group `source` | lang `python` | LOC 851 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `graph/indexer.py` | group `source` | lang `python` | LOC 820 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `graph/rules.py` | group `source` | lang `python` | LOC 671 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `graph/analysis_view.py` | group `source` | lang `python` | LOC 682 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `graph/extractors/python.py` | group `source` | lang `python` | LOC 626 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `runtime/desktop_dialogs.py` | group `source` | lang `python` | LOC 523 | weight 13: large_file: use symbols, search, or bounded ranges before full read
- `runtime/desktop_dialog_controls.py` | group `source` | lang `python` | LOC 530 | weight 13: large_file: use symbols, search, or bounded ranges before full read
- `graph/extractors/javascript.py` | group `source` | lang `python` | LOC 558 | weight 13: large_file: use symbols, search, or bounded ranges before full read
- `mission/tools.py` | group `source` | lang `python` | LOC 514 | weight 13: large_file: use symbols, search, or bounded ranges before full read

## Entrypoints


## Hotspots

- `tools/write/core.py`: complexity 492, 1995 LOC, group `source`
- `session/store.py`: complexity 337, 1908 LOC, group `source`
- `tools/search.py`: complexity 335, 1480 LOC, group `source`
- `search_snapshots.py`: complexity 309, 1304 LOC, group `source`
- `graph/tools.py`: complexity 257, 1149 LOC, group `source`
- `server/mcp_stdio.py`: complexity 245, 1520 LOC, group `source`
- `council/glass_dashboard.html`: complexity 236, 62 LOC, group `ui`
- `tools/ocr_review.py`: complexity 183, 753 LOC, group `source`
- `server/audit_writer.py`: complexity 179, 1089 LOC, group `source`
- `graph/rules.py`: complexity 175, 671 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
