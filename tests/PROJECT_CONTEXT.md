# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\tests`
- Generated: 2026-11-21T06:06:26
- Files indexed: 537

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

- `config`: 81 files, 14182 LOC
- `doc`: 2 files, 41 LOC
- `script`: 2 files, 176 LOC
- `source`: 447 files, 89976 LOC
- `unknown`: 5 files, 20 LOC

## Highest Value Files

- `test_no_window_spawn_census.py` | group `source` | lang `python` | LOC 915 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `test_orchestrator_server_contract.py` | group `source` | lang `python` | LOC 4197 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `test_code_tools_cache.py` | group `source` | lang `python` | LOC 2553 | weight 14: large_file: use symbols, search, or bounded ranges before full read
- `test_manage_agent_rule_sync.py` | group `source` | lang `python` | LOC 897 | weight 13: large_file: use symbols, search, or bounded ranges before full read
- `test_a39_chain_registration_gate.py` | group `source` | lang `python` | LOC 454 | weight 11: symbol_routed: use function/class refs from INDEX first
- `test_dispatch_identity_field_type_gate.py` | group `source` | lang `python` | LOC 815 | weight 11: large_file: use symbols, search, or bounded ranges before full read
- `test_a45_returns_shape.py` | group `source` | lang `python` | LOC 942 | weight 11: large_file: use symbols, search, or bounded ranges before full read
- `test_search_regex_budget.py` | group `source` | lang `python` | LOC 768 | weight 11: large_file: use symbols, search, or bounded ranges before full read
- `test_listener_supervisor_fi.py` | group `source` | lang `python` | LOC 556 | weight 10: large_file: use symbols, search, or bounded ranges before full read
- `test_a34_active_chain.py` | group `source` | lang `python` | LOC 585 | weight 10: large_file: use symbols, search, or bounded ranges before full read
- `test_git_pathspec_contract.py` | group `source` | lang `python` | LOC 612 | weight 10: large_file: use symbols, search, or bounded ranges before full read
- `test_durable_audit_writer.py` | group `source` | lang `python` | LOC 651 | weight 10: large_file: use symbols, search, or bounded ranges before full read
- `test_t4_dialog_cdp.py` | group `source` | lang `python` | LOC 711 | weight 10: large_file: use symbols, search, or bounded ranges before full read
- `test_payload_says_each_thing_once_b021.py` | group `source` | lang `python` | LOC 676 | weight 10: large_file: use symbols, search, or bounded ranges before full read
- `test_write_ledger_finalization.py` | group `source` | lang `python` | LOC 612 | weight 10: large_file: use symbols, search, or bounded ranges before full read
- `test_session_store_writer.py` | group `source` | lang `python` | LOC 474 | weight 9: symbol_routed: use function/class refs from INDEX first
- `test_a31_skill_surface_gates.py` | group `source` | lang `python` | LOC 471 | weight 9: symbol_routed: use function/class refs from INDEX first
- `test_a37_bootstrap_hint.py` | group `source` | lang `python` | LOC 558 | weight 9: large_file: use symbols, search, or bounded ranges before full read
- `test_search_snapshots.py` | group `source` | lang `python` | LOC 528 | weight 9: large_file: use symbols, search, or bounded ranges before full read
- `test_ca_first_upgrade_contract.py` | group `source` | lang `python` | LOC 466 | weight 9: symbol_routed: use function/class refs from INDEX first

## Entrypoints


## Hotspots

- `test_no_window_spawn_census.py`: complexity 247, 915 LOC, group `source`
- `test_orchestrator_server_contract.py`: complexity 122, 4197 LOC, group `source`
- `test_code_tools_cache.py`: complexity 96, 2553 LOC, group `source`
- `fixtures/returns_shape/get_rule_core_text__deepest.json`: complexity 94, 195 LOC, group `config`
- `fixtures/returns_shape/get_rule_core_text__largest_bytes.json`: complexity 94, 195 LOC, group `config`
- `fixtures/returns_shape/get_rule_core_text__second_deepest.json`: complexity 94, 195 LOC, group `config`
- `test_manage_agent_rule_sync.py`: complexity 61, 897 LOC, group `source`
- `test_a39_chain_registration_gate.py`: complexity 61, 454 LOC, group `source`
- `test_listener_supervisor_fi.py`: complexity 59, 556 LOC, group `source`
- `test_conn_leak_audit.py`: complexity 54, 284 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
