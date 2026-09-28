---
name: server_lifecycle
description: |
  Use when [A] orchestrator or any project server is stuck, port-blocked, cache-stale,
  or "code changed but UI shows old version". Wraps T4c tools (server_lifecycle_status,
  port_owner_inspect, detect_stale_server, static_asset_cache_check, bundle_version_check,
  restart_server_via_bat_hint). Avoids "Run as admin" trap (V15 R7 fix).
trigger:
  - task_pattern: "port | stuck | won't restart | server status | stale | cache | admin | High IL | schtasks"
  - error_name: "PORT_IN_USE | STALE_SERVER_SUSPECTED | ADMIN_REQUIRED | CACHE_STALE"
  - tool: "server_lifecycle_status | port_owner_inspect | restart_server_via_bat_hint | bundle_version_check | cancel_pending_restart | restart_orchestrator_via_bat | restart_orchestrator_via_task"
preconditions:
  - "Health OK or KNOWN-DOWN (some tools work even when [A] itself is down)"
related_skills:
  - 11_repo_vs_runtime
  - 18_runtime_dom_T4
  - 12_native_audit
risk_level: medium
---

# 16 — Server Lifecycle

## ⚠️ V15 R28 — Stale cache decision table

`workflow_status` response in R28 includes `stale_decision` field with explicit T1/T2/T4 routing. Use it instead of guessing:

| `cache_status` | Task tier | Action |
|---|---|---|
| `stale` + `drift_warning` | **T1 read-only** | warning_only_proceed |
| `stale` + `drift_warning` | **T2 write/mutate** | **BLOCKER** — call `refresh_project_map` before any T2 |
| `stale` + `drift_warning` | **T4 runtime** | warning — only restart if probe-stale evidence (`detect_stale_server` returns drift) |
| `ok` | any | proceed |

Anti-pattern: ❌ Treat stale as always-blocker. Read-only T1 (search_code/find_symbol) on slightly-stale index is fine — search still returns correct hits; only write/mutate needs fresh INDEX.

## Tools (T4c, V15 R8 B.4)

| Tool | Purpose | EXECUTE trigger? |
|------|---------|------------------|
| `server_lifecycle_status(port)` | Full PID + cmdline + can_kill probe | No (read-only) |
| `port_owner_inspect(port)` | Lighter: just PID + process name | No |
| `detect_stale_server(port, expected_bundle_version?)` | Hash/etag diff vs last known | No |
| `static_asset_cache_check(url)` | HEAD + 2nd-fetch hash compare | No |
| `bundle_version_check(html_url, marker_regex?)` | Extract `<meta name=version>` or `?v=` | No |
| `restart_server_via_bat_hint(bat_path, port?)` | Restart command for a NON-[A] server's .bat ([A]'s own bat → `restart_orchestrator_via_bat`) | No (hint only, no execute) |
| `restart_orchestrator_via_task(delay_seconds=2)` | V15 R13.2: spawn detached helper → `schtasks /end` + `/run` → [A] relaunch at High IL, no UAC. Not a CA restart path: Anh-requested admin/UIPI recovery only. | Anh asks explicitly |
| `restart_orchestrator_via_bat` | The CA restart path for [A]: queue [A:S] hidden helper (`CREATE_NO_WINDOW`); call only `delay_seconds=2`; do **NOT** pass `expected_generation`; verify **new** `/health.generation` | Needs "tiến hành" / user go-ahead (T4c) |

## When to invoke each

| Symptom | Tool flow |
|---------|-----------|
| Port 8083 already in use | `server_lifecycle_status(8083)` → if same [A] running, OK; else kill old |
| New code, UI same | `bundle_version_check(http://localhost:3099)` then `static_asset_cache_check` |
| Stop-Process Access Denied | That measured the CA shell (Medium IL), not [A] (High IL). [A] itself → `restart_orchestrator_via_bat`; another PID → [A] `kill_process` only on Anh's ask. `server_lifecycle_status.can_kill` is a username estimate, not a permission probe |
| "Restart but health still old PID" | `detect_stale_server(8083, expected_bundle_version=<from log>)` |
| Browser shows old text after re-deploy | `static_asset_cache_check(<asset url>)` → Cache-Control + hash compare |
| schtasks LastResult=1 / NO_8083 | Diagnose High-IL `&&` short-circuit (below); do not thrash more restarts first |

