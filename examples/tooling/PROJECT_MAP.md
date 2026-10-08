# Project Map

- Schema: 2
- Generated: 2026-10-08T11:36:53
- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. AGENT TOOL - READ WRITE TEST - minimax - USE\project-orchestrator\examples\tooling`
- Files indexed: 2

## Groups

- `doc`: 2 files, 13 LOC

## Entrypoints


## Hotspots

- `repo_vs_runtime_routing.md`: complexity 6, 6 LOC, group `doc`
- `tool_tiers.md`: complexity 2, 7 LOC, group `doc`

## Agent Usage

- Start with `.orchestrator_cache/INDEX.json` for routing.
- Prefer `PROJECT_CONTEXT.md` for a compact route guide before opening source.
- Use function/class refs to call `read_file_range` only on the needed line span.
- Use per-file cache JSON for imports, purpose, and symbol details before opening source.
