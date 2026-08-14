---
name: error_schema_hints
description: |
  Use to interpret structured errors from [A] API and decide next step. Activate on any
  non-2xx response or error_name field. Maps error_name → corrected_payload pattern, retry
  policy, and escalation to fallback. Saves tokens by avoiding verbose ca_guidance re-reads.
trigger:
  - error_name: "SCHEMA_VIOLATION | UNSUPPORTED_FIELD | MISSING_REQUIRED_FIELD | INVALID_BASE64 | INVALID_CONTENT_ENCODING | STALE_INDEX | MUTATION_TOKEN_REQUIRED | ACTIVE_SESSION_REQUIRED | PROJECT_PATH_AMBIGUOUS | FILE_CHANGED_OUTSIDE_ORCHESTRATOR"
  - tool: "alias_contract_test | /api/tools | /api/tools/names"
preconditions:
  - "Error response received from /api/call/*"
related_skills:
  - 05_write_pipeline
  - 06_token_auth
  - 03_path_session
risk_level: low
---

# 07 — Error / Schema / Hint handling

## V15 R40 — `see_also` + `suggested_alternative_tool` fields

Mọi response error giờ kèm 2 field tự động:

**`suggested_alternative_tool`**: `{tool: "...", reason: "..."}` — tool thay thế cụ thể cho cặp (tool_failed, error_name). Map biết:
- `(write_fix, FILE_CHANGED_OUTSIDE_ORCHESTRATOR)` → `write_transaction`
- `(fill, JSON_ENCODING_ERROR)` → `fill_value(encoding='base64')`
- `(fill_folder_dialog, DIALOG_NOT_FOUND)` → `folder_dialog_snapshot`
- `(click_and_handle_folder_dialog, NEW_DIALOG_NOT_FOUND)` → `invoke_app_function`
- `(desktop_click, CDP_DISPATCH_FAILED)` → `desktop_click(force_win32=True)`
- `(run_playwright_test, MODULE_NOT_FOUND)` → `run_project_command npm install playwright`
- `(workflow_submit_step, STEP_MISMATCH)` → `workflow_get_current`
- `(workflow_start, WORKFLOW_NOT_FOUND)` → `workflow_list`
- `(read_file_range, FILE_NOT_FOUND)` → `query_index`

**`ca_guidance.see_also`**: bản map đầy đủ tool-related (không phụ thuộc error). Vd `write_fix.see_also = {write_transaction: "Multi-hunk same file", search_code: "Find anchor first", read_file_range: "Read before write"}`.

CA flow khi nhận error:
1. Check `suggested_alternative_tool` first — nếu có, switch sang tool đó.
2. Nếu không có, đọc `ca_guidance.see_also` để biết tool nào tương tự có thể thử.
3. Cuối cùng đọc `notes` + `corrected_payload` như cũ.



## ⚡ V15 R22 — Per-tool verbosity toggle

**8 tool nhận `verbosity ∈ {full|terse|ref}`** (default `full`, back-compat):

| Tool | Saving terse | What gets stripped |
|---|---|---|
| `open_session` | ~400/call | path_resolution_note + progressive_gaps_hint + resolved_from + matrix_audit compressed to 3 counts |
| `audit_tool_matrix` | ~1500/call | drift/alias_drift capped at 3 entries + coverage + inventory_classification dropped |
| `get_skill_state` | ~2800/call | per-entry path/path_absolute/size_bytes/read_file_range_payload* across 23 skills |
| `read_skill` | ~300/call | path_absolute + total_lines + shown_lines + max_lines + truncated metadata |
| `declare_mission` | ~800/call | irrelevant_skills_skip + recommended_sequence + caveats + score_floor_explanation |
| `tool_decision_hint` | ~550/call | rationale + tools_validation + payload_example_per_tool schemas (kept: keys only) |
| Error responses (`_err`) | ~350/error | ca_guidance.notes + known_gotchas + recommended_prompt_rule |

**Tổng**: ~5-7K tokens/session khi CA opt-in `terse` từ round 2.

### Drift safety net (auto-force `full`)

- `open_session`: nếu `matrix_drift_present=True` OR `progressive_gap_count>0` → bỏ qua `verbosity=terse/ref`, return `full` + `_drift_override` field.
- `audit_tool_matrix`: nếu `drift_count>0` OR `progressive_gap_count>0` → cùng cơ chế.
- Các tool khác (skill_state, read_skill, mission, decision_hint): respect verbosity as-is. CA chọn = CA chịu.

### Usage pattern

```jsonc
// Round 1: full để CA học content
open_session({project_path, session_title: "..."})
get_skill_state({})
declare_mission({mission: "..."})

// Round 2+: terse
open_session({project_path, verbosity: "terse"})  // matrix_audit chỉ 3 counts
get_skill_state({verbosity: "terse"})              // chỉ tên 23 skills
read_skill({name: "18", verbosity: "terse"})       // chỉ result + skill_name
tool_decision_hint({task: "...", verbosity: "terse"})  // chỉ recommended_*
audit_tool_matrix({project_path, verbosity: "terse"})  // drift capped at 3
```

### `_err` verbosity

`_err(msg, error_name, verbosity="full"|"terse")` — caller forward CA's verbosity choice.
Default `full` (errors should be visible). `terse` chỉ khi CA confident về error pattern.

## 🔄 V15 R19 — Alias contract registry

Trên `UNSUPPORTED_FIELD`, response giờ luôn có `accepted_aliases` + `ca_next_action`:

- **`ca_next_action="retry_with_alias_mapping"`** = field CA gửi có alias đã biết → dùng `corrected_payload` (đã map sang canonical) + retry. KHÔNG escalate.
- **`ca_next_action="remove_unsupported_field"`** = field CA gửi không có alias map → dùng `corrected_payload` (stripped or registry fallback) + retry.

