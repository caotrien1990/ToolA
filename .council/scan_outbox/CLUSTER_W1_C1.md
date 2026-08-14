# CLUSTER SCAN — W1_C1 (kilo/ltnai/glm-5.2)

Worker: KILO (kilo/ltnai/glm-5.2) — TOOL-DRIVEN CLUSTER SCAN (broker one-shot, no prior context)
Project root: D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator
Session: untitled-session-07-10-00-15 | Orchestrator gen: 4955698a59ab | [A:API] http://localhost:8083
Cluster scope: orchestrator_server.py L1-5000 + path_runtime.py + restart_orchestrator.py + mcp_client_manager.py
Overlap region (W1_C4): orchestrator_server.py L4500-5000

NOTE on tool fidelity: search_code "callers" mode ran against a STALE INDEX with live-filesystem fallback (warning: "INDEX stale; returned live filesystem search results", category "stale_index_live_fallback"). For several internal helpers it returned count=1 (definition line only). read_file_range ground-truth proved at least one such symbol (_ensure_mcp_loop) IS called intra-file, so count=1 means "live search did not surface call sites", NOT "confirmed dead". cbm_dead_code (count=0) and cbm_template_query orphan_symbols (0 cluster hits) corroborate: NO confirmed dead/orphan code in cluster. find_symbol endpoint returned HTTP 400 (empty body) on all 5 attempts (incl. minimal {"name":...}); symbol locations were instead verified via get_file_symbols (exact line numbers for all 4 files) + read_file_range (ground truth).

