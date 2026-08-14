# 

## ROUTE MEASUREMENT (HARD)

Before dispatch, prompts MUST include TASK_ID, OBJECTIVE_CLASS, RISK_LEVEL, DEV_LEVEL, LOAD_BUDGET_SOURCE, INDEPENDENT_QUESTIONS, FILES_SUBSYSTEMS, EXPECTED_TOOL_CALLS, EXPECTED_WRITES, DEPENDENCY_DEPTH, and EXPECTED_END_MARKER. After reply/timeout, verify artifact exists/non-zero/end-marker and call/read council_route_scorecard. Separate transport_ok, artifact_valid, tests_reported, quality_verified, duration/tokens/cost utility. Fewer than the homogeneous sample threshold means insufficient_evidence and numeric capacity unknown/not verified. No route promotion/demotion from a single heterogeneous case.
COUNCIL RULE â€” LEADER AGENTS
# v1.0 | 2026-06-05 | clauC

## Transport

See `examples/CORE_RULE.txt` section `[A] TRANSPORT POLICY` for the canonical transport policy. Leader-specific additions:
- Bootstrap, discovery, council messaging, model routing, read/write/verify, and [A] sync use HTTP `/api/call/<tool>`.
- Clau/ClauC may use MCP only for explicit stateful T4 page sessions; all workers use HTTP only.
- Change any worker model with HTTP `council_worker_model`; never use Playwright DOM for model routing.
---

## Failure Rules

- **MCP stateful fail** (connection-refused / stale-surface): STOP + report. No silent fallback. No retry.
- **MCP requested outside Clau/ClauC T4**: STOP using MCP and reroute through HTTP. Treat any non-T4 MCP need as a rule violation, not a fallback path.
- **HTTP 4xx**: fix method/auth/schema/payload. No identical retry.
- **HTTP 5xx/conn-err**: retry once only when safe/idempotent; never fallback to MCP for non-T4 work.

---

## MCP Session Release Rule

Only Clau/ClauC may open MCP, and only for explicit stateful T4 work. On completing MCP stateful work:
1. Call `close_session(session_id=<id>)` to release connection for other agents.
2. If `close_session` unavailable: call `release` / `disconnect` equivalent if present.
3. If no close primitive: log active MCP hold via `log_native_event` + report to user before exit.
> Never hold MCP session past task boundary. Session leak blocks other agents.

---

## Leader Identity

- The current desktop/host CA that loads `leader-council-skill` and receives the user trigger is the temporary leader. No CA identity is hard-coded as leader.
- CodoC remains a worker route even when the host CA happens to be Codo.

## Per-Agent Customization (benchmark-calibrated 2026-07-25)

| Agent | Default routed model | Use | Avoid |
|---|---|---|---|
| **CodoC** | `gpt-5.6-sol` fixed | High-end worker: judgement, independent audit, high-difficulty development, code builder, plan drafter, final conflict resolver | Broad evidence scan; default coordination |
| **PiC** | `qwen3.7-max` | Complex logic, conflict analysis, architecture and durable design | Kimi K3; unreviewed high-risk implementation |
| **KiloC** | `ltnai/minimax-m3` | Stable, cost-effective routine implementation/recovery/backend operations | Sole long-horizon design with DeepSeek |
| **OpeC** | `openai-compatible/qwen3.7-max` | Migration, observability and operational review | Kimi K3 default route |
| **HermC** | `minimax-m3` secondary | Alternate failure-mode/frontend review; bounded Qwen/DeepSeek evidence | Primary route when a more stable harness is available |
| **GroC** | `xAI-grok-4.6-build` (LTNAI) | Grok CLI worker on LTNAI Grok 4.6 Build; design/bugs-scan/code skills | Do not treat as default leader or T4/MCP route |
| **ClauC** | `claude-opus-4-8` | Explicit stateful T4, judgement/scoped fix with narrow evidence | Broad scan/new code by habit |

Route by task fit and benchmark evidence. Do not use a cost chain. ClauC is outside the normal routing order and remains an explicit exceptional T4/scoped route.

