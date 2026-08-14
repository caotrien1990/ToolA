# ComC3 Round 2 Plan — Corrected Baseline + Low-Risk Implementation Gates

## Identity

- **Worker:** ComC3
- **Role:** Tool/test/benchmark gatekeeper — conservative on pipeline edits, aggressive on measurement
- **Objective:** Produce corrected, scorable plan for tonight's QSleep upgrade with exact implementation batches and acceptance gates
- **Mode:** Read-only planning; write only this output

## Files Read

| # | Path | Purpose |
|---|---|---|
| 1 | `DATA/ROUND2_PROMPT.md` | Task: score peer, correct baseline, produce improved plan |
| 2 | `DATA/CONTEXT.md` | Audit context, failure ledger, patch list |
| 3 | `round1/COMC1_ROUND1_PLAN.md` | Peer plan: ComC1 DeepSeek, aggressive on source edits |
| 4 | `round1/COMC3_ROUND1_PLAN.md` | Own Round 1 plan — needs baseline correction |
| 5 | `round1/HERMC1_SUPPORT_RESEARCH.md` | Deep analysis: proven/inferred needs, patch assessment |
| 6 | `round1/OPEC4_LOCAL_SCAN.md` | Local scan: rank analysis, metric gaps |
| 7 | `round1/OPEC2_NEEDS_CONSOLIDATION.md` | TA0/TA1/TA2 P0-P3 taxonomy consolidation |
| 8 | `small-20260627-110455/report.md` | Benchmark: 12 cases, exact rank per case |

## Peer Score

**ComC1 Score: 7/10**

Strong points:
- Correctly identifies Recall@1 = 5/12 baseline (lines 27-31) — accurate count
- Full Hermes log dump (Change 1) addresses the root cause gap for no_converge
- Silent crash detection (Change 2) closes raw-860 class
- Two-phase approach for Hybrid boost (Phase A tonight, Phase B later) is correctly scoped
- 7 benchmark metrics (Change 4) match OpeC2 consensus
- Backlog decisions (no architecture rewrite, no parallelization) aligned with CONTEXT.md

Deductions:
- Change 3 (`--qsleep-max-versions`) adds a CLI flag without evidence that max_versions=8 is a bottleneck. Insufficient data to justify the source edit tonight.
- Change 5 Phase B (server-side Hybrid scoring in /query handler) is high-risk for tonight: the files to modify are guessed (clm_librarian_api.py or clm_librarian_vector.py), and a 1.5x multiplier without score distribution data could regress existing recalls. Score deltas are unknown (HermC1 Evidence Gaps).
- No explicit baseline correction section — Recall@1=5/12 is mentioned in F1 but never formally stated as correction
- Acceptance criteria set Recall@1 >= 9/12 from 5/12 baseline — ambitious but the plan does not explain how 9/12 is reached given score distribution data gap

## Baseline Correction

**My Round 1 plan (COMC3 line 63) incorrectly states Recall@1 = 7/12 (58%).**

From the benchmark report raw data:
| Case | Exact Rank |
|---|---|
| A1_stale_ports | 4 |
| A2_cline_mcp_path | 1 |
| A3_codex_claude_mem_install | 5 |
| A4_cross_session_memory | 1 |
| A5_vector_vs_rag | 4 |
| A6_codoc_parallel | 1 |
| B1_secret_guardrail | 1 |
| B2_broker_sender_schema | 4 |
| B3_same_identity_sender | 2 |
| B4_no_raw_dump | 2 |
| C1_agent_error_workflow | 1 |
| C2_claude_mem_writer_policy | 2 |

**Exact Recall@1 = 5/12 (41.7%).** Not 7/12. The 7/12 count erroneously included B3 (rank 2), B4 (rank 2), and C2 (rank 2) as rank 1.

**Impact:**
- The ranking gap is worse than my Round 1 claimed — 7/12 non-R1 cases instead of 5/12
- My Round 1 AC target of >= 9/12 is an 80% improvement from 5/12, not 29% from 7/12
- The priority of ranking fix increases: it is not "5 cases need help" but "7 cases need help"
- A3 (rank 5) and A1/A5/B2 (rank 4) are the hardest — they need boost by 3-4 positions

**Correction cascade:**
- All acceptance criteria values referencing "from 7/12" are recalculated below
- The baseline is 5/12. A realistic tonight target is 8/12 (recovering B3, B4, C2 from rank 2 to rank 1)

## Keep

From my Round 1 plan:
1. **TA0-T1: Config tightening** — Set `--qsleep-max-audit-fails=5`, `--qsleep-hermes-timeout-sec=120`. Zero risk, config only.
2. **TA0-T2: External wrapper for Hermes crash capture** — Wrapper in BR audit directory captures full stdout/stderr to timestamped log.
3. **TA0-T3: PowerShell retry wrapper** — Max 2 retries, checks `_failed/` artifacts after run.
4. **TA1-T1: Discovery-only ranking confirmation** — Run small benchmark, inspect `hybrid_top_ids` to confirm ranking pattern.
5. **TA2-T1: Post-processing metrics** — `post_process_benchmark.py` in BR audit dir reads results.jsonl and QSleep run metadata.
6. **Backlog scope** — Full architecture rewrite, QSleep parallelization, GPU mode `mid`->`full`, overnight run without gate.
7. **QDreamer2 baseline defer** — Need tonight's run data to assess over-pruning risk.

