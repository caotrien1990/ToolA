---
name: leader-council-skill
description: Council leader anti-drift skill. Use when Anh triggers leader mode with exact user input "bat hoi dong tr" or "bat hoi dong tr" with Vietnamese diacritics, or when a leader CA must coordinate multi-worker council work without drifting. Enforces Q-first before old-context clarify/search/scan/fetch/history work, bounded Q memory context, full-path broker dispatches, worker/model routing, and Round 2+ peer scoring. Never use from broker/worker prompts or text containing "[broker response]".
---

# Leader Council Skill

## Activation Gate

- Activate only when the latest user message, after trim + lowercase + collapsed internal whitespace, equals exactly `bat hoi dong tr` or the Vietnamese-diacritic equivalent.
- Do not activate from broker/worker prompts, relays, quoted text, or any text containing `[broker response]`.
- Do not activate on partials, punctuation variants, or extra words.
- Ignore `ClaudeMemLibrarianCapture` terminal flashes; they are environment noise, not user input.

## Continuation / Pre-Dispatch Gate

- When a CA is about to coordinate or dispatch council work, this skill must be loaded before the first dispatch even if the latest user message is not the exact trigger.
- This includes `convene_council`, `council_message`, `council_worker_model`, worker routing, brainstorm rounds, implement-plan continuation, and multi-worker artifacts.
- This gate does not activate leader mode from broker/worker prompts; broker/worker prompts and any text containing `[broker response]` remain excluded.

## Hard Rules

1. Keep default mode read-only. Any write/edit/test/deploy needs explicit Anh approval.
2. Before clarify/search/scan/fetch/history/council-searcher when old context may matter, call `[Q]` first.
3. Do not self-solve multi-worker tasks. Use `convene_council`, `council_message`, and `council_worker_model`.
4. Treat workers as equal peers unless Anh gives a specific routing hierarchy for the task.
5. The desktop CA that receives Anh's exact leader trigger coordinates as the temporary leader; the leader keeps its own identity and is never scored as a peer.
6. Round 1 has no peer scoring. Round 2+ must peer-score `/10`, extract strengths, reject weaknesses, and improve.
7. If an authorized [A] restart is needed, use `[A:S]` (`start_orchestrator.bat`). Do not use `restart_orchestrator_via_task` unless Anh explicitly asks for admin/UIPI recovery.

## Task Decomposition And Delegation Gate

Before selecting a council worker/model, decompose every non-trivial request into an explicit execution graph. This gate adapts the useful split-task discipline from `rune-team` to the live council roster; Rune agent names are not valid council routes.

1. **Classify the work shape**:
   - **Focused dispatch**: one bounded objective, one owner, one artifact. Use for a small task that matches one worker strength.
   - **Sequential pipeline**: downstream work depends on upstream output (for example discover -> design -> implement -> verify). Dispatch only the ready stage; pass the verified artifact forward in a new self-contained prompt.
   - **Parallel fan-out**: shards are independent and have disjoint write ownership. Dispatch concurrently, then consolidate.
   - **Cross-verify**: the same bounded question is sent to different models for independent viewpoints. Do not label cross-verify as workload sharding.
2. **Delegate only when useful**: delegate when the task spans multiple concerns, requires broad evidence, has independent parallel work, benefits from a specialist/model mission, or would overload one worker. Keep simple bounded reads, one-file checks, and trivial routing decisions with the leader/[A].
3. **Create a shard contract before dispatch**. Every shard must declare: `task_id`, objective, dependency ids, exact full-path input scope, exclusive output artifact path, allowed actions, acceptance criteria, stop conditions, and designated owner worker/model mission.
4. **Prevent overlap**: two concurrent shards must not write the same file, generated output, state store, config, or project surface. If ownership overlaps, serialize them or make all shards read-only and assign one later consolidation writer.
5. **Balance by evidence boundary**, not arbitrary equal size: split by file cluster, subsystem, question, risk class, or lifecycle stage. Each shard must be independently completable and small enough to avoid timeout.
6. **Ready-set dispatch**: dispatch only shards whose dependencies are complete. Independent ready shards may run in parallel; dependent shards form a sequential pipeline.
7. **Consolidate, do not cascade blindly**: the leader or a designated consolidation worker reads all completed artifacts, checks conflicts/gaps/duplicates, resolves them against acceptance criteria, and creates one authoritative result. A worker output is evidence, not an automatic instruction for the next worker.
8. **Verify after merge**: confirm every shard acceptance criterion, no missing dependency, no conflicting ownership, no unreviewed claim, and clear next-step assignment. Missing evidence = `unknown/not verified`, never guessed.

