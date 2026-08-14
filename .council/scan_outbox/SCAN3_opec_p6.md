# SCAN3 Round 3 - Partition P6 - OpeC (ope/glm-5.2)

Worker: OpeC (ope/glm-5.2)
Partition: P6 (upgrade contract tests + council tmp + dispatch scripts + misc, ~2741 LOC, 20 files)
Project root: D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator
Scan objectives: dead code, bug patterns, error-handling gaps, race conditions.

Bootstrap: get_rule_core_text(ok, already_fetched=false) -> open_session(ok, resumed, session_id=untitled-session-07-10-00-15). Transport policy noted (script-file preferred for non-trivial payloads); direct Invoke-RestMethod used for HTTP /api/call.

P6 files inspected by read_file_range (start_line/end_line fields - initial line_start/line_end got 400 UNSUPPORTED_FIELD, corrected once):
- .council/tmp_qsleep/build/lease_orchestrator.py (1-156, full)
- .council/tmp_qsleep/build/loop_orchestrator.py (1-369, full)
- .council/tmp_qsleep/build/qsleep_core.py (1-215, full)
- .council/tmp_qsleep/build/artifact_schemas.py (1-327, full)
- .council/mcp_phase4_final.py (1-188, full)
- .council/mcp_phase4_fix.py (1-84, full)
- _dispatch_scan2_v2.py (1-134, full)
- _dispatch_scan2.py (1-93, full - confirmed total_lines=93)
- scripts/mcp_api_parity_check.py (1-70, full)
- tests/test_ca_first_upgrade_contract.py (1-200 inspected of 448; tail 200-448 not read - contract tests, low risk)
- phase1p5_build_truthset.py (1-200 inspected of 267; tail 200-267 = judge_relevance group D + main, low risk)
- tests/test_release_gate_182.py (1-56, full)
- __reeval_run.py (1-41, full)
- .council/_verify_comc_argv.py (1-34, full)
- _download_model.py (1-22, full)
- research/CBM_CG/CBM/scripts/license-gate-check-npm.py (1-109, full)
- research/CBM_CG/CBM/scripts/test_mcp_rapid_init.py (1-98, full)
- orchestrator/__init__.py (1-7, full - docstring only)
- research/CBM_CG/CBM/pkg/pypi/src/codebase_memory_mcp/__init__.py (1-17, full)
- .pytest_basetemp/run/test_python_callable_reference2/sort_key_kwarg/mod.py - GENERATED pytest temp artifact (basetemp), not hand-maintained source; excluded from findings.

## Tool Stability Table

Wall time = stopwatch around Invoke-RestMethod; tool_elapsed = backend-reported elapsed_ms where available. ~100s wall on first 3 tools indicates backend index refresh/rebuild blocking (search_code returned stale_index_live_fallback warning).

| # | Tool | Wall ms | OK? | Count | Notes |
|---|---|---|---|---|---|
| 1 | cbm_dead_code | 100446 | ok | 0 | scope=full, limit=50; engine=vulture+ruff; elapsed_ms=30984; 0 candidates project-wide (advisory; .council/tmp_qsleep likely excluded from index) |
| 2 | ast_query (try_bare_except) | 103610 | ok | 50 | capped at max_results=50; 5 in P6 project files + 45 in vendored .tmp/uv-cache-codoc (out of scope) |
| 3 | search_code (callers, loop_orchestrator) | 100725 | ok | 0 | stale_index_live_fallback warning; 0 static callers (script-style module, likely dynamic invocation - NOT confirmed dead) |
| 4 | cbm_template_query (orphan_symbols) | 46747 | ok | 50 | truncated (total_count=134); none of the visible 50 are in P6 partition files (all in orchestrator/ package internals) |
| 5 | search_code (regex Thread/Lock/asyncio) | 47573 | ok | 50 | bounded; 0 matches inside P6 files - all matches in code_tools.py + orchestrator_server.py (other partitions) |

