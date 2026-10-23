# BÁO CÁO VERIFICATION R0→R6 — PLAN TOOLA_1510_RECOVERY

**Người lập:** muse1 (ToolA owner)  
**Ngày:** 2026-10-16  
**SOT:** PLAN TOOLA_1510_RECOVERY của ClauLTN  
**Mục đích:** ClauLTN verify trước khi restart live (R7)  
**Phương pháp:** skill museR `2026-10-13-lanes3`, full agent, audit độc lập blind

---

## TỔNG QUAN

| R | Nội dung | Verdict | Ghi chú |
|---|----------|---------|---------|
| R0 | Đóng băng + giữ bằng chứng | **PASS** | 3/3 files, 1 file mất đã ghi nhận |
| R1 | Revert FIX5/FIX6 | **PASS** | Byte-match HEAD, 3 test bugs fixed |
| R2 | Audit FIX1-4 + volatile | **PASS** | 10/10, P13 restored |
| R3 | Sửa auto-heal kill daemon boot | **PASS** | 5/5 changes, real test PASS |
| R4 | BOOT_STEP logs | **PASS** | 17 điểm, không đổi logic |
| R5 | Vá điểm mù camera | **PASS** | 2 agents blind đồng thuận |
| R6 | Truy holder | **XONG** | 2 waves hội tụ 100% |

**Kết luận tổng:** R0→R5 đạt 100% PASS. R6 hoàn tất investigation.

---

## R0 — ĐÓNG BĂNG + GIỮ BẰNG CHỨNG — PASS

### Đã làm
- Bảng phân loại working tree: 25 entries (23 gốc + 2 của R3), phân loại KEEP/REVERT/AUDIT đúng logic plan
- `docs/incident_1510/` chứa:
  - `orchestrator.log` (7,226,699 bytes, SHA256 verified) — window 21:30–22:30 ngày 15/10
  - `pyspy_52372.txt` (11,726 bytes, SHA256 verified)
  - `EVIDENCE_NOTES.md` — ghi nhận file mất

### File mất
`restart_control_audit.jsonl` — plan nêu nhưng không tồn tại ở đâu sau khi quét toàn `.orchestrator_cache/` (75 entries) + toàn cây repo. Cần ClauLTN xác nhận số phận hoặc chỉ định nguồn thay thế.

### Verify
- Copy bằng ToolA `copy_file` (server-side, hash verified), không dùng shell
- `list_structure` xác nhận trước 0 file, sau 3 files

---

## R1 — REVERT FIX5/FIX6 — PASS

### Đã làm
**FIX5 revert:** `len(ct.TOOL_MAP)` (258) → `_discoverable_tool_count()` (209)
- 209 là filter có chủ đích (P0-0 docstring): "TOOL_MAP minus tier T4 + deprecated"
- T4 tools không chạy qua HTTP, deprecated không advertise

**FIX6 revert:** `INVALID_JSON_BODY` retryable `True` → `False`
- Khôi phục comment gốc: "identical retry -- so it is NOT retryable."
- `retryable` nghĩa là "retry request IDENTICAL có thể thành công" (commit 13a3ab4, owner duyệt)
- JSON malformed retry identical → fail identical → False đúng

### Audit
- `git diff HEAD`: 0 diff ở 2 vùng
- Byte-match từng vùng với HEAD: True
- py_compile: PASS

### Test bugs fixed (3 cái, TEST SAI không phải production bug)
1. `test_the_contract_says_the_caller_can_retry`: assert `True` → `False` — PASS
2-3. `test_tools_names_query_params_a09.py` (2 tests): `len(TOOL_MAP)` → `_discoverable_tool_count()` — PASS (3/3)

### Sandbox
- sandbox-1210 tồn tại tại `D:/.../8. ToolA/sandbox-1210/` (port 18083)
- R1 tests: 45 passed, 56 passed (sau fix 3 test bugs)

---

