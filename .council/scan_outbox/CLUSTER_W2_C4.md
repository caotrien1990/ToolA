# CLUSTER SCAN — W2_C4 (kilo)

## Tool Call Log (MANDATORY)

| # | Tool | Params summary | Result count | Status |
|---|------|---------------|-------------|--------|
| 1 | cbm_get_architecture | project_path=PROJ | 1 module (truncated, 1202 sym) | ok |
| 2 | import_graph | project_path=PROJ | 2940 edges, 500 nodes | ok |
| 3 | get_file_symbols | file=.council/mcp_full_surface_test.py | 4 symbols | ok |
| 4 | get_file_symbols | file=.council/tmp_qsleep/build/loop_orchestrator.py | 13 symbols | ok |
| 5 | get_file_symbols | file=.council/tmp_qsleep/build/artifact_schemas.py | 11 symbols | ok |
| 6 | get_file_symbols | file=.council/mcp_full_test_v3.py | 7 symbols | ok |
| 7 | get_file_symbols | file=.council/mcp_phase2_fix.py | 4 symbols | ok |
| 8 | get_file_symbols | file=.council/mcp_phase3_fix.py | 4 symbols | ok |
| 9 | get_file_symbols | file=.council/tmp_qsleep/build/qsleep_core.py | 15 symbols | ok |
| 10 | get_file_symbols | file=.council/tmp_qsleep/build/lease_manager.py | 12 symbols | ok |
| 11 | get_file_symbols | file=.council/tmp_qsleep/build/kick_qsleep.py | 2 symbols | ok |
| 12 | get_file_symbols | file=.council/mcp_phase4_final.py | 1 symbol | ok |
| 13 | get_file_symbols | file=.council/tmp_qsleep/build/cluster_runner.py | 7 symbols | ok |
| 14 | get_file_symbols | file=.council/tmp_qsleep/build/lease_orchestrator.py | 4 symbols | ok |
| 15 | get_file_symbols | file=.council/tmp_qsleep/build/queue_builder.py | 4 symbols | ok |
| 16 | get_file_symbols | file=.council/tmp_qsleep/build/measure_vram.py | 6 symbols | ok |
| 17 | get_file_symbols | file=.council/tmp_qsleep/build/process_guard.py | 4 symbols | ok |
| 18 | get_file_symbols | file=COUNCIL_TRACKER.py | 12 symbols | ok |
| 19 | get_file_symbols | file=bootstrap_rule_loader.py | 1 symbol | ok |
| 20 | get_file_symbols | file=rule_core.py | 2 symbols | ok |
| 21 | cbm_dead_code | project_path=PROJ, scope=full, limit=50 | 0 candidates | ok |
| 22 | ast_query | pattern=try_bare_except | 50 results (9 project-files, 1 in cluster) | ok |
| 23 | ast_query | pattern=class_inheriting | 50 results (2 in cluster) | ok |
| 24 | ast_query | pattern=function_returning_dict | 50 results | ok |
| 25 | ast_query | pattern=async_function | 50 results (0 in cluster) | ok |
| 26 | cbm_template_query | template=orphan_symbols | 1313 total (50 returned) | ok |
| 27 | search_code | mode=callers, query=gate_all | 0 callers | ok |
| 28 | search_code | mode=callers, query=LeaseManager | 0 callers | ok |
| 29 | search_code | mode=callers, query=run_one_cluster | 0 callers | ok |
| 30 | search_code | mode=callers, query=load_core_rule | 1 caller (self) | ok |
| 31 | search_code | mode=callers, query=process_cluster | 0 callers | ok |
| 32 | search_code | mode=callers, query=validate_raw_packet | 0 callers | ok |
| 33 | find_symbol | query=gate_all | 6 results (orchestrator_server.py _xroot_gate_allowed — different symbol) | ok |
| 34 | find_symbol | query=load_core_rule | 2 results (bootstrap_rule_loader.py self-only) | ok |
| 35 | find_symbol | query=process_cluster | 0 results | ok |
| 36 | read_file_range | file=.council/mcp_full_test_v3.py L20-35 | bare_except at L24 confirmed | ok |
| 37 | read_file_range | file=bootstrap_rule_loader.py L1-25 | load_core_rule body | ok |
| 38 | read_file_range | file=rule_core.py L1-21 | get_rule_core body | ok |
| 39 | read_file_range | file=COUNCIL_TRACKER.py L1-30 | headers + config | ok |
| 40 | read_file_range | file=.council/tmp_qsleep/build/qsleep_core.py L215-245 | gate_all body | ok |
| 41 | read_file_range | file=.council/tmp_qsleep/build/lease_manager.py L16-45 | LeaseManager.__init__ | ok |
| 42 | read_file_range | file=.council/mcp_phase4_final.py L1-25 | call function | ok |

## Findings

