ENV
- CA = Code Agent; User = Human operator
- CA on Windows 11 persistent node.
- Windows node ID: 71ffaf590891829331c87b479d9a615b502af288905b84528ea31cd6410fce25
- Use CMD: ["cmd","/c","<command>"].
- PowerShell inline is broken. Use only script file: cmd /c powershell -NoProfile -File ...
- No WMIC. Use PowerShell Get-CimInstance when needed.

---
## ═════ PROJECT ORCHESTRATOR RULE ═════

# ═════ ORCHESTRATOR BOOTSTRAP ═════
0a. DEFINITIONS

- [A] = Orchestrator app
- [A:P] = D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA | WSL2/container → use [A:API]
- [A:S] = [A:P]\start_orchestrator.bat
- [A:API] = env ORCHESTRATOR_BASE_URL, fallback http://localhost:8083

0a.1 BOOTSTRAP RULE SOURCE

Active bootstrap rules may be loaded before health check.
If rules are already in prompt/context, use them directly; do not read rule files.
If not preloaded, read ONLY the explicit rule source provided by runner/User/config.
This exemption is only for rule loading, not repo/source files, folder scans, glob/grep, or runtime inspection.
Rule loading ≠ native fallback; no log_native_event.
After loading rules, run `0b` and enforce API boundary.

0b. HEALTH CHECK

Health check uses node -e only. No PowerShell inline for health. On Windows CMD, keep outer double quotes, prefer single quotes inside JS, and escape any required inner double quotes as \".

node -e "const http=require('http');http.get((process.env.ORCHESTRATOR_BASE_URL||'http://localhost:8083')+'/health',r=>{let d='';r.on('data',c=>d+=c);r.on('end',()=>console.log(d));}).on('error',e=>console.log('ORCHESTRATOR_NOT_RUNNING:'+e.message));"

OK: {"ok":true} | FAIL: ORCHESTRATOR_NOT_RUNNING:...
FAIL → ask User to run [A:S] → retry health.
OK / Retry OK → enter Mode Control, DEFAULT T1 Read-Only.
Retry FAIL during bootstrap → STOP, notify User, no bootstrap fallback. Work-phase fallback follows [PI:9].

# ═════ ORCHESTRATOR MODE CONTROL ═════
0c. DEFAULT MODE

Entry: health OK, execute done, or invalid/missing execute trigger.
Before project work, ask only missing startup fields: (1) response language, (3) target path for open_session.
Do not ask for fields already provided or inferable.
This startup check does not permit T2/T3; Mode Control still applies.

T1 only; see [PI:3]. No token.
No code/edit/project-command/test/deploy.
Runtime read-only DOM inspection is allowed for analysis; browser actions that change runtime state require clear User intent.
End per [RC]; usually 🔍 or ❓.

0d. EXECUTE MODE
Trigger: "tiến hành" + clear non-infra action; "tiến hành" alone is not enough.
VERIFY with T2 verify/test tools requires "tiến hành" unless covered by the post-write validation exception below.

Use T1 as needed.
Use T2/T3 only for the triggered action; token only for protected tools per [PI:4].
Infra commands require explicit command-level permission; see [PI:2].
Missing/ambiguous action → ASK; refuse T2/T3, stay DEFAULT, end ❓.
Active → [RC:3]. Done → DEFAULT, end 🔍 per [RC].
Native fallback only per [PI:9].
Exception — post-write validation:
After a successful write batch, validate_file, validate_symbols if supported,
and one cache_delta for the same changed files are allowed without a new
"tiến hành" trigger. This exception only completes the triggered write action;
it does not allow new edits, new tests, deploy, or unrelated T2/T3 tools.

0e. STRICT API BOUNDARY (HARD)
node -e = health only; http only; no fs/path/scripts.
Health OK → no native project file ops: no Read/Write/Edit, Node fs/path, Python, scripts, or shell file reads/writes.
Project ops = API only: POST [A:API]/api/call/{TOOL}; PowerShell = Invoke-RestMethod transport only.
Paths per [PI:5]: project_path absolute; file_path relative; folder_path only if schema requires.
User paths → open_session FIRST per [PI:2b]. If API call quoting fails before/while registering complex paths, use temp .ps1 API wrapper only as transport.
API/schema/tool errors → follow live schema/hints; retry corrected payload once before fallback. See [PI:6], [PI:10-11].
Native fallback only per [PI:9] → log_native_event + notify User. Token refresh ≠ fallback.

0f. PLATFORM TOOL BOUNDARY

Platform tools are not exempt from [0e]. After health OK, platform execution is allowed only as API transport to [A:API] via Invoke-RestMethod/approved wrapper. Platform file tools (`read_file`, `write_to_file`, `replace_in_file`, `search_files`, `list_files`) are forbidden for repo/project files. Do not bypass open_session, dry-run, validation, or audit. If no API transport is available, STOP and notify User. Bootstrap rule file read is the only pre-health native-read exception.

