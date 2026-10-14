# BÁO CÁO ENGINEERING LANE — Audit PLAN_TOOLA_1210 V5

## 1. SOT CHECK (verbatim)

**SOT của vòng audit này:** file `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\docs\PLAN_TOOLA_1210_V5.md`

**Verify pin qua ToolA (read-only, 2026-10-12):**
- `content_hash` do server trả: `a5967dcd31851376ef89fdaf6dd4d70ce281f1f460c851f6c3cccd158b4bb9a8` — khớp pin `A5967DCD31851376EF89FDAF6DD4D70CE281F1F460C851F6C3CCCD158B4BB9A8` (chỉ khác hoa/thường)
- `total_lines` = 836 — khớp pin.

**§11.3 verbatim từ SOT (dòng 834):**
> 3. **Engineering lane:** một agent đóng vai người implement, trả lời cho mỗi phase: "làm đúng plan này thì hệ thống hỏng ở đâu?"

**Phương pháp:** đóng vai implementer làm đúng từng chữ; mỗi finding ghi rõ step/dòng SOT, kịch bản hỏng cụ thể, mức nặng, và điều kiện falsifiable.

---

## 2. PHASE 0 — Đo đạc, sandbox, truy gốc → Verdict: OK

Không có finding gây hỏng. Một ghi chú nhỏ (LOW): step 0.4 gắn marker `admitted/gate_done/read_token_done/sem_acquired/tool_done/finally_done/sent` (d233), nhưng sau Phase 2 funnel có thêm tầng admission và per-agent queue mà plan không cập nhật marker → chẩn đoán kiểu U2 sau Phase 2 sẽ thiếu breakdown ở tầng mới.

## 3. PHASE 1 — Persistence ra khỏi response → Verdict: CÓ FINDING (7)

**E1 (HIGH) — H8 thành test rỗng sau chính kiến trúc Phase 1 xây.**
- SOT: H2 bị pin "chỉ có nghĩa này trong toàn plan": `BEGIN IMMEDIATE` trên `sessions.db` của sandbox (d184–185). H8 `write_then_audit_stall`: "cho write apply xong file, rồi H2 khóa đúng lúc ghi audit/ledger", kỳ vọng "response thành công với `audit_pending:true`, không có 503" (d198–200).
- Nhưng sau step 1.2 (audit event → spool file, d322), 1.5 (read-token → RAM, d357–362), 1.6 (ledger → spool file, d376): **không còn await nào trên response path chạm `sessions.db`**. H2 khóa `sessions.db` không stall được đường audit/ledger nữa → H8 không bao giờ tạo ra `audit_pending:true` → "Xong khi: H8 PASS" (d395) đạt một cách rỗng. T3 (H8×50, d749) cũng rỗng theo.
- Kịch bản hỏng: bất biến headline của step 1.7 ("write đã apply thì không bao giờ 503") ra production mà **chưa từng bị stress thật**.
- Kèm theo: 1.7.1 "phần audit/ledger end chờ tối đa `ORCH_WRITE_AUDIT_WAIT_MS`" (d390) — sau khi 1.2/1.6 gỡ mọi await post-apply thì không còn gì để "chờ": wait target không được định nghĩa.
- Falsifiable: implement đúng chữ → chạy H8 → quan sát spool không bị ảnh hưởng bởi lock `sessions.db` → H8 PASS rỗng.

**E2 (HIGH) — Bất biến 1.7 không có cơ chế enforce, chỉ có audit thủ công một lần.**
- SOT 1.7.2 (d391): "write đã apply thì không bao giờ trả 503 hay lỗi retryable." Cơ chế duy nhất: 1.7.4 "liệt kê mọi nhánh trả lỗi sau điểm apply, mỗi nhánh phải là non-retryable hoặc thành công" (d393) — audit bằng mắt, một lần.
- Plan không định nghĩa: (a) "điểm apply" nằm đâu trong code cho từng write tool; (b) quy tắc phân loại lỗi sau apply — A40 classifier giữ nguyên.
- Kịch bản hỏng: post-apply spool-append gặp `IOError` (đĩa đầy) → classifier cũ đánh `retryable:true` → funnel trả 503 → CA retry → **write apply hai lần**.
- Falsifiable: sau khi implement, gây lỗi ghi spool sau apply → quan sát response có phải 503 retryable không.

