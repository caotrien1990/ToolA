# Draft Breakdown Plan

Project: `breakdown_devine_plan`

Source plan:
`D:\BR\4_Output\rebuild_rule\DIVINE_RULE_SYSTEM_BUILDUP_PLAN_v2.md`

Loop:
`D:\BR\3_Working\breakdown_devine_plan\loop_draft_plan`

Status: Draft from 5-round `loop_draft_plan`. This is not the final output; it is the input for `loop_feedback_plan`.

## 1. Synthesis Decision

Winner base: `comc1_Round4`, peer-received average `8.61/10`.

Imported strengths:
- `comc2`: strict schema, generated-header, counter matrix, `sender`/`from` enforcement, P1.7a bundle sanity, and dependency hardening.
- `comc3`: v2-faithful phase boundaries, Phase 0 read-only, Phase 4 as model benchmark, cost/counter as guardrails, and Phase 5 stub-only.

Rejected weaknesses:
- Any Phase 0 row that edits files.
- Any Phase 4 rewrite that turns the model benchmark into a cost-only phase.
- Any HermC request for static local schema/code facts that OpeC can scan.
- Any worker-visible model routing prose that belongs in runtime config.
- Any Phase 5 implementation row.

## 2. Cross-Phase Guardrails

| ID | Guardrail | Harness | Acceptance |
|---|---|---|---|
| G0 | No source edit before explicit implementation approval. | H-permission | No source/rule/code diff during planning. |
| G1 | Phase 0 is read-only reports only. | H-p0-readonly | Phase 0 outputs are inventory/report files; no row claims a file was changed. |
| G2 | `sender` is mandatory; `from` is forbidden. | H-schema + H-broker | Malformed envelope/reply is rejected and counted. |
| G3 | Generated L1 artifacts require immutable headers. | H-hdr | `RULE-GEN-FROM`, `RULE-GEN-SHA256`, generator version, timestamp exist. |
| G4 | `open_session(project_path=".")` hard rejects. | H-open-session | Absolute-path success returns 4 fields; dot path returns structured refusal. |
| G5 | FREE production route is blocked unless ratified. | H-rat | `ratified` allows; `rejected` and missing file block. |
| G6 | PASS requires evidence. | H-evidence | PASS rows cite file/tool/log evidence; unknown stays `UNKNOWN`. |
| G7 | Cost and counters are guardrails, not a replacement phase. | H-cost + H-cnt | Cost/counter evidence appears in sign-off packets. |
| G8 | HermC is for live external/trigger evidence only. | H-hermc | Static local scans are routed to OpeC. |
| G9 | Phase 5 remains deferred stub only. | H-p5 | No owner/verifier/harness rows for Phase 5 implementation. |
| G10 | No restart while workers are in flight. | H-runtime | Runtime ledger shows no active dispatch before restart. |

## 3. Phase 0 - Freeze And Inventory

Objective: create a read-only evidence base before any implementation.

| Step | Work | Dependency | Owner | Verifier | Harness | Acceptance |
|---|---|---|---|---|---|---|
| P0.1 | Inventory canonical rule, skill, workflow, broker, and generated artifacts. | none | OpeC | Codo | H-inventory | `D:\BR\inventory\P0.1_rule_inventory.md` exists with paths, role labels, sha/header or `UNKNOWN`. |
| P0.2 | Capture canonical evidence for `get_rule_core_text`, `read_skill(24_council_tr)`, registry references, and root shim. | P0.1 | OpeC | ComC2 | H-resolver | Every target has path, purpose, owner, sha/header, and mismatch note. |
| P0.3 | Build admission gate matrix: who reads what, enforcement point, violation signal, recovery. | P0.1 | Codo | Anh | H-admission | Matrix has no blank enforcement/recovery cells. |
| P0.4 | Freeze session vocabulary and current `open_session` response shape. | P0.2 | OpeC | ComC2 | H-session | `D:\BR\inventory\P0.4_session_vocab.md` records current fields and gaps. |
| P0.5 | Create implementation admission checklist. | P0.3 | Codo | Anh | H-checklist | Checklist blocks implementation until Phase 0 sign-off. |
| P0.6 | Classify Qwen/FREE routing hits as `remove`, `update`, `defer`, `historical`; FREE hits as `provisional`, `ratified`, `rejected`, `missing`. | P0.1 | OpeC | ComC2 | H-model-scan | Report lists every hit with classification and no edits. |
| P0.7 | Confirm root shim and duplicate source risk. | P0.2 | OpeC | Codo | H-shim | Root shim risk is PASS/FAIL/UNKNOWN with evidence. |
| P0.8 | Produce freeze report. | P0.1-P0.7 | Codo | ComC1 | H-freeze | `D:\BR\reports\P0.8_phase0_freeze_report.md` has PASS/FAIL/UNKNOWN table. |
| P0.9 | Record Phase 5 deferral decision. | P0.8 | Codo | Anh | H-p5 | Single decision note: Phase 5 is stub-only in this build. |

