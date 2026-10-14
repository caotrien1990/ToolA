# PLAN TOOLA_1210 V6 — ToolA chịu tải 50 CA, read dài không mất output

> Tác giả: ClauLTN. V6 (2026-10-13) = V5 + fix audit V5 (`V5-AUDIT-VERDICT.md`, xem §12). Thay thế V5, V4 và mọi bản dựng lại trước đó.
> **SOT cho đội muse = chính file này.** Không dùng bản dựng lại nào khác làm SOT. Mọi bằng chứng ở §2 có lệnh kiểm lại; bằng chứng nào không chạy lại ra đúng số thì đánh `UNSOURCED`, không dùng.
> Input bắt buộc: `GATE-REPORT.md` (museR; `13a3ab454b20d07dac0aed2252ae7566d921fb21` là **commit ToolA** mà report pin làm code SOT, không phải SHA của file report — muse pin SHA file report riêng khi dùng) và `IMPLEMENTATION-BRIEF.md` của job_status v3 (O3/O4 đã ratify).
> Phạm vi: chỉ muse và đội agent của muse thực hiện. Không dùng cơ chế council (không convene_council, council_message, council_worker_model, inbox/outbox).

**Đích**
1. 50 CA dùng ToolA cùng lúc mà không bị nghẽn hàng loạt.
2. Tool đọc (read, search, ocr, cbm) chạy lâu không mất output, kể cả khi session chết, hết limit hay server restart.

**Root cause đã verify (GATE-REPORT):** nghẽn không do CPU hay tool dài. Ba write path không điều phối nhau trên cùng lock domain `sessions.db` (SessionWriter 750 ms, ledger 100 ms, outbox). Request path chờ durable COMMIT không giới hạn: session gate, read-token, persist ở `finally`. Retry thiếu circuit breaker biến một lần giữ khóa thành stall vài phút. Danh tính bên giữ khóa: UNRESOLVED.

---

## Mục lục
1. SLO
2. Bằng chứng (có lệnh kiểm lại)
3. Quy ước chung: vai trò, khung step, checklist audit, real test sandbox, guardrail, flag
4. Harness H1–H8
5. Phase 0 → Phase 6
6. Bộ test cuối T1–T12
7. Rủi ro
8. Quyết định
9. Gate
10. Thay đổi so với V4
11. Yêu cầu cho vòng audit plan của muse

---

## 1. SLO (mọi test chấm theo đây)

Định nghĩa dùng chung:
- **Lỗi**: response không phải 2xx và không phải `503 SERVER_BUSY`, hoặc client timeout. `503 SERVER_BUSY` (có `retry_after_s`) đếm riêng, không tính là lỗi.
- **Orphan bỏ phí**: request đã chạy tool tới khi xong rồi mới bị `CALL_ORPHANED` (server làm xong nhưng client đã đi).
- **Baseline throughput**: trung bình call/phút của 5 phút liền trước.

1. **SLO1:** với 50 agent (H1), `read_file_range` đạt p95 < 2 s và p99 < 5 s.
2. **SLO2:** 0 orphan bỏ phí do chờ hàng. Call quá deadline nhận `503 SERVER_BUSY` trước khi tool chạy, trễ nhất deadline + 1 s.
3. **SLO3:** tải ổn định thì không có phút nào throughput sụt quá 80% so với baseline throughput.
4. **SLO4:** mỗi call có đúng 1 audit event theo `audit_id`, có mặt trong DB chậm nhất 60 s sau mốc max(lúc gọi, lúc khóa `sessions.db` được nhả). Call nằm ngoài cửa sổ khóa thì mốc là lúc gọi. 0 thiếu, 0 trùng, 0 event_id giả.
5. **SLO5 (write):** 0 write mất, 0 write apply hai lần. Write cùng file chạy tuần tự. Sau step 2.7, 50 agent ghi 50 file khác nhau trong một project có p95 < 1.5 × p95 của một write đơn lẻ.
6. **SLO6 (read dài):** read quá soft deadline trả stub trong soft deadline + 1 s. Kết quả lấy lại được khi session chết hoặc server restart, bằng cách gọi lại đúng args.
7. **SLO7 (burst):** burst 200 agent trong 2 phút thì 0 crash, lỗi < 1%, mọi call bị từ chối đều là 503 có `retry_after_s`, và hệ thống về lại SLO1 trong 60 s sau burst.

---

## 2. Bằng chứng (đã kiểm 2026-10-12; mỗi mục có lệnh kiểm lại)

Quy ước: `LOG` = `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\orchestrator.log`. Số dòng code tính trên HEAD ngày 2026-10-12. Commit đổi thì kiểm lại bằng `find_symbol`. **Log đã rotate lúc 2026-10-12 21:09:27:** dữ liệu ngày 2026-10-12 (kể cả incident) giờ nằm trong `orchestrator.log.1`, nên `LOG` cho mọi lệnh ở §2.2 = `orchestrator.log.1`. Số E15/E16 đo lúc ~18:30 ngày 2026-10-12; đếm lại cả ngày trên `orchestrator.log.1` (2026-10-13): contention **2,626**, orphan **683** (cùng tỉ trọng theo tool). Baseline dùng số cả ngày.

### 2.1. Code
1. **E1 — Funnel dispatch:** `orchestrator_server.py:14934` `_dispatch_tool_call_request`. Kiểm: `find_symbol _dispatch_tool_call_request`. (14934 là SỐ DÒNG, không phải số call.)
2. **E2 — write_status:** handler `orchestrator_server.py:18710`, tool `:18849`, route `:19042`.
3. **E3 — SessionWriter:** một writer cho cả process, `orchestrator/session/store.py:1014` `_run_session_write`. Durable call chờ COMMIT. Busy budget `_SESSION_DB_BUSY_TIMEOUT_MS = 750` (`store.py:60`).
4. **E4 — Ghi event:** `store.py:2259–2295`. `INSERT OR IGNORE` theo `audit_id` (idempotent). Contention thì log `session event persistence hit store contention` rồi raise (`:2290`). Bất biến A20/A33: không trả event_id giả.
5. **E5 — Ledger:** `orchestrator_server.py:2703/2851/2887/2937` `PRAGMA busy_timeout=100`. Lỗi chỉ log `write ledger persistence failed` (`:2749`), không retry, không spool.
6. **E6 — `finally` chặn response:** `orchestrator_server.py:16903–16915` `await asyncio.to_thread(_stage_durable_session_event, ...)` nằm trước lúc trả response, sau dòng log `CALL_ORPHANED` (`:16890`).
7. **E7 — Read-token đồng bộ:** `orchestrator/tools/read/fs.py:176` gọi `ct._read_token_upsert(...)` (durable). Contention đi qua bộ phân loại A40 `code_tools.py:1033` (`SESSION_STORE_CONTENTION`, `retryable:true`). Seal ở `orchestrator/tools/write/core.py:650` `_seal_read_token`.
8. **E8 — Khóa write theo PROJECT:** `orchestrator_server.py:2110–2117` `_tool_semaphore` → `_WRITE_PROJECT_SEMS[project_path] = asyncio.Semaphore(1)`. Ngoài ra có tầng `_TOOL_SEMS` theo category, env `ORCH_SEM_<CAT>` (`:2118–2125`).
9. **E9 — Lane:** `orchestrator/server/executor_lanes.py:272–292`. read_io 12/48, search 8/24, command 4/16, chờ vào hàng 2 s. Env `ORCH_EXEC_<LANE>_WORKERS/_QUEUE/_QUEUE_WAIT_SECONDS`.
10. **E10 — Singleflight có sẵn:** `orchestrator/core/call_cache.py:239` `call_signature` = sha256 `{tool, params}` sau khi bỏ `_SIGNATURE_IGNORED_KEYS`. `:383` `begin()`. Bên đến sau poll tối đa `CALL_SINGLEFLIGHT_WAIT_S` (`:766`). Chỉ áp dụng cho `CACHEABLE_TOOLS`.
11. **E11 — Spool JSONL có sẵn:** `orchestrator/storage/spool.py`. Ghi chú producer ở `orchestrator_server.py:1544–1552`. Writer drain spool khi `start()`.
12. **E12 — Cancellation scope chạy trước ledger begin:** `orchestrator_server.py:16111–16123` (`_setup_cancellation_scope` rồi mới `_write_ledger_begin_async`). Ledger là bản ghi write-ahead: comment `:16118` "record a mutating call BEFORE it runs".
13. **E13 — cbm khai mutating:** `call_cache.py:769–776`. `cbm_dead_code` và `cbm_test_gap` có `mutating:true`, `mutation_scope="derived_index"`, có thể reindex `graph.db`.
14. **E14 — Chưa có job infra:** `search_code` cho `job_status`, `async_status`, `boot_epoch` đều 0 hit.

### 2.2. Log 2026-10-12
15. **E15 — Contention:** `rg -c "^2026-10-12 .*store contention" LOG` → **2,178**.
16. **E16 — Orphan:** `rg -c "^2026-10-12 .*CALL_ORPHANED" LOG` → **532**: 458 read_file_range, 49 run_ps1_script, 17 write_fix, 3 search_code, 2 git_status, 2 git_diff, 1 run_project_command.
17. **E17 — Stall 211 s:** `15:34:29 CALL_ORPHANED tool=read_file_range ms=211438` và `tool=write_fix ms=219464`. Read nhỏ (khoảng 1–3 KB) phải chờ hơn 3 phút.
18. **E18 — 10 cửa sổ nghẽn** (throughput từ ≥ 80 call/phút rơi xuống < 15): 09:40, 10:15, 14:56, 15:11, 15:20, 15:31, 15:38, 15:43, 16:19, 17:21. Kiểm: `rg "^2026-10-12 .*Tool call:" LOG | cut -c12-16 | uniq -c`, rồi lọc phút liền trước ≥ 60 và phút hiện tại < 15.
19. **E19 — Đỉnh tải:** 162 call/phút lúc 12:41 (~2.7 call/s).
20. **E20 — Retry storm:** cùng một `audit_id` báo contention 37–57 lần, khoảng 3 s một lần. Kiểm: `rg "^2026-10-12 15:.*store contention" LOG | rg -o "audit_id='[0-9a-f]+'" | sort | uniq -c | sort -rn`.
21. **E21 — Writer không STALLED trong incident:** `SessionWriter STALLED` xuất hiện 14 lần (00:00, 09:40–09:58, 16:41), 0 lần trong 15:20–15:45. Wedge detector không thấy kiểu "fail đều đặn".
22. **E22 — Ledger fail:** `rg "write ledger persistence failed" LOG | cut -c1-10 | uniq -c` → 2026-10-11: 4, 2026-10-12: 21. Mốc incident: 15:23:19, 15:31:00, 15:37:20. GATE-REPORT §2.1 có bảng đủ ngày (258 lần, 2026-08-19 → 10-12).
23. **E23 — Event loop vẫn sống:** `GET /health` trả 200 ngay lúc 15:31:59, giữa cửa sổ nghẽn.
24. **E24 — Không phải tool dài:** 15:30–15:45 không có call `ocr_map` nào. Trong ngày chỉ có probe 11:55 và một GET schema lúc 15:34.

