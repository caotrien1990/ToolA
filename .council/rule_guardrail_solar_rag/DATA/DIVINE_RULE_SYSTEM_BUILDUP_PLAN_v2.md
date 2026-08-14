# Divine Rule System Buildup Plan

Date: 2026-06-17
Coordinator: Codo
Base winner: `clauc`
Review integration: ComC2 feedback pass 1 applied
Scope: planning only. No [A] source/rule/code edits were performed in Phase 0.

## Executive Principle

Validate at the boundary, never inside the agent.

A rule is allowed into canonical rule text only if it names:

1. Who reads it.
2. Which boundary enforces it.
3. What violation signal proves failure.
4. What recovery action the caller must take.

Anything that cannot pass this admission gate is guidance, not bootstrap-critical rule.

Admission gate table:

| Rule area | Who reads | Enforcement boundary | Violation signal | Recovery |
|---|---|---|---|---|
| Canonical rule SOT | CA, leader, sync tools | registry loader + sync path | missing registry path or sha mismatch | fail closed, regenerate through `manage_agent(action='sync_rule', target='all')` |
| Session type | CA, tool caller, [A] API | `open_session` + tool schema labels | relative path, wrong session kind, missing path fields | hard-reject with actionable error |
| Leader dispatch | leader + broker | `TaskEnvelope` schema | malformed envelope, missing stop/fallback/verification policy | reject before dispatch; notify leader after threshold |
| Worker reply | worker + leader | `WorkerReply` schema | missing verdict/evidence/artifact/fallback fields | mark invalid, repair only missing artifact |
| Native fallback | CA/worker + audit/replay | fallback policy + `log_native_event` | unlogged native write while [A] reachable | forbid write or replay pending log on reconnect |
| Identity | broker + council_message | PID trace + sender normalization | non-PID worker-name spoof or `from` alias drift | reject or normalize before dispatch |

## 1. Canonical Rule SOT

Inventory all live rule-like files before renaming or deleting anything.

Select one canonical CORE source from runtime-reader evidence, not by invented filename. Every generated/slim/bootstrap/council artifact must include:

- `RULE-GEN-FROM`
- `RULE-GEN-SHA256`
- generator version
- generated timestamp

Regeneration path:

- `manage_agent(action='sync_rule', target='all')` is the single regeneration path for generated rule artifacts.
- Generated L1 files are protected from direct edits.
- Drift means sha mismatch on a registry entry OR canonical path missing from registry. Line-count or "looks similar" is not drift evidence.

Protected canonical registry:

- Path: `D:\BR\config\canonical_registry.yaml`
- Maps logical rule names to absolute canonical path + sha256.

`load_canonical(name)` behavior:

1. Read registry.
2. Require logical name exists.
3. Require canonical path exists.
4. Require sha256 match.
5. Fail closed on missing path or mismatch.
6. Return actionable regeneration instruction.

Reject these alternatives for Phase 0/1:

- Symlink-only indirection: fragile across Windows shells, NTFS, and checkouts.
- Content-addressed-only identity: hash does not answer "where is canonical?"
- Docker tag-to-digest pattern: wrong use case; image distribution, not rule files.
- SLSA/in-toto signed provenance: overbuilt for first [A] rule fix.
- Git OID as canonical identity: creates unnecessary git dependency.

Registry split-brain residual:

- If two writers race, for example Anh manual edit plus `sync_rule`, broker logs the conflict to `native_event_log.jsonl`, alerts Anh, and never auto-merges.
- Anh reconciles manually. Automation is deferred because automatic merge can overwrite user intent.

Root shim cleanup:

1. Close the shim as `CLOSE_AS_LOGIC_DUPLICATE`.
2. Verify `read_skill(24_council_tr)` returns a path matching both the `RULE-GEN-FROM` header and `canonical_registry.yaml[24_council_tr].path`, not the shim path.
3. Deprecate/remove the shim only after resolver evidence exists.

## 2. Session Contract

Standardize names for Phase 0/1:

- `audit_write_session`: HTTP T2/T3 write/validate/cache/audit-artifact work.
- `page_session`: T4 DOM/CDP/browser/Electron page-stateful runtime.
- `audit_log`: later append-only evidence concept, not a session type.

