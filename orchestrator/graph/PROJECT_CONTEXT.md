# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\orchestrator\graph`
- Generated: 2026-10-08T13:16:41
- Files indexed: 28

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

- `source`: 28 files, 11086 LOC

## Highest Value Files

- `tools.py` | group `source` | lang `python` | LOC 1204 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `query.py` | group `source` | lang `python` | LOC 883 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `indexer.py` | group `source` | lang `python` | LOC 826 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `rules.py` | group `source` | lang `python` | LOC 684 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `analysis_view.py` | group `source` | lang `python` | LOC 691 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `extractors/python.py` | group `source` | lang `python` | LOC 626 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `evolution.py` | group `source` | lang `python` | LOC 612 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `extractors/javascript.py` | group `source` | lang `python` | LOC 558 | weight 13: large_file: use symbols, search, or bounded ranges before full read
- `whatif.py` | group `source` | lang `python` | LOC 375 | weight 12: symbol_routed: use function/class refs from INDEX first
- `dsm.py` | group `source` | lang `python` | LOC 369 | weight 11: symbol_routed: use function/class refs from INDEX first
- `cycle_point.py` | group `source` | lang `python` | LOC 506 | weight 11: large_file: use symbols, search, or bounded ranges before full read
- `analysis_service.py` | group `source` | lang `python` | LOC 408 | weight 10: symbol_routed: use function/class refs from INDEX first
- `testgap.py` | group `source` | lang `python` | LOC 381 | weight 10: symbol_routed: use function/class refs from INDEX first
- `lsp_resolver.py` | group `source` | lang `python` | LOC 297 | weight 9: symbol_routed: use function/class refs from INDEX first
- `extractors/vulture_ruff.py` | group `source` | lang `python` | LOC 374 | weight 9: symbol_routed: use function/class refs from INDEX first
- `store.py` | group `source` | lang `python` | LOC 378 | weight 9: symbol_routed: use function/class refs from INDEX first
- `enrich_producers.py` | group `source` | lang `python` | LOC 287 | weight 8: symbol_routed: use function/class refs from INDEX first
- `stability.py` | group `source` | lang `python` | LOC 285 | weight 8: symbol_routed: use function/class refs from INDEX first
- `hooks.py` | group `source` | lang `python` | LOC 212 | weight 7: symbol_routed: use function/class refs from INDEX first
- `baseline.py` | group `source` | lang `python` | LOC 231 | weight 7: symbol_routed: use function/class refs from INDEX first

## Entrypoints


## Hotspots

- `tools.py`: complexity 275, 1204 LOC, group `source`
- `rules.py`: complexity 178, 684 LOC, group `source`
- `query.py`: complexity 158, 883 LOC, group `source`
- `analysis_view.py`: complexity 152, 691 LOC, group `source`
- `indexer.py`: complexity 142, 826 LOC, group `source`
- `extractors/javascript.py`: complexity 133, 558 LOC, group `source`
- `whatif.py`: complexity 132, 375 LOC, group `source`
- `extractors/python.py`: complexity 128, 626 LOC, group `source`
- `evolution.py`: complexity 120, 612 LOC, group `source`
- `dsm.py`: complexity 84, 369 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
