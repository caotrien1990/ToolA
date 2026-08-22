# Project Orchestrator Server

Deterministic REST/MCP server for project inspection, cache routing, bounded reads, edits, workflow bootstrap, and focused tests.

## Purpose

Project Orchestrator gives coding agents a small, auditable tool layer for reading, editing, validating, testing, caching, and closing sessions without scanning whole projects or writing outside the workflow contract.

## Run

```powershell
python orchestrator_server.py
```

Open `http://localhost:8083`.

Restart is a single tool: `restart_orchestrator_via_bat` with only `delay_seconds=2`. A commit does **not** change the running process -- the daemon holds its code in memory, so a fix is not live until a restart. See `MAINTENANCE_DOCTOR.md`.

## Core Workflow

1. `open_session(session_title, project_path)` -- start session log
2. `get_project_summary(project_path)` -- route via tools, do not read `INDEX.json` raw
3. `workflow_status(project_path)` when workflow state exists
4. Use `query_index`, `find_symbol`, `search_code`, and `read_file_range` for targeted context
5. Edit through write tools (`write_fix`, `write_fix_html`, `write_new_file`, `delete_file` after explicit confirmation)
6. After each write: `validate_file(file_path, project_path)` -> `validate_symbols(file_path, project_path)` -> `cache_delta(project_path)`
7. End each session with `log_native(native=[...])` (or `native=[]`) then `close_session(project_path)`

A read that precedes a write must go through `read_file_range`: it seeds the read token `write_fix` requires. A native read before a write yields `FILE_CHANGED_OUTSIDE_ORCHESTRATOR`; recover with `read_file_range(force_reseed=true)`.

A write that times out on the client has **not** failed. Every mutating call opens a ledger entry *before* the tool runs, so the outcome is queryable the whole time the server is still working:

```
GET /api/write_status?operation_id=<id>
GET /api/write_status?project_path=<root>&file_path=<file>   # no id: the response never arrived
```

It answers `commit_state` (`in_flight` | `committed` | `not_committed`), `safe_to_retry`, and `ca_next_action`. A successful write also returns its own `operation_id` and `write_status_url`. Poll until `commit_state` is terminal; do not resend a large write blind. Measured on a 10MB file: the client gave up at 4s, `write_status` reported `in_flight` from t+4.6s, and `committed` at t+54s — a blind retry there would have been a second full write of a file that was already being written.

## Read These Docs

- `AGENT_BOOTSTRAP_PROMPT.md` -- agent rules, T1/T2/T3 phases, native fallback, and session close contract.
- `MAINTENANCE_DOCTOR.md` -- **start here when something is broken.** Symptom-first triage: the symptom as it appears, the cause that was assumed, the cause that was real, and the test that pins it. Also covers editing [A] itself and the mutation-testing bar.
- `KNOWN_BUGS.md` -- open and closed defects in `B###`/`R###` form.
- `scripts/live_answer_doctor.py` -- **run this after a restart that ships a change to an extractor, an index path, a fallback, or a filtering argument.** Asks the LIVE daemon about a real, already-indexed, non-Python repo and checks each answer against something knowable independently of the tool being asked. B011-B016 all shipped through a green suite, because a fresh `tmp_path` fixture is the one environment in which all six of those answers are honest. `--ci` exits 1 on FAIL; a WARN means the check could not run here; exit 2 means the daemon did not answer, which is not a verdict. Registered in `scripts/DIAGNOSTIC_SUITE.md` as the *answers* layer next to the *structure* layer.
- `TOOL_REFERENCE.md` -- callable tool inventory and current live-tool notes.
- `WORKFLOW_T1.md` -- fixing or improving an existing app.
- `WORKFLOW_T2.md` -- building a new app or scaffold from scratch.
- `WORKFLOW_T3.md`, `WORKFLOW_T4.md` -- research/council and stateful page-session tiers.
- `ALIAS_CONTRACT.md` -- tool parameter aliases (backed by `test_schema_params_contract.py`).

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

**Test suite** -- use the project `venv312` interpreter and the default temp directory:

```powershell
venv312\Scripts\python.exe -m pytest tests\ -q
```

**Do not pass `--basetemp` at a fixed path, and never one on `D:`.** The opposite advice used to live in this file and did real damage: `D:` is a HDD, and routing the suite's temp I/O onto it saturated the spindle until unrelated folders stopped opening in Explorer -- which read as a locked folder and was a busy disk. A fixed path is also shared mutable state, since pytest wipes its basetemp at startup and two concurrent runs then delete each other's fixtures. `%TEMP%` is on SSD, per-run, and is never OneDrive-redirected; it is the correct default. `test_conftest_stale_symlink_tolerance.py` enforces this repo-wide.

Quiescent baseline: **2610 passed, 0 failed, 4 skipped, 1 xfailed, 169 subtests passed in 420s** across 250 files in `tests\`. Scope is `tests\` only: 27 more tests live in three files at the repo root (`test_all_tools.py`, `test_council_reply_fallback.py`, `test_open_session_drift_note.py`), so a bare `pytest` collects 2642 and reports **2637 passed** (173 subtests -- the root files carry 4 of their own) -- both numbers are correct, they count different sets. Run with the orchestrator server idle (no active council dispatch): `test_council_runtime_status_tracks_terminal_state` reads a shared council-runtime file, so a live dispatch for the same agent (e.g. `ope`) can flip it to an environmental failure unrelated to source state.

If the run aborts during **collection** with `PermissionError: [WinError 5]` on a path under `%LOCALAPPDATA%\Temp`, that is the stale `pytest-current` symlink -- Entry 1 of `MAINTENANCE_DOCTOR.md`, not a sync client.

**Rebuild the CBM graph DB** (derived artifact - safe to regenerate):

```powershell
Remove-Item .orchestrator_cache\graph.db -ErrorAction SilentlyContinue
venv312\Scripts\python.exe -c "from orchestrator.graph.indexer import cbm_index; import json; print(json.dumps(cbm_index('.', 'full')))"
```

A clean full re-extract (~25s) rebuilds `.orchestrator_cache/graph.db` from source.

**Version marker.** Current revision: **V15 R102** (`R###` comment markers are used throughout `orchestrator_server.py`; 80 carry the `V15 R` prefix). `GET /health` exposes liveness, port, auth posture (`auto_token_inject`, `mutation_token_required`) and the running `generation`, but no version string -- use the highest in-source `R###` marker as the semver reference.