From ComC1 plan:
8. **Change 1: Full Hermes log dump** — Replace 240-char tails with `<run_dir>/<phase>_<ts>.log` (cap 500KB). Essential for raw-1-2-3-4-6 no_converge debugging.
9. **Change 2: Silent crash detection** — After `subprocess.run()`: if returncode != 0 and stdout+stderr both empty -> raise distinct `HermesSilentCrashError`.
10. **Change 4: Add 7 benchmark metrics** — Per-query latency, Recall@1, keyword-vs-exact gap, Precision@5, rank histogram, QSleep loop count, fail reason.

## Reject or Defer

| Item | From | Reason |
|---|---|---|
| **Change 3: `--qsleep-max-versions` CLI flag** | ComC1 | No evidence max_versions=8 is a bottleneck. Add tomorrow if needed. |
| **Change 5 Phase B: Server-side Hybrid scoring** | ComC1 | High risk without score distribution data. Files to modify are guessed. 1.5x multiplier could regress existing 12/12 hits@5. Defer to tomorrow after Phase A data. |
| **TA1-T2: Defer exact-id scoring boost to tomorrow** | ComC3 R1 | **Overruled.** ComC1's Phase A (client-side re-ranking proof) is low-risk and should be done tonight. |
| **Recall@1 target >= 9/12** | Both R1 plans | Too aggressive for 5/12 baseline without score distribution knowledge. Corrected target: **>= 8/12** tonight. |
| **TA0-P2: `HERMES_BASE` env var** | Both R1 plans | Correctly backlogged. Profile exists and works. |
| **QDreamer2 further tightening** | OpeC4, OpeC2 | Correctly deferred. Need baseline data first. |

## Improved Plan

### Principle: Targeted source edits (Hermes logging + crash detection only) + measurement tools. No server-side Hybrid changes tonight.

### TA0 — Pipeline Reliability

**TA0-1: Full Hermes log dump (source edit: loop_orchestrator.py `_run_hermes`)**
- Replace `tail_trim(240)` with full file write to `<run_dir>/<phase>_<ts>.log`
- Header: `PHASE=<phase> TIMEOUT=<sec> EXIT_CODE=<code> TS=<iso8601>`
- Cap at 500KB (truncate-middle if exceeded)
- Risk: low — adding a file write, not changing behavior

**TA0-2: Silent crash detection (source edit: loop_orchestrator.py post-subprocess.run)**
- `if proc.returncode != 0 and not stdout.strip() and not stderr.strip():` -> raise `HermesSilentCrashError`
- Risk: low — 3-line guard, no behavior change for normal exits

**TA0-3: Config tuning (no edit, CLI flags)**
- `--qsleep-max-audit-fails=5` (from default 4)
- `--qsleep-hermes-timeout-sec=120` (from default 60)
- Risk: zero

**TA0-4: External Hermes crash wrapper (BR audit directory, no QSleep edits)**
- Batch file wraps `clm_qnight_runner.py` with `> %RUNNAME%_hermes_wrapper.log 2>&1`
- Captures full subprocess output including cases where Hermes.exe itself silently exits
- Risk: zero

### TA1 — Hybrid Query Ranking

**TA1-1: Client-side re-ranking proof (benchmark script addition: clm_qsleep_small_benchmark.py)**
- In benchmark, for each case: check if any expected_id appears in `hybrid_top_ids` at rank > 1
- Compute `boosted_recall@1`: count of cases where expected_id appears in top-K
- Report delta: `recall@1=(N) boosted_recall@1=(M) delta=+N`
- This **proves the potential** of exact-id boost without modifying the server
- Risk: low — addition to read-only benchmark script, no server state change

### TA2 — Benchmark and Gating

**TA2-1: Add 7 benchmark metrics (benchmark script addition: clm_qsleep_small_benchmark.py)**
1. Per-query latency (ms) — `time.time()` before/after `post_query()`
2. Recall@1 — count of `exact_rank == 1`
3. Keyword-vs-exact gap — `keyword_rank - exact_rank` (negative = keyword better)
4. Precision@5 — `expected_ids_in_top5 / 5`
5. Rank distribution histogram — count of cases at rank 1, 2, 3, 4, 5+
6. QSleep loop count — from `cluster_meta.json.loop_count` per case
7. QSleep fail reason — from `cluster_meta.json.stopped_reason` + `last_error`

