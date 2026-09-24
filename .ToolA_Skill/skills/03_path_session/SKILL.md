---
name: path_session
description: |
  Use whenever CA touches a file/folder path or operates on a project. Activate on User
  mentioning any path, before any mutating tool (open_session required), and when
  deciding between project_path / file_path / folder_path / path semantics.
trigger:
  - task_pattern: "open file | edit | read | path is | folder | project at"
  - error_name: "PROJECT_PATH_REQUIRED | PROJECT_PATH_AMBIGUOUS | ACTIVE_SESSION_REQUIRED | PATH_NOT_FOUND"
  - tool: "open_session | resolve_project_path | inspect_project_usage | list_projects"
preconditions:
  - "Health OK"
related_skills:
  - 02_mode_control
  - 06_token_auth
  - 17_session_audit
risk_level: medium
---

# 03 — Path & Session

## Identifier system (V15)

| Token | Meaning | Where it applies |
|-------|---------|------------------|
| `[A]` | Orchestrator itself | (system) |
| `[A:P]` | `[A]` parent folder (auto-resolved) | `path_runtime.ORCHESTRATOR_DIR` |
| `[Ab]` | The Absolute Project CA works on | First non-[A] project User mentions |
| `[Ab:P]` | `[Ab]` absolute path | Pass as `project_path` |
| `[Ab:Rel]` | Path inside `[Ab]` | Pass as `file_path` (relative) |
| `[Ac]..[Az]` | Secondary registered projects | Auto-allocated by session DB |

## Path semantics (HARD)

| Field | Form | Example |
|-------|------|---------|
| `project_path` | **Absolute** root of project | `D:/proj/myapp` (forward slash OK on Windows) |
| `file_path` | **Relative** inside project_path | `src/utils.py` |
| `folder_path` | Absolute (only when schema requires it, e.g., `get_folder_size`) | `D:/proj/myapp/dist` |
| `path` (legacy) | Avoid; use `project_path` or `file_path` | — |

Never join `project_path` with absolute `file_path` (would double-prefix). If schema returns error about path, call `resolve_project_path(input_path)` first.

## Auto-register on User-mentioned path (HARD [PI:2b])

Whenever User mentions any file/folder path (even outside current project):
1. Identify project root by walking up looking for `.orchestrator_cache/`, `.git/`, `package.json`, or `requirements.txt`
2. Call `open_session(project_path=<root>)` BEFORE any read/write
3. The file becomes `file_path` relative to project_path
4. Session DB stores `[Ab]/[Ac]/[Ad]..` slot for it; `list_sessions` enumerates

## Open / close lifecycle

```
1. open_session(session_title, project_path) → returns {session_id, resumed: bool}
   - resumed=true means active session existed for this project_path; reuse it
2. <work>
3. (Optional) get_session_events(session_id) → audit trail
4. close_session(project_path)
   - V15 R8 C.1: middleware now resolves subdir → registered root automatically
```

## Common errors → action

| error_name | Fix |
|------------|-----|
| `PROJECT_PATH_REQUIRED` | Add `project_path` to body |
| `PROJECT_PATH_AMBIGUOUS` | Call `resolve_project_path` first, use returned canonical root |
| `ACTIVE_SESSION_REQUIRED` | Call `open_session(project_path)` first |
| `PATH_NOT_FOUND` | Check absolute vs relative; verify folder exists; for subdir of [Ab], pass `file_path` not `project_path` |

## Special handling

- **Unicode/Vietnamese paths**: pass as UTF-8 JSON strings; never base64-encode the path itself unless schema explicitly supports it
- **Path with spaces**: schema handles fine; for shell command in `run_project_command`, use `path_quote_helper` or `Set-Location` pattern (see skill 08)
- **Worker cwd-required BR projects**: if a worker/CLI requires a cwd before the final BR folder is known, create/use a temporary BR workspace under `[A]` or the active project, keep all artifacts bounded there, then move/close into the canonical `D:\BR` structure only at project close. Do not spend ClauC/CodoC on BR cleanup; route path verify/move/cleanup to OpeC or `[A]` write tools.
- **WSL/container CA**: use `[A:API]` (env `ORCHESTRATOR_BASE_URL`) instead of `localhost`; pass Windows paths as-is

## Anti-patterns

- ❌ Native Read/Write on User-mentioned external path without `open_session` first
- ❌ Passing relative `file_path` without `project_path` (server can't anchor)
- ❌ Manual `session_id` tracking (server keys by project_path; pass that)
- ❌ Joining `project_path + file_path` client-side (server does it)

## Guardrails

- After health OK and BEFORE first mutating call: `open_session` mandatory
- V15 R8 C.1: close_session middleware resolves path automatically
- Sessions auto-close if idle > N hours (server policy); resume creates new session_id

## Operational Impact

- Single open_session covers entire turn chain → 1 audit thread vs N
- Path resolution cached in session DB → subsequent calls skip lookup
- `resumed=true` saves session_id allocation

## Harness

- Test 1: User msg "edit D:/foo/bar/baz.py" → CA calls open_session(D:/foo/bar) before write
- Test 2: open with `D:/proj/sub` → close with `D:/proj` → V15 R8 C.1 middleware resolves, ok=true
- Test 3: pass file_path absolute → expect PATH_AMBIGUOUS or auto-correct
- Test 4: 5 sequential operations same project → all see resumed=true, 1 session_id
