# Prompt Templates

Prefer file-based canonical prompts. Store one prompt file per round/type, then dispatch a short message that references the prompt file and lists every required full Windows path. Do not paste the full prompt repeatedly unless the worker cannot read the prompt file.

Before dispatch, check that all path values are concrete full Windows paths or explicit `none`. This applies to EVERY broker dispatch: CodoC, PiC, OpeC, HermC, KiloC, exceptional ClauC, repairs, investigations, reviews, and normal rounds. Do not send unresolved variables like `$own` or `__OWN__`, relative paths, path fragments, or conversational references like `current loop`, `failed dispatch`, `existing artifact`, `same as before`, or `above`. Broker workers have no context, so a wrapper must explicitly list all files the prompt expects in the same message.

After dispatch, do not move on from the response screen. Record the broker reply in `D:\BR\final\BROKER_DISPATCH_LEDGER.md` or the room's equivalent ledger: event id, `ok`, `returncode`, exposed `session_id`, response class, artifact status, and next action.

## Short Dispatch Wrapper

```text
You are `IDENTITY` for Phase 0 brainstorm Round N.
Read and follow exactly this prompt file:
`PROMPT_FILE_PATH`

Required full paths:
- prompt_file: `PROMPT_FILE_PATH`
- data_or_base_plan: `DATA_OR_BASE_PLAN_PATH`
- own_previous_plan: `OWN_PREVIOUS_PLAN_OR_none`
- peer_plan_1: `PEER_PREVIOUS_PLAN_1_OR_none`
- peer_plan_2: `PEER_PREVIOUS_PLAN_2_OR_none`
- peer_plan_3: `PEER_PREVIOUS_PLAN_3_OR_none`
- support_artifact: `HERMC_SUPPORT_OR_none`
- output_path: `OUTPUT_PATH`
- objective_or_note: `OBJECTIVE_OR_NOTE`

Broker one-shot: no previous context. Read the prompt file and every full path listed above. Verify output exists and has non-zero length before replying.
```

## Reply-Artifact Wrapper

Use when a broker worker can read staged files but cannot write files in one-shot mode. Stage required `D:\BR` inputs into `[A]\.council\tmp_<project>\<worker_round>\` first. The current leader CA saves the reply to `OFFICIAL_OUTPUT_PATH`.

```text
Broker one-shot: no previous context. Do not rely on chat history.

You are `IDENTITY` for PROJECT ROUND.

IMPORTANT: Do NOT write files. Do NOT call write_file. Do NOT call shell_command.
Produce the full artifact as your broker reply only. The current leader CA will save your reply to:
`OFFICIAL_OUTPUT_PATH`

Read ONLY these staged full paths:
- prompt_file: `STAGED_PROMPT_FILE`
- loop_context: `STAGED_LOOP_CONTEXT_OR_none`
- source_plan: `STAGED_SOURCE_PLAN`
- own_previous_plan: `STAGED_OWN_PREVIOUS_OR_none`
- peer_plan_1: `STAGED_PEER_1_OR_none`
- peer_plan_2: `STAGED_PEER_2_OR_none`
- support_artifact: `STAGED_SUPPORT_OR_none`

Task:
1. Follow `prompt_file`.
2. Produce the complete markdown artifact in this reply.
3. Start with `# EXPECTED_TITLE`.
4. End with `END_<IDENTITY>_<ROUND>_ARTIFACT`.
5. Do not summarize instead of producing the artifact.
```

If the reply truncates, stage the partial file and dispatch a continuation-only prompt from the exact cutoff line. Codo appends the continuation and removes duplicate cutoff/fence lines.

## One-Round Feedback Wrapper

Use for tasks that only ask workers to review a final plan and return point lists. Do not invoke loop scoring.

```text
You are `IDENTITY` for one-round feedback.
Read and follow exactly this prompt file:
`PROMPT_FILE_PATH`

