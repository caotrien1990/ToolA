# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Generated: 2026-10-08T13:00:04
- Files indexed: 1025

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

- `config`: 40 files, 16039 LOC
- `doc`: 342 files, 24335 LOC
- `entrypoint`: 1 files, 15750 LOC
- `script`: 11 files, 1235 LOC
- `source`: 213 files, 94497 LOC
- `test`: 388 files, 74149 LOC
- `ui`: 2 files, 67 LOC
- `unknown`: 28 files, 3756 LOC

## Highest Value Files

- `orchestrator_server.py` | group `entrypoint` | lang `python` | LOC 15750 | weight 24: large_file: use symbols, search, or bounded ranges before full read
- `code_tools.py` | group `source` | lang `python` | LOC 16595 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `surgical_mapper.py` | group `source` | lang `python` | LOC 1563 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/surgical_mapper.py` | group `source` | lang `python` | LOC 1066 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `.council/_r3_before/os_head.py` | group `source` | lang `python` | LOC 13917 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tools_v15.py` | group `source` | lang `python` | LOC 8550 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/write/core.py` | group `source` | lang `python` | LOC 2189 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/search.py` | group `source` | lang `python` | LOC 2055 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__tools__write__core.py` | group `source` | lang `python` | LOC 1762 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/session/store.py` | group `source` | lang `python` | LOC 1942 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/search_snapshots.py` | group `source` | lang `python` | LOC 1312 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/tools.py` | group `source` | lang `python` | LOC 1204 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/server/mcp_stdio.py` | group `source` | lang `python` | LOC 1603 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/server/audit_writer.py` | group `source` | lang `python` | LOC 1472 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/core/ca_policy_matrix.py` | group `source` | lang `python` | LOC 900 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/ocr_review.py` | group `source` | lang `python` | LOC 757 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__lifecycle__restart_control_service.py` | group `source` | lang `python` | LOC 843 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/lifecycle/restart_control_service.py` | group `source` | lang `python` | LOC 843 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/mcp_packs/generator.py` | group `source` | lang `python` | LOC 1100 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/query.py` | group `source` | lang `python` | LOC 883 | weight 15: large_file: use symbols, search, or bounded ranges before full read

## Entrypoints

- `orchestrator_server.py`: HTTP/MCP server that exposes project tools.

## Hotspots

- `orchestrator_server.py`: complexity 3744, 15750 LOC, group `entrypoint`
- `code_tools.py`: complexity 3581, 16595 LOC, group `source`
- `.council/_r3_before/os_head.py`: complexity 3253, 13917 LOC, group `source`
- `tools_v15.py`: complexity 1810, 8550 LOC, group `source`
- `examples/mcp_175_tool_rag.html`: complexity 569, 5 LOC, group `ui`
- `orchestrator/tools/write/core.py`: complexity 525, 2189 LOC, group `source`
- `orchestrator/tools/search.py`: complexity 521, 2055 LOC, group `source`
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__tools__write__core.py`: complexity 445, 1762 LOC, group `source`
- `surgical_mapper.py`: complexity 364, 1563 LOC, group `source`
- `orchestrator/session/store.py`: complexity 341, 1942 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