**TA2-2: Post-processing script (BR audit directory)**
- Reads `results.jsonl` + each case's `qsleep_run` metadata
- Outputs enriched table with all 7 metrics + score distribution if available
- Script path: `<audit_dir>/post_process_benchmark.py`

**TA2-3: PowerShell retry wrapper (BR audit directory)**
- Wraps `clm_qnight_runner.py` with max 2 retries on non-zero exit
- After each retry: checks for `_failed/` dirs
- Wrapper path: `<audit_dir>/run_qnight_with_retry.ps1`
- Risk: zero

## Implementation Batches

### Batch 1 — Hermes Logging Fix (TA0-1 + TA0-2)
Files: `qsleep/controller/loop_orchestrator.py`
Changes:
- `_run_hermes`: replace 240-char tail with `<run_dir>/<phase>_<ts>.log` write
- After `subprocess.run`: empty-buffer non-zero exit -> `HermesSilentCrashError`
- Header metadata in log file

### Batch 2 — Benchmark Metrics (TA1-1 + TA2-1)
Files: `qsleep/bin/clm_qsleep_small_benchmark.py`
Changes:
- Add 7 metric fields to output
- Add client-side re-ranking proof (`boosted_recall@1`)
- Print enriched table after run

### Batch 3 — Config + Wrappers (TA0-3 + TA0-4 + TA2-2 + TA2-3)
Files (new, in BR audit directory):
- `<audit_dir>/post_process_benchmark.py`
- `<audit_dir>/run_qnight_with_retry.ps1`
No QSleep source edits.

### Batch 4 — Run Sequence
1. Run small benchmark (before tape) -> record baseline metrics
2. Run QSleep nightly with new config
3. Run post-processing on results
4. Run small benchmark again (after tape) -> measure delta
5. If acceptance criteria pass -> good; if not -> inspect `_failed/` dirs + Hermes logs

## Verification Plan

| Batch | Check | Method |
|---|---|---|
| Batch 1 | `py_compile` passes | `python -m py_compile loop_orchestrator.py` |
| Batch 1 | Log file created | Run one cluster, verify `<phase>_<ts>.log` exists in run dir |
| Batch 1 | Silent crash error raised | Mock subprocess with empty stdout/stderr + code=1 -> expect exception |
| Batch 2 | Benchmark runs without exception | `clm_qsleep_small_benchmark.py` exits clean on 2 test cases |
| Batch 2 | New metrics in output | grep for "latency", "recall@1", "rank_histogram" in benchmark output |
| Batch 3 | Post-processing runs | `post_process_benchmark.py <results.jsonl>` produces non-empty output |
| Batch 3 | Retry wrapper runs | `run_qnight_with_retry.ps1` executes and returns exit code |
| Batch 4 | Recall@5 no regression | Pre/post benchmark both show 12/12 |
| Batch 4 | `_failed/` dirs present if errors exist | Directory check after QSleep run |

## Acceptance Criteria

**Corrected from 5/12 baseline (not 7/12).**

### Hard Gates (ALL must pass)
1. **Benchmark Recall@5 = 12/12** — no regression from baseline
2. **Benchmark Recall@1 >= 8/12** — from 5/12 baseline (recover B3, B4, C2 from rank 2 to rank 1)
3. **QSleep coverage >= 24 new clusters** — double last night's 12
4. **QSleep fail rate <= 15%** — errors / groups_tried <= 15%
5. **Zero artifacts deleted** — all orphan/crash runs under `_failed/` for forensics

### Soft Gates
6. **Full benchmark report** includes all 7 new metrics (latency, recall@1, gap, precision, histogram, loop count, fail reason)
7. **Client-side boosted Recall@1 >= 10/12** — proving that exact-id re-rank would fix most cases
8. **A3 exact rank <= 3** — improvement from rank 5
9. **Keyword-vs-exact gap <= 1** for all 12 cases (currently A1, A5, B2 have gap of 3)

### Post-Run
10. **Failure ledger updated** with tonight's results (new, skipped, errors, _failed/ artifacts)
11. **Hermes log files present** for all convergence cases (raw sample)
12. **Post-processing output saved** alongside benchmark report

## Self-Check

- [x] Baseline correction: Recall@1 = 5/12, NOT 7/12 — explicitly fixed
- [x] Peer score: ComC1 scored 7/10 with evidence-based deductions
- [x] Keep: 10 items adopted from both plans, with cherry-picking rationale
- [x] Reject/Defer: 6 items rejected with specific reasons
- [x] Implementation batches: 4 sequential batches with file paths
- [x] Verification plan: per-batch checks with methods
- [x] Acceptance criteria: corrected from 5/12 baseline, realistic 8/12 tonight target
- [x] All changes traceable to evidence in benchmark report, CONTEXT.md, or peer artifacts
- [x] No undefined behavior: silent crash has distinct error type, log dumps have caps, boost is client-side only
- [x] Backlog explicitly framed (HERMES_BASE env var, max-versions, server-side boost, A3 query rewriting)
