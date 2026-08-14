---
name: electron_automation
description: |
  Use when task involves Electron-based app UI: launch, attach CDP, click,
  fill input, read DOM, extract in-page log section, monitor state change.
  Specifically tuned for Electron quirks: page-level WS rejection, hidden
  window coords, --remote-debugging-port flag injection, CSP unsafe-eval
  warnings. CA cấp thấp paste recipe là chạy được.
trigger:
  - task_pattern: "electron | rawcut | tr_rawcut | preview.bat | remote-debugging-port | cdp port 9223"
  - error_name: "ELECTRON_LAUNCHER_NOT_FOUND | CDP_UNREACHABLE_AFTER_LAUNCH | CDP_ATTACH_FAILED"
  - tool: "launch_electron_with_cdp | inspect_project_runtime | cdp_attach_target | ensure_single_electron_instance | inspect_project_usage | invoke_app_function | fill_folder_dialog | cancel_native_dialog | folder_stats | folder_diff | folder_exists"
preconditions:
  - "[A] health OK"
  - "Target Electron app installable + electron in package.json deps"
  - "Node.js on PATH (skill 01)"
related_skills:
  - 01_bootstrap_health
  - 08_command_windows
  - 18_runtime_dom_T4
  - 16_server_lifecycle
risk_level: medium
---

# 22 — Electron Automation (V15 R16)

## "Is this Electron?" detection

Use `inspect_project_runtime(project_path)`. Look at response fields:

| Field | Electron app signal |
|---|---|
| `launcher_type` | starts with `electron-` |
| `package_dependencies_highlight.electron` | version string present |
| Any `.bat`/`.cmd` containing `electron` or `npx electron` | yes |
| `electron_installed` (R16.2) | `true`=ready / `false`=need npm install first / `null`=not Electron |

If none of those match → it's not Electron; use skill 18 generic browser recipes instead.

## Pre-flight: Electron install check (R16.2)

`inspect_project_runtime` now probes `<resolved-cwd>/node_modules/electron`. Response:

```jsonc
{
  ...,
  "electron_installed": false,
  "install_command": "cd /d \"D:\\path\\to\\Source\" && npm install electron",
  "install_note": "Electron not in node_modules. launch_electron_with_cdp will auto-inject --yes to npx, but first-run will download ~150MB which may exceed background verify_after_ms. Pre-install once to avoid CDP_UNREACHABLE."
}
```

**Decision rule:**
- `electron_installed=true` → safe to call `launch_electron_with_cdp` immediately.
- `electron_installed=false` → either:
  1. Ask User to run `install_command` once (preferred, fast subsequent launches), or
  2. Call `launch_electron_with_cdp` with `verify_after_ms=180000` (3 min) to ride out npm install.
- `electron_installed=null` → not an Electron project.

## Launch overrides

Electron apps shipped without remote-debugging-port can be launched WITH the flag via `launch_electron_with_cdp`. It detects the launcher, injects `--remote-debugging-port=<port>`, runs background, fetches `/json/version`, calls `cdp_attach_target` — one tool call.

```jsonc
launch_electron_with_cdp({"project_path": "<root>", "cdp_port": 9223, "verify_after_ms": 6000})
// → {ok, page_id, cdp_port, attach_via, command_executed, launch_pid}
```

Common Electron-friendly args added when needed:
- `--no-sandbox` (some bundled Electrons fail without)
- `--disable-gpu` (rare; headless rendering)
- `--disable-features=UseChromeOSDirectVideoDecoder` (older builds)

Add these by overriding `launcher_path` to a customized command via `run_project_command` if `launch_electron_with_cdp` insufficient.

## Browser-level vs page-level WS — the #1 Electron pitfall

```
/json/list    → returns per-page webSocketDebuggerUrl  (PAGE-level)
/json/version → returns browser-level webSocketDebuggerUrl  (BROWSER-level)
```

| WS level | Playwright `connect_over_cdp` + `browser.new_context` | Raw CDP commands |
|---|---|---|
| Page-level | ❌ Electron rejects `Target.createBrowserContext: Not allowed` | ✅ works for DOM/Input on that page only |
| Browser-level | ✅ works | ✅ works |

**Rule:** Cho Playwright (qua `cdp_attach_target`), LUÔN dùng browser-level WS từ `/json/version`. V15 R16 auto-falls back nếu CA tình cờ pass page-level URL.

