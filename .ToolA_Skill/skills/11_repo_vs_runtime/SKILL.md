---
name: repo_vs_runtime
description: |
  Use when CA must decide if a bug requires repo-side inspection (code) or runtime-side
  confirmation (DOM/browser/server state). Enforces the HARD rule "after 2 failed
  fix-test cycles where code changed but runtime unchanged → check T4 BEFORE further
  code edits". Bridges source truth and live truth.
trigger:
  - task_pattern: "UI bug | not rendering | button doesn't work | cache | stale | runtime issue"
  - error_name: "STALE_SERVER_SUSPECTED | UI_NOT_RENDERED"
preconditions:
  - "Health OK"
  - "Repository accessible via [A] T1 tools"
related_skills:
  - 18_runtime_dom_T4
  - 16_server_lifecycle
  - 05_write_pipeline
risk_level: medium
---

# 11 — Repo vs Runtime routing

## The fundamental rule (CORE_RULE.txt)

> "After 2 failed fix-test cycles where code changed but runtime unchanged → 18_runtime_dom_T4 BEFORE further code edits."

CA must enforce this. It is the **single biggest feedback-loop saver** for UI/integration bugs.

## Decision flow

```
Bug reported →
  Is it visible-in-code (syntax / logic clearly wrong)?
  ├─ yes → T1 read + T2 fix + T4a confirm
  └─ no:
    Is it visible-in-runtime (CSS / event / cache / server lifecycle)?
    ├─ likely runtime → T4 FIRST
    └─ unclear → start with T1 ONE round, then if no fix → T4

After fix attempt #1 → re-test → no change?
  → STILL TRY repo angle (#2)
After fix attempt #2 → re-test → no change?
  → FORCE T4 (server lifecycle, cache, bundle version, DOM render)
  → If T4 reveals cache/server issue → user action (restart .bat) → then re-test
  → If T4 reveals genuine code issue → back to repo with new info
```

## Repo-side tools (T1+T2)

- `find_symbol`, `search_code` (skill 15)
- `read_file_range` for precise scope
- `write_fix` for fix (skill 05)
- `validate` post-write (skill 09)

## Runtime-side tools (T4)

- `open_browser_page + read_page + screenshot_page` — DOM truth
- `read_console_errors + read_network_errors` — runtime errors
- `server_lifecycle_status(port)` — stale server detect
- `static_asset_cache_check(url)` — browser cache stale
- `bundle_version_check(html_url)` — bundle reloaded?

## Symptoms → which side first

| Symptom | Start with | Reason |
|---------|-----------|--------|
| "Button does nothing" | T4a click + console | Event handler may not be bound at runtime |
| "Function returns wrong value" | T1 find_symbol + read | Logic bug, code-visible |
| "Page shows old text after save" | T4c cache_check + bundle_version | Cache/stale |
| "Test fails locally pass in CI" | T4a + env diff | Runtime env divergence |
| "Server stuck on port" | T4c server_lifecycle_status | Lifecycle |
| "TypeError undefined is not function" | T1 search_code + T4a console_errors | Both sides |

## V15 R8 enforcement

CA tracking turn counter implicitly: if same bug + same file edited 2x with no test improvement, the third turn MUST start with a T4 check (don't write more code).

UI surfaces this via the Decision Hint panel (server-side `tool_decision_hint` returns suggested_skill=18_runtime_dom_T4 when task signals "same bug repeated").

## Provider/protocol boundary failures

For resumed-session tool replay errors, source truth and runtime truth may diverge across a protocol-translating custom provider. Use `references/cross-protocol-tool-replay.md` to trace one `call_id` from persisted history through the final request boundary, distinguish a real DB orphan from translator/retry reconstruction loss, and design a non-destructive one-shot compatibility retry.

## Anti-patterns

- ❌ Editing CSS 3 times to fix "not visible" without checking T4a — culprit usually cache
- ❌ Adding `console.log` for debug when T4a `read_console_errors` already lists all
- ❌ Restarting [A] orchestrator when actually app server (Rawcut/etc.) needs restart
- ❌ Assuming `Ctrl+F5` cleared cache without `static_asset_cache_check` verification

## Guardrails

- 2-cycle rule is HARD per CORE_RULE — never skip T4 escalation
- T4a read-only OK in DEFAULT mode (no trigger)
- T4b/c/d mutate require EXECUTE trigger

## Operational Impact

- Single T4 check saves 3-5 wasted edit cycles
- ~50-70% UI bug feedback rounds reduced when 2-cycle rule enforced
- Token cost per UI bug: from ~50 KB (guess loops) down to ~10 KB (T4-first)

## Harness

- Test 1: simulated UI bug → 2 code fixes → no change → next attempt CA must call T4
- Test 2: cache-stale scenario → T4c flags → User restart .bat → re-test pass
- Test 3: server-restart-needed scenario → T4 detects port stale → recommend action
- Test 4: real code bug → T1 find_symbol succeeds first attempt (skill correctly chose repo-first)
