# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\tests`
- Generated: 2026-09-23T23:29:47
- Files indexed: 362

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

- `script`: 2 files, 176 LOC
- `source`: 348 files, 63673 LOC
- `unknown`: 12 files, 138 LOC

## Highest Value Files

- `test_orchestrator_server_contract.py` | group `source` | lang `python` | LOC 3834 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `test_code_tools_cache.py` | group `source` | lang `python` | LOC 2200 | weight 13: large_file: use symbols, search, or bounded ranges before full read
- `test_a39_chain_registration_gate.py` | group `source` | lang `python` | LOC 454 | weight 11: symbol_routed: use function/class refs from INDEX first
- `test_a34_active_chain.py` | group `source` | lang `python` | LOC 585 | weight 10: large_file: use symbols, search, or bounded ranges before full read
- `test_payload_says_each_thing_once_b021.py` | group `source` | lang `python` | LOC 675 | weight 10: large_file: use symbols, search, or bounded ranges before full read
- `test_session_store_writer.py` | group `source` | lang `python` | LOC 474 | weight 9: symbol_routed: use function/class refs from INDEX first
- `test_search_regex_budget.py` | group `source` | lang `python` | LOC 565 | weight 9: large_file: use symbols, search, or bounded ranges before full read
- `test_durable_audit_writer.py` | group `source` | lang `python` | LOC 556 | weight 9: large_file: use symbols, search, or bounded ranges before full read
- `test_search_snapshots.py` | group `source` | lang `python` | LOC 510 | weight 9: large_file: use symbols, search, or bounded ranges before full read
- `test_alias_contract.py` | group `source` | lang `python` | LOC 465 | weight 9: symbol_routed: use function/class refs from INDEX first
- `test_ca_first_upgrade_contract.py` | group `source` | lang `python` | LOC 460 | weight 9: symbol_routed: use function/class refs from INDEX first
- `test_a37_bootstrap_hint.py` | group `source` | lang `python` | LOC 455 | weight 9: symbol_routed: use function/class refs from INDEX first
- `test_conn_leak_audit.py` | group `source` | lang `python` | LOC 277 | weight 8: symbol_routed: use function/class refs from INDEX first
- `test_cbm_campaign_contracts.py` | group `source` | lang `python` | LOC 334 | weight 8: symbol_routed: use function/class refs from INDEX first
- `test_graph_analysis_service.py` | group `source` | lang `python` | LOC 389 | weight 8: symbol_routed: use function/class refs from INDEX first
- `test_session_retention_a22.py` | group `source` | lang `python` | LOC 359 | weight 8: symbol_routed: use function/class refs from INDEX first
- `test_a31_skill_surface_gates.py` | group `source` | lang `python` | LOC 398 | weight 8: symbol_routed: use function/class refs from INDEX first
- `test_search_singleflight_concurrency.py` | group `source` | lang `python` | LOC 406 | weight 8: symbol_routed: use function/class refs from INDEX first
- `test_post_call_accounting.py` | group `source` | lang `python` | LOC 393 | weight 8: symbol_routed: use function/class refs from INDEX first
- `test_durable_audit_integration.py` | group `source` | lang `python` | LOC 456 | weight 8: symbol_routed: use function/class refs from INDEX first

## Entrypoints


## Hotspots

- `test_orchestrator_server_contract.py`: complexity 117, 3834 LOC, group `source`
- `test_code_tools_cache.py`: complexity 69, 2200 LOC, group `source`
- `test_a39_chain_registration_gate.py`: complexity 61, 454 LOC, group `source`
- `test_conn_leak_audit.py`: complexity 54, 277 LOC, group `source`
- `test_a34_active_chain.py`: complexity 52, 585 LOC, group `source`
- `test_session_store_writer.py`: complexity 37, 474 LOC, group `source`
- `test_cbm_campaign_contracts.py`: complexity 37, 334 LOC, group `source`
- `test_graph_analysis_service.py`: complexity 36, 389 LOC, group `source`
- `test_upgrade_p5_registration_b5.py`: complexity 33, 294 LOC, group `source`
- `test_a31_autochain_registry.py`: complexity 31, 225 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