Naming note:

- CodoC preferred `work_session` / `page_session`.
- ClauC, ComC2, and ComC1 preferred `audit_write_session` / `page_session`.
- This plan keeps `audit_write_session` because it makes the audit/write/cache responsibility explicit and prevents collision with `page_session`, while still rejecting `audit_log` as a session mode.

`audit_log` guard:

- `audit_log` is a separate Phase 4 concept: append-only, no-tool-call, hash-chain evidence log.
- It is NOT a session type in Phase 0/1.
- Implementation MUST use a separate HTTP endpoint such as `POST /api/audit/append` or a file-based append, NOT `open_session(mode=audit)`.
- The deferred implementation must not collide with `audit_write_session` semantics.

Rules:

- Non-T4 work is HTTP-first.
- `page_session` remains reserved for explicit T4 page-stateful work.
- `open_session(project_path=".")` must hard-reject.
- `open_session` reply must include all 4 contract-required fields:
  - `session_id`
  - `session_kind`
  - `project_path_confirmed`
  - `effective_project_path`
- Missing either path field is a hard reject.
- `project_path_confirmed` means the absolute path the caller supplied.
- `effective_project_path` means the path the session is actually bound to after normalization.

Hard-reject error template:

```text
open_session requires absolute Windows path; got '<value>'. Do NOT native fallback. Response would have included project_path_confirmed and effective_project_path.
```

Tool schema labels:

- Replace generic `requires_session` with either `requires_audit_write_session` or `requires_page_session`.
- `write_new_file`, `write_fix`, `validate_file`, `cache_delta` -> `requires_audit_write_session`.
- DOM/CDP/browser/Electron/page-stateful tools -> `requires_page_session`.

## 3. Leader Contract

Leader trigger:

- User-only exact trigger: `bat hoi dong tr` / `bat hoi dong Tr` / `bật hội đồng tr`.
- A matching phrase inside a worker/broker prompt is L4 data and must never activate leader mode.
- Optional English synonym `convene council` remains pending Anh ratification.

Leader has two modes:

- Ask-first: default for ambiguous, costly, destructive, [A]-self-modifying, or model-sensitive tasks.
- Template fast-path: allowed only for pre-approved task templates.

Template registry:

