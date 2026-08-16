---
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

## ⚡ V15 R42 — FIRST-CALL EVERY SESSION: `bootstrap_session(project_path)`

CA pain report R42: 17% calls failed UNSUPPORTED_FIELD vì không biết schema. Fix: **mỗi session đầu tiên gọi `bootstrap_session(project_path)`** trước bất cứ work khác. Trả 1-call compact:

- `orchestrator_health` (port, version)
- `active_workflow` (User-activated workflow if any)
- `key_tools_compact` (~35 tool with required fields + alias hints + notes)
- `common_pitfalls` (lessons R29-R41 distilled)
- `active_skills_index` (skill names)
- `see_also_macros` (audit_tool_matrix, acceptance_dashboard, per-tool inspector endpoint URLs)

Saves ~5-10 schema queries per session. Read once → biết tool nào accept project_path, field aliases, token requirements, workflow state.

Sau bootstrap, đọc skill chuyên biệt nếu task vào sâu lĩnh vực cụ thể.

---

## Quick lookup — task type → tool tier

| Task | Tool | Skill |
|------|------|-------|
| Analyze repo / structural search | [T1] [A] | 15_search_code_intel |
| Old memory/context before search/scan/fetch/history | [Q] `/pre_search` via [Q:A] first, then normal [A]/council route | leader-council-skill |
| Council dispatch / worker routing / multi-worker brainstorm / implement council plan | Load `leader-council-skill` first, then use the loop/fix skill needed by the task | leader-council-skill + council-brainstorm-loop/project-orchestrator-fix-loop |
| Multi-file write + audit/rollback | [T2] [A] | 05_write_pipeline |
| Single-file <30 lines low-risk | Native Edit + log_native_event | 12_native_audit |
| Auth/token/write pipeline (any size) | [T2] [A] | 05_write_pipeline + 06_token_auth |
| UI bug / runtime / DOM | [T4] | 18_runtime_dom_T4 |
| Server stuck / port / cache stale | [T4c] | 16_server_lifecycle |
| Session audit / handoff | [T3] | 17_session_audit |
| Search exact text multi-word | [T1] search_code (v2 smart) | 15_search_code_intel |
| Find symbol callers/usage | [T1] find_symbol (v2 include=usage) | 15_search_code_intel |
| Edit [A] backend itself | [A] HYBRID + log_native_event | 12_native_audit |
| Launch Electron / electron port / remote-debugging | [T4] launch_electron_with_cdp | 22_electron_automation |
| Button state / DOM state / color change over time | [T1] dom_state_diff (Recipe D) | 18_runtime_dom_T4 |
| Extract log / log area / textarea content from DOM | [T1] extract_log_section (Recipe E) | 18_runtime_dom_T4 |
| Fill UNC / fill backslash / fill unicode / multi-line | [T2] fill_value(encoding=base64) (Recipe C) | 18_runtime_dom_T4 |
| Read element.value (textarea/input) by selector | [T1] read_value(encoding=base64 optional) | 18_runtime_dom_T4 |
| Dump all log panels at once (System Error + Agent + AI + User) | [T1] read_all_logs | 18_runtime_dom_T4 |
| Run JS too long/escape-prone for inline evaluate_js | [T4b] run_script(script_path) | 18_runtime_dom_T4 |
| Enumerate DOM inputs/buttons/textareas/links | [T1] query_dom_candidates(filter) | 18_runtime_dom_T4 |
| Map full DOM section + view-* panel structure | [T1] describe_page | 18_runtime_dom_T4 |
| Cleanup test (close CDP page + taskkill electron + close session) | [T2] cleanup_test_session | 18_runtime_dom_T4 |
| Pre-launch hygiene (kill stale electron + free CDP port) | [T4] ensure_single_electron_instance | 22_electron_automation |
| Click + need state delta in 1 call | [T4] click_selector | 18_runtime_dom_T4 |
| Poll DOM state until change (server-side loop) | [T4] checkpoint_loop_dom_state | 18_runtime_dom_T4 |
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
  bootstrap [A] once (health OK; stop on connection-refused)
  is_project_work?
    yes → [A]-first: classify task_class via CA_TOOL_POLICY_MATRIX
          (SOT: .council/CA_TOOL_POLICY_MATRIX.json; skill 21_tool_matrix
           references/ca-tool-policy-matrix.md)
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
              is_multi_file or audit_required?
                yes → [T2] [A] write_pipeline
                no →
                  scope ≤ 30 lines AND low_risk AND not in {auth, token, write_pipeline}?
                    yes → Native Edit + log_native_event (skill 12) — still counts for metrics
                    no → [T2] [A] write_pipeline
              after 2 failed cycles + runtime unchanged →
                FORCE [T4] check (server/cache/port)
```

### Project A-first hard defaults (CSLt)

| Class | Primary | Native |
|---|---|---|
| Project read/search/path | `[A]` T1 | forbid |
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
## Worker Cost Routing (R20260619)

Before any council dispatch, choose the cheapest tier that can safely complete the task:

| Task class | Default route | Do not use |
|---|---|---|
| Local scan, grep, path verify, backup/remove/move, D:/BR cleanup | OpeC / FREE | ClauC/CodoC unless Anh explicitly requests |
| Internet research, docs/community fetch, broad source gathering | HermC / FREE | ClauC/CodoC |
| Synthesis, implementation proposal, rule shrink, gatekeeper design, 2-round brainstorm | ComC / MEDIUM routed model | Free workers as final authority |
| T4/stateful MCP/browser runtime, high-stakes final judgement, user-requested expensive review | ClauC or CodoC / EXPENSIVE | Routine cleanup/scan |

Hard gates:
- ClauC is not a cleanup worker.
- CodoC is not a default helper.
- ComC2/HermC2 removed-route notes are obsolete. Default research/scan/report split is `HermC1=OpeC4`; OpeC2 is scan+reason with short response only; OpeC1 is legacy scan fallback; OpeC3 is narrow factual scan only.
- Expensive dispatch prompts must state why OpeC/HermC/ComC/[A] write tools are insufficient.
- For memory/context-pool writes, prefer `[A] write` to a bounded artifact, then let librarian ingest/distill if needed.

Lookup artifact: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\17. Council_Tr\context_pool_specs\MODEL_COST_ROUTING_SPEC_v1_1.md`.