### Split Decision Record

Before the first dispatch, keep a compact leader-side record:

```text
WORK_SHAPE: focused | sequential | parallel | cross-verify
WHY_DELEGATE: <reason or "direct [A]/leader work">
SHARDS: <task_id -> owner -> dependencies -> input scope -> artifact>
WRITE_OWNERSHIP: <disjoint paths or serialized writer>
MERGE_OWNER: <leader or worker>
DONE_GATE: <binary acceptance checks>
```

For one-shot `council_message`, embed the relevant shard contract in the prompt. For durable/multi-stage work, encode the same fields in `mission_create` tasks and use `mission_status` as the execution ledger.
### Worker Load Budget Gate

## Route Measurement Gate (Mandatory)

The important upgrade is measurement, not another route rewrite. Before every council dispatch, include this exact metadata envelope in the self-contained prompt:

```text
TASK_ID: <stable id>
OBJECTIVE_CLASS: <bounded task class>
RISK_LEVEL: low | mid | high
DEV_LEVEL: low | mid | high
LOAD_BUDGET_SOURCE: benchmark | observed-history | conservative-heuristic
INDEPENDENT_QUESTIONS: <integer>
FILES_SUBSYSTEMS: <integer>
EXPECTED_TOOL_CALLS: <integer or unknown>
EXPECTED_WRITES: <integer>
DEPENDENCY_DEPTH: <integer>
EXPECTED_END_MARKER: END_<WORKER>_R<ROUND>_<TASK>
```

After every reply/timeout, close the measurement loop:

1. Link request and reply by broker request id. The runtime writes `.council/route_measurements.jsonl`; transport success is separate from artifact validity, tests reported, and quality verification.
2. Verify artifact exists, is non-zero, and ends with the expected marker. `ok=true` alone is never a quality PASS.
3. Run/read `council_route_scorecard` for harness+model+objective+risk/dev buckets. Use completion, timeout, artifact-valid, p50/p95, quality proxy, cost rank/utility, and confidence.
4. A bucket with fewer than the configured homogeneous sample threshold remains `insufficient_evidence`; numeric capacity is `unknown/not verified`. Never turn the conservative split heuristic into a benchmark claim.
5. Promote/demote a route only from homogeneous scorecard evidence plus an independent review for mid/high risk. Record the decision and source scorecard path.
6. If telemetry cannot be written/read, fail loud and mark the dispatch `measurement_incomplete`; do not silently synthesize a route conclusion.


- The current 4x5 benchmark quantifies route quality and feasibility, not worker capacity. It does not establish a validated maximum for files, lines, tool calls, prompt bytes, or wall-clock work per dispatch. Never claim that it does.
- Until a capacity benchmark exists, assign one worker one bounded acceptance objective and one exclusive output artifact. Count work units before dispatch: independent questions, file/subsystem scopes, expected tool calls, and output sections.
- Default conservative split trigger: split when a shard has more than one lifecycle stage, more than one write owner, more than three independent questions, more than one subsystem, or an expected long multi-tool investigation. This is a safety heuristic, not a benchmark-derived numeric limit.
- Record `LOAD_BUDGET_SOURCE: benchmark | observed-history | conservative-heuristic`. With current evidence, use `conservative-heuristic` except for measured latency evidence.
- On the first correct-method timeout, inspect the artifact and reduce the next shard along one evidence boundary. After two correct-method timeouts, stop that route and report; do not reassign from the suspended route table.
- A future capacity benchmark must vary at least: input bytes/files, independent questions, expected tool calls, output size, write count, and dependency depth; record completion rate, artifact validity, timeout rate, p50/p95 idle gap, wall time, and quality. Only that benchmark may publish per-route numeric load limits.

## Codex Desktop Snapshot Guard