## Common Electron selector patterns

Real-world Electron renderers (Rawcut, Tr_MEDIA_BOT) thường có:

| UI element | Common selector |
|---|---|
| Step buttons | `#btnStep1`, `#btnStep2`, ... |
| Action buttons | `#btnGetFootage`, `#btnRender`, `#btnValidate` |
| Manual input textarea | `#manualInput`, `#sourceText`, `textarea[data-input]` |
| Log section title | `span.result-title`, `h2.section-title` |
| Log content body | `.log-area textarea`, `.log-body`, `pre.log` |
| Status indicator | `[data-status]`, `.status-badge`, `.workflow-state` |

Don't memorize selectors blind — call `evaluate_js` to enumerate:
```js
Array.from(document.querySelectorAll('button')).map(b => ({id: b.id, text: b.textContent.trim()}))
```

## Cleanup pattern

```jsonc
// After test/round done:
run_project_command({
  "project_path": "<root>",
  "command": "taskkill /F /IM electron.exe /T",
  "shell_mode": "auto"
})
```

Hoặc nếu launch tracked PID:
```jsonc
kill_process({"pid": <launch_pid from launch_electron_with_cdp>})
```

## CSP unsafe-eval handling

Electron với `contextIsolation: true` thường log warning `[CSP] Refused to evaluate a string ... unsafe-eval not allowed in Content Security Policy`. Đây là **warning, không phải error** — bỏ qua:

```jsonc
read_console_errors({"page_id": "<id>", "level": "error"})  // default; auto-ignores
```

Chỉ retry test khi `level=error` thực sự trả entries.

## End-to-end example (Rawcut test from V15 R16)

```jsonc
// 1. Pre-flight
inspect_project_runtime({"project_path": "<rawcut>"})
// → launcher_type=electron-bat, has_remote_debugging_in_launcher=false

// 2. Launch + auto-attach
launch_electron_with_cdp({"project_path": "<rawcut>"})
// → page_id="cdp-...", attach_via="browser_level_fallback"

// 3. Baseline button state
dom_state_diff({"page_id": "<id>", "selector": "#btnGetFootage"})
// → snapshot_id="dom-..."

// 4. Fill UNC path (base64 transport)
fill_value({"page_id":"<id>", "selector":"#manualInput",
             "value":"<b64>", "encoding":"base64"})

// 5. Click Step 1
click({"page_id":"<id>", "selector":"#btnStep1"})

// 6. Monitor state delta (loop 15s × 4)
dom_state_diff({"page_id":"<id>", "selector":"#btnGetFootage",
                 "baseline_id":"<snapshot_id>"})
// → {changed:true, changes:{bg:{...}, className:{...}}}

// 7. Extract log
extract_log_section({"page_id":"<id>", "section_title":"System Error Log"})
// → {ok, content:"<full log>", is_empty:false}

// 8. Cleanup
run_project_command({"project_path":"<rawcut>",
                      "command":"taskkill /F /IM electron.exe /T"})

// 9. final report + session wrap-up
```

## Anti-patterns

- ❌ Dùng `desktop_click(x, y)` cho Electron khi đã có CDP (CDP `click(selector)` chính xác hơn nhiều).
- ❌ Pass page-level WS tới `cdp_attach_target` rồi expect `new_context` work (Electron block).
- ❌ Treat CSP unsafe-eval warning là error → infinite retry.
- ❌ `fill()` với UNC path (mất `\\`); always Recipe C base64.
- ❌ Polling raw DOM bằng `evaluate_js` mỗi 1s; use `dom_state_diff` với 15s interval.

## Harness

- Test 1: `inspect_project_runtime` trên electron-bat project → `launcher_type=electron-bat` + `package_dependencies_highlight.electron` set.
- Test 2: `launch_electron_with_cdp` trên Rawcut → response trong <10s, `attach_via="browser_level_fallback"`, `page_id` resolvable.
- Test 3: `fill_value(encoding="base64")` với UNC string → `length_written` matches expected UTF-8 byte length.
- Test 4: `dom_state_diff` 2-call → baseline_id + diff với `changed:true` khi className thực sự thay đổi.
- Test 5: `extract_log_section("System Error Log")` → `content_length > 0` + `is_empty:false`.
