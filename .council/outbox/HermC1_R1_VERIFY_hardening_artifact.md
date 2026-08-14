# RES: HermC1_R1 — VERIFY artifact hardening qsleep_stdout_fix_20260701

## (1) 6 FIX — CO / THIEU

| # | Fix | Trang thai | Bang chung |
|---|-----|-----------|------------|
| 1 | QSleep stdout reconfigure (`TextIOWrapper` → `sys.stdout.reconfigure()`) | **CO** | AUDIT_PACKET.md L22-23, COMC1_FIX_VERIFY.md L24-26, grep zero TextIOWrapper trong active source (verified: exit code 1 = 0 matches), py_compile 7/7 PASS |
| 2 | qsleep-max cap `groups_tried` (không runaway qua skip/error groups) | **CO** | AUDIT_PACKET.md L24 + L61-63 (monkeypatch unit: `groups_tried=1, skipped=1`), COMC1_TIMEOUT_DEFER_VERIFY.md L11 (`tried >= max_clusters`), EVIDENCE.md L18 (staged Qnight `groups_tried=1`) |
| 3 | Timeout/orphan kill process tree (`taskkill /F /T`) + clean lock/lease | **CO** | COMC1_TIMEOUT_DEFER_VERIFY.md L8-11 (`_run_capture_tree` timeout→`taskkill /F /T`, `_cleanup_kick_timeout` xoá lock+lease), EVIDENCE.md L18-19 (short loop timeout=8, no stale lock/lease/process) |
| 4 | Hermes OSError cleanup handler | **CO** | COMC1_TIMEOUT_DEFER_VERIFY.md L9 (`OSError` → `"kick_oserror"`), EVIDENCE.md L16 |
| 5 | Defer bad groups jsonl (24h cooldown) | **CO** | COMC1_TIMEOUT_DEFER_VERIFY.md L12 (`_defer_group` 86400s), EVIDENCE.md L21-22 (raw 30-31-32-33-33, 69-70-71-72-73 deferred 24h), test_defer_roundtrip_and_expiry PASS |
| 6 | Deterministic test `bin\test_qsleep_timeout_defer.py` + py_compile | **CO** | File exists `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\bin\test_qsleep_timeout_defer.py`, py_compile PASS (verified), 3/3 tests PASS (verified: test_run_capture_tree_timeout/test_defer_roundtrip_and_expiry/test_cleanup_kick_timeout_scope) |

**KET LUAN: 6/6 CO bang chung. Khong thieu.**

---

## (2) ACCEPTANCE CRITERIA STATUS (DEVINE 6R Plan)

| Criterion | Trang thai | Trich dan |
|-----------|-----------|-----------|
| **C4** — semantic_verified >= 40% | **FAIL** | DEVINE_ROUND_FINAL_STATUS.md L61: "C4 semantic_verified >=40%: not achieved; current ... 2 semantic_verified out of 17 plan chunks, global qwen status is 2/34 scored" |
| **C7** — QSleep repair success >= 80% | **FAIL** | DEVINE_ROUND_FINAL_STATUS.md L59: "C7 / QSleep repair success >=80%: not achieved as a release gate. Current canary proves non-blocking behavior, not 80% repair success" |
| **C9** — Recall@5 >= 0.70 / nDCG@5 baseline | **FAIL** | DEVINE_ROUND_FINAL_STATUS.md L60: "C3/C9 retrieval regression/golden recall: not fully achieved; only targeted vector search canary was run" |
| **C10** — Rollback tests (schema/verify/vector/purge) | **FAIL** | DEVINE_ROUND_FINAL_STATUS.md L63: "C10 are not fully implemented or passed" |
| **Full 73-test matrix** (39A+24B+10C) | **FAIL** | DEVINE_ROUND_FINAL_STATUS.md L65-66: "Full DEVINE plan is NOT complete ... because the 73-test matrix and release gates C3/C4/C7/C9/C10 are not fully implemented or passed" |

**KET LUAN: Tat ca 4 acceptance criteria va full 73-test matrix deu FAIL (chua dat). Chi co QSleep/Qnight blocker da CLOSED cho staged/canary execution.**

---

## (3) DEVINE COMPLETION STATUS

DEVINE_ROUND_FINAL_STATUS.md L63-66:

> Decision: "QSleep/Qnight blocker is CLOSED for staged/canary execution."
> "Full DEVINE plan is NOT complete as a release claim because the 73-test matrix and release gates C3/C4/C7/C9/C10 are not fully implemented or passed. Continue with DEVINE execution as staged/canary, not LIVE promotion."

Next steps (L68-73): bounded DEVINE gate pass — feedback loop, golden retrieval benchmark, Qwen VERIFY calibration, rollback tests.

**KET LUAN: DEVINE CHUA XONG. Chi blocker QSleep/Qnight da giai quyet. Can chay bounded DEVINE gate pass truoc khi LIVE promotion.**

---

## RES TOM TAT

- **Fix**: 6/6 CO bang chung (stdout reconfigure, cap groups_tried, timeout tree kill, OSError cleanup, defer jsonl, deterministic test)
- **AC status**: C4 FAIL (2/34 scored), C7 FAIL (chua 80%), C9 FAIL (chua golden), C10 FAIL (chua rollback test). 73-test matrix CHUA DAT.
- **DEVINE**: CHUA XONG — chi blocker QSleep/Qnight da dong. Can chay bounded pass tiep.

END_HERMC1_R1
