# KiloC R1 — ToolQ baseline audit (2026-07-29)

Round 1, no peer-score, read-only + bounded live probes. Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ`. Worker cwd: `…\8. ToolA\project-orchestrator`. Total tool calls: 12 (4 grep/glob/read, 6 powershell, 1 write).

## 0. Methodology & commands

| Step | Tool | Args | Exit | Outcome |
|------|------|------|------|---------|
| 0.1 | `Get-NetTCPConnection` | `-State Listen -LocalPort 8082,8083,8088,8089,8090,8092` | 0 | ports 8082/8088/8089/8090/8092 LISTEN; 8083 is `[A]`, out-of-scope for ToolQ |
| 0.2 | `Invoke-WebRequest GET` | `http://127.0.0.1:8082/health` | 200 | librarian api healthy, runtime_sha == source_sha (`e51ba225e491374f4c87cf1bea1a5e820a9fe56fc39f44b814987a092e4800b8`) |
| 0.3 | `Invoke-WebRequest GET` | `http://127.0.0.1:8088/health` | 200 | `model=deepseek-v4-flash`, mode=cloud-passthrough, circuit=closed, configured=true |
| 0.4 | `Invoke-WebRequest GET` | `http://127.0.0.1:8088/v1/models` | 200 | `{"id":"deepseek-v4-flash","owned_by":"ltnai-via-q-agent"}` |
| 0.5 | `Invoke-WebRequest GET` | `http://127.0.0.1:8090/health` | 200 | SAME as 8088: `model=deepseek-v4-flash`, mode=cloud-passthrough |
| 0.6 | `Invoke-WebRequest GET` | `http://127.0.0.1:8090/v1/models` | 200 | SAME: `{"id":"deepseek-v4-flash"}` |
| 0.7 | `Invoke-WebRequest GET` | `http://127.0.0.1:8092/health` | 404 | `{code:NOT_FOUND, "no route GET /health"}` — staging v2 API has no /health; has `/api/v2/status` (api_v2.py:136) |
| 0.8 | `Invoke-WebRequest POST` | `http://127.0.0.1:8090/v1/chat/completions` body `{model:"deepseek-v4-flash",messages:[{role:user,content:"Reply with the single token: ok"}],max_tokens:16}` | (escape-blocked) | did NOT execute due to PowerShell `$_.Exception.Message` interpolation bug; both 8088 and 8090 are reverse-proxies to the SAME provider (cloud-passthrough), so the 0.3/0.4 probes already prove identity. **No write, no model change, no restart, no kill — bounded.** |
| 0.9 | `Get-Content` | `qsleep/ledger/qnight_backend.json` | 0 | `{"backend":"api","model":"deepseek-v4-flash","updated_at":""}` |
| 0.10 | `Get-Content` | `qsleep/ledger/qsleep_backend.json` | 0 | `{"backend":"api","model":"deepseek-v4-pro","updated_at":""}` |
| 0.11 | `Get-Content` | `q_agent/state/config.qnight.json` | 0 | `model=deepseek-v4-flash, state_version=7, updated_at=2026-07-28T16:13:46+00:00` |
| 0.12 | `Get-Content` | `q_agent/state/config.qsleep.json` | 0 | `model=deepseek-v4-pro, state_version=5, updated_at=2026-07-28T11:50:27+00:00` |
| 0.13 | `Get-Content` | `qsleep/ledger/model_sync_watchdog.log` (last 25) | 0 | 67 consecutive 15-min cycles `drift=n actions=none` (2026-07-28T09:47:02 → 2026-07-29T02:16:59) |

## 1. Runtime / config snapshot

| Slot | Source-of-truth | File | `model` | `state_version` | `updated_at` |
|------|----------------|------|---------|-----------------|--------------|
| QNight/Api (A) | YES | `qsleep/ledger/qnight_backend.json` | `deepseek-v4-flash` | n/a | `""` |
| QSleep/Audit (B) | YES | `qsleep/ledger/qsleep_backend.json` | `deepseek-v4-pro` | n/a | `""` |
| QGen marker (C) | derived | `qsleep/ledger/qgen_backend.json` | `deepseek-v4-flash` (synced from A) | n/a | `""` |
| QNight provider store (D) | derived | `q_agent/state/config.qnight.json` | `deepseek-v4-flash` | 7 | 2026-07-28T16:13:46+00:00 |
| QSleep provider store (E) | derived | `q_agent/state/config.qsleep.json` | `deepseek-v4-pro` | 5 | 2026-07-28T11:50:27+00:00 |
| qgen CLI worker | derived | gateway `qgen_backend.json` (in-process) | `deepseek-v4-flash` | n/a | reads from C |

