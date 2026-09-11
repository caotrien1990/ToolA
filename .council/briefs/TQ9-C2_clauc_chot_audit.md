# TQ9-C2 ClauC chot audit (READ-ONLY)

WORKER: ClauC
TASK_ID: TQ9-C2
OBJECTIVE_CLASS: leftover-chot-audit-codo9
RISK_LEVEL: mid
DEV_LEVEL: low
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 1
FILES_SUBSYSTEMS: 1
EXPECTED_TOOL_CALLS: 10
EXPECTED_WRITES: 1 (artifact only)
DEPENDENCY_DEPTH: 1
EXPECTED_END_MARKER: END_ClauC_R1_TQ9-C2

You are ClauC (worker). Leader is desktop ClauLTN. Identity stays ClauC.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.

## WORK_SHAPE
parallel leftover with TQ9-C1. You CHOT AUDIT table. C1 scans. Disjoint ART only.
Do NOT write source. Do NOT wait for C1. Use leader evidence below + your own bounded reads of the 5 patched files + Codo original claims.

## Leader independent verify 2026-08-30 (authoritative for A+B)
pytest tests/test_tq9a_clauc_fixes.py tests/test_tq9b_planner_graph_citation.py tests/test_ask_planner.py -q --> 16 passed in 0.18s
node tests/test_tq9b_src_zone_block.js --> lostNumbers=false reordered=false duplicateLineSuffix=false
Source read via [A] read_file_range:
- interactive_hold.py L118-155 hold() prune if gate_until < now; L241-252 __enter__ fail-open
- clm_librarian_api.py L2742-2755 images multimodal; L2829-2830 MAX_ASK_BODY_BYTES includes /ask_stream; L2771-2818 _timed_out + done.ok=not _timed_out
- ask_planner.py L267-305 invalid-only -> ok=false ZONE_NOT_ALLOWED; mixed keep valid + rejected_zones
- routes_read.py L2127 and L2180 no_context iff not hits and not graph_hits and not ctx_lines
- Qwen_html srcZoneBlock L1735-1749 keep [n], no group/sort, skip :line if ref already /:\\d+$/

Codo original 9-bug (leader isolated repro BEFORE fixes): #1 CONFIRM, #2 CONFIRM stream-only, #3 CONFIRM, #4 CONFIRM, #5 CONFIRM, #6 CONFIRM, #7 PARTIAL (CUSTOM silent drop), #8 CONFIRM graph-only no_context, #9 CONFIRM lost [n]/reorder/double :line.

## Exclusive input
- Leader evidence above (trust as already re-run)
- Optional bounded re-read of the 5 patched files if you doubt a line
- Do NOT edit embed.py. Official embed PID 51276 live (--index --apply --limit 41999 --max-seconds 20999 --force).
- Do NOT scoop store.py _CACHE_SIZE leftover or unused planner_prompts.

## Deliverable: one leftover verdict table
For bugs #1..#9 plus leftover notes, columns:
ID | verdict NOW | evidence (file:line or test) | action
Allowed action values: FIXED | DEFER_EMBED_LIVE | LEFTOVER_DIRTY_NO_SCOOP | NOTE_NOT_BUG | OPEN_NEW

Expected shape unless you find contrary evidence:
#1 DEFER_EMBED_LIVE, #2 FIXED, #3 FIXED, #4 DEFER_EMBED_LIVE, #5 FIXED, #6 FIXED, #7 FIXED, #8 FIXED, #9 FIXED
store.py comment-vs-512KiB = LEFTOVER_DIRTY_NO_SCOOP
planner_prompts unused = NOTE_NOT_BUG

If you find a NEW confirmed bug in the 5 patched files: mark OPEN_NEW with file:line. Do not patch.

## Forbidden
- Source edits, restart :8082, kill PID, rewrite qnight_state.json, delete .bak
- Copy HF cache, Phase 1 schema, scoop leftover
- Dispatch GroC or any other worker

## Artifact (exclusive)
Write UTF-8:
D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ9-C2_clauc_audit.md
If that path fails, also write .council/worker_output/ with same body.
Must end with exact line: END_ClauC_R1_TQ9-C2

STOP after artifact.
