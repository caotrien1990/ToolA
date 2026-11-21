"""Registry data literals for code_tools.



This module is intentionally independent from code_tools to avoid circular imports.

It stores the registry data and light builders used by the legacy code_tools shims.

"""

from __future__ import annotations



from copy import deepcopy

from typing import Any

# A34 P2c: the enum and the bound for the `pipeline` parameter are DERIVED from the
# active-chain leaf, never retyped here. active_chain imports only os/time/typing and
# orchestrator/core/__init__.py is empty, so this stays inside the "independent from
# code_tools" promise in the module docstring -- no cycle is introduced.
from orchestrator.core.active_chain import ACTIVE_CHAIN_STEPS, MAX_STEPS_PER_CALL



T1_TOOLS = [

    # V15 R13: get_rule_core_text kept for backward compat (existing UI "Get Core Rule" button

    # still uses it), but the primary "Copy Agent Prompt" button now returns a MINIMAL

    # session prompt via prepare_ca_session — CORE_RULE.txt is the agent's GLOBAL rule

    # so pasting both produces no duplicated content.

    "get_rule_core_text",

    "open_session", "resolve_project_path", "protected_tools_status",

    "path_quote_helper", "optional_tool_probe", "cache_status",

    "search_code", "find_symbol", "regex_search", "find_callers",

    "read_file_range", "read_context_batch", "architecture_context_pack", "get_file_symbols",

    "list_structure",

    "workflow_status", "workflow_status_registry", "next_recommended_action", "capability_status",

    "suggest_missing_capabilities", "get_folder_size", "get_usage_stats",

    "list_sessions", "get_session_events",

    # R81 CEP deploy tooling (read-only)

    "cep_manifest_validate", "cep_extension_status", "convene_council",

    "council_config_diff", "council_tracker", "council_route_scorecard",

    "mission_next", "mission_status", "leader_bootstrap",

    "t4_host_status",

]

T2_TOOLS = [

    "backup_status", "backup_project", "write_fix", "write_fix_html",

    "write_new_file", "write_draft_patch", "delete_file", "replace_between_anchors",

    "validate_file", "validate_symbols",

    "validate_encoding", "post_write_batch_verify", "cache_delta", "refresh_project_map", "generate_project_map",

    "run_project_command", "kill_process", "run_test_profile", "create_benchmark_request",

    "create_build_context", "set_workflow_mode", "apply_after_dry_run",

    "create_folder", "create_folders",

    # R81 CEP deploy tooling (mutating: writes files / mirrors to LIVE)

    "copy_file", "cep_deploy",

    # Council SOT: writes derived rule docs from council_config.json

    "council_config_sync",

]

T3_TOOLS = [

    "prepare_ca_session", "close_session", "rename_session",

    "log_native", "log_native_event", "create_pending_audit", "sync_pending_audit",
    "record_error_resolution",

    "clear_closed_sessions",

    "mission_create", "mission_dispatch", "mission_report", "mission_gate",

    "write_draft_status", "write_draft_abort",

]



