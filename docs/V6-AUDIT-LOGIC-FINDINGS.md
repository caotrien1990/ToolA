# V6 — 8 finding đổi logic còn mở + 1 điểm cần chốt (audit nhanh §11, 2026-10-13)

SOT: `docs/PLAN_TOOLA_1210_V6.md` (SHA `0265F86D…A9F2`, 886 dòng). 2 verifier blind độc lập.
Kết quả 4 HIGH: E2/E8/E9 FIXED bằng cơ chế thật; E14 fix đúng cơ chế join nhưng còn mâu thuẫn phạm vi (xem D1).

## 8 finding đổi logic (cần sửa đặc tả, mỗi cái 1–3 dòng, không đụng kiến trúc)

**F1 — E25: Rollout live catch-22.** V6 giữ G3 "không restart [A] live khi agent khác đang có call dở" (d145) và step 6.2 đòi restart cho cả 5 nhóm flag (d758), nhưng không có cơ chế drain/quiesce → đọc đúng chữ rollout kẹt vô hạn trên live 20–30 CA; đọc lỏng vi phạm G3. *Gợi ý:* thêm vào 6.2 bước drain — ngừng nhận call mới, chờ in-flight xong (timeout 60 s), rồi restart; ghi rõ vào G3.

**F2 — E24: Tiêu chí live so sai mẫu số.** V6 6.2 (d764–766) vẫn "số live ≥ mức sandbox" và "orphan giảm ≥ 95% so với E16". Step 0.10 (d290–295) chỉ baseline sandbox, không có baseline live ngày thường; E16 là số ngày incident (532) → ngày thường vốn ~0 → PASS rỗng, gate G6 không phát hiện được regression. *Gợi ý:* đo baseline live 24 h TRƯỚC rollout (bổ sung 0.10 hoặc đầu 6.2), so live-vs-live-baseline thay vì E16.

**F3 — E22: fsync thất bại không có failure mode.** V6 1.6 (d385–386) bắt buộc "ledger begin fsync TRƯỚC khi write chạy" nhưng im lặng khi fsync fail (đĩa đầy) → implementer tự chọn: chạy tiếp (phá write-ahead) hay chặn (sinh lỗi mới không định nghĩa). *Gợi ý:* chặn write, trả lỗi non-retryable kèm `write_status_url`; ghi rõ vào 1.6 và audit 5.1.

**F4 — E19: ba chi tiết chưa định nghĩa.**
- Ngưỡng `startup_recovery` (3.5, d627–629) không có số → check "boot 10,000 folder dưới ngưỡng" không đo được. *Gợi ý:* pin số (vd 30 s).
- Phạm vi bộ đếm RAM (3.8, d649): rate-limit 3 lớp ở 3.7 đòi counter per-job/per-principal/per-session; không ghi scope → counter global → rate limit sai. *Gợi ý:* ghi rõ scope từng counter.
- "Singleflight theo project" (3.1, d574): singleflight hiện key theo signature, không có bản theo project → implementer tự bịa. *Gợi ý:* định nghĩa dict riêng key theo project cho reindex.

**F5 — E21: env nhạy cảm không có danh sách.** V6 4.2 (d700) "process con không kế thừa env nhạy cảm" nhưng không liệt kê; Windows spawn kế thừa toàn bộ env mặc định → token/key lọt sang process con. *Gợi ý:* liệt kê pattern (`*TOKEN*`, `*KEY*`, `*SECRET*`, `*PASSWORD*`, `*CREDENTIAL*`).

**F6 — E11: vị trí ledger-begin trong funnel chưa chốt.** Thứ tự normative (d460) không có ledger begin; 1.6 chỉ chốt "TRƯỚC khi write chạy". Gate-503 xảy ra sau begin → begin treo → `write_status` báo "running" tới lần boot kế (reconcile hash ở 1.6 chỉ sửa được khi boot). *Gợi ý:* pin begin SAU session gate trong thứ tự d460 — gate-503 không để lại begin treo.

**F7 — E15: `source_version` hash toàn file trên hot path.** V6 3.3 (d591–593) vẫn "mtime + size + hash file" **mỗi lần gọi** để tính job_key ở 3.3b → O(file) IO trước dispatch, đe dọa SLO1 với file lớn. *Gợi ý:* cache hash theo (path, mtime, size), chỉ hash lại khi đổi.

**F8 — E17: kịch bản C flaky.** Real test Phase 3 (d675–679): "client timeout trước soft deadline, rồi gọi một tool khác" → PASS "lời gọi kế tiếp nhận `async_done`", nhưng không pin khoảng chờ — gọi ngay khi job còn running → FAIL oan → kẹt gate G3. *Gợi ý:* pin "gọi lại SAU KHI job xong (poll `job_status` tới done hoặc chờ push có timeout)".

## D1 — điểm cần anh/Claude chốt trước Phase 3 (HIGH)

**E14 scope gap:** 3.3b fix đúng cơ chế join (tra index job ở funnel + CAS, delta f, real test A), nhưng V6 loại `read_file_range` khỏi job hẳn (3.1, d574) trong khi **Đích #2** (d10) vẫn hứa "Tool đọc (**read**, search, ocr, cbm) chạy lâu không mất output". Mâu thuẫn chưa được thừa nhận trong V6. Cần một trong hai: (a) sửa Đích #2 — ghi rõ read_file_range loại trừ by-design + lý do vì sao read dài không cần job; hoặc (b) bổ sung cơ chế bảo vệ read_file_range dài.

## Đã xử lý / chỉ polish (không đổi logic)

- E5: N/M phát hiện livelock (1.8, d414) chưa pin số — cơ chế đủ, real test tự hiệu chỉnh.
- E6: "Budget 100 ms giữ nguyên" (1.6, d386) không còn đối tượng sau khi bỏ `PRAGMA busy_timeout` → định nghĩa lại hoặc bỏ số.
- E7: `ORCH_WRITE_AUDIT_WAIT_MS` một knob hai nghĩa ngược nhau (1.4.2 → 503; 1.7.1 → thành công + audit_pending) → tách hai env.
- E12: "mặc định theo profile harness (0.9)" (2.1, d463) chưa có production default → ghi rõ default cho live.
- E13: "rejection_counts ≈ 0" (2.4, d500) vẫn vi phạm quy tắc "đo được bằng số"; audit "tool gọi tool" (2.3, d489) rỗng.
- E16: ĐÃ XỬ LÝ — join qua index job ở 3.3b, không phụ thuộc singleflight-waiter.
- E18: delta c "Tương thích có điều kiện" (3.0, d566) — thêm "chờ anh chốt" cho đúng luật governance của plan.
