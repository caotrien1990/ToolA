---
name: verify-new-function
description: "Run the post-build verification pipeline after a new function/module/fix is implemented. Trigger when Anh says 'chạy skill verify' / 'chay skill verify' or asks to verify a newly built function with the locked council process: detailed code/function/logic audit, consolidated final audit, ClauC devine audit, scoped fix verification, benchmark, and guardrail update."
---

# Verify New Function

Use this skill immediately after a new function/module/fix is built and before declaring it safe. The exact user trigger is `chạy skill verify`; accept `chay skill verify` as an encoding-safe alias. The goal is to prevent leader drift by forcing a fixed five-phase evidence pipeline.

Default artifact root:

```text
C:\Users\ADMIN\.claude-mem\staging\q-hardening\<function_or_step_id>\
```

If the work is not ToolQ/Q, still use the same structure under a bounded staging folder chosen by Anh or the active project.

## Hard Rules

- Do not skip a phase.
- Do not let one worker report replace a lock.
- Do not use ClauC for broad scan, context gathering, or new-code creation.
- Do not implement new ToolQ runtime code while verifying this skill or a verification-only scope.
- Do not let Q/local-LLM/LLM hits become verified truth without direct evidence.
- Do not call destructive Q endpoints: `filter_delete`, `purge`, `undo`.
- Codex Desktop snapshot guard: do not run snapshot-like or long-running recovery-sensitive work from the Codex Desktop shell while the Windows PowerShell `shell_snapshot` bug is active. This includes live DB/Chroma/raw memory snapshot/copy, local-LLM/llama real runs, GPU/VRAM watchdog loops, and process recovery checks. Route those tasks to council workers or [A]-managed execution and verify by artifacts. If a Codex shell command aborts with snapshot/process symptoms, stop and re-dispatch through council.
- If evidence is missing, write `unknown / not verified`.

## Worker Routing (rev6 — Anh override 2026-07-13, model-anchored mission)

- Leader (CA running this skill): coordinator, main builder/fixer, adjudicator. Codes T2 writes directly. Escalation target when all workers fail.
- ClauC (Supreme Dev): judgement or scoped existing-code bug fix with a narrow evidence pack. **Mandatory devine audit**. Master dev only if the same issue fails council retry more than 3 times. Scarcity gate: no broad scan/read/context gathering or new-code creation. If ClauC is limited/out, fallback to Leader. Use `sender="user"` for same-identity dispatch.
- OpeC5 = HermC5 = KiloC5 (glm-5.2): High Dev — high-judgement dev code, plan implementation, verify. Parallel-safe (ope≠hermes≠kilo, 3 agent queues).
- OpeC7 = HermC7 = KiloC7 (deepseek-v4-pro): Mid Dev + Scanner/Bug hunter/Edge case/Tester — mid-judgement dev code, breakdown, bug hunting, edge case discovery, test fixtures, test verify. Dual role.
- deepseek-v4-pro mission (OpeC7+HermC7+KiloC7, 3-shard parallel): edge-case finder, bug finder, dev-code auditor, final-console verifier, fix verifier (Mid Dev judgement). ComC1 is removed; its function moved to this 3-shard mission. Split large audit into 3 shard (OPEC7/HERMC7/KILOC7 artifacts) for parallel cross-verify; 2-shard (OpeC7+HermC7) for consensus verify.
- ComC3 (mimo-v2.5-pro, single sequential): lower-priority brainstorm/tool/test verifier when the ds-pro mission is unavailable or the task is low risk. Model unique, no shard.
- HermC1 (deepseek-v4-flash-free): Scan data NOT reasoning — data scan, file fetch, literal extraction, path/grep checks. NO reasoning verdict.
- Q librarian DeepSeek v4 Flash: production adjudicator for Q candidate evidence; must follow `Q_LIBRARIAN_GUARDRAILS.md` when relevant.
- Qwen/local LLM: offline candidate-only worker when the specific pipeline asks for it; never authoritative.

