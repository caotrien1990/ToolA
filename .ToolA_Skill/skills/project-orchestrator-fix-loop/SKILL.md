---
name: project-orchestrator-fix-loop
description: |
  Fix/repair loop through council workers. Use when the leader CA must coordinate
  workers to diagnose and fix bugs, apply patches, run validation, and verify fixes.
  Targeted repair workflow — not brainstorming or creative planning.
  Complement to council-brainstorm-loop: brainstorm = plan, fix-loop = repair.
trigger:
  - task_pattern: "fix bug | patch | repair | debug and fix | council fix"
  - council_mode: "fix_repair"
preconditions:
  - "leader-council-skill loaded first"
  - "Bug/issue identified with symptoms"
related_skills:
  - leader-council-skill
  - council-brainstorm-loop
  - 05_write_pipeline
risk_level: medium
---

# Project-Orchestrator Fix Loop

Use this skill when the current leader CA coordinates council workers to diagnose
and fix specific bugs/issues through [A]. This is the repair sibling of
`council-brainstorm-loop`: brainstorm invents plans, fix-loop executes repairs.

Before dispatching any worker, load `leader-council-skill` first for routing
and model gates.

## Fix Loop Flow

1. **Diagnose** — Leader reads error symptoms, identifies affected files/tools
2. **Dispatch** — Send fix prompt to appropriate worker (CodoC for code, OpeC for ops)
3. **Apply** — Worker uses [A] `write_fix` to patch the file
4. **Validate** — Worker calls `validate_file` + `ruff_fix` (auto-chain handles this)
5. **Verify** — Leader runs tests/audit to confirm fix
6. **Close** — If verified, mark issue resolved; if not, re-dispatch with new findings

## Mandatory Measurement Closeout

Inherit the leader Route Measurement Gate. Every dispatch prompt must carry
TASK_ID, OBJECTIVE_CLASS, RISK_LEVEL, DEV_LEVEL, and EXPECTED_END_MARKER.

## Worker Selection

| Bug Type | Worker | Why |
|----------|--------|-----|
| Python logic / syntax | CodoC | High-end code audit |
| Server/config/ops | OpeC | Ops specialist |
| Council/transport | ClauC | Exceptional-only |
| UI/DOM/runtime | PiC | Desktop/T4 specialist |

## Rules

- Artifacts carry truth. Workers have no prior context.
- Each fix prompt must include: file path, error message, expected behavior, acceptance test.
- Never dispatch without a concrete reproduction step.
- One fix per dispatch — don't bundle multiple unrelated fixes.
- After 2 failed attempts on same issue, escalate to leader for manual intervention.
