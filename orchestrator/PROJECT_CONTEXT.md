# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\orchestrator`
- Generated: 2026-08-18T12:02:52
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

- `source`: 6 files, 5618 LOC

## Highest Value Files

- `session/store.py` | group `source` | lang `python` | LOC 1704 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `search_snapshots.py` | group `source` | lang `python` | LOC 1003 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tools/search.py` | group `source` | lang `python` | LOC 631 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `core/registry_data.py` | group `source` | lang `python` | LOC 1616 | weight 10: large_file: use symbols, search, or bounded ranges before full read
- `server/accounting.py` | group `source` | lang `python` | LOC 299 | weight 8: symbol_routed: use function/class refs from INDEX first
- `server/executor_lanes.py` | group `source` | lang `python` | LOC 365 | weight 8: symbol_routed: use function/class refs from INDEX first

## Entrypoints


## Hotspots

- `session/store.py`: complexity 302, 1704 LOC, group `source`
- `search_snapshots.py`: complexity 246, 1003 LOC, group `source`
- `tools/search.py`: complexity 166, 631 LOC, group `source`
- `server/accounting.py`: complexity 54, 299 LOC, group `source`
- `server/executor_lanes.py`: complexity 39, 365 LOC, group `source`
- `core/registry_data.py`: complexity 2, 1616 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