| # | Tool cited | file:line | severity | type | Evidence (tool output quote) |
|---|-----------|-----------|----------|------|------------------------------|
| 1 | search_code | .council/tmp_qsleep/build/qsleep_core.py:223 | HIGH | isolation | search_code callers=0 for `gate_all`; find_symbol only matches `_xroot_gate_allowed` in orchestrator_server.py (different symbol); no imports graph edge to qsleep_core |
| 2 | search_code | .council/tmp_qsleep/build/lease_manager.py:16 | HIGH | isolation | search_code callers=0 for `LeaseManager`; get_file_symbols: 12 symbols including class LeaseManager, but no import_graph edges from project files |
| 3 | search_code | .council/tmp_qsleep/build/kick_qsleep.py:36 | HIGH | isolation | search_code callers=0 for `run_one_cluster`; get_file_symbols: only 2 symbols (run_one_cluster, main) |
| 4 | search_code | .council/tmp_qsleep/build/lease_orchestrator.py:60 | HIGH | isolation | search_code callers=0 for `process_cluster`; find_symbol=0 results |
| 5 | search_code | .council/tmp_qsleep/build/artifact_schemas.py:181 | MEDIUM | isolation | search_code callers=0 for `validate_raw_packet`; 11 symbols defined but no external consumers found |
| 6 | ast_query | .council/mcp_full_test_v3.py:24 | LOW | bare_except | ast_query try_bare_except: `except:` swallows all errors silently in `req()`; read_file_range L20-25 confirms `except: return {"_error": f"HTTP {e.code}", "_raw": raw[:200]}` |
| 7 | get_file_symbols + import_graph | bootstrap_rule_loader.py:6 | MEDIUM | redundancy | `load_core_rule` hardcodes `agent_id="hermes"`; rule_core.py already provides `get_rule_core()` for file-based lookup — two mechanisms for same purpose, no caller besides self |
| 8 | cbm_dead_code | (project-wide) | INFO | clean | cbm_dead_code returned 0 candidates — no dead code detected by Vulture+Ruff |
| 9 | cbm_get_architecture | orchestrator_server.py | INFO | centralization | Only 1 module extracted (extraction.test), 1202 symbols, truncated=max_symbols; entry_points dominated by orchestrator_server.py (17 of 20) |
| 10 | ast_query | .council/tmp_qsleep/build/qsleep_core.py:43,73 | INFO | ctypes | class_inheriting: `MEMORYSTATUSEX(Structure)` and `LASTINPUTINFO(Structure)` — ctypes Windows API wrappers for system metrics |
| 11 | import_graph | .council/mcp_full_surface_test.py | INFO | reachable | Present in import_graph nodes list — file is reachable in project graph |
| 12 | get_file_symbols | .council/mcp_phase4_final.py:12 | INFO | single_symbol | Only 1 symbol: `call()` — wrapper around urllib.request for tool calls |

## Overlap Verification

| Overlap region | read_file_range called? | Lines read | Finding |
|----------------|------------------------|------------|---------|
| .council/* <-> tests/* (W2_C3) | yes | COUNCIL_TRACKER.py L1-30 | COUNCIL_TRACKER.py references `D:\BR\LIVE_COUNCIL\council_live.txt` — bridges council and runtime; no tests/* files in cluster scope |

## Coverage

| File | LOC | get_file_symbols | read_file_range | search_code |
|------|-----|-----------------|-----------------|-------------|
| .council/mcp_full_surface_test.py | ~306 | yes 4 sym | no | no |
| .council/tmp_qsleep/build/loop_orchestrator.py | ~255 | yes 13 sym | no | no |
| .council/tmp_qsleep/build/artifact_schemas.py | ~319 | yes 11 sym | no | yes (validate_raw_packet) |
| .council/mcp_full_test_v3.py | ~295 | yes 7 sym | yes L20-35 | no |
| .council/mcp_phase2_fix.py | ~135 | yes 4 sym | no | no |
| .council/mcp_phase3_fix.py | ~109 | yes 4 sym | no | no |
| .council/tmp_qsleep/build/qsleep_core.py | ~245 | yes 15 sym | yes L215-245 | yes (gate_all) |
| .council/tmp_qsleep/build/lease_manager.py | ~235 | yes 12 sym | yes L16-45 | yes (LeaseManager) |
| .council/tmp_qsleep/build/kick_qsleep.py | ~195 | yes 2 sym | no | yes (run_one_cluster) |
| .council/mcp_phase4_final.py | ~184 | yes 1 sym | yes L1-25 | no |
| .council/tmp_qsleep/build/cluster_runner.py | ~145 | yes 7 sym | no | no |
| .council/tmp_qsleep/build/lease_orchestrator.py | ~138 | yes 4 sym | no | yes (process_cluster) |
| .council/tmp_qsleep/build/queue_builder.py | ~125 | yes 4 sym | no | no |
| .council/tmp_qsleep/build/measure_vram.py | ~130 | yes 6 sym | no | no |
| .council/tmp_qsleep/build/process_guard.py | ~71 | yes 4 sym | no | no |
| COUNCIL_TRACKER.py | ~339 | yes 12 sym | yes L1-30 | no |
| bootstrap_rule_loader.py | ~36 | yes 1 sym | yes L1-25 | yes (load_core_rule) |
| rule_core.py | ~21 | yes 2 sym | yes L1-21 | no |

## Summary

- **Total files**: 18 scanned
- **Total tool calls**: 42
- **HIGH findings**: 4 (all isolation — qsleep/build subsystem has zero external callers)
- **MEDIUM findings**: 2 (duplicate bootstrap mechanism, untested artifact validation)
- **LOW findings**: 1 (bare except in mcp_full_test_v3.py)
- **INFO findings**: 5

END_CLUSTER_W2_C4