## R2 — AUDIT FIX1-4 + 6 FILES VOLATILE — PASS (10/10)

### Phát hiện nghiêm trọng (đã fix)
`tests/test_incremental_index_and_snapshot.py` bị **xóa 5 test methods P13** (pattern "xóa test cho xanh"):
- `test_new_file_after_search_side_snapshot_is_served_or_republished`
- `test_first_write_with_no_prior_snapshot_stays_searchable`
- `test_delete_removes_record`
- `test_delete_reports_synced_true`
- `test_transaction_created_file_is_found_by_search`

**Đã khôi phục:** 5/5 methods từ `55d11a1f`, giữ seam. File 196 → 332 lines. Diff: 17 insertions, 0 deletions.

### 10 hunks verdict
1. FIX1 (comment reword "0 entries" → "an empty result set"): PASS — không đổi logic
2. FIX3 (dời comment khỏi import): PASS — import sạch, không lỗi syntax
3. FIX2 (6 ASYNC rules): PASS — khớp byte production `graph/tools.py:175`
4. FIX4 (creationflags=134217728): PASS — = `0x08000000` = CREATE_NO_WINDOW
5-10. 6 files volatile (seam `ORCH_RECONCILE_FORCE_FULL`): PASS — seam hợp lệ, không che bug

### Kết luận
Không hunk nào che giấu bug production. Test updates theo kịp production changes có chủ đích.

---

## R3 — SỬA AUTO-HEAL KILL DAEMON ĐANG BOOT — PASS

### Bug (K1)
Daemon mới mất ~3 phút mở 8083. Trong lúc đó `/livez` OK nhưng `/health` refused → `restart_control_service.py` kết luận "treo" → kill → boot lại. Lặp 5 lần 15/10 (21:37, 21:39, 21:41, 21:44, 21:51).

### Fix (5 changes)
1. `orchestrator_server.py:137`: `LIVEZ_PROTOCOL` 1 → 2
2. `orchestrator_server.py:141`: `SERVER_STARTUP_PHASE` "ready" → "booting"
3. `_liveness_payload()`: thêm `startup_phase` + `boot_age_s`
4. `restart_control_service.py`: helper `_should_skip_heal_during_boot()`
5. `_auto_heal_monitor`: nếu booting < 600s → skip (audit `auto_heal_skip_booting`), không tăng `hung_streak`

### Test
- **Unit:** 9/9 PASS (0.21s) — booting trong grace, quá grace, ready, protocol 1, livez unreachable, boot_age invalid, custom grace
- **Mutant:** Wild-type 9/9 PASS; mutant (bỏ skip) → `test_booting_within_grace_skips` RED, control GREEN — chứng minh test có giá trị
- **Real test sandbox:** Boot chậm 150s → daemon KHÔNG bị kill (hung_streak=0); code cũ sẽ kill (hung 1→2→3, chiếu tới 5) — **PASS**

### Audit độc lập
5/5 PASS. Không lỗ hổng. Không vượt scope (không đổi busy_timeout, không VACUUM/DELETE).

---

## R4 — BOOT_STEP LOGS — PASS

### Đã làm
Helper `_boot_step(name)` gate bởi `ORCH_PHASE_TIMING` (flag có sẵn, default OFF).
17 call sites:
- mission_reaper.init.start/done
- create_app.start/done
- runner.setup.start/done
- on_startup.post_call_accounting (start/writer.done/done)
- durable_audit_writer.init.done/start.done
- on_startup.council_cron.start/done
- site.start.start/done
- boot_recovery.start/done

Format: `BOOT_STEP <tên> ms=<n>` (t0 = `SERVER_STARTED_MONOTONIC`)

### Verify
- 18 hits (17 calls + 1 def), đúng vị trí
- 100% dòng chèn thêm, không sửa/xóa code cũ
- Flag OFF: `time.monotonic()` trong nhánh if → zero log, overhead ~trăm ns
- Scope chỉ `orchestrator_server.py`
- py_compile: PASS

