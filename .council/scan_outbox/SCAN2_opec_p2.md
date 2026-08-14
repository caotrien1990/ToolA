# SCAN2 — OpeC Round 1 / Partition P2
**Worker:** OpeC (ope / glm-5.2) | **Date:** 2026-07-10 | **Partition:** P2 (orchestrator_server + server/session/runtime/storage + tests)
**Project root:** D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator
**Objectives:** dead code, bug patterns, error-handling gaps, race conditions, socket/resource leaks

---

## 1. Stability Report

| # | Tool | ok | count | elapsed / notes |
|---|------|----|-------|-----------------|
| 1 | get_rule_core_text | true | — | cache hit (already_fetched:true, sha256:75eb0d6c) |
| 2 | open_session | true | — | resumed session untitled-session-07-10-00-15; project confirmed |
| 3 | cbm_dead_code (file_filter=orchestrator_server.py) | true | 0 | single-file vulture scope too narrow; 0 candidates |
| 4 | cbm_dead_code (scope=core, no filter) | true | 80 | 422ms; vulture+ruff; advisory |
| 5 | ast_query try_bare_except | true | 0 | clean — no bare `except:` in orchestrator_server.py |
| 6 | ast_query async_function | true | 30 | mapped all async handlers in orchestrator_server.py |
| 7 | ast_query except_exception | **false** | — | UNSUPPORTED_FIELD — accepted: async_function, class_inheriting, function_returning_dict, try_bare_except only |
| 8 | search_code mode=callers _error_response | **false** | — | TOOL_ERROR WinError 5: access denied on .pytest_tmp_graphfix_full stale test dir |
| 9 | find_callers _error_response | true | 30 | fallback for #8; success |
| 10 | search_code regex "except Exception" (orchestrator_server.py) | true | 40 | 40 broad-except sites found |
| 11 | search_code regex "Popen" (attempt 1) | **false** | — | WRONG_ARGUMENTS — JSON body broken by backslash escaping |
| 12 | search_code regex "Popen" (attempt 2, simplified) | true | 2 | 2 Popen refs |
| 13 | search_code regex "create_task" | true | 2 | 2 fire-and-forget tasks |
| 14 | search_code regex "_lock\|Lock" | true | 30 | 7 threading.Lock/RLock + many `with` sites |
| 15 | read_file_range (line_start/line_end) | **false** | — | UNSUPPORTED_FIELD — correct fields: start_line/end_line |
| 16 | read_file_range (start_line/end_line) ×3 | true | — | confirmed Popen cleanup, drain scheduling, _bg() swallow |

**Errors encountered:** 4 (UNSUPPORTED_FIELD ×2, TOOL_ERROR ×1, WRONG_ARGUMENTS ×1). All recovered except search_code callers (worked around with find_callers).

---

## 2. Findings

### HIGH severity

| # | file:line | severity | type | evidence | fix_hint |
|---|-----------|----------|------|----------|----------|
| H1 | orchestrator_server.py:4422-4437 | HIGH | resource_leak | `proc = subprocess.Popen(...)` then `_council_communicate_idle(proc,...)` — NO try/finally. If _council_communicate_idle raises, proc + stdout/stderr pipes orphaned (zombie process + FD leak). | Wrap in try/finally: on any exception kill_process_tree(proc.pid)+proc.wait(); close proc.stdout/stderr in finally. |
| H2 | orchestrator_server.py:6525-6535 | HIGH | silent_failure | `_bg()` catches `except Exception: pass` — ALL dispatch errors swallowed. Caller already returned `{ok:true, dispatched:true}` at L6536. User believes message dispatched but it silently failed. No log, no broker_filtered.jsonl entry. | Replace `pass` with logger.error; write error envelope to broker_filtered.jsonl so dashboard shows failure; consider tracking dispatch state. |
| H3 | orchestrator_server.py:6535 | HIGH | race/gc_leak | `asyncio.create_task(_bg())` — task NOT stored in any reference set. CPython holds only weak ref; GC may cancel mid-flight, losing dispatch silently. | Store in `set`: `_t = asyncio.create_task(_bg()); _pending.add(_t); _t.add_done_callback(_pending.discard)`. |
| H4 | orchestrator_server.py:2588 | HIGH→MEDIUM | race/gc_leak | `asyncio.create_task(_council_queue_drain(agent))` — same GC risk. CAN-023 fix correctly resets DRAINING flag on exception (L2594), but task ref not stored. | Same fix: store task ref in a module-level set with done-callback discard. |

### MEDIUM severity

