# VERDICT ĐÓNG G0 — Camera truy gốc nghẽn ToolA (vụ 12/10)

- Ngày đóng: 2026-10-24. Người dùng duyệt hướng: "Chốt verdict G0 + kiểm lại plan theo code hiện tại" (08:22 +07).
- Căn cứ: GATE-REPORT 2026-10-12 (`congestion-rootcause-20261012/GATE-REPORT.md`), Plan TOOLA_1210 bản A′ (gắn camera G0, quan sát 72h/R8), dữ liệu camera đo trực tiếp từ `orchestrator.log` của daemon live.

## 1. G0 yêu cầu gì

GATE-REPORT §5–§6: danh tính kẻ giữ write-lock của `sessions.db` trong vụ nghẽn 12/10 (P-H) là UNRESOLVED — 6/6 agent không xác định được bằng phân tích tĩnh. Plan lập bước G0 = gắn instrumentation 2 lớp lên live để bắt quả tang ở vụ nghẽn thật kế tiếp:

- Lớp trong process (camera B1): bọc mọi `sqlite3.connect` tới sessions.db/durable_audit.db; watchdog thread kiểm tra connection nào mở transaction >2s thì ghi nhận (LOCK_HOLDER_TRACE) + PHASE_TIMING cho mọi call.
- Quy tắc đóng đã định trước (§6): *"Nếu sau một đợt nghẽn thật mà vẫn không ra holder → G0 đóng với kết luận UNRESOLVED, không chặn Phase 1."*

## 2. Dữ liệu quan sát (đo, không suy)

1. **Camera sống liên tục và tự khai báo mỗi lần boot** — dòng mới nhất xác nhận: `2026-10-24 01:09:22 INFO LOCK_HOLDER_TRACE enabled: sqlite3.connect wrapped for sessions.db/durable_audit.db` (trong chính lần restart đưa bản fix livez lên live). Tổng tích lũy trong `orchestrator.log` hiện tại: LOCK_HOLDER_TRACE 702 dòng, PHASE_TIMING 3.737 dòng (đo 07:50 24/10).
2. **Cửa sổ quan sát R8 (72h, kết ~19/10):** camera bắt được các vụ giữ lock dài rời rạc (9 vụ long-hold tính tới 17/10), **không** có đợt sóng nghẽn lớn trong cửa sổ.
3. **Vụ nghẽn thật dưới camera — 23/10 ~15:00–15:29:** đợt nghẽn nặng xảy ra khi camera đang chạy: LOCK_HOLDER_TRACE ×119 trong ~25 phút, SessionWriter STALLED ×22 / FAILED ×5, call kéo dài 74–199s (số liệu điều tra cùng ngày). Đây chính là "đợt nghẽn thật" mà quy tắc đóng G0 chờ.
4. **Định danh holder: vẫn trống.** Dòng camera mẫu đo 24/10 08:07–08:08: `LOCK_HOLDER_TRACE txn_open>2s db=…sessions.db thread=None tool=None age=3.0s` — cơ chế ghi nhận thread/tool là best-effort và thường trả None (đã ghi nhận từ 17/10; mục cải tiến camera `_short_stack→sys._current_frames` trong TOOLA_1510 vẫn chưa làm). Camera chứng minh "có kẻ giữ lock lâu" nhưng không gọi được tên.
5. **Sau restart 24/10 (01:09→08:04, ~7h):** 181 sự kiện (147 slow_stmt>2s + 25 txn_open>2s); theo DB: durable_audit.db 108, sessions.db 73; SessionWriter STALLED 2 / FAILED 2 rời rạc; watchdog mới không bắn (đúng thiết kế sau fix livez). Bài toán nghẽn còn nguyên ở mức nhẹ hơn, không tái diễn kịch bản 23/10 trong cửa sổ này.

## 3. Verdict

- **P-H (danh tính lock holder): UNRESOLVED — ĐÓNG theo đúng quy tắc §6 của GATE-REPORT.** Một đợt nghẽn thật đã xảy ra dưới camera (23/10) mà holder vẫn không được định danh; chờ thêm không đổi kết quả vì giới hạn nằm ở chính cơ chế định danh của camera, không nằm ở thiếu dữ kiện.
- **G0: ĐÓNG.** Điều kiện tiên quyết của Phase 1 được thỏa theo nhánh "đóng UNRESOLVED, không chặn" — plan đã ghi rõ Phase 1 (gỡ việc chờ khỏi response path) là đúng bất kể ai giữ khóa.
- **Di sản giữ lại:** (a) camera ở lại trên live vô thời hạn (đang chạy, chi phí thấp, tiếp tục cho số liệu tải); (b) mục cải tiến định danh camera (`_short_stack→sys._current_frames`, TOOLA_1510) giữ trạng thái MỞ dạng việc riêng — không còn là điều kiện chặn của bất kỳ phase nào.

## 4. Hệ quả cho Phase 1

Thứ tự plan giữ nguyên: **P0** persistence ra khỏi response path → **P1** ledger spool/writer có điều phối → **P2** circuit breaker + 503 tới được client → **P3** admission theo agent. Điểm thiết kế cần phán trước khi thi công P0: không dùng cờ "audit ghi sau" làm yếu bảo chứng ledger (bất biến A20/A33 — GATE-REPORT §6 + ghi chú coordinator 12/10). Phạm vi từng P theo kết quả re-pin ở §5.