Removed: ComC1, ComC2, HermC2, KiloC1. CodoC not used in this skill (Leader runs the pipeline). OpeC2 (nemotron) and OpeC3 (north-mini) removed; map scan+reason slots to the kimi mission (OpeC9+HermC9+KiloC9), narrow factual scan to the ds-flash mission (HermC1+OpeC1), consolidation to OpeC5 (glm-5.2). OpeC1/OpeC4 = legacy fallback only if main roster unavailable.

## Timeout Policy

- `timeout_sec` in `council_message` is an **IDLE/inactivity window** (seconds of silence with no stdout bytes from worker → kill), NOT an absolute wall-clock deadline. `hard_cap` is derived as `timeout_sec × 4` (clamped [1800s, 14400s]) and is the absolute ceiling. `queue_wait_timeout_sec` (default 6000) is the caller wall-clock wait for the reply on the sync path.
- When a worker dispatch returns `timeout` / `[timeout: no final reply]` MORE THAN ONCE: **load leader rule + leader skill again and compare the dispatch method** before any retry:
  1. If the dispatch was done the **wrong way** (missing Q pre_search First-Dispatch gate, prompt not following dispatch template, `timeout_sec` too short for the model/task, provider not verified with `action=get`) → **retry the correct way** (query Q + dispatch template + adequate `timeout_sec` + verify provider). Do not just split — splitting does not fix a wrong dispatch method.
  2. If the dispatch was already done the **correct way** → **split the task smaller and retry**.
- This replaces the old policy "timeout >1 → split task + retry", which did not fix the root cause (a leader dispatching the wrong way would retry the same wrong way).
- Model-specific guidance: HermC5/KiloC5 (glm-5.2 via ltnai) multi-step tasks (≥3 tool calls + write report) have high reasoning latency between tool calls (~230s observed for 6 tool calls, tool execution only ~6s). Pass `timeout_sec=360` for such tasks. OpeC7/HermC7/KiloC7 (deepseek-v4-pro) are faster and stream more; `timeout_sec=180-300` is usually enough.
- Before declaring a timeout a failure, check the worker's artifact/output path: workers may write the report file before emitting the final reply banner, and `_council_clean_timeout` discards raw output. A completed artifact file = task done even if the reply text says `[timeout: no final reply]`.

## Phase 1: Audit Reports

Goal: obtain three independent audit reports over the same completed function scope.

Guardrails:

- All workers scan the same files/functions/tests/artifacts.
- Audit only; no fixes and no source mutation.
- Every finding must include file/function/line or `evidence_missing`.
- Do not use ClauC.
- Audit must be line-aware and function-aware, not a high-level review.

Steps:

1. Leader creates `AUDIT_PACKET.md` with:
   - function/step id;
   - exact changed files/functions/lines;
   - expected behavior;
   - tests already run;
   - known non-goals;
   - output paths.
2. Dispatch the deepseek-v4-pro mission as 3 shard in parallel: OpeC7 writes `OPEC7_DETAILED_AUDIT.md`, HermC7 writes `HERMC7_DETAILED_AUDIT.md`, KiloC7 writes `KILOC7_DETAILED_AUDIT.md`. Same model + different agent = same mission, split task to avoid timeout/overload. Use `mission_create` with 3 tasks (one per shard, each with its own `output_artifact_path`).
3. Dispatch OpeC7 (active scanner) to write `OPEC_SLOT_DETAILED_AUDIT.md`; for extra split POV use another OpeC7 pass or HermC1, and reserve OpeC5 (high dev) for consolidation when possible.
4. Dispatch HermC1 to write `HERMC1_DETAILED_AUDIT.md`.
5. Leader checks all three reports have scope, evidence coordinates, no-fix statement, and no missing output.

Each audit must cover these surfaces:

