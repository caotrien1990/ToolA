---
name: tool_matrix
description: |
  Use after adding/removing/renaming an [A] tool, after large skill/workflow edits,
  or when User asks "check sync / matrix / drift / docs aligned". V15 R13 built-in
  `audit_tool_matrix` cross-references TOOL_MAP / TOOL_SCHEMAS / TOOL_PHASES against
  skill SKILL.md trigger.tool, SKILLS.md router rows, harness *.md test cases,
  and `.ToolA_Skill/references/tool_surface_current.md`. Auto-runs on open_session /
  prepare_ca_session (silent unless drift). Reports drift to .orchestrator_cache/
  matrix_log.jsonl and via /api/matrix_log endpoint.
trigger:
  - task_pattern: "matrix | audit drift | sync docs | tool inventory | check sync | đồng bộ tool | đồng bộ skill | rà tài liệu | kiểm tra sync"
  - tool: "audit_tool_matrix | architecture_audit_compact | progressive_matrix_check | acceptance_dashboard | mcp_surface_diff"
  - lifecycle: "after_add_tool | after_remove_tool | after_skill_md_edit | after_router_update | session_open"
preconditions:
  - "[A] orchestrator running"
  - "Project path resolvable"
related_skills:
  - 00_skill_router
  - 16_server_lifecycle
  - 17_session_audit
risk_level: low
---

# 21 — Tool Matrix Audit (V15 R13)

## When to invoke

**Always after:**
- Adding a new tool to `TOOL_MAP` / `TOOL_SCHEMAS` in code_tools.py or tools_v15.py.
- Removing or renaming an existing tool.
- Editing a SKILL.md trigger.tool field.
- Rewriting a harness `*.md` / `*.ps1` test case that references tool names.
- Updating `.ToolA_Skill/references/tool_surface_current.md` or `SKILLS.md` router.
- User says "đồng bộ", "audit drift", "check sync", "kiểm tra tài liệu", "matrix".

**Auto-fires on:**
- `open_session` (silent; only `matrix_drift_present:true` in response when drift).
- `prepare_ca_session` (same: silent summary, hint when drift).

## How to call

```jsonc
// CA-side: any time, no token required.
POST /api/call/audit_tool_matrix
{
  "project_path": "<absolute>",
  "include_coverage": false   // true = heavier response with per-tool flags
}
```

Response shape:

```jsonc
{
  "ok": true,
  "generated_at": "2026-05-16T...",
  "summary": {
    "code_tools": N, "schema_tools": N, "skill_tools": M,
    "skill_folders": K, "reference_tools": L,
    "drift_count": D, "ok_count": (N-D)
  },
  "drift": [
    { "type": "missing_schema",  "tool": "X", "severity": "high",   "detail": "..." },
    { "type": "ghost_schema",    "tool": "X", "severity": "high",   "detail": "..." },
    { "type": "skill_ref_missing_tool", "tool": "X", "skill_refs": ["NN_skill"], "severity": "medium" },
    { "type": "inventory_outdated",     "tool": "X", "severity": "low" },
    { "type": "obsolete_payload_in_harness", "file": "...", "line": 12, "pattern": "mode=base64 (REMOVED V15 R12)", "severity": "medium" }
  ],
  "matrix_log_path": ".orchestrator_cache/matrix_log.jsonl",
  "ca_next_action": "review_drift_and_fix" | "no_action_needed"
}
```

## Drift type → action

| Type | Severity | Fix |
|------|----------|-----|
| `missing_schema` | high | Add `TOOL_SCHEMAS` entry (use `_v15_schema(name, description, props, required)`). |
| `ghost_schema` | high | Remove schema OR add corresponding `TOOL_MAP` entry. |
| `skill_ref_missing_tool` | medium | Either fix skill SKILL.md trigger.tool typo, or register the tool. |
| `inventory_outdated` | low | Append tool to the appropriate tier section in `.ToolA_Skill/references/tool_surface_current.md`. |
| `obsolete_payload_in_harness` | medium | Update harness `.md`/`.ps1` to current contract (e.g. `mode=base64` → `query_encoding=base64`). |

## Pre-call optimizations

