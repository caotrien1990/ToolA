---
name: council-console-loop
description: Use when the current leader CA must coordinate council workers for text-only console/merge/audit/verify loops, including the alias council_console_loop. Use for combining base and plug artifacts, updating plans or reports, cross-checking source documents, eliminating duplicate/missing/drifted points, and producing a final zero-audit artifact. This skill is for deterministic document consolidation and verification, not brainstorming or creative plan invention.
---

# Council Console Loop

Use this skill for artifact-first text work where the goal is to preserve meaning, reduce drift, and produce a clean final document. It is the console/audit sibling of `council-brainstorm-loop`: no idea competition, no winner scoring, no creative expansion.

Before coordinating workers or sending any `council_message` / `council_worker_model` call, load `leader-council-skill`. This skill handles the document loop; it does not replace leader routing/model gates.

## Mandatory Measurement Closeout

Inherit the leader Route Measurement Gate. Every dispatch prompt must carry TASK_ID, OBJECTIVE_CLASS, RISK_LEVEL, DEV_LEVEL, LOAD_BUDGET_SOURCE, INDEPENDENT_QUESTIONS, FILES_SUBSYSTEMS, EXPECTED_TOOL_CALLS, EXPECTED_WRITES, DEPENDENCY_DEPTH, and EXPECTED_END_MARKER. After each reply/timeout, verify artifact+marker and refresh/read `council_route_scorecard`. Transport `ok` is not artifact/quality PASS; insufficient homogeneous samples keep capacity `unknown/not verified`.

## Core Rule

Artifacts carry truth. Workers have no prior context. The leader must provide exact input paths, output paths, source hierarchy, and acceptance gates in every prompt.

## When To Use

- Console two or more reports into one clean report.
- Plug a verified addendum into a large plan without bloating or duplicating sections.
- Update a final plan from a resolved conflict report.
- Audit a console artifact against its sources.
- Verify that numeric claims, paths, worker routes, dependencies, and gates are preserved.
- Produce a leader-ready execution artifact from prior phase outputs.

Do not use this skill for solution ideation, stress brainstorm, worker competition, or scoring. Use `council-brainstorm-loop` for those.

## Roles

Default role pattern:

- `KiloC + MiniMax M3`: default console/merge/fix route for bounded routine text artifacts.
- `PiC + DeepSeek V4 Pro`: adversarial source-gap scan for missing, wrong, duplicate, drift, and placeholder leaks; never the sole final authority.
- `OpeC + Qwen 3.7 Max`: operational audit of paths, routes, dependencies, rollout, observability, and source mismatch.
- `PiC + Qwen 3.7 Max`: high-judgement structural/logic verification when the document encodes architecture or durable protocols.
- `CodoC + GPT-5.6 Sol`: independent final judgement/conflict resolution for high-risk or disputed results, not routine merging.
- `ClauC`: outside normal routing; exceptional stateful T4/MCP or narrow scoped verification only. Kimi K3 remains default-off.

Adjust roles only when Anh gives a different route. Helper outputs are evidence, not new source truth. First classify `RISK_LEVEL` and `DEV_LEVEL` using `leader-council-skill`: low-risk/low-dev may use the routine route; mid-risk requires an independent reviewer; high-risk work requires PiC+Qwen design, CodoC independent high-end audit/judgement/high-dev, OpeC+Qwen operational review, and no DeepSeek/MiniMax result as sole authority.

## Load And Split Gate

- Inherit `leader-council-skill` Worker Load Budget Gate. The current benchmark does not quantify how many files, lines, audit points, or tool calls one worker can safely accept.
- One console shard owns one stable section/step range and one output artifact. Split large documents by stable phase/section boundaries; never split a semantic invariant across shards without one later consolidation owner.
- More than three independent audit questions, multiple subsystems, overlapping writers, or multiple lifecycle stages require split/serialization. Mark these as conservative heuristics, not benchmark limits.
- On correct-method timeout, inspect the artifact, reduce one section/evidence boundary, and reroute after a second correct-method timeout.

## Loop Pattern

1. **Prepare DATA**: stage source files, prior decisions, route/evidence notes, and output contract in a room folder.
2. **Console**: one worker merges or updates the base artifact with plug artifacts.
3. **Audit**: an auditor compares the console artifact against every source artifact.
4. **Fix**: if audit has points, return only the actionable audit report to the console worker.
5. **Verify**: high-judgement verifier checks final console artifact against sources and audit trail.
6. **Close**: close only when `audit_point = 0` and the leader sees no obvious drift.