- changed code lines and nearby context;
- function signatures, parameters, defaults, return type/shape, and exceptions;
- control-flow logic, branching, loops, early returns, and unreachable/dead code;
- data contracts, schema fields, JSON keys, accepted aliases, and rejected fields;
- caller/callee integration, imports, lazy imports, route handlers, CLI flags, and public APIs;
- input validation, malformed input behavior, empty/null cases, and boundary limits;
- error handling, literal traceback/error capture, logging, and failure visibility;
- state mutation, file/DB/network side effects, read-only/write scope, and idempotency;
- concurrency, timeout, retry, process/resource guards, and stale runtime activation;
- encoding, UTF-8 roundtrip, Vietnamese text, Windows paths, escaping, base64, and PowerShell command text;
- security/destructive action risk, SQL/query construction, path traversal, shell injection, and token/key leakage;
- retrieval/ranking/gating behavior if the function affects Q/RAG/search/pre_search;
- tests present, tests missing, weak assertions, test data drift, and real-use coverage;
- regression risk against adjacent functions and old artifacts.

Required audit classifications:

```text
TRUE_BUG_CANDIDATE
TRUE_RISK_CANDIDATE
TEST_GAP_CANDIDATE
DOCS_OR_ACTIVATION_GAP
MISUNDERSTANDING_OR_NEEDS_VERIFICATION
```

## Phase 2: Consolidated Final Audit

Goal: produce one indexed audit with no missing, extra, or badly merged point.

Guardrails:

- OpeC5 (high dev) consolidates only from the three audit reports when available; otherwise use OpeC7 (scanner) and record the fallback.
- No self-invented points.
- Duplicate points are merged, not renumbered twice.
- Weak evidence becomes `needs_verification`, not `bug`.

Steps:

1. OpeC5 writes `CONSOLIDATED_AUDIT.md` when available; otherwise OpeC7 (scanner) writes it and records the fallback.
2. Each point must use:

```text
STT-N
type: bug | risk | test_gap | docs_gap | activation_risk | unknown
severity: P0 | P1 | P2 | P3
sources: OpeC7/HermC7/KiloC7 (ds-pro mission) + HermC1 (ds-flash)
evidence: file:line/function
claim:
impact:
suggested_verification:
dedupe_notes:
```

3. The ds-pro mission verifies consolidated vs all three audit reports (2-shard consensus: OpeC7+HermC7) and writes `DS_PRO_CONSOLIDATED_VERIFICATION_R1.md`.
4. If missing/extra/bad merge exists, OpeC5 or OpeC7 revises to `FINAL_CONSOLIDATED_AUDIT.md`.
5. Loop until the ds-pro mission (OpeC7+HermC7 consensus) writes `DS_PRO_FINAL_CONSOLE_VERIFICATION.md` with:

```text
NO_MISSING_POINTS
NO_EXTRA_POINTS
NO_BAD_MERGES
FINAL_CONSOLE_PASS
```

## Phase 3: Devine Audit (ClauC supreme)

Goal: independent ClauC judgement of every final audit point.

Guardrails:

- Leader must use `[A]`/HTTP evidence access where available.
- Leader does not fix code.
- No `UNKNOWN_NOT_VERIFIED` caused by narrow scope. If unknown remains, it must be because evidence/runtime truly cannot be reproduced.

Steps:

1. Leader dispatches ClauC with full paths:
   - `AUDIT_PACKET.md`;
   - three detailed audits;
   - `FINAL_CONSOLIDATED_AUDIT.md`;
   - exact source/test files;
   - output path `CLAUC_DEVINE_AUDIT.md`.
2. If dispatching the same identity, call council with `sender="user"`.
3. ClauC classifies each STT:

```text
TRUE_BUG
TRUE_RISK
TEST_GAP
MISUNDERSTANDING_NOT_BUG
DUPLICATE_MERGED
OUT_OF_SCOPE
UNKNOWN_NOT_VERIFIED
```

