---
name: runtime_dom_T4
description: |
  Use this skill when the task involves runtime UI verification, DOM inspection,
  browser-visible behavior, Electron app driving, server lifecycle checks, or
  any case where source-code inspection alone cannot confirm the bug or fix.
trigger:
  - task_pattern: "UI bug | DOM | button not visible | progress bar | reload | Electron | browser"
  - error_name: "STALE_SERVER_SUSPECTED | UI_NOT_RENDERED | CACHE_STALE | CDP_UNREACHABLE | DESKTOP_INTERACTION_FAILED | WINDOW_NOT_FOUND"
  - tool: "open_browser_page | read_page | query_selector | evaluate_js | server_lifecycle_status | desktop_app_focus | desktop_click | query_dom_candidates | describe_page | wait_for_selector | compare_dom_snapshot | click_and_checkpoint_loop | ui_state_summary | read_console_errors | read_network_errors | desktop_window_list | read_value | save_log_snapshot | invoke_app_function | fill_folder_dialog | cancel_native_dialog"
preconditions:
  - "[A] is reachable through MCP/API"
  - "Target app server is running when the test needs a live app"
  - "For Electron/Chromium CDP: process was launched with --remote-debugging-port, usually 9223"
related_skills:
  - 03_path_session
  - 08_command_windows
  - 12_native_audit
  - 16_server_lifecycle
risk_level: medium
---

# T4 - Runtime / DOM / Browser / Electron Driver

T4 bridges the repo/runtime gap. Use it when code inspection cannot prove browser,
desktop, or server behavior.

## Runtime UI Fix Workflow

1. Use `list_cdp_targets(cdp_port=9223)` or `launch_electron_with_cdp` to confirm a page is attached.
2. Use `verify_active_electron_source(page_id, expected_path=...)` before editing Electron HTML/JS/CSS.
3. Use `read_console_errors(page_id, level="error")` for a quick runtime error scan.
4. Use `query_selector`, `read_value`, `read_page`, or `evaluate_js` to inspect the live state.
5. Use `save_log_snapshot(project_path, page_id, ...)` when evidence should be stored as an artifact.
6. Patch the active source only after the runtime source has been verified.
7. Reload or drive the relevant UI action, then retest the interaction.
8. Use `dom_state_diff` or `click_and_checkpoint_loop` to prove the visible state changed as expected.

## Common Failure Triage

- Button state does not change after click: check CDP attach and active source before more edits.
- Backslashes or UNC paths are altered by fill: use `fill_value(encoding="base64")`.
- CSP `unsafe-eval` warning: treat it as a warning by default; do not block unless it changes app behavior.
- `CDP_UNREACHABLE` with no matching desktop window means runtime is absent; launch the app first.
- `CDP_UNREACHABLE` with a visible desktop window means UI is present but CDP is inaccessible; verify the port and launch flags.

## Token-Saving Log Reads

Use compact modes unless full forensic text is required:

```jsonc
extract_log_section({ "page_id": "...", "section_title": "System Error Log", "mode": "ref" })
extract_log_section({ "page_id": "...", "section_title": "System Error Log", "mode": "preview" })
read_all_logs({ "page_id": "...", "max_chars": 2000, "tail": true })
```

## Tool Selection Map

| Intent | Prefer |
|---|---|
| Read visible text | `read_page` |
| Inspect one element | `query_selector` |
| Enumerate inputs/buttons | `query_dom_candidates` |
| Read input/textarea value | `read_value` |
| Fill text, UNC, or multiline value | `fill_value(encoding="base64")` |
| Click and verify a state change | `click_and_checkpoint_loop` |
| Compare before/after DOM state | `dom_state_diff` |
| Read console errors | `read_console_errors` |
| Read failed network requests | `read_network_errors` |
| Read Rawcut log panels | `read_all_logs` |
| Save evidence to disk | `save_log_snapshot` or `screenshot_page(save_path=...)` |
| Launch Electron and attach CDP | `launch_electron_with_cdp` |
| Check a desktop window before Win32 action | `check_window_visibility` |
| Clean up a runtime test | `cleanup_test_session` |

## Script Execution

Use `run_script(page_id, script_path)` for multi-statement or escape-heavy JavaScript.
Wrap scripts in an async IIFE:

```js
(async () => {
  const el = document.querySelector("#main");
  return { text: el ? el.textContent : "" };
})()
```

Do not use top-level `return` in script files.