### 2.3. Môi trường
25. **E25 — DB:** `.orchestrator_cache\sessions.db` 338 MB lúc đo (tăng dần, ~344 MB lúc audit V5), nằm trên D: (SSD USB T7). Lock domain là `sessions.db`. (`durable_audit.db` không phải lock domain, đã rút khỏi bằng chứng.)
26. **E26 — Runtime:** Python 3.12 bản có GIL (`venv312`), máy 28 luồng logic.
27. **E27 — Số CA đồng thời:** 20–30 CA lúc incident. Nguồn: Anh báo, chưa đo. Phase 0 phải đo từ `agent_id` trong log.

28. **E28 — `read_file_range` không cacheable:** `orchestrator/core/call_cache.py:40–46` `CACHEABLE_TOOLS` = cbm_get_architecture, cbm_dsm, cbm_trace_path, cbm_nodes_query, cbm_edges_query, cbm_what_if, cbm_dead_code, cbm_test_gap, ocr_map, ocr_scan, ocr_review, search_code, find_symbol. `read_file_range` không có trong danh sách (có chủ đích: nó seed read-token, E7), nên `read_file_range` không có tầng RAM/singleflight.

### 2.4. Chưa biết (Phase 0 phải trả lời)
- U1: Bên giữ khóa `sessions.db` là ai (GATE-REPORT P-H UNRESOLVED).
- U2: 211 s chia cho các pha nào (gate, read-token, sem, tool, finally).
- U3: `write_fix` check token từ DB hay cache.
- U4: Nội dung `_SIGNATURE_IGNORED_KEYS` (danh sách `CACHEABLE_TOOLS` đã biết, E28).
- U5: Timeout client nhỏ nhất của từng harness.
- U6: COMMIT latency trên D: so với NVMe nội bộ.

---

## 3. Quy ước chung

### 3.1. Vai trò
1. **muse (điều phối):** viết spec cho từng step, chấm "Xong khi", xin Anh duyệt ở mỗi gate, commit.
2. **Agent build:** viết code production, chỉ sửa file trong scope của step.
3. **Agent audit:** khác agent build của step đó. Tự kiểm trước rồi mới đọc self-report của agent build.
4. **Agent test:** viết harness và chạy real test bằng skill test sandbox của muse. Chỉ báo cáo, không sửa code production.

### 3.2. Khung bắt buộc của mỗi step
Mỗi step phải có đủ 6 mục:
1. **Việc làm:** làm gì, file nào, sau flag nào.
2. **Kiểm tra:** lệnh, test hoặc số đo chứng minh step chạy đúng.
3. **Audit:** điểm riêng cần soi ở step này, cộng checklist §3.3.
4. **Real test:** kịch bản sandbox (§3.4) nếu step đổi hành vi. Step chỉ đọc ghi "không áp dụng".
5. **Xong khi:** điều kiện đo được bằng số. Chưa đạt thì chưa sang step sau.
6. **Rollback:** cách gỡ step.

### 3.3. Checklist audit chuẩn (mọi step có diff)
1. **A1 — Đúng scope:** diff chỉ chạm file của step. Kiểm bằng `ocr_map` hoặc `git diff --stat`.
2. **A2 — Flag OFF giữ hành vi HEAD:** chạy lại test liên quan với flag OFF, kết quả phải giống HEAD.
3. **A3 — Ghi qua [A]:** mọi lần ghi file đi `read_file_range` → `write_fix` hoặc `write_new_file`. Không ghi bằng shell.
4. **A4 — Test bắt được lỗi:** test mới GREEN trên code mới và RED khi revert dòng thay đổi chính (mutant). Phải có control GREEN đi kèm.
5. **A5 — Không regression:** full suite so với HEAD theo id test, không so theo số lượng. 0 fail mới. Log pytest phải strip ANSI.
6. **A6 — Không lộ token:** không có token hay secret trong log, response, report.
7. **A7 — Bất biến A20/A33:** không event_id giả, không nuốt contention rồi trả None, không bỏ event.
8. **A8 — Bằng chứng thật:** mọi kết luận kèm lệnh đã chạy và output thật.
9. **A9 — Reality check:** mọi con số hay chuỗi log mà step dựa vào phải được chạy lại lệnh và ra đúng.
10. **A10 — Implementer lane:** agent audit trả lời bằng văn bản: "nếu làm đúng step này, hệ thống hỏng ở đâu?" Ít nhất 1 kịch bản hỏng, kèm lý do nó không xảy ra hoặc test chặn nó.

### 3.4. Real test chuẩn (sandbox)
1. Chạy bằng skill test sandbox của muse.
2. Target là [A] cô lập, không bao giờ là [A] live (port 8083):
   - port riêng, ví dụ 18083;
   - thư mục cache/DB riêng (HOME riêng);
   - mọi `audit_id` do harness phát mang tiền tố `sbx-`.
3. Ba bằng chứng cô lập, nộp sau mỗi real test:
   - `/health` của sandbox có `pid` khác `pid` của [A] live;
   - đường dẫn DB của sandbox nằm trong thư mục sandbox (liệt kê file process sandbox đang mở bằng `run_ps1_script`);
   - DB live có **0 dòng** `audit_id LIKE 'sbx-%'`.
4. Không dựng được sandbox cô lập thì STOP và báo Anh. Tuyệt đối không test tải lên [A] live.
5. Report real test gồm: kịch bản, thông số (số agent, thời lượng, flag), số đo so với SLO, PASS/FAIL từng tiêu chí, đường dẫn log thô, commit hash đang chạy.

### 3.5. Guardrail
1. **G1 — Quyền:** mặc định chỉ đọc. Mỗi phase cần Anh nói "tiến hành" riêng.
2. **G2 — Destructive 2 turn:** mọi việc xóa (retention events, dọn folder job, xóa harness/sandbox) làm theo 2 turn.
   - Turn 1 liệt kê đích chính xác (đường dẫn đầy đủ, số lượng) rồi dừng.
   - Turn 2 chỉ xóa đúng danh sách Anh duyệt.
   - Chỉ xóa theo whitelist, không bao giờ "xóa hết trừ...".
3. **G3 — Restart:** chỉ qua `restart_orchestrator_via_bat`. Bằng chứng là `/health` có `pid` mới và `generation` đổi. Không restart [A] live khi agent khác đang có call dở.
4. **G4 — Feature flag:** mọi thay đổi hành vi đứng sau flag env, mặc định OFF. Rollback = tắt flag rồi restart.
5. **G5 — Không di chuyển DB** khi chưa thỏa §8A.2.
6. **G6 — Không thêm lock toàn cục mới.** Write khóa theo project (hiện tại), rồi theo file sau step 2.7.
7. **G7 — Phân loại tool theo schema:** dùng `mutation_scope` (§8B.3), không phân loại theo tên tool.
8. **G8 — 2 lần fail thì kiểm runtime:** cùng step FAIL 2 lần mà hành vi sandbox không đổi thì kiểm process thật, `/health`, generation, `.pyc` trước khi sửa tiếp.
9. **G9 — Số đo kèm đơn vị:** ms, s, call/phút, MB.
10. **G10 — Không tăng `busy_timeout`** để "fix". Busy budget 750 ms và 100 ms giữ nguyên.

### 3.6. Feature flag (mặc định OFF)
| Flag | Phase/step | Ý nghĩa |
|---|---|---|
| `ORCH_PHASE_TIMING` | 0.4 | Gắn timestamp theo pha cho mỗi request (chỉ quan sát) |
| `ORCH_LOCK_HOLDER_TRACE` | 0.6 | Instrumentation tìm bên giữ khóa (chỉ quan sát) |
| `ORCH_SPOOL_AUDIT` | 1.2/1.3 | Session event của tool đọc vào spool, không await |
| `ORCH_SESSION_GATE_NONBLOCK` | 1.4 | Session gate resolve từ RAM, ghi nền |
| `ORCH_READ_TOKEN_RAM` | 1.5 | Read-token RAM + write-through |
| `ORCH_LEDGER_SPOOL` | 1.6 | Ledger write-ahead vào spool fsync, SQLite chạy nền |
| `ORCH_WRITE_AUDIT_WAIT_MS` | 1.7 | Write chờ audit tối đa (mặc định 2000) |
| `ORCH_BREAKER_RETRY` | 1.8 | Circuit breaker cho retry storm của writer |
| `ORCH_ADMISSION_DEADLINE_S` | 2.2 | Deadline hàng đợi admission. Khởi đầu 20 s, chốt theo số đo 2.4 và 0.9 |
| `ORCH_AGENT_CONCURRENCY` | 2.3 | Số call đồng thời mỗi agent (8) |
| `ORCH_WRITE_LOCK_SCOPE` | 2.7 | `project` (mặc định) hoặc `file` |
| `ORCH_READ_JOBS` | 3.x | Bật cơ chế job cho read dài |
| `ORCH_READ_JOB_SOFT_DEADLINE_S` | 3.2 | Soft deadline (§8A.1) |
| `ORCH_PROCPOOL_TOOLS` | 4.x | Chạy tool nặng CPU trong process pool |

---

## 4. Harness (agent test xây ở Phase 0, dùng ở mọi phase)

- Harness nằm trong thư mục riêng ngoài cây production, nhận path và port qua tham số.
- Có hằng chặn cứng: từ chối chạy nếu target là port 8083 hoặc đường dẫn `.orchestrator_cache` của [A] live.
- Mỗi harness phải qua self-test có positive control trước khi được dùng để chấm.

1. **H1 `load_replay`:** giả lập N agent, phát lại hỗn hợp tool và args thật từ `USAGE_LOG.jsonl`.
   - Think-time lấy theo phân bố thật.
   - Timeout client theo profile harness (số đo 0.9).
   - Gửi header `X-Client-Request-Id` và `X-Attempt`.
   - Xuất p50/p95/p99 theo tool, call/phút, số 503, số lỗi, số client timeout.
   - Self-test: 1 agent → p95 xấp xỉ latency đo tay (sai số < 20%).
