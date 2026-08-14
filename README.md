# Project Orchestrator Server

Deterministic REST/MCP server for project inspection, cache routing, bounded reads, edits, workflow bootstrap, and focused tests.

## Purpose

Project Orchestrator gives coding agents a small, auditable tool layer for reading, editing, validating, testing, caching, and closing sessions without scanning whole projects or writing outside the workflow contract.

## Run

```powershell
python orchestrator_server.py
```

Open `http://localhost:8083`.

## Core Workflow

1. `open_session(session_title, project_path)` â€” start session log
2. `get_project_summary(project_path)` â€” route via tools, do not read `INDEX.json` raw
3. `workflow_status(project_path)` when workflow state exists
4. Use `query_index`, `find_symbol`, `search_code`, and `read_file_range` for targeted context
5. Edit through write tools (`write_fix`, `write_fix_html`, `write_new_file`, `delete_file` after explicit confirmation)
6. After each write: `validate_file(file_path, project_path)` â†’ `validate_symbols(file_path, project_path)` â†’ `cache_delta(project_path)`
7. End each session with `log_native(native=[...])` (or `native=[]`) then `close_session(project_path)`

## Read These Docs

- `AGENT_BOOTSTRAP_PROMPT.md` â€” agent rules, T1/T2/T3 phases, native fallback, and session close contract.
- `TOOL_REFERENCE.md` â€” callable tool inventory and current live-tool notes.
- `WORKFLOW_T1.md` â€” fixing or improving an existing app.
- `WORKFLOW_T2.md` â€” building a new app or scaffold from scratch.
- `TEST_HARNESS.md` â€” deterministic smoke and regression cases for tool behavior.

## Deterministic Boundary

This server does not use internal AI, local model executors, model proxies, API-key AI adapters, or crawler tools.

## Optional Capability Reporting

Use `capability_status` and `suggest_missing_capabilities` to see which parser libraries could improve cache quality. The server does not install dependencies automatically.

## MCP Mode

```powershell
python orchestrator_server.py --mcp
```

The MCP mode exposes the same deterministic project tools.

## Reproducibility & Verification

**Test suite** - use the project `venv312` interpreter and a *fresh* `--basetemp` directory per run (reusing basetemp leaks state across runs):

```powershell
venv312\Scripts\python.exe -m pytest --basetemp=tests\tmp_fresh -q
```

Quiescent baseline: **385 passed, 0 failed, 1 skipped**. Run with the orchestrator server idle (no active council dispatch): `test_council_runtime_status_tracks_terminal_state` reads a shared council-runtime file, so a live dispatch for the same agent (e.g. `ope`) can flip it to an environmental failure unrelated to source state.

**Rebuild the CBM graph DB** (derived artifact - safe to regenerate):

```powershell
Remove-Item .orchestrator_cache\graph.db -ErrorAction SilentlyContinue
venv312\Scripts\python.exe -c "from orchestrator.graph.indexer import cbm_index; import json; print(json.dumps(cbm_index('.', 'full')))"
```

A clean full re-extract (~25s) rebuilds `.orchestrator_cache/graph.db` from 448/448 source files.

**Source-of-truth & contract docs** (project root):
- `EXECUTION_TRACKER.md` - per-phase execution history + timestamps (AQ upgrade).
- `SOT_MAPPING.md` - 12 standalone sources, 0-drift invariant.
- `ALIAS_CONTRACT.md` - tool parameter aliases (backed by `test_schema_params_contract.py`).

**Version marker.** Current revision: **V15 R42** (referenced throughout `orchestrator_server.py`). `GET /health` exposes liveness, port, and auth posture (`auto_token_inject`, `mutation_token_required`) but no version string - use the in-source `V15 R42` marker as the semver reference.
