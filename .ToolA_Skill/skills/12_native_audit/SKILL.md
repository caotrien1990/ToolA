---
name: native_audit
description: |
  Use whenever a native tool (Edit, Read, Write, Bash, Grep, Glob) is invoked on repo/project
  files after health OK. MANDATORY for each native operation. Ensures full audit trail via
  log_native_event so [A] governance is preserved even when bypass is unavoidable.
trigger:
  - task_pattern: "native fallback | native Edit | edit [A] backend | API blocked"
  - tool: "log_native_event | create_pending_audit | sync_pending_audit"
preconditions:
  - "Active session exists"
  - "Either: User explicit authorization OR [A] API failed after 1 corrected retry OR Anh's break-glass while [A] itself is broken"
related_skills:
  - 07_error_schema_hints
  - 05_write_pipeline
risk_level: medium
---

# 12 — Native Audit

## When native is allowed (HARD)

ONLY in these cases:
1. **[A] itself broken** — Anh opens break-glass (never self-authorized). A working [A] edits itself through `read_file_range` → `write_fix` like any project
2. **[A] API down** mid-task → `create_pending_audit` + sync when API returns
3. **Environment permission block** preventing API call (rare)
4. **Single-file <30 lines** low-risk iteration with active User feedback loop (User must explicitly accept this scope)
5. **Read-only OS config** outside any project (`%APPDATA%\Claude\*.json`)

Never allowed:
- Multi-file batch without [A] API
- Auth/token/delete operations
- Project files, [A]'s own tree included, when [A] is available — must use API

> Self-edit is NOT an exception. A native write to [A]'s own tree is still
> `task_class=upgrade_orchestrator`, so the ledger names it, but its
> `native_policy` is `forbid` like any project write, and preflight and
> `log_native_event` both answer `HOST_GATE_DENY`. [A] edits itself through
> `read_file_range` → `write_fix`, and `restart_orchestrator_via_bat` loads the new
> code. The only native path into [A] is case 1: Anh's break-glass while [A] itself
> is broken, never self-authorized.

## Mandatory audit (every native use)

Live schema fields only (GET `/api/tools/log_native_event/schema` if unsure).  
**Required:** `action_type`, `tool_name`.  
**Accepted extras:** `target`, `duration_ms`, `status`, `reason`, `instead_of`, `failed_payload`, `failed_error`, `native_command`, `scope`, `risk`, `transport` (`native`|`via_a`), `project_path`.

**Reject / never send** (cause `UNSUPPORTED_FIELD`): `action`, `agent_id`, `files`, `summary`, `details`, `target_path`.

```
log_native_event({
  action_type: "read" | "write" | "execute" | "validate" | "cache" | "backup" | "workflow",
  tool_name: "native_Edit" | "native_batch_edit" | "native_Read" | "native_Bash" | etc.,
  project_path: <[Ab:P] or [A:P]>,
  target: "<relative path(s) or command — put multi-file list HERE, not in details>",
  status: "ok" | "error" | "warn",
  instead_of: "<what API tool would have been called>",
  failed_error: "<API error that pushed to native, if any>",
  reason: "<one sentence why native chosen, <300 chars>",
  native_command: "<optional command/action label>",
  scope: "<optional scope note>",
  risk: "<optional risk note>",
  transport: "native"   // or "via_a" when work went through [A] but still logged
})
```

## Pending audit (API down or off-project host paths)

Live `create_pending_audit` — do **not** send free-form `details` objects. Stick to documented fields:

```
1. create_pending_audit({
     project_path, action_type, tool_name,
     instead_of?, failed_error?, reason?
   })
2. Continue native work
3. When API back → sync_pending_audit(project_path)
```

Multi-file path lists belong in `reason`/`target` (truncated), not unsupported nested blobs.

## Workflow per native call

