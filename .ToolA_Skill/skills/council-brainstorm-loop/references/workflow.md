# Council Brainstorm Loop Workflow

This is the canonical workflow detail for `council-brainstorm-loop`. Keep it compact and artifact-first.

## Room

Default:

```text
D:\BR
  DATA\
  round1\
  round2\
  ...
  final\
```

`DATA` holds stable context and canonical prompt files only. Avoid source indexes and duplicate workflow docs unless Anh asks.


## Universal Broker Dispatch Gate

Applies to every broker dispatch, not just helper/debug tasks.

Hard fail before dispatch if the wrapper lacks any of these:

1. Full Windows path for the prompt/context file.
2. Full Windows path for each required input artifact.
3. Full Windows path for each peer/support/evidence/log artifact, or explicit `none`.
4. Full Windows path for the output artifact/report.
5. Worker identity, round/loop/project, task contract, and access boundary.
6. The line: `Broker one-shot: no previous context. Do not rely on chat history.`

Forbidden in broker prompts: relative paths, path fragments, unresolved variables, `current loop`, `this project`, `failed dispatch`, `existing artifact`, `above`, `same as before`, or any dependency on chat memory.

If a dispatch needs more than a short wrapper, first write a prompt/context artifact, then dispatch a wrapper that points to that full path and all input/output paths.

## Dispatch

Preflight:

1. Confirm prompt file and all input/output paths are concrete full Windows paths.
2. Confirm no unresolved placeholders remain.
3. Select the route by task fit, then `set` and `get` the worker model immediately before dispatch; never use `slot`.
4. If state is unclear, inspect broker/runtime state or snapshot before dispatch.

After dispatch:

1. Read the broker response.
2. Verify output exists and has non-zero length.
3. If the response is `AGENT_ERROR`, immediately dispatch OpeC to investigate with bounded scope: broker/runtime logs, expected artifact path, model route, and recent council events. OpeC must write a support report artifact before any retry/skip decision.
4. Record a compact ledger entry when the result is not trivially clean: worker, prompt path, output path, `ok`, `returncode`, error class, artifact state, OpeC report path if applicable, and next action.
5. Do not do special session-id recovery by default. Only use exposed session ids when Anh explicitly asks for resume/recovery.

Error classes:

- `artifact_ok`
- `artifact_missing`
- `placeholder_leak`
- `wrong_model`
- `usage_limit`
- `overload`
- `agent_error`
- `timeout`
- `unknown`

Usage limit: pause that worker and report. Resume only when Anh asks.


Helper/investigation dispatch hard rule:

- Broker workers have no memory even for support/debug tasks. Helper investigation prompts must be file-based or wrapper-based with concrete full Windows paths for every input, expected log/artifact path, and output report path.
- Never rely on conversational phrases such as "this loop", "the failed dispatch", "the current project", or prior chat context unless the same message also lists the concrete full path(s) that define them.
- For helper investigations, create or reference a prompt/context artifact when the task has more than a few facts, then dispatch only a short wrapper with `prompt_file`, `issue_context`, `evidence_paths`, and `output_path`.

## Load Budget

- Current benchmark measures route quality/feasibility, not worker capacity. Treat every numeric shard limit as `unknown/not verified`.
- One shard carries one bounded acceptance objective, one read window, and one output artifact. Split when scope crosses subsystems/lifecycle stages, has overlapping writers, or contains more than three independent questions.
- Correct-method timeout: check artifact first, reduce one evidence boundary, then reroute after a second correct-method timeout.

## Rounds

Round 1:

- Read stable DATA only.
- Write initial plan.
- No scoring.

Round 2+:

- Read stable DATA.
- Read own previous plan.
- Read peer previous plans.
- Read listed support artifact if any.
- Score peers `/10`, extract strengths, reject weaknesses, write improved plan.

Final:

- Score by peer-received average from Round 2 onward.
- Exclude self-scores, the current leader, helper/support workers, temp/excluded artifacts.
- If there are exactly two scored brainstormers, the current leader CA must add an independent leader score for each brainstormer from Round 2 onward. The winner is the average of peer-received score and leader score. Record both numbers in `SCOREBOARD.md` and explain any disagreement between peer and leader scoring before selecting the winner.
- Winner is highest average; tie-break by evidence use, self-repair, acceptance criteria, and lower scope drift.
- Final plan is synthesis, not a copy: winner base plus best peer ideas and helper/support-worker evidence.

## Helpers

Helpers:

- The 2026-07-25 helper table is SUSPENDED. Do not default to any suspended worker+model cell; the live helper set is the `enabled` rows of `.council/council_config.json` routed via `leader-council-skill` (`council_worker_model` SET+GET).
- Dispatch a helper only when Anh names the worker/model or a live pin already exists after SET+GET.
- ClauC: exceptional stateful T4/MCP or narrow scoped work only.
- Model selection is per logical agent. Serialize switches for the same agent; parallelize only different agents with independent shards and disjoint artifacts.

Fallback:

- Normal path is [A] HTTP.
- Native room fallback is allowed only when Anh allowed it and only for room artifacts after [A] is down or tool schema blocks progress.
- Every fallback reports yes/no, reason, files read, files written.

## HARD GATE - Broker Windows Backslash Escaping - 2026-06-17

- In `council_message` wrapper text, every Windows path must be safe against broker/ARGV escape processing.
- Use doubled backslashes in the transmitted wrapper, for example `D:\BR\3_Working\project\file.md`, especially for segments beginning with `b`, `n`, `r`, `t`, `f`, or `u`.
- Reason: broker/ARGV parsing may interpret backslash escape sequences before a worker receives the prompt.
- If a worker must read a path, prefer a prompt/context artifact plus escaped full path to that artifact in the wrapper.

## HARD GATE - Broker Workspace And Write Permissions - 2026-06-17

- A broker worker may expose only `[A] project-orchestrator` as the accessible workspace. `D:\BR` paths can be outside its boundary.
- Stage/copy the exact input set into `[A]\.council\tmp_<project>\<worker_round>\` when needed, then dispatch staged full paths only.
- If artifact writing is unavailable, use reply-artifact mode and have the authorized leader/[A] save the reply to the official room path.
- If the reply truncates, stage the partial artifact, ask the same worker for continuation-only from the exact cutoff line, append it, then clean duplicate cutoff lines and markdown fences before marking the artifact complete.
- `--yolo` in the ARGV is not proof that `write_file` or `shell_command` will be available inside CommandCode print-mode. Verify actual file existence, not the worker's intention to write.

