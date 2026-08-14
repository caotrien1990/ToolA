# Project-Orchestrator MCP 175-Tool Linked Test Workflow

Purpose: give a low-cost agent one deterministic workflow to test all active `project-orchestrator` MCP tools without confusing expected precondition failures with backend regressions.

Hard rule for the tester:
- Use MCP only: `get_rule_core_text` once, then `open_session`, `tool_names`, `tool_schema`, `call_tool`.
- For every tool step: call `tool_schema(name)` immediately before `call_tool(name,payload)`.
- Never count a missing required field as a tool failure. Build payload from schema.
- Record every result as `PASS`, `EXPECTED_FAIL`, or `BUG`.
- `BUG` means crash, transport close, malformed/unstructured error, wrong schema, or a result contradicting the expected class below.

## Shared Context

Use these variables through the whole workflow:

```text
P_ROOT = %TEMP%\po_mcp_175_case
P_SRC = %TEMP%\po_mcp_175_case\src
P_UI = http://localhost:8083
P_FILE = src\sample.py
P_HTML = index.html
P_COPY = src\sample_copy.py
P_FOLDER = %TEMP%\po_mcp_175_case\created_folder
WF_NAME = mcp_175_smoke
WF_DRAFT = captured from workflow_draft_create
PAGE_ID = captured from open_browser_page(P_UI)
CDP_PAGE_ID = captured from cdp_attach_target when Chrome CDP is available
SESSION_ID = captured from open_session
```

Prepare fixtures before step 001:

```text
Create P_ROOT and P_SRC.
Create P_FILE with:
def sample():
    return {"ok": True}

async def async_sample():
    return 1

try:
    pass
except:
    pass

Create P_HTML with:
<!doctype html><html><body>
<h1 id="title">MCP 175 Fixture</h1>
<input id="input" value="">
<button id="btn">Click</button>
<script>
window.__clicked = 0;
document.getElementById('btn').addEventListener('click',()=>{window.__clicked += 1; document.body.dataset.clicked=String(window.__clicked);});
</script>
</body></html>
```

Core setup sequence:

```text
S00 get_rule_core_text -> PASS ok=true.
S00 open_session(project_path=P_ROOT, session_title="MCP 175 linked test", mode="EXECUTE") -> save SESSION_ID.
S00 generate_project_map(project_path=P_ROOT) -> PASS.
S00 open_browser_page(url=P_UI, project_path=P_ROOT, headless=true) -> save PAGE_ID.
S00 optional: launch Chrome with --remote-debugging-port=9223 to P_UI, list_cdp_targets, cdp_attach_target -> save CDP_PAGE_ID.
```

Result classes:

```text
PASS = ok=true, or structured expected negative with documented error_name.
EXPECTED_FAIL = environment/precondition limitation: CEP missing, CDP absent, invalid profile, missing external browser/window, deprecated/preview.
BUG = transport closed, timeout without structured error, Python traceback leaking as unclassified error, schema lacks required guidance, or state from earlier step unusable.
```

## Payload Profiles

Use schema first, then these profiles:

```text
R0 = {}
RP = {"project_path": P_ROOT}
RF = {"project_path": P_ROOT, "file_path": P_FILE}
RR = {"project_path": P_ROOT, "query": "sample", "file_pattern": "*.py", "max_results": 5}
RSYM = {"project_path": P_ROOT, "name": "sample", "context_lines": 2}
RSYM2 = {"project_path": P_ROOT, "symbol": "sample"}
RUNC = {"paths": ["\\\\localhost\\C$\\Windows"], "sample_count": 1, "root_check": false, "timeout_sec": 1}
RW = {"project_path": P_ROOT, "name": WF_NAME}
RWF = {"project_path": P_ROOT, "name": WF_NAME, "body_md": "# MCP 175\n\n- [ ] Step 1\n- [ ] Step 2\n"}
RB = {"page_id": PAGE_ID}
RBS = {"page_id": PAGE_ID, "selector": "body"}
RBTN = {"page_id": PAGE_ID, "selector": "#btn"}
RJS = {"page_id": PAGE_ID, "expression": "(()=>({title:document.title,url:location.href}))()"}
RCDP = {"cdp_port": 9223}
RPROC = {"port": 8083}
```

## 175 Linked Steps

Each step format: `step tool | profile | expected`.

### T1 Read and Discovery

