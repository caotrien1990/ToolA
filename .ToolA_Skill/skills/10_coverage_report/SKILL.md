---
name: coverage_report
description: |
  Use when reporting regression test results, tool coverage, skill loop test pass-rate.
  Enforces strict classification (pass/fail/skip/untested/retry-pass) per
  coverage_report_contract.md. Activate after run_test_profile or skill loop run.
trigger:
  - task_pattern: "coverage | report | pass rate | regression | test summary"
  - tool: "run_test_profile (post-call) | session_audit_summary"
preconditions:
  - "Test run completed (or skill loop matrix executed)"
related_skills:
  - 09_testing_validation
  - 17_session_audit
risk_level: low
---

# 10 — Coverage Report

## 5-class taxonomy (HARD)

| Class | Definition | Counted as |
|-------|-----------|------------|
| **pass** | Assertion succeeded, no retry needed | success |
| **fail** | Assertion failed | failure (P0) |
| **skip** | Explicitly skipped (`@unittest.skip`, `pytest.skip`, conditional) | NOT pass, separate bucket |
| **untested** | Function/class with no test file mention (audit gap) | NOT pass |
| **retry-pass** | Passed only on retry (flaky) | success WITH warning flag |

**Never collapse skip/untested into pass**.

## Report JSON contract

```json
{
  "ok": true,
  "report_id": "cov-<ts>",
  "profile": "unittest|pytest|skill_loop",
  "totals": {
    "items": 42,
    "pass": 30,
    "fail": 2,
    "skip": 5,
    "untested": 4,
    "retry_pass": 1
  },
  "fail_items": [
    { "name": "...", "file": "...", "line": ..., "error": "..." }
  ],
  "skip_items": [
    { "name": "...", "reason": "..." }
  ],
  "untested_items": [
    { "name": "...", "file": "..." }
  ],
  "retry_pass_items": [
    { "name": "...", "retries": 1, "flake_risk": "low|med|high" }
  ],
  "computed_at": "<iso>",
  "live_tool_count": <int>
}
```

Markdown rendering of the same JSON for User-readable export.

## Skill loop test report (R6)

Variant for skill router validation:
```json
{
  "ok": true,
  "matrix_size": 30,
  "router_accuracy": 0.93,
  "details": [
    { "case_id": "R1", "input": "Health fail", "expected_skill": "01_bootstrap_health", "actual_skill": "01_bootstrap_health", "match": true },
    ...
  ],
  "negative_tests_passed": 7,
  "loops_required": 1
}
```

Pass threshold: ≥ 95% router accuracy + 100% negative_tests passed.

## Tool coverage report

For "verify all 48 tools" tasks:
- Iterate `/api/tools` list
- Smoke test 1 representative call per tool (with payload_example)
- Classify: ok=true → pass; specific error_name → fail; missing/404 → untested

Use `<live_tool_count>` placeholder in template, substitute count from /api/tools length.

## Anti-patterns

- ❌ `"total tests: 5, passed: 5"` when 2 were skipped (lying via aggregation)
- ❌ Omitting `untested_items` (silent gap)
- ❌ Counting flaky retry-pass without flag
- ❌ Hardcoding tool count (use live_tool_count from `/api/tools`)
- ❌ Coverage report for "no requested" — only run when explicitly asked or regression context

## Guardrails

- Adaptive guidance: coverage report only when User requests regression, not for every task
- Report MUST surface all 5 buckets even if some are zero
- File a P0 issue automatically if `fail > 0`

## Operational Impact

- Compact JSON < 2 KB for typical 30-50 item run
- Markdown export ~5 KB
- Skill loop test matrix doc-only when accuracy ≥ 95% (else attach details)

## Harness

- Test 1: 10 tests with 7 pass / 2 fail / 1 skip → report has totals + fail_items + skip_items, no merge
- Test 2: Skill loop 30 cases / 28 match → accuracy 0.93 (just below 0.95), report flags below-threshold
- Test 3: Tool coverage on /api/tools — each tool classified, live_tool_count from real count
- Test 4: Untested items present → report visible, no silent omission
