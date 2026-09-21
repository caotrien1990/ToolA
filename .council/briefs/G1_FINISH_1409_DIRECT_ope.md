# G1-FINISH-1409-DIRECT — OpeC isolated leftover (1)+(2) + room CP-032

Session moi, one-shot. Khong nho turn truoc. Khong dung lich su chat.
If this task must write outside cwd, bootstrap [A] and use [A] write tools.
Dung HTTP /api/call ONLY. Khong MCP. Khong browser.

## MODE (doc truoc, bat buoc)
MUTATE AUTHORIZED. Exact keyword present: `tiến hành`.
Anh da noi `tiến hành` + `bật hội đồng tr` + goi worker ope + worker thuc thi code va bao cao vao room.
Duoc SUA isolated tree + ghi room CP-032 + ghi exclusive artifact + chay pytest/pyright qua [A].
KHONG delete. KHONG promote. KHONG recycle. KHONG kill P5.
Day KHONG phai VERIFY-ONLY. Dung dung de hoi lai. Lenh `tiến hành` da duoc cap.

## WRITE PATH (khong dung, doc het)
Host OpenCode co the goi y `apply_patch`. Tren chien dich nay `apply_patch` KHONG phai duong ghi.
Duong ghi duy nhat Anh uy quyen: HTTP POST http://localhost:8083/api/call/write_fix va write_new_file.
Doc = HTTP read_file_range (seed token). Test = HTTP run_project_test_script.
Day KHONG phai xung dot. Rule chien dich [A] thang host apply_patch. Khong ASK, khong dung som.
write_fix expected_hash = prior write_fix.new_hash (raw-byte SHA256). KHONG truyen read_file_range.content_hash.
Vietnamese/multiline: content_encoding=base64, encode qua Python + HTTP. Khong copy b64 bang tay qua JSON.
read_file_range: start_line <= end_line.

TASK_ID: G1-FINISH-1409-DIRECT
OBJECTIVE_CLASS: isolated-g1-owner-recovery-and-process-reservation
RISK_LEVEL: mid
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 2
FILES_SUBSYSTEMS: 2
EXPECTED_TOOL_CALLS: 40
EXPECTED_WRITES: 8
DEPENDENCY_DEPTH: 0
EXPECTED_END_MARKER: END_OpeC_DIRECT_G1-FINISH-1409

You are OpeC. Equal peer. Round 1 of THIS session: khong peer scoring.

## OBJECTIVE
Dong 2 leftover G1 tren isolated tree, chay test that, ghi room CP-032.
G1 chi ACCEPTED khi ca 2 acceptance dat. Neu 1 cai con lo: giu G1 NOT ACCEPTED / DO NOT PROMOTE, ghi ro lo con lai.
Khong bia PASS. Khong tuyen 4-sprint CLOSED / G3 PASS / G4.