## Admin / High IL pattern (V15 R13 — Task Scheduler bypass UAC)

### Goal
[A] needs to run at **High IL (Administrator)** so it can drive admin-context windows via WM_LBUTTONDOWN/UP (UIPI blocks Medium → High IL message routing). But "Right-click → Run as Administrator" prompts UAC every restart and breaks cross-elevation kills.

### Solution — Windows Task Scheduler `RunLevel=Highest`
1. **One-time setup (no High-IL [A] up yet → User, elevated PowerShell — NOT right-click the .ps1; a High-IL [A] that is up runs it via `run_ps1_script`, see Admin boundary):**
   - Win11 often **hides** “Run as administrator” on `.ps1` files. Open **PowerShell/Terminal as Administrator first**, then run the installer:
   ```powershell
   cd "D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator"
   Set-ExecutionPolicy -Scope Process Bypass -Force
   .\install_orchestrator_admin_task.ps1
   schtasks /Run /TN ToolA_Orchestrator
   ```
   - Alt GUI: Task Scheduler → `ToolA_Orchestrator` → Actions = `cmd.exe /c "...\start_orchestrator_high_il.cmd"` + **Run with highest privileges**.
   - PASS: LastResult=0, ArgsHasWrapper (not `&&` chain), `/health` ok.
2. **Every restart (no UAC):**
   - User: double-click `start_orchestrator.bat` → bat detects task → `schtasks /run /tn ToolA_Orchestrator` (no UAC).
   - CA: call tool `restart_orchestrator_via_bat` (hidden `CREATE_NO_WINDOW` helper, only `delay_seconds=2`). Do not open a visible cmd/powershell.
3. Result: [A] always launched at High IL, no UAC every time.

### Why cross-elevation kills fail (UIPI)
- User-context `Stop-Process -Id <pythonw_PID>` → ACCESS DENIED if target is High IL.
- Fix: `schtasks /end /tn ToolA_Orchestrator` — Task Scheduler runs as SYSTEM, can stop its own High IL children.

### Tool: `restart_orchestrator_via_task` (V15 R13.2)
- Not a CA restart path: kept for Anh-requested admin/UIPI recovery only.
- Spawn-detached helper pattern: tool returns `{ok:true, status:"restart_queued", poll_health_after_seconds:N}` IMMEDIATELY (~500ms), helper kills self+relaunches independently.
- Caller polls `/health` until ok=true → restart complete.
- Returns `INSTALL_TASK_REQUIRED` if `install_orchestrator_admin_task.ps1` hasn't been run.

### Tool: `restart_orchestrator_via_bat` (the CA restart path)
- Canonical T0 restart for [A]: POST `/api/call/restart_orchestrator_via_bat` with **only** `delay_seconds=2`.
- Do **NOT** pass `expected_generation` / `controller_url` / `request_id` (internal). Do **NOT** invent a kill pipeline.
- Helper is **hidden**: `CREATE_NO_WINDOW` on the cmd helper (never OR with `DETACHED_PROCESS`); `start_orchestrator.bat` powershell uses `-WindowStyle Hidden`. Do not steal Anh's mouse/keyboard.
- Returns `{status:"restart_queued", mechanism:"[A:S] start_orchestrator.bat (hidden helper)", helper_log, delay_before_restart_seconds}`.
- **Verify:** capture pre-restart PID + `generation` → poll `/health` every ~2s until `ok=true` **and** PID + `generation` changed.
- The only CA restart path for [A]: no ad-hoc kill, no raw `schtasks /Run`, no native bat launch.
- Before restart: check council broker / unreplied dispatch (in-flight gate). Empty broker → safe.

### Tool: `restart_server_via_bat_hint`
Restart-command hint for a NON-[A] project server's .bat; nothing is executed. [A]'s own bat answers `restart_orchestrator_via_bat`.

## High-IL task short-circuit pitfall (CSLt 2026-07)

