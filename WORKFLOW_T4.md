# WORKFLOW_T4 - Runtime / DOM / Browser / Server Lifecycle

T4 owns runtime inspection, browser/DOM evidence, server lifecycle checks, desktop/Electron probes, and UI harnesses. T4 inspect is read-first; runtime mutation requires an EXECUTE trigger.

## Step T4.0 - Runtime Target Gate

Goal: identify the exact URL, port, window, or process to inspect.

Allowed tools: `server_lifecycle_status`, `port_owner_inspect`, `desktop_window_list`, `list_cdp_targets`, `tool_decision_hint`.

Hint source: selected T4 inspector.

Schema source: selected T4 tool schema.

Skill route: `18_runtime_dom_T4` or `16_server_lifecycle`.

Guardrail: no server restart, browser click, desktop focus, or JS evaluation without explicit trigger semantics.

Validation: target URL/port/window id is known and current.

## Step T4.1 - Browser Snapshot

Goal: collect a read-only page snapshot before any interaction.

Allowed tools: `open_browser_page`, `read_page`, `query_selector`, `read_console_errors`, `read_network_errors`, `screenshot_page`.

Hint source: selected browser tool inspector.

Schema source: selected browser tool schema.

Skill route: `18_runtime_dom_T4`.

Guardrail: initial browser action is inspect-only unless the user asked for interaction.

Validation: DOM text, console/network status, or screenshot artifact is available.

## Step T4.2 - Runtime Interaction

Goal: perform the smallest user-triggered runtime interaction and observe its effect.

Allowed tools: `click`, `fill`, `evaluate_js`, `wait_for_selector`, `desktop_app_focus`, `desktop_click`.

Hint source: selected interaction tool inspector.

Schema source: selected interaction tool schema.

Skill route: `18_runtime_dom_T4`.

Guardrail: runtime-mutating actions need EXECUTE; use selectors/coordinates from the latest snapshot. For Electron/Chromium, run `list_cdp_targets` first and set `fallback_to_win32=false` when isolating CDP-only behavior.

Validation: before/after state changed as expected or the failure is captured.

## Step T4.3 - Server Lifecycle

Goal: diagnose stale or broken runtime host state.

Allowed tools: `server_lifecycle_status`, `detect_stale_server`, `static_asset_cache_check`, `bundle_version_check`, `restart_server_via_bat_hint`, `process_tree_inspect`, `kill_process`.

Hint source: selected lifecycle inspector.

Schema source: selected lifecycle schema.

Skill route: `16_server_lifecycle`.

Guardrail: `restart_server_via_bat_hint` returns guidance only, for a non-[A] server; [A] itself restarts only via `restart_orchestrator_via_bat` (needs "tiến hành"). `kill_process` is protected T2 cleanup even when used by a T4 lifecycle workflow.

Validation: health response, process owner, and bundle/cache state are reported.

## Step T4.4 - Runtime Harness

Goal: certify that a UI/backend surface behaves as the matrix claims.

Allowed tools: `run_playwright_test`, `record_ui_flow`, `assert_text_visible`, `assert_button_click_changes_state`, `assert_console_no_error`, `compare_dom_snapshot`.

Hint source: selected harness inspector.

Schema source: selected harness schema.

Skill route: `18_runtime_dom_T4`.

Guardrail: static matrix checks cannot replace runtime harnesses for browser/server behavior.

Validation: harness result includes pass/fail evidence and exact runtime target.
