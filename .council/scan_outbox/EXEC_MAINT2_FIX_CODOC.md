# EXEC_MAINT2_FIX_CODOC

## Files changed
- tools_v15.py:49,63-69,6096-6099,6542-6543,7220-7221
- surgical_mapper.py:5,362-364,463-469
- tests/test_conn_leak_audit.py:45
- orchestrator_server.py:31,55,2027,2054-2059
- .council/scan_outbox/EXEC_MAINT2_FIX_CODOC.md: created artifact

## Backups
- tools_v15.py.bak_maint2
- surgical_mapper.py.bak_maint2
- tests/test_conn_leak_audit.py.bak_maint2
- orchestrator_server.py.bak_maint2

## Verification
- py_compile: PASS (`./venv312/Scripts/python.exe -m py_compile tools_v15.py surgical_mapper.py tests/test_conn_leak_audit.py orchestrator_server.py`)
- pytest: PASS
- pytest result line verbatim: `497 passed, 7 warnings, 2 subtests passed in 83.66s (0:01:23)`
- [A] validate_file: PASS for all 4 source/test files.
- [A] validate_symbols: PASS for all 4 source/test files; stale index warning used live AST fallback, no symbols removed. `tools_v15.py` added `_safe_detach` as expected.
- [A] cache_delta: TOOL_ERROR on unrelated stale `.pytest_tmp_graphfix_full/.../artifact.txt` access denied; not a patch failure.

## Per-finding verify result
- #9 tools_v15.py socket close: PASS. Bare `except` replaced with `except OSError as exc` and debug log `socket close failed: %s`.
- #10 tools_v15.py focus detach: PASS. `finally` now calls shared `_safe_detach(current_tid, target_tid)`.
- #11 tools_v15.py click detach: PASS. Duplicate detach block now calls shared `_safe_detach(current_tid, target_tid)`.
- #12 surgical_mapper.py tree-sitter parse: PASS. Broad `except Exception: pass` replaced with `except (ValueError, RuntimeError) as exc` and debug log; regex fallback still follows.
- #13 surgical_mapper.py stat guard: PASS. `exists/is_file` guard now runs before `stat()` and `get_file_hash()`.
- #14 tests/test_conn_leak_audit.py retry default: PASS. Default changed from `60` to `5`; env override preserved.
- G3 orchestrator/core/errors.py reachability: PASS. Added `from orchestrator.core import errors as _legacy_errors` in `orchestrator_server.py` and documented legacy taxonomy in `_error_response`.
- G5 structured error log envelope: PASS. `_error_response` now logs JSON warning with `event`, `tool`, `error_name`, truncated `error`, and UTC timestamp before returning; return payload shape unchanged.