4. Leader verifies `UNKNOWN_NOT_VERIFIED = 0` or justified.

## Phase 4: Fix Verify

Goal: fix only true points, then verify with workers and tests.

Guardrails:

- Fix in small batches ordered by lowest risk and highest blocker value.
- ClauC only receives a narrow evidence pack for a specific existing-code bug.
- Issue outside scope goes to backlog.
- Each batch needs smoke/unit before council verify.

Steps:

1. Leader splits `TRUE_BUG`, `TRUE_RISK`, and `TEST_GAP` into batches.
2. Leader fixes directly unless Anh routes a specific bug to ClauC.
3. If ClauC is used, send:
   - exact files/functions/lines;
   - observed bug;
   - expected behavior;
   - tests to run;
   - non-goals.
4. Leader runs smoke/unit for the batch.
5. The kimi mission (KiloC9, code-focused) verifies the fix and writes `KILOC9_FIX_VERIFY.md`; if KiloC9 is unavailable, use the ds-pro mission (OpeC7) and record the fallback.
6. HermC1 verifies the fix and writes `HERMC1_FIX_VERIFY.md`; if HermC1 is unavailable, use a small OpeC slot and record the fallback.
7. Leader adjudicates feedback:
   - correct feedback -> fix and repeat;
   - incorrect feedback -> deny with evidence.
8. When clean, HermC1 writes step artifacts; if HermC1 is unavailable, use a small OpeC slot and record the fallback:
   - `PLAN.md`
   - `EVIDENCE.md`
   - `SMOKE.md`
   - `UNIT.md`
   - `REAL_USE.md`
   - `COUNCIL.md`
   - `LOCK.md`
9. Leader verifies `LOCK.md` first line is `PASS`.

## Phase 5: Test Benchmark And Guardrail

Goal: test real behavior and update guardrails only when the test exposes a judgement gap.

Guardrails:

- Benchmark ground truth comes from Leader/current session/context or explicit source evidence, not Q-returned hits.
- Test both direct action and retrieval/pre-search path when relevant.
- Do not create a large benchmark project unless Anh asks.
- Related hit is not verified answer.
- Guardrail updates go to a separate artifact, not into this skill as source of truth.

Steps:

1. OpeC7 runs real-use sanity against live Q/runtime, read-only where possible.
2. Leader runs context-session benchmark when the function affects Q/memory/retrieval:
   - fill in the blank;
   - information absent from Q rawdata;
   - prompt/response fragment recall;
   - ambiguous query.
3. Leader compares runtime/Q output against held context/source evidence.
4. Classify each result:

```text
PASS
PARTIAL
WEAK/RISK
UNKNOWN / NOT VERIFIED
```

5. If failure is mechanical retrieval/function behavior, return to Phase 4.
6. If failure is librarian judgement, update only:

```text
C:\Users\ADMIN\.claude-mem\staging\q-hardening\Q_LIBRARIAN_GUARDRAILS.md
```

7. HermC1 writes final lock/seal report; if HermC1 is unavailable, use a small OpeC slot and record the fallback:

```text
PASS
ALL_REQUIRED_AUDITS_PRESENT
FINAL_CONSOLE_PASS
CLAUC_DEVINE_AUDIT_REVIEWED
FIX_VERIFY_PASS
REAL_USE_PASS
BENCHMARK_REVIEWED
GUARDRAIL_REVIEWED
NO_DESTRUCTIVE_LIVE_WRITE
UNKNOWN_NOT_VERIFIED_ZERO_OR_JUSTIFIED
```

## Completion Criteria

Only declare the function locked when:

- Phase 1 has three independent audit reports.
- Phase 2 has final-console pass.
- Phase 3 has devine audit with no unjustified unknown.
- Phase 4 has smoke/unit/fix verification and `LOCK.md` first line `PASS`.
- Phase 5 has real-use benchmark and guardrail review.
- Any remaining risk is explicitly listed as `unknown / not verified` or backlog.