## 4. Phase 1 - Contracts And Schemas

Objective: formalize contracts before safety gates and harnesses depend on them.

| Step | Work | Dependency | Owner | Verifier | Harness | Acceptance |
|---|---|---|---|---|---|---|
| P1.1 | Define `canonical_registry.yaml`. | P0.8 | ClauC | ComC2 | H-registry | Registry maps canonical files to owner, role, sha, and generated status. |
| P1.2 | Implement `load_canonical` resolver path. | P1.1 | ClauC | Codo | H-resolver | Resolver refuses unknown aliases and logs canonical path. |
| P1.3 | Define `task_envelope_v1.json`. | P1.1 | ClauC | ComC2 | H-schema | `sender` required; `from` forbidden; `additionalProperties:false` unless documented exception. |
| P1.3a | Define generated-header rule. | P1.1 | ClauC | ComC2 | H-hdr | Generated artifacts include source path, source sha, generator version, timestamp. |
| P1.4 | Define `worker_reply_v1.json`. | P1.3 | ClauC | ComC2 | H-schema | Reply schema rejects missing sender, malformed status, and extra undeclared fields. |
| P1.5 | Define `phase0_brainstorm.yaml` template. | P1.3 | ClauC | Codo | H-template | Template includes stop conditions and broker counters. |
| P1.6 | Add broker pre-dispatch validation. | P1.3-P1.5 | ClauC | ComC2 | H-broker | Counters increment: `broker.rejected_malformed_envelope`, `broker.rejected_malformed_reply`. |
| P1.7 | Harden `open_session` contract. | P0.4, P1.3 | ClauC | ComC2 | H-open-session | Dot path hard-rejects; absolute-path success returns `session_id`, `session_kind`, `project_path_confirmed`, `effective_project_path`. |
| P1.7a | Bundle/import sanity pre-flight. | P1.7 | OpeC | ComC1 | H-bundle | Bundle import check passes before enforcement flip. |
| P1.8 | Split tool session labels. | P1.7 | ClauC | Codo | H-tool-label | Tools declare `requires_audit_write_session` or `requires_page_session`. |
| P1.9 | Fix `create_folder` contract if scan confirms drift. | P1.8 | ClauC | ComC2 | H-folder | Contract and implementation match; no silent path fallback. |
| P1.10 | Remove or update root shim after registry is active. | P1.1-P1.2 | ClauC | OpeC | H-shim | Shim state matches registry decision. |
| P1.11 | Phase 1 sign-off packet. | P1.1-P1.10 | Codo | ComC1 | H-signoff | Sign-off cites schema tests, broker tests, and open-session tests. |
| P1.12 | Phase 1 acceptance report. | P1.11 | Codo | Anh | H-report | Report has PASS/FAIL/UNKNOWN table and residual risks. |
| P1.13 | Post-freeze Qwen cleanup edit. | P0.6, P1.12, Anh approval | ClauC | ComC2 | H-model-cleanup | Only approved `remove/update` rows are changed; historical/defer remain. |

## 5. Phase 2 - Safety Gates

Objective: make unsafe writes and cross-boundary drift fail closed.

