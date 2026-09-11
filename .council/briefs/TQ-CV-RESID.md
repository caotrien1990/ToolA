# TQ-CV-RESID — VERIFY ONLY Round 1

You are ClauC (council worker). Leader = desktop Clau. Round 1: no peer scores.
VERIFY ONLY. Do NOT edit ToolQ source, tests, flags, DB, live processes, or qnight_state.json.
Do NOT kill any PID. Do NOT spawn embed. Do NOT implement remediations.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.

TASK_ID: TQ-CV-RESID
OBJECTIVE_CLASS: independent confirm/deny of Codo residual claims R31-01/03/04/05
RISK_LEVEL: mid
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 4
FILES_SUBSYSTEMS: 2 (local pipeline config + lease/pause/risk admission)
EXPECTED_TOOL_CALLS: 14
EXPECTED_WRITES: 1 (this artifact only)
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_CLAUC_R1_TQ-CV-RESID

## Claims to CONFIRM | DENY | UNKNOWN

### R31-01 — local still HERMES_BASE
Codo: pipeline local still points at HERMES_BASE.

### R31-03 — renew/TOCTOU; lost lease still publishes
Codo: lease owner check then delete/publish race. Repro cases: `lease_owner_check_to_delete_race`, `lost_lease_still_publishes`.

### R31-04 — pause after ingest still apply_all
Codo: pause between ingest and apply not rechecked. Repro: `pause_between_ingest_and_apply_not_checked`.

### R31-05 — unknown risk allowed
Codo: adjudication unknown risk still allowed. Repro: `adjudication_unknown_risk_allowed`.

Do NOT re-judge N31-01..06 or R31-02 (other shards).

## Mandatory evidence (full Windows paths)
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\AUDIT_DEEP_CODO_3108.md`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\EVIDENCE_CODO_3108.json`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\repro_3108\codo_deep_3108_patch_residual_26ab486cd2bd54f2.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\qsleep\controller\adjudication_client.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\qsleep\ledger\lease_manager.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\qsleep\bin\clm_qnight_runner.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\bin\clm_lib\routes_write.py`

Codo hashes: adjudication_client=e8559ac379bcffa9c524eddd6ba0ccb1447a3ff109d15f15de54efab4393722c ; lease_manager=99bd441783b8738fdb4fc364e0377e69527f0b7013313990da746de4fa0463b8 ; clm_qnight_runner=b09c998464810733c314e874316e05f9597df76c484c761443eb5a19407c8fa9 ; routes_write=1d8eadb6a75bc7e81ebe05be55bd5e91f89401fa1d698cba2b5cc7e8ef95a2d6
HEAD claimed: b8419119bbd89cb8ad86634a10ed5507fc7dd7e1

## Method
1. Read Codo residual section + patch_residual repro (bounded).
2. Read current source. Confirm hash if possible via [A] read.
3. For R31-01: find the actual local base/url constant — is it still HERMES_BASE?
4. For R31-03/04/05: confirm the admission hole on disk; say whether Codo repro still holds as written.
5. Verdict per point: CONFIRM | DENY | UNKNOWN. UNKNOWN if evidence missing — never guess.
6. Cite file:line.

## Exclusive artifact (REQUIRED)
Write UTF-8 markdown to EXACTLY:
`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council\tmp_codo_verify_3108\DATA\TQ-CV-RESID.md`

Format:
```
# TQ-CV-RESID
R31-01: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
R31-03: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
R31-04: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
R31-05: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
END_CLAUC_R1_TQ-CV-RESID
```

Do not write any other path. Do not score peers.
