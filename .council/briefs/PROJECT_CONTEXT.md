# Project Context

This file is generated for CA routing. Read it before opening source files.

- Root: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council\briefs`
- Generated: 2026-11-21T06:06:26
- Files indexed: 27

## First Actions

1. `cache_status` -> `get_project_summary` -> (if has_session_log) `get_usage_stats`
2. Navigate: `find_symbol(name)` -> lay range_id -> `read_file_range` (<=200 lines)
3. KHONG doc `.orchestrator_cache/INDEX.json` raw -- dung tool query
4. Sau write: `validate_file` -> `cache_delta`

## Search Routing

| Goal | Tool | Use search_code when |
|------|------|----------------------|
| Find function/class def | `find_symbol` | find_symbol misses |
| Filter files by group/LOC | `query_index` | -- |
| Find callers/imports | `search_code` | always valid |
| Regex pattern search | `search_code` | always valid |

Guardrail: NOT first tool in session -- NOT replacing find_symbol for defs -- escape `.()+-` in regex

## Groups

- `doc`: 24 files, 1528 LOC
- `unknown`: 3 files, 125 LOC

## Highest Value Files

- `TQ11-C2_verify.md` | group `doc` | lang `markdown` | LOC 56 | weight 1: small_context: full read acceptable if needed
- `TQ11-C1_fix.md` | group `doc` | lang `markdown` | LOC 93 | weight 1: small_context: full read acceptable if needed
- `TQ9-C2_clauc_chot_audit.md` | group `doc` | lang `markdown` | LOC 52 | weight 1: small_context: full read acceptable if needed
- `TQ11-C1R2_fix.md` | group `doc` | lang `markdown` | LOC 72 | weight 1: small_context: full read acceptable if needed
- `TQ-FX-AUDIT.md` | group `doc` | lang `markdown` | LOC 59 | weight 1: small_context: full read acceptable if needed
- `TQ10-C1_clauc_ds0105.md` | group `doc` | lang `markdown` | LOC 43 | weight 1: small_context: full read acceptable if needed
- `TQ-FX-ADMIT.md` | group `doc` | lang `markdown` | LOC 61 | weight 1: small_context: full read acceptable if needed
- `TQ10-C2_clauc_ds0611.md` | group `doc` | lang `markdown` | LOC 42 | weight 1: small_context: full read acceptable if needed
- `G1_FINISH_1409_ope.md` | group `doc` | lang `markdown` | LOC 110 | weight 1: small_context: full read acceptable if needed
- `TQ-CV-JOBS.md` | group `doc` | lang `markdown` | LOC 70 | weight 1: small_context: full read acceptable if needed
- `TQ-CV-RESID.md` | group `doc` | lang `markdown` | LOC 68 | weight 1: small_context: full read acceptable if needed
- `TQ-CV-AUDIT.md` | group `doc` | lang `markdown` | LOC 53 | weight 1: small_context: full read acceptable if needed
- `TQ9-C1_clauc_leftover_scan.md` | group `doc` | lang `markdown` | LOC 53 | weight 1: small_context: full read acceptable if needed
- `TQ-CV-LINEAGE.md` | group `doc` | lang `markdown` | LOC 48 | weight 1: small_context: full read acceptable if needed
- `G1_FINISH_1409_DIRECT_ope.md` | group `doc` | lang `markdown` | LOC 111 | weight 1: small_context: full read acceptable if needed
- `DISPATCH_CLAUC_R1_AHEALTHHUNG.md` | group `doc` | lang `markdown` | LOC 109 | weight 1: small_context: full read acceptable if needed
- `DISPATCH_CLAUC_R2_ARGVFALSE.md` | group `doc` | lang `markdown` | LOC 80 | weight 1: small_context: full read acceptable if needed
- `TQ-FX-LINEAGE.md` | group `doc` | lang `markdown` | LOC 48 | weight 1: small_context: full read acceptable if needed
- `TQ11-C2R2_verify.md` | group `doc` | lang `markdown` | LOC 38 | weight 1: small_context: full read acceptable if needed
- `clauc_harness_verify_r1.md` | group `doc` | lang `markdown` | LOC 117 | weight 1: small_context: full read acceptable if needed

## Entrypoints


## Hotspots

- `TQ11-C2_verify.md`: complexity 9, 56 LOC, group `doc`
- `TQ11-C1_fix.md`: complexity 6, 93 LOC, group `doc`
- `TQ9-C2_clauc_chot_audit.md`: complexity 5, 52 LOC, group `doc`
- `TQ11-C1R2_fix.md`: complexity 4, 72 LOC, group `doc`
- `TQ-FX-AUDIT.md`: complexity 4, 59 LOC, group `doc`
- `TQ10-C1_clauc_ds0105.md`: complexity 4, 43 LOC, group `doc`
- `TQ-FX-ADMIT.md`: complexity 3, 61 LOC, group `doc`
- `TQ10-C2_clauc_ds0611.md`: complexity 3, 42 LOC, group `doc`
- `G1_FINISH_1409_ope.md`: complexity 2, 110 LOC, group `doc`
- `TQ-CV-JOBS.md`: complexity 2, 70 LOC, group `doc`

## Deterministic Rules

- No internal AI/model/proxy tools are required.
- No crawler tools are part of this workflow.
- Preserve human-language text unless the user requests rewriting or translation.
- Treat files over 500 LOC as large: read symbols or bounded ranges first.
- Never read `.orchestrator_cache/INDEX.json` raw -- use `get_project_summary` or `query_index`.
- Use `find_symbol` before `read_file_range` for large files (>500 LOC).
