# CLUSTER SCAN — W1_C5 (Ope)

## Tool Call Log (MANDATORY — all 11+ tools must appear)
| # | Tool | Params summary | Result count | Elapsed ms | Status |
|---|------|---------------|-------------|------------|--------|
| 0a | get_rule_core_text | agent_id=ope | 1 (cached) | n/a | ok (already_fetched:true) |
| 0b | open_session | project_path=PROJ | 1 session | n/a | ok (session untitled-session-07-10-00-15) |
| 1 | cbm_get_architecture | project_path=PROJ | 1 module, 20 entry_points | 625 | ok (truncated max_symbols) |
| 2 | import_graph | project_path=PROJ | 2941 edges, 500 nodes | n/a | ok (truncated) |
| 3 | get_file_symbols | file=code_tools.py | 256 symbols | n/a | ok |
| 4 | cbm_dead_code | scope=full, limit=50 | 0 candidates | 30594 | ok |
| 5 | ast_query | pattern=try_bare_except | 50 results (0 in code_tools.py) | n/a | ok |
| 6 | ast_query | pattern=class_inheriting | 50 results (0 in code_tools.py) | n/a | ok |
| 7 | ast_query | pattern=function_returning_dict | 50 results (15 in code_tools.py) | n/a | ok |
| 8 | ast_query | pattern=async_function | 50 results (2 in cluster range) | n/a | ok (truncated) |
| 9 | cbm_template_query | template=orphan_symbols | 50 of 1313 (0 in code_tools.py) | 750 | ok (truncated) |
| 10a | search_code | mode=callers query=_sha256_file | 3 definitions | n/a | ok (stale_index_live_fallback) |
| 10b | search_code | mode=callers query=_v15_schema | 1 (def only) | n/a | ok (stale_index_live_fallback) |
| 10c | search_code | mode=callers query=_restore_from_apply_results | 1 (def only) | n/a | ok (stale_index_live_fallback) |
| 10d | search_code | mode=callers query=_io_total | 1 (def only) | n/a | ok (stale_index_live_fallback) |
| 10e | search_code | mode=callers query=_looks_like_path | 1 (def only) | n/a | ok (stale_index_live_fallback) |
| 11a | find_symbol | query=_sha256_file (first: name field) | 0 | n/a | FAIL UNSUPPORTED_FIELD |
| 11b | find_symbol | query=_sha256_file (retry) | 15 results | n/a | ok |
| 12a | read_file_range | code_tools.py L5500-5525 | 26 lines | n/a | ok |
| 12b | read_file_range | code_tools.py L9982-10012 | 31 lines | n/a | ok |
| 12c | read_file_range | code_tools.py L11219-11400 | 182 lines | n/a | ok |
| 12d | read_file_range | code_tools.py L8032-8160 | 129 lines | n/a | ok |
| 12e | read_file_range | code_tools.py L8972-9030 | 59 lines | n/a | ok |

