# SCAN ROUND 2 — CONSOLIDATED REPORT
**Date:** 2026-07-10
**Leader:** CozoC (zcode)
**Workers:** KiloC (deepseek-v4-pro) / OpeC (glm-5.2) / ComC (mimo-v2.5-pro) / HermC (kimi-k2.7-code)
**Method:** 4-worker parallel scan, full CORE_RULE T0 v4 synced, HTTP /api/call toolA bootstrap

---

## 1. Tool Stability Summary (cross-partition)

| Tool | KiloC P1 | OpeC P2 | ComC P3 | HermC P4 | Notes |
|------|----------|---------|---------|---------|-------|
| get_rule_core_text | — | OK (cache hit) | — | — | bootstrap |
| open_session | — | OK (resumed) | — | — | bootstrap |
| cbm_dead_code | OK 0 (narrow) | OK 80 (broad) | OK 0 | OK 50 | vulture+ruff |
| ast_query try_bare_except | OK 0 | OK 0 | OK 0 | OK 50 | clean except P4 cache noise |
| ast_query async_function | — | OK 30 | — | — | P2 only |
| ast_query class_inheriting | — | — | OK 30 | — | P3 only |
| ast_query except_exception | — | FAIL UNSUPPORTED_FIELD | — | — | pattern not in enum |
| cbm_template_query orphan_symbols | OK 134 | — | — | OK 50 | |
| cbm_template_query entry_points | — | — | — | OK 3 | |
| search_code callers | FAIL WinError5 | FAIL WinError5 | FAIL WinError5 | FAIL WinError5 | **BLOCKER** |
| find_callers (fallback) | OK 5 | OK 30 | OK 5 | — | workaround |
| read_file_range | — | OK (after field fix) | — | — | start_line/end_line |
| search_code regex | — | OK 40/2/2/30 | — | — | P2 workaround |

**Tool failures:** 4 distinct error types
- **search_code mode=callers**: WinError 5 Access Denied on `.pytest_tmp_graphfix_full` — **affects ALL 4 partitions**, root cause = stale pytest temp dir with restrictive ACLs
- **ast_query except_exception**: UNSUPPORTED_FIELD — pattern not in accepted enum (async_function, class_inheriting, function_returning_dict, try_bare_except only)
- **read_file_range**: UNSUPPORTED_FIELD — wrong field names (line_start→start_line)
- **search_code Popen**: WRONG_ARGUMENTS — JSON body broken by backslash escaping

**ToolA adoption:** 4/4 workers used HTTP /api/call tools (after full rule sync fix). Round 1 had 0/4.

---

## 2. Findings Summary

| Partition | Worker | CRITICAL | HIGH | MEDIUM | LOW | INFO | Total |
|-----------|--------|----------|------|--------|-----|------|-------|
| P1 code_tools | KiloC | 0 | 0 | 3 | 8 | 0 | 11 |
| P2 orchestrator_server | OpeC | 0 | 4 | 4 | 23 | 4 FP | 35 |
| P3 tools_v15+mission | ComC | 0 | 0 | 0 | 5 | 5 | 10 |
| P4 graph+core | HermC | 0 | 1 (tool fail) | 22 | 0 | 3 | 26 |
| **TOTAL** | | **0** | **5** | **29** | **36** | **12** | **82** |

---

## 3. TOP findings (HIGH severity, action required)

### H1 — Popen resource leak [OpeC P2 H1]
- **file:line:** orchestrator_server.py:4422-4437
- **type:** resource_leak
- **evidence:** `proc = subprocess.Popen(...)` then `_council_communicate_idle(proc,...)` with NO try/finally. If _council_communicate_idle raises, proc + stdout/stderr pipes orphaned (zombie + FD leak).
- **fix:** Wrap in try/finally: on exception kill_process_tree(proc.pid)+proc.wait(); close pipes in finally.

### H2 — _bg() silent dispatch failure [OpeC P2 H2]
- **file:line:** orchestrator_server.py:6525-6535
- **type:** silent_failure
- **evidence:** `_bg()` catches `except Exception: pass` — ALL dispatch errors swallowed. Caller already returned `{ok:true, dispatched:true}`. User believes message dispatched but it silently failed. No log, no broker entry.
- **fix:** Replace `pass` with logger.error; write error envelope to broker_filtered.jsonl.