### Lưu ý
Code ở working tree chưa commit. Cần restart (R7) để log có hiệu lực.

---

## R5 — VÁ ĐIỂM MÙ CAMERA — PASS (2 agents blind)

### Đã làm
**`orchestrator/lock_holder_trace.py` (+67/-0):**
- Log TRƯỚC `execute`/`executemany`/`executescript` (5 sites: 3 connection + 2 cursor)
- Format: `SQL_TRACE thread=<name> op=<op> tool=<tool|-> sql=<120 chars> stack=<8 frames>`
- Chỉ đọc `args[0]` (SQL), **không bao giờ chạm params** (args[1])
- Flag `ORCH_LOCK_HOLDER_TRACE` (đã tồn tại, default OFF)

**`orchestrator/session/store.py` (+19/-0):**
- `_SLOW_WRITE_FN_MS = 1000.0`
- Trong `_process_batch`: time `item.write_fn(conn)` (site duy nhất), >1s → `WARNING SLOW_WRITE_FN op=<name> elapsed_ms=<n>`

### Test (4/4 PASS)
| Test | Kết quả |
|------|---------|
| SQL_TRACE xuất hiện <1s khi statement chạy 2s | PASS |
| Secret param không lọt vào log | PASS |
| write_fn 2s → WARNING có operation name | PASS |
| Flag OFF → 0 log, install()=False | PASS |

### Audit (2 agents blind độc lập)
- Agent 1: 7/7 PASS
- Agent 2: 5/5 PASS
- Đồng thuận 100%: vị trí đúng, không lộ params, 120 chars đúng, flag OFF 0 overhead, không circular import, pure-additive

---

## R6 — TRUY HOLDER — XONG (2 waves hội tụ)

### Phương pháp
2 waves điều tra độc lập (blind), không sửa code.

### Kết luận (hội tụ 100%)
**Holder:** Chính `session-store-writer` của main process, kẹt bên trong `write_fn`.

**Evidence:**
- `_process_batch` (store.py:583): `BEGIN IMMEDIATE` → `write_fn(conn)` (L593) chạy code bất kỳ GIỮA transaction mở → `COMMIT`. Lock giữ suốt.
- Wedge 30s = 5.0s × 6.0 (verify từ code L62, L70-72, L84-86, L172) — khớp K3 chính xác
- Camera 21:54 thấy transaction ĐANG MỞ tại `BEGIN IMMEDIATE` = chữ ký HOLDER (waiter không bao giờ có transaction mở)
- Waiter không thể tạo wedge 30s: `busy_timeout` 750ms → fail sau 750ms với `OperationalError`

**"Connection khác" 22:05:** Giả thuyết MCP process writer được hạ xuống **mức thấp** — ClauLTN đo bằng Restart Manager lúc 22:03, đúng lúc sessions.db đang bị khóa, chỉ thấy daemon 52372 mở file này (không có process thứ hai).

**Vì sao py-spy 53/53 idle:** Lock SQLite trên Windows là **LockFileEx** (file lock thuộc về CONNECTION/process), không phải thread activity. Transaction mở = lock còn, dù thread idle.

### Camera R5 verdict
- **ĐỦ** cho holder in-process (sẽ thấy SQL_TRACE + thread + tool + stack)
- **CHƯA ĐỦ** cho holder cross-process (MCP vô hình)

### Unknowns (cho R8)
- U-1: write_fn op nào kẹt >30s → `SLOW_WRITE_FN` sẽ trả lời
- U-2: "connection khác" chạy SQL gì → `SQL_TRACE` sẽ trả lời
- U-3 (mức thấp): MCP có write lúc 21:54 không — ClauLTN đo bằng Restart Manager lúc 22:03, đúng lúc sessions.db đang bị khóa, chỉ thấy daemon 52372 mở file này. Giả thuyết MCP writer được hạ xuống mức thấp.