2. **H2 `lock_inject`:** mở kết nối riêng tới `sessions.db` của sandbox, `BEGIN IMMEDIATE`, giữ X giây. (H2 chỉ có nghĩa này trong toàn plan.)
   - Self-test: trong lúc giữ khóa, sandbox log `store contention`.
3. **H3 `crash_inject`:** restart sandbox đúng lúc đang có job hoặc write.
   - Self-test: `generation` đổi sau restart.
4. **H4 `session_death`:** client đóng socket giữa chừng, rồi một client khác (agent_id khác) gọi lại đúng args.
   - Self-test: trên HEAD, sandbox log `CALL_ORPHANED`.
5. **H5 `metrics_tap`:** poll `/health` mỗi 1 s (`executor_*`, `durable_audit_*`, `call_cache_*`, `admission_rejections`). Đếm trong log: `CALL_ORPHANED`, `store contention`, `SERVER_BUSY`, `lock_hold_ms`, `SessionWriter STALLED`.
   - Self-test: bơm 5 dòng giả vào log test, đếm phải ra đúng 5.
6. **H6 `audit_reconcile`:** đối chiếu `audit_id` đã phát với event trong DB sandbox. Xuất thiếu, trùng, event_id giả.
   - Self-test: xóa 1 event trong DB sandbox, harness phải báo đúng 1 dòng thiếu.
7. **H7 `write_race`:**
   - (A) 10 agent cùng ghi 1 file;
   - (B) 50 agent ghi 50 file khác nhau trong một project.
   - Self-test: (A) trên HEAD cho đúng 1 apply mỗi `expected_hash`.
8. **H8 `write_then_audit_stall`:** cho write apply xong file, rồi H2 khóa đúng lúc ghi audit/ledger.
   - Kỳ vọng sau Phase 1: response thành công với `audit_pending:true`, **không có 503**, file đúng nội dung, `write_status` cuối cùng `committed`.
   - Self-test: trên HEAD, ca này treo hoặc orphan (ghi lại số giây).

---

## 5. Các phase

### PHASE 0 — Đo đạc, sandbox, truy gốc (chỉ thêm code quan sát, sau flag)

**Step 0.1 — Tiếp nhận GATE-REPORT**
1. Việc làm: đọc `GATE-REPORT.md` (pin `13a3ab45`). Chạy `ocr_map` giữa pin và HEAD trên các file thuộc chuỗi lời gọi (`orchestrator_server.py`, `orchestrator/session/store.py`, `orchestrator/tools/read/fs.py`, `orchestrator/tools/write/core.py`, `code_tools.py`, `orchestrator/core/call_cache.py`).
2. Kiểm tra: liệt kê hunk đổi trong chuỗi lời gọi. Có hunk đổi thì kiểm lại các point bị ảnh hưởng.
3. Audit: agent audit tự chạy `ocr_map` rồi so kết quả.
4. Real test: không áp dụng.
5. Xong khi: có danh sách "point còn đúng / point cần kiểm lại" kèm hunk.
6. Rollback: không áp dụng.

**Step 0.2 — Dựng sandbox cô lập**
1. Việc làm: chạy [A] với port 18083, HOME/cache/DB riêng, audit_id tiền tố `sbx-`. Ghi lệnh khởi động vào README của harness.
2. Kiểm tra: đủ 3 bằng chứng cô lập (§3.4.3).
3. Audit: liệt kê file process sandbox đang mở bằng `run_ps1_script`. Không file nào thuộc `.orchestrator_cache` của [A] live.
4. Real test: không áp dụng.
5. Xong khi: 3/3 bằng chứng cô lập đạt.
6. Rollback: dừng process sandbox. Xóa thư mục sandbox theo G2.

**Step 0.3 — Xây harness H1–H8**
1. Việc làm: viết H1–H8 theo §4.
2. Kiểm tra: từng harness qua self-test có positive control.
3. Audit: đọc code harness, grep cứng `8083` và đường dẫn `.orchestrator_cache` live. Hằng chặn phải từ chối khi trỏ vào live (test bằng cách trỏ thử).
4. Real test: self-test chính là real test.
5. Xong khi: 8/8 self-test PASS và hằng chặn live hoạt động.
6. Rollback: không áp dụng.

**Step 0.4 — Timestamp theo pha (`ORCH_PHASE_TIMING`)**
1. Việc làm: ghi mốc thời gian cho mỗi request: `admitted`, `gate_done`, `read_token_done`, `sem_acquired`, `tool_done`, `finally_done`, `sent`. Ghi ra log hoặc metric. Không đổi hành vi.
2. Kiểm tra: trong sandbox, H2 giữ khóa 60 s cùng H1 30 agent. Bảng phân bổ thời gian theo pha phải trả lời U2.
3. Audit: overhead < 1% trên 10,000 call (so với flag OFF). Flag OFF thì không ghi gì (A2).
4. Real test: kịch bản ở mục 2.
5. Xong khi: có bảng p50/p95/max theo pha, cho biết stall nằm ở pha nào. Đây là phép bác bỏ giả định "không có holder suốt 211 s, chỉ là chuỗi chờ có retry".
6. Rollback: tắt flag.

**Step 0.5 — Trace read-token (U3)**
1. Việc làm: trace `fs.py:176` `_read_token_upsert` và `core.py:650` `_seal_read_token`. Xác định `write_fix` đọc token từ DB hay cache, và trong cùng transaction nào.
2. Kiểm tra: chuỗi lời gọi dạng file:line. Mỗi mắt xích đọc bằng `read_file_range`, bên gọi cross-check bằng `search_code(mode='callers')`.
3. Audit: agent audit trace độc lập rồi so hai chuỗi. Chỗ lệch phải giải thích bằng code.
4. Real test: không áp dụng.
5. Xong khi: trả lời được "DB hay cache" kèm bằng chứng. Đây là input bắt buộc của step 1.5.
6. Rollback: không áp dụng.

**Step 0.6 — Instrumentation tìm bên giữ khóa (`ORCH_LOCK_HOLDER_TRACE`)**
1. Việc làm, lớp trong process: factory bọc mọi `sqlite3.connect` tới `sessions.db`. Watchdog thread mỗi 1 s: connection nào `in_transaction=True` quá 2 s thì log stack chỗ đã mở transaction, kèm thread và tool.
2. Việc làm, lớp ngoài process: khi lock_hold vượt 30 s, `run_ps1_script` liệt kê process đang mở `sessions.db` (`handle.exe` nếu có), kiểm Defender exclusion, kiểm tool sync/backup trên `.orchestrator_cache`.
3. Kiểm tra: trong sandbox, mở một transaction DML không commit từ một connection bọc → watchdog log đúng stack trong ≤ 3 s. Dùng H2 (connection ngoài factory) → lớp ngoài process liệt kê đúng PID của H2.
4. Audit: overhead < 1%. Không đổi `isolation_level` hay hành vi commit của connection nào. Flag OFF thì factory không bọc.
5. Real test: hai kịch bản ở mục 3.
6. Xong khi: cả hai lớp bắt đúng holder giả trong sandbox. Sau đó deploy lên [A] live dưới flag ON (chỉ quan sát, cần "tiến hành" + G3).
7. Rollback: tắt flag rồi restart.

**Step 0.7 — Giả thuyết bên giữ khóa (U1)**
1. Việc làm: đối chiếu 10 cửa sổ nghẽn (E18) với log instrumentation 0.6 (khi đã có), WAL checkpoint, transaction dài, tool chạy ngay trước mốc nghẽn, `database is locked`.
2. Thứ tự nghi phạm: **in-process trước, external sau.**
   - Quét mọi `sqlite3.connect` vào `sessions.db` ngoài SessionWriter. DML không có `with conn:` hoặc không commit/rollback trong `finally` là nghi phạm.
   - Kiểm các direct-connect site có tắt autocommit hoặc có lệnh ghi khác không.
   - Kiểm `_apply_session_schema` (`store.py:1080` có `UPDATE sessions`) chạy khi nào; phải loại trừ restart trong cửa sổ nghẽn.
   - Ứng viên external: antivirus, backup, sync trên file 338 MB.
3. Kiểm tra: nêu giả thuyết cụ thể (ai giữ, bao lâu), rồi tái tạo trong sandbox. Phải thấy cùng kiểu: throughput rơi đột ngột, contention lặp khoảng 3 s.
4. Audit: agent audit tìm phản ví dụ, tức một cửa sổ nghẽn mà giả thuyết không giải thích được. Chỉ có tương quan thời gian thì chưa đủ.
5. Real test: tái tạo ở mục 3.
6. Xong khi, một trong hai:
   - (a) holder có tên, tái tạo được;
   - (b) ghi `UNRESOLVED` kèm danh sách đã loại và lý do. Khi đó **bỏ step 1.9**, các phase khác không bị chặn.
7. Rollback: không áp dụng.

**Step 0.8 — fsync/COMMIT latency (U6)**
1. Việc làm: DB tạm, WAL, `synchronous=NORMAL` và `FULL`. Đo 1,000 COMMIT trên D: và 1,000 COMMIT trên NVMe nội bộ.
2. Kiểm tra: bảng p50/p95/p99 (ms) theo ổ và chế độ.
3. Audit: DB tạm đúng nằm trên ổ đang đo, không chạm DB thật. Xóa DB tạm theo G2.
4. Real test: không áp dụng.
5. Xong khi: có bảng đủ hai ổ. Đây là input của §8A.2.
6. Rollback: không áp dụng.

**Step 0.9 — Timeout client từng harness (U5)**
1. Việc làm: endpoint sandbox cố ý ngủ 10, 20, 30, 60, 120 s. Gọi qua Codex, Claude Code, opencode, zcode và client HTTP thô, ghi mốc client bỏ cuộc.
2. Kiểm tra: mỗi harness đo 3 lần.
3. Audit: 3 lần nhất quán (sai số < 2 s).
4. Real test: chính là phép đo này.
5. Xong khi: có bảng timeout nhỏ nhất của từng harness. Đây là input cho §8A.1 và `ORCH_ADMISSION_DEADLINE_S`.
6. Rollback: gỡ endpoint ngủ.

