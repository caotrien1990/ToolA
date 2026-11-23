---
version: "1.0.0"
last_verified: "2026-11-22"
verified_head: "dd41e2fc8f78761f8f7b9a104fb5cd98fdf1cad0"
name: skill_router
description: |
  Master decision tree for routing CA actions across [A] tools, native tools, and T4 runtime.
  Activate when: task type unclear, multiple candidate tools, need tradeoff analysis,
  or "should I use [A] write_fix or native Edit?". Returns ranked tool + skill recommendation.
trigger:
  - task_pattern: "which tool | should I use | best way to | choose between"
  - error_name: "DECISION_NEEDED"
  - tool: "tool_decision_hint"
preconditions:
  - "[A] health OK"
  - "Task description provided"
related_skills:
  - 05_write_pipeline
  - 12_native_audit
  - 15_search_code_intel
  - 18_runtime_dom_T4
risk_level: low
---

# 00 — Skill Router (Master Decision Tree)

## Session entry

Call get_rule_core_text with your agent_id once per process/session. Reuse that bootstrap epoch after compaction. For file or folder work, open_session with the absolute project root. When task ownership is required, follow the owner claim flow in skill 08. bootstrap_session needs session_id, agent_id and owner_claim_id; project_path alone is insufficient.

---

## Quick lookup — task type → tool tier

