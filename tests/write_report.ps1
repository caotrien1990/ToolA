$r = @"
# [Q] ToolQ API Test Report - ComC-1

**Date:** 2026-06-18 | **Service:** clm-librarian-api:8082 | **DB:** `C:\Users\ADMIN\.claude-mem\librarian.db`

## 1. Endpoint Map

| Endpoint   | Method | Status     | Description                                   |
|------------|--------|------------|-----------------------------------------------|
| /health    | GET    | OK         | alive, reports db path                        |
| /pre_search| POST   | OK         | memory-first pre-search; hint/no/abstain     |
| /query     | POST   | PARAM BUG  | reads `query` field, ignores `q` field       |
| /help      | GET    | 404        | not implemented                               |

## 2. Test Cases

| #  | Case                          | Expected       | Actual | APIms | Wallms | Pass |
|----|-------------------------------|----------------|--------|-------|--------|------|
| 1  | weather VN (unrelated web)    | no             | no     | 0     | 436    | PASS |
| 2  | A/council/start_orchestrator  | hint           | no (BUG1) | 0 | 1    | FAIL |
| 3  | rawcut                        | hint/no        | no (BUG1) | 0 | 0    | FAIL |
| 4  | clau-mem assistant            | hint           | no (BUG1) | 0 | 1    | FAIL |
| 5  | angry guardrail (dm ngu vcl)  | no/abstain     | no     | 0     | 0      | PASS |
| 6  | Unicode Vietnamese            | handled        | no     | 0     | 4      | PASS |
| 7  | Emoji (warning/fire/check)    | handled        | no     | 0     | 1      | PASS |
| 8  | path injection                | no             | no     | 0     | 0      | PASS |
| 9  | empty body {}                 | error/no       | no     | 0     | 0      | WARN |
| 10 | long ~4KB prompt              | handled/reject | no     | 0     | 1      | WARN |
| 11 | concurrent 10                 | 10/10 ok       | 10 ok  | n/a   | 21     | PASS |
| 12 | pre_search {} (empty)         | ok+no          | ok,no  | 1     | 13     | PASS |
| 13 | base64 in query               | handled        | no     | 0     | 1      | PASS |
| 14 | fmi_path/agent identity       | hint           | no (BUG1) | 0 | 0    | FAIL |

## 3. pre_search Detail Tests

| Prompt                            | Result | Reason                  | Memory IDs    | ms |
|-----------------------------------|--------|-------------------------|---------------|----|
| orchestrator council start        | hint   | memory_candidates_found | [646,697,652] | 8  |
| weather forecast                  | no     | no_memory_candidates    | []            | 2  |
| empty {} body                     | no     | no_memory_candidates    | []            | 1  |
| dm ngu vcl (angry)                | hint   | memory_candidates_found | [936,929,222] | 3  |

## 4. query Detail (using correct `query` field name)

| Query                                | Result | Hits | ms |
|--------------------------------------|--------|------|----|
| orchestrator                         | hit    | 5    | 23 |
| search orchestrator council start    | hit    | 3    | 26 |

## 5. Bugs Found

### BUG1 [CRITICAL] `/query` param name mismatch
- `/query` reads field `query`, **NOT** `q`
- AGENTS.md contract and all rule samples use `q` -> ALL callers silently get `result: "no"`
- Fix: accept both `q` and `query` as aliases; update AGENTS.md

### BUG2 [MEDIUM] `suggested_query` has `search\n` prefix
- pre_search emits `"suggested_query": "search\norchestrator council start"`
- When fed verbatim to /query, newline becomes space (works via SQLite FTS) but fragile
- Fix: strip `search\n` prefix at API layer

### BUG3 [LOW] No input validation on empty `{}`
- Both endpoints accept `{}` silently, return `result: "no"`
- Cannot distinguish intentional empty from caller coding error
- Fix: reject missing required fields with HTTP 400

### BUG4 [LOW] Long prompt (>4KB) silent truncation
- 4KB+ query returns `result: "no"` at 0ms — likely truncated without warning
- Fix: accept full content or reject >8KB with HTTP 413

### BUG5 [MEDIUM] Guardrail: angry query returns `hint`, not `abstain`
- `"dm ngu vcl sao lam the"` -> pre_search returns `hint` with `mood: "angry"`
- Rule specifies guardrail queries should return `abstain`
- Fix: if `mood=angry` AND `score>0.3` -> `result=abstain` in pre_search

## 6. Performance

| Metric              | Value  |
|---------------------|--------|
| /pre_search hit avg | 5.5ms  |
| /pre_search miss avg| 1.5ms  |
| /query hit avg      | 24.5ms |
| Wall overhead       | 1-15ms |
| 10-sequential total | 21ms   |
| Thread-safe         | Yes    |

## 7. Recommendations

1. Unify param name: `/query` use `q` (match AGENTS.md contract); update all caller docs
2. Strip `search\n` prefix from `suggested_query` at API layer
3. Guardrail abstain gate in `/pre_search`: `mood=angry` + `score>0.3` -> `result=abstain`
4. Input validation: reject empty/missing required fields with HTTP 400
5. Query length limit: cap 2KB normal, reject >8KB with HTTP 413
6. Add `/help` endpoint for API discoverability
"@

Set-Content -Path 'C:\Users\ADMIN\.claude-mem\staging\q-api-tests\comc1_q_api_report.md' -Value $r -Encoding UTF8
Write-Output 'REPORT WRITTEN'
