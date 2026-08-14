# BR Room Organization

BR is organized by lifecycle category first, then project name.

```text
D:\BR
├── 1_History\<project_name>
├── 2_Data\<project_name>
├── 3_Working\<project_name>
└── 4_Output\<project_name>
```

## Meanings

- `1_History`: temporary/intermediate/log files generated during working that no longer need normal reading after final output exists. Keep for audit/recovery.
- `2_Data`: reusable learning for future CAs/projects: workflow notes, edge cases, conventions, distilled lessons.
- `3_Working`: active work only. After Anh accepts/chốt a project, move working contents to `1_History\<project_name>` and leave only `INDEX.md` with pointers.
- `4_Output`: final output only, one project folder per project.

## Current Projects

### rebuild_rule
- Purpose: build and verify/upgrade the divine rule system rebuild plan.
- Includes two loops: build-plan brainstorm loop, then verify/upgrade distillation loop.
- Final output: `D:\BR\4_Output\rebuild_rule\DIVINE_RULE_SYSTEM_BUILDUP_PLAN_v2.md`.
- Working index: `D:\BR\3_Working\rebuild_rule\INDEX.md`.
- Historical artifacts: `D:\BR\1_History\rebuild_rule`.

### broker_retry_fix
- Purpose: fix [A] BROKER-RETRY-AFTER-SUCCESS.
- Final output: `D:\BR\4_Output\broker_retry_fix\CLAUC_FIX_REPORT.md`.
- Working index: `D:\BR\3_Working\broker_retry_fix\INDEX.md`.
- Historical artifacts: `D:\BR\1_History\broker_retry_fix`.

### breakdown_devine_plan
- Purpose: break down `DIVINE_RULE_SYSTEM_BUILDUP_PLAN_v2.md` into actionable phases, steps, dependencies, guardrails, harnesses, acceptance criteria, and owner/verifier assignments.
- Includes two loops: `loop_draft_plan` (5-round worker draft) and `loop_feedback_plan` (ComC2 review to STOP).
- Final output: `D:\BR\4_Output\breakdown_devine_plan\DEVINE_PLAN_BREAKDOWN.md`.
- Working index: `D:\BR\3_Working\breakdown_devine_plan\INDEX.md`.
- Historical artifacts: `D:\BR\1_History\breakdown_devine_plan\closed_working_20260617`.

## Triggers For Future CAs

- If Anh says `mở project`, ask Anh for the project name, then create/open these folders: `1_History\<project_name>`, `2_Data\<project_name>`, `3_Working\<project_name>`, `4_Output\<project_name>`.
- If Anh says a project is chốt/closed/done/accepted, perform the project-close routine below.

## Project-Close Routine

Every time a project is done/chốt:
1. Move all non-index files from `3_Working\<project_name>` to `1_History\<project_name>\closed_working_<date>`.
2. Leave or create only `3_Working\<project_name>\INDEX.md` with pointers to Output, History, and Data.
3. Ensure `4_Output\<project_name>` contains only final output files, not evidence bundles.
4. Update `D:\BR\2_Data\BR_TIMELINE_TREE_COMPACT.md` with the project timeline in compact arrow format.
5. Update this README if project names, final outputs, triggers, or folder rules changed.
6. Do not delete history unless Anh explicitly asks.

Compact timeline format:
```text
[project_name]
[time] [file name] [context shrink] -> [time 2] [file name 2] [context shrink] -> [time final] [file name final] [context shrink]
```

## CA Rules

1. Read this README first.
2. Read `4_Output\<project_name>` first to understand the final result.
3. Use `3_Working\<project_name>` only for active/current project files.
4. Put reusable lessons in `2_Data\<project_name>`.
5. If a folder is locked, copy the needed final files into the correct target first; clean the locked folder later.
