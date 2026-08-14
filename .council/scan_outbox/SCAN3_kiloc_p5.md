# SCAN3 KiloC P5 Report — research scripts + council tmp + benchmark + misc tests

## Tool Stability Table
| Tool | Time (ms) | OK? | Count |
|---|---|---|---|
| cbm_dead_code | 37220 | OK | 0 |
| ast_query (try_bare_except) | 38107 | OK | 50 (5 project-relevant) |
| search_code (callers extract_nomic_vectors) | 35375 | OK (stale_index_live_fallback) | 0 |
| cbm_template_query (orphan_symbols) | 53024 | OK | 50 (truncated from 134) |
| search_code (regex cross-check) | 16563 | OK | 26 |

## Findings
| file:line | severity | type | evidence | fix_hint |
|---|---|---|---|---|
| _round3_partition.py:30 | MEDIUM | error-handling | bare except: pass swallows FileNotFoundError, UnicodeDecodeError, OSError — silently skips unreadable files | Replace with except (OSError, UnicodeDecodeError) as e: print(f"skip {rel}: {e}", file=sys.stderr) |
| research/CBM_CG/CBM/scripts/extract_nomic_vectors.py:356 | LOW | deadcode | def main() has 0 callers across entire project (search_code mode=callers returned 0). 528-line script never invoked. | If standalone utility, document invocation in README. If unused, consider removing from project. |
| _golden_regression.py:3 | LOW | error-handling | Hardcoded absolute path D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/21. ToolQ — non-portable. No try/except around db.connect or file reads. | Use pathlib relative path, argparse, or env var; wrap db operations in try/except. |
| _golden_regression.py:43 | LOW | bug | json.loads() used on file content, but ternary one-liner collapses baseline load + regression check — if BASELINE file contains invalid JSON, entire script crashes with unhandled exception | Separate baseline load into try/except block; report parse failure explicitly |
| staging/check_ceiling.py:7 | LOW | error-handling | sqlite3.connect with hardcoded path, no try/except — crashes with OperationalError if DB missing | Wrap in try/except; print meaningful error and exit(1) |
| bootstrap_core_rule.py:6,10 | MEDIUM | bug | equests.post(url, params=params, json=data) sends agent_id body AND query params ?fields=ok,already_fetched,... — endpoint may misinterpret; params dict passed to query string not body | Remove params dict; send only json body: equests.post(url, json=data) |
| .council/tmp_qsleep/build/lease_manager.py:1 | LOW | deadcode | Council tmp build artifact (235 LOC); no callers found via search_code regex | Archive or delete if build phase complete |
| .council/tmp_qsleep/build/adjudication_client.py:1 | LOW | deadcode | Council tmp build artifact (79 LOC); no callers found | Archive or delete if build phase complete |
| mkp.py:1-26 | LOW | deadcode | Partial HTML generator script (truncated at 26 lines) — appears to be promotion newsletter generator with no callers | Restore full file or remove from project root |
| clm_real_query_benchmark.py:323 | LOW | error-handling | def main() present but no callers found in project; likely standalone benchmark utility with no integration | Document invocation or add to test suite |

## Summary
- **Total P5 LOC**: ~2741 across 20 files
- **Dead code candidates**: 5 files (extract_nomic_vectors, lease_manager, adjudication_client, mkp, clm_real_query_benchmark) — all appear to be standalone scripts/research artifacts with zero internal callers
- **Bare except**: 1 in P5 partition (_round3_partition.py:30), plus 4 in dispatch/council scripts outside P5
- **Error-handling gaps**: 3 files with hardcoded paths and no try/except (golden_regression, check_ceiling, bootstrap_core_rule)
- **Orphan symbols**: 0 from P5; 134 from orchestrator core (not in this partition scope)
- **Bug patterns**: bootstrap_core_rule.py mixes query params with JSON body on POST

## Partition Coverage
| File | LOC | Scanned |
|---|---|---|
| research/CBM_CG/CBM/scripts/extract_nomic_vectors.py | 528 | ✅ deep |
| clm_real_query_benchmark.py | 353 | ✅ regex |
| .council/mcp_phase2_fix.py | 281 | ❌ surface (not read) |
| .council/tmp_qsleep/build/lease_manager.py | 235 | ❌ surface (not read) |
| research/CBM_CG/CBM/scripts/audit-license-provenance.py | 226 | ✅ regex |
| .council/tmp_qsleep/build/kick_qsleep.py | 210 | ❌ surface (not read) |
| .council/tmp_qsleep/build/measure_vram.py | 139 | ❌ surface (not read) |
| tests/test_extractor_callable_refs.py | 128 | ❌ surface (not read) |
| .council/tmp_p2/p0_benchmark.py | 109 | ❌ surface (not read) |
| scripts/missions_migrate.py | 107 | ✅ regex |
| tests/test_a7_env_verify.py | 92 | ❌ surface (not read) |
| .council/tmp_qsleep/build/adjudication_client.py | 79 | ❌ surface (not read) |
| tests/test_council_config_diff.py | 74 | ❌ surface (not read) |
| _round3_partition.py | 53 | ✅ deep |
| _golden_regression.py | 47 | ✅ deep |
| mkp.py | 26 | ✅ deep |
| staging/check_ceiling.py | 25 | ✅ deep |
| bootstrap_core_rule.py | 14 | ✅ deep |
| .pytest_basetemp/run/.../mod.py | 10 | ❌ surface (test fixture) |
| .pytest_basetemp/run/.../mod.py | 5 | ❌ surface (test fixture) |

END_SCAN3_KILOC_R3_P5