## CONTEXT (verified 2026-09-14)
- Isolated tree: D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\_iso_campaign\hold_retrieve_g0_20260914\
- README hash c159aa8d6cd1c49350dfc9094fd7ff9ba9a645d2be6f2c9ca28ad962a4a0458c — G1 NOT ACCEPTED / DO NOT PROMOTE.
- Prior isolated work (already in tree, do not redo): 62 PASS / 15.85s op e0d7ca829df8e5b6; pyright0 op d9c6e0d686abff30. Task 3/4/5 xong. Task 1+2 con.
- ClauLTN G1 baseline: 34 PASS / 11.39s op 73f09457ae858217. Hash 6/6 MATCH op 9095a718bfc82c91. writer_admission extra hash f321b0f718c8e0b75a1469520d8a77fe3573c61458b55699c5397902d769262d.
- Isolated candidate hashes (baseline): hold 23362c79b056321bc5d4f04b52d444c2248e9f057d9a746327b426aa1c261a47 ; API 05c45d03dd05fe3ad1151bdf643eb61345c66e187d38049508d87685ecedbd92 ; semantic 22a888e706f817723b10e47978cc88212d5949013a9d029637cee58770bb01e7.
- Production UNCHANGED: hold 7ff8212b / API 5f882ba0 / semantic 3b9c5eb2. HEAD cf3db09134bc6776f1b0c54e7202ee8934fc95f7.
- Room SOT: D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_1009\ROOM_TOOLQ_1009.md
- Room current write_fix.new_hash (KHONG dung content_hash): 59d75e430da263bf274f6f9bdd78982531da6d8b39358eea09c41e1ab69e3492
- Next CP = CP-1009-032. Session room: room-1009-cp027-leftover-execute-clau-93696391-e13d19fd294e
- Live P5 14332 + runner 49904 + embed 32512 + 36572. :8082 PID 48500 generation 48500. KHONG kill.
- A-list 1009 CLOSED (CP-024). 4-sprint OPEN. G3 0.239h/72h nightly=0 NOT PASS. Snapshot WAIVED CP-028 != G3/G4.
- writer_admission.py (iso, 119 lines): writer_window + AdmittedConnection. Authorizer acquires writer_window before BEGIN/SAVEPOINT/DML/DDL. reader_scope DENY aux writes as interactive_read_aux_write_blocked.
- Iso callers already copied: qsleep/bin/clm_qnight_runner.py, qsleep/bin/clm_qnight_common.py L695 run_clusters / L761 _connect_writer, bin/clm_folder_watcher.py, bin/clm_librarian_distill.py, bin/clm_lib/routes_read.py, bin/clm_lib/store.py.
- Cam librarian.db. Cam live :8082 nhu SUT (temp HTTP socket cho SSE/handler OK).

## TASK 1 — owner/recovery/ingest e2e (connector KHONG DU)
Issue: tests/test_real_writer_callers.py chi goi _connect_writer + runner._maintenance. Thieu:
- QSleep run_clusters (iso qsleep/bin/clm_qnight_common.py L695): claim O_CREAT|O_EXCL + PID, orphan unlink+reclaim, roi _connect_writer L761.
- Watcher CLI (iso bin/clm_folder_watcher.py L2310): con poll cu `if _hold_active(): return 0`.
Target tren TEMP DB (cam librarian.db):
1. Goi that run_clusters: hold giua vong -> writer park -> release -> resume cluster con lai.
2. Flag owner PID chet / recycle -> reclaim, khong orphan, khong double-claim.
3. Watcher: drop file -> ingest -> hold -> park -> cycle sau ingest tiep.
4. _hold_active poll L2310: XOA hoac chung minh unreachable (moi path qua admission).
Xong (1) = owner/recovery/ingest song voi hold, khong chi connect duoc.

## TASK 2 — reservation qua process retrieval_deadline that
Issue: tests/test_reservation_routes.py unwrap:
  monkeypatch.setattr(routes, 'handle_query', routes.handle_query.__wrapped__)
Production bounded_route (bin/clm_lib/retrieval_deadline.py L253) spawn child execute_route L261, parent goi _schedule_retrieval_feedback L263 (routes_read.py L294). Child KHONG thua ke reservation/reader_scope. Hold BEGIN IMMEDIATE -> aux write treo busy_timeout.
Target:
1. KHONG unwrap retrieval_deadline. Spawn worker that.
2. Aux write: fail nhanh + ten ro (interactive_read_aux_write_blocked / unregistered_writer_or_db_busy) HOAC inherit reader_scope sang child. Chon MOT path, ghi ly do 1 cau.
3. /ask /query /pre_search tra ve trong budget — khong treo vi hold.
4. Feedback Q-5: drop hoac fail-named, khong block request thread.
Xong (2) = duong production process-worker khong deadlock voi hold.

## SCOPE
DUOC SUA:
- D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\_iso_campaign\hold_retrieve_g0_20260914\  (code + tests + README)
- D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_1009\ROOM_TOOLQ_1009.md  (CP-032)
- Exclusive artifact (BAT BUOC, ke ca khi PARTIAL):
  D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council\tmp_g1_finish_1409\DATA\OpeC_G1_FINISH_1409.md
  Last line PHAI dung: END_OpeC_DIRECT_G1-FINISH-1409

