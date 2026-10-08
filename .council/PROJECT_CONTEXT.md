# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council`
- Generated: 2026-10-08T09:34:27
- Files indexed: 467

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

- `config`: 44 files, 8974 LOC
- `doc`: 322 files, 21690 LOC
- `script`: 2 files, 114 LOC
- `source`: 79 files, 21386 LOC
- `unknown`: 20 files, 3367 LOC

## Highest Value Files

- `tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/surgical_mapper.py` | group `source` | lang `python` | LOC 1066 | weight 20: large_file: use symbols, search, or bounded ranges before full read
- `_r3_before/os_head.py` | group `source` | lang `python` | LOC 13917 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__tools__write__core.py` | group `source` | lang `python` | LOC 1762 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__lifecycle__restart_control_service.py` | group `source` | lang `python` | LOC 843 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `mcp_full_surface_test.py` | group `source` | lang `python` | LOC 380 | weight 12: symbol_routed: use function/class refs from INDEX first
- `Live_council/archives/extracted_130325/council_live.txt` | group `unknown` | lang `text` | LOC 373 | weight 8: metadata_first: search or read narrow ranges
- `mcp_full_test_v3.py` | group `source` | lang `python` | LOC 253 | weight 8: symbol_routed: use function/class refs from INDEX first
- `tmp_ahealthhung/REALTEST_R1/ROUTE_RULE_REALTEST.md` | group `doc` | lang `markdown` | LOC 201 | weight 7: metadata_first: search or read narrow ranges
- `rule_guardrail_solar_rag/DATA/A_RULE_TOOL_SYMBOL_HITS.txt` | group `unknown` | lang `text` | LOC 500 | weight 7: metadata_first: search or read narrow ranges
- `mcp_phase2_fix.py` | group `source` | lang `python` | LOC 237 | weight 7: symbol_routed: use function/class refs from INDEX first
- `mcp_phase3_fix.py` | group `source` | lang `python` | LOC 221 | weight 7: symbol_routed: use function/class refs from INDEX first
- `tmp_codo9_fix_3008/apply_tq9b.js` | group `source` | lang `javascript` | LOC 214 | weight 7: symbol_routed: use function/class refs from INDEX first
- `mcp_phase4_final.py` | group `source` | lang `python` | LOC 153 | weight 7: symbol_routed: use function/class refs from INDEX first
- `tmp_clr2_writes.js` | group `source` | lang `javascript` | LOC 399 | weight 7: symbol_routed: use function/class refs from INDEX first
- `tools_manifest.md` | group `doc` | lang `markdown` | LOC 261 | weight 6: metadata_first: search or read narrow ranges
- `scan_outbox/FULL_REPORT_2_ROUNDS.md` | group `doc` | lang `markdown` | LOC 547 | weight 6: large_file: use symbols, search, or bounded ranges before full read
- `Live_council/council_live.txt` | group `unknown` | lang `text` | LOC 604 | weight 6: large_file: use symbols, search, or bounded ranges before full read
- `tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/tests__test_autoheal_write_index_r1.py` | group `source` | lang `python` | LOC 156 | weight 6: symbol_routed: use function/class refs from INDEX first
- `tmp_codo9_fix_3008/write_tq9b_tests.js` | group `source` | lang `javascript` | LOC 224 | weight 6: symbol_routed: use function/class refs from INDEX first
- `route_scorecard.json` | group `config` | lang `json` | LOC 3306 | weight 6: large_file: use symbols, search, or bounded ranges before full read

## Entrypoints


## Hotspots

- `_r3_before/os_head.py`: complexity 3253, 13917 LOC, group `source`
- `tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__tools__write__core.py`: complexity 445, 1762 LOC, group `source`
- `tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/surgical_mapper.py`: complexity 243, 1066 LOC, group `source`
- `tmp_ahealthhung/REALTEST_R1/ROUTE_RULE_REALTEST.md`: complexity 200, 201 LOC, group `doc`
- `tmp_ahealthhung/GROC_FIX_SNAPSHOT_1315/orchestrator__lifecycle__restart_control_service.py`: complexity 168, 843 LOC, group `source`
- `Live_council/archives/extracted_130325/council_live.txt`: complexity 157, 373 LOC, group `unknown`
- `mcp_full_surface_test.py`: complexity 129, 380 LOC, group `source`
- `tools_manifest.md`: complexity 92, 261 LOC, group `doc`
- `rule_guardrail_solar_rag/DATA/A_RULE_TOOL_SYMBOL_HITS.txt`: complexity 63, 500 LOC, group `unknown`
- `scan_outbox/CLUSTER_W1_C6_log.json`: complexity 46, 228 LOC, group `config`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
