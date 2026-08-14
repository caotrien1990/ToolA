# OPE REAL-TEST — [A] new tools items 1/3/4 (live /api/call verify, 2026-08-07)

TASK_ID: A_REALTEST_P134_20260807 | Worker: OpeC. Output: Vietnamese.
Repo [A:P]: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`

## Context
13 new tools are LIVE in [A] (gen f492ae687307) after restart. Clau already smoke-verified query_sqlite (37 tables) + http_check (200 on :8082). This task = REAL end-to-end test of EVERY tool via `POST http://localhost:8083/api/call/<tool>` (not import self-test), including the under-tested item-4 process/browser tools. Mutating tools (item-3, start_app/stop_app) require X-Orchestrator-Token (GET http://localhost:8083/api/token).

## Objective
Confirm each of the 13 tools works end-to-end via /api/call against real targets, produces correct structured JSON, mutating tools actually mutate + reject bad input, and none returns 500/NotFound/unhandled exception.

## Tools + required real test (use %TEMP% scratch; clean up after)
### item-1 (T1, no token)
- query_sqlite: schema + a real SELECT + a rejected write (DROP) on `D:\...\21. ToolQ\librarian.db`.
- file_profile: on a real .py and a real binary/large file — check size/encoding/line_count/language.
- sample_data_file: head + tail + random on a CSV/JSONL you create under %TEMP%.
- read_chunks: by line and by byte, with has_more boundary.
### item-3 (T2, TOKEN) — scratch files under %TEMP% only
- insert_text: append + at specific line; VERIFY the file content actually changed.
- replace_symbol: replace a def/class/var in a scratch .py; verify; missing-symbol rejection.
- move_file: move a scratch file; verify src gone + dst exists.
- copy_range / cut_range: verify destination content + (cut) source removal; dry-run no-write.
### item-4 (T2)
- http_check: 200 on an EXTERNAL server (e.g. http://127.0.0.1:8082/health) + a status-mismatch rejection. Do NOT point at :8083 (self-call deadlock).
- start_app: start a REAL bounded process, e.g. `python -m http.server 8791` under %TEMP%; capture app/session id.
- stop_app: stop the process started above by id; verify port freed.
- browser_smoke_test: against a real local URL (e.g. http://127.0.0.1:8082/health or the started http.server); headless; verify ok + clean up browser session.

## Constraints
- Read-only on [A] source; do NOT edit [A] code or restart [A]. Only exercise the tools + write scratch under %TEMP%.
- Token for mutating tools via GET /api/token; never print the token.
- Clean up all scratch files + started processes/browser sessions before final report.

## Acceptance
- Each of 13 tools: PASS (works + correct output/mutation) or FAIL (with exact error). A rejected-bad-input case counts as PASS when it returns a proper structured error (not a crash).

## Stop conditions
- Any tool returns a 500 / unhandled traceback / NotFound → record + continue others; do not retry >2x.
- Do NOT restart [A] or the controller. Do NOT touch item files or council config.

## Output contract
- Artifact-first: full per-tool table -> `.council/OPE_REALTEST_A_TOOLS_20260807_RESULT.md`.
- Broker reply <=5 lines: N/13 PASS + any FAIL names + artifact path + marker END_OPE_REALTEST_P134. Vietnamese.