| # | file:line | severity | type | evidence | fix_hint |
|---|-----------|----------|------|----------|----------|
| M1 | orchestrator_server.py:4438-4443 | MEDIUM | resource_leak | Timeout path: `_council_kill_process_tree(proc.pid)` then `proc.wait(timeout=5)` wrapped in `except Exception: pass`. If kill fails (e.g. PID recycled), zombie persists and wait failure is silent. | Log wait/kill failure at warning; if kill returns nonzero, escalate (log PID for manual cleanup). |
| M2 | orchestrator_server.py (7 locks: L128, L2202, L2204, L2207, L3655, L4575, L4232) | MEDIUM | race_condition | 7 threading.Lock/RLock used in aiohttp async server. Pattern is correct ONLY if no `await` inside `with _LOCK:` block. Holding threading.Lock blocks event loop. Fragile — any future edit adding await inside a lock block freezes the server. | Audit each `with _LOCK:` for await; if await needed, switch to asyncio.Lock. Add lint rule / comment guard at each lock site. |
| M3 | .pytest_tmp_graphfix_full/test_xroot_outside_write_allow0/BR_like/artifact.txt | MEDIUM | test_cleanup | Stale test temp dir with restrictive ACLs (WinError 5) blocks search_code callers scan project-wide. Test xroot_outside_write_allow0 left artifact.txt with denied access. | Add teardown/fixture cleanup; or `icacls` reset on .pytest_tmp_*; add conftest cleanup. |
| M4 | orchestrator_server.py:2589-2597 | MEDIUM | error_handling | CAN-023 fix is sound (broad except + re-raise after flag reset). But re-raise propagates to caller — if caller is an async handler without try/except, 500 returned. Verify all callers of _council_schedule_queue_drain handle the re-raise. | Audit callers; ensure async handlers catch and return structured error. |

### LOW — Error-handling swallows (`except Exception: pass`, select most concerning)

| # | file:line | severity | type | evidence | fix_hint |
|---|-----------|----------|------|----------|----------|
| L1 | orchestrator_server.py:120 | LOW | error_swallow | `except Exception: pass` — early init cleanup failure silent. | logger.debug at minimum. |
| L2 | orchestrator_server.py:212,218 | LOW | error_swallow | request logging / session event logging failure swallowed. | logger.debug; logging infra failure should not crash request. |
| L3 | orchestrator_server.py:222,246 | LOW | error_swallow | `_TOKEN_FILE.write_text` failure swallowed — token write failure could cause auth issues downstream with no diagnostic. | logger.warning; surface in /health. |
| L4 | orchestrator_server.py:382 | LOW | error_swallow | Retry-After header computation swallowed — 429 response may lack Retry-After. | log; fallback to static retry value. |
| L5 | orchestrator_server.py:452 | LOW | error_swallow | DB row[0] access swallowed — silent data loss on DB schema mismatch. | log; return sentinel. |
| L6 | orchestrator_server.py:475 | LOW | error_swallow | CAN-045 already tracked: tools_v15 import/lookup error masking. | in progress per KNOWN_BUGS. |
| L7 | orchestrator_server.py:735 | LOW | error_swallow | `os.path.relpath` failure swallowed — path field silently left as absolute. | log; keep absolute with warning. |
| L8 | orchestrator_server.py:2464,2475 | LOW | error_swallow | PID liveness check: `except Exception: pass` → returns True (assume alive). Documented intent but masks real errors. | narrow except to (ValueError, OSError). |
| L9 | orchestrator_server.py:2987,3005,3032,3283,3289 | LOW | error_swallow | 5× stat/glob failure → continue/empty. Council log file scanning silently degrades. | logger.debug per failure. |
| L10 | orchestrator_server.py:7780,7795 | LOW | error_swallow | `_error_response` called after `except Exception:` — tool dispatch errors generically wrapped, original traceback lost. | log original exception before calling _error_response. |

### LOW — Dead code (P2-specific, from cbm_dead_code vulture+ruff)