Required full paths:
- prompt_file: `PROMPT_FILE_PATH`
- base_plan: `BASE_PLAN_PATH`
- round5_clauc: `CLAUC_PLAN_PATH`
- round5_codoc: `CODOC_PLAN_PATH`
- output_path: `OUTPUT_PATH`

Broker one-shot: no previous context. This is feedback only, not scoring and not editing. Write numbered points to output_path and verify the output exists/non-zero before replying.
```

## Round 1 Worker Prompt

Save this as a prompt file such as `D:\BR\DATA\BROKER_PROMPT_ROUND1_ONLY.md`.

```text
You are `IDENTITY` for Phase 0 brainstorm Round 1.

Broker one-shot: you have no previous context. Read the files below before writing.

Must-load context files:
1. `DATA_FILE_1`
2. `DATA_FILE_2`

Task:
1. Build an initial plan for OBJECTIVE.
2. Keep it practical for [A] and council.
3. Include assumptions, risks, acceptance criteria, and what not to build yet.
4. No scoring in Round 1.
5. Include `Fallback used: yes/no`; if yes, list reason and files read/written.
6. Do not include provider/model route names in worker-visible prose unless the task is a model audit or benchmark.

Write output to exactly:
`OUTPUT_PATH`

Output contract:
- Markdown only.
- Sections: `Identity`, `Files Read`, `Round1 Plan`, `Risks`, `HermC request for next round`, `Fallback used`, `Self-check`.
- In `Self-check`, confirm no peer plans were read.
- Verify the output file exists and has non-zero length before replying.
```

## Round N Worker Prompt

Save this as a prompt file such as `D:\BR\DATA\BROKER_PROMPT_ROUND3_TO_N_ONLY.md`.

```text
You are `IDENTITY` for Phase 0 brainstorm Round N.

Broker one-shot: you have no usable memory from previous dispatches. Read ONLY the files listed below before writing. Do not read earlier rounds unless a listed previous-round plan explicitly quotes them.

Must-load stable DATA context:
1. `DATA_FILE_1`
2. `DATA_FILE_2`

Must-load immediately previous round plans only:
- Your previous plan: `OWN_PREVIOUS_PLAN`
- Peer plans to score/review:
  1. `PEER_PREVIOUS_PLAN_1`
  2. `PEER_PREVIOUS_PLAN_2`
  3. `PEER_PREVIOUS_PLAN_3`

Optional support artifact:
- `HERMC_SUPPORT_OR_none`

Task:
1. Score each peer plan `/10` in a compact table. Score only those 3 peer plans.
2. Extract strengths from peer plans and support research that your own previous plan lacks.
3. Reject weak/unsafe parts explicitly.
4. Build a better Round N plan for OBJECTIVE.
5. Include `Fallback used: yes/no`; if yes, list reason and files read/written.
6. If you want HermC research for the next round, add at most 3 precise questions under `HermC request for next round`; otherwise write `none`.
7. Do not include provider/model route names in worker-visible prose unless the task is a model audit or benchmark.

Write output to exactly:
`OUTPUT_PATH`

Output contract:
- Markdown only.
- Sections: `Identity`, `Files Read`, `Peer Scores`, `Borrowed Strengths`, `Rejected Weaknesses`, `RoundN Plan`, `HermC request for next round`, `Fallback used`, `Self-check`.
- In `Self-check`, confirm only DATA + immediately previous round files + listed support artifact were read.
- Verify the output file exists and has non-zero length before replying.
```

## Two-Brainstormer Leader Score Addendum

Use this as a coordinator-side scoring rule, not as a worker prompt, when the loop has exactly two scored brainstormers.

```text
Two-brainstormer anti-bias rule:
- Round 2 onward: each brainstormer gives the normal peer score `/10`.
- The current desktop leader also gives an independent leader score `/10` for each brainstormer after reading the same allowed DATA and immediately previous round artifacts.
- SCOREBOARD.md must include: brainstormer, peer_received_average, leader_average, combined_average.
- Winner = highest combined_average.
- Final synthesis must explain why the leader score agreed or disagreed with peer scoring. The leader is not a scored peer.
- Helpers such as HermC/OpeC remain unscored unless Anh explicitly promotes them to brainstormers.
```

## HermC Support Prompt

Save this as a prompt file such as `D:\BR\DATA\BROKER_PROMPT_HERMC_SUPPORT.md`.

```text
You are HermC for brainstorm support research Round N.

