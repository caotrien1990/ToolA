# ComC1 Detailed Audit — ToolQ QSleep Upgrade 2026-06-27

**Agent:** ComC1 verify-new-function Phase 1
**Audit packet:** `D:\BR\ToolQ_QSleep_Audit_20260627\verify\AUDIT_PACKET.md`
**Files audited:** 5 changed files
**Access boundary:** audit only, no fixes

---

## Finding 1 — TRUE_BUG_CANDIDATE

**File:** `loop_orchestrator.py`, `_run_hermes()` lines 100–106 + `loop_until_clean()` lines 194–206

**Problem:** `hermes_crash` (non-zero exit + empty stdout/stderr) raises `RuntimeError` caught as generic `Exception` in loop. Catch block increments `json_error_count` then unconditionally sets `stopped_reason='json_corrupt'`. Two distinct failure modes share the same counter. `MAX_JSON_ERRORS=3` is unreachable — a single crash always halts. No retry for transient Hermes crash.

**Lines:** loop_orchestrator.py:104-106 (raise), 195-206 (catch)
**Severity:** MEDIUM

---

## Finding 2 — TRUE_RISK_CANDIDATE

**File:** `loop_orchestrator.py`, `_write_phase_log()` lines 53–70

**Problem:** `path.write_text()` WITHOUT try/except. If filesystem full or run_dir unwritable, unhandled OSError propagates through `_run_hermes()`, potentially masking TimeoutExpired or corrupting cluster_meta.json mid-update. Contrast: `_log_event()` safely wraps in try/except; `_write_phase_log()` does not.

**Lines:** loop_orchestrator.py:68
**Severity:** LOW-MEDIUM

---

## Finding 3 — TRUE_BUG_CANDIDATE

**File:** `qwake.py`, `teardown_model_server()` lines 86–97

**Problem:** `pid_path.unlink(missing_ok=True)` in `finally` block ALWAYS removes pid file even if `taskkill /F /PID` failed (access denied, process not found). If kill fails silently: llama-server remains running, subsequent `teardown_model_server()` returns False (pid file gone), no mechanism detects orphaned GPU process.

**Lines:** qwake.py:86-97
**Severity:** MEDIUM

---

## Finding 4 — TRUE_RISK_CANDIDATE

**File:** `clm_qnight_runner.py`, auto-apply block lines 133–143

**Problem:** `apply_all` POST to `http://127.0.0.1:8082/qjobs/apply_all` wrapped in bare `except Exception: pass`. If server unreachable or call times out (600s), error silently swallowed. Pipeline continues — downstream QSleep operates on stale data since candidate was never applied.

**Lines:** clm_qnight_runner.py:143
**Severity:** MEDIUM

---

## Finding 5 — TRUE_RISK_CANDIDATE

**File:** `clm_qnight_runner.py`, `_maintenance()` lines 78–95

**Problem:** FTS rebuild (DELETE + INSERT) not wrapped in explicit transaction. Python sqlite3 auto-commits each statement. If INSERT fails partway, FTS index left empty. QSleep redistill finds zero FTS candidates silently.

**Lines:** clm_qnight_runner.py:87-93
**Severity:** LOW-MEDIUM

---

## Finding 6 — TEST_GAP_CANDIDATE

**File:** `loop_orchestrator.py`, `_run_hermes()` lines 93–113

**Gap:** `hermes_crash` branch (empty stdout/stderr + non-zero exit) not unit-testable without monkeypatching subprocess.run. No test exists. Core observability improvement of this upgrade is untested.

**Lines:** loop_orchestrator.py:104
**Severity:** MEDIUM

---

## Finding 7 — TEST_GAP_CANDIDATE

**File:** `qwake.py`, `gc_orphan_runs()` lines 122–146

**Gap:** `_failed` archive (replaced deletion) has no test: _failed dir creation, shutil.move, naming collision, _-prefixed skip, marker detection.

**Lines:** qwake.py:129-143
**Severity:** LOW-MEDIUM

---

## Finding 8 — TEST_GAP_CANDIDATE

**File:** `clm_qnight_runner.py`, GPU mode check lines 107–117

**Gap:** GPU mode pre-check wrapped in bare `except Exception: pass`. If mode check/set fails, pipeline continues in whatever GPU mode server was in. No validation that mode was actually set.

**Lines:** clm_qnight_runner.py:117
**Severity:** LOW

---

## Finding 9 — MISUNDERSTANDING_OR_NEEDS_VERIFICATION

**File:** `loop_orchestrator.py`, `_log_event()` line 40 and `_update_meta()` line 159

**Issue:** Ledger path computed as `run_dir.resolve().parent.parent / 'ledger'` assuming 2-level nesting under qsleep root. If run_dir at different depth, ledger path wrong. Logs/progress writes silently fail (try/except pass).

