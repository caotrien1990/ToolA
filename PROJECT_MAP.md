# Project Map

- Schema: 2
- Generated: 2026-08-21T22:54:27
- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Files indexed: 1260

## Groups

- `config`: 105 files, 30459 LOC
- `doc`: 282 files, 35968 LOC
- `entrypoint`: 3 files, 13910 LOC
- `script`: 15 files, 1579 LOC
- `source`: 422 files, 128315 LOC
- `test`: 293 files, 45509 LOC
- `ui`: 23 files, 1710 LOC
- `unknown`: 117 files, 53919 LOC

## Entrypoints

- `orchestrator_server.py`: HTTP/MCP server that exposes project tools.
- `research/CBM_CG/CBM/pkg/npm/package.json`: Application entrypoint.
- `research/CBM_CG/CG/package.json`: Application entrypoint.

## Hotspots

- `orchestrator_server.py`: complexity 3227, 13813 LOC, group `entrypoint`
- `code_tools.py`: complexity 2927, 13622 LOC, group `source`
- `tools_v15.py`: complexity 1735, 8103 LOC, group `source`
- `research/CBM_CG/CBM/docs/EVALUATION_PLAN.md`: complexity 751, 8802 LOC, group `doc`
- `examples/mcp_175_tool_rag.html`: complexity 569, 5 LOC, group `ui`
- `orchestrator/tools/write/core.py`: complexity 438, 1748 LOC, group `source`
- `orchestrator/session/store.py`: complexity 307, 1749 LOC, group `source`
- `orchestrator/search_snapshots.py`: complexity 299, 1270 LOC, group `source`
- `research/CBM_CG/CBM/scripts/smoke-test.sh`: complexity 260, 1505 LOC, group `unknown`
- `orchestrator/tools/search.py`: complexity 247, 925 LOC, group `source`

## Agent Usage

- Start with `.orchestrator_cache/INDEX.json` for routing.
- Prefer `PROJECT_CONTEXT.md` for a compact route guide before opening source.
- Use function/class refs to call `read_file_range` only on the needed line span.
- Use per-file cache JSON for imports, purpose, and symbol details before opening source.
