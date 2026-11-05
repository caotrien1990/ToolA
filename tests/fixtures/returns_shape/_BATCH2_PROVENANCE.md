# Batch 2 returns-shape fixtures — provenance (P16, 2026-11-05)

Batch 2 authored `returns` schemas for 22 of the 28 SET-38 tools that lacked
one after batch 1. Fixtures are real tool answers captured live on
2026-11-05 unless marked DERIVED below.

## Derived fixtures (constructed from handler code, NOT captured live)

- `backup_project` — success + skipped variants built verbatim from the
  return statements of `backup_project` / `_backup_project_locked`
  (code_tools.py). Not called live: a real call copies the full tree.
- `delete_file` — success + relative-path-error variants built verbatim
  from the handler in orchestrator/tools/write/core.py. Not called live:
  destructive tool.
- `convene_council` — config + fallback variants built from
  `convene_council` / `_council_build_from_config` and the
  `_COUNCIL_FALLBACK_*` constants (code_tools.py). Not called live: would
  convene real council work.

## Live-captured fixtures

- Live-safe reads: cache_status, backup_status, tool_decision_hint,
  validate_file, council_tracker, cbm_council_context, audit_tool_matrix,
  run_ps1_script, run_project_command.
- Scratch-target writes (effects confined to files created under
  C:\Users\ADMIN\AppData\Local\Temp\p16b2_scratch\): write_new_file,
  write_fix, write_fix_html, write_transaction, apply_after_dry_run,
  copy_file, ruff_fix, run_project_test_script.
- open_session (routine session state on the repo, title
  "p16b2-itemA-capture") and log_native_event (one event noting the
  capture) were also captured live.

## Not in batch 2

council_message, council_message_async, council_worker_model,
restart_orchestrator_via_bat, git_status and git_diff still have no
audited returns block: their only effective schema entries live in
orchestrator_server.py, which was out of scope for this batch. Prepared
blocks + fixtures are held by the coordinator pending a decision on
that file.