001 acceptance_dashboard | RP | PASS or structured no-data.
002 alias_contract_test | RP | PASS.
003 architecture_audit_compact | RP | PASS.
004 assert_target_idle | RP | PASS or EXPECTED_FAIL if no tracked target.
005 ast_query | {"project_path":P_ROOT,"pattern":"async_function","file_pattern":"*.py","max_results":5} | PASS.
006 bootstrap_session | RP | PASS.
007 cache_status | RP | PASS.
008 capability_status | RP | PASS.
009 cep_extension_status | RP | PASS or EXPECTED_FAIL no CEP.
010 cep_manifest_validate | RP | EXPECTED_FAIL MANIFEST_NOT_FOUND unless CEP fixture exists.
011 check_server_path | RP | PASS.
012 classify_get_footage_outcome | RP | PASS or EXPECTED_FAIL no Rawcut fixture.
013 declare_mission | RP plus mission text from schema | PASS.
014 describe_page | RB | PASS after PAGE_ID.
015 dom_state_diff | RBS | PASS snapshot_id.
016 extract_log_section | RB plus selector/log args from schema | PASS or EXPECTED_FAIL no matching log section.
017 fetch_sheet_csv_summary | use harmless public CSV or EXPECTED_FAIL network unavailable.
018 find_callers | RSYM2 | PASS.
019 find_definition_and_usage | RSYM2 | PASS.
020 find_symbol | RSYM | PASS.
021 folder_dialog_snapshot | R0 | EXPECTED_FAIL if no native dialog.
022 folder_diff | paths P_ROOT and P_ROOT | PASS.
023 folder_exists | {"path":P_ROOT} | PASS exists=true.
024 folder_stats | {"path":P_ROOT,"recursive":true} | PASS.
025 get_file_symbols | RF | PASS.
026 get_folder_size | {"folder_path":P_ROOT} | PASS.
027 get_project_summary | RP | PASS after generate_project_map.
028 get_rule_core_text | R0 | PASS.
029 get_session_events | {"project_path":P_ROOT,"limit":20} | PASS.
030 get_skill_state | RP | PASS or structured no-state.
031 get_token_meter | RP | PASS.
032 get_usage_stats | RP | PASS.
033 get_validation_queue | RP | PASS.
034 git_blame_symbol | RSYM2 | PASS if git repo, else EXPECTED_FAIL structured git/precondition.
035 import_graph | RP | PASS.
036 inspect_project_runtime | RP | PASS.
037 inspect_project_usage | RP | PASS.
038 list_projects | R0 | PASS.
039 list_sessions | RP | PASS.
040 list_structure | {"path":P_ROOT,"depth":2,"project_path":P_ROOT} | PASS.
041 mcp_surface_diff | R0 | PASS.
042 next_recommended_action | RP | PASS.
043 open_session | RP plus title/mode | PASS save SESSION_ID.
044 optional_tool_probe | {"tools":["cache_status","query_index","read_page"]} | PASS.
045 parse_log_section | RP plus known section or EXPECTED_FAIL no section.
046 path_quote_helper | {"path":P_ROOT} | PASS.
047 progressive_matrix_check | R0 | PASS 175 active/no drift.
048 protected_tools_status | R0 | PASS.
049 query_dom_candidates | {"page_id":PAGE_ID,"filter":"buttons","max_items":20} | PASS.
050 query_index | {"project_path":P_ROOT,"group":"source","max_results":10} | PASS.
051 read_all_logs | RB or RP per schema | PASS or structured empty.
052 read_file_range | {"project_path":P_ROOT,"file_path":P_FILE,"start_line":1,"end_line":8} | PASS.
053 read_skill | use a known skill from schema/list | PASS.
054 read_value | {"page_id":PAGE_ID,"selector":"#input"} | PASS.
055 regex_search | RR | PASS.
056 resolve_project_path | {"project_path":P_ROOT,"file_path":P_FILE} | PASS.
057 sample_unc_paths_with_root_check | RUNC | PASS or EXPECTED_FAIL auth/reachability structured.
058 search_code | RR | PASS.
059 semantic_search | RP plus query | EXPECTED_FAIL preview/not implemented.
060 state | RP | PASS.
061 suggest_missing_capabilities | RP | PASS.
062 tool_decision_hint | {"task_description":"test MCP tools","scope_hint":P_ROOT} | PASS.
063 type_search | RP plus signature | EXPECTED_FAIL preview/not implemented.
064 ui_state_summary | RB | PASS.
065 verify_active_electron_source | RP | EXPECTED_FAIL if no Electron target.
066 verify_get_footage_copy_completion | RP | EXPECTED_FAIL no Rawcut fixture.
067 wait_for_idle | RP | PASS or structured no target.
068 webfetch | {"url":"http://localhost:8083/health","format":"json","max_chars":2000} | PASS.
069 workflow_active_get | RP | PASS.
070 workflow_draft_get | use WF_DRAFT from step 124 | PASS after draft exists; run later if needed.
071 workflow_get_current | RP | PASS or structured no active workflow.
072 workflow_list | RP | PASS.
073 workflow_read_md | RW | PASS after workflow saved; EXPECTED_FAIL before.
074 workflow_status | RW | PASS after workflow start; EXPECTED_FAIL before.
075 workflow_status_registry | RP | PASS.

