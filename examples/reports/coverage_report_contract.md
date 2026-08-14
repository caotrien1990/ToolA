# Coverage / Report Contract

## Purpose

This file defines detailed coverage/report examples. Core rules remain the source of hard constraints. Use this file only for regression/coverage report formatting and examples.

## Coverage Scope

Coverage Dashboard is used only for regression/coverage tasks.

Do not run coverage during normal analyze/write tasks unless User explicitly requests regression/coverage.

## Required Coverage States

Each tool must be classified as exactly one of:

- pass
- fail
- skip
- untested
- retry-pass
- needs-manual-confirmation

## Hard Rules

- Never count skipped as pass.
- Never count untested as pass.
- Keep skipped separate from untested.
- Keep Baseline Failure View separate from After-Retry Coverage View.
- Reports use JSON as machine source, not Markdown.
- Never report error="N/A".
- Optional helper unavailable = skip/unavailable with reason, not task failure unless required for requested action.

## Minimum JSON Source

```json
{
  "coverage_run_id": "string",
  "project_path": "D:\\project",
  "started_at": "ISO-8601",
  "finished_at": "ISO-8601",
  "tool_total": "<live_tool_count>",
  "summary": {
    "pass": 0,
    "fail": 0,
    "skip": 0,
    "untested": 0,
    "retry_pass": 0,
    "needs_manual_confirmation": 0
  },
  "tools": [
    {

      "tool_name": "read_file_range",

      "tier": "T1",

      "protected": false,

      "mutating": false,

      "requires_session": true,

      "requires_token": false,

      "status": "pass",

      "baseline_status": "fail",

      "after_retry_status": "pass",

      "reason": "",

      "error_name": "",

      "retry_count": 1,

      "evidence": {

        "request_id": "",

        "response_excerpt": "",

        "artifact_path": ""

      }

    }

  ]

}

```
## Baseline Failure View

Use this view for first-run failures before retry.

Required columns:

| Tool | Tier | Baseline status | Error | Reason | CA next action |
| ---- | ---- | --------------- | ----- | ------ | -------------- |

## After-Retry Coverage View

Use this view after corrected retry / schema retry / helper retry.

Required columns:

| Tool | Tier | Final status | Retry count | Evidence | Remaining issue |
| ---- | ---- | ------------ | ----------- | -------- | --------------- |

## Status Meaning

pass = tool ran successfully and result matches expected behavior.

fail = tool ran but failed with real blocker.

skip = intentionally not run because optional/unavailable/unsafe/not relevant; must include reason.

untested = not attempted; must include reason.

retry-pass = failed first, then passed after corrected retry.

needs-manual-confirmation = tool needs User approval before safe execution.

## Report Template
COVERAGE REPORT
Summary
Tool total:
Pass:
Fail:
Skip:
Untested:
Retry-pass:
Needs manual confirmation:
JSON source:
Stop condition:
Baseline Failure View
| Tool | Tier | Baseline status | Error | Reason | CA next action |
| ---- | ---- | --------------- | ----- | ------ | -------------- |
After-Retry Coverage View
| Tool | Tier | Final status | Retry count | Evidence | Remaining issue |
| ---- | ---- | ------------ | ----------- | -------- | --------------- |
Remaining Blockers
| Tool | Severity | Blocker | Proposed fix |
| ---- | -------- | ------- | ------------ |
Skip / Untested Reasons
| Tool | Status | Reason |
| ---- | ------ | ------ |

## Guardrail

- File này là **example/spec phụ**, không được mâu thuẫn với core rule.
- Nếu có mâu thuẫn, core rule thắng.
- Không chứa token thật.
- Không hardcode project thật trong JSON ví dụ, chỉ dùng placeholder.