P6-relevant bare_except hits from ast_query (#2):
- _dispatch_scan2_v2.py:117 (bare_except) - P6
- .council/mcp_phase4_final.py:45 (bare_except) - P6
- _dispatch_scan3.py:112, _round3_partition.py:30, .council/mcp_full_test_v3.py:24 - sibling dispatch/council scripts (not in P6 list but adjacent)
- remaining 45: vendored .tmp/uv-cache-codoc/* (adodbapi, protobuf, jsonschema) - OUT OF SCOPE

Race/asyncio regex (#5): no Thread/Lock/asyncio primitives inside any P6 file. P6 concurrency is file-based (lease/lock files), not threading - see race findings below.

## Findings

| file:line | severity | type | evidence | fix_hint |
|---|---|---|---|---|
| .council/tmp_qsleep/build/lease_orchestrator.py:99-100,150 + :37-39 | CRITICAL | race/correctness | recover_expired_leases appends status "pending_rollback" to processed_clusters.jsonl (line 150), but _read_processed_set adds ANY cluster_id to the seen set regardless of status (line 37-39: `if cid: seen.add(str(cid))`). Rolled-back clusters are falsely treated as DONE by the fast-skip -> never retried -> recover_expired_leases is self-defeating. | Filter _read_processed_set by status=="done" only: `if entry.get("status")=="done": seen.add(...)`. |
| .council/tmp_qsleep/build/qsleep_core.py:144-151 | CRITICAL | race/platform | check_lock uses `os.kill(pid, 0)` to test PID liveness. On Windows os.kill signal-0 semantics are undefined/quirky (may raise PermissionError for alive PIDs, or behave like TerminateProcess). except(OSError,PermissionError) at :148 treats those as "PID dead" -> removes a LIVE instance's lock -> duplicate concurrent QSleep instances. | Replace with ctypes OpenProcess+GetExitCodeProcess or psutil.pid_exists(pid); or use a file mtime staleness check. |
| .council/tmp_qsleep/build/qsleep_core.py:154-159 + :125-142 | CRITICAL | race | Lock acquisition is check-then-write (check_lock then write_lock) with no atomic create. Two instances can both pass check_lock (no lock present) then both call write_lock -> both run concurrently. No O_EXCL / `open(...,'x')` / atomic-rename. | Use `open(lock_path,'x')` (atomic exclusive create) or write to temp+os.rename; re-check after acquire. |
| .council/tmp_qsleep/build/lease_orchestrator.py:117-124 | HIGH | error-handling/resource-leak | process_callback (line 118) is NOT wrapped in try/finally. If it raises, mgr.release() (line 121) and _append_processed (line 124) are skipped -> lease held until TTL 600s expiry -> cluster stuck for 10 min. | Wrap callback in try/finally: `try: process_callback(...); finally: mgr.release(cluster_id, status="done" if not exc else "failed")`. |
| .council/tmp_qsleep/build/loop_orchestrator.py:331,347 | HIGH | error-handling | step_qdreamer1/qdreamer2 exceptions are re-raised (line 331, 347) after stop_check says don't-stop; propagates out of loop_until_clean. If called inside a leased section, the lease is never released (compounds lease_orchestrator finding). | Either catch+mark meta 'failed' and return, or guarantee caller wraps loop_until_clean in lease try/finally. |
| .council/tmp_qsleep/build/loop_orchestrator.py:67-74,109,140,232 | HIGH | error-handling/silent-fail | _run_hermes uses subprocess.run but the return value (returncode) is NEVER checked - only output-file existence is checked (lines 112,143,235). A Hermes crash/nonzero-exit that leaves a stale or partial JSON from a prior run is read as success -> corrupt candidate/audit flows downstream. | Check `res.returncode==0` (or log res.stderr) before reading output; clear stale output file before run. |
| _dispatch_scan2.py:80-93 | HIGH | deadcode/incomplete | File is INCOMPLETE: PROMPTS['hermes'] tuple opened at line 80 is never closed; file ends at line 93 (whitespace) with no dispatch logic. SyntaxError if imported. Superseded by _dispatch_scan2_v2.py. | Delete file (it is a broken early draft) or restore the closing/dispatch section from v2. |
| .council/_verify_comc_argv.py:16-32 | HIGH | bug/test-correctness | Verification relies on `assert` statements. Under `python -O` all asserts are stripped -> VERIFY_COMC_ARGV_OK (line 34) prints unconditionally -> false verification pass. | Replace asserts with explicit if/raise or sys.exit(1); never use bare assert in a verification gate. |
| .council/tmp_qsleep/build/qsleep_core.py:151 | MEDIUM | bug | `return (True, "Removed stale lock (PID {pid} dead)")` is a plain string, NOT an f-string -> `{pid}` not interpolated; also `pid` is out of scope at that return. | `return (True, f"Removed stale lock (PID {pid} dead)")` and ensure pid in scope. |
| .council/mcp_phase4_final.py:45 | MEDIUM | error-handling | bare `except:` (catches KeyboardInterrupt/SystemExit too). Confirmed by ast_query. | `except Exception:`. |
| .council/mcp_phase4_final.py:24-27 | MEDIUM | deadcode | get_token() defined but NEVER called (actual token fetch is inline at lines 38-46); returns hardcoded "auto". | Remove get_token() or wire it into the token-fetch block. |
| .council/mcp_phase4_final.py:186-188 | MEDIUM | bug/data-loss | Writes final report back to REPORT_IN = mcp_full_surface_report_final.json (the INPUT file) -> overwrites/destroys original input report. | Write to a separate output path (e.g. mcp_full_surface_report_final_phase4.json). |
| .council/mcp_phase4_fix.py:38,42,56,60 vs mcp_phase4_final.py:56,60,67,71 | MEDIUM | bug/schema | Inconsistent field names for the SAME tools between sibling scripts: folder_diff {path1,path2} vs {path_a,path_b}; read_skill {skill_name} vs {name}; workflow_activate {workflow_name} vs {name}; workflow_save_md {md_content} vs {body_md}. At least one set is schema-wrong -> UNSUPPORTED_FIELD. | Reconcile both scripts against the live tool schemas (GET /api/tools/<tool>/schema) and use the correct field names. |
| .council/mcp_phase4_fix.py:74-75 | MEDIUM | bug/test | Calls run_node_script/run_ps1_script WITHOUT a mutation token -> always gets MUTATION_TOKEN_REQUIRED -> reported FAIL. final.py correctly skips these (line 61-62). | Add token fetch or skip these two tools like final.py. |
| _dispatch_scan2_v2.py:117 | MEDIUM | error-handling | bare `except:` inside the HTTPError handler (confirmed by ast_query); `str(e)` refers to the OUTER HTTPError, losing the inner json.loads error detail. | `except Exception as ie: results[agent] = {'error': str(e) + ' inner=' + str(ie)}`. |
| _dispatch_scan2_v2.py:112,123,126-127 | MEDIUM | race/resource-leak | dispatch sends timeout_sec=600 to the worker but local urllib timeout=100s (mismatch -> orphan worker keeps running after dispatcher gives up); threads are non-daemon + join(timeout=120) -> script may hang if a thread outlives join. | Align urllib timeout with timeout_sec; set `threading.Thread(..., daemon=True)` or join without timeout+force-exit. |
| scripts/mcp_api_parity_check.py:27 | MEDIUM | error-handling/silent | `source.find("from zeromcp import McpServer")` returns -1 if marker absent -> `source[-1:]` -> regex finds nothing -> empty wrapper set -> false "all tools missing top-level wrapper" without an explicit failure. | `idx=source.find(...); assert idx!=-1, "marker not found"` before slicing. |
| __reeval_run.py:2-3,23 | MEDIUM | bug/portability | Hardcoded absolute paths to ANOTHER project (D:\...\21. ToolQ\bin / librarian.db). Breaks outside this exact machine layout. | Resolve via env var (ORCHESTRATOR_HOME / Q_HOME) or relative to a known root. |
| __reeval_run.py:7,14 | MEDIUM | resource-leak/error-handling | `json.load(open(metrics_file,'r'))` / `json.dump(cycles, open(cycles_file,'w'), indent=2)` - no `with`, no encoding -> file-handle leak + platform-default encoding. | Use `with open(..., encoding='utf-8') as fh:`. |
| __reeval_run.py:40 | MEDIUM | error-handling | `os.unlink(cycles_file)` cleanup not in try/finally -> temp _reeval_cycles.json left on any earlier exception. | Wrap body in try/finally with unlink in finally. |
| phase1p5_build_truthset.py:65-71 | MEDIUM | error-handling | load_result has no try/except; one malformed curl output file crashes the entire build. Comment at line 70 claims "\r handling" but code does NOT strip \r before json.loads. | Add try/except around json.loads; strip `\r` (`raw.replace('\r','')`) or use json.loads on stripped string. |
| .council/tmp_qsleep/build/loop_orchestrator.py:39-43 | MEDIUM | error-handling | _next_version does `int(parts[-1])` without guard; a stem without ".vNNN" -> uncaught ValueError. | Guard: `if '.v' not in stem: raise ValueError(...)`. |
| .council/tmp_qsleep/build/loop_orchestrator.py:15-17 | LOW | bug/portability | flat imports (from lease_manager / stop_conditions / artifact_schemas) rely on sys.path injection (line 12-14); fragile if imported as a package. | Acceptable for script-style run; document that it must be run as a script from the build dir. |
| .council/tmp_qsleep/build/lease_orchestrator.py:15 | LOW | bug/portability | `from lease_manager import LeaseManager` flat import; same sys.path fragility as loop_orchestrator. | Same - script-style only. |
| .council/tmp_qsleep/build/lease_orchestrator.py:54-55 | LOW | race (mitigated) | _append_processed uses `open(...,"a")` non-atomic; concurrent appenders can interleave partial lines. Mitigated by JSONDecodeError skip in _read_processed_set (line 40-41). | Use a lock file or atomic append with line buffering; acceptable as-is given mitigation. |
| .council/tmp_qsleep/build/artifact_schemas.py:91-93 | LOW | deadcode | tuple-type branch in _check_fields (`if isinstance(typ, tuple)`) never exercised - all schemas map to single `type` values. | Remove dead branch or add a tuple-typed schema field to justify it. |
| .council/tmp_qsleep/build/artifact_schemas.py:268-327 | LOW | deadcode (unverified) | make_mock_* factories labeled "for downstream testing/examples"; if no downstream consumer exists, they are dead in production. | Verify consumers via search_code; remove if unused. |
| _download_model.py:4,14-19 | LOW | error-handling | hardcoded target path + no try/except around hf_hub_download (network failure -> raw traceback). One-off script. | Add try/except + env-based target dir. |
| research/CBM_CG/CBM/scripts/license-gate-check-npm.py:65 | LOW | error-handling | `sys.argv[1], sys.argv[2]` with no len check -> IndexError if args missing. | Use argparse or check `len(sys.argv)`. |
| research/CBM_CG/CBM/scripts/license-gate-check-npm.py:66 | LOW | error-handling | `open(policy_path)` no encoding, no try/except for FileNotFoundError. | `with open(policy_path, encoding='utf-8') as fh:`. |
| research/CBM_CG/CBM/scripts/test_mcp_rapid_init.py:88 | LOW | test-robustness | `"tools" not in output` checks a raw substring, not parsed JSON -> could false-pass if "tools" appears in an error string. | Parse the id:2 response and check its `result.tools` key instead. |
| tests/test_ca_first_upgrade_contract.py:35,57,82 | LOW | test-maintainability | Tests call private internals (_session_prompt, _error_response, _ca_guidance); brittle to internal refactors. | Acceptable for a contract test; keep private API stable or expose test hooks. |
| phase1p5_build_truthset.py:5 | LOW | portability | hardcoded RAW_DIR absolute path. One-off build script. | Resolve relative to project root. |
| _dispatch_scan2_v2.py:3-5 | LOW | portability | hardcoded BASE/PROJ/OUT paths. | Use env ORCHESTRATOR_BASE_URL. |
| tests/test_release_gate_182.py | LOW | none | clean contract test; no bare-except, no resource leak. Uses utf-8-sig for BOM baseline (line 26). | No fix needed. |
| orchestrator/__init__.py | LOW | none | docstring only (no code). | No fix needed. |
| research/CBM_CG/CBM/pkg/pypi/src/codebase_memory_mcp/__init__.py | LOW | none | clean package init; handles ImportError for importlib.metadata. | No fix needed. |

## Dead-code summary

- _dispatch_scan2.py - entire file is broken/superseded (incomplete, unterminated tuple, SyntaxError). Strongest dead-code candidate in P6.
- .council/mcp_phase4_final.py:24-27 - get_token() unused function.
- .council/tmp_qsleep/build/artifact_schemas.py:91-93 - dead tuple-type branch; :268-327 mock factories possibly unused (unverified - no consumer search done).
- cbm_dead_code tool reported 0 candidates project-wide (advisory only; .council/tmp_qsleep and research/ trees may be excluded from the vulture/ruff index).
- search_code callers "loop_orchestrator" = 0 static callers - script-style module likely invoked dynamically (subprocess), NOT confirmed dead.

## Race-condition summary

P6 has NO threading/asyncio primitives (regex search #5 = 0 matches inside P6). All P6 concurrency is FILE-BASED (lease files + PID lock files):
- CRITICAL: qsleep_core check_lock+write_lock non-atomic (no O_EXCL) -> duplicate instances.
- CRITICAL: qsleep_core os.kill(pid,0) unreliable on Windows -> live lock wrongly removed.
- CRITICAL: lease_orchestrator rollback-status poisoning the processed set -> rolled-back clusters never retried (correctness race between recover and fast-skip).
- HIGH: lease_orchestrator process_callback not in try/finally -> lease leak on exception.
- LOW (mitigated): lease_orchestrator non-atomic append to processed_clusters.jsonl (partial-line reads tolerated by JSONDecodeError skip).

END_SCAN3_OPEC_R3_P6
