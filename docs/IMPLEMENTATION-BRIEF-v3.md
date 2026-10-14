# `job_status` — Implementation Brief (USER-APPROVED 2026-10-12)

> **CORRECTION 2026-10-12 ~19:00** (Opus 5.5 review + independent log
> verification by muse1): §2–§3's framing of the 15:30 incident as "a long
> OCR read the client gave up on" is **wrong at the server level**.
> `orchestrator.log` shows **zero** `ocr_map` calls 15:30–15:42 (today's 14
> `ocr_map` POSTs were 11:55–11:58). What the log actually shows is
> **server-wide store contention**: `read_file_range` sent 15:30:58 returned
> 15:34:30 (~211s), `write_fix` sent 15:30:49 returned 15:34:30 (~221s),
> `RESPONSE_UNDELIVERED` storm (tools completed into dead client sockets),
> `write ledger persistence failed: database is locked` (15:31:00,
> 15:37:20), `session event persistence hit store contention` from
> 15:30:58, second wave 15:37–15:42 (28 undelivered). Client-side E1 below
> still describes what Codo experienced; the server-side cause was
> contention, not a long read. **Consequence: a job/async mechanism on the
> same event loop would NOT have saved this incident** — the conversion
> timer itself would have been stuck behind the same lock. Root-causing the
> congestion is prerequisite work (proposed order below §5).

Plan: `draft-v2-job-status-plan.md` v3 (museB 4 waves, 100% convergence).
O3 (meta.json) RATIFIED. O4 (surface) RATIFIED as ToolA tool.
This document is the consolidated handover: context → issue → root cause →
evidence → handling direction.

---

## 1. Context

**The incident (2026-10-12).** Codo (Codex CLI, ToolQ session) received a bug
report at 15:30:51+07: "ask presearch query không pause khi đang ghi. verify
claim và fix nhé." It called ToolA `ocr_map` (long-running read). Codex yields
at ~30s; the tool ran far longer. What followed: 9 `exec` calls returning the
empty stub `"Script running with cell ID N"`, 35 empty `wait` polls, 8 progress
messages, 0 code changes — and at 15:42:22 the task died on a usage-limit
error. Quota burned polling for output that never came; the bug was never
verified, never fixed.

**The three timeout mechanisms (disambiguated 2026-10-12).**
1. *Retryable/overload contract* — FIXED 2026-10-11 (DONE_VERIFIED): ToolA
   answers `TOOL_OVERLOADED` + `retryable:true` + `retry_after_seconds`; CA
   waits and retries with backoff. This covers **rejection under load**, not
   long-running calls. (Server-side flip not yet live — restart pending R9.)
2. *Write tools* — STABLE: 4,539 writes OK in the last 3 days, 0 timeout
   errors (audit-log measurement 2026-10-12). Long writes are safe because of
   the **ledger + `write_status`** mechanism: the ledger opens before the
   write runs; after a client timeout the CA queries `GET /api/write_status`
   → `commit_state` (`in_flight`|`committed`|`not_committed`|`unknown`) +
   `safe_to_retry` + `ca_next_action` — never resend blind.
3. *Long-running reads (OCR/CBM)* — NOTHING EXISTS. This is the gap.

**The async SOT (v9).** Distilled from v8 (263 lines, 8 patch versions) via
museB: 4 waves + F2 code-scan wave, 100% convergence, user-approved
2026-10-12. SHA-256 `f0af53e4b5e1f7fa…9b0158c`, 219 lines. Decision-ready:
7 blocking items B1–B7 with owners. Core design: calls exceeding their sync
window convert to background jobs; CA gets an accept stub
`{ok:true, result:{status:"accepted", job_id, output_path}}`; the job writes
`result.json` into its per-job folder; the next same-session call carries an
`async_done` push notification; session death → pull recovery (§4).

**The `job_status` plan (v3).** Brainstormed via museB (4 waves, 100%
convergence), user-approved 2026-10-12. First-class ToolA tool answering
"where is my result?" — modeled on `write_status`, adapted for reads.

---

## 2. Issue

Long-running **read** tools (OCR/CBM) have no status, no job identity, no
result persistence, and no recovery query. When the client stops listening
(Codex 30s yield) or the session dies (usage-limit, crash), the computed
result is lost with no way to ask about it afterward.

The SOT's push design (`async_done` injected into the next same-session
call) is best-effort only: **it could not have saved the 2026-10-12
incident** — the task died; there was no "next call" to inject into. The
only fallback the system leaves is polling, and polling is what killed the
task (35 polls → quota death). The SOT explicitly rejects manual polling
(§7.3); without a status mechanism, the rejection is unenforceable.

---

## 3. Root cause

Reads never got a `write_status` equivalent because the original threat
model only covered **mutations**: the danger that shaped the ledger design
was double-apply (measured: a 10MB `write_fix` committed at t+54s after the
client gave up at t+4s). Reads were assumed "just re-run" — but re-running
a 360-second OCR job after session death is not free, and a result already
computed is simply discarded.

