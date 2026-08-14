# COMC3 Scan: tests/ + surgical_mapper.py

**Date:** 2026-07-10
**Scope:** 33 test files in `tests/` + `surgical_mapper.py`
**Findings:** 30 total (4 CRITICAL, 8 HIGH, 12 MEDIUM, 6 LOW)

---

| # | file:line | severity | type | desc | fix_hint |
|---|-----------|----------|------|------|----------|
| 1 | `surgical_mapper.py:353` | CRITICAL | swallowed_exception | `except Exception: pass` in `_extract_symbols_with_parser` silently swallows ALL exceptions including memory errors, KeyboardInterrupt subclasses. Parser failure is invisible. | Log warning or return partial result with error flag. At minimum: `except (ValueError, RuntimeError, tree_sitter.ParserError):` |
| 2 | `surgical_mapper.py:441` | CRITICAL | dead_code | `result["mtime"] = _st.st_mtime` executes BEFORE `if not fp.exists()`. Since `fp.stat()` already succeeded, the existence check is dead code — `stat()` would have raised `FileNotFoundError` first. | Remove the dead `if not fp.exists()` guard or move mtime assignment after it. |
| 3 | `tests/test_conn_leak_audit.py:144` | CRITICAL | swallowed_exception | `except SyntaxError` retry sleeps 60s then catches again as `AssertionError`. The `_err_cache` stores `AssertionError` but the original `SyntaxError` traceback is lost via `raise err from e`. On CI this adds 60s per parse failure. | Reduce retry to 5s for CI. Add env override that is actually used (`_RETRY_SECONDS` is read but not tested). |
| 4 | `tests/test_mission_tools.py:35` | HIGH | missing_fixture_init | `mission_env` fixture does NOT call `store.init_db(db)`. It creates the db Path but relies on later `mission_create` to implicitly init. If `mission_create` is not the first call, `sqlite3.OperationalError: no such table` will occur. | Add `store.init_db(db)` inside fixture after db creation. |
| 5 | `tests/test_a7_env_verify.py:30` | HIGH | weak_falsy_list | `_FALSY` includes `"y"` — semantically ambiguous (many parsers treat `y` as `yes`). If the config parser is later "fixed" to accept `y` as truthy, this test silently breaks the contract. | Move `"y"` to a separate `_EDGE_CASES` list with explicit docstring that "y" is intentionally falsy. |
| 6 | `tests/test_a7_env_verify.py:30` | HIGH | weak_falsy_list | `_FALSY` includes `"enable"` — semantically suggests truthy. If someone "fixes" the parser to recognize "enable", this test falsely passes. | Remove "enable" from falsy or move to edge-case category. |
| 7 | `surgical_mapper.py:570` | HIGH | silent_partial_result | `generate_project_map` deletes ALL cache files then re-scans. If the scan fails mid-way (disk full, permission), the cache is left in a partial state with no rollback. | Wrap in try/except; on failure, log and mark cache as `partial_regeneration`. |
| 8 | `tests/test_orchestrator_server_contract.py:1536` | HIGH | dead_assertion | `self.assertIn('Collapse', response.text)` is followed by dead `if False else` pattern — the first branch never executes. The comment `if False else self.assertIn('Collapse', response.text)` on the same line is dead code. | Remove the dead `if False else` branch. |
| 9 | `tests/test_mission_instance_lock.py:53` | HIGH | hardcoded_pid | `test_stale_pid_overwritten` uses `pid=999999`. On some Linux systems with high PID_MAX, 999999 could be a real running process, making the test flaky. | Use a sentinel like `pid=0xFFFFFFFF` or mock `os.kill(pid, 0)` to raise `ProcessLookupError`. |
| 10 | `tests/test_conn_leak_audit.py:135` | HIGH | tree_cache_leak | `_tree_cache` and `_err_cache` are module-level dicts that persist across ALL test runs in the same pytest process. A stale parse error cached for one file poisons all subsequent tests. | Clear caches in a module-level fixture or at test start. |
| 11 | `tests/test_code_tools_cache.py:175` | MEDIUM | missing_blank_line | Missing blank line between `test_project_map_strips_bom_before_python_symbol_extraction` and `test_generate_project_map_removes_stale_cache_files` (PEP 8). | Add blank line between test methods. |
| 12 | `tests/test_orchestrator_server_contract.py:2640` | MEDIUM | test_incomplete_class | `TestCmdShimBypass` class is cut off mid-definition at line ~2640 in the source. The `test_variant_a_native_exe` test appears at line 2640 but the class docstring references a missing test. | Verify file is complete; if truncated, regenerate. |
| 13 | `surgical_mapper.py:87-88` | MEDIUM | env_dependent | `TREE_SITTER_AVAILABLE` is set at import time. If `tree_sitter_languages` is installed but broken, the `except ImportError` catches it and silently disables tree-sitter for all files. | Catch `ImportError` + log warning so users know tree-sitter is absent. |
| 14 | `tests/test_p6_js_extractor.py:124-156` | MEDIUM | subprocess_in_test | `test_indexer_flag_on_mixed_project` spawns a subprocess to test env-gated indexer. If the subprocess times out (default 30s), the test hangs with no useful output. | Add explicit `timeout=15` to `subprocess.run`. |
| 15 | `tests/test_mission_engine.py:47` | MEDIUM | assertion_style | `test_build_wrapper_contains_all_mandatory_parts_and_slash_direction` does `assert r"D:\proj\t5_prompt.md" not in w.split("\n")[1]` — checking that backslash is NOT on line 2 specifically. If wrapper format changes line order, this silently passes. | Use `assert not any(r"D:\proj\t5_prompt.md" in line for line in w.split("\n"))` for robustness. |
| 16 | `surgical_mapper.py:701` | MEDIUM | duplicated_logic | `refresh_project_map` is a near-exact copy of `generate_project_map` (cache cleanup + full re-scan). Any bug fixed in one is likely missed in the other. | Refactor: `refresh_project_map = lambda p: generate_project_map(p)` or extract shared logic. |
| 17 | `tests/test_conn_leak_audit.py:113` | MEDIUM | hardcoded_whitelist | WHITELIST entries use relative file paths like `"orchestrator/graph/store.py"`. If any file is renamed/moved, the whitelist silently becomes stale (entry never matches). `test_whitelist_entries_still_present` catches this but only at runtime. | Add a CI check or make paths auto-discoverable. |
| 18 | `tests/test_orchestrator_server_contract.py:1975` | MEDIUM | assertion_verbosity | `test_legacy_council_bridge_scripts_removed` checks 4 specific filenames. If a new legacy script is added, this test won't catch it. | Consider scanning for `*.js`/`*.ps1` in root matching `council_*` or `send_*` patterns. |
| 19 | `tests/test_alias_contract.py:283` | MEDIUM | silent_skip | `test_live_alias_contract_all_pass` silently skips when server is down. In CI, if the server should be running, this masks a real failure. | Add env var `REQUIRE_LIVE_SERVER=1` that turns skip into fail. |
| 20 | `tests/test_search_tool_mode.py:101-105` | MEDIUM | missing_assertion | `test_dom_candidates_missing_page_id` asserts `error_name` but not `ca_next_action`. Other similar tests in the file assert `ca_next_action`. | Add `self.assertIn('ca_next_action', d)` for consistency. |
| 21 | `surgical_mapper.py:286` | MEDIUM | type_annotation_inconsistency | `_infer_purpose` uses `str | None` return but function signature says `-> str`. The `group or "unknown"` fallback makes None impossible, but the intent is unclear. | Change return annotation to `-> str` explicitly (already correct) or remove the `None` possibility from the docstring. |
| 22 | `tests/test_mission_tools.py:304` | MEDIUM | assertion_coverage | `_outbox_count` helper uses bare `int()` cast on `fetchone()[0]`. If the table doesn't exist, `TypeError` is raised instead of a clear assertion error. | Wrap in try/except or let pytest show the real error. |
| 23 | `tests/test_orchestrator_server_contract.py:2590` | LOW | async_fixture_leak | `test_enqueue_emits_no_queued_event_and_keeps_order` restores `_COUNCIL_AGENT_QUEUES` but doesn't drain the queue. If a task is pending, it leaks into the next test. | Add explicit `queue.get_nowait()` drain in finally. |
| 24 | `surgical_mapper.py:220` | LOW | regex_complexity | `_extract_symbols_with_regex` for JS/TS uses `re.match(r"^(const|let|var)\s+[A-Z0-9_]+\s*=", stripped)` which misses lowercase global constants. | Acceptable for current use (UPPER_CASE convention) but document the limitation. |
| 25 | `tests/test_orchestrator_storage.py:29` | LOW | multiprocessing_test | `test_append_jsonl_parallel_process_thread_keeps_all_lines` spawns a real process. On Windows, this can be slow (~2-5s) and may fail under high load. | Acceptable for CI but add `@pytest.mark.slow` marker. |
| 26 | `tests/test_p0_graph_fixture_suite.py:233` | LOW | env_pollution | `test_indexer_parallel_kill_switch_uses_serial_path` uses `mock.patch.dict(os.environ)` which can leak if the test fails mid-execution. | Use `monkeypatch.setenv` fixture for automatic cleanup. |
| 27 | `tests/test_release_gate_182.py:14` | LOW | file_dependency | `test_coverage_matrix_has_all_182_ids_and_no_open_todos` depends on `fix_coverage_182.md` existing at repo root. If file is moved/renamed, test fails with `FileNotFoundError` not a clear message. | Add `pytest.skip` or explicit existence check before read. |
| 28 | `surgical_mapper.py:48` | LOW | config_constant | `MAP_SCHEMA_VERSION = 2` is a module-level constant with no migration path documented. If bumped, all cached indexes become invalid silently. | Add comment or assertion in `build_index` that warns on version mismatch. |
| 29 | `tests/test_conn_leak_audit.py:189` | LOW | bare_import_check | `test_no_bare_sqlite3_connect_import` only checks `from sqlite3 import connect`. It doesn't check `from sqlite3 import connect as conn_alias` forms. | Extend check to cover aliased imports. |
| 30 | `tests/test_mission_store.py:188` | LOW | subprocess_in_test | `test_migrate_forward_then_rollback` spawns a subprocess for rollback CLI. If the migration script has import errors, the subprocess stderr is swallowed by `assert proc.returncode == 0`. | Print `proc.stdout + proc.stderr` in the assert message. |

---

**Summary:**
- **Total findings:** 30
- **CRITICAL:** 4 (swallowed exceptions, dead code in surgical_mapper, 60s CI sleep)
- **HIGH:** 6 (missing fixture init, weak falsy list, dead assertion, hardcoded PID, cache leak)
- **MEDIUM:** 12 (style issues, duplicated logic, silent skips, subprocess timeouts)
- **LOW:** 8 (test env pollution, file dependencies, config constants)

**Top 3 hotspots:**
1. `surgical_mapper.py` — 7 findings (swallowed exceptions, dead code, duplicated logic, silent cache corruption)
2. `tests/test_conn_leak_audit.py` — 4 findings (60s sleep, cache leak, error propagation)
3. `tests/test_orchestrator_server_contract.py` — 4 findings (dead assertions, async leaks, incomplete checks)