BASE_TOOL_SCHEMAS = [

	{

		"type": "function",

		"function": {

			"name": "list_structure",

			"description": "Return the project folder tree without reading file contents. Pass 'project_path' (preferred) or 'path' (legacy alias).",

			"parameters": {

				"type": "object",

				"properties": {

					"path": {"example": 'orchestrator\\core', "type": "string", "default": "", "description": "Legacy alias for project_path"},

					"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "default": "", "description": "Project root (preferred)"},

					"depth": {"example": 2, "type": "integer", "description": "Maximum depth (default 2)", "default": 2},

	 					"save_output": {"example": False, "type": "boolean", "description": "Write the output to structure_output.txt (default false)", "default": False},

	 						"compact": {"example": True, "type": "boolean", "default": False, "description": "R43.1: hard cap depth=2 + summarize folders with >30 files. Saves tokens."},

	 						"verbosity": {"example": 'terse', "type": "string", "enum": ["", "terse", "normal", "full"], "default": "", "description": "R43.1: terse implies compact=true."},

	 						"strict": {"example": False, "type": "boolean", "default": False, "description": "R92: when false, missing path is a successful probe with exists=false; when true, missing path returns ok=false."},

				},

			},
			"returns": {
				'properties': {
					'ca_next_action': {
						'type': 'string',
						'x-dual': True
					},
					'compact_mode': {
						'type': 'boolean'
					},
					'depth': {
						'type': 'integer'
					},
					'error': {
						'type': 'string'
					},
					'error_name': {
						'type': 'string'
					},
					'exists': {
						'type': 'boolean'
					},
					'ok': {
						'type': 'boolean'
					},
					'path': {
						'type': 'string'
					},
					'result': {
						'type': 'string'
					},
					'save_output': {
						'type': 'boolean'
					},
					'truncated_folders': {
						'items': {
							'type': 'string'
						},
						'type': 'array'
					},
					'truncation_hint': {
						'type': 'string'
					}
				},
				'required': [
					'ok',
					'path'
				],
				'type': 'object',
				'x-audit': {
					'status': 'audited',
					'schema_sha256': 'f7769ac090f177b91d107060defc887c6cf59064d77fe5b3b779890631499550',
					'depth_D': 1
				}
			},
		},

	},

	{

		"type": "function",

		"function": {

			"name": "find_symbol",

			"description": "Find where a function/class/variable is defined in the codebase. Returns file:line so you know what range to read next. V15 R8 A.1: include=def|callers|usage|all. V15 R12: name_encoding=plain|base64 for Unicode symbol names via central transport helper.",

			"parameters": {

				"type": "object",

				"properties": {

					"name": {"example": 'build_tool_schemas', "type": "string", "description": "Symbol name (function, class, variable). Aliases: query, symbol, symbol_name, q."},

					"query": {"example": 'build_tool_schemas', "type": "string", "description": "Alias for name."},

					"path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\orchestrator', "type": "string", "description": "Project folder path"},

					"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "default": "", "description": "Preferred over 'path'"},

					"context_lines": {"example": 2, "type": "integer", "default": 0, "description": "Number of context lines around each match"},

					"include": {"description": "Which occurrences to return for the symbol: 'def' (definition only, default), 'callers', 'usage', or 'all'.", "example": 'def', "type": "string", "enum": ["def", "callers", "usage", "all"], "default": "def"},

                "include_impact": {"example": False, "type": "boolean", "default": False, "description": "CAN-053 opt-in: attach bounded CBM impact_scope (callers/dependents) for the resolved symbol"},

					"name_encoding": {"description": "Encoding of the name argument: 'plain' (default) or 'base64' when the symbol name carries characters that JSON transport would corrupt.", "example": 'plain', "type": "string", "enum": ["plain", "base64"], "default": "plain"},

				},

				"required": ["name"],

				"accepted_encodings": ["plain", "base64"],

			},
			"returns": {
				'type': 'object',
				'properties': {
					'ok': {
						'type': 'boolean',
						'x-dual': True
					},
					'error': {
						'x-dual': True,
						'type': 'string'
					},
					'error_name': {
						'x-dual': True,
						'type': 'string'
					},
					'type': {
						'type': 'string'
					},
					'severity': {
						'type': 'string'
					},
					'retryable': {
						'type': 'boolean'
					},
					'missing_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'required_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'accepted_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'payload_example': {
						'x-dual': True,
						'type': 'object'
					},
					'ca_next_action': {
						'x-dual': True,
						'type': 'string'
					},
					'write_performed': {
						'type': 'boolean'
					},
					'applied': {
						'type': 'boolean'
					},
					'path': {
						'type': 'string'
					},
					'hint': {
						'x-dual': True,
						'type': 'string'
					},
					'results': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'file': {
									'type': 'string'
								},
								'line': {
									'type': 'integer'
								},
								'match': {
									'type': 'string'
								},
								'range_id': {
									'type': 'string'
								},
								'body': {
									'type': 'string'
								}
							}
						}
					},
					'count': {
						'type': 'integer'
					},
					'source': {
						'type': 'string'
					},
					'total_count': {
						'type': 'integer'
					},
					'truncated': {
						'type': 'boolean'
					},
					'truncation_reason': {
						'type': 'string'
					},
					'result_cap': {
						'type': 'integer'
					},
					'warning': {
						'type': 'string'
					},
					'category': {
						'type': 'string'
					},
					'drift': {
						'type': 'object',
						'properties': {
							'ok': {
								'type': 'boolean'
							},
							'added': {
								'type': 'array'
							},
							'modified': {
								'type': 'array',
								'items': {
									'type': 'string'
								}
							},
							'deleted': {
								'type': 'array'
							}
						}
					},
					'stale_index': {
						'anyOf': [
							{
								'type': 'boolean'
							},
							{
								'type': 'null'
							}
						]
					},
					'stale_index_reason': {
						'type': 'string'
					},
					'impact_scope': {
						'type': 'object'
					},
					'callers': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'file': {
									'type': 'string'
								},
								'line': {
									'type': 'integer'
								},
								'match': {
									'type': 'string'
								},
								'range_id': {
									'type': 'string'
								}
							}
						}
					},
					'callers_count': {
						'type': 'integer'
					},
					'usages': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'file': {
									'type': 'string'
								},
								'line': {
									'type': 'integer'
								},
								'match': {
									'type': 'string'
								},
								'range_id': {
									'type': 'string'
								}
							}
						}
					},
					'usages_count': {
						'type': 'integer'
					},
					'definition': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'file': {
									'type': 'string'
								},
								'line': {
									'type': 'integer'
								},
								'match': {
									'type': 'string'
								},
								'range_id': {
									'type': 'string'
								},
								'body': {
									'type': 'string'
								}
							}
						}
					},
					'include': {
						'type': 'string'
					},
					'wall_clock_budget_seconds': {
						'type': 'number'
					},
					'bounded': {
						'type': 'boolean'
					},
					'mode': {
						'type': 'string'
					},
					'query': {
						'type': 'string'
					},
					'accepted_narrowing_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						}
					},
					'retry_sequence': {
						'x-dual': True,
						'type': 'array'
					},
					'ca_hint': {
						'x-dual': True,
						'type': 'string'
					},
					'ca_next_tool': {
						'type': 'string',
						'x-dual': True
					},
					'ca_guidance': {
						'x-dual': True,
						'type': 'object'
					},
					'phase': {
						'type': 'string',
						'x-dual': True
					},
					'suggested_alternative_tool': {
						'properties': {
							'reason': {
								'type': 'string'
							},
							'tool': {
								'type': 'string'
							}
						},
						'type': 'object',
						'x-dual': True
					},
					'normalization_applied': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'original_error_name': {
						'type': 'string',
						'x-dual': True
					},
					'path_normalized': {
						'x-dual': True
					},
					'project_path_resolution': {
						'x-dual': True
					},
					'deprecated_fields': {
						'type': 'array',
						'x-dual': True
					},
					'canonical_replacements': {
						'type': 'object',
						'x-dual': True
					},
					'compatibility_applied': {
						'type': 'boolean',
						'x-dual': True
					},
					'removal_version': {
						'type': 'string',
						'x-dual': True
					}
				},
				'required': [
					'ok'
				],
				'x-audit': {
					'status': 'audited',
					'schema_sha256': '2bbd9b0cbb31510a206101432e8087a9f24b01417cd31798fd2feaecef3ed059',
					'depth_D': 3
				}
			},
		},

	},

	{
		"type": "function",
		"function": {
			"name": "read_file_range",
			"description": "Adaptive pure read: range, function, class, section, or file summary. Small files auto-expand; large files remain bounded and return AST-aware next_ranges. On a LARGE or unfamiliar repo do NOT bulk-read to understand it - ask the index first (cbm_graph_status for freshness, cbm_get_architecture for the module graph + entrypoints, cbm_dsm for coupling, cbm_trace_path between two files, find_symbol or search_code mode=symbol for the exact line), then read ONE bounded range. That is the CSLt order: structure from the graph, then the smallest read that answers the question. This is also the ONLY call that seeds the write_fix read_token, so read the exact region before editing it and use force_reseed=true after FILE_CHANGED_OUTSIDE_ORCHESTRATOR; its content_hash is a raw-bytes digest and must NEVER be passed as write_fix.expected_hash.",
			"parameters": {
				"type": "object",
				"properties": {
					"file_path": {"example": 'orchestrator\\core\\registry_data.py', "type": "string", "description": "File path, absolute or relative to project_path."},
					"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "description": "Absolute project root for relative file_path."},
					"start_line": {"example": 100, "type": "integer", "description": "1-based range start or structural anchor. For scope=range must be >= 1.", "default": 1},
					"end_line": {"example": 160, "type": "integer", "description": "Inclusive range end; for scope=range must be >= start_line when supplied. Equal bounds read one line; omit for default bounded reading. Reversed bounds return INVALID_RANGE without seeding a token; correct the bounds, not force_reseed."},
					"max_lines": {"example": 200, "type": "integer", "default": 200, "description": "Maximum returned lines, backend-capped."},
					"force_reseed": {"example": False, "type": "boolean", "default": False, "description": "Force read-token reseed on unstable read; audited."},
					"scope": {"example": 'range', "type": "string", "enum": ["range", "function", "class", "section", "file_summary"], "default": "range"},
					"symbol": {"example": 'build_tool_schemas', "type": "string", "default": "", "description": "Exact or qualified function/class selector."},
					"section": {"example": '## Usage', "type": "string", "default": "", "description": "Markdown heading or explicit section selector."},
					"related_query": {"example": 'where is build_tool_schemas called', "type": "string", "default": "", "description": "Relevance query used to rank next_ranges."},
					"query": {"example": 'registry_data', "type": "string", "default": "", "description": "Alias for related_query."},
					"token_budget": {"example": 4000, "type": "integer", "default": 0, "description": "Content-token budget for this call. When > 0, the budget replaces the default line caps: lines are delivered until the estimated token count reaches the budget, subject to an absolute cap of 12000 (an explicit max_lines still applies). Responses too large for the envelope spill to result_ref. 0 (default) uses line bounds."},
					"small_file_threshold": {"example": 240, "type": "integer", "default": 240, "description": "Auto-full line threshold."},
					"include_next_ranges": {"example": True, "type": "boolean", "default": True, "description": "Include ranked bounded follow-up ranges."},
					"content_hash": {"example": '9f2c1ab4', "type": "string", "default": "", "description": "Hash returned by a prior read for cache reuse/staleness signaling."},
					"force_refresh": {"example": False, "type": "boolean", "default": False, "description": "Bypass the in-memory content cache."},
					"ranges": {"example": [{"start_line": 100, "end_line": 200}], "type": "array", "items": {"type": "object", "properties": {"start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["start_line", "end_line"]}, "description": "Multi-range read (S4): an array of {start_line, end_line} objects, at most 16 raw elements. v11 1a CALL SHAPE: send ranges ALONE (file_path + ranges, no start_line/end_line/max_lines) - e.g. {file_path: relative/file.py, ranges: [{start_line: 10, end_line: 12}, {start_line: 20, end_line: 25}]}; for ONE range use the plain start_line/end_line call instead. Mixing both groups fails with RANGES_WITH_LEGACY_PARAMS. Ranges are normalized before delivery: per-element errors go to range_errors (INVALID_RANGE / EMPTY_RANGE / PAST_EOF) without failing the call, end_line past EOF is clamped (clamped: true in the range metadata), ranges are sorted and merged when overlapping OR adjacent, and delivery is a single result block with one unnumbered marker line '--- RANGE k/N: L<start>-L<end> ---' before each delivered range. Whole-call errors, checked in this order before any element: RANGES_DISABLED (kill-switch ORCHESTRATOR_READ_RANGES_ENABLED is off iff its trimmed lower-cased value is 0 or false) > RANGES_INVALID_TYPE (ranges is not an array; ranges: null counts as absent) > RANGES_WITH_LEGACY_PARAMS (judged on coerced values: end_line present, or coerced start_line != 1, or coerced max_lines != 200; so max_lines=0/200 and start_line=0/1/null do NOT conflict) > RANGES_SCOPE_UNSUPPORTED_V1 (resolved scope != range -- scope is lower-cased but NOT trimmed, so 'RANGE' is allowed and 'range ' is rejected -- or a non-empty symbol/section) > RANGES_PIPELINE_UNSUPPORTED_V1 > RANGES_EMPTY > RANGES_TOO_MANY (more than 16 raw elements). One token_budget is shared by the whole call (capped at 12000); without one, each range is fitted in legacy line mode and the response carries no budget_mode/token_budget_effective keys. The read token is seeded once for the whole file when at least one normalized range will be delivered. Token counts use this system's own estimator (numbered-line UTF-8 bytes / 4), not the client's tokenizer [R-11]. Client guardrail: clients with their own payload limit (e.g. the Codex 64,000-byte strict limit) must fetch overflow through result_ref with max_bytes <= 20,000-32,000, follow the server-returned next_offset (never a self-computed stride), and verify content_sha256 before use. Kill-switch ORCHESTRATOR_READ_RANGES_ENABLED: the flag is OFF only when its value, after trimming and lower-casing, is exactly 0 or false -- values such as off, no or 00 do NOT turn it off, and an unset or empty value means ON. When the flag is OFF, ranges[] calls are rejected with RANGES_DISABLED and a declared token_budget falls back to legacy semantics (early stop inside the 800-line cap, no budget_mode / token_budget_effective keys). The flag is read from the process environment on every call; changing it for an already-running daemon takes effect only after that process restarts."},
					# A34 P2c (2026-09-18): see the twin block in the search_code schema below
					# for why a dispatcher-popped field still has to be declared here.
					"pipeline": {"example": ['next_range'], "type": "array", "default": [], "items": {"type": "string", "enum": sorted(ACTIVE_CHAIN_STEPS.get("read_file_range", ()))}, "description": "OPT-IN follow-up READS to run inside this same call, at most %d; each answer lands under the response key `pipeline`. Every step is built from THIS call's own result -- the neighbour range it already computed, the symbol map of the file it just read -- so nothing runs unless you name it here and an unset pipeline costs nothing. An unknown name is echoed under pipeline.rejected next to the steps that did run, never raised." % MAX_STEPS_PER_CALL},
				},
				"required": ["file_path"],
			},
			"returns": {
				'type': 'object',
				'properties': {
					'ok': {
						'type': 'boolean',
						'x-dual': True
					},
					'result': {
						'type': 'string'
					},
					'summary': {
						'type': 'object',
						'properties': {
							'file': {
								'type': 'string'
							},
							'line_count': {
								'type': 'integer'
							},
							'size_bytes': {
								'type': 'integer'
							},
							'content_hash': {
								'type': 'string'
							},
							'parser': {
								'type': 'string'
							},
							'parse_error': {
								'anyOf': [
									{
										'type': 'string'
									},
									{
										'type': 'null'
									}
								]
							},
							'imports': {
								'type': 'array',
								'items': {
									'type': 'string'
								}
							},
							'symbols': {
								'type': 'array',
								'items': {
									'type': 'object'
								}
							},
							'sections': {
								'type': 'array',
								'items': {
									'type': 'object'
								}
							},
							'symbols_total': {
								'type': 'integer'
							},
							'sections_total': {
								'type': 'integer'
							}
						}
					},
					'file': {
						'type': 'string'
					},
					'project_path': {
						'anyOf': [
							{
								'type': 'null'
							},
							{
								'type': 'string'
							}
						]
					},
					'file_group': {
						'type': 'null'
					},
					'lang': {
						'type': 'string'
					},
					'total_lines': {
						'type': 'integer'
					},
					'shown': {
						'anyOf': [
							{
								'type': 'null'
							},
							{
								'type': 'string'
							}
						]
					},
					'range_id': {
						'type': 'string'
					},
					'bounded': {
						'type': 'boolean'
					},
					'max_lines': {
						'type': 'integer'
					},
					'max_lines_cap': {
						'type': 'integer'
					},
					'max_lines_default': {
						'type': 'integer'
					},
					'scope_requested': {
						'type': 'string'
					},
					'scope_resolved': {
						'type': 'string'
					},
					'selection': {
						'anyOf': [
							{
								'type': 'null'
							},
							{
								'type': 'object',
								'properties': {
									'ok': {
										'type': 'boolean'
									},
									'scope': {
										'type': 'string'
									},
									'name': {
										'anyOf': [
											{
												'type': 'null'
											},
											{
												'type': 'string'
											}
										]
									},
									'start_line': {
										'anyOf': [
											{
												'type': 'null'
											},
											{
												'type': 'integer'
											}
										]
									},
									'end_line': {
										'anyOf': [
											{
												'type': 'null'
											},
											{
												'type': 'integer'
											}
										]
									},
									'source': {
										'type': 'string'
									},
									'qualified_name': {
										'type': 'string'
									},
									'kind': {
										'type': 'string'
									}
								}
							}
						]
					},
					'content_hash': {
						'type': 'string'
					},
					'range_ref': {
						'anyOf': [
							{
								'type': 'null'
							},
							{
								'type': 'string'
							}
						]
					},
					'cache_hit': {
						'type': 'boolean'
					},
					'cache_waited': {
						'type': 'boolean'
					},
					'estimated_tokens': {
						'type': 'integer'
					},
					'token_budget': {
						'anyOf': [
							{
								'type': 'integer'
							},
							{
								'type': 'null'
							}
						]
					},
					'token_estimator': {
						'type': 'string'
					},
					'auto_full_file': {
						'type': 'boolean'
					},
					'small_file_threshold': {
						'type': 'integer'
					},
					'truncated': {
						'type': 'boolean'
					},
					'next_ranges': {
						'type': 'array'
					},
					'read_token_seeded': {
						'type': 'boolean'
					},
					'derived_state_touched': {
						'type': 'boolean'
					},
					'empty_file': {
						'type': 'boolean'
					},
					'read_token_seed_failure': {
						'type': 'object'
					},
					'content_hash_mismatch': {
						'type': 'boolean'
					},
					'warn': {
						'type': 'string'
					},
					'read_unstable': {
						'type': 'boolean'
					},
					'error': {
						'type': 'string',
						'x-dual': True
					},
					'error_name': {
						'type': 'string',
						'x-dual': True
					},
					'hint': {
						'x-dual': True,
						'type': 'string'
					},
					'ca_next_action': {
						'type': 'string',
						'x-dual': True
					},
					'retryable': {
						'type': 'boolean'
					},
					'reseed_failure_reason': {
						'type': 'string'
					},
					'reseed_error': {
						'type': 'string'
					},
					'exists': {
						'type': 'boolean'
					},
					'repeat_probe': {
						'type': 'boolean'
					},
					'probe_count': {
						'type': 'integer'
					},
					'fuzzy_suggestions': {
						'type': 'array'
					},
					'fuzzy_suggestions_source': {
						'type': 'string'
					},
					'fuzzy_suggestions_limitation': {
						'type': 'string'
					},
					'markdown_escape_detected': {
						'type': 'boolean'
					},
					'unescaped_path_candidate': {
						'type': 'string'
					},
					'circuit_breaker': {
						'type': 'object'
					},
					'type': {
						'type': 'string'
					},
					'severity': {
						'type': 'string'
					},
					'path': {
						'type': 'string'
					},
					'suggested_tools': {
						'type': 'array',
						'items': {
							'type': 'string'
						}
					},
					'suffix': {
						'type': 'string'
					},
					'size_bytes': {
						'anyOf': [
							{
								'type': 'integer'
							},
							{
								'type': 'null'
							}
						]
					},
					'accepted_values': {
						'type': 'array',
						'items': {
							'type': 'string'
						}
					},
					'requested_start_line': {},
					'requested_end_line': {},
					'action_required': {
						'type': 'string'
					},
					'candidates': {
						'type': 'array',
						'items': {
							'type': 'object'
						}
					},
					'parse_error': {
						'anyOf': [
							{
								'type': 'string'
							},
							{
								'type': 'null'
							}
						]
					},
					'ca_hint': {
						'type': 'string',
						'x-dual': True
					},
					'ca_next_tool': {
						'type': 'string',
						'x-dual': True
					},
					'ca_guidance': {
						'type': 'object',
						'properties': {
							'phase': {
								'type': 'string'
							},
							'phase_label': {
								'type': 'string'
							},
							'error_name': {
								'type': 'string'
							},
							'ca_next_action': {
								'type': 'string'
							},
							'call_pattern': {
								'type': 'string'
							},
							'payload_example': {
								'type': 'object',
								'properties': {
									'file_path': {
										'type': 'string'
									},
									'project_path': {
										'type': 'string'
									},
									'start_line': {
										'type': 'integer'
									},
									'max_lines': {
										'type': 'integer'
									},
									'force_reseed': {
										'type': 'boolean'
									},
									'scope': {
										'type': 'string'
									},
									'symbol': {
										'type': 'string'
									},
									'section': {
										'type': 'string'
									},
									'related_query': {
										'type': 'string'
									},
									'query': {
										'type': 'string'
									},
									'token_budget': {
										'type': 'integer'
									},
									'small_file_threshold': {
										'type': 'integer'
									},
									'include_next_ranges': {
										'type': 'boolean'
									},
									'content_hash': {
										'type': 'string'
									},
									'force_refresh': {
										'type': 'boolean'
									},
									'pipeline': {
										'type': 'array'
									}
								}
							},
							'required_fields': {
								'type': 'array',
								'items': {
									'type': 'string'
								}
							},
							'accepted_aliases': {
								'type': 'object',
								'properties': {
									'path': {
										'type': 'object',
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										}
									},
									'file': {
										'type': 'object',
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										}
									},
									'start': {
										'type': 'object',
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										}
									},
									'end': {
										'type': 'object',
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										}
									},
									'from_line': {
										'type': 'object',
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										}
									},
									'to_line': {
										'type': 'object',
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										}
									},
									'line_start': {
										'type': 'object',
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										}
									},
									'line_end': {
										'type': 'object',
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										}
									}
								}
							},
							'notes': {
								'type': 'array',
								'items': {
									'type': 'string'
								}
							},
							'known_gotchas': {
								'type': 'array'
							},
							'recommended_prompt_rule': {
								'type': 'string'
							},
							'see_also': {
								'type': 'object',
								'properties': {}
							},
							'suggested_alternative_tool': {
								'type': 'object',
								'properties': {
									'tool': {
										'type': 'string'
									},
									'reason': {
										'type': 'string'
									}
								}
							}
						},
						'x-dual': True
					},
					'retry_sequence': {
						'type': 'array',
						'x-dual': True
					},
					'payload_example': {
						'type': 'object',
						'properties': {
							'file_path': {
								'type': 'string'
							},
							'project_path': {
								'type': 'string'
							},
							'start_line': {
								'type': 'integer'
							},
							'max_lines': {
								'type': 'integer'
							},
							'force_reseed': {
								'type': 'boolean'
							},
							'scope': {
								'type': 'string'
							},
							'symbol': {
								'type': 'string'
							},
							'section': {
								'type': 'string'
							},
							'related_query': {
								'type': 'string'
							},
							'query': {
								'type': 'string'
							},
							'token_budget': {
								'type': 'integer'
							},
							'small_file_threshold': {
								'type': 'integer'
							},
							'include_next_ranges': {
								'type': 'boolean'
							},
							'content_hash': {
								'type': 'string'
							},
							'force_refresh': {
								'type': 'boolean'
							},
							'pipeline': {
								'type': 'array'
							}
						},
						'x-dual': True
					},
					'phase': {
						'type': 'string',
						'x-dual': True
					},
					'suggested_alternative_tool': {
						'type': 'object',
						'properties': {
							'tool': {
								'type': 'string'
							},
							'reason': {
								'type': 'string'
							}
						},
						'x-dual': True
					},
					'normalization_applied': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'missing_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'required_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'accepted_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'original_error_name': {
						'type': 'string',
						'x-dual': True
					},
					'path_normalized': {
						'x-dual': True
					},
					'project_path_resolution': {
						'x-dual': True
					},
					'deprecated_fields': {
						'type': 'array',
						'x-dual': True
					},
					'canonical_replacements': {
						'type': 'object',
						'x-dual': True
					},
					'compatibility_applied': {
						'type': 'boolean',
						'x-dual': True
					},
					'removal_version': {
						'type': 'string',
						'x-dual': True
					}
				},
				'required': [
					'ok'
				],
				'x-audit': {
					'status': 'audited',
					'schema_sha256': '1efe790518fac87cc45e32a930bfe5e2e103826933c9a48adbdc23a924e37d21',
					'depth_D': 4
				}
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "read_context_batch",
			"description": "Read multiple files or structural scopes in one pure T1 call under one total token budget. Use it once cbm has already said WHICH files matter (cbm_get_architecture, cbm_dsm, cbm_trace_path, ocr_map, find_symbol): N targeted scopes in one budgeted call costs far less context than N separate read_file_range calls. It is a PURE read and does NOT seed the write_fix read_token - seed that with read_file_range on the exact region you are about to edit.",
			"parameters": {
				"type": "object",
				"properties": {
					"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "description": "Absolute project root."},
					"requests": {"example": [{'file_path': 'code_tools.py', 'start_line': 1360, 'end_line': 1420}], 
						"type": "array",
						"description": "Ordered structural read requests sharing the total token budget.",
						"minItems": 1,
						"maxItems": 20,
						"items": {
							"type": "object",
							"properties": {
									"file_path": {"type": "string", "description": "File path relative to project_path."},
									"scope": {"type": "string", "enum": ["range", "function", "class", "section", "file_summary"], "default": "range", "description": "Structural scope to select before applying output bounds."},
									"start_line": {"type": "integer", "default": 1, "description": "One-based range start or structural anchor."},
									"end_line": {"type": "integer", "description": "Inclusive range end for explicit range reads."},
									"max_lines": {"type": "integer", "default": 200, "description": "Maximum lines returned for this item."},
									"symbol": {"type": "string", "default": "", "description": "Exact or qualified function/class selector."},
									"section": {"type": "string", "default": "", "description": "Markdown heading selector."},
									"related_query": {"type": "string", "default": "", "description": "Query used to rank suggested next ranges."},
									"content_hash": {"type": "string", "default": "", "description": "Hash from a prior read for cache reuse or staleness signaling."},
									"priority": {"type": "integer", "default": 100, "description": "Lower values are read first within the shared budget."},
							},
							"required": ["file_path"],
						},
					},
					"token_budget": {"example": 6000, "type": "integer", "default": 6000, "minimum": 64, "maximum": 12000, "description": "Shared maximum content-token budget."},
					"max_items": {"example": 20, "type": "integer", "default": 20, "minimum": 1, "maximum": 20, "description": "Maximum request items processed."},
					"small_file_threshold": {"example": 240, "type": "integer", "default": 240, "description": "Line threshold for automatic whole-file reads."},
					"include_next_ranges": {"example": True, "type": "boolean", "default": True, "description": "Include ranked bounded follow-up ranges."},
				},
				"required": ["project_path", "requests"],
			},
			"returns": {
				'type': 'object',
				'properties': {
					'ok': {
						'type': 'boolean',
						'x-dual': True
					},
					'error': {
						'type': 'string',
						'x-dual': True
					},
					'error_name': {
						'type': 'string',
						'x-dual': True
					},
					'ca_next_action': {
						'type': 'string',
						'x-dual': True
					},
					'items': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'ok': {
									'type': 'boolean'
								},
								'result': {
									'type': 'string'
								},
								'file': {
									'type': 'string'
								},
								'project_path': {
									'type': 'string'
								},
								'file_group': {
									'type': 'null'
								},
								'lang': {
									'type': 'string'
								},
								'total_lines': {
									'type': 'integer'
								},
								'shown': {
									'type': 'string'
								},
								'range_id': {
									'type': 'string'
								},
								'bounded': {
									'type': 'boolean'
								},
								'max_lines': {
									'type': 'integer'
								},
								'max_lines_cap': {
									'type': 'integer'
								},
								'max_lines_default': {
									'type': 'integer'
								},
								'scope_requested': {
									'type': 'string'
								},
								'scope_resolved': {
									'type': 'string'
								},
								'selection': {
									'type': 'object',
									'properties': {
										'ok': {
											'type': 'boolean'
										},
										'scope': {
											'type': 'string'
										},
										'name': {
											'anyOf': [
												{
													'type': 'null'
												},
												{
													'type': 'string'
												}
											]
										},
										'start_line': {
											'type': 'integer'
										},
										'end_line': {
											'type': 'integer'
										},
										'source': {
											'type': 'string'
										},
										'qualified_name': {
											'type': 'string'
										},
										'kind': {
											'type': 'string'
										}
									}
								},
								'content_hash': {
									'type': 'string'
								},
								'range_ref': {
									'type': 'string'
								},
								'cache_hit': {
									'type': 'boolean'
								},
								'cache_waited': {
									'type': 'boolean'
								},
								'estimated_tokens': {
									'type': 'integer'
								},
								'token_budget': {
									'type': 'integer'
								},
								'token_estimator': {
									'type': 'string'
								},
								'auto_full_file': {
									'type': 'boolean'
								},
								'small_file_threshold': {
									'type': 'integer'
								},
								'truncated': {
									'type': 'boolean'
								},
								'next_ranges': {
									'type': 'array',
									'items': {
										'type': 'object',
										'properties': {
											'file_path': {
												'type': 'string'
											},
											'scope': {
												'type': 'string'
											},
											'start_line': {
												'type': 'integer'
											},
											'end_line': {
												'type': 'integer'
											},
											'name': {
												'anyOf': [
													{
														'type': 'string'
													},
													{
														'type': 'null'
													}
												]
											},
											'kind': {
												'type': 'string'
											},
											'relevance_score': {
												'type': 'number'
											},
											'reason': {
												'type': 'string'
											},
											'source': {
												'type': 'string'
											}
										}
									}
								},
								'read_token_seeded': {
									'type': 'boolean'
								},
								'derived_state_touched': {
									'type': 'boolean'
								},
								'request_index': {
									'type': 'integer'
								},
								'error': {
									'type': 'string'
								},
								'error_name': {
									'type': 'string'
								},
								'exists': {
									'type': 'boolean'
								},
								'repeat_probe': {
									'type': 'boolean'
								},
								'probe_count': {
									'type': 'integer'
								},
								'fuzzy_suggestions': {
									'type': 'array'
								},
								'fuzzy_suggestions_source': {
									'type': 'string'
								},
								'fuzzy_suggestions_limitation': {
									'type': 'string'
								},
								'ca_next_action': {
									'type': 'string'
								},
								'hint': {
									'type': 'string'
								}
							}
						}
					},
					'count': {
						'type': 'integer'
					},
					'token_budget': {
						'type': 'integer'
					},
					'used_tokens': {
						'type': 'integer'
					},
					'remaining_tokens': {
						'type': 'integer'
					},
					'within_budget': {
						'type': 'boolean'
					},
					'token_estimator': {
						'type': 'string'
					},
					'truncated': {
						'type': 'boolean'
					},
					'deferred': {
						'type': 'array'
					},
					'next_ranges': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'file_path': {
									'type': 'string'
								},
								'scope': {
									'type': 'string'
								},
								'start_line': {
									'type': 'integer'
								},
								'end_line': {
									'type': 'integer'
								},
								'name': {
									'anyOf': [
										{
											'type': 'string'
										},
										{
											'type': 'null'
										}
									]
								},
								'kind': {
									'type': 'string'
								},
								'relevance_score': {
									'type': 'number'
								},
								'reason': {
									'type': 'string'
								},
								'source': {
									'type': 'string'
								}
							}
						}
					},
					'deduplicated': {
						'type': 'integer'
					},
					'cache': {
						'type': 'object',
						'properties': {
							'entries': {
								'type': 'integer'
							},
							'bytes': {
								'type': 'integer'
							},
							'hits': {
								'type': 'integer'
							},
							'misses': {
								'type': 'integer'
							},
							'parse_hits': {
								'type': 'integer'
							},
							'max_entries': {
								'type': 'integer'
							},
							'max_bytes': {
								'type': 'integer'
							}
						}
					},
					'derived_state_touched': {
						'type': 'boolean'
					},
					'ca_hint': {
						'x-dual': True,
						'type': 'string'
					},
					'ca_next_tool': {
						'type': 'string',
						'x-dual': True
					},
					'ca_guidance': {
						'x-dual': True,
						'type': 'object'
					},
					'retry_sequence': {
						'x-dual': True,
						'type': 'array'
					},
					'payload_example': {
						'x-dual': True,
						'type': 'object'
					},
					'phase': {
						'type': 'string',
						'x-dual': True
					},
					'suggested_alternative_tool': {
						'properties': {
							'reason': {
								'type': 'string'
							},
							'tool': {
								'type': 'string'
							}
						},
						'type': 'object',
						'x-dual': True
					},
					'normalization_applied': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'missing_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'required_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'accepted_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'original_error_name': {
						'type': 'string',
						'x-dual': True
					},
					'path_normalized': {
						'x-dual': True
					},
					'project_path_resolution': {
						'x-dual': True
					},
					'deprecated_fields': {
						'type': 'array',
						'x-dual': True
					},
					'canonical_replacements': {
						'type': 'object',
						'x-dual': True
					},
					'compatibility_applied': {
						'type': 'boolean',
						'x-dual': True
					},
					'removal_version': {
						'type': 'string',
						'x-dual': True
					}
				},
				'required': [
					'ok'
				],
				'x-audit': {
					'status': 'audited',
					'schema_sha256': 'f54a9ed408b41d8a9b60797d2c209f6833a4fc1eaeb53a7e969ff195621c8ce7',
					'depth_D': 5
				}
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "architecture_context_pack",
			"description": "Graph-first readonly context pack that selects exact files/functions then reads them under one budget. Never auto-reindexes.",
			"parameters": {
				"type": "object",
				"properties": {
					"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "description": "Absolute project root."},
					"symbol": {"example": 'build_tool_schemas', "type": "string", "default": "", "description": "Symbol or qualified symbol seed."},
					"file_path": {"example": 'orchestrator\\core\\registry_data.py', "type": "string", "default": "", "description": "Fallback file when graph has no symbol seed."},
					"query": {"example": 'tool schema assembly', "type": "string", "default": "", "description": "Alias/fallback graph seed."},
					"depth": {"example": 2, "type": "integer", "default": 2, "minimum": 1, "maximum": 5},
					"token_budget": {"example": 6000, "type": "integer", "default": 6000, "minimum": 64, "maximum": 12000, "description": "Shared maximum content-token budget."},
					"max_files": {"example": 8, "type": "integer", "default": 8, "minimum": 1, "maximum": 20, "description": "Maximum distinct files in the context pack."},
					"max_ranges": {"example": 20, "type": "integer", "default": 20, "minimum": 1, "maximum": 20, "description": "Maximum structural ranges in the context pack."},
				},
				"required": ["project_path"],
			},
			"returns": {
				'type': 'object',
				'properties': {
					'ok': {
						'type': 'boolean',
						'x-dual': True
					},
					'error': {
						'type': 'string',
						'x-dual': True
					},
					'error_name': {
						'type': 'string',
						'x-dual': True
					},
					'graph_used': {
						'type': 'boolean'
					},
					'graph_degraded_reason': {
						'anyOf': [
							{
								'type': 'string'
							},
							{
								'type': 'null'
							}
						]
					},
					'recommended_action': {
						'type': 'string'
					},
					'provenance': {
						'type': 'object',
						'properties': {
							'graph_used': {
								'type': 'boolean'
							},
							'graph_error': {
								'type': 'null'
							},
							'graph_truncated': {
								'type': 'boolean'
							},
							'graph_auto_reindex': {
								'type': 'boolean'
							},
							'target_node_id': {
								'type': 'string'
							},
							'nodes_seen': {
								'type': 'integer'
							}
						}
					},
					'derived_state_touched': {
						'type': 'boolean'
					},
					'query': {
						'type': 'string'
					},
					'symbol': {
						'type': 'string'
					},
					'depth': {
						'type': 'integer'
					},
					'ranked_files': {
						'type': 'array',
						'items': {
							'type': 'string'
						}
					},
					'ranked_symbols': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'id': {
									'type': 'string'
								},
								'kind': {
									'type': 'string'
								},
								'name': {
									'type': 'string'
								},
								'qualified_name': {
									'type': 'string'
								},
								'file_path': {
									'type': 'string'
								},
								'start_line': {
									'type': 'integer'
								},
								'end_line': {
									'type': 'integer'
								}
							}
						}
					},
					'items': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'ok': {
									'type': 'boolean'
								},
								'result': {
									'type': 'string'
								},
								'summary': {
									'type': 'object',
									'properties': {
										'file': {
											'type': 'string'
										},
										'line_count': {
											'type': 'integer'
										},
										'size_bytes': {
											'type': 'integer'
										},
										'content_hash': {
											'type': 'string'
										},
										'parser': {
											'type': 'string'
										},
										'parse_error': {
											'type': 'null'
										},
										'imports': {
											'type': 'array',
											'items': {
												'type': 'string'
											}
										},
										'symbols': {
											'type': 'array',
											'items': {
												'type': 'object',
												'properties': {
													'name': {
														'type': 'string'
													},
													'qualified_name': {
														'type': 'string'
													},
													'kind': {
														'type': 'string'
													},
													'start_line': {
														'type': 'integer'
													},
													'end_line': {
														'type': 'integer'
													}
												}
											}
										},
										'sections': {
											'type': 'array',
											'items': {
												'type': 'object',
												'properties': {
													'name': {
														'type': 'string'
													},
													'qualified_name': {
														'type': 'string'
													},
													'kind': {
														'type': 'string'
													},
													'start_line': {
														'type': 'integer'
													},
													'end_line': {
														'type': 'integer'
													}
												}
											}
										},
										'symbols_total': {
											'type': 'integer'
										},
										'sections_total': {
											'type': 'integer'
										}
									}
								},
								'file': {
									'type': 'string'
								},
								'project_path': {
									'type': 'string'
								},
								'file_group': {
									'type': 'null'
								},
								'lang': {
									'type': 'string'
								},
								'total_lines': {
									'type': 'integer'
								},
								'shown': {
									'type': 'string'
								},
								'range_id': {
									'type': 'string'
								},
								'bounded': {
									'type': 'boolean'
								},
								'max_lines': {
									'type': 'integer'
								},
								'max_lines_cap': {
									'type': 'integer'
								},
								'max_lines_default': {
									'type': 'integer'
								},
								'scope_requested': {
									'type': 'string'
								},
								'scope_resolved': {
									'type': 'string'
								},
								'selection': {
									'type': 'object',
									'properties': {
										'ok': {
											'type': 'boolean'
										},
										'scope': {
											'type': 'string'
										},
										'name': {
											'type': 'string'
										},
										'start_line': {
											'type': 'integer'
										},
										'end_line': {
											'type': 'integer'
										},
										'source': {
											'type': 'string'
										}
									}
								},
								'content_hash': {
									'type': 'string'
								},
								'range_ref': {
									'type': 'string'
								},
								'cache_hit': {
									'type': 'boolean'
								},
								'cache_waited': {
									'type': 'boolean'
								},
								'estimated_tokens': {
									'type': 'integer'
								},
								'token_budget': {
									'type': 'integer'
								},
								'token_estimator': {
									'type': 'string'
								},
								'auto_full_file': {
									'type': 'boolean'
								},
								'truncated': {
									'type': 'boolean'
								},
								'next_ranges': {
									'type': 'array'
								},
								'read_token_seeded': {
									'type': 'boolean'
								},
								'derived_state_touched': {
									'type': 'boolean'
								},
								'request_index': {
									'type': 'integer'
								},
								'error_name': {
									'type': 'string'
								},
								'error': {
									'type': 'string'
								},
								'candidates': {
									'type': 'array',
									'items': {
										'type': 'object',
										'properties': {
											'name': {
												'type': 'string'
											},
											'qualified_name': {
												'type': 'string'
											},
											'kind': {
												'type': 'string'
											},
											'start_line': {
												'type': 'integer'
											},
											'end_line': {
												'type': 'integer'
											}
										}
									}
								},
								'parse_error': {
									'type': 'null'
								},
								'ca_next_action': {
									'type': 'string'
								}
							}
						}
					},
					'next_ranges': {
						'type': 'array'
					},
					'budget': {
						'type': 'object',
						'properties': {
							'requested': {
								'type': 'integer'
							},
							'used': {
								'type': 'integer'
							},
							'remaining': {
								'type': 'integer'
							},
							'within_budget': {
								'type': 'boolean'
							}
						}
					},
					'cache': {
						'type': 'object',
						'properties': {
							'entries': {
								'type': 'integer'
							},
							'bytes': {
								'type': 'integer'
							},
							'hits': {
								'type': 'integer'
							},
							'misses': {
								'type': 'integer'
							},
							'parse_hits': {
								'type': 'integer'
							},
							'max_entries': {
								'type': 'integer'
							},
							'max_bytes': {
								'type': 'integer'
							}
						}
					},
					'ca_hint': {
						'x-dual': True,
						'type': 'string'
					},
					'ca_next_action': {
						'type': 'string',
						'x-dual': True
					},
					'ca_next_tool': {
						'type': 'string',
						'x-dual': True
					},
					'ca_guidance': {
						'type': 'object',
						'properties': {
							'phase': {
								'type': 'string'
							},
							'phase_label': {
								'type': 'string'
							},
							'error_name': {
								'type': 'string'
							},
							'ca_next_action': {
								'type': 'string'
							},
							'call_pattern': {
								'type': 'string'
							},
							'payload_example': {
								'type': 'object',
								'properties': {
									'project_path': {
										'type': 'string'
									},
									'symbol': {
										'type': 'string'
									},
									'file_path': {
										'type': 'string'
									},
									'query': {
										'type': 'string'
									},
									'depth': {
										'type': 'integer'
									},
									'token_budget': {
										'type': 'integer'
									},
									'max_files': {
										'type': 'integer'
									},
									'max_ranges': {
										'type': 'integer'
									}
								}
							},
							'required_fields': {
								'type': 'array',
								'items': {
									'type': 'string'
								}
							},
							'accepted_aliases': {
								'type': 'object',
								'properties': {}
							},
							'notes': {
								'type': 'array',
								'items': {
									'type': 'string'
								}
							},
							'known_gotchas': {
								'type': 'array'
							},
							'recommended_prompt_rule': {
								'type': 'string'
							},
							'see_also': {
								'type': 'object',
								'properties': {}
							},
							'suggested_alternative_tool': {
								'type': 'object',
								'properties': {}
							}
						},
						'x-dual': True
					},
					'retry_sequence': {
						'type': 'array',
						'x-dual': True
					},
					'payload_example': {
						'type': 'object',
						'properties': {
							'project_path': {
								'type': 'string'
							},
							'symbol': {
								'type': 'string'
							},
							'file_path': {
								'type': 'string'
							},
							'query': {
								'type': 'string'
							},
							'depth': {
								'type': 'integer'
							},
							'token_budget': {
								'type': 'integer'
							},
							'max_files': {
								'type': 'integer'
							},
							'max_ranges': {
								'type': 'integer'
							}
						},
						'x-dual': True
					},
					'phase': {
						'type': 'string',
						'x-dual': True
					},
					'suggested_alternative_tool': {
						'properties': {
							'reason': {
								'type': 'string'
							},
							'tool': {
								'type': 'string'
							}
						},
						'type': 'object',
						'x-dual': True
					},
					'normalization_applied': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'missing_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'required_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'accepted_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'original_error_name': {
						'type': 'string',
						'x-dual': True
					},
					'path_normalized': {
						'x-dual': True
					},
					'project_path_resolution': {
						'x-dual': True
					},
					'deprecated_fields': {
						'type': 'array',
						'x-dual': True
					},
					'canonical_replacements': {
						'type': 'object',
						'x-dual': True
					},
					'compatibility_applied': {
						'type': 'boolean',
						'x-dual': True
					},
					'removal_version': {
						'type': 'string',
						'x-dual': True
					}
				},
				'required': [
					'derived_state_touched',
					'graph_degraded_reason',
					'graph_used',
					'ok',
					'provenance'
				],
				'x-audit': {
					'status': 'audited',
					'schema_sha256': 'b788937786168c4b91d062feec50d0fcd41e5b690d75afc9dcb2fbfbbf38ae9f',
					'depth_D': 6
				}
			},
		},
	},

	{

		"type": "function",

		"function": {

			"name": "search_code",

			"description": "Unified code/search entrypoint. mode defaults to text; supported modes: text|regex|symbol|callers|definition_usage|index|smart. DOM element candidates are not served by search_code — use the query_dom_candidates tool with a live page_id. Legacy tool names route here as HTTP aliases (find_callers / find_definition_and_usage / regex_search / read_chunks are DEPRECATED - come here instead). query_encoding = plain|base64. Pick by SEMANTICS, not habit: search_code answers WHERE a string or symbol sits, while the cbm_* family answers HOW the repo is shaped - cbm_get_architecture for the module graph and entrypoints, cbm_dsm for coupling, cbm_evolution for churn, cbm_trace_path between two files, cbm_dead_code for unreachable callables. It indexes CODE only, so file_pattern=\"*.txt\"/\"*.md\"/\"*.log\" answering 0 hits is a measurement trap, not proof the text is absent - drop the pattern or read the file. It resolves by absolute project_path and never by cwd, so use it whenever a native grep says 'no such file' on a path that certainly exists. mode=callers DOES see instance receivers (self.fn() / obj.fn()), which is exactly why it is the cross-check to run before believing a cbm_dead_code 'no callers' verdict: the cbm graph resolves a call edge only through an imported module/alias, so an empty cbm callers list means 'no RESOLVED reference', never 'unused'.",

			"parameters": {

				"type": "object",

				"properties": {

					"query": {"example": 'build_tool_schemas', "type": "string", "default": "", "description": "Text, regex, symbol, or index query depending on mode"},

					"path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "description": "Legacy alias of project_path; satisfies the anyOf project_path/path requirement on its own. No default: an empty path is rejected as missing, not defaulted."},

					"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "description": "Project root to search. REQUIRED unless the legacy alias `path` carries it (schema anyOf: one of project_path/path); omitting both fails MISSING_REQUIRED_FIELD. Absolute root; resolves by this path, never by cwd."},

					"file_pattern": {"example": '*.py', "type": "string", "description": "File glob filter. The RIGHT shape is the narrowest thing that still answers the question: one filename (kick_qsleep.py) or a subdirectory glob (orchestrator/tools/*). Default: * = every indexed file. D2: WIDENING THIS IS THE EXPENSIVE DIRECTION and the contract used to hide it -- the wall clock tracks the bytes the glob admits, so on an 838-file repo (same query, mode='text', warm) one exact filename answered in 0.36s while a language-wide *.py glob took 25.33s, ~70x. On a same-language repo a language-wide glob is not a narrowing at all, so it is never the fix for a SEARCH_TOO_BROAD or SEARCH_TIMEOUT answer. Pass the narrowest pattern that can still answer the question, and never widen it on a retry 'to be safe'. The non-regex modes are bounded at SEARCH_TEXT_DEADLINE_SECONDS (60.0s) and answer error_name=SEARCH_TIMEOUT past it; there is no deadline parameter on those lanes, because a bigger budget buys a bigger bill rather than an answer.", "default": "*"},

					"mode": {"example": 'smart', "type": "string", "enum": ["text", "regex", "symbol", "callers", "definition_usage", "index", "smart"], "default": "text", "description": "Search mode or legacy-tool preset. The answer key follows the mode: symbol answers under results/count; callers under callers/callers_count and no results, since the definition list is exactly what it leaves out; definition_usage carries results, definition, callers and usages."},

					"query_encoding": {"example": 'plain', "type": "string", "enum": ["plain", "base64"], "default": "plain", "description": "Encoding of query before search."},

					"flags": {"example": 'i', "type": "string", "default": "i", "description": "Regex flags: i|m|s"},

                "force": {"example": False, "type": "boolean", "default": False, "description": "CAN-053 escape hatch, and explicitly NOT the remedy for SEARCH_TOO_BROAD: it only switches the guard off and pays the full-tree scan, so it answers slower, never better. The remedy is a narrower query or file_pattern. Set this only when a catch-all scan is genuinely the thing you want."},

					"max_context_lines": {"example": 1, "type": "integer", "default": 0, "description": "Context lines before and after each text/regex hit."},

					"max_results": {"example": 50, "type": "integer", "default": 20, "description": "Maximum returned hits, capped by backend defaults."},

					# B026 (2026-08-22): mode='text' now skips records above MAX_TEXT_RECORD_BYTES
					# (2 MB). Same lesson as B-01 twelve days ago: a parameter the backend
					# understands is still UNSUPPORTED_FIELD until the registry lists it, and an
					# escape hatch nobody can reach is not an escape hatch. 0 = scan everything.
					"max_file_bytes": {"example": 2000000, "type": "integer", "default": 2000000, "description": "mode='text' skips records larger than this (bytes). 0 = no cap, scan every record. Skipped files are reported in skipped_large_files."},
					# A34 P2c (2026-09-18): the ACTIVE half of the auto-chain, opt-in per call.
					# The dispatcher POPS this field before the handler ever sees it, so the
					# tool signature does not need it -- but B-01 and B026 directly above are
					# the same lesson twice: a field missing from THIS schema is a field no CA
					# can discover, and the dispatcher's own allowed_params comes from here.
					# enum and the bound are derived from the leaf's table, never retyped, so
					# this line cannot drift from what resolve_active_pipeline really accepts.
					"pipeline": {"example": ['top_hit'], "type": "array", "default": [], "items": {"type": "string", "enum": sorted(ACTIVE_CHAIN_STEPS.get("search_code", ()))}, "description": "OPT-IN follow-up READS to run inside this same call, at most %d; each answer lands under the response key `pipeline`. Every step is built from THIS call's own result, so nothing runs unless you name it here and an unset pipeline costs nothing. An unknown name is echoed under pipeline.rejected next to the steps that did run, never raised." % MAX_STEPS_PER_CALL},

				},

				"required": [],
				# SEARCH_CODE_PATH_REQUIREMENT (2026-11-21): runtime REJECTS a call
				# with neither path nor project_path (MISSING_REQUIRED_FIELD), so the
				# contract must say one of the two is required. anyOf keeps the legacy
				# `path` alias valid -- a plain required:["project_path"] would make
				# the dispatcher gate reject path-only calls.
				"anyOf": [
					{"required": ["project_path"]},
					{"required": ["path"]},
				],

				"accepted_encodings": ["plain", "base64"],

			},
			"returns": {
				'type': 'object',
				'properties': {
					'ok': {
						'type': 'boolean',
						'x-dual': True
					},
					'error': {
						'x-dual': True,
						'type': 'string'
					},
					'error_name': {
						'x-dual': True,
						'type': 'string'
					},
					'type': {
						'type': 'string'
					},
					'severity': {
						'type': 'string'
					},
					'retryable': {
						'type': 'boolean'
					},
					'ca_next_action': {
						'x-dual': True,
						'type': 'string'
					},
					'accepted_modes': {
						'type': 'array',
						'items': {
							'type': 'string'
						}
					},
					'accepted_encodings': {
						'type': 'array',
						'items': {
							'type': 'string'
						}
					},
					'mode_aliases': {
						'type': 'object'
					},
					'accepted_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'accepted_narrowing_fields': {},
					'hint': {
						'x-dual': True,
						'type': 'string'
					},
					'payload_example': {
						'x-dual': True,
						'type': 'object'
					},
					'corrected_payload': {},
					'bounded': {
						'type': 'boolean'
					},
					'query': {},
					'wall_clock_budget_seconds': {},
					'retry_sequence': {
						'x-dual': True,
						'type': 'array'
					},
					'file_pattern': {
						'type': 'string'
					},
					'strategy_hint': {
						'type': 'string'
					},
					'timeout_phase': {
						'type': 'string'
					},
					'timeout_phase_unknown_reason': {
						'type': 'string'
					},
					'input_files_read': {
						'type': 'integer'
					},
					'input_bytes_read': {
						'type': 'integer'
					},
					'results': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'file': {
									'type': 'string'
								},
								'line': {
									'type': 'integer'
								},
								'match': {
									'type': 'string'
								},
								'context_before': {
									'type': 'string'
								},
								'context_after': {
									'type': 'string'
								},
								'range_id': {
									'type': 'string'
								},
								'body': {
									'type': 'string'
								}
							}
						}
					},
					'count': {
						'type': 'integer'
					},
					'partial': {
						'type': 'boolean'
					},
					'truncated_by_deadline': {
						'type': 'boolean'
					},
					'partial_files_scanned': {
						'type': 'integer'
					},
					'partial_files_total': {
						'type': 'integer'
					},
					'partial_files_remaining': {
						'type': 'integer'
					},
					'partial_checkpoint_age_ms': {
						'type': 'integer'
					},
					'partial_hint': {
						'type': 'string'
					},
					'input_budget_bytes': {
						'type': 'integer'
					},
					'input_budget_records': {
						'type': 'integer'
					},
					'tried_paths': {
						'type': 'array'
					},
					'snapshot_root': {
						'type': 'string'
					},
					'mode': {
						'type': 'string'
					},
					'max_results': {
						'type': 'integer'
					},
					'max_context_lines': {
						'type': 'integer'
					},
					'snapshot_id': {
						'anyOf': [
							{
								'type': 'string'
							},
							{
								'type': 'null'
							}
						]
					},
					'ca_chain_hint': {
						'type': 'string'
					},
					'ca_chain_next': {
						'type': 'string'
					},
					'ca_hint': {
						'type': 'string',
						'x-dual': True
					},
					'regex_deadline_floor_applied': {
						'type': 'number'
					},
					'skipped_large_files': {},
					'skipped_large_count': {},
					'skipped_large_listed': {},
					'skipped_large_hint': {},
					'max_file_bytes': {},
					'file_pattern_matched_files': {},
					'indexed_files_scanned': {},
					'inconclusive': {},
					'tip': {},
					'stale_index': {
						'anyOf': [
							{
								'type': 'boolean'
							},
							{
								'type': 'null'
							}
						]
					},
					'stale_index_reason': {},
					'stale_index_error': {},
					'stale_files_count': {},
					'stale_index_scope': {},
					'snapshot_missing_indexed_files': {},
					'snapshot_missing_indexed_sample': {},
					'stale_reconcile': {
						'type': 'object'
					},
					'warning': {
						'type': 'string'
					},
					'category': {
						'type': 'string'
					},
					'drift': {
						'type': 'object',
						'properties': {
							'ok': {
								'type': 'boolean'
							},
							'added': {
								'type': 'array'
							},
							'modified': {
								'type': 'array',
								'items': {
									'type': 'string'
								}
							},
							'deleted': {
								'type': 'array'
							}
						}
					},
					'callers': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'file': {
									'type': 'string'
								},
								'line': {
									'type': 'integer'
								},
								'match': {
									'type': 'string'
								},
								'range_id': {
									'type': 'string'
								}
							}
						}
					},
					'callers_count': {
						'type': 'integer'
					},
					'usages': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'file': {
									'type': 'string'
								},
								'line': {
									'type': 'integer'
								},
								'match': {
									'type': 'string'
								},
								'range_id': {
									'type': 'string'
								}
							}
						}
					},
					'usages_count': {
						'type': 'integer'
					},
					'definition': {
						'type': 'array',
						'items': {
							'type': 'object',
							'properties': {
								'file': {
									'type': 'string'
								},
								'line': {
									'type': 'integer'
								},
								'match': {
									'type': 'string'
								},
								'range_id': {
									'type': 'string'
								},
								'body': {
									'type': 'string'
								}
							}
						}
					},
					'include': {
						'type': 'string'
					},
					'source': {},
					'total_count': {},
					'truncated': {},
					'truncation_reason': {
						'type': 'string'
					},
					'result_cap': {
						'type': 'integer'
					},
					'impact_scope': {
						'type': 'object'
					},
					'missing_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'required_fields': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'write_performed': {
						'type': 'boolean'
					},
					'applied': {
						'type': 'boolean'
					},
					'path': {
						'type': 'string'
					},
					'ca_next_tool': {
						'type': 'string',
						'x-dual': True
					},
					'ca_guidance': {
						'x-dual': True,
						'type': 'object'
					},
					'phase': {
						'type': 'string',
						'x-dual': True
					},
					'suggested_alternative_tool': {
						'properties': {
							'reason': {
								'type': 'string'
							},
							'tool': {
								'type': 'string'
							}
						},
						'type': 'object',
						'x-dual': True
					},
					'normalization_applied': {
						'type': 'array',
						'items': {
							'type': 'string'
						},
						'x-dual': True
					},
					'original_error_name': {
						'type': 'string',
						'x-dual': True
					},
					'path_normalized': {
						'x-dual': True
					},
					'project_path_resolution': {
						'x-dual': True
					},
					'deprecated_fields': {
						'type': 'array',
						'x-dual': True
					},
					'canonical_replacements': {
						'type': 'object',
						'x-dual': True
					},
					'compatibility_applied': {
						'type': 'boolean',
						'x-dual': True
					},
					'removal_version': {
						'type': 'string',
						'x-dual': True
					}
				},
				'required': [
					'ok'
				],
				'x-audit': {
					'status': 'audited',
					'schema_sha256': '516edc81bbf372300bb46ba113c8a5f8cb657d19bdb4d0fa1e5eba7a494fbd62',
					'depth_D': 3
				}
			},
		},

	},

	{

		"type": "function",

		"function": {

			"name": "get_file_symbols",

			"description": "List all functions, classes, and variables defined in one file.",

			"parameters": {

				"type": "object",

				"properties": {

					"file_path": {"type": "string", "description": "File path to analyze"},

				},

				"required": ["file_path"],

			},

		},

	},

	{

		"type": "function",

		"function": {

			"name": "write_fix",

            "description": "Edit a code range in a file by replacing it by line numbers. NO size limit on new_content - send the whole block in one call, and set content_encoding='base64' (UTF-8) whenever new_content is multiline or contains backslashes (Windows paths, regex escapes), double quotes, or non-ASCII/Vietnamese, or when the caller is Windows PowerShell; plain JSON transport can corrupt those. The file's existing line ending (CRLF vs LF) is auto-preserved, so editing a .bat/.ps1 keeps CRLF - no manual newline handling needed. On validation failure the backend opens a draft: response carries draft_id + failed_parts (line-accurate, with excerpts); fix ONLY the failed range via write_draft_patch until clean - the backend then ships automatically. Automatically creates a .bak backup and syncs the cache after the write. Requires INDEX.json to exist first (run generate_project_map). If the file changed outside Orchestrator, returns a conflict. When Python validation fails with a Ruff safe-fixable diagnostic, the response includes ruff_fix_applicable and ruff_fix_hint suggesting ruff_fix(path=<file>, apply=false) for CA-controlled preview; it never auto-fixes. ONE region per call and the shape is FLAT (file_path,start_line,end_line,new_content) - there is no edits[] array, so several regions in the SAME file need several calls and they MUST go BOTTOM-UP: an earlier top region renumbers every line beneath it. expected_hash is a PREVIOUS write_fix.new_hash, never a read_file_range.content_hash (that one is a raw-bytes digest and seeds no edit authority). On a large repo, run cbm_what_if / cbm_dsm on the target first so the edit set is chosen from the call graph instead of guessed, and a client timeout is NOT a failed write - poll /api/write_status?operation_id=... until commit_state is terminal instead of resending.",

			"parameters": {

				"type": "object",

				"properties": {

					"file_path": {"example": 'orchestrator\\core\\registry_data.py', "type": "string", "description": "File path to edit"},

					"start_line": {"example": 5843, "type": "integer", "description": "Start line to replace (1-based)"},

					"end_line": {"example": 5843, "type": "integer", "description": "End line to replace (inclusive)"},

					"new_content": {"example": '        "doc_params_undescribed_max": 0,  # P23', "type": "string", "description": "New content to insert for that range"},

					"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "description": "Project root (optional, for logging)"},

					"dry_run": {"example": True, "type": "boolean", "default": False, "description": "True = preview diff + validate, DO NOT write the file. REQUIRED for HTML preview."},

					"content_encoding": {"example": 'base64', "type": "string", "default": "none", "description": "'none' (default) or 'base64' if new_content is base64-encoded. RECOMMENDED: use 'base64' (UTF-8) whenever new_content is multiline or contains backslashes (Windows paths, regex escapes), double quotes, or non-ASCII/Vietnamese, or when the caller is Windows PowerShell - plain JSON transport can corrupt these."},

					"expected_hash": {"example": 'a1b2c3d4', "type": "string", "default": "", "description": "Optional raw-byte SHA256 from the previous successful write_fix.new_hash for sequential writes to the same file. For normal read_file_range -> write_fix, OMIT this field and use the seeded read token. NEVER pass read_file_range.content_hash: it hashes normalized text and differs for CRLF/BOM files. An explicit mismatch is rejected; force_reseed does not override it."},

					"backup_override": {"example": False, "type": "boolean", "default": False, "description": "Skip the pre-write project backup gate after explicit user approval. Use only for tiny/temp/derived files."},

				},

				"required": ["file_path", "start_line", "end_line", "new_content"],

			},

			"returns": {
				'properties': {
					'_harness_warnings': {
						'items': {
							'type': 'string'
						},
						'type': 'array'
					},
					'backup': {
						'type': 'string'
					},
					'bytes_written': {
						'type': 'integer'
					},
					'ca_guidance': {
						'properties': {
							'accepted_aliases': {
								'properties': {
									'content': {
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										},
										'required': [
											'maps_to',
											'status'
										],
										'type': 'object'
									},
									'end': {
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										},
										'required': [
											'maps_to',
											'status'
										],
										'type': 'object'
									},
									'line_end': {
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										},
										'required': [
											'maps_to',
											'status'
										],
										'type': 'object'
									},
									'line_start': {
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										},
										'required': [
											'maps_to',
											'status'
										],
										'type': 'object'
									},
									'path': {
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										},
										'required': [
											'maps_to',
											'status'
										],
										'type': 'object'
									},
									'replacement': {
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										},
										'required': [
											'maps_to',
											'status'
										],
										'type': 'object'
									},
									'start': {
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										},
										'required': [
											'maps_to',
											'status'
										],
										'type': 'object'
									}
								},
								'required': [
									'path',
									'content',
									'replacement',
									'start',
									'end',
									'line_start',
									'line_end'
								],
								'type': 'object'
							},
							'ca_next_action': {
								'type': 'string'
							},
							'call_pattern': {
								'type': 'string'
							},
							'error_name': {
								'type': 'string'
							},
							'known_gotchas': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'notes': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'payload_example': {
								'properties': {
									'backup_override': {
										'type': 'boolean'
									},
									'content_encoding': {
										'type': 'string'
									},
									'dry_run': {
										'type': 'boolean'
									},
									'end_line': {
										'type': 'integer'
									},
									'expected_hash': {
										'type': 'string'
									},
									'file_path': {
										'type': 'string'
									},
									'new_content': {
										'type': 'string'
									},
									'project_path': {
										'type': 'string'
									},
									'skip_symbol_check': {
										'type': 'boolean'
									},
									'start_line': {
										'type': 'integer'
									}
								},
								'required': [
									'file_path',
									'start_line',
									'end_line',
									'new_content',
									'project_path',
									'dry_run',
									'content_encoding',
									'expected_hash',
									'backup_override',
									'skip_symbol_check'
								],
								'type': 'object'
							},
							'phase': {
								'type': 'string'
							},
							'phase_label': {
								'type': 'string'
							},
							'recommended_prompt_rule': {
								'type': 'string'
							},
							'required_fields': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'see_also': {
								'properties': {
									'read_file_range': {
										'type': 'string'
									},
									'search_code': {
										'type': 'string'
									},
									'write_transaction': {
										'type': 'string'
									}
								},
								'required': [
									'write_transaction',
									'search_code',
									'read_file_range'
								],
								'type': 'object'
							},
							'suggested_alternative_tool': {
								'properties': {
									'reason': {
										'type': 'string'
									},
									'tool': {
										'type': 'string'
									}
								},
								'type': 'object'
							}
						},
						'required': [
							'phase',
							'phase_label',
							'error_name',
							'ca_next_action',
							'call_pattern',
							'payload_example',
							'required_fields',
							'accepted_aliases',
							'notes',
							'known_gotchas',
							'recommended_prompt_rule',
							'see_also',
							'suggested_alternative_tool'
						],
						'type': 'object',
						'x-dual': True
					},
					'ca_hint_commit': {
						'type': 'string'
					},
					'ca_next_action': {
						'type': 'string',
						'x-dual': True
					},
					'ca_next_tool': {
						'type': 'string',
						'x-dual': True
					},
					'cache_synced': {
						'type': 'boolean'
					},
					'diff': {
						'type': 'string'
					},
					'diff_preview': {
						'properties': {
							'added': {
								'type': 'integer'
							},
							'changed_lines': {
								'type': 'integer'
							},
							'diff_unified': {
								'type': 'string'
							},
							'preview_after': {
								'type': 'string'
							},
							'preview_before': {
								'type': 'string'
							},
							'preview_hash': {
								'type': 'string'
							},
							'removed': {
								'type': 'integer'
							},
							'truncated': {
								'type': 'boolean'
							}
						},
						'required': [
							'changed_lines',
							'added',
							'removed',
							'preview_before',
							'preview_after',
							'diff_unified',
							'truncated',
							'preview_hash'
						],
						'type': 'object'
					},
					'dry_run': {
						'type': 'boolean'
					},
					'dry_run_id': {
						'type': 'string'
					},
					'encoding_check': {
						'properties': {
							'bytes_written': {
								'type': 'integer'
							},
							'encoding_used': {
								'type': 'string'
							},
							'mojibake_confirmed': {
								'type': 'boolean'
							},
							'mojibake_markers_present': {
								'type': 'boolean'
							},
							'mojibake_risk': {
								'type': 'boolean'
							},
							'suspicious_sequences': {
								'type': 'array'
							},
							'utf8_valid': {
								'type': 'boolean'
							},
							'vietnamese_diacritic_check': {
								'properties': {
									'has_vietnamese_diacritics': {
										'type': 'boolean'
									},
									'status': {
										'type': 'string'
									}
								},
								'required': [
									'has_vietnamese_diacritics',
									'status'
								],
								'type': 'object'
							}
						},
						'required': [
							'encoding_used',
							'utf8_valid',
							'mojibake_risk',
							'mojibake_markers_present',
							'mojibake_confirmed',
							'vietnamese_diacritic_check',
							'suspicious_sequences',
							'bytes_written'
						],
						'type': 'object'
					},
					'encoding_used': {
						'type': 'string'
					},
					'error': {
						'type': 'string',
						'x-dual': True
					},
					'error_name': {
						'type': 'string',
						'x-dual': True
					},
					'expires_at': {
						'type': 'string'
					},
					'file': {
						'type': 'string'
					},
					'guardrail_warning': {
						'type': 'string'
					},
					'hint': {
						'type': 'string'
					},
					'index_auto_seed_pending': {
						'type': 'boolean'
					},
					'mojibake_risk': {
						'type': 'boolean'
					},
					'new_hash': {
						'type': 'string'
					},
					'new_lines_count': {
						'type': 'integer'
					},
					'new_mtime': {
						'type': 'number'
					},
					'ok': {
						'type': 'boolean',
						'x-dual': True
					},
					'payload_example': {
						'properties': {
							'backup_override': {
								'type': 'boolean'
							},
							'content_encoding': {
								'type': 'string'
							},
							'dry_run': {
								'type': 'boolean'
							},
							'end_line': {
								'type': 'integer'
							},
							'expected_hash': {
								'type': 'string'
							},
							'file_path': {
								'type': 'string'
							},
							'new_content': {
								'type': 'string'
							},
							'project_path': {
								'type': 'string'
							},
							'skip_symbol_check': {
								'type': 'boolean'
							},
							'start_line': {
								'type': 'integer'
							}
						},
						'required': [
							'file_path',
							'start_line',
							'end_line',
							'new_content',
							'project_path',
							'dry_run',
							'content_encoding',
							'expected_hash',
							'backup_override',
							'skip_symbol_check'
						],
						'type': 'object',
						'x-dual': True
					},
					'payload_hash': {
						'type': 'string'
					},
					'phase': {
						'type': 'string',
						'x-dual': True
					},
					'preview': {
						'properties': {
							'lines_added': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'lines_removed': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'range': {
								'type': 'string'
							}
						},
						'required': [
							'range',
							'lines_removed',
							'lines_added'
						],
						'type': 'object'
					},
					'range_id': {
						'type': 'string'
					},
					'read_token_seal_error': {
						'type': 'string'
					},
					'read_token_sealed': {
						'type': 'boolean'
					},
					'replaced_lines': {
						'type': 'string'
					},
					'retry_sequence': {
						'type': 'array',
						'x-dual': True
					},
					'sequential_chain_hint': {
						'type': 'string'
					},
					'suggested_alternative_tool': {
						'properties': {
							'reason': {
								'type': 'string'
							},
							'tool': {
								'type': 'string'
							}
						},
						'required': [
							'tool',
							'reason'
						],
						'type': 'object',
						'x-dual': True
					},
					'target_file_hash': {
						'type': 'string'
					},
					'utf8_valid': {
						'type': 'boolean'
					},
					'validation': {
						'anyOf': [
							{
								'type': 'object'
							},
							{
								'type': 'null'
							}
						]
					},
					'vietnamese_diacritic_check': {
						'properties': {
							'has_vietnamese_diacritics': {
								'type': 'boolean'
							},
							'status': {
								'type': 'string'
							}
						},
						'required': [
							'has_vietnamese_diacritics',
							'status'
						],
						'type': 'object'
					},
					'warnings': {
						'type': 'array'
					},
					'write_mode': {
						'type': 'string'
					}
				},
				'required': [
					'ok'
				],
				'type': 'object',
				'x-audit': {
					'status': 'audited',
					'schema_sha256': 'f97f8a6c91a804913ee8ef3f98c114bbe3a6c23f495ebb6d057af7f8b9c554ec',
					'depth_D': 4
				}
			},
		},

	},

	{

		"type": "function",

		"function": {

			"name": "write_new_file",

            "description": "Create a new file with the provided content. NO size limit — send the whole file in one call (base64 for multiline). On validation failure the backend opens a draft (draft_id + failed_parts with line-accurate errors); fix only the failed range via write_draft_patch until clean, then it ships automatically. Supports dry_run preview, strict UTF-8 base64, diff_preview, and apply_after_dry_run. Rejects existing files unless overwrite=True. When Python validation fails with a Ruff safe-fixable diagnostic, the response includes ruff_fix_applicable and ruff_fix_hint suggesting ruff_fix(path=<file>, apply=false) for CA-controlled preview; it never auto-fixes.",

			"parameters": {

				"type": "object",

				"properties": {
					"path": {"example": 'docs\\NEW_NOTE.md', "type": "string", "description": "Relative path of the new file"},
					"new_content": {"example": '# Note\n', "type": "string", "description": "New file content"},
					"content": {"example": '# Note\n', "type": "string", "description": "Legacy alias for new_content"},
					"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "description": "Project root"},
					"content_encoding": {"example": 'plain', "type": "string", "default": "plain", "description": "'plain' (default) or 'base64' (UTF-8). RECOMMENDED: use 'base64' whenever new_content is multiline or contains backslashes (Windows paths, regex escapes), double quotes, or non-ASCII/Vietnamese, or when the caller is Windows PowerShell - plain JSON transport can corrupt these."},
					"dry_run": {"example": False, "type": "boolean", "default": False, "description": "Preview only"},
					"overwrite": {"example": False, "type": "boolean", "default": False, "description": "Allow overwrite"},
					"backup_override": {"example": False, "type": "boolean", "default": False, "description": "Dispatcher-only backup bypass approval; removed before handler binding."},
				},

				"required": ["path"],

			},

			"returns": {
				'properties': {
					'bytes_written': {
						'type': 'integer'
					},
					'ca_guidance': {
						'properties': {
							'accepted_aliases': {
								'properties': {
									'content': {
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										},
										'required': [
											'maps_to',
											'status'
										],
										'type': 'object'
									},
									'file_path': {
										'properties': {
											'maps_to': {
												'type': 'string'
											},
											'status': {
												'type': 'string'
											}
										},
										'required': [
											'maps_to',
											'status'
										],
										'type': 'object'
									}
								},
								'required': [
									'file_path',
									'content'
								],
								'type': 'object'
							},
							'ca_next_action': {
								'type': 'string'
							},
							'call_pattern': {
								'type': 'string'
							},
							'error_name': {
								'type': 'string'
							},
							'known_gotchas': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'notes': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'payload_example': {
								'properties': {
									'backup_override': {
										'type': 'boolean'
									},
									'content_encoding': {
										'type': 'string'
									},
									'dry_run': {
										'type': 'boolean'
									},
									'overwrite': {
										'type': 'boolean'
									},
									'path': {
										'type': 'string'
									},
									'project_path': {
										'type': 'string'
									}
								},
								'required': [
									'path',
									'project_path',
									'content_encoding',
									'dry_run',
									'overwrite',
									'backup_override'
								],
								'type': 'object'
							},
							'phase': {
								'type': 'string'
							},
							'phase_label': {
								'type': 'string'
							},
							'recommended_prompt_rule': {
								'type': 'string'
							},
							'required_fields': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'see_also': {
								'properties': {
									'write_transaction': {
										'type': 'string'
									}
								},
								'required': [
									'write_transaction'
								],
								'type': 'object'
							},
							'suggested_alternative_tool': {
								'properties': {},
								'type': 'object'
							}
						},
						'required': [
							'phase',
							'phase_label',
							'error_name',
							'ca_next_action',
							'call_pattern',
							'payload_example',
							'required_fields',
							'accepted_aliases',
							'notes',
							'known_gotchas',
							'recommended_prompt_rule',
							'see_also',
							'suggested_alternative_tool'
						],
						'type': 'object',
						'x-dual': True
					},
					'ca_hint_commit': {
						'type': 'string'
					},
					'ca_next_action': {
						'type': 'string',
						'x-dual': True
					},
					'ca_next_tool': {
						'type': 'string',
						'x-dual': True
					},
					'cache_synced': {
						'type': 'boolean'
					},
					'diff_preview': {
						'properties': {
							'added': {
								'type': 'integer'
							},
							'changed_lines': {
								'type': 'integer'
							},
							'diff_unified': {
								'type': 'string'
							},
							'preview_after': {
								'type': 'string'
							},
							'preview_before': {
								'type': 'string'
							},
							'preview_hash': {
								'type': 'string'
							},
							'removed': {
								'type': 'integer'
							},
							'truncated': {
								'type': 'boolean'
							}
						},
						'required': [
							'changed_lines',
							'added',
							'removed',
							'preview_before',
							'preview_after',
							'diff_unified',
							'truncated',
							'preview_hash'
						],
						'type': 'object'
					},
					'encoding_check': {
						'properties': {
							'bytes_written': {
								'type': 'integer'
							},
							'encoding_used': {
								'type': 'string'
							},
							'mojibake_confirmed': {
								'type': 'boolean'
							},
							'mojibake_markers_present': {
								'type': 'boolean'
							},
							'mojibake_risk': {
								'type': 'boolean'
							},
							'suspicious_sequences': {
								'type': 'array'
							},
							'utf8_valid': {
								'type': 'boolean'
							},
							'vietnamese_diacritic_check': {
								'properties': {
									'has_vietnamese_diacritics': {
										'type': 'boolean'
									},
									'status': {
										'type': 'string'
									}
								},
								'required': [
									'has_vietnamese_diacritics',
									'status'
								],
								'type': 'object'
							}
						},
						'required': [
							'encoding_used',
							'utf8_valid',
							'mojibake_risk',
							'mojibake_markers_present',
							'mojibake_confirmed',
							'vietnamese_diacritic_check',
							'suspicious_sequences',
							'bytes_written'
						],
						'type': 'object'
					},
					'encoding_used': {
						'type': 'string'
					},
					'error': {
						'type': 'string',
						'x-dual': True
					},
					'error_name': {
						'type': 'string',
						'x-dual': True
					},
					'file': {
						'type': 'string'
					},
					'lines_written': {
						'type': 'integer'
					},
					'mojibake_risk': {
						'type': 'boolean'
					},
					'new_hash': {
						'type': 'string'
					},
					'ok': {
						'type': 'boolean',
						'x-dual': True
					},
					'overwrite_risk': {
						'type': 'boolean'
					},
					'overwrite_warning': {
						'type': 'boolean'
					},
					'payload_example': {
						'properties': {
							'backup_override': {
								'type': 'boolean'
							},
							'content_encoding': {
								'type': 'string'
							},
							'dry_run': {
								'type': 'boolean'
							},
							'overwrite': {
								'type': 'boolean'
							},
							'path': {
								'type': 'string'
							},
							'project_path': {
								'type': 'string'
							}
						},
						'required': [
							'path',
							'project_path',
							'content_encoding',
							'dry_run',
							'overwrite',
							'backup_override'
						],
						'type': 'object',
						'x-dual': True
					},
					'phase': {
						'type': 'string',
						'x-dual': True
					},
					'read_token_seal_error': {
						'type': 'string'
					},
					'read_token_sealed': {
						'type': 'boolean'
					},
					'retry_sequence': {
						'type': 'array',
						'x-dual': True
					},
					'tag_errors': {
						'type': 'array'
					},
					'target_exists': {
						'type': 'boolean'
					},
					'truncated': {
						'type': 'boolean'
					},
					'unclosed_tags': {
						'type': 'array'
					},
					'utf8_valid': {
						'type': 'boolean'
					},
					'validation': {
						'properties': {
							'checked': {
								'type': 'boolean'
							},
							'file': {
								'type': 'string'
							},
							'lang': {
								'type': 'string'
							},
							'note': {
								'type': 'string'
							},
							'ok': {
								'type': 'boolean'
							},
							'ruff_fix_applicable': {
								'type': 'boolean'
							},
							'ruff_fix_hint': {
								'type': 'string'
							},
							'ruff_rules_matched': {
								'type': 'array'
							},
							'tag_errors': {
								'type': 'array'
							},
							'truncated': {
								'type': 'boolean'
							},
							'unclosed_tags': {
								'type': 'array'
							}
						},
						'required': [
							'ok',
							'lang',
							'file',
							'ruff_fix_applicable',
							'checked',
							'ruff_rules_matched',
							'ruff_fix_hint'
						],
						'type': 'object'
					},
					'vietnamese_diacritic_check': {
						'properties': {
							'has_vietnamese_diacritics': {
								'type': 'boolean'
							},
							'status': {
								'type': 'string'
							}
						},
						'required': [
							'has_vietnamese_diacritics',
							'status'
						],
						'type': 'object'
					},
					'write_mode': {
						'type': 'string'
					}
				},
				'required': [
					'ok'
				],
				'type': 'object',
				'x-audit': {
					'status': 'audited',
					'schema_sha256': '68aaca1d87ee9d7d82225628845a23f5aa9cfb9ef5523aee868ab8608d7639b6',
					'depth_D': 4
				}
			},
		},

	},

	{

		"type": "function",

		"function": {

			"name": "write_draft_patch",

			"description": "Fix ONLY a failed line range of a pending write draft (opened automatically when write_fix/write_new_file validation fails). Backend re-audits the whole draft, returns remaining failed_parts, and SHIPS automatically through the normal write pipeline (backup, validation, symbol check, cache sync) the moment the draft is clean. Line numbers refer to the DRAFT content (failed_parts ranges).",

			"parameters": {

				"type": "object",

				"properties": {

					"draft_id": {"type": "string", "description": "draft_id from the failed write response"},

					"start_line": {"type": "integer", "description": "First draft line to replace (1-based, from failed_parts)"},

					"end_line": {"type": "integer", "description": "Last draft line to replace (inclusive)"},

					"new_content": {"type": "string", "description": "Replacement content for that range only"},

					"content_encoding": {"type": "string", "default": "none", "description": "'none' (default) or 'base64' if new_content is base64-encoded. RECOMMENDED: use 'base64' (UTF-8) whenever new_content is multiline or contains backslashes (Windows paths, regex escapes), double quotes, or non-ASCII/Vietnamese, or when the caller is Windows PowerShell - plain JSON transport can corrupt these."},

				},

				"required": ["draft_id", "start_line", "end_line", "new_content"],

			},

		},

	},

	{

		"type": "function",

		"function": {

			"name": "write_draft_status",

			"description": "Inspect a pending write draft: remaining failed_parts with line-accurate errors and excerpts. Empty draft_id lists all pending drafts.",

			"parameters": {

				"type": "object",

				"properties": {

					"draft_id": {"type": "string", "default": "", "description": "Draft to inspect; empty = list all"},

				},

				"required": [],

			},

		},

	},

	{

		"type": "function",

		"function": {

			"name": "write_draft_abort",

			"description": "Discard a pending write draft without writing anything to the target file.",

			"parameters": {

				"type": "object",

				"properties": {

					"draft_id": {"type": "string", "description": "Draft to discard"},

				},

				"required": ["draft_id"],

			},

		},

	},

	{

		"type": "function",

		"function": {

			"name": "generate_project_map",

			"description": "Build the project cache, INDEX.json, PROJECT_MAP.md, PROJECT_CONTEXT.md and the search snapshot. Automatically skips venv, node_modules, cache, log, backup, archive, secret, and generated output folders. R80 staleness gate: before rebuilding, the index is hash-compared against disk (~2s, coalesced across concurrent callers). If nothing drifted AND every artifact exists, the rebuild is SKIPPED and the response carries skipped=true, rebuilt=false, reason=\"cache_fresh\" plus the usual index summary — safe to call at session start without herding N agents into the same O(repo) rescan. Pass force=true to rebuild unconditionally (corrupt/suspect cache).",

			"parameters": {

				"type": "object",

				"properties": {

					"project_path": {"type": "string", "description": "Project folder path to scan"},

					"force": {"type": "boolean", "default": False, "description": "Skip the staleness gate and rebuild even when the cache is fresh"},

				},

				"required": ["project_path"],

			},

		},

	},

	{

		"type": "function",

		"function": {

			"name": "refresh_project_map",

			"description": "Delete the old cache and rescan the entire project to rebuild INDEX.json. Large repos (10k+ files, big archive/cache trees) can take 60-180s+; set your client/tool-call timeout to >= 300s. The rescan finishes server-side even if the client call times out -- a follow-up read sees the completed INDEX, so do NOT lower the timeout and retry in a loop. R80 staleness gate: a hash-based drift probe (~2s) runs first and the expensive rescan is SKIPPED when the index already matches disk and all artifacts exist (skipped=true, rebuilt=false, reason=\"cache_fresh\"). Pass force=true to rescan unconditionally.",

			"parameters": {

				"type": "object",

				"properties": {

					"project_path": {"type": "string", "description": "Project folder path to rescan"},

					"force": {"type": "boolean", "default": False, "description": "Skip the staleness gate and rescan even when the cache is fresh"},

				},

				"required": ["project_path"],

			},

		},

	},

	# R43.2 C3: removed dead schema for `update_file_cache` — was stripped by

	# _HIDDEN_ORPHAN_SCHEMAS filter. Function `update_file_cache` is internal-only

	# (see comment block above TOOL_MAP). Declaring then stripping was dead code.

	{

		"type": "function",

		"function": {

			"name": "cache_status",

			"description": "Check the project cache status and the latest INDEX.json update time.",

			"parameters": {

				"type": "object",

				"properties": {

					"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "description": "Project folder path to inspect"},

				},

				"required": ["project_path"],

			},

			"returns": {
				'properties': {
					'ca_next_action': {
						'type': 'string',
						'x-dual': True
					},
					'ca_next_tool': {
						'type': 'string',
						'x-dual': True
					},
					'cache_age_hours': {
						'type': 'number'
					},
					'cache_exists': {
						'type': 'boolean'
					},
					'cache_files': {
						'type': 'integer'
					},
					'drift': {
						'properties': {
							'added': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'added_total': {
								'type': 'integer'
							},
							'deleted': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'deleted_total': {
								'type': 'integer'
							},
							'modified': {
								'items': {
									'type': 'string'
								},
								'type': 'array'
							},
							'modified_total': {
								'type': 'integer'
							}
						},
						'required': [
							'added',
							'modified',
							'deleted',
							'added_total',
							'modified_total',
							'deleted_total'
						],
						'type': 'object'
					},
					'drift_action': {
						'type': 'string'
					},
					'drift_warning': {
						'type': 'boolean'
					},
					'index_exists': {
						'type': 'boolean'
					},
					'last_updated': {
						'type': 'number'
					},
					'last_updated_iso': {
						'type': 'string'
					},
					'message': {
						'type': 'string'
					},
					'ok': {
						'type': 'boolean',
						'x-dual': True
					},
					'stale_warning': {
						'type': 'boolean'
					},
					'status': {
						'type': 'string'
					}
				},
				'required': [
					'cache_exists',
					'index_exists',
					'status',
					'message',
					'ok'
				],
				'type': 'object',
				'x-audit': {
					'status': 'audited',
					'schema_sha256': 'be8c1ef7494c028ca8c9f144e7b4b154a4c6bb5f11d2bc253533535759e7df6c',
					'depth_D': 3
				}
			},
		},

	},

	{

		"type": "function",

		"function": {

			"name": "delete_file",

			"description": "Delete one file at the requested path. Caution: permanent. R83: ephemeral=true cleans a temp/derived file (C:/temp, %TEMP%, *.bak_*, .orchestrator_cache) in ONE call with no project_path/backup_override (gates skipped only for temp-rooted absolute paths).",

			"parameters": {

				"type": "object",

				"properties": {
					"file_path": {"example": 'docs\\stale_note.md', "type": "string", "description": "File path to delete"},
					"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "default": "", "description": "Project root"},
					"ephemeral": {"example": False, "type": "boolean", "default": False, "description": "Temp cleanup mode"},
					"backup_override": {"example": False, "type": "boolean", "default": False, "description": "Dispatcher-only backup bypass approval; removed before handler binding."},
				},

				"required": ["file_path"],

			},

			"returns": {
				'properties': {
					'deleted': {
						'type': 'string'
					},
					'error': {
						'type': 'string',
						'x-dual': True
					},
					'hint': {
						'type': 'string'
					},
					'ok': {
						'type': 'boolean',
						'x-dual': True
					}
				},
				'required': [
					'ok'
				],
				'type': 'object',
				'x-audit': {
					'status': 'audited',
					'schema_sha256': '03065cb310d9c32f913b3ef4d73ff76ae9eda8b801b0bd7e0430217e2d806832',
					'depth_D': 1
				}
			},
		},

	},

	# R43.2 C3: removed dead schema for `execute_script` — internal-only helper, was

	# stripped by _HIDDEN_ORPHAN_SCHEMAS filter. Function still exists at line ~2503.

]



