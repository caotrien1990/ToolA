# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Generated: 2026-09-22T02:00:58
- Files indexed: 991

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

- `config`: 41 files, 16119 LOC
- `doc`: 345 files, 26563 LOC
- `entrypoint`: 1 files, 15276 LOC
- `script`: 10 files, 1089 LOC
- `source`: 209 files, 88995 LOC
- `test`: 354 files, 62346 LOC
- `ui`: 3 files, 87 LOC
- `unknown`: 28 files, 3663 LOC

## Highest Value Files

- `orchestrator_server.py` | group `entrypoint` | lang `python` | LOC 15276 | weight 24: large_file: use symbols, search, or bounded ranges before full read
- `code_tools.py` | group `source` | lang `python` | LOC 15015 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `surgical_mapper.py` | group `source` | lang `python` | LOC 1159 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/surgical_mapper.py` | group `source` | lang `python` | LOC 1066 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `.council/_r3_before/os_head.py` | group `source` | lang `python` | LOC 13917 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tools_v15.py` | group `source` | lang `python` | LOC 8452 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/write/core.py` | group `source` | lang `python` | LOC 1995 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__tools__write__core.py` | group `source` | lang `python` | LOC 1762 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/session/store.py` | group `source` | lang `python` | LOC 1908 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/search.py` | group `source` | lang `python` | LOC 1449 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/search_snapshots.py` | group `source` | lang `python` | LOC 1304 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/tools.py` | group `source` | lang `python` | LOC 1149 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/server/mcp_stdio.py` | group `source` | lang `python` | LOC 1419 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/ocr_review.py` | group `source` | lang `python` | LOC 753 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/server/audit_writer.py` | group `source` | lang `python` | LOC 1089 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/core/ca_policy_matrix.py` | group `source` | lang `python` | LOC 843 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__lifecycle__restart_control_service.py` | group `source` | lang `python` | LOC 843 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/lifecycle/restart_control_service.py` | group `source` | lang `python` | LOC 842 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/mcp_packs/generator.py` | group `source` | lang `python` | LOC 1090 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/query.py` | group `source` | lang `python` | LOC 851 | weight 15: large_file: use symbols, search, or bounded ranges before full read

## Entrypoints

- `orchestrator_server.py`: HTTP/MCP server that exposes project tools.

## Hotspots

- `orchestrator_server.py`: complexity 3639, 15276 LOC, group `entrypoint`
- `.council/_r3_before/os_head.py`: complexity 3253, 13917 LOC, group `source`
- `code_tools.py`: complexity 3124, 15015 LOC, group `source`
- `tools_v15.py`: complexity 1793, 8452 LOC, group `source`
- `examples/mcp_175_tool_rag.html`: complexity 569, 5 LOC, group `ui`
- `orchestrator/tools/write/core.py`: complexity 492, 1995 LOC, group `source`
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__tools__write__core.py`: complexity 445, 1762 LOC, group `source`
- `orchestrator/session/store.py`: complexity 337, 1908 LOC, group `source`
- `orchestrator/tools/search.py`: complexity 330, 1449 LOC, group `source`
- `orchestrator/search_snapshots.py`: complexity 309, 1304 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