### T2 Write and Verify

076 backup_status | RP | PASS.
077 backup_project | RP plus force=true | PASS.
078 create_folder | {"path":P_FOLDER} | PASS.
079 create_folders | {"paths":[P_ROOT+"\\a",P_ROOT+"\\b"]} | PASS.
080 generate_project_map | RP | PASS.
081 project_map | RP | PASS.
082 cache_delta | RP | PASS.
083 query_index | RP plus file_pattern="*.py" | PASS confirms index.
084 write_new_file | {"project_path":P_ROOT,"path":"src/new_file.py","new_content":"def new_file():\n    return 2\n","content_encoding":"plain","dry_run":true} | PASS dry_run_id.
085 apply_after_dry_run | use dry_run_id/hash from 084 | PASS.
086 validate_file | {"project_path":P_ROOT,"file_path":"src/new_file.py","lang":"python"} | PASS.
087 validate_symbols | {"project_path":P_ROOT,"file_path":"src/new_file.py"} | PASS or stale-index warning structured.
088 validate_encoding | {"project_path":P_ROOT,"file_path":"src/new_file.py"} | PASS.
089 write_fix | replace one safe line in src/new_file.py with dry_run=true | PASS dry_run.
090 apply_after_dry_run | use 089 dry_run | PASS.
091 write_fix_html | edit P_HTML dry_run=true | PASS.
092 apply_after_dry_run | use 091 dry_run | PASS.
093 replace_between_anchors | safe anchors in P_HTML dry_run=true | PASS.
094 write_transaction | two harmless dry_run ops | PASS.
095 copy_file | copy P_FILE to P_COPY | PASS.
096 post_write_batch_verify | changed files new_file.py/P_COPY/P_HTML | PASS.
097 refresh_project_map | RP | PASS.
098 run_project_test_script | powershell or node tiny script in P_ROOT | PASS.
099 run_node_script | tiny JS returns JSON | PASS.
100 run_ps1_script | tiny PS returns text | PASS.
101 run_project_command | simple read-only command in P_ROOT | PASS.
102 run_test_profile | profile=py_compile or available enum | PASS or structured invalid if fixture lacks profile.
103 validate | RF | PASS.
104 fill_value | use PAGE_ID + #input + value | PASS.
105 save_log_snapshot | RB/RP per schema | PASS.
106 set_workflow_mode | {"project_path":P_ROOT,"mode":"EXECUTE"} | PASS.
107 create_benchmark_request | RP plus idea | PASS.
108 create_build_context | RP plus idea/approved_inputs | PASS.
109 cep_deploy | RP | EXPECTED_FAIL MANIFEST_NOT_FOUND unless CEP fixture.
110 kill_process | use impossible PID | EXPECTED_FAIL PROCESS_KILL_FAILED structured; do not kill real process.
111 cleanup_test_session | use PAGE_ID/process list only if created by test | PASS or EXPECTED_FAIL no cleanup targets.
112 delete_file | delete P_COPY with backup_override=true if needed | PASS.

### T3 Session and Workflow

113 audit_tool_matrix | R0 | PASS no drift.
114 manage_agent | {"action":"list"} | PASS; do not sync in free-agent test.
115 prepare_ca_session | RP plus idea | PASS.
116 create_pending_audit | RP plus harmless target/reason | PASS.
117 sync_pending_audit | RP | PASS.
118 log_native_event | RP plus action_type/tool_name/target/status | PASS.
119 log_native | {"native":[]} | PASS.
120 rename_session | RP plus new_title | PASS.
121 session_audit_summary | RP | PASS.
122 workflow_draft_create | RWF | PASS save WF_DRAFT.
123 workflow_draft_get | {"draft_token":WF_DRAFT} | PASS.
124 workflow_save_md | RW plus body_md | PASS.
125 workflow_read_md | RW | PASS.
126 workflow_create_from_ui | RW or body per schema | PASS.
127 workflow_start | RW | PASS.
128 workflow_status | RW | PASS.
129 workflow_get_current | RP | PASS.
130 workflow_active_get | RP | PASS.
131 workflow_checkpoint | RW plus step/status | PASS.
132 workflow_submit_step | RW plus step result | PASS.
133 workflow_goto_step | RW plus target_step=1 | PASS.
134 workflow_retry_step | RW plus target_step=1 | PASS.
135 workflow_skip_step | RW plus target_step=2/reason | PASS.
136 workflow_deactivate | RP | PASS.
137 workflow_activate | RW | PASS.
138 workflow_reset | RW | PASS.
139 close_session | RP | PASS only at end of this workflow; after this reopen if continuing.
140 open_session | RP plus title="MCP 175 post-close verify" | PASS transport must remain alive.

