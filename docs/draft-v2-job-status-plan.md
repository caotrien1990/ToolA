# `job_status` — Design Plan v2 (museB draft)

Status: draft v2 — NOT approved for implementation. Addresses all Wave-2
review findings (4 HIGH, 9 MEDIUM, 14 LOW/nits). Blocked on user-owned SOT
items — see §8.

## §1. Decision

Ship `job_status` as a **first-class ToolA catalog tool** (not a bare HTTP
endpoint, not a `result.async_status` promotion).

- Rationale: CAs discover and call tools, not raw endpoints; schema-first
  gives typed validation; the calling session arrives free from transport
  metadata. (JS-P1, high confidence)
- Fallback if tool-surface growth is vetoed: `GET /api/job_status` with
  identical contract semantics. (JS-P1)
- `result.async_status` stays strictly the SOT §2.2 collision-fallback field
  name. (JS-P1)

## §2. Contract

**Input:** `job_id` (string, optional). Absent → list the calling session's
jobs. There is **no** `session_id` parameter: the only permitted listing scope
is the calling session, which the server already knows from transport
metadata — a parameter whose sole valid value is "yourself" is pure attack
surface. (JS-RV2/M-2)

**By job_id:**
```json
{ok: true, result: {job_id: "<id>", job_state: "created|running|done|failed|unknown",
                    tool_name: "<catalog name of the converted tool>",
                    output_path: "<abs path>",
                    created_at: "<ISO 8601 UTC>", updated_at: "<ISO 8601 UTC>",
                    ca_next_action: "<canonical string>"}}
```
(`interrupted` joins the enum in Phase 2 — B3 policy, §7. Phase 1 emits
`created|running|done|failed|unknown` only.)

**Session listing:** `{ok: true, result: {jobs: [{job_id, job_state,
tool_name, output_path, created_at, updated_at}]}}`, ordered by
`updated_at` descending.

**Unknown ID:** `{ok: false, error_name: "JOB_NOT_FOUND", retryable: false}`
— terminal for malformed/never-issued IDs. **Scoped:** during boot rebuild, a
legitimately-issued ID not yet indexed may transiently return this; that case
is *not* terminal — the rebuild's consistency window is documented in §4, and
`retryable: false` applies to the ID-validity verdict, not the index state.
(JS-RV2/L-1)

**Field justification (optimizer lens):**
- `job_state` — the one thing the CA needs (analog of write_status's
  `commit_state`). KEEP. One name everywhere: API response **and** `meta.json`
  both use `job_state` (the draft v1 `state`/`job_state` split was an
  implementability defect — JS-RV3/G-2).
- `safe_to_retry` analog — **REMOVED**. Reads are idempotent. (JS-P1; JS-C1/C1)
- `ca_next_action` — KEEP. Canonical strings (quoted here so implementers
  never invent them — JS-RV3/G-7):
  - on `running`: `"job still running; do not poll — async_done will notify on your next same-session tool call; if this session may die, persist job_id and check once on resume."`
  - on `done`: `"result ready; read the needed ranges via read_file_range with project_path=<ToolA root> — do not read the whole file."`
  - on `JOB_NOT_FOUND`: `"unknown job id: check for typos; if issued before a server restart, the record may not be rebuilt yet — retry once after 60s, then treat as expired."`
  - on rate-limit 429: `"slow down — re-check at a natural breakpoint, never on a timer."`
  - on `failed`: `"job failed; error detail format pending B2 — inspect result.json if present; do not retry blindly."`
  - on `interrupted`: `"job interrupted by server restart; restart policy pending B3 — check output_path for partial result.json; do not assume completion."`
  - on `unknown`: `"record predates the current server generation or was never completed — check output_path directly; treat absence as indeterminate, not as failure."`
  - Error responses carry `ca_next_action` too.
- `output_path` — KEEP. Re-grounds the result location after session resume.
- The response never carries output.

**Funnel rule (load-bearing):** the conversion implementation MUST maintain a
structural exemption set in `_dispatch_tool_call_request`
(`orchestrator_server.py:14934`), and `job_status` MUST be in it — a status
check must never itself convert to async. (The set does not exist yet; the
conversion is greenfield. JS-RV1/F-4)

**Authorization note (honest scoping — JS-RV2/M-1):** `job_id` is
collision-resistant (16.7M combos, SOT §2.4), **not** a capability — 24 bits
of entropy with a caller-known timestamp is not unguessable. By-`job_id`
lookup is therefore bearer-token-by-obscurity: acceptable for v1 because the
disclosed data is the CA's own job metadata, but the server-absolute
`output_path` disclosure to any holder is a documented trade-off. Principal
scoping (binding jobs to the owning principal, not just the ID) is flagged
for hardening post-v1, not designed here.

## §3. Lifecycle (job states only)

