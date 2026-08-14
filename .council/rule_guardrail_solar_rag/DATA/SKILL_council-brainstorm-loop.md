---
name: council-brainstorm-loop
description: Run artifact-based multi-worker brainstorm loops through the [A] council/broker. Use when Anh asks Codo to coordinate council workers across rounds, dispatch file-based prompts, collect broker responses, use HermC/OpeC as helpers, score peer plans, or synthesize a final rule/system plan.
---

# Council Brainstorm Loop

Use this skill when Codo coordinates one-shot broker workers through [A]. The rule of the skill is simple: **artifacts carry context; broker replies prove execution; Codo coordinates but does not assume memory.**

## Canonical Files

- Main workflow: `references/workflow.md`
- Prompt patterns: `references/prompt-templates.md`
- Helper script: `scripts/council_brainstorm_loop.ps1`

Do not recreate extra checklist/scoring files unless Anh asks. Keep room-local docs minimal; `D:\BR\workflow\COUNCIL_BRAINSTORM_LOOP_WORKFLOW.md` may point back to this skill but should not duplicate every detail.


## Broker Dispatch Hard Gate

This gate applies to EVERY broker dispatch: ComC, CodoC, OpeC, HermC, ClauC, support scans, investigations, repairs, reviews, and normal round workers.

Before sending `council_message`, Codo must verify the message is self-contained and includes:

- concrete full Windows path for `prompt_file` or `prompt_or_context_file`
- concrete full Windows path for every input artifact the worker must read
- concrete full Windows path for every peer/support/evidence/log artifact, or explicit `none`
- concrete full Windows path for `output_path`
- explicit role, round, worker identity, access boundary, and task contract
- explicit line: `Broker one-shot: no previous context. Do not rely on chat history.`

Never dispatch broker prompts containing shorthand or conversational references such as `current loop`, `this project`, `failed dispatch`, `existing artifact`, `above`, `same as before`, relative paths, unresolved variables, or implied context. If the wrapper fails this gate, do not dispatch; create/fix a prompt/context artifact first.

## Operating Protocol

1. Use a room such as `D:\BR` with `DATA`, `roundN`, and `final`.
2. Put stable context and canonical prompt files in `DATA`.
3. Dispatch workers with a short wrapper that includes concrete full Windows paths for prompt, inputs, support artifact, and output.
4. Broker workers have no memory. Never send unresolved variables such as `$own`, `$promptFile`, `__OWN__`, or shorthand paths.
5. Read only the allowed round window: stable DATA plus the immediately previous round unless Anh explicitly expands scope.
6. Route ComC before every ComC dispatch with `council_worker_model set` then `get`. `comc1`/`comc2` are logical personas over one sequential ComC worker.
7. ClauC and CodoC may run in parallel with one ComC dispatch when independent.
8. HermC and OpeC are helpers, not scored brainstormers.
9. After each dispatch, inspect broker response and artifact status before moving on. Record enough state to know what happened; do not perform special session recovery unless Anh asks.
10. If a worker is slow, continue independent safe work: artifact checks, HermC request consolidation, prompt prep, or state snapshots.
11. Repair only concrete defects: missing artifact, zero-byte artifact, placeholder leak, wrong model, disallowed read window, wrong output path, `AGENT_ERROR`, timeout, overload, or usage limit.
12. Usage limit means pause that worker and report. Do not bypass by starting/churning fresh sessions unless Anh explicitly tells Codo to resume or recover.
13. Score only peer-received scores from Round 2 onward; exclude Codo coordinator, HermC, OpeC, temp files, and explicitly excluded artifacts.
14. Final synthesis uses the winner as base but imports strong peer ideas, evidence, rejected ideas, phases, acceptance criteria, residual risks, and unknowns.

## Quality Gates

Before next round:

- Expected artifacts exist and are non-zero, or a failure artifact/ledger entry exists.
- Broker response was inspected for `ok`, `returncode`, error text, model mismatch, placeholder leak, timeout, overload, or usage limit.
- ComC model was verified immediately before each ComC dispatch.
- Helper artifacts are not scored.
- Fallback is explicit and limited to the room when allowed.
- No [A] source/rule/code edits occur in planning-only phases.

Before final response:

- `SCOREBOARD.md`, `WINNER_BASE_PLAN.md`, and final synthesis exist when the loop requires them.
- Unknowns are reported as unknown/not verified.
- The final answer says which workers/artifacts succeeded, failed, or were deferred.

## HARD GATE - Broker Windows Backslash Escaping - 2026-06-17

- In `council_message` wrapper text, every Windows path must be safe against broker/ARGV escape processing.
- Use doubled backslashes in the transmitted wrapper, for example `D:\BR\3_Working\project\file.md`, especially for segments beginning with `b`, `n`, `r`, `t`, `f`, or `u`.
- Reason: OpeC found ComC broker prompt corruption where `\breakdown` became backspace `0x08` (`\x08reakdown`) before CommandCode parsed arguments.
- If a worker must read a path, prefer a prompt/context artifact plus escaped full path to that artifact in the wrapper.

## HARD GATE - Broker Workspace And Write Permissions - 2026-06-17

- ComC broker print-mode may have `[A] project-orchestrator` as its only accessible workspace even when the loop artifacts live under `D:\BR`.
- If ComC must read `D:\BR` artifacts, Codo stages/copies the exact required inputs into an explicit `[A]` workspace folder such as `.council\tmp_<project>\<worker_round>\` and dispatches only those staged full paths.
- Do not require ComC broker to write the final artifact directly. If `write_file` or `shell_command` is permission-blocked, ask ComC to output the full markdown artifact in its broker reply only, then Codo saves that reply to the official `D:\BR\...` output path.
- If the reply artifact truncates, stage the partial artifact, request a continuation-only reply from the exact cutoff line, then append and remove duplicate cutoff/fence lines before marking the artifact complete.

