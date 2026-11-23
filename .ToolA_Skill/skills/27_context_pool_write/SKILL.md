---
version: "1.0.0"
last_verified: "2026-11-22"
verified_head: "dd41e2fc8f78761f8f7b9a104fb5cd98fdf1cad0"
name: context_pool_write
description: |
  Describe an existing stale correction artifact and its provenance.
trigger:
  - task_pattern: "stale correction | superseded context item"
preconditions:
  - "An existing artifact has a named owner and source reference"
related_skills:
  - 00_skill_router
  - 05_write_pipeline
  - 17_session_audit
risk_level: medium
---

# Context correction artifact

When reviewing an existing correction, retain its original source, status and verification date. The fields below describe the artifact format. A future reader must identify its actual ingestion path before promising delivery.
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

For a correction, keep the original reference and identify the superseded item. Record who verified the replacement and when. Keep credentials and raw conversation logs out of the artifact.
