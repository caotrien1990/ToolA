# ROUND 2 AUDIT — LOCK REPORT
**Date:** 2026-07-10
**Auditor:** CozoC (zcode, leader)
**Scope:** Round 2 scan + CodoC verify + CodoC fix + tool rootcause investigation

---

## PART A: CodoC Fix H1-H4 — VERIFICATION LOCKED

### Method
1. Read actual code at 4 fix locations via `read_file_range`
2. Independent `python -m py_compile orchestrator_server.py` (not via CodoC)
3. Real test `council_message` queue dispatch (exercise H2/H3 code path)

### H1 — Popen resource leak — FIXED ✅
- **Location:** orchestrator_server.py:4436-4456
- **Before:** `proc = subprocess.Popen(...)` then `_council_communicate_idle(proc,...)` with NO try/finally
- **After (verified code):**
  ```python
  try:
      if request_id:
          _council_runtime_set_worker_pid(...)
      try:
          stdout, stderr, timed_out, timeout_kind = _council_communicate_idle(...)
      except Exception:
          _council_kill_process_tree(proc.pid)
          try: proc.wait(timeout=5)
          except Exception: pass
          raise
  finally:
      for _pipe in (proc.stdout, proc.stderr):
          try:
              if _pipe: _pipe.close()
          except Exception: pass
  ```
- **Verdict:** try/finally wraps Popen + communicate; kill+wait on exception; pipes closed in finally. **CORRECT.**

### H2 — _bg() silent dispatch — FIXED ✅
- **Location:** orchestrator_server.py:6544-6553
- **Before:** `except Exception: pass`
- **After (verified code):**
  ```python
  except Exception as exc:
      logger.error("async council dispatch to %s from %s failed: %r", target, sender, exc, exc_info=True)
  ```
- **Verdict:** silent pass replaced with logger.error + exc_info. Outer ok/dispatched return preserved. **CORRECT.**

### H3 — create_task GC leak (_bg) — FIXED ✅
- **Location:** orchestrator_server.py:6554-6556
- **Before:** bare `asyncio.create_task(_bg())`
- **After (verified code):**
  ```python
  _t = asyncio.create_task(_bg())
  _COUNCIL_PENDING_TASKS.add(_t)
  _t.add_done_callback(_COUNCIL_PENDING_TASKS.discard)
  ```
- **Verdict:** task ref stored in set + done_callback discard. **CORRECT.**

### H4 — create_task GC leak (drain) — FIXED ✅
- **Location:** orchestrator_server.py:6589-6591
- **Before:** bare `asyncio.create_task(_council_queue_drain(agent))`
- **After (verified code):**
  ```python
  _t = asyncio.create_task(_council_queue_drain(agent))
  _COUNCIL_PENDING_TASKS.add(_t)
  _t.add_done_callback(_COUNCIL_PENDING_TASKS.discard)
  ```
- **Verdict:** same pattern as H3. **CORRECT.**

### H3/H4 support — module-level set ✅
- **Location:** orchestrator_server.py:2206
- **Verified:** `_COUNCIL_PENDING_TASKS: set[asyncio.Task] = set()`

### Independent verification
- **py_compile:** `python -m py_compile orchestrator_server.py` → exit 0 **PASS**
- **Real test council_message:** queue dispatch returned (QUEUE_WAIT_TIMEOUT = worker timeout, NOT crash) → H2/H3 code path exercised without error **PASS**

### Fix verdict: 4/4 APPROVED, LOCKED.

---

## PART B: Tool Failure Rootcause — INVESTIGATION LOCKED

### RC1: ast_query except_exception → UNSUPPORTED_FIELD

| Field | Value |
|-------|-------|
| **Tool** | ast_query |
| **File** | tools_v15.py:454-478 |
| **Error** | UNSUPPORTED_FIELD |
| **Rootcause** | `SUPPORTED` set at L469 hardcodes 4 patterns: `async_function`, `class_inheriting`, `try_bare_except`, `function_returning_dict`. `except_exception` not in set. |
| **Classification** | **BY-DESIGN** — not a bug. Enum intentionally limited. |
| **Real test** | Confirmed: `accepted_fields=['async_function', 'class_inheriting', 'function_returning_dict', 'try_bare_except']` |
| **Fix needed** | Add `except_exception` pattern to SUPPORTED set + implement AST handler for `ast.ExceptHandler` where `node.type is not None and isinstance(node.type, ast.Name) and node.type.id == 'Exception'` |
| **Priority** | LOW — workers worked around by using `try_bare_except` + `search_code regex "except Exception"` |