## Tool Call Log (MANDATORY — all 11 tool types called; 40 [A] HTTP calls + schema/grep aux)
| # | Tool | Params summary | Result count | Elapsed ms | Status |
|---|------|---------------|-------------|------------|--------|
| 0 | GET /health | — | ok=true gen=4955698a59ab | ~50 | ok |
| 0b | get_rule_core_text | agent_id=kilo | already_fetched=false (text fetched) | ~30 | ok |
| 0c | open_session | project_path=PROJ | session_id=untitled-session-07-10-00-15 | ~40 | ok |
| 1 | cbm_get_architecture | project_path=PROJ | 1 module shown (truncated max_symbols), 20+ entry_points | 602 | ok (truncated) |
| 2 | import_graph | project_path=PROJ | 108568 bytes edges (path_runtime=12, restart_orchestrator=8, mcp_client_manager=5, orchestrator_server=66 refs) | 2149 | ok |
| 3a | get_file_symbols | file=orchestrator_server.py | ~180 symbols (file total_lines=9441) | 573 | ok |
| 3b | get_file_symbols | file=path_runtime.py | 3 symbols | 177 | ok |
| 3c | get_file_symbols | file=restart_orchestrator.py | 10 symbols | 445 | ok |
| 3d | get_file_symbols | file=mcp_client_manager.py | 1 symbol | 184 | ok |
| 4 | cbm_dead_code | scope=full, limit=50 | count=0 candidates=[] | 34684 | ok |
| 5 | ast_query | pattern=try_bare_except, *.py, max=50 | ~50 results, 0 in cluster files | 47755 | ok |
| 6 | ast_query | pattern=class_inheriting, *.py, max=50 | ~50 results, 1 in cluster (orch L108) | 10216 | ok |
| 7 | ast_query | pattern=function_returning_dict, *.py, max=50 | count=50 (10 in orchestrator_server.py L1-5000) | 5957 | ok |
| 8 | ast_query | pattern=async_function, *.py, max=50 | count=50 (all code_tools.py; capped — mcp_client_manager.py:9 main async NOT captured) | 3819 | ok (capped) |
| 9 | cbm_template_query | template=orphan_symbols, limit=50 | ~50 results, 0 in cluster .py (all research/*.tsx) | 478 | ok |
| 10a | search_code | mode=callers, query=_council_kill_process_tree | count=2 (reaper.py:145 import + def L4115) | 982 | ok (stale_index_live_fallback) |
| 10b | search_code | mode=callers, query=_run_mcp_coro | count=1 (def L154 only) | 2097 | ok (stale_index_live_fallback) |
| 10c | search_code | mode=callers, query=_ensure_mcp_loop | count=1 (def L131 only) | 3068 | ok (stale_index_live_fallback) |
| 10d | search_code | mode=callers, query=_council_cleanup_broker_session_async | count=1 (def L4132 only) | 3587 | ok (stale_index_live_fallback) |
| 10e | search_code | mode=callers, query=_council_run_agent | count=1 (def L4329 only) | 4129 | ok (stale_index_live_fallback) |
| 10f | search_code | mode=callers, query=kill_targets | count=1 (def restart L123 only) | 866 | ok (stale_index_live_fallback) |
| 10g | search_code | mode=callers, query=start_orchestrator | count=1 (def restart L151 only) | 2015 | ok (stale_index_live_fallback) |
| 10h | search_code | mode=callers, query=_council_http_json | count=1 (def L4935 only) | 2507 | ok (stale_index_live_fallback) |
| 10i | search_code | mode=callers, query=_rotate_token | count=1 (def L236 only) | 3015 | ok (stale_index_live_fallback) |
| 11a | find_symbol | name=_council_kill_process_tree (+project_path) | — | ~4000 | FAIL HTTP 400 (empty body) |
| 11b | find_symbol | name=_run_mcp_coro, include=callers | — | ~4000 | FAIL HTTP 400 |
| 11c | find_symbol | name=_council_cleanup_broker_session_async, include=callers | — | ~4000 | FAIL HTTP 400 |
| 11d | find_symbol | name=_council_kill_process_tree (minimal) | — | 56 | FAIL HTTP 400 (empty body) |
| 11e | find_symbol | name=_council_kill_process_tree, path=PROJ, include=def | — | 54 | FAIL HTTP 400 |
| 11s | GET find_symbol/schema | — | schema ok (required:name, include enum) | ~30 | ok (location verify done via get_file_symbols+read_file_range) |
| 12a | read_file_range | orchestrator_server.py L128-205 | 78 lines (_ensure_mcp_loop+_run_mcp_coro) | 5158 | ok |
| 12b | read_file_range | orchestrator_server.py L4110-4180 | 71 lines (kill_process_tree+cleanup) | 5287 | ok |
| 12c | read_file_range | orchestrator_server.py L100-135 | 36 lines (_DashboardAccessLogger) | 1424 | ok |
| 12d | read_file_range | orchestrator_server.py L226-295 | 70 lines (token rotation/validation) | 1556 | ok |
| 12e | read_file_range | orchestrator_server.py L4500-4525 | 26 lines (overlap start, _council_run_agent reply extraction) | 1688 | ok |
| 12f | read_file_range | restart_orchestrator.py L1-180 | 180 lines (full, total_lines=200) | 561 | ok |
| 12g | read_file_range | mcp_client_manager.py L1-31 | 31 lines (full) | 681 | ok |
| 12h | read_file_range | path_runtime.py L1-46 | 46 lines (full) | 2380 | ok |
| 12i | read_file_range | orchestrator_server.py L4980-5010 | 31 lines (overlap end, cluster boundary _council_local_clau_usage L4993) | 2709 | ok |
| AUX | rg on saved tool JSONs | grep 05/08/02_*.json for cluster files | bare_except=0, async=0(in cluster, capped), import refs confirmed | — | ok |

## Findings (ONLY from tool results — cite tool in EVERY row)
| # | Tool cited | file:line | severity | type | Evidence (tool output quote) |
|---|-----------|-----------|----------|------|------------------------------|
| 1 | read_file_range(12b) + search_code(10a) + ast_query(6) | orchestrator_server.py:4115-4129 | MEDIUM | silent_error_swallow | read_file_range L4110-4180: `def _council_kill_process_tree(pid: int) -> None:` ... `subprocess.run(["taskkill","/PID",str(pid),"/T","/F"],...)` then `except Exception:` / `pass` (L4128-4129) swallows ALL kill errors. search_code(10a) count=2: `orchestrator/mission/reaper.py:145 ... import _council_kill_process_tree` + def L4115 — so function IS referenced but failures are invisible (masks pid/permission errors during process-tree kill). |
| 2 | read_file_range(12d) | orchestrator_server.py:236-248 | MEDIUM | token_desync_risk | read_file_range L226-295: `def _rotate_token() -> str:` sets `EXPECTED_TOKEN=_secrets.token_urlsafe(32)` + `TOKEN_CREATED_AT=time.time()` then `_TOKEN_FILE.write_text(EXPECTED_TOKEN,...)` inside `except Exception:` / `pass` (L246-247). If file write fails, in-memory token rotates but persisted token file stays stale → on [A] restart the old token loads → MUTATION_TOKEN_REQUIRED mismatch. search_code(10i) count=1 (def only). |
| 3 | read_file_range(12c) + ast_query(6) | orchestrator_server.py:108-122 | LOW | silent_error_swallow (graceful fallback) | ast_query(6) class_inheriting: `{"file":"orchestrator_server.py","line":108,"name":"_DashboardAccessLogger","bases":["AccessLogger"]}`. read_file_range L100-135: `class _DashboardAccessLogger(web.AccessLogger):` `def log(self,request,response,time):` ... `except Exception:` / `pass` (L120-121) then `super().log(request, response, time)` (L122) — dashboard-path logging errors swallowed but degrade gracefully to default logger. |
| 4 | read_file_range(12b) + search_code(10d) | orchestrator_server.py:4132-4176 | LOW-MEDIUM | unbounded_thread_spawn | read_file_range L4110-4180: `_council_cleanup_broker_session_async` spawns `threading.Thread(target=_council_cleanup_broker_session, ..., daemon=True, name=f"council-cleanup-{agent}")` per broker reply (L4136-4140) with no pool/limit/join. `_council_cleanup_broker_session` runs `subprocess.run([cli("opencode"),"session","delete",session_id], ..., timeout=30, ...)` (L4157-4172) + `except Exception: return False` (L4174-4175). Under high broker throughput → unbounded daemon threads each blocking up to 30s. search_code(10d) count=1 (def only). |
| 5 | read_file_range(12a) + search_code(10b,10c) | orchestrator_server.py:131-163 | LOW | caller_coupling_unverified | read_file_range L128-205: `_ensure_mcp_loop()` (L131) creates persistent daemon thread `loop.run_forever()` (L141); `_run_mcp_coro(coro)` (L154) calls `loop = _ensure_mcp_loop()` (L156) + `asyncio.run_coroutine_threadsafe(coro, loop).result()` (L163). search_code(10c) _ensure_mcp_loop count=1 (def only) YET read proves it IS called at L156; search_code(10b) _run_mcp_coro count=1 (def only) — caller of _run_mcp_coro not surfaced. cbm_dead_code(4)=0 + orphan_symbols(9)=0 → NOT confirmed dead; MCP async-bridge entry point unverified (likely dynamic dispatch). |
| 6 | read_file_range(12g) + ast_query(8) | mcp_client_manager.py:9 | INFO | ast_query_coverage_gap | ast_query(8) async_function count=50 (all code_tools.py — capped at max_results=50). read_file_range(12g) mcp_client_manager.py L1-31: `async def main():` (L9) `async with stdio_client(server_params)` (L16) — IS async but NOT captured by ast_query due to result cap. Cluster DOES contain async code (contradicts the capped ast_query result). |
| 7 | search_code(10b-10i) + read_file_range(12a,12f) | orchestrator_server.py + restart_orchestrator.py | INFO | search_coverage_gap | search_code callers returned count=1 (def-only) for 7 helpers: _run_mcp_coro, _ensure_mcp_loop, _council_run_agent, _council_cleanup_broker_session_async, _rotate_token, _council_http_json, kill_targets, start_orchestrator. read_file_range(12a) proved _ensure_mcp_loop called intra-file (L156); get_file_symbols(3c)+read_file_range(12f) show restart kill_targets(L123)/start_orchestrator(L151) invoked from main()(L163). Conclusion: stale-index live-fallback does NOT reliably surface intra-file / CLI-entrypoint call sites — count=1 is NOT a dead-code verdict. |
| 8 | cbm_dead_code(4) + cbm_template_query(9) | cluster-wide | INFO | no_dead_code_no_orphans | cbm_dead_code(4): `{"candidates":[],"count":0}`. cbm_template_query(9) orphan_symbols: results all `research/CBM_CG/CBM/graph-ui/src/*.tsx` (0 cluster .py). No confirmed dead/orphan symbols in the 4 cluster files. |
| 9 | ast_query(5) | cluster-wide | INFO | zero_bare_excepts | ast_query(5) try_bare_except results: `_check_remaining.py:38`, `_dispatch_cluster_scan.py:202`, `_dispatch_fix_r3.py:99`, `_dispatch_fix_r3_codo.py:70`, `_dispatch_scan3.py:112`, `_dispatch_verify_r3.py:70`, `_dispatch_verify_r3_ope.py:70`, `_round3_partition.py:30`, `.council/mcp_full_test_v3.py:24` + .tmp cache. rg on saved results for orchestrator_server/path_runtime/restart_orchestrator/mcp_client_manager = 0 matches. Cluster files have ZERO bare `except:` (broad `except Exception:` exists — see findings 1,2,3,4). |
| 10 | ast_query(7) | orchestrator_server.py L1-5000 | INFO | api_contract_dict_returners | ast_query(7) function_returning_dict count=50; 10 in orchestrator_server.py L1-5000: _error_contract_meta L1329, _missing_required_error L1473, _schema_for_tool L1518, _generated_payload_example L1526, _ca_guidance L1802, _r40_suggest_alternative L1980, _artifact_already_complete L2354, _council_build_dispatch_reply L2690, _council_enqueue_or_run L2780, _council_read_json L3314. Consistent API-contract dict-return pattern (error/payload/schema builders) — no defect, noted for context. |
| 11 | import_graph(2) + read_file_range(12h) | path_runtime.py | INFO | clean_utility_coupling | import_graph(2): path_runtime=12 refs (imported by code_tools.py etc.). read_file_range(12h) L1-46: `find_orchestrator_dir` (L16) has UNC guard `if raw.startswith("\\\\") or raw.startswith("//"): return None` (L21-22) to avoid 30-60s SMB hang; self-contained (avoids circular import). `resolve_ui_html` (L36). No issues. |
| 12 | read_file_range(12f) + get_file_symbols(3c) | restart_orchestrator.py | INFO | process_mgmt_fails_loud | read_file_range(12f) L1-180: `_run` (L24) subprocess.run timeout=20 capture_output; `_ps_json` (L36) `raise RuntimeError(...)` on PS failure (L48) — fails loud, no silent swallow. `kill_targets` L123, `wait_until_clear` L142, `start_orchestrator` L151, `main` L163 (get_file_symbols 3c). CLI kill-only entrypoint (docstring L2-6). search_code(10f,10g) count=1 each (intra-file from main, see finding 7). |

## Overlap Verification (cluster overlap region with W1_C4: orchestrator_server.py L4500-5000)
| Overlap region | read_file_range called? | Lines read | Content summary |
|----------------|------------------------|------------|-----------------|
| orchestrator_server.py L4500-5000 | yes (2 chunks) | L4500-4525, L4980-5010 | L4500-4525: inside `_council_run_agent` (def L4329) — reply extraction per agent (clau/hermes/ope/codo/kilo/comc) + `captured_session_id` capture (L4512-4525). L4980-5010: cluster boundary — comment block "Local usage readers (Task 3)" (L4986-4990) + `def _council_local_clau_usage() -> dict:` starts L4993 (this function continues BEYOND L5000 into W1_C4 territory — handoff point). |

Handoff note for W1_C4: `_council_local_clau_usage` (L4993) is the boundary function; its body (L4999+) and subsequent `_council_local_codo_usage` L5058, `_council_local_ope_usage` L5182 belong to W1_C4. No defect observed in the L4500-5000 overlap slice.

## Coverage
| File | LOC | get_file_symbols | read_file_range | search_code |
|------|-----|-----------------|-----------------|-------------|
| orchestrator_server.py | 9441 total (cluster scope L1-5000) | yes (~180 sym, full file; ~90 in L1-5000) | yes: L100-135, L128-205, L226-295, L4110-4180, L4500-4525, L4980-5010 | yes: 8 symbols (kill_process_tree, run_mcp_coro, ensure_mcp_loop, cleanup_broker_session_async, run_agent, http_json, rotate_token + class_inherit L108) |
| path_runtime.py | 46 | yes (3 sym) | yes: L1-46 (full) | no (clean utility; import_graph 12 refs) |
| restart_orchestrator.py | 200 | yes (10 sym) | yes: L1-180 | yes: kill_targets, start_orchestrator |
| mcp_client_manager.py | 31 | yes (1 sym) | yes: L1-31 (full) | no (test harness, 1 symbol; async confirmed via read) |

Tool-type coverage check (all 11 mandatory + read): cbm_get_architecture ✓, import_graph ✓, get_file_symbols ✓(x4), cbm_dead_code ✓, ast_query ✓(x4 patterns), cbm_template_query ✓, search_code ✓(x9 symbols), find_symbol ✓(called x5 — endpoint returned 400; locations verified via get_file_symbols+read_file_range), read_file_range ✓(x9 ranges). All 12 tool types invoked.

END_CLUSTER_W1_C1
