# Plan TOOLA_1210 — Concurrency & Long-Read Survival

> Nguồn: Claude (Opus 5.5) chuyển qua user, 2026-10-12.
> Trạng thái: NHÁP — chờ duyệt các gate G0–G5 và quyết định §8.
> Liên quan: museR congestion-rootcause audit (campaign congestion-rootcause-20261012, SOT pin `13a3ab45`) đang chạy — S0.1/S0.2 sẽ dùng kết quả của nó.
> Ghi chú người ghi file: chỉ sửa các ô bảng bị lỗi paste cho đọc được, không đổi nội dung.

## 0. Mục tiêu và định nghĩa xong

Đích là 50 CA dùng ToolA đồng thời mà không nghẽn hàng loạt và không mất output của các read dài.

| Chỉ tiêu (SLO) | Ngưỡng |
|---|---|
| read_file_range p95 / p99 khi có 50 CA | < 2 s / < 5 s |
| CALL_ORPHANED do phải chờ hàng | 0 (lời gọi quá hạn phải fast-fail, không làm xong rồi bỏ phí) |
| Throughput sụt đột ngột khi tải không đổi | không có phút nào sụt > 80% |
| Audit đầy đủ | 100% lời gọi có đúng 1 event theo audit_id, chậm nhất 60 s sau khi gọi |
| Write | 0 mất, 0 apply 2 lần; cùng file thì chạy tuần tự, khác file thì song song |
| Read dài hơn soft deadline | trả stub trước soft + 1 s; session chết hay restart vẫn lấy lại được kết quả |

## 1. Bằng chứng đã kiểm (SOT cho plan này)

| # | Sự kiện | Nguồn |
|---|---|---|
| E1 | Funnel dispatch `_dispatch_tool_call_request` | orchestrator_server.py:14934 |
| E2 | write_status: handler, tool, route | orchestrator_server.py:18710, :18849, :19042 |
| E3 | Một writer chung cho cả tiến trình; durable thì chờ COMMIT | orchestrator/session/store.py:1014 `_run_session_write` |
| E4 | Ghi event dùng INSERT OR IGNORE theo audit_id (đã idempotent); khi contention thì raise | store.py:2259–2295 |
| E5 | Lane mặc định: read_io 12/48, search 8/24; chỉ chờ chỗ 2 s; override bằng env ORCH_EXEC_\<LANE\>_WORKERS/QUEUE/QUEUE_WAIT_SECONDS | orchestrator/server/executor_lanes.py:272–292 |
| E6 | Log 2026-10-12: 2,178 dòng store contention, 532 CALL_ORPHANED (458 là read_file_range), đỉnh 162 call/phút, có read chờ 211 s | orchestrator.log |
| E7 | sessions.db 338 MB, durable_audit.db 361 MB, nằm trên D: (SSD USB T7) | .orchestrator_cache/ |
| E8 | Python 3.12 bản có GIL (venv312); máy 28 luồng | /api/config |
| E9 | Đã có singleflight: call_cache_inflight, call_cache_joins_waited_total | /health |
| E10 | Chưa có job_status, async_status, boot_epoch trong code | search_code: 0 hit |

Chưa kiểm chứng (Sprint 0 phải trả lời):
- Read tool ghi event với durable=True hay không.
- Cái gì giữ khóa ghi trong 10 cửa sổ nghẽn.
- Thời gian fsync trên D:.
- Có chạy được một [A] cô lập (port và cache riêng) để test tải hay không.
- Timeout client nhỏ nhất của từng harness.

## 2. Vai trò

| Vai | Việc |
|---|---|
| Anh | Duyệt các gate G0–G5 và các quyết định ở §8 |
| Leader | Viết spec, chấm gate, commit; không tự viết diff |
| muse | Scan, audit đối kháng mỗi diff, phản biện thiết kế |
| Codo | Viết code production |
| k3 / scanner | Viết harness, đo, chỉ báo cáo (không sửa code production) |

## 3. Guardrails (bắt buộc ở mọi sprint)

