# CLUSTER SCAN — W1_C2 (Ope)

## Tool Call Log (MANDATORY — all 11+ tools must appear)
| # | Tool | Params summary | Result count | Elapsed ms | Status |
|---|------|---------------|-------------|------------|--------|
| 0a | get_rule_core_text | agent_id=ope | already_fetched:true | ~50 | ok |
| 0b | open_session | project_path=PROJ | session opened | ~30 | ok |
| 1 | cbm_get_architecture | project_path=PROJ | 20 entry_points, modules=[] (timeout) | 6797 | ok (truncated) |
| 2 | import_graph | project_path=PROJ | 2941 edges, 500 nodes | ~500 | ok (truncated) |
| 3 | get_file_symbols | file=code_tools.py | 256 symbols | ~15 | ok |
| 4 | get_file_symbols | file=_write_r7.py | 0 symbols | ~10 | ok |
| 5 | get_file_symbols | file=_payload_gen.py | 0 symbols | ~10 | ok |
| 6 | get_file_symbols | file=_v2bench.py | 4 symbols | ~10 | ok |
| 7 | get_file_symbols | file=_v2bench2.py | 5 symbols | ~10 | ok |
| 8 | cbm_dead_code | scope=full, limit=50 | 0 candidates | 30438 | ok |
| 9 | ast_query | pattern=try_bare_except, *.py | 50 results (0 in cluster) | ~2000 | ok |
| 10 | ast_query | pattern=class_inheriting, *.py | 50 results (0 in cluster) | ~2000 | ok |
| 11 | ast_query | pattern=function_returning_dict, *.py | 50 results (~24 in code_tools.py) | ~2000 | ok |
| 12 | ast_query | pattern=async_function, *.py | 50 results (ALL in code_tools.py) | ~2000 | ok |
| 13 | cbm_template_query | template=orphan_symbols | 50 of 1313 (0 in cluster) | 2797 | ok |
| 14 | search_code | mode=callers, query=resolve_ids | 1 result (definition only) | ~500 | ok (stale_index) |
| 15 | search_code | mode=callers, query=sample_unc_paths_with_root_check | 1 result (definition only) | ~500 | ok (stale_index) |
| 16 | search_code | mode=callers, query=_r54_detect_mojibake | 1 result (definition only) | ~500 | ok (stale_index) |
| 17 | search_code | mode=callers, query=verify_get_footage_copy_completion | 1 result (definition only) | ~500 | ok (stale_index) |
| 18 | find_symbol | query=resolve_ids (retry: name UNSUPPORTED_FIELD -> query) | 2 results (def + call _v2bench2.py:98) | ~300 | ok |
| 19 | find_symbol | query=sample_unc_paths_with_root_check | 14 results (registry_data.py L553/656/1157, test L495, viewer) | ~300 | ok |
| 20 | read_file_range | file=_write_r7.py L1-200 | 200/212 lines | ~50 | ok |
| 21 | read_file_range | file=_payload_gen.py L1-200 | 3/3 lines | ~30 | ok |
| 22 | read_file_range | file=code_tools.py L5500-5525 | 26 lines | ~30 | ok |
| 23 | read_file_range | file=code_tools.py L5525-5725 | 200 lines | ~50 | ok |
| 24 | read_file_range | file=code_tools.py L5725-5925 | 200 lines | ~50 | ok |
| 25 | read_file_range | file=code_tools.py L5925-6000 | 76 lines | ~40 | ok |
| 26 | read_file_range | file=_v2bench.py L1-100 | 100/141 lines | ~40 | ok |
| 27 | read_file_range | file=_v2bench2.py L1-100 | 100/131 lines | ~40 | ok |

