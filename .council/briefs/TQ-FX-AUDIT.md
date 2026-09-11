# TQ-FX-AUDIT — FIX until tests PASS

You are ClauC. Leader = desktop Clau. Model already pinned; do not name models.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.
Council workers: HTTP ONLY to localhost:8083. First call get_rule_core_text agent_id=clau.

TASK_ID: TQ-FX-AUDIT
OBJECTIVE_CLASS: fix N31-01 + R31-02 + R31-01 then isolated test+validate until PASS
RISK_LEVEL: high
DEV_LEVEL: high
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 3
FILES_SUBSYSTEMS: 1
EXPECTED_TOOL_CALLS: 30
EXPECTED_WRITES: 2
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_CLAUC_FX_TQ-FX-AUDIT

## Exclusive write ownership (ONLY these)
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\qsleep\\controller\\loop_orchestrator.py
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\tests\\test_tq10_ds010307.py  (update HERMES_BASE assert if local dispatch changes)
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\qsleep\\tests\\test_front_tier_context_and_empty_terminal.py  (only if needed to keep empty-terminal persist behavior)
- New isolated test file if required: tests/test_n31_01_r31_02_r31_01.py

Do NOT write any other ToolQ source. Do NOT touch jobs.py, qwake.py, kick_qsleep.py, lease_manager.py, routes_write.py, adjudication*, queue_builder.py, clm_qnight_runner.py, embed.py, gitignore.

## Fix contract
### N31-01
step_qdreamer1 recomputes audit in RAM then `_update_meta` + return; no `_write_json_atomic`. Also synthesized missing-entry lacks source_ids_grounded/evidence_matches so validate_audit rejects.
Must: complete schema on synthesized entries; combine semantic+deterministic per existing policy (do NOT revive semantic-fail→pass); validate; atomic persist; read-back. Only then may later steps see disk verdict.
Witness: test_empty_candidate_is_terminal_audit_clean currently FAIL.

### R31-02
`_resume_artifacts` accepts marker.exists + overall=pass. Bind marker/candidate/audit version + source/policy hash. Stale v999 over v001 must reject.

### R31-01
local/hermes/empty still HERMES_BASE+Popen (L30, L368-389). Do NOT paper over by flipping config to api. Route local through actual HTTP dispatch used by `_run_api_model` (3 phases). Update test_tq10_ds010307.py so it asserts the new local HTTP path, not leftover HERMES_BASE outside the gate.

## Method (loop until 100%)
1. open_session ToolQ. read_file_range the exclusive files (need write token).
2. tool_decision_hint if unsure of test runner.
3. write_fix only exclusive files. After EACH write: validate_file (or validate checks=file).
4. Isolated tests via run_project_test_script (NOT full suite):
   - qsleep/tests/test_front_tier_context_and_empty_terminal.py::test_empty_candidate_is_terminal_audit_clean
   - tests/test_tq10_ds010307.py
   - any new isolated test you add
   - optional: run Codo residual cases that only need loop_orchestrator (do not mutate live DB)
5. If fail: search_code / read_file_range / scan the failure, fix again. Max 4 loops. Then report remaining FAIL honestly.
6. Do not rewrite failing expectations to hide the bug.

## Hard bans
No qnight_state.json. No kill PID. No delete/.bak. No HF cache copy. No leftover dirty scoop. No Phase 1 schema. No embed.py / clm_semantic_embed.py. No spawn embed. No restart :8082. No git commit. No git reset.

## Evidence
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo_verify_3108\\DATA\\TQ-CV-AUDIT.md
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\41. CLAUDELTNAI\\FIX_TOOLQ_2508\\AUDIT_DEEP_CODO_3108.md
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\41. CLAUDELTNAI\\FIX_TOOLQ_2508\\repro_3108\\codo_deep_3108_patch_residual_26ab486cd2bd54f2.py

## Exclusive artifact
Write UTF-8 to EXACTLY:
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo_fix_3108\\DATA\\TQ-FX-AUDIT.md

```
# TQ-FX-AUDIT
N31-01: FIXED|PARTIAL|FAIL  tests: ...  validate: ...
R31-02: FIXED|PARTIAL|FAIL  tests: ...  validate: ...
R31-01: FIXED|PARTIAL|FAIL  tests: ...  validate: ...
files_changed:
loops: N
END_CLAUC_FX_TQ-FX-AUDIT
```