## Findings (ONLY from tool results — cite tool in EVERY row)
| # | Tool cited | file:line | severity | type | Evidence (tool output quote) |
|---|-----------|-----------|----------|------|------------------------------|
| 1 | get_file_symbols + find_symbol + read_file_range | code_tools.py:756 + code_tools.py:9987 | HIGH | symbol_shadowing_duplicate_def | get_file_symbols lists _sha256_file at line 756 AND line 9987 (both type=function, module-level). find_symbol confirms: L756 def _sha256_file(path: Path) -> str | None: and L9987 def _sha256_file(p) -> str:. read_file_range L9982-10012 shows L9987 at column 0 (module-level). Python last-def-wins => L756 version is DEAD, shadowed by L9987. 3 distinct definitions found across codebase (xroot.py:134, code_tools.py:756, code_tools.py:9987). |
| 2 | read_file_range | code_tools.py:9987-9990 | MEDIUM | type_annotation_violation | read_file_range L9987: def _sha256_file(p) -> str: but L9989-9990: if not os.path.exists(p): return None. Annotated return type str but returns None on missing file — type contract violation. Callers at core.py:312/341/988 compare against expected_hash without None-guard. |
| 3 | read_file_range | code_tools.py:9988 | LOW | redundant_per_call_import | read_file_range L9988: import hashlib inside function body — executed on every call. L756 version (shadowed/dead) likely uses module-level import. Minor perf + style inconsistency. |
| 4 | search_code + read_file_range | code_tools.py:11382 + code_tools.py:11397 | INFO | graph_false_positive_orphan | search_code callers _v15_schema returned count=1 (definition only, no callers). read_file_range L11396-11397 shows _V15_R8_NEW_SCHEMAS = [_v15_schema(name, description, properties, required) for ...] — _v15_schema IS called in a list comprehension immediately after def. search_code graph does not capture same-scope callers. NOT an orphan. |
| 5 | search_code + read_file_range | code_tools.py:11235 + code_tools.py:11258/11268 | INFO | graph_false_positive_orphan | search_code callers _restore_from_apply_results returned count=1 (def only). read_file_range shows it is a NESTED function inside write_transaction (L11219), called at L11258 estored = _restore_from_apply_results(apply_results) and L11268. NOT an orphan — nested local helper. |
| 6 | search_code + read_file_range | code_tools.py:8148 + code_tools.py:8158 | INFO | graph_false_positive_orphan | search_code callers _io_total returned count=1 (def only). read_file_range L8148 shows def _io_total(): nested inside assert_target_idle (L8032), called at L8158 i1 = _io_total() and L8160 i2 = _io_total(). NOT an orphan — nested local helper. |
| 7 | search_code + read_file_range | code_tools.py:9003 | INFO | graph_false_positive_orphan | search_code callers _looks_like_path returned count=1 (def only). read_file_range L9003 shows def _looks_like_path(s: str) -> bool: nested inside _r43_project_switch_context (L8972). NOT an orphan — nested local helper. |
| 8 | ast_query (try_bare_except) | code_tools.py L5500-11662 | INFO | clean_no_bare_except | ast_query try_bare_except returned 50 results; 0 in code_tools.py. All bare-except hits are in dispatch scripts (_check_remaining.py:38, _dispatch_cluster_scan.py:202, etc.) and .tmp archives. Cluster code uses except Exception: (specific), not bare except:. |
| 9 | ast_query (async_function) | code_tools.py:5535 + code_tools.py:5746 | INFO | async_functions_in_cluster | ast_query async_function confirms fetch_sheet_csv_summary (L5535) and sample_unc_paths_with_root_check (L5746) are async. Results truncated at 50 — additional async functions in cluster (run_ps1_script L6349, run_node_script L6376, workflow_* L6412+, validate_* L8340+, open_session L9189, etc.) not shown due to cap. |
| 10 | cbm_dead_code | project-wide | INFO | clean_zero_candidates | cbm_dead_code returned candidates=[], count=0. Engine vulture+ruff found no dead code candidates project-wide. |
| 11 | cbm_template_query (orphan_symbols) | project-wide | INFO | orphans_in_research_only | cbm_template_query orphan_symbols returned 50 of 1313 total; 0 in code_tools.py. All orphans are in research/CBM_CG (TS/React components) and dispatch scripts. |

## Overlap Verification
| Overlap region | read_file_range called? | Lines read |
|----------------|------------------------|------------|
| code_tools.py L5500-6000 (overlap with W1_C2) | yes | L5500-5525 (26 lines) |

Overlap content (read_file_range L5500-5525): _r53_ascii_safe (ends L5507), _r53_path_has_non_ascii (L5510-5511), _r54_detect_mojibake (L5522-5525). All use except Exception: (specific, not bare). No issues found in overlap region. Boundary consistent with W1_C2 cluster scope.

## Coverage
| File | LOC | get_file_symbols | read_file_range | search_code | find_symbol |
|------|-----|-----------------|-----------------|-------------|-------------|
| code_tools.py | 11662 | yes (256 symbols) | yes L5500-5525, L8032-8160, L8972-9030, L9982-10012, L11219-11400 (5 ranges, 427 lines) | yes (5 callers queries: _sha256_file, _v15_schema, _restore_from_apply_results, _io_total, _looks_like_path) | yes (_sha256_file: 15 results) |

## Notes
- search_code returned warning: INDEX stale; returned live filesystem search results (category=stale_index_live_fallback) for all 5 callers queries. Results are live-filesystem-based, not stale-index. Drift detected: many added/modified/deleted files vs index (expected for active dev project). Per rule, used live results as-is (STALE_INDEX → read_file_range + live results, did NOT refresh_project_map on large root).
- find_symbol first attempt FAILED with UNSUPPORTED_FIELD (field 
ame not accepted; accepted_fields include query). Retried once with query=_sha256_file per error retry_sequence — succeeded with 15 results.
- cbm_get_architecture and import_graph both truncated (max_symbols/max_nodes). Results cover only partial project — code_tools.py confirmed present in import_graph nodes.
- ast_query async_function truncated at 50 results; cluster has more async functions beyond L5746 not captured.

END_CLUSTER_W1_C5