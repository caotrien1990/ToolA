# TQ10-C2 ClauC VERIFY — DS-06..11 + live room drift

WORKER: ClauC
TASK_ID: TQ10-C2
OBJECTIVE_CLASS: scoped-audit-verify-qsleep-lifecycle
RISK_LEVEL: mid
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 7
FILES_SUBSYSTEMS: 1
EXPECTED_TOOL_CALLS: 20
EXPECTED_WRITES: 1
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_ClauC_R1_TQ10-C2

READ-ONLY. Do NOT edit ToolQ source. Do NOT restart :8082. Do NOT kill any PID (including 51276/13896). Do NOT rewrite qnight_state.json. Do NOT delete .bak. Do NOT edit embed.py. Do NOT scoop leftover. Do NOT implement Phase 1. Do NOT spawn second embed. Do NOT rewrite council room.
If you must write outside cwd, bootstrap [A] and use [A] write tools.

## Objective
Independently CONFIRM or REFUTE Codo DS-06..DS-11 against CURRENT worktree. Cross-check BOTH rooms vs LIVE process so design does not drift.

## Rooms (must read both)
1. Council: `D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\rooms\\room-20260830-toolq-ask-hold\\ROOM.md` + status.json + room_meta.json + events.jsonl
   - Stale 18:05: p5=32232, 69.82%, cron 890f644f 19:17, librarian 13896. No DS list.
2. Campaign Codo 21:25: `D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\41. CLAUDELTNAI\\FIX_TOOLQ_2508\\AUDIT_DEEP_CODO_3008.md` + CHECKPOINTS.md CP-009 + PLAN.md ADDENDUM CODO 30/08 + CONFLICT_GRAPH_CODO_3008.md
   - Claims embed 32232 + runner 51968; DS-06..11 OPEN; K01-K09 OPEN (timing stale vs TQ9-A/B).

Live leader facts to verify (cheap process/flag only; do NOT query full DB COUNT if embed is writing):
- Official P5 **51276** LIVE, flag embed.running pid=51276. 32232 DEAD. 51968 DEAD. librarian 13896 still live last check.
- Do not treat Codo 21:17 health snapshot as current.
- Coverage authority = sqlite WAL RO only if you already have a cheap existing number; otherwise mark coverage NOT_VERIFIED this shard. Do not heavy-scan librarian.db while 51276 writes.

## DS to verify (this shard only)
Repo: `D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ`
- DS-06 P1 global lock no owner: `qsleep/controller/qsleep_core.py` write_lock open(w); remove_lock unlink; `kick_qsleep.py` except path still remove_lock.
- DS-07 P1 lease TTL/fence: kick acquire ttl=600; LeaseManager.release() no owner; NOTE Codo said no renew — `lease_manager.py` HAS renew() with run_id check (Fix-J). Confirm whether kick/loop actually CALLS renew. Partial vs confirmed.
- DS-08 P1 pause still POSTs review_ingest + apply_all: `qsleep/bin/clm_qnight_runner.py` break on qpause then still _ka_post those two URLs.
- DS-09 P2 adj error promoted ok:true: `qsleep/controller/adjudication_client.py` result ok=True ignoring raw.ok.
- DS-10 P1 deferred counted drained: select skips deferred_raw; drained = remaining==0.
- DS-11 P1 apply no deleted_at: `bin/clm_lib/routes_write.py` SELECT 1 FROM raw_turns WHERE id=?.

Optional isolated scripts: `codo_deep_3008_verified_*.py` (lease/lock/pause/adj) and `codo_deep_3008_routing_backlog_*.py` (deferred/dead parent). Mocks/temp SQLite only. No live mutation.

## Output (exclusive)
Write FULL report via [A] write_new_file to:
`D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ10-C2_clauc_verify.md`

Table per DS: CONFIRMED|REFUTED|PARTIAL|NOT_VERIFIED | file:line | Codo vs source | room A vs room B vs LIVE | next (no patch).
ROOM_DRIFT table: field | council room | Codo room | live now | action (report only).
End with END_ClauC_R1_TQ10-C2
Chat: DONE <path> + <=3 line TLDR.