## Findings (ONLY from tool results — cite tool in EVERY row)
| # | Tool cited | file:line | severity | type | Evidence (tool output quote) |
|---|-----------|-----------|----------|------|------------------------------|
| 1 | read_file_range | _v2bench.py:57 | MEDIUM | silent_exception_swallow | `except Exception: return []` in search() — swallows ALL exceptions (network, JSON parse, etc.) with no logging. read_file_range L1-100 confirms: `with urllib.request.urlopen(req, timeout=15) as resp: d = json.loads(resp.read().decode()); return d.get("hits", []) if d.get("ok") else []` then `except Exception: return []` |
| 2 | read_file_range | _v2bench2.py:57 | MEDIUM | silent_exception_swallow | Identical `except Exception: return []` in search() at L57 — duplicated from _v2bench.py. read_file_range confirms same function body |
| 3 | get_file_symbols + read_file_range | _v2bench.py L1-100 + _v2bench2.py L1-100 | MEDIUM | code_duplication | get_file_symbols: _v2bench.py=4 sym (expand L28, search L45, rk L60, mk L63), _v2bench2.py=5 sym (same 4 + resolve_ids L69). read_file_range confirms 90%+ identical: same TE dict (L7-26), same TOOLQ_ROOT/API (L4-5), same expand/search/rk/mk bodies. _v2bench2.py only adds resolve_ids() L69-80 + calls it at L98 |
| 4 | get_file_symbols + read_file_range | _write_r7.py | INFO | standalone_script_no_symbols | get_file_symbols: count=0. read_file_range L1-200: 212-line one-off script with `import base64` + `content = r"""# ComC2 - Round 7 Cross-Audit Report..."""` + `target = r'D:\\BR\\DISTILATION_FINAL_PLAN\\output_verify\\verify_round07_comc2.md'`. No function/class defs — module-level script body only |
| 5 | get_file_symbols + read_file_range | _payload_gen.py | INFO | standalone_script_no_symbols | get_file_symbols: count=0. read_file_range L1-3: `import base64, json` + `with open("/tmp/write_payload.json","r") as f: print(json.dumps(json.load(f)))`. Trivial 3-line utility, no functions |
| 6 | search_code (all 4 calls) | project-wide | HIGH | stale_index | All 4 search_code calls returned `"warning":"INDEX stale; returned live filesystem search results"` + `"category":"stale_index_live_fallback"`. Drift: ~180+ added files, ~9 modified (agents_registry.json, tools_v15.py, orchestrator/core/registry_data.py, etc.), ~10 deleted. Graph-based callers mode returned 0 external callers for ALL 4 queried symbols |
| 7 | find_symbol | sample_unc_paths_with_root_check | INFO | not_orphan_api_routed | find_symbol returned 14 results: `orchestrator\core\registry_data.py:553` `('sample_unc_paths_with_root_check', 'sample_unc_paths_with_root_check')` alias registration; `:656` `"sample_unc_paths_with_root_check": "T1"` tier; `:1157` full schema def; `tests\test_code_tools_cache.py:495` `result = json.loads(asyncio.run(code_tools.sample_unc_paths_with_root_check(`. NOT orphan — called via string dispatch, invisible to graph callers mode |
| 8 | find_symbol | resolve_ids | INFO | internal_only_call | find_symbol returned 2 results: def `_v2bench2.py:69` `def resolve_ids(hits, db):` + call `_v2bench2.py:98` `ret = resolve_ids(hits, db)`. Called only within _v2bench2.py main loop — no external callers |
| 9 | ast_query (async_function) | code_tools.py L1190-5746 | INFO | all_async_tool_layer | ast_query pattern=async_function returned 50 results, ALL in code_tools.py (L1190 write_fix_html through L5746 sample_unc_paths_with_root_check). Entire tool layer is async — no sync tool functions in first 50 results |
| 10 | ast_query (try_bare_except) | cluster files | INFO | no_bare_except_in_cluster | ast_query returned 50 bare_except results — 0 in code_tools.py, _write_r7.py, _payload_gen.py, _v2bench.py, _v2bench2.py. Results were in _dispatch_*.py, _round3_partition.py, _check_remaining.py, .council/, .tmp/. Cluster files clean of bare excepts |
| 11 | ast_query (class_inheriting) | cluster files | INFO | no_class_inheritance_in_cluster | ast_query returned 50 class_inheriting results — 0 in cluster files. Only non-.tmp match: `orchestrator_server.py:108 _DashboardAccessLogger(AccessLogger)`. code_tools.py uses only functions, no classes |
| 12 | read_file_range | code_tools.py:5886-5887 | LOW | daemon_thread_no_join | read_file_range L5725-5925: `th = _th_su.Thread(target=_run, daemon=True)` at L5886, `th.start()` at L5887. Thread not joined after `q.get(timeout=timeout_sec)` at L5889. daemon=True ensures process-exit cleanup, but stuck UNC probe threads continue in background until process exits |
| 13 | read_file_range | _v2bench2.py | INFO | file_group_null_not_indexed | read_file_range returned `"file_group":null` and `"read_token_seeded":false` for _v2bench2.py (vs `"file_group":"source"`, `"read_token_seeded":true` for _v2bench.py). File not classified as source in index — recently added or excluded from index scope |
| 14 | cbm_dead_code | project-wide | INFO | no_dead_code_detected | cbm_dead_code returned `candidates:[], count:0` with disclaimer `"advisory only - Vulture/Ruff findings require manual verify before deleting code"`. Engine: vulture+ruff |

