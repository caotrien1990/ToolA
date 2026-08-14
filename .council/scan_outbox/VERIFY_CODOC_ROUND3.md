## Finding Verdicts
| ID | file:line | Round3 claim | verdict (APPROVE/DENY) | evidence (code quote) |
|---|---|---|---|---|
| C1 | `.council/tmp_qsleep/build/lease_orchestrator.py:37-39,150` | `_read_processed_set` adds ANY `cluster_id` to seen regardless of status; `pending_rollback` is falsely DONE. | DENY | `37: cid = entry.get("cluster_id")` / `38: if cid and entry.get("status") == "done":` / `39: seen.add(str(cid))`. Rollback writes `155: _append_processed(ledger_dir, cid, "pending_rollback")`, but line 38 filters it out. |
| C2 | `.council/tmp_qsleep/build/qsleep_core.py:144-151` | Uses `os.kill(pid,0)` on Windows and treats live PIDs as dead via broad except. | DENY | `_pid_alive` uses psutil/WinAPI, not `os.kill`: `84: def _pid_alive(pid: int) -> bool:`, `86: if psutil is not None:`, `87: return psutil.pid_exists(pid)`, `90: handle = ctypes.windll.kernel32.OpenProcess(`. Lock check only catches `OSError`: `169: try:` / `170: alive = _pid_alive(pid)` / `171: except OSError:`. |
| C3 | `.council/tmp_qsleep/build/qsleep_core.py:154-159` | `check_lock` then `write_lock` is non-atomic; no `O_EXCL`. | DENY | `write_lock` is atomic exclusive create: `181: """Write current PID into qsleep.lock (atomic exclusive create)."""`, `185: with open(lock_path, "x", encoding="utf-8") as f:`, `188: except FileExistsError:`. |
| H1 | `bootstrap_rule_loader.py:11` | `requests.post(..., fields=[...])` uses invalid requests param. | DENY | `8: response = requests.post(`, `9: "http://localhost:8083/api/call/get_rule_core_text",`, `10: json={"agent_id": "hermes"},`, `11: )`. No `fields` kwarg exists. |
| H2 | `.council/mcp_phase3_fix.py:105-108/157-159` | `fix.pop("method")` mutates dict during iteration. | DENY | `157: fix = dict(fix)` copies before mutation; `158: method = fix.pop("method", "POST")`; `159: payload = fix.pop("payload", None) if "payload" in fix else fix.copy()`. |
| H3 | `.council/tmp_qsleep/build/lease_orchestrator.py:117-124` | `process_callback` is not in `try/finally`. | DENY | `117: ok = True`, `118: try:`, `119: if process_callback is not None:`, `120: process_callback(cluster_id, run_id)`, `124: finally:`, `126: mgr.release(cluster_id, status="done" if ok else "failed")`. |
| H4 | `.council/tmp_qsleep/build/loop_orchestrator.py:331,347` | Step exceptions are re-raised. | DENY | Exception handlers return meta, no raise: `326: except Exception as exc:`, `335: _update_meta(run_dir, status='failed')`, `336: return _load_meta(run_dir)` and `343: except Exception as exc:`, `352: _update_meta(run_dir, status='failed')`, `353: return _load_meta(run_dir)`. |
| H5 | `.council/tmp_qsleep/build/loop_orchestrator.py:67-74` | `_run_hermes` returncode is never checked. | DENY | `75: if res.returncode != 0:`, `76: raise RuntimeError(`, `77: f'hermes_failed: returncode={res.returncode} stderr={res.stderr[:200]!r}')`. |
| H6 | `_dispatch_scan2.py:80-93` | File incomplete with unterminated tuple SyntaxError. | DENY | `read_file_range` result: `File not found: _dispatch_scan2.py`; `validate_file` result: `File not found: ...\project-orchestrator\_dispatch_scan2.py syntax validation failed`. There is no current file to prove an unterminated tuple. |
| H7 | `.council/_verify_comc_argv.py:16-32` | Bare `assert`, stripped under `python -O`. | DENY | The code uses explicit exceptions: `17: if argv[i + 1] != PROMPT:`, `18: raise RuntimeError(...)`, `20: if not (...)`, `21: raise RuntimeError(...)`, `27: if flag not in argv:`, `28: raise RuntimeError(...)`. No `assert` in cited range. |
| M1 | `.council/tmp_qsleep/build/qsleep_core.py:151` | Return string is not f-string; pid not interpolated. | DENY | Current PID messages are f-strings: `174: return (False, f"Another QSleep instance running (PID {pid})")`, `177: return (True, f"Removed stale lock (PID {pid} dead)")`. |
| M2 | `.council/mcp_phase4_final.py:45` | Bare except catches `KeyboardInterrupt`/`SystemExit`. | DENY | `35: try:`, `36: tr = urllib.request.Request(...)`, `41: except Exception:`, `42: print("No token endpoint or failed")`. This is broad, but note `except Exception` does not catch `KeyboardInterrupt`/`SystemExit`; the bare-except wording is inaccurate. |
| M3 | `.council/mcp_phase4_final.py:24-27` | `get_token()` defined but never called. | DENY | No `get_token` function exists in file. Token retrieval is inline and executed: `33: # Try to get token`, `34: token = None`, `36: tr = urllib.request.Request(f"{BASE}/api/token", ...)`, `39: token = token_data.get("token")`. |
| M4 | `.council/mcp_phase4_final.py:186-188` | Writes report back to INPUT file. | DENY | Output path differs: `9: REPORT_IN = ... "mcp_full_surface_report_final.json"`, `10: REPORT_OUT = REPORT_IN.replace(".json", "_phase4.json")`, `182: with open(REPORT_OUT, "w", encoding="utf-8") as f:`. |
| M5 | `_dispatch_scan2_v2.py:117` | Bare except, `str(e)` refers to OUTER `HTTPError`. | APPROVE | `115: except urllib.error.HTTPError as e:`, `116: try: results[agent] = json.loads(e.read())`, `117: except Exception as ie: results[agent] = {'error': str(e) + " inner=" + str(ie)}`. Inner handler is broad and uses outer `e`. |

## Tool Usage
| Tool | Called? | Result |
|---|---|---|
| `get_rule_core_text` | Yes | OK, bootstrap returned core rule. |
| `open_session` | Yes | OK, session resumed/opened for project root. |
| `read_file_range` | Yes | OK for cited ranges except `_dispatch_scan2.py`, which returned file not found. |
| `get_file_symbols` | Yes | Called for all cited files; symbol lists returned for existing files, `_dispatch_scan2.py` returned not found. |
| `validate_file` | Yes | Existing Python files validated OK; `_dispatch_scan2.py` returned file not found/syntax validation failed. |
| `search_code` callers mode | Yes | Called for cited symbols/terms to cross-check dead/orphan claims; callers mode returned no matches for searched terms in this run, so verdicts rely primarily on cited source plus symbols/validation. |
| `write_new_file` | Yes | This artifact written with `content_encoding=base64`. |

## Summary
- CRITICAL approved: 0/3
- HIGH approved: 0/7
- MEDIUM approved: 1/5
- Overall: NEEDS_REVISION

END_VERIFY_CODOC_ROUND3