1. After **one** code edit, run audit once at the end of the batch — not after every line.
2. Use `include_coverage=false` (default) for normal drift report. Only ask `true` when investigating a specific tool.
3. Read `.orchestrator_cache/matrix_log.jsonl` (last record) instead of re-running if you already audited in this round.

## UI integration

- `GET /api/matrix_log?limit=20` → JSON with `history[]` + `live` (fresh audit). UI Matrix Log panel renders this.
- `prepare_ca_session` response carries `matrix_audit` summary + `matrix_drift_present` flag + `matrix_hint`. CA should surface in User report when present.

## Operational Impact

- 1 audit call (~50-200 ms) replaces 3-5 grep/read calls to manually verify sync → C+, S-, t-.
- After adding a new tool, **always** audit before responding to User to avoid shipping silent drift.

## Anti-patterns

- ❌ Manually grepping skill files instead of calling audit_tool_matrix.
- ❌ Calling audit_tool_matrix every single tool call (excessive); once per round end is enough.
- ❌ Ignoring `matrix_drift_present:true` in open_session response → silent shipping of drift.
- ❌ Treating `inventory_outdated` (low) as urgent — defer to batch update at end of round.

## Workflow (CA template)

```
1. Edit code (add/remove tool, change schema)
2. Restart [A] (restart_orchestrator_via_bat, delay_seconds=2; needs "tiến hành")
3. Call audit_tool_matrix(project_path=<root>)
4. Review drift; fix high-severity first
5. Re-run audit until drift_count <= acceptable threshold (typically inventory-only)
6. Report to User: "audit_tool_matrix → drift_count=N, fixed M, deferred K (inventory)"
```

## 4Ktr Coverage References

The matrix/4Ktr audit also covers these auxiliary and lifecycle tools:
`backup_status`, `capability_status`, `cep_deploy`, `cep_extension_status`,
`cep_manifest_validate`, `check_server_path`, `classify_get_footage_outcome`,
`copy_file`, `create_benchmark_request`, `create_build_context`,
`create_folder`, `create_folders`, `desktop_window_state`,
`fetch_sheet_csv_summary`, `get_rule_core_text`, `get_validation_queue`,
`log_native`, `manage_agent`, `parse_log_section`, `process_tree_inspect`,
`protected_tools_status`, `replace_between_anchors`, `run_node_script`,
`run_project_test_script`, `run_ps1_script`,
`sample_unc_paths_with_root_check`, `set_workflow_mode`,
`suggest_missing_capabilities`, `verify_get_footage_copy_completion`,
`webfetch`, `workflow_activate`, `workflow_active_get`,
`workflow_checkpoint`, `workflow_deactivate`, `workflow_draft_create`,
`workflow_draft_get`, `workflow_goto_step`, `workflow_read_md`,
`workflow_save_md`, `workflow_status_registry`.

## Related references / scripts

- **CA execution-policy matrix** — SOT `.council/CA_TOOL_POLICY_MATRIX.json`, echoed live by `open_session.cslt_policy`. There is deliberately no markdown copy: a second copy of a policy is a second policy. What it governs, vs the tool-surface audit: **CSLt** north star (C→S→L→t, never CLSt/CSLT); residual-scan checklist + **data-identity** residual (`why_cslt`, registry `governance.acronym`, plan `write_range`); source-vs-runtime verify gate; hybrid A+native; axes order Matrix→Registration→Metrics→Ledger→Host gate; sticky `http_only` desktop pitfall; progress-report contract after “tiến hành”.
- `scripts/cslt_governance_smoke.py` — post-restart CSLt governance smoke (health, gov tools, classify, host gate, ledger forbid, metrics, backup_status, registry).
- Cross-ref: hard vs soft residual data scan + registry identity contract — no document holds these. The binding that enforces them is `tests/test_a31_soft_data_binding.py` (committed file vs runtime) plus the drift types `_audit_tool_matrix_sync` emits. The skill `council-agent-operation` this line used to point at does not exist under `.ToolA_Skill/skills/`.

## Operations matrix (V15 R15)

Three audit modes via `scripts/sync_tool_matrix.py` covering the full lifecycle.
Each mode has a distinct trigger, action-on-fail, payload size, and runtime.