**E3 (MEDIUM-HIGH) — SLO4 mâu thuẫn với check của chính các step.**
- SLO4 (d42): event "có mặt trong DB chậm nhất **60 s sau khi gọi**".
- Nhưng check của step 1.3.5 (d340), 1.8.5 (d411) đều viết "**sau khi nhả khóa**". Real test Phase 1 (d431) giữ H2 **120 s**, PASS đòi "SLO4 (H6 100% ≤ 60 s)" (d436).
- Kịch bản hỏng: call lúc t=0 trong cửa sổ khóa 120 s → event vào DB lúc ~t=120 s+ → vượt "60 s sau khi gọi" → implementation đúng vẫn FAIL real test → kẹt gate G1.

**E4 (MEDIUM-HIGH) — Một spool chung, drainer chỉ biết event.**
- 1.2.1 (d322): event vào "spool JSONL có sẵn (E11), **mở rộng chứ không xây spool thứ hai**". 1.6.1 (d376): ledger `begin` cũng append+fsync vào "spool" → một file chứa cả audit event lẫn ledger begin/end.
- Nhưng 1.3.1 (d333) chỉ mô tả drain **event** (`INSERT OR IGNORE` theo `audit_id`); không có: phân biệt loại record, xử lý ledger record, kỷ luật fsync (ledger cần fsync từng bản ghi **trước** khi write chạy; event fsync theo lô).
- Nguy hiểm nhất: 1.3.1 "dòng cuối spool bị cắt dở thì bỏ qua dòng đó" — nếu dòng cắt dở là **ledger begin/end** → mất write-ahead → sau crash `write_status` sai → check 1.6.5 "write_status đúng 100% sau crash" (d386) FAIL.

**E5 (MEDIUM) — Check của step 1.8 mô tả thế giới không còn tồn tại.**
- 1.8.2 (d404): "H2 giữ 120 s → … caller chưa chạy nhận 503 < 1 s". Nhưng 1.2–1.7 đã gỡ mọi blocking trên writer khỏi response path → không còn "caller chờ writer" để nhận 503 → điều kiện PASS không thể xảy ra (hoặc rỗng).
- Thêm: "N batch liên tiếp fail contention trong M giây" (d399) — N, M **không được định nghĩa ở đâu** → "Xong khi" (d411) không kiểm được. Nhánh 503-của-breaker thành dead code, trùng lắp với admission 503 của Phase 2.

**E6 (MEDIUM) — Budget 100 ms treo lơ lửng sau khi cơ chế bị thay.**
- 1.6.1 (d376): "Budget 100 ms giữ nguyên (G10)". Nhưng đối tượng của budget (PRAGMA `busy_timeout=100` của ledger SQLite — E5, d57) **không còn tồn tại** sau khi ledger chuyển sang spool fsync. 100 ms giờ ràng buộc cái gì — fsync từng bản ghi trên D: (USB SSD)? Plan không có số đo fsync-dưới-tải, cũng không định nghĩa vi phạm thì xử lý thế nào.

**E7 (LOW-MEDIUM) — Một knob, hai nghĩa, hai ngữ nghĩa lỗi ngược nhau.**
- `ORCH_WRITE_AUDIT_WAIT_MS` dùng ở 1.4.2 (d345): chờ session gate → quá hạn trả **503**; và ở 1.7.1 (d390): chờ audit/ledger end → quá hạn trả **thành công** + `audit_pending`. Bảng flag §3.6 (d161) chỉ ghi một nghĩa.

## 4. PHASE 2 — Admission/fairness → Verdict: CÓ FINDING (6)

**E8 (HIGH) — Ngoặc đơn reader-writer ở 2.7 đọc đúng chữ sẽ phá SLO5.**
- 2.7.1 (d504–508): "khóa project loại trừ mọi khóa file của project đó (**reader-writer: khóa file = shared trên project, khóa project = exclusive**)".
- Đọc đúng chữ → một RWLock/project: hai `write_fix` cùng file cùng giữ shared → chạy song song → vi phạm SLO5 "Write cùng file chạy tuần tự" (d42) và H7(A)/T5.
- Hai bullet đầu (key = project+file_path; nhiều file lấy lock theo thứ tự sort) gợi ý per-file mutex — **mâu thuẫn** với ngoặc đơn.

