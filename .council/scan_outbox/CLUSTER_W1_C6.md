# CLUSTER SCAN — W1_C6 (hermes/kimi-k2.7-code)

## Tool Call Log (MANDATORY — all 11+ tools must appear)
| # | Tool | Params summary | Result count | Elapsed ms | Status |
|---|------|---------------|-------------|------------|--------|
| 1 | cbm_get_architecture | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 2 | 1103.6 | 200 |
| 2 | import_graph | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 1000 | 1498.8 | 200 |
| 3 | get_file_symbols:tools_v15.py | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 140 | 371.9 | 200 |
| 4 | get_file_symbols:orchestrator/tools/write/core.py | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 29 | 182.1 | 200 |
| 5 | get_file_symbols:orchestrator/tools/search.py | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 6 | 172.2 | 200 |
| 6 | get_file_symbols:orchestrator/tools/read/fs.py | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 7 | 207.6 | 200 |
| 7 | get_file_symbols:orchestrator/tools/read/symbols.py | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 2 | 149.1 | 200 |
| 8 | get_file_symbols:orchestrator/tools/write/copy_file.py | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 1 | 171.6 | 200 |
| 9 | get_file_symbols:orchestrator/tools/__init__.py | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 0 | 151.4 | 200 |
| 10 | get_file_symbols:orchestrator/tools/read/__init__.py | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 0 | 162.6 | 200 |
| 11 | get_file_symbols:orchestrator/tools/write/__init__.py | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 0 | 123.1 | 200 |
| 12 | cbm_dead_code | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 2 | 31996.8 | 200 |
| 13 | ast_query:try_bare_except | {"pattern": "try_bare_except", "project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE | 50 | 16249.2 | 200 |
| 14 | ast_query:class_inheriting | {"pattern": "class_inheriting", "project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIB | 50 | 2039.4 | 200 |
| 15 | ast_query:function_returning_dict | {"pattern": "function_returning_dict", "project_path": "D:/0. TRIEN/1. DATA/DATA | 50 | 857.9 | 200 |
| 16 | ast_query:async_function | {"pattern": "async_function", "project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE  | 50 | 489.3 | 200 |
| 17 | cbm_template_query:orphan_symbols | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 2 | 214.3 | 200 |
| 18 | read_file_range:overlap | {"project_path": "D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-o | 14 | 778.0 | 200 |

## Findings (ONLY from tool results — cite tool in EVERY row)
| # | Tool cited | file:line | severity | type | Evidence (tool output quote) |
|---|-----------|-----------|----------|------|------------------------------|
| 1 | ast_query:try_bare_except | _check_remaining.py:38 | MEDIUM | bare_except | {"file": "_check_remaining.py", "line": 38, "kind": "bare_except"} |
| 2 | ast_query:try_bare_except | _dispatch_cluster_scan.py:202 | MEDIUM | bare_except | {"file": "_dispatch_cluster_scan.py", "line": 202, "kind": "bare_except"} |
| 3 | ast_query:try_bare_except | _dispatch_fix_r3.py:99 | MEDIUM | bare_except | {"file": "_dispatch_fix_r3.py", "line": 99, "kind": "bare_except"} |
| 4 | ast_query:try_bare_except | _dispatch_fix_r3_codo.py:70 | MEDIUM | bare_except | {"file": "_dispatch_fix_r3_codo.py", "line": 70, "kind": "bare_except"} |
| 5 | ast_query:try_bare_except | _dispatch_scan3.py:112 | MEDIUM | bare_except | {"file": "_dispatch_scan3.py", "line": 112, "kind": "bare_except"} |
| 6 | ast_query:try_bare_except | _dispatch_verify_r3.py:70 | MEDIUM | bare_except | {"file": "_dispatch_verify_r3.py", "line": 70, "kind": "bare_except"} |
| 7 | ast_query:try_bare_except | _dispatch_verify_r3_ope.py:70 | MEDIUM | bare_except | {"file": "_dispatch_verify_r3_ope.py", "line": 70, "kind": "bare_except"} |
| 8 | ast_query:try_bare_except | _round3_partition.py:30 | MEDIUM | bare_except | {"file": "_round3_partition.py", "line": 30, "kind": "bare_except"} |
| 9 | ast_query:try_bare_except | .council/mcp_full_test_v3.py:24 | MEDIUM | bare_except | {"file": ".council/mcp_full_test_v3.py", "line": 24, "kind": "bare_except"} |
| 10 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/BPt4cc2IMMQorVYd/jsonschema/tests/test_validators.py:2270 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/BPt4cc2IMMQorVYd/jsonschema/tests/test_validators.py", "line": 2270, "kind": "bare_except"} |
| 11 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/EUxn1XuxrfsUgCsD/google/protobuf/descriptor_pool.py:622 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/EUxn1XuxrfsUgCsD/google/protobuf/descriptor_pool.py", "line": 622, "kind": "bare_except"} |
| 12 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/EUxn1XuxrfsUgCsD/google/protobuf/descriptor_pool.py:630 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/EUxn1XuxrfsUgCsD/google/protobuf/descriptor_pool.py", "line": 630, "kind": "bare_except"} |
| 13 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/EUxn1XuxrfsUgCsD/google/protobuf/descriptor_pool.py:586 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/EUxn1XuxrfsUgCsD/google/protobuf/descriptor_pool.py", "line": 586, "kind": "bare_except"} |
| 14 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/EUxn1XuxrfsUgCsD/google/protobuf/internal/python_message.py:514 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/EUxn1XuxrfsUgCsD/google/protobuf/internal/python_message.py", "line": 514, "kind": "bare_except"} |
| 15 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:43 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 43, "kind": "bare_except"} |
| 16 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:70 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 70, "kind": "bare_except"} |
| 17 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:154 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 154, "kind": "bare_except"} |
| 18 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:456 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 456, "kind": "bare_except"} |
| 19 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:493 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 493, "kind": "bare_except"} |
| 20 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:710 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 710, "kind": "bare_except"} |
| 21 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:727 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 727, "kind": "bare_except"} |
| 22 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:756 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 756, "kind": "bare_except"} |
| 23 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:1145 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 1145, "kind": "bare_except"} |
| 24 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:317 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 317, "kind": "bare_except"} |
| 25 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py:410 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/adodbapi.py", "line": 410, "kind": "bare_except"} |
| 26 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/ado_consts.py:50 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/ado_consts.py", "line": 50, "kind": "bare_except"} |
| 27 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py:28 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py", "line": 28, "kind": "bare_except"} |
| 28 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py:33 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py", "line": 33, "kind": "bare_except"} |
| 29 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py:172 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py", "line": 172, "kind": "bare_except"} |
| 30 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py:169 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py", "line": 169, "kind": "bare_except"} |
| 31 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py:175 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py", "line": 175, "kind": "bare_except"} |
| 32 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py:212 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/apibase.py", "line": 212, "kind": "bare_except"} |
| 33 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/is64bit.py:29 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/is64bit.py", "line": 29, "kind": "bare_except"} |
| 34 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/process_connect_string.py:74 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/process_connect_string.py", "line": 74, "kind": "bare_except"} |
| 35 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/examples/xls_write.py:25 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/examples/xls_write.py", "line": 25, "kind": "bare_except"} |
| 36 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:78 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 78, "kind": "bare_except"} |
| 37 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:97 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 97, "kind": "bare_except"} |
| 38 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:109 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 109, "kind": "bare_except"} |
| 39 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:126 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 126, "kind": "bare_except"} |
| 40 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1075 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1075, "kind": "bare_except"} |
| 41 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1079 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1079, "kind": "bare_except"} |
| 42 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1105 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1105, "kind": "bare_except"} |
| 43 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1137 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1137, "kind": "bare_except"} |
| 44 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1166 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1166, "kind": "bare_except"} |
| 45 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1191 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1191, "kind": "bare_except"} |
| 46 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1223 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1223, "kind": "bare_except"} |
| 47 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1227 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1227, "kind": "bare_except"} |
| 48 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1255 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1255, "kind": "bare_except"} |
| 49 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1259 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1259, "kind": "bare_except"} |
| 50 | ast_query:try_bare_except | .tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py:1321 | MEDIUM | bare_except | {"file": ".tmp/uv-cache-codoc/archive-v0/ozl22azqf9vcjahI/Lib/site-packages/adodbapi/test/adodbapitest.py", "line": 1321, "kind": "bare_except"} |

## Overlap Verification
| Overlap region | read_file_range called? | Lines read |
|----------------|------------------------|------------|
| tools_v15.py L3700-4000 | True | L3700-4000 |

## Coverage
| File | get_file_symbols | read_file_range | search_code |
|------|-----------------|-----------------|-------------|
| tools_v15.py | yes | yes | no |
| orchestrator/tools/write/core.py | yes | no | no |
| orchestrator/tools/search.py | yes | no | no |
| orchestrator/tools/read/fs.py | yes | no | no |
| orchestrator/tools/read/symbols.py | yes | no | no |
| orchestrator/tools/write/copy_file.py | yes | no | no |
| orchestrator/tools/__init__.py | yes | no | no |
| orchestrator/tools/read/__init__.py | yes | no | no |
| orchestrator/tools/write/__init__.py | yes | no | no |

END_CLUSTER_W1_C6