**Step 0.10 — Baseline HEAD**
1. Việc làm: sandbox chạy đúng commit HEAD. H1 ở 10, 30, 50 agent, mỗi mức 10 phút. Sau đó H1 30 agent cùng H2 giữ khóa 60 s. Đo số CA đồng thời thật trong log live (E27) từ `agent_id`.
2. Kiểm tra: ghi p95/p99, throughput, orphan, contention, 503.
3. Audit: sandbox đúng commit HEAD (so hash). Không có process sandbox khác chạy song song.
4. Real test: chính là baseline.
5. Xong khi: có bộ số baseline. Mọi phase sau so với bộ số này.
6. Rollback: không áp dụng.

**Step 0.11 — Bảng điểm apply của write tool**
1. Việc làm: lấy danh sách tool `mutating:true` có ghi file từ registry (`GET /api/tools/names` + schema, không liệt kê tay). Với mỗi tool ghi file:line của thao tác thay đổi đĩa đầu tiên (điểm apply) và mọi thao tác chạy sau nó trước khi trả response.
2. Kiểm tra: mỗi dòng đọc lại bằng `read_file_range`.
3. Audit: agent audit lập bảng độc lập rồi so hai bảng.
4. Real test: không áp dụng.
5. Xong khi: bảng phủ 100% write tool trong registry. Đây là input bắt buộc của step 1.7.
6. Rollback: không áp dụng.

**REAL TEST PHASE 0 — Tái hiện incident 15:31**
1. Kịch bản: H1 30 agent trong 10 phút. Ở phút 3 và phút 7, H2 giữ khóa 60 s (hoặc dùng cơ chế holder tìm được ở 0.7).
2. PASS khi sandbox tái hiện đặc điểm E15–E21:
   - throughput rơi > 80%;
   - có `CALL_ORPHANED` của read nhỏ;
   - `store contention` lặp lại cùng `audit_id` khoảng 3 s một lần;
   - writer không báo STALLED;
   - `/health` vẫn trả 200.
3. Không tái hiện được thì STOP, chưa vào Phase 1.
4. Gate G0 (§9).

---

### PHASE 1 — Đưa persistence ra khỏi đường response (P0 + P1 + P2 của GATE-REPORT)

Nguyên tắc: **tool đọc không bao giờ chờ durable COMMIT. Write chỉ chờ có hạn. Việc ghi bền trước khi write chạy (write-ahead) được giữ nguyên.**

**Step 1.1 — Đo `lock_hold_ms` (luôn bật, không qua flag)**
1. Việc làm: trong SessionWriter và 4 site ledger, đo thời gian giữ khóa mỗi transaction, gắn nhãn vai (`session_event`, `session_gate`, `read_token`, `ledger`, `outbox`). Transaction > 500 ms thì log WARNING kèm vai và operation.
2. Kiểm tra: H2 giữ X s thì log `lock_hold_ms` (hoặc thời gian chờ) khớp X, sai số < 10%.
3. Audit: overhead < 1% trên 10,000 lần ghi. Không đổi hành vi commit.
4. Real test: H2 + H1 10 agent, 5 phút.
5. Xong khi: log hiện rõ transaction giữ lâu theo vai.
6. Rollback: revert commit.

**Step 1.2 — Session event của tool đọc vào spool (`ORCH_SPOOL_AUDIT`)**
1. Việc làm: với tool có `mutation_scope ∈ {none, derived_index}`, `finally` (E6) không `await` `_stage_durable_session_event`. Event được append vào **spool JSONL có sẵn** (E11, mở rộng chứ không xây spool thứ hai), fsync theo lô, rồi response trả ngay. Áp dụng cho cả stub của Phase 3.
2. Kiểm tra: H2 giữ khóa 60 s cùng H1 30 agent → read p95 < 2 s suốt cửa sổ khóa.
3. Audit:
   - grep đường đi của tool đọc: không còn `await` đồng bộ nào tới writer trong `finally`;
   - write tool không bị đưa vào nhánh này;
   - mutant: gỡ phần rẽ nhánh → test RED.
4. Real test: kịch bản ở mục 2.
5. Xong khi: đạt kiểm tra và audit.
6. Rollback: tắt flag.

**Step 1.3 — Drainer ghi theo lô + replay khi boot**
1. Việc làm: worker nền đọc spool, group commit N event mỗi 50–100 ms bằng `INSERT OR IGNORE` theo `audit_id`. Boot thì replay phần chưa drain. Contention thì backoff có trần, không bỏ event. Drainer đọc cả spool event (step 1.2) và spool ledger riêng (step 1.6), mỗi loại dedupe theo khóa riêng (`audit_id` / `operation_id`+`phase`). Dòng cuối bị cắt dở thì bỏ qua, không crash. Bỏ qua là an toàn: event cắt dở là event chưa ack; ledger `begin` cắt dở nghĩa là fsync chưa xong nên write chưa được chạy (bất biến 1.6); ledger `end` cắt dở xử lý bằng reconcile ở step 1.6.
2. Kiểm tra:
   - H6 sau khi drain: 0 thiếu, 0 trùng, 0 event_id giả;
   - H3 kill giữa chừng → sau boot H6 vẫn 100%;
   - spool bị cắt dở dòng cuối → drainer chạy tiếp.
3. Audit: A7. Drainer dùng chung SessionWriter hoặc writer có điều phối, không mở thêm write path không điều phối.
4. Real test: H1 30 agent + H2 60 s + H3 1 lần.
5. Xong khi: H6 100% trong ≤ 60 s sau khi nhả khóa (SLO4), cả bình thường lẫn sau crash.
6. Rollback: tắt flag. Drainer vẫn drain nốt phần đã spool.

**Step 1.4 — Session gate không chặn (`ORCH_SESSION_GATE_NONBLOCK`)**
1. Việc làm, tool đọc: resolve session từ cache RAM. `_auto_open_session` và `_close_stale_active_sessions` chuyển thành ghi nền có deadline. Quá deadline thì tool vẫn chạy, kèm `session_pending:true`.
2. Việc làm, write: vẫn cần session thật. Chờ gate tối đa `ORCH_WRITE_AUDIT_WAIT_MS`. Quá hạn thì trả `503 SERVER_BUSY` (retryable). Lúc này **write chưa chạy**, nên retry an toàn.
3. Kiểm tra:
   - H2 khóa → p95 thời gian qua gate của tool đọc < 200 ms;
   - write quá hạn gate nhận 503 trong deadline + 1 s;
   - file không bị đổi.
4. Audit:
   - event của call có `session_pending` vẫn gắn đúng session khi session được tạo xong (H6);
   - không tạo 2 session active cho cùng (project, agent) (unique index `uq_sessions_active_project_agent`).
5. Real test: H1 30 agent + H2 60 s.
6. Xong khi: đạt kiểm tra, 0 write treo vì gate, H6 100%.
7. Rollback: tắt flag.

**Step 1.5 — Read-token RAM + write-through (`ORCH_READ_TOKEN_RAM`)** (cần kết quả 0.5)
1. Việc làm:
   - Bản gốc khi process sống là map RAM key `(root, rel_path)`, lock theo file.
   - `read_file_range` seed vào map ngay. Ghi SQLite chạy nền (write-through qua spool hoặc writer).
   - `write_fix` check và seal token trên map RAM.
   - Sau restart, map rỗng thì nạp từ DB. Token chưa kịp xuống đĩa thì `write_fix` trả `FILE_CHANGED_OUTSIDE_ORCHESTRATOR`, CA làm `force_reseed`. Tốn một lần đọc lại, correctness giữ nguyên.
2. Kiểm tra:
   - (a) H2 khóa, 1,000 lần `read_file_range` → `write_fix` ngay: 0 false conflict;
   - (b) 2 agent cùng đọc rồi cùng ghi 1 file: đúng 1 bên thành công;
   - (c) H3 restart giữa read và write: conflict rõ ràng, không mất update.
3. Audit:
   - mutant bỏ check token → (b) RED;
   - token của file A không bao giờ seal được write file B;
   - map RAM có trần bộ nhớ.
4. Real test: (a), (b), (c) trên sandbox.
5. Xong khi: 3/3 PASS và mutant RED.
6. Rollback: tắt flag (quay về DB đồng bộ).

**Step 1.6 — Ledger write-ahead bền qua spool (`ORCH_LEDGER_SPOOL`)**
1. Việc làm: ledger là bản ghi write-ahead (E12). Bản ghi `begin` phải được **append + fsync vào spool TRƯỚC khi write chạy**. Phần ghi SQLite chạy nền qua writer có điều phối. Bỏ việc chờ SQLite, không bỏ việc ghi bền. Budget 100 ms giữ nguyên (G10). `write_status` tra theo thứ tự RAM → spool → DB. Ledger ghi vào **file spool riêng** (`ledger.spool.jsonl`), fsync **từng record**, tách khỏi spool event (fsync theo lô). Bản ghi `begin` mang `old_hash` và, với tool biết trước nội dung (`write_fix`, `write_new_file`), `new_hash`. Reconcile khi boot: `begin` không có `end` → so hash file hiện tại: khớp `new_hash` → `committed`; khớp `old_hash` → `not_applied`; còn lại → `unknown` (CA kiểm bằng read). Không bao giờ báo `committed` khi không có bằng chứng hash.
2. Kiểm tra:
   - H2 khóa trong lúc write → 0 row ledger mất (so số write với số row sau drain);
   - H3 kill ngay sau write apply → sau boot `write_status` vẫn trả đúng `committed`;
   - E22 (ledger fail) về 0 dưới H2.
3. Audit:
   - không có đường nào để write chạy trước khi ledger `begin` đã fsync;
   - `write_status` không bao giờ báo `committed` trước khi file thật sự đã ghi;
   - mutant: chạy write trước fsync → test kill RED.
4. Real test: H7 (A)(B) + H2 + H3.
5. Xong khi: 0 row mất, `write_status` đúng 100% sau crash.
6. Rollback: tắt flag (quay về ledger đồng bộ 100 ms).