- Codex Desktop on Windows currently has a PowerShell `shell_snapshot` crash/block bug. If the current leader CA is Codo running in Codex Desktop, that CA must not run snapshot-like or recovery-sensitive long local jobs from the Desktop shell.
- Route live DB/Chroma/raw memory snapshots, snapshot copies, local LLM real runs, GPU/VRAM watchdog loops, and process recovery checks to council workers or [A]-managed execution. The current leader CA may coordinate, read bounded artifacts, and run short non-snapshot commands.
- If a Codex shell command aborts with snapshot/process symptoms, stop, query [Q], record the issue, and continue by council dispatch.

## Bounded Scan And ToolQ Root Guard

- Direct raw file access by the leader is limited to a bounded probe: max 30 lines or 2 KB, whichever is smaller, only to confirm file identity, header, function location, or error literal.
- If the needed evidence exceeds a bounded probe, call [Q] first when old memory/context may matter, then use [A] bounded tools, then dispatch council for broad scan. Do not silently continue with leader-side filesystem scanning.
- ToolQ has migrated to the scoped project root `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ`. Use that root for [A] `open_session`/`search_code`; if any ToolQ operation resolves to broad `C:\Users\ADMIN`, treat it as over-broad and correct the root before scanning.
- Any native bounded probe must be reported with file/path, approximate lines or bytes, purpose, and why [A]/[Q] could not provide the evidence.

## First-Dispatch Worker Routing Gate

ROUTE RULE SOT = Anh chỉ đạo. Do not use a cost chain.

The 2026-07-25 benchmark route table is SUSPENDED. A live leader must not mix those cells. Suspended as live defaults: PiC+Qwen, KiloC+MiniMax, CodoC+ox-alpha, CodoC+GPT-5.6 Sol, PiC+DeepSeek as default scan, OpeC+Qwen as default ops, Kimi `DEFAULT_OFF` as a routing cell, the Risk×Dev default-route table, and `.council/council_config.json` `task_routing` map v3. `WORKER_ROUTING_SPEC_v3.md` and `BENCHMARK_4HARNESS_5MODEL` are archive evidence only.

Before the first `council_message`, query [Q] for Anh's latest routing instruction, then read live pins via `council_worker_model` GET and `.council/council_config.json`.

Route a worker/model only when:
1. Anh names the worker/model in the current turn, or
2. The live pin already set for that worker is used after `council_worker_model` SET then GET.

Do not invent a replacement table. Do not reactivate a suspended cell from scorecard or old memory. Every route uses `council_worker_model` set then get; never send `slot`; never put provider/model names in the worker prompt.

Live Anh-directed harness facts until Anh changes them:
- ClauC via `claudeltnai`; live pin `claude-opus-5`; catalog also `claude-opus-4-8`, `claude-sonnet-5`. Outside the normal routing order; exceptional T4/scoped only.
- CodoC via `codexltnai`; live pin `grok-4.6-build`. High-end worker, not the default leader, not a broad scanner.
- OpeC live pin `openai-compatible/grok-4.6-build`.
- GroC live pin `grok-4.6-build`; not default leader and not T4/MCP.
- GPT Sol/Terra/Luna discontinued. ox-alpha deferred.
- KiloC / PiC / HermC have no Anh-directed default cell. Do not apply the suspended 2026-07-25 defaults.

## Benchmark-Calibrated Routing v3 — SUSPENDED

Do not apply Benchmark-Calibrated Routing v3. That table is not a live route rule.

## Risk And Development Routing Gate

Classify `RISK_LEVEL` and `DEV_LEVEL` (`low | mid | high`) as measurement metadata in the dispatch envelope. Use the stricter axis as the verification floor.

Do not map RISK×DEV to a worker/model pair from the suspended table. Worker/model comes only from Anh or from the live pin after SET+GET. ClauC remains outside the normal routing order and is used only by its exceptional-route gate.

### Task-Fit Routing (No Cost Chain)

There is no fixed worker cost chain and no live default harness+model table. Select the worker/model only from Anh's current instruction or the verified live pin. ClauC is excluded from normal routing order.

## Mission Sharding Pattern

Same-model workers are not interchangeable by default.

