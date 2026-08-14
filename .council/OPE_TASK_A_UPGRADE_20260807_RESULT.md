# OPE TASK RESULT — [A] Upgrade items 3+4 + restart-guard fix (2026-08-07)

TASK_ID: A_UPG_P234_20260807 | Worker: OpeC
Full detail mirrored to .council/worker_output/20260807_081615_ope_d7440c31.md

## ST1 — Restart-guard COUNCIL_PID_ALIVE false-positive (PASS)
Root cause: `orchestrator/lifecycle/restart_guard.py::evaluate_restart_guard` called
`pid_alive()` on EVERY slot in `.council/runtime_state.json` regardless of state. Each
slot `pid` is `os.getpid()` at dispatch (orchestrator_server.py:4203) = the ORCHESTRATOR's
own PID, always alive while [A] runs → permanent false `COUNCIL_PID_ALIVE` even with no
live worker (65 completed / 21 error / 5 stopped historical slots).
Fix: PID-liveness check now applies ONLY to ACTIVE slots (running|starting|queued) and
prefers `worker_pid` (real worker) over `pid` (launcher). Terminal slots never block.
Backups: restart_guard.py.bak_ope_20260807, restart_control_service.py.bak_ope_20260807.
Verified: no-worker→allowed; live-worker→blocked; stale-running→state block only.
tests/test_restart_control_core.py + test_restart_helper_contract.py: 33 passed.

## ST2 — Item 3 mutating tools (PASS)
tools_upgrade_p12.py (T2, file_artifact): insert_text, replace_symbol, move_file,
copy_range, cut_range. Registered in code_tools.py UP12 block (TOOL_MAP /
TOOL_PHASES=T2 / TOOL_SCHEMAS / PROTECTED_TOOL_NAMES / _MUTATION_SCOPE_MAP).
py_compile clean. %TEMP% self-tests all PASS (incl. dry-run no-write, symbol rejection,
base64).

## ST3 — Item 4 app/runtime tools (PASS)
tools_upgrade_p5.py (T2): start_app, stop_app, http_check, browser_smoke_test. Reuses
run_project_command keep_alive + kill_process + tools_v15 Playwright/CDP. Registered in
code_tools.py UP5 block (start/stop_app protected, process scope). py_compile clean.
Self-tests: http_check :8083/health=200 + text_contains + mismatch reject;
browser_smoke_test :8083/health ok; stop_app reject.

## ST4 — Activate + verify (REGISTRATION PASS / LIVE RESTART PENDING LEADER)
- Matrix audit (fresh import): code_tools=226, schema_tools=226, ZERO "in TOOL_MAP but no
  schema"; drift_count=13 all inventory_outdated (doc reference) incl. item-1 (pre-existing).
- All item1+3+4 tools in TOOL_MAP+TOOL_SCHEMAS+TOOL_PHASES (item-1 untouched).
- Restart via POST /api/call/restart_orchestrator_via_bat → RESTART_DRAIN_REQUIRED
  (executor_in_flight=1 = this dispatch). Guard also blocks while OpeC worker alive —
  the exact ST1 acceptance "still blocks with a live worker". CORE_RULE forbids restart
  while a worker is in-flight.
- Activation restart + live /api/call + /api/tools/<tool>/schema verification: leader
  action AFTER this dispatch completes (no live worker → guard allows). Steps in
  worker_output artifact.

## Constraints honored
No file deleted. No dependency added. No architecture/transport contract change.
Backups *.bak_ope_20260807. py_compile clean before any restart. Item-1 not modified.

END_OPE_A_UPG_P234