**Step 1.7 — Write chờ audit có hạn (`ORCH_WRITE_AUDIT_WAIT_MS`, mặc định 2000)**
1. Việc làm: sau khi write đã apply, phần audit/ledger `end` chờ tối đa `ORCH_WRITE_AUDIT_WAIT_MS`. Quá hạn thì trả **thành công** kèm `audit_pending:true`, phần còn lại ghi nền.
2. Quy tắc bất biến: **write đã apply thì không bao giờ trả 503 hay lỗi retryable.** Trả 503 khiến CA retry, và write bị apply hai lần.
3. Kiểm tra: H8 → response < 3 s, thành công, `audit_pending:true`, không 503, file đúng, `write_status` cuối cùng `committed`.
4. Audit + enforce (cơ chế, không chỉ đọc bằng mắt):
   - dùng bảng **điểm apply** của step 0.11 (file:line thao tác đổi đĩa đầu tiên của từng write tool);
   - code sau điểm apply chạy trong một guard chung `post_apply_guard`: mọi exception bên trong chuyển thành response thành công kèm `audit_pending:true` + `operation_id`, hoặc `POST_APPLY_ERROR` với `retryable:false` + `write_status_url`. Bộ phân loại A40 không được chạy sau điểm apply;
   - test fault-injection cho **mọi** write tool trong bảng: ép lỗi (IOError khi append spool, `SessionStoreContentionError`, timeout) ở từng thao tác sau điểm apply → 0 response retryable, 0 lần 503;
   - mutant: gỡ guard → test RED;
   - test registry: write tool nào chưa có trong bảng điểm apply → test FAIL, để tool mới không lọt.
5. Real test: H8 + H7 (A).
6. Xong khi: H8 PASS, H7 (A) đúng 1 apply mỗi hash.
7. Rollback: tắt flag.

**Step 1.8 — Circuit breaker cho retry storm (`ORCH_BREAKER_RETRY`)**
1. Việc làm: SessionWriter phát hiện livelock (N batch liên tiếp fail contention trong M giây, kể cả khi đôi lúc vẫn commit được) → mở breaker. Khi mở:
   - ngừng retry liên tục, backoff có trần;
   - durable write mới đi vào spool;
   - caller **chưa thực thi** nhận `503 SERVER_BUSY` nhanh.
   Breaker half-open thử lại định kỳ. Breaker **không bỏ event, không trả event_id rỗng như đã ghi.**
2. Kiểm tra: H2 giữ 120 s → log contention không còn lặp kiểu E20; caller chưa chạy nhận 503 < 1 s; sau khi nhả khóa H6 100% trong 60 s.
3. Audit:
   - A7;
   - breaker không áp lên call đã apply (step 1.7);
   - wedge detector mới phân biệt "chậm nhưng có tiến triển" với "fail đều đặn" (sửa khuyết P-C3);
   - mutant: tắt phát hiện livelock → test RED.
4. Real test: H1 50 agent + H2 120 s.
5. Xong khi: đạt kiểm tra; không còn stall > 60 s.
6. Rollback: tắt flag.

**Step 1.9 — Sửa holder (chỉ chạy khi 0.7 kết luận (a))**
1. Việc làm: sửa đúng nguyên nhân (đưa vào writer có điều phối, thêm commit/rollback trong `finally`, chia nhỏ transaction, hoặc exclusion cho antivirus/backup cần Anh duyệt). Không tăng `busy_timeout`.
2. Kiểm tra: tái tạo của 0.7 không còn gây nghẽn; `lock_hold_ms` > 2 s về 0 dưới H1 50 agent.
3. Audit: diff chỉ chạm thành phần đã chứng minh là holder.
4. Real test: kịch bản tái tạo 0.7.
5. Xong khi: (a) đạt kiểm tra; hoặc (b) nếu 0.7 là `UNRESOLVED` thì ghi "bỏ qua" kèm lý do.
6. Rollback: revert commit.

**Step 1.10 — Báo cáo `lock_hold_ms` theo vai (input §8A.5)**
1. Việc làm: sau 1.1–1.9, chạy H1 50 agent 30 phút, xuất p50/p95/max `lock_hold_ms` và số lần chờ chéo vai.
2. Kiểm tra: bảng số.
3. Audit: số khớp log thô.
4. Real test: chính là phép đo.
5. Xong khi: có kết luận tách hay không tách bảng `events` bằng số (§8A.5).
6. Rollback: không áp dụng.

**REAL TEST PHASE 1**
1. Kịch bản: H1 50 agent trong 20 phút; 3 lần H2 giữ khóa 60 s; 1 lần H2 giữ 120 s; 1 lần H3 restart; H8 chạy 20 lần rải đều.
2. PASS khi:
   - SLO1 giữ suốt các cửa sổ khóa;
   - write trả về < 3 s (có thể kèm `audit_pending`);
   - H8 0 lần 503;
   - SLO4 (H6 100% ≤ 60 s);
   - E17 không tái hiện (không call nào > 60 s);
   - full suite 0 fail mới (A5).
3. Gate G1.

---

### PHASE 2 — Kiểm soát lưu lượng và chia công bằng

**Thứ tự funnel bắt buộc (normative):** admission/deadline → per-agent queue → session gate → tool semaphore `_TOOL_SEMS` (hoặc khóa write) → lane executor → tool → `finally` → response. Mọi lần từ chối trước điểm "tool" trả cùng một `503 SERVER_BUSY` kèm `retry_after_s` và `ca_next_action`.

**Step 2.1 — Deadline từ client + định danh retry**
1. Việc làm: nhận `client_deadline_ms` qua header (ưu tiên) hoặc field. Thiếu thì lấy mặc định theo profile harness (0.9). Log `X-Client-Request-Id` và `X-Attempt` của mỗi request.
2. Kiểm tra: unit test 3 trường hợp: có header, có field, không có cả hai.
3. Audit: field không lọt vào args của tool (không sinh `UNSUPPORTED_FIELD`); không đưa vào `call_signature`.
4. Real test: không áp dụng.
5. Xong khi: test PASS, mutant RED.
6. Rollback: revert commit.

**Step 2.2 — Admission fast-fail (`ORCH_ADMISSION_DEADLINE_S`)**
1. Việc làm: call đang chờ ở bất kỳ tầng nào trước điểm "tool" mà quá deadline thì trả 503 và không bắt đầu chạy. Đặt admission trước session gate.
2. Kiểm tra: H1 burst 200 agent → 0 orphan bỏ phí (SLO2); 100% call quá deadline nhận 503 trong deadline + 1 s.
3. Audit:
   - write đã bắt đầu không bao giờ bị cắt;
   - `SERVER_BUSY` có `retryable:true`;
   - thông điệp nằm trong contract và mô tả tool, không thêm field giải thích (ROOT-CAUSE RULE);
   - 503 này là HTTP 503 ở tầng admission, khác A40 (lỗi trong body).
4. Real test: burst 200 agent.
5. Xong khi: SLO2 PASS.
6. Rollback: tắt flag.

**Step 2.3 — Giới hạn đồng thời theo agent + hàng chờ công bằng (`ORCH_AGENT_CONCURRENCY=8`)**
1. Việc làm: mỗi `agent_id` tối đa 8 call đồng thời. Vượt mức thì xếp hàng riêng của agent đó, chia xoay vòng giữa các agent. Chỉ 503 khi quá deadline. Call thiếu `agent_id` vào một nhóm chung.
2. Kiểm tra:
   - 1 agent × 50 call đồng thời → đúng 8 in-flight, 42 chờ, 0 call quá deadline + 1 s;
   - 1 agent spam 100 call/s trong 60 s cùng 49 agent tải thường → 49 agent vẫn đạt SLO1.
3. Audit:
   - call thiếu `agent_id` không né được giới hạn;
   - không deadlock khi agent gọi lồng (tool gọi tool);
   - hàng chờ có trần bộ nhớ.
4. Real test: hai kịch bản ở mục 2.
5. Xong khi: cả hai PASS.
6. Rollback: tắt flag.

**Step 2.4 — `admission_rejections` theo tầng + bậc thang tải + chỉnh lane**
1. Việc làm: `/health` thêm `admission_rejections` tách theo `agent`, `sem`, `lane`, `deadline`. Chạy H1 ở 50, 100, 150, 200 agent để tìm mức đầu tiên thấy 503 ở từng tầng. Đặt `ORCH_EXEC_*` và `ORCH_SEM_*` theo số đo, ghi vào config kèm lý do bằng số.
2. Kiểm tra: đối chiếu Little's law (in-flight ≈ tốc độ × latency) với số in-flight đo được.
3. Audit: mọi con số chọn đều có số đo đi kèm, không chọn theo cảm tính.
4. Real test: bậc thang 4 mức × 10 phút.
5. Xong khi: có bảng "mức tải đầu tiên thấy 503" theo tầng; ở 50 agent `rejection_counts` ≈ 0 và `queue_wait` p95 < 500 ms; `ORCH_ADMISSION_DEADLINE_S` chốt theo số đo.
6. Rollback: xóa env, quay về mặc định.

**Step 2.5 — Kiểm singleflight (U4)**
1. Việc làm: đọc `_SIGNATURE_IGNORED_KEYS`, xác định agent_id, session, `fields`, `client_deadline_ms` có nằm trong key không.
2. Kiểm tra: dùng tool **có trong `CACHEABLE_TOOLS`** (E28), ví dụ `search_code` hoặc `ocr_map` cùng args: 20 agent gọi đồng thời → `call_cache_joins_waited_total` tăng ≈ 19, tool chạy thật 1 lần; query hoặc project khác nhau → không bị gộp. **Cấm** thêm `read_file_range` vào `CACHEABLE_TOOLS`: nó seed read-token, phục vụ từ cache sẽ bỏ qua seed và có thể trả nội dung cũ trong TTL 300 s.
3. Audit: `fields` khác nhau phải ra kết quả đúng của từng caller (gộp chỉ đúng khi cache lưu answer thô rồi project theo từng caller).
4. Real test: hai kịch bản ở mục 2.
5. Xong khi: PASS, hoặc FAIL kèm mô tả sai → sang 2.6.
6. Rollback: không áp dụng.

**Step 2.6 — Sửa singleflight (chỉ khi 2.5 FAIL)**
1. Việc làm: sửa đúng chỗ sai mà 2.5 chỉ ra, vẫn một hàm key duy nhất.
2. Kiểm tra: lặp lại kiểm tra 2.5.
3. Audit: không có hàm key thứ hai; mutant RED.
4. Real test: như 2.5.
5. Xong khi: kiểm tra 2.5 PASS.
6. Rollback: revert commit.

**Step 2.7 — Khóa write theo file (`ORCH_WRITE_LOCK_SCOPE=file`)**
1. Việc làm:
   - key = project + normalized `file_path` (lowercase, `/`);
   - tool ghi nhiều file lấy tập lock theo thứ tự sort;
   - khóa 2 tầng. (1) **Khóa project kiểu reader-writer:** write một file (`write_fix`, `write_new_file`) lấy **shared**; tool không có `file_path` cụ thể (`git_commit`, `run_*`, xóa hàng loạt) lấy **exclusive**. (2) **Mutex theo file, luôn exclusive:** write một file lấy thêm mutex của đúng file đó sau khi đã có shared project;
   - hệ quả: hai write cùng file → cùng mutex → chạy tuần tự (SLO5); hai write khác file → khác mutex → song song; `git_commit` chờ mọi shared nhả;
   - thứ tự lấy cố định: khóa project trước, rồi mutex file theo thứ tự sort. Không bao giờ lấy mutex file trước khóa project.
