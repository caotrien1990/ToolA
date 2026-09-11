# TQ9-C1 ClauC leftover scan (READ-ONLY)

WORKER: ClauC
TASK_ID: TQ9-C1
OBJECTIVE_CLASS: leftover-scan-codo9
RISK_LEVEL: mid
DEV_LEVEL: low
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 4
FILES_SUBSYSTEMS: 1
EXPECTED_TOOL_CALLS: 12
EXPECTED_WRITES: 1 (artifact only)
DEPENDENCY_DEPTH: 1
EXPECTED_END_MARKER: END_ClauC_R1_TQ9-C1

You are ClauC (worker). Leader is desktop ClauLTN. Identity stays ClauC.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.

## WORK_SHAPE
parallel leftover with TQ9-C2. You SCAN. C2 AUDITS/CHOT. Disjoint ART only.
Do NOT write source. Do NOT edit embed.py. Do NOT scoop leftover dirty.

## Leader already verified (do not re-fix A+B)
- #2 images + /ask_stream 16MB, #3 done.ok=not _timed_out, #5 hold prune, #6 fail-open: SOURCE MATCH + pytest 16 passed 0.18s (test_tq9a + test_tq9b + test_ask_planner)
- #7 ZONE_NOT_ALLOWED invalid-only / mixed keep valid, #8 no_context only if hits AND graph_hits AND ctx_lines empty, #9 srcZoneBlock keep [n] no zone-sort no double :line: SOURCE MATCH + node lostNumbers=false reordered=false duplicateLineSuffix=false
- 32232 DEAD. Official embed NOW PID 51276 python3.12 clm_semantic_embed.py --db librarian.db --index --apply --limit 41999 --max-seconds 20999 --force RAM~1688MB. Do NOT spawn. Do NOT kill unnamed PIDs.

## Exclusive input (full paths)
1. D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\bin\\clm_semantic_embed.py  (_acquire_embed_slot ~L1020-1077; select_live_semantic_rows / _live_rows_data_version ~L749-821)
2. D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\bin\\clm_lib\\store.py  (L19-23 _CACHE_SIZE=-512)
3. D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ\\bin\\clm_lib\\ask_planner.py  (planner_prompts callers only)
4. The 5 already-patched files ONLY for NEW leftover/regression next to A+B:
   - bin\\clm_lib\\interactive_hold.py
   - bin\\clm_librarian_api.py
   - bin\\clm_lib\\ask_planner.py
   - bin\\clm_lib\\routes_read.py
   - toolQ_UI\\Qwen_html_20260819_council.html  (srcZoneBlock only)

Do NOT scan the rest of ToolQ. Do NOT refresh_project_map. Use [A] read_file_range / search_code bounded.

## Questions (answer each CONFIRM/DENY/NOTE with line evidence)
Q1. #1 slot reclaim still unlink-then-O_EXCL race? (expect CONFIRM, DEFER — embed 51276 live)
Q2. #4 PRAGMA data_version still per-connection stale? (expect CONFIRM, DEFER — embed.py live)
Q3. store.py comment claims 512MB but _CACHE_SIZE=-512 is 512 KiB? (NOTE leftover dirty — do not scoop)
Q4. Any NEW bug introduced by A+B in the 5 patched files (not leftover dirty, not unused planner_prompts)? If none: NONE.

## Allowed actions
- [A] HTTP read/search/test-read only
- Write ONLY the artifact below

## Forbidden
- Edit any source (embed.py, store.py, hold, api, planner, routes, html)
- Restart :8082, kill any PID, rewrite qnight_state.json, delete .bak
- Copy HF cache, implement Phase 1 schema, scoop leftover dirty
- Dispatch other workers

## Artifact (exclusive)
Write UTF-8:
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ9-C1_clauc_scan.md
If that path fails, also write .council/worker_output/ with same body.
Must end with exact line: END_ClauC_R1_TQ9-C1
Include Q1-Q4 verdicts + file:line.

STOP after artifact. Do not start C2 work.
