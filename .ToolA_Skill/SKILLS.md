# SKILLS — Router (Tầng 2)

Routing table. Lookup only; do not read full skill bodies here.

> **Progressive Disclosure**: open ONLY the skill matching current task. If unsure, see `00_skill_router/SKILL.md`.

## Quick route table

| Task / Error / Tool | Skill folder | Confidence |
|---------------------|--------------|------------|
| Health fail / bootstrap | `01_bootstrap_health/` | high |
| EXECUTE trigger, mode gate | `02_mode_control/` | high |
| File path / session / project_path | `03_path_session/` | high |
| Tool tier question, schema lookup | `04_tool_tiers/` | high |
| `write_fix`, `write_new_file`, `ruff_fix`, base64, dry_run | `05_write_pipeline/` | high |
| `MUTATION_TOKEN_REQUIRED`, `/api/token` | `06_token_auth/` | high |
| `SCHEMA_VIOLATION`, `UNSUPPORTED_FIELD` | `07_error_schema_hints/` | high |
| `run_project_command`, path-with-spaces | `08_command_windows/` | high |
| `run_test_profile`, `validate_file` | `09_testing_validation/` | high |
| Coverage report, 47-tool checklist | `10_coverage_report/` | medium |
| UI bug / DOM / browser visible | `11_repo_vs_runtime/` | high |
| Native fallback usage | `12_native_audit/` | high |
| `apply_after_dry_run`, `STALE_TARGET` | `13_apply_after_dry_run/` | medium |
| Token/JWT redesign | `14_token_jwt_redesign/` | medium |
| Code search (text, symbol, semantic) | `15_search_code_intel/` | high (V15 new) |
| Governed Git status/stale/diff/log/stage/unstage/commit | `git-governance/` | high |
| Server lifecycle / port / cache stale | `16_server_lifecycle/` | high (V15 new) |
| Tool selection ([A] vs native vs T4) | `00_skill_router/` | high (V15 new) |
| Matrix audit / drift / sync docs after tool change / "đồng bộ" | `21_tool_matrix/` | high (V15 R13) |
| Trigger `cập nhật skill rule` / update rule+skill for all CA / sync_rule sync_skill / adapter skill refs / related [A] Python files | `rule_skill_changer/` | high |
| Electron app launch / CDP attach / DOM state delta / log extraction | `22_electron_automation/` | high (V15 R16) |
| Restart [A] at High IL / Task Scheduler / UIPI / admin-context desktop_click | `16_server_lifecycle/` | high (V15 R13) |
| Multi-agent handoff [CA1]→[CA2] | `17_session_audit/` | medium (V15 new) |
| Runtime DOM via T4 (Electron + browser) | `18_runtime_dom_T4/` | high (V15 R1 LIVE) |
| Image-mode output (token saving) | `19_image_mode/` | medium (V15 new) |
| Folder picker (`fill_folder_dialog`, `folder_dialog_snapshot`, `cancel_native_dialog`) | `18_runtime_dom_T4/` | high (R32+) |
| Workflow dispenser (`workflow_list`, `workflow_start`, `workflow_submit_step`) | `26_workflow_ingestion/` | high (R35+) |
| `bootstrap_session` first-call macro (compact overview) | `00_skill_router/` | high (R42) |
| MD workflow ingestion / parse `## Known domain rules` | `26_workflow_ingestion/` | high (R43 new) |
| Manual memory/context-pool write, stale correction artifact, librarian ingest note | `27_context_pool_write/` | high (R20260619) |
| Leader mode anti-drift / first-dispatch Q worker routing / council coordination skill | `leader-council-skill/` | high (R20260619) |
| Broker worker task needs write outside cwd / bootstrap [A] for worker artifact writes | `broker-worker-bootstrap/` | high |
| Council / `bat hoi dong Tr` / `hoi dong` legacy compatibility, `council_message` broker one-shot, `council_worker_model` PiC model routing | `leader-council-skill/` | high (D4 2026-08-11: 24_council_tr retired) |
| Council cron schedule CRUD/trigger/history, pinned worker+model and narrator+model | `council-cron-jobs/` | high |
| Console/merge/audit/verify text artifacts, base+plug report update, zero-audit document loop (`council_console_loop` alias) | `council-console-loop/` | high |

> **Numbering gap (R43.3 + D4 2026-08-11)**: slots `23/`, `24/` and `25/` are retired/unpopulated. `24_council_tr/` was removed 2026-06-19 (replaced by `00_skill_router`, `04_tool_tiers`, `07_error_schema_hints`); its residual routes merged into `leader-council-skill/` on 2026-08-11 (D4). `26_workflow_ingestion/` was assigned when R43 introduced MD workflow parsing; renumbering would break inbound references.

## Routing constraints

- Each task should load ≤ 3 skills.
- Negative test: skill not found → ASK/STOP, never guess.
- Risk-high skills (write/delete/native) → require User trigger "tiến hành".

## Status

**R43.3 status (2026-05-21; R2 structure cleanup 2026-06-16; D4 cleanup 2026-08-11)**: council route resolves to `leader-council-skill/SKILL.md`. The legacy `24_council_tr/` folder was removed 2026-06-19 and its residual routes were merged into `leader-council-skill/` on 2026-08-11. Run `audit_tool_matrix` for live count.

- Council route measurement/scorecard: use `council_route_scorecard` after council replies/timeouts; source contract: `leader-council-skill` Route Measurement Gate.
