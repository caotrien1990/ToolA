# TQ-FX-LINEAGE — FIX until tests PASS

You are ClauC. Leader = desktop Clau. Model already pinned; do not name models.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.
Council workers: HTTP ONLY to localhost:8083. First call get_rule_core_text agent_id=clau.

TASK_ID: TQ-FX-LINEAGE
OBJECTIVE_CLASS: fix N31-02 typed lineage then isolated test+validate until PASS
RISK_LEVEL: high
DEV_LEVEL: high
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 1
FILES_SUBSYSTEMS: 1
EXPECTED_TOOL_CALLS: 24
EXPECTED_WRITES: 4
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_CLAUC_FX_TQ-FX-LINEAGE

## Exclusive write ownership (ONLY these)
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\bin\\clm_librarian_adjudication.py
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\qsleep\\controller\\queue_builder.py
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\bin\\clm_lib\\routes_write.py
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\bin\\clm_candidate_queue.py  (only if it flattens IDs)
- New isolated test: tests/test_n31_02_typed_lineage.py

Do NOT write loop_orchestrator.py, jobs.py, qwake.py, kick_qsleep.py, lease_manager.py, clm_qnight_runner.py, adjudication_client.py, .gitignore, embed.py.

## Fix contract — N31-02
Raw/point/chunk IDs flatten to set[str]. finding source_ids=[1] (point of raw=42) approved because "1" in distilled_point_ids; queue keeps ["1"]; review_apply int(ref) then SELECT raw_turns.id → actual_raw_id=1.
Must: typed refs (kind,id,raw_turn_id,source/version/hash) through adjudicate → queue source_refs → review_apply. Resolve lineage from DB in the apply transaction. Reject ambiguous/untyped numeric-only refs. Legacy untyped refs fail-closed (no guess by colliding integers).
Positive control: valid typed raw=42 still applies. Dead/deleted parent still rejected (DS-11 must stay green).

## Method (loop until 100%)
1. open_session ToolQ. read_file_range exclusive files.
2. write_fix. After EACH write: validate_file.
3. Isolated tests via run_project_test_script:
   - new tests/test_n31_02_typed_lineage.py covering: point1/raw42 must NOT apply as raw1; typed valid raw accepted; deleted parent rejected 0 writes
   - existing apply/deleted_at tests that live in tests/ and only need these files (do not run full suite)
4. You MAY adapt Codo repro D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\41. CLAUDELTNAI\\FIX_TOOLQ_2508\\repro_3108\\codo_deep_3108_lineage_gc_aed0724994b6a4c3.py as a read-only oracle, then write a proper pytest under ToolQ/tests. Do not edit the Codo repro.
5. Fail → scan (search_code) → fix. Max 4 loops. Do not weaken assertions.

## Hard bans
No qnight_state.json. No kill PID. No delete. No embed.py. No spawn embed. No restart :8082. No git commit. No live librarian.db writes — tests use temp SQLite only.

## Evidence
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo_verify_3108\\DATA\\TQ-CV-LINEAGE.md
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\41. CLAUDELTNAI\\FIX_TOOLQ_2508\\AUDIT_DEEP_CODO_3108.md

## Exclusive artifact
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo_fix_3108\\DATA\\TQ-FX-LINEAGE.md

```
# TQ-FX-LINEAGE
N31-02: FIXED|PARTIAL|FAIL  tests: ...  validate: ...
files_changed:
loops: N
END_CLAUC_FX_TQ-FX-LINEAGE
```