## 5. Phụ lục re-pin theo code hiện tại (24/10/2026)

Phương pháp: 2 agent độc lập, mù, cùng brief — đối chiếu từng khẳng định C1–C8 của GATE-REPORT 12/10 với working tree hôm nay (HEAD `7d9d67c` + hunks chưa commit; daemon live chạy đúng cây này, gen `8e217cc459e6`). Hai bản hội tụ trên mọi điểm hệ trọng; chênh lệch duy nhất là lớp số liệu ở C6, coordinator tự verify trực tiếp để phân xử (xem C6).

| # | Khẳng định 12/10 | Verdict hôm nay | Bằng chứng chính |
|---|---|---|---|
| C1 | sessions.db là lock domain duy nhất; writer 750ms; ledger 100ms + silent drop | **CÒN ĐÚNG** (domain + budget); hành vi retry của writer đã đổi (xem C5) | `_SESSION_DB_BUSY_TIMEOUT_MS = 750` (store.py:62); `BEGIN IMMEDIATE` (store.py:583); ledger `busy_timeout=100` (server:2763-2765) |
| C2 | Read-token upsert là durable write trên đường đọc | **CÒN ĐÚNG** | fs.py:176 → store.py:1985 `_run_session_write`, durable mặc định True; khác biệt duy nhất: contention nay raise thay vì trả False (F8) |
| C3 | Session gate ghi durable trên request path | **CÒN ĐÚNG** | server:16039-16046 `await asyncio.to_thread(ct._auto_open_session…)`; sweep đóng session stale trên mọi lookup (store.py:1728 → 1684) |
| C4 | `_log_session_event_safe` chờ COMMIT, không deadline phía caller (A33); wedge detector mù với fail-fast livelock | **Phần lõi CÒN ĐÚNG; detector ĐÃ LÀM LẠI** | durable=True (store.py:2211); A33 ghi thẳng trong code (store.py:465); detector mới: stall 5s → chặn submit durable mới, wedge 30s, guard verdict stale (F25) → tiểu khẳng định "mù với livelock" đã chết |
| C5 | A40 503 tồn tại nhưng không tới được generic handler (fired 0 lần); retry không circuit breaker kiểu metronome | **ĐẢO NGƯỢC CẢ HAI NỬA** | Generic handler trả HTTP 503 `SESSION_STORE_CONTENTION` (server:16869-16874; classifier code_tools.py:1036-1067); tên `SERVER_BUSY` không còn trong code production; retry metronome đã bị gỡ — batch fail thì fail item, submit bị chặn fail-fast khi stall/wedge. Lưu ý (agent B): 503 chỉ bắn khi contention raise; writer "chậm nhưng đang tiến" vẫn chờ không giới hạn theo thiết kế |
| C6 | (Plan P3) admission theo agent chưa có | **ĐÃ CÓ, gần như đủ** | fair_admission.py:21-22 quota mặc định (command=1/principal, global cap 32) + 429/503 contract đã nối dispatch. **Số LIVE khác mặc định** (coordinator tự đọc): launcher high-IL đặt `ORCH_FAIR_COMMAND_QUOTA=8`, `ORCH_GLOBAL_CAP=96`, waiters 256/principal (high_il_launch.py:45-61) — daemon hiện tại chạy theo bộ số này |
| C7 | Ledger: 4 site, connection riêng, commit ngay, lỗi = warning + return False | **CÒN ĐÚNG NGUYÊN VẸN** | upsert :2763 / get :2911 / list :2947 / purge :2997 — cùng ghế với 12/10, 100ms, không `isolation_level`, silent drop :2810-2812 |
| C8 | Các campaign sau 12/10 có đụng các seam này không | **KHÔNG** | 34e2e1e3 = manage_agent/Nhóm 3; 7d9d67c = livez SSoT; hunks chưa commit ở seam files chỉ là observability (camera R5 SLOW_WRITE_FN trong store.py, boot markers ở server) |

### Phạm vi Phase 1 sau re-pin

- **P0 — còn nguyên việc:** cả ba durable write trên response path (C2/C3/C4) vẫn tại chỗ. (Có sẵn một phần giảm nhẹ phía audit: lane kế toán sau call + spool cho session events — chưa phải P0 hoàn tất.)
- **P1 — còn nguyên việc, đích sạch nhất:** ledger giữ nguyên từng chữ so với 12/10 (C7) — defect chronic (silent drop, GATE-REPORT §2.1: 258 lần/2 tháng) chưa campaign nào đụng tới.
- **P2 — thu hẹp mạnh:** tiền đề "503 không tới được client" đã sai. P2 còn lại là ngữ nghĩa: tên lỗi, caveat chờ-không-giới-hạn khi writer chậm-mà-tiến, và tương tác với spool của P1 — không còn là việc xây mới.
- **P3 — chuyển thành quyết định chính sách:** hạ tầng đã xây xong; còn lại là chốt bộ số live (command 8/principal theo launcher high-IL so với mặc định 1 trong code) có phải ý đồ hay không.
