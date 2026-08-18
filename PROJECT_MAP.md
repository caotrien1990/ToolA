# Project Map

- Schema: 2
- Generated: 2026-08-18T13:02:54
- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Files indexed: 1800

## Groups

- `config`: 196 files, 60837 LOC
- `doc`: 737 files, 100069 LOC
- `entrypoint`: 3 files, 13608 LOC
- `script`: 15 files, 1579 LOC
- `source`: 440 files, 128590 LOC
- `test`: 251 files, 37787 LOC
- `ui`: 24 files, 4632 LOC
- `unknown`: 134 files, 54360 LOC

## Entrypoints

- `research/CBM_CG/CBM/pkg/npm/package.json`: Application entrypoint.
- `research/CBM_CG/CG/package.json`: Application entrypoint.
- `orchestrator_server.py`: HTTP/MCP server that exposes project tools.

## Hotspots

- `orchestrator_server.py`: complexity 3185, 13511 LOC, group `entrypoint`
- `code_tools.py`: complexity 2834, 13027 LOC, group `source`
- `tools_v15.py`: complexity 1735, 8094 LOC, group `source`
- `research/CBM_CG/CBM/docs/EVALUATION_PLAN.md`: complexity 751, 8802 LOC, group `doc`
- `examples/mcp_175_tool_rag.html`: complexity 569, 5 LOC, group `ui`
- `orchestrator/tools/write/core.py`: complexity 432, 1703 LOC, group `source`
- `orchestrator/session/store.py`: complexity 302, 1704 LOC, group `source`
- `orchestrator/search_snapshots.py`: complexity 302, 1272 LOC, group `source`
- `.council/tmp_ui_audit/a_dashboard.html`: complexity 279, 2922 LOC, group `ui`
- `research/CBM_CG/CBM/scripts/smoke-test.sh`: complexity 260, 1505 LOC, group `unknown`

## Agent Usage

- Start with `.orchestrator_cache/INDEX.json` for routing.
- Prefer `PROJECT_CONTEXT.md` for a compact route guide before opening source.
- Use function/class refs to call `read_file_range` only on the needed line span.
- Use per-file cache JSON for imports, purpose, and symbol details before opening source.
