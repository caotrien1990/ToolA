EXEC_CODOC_SEARCHCODE_FIX

Status: completed with native fallback.

Reason for fallback: [A] write_fix was blocked by STALE_INDEX, and both refresh_project_map and generate_project_map failed on .orchestrator_cache/orchestrator.lock with WinError 32. Scope was limited to the four requested os.walk call sites.

Changes:
- orchestrator/tools/search.py: find_symbol _search os.walk now uses onerror=lambda e: None.
- orchestrator/tools/search.py: find_symbol callers/usage _scan os.walk now uses onerror=lambda e: None.
- orchestrator/tools/search.py: search_code _search os.walk now uses onerror=lambda e: None.
- tools_v15.py: ast_query _scan os.walk now uses onerror=lambda e: None.

END_EXEC_CODOC_SEARCHCODE_FIX
