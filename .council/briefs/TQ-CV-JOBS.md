# TQ-CV-JOBS — VERIFY ONLY Round 1

You are ClauC (council worker). Leader = desktop Clau. Round 1: no peer scores.
VERIFY ONLY. Do NOT edit ToolQ source, tests, flags, DB, live processes, or qnight_state.json.
Do NOT kill any PID. Do NOT spawn embed. Do NOT implement remediations.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.

TASK_ID: TQ-CV-JOBS
OBJECTIVE_CLASS: independent confirm/deny of Codo JobStore+GC+gitignore claims
RISK_LEVEL: high
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 4
FILES_SUBSYSTEMS: 3 (jobs.py CAS, qwake GC, gitignore)
EXPECTED_TOOL_CALLS: 16
EXPECTED_WRITES: 1 (this artifact only)
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_CLAUC_R1_TQ-CV-JOBS

## Claims to CONFIRM | DENY | UNKNOWN

### N31-03 P1 — acquire_lease SELECT then UPDATE no CAS
Codo: `q_agent/jobs.py` ~L264-283. 3/3 two-instance race reproduced. Sequential controls pass.

### N31-04 P1 — transition version check in Python, UPDATE WHERE id only
Codo: `q_agent/jobs.py` ~L206-260. 3/3 overwrite reproduced.

### N31-05 P1 — GC by folder mtime
Codo: `qsleep/controller/qwake.py` ~L178-217 + `kick_qsleep.py` ~L94. Mock: active_heartbeat_fresh=true, folder_age=7200s, archive_attempts=1, actual_move=false (did not move live run).

### N31-06 P1 — .gitignore qsleep/ledger/ hides lease_manager.py
Codo: `.gitignore:51` `qsleep/ledger/` → `qsleep/ledger/lease_manager.py` untracked; HEAD rollback incomplete.

## Mandatory evidence (full Windows paths)
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\AUDIT_DEEP_CODO_3108.md`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\EVIDENCE_CODO_3108.json`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\repro_3108\codo_deep_3108_jobs_races_16f1e9cc522c942c.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\repro_3108\codo_deep_3108_lineage_gc_aed0724994b6a4c3.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\q_agent\jobs.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\qsleep\controller\qwake.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\qsleep\controller\kick_qsleep.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\.gitignore`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\qsleep\ledger\lease_manager.py`

Codo hashes: jobs.py=d9e4e0f0f1a444920284bc9cd47531015c3f3bcc087e47d43bcc4dc441256760 ; qwake=f908cf76ce9f34a3d550bdfd867a1b3d42158d2cff3a142a14aabd1c42bca933 ; kick_qsleep=b17962b77295ff9982e142d4742a67510e26468bac7fcfc4a13d82ed66dbea0d ; lease_manager=99bd441783b8738fdb4fc364e0377e69527f0b7013313990da746de4fa0463b8
HEAD claimed: b8419119bbd89cb8ad86634a10ed5507fc7dd7e1

## Method
1. Read Codo claim + race/gc repros (bounded).
2. Read current source at cited lines. Confirm hash if possible via [A] read.
3. For 03/04: confirm SELECT-then-UPDATE vs CAS; confirm version check is Python-only then UPDATE WHERE id.
4. For 05: confirm GC key is folder mtime vs heartbeat; state whether Codo mock actually moved a live run.
5. For 06: confirm .gitignore rule and whether lease_manager.py is tracked.
6. Verdict per point: CONFIRM | DENY | UNKNOWN. UNKNOWN if evidence missing — never guess.
7. Cite file:line.

## Exclusive artifact (REQUIRED)
Write UTF-8 markdown to EXACTLY:
`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council\tmp_codo_verify_3108\DATA\TQ-CV-JOBS.md`

Format:
```
# TQ-CV-JOBS
N31-03: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
N31-04: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
N31-05: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
N31-06: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
END_CLAUC_R1_TQ-CV-JOBS
```

Do not write any other path. Do not score peers.