### T4 Runtime, Browser, Desktop, Server

141 server_lifecycle_status | R0 | PASS.
142 detect_stale_server | R0 | PASS.
143 port_owner_inspect | {"port":8083} | PASS.
144 process_tree_inspect | use PID from port_owner_inspect | PASS.
145 static_asset_cache_check | RP or URL per schema | PASS.
146 bundle_version_check | {"cdp_port":9223} | PASS if CDP open, else EXPECTED_FAIL VERSION_CHECK_FAILED.
147 desktop_window_list | R0 | PASS.
148 desktop_screenshot | R0 | PASS or EXPECTED_FAIL if desktop capture unavailable.
149 check_window_visibility | title_pattern="Tool [A]" | PASS if window visible, else structured WINDOW_NOT_FOUND.
150 desktop_app_focus | title_pattern="Tool [A]" | PASS or EXPECTED_FAIL UIPI/window-state.
151 desktop_window_state | title_pattern="Tool [A]", state="normal" | PASS or EXPECTED_FAIL invalid/no window.
152 desktop_click | invalid x string test | PASS as structured WRONG_ARGUMENTS.
153 open_browser_page | P_UI | PASS save PAGE_ID2.
154 read_page | {"page_id":PAGE_ID2,"selector":"body"} | PASS.
155 query_selector | {"page_id":PAGE_ID2,"selector":"#btn-set-folder"} | PASS.
156 wait_for_selector | {"page_id":PAGE_ID2,"selector":"#btn-set-folder","timeout_ms":5000} | PASS.
157 query_dom_candidates | PAGE_ID2 buttons | PASS.
158 dom_state_diff | PAGE_ID2 body baseline | PASS snapshot_id.
159 evaluate_js | PAGE_ID2 RJS | PASS.
160 run_script | PAGE_ID2 harmless JS | PASS.
161 read_console_errors | PAGE_ID2 | PASS possibly empty.
162 read_network_errors | PAGE_ID2 | PASS possibly empty.
163 assert_console_no_error | PAGE_ID2 | PASS or structured console findings.
164 assert_text_visible | PAGE_ID2 text="Project Orchestrator" | PASS.
165 screenshot_page | PAGE_ID2 | PASS artifact path.
166 compare_dom_snapshot | use two snapshot IDs from dom_state_diff/checkpoint | PASS.
167 checkpoint_loop_dom_state | PAGE_ID2 selector body | PASS.
168 click_selector | PAGE_ID2 selector "#theme-toggle" | PASS.
169 click | PAGE_ID2 coords from query_selector bbox | PASS.
170 click_and_checkpoint_loop | PAGE_ID2 selector/state target | PASS or structured no-change.
171 assert_button_click_changes_state | PAGE_ID2 target "#theme-toggle" | PASS or structured no-change.
172 fill | PAGE_ID2 selector "#input" value "abc" | PASS.
173 fill_folder_dialog | EXPECTED_FAIL unless native folder dialog open.
174 click_and_handle_folder_dialog | EXPECTED_FAIL unless fixture opens native dialog.
175 invoke_app_function | EXPECTED_FAIL unless page exposes target function; otherwise call harmless function.

## Optional CDP Branch

Run after step 153 if Chrome CDP is available:

```text
A. list_cdp_targets(cdp_port=9223) -> PASS if target list includes P_UI.
B. cdp_attach_target(target_id=<P_UI target>, cdp_port=9223, project_path=P_ROOT) -> save CDP_PAGE_ID.
C. read_page(CDP_PAGE_ID, body) -> PASS.
D. query_selector(CDP_PAGE_ID, #btn-set-folder) -> PASS.
```

Do not mark CDP branch as BUG when port 9223 is closed. That is `EXPECTED_FAIL` unless this workflow explicitly launched Chrome with remote debugging.

## Final Report Format

The tester must return this summary:

```text
MCP 175 Result
- Total steps: 175
- PASS:
- EXPECTED_FAIL:
- BUG:
- Transport closed count:
- Unstructured error count:
- First failing step:
- Repro payload for every BUG:
- Whether close_session kept MCP alive:
- Whether open_browser_page/read_page kept PAGE_ID alive:
- Whether cdp_attach_target/read_page kept CDP_PAGE_ID alive:
```

For each BUG include:

```json
{
  "step": 0,
  "tool": "",
  "payload": {},
  "error_name": "",
  "raw_result_excerpt": "",
  "expected": "",
  "actual": "",
  "retry_done": true
}
```