Code-verified (F2 scans + JS-R1/JS-R2, ToolA gen `3616018e5c6b`):
- No `job_status`, no `async_status`, no `jobs/` directory, no job record,
  no status endpoint anywhere in the codebase — greenfield confirmed.
- The dispatch funnel (`_dispatch_tool_call_request`,
  `orchestrator_server.py:14934`) has exactly one success-exit path where a
  job system would attach (fields= projection `:16606` → schema push
  `:16629–16649` → pending advisory `:16663–16671`) — the insertion point
  exists; the mechanism does not.

**Attribution (user ruling 2026-10-12):** Codo is among the most
rule-disciplined CAs in the system. Its 35 polls were the only strategy the
system left it — empty stubs with no guidance on when output would arrive.
The failure was systemic (no mechanism existed), not disciplinary. Per the
root-cause rule: when a CA misbehaves, investigate the contract and the
system first — never blame CA discipline before the system is cleared.

---

## 4. Evidence

| # | Claim | Evidence |
|---|---|---|
| E1 | Incident: 9 empty exec stubs, 35 empty `wait` polls, death 15:42:22, 0 fixes | Session log `C:\Users\ADMIN\.codex\sessions\2026\09\27\rollout-2026-09-27T17-24-25-01a0b561-aea1-72b0-894b-f3b542d1e888_01a0e265-011c-78a2-bc7e-b0bd2f6bed74.jsonl`; prompt 2026-10-12T08:30:51Z (SOT v9 §1, §8.1 #4) |
| E2 | Funnel insertion point exists at exact lines | `orchestrator_server.py:14934` (`_dispatch_tool_call_request`), `:16606`, `:16629–16649`, `:16663–16671` — F2S1, live code |
| E3 | `write_status` is real and matches docs | Handler `:18710`, route `:19042`, commit states `:2951–2958`, target lookup `:18720–18743` — JS-R1, live code |
| E4 | No job infrastructure exists | 0 hits `job_status`/`async_status`; no `jobs/` dir; `job_id` hits are council-cron only — JS-R2, live code |
| E5 | Write side stable 3 days | 4,539 OK / 0 timeout errors in the toola-call wrapper audit log (2026-10-10–12) |
| E6 | SOT v9 approved | SHA-256 `f0af53e4b5e1f7fa…9b0158c`, 219 lines, decision-ready, 7 blocking B1–B7 |
| E7 | Plan v3 converged | museB 4 waves, 100% convergence, SOT pin stable throughout |

---

## 5. Handling direction (approved)

**Build `job_status` as a ToolA catalog tool** per plan v3
(`draft-v2-job-status-plan.md`):

- **Contract:** `{job_id?}` → `{job_state, tool_name, output_path,
  created_at/updated_at (ISO 8601 UTC), ca_next_action}`; absent `job_id` →
  list calling session's jobs (transport-derived, no `session_id` param).
  Unknown ID → `JOB_NOT_FOUND`, `retryable:false` (terminal, scoped).
  `job_state`: `created|running|done|failed|unknown` (Phase 1),
  `+interrupted` (Phase 2). **No `safe_to_retry`** — reads are idempotent.
  Canonical `ca_next_action` strings quoted in the plan — implementers never
  invent them.
- **Lifecycle:** `created → running → done|failed`; boot marks
  previous-generation `running` as `unknown` (via `boot_epoch` in
  `meta.json`). `notified`/`consumed` excluded (queue concepts, not job
  concepts). Three crash windows documented with mitigations.
- **Storage (O3 RATIFIED):** `meta.json` beside `result.json` in the per-job
  folder (write-ahead, MUST) + in-memory index rebuilt on boot (folders
  without `meta.json` synthesize `unknown` — orphans visible, never
  silent). No SQLite. Counters in-memory (never a disk write per query).
- **Anti-poll (3 guardrails):** per-job 1/30s + per-principal 20/60s +
  per-session listing 1/60s (listing counts toward tripwires); in-memory
  tripwire → `poll_warning`; terminal `JOB_NOT_FOUND`. Stated honestly:
  per-job pacing ≠ prevention; prevention is contract + aggregates together.
- **Funnel rule:** conversion implementation MUST maintain a structural
  exemption set containing `job_status` (a status check must never itself
  convert).
- **Phase 1** (now, co-implemented with the SOT conversion mechanism):
  query surface + both lookups + guardrails + `meta.json` + boot rebuild.
  `failed` transition ships with TBD payload.
- **Phase 2** (after user decides): B2 failure payload format; B3 restart
  policy beyond degraded marking.

**Remaining user-owned items:** O1 (B2 failure contract), O2 (B3 restart
policy), O5 (`failed → notified` — if never, `job_status` is the *only*
failure signal). SOT-level: B1 (default timeout — 25s proposed, NOT yet
ratified), B1b (enforcement, blocked on §3 semantic), B5 (resource bounds),
B6 (consumer inventory), B7 (pre-implementation gate).

**Next step:** museR pipeline — Stage 1 (audit) → Stage 2 (plan + user
"tiến hành") → Stage 3 (execute). No code touched until then.