```
1. Pre-check: is this case 1-5 above?  
   no → STOP, return to [A] API path
2. Perform native action
3. IMMEDIATELY log_native_event (do not batch)
4. If API down → create_pending_audit
5. Report includes native fallback_count
```

## Common scenarios

### Editing [A] itself
```
- Default: read_file_range → write_fix / write_new_file; restart_orchestrator_via_bat loads the new code
- write_fix itself broken (circular), or [A] still hung after restart → STOP, ask Anh for break-glass
- Under break-glass only: native Edit; the host gate appends each write to its off-log
- After: restart [A], verify new pid + changed generation; Anh closes break-glass
```

### Quick CSS tweak rapid iteration
```
- Scope: <30 lines, 1 file
- User watching live → fast feedback loop
- Use: native Edit
- Log: instead_of="write_fix", reason="rapid UI iteration with live User feedback"
```

### Backup project before destructive
```
- Use: [A] backup_project (NEVER native)
- Native backup forbidden — defeats audit purpose
```

## Verification after native batch

End of session: `get_session_events(session_id)` / `session_audit_summary` → check `native_fallback_count`. If unexpectedly high (>5 per task) → investigate WHY [A] tools were insufficient, file bug.

## Metrics classification (policy matrix)

Every native action on a governed project must be classifiable against `.council/CA_TOOL_POLICY_MATRIX.json`:

| Counter | When |
|---|---|
| `forbidden_native_count` | native while row `native_policy=forbid` → **bypass event** (a native self-edit of [A] counts here too) |
| `allowed_native_count` | native while policy allows **and** audit present |
| `via_a_count` | same task class executed via `/api/call/<tool>` |
| `unclassified_count` | action not mapped to any `task_class` |
| `bypass_rate` | `forbidden_native / all_classified_governed_actions` (denom 0 → `null`) |

`log_native_event` is evidence for `allowed_native_count` only when the policy row actually permits native. Logging does **not** convert a forbidden bypass into allowed.

Hybrid steering (“A plus native”): still log each native call with accurate `instead_of` + `reason`; metrics must remain honest.

## Axis 4 hard reject (ledger enforce — live)

