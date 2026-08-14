# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council`
- Generated: 2026-08-09T18:01:57
- Files indexed: 7

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

- `config`: 2 files, 217 LOC
- `unknown`: 5 files, 87 LOC

## Highest Value Files

- `council_config.json` | group `config` | lang `json` | LOC 187 | weight 3: metadata_first: search or read narrow ranges
- `worker_models.json` | group `config` | lang `json` | LOC 30 | weight 1: small_context: full read acceptable if needed
- `ID_MODELS/KILO.txt` | group `unknown` | lang `text` | LOC 20 | weight 1: metadata_first: search or read narrow ranges
- `ID_MODELS/OPENCODE.txt` | group `unknown` | lang `text` | LOC 20 | weight 1: metadata_first: search or read narrow ranges
- `ID_MODELS/PI.txt` | group `unknown` | lang `text` | LOC 20 | weight 1: metadata_first: search or read narrow ranges
- `ID_MODELS/HERMES.txt` | group `unknown` | lang `text` | LOC 16 | weight 1: metadata_first: search or read narrow ranges
- `ID_MODELS/GPT.txt` | group `unknown` | lang `text` | LOC 11 | weight 1: metadata_first: search or read narrow ranges

## Entrypoints


## Hotspots

- `council_config.json`: complexity 26, 187 LOC, group `config`
- `worker_models.json`: complexity 0, 30 LOC, group `config`
- `ID_MODELS/KILO.txt`: complexity 0, 20 LOC, group `unknown`
- `ID_MODELS/OPENCODE.txt`: complexity 0, 20 LOC, group `unknown`
- `ID_MODELS/PI.txt`: complexity 0, 20 LOC, group `unknown`
- `ID_MODELS/HERMES.txt`: complexity 0, 16 LOC, group `unknown`
- `ID_MODELS/GPT.txt`: complexity 0, 11 LOC, group `unknown`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
