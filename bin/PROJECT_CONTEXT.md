# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\bin`
- Generated: 2026-11-21T06:06:26
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

- `script`: 3 files, 172 LOC
- `source`: 3 files, 538 LOC

## Highest Value Files

- `layer2_watch.py` | group `source` | lang `python` | LOC 344 | weight 11: symbol_routed: use function/class refs from INDEX first
- `evaluate_tool_retrieval.py` | group `source` | lang `python` | LOC 125 | weight 6: symbol_routed: use function/class refs from INDEX first
- `calibrate_layer2.py` | group `source` | lang `python` | LOC 69 | weight 5: symbol_routed: use function/class refs from INDEX first
- `a.ps1` | group `script` | lang `powershell` | LOC 153 | weight 3: metadata_first: search or read narrow ranges
- `watch_layer2.ps1` | group `script` | lang `powershell` | LOC 10 | weight 1: metadata_first: search or read narrow ranges
- `lock_holder_layer2.ps1` | group `script` | lang `powershell` | LOC 9 | weight 1: metadata_first: search or read narrow ranges

## Entrypoints


## Hotspots

- `layer2_watch.py`: complexity 91, 344 LOC, group `source`
- `a.ps1`: complexity 25, 153 LOC, group `script`
- `evaluate_tool_retrieval.py`: complexity 20, 125 LOC, group `source`
- `calibrate_layer2.py`: complexity 14, 69 LOC, group `source`
- `watch_layer2.ps1`: complexity 0, 10 LOC, group `script`
- `lock_holder_layer2.ps1`: complexity 0, 9 LOC, group `script`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
