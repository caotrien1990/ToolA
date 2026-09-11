# TQ-FX-ADMIT — FIX until tests PASS

You are ClauC. Leader = desktop Clau. Model already pinned; do not name models.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.
Council workers: HTTP ONLY to localhost:8083. First call get_rule_core_text agent_id=clau.

TASK_ID: TQ-FX-ADMIT
OBJECTIVE_CLASS: fix N31-05 + R31-03/04/05 GC/lease/pause/risk then test+validate until PASS
RISK_LEVEL: high
DEV_LEVEL: high
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 4
FILES_SUBSYSTEMS: 1
EXPECTED_TOOL_CALLS: 30
EXPECTED_WRITES: 5
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_CLAUC_FX_TQ-FX-ADMIT

## Exclusive write ownership (ONLY these)
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\qsleep\\controller\\qwake.py
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\qsleep\\controller\\kick_qsleep.py
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\qsleep\\ledger\\lease_manager.py
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\qsleep\\bin\\clm_qnight_runner.py
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\qsleep\\controller\\adjudication_client.py
- New isolated test: tests/test_n31_05_r31_030405.py

Do NOT write loop_orchestrator.py, jobs.py, routes_write.py, queue_builder.py, adjudication (librarian), .gitignore, embed.py.

## Fix contract
### N31-05
qwake GC uses folder st_mtime; kick calls gc_orphan_runs BEFORE gate_all. Fresh heartbeat in old folder can be selected.
Must: GC after admission/owner gate; skip if heartbeat/PID/lease generation says live or unknown (fail-closed). Recheck identity immediately before any move. Tests must mock move — do NOT move live runs. Do not call GC on live qsleep dirs.

### R31-03
lease release: owner check then _delete_lease no CAS. kick renews once, ignores False, still write_queue + ok=true.
Must: atomic owner fence on release (UPDATE/DELETE WHERE owner=?); if renew/release False → do not publish/queue/receipt ok. Lost lease stops publish.

### R31-04
qpause checked before kick; after review_ingest apply_all runs without recheck.
Must: recheck pause before every mutation including apply_all / server commit. Early-pause-before-loop must stay blocked (positive control).

### R31-05
ok=true + missing risk → unknown; kick only rejects high.
Must: unknown/malformed/missing risk fail-closed. HTTP 503 / body ok=false stay rejected. Valid low/medium still pass.

## Method (loop until 100%)
1. open_session ToolQ. read_file_range exclusive files.
2. write_fix. After EACH write: validate_file.
3. Isolated tests via run_project_test_script on tests/test_n31_05_r31_030405.py + any existing pause/deferred tests you can run isolated (do not run full suite; do not run test_runner_pause_never_auto_applies if it historically times out — write a smaller unit that proves the recheck).
4. Temp dirs / mocks only. No live GC. No live lease delete.
5. Fail → search_code → fix. Max 4 loops.

## Hard bans
No qnight_state.json. No kill PID (including 49068/13896). No delete live ledger. No embed. No spawn embed. No restart :8082. No git commit. Do not archive real run folders.

## Evidence
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo_verify_3108\\DATA\\TQ-CV-JOBS.md
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo_verify_3108\\DATA\\TQ-CV-RESID.md
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\41. CLAUDELTNAI\\FIX_TOOLQ_2508\\repro_3108\\codo_deep_3108_patch_residual_26ab486cd2bd54f2.py
- D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\41. CLAUDELTNAI\\FIX_TOOLQ_2508\\repro_3108\\codo_deep_3108_lineage_gc_aed0724994b6a4c3.py

## Exclusive artifact
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo_fix_3108\\DATA\\TQ-FX-ADMIT.md

```
# TQ-FX-ADMIT
N31-05: FIXED|PARTIAL|FAIL  tests: ...  validate: ...
R31-03: FIXED|PARTIAL|FAIL  tests: ...  validate: ...
R31-04: FIXED|PARTIAL|FAIL  tests: ...  validate: ...
R31-05: FIXED|PARTIAL|FAIL  tests: ...  validate: ...
files_changed:
loops: N
END_CLAUC_FX_TQ-FX-ADMIT
```