1. Quyền. Mặc định T1. Mỗi sprint cần "tiến hành" riêng. Xóa dữ liệu (retention cho events, dọn folder job) là thao tác destructive: turn 1 liệt kê đích cụ thể, turn 2 mới làm.
2. Đường ghi. Chỉ ghi qua [A] (read_file_range → write_fix, write_new_file). Không ghi bằng shell.
3. Restart. Chỉ dùng restart_orchestrator_via_bat. Không restart khi còn worker đang chạy. Verify sau restart là /health có pid mới và generation đổi.
4. Feature flag. Mọi thay đổi hành vi đứng sau flag env, mặc định OFF, bật dần từng bước. Rollback bằng cách tắt flag rồi restart. Các flag:
   - ORCH_AUDIT_ASYNC_READS
   - ORCH_WRITE_AUDIT_WAIT_MS
   - ORCH_ADMISSION_DEADLINE
   - ORCH_AGENT_CONCURRENCY
   - ORCH_READ_JOBS
   - ORCH_PROCPOOL_TOOLS
5. Cách ly test tải. Load test chạy trên [A] cô lập (port khác, cache/DB khác), không bao giờ chạy vào DB live. Nếu S0.6 cho thấy không cô lập được thì STOP và hỏi Anh xin cửa sổ test.
6. Baseline là git HEAD. Mỗi sprint chạy full suite trước và sau; so sánh theo id test.
7. Không tin self-report. Mỗi kết quả phải kèm lệnh đã chạy và output. Mỗi test mới cần control GREEN, và mutant phải làm test RED.
8. Bất biến audit (A20/A33). Không bao giờ ghi event_id giả. Outbox kiểu at-least-once, dedupe bằng audit_id (E4).
9. Lock theo file. Hai CA ghi cùng file thì có đúng một bên apply, bên kia nhận conflict kèm hash mới.
10. Không di chuyển DB sang ổ khác khi Anh chưa duyệt (§8).

## 4. Harness (k3 viết ở S0, dùng ở mọi sprint)

| ID | Harness | Mô tả |
|---|---|---|
| H1 | load_replay.py | N agent ảo, phát lại hỗn hợp tool và args lấy từ USAGE_LOG.jsonl, có think-time, timeout client theo profile harness (vd Codex 30 s). Xuất p50/p95/p99 theo tool và tổng số call mỗi phút. |
| H2 | lock_inject.py | Mở kết nối riêng tới DB test, BEGIN IMMEDIATE, giữ X giây. Dùng để tái tạo nghẽn. |
| H3 | crash_inject | Restart [A] cô lập khi job đang chạy. |
| H4 | session_death | Client đóng socket giữa chừng, rồi một client khác gọi lại cùng args. |
| H5 | metrics_tap | Poll /health mỗi 1 s (executor_\*, durable_audit_\*, call_cache_\*). Đếm CALL_ORPHANED, store contention, SERVER_BUSY trong log. |
| H6 | audit_reconcile | Đối chiếu audit_id đã phát với event trong DB: thiếu, trùng, giả. |
| H7 | write_race | Kịch bản A: 10 agent cùng ghi 1 file. Kịch bản B: 50 agent ghi 50 file khác nhau. |

Mọi harness nằm trong thư mục riêng ngoài cây production và nhận path qua tham số.

## 5. Sprints

### S0. Đo đạc và truy gốc (chỉ đọc, cộng thêm harness)

| Step | Việc | Owner | Output |
|---|---|---|---|
| S0.1 | Trace từ E1 tới E3: read tool ghi event ở đâu, durable bằng gì, response có chờ COMMIT không | muse | Call chain kèm file:line |
| S0.2 | Đối chiếu 10 cửa sổ nghẽn (E6) với WAL checkpoint, durable_audit worker, transaction dài, tool đang chạy | muse | Tên cái giữ khóa + bằng chứng log |
| S0.3 | Đo fsync và COMMIT latency trên D: so với ổ NVMe nội bộ (dùng DB tạm) | k3 | Bảng ms |
| S0.4 | Đo timeout client nhỏ nhất của từng harness (Codex, Claude Code, opencode, zcode...) | k3 | Bảng; là input cho B1 |
| S0.5 | Viết H1–H7 | k3 | Code harness + README |
| S0.6 | Dựng [A] cô lập (port, cache, DB riêng); chứng minh không ghi vào DB live | k3 + muse | Lệnh chạy + hash DB live trước và sau không đổi |
| S0.7 | Baseline: H1 với 10/30/50 agent trên HEAD, rồi H2 giữ khóa 60 s | k3 | Số baseline |