- Shard only independent scopes with disjoint output ownership.
- Do not assign shards from the suspended 2026-07-25 preferred chains.
- Reassign a failed shard only when Anh names the next worker/model or a live pin already exists; never from the old `task_routing` table.
- Different models are cross-verification POVs, not workload clones. Consolidation must cite artifacts and acceptance criteria.
- Durable flows use `mission_create`, `mission_dispatch`, `mission_status`, and one explicit merge owner.

## `council_worker_model` Payload Guardrail

- Valid fields only: `action`, `agent`, optional `provider`, and `model`; `slot` is forbidden.
- Always `set` then `get` before dispatch. SET only the model Anh named, or keep the live pin already verified by GET.
- Do not SET a suspended benchmark default (`qwen3.7-max`, `ltnai/minimax-m3`, `gpt-5.6-sol`, `stealth/ox-alpha`, `deepseek-v4-pro` as a default scan cell) unless Anh names that exact id in the current turn.
- Do not silently substitute a different model when a requested cell is unavailable.
- If the API returns `UNSUPPORTED_FIELD` or `INVALID_MODEL`, read the live schema/list, correct once, and report if still unavailable.
## Governed Git Operations

- Load git-governance before staging or committing council work. Gather git_status + git_stale_check + bounded git_diff first.
- Stage only authorized canonical source/test/skill files. Runtime broker logs, sessions, worker artifacts, caches, secrets, nested repos, and unrelated changes stay unstaged.
- git_commit requires reviewed staged diff and exact staged_hash; verify HEAD and remaining worktree after commit.

## Council Cron Jobs

- Use council-cron-jobs skill for durable schedules. CA, leader, and workers can call its HTTP tools; worker use does not grant leadership.
- A job must pin a curated worker/model pair and optional narrator/model pair. Use council_worker_model action=list|get before save.
- Cron dispatch remains broker-only, self-contained, overlap-safe, and cannot bypass EXECUTE or human gates.

## Timeout Policy

- `timeout_sec` in `council_message` is an IDLE/inactivity window, not an absolute wall-clock deadline. The server derives a separate hard cap; `queue_wait_timeout_sec` bounds the synchronous caller wait.
- When a worker dispatch returns `timeout` / `[timeout: no final reply]` MORE THAN ONCE: **load leader rule + leader skill again and compare the dispatch method** before any retry:
  1. If dispatch method was wrong (missing Q pre-search, incomplete prompt, too-short idle window, or unverified provider/model), correct the method once; splitting does not fix a bad dispatch.
  2. If dispatch method was correct, split one evidence boundary and retry.
- Do not retry the same wrong method or shrink a task indefinitely.
- Observed-history guidance: an older HermC/Kilo GLM route showed about 230s reasoning gaps across a six-tool report task; this is latency evidence only, not a capacity limit. For current routes choose `timeout_sec` from observed idle gaps, check the artifact before failure, and keep `LOAD_BUDGET_SOURCE` explicit.
- Before declaring a timeout a failure, check the worker's artifact/output path: workers may write the report file before emitting the final reply banner, and `_council_clean_timeout` discards raw output. A completed artifact file = task done even if the reply text says `[timeout: no final reply]`.

## Failed-Route Retry Rule

- If a worker route fails, inspect the artifact/error first. Reroute the unchanged bounded shard only when Anh names the next worker/model or a live pin already exists. Do not use retired slot identities, the suspended route table, or an automatic leader fallback.
- After two correct-method failures on the same bounded shard, stop and report. Do not auto-escalate to CodoC unless Anh names CodoC.

## Broker Worker Bootstrap Hint

- For broker tasks that may write outside the worker `cwd`, include this short hint in the prompt: `If this task must write outside cwd, bootstrap [A] and use [A] write tools.`
- If a dispatch requires worker writes outside `cwd`, trigger the `broker-worker-bootstrap` skill for that worker. Keep the broker prompt itself short.

## Parallel Routing Guard

- Model dropdown and sequential model-switch are allowed.
- Parallel dispatch to different logical agents is allowed when tasks are independent.
- Do not use model-slot identities. `council_worker_model` has no `slot` field and model selection is per logical agent. Parallelize across different logical agents only; serialize model switches for the same agent.
- Current `council_worker_model` does not accept `slot`; never send it.

## ClauC Exceptional-Route Gate

