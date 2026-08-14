# EXEC OpeC Round 3 Fix Batch — Artifact

Worker: OpeC (ope/glm-5.2)
Round: 3 (EXECUTE fix batch)
Project root: D:/0. TRIEN/1. DATA/DATA/5. VIBE APP/1. APP/8. ToolA/project-orchestrator
Session: untitled-session-07-10-00-15

## Summary
All 15 fixes applied (17 edits + 1 file deletion) across 8 files.
Strict scope honored: orchestrator_server.py, tools_v15.py, search.py NOT touched.

## Fixes Applied

### CRITICAL
| ID | File | Change | Status |
|----|------|--------|--------|
| C1 | .council/tmp_qsleep/build/lease_orchestrator.py:38 | _read_processed_set filters status=="done" only | APPLIED |
| C2 | .council/tmp_qsleep/build/qsleep_core.py:144-151 | os.kill(pid,0) -> _pid_alive() (psutil.pid_exists + ctypes OpenProcess/GetExitCodeProcess fallback); except OSError only | APPLIED |
| C3 | .council/tmp_qsleep/build/qsleep_core.py:154-159 | write_lock: atomic open(lock_path,"x") + try/except FileExistsError; return bool | APPLIED |

### HIGH
| ID | File | Change | Status |
|----|------|--------|--------|
| H1 | bootstrap_rule_loader.py:11 | removed invalid `fields=` kwarg from requests.post | APPLIED |
| H2 | .council/mcp_phase3_fix.py:157-158 | fix=dict(fix) copy before pop (no shared FIXES mutation) | APPLIED |
| H3 | .council/tmp_qsleep/build/lease_orchestrator.py:117-124 | process_callback wrapped try/except/finally; release(status="done" if ok else "failed") | APPLIED |
| H4 | .council/tmp_qsleep/build/loop_orchestrator.py:331,347 | raise -> _update_meta(status='failed') + return (QDreamer1 & QDreamer2) | APPLIED |
| H5 | .council/tmp_qsleep/build/loop_orchestrator.py:67-74 | _run_hermes: check res.returncode != 0 -> raise RuntimeError | APPLIED |
| H6 | _dispatch_scan2.py | DELETED (incomplete/SyntaxError, superseded by _dispatch_scan2_v2.py) | APPLIED |
| H7 | .council/_verify_comc_argv.py:16-32 | bare assert -> explicit if/raise RuntimeError | APPLIED |

### MEDIUM
| ID | File | Change | Status |
|----|------|--------|--------|
| M1 | .council/tmp_qsleep/build/qsleep_core.py:151 | plain string -> f-string (PID interpolation) | APPLIED |
| M2 | .council/mcp_phase4_final.py:45 | bare except: -> except Exception: | APPLIED |
| M3 | .council/mcp_phase4_final.py:24-27 | removed dead get_token() function | APPLIED |
| M4 | .council/mcp_phase4_final.py:186-188 | writes to REPORT_OUT (mcp_full_surface_report_final_phase4.json) | APPLIED |
| M5 | _dispatch_scan2_v2.py:117 | bare except: -> except Exception as ie (includes inner error) | APPLIED |

## Verification

### py_compile (syntax) — PASS
`python -m py_compile` on all 8 fixed files -> PY_COMPILE_EXIT=0 (no syntax errors).

Files compiled:
- .council/tmp_qsleep/build/lease_orchestrator.py
- .council/tmp_qsleep/build/qsleep_core.py
- bootstrap_rule_loader.py
- .council/mcp_phase3_fix.py
- .council/tmp_qsleep/build/loop_orchestrator.py
- .council/_verify_comc_argv.py
- .council/mcp_phase4_final.py
- _dispatch_scan2_v2.py

### [A] validate_file (HTTP) — PARTIAL
- lease_orchestrator.py: ok=True (passed before server hang)
- Other 7 files: could not complete ([A] crashed, see below)

### [A] validate_symbols (HTTP) — NOT COMPLETED
validate_symbols triggered a heavy index drift check on the large project root; the
request hung (>30s) and [A] subsequently CRASHED (connection refused on /health).
Could not complete validate_symbols for any file. See [A] DOWN note.

### Manual self-verification — PASS
Re-read every changed region post-edit; all 14 changes confirmed semantically correct:
- C1: status=="done" filter present
- C2: _pid_alive helper (psutil + ctypes fallback), except OSError only
- C3: open(lock_path,"x") + FileExistsError, return bool
- H1: fields kwarg removed
- H2: fix=dict(fix) before pop
- H3: try/except/finally, release status based on ok flag
- H4: both raise -> status='failed' + return
- H5: returncode != 0 -> raise RuntimeError
- H6: file confirmed deleted (Test-Path=False)
- H7: all asserts -> if/raise RuntimeError
- M1: f-string with {pid}
- M2: except Exception:
- M3: get_token() removed
- M4: REPORT_OUT defined + used in write
- M5: except Exception as ie with inner error

## [A] DOWN — RESTART REQUIRED
During validate_symbols, [A] (http://localhost:8083) became unresponsive (timeout)
then crashed (connection refused on /health). Per SAFETY rule, OpeC did NOT restart
[A] unprompted (restart requires user-approved force / trigger gate).

Action needed: Restart [A] via start_orchestrator.bat before further [A]-dependent
verification. After restart, run validate_file + validate_symbols on the 8 fixed
files to complete the T2 verify chain, and cache_delta to update INDEX.

## Files NOT touched (scope respected)
- orchestrator_server.py (fixed Round 2)
- tools_v15.py (fixed Round 2)
- search.py (fixed Round 2)

END_EXEC_OPEC_R3_FIXES
