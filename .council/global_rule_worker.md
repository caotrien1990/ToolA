# 

## MEASUREMENT OUTPUT CONTRACT

Preserve the leader metadata envelope: TASK_ID, OBJECTIVE_CLASS, RISK_LEVEL, DEV_LEVEL, LOAD_BUDGET_SOURCE, INDEPENDENT_QUESTIONS, FILES_SUBSYSTEMS, EXPECTED_TOOL_CALLS, EXPECTED_WRITES, DEPENDENCY_DEPTH, EXPECTED_END_MARKER. Write the exclusive artifact, ensure it is non-zero, end with EXPECTED_END_MARKER exactly, and return a compact DONE/PASS line plus test evidence. Do not report transport success as quality verification. If a requested measurement field cannot be observed, return unknown/not verified; never invent token, duration, cost, capacity, or test data.
GLOBAL RULE — WORKER AGENTS
# v2 | broker-only | HTTP-only

## Transport

See `examples/CORE_RULE.txt` section `[A] TRANSPORT POLICY` for the canonical transport policy. Worker-specific summary: CodoC/OpeC/GroC use HTTP `/api/call/<tool>` only and never MCP. If a worker task needs browser/T4 state beyond HTTP tools, report that it must be routed to ClauC/T4.

## Failure Handling

| Condition | Action |
|---|---|
| [A] connection-refused / ORCHESTRATOR_NOT_RUNNING | STOP + report immediately; no retry, no native fallback |
| HTTP 4xx (auth/method/schema/rate/body) | Fix payload/auth; respect `Retry-After`; no identical retry |
| HTTP 5xx / conn-err after bootstrap | Retry once only if idempotent; otherwise report unknown/not verified |

## Per-Agent Quick Ref — Anh chỉ đạo (benchmark 2026-07-25 SUSPENDED)

- CodoC: live pin `grok-4.6-build` via CodexLTNAI; high-end worker when assigned; not the default leader and not broad scanner.
- OpeC: live pin `openai-compatible/grok-4.6-build`. Do not apply suspended Qwen default.
- GroC: LTNAI `grok-4.6-build` via Grok CLI; design/bugs-scan/code skills; not default leader and not T4/MCP.
- ClauC: T4/stateful judgement and scoped fix with narrow evidence only; outside the normal routing order.
- Workers execute the assigned model/task; they do not reroute themselves. If the requested model/provider is unavailable, fail loud with the exact error and do not substitute. The current host/desktop CA that loads `leader-council-skill` is the temporary leader; no worker identity is hard-coded as leader. RISK_LEVEL / DEV_LEVEL are measurement metadata, not a route table. Do not use a cost chain.
## Addressing + Report Rule

- Direct user-facing reply: start `Chào Anh,`.
- Agent-to-agent council prompt/report: greet the actual agent, e.g. `Chào ClauC,`, `Chào OpeC,`, `Chào CodoC,`, `Chào GroC,`.
- VERIFY ONLY / no-edits still MUST append the required RES block to the worker's own outbox. That append is task reporting, not a forbidden source edit.
- Keep worker replies short and with correct Vietnamese diacritics.

---

## Council Mode (v2 — BROKER-ONLY)

- **Broker-only**: context mode is removed. Every dispatch is a fresh one-shot — NO session/history retention. Anything you need from earlier turns MUST be embedded in the prompt you receive.
- **No memory scaffolding**: do NOT create/read/write `.rune/` (or decisions.md/progress.md/cumulative-notes.md/learnings.jsonl etc.) and do NOT run any onboarding. Keep the working dir clean.
- **Provider/model**: selected by the leader through HTTP `council_worker_model` when needed and persisted in `.council/worker_models.json`. Run with whatever is configured; do not change your own provider/model.
- **Outside-cwd writes**: if a broker prompt says `If this task must write outside cwd, bootstrap [A] and use [A] write tools.`, load/use the `broker-worker-bootstrap` skill and write through HTTP [A] tools; do not silently native-write outside `cwd`.

## Scheduled Council Jobs

Workers may create/read/trigger/delete durable schedules only through [A] HTTP council_cron_job_* tools when their task carries explicit authorization. Always pin worker+model and optional narrator+model; cron is not a permission bypass.

## Governed Git Tools

Use git_status/git_stale_check/git_diff/git_log for local evidence. git_stage/git_unstage/git_commit require exact EXECUTE authorization, token/session gates, explicit path scope, and stale hashes. Never commit unrelated council logs, sessions, secrets, generated caches, or other agents changes. No implicit fetch/pull/push/reset-hard/clean/rebase.

## Inter-worker Messaging

- **Channel = the `council_message` [A] TOOL, called via the [A] API (HTTP) — workers use the API ONLY, NEVER MCP.** Invoke it exactly like any other [A] tool, with Invoke-RestMethod (structured body → no escaping, no curl). **You MUST pin UTF-8** or Vietnamese diacritics corrupt — PowerShell encodes a string body as cp1252: `$b=[Text.Encoding]::UTF8.GetBytes((@{sender='<you>'; to='<ope|codo|clau|gro|user>'; text='<your message>'} | ConvertTo-Json)); Invoke-RestMethod -Method Post -Uri http://localhost:8083/api/call/council_message -ContentType 'application/json; charset=utf-8' -Body $b`. Field is `sender`, NOT `from`. The live `to` set is the `enabled` rows in `.council/council_config.json`; a retired id is refused. [A] auto-handles validation + dispatch + broker logging and returns the recipient's reply in the `reply` field. Report that `reply` verbatim.
- `to: "user"` (or `"Anh"`) posts your message to the human/leader dashboard — no agent is run.
- **NEVER** run `curl`/`curl.exe`/shell, **NEVER** hand-build JSON, **NEVER** run helper scripts to message agents. Just call the `council_message` tool with structured args; passing shell commands desyncs the system.
- Each message is an INDEPENDENT one-shot — never assume the other agent remembers a previous message.
- Read cross-agent replies from `.council/broker_filtered.jsonl` (filtered transcript: type/from/to/text/id; no raw/reasoning). Filter by route/task; missing match = report unknown, never fabricate.
- **Investigating council/broker/dispatch/prompt/reply issues → call the [A] tool `council_tracker` FIRST** (HTTP `POST /api/call/council_tracker`). It returns `output_path` to a full `council_live.txt` snapshot (BROKER FILTERED FULL / COUNCIL EVENTS FULL / LIVE RAW COMC FULL) and archives the previous one; read that path. Do NOT manually scrape `.council` raw logs unless `council_tracker` is unavailable or you need a deeper raw file after the snapshot.
- Greet the target agent directly: `Chào OpeC,` / `Chào CodoC,` / `Chào GroC,` / `Chào ClauC,`. Use `Chào Anh,` only for the human user.

Council routing order: no fixed chain; ROUTE RULE SOT = Anh chỉ đạo. ClauC is excluded from normal routing order.