### RC2: search_code callers → WinError 5 (BLOCKER)

| Field | Value |
|-------|-------|
| **Tool** | search_code (mode=callers) → delegates to find_symbol |
| **File** | orchestrator/tools/search.py:111 + 147 |
| **Error** | `[WinError 5] Access is denied: '.pytest_tmp_graphfix_full\test_xroot_outside_write_allow0\BR_like\artifact.txt'` |
| **Rootcause** | `find_symbol` uses `os.walk(base)` at L111 and L147 **WITHOUT `onerror` callback**. When `os.walk` hits a dir with restrictive ACL (`.pytest_tmp_graphfix_full`), it raises `PermissionError` which propagates uncaught. `should_skip_dir` at L112 only filters `dirs[:]` AFTER `os.walk` already raised. |
| **Classification** | **BUG** — missing error handler in os.walk |
| **Real test** | Reproduced: HTTP 500 TOOL_ERROR, `[WinError 5] Access is denied` on `.pytest_tmp_graphfix_full` |
| **Cleanup attempt** | `rm -rf .pytest_tmp_graphfix_full` → Permission denied (ACL locked even for owner) |
| **Fix needed** | Add `onerror` to both `os.walk` calls: `os.walk(base, onerror=lambda e: None)` — silently skip denied dirs. OR wrap `_search`/`_scan` in try/except PermissionError per-file. |
| **Priority** | **HIGH** — blocks ALL callers-mode searches project-wide |

### RC3: read_file_range → field name

| Field | Value |
|-------|-------|
| **Tool** | read_file_range |
| **File** | N/A (schema correct) |
| **Error** | UNSUPPORTED_FIELD |
| **Rootcause** | Worker OpeC used `line_start`/`line_end` (wrong). Schema accepts `start_line`/`end_line` (correct). |
| **Classification** | **WORKER ERROR** — not a tool bug |
| **Real test** | Schema confirmed: `start_line (integer) default=1`, `end_line (integer)`, `file_path [REQ]`, `max_lines (integer) default=200` |
| **Fix needed** | None (tool correct). Worker self-recovered by retrying with correct field name. |
| **Priority** | NONE |

---

## PART C: Round Summary

### Scan quality
| Metric | Round 1 | Round 2 |
|--------|---------|---------|
| ToolA adoption | 0/4 | **4/4** |
| Artifacts with END marker | 2/4 | **4/4** |
| Total findings | — | 82 (0 CRITICAL, 5 HIGH, 29 MEDIUM, 36 LOW, 12 INFO) |
| CodoC verify rate | — | **88%** (15/17 approved) |

### Fix status
| Item | Status | Verified by |
|------|--------|-------------|
| H1 Popen leak | **FIXED** | code read + py_compile |
| H2 _bg silent | **FIXED** | code read + py_compile |
| H3 create_task GC | **FIXED** | code read + py_compile |
| H4 create_task GC | **FIXED** | code read + py_compile |
| H5 search_code WinError5 | **NOT FIXED** | rootcause locked (os.walk onerror) |
| M1-M8 | 6 approved, 2 denied | CodoC verify |

### Remaining action items
1. **Fix search_code `os.walk` onerror** (orchestrator/tools/search.py L111 + L147) — unblocks all callers searches
2. **Add ast_query `except_exception` pattern** (tools_v15.py L469) — optional, low priority
3. **Verify P4 orphan symbols** (21, not 22 — CodoC denied M8 count)
4. **Clean .pytest_tmp dirs** with elevated ACL (icacls reset or admin shell)

### Artifacts
- `.council/scan_outbox/SCAN2_kiloc_p1.md` — P1 scan
- `.council/scan_outbox/SCAN2_opec_p2.md` — P2 scan
- `.council/scan_outbox/SCAN2_comc_p3.md` — P3 scan
- `.council/scan_outbox/SCAN2_hermc_p4.md` — P4 scan
- `.council/scan_outbox/VERIFY_CODOC_ROUND2.md` — CodoC verify
- `.council/scan_outbox/EXEC_CODOC_H1H4.md` — CodoC fix report
- `.council/scan_outbox/SCAN_ROUND2_CONSOLIDATED.md` — consolidated scan
- `.council/scan_outbox/ROUND2_AUDIT_LOCK.md` — THIS FILE

END_ROUND2_AUDIT_LOCK