EXTRA_TOOL_SCHEMAS = [

{"type": "function", "function": {"name": "begin_task", "description": "Confirm or renew a server-owned task checkpoint. For two-phase root claim, send the same owner_claim_id and owner_capability returned by bootstrap_session; confirmation commits immutable ownership. Root owners issue named subtasks with delegate_agent_id; workers renew only with delegation_capability. Arbitrary agent_id never creates authority.", "parameters": {"type": "object", "properties": {"session_id": {"type": "string", "description": "Stable host conversation/session id"}, "prompt": {"type": "string", "description": "Newest real user request"}, "agent_id": {"type": "string", "default": "", "description": "Stable identity label; checked against server-owned capability state"}, "max_chars": {"type": "integer", "default": 2000, "description": "Maximum verbatim task chars in checkpoint"}, "owner_capability": {"type": "string", "default": "", "description": "Opaque capability returned by bootstrap_session; required for root begin_task/delegation"}, "owner_claim_id": {"type": "string", "default": "", "description": "Original caller-generated high-entropy claim id; required when confirming a two-phase owner claim"}, "delegation_capability": {"type": "string", "default": "", "description": "Opaque capability issued by the root for this exact subtask agent"}, "delegate_agent_id": {"type": "string", "default": "", "description": "Root-only: issue/replace an explicit delegation for this agent"}}, "required": ["session_id", "prompt", "agent_id"]}}},

{"type": "function", "function": {"name": "owner_heartbeat", "description": "Record authenticated liveness for a committed root owner. The server verifies the capability, stores a timestamp, and exposes only safe active/stale diagnostics; it never returns the capability or its hash.", "parameters": {"type": "object", "properties": {"session_id": {"type": "string", "description": "Stable host conversation/session id"}, "agent_id": {"type": "string", "description": "Committed root owner identity"}, "owner_capability": {"type": "string", "description": "Opaque capability returned by bootstrap_session and confirmed by begin_task"}, "summary": {"type": "string", "default": "", "description": "Optional short heartbeat note; do not include secrets"}}, "required": ["session_id", "agent_id", "owner_capability"]}}},

{"type": "function", "function": {"name": "get_task_checkpoint", "description": "Return the current compact active-task checkpoint for host reinsertion after tool batches or compaction. When include_owner_status=true, add safe diagnostic state for the server-owned owner claim without returning capabilities or hashes.", "parameters": {"type": "object", "properties": {"session_id": {"type": "string", "description": "Stable host conversation/session id"}, "include_owner_status": {"type": "boolean", "default": False, "description": "Include safe owner claim state, checkpoint freshness, and liveness limits; never returns opaque capabilities or hashes."}}, "required": ["session_id"]}}},

{"type": "function", "function": {"name": "get_rule_core_text", "description": "Return the rendered CORE_RULE text plus runtime path metadata and a content hash. HTTP bootstrap is canonical; pass a stable agent_id so per-agent cache does not collide.", "parameters": {"type": "object", "properties": {"task_hint": {"type": "string", "description": "Optional task text to return up to eight ranked eligible purposes plus eligible curated core tools. Omit to keep the normal bootstrap.", "example": "Find the caller of a Python function"}, "agent_id": {"example": 'muse1', "type": "string", "default": "", "description": "Your own stable agent id. agents_registry.json is SOT and manage_agent(action='list') prints the live ids; never copy an id list into this description -- measured 2026-09-20 it listed 7 ids of which 3 (comc, ope, cozo) were not registry ids at all while 6 live ids were missing, and no drift check reads a description."}, "force": {"example": False, "type": "boolean", "default": False, "description": "Force re-read when explicitly upgrading or verifying rules; otherwise omit."}}}, "returns": {'properties': {'agent_id': {'type': 'string'}, 'already_fetched': {'type': 'boolean'}, 'app_root': {'type': 'string'}, 'bootstrap_epoch': {'type': 'integer'}, 'bytes': {'type': 'integer'}, 'ca_next_action': {'type': 'string', 'x-dual': True}, 'core_rule_path': {'type': 'string'}, 'ctx_id': {'type': 'string'}, 'error': {'type': 'string'}, 'error_name': {'type': 'string'}, 'hint': {'type': 'string'}, 'mtime_ns': {'type': 'integer'}, 'ok': {'type': 'boolean'}, 'orchestrator_dir': {'type': 'string'}, 'phase': {'type': 'string'}, 'reason': {'type': 'string'}, 'retryable': {'type': 'boolean'}, 'severity': {'type': 'string'}, 'sha256': {'type': 'string'}, 'start_script': {'type': 'string'}, 'text': {'type': 'string'}, 'text_omitted': {'type': 'boolean'}, 'text_omitted_reason': {'type': 'string'}, 'tool_purposes': {'type': 'object'}, 'tool_purposes_error': {'type': 'string'}, 'tool_purposes_note': {'type': 'string'}, 'type': {'type': 'string'}}, 'required': ['ok'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': '0f28d8648ac8524a132109789aa73403e5a49180922d6725e70d7a95b40a1478', 'depth_D': 2}}}},

    {"type": "function", "function": {"name": "audit_tool_matrix", "description": "V15 R13 / R22 / E-04 — Cross-check TOOL_MAP / TOOL_SCHEMAS / TOOL_PHASES against skill SKILL.md trigger.tool, SKILLS.md router, harness *.md, and .ToolA_Skill/references/tool_surface_current.md. Returns drift list + summary + matrix_log_path. Auto-invoked by prepare_ca_session and open_session. R22 verbosity ∈ {full|terse|ref}: terse caps drift/alias_drift arrays at 3 + drops coverage/inventory_classification (~1500 tokens saved). Drift safety: auto-force full when drift_count>0 or progressive_gap_count>0. E-04: persist defaults False — a read call is pure read; pass persist=True to append the record to matrix_log.jsonl (auto-invocations from prepare_ca_session/open_session keep persist=True).", "parameters": {"type": "object", "properties": {"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "default": ""}, "include_coverage": {"example": True, "type": "boolean", "default": False, "description": "Include per-tool coverage map (heavier response)"}, "verbosity": {"example": 'terse', "type": "string", "enum": ["full", "terse", "ref"], "default": "full", "description": "R22: terse caps arrays + drops coverage; ref drops all arrays."}, "persist": {"example": False, "type": "boolean", "default": False, "description": "E-04: when True, append the audit record to .orchestrator_cache/matrix_log.jsonl. Default False makes this tool a pure read."}}, "required": []}, "returns": {'properties': {'alias_drift': {'type': 'array'}, 'alias_drift_truncated_at': {'type': 'integer'}, 'ca_next_action': {'type': 'string', 'x-dual': True}, 'contract_drift': {'items': {'properties': {'detail': {'type': 'string'}, 'field': {'type': 'string'}, 'severity': {'type': 'string'}, 'skill': {'type': 'string'}, 'type': {'type': 'string'}, 'value': {'type': 'string'}}, 'required': ['type', 'skill', 'field', 'value', 'severity', 'detail'], 'type': 'object'}, 'type': 'array'}, 'coverage_detail': {'properties': {'returns_coverage': {'properties': {'covered': {'type': 'integer'}, 'depth_suspect': {'type': 'integer'}, 'disabled': {'type': 'integer'}, 'eligible': {'type': 'integer'}}, 'required': ['covered', 'depth_suspect', 'disabled', 'eligible'], 'type': 'object'}}, 'required': ['returns_coverage'], 'type': 'object'}, 'drift': {'type': 'array'}, 'drift_truncated_at': {'type': 'integer'}, 'generated_at': {'type': 'string'}, 'inventory_classification': {'properties': {'active_in_code': {'items': {'type': 'string'}, 'type': 'array'}, 'extras_doc_only': {'type': 'array'}, 'extras_endpoints': {'type': 'array'}, 'extras_internal': {'type': 'array'}, 'extras_or_endpoints': {'type': 'array'}, 'extras_planned': {'type': 'array'}, 'extras_unknown': {'type': 'array'}, 'retired_referenced': {'type': 'array'}}, 'required': ['active_in_code', 'retired_referenced', 'extras_or_endpoints', 'extras_endpoints', 'extras_internal', 'extras_planned', 'extras_doc_only', 'extras_unknown'], 'type': 'object'}, 'matrix_log_path': {'type': 'string'}, 'ok': {'type': 'boolean', 'x-dual': True}, 'persisted': {'type': 'boolean'}, 'progressive_gaps': {'type': 'array'}, 'router_topics_count': {'type': 'integer'}, 'scope': {'type': 'string'}, 'scope_note': {'type': 'string'}, 'summary': {'properties': {'alias_drift_count': {'type': 'integer'}, 'alias_drift_severity': {'type': 'string'}, 'code_tools': {'type': 'integer'}, 'contract_drift_count': {'type': 'integer'}, 'doc_description_present_count': {'type': 'integer'}, 'doc_description_weak_count': {'type': 'integer'}, 'doc_fields_absent_by_design_count': {'type': 'integer'}, 'doc_param_examples_registry_count': {'type': 'integer'}, 'doc_param_examples_served_count': {'type': 'integer'}, 'doc_params_described_count': {'type': 'integer'}, 'doc_params_total': {'type': 'integer'}, 'doc_params_undescribed_count': {'type': 'integer'}, 'drift_count': {'type': 'integer'}, 'error_phase_map_entries': {'type': 'integer'}, 'error_system_names_phase_mapped_count': {'type': 'integer'}, 'error_system_names_total': {'type': 'integer'}, 'error_system_names_unmapped_count': {'type': 'integer'}, 'inventory_active_count': {'type': 'integer'}, 'inventory_extra_count': {'type': 'integer'}, 'inventory_extra_doc_only_count': {'type': 'integer'}, 'inventory_extra_endpoints_count': {'type': 'integer'}, 'inventory_extra_internal_count': {'type': 'integer'}, 'inventory_extra_planned_count': {'type': 'integer'}, 'inventory_extra_unknown_count': {'type': 'integer'}, 'inventory_retired_count': {'type': 'integer'}, 'inventory_tools': {'type': 'integer'}, 'ok_count': {'type': 'integer'}, 'progressive_gap_count': {'type': 'integer'}, 'purpose_covered_count': {'type': 'integer'}, 'purpose_eligible_count': {'type': 'integer'}, 'purpose_eligible_gap_count': {'type': 'integer'}, 'schema_tools': {'type': 'integer'}, 'skill_folders': {'type': 'integer'}, 'skill_tools': {'type': 'integer'}}, 'required': ['code_tools', 'schema_tools', 'skill_tools', 'skill_folders', 'inventory_tools', 'inventory_active_count', 'inventory_retired_count', 'inventory_extra_count', 'inventory_extra_endpoints_count', 'inventory_extra_internal_count', 'inventory_extra_planned_count', 'inventory_extra_doc_only_count', 'inventory_extra_unknown_count', 'drift_count', 'ok_count', 'alias_drift_count', 'alias_drift_severity', 'contract_drift_count', 'progressive_gap_count', 'doc_description_present_count', 'doc_description_weak_count', 'doc_params_total', 'doc_params_described_count', 'doc_params_undescribed_count', 'doc_param_examples_registry_count', 'doc_param_examples_served_count', 'doc_fields_absent_by_design_count', 'purpose_eligible_count', 'purpose_covered_count', 'purpose_eligible_gap_count', 'error_phase_map_entries', 'error_system_names_total', 'error_system_names_phase_mapped_count', 'error_system_names_unmapped_count'], 'type': 'object'}, 'verbosity_mode': {'type': 'string'}}, 'required': ['ok'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': 'e46a733f549e421ae57d7744e3f3ffe835e27ed844a3e187a66091e573bcfc19', 'depth_D': 3}}}},

    {"type": "function", "function": {"name": "prepare_ca_session", "description": "Canonical one-call startup: prepare project context, open/resume the agent session, generate or reuse a caller-side owner_claim_id, then run bootstrap_session. The response exposes owner_claim_id and owner_capability so the host can persist them for begin_task/retry.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}, "mode": {"type": "string", "default": "T1"}, "idea": {"type": "string", "default": ""}, "agent_id": {"type": "string", "default": "", "description": "Stable calling agent identity; transport header and environment fallback may supply it"}, "host_session_id": {"type": "string", "default": "", "description": "Stable host conversation/session id required to run bootstrap_session and return bootstrap_ref"}, "owner_claim_id": {"type": "string", "default": "", "description": "Caller-generated stable claim id; helper generates one before bootstrap when omitted and returns it for retry reuse"}}, "required": ["project_path"]}}},

    {"type": "function", "function": {"name": "set_workflow_mode", "description": "Set workflow mode in WORKFLOW_STATE.json.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}, "mode": {"type": "string"}, "idea": {"type": "string", "default": ""}}, "required": ["project_path", "mode"]}}},

    {"type": "function", "function": {"name": "workflow_status_registry", "description": "R43.2 C2: T1 registry inspector — cache/drift/mode/backup state from .orchestrator_cache state file. Renamed from workflow_status to disambiguate from R35 MD workflow runner.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}}, "required": ["project_path"]}}},

    # R43.2 C3: removed dead schemas for `create_session_bootstrap` + `update_workflow_state`

    # — internal-only helpers, were stripped by _HIDDEN_ORPHAN_SCHEMAS filter (now also removed).

    {"type": "function", "function": {"name": "next_recommended_action", "description": "Return next deterministic workflow action.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}}, "required": ["project_path"]}}},

    {"type": "function", "function": {"name": "capability_status", "description": "Report parser/indexing capabilities and missing optional libraries.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string", "default": ""}}}}},

    {"type": "function", "function": {"name": "suggest_missing_capabilities", "description": "Suggest optional parser libraries without installing them.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string", "default": ""}}}}},

    {"type": "function", "function": {"name": "backup_status", "description": "Read Orchestrator backup manifest status.", "parameters": {"type": "object", "properties": {"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string"}}, "required": ["project_path"]}, "returns": {'properties': {'all_backups': {'items': {'properties': {'backup_age_hours': {'anyOf': [{'type': 'number'}, {'type': 'null'}]}, 'backup_path': {'type': 'string'}, 'backup_scope': {'type': 'string'}, 'created_at': {'type': 'string'}, 'files_skipped': {'type': 'integer'}, 'files_written': {'type': 'integer'}, 'size_mb': {'anyOf': [{'type': 'number'}, {'type': 'null'}]}, 'skipped_sample': {'items': {'type': 'string'}, 'type': 'array'}, 'target_paths': {'items': {'type': 'string'}, 'type': 'array'}}, 'required': ['created_at', 'backup_path', 'size_mb', 'backup_age_hours'], 'type': 'object'}, 'type': 'array'}, 'latest': {'anyOf': [{'properties': {'backup_age_hours': {'anyOf': [{'type': 'number'}, {'type': 'null'}]}, 'backup_path': {'type': 'string'}, 'backup_scope': {'type': 'string'}, 'created_at': {'type': 'string'}, 'files_skipped': {'type': 'integer'}, 'files_written': {'type': 'integer'}, 'size_mb': {'anyOf': [{'type': 'number'}, {'type': 'null'}]}, 'skipped_sample': {'items': {'type': 'string'}, 'type': 'array'}, 'target_paths': {'items': {'type': 'string'}, 'type': 'array'}}, 'required': ['created_at', 'backup_path', 'size_mb', 'backup_age_hours'], 'type': 'object'}, {'type': 'null'}]}, 'ok': {'type': 'boolean', 'x-dual': True}, 'status': {'type': 'string'}, 'total_backups': {'type': 'integer'}}, 'required': ['ok', 'status'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': '033bdaff7c28f963a4eda148fae35b33063688897f9225d3985c160a46086946', 'depth_D': 4}}}},

    {"type": "function", "function": {"name": "resolve_project_path", "description": "Resolve any Windows file/folder path into project_path plus file_path/folder_path metadata without guessing.", "parameters": {"type": "object", "properties": {"input_path": {"type": "string"}, "preferred_root": {"type": "string", "default": ""}, "mode": {"type": "string", "default": "auto", "enum": ["file", "folder", "auto"]}}, "required": ["input_path"]}}},

    {"type": "function", "function": {"name": "protected_tools_status", "description": "Return machine-readable mutation-token protection status without exposing the raw token.", "parameters": {"type": "object", "properties": {}}}},

    {"type": "function", "function": {"name": "path_quote_helper", "description": "V15 R16.9: Generate a Windows-safe run_project_command wrapper for subfolders or paths with spaces. shell=powershell|cmd (R16.9 adds cmd mode with pushd/popd + double-quote escaping for .bat with spaces). Does not execute.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string", "default": ""}, "subfolder": {"type": "string", "default": ""}, "command": {"type": "string"}, "shell": {"type": "string", "enum": ["powershell", "pwsh", "cmd"], "default": "powershell"}}, "required": ["command"]}}},

    {"type": "function", "function": {"name": "optional_tool_probe", "description": "Check whether optional helper tools or endpoints are available without mutating state or fetching raw tokens.", "parameters": {"type": "object", "properties": {"tools": {"type": "array", "items": {"type": "string"}, "description": "Tool names or /api endpoint paths to probe."}}, "required": ["tools"]}}},

	{"type": "function", "function": {"name": "backup_project", "description": "Create timestamped zip backup and manifest. The 600-second timeout matches the handler default.", "parameters": {"type": "object", "properties": {"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string"}, "backup_dir": {"example": 'D:\\backups\\toola', "type": "string"}, "force": {"example": False, "type": "boolean", "default": False, "description": "Overwrite a backup from the same day"}, "keep_last_n": {"example": 2, "type": "integer", "default": 2, "description": "Maximum backups to keep"}, "timeout": {"example": 600, "type": "integer", "default": 600, "description": "Maximum archive time in seconds; matches handler default"}, "target_paths": {"example": ['orchestrator\\core\\registry_data.py'], "type": "array", "items": {"type": "string"}, "description": "Optional project-relative archive targets"}}, "required": ["project_path"]}, "returns": {'properties': {'backup_path': {'type': 'string'}, 'backup_scope': {'type': 'string'}, 'created_at': {'type': 'string'}, 'files_skipped': {'type': 'integer'}, 'files_written': {'type': 'integer'}, 'ok': {'type': 'boolean', 'x-dual': True}, 'reason': {'type': 'string'}, 'skipped': {'type': 'boolean'}, 'skipped_sample': {'items': {'type': 'string'}, 'type': 'array'}, 'target_paths': {'type': 'array'}, 'total_backups': {'type': 'integer'}}, 'required': ['ok'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': 'e7a886b3165661b6d93a290885994525e3eb38e865cd25ca4f6865b0e50cecf2', 'depth_D': 2}}}},

	{"type": "function", "function": {"name": "cache_delta", "description": "Compare filesystem against INDEX for added/modified/deleted files.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}}, "required": ["project_path"]}}},

	{"type": "function", "function": {

        "name": "run_project_command",

        "description": "Run bounded command in project cwd. V15 R14: shell wrapper plus optional background/keep_alive launch verification via expected_port / expected_process_name. JSON transport is a separate layer: encode backslashes before placing command in an outer JSON string. On Windows, shell operators and findstr/dir file reads are fragile; prefer direct search/read tools or script-file transport. Auto routes only a standalone script.ps1 to PowerShell -File; a command such as findstr text script.ps1 remains a normal command. CAN-053 readiness DSL: health_url + expect_status (+ readiness_protocol=http|tcp|cdp|process) verify service readiness over HTTP; CDP_UNREACHABLE is returned ONLY for cdp, dead TCP ports return PORT_UNREACHABLE, failed health probes return HEALTH_PROBE_FAILED; launcher-exited-but-health-ok returns ok_with_warning=true. CASE-2008 destructive guard: a command containing a delete/move/truncate/overwrite statement in ANY runtime (cmd rd/del, PowerShell Remove-Item, node fs.rmSync, python shutil.rmtree, sh rm -rf, git clean) refuses to run until confirm_destructive equals the scanned count, takes a project backup first, and returns untruncated output; dry_run=true previews without executing. A PowerShell -Include paired with -LiteralPath is REFUSED outright (DESTRUCTIVE_SCRIPT_FOOTGUN) because PowerShell silently discards the filter and removes the whole target.",

        "parameters": {

            "type": "object",

            "properties": {

                "project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string"},

                "command": {"example": 'git status --porcelain', },

                "timeout": {"example": 60, "type": "integer", "default": 60, "description": "Max run time in SECONDS (not ms). Use this, or timeout_ms for milliseconds."},

                "timeout_ms": {"example": 0, "type": "integer", "default": 0, "description": "R66: optional max run time in MILLISECONDS; converted to seconds and overrides `timeout` when > 0. Provided so CA's T4-style timeout_ms habit no longer 400s."},

                "shell_mode": {"example": 'auto', "type": "string", "enum": ["auto", "cmd", "powershell", "exec", "posix"], "default": "auto", "description": "Windows auto selects PowerShell -File only for a standalone .ps1 path; commands that merely target a .ps1 file stay normal shell commands."},

                "command_encoding": {"description": "How the command string is transported: 'plain' (default) or 'base64' (UTF-8) when the command contains quotes, backslashes, or non-ASCII that plain JSON would corrupt.", "example": 'plain', "type": "string", "enum": ["plain", "base64"], "default": "plain"},

                "expected_port": {"example": 0, "type": "integer", "default": 0, "description": "Optional port expected to be reachable after launch, e.g. CDP 9223"},

                "expected_process_name": {"example": 'node.exe', "type": "string", "default": "", "description": "Optional process name or cmdline substring expected after launch"},

                "verify_after_launch_ms": {"example": 0, "type": "integer", "default": 0, "description": "Optional delay before launch verification"},

                "readiness_protocol": {"example": 'http', "type": "string", "enum": ["", "http", "tcp", "cdp", "process"], "default": "", "description": "CAN-053 readiness DSL: explicit protocol for launch verification; empty = infer (health_url->http, port 9222/9223->cdp, port->tcp)"},

                "health_url": {"example": 'http://127.0.0.1:8082/health', "type": "string", "default": "", "description": "CAN-053: HTTP readiness probe URL (e.g. http://127.0.0.1:8082/health). Probe success verifies launch even when the wrapper already exited (ok_with_warning)."},

                "expect_status": {"example": 200, "type": "integer", "default": 200, "description": "Expected HTTP status for health_url probe"},

                "keep_alive": {"example": False, "type": "boolean", "default": False, "description": "Spawn GUI/long-running process in background and do not communicate/auto-kill it"},

                "background": {"example": False, "type": "boolean", "default": False, "description": "Alias for keep_alive; spawn detached/background and return spawned_pid"},

                "interactive": {"example": False, "type": "boolean", "default": False, "description": "Windows keep_alive launches with a visible console/user-session process instead of DETACHED_PROCESS"},

                "elevated": {"example": False, "type": "boolean", "default": False, "description": "Windows only: request ShellExecuteW runas launch; no stdout/stderr capture or PID guarantee. Not needed for admin work: a normal child already inherits the High IL of [A]"},

                "include_command": {"example": True, "type": "boolean", "default": True, "description": "R43 P0 #4: when False, suppresses command_received/command/command_executed echo in response; replaced by command_hash_sha256 + command_preview (50 chars). Saves ~500-2000 bytes/call for long node -e / base64 scripts."},

                "stdout_max_chars": {"example": 8000, "type": "integer", "default": 0, "description": "R43 P0 #4: explicit truncation cap for stdout (0 = use default 12000)."},

                "stderr_max_chars": {"example": 2000, "type": "integer", "default": 0, "description": "R43 P0 #4: explicit truncation cap for stderr (0 = use default 12000)."},

                "dry_run": {"example": False, "type": "boolean", "default": False, "description": "CASE-2008: static no-execute preview of the destructive-statement scan. Nothing runs."},

                "confirm_destructive": {"example": -1, "type": "integer", "default": -1, "description": "CASE-2008: acknowledged destructive-statement count. A command containing rd/del/Remove-Item/rm -rf/fs.rmSync/shutil.rmtree etc. refuses to run until this EQUALS the scanner count (returned as confirm_destructive_required). A count rather than a boolean, so editing the command invalidates a stale acknowledgement."},

                "skip_backup": {"example": False, "type": "boolean", "default": False, "description": "CASE-2008: skip the automatic pre-run project backup for a destructive command. Only for genuinely disposable targets (e.g. inside .orchestrator_cache)."},

            },

            "required": ["project_path", "command"],

            "accepted_shell_modes": ["auto", "cmd", "powershell", "exec", "posix"],

            "accepted_encodings": ["plain", "base64"],

        },

            "returns": {
                'properties': {
                    'background': {
                        'type': 'boolean'
                    },
                    'command': {
                        'items': {
                            'type': 'string'
                        },
                        'type': 'array'
                    },
                    'command_executed': {
                        'items': {
                            'type': 'string'
                        },
                        'type': 'array'
                    },
                    'command_received': {
                        'anyOf': [
                            {
                                'type': 'string'
                            },
                            {
                                'items': {
                                    'type': 'string'
                                },
                                'type': 'array'
                            }
                        ]
                    },
                    'cwd': {
                        'type': 'string'
                    },
                    'duration_seconds': {
                        'type': 'number'
                    },
                    'elevated': {
                        'type': 'boolean'
                    },
                    'exit_code': {
                        'anyOf': [
                            {
                                'type': 'integer'
                            },
                            {
                                'type': 'null'
                            }
                        ]
                    },
                    'expected_port': {
                        'anyOf': [
                            {
                                'type': 'integer'
                            },
                            {
                                'type': 'null'
                            }
                        ]
                    },
                    'expected_process_name': {
                        'type': 'string'
                    },
                    'interactive': {
                        'type': 'boolean'
                    },
                    'launch_verified': {
                        'anyOf': [
                            {
                                'type': 'boolean'
                            },
                            {
                                'type': 'null'
                            }
                        ]
                    },
                    'ok': {
                        'type': 'boolean',
                        'x-dual': True
                    },
                    'path_resolution_note': {
                        'type': 'string'
                    },
                    'port_probe': {
                        'anyOf': [
                            {
                                'type': 'object'
                            },
                            {
                                'type': 'null'
                            }
                        ]
                    },
                    'process_probe': {
                        'anyOf': [
                            {
                                'type': 'object'
                            },
                            {
                                'type': 'null'
                            }
                        ]
                    },
                    'project_path': {
                        'type': 'string'
                    },
                    'redirection_requested': {
                        'type': 'boolean'
                    },
                    'returncode': {
                        'anyOf': [
                            {
                                'type': 'integer'
                            },
                            {
                                'type': 'null'
                            }
                        ]
                    },
                    'root_causes': {
                        'type': 'array'
                    },
                    'shell': {
                        'type': 'string'
                    },
                    'shell_mode_requested': {
                        'type': 'string'
                    },
                    'shell_mode_resolved': {
                        'type': 'string'
                    },
                    'spawned_pid': {
                        'type': 'integer'
                    },
                    'stderr': {
                        'type': 'string'
                    },
                    'stdout': {
                        'type': 'string'
                    },
                    'timed_out': {
                        'type': 'boolean'
                    }
                },
                'required': [
                    'ok'
                ],
                'type': 'object',
                'x-audit': {
                    'status': 'audited',
                    'schema_sha256': '1c16296de6e3a9041be73c8f9ff09fb1dec2b9236d4535d8705dedd1b5269e7a',
                    'depth_D': 2
                }
            },
    }},

	{"type": "function", "function": {"name": "kill_process", "description": "Terminate process by pid or process_name. Protected T2 cleanup tool for runtime/test environments.", "parameters": {"type": "object", "properties": {"pid": {"description": "OS process id to terminate. Use this or process_name; default 0 means no pid target was given.", "type": "integer", "default": 0}, "process_name": {"description": "Process name (e.g. 'electron.exe') to terminate when the pid is not known; empty string means no name target was given.", "type": "string", "default": ""}, "force": {"type": "boolean", "default": True}}}}},

	{"type": "function", "function": {"name": "run_test_profile", "description": "Run known bounded test profile.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}, "profile": {"type": "string", "default": "unittest"}, "timeout": {"type": "integer", "default": 120}}, "required": ["project_path"]}}},

	{"type": "function", "function": {"name": "create_benchmark_request", "description": "Create BENCHMARK_REQUEST.md for T2 without crawling.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}, "idea": {"type": "string", "default": ""}}, "required": ["project_path"]}}},

	{"type": "function", "function": {"name": "create_build_context", "description": "Create BUILD_CONTEXT.md from user-approved inputs only.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}, "idea": {"type": "string", "default": ""}, "approved_inputs": {"type": "string", "default": ""}}, "required": ["project_path"]}}},

	{"type": "function", "function": {"name": "get_project_summary", "description": "Quick INDEX overview: groups, hotspots, and entry points. Use this instead of reading INDEX.json manually.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}}, "required": ["project_path"]}}},

	{"type": "function", "function": {"name": "query_index", "description": "Filter files from INDEX by group, complexity, LOC, fnmatch pattern, and optional text query (query MAY be empty). V15 R11: auto_refresh=true (default) silently refreshes INDEX inline when drift detected; response sets auto_refreshed=true so CA knows. Set auto_refresh=false to receive STALE_INDEX error instead. file_pattern uses fnmatch against both the file name and the full relative path (e.g. '*.py', '*service*', '*autocutter*', 'src/*.ts'). CAN-051 mutation_scope=derived_index: auto_refresh rewrites INDEX.json (derived state, not source).", "parameters": {"type": "object", "properties": {"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string"}, "query": {"example": 'registry', "type": "string", "default": "", "description": "Optional text query to filter files. Aliases: pattern, regex, q."}, "group": {"example": 'source', "type": "string"}, "min_complexity": {"example": 2, "type": "integer"}, "min_loc": {"example": 50, "type": "integer"}, "file_pattern": {"example": '*.py', "type": "string", "description": "fnmatch pattern matched against basename or full relative path"}, "max_results": {"example": 50, "type": "integer", "default": 50}, "auto_refresh": {"example": True, "type": "boolean", "default": True, "description": "When true and INDEX is stale, refresh inline then query."}}, "required": ["project_path"]}, "returns": {'properties': {'auto_refreshed': {'type': 'boolean'}, 'ca_hint': {'type': 'string', 'x-dual': True}, 'ca_next_action': {'type': 'string', 'x-dual': True}, 'ca_next_tool': {'type': 'string', 'x-dual': True}, 'category': {'type': 'string'}, 'content_files_scanned': {'type': 'integer'}, 'drift': {'type': 'object'}, 'error': {'type': 'string'}, 'error_name': {'type': 'string'}, 'files': {'items': {'properties': {'complexity': {'type': 'integer'}, 'group': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}, 'loc': {'type': 'integer'}, 'matched_symbols': {'items': {'type': 'string'}, 'type': 'array'}, 'matched_via': {'items': {'type': 'string'}, 'type': 'array'}, 'path': {'type': 'string'}}, 'required': ['path', 'group', 'loc', 'complexity'], 'type': 'object'}, 'type': 'array'}, 'hint': {'type': 'string'}, 'matched': {'type': 'integer'}, 'note': {'type': 'string'}, 'ok': {'type': 'boolean'}, 'query_scope': {'items': {'type': 'string'}, 'type': 'array'}, 'returned': {'type': 'integer'}, 'snapshot_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}, 'total_files': {'type': 'integer'}, 'truncated': {'type': 'boolean'}}, 'required': ['ok', 'snapshot_id'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': 'ef3a776280b701d6af04782a04838ce5089b336173d51b8b8bf9bc773a1b7c41', 'depth_D': 4}}}},

	{"type": "function", "function": {"name": "get_usage_stats", "description": "Read APP_SESSION_LOG.jsonl and SQLite session events for ONE project (project-scoped only). Commit E (S3-CONTRACT-01): project-scoped by default; project_path is required for scope='project'. scope='all' (cross-project global) is DEFERRED until orchestrator-token auth + audit lands and returns GLOBAL_SCOPE_NOT_ENABLED. E-01: include_global_usage and include_http_log are tombstoned escape hatches — they leaked process-global root/UI/HTTP data into project scope and now fail loud with GLOBAL_SCOPE_NOT_ENABLED on explicit use; they are no longer advertised in this schema.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string", "description": "Absolute project root path. Canonicalized before filtering; required for scope='project'."}, "scope": {"type": "string", "enum": ["project", "all"], "default": "project", "description": "'project' (default) scopes results to project_path. 'all' is deferred (GLOBAL_SCOPE_NOT_ENABLED) until auth+audit lands."}, "last_n": {"type": "integer", "default": 200}}, "required": ["project_path"]}}},

	{"type": "function", "function": {"name": "get_folder_size", "description": "Count files and total folder size recursively without blocking the API loop. Safe defaults skip generated/cache trees; include_skipped=true scans them.", "parameters": {"type": "object", "properties": {"folder_path": {"type": "string"}, "include_skipped": {"type": "boolean", "default": False, "description": "When true, include .git, virtual environments, node_modules and cache trees."}}, "required": ["folder_path"]}}},

	{"type": "function", "function": {"name": "validate_file", "description": "Check syntax/validity of a file after write_fix/write_new_file. Python: py_compile. JS: node --check. TS/JSX/TSX/Vue: esbuild syntax check (never a type check). Call immediately after a write to catch errors early.", "parameters": {"type": "object", "properties": {"file_path": {"example": 'orchestrator\\core\\registry_data.py', "type": "string"}, "lang": {"example": 'auto', "type": "string", "description": "auto|python|js|ts|esbuild|html|vue|json|css|toml (common aliases accepted; ts routes to the esbuild syntax check)", "default": "auto"}, "project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "description": "Project root to resolve relative file_path", "default": ""}}, "required": ["file_path"]}, "returns": {'properties': {'checked': {'type': 'boolean'}, 'file': {'type': 'string'}, 'lang': {'type': 'string'}, 'ok': {'type': 'boolean', 'x-dual': True}, 'ruff_fix_applicable': {'type': 'boolean'}, 'ruff_fix_hint': {'type': 'string'}, 'ruff_rules_matched': {'type': 'array'}}, 'required': ['ok'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': 'f23f471f9828559b8ba11c3932718fd1f6a517e14dad7b73baea814d0c708fca', 'depth_D': 2}}}},

	{"type": "function", "function": {"name": "validate_symbols", "description": "Post-write integration check: syntax plus function/class comparison against INDEX. Detects accidentally deleted functions. Use after write_fix on .py/.js/.ts files.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "project_path": {"type": "string", "default": ""}}, "required": ["file_path"]}}},

	{"type": "function", "function": {"name": "validate_encoding", "description": "Validate UTF-8, BOM, mojibake risk, and Vietnamese diacritic integrity for a text file after writes.", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "project_path": {"type": "string", "default": ""}, "expected_encoding": {"type": "string", "default": "utf-8"}, "language_hint": {"type": "string", "default": ""}}, "required": ["file_path"]}}},

	{"type": "function", "function": {"name": "post_write_batch_verify", "description": "Run standard non-mutating post-write checks for a batch: validate_file per file, optional validate_symbols, optional validate_encoding, and one cache_delta.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}, "changed_files": {"type": "array", "items": {"type": "string"}}, "run_validate_symbols": {"type": "boolean", "default": True}, "run_cache_delta": {"type": "boolean", "default": True}, "run_validate_encoding": {"type": "boolean", "default": True}}, "required": ["project_path", "changed_files"]}}},

    {"type": "function", "function": {"name": "write_fix_html", "description": "Edit a code range in an .html file. Automatically runs dry_run and checks closing delimiters (<script> <style> <body> <html>) before writing. Use force=True to bypass. When Python validation fails with a Ruff safe-fixable diagnostic, the response includes ruff_fix_applicable and ruff_fix_hint suggesting ruff_fix(path=<file>, apply=false) for CA-controlled preview; it never auto-fixes.", "parameters": {"type": "object", "properties": {"file_path": {"example": 'ui\\index.html', "type": "string"}, "start_line": {"example": 10, "type": "integer"}, "end_line": {"example": 12, "type": "integer"}, "new_content": {"example": '<div>updated</div>', "type": "string"}, "project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "default": ""}, "content_encoding": {"example": 'none', "type": "string", "default": "none"}, "force": {"example": False, "type": "boolean", "default": False}, "backup_override": {"example": False, "type": "boolean", "default": False, "description": "Skip the pre-write project backup gate after explicit user approval. Use only for tiny/temp/derived files."}}, "required": ["file_path", "start_line", "end_line", "new_content"]}, "returns": {'properties': {'backup': {'type': 'string'}, 'bytes_written': {'type': 'integer'}, 'ca_guidance': {'properties': {'accepted_aliases': {'properties': {'content': {'properties': {'maps_to': {'type': 'string'}, 'status': {'type': 'string'}}, 'required': ['maps_to', 'status'], 'type': 'object'}, 'end': {'properties': {'maps_to': {'type': 'string'}, 'status': {'type': 'string'}}, 'required': ['maps_to', 'status'], 'type': 'object'}, 'line_end': {'properties': {'maps_to': {'type': 'string'}, 'status': {'type': 'string'}}, 'required': ['maps_to', 'status'], 'type': 'object'}, 'line_start': {'properties': {'maps_to': {'type': 'string'}, 'status': {'type': 'string'}}, 'required': ['maps_to', 'status'], 'type': 'object'}, 'path': {'properties': {'maps_to': {'type': 'string'}, 'status': {'type': 'string'}}, 'required': ['maps_to', 'status'], 'type': 'object'}, 'replacement': {'properties': {'maps_to': {'type': 'string'}, 'status': {'type': 'string'}}, 'required': ['maps_to', 'status'], 'type': 'object'}, 'start': {'properties': {'maps_to': {'type': 'string'}, 'status': {'type': 'string'}}, 'required': ['maps_to', 'status'], 'type': 'object'}}, 'required': ['path', 'content', 'start', 'end', 'line_start', 'line_end', 'replacement'], 'type': 'object'}, 'ca_next_action': {'type': 'string'}, 'call_pattern': {'type': 'string'}, 'error_name': {'type': 'string'}, 'known_gotchas': {'items': {'type': 'string'}, 'type': 'array'}, 'notes': {'items': {'type': 'string'}, 'type': 'array'}, 'payload_example': {'properties': {'backup_override': {'type': 'boolean'}, 'content_encoding': {'type': 'string'}, 'end_line': {'type': 'integer'}, 'file_path': {'type': 'string'}, 'force': {'type': 'boolean'}, 'new_content': {'type': 'string'}, 'project_path': {'type': 'string'}, 'start_line': {'type': 'integer'}}, 'required': ['file_path', 'start_line', 'end_line', 'new_content', 'project_path', 'content_encoding', 'force', 'backup_override'], 'type': 'object'}, 'phase': {'type': 'string'}, 'phase_label': {'type': 'string'}, 'recommended_prompt_rule': {'type': 'string'}, 'required_fields': {'items': {'type': 'string'}, 'type': 'array'}, 'see_also': {'properties': {}, 'type': 'object'}, 'suggested_alternative_tool': {'properties': {}, 'type': 'object'}}, 'required': ['phase', 'phase_label', 'error_name', 'ca_next_action', 'call_pattern', 'payload_example', 'required_fields', 'accepted_aliases', 'notes', 'known_gotchas', 'recommended_prompt_rule', 'see_also', 'suggested_alternative_tool'], 'type': 'object', 'x-dual': True}, 'ca_hint_commit': {'type': 'string'}, 'ca_next_action': {'type': 'string', 'x-dual': True}, 'cache_synced': {'type': 'boolean'}, 'changed_lines': {'type': 'integer'}, 'delimiter_check': {'type': 'string'}, 'diff': {'type': 'string'}, 'diff_preview': {'properties': {'added': {'type': 'integer'}, 'changed_lines': {'type': 'integer'}, 'diff_unified': {'type': 'string'}, 'preview_after': {'type': 'string'}, 'preview_before': {'type': 'string'}, 'preview_hash': {'type': 'string'}, 'removed': {'type': 'integer'}, 'truncated': {'type': 'boolean'}}, 'required': ['changed_lines', 'added', 'removed', 'preview_before', 'preview_after', 'diff_unified', 'truncated', 'preview_hash'], 'type': 'object'}, 'encoding_check': {'properties': {'bytes_written': {'type': 'integer'}, 'encoding_used': {'type': 'string'}, 'mojibake_confirmed': {'type': 'boolean'}, 'mojibake_markers_present': {'type': 'boolean'}, 'mojibake_risk': {'type': 'boolean'}, 'suspicious_sequences': {'type': 'array'}, 'utf8_valid': {'type': 'boolean'}, 'vietnamese_diacritic_check': {'properties': {'has_vietnamese_diacritics': {'type': 'boolean'}, 'status': {'type': 'string'}}, 'required': ['has_vietnamese_diacritics', 'status'], 'type': 'object'}}, 'required': ['encoding_used', 'utf8_valid', 'mojibake_risk', 'mojibake_markers_present', 'mojibake_confirmed', 'vietnamese_diacritic_check', 'suspicious_sequences', 'bytes_written'], 'type': 'object'}, 'encoding_used': {'type': 'string'}, 'error': {'type': 'string', 'x-dual': True}, 'error_name': {'type': 'string', 'x-dual': True}, 'file': {'type': 'string'}, 'html_tool': {'type': 'boolean'}, 'mojibake_risk': {'type': 'boolean'}, 'new_hash': {'type': 'string'}, 'new_lines_count': {'type': 'integer'}, 'new_mtime': {'type': 'number'}, 'ok': {'type': 'boolean', 'x-dual': True}, 'payload_example': {'properties': {'backup_override': {'type': 'boolean'}, 'content_encoding': {'type': 'string'}, 'end_line': {'type': 'integer'}, 'file_path': {'type': 'string'}, 'force': {'type': 'boolean'}, 'new_content': {'type': 'string'}, 'project_path': {'type': 'string'}, 'start_line': {'type': 'integer'}}, 'required': ['file_path', 'start_line', 'end_line', 'new_content', 'project_path', 'content_encoding', 'force', 'backup_override'], 'type': 'object', 'x-dual': True}, 'phase': {'type': 'string', 'x-dual': True}, 'range_id': {'type': 'string'}, 'replaced_lines': {'type': 'string'}, 'retry_sequence': {'type': 'array', 'x-dual': True}, 'sequential_chain_hint': {'type': 'string'}, 'symbol_check': {'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'type': 'object'}, 'symbols_changed': {'type': 'array'}, 'symbols_deleted': {'type': 'array'}, 'syntax_ok': {'type': 'boolean'}, 'tag_errors': {'type': 'array'}, 'truncated': {'type': 'boolean'}, 'unclosed_tags': {'type': 'array'}, 'utf8_valid': {'type': 'boolean'}, 'validation': {'properties': {'checked': {'type': 'boolean'}, 'file': {'type': 'string'}, 'lang': {'type': 'string'}, 'note': {'type': 'string'}, 'ok': {'type': 'boolean'}, 'ruff_fix_applicable': {'type': 'boolean'}, 'ruff_fix_hint': {'type': 'string'}, 'ruff_rules_matched': {'type': 'array'}, 'tag_errors': {'type': 'array'}, 'truncated': {'type': 'boolean'}, 'unclosed_tags': {'type': 'array'}}, 'required': ['ok', 'lang', 'file', 'ruff_fix_applicable', 'checked', 'ruff_rules_matched', 'ruff_fix_hint'], 'type': 'object'}, 'vietnamese_diacritic_check': {'properties': {'has_vietnamese_diacritics': {'type': 'boolean'}, 'status': {'type': 'string'}}, 'required': ['has_vietnamese_diacritics', 'status'], 'type': 'object'}, 'warnings': {'type': 'array'}, 'write_mode': {'type': 'string'}}, 'required': ['ok'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': '9ae8eb1b905d8a532191ef533b49e4ddc2279c38b26cdd65d314dc23d1a4ce42', 'depth_D': 4}}}},

{"type": "function", "function": {"name": "apply_after_dry_run", "description": "Apply an exact previously previewed dry-run payload by dry_run_id. Rejects stale target files and expired previews.", "parameters": {"type": "object", "properties": {"project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string"}, "dry_run_id": {"example": 'dry_20261105_001', "type": "string"}, "confirm_hash": {"example": 'a1b2c3d4', "type": "string", "default": ""}, "confirm_warnings": {"example": False, "type": "boolean", "default": False}, "backup_override": {"example": False, "type": "boolean", "default": False, "description": "Dispatcher-only backup bypass approval; removed before handler binding."}}, "required": ["project_path", "dry_run_id"]}, "returns": {'properties': {'admission': {'properties': {'category': {'type': 'string'}, 'held': {'type': 'integer'}, 'held_for_sec': {'type': 'number'}, 'holder_op_id': {'type': 'string'}, 'limit': {'type': 'integer'}, 'oldest_wait_ms': {'type': 'number'}, 'queue_capacity': {'type': 'integer'}, 'queue_rejected': {'type': 'integer'}, 'waiters': {'type': 'integer'}}, 'required': ['category', 'limit', 'held', 'waiters', 'queue_capacity', 'queue_rejected', 'holder_op_id', 'held_for_sec', 'oldest_wait_ms'], 'type': 'object'}, 'ca_guidance': {'properties': {'accepted_aliases': {'properties': {}, 'type': 'object'}, 'ca_next_action': {'type': 'string'}, 'call_pattern': {'type': 'string'}, 'error_name': {'type': 'string'}, 'known_gotchas': {'type': 'array'}, 'notes': {'items': {'type': 'string'}, 'type': 'array'}, 'payload_example': {'properties': {'backup_override': {'type': 'boolean'}, 'confirm_hash': {'type': 'string'}, 'confirm_warnings': {'type': 'boolean'}, 'dry_run_id': {'type': 'string'}, 'project_path': {'type': 'string'}, 'skip_verify': {'type': 'boolean'}}, 'required': ['project_path', 'dry_run_id', 'confirm_hash', 'confirm_warnings', 'backup_override', 'skip_verify'], 'type': 'object'}, 'phase': {'type': 'string'}, 'phase_label': {'type': 'string'}, 'recommended_prompt_rule': {'type': 'string'}, 'required_fields': {'items': {'type': 'string'}, 'type': 'array'}, 'see_also': {'properties': {}, 'type': 'object'}, 'suggested_alternative_tool': {'properties': {}, 'type': 'object'}}, 'required': ['phase', 'phase_label', 'error_name', 'ca_next_action', 'call_pattern', 'payload_example', 'required_fields', 'accepted_aliases', 'notes', 'known_gotchas', 'recommended_prompt_rule', 'see_also', 'suggested_alternative_tool'], 'type': 'object', 'x-dual': True}, 'ca_next_action': {'type': 'string', 'x-dual': True}, 'error': {'type': 'string', 'x-dual': True}, 'error_name': {'type': 'string', 'x-dual': True}, 'ok': {'type': 'boolean', 'x-dual': True}, 'payload_example': {'properties': {'backup_override': {'type': 'boolean'}, 'confirm_hash': {'type': 'string'}, 'confirm_warnings': {'type': 'boolean'}, 'dry_run_id': {'type': 'string'}, 'project_path': {'type': 'string'}, 'skip_verify': {'type': 'boolean'}}, 'required': ['project_path', 'dry_run_id', 'confirm_hash', 'confirm_warnings', 'backup_override', 'skip_verify'], 'type': 'object', 'x-dual': True}, 'phase': {'type': 'string', 'x-dual': True}, 'reason': {'type': 'string'}, 'retry_after_seconds': {'type': 'integer'}, 'retry_sequence': {'type': 'array', 'x-dual': True}, 'retryable': {'type': 'boolean'}, 'severity': {'type': 'string'}, 'tool_category': {'type': 'string'}, 'type': {'type': 'string'}}, 'required': ['ok'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': '90e48ec654f27f1f2c50bcb3ce323889d13011705cee00c389adbf638cb2b420', 'depth_D': 3}}}},

]



EXTRA_TOOL_SCHEMAS_R43 = [

		{"type": "function", "function": {"name": "open_session", "description": "Open or resume the active session for a project and optional agent identity. Resume preserves title and mode by default; call rename_session to apply a different title. Opt-in force_new_session closes then creates atomically and refuses pending writes, drafts or audits; apply_requested_title renames in place without changing mode. Flags are mutually exclusive and require agent_id. Distinct non-empty agent_id values may hold concurrent active sessions on one project. R22 verbosity ∈ {full|terse|ref}; drift auto-forces full.", "parameters": {"type": "object", "properties": {"session_title": {"example": 'doc backfill', "type": "string", "default": "", "description": "Session title (if empty -> 'Untitled Session {time}')"}, "project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "default": ".", "description": "Project root (default '.')"}, "mode": {"example": 'T1', "type": "string", "enum": ["T1", "T2"], "default": "T1", "description": "Workflow TIER, never an execution verb. ONLY T1 or T2 are valid values. Aliases are accepted and folded: T1_FIX / T1_EXISTING_APP -> T1 (work inside an app that already exists), T2_BUILD / T2_NEW_APP -> T2 (build a new one). 'execute', 'write' and 'fix' are INVALID_MODE. Resume ignores mode; change an open session via set_workflow_mode."}, "verbosity": {"example": 'full', "type": "string", "enum": ["full", "terse", "ref"], "default": "full", "description": "R22: terse compresses metadata; drift auto-forces full."}, "agent_id": {"example": 'muse1', "type": "string", "default": "", "description": "Stable agent identity used to isolate concurrent active sessions"}, "force_new_session": {"example": False, "type": "boolean", "default": False, "description": "Explicitly close the active session before creating a new one; returns closed_session_id. Refuses pending work. Cannot combine with apply_requested_title."}, "apply_requested_title": {"example": False, "type": "boolean", "default": False, "description": "Explicitly apply a non-empty requested title on resume. Preserves session_id, mode and events; returns old_title, new_title and renamed."}}}, "returns": {'properties': {'agent_id': {'type': 'string'}, 'ca_next_action': {'type': 'string', 'x-dual': True}, 'ca_next_tool': {'type': 'string', 'x-dual': True}, 'cslt_policy': {'properties': {'a_first': {'type': 'boolean'}, 'agent_transport_policy': {'type': 'string'}, 'host_gate': {'properties': {'agent_id': {'type': 'string'}, 'allowed': {'type': 'boolean'}, 'decision': {'type': 'string'}, 'is_hermes': {'type': 'boolean'}, 'native_policy': {'type': 'string'}, 'reason': {'type': 'string'}, 'scope': {'type': 'string'}}, 'required': ['agent_id', 'is_hermes', 'scope', 'decision', 'allowed', 'reason', 'native_policy'], 'type': 'object'}, 'matrix': {'properties': {'path': {'type': 'string'}, 'task_class_count': {'type': 'integer'}}, 'required': ['path', 'task_class_count'], 'type': 'object'}, 'metrics_contract': {'properties': {'bypass_event': {'type': 'string'}, 'bypass_rate_formula': {'type': 'string'}, 'group_by': {'items': {'type': 'string'}, 'type': 'array'}, 'required_counters': {'items': {'type': 'string'}, 'type': 'array'}, 'zero_denominator': {'type': 'string'}}, 'required': ['bypass_event', 'bypass_rate_formula', 'required_counters', 'zero_denominator', 'group_by'], 'type': 'object'}, 'north_star': {'properties': {'acronym': {'type': 'string'}, 'note': {'type': 'string'}, 'order': {'items': {'type': 'string'}, 'type': 'array'}, 'text': {'type': 'string'}}, 'required': ['acronym', 'order', 'text', 'note'], 'type': 'object'}, 'rules': {'items': {'type': 'string'}, 'type': 'array'}}, 'required': ['north_star', 'agent_transport_policy', 'matrix', 'a_first', 'host_gate', 'metrics_contract', 'rules'], 'type': 'object'}, 'effective_mode': {'type': 'string'}, 'effective_project_path': {'type': 'string'}, 'effective_session_title': {'type': 'string'}, 'matrix_audit': {'properties': {'alias_drift_count': {'type': 'integer'}, 'alias_drift_severity': {'type': 'string'}, 'audit_scope': {'type': 'string'}, 'code_tools': {'type': 'integer'}, 'contract_drift_count': {'type': 'integer'}, 'doc_description_present_count': {'type': 'integer'}, 'doc_description_weak_count': {'type': 'integer'}, 'doc_fields_absent_by_design_count': {'type': 'integer'}, 'doc_param_examples_registry_count': {'type': 'integer'}, 'doc_param_examples_served_count': {'type': 'integer'}, 'doc_params_described_count': {'type': 'integer'}, 'doc_params_total': {'type': 'integer'}, 'doc_params_undescribed_count': {'type': 'integer'}, 'drift_count': {'type': 'integer'}, 'error_phase_map_entries': {'type': 'integer'}, 'error_system_names_phase_mapped_count': {'type': 'integer'}, 'error_system_names_total': {'type': 'integer'}, 'error_system_names_unmapped_count': {'type': 'integer'}, 'inventory_active_count': {'type': 'integer'}, 'inventory_extra_count': {'type': 'integer'}, 'inventory_extra_doc_only_count': {'type': 'integer'}, 'inventory_extra_endpoints_count': {'type': 'integer'}, 'inventory_extra_internal_count': {'type': 'integer'}, 'inventory_extra_planned_count': {'type': 'integer'}, 'inventory_extra_unknown_count': {'type': 'integer'}, 'inventory_retired_count': {'type': 'integer'}, 'inventory_tools': {'type': 'integer'}, 'ok_count': {'type': 'integer'}, 'progressive_gap_count': {'type': 'integer'}, 'purpose_covered_count': {'type': 'integer'}, 'purpose_eligible_count': {'type': 'integer'}, 'purpose_eligible_gap_count': {'type': 'integer'}, 'schema_tools': {'type': 'integer'}, 'skill_folders': {'type': 'integer'}, 'skill_tools': {'type': 'integer'}}, 'required': [], 'type': 'object'}, 'matrix_drift_present': {'type': 'boolean'}, 'mode_applied': {'type': 'boolean'}, 'mode_ignored_on_resume': {'type': 'boolean'}, 'mode_matches_persisted': {'type': 'boolean'}, 'ok': {'type': 'boolean', 'x-dual': True}, 'path_resolution_note': {'type': 'string'}, 'progressive_gap_count': {'type': 'integer'}, 'progressive_gaps_hint': {'type': 'string'}, 'project_name': {'type': 'string'}, 'project_path': {'type': 'string'}, 'project_path_confirmed': {'type': 'boolean'}, 'project_switch_context': {'properties': {'current_project_active_workflow_hint': {'type': 'string'}, 'current_project_recent_files_5': {'items': {'type': 'string'}, 'type': 'array'}, 'current_project_recent_queries_5': {'items': {'properties': {'query': {'type': 'string'}, 'tool': {'type': 'string'}}, 'required': ['tool', 'query'], 'type': 'object'}, 'type': 'array'}, 'recent_projects_today': {'items': {'properties': {'ended_at': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}, 'is_current': {'type': 'boolean'}, 'last_session_id': {'type': 'string'}, 'project_path': {'type': 'string'}, 'started_at': {'type': 'string'}, 'status': {'type': 'string'}}, 'required': ['project_path', 'last_session_id', 'started_at', 'ended_at', 'status', 'is_current'], 'type': 'object'}, 'type': 'array'}}, 'required': ['recent_projects_today', 'current_project_recent_files_5', 'current_project_recent_queries_5', 'current_project_active_workflow_hint'], 'type': 'object'}, 'relative_base_hint': {'type': 'string'}, 'rename_session_payload': {'properties': {'agent_id': {'type': 'string'}, 'new_title': {'type': 'string'}, 'project_path': {'type': 'string'}}, 'required': ['project_path', 'new_title', 'agent_id'], 'type': 'object'}, 'requested_mode': {'type': 'string'}, 'requested_project_path': {'type': 'string'}, 'requested_session_title': {'type': 'string'}, 'resolved_from': {'type': 'string'}, 'resume_policy': {'type': 'string'}, 'resumed': {'type': 'boolean'}, 'root_exists': {'type': 'boolean'}, 'session_id': {'type': 'string'}, 'session_title': {'type': 'string'}, 'session_title_mismatch': {'type': 'boolean'}, 'started_at': {'type': 'string'}, 'transport_policy': {'properties': {'ca_next_action': {'type': 'string'}, 'platform': {'type': 'string'}, 'posix': {'properties': {'powershell_inline': {'type': 'string'}, 'preferred_tool': {'type': 'string'}, 'preferred_transport': {'type': 'string'}, 'shell_mode': {'type': 'string'}}, 'required': ['powershell_inline', 'preferred_transport', 'preferred_tool', 'shell_mode'], 'type': 'object'}, 'schema_version': {'type': 'string'}, 'windows': {'properties': {'enforcement': {'properties': {'error_names': {'items': {'type': 'string'}, 'type': 'array'}, 'tool': {'type': 'string'}, 'triggers_common': {'items': {'type': 'string'}, 'type': 'array'}, 'triggers_ps_only': {'items': {'type': 'string'}, 'type': 'array'}}, 'required': ['tool', 'error_names', 'triggers_common', 'triggers_ps_only'], 'type': 'object'}, 'node_inline': {'type': 'string'}, 'one_shot_recipe_node': {'properties': {'args': {'properties': {'project_path': {'type': 'string'}, 'runtime': {'type': 'string'}, 'script_content': {'type': 'string'}, 'script_label': {'type': 'string'}}, 'required': ['project_path', 'runtime', 'script_content', 'script_label'], 'type': 'object'}, 'behaviour': {'type': 'string'}, 'tool': {'type': 'string'}}, 'required': ['tool', 'args', 'behaviour'], 'type': 'object'}, 'one_shot_recipe_powershell': {'properties': {'args': {'properties': {'project_path': {'type': 'string'}, 'runtime': {'type': 'string'}, 'script_content': {'type': 'string'}, 'script_label': {'type': 'string'}}, 'required': ['project_path', 'runtime', 'script_content', 'script_label'], 'type': 'object'}, 'behaviour': {'type': 'string'}, 'tool': {'type': 'string'}}, 'required': ['tool', 'args', 'behaviour'], 'type': 'object'}, 'path_as_argv_warning': {'type': 'string'}, 'powershell_inline': {'type': 'string'}, 'preferred_tool': {'type': 'string'}, 'preferred_transport': {'type': 'string'}, 'rationale': {'type': 'string'}}, 'required': ['powershell_inline', 'node_inline', 'preferred_transport', 'preferred_tool', 'enforcement', 'rationale', 'path_as_argv_warning', 'one_shot_recipe_powershell', 'one_shot_recipe_node'], 'type': 'object'}}, 'required': ['platform', 'windows', 'posix', 'ca_next_action', 'schema_version'], 'type': 'object'}}, 'required': ['ok'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': 'd4cceedd7d2254b3b54c995aa5bde31002c3e9b5c36b97c3a8851882bb961081', 'depth_D': 5}}}},

	{"type": "function", "function": {"name": "close_session", "description": "Close the active session after log_native(native=[...]) has been called. V15 R64-D: now accepts either project_path (primary session lookup) OR session_id (direct lookup for sub-sessions like [Ac]/[Ad] opened mid-workflow). At least one of the two MUST be provided.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string", "default": "", "description": "Project root of the session to close. Use for the primary [Ab] session."}, "session_id": {"type": "string", "default": "", "description": "V15 R64-D: direct session_id lookup. Use when you opened multiple sessions (e.g. [Ac], [Ad] sub-sessions) and need to close a specific one without re-deriving its project_path. Pulled from open_session's response."}, "agent_id": {"type": "string", "default": "", "description": "Stable agent identity; scopes active-session selection when non-empty."}}}}},

	{"type": "function", "function": {"name": "rename_session", "description": "Rename the active session. Works only while the session is still active.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}, "new_title": {"type": "string"}, "agent_id": {"type": "string", "default": "", "description": "Stable agent identity; scopes active-session selection when non-empty."}}, "required": ["project_path", "new_title"]}}},

	{"type": "function", "function": {"name": "clear_closed_sessions", "description": "Lifecycle cleanup: permanently DELETE all closed sessions (and their events) for one project. Active sessions are preserved. Use to reclaim sessions.db space after a project has accumulated many closed sessions. PC1-CLEANUP (2026-08-11): registered as a public tool — handler existed but was unreachable via /api/call.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string", "description": "Project root whose CLOSED sessions should be deleted (active session is never touched)."}}, "required": ["project_path"]}}},

	{"type": "function", "function": {"name": "log_native", "description": "At the end of each CA session: mark the session complete and record the list of native fallbacks. Use native=[] when no native tool was used. Each item: {tool, instead_of, reason}.", "parameters": {"type": "object", "properties": {"native": {"type": "array", "items": {"type": "object"}, "default": [], "description": "Array [{tool, instead_of, reason}]. Pass [] when no native tool was used."}}}}},

	{"type": "function", "function": {"name": "log_native_event", "description": "Record one native action in the session immediately when native is used instead of an [A] Rule C tool. Call inline right after the action.", "parameters": {"type": "object", "properties": {"action_type": {"example": 'read', "type": "string", "description": "read|write|execute|validate|cache|backup|workflow"}, "tool_name": {"example": 'read_file_range', "type": "string", "description": "Name of the native tool/command used, e.g. 'Edit', 'Bash', 'Read'"}, "target": {"example": 'orchestrator\\core\\registry_data.py', "type": "string", "default": ""}, "duration_ms": {"example": 1200, "type": "integer", "default": 0}, "status": {"example": 'ok', "type": "string", "default": "ok", "description": "ok|error|warn"}, "reason": {"example": 'native read used for a small bounded range', "type": "string", "default": ""}, "instead_of": {"example": 'read_file_range', "type": "string", "default": ""}, "failed_payload": {"example": {'tool': 'read_file_range', 'file_path': 'missing.py'}, "type": "object", "default": {}}, "failed_error": {"example": 'ENOENT: no such file or directory', "type": "string", "default": ""}, "native_command": {"example": 'Get-Content registry_data.py | Select-Object -First 20', "type": "string", "default": ""}, "scope": {"example": 'project', "type": "string", "default": ""}, "risk": {"example": 'low', "type": "string", "default": ""}, "transport": {"example": 'native', "type": "string", "default": "native", "description": "R81: 'native' (true bypass of [A]) | 'via_a' (work done THROUGH an [A] tool e.g. write_new_file/run_project_test_script — logged for traceability, tagged via_A, NOT counted as a native fallback)."}, "project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string"}, "agent_id": {"example": 'muse1', "type": "string", "default": "", "description": "Stable agent identity; logs to that agent's active session."}}, "required": ["action_type", "tool_name", "project_path"]}, "returns": {'properties': {'audit_status': {'type': 'string'}, 'ca_guidance': {'properties': {'accepted_aliases': {'properties': {}, 'type': 'object'}, 'ca_next_action': {'type': 'string'}, 'call_pattern': {'type': 'string'}, 'error_name': {'type': 'string'}, 'known_gotchas': {'items': {'type': 'string'}, 'type': 'array'}, 'notes': {'items': {'type': 'string'}, 'type': 'array'}, 'payload_example': {'properties': {'action_type': {'type': 'string'}, 'agent_id': {'type': 'string'}, 'duration_ms': {'type': 'integer'}, 'failed_error': {'type': 'string'}, 'failed_payload': {'properties': {}, 'type': 'object'}, 'instead_of': {'type': 'string'}, 'native_command': {'type': 'string'}, 'project_path': {'type': 'string'}, 'reason': {'type': 'string'}, 'risk': {'type': 'string'}, 'scope': {'type': 'string'}, 'status': {'type': 'string'}, 'target': {'type': 'string'}, 'tool_name': {'type': 'string'}, 'transport': {'type': 'string'}}, 'required': ['action_type', 'tool_name', 'project_path', 'target', 'duration_ms', 'status', 'reason', 'instead_of', 'failed_payload', 'failed_error', 'native_command', 'scope', 'risk', 'transport', 'agent_id'], 'type': 'object'}, 'phase': {'type': 'string'}, 'phase_label': {'type': 'string'}, 'recommended_prompt_rule': {'type': 'string'}, 'required_fields': {'items': {'type': 'string'}, 'type': 'array'}, 'see_also': {'properties': {}, 'type': 'object'}, 'suggested_alternative_tool': {'properties': {}, 'type': 'object'}}, 'required': ['phase', 'phase_label', 'error_name', 'ca_next_action', 'call_pattern', 'payload_example', 'required_fields', 'accepted_aliases', 'notes', 'known_gotchas', 'recommended_prompt_rule', 'see_also', 'suggested_alternative_tool'], 'type': 'object', 'x-dual': True}, 'ca_next_action': {'type': 'string', 'x-dual': True}, 'cslt_classification': {'properties': {'ca_next_action': {'type': 'string'}, 'host_gate': {'type': 'string'}, 'is_allowed_native': {'type': 'boolean'}, 'is_forbidden_native': {'type': 'boolean'}, 'is_unclassified': {'type': 'boolean'}, 'is_via_a': {'type': 'boolean'}, 'native_policy': {'type': 'string'}, 'task_class': {'type': 'string'}}, 'required': ['task_class', 'native_policy', 'is_forbidden_native', 'is_allowed_native', 'is_via_a', 'is_unclassified', 'host_gate', 'ca_next_action'], 'type': 'object'}, 'error': {'type': 'string', 'x-dual': True}, 'error_name': {'type': 'string', 'x-dual': True}, 'fallback_count_so_far': {'type': 'integer'}, 'logged': {'type': 'string'}, 'ok': {'type': 'boolean', 'x-dual': True}, 'payload_example': {'properties': {'action_type': {'type': 'string'}, 'agent_id': {'type': 'string'}, 'duration_ms': {'type': 'integer'}, 'failed_error': {'type': 'string'}, 'failed_payload': {'properties': {}, 'type': 'object'}, 'instead_of': {'type': 'string'}, 'native_command': {'type': 'string'}, 'project_path': {'type': 'string'}, 'reason': {'type': 'string'}, 'risk': {'type': 'string'}, 'scope': {'type': 'string'}, 'status': {'type': 'string'}, 'target': {'type': 'string'}, 'tool_name': {'type': 'string'}, 'transport': {'type': 'string'}}, 'required': ['action_type', 'tool_name', 'project_path', 'target', 'duration_ms', 'status', 'reason', 'instead_of', 'failed_payload', 'failed_error', 'native_command', 'scope', 'risk', 'transport', 'agent_id'], 'type': 'object', 'x-dual': True}, 'phase': {'type': 'string', 'x-dual': True}, 'policy_warning': {'type': 'string'}, 'preferred_via_a': {'items': {'type': 'string'}, 'type': 'array'}, 'retry_sequence': {'type': 'array', 'x-dual': True}, 'retryable': {'type': 'boolean'}, 'session_id': {'type': 'string'}, 'severity': {'type': 'string'}, 'transport': {'type': 'string'}, 'transport_warning': {'type': 'string'}, 'type': {'type': 'string'}}, 'required': ['ok'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': 'e5449b0da6d03c2d015b69b44f0b7e88662f2c132b205c3fe54c2c2516ca66b0', 'depth_D': 4}}}},

	{"type": "function", "function": {"name": "create_pending_audit", "description": "Store a pending native fallback audit entry for later sync when the API is available. Does not store raw tokens, secrets, or file content.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}, "action_type": {"type": "string"}, "tool_name": {"type": "string"}, "instead_of": {"type": "string", "default": ""}, "failed_error": {"type": "string", "default": ""}, "target": {"type": "string", "default": ""}, "reason": {"type": "string", "default": ""}, "agent_id": {"type": "string", "default": "", "description": "Stable agent identity stored with this pending audit."}}, "required": ["project_path", "action_type", "tool_name"]}}},

	{"type": "function", "function": {"name": "sync_pending_audit", "description": "Sync agent-scoped pending native audit entries. Legacy unscoped rows require target_session_id plus include_legacy=true.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}, "pending_audit_id": {"type": "string", "default": ""}, "limit": {"type": "integer", "default": 50}, "agent_id": {"type": "string", "default": "", "description": "Stable agent identity; must match the target session agent."}, "target_session_id": {"type": "string", "default": "", "description": "Explicit session that receives synced events."}, "include_legacy": {"type": "boolean", "default": False, "description": "Include legacy rows with no agent_id; requires target_session_id."}}, "required": ["project_path"]}}},

	{"type": "function", "function": {"name": "list_sessions", "description": "List the most recent sessions for ONE project (without events). Use this to inspect session history. Commit E (S3-CONTRACT-01): rows are filtered by the canonical project_path BEFORE ordering/limit, so a cross-project session can never leak. project_path is required for scope='project'. scope='all' is DEFERRED until orchestrator-token auth + audit lands and returns GLOBAL_SCOPE_NOT_ENABLED.", "parameters": {"type": "object", "properties": {"project_path": {"type": "string", "description": "Absolute project root path. Canonicalized before filtering; required for scope='project'."}, "scope": {"type": "string", "enum": ["project", "all"], "default": "project", "description": "'project' (default) scopes results to project_path. 'all' is deferred (GLOBAL_SCOPE_NOT_ENABLED) until auth+audit lands."}, "limit": {"type": "integer", "default": 10}}, "required": ["project_path"]}}},

	{"type": "function", "function": {"name": "get_session_events", "description": "Paginated events for one session. CAN-054: pass target_session_id for the session you want to READ (unambiguous domain param); session_id stays accepted but doubles as host transport metadata. Or pass project_path plus agent_id to resolve an active session.", "parameters": {"type": "object", "properties": {"session_id": {"type": "string", "default": ""}, "target_session_id": {"type": "string", "default": "", "description": "CAN-054: explicit target session to read; wins over session_id"}, "project_path": {"type": "string", "default": "", "description": "Auto-resolve the active session from project_path when session_id is empty"}, "agent_id": {"type": "string", "default": "", "description": "Stable agent identity used when resolving by project_path."}, "limit": {"type": "integer", "default": 50}, "offset": {"type": "integer", "default": 0}}}}},

]



V15_R8_SCHEMAS = [

    {"type": "function", "function": {"name": "validate", "description": "V15 R8: Unified validate dispatcher. checks=['file','symbols','encoding'] — runs the selected sub-validators and aggregates one response. Replaces validate_file/validate_symbols/validate_encoding. NOTE: file_path is RELATIVE inside project_path (pass project_path absolute, file_path relative).",

     "parameters": {"type": "object", "properties": {

         "file_path": {"type": "string", "description": "Relative path inside project_path (e.g. 'code_tools.py')"},

         "project_path": {"type": "string", "description": "Absolute project root", "default": ""},

         "checks": {"description": "Which sub-validators the unified dispatcher runs: any of 'file', 'symbols', 'encoding'. Default ['file'] runs only the file validator.", "type": "array", "items": {"type": "string", "enum": ["file", "symbols", "encoding"]}, "default": ["file"]},

         "lang": {"type": "string", "default": "auto"},

         "expected_encoding": {"type": "string", "default": "utf-8"},

         "language_hint": {"type": "string", "default": ""},

     }, "required": ["file_path"]}}},

    {"type": "function", "function": {"name": "project_map", "description": "V15 R8: Unified project_map dispatcher. mode=status|generate|refresh|delta. Replaces generate_project_map/refresh_project_map/cache_delta/cache_status.",

     "parameters": {"type": "object", "properties": {

         "project_path": {"type": "string"},

         "mode": {"type": "string", "enum": ["status", "generate", "refresh", "delta"], "default": "status"},

         "backup_override": {"description": "When true, allow the project-map operation to proceed even where a fresh backup would normally be required first; default false keeps the backup guard.", "type": "boolean", "default": False},

         "force": {"type": "boolean", "default": False},

     }, "required": ["project_path"]}}},

    {"type": "function", "function": {"name": "state", "description": "V15 R8/R11: Unified state dispatcher. aspect=cache|workflow|recommendation|session|all. Replaces cache_status/workflow_status/next_recommended_action when aggregated read is needed. R11 added 'session' aspect = recent events summary.",

     "parameters": {"type": "object", "properties": {

         "project_path": {"type": "string"},

         "aspect": {"description": "Which state slice to aggregate: 'cache', 'workflow', 'recommendation', 'session', or 'all' (default) for every slice in one response.", "type": "string", "enum": ["cache", "workflow", "recommendation", "session", "all"], "default": "all"},

     }, "required": ["project_path"]}}},

    {"type": "function", "function": {"name": "council_tracker", "description": "READ/COLLECT council live logs. Runs the council tracker collector and returns the path to the council_live.txt snapshot (broker dispatches + full prompts, raw worker ARGV/output, council_events, latest replies). Call this FIRST when investigating council/broker/worker-dispatch/prompt/reply issues instead of hand-scraping .council. Auto-open is disabled (open_file is a no-op); archive behavior is kept (old snapshot zipped to archives/, fresh one written). save_output=false skips collection and just reads the current snapshot.",

     "returns": {'properties': {'archive_created': {'type': 'string'}, 'archive_dir': {'type': 'string'}, 'bytes': {'type': 'integer'}, 'collected': {'type': 'boolean'}, 'excerpt': {'type': 'string'}, 'full_content_available': {'type': 'boolean'}, 'hint': {'type': 'string'}, 'historical': {'type': 'boolean'}, 'line_count': {'type': 'integer'}, 'ok': {'type': 'boolean', 'x-dual': True}, 'open_file': {'type': 'boolean'}, 'output_path': {'type': 'string'}}, 'required': ['ok', 'output_path', 'archive_dir', 'archive_created', 'bytes', 'line_count', 'collected', 'open_file', 'historical', 'hint'], 'type': 'object', 'x-audit': {'status': 'audited', 'schema_sha256': 'e15e0924de164861cc3949089b4c0f909919f9a0ebf56c6b6f2da1f030d93ec1', 'depth_D': 1}},
     "parameters": {"type": "object", "properties": {

         "open_file": {"example": False, "type": "boolean", "default": False, "description": "Ignored no-op (kept for backward compat); the tool never auto-opens a desktop window."},

         "tail_lines": {"example": 80, "type": "integer", "default": 80, "description": "How many trailing lines to include in the `excerpt` response field."},

         "return_full": {"example": False, "type": "boolean", "default": False, "description": "When true, return the entire snapshot in `content` instead of a tail `excerpt`."},

         "save_output": {"example": True, "type": "boolean", "default": True, "description": "true = fresh collection (archive old + write new). false = read current snapshot only."},

     }}}},

    {"type": "function", "function": {"name": "council_status", "description": "T1 read-only deterministic dispatch-state query for one council dispatch id.",
     "parameters": {"type": "object", "properties": {
         "dispatch_id": {"type": "string", "description": "Dispatch correlation id returned by council_message or council_message_async."},
     }, "required": ["dispatch_id"]}}},

    {"type": "function", "function": {"name": "council_route_scorecard", "description": "T1 measurement scorecard for council harness+model routes. Groups evidence by objective/risk/dev and keeps numeric capacity unknown until enough homogeneous samples.",
     "parameters": {"type": "object", "properties": {"project_path": {"type": "string", "default": ""}, "min_samples": {"description": "Minimum homogeneous samples a route group needs before the scorecard reports numeric capacity for it; groups below this stay 'unknown'. Default 5.", "type": "integer", "default": 5}, "limit": {"type": "integer", "default": 0}, "write_artifacts": {"description": "When true (default), persist the scorecard artifacts to disk; false returns the scorecard in the response only.", "type": "boolean", "default": True}}}}},

    {"type": "function", "function": {"name": "mission_create", "description": "P3b-2 T3: create a durable mission and idempotent tasks in .orchestrator_cache/missions.db. Each task must include output_artifact_path.",

     "parameters": {"type": "object", "properties": {

         "title": {"description": "Human-readable mission title recorded in missions.db for the new mission.", "type": "string"},

         "tasks_json": {"type": "string", "description": "JSON array of task objects; each requires output_artifact_path"},

         "task_ledger_json": {"type": "string", "default": "{}", "description": "Optional mission ledger metadata JSON"},

         "room_path": {"description": "Optional council room path associated with the mission, used as the coordination channel for its tasks; empty means no room is bound.", "type": "string", "default": ""},

         "created_by": {"description": "Agent or user id recorded as the mission's creator in missions.db; empty leaves the creator unset.", "type": "string", "default": ""},

     }, "required": ["title", "tasks_json"]}}},

    {"type": "function", "function": {"name": "mission_next", "description": "P3b-2 T1: return the next dependency-ready pending task for a mission, or null.",

     "parameters": {"type": "object", "properties": {

         "mission_id": {"description": "Mission id (from mission_create) whose next dependency-ready pending task should be returned.", "type": "integer"},

     }, "required": ["mission_id"]}}},

    {"type": "function", "function": {"name": "mission_dispatch", "description": "P3b-2 T3: verify/set worker model, build mission wrapper, write dispatch outbox/ledger, and send via council send-async.",

     "parameters": {"type": "object", "properties": {

         "task_id": {"description": "Mission task id (from mission_create/mission_next) to dispatch to its assigned worker.", "type": "integer"},

     }, "required": ["task_id"]}}},

    {"type": "function", "function": {"name": "mission_report", "description": "P3b-2 T3: heartbeat or final report for a mission task with attempt-token validation and artifact-first gate handling.",

     "parameters": {"type": "object", "properties": {

         "task_id": {"description": "Mission task id this heartbeat or final report belongs to; validated against the task's attempt token.", "type": "integer"},

         "attempt_token": {"type": "string", "description": "Contains mission_attempt=<task_id>:<attempt>"},

         "res_path": {"description": "Path of the result artifact produced by the task; the artifact-first gate checks this file before accepting a final report.", "type": "string", "default": ""},

         "summary": {"description": "Free-text summary of the task outcome carried in the report and stored in the mission ledger.", "type": "string", "default": ""},

         "kind": {"description": "Report kind: 'final' (task outcome, default) or 'heartbeat' (progress signal that keeps the attempt alive without closing the task).", "type": "string", "enum": ["final", "heartbeat"], "default": "final"},

     }, "required": ["task_id"]}}},

    {"type": "function", "function": {"name": "mission_status", "description": "P3b-2 T1: compact mission/task status. verbosity=full includes progress_ledger tail 50.",

     "parameters": {"type": "object", "properties": {

         "mission_id": {"description": "Mission id whose compact status (or full ledger with verbosity='full') should be returned.", "type": "integer"},

         "verbosity": {"description": "'summary' (default) for compact status, or 'full' to also include the last 50 progress_ledger entries.", "type": "string", "enum": ["summary", "full"], "default": "summary"},

     }, "required": ["mission_id"]}}},

    {"type": "function", "function": {"name": "mission_gate", "description": "P3b-2 T3: approve/reject a human-gated task. approve -> pending; reject -> skipped; appends human_gate ledger.",

     "parameters": {"type": "object", "properties": {

         "task_id": {"description": "Human-gated mission task id awaiting the approve/reject decision.", "type": "integer"},

         "decision": {"description": "Gate decision: 'approve' moves the task to pending, 'reject' marks it skipped; either is appended to the human_gate ledger.", "type": "string", "enum": ["approve", "reject"]},

         "reason": {"type": "string", "default": ""},

     }, "required": ["task_id", "decision"]}}},

    {"type": "function", "function": {"name": "leader_bootstrap", "description": "P3b-2 T1: one-call leader bootstrap with council roster, worker models, active missions, and best-effort Q hint.",

     "parameters": {"type": "object", "properties": {}, "required": []}}},

{"type": "function", "function": {"name": "classify_ca_action", "description": "T1 READ: classify tool/action against .council/CA_TOOL_POLICY_MATRIX.json (CSLt A-first). Returns task_class, native_policy, forbidden/allowed/via_a flags, hermes host_gate.", "parameters": {"type": "object", "properties": {"tool_name": {"type": "string"}, "action_type": {"type": "string"}, "transport": {"description": "Channel the classified action would use: 'native' (default, direct host tool) or 'via_a' (through ToolA); the policy matrix scores the two differently.", "type": "string", "enum": ["native", "via_a"], "default": "native"}, "scope": {"type": "string"}, "target": {"type": "string"}, "project_path": {"type": "string"}, "agent_id": {"description": "Agent identity the classification is evaluated for; agent-specific policy rows (e.g. CLI C-suffix http-only) depend on it.", "type": "string"}}}}},

{"type": "function", "function": {"name": "ca_policy_metrics", "description": "T3 READ: aggregate Matrix bypass counters from sessions.db (forbidden_native/allowed_native/via_a/unclassified/bypass_rate).", "parameters": {"type": "object", "properties": {"project_path": {"type": "string"}, "session_id": {"type": "string"}, "agent_id": {"description": "Restrict the bypass-counter aggregation to this agent id; omit to aggregate across all agents in sessions.db.", "type": "string"}, "time_window_hours": {"description": "Look-back window in hours for the sessions.db counters. Default 24.0.", "type": "number", "default": 24.0}}}}},

# AUDIT-KEEP(hermes): historical tool name and "hermes" default -- see ca_policy_matrix.hermes_host_gate.
{"type": "function", "function": {"name": "hermes_host_gate_check", "description": "T1 READ: Hermes/peer host gate for project vs outside-project native ops (CSLt axis 3).", "parameters": {"type": "object", "properties": {"agent_id": {"description": "Peer agent identity being gate-checked (default 'hermes').", "type": "string", "default": "hermes"}, "project_path": {"type": "string"}, "target": {"type": "string"}, "scope": {"type": "string"}, "tool_name": {"type": "string"}, "action_type": {"type": "string"}, "transport": {"description": "Channel the checked operation would use: 'native' (default) or 'via_a'; outside-project native ops are what the host gate restricts.", "type": "string", "enum": ["native", "via_a"], "default": "native"}}}}},

{"type": "function", "function": {"name": "normalize_ca_registration", "description": "T3 WRITE: normalize agents_registry.json — mode=A default + transport_policy (http_only for CLI C-suffix, a_first_mcp_t4_ok for desktop). dry_run default true.", "parameters": {"type": "object", "properties": {"dry_run": {"type": "boolean", "default": True}}}}},

]



BASE_TOOL_MAP_SPECS = (

    ('open_session', 'open_session'),

    ('resolve_project_path', 'resolve_project_path'),

    ('protected_tools_status', 'protected_tools_status'),

    ('path_quote_helper', 'path_quote_helper'),

    ('optional_tool_probe', 'optional_tool_probe'),

    ('get_rule_core_text', 'get_rule_core_text'),

    ('audit_tool_matrix', 'audit_tool_matrix'),

    ('t4_host_status', 't4_host_status'),

    ('prepare_ca_session', 'prepare_ca_session'),

    ('next_recommended_action', 'next_recommended_action'),

    ('log_native', 'log_native'),

    ('log_native_event', 'log_native_event'),

    ('manage_agent', 'manage_agent'),

    ('convene_council', 'convene_council'),

    ('council_config_diff', 'council_config_diff'),

    ('council_config_sync', 'council_config_sync'),

    ('council_tracker', 'council_tracker'),

    ('council_route_scorecard', 'council_route_scorecard'),

    ('mission_create', '_mission_tools.mission_create'),

    ('mission_next', '_mission_tools.mission_next'),

    ('mission_dispatch', '_mission_tools.mission_dispatch'),

    ('mission_report', '_mission_tools.mission_report'),

    ('mission_status', '_mission_tools.mission_status'),

    ('mission_gate', '_mission_tools.mission_gate'),

    ('leader_bootstrap', '_mission_tools.leader_bootstrap'),

    ('create_pending_audit', 'create_pending_audit'),

    ('sync_pending_audit', 'sync_pending_audit'),

    ('read_file_range', 'read_file_range'),
    ('read_context_batch', 'read_context_batch'),
    ('architecture_context_pack', 'architecture_context_pack'),

    ('get_project_summary', 'get_project_summary'),

    ('search_code', 'search_code'),

    ('find_symbol', 'search_code'),

    ('regex_search', 'search_code'),

    ('find_callers', 'search_code'),

    ('find_definition_and_usage', 'search_code'),

    ('query_index', 'query_index'),

    ('query_dom_candidates', 'search_code'),

    ('write_fix', 'write_fix'),

    ('write_fix_html', 'write_fix_html'),

    ('write_new_file', 'write_new_file'),

    ('write_draft_patch', 'write_draft_patch'),

    ('write_draft_status', 'write_draft_status'),

    ('write_draft_abort', 'write_draft_abort'),

    ('apply_after_dry_run', 'apply_after_dry_run'),

    ('delete_file', 'delete_file'),

    ('validate_file', 'validate_file'),

    ('validate_symbols', 'validate_symbols'),

    ('validate_encoding', 'validate_encoding'),

    ('post_write_batch_verify', 'post_write_batch_verify'),

    ('cache_delta', 'cache_delta'),

    ('run_project_command', 'run_project_command'),

    ('kill_process', 'kill_process'),

    ('backup_project', 'backup_project'),

    ('close_session', 'close_session'),

    ('rename_session', 'rename_session'),

    ('clear_closed_sessions', 'clear_closed_sessions'),

    ('list_sessions', 'list_sessions'),

    ('get_session_events', 'get_session_events'),

    ('get_file_symbols', 'get_file_symbols'),

    ('list_structure', 'list_structure'),

    ('run_test_profile', 'run_test_profile'),

    ('backup_status', 'backup_status'),

    ('workflow_status_registry', 'workflow_status_registry'),

    ('set_workflow_mode', 'set_workflow_mode'),

    ('cache_status', 'cache_status'),

    ('generate_project_map', 'generate_project_map'),

    ('refresh_project_map', 'refresh_project_map'),

    ('create_build_context', 'create_build_context'),

    ('create_benchmark_request', 'create_benchmark_request'),

    ('get_usage_stats', 'get_usage_stats'),

    ('capability_status', 'capability_status'),

    ('suggest_missing_capabilities', 'suggest_missing_capabilities'),

    ('get_folder_size', 'get_folder_size'),

    ('validate', 'validate'),

    ('project_map', 'project_map'),

    ('state', 'state'),

    ('cep_manifest_validate', 'cep_manifest_validate'),

    ('cep_extension_status', 'cep_extension_status'),

    ('copy_file', 'copy_file'),

    ('cep_deploy', 'cep_deploy'),

)



V15_NEW_TOOL_SPECS = (

    ('import_graph', '_v15.import_graph'),

    ('ast_query', '_v15.ast_query'),

    ('git_blame_symbol', '_v15.git_blame_symbol'),

    ('write_transaction', '_v15.write_transaction'),

    ('replace_between_anchors', '_v15.replace_between_anchors'),

    ('session_audit_summary', '_v15.session_audit_summary'),

    ('record_error_resolution', '_v15.record_error_resolution'),

    ('record_session_audit_checkpoint', '_v15.record_session_audit_checkpoint'),

    ('tool_decision_hint', '_v15.tool_decision_hint'),

    ('progressive_matrix_check', '_v15.progressive_matrix_check'),

    ('open_browser_page', '_v15.open_browser_page'),

    ('read_page', '_v15.read_page'),

    ('query_selector', '_v15.query_selector'),

    ('click', '_v15.click'),

    ('fill', '_v15.fill'),

    ('evaluate_js', '_v15.evaluate_js'),

    ('read_console_errors', '_v15.read_console_errors'),

    ('read_network_errors', '_v15.read_network_errors'),

    ('screenshot_page', '_v15.screenshot_page'),

    ('wait_for_selector', '_v15.wait_for_selector'),

    ('compare_dom_snapshot', '_v15.compare_dom_snapshot'),

    ('close_browser_page', '_v15.close_browser_page'),

    ('server_lifecycle_status', '_v15.server_lifecycle_status'),

    ('port_owner_inspect', '_v15.port_owner_inspect'),

    ('detect_stale_server', '_v15.detect_stale_server'),

    ('static_asset_cache_check', '_v15.static_asset_cache_check'),

    ('bundle_version_check', '_v15.bundle_version_check'),

    ('restart_server_via_bat_hint', '_v15.restart_server_via_bat_hint'),

    ('restart_orchestrator_via_task', '_v15.restart_orchestrator_via_task'),

    ('cancel_pending_restart', '_v15.cancel_pending_restart'),

    ('check_window_visibility', '_v15.check_window_visibility'),

    ('cdp_attach_target', '_v15.cdp_attach_target'),

    ('inspect_project_runtime', '_v15.inspect_project_runtime'),

    ('launch_electron_with_cdp', '_v15.launch_electron_with_cdp'),

    ('dom_state_diff', '_v15.dom_state_diff'),

    ('extract_log_section', '_v15.extract_log_section'),

    ('fill_value', '_v15.fill_value'),

    ('read_value', '_v15.read_value'),

    ('read_all_logs', '_v15.read_all_logs'),

    ('run_script', '_v15.run_script'),

    ('describe_page', '_v15.describe_page'),

    ('cleanup_test_session', '_v15.cleanup_test_session'),

    ('click_selector', '_v15.click_selector'),

    ('read_skill', '_v15.read_skill'),

    ('checkpoint_loop_dom_state', '_v15.checkpoint_loop_dom_state'),

    ('ensure_single_electron_instance', '_v15.ensure_single_electron_instance'),

    ('click_and_checkpoint_loop', '_v15.click_and_checkpoint_loop'),

    ('ui_state_summary', '_v15.ui_state_summary'),

    ('save_log_snapshot', '_v15.save_log_snapshot'),

    ('declare_mission', '_v15.declare_mission'),

    ('alias_contract_test', '_v15.alias_contract_test'),

    ('architecture_audit_compact', '_v15.architecture_audit_compact'),

    ('mcp_surface_diff', '_v15.mcp_surface_diff'),

    ('acceptance_dashboard', '_v15.acceptance_dashboard'),

    ('folder_exists', 'folder_exists'),

    ('create_folder', 'create_folder'),

    ('create_folders', 'create_folders'),

    ('folder_stats', 'folder_stats'),

    ('folder_diff', 'folder_diff'),

    ('fill_folder_dialog', 'fill_folder_dialog'),

    ('cancel_native_dialog', 'cancel_native_dialog'),

    ('folder_dialog_snapshot', 'folder_dialog_snapshot'),

    ('click_and_handle_folder_dialog', 'click_and_handle_folder_dialog'),

    ('workflow_list', 'workflow_list'),

    ('workflow_start', 'workflow_start'),

    ('workflow_get_current', 'workflow_get_current'),

    ('workflow_submit_step', 'workflow_submit_step'),

    ('workflow_retry_step', 'workflow_retry_step'),

    ('workflow_skip_step', 'workflow_skip_step'),

    ('workflow_reset', 'workflow_reset'),

    ('workflow_status', 'workflow_status'),

    ('workflow_create_from_ui', 'workflow_create_from_ui'),

    ('workflow_save_md', 'workflow_create_from_ui'),

    ('workflow_read_md', 'workflow_read_md'),

    ('workflow_draft_create', 'workflow_draft_create'),

    ('workflow_draft_get', 'workflow_draft_get'),

    ('workflow_activate', 'workflow_activate'),

    ('workflow_deactivate', 'workflow_deactivate'),

    ('workflow_active_get', 'workflow_active_get'),

    ('classify_get_footage_outcome', 'classify_get_footage_outcome'),

    ('assert_target_idle', 'assert_target_idle'),

    ('verify_active_electron_source', 'verify_active_electron_source'),

    ('bootstrap_session', 'bootstrap_session'),

    ('project_context_pack', 'project_context_pack'),

    ('resolve_candidate_paths', 'resolve_candidate_paths'),

    ('begin_task', 'begin_task'),

    ('owner_heartbeat', 'owner_heartbeat'),

    ('get_task_checkpoint', 'get_task_checkpoint'),

    ('run_project_test_script', 'run_project_test_script'),

    ('check_server_path', 'check_server_path'),

    ('webfetch', 'webfetch'),

    ('fetch_sheet_csv_summary', 'fetch_sheet_csv_summary'),

    ('sample_unc_paths_with_root_check', 'sample_unc_paths_with_root_check'),

    ('verify_get_footage_copy_completion', 'verify_get_footage_copy_completion'),

    ('run_ps1_script', 'run_ps1_script'),

    ('run_node_script', 'run_node_script'),

    ('workflow_checkpoint', 'workflow_checkpoint'),

    ('workflow_goto_step', 'workflow_goto_step'),

    ('wait_for_idle', 'wait_for_idle'),

    ('parse_log_section', 'parse_log_section'),

    ('inspect_project_usage', '_v15.inspect_project_usage'),

    ('invoke_app_function', '_v15.invoke_app_function'),

    ('desktop_window_list', '_v15.desktop_window_list'),

    ('desktop_app_focus', '_v15.desktop_app_focus'),

    ('desktop_click', '_v15.desktop_click'),

    ('desktop_window_state', '_v15.desktop_window_state'),

    ('desktop_screenshot', '_v15.desktop_screenshot'),

    ('desktop_send_text', '_v15.desktop_send_text'),

    ('electron_devtools_log', '_v15.electron_devtools_log'),

    ('list_cdp_targets', '_v15.list_cdp_targets'),

    ('process_tree_inspect', '_v15.process_tree_inspect'),

    ('run_playwright_test', '_v15.run_playwright_test'),

    ('record_ui_flow', '_v15.record_ui_flow'),

    ('assert_text_visible', '_v15.assert_text_visible'),

    ('assert_button_click_changes_state', '_v15.assert_button_click_changes_state'),

    ('assert_console_no_error', '_v15.assert_console_no_error'),

    ('get_token_meter', '_v15.get_token_meter'),

    ('get_skill_state', '_v15.get_skill_state'),

    ('list_projects', '_v15.list_projects'),

    ('get_validation_queue', '_v15.get_validation_queue'),

    ('classify_ca_action', '_v15.classify_ca_action'),

    ('ca_policy_metrics', '_v15.ca_policy_metrics'),

    ('hermes_host_gate_check', '_v15.hermes_host_gate_check'),

    ('normalize_ca_registration', '_v15.normalize_ca_registration'),

)

AUDIT_RECONCILIATION_SCHEMA = {
    "description": "C1 T1 READ-ONLY reconciliation of unresolved errors. Classification is expected, recovered, still_open, or infrastructure; infrastructure remains a cause category while disposition is recovered or still_open. Strong evidence yields an explicit record_error_resolution suggestion; this tool never writes or auto-closes.",
    "parameters": {"type": "object", "properties": {
        "project_path": {"type": "string", "default": "", "description": "Project root to reconcile. Empty resolves from the caller's session."},
        "target_session_id": {"type": "string", "default": "", "description": "Session whose unresolved errors are reconciled. Empty means the caller's own session."},
        "session_id": {"type": "string", "default": "", "description": "Legacy target-session alias."},
        "agent_id": {"type": "string", "default": "", "description": "Restrict the report to errors raised by this agent. Empty means all agents."}}, "required": []}}



V15_MUTATION_SCOPES = {
    # A checkpoint appends to the session audit ledger. Keeping this explicit in
    # registry metadata prevents a protected T3 audit write from being reported as
    # scope=none while preserving its project/session targeting contract.
    "record_session_audit_checkpoint": "audit",
    "record_error_resolution": "audit",
}


V15_TIERS = {

        # T1

        "import_graph": "T1", "ast_query": "T1", "git_blame_symbol": "T1",

        "convene_council": "T1",

        # T2/T3

        "write_transaction": "T2", "replace_between_anchors": "T2", "session_audit_summary": "T3",
        "record_session_audit_checkpoint": "T3", "record_error_resolution": "T3",

        "tool_decision_hint": "T1", "progressive_matrix_check": "T1", "mcp_surface_diff": "T1",

        "classify_ca_action": "T1", "hermes_host_gate_check": "T1",

        "ca_policy_metrics": "T3", "normalize_ca_registration": "T3",

        # T4a (read-only mostly)

        "open_browser_page": "T4", "read_page": "T4", "query_selector": "T4",

        "click": "T4", "fill": "T4", "evaluate_js": "T4",

        "read_console_errors": "T4", "read_network_errors": "T4",

        "screenshot_page": "T4", "wait_for_selector": "T4",

        "compare_dom_snapshot": "T4", "close_browser_page": "T4",

        # T4c

        "server_lifecycle_status": "T4", "port_owner_inspect": "T4",

        "detect_stale_server": "T4", "static_asset_cache_check": "T4",

        "bundle_version_check": "T4", "restart_server_via_bat_hint": "T4",

        # T4d

        "desktop_window_list": "T4", "desktop_app_focus": "T4",

        "desktop_click": "T4", "desktop_window_state": "T4", "desktop_screenshot": "T4",

        "desktop_send_text": "T4",

        "restart_orchestrator_via_task": "T4",

        "cancel_pending_restart": "T4",

        "check_window_visibility": "T4",

        "cdp_attach_target": "T4",

        "inspect_project_runtime": "T1",

        "launch_electron_with_cdp": "T4",

        "dom_state_diff": "T1",

        "extract_log_section": "T1",

        "fill_value": "T2",

        "read_value": "T1",

        "read_all_logs": "T1",

        "run_script": "T4",

        "describe_page": "T1",

        "cleanup_test_session": "T2",

        "click_selector": "T4",

        "read_skill": "T1",

        "checkpoint_loop_dom_state": "T4",

        "ensure_single_electron_instance": "T4",

        "click_and_checkpoint_loop": "T4",

        "ui_state_summary": "T1",

        "save_log_snapshot": "T2",

        "declare_mission": "T1",

        "alias_contract_test": "T1",

        "architecture_audit_compact": "T1",

        "acceptance_dashboard": "T1",

        # R27

        "folder_exists": "T1",

        "create_folder": "T2", "create_folders": "T2",

        "folder_stats": "T1",

        "folder_diff": "T1",

        "fill_folder_dialog": "T4",

        "cancel_native_dialog": "T4",

        "folder_dialog_snapshot": "T1",

        "click_and_handle_folder_dialog": "T4",

        # V15 R35 — MD workflow dispenser + R34 classifier/idle gate

        "workflow_list": "T1", "workflow_start": "T3",

        "workflow_get_current": "T1", "workflow_submit_step": "T3",

        "workflow_retry_step": "T3", "workflow_skip_step": "T3",

        "workflow_reset": "T3", "workflow_status": "T1", "workflow_status_registry": "T1",

        "workflow_create_from_ui": "T3",

        "workflow_save_md": "T3",

        # V15 R36

        "workflow_read_md": "T1", "workflow_draft_create": "T3", "workflow_draft_get": "T1",

        "workflow_activate": "T3", "workflow_deactivate": "T3", "workflow_active_get": "T1",

        "classify_get_footage_outcome": "T1",

        "assert_target_idle": "T1",

        "verify_active_electron_source": "T1",

        # V15 R42

        "bootstrap_session": "T1", "begin_task": "T3", "owner_heartbeat": "T3",
        "project_context_pack": "T1", "resolve_candidate_paths": "T1",
        "read_context_batch": "T1", "architecture_context_pack": "T1",
        "get_task_checkpoint": "T1", "check_server_path": "T1",

        "run_project_test_script": "T2",  # R43 P1 #5

        "webfetch": "T1",

        "fetch_sheet_csv_summary": "T1",

        "sample_unc_paths_with_root_check": "T1",

        "verify_get_footage_copy_completion": "T1",

        "run_ps1_script": "T2",

        "run_node_script": "T2",

        "workflow_checkpoint": "T3",

        "manage_agent": "T3",

        "workflow_goto_step": "T3", "wait_for_idle": "T1", "parse_log_section": "T1",

        "inspect_project_usage": "T1",

        "invoke_app_function": "T4",

        "electron_devtools_log": "T4", "list_cdp_targets": "T4", "process_tree_inspect": "T4",

        # T4 test

        "run_playwright_test": "T4", "record_ui_flow": "T4",

        "assert_text_visible": "T4", "assert_button_click_changes_state": "T4",

        "assert_console_no_error": "T4",

        # UI backend (read-only)

        "get_token_meter": "T1", "get_skill_state": "T1",

        "list_projects": "T1", "get_validation_queue": "T1",

    }



V15_R8_NEW_SCHEMA_SPECS = (

    ('council_config_diff', 'T1 READ-ONLY: report drift between the SOT .council/council_config.json and derived rule docs (CORE_RULE.txt + .council/*.md). convene_council + runtime maps read the SOT live; this checks the human docs. Never writes.', {

            "project_path": {"description": "Project root whose .council/council_config.json is compared against the derived rule docs; defaults to the active project when empty.", "type": "string"},

        }, []),

    ('project_context_pack', 'CAN-053 T1: one-call compact project profile (component roots, languages, groups, hotspots, entrypoints, graph freshness, recommended next tools). Cached by INDEX fingerprint; refresh=true to rebuild. Call this FIRST on an unfamiliar project instead of blind file probes.', {

            "project_path": {"type": "string", "description": "Absolute project root."},

            "refresh": {"type": "boolean", "default": False, "description": "Force rebuild even when the cached pack matches the INDEX fingerprint"},

        }, ["project_path"]),

    ('resolve_candidate_paths', 'CAN-053 T1: batch existence check for up to 50 candidate paths with fuzzy suggestions from INDEX — one call instead of N blind read_file_range probes.', {

            "project_path": {"type": "string", "description": "Absolute project root."},

            "paths": {"type": "array", "items": {"type": "string"}, "description": "Relative (to project root) or absolute paths to check"},

        }, ["project_path", "paths"]),

    ('record_session_audit_checkpoint', 'CAN-053 T3 (protected): persist an audit checkpoint event into the session ledger — the mutating half of session_audit_summary (which is now pure T1 read, token-free).', {

            "project_path": {"type": "string", "default": "", "description": "Project root; resolves the active session when session_id is empty."},

            "session_id": {"type": "string", "default": "", "description": "Session to checkpoint (defaults to the active session of project_path)."},

            "note": {"type": "string", "default": "", "description": "Optional checkpoint note (<=200 chars)"},

        }, []),

    ('record_error_resolution', 'T3 protected audit write: append an explicit state transition for one exact error event. Requires transport-only X-Session-Id, X-Agent-Id, X-Task-Epoch, X-Prompt-Hash, and X-Task-Capability matching the server-issued root/delegation checkpoint for that exact session+agent. Transport fields are intentionally excluded from this payload schema. target_session_id must equal the validated host session or the exact error event must carry matching host_session_id correlation. Missing/invalid ownership returns SESSION_OWNERSHIP_REQUIRED; stale epoch/hash returns STALE_TASK_EPOCH; neither writes. No privileged bearer-token bypass is provided.', {
            "target_session_id": {"type": "string", "description": "Session that owns the exact error event. This is a domain target, not host transport identity."},
            "resolves_event_id": {"type": "integer", "description": "Exact source error event id returned by get_session_events."},
            "resolution_kind": {"description": "State transition to append for the error event: 'recovered', 'expected_failure', or 'reopened'.", "type": "string", "enum": ["recovered", "expected_failure", "reopened"]},
            "error_owner": {"type": "string", "default": "", "description": "Optional owner override; otherwise inherited from the source error."},
            "evidence": {"type": ["object", "array", "string", "number", "boolean", "null"], "description": "Optional JSON evidence for this transition."},
            "correlation_type": {"type": "string", "default": "", "description": "Optional correlation type override."},
            "correlation_key": {"type": "string", "default": "", "description": "Optional correlation key override."},
        }, ["target_session_id", "resolves_event_id", "resolution_kind"]),

    ('council_config_sync', 'T2 WRITE (clauC/codoC only): propagate the SOT .council/council_config.json to derived rule docs (rewrites cost-order chain + roster list, backs up <file>.bak_ccsync), then optionally runs manage_agent(sync_rule, all). Restart [A] afterwards to refresh runtime maps.', {

            "project_path": {"type": "string", "description": "Project root for the session gate. W7 2026-08-19 (F020): declared required because the write gate enforces it and no other field can resolve it."},

            "run_sync_rule": {"description": "When true (default), after propagating council_config.json to the derived docs also run manage_agent(sync_rule, all) so every agent's rule body is rewritten from the new SOT.", "type": "boolean", "default": True},

        }, ["project_path"]),

    ('regex_search', "V15 R8 B.1: deprecated alias of search_code(mode='regex'). flags: i|m|s.", {

            "pattern": {"type": "string", "description": "Regular expression pattern to search for. Alias: query."},

            "query": {"type": "string", "description": "Alias for pattern."},

            "project_path": {"type": "string", "description": "Project root to search."},

            # B019 (2026-08-21): the third half of the same defect. The signature
            # fix in tools_v15 was invisible over HTTP because the dispatcher
            # validates against THIS schema, not the Python signature -- so the
            # alias still answered UNSUPPORTED_FIELD for `path` while its own
            # REGEX_INPUT_BUDGET_EXCEEDED told the caller to narrow by path. Same
            # shape as B-01 directly below, which had to expose
            # regex_deadline_seconds here for exactly the same reason. In
            # search_code `path` resolves as `_path = path or project_path`, so
            # passing a subtree makes that subtree the search root: that IS the
            # narrowing the budget error advises.
            "path": {"type": "string", "default": "", "description": "Narrow the search to this subtree (becomes the search root). The field REGEX_INPUT_BUDGET_EXCEEDED tells you to narrow by."},

            "file_pattern": {"type": "string", "default": "*", "description": "File glob filter."},

            "flags": {"type": "string", "default": "i", "description": "Regex flags: i|m|s."},

            "max_results": {"type": "integer", "default": 50, "description": "Maximum returned hits."},

            "max_context_lines": {"type": "integer", "default": 0, "description": "Context lines around each hit."},

            # B-01 (2026-08-10): expose the regex deadline on the deprecated
            # regex_search alias. The underlying search_code already supports
            # regex_deadline_seconds (search_regex_worker.DEFAULT/MAX bounds);
            # without registry exposure the dispatcher rejected it as
            # UNSUPPORTED_FIELD, leaving callers stuck at the baked 1s default
            # that cannot fit the live daemon execution path.
            # B031 (2026-09-21): the published default/range had drifted BOTH ways from
            # search_regex_worker (default 5.0 vs DEFAULT_DEADLINE_SECONDS 8.0; range
            # "0.05-10" vs the real clamp max(MIN 0.05, min(parsed, MAX 15.0))), so a CA
            # reading the schema under-asked by 5s at the ceiling and mis-predicted the
            # default. Numbers here must track search_regex_worker.py:17-19.
            "regex_deadline_seconds": {"type": "number", "default": 8.0, "description": "Hard deadline for the regex worker, seconds (clamped to 0.05-15.0; default 8.0). Bounds ONE phase of ONE attempt: input and match phases are budgeted separately, and a timeout with no explicit value here is retried once under a doubled deadline, so the call's wall clock can reach ~4x this number. Read wall_clock_budget_seconds on a REGEX_SEARCH_TIMEOUT payload for the measured budget."},

        }, ["pattern", "project_path"]),

    ('find_callers', 'V15 R8 B.1: deprecated alias of search_code(mode=callers). project_path optional when an active session/task is attached (resolved from session/project context); otherwise required.', {

            "symbol": {"type": "string", "description": "Symbol name whose call sites should be found. Aliases: query, name."},

            "query": {"type": "string", "description": "Alias for symbol."},

            "project_path": {"type": "string", "description": "Project root to search. Optional when the request carries an active session/task context (resolved server-side)."},

            "context_lines": {"type": "integer", "default": 0, "description": "Context lines around each caller hit."},

            "page_id": {"type": "string", "default": "", "description": "Internal/session-resolution field (Agent-enablement F2, 2026-08-08). Used ONLY by the dispatcher to resolve project_path from the _BROWSER_PAGES registry when session_id is absent. Stripped before handler invocation; never reaches search_code."},

        }, ["symbol"]),

    ('find_definition_and_usage', 'V15 R8 B.1: deprecated alias of search_code - use search_code with query=<symbol> for definition lookup.', {

            "query": {"type": "string", "description": "Symbol name whose definition and usages should be found."},

            "project_path": {"type": "string", "description": "Project root to search."},

            "context_lines": {"type": "integer", "default": 0, "description": "Context lines around each hit."},

        }, ["query", "project_path"]),

    ('import_graph', 'V15 R8 B.1: scan import/require edges across project.', {

            "project_path": {"description": "Project root to scan for import/require edges.", "type": "string"},

            "file_pattern": {"description": "Comma-separated file globs included in the import scan. Default '*.py,*.js,*.ts'.", "type": "string", "default": "*.py,*.js,*.ts"},

            "max_files": {"description": "Maximum number of files scanned for import edges. Default 500.", "type": "integer", "default": 500},

        }, ["project_path"]),

    ('ast_query', 'V15 R8 B.1: Python AST preset patterns: async_function, class_inheriting, try_bare_except, function_returning_dict.', {

            "pattern": {"description": "AST preset to match: 'async_function', 'class_inheriting', 'try_bare_except', or 'function_returning_dict'.", "type": "string", "enum": ["async_function", "class_inheriting", "try_bare_except", "function_returning_dict"]}, "project_path": {"description": "Project root whose Python files are AST-scanned for the preset pattern.", "type": "string"},

            "file_pattern": {"description": "File glob for files included in the AST scan. Default '*.py'.", "type": "string", "default": "*.py"},

            "max_results": {"description": "Maximum number of AST matches returned. Default 50.", "type": "integer", "default": 50},

        }, ["pattern", "project_path"]),

    ('git_blame_symbol', "V15 R8 B.1: git log -L for symbol's definition lines.", {

            "symbol": {"description": "Symbol whose definition lines are traced through git log -L.", "type": "string"}, "project_path": {"description": "Project root of the git repository the symbol history is read from.", "type": "string"},

            "file_pattern": {"description": "File glob narrowing which files are searched for the symbol's definition. Default '*.py'.", "type": "string", "default": "*.py"},

        }, ["symbol", "project_path"]),

    ('write_transaction', "V15 R8 B.2 (+R43 P1 #3-B): atomic multi-file write batch with rollback. R43 ops can be tool='write_fix' (line range, default), 'write_new_file' (create), or 'replace_between_anchors' (anchor-based patch). Each operation MUST use file_path, not path. R43 structural_check=true (default) runs brace/paren/bracket delta count on dry_run preview → STRUCTURAL_IMBALANCE if range likely incomplete. Each write_fix op also supports expected_hash to skip FILE_CHANGED guardrail in sequential chain.", {

			"operations": {"example": [{'tool': 'write_fix', 'file_path': 'tools_v15.py', 'start_line': 5843, 'end_line': 5843, 'new_content': '        "doc_params_undescribed_max": 0,  # P23'}], "type": "array", "description": "Non-empty list of operation objects. Default op is write_fix: {file_path,start_line,end_line,new_content,content_encoding}. For creates: {tool:'write_new_file',file_path,new_content,content_encoding,overwrite}. For anchors: {tool:'replace_between_anchors',file_path,start_anchor,end_anchor,new_content}. P4: set op content_encoding='base64' (UTF-8) when new_content is multiline or has backslashes (Windows paths, regex), double quotes, or non-ASCII - plain JSON transport can corrupt these.", "items": {"type": "object", "required": ["file_path"], "properties": {"tool": {"type": "string", "enum": ["write_fix", "write_new_file", "replace_between_anchors"], "default": "write_fix"}, "file_path": {"type": "string"}, "start_line": {"type": "integer", "default": 1}, "end_line": {"type": "integer", "default": 1}, "start_anchor": {"type": "string"}, "end_anchor": {"type": "string"}, "new_content": {"type": "string"}, "content_encoding": {"type": "string", "default": "none", "description": "'none' or 'base64' (UTF-8); recommended for multiline/backslash/quote/non-ASCII new_content"}, "overwrite": {"type": "boolean", "default": False}, "expected_hash": {"type": "string", "default": ""}}}},

            "project_path": {"description": "Project root the batch's operations are gated and written under; enforced by the write gate.", "example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string"}, "backup": {"description": "When true (default), take a project backup before applying the batch so a failed verify can roll back.", "example": True, "type": "boolean", "default": True},

            "structural_check": {"example": True, "type": "boolean", "default": True, "description": "R43 P1 #3-B: detect range-incomplete patterns via brace/paren delta on dry_run preview."},

        }, ["operations", "project_path"]),  # W7: project_path is enforced by the write gate; declaring it keeps schema == runtime.

    ('replace_between_anchors', 'V15 R43 P1 #3-B T2: anchor-based file patch. Resolves text anchors (start_anchor/end_anchor as substring or regex) to line numbers, then delegates to write_fix. Eliminates line-number brittleness for long functions. end_anchor_inclusive=false (default) preserves the end_anchor line.', {

            "file_path": {"description": "File to patch, relative to project_path; the anchor range inside it is replaced.", "type": "string"},

            "start_anchor": {"type": "string", "description": "Substring (or regex if start_anchor_is_regex=true) on the first line of the range. First match wins."},

            "end_anchor": {"type": "string", "description": "Substring on the line after the last line to replace (or last line if end_anchor_inclusive=true)."},

            "new_content": {"description": "Replacement text written between the resolved start and end anchors.", "type": "string"},

            "project_path": {"description": "Project root file_path is resolved against; empty resolves from the active session's project.", "type": "string", "default": ""},

            "dry_run": {"description": "When true, resolve the anchors and preview the patch without writing; default false applies it.", "type": "boolean", "default": False},

            "content_encoding": {"description": "'none' (default) for plain text, or 'base64' (UTF-8) when new_content is multiline or carries backslashes, quotes, or non-ASCII.", "type": "string", "default": "none"},

            "end_anchor_inclusive": {"type": "boolean", "default": False, "description": "If true, end_anchor line is replaced too."},

            "start_anchor_is_regex": {"description": "When true, start_anchor is matched as a regular expression instead of a literal substring; default false.", "type": "boolean", "default": False},

            "end_anchor_is_regex": {"description": "When true, end_anchor is matched as a regular expression instead of a literal substring; default false.", "type": "boolean", "default": False},

        }, ["file_path", "start_anchor", "end_anchor", "new_content"]),

    ('session_audit_summary', 'V15 R8 B.2: aggregate session state {tools_used, mutations, errors, recovered_errors, open_errors, caller_errors, backend_errors, native_fallback_count, open_risks}. CAN-053: pure T1 read (token-free); CAN-054: pass target_session_id to audit another session.', {

"project_path": {"type": "string", "description": "Project root used to resolve the active session when no explicit session is supplied."}, "session_id": {"type": "string", "description": "Legacy explicit session identifier; prefer target_session_id for unambiguous reads."},

            "target_session_id": {"type": "string", "default": "", "description": "CAN-054: explicit target session to audit; wins over session_id"},

            "agent_id": {"type": "string", "default": "", "description": "Stable agent identity used when resolving by project_path."},

        }, []),

    ('tool_decision_hint', 'V15 R8 B.2 / R22: returns recommended tier+tool+skill for task description. R22 verbosity ∈ {full|terse|ref} — terse drops rationale + tools_validation + payload_example schemas (saves ~550 tokens).', {

            "task_description": {"description": "The task to route: a plain-language description of what the caller wants to do; the hint ranks tools/tiers/skills against it.", "example": 'read a file range in a large repo', "type": "string"},

            "scope_hint": {"description": "Optional scope hint (e.g. read vs write, target area) that biases the tool recommendation.", "example": 'read', "type": "string"}, "risk_hint": {"description": "Optional risk hint (e.g. destructive, runtime, external) that biases the recommendation toward safer tiers/tools.", "example": 'none', "type": "string"},

            "verbosity": {"example": 'terse', "type": "string", "enum": ["full", "terse", "ref"], "default": "full", "description": "R22: terse drops reasoning blocks; full = back-compat."},

        }, ["task_description"]),

    ('progressive_matrix_check', 'V15 R13: read-only certification snapshot for progressive-disclosure workflow docs, schema phase metadata, and UI special-action mapping. This is a static matrix harness; runtime T4 behavior still needs runtime tests.', {

            "project_path": {"type": "string", "default": "", "description": "App root to inspect; empty uses path_runtime.APP_ROOT"},

            "mode": {"type": "string", "default": "summary", "description": "summary, tool, or step"},

            "tool_name": {"type": "string", "default": "", "description": "Optional tool to trace through schema/phase"},

            "step_id": {"type": "string", "default": "", "description": "Optional workflow step id such as T4.1"},

        }, []),

    ('mcp_surface_diff', 'R90 T1: compare [A] backend tool surfaces (TOOL_MAP, TOOL_SCHEMAS, /api/tools) and optionally caller-provided MCP host tool names. Use after [A] upgrades to detect stale MCP/client surfaces.', {

            "project_path": {"description": "Project root used to resolve the [A] install whose backend surfaces are compared; empty defaults to this install.", "type": "string", "default": ""},

            "client_tool_names": {"type": "array", "default": [], "description": "Optional tool names observed by the current MCP client/agent; enables client-vs-backend diff."},

            "include_details": {"type": "boolean", "default": False, "description": "Return full diff arrays instead of compact non-empty samples."},

        }, ["project_path"]),

    ('convene_council', 'Call this when activating the council (trigger: bat hoi dong Tr). Before any council coordination/dispatch, load leader-council-skill even when continuing an existing plan without the exact trigger. Payload hint: pass only {project_path:"<absolute project root>"}; do not pass leader/topic/task. Returns roster + per-agent CLI channels + bridge protocol + council_leader_skill; the calling agent becomes leader.', {

            "project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "default": "", "description": "Optional absolute current project root for traceability. This is the only accepted input field."},

        }, []),

    ('open_browser_page', 'V15 R8 B.3 / R44 T4a: launch Chromium, return page_id. R44: optional project_path pins page state so downstream T4 mutating tools resolve active session via page_id.', {

            "url": {"description": "URL the new Chromium page navigates to on launch.", "type": "string"}, "viewport_w": {"description": "Browser viewport width in pixels. Default 1280.", "type": "integer", "default": 1280},

            "viewport_h": {"description": "Browser viewport height in pixels. Default 800.", "type": "integer", "default": 800},

            "headless": {"description": "When true (default), launch Chromium headless; false opens a visible browser window.", "type": "boolean", "default": True},

            "wait_for": {"type": "string", "default": "load", "description": "Navigation wait state: load | domcontentloaded | networkidle | commit. Polling/SPA apps should use domcontentloaded (networkidle may never settle). Alias: wait_until."},

            "timeout_ms": {"type": "integer", "default": 20000, "description": "Per-navigation timeout in ms (CAN-051; was a hidden hard-coded 20000). Alias: timeout."},

            "attach_id": {"type": "string", "default": "", "description": "Reuse existing CDP-attached page id (R15 P2.6)"},

            "project_path": {"type": "string", "description": "R44: originating project root pinned to page state so downstream page-bound tools resolve the session via page_id. W7 2026-08-19 (F020): the process/session gate DEMANDS a resolvable project root and this tool has no file_path/page_id/session_id to derive one from, so a payload built from `required` always answered 400 PROJECT_PATH_REQUIRED. Schema now says what runtime enforces."},

        }, ["url", "project_path"]),

    ('read_page', 'T4a: text content of selector (max 5KB).', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the page whose text content is read.", "type": "string"}, "selector": {"description": "CSS selector whose element text is read. Default 'body' reads the whole page (capped at max_chars).", "type": "string", "default": "body"},

            "max_chars": {"description": "Maximum characters of element text returned (tool caps at 5KB). Default 5000.", "type": "integer", "default": 5000},

        }, ["page_id"]),

    ('query_selector', 'T4a: existence + visibility + bbox.', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the page queried for the selector.", "type": "string"}, "selector": {"description": "CSS selector to test for existence, visibility, and bounding box on the page.", "type": "string"},

        }, ["page_id", "selector"]),

    ('click', 'T4b: click selector (EXECUTE-gated).', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the click is dispatched on this page.", "type": "string"}, "selector": {"description": "CSS selector of the element to click on the page.", "type": "string"},

        }, ["page_id", "selector"]),

    ('fill', 'T4b: fill input (EXECUTE-gated).', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the input is filled on this page.", "type": "string"}, "selector": {"description": "CSS selector of the input element to fill.", "type": "string"},

            "value": {"description": "Text value written into the selected input element.", "type": "string"},

        }, ["page_id", "selector", "value"]),

    ('evaluate_js', 'T4b: evaluate JS expression in page (EXECUTE-gated). V15 R16.3: timeout_ms protects against infinite loops; errors surface stack line/column when available.', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the expression is evaluated in this page's context.", "type": "string"}, "expression": {"description": "JavaScript expression to evaluate in the page; its result is returned.", "type": "string"},

            "timeout_ms": {"type": "integer", "default": 8000, "description": "R16.3: max ms before EVAL_TIMEOUT (default 8000)"},

        }, ["page_id", "expression"]),

    ('read_console_errors', 'T4a: console messages (default level=error).', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the page whose console messages are read.", "type": "string"}, "level": {"description": "Console message level to return, e.g. 'error' (default), 'warning', or 'log'.", "type": "string", "default": "error"},

        }, ["page_id"]),

    ('read_network_errors', 'T4a: failed/4xx/5xx requests.', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the page whose failed/4xx/5xx requests are listed.", "type": "string"},

        }, ["page_id"]),

    ('screenshot_page', 'T4a: PNG screenshot to .orchestrator_cache/screenshots/.', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the page captured as PNG.", "type": "string"}, "full_page": {"description": "When true, capture the full scrollable page; default false captures the viewport only.", "type": "boolean", "default": False},

            "save_path": {"description": "Optional explicit PNG output path; empty saves under .orchestrator_cache/screenshots/.", "type": "string", "default": ""},

        }, ["page_id"]),

    ('wait_for_selector', 'T4a: wait for visibility (timeout_ms).', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the page waited on.", "type": "string"}, "selector": {"description": "CSS selector awaited on the page.", "type": "string"},

            "timeout_ms": {"description": "Maximum wait for the selector to reach state_str, in milliseconds. Default 5000.", "type": "integer", "default": 5000},

            "state_str": {"description": "Playwright wait state: 'visible' (default), 'attached', 'detached', or 'hidden'.", "type": "string", "default": "visible"},

        }, ["page_id", "selector"]),

    ('compare_dom_snapshot', 'T4a: unified diff of selector text between two pages.', {

            "page_id_a": {"description": "First browser page (by page_id) whose selector text is snapshotted for the diff.", "type": "string"}, "page_id_b": {"description": "Second browser page (by page_id) whose selector text is diffed against page_id_a.", "type": "string"},

            "selector": {"description": "CSS selector whose text is compared on both pages. Default 'body'.", "type": "string", "default": "body"},

        }, ["page_id_a", "page_id_b"]),

    ('close_browser_page', 'Phase 2 Sprint 2 (plan 3.5) T4: explicit close of one page minted by open_browser_page/cdp_attach_target. Launched pages are closed; attached pages are DETACHED ONLY (the user app is never closed). Owner-checked at the bridge seam for HTTP calls.', {

            "page_id": {"description": "Browser page id returned by open_browser_page or cdp_attach_target; the page to close/detach.", "type": "string"},

        }, ["page_id"]),

    ('t4_host_status', 'Phase 2 Sprint 2 (plan 2/4) T1 read: T4-over-HTTP host status — daemon session id, console lock state, T4 loop alive + epoch, driver-lock holder, open pages (own pages in full; other owners as counts only), quarantine records, daemon generation. Not bridged: a plain daemon read.', {

            "agent_id": {"description": "Caller identity supplied by the transport. Unidentified HTTP callers receive counts only.", "type": "string", "default": ""},

        }, []),

    ("server_lifecycle_status", "T4 read-only: shared cached psutil port-owner scan. listening may be true, false or null (unknown); owner details may be absent/null. can_kill and requires_admin are username-based estimates, not permission probes. Includes family, laddr and scan/cache evidence when available.", {

            "port": {"description": "TCP port whose owning process / listening state is reported from the shared cached scan.", "type": "integer"},

        }, ["port"]),

    ("port_owner_inspect", "T4 read-only: inspect which process owns a port, returning listening state, pid, and process name. Success returns port, listening (boolean or null when unknown), pid (integer or null), process_name (string or null), plus response metadata. It is output-light, reusing the server_lifecycle_status scan/cache, not a separate cheaper scan. Errors pass through.", {

            "port": {"description": "TCP port to inspect for its owning process (listening state, pid, process name).", "type": "integer"},

        }, ["port"]),

    ('detect_stale_server', 'T4c: inspect localhost:<port> ownership and optional bundle version drift. This contract accepts port and expected_bundle_version only; use static_asset_cache_check or bundle_version_check for URL probes.', {

            "port": {"description": "Localhost port whose server ownership and bundle drift are inspected.", "type": "integer"},

            "expected_bundle_version": {"description": "Bundle version the running server is expected to serve; a mismatch with the served bundle marks the server stale. Empty skips the version check.", "type": "string", "default": ""},

        }, ["port"]),

    ('static_asset_cache_check', 'T4c: HEAD + 2 GET hash compare.', {

            "url": {"description": "Static asset URL probed with HEAD + two GET hash comparisons to detect cache staleness.", "type": "string"},

        }, ["url"]),

    ('bundle_version_check', 'T4c: extract a build/version marker from HTML. marker_regex must contain at least one capturing group around the version value; the default supports name="version" content="..." and ?v=... patterns.', {

            "html_url": {"description": "HTML page URL from which the build/version marker is extracted.", "type": "string"},

            "marker_regex": {"type": "string", "default": r'(?:name="version" content="([^"]+)"|\?v=([^"&\s]+))', "description": "Regex with at least one capture group; the first non-empty capture is returned."},

        }, ["html_url"]),

    ('restart_server_via_bat_hint', 'T4c: restart-command hint for a NON-[A] project server launched from its .bat; nothing is executed. [A] itself restarts only via restart_orchestrator_via_bat.', {

            "bat_path": {"description": "Path of the non-[A] project server's launcher .bat; the hint is built for this launcher and nothing is executed.", "type": "string"}, "port": {"description": "Port the project server listens on, used in the restart hint's verify step; 0 omits the port check.", "type": "integer", "default": 0},

        }, ["bat_path"]),

    ('restart_orchestrator_via_task', 'Legacy T4c High-IL Task Scheduler restart helper, kept for Anh-requested admin/UIPI recovery. Not a CA restart path: [A] restarts only via restart_orchestrator_via_bat.', {

            "delay_seconds": {"type": "integer", "default": 2, "description": "Helper waits this many seconds before killing the orchestrator (lets HTTP response flush). Clamped [1,10]."},

        }, []),

    ('cancel_pending_restart', 'V15 R14 T4c: kill the detached restart_helper.cmd if it has not yet killed the orchestrator. Useful when User queued a restart_orchestrator_via_task but changed mind during the delay window. Returns NO_PENDING_RESTART if no helper exists.', {}, []),

    ('check_window_visibility', 'V15 R15 P2.9 T4d: pre-action visibility check for desktop automation. Returns {visible, rect, minimized, off_screen, suggestion ∈ restore|focus|cdp_fallback|ok}. Call before desktop_click/fill to detect hidden/minimized/off-screen windows. Accepts hwnd OR title_pattern.', {

            "hwnd": {"description": "Window handle to check for visibility/minimized/off-screen state; 0 resolves the window by title_pattern instead.", "type": "integer", "default": 0},

            "title_pattern": {"description": "Title substring used to find the window when hwnd is 0; empty relies on hwnd alone.", "type": "string", "default": ""},

            "match_mode": {"type": "string", "default": "contains_case_insensitive", "description": "exact|contains|regex|case_insensitive|contains_case_insensitive"},

        }, []),

    ('cdp_attach_target', 'V15 R15 P2.6 / R44 T4a: attach Playwright to existing CDP target. Browser-level WS fallback via /json/version is opt-in over HTTP (allow_browser_level_fallback=true). Returns attach_id == page_id consumable by click/fill/evaluate_js/read_page/query_selector + attach_via field showing which path succeeded. For HTTP, provide target_id from list_cdp_targets and an expected title or URL. R44: pass project_path so subsequent T4 mutating tools (fill_value/click) resolve the active session via page_id alone.', {

            "target_id": {"type": "string", "default": "", "description": "Target id from list_cdp_targets response. Required over HTTP, including when websocket_url is supplied."},

            "cdp_port": {"description": "Chrome DevTools Protocol port of the already-running target to attach to. Default 9223.", "type": "integer", "default": 9223},

            "websocket_url": {"type": "string", "default": "", "description": "Optional direct ws:// endpoint. HTTP still requires target_id and the expected title or URL. The direct handler also supports a websocket_url-only attach."},

            "project_path": {"type": "string", "default": "", "description": "R44: originating project root — pinned to page state so fill_value/click/evaluate_js can resolve active session by page_id without re-passing project_path."},

            "expected_title_substr": {"type": "string", "default": "", "description": "Sprint 2 (plan 5.2(b)/(c)): caller-declared expectation for bridged attach — REQUIRED (with expected_url_substr, at least one non-empty) for calls over HTTP; verified exactly once post-connect against live pages (unique live match)."},

            "expected_url_substr": {"type": "string", "default": "", "description": "Sprint 2 (plan 5.2(b)/(c)): caller-declared expectation for bridged attach — see expected_title_substr."},

            "allow_browser_level_fallback": {"type": "boolean", "default": False, "description": "Sprint 2 (plan 5.2(f)): opt in to the browser-level attach fallback; refused by default for bridged calls (CDP_ATTACH_FALLBACK_REFUSED)."},

        }, ["target_id"]),

    ('inspect_project_runtime', 'V15 R16 A.3 T1: pre-flight introspection. Detects launcher_type (electron-bat|electron-npm-script|node|python|unknown), launcher_path, has_remote_debugging_in_launcher, npm_scripts, recent_logs, dependency highlights. Call BEFORE launching to decide which recipe to use.', {

            "project_path": {"type": "string", "description": "Absolute project root"},

        }, ["project_path"]),

    ('launch_electron_with_cdp', 'Stateful MCP launch and attach. Over HTTP, start the app with remote debugging enabled and use list_cdp_targets followed by cdp_attach_target.', {

            "launcher_path": {"type": "string", "default": "", "description": ".bat/.ps1 launcher path; if empty, auto-find via inspect_project_runtime(project_path)"},

            "project_path": {"type": "string", "default": "", "description": "Project root (used when launcher_path empty)"},

            "cdp_port": {"description": "CDP port injected into the Electron launch via --remote-debugging-port and then attached to. Default 9223.", "type": "integer", "default": 9223},

            "verify_after_ms": {"type": "integer", "default": 6000, "description": "Wait time before verifying CDP port"},

        }, []),

    ('dom_state_diff', 'V15 R16 A.4 T1: snapshot DOM element state (disabled/bg/color/className/visible/text) and compare across time. page_id must be a live pg-... ID. Two modes: no baseline_id returns a snapshot_id cached 10 minutes; baseline_id compares against that snapshot and must be recreated after restart or page replacement.', {

            "page_id": {"type": "string", "pattern": "^(pg|cdp)-[A-Za-z0-9][A-Za-z0-9_-]*$", "description": "Only the pg-.../cdp-... page_id returned by cdp_attach_target or open_browser_page"},

            "selector": {"type": "string", "description": "CSS selector for the element to snapshot"},

            "baseline_id": {"type": "string", "default": "", "description": "From a previous dom_state_diff call without this param; TTL 10 minutes and invalid after restart/page replacement"},

            "snapshot_fields": {"type": "array", "default": ["disabled","bg","color","className","visible","text"], "description": "Optional subset: disabled|bg|color|className|visible|text|value|rect"},

        }, ["page_id", "selector"]),

    ('extract_log_section', 'V15 R16.7 A.5 T1: find DOM section by title text → traverse to closest area_class ancestor → extract textarea/pre/.log-body content. Smart pick: prefers [data-log] attr, then id matching *logArea*, then longest content. Returns content + is_empty + alternative_bodies for re-pick. R16.7: max_chars + tail (default head) for log compaction.', {

            "page_id": {"description": "Browser page id whose DOM is searched for the log section.", "type": "string"},

            "section_title": {"type": "string", "description": "Exact or substring match for title element text"},

            "area_class": {"type": "string", "default": "log-area", "description": "Class of ancestor container holding the log body"},

            "body_selector": {"type": "string", "default": "", "description": "Override body locator (default: 'textarea, pre, .log-body, .log-content, [data-log]')"},

            "pick_strategy": {"type": "string", "enum": ["smart", "longest", "first"], "default": "smart", "description": "R16.2: how to pick when area has multiple matching bodies. smart = [data-log] > id*logArea > longest > first."},

            "max_chars": {"type": "integer", "default": 0, "description": "R16.7: truncate content to N chars (0 = no cap). truncated=true + original_length in response if exceeded."},

            "tail": {"type": "boolean", "default": False, "description": "R16.7: when truncating, keep tail (most recent log lines) instead of head."},

            "mode": {"type": "string", "enum": ["full", "ref", "preview"], "default": "full", "description": "R16.9: full=current; ref=metadata-only (hash+length+head_200+tail_200, no content); preview=head_200+tail_200 with truncation marker — saves 95% tokens"},

            "baseline_sha256": {"type": "string", "default": "", "description": "R27: pass prior `current_baseline_sha256_prefix` to get only `new_content_since_baseline` delta. In-memory cache TTL 10min."},

        }, ["page_id", "section_title"]),

    ('fill_value', 'V15 R16.2 A.6 T2: transport-safe DOM fill. encoding=plain uses Playwright .fill(); encoding=base64 decodes in browser via atob+TextDecoder, preserving UNC paths/UTF-8/multi-line WITHOUT JSON backslash strip. Always dispatches input+change events. R16.2: verify=true (default) auto reads back, returns matches_intent + verify_drift.', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the DOM fill happens on this page.", "type": "string"},

            "selector": {"description": "CSS selector of the element whose value is set.", "type": "string"},

            "value": {"type": "string", "description": "Plain text OR base64 of UTF-8 bytes"},

            "encoding": {"description": "'plain' (default) fills via Playwright .fill(); 'base64' decodes the value in-browser so UNC paths, UTF-8, and multi-line text survive transport.", "type": "string", "enum": ["plain", "base64"], "default": "plain"},

            "verify": {"type": "boolean", "default": True, "description": "R16.2: post-write read-back verify. matches_intent in response."},

        }, ["page_id", "selector", "value"]),

    ('read_value', 'V15 R16.3 T1: symmetric counterpart to fill_value. Reads element.value (or textContent fallback) without evaluate_js boilerplate. encoding=base64 wraps content for transport-safe UNC/UTF-8/multi-line round-trip.', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the element value is read from this page.", "type": "string"},

            "selector": {"description": "CSS selector of the element whose value (or textContent fallback) is read.", "type": "string"},

            "encoding": {"description": "'plain' (default) returns the raw value; 'base64' wraps it for transport-safe UNC/UTF-8/multi-line round-trip.", "type": "string", "enum": ["plain", "base64"], "default": "plain"},

            "max_chars": {"type": "integer", "default": 100000, "description": "Truncate cap; truncated=true in response if exceeded"},

        }, ["page_id", "selector"]),

    ('read_all_logs', 'V15 R16.4 T1: batch read multiple log panels in one call (default: System Error Log + User Error & Help + Agent Task Log + AI Log Analysis). Each panel uses smart pick like extract_log_section. R16.6: include_view_panels=true also pulls <textarea id^=view-> JSON tabs. Saves N round-trips.', {

            "page_id": {"description": "Browser page id whose log panels are batch-read.", "type": "string"},

            "panels": {"type": "array", "default": [], "description": "List of panel titles to read; empty = default 4 Rawcut panels"},

            "area_class": {"description": "CSS class marking each log panel's container area. Default 'log-area'.", "type": "string", "default": "log-area"},

            "encoding": {"description": "'plain' (default) or 'base64' for transport-safe return of log text with special characters.", "type": "string", "enum": ["plain", "base64"], "default": "plain"},

            "max_chars": {"type": "integer", "default": 30000, "description": "Per-panel cap"},

            "include_view_panels": {"type": "boolean", "default": False, "description": "R16.6: enumerate view-* textareas (Rawcut JSON view tabs)"},

            "view_id_pattern": {"type": "string", "default": "^view-", "description": "Regex for view panel id"},

            "auto_artifact": {"type": "boolean", "default": False, "description": "R18: when response > max_bytes, auto-write to artifact file and return preview + sha256"},

            "project_path": {"type": "string", "default": "", "description": "R18: required when auto_artifact=true"},

            "round_id": {"type": "string", "default": "", "description": "R18: artifact subdir name"},

            "auto_artifact_max_bytes": {"type": "integer", "default": 4096, "description": "R18: threshold to trigger artifact write"},

            "tail": {"type": "boolean", "default": False, "description": "R30: when truncating by max_chars, keep TAIL (most recent log entries) instead of head"},

        }, ["page_id"]),

    ('run_script', 'V15 R16.4 T4b: escape-free JS executor. Read JS expression from `script_path` (.js file) OR `script_inline`, evaluate in browser page, return result. Bypasses shell/JSON quote-escape pain. File contents must be a single JS expression (wrap multi-statement in IIFE). EXECUTE-gated.', {

            "page_id": {"description": "Browser page id in which the script file/inline expression is evaluated.", "type": "string"},

            "script_path": {"type": "string", "default": "", "description": "Absolute path to .js file. Takes precedence over script_inline."},

            "script_inline": {"type": "string", "default": "", "description": "Inline JS source if no file"},

            "timeout_ms": {"description": "Maximum execution time for the script in the page, in milliseconds. Default 10000.", "type": "integer", "default": 10000},

        }, ["page_id"]),

    ('manage_agent', "R86 T3: central multi-agent config manager. actions: register (upsert agent into agents_registry.json), unregister, list, get, set_mode, sync_rule (rewrite global-rule body for target id or 'all', persona line preserved), sync_mcp (rewrite project-orchestrator MCP block; formats json/vscode/toml), sync_skill (native: copy canonical .ToolA_Skill/skills/<skill_name> folder to each target agent skill_dir when skill_format=folder -- native copy is verified for any agent whose registry row has a real skill_dir, so read agents_registry.json rather than an id list; adapter: skill_format=agents_md_ref/rule_file_append writes a managed skill reference into rule_file for agents that have a rule_file but no skill_dir -- adapter writes only when target is an explicit id + dry_run=false, else reports adapter_needed; agents with neither report not_verified -- never not_supported), sync_hook (install + register the native read/write enforcement gates: copies the repo SOT gates + adapter into the agent's hooks_dir, writes hook-agent.json, merge-registers entries into hook_file -- idempotent, unrelated hooks preserved, .bak_sync_hook backup -- then runs a live probe returning read_deny/read_allow/write_deny/outside_deny/memory_allow -- the allow leg is the agent own auto-memory tree, the one native write the Matrix permits; agents with hook_format=none skip with coverage=none; trust_step names any manual step Anh must run). backup .bak_ma; validates SKILL.md + canonical source. Auto-backup + validate + rollback on failure. dry_run=true previews.", {

            "action": {"description": "Registry operation: 'register', 'unregister', 'list', 'get', 'set_mode', 'sync_rule', 'sync_mcp', 'sync_skill', or 'sync_hook'.", "type": "string", "enum": ["register", "unregister", "list", "get", "set_mode", "sync_rule", "sync_mcp", "sync_skill", "sync_hook"]},

            "agent_id": {"type": "string", "default": "", "description": "the agent for register/unregister/get/set_mode; those four ignore target. agents_registry.json is SOT for valid ids."},

            "target": {"type": "string", "default": "all", "description": "sync_rule/sync_mcp/sync_skill/sync_hook ONLY: one agent id or 'all'. register/unregister/get/set_mode ignore target and read agent_id."},

            "persona": {"description": "Persona line written for the agent on register/sync_rule; preserved across rule rewrites. Empty leaves the existing persona unchanged.", "type": "string", "default": ""},

            "rule_file": {"description": "Path of the agent's rule file targeted by sync_rule; empty resolves the default rule file for the agent id.", "type": "string", "default": ""},

            "rule_format": {"description": "How the rule body is stored in rule_file: 'markdown' (default) writes the body directly, 'json_field' embeds it in a JSON field.", "type": "string", "enum": ["markdown", "json_field"], "default": "markdown"},

            "rule_template": {"description": "Rule template used when generating a rule file: 'slim' (default) or a named full template.", "type": "string", "default": "slim"},

            "mcp_file": {"description": "Path of the MCP client config file targeted by sync_mcp; empty resolves the agent's default config location.", "type": "string", "default": ""},

            "mcp_format": {"description": "MCP config file format: 'json' (default), 'vscode', or 'toml' — decides how the project-orchestrator entry is serialized by sync_mcp.", "type": "string", "enum": ["json", "vscode", "toml"], "default": "json"},

            "mcp_key": {"description": "Server key under which project-orchestrator is registered in the MCP config. Default 'project-orchestrator'.", "type": "string", "default": "project-orchestrator"},

            "dry_run": {"description": "When true, preview the registry/config change without writing it; default false applies it.", "type": "boolean", "default": False},

            "skill_name": {"type": "string", "default": "", "description": "skill folder name under .ToolA_Skill/skills for sync_skill"},

            "skill_dir": {"type": "string", "default": "", "description": "target skills root for register"},

            "skill_format": {"type": "string", "enum": ["folder", "agents_md_ref", "rule_file_append"], "default": "folder", "description": "folder=native SKILL.md copy into the agent's registry skill_dir; agents_md_ref/rule_file_append=adapter that writes a managed skill reference into rule_file, for agents that have a rule_file but no skill_dir. Which agent is which comes from agents_registry.json, never from an id list in this text. Native load is NOT assumed for adapters."},

            "mode": {"type": "string", "enum": ["A", "native"], "default": "", "description": "set_mode/register: A = [A]-first (DEFAULT for every CA), native = Anh-approved temporary opt-out; set_mode regenerates the agent's rule file immediately"},

            "cleanup": {"type": "boolean", "default": True, "description": "unregister: also strip the managed rule body (persona kept) and remove the [A] MCP block from json-format mcp_file"},

        }, ["action"]),

    ('query_dom_candidates', 'V15 R16.6 T1: the single supported route for DOM candidate queries (search_code no longer serves mode=dom_candidates; direct calls are redirected here). Enumerate DOM input/button/textarea/contenteditable elements with metadata. filter in all|inputs|buttons|textareas|links|contenteditable. R18: auto_artifact for oversize results.', {

            "page_id": {"type": "string", "description": "Page/session id to inspect."},

            "filter": {"type": "string", "enum": ["all", "inputs", "buttons", "textareas", "links", "contenteditable"], "default": "all", "description": "DOM candidate category filter."},

            "max_items": {"type": "integer", "default": 200, "description": "Maximum candidates to return."},

            "auto_artifact": {"type": "boolean", "default": False, "description": "R18: when response > max_bytes, auto-write to artifact file"},

            "project_path": {"type": "string", "default": "", "description": "Optional project root for artifact placement."},

            "round_id": {"type": "string", "default": "", "description": "Optional round id used in artifact names."},

            "auto_artifact_max_bytes": {"type": "integer", "default": 4096, "description": "Maximum inline response bytes before artifact fallback."},

        }, ["page_id"]),

    ('describe_page', 'V15 R16.6 T1: DOM structure map — lists all titled sections + view-* panels in 1 call. R18: auto_artifact for oversize results.', {

            "page_id": {"description": "Browser page id whose DOM structure (titled sections + view-* panels) is mapped.", "type": "string"},

            "area_class": {"description": "CSS class grouping the page areas listed in the structure map. Default 'log-area'.", "type": "string", "default": "log-area"},

            "view_id_pattern": {"type": "string", "default": "^view-", "description": "Regex for view panel id matching"},

            "auto_artifact": {"type": "boolean", "default": False, "description": "R18: when response > max_bytes, auto-write to artifact file"},

            "project_path": {"description": "Optional project root used to resolve session/artifact context for oversize results; empty resolves from the page's pinned session.", "type": "string", "default": ""},

            "round_id": {"description": "Optional round identifier; when set, oversize structure maps are stored as artifacts under this round. Empty disables round-scoped artifacts.", "type": "string", "default": ""},

            "auto_artifact_max_bytes": {"description": "Size ceiling in bytes before the structure map is spilled to an artifact instead of returned inline. Default 4096.", "type": "integer", "default": 4096},

        }, ["page_id"]),

    ('cleanup_test_session', 'Release the named test page. A launched page closes and a CDP attachment detaches. Requires a mutation token.', {

            "project_path": {"description": "Project root for the session gate.", "type": "string", "default": ""},
            "page_id": {"type": "string", "description": "Owned page returned by open_browser_page or cdp_attach_target."},
            "processes": {"type": "array", "default": [], "description": "Compatibility field. Non-empty process lists are refused."},
            "close_session_after": {"type": "boolean", "default": False, "description": "Compatibility field. Closing a session requires a separate call with its exact ID."},

            "idle_guard_signals": {"type": "array", "default": [], "description": "R43 P0 #6A: signals[] array same shape as assert_target_idle. If provided and outcome != idle, cleanup is refused (CLEANUP_BLOCKED_TARGET_BUSY). Empty array = skip guard (legacy behavior)."},

            "idle_guard_stable_seconds": {"type": "integer", "default": 0, "description": "R43 P0 #6A: override stable_seconds for all signals (0 = use per-signal value)."},

            "idle_guard_force": {"type": "boolean", "default": False, "description": "R43 P0 #6A: bypass idle guard. Use only when test intentionally cleaning up a failed/aborted run — note this in CA reply."},

        }, ["page_id"]),

    ('click_selector', 'V15 R16.9 T4: alias of click() that snapshots target element state BEFORE + AFTER click — returns enabled/disabled, visible, bg/color, className, value_length deltas in 1 call. Replaces CA pattern of (dom_state_diff → click → dom_state_diff). Pass before_after_state=false for pure click. Routed on both transports (HTTP + MCP) after the T4-11 fix.', {

            "page_id": {"description": "Browser page id returned by open_browser_page; the snapshotted click happens on this page.", "type": "string"},

            "selector": {"description": "CSS selector of the element clicked, with its state snapshotted before and after.", "type": "string"},

            "before_after_state": {"type": "boolean", "default": True, "description": "Snapshot element state pre/post click"},

            "state_fields": {"type": "array", "default": [], "description": "Default: disabled|visible|bg|color|className|value_length. Add 'text_preview' for first 100 text chars."},

            "timeout_ms": {"description": "Maximum time to wait for the selector to be actionable before clicking, in milliseconds. Default 5000.", "type": "integer", "default": 5000},

        }, ["page_id", "selector"]),

    ('read_skill', 'V15 R16.9 / R22 T1: read skill body by short name. Resolves <orchestrator>/.ToolA_Skill/skills/<name>/SKILL.md. R22 verbosity ∈ {full|terse|ref} — terse drops path_absolute/total_lines/shown_lines/max_lines/truncated metadata (saves ~300 tokens).', {

            "name": {"type": "string", "description": "Folder name or prefix, e.g. '18' or '18_runtime_dom_T4' or 'runtime_dom'"},

            "max_lines": {"type": "integer", "default": 400, "description": "Cap; truncated=true in response if exceeded. Max 2000."},

            "verbosity": {"type": "string", "enum": ["full", "terse", "ref"], "default": "full", "description": "R22: terse keeps result+skill_name only."},

        }, ["name"]),

    ('checkpoint_loop_dom_state', 'V15 R16.9 / R16.10 T4: poll DOM element state server-side and report changes from a baseline snapshot. R16.10: accepts `baseline_id` (from prior dom_state_diff, TTL 10min) OR `baseline_fields` (inline state dict) — fixes baseline-timing pitfall when CA clicked before calling loop. If both empty, fresh-baseline at t=0 (R16.9 legacy). Returns baseline, baseline_source, final_state, changed, polls, elapsed_sec.', {

            "page_id": {"description": "Browser page id whose element state is polled against the baseline snapshot.", "type": "string"},

            "selector": {"description": "CSS selector of the element whose DOM state is polled for changes.", "type": "string"},

            "interval_sec": {"type": "number", "default": 15.0, "description": "Poll cadence in seconds (min 0.5)"},

            "timeout_sec": {"type": "number", "default": 90.0, "description": "Max wall-clock to wait"},

            "fields": {"type": "array", "default": [], "description": "Default: disabled|visible|bg|color|className|value_length"},

            "stop_on_change": {"type": "boolean", "default": True, "description": "Return early on first field change"},

            "baseline_id": {"type": "string", "default": "", "description": "R16.10: snapshot_id from prior dom_state_diff (TTL 10min). Priority 1."},

            "baseline_fields": {"type": "object", "default": {}, "description": "R16.10: inline baseline state dict, e.g. {disabled:true,bg:'rgb(150,150,150)'}. Priority 2."},

        }, ["page_id", "selector"]),

    ('ensure_single_electron_instance', "Inspect running Electron processes and CDP port usage. With kill_existing=true, terminates electron.exe processes by image name across the desktop. To attach over HTTP, start the app with remote debugging enabled, then use list_cdp_targets and cdp_attach_target.", {

            "project_path": {"description": "Project context reported in the result. The process scan and kill are system-wide.", "type": "string", "default": ""},

            "cdp_port": {"description": "CDP port that must be freed before the fresh Electron launch. Default 9223.", "type": "integer", "default": 9223},

            "kill_existing": {"type": "boolean", "default": True, "description": "False = inspect only, no kill"},

        }, []),

    ('click_and_checkpoint_loop', 'V15 R16.10 T4: atomic click + checkpoint loop. Snapshots watch_selector state BEFORE click (correct baseline timing), clicks click_selector_str, polls watch_selector until state changes OR timeout. Eliminates baseline-after-click pitfall. watch_selector defaults to click_selector_str.', {

            "page_id": {"description": "Browser page id on which the atomic click + checkpoint poll runs.", "type": "string"},

            "click_selector_str": {"type": "string", "description": "Element to click (e.g. '#btnStep1')"},

            "watch_selector": {"type": "string", "default": "", "description": "Element whose state to observe (defaults to click_selector_str)"},

            "interval_sec": {"description": "Polling interval in seconds between watch_selector state checks. Default 15.0.", "type": "number", "default": 15.0},

            "timeout_sec": {"description": "Overall deadline in seconds for a watched state change after the click. Default 90.0.", "type": "number", "default": 90.0},

            "fields": {"type": "array", "default": [], "description": "Default: disabled|visible|bg|color|className|value_length"},

            "stop_on_change": {"description": "When true (default), stop polling at the first watched-state change; false polls until timeout and reports every change.", "type": "boolean", "default": True},

            "pre_click_settle_ms": {"type": "integer", "default": 200, "description": "Delay between baseline snapshot and click (lets baseline render commit)"},

        }, ["page_id", "click_selector_str"]),

    ('ui_state_summary', 'V15 R16.10 T1: structured TEXT-only state summary per UI region. NOT a screenshot (saves ~99% tokens vs base64 image). Returns visibility/disabled/text_preview/value_preview/classes/has_error_class/bg/rect per region. Default regions (empty list) = all [data-region] + section[id].', {

            "page_id": {"description": "Browser page id whose UI regions are summarized as text.", "type": "string"},

            "regions": {"type": "array", "default": [], "description": "List of {label, selector}; empty = auto-discover via [data-region] + section[id]"},

        }, ["page_id"]),

    ('alias_contract_test', 'V15 R20/R21 T1: regression smoke for the alias contract. Default = in-process _normalize_body() probe for every (tool, legacy_field) in _ARG_ALIASES + _HARMLESS_IGNORE_FIELDS. R21: include_http_smoke=true ALSO runs live HTTP probes for historically failing cases (round 8/9 audit list). Returns alias_contract_intact + per-tool details.', {

            "tool_filter": {"type": "string", "default": "", "description": "Optional substring filter on tool name to test a subset"},

            "include_http_smoke": {"type": "boolean", "default": False, "description": "R21: also run live HTTP /api/call/{tool} probes — covers cases that in-process normalize might miss"},

            "http_sample": {"type": "array", "default": [], "description": "R21: override default historically-failing sample with [{tool, payload}]"},

        }, []),

    ('architecture_audit_compact', 'V15 R20 T1: 4-layer (KtrB/KtrSK/KtrSH/KtrI) compact audit digest. Combines matrix audit + alias drift + progressive gaps + inventory classification into ~30-line digest. ~500 tokens vs ~5000 for raw audit_tool_matrix.', {

            "project_path": {"description": "Project root the compact 4-layer audit digest is computed for; empty defaults to the ToolA install.", "type": "string", "default": ""},

        }, []),

    ('acceptance_dashboard', 'V15 R29 T1: SINGLE-CALL acceptance dashboard. Aggregates matrix + alias_contract_test + (optional) inspect_project_usage probe + schema weak/mismatch checks into one ~500-token digest with pass/fail vs R28/R29 targets. Use instead of 10 separate audit calls. probe_path optional: triggers Rawcut/app scan probe in same response.', {

            "project_path": {"description": "Project root the acceptance dashboard aggregates gates for; empty defaults to the ToolA install.", "type": "string", "default": ""},

            "probe_path": {"type": "string", "default": "", "description": "Optional: run inspect_project_usage on this app/project path for Rawcut-style scan health check"},

        }, []),

    ('folder_exists', 'V15 R27 T1: minimal folder existence + type probe.', {

            "path": {"description": "Folder path probed for existence and type (file vs directory).", "type": "string"},

        }, ["path"]),

    ('create_folder', 'V15 R65 T2 file-artifact mutation: create a directory. Idempotent mkdir; an existing directory is SUCCESS (created=false). recursive=true creates the parent tree like mkdir -p. Requires normal mutation authorization. Path is a plain JSON string so spaces/non-ASCII survive with no shell quoting.', {

            "path": {"type": "string", "description": "Absolute folder path to create. Windows path with spaces/diacritics OK (JSON string, no shell)."},

            "project_path": {"type": "string", "default": "", "description": "Optional project root for session/backup binding. Declared so the session gate can see it (avoids PROJECT_PATH_REQUIRED vs stripped-field catch-22)."},

            "recursive": {"type": "boolean", "default": True, "description": "Create parent tree (mkdir -p). Set false to require the parent to already exist."},

        }, ["path"]),

    ('create_folders', 'V15 R65 T2 file-artifact mutation: batch create_folder. Pass a flat array of string paths. Requires normal mutation authorization. ok=true only when every path succeeded; a bad path does not abort the rest.', {

            "paths": {"type": "array", "description": "List of absolute folder paths to create."},

            "project_path": {"type": "string", "default": "", "description": "Optional project root for session/backup binding. Declared so the session gate can see it (avoids PROJECT_PATH_REQUIRED vs stripped-field catch-22)."},

            "recursive": {"description": "When true (default), create missing parent folders along each path; false fails a path whose parent is missing.", "type": "boolean", "default": True},

        }, ["paths"]),

    ('folder_stats', "V15 R27 T1 (R64-C): folder stats (file_count, dir_count, total_bytes, latest_mtime, top_extensions). recursive=false by default (matches `dir`). R64-C: response now includes `subdirs_present_hint` when non-recursive AND file_count=0 AND dir_count>0, so CA doesn't mistake 'subdir-only folder' for 'empty folder' (live drift hit on getlink_output during rawcut_getfootage_sheet_v0 step 25).", {

            "path": {"description": "Folder whose file/dir counts, total bytes, latest mtime, and top extensions are reported.", "type": "string"},

            "recursive": {"type": "boolean", "default": False, "description": "When true, walk all subdirectories via rglob. When false, only top-level entries (matches `dir`)."},

            "ignore_patterns": {"type": "array", "default": [], "description": "Substring patterns to skip"},

            "hint_when_subdirs_present": {"type": "boolean", "default": True, "description": "V15 R64-C: include `subdirs_present_hint` in response when non-recursive AND file_count=0 AND dir_count>0. Set false to suppress the hint."},

        }, ["path"]),

    ('folder_diff', 'V15 R27 T1: compare top-level contents of 2 folders. compare_by ∈ name|name+size|name+sha (sha=slow opt-in).', {

            "path_a": {"description": "First folder in the top-level content comparison.", "type": "string"},

            "path_b": {"description": "Second folder compared against path_a.", "type": "string"},

            "ignore_patterns": {"description": "Glob patterns excluded from the folder comparison (names matching any pattern are skipped on both sides).", "type": "array", "default": []},

            "compare_by": {"description": "Comparison key: 'name' (default), 'name+size', or 'name+sha' (sha hashes contents, slower opt-in).", "type": "string", "enum": ["name", "name+size", "name+sha"], "default": "name"},

        }, ["path_a", "path_b"]),

    ('fill_folder_dialog', 'Fill a pinned native folder dialog, read back the entered path, and observe dialog closure. HTTP requires parent_hwnd or both owner_pid and a non-empty title_pattern. Verify the application selection separately.', {

            "title_pattern": {"type": "string", "default": "", "description": "Regex against dialog title; combined with auto_try_common_titles"},

            "folder_path": {"description": "Folder path to enter and read back before submitting the dialog.", "type": "string"},

            "timeout_ms": {"description": "Overall timeout in milliseconds for finding and filling the dialog. Default 5000.", "type": "integer", "default": 5000},

            "wait_for_dialog_ms": {"description": "Time in milliseconds to wait for the folder dialog to appear before giving up. Default 3000.", "type": "integer", "default": 3000},

            "auto_try_common_titles": {"description": "Try common titles on direct calls. The HTTP bridge forces this off and uses the supplied pin.", "type": "boolean", "default": True},

            "electron_fallback_hint": {"type": "object", "default": {}, "description": "Legacy compatibility field. Recovery guidance uses the current dialog census."},

            "owner_pid": {"type": "integer", "default": 0, "description": "R31: only match windows owned by this PID (e.g. Rawcut electron PID) — eliminates CapCut/browser false-match"},

            "parent_hwnd": {"type": "integer", "default": 0, "description": "R31: only match dialogs whose parent/owner hwnd is this app window"},

            "class_name_pattern": {"type": "string", "default": "", "description": "R31: restrict by class regex (e.g. '#32770')"},

            "exclude_titles": {"type": "array", "default": [], "description": "R31: reject windows whose title contains any of these substrings"},

            "launched_after_ts": {"type": "string", "default": "", "description": "R31: ISO timestamp; only windows whose process created after"},

            "dry_run": {"type": "boolean", "default": False, "description": "R31: return candidate list WITHOUT mutating"},

            "try_modern_picker": {"type": "boolean", "default": True, "description": "Expose the address Edit with Ctrl+L, set and read back the path, then press Enter."},

            "expected_hwnd": {"type": "integer", "default": 0, "description": "Bridge-set only (T4-over-HTTP 5.2(g)): hwnd bound by the bridge census; the handler re-checks it before acting. Caller-supplied values are discarded by the bridge."},

        }, ["folder_path"]),

    ('folder_dialog_snapshot', 'V15 R31 T1: read-only discovery of open folder/file dialog candidates. Use BEFORE fill_folder_dialog to verify CA will target the right window. Returns dialog candidates with full metadata (pid, process_name, class, parent_hwnd, owner_hwnd, child Edit/Button counts) + classifies as classic_win32_with_edit | modern_ifiledialog_no_edit. Pair with owner_pid filter.', {

            "owner_pid": {"type": "integer", "default": 0, "description": "Filter only windows owned by this PID"},

            "parent_hwnd": {"type": "integer", "default": 0, "description": "Filter only windows whose parent/owner is this hwnd"},

            "title_pattern": {"type": "string", "default": "", "description": "Override default common-folder-dialog title regex"},

            "launched_after_ts": {"type": "string", "default": "", "description": "ISO timestamp filter"},

            "include_child_controls": {"description": "When true (default), include each dialog candidate's child controls (buttons, edit fields) in the snapshot metadata.", "type": "boolean", "default": True},

        }, []),

    ('cancel_native_dialog', 'Dismiss one pinned native dialog. HTTP requires parent_hwnd or both owner_pid and a non-empty title_pattern.', {
            "owner_pid": {"type": "integer", "default": 0, "description": "Owning PID, paired with title_pattern."},
            "parent_hwnd": {"type": "integer", "default": 0, "description": "Parent or owner HWND identifying the dialog."},
            "exclude_titles": {"type": "array", "items": {"type": "string"}, "description": "Exclude titles containing these strings."},
            "title_pattern": {"type": "string", "default": "", "description": "Title pattern required with the PID-only pin."},

        "button_pattern": {"description": "Button label pattern clicked to dismiss the dialog. Default 'Cancel|Huy|Close|Dong'; falls back to WM_CLOSE when no button matches.", "type": "string", "default": "Cancel|Huy|Close|Dong"},

            "expected_hwnd": {"type": "integer", "default": 0, "description": "Bridge-set only (T4-over-HTTP 5.2(g)): hwnd bound by the bridge census; the handler re-checks it before acting. Caller-supplied values are discarded by the bridge."},

        }, []),

    ('workflow_list', 'V15 R35 T1: list `.md` workflows from project (`<project>/.orchestrator_cache/workflows/`) and global (`<[A]>/.ToolA_Skill/workflows/`). Merge by name — project overrides global. Returns name, title, step_count, status (active/idle/complete), source, path.', {

            "project_path": {"description": "Project root whose project-level workflows are merged over the global ones; empty lists global workflows only.", "type": "string", "default": ""},

        }, []),

    ('workflow_start', 'V15 R35 / R37 Y1 T3: start workflow by name. Creates state file (current_step=1), returns step 1 body. State is GLOBAL (R37) — project_path optional.', {

            "name": {"description": "Workflow name (its .md file stem) to start; creates the state file at step 1.", "type": "string"},

            "project_path": {"type": "string", "default": "", "description": "Optional (R37: state is global)"},

        }, ["name"]),

    ('workflow_get_current', 'V15 R35 / R37 Y1 T1: read current step body (idempotent). project_path optional.', {

            "name": {"description": "Workflow name whose current step body is read.", "type": "string"},

            "project_path": {"description": "Project root used to resolve the workflow; optional because workflow state is global (R37).", "type": "string", "default": ""},

        }, ["name"]),

    ('workflow_submit_step', "V15 R35 / R37 / R42 T3 / R77 / R92: submit step (done|fail). done→advance; fail→pause. R42: also accepts step_range=[start, end] for batch advance (status='done' only). R92: accepts next_step=<declared step number> for branch workflows without workflow_goto_step. On STEP_MISMATCH returns delta + suggestion + alternative_tool=workflow_goto_step. If step has [completion_signal: ...], auto-call assert_target_idle. R77: if step has [require_artifact: ...], you MUST pass artifacts={key: abs_path}; engine verifies the file exists on disk (existence/size/json-items/freshness) and returns ARTIFACT_GATE_FAILED if not — fabricated 'done' is rejected. Gated steps cannot be batch-advanced (ARTIFACT_GATE_REQUIRES_SEQUENTIAL).", {

            "name": {"description": "Workflow name whose current step is submitted.", "type": "string"},

            "step": {"type": "integer", "default": 0, "description": "Single-step mode: must match current_step"},

            "step_range": {"type": "array", "default": [], "description": "R42 batch mode: [start, end]. start MUST equal current_step. status must be 'done'. R77: rejected if any step in range has required artifacts."},

            "status": {"description": "Step outcome: 'done' (default) advances to the next step, 'fail' pauses the workflow.", "type": "string", "enum": ["done", "fail"], "default": "done"},

            "evidence": {"description": "Evidence text recorded with the step submission (what was done / how it was verified).", "type": "string", "default": ""},

            "project_path": {"description": "Project root used to resolve the workflow; optional because workflow state is global (R37).", "type": "string", "default": ""},

            "artifacts": {"type": "object", "default": {}, "description": "R77: {artifact_key: absolute_path} map for steps with [require_artifact: key=...]. Engine checks each path on disk before accepting done. Required only for gated steps."},

            "next_step": {"type": "integer", "default": 0, "description": "R92 branch mode: after submitting current step done, advance to this declared workflow step instead of the next sequential step."},

        }, ["name"]),

    ('workflow_retry_step', 'V15 R35 / R37 Y1 T3: retry current step after fail/pause.', {

            "name": {"description": "Workflow name whose failed/paused current step is retried.", "type": "string"},

            "project_path": {"description": "Project root used to resolve the workflow; optional because workflow state is global (R37).", "type": "string", "default": ""},

        }, ["name"]),

    ('workflow_skip_step', 'V15 R35 / R37 Y1 T3: skip current step, advance to next.', {

            "name": {"description": "Workflow name whose current step is skipped.", "type": "string"},

            "reason": {"description": "Why the step is being skipped; recorded in the workflow state.", "type": "string", "default": ""},

            "project_path": {"description": "Project root used to resolve the workflow; optional because workflow state is global (R37).", "type": "string", "default": ""},

        }, ["name"]),

    ('workflow_reset', 'V15 R35 / R37 Y1 T3: reset workflow state back to step 1. Archives old state.', {

            "name": {"description": "Workflow name whose state is reset back to step 1 (old state is archived).", "type": "string"},

            "project_path": {"description": "Project root used to resolve the workflow; optional because workflow state is global (R37).", "type": "string", "default": ""},

        }, ["name"]),

    ('workflow_status', 'V15 R35 / R37 Y1 T1: full workflow state (current_step, step statuses, timestamps, evidence).', {

            "name": {"description": "Workflow name whose full state (current step, step statuses, timestamps, evidence) is returned.", "type": "string"},

            "project_path": {"description": "Project root used to resolve the workflow; optional because workflow state is global (R37).", "type": "string", "default": ""},

        }, ["name"]),

    ('workflow_create_from_ui', 'V15 R35 / R37 Y1 T3: save MD body. [A] does NOT transform. R37: workflows are GLOBAL only — written to `<[A]>/.ToolA_Skill/workflows/<slug>.md` (scope param ignored, project_path ignored).', {

            "name": {"description": "Workflow name (slug) the MD body is saved under in the global workflows directory.", "type": "string"},

            "body_md": {"description": "Raw Markdown workflow definition saved verbatim; [A] does not transform it.", "type": "string"},

            "scope": {"type": "string", "enum": ["global"], "default": "global", "description": "R37: forced to 'global'"},

            "project_path": {"type": "string", "default": "", "description": "R37: ignored"},

            "overwrite": {"description": "When true, replace an existing workflow file with the same name; default false refuses to overwrite.", "type": "boolean", "default": False},

        }, ["name", "body_md"]),

    ('workflow_save_md', 'V15 R36 / R37 Y1 T3: agent-friendly alias of workflow_create_from_ui. Always saves to GLOBAL workflows dir.', {

            "name": {"description": "Workflow name (slug) the MD body is saved under in the global workflows directory.", "type": "string"},

            "body_md": {"description": "Raw Markdown workflow definition saved verbatim.", "type": "string"},

            "scope": {"description": "Save scope; only 'global' (default) exists — workflows are global-only since R37, so this is accepted for compatibility.", "type": "string", "enum": ["global"], "default": "global"},

            "project_path": {"description": "Ignored for storage (workflows are global-only since R37); accepted so project-scoped callers do not fail validation.", "type": "string", "default": ""},

            "overwrite": {"description": "When true, replace an existing workflow file with the same name; default false refuses to overwrite.", "type": "boolean", "default": False},

        }, ["name", "body_md"]),

    ('workflow_read_md', 'V15 R36 T1: read raw MD body for a workflow (project-first, global fallback). Used by agent edit-then-save flow and UI editor `?load=<slug>`.', {

            "name": {"description": "Workflow name whose raw MD body is read (project copy first, global fallback).", "type": "string"},

            "project_path": {"description": "Project root checked first for a project-level copy of the workflow MD; empty reads the global copy only.", "type": "string", "default": ""},

        }, ["name"]),

    ('workflow_draft_create', 'V15 R36 T3: push agent-authored draft MD into in-memory store, return token. UI editor opens `/ui/workflow_editor?draft=<token>` to populate. Drafts auto-expire (default 1h, max 24h).', {

            "name": {"description": "Workflow name the draft MD is stored under in the in-memory draft store.", "type": "string"},

            "body_md": {"description": "Draft Markdown body pushed to the in-memory store for the UI editor to pull by token.", "type": "string"},

            "ttl_sec": {"type": "integer", "default": 3600, "description": "Time-to-live in seconds (60..86400)"},

        }, ["name", "body_md"]),

    ('workflow_draft_get', 'V15 R36 T1: UI editor pulls draft by token (called when URL has `?draft=<token>`).', {

            "token": {"description": "Draft token returned by workflow_draft_create; the UI editor pulls the draft with it.", "type": "string"},

        }, ["token"]),

    ('workflow_activate', 'V15 R36 / R37 Y1 T3: User activates workflow GLOBALLY. project_path optional (state is global). [A] starts injecting workflow hints.', {

            "name": {"description": "Workflow name to activate globally so [A] starts injecting its step hints.", "type": "string"},

            "project_path": {"description": "Project root used to resolve the workflow; optional because activation state is global (R37).", "type": "string", "default": ""},

        }, ["name"]),

    ('workflow_deactivate', 'V15 R36 / R37 Y1 T3: stop workflow mode (UI Stop or CA). Global state cleared, MD preserved.', {

            "project_path": {"description": "Project root used to resolve the active workflow; optional because workflow state is global — deactivation clears the global state and preserves the MD.", "type": "string", "default": ""},

        }, []),

    ('workflow_active_get', 'V15 R36 / R37 Y1 T1: read active workflow state (global). UI polls this every 5s; CA hint stream uses this.', {

            "project_path": {"description": "Project root used to resolve the active workflow; optional because the active state is global.", "type": "string", "default": ""},

        }, []),

    ('classify_get_footage_outcome', 'V15 R34 T1: post-action classifier. Reads log + folder_stats, classifies outcome ∈ {success, folder_picker_fail, source_unc_fail, copy_runtime_fail, unknown}. Returns evidence + ca_next_action.', {

            "project_path": {"description": "Project root whose log and target folder are read to classify the get-footage outcome; empty resolves from the active session.", "type": "string", "default": ""},

            "since_ts": {"type": "string", "default": "", "description": "ISO timestamp to slice log from"},

            "output_folder": {"type": "string", "default": "", "description": "Output folder for file-count delta check"},

            "log_excerpt": {"type": "string", "default": "", "description": "Skip log read by passing log text directly"},

        }, []),

    ('verify_active_electron_source', 'V15 R41 T1: verify which file Electron CDP target is actually loaded from. Reads document.URL + window.location.href via evaluate_js, decodes file:/// to a local path. If expected_path is passed, returns matches_expected. Use case: prevent editing Source/ui/index.html when the app loaded a dist/ or public/ mirror. Returns all_loaded_resources (top 10 script/link href) for inspection.', {

            "page_id": {"description": "CDP page id (from cdp_attach_target) whose loaded source file is verified.", "type": "string"},

            "expected_path": {"description": "Expected local file path the Electron target should have loaded; when given, the result reports matches_expected. Empty only reports the actual path.", "type": "string", "default": ""},

            "cdp_port": {"description": "CDP port of the Electron target being verified. Default 9223.", "type": "integer", "default": 9223},

        }, ["page_id"]),

    ('bootstrap_session', 'V15 R42 T1: bootstrap a session with a mandatory two-phase owner claim. New ownership requires caller-generated high-entropy owner_claim_id. Retry with the same claim recovers the same capability; a different claim cannot replace pending or committed ownership. Pending claims expire and begin_task commits. Existing committed legacy checkpoints remain readable; no new legacy one-shot claim is created.', {

            "project_path": {"type": "string", "default": "", "description": "Optional absolute project root for workflow/session context"},

            "session_id": {"type": "string", "description": "Explicit host session identity used to scope this bootstrap"},

            "agent_id": {"type": "string", "description": "Stable CA identity for bootstrap/rule correlation"},

            "owner_claim_id": {"type": "string", "description": "Required caller-generated high-entropy claim id (minimum 32 bytes). Persist locally until begin_task confirms ownership."},

            "verbosity": {"type": "string", "enum": ["terse", "compact", "ref", "full"], "default": "terse", "description": "Ownership-first compact response by default; full opts into discovery catalogs."},

            "include_discovery": {"type": "boolean", "default": False, "description": "Include tool, workflow and skill discovery after the ownership fields."},

        }, ["session_id", "agent_id", "owner_claim_id"]),

    ('run_project_test_script', 'V15 R43 P1 #5 T2: script-file transport for node|python|powershell|sh. Python selects the first interpreter satisfying required_python_modules; otherwise returns INTERPRETER_CAPABILITY_MISSING without installing packages. Results include runtime_used, script metadata, and stdout_metadata/stderr_metadata with full-stream hashes and truncation state. Execution failures use SCRIPT_EXEC_FAILED; unsafe inline callers should route here instead of retrying COMMAND_TRANSPORT_UNSAFE or SCRIPT_RUNTIME_REQUIRED. CASE-2008 destructive guard (all runtimes): a body containing a delete/move/truncate/overwrite statement refuses to run until confirm_destructive equals the scanned count, takes a project backup first, and returns untruncated output; dry_run=true previews without executing. A PowerShell -Include paired with -LiteralPath is REFUSED outright (DESTRUCTIVE_SCRIPT_FOOTGUN) because PowerShell silently discards the filter and removes the whole target.', {

            "project_path": {"description": "Project root the test script runs in; also used to resolve the interpreter and required modules.", "example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string"},

            "script_content": {"example": "import subprocess,sys; sys.exit(subprocess.call([sys.executable,'-m','pytest','tests/test_a44_matrix_coverage.py','-q']))", "type": "string", "description": "Source code of the script. Use content_encoding='base64' for multi-line / Unicode / backslash-heavy scripts."},

            "runtime": {"example": 'python', "type": "string", "enum": ["node", "python", "powershell", "sh"], "default": "node", "description": "A35: 'python' children get PYTHONIOENCODING=utf-8 + PYTHONUTF8=1 (setdefault, so an [A]-level env var still wins) and report it back as child_encoding -- the same guarantee the 'powershell' branch gets from its UTF-8 BOM + chcp 65001 prelude. Before that, a non-cp1252 character raised UnicodeEncodeError inside the script."},

            "timeout": {"example": 120, "type": "integer", "default": 120, "description": "Seconds for the CHILD process -- it does NOT bound the call. Admission, script staging and result assembly all sit outside it, so wall clock can exceed this number: measured 2026-09-22, a declared 90 answered after 231.7s. Size a client timeout off measured wall clock, never off this value. A35 gap#3: over MCP stdio the TRANSPORT dies at ~180-195s, so a timeout above ~170 loses the result even though the child keeps running -- call POST http://localhost:8083/api/call/run_project_test_script over HTTP for longer runs, or use a detached supervisor (0.1s launcher + Popen + timestamped status file to poll). A request above 170 echoes transport_ceiling_note in the result."},

            "script_label": {"example": 'a44 gate', "type": "string", "default": "", "description": "Human-readable label included in filename (alphanumeric+dash+underscore only)."},

            "return_full_stdout": {"example": False, "type": "boolean", "default": False, "description": "When True, return entire stdout (overrides stdout_max_chars)."},

            "stdout_max_chars": {"description": "Maximum stdout characters returned from the test script. Default 4000.", "example": 4000, "type": "integer", "default": 4000},

            "stderr_max_chars": {"description": "Maximum stderr characters returned from the test script. Default 2000.", "example": 2000, "type": "integer", "default": 2000},

            "content_encoding": {"description": "'plain' (default) or 'base64' (UTF-8) transport for the script content, for sources with quotes/backslashes/non-ASCII.", "example": 'plain', "type": "string", "enum": ["plain", "base64"], "default": "plain"},

            "python_exe": {"example": 'venv312\\Scripts\\python.exe', "type": "string", "default": "", "description": "Optional Python executable. Candidate order: explicit, ORCHESTRATOR_PYTHON_EXE, project venv312/.venv/venv, sys.executable, PATH."},

            "required_python_modules": {"example": ['pytest'], "type": "array", "items": {"type": "string"}, "default": [], "description": "Importable modules required from the selected Python interpreter. No packages are installed automatically."},

            "executed_test_files": {"example": ['tests/test_a44_matrix_coverage.py'], "type": "array", "items": {"type": "string"}, "description": "GAP-01/H2 (CBM_ENRICH_TEST=1): caller-DECLARED test files this script executed, project-relative paths. Honest source only -- the daemon never scrapes stdout. Declaring it emits executed_test_files in the result and unlocks the cached test_gap advisory keyed by script_hash."},

            "dry_run": {"example": False, "type": "boolean", "default": False, "description": "CASE-2008: static no-execute preview of the destructive-statement scan. Nothing is written and nothing runs."},

            "confirm_destructive": {"example": -1, "type": "integer", "default": -1, "description": "CASE-2008: acknowledged destructive-statement count. A body containing Remove-Item/rm/del/Clear-Content/Move-Item/fs.rm/rmtree etc. refuses to run until this EQUALS the scanner count (returned as confirm_destructive_required). A count rather than a boolean, so editing the script invalidates a stale acknowledgement."},

            "skip_backup": {"example": False, "type": "boolean", "default": False, "description": "CASE-2008: skip the automatic pre-run project backup for a destructive script. Only for genuinely disposable targets (e.g. inside .orchestrator_cache)."},

        }, ["project_path", "script_content"]),

    ('check_server_path', 'V15 R42 / R45 T1: probe path accessibility — UNC share, HTTP(s) HEAD, local file/folder. Mode auto-detects by prefix. Returns reachable + latency_ms + status_code (if HTTP) + is_file/is_dir (if filesystem) + size/permission. R45: Unicode-safe for UNC (Python Path.exists preserves Vietnamese diacritics) — prefer this over PowerShell Test-Path which loses non-ASCII via console code page.', {

            "path": {"description": "Path to probe: a UNC share, an HTTP(s) URL, or a local file/folder path.", "type": "string"},

            "mode": {"description": "Probe mode: 'auto' (default, detected from the path prefix), 'unc', 'http', 'https', or 'local'.", "type": "string", "enum": ["auto", "unc", "http", "https", "local"], "default": "auto"},

            "timeout_sec": {"description": "Probe timeout in seconds for network paths. Default 5.", "type": "integer", "default": 5},

            "verify_tls": {"type": "boolean", "default": True, "description": "Verify TLS certificate and hostname on HTTPS probes. Set false only on isolated corp networks with out-of-band trust — produces tls_verified:false + security_warning."},

        }, ["path"]),

    ('webfetch', "V15 R44 T1: HTTP GET fetch URL content (re-introduced for getfootage_sheet_v3 Google Sheets CSV pull). format='text' returns raw decoded content; format='json' parses JSON. Truncates at max_chars (default 200KB) and reports `truncated`. NOT a substitute for browser scraping — no JS execution.", {

            "url": {"type": "string", "description": "http:// or https:// URL"},

            "max_chars": {"type": "integer", "default": 200000, "description": "Truncate response body to this many chars (default 200KB)"},

            "format": {"description": "Response format: 'text' (default) returns the raw decoded body, 'json' parses the body as JSON.", "type": "string", "enum": ["text", "json"], "default": "text"},

            "timeout_seconds": {"description": "HTTP request timeout in seconds. Default 15.", "type": "integer", "default": 15},

            "verify_tls": {"type": "boolean", "default": True, "description": "Verify TLS certificate and hostname. Set false only on isolated corp networks — produces tls_verified:false + security_warning."},

        }, ["url"]),

    ('fetch_sheet_csv_summary', 'V15 R49/R53/R55 T1 v3 workflow primitive: fetch Google Sheets CSV → parse → classify into UNC/HTTP/local/header rows → return structured summary. Replaces v3 steps 7-10. R53: row-level header drift detection + header-noise tail trim + ASCII-safe path display. R55: configurable sample_row_count + dedicated rows_with_path_sample for content audit (not just counts).', {

            "url": {"type": "string", "description": "CSV export URL (e.g. .../gviz/tq?tqx=out:csv&gid=0)"},

            "max_rows": {"type": "integer", "default": 5000, "description": "Cap parsed rows for safety"},

            "timeout_seconds": {"description": "Timeout in seconds for fetching the Google Sheets CSV export. Default 15.", "type": "integer", "default": 15},

            "unc_pattern": {"type": "string", "default": "\\\\\\\\[^\\\\]+\\\\[^\\\\]+", "description": "Regex for UNC paths (default `\\\\server\\share` prefix)"},

            "sample_row_count": {"type": "integer", "default": 5, "description": "R55: how many rows to return in first_n / last_n / rows_with_path samples"},

            "sample_max_chars_per_row": {"type": "integer", "default": 400, "description": "R55: per-row truncation cap for sample raw fields"},

            "verify_tls": {"type": "boolean", "default": True, "description": "Passed through to webfetch. Verify TLS certificate and hostname on the CSV fetch."},

        }, ["url"]),

    ('sample_unc_paths_with_root_check', 'V15 R49 T1 v3 workflow primitive: sample N UNC paths, probe each via Unicode-safe Path.exists(), if any fail probe the root share \\\\<server>\\<share> to distinguish stale-path vs server-down. Replaces v3 steps 12-14. Verdict ∈ {all_reachable, partial_reachable_paths_stale, all_failed_but_root_alive_paths_stale, all_failed_server_down, ...}.', {

            "paths": {"type": "array", "description": "Candidate UNC paths (from fetch_sheet_csv_summary or other source)"},

            "sample_count": {"type": "integer", "default": 3, "description": "Number to test (v3 step 12: min 3 from ≥2 parents)"},

            "root_check": {"type": "boolean", "default": True, "description": "Probe \\\\server\\share root for any failed sample (v3 step 14)"},

            "timeout_sec": {"type": "integer", "default": 5, "description": "Per-probe timeout"},

            "unc_pattern": {"type": "string", "default": "\\\\\\\\[^\\\\]+\\\\[^\\\\]+", "description": "Regex matching valid UNC paths in input list"},

        }, ["paths"]),

    ('verify_get_footage_copy_completion', 'V15 R50 T1 v1/v2 causality verifier: walk evidence across folder filesystem + Rawcut tas-footage-err.log, cross-check folder_delta vs log_files_total, return single structured verdict. Replaces v1/v2 steps 22-29 (folder_stats after + log parse + cross-check + outcome classify). Verdict ∈ {copy_complete_clean, copy_complete_with_warnings, copy_in_progress_no_complete_marker, copy_started_but_no_files_yet, copy_did_not_start, mismatch_folder_vs_log, copy_failed_with_errors, log_path_not_found, indeterminate}.', {

            "target_folder": {"type": "string", "description": "Absolute path where Rawcut copied files (from workflow step 12)"},

            "baseline_file_count": {"type": "integer", "default": 0, "description": "File count from pre-copy folder_stats (workflow step 13)"},

            "baseline_total_bytes": {"type": "integer", "default": 0, "description": "Total bytes from pre-copy folder_stats"},

            "log_path": {"type": "string", "default": "", "description": "Absolute path to tas-footage-err.log (Rawcut writes per-file copy results here)"},

            "since_iso": {"type": "string", "default": "", "description": "Filter log entries to ts >= this ISO-8601 timestamp (e.g. baseline capture time)"},

            "expected_min_files": {"type": "integer", "default": 1, "description": "Minimum files to qualify as 'copy actually happened'"},

            "recursive_folder_scan": {"type": "boolean", "default": True, "description": "Deep recurse target_folder for file count"},

        }, ["target_folder"]),

    ('run_ps1_script', 'V15 R50 T2 narrow wrapper for PowerShell script-file execution. Hides runtime=powershell selection. Writes UTF-8 BOM .ps1 + OutputEncoding prelude + UTF-8 subprocess decode → Vietnamese diacritics survive end-to-end. Use INSTEAD of inline `powershell -Command` (blocked by R46 guard POWERSHELL_INLINE_FORBIDDEN). CASE-2008 destructive guard: Remove-Item (and aliases rm/del/ri/rd/rmdir/erase), Clear-Content, Move-Item, Copy-Item -Force and .NET deletes require confirm_destructive to match the scanned count, trigger a pre-run backup, and disable output truncation; -Include with -LiteralPath is refused (PowerShell ignores the filter and deletes everything). Runs as a child of the elevated [A] daemon (High IL; a CA shell is Medium), so admin steps (schtasks /change, HKLM, services, firewall) run here instead of being handed to Anh; elevation is capability, not authority, so the MUTATE/DESTRUCTIVE gates still bind.', {

            "project_path": {"description": "Project root the PowerShell script runs under; used for session resolution and the working directory.", "example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string"},

            "script_content": {"example": 'Get-Date', "type": "string", "description": "Full PS body (multiline OK, UTF-8 safe)"},

            "timeout": {"description": "Script wall-clock timeout in seconds. Default 120.", "example": 120, "type": "integer", "default": 120},

            "script_label": {"example": 'clock check', "type": "string", "default": "", "description": "Short audit identifier for cache filename"},

            "return_full_stdout": {"description": "When true, return the script's full stdout instead of the capped excerpt; default false caps at stdout_max_chars.", "example": False, "type": "boolean", "default": False},

            "stdout_max_chars": {"description": "Maximum stdout characters returned when return_full_stdout is false. Default 4000.", "example": 4000, "type": "integer", "default": 4000},

            "stderr_max_chars": {"description": "Maximum stderr characters returned. Default 2000.", "example": 2000, "type": "integer", "default": 2000},

            "content_encoding": {"description": "'plain' (default) or 'base64' (UTF-8) transport for script_content, for scripts with quotes/backslashes/Vietnamese text.", "example": 'plain', "type": "string", "enum": ["plain", "base64"], "default": "plain"},

            "dry_run": {"example": False, "type": "boolean", "default": False, "description": "CASE-2008: static no-execute preview of the destructive-statement scan. Nothing is written and nothing runs."},

            "confirm_destructive": {"example": -1, "type": "integer", "default": -1, "description": "CASE-2008: acknowledged destructive-statement count. A body containing Remove-Item/rm/del/Clear-Content/Move-Item/fs.rm/rmtree etc. refuses to run until this EQUALS the scanner count (returned as confirm_destructive_required). A count rather than a boolean, so editing the script invalidates a stale acknowledgement."},

            "skip_backup": {"example": False, "type": "boolean", "default": False, "description": "CASE-2008: skip the automatic pre-run project backup for a destructive script. Only for genuinely disposable targets (e.g. inside .orchestrator_cache)."},

        }, ["project_path", "script_content"]),

    ('run_node_script', 'V15 R50 T2 narrow wrapper for Node.js script-file execution. Hides runtime=node selection. Writes .js artifact + invokes `node <temp.js>` → JS source bytes (JSON.stringify, regex literals, UNC paths) pass through unchanged. Use INSTEAD of inline `node -e` (blocked by R49 guard NODE_INLINE_FORBIDDEN). CASE-2008 destructive guard: fs.rm/unlink/rmdir/truncate and rimraf require confirm_destructive to match the scanned count, trigger a pre-run backup, and disable output truncation.', {

            "project_path": {"description": "Project root the Node.js script runs under; used for session resolution and the working directory.", "type": "string"},

            "script_content": {"type": "string", "description": "Full JS body (multiline OK)"},

            "timeout": {"description": "Script wall-clock timeout in seconds. Default 120.", "type": "integer", "default": 120},

            "script_label": {"type": "string", "default": "", "description": "Short audit identifier for cache filename"},

            "return_full_stdout": {"description": "When true, return the script's full stdout instead of the capped excerpt; default false caps at stdout_max_chars.", "type": "boolean", "default": False},

            "stdout_max_chars": {"description": "Maximum stdout characters returned when return_full_stdout is false. Default 4000.", "type": "integer", "default": 4000},

            "stderr_max_chars": {"description": "Maximum stderr characters returned. Default 2000.", "type": "integer", "default": 2000},

            "content_encoding": {"description": "'plain' (default) or 'base64' (UTF-8) transport for the JS source, so JSON.stringify/regex literals/UNC paths pass through unchanged.", "type": "string", "enum": ["plain", "base64"], "default": "plain"},

            "dry_run": {"type": "boolean", "default": False, "description": "CASE-2008: static no-execute preview of the destructive-statement scan. Nothing is written and nothing runs."},

            "confirm_destructive": {"type": "integer", "default": -1, "description": "CASE-2008: acknowledged destructive-statement count. A body containing Remove-Item/rm/del/Clear-Content/Move-Item/fs.rm/rmtree etc. refuses to run until this EQUALS the scanner count (returned as confirm_destructive_required). A count rather than a boolean, so editing the script invalidates a stale acknowledgement."},

            "skip_backup": {"type": "boolean", "default": False, "description": "CASE-2008: skip the automatic pre-run project backup for a destructive script. Only for genuinely disposable targets (e.g. inside .orchestrator_cache)."},

        }, ["project_path", "script_content"]),

    ('workflow_checkpoint', 'V15 R51 T3 mid-flow drift gate: consolidate round accounting + session health + recent forbidden-transport policy hits + (optional) workflow state into ONE checkpoint snapshot. Call between mutating steps to ASK [A] whether execution path so far is clean. Returns drift_detected + warnings[] + ca_next_action. Warning kinds: HIGH_CALL_COUNT_NO_SUBMIT, ROUND_NOT_STARTED, SESSION_NOT_ACTIVE, RECENT_TRANSPORT_POLICY_DRIFT, WORKFLOW_PAUSED.', {

            "project_path": {"type": "string", "default": "", "description": "Project root (used to resolve active session)"},

            "label": {"type": "string", "default": "", "description": "Human-readable checkpoint name (e.g. 'after_step_10')"},

            "expected_state": {"type": "string", "default": "", "description": "Free-form audit note for what CA expects at this point"},

            "evidence": {"type": "string", "default": "", "description": "Optional 200-char evidence excerpt"},

            "workflow_name": {"type": "string", "default": "", "description": "Optional — include workflow_get_current data for this workflow"},

        }, []),

    ('workflow_goto_step', 'V15 R42 T3: jump current_step to arbitrary target (forward + backward, no cycle limit). Logs jump_history in state. Clears paused flag on jump. Use case: workflow V1 step 34 needs replay from step 10 for V2.', {

            "name": {"description": "Workflow name whose current_step is jumped to the target step.", "type": "string"},

            "target_step": {"type": "integer", "description": "Step number to jump to (must exist in workflow MD)"},

            "reason": {"type": "string", "default": "", "description": "Audit trail — why jumping"},

            "project_path": {"description": "Project root used to resolve the workflow; optional because workflow state is global (R37).", "type": "string", "default": ""},

        }, ["name", "target_step"]),

    ('wait_for_idle', "V15 R42 T1: built-in polling loop wrapping assert_target_idle. Returns when outcome='idle' for stable_seconds consecutive OR timeout. Saves CA from manual 60-iteration loops (~180 API calls → 1 call).", {

            "signals": {"type": "array", "description": "Same shape as assert_target_idle.signals"},

            "stable_seconds": {"type": "integer", "default": 20, "description": "Consecutive idle duration required"},

            "timeout_seconds": {"type": "integer", "default": 900, "description": "Total polling budget"},

            "check_interval_sec": {"type": "integer", "default": 30, "description": "Pause between probes"},

            "project_path": {"description": "Project root used to resolve the idle signals' context (folders/processes); empty resolves from the active session.", "type": "string", "default": ""},

        }, ["signals"]),

    ('parse_log_section', 'V15 R42 T1: extract_log_section + base64 decode + nested JSON parse. For each line tries to decode *_b64 fields and parse double-encoded JSON. Returns structured entries (last 50) + summary counts + top_categories.', {

            "page_id": {"description": "Browser page id whose log section is extracted, base64-decoded, and JSON-parsed.", "type": "string"},

            "section_title": {"description": "Title text of the log section to locate in the page DOM.", "type": "string"},

            "max_chars": {"description": "Maximum characters of section content read before parsing. Default 50000.", "type": "integer", "default": 50000},

            "tail": {"description": "When true (default), keep the LAST entries of the section (most recent log lines); false keeps the first.", "type": "boolean", "default": True},

            "decode_base64": {"description": "When true (default), decode *_b64 fields found in the section into structured values.", "type": "boolean", "default": True},

            "parse_nested_json": {"description": "When true (default), parse double-encoded JSON strings inside entries into objects.", "type": "boolean", "default": True},

        }, ["page_id", "section_title"]),

    ('assert_target_idle', "V15 R35 / R38 / R44 T1: universal completion-signal gate. Returns `outcome` ∈ {idle, copy_in_progress, process_busy, busy, unknown, no_signals}. ⚠️ CA MUST check `outcome` (NOT `ok`) — `ok` only confirms gate tool ran. Soft gate — CA decides; advance only when outcome='idle'. R38 added outcome classifier + ok_vs_idle_warning. R44 backward-compat: callers may pass path/folder + stable_seconds + timeout_seconds (legacy completion_signal flat form) and the gate synthesizes a folder_growth_stable signal.", {

            "signals": {"type": "array", "default": [], "description": "List of {kind, ...params} signal specs. Either signals OR path/folder must be provided."},

            "window_seconds": {"type": "integer", "default": 3, "description": "Stability observation window for *_stable signals"},

            "project_path": {"description": "Project root used to resolve the checked signals (folders/processes); empty resolves from the active session.", "type": "string", "default": ""},

            # R44 backward-compat aliases for legacy workflow markdown

            "path": {"type": "string", "default": "", "description": "R44 legacy: folder path. Synthesizes folder_growth_stable when signals not provided."},

            "folder": {"type": "string", "default": "", "description": "R44 legacy alias of path"},

            "stable_seconds": {"type": "integer", "default": 0, "description": "R44 legacy: stability window for synthesized signal. Promotes to window_seconds if window_seconds left at default."},

            "timeout_seconds": {"type": "integer", "default": 0, "description": "R44 legacy: total wait budget. Read-only gate; recorded as evidence only (no internal polling loop)."},

        }, []),

    ('click_and_handle_folder_dialog', 'V15 R31 T4: GENERIC atomic click → folder picker → paste path → verify. Baseline visible windows, click DOM selector via CDP, poll for NEW dialog (owner_pid/launched_after filter), fill via fill_folder_dialog. Use for any Electron/web app with native folder picker after a UI click. Stateful MCP only. Returns aggregated phases + picker_route.', {

            "page_id": {"description": "Browser page id where the selector is clicked to trigger the folder picker.", "type": "string"},

            "click_selector": {"type": "string", "description": "DOM selector to click (e.g. '#btnStep3')"},

            "folder_path": {"description": "Folder path filled into the picker dialog once it appears.", "type": "string"},

            "owner_pid": {"type": "integer", "default": 0, "description": "If known, dialog filter PID; else derived from owner_title"},

            "owner_title": {"type": "string", "default": "", "description": "App window title (e.g. 'Tr Rawcut Bot') — used to derive PID + parent_hwnd"},

            "wait_for_dialog_ms": {"type": "integer", "default": 5000, "description": "Poll budget after click for NEW dialog"},

            "timeout_ms": {"description": "Overall timeout in milliseconds for the dialog to appear and be filled. Default 8000.", "type": "integer", "default": 8000},

            "try_modern_picker": {"description": "When true (default), also handle the modern (IFileDialog) picker variant, not only the legacy dialog.", "type": "boolean", "default": True},

            "verify_target_exists": {"type": "boolean", "default": True, "description": "Pre-flight folder_exists check"},

        }, ["page_id", "click_selector", "folder_path"]),

    ('inspect_project_usage', 'V15 R27 T1: workflow-level project profile beyond inspect_project_runtime. Returns primary_ui_fields, primary_buttons (with event_handler_hint), runtime_functions, dangerous_controls, recommended_workflow_hint. Reuse after open_session.', {

            "project_path": {"description": "Project root whose UI surface (fields, buttons, runtime functions, dangerous controls) is profiled.", "type": "string"},

        }, ["project_path"]),

    ('invoke_app_function', "V15 R27 T4: structured invoke of a top-level page function. Wraps evaluate_js with arg serialization + optional poll on window[poll_until_global]. Use case: invoke_app_function(page_id, 'runGetFootageToTarget', ['D:/target'], poll_until_global='__codexRawcutCopyDone').", {

            "page_id": {"description": "Browser page id in which the top-level page function is invoked.", "type": "string"},

            "function_name": {"description": "Name of the top-level page function (window member) to invoke.", "type": "string"},

            "args": {"description": "JSON argument list serialized and passed to the page function, in order.", "type": "array", "default": []},

            "poll_until_global": {"description": "Name of a window global to poll after invocation; the call returns when it becomes truthy. Empty disables polling.", "type": "string", "default": ""},

            "poll_interval_ms": {"description": "Interval in milliseconds between poll_until_global checks. Default 500.", "type": "integer", "default": 500},

            "timeout_ms": {"description": "Maximum time in milliseconds to wait for the function/poll to complete. Default 30000.", "type": "integer", "default": 30000},

        }, ["page_id", "function_name"]),

    ('declare_mission', 'V15 R18 Phase B / R22 T1: curate skill + tool subset for the active session. R22 verbosity ∈ {full|terse|ref} — terse drops irrelevant_skills_skip + recommended_sequence + caveats (saves ~800 tokens). Keeps relevant_skills + relevant_tools (the curation output).', {

            "mission": {"type": "string", "description": "Free-form task description, e.g. 'test Electron Rawcut UI: launch, fill manual input, click Step 1, watch Step 3 state, extract log'. Required if context_hints not provided."},

            "context_hints": {"type": "array", "default": [], "description": "Keyword anchors to boost scoring: ['electron','ui','log','cdp']. Required if mission not provided."},

            "project_path": {"type": "string", "default": "", "description": "Active session project root — pins mission state to session"},

            "top_skills": {"type": "integer", "default": 5, "description": "Max number of relevant skills returned"},

            "top_tools": {"type": "integer", "default": 20, "description": "Max number of relevant tools returned"},

            "verbosity": {"type": "string", "enum": ["full", "terse", "ref"], "default": "full", "description": "R22: terse keeps curation output, drops irrelevant_skills_skip/recommended_sequence/caveats."},

        }, ["mission"]),

    ('save_log_snapshot', 'V15 R17 P1 T2: write log content to <project>/.orchestrator_cache/artifacts/<round_id>/<slug>.txt and return {path, sha256, length, lines, head_200, tail_200}. Final report cites artifact ref INSTEAD of pasting full log → ~40-50% S/L saving per round. Two input modes: (a) section_title (reuses extract_log_section logic) OR (b) selector (direct read).', {

            "page_id": {"description": "Browser page id whose log content is snapshotted to an artifact file.", "type": "string"},

            "section_title": {"type": "string", "default": "", "description": "Mode A — uses extract_log_section logic to locate body"},

            "selector": {"type": "string", "default": "", "description": "Mode B — direct CSS selector (e.g. '#systemLogArea')"},

            "project_path": {"type": "string", "description": "Required — project root for artifact path"},

            "round_id": {"type": "string", "default": "", "description": "Optional — defaults to timestamp; pair with the corresponding marker_event_id for traceability"},

            "area_class": {"type": "string", "default": "log-area", "description": "Mode A only"},

            "body_selector": {"type": "string", "default": "", "description": "Mode A only — override body locator"},

            "pick_strategy": {"type": "string", "enum": ["smart", "longest", "first"], "default": "smart", "description": "Mode A only"},

            "artifact_label": {"type": "string", "default": "", "description": "Override slug for file name; default = section_title or selector"},

        }, ["page_id", "project_path"]),

    ('desktop_window_list', 'V15 R8 B.5 T4d: enumerate visible top-level windows.', {

            "title_filter": {"description": "Case-insensitive title substring filter for the enumerated windows; empty lists all visible top-level windows.", "type": "string", "default": ""},
            "owner_pid": {"type": "integer", "default": 0, "description": "Limit the window census to this PID."},
            "include_child_controls": {"type": "boolean", "default": False, "description": "Include the child controls in each result."},
            "include_invisible": {"type": "boolean", "default": False, "description": "Include hidden windows in the census."},
        }, []),

    ('desktop_app_focus', 'V15 R57: CDP-first focus with flexible target matching, optional navigation wait, and controlled Win32 fallback. Returns matched_title plus initial_url/final_url for CDP. project_path is OPTIONAL — runtime resolves session from project_path → file_path → page_id chain; omit only when session is already pinned via prior page-bound call.', {

            "project_path": {"type": "string", "default": "", "description": "V15 R57: optional project root for session binding (mutating tool). Schema declares this so passing it never raises UNSUPPORTED_FIELD. When empty, runtime falls back to find_project_root(file_path) → page_id resolution; if all fail, raises PROJECT_PATH_REQUIRED (single source of truth)."},

            "hwnd": {"description": "Window handle to focus; 0 resolves the window by title_pattern instead.", "type": "integer", "default": 0},

            "title_pattern": {"description": "Title substring used to match the window to focus when hwnd is 0 (also a fallback match check when hwnd is given).", "type": "string", "default": ""},

            "match_mode": {"type": "string", "default": "contains_case_insensitive", "description": "exact|contains|regex|case_insensitive|contains_case_insensitive"},

            "fallback_to_win32": {"type": "boolean", "default": True, "description": "When false, return immediately if CDP target/focus fails"},

            "expected_url": {"type": "string", "default": "", "description": "Optional URL substring expected before selecting the CDP target"},

            "wait_for_navigation": {"type": "boolean", "default": False, "description": "Poll CDP /json until expected_url appears before dispatch"},

            "cdp_port": {"type": "integer", "default": 9223, "description": "Chromium DevTools Protocol port"},

        }, []),

    ('desktop_click', "V15 R59: CDP-first click with mouseMoved hover-arm, mouseDown/Up dispatch, and DOM-level synthetic .click() fallback. Returns dom_evidence_at_xy (tag/id/class/rect/disabled/textContent of element under coord) for diagnosing 'click=ok but app didn't react'. Win32 fallback retained. checks reports converted virtual-screen bounds and modal-disabled warnings for Win32; out-of-bounds refuses, modal warns and dispatches. CDP geometry/modal checks are explicitly skipped. clicked describes dispatch, not independently verified app effect. project_path optional; force_win32 skips CDP entirely.", {

            "project_path": {"type": "string", "default": "", "description": "V15 R57: optional project root for session binding (mutating tool). Schema declares this so passing it never raises UNSUPPORTED_FIELD. When empty, runtime falls back to find_project_root(file_path) → page_id resolution; if all fail, raises PROJECT_PATH_REQUIRED (single source of truth)."},

            "hwnd": {"type": "integer", "default": 0, "description": "Optional for CDP path; required for Win32 fallback unless title_pattern set"},

            "x": {"description": "X in the selected CDP page viewport, or in the target window client area for Win32. No automatic conversion between these two frames.", "type": "integer"},

            "y": {"description": "Y in the selected CDP page viewport, or in the target window client area for Win32. No automatic conversion between these two frames.", "type": "integer"},

            "cdp_port": {"description": "CDP port of the Electron target the click is routed through first. Default 9223.", "type": "integer", "default": 9223},

            "title_pattern": {"description": "Title filter for selecting the target window/page; empty is unpinned. Selection does not verify the application effect.", "type": "string", "default": ""},

            "match_mode": {"type": "string", "default": "contains_case_insensitive", "description": "exact|contains|regex|case_insensitive|contains_case_insensitive"},

            "fallback_to_win32": {"type": "boolean", "default": True, "description": "When false, return immediately if CDP target/click fails"},

            "expected_url": {"type": "string", "default": "", "description": "Optional URL substring expected before selecting the CDP target"},

            "wait_for_navigation": {"type": "boolean", "default": False, "description": "Poll CDP /json until expected_url appears before dispatch"},

            "force_win32": {"type": "boolean", "default": False, "description": "V15 R33 / R57: SKIP CDP entirely — use when targeting a NATIVE Win32 window (system modal, MessageBox, native folder dialog). Required for hwnd-targeted system dialogs. Previously implemented in function but missing from schema (R57 sync)."},

            "dispatch_dom_click": {"type": "boolean", "default": True, "description": "V15 R59: After CDP mouse dispatch, ALSO call .click() via Runtime.evaluate on elementFromPoint(x,y). Catches frameworks using event delegation, pointer events, or capture-phase listeners that miss synthesized mouse events. Default True because most app buttons are idempotent; set False if your handler is sensitive to double-fire."},

            "report_dom_evidence": {"type": "boolean", "default": True, "description": "V15 R59: Return dom_evidence_at_xy with tag/id/class/outerHTML/rect/disabled/textContent of the element actually under (x,y). Diagnoses 'clicked but nothing happened' — verify the click hit the intended element vs an overlay/wrong target."},

            "target_selector": {"type": "string", "default": "", "description": "V15 R64-A: when set, the DOM-level synthetic .click() targets document.querySelector(target_selector) INSTEAD of document.elementFromPoint(x,y). Solves the 'bbox center hits hint DIV overlay, not the actual BUTTON' drift seen live on Tr_Rawcut_Bot Sheet Scanner. Coord-based mouse events (mouseMoved/Pressed/Released) still fire for raw-event handlers — target_selector only redirects the dom_synthetic_click path. Pair with x/y computed from query_selector(target_selector).bbox to also fire mouse events at the correct geometric point. Leave empty to keep R59 elementFromPoint behavior."},

        }, ["x", "y"]),

    ('desktop_window_state', "V15 R58 T4d: Win32 ShowWindow primitive for window state changes (maximize/restore/minimize/normal/hide/show). Bypasses the unreliable 'click on chrome titlebar maximize button' pattern — clicking at fixed coords drifts with DPI scaling, theme, and Electron frame variations. Uses official Windows API ShowWindow(hwnd, SW_*). Returns rect_before, rect_after, was_visible, hwnd, prev_state and observation (immediate plus 100 ms samples over 1 second). Holds the existing driver lock during observation. Reports observed transitions with actor unknown; does not retry or guarantee persistence after the interval. For Electron BrowserWindow maximize, prefer this over desktop_click(maximize_button_coords).", {

            "project_path": {"type": "string", "default": "", "description": "Optional project root for session binding (mutating tool)."},

            "state": {"type": "string", "enum": ["maximize", "restore", "minimize", "normal", "hide", "show"], "description": "Target window state. Maps to SW_MAXIMIZE / SW_RESTORE / SW_MINIMIZE / SW_NORMAL / SW_HIDE / SW_SHOW."},

            "hwnd": {"type": "integer", "default": 0, "description": "Target window HWND. If 0, resolved via title_pattern."},

            "title_pattern": {"type": "string", "default": "", "description": "Title substring/regex to find window when hwnd=0."},

            "match_mode": {"type": "string", "default": "contains_case_insensitive", "description": "exact|contains|regex|case_insensitive|contains_case_insensitive"},

        }, ["state"]),

    ('desktop_screenshot', 'T4d: capture a desktop screenshot of all monitors, one monitor, or one window. Default captures ALL monitors (virtual desktop); monitor=N (1-based) captures one monitor; hwnd>0 captures a window. Result includes monitors[] (bounds+primary). Bridged capture checks console lock before capture and again before saving: T4_SCREEN_LOCKED/T4_SCREEN_STATE_UNKNOWN creates no PNG; unlocked uniform frames report uniform_frame and variance.', {

            "hwnd": {"description": "Window handle to capture; 0 (default) captures monitors instead of a single window.", "type": "integer", "default": 0},

            "save_path": {"description": "Explicit PNG output path; empty saves to the default screenshot location and returns its path.", "type": "string", "default": ""},

            "monitor": {"type": "integer", "default": 0, "description": "1-based monitor index (see monitors[] in result); 0 = all monitors / virtual desktop."},

            "all_monitors": {"type": "boolean", "default": True, "description": "Capture the full virtual desktop across all monitors when hwnd/monitor are not set."},

        }, []),

    ('desktop_send_text', 'T4d: focus and paste text, optionally submitting. Native Edit text_verification precedes Enter; password/DOM controls report not_verified. delivery_verified remains dispatch metadata. Preserves text-only clipboard unless the user changes it; unsupported prior formats or snapshot failures abort before replacement. Bridged calls refuse locked/unknown console state.', {

            "text": {"type": "string", "description": "Required text to paste/type into the target desktop window."},

            "hwnd": {"description": "Window handle to focus and paste text into; 0 resolves the window by title_pattern instead.", "type": "integer", "default": 0},

            "title_pattern": {"description": "Title substring used to find the target window when hwnd is 0.", "type": "string", "default": ""},

            "match_mode": {"type": "string", "default": "contains_case_insensitive", "description": "exact|contains|regex|case_insensitive|contains_case_insensitive"},

            "submit": {"type": "boolean", "default": True, "description": "Press Enter after readback and clipboard restoration. Native Edit mismatch or moved focus prevents submit; unsupported controls remain explicitly not_verified."},

            "method": {"type": "string", "default": "clipboard", "description": "Clipboard dispatch. Verify the actual value in the target app."},
            "paste_shortcut": {"type": "string", "enum": ["auto", "shift_insert", "ctrl_v"], "default": "auto", "description": "Auto uses Shift+Insert. Select ctrl_v for apps that require it."},
            "clear_first": {"type": "boolean", "default": False, "description": "Send Ctrl+A before paste. Explicit opt-in: applications may select records outside an input; verify the focused control."},

            "pre_focus_delay_ms": {"description": "Delay in milliseconds after focusing the window before pasting, so the target app settles. Default 250.", "type": "integer", "default": 250},

            "require_focus": {"description": "Check foreground before optional focus-click. Keyboard dispatch always rechecks the target foreground.", "type": "boolean", "default": True},

            "click_focus": {"description": "When true, click at click_x/click_y inside the window to set the text caret before pasting; default false pastes at the current caret.", "type": "boolean", "default": False},

            "click_x": {"description": "Screen X coordinate of the optional focus click; 0 uses the window-center heuristic.", "type": "integer", "default": 0},

            "click_y": {"description": "Screen Y coordinate of the optional focus click; 0 uses the window-bottom heuristic.", "type": "integer", "default": 0},

            "project_path": {"type": "string", "description": "Project root for the session gate. W7 2026-08-19 (F020): this is a mutating tool with no file_path/page_id/session_id, so the gate cannot derive a project and a payload built from `required` always answered 400 PROJECT_PATH_REQUIRED."},

        }, ["text", "project_path"]),

    ('electron_devtools_log', "⚠️ DEPRECATED R41: enumerate Electron CDP targets — do not use; use list_cdp_targets instead. Use `list_cdp_targets` for CDP target enumeration, `read_console_errors` for console messages, or `evaluate_js` for log inspection. This tool will be removed in R42+. Reason: confusing name (it's enumeration not log read), overlaps with list_cdp_targets. Migration: replace `electron_devtools_log(cdp_port=9223)` with `list_cdp_targets(cdp_port=9223)`.", {

            "cdp_port": {"description": "CDP port of the Electron target whose targets were enumerated by this deprecated tool. Default 9223.", "type": "integer", "default": 9223},

            "max_messages": {"description": "Maximum log/target entries returned. Default 50.", "type": "integer", "default": 50},

        }, []),

    ('list_cdp_targets', 'T4 read-only: query Chromium DevTools Protocol targets through [A] without leaving the orchestrator audit boundary.', {

            "cdp_port": {"type": "integer", "default": 9223, "description": "Chromium DevTools Protocol port"},

        }, []),

    ('process_tree_inspect', "T4d: psutil process tree from root_pid or name_pattern. root_pid takes precedence when both are supplied. name_pattern is a case-insensitive process-name or cmdline substring, not regex; pass 'electron' rather than 'electron|node'.", {

            "root_pid": {"type": "integer", "default": 0, "description": "Process ID to inspect. Required if name_pattern not provided."},

            "name_pattern": {"type": "string", "default": "", "description": "Case-insensitive substring matched against process name or full cmdline. Required if root_pid not provided; not regex."},

            "max_depth": {"description": "Maximum depth of the process tree walked from the root process. Default 3.", "type": "integer", "default": 3},

        }, []),

    ('run_playwright_test', 'V15 R8 B.6: Node Playwright runner; .js/.mjs/.cjs only; requires Playwright dependency. Python uses run_project_test_script.', {

            "test_script_path": {"type": "string", "description": "Absolute path to Node test script. Parent dir must have node_modules/playwright installed (or use playwright/chromium-only)."},

            "project_path": {"type": "string", "default": "", "description": "Optional project root (used for CWD resolution)"},

            "timeout_sec": {"description": "Wall-clock timeout in seconds for the Node Playwright test run. Default 300.", "type": "integer", "default": 300},

        }, ["test_script_path"]),

    ('record_ui_flow', 'T4 test: Playwright tracing stub. Use output_path for the intended trace path.', {

            "page_id": {"description": "Browser page id whose UI flow is traced.", "type": "string"}, "output_path": {"description": "Intended trace output path for the recorded flow; empty uses the default trace location.", "type": "string", "default": ""},

        }, ["page_id"]),

    ('assert_text_visible', 'T4 test: wait for text visible.', {

            "page_id": {"description": "Browser page id on which the text visibility is asserted.", "type": "string"}, "text": {"description": "Text that must become visible on the page.", "type": "string"},

            "timeout_ms": {"description": "Maximum wait in milliseconds for the text to appear. Default 2000.", "type": "integer", "default": 2000},

        }, ["page_id", "text"]),

    ('assert_button_click_changes_state', 'T4 test: before/after diff.', {

            "page_id": {"description": "Browser page id on which the button click state-change is asserted.", "type": "string"}, "button_selector": {"description": "CSS selector of the button clicked for the assertion.", "type": "string"},

            "observed_selector": {"description": "CSS selector of the element whose state is diffed before/after the button click.", "type": "string"},

            "timeout_ms": {"description": "Maximum wait in milliseconds for the observed state to change after the click. Default 3000.", "type": "integer", "default": 3000},

        }, ["page_id", "button_selector", "observed_selector"]),

    ('assert_console_no_error', 'T4 test: assert console.error count == 0. Routed on both transports (HTTP + MCP) after the T4-12 fix.', {

            "page_id": {"description": "Browser page id whose console.error count is asserted to be zero.", "type": "string"},

        }, ["page_id"]),

    ('get_token_meter', 'V15 R8 B.7 / R33: session byte estimate. Pass project_path (preferred) OR session_id; returns approx_bytes, approx_tokens, files_tracked, last_op_at. Use before/after large rounds to track token burn.', {

            "session_id": {"type": "string", "default": "", "description": "session UUID from open_session; optional if project_path given"},

            "project_path": {"type": "string", "default": "", "description": "absolute project root; resolves active session"},

        }, []),

    ('get_skill_state', 'V15 R8 B.7 / R22: skills available in .ToolA_Skill/. R22 verbosity supports full, terse, and ref. terse strips per-entry path metadata across 23 entries (path/path_absolute/size_bytes/read_file_range_payload* fields). Keeps only `name`. Saves about 2800 tokens per call.', {

            "session_id": {"description": "Session id used to resolve which project's .ToolA_Skill/ state is reported; empty resolves the active session.", "type": "string", "default": ""},

            "project_path": {"description": "Project root whose .ToolA_Skill/ skills are listed; empty resolves from the session.", "type": "string", "default": ""},

            "verbosity": {"type": "string", "enum": ["full", "terse", "ref"], "default": "full", "description": "R22: terse → name-only per entry; pass 'full' if you need read_file_range_payload."},

        }, []),

    ('list_projects', 'V15 R8 B.7: registered [Ab..Az] projects from sessions DB.', {

            "limit": {"type": "integer", "default": 26, "description": "Maximum number of project records to return"},

        }, []),

    ('get_validation_queue', 'V15 R8 B.7: pending dry_run_ids.', {

            "project_path": {"description": "Project root whose pending dry_run validation queue is listed.", "type": "string"},

        }, ["project_path"]),

    ('cep_manifest_validate', 'R81 T1: validate a CEP CSXS/manifest.xml — XML well-formed + ExtensionBundleId + each Extension MainPath/ScriptPath exist on disk + declared Hosts + duplicate Extension Ids. Pass project_path (extension root with CSXS/manifest.xml) OR manifest_path.', {

            "project_path": {"type": "string", "default": "", "description": "Extension repo root containing CSXS/manifest.xml"},

            "manifest_path": {"type": "string", "default": "", "description": "Explicit path to manifest.xml (overrides project_path)"},

        }, []),

    ('cep_extension_status', "R81 T1: CEP extension readiness — repo↔LIVE (%APPDATA%/Adobe/CEP/extensions/<live_name>) per-file hash diff (same/diff/missing_in_live), PlayerDebugMode registry flag, manifest validity, in_sync + ready_hint. Answers 'will Premiere see my latest panel?'.", {

            "project_path": {"type": "string", "description": "Extension repo root"},

            "live_name": {"type": "string", "default": "", "description": "LIVE folder name under %APPDATA%/Adobe/CEP/extensions/ (default = repo folder name, e.g. AutoCutter)"},

        }, ["project_path"]),

    ('copy_file', 'R81 T2 (protected): copy ONE file src→dst (absolute or relative via project_path) with audit — backs up existing dst, copies, verifies sha256. If dst exists and overwrite=false, return DST_EXISTS and do not mutate. Safe rerun is a new destination, or overwrite=true with backup_existing=true. project_path resolves relative src/dst. For repo→LIVE use cep_deploy.', {

            "src": {"example": 'docs\\a.md', "type": "string", "description": "Absolute path, or relative path resolved against project_path"},

            "dst": {"example": 'docs\\a_copy.md', "type": "string", "description": "Absolute path, or relative path resolved against project_path"},

            "project_path": {"example": 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator', "type": "string", "default": "", "description": "Project root; resolves relative src/dst when set"},

            "overwrite": {"description": "When true (default), an existing destination is backed up and replaced; false returns DST_EXISTS and does not mutate when the destination exists.", "example": True, "type": "boolean", "default": True},

            "backup_existing": {"example": True, "type": "boolean", "default": True, "description": "Back up dst to dst.bak_<ts> before overwriting"},

        }, ["src", "dst"]),

    ('cep_deploy', 'R81 T2 (protected): mirror a CEP extension repo→LIVE with manifest gate + per-file backup + hash verify + audit. files=[] deploys the curated set (manifest + html/js/jsx/css/json/png, excludes node_modules/.bak); pass relative paths to deploy a subset. Returns per-file {sha256,dst_existed,hash_verified,backup} + reload_hint.', {

            "project_path": {"type": "string", "description": "Extension repo root"},

            "live_name": {"type": "string", "default": "", "description": "LIVE folder name (default = repo folder name)"},

            "files": {"type": "array", "default": [], "description": "Relative paths to deploy; empty = curated default set"},

            "validate_first": {"type": "boolean", "default": True, "description": "Abort if manifest invalid"},

        }, ["project_path"]),

)





def build_tool_phases() -> dict[str, str]:

    phases = {

        **{name: "T1" for name in T1_TOOLS},

        **{name: "T2" for name in T2_TOOLS},

        **{name: "T3" for name in T3_TOOLS},

        "validate": "T2",

        "project_map": "T2",

        "state": "T1",

    }

    phases["council_tracker"] = "T1"
    phases["council_route_scorecard"] = "T1"

    phases["audit_tool_matrix"] = "T3"

    # 3B.1 SEARCH_TOOL consolidation: legacy names remain callable as aliases of

    # search_code and must stay phase-mapped for map<->schema<->phase lockstep.

    for _alias in ("find_definition_and_usage", "query_index", "query_dom_candidates"):

        phases.setdefault(_alias, "T1")

    # restored after an accidental drop during the 3B.1 map rewrite

    phases.setdefault("get_project_summary", "T1")

    return phases





# agent_id classification (fix plan section 2, as amended): the tools whose
# handlers consume body["agent_id"] as OBJECT/scope data -- the named agent
# or scope -- not as the caller's identity. For these tools the dispatcher
# preserves the field exactly as sent (value AND absence: no overwrite, no
# injection, no pop); every other declaring tool keeps identity
# canonicalization. manage_agent additionally enforces a handler-side
# read/write split (plan section 3); mcp_pack_generate is preserve-only --
# its agent_id is the documented alias of ca_id (the generator resolves
# cid = ca_id or agent_id), and ca_id itself is cross-agent by design.
AGENT_ID_OBJECT_TOOLS = {
    "manage_agent",
    "audit_reconciliation_report",
    "session_audit_summary",
    "ca_policy_metrics",
    "mcp_pack_generate",
}


def build_tool_schemas() -> list[dict[str, Any]]:

    return (

        deepcopy(BASE_TOOL_SCHEMAS)

        + deepcopy(EXTRA_TOOL_SCHEMAS)

        + deepcopy(EXTRA_TOOL_SCHEMAS_R43)

        + deepcopy(V15_R8_SCHEMAS)

    )





def build_tool_map(specs: tuple[tuple[str, str], ...], namespace: dict[str, Any]) -> dict[str, Any]:

    # V15 tools are registered after code_tools creates its live scope map. Merge
    # registry-owned metadata here so late V15 entries receive the same enforcement
    # metadata as legacy tools without duplicating the declaration in code_tools.
    scope_map = namespace.get("_MUTATION_SCOPE_MAP")
    if isinstance(scope_map, dict):
        scope_map.update(V15_MUTATION_SCOPES)

    return {name: eval(expr, namespace) for name, expr in specs}


# ── CSLt 2026-07-16: curated core tool surface (SOT, layer L4) ───────────────
# Single source of truth for the bounded/curated tool list a CA should see:
#   - HTTP GET /api/tools/names (no filter) returns exactly this set
#   - mcp_packs generator preset "curated_core" builds packs from it
# Rationale: D:/BR/_CSLt_plan/MCP_SURFACE_REPORT/MCP_TOOL_SURFACE_FINAL.md.
# Change HERE only; server/names endpoint and pack generator import this.
CURATED_CORE_TOOLS = (
    # Core read. A37: find_callers and find_definition_and_usage were REMOVED here.
    # They are hard-deprecated aliases, and this tuple is exactly what /api/tools/names
    # answers unfiltered and what the MCP packs are generated from -- so leaving them in
    # made the FIRST surface every CA sees advertise two tools CORE_RULE forbids.
    # search_code (next line) is their live replacement through mode=callers /
    # mode=definition_usage. Their slots went to one entry point each for the cbm graph
    # and the OCR family: the two surfaces a CA was measured never to discover.
    "read_file_range", "read_context_batch", "architecture_context_pack",
    "search_code", "find_symbol", "cbm_get_architecture",
    "ocr_map", "list_structure", "query_index", "get_rule_core_text",
    # Core write (8)
    "write_fix", "write_new_file", "write_fix_html", "write_transaction",
    "apply_after_dry_run", "backup_project", "delete_file", "copy_file",
    # Command (2)
    "run_project_command", "run_project_test_script",
    # Council (6)
    "council_message", "council_message_async", "council_worker_model",
    "convene_council", "council_tracker", "cbm_council_context",
    # Infrastructure (4)
    "tool_decision_hint", "cache_status", "restart_orchestrator_via_bat", "backup_status",
    # CA must-know (session / verify / native / ruff)
    "open_session", "validate_file", "ruff_fix", "log_native_event",
)
