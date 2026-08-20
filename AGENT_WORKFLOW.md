# Agent Workflow

This workflow is mandatory for Cloud Agent (CA) sessions that work on this project.

## Primary Contract

The source of truth for this project is:

- `AGENT_BOOTSTRAP_PROMPT.md` — session rules, tool classification, bug history
- `TOOL_REFERENCE.md` — field-data tool ratings (trust [CAgpt] over [Est])
- `WORKFLOW_T1.md` / `WORKFLOW_T2.md` / `WORKFLOW_T3.md` / `WORKFLOW_T4.md` — step-sized progressive workflow owners
- `CHANGELOG.md` — latest fix status (read newest first)
- `PROJECT_MAP.md` — project structure overview
- `.orchestrator_cache/INDEX.json` — accessed via `get_project_summary` / `query_index` only (never raw)

## Progressive Disclosure

Workflow chooses the next step. The step chooses one allowed tool family. The chosen tool family loads only the matching schema. The schema loads only the matching hint. The hint routes only to the matching skill.

Use `progressive_matrix_check` for static alignment between workflow docs, schema phase metadata, and UI affordances. Treat that as design certification only; runtime behavior still requires the relevant T4 harness.

## Roles

| Actor | Role |
| :--- | :--- |
| CA | Decides what to inspect, edit, verify, and report |
| Orchestrator | Provides REST/MCP tools for controlled filesystem access |
| Cache | Provides low-token routing metadata |
| User | Provides goal, approval, and acceptance test |

## Mandatory Session Start

1. `open_session(session_title, project_path)` — start session log.
2. `get_project_summary(project_path)` — route from groups, hotspots, entrypoints, and context flags.
3. `workflow_status(project_path)` when workflow state exists.
4. `cache_status(project_path)` only as a cache health check; if missing or stale, call `refresh_project_map`.
5. `get_usage_stats(project_path)` — always call to see error rates + recent writes (not conditional on `has_session_log`).
6. Do not read source files before this step unless the task is explicitly about missing/broken cache.

## Read Workflow

1. Follow `WORKFLOW_T1.md` and load only the next step-sized hint/schema/skill.
2. Use `get_project_summary` / `query_index` to identify candidate modules by group, purpose, imports, LOC, and complexity.
3. Use `find_symbol` to locate exact function/class line ranges.
4. Call `read_file_range(file_path, line_start, line_end)` for the target range.
5. Use `search_code` only when the index does not contain the needed symbol or text.
6. Keep source reads narrow. Do not dump entire source files into context.

## Write Workflow

1. Follow `WORKFLOW_T2.md` and keep changes within the explicit EXECUTE trigger.
2. Classify change size and risk.
3. Ask the user about backup before large or risky changes.
4. Read the smallest relevant range first.
5. Use `write_fix` for existing file edits.
6. Use `write_new_file` for new files.
7. Use `delete_file` only with explicit user approval.
8. Run focused tests or checks.
9. After writes: `validate_file` → `validate_symbols` (code files) → `cache_delta`. `cache_status` only confirms cache exists — do NOT use as post-write verifier.
10. Call `refresh_project_map` when symbols/line refs may have shifted. Do not read `INDEX.json` raw; use `get_project_summary` / `query_index` / `find_symbol`.

## Runtime Workflow

1. Follow `WORKFLOW_T4.md` for browser, DOM, desktop, Electron, server lifecycle, and runtime harness work.
2. Inspect first with read-only T4 tools.
3. Use runtime-mutating T4 actions only when the current user turn has explicit trigger semantics.
4. Prove browser/server behavior with T4 runtime harnesses; do not certify it from the matrix alone.

## Backup Gate

Ask the user to confirm backup before:

- 3 or more files are touched
- cross-language or cross-module edits
- delete, cut, move, overwrite, or generated migrations
- user-facing behavior changes
- files larger than 500 LOC **that will be modified** (read-only large files do not trigger gate)

If the user already backed up the app, record that and proceed.

## Multilingual And Multi-Code Workflow

1. Preserve human language content unless translation is requested.
2. Detect code language from extension, imports, parser data, and cache metadata.
3. Route mixed-language work by language and file group.
4. Prefer language-aware symbol refs over text search.
5. For unknown or huge files, inspect metadata and bounded chunks first.

## Cache Output Requirements

`INDEX.json` should remain optimized for CA routing:

- `files`: path, group, purpose, LOC, complexity, imports, symbol count
- `functions`: name to file and line refs
- `classes`: name to file and line refs
- `entrypoints`: startup files
- `groups`: project composition
- `hotspots`: complex files worth prioritizing

`PROJECT_MAP.md` should remain optimized for human scanning:

- groups
- entrypoints
- hotspots
- agent usage notes

## Guardrails

- Do not scan `venv`, `node_modules`, logs, backups, archives, generated reports, or secret files.
- Do not use `search_code` as the first tool when cache is available.
- Do not delete cleanup candidates without user approval.
- Do not claim token savings without stating the estimate method.
- Do not translate or rewrite human-language text unless requested.
- Follow `../README.md` section 9 for the shipped-vs-planned tool roadmap (supersedes the retired `ORCHESTRATOR_UPGRADE_ROADMAP.md`).