### Symptom
- `schtasks /Run /TN ToolA_Orchestrator` → **LastResult=1**, port **8083 free**, [A] never comes up.
- Task action still contains `restart_orchestrator.py && start "" pythonw orchestrator_server.py`.

### Root cause
Windows `cmd` `&&`: if `restart_orchestrator.py` exits **non-zero**, **pythonw never starts**. Legacy hard_fail when port free / race makes this common.

### Code fixes (repo)
1. `restart_orchestrator.py` **soft-clear**: port free / nothing to kill → **exit 0** so chain can start.
2. Wrapper `start_orchestrator_high_il.cmd` always launches pythonw + logs `.orchestrator_cache/high_il_start.log`.
3. `install_orchestrator_admin_task.ps1` must point task action at the **wrapper**, not `restart && start`.

### Diagnose before "more restarts"
```
# Task action still fragile?
ArgsHasAndAnd=true  → needs admin rebind
ArgsHasWrapper=true  → good
LastResult=0 + high_il_start.log OK → healthy High-IL path
```
PowerShell: read `Get-ScheduledTask -TaskName ToolA_Orchestrator` Actions + `Get-ScheduledTaskInfo` LastTaskResult.

### Admin boundary (do not loop)
- `Access denied` from the CA shell measures THAT shell (always Medium IL), not [A]. A High-IL [A] that is up runs the rebind itself via `run_ps1_script`, after Anh's go-ahead (capability ≠ authority); backup + diff steps in the Reference file.
- Only when [A] is down, or `whoami /groups` through [A] answers Medium: report Anh once: **elevated PowerShell first**, then `.\install_orchestrator_admin_task.ps1` + `schtasks /Run /TN ToolA_Orchestrator` (do **not** tell Anh to right-click the `.ps1` — Win11 often has no admin entry there).
- Soft-clear alone may make old `&&` chain work **temporarily** (LastResult=0) — still rebind to wrapper for durability.

### Restart queued → [A] stays DOWN (STOP + report)
Symptom after `restart_orchestrator_via_bat`: the helper log says task started / health wait, but `/health` never answers inside the poll window (**NO_8083**), and no orchestrator python owns the port.
Cause: the relaunch half failed (fragile `&&` task action, LastResult≠0, import crash, or a stale lock).
CA action = read-only diagnosis, then STOP:
1. Poll `/health` THROUGH the refusal first: a refused port in the middle of a restart you asked for is expected. Conclude "down" only when it never comes back.
2. Collect the facts read-only (native inspection mutates nothing): who owns 8083 (`netstat -ano`), the task action + LastTaskResult (`schtasks /query /tn ToolA_Orchestrator /v`), the `orchestrator.log` tail, and whether `.orchestrator_cache/orchestrator.lock*` exists. Checklist: the Reference file below.
3. STOP and report those facts to Anh. With [A] down never launch `orchestrator_server.py` or `start_orchestrator.bat` yourself, never clear the lock, never edit [A] source: each is a native act on [A]'s own tree, which only Anh's break-glass authorizes (skill `12_native_audit`). A hand launch from a CA shell is also Medium IL: the .bat does not self-elevate.
4. Anh relaunches (`schtasks /Run /TN ToolA_Orchestrator`, after the admin rebind if the task action is the broken `&&` chain). Then probe `/health` once and prove it: NEW `pid` + CHANGED `generation`.

### Import crash blocks start (related)
If [A] dies immediately after restart: tail `orchestrator.log`. Classic: `registry_data.py` JSON-style `true`/`false` in Python → **NameError** at import → process never listens. Report the traceback to Anh and STOP: with [A] down the source fix is a native write into [A]'s tree, so it happens only under Anh's break-glass, and Anh relaunches.

### Reference
See `references/high_il_restart_pitfalls.md` for verify checklist + admin rebind recipe.

## 2-failed-cycle escalation (CORE_RULE)

When code edits did not change runtime after 2 attempts:
```
1. detect_stale_server(<app_port>, expected_bundle_version=<from server log>)
   → if stale=true → notify User, return STALE_SERVER_SUSPECTED
2. If !stale → static_asset_cache_check(<main asset url>)
   → if cache=stale → ask User Ctrl+F5 + recheck
3. If !cache_stale + !server_stale → genuinely a code issue, back to repo
```

