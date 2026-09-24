---
name: tool_tiers
description: |
  Use to pick the right tool tier for any task — T1 read, T2 write/verify, T3
  session/audit, T4 runtime. Activate when CA must choose between [A] tools, native
  shell tools, or T4 browser/desktop. Returns tier + skill folder pointer.
trigger:
  - task_pattern: "which tool | what tier | T1 | T2 | T3 | T4 | best for this"
  - tool: "tool_decision_hint"
preconditions:
  - "Health OK"
related_skills:
  - 00_skill_router
  - 06_token_auth
  - 12_native_audit
  - 18_runtime_dom_T4
risk_level: low
---

# 04 — Tool Tiers

## The 4 tiers

| Tier | Label | Token | Examples |
|------|-------|-------|----------|
| **T1** | Discover / Analyze / Read | No | find_symbol, search_code, read_file_range, list_structure, get_project_summary |
| **T2** | Mutate / Verify / Refresh | Yes (most) | write_fix, validate, project_map, run_project_command, backup_project |
| **T3** | Session / Audit / Auxiliary | Yes (mutating) | open_session, close_session, log_native_event, prepare_ca_session |
| **T4** | Runtime / DOM / Browser / Server lifecycle | Mixed | open_browser_page, server_lifecycle_status, screenshot_page, electron_devtools_log |

## Selection Matrix

```
Project work?                    → [A]-first via CA_TOOL_POLICY_MATRIX
                                   (.council/CA_TOOL_POLICY_MATRIX.json;
                                    skill 21_tool_matrix references/)
Read code/files (project)?       → T1 [A] (read_file_range / search_code) — native forbid
Need to write/modify?            → T2
Multi-file or critical write?    → T2 [A] (rollback essential)
Single-file <30 lines low-risk?  → T2 write_fix; native only under Anh's break-glass (skill 12)
Auth/token/delete?               → T2 [A] always (governance)
Open/close session?              → T3
Native used / off-project host?  → T3 log_native_event / create_pending_audit
UI bug / runtime / browser?      → T4
Server stuck / port issue?       → T4c
CLI C-suffix worker?             → HTTP-only (no native project FS)
2 failed code-fix cycles?        → FORCE T4 check before more code
```

Policy matrix is the **routing SOT** for project task classes; this skill picks tier/tool *after* matrix classification. Do not treat “Native faster” as override of `native_policy=forbid`.

## Token-protection map

Token-gated tools = `requires_token` in `GET /api/tools/<tool>/schema` (SOT `code_tools.TOKEN_REQUIRED_TOOLS`). `protected` on `/api/tools/names` is the MUTATING gate, not the token gate: `write_fix`/`write_new_file` are protected and need NO token. [A] injects the token itself while AUTO_TOKEN_INJECT is on (the default); a MUTATION_TOKEN_REQUIRED answer is the only cue to fetch one (06_token_auth).

## Tier transitions in a task

A typical task spans tiers:
```
T1 (find_symbol/read) → T1 (analyze) → T3 (open_session) →
T2 (write_fix dry_run) → T2 (apply) → T2 (validate) → T3 (close_session)
```
Each tier transition is a natural checkpoint for the routing decision.

## Anti-patterns

- ❌ Native Bash for repo file ops when API up (loses governance)
- ❌ Skipping T3 open_session before T2 write (server returns ACTIVE_SESSION_REQUIRED)
- ❌ Calling T2 without first fetching token (returns MUTATION_TOKEN_REQUIRED; retry once)
- ❌ Using T1 when T4 needed for UI bug (you'll loop guessing CSS)

## Inline decision examples

| Task | Tier | Reason |
|------|------|--------|
| "Find where btnStep1 is defined" | T1 find_symbol | structural lookup |
| "Refactor 5 files" | T2 [A] | atomic + audit |
| "Add 1 export line" | Native + log | t < 30s iteration |
| "Bug: UI not rendering" | T4a | DOM truth source |
| "Server stuck on port" | T4c | lifecycle inspection |
| "Wrap up + summarize" | T3 close_session + session_audit_summary | finalize trail |

## Guardrails

- Tier label is server-authoritative via `protected/mutating/requires_session/requires_token` fields
- CA must NEVER bypass tier (e.g., shell-out to native for what T2 covers)
- After 2 failed fix-test cycles with code changes but UI unchanged → mandatory T4 check (CORE_RULE)

## Operational Impact

- T1 zero-token, high-volume — favor for analysis
- T2 token + 1 retry rule keeps overhead minimal
- T4 read-only (T4a/T4c read) gives confidence without trigger overhead
- Decision matrix saves ~3 round-trips per task vs guessing

## Harness

- Test 1: Pick tier for 30 cases in `decision_examples.md` → ≥ 27 match expected
- Test 2: `/api/tools` returns protected:true matches PROTECTED_TOOL_NAMES set count
- Test 3: T2 call without token → 403 + retry_sequence includes `GET /api/token`
- Test 4: T4 read tool (T4a inspect) without trigger → 200, no error