```
created → running → done ─┐
                  → failed ─┤→ (expired — DEFERRED, SOT §7.2) → record swept
                  → interrupted ─┘
```

- `created → running`: at conversion; folder + `meta.json` written first
  (**MUST** — write-ahead, SOT §3).
- `running → done`: `result.json` written = the exact sync output.
- `running → failed`: exception. The **transition** ships in Phase 1 with
  error payload marked TBD (no B2 format decision needed for the transition
  itself — JS-RV1/F-2). Payload format is B2's (user-owned).
- `running → interrupted`: server restart kills the in-flight task. Phase-1
  degraded behavior (no B3 decision needed): **boot marks previous-generation
  `running` records as `unknown`** (see §4). The B3 *policy* (retry/clean/
  leave) stays user-owned; the honesty mechanism does not wait for it.
  (JS-RV1/F-3; JS-RV2/M-3)
- `notified`/`consumed` deliberately excluded (notification-queue concepts).
- **Crash windows (stated honestly — JS-RV2/H-4):**
  1. *mkdir → meta.json write:* crash leaves a folder with no `meta.json`.
     Mitigation: boot rebuild lists job folders **without** `meta.json` too
     and synthesizes `{job_state: "unknown"}` entries for them — orphans are
     visible, never silent.
  2. *meta.json{running} → task start:* crash leaves a record claiming
     `running` with no task. Mitigation: `meta.json` carries `boot_epoch`
     (server generation at creation); boot marks any `running` record whose
     `boot_epoch` ≠ current generation as `unknown`. This makes `unknown`
     implementable as specified.
  3. *result.json → meta.json{done}:* crash leaves result on disk with state
     `running` (or partial result). Ordering noted; torn-write semantics are
     B3's domain (partial `result.json`, user-OPEN).

## §4. Storage

The job record **is** `meta.json` beside `result.json` in the per-job folder:

```json
{job_id, session_id, tool_name, job_state, output_path,
 created_at, updated_at, boot_epoch}
```

- Written at conversion (marginal cost on the already-mandatory folder
  creation). `updated_at` bumps **on state transitions only** (not on status
  checks — it means "last state change", not "last touched"). (JS-RV3/G-5)
- In-memory index (`job_id → meta`, `session_id → [job_ids]`), module-level
  in `orchestrator_server.py` beside the conversion code, rebuilt on boot by
  scanning `jobs/*/meta.json` **plus** folders lacking `meta.json` (→
  synthesized `unknown`). Rebuild has a documented consistency window: queries
  served during rebuild may transiently miss records (see §2 JOB_NOT_FOUND
  scoping). (JS-RV3/N-5; JS-RV2/L-1)
- **Anti-poll counters are in-memory, not persisted** (JS-RV5/iv): persisting
  `status_checks` would add a disk write to every status query — the
  anti-poll mechanism must not cost a write per poll. Post-restart the
  counter is meaningless anyway (jobs go `unknown` per §3).
- **No SQLite, no second store.** Record lifetime = folder lifetime; the
  already-deferred folder sweeper (SOT §7.2) covers both.
- **SOT §2.4 amendment note:** §2.4's normative folder tree shows only
  `result.json`; adopting `meta.json` amends it — flagged at §8 O3 so the
  dependency is visible at the point of claim. (JS-RV1/F-6)
- In-memory index has no memory bound — flagged as a **B5** (resource bounds,
  user-OPEN) sub-question for the implementer. (JS-RV2/L-2)

## §5. Lookup paths

1. **job_id known** (normal case): server-global lookup. Works **across
   sessions** — the SOT §4 resume case. (JS-P1, JS-P2)
2. **job_id absent:** list the **calling** session's jobs (transport-derived;
   no parameter). (JS-RV2/M-2)
3. **New session, job_id unknown:** unrecoverable **by design** — D1 forbids
   cross-session fallback. `ca_next_action` says so plainly. (D1 honest cost)

## §6. Anti-poll discipline (load-bearing)

**Honest framing first (JS-RV4/M-1):** write_status — the model — permits
**bounded polling to a terminal state** (SOUL.md: "Poll it"). `job_status`
is deliberately **stricter** (recovery-only): its waiting-use recreates the
35-poll incident, so the draft does not mirror write_status's polling
allowance. The difference is stated, not smuggled.

1. **Contract:** recovery episodes only (session resume, lost notification).
   On `running`: do other work; the same-session `async_done` push remains
   the primary completion signal.
2. **Response design:** every `running` response carries the canonical
   `ca_next_action` (§2) naming the next step and the reason not to ask again.
