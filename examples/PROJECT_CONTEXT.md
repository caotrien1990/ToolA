# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\examples`
- Generated: 2026-08-18T10:31:24
- Files indexed: 3

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

- `unknown`: 3 files, 88 LOC

## Highest Value Files

- `CORE_RULE.txt` | group `unknown` | lang `text` | LOC 42 | weight 2: metadata_first: search or read narrow ranges
- `SLIM_RULE.txt` | group `unknown` | lang `text` | LOC 45 | weight 1: metadata_first: search or read narrow ranges
- `SLIM_RULE_INLINE.txt` | group `unknown` | lang `text` | LOC 1 | weight 1: metadata_first: search or read narrow ranges

## Entrypoints


## Hotspots

- `CORE_RULE.txt`: complexity 21, 42 LOC, group `unknown`
- `SLIM_RULE.txt`: complexity 12, 45 LOC, group `unknown`
- `SLIM_RULE_INLINE.txt`: complexity 9, 1 LOC, group `unknown`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
