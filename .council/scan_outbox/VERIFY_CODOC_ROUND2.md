## Tool Re-verification
| Tool | Round2 reported | CodoC re-test | Match? |
|---|---|---|---|
| T1 cbm_dead_code | ok + count | ok=true, count=10, engine=vulture+ruff | YES |
| T2 ast_query | ok + count | ok=true, count=0 for try_bare_except in orchestrator_server.py | YES |
| T3 search_code callers | WinError 5 on .pytest_tmp_graphfix_full | reproduced: ok=false, 500 TOOL_ERROR, `[WinError 5] Access is denied ... .pytest_tmp_graphfix_full\\test_xroot_outside_write_allow0\\BR_like\\artifact.txt` | YES |
| T4 cbm_template_query | ok + count | ok=true, count=10, total_count=134, truncated=true | YES |

## Finding Verdicts
| ID | file:line | Round2 claim | CodoC verdict (APPROVE/DENY) | evidence |
|---|---|---|---|---|
| H1 | orchestrator_server.py:4422-4437 | Popen then `_council_communicate_idle` with no try/finally can orphan proc/pipes if communicate raises | APPROVE | Lines 4422-4437 create `subprocess.Popen(...)`, then call `_council_communicate_idle(...)`; no enclosing try/finally is present before timeout handling starts at 4438. |
| H2 | orchestrator_server.py:6525-6535 | `_bg()` catches all dispatch errors and caller returns ok:true | APPROVE | Lines 6525-6534 define `_bg`, await `_council_enqueue_or_run(...)`, then `except Exception: pass`; lines 6535-6538 immediately create task and return `{"ok": True, "dispatched": True}`. |
| H3 | orchestrator_server.py:6535 | `asyncio.create_task(_bg())` task is not stored in ref set | APPROVE | Line 6535 is a bare `asyncio.create_task(_bg())`; no variable assignment or task registry is used in the cited code. |
| H4 | orchestrator_server.py:2588 | `asyncio.create_task(_council_queue_drain(agent))` task not stored | APPROVE | Line 2588 is a bare `asyncio.create_task(_council_queue_drain(agent))`; the surrounding code only resets `_COUNCIL_AGENT_QUEUE_DRAINING` if scheduling raises. |
| H5 | search_code tool | WinError 5 Access Denied on `.pytest_tmp_graphfix_full` blocks callers-mode search | APPROVE | Re-test T3 returned ok=false with `[WinError 5] Access is denied` under `.pytest_tmp_graphfix_full\\test_xroot_outside_write_allow0\\BR_like\\artifact.txt`. |
| M1 | code_tools.py:44-61 | `_shared_inplace_write_bytes` catches only PermissionError; other exceptions exit retry silently | DENY | It catches only `PermissionError` at lines 56-58, but other exceptions are not swallowed; they propagate out of the `try`. The claim's `silently` part is inaccurate. |
| M2 | code_tools.py:1617-1638 | `update_workflow_state` read-modify-write is last-writer-wins | APPROVE | Lines 1617-1638 read JSON, mutate `state`, then `_atomic_write_json`; comments at 1618-1624 explicitly document `LAST-WRITER-WINS`. |
| M3 | code_tools.py:500-508 | `_update_cache_after_write` has silent `except Exception: pass` | APPROVE | Lines 500-507 retry mtime verification and catch `except Exception: pass`; no logging or structured error is emitted in that inner handler. |
| M4 | orchestrator_server.py:4438-4443 | timeout path kill+wait wraps wait failure in `except Exception: pass` | APPROVE | Lines 4438-4443 call `_council_kill_process_tree(proc.pid)`, then `proc.wait(timeout=5)` inside `try`, followed by `except Exception: pass`. |
| M5 | orchestrator_server.py:128,2202,2204,2207,3655,4232,4575 | `threading.Lock`/`RLock` in aiohttp async areas is fragile if await is introduced inside | APPROVE | The cited lock objects exist. Usage spot-checks show locks inside async paths: `_council_queue_drain` uses `_COUNCIL_AGENT_QUEUE_LOCK` in async def at 2723-2738, and `handle_council_worker_model` awaits request JSON at 6111 then uses `_COUNCIL_WORKER_MODELS_LOCK` at 6139-6143. No await was seen inside the sampled lock blocks, so the current evidence supports fragility/blocking risk, not an existing await-inside-lock deadlock. |
| M6 | `.pytest_tmp_graphfix_full` | stale test temp dir blocks `search_code` | APPROVE | Same as H5/T3: callers-mode `search_code` fails before returning results due Access Denied inside `.pytest_tmp_graphfix_full`, proving the stale temp tree blocks that search. |
| M7 | orchestrator_server.py:2589-2597 | CAN-023 re-raise propagates; callers need handling | APPROVE | Lines 2589-2597 reset draining flag, log, then `raise`. Caller at 2857 invokes `_council_schedule_queue_drain(runtime_key)` before the `try` beginning at 2858, so a scheduling exception propagates out. |
| M8 | orphan symbols in fileops/common.py, graph/config.py, council/dashboard.py, core/errors.py, graph/indexer.py, core/interfaces.py | 22 orphan symbols: 8+2+1+1+2+8 | DENY | `cbm_template_query(limit=200)` returned matching file counts 8,2,1,1,2,7 = 21, not 22. The `core/interfaces.py` count is 7, not 8. The dead-code direction is plausible, but the reported count is inaccurate. |

## Summary
- HIGH approved: 5/5
- MEDIUM approved: 6/8
- Tool stability confirmed: 4/4 re-test outcomes matched; 3/4 tools returned ok, T3 reproduced the reported WinError 5 failure
- Overall scan quality: NEEDS_REVISION

END_VERIFY_CODOC_ROUND2