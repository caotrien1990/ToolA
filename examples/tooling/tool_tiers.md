DEFAULT = T1 only. EXECUTE = T1 as needed + T2/T3 only for the triggered action.

T1 = Read/Analyze, no token: open_session, cache_status, get_project_summary, query_index, find_symbol, search_code, read_file_range, get_file_symbols, list_structure, workflow_status, next_recommended_action, capability_status, suggest_missing_capabilities, get_folder_size, get_usage_stats, list_sessions, get_session_events, resolve_project_path, protected_tools_status, path_quote_helper, optional_tool_probe.

T2 = Mutate/Verify/Refresh/Run: backup_status, backup_project, write_fix, write_fix_html, write_new_file, delete_file, validate_file, validate_symbols, validate_encoding, post_write_batch_verify, apply_after_dry_run, cache_delta, refresh_project_map, generate_project_map, run_project_command, run_test_profile, create_benchmark_request, create_build_context, set_workflow_mode.

T3 = Session/Audit/Aux: prepare_ca_session, close_session, rename_session, log_native, log_native_event, create_pending_audit, sync_pending_audit.

Token is required only for protected tools per live schema; check Token Panel / Tool Inspector / 403 guidance when unsure.
Not tier label; see [PI:4].
DEFAULT must not use T2 verify/test/run tools. VERIFY is response style; T2 verify/test still needs EXECUTE trigger unless covered by post-write validation in [0d].

