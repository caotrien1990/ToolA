# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Generated: 2026-11-21T06:06:26
- Files indexed: 1062

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

- `config`: 52 files, 19649 LOC
- `doc`: 336 files, 27912 LOC
- `entrypoint`: 1 files, 16929 LOC
- `script`: 15 files, 1401 LOC
- `source`: 164 files, 101938 LOC
- `test`: 466 files, 96629 LOC
- `ui`: 2 files, 67 LOC
- `unknown`: 26 files, 3408 LOC

## Highest Value Files

- `orchestrator_server.py` | group `entrypoint` | lang `python` | LOC 16929 | weight 24: large_file: use symbols, search, or bounded ranges before full read
- `code_tools.py` | group `source` | lang `python` | LOC 19926 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `surgical_mapper.py` | group `source` | lang `python` | LOC 1689 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `.council/_r3_before/os_head.py` | group `source` | lang `python` | LOC 13917 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tools_v15.py` | group `source` | lang `python` | LOC 8686 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/write/core.py` | group `source` | lang `python` | LOC 2184 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/search.py` | group `source` | lang `python` | LOC 2055 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/session/store.py` | group `source` | lang `python` | LOC 2153 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/search_snapshots.py` | group `source` | lang `python` | LOC 1312 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/tools.py` | group `source` | lang `python` | LOC 1373 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/server/mcp_stdio.py` | group `source` | lang `python` | LOC 1626 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/t4_bridge.py` | group `source` | lang `python` | LOC 914 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/server/audit_writer.py` | group `source` | lang `python` | LOC 1486 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/core/ca_policy_matrix.py` | group `source` | lang `python` | LOC 920 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/ocr_review.py` | group `source` | lang `python` | LOC 757 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/read/context.py` | group `source` | lang `python` | LOC 794 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/lifecycle/restart_control_service.py` | group `source` | lang `python` | LOC 886 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/mcp_packs/generator.py` | group `source` | lang `python` | LOC 1100 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/indexer.py` | group `source` | lang `python` | LOC 983 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/query.py` | group `source` | lang `python` | LOC 883 | weight 15: large_file: use symbols, search, or bounded ranges before full read

## Entrypoints

- `orchestrator_server.py`: HTTP/MCP server that exposes project tools.

## Hotspots

- `orchestrator_server.py`: complexity 3998, 16929 LOC, group `entrypoint`
- `code_tools.py`: complexity 3703, 19926 LOC, group `source`
- `.council/_r3_before/os_head.py`: complexity 3253, 13917 LOC, group `source`
- `tools_v15.py`: complexity 1841, 8686 LOC, group `source`
- `examples/mcp_175_tool_rag.html`: complexity 569, 5 LOC, group `ui`
- `orchestrator/tools/write/core.py`: complexity 526, 2184 LOC, group `source`
- `orchestrator/tools/search.py`: complexity 521, 2055 LOC, group `source`
- `orchestrator/session/store.py`: complexity 422, 2153 LOC, group `source`
- `surgical_mapper.py`: complexity 400, 1689 LOC, group `source`
- `orchestrator/search_snapshots.py`: complexity 310, 1312 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
