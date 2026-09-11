# TQ11-C1 FIX — leftover DS-07 release fence + thicker tests

WORKER: ClauC (same-identity; sender already set by leader)
TASK_ID: TQ11-C1
OBJECTIVE_CLASS: scoped-bug-fix-qsleep-lease-fence
RISK_LEVEL: mid
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 3
FILES_SUBSYSTEMS: 2
EXPECTED_TOOL_CALLS: 18
EXPECTED_WRITES: 4
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_ClauC_R1_TQ11-C1

If this task must write outside cwd, bootstrap [A] and use [A] write tools.

## MODE
MUTATE authorized (Anh: tien hanh). Sticky MUTATE, NOT destructive.
You MAY edit ToolQ source + tests listed below via [A] write_fix / write_new_file.
You MUST write the exclusive artifact even if you decide a caller is intentional.

## REPO
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ

## EXCLUSIVE ARTIFACT (mandatory)
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ11-C1_clauc_fix.md
Overwrite ok. Last line MUST be exactly:
END_ClauC_R1_TQ11-C1

## HARD DO-NOT
- KHONG rewrite qnight_state.json
- KHONG kill PID (ke ca 51276 / 13896 / 44868 / 36192 / 40528)
- KHONG xoa .bak
- KHONG copy HF cache
- KHONG scoop leftover dirty (store.py / vector.py / q_agent)
- KHONG implement Phase 1 schema
- KHONG edit clm_semantic_embed.py (P5 PID 51276 LIVE)
- KHONG restart :8082
- KHONG ruff_fix pre-existing lint tren runner / routes_write / kick / loop
- KHONG doi qwake.py / recover rollback_pending(cid) khong run_id — INTENTIONAL reclaim/shutdown
- KHONG dispatch worker khac
- KHONG delete anything

## ALREADY SHIPPED (do not reopen unless you prove a regression)
- DS-01: loop_orchestrator._run_hermes skips CLI/ultra when QSLEEP_DISTILL_BACKEND in (local, hermes, empty)
- DS-02: missing findings_audit coverage -> overall=needs_fix / incomplete_findings_audit
- DS-03: semantic_audit_status preserved before deterministic overwrite
- DS-04: routes_write score path uses point then excerpt
- DS-05 PARTIAL: _resume_artifacts requires overall==pass; hash/version marker fence still OPEN (optional if cheap)
- DS-06: qsleep_core write_lock O_EXCL + remove_lock owner_pid
- DS-07: lease_manager.release / rollback_pending accept run_id; kick_qsleep acquire+renew+release/rollback pass scope[run_id]
- DS-08: runner pause skips review_ingest + apply_all POST
- DS-09: adjudication ok only if raw.ok is True; HTTP non-2xx raises
- DS-10: drained requires deferred_count==0; selector deferred_raw_total
- DS-11: apply checks deleted_at empty
- Isolated tests 19 passed: tests/test_tq10_ds06.py + ds0811 + ds0910 + ds010307
- K01 K04 DEFER_EMBED_LIVE; K02 K03 K05-K09 FIXED — do not reopen K09

## MUST FIX (confirmed leftover after leader process-audit)
1) qsleep/controller/lease_orchestrator.py ~L147
   NOW: mgr.release(cluster_id, status="done")
   NEED: pass run_id=run_id (already in scope; acquire used it at L130)

2) qsleep/bin/clm_qnight_common.py ~L784 JobContext.run_clusters finally
   NOW: self.lease.release(lease_key, lease_outcome)
   NEED: pass run_id if LeaseManager.release accepts it.
   Heartbeat already renews with self._lease_rid (L611-613).
   After acquire at L742, self._lease_rid = rid.
   Confirm LeaseManager.release signature before calling:
   release(self, cluster_id, status="done", run_id=None) -> bool
   If JobContext.lease is NOT LeaseManager (wrapper), read the wrapper and pass the owner token the same way renew does.
   Do NOT change qwake recover rollback-without-run_id.

## OPTIONAL IF CHEAP (same files only)
- DS-05 hash/version marker fence in loop_orchestrator step_qdreamer2 / _resume_artifacts
- DS-07 loop_until_clean on_tick renew (JobContext.heartbeat already renews p4/p5; only add if you find a live kick/loop path that holds a lease without renew)
If not cheap: document OPEN in the artifact, do not expand scope.

## TESTS (required)
Thicken behavior tests — NOT AST-only / `in src`.
Preferred: extend tests/test_tq10_ds010307.py OR add tests/test_tq11_ds07_callers.py.
Must cover:
- lease_orchestrator process_one (or equivalent) release refuses foreign run_id / passes own run_id (tmp_path)
- JobContext / common release path actually forwards run_id (import + mock LeaseManager, or tmp_path if constructable without starting a job)
Keep existing 19 tests green. PYTHONPATH must include ToolQ/bin + qsleep/ledger + qsleep/controller.
Use [A] run_project_test_script wrapping pytest. Do not invent run_project_pytest.

## WRITE RULES ([A])
- Native grep/read = inspection OK.
- write_fix needs open_session on ToolQ + read_file_range token.
- Sequential same-file writes: pass expected_hash from prior new_hash.
- Multi-hunk same file: write_transaction bottom-up OR sequential expected_hash. NEVER parallel write_fix on one file.
- SYNTAX_ERROR_PRE_WRITE -> write_draft_abort (do not write_draft_patch a corrupted overlap).
- write_fix creates .bak — do not delete.
- New test file: write_new_file.

## ARTIFACT SECTIONS (required)
1. What you changed (full Windows paths + before/after 1-liner)
2. What you left OPEN and why
3. Test command + result (pass count)
4. Callers of LeaseManager.release / rollback_pending still missing run_id (grep after patch). qwake recover without run_id = INTENTIONAL, list it as such.
5. Limits / unknown
Last line: END_ClauC_R1_TQ11-C1

## STOP
After tests + artifact with END marker, STOP. Do not start a verify pass. Leader will dispatch TQ11-C2.
