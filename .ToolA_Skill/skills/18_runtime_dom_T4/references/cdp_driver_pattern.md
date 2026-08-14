# CDP Driver Pattern

Use this reference for Electron or Chromium automation through the Chrome DevTools Protocol.

## Preconditions

- The app must be launched with a remote debugging port, usually `--remote-debugging-port=9223`.
- The debug port must answer `http://127.0.0.1:9223/json`.
- A page target must exist. If `title_pattern` is provided, the title must match one page target.
- Shell wrapper exit code is not proof of GUI readiness.

## Launch Verification

When launch is performed through `run_project_command`, pass at least one verification field:

```json
{
  "project_path": "D:/path/to/app",
  "command": "cmd /c \"\"D:\\path with spaces\\start_app.bat\"\"",
  "shell_mode": "cmd",
  "expected_port": 9223,
  "expected_process_name": "electron",
  "verify_after_launch_ms": 1000
}
```

Expected evidence:

- `spawned_pid`
- `launch_verified`
- `port_probe`
- `process_probe`
- `root_causes`

If the debug port is not reachable, the tool should return `error_name=CDP_UNREACHABLE` and `launch_verified=false`.

## Desktop Evidence

CDP focus success should include:

- `method=cdp`
- `cdp_port`
- `target_id`
- `bring_to_front_sent`
- `dispatch_results`

CDP click success should include:

- `events_dispatched`
- `dispatch_results`

Win32 fallback success should include:

- `resolved_hwnd`
- `child_hwnd`
- `resolved_from`
- `attach_thread_input`
- `sequence`

If both CDP and Win32 fail, return `DESKTOP_INTERACTION_FAILED` with `root_causes` and `fallback_attempted`.
