# BR Timeline Tree Compact

## Folder Tree Compact

```text
D:\BR
|-- 1_History\
|   |-- rebuild_rule\           (closed_working_20260617/, legacy_dot_history/, strays/)
|   |-- broker_retry_fix\       (closed_working_20260617/, full_working_tree/)
|   |-- breakdown_devine_plan\  (closed_working_20260617/)
|   `-- BR_CLOSE_PROJECTS_20260617_MANIFEST.json
|-- 2_Data\
|   |-- rebuild_rule\           (BR_CLEANUP_TIMELINE_TREE.md, README_DATA.md)
|   |-- broker_retry_fix\       (README_DATA.md)
|   `-- breakdown_devine_plan\
|-- 3_Working\                  (closed projects keep only INDEX.md)
|   |-- rebuild_rule\INDEX.md
|   |-- broker_retry_fix\INDEX.md
|   `-- breakdown_devine_plan\INDEX.md
|-- 4_Output\
|   |-- rebuild_rule\DIVINE_RULE_SYSTEM_BUILDUP_PLAN_v2.md
|   |-- broker_retry_fix\CLAUC_FIX_REPORT.md
|   `-- breakdown_devine_plan\DEVINE_PLAN_BREAKDOWN.md
`-- README.md
```

## Timeline

### rebuild_rule
```text
[2026-06-16] Phase1 Pre-Brainstorm [Codo audit/verify -> playbook templates] -> [2026-06-16 22:58] Phase2 Council Brainstorm R1-R5 [4 workers x 5 rounds -> scoring -> clauc winner] -> [2026-06-17 07:53] Phase3 Devine Loop [ComC2 controller, 1 round, STOP] -> [2026-06-17 09:51] Phase4 Distillation Loop [ComC1 controller + ComC2 reviewer, R1-R7, v1->v2] -> [final] 4_Output\rebuild_rule\DIVINE_RULE_SYSTEM_BUILDUP_PLAN_v2.md [canonical plan]
```

### broker_retry_fix
```text
[2026-06-17 11:26] ISSUE_CONTEXT.md [broker re-dispatch after success bug discovered] -> [HermC research] HERMC_RETRY_IDEMPOTENCY_RESEARCH.md [knowledge-based + Codo web supplement] -> [ComC investigate] COMC_LOCAL_INVESTIGATION.md [259 lines, definitive root cause] -> [ComC verify targets] COMC_TARGET_VERIFICATION.md [F1/F2/F3 approved] -> [ClauC fix] CLAUC_FIX_REPORT.md [F1+F2+F3 implemented] -> [ComC post-fix verify] COMC_POSTFIX_VERIFY.md [5 checks PASS] -> [final] 4_Output\broker_retry_fix\CLAUC_FIX_REPORT.md
```

### breakdown_devine_plan
```text
[2026-06-17 18:54] loop_draft_plan Round1 [draft breakdown from Devine Plan v2; ComC2 broker access edge case recovered by staging] -> [2026-06-17 19:16] loop_draft_plan Round2 [peer scoring + borrowed/rejected ideas] -> [2026-06-17 19:31] loop_draft_plan Round3 [compact deltas] -> [2026-06-17 19:42] loop_draft_plan Round4 [convergence, comc1_Round4 emerges as base] -> [2026-06-17 19:54] loop_draft_plan Round5 [final recommendations] -> [2026-06-17 19:59] DRAFT_BREAKDOWN_PLAN.md [winner base comc1_Round4 + comc2/comc3 overlays] -> [2026-06-17 23:54] loop_feedback_plan Round1 [ComC2 MiniMax review, STOP no material review remains] -> [final] 4_Output\breakdown_devine_plan\DEVINE_PLAN_BREAKDOWN.md [accepted breakdown plan]
```

## Read Order For CA

- rebuild_rule: `4_Output\rebuild_rule\DIVINE_RULE_SYSTEM_BUILDUP_PLAN_v2.md` -> `2_Data\rebuild_rule\BR_CLEANUP_TIMELINE_TREE.md` if audit trail is needed.
- broker_retry_fix: `4_Output\broker_retry_fix\CLAUC_FIX_REPORT.md` -> `1_History\broker_retry_fix\closed_working_20260617\evidence\` if root cause detail is needed.
- breakdown_devine_plan: `4_Output\breakdown_devine_plan\DEVINE_PLAN_BREAKDOWN.md` -> `3_Working\breakdown_devine_plan\INDEX.md` -> `1_History\breakdown_devine_plan\closed_working_20260617\` if loop audit is needed.

## Unknowns

- HermC fetch scripts (`fetch_*.py`, `fetch_*.sh`): function unknown/not verified, likely unused prototypes.
- 6 `CODO_REVIEW_PACKET.md` copies in broker_retry_fix: assumed redundant summaries, content not cross-checked.
- `runtime_state_devine_feedback_preflight.json`: purpose inferred from name only.