| Step | Work | Dependency | Owner | Verifier | Harness | Acceptance |
|---|---|---|---|---|---|---|
| P2.1 | Protected-path guard. | P1.8 | ClauC | Codo | H-path | Protected paths reject unauthorized writes with evidence. |
| P2.2 | Pre-edit read gate. | P2.1 | ClauC | ComC2 | H-read-before-write | Write attempts without fresh read are refused. |
| P2.3 | Post-write validation accumulator. | P2.2 | ClauC | Codo | H-validate | Validation failures accumulate and block PASS. |
| P2.4 | Stale generated artifact refusal. | P1.3a, P2.3 | ClauC | ComC2 | H-stale | Sha/header mismatch refuses stale generated outputs. |
| P2.5 | Native fallback audit hardening. | P2.1-P2.4 | ClauC | ComC2 | H-fallback | Reason field enum: `reachable_log_ok`, `reachable_log_fail`, `a_refused`, `workspace_guard`. |
| P2.6 | Identity anti-spoof. | P1.3-P1.6 | ClauC | ComC2 | H-identity | Counter `broker.rejected_spoofed_identity` increments on spoof attempt. |
| P2.7 | L1-L4 hierarchy guard. | P1.1, P2.6 | ClauC | Codo | H-hierarchy | `violation_signal:hierarchy_drift` appears on hierarchy violation. |
| P2.8 | Cross-boundary checkpoint hash. | P2.3, P2.7 | ClauC | HermC | H-checkpoint | `page_session` to `audit_write_session` boundary preserves checkpoint hash. |
| P2.9 | Phase 2 sign-off packet. | P2.1-P2.8 | Codo | ComC1 | H-signoff | Packet includes risk register snapshot and counter evidence. |

## 6. Phase 3 - Rule Harness And FREE Ratification

Objective: validate CA/default, leader, worker, HermC trigger, and FREE production gates.

| Step | Work | Dependency | Owner | Verifier | Harness | Acceptance |
|---|---|---|---|---|---|---|
| P3.0 | Bootstrap `D:\BR\config\model_ratification.yaml`. | none | Codo | Anh | H-rat | Literal `ratified` or `rejected`; missing file fixture exists. |
| P3.1 | CA/default first-prompt rule load tests. | P1.11 | ClauC | Codo | H-ca | First prompt loads core rule once and respects transport policy. |
| P3.2 | Council leader trigger tests. | P1.11 | ClauC | ComC2 | H-leader | Exact `bật hội đồng tr` trigger enters leader path only from user. |
| P3.3 | Worker reply validation tests. | P1.4, P1.6 | ClauC | ComC2 | H-worker | Worker reply schema rejects malformed/missing sender. |
| P3.4 | HermC trigger-flow evidence. | P3.1-P3.3 | HermC | Codo | H-hermc | 3-mode trigger-flow diff is produced or `BLOCKED_NO_LIVE_FETCH`. |
| P3.5 | FREE production gate. | P3.0, P1.6 | ClauC | ComC2 | H-rat | Counters: `broker.checked_model_ratification`, `broker.rejected_unratified_model`. |
| P3.6 | Evidence report. | P3.1-P3.5 | Codo | Anh | H-report | No PASS without cited evidence. |
| P3.7 | Phase 3 sign-off. | P3.6 | Codo | ComC1 | H-signoff | FREE hard gate and trigger tests are signed off or blocked. |

## 7. Phase 4 - Model Benchmark

Objective: run benchmark and route-by-task proposal after Phase 3 evidence and FREE status are resolved.

Cost/counter discipline is cross-phase evidence here, not the theme of Phase 4.

| Step | Work | Dependency | Owner | Verifier | Harness | Acceptance |
|---|---|---|---|---|---|---|
| P4.0 | Confirm Phase 3 evidence and FREE binary status. | P3.7 | Codo | ComC2 | H-precheck | Candidate list cites ratification state before benchmark starts. |
| P4.1 | Prepare benchmark prompt set. | P4.0 | CodoC | ComC2 | H-bench | Prompt set covers CA, leader, worker, HermC/OpeC, failure cases. |
| P4.2 | Define scoring rubric. | P4.1 | CodoC | ComC1 | H-rubric | Rubric scores correctness, rule obedience, evidence, cost, and failure clarity. |
| P4.3 | Run 5-run variance table per model/route. | P4.2 | CodoC | ComC2 | H-variance | Five runs per candidate or explicit blocked reason. |
| P4.4 | Produce route-by-task proposal. | P4.3 | Codo | Anh | H-route | Proposal is evidence-based and does not override ratification gate. |
| P4.5 | Generate `cost_discipline.md`. | P1.3a, P4.2-P4.3 | Codo | OpeC | H-cost | Generated header exists; cost guidance references benchmark evidence. |
| P4.6 | Add cost-aware dispatch hint where allowed. | P4.4-P4.5 | ClauC | ComC2 | H-dispatch | Hint never routes unratified FREE production dispatch. |
| P4.7 | Phase 4 sign-off. | P4.1-P4.6 | Codo | Anh | H-signoff | Sign-off packet has benchmark, variance, route proposal, cost/counter evidence. |

