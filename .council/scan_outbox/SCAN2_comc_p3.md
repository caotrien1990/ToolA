# SCAN2_comc_p3 — Partition P3 Scan Report
**Worker**: ComC (comc/mimo-v2.5-pro)
**Round**: 2, Round 1
**Date**: 2026-07-10
**Partition**: P3 — tools_v15 + surgical_mapper + tools/mission + tests (~14K lines)

---

## Stability Report

| Tool | Status | Time | Count | Notes |
|------|--------|------|-------|-------|
| cbm_dead_code (tools_v15.py) | OK | ~0ms | 0 candidates | Vulture/Ruff found no dead code |
| cbm_dead_code (surgical_mapper.py) | OK | ~0ms | 0 candidates | Clean |
| cbm_dead_code (orchestrator/mission) | OK | ~0ms | 0 candidates | Clean |
| cbm_dead_code (orchestrator/tools) | OK | ~0ms | 0 candidates | Clean |
| ast_query (try_bare_except) | OK | ~0ms | 0 results | No bare except in tools_v15.py |
| ast_query (class_inheriting) | OK | ~0ms | 30 results | 2 project-internal classes found |
| find_callers (_safe_detach) | OK | ~50ms | 5 results | Defined L63, called L6543, L7221 |
| search_code (callers mode) | FAIL | N/A | N/A | WinError 5 on .pytest_tmp — tool scans inaccessible files |

---

## Findings Table

| # | Severity | Category | File | Line(s) | Description |
|---|----------|----------|------|---------|-------------|
| F1 | LOW | Error Handling | tools_v15.py | 7766 | `except Exception: return []` — silently swallows all DB errors in list_projects. Should log warning. |
| F2 | LOW | Error Handling | tools_v15.py | 6726 | `except Exception: pass` in finally block for _safe_detach — acceptable for cleanup but no debug log. |
| F3 | LOW | Error Handling | surgical_mapper.py | 196, 547, 571 | Multiple `except Exception: pass` for best-effort operations (file type detection, cache removal). Acceptable pattern. |
| F4 | LOW | Error Handling | orchestrator/tools/write/core.py | 389-448 | Multiple `except Exception: pass` in _collect_known_roots — silently skips all root sources. |
| F5 | INFO | Thread Safety | tools_v15.py | 72 | `_SESSION_MISSION` module-level dict has no lock. Only one write site (L3910). Python GIL protects single dict ops, low risk. |
| F6 | INFO | Thread Safety | orchestrator/mission/store.py | 108-120 | SQLite WAL + CAS transitions. Proper atomic pattern. No issues found. |
| F7 | INFO | Thread Safety | orchestrator/mission/instance_lock.py | 55-148 | OS-level msvcrt file locking with restart-race handling. Well-implemented. |
| F8 | LOW | Tool Bug | search_code | N/A | Tool crashes with WinError 5 when scanning .pytest_tmp_graphfix_full dirs with restricted permissions. Should skip inaccessible files gracefully. |
| F9 | INFO | Dead Code | tools_v15.py | N/A | No dead code detected by vulture/ruff. |
| F10 | INFO | Bare Except | tools_v15.py | N/A | Zero bare `except:` clauses found. All use specific exception types or `except Exception`. |

---

## Architecture Notes

- **tools_v15.py** (~7,799 lines): Monolithic tool module with T1-T4 tool implementations. No threading — all async via asyncio.to_thread() for Win32 API calls. Import topology enforced (no top-level imports of orchestrator_server/code_tools).

- **surgical_mapper.py** (~916 lines): File-type detection and symbol mapping. Pure functions, no I/O side effects beyond file reads.

- **orchestrator/mission/**: Well-structured mission queue system:
  - store.py: SQLite WAL + atomic CAS for state transitions
  - engine.py: Pure logic (no I/O), attempt-token tracking, artifact verification
  - tools.py: HTTP integration layer over store/engine
  - reaper.py: Background task for stale mission recovery (proper logging)
  - instance_lock.py: OS-level single-instance locking with restart-race handling

- **orchestrator/tools/write/**: core.py handles file writes with xroot policy enforcement, structural balance checks, and session validation.

- **orchestrator/tools/read/**: fs.py and symbols.py provide read-only file operations with proper error handling.

---

## Recommendations

1. **F1 (MEDIUM→actual)**: Add logging to `except Exception: return []` at tools_v15.py:7766. Silent DB failures make debugging difficult.
2. **F8 (BUG)**: search_code tool should catch PermissionError on individual files and continue scanning rather than aborting the entire search.
3. **F4**: Consider logging root collection failures in _collect_known_roots to aid debugging when expected roots are missing.

---

END_SCAN2_COMC_R1_P3
