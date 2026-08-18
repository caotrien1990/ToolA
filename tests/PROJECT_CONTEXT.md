# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\tests`
- Generated: 2026-08-18T12:02:54
- Files indexed: 148

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

- `config`: 6 files, 231 LOC
- `doc`: 14 files, 163 LOC
- `script`: 2 files, 176 LOC
- `source`: 111 files, 21869 LOC
- `unknown`: 15 files, 143 LOC

## Highest Value Files

- `test_orchestrator_server_contract.py` | group `source` | lang `python` | LOC 3631 | weight 15: large_file: use symbols, search, or bounded ranges before full read
- `test_code_tools_cache.py` | group `source` | lang `python` | LOC 2104 | weight 13: large_file: use symbols, search, or bounded ranges before full read
- `test_search_regex_budget.py` | group `source` | lang `python` | LOC 565 | weight 9: large_file: use symbols, search, or bounded ranges before full read
- `test_durable_audit_writer.py` | group `source` | lang `python` | LOC 556 | weight 9: large_file: use symbols, search, or bounded ranges before full read
- `test_search_snapshots.py` | group `source` | lang `python` | LOC 510 | weight 9: large_file: use symbols, search, or bounded ranges before full read
- `test_ca_first_upgrade_contract.py` | group `source` | lang `python` | LOC 460 | weight 9: symbol_routed: use function/class refs from INDEX first
- `test_conn_leak_audit.py` | group `source` | lang `python` | LOC 266 | weight 8: symbol_routed: use function/class refs from INDEX first
- `test_search_singleflight_concurrency.py` | group `source` | lang `python` | LOC 406 | weight 8: symbol_routed: use function/class refs from INDEX first
- `test_durable_audit_integration.py` | group `source` | lang `python` | LOC 456 | weight 8: symbol_routed: use function/class refs from INDEX first
- `test_schema_params_contract.py` | group `source` | lang `python` | LOC 151 | weight 7: symbol_routed: use function/class refs from INDEX first
- `test_post_call_accounting.py` | group `source` | lang `python` | LOC 278 | weight 7: symbol_routed: use function/class refs from INDEX first
- `test_agent_enablement.py` | group `source` | lang `python` | LOC 322 | weight 7: symbol_routed: use function/class refs from INDEX first
- `test_council_snapshot_handlers.py` | group `source` | lang `python` | LOC 411 | weight 7: symbol_routed: use function/class refs from INDEX first
- `test_session_store.py` | group `source` | lang `python` | LOC 400 | weight 7: symbol_routed: use function/class refs from INDEX first
- `test_restart_infra_fixes.py` | group `source` | lang `python` | LOC 308 | weight 7: symbol_routed: use function/class refs from INDEX first
- `test_council_status_b4.py` | group `source` | lang `python` | LOC 416 | weight 7: symbol_routed: use function/class refs from INDEX first
- `test_task_epoch_guard.py` | group `source` | lang `python` | LOC 405 | weight 7: symbol_routed: use function/class refs from INDEX first
- `test_webfetch_tls.py` | group `source` | lang `python` | LOC 300 | weight 7: symbol_routed: use function/class refs from INDEX first
- `test_restart_control_core.py` | group `source` | lang `python` | LOC 359 | weight 7: symbol_routed: use function/class refs from INDEX first
- `test_churn2707_tooling_hardening.py` | group `source` | lang `python` | LOC 307 | weight 7: symbol_routed: use function/class refs from INDEX first

## Entrypoints


## Hotspots

- `test_orchestrator_server_contract.py`: complexity 114, 3631 LOC, group `source`
- `test_code_tools_cache.py`: complexity 61, 2104 LOC, group `source`
- `test_conn_leak_audit.py`: complexity 54, 266 LOC, group `source`
- `test_schema_params_contract.py`: complexity 30, 151 LOC, group `source`
- `test_search_regex_budget.py`: complexity 27, 565 LOC, group `source`
- `test_search_singleflight_concurrency.py`: complexity 24, 406 LOC, group `source`
- `test_durable_audit_writer.py`: complexity 23, 556 LOC, group `source`
- `test_registry_parity.py`: complexity 23, 97 LOC, group `source`
- `test_search_snapshots.py`: complexity 20, 510 LOC, group `source`
- `test_ca_first_upgrade_contract.py`: complexity 20, 460 LOC, group `source`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