## 8. Phase 5 - Deferred Platform Stub

Phase 5 is a stub only in this plan. It records deferred platform work from v2 and must not create implementation rows, owners, harnesses, or acceptance criteria until Anh explicitly opens that project.

## 9. Support Requests

HermC requests, max 3:
- HR-1: 3-mode trigger-flow evidence after Phase 3 harness exists; output diff or `BLOCKED_NO_LIVE_FETCH`.
- HR-2: dynamic/browser evidence for `open_session(project_path=".")` only if static evidence is insufficient; otherwise skip.
- HR-3: checkpoint-hash live flow only after P2.8 exists; otherwise skip.

OpeC requests, max 3:
- OSR-1: canonical resolver/root-shim/generated-header evidence.
- OSR-2: session/tool/schema inventory for `open_session`, write tools, validate tools, `create_folder`, DOM/CDP tools.
- OSR-3: broker envelope/reply/identity/counter wiring scan.

## 10. Acceptance Shortlist

| ID | Acceptance |
|---|---|
| AC0 | Phase 0 has no edits. |
| AC1 | Canonical registry and resolver are authoritative. |
| AC2 | Schemas are strict; `sender` required; `from` forbidden. |
| AC3 | Generated artifacts carry source and sha headers. |
| AC4 | Broker rejects malformed envelope/reply with counters. |
| AC5 | `open_session(".")` hard-rejects. |
| AC6 | Tool session labels are explicit. |
| AC7 | Protected paths and pre-edit read gates fail closed. |
| AC8 | Post-write validation accumulates failures. |
| AC9 | Native fallback is audited with reason fields. |
| AC10 | Identity spoof attempts are rejected and counted. |
| AC11 | FREE production dispatch is blocked unless ratified. |
| AC12 | PASS requires evidence; unknown remains unknown. |
| AC13 | Phase 4 benchmark includes prompt set, rubric, 5-run variance, route-by-task proposal. |
| AC14 | Phase 5 remains stub-only. |

## 11. Known Risks

| Risk | Severity | Mitigation |
|---|---|---|
| FREE production ambiguity | HIGH | P3.0 binary ratification + P3.5 broker gate. |
| Broker validation gap | HIGH | P1.6 strict validation + counters. |
| `open_session` path ambiguity | HIGH | P1.7 hard contract + H-open-session. |
| Root shim split-brain | HIGH | P0.7 evidence + P1.10 controlled cleanup. |
| Phase 4 drift to cost-only | MEDIUM | P4 remains benchmark; cost/counter are guardrails. |
| Generated-file self-reference | MEDIUM | P4.5 depends on P1.3a. |
| HermC live evidence unavailable | MEDIUM | `BLOCKED_NO_LIVE_FETCH`, no inferred PASS. |
| Phase 5 scope creep | MEDIUM | P0.9 decision + Phase 5 stub-only rule. |

## 12. Loop Evidence

Round artifacts:
- `D:\BR\3_Working\breakdown_devine_plan\loop_draft_plan\round1`
- `D:\BR\3_Working\breakdown_devine_plan\loop_draft_plan\round2`
- `D:\BR\3_Working\breakdown_devine_plan\loop_draft_plan\round3`
- `D:\BR\3_Working\breakdown_devine_plan\loop_draft_plan\round4`
- `D:\BR\3_Working\breakdown_devine_plan\loop_draft_plan\round5`

Final support artifacts:
- `D:\BR\3_Working\breakdown_devine_plan\loop_draft_plan\final\SCOREBOARD.md`
- `D:\BR\3_Working\breakdown_devine_plan\loop_draft_plan\final\WINNER_BASE_PLAN.md`

Council/broker investigation note:
- ComC direct CLI with `--yolo` was viable.
- Broker issue was traced to wrapper/ARGV/workspace/write-permission edge cases, not MiniMax CLI inability.
- Current safe broker workflow: stage D:\BR inputs into `[A]\.council\tmp_*`, dispatch self-contained full paths, let ComC return artifact inline if write is blocked, then Codo saves to official D:\BR output.