**E9 (HIGH — đã verify bằng code thật trên máy A) — Check singleflight 2.5 viết cho tool không bao giờ join.**
- 2.5.2 (d490): "20 agent **đọc cùng range** → `call_cache_joins_waited_total` tăng ≈ 19" — "đọc cùng range" là `read_file_range`.
- Code thật: `CACHEABLE_TOOLS` (call_cache.py:40–46) gồm cbm_*, ocr_map/ocr_scan/ocr_review, search_code, find_symbol — **không có `read_file_range`**. `begin()`: "A non-cacheable tool never joins", code `if not is_cacheable(tool): return True, ""` → `_JOINS_WAITED` đứng yên ở 0 → check FAIL đúng chữ → rẽ sang 2.6 "sửa đúng chỗ sai" → cám dỗ widen whitelist → read bị **cache kết quả TTL 300 s** → stale read.

**E10 (MEDIUM) — Wire format của 503 không được chốt.**
- 2.2.3 (d462): "503 này là HTTP 503 ở tầng admission, khác A40 (lỗi trong body)" — nhưng không chốt: HTTP status 503 + body JSON, hay HTTP 200 + body `{ok:false, error:SERVER_BUSY,...}`?

**E11 (MEDIUM) — Funnel normative thiếu vị trí ledger-begin.**
- Thứ tự bắt buộc (d445): admission → per-agent queue → session gate → semaphore → lane → tool → finally → response — **không có ledger begin**.
- 1.4.2 "write chưa chạy, retry an toàn" (d345) chỉ đúng nếu ledger begin nằm **sau** session gate; E12 (d66) cho thấy hiện tại ledger begin chạy sớm. Plan không chốt lại vị trí mới → gate-503 để lại begin treo → `write_status` báo "running" vĩnh viễn.

**E12 (LOW-MEDIUM) — Ánh xạ client → profile harness không định nghĩa.**
- 2.1.1 (d448): "Thiếu thì lấy mặc định theo profile harness (0.9)" — server biết call thuộc harness nào bằng gì? Không định nghĩa. Không hòa giải với deadline toàn cục duy nhất ở 2.4/§8A.1 (d779).

**E13 (LOW) — Hai điểm tự mâu thuẫn nhỏ.**
- 2.4.5 "rejection_counts ≈ 0" (d485) vi phạm quy tắc "Xong khi: điều kiện đo được bằng số" của chính §3.2.5 (d109).
- 2.3.3 đòi "không deadlock khi agent gọi lồng (tool gọi tool)" (d474) — kiểm code: không tool production nào gọi funnel lồng nhau → yêu cầu audit rỗng.

## 5. PHASE 3 — job_status v3 + 5 delta → Verdict: CÓ FINDING (6)

**E14 (HIGH) — Đường join cho `read_file_range` (tool của ca Codo) không tồn tại.**
- Phase 3 dựng trên "tầng RAM = call_cache/singleflight hiện có" (3.3.1, d572) và "bên đến sau đang poll singleflight cũng nhận stub tại soft deadline" (3.2.1, d564). Nhưng `read_file_range` ∉ CACHEABLE_TOOLS → **không có RAM tier, không có poll singleflight**.
- Plan không mô tả bước "tra folder job trước khi chạy tool" ở funnel → caller 2 cùng args → không join → chạy tool lần 2 → nhận `job_id` khác → REAL TEST Phase 3 kịch bản A "tool chạy thật 1 lần" (d643–647) **FAIL**; check 3.3.2 (d576) FAIL với mọi read tool.

**E15 (MEDIUM) — `source_version` = hash toàn file trên hot path.**
- 3.3.1 (d573–575): với read/search, `source_version` gồm "mtime + size + **hash file**" — hash toàn file **mỗi lần gọi** = O(file) IO trước cả khi dispatch, đe dọa SLO1 p95 < 2 s.

**E16 (MEDIUM) — Không có cơ chế nối waiter với stub deadline.**
- 3.2.1 (d564): "bên đến sau đang poll singleflight cũng nhận stub tại soft deadline" — plan không mô tả cơ chế nối timer của waiter với timeout của leader; implementer tự bịa.

**E17 (MEDIUM) — Real test Phase 3 kịch bản C thiếu chốt thời gian, flaky-by-design.**
- d645: "client timeout trước soft deadline, rồi gọi một tool khác" → PASS "lời gọi kế tiếp nhận `async_done`" (d649). Nếu gọi kế tiếp **ngay** (job còn running) → chưa có push → FAIL. Không pin khoảng chờ.

