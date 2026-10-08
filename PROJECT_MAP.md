# Project Map

- Schema: 2
- Generated: 2026-10-08T13:00:04
- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Files indexed: 1025

## Groups

- `config`: 40 files, 16039 LOC
- `doc`: 342 files, 24335 LOC
- `entrypoint`: 1 files, 15750 LOC
- `script`: 11 files, 1235 LOC
- `source`: 213 files, 94497 LOC
- `test`: 388 files, 74149 LOC
- `ui`: 2 files, 67 LOC
- `unknown`: 28 files, 3756 LOC

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

## Agent Usage

- Start with `.orchestrator_cache/INDEX.json` for routing.
- Prefer `PROJECT_CONTEXT.md` for a compact route guide before opening source.
- Use function/class refs to call `read_file_range` only on the needed line span.
- Use per-file cache JSON for imports, purpose, and symbol details before opening source.