## bundle_version_check details

- For HTML pages with build metadata: extract `<meta name="build-version" content="...">` or query `?v=2026-05-13` from CSS/JS link
- Compare against expected_bundle_version (CA tracks from last deploy)
- Returns `{version_found, version_expected, drift: bool}`

## static_asset_cache_check details

- HEAD request → returns Cache-Control + ETag + Last-Modified
- Second GET (same URL, ignore-cache) → hash compare with first
- If different headers but same body → false cache scare; report
- If same headers different body → server-side update behind cache

## restart_server_via_bat_hint output

```json
{
  "ok": true,
  "bat_path": "D:/proj/start_app.bat",
  "current": {"...": "port_owner_inspect(port) answer, or null without port"},
  "restart_command": "Run \"D:/proj/start_app.bat\"",
  "post_check": "Verify with a fresh API call (for [A]: GET /health shows a new pid AND a changed generation).",
  "note": "Hint only, nothing executed. [A] itself restarts only via restart_orchestrator_via_bat."
}
```

Given [A]'s own `start_orchestrator.bat`, `restart_command` is `call restart_orchestrator_via_bat(delay_seconds=2)`.

CA shows this to User; does NOT execute the kill itself. That boundary is AUTHORITY, not privilege: [A] runs at High IL and could, but a kill is Anh's call — on Anh's explicit ask, [A] `kill_process`.

## Anti-patterns

- ❌ Suggesting "Run as administrator" (V15 R7 obsolete)
- ❌ Restarting [A] by launching start_orchestrator.bat natively or via `restart_orchestrator_via_task` — the CA path is `restart_orchestrator_via_bat` only
- ❌ Opening a visible cmd/powershell when restarting [A] (`DETACHED_PROCESS`, `CREATE_NEW_CONSOLE`, powershell without `-WindowStyle Hidden`)
- ❌ Passing `expected_generation` from a CA call (internal only)
- ❌ Combining `DETACHED_PROCESS | CREATE_NO_WINDOW` (Win32 combo is invalid / undefined)
- ❌ Calling Stop-Process from the CA shell (Medium IL: denied on a High-IL target; and a kill is Anh's call → on Anh's ask, [A] `kill_process`)
- ❌ Polling `/health` rapidly during restart (1 probe per 2s; not 10/s)
- ❌ Inferring "server fixed" from health OK alone — also check bundle_version_check (app) **or generation change** ([A])
- ❌ Looping `Set-ScheduledTask` from the CA shell after Access denied (the CA shell is Medium IL) — a High-IL [A] runs it once via `run_ps1_script`
- ❌ Claiming High-IL durable while task still has `&&` chain (`ArgsHasAndAnd=true`)

## Guardrails

- T4c tools are READ-ONLY by default — never execute kills
- Any kill suggestion → User-actionable hint, not automation; restarting [A] itself is `restart_orchestrator_via_bat` after the go-ahead below
- Port 0-1024 always treated as admin-protected (warn User)
- Restart [A] needs user "tiến hành" / go-ahead; notify council; in-flight gate first

## Operational Impact

- 1 lifecycle check saves 5-10 wrong-direction code edits
- Cache check is fast (~200ms × 2) vs hours of CSS debugging
- bundle_version_check is 1 fetch vs full DevTools session
- Diagnosing `&&` short-circuit once avoids thrashing schtasks while [A] never listens

## Harness

- Test 1: kill [A] → `port_owner_inspect(8083)` → returns process_name=null, listening=false
- Test 2: start [A] → `server_lifecycle_status(8083)` → returns own PID + can_kill=true
- Test 3: simulate cache stale (Cache-Control: max-age=3600 on CSS) → 2nd fetch differs → flag
- Test 4: restart_server_via_bat_hint on a project .bat → `restart_command` runs that bat; on [A]'s own `start_orchestrator.bat` → it names `restart_orchestrator_via_bat`
- Test 5: restart_orchestrator_via_bat → pre gen G0 → post gen G1≠G0 + health ok
- Test 6: task ArgsHasAndAnd=true + restart non-zero → LastResult=1 expected until wrapper rebind
