# SCAN3 HermC Partition P8 Report

Round: 3 | Worker: HermC (hermes/kimi-k2.7-code) | Partition: P8
Project: D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator

## Stability Table
| Tool | Status | Count | Elapsed_ms | Notes |
|------|--------|-------|------------|-------|
| cbm_dead_code | ok | 0 | 30625.0 | advisory only — Vulture/Ruff findings require manual verify before deleting code |
| cbm_template_query orphan_symbols | ok | 50 | 32.0 | truncated=True |
| cbm_template_query entry_points | ok | 3 | 16.0 | |
| ast_query try_bare_except | ok | 5 in-project | - | 50 total incl .tmp |
| search_code callers sync_tool_matrix | ok | 0 | - | stale_index_live_fallback |
| search_code regex threading.Thread | ok | 8 | - | |
| search_code regex except Exception | ok | 50 | - | 50 cap, many in code_tools.py |

## Partition P8 Files Scanned
21 files (~2740 LOC):
- .council/mcp_full_surface_test.py
- staging/clm_qwen_rerank.py
- .council/mcp_full_test_v3.py
- scripts/sync_tool_matrix.py
- research/CBM_CG/CBM/pkg/pypi/src/codebase_memory_mcp/_cli.py
- .council/tmp_qsleep/build/cluster_runner.py
- broker_research.py
- research/CBM_CG/CBM/scripts/generate-lang-code.py
- .council/tmp_qsleep/build/process_guard.py
- .council/tmp_qsleep/build/qwake.py
- .council/tmp_qsleep/build/kick_safety.py
- .council/tmp_qsleep/build/test_adjudication_gate.py
- mkpromo5.py
- tests/test_registry_pipeline.py
- research/CBM_CG/CBM/scripts/license-gate-check.py
- staging/debug_candidates.py
- rule_core.py
- orchestrator/runtime/__init__.py
- .pytest_basetemp/run/test_python_callable_reference1/thread_target_kwarg/mod.py
- research/CBM_CG/CBM/pkg/pypi/src/codebase_memory_mcp/__main__.py
- orchestrator/council/__init__.py

## Findings Table
| Category | File | Line | Kind / Detail | Severity | Notes |
|----------|------|------|---------------|----------|-------|
| dead_code | - | - | no candidates | info | vulture+ruff returned empty |
| orphan_symbol | orchestrator/fileops/common.py | 129 | common.atomic_write_text | info | may be library/util glue |
| orphan_symbol | orchestrator/fileops/common.py | 150 | common.decode_text_payload | info | may be library/util glue |
| orphan_symbol | orchestrator/fileops/common.py | 49 | common.detect_text_encoding | info | may be library/util glue |
| orphan_symbol | orchestrator/fileops/common.py | 237 | common.ensure_fresh_index | info | may be library/util glue |
| orphan_symbol | orchestrator/fileops/common.py | 177 | common.find_project_root | info | may be library/util glue |
| orphan_symbol | orchestrator/fileops/common.py | 61 | common.is_binary_file | info | may be library/util glue |
| orphan_symbol | orchestrator/fileops/common.py | 80 | common.resolve_project_file | info | may be library/util glue |
| orphan_symbol | orchestrator/fileops/common.py | 133 | common.sha256_file | info | may be library/util glue |
| orphan_symbol | orchestrator/graph/config.py | 80 | config.graph_db_path | info | may be library/util glue |
| orphan_symbol | orchestrator/graph/config.py | 19 | config.use_graph_for | info | may be library/util glue |
| entry_point | orchestrator/core/errors.py | 27 | errors.OrchestratorError.__init__ | info | |
| entry_point | orchestrator/graph/extractors/javascript.py | 89 | javascript._JsWalker.__init__ | info | |
| entry_point | orchestrator/graph/extractors/python.py | 81 | python._Extractor.__init__ | info | |
| bare_except | .council/mcp_full_test_v3.py | 24 | bare except | warn | catches KeyboardInterrupt/SystemExit too |
| orphan_usage | scripts/sync_tool_matrix.py | - | no callers found | info | stale_index fallback; likely standalone script |

## Observations
- cbm_dead_code returned zero candidates for the full project.
- orphan_symbols list is long (50+); many are utility functions in orchestrator/fileops, graph, storage, tools, mission, runtime. These may be library glue rather than truly dead code.
- entry_points only identified __init__ methods in errors.py and extractors, suggesting this template is tuned to class constructors, not CLI entry points.
- search_code callers for 'sync_tool_matrix' returned zero results with stale_index warning; fallback used live filesystem but still no references. The script is likely a standalone maintenance utility.
- try_bare_except hits inside P8: .council/mcp_full_test_v3.py:24 (partition file). Other in-project bare excepts are in _dispatch_scan2_v2.py, _dispatch_scan3.py, _round3_partition.py, .council/mcp_phase4_final.py.
- threading.Thread appears in orchestrator_server.py (4 sites), _dispatch_scan2_v2.py, _dispatch_scan3.py, and tests. Not in the listed P8 source files, but orchestrator_server.py is the runtime host.
- except Exception is extremely common (50+ hits, capped). code_tools.py alone has dozens. Within P8, no explicit except Exception was found by this regex, but mcp_full_test_v3.py has a bare except.

END_SCAN3_HERMC_R3_P8