**G0:** cái giữ khóa có tên kèm bằng chứng; H2 tái tạo được kiểu nghẽn ở E6; có số baseline. Anh duyệt.

### S1. Tách audit khỏi đường response (P0 + P1)

| Step | Việc | Verify |
|---|---|---|
| S1.1 | Đo thời gian giữ khóa mỗi transaction trong session writer; log transaction > 500 ms (luôn bật, không qua flag) | Log có lock_hold_ms |
| S1.2 | ORCH_AUDIT_ASYNC_READS: tool có mutating:false ghi event vào spool jsonl append-only, rồi trả kết quả ngay; drainer nền ghi theo lô vào SQLite | H2 giữ khóa 60 s → read p95 < 2 s |
| S1.3 | Drainer: group commit N event mỗi 50–100 ms, INSERT OR IGNORE theo audit_id; sau restart thì replay spool | H6: 0 thiếu, 0 trùng sau khi drain |
| S1.4 | ORCH_WRITE_AUDIT_WAIT_MS (mặc định 2000): write chờ ledger/audit có hạn; quá hạn trả audit_pending:true; write_status vẫn đúng | H2 + write: response < 3 s; write_status cuối cùng ra committed |
| S1.5 | Gỡ cái giữ khóa đã tìm ở S0.2 (checkpoint PASSIVE định kỳ, hoặc chia nhỏ transaction...) | 10 cửa sổ nghẽn không tái hiện dưới H1 với 50 agent |
| S1.6 | Đề xuất tách DB theo vai (audit / session state / write ledger). Chỉ làm sau khi Anh duyệt | Thiết kế + đánh giá rủi ro migration |
| S1.7 | Đề xuất retention cho events (destructive): chỉ liệt kê, chưa xóa | Danh sách + dung lượng thu hồi được |

**G1:** H2 + H1 với 50 agent đạt SLO cho read; H6 cho 100%; full suite không có fail mới so với HEAD.

### S2. Kiểm soát lưu lượng (admission control) và chia công bằng

| Step | Việc | Verify |
|---|---|---|
| S2.1 | Nhận client_deadline_ms (header hoặc field); nếu thiếu thì lấy mặc định theo profile harness (S0.4) | Unit test |
| S2.2 | ORCH_ADMISSION_DEADLINE: lời gọi còn trong hàng mà đã quá deadline thì trả 503 SERVER_BUSY + retry_after_s + ca_next_action; không bắt đầu chạy | H1 burst 200 → 0 orphan có công bỏ phí |
| S2.3 | ORCH_AGENT_CONCURRENCY (mặc định 6): giới hạn số call đồng thời theo agent_id; hàng chờ chia công bằng giữa các agent | Kịch bản T6 |
| S2.4 | Chỉnh lane theo số đo S0 (E5 env); ghi số đã chọn vào config, không đặt cứng | /health rejection_counts ~0 với 50 agent |
| S2.5 | Kiểm tra singleflight (E9) gộp được các read trùng args giữa các agent | call_cache_joins_waited_total tăng trong H1 |

**G2:** T6 và T7 đạt (§6).

### S3. Job cho read dài (thiết kế đã chốt ở các turn trước)

| Step | Việc | Verify |
|---|---|---|
| S3.1 | Whitelist các tool có mutating:false trong schema; write không bao giờ được chuyển sang async | Unit test trên registry |
| S3.2 | Tại funnel E1: asyncio.shield + wait_for(soft_deadline) (B1); quá hạn thì trả stub {job_id, job_state:"running", output_path, ca_next_action} | T3 |
| S3.3 | job_key = hash(tool, args chuẩn hóa, project_path, source_version); gọi lại cùng key thì join job đang chạy, hoặc trả result.json nếu đã xong và còn mới | Đếm số lần chạy thật = 1 |
| S3.4 | Mỗi job một folder: ghi meta.json (running, generation) trước khi chạy; ghi result.json atomic (tmp + os.replace), rồi mới cập nhật meta thành done | Kill ở từng crash window → trạng thái đúng |
| S3.5 | Lúc boot reconcile: record running của generation cũ → done nếu result.json parse được, ngược lại interrupted; folder thiếu meta → unknown. Không tự chạy lại | T4 |
| S3.6 | Push async_done:[{job_id, tool, output_path}] vào response kế tiếp của cùng agent/session (chỉ là tiện ích) | H4 |
| S3.7 | Tool job_status(job_id) để kéo trạng thái, kiểu write_status; lỗi trả nguyên envelope JSON | Contract test |
| S3.8 | Anti-poll: lời gọi lặp lại chỉ join job, không tốn thêm tài nguyên; quá 3 lần thì gắn cờ poll_warning | Unit test |
| S3.9 | Sweeper TTL + giới hạn dung lượng cho folder job (destructive → policy do Anh duyệt) | Soak |