| Mode | Flag | Trigger | Action on fail (drift > 0) | Payload | Runtime |
|------|------|---------|---------------------------|---------|---------|
| **Gate** | `--ci` | Pre-commit hook + GitHub Actions on push/PR | Exit 1 → block commit / fail PR check | ~500 B (stderr summary) | 1-3 s |
| **Monitor** | `--summary-only` | Hourly Task Scheduler / cron | Exit 0 (never block); log alert if drift_count > 0 | ~2-5 KB (core counts) | 1-2 s |
| **Audit** | `--check --output md\|json` | Manual / weekly / pre-release | Exit 0 (informational); CA reviews + fixes manually | ~150-300 KB (full coverage + per-tool fix) | 5-15 s |

### 4 mandatory `--check` scenarios

CA / developer MUST run `python scripts/sync_tool_matrix.py --check --output md` in these cases:

1. **After adding a new T4 or T2 mutating tool** — Coverage map confirms TOOL_MAP + schema + inventory all reference the new tool.
2. **After refactoring tool architecture** — Rename, merge, split, or tier reassignment. Detects ghost_schema or skill_ref_missing_tool drifts.
3. **Weekly / pre-release audit** — Catches accumulated low-severity drift (typically `inventory_outdated`).
4. **Debugging suspected drift root cause** — When CA workflow misbehaves and audit_tool_matrix alone is insufficient, the `--check` mode adds `suggested_fix` per item.

### Pre-commit installation

```cmd
:: run from the checkout root -- `project-orchestrator` IS the repo root, so a
:: `cd project-orchestrator` here fails and the install silently never happens
scripts\install_precommit.bat     :: one-time, copies hook into .git/hooks/pre-commit
```

After install, every `git commit` runs `sync_tool_matrix.py --ci` automatically. Bypass with `git commit --no-verify` only for emergency hotfixes (and follow up with audit immediately).

### CI integration (cloud)

`.github/workflows/tool-matrix-ci.yml` runs on push/PR to `main|master|develop`. Failed runs upload a `tool-matrix-audit` artifact (Markdown report). Block merge until drift_count = 0.

### Continuous monitoring (Windows Task Scheduler example)

```cmd
schtasks /create /tn "ToolA_MatrixMonitor" /tr ^
  "cmd /c cd /d D:\path\to\project-orchestrator && python scripts\sync_tool_matrix.py --summary-only >> .orchestrator_cache\matrix_monitor.log 2>&1" ^
  /sc hourly /mo 1
```

Tail `matrix_monitor.log` for `drift_count > 0` lines. Trigger external alert (User integration; out of scope for V15 R15).

## V15 R16 — T4 Electron test pre-flight checklist

Trước khi chạy bất kỳ UI test nào trên Electron app, theo thứ tự:

```
[ ] 1. inspect_project_runtime(project_path)
       → Verify launcher_type starts with "electron-"
       → Note has_remote_debugging_in_launcher (informs Recipe A vs B)
[ ] 2. launch_electron_with_cdp(project_path) hoặc Recipe B manual
       → Verify response.attach_via + page_id present
[ ] 3. check_window_visibility(title_pattern="<app name>")
       → if suggestion ≠ "ok" → desktop_app_focus first
[ ] 4. dom_state_diff(page_id, "<critical button selector>") to capture BASELINE
       → Save snapshot_id for later diff
[ ] 5. read_console_errors(page_id, level="error") to verify no startup errors
       → severity_summary.warning_count expected to be >0 (CSP warnings); OK to ignore
```

Sau mỗi action mutation:
```
[ ] dom_state_diff(... baseline_id=...) to confirm UI state changed
[ ] read_console_errors(level="error") to catch new errors
```

End of test:
```
[ ] extract_log_section(... "System Error Log") for final audit
[ ] cleanup via taskkill /F /IM electron.exe /T
[ ] final reporting flow removed from matrix
```

## Harness

- Test 1: After adding new dummy tool to TOOL_MAP only (no schema) → audit reports `missing_schema` drift.
- Test 2: After adding tool to both code + schema + skill SKILL.md trigger → audit reports drift_count unchanged.
- Test 3: Harness file with `mode="base64"` literal → audit reports `obsolete_payload_in_harness`.
- Test 4: `open_session` response includes `matrix_audit` summary + `matrix_drift_present` boolean.
- Test 5: `GET /api/matrix_log?limit=3` → returns history + live audit.
