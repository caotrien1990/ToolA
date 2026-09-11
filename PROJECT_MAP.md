# Project Map

- Schema: 2
- Generated: 2026-09-10T21:42:51
- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Files indexed: 1378

## Groups

- `config`: 56 files, 30414 LOC
- `doc`: 392 files, 43818 LOC
- `entrypoint`: 3 files, 14089 LOC
- `script`: 15 files, 1579 LOC
- `source`: 470 files, 163438 LOC
- `test`: 288 files, 49057 LOC
- `ui`: 22 files, 1708 LOC
- `unknown`: 132 files, 54285 LOC

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

## Agent Usage

- Start with `.orchestrator_cache/INDEX.json` for routing.
- Prefer `PROJECT_CONTEXT.md` for a compact route guide before opening source.
- Use function/class refs to call `read_file_range` only on the needed line span.
- Use per-file cache JSON for imports, purpose, and symbol details before opening source.