### Alias contract (R19 audit Round 8)

| Tool | Legacy field | → Canonical | Status |
|---|---|---|---|
| `read_skill` | `skill_name`, `skill` | `name` | alias auto-normalized |
| `read_skill` | `max_chars` | `max_lines` | alias auto-normalized |
| `optional_tool_probe` | `tool_names`, `names` | `tools` | alias |
| `run_script` | `expression`, `code`, `inline` | `script_inline` | alias |
| `click_and_checkpoint_loop` | `click_selector` | `click_selector_str` | alias |
| `click_and_checkpoint_loop` | `observe_selector`, `watch` | `watch_selector` | alias |
| `click_selector` | `verify_state_after` | `before_after_state` | alias |
| `checkpoint_loop_dom_state` | `snapshot_fields` | `fields` | alias |
| `desktop_window_list` | `title_contains`, `pattern` | `title_filter` | alias |
| `tool_decision_hint` | `task` | `task_description` | alias |
| `save_log_snapshot` | `log_selector`, `title` | `selector`, `section_title` | alias |

### Harmless-ignore alias / legacy field (silently dropped — no 400)

| Tool | Field | Reason |
|---|---|---|
| `tool_decision_hint` | `project_path` | tool không cần project context |
| `read_skill` | `project_path` | resolves orchestrator dir internally |
| `optional_tool_probe` | `project_path` | doesn't touch projects |
| `desktop_screenshot` | `project_path`, `label` | label → derived save_path |
| `desktop_window_list` | `project_path` | system-wide enumeration |
| `list_projects` | `project_path` | iterates all projects |
| `get_token_meter` | `project_path` | server-scoped |

### Retired / deprecated tool names (KHÔNG dùng nữa — sẽ trả UNSUPPORTED_TOOL/UNKNOWN_TOOL)

- `tool_inspector` → use `GET /api/tools?name_contains=<tool>` (R16.9)
- `tool_inventory` → use `GET /api/tools` or `GET /api/tools/names`
- `dom_screenshot_summary` → use `ui_state_summary` (R16.10, text-only)


## Trust contract

Every [A] error includes:
- `error_name` — machine-readable
- `ca_next_action` — single action verb
- `retry_sequence` — ordered steps
- `corrected_payload` — pre-built fix (when applicable)
- `accepted_fields` — schema reference
- `payload_example` — minimal correct payload

**Use these BEFORE reading `ca_guidance.notes`** (notes are verbose; skip unless retry fails).

## Common errors → action

| error_name | Action | Retry? |
|------------|--------|--------|
| SCHEMA_VIOLATION | Use `corrected_payload`, send X-Orchestrator-Token if needed | once |
| UNSUPPORTED_FIELD | Remove fields in `unsupported_fields[]`, keep `accepted_fields[]` | once |
| MISSING_REQUIRED_FIELD | Add fields in `missing_fields[]` from `payload_example` | once |
| MUTATION_TOKEN_REQUIRED | GET /api/token → resend with `X-Orchestrator-Token` header | once |
| ACTIVE_SESSION_REQUIRED | `open_session` (auto in retry_sequence) | once |
| PROJECT_PATH_AMBIGUOUS | `resolve_project_path` first | once |
| INVALID_BASE64 | Re-encode UTF-8 → base64, retry (V15 R12: applies to `query`/`name`/`content` when `*_encoding="base64"`) | once |
| INVALID_CONTENT_ENCODING | Set `*_encoding` to `plain` or `base64` (no other values) | once |
| STALE_INDEX / FILE_CHANGED_OUTSIDE_ORCHESTRATOR | `refresh_project_map` OR `generate_project_map(backup_override=true)` | once |
| PATH_NOT_FOUND | Verify absolute path; for relative inside project → use `file_path` not `project_path` | once |
| BINARY_FILE_UNSUPPORTED | STOP, report — no retry | no |

## Retry policy (HARD)

- **One corrected retry max** per error code per call.
- After 2nd failure → escalate per `suggested_fallback`:
  - `FETCH_TOKEN` → token skill
  - `OPEN_SESSION` → session skill
  - `READ_LIVE_SCHEMA` → `GET /api/tools?name_contains=<tool>` (retired: `get_tool_inspector`)
  - `STOP_AND_REPORT` → terminate, report to User
  - (no field) → native fallback ONLY if scope is single-file low-risk + log_native_event

## Hint quality triage

When `hint` is vague or `ca_next_action` is empty:
1. Call `GET /api/tools?name_contains=<tool_name>` → live schema with `known_gotchas` (retired: `get_tool_inspector`)
2. Check `recommended_prompt_rule` (if present)
3. If still unclear → end ❓ with error JSON pasted for User

## Anti-patterns

- ❌ Native fallback BEFORE corrected retry
- ❌ Retry with same payload (ignoring corrected_payload)
- ❌ Sending raw token in retry without `X-Orchestrator-Token` header
- ❌ Looping retry on persistent error without escalation
- ❌ Reading full `ca_guidance.notes` when shorter fields suffice

## Efficiency Notes

- Skip `ca_guidance.notes` array unless first retry failed
- Use `corrected_payload` directly (one short JSON) instead of rebuilding from `accepted_fields`
- Stale `drift` block in find_symbol/search_code responses → ignore if just ran refresh

## Workflow

```
1. Parse error JSON
2. Check error_name → look up action above
3. Apply corrected_payload OR retry_sequence[0]
4. Retry once
5. If fail → suggested_fallback
6. If still fail → native fallback with log_native_event OR end ❓
```
