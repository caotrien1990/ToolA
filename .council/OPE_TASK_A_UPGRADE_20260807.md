# OPE TASK — [A] Orchestrator upgrade items 3+4 + restart-guard bug fix (2026-08-07)

TASK_ID: A_UPG_P234_20260807
Leader: Clau (desktop). Worker: OpeC. Output language: Vietnamese (đúng dấu).
Repo root [A:P]: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`

## Context / starting state (verified by Clau)
- Add-a-tool pattern in [A]: `async def name(...) -> str` (returns json.dumps) in a module; register in `code_tools.py` near line ~13275 (block after the V15 `try/except`): `TOOL_MAP.update(...)` + `TOOL_PHASES[name]="T1|T2"` + append schema dict to `TOOL_SCHEMAS` (list; `_schema_for_tool` reads it live) + add mutating tools to `PROTECTED_TOOL_NAMES` (set at code_tools.py:199). Registry specs live in `orchestrator\core\registry_data.py`. Dispatch does NOT hard-validate params (tool self-validates).
- **Item 1 ALREADY DONE by Clau** (do NOT redo): `tools_upgrade_p4.py` (query_sqlite, file_profile, sample_data_file, read_chunks) + register block in `code_tools.py`. py_compile clean, self-tested on real librarian.db. Pending [A] restart to go live.
- **BUG confirmed:** restart is BLOCKED even with NO council worker running. `restart_orchestrator_via_bat` returns `RESTART_GUARD_BLOCKED / reasons:["COUNCIL_PID_ALIVE"]` although no council worker process exists (false-positive). Raw `start_orchestrator.bat` also fails to restart because [A] runs as a High-IL task ('ToolA_Orchestrator') that a non-elevated kill cannot stop. Guard source starts at `orchestrator_server.py:_council_alive_agents` (L7601) + independent restart-controller (`tools_v15.py` ~L6848, `RESTART_CONTROL_URL`). Likely stale `active_agents` or cmdline-signature matching an unrelated process.

## Objective
Complete [A] upgrade items 3 & 4 and fix the restart-guard false-positive so the upgrade (incl. item 1) can be activated and verified live.

## Split sub-tasks (sequential; ST1 unblocks ST4)
- **ST1 (unblock, HIGH risk):** Fix `COUNCIL_PID_ALIVE` false-positive. Requirement: when NO council worker process is actually alive, `restart_orchestrator_via_bat` must proceed; when a real worker IS alive it must still block. Investigate `_council_alive_agents` + restart-controller active_agents source. Minimal, targeted fix. Backup touched files first.
- **ST2 (item 3, MID-HIGH):** Implement MUTATING tools `insert_text`, `replace_symbol`, `move_file`, `copy_range`, `cut_range` in a new module `tools_upgrade_p12.py`; register + add to `PROTECTED_TOOL_NAMES`; update file cache after writes (reuse existing write_fix/cache helpers). py_compile + self-test each on a scratch file under `%TEMP%`.
- **ST3 (item 4, MID):** Implement `start_app`, `stop_app`, `http_check`, `browser_smoke_test` in `tools_upgrade_p5.py`; reuse existing run_project_command / CDP / browser infra; register. py_compile + self-test `http_check` against a known local port (e.g. :8083/health).
- **ST4 (activate+verify):** After ST1, restart [A]; verify EVERY new tool (item1: query_sqlite/file_profile/sample_data_file/read_chunks; item3; item4) is callable via `POST /api/call/<tool>` and has a schema at `/api/tools/<tool>/schema`. Run [A] built-in matrix audit (TOOL_MAP/TOOL_SCHEMAS/TOOL_PHASES). Report per-tool PASS/FAIL with evidence.

## Constraints / allowed actions
- Transport HTTP `/api/call/<tool>`. [A] self-edit = native edit + `log_native_event` (HYBRID); py_compile with `venv312\Scripts\python.exe` before any restart.
- Do NOT delete existing tools/files. Do NOT change [A] architecture or the transport contract. Do NOT touch item-1 files except to verify.
- Do NOT restart [A] until ST1 is verified fixed AND all edits py_compile clean.
- Back up each file before editing (copy to `*.bak_ope_20260807`).

## Acceptance criteria
- ST1: restart succeeds with no worker; still blocks with a live worker (show both).
- ST2/ST3: each new tool callable via /api/call + returns structured JSON; mutating tools reject on bad input; py_compile clean.
- ST4: all item1+3+4 tools listed in matrix audit with no "in TOOL_MAP but no schema" warnings; /api/call smoke for each returns ok or a proper validation error (not 500/NotFound).

## Stop conditions (report + wait)
- Before deleting any file, adding a pip dependency, or any architecture/schema change.
- If [A] fails to come back after a restart (STOP immediately, report, do not loop).
- After 2 failed attempts on any sub-task.
- If ST1 root cause requires editing the independent restart-controller service in a way that risks [A] availability.

## Output contract
- Artifact-first: write full findings/patches to `\.council\OPE_TASK_A_UPGRADE_20260807_RESULT.md`. Broker reply ≤5 lines with per-ST PASS/PARTIAL/FAIL + artifact path + END marker `END_OPE_A_UPG_P234`.
- Exact file:line evidence; no full files/logs in broker.
