# Project Map

- Schema: 2
- Generated: 2026-10-14T13:48:44
- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
- Files indexed: 931

## Groups

- `config`: 37 files, 9763 LOC
- `doc`: 320 files, 26798 LOC
- `entrypoint`: 1 files, 15921 LOC
- `script`: 11 files, 1235 LOC
- `source`: 146 files, 88537 LOC
- `test`: 389 files, 74267 LOC
- `ui`: 2 files, 67 LOC
- `unknown`: 25 files, 3550 LOC

## Entrypoints

- `orchestrator_server.py`: HTTP/MCP server that exposes project tools.

## Hotspots

- `orchestrator_server.py`: complexity 3774, 15921 LOC, group `entrypoint`
- `code_tools.py`: complexity 3585, 16619 LOC, group `source`
- `.council/_r3_before/os_head.py`: complexity 3253, 13917 LOC, group `source`
- `tools_v15.py`: complexity 1810, 8552 LOC, group `source`
- `examples/mcp_175_tool_rag.html`: complexity 569, 5 LOC, group `ui`
- `orchestrator/tools/write/core.py`: complexity 526, 2191 LOC, group `source`
- `orchestrator/tools/search.py`: complexity 521, 2055 LOC, group `source`
- `surgical_mapper.py`: complexity 399, 1687 LOC, group `source`
- `orchestrator/session/store.py`: complexity 341, 1942 LOC, group `source`
- `orchestrator/search_snapshots.py`: complexity 310, 1312 LOC, group `source`

## Agent Usage

- Start with `.orchestrator_cache/INDEX.json` for routing.
- Prefer `PROJECT_CONTEXT.md` for a compact route guide before opening source.
- Use function/class refs to call `read_file_range` only on the needed line span.
- Use per-file cache JSON for imports, purpose, and symbol details before opening source.