**E18 (LOW-MEDIUM) — Delta c ghi đè v3 đã ratify nhưng bảng verdict sẵn "tương thích".**
- Bảng 3.0 (d549): reconcile "result.json parse được → done", verdict "Tương thích có điều kiện". Nhưng IMPLEMENTATION-BRIEF.md:137 (v3 đã ratify): "previous-generation `running` → `unknown` (via `boot_epoch`)" — v3 nói unknown **mọi** trường hợp; delta đổi thành done khi parse được. Vi phạm luật của chính plan (d541) "Delta mâu thuẫn với v3 thì muse báo Anh, không tự sửa".

**E19 (LOW) — Ba chi tiết không định nghĩa:** 3.5.3 "ngưỡng `startup_recovery`" (d597); 3.8.1 "bộ đếm nằm trong RAM" (d619) phạm vi không rõ; 3.1.1 "singleflight theo project" cho reindex (d556) không tồn tại trong singleflight theo-signature.

## 6. PHASE 4 — Process pool → Verdict: CÓ FINDING (2)

**E20 (MEDIUM-HIGH) — Child process reindex `graph.db`: cấm sessions.db nhưng quên graph.db.**
- 4.2 đưa cbm_* vào `ProcessPoolExecutor` (d666); E13 (d65): `cbm_dead_code`/`cbm_test_gap` (`derived_index`) "có thể reindex `graph.db`".
- Lệnh cấm duy nhất: "không mở writer `sessions.db`" (4.2.3 d669, §7.5 d768) — **graph.db không bị cấm** → nhiều child reindex song song → `SQLITE_BUSY` trên graph.db. Quy tắc 3.1 "hai job reindex không chạy song song" (d556) dựa trên singleflight ở **main process**, không serialize write SQLite của các child.

**E21 (LOW-MEDIUM) — "Không kế thừa env nhạy cảm" không có danh sách.**
- 4.2.3 (d670): "process con không kế thừa env nhạy cảm" — plan không liệt kê env nào là nhạy cảm; trên Windows (spawn) env được kế thừa mặc định.

## 7. PHASE 5 — Dọn dẹp → Verdict: CÓ FINDING (1)

**E22 (MEDIUM) — Thất bại của fsync bắt buộc không được định nghĩa.**
- 5.1.1 (d695): "Ledger `begin` fsync spool là ghi bền bắt buộc" — nhưng khi fsync **thất bại** (đĩa đầy): write được chạy tiếp hay bị chặn? Plan im lặng.

## 8. PHASE 6 — Nghiệm thu, bật dần trên live → Verdict: CÓ FINDING (3)

**E23 (MEDIUM-HIGH) — T12 "tắt flag → hành vi về HEAD" không thể đạt sau Phase 5.**
- T12 (d758): "tắt từng flag → … hành vi về HEAD, full suite 0 fail mới".
- Nhưng: step 1.1 "luôn bật, không qua flag", rollback = **revert commit** (d313, d319); 5.2 xóa code chết, rollback = **revert commit** (d708); 5.3 archive rồi xóa events, rollback = **nạp lại từ archive** (d716). Tắt flag không phục hồi được → T12 FAIL trên implementation đúng → mâu thuẫn guardrail G4 "Rollback = tắt flag rồi restart" (d148).

**E24 (MEDIUM) — Tiêu chí live so với mẫu số sai.**
- 6.2.3 (d735): "số live ≥ mức sandbox" — nhưng **không có baseline live ngày thường**: 0.10 chỉ baseline sandbox (d288–294).
- 6.2.4 (d736): "`CALL_ORPHANED` bỏ phí trên live giảm ≥ 95% so với E16" — E16 = 532 orphan của **ngày incident**; ngày thường sau rollout đương nhiên ~0 → PASS rỗng.

**E25 (MEDIUM) — Catch-22 của rollout live.**
- G3 (d147): "Không restart [A] live khi agent khác đang có call dở". 6.2.1: cả **5 nhóm** rollout đều cần "tiến hành" + restart (d728). Plan **không có cơ chế drain/quiesce** → trên live 20–30 CA gần như luôn có call dở → rollout kẹt vô hạn (đọc đúng chữ) hoặc vi phạm G3 (đọc lỏng).

## 9. Kết luận lane

**CÒN FINDING MỞ — vòng audit chưa đóng được** (theo §11.5).

Tổng: **5 finding HIGH** (E1, E2, E8, E9, E14) + 4 MEDIUM-HIGH (E3, E4, E20, E23) + 9 MEDIUM/LOW-MEDIUM + 3 LOW.
