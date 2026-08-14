# CLUSTER SCAN — W1_C4 (KILO / kilo/ltnai/glm-5.2)

Worker: KILO (scan worker, TOOL-DRIVEN CLUSTER SCAN)
Project root: D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator
Cluster scope: orchestrator_server.py L4500-9441 + orchestrator/server/mcp_stdio.py + bootstrap_core_rule.py
Bootstrap: get_rule_core_text (agent_id=kilo) + open_session (project_path=PROJ) — both ok.

## Tool Call Log (MANDATORY — all 11+ tools)
| # | Tool | Params summary | Result count | Elapsed ms | Status |
|---|------|---------------|-------------|------------|--------|
| 0a | get_rule_core_text | agent_id=kilo | 1 (cached) | ~600 | ok |
| 0b | open_session | project_path=PROJ | 1 session | ~700 | ok |
| 1 | cbm_get_architecture | project_path=PROJ | 20 entry_points; files_seen=1; symbols_seen=1202 (truncated) | 982 | ok |
| 2 | import_graph | project_path=PROJ | edge_count=2941; node_count=500 (truncated; polluted by .tmp cache) | 205 | ok |
| 3 | get_file_symbols | file=orchestrator_server.py | 291 symbols | 1 char-truncated | ok |
| 4 | get_file_symbols | file=orchestrator/server/mcp_stdio.py | 57 symbols | 1231 | ok |
| 5 | get_file_symbols | file=bootstrap_core_rule.py | 0 symbols (ANOMALY) | 1036 | ok |
| 6 | cbm_dead_code | scope=full, limit=50 | 0 items | 46863 | ok |
| 7 | ast_query | pattern=try_bare_except, *.py, max=50 | 50 matches; 0 in cluster files | 16942 | ok |
| 8 | ast_query | pattern=class_inheriting, *.py, max=50 | 50 matches; 1 in cluster (orchestrator_server.py:108 _DashboardAccessLogger) | 3835 | ok |
| 9 | ast_query | pattern=function_returning_dict, *.py, max=50 | 50 matches; 10 in orchestrator_server.py (all L1329-3314, below cluster) | 1362 | ok |
| 10 | ast_query | pattern=async_function, *.py, max=50 | 50 matches; 0 in cluster files (TRUNCATION ARTIFACT — see Finding 5) | 1347 | ok |
| 11 | cbm_template_query | template=orphan_symbols, limit=50 | 50 results (total 1313); 0 in cluster files | 1471 | ok |
| 12 | search_code | mode=callers, query=_council_kill_process_tree | 2 (def + reaper.py:145 import) | 852 | ok (stale_index_live_fallback) |
| 13 | search_code | mode=callers, query=_council_cleanup_broker_session | 1 (def only; FALSE-NEGATIVE — see Finding 6) | 1397 | ok (stale_index_live_fallback) |
| 14 | search_code | mode=callers, query=restart_orchestrator_via_bat | 2 (def orchestrator_server.py:6865 + def tools_v15.py:6215) | 553 | ok (stale_index_live_fallback) |
| 15 | search_code | mode=callers, query=_dispatch_tool_call_request | 1 (def only) | 503 | ok (stale_index_live_fallback) |
| 16 | search_code | mode=callers, query=_council_run_agent | 1 (def only) | 621 | ok (stale_index_live_fallback) |
| 17 | search_code | mode=callers, query=_toolq_post | 1 (def only) | 600 | ok (stale_index_live_fallback) |
| 18 | find_symbol | name=restart_orchestrator_via_bat, project_path, include=def | 0 (400 BadRequest) | 825 | FAIL 400 |
| 19 | find_symbol | name=_council_run_agent, project_path, include=def | 0 (400 BadRequest) | 2 | FAIL 400 |
| 20 | find_symbol | name=_council_kill_process_tree, project_path, include=def | 0 (400 BadRequest) | 1 | FAIL 400 |
| 21 | find_symbol | retry name=restart_orchestrator_via_bat (+include=def,context_lines=0) | 0 (400 BadRequest) | 2 | FAIL 400 |
| 22 | find_symbol | retry name=_council_run_agent | 0 (400 BadRequest) | 2 | FAIL 400 |
| 23 | find_symbol | retry name=_council_cleanup_broker_session | 0 (400 BadRequest) | 2 | FAIL 400 |
| 24 | find_symbol | minimal {"name":"restart_orchestrator_via_bat"} | 0 (400 BadRequest, empty body) | 64 | FAIL 400 |
| 25 | read_file_range | orchestrator_server.py L4500-4560 (OVERLAP) | 61 lines | 750 | ok |
| 26 | read_file_range | bootstrap_core_rule.py L1-60 | 14 lines (FULL FILE) | 531 | ok |
| 27 | read_file_range | orchestrator_server.py L6855-6910 (restart_orchestrator_via_bat) | 56 lines | 2010 | ok |
| 28 | read_file_range | orchestrator_server.py L4130-4185 (_council_cleanup_broker_session) | 56 lines | 2249 | ok |
| 29 | read_file_range | orchestrator_server.py L4329-4370 (_council_run_agent start) | 42 lines | 2347 | ok |
| 30 | read_file_range | orchestrator_server.py L4395-4470 (Popen + resource handling) | 76 lines | 1115 | ok |
| 31 | read_file_range | orchestrator_server.py L4560-4625 (overlap cont. + _council_cached) | 66 lines | 985 | ok |
| 32 | read_file_range | orchestrator/server/mcp_stdio.py L1-135 (structure + run_mcp_stdio) | 135 lines | 1144 | ok |
| 33 | read_file_range | mcp_stdio.py L195-275 (call_tool dispatch) | 81 lines | 891 | ok |
| 34 | read_file_range | orchestrator_server.py L8320-8395 (dispatch_tool_call + handle_call) | 76 lines | 828 | ok |