---
## PROMPT INDEX = [PI]
[PI:1] Orchestrator-first
[PI:2] Session lifecycle
[PI:2b] Auto-register paths (HARD)
[PI:3] Tool tiers / mode gate
[PI:4] Token-protected tools
[PI:5] Path semantics
[PI:6] Write/dry-run rule
[PI:6b] Backup threshold (RECOMMENDED)
[PI:7] Command rules (Windows-first)
[PI:7b] CLEANUP
[PI:8] Test profile rules
[PI:9] Native fallback audit
[PI:10] Error/retry guidance
[PI:11] Live schema helpers
[PI:12] Token Panel
[PI:13] Adaptive guidance / Coverage
[PI:14] Report contract
[PI:15] Post-write checklist

---
1. Orchestrator-first

North star CSLt: Correctness > Save token > Less context > Time fee. Before any work, verify health per 0b.
If health OK, use API via [A:API] only. Native fallback only per [PI:9].

2. Session lifecycle

Infra commands are User-run by default; generic “tiến hành” is not infra permission.
CA may run infra only with explicit in-session permission naming the command; record the override.
If health is down, ask User to run [A:S], then recheck per 0b.
Start project work with open_session → get_project_summary. prepare_ca_session is T3 handoff/bootstrap only.
Before close_session, call get_session_events if summary/audit is needed.
If path/action is inferable from User input, do not ask again; proceed within current mode constraints.

2b. Auto-register paths (HARD)

Any User-mentioned file/folder → identify project root first, then open_session(session_title, absolute project_path). File itself becomes relative file_path per [PI:5].
Applies to all paths, including outside current project/[A:P]. Use project_path/file_path per [PI:5].
After registration, API only; never native Read/Write/Edit/Glob/Grep for “outside project”.

3. Tool tiers / mode gate

DEFAULT = T1 only. EXECUTE = T1 as needed + T2/T3 only for triggered action.
Tool list: [A:P]\examples\tooling\tool_tiers.md.
Token follows protected schema, not tier label; see [PI:4].
No T2 verify/test/run in DEFAULT except post-write validation per [0d].

3b. Repo vs Runtime tool routing (HARD)

Repo/code checks use [A]; runtime UI checks use DOM/browser. DOM confirms runtime only, never substitutes repo/source inspection.
Full routing: [A:P]\examples\tooling\repo_vs_runtime_routing.md.

4. Token-protected tools

Token is required only for protected tools per live schema.
Do not infer token from tier label.
Before first protected call: GET [A:API]/api/token → store $token → send X-Orchestrator-Token: $token.
Never send empty token header. Reuse $token for the session. On 403/MUTATION_TOKEN_REQUIRED: refetch and retry max 2, then stop with structured error.
Never expose raw token. Token refresh ≠ native fallback. Example: run_project_command protected; run_test_profile unprotected.

5. Path semantics

Unicode/special-char paths must remain UTF-8 JSON strings; do not base64-encode project_path/file_path unless live schema explicitly supports path encoding. If quoting/transport fails, use Command Helper or temp .ps1 API wrapper per [0e]/[PI:7].

project_path = absolute target project root, not necessarily [A:P]. file_path = relative inside project_path; never absolute.
folder_path = absolute; use only for get_folder_size or when live schema explicitly requires folder_path.
Avoid legacy path unless live schema requires it.
Never join project_path with absolute file_path.

6. Write/dry-run rule

write_fix is line-range replace only, never pattern replace.
Flow: read_file_range → choose start_line/end_line → write_fix(dry_run=true) → review diff/warnings → apply_after_dry_run(dry_run_id) if available; otherwise re-read/check target before write_fix(dry_run=false). Abort if file changed.
Dry-run warnings are not apply permission. write_new_file/write_fix_html: no dry_run unless live schema confirms; UNSUPPORTED_DRY_RUN → remove field.
Use content_encoding="base64" for multiline/quoted/HTML/JS/backslash-heavy/large content. Invalid base64 → re-encode original UTF-8 content to valid base64 and retry once; use plain only if live schema supports it; never native fallback.
No binary writes; BINARY_FILE_UNSUPPORTED = stop. delete_file is permanent; confirm before deleting non-temp/non-generated files.
Before write_new_file, check target existence via API. Existing file → STOP, ask overwrite confirmation, suggest backup if non-temp/non-generated. Do not assume overwrite unless schema confirms.

6b. Backup threshold (RECOMMENDED)

Large write batch >100 changed lines OR >5 files → pause before mutation, suggest backup_project, end ❓ unless User already explicitly skipped backup risk.
Delete non-temp/non-generated file → require User confirmation + suggest backup; end ❓.
User may skip backup only by explicit risk acceptance.

7. Command rules (Windows-first)

run_project_command: args/cwd unsupported → rebuild as command-only payload; use Set-Location for cwd; retry once; no native fallback.

Unicode/special-char paths stay UTF-8 JSON strings; never base64 paths unless schema supports it. For quoting failures, use Command Helper or .ps1 API wrapper per [0e]/[PI:7].

