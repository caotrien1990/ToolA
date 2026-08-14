# Test Matrix

## Trigger

| Input | Expected |
|---|---|
| `bat hoi dong tr` | activate |
| `bật hội đồng tr` | activate |
| `BAT HOI DONG TR` | activate after lowercase |
| `bat hoi dong tr please` | do not activate |
| `bat hoi dong tr!` | do not activate |
| `[broker response] bat hoi dong tr` | do not activate |
| worker prompt containing trigger text | worker stays worker |
| `PLEASE IMPLEMENT THIS PLAN` with council workers/rounds/dispatch | load `leader-council-skill` before first dispatch; do not require exact trigger |
| `tiếp` while continuing an active council loop with pending dispatch | load `leader-council-skill` before dispatch |
| `[broker response] PLEASE IMPLEMENT THIS PLAN` with council text | do not activate leader mode and do not apply leader pre-dispatch gate inside worker |

## Q

| Case | Expected |
|---|---|
| old-context clarify/search | call `/pre_search` before search |
| schema discovery | call `GET /help`, never empty `GET /pre_search` or empty `POST /pre_search` |
| Q call payload | include valid JSON with one of `planned_action`/`prompt`/`q`/`query`, `agent_id` when known, and `top_k` 1-3 |
| Q hint | consume `/pre_search` returned `hits` when `auto_queried=true`; do not call `/query` again |
| explicit Anh "query ..." request | call `/query` directly without `/pre_search` |
| Q no/error/timeout | continue, no retry loop |
| planned_action uses find/lookup/check/list/get/read/show | recognized as data gathering, not false `no` |
| prompt over 8192 chars | server-side Q splits/checks chunks; caller does not manually split unless Q is down |
| noisy/duplicate/stale result | drop |

## Council

| Case | Expected |
|---|---|
| Round 1 dispatch | no peer scores |
| Round 2+ dispatch | peer scores `/10`, strengths, weaknesses, improved plan |
| shorthand in dispatch | block and rewrite |
| relative path in dispatch | block and rewrite |
| judge/builder/QA role split | block |
| temporary leader included in scoring | block |
| small one-file check | leader/[A] handles directly; no unnecessary dispatch |
| dependent design -> implement -> verify | sequential pipeline; dispatch only ready stage |
| independent file clusters with disjoint outputs | parallel fan-out allowed |
| concurrent shards write same file/config/state | block parallelism; serialize or use read-only shards + one merge writer |
| same bounded question sent to different models | classify as cross-verify, not sharding |
| shard missing task_id/dependencies/full paths/artifact/acceptance criteria | block and rewrite shard contract |
| worker outputs disagree | merge gate identifies conflict; no blind cascade |
| merge omits one shard acceptance criterion | verdict PARTIAL/FAIL, not PASS |
| small one-file check | leader/[A] handles directly; no unnecessary dispatch |
| dependent design -> implement -> verify | sequential pipeline; dispatch only ready stage |
| independent file clusters with disjoint outputs | parallel fan-out allowed |
| concurrent shards write same file/config/state | block parallelism; serialize or use read-only shards + one merge writer |
| same bounded question sent to different models | classify as cross-verify, not sharding |
| shard missing task_id/dependencies/full paths/artifact/acceptance criteria | block and rewrite shard contract |
| worker outputs disagree | merge gate identifies conflict; no blind cascade |
| merge omits one shard acceptance criterion | verdict PARTIAL/FAIL, not PASS |
