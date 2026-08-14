# SEARCH_CODE FIX — AUDIT LOCK
**Date:** 2026-07-10
**Auditor:** CozoC (zcode, leader)
**Scope:** CodoC fix os.walk onerror + 3 tool error re-test

---

## PART A: CodoC Fix — VERIFICATION LOCKED

### Rootcause (locked in ROUND2_AUDIT_LOCK.md)
`find_symbol` and `search_code` use `os.walk(base)` WITHOUT `onerror` callback. When `os.walk` hits a dir with restrictive ACL (`.pytest_tmp_graphfix_full`), it raises `PermissionError` (WinError 5) which propagates uncaught.

### Fix: add `onerror=lambda e: None` to 4 os.walk calls

| # | File | Line | Function | Verified |
|---|------|------|----------|----------|
| 1 | orchestrator/tools/search.py | 111 | find_symbol _search | ✅ `os.walk(base, onerror=lambda e: None)` |
| 2 | orchestrator/tools/search.py | 147 | find_symbol _scan (callers) | ✅ `os.walk(base, onerror=lambda e: None)` |
| 3 | orchestrator/tools/search.py | 309 | search_code _search | ✅ `os.walk(base, onerror=lambda e: None)` |
| 4 | tools_v15.py | 482 | ast_query _scan | ✅ `os.walk(base, onerror=lambda e: None)` |

### Code verification
- All 4 locations confirmed via `search_code mode=regex query="os\.walk"` — onerror kwarg present
- L417 in tools_v15.py (different function, outside scope) — intentionally NOT changed

### py_compile (independent)
- `python -m py_compile orchestrator/tools/search.py` → exit 0 **PASS**
- `python -m py_compile tools_v15.py` → exit 0 **PASS**
- `python -m py_compile orchestrator_server.py` → exit 0 **PASS** (regression check)

### [A] restart
- restart_orchestrator_via_bat with delay_seconds=2 → ok=True, restart_queued
- [A] came back up after ~60s
- Module reload picked up search.py change

### Fix verdict: 4/4 APPROVED, LOCKED.

---

## PART B: 3 Tool Error Re-Test — LOCKED

### T1: search_code callers — FIXED ✅

| Run | Before fix | After fix + restart |
|-----|-----------|---------------------|
| query=_error_response | HTTP 500 TOOL_ERROR `[WinError 5] Access denied .pytest_tmp_graphfix_full` | **ok, callers_count=26, elapsed=6.88s** |
| query=build_tool_schemas | (not tested before) | **ok, elapsed=2.00s** |

**Verdict:** WinError 5 BLOCKER **RESOLVED**. search_code callers mode now works project-wide.

### T2: ast_query except_exception — BY-DESIGN ✅

| Run | Result |
|-----|--------|
| pattern=except_exception | ok=False, error_name=UNSUPPORTED_FIELD |
| accepted_fields | `['async_function', 'class_inheriting', 'function_returning_dict', 'try_bare_except']` |

**Verdict:** BY-DESIGN — enum intentionally limited to 4 patterns. NOT a bug. Workers worked around using `try_bare_except` + `search_code regex "except Exception"`. Optional enhancement: add `except_exception` pattern to SUPPORTED set.

### T3: read_file_range field name — SCHEMA CORRECT ✅

| Run | Result |
|-----|--------|
| start_line/end_line (correct) | **ok=True, elapsed=3.88s** |
| line_start/line_end (wrong) | HTTP 400 UNSUPPORTED_FIELD |

**Verdict:** Schema is correct (`start_line`/`end_line`). Worker OpeC used wrong field name — WORKER ERROR, not tool bug. Worker self-recovered in Round 2 by retrying with correct field.

---

## PART C: Coverage Disclosure

### Thắc mắc: đã quét toàn bộ A hay 1 phần?

**Trả lời: chỉ quét 60.1% codebase.**

| Metric | Value |
|--------|-------|
| Total .py files | 168 |
| Total LOC | 62,974 |
| Scanned (P1-P4) | 39 files, 37,867 LOC (60.1%) |
| Unscanned | 129 files, 25,107 LOC (39.9%) |

### Partition breakdown
| P | Files | LOC | Coverage |
|---|-------|-----|----------|
| P1 code_tools | 7 | 12,359 | 19.6% |
| P2 orchestrator_server | 11 | 13,248 | 21.0% |
| P3 tools_v15+mission | 11 | 10,625 | 16.9% |
| P4 graph+core | 10 | 1,635 | 2.6% |

### Top unscanned files (by LOC)
1. `orchestrator/core/registry_data.py` — 1429 LOC (registry SOT)
2. `orchestrator/council/dashboard.py` — 1179 LOC
3. `tests/test_code_tools_cache.py` — 1094 LOC (counted P1 but partial)
4. `orchestrator/tools/write/core.py` — 1072 LOC
5. `orchestrator/graph/query.py` — 752 LOC
6. `orchestrator/runtime/desktop_dialog_controls.py` — 605 LOC
7. `orchestrator/runtime/desktop_dialogs.py` — 597 LOC
8. `orchestrator/mission/tools.py` — 568 LOC
9. `orchestrator/graph/extractors/javascript.py` — 551 LOC
10. `orchestrator/server/mcp_stdio.py` — 530 LOC
11. `research/CBM_CG/CBM/scripts/*` — ~960 LOC (research scripts)

**Note:** P4 LOC (1635) thấp bất thường vì graph/core files lớn (registry_data 1429, query 752, indexer 441) bị miss trong partition assignment do path matching. **Round 3 nên scan 39.9% còn lại.**

---

## PART D: Final Status

| Item | Status |
|------|--------|
| H1 Popen leak | **FIXED + VERIFIED** |
| H2 _bg silent | **FIXED + VERIFIED** |
| H3 create_task GC | **FIXED + VERIFIED** |
| H4 create_task GC | **FIXED + VERIFIED** |
| H5 search_code WinError5 | **FIXED + VERIFIED** (os.walk onerror, 4 locations, restart confirmed) |
| RC1 ast_query enum | BY-DESIGN (optional enhancement) |
| RC2 search_code onerror | **FIXED** |
| RC3 read_file_range | SCHEMA CORRECT (worker error) |
| Coverage | 60.1% — Round 3 needed for 39.9% remaining |

### Artifacts
- `.council/scan_outbox/EXEC_CODOC_H1H4.md` — H1-H4 fix report
- `.council/scan_outbox/EXEC_CODOC_SEARCHCODE_FIX.md` — search_code fix report
- `.council/scan_outbox/ROUND2_AUDIT_LOCK.md` — Round 2 audit
- `.council/scan_outbox/SEARCHCODE_FIX_AUDIT_LOCK.md` — THIS FILE

END_SEARCHCODE_FIX_AUDIT_LOCK
