# WORKFLOW_T3 - Session / Audit / Reporting

T3 owns session lifecycle, audit repair, and handoff summaries. T3 does not replace T1 reads or T2 writes.

## Step T3.0 - Session Open Or Resume

Goal: create or resume the active CA session for the project.

Allowed tools: `open_session`, `prepare_ca_session`, `rename_session`.

Hint source: session tool inspector.

Schema source: chosen session tool schema.

Skill route: `17_session_audit`.

Guardrail: every project path must be canonicalized before session mutation.

Validation: active session id and project path are known.

## Step T3.1 - Handoff Summary

Goal: produce a compact audit/handoff summary when a work segment ends.

Allowed tools: `session_audit_summary`, `close_session`.

Hint source: selected tool inspector.

Schema source: selected tool schema.

Skill route: `17_session_audit`.

Guardrail: do not close a session that still has unresolved write validation.

Validation: summary names changed files, validations, open risks, and next owner.

## Step T3.2 - Native Fallback Audit

Goal: record authorized native fallback or queue it while [A] is unavailable.

Allowed tools: `log_native_event`, `create_pending_audit`, `sync_pending_audit`.

Hint source: native audit inspector.

Schema source: selected audit tool schema.

Skill route: `12_native_audit`.

Guardrail: native fallback is never silent; after [A] recovers, pending audit must sync.

Validation: audit event id or pending audit id is returned.

## Step T3.4 - Matrix Reporting

Goal: summarize static workflow/schema/UI alignment status after certification work.

Allowed tools: `progressive_matrix_check`.

Hint source: selected tool inspector.

Schema source: selected tool schema.

Skill route: `00_skill_router`.

Guardrail: separate design/matrix fixes from runtime proof.

Validation: final report includes implemented changes, validation results, and runtime-required gaps.