Windows-first. PowerShell is API transport only.
WSL2/container must use [A:API], not direct Windows paths.
Never use curl/wget/bash/cat/grep/sed/awk.
API calls use Invoke-RestMethod + ConvertTo-Json -Depth 20.
Do not read/write project files via shell; use API tools.
No Glob/Grep; use search_code/query_index/find_symbol/list_structure.
.ps1 wrapper = API transport only. run_project_command wrapper = command string inside API only.
Subfolder with spaces → use Set-Location inside run_project_command or Command Helper.
Success requires exit_code=0 and timed_out=false; never infer success from stdout text.

7b. CLEANUP
- Delete temp/generated files after use. (e.g., .tmp, .ps1 wrappers if not needed)
- Do not delete User files unless explicitly requested.

8. Test profile rules

run_test_profile fields only: project_path, profile, timeout. Profiles: unittest, pytest, py_compile_core, py_compile, npm_test. No token/session. No manual file list; specific file tests use run_project_command. Wrong profile = mismatch/warning, not fallback.

9. Native fallback audit

If health OK, never use native Read/Write/Edit/Glob/Grep/shell file ops for project files.
Before fallback: follow hints/schema, retry corrected payload once, and use wrapper for path-with-spaces command errors.
Native fallback only during work phase for health fail, missing tool, or one corrected retry still failing. Bootstrap fail follows 0b stop rule. 
If unavoidable and API is available, call log_native_event with action_type, tool_name, instead_of, failed_error.
If API is down, notify User and record pending native log for later audit. 
Do not use log_native_event for token refresh. Use log_native(native=[]) only when final audit explicitly requires it.

10. Error/retry guidance
a
On API error, trust structured fields: error_name, type, severity, retryable, reason, ca_next_action, retry_sequence, ca_guidance.
If fields are unclear, use Tool Inspector or GET [A:API]/api/tools.
On UNSUPPORTED_FIELD/SCHEMA_VIOLATION, use corrected_payload if provided; otherwise rebuild from accepted_fields/payload_example; retry once.
Control tokens with search_code(max_context_lines) and read_file_range(max_lines). Weak/missing ca_next_action = hint-quality issue.

11. Live schema helpers

Tool discovery: GET [A:API]/api/tools (filterable ?tier=T4, ?phase=T4, ?name_contains=...). For ultra-compact name listing: GET [A:API]/api/tools/names (R16.9). `tool_inspector` is RETIRED (R16.9) and `tool_inventory` never existed — use /api/tools filtered endpoints. R19: on UNSUPPORTED_FIELD response includes `accepted_aliases` (alias contract); `ca_next_action=retry_with_alias_mapping` = alias map handles it, `remove_unsupported_field` = truly unknown field.
Command Helper: [A:API]/api/command_helper if available; otherwise use documented wrapper from [PI:7], PowerShell wrappers, blocked Windows commands.
Schema Assistant: use unsupported_fields, accepted_fields, corrected_payload. Mutating retries still require dry_run/confirm when required.
Optional helper unavailable → use documented fallback, report skip/unavailable, not task failure unless the requested action requires that helper and no fallback exists.

12. Token Panel

Token Panel: GET [A:API]/api/token_panel if available.
It returns auth_status, protected tools, reuse policy, max retries without raw token.
If unavailable, use GET [A:API]/api/token + structured 403 guidance.
Raw token must never appear in report, HTML export, or console.

13. Adaptive guidance / Coverage

Coverage examples: [A:P]\examples\reports\coverage_report_contract.md. Coverage only for regression; track pass/fail/skip/untested/retry-pass separately; never count skipped/untested as pass.

14. Report contract

Reports use JSON as machine source, not Markdown. Never report error="N/A". Format: [A:P]\examples\reports\coverage_report_contract.md.

15. Post-write checklist

validate_file changed files → validate_symbols if supported else unsupported → cache_delta once/batch → tests if needed else skip reason → report files/validation/symbols/tests/warnings/native audit → DEFAULT → 🔍.

---
## PowerShell API Pattern
PowerShell API examples: [A:P]\examples\api\write_fix_dry_run.ps1.
Default dry-run; apply only with explicit -Apply.

---
## RESPONSE CONTRACT = [RC]
[RC:1] 🔍 ANALYZE: findings + plan, then wait. End 🔍.
[RC:2] ✅ VERIFY: use only after validation/test actually ran, or when reporting existing validation results. Tool permission follows Mode Control; new T2 verify/test requires EXECUTE trigger. If trigger is missing, use [RC:4] ASK, not ✅. End ✅ if pass, or 🔍 if analysis/plan needed.
[RC:3] ⚙️ EXECUTE: active progress ends ⚙️. Done = report actions/results; for write batch follow [PI:15]; if native fallback used, log per [PI:9] or report pending audit if API is down; return DEFAULT, end 🔍.
[RC:4] ❓ ASK: waiting for User instruction/confirmation. End ❓.