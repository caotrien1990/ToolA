# ALL TOOL TEST — 201 tools

## Summary
OK: 69 | ERROR: 128 | TIMEOUT: 4 | SKIP: 0

## All Results
| # | Tool | Status | Detail |
|---|------|--------|--------|
| 1 | list_structure | ✅ OK | 0.46s |
| 2 | find_symbol | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 3 | read_file_range | ✅ OK | 0.29s |
| 4 | search_code | ❌ ERROR | MISSING_REQUIRED_FIELD |
| 5 | get_file_symbols | ✅ OK | 0.21s |
| 6 | write_fix | ❌ ERROR | IMPORT_ERROR_PRE_WRITE |
| 7 | write_new_file | ❌ ERROR | FILE_ALREADY_EXISTS |
| 8 | generate_project_map | ❌ ERROR | TOOL_ERROR |
| 9 | refresh_project_map | ❌ ERROR | TOOL_ERROR |
| 10 | cache_status | ✅ OK | 1.12s |
| 11 | delete_file | ❌ ERROR | HTTPError: HTTP Error 403: Forbidden |
| 12 | get_rule_core_text | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 13 | audit_tool_matrix | ✅ OK | 2.24s |
| 14 | prepare_ca_session | ✅ OK | 5.48s |
| 15 | set_workflow_mode | ✅ OK | 0.57s |
| 16 | workflow_status_registry | ✅ OK | 1.20s |
| 17 | next_recommended_action | ✅ OK | 0.67s |
| 18 | capability_status | ✅ OK | 0.72s |
| 19 | suggest_missing_capabilities | ✅ OK | 0.40s |
| 20 | backup_status | ✅ OK | 0.26s |
| 21 | resolve_project_path | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 22 | protected_tools_status | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 23 | path_quote_helper | ✅ OK | 0.20s |
| 24 | optional_tool_probe | ❌ ERROR | EMPTY_TOOL_LIST |
| 25 | backup_project | ⏱ TIMEOUT | 30.00s |
| 26 | cache_delta | ✅ OK | 1.81s |
| 27 | run_project_command | ❌ ERROR | HTTPError: HTTP Error 403: Forbidden |
| 28 | kill_process | ❌ ERROR | HTTPError: HTTP Error 403: Forbidden |
| 29 | run_test_profile | ⏱ TIMEOUT | 30.00s |
| 30 | create_benchmark_request | ✅ OK | 1.38s |
| 31 | create_build_context | ✅ OK | 1.09s |
| 32 | get_project_summary | ⏱ TIMEOUT | 30.01s |
| 33 | query_index | ❌ ERROR | MISSING_REQUIRED_FIELD |
| 34 | get_usage_stats | ✅ OK | 0.87s |
| 35 | get_folder_size | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 36 | validate_file | ✅ OK | 1.07s |
| 37 | validate_symbols | ✅ OK | 2.30s |
| 38 | validate_encoding | ✅ OK | 0.28s |
| 39 | post_write_batch_verify | ❌ ERROR | EMPTY_CHANGED_FILES |
| 40 | write_fix_html | ❌ ERROR | IMPORT_ERROR_PRE_WRITE |
| 41 | apply_after_dry_run | ❌ ERROR | DRY_RUN_NOT_FOUND |
| 42 | open_session | ✅ OK | 1.04s |
| 43 | close_session | ✅ OK | 0.40s |
| 44 | rename_session | ❌ ERROR | HTTPError: HTTP Error 409: Conflict |
| 45 | log_native | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 46 | log_native_event | ✅ OK | 0.64s |
| 47 | create_pending_audit | ✅ OK | 0.33s |
| 48 | sync_pending_audit | ✅ OK | 0.31s |
| 49 | list_sessions | ✅ OK | 0.18s |
| 50 | get_session_events | ✅ OK | 0.45s |
| 51 | validate | ✅ OK | 0.37s |
| 52 | project_map | ✅ OK | 2.49s |
| 53 | state | ✅ OK | 3.92s |
| 54 | council_tracker | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 55 | mission_create | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 56 | mission_next | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 57 | mission_dispatch | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 58 | mission_report | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 59 | mission_status | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 60 | mission_gate | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 61 | leader_bootstrap | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 62 | council_config_diff | ✅ OK | 0.61s |
| 63 | council_config_sync | ✅ OK | 1.29s |
| 64 | regex_search | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 65 | find_callers | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 66 | find_definition_and_usage | ✅ OK | 1.69s |
| 67 | import_graph | ✅ OK | 2.28s |
| 68 | ast_query | ✅ OK | 23.76s |
| 69 | git_blame_symbol | ❌ ERROR | SYMBOL_NOT_FOUND |
| 70 | write_transaction | ❌ ERROR | HTTPError: HTTP Error 403: Forbidden |
| 71 | replace_between_anchors | ❌ ERROR | SYNTAX_ERROR_PRE_WRITE |
| 72 | session_audit_summary | ✅ OK | 0.39s |
| 73 | tool_decision_hint | ❌ ERROR | MISSING_REQUIRED_FIELD |
| 74 | progressive_matrix_check | ✅ OK | 0.23s |
| 75 | mcp_surface_diff | ✅ OK | 5.52s |
| 76 | convene_council | ✅ OK | 0.89s |
| 77 | open_browser_page | ❌ ERROR | BROWSER_LAUNCH_FAILED |
| 78 | read_page | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 79 | query_selector | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 80 | click | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 81 | fill | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 82 | evaluate_js | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 83 | read_console_errors | ❌ ERROR | PAGE_NOT_FOUND |
| 84 | read_network_errors | ❌ ERROR | PAGE_NOT_FOUND |
| 85 | screenshot_page | ❌ ERROR | PAGE_NOT_FOUND |
| 86 | wait_for_selector | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 87 | compare_dom_snapshot | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 88 | server_lifecycle_status | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 89 | port_owner_inspect | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 90 | detect_stale_server | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 91 | static_asset_cache_check | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 92 | bundle_version_check | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 93 | restart_server_via_bat_hint | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 94 | restart_orchestrator_via_task | ❌ ERROR | HTTPError: HTTP Error 403: Forbidden |
| 95 | cancel_pending_restart | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 96 | check_window_visibility | ❌ ERROR | WINDOW_NOT_FOUND |
| 97 | cdp_attach_target | ❌ ERROR | MISSING_REQUIRED_FIELD |
| 98 | inspect_project_runtime | ✅ OK | 6.09s |
| 99 | launch_electron_with_cdp | ❌ ERROR | ELECTRON_LAUNCHER_NOT_FOUND |
| 100 | dom_state_diff | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 101 | extract_log_section | ❌ ERROR | PAGE_NOT_FOUND |
| 102 | fill_value | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 103 | read_value | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 104 | read_all_logs | ❌ ERROR | PAGE_NOT_FOUND |
| 105 | run_script | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 106 | manage_agent | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 107 | query_dom_candidates | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 108 | describe_page | ❌ ERROR | PAGE_NOT_FOUND |
| 109 | cleanup_test_session | ✅ OK | 1.38s |
| 110 | click_selector | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 111 | read_skill | ❌ ERROR | SKILL_NOT_FOUND |
| 112 | checkpoint_loop_dom_state | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 113 | ensure_single_electron_instance | ❌ ERROR | HTTPError: HTTP Error 409: Conflict |
| 114 | click_and_checkpoint_loop | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 115 | ui_state_summary | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 116 | alias_contract_test | ✅ OK | 0.06s |
| 117 | architecture_audit_compact | ✅ OK | 1.37s |
| 118 | acceptance_dashboard | ✅ OK | 1.63s |
| 119 | folder_exists | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 120 | create_folder | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 121 | create_folders | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 122 | folder_stats | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 123 | folder_diff | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 124 | fill_folder_dialog | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 125 | folder_dialog_snapshot | ✅ OK | 0.08s |
| 126 | cancel_native_dialog | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 127 | workflow_list | ✅ OK | 0.06s |
| 128 | workflow_start | ❌ ERROR | WORKFLOW_NOT_FOUND |
| 129 | workflow_get_current | ❌ ERROR | WORKFLOW_NOT_FOUND |
| 130 | workflow_submit_step | ❌ ERROR | WORKFLOW_NOT_FOUND |
| 131 | workflow_retry_step | ❌ ERROR | WORKFLOW_NOT_FOUND |
| 132 | workflow_skip_step | ❌ ERROR | WORKFLOW_NOT_FOUND |
| 133 | workflow_reset | ❌ ERROR | WORKFLOW_NOT_FOUND |
| 134 | workflow_status | ❌ ERROR | WORKFLOW_NOT_FOUND |
| 135 | workflow_create_from_ui | ❌ ERROR | MISSING_REQUIRED_FIELD |
| 136 | workflow_save_md | ❌ ERROR | MISSING_REQUIRED_FIELD |
| 137 | workflow_read_md | ❌ ERROR | WORKFLOW_NOT_FOUND |
| 138 | workflow_draft_create | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 139 | workflow_draft_get | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 140 | workflow_activate | ❌ ERROR | WORKFLOW_NOT_FOUND |
| 141 | workflow_deactivate | ✅ OK | 0.04s |
| 142 | workflow_active_get | ✅ OK | 0.04s |
| 143 | classify_get_footage_outcome | ✅ OK | 0.08s |
| 144 | verify_active_electron_source | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 145 | bootstrap_session | ✅ OK | 0.06s |
| 146 | run_project_test_script | ❌ ERROR | HTTPError: HTTP Error 403: Forbidden |
| 147 | check_server_path | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 148 | webfetch | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 149 | fetch_sheet_csv_summary | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 150 | sample_unc_paths_with_root_check | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 151 | verify_get_footage_copy_completion | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 152 | run_ps1_script | ❌ ERROR | HTTPError: HTTP Error 403: Forbidden |
| 153 | run_node_script | ❌ ERROR | HTTPError: HTTP Error 403: Forbidden |
| 154 | workflow_checkpoint | ✅ OK | 0.06s |
| 155 | workflow_goto_step | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 156 | wait_for_idle | ❌ ERROR | MISSING_REQUIRED_FIELD |
| 157 | parse_log_section | ✅ OK | 0.04s |
| 158 | assert_target_idle | ✅ OK | 0.05s |
| 159 | click_and_handle_folder_dialog | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 160 | inspect_project_usage | ✅ OK | 2.97s |
| 161 | invoke_app_function | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 162 | declare_mission | ❌ ERROR | MISSING_REQUIRED_FIELD |
| 163 | save_log_snapshot | ❌ ERROR | HTTPError: HTTP Error 409: Conflict |
| 164 | desktop_window_list | ✅ OK | 0.04s |
| 165 | desktop_app_focus | ❌ ERROR | MISSING_REQUIRED_FIELD |
| 166 | desktop_click | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 167 | desktop_window_state | ❌ ERROR | UNSUPPORTED_FIELD |
| 168 | desktop_screenshot | ✅ OK | 0.81s |
| 169 | desktop_send_text | ❌ ERROR | HTTPError: HTTP Error 409: Conflict |
| 170 | electron_devtools_log | ❌ ERROR | CDP_UNREACHABLE |
| 171 | list_cdp_targets | ❌ ERROR | CDP_UNREACHABLE |
| 172 | process_tree_inspect | ❌ ERROR | MISSING_REQUIRED_FIELD |
| 173 | run_playwright_test | ✅ OK | 0.13s |
| 174 | record_ui_flow | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 175 | assert_text_visible | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 176 | assert_button_click_changes_state | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 177 | assert_console_no_error | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 178 | get_token_meter | ✅ OK | 0.04s |
| 179 | get_skill_state | ✅ OK | 0.05s |
| 180 | list_projects | ✅ OK | 0.06s |
| 181 | get_validation_queue | ✅ OK | 0.04s |
| 182 | cep_manifest_validate | ❌ ERROR | MANIFEST_NOT_FOUND |
| 183 | cep_extension_status | ✅ OK | 1.35s |
| 184 | copy_file | ❌ ERROR | HTTPError: HTTP Error 409: Conflict |
| 185 | cep_deploy | ❌ ERROR | HTTPError: HTTP Error 403: Forbidden |
| 186 | rule_drift_check | ✅ OK | 0.05s |
| 187 | cbm_index | ✅ OK | 1.22s |
| 188 | cbm_graph_status | ✅ OK | 0.49s |
| 189 | cbm_trace_path | ❌ ERROR | SCHEMA_VIOLATION |
| 190 | cbm_template_query | ✅ OK | 0.12s |
| 191 | cbm_get_architecture | ✅ OK | 0.28s |
| 192 | cbm_dead_code | ⏱ TIMEOUT | 30.02s |
| 193 | cbm_incremental_index | ✅ OK | 0.62s |
| 194 | cbm_council_context | ✅ OK | 0.21s |
| 195 | council_message | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 196 | council_worker_model | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 197 | restart_orchestrator_via_bat | ❌ ERROR | HTTPError: HTTP Error 403: Forbidden |
| 198 | memory_query | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 199 | memory_recall_cluster | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 200 | memory_capture | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
| 201 | memory_ingest_path | ❌ ERROR | HTTPError: HTTP Error 400: Bad Request |
