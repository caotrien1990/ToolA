# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Generated: 2026-08-20T20:20:02
- Files indexed: 1661

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

- `config`: 123 files, 30619 LOC
- `doc`: 692 files, 92138 LOC
- `entrypoint`: 3 files, 13622 LOC
- `script`: 15 files, 1579 LOC
- `source`: 429 files, 128209 LOC
- `test`: 254 files, 38079 LOC
- `ui`: 24 files, 4632 LOC
- `unknown`: 121 files, 53844 LOC

## Highest Value Files

- `orchestrator_server.py` | group `entrypoint` | lang `python` | LOC 13525 | weight 24: large_file: use symbols, search, or bounded ranges before full read
- `code_tools.py` | group `source` | lang `python` | LOC 13188 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `surgical_mapper.py` | group `source` | lang `python` | LOC 1040 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `tools_v15.py` | group `source` | lang `python` | LOC 8094 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/write/core.py` | group `source` | lang `python` | LOC 1732 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/session/store.py` | group `source` | lang `python` | LOC 1749 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/search_snapshots.py` | group `source` | lang `python` | LOC 1272 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `.council/tmp_ui_audit/a_dashboard.html` | group `ui` | lang `html` | LOC 2922 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/tools/search.py` | group `source` | lang `python` | LOC 877 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/server/audit_writer.py` | group `source` | lang `python` | LOC 1089 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/core/ca_policy_matrix.py` | group `source` | lang `python` | LOC 794 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/mcp_packs/generator.py` | group `source` | lang `python` | LOC 1090 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/graph/query.py` | group `source` | lang `python` | LOC 791 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/lifecycle/restart_control_service.py` | group `source` | lang `python` | LOC 780 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/server/mcp_stdio.py` | group `source` | lang `python` | LOC 901 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `research/CBM_CG/CBM/pkg/npm/package.json` | group `entrypoint` | lang `json` | LOC 41 | weight 14: metadata_first: search or read narrow ranges
- `research/CBM_CG/CG/package.json` | group `entrypoint` | lang `json` | LOC 56 | weight 14: metadata_first: search or read narrow ranges
- `orchestrator/runtime/desktop_dialogs.py` | group `source` | lang `python` | LOC 524 | weight 13: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/runtime/desktop_dialog_controls.py` | group `source` | lang `python` | LOC 530 | weight 13: large_file: use symbols, search, or bounded ranges before full read
- `orchestrator/mission/tools.py` | group `source` | lang `python` | LOC 504 | weight 13: large_file: use symbols, search, or bounded ranges before full read

## Entrypoints

- `orchestrator_server.py`: HTTP/MCP server that exposes project tools.
- `research/CBM_CG/CBM/pkg/npm/package.json`: Application entrypoint.
- `research/CBM_CG/CG/package.json`: Application entrypoint.

## Hotspots

- `orchestrator_server.py`: complexity 3187, 13525 LOC, group `entrypoint`
- `code_tools.py`: complexity 2879, 13188 LOC, group `source`
- `tools_v15.py`: complexity 1735, 8094 LOC, group `source`
- `research/CBM_CG/CBM/docs/EVALUATION_PLAN.md`: complexity 751, 8802 LOC, group `doc`
- `examples/mcp_175_tool_rag.html`: complexity 569, 5 LOC, group `ui`
- `orchestrator/tools/write/core.py`: complexity 438, 1732 LOC, group `source`
- `orchestrator/session/store.py`: complexity 307, 1749 LOC, group `source`
- `orchestrator/search_snapshots.py`: complexity 302, 1272 LOC, group `source`
- `.council/tmp_ui_audit/a_dashboard.html`: complexity 279, 2922 LOC, group `ui`
- `research/CBM_CG/CBM/scripts/smoke-test.sh`: complexity 260, 1505 LOC, group `unknown`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