## Findings (ONLY from tool results — cite tool in EVERY row)

| # | Tool cited | file:line | severity | type | Evidence (tool output quote) |
|---|-----------|-----------|----------|------|------------------------------|
| 1 | read_file_range (#33) | orchestrator/server/mcp_stdio.py:225 | MEDIUM | defect/malformed_string | `"hint": "Pass payload as a JSON object/disrv.ct."` — corrupted token "disrv.ct." in user-facing WRONG_ARGUMENTS error hint of call_tool wrapper. Likely copy-paste/encoding artifact. |
| 2 | get_file_symbols (#5) + read_file_range (#26) | bootstrap_core_rule.py:1-14 | LOW | dead/oneoff_file | get_file_symbols returned count=0 (no functions/classes); read_file_range total_lines=14, file_group=null. File is a standalone throwaway HTTP client: `url = "http://localhost:8083/api/call/get_rule_core_text"` + `requests.post(...)`. Not a real module; lives in project root unclassified. |
| 3 | read_file_range (#28) | orchestrator_server.py:4143-4176 | LOW | silent_exception_swallow + coverage_gap | _council_cleanup_broker_session: `except Exception: return False` (L4174) swallows all errors with no logging. Only handles agent=="ope" (L4153) and agent=="kilo" (L4163); clau/codo/comc/hermes fall through to `return False` (L4176) with NO session cleanup. |
| 4 | read_file_range (#30) | orchestrator_server.py:4425-4462 | INFO/POSITIVE | resource_handling_ok | _council_run_agent Popen: pipes closed in `finally` (`for _pipe in (proc.stdout, proc.stderr): ... _pipe.close()` L4451-4456); process killed on exception (`_council_kill_process_tree(proc.pid)` L4444) and on timeout (L4458); temp output_path unlinked in outer finally (`Path(output_path).unlink(missing_ok=True)` L4568). NO resource leak. |
| 5 | ast_query (#10) + read_file_range (#27) | orchestrator_server.py:6865,6905 | INFO | tool_truncation_artifact | ast_query async_function reported cluster_count=0 (matches=50, truncated by .tmp cache). read_file_range confirms `async def restart_orchestrator_via_bat` (L6865) and `async def _toolq_post` (L6905) EXIST — ast_query result incomplete due to max_results=50 cap. |
| 6 | search_code (#13) + read_file_range (#28,#30) | orchestrator_server.py:4137,4465 | INFO | callers_false_negative | search_code callers(_council_cleanup_broker_session) returned count=1 (definition only). read_file_range confirms it IS called: `target=_council_cleanup_broker_session` (L4137, thread ref) and `_council_cleanup_broker_session_async(agent, raw, prompt, run_started_at)` (L4465). search_code missed call-by-reference (thread target=) pattern. NOT orphan. |
| 7 | search_code (#14) + read_file_range (#27) | orchestrator_server.py:6865-6868 | INFO | delegation_not_duplicate | search_code found 2 defs (orchestrator_server.py:6865 + tools_v15.py:6215). read_file_range shows orchestrator_server.py is a thin async wrapper: `import tools_v15 as _tv; return await _tv.restart_orchestrator_via_bat(delay_seconds=delay_seconds)` (L6867-6868). Intentional delegation, not duplicate logic. |
| 8 | search_code (#12-17) | (all) | INFO/META | stale_index | Every search_code result carried `"warning":"INDEX stale; returned live filesystem search results","category":"stale_index_live_fallback"` with drift.ok=true and ~200 added[] files (incl. bootstrap_core_rule.py). Orchestrator symbol index is STALE — reduces caller-analysis accuracy. |
| 9 | read_file_range (#34) | orchestrator_server.py:8362 | LOW | silent_proxy_error_swallow | dispatch_tool_call stale-MCP proxy: `except Exception: pass  # fall through to normal dispatch` (L8362) silently swallows ALL proxy errors then returns UNKNOWN_TOOL. Masks proxy-failure root cause (network/auth/timeout). |
| 10 | read_file_range (#34) | orchestrator_server.py:8340-8348 | INFO/POSITIVE | security_control | dispatch_tool_call anti-spoofing: for council_message, forces `body["sender"] = os.environ.get("COUNCIL_AGENT_ID") or _council_identify_self()` (L8343) and strips aliases `for _alias in ("from","frm","from_agent"): body.pop(_alias, None)` (L8347-8348). Worker cannot spoof sender. |
| 11 | read_file_range (#34) | orchestrator_server.py:8392-8395 | INFO/POSITIVE | destructive_op_gated | handle_clear_closed_sessions enforces: `if not _is_local(request) or not _origin_ok(request): ...403` (L8392) AND `if request.headers.get("X-Orchestrator-Token") != EXPECTED_TOKEN: ...403` (L8394). Destructive op properly gated by token + origin. |
| 12 | ast_query (#7) + read_file_range (#32,#33) | orchestrator/server/mcp_stdio.py:33,61,95,119 | INFO/POSITIVE | fail_soft_pattern | ast_query try_bare_except: 0 bare-except in cluster files. read_file_range confirms mcp_stdio.py uses `except Exception:` with safe defaults throughout: `return None` (L34), `return text` (L62), `return []` (L95), `return text` (L119). No bare except; consistent fail-soft. |
| 13 | cbm_dead_code (#6) | (full scope) | INFO | no_dead_code | cbm_dead_code returned items=0, total empty. No dead code detected in full project scope. |
| 14 | cbm_template_query (#11) | (full scope) | INFO | no_orphans_in_cluster | orphan_symbols: 50 results (total_count=1313, truncated); ALL in research/CBM_CG/*.tsx + _dispatch_*.py + _check_remaining.py + _round3_partition.py. NONE in orchestrator_server.py / mcp_stdio.py / bootstrap_core_rule.py. |
| 15 | find_symbol (#18-24) | (tool) | INFO | tool_broken | find_symbol HTTP tool returned 400 BadRequest (empty body) for all 7 attempts incl. minimal `{"name":"..."}`. Schema GET confirmed required field is `name` (only). Tool unavailable/broken for this project — symbol locations verified via search_code + read_file_range instead. |
| 16 | read_file_range (#31) | orchestrator_server.py:4574-4587 | LOW | caches_error_result | _council_cached: on fetcher exception stores the error dict into cache `_COUNCIL_METRICS_CACHE[key] = (now, result)` where `result = {"error": str(exc)}` (L4585-4586). A transient failure is cached for ttl duration. |
| 17 | ast_query (#8) | orchestrator_server.py:108 | INFO | out_of_cluster_range | class_inheriting match _DashboardAccessLogger(AccessLogger) at L108 — OUTSIDE cluster range (L4500-9441); belongs to W1_C1. Only class-inheritance match in orchestrator_server.py. (_InProcessToolRequest at L8206 has no explicit base, not returned by pattern.) |

## Overlap Verification
| Overlap region | read_file_range called? | Lines read |
|----------------|------------------------|------------|
| orchestrator_server.py L4500-5000 | yes (#25, #31) | L4500-4560 (#25) + L4560-4625 (#31) = L4500-4625 covered |
| Cross-check vs W1_C1 | yes | L4500-4528 = _council_run_agent tail (session-ID capture for clau/ope/codo/kilo/comc); L4574 = _council_cached (cluster boundary fn). Boundary confirmed: _council_run_agent spans L4329-4571 (W1_C1 start → W1_C4 overlap); _council_cached at L4574 is first W1_C4-exclusive function. |

## Coverage
| File | LOC | get_file_symbols | read_file_range | search_code | Notes |
|------|-----|-----------------|-----------------|-------------|-------|
| orchestrator_server.py | 9441 | yes — 291 symbols | yes — L4130-4185, L4329-4370, L4395-4470, L4500-4560, L4560-4625, L6855-6910, L8320-8395 (7 ranges) | yes — 6 caller queries | Cluster range L4500-9441; sampled key areas (council runtime, dispatch, restart, cleanup). Full linear coverage NOT done (4941 LOC) — sampled by symbol map. |
| orchestrator/server/mcp_stdio.py | ~493 | yes — 57 symbols | yes — L1-135, L195-275 (2 ranges) | no (no suspicious symbols surfaced) | Helpers + dispatch wrappers covered; T2/T3 wrappers (L272-493) not read (thin pass-throughs to srv.ct.*). |
| bootstrap_core_rule.py | 14 | yes — 0 symbols | yes — L1-14 (FULL FILE) | no | Full file read; 14-line throwaway script. |

## Tooling Caveats (affect accuracy, not code findings)
- INDEX STALE: search_code fell back to live filesystem (stale_index_live_fallback). Caller graphs may be incomplete.
- ast_query max_results=50 truncation: async_function + function_returning_dict results truncated; cluster coverage incomplete for these patterns (corrected via read_file_range where relevant).
- find_symbol 400: HTTP /api/call/find_symbol non-functional for this project (7 attempts). Locations verified via search_code + read_file_range.
- import_graph polluted: edge_count=2941 dominated by .tmp/uv-cache-codoc archives (numpy/httpx/protobuf), not project source.

END_CLUSTER_W1_C4