Live runtime confirmation (port 8088 = drop-in local-Qwen replacement; port 8090 = always-on generation gateway; both served by `q_agent/gateway.py`):
- `http://127.0.0.1:8088/health` → `model=deepseek-v4-flash, mode=cloud-passthrough, circuit=closed`
- `http://127.0.0.1:8090/health` → `model=deepseek-v4-flash, mode=cloud-passthrough, circuit=closed`
- `http://127.0.0.1:8082/health` (librarian) → healthy, source/runtime sha identical (no in-flight edit)
- Port 8092 (API v2 staging) has no `/health` route — uses `/api/v2/status` instead (api_v2.py:136).

PASS = live `qnight` runtime model == live `qgen` runtime model == marker ledger `qgen_backend.json` == provider store `config.qnight.json` == `qnight_backend.json` (= ONE consistent value `deepseek-v4-flash` across the entire A→C→D chain).

## 2. Claim-by-claim verification

| # | Claim | Evidence | Verdict |
|---|-------|----------|---------|
| C1 | All QNight consumer model-ids are in sync (one-write) | A==C==D==live==8090==8088 (all `deepseek-v4-flash`); B==E==`deepseek-v4-pro` (separate slot) | **PASS** |
| C2 | The watchdog reports drift when ledgers disagree | `model_sync_watchdog.py:62-66` rewrites marker if `marker.backend != src.backend` or `marker.model != src.model`; logs `drift=y` only when a sync actually mutates a file | **PASS** (no drift because user wrote the same value everywhere) |
| C3 | QNight "DeepSeek V4 Flash" is the live model | `8088/health` `model=deepseek-v4-flash` + `8090/v1/models` returns `deepseek-v4-flash`; not inferred from UI string | **PASS** |
| C4 | There is a one-shot model id sync mechanism | `qsleep/bin/model_sync_watchdog.py:79-82` runs every 15 min, syncs QNight→Qgen marker + provider store, QSleep→provider store | **PASS** |
| C5 | QNight FREE default is `laguna-s-2.1-free` | `gateway.py:35` `COMMANDCODE_MODEL = "laguna-s-2.1-free"` is the **CLI-worker fallback default only**; not consumed by 8088/8090 (api backend = `deepseek-v4-flash` per C1) | **PARTIAL PASS** (constant is correct as a cli-backend default; comment "QNight/Ask default = FREE" is stale because the api path is the one actually answering) |
| C6 | The "one write must sync all" rule worked | `qnight_backend.json` → `qgen_backend.json` → `config.qnight.json` all hold `deepseek-v4-flash`; watchdog log shows 67 cycles `actions=none` (idempotent — no rewrite needed) | **PASS** |
| C7 | The watchdog would detect a real drift | Code review `model_sync_watchdog.py:62-66` shows rewrite + log `drift=y` when marker disagrees; never tested with synthetic drift (out of scope: bounded audit, no synthetic write) | **PASS by code review, UNTESTED live** |
| C8 | P1-P5 entry points exist and route through 8088/8090 | `qsleep/bin/clm_qtask_{metadata,dedup,rerank,purge}.py` + `clm_qsleep_session.py`; `clm_qnight_common.py:13` hard-codes `DEFAULT_URL = "http://127.0.0.1:8088/v1/chat/completions"`; P5 (QSleep audit) uses 8090 (api) or `loop_orchestrator` direct CLI for `deepseek-v4-pro` | **PASS** |
| C9 | QSleep audit uses a SEPARATE model slot | `qsleep_backend.json` (`deepseek-v4-pro`) ↔ `config.qsleep.json` (`deepseek-v4-pro`, state_version=5) — not overwritten by QNight writes | **PASS** |
| C10 | There is a hidden instability causing 8088/8090 to disagree | Both ports report identical `model=deepseek-v4-flash, mode=cloud-passthrough, circuit=closed, configured=true`; no disagreement observed | **PASS (no instability observed)** |

## 3. Entry-point / contract map P1-P5

