---
name: project-orchestrator-fix-loop
description: Coordinate safe backend fixes for [A] project-orchestrator and council/broker issues. Use when Anh asks Codo to investigate, patch, or verify [A] backend/council bugs with HermC research, ComC MiniMax review, ClauC implementation, restart discipline, and post-fix verification.
---

# Project Orchestrator Fix Loop

Use this skill for [A] backend/council fixes. The invariant is: **research and local evidence narrow the patch; ComC verifies targets; ClauC patches; ComC verifies the result; Codo decides.**


## Broker Dispatch Hard Gate

All broker dispatches in this workflow are one-shot and contextless. This applies to HermC, ComC, ClauC, OpeC if explicitly re-enabled, and any verifier/reviewer.

Before `council_message`, the wrapper must include concrete full Windows paths for prompt/context, every input/evidence artifact, every log/code report artifact to read, and output path. Use explicit `none` for absent optional inputs. Never rely on chat history or phrases like `current issue`, `failed dispatch`, `same as before`, `above`, or relative paths.

If the prompt cannot be made self-contained in the message, write `DATA\ISSUE_CONTEXT.md` first and dispatch with that full path plus all required evidence/output paths.

## Canonical Reference

- Detailed workflow: `references/workflow.md`
- Prompt contracts: `references/prompt-contracts.md`

Load the references only when executing the workflow. Keep per-issue rooms minimal.

## Core Workflow

1. Create a room such as `D:\BR\ISSUE_NAME` with `DATA`, `hermc`, `comc_investigate`, `comc_verify_targets`, `clauc_fix`, and `comc_postfix_verify`.
2. Write `DATA\ISSUE_CONTEXT.md` with exact evidence paths, suspected bug, constraints, and desired fix direction.
3. Ask HermC for live internet research first. HermC should prefer its native internet/file fetch tools over [A] fetch tools when they are more effective. HermC must produce source-backed artifacts. If live fetch is unavailable, HermC must write `BLOCKED_NO_LIVE_FETCH` and stop; Codo may add its own live web supplement.
4. Route ComC to `MiniMaxAI/MiniMax-M3-FREE`, verify with `council_worker_model get`, then ask ComC to investigate local code using HermC/Codo research.
5. Require ComC to output a short `CODO_REVIEW_PACKET.md` with exact file/function/line evidence. Codo reviews only that packet plus bounded code windows.
6. Run a second ComC pass to approve/deny patch targets and remove overbuild.
7. Dispatch ClauC to implement only approved targets. ClauC must produce `CLAUC_FIX_REPORT.md` and a short Codo packet.
8. Restart [A] only when no worker dispatch is in flight and runtime state is completed/error/timeout, then health-check with `get_rule_core_text`.
9. Route ComC again and run post-fix verification. Treat provider/network errors as provider errors unless artifacts prove fix failure.
10. Final response must list changed files, tests, PASS/FAIL/UNKNOWN, restart status, and residual risks.

## Hard Gates

- Do not use OpeC unless Anh explicitly adds it back.
- Do not let ComC implement patches. ComC investigates and verifies.
- Do not let HermC write knowledge-based internet reports as if sourced. If native/live fetch fails, require `BLOCKED_NO_LIVE_FETCH`.
- Do not patch F4/F5/F6-style adjacent findings unless Codo explicitly approves them after ComC second-pass.
- Do not restart [A] while any worker dispatch is in flight.
- Do not broaden to rule text, provider circuit breakers, outbox redesign, or global exactly-once semantics unless Anh asks.

## Token Discipline For Codo Review

- Read `CODO_REVIEW_PACKET.md` first.
- Open at most five code windows, each <= 80 lines, around approved findings.
- Classify each finding as `APPROVE`, `DENY`, `DEFER`, or `UNKNOWN`.
- Ask ComC to compress if the packet exceeds 220 lines.

## HermC Research Rule

HermC prompts must include:

- `live_fetch_required: yes`
- prefer HermC native internet/file fetch tools for internet research; use [A] fetch only when it is clearly better for project-local context
- official/primary sources preferred
- `[antibot]` when dynamic/community/browser evidence is needed
- `BLOCKED_NO_LIVE_FETCH` if HermC cannot actually fetch current web sources
- source URLs and 1-line relevance notes in the Codo packet

If HermC reports no live fetch, Codo must either perform live web supplement directly or stop before ComC investigation.