### H3 — asyncio.create_task GC leak [OpeC P2 H3]
- **file:line:** orchestrator_server.py:6535
- **type:** race/gc_leak
- **evidence:** `asyncio.create_task(_bg())` — task NOT stored in any reference set. CPython holds only weak ref; GC may cancel mid-flight, losing dispatch silently.
- **fix:** Store in `set`: `_t = asyncio.create_task(_bg()); _pending.add(_t); _t.add_done_callback(_pending.discard)`.

### H4 — asyncio.create_task GC leak (drain) [OpeC P2 H4]
- **file:line:** orchestrator_server.py:2588
- **type:** race/gc_leak
- **evidence:** `asyncio.create_task(_council_queue_drain(agent))` — same GC risk. CAN-023 fix resets DRAINING flag on exception but task ref not stored.
- **fix:** Same as H3 — store task ref in module-level set with done-callback.

### H5 — search_code tool failure [HermC P4, all partitions]
- **file:line:** search_code tool / .pytest_tmp_graphfix_full
- **type:** tool_bug
- **evidence:** WinError 5 Access Denied on stale pytest temp artifact blocks ALL callers-mode searches project-wide.
- **fix:** search_code should catch PermissionError per-file and continue; OR clean .pytest_tmp_* dirs.

---

## 4. MEDIUM findings (selected)

| # | Partition | file:line | type | evidence |
|---|-----------|----------|------|----------|
| M1 | P1 | code_tools.py:44-61 | error-handling | _shared_inplace_write_bytes catches only PermissionError; other exceptions exit retry loop silently, returns "shared_inplace" without writing |
| M2 | P1 | code_tools.py:1617-1638 | race | update_workflow_state read-modify-write last-writer-wins; dropped fields silent |
| M3 | P1 | code_tools.py:500-508 | error-handling | _update_cache_after_write silent except Exception: pass — cache stale without notification |
| M4 | P2 | orchestrator_server.py:4438-4443 | resource_leak | Timeout path kill+wait wrapped in except Exception: pass — zombie silent |
| M5 | P2 | orchestrator_server.py (7 locks) | race_condition | 7 threading.Lock in aiohttp async server — fragile if await added inside lock block |
| M6 | P2 | .pytest_tmp_graphfix_full | test_cleanup | Stale test temp dir blocks search_code project-wide |
| M7 | P2 | orchestrator_server.py:2589-2597 | error_handling | CAN-023 re-raise propagates to caller — verify callers handle it |
| M8 | P4 | 22 orphan symbols | deadcode | fileops/common.py (8), graph/config.py (2), council/dashboard.py (1), core/errors.py (1), graph/indexer.py (2), core/interfaces.py (8) — verify before removing |

---

## 5. Dead code summary

| Partition | Source | Count | Notes |
|-----------|--------|-------|-------|
| P1 | manual | 5 | _write_r7.py, _v2bench.py, _c8_query.py, _payload_gen.py, write_fix_html (deprecated) |
| P2 | cbm_dead_code vulture+ruff | 13 | unused imports (time, json, win32gui, win32con), unused funcs (run_mcp_stdio, tool_names, ack_outbox, get_schema_version, render_council_dashboard), unused var (now, generated_at), ~30 unused exception classes in errors.py |
| P3 | cbm_dead_code | 0 | clean |
| P4 | cbm_template_query orphan_symbols | 22 | see M8 above |

**False positives flagged:** 4 (P2 FP1-FP4) — mission tools, registry_data, instance_lock, reaper — registered via string lookup, NOT dead.

---

## 6. Decision Gate

**Round 2 scan: COMPLETE.** 4/4 artifacts received with END markers. All workers used toolA (HTTP /api/call).

**Recommended next steps (priority order):**
1. **FIX H1-H4** (orchestrator_server.py) — resource leak + silent dispatch + GC leak. High impact, low risk.
2. **FIX search_code WinError 5** — per-file PermissionError catch OR clean .pytest_tmp_* dirs. Unblocks all future callers-mode scans.
3. **CLEAN dead code P1** — remove _write_r7.py, _v2bench.py, _c8_query.py, _payload_gen.py from project root.
4. **VERIFY orphan symbols P4** — 22 orphans in fileops/graph/core; cross-check with find_callers before removing.
5. **AUDIT 7 threading.Lock sites P2** — verify no await inside lock blocks.

**Tool stability verdict:** cbm_dead_code + ast_query + cbm_template_query = stable. search_code callers mode = BROKEN (WinError 5). ast_query pattern enum = LIMITED (4 patterns only).

END_SCAN_ROUND2_CONSOLIDATED
