---
name: council-brainstorm-loop
description: Run artifact-based multi-worker brainstorm loops through the [A] council/broker. Use when Anh asks the current CA to coordinate council workers across rounds, dispatch file-based prompts, collect broker responses, use HermC/OpeC as helpers, score peer plans, or synthesize a final rule/system plan.
---

# Council Brainstorm Loop

Use this skill when the current leader CA coordinates one-shot broker workers through [A]. The rule of the skill is simple: **artifacts carry context; broker replies prove execution; Codo coordinates but does not assume memory.**

Before the current leader CA coordinates a brainstorm loop or sends any `council_message` / `council_worker_model` call, Codo must load `leader-council-skill` first. This applies to continuation prompts such as "implement plan" or "tiếp" even without the exact `bật hội đồng tr` trigger; this skill handles loop mechanics and does not replace leader routing/model gates.

## Mandatory Measurement Closeout

Inherit the leader Route Measurement Gate. Every dispatch prompt must carry TASK_ID, OBJECTIVE_CLASS, RISK_LEVEL, DEV_LEVEL, LOAD_BUDGET_SOURCE, INDEPENDENT_QUESTIONS, FILES_SUBSYSTEMS, EXPECTED_TOOL_CALLS, EXPECTED_WRITES, DEPENDENCY_DEPTH, and EXPECTED_END_MARKER. After each reply/timeout, verify artifact+marker and refresh/read `council_route_scorecard`. Transport `ok` is not artifact/quality PASS; insufficient homogeneous samples keep capacity `unknown/not verified`.
Canonical Files

- Main workflow: `references/workflow.md`
- Prompt patterns: `references/prompt-templates.md`
- Helper script: `scripts/council_brainstorm_loop.ps1`

Do not recreate extra checklist/scoring files unless Anh asks. Keep room-local docs minimal; `D:\BR\workflow\COUNCIL_BRAINSTORM_LOOP_WORKFLOW.md` may point back to this skill but should not duplicate every detail.


## Broker Dispatch Hard Gate

This gate applies to EVERY broker dispatch: CodoC, PiC, OpeC, HermC, KiloC, exceptional ClauC, support scans, investigations, repairs, reviews, and normal round workers.

Before sending `council_message`, the current leader CA must verify the message is self-contained and includes:

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
6. Classify `RISK_LEVEL` and `DEV_LEVEL` as measurement metadata only. The 2026-07-25 benchmark table is SUSPENDED. Route only when Anh names the worker/model or after `council_worker_model set` then `get` of the live pin; set only `action`, `agent`, and `model`, never `slot`.
7. ClauC is outside normal routing; use only for explicit exceptional stateful T4/MCP or narrowly scoped work. CodoC may join independent work as a high-end worker when Anh names CodoC, never as an automatic coordinator/fallback.
8. Helpers are selected only when Anh names them or a live pin already exists. Helpers are not scored brainstorm peers unless Anh explicitly promotes them.
9. Parallelize only different logical agents with independent shards and disjoint output ownership. Model selection is per logical agent, so serialize model switches for the same agent. Different models provide cross-verification viewpoints, not duplicate workload clones.
10. After each dispatch, inspect broker response and artifact status before moving on. Record enough state to know what happened; do not perform special session recovery unless Anh asks.
11. If a worker is slow, continue independent safe work: artifact checks, HermC request consolidation, prompt prep, or state snapshots.
12. Repair only concrete defects: missing artifact, zero-byte artifact, placeholder leak, wrong model, disallowed read window, wrong output path, `AGENT_ERROR`, timeout, overload, or usage limit.
13. Usage limit means pause that worker and report. Do not bypass by starting/churning fresh sessions unless Anh explicitly tells the current leader CA to resume or recover.
14. Score only peer-received scores from Round 2 onward; exclude the current leader, HermC, OpeC, temp files, and explicitly excluded artifacts, except the two-brainstormer anti-bias rule below.
15. If a brainstorm loop has exactly two scored brainstormers, the current leader CA must add an independent leader score for each brainstormer from Round 2 onward. Each brainstormer then has two score streams: peer-received score and leader score. The scoreboard winner is the average of both streams, and final synthesis must record both numbers. Leader scoring is mandatory only for the two-brainstormer case because pairwise scoring can easily bias toward symmetric ties or style preference.
16. Final synthesis uses the winner as base but imports strong peer ideas, evidence, rejected ideas, phases, acceptance criteria, residual risks, and unknowns.

## Load And Split Gate

- Inherit `leader-council-skill` Worker Load Budget Gate. Current benchmark has no validated per-worker task-count or file/tool-call capacity limit.
- One brainstorm shard = one bounded question/POV, one allowed read window, and one exclusive output artifact. Split by evidence boundary when a prompt spans multiple subsystems, lifecycle stages, or more than three independent questions.
- Record `LOAD_BUDGET_SOURCE`; currently use `conservative-heuristic` unless a specific observed-history record applies. Correct-method timeout -> inspect artifact -> shrink one boundary; second correct-method timeout -> stop and report; do not reroute from the suspended table.

## Quality Gates

Before next round:

- Expected artifacts exist and are non-zero, or a failure artifact/ledger entry exists.
- Broker response was inspected for `ok`, `returncode`, error text, model mismatch, placeholder leak, timeout, overload, or usage limit.
- Each routed worker/model pair was verified immediately before dispatch via `council_worker_model set` then `get`; no silent substitution and no suspended-table default.
- Helper artifacts are not scored.
- Fallback is explicit and limited to the room when allowed.
- No [A] source/rule/code edits occur in planning-only phases.

Before final response:

- `SCOREBOARD.md`, `WINNER_BASE_PLAN.md`, and final synthesis exist when the loop requires them. For exactly two brainstormers, `SCOREBOARD.md` must include both peer-received score and leader score for each brainstormer.
- Unknowns are reported as unknown/not verified.
- The final answer says which workers/artifacts succeeded, failed, or were deferred.

## HARD GATE - Broker Windows Backslash Escaping - 2026-06-17

- In `council_message` wrapper text, every Windows path must be safe against broker/ARGV escape processing.
- Use doubled backslashes in the transmitted wrapper, for example `D:\BR\3_Working\project\file.md`, especially for segments beginning with `b`, `n`, `r`, `t`, `f`, or `u`.
- Reason: broker/ARGV parsing may interpret backslash escape sequences before a worker receives the prompt.
- If a worker must read a path, prefer a prompt/context artifact plus escaped full path to that artifact in the wrapper.

## HARD GATE - Broker Workspace And Write Permissions - 2026-06-17

- A broker worker may expose only `[A] project-orchestrator` as its accessible workspace even when loop artifacts live under `D:\BR`.
- If a worker cannot read `D:\BR`, stage/copy exact required inputs into `[A]\.council\tmp_<project>\<worker_round>\` and dispatch only those staged full paths.
- If artifact writing is permission-blocked, request a full reply artifact and have the authorized leader/[A] save it to the official room path; verify file existence rather than worker intent.
- If the reply artifact truncates, stage the partial artifact, request a continuation-only reply from the exact cutoff line, then append and remove duplicate cutoff/fence lines before marking the artifact complete.

