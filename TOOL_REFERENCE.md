# Tool Reference

> **R43.3**: this file lists representative tools per tier for the current public surface. The live `/api/tools` endpoint remains the canonical exhaustive source.
>
> **Metadata sources** (R43.3 M5 note): three endpoints expose tool metadata with different shapes:
> - `GET /api/tools` — full JSON schema array (canonical, machine-readable).
> - `GET /api/tools?name_contains=<name>` — filtered subset (canonical replacement for the deprecated `/api/tool_inspector/{name}`).
> - `GET /api/tool_inspector/{name}` — **DEPRECATED since R16.9**, returns `deprecated:true` header. Kept for backwards compat. Migrate before R45.
> - `ca_guidance` block inside tool response — error-time hint (`call_pattern`, `payload_example`, `required_fields`, `accepted_aliases`). Trimmed view derived from the schema.

The canonical tool contract is available at `GET http://localhost:8083/api/tools`. Each public function schema includes `x_phase` and `x_phase_label`.

## Progressive Disclosure Contract

Workflow chooses the next step. The step chooses one allowed tool family. The chosen tool family loads only its matching schema. The schema loads only its matching hint. The hint routes only to the matching skill.

Do not eager-load all schemas, hints, or skills. Use `progressive_matrix_check` to certify static alignment, then run T4 harnesses for runtime behavior.

## Phase T1: Discover / Analyze / Read

Use T1 before mutation: open a session, summarize the project, search indexes, locate symbols, read exact ranges, inspect capabilities, and run static matrix certification.

Representative tools: `open_session`, `get_project_summary`, `search_code`, `find_symbol`, `read_file_range`, `tool_decision_hint`, `progressive_matrix_check`.

CBM campaign P3: `cbm_test_gap` is a T2 derived-index-aware advisory for static import-reach test gaps (`no_tests` => unavailable, not 0%). `include_architecture` defaults false; when true the same payload adds stability + entry reach. Generic architecture/quality routes to T2 `cbm_cycle_point`.

CBM campaign P4: `cbm_quality_gate` (T1) modes `rules` / `compare` / `snapshot`. Snapshot returns JSON only (`wrote=false`); persist only via later `write_new_file` overwrite=false under `.orchestrator_cache/cbm/baselines/<scope-hash>/<run-id>.json`. 13 keys; unknown key = invalid_config; `max_cc` / `no_god_files` unavailable v1. Named `cbm_quality_gate` is H1 C4 after registration; generic quality still `cbm_cycle_point`.

CBM campaign P5: `cbm_what_if` (T2) may refresh graph.db before RAM-clone simulation of `move_file`/`add_edge`/`remove_edge`/`remove_file`/`break_cycle`. Simulation never writes source/DB.

CBM campaign P6: `cbm_evolution` (T1) argv-only git history (90d/1000 commits, no shell/network/git config). Jaccard=co/(a+b-co). Pair cap 50 files/commit (1225 pairs) + dropped count. Author IDs hashed by default (SHA256 email, full 64 hex; missing email unknown). Rename v1 = new path + `rename_history_partial`. No git => `unavailable:not_git_repository`. Missing CC never invents a hotspot. `cbm_dsm` (T1) RAM-only DSM: level = longest path toward sinks, order level then lexical, exact propagation N<=500, 501 sampled, 2001 truncated (lowest+highest 1000), page <=100x100 sparse cells only. Named `cbm_evolution` / `cbm_dsm` are H1 after registration; generic quality still `cbm_cycle_point`. No H4 stage/diff feed. Never writes source/DB.


Use T2 for backup, write, delete, validation, cache refresh, bounded commands, and test profiles. Mutating T2 calls require `X-Orchestrator-Token` unless `run_project_command` is recognized as read-only.

Representative tools: `backup_status`, `backup_project`, `write_fix`, `write_new_file`, `write_transaction`, `validate_file`, `validate_symbols`, `run_test_profile`, `cache_delta`.

## Phase T3: Session / Audit / Reporting

Use T3 for session lifecycle, audit repair, native fallback logging, and handoff summaries.

Representative tools: `prepare_ca_session`, `session_audit_summary`, `close_session`, `log_native_event`, `create_pending_audit`, `sync_pending_audit`.

## Phase T4: Runtime / DOM / Browser / Server Lifecycle

Use T4 for runtime inspection, browser/DOM snapshots, server lifecycle checks, desktop/Electron probes, and UI harnesses. T4 inspect is read-first; browser clicks, form fills, JS evaluation, desktop actions, and server restarts need explicit trigger semantics.

Representative tools: `open_browser_page`, `read_page`, `query_selector`, `read_console_errors`, `screenshot_page`, `click`, `fill`, `server_lifecycle_status`, `detect_stale_server`, `run_playwright_test`, `assert_console_no_error`.

Desktop/CDP notes: use `list_cdp_targets` before Electron/Chromium focus/click debugging. `desktop_app_focus` and `desktop_click` return `method` (`cdp` or `win32_attach`), `matched_title`, and CDP URL evidence when available. Win32 `desktop_click` returns `resolved_hwnd`, `child_hwnd`, and `resolved_child_hwnd`; it uses synchronous `SendMessageTimeout` rather than `PostMessage`.

Runtime cleanup: use protected T2 `kill_process` for explicit PID/name cleanup. Prefer `pid` over `process_name` to avoid broad termination.

GUI launch notes: `run_project_command` supports `interactive` and `elevated` for Windows GUI launch flows. `elevated` requests ShellExecuteW `runas` and cannot guarantee stdout/stderr capture or a PID; verify with `expected_port` or `expected_process_name`.

## Field Corrections

- `write_fix_html`: no `dry_run`; supports `content_encoding`.
- `write_new_file`: no `dry_run`; use `path`, not `file_path`.
- `run_project_command`: mutating command requires token.
- `run_test_profile`: use returned `available_profiles` if profile is invalid.
- `create_benchmark_request`: accepts `project_path`, `idea`; no `description`.
- `create_build_context`: accepts `project_path`, `idea`, `approved_inputs`; no `name`.
- `get_folder_size`: use `folder_path`.
- `log_native`: use `native`.
- `get_session_events`: prefer `session_id`; `project_path` only resolves active sessions.
- `progressive_matrix_check`: static alignment only; runtime behavior still requires a T4 harness.
- `list_cdp_targets`: read-only CDP target enumeration through [A].
- `desktop_click`: audit `method`, `sequence`, `resolved_child_hwnd`, and `message_api`.
- `/api/token/refresh`: renew token metadata for long rounds; never log the token.
