# VERIFY_OPEC_ROUND3 — Round 3 Scan Verification

Worker: OpeC (ope/glm-5.2) | Project: project-orchestrator | Mode: VERIFY-ONLY (T1 read)
Bootstrap: get_rule_core_text ok (cached, already_fetched=true) | open_session ok (session untitled-session-07-10-00-15)
Method: read_file_range at cited file:line (~25 lines context), verdict vs actual code.

## Finding Verdicts

| ID | file:line | Round3 claim | verdict | evidence |
|----|-----------|--------------|---------|----------|
| C1 | lease_orchestrator.py:37-39,150 | _read_processed_set adds ANY cluster_id regardless of status; recover_expired_leases appends "pending_rollback" -> rolled-back clusters falsely treated DONE | APPROVE | L37-39: `cid = entry.get("cluster_id"); if cid: seen.add(str(cid))` — no status filter. L150: `_append_processed(ledger_dir, cid, "pending_rollback")`. seen set ignores status so pending_rollback cids are treated as done. |
| C2 | qsleep_core.py:144-151 | os.kill(pid,0) on Windows unreliable; except(OSError,PermissionError) treats alive PIDs as dead -> removes LIVE lock | APPROVE | L146: `os.kill(pid, 0)`; L148: `except (OSError, PermissionError):` then L150: `lock_path.unlink(missing_ok=True)` returns True "Removed stale lock". PermissionError on Windows = process alive but no signal permission -> falsely removed. |
| C3 | qsleep_core.py:154-159 | check_lock then write_lock non-atomic; two instances can both pass check then both write | APPROVE | L144-151 check_lock reads+verifies; L154-159 write_lock opens "w" and dumps pid. No atomic create (O_CREAT\|O_EXCL) / no file lock between check and write -> TOCTOU race. |
| H1 | bootstrap_rule_loader.py:11 | requests.post(..., fields=[...]) fields not valid requests param | APPROVE | L8-12: `requests.post(url, json={...}, fields=["ok","already_fetched","text","sha256"])`. `fields` is not a requests.post kwarg (requests_toolbelt/urllib3 concept) -> ignored/TypeError; response-field filtering intent broken. |
| H2 | mcp_phase3_fix.py:105-108 | fix.pop("method") mutates dict during iteration -> data corruption on retry | DENY | Cited L105-108 = `STUBS = ["semantic_search","type_search"]` — NOT fix.pop. The described bug DOES exist but at L157-158: `method = fix.pop("method","POST")` / `payload = fix.pop("payload", None) if "payload" in fix else fix.copy()` mutating shared FIXES[name] reference. Line citation wrong by ~50 lines. |
| H3 | lease_orchestrator.py:117-124 | process_callback not in try/finally -> lease held until TTL on exception | APPROVE | Full fn L60-130: acquire at L108, then L117-118 `process_callback(...)` then L121 `mgr.release(...)` — sequential, NO try/finally. Raise in callback bypasses release; docstring L77-78 even acknowledges crash-before-release. |
| H4 | loop_orchestrator.py:331,347 | step exceptions re-raised -> lease never released | APPROVE | L331 `raise` and L347 `raise` after check_stop non-stop branch. Re-raise propagates past caller process_cluster's unprotected release (H3) -> lease not released. (Lease acquire/release lives in lease_orchestrator.process_cluster.) |
| H5 | loop_orchestrator.py:67-74 | _run_hermes subprocess returncode NEVER checked -> stale output read as success | APPROVE | L67-74: `_run_hermes` does `return subprocess.run(cmd, ...)` directly — .returncode never inspected in this function; CompletedProcess returned raw to caller. |
| H6 | _dispatch_scan2.py:80-93 | File INCOMPLETE: unterminated tuple, SyntaxError. Superseded by v2 | APPROVE | total_lines=93; L80 `PROMPTS['hermes'] = (` opens paren, file ends L93 mid-expression `...limit=50. Report time + count.\n'  ` with no closing `)` -> SyntaxError. _dispatch_scan2_v2.py exists as replacement. |
| H7 | _verify_comc_argv.py:16-32 | Verification uses bare assert -> stripped under python -O -> false pass | APPROVE | L17,19,21,24,31,32 all `assert ...`; L34 `print("VERIFY_COMC_ARGV_OK")`. Under `python -O` asserts are no-ops -> always prints OK regardless of argv correctness. |
| M1 | qsleep_core.py:151 | return string not f-string, pid not interpolated | APPROVE | L151: `return (True, "Removed stale lock (PID {pid} dead)")` — plain string, no f prefix; `{pid}` literal. Contrast L147 `f"Another QSleep instance running (PID {pid})"` correct. |
| M2 | mcp_phase4_final.py:45 | bare except: catches KeyboardInterrupt/SystemExit | APPROVE | L45: `except:` (bare) -> L46 print. Catches BaseException including KeyboardInterrupt/SystemExit. |
| M3 | mcp_phase4_final.py:24-27 | get_token() defined but NEVER called | APPROVE | L24-27 `def get_token(): ... return "auto"`. Token actually fetched at L37-44 via urllib /api/token directly. No get_token() call anywhere in file (scanned L1-188). Dead code. |
| M4 | mcp_phase4_final.py:186-188 | writes report back to INPUT file -> destroys original | APPROVE | L30 reads `REPORT_IN` ("r"); L186 `with open(REPORT_IN, "w", ...) as f: json.dump(report, f, ...)` overwrites same input path -> original input destroyed. |
| M5 | _dispatch_scan2_v2.py:117 | bare except: str(e) refers to OUTER HTTPError, loses inner error | APPROVE | L115 `except urllib.error.HTTPError as e:`; L116 `try: results[agent]=json.loads(e.read())`; L117 `except: results[agent]={'error': str(e)}` — bare except binds no inner name, `e` is outer HTTPError, inner parse error lost. |

## Summary
- CRITICAL approved: 3/3
- HIGH approved: 6/7  (H2 DENIED: line citation 105-108 wrong — points to STUBS list; real fix.pop bug at 157-158)
- MEDIUM approved: 5/5
- Overall: NEEDS_REVISION — 14/15 findings accurate; H2 substance valid but line citation must be corrected to mcp_phase3_fix.py:157-158.

END_VERIFY_OPEC_ROUND3