When Matrix row is `native_policy=forbid` (write/search on any project via native, [A]'s own tree included), **ledger must reject**, not only warn.
A native write inside [A]'s own source tree is classified `upgrade_orchestrator`
and rejected the same way — see "Self-edit is NOT an exception" above:

| Signal | Meaning |
|--------|---------|
| `ok: false` | Expected for forbid path |
| `error_name: HOST_GATE_DENY` (or FORBIDDEN_NATIVE) | Gate denied native |
| `type: POLICY_REJECT` | Structured reject, not transport error |
| `audit_status: logged_and_rejected` | Event recorded **and** rejected |
| `policy_warning` + `preferred_via_a` | Prefer [A] tools listed |

**Do not** treat hard reject as “log_native_event broken”. That is the acceptance proof for Axis 4.

### Reporting pitfall (Anh: “đã fix ổn?”)

| Situation | Correct report |
|---|---|
| Forbid-native → `HOST_GATE_DENY` + `logged_and_rejected` | **PASS** (gate works) |
| Forbid-native → `ok=true` with no reject | **FAIL** (ledger soft / not enforcing) |
| Outside-project OS read → allow / `allow_readonly` | **PASS** |
| Project native Read/Edit while [A] up → accepted without reject | **FAIL** (bypass) |

Intentional negative controls in the same metrics window raise `forbidden_native_count` — label them so Anh does not read a “bypass spike”.

### Smoke order (post-restart or post-policy change)
1. `classify_ca_action` — expect `is_forbidden_native=true`, `native_policy=forbid`, host_gate deny inside project.
2. `log_native_event` with `transport=native` + project write target — expect reject fields above.
3. `ca_policy_metrics` — `forbidden_native_count` increments; `bypass_rate = forbidden / total_governed`; north_star acronym **CSLt**.
4. `hermes_host_gate_check` — OS-mutate deny; outside-project readonly allow.

Acceptance pack: `.council/CA_A_USAGE_ACCEPTANCE_TEST.md`.  
Governance smoke (classify/ledger/gate/metrics/registry) is enough for axis wave report; full 7-CA black-box still human-scored on sandbox.

### Hybrid multi-file wording / policy-string batch (CSLt align, etc.)

When Anh says stop pure-[A] micro-calls and use **A plus native** for mechanical multi-file string fixes:

1. **Default project mutate** still prefers `[A]` `write_fix` / `write_new_file` / `write_transaction`. There is **no** [A] tool named `write_file` or `write_range` (common skill drift — always discover via `/api/tools/names`).
2. **Hermes native** `write_file` / `patch` / raw python on **project** paths = `transport=native` + Matrix forbid for write rows → preflight/host_gate deny + ledger hard-reject. Only use native multi-file when Anh explicitly authorizes HYBRID / A+native AND case 1–4 above still holds.
3. If authorized native batch: **One** `log_native_event` with `tool_name=native_batch_edit`, `target` listing paths, `transport=native`. Expect `HOST_GATE_DENY`/`logged_and_rejected` when row forbids — that is PASS for Axis 4, not a broken logger.
4. **`create_pending_audit`** (or separate log) for paths **outside** the open project (Hermes host `SOUL.md`/`USER.md`/skills, handoff packs under `D:/BR/...`). Host paths are not project `write_fix`.
5. Keep matrix/read-back/session/metrics on `[A]` — do not skip governance to go faster.
6. Verify with read-back of the canonical strings (e.g. CSLt full north star) — do not claim fixed from write alone.
7. **`SYNTAX_ERROR_PRE_WRITE` / unexpected indent** on `write_fix` mid-function → retry with `content_encoding=base64` (indent strip pitfall). Prefer temp `.py` HTTP caller over git-bash heredoc. Do **not** treat as license to go native while [A] is up.

### Hermes write path ban (project)

| Surface | Allowed project write |
|---|---|
| [A] | `write_fix`, `write_new_file`, `write_fix_html`, `write_transaction` |
| Hermes native | **forbid** while [A] up (`write_file`/`patch`/raw FS) |
| Host-only (SOUL/skills under `%LOCALAPPDATA%\hermes`) | native OK + audit/pending as outside-project |

**Preflight (gen 4d1fe2362b6a+):** `hermes_host_gate_check` returns `preflight_hard_stop`, `error_name` (`HOST_GATE_DENY`|`FORBIDDEN_NATIVE`), `preferred_via_a`, `severity=error`, `ca_next_action=stop_native_use_a_tool` for native project writes. Transport enum **`native`|`via_a` only** (not `"A"`). Metrics field is **`time_window_hours`**.

**Honesty:** T1 preflight is **advisory for host Herm tools** (does not kill `write_file` syscall). Ledger hard-reject on `log_native_event` is after-the-fact. CA MUST still STOP when `preflight_hard_stop=true` and use [A] `write_fix`/`write_new_file`/`write_transaction` from `preferred_via_a`.

Before native project write: call `hermes_host_gate_check` / `classify_ca_action` with `transport=native`. If `allowed=false` / `is_forbidden_native=true` / `preflight_hard_stop=true` → STOP and use [A] tool from `preferred_via_a`.

## Token-saving

- Do NOT log raw file content in `reason` field — sanitize to <300 chars
- Do NOT include token in any audit field
- Reason should reference the [A] tool that would have been used, not describe the change

## Harness

- Test 1: open session, native Edit 1 file, log_native_event, close session → events table shows 1 native
- Test 2: API down (stop [A]) → native Edit → create_pending_audit → restart [A] → sync_pending_audit
- Test 3: try native on [Ab:P] file when API is up → must STOP (negative test)
- Test 4: forbid-row native write → log_native_event returns HOST_GATE_DENY / POLICY_REJECT / logged_and_rejected