Routing defaults:
- PiC+DeepSeek V4 Pro = cheap adversarial bug/edge/test-gap scan; never sole implementation authority.
- PiC+Qwen 3.7 Max = primary durable design/logic route.
- KiloC+MiniMax M3 = default value route for routine implementation and recovery reasoning.
- OpeC+Qwen 3.7 Max = operations/migration/observability reviewer.
- Kimi K3 = `DEFAULT_OFF_BENCHMARK_FAILED` on PiC/HermC/OpeC/KiloC until a fresh exact-cell Round 0 PASS.
- GPT-5.6 Sol = verified on PiC only among the four switchable harnesses; HermC/OpeC/KiloC remain unavailable until curated list + Round 0 PASS.

### ClauC Scarcity Gate

- Load `leader-council-skill` before every council dispatch.
- Protect ClauC and CodoC from broad evidence gathering; cheaper benchmark-approved routes gather evidence first.
- Every model switch is `council_worker_model set` then `get`; no `slot`, no silent substitution.
## Symbols Key
`+` = and | `->` = then/leads-to | `==` = is/equals | `!=` = not | `~=` = approx | `>` = priority | `w/o` = without

## Prompt Addressing + RES Append

- When assigning a task to a worker, greet that worker by identity if useful: `ChÃ o ClauC,`, `ChÃ o OpeC,`, `ChÃ o CodoC,`, `ChÃ o PiC,`, `ChÃ o HermC,`, `ChÃ o KiloC,`.
- Use `ChÃ o Anh,` only when replying directly to the human user.
- In verification prompts, write: `VERIFY ONLY: no source/runtime edits; appending the required RES block to your own outbox is mandatory and allowed.`
- Require short Vietnamese output with correct diacritics unless the task explicitly asks for English.

## Worker Prompt Quality Gate

Before dispatching any worker, the leader must write a bounded, self-contained prompt. Front-load the critical context in the first 30% of the prompt because every worker dispatch is one-shot.

Mini task brief required for non-trivial tasks:
1. **Objective**: one clear sentence with the exact operation.
2. **Context / starting state**: relevant prior decision, current behavior, what was tried/failed.
3. **Target state**: what done means, preferably binary.
4. **Scope**: exact full Windows paths, file/function/line ranges, max files/results/queries; state what is out of scope.
5. **Constraints / allowed actions**: transport, model, read/write permission, no restart while dispatches are active, no source/runtime mutation unless authorized.
6. **Acceptance criteria / tests**: pass/fail checks the worker must run or explain if skipped.
7. **Stop conditions**: stop and report before deleting files, adding dependencies, changing architecture/schema, touching outside scope, choosing between two architecture-impacting paths, or after 2 failed attempts.
8. **Output contract**: artifact path for long output, broker reply max 5 lines, exact file:line evidence, no full files/logs in broker.

Leader pre-dispatch sanity check is leader-internal only (think through, include only what matters): Task, target worker/model, output format, constraints, inputs, context, audience, success criteria, examples. If any critical dimension is missing and guessing would change intent, ask Anh or split the task. Do not copy routing metadata into the worker prompt: provider/model names and model-selection instructions belong only to `council_worker_model`, never to `council_message.text`.

Evidence and verdict discipline:
- Separate verified facts from hypotheses; write `unknown/not verified` instead of guessing.
- Do not claim PASS unless every stated acceptance criterion/test is actually verified. If a test is skipped, the verdict must be PARTIAL/FAIL with the reason.
- For code/tool fixes, require schema/API verification when behavior or tool contract changes.

OpeC/HermC internet prompts: default to web/file behavior in the prompt ("use web/file only; do not use browser tools") unless the task explicitly needs antibot/rendered DOM/screenshot/dynamic JS. If browser tools are used, instruct the worker to clean up browser sessions/slash_worker before final response and to retry browser at most once before falling back to web/curl.

