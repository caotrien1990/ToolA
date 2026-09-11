# TQ-CV-LINEAGE — VERIFY ONLY Round 1

You are ClauC (council worker). Leader = desktop Clau. Round 1: no peer scores.
VERIFY ONLY. Do NOT edit ToolQ source, tests, flags, DB, live processes, or qnight_state.json.
Do NOT kill any PID. Do NOT spawn embed. Do NOT implement remediations.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.

TASK_ID: TQ-CV-LINEAGE
OBJECTIVE_CLASS: independent confirm/deny of Codo typed-ID / wrong-raw-anchor claim
RISK_LEVEL: high
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 1
FILES_SUBSYSTEMS: 1 (adjudication + queue + apply lineage)
EXPECTED_TOOL_CALLS: 10
EXPECTED_WRITES: 1 (this artifact only)
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_CLAUC_R1_TQ-CV-LINEAGE

## Claim to CONFIRM | DENY | UNKNOWN

### N31-02 P1 — raw/point/chunk IDs flattened; apply anchored raw=1 instead of 42
Codo: IDs lose namespace across `bin/clm_librarian_adjudication.py` + `qsleep/controller/queue_builder.py` + `bin/clm_lib/routes_write.py` ~L1118-1128. Fixture apply intended_raw_id=42 but actual_raw_id=1, risk=low, applied=true. Controls: deleted_anchor_rejected_zero_memory_writes; valid_raw_anchor_accepted.

## Mandatory evidence (full Windows paths)
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\AUDIT_DEEP_CODO_3108.md`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\EVIDENCE_CODO_3108.json`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\repro_3108\codo_deep_3108_lineage_gc_aed0724994b6a4c3.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\bin\clm_librarian_adjudication.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\qsleep\controller\queue_builder.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\bin\clm_lib\routes_write.py`

Codo hashes: adjudication=8a341963c1e659cc9c163711bad0a78a540aac3e7cf5f32ee5c4776aaf9bd13d ; queue_builder=00e7590caf2f06f4620104284caae11fbf489cd0630b44ef94d36823b200faf2 ; routes_write=1d8eadb6a75bc7e81ebe05be55bd5e91f89401fa1d698cba2b5cc7e8ef95a2d6
HEAD claimed: b8419119bbd89cb8ad86634a10ed5507fc7dd7e1

## Method
1. Read Codo claim + lineage repro (bounded).
2. Read current source at cited lines. Confirm hash if possible via [A] read.
3. Trace how a point-id=1 / raw-id=42 pair is (or is not) namespaced through adjudicate → queue source_refs → review_apply.
4. Decide independently: defect exist NOW on disk? Codo repro still hold as written?
5. Verdict: CONFIRM | DENY | UNKNOWN. UNKNOWN if evidence missing — never guess.
6. Cite file:line.

## Exclusive artifact (REQUIRED)
Write UTF-8 markdown to EXACTLY:
`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council\tmp_codo_verify_3108\DATA\TQ-CV-LINEAGE.md`

Format:
```
# TQ-CV-LINEAGE
N31-02: CONFIRM|DENY|UNKNOWN
  file:line
  why
  repro_still_holds: yes|no|unknown
END_CLAUC_R1_TQ-CV-LINEAGE
```

Do not write any other path. Do not score peers.