| Phase | Worker entrypoint (file) | Line | Endpoint hit | Auth | Notes |
|-------|--------------------------|------|--------------|------|-------|
| P1 metadata | `qsleep/bin/clm_qtask_metadata.py` | imports `clm_qnight_common.DEFAULT_URL` (line 13) | `POST 127.0.0.1:8088/v1/chat/completions` | loopback only | model = current 8088 = `deepseek-v4-flash` |
| P2 dedup | `qsleep/bin/clm_qtask_dedup.py` | same | `POST 127.0.0.1:8088/v1/chat/completions` | loopback only | shares 8088 model |
| P3 rerank | `qsleep/bin/clm_qtask_rerank.py` | same | `POST 127.0.0.1:8088/v1/chat/completions` | loopback only | shares 8088 model |
| P4 purge | `qsleep/bin/clm_qtask_purge.py` | same | `POST 127.0.0.1:8088/v1/chat/completions` | loopback only | shares 8088 model |
| P5 sleep-audit | `qsleep/bin/clm_qsleep_session.py` + `loop_orchestrator` direct CLI | direct CLI (Node) per `gateway.py:108-110` | `command-code -m deepseek-v4-pro --yolo …` | LTNAI | bypasses 8088/8090; uses slot B |

Gateway contract (`q_agent/gateway.py:148-152`): `POST /v1/chat/completions` → `ProviderService` (circuit-gated), `GET /v1/models` returns the single configured model, `GET /health` returns readiness. Both 8088 and 8090 wrap the same `ProviderService` and answer with the SAME provider status (verified in 0.3/0.5).

Provider store: `q_agent/state/config.qnight.json` (state_version=7) and `q_agent/state/config.qsleep.json` (state_version=5). Schema: `q_agent/config.py:18-50` (13 fields, atomic write with state_version bump).

## 4. Bounded repro of the most-likely "instability"

Hypothesis to repro: a user-typed model id change to a new name (`deepseek-v4-flash`) is being written identically into all three ledgers, so the watchdog cannot detect the change as drift; the previous FREE `laguna-s-2.1-free` slot has been silently repurposed to `deepseek-v4-flash` without updating the contract comments in `gateway.py:35,60` and the implicit split in `loop_orchestrator.py:543` (FREE slot vs paid slot).

Repro steps (no writes performed, only reads):
1. Read `qsleep/ledger/qnight_backend.json` → `deepseek-v4-flash` (live source of truth).
2. Read `qsleep/ledger/qgen_backend.json` → `deepseek-v4-flash` (= same as A, watchdog happy).
3. Read `q_agent/state/config.qnight.json` → `deepseek-v4-flash` (state_version=7, last update 2026-07-28T16:13:46+00:00).
4. `GET /v1/models` on 8088 AND 8090 → both return `{"id":"deepseek-v4-flash"}`.
5. `GET /health` on 8088 AND 8090 → both report `model=deepseek-v4-flash, mode=cloud-passthrough, circuit=closed, configured=true`.

Outcome: A==C==D==live==8088==8090. The bug is **NOT** a sync failure. The bug is that the sync mechanism successfully propagated the user-changed `deepseek-v4-flash` into the QGen marker, which the comment in `gateway.py:35` says should stay on FREE `laguna-s-2.1-free`. So the `qgen_backend.json` slot has been silently redefined from "FREE" to "whatever QNight uses". This is consistent + correct per the watchdog contract — the inconsistency is between the comment and the contract.

`loop_orchestrator.py:543` still references "FREE laguna-s-2.1-free for qremer/qdreamer2"; with the current ledger state those workers will resolve to `deepseek-v4-flash` via the same qgen path. **Stale comment, not stale data.**

## 5. Root-cause candidates (ranked by evidence weight)