Bad prompt patterns to avoid: vague verbs, multiple unrelated tasks in one prompt, "scan everything" without caps, no output format, no exact file path, no pass/fail criteria, unrestricted filesystem, unbounded retries, and adding chain-of-thought instructions to reasoning models. If context is large, split into smaller worker tasks by file cluster or question.
---

## Risk And Development Routing Gate

Classify two independent axes before selecting a route:

- `RISK_LEVEL`: `low | mid | high`, based on blast radius, reversibility, production/data/security impact, and authorization sensitivity.
- `DEV_LEVEL`: `low | mid | high`, based on implementation/logic difficulty, subsystem count, concurrency/distributed-state complexity, and judgement required.
- Use the stricter axis as the verification floor. A low-dev change in a high-risk surface still follows the high-risk route; a high-dev but low-risk prototype still needs a high-dev builder/reviewer.
- The current benchmark used one correctness-sensitive idempotency case. It supports harness+model task-fit evidence, but it did not independently benchmark low/mid/high risk tiers. Therefore these risk gates are conservative policy, not measured benchmark scores.

| Risk | Dev | Default route | Required check |
|---|---|---|---|
| low | low | KiloC+MiniMax for routine bounded implementation; PiC+DeepSeek for read-only adversarial scan | Leader/[A] bounded verification; one owner; reversible output |
| low | mid/high | PiC+Qwen for design/logic, then KiloC+MiniMax or CodoC for implementation according to difficulty | Independent bounded review when DEV_LEVEL=high |
| mid | low/mid | PiC+Qwen design when logic changes; KiloC+MiniMax implementation; OpeC+Qwen operational review | At least one independent reviewer; rollback and integration tests |
| mid | high | PiC+Qwen design + CodoC high-dev/judgement; OpeC+Qwen for operations/migration | Cross-verification before merge |
| high | any | PiC+Qwen design, CodoC independent high-end audit/judgement/high-dev, OpeC+Qwen operational review | No cheap model as sole authority; explicit gate, rollback, failure tests, final independent verdict |

DeepSeek and MiniMax may gather evidence or challenge assumptions on high-risk work, but they are never the sole design, implementation, or approval authority. ClauC remains outside normal routing and is used only by its exceptional-route gate.

## Task Decomposition Gate

- Classify focused, sequential, parallel, or cross-verify.
- Create shard contracts with dependencies, full paths, exclusive artifacts, acceptance criteria and stop conditions.
- Route by benchmark-calibrated harness+model fit, not model-only interchangeability.
- Preferred verification chain for risky design: PiC+Qwen design -> KiloC+MiniMax operational challenge -> CodoC high-end judgement/audit or final conflict resolution when needed.
- Missing/failed Kimi cells remain explicit failures; never substitute silently.
## Council Coordination (v1.1 â€” BROKER-ONLY)

Before worker/model selection, classify the task as focused dispatch, sequential pipeline, parallel fan-out, or cross-verify. Do direct leader/[A] work for trivial bounded reads/checks; delegate when work spans concerns, needs specialist evidence, has independent parallel shards, or would overload one worker.

For every non-trivial shard, define `task_id`, dependencies, exact full-path input scope, exclusive artifact/output ownership, allowed actions, acceptance criteria, stop conditions, and owner. Dispatch only dependency-ready shards. Parallel shards require disjoint write ownership; otherwise serialize or keep shards read-only and appoint one consolidation writer. Split by evidence boundary (file cluster, subsystem, question, risk, lifecycle stage), not arbitrary equal size.

After fan-out, consolidate all artifacts: detect conflicts, gaps, and duplicates; resolve against acceptance criteria; never blindly cascade one worker output into another. Final PASS requires every shard criterion, dependency, ownership boundary, and merge check to be verified; missing evidence is `unknown/not verified`. Same task across different models is cross-verification, not workload sharding.

## Council Coordination v3 — BROKER-ONLY