**Lines:** loop_orchestrator.py:40,159
**Severity:** NEEDS VERIFICATION

---

## Finding 10 — MISUNDERSTANDING_OR_NEEDS_VERIFICATION

**File:** `clm_qsleep_small_benchmark.py`, `cluster_key()` line 179 and `find_run()` lines 182–194

**Issue:** Prefix matching on cluster_id. Overlapping raw_id prefixes (raw-1-2- vs raw-1-2-3-) match both. find_run picks most recent by mtime — possibly wrong. Current 12 cases don't overlap, but risk if extended.

**Lines:** clm_qsleep_small_benchmark.py:179-194
**Severity:** LOW

---

## Finding 11 — DOCS_OR_ACTIVATION_GAP

**File:** `clm_qsleep_small_benchmark.py`, line 248

**Issue:** `boosted_exact_r1 = hybrid_exact_hits` (NOT `hybrid_exact_r1`). Markdown report correctly labels "Client-side boosted exact Recall@1 proof". JSONL contains both hybrid_exact_recall_at_1 (5/12) and client_side_boosted_exact_recall_at_1 (12/12) — naive consumer could misinterpret boosted as real R@1.

**Lines:** clm_qsleep_small_benchmark.py:248
**Severity:** LOW

---

## Finding 12 — DOCS_OR_ACTIVATION_GAP

**File:** `run_qnight_with_retry.ps1`

**Issue:** `$MaxRetries` controls total attempts, not retries. With MaxRetries=2, loop runs 3 times (attempts 1,2,3). Parameter name suggests 2 retries = 2 extra attempts, but implementation gives 2 retries = 3 total. Off-by-one in naming.

**Lines:** run_qnight_with_retry.ps1:2 (param), :13 (loop)
**Severity:** LOW

---

## Finding 13 — PASS

**File:** `run_qnight_with_retry.ps1`, -DryRun switch
**Verdict:** Clean dry-run. Only echoes command and exits 0. Verified by audit packet.

---

## Finding 14 — PASS

**File:** `qwake.py`, `gc_orphan_runs()` _failed archive
**Verdict:** Archive correct: naming collision handled, _-prefixed dirs skipped, recent runs protected by max_age, complete runs by marker.

---

## Finding 15 — PASS

**File:** `clm_qsleep_small_benchmark.py` — read-only guarantee
**Verdict:** Strictly read-only (SELECT only on DB, file reads on QSleep runs). Only writes to staging/qsleep_benchmark/. No UPDATE/DELETE/INSERT/DROP.

---

## Summary

| # | Classification | File | Severity |
|---|---|---|---|
| 1 | TRUE_BUG_CANDIDATE | loop_orchestrator hermes_crash/json_corrupt conflation | MEDIUM |
| 2 | TRUE_RISK_CANDIDATE | loop_orchestrator unwrapped write_text | LOW-MEDIUM |
| 3 | TRUE_BUG_CANDIDATE | qwake pid file always deleted on failed kill | MEDIUM |
| 4 | TRUE_RISK_CANDIDATE | clm_qnight_runner silent apply_all failure | MEDIUM |
| 5 | TRUE_RISK_CANDIDATE | clm_qnight_runner FTS rebuild non-transactional | LOW-MEDIUM |
| 6 | TEST_GAP_CANDIDATE | loop_orchestrator hermes_crash untested | MEDIUM |
| 7 | TEST_GAP_CANDIDATE | qwake _failed archive untested | LOW-MEDIUM |
| 8 | TEST_GAP_CANDIDATE | clm_qnight_runner GPU mode set untested | LOW |
| 9 | MISUNDERSTANDING | loop_orchestrator ledger path depth assumption | NEEDS VERIFY |
| 10 | MISUNDERSTANDING | benchmark prefix matching overlap risk | LOW |
| 11 | DOCS_OR_ACTIVATION_GAP | benchmark boosted R@1 naming | LOW |
| 12 | DOCS_OR_ACTIVATION_GAP | PS1 MaxRetries naming | LOW |
| 13 | PASS | PS1 dry-run safety | — |
| 14 | PASS | qwake _failed archive correctness | — |
| 15 | PASS | benchmark read-only guarantee | — |

### Overall: **CONDITIONAL PASS**

- Critical/blocking: 0
- Medium (fix before overnight): Findings 1, 3, 4, 6
- Low (tech debt): Findings 2, 5, 7, 8, 10, 11, 12
- Needs verification: Finding 9

**Primary recommendation:** Finding 1 breaks stop-condition logic (MAX_JSON_ERRORS=3 unreachable — single crash kills cluster). Finding 3 can orphan llama-server silently. Both should be addressed before overnight QNight run.
