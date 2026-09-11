# TQ11-C1R2 FIX — thicken JobContext run_clusters tests only

WORKER: ClauC (same-identity; sender already set by leader)
TASK_ID: TQ11-C1R2
OBJECTIVE_CLASS: scoped-test-fix-qsleep-lease-fence
RISK_LEVEL: low
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 1
FILES_SUBSYSTEMS: 1
EXPECTED_TOOL_CALLS: 10
EXPECTED_WRITES: 2
DEPENDENCY_DEPTH: 1
EXPECTED_END_MARKER: END_ClauC_R2_TQ11-C1

If this task must write outside cwd, bootstrap [A] and use [A] write tools.

## MODE
MUTATE authorized (Anh: tien hanh). Sticky MUTATE, NOT destructive.
Preferred write: tests only. Do NOT change production source unless a test cannot be made real without a tiny test-hook (prefer no hook).

## REPO
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ

## EXCLUSIVE ARTIFACT (mandatory)
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ11-C1R2_clauc_fix.md
Overwrite ok. Last line MUST be exactly:
END_ClauC_R2_TQ11-C1

## HARD DO-NOT
- KHONG rewrite qnight_state.json
- KHONG kill PID (ke ca 51276 / 13896)
- KHONG xoa .bak
- KHONG edit clm_semantic_embed.py
- KHONG restart :8082
- KHONG scoop leftover / Phase 1
- KHONG ruff_fix pre-existing lint
- KHONG doi qwake / recover / handle_ops_qpause rollback-without-run_id (INTENTIONAL)
- KHONG reopen DS-05 hash or DS-07 on_tick unless a one-line cheap fix is already in the same test file
- KHONG delete anything

## WHY THIS ROUND
C2 (grok-4.6-build) independently verified source FENCED:
- lease_orchestrator.py:147 release(run_id=run_id)
- clm_qnight_common.py:784 release(..., run_id=self._lease_rid)
- kick already fenced
- qwake L142/151/232 + recover L171 + routes_write handle_ops_qpause L1432 = INTENTIONAL

NOT_CLEAN leftover is TEST-DEPTH only:
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\tests\\test_tq11_ds07_callers.py
- L88-120 test_tq11_jobcontext_release_forwards_lease_rid copies the finally block; does NOT call QNightJob.run_clusters. Revert L784 and this test still passes.
- L123-142 test_tq11_jobcontext_release_forwards_rid_failure same tautology for failed.

Leader independently read the file and CONFIRMS C2.

## MUST FIX
Rewrite those two tests so they CALL `QNightJob.run_clusters` (or the real method that owns L784).
Acceptance:
1. Construct a QNightJob (or load via importlib) with a MagicMock/Spy lease.
2. acquire returns True; process_fn returns ([], {}) for done path.
3. After run_clusters returns, assert lease.release was called with run_id=job._lease_rid (or the rid passed into acquire).
4. Failed path: process_fn raises OR returns stats with cluster_failed set; assert release(..., "failed", run_id=...).
5. A revert of L784 `run_id=self._lease_rid` MUST make at least one of these tests FAIL. Prove that in the artifact (mentally / by reading call site, not by actually reverting production).
6. Keep the two process_cluster behavior tests. Keep or drop the AST source grep test — optional.
7. Isolated suite must stay green:
   tests/test_tq10_ds06.py tests/test_tq10_ds0811.py tests/test_tq10_ds0910.py tests/test_tq10_ds010307.py tests/test_tq11_ds07_callers.py
   PYTHONPATH = ToolQ/bin + qsleep/ledger + qsleep/controller + qsleep/bin
   Use [A] run_project_test_script wrapping pytest.

## HINTS (read source, do not invent)
- QNightJob.lease is LeaseManager (clm_qnight_common.py ~L482)
- run_clusters sets self._lease_key / self._lease_rid then acquire(lease_key, rid, ttl=...)
- finally: self.lease.release(lease_key, lease_outcome, run_id=self._lease_rid)
- You will need enough QNightJob fields for run_clusters to enter the loop (job, queue, log, set_progress, cap_reached, processed_path, final_items, clusters list, process_fn, lease). Read the function before writing the test.
- Do not start a real model / HTTP / :8082.

## ARTIFACT SECTIONS
1. What you changed (full Windows paths)
2. Why the new tests would fail if L784 dropped run_id
3. Test command + pass count
4. Anything still OPEN
Last line: END_ClauC_R2_TQ11-C1

## STOP
After tests + artifact END marker, STOP. Leader will dispatch C2R2 grok verify.
