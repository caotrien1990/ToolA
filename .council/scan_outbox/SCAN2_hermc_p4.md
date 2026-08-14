# SCAN2_hermc_p4 — Partition P4 Scan Report

Worker: HermC (hermes/kimi-k2.7-code)
Round: 2
Partition: P4 (graph + core + council + fileops + tests)

## Stability

| tool | ok | count | note |
|------|----|-------|------|
| cbm_dead_code | YES | 50 | ~ |
| cbm_template_query orphan_symbols | YES | 50 | ~ |
| cbm_template_query entry_points | YES | 3 | ~ |
| ast_query try_bare_except | YES | 50 | ~ |
| search_code callers dead_code_candidates | NO | 0 | TOOL_ERROR |

## Findings

| category | severity | file | line | symbol | kind | source | rule | reason |
|----------|----------|------|------|--------|------|--------|------|--------|
| orphan symbol | medium | orchestrator/fileops/common.py | 129 | common.atomic_write_text | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/fileops/common.py | 150 | common.decode_text_payload | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/fileops/common.py | 49 | common.detect_text_encoding | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/fileops/common.py | 237 | common.ensure_fresh_index | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/fileops/common.py | 177 | common.find_project_root | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/fileops/common.py | 61 | common.is_binary_file | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/fileops/common.py | 80 | common.resolve_project_file | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/fileops/common.py | 133 | common.sha256_file | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/graph/config.py | 80 | config.graph_db_path | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/graph/config.py | 19 | config.use_graph_for | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/council/dashboard.py | 20 | dashboard.render_council_dashboard | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/core/errors.py | 32 | errors.OrchestratorError.to_payload | method | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/graph/indexer.py | 252 | indexer.compute_file_hash | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/graph/indexer.py | 347 | indexer.incremental_index | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/core/interfaces.py | 14 | interfaces.DesktopDialogRuntime.cancel_native_dialog | method | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/core/interfaces.py | 15 | interfaces.DesktopDialogRuntime.click_and_handle_folder_dialog | method | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/core/interfaces.py | 12 | interfaces.DesktopDialogRuntime.fill_folder_dialog | method | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/core/interfaces.py | 13 | interfaces.DesktopDialogRuntime.folder_dialog_snapshot | method | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/core/interfaces.py | 76 | interfaces.bidirectional_import_pairs | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/core/interfaces.py | 52 | interfaces.build_static_import_graph | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/core/interfaces.py | 18 | interfaces.protocol_method_signatures | function | cbm | orphan_symbols | symbol has no known callers |
| orphan symbol | medium | orchestrator/graph/extractors/javascript.py | 473 | javascript.extract | function | cbm | orphan_symbols | symbol has no known callers |
| entry point | info | orchestrator/core/errors.py | 27 | errors.OrchestratorError.__init__ | method | cbm | entry_points | public API / lifecycle entry point |
| entry point | info | orchestrator/graph/extractors/javascript.py | 89 | javascript._JsWalker.__init__ | method | cbm | entry_points | public API / lifecycle entry point |
| entry point | info | orchestrator/graph/extractors/python.py | 81 | python._Extractor.__init__ | method | cbm | entry_points | public API / lifecycle entry point |
| tool failure | high |  |  | search_code(mode=callers, query=dead_code_candidates) | TOOL_ERROR | orchestrator |  | TOOL_ERROR: [WinError 5] Access is denied: 'D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator\\.pytest_tmp_graphfix_full\\test_xroot_outside_write_allow0\\BR_like\\artifact.txt' |

## Notes

- `cbm_dead_code` returned many candidates outside P4 (`.council`, `.tmp`, `.pytest_tmp_*`). Only P4-scoped results are listed above.
- `ast_query try_bare_except` returned mostly third-party cache files under `.tmp/uv-cache-codoc` plus one broker file `_dispatch_scan2_v2.py`; only P4 files are included.
- `search_code` call for `dead_code_candidates` failed with `[WinError 5] Access is denied` on a `.pytest_tmp_graphfix_full` artifact, likely due to leftover temp-file permissions. This is a P4-adjacent concern (test cleanup / xroot temp file handling).
- Orphan symbols in `orchestrator/fileops/common.py` and `orchestrator/council/dashboard.py` suggest utility functions not yet referenced by active tools, but may still be public utilities.

END_SCAN2_HERMC_R1_P4