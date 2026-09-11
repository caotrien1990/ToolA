# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council`
- Generated: 2026-08-31T08:06:05
- Files indexed: 5

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

- `config`: 2 files, 44 LOC
- `doc`: 3 files, 320 LOC

## Highest Value Files

- `worker_output/20260828_190715_clau_e1e9906d.md` | group `doc` | lang `markdown` | LOC 208 | weight 2: metadata_first: search or read narrow ranges
- `worker_output/20260828_215622_clau_4cae89d3.md` | group `doc` | lang `markdown` | LOC 30 | weight 1: small_context: full read acceptable if needed
- `rooms/room-20260830-toolq-ask-hold/ROOM.md` | group `doc` | lang `markdown` | LOC 82 | weight 1: small_context: full read acceptable if needed
- `rooms/room-20260830-toolq-ask-hold/status.json` | group `config` | lang `json` | LOC 27 | weight 1: small_context: full read acceptable if needed
- `rooms/room-20260830-toolq-ask-hold/room_meta.json` | group `config` | lang `json` | LOC 17 | weight 1: small_context: full read acceptable if needed

## Entrypoints


## Hotspots

- `worker_output/20260828_190715_clau_e1e9906d.md`: complexity 14, 208 LOC, group `doc`
- `worker_output/20260828_215622_clau_4cae89d3.md`: complexity 2, 30 LOC, group `doc`
- `rooms/room-20260830-toolq-ask-hold/ROOM.md`: complexity 0, 82 LOC, group `doc`
- `rooms/room-20260830-toolq-ask-hold/status.json`: complexity 0, 27 LOC, group `config`
- `rooms/room-20260830-toolq-ask-hold/room_meta.json`: complexity 0, 17 LOC, group `config`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
