# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Generated: 2026-09-10T21:42:51
- Files indexed: 1378

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

- `config`: 56 files, 30414 LOC
- `doc`: 392 files, 43818 LOC
- `entrypoint`: 3 files, 14089 LOC
- `script`: 15 files, 1579 LOC
- `source`: 470 files, 163438 LOC
- `test`: 288 files, 49057 LOC
- `ui`: 22 files, 1708 LOC
- `unknown`: 132 files, 54285 LOC

## Highest Value Files

- `orchestrator_server.py` | group `entrypoint` | lang `python` | LOC 13992 | weight 24: large_file: use symbols, search, or bounded ranges before full read
- `code_tools.py` | group `source` | lang `python` | LOC 13846 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `surgical_mapper.py` | group `source` | lang `python` | LOC 1066 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/surgical_mapper.py` | group `source` | lang `python` | LOC 1066 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `.council/_r3_before/os_head.py` | group `source` | lang `python` | LOC 13917 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `_baseline_orchestrator_server_20260827.py` | group `source` | lang `python` | LOC 13811 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tools_v15.py` | group `source` | lang `python` | LOC 8294 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/write/core.py` | group `source` | lang `python` | LOC 1793 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__tools__write__core.py` | group `source` | lang `python` | LOC 1762 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/session/store.py` | group `source` | lang `python` | LOC 1811 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/search_snapshots.py` | group `source` | lang `python` | LOC 1270 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/search.py` | group `source` | lang `python` | LOC 956 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/tools.py` | group `source` | lang `python` | LOC 964 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/server/audit_writer.py` | group `source` | lang `python` | LOC 1089 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/core/ca_policy_matrix.py` | group `source` | lang `python` | LOC 843 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__lifecycle__restart_control_service.py` | group `source` | lang `python` | LOC 843 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/lifecycle/restart_control_service.py` | group `source` | lang `python` | LOC 843 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/mcp_packs/generator.py` | group `source` | lang `python` | LOC 1090 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/query.py` | group `source` | lang `python` | LOC 829 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/indexer.py` | group `source` | lang `python` | LOC 760 | weight 15: large_file: use symbols, search, or bounded ranges before full read

## Entrypoints

- `research/CBM_CG/CBM/pkg/npm/package.json`: Application entrypoint.
- `research/CBM_CG/CG/package.json`: Application entrypoint.
- `orchestrator_server.py`: HTTP/MCP server that exposes project tools.

## Hotspots

- `orchestrator_server.py`: complexity 3267, 13992 LOC, group `entrypoint`
- `.council/_r3_before/os_head.py`: complexity 3253, 13917 LOC, group `source`
- `_baseline_orchestrator_server_20260827.py`: complexity 3228, 13811 LOC, group `source`
- `code_tools.py`: complexity 2952, 13846 LOC, group `source`
- `tools_v15.py`: complexity 1772, 8294 LOC, group `source`
- `research/CBM_CG/CBM/docs/EVALUATION_PLAN.md`: complexity 751, 8802 LOC, group `doc`
- `examples/mcp_175_tool_rag.html`: complexity 569, 5 LOC, group `ui`
- `orchestrator/tools/write/core.py`: complexity 457, 1793 LOC, group `source`
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__tools__write__core.py`: complexity 445, 1762 LOC, group `source`
- `orchestrator/session/store.py`: complexity 318, 1811 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
