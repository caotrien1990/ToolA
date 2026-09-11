# TQ-CV-AUDIT — VERIFY ONLY Round 1

You are ClauC (council worker). Leader = desktop Clau. Round 1: no peer scores.
VERIFY ONLY. Do NOT edit ToolQ source, tests, flags, DB, live processes, or qnight_state.json.
Do NOT kill any PID. Do NOT spawn embed. Do NOT implement remediations.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.

TASK_ID: TQ-CV-AUDIT
OBJECTIVE_CLASS: independent confirm/deny of Codo persist+resume claims
RISK_LEVEL: high
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 2
FILES_SUBSYSTEMS: 1 (qdreamer audit persist/marker/resume)
EXPECTED_TOOL_CALLS: 12
EXPECTED_WRITES: 1 (this artifact only)
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_CLAUC_R1_TQ-CV-AUDIT

## Claims to CONFIRM | DENY | UNKNOWN

### N31-01 P1 — audit recompute not persisted
Codo: `qsleep/controller/loop_orchestrator.py` ~L883-942 `step_qdreamer1` recomputes audit then `_update_meta` + return; no `_write_json_atomic`. Witness: `test_empty_candidate_is_terminal_audit_clean` FAIL. Repro case `audit_recomputed_failure_not_persisted`.

### R31-02 — resume unbound hash/version
Codo: clean marker / resume still ignores version+hash (DS-05 leftover). Repro case `resume_still_ignores_version`.

## Mandatory evidence (full Windows paths)
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\AUDIT_DEEP_CODO_3108.md`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\EVIDENCE_CODO_3108.json`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\repro_3108\codo_deep_3108_patch_residual_26ab486cd2bd54f2.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\repro_3108\codo_deep_3108_regression_core_7e090459b6f426ac.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\qsleep\controller\loop_orchestrator.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\qsleep\controller\qsleep_core.py`

Codo source hash at audit time: loop_orchestrator.py = a5be7b6e9b87be959c2298e405992eb3ad4d37b323fe9ac20fe7bee3786fb4f2
HEAD claimed: b8419119bbd89cb8ad86634a10ed5507fc7dd7e1

## Method
1. Read Codo claim + repro (bounded).
2. Read current source at cited lines. Confirm hash if possible via [A] read.
3. Decide independently: does the defect exist NOW on disk? Does Codo repro still hold as written?
4. Verdict per point: CONFIRM | DENY | UNKNOWN. UNKNOWN if evidence missing — never guess.
5. Cite file:line. Quote the exact persist/skip path.

## Exclusive artifact (REQUIRED)
Write UTF-8 markdown to EXACTLY:
`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council\tmp_codo_verify_3108\DATA\TQ-CV-AUDIT.md`

Format:
```
# TQ-CV-AUDIT
N31-01: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
R31-02: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
END_CLAUC_R1_TQ-CV-AUDIT
```

Do not write any other path. Do not score peers.
