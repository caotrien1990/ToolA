---
name: apply_after_dry_run
description: |
  Use when CA has run write_fix/write_new_file with dry_run=true and needs to apply
  the change safely. Manages dry_run_id lifecycle, STALE_TARGET handling, confirm_hash
  + confirm_warnings, and ensures atomic preview→apply without re-reading.
trigger:
  - tool: "apply_after_dry_run | write_fix with dry_run=true"
  - error_name: "STALE_TARGET | DRY_RUN_NOT_FOUND | TARGET_MODIFIED_SINCE_DRY_RUN"
preconditions:
  - "Active session"
  - "Mutation token"
  - "Recent dry_run completed (returned dry_run_id)"
related_skills:
  - 05_write_pipeline
  - 07_error_schema_hints
risk_level: high
---

# 13 — Apply after dry-run

## Lifecycle

```
1. write_fix(file, range, content, dry_run=true)
   → returns { ok:true, dry_run_id: "<uuid>", target_file_hash, diff_preview, validation, warnings }
2. Review diff_preview + validation + warnings (CA + User)
3. apply_after_dry_run(project_path, dry_run_id, confirm_hash=<target_file_hash>, confirm_warnings=[...])
   → server re-checks target_file_hash matches current disk; if not → STALE_TARGET
   → applies the previously-stored payload atomically
   → triggers same post-write validate + cache_delta
```

## When to use vs direct apply

| Situation | Tool |
|-----------|------|
| Low-risk single file < 30 lines | `write_fix(dry_run=false)` direct |
| Multi-file batch | dry_run all → review collectively → apply each with `apply_after_dry_run` |
| User-watching live review needed | dry_run + show diff_preview → wait confirm → apply |
| Risky / auth / token / delete | ALWAYS dry_run first, apply_after_dry_run with confirm_hash |
| Long content > 1000 lines | dry_run mandatory (reuse server-side cached payload) |

## STALE_TARGET handling

Server checks `target_file_hash` against current disk at apply time:
- If file changed externally between dry_run and apply → `STALE_TARGET`
- CA must NOT silently re-write; must:
  1. Re-read file (`read_file_range`)
  2. Compare with what the dry_run was based on
  3. Decide: re-do dry_run from new state OR cancel apply
- Discarding old dry_run_id is automatic on STALE_TARGET (no manual cleanup needed)

## confirm_warnings parameter

Dry_run may surface warnings (e.g., "removes 3 functions", "BOM detected", "encoding mismatch risk"). To apply despite warnings, pass them in `confirm_warnings=[...]`:
- Acts as explicit acknowledgment ("I read the warnings")
- Without it, apply may refuse with `WARNINGS_NOT_ACKNOWLEDGED`
- Empty list `[]` = no warnings to acknowledge (cleanest path)

## Anti-patterns

- ❌ Skipping confirm_hash (no protection against TOCTOU)
- ❌ Reusing same dry_run_id after STALE_TARGET (server already invalidated)
- ❌ Multiple apply calls per dry_run (idempotency depends on hash check; subsequent calls return DRY_RUN_NOT_FOUND)
- ❌ Long delay between dry_run and apply (window for external change widens; redo if > 60s)

## Workflow (canonical)

```
dry = write_fix(..., dry_run=true)
# CA + User review dry.diff_preview
result = apply_after_dry_run(
  project_path=proj,
  dry_run_id=dry.dry_run_id,
  confirm_hash=dry.target_file_hash,
  confirm_warnings=dry.warnings or []
)
# auto post-write: validate + cache_delta in result
```

## Error → action

| error_name | Action |
|------------|--------|
| `STALE_TARGET` | Re-read file, redo dry_run, do NOT auto-apply |
| `DRY_RUN_NOT_FOUND` | dry_run_id expired or wrong; start over |
| `WARNINGS_NOT_ACKNOWLEDGED` | Include confirm_warnings from dry response |
| `MUTATION_TOKEN_REQUIRED` | Fetch token (skill 06), retry once |

## Operational Impact

- Server caches dry_run payload → avoid resending content on apply
- Hash check prevents accidental overwrite
- Atomic apply reduces partial-write risk

## Guardrails

- Dry_run TTL ~5 minutes (server-side); after that, redo
- One apply per dry_run (idempotent via hash, but redundant calls = noise)
- Token required (this is a protected mutating tool)

## Harness

- Test 1: dry_run → immediate apply → success, auto validate
- Test 2: dry_run → wait > TTL → apply → DRY_RUN_NOT_FOUND
- Test 3: dry_run → external `touch` file → apply → STALE_TARGET → redo
- Test 4: dry_run with warnings → apply without confirm_warnings → WARNINGS_NOT_ACKNOWLEDGED → retry with list → success