KHONG DUOC:
- Sua production bin/ (ngoai isolated tree)
- Promote / commit / recycle :8082 / apply_schema / COUNT librarian.db
- Kill P5 14332/49904/32512/36572
- Q_S.bat / recreate nightly 41492
- Tuyen G1 ACCEPTED neu con lo; tuyen G3 PASS / G4 / 4-sprint CLOSED
- Delete any file. Delete forbidden. Isolated edit/test/room/artifact MUTATE remains authorized.
- Native write / apply_patch.

## ACCEPTANCE
A. pytest isolated suite van PASS (khong hoi quy 62 test cu). Them test e2e Task 1 + Task 2.
B. Task 1: run_clusters owner/recovery + watcher file-ingest tren temp DB — PASS that, khong mock _connect_writer.
C. Task 2: KHONG unwrap; worker process that; aux write fail-named nhanh HOAC inherit scope; route khong deadlock.
D. _hold_active L2310 xoa hoac proven unreachable.
E. pyright isolated tree 0/0. Khong disable diagnostic.
F. README isolated cap nhat honest: ACCEPTED chi khi A-E dat; neu thieu thi NOT ACCEPTED + liet ke lo.
G. Room CP-032 ghi: ops, hash, so test, verdict G1 (ACCEPTED hoac NOT), A CLOSED giu, 4-sprint OPEN, khong promote.
   open_session project_path = D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_1009 session_title room-1009-cp032 agent_id=ope
   Sequential expected_hash bat dau 59d75e430da263bf274f6f9bdd78982531da6d8b39358eea09c41e1ab69e3492.
   Cap nhat: banner L7, 0b, leftover item 5, section 5 named auth CP-032, section 7, checkpoint row, section 9 evidence.
   Khong xoa CP cu.
H. Artifact OpeC_G1_FINISH_1409.md co evidence file:line + operation_id + ket luan.

## TESTS
- Isolated tree tests/: test_hold_g1.py, test_hold_handshake.py, test_hold_p5_p3.py, test_hold_sse_real.py, test_interactive_hold.py, test_reservation_routes.py, test_real_writer_callers.py, test_room_0309_park_hold.py.
- Them test e2e Task 1 + Task 2. Khong unwrap retrieval_deadline. Khong mock _connect_writer cho Task 1.
- Chay qua HTTP run_project_test_script, project_path = isolated tree. Khong pytest native neu [A] chay duoc.

## GRAPH_CONTEXT (advisory; graph index lech backup/st11 — dung iso tree that)
- qsleep_bin_clm_qnight_common.QNightJob.run_clusters file=_backup_stabilize_20260806T0833/qsleep_bin_clm_qnight_common.py:337 (EXACT) — STALE PATH. Live iso = _iso_campaign/hold_retrieve_g0_20260914/qsleep/bin/clm_qnight_common.py:695
- retrieval_deadline.bounded_route file=_iso_campaign/st11_post_promote_fix/bin/clm_lib/retrieval_deadline.py:253 (EXACT) — STALE PATH. Live iso/prod = bin/clm_lib/retrieval_deadline.py:253
Bao cao GRAPH_EVIDENCE: cite dung file:line iso that, ghi STALE neu dung path graph.

## OUTPUT CONTRACT
Broker/session reply toi da 8 dong tieng Viet: da lam gi, test so/op, G1 ACCEPTED hay NOT, room CP-032 hash, artifact path.
Chi tiet vao artifact + room. Khong dump full log.

## STOP
Sau 2 lan fail dung method: STOP + bao cao PARTIAL. Khong doi kien truc ngoai hold/admission/reservation. Khong them dependency. Khong chon 2 architecture-impacting paths — chon inherit-scope HOAC fail-named, ghi ly do 1 cau.

END_OpeC_DIRECT_G1-FINISH-1409
