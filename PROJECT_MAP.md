# Project Map

- Schema: 2
- Generated: 2026-11-21T06:06:26
- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Files indexed: 1062

## Groups

- `config`: 52 files, 19649 LOC
- `doc`: 336 files, 27912 LOC
- `entrypoint`: 1 files, 16929 LOC
- `script`: 15 files, 1401 LOC
- `source`: 164 files, 101938 LOC
- `test`: 466 files, 96629 LOC
- `ui`: 2 files, 67 LOC
- `unknown`: 26 files, 3408 LOC

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

## Agent Usage

- Start with `.orchestrator_cache/INDEX.json` for routing.
- Prefer `PROJECT_CONTEXT.md` for a compact route guide before opening source.
- Use function/class refs to call `read_file_range` only on the needed line span.
- Use per-file cache JSON for imports, purpose, and symbol details before opening source.
