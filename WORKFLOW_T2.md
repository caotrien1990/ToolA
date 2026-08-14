# WORKFLOW_T2 - Write / Verify

T2 is only active after an explicit EXECUTE trigger. Every write batch must validate, refresh cache, and return to T1.

## Step T2.0 - Write Scope Gate

Goal: confirm the requested mutation is local enough to perform and identify the exact files.

Allowed tools: `backup_status`, `tool_decision_hint`, `read_file_range`.

Hint source: selected tool inspector.

Schema source: selected tool schema only.

Skill route: `05_write_pipeline` for multi-file writes, `12_native_audit` only for authorized native fallback.

Guardrail: do not mutate files outside the triggered scope.

Validation: list target relative paths and the reason each is needed.

## Step T2.1 - Backup And Token Gate

Goal: ensure protected writes have a mutation token and a rollback path.

Allowed tools: `backup_status`, `backup_project`, `protected_tools_status`, `get_token`, `apply_after_dry_run`.

Hint source: token/auth inspector when token errors occur.

Schema source: chosen protected tool schema.

Skill route: `06_token_auth` on token errors, otherwise `05_write_pipeline`.

Guardrail: never expose raw token in reports or logs.

Validation: backup state and token requirement are known before write.

## Step T2.2 - Apply Smallest Coherent Patch

Goal: make the smallest coherent implementation change.

Allowed tools: `write_fix`, `write_fix_html`, `write_new_file`, `delete_file`, `write_transaction`.

Hint source: chosen write tool inspector.

Schema source: chosen write tool schema.

Skill route: `05_write_pipeline`.

Guardrail: no unrelated refactors; no silent native fallback.

Validation: tool response confirms changed files or dry-run id.

## Step T2.3 - Focused Validation

Goal: prove touched files are syntactically and semantically acceptable.

Allowed tools: `validate_file`, `validate_symbols`, `validate_encoding`, `post_write_batch_verify`, `run_test_profile`.

Hint source: validation tool inspector.

Schema source: validation tool schema.

Skill route: `05_write_pipeline`, plus test-specific skill only when the harness requires it.

Guardrail: validation failure blocks final DONE; fix or report the blocker.

Validation: include exact validation and test outcomes.

## Step T2.4 - Cache Delta And Return

Goal: update [A] cache for changed files and return to T1.

Allowed tools: `cache_delta`, `refresh_project_map`, `generate_project_map`.

Hint source: cache tool inspector.

Schema source: selected cache tool schema.

Skill route: `05_write_pipeline`.

Guardrail: only one post-write `cache_delta` is allowed without a new trigger.

Validation: cache delta reports changed files; final state is DEFAULT T1.