Repeat steps 2-5 until zero-audit. Do not bypass a missing artifact, zero-byte artifact, placeholder leak, unsupported number, or unverified route.

## Prompt Contract

Every worker prompt must be self-contained and include:

- `Broker one-shot: no previous context. Do not rely on chat history.`
- role and exact task: console, audit, fix, verify, or final confirm.
- full Windows path for `prompt_file`.
- full Windows path for every source/input artifact.
- full Windows path for prior audit/fix artifact, or explicit `none`.
- full Windows path for `output_path`.
- source hierarchy: which file is base, which files are plugs, which files are evidence only.
- write permission: report-only, artifact write, or reply-artifact fallback.
- forbidden expansion: no new technical claims unless source-backed.
- output format and required verdict field.

Never dispatch prompts containing shorthand such as `current`, `above`, `same as before`, `the previous file`, unresolved variables, relative paths, or implied context.

## Source Hierarchy

Use explicit source roles:

- `base`: the document whose structure should remain dominant.
- `plug`: content to insert/update into the base.
- `audit`: defects to fix, not a source of new claims unless it quotes a source.
- `evidence`: supports a claim but must not override base/plug unless verified.
- `final`: the output artifact to close.

If two sources conflict, preserve the base structure and mark the conflict as `needs leader decision` unless Anh already resolved it.

## Audit Taxonomy

Auditors must report findings using these categories:

- `missing`: source point absent from output.
- `wrong`: output contradicts source.
- `duplicate`: same point appears more than once or in competing forms.
- `drift`: output adds unsupported interpretation or changes intent.
- `unsupported_number`: number, threshold, score, count, date, model id, path, or worker route lacks source evidence.
- `placeholder_leak`: unresolved variable or conversational shorthand leaked into artifact.
- `structure_loss`: required phase/step/table/field shape lost.
- `scope_creep`: output added creative solution or implementation detail not requested.

Each finding must include source file path and exact local evidence such as heading, step id, or line number when available. If evidence is unavailable, write `unknown/not verified`.

## Zero-Audit Gate

A loop can close only when all are true:

- Console artifact exists and is non-zero.
- Auditor says `audit_point = 0`.
- Verifier says `audit_point = 0` or `PASS`.
- Leader spot-checks required source claims, paths, worker routes, numbers, and stop conditions.
- No placeholder leak appears: `$promptFile`, `$own`, `same as before`, `current`, `above`, `TBD`, `TODO`, or unresolved temp names.

If the verifier reports checks passed as a count, distinguish it from findings. Do not treat `audit_check_count` as `audit_point`.

## Output Contract

Console artifacts should be clean final documents, not process narratives. Keep audit trail in separate files or ledgers.

Required fields for audit/verify reports:

```text
verdict: PASS | FAIL | PARTIAL
audit_point: <integer finding count>
scope_checked:
- <file/path or section>
findings:
- category:
  evidence:
  required_fix:
closed_notes:
- <short note or none>
```

For final leader execution artifacts, prefer a compact schema:

```text
step_id:
goal:
owner_worker:
support_worker:
route_reason:
dependency:
parallel_group:
input:
output:
verify_gate:
rollback:
stop_condition:
```

## Guardrails

- Text-only means no source/runtime code edits unless Anh explicitly expands scope.
- Preserve exact full Windows paths.
- Preserve exact step ids, phase numbers, thresholds, worker names, model-route decisions, and verify gates.
- Do not rewrite a settled plan into a new plan.
- Do not inflate context by pasting full source files into broker replies; use artifacts and bounded evidence.
- Do not let a plug file dominate the base unless Anh explicitly says to replace base.
- Do not invent benchmark numbers, model prices, route evidence, or implementation status.
- Write `unknown/not verified` instead of guessing.

## Leader Checklist

Before dispatch:

- Identify base, plug, audit, evidence, and final output paths.
- Decide whether the task is update, console, split-console, or final stitch.
- Split very large plans by stable phase/step ranges, then stitch and verify.
- Stage worker-readable copies under `[A]` workspace when a broker worker cannot read `D:\BR` directly.

After each dispatch:

- Inspect broker response and output artifact.
- Check artifact exists and is non-zero.
- Search for placeholder leaks and forbidden shorthand.
- Read audit report before deciding next loop.
- Record enough ledger state to resume without re-discovery.

Before final:

- Spot-check source hierarchy was honored.
- Verify `audit_point = 0` from the required auditor/verifier.
- State any skipped check as `unknown/not verified`, not PASS.
