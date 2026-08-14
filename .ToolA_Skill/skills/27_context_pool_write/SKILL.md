---
name: context_pool_write
description: |
  Use when Anh or a CA asks to manually save a compact memory, decision, rule, schedule,
  or context item into the shared context pool without forcing clau-mem to distill raw noise.
trigger:
  - task_pattern: "ghi memory | save memory | context pool | ghi vao pool | librarian ingest | thu thu ghi"
  - tool: "write_new_file | write_fix"
preconditions:
  - "Anh explicitly asks to save/write memory/context OR leader needs to persist an approved decision"
related_skills:
  - 00_skill_router
  - 05_write_pipeline
  - 17_session_audit
risk_level: medium
---

# 27 - Context Pool Write

Goal: persist only clean, bounded memory artifacts for the librarian. Do not dump raw transcript, full logs, or full tool output.

## When To Use

Use this skill when Anh asks to save a fact/rule/decision/schedule into the shared memory/context pool, or when a council/leader workflow produces an approved durable rule that future CAs should query.

Do not use for ordinary session notes, speculative ideas, or unverified worker claims.

## Write Shape

Create one UTF-8 markdown or JSON artifact under a bounded context-pool/staging folder, then let the librarian ingest/distill it later.

Recommended fields:

- `title`
- `status`: `approved | stale | superseded | needs_verify`
- `scope`: `[A] | Q | rawcut | council | global_policy | project_specific`
- `when_to_use`
- `content`
- `avoid`
- `source_ref`
- `verified_by`
- `created_at`

## Procedure

1. Use `[A] write_new_file` or `write_fix` only. Prefer dry-run first for new policy/rule artifacts.
2. Keep the artifact short: one decision/fact cluster per file.
3. Mark stale corrections explicitly instead of deleting history unless Anh requests destructive cleanup.
4. If the item came from worker/council output, include the worker id and report path, not the full raw response.
5. After writing, route librarian ingest/distill as a separate step if needed; do not force clau-mem to infer from noisy raw session text.

## Default Paths

Use the active project when it owns the fact. For shared CA policy, use a bounded artifact folder such as:

`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\17. Council_Tr\context_pool_specs\`

For [A] source-of-truth rule changes, update canonical [A] rule/config first, then write a compact context-pool note pointing to the canonical file.

## Hard Gates

- Never write secrets/API keys into memory artifacts.
- Never write unbounded raw transcript.
- Never overwrite old memory silently; use `supersedes` or `stale_correction` fields.
- If the correction is about model pricing/free status, include the exact date and source/config evidence.