Role: internet/file research provider only, NOT a brainstormer. Do not write a rule plan.

Context files to read first:
1. `DATA_FILE_1`
2. `DATA_FILE_2`
3. `ROUND_PREVIOUS_PLAN_1`
4. `ROUND_PREVIOUS_PLAN_2`
5. `ROUND_PREVIOUS_PLAN_3`
6. `ROUND_PREVIOUS_PLAN_4`

Research questions consolidated from workers:
1. QUESTION_1
2. QUESTION_2
3. QUESTION_3

Internet guidance:
- live_fetch_required: yes. If you cannot fetch live web/file sources, write `BLOCKED_NO_LIVE_FETCH` to the output and stop; do not substitute knowledge-based research.
- Prefer HermC native internet/file fetch tools for internet research; use [A] fetch only for project-local context or when explicitly needed.
- Prefer official docs/specs/primary sources.
- Use community sources only for practical failure modes.
- If blocked by anti-bot, dynamic JS, rendered DOM, or community pages requiring browser state, mark query with `[antibot]`, use browser only for that evidence, and clean up before final response.
- Label inference as `Inference`.
- If available tools are only file-level tools such as `patch/read_file/search_files/write_file`, report `BLOCKED_NO_LIVE_FETCH` plus the tool list. The leader must stop and report the capability issue instead of silently doing leader-side web fallback.

Write output to exactly:
`OUTPUT_PATH`

Output contract:
- Markdown only.
- Sections: `Scope`, `Sources`, `Findings By Question`, `Recommended Adoption For [A]`, `Do Not Adopt`, `Evidence Gaps`, `Fallback used`.
- `Sources` must contain URLs or file paths plus 1-line relevance notes. If no live source was fetched, the report status must be `BLOCKED_NO_LIVE_FETCH`.
- Verify the output file exists and has non-zero length before replying.
```

## Repair Prompt

```text
You are `IDENTITY`. This is a targeted artifact repair, not a new brainstorm round.

Problem:
PROBLEM_DESCRIPTION

Allowed inputs:
1. `FILE_1`
2. `FILE_2`
...

Task:
1. Reconstruct only the missing/invalid artifact for Round N.
2. Do not read unlisted rounds.
3. Preserve the same scoring/task requirements from the original round prompt.
4. Write to exactly `OUTPUT_PATH`.
5. Verify the file exists and has non-zero length.

If you cannot write it, reply `WRITE_FAILED` with exact reason.
```


## Helper Investigation Wrapper

Use for OpeC/HermC local investigation, broker errors, artifact failures, or support scans. Helper workers are broker one-shot too.

```text
You are `HELPER_IDENTITY` for helper investigation.
Read and follow exactly this prompt/context file:
`PROMPT_OR_CONTEXT_FILE_PATH`

Required full paths:
- prompt_or_context_file: `PROMPT_OR_CONTEXT_FILE_PATH`
- loop_root: `LOOP_ROOT_PATH`
- failed_worker_artifact_or_none: `FAILED_ARTIFACT_PATH_OR_none`
- failure_marker_or_none: `FAILURE_MARKER_PATH_OR_none`
- relevant_success_artifact_1_or_none: `SUCCESS_ARTIFACT_1_OR_none`
- relevant_success_artifact_2_or_none: `SUCCESS_ARTIFACT_2_OR_none`
- local_log_or_evidence_path_1_or_none: `EVIDENCE_PATH_1_OR_none`
- output_path: `OUTPUT_REPORT_PATH`

Broker one-shot: no previous context. Do not rely on chat history. Read only the paths listed above. Verify output exists and has non-zero length before replying.
```