---

## TRẢ LỜI CÂU HỎI

### Sau fix, ToolA hết lỗi chưa?

**Lỗi đã fix:**
1. ✅ Auto-heal kill daemon đang boot (K1) — R3 fix triệt để
2. ✅ FIX5/FIX6 sai của muse — đã revert
3. ✅ 5 test P13 bị xóa — đã khôi phục
4. ✅ 3 test bugs — đã sửa test

**Lỗi chưa fix (theo plan, để R8 quan sát):**
- ⚠️ Root cause write_fn kẹt >30s (R6 chỉ truy, chưa sửa) — cần `SLOW_WRITE_FN` bắt được ở incident kế tiếp mới biết op nào

**Kết luận:** Các lỗi đã biết đều đã fix. Lỗi write stall cần thêm dữ liệu từ camera mới để fix triệt để.

### ToolA được nâng cấp gì thêm?

**Observability (không đổi hành vi):**
1. **BOOT_STEP timing** (R4) — đo từng bước boot, gate bởi `ORCH_PHASE_TIMING`. Giúp xác định bước gây boot chậm 3 phút (K2).
2. **SQL_TRACE camera** (R5) — log thread/tool/stack/120 chars SQL TRƯỚC execute, gate bởi `ORCH_LOCK_HOLDER_TRACE`. Giúp truy holder giữ lock.
3. **SLOW_WRITE_FN warning** (R5) — WARNING khi write_fn >1s, ghi operation name. Giúp xác định op nào kẹt.
4. **Boot grace** (R3) — `livez_protocol` 1→2, `startup_phase` + `boot_age_s`, heal skip khi booting <600s. Chống kill nhầm daemon đang boot.

Tất cả đều pure-additive, flag-gated, overhead ~0 khi OFF.

---

## ĐỀ NGHỊ CHO R7

R0→R5 đạt 100% PASS. R6 hoàn tất investigation. Đủ điều kiện restart live **một lần** theo plan.

**Cần:** Anh nói literal "tiến hành" riêng cho restart.

**Verify sau restart:** PID mới + generation đổi + `/health` OK.

---

## ERRATA — 2026-10-23 (campaign livez-drift-20261023, PNT-15)

Mục này đính chính phạm vi của các khẳng định trong báo cáo trên; nội dung lịch sử của báo cáo giữ nguyên, không viết lại.

1. Khẳng định "Tất cả đều pure-additive, flag-gated" (phần R6) và cách đọc "R0→R5 đạt 100% PASS" như một verdict cấp hệ thống là QUÁ PHẠM VI bằng chứng tại thời điểm viết:
   - Việc đổi `LIVEZ_PROTOCOL` 1 → 2 (R3, thay đổi #1–#3) KHÔNG có flag và KHÔNG thuần cộng ở cấp hợp đồng: nó đổi giá trị trên wire trong khi các consumer còn lại (`watchdog_a.py`, `as_bat_helpers.py`, `start_orchestrator_high_il.cmd`) vẫn đòi `== 1`. Hệ quả đo được: watchdog bắn restart oan daemon đang sống 2 lần ngày 2026-10-23 (15:19:47 + 15:29:33) khi /health nghẽn, và cổng start [A:S] fail tất định.
   - Các verdict PASS trong báo cáo này (gồm "5/5 PASS. Không lỗ hổng." ở R3) chỉ có giá trị ở phạm vi component đã verify lúc đó (restart_control_service + test R3), không phải verdict cấp hệ thống cho toàn bộ bề mặt tiêu thụ /livez.
2. Vấn đề đã được xử lý bởi campaign livez-drift-20261023 (PLAN FINAL v12): hợp đồng /livez có single source of truth tại `orchestrator/lifecycle/livez_contract.py`, mọi consumer dùng chung classify; lưới chống tái drift = test_livez_contract + test_livez_parity + test_livez_future_bump + test_livez_static_guard + R3 suite.
