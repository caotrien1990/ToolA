# Project Map

- Schema: 2
- Generated: 2026-09-22T08:48:06
- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Files indexed: 991

## Groups

- `config`: 41 files, 16119 LOC
- `doc`: 345 files, 26686 LOC
- `entrypoint`: 1 files, 15279 LOC
- `script`: 10 files, 1089 LOC
- `source`: 209 files, 89046 LOC
- `test`: 354 files, 62395 LOC
- `ui`: 3 files, 87 LOC
- `unknown`: 28 files, 3663 LOC

## Entrypoints

- `orchestrator_server.py`: HTTP/MCP server that exposes project tools.

## Hotspots

- `orchestrator_server.py`: complexity 3639, 15279 LOC, group `entrypoint`
- `.council/_r3_before/os_head.py`: complexity 3253, 13917 LOC, group `source`
- `code_tools.py`: complexity 3124, 15015 LOC, group `source`
- `tools_v15.py`: complexity 1793, 8452 LOC, group `source`
- `examples/mcp_175_tool_rag.html`: complexity 569, 5 LOC, group `ui`
- `orchestrator/tools/write/core.py`: complexity 492, 1995 LOC, group `source`
- `.council/tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__tools__write__core.py`: complexity 445, 1762 LOC, group `source`
- `orchestrator/session/store.py`: complexity 337, 1908 LOC, group `source`
- `orchestrator/tools/search.py`: complexity 330, 1449 LOC, group `source`
- `orchestrator/search_snapshots.py`: complexity 309, 1304 LOC, group `source`

## Agent Usage

- Start with `.orchestrator_cache/INDEX.json` for routing.
- Prefer `PROJECT_CONTEXT.md` for a compact route guide before opening source.
- Use function/class refs to call `read_file_range` only on the needed line span.
- Use per-file cache JSON for imports, purpose, and symbol details before opening source.