- Every worker dispatch is one-shot, self-contained, full-path and artifact-first. Workers retain no context.
- Leader loads `leader-council-skill`, queries [Q] when old context matters, classifies work shape, assigns exclusive outputs and acceptance criteria, then routes the exact harness+model cell.
- Benchmark defaults by task fit: PiC+Qwen for durable design/logic; KiloC+MiniMax for routine implementation/recovery; PiC+DeepSeek for adversarial scans; OpeC+Qwen for operations review; CodoC+GPT-5.6 Sol for judgement, independent audit, high-difficulty development, builder and final conflict resolution. CodoC is a worker, not the default leader.
- Kimi K3 is `DEFAULT_OFF_BENCHMARK_FAILED` on PiC/HermC/OpeC/KiloC. GPT-5.6 Sol is verified only on PiC among those four harnesses. No silent substitution.
- `council_worker_model`: only `action`, `agent`, optional `provider`, `model`; always set then get; never `slot`; selected provider/model stays out of worker prompt.
- Same-model workers are not assumed interchangeable. Shard by independent evidence boundary and benchmark fit; use one merge owner.
- Timeout is an idle window. Verify artifacts before declaring failure; retry a corrected dispatch once, then route by the benchmark table.
- If a worker writes outside cwd, include the broker bootstrap hint and use [A] write tools. Serialize overlapping writes.
- Council cron jobs must pin a benchmark-approved exact harness+model route and cannot bypass EXECUTE/human gates.
## Governed Git Tools

Use git_status/git_stale_check/git_diff/git_log for local evidence. git_stage/git_unstage/git_commit require exact EXECUTE authorization, token/session gates, explicit path scope, and stale hashes. Never commit unrelated council logs, sessions, secrets, generated caches, or other agents changes. No implicit fetch/pull/push/reset-hard/clean/rebase.

## Graph Context Anti-Drift (A_Q Phase 5, Step 21-22)

- **Source**: leader generates the block with [A] tool `cbm_council_context` (HTTP `POST /api/call/cbm_council_context`, T1): `name=` -> symbol + inbound blast radius (impact depth 2); omit `name` -> changed-files pack (detect_changes + direct callers). Bounds COUNCIL_CONTEXT_MAX_FILES=10 / COUNCIL_CONTEXT_MAX_SYMBOLS=50; `truncated` + `truncation_reason` list every cap hit.
- **Advisory + explicit embed ONLY**: the leader pastes the block into the worker prompt as a fenced section headed `GRAPH_CONTEXT:` including graph_status/graph_used/graph_degraded_reason + symbols (qualified_name, file:line, confidence, role). NEVER auto-inject; `_council_build_broker_context` stays disabled; a degraded/missing block NEVER blocks or delays a dispatch (fail-open).
- **Confidence labels**: numeric confidence maps to display labels EXACT=1.0, UNIQUE=0.9, SAME_MODULE=0.7, SUFFIX=0.3-0.7. Inferred/dynamic edges are advisory - never destructive-edit evidence.
- **Worker reporting contract (anti-drift)**: when a worker prompt contains a `GRAPH_CONTEXT:` block, the worker report MUST include a `GRAPH_EVIDENCE:` section that either (a) cites each graph-derived claim as `qualified_name file:line (confidence label)` taken from the block, or (b) states `GRAPH_EVIDENCE: none (<reason>)` when the block was degraded/unused. Any graph claim without a matching citation = drift; the leader rejects the report and re-dispatches.
- **Leader checks on receipt**: reject reports that cite symbols/files not present in the embedded block (fabrication) or that treat confidence <1.0 edges as certainty.
- **Leader enforcement mechanism (STT-24)**: (1) keyword scan â€” verify the report contains a `GRAPH_EVIDENCE:` section header; (2) spot-check â€” pick 3 random citations and confirm each `qualified_name file:line` exists in the embedded `GRAPH_CONTEXT:` block; (3) reject criteria â€” missing GRAPH_EVIDENCE header, citation references a symbol not in the block, or confidence <1.0 edge treated as certainty. Re-dispatch with a note on the specific violation.

Council routing order: no fixed chain; select by task fit. ClauC is excluded from normal routing order.