| Task | Tool | Skill |
|------|------|-------|
| Analyze repo / structural search | [T1] [A] | 15_search_code_intel |
| Old memory/context before search/scan/fetch/history | [Q] `/pre_search` via [Q:A] first, then normal [A]/council route | leader-council-skill |
| Council dispatch / worker routing / multi-worker brainstorm / implement council plan | Load `leader-council-skill` first, then use the loop/fix skill needed by the task | leader-council-skill + council-brainstorm-loop/project-orchestrator-fix-loop |
| Auto-chain hint in tool response (`ca_hint`/`ca_next_action`, the scoped `ca_hint_commit`/`ca_hint_rule`/`ca_hint_review`/`ca_hint_scope`, or the rarer `ca_chain_hint`/`ca_chain_next`) | Read whichever key the payload actually carries -- both vocabularies exist in code, so never rename an emitted field to match a doc. Present as suggestion, do NOT auto-execute `ca_next_action`/`ca_chain_next` without user go-ahead | 00_skill_router |
| Deprecated search aliases | search_code with regex, callers or definition_usage as appropriate; read_file_range for a known span | 15_search_code_intel |
| Multi-file write + audit/rollback | [T2] [A] | 05_write_pipeline |
| Small project edit | [T2] read_file_range, then write_fix | 05_write_pipeline |
| Auth/token/write pipeline (any size) | [T2] [A] | 05_write_pipeline + 06_token_auth |
| UI bug / runtime / DOM | [T4] | 18_runtime_dom_T4 |
| Server stuck / port / cache stale | [T4c] | 16_server_lifecycle |
| Session audit / handoff | [T3] | 17_session_audit |
| Search exact text multi-word | [T1] search_code (v2 smart) | 15_search_code_intel |
| Find symbol callers/usage | [T1] find_symbol (v2 include=usage) | 15_search_code_intel |
| Edit [A] backend itself | read_file_range → write_fix, then restart_orchestrator_via_bat (native only under Anh's break-glass) | 05_write_pipeline |
| Attach to an Electron app with remote debugging enabled | [T4] list_cdp_targets → cdp_attach_target (stateful MCP can launch with launch_electron_with_cdp) | 22_electron_automation |
| Button state / DOM state / color change over time | [T1] dom_state_diff (Recipe D) | 18_runtime_dom_T4 |
| Extract log / log area / textarea content from DOM | [T1] extract_log_section (Recipe E) | 18_runtime_dom_T4 |
| Fill UNC / fill backslash / fill unicode / multi-line | [T2] fill_value(encoding=base64) (Recipe C) | 18_runtime_dom_T4 |
| Read element.value (textarea/input) by selector | [T1] read_value(encoding=base64 optional) | 18_runtime_dom_T4 |
| Dump all log panels at once (System Error + Agent + AI + User) | [T1] read_all_logs | 18_runtime_dom_T4 |
| Run JS too long/escape-prone for inline evaluate_js | [T4b, stateful MCP only] run_script(script_path) | 18_runtime_dom_T4 |
| Enumerate DOM inputs/buttons/textareas/links | [T1] query_dom_candidates(filter) | 18_runtime_dom_T4 |
| Map full DOM section + view-* panel structure | [T1] describe_page | 18_runtime_dom_T4 |
| Release your own page | [T4] close_browser_page(page_id); attached pages detach | 18_runtime_dom_T4 |
| Inspect a CDP launch conflict | Inspect the owner and the port supplied by the launcher. Keep other apps running. | 22_electron_automation |
| Click + need state delta in 1 call | [T4, stateful MCP only] click_selector | 18_runtime_dom_T4 |
| Poll DOM state until change (server-side loop) | [T4, stateful MCP only] checkpoint_loop_dom_state | 18_runtime_dom_T4 |
| Read skill body by short name | [T1] read_skill(name) | 03_path_session |
| Compact log (hash+preview only) | [T1] extract_log_section(mode="ref"\|"preview") | 18_runtime_dom_T4 |
| List all tool names (3KB compact) | [T1] GET /api/tools/names | — |
| Click + poll state atomic (1 call) | [T4] click_and_checkpoint_loop | 18_runtime_dom_T4 |
| Multi-region UI state summary (text) | [T1] ui_state_summary | 18_runtime_dom_T4 |
| Project usage profile (R27 — after open_session, before T4 actions) | [T1] inspect_project_usage(project_path) | 03_path_session |
| R28 workflow rule | open_session → inspect_project_usage → discover handlers/IPC → T2/T4 act | 03_path_session |
| Save log as artifact ref (instead of full paste) | [T2] save_log_snapshot | 17_session_audit |
| Mission curation (skill/tool subset for task) | [T1] declare_mission | 17_session_audit |
| Auto-artifact oversize response | [T1/T2] read_all_logs(auto_artifact=true) | 18_runtime_dom_T4 |
| UNSUPPORTED_FIELD with alias remediable | check `accepted_aliases` + `ca_next_action=retry_with_alias_mapping` | 07_error_schema_hints |
| Legacy field name (R19 audit) | `_ARG_ALIASES` auto-normalize | 07_error_schema_hints |
| Pre-flight inspect [Ab] project (launcher type, ports) | [T1] inspect_project_runtime | 22_electron_automation |
| Tool matrix drift / sync layers | [T3] audit_tool_matrix + sync_tool_matrix.py --ci | 21_tool_matrix |

## Decision tree (formal)

```
task arrives →
  bootstrap with get_rule_core_text once per process/session; stop on refusal
  is_project_work?
    yes → [A]-first: classify task_class via CA_TOOL_POLICY_MATRIX
          (SOT: .council/CA_TOOL_POLICY_MATRIX.json, echoed live by
           open_session.cslt_policy; skill 21_tool_matrix explains the axes)
          native only if row.native_policy permits + audit
    no → outside project: allow_readonly native / env probe only
  is_read_only?
    yes → [T1] only (no trigger needed); project reads via [A] read_file_range/search_code
    no →
      has "tiến hành" trigger?
        no → ASK ❓
        yes →
          is_UI_runtime_concern?
            yes → [T4] (use 18_runtime_dom_T4)
            no →
              use [T2] read_file_range and the project write pipeline
              after 2 failed cycles + runtime unchanged →
                FORCE [T4] check (server/cache/port)
```

### Project A-first hard defaults (CSLt)

| Class | Primary | Native |
|---|---|---|
| Project read/search/path | [A] T1 | Bounded pure inspection per CORE_RULE; read-to-edit via read_file_range |
| Project write/create/batch/delete | `[A]` T2 | forbid |
| Session/backup/audit | `[A]` T3 | forbid (log native only for true exceptions) |
| CLI C-suffix worker | HTTP-only | no native project FS |
| OS/config outside project | native read-only | allow_readonly |
| Unknown op | `tool_decision_hint` / fail loud | no silent native |

When Anh steers “A plus native” / rejects micro-call spam: batch mechanical native under audit, keep governance calls on `[A]` — do not abandon matrix.

## Threshold Guide

| Condition | Tool | Reason |
|-----------|------|--------|
| ≤2 edits, file <500 lines | Native | t: ~10x faster |
| 3-5 edits same file | Equal | judgement call on risk |
| ≥6 edits OR ≥3 files atomic | [A] | C: rollback |
| Any auth/token/delete/migration | [A] | C: governance |
| Unicode/path-with-spaces complex | [A] | C: encoding safe |
| File >2000 lines + precise range | [A] | L: range_id reuse |
| Iteration cycle <30s with live User feedback | Native | t |
| Project not generated_project_map yet | Native | t: avoid setup cost |

## Negative tests (must STOP, not guess)

- Skill missing → STOP/ASK
- Tool error unclear → call `GET /api/tools?name_contains=<tool>` before fallback (R16.9: `get_tool_inspector` retired)
- Multiple skills match equally → ASK which scope
- Token boundary unclear → fetch_token, do not assume

## Workflow

1. Read task description.
2. Apply tree above → tier+skill.
3. If decision low-confidence (e.g., scope ambiguous) → end ❓ with 2-3 options.
4. Else proceed → open related skill folder for workflow details.
5. After task complete → log decision in session events for analytics.

## Related references

- `.ToolA_Skill/references/tool_surface_current.md` — exhaustive tiered tool surface reference for matrix audits
- `TOOL_REFERENCE.md` — representative tiered reference for user-facing documentation
## Council routing

Read leader-council-skill and the currently enabled council configuration before dispatch. Use the assigned role, risk level and cost limits for this task.

## ToolA pages

Resolve [A:API] from the active launcher or ORCHESTRATOR_BASE_URL. Main uses /, Council uses /council. Check these three integrated routes separately and record status plus generation. For /monitor compare the HTML title with orchestrator/web_ui/monitor.html. For /api/monitor/health require a JSON object with boolean up. For /matrixtool compare the title with orchestrator/web_ui/matrix.html. Record the working-file SHA when the served build contains uncommitted pages. A legacy Monitor origin is optional.
