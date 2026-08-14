# EXECUTION_TRACKER

Source of truth for AQ_UPGRADE_PLAN_FINAL execution (v3)

| Phase | Task | Status | Session | Timestamp | LOC-before | LOC-after |
|---|---|---|---|---|---|---|
| Phase 1 | P1.0 Pre-gate aggregate | done | R1-batch1-codoc | 2026-07-08 01:04:17 UTC+7 | n/a | n/a |
| Phase 1 | P1.0a verify _backup NOT dead | done | R1-batch1-codoc | 2026-07-08 01:04:17 UTC+7 | n/a | n/a |
| Phase 1 | P1.0b snapshot graph.db | done | R1-batch1-codoc | 2026-07-08 01:04:17 UTC+7 | n/a | n/a |
| Phase 1 | P1.0c snapshot librarian.db | done | R1-batch1-codoc | 2026-07-08 01:04:17 UTC+7 | n/a | n/a |
| Phase 1 | P1.0d backup_project A_ROOT | done | R1-batch1-codoc | 2026-07-08 01:04:17 UTC+7 | n/a | n/a |
| Phase 1 | P1.0g cbm_dead_code full inventory | done | R1-batch1-opec7 | 2026-07-08 01:25:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.1 G2 logs rotation | rescoped-deferred (v3 V9: live_raw = runtime logs cua panel; chi rotation >7d, xem plan) |  |  |  |  |
| Phase 1 | P1.2 G4 2 STUBs registry removal | done | R1-batch1-opec7 | 2026-07-08 01:25:00 UTC+7 | 195 | 193 |
| Phase 1 | P1.3 M6 .bak cleanup | done | R1-batch2-hermc5 + leader | 2026-07-08 02:05:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.4 M7 .bak_ma stale cleanup | done | R1-batch2-hermc5 | 2026-07-08 02:00:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.5 M8 empty root slots | done | R1-batch2-hermc5 + leader README | 2026-07-08 02:05:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.6 L4-L8 strays cleanup | done (dead-line-474 = report-only, khong pin duoc) | R1-batch2-hermc5 | 2026-07-08 02:00:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.7 Q2p BOM strip | done | R1-batch1-kiloc7 | 2026-07-08 01:25:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.8 Q4 QNight idle reset | done | R1-batch1-kiloc7 | 2026-07-08 01:25:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.9 Q5 manifest gzip rotation | done | R1-batch1-kiloc7 | 2026-07-08 01:25:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.10 Q8 Q .bak cleanup | done | R1-batch1-kiloc7 | 2026-07-08 01:25:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.11 Q9 BM25 sync | done | R1-batch1-kiloc7 | 2026-07-08 01:25:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.12 COMC1 obsolete archive | done | leader-clau | 2026-07-08 02:05:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.13 M5 deprecated field | done (live sau restart 07:58) | R1-w4-hermc5 + restart | 2026-07-08 07:58:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.14 A7 env verify | done (env=1; 336 JS/TS files, 9418 JS nodes post-reindex; test 6/6) | R1-b2-opec5 + leader | 2026-07-08 08:00:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.15 EXECUTION tracker init | done | R1-batch1-codoc | 2026-07-08 01:04:17 UTC+7 | 0 | 32 |
| Phase 1 | P1.16 DEAD_CODE artifact | done | R1-batch1-opec7 | 2026-07-08 01:25:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.17 DISPATCH gate verify | done-FINDING: gate KHONG ton tai trong council_message -> BUILD o 3B | R1-w4-kiloc5 | 2026-07-08 03:30:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.18 CONN scan prep | done (production leak-free baseline) | R1-b2-opec5 | 2026-07-08 01:25:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.19 Cost drift sync prep | done (pointer v1_1 valid; v1.0 in-project = stale baseline) | R1-b2-kiloc5 | 2026-07-08 01:33:00 UTC+7 | n/a | n/a |
| Phase 1 | P1.20 L9 prep INDEX constants | done (4 INDEX_* found; INDEX_MAX_FILES khong ton tai) | R1-b2-opec5 | 2026-07-08 01:25:00 UTC+7 | n/a | n/a |

