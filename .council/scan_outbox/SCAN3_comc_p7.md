# SCAN3 COMC P7 — council tracker + schema tests + council tmp + scripts

## Stability Report

| Tool | Status | Time (ms) | Count |
|---|---|---|---|
| cbm_dead_code | ok | 30188 | 0 candidates |
| ast_query (try_bare_except) | ok | <1000 | 50 results (mostly .tmp cache) |
| ast_query (class_inheriting) | ok | <1000 | 30 results |
| search_code (callers of COUNCIL_TRACKER) | ok | <1000 | 0 callers (stale index warning) |
| cbm_template_query (orphan_symbols) | ok | <1000 | 50 results (134 total) |

## Findings

| # | File | Line | Severity | Category | Finding |
|---|---|---|---|---|---|
| 1 | `COUNCIL_TRACKER.py` | 27-30 | MED | Dead code | Hardcoded `COUNCIL_DIR` and `OUT_DIR` with absolute `D:\0. TRIEN\...` paths. Should use `__file__`-relative or env var. |
| 2 | `COUNCIL_TRACKER.py` | 133-138 | LOW | Dead code | `open_output_file()` is a documented no-op (auto-open removed). Still called at L285 — harmless but dead call site. |
| 3 | `COUNCIL_TRACKER.py` | 119-129 | LOW | Dead code | `check_commandcode_output()` reads from hardcoded external path `D:\BR\DISTILATION_FINAL_PLAN\output_verify`. If path missing, silently returns []. Dead coupling. |
| 4 | `COUNCIL_TRACKER.py` | 62-65 | MED | Race condition | `count_lines()` called twice per file (L170 for delta, L180 for total display). File can change between calls → inconsistent delta vs total. |
| 5 | `COUNCIL_TRACKER.py` | 269-278 | MED | Race condition | Archive section: `os.path.exists(OUT_FILE)` → `zf.write(OUT_FILE)` → `os.remove(OUT_FILE)`. TOCTOU — another process can write between check and archive. |
| 6 | `bootstrap_rule_loader.py` | 11 | HIGH | Bug | `requests.post(..., fields=[...])` — `fields` is not a valid `requests` parameter. This call will fail or be silently ignored. Should remove `fields` kwarg. |
| 7 | `send_council_message.py` | 12 | MED | Bug | `requests.post(url, params=params, json=data)` — `params` adds query params to URL, but the API expects JSON body. `params` is likely dead/misleading. |
| 8 | `.council/mcp_phase3_fix.py` | 105-108 | HIGH | Bug | `fix.pop("method", "POST")` and `fix.pop("payload", None)` mutate the `FIXES` dict entry during iteration. Second call to the same tool name will lose `method`/`payload` keys — silent data corruption on retry. |
| 9 | `.council/mcp_phase3_fix.py` | 26 | LOW | Error handling | Bare `except Exception` at L123 swallows all errors during tool re-testing. |
| 10 | `.council/tmp_qsleep/build/guardrails.py` | 24-39 | MED | Race condition | `check_no_double_process()` reads `processed_clusters.jsonl` without file lock. Two concurrent processes can both pass the check → double-process. |
| 11 | `.council/tmp_qsleep/build/queue_builder.py` | 127-148 | LOW | Error handling | `preview()` catches broad `(URLError, OSError, json.JSONDecodeError)` and returns `{ok: False}`. Caller must check `ok` — no exception propagation. Acceptable for preview-only function. |
| 12 | `scripts/comc_real_tool_usage_test.py` | 88-100 | LOW | Error handling | `call_tool()` catches `HTTPError` and returns `(code, body)`. Generic catch at L100 returns `(None, {})` for unexpected exceptions — `None` http_status may confuse downstream status logic. |
| 13 | `research/CBM_CG/CBM/scripts/gen-ui-licenses.py` | 51 | LOW | Error handling | `subprocess.run("npm ls ...", shell=True, ...)` uses `shell=True` (documented as needed for npm.cmd on Windows). stderr not captured — npm failures produce no diagnostic output. |
| 14 | `.council/mcp_phase3_fix.py` | full file | LOW | Dead code | One-shot phase 3 test script. Hardcoded paths, reads previous report from `.council/mcp_full_surface_report_final.json`. Not importable or reusable. |
| 15 | `.council/mcp_debug_fails.py` | full file | LOW | Dead code | One-shot debug script with hardcoded tool names and payloads. 56 lines of non-reusable diagnostic code. |
| 16 | `send_council_message.py` | full file | LOW | Dead code | One-shot script with hardcoded broker task text. 26 lines, single-purpose. |
| 17 | `build_req.py` | full file | LOW | Dead code | One-shot utility that writes a JSON request body to disk. 14 lines. |
| 18 | `.council/tmp_b4/bench_runner.py` | 34 | LOW | Resource leak | `json.load(open(...))` — file handle not closed explicitly (relies on GC). Should use `with` statement. |
| 19 | `.council/tmp_b4/bench_runner.py` | 66 | LOW | Resource leak | `json.dump(report, open(path, "w", ...))` — same pattern, file handle not explicitly closed. |
| 20 | `.council/tmp_qsleep/build/test_jinja_toolcall.py` | full file | LOW | Dead code | One-shot test script for llama-server Jinja verification. Writes result to disk, not reusable. |
| 21 | `.pytest_basetemp/` files (2) | - | INFO | Dead code | pytest temp artifacts (`mod.py` x2). 6-10 lines each. Should be gitignored or cleaned. |
| 22 | `tests/test_schema_params_contract.py` | 8-9 | LOW | Error handling | `import orchestrator_server as srv` wrapped in bare `except Exception`. Acceptable for env-dependent import but hides import bugs. |
| 23 | `COUNCIL_TRACKER.py` | 24 | LOW | Thread safety | `LOG_FILES` list mutated at module load via `glob.glob()`. Safe at import time but fragile if module is reloaded. |
| 24 | `.council/tmp_qsleep/build/stop_conditions.py` | full file | LOW | Dead code | Pure check function, only usable within qsleep loop context. No callers found in partition. |
| 25 | `scripts/bench_p6_clustering.py` | 22 | LOW | Hardcoded path | `DB_URI` hardcoded to specific project path. Should use `__file__`-relative or env var. |

## Summary

- **Total findings**: 25
- **HIGH**: 2 (bootstrap_rule_loader fields kwarg bug, mcp_phase3_fix dict mutation)
- **MED**: 5 (COUNCIL_TRACKER hardcoded paths x2, race conditions x2, send_council_message params bug)
- **LOW**: 17 (dead code scripts, error handling, resource leaks, hardcoded paths)
- **INFO**: 1 (pytest temp artifacts)
- **Vulture/Ruff dead code scan**: 0 candidates found (clean)
- **Bare except patterns in partition**: 0 (ast_query bare_except hits are all in `.tmp/` cache, not partition files)

END_SCAN3_COMC_R3_P7