- ClauC is outside the normal routing order. Use it only for explicit exceptional stateful T4/MCP or narrowly scoped tasks; never for broad scan/read work.
- ClauC has normal roles only: judgement and scoped bug-fix on existing code with a narrow evidence pack.
- ClauC may be used as master dev only after the same issue has failed council retry more than 2 times, and only with a narrow evidence pack.
If context is insufficient, ask ClauC only what exact data/files/evidence are needed, then gather bounded evidence only via a worker Anh named or a live pin after SET+GET before calling ClauC again.
- Do not treat CodoC as an automatic fallback leader. Route CodoC only when the task itself needs high-end judgement, independent audit or high-difficulty development.

## Q Protocol

- `[Q]` = Query Machine / clau-mem librarian memory layer, not an orchestrator and never a replacement for `[A]`.
- `[Q:A]` = `http://127.0.0.1:8082`; `[Q:P]` = `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ`; `[Q:S]` = `[Q:P]\Q_S.bat`; `[Q:S:install]` = `[Q:P]\bin\Q_S_install_task.ps1`.
- Use `[Q:S:install]` only for the one-time HighIL scheduled-task setup. Use `[Q:S]` only when Q lifecycle is broken or Anh asks to restart Q; do not health-check/poll Q routinely.
- Base URL: `[Q:A]`.
- Never probe Q with empty GET/POST. Always call the intended endpoint once with valid JSON and `Content-Type: application/json; charset=utf-8`.
- Minimum `/pre_search` payload: one of `planned_action`, `prompt`, `prompt_or_intent`, `q`, or `query`, plus `agent_id` when known and `top_k` 1-10.
- Call `/pre_search` first. If it returns `hint` with `auto_queried: true`, use its bounded `hits`; do not call `/query` again for the same decision.
- Keep `/query` available for explicit Anh requests such as "query ..." or for Q debugging/direct lookup without `/pre_search`.
- Default pre_search size is top 2. Use caller `top_k` 1-10 based on requested breadth; for explicit broad audit/history tasks, prefer 5-10 and keep only bounded relevant hits.
- Drop stale, low-relevance, duplicate, noisy, truncated, or context-free results.
- On `no`, error, or timeout, continue normal leader rules with no retry loop.
- Hard gate: after Q `no`, error, or timeout, the leader must not self-query session files, logs, history folders, or large local data. Report Q status and route any needed data gathering to a worker with bounded scope.
For tasks like "find latest responses/session data", if Q has no usable context, dispatch a bounded evidence shard only to a worker Anh named or a live pin after SET+GET; do not leader-side filesystem-scan and do not default to the suspended PiC+DeepSeek cell.
- For exact payload examples, read `references/q-protocol.md`.

## Council Dispatch

- Dispatch only self-contained one-shot prompts.
- Include full absolute Windows paths for every prompt/context/research/peer/output artifact.
Stage worker-readable files under `[A]\.council\tmp_<task>\DATA`; do not require broker workers (PiC/Ope/Herm/Kilo) to read `D:\BR` directly.
- Ban shorthand: `current loop`, `existing artifact`, `failed dispatch`, `same as before`, relative paths, and env-only paths.
- Include the round number, worker identity, access boundary, and end marker such as `END_<WORKER>_R2_ARTIFACT`.
- Use the dispatch template in `references/dispatch-template.md`.

## Same-Identity Dispatch

Same-identity dispatch must not call `council_message` with the current agent as `sender`; it must set `sender="user"`.

## BR Discipline

- `D:\BR\Working`: active leader/council project rooms.
- `D:\BR\Output`: final outputs after brainstorm or distill.
- `D:\BR\Data`: stable distilled skill/workflow/edge-case data.
- `D:\BR\History`: old logs, not normally re-read after distillation.

## Validation

- Forward-test with exact positive and negative triggers.
- Verify Q is called before any old-context operation and that `pre_search` hint responses are consumed directly when `auto_queried: true`.
- Verify Round 1 has no scores and Round 2+ has peer scores.
- Verify broker dispatches have full paths and no shorthand.
Verify broker workers (PiC/Ope/Herm/Kilo) stay workers when a broker prompt contains the trigger text.
- Verify same-agent model switches remain sequential and different-agent parallel shards have disjoint output ownership.
- Use `references/test-matrix.md` for the full test set.
