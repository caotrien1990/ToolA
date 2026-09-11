# TQ11-C2R2 VERIFY — READ-ONLY after C1R2 test rewrite

WORKER: ClauC (same-identity; sender already set by leader)
TASK_ID: TQ11-C2R2
OBJECTIVE_CLASS: scoped-audit-verify-qsleep-lease-tests
RISK_LEVEL: mid
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 3
FILES_SUBSYSTEMS: 1
EXPECTED_TOOL_CALLS: 12
EXPECTED_WRITES: 1
DEPENDENCY_DEPTH: 1
EXPECTED_END_MARKER: END_ClauC_R2_TQ11-C2

If this task must write outside cwd, bootstrap [A] and use [A] write tools.

## MODE
READ-ONLY. Artifact write is the only allowed write. Do NOT patch source or tests.

## REPO
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ

## EXCLUSIVE ARTIFACT
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ11-C2R2_clauc_verify.md
Last line exactly:
END_ClauC_R2_TQ11-C2

## INPUTS (evidence, not truth)
- C1R2 artifact: ...\\DATA\\TQ11-C1R2_clauc_fix.md
- Prior C2: ...\\DATA\\TQ11-C2_clauc_verify.md (NOT_CLEAN test-depth)
- Tests: tests/test_tq11_ds07_callers.py (rewritten)

## VERIFY
1. Re-read test_tq11_ds07_callers.py. Confirm the two JobContext tests now CALL `job.run_clusters(...)` — not a copied finally. A revert of clm_qnight_common.py L784 dropping run_id MUST fail those tests.
2. Re-read L784 still has run_id=self._lease_rid. lease_orchestrator.py:147 still has run_id=run_id.
3. Run isolated suite via [A] run_project_test_script wrapping pytest. PYTHONPATH=ToolQ/bin + qsleep/ledger + qsleep/controller + qsleep/bin.
4. Known PARTIAL still allowed: DS-05 hash/version; DS-07 loop on_tick. qwake/qpause unfenced rollback = INTENTIONAL.
5. Official P5 = PID 51276. Do not COUNT rag/vec. Do not reopen K09.

## VERDICT
CLEAN = no CONFIRMED leftover bug in scope (test-depth of the two run_clusters tests is real; source still fenced).
NOT_CLEAN = tests still tautology / do not call run_clusters / would stay green if L784 dropped run_id / new leftover owner-path.
If NOT_CLEAN list exact leftover file:line. Do not patch.

## HARD DO-NOT
KHONG rewrite qnight_state. KHONG kill PID. KHONG xoa .bak. KHONG edit embed.py. KHONG restart :8082. KHONG scoop leftover. KHONG Phase 1. KHONG patch.
