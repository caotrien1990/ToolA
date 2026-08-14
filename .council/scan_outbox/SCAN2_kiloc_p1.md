# END_SCAN2_KILOC_R1_P1

## Scan Partition P1: code_tools core (~13.5K LOC)

Date: 2026-07-10 16:50+07 UTC
Worker: KiloC (kilo/deepseek-v4-pro) / Round 1 / Broker one-shot
Scanned: code_tools.py, test_code_tools_cache.py, _write_r7.py, _payload_gen.py, _v2bench.py, _c8_query.py, orchestrator_ui.py

## Tool Stability Table

| Tool | Time (EST ms) | OK? | Count | Notes |
|---|---|---|---|---|
| cbm_dead_code | 0 | OK | 0 | Vulture+Ruff: no candidates; scope=core filtered to code_tools.py |
| ast_query (try_bare_except) | ~- | OK | 0 | No bare excepts in code_tools.py |
| find_symbol (build_tool_schemas) | ~- | OK | 5 | Definition: registry_data.py:1419; callers: code_tools.py:113,953 |
| find_callers (build_tool_schemas) | ~- | OK | 5 | Same results as find_symbol; rich caller info |
| cbm_template_query (orphan_symbols) | 0 | OK | 134 total, 50 shown | Orphans in common.py, graph, mission, storage, tools/write, council, runtime; not in code_tools.py |
| search_code (symbol) | ~- | ERROR | N/A | WinError 5: pytest temp artifact access denied; retried with find_symbol instead |
| ast_query (function_returning_dict) | ~- | OK | 30 | 30 dict-returning functions located; normal for this codebase |

## Findings

| file:line | Severity | Type | Evidence | fix_hint |
|---|---|---|---|---|
| code_tools.py:44-61 | MEDIUM | error-handling | _shared_inplace_write_bytes catches only PermissionError. If FileNotFoundError, OSError (disk full), or other exceptions occur, the 5-iteration retry loop exits early without raising, and the function falls through to line 61 returning "shared_inplace" silently. Write never happened but caller thinks it succeeded. | Wrap non-PermissionError exceptions with raise; or use try: ... except Exception as exc: last_exc = exc so the final raise last_exc actually fires |
| code_tools.py:1617-1638 | MEDIUM | race | update_workflow_state uses read-modify-write with last-writer-wins (documented). Concurrent writers can drop fields set by others. CAN-037 acknowledges this but calls it documented intended behaviour. In practice, the post-write pipeline (validate_file -> validate_symbols -> cache_delta) is not re-checked after update_workflow_state so dropped fields are silent. | Add a check-and-merge step: if path.exists(): re-read and merge (not overwrite) untouched fields |
| code_tools.py:500-508 | MEDIUM | error-handling | _update_cache_after_write has multiple silent except Exception: pass catch-alls that swallow exceptions during cache update. If cache update fails, the write succeeds but cache is stale without notification. The mtime comparison on line 500 has a related retry loop that also swallows exceptions from stat calls. | Log at least a warning via logging.warning when cache update fails; consider returning cache_drift_warning in the response |
| code_tools.py:64-93 | LOW | bug | _atomic_write_bytes: keep_tmp is set in the PermissionError handler (line 79) but the outer except Exception (line 81) will catch and try to unlink if keep_tmp is False; then the finally also runs unlink. Double unlink attempts -- safe but wasteful, and the outer except: raise on line 87 will never fire for PermissionError because it is caught on line 78 | Restructure: single cleanup path with cleanup_tmp() helper; re-order exception handling so PermissionError flows to shared_inplace_write_bytes directly |
| code_tools.py:1190-1263 | LOW | deadcode | write_fix_html is [DEPRECATED V15 R8] and routes non-HTML extensions to write_fix. The HTML-specific delimiter check and brace balance check are useful safety guards but add duplicated write path complexity | Consider moving delimiter check into write_fix directly (when suffix == .html) and removing write_fix_html after adequate deprecation period |
| orchestrator_ui.py:53-81 | LOW | error-handling | _start_orchestrator writes config synchronously on the main TK thread. If the file write blocks (network share, disk IO), the UI freezes. No threading / async write | Wrap file write in threading.Thread or async if TK interop supported |
| _payload_gen.py:1-3 | LOW | deadcode | 3-line script reads from /tmp/write_payload.json (Linux path, dead on Windows). Trivial helper, no imports or callers | Remove or move to scripts/ directory if still needed |
| _write_r7.py:1-212 | LOW | deadcode | 212-line Round 7 cross-audit report artifact (ComC2 broker worker) checked into project root. Not a tool; no imports by code_tools.py or orchestrator_server.py | Move to .council/ or delete; clutters project root with artifacts |
| _v2bench.py:1-141 | LOW | deadcode | Native urllib.request TOOLQ http calls -- bypasses [A] HTTP transport. Likely a one-off benchmark or Round 2 scanning utility | Remove or move to scripts/; functionality appears superseded by _c8_query.py |
| _c8_query.py:1-62 | LOW | deadcode | SQLite query script against TOOLQ database; hardcoded paths; no imports from code_tools | Remove or move to scripts/; one-off analytics utility |
| code_tools.py:1289-1319 | LOW | error-handling | execute_script does not validate script_path is inside project root. Any .py file on disk can be executed. This is by design but should be auditable | Add optional project_path parameter with path confinement guardrail; log all executions to native_audit |

## Health Summary

- TOTAL LOC: ~11,662 lines (code_tools.py) + ~1,900 lines (supporting files)
- Bare excepts: 0 (clean)
- Vulture/Ruff dead code: 0 auto-detected
- Orphan symbols: 134 total across project (not in code_tools.py itself)
- build_tool_schemas callers: 2 active calls in code_tools.py + definition in registry_data.py
- Critical findings: 0
- High findings: 0
- Medium findings: 3
  1. _shared_inplace_write_bytes error catch gap (code_tools.py:44-61)
  2. update_workflow_state race (code_tools.py:1617-1638)
  3. _update_cache_after_write silent failures (code_tools.py:500-508)
- Low findings: 8

END_SCAN2_KILOC_R1_P1