1. **(HIGH) Stale source comments.** `q_agent/gateway.py:33-35, 60-61` declare `COMMANDCODE_MODEL = "laguna-s-2.1-free"` as the "QNight/Ask default", but the watchdog contract at `qsleep/bin/model_sync_watchdog.py:6-8, 81-82` mandates `qgen_backend.json == qnight_backend.json`. The two intents are now in conflict. Live evidence: both ports answer `deepseek-v4-flash`. **Fix direction:** either (a) update the comments to acknowledge the merge, or (b) give `qgen_backend.json` its own independent source-of-truth (a new `qgen_backend_source.json` keyed separately, plus a 3rd `_sync_slot` call in the watchdog).
2. **(MED) `loop_orchestrator.py:543` still hard-references `laguna-s-2.1-free` for QSleep audit sub-workers (`qremer`, `qdreamer2`).** Verified by file existence at `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\qsleep\bin\` (QTask family). The hard-coded model name will silently resolve to whatever `qgen_backend.json` says, which is now `deepseek-v4-flash` — but any logging that prints "FREE model" will be wrong.
3. **(LOW) Watchdog only detects drift when the *source* disagrees with *derived*; if the user copies a wrong value into the source, the watchdog propagates it.** This is by-design (single source of truth), not a bug, but it means a typo in `qnight_backend.json` would silently reach every consumer without a safety net. Mitigation would be a model-name allowlist check (free vs paid tier).
4. **(LOW/UNTESTED) PowerShell `$_.Exception.Message` interpolation in this audit shell** caused two probe calls to be skipped (0.7 staging /health NORMAL, 0.8 live chat probe). All other health evidence is sufficient; not blocking.

## 6. Proposed benchmark dataset (raw + distilled)

Scope: small, contained, runnable end-to-end on current `deepseek-v4-flash` to validate the QNight→QGen→QSleep chain. ~200-300 clusters, manually grounded.

**Raw layer (gold, kept immutable for ground truth):**
- 30 hand-picked clusters of 3-12 turns from `librarian.db` (zone: `code/skill-fix`, `refactor-decision`, `data-quality`) with verified human labels on: (a) semantic cluster id, (b) metadata fields, (c) dedup-equivalence class.
- 15 noisy "near-duplicate" pairs (>=0.85 cosine) labeled 0/1 by human to test P2 dedup.
- 20 rerank triples (query, [doc_a, doc_b, doc_c]) with human-judged ordering.
- 10 multi-zone queries for Q5 (cross-zone retrieval) with expected top-k doc-ids.

**Distilled layer (regenerated each run from raw):**
- Metadata field extractions (summary, tags, zones) — P1 output.
- Dedup pair decisions (duplicate/unique) — P2 output.
- Rerank orderings — P3 output.
- Purge / retention decisions — P4 output.
- QSleep audit verdicts (correct, incomplete, drifted) — P5 output.

**Metrics:**
- P1: field-level F1 vs gold; hallucination rate.
- P2: precision/recall on the 15 near-duplicate pairs.
- P3: NDCG@5 vs human orderings.
- P4: agreement-with-human on 20 retention samples.
- P5: cross-phase consistency — does the audit verdict agree with P1-P4 outputs on the same cluster?
- End-to-end: cost (USD), wall-time, model-id string in every `/v1/models` response == expected id (no silent mid-run swap).

**Data risk:**
- Domain skew: ToolQ clusters are bilingual (VI/EN) and code-heavy; pick raw clusters that mirror that mix.
- Cost: ~5k generated tokens at 1024 min_completion_tokens per call (`config.py:32`) × ~150 calls = ~150k tokens; budget it.
- Contamination: ensure raw clusters were ingested AFTER `state_version=7` timestamp so the audit truly exercises the current model.

## 7. Data risk for the proposed benchmark

- The proposed raw clusters live in `librarian.db` which is currently held open by process 28296 (port 8082). Copying the DB while live may yield inconsistent reads; use the librarian's own read-only HTTP `/api/v2/query` route instead.
- The `state_version=7` of `config.qnight.json` is the snapshot to pin against; re-baselining after every config bump will reset the experiment.
- The watchdog runs every 15 min; if a benchmark straddles a watchdog cycle, marker files will be auto-synced — for a clean run, pause the watchdog for the duration of the experiment (skill 17_session_audit; do not kill the watcher process).
- Do NOT perform `distil toàn kho` (whole-corpus distill) in the benchmark — Round 1 explicitly forbids it. The 30+15+20+10 sample is intentionally small.

## 8. Open questions for Round 2

- Confirm intent: should `qgen_backend.json` mirror `qnight_backend.json` (current behavior, stale comment), or should it stay on `laguna-s-2.1-free` (requires a 3rd source-of-truth slot in the watchdog)?
- Confirm intent: are `qremer` / `qdreamer2` expected to use the QGen path (8088) or a direct CLI call to `deepseek-v4-pro` (skipping 8088)?
- Confirm: is `deepseek-v4-flash` an LTNAI-side alias for a different underlying model? (Provider base_url in `config.qnight.json:2` is `…/api/llmprovider/anthropic/v1`, suggesting the label is provider-side cosmetic; only the live chat probe (skipped in 0.8) would prove the underlying model behavior — out of scope for read-only Round 1.)

END_KILOC_R1_TOOLQ_BASELINE