## Overlap Verification (if cluster has overlap region)
| Overlap region | read_file_range called? | Lines read |
|----------------|------------------------|------------|
| code_tools.py L5500-6000 (overlap with W1_C5) | yes | L5500-5525 (26 lines), L5525-5725 (200 lines), L5725-5925 (200 lines), L5925-6000 (76 lines) — total 502 lines covering full overlap |

### Overlap region content summary (from read_file_range tool output):
- **L5500-5532**: `_r53_ascii_safe` (ends L5507, `try: nfkd = _ud.normalize("NFKD", s); ... except Exception: return s`), `_r53_path_has_non_ascii` (L5510-5511, `return bool(s) and any(ord(c) > 127 for c in s)`), `_r54_detect_mojibake` (L5522-5532, scans `_MOJIBAKE_MARKERS` tuple, returns `(bool, list)`)
- **L5535-5743**: `fetch_sheet_csv_summary` — async, ~200 LOC. Fetches Google Sheets CSV via `webfetch()`, parses rows with `_r49_parse_csv_row`, classifies UNC/HTTP/local paths via regex, R53 header-row exclusion (`_r53_count_header_tokens >= 2`), R55 configurable sampling. Returns JSON with `ca_next_action: "sample_unc_paths_with_root_check_then_compare_log"` when UNC paths found
- **L5746-6000+**: `sample_unc_paths_with_root_check` — async, ~250+ LOC (extends past L6000 into W1_C5 scope). Samples N UNC paths, probes via `Path.exists()` with threading+Queue timeout (`_th_su.Thread(target=_run, daemon=True)`), R85 WinError surfacing for auth codes `{1326, 1219, 1385, 86, 5}`, R54 mojibake detection on all input paths. Calls `_r54_detect_mojibake` at L5829 and L5969 — confirms helper is NOT orphan. Verdict logic at L5922-5960 distinguishes `all_reachable`, `partial_reachable_paths_stale`, `unc_auth_context_mismatch`, `all_failed_server_down`

### Cross-check note for W1_C5:
- `sample_unc_paths_with_root_check` spans L5746-6000+ (continues into W1_C5). W1_C5 should verify the function's return statement and any code after L6000.
- `verify_get_footage_copy_completion` starts at L6040 (W1_C5 scope) — search_code confirmed only definition found (stale index); likely also API-routed via registry_data.py string dispatch.

## Coverage
| File | LOC | get_file_symbols | read_file_range | search_code | find_symbol |
|------|-----|-----------------|-----------------|-------------|-------------|
| code_tools.py | 11662 total (scope L1-6000) | yes (256 sym total, ~180 in L1-6000 scope) | yes L5500-6000 (502 lines, full overlap) | yes (4 symbols queried: sample_unc_paths_with_root_check, _r54_detect_mojibake, resolve_ids, verify_get_footage_copy_completion) | yes (2 symbols: sample_unc_paths_with_root_check, resolve_ids) |
| _write_r7.py | 212 | yes (0 sym) | yes L1-200 (200/212 lines) | no (no symbols to search) | no |
| _payload_gen.py | 3 | yes (0 sym) | yes L1-3 (3/3 lines, full file) | no (no symbols to search) | no |
| _v2bench.py | 141 | yes (4 sym: expand L28, search L45, rk L60, mk L63) | yes L1-100 (100/141 lines) | no (covered by _v2bench2.py analysis via find_symbol) | no |
| _v2bench2.py | 131 | yes (5 sym: expand L28, search L45, rk L60, mk L63, resolve_ids L69) | yes L1-100 (100/131 lines) | yes (resolve_ids) | yes (resolve_ids) |

END_CLUSTER_W1_C2
