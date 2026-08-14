---
name: command_windows
description: |
  Use when CA needs to run shell commands via run_project_command, especially for paths
  with spaces, Unicode, or complex quoting. Encodes Windows-first rules (cmd /c,
  PowerShell -File only, no WMIC) and the path_quote_helper escape hatch.
trigger:
  - tool: "run_project_command"
  - task_pattern: "run command | execute | bash | powershell | shell | cmd"
  - error_name: "COMMAND_QUOTING_FAILED | PATH_HAS_SPACES"
preconditions:
  - "Health OK"
  - "Active session for protected tool"
related_skills:
  - 03_path_session
  - 09_testing_validation
  - 12_native_audit
  - 16_server_lifecycle
risk_level: medium
---

# 08 — Command (Windows-first)

## V15 R12 — `shell_mode` & `command_encoding`

`run_project_command` now accepts two transport fields that REMOVE most manual quoting work:

| Field | Values | Behavior |
|-------|--------|----------|
| `shell_mode` | `auto` (default) | Windows → `cmd`; non-Windows → `exec` |
|  | `cmd` | Wraps as `cmd /c "<raw>"`. Auto-escapes `%`→`%%`, plus `^ & | ( ) < >` outside double-quoted regions |
|  | `powershell` | `powershell -NoProfile -Command "<raw>"`. If `command` ends in `.ps1` → `-File <path>` route |
|  | `exec` | `shlex.split(posix=not _is_win)` — legacy argv mode |
|  | `posix` | `shlex.split(posix=True)` — explicit Unix mode (don't use on Windows) |
| `command_encoding` | `plain` (default) / `base64` | Decode via central `_decode_text_payload` before parsing; use when command has Unicode, embedded quotes, or newlines |

Response now echoes `shell_mode_requested` + `shell_mode_resolved` so CA can verify routing.

**Default rule:** Just pass `command="dir"` or `command="npm test"` — `auto` resolves to `cmd` on Windows, handles `%` and special chars for you. Reach for `.ps1 + -File` only when you genuinely need PowerShell semantics.

## V15 R16 — `node -e` 2-mode distinction

`node -e` xuất hiện ở **2 ngữ cảnh hoàn toàn khác nhau**. Phân biệt rõ để tránh hiểu nhầm "vi phạm API boundary":

| Mode | Khi nào | Đó là gì | Audit |
|------|---------|----------|-------|
| **A. PRE-HEALTH bootstrap probe** | Trước health-check đầu session | Native exemption duy nhất pre-health (CORE_RULE §BOOTSTRAP) | KHÔNG cần audit |
| **B. POST-HEALTH `[A]` API call** | Sau health OK, từ CA shell | `node -e` chạy `http.request('http://localhost:8083/api/call/<tool>')` — đây vẫn là **[A] API usage**, không phải native fallback | KHÔNG cần audit |
| **C. Native subprocess / fs / spawn** | Bất cứ khi nào tránh API | Native fallback, vi phạm SAFETY rule | **Required**: `log_native_event` |

**Hardline**: Mode A & B chỉ KHÁC nhau ở thời điểm. Cả hai đều dùng `node -e` cú pháp giống nhau. Mode C khác bản chất — gọi `fs`, `child_process.spawn`, `os`, etc.

**Khi NÊN dùng `node -e` inline thay vì `.ps1`:**
- Simple HTTP POST tới `/api/call/<tool>` (<200 chars, chỉ `http.request`)
- Quick probe (health, version, status)
- Test loop với multiple sequential calls (counter, retry)

**Khi NÊN dùng `.ps1`:**
- Path-with-spaces (Set-Location pattern)
- PowerShell cmdlet (`Get-CimInstance`, `Get-NetTCPConnection`)
- Multi-step orchestration với `if`/`try`/`catch`
- Cần `Invoke-RestMethod` cho UI-friendly progress

## Hard rules

| Rule | Reason |
|------|--------|
| **Simple HTTP POST to [A] API (<200 chars) → `node -e` inline OK** | Vẫn là [A] API usage qua HTTP; không phải native fallback |
| Use `cmd /c "<command>"` for system commands | Native command line, predictable quoting |
| **PowerShell only via `cmd /c powershell -NoProfile -File <script.ps1>`** | Inline PowerShell quoting is broken on Windows |
| **No WMIC** ever | Deprecated; use `Get-CimInstance` in a `.ps1` file |
| No `curl/wget/bash/cat/grep/sed/awk` | Use `[A]` API tools (read_file_range, search_code, etc.) |
| `Invoke-RestMethod` for HTTP transport only | In `.ps1` wrappers calling `[A:API]` |

## Path-with-spaces patterns

### Pattern 1: `Set-Location` inside `.ps1`
When command must run inside a project subfolder:
```powershell
Set-Location "D:\proj with spaces\sub folder"
npm install
```
Save as `<orchestrator_cache>/cmd_XXX.ps1`, then `run_project_command` with `command="cmd /c powershell -NoProfile -File <path>"`.

### Pattern 2: `path_quote_helper`
For one-off commands, call `path_quote_helper(project_path, subfolder, command, shell="powershell")` to get a properly-escaped wrapper.

### Pattern 3: Avoid `cwd=` kwarg
`run_project_command` may not accept `cwd` (schema-dependent). Use Set-Location inside .ps1 wrapper instead. If rebuilding payload required, drop `cwd` and inject Set-Location.

## Success criteria (HARD)

`run_project_command` returns OK only if:
- `exit_code == 0`
- `timed_out == False`
- `stderr` does NOT contain platform-specific error markers

Never infer success from `stdout` text alone. Always check `exit_code`.

## Common errors → action

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `exit_code=1` + stderr about quotes | PowerShell inline | Wrap in .ps1 file |
| `exit_code=2` "is not recognized" | Path with spaces unescaped | Use Set-Location or path_quote_helper |
| `timed_out=True` | Long-running build | Increase `timeout`; consider background pattern |
| "WMIC is deprecated" | Used WMIC | Switch to Get-CimInstance |
| Encoding garbage in output | UTF-8 not set | Add `chcp 65001 >NUL` at top of .ps1 |

## Helper: temp `.ps1` wrapper template

```powershell
# saved at: <orchestrator_cache>/cmd_<uuid>.ps1
$ErrorActionPreference = 'Stop'
chcp 65001 > $null
Set-Location "<absolute path with spaces>"

# ... actual command(s) here ...

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Call from CA via:
```
run_project_command(project_path=<root>, command="cmd /c powershell -NoProfile -File <abs ps1 path>")
```

## Subfolder + Unicode

Pass `project_path` UTF-8 as JSON string (server handles). Inside command, refer to paths via PowerShell variables (set absolute literal in `.ps1`). Never inline Unicode in single-line cmd.

## Anti-patterns

- ❌ Direct shell with PowerShell single line + multiple inner quotes
- ❌ Using `start /B` for background — instead use `run_in_background` API flag
- ❌ Using `cd <path>` then `<cmd>` in one line — quoting fails
- ❌ Inferring success from "OK" in stdout when exit_code=1
- ❌ Restarting [A] by opening a visible cmd/powershell. Use `restart_orchestrator_via_bat` with only `delay_seconds=2` (hidden `CREATE_NO_WINDOW` helper) — skill `16_server_lifecycle`

## Guardrails

- Native shell forbidden when [A] tool covers same need (e.g., `find` → use `find_symbol`/`search_code`)
- `.ps1` wrapper temp files cleaned up post-call (CA should `delete_file` after use)
- `run_project_command` is protected (token required)

## Operational Impact

- One `.ps1` wrapper avoids 2-3 quoting-retry round-trips
- `path_quote_helper` saves designing wrapper from scratch
- Set-Location pattern reusable across runs (skill reuse)

## Harness

- Test 1: `run_project_command` with path-spaces dir → use Set-Location wrapper → exit_code 0
- Test 2: PowerShell inline quoting → returns COMMAND_QUOTING_FAILED hint → CA switches to .ps1 → success
- Test 3: WMIC attempted → path_quote_helper rewrites to Get-CimInstance .ps1
- Test 4: Background long-running build → run_in_background=true → CA polls via task ID
- Test 5 (R12): `run_project_command(command="echo 100% done", shell_mode="auto")` on Windows → exit 0, stdout contains `100% done` (auto-escape worked)
- Test 6 (R12): `run_project_command(command=<base64 of "echo xin chào">, command_encoding="base64")` → exit 0, UTF-8 stdout preserved
- Test 7 (R12): `run_project_command(command="script.ps1", shell_mode="auto")` → resolved_mode="powershell", uses `-File` route
