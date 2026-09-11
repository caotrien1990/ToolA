# TQ10-C1 ClauC VERIFY — DS-01..05 + K01-K09 vs rooms

WORKER: ClauC
TASK_ID: TQ10-C1
OBJECTIVE_CLASS: scoped-audit-verify-qsleep-ds
RISK_LEVEL: mid
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 6
FILES_SUBSYSTEMS: 1
EXPECTED_TOOL_CALLS: 20
EXPECTED_WRITES: 1
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_ClauC_R1_TQ10-C1

READ-ONLY. Do NOT edit ToolQ source. Do NOT restart :8082. Do NOT kill any PID. Do NOT rewrite qnight_state.json. Do NOT delete .bak. Do NOT edit embed.py. Do NOT scoop leftover dirty. Do NOT implement Phase 1. Do NOT spawn embed. Do NOT rewrite council room.
If you must write outside cwd, bootstrap [A] and use [A] write tools.

## Objective
Independently CONFIRM or REFUTE Codo DS-01..DS-05 against CURRENT worktree source (not Codo text, not ToolA stale graph). Cross-check BOTH rooms so design does not drift.

## Rooms (must read both)
1. Council room (STALE 18:05, ClauLTN): `D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\rooms\\room-20260830-toolq-ask-hold\\ROOM.md` + status.json + room_meta.json + events.jsonl
   - Claims: p5_pid=32232, coverage 69.82, cron 890f644f 19:17, librarian 13896, Ask/hold PASS. NO DS-01..11. NO TQ9-A/B.
2. Campaign room (Codo 21:23-21:25): `D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\41. CLAUDELTNAI\\FIX_TOOLQ_2508\\AUDIT_DEEP_CODO_3008.md` + PLAN.md addendum CP-009 + CHECKPOINTS.md CP-009 + CONFLICT_GRAPH_CODO_3008.md
   - Claims: DS-01..11 OPEN; K01-K09 still OPEN; embed worker 32232; HEAD b841911.

Live leader facts (do not invent; verify if cheap, else mark NOT_VERIFIED):
- Official P5 NOW pid **51276** (32232 DEAD). Flag `qsleep/ledger/embed.running` owner 51276.
- TQ9-A/B already FIXED K02,K03,K05,K06,K07,K08,K09. K01+K04 DEFER_EMBED_LIVE.
- Codo audit mtime 21:23 is BEFORE TQ9-A/B (~21:54). K02-K09 OPEN in CP-009 is stale timing.

## DS to verify (this shard only)
Repo: `D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\21. ToolQ`
- DS-01 P1 local routing falls to Hermes: `qsleep/bin/clm_qnight_runner.py` `_resolve_distill_env` sets QSLEEP_DISTILL_BACKEND=local; `qsleep/controller/loop_orchestrator.py` `_run_hermes` only routes `api` to HTTP, else Popen HERMES_BASE. Phase CLI override still wins.
- DS-02 P1 missing audit coverage still PASS: loop_orchestrator step_qdreamer1 findings_audit coverage.
- DS-03 P1 semantic FAIL overwritten by quote-match: same function overwrites audit_status.
- DS-04 P1 score excerpt, apply point: `bin/clm_lib/routes_write.py` apply_all content=evidence_excerpt or point; handle_review_apply writes payload.point.
- DS-05 P2 resume marker no hash/verdict: `_resume_artifacts` only file-exists + validate_audit structure.

Also: K01-K09 section 7 of AUDIT_DEEP_CODO_3008 — mark each FIXED / DEFER / STALE / STILL_OPEN with current file:line. Do not reopen FIXED bugs.

Codo witness scripts (optional, isolated only): `21. ToolQ\\.orchestrator_cache\\test_scripts\\codo_deep_3008_verified_*.py` and `codo_deep_3008_routing_backlog_*.py`. Do not hit live DB writes. Do not call live Hermes/CLI.

## Output (exclusive)
Write FULL report to EXACT path via [A] write_new_file:
`D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.council\\tmp_codo9_fix_3008\\DATA\\TQ10-C1_clauc_verify.md`

Table per DS: verdict CONFIRMED|REFUTED|PARTIAL|NOT_VERIFIED | file:line | Codo claim vs current source | room A vs room B drift | next (no patch).
Then K01-K09 table.
Then ROOM_DRIFT for fields you touched (pid, K-status, DS presence).
End with END_ClauC_R1_TQ10-C1
Chat: DONE <path> + <=3 line TLDR.