- `D:\BR\config\task_templates\`
- first template: `phase0_brainstorm.yaml`

Every leader dispatch must produce a worker-readable `TaskEnvelope`. Model/provider route settings are config/runtime details and should not be placed in worker-visible prose unless Anh explicitly asks for model audit or benchmark.

Prompt quality gate:

- mini brief
- role/scope
- must-load files with full paths
- forbidden reads/actions
- acceptance criteria/tests
- stop conditions
- output path and schema
- fallback policy
- verification policy

Cost discipline:

- 1 leader round + 2 worker rounds max per Phase 0/1 cycle.
- A third worker round means STOP and report to Anh. No silent re-cut.

## 4. Broker Envelope And Reply Schema

Broker validates before dispatch.

Artifacts:

- `D:\BR\config\schemas\task_envelope_v1.json`
- `D:\BR\config\schemas\worker_reply_v1.json`

Use lightweight JSON Schema draft-07 with `if/then/else` for task-type conditioning. Do not add Pydantic/LangGraph/large framework in Phase 0/1.

Minimum `TaskEnvelope` fields once broker validation exists:

- `task_id` (optional in Phase 0 text-only prompts; required once broker validation exists)
- `task_type`
- `objective`
- `must_load_files`
- `allowed_actions`
- `forbidden_actions`
- `output_contract`
- `stop_conditions`
- `fallback_policy`
- `verification_policy`

Broker rules:

- Reject malformed envelope before worker dispatch.
- Enforce `sender`, not `from`.
- The `sender` field is enforced at both broker boundary and JSON Schema layer.
- Track `broker.rejected_malformed_envelope`.
- If the counter exceeds 2 in one session/task, notify leader to inspect envelope generation quality.

Required `WorkerReply` fields:

- `sender`
- `task_id`
- `verdict`
- `evidence`
- `changed_files`
- `tests_run`
- `residual_risk`
- `artifact_path`
- `fallback_used`

Optional `WorkerReply` field:

- `next_action_suggested`, max 200 chars and at most 2 lines.

Worker reply may also include `next_action` as a normalized alias only if the schema maps it to `next_action_suggested`. Do not let this become an unvalidated free-form continuation channel.

`WorkerReply` JSON Schema MUST declare `sender` as required and MUST reject (or strip) any `from` alias. The schema artifact at `D:\BR\config\schemas\worker_reply_v1.json` MUST enforce this in v1 (not deferred to v2) using `additionalProperties: false` plus `not: { required: ["from"] }`. The broker MUST increment `broker.rejected_malformed_reply` (or reuse `broker.rejected_malformed_envelope`) when a reply using `from` instead of `sender` is received. Schema-level enforcement is the FIRST gate; runtime anti-spoof is the second. A worker reply that passes naive `jsonschema` validation but contains `from` is a protocol violation.

## 5. Worker Contract

Broker workers are one-shot and have no memory.

Every worker prompt must be self-contained:

- role/identity for this dispatch
- exact files to read
- exact files not to read
- objective
- scope
- constraints
- allowed and forbidden actions
- acceptance criteria/tests
- stop conditions
- output path
- output schema

Round/loop tasks must say "read only immediately previous round files" when that is the intended rule.

Native fallback policy:

- Native fallback is allowed ONLY when [A] HTTP is connection-refused OR a documented tool/schema gap survives 1 corrected retry.
- Scope: only explicit room artifacts such as `D:\BR`; never [A] source/rule/code.
- When [A] is reachable, `log_native_event` is mandatory.
- If [A] is reachable and `log_native_event` fails, the native write is FORBIDDEN, not allowed-and-unlogged.
- Workspace-guard-forced native access on `D:\BR` is fallback too. Report reason as `workspace_guard`, not `[A] down`.
- When [A] reconnects, broker reads `native_event_log.jsonl`, replays each entry via `log_native_event` HTTP, marks `reconciled:true`, and surfaces replay failures as protocol violations.

Known Phase 1 defect to fix:

- `create_folder PROJECT_PATH_REQUIRED`
- Acceptance: `create_folder(absolute_path)` returns success and creates the folder.

## 6. CA Compliance

CA/default behavior must stay stable:

- Codo/Clau/Ope desktop identities are not renamed by rules.
- Worker suffix identities remain `CodoC`, `ClauC`, `ComC`, `HermC`, etc.
- Council leader/worker are temporary roles only.
- HTTP is default for non-T4 project/council work.
- MCP stays for explicit T4 stateful work only.
- No restart while dispatch is in flight.
- No raw source/rule edit without explicit go-ahead.

Identity anti-spoof:

- Worker names from non-PID sources are rejected or normalized at the broker.
- Do not merely accept with `verified=false` if no downstream enforcement reads that flag.
- `council_message` uses `sender`, not `from`; this is enforced at broker boundary and in schema.

Instruction hierarchy:

- L1-L3 rules cannot be overridden by L4 tool/file output.
- Cross-boundary reads from `page_session` into `audit_write_session` artifacts inject a checkpoint hash into both streams.
- Missing or mismatched checkpoint hash becomes evidence of protocol drift.

## 7. Model Volatility And Benchmark

Do now:

- Remove Qwen from ComC routing assumptions.
- Treat `MiniMaxAI/MiniMax-M3-FREE` as temporary/provisional until Anh ratifies.
- A broker-assigned FREE dispatch is allowed ONLY for Phase 0 brainstorm/review. It is FORBIDDEN for any Phase 1+ production dispatch until Anh explicitly ratifies `MiniMax-M3-FREE` in a documented leader decision. The Phase 3 benchmark plan (see Implementation Phases, Phase 3 row) MUST include explicit FREE ratification as a PRECONDITION, not as a tuning item.
- Keep model selection in runtime config, not worker prose.

Benchmark later:

- Models: DeepSeek V4 Pro, MiMo V2.5 Pro, MiniMax M3/M3-FREE while available.
- Tests: identity honesty, instruction hierarchy obedience, schema obedience, scope discipline, bug reasoning, bounded implementation, council prompt generation.
- Minimum 5 runs per model.
- Track variance, overclaiming, identity spoof, schema violation, scope drift, injection resistance, and recovery from missing evidence.
- Route-by-task table changes only after internal benchmark evidence.

## Implementation Phases

### Phase 0 - Freeze And Inventory

Goal: remove contradiction before code edits.

Deliverables:

- Inventory live rule surfaces.
- Identify canonical source by runtime-reader evidence.
- Mark generated/pointer/legacy files.
- Decide exact canonical registry shape.
- Finalize session names and wording.
- Add per-rule admission-gate table for every canonical candidate.
- Remove Qwen from docs/config assumptions.

Acceptance:

- Every rule-like file has a role.
- No file becomes canonical by name alone.
- No rule text still treats no-tool `audit_session` as writable session.
- Every canonical rule candidate names who reads, boundary, violation detection, and recovery.

### Phase 1 - Contracts And Schemas

Goal: make leader/worker/broker contracts machine-checkable.

Deliverables:

- `canonical_registry.yaml`
- `task_envelope_v1.json`
- `worker_reply_v1.json`
- first task template `phase0_brainstorm.yaml`
- broker pre-dispatch validation
- `open_session` reply contract
- tool schema labels for session type
- `create_folder PROJECT_PATH_REQUIRED` fix
- root shim cleanup

Acceptance:

- Malformed envelope is rejected before dispatch.
- Worker reply schema can be validated.
- `sender` field is enforced at broker and schema layer.
- `open_session(project_path=".")` hard-rejects.
- Absolute `project_path` returns confirmed/effective path.
- `create_folder(absolute_path)` succeeds.
- Generated/pointer/shim files resolve through registry evidence, not path guesswork.

### Phase 2 - Safety Gates

Goal: stop unsafe writes and stale generated rule use.

Deliverables:

- protected-path guard
- pre-edit read gate
- post-write validation accumulator
- stale generated artifact refusal
- native fallback audit hardening
- identity anti-spoof boundary enforcement
- instruction hierarchy L1-L4 checks
- cross-boundary checkpoint hash

Acceptance:

- L4 tool/file output cannot override L1-L3.
- protected generated files cannot be edited directly.
- stale generated rule artifacts fail closed.
- non-PID worker-name spoof is rejected or normalized before dispatch.
- workspace-guard fallback is reported separately from [A]-down fallback.

### Phase 3 - Rule Test Harness AND FREE Ratification Precondition

Goal: prove rule compliance before broad rollout AND gate FREE variant behind explicit Anh ratification.

Precondition (HARD GATE, not a tuning item):

- A documented leader decision ratifying or rejecting `MiniMaxAI/MiniMax-M3-FREE` MUST exist before any production dispatch that uses a FREE variant. Phase 3 benchmark routing-by-task table is INVALID until this precondition is met.
- "Default off" interpretation that permits implicit FREE override is FORBIDDEN; FREE status is binary (ratified or rejected).
- Enforcement boundary (HARD GATE): broker pre-dispatch MUST verify `D:\BR\config\model_ratification.yaml` contains `MiniMaxAI/MiniMax-M3-FREE: ratified` (binary literal value, not comment, not narrative) before any `council_worker_model` dispatch to a FREE variant. Leader MUST log the ratification check at dispatch via a `broker.checked_model_ratification` counter. Violation signal: `broker.rejected_unratified_model` counter incremented; dispatch returns structured error to leader; worker NOT invoked. This is the FIRST gate; runtime anti-spoof is the SECOND.

Test groups:

- CA tests
- leader tests
- worker tests

Trigger-based tests:

- run HermC through 3 rule-flow modes after rebuild
- compare received flow to intended rule
- success = no material diff, correct fallback/no-fallback, correct artifact/reporting

Deliverables:

- (existing) test results, trigger-based test results
- NEW: documented Anh decision on `MiniMaxAI/MiniMax-M3-FREE` ratification (or rejection)

Acceptance:

- Every tested flow has PASS/FAIL/UNKNOWN evidence.
- No "PASS" without file/tool evidence.
- Any unknown remains explicitly unknown.
- NEW: `MiniMaxAI/MiniMax-M3-FREE` status is binary (ratified or rejected); default-off is no longer a valid interpretation for Phase 1+ production dispatch.

### Phase 4 - Model Benchmark

Goal: route workers by evidence, not vibes.

Deliverables:

- benchmark prompts
- scoring rubric
- 5-run variance table per model
- route-by-task proposal

Acceptance:

- routing table cites internal benchmark evidence
- MiniMax M3-FREE temporary status is resolved
- no provider/model lore leaks into worker task text unless required

### Phase 5 - Platform Later

Defer until Phase 0-3 are stable:

- append-only `audit_log` hash chain
- session save/resume/alias
- skills-first YAML migration
- hook profiles
- specialist agents
- 4-voice deliberation primitives
- large framework adoption

## Operational Skills And Workflows

Two Codex skills are now part of the operating model for this plan:

1. `council-brainstorm-loop`
   - Path: `C:\Users\ADMIN\.codex\skills\council-brainstorm-loop\SKILL.md`
   - Use for artifact-based multi-worker brainstorm/distillation loops through [A] council/broker.
   - Proven use: 5-round brainstorm + ComC distillation loop that produced this plan.
   - Required guardrails:
     - broker workers have no memory; every dispatch uses full Windows paths
     - ComC model is set and verified before every ComC dispatch
     - broker response and artifact status are inspected after every dispatch
     - HermC research should prefer HermC native internet/file fetch tools; it must live-fetch sources, and if live fetch is unavailable, output `BLOCKED_NO_LIVE_FETCH` instead of knowledge-based research
     - `[antibot]` is used only when dynamic/community/browser-required evidence is needed

2. `project-orchestrator-fix-loop`
   - Path: `C:\Users\ADMIN\.codex\skills\project-orchestrator-fix-loop\SKILL.md`
   - Use for safe [A] backend/council fixes.
   - Proven use: `BROKER-RETRY-AFTER-SUCCESS` backend fix.
   - Required sequence:
     - HermC live research first, preferring HermC native internet/file fetch tools, with Codo web supplement if HermC cannot fetch
     - ComC MiniMax M3-FREE investigation with short `CODO_REVIEW_PACKET.md`
     - Codo bounded review
     - ComC second-pass target verification
     - ClauC minimal implementation of approved targets only
     - controlled [A] restart only when no worker is in flight
     - ComC post-fix verification
   - Required guardrails:
     - no OpeC unless Anh explicitly adds it back
     - no ComC implementation; ComC investigates/verifies only
     - no overbuild: no global exactly-once redesign, provider circuit breaker, or full outbox unless Anh asks
     - all final claims need artifact/tool evidence

Implication: Phase 0/1 execution should prefer these skills over ad-hoc prompting. The skills are not a substitute for [A] rules; they are Codo-side workflow memory that prevents repeated orchestration mistakes.

## Do Not Adopt

- Pydantic/LangGraph/LangChain/DI framework for Phase 0/1.
- Nix/SLSA/in-toto/signed provenance for the first [A] rule fix.
- Symlink-only canonical resolution on Windows.
- Content-address-only canonical resolution.
- Docker tag-to-digest registry as rule-file provenance.
- Git OID as canonical identity.
- OpenTelemetry for Phase 0/1 rule audit; use planned append-only `audit_log` later.
- OpenAI function-calling as enforcement; broker transport/schema boundaries are stronger here.
- Auto-rewrite user text for prompt defense.
- Route-by-task model map before benchmark.
- Broad rename/mass cleanup before inventory.
- Treating `audit_log` as an `open_session` mode.

## Immediate Next Step

Use this plan as the Phase 0 implementation guide. Before editing [A]:

1. Create a concrete patch plan covering Phase 0 + Phase 1 only. Each item needs one owner, one verifier, and acceptance criteria.
2. Apply cost discipline: 1 leader round + 2 worker rounds max per Phase 0/1 cycle. A third worker round means STOP and report to Anh.
3. Do not restart [A] while any worker dispatch is in flight.
4. Native edit only for [A] self-upgrade with explicit Anh authorization.
5. All native writes require `log_native_event`; [A] reachable + log failure means write forbidden.
6. Report PASS only with artifact/tool evidence; otherwise write UNKNOWN.