2. Kiểm tra:
   - H7 (B) → p95 < 1.5 × một write đơn lẻ (SLO5);
   - H7 (A) → đúng 1 apply mỗi hash;
   - `git_commit` chạy song song với `write_fix` → `git_commit` chờ hết write của project.
3. Audit:
   - 2 path khác chữ hoa/thường hoặc `\` vs `/` trỏ cùng file → cùng khóa;
   - không deadlock (test đảo thứ tự lấy lock);
   - `expected_hash` vẫn kiểm.
4. Real test: H7 (A)(B) + kịch bản git_commit.
5. Xong khi: 3 kiểm tra PASS.
6. Rollback: `ORCH_WRITE_LOCK_SCOPE=project`.

**Step 2.8 — 503 tới được CA thật**
1. Việc làm: gửi 503 `SERVER_BUSY` qua 4 harness thật (Codex, Claude Code, opencode, zcode), ghi CA thấy gì (lỗi rõ, retry ngầm, hay timeout). Dùng `X-Attempt` để phân biệt client tự retry với call mới.
2. Kiểm tra: bảng theo harness: "thấy 503 / retry ngầm N lần / nuốt".
3. Audit: số attempt trên server khớp hành vi harness.
4. Real test: chính là phép đo.
5. Xong khi: có bảng. Harness nào nuốt 503 thì ghi vào report và bổ sung `ca_next_action` vào **mô tả tool** (không thêm field response).
6. Rollback: không áp dụng.

**REAL TEST PHASE 2**
1. Kịch bản:
   - (a) 1 agent bắn 100 call song song cùng 49 agent tải thường, 10 phút;
   - (b) burst 200 agent trong 2 phút rồi về 50 agent;
   - (c) H7 (B).
2. PASS khi: (a) 49 agent đạt SLO1; (b) đạt SLO7 và SLO2; (c) đạt SLO5.
3. Gate G2.

---

### PHASE 3 — Implement job_status v3 + delta (read dài không mất output)

Phạm vi: chữa ca read dài khi session chết (ca Codo), **không** chữa nghẽn (P-C5). Spec v3 (`IMPLEMENTATION-BRIEF.md`) là spec gốc, O3 (`meta.json`) và O4 (job_status là tool) giữ hiệu lực, rate-limit 3 lớp của v3 giữ nguyên. Delta mâu thuẫn với v3 thì muse báo Anh, không tự sửa.

**Step 3.0 — Bảng đối chiếu delta với v3** (muse điền §x thật từ IMPLEMENTATION-BRIEF, agent audit kiểm từng ô)

| Delta | v3 §x nói gì | Delta đổi gì | Verdict |
|---|---|---|---|
| a. job_key join | v3 tra theo `job_id`; không `job_id` thì list job của session; ID lạ → `JOB_NOT_FOUND` | Thêm đường gọi lại cùng args → join job cũ | Additive, giữ cả hai đường |
| b. result.json atomic | write-ahead (MUST) | Cách thực hiện: tmp + `os.replace` | Tương thích |
| c. reconcile boot | running của generation cũ → `unknown` (boot_epoch) | `result.json` parse được → `done`; không thì giữ `unknown` | Tương thích có điều kiện |
| d. eligibility `mutation_scope` | v3 không nói | Whitelist `mutation_scope ∈ {none, derived_index}` | Additive |
| e. finally không chặn stub | v3 không nói | Stub đi qua `finally` không await (step 1.2) | Additive, bắt buộc để SLO6 đạt |
| f. tra job ở funnel | v3 không nói | Tra index job trước khi chạy tool (step 3.3b), độc lập call_cache | Additive, bắt buộc cho ca Codo |

- Xong khi: 6/6 ô có trích dẫn §x thật từ IMPLEMENTATION-BRIEF; agent audit xác nhận.

**Step 3.1 — Eligibility**
1. Việc làm: chỉ tool có `mutation_scope ∈ {none, derived_index}` được chuyển async. Loại trừ tường minh tool có side effect phải chạy mỗi lần: `read_file_range` (seed read-token, E7) không bao giờ là job. Danh sách loại trừ lập ở bước này, mỗi mục kèm lý do. Với `derived_index`: `job_key` chứa graph generation, và hai job reindex không chạy song song trên cùng `graph.db` (singleflight theo project).
2. Kiểm tra: unit test duyệt toàn registry; mọi tool `mutation_scope` khác bị từ chối chuyển async.
3. Audit: tìm tool khai `none` mà thực tế ghi file hoặc DB.
4. Real test: không áp dụng.
5. Xong khi: test PASS; Anh xem danh sách whitelist.
6. Rollback: tắt `ORCH_READ_JOBS`.

**Step 3.2 — Soft deadline + stub**
1. Việc làm: tại funnel E1, chạy tool dưới `asyncio.shield` + `wait_for(soft_deadline)` (§8A.1). Quá hạn thì trả stub `{job_id, job_state:"running", output_path, ca_next_action}`, task chạy tiếp. Bên đến sau đang poll singleflight cũng nhận stub tại soft deadline, không poll tới hết `CALL_SINGLEFLIGHT_WAIT_S`.
2. Kiểm tra: tool giả chạy 2 × soft → stub về ≤ soft + 1 s **trong lúc H2 giữ khóa**; task chạy tới khi xong.
3. Audit: tool xong trước hạn thì response giống hệt HEAD; shield không rò task khi shutdown; stub đi qua `finally` không await (1.2).
4. Real test: H1 30 agent + H2 + tool giả.
5. Xong khi: 100% stub ≤ soft + 1 s (SLO6 phần 1).
6. Rollback: tắt flag.

**Step 3.3 — job_key = call_signature + source_version (mở rộng call_cache)**
1. Việc làm: một hàm key duy nhất `job_key = call_signature(tool, params) + source_version`, dùng chung cho call_cache (tool trong `CACHEABLE_TOOLS`) và folder job (mọi tool đủ điều kiện ở 3.1, kể cả tool **không** cacheable). Join không dựa vào call_cache, vì tool không cacheable không có tầng RAM (E28); join đi qua index job ở step 3.3b.
   - `source_version`: graph generation với cbm;
   - HEAD + dirty-hash với ocr;
   - mtime + size + hash file với read/search.
2. Kiểm tra: 5 lần gọi cùng args khi job đang chạy → tool chạy thật 1 lần; sửa file nguồn → job mới; args khác thứ tự key → cùng job_key.
3. Audit: không có hàm key thứ hai; `fields=` xử lý như 2.5; agent khác cùng project join được.
4. Real test: kịch bản ở mục 2.
5. Xong khi: đạt kiểm tra.
6. Rollback: tắt flag.

**Step 3.3b — Tra job ở funnel trước khi chạy tool**
1. Việc làm: tại funnel E1, với tool đủ điều kiện (3.1) khi `ORCH_READ_JOBS` bật, **trước khi chạy tool** tính `job_key` và tra index job (index RAM của folder job, nạp lại khi boot):
   - có job `created`/`running` cùng key → không chạy lại; chờ tới soft deadline rồi trả stub với **đúng `job_id` cũ** (hoặc trả kết quả nếu job xong trước soft);
   - có job `done` cùng key, còn TTL → trả `result.json`;
   - không có → tạo job (3.4) rồi chạy;
   - tạo job dùng compare-and-set trên index (lock theo `job_key`), để 2 caller đến cùng lúc không tạo 2 job.
2. Kiểm tra: 10 caller (agent khác nhau) cùng args gọi đồng thời → 1 folder job, tool chạy thật 1 lần, 10 response cùng `job_id`.
3. Audit: tra index không chạm `sessions.db`; index có trần bộ nhớ; `source_version` khác → job mới.
4. Real test: H4 + 10 caller đồng thời.
5. Xong khi: đạt kiểm tra.
6. Rollback: tắt flag.

**Step 3.4 — Folder job: meta.json write-ahead + result.json atomic**
1. Việc làm: mỗi job một folder.
   - Ghi `meta.json` (`job_state:"created"`, `boot_epoch`=generation, `job_key`, `tool_name`, `created_at`) trước khi chạy; chuyển `running` khi task bắt đầu. Giữ đủ tập trạng thái v3: `created|running|done|failed|unknown`.
   - Xong thì ghi `result.json` qua tmp + `os.replace`, rồi mới cập nhật meta thành `done` hoặc `failed`.
   - `result.json` = đúng output của bản chạy đồng bộ, kể cả error envelope.
   - Dùng một tên trường `job_state` ở cả meta lẫn API.
2. Kiểm tra: kill ở 3 thời điểm (sau mkdir; sau meta; sau result trước khi cập nhật meta) → không bao giờ có `result.json` ghi dở.
3. Audit: thư mục job nằm trong cache của [A], không nằm trong project của CA.
4. Real test: H3 ở 3 thời điểm.
5. Xong khi: 3/3 PASS.
6. Rollback: tắt flag. Folder cũ dọn theo G2.

**Step 3.5 — Reconcile lúc boot**
1. Việc làm: khi boot, record `created` hoặc `running` của generation cũ → `done` nếu `result.json` parse được, không thì `unknown` (theo v3); folder thiếu meta → `unknown`. Không tự chạy lại.
2. Kiểm tra: H3 ở từng crash window của 3.4 → trạng thái sau boot đúng.
3. Audit: reconcile có trần thời gian; 10,000 folder không làm boot vượt ngưỡng `startup_recovery`.
4. Real test: như kiểm tra.
5. Xong khi: đúng mọi nhánh; boot với 10,000 folder dưới ngưỡng.
6. Rollback: tắt flag.

**Step 3.6 — Push `async_done`**
1. Việc làm: job xong thì đính `async_done:[{job_id, tool, output_path}]` vào response kế tiếp của cùng agent/session.
2. Kiểm tra: H4 → lời gọi kế tiếp nhận push đúng 1 lần.
3. Audit: push không đến nhầm agent; mất push không mất dữ liệu (gọi lại cùng args vẫn lấy được).
4. Real test: H4.
5. Xong khi: đạt kiểm tra.
6. Rollback: tắt flag.

**Step 3.7 — Tool `job_status` (O4)**
1. Việc làm: implement theo spec v3 (contract, trạng thái, `JOB_NOT_FOUND`, rate-limit 3 lớp: per-job 1/30 s, per-principal 20/60 s, per-session listing 1/60 s, theo IMPLEMENTATION-BRIEF). Đăng ký registry và schema, chạy `audit_tool_matrix` (skill `21_tool_matrix`).
2. Kiểm tra: contract test cho `running`, `done`, `failed`, `unknown`, `JOB_NOT_FOUND`, 429.
3. Audit: `job_status` không bao giờ bị chuyển async; `audit_tool_matrix` 0 drift.
4. Real test: contract test trên sandbox.
5. Xong khi: đạt kiểm tra và audit.
6. Rollback: tắt flag, tool trả `FEATURE_DISABLED`.

**Step 3.8 — Chống poll**
1. Việc làm: gọi lại khi job đang chạy chỉ join, không tốn thêm tài nguyên. Response mang `ca_next_action` chuẩn của v3. Quá 3 lần thì `poll_warning`. Bộ đếm nằm trong RAM.
2. Kiểm tra: poll 10 lần → CPU và IO không tăng; `poll_warning` từ lần 4.
3. Audit: không ghi đĩa mỗi lần poll.
4. Real test: như kiểm tra.
5. Xong khi: đạt kiểm tra.
6. Rollback: tắt flag.

**Step 3.9 — Sweeper folder job**
1. Việc làm: TTL 24 h + trần 2 GB (§8A.4). Không xóa job `running`. Lần đầu chạy dry_run.
2. Kiểm tra: dry_run liệt kê đúng tập sẽ xóa.
3. Audit: chỉ xóa đúng danh sách dry_run đã duyệt (G2); folder `running` không bao giờ nằm trong danh sách.
4. Real test: tạo 100 job giả với tuổi khác nhau.
5. Xong khi: lần chạy thật xóa đúng danh sách.
6. Rollback: tắt sweeper.

**Step 3.10 — Mô tả tool cho CA**
1. Việc làm: cập nhật description của tool trong whitelist: có thể nhận stub, gọi lại đúng args để lấy kết quả, không poll.
2. Kiểm tra: `GET /api/tools/<tool>/schema` trả mô tả mới.
3. Audit: đổi rule/bootstrap của CA là task riêng (skill `rule_skill_changer`), không gộp vào đây.
4. Real test: không áp dụng.
5. Xong khi: đạt kiểm tra.
6. Rollback: revert commit.

**REAL TEST PHASE 3 — Tái hiện ca Codo**
1. Kịch bản A (session chết): tool giả khai `mutation_scope:none` và **không** nằm trong `CACHEABLE_TOOLS` (chứng minh join không phụ thuộc call_cache), chạy 3 × soft, nhận stub, client đóng socket. Client mới (agent khác, session khác) gọi lại đúng args.
2. Kịch bản B (restart): đang có job thì H3 restart, sau boot gọi lại đúng args.
3. Kịch bản C (mất output): client timeout trước soft deadline, rồi gọi một tool khác.
4. PASS khi:
   - A: client mới nhận kết quả, tool chạy thật 1 lần;
   - B: meta đúng thực tế; gọi lại thì dùng result cũ (nếu `done`) hoặc chạy mới đúng 1 lần;
   - C: lời gọi kế tiếp nhận `async_done`;
   - với 50 agent nền, SLO1, SLO2, SLO4 vẫn đạt.
5. Gate G3.

---

### PHASE 4 — Process pool cho tool nặng CPU (dùng được 28 luồng)

**Step 4.1 — Profile**
1. Việc làm: dưới H1 50 agent, đo CPU time và wall-clock của `ocr_*`, `cbm_*`, search lớn.
2. Kiểm tra: bảng theo tool (ms).
3. Audit: chỉ chọn tool có CPU thuần Python lớn hơn IO.
4. Real test: chính là phép đo.
5. Xong khi: có danh sách tool kèm số đo.
6. Rollback: không áp dụng.

**Step 4.2 — Process pool (`ORCH_PROCPOOL_TOOLS`)**
1. Việc làm: chạy các tool trong danh sách bằng `ProcessPoolExecutor`. Args hoặc kết quả không pickle được thì chạy thread như cũ.
2. Kiểm tra: cùng input chạy thread và pool → output giống hệt byte-by-byte.
3. Audit:
   - process con không mở connection ghi tới `sessions.db` hoặc `graph.db`. Reindex `graph.db` (tool `derived_index`) chạy ở process cha, tuần tự theo project; process con chỉ đọc `graph.db` (`mode=ro`);
   - process con không kế thừa env nhạy cảm;
   - process con chết → lỗi rõ, không treo call;
   - tool `derived_index` vẫn giữ quy tắc 3.1.
4. Real test: H1 50 agent, 20 agent chủ yếu cbm/ocr.
5. Xong khi: output giống hệt; không có call treo khi kill process con.
6. Rollback: tắt flag.

**Step 4.3 — Kích thước pool**
1. Việc làm: đo với 8, 16, 24 process.
2. Kiểm tra: CPU dùng > 1 core; read p95 không xấu đi.
3. Audit: RAM của pool có trần.
4. Real test: như 4.2 cho từng kích thước.
5. Xong khi: chọn kích thước có số đo.
6. Rollback: tắt flag.

**REAL TEST PHASE 4**
1. Kịch bản: 50 agent, 20 agent chủ yếu gọi cbm/ocr, 15 phút.
2. PASS khi: throughput cbm/ocr ≥ 3 × baseline 0.10; output không đổi; SLO1 vẫn đạt.
3. Gate G4.

---

### PHASE 5 — Dọn dẹp và quét tĩnh

**Step 5.1 — Quét await persist không giới hạn**
1. Việc làm: quét toàn bộ đường request/response: không còn `await` tới writer/ledger/read-token không có bound. Write được phép chờ có hạn (`ORCH_WRITE_AUDIT_WAIT_MS`). Ledger `begin` fsync spool là ghi bền bắt buộc, không phải chờ SQLite.
2. Kiểm tra: danh sách mọi await persist trên đường response, mỗi dòng ghi bound hoặc lý do miễn trừ.
3. Audit: agent audit tự quét độc lập rồi so hai danh sách.
4. Real test: không áp dụng.
5. Xong khi: 0 await không bound ngoài danh sách miễn trừ.
6. Rollback: không áp dụng.

**Step 5.2 — Code chết**
1. Việc làm: tìm code cũ không còn dùng sau Phase 1–4.
2. Kiểm tra: `cbm_dead_code`, cross-check `search_code(mode='callers')`.
3. Audit: liệt kê consumer trước khi xóa; xóa theo G2.
4. Real test: full suite sau khi xóa.
5. Xong khi: danh sách xóa được Anh duyệt và đã xóa; full suite 0 fail mới.
6. Rollback: revert commit.

**Step 5.3 — Lần xóa events đầu tiên (retention §8A.4)**
1. Việc làm: archive events > 30 ngày ra `events_YYYY-MM.jsonl.gz`, kiểm trên bản copy, rồi mới xóa khỏi DB live theo G2.
2. Kiểm tra: số dòng archive = số dòng sẽ xóa; archive giải nén đọc được.
3. Audit: không có DELETE trước khi archive verify xong.
4. Real test: trên bản copy DB.
5. Xong khi: Anh duyệt danh sách (turn 1) và lần xóa (turn 2) đúng danh sách.
6. Rollback: nạp lại từ archive.

**REAL TEST PHASE 5:** chạy lại toàn bộ real test Phase 0–4 trên sandbox → 100% PASS. Gate G5.

---

### PHASE 6 — Nghiệm thu cuối và bật dần trên [A] live

**Step 6.1 — Bộ test T1–T12 (§6) trên sandbox, tất cả flag ON.**
- Xong khi: 12/12 PASS.

**Step 6.2 — Bật flag dần trên [A] live**
1. Việc làm: bật theo nhóm, mỗi lần cần "tiến hành" + restart hợp lệ (G3):
   - (1) persistence: `ORCH_SPOOL_AUDIT`, `ORCH_SESSION_GATE_NONBLOCK`, `ORCH_READ_TOKEN_RAM`, `ORCH_LEDGER_SPOOL`, `ORCH_WRITE_AUDIT_WAIT_MS`, `ORCH_BREAKER_RETRY`;
   - (2) admission: `ORCH_ADMISSION_DEADLINE_S`, `ORCH_AGENT_CONCURRENCY`;
   - (3) lock: `ORCH_WRITE_LOCK_SCOPE=file`;
   - (4) job: `ORCH_READ_JOBS`, `ORCH_READ_JOB_SOFT_DEADLINE_S`;
   - (5) pool: `ORCH_PROCPOOL_TOOLS`.
2. Kiểm tra: sau mỗi nhóm, H5 chạy chỉ đọc trên log live 24 h, đếm orphan, contention, `lock_hold_ms`, 503, ledger fail.
3. Audit: số live ≥ mức sandbox; xấu đi thì tắt nhóm ngay.
4. Xong khi: 5 nhóm chạy 24 h không regression; `CALL_ORPHANED` bỏ phí trên live giảm ≥ 95% so với E16; ledger fail = 0.
5. Rollback: tắt nhóm gần nhất rồi restart.

**Step 6.3 — Báo cáo tổng kết:** trước/sau theo từng SLO, flag đang bật, việc còn mở (kể cả rủi ro D: là USB SSD). Anh duyệt.

---

## 6. Bộ test cuối (sandbox, tất cả phải PASS)

| ID | Kịch bản | PASS khi |
|---|---|---|
| T1 | H1 50 agent hỗn hợp, 30 phút | SLO1, SLO3, SLO4 |
| T2 | T1 + H2 giữ khóa 60 s ×3 và 120 s ×1 | SLO1 suốt cửa sổ khóa; write < 3 s; H6 100% ≤ 60 s; không call > 60 s |
| T3 | H8 ×50 | 0 lần 503; 100% thành công; `audit_pending` khi khóa; `write_status` cuối cùng `committed` |
| T4 | 1,000 read→write dưới H2 | 0 false conflict |
| T5 | H7 (A) 10 agent cùng 1 file | đúng 1 apply mỗi hash, còn lại conflict |
| T6 | H7 (B) 50 file, sau step 2.7 | SLO5 |
| T7 | H3 restart giữa read và write; giữa write apply và ledger end | conflict rõ ràng; `write_status` đúng; không mất update |
| T8 | 1 agent 100 call song song + 49 agent | 49 agent đạt SLO1 |
| T9 | Burst 200 agent 2 phút | SLO7, SLO2 |
| T10 | Ca Codo A/B/C (Phase 3) | SLO6; tool chạy thật 1 lần |
| T11 | 503 qua 4 harness thật | bảng hành vi đầy đủ; harness nuốt 503 đã có `ca_next_action` trong mô tả tool |
| T12 | Soak 2 h ở 30 agent, rồi tắt từng flag | RAM tăng < 10%; WAL có trần; sweeper chạy; H6 100%; tắt từng flag → hành vi **của phần sau flag đó** về HEAD, full suite 0 fail mới. Không áp dụng cho phần không có flag: 1.1 (đo `lock_hold_ms`), 5.2 (xóa code chết), 5.3 (retention); các phần này rollback như ghi ở step |

---

## 7. Rủi ro và cách giảm

1. **Spool mất khi crash:** fsync theo lô (event) hoặc fsync từng bản ghi (ledger begin); replay khi boot; dedupe theo `audit_id`.
2. **Write apply hai lần do 503 sai chỗ:** quy tắc step 1.7 + H8 + T3.
3. **Read-token stale sau restart:** trả `FILE_CHANGED_OUTSIDE_ORCHESTRATOR` rõ ràng; CA `force_reseed`.
4. **Push `async_done` nhầm CA:** gắn agent_id + session; đường lấy lại bằng job_key không phụ thuộc push.
5. **Process con mở writer sessions.db:** cấm ở audit 4.2.
6. **Retention xóa nhầm:** archive trước, G2, whitelist.
7. **Test tải làm bẩn live:** sandbox + hằng chặn trong harness + bằng chứng `sbx-` = 0 dòng trên live.
8. **D: là USB SSD:** rút cáp lúc đang ghi có thể hỏng DB. Ghi trong báo cáo cuối; chuyển DB chỉ theo §8A.2.
9. **Holder UNRESOLVED tái phát:** Phase 1 làm request path không phụ thuộc holder; instrumentation 0.6 tiếp tục bắt.

---

## 8. Quyết định

### 8A. Đã chốt
1. **Soft deadline B1** = clamp(timeout client nhỏ nhất (0.9) − 5 s, 10 s, 25 s); env `ORCH_READ_JOB_SOFT_DEADLINE_S`. **Nguồn chốt:** Anh ủy quyền ClauLTN quyết các mục §8 ngày 2026-10-12 ("trả lời luôn, tôi không thể duyệt"). Mục này thay đề xuất v3 "B1 = 25 s, NOT yet ratified" (IMPLEMENTATION-BRIEF §5). Công thức cho kết quả ≤ 25 s, nên không vượt đề xuất v3; chỉ thấp hơn khi có harness timeout ngắn. Muse sửa dòng B1 trong IMPLEMENTATION-BRIEF trỏ về mục này để không có hai SOT.
2. **Không chuyển DB.** Chỉ chuyển sang NVMe khi (a) Real test Phase 1 FAIL SLO **và** (b) step 0.8 cho COMMIT p95 trên D: ≥ 3 × NVMe.
3. **`ORCH_AGENT_CONCURRENCY` = 8**; vượt mức thì xếp hàng theo agent, chỉ 503 khi quá deadline.
4. **Retention:** events giữ 30 ngày, cũ hơn → archive `events_YYYY-MM.jsonl.gz` rồi mới xóa. Folder job TTL 24 h + trần 2 GB, không xóa `running`. Lần xóa events đầu tiên theo G2.
5. **Không tách DB.** Chỉ tách bảng `events` khi step 1.10 chứng minh tranh chấp khác vai > 500 ms sau Phase 1.

### 8B. Constraint đã chốt (ClauLTN theo ủy quyền của Anh, 2026-10-12) — nhắc lại, không cần duyệt lại
1. Phase 1 + 2 trước, Phase 3 sau (P-C5).
2. Không tăng `busy_timeout` (G10).
3. Eligibility theo `mutation_scope ∈ {none, derived_index}`, kèm điều kiện graph generation trong job_key và không chạy song song hai job reindex trên cùng `graph.db`.
4. Không test trên DB live.
5. Không dùng cơ chế council.

---

## 9. Gate

| Gate | Điều kiện |
|---|---|
| G0 | Real test Phase 0 PASS (tái hiện incident) + instrumentation 0.6 live + 0.7 kết luận (a) hoặc UNRESOLVED. UNRESOLVED không chặn Phase 1. Anh duyệt. |
| G1 | Real test Phase 1 PASS + full suite 0 fail mới. Anh duyệt. |
| G2 | Real test Phase 2 PASS. Anh duyệt. |
| G3 | Real test Phase 3 PASS (ca Codo). Anh duyệt. |
| G4 | Real test Phase 4 PASS. Anh duyệt. |
| G5 | Real test Phase 5 PASS. Anh duyệt. |
| G6 | T1–T12 PASS + 5 nhóm flag 24 h live không regression + báo cáo tổng kết. Anh duyệt. |

(Gate G0–G6 ở §9 khác guardrail G1–G10 ở §3.5: gate viết dạng "Gate Gx", guardrail viết dạng "guardrail Gx".)

---

## 10. Thay đổi so với V4

1. §2 thay toàn bộ bằng chứng: bỏ các mục không có nguồn của V4 (E9 "14,934 call" thực ra là số dòng code; E2 "4 CA"; E4/E7/E8 chuỗi log có 0 dòng; E1 write 10 MB/120 s chưa kiểm). Mỗi bằng chứng kèm lệnh kiểm lại.
2. Ledger: write-ahead fsync vào spool trước khi write chạy (step 1.6), không còn "không await".
3. Quy tắc write đã apply thì không bao giờ 503 (step 1.7) + harness H8 + T3.
4. Khôi phục Phase 4 process pool; dọn dẹp chuyển sang Phase 5.
5. Khôi phục harness H1–H8 có định nghĩa và self-test; H2 chỉ có một nghĩa (giữ khóa).
6. Khôi phục sandbox cô lập + 3 bằng chứng + hằng chặn live trong harness.
7. Khôi phục checklist audit A1–A8, thêm A9 (reality) và A10 (implementer).
8. Step 0.8 đo cả D: và NVMe.
9. Real test Phase 0 dùng H1 30 agent + H2, không còn "burst 4 CA".
10. SLO7 định nghĩa rõ "lỗi" (503 đếm riêng). Bỏ T9 cũ (batch 10 call) không khớp SLO3.
11. Bỏ step 1.1 cũ trùng 1.2. Instrumentation tìm holder thành step 0.6 có build, audit, rollback.
12. Thêm step 0.4 (timestamp theo pha) thành code quan sát sau flag.
13. Thêm delta e (finally không chặn stub) vào bảng Phase 3.
14. Khóa project + khóa file thành mô hình reader-writer rõ ràng (step 2.7).
15. Tách tên gate (G0–G6, §9) khỏi tên guardrail (G1–G10, §3.5).

---

## 11. Yêu cầu cho vòng audit plan của muse (trước Phase 0)

1. **SOT = file này.** Không dựng lại bản khác làm SOT.
2. **Reality lane:** một agent chạy lại lệnh kiểm của mọi bằng chứng §2 và mọi số trong step. Lệch thì báo.
3. **Engineering lane:** một agent đóng vai người implement, trả lời cho mỗi phase: "làm đúng plan này thì hệ thống hỏng ở đâu?"
4. **Regression lane:** một agent so file này với `GATE-REPORT.md` và spec v3. Mục nào trong nguồn bị rơi mà không ghi lý do thì báo.
5. Finding trình bày (đánh số, ref treo) xếp sau finding kỹ thuật. Không đóng vòng audit khi engineering lane còn finding mở.

---

## 12. Thay đổi V5 → V6 (fix `V5-AUDIT-VERDICT.md`)

| Finding | Kết luận ClauLTN | Sửa ở |
|---|---|---|
| E2 HIGH — bất biến "đã apply thì không 503" không có cơ chế | Đúng | Step 0.11 (bảng điểm apply) + step 1.7 (`post_apply_guard`, fault-injection mọi write tool, test registry) |
| E8 HIGH — "khóa file = shared" cho 2 write cùng file chạy song song | Đúng (câu chữ sai) | Step 2.7: khóa project RW + mutex file luôn exclusive, thứ tự lấy cố định |
| E9 HIGH — check 2.5 dùng `read_file_range` không cacheable | Đúng (đã kiểm `call_cache.py:40–46`) | E28; step 2.5 dùng tool cacheable, cấm thêm `read_file_range` vào `CACHEABLE_TOOLS` |
| E14 HIGH — join ca Codo dựa vào call_cache | Đúng | Step 3.1 loại `read_file_range`; 3.3 tách khỏi call_cache; thêm step 3.3b tra index job ở funnel; delta f; real test A dùng tool không cacheable |
| G1 — B1 "đã chốt" trong khi v3 ghi chưa ratify | Đúng về nguồn; quyết định giữ | §8A.1 ghi nguồn chốt (Anh ủy quyền), quan hệ với đề xuất 25 s của v3, yêu cầu sửa IMPLEMENTATION-BRIEF |
| E3 — SLO4 60 s "sau khi gọi" mâu thuẫn real test giữ khóa 120 s | Đúng | SLO4 tính từ max(lúc gọi, lúc nhả khóa) |
| E4 — torn line ledger trong spool chung | Đúng | Ledger spool riêng, fsync từng record; quy tắc torn line + reconcile hash ở 1.3/1.6 |
| E20 — process con quên `graph.db` | Đúng | Step 4.2: con chỉ đọc `graph.db`, reindex ở process cha |
| E23 — T12 "tắt flag về HEAD" với phần không có flag | Đúng | T12 giới hạn phạm vi, liệt kê 1.1/5.2/5.3 |
| G2 — mất `job_state: created` | Đúng | Step 3.3b/3.4/3.5 giữ đủ trạng thái v3 |
| R1–R4 — số log/DB lệch | Đúng, do log rotate 21:09:27 và đo sớm | §2 quy ước `LOG` = `orchestrator.log.1`, số cả ngày 2,626/683; E25 ghi thời điểm đo |
| Caveat "GATE-REPORT SHA ≠ pin 13a3ab45" | Audit hiểu sai: `13a3ab45` là commit ToolA (code SOT), không phải SHA file | Header V6 ghi rõ; muse pin SHA file report riêng |
| 9 MEDIUM/LOW còn lại trong engineering-lane-report | Chưa xem (file chỉ có trong working files của campaign plan-v5-audit) | Muse xử lý theo §11; finding nào đổi logic thì báo ClauLTN |