| # | file:line | severity | type | evidence | fix_hint |
|---|-----------|----------|------|----------|----------|
| D1 | orchestrator/server/mcp_stdio.py:72 | LOW | dead_import | unused import `time` (ruff F401 + vulture 0.9) | remove import |
| D2 | orchestrator/server/mcp_stdio.py:123 | LOW | dead_code | unused function `run_mcp_stdio` (vulture 0.6) | verify callers (may be entrypoint); remove if unused |
| D3 | orchestrator/server/mcp_stdio.py:199 | LOW | dead_code | unused function `tool_names` (vulture 0.6) | verify; remove if unused |
| D4 | orchestrator/session/store.py:5 | LOW | dead_import | unused import `json` (ruff F401) | remove import |
| D5 | orchestrator/runtime/desktop_dialog_controls.py:48 | LOW | dead_import | unused import `win32gui` (ruff F401) | remove import |
| D6 | orchestrator/runtime/desktop_dialogs.py:366 | LOW | dead_import | unused import `win32con`/`_wc` (ruff F401 + vulture 0.9) | remove import |
| D7 | orchestrator/mission/store.py:16 | LOW | dead_import | unused import `json` (ruff F401) | remove import |
| D8 | orchestrator/mission/store.py:197 | LOW | dead_var | unused var `now` (ruff F841) | remove assignment |
| D9 | orchestrator/mission/store.py:338 | LOW | dead_code | unused function `ack_outbox` (vulture 0.6) | verify; remove if unused |
| D10 | orchestrator/mission/store.py:359 | LOW | dead_code | unused function `get_schema_version` (vulture 0.6) | verify; remove if unused |
| D11 | orchestrator/council/dashboard.py:23 | LOW | dead_var | unused var `generated_at` (ruff F841) | remove assignment |
| D12 | orchestrator/council/dashboard.py:20 | LOW | dead_code | unused function `render_council_dashboard` (vulture 0.6) | verify; remove if unused |
| D13 | orchestrator/core/errors.py:64-210 | LOW | dead_class | ~30 unused exception classes (vulture 0.6) — NotFoundError, UnsupportedField, MissingRequiredField, InvalidToolName, ManifestDrift, PathOutsideProject, FileChangedOutsideOrchestrator, BinaryFileRejected, InvalidEncoding, MutationTokenRequired, OriginNotAllowed, SessionRequired, PatchApplyFailed, ValidationFailed, BackupFailed, AtomicWriteFailed, CommandPolicyViolation, CommandTimeout, CdpAttachFailed, DesktopWindowNotFound, UipiBlocked, InvalidAgent, AgentDisabled, CouncilModeNotSupported, WorkerTimeout, WorkerUnavailable, BrokerAppendFailed, ReplyParseFailed, JsonStoreCorrupt, SqliteUnavailable — may be used via dynamic/string refs | cross-check with find_callers/find_symbol before removing; likely some are registered error_names |

### FALSE POSITIVES (flagged but likely NOT dead)

| # | file:line | note |
|---|-----------|------|
| FP1 | orchestrator/mission/tools.py:230-529 | mission_create, mission_next, mission_dispatch, mission_report, mission_status, mission_gate, leader_bootstrap — vulture 0.6 "unused" but these are HTTP tool endpoints registered via string lookup in registry_data.py. NOT dead. |
| FP2 | orchestrator/core/registry_data.py:392-1428 | BASE_TOOL_MAP_SPECS, V15_NEW_TOOL_SPECS, V15_TIERS, build_tool_schemas, build_tool_map, build_tool_phases — vulture 0.6 but likely consumed by dynamic registry builder. Verify before removing. |
| FP3 | orchestrator/mission/instance_lock.py:52,141 | acquire/release_instance_lock — may be called from mission tools via string dispatch. Verify. |
| FP4 | orchestrator/mission/reaper.py:331 | start_reaper_task — may be called at server startup. Verify. |

---

## 3. Async function map (orchestrator_server.py, 30 functions)

Key async handlers with concurrency implications:
- `_council_queue_drain` (L2723) — drains per-agent queue under `_COUNCIL_AGENT_QUEUE_LOCK`; runs as fire-and-forget task
- `_council_enqueue_or_run` (L2777) — enqueues or runs dispatch; holds queue lock
- `council_message` (L6564) — main broker dispatch entry
- `council_worker_model` (L6723) — GET/SET worker model under `_COUNCIL_WORKER_MODELS_LOCK` (RLock)
- `_toolq_post` (L6884) — ToolQ librarian HTTP call
- `restart_orchestrator_via_bat` (L6844) — restart (T4c)
- `_http_defense_middleware` (L338) — request middleware (runs on every request)

---

## 4. Summary

| Category | HIGH | MEDIUM | LOW | Total |
|----------|------|--------|-----|-------|
| resource_leak | 1 (H1) | 1 (M1) | — | 2 |
| silent_failure | 1 (H2) | — | — | 1 |
| race/gc_leak | 2 (H3,H4) | — | — | 2 |
| race_condition | — | 1 (M2) | — | 1 |
| error_swallow | — | 1 (M4) | 10 (L1-L10) | 11 |
| test_cleanup | — | 1 (M3) | — | 1 |
| dead_code/import | — | — | 13 (D1-D13) | 13 |
| false_positive | — | — | — | 4 (FP1-FP4) |
| **Total** | **4** | **4** | **23** | **35** |

**Top 3 action items:**
1. **H1**: Add try/finally around Popen at L4422 — zombie process + FD leak on exception path.
2. **H2+H3**: Fix `_bg()` silent swallow + store create_task ref — dispatches silently lost.
3. **M2**: Audit 7 threading.Lock sites for `await` inside — event-loop freeze risk.

END_SCAN2_OPEC_R1_P2
