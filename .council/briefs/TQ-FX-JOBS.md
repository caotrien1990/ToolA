# TQ-FX-JOBS — FIX until tests PASS

You are ClauC. Leader = desktop Clau. Model already pinned; do not name models.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.
Council workers: HTTP ONLY to localhost:8083. First call get_rule_core_text agent_id=clau.

TASK_ID: TQ-FX-JOBS
OBJECTIVE_CLASS: fix N31-03/04/06 JobStore CAS + gitignore exception then test+validate until PASS
RISK_LEVEL: high
DEV_LEVEL: high
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 3
FILES_SUBSYSTEMS: 2
EXPECTED_TOOL_CALLS: 24
EXPECTED_WRITES: 3
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_CLAUC_FX_TQ-FX-JOBS

## Exclusive write ownership (ONLY these)
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\q_agent\\jobs.py
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\.gitignore   (ONE exception line only)
- New isolated test: tests/test_n31_03_04_jobs_cas.py

Do NOT write lease_manager.py (owned by TQ-FX-ADMIT). Do NOT write loop_orchestrator, qwake, kick, runner, routes_write, adjudication. Do NOT git add -A. Do NOT git commit. Do NOT force-add qsleep/ledger/.

## Fix contract
### N31-03
acquire_lease SELECT then UPDATE WHERE id. Two JobStore instances both win 3/3.
Must: BEGIN IMMEDIATE or single UPDATE ... WHERE id=? AND (lease_owner IS NULL OR lease expired) ; rowcount=1 is success; return owner/fence from that commit. Sequential foreign-owner still denied.

### N31-04
transition checks version in Python then UPDATE WHERE id only. Two racers both accepted; last checkpoint wins.
Must: UPDATE ... WHERE id=? AND state_version=? ; rowcount=1; bump version + checkpoint + event in same transaction. Sequential stale-version still denied. Separate from N31-03 — fixing acquire does not fix transition.

### N31-06
.gitignore:51 `qsleep/ledger/` hides lease_manager.py. Add a NARROW exception AFTER that rule:
  !qsleep/ledger/lease_manager.py
Do NOT un-ignore the whole ledger. Do NOT git add the ledger dir. Do NOT commit. Leave a note that leader will track the one file later.

## Method (loop until 100%)
1. open_session ToolQ. read_file_range exclusive files.
2. write_fix. After EACH write: validate_file.
3. Isolated tests via run_project_test_script on tests/test_n31_03_04_jobs_cas.py:
   - two-instance acquire_lease: exactly one winner
   - sequential foreign-owner denied
   - two-instance transition same version: exactly one accepted
   - sequential stale version denied
   Temp SQLite only.
4. After gitignore edit: run_project_command `git check-ignore -v qsleep/ledger/lease_manager.py` must show it is NOT ignored. `git check-ignore -v qsleep/ledger/embed.running` (or any state file) must still be ignored.
5. Fail → scan → fix. Max 4 loops.

## Hard bans
No qnight_state.json. No kill. No delete. No embed. No live jobs DB. No git commit/reset/force-add ledger.

## Evidence
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo_verify_3108\\DATA\\TQ-CV-JOBS.md
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\41. CLAUDELTNAI\\FIX_TOOLQ_2508\\repro_3108\\codo_deep_3108_jobs_races_16f1e9cc522c942c.py

## Exclusive artifact
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo_fix_3108\\DATA\\TQ-FX-JOBS.md

```
# TQ-FX-JOBS
N31-03: FIXED|PARTIAL|FAIL  tests: ...  validate: ...
N31-04: FIXED|PARTIAL|FAIL  tests: ...  validate: ...
N31-06: FIXED|PARTIAL|FAIL  check-ignore: ...
files_changed:
loops: N
END_CLAUC_FX_TQ-FX-JOBS
```
