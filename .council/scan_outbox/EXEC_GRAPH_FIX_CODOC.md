# EXEC_GRAPH_FIX_CODOC

## Files changed
- `orchestrator/graph/tools.py`
  - Lines 8-10: added `glob` import.
  - Lines 149-163: `cbm_dead_code` now accepts and forwards `scope` and `file_filter`.
  - Lines 189-211: dispatch restricts `scope="core"` to `orchestrator/`, expands optional glob file filters, short-circuits empty filters, and passes file lists to Vulture/Ruff.
- `orchestrator/graph/extractors/vulture_ruff.py`
  - Lines 133-149: `scan_vulture(..., files=None)` passes file targets positionally and uses `timeout=30`.
  - Lines 161-181: `scan_ruff(..., files=None)` passes file targets positionally and uses `timeout=30`.
- `orchestrator/graph/store.py`
  - Lines 18, 25, 30: SQLite connect timeout and `busy_timeout` raised from 5s to 15s.
- `orchestrator/graph/indexer.py`
  - Lines 12, 366-374: imports `sqlite3` and retries `BEGIN IMMEDIATE` lock errors 3 times with 1s sleeps.
- `orchestrator/core/registry_data.py`
  - Lines 696-700: deprecated alias description now points users to `search_code` with `query=<symbol>`; schema canonical field is `query`.
- `orchestrator_server.py`
  - Lines 747-749: `find_definition_and_usage` legacy `symbol` payload is normalized to `query`.
- `code_tools.py`
  - Lines 11609-11610: `cbm_dead_code` schema now includes `scope` and `file_filter`.

## Backups
- `orchestrator/graph/tools.py.bak_graphfix`
- `orchestrator/graph/extractors/vulture_ruff.py.bak_graphfix`
- `orchestrator/graph/store.py.bak_graphfix`
- `orchestrator/graph/indexer.py.bak_graphfix`
- `orchestrator/core/registry_data.py.bak_graphfix`
- `orchestrator_server.py.bak_graphfix`
- `code_tools.py.bak_graphfix`

## Verification
- `py_compile`: pass for every touched source file.
- Initial exact `.\venv312\Scripts\python.exe -m pytest -q`: blocked by environment temp permissions at `C:\Users\ADMIN\AppData\Local\Temp\pytest-of-ADMIN`.
- Rerun with workspace basetemp: `.\venv312\Scripts\python.exe -m pytest -q --basetemp .\.pytest_tmp_graphfix_full`
- Pytest result line verbatim: `497 passed, 1 warning, 2 subtests passed in 73.61s (0:01:13)`

## Per-bug verify
- BUG 1 `cbm_dead_code`: direct call `scope="core" file_filter="orchestrator_server.py" limit=10` returned `ok=True`, `count=0`, `engine=vulture+ruff`, elapsed `0.000s`.
- BUG 2 `cbm_index` lock: concurrent direct `index_project(scope="core")` plus `graph_status` returned `index_ok=True`, `status_ok=True`, `lock_error=False`.
- BUG 3 `find_definition_and_usage`: code-new normalization maps `symbol="main"` to `query="main"` and reports `status=200 OK` in-process; live HTTP server was not restarted during the task, so the running process still had the old alias table.

END_CODOC_GRAPH_FIX
