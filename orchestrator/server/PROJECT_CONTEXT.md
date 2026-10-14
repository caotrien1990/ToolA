# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\orchestrator\server`
- Generated: 2026-10-08T13:17:12
- Files indexed: 6

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

- `source`: 6 files, 3883 LOC

## Highest Value Files

- `mcp_stdio.py` | group `source` | lang `python` | LOC 1603 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `audit_writer.py` | group `source` | lang `python` | LOC 1472 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `executor_lanes.py` | group `source` | lang `python` | LOC 376 | weight 9: symbol_routed: use function/class refs from INDEX first
- `accounting.py` | group `source` | lang `python` | LOC 299 | weight 8: symbol_routed: use function/class refs from INDEX first
- `fair_admission.py` | group `source` | lang `python` | LOC 132 | weight 6: symbol_routed: use function/class refs from INDEX first
- `__init__.py` | group `source` | lang `python` | LOC 1 | weight 5: metadata_first: search or read narrow ranges

## Entrypoints


## Hotspots

- `mcp_stdio.py`: complexity 263, 1603 LOC, group `source`
- `audit_writer.py`: complexity 231, 1472 LOC, group `source`
- `accounting.py`: complexity 54, 299 LOC, group `source`
- `executor_lanes.py`: complexity 44, 376 LOC, group `source`
- `fair_admission.py`: complexity 31, 132 LOC, group `source`
- `__init__.py`: complexity 0, 1 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