**G3:** T3, T4 và H4 đạt; không job nào chạy 2 lần cho cùng key.

### S4. Process pool cho tool nặng CPU

| Step | Việc | Verify |
|---|---|---|
| S4.1 | Lập danh sách tool nặng CPU (ocr_\*, cbm_\*, search lớn) dựa trên profile S0 | Bảng thời gian CPU |
| S4.2 | ORCH_PROCPOOL_TOOLS: chạy các tool đó trong ProcessPoolExecutor (hoặc worker process); args và kết quả phải picklable; giữ nguyên contract | So sánh output sync và pool giống hệt nhau |
| S4.3 | Kích thước pool theo số core (ví dụ 16); không làm nghẽn event loop | Dưới H1, CPU dùng > 1 core; read p95 không xấu đi |

**G4:** throughput của cbm/ocr tăng ≥ 3 lần khi có 50 agent; output không đổi.

### S5. Nghiệm thu cuối (§6), rồi bật flag dần trên [A] live

Thứ tự bật: S1 → S2 → S3 → S4. Mỗi bước theo dõi 24 h bằng H5 ở chế độ chỉ đọc log live.

## 6. Bài test cuối (chạy trên [A] cô lập, tất cả phải PASS)

| ID | Kịch bản | PASS khi |
|---|---|---|
| T1 | 50 agent, tải hỗn hợp, 30 phút | Đạt mọi SLO ở §0 |
| T2 | T1 + H2 giữ khóa DB 60 s | Read p95 < 2 s suốt cửa sổ khóa; write trả về < 3 s kèm audit_pending; sau khi nhả khóa, H6 = 100% trong 60 s |
| T3 | Read dài hơn soft deadline, client ngắt kết nối, client mới gọi lại cùng args | Stub trả về ≤ soft + 1 s; lần gọi lại nhận kết quả; số lần chạy = 1 |
| T4 | Restart khi job đang chạy | Meta reconcile đúng (done/interrupted); gọi lại thì chạy mới đúng 1 lần |
| T5 | H7: cùng file × 10 agent; 50 file khác nhau | Cùng file: đúng 1 apply/hash, còn lại conflict. Khác file: 50/50 thành công, wall-clock ≈ write chậm nhất (không bị tuần tự hóa) |
| T6 | 1 agent bắn 100 call song song, 49 agent tải thường | 49 agent vẫn đạt SLO |
| T7 | Burst 200 agent | Fast-fail 503 có retry_after; 0 crash; 0 orphan có công bỏ phí; về lại SLO ≤ 60 s sau burst |
| T8 | Full suite: HEAD so với sau thay đổi | 0 fail mới (so theo id) |
| T9 | Soak 2 h ở 30 agent | RAM ổn định (tăng < 10%), WAL bị chặn trần, sweeper chạy, H6 = 100% |
| T10 | Tắt từng flag (rollback) | Hành vi quay về HEAD; test vẫn pass |

## 7. Rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| Spool audit mất khi crash | fsync spool theo lô; replay khi boot; dedupe theo audit_id |
| Args không picklable khi chạy process pool | Whitelist từng tool; nếu không pickle được thì chạy thread như cũ |
| Push async_done đến nhầm CA | Gắn theo agent_id + session; lấy lại bằng job_key thì không phụ thuộc chuyện này |
| Retention xóa nhầm event | Thủ tục destructive 2 turn, có dry_run |

## 8. Quyết định chờ Anh

1. B1 soft deadline: em đề xuất = min(timeout client từ S0.4) − 5 s.
2. Chuyển DB sang NVMe nội bộ: quyết sau khi có số đo S0.3.
3. ORCH_AGENT_CONCURRENCY: đề xuất = 6.
4. Retention cho events và TTL job: đề xuất 7 ngày / 24 h, theo thủ tục destructive riêng.
5. Tách DB theo vai (S1.6): có làm hay không.
