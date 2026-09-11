# TQ11-C2 VERIFY — READ-ONLY scan after C1 lease-fence fix

WORKER: ClauC (same-identity; sender already set by leader)
TASK_ID: TQ11-C2
OBJECTIVE_CLASS: scoped-audit-verify-qsleep-lease-fence
RISK_LEVEL: mid
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 4
FILES_SUBSYSTEMS: 2
EXPECTED_TOOL_CALLS: 16
EXPECTED_WRITES: 1
DEPENDENCY_DEPTH: 1
EXPECTED_END_MARKER: END_ClauC_R1_TQ11-C2

If this task must write outside cwd, bootstrap [A] and use [A] write tools.

## MODE
READ-ONLY. No ToolQ source edit. Artifact write is the only allowed write.
Do NOT patch. Do NOT restart :8082. Do NOT kill PIDs. Do NOT rewrite qnight_state.
Do NOT scoop leftover. Do NOT implement Phase 1. Do NOT edit embed.py.

## REPO
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ

## EXCLUSIVE ARTIFACT (mandatory)
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ11-C2_clauc_verify.md
Overwrite ok. Last line MUST be exactly:
END_ClauC_R1_TQ11-C2

## INPUTS (read, do not trust as source of truth)
- C1 artifact: D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ11-C1_clauc_fix.md
- Brief C1: D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\briefs\\TQ11-C1_fix.md
- Council room: D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\rooms\\room-20260830-toolq-ask-hold\\
- Codo campaign docs are STALE (21:23-21:25, pid 32232+51968, DS OPEN). Report drift only. Do not rewrite.
- Isolated tests: tests/test_tq10_ds06.py + ds0811 + ds0910 + ds010307 + any test_tq11_*.py C1 added

## VERIFY AGAINST CURRENT WORKTREE (independent)
1) lease_orchestrator.py release MUST pass run_id. Confirm by reading source, not C1 text.
2) clm_qnight_common.py JobContext finally MUST forward owner run_id / _lease_rid if lease is LeaseManager. Confirm signature match.
3) Grep all LeaseManager.release / rollback_pending callers. Classify each:
   - FENCED (passes run_id)
   - INTENTIONAL unfenced (qwake recover/closeout reclaim/shutdown)
   - LEFTOVER BUG (unfenced on a live owner path)
4) Tests: are they behavior (tmp_path / mock) or AST/`in src` only? Run isolated suite if cheap via [A] run_project_test_script wrapping pytest. PYTHONPATH=ToolQ/bin + qsleep/ledger + qsleep/controller.
5) Room drift vs live: official P5 is PID 51276; 32232/51968 DEAD; :8082 is 13896. Do not COUNT full rag/vec while 51276 writes.
6) Known PARTIAL still allowed if still true: DS-05 hash/version marker fence; DS-07 loop_until_clean on_tick renew (JobContext.heartbeat already renews p4/p5).
7) Do NOT reopen K01-K09 except if you find a source regression vs TQ9. K09 is FIXED (C1-of-TQ10 wrongly said STILL_OPEN).

## VERDICT RULES
- CONFIRMED leftover bug = a live owner path still releases/rollbacks without run_id, OR a shipped DS-01..11 patch is missing/regressed, OR tests are green but do not exercise the claim.
- CLEAN = no CONFIRMED leftover bug in scope. Known PARTIAL DS-05 hash + DS-07 on_tick may remain OPEN if documented.
- AST-only tests for a claimed behavior fix = CONFIRMED issue (test-depth), not CLEAN.
- Worker text is evidence, not truth. Cite file:line from current worktree.

## ARTIFACT SECTIONS
1. Live snapshot cheap (flag embed.running + process 51276/13896 only; no full COUNT)
2. Table of release/rollback callers with verdict FENCED / INTENTIONAL / LEFTOVER
3. DS-01..11 current status vs source (SHIPPED / PARTIAL / REGRESSED)
4. Test depth + result if run
5. Room drift (report only)
6. CLEAN or NOT_CLEAN. If NOT_CLEAN, list exact leftover for next C1 fix (file:line + needed change). Do not patch.
Last line: END_ClauC_R1_TQ11-C2

## HARD DO-NOT
KHONG rewrite qnight_state.json. KHONG kill PID. KHONG xoa .bak. KHONG copy HF cache. KHONG scoop leftover. KHONG implement Phase 1. KHONG edit embed.py. KHONG restart :8082. KHONG patch source.