3. **Server guardrails (all three — each catches what the others miss):**
   - per-`job_id`: 1 query / 30s → `429 {error_name:
     "JOB_STATUS_RATE_LIMITED", retry_after_seconds: 30}`. (429s do **not**
     increment the tripwire — blocked queries are not polls. JS-RV3/G-4)
   - per-**principal aggregate**: 20 queries / 60s across all job_ids →
     same 429 shape. **This** is what stops fan-out: per-job keying alone
     permits N/30 qps across N jobs (JS-RV2/H-1's arithmetic stands — the
     per-job limit paces, the aggregate limit bounds).
   - per-**session listing**: 1 listing / 60s per session; each listing query
     counts toward **every returned job's** tripwire counter — the listing
     mode is not a throttle bypass. (JS-RV2/H-2)
   - Stated plainly: the per-job limit **paces** polling; it does not prevent
     it. Prevention is the contract + terminal `JOB_NOT_FOUND` + these
     aggregate bounds together.
4. **Tripwire (in-memory):** >3 `running`-state queries with no state
   transition → `"poll_warning": true` inside `result` + escalated
   `ca_next_action`. Signaling, not enforcement. (JS-P1; JS-RV3/N-2)
5. `JOB_NOT_FOUND` terminal with `retryable: false` (scoped per §2).

## §7. Sequencing (restated honestly — JS-RV2/H-3, JS-RV5/iv)

- **Phase 1:** the query surface (`created|running|done` + `failed`
  transition with TBD payload + boot `unknown`-marking), both lookup paths,
  all §6 guardrails, `meta.json` + boot rebuild. **Dependency, stated:**
  records are created **at conversion**, and conversion is B1-gated (default
  timeout, `timeout=0` semantics) and not yet implemented — so Phase 1 is
  co-implemented with (or after) the conversion mechanism, and is testable
  standalone only against synthetic records. "Implementable now" in v1
  overclaimed; the query *logic* needs no B1/B2/B3 value, but the *feature*
  is undeployable without the conversion path. **Phase 1 additionally gates
  on O3 ratification** — its storage design *is* meta.json (§8 O3); if O3 is
  answered "no", §4 is replanned. (JS-RV1/F-1; JS-RV5/ii)
- **Phase 2 (after user decides B2/B3):** `failed` payload format (B2);
  `interrupted` *policy* beyond the Phase-1 degraded marking (B3).

## §8. Open items with owners

| # | Item | Owner |
|---|---|---|
| O1 | B2 failure contract (error payload format) | user (SOT §7.1) |
| O2 | B3 restart recovery policy (retry/clean/leave) | user (SOT §7.1) |
| O3 | B4 ratification — does meta.json settle the record half? Amends SOT §2.4 | user — **Phase 1 gates on this** |
| O4 | Surface ratification — tool (recommended) vs HTTP endpoint | user |
| O5 | `failed → notified` push — if never, job_status is the *only* failure signal | user (SOT §7.1) |

## §9. Execution trace — 2026-10-12 Codo incident (honest preconditions)

Preconditions: B1 default timeout < 30s Codex yield; conversion implemented;
O3 ratified; CA persisted `job_id` per SOT §4.

1. 15:30:51 — Codo calls `ocr_map`, default 25s. t+25s: conversion → accept
   stub. Codex still listening → Codo receives the stub. **Zero polls.**
2. Server: folder + `meta.json` `{job_state: "running", boot_epoch: <gen>}`
   written (write-ahead); job runs; `result.json` written → `meta.json` →
   `{job_state: "done", updated_at}`.
3. Session dies (usage-limit) before any next call → push lost (D1). No
   orphan sweep: completion checks session liveness, **skips enqueue for dead
   sessions** (JS-P2 — new behavior, implementer to confirm a cheap liveness
   check exists; flagged, not assumed).
4. New session resumes; notes survived → `job_status({job_id})` **once** →
   `{job_state: "done", output_path, ca_next_action: <done-string>}`. Codo
   reads the needed ranges. Bug verified.
5. Variant — notes lost: `job_status()` (no args) in the new session →
   `jobs: []` → honest `ca_next_action`. No loop.

## Appendix A — Disposition of Wave-1 and Wave-2 findings

### Wave-1 critic (JS-C1) — 8-item disposition mapping

| # | Critique | Disposition |
|---|---|---|
| C1 | write_status analogy collapses on load-bearing fields (reads have no double-apply risk) | ACCEPTED — `safe_to_retry` removed from the contract (§2) |
| C2 | Redundant with §4 pull recovery on the known-job_id path | REBUTTED with evidence — R2 finding 6: SOT's own OPEN items (failed format, partial result.json, absence ambiguity) are exactly what file-presence checks cannot resolve |
| C3 | job_id-unknown lookup unsatisfiable under D1 | ACCEPTED as D1's honest cost — encoded in §5.3, not patched around |
| C4 | "Recovery-only" unenforceable against a queryable endpoint | ANSWERED structurally — §6 layers 3–5 (rate limit, tripwire, terminal JOB_NOT_FOUND) are mechanism, not documentation |
| C5 | Motivating incident outside the save envelope | ACKNOWLEDGED — §9 states preconditions explicitly instead of claiming unconditional rescue |
| C6 | Scope "no CA-side changes" contradicts the problem statement | CORRECTED — scope is "no NEW CA-side changes beyond SOT §4" |
| C7 | Acceptance criteria gated on user-owned B1–B4 | SEQUENCED — §7 Phase 1 needs no B-value; Phase 2 waits for B2/B3; Phase 1 gates on O3 |
| C8 | Retention drags B4/D1 into the critical path | RESOLVED — §4 couples record lifetime to folder lifetime; no separate retention policy |