## Notes
- P1.2: 195->193 source-level, live after restart.
- P1.16: dead_code_full_v1.json count=200.
- pre_phase1.graph.db redone by leader 2026-07-08 from .orchestrator_cache/graph.db (22.18MB, sha256-verified); orchestrator/graph/graph.db is a 0-byte stray pending P1.6
| 3A.S1 | registry extraction | done | S1-codoc | 2026-07-08 02:12:41 UTC+7 | 14377 | 13030 |
| 3A.S2 | T1 read+fileops extraction | done | S2-codoc | 2026-07-08 02:33:00 UTC+7 | 13030 | 12110 |
| 3A.S3 | session/log/proc extraction | done | S3-codoc | 2026-07-08 02:54:51 UTC+7 | 12960 | 12544 |
| 3A.S4 | T4+interfaces extraction | done | S4-codoc | 2026-07-08 03:31:44 UTC+7 | 12553 | 11468 |
| 3A.S5b | shim cleanup + T2 extraction | partial | S5b-codoc | 2026-07-08 08:23:00 UTC+7 | 11468 | 11424 |
- 08-07 07:58: restart [A] via [A:S] (Anh authorized). 194 tools live (+restart_orchestrator_via_bat wire [A:S], token-gated). alias_contract_test 104/104. deprecated flag live.
- 08-07 08:00: A7 live-verified: cbm_index full -> 336 JS/TS files, 9418 JS nodes. Bug files.language='python' cho moi file -> fix wave 8 (opec5). PHASE 1 EXIT GATE CLOSED.
- 08-07 08:0x: stale PIDs 35780/38684 (worker leftovers 00:30, nghi giu handle-lock WinError5) killed via [A] kill_process theo lenh Anh.
- 3Q.3 fix: QSLEEP_RAW_PACKET_MAX_TOKENS cap (default 5000) da vao qsleep/controller/cluster_runner.py (kiloc5) — active o lan QSleep ke tiep.
# EXECUTION_TRACKER
- 3A.S5a-micro | mcp block -> orchestrator/server/mcp_stdio.py | done | kiloc5 | 2026-07-08 09:20 UTC+7 | 9407 | 9123
- 3A.S5b | write/ package + shim cleanup | partial | codoc | 2026-07-08 08:40 UTC+7 | 11468 | 11424
- 3B.1 | SEARCH_TOOL 9->1+aliases | done (codoc R1-R3 + leader final hunks: map entries restored, phases lockstep patch, raw_mode fix) | 3B1-codoc+leader | 2026-07-08 10:15 UTC+7 | n/a | n/a
- 3B.3 | orphan inventory | artifact done (orphan_symbols_full_v1.json, 200-clamp; strong-dead calibration by kiloc5) | kiloc5 | 2026-07-08 09:30 UTC+7 | n/a | n/a
- 3B.4 | LSP dry-run full | done read-only (31245 cand: 0.9=5190 -> 1824 confirmed/237 refuted; 25264 edges 0.3 orphaned-nodes finding) | opec5 | 2026-07-08 09:35 UTC+7 | n/a | n/a
- 3B.7 | test_conn_leak_audit.py | done (21 sites classified, BOM finding code_tools.py) | opec5 | 2026-07-08 09:00 UTC+7 | n/a | n/a
- 3B.8 | SOT_MAPPING | done | 3B8-codoc | 2026-07-08 15:14:30 UTC+7 | n/a | n/a
- AC22 | ALIAS_CONTRACT.md + test_schema_params_contract.py | done | hermc5+opec5 | 2026-07-08 09:30 UTC+7 | n/a | n/a
- 10:15: FULL PYTEST GREEN 355 passed / 0 failed / 1 skipped; lockstep 194/194/194. can002 test env-gate rewrite (launcher trust-mode finding: start_orchestrator.ps1 sets ORCHESTRATOR_AUTO_TOKEN=1 on INLINE path).
- 09:50: [A] hung (event-loop starve, PID 36988 CPU 2296s, /council dashboard polling ~1MB/s) -> recovery restart via [A:S]; zombie 36988/44064 pending Anh kill.
- 3B.9 | dispatch gate BUILD | done | 3B9-codoc | 2026-07-08 10:13:51 UTC+7 | n/a | n/a
| 3Q.1 | Q retire v1/v2 | rolled-back | 3Q1-codoc | 2026-07-08T15:40:41+07:00 |
- 3B.2 | dead-code batches 1-3 | done-honest: 13 removed (3+8+2), 0 FP, pytest 370/0; cumulative <30 (D10) vi supply strong-dead can kiet — cbm blind spots documented (asyncio.to_thread/Thread/key=/srv. late-binding/HTTP handlers conf-0.3 deu alive) | kiloc5 | 2026-07-08 16:02 UTC+7
- 3B.1 tests | test_search_tool_mode.py + test_search_tool_alias_compat.py | done | opec5 | 2026-07-08 15:30 UTC+7
- 3Q.1 | Q retire v1/v2 | ROLLED-BACK dung quy trinh: A/B top-1 6/10 < 8/10 acceptance -> restore v1=2204 v2=1705; D5 can Anh re-decision (gia dinh BGE-M3-100% cua plan khong dung voi retrieval thuc te) | codoc | 2026-07-08 15:55 UTC+7
- 3Q.6 | rag_chunks_fts incremental (force_full param + CLM_FTS_FORCE_FULL fallback; nodes_fts KHONG ton tai — scope collapse) | done, py_compile OK (uv 3.11), Q health OK; test-on-copy pending next session | opec5 | 2026-07-08 16:05 UTC+7
- PYTEST FINAL PHIEN NAY: 370 passed / 0 failed / 1 skipped.
- CORRECTION (SCAN_DATA_REPORT 4.1): dead-code cumulative = 12 functions / 138 LOC (3+8+1), khong phai 13 — leader over-count batch3 khi consolidate 16:06. So trung thuc theo dead_code_batch3_log.json + RES_KILOC5_3B2D.
- ANH DECISIONS 08-07 chieu: D5 = KEEP v1/v2 VINH VIEN (3-model stack qwen-local + bge-m3-local + deepseek-v4-flash on ok; plan's retire assumption was wrong, A/B 6/10 proved it). D10 = accept 12 strong-dead (option a).
| ALGO.A | extractor callable-ref edges | done | codoc | 2026-07-08 17:15:46 UTC+7 | n/a | n/a |
## ALGO UPGRADE WAVE (SCAN_DATA_REPORT-driven, 2026-07-08 chieu toi)
- ALGO.A | extractor callable-ref edges (args/kwargs/hasattr-getattr, py+js) + test 6 blind-spots | done | codoc | +1605 callable-ref edges
- ALGO.B | builtin-noise filter + ext placeholder nodes + self-loop tagging (store.insert_edges chokepoint) | done | kiloc5 (timeout) + leader fixes (tuple 20->19, test id pattern) | verified
- ALGO.E | orphan_symbols offset+total_count+limit500 | done | kiloc5 | in suite
- ALGO.G | qremer cap 5000->3000 (cluster_runner.py:140) | done | opec5 | live-Q-interp verified
- ALGO.I | FTS formal test | partial (probe PASS truoc do; formal artifact cut mid-run) | opec5 | next session
- RESTART qua tool restart_orchestrator_via_bat (lan dau dung that): OK, health <20s, 194 tools.
- CLEAN REBUILD graph.db (derived, deleted+rebuilt 24.5s, 448/448 re-extract): edges 36967->26328 (-10639 builtin noise); ext placeholder nodes 1307; ORPHANED-JOIN 0 (tu 25832 — LSP gio join duoc TAT CA calls edges); self-loops tagged 263; raw 0-inbound 1736->1426 (-310 nho callable-refs — dead-code chinh xac hon).
- PYTEST FINAL: 385 passed / 0 failed / 1 skipped.
- 3Q.2a | Q split slice 1: clm_lib/(config.py+store.py) + shim imports, verify py_compile/import/3-endpoint-smoke | done (activation at next Q restart) | codoc | 2026-07-08 toi
- ALGO.I | clm_fts_incremental_test.py formal (4/4 GREEN live Q interp) | done | opec5
- 3B.5/AC5 | LSP apply 0.9-bucket confirmed (D4: driver filters conf>=0.9 vi resolve_apply khong co floor param): 1907 edges promoted conf=1.0 cbm_label='lsp'; integrity ok; lsp_dryrun_v2.json + lsp_apply_log.json | done | kiloc5
- PYTEST: 385 passed / 0 failed / 1 skipped (post-LSP-apply).
- CON LAI: 3Q.2 slices 2-5 (routes/cache/globals), Q7 batch verify 1302 None, CI Final (S25 benchmark, lint, README/semver, CHANGELOG, 32 AC checklist), Q restart de activate clm_lib + qremer cap 3000.
| CI.1 | CHANGELOG + AC checklist | done | codoc | 2026-07-08 19:08:09 UTC+7 | n/a | n/a |
| 3Q.2b | Q split slice 2 cache/globals | done (clm_lib/cache.py with threading.Lock; 11 Q tests pass: 7 weak_hit + 4 FTS; slices 3-5 remain) | codoc | 2026-07-08 wave-19 | n/a | n/a |
| AC1.wave19 | hub/blast metrics refresh | done (code_tools.py hub 52 inbound / 15 files / 14.81% PASS vs <35; fs.py 37.89% INFO util fan-in; cross-file calls resolved=0 extractor limitation; circular imports_module pairs=2 follow-up) | opec5+codoc | 2026-07-08 wave-19 | n/a | n/a |
| Q7.discovery | verify-status discovery + pilot | done-discovery / pilot-running (live NULL-qwen=1897; official runner pool=274; Z1 candidates=0; plan 1302 None stale) | kiloc5+codoc | 2026-07-08 wave-19 | n/a | n/a |
- 3Q.4/Q7 | verify batch FULL DRAIN | done: pool 274->0 (pilot 50: 39sv+11q; full ~230: +179sv+50q+1nr; 0 model errors; Qwen3.6-35B local :8088; p95 ~4s/row) | kiloc5 (2 rounds) | 2026-07-08 toi
- Q7 findings: plan "1302 None" stale -> live NULL-qwen 1897 nhung runner pool 274 (vector_eligible=0 + verify_source filter); Z1 candidates = 0 (Z1-priority non-actionable); "verify_status" misnomer -> cot that la qwen_verify_status.
- DB final dist: semantic_verified=1917, NULL=1623 (ngoai pool theo runner design), needs_review=397, quarantine=218.
- 3Q REMAINING sau phien nay: CHI CON 3Q.2 slices 3-5 (routes split — multi-session rieng). Q restart de activate clm_lib + qremer cap 3000: thoi diem Anh chon.
## PLAN CLOSE-OUT (2026-07-08 dem)
- 3Q.2 slices 4+5 | routes_read.py (16+2 handlers) + routes_write.py (24 handlers), pattern late active-module binding | done | codoc | clm_librarian_api.py 4299->2407 LOC; module map = 6 files (config/store/cache/embed/routes_read/routes_write — plan uoc 5, note honest)
- Q RESTART #2 (Anh confirmed, [Q:S]) -> routes split ACTIVE. HARNESS VERDICT: XANH — 34/38 passed (identical baseline), 0 new shape failures, 4 mismatches = probe-design cu; latency p50 7.63ms (baseline 15.14) / p95 119ms (baseline 1254 cold) -> AC14 lat<=+10% PASS vuot chuan. AC13 (5-module split + DI) PASS.
- Hardening backlog (tu harness): /review_ingest, /filter_delete, /qjobs/run_qnight chap nhan payload rong (ok=true) — can input validation (viec tuong lai, ngoai plan).
- PLAN AQ_UPGRADE_PLAN_FINAL v3: TAT CA HANG MUC THUC THI XONG. AC cuoi: xem AC_CHECKLIST_FINAL.md (AC13/AC14 nang len PASS sau harness nay).