### Wave-2 findings

- **RV2 H-1** (guardrail permits incident cadence; fan-out): ACCEPTED —
  added per-principal aggregate limit (§6.3); pacing-vs-prevention stated
  honestly.
- **RV2 H-2** (listing mode unthrottled): ACCEPTED — listing rate-limited per
  session + counts toward tripwires (§6.3).
- **RV2 H-3** (Phase 1 / B1): ACCEPTED — §7 restated (§9 preconditions
  unchanged).
- **RV2 H-4** (crash windows; no generation field): ACCEPTED — `boot_epoch`
  added (§3, §4); three crash windows documented with mitigations; B4-scope
  claim removed (now: "user ratifies", §4 + O3).
- **RV2 M-1** (unguessable/authorisation): ACCEPTED — honest bearer-token
  scoping + hardening flagged (§2).
- **RV2 M-2** (session_id param): ACCEPTED — parameter removed (§2, §5).
- **RV2 M-3** (crash incoherence): ACCEPTED — boot unknown-marking in Phase 1
  (§3, §4, §7).
- **RV2 L-1/L-2**: ACCEPTED — JOB_NOT_FOUND scoped (§2); effort estimates
  removed; liveness check flagged; B5 memory bound flagged (§4).
- **RV1 F-1** (Phase1/B4-O3): ACCEPTED — Phase 1 gates on O3 (§7, §8).
- **RV1 F-2** (failed transition in Phase 1): ACCEPTED — transition ships
  with TBD payload (§3, §7).
- **RV1 F-3** (B3 degraded behavior): ACCEPTED — boot unknown-marking (§3).
- **RV1 F-4..F-10**: ACCEPTED — funnel rephrase (§2); (expired) deferred
  (§3); §2.4 amendment flag (§4, §8); field precision (§2); exactly-one-of
  moot after M-2; dependency stated (§7).
- **RV3 G-1..G-7**: ACCEPTED — ISO 8601 UTC (§2); `job_state` unified
  (§2–§4); session default via transport (§2, §5); 429 shape + tripwire
  non-increment (§6.3); writer discipline (§4); dependency restated (§7);
  canonical strings quoted (§2).
- **RV3 N-1..N-5**: ACCEPTED — created_at in listings; poll_warning inside
  result; updated_at-desc order; tool_name defined; index location (§4).
- **RV4 M-1** (write_status discipline): ACCEPTED — §6 honest framing; the
  v1 analogy was factually wrong.
- **RV4 U-1** (Phase-1 independence): ACCEPTED — justification now inline
  (§7).
- **RV5 i** (procedural override): ACCEPTED — logged below.
- **RV5 ii** (§7/§8 contradiction): ACCEPTED — resolved via O3 gating.
- **RV5 iii** (C2 rebuttal): stands (no change).
- **RV5 iv** (tripwire cost): ACCEPTED — counters in-memory (§4, §6.4).
- **RV5 v** (phantom P5): ACCEPTED — corrected to JS-P2 (§9.3, App. A).

## Appendix B — Override Log (v2)

1. **Procedural override — P1's B4-deferral on storage timing.** P1's
   substantive position was "storage is B4's to decide" (procedural: decide
   later, by the user). P2 proposed meta.json now. The coordinator adopts
   meta.json **pre-ratification**, pending O3 — overruling P1 on *timing*.
   Denominator: agents expressing a position on storage timing = {P1
   (defer), P2 (now)} → 1/2 = 50%, not <50% — but a procedural deferral
   overruled on timing is an override in substance regardless of the
   fraction, so it is logged here. Next-wave verdict item:
   APPROVE-OVERRIDE / REJECT-OVERRIDE.
2. **Anti-poll combination (rate limit + tripwire):** no agent proposed the
   combination; each proposer backed their own layer. Basis for carrying
   both: they catch disjoint patterns (burst volume vs slow-drip under the
   rate limit — JS-RV2/H-1's own analysis establishes the disjointness), and
   v2 prices the tripwire (in-memory, zero persisted cost). Verdict item:
   APPROVE-OVERRIDE / REJECT-OVERRIDE on the combination.
