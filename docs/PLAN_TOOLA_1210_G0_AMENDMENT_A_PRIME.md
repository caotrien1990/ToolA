# PLAN PHỤ — G0 amendment thực thi (đường A′)
## TOOLA_1210 — Gắn camera quan sát lên live, bắt storm thật, rồi mới xét Phase 1

**Trạng thái:** DRAFT chờ anh duyệt. Chưa thực thi bước nào.
**Ngày:** 2026-10-14 (v4: vá 6 điểm adversarial Wave 5.5 B1–B6 — coordinator chốt theo ủy quyền)
**Nguồn:** Chỉ dẫn của Claude (đường A′) → museB brainstorm 4 waves → hội tụ 100% (reviewer cuối APPROVE).
**File SOT của plan chính:** `docs/PLAN_TOOLA_1210_FINAL.md` (V8 FINAL, SHA git blob `67CF0079FE1AB2CB3E41F2C5501E9EC21F192D1F05A0785723465151F8A57DC9`)

---

## 1. Vì sao có plan phụ này

Phase 0 đã dừng đúng ở cổng G0 (plan chính dòng 319–327): sandbox chỉ tái hiện được **2/5** dấu hiệu incident, nên KHÔNG được vào Phase 1.

Claude đề xuất đường A′: **đừng sửa dựa trên số đo sandbox** (1,27s + 0,23s cộng lại chưa tới 1,5s, trong khi incident thật là 211s — sửa theo số này dễ sửa sai pha). Thay vào đó: gắn camera quan sát lên live, bắt **một storm thật**, dựng lại kịch bản tái hiện từ dữ liệu thật, rồi mới quyết.

Đo trên toàn bộ log live được 10-11: 16, 10-12: 18, 10-13: 12, 10-14: 1 storm ≥60s (ngày 10-14 chưa hết) — 72h là time-box thực dụng, không phải dự báo thống kê.

**Một điểm quan trọng Claude chỉ ra:** live đang chạy code từ 10-12 08:15 (pid 62320). Code camera chỉ có ở commit mới. Vậy "gắn camera" thực chất = **restart live để nạp code mới** — đây là sự kiện rủi ro duy nhất của plan phụ, nên toàn bộ track A được thiết kế để bọc nó.

**Hiệu chỉnh số liệu restart:** 552 dòng module mới (lock_holder_trace.py 446 + phase_timing.py 106) được import và install() khi khởi động, cộng 142 dòng thêm / 11 dòng xóa trong 4 file cũ (adaptive.py, context.py, executor_lanes.py, orchestrator_server.py).

---

## 2. Quyết định G0

**KHÔNG vào Phase 1.** Đi đường A′ dưới đây. Sau khi anh duyệt plan phụ này, coordinator ghi nó vào plan FINAL thành §15 "G0 amendment".

---

## 3. Track A — Camera lên live (làm tuần tự, mỗi bước có cổng chặn bước sau)

### A0 — Chốt xem live đang chạy code nào
- Xác định commit mà live process (pid 62320, start 10-12 08:15:41 +07) thực sự nạp: đối chiếu start time với git log/mtime.
- Chạy `git status --porcelain` + `git stash list` và ghi lại. File nào có mtime trước start-time mà nội dung không khớp commit nào (so bằng `git hash-object`) → đánh dấu INDETERMINATE.
- **Xong khi:** có verdict CLEAN / DIRTY / INDETERMINATE. Fallback đã chốt trước (không cần báo anh mới đi tiếp): baseline chính cho A1 = **13a3ab4**; chạy thêm cùng suite trên **8007017** (hai baseline chỉ chênh 11 dòng thêm / 10 dòng xóa ở orchestrator_server.py).
- *(Ghi chú từ fix audit: commit 13a3ab4 tạo lúc 08:18:35, sau live 3 phút → live đang chạy 8007017 + code chưa commit lúc đó. Kết quả gần như chắc là INDETERMINATE hoặc DIRTY.)*

### B1 — Gỡ endpoint `/api/sleep` (làm TRƯỚC A1 để A1 test đúng cây sẽ deploy)
- Re-derive số dòng từ file live bằng read trước write; check cả chuỗi route `"/api/sleep"`, không chỉ tên hàm.
- Xóa block route trước, block code sau (bottom-up, read → write_fix). py_compile + test: route trả 404/405, 0 tham chiếu còn lại.
- Cần anh nói **"tiến hành"** cho 2a/2b. KHÔNG restart riêng cho việc này.
- Xong khi: test pass + lưu unified diff của B1 thành artifact.
- **Nhánh B1-skip:** nếu anh không duyệt 2a/2b → A1 pin tree có sleep-code (flag OFF nên route không đăng ký, vô hại), pre-A3 gate check delta = A2 + D3; A3 vẫn đi tiếp được.

### D3 — Throttle log phase_timing (diff #3, thuộc việc 1)
- Lý do (L1): PHASE_TIMING ghi 1 dòng INFO cho mỗi call, trung bình ~495 byte (đo trên 10 789 dòng log sandbox). Live ~170k call/ngày → log tăng thêm ~85 MB/ngày (gấp ~2,8 lần mức 45–47 MB/ngày hiện tại). Hệ quả: trigger (c) "log tăng bất thường" chắc chắn tự bắn ngay sau restart; log rotate 50MB×5 chỉ giữ được ~2 ngày → storm đầu cửa sổ 72h bị xóa trước khi kịp phân tích.
- Sửa code: chỉ ghi dòng phase khi tổng thời gian ≥ 2s, cộng 1% mẫu ngẫu nhiên để có baseline. Sampler nhận thêm tham số seed tùy chọn (mặc định None = random) — production không đổi hành vi.
- D3 cũng thêm **1 dòng INFO lúc boot**, vd `PHASE_TIMING enabled (threshold 2.0s, sample 1%)` — để A3(5) verify cờ đang chạy mà không phụ thuộc vào log từng call (sau throttle, read nhanh chỉ có ~1% khả năng được log). Dòng boot CHỈ được phát **bên trong nhánh install khi `ORCH_PHASE_TIMING=1`** (không đặt ở mức import module) — để dòng boot thật sự chứng minh cờ đang ACTIVE, không phải chỉ chứng minh module được nạp.
- Xong khi: code change qua read → write_fix + py_compile; lưu unified diff thành artifact (diff #3).
- Ủy quyền: thuộc việc 1 (camera).

### A1 — Bằng chứng an toàn trên đúng cây deploy (chạy trên sandbox, không cần ủy quyền)
- Full suite trên cây cuối (code mới − sleep + D3, 3 cờ quan sát OFF). Baseline chính = **13a3ab4**; chạy thêm cùng suite trên **8007017**; ghi phần lệch giữa hai baseline vào artifact. → 0 fail mới, so theo ID test. Overhead khi bật cờ tái dùng số Phase 0 (<1%).
- A1 cũng test diff #3 trong sandbox: call chậm ≥ 2s (assert dòng phase ĐƯỢC log); với seed cố định, assert deterministic số dòng được log đúng kỳ vọng (không cần test 2000 call thống kê); restart sandbox với cờ OFF → assert KHÔNG có dòng boot `PHASE_TIMING enabled`; equivalence khi cờ OFF. Không dựa vào live để test throttle.
- Xong khi: có bảng so sánh + lưu unified diff của A2 và D3 thành artifact.

### A2 — Bật 2 cờ quan sát
- Thêm `"ORCH_PHASE_TIMING": "1"`, `"ORCH_LOCK_HOLDER_TRACE": "1"` vào `HIGH_IL_ENV` trong `orchestrator/lifecycle/high_il_launch.py`.
- **Chỉ được đụng file này trên cây live SAU KHI anh duyệt việc 1.** Nếu chưa duyệt: làm trên worktree/branch riêng, merge sau.
- KHÔNG thêm `ORCH_SLEEP_ENDPOINT`, không bật cờ hành vi nào.
- Xong khi: đọc lại dict thấy đúng 2 key mới, không key lạ. Rollback: xóa 2 key.

### A5-prep — Hiệu chuẩn trigger watcher (chỉ đọc, chạy sau khi plan duyệt, trước A3 — không cần "tiến hành")
- Trên log ngày 10-13 (ngày nhiều storm) vs một ngày yên: đếm số dòng khớp pattern trigger theo cửa sổ 30s trượt → 2 phân phối.
- Chọn ngưỡng nằm giữa 2 phân phối; ghi con số vào config watcher + plan (thay cho "5/30s" cứng).
- Grep xác nhận mọi literal mà watcher/A3(5) phụ thuộc tồn tại trong code/log thật: `store contention`, `busy budget`, `lock_hold>`, `watchdog started`, `PHASE_TIMING enabled`. Literal nào không tồn tại → sửa pattern TRƯỚC khi deploy watcher.
- Xong khi: có ngưỡng số + grep PASS cho cả 5 literal.

### Pre-A3 gate — sensor chống code lạ (agent độc lập check, không phải coordinator)
- Định nghĩa: "tree-hash" = sha256 từng file mà B1/A2/D3 đã chạm + `git status --porcelain` phải sạch ngoài 3 diff đã lưu.
- Whitelist (không tính là bẩn): `PROJECT_CONTEXT.md`, `PROJECT_MAP.md` ([A] tự sinh lại) và chính file amendment `PLAN_TOOLA_1210_G0_AMENDMENT_A_PRIME.md` (untracked). Không có whitelist này gate sẽ HALT oan.
- Check: recompute và verify diff hiện tại khớp **đúng** 3 diff đã lưu (B1: gỡ sleep, A2: 2 cờ, D3: throttle log) — không hơn, không kém.
- Sai khác → HALT, tối đa 2 lần rồi báo anh (không chạy lại A1 vô hạn — máy A là máy anh đang thao tác).

### A3 — Restart live (CẦN anh nói "tiến hành" cho việc 1 — sự kiện rủi ro duy nhất)
- Chờ `executor_active_total` = 0 qua N lần poll liên tiếp (in-flight = executor lanes + active sessions). Tối đa 30 phút; không đạt → báo anh, không cưỡng ép.
- Restart CHỈ qua `restart_orchestrator_via_bat`.
- Verify 5 tiêu chí (từng cái một, không gộp): (1) pid mới; (2) **generation đổi** — không đổi thì DỪNG, kiểm tra cây code process mới nạp, báo anh; (3) /health 200; (4) smoke test 1 read + 1 write file nháp tại path cố định `.tmp\__smoke_a3.md` (đã gitignore) — KHÔNG tự xóa (xóa là destructive, theo 2-turn: file này đã nằm trong danh sách B3, xóa ở turn 2 sau duyệt); (5) **cờ thật sự chạy**: log live có dòng boot `PHASE_TIMING enabled (threshold 2.0s, sample 1%)` + dòng `watchdog started` của LOCK_HOLDER_TRACE — poll tối đa 60s chờ dòng boot xuất hiện (log cần thời gian flush); quá 60s không thấy → DỪNG như tiêu chí (2), không proceed.
- Sau restart: theo dõi +5 phút / +30 phút / +2 giờ / +12 giờ.

### A4 — Rollback (kích hoạt sau A3)
- **ClauLTN chốt (i) — ủy quyền trước theo ủy quyền của Anh 2026-10-14, giới hạn 2 trigger cứng:**
  - **Tự rollback ngay** khi: (a) /health fail; (b) p95 read_file_range tệ hơn >10% **kéo dài 2 giờ** so với baseline.
  - **Chỉ ALERT** (không tự rollback) khi: (c) tốc độ tăng disk/log bất thường (bytes/phút trên cửa sổ 10 phút); (d) error-rate > 5%/10 phút so với baseline.
  - Lý do: rollback chỉ là xóa 2 cờ quan sát; để live degraded ~11h chờ lệnh (worst-case của (ii)) thì phí.
- Nguồn đo p95 cho trigger (b): `duration_ms` trong `durable_audit_outbox`, đọc mode=ro. (Sau D3, log phase chỉ còn call chậm nên không dùng được cho p95.)
- Rollback cũng là một lần restart → **theo đúng điều kiện drain như A3** (poll executor_active_total = 0, tối đa 30 phút).
- Bước rollback: xóa 2 cờ → restart → verify pid/generation.
- Baseline = cửa sổ 2h SẠCH gần nhất trước restart; không có → dùng kỳ vọng overhead sandbox (<1%) + mở rộng cửa sổ tìm.
- Đánh giá trigger NGOÀI storm window. /health fail → rollback ngay kể cả trong storm.
- Người đánh giá: muse kiểm tra mỗi 6h, ghi tên vào log; đồng thời verify heartbeat watcher < 10 phút — nếu stale → restart watcher + ghi khoảng mù vào báo cáo G0′ (không diễn giải "không có storm" thành "không có dữ liệu").
- Giới hạn log watchdog: dùng giới hạn có sẵn của code — mỗi transaction tối đa 1 WARNING (alerted_2s) và 1 ERROR (alerted_30s). (Bỏ yêu cầu token-bucket ở bản trước: vừa là sửa code mâu thuẫn với pre-A3 gate, vừa không cần thiết.)

### A5 — Theo dõi chỉ đọc (cấm mọi thay đổi)
- MỌI storm ≥60s đều xuất dữ liệu tối thiểu (ít nhất bảng pha từ received tới sent) — kể cả không có orphan.
- Storm ≥60s đúng signature incident cũ (có orphan) → xuất full: bảng pha + stack/holder + PID ngoài process nếu hold >30s.
- Quy tắc: ≥2 storm dài mà không có orphan → xem lại định nghĩa signature + báo anh (không âm thầm loại dữ liệu).
- **Watcher layer-2 (M2):** một process PowerShell riêng (`watch_layer2.ps1`), chỉ đọc, poll log live mỗi ≤5s từ watermark, có log riêng, KHÔNG gắn vào session của muse (chạy detached bằng Start-Process ẩn — session chết thì watcher vẫn sống). Watcher append heartbeat (timestamp) vào log riêng mỗi 60s. Watermark persist xuống đĩa (file) — watcher restart thì đọc tiếp từ watermark, không trigger oan trên dòng cũ. Watcher tự tạo thư mục archive bằng `New-Item -Force` khi khởi động.
- Trigger watcher (ngưỡng đã hiệu chuẩn ở A5-prep, không hardcode 5/30s): (a) ≥ N dòng khớp `store contention|busy budget` trong 30s (N = ngưỡng hiệu chuẩn), HOẶC (b) dòng `lock_hold>`. Khi trigger: chạy ngay `lock_holder_layer2.ps1` qua run_ps1_script, lưu output kèm timestamp vào `<project>\.tmp\archive\layer2\`. Dedupe theo đợt (episode): các dòng trigger liên tục, ngắt quãng <60s coi là cùng đợt → mỗi đợt chạy layer-2 tối đa 1 lần; 60s yên → re-arm, đợt mới được chạy lại.
- Lý do (M2): dòng `lock_hold>` chỉ bắn khi connection TRONG process [A] ở in_transaction > 30s (lock_holder_trace.py:404-415) — holder ngoài (antivirus, backup, sync) làm connection chờ busy mà chưa vào transaction nên không bao giờ kích hoạt; cron 5 phút cũng quá thưa cho storm 60–300s.
- **Archive khi rotate (L1):** thư mục archive = `<project>\.tmp\archive\layer2\` (gitignore, cùng họ `.tmp`). Watcher làm owner bước copy-on-rotate: phát hiện rotate qua size giảm hoặc mtime reset → copy file vừa rotate sang archive; check dung lượng đĩa trước khi copy — giữ bằng chứng đủ 72h.
- Xong khi: có ≥1 bộ dữ liệu đạt chuẩn G0′, hoặc hết 72h.

---

## 4. Track B — Dọn dẹp

- **B2** (song song): sửa bẫy launcher sandbox — bỏ `os.environ.setdefault(... "1")` ở dòng 58–59 `launch_sandbox.py`; cờ nhận từ tham số/env từng run. Xong khi: chạy không cờ → instrumentation thật sự OFF.
- **B3** (2-turn): turn này **CHỈ liệt kê** đường dẫn đầy đủ + số lượng file tạm, rồi DỪNG chờ anh duyệt. Danh sách bao gồm cả `.tmp\__smoke_a3.md` — file smoke test của A3 (đã gitignore; chưa tồn tại lúc liệt kê; sẽ tạo ở A3). Turn 2 (xóa) cần duyệt riêng và chỉ chạy sau khi A3 hoàn tất.

---

## 5. Track C — Song song trên sandbox (không chạm live)

- **C1 — Step 1.5** (read-token RAM + write-through): chỉ dựng + test trong sandbox, flag OFF, không deploy. Build trên **git worktree/branch riêng**, **CẤM merge vào main** trước G0′ PASS. Code 1.5 không được land vào repo trước restart A3 (nếu land thì A1 phải chạy lại). Xong khi: checks (a)–(d) + mutant RED theo spec step 1.5 trong plan chính.
- **C2 — Step 1.1**: tách riêng, làm sau G0′. (Không ghép vào restart A3 như Claude gợi ý: trộn change quan sát + change hành-vi vào một restart thì rollback không biết đổ lỗi cho ai — đúng tinh thần lệnh CẤM bật cờ hành vi.)

---

## 6. Track D — Cổng G0′ và ghi §15

- **G0′ PASS khi:** camera bắt ≥1 storm thật ≥60s, bảng pha giải thích ≥90% wall-clock của call bị orphan, VÀ holder có tên (hoặc UNRESOLVED kèm danh sách đã loại từ dữ liệu live).
- Nếu holder vẫn UNRESOLVED: chạy lại real test với H2 best-effort + báo anh quyết (không để điều kiện không bao giờ đạt được).
- Sau PASS: dựng lại H2 theo đúng kiểu holder thật (nếu có tên) → chạy lại real test Phase 0 → mới vào Phase 1 với scope theo bảng pha live.
- **Time-box 72h** từ restart: hết 72h không có storm đạt chuẩn → báo anh kèm (a) dữ liệu camera đã thu, (b) đề xuất đường B CỤ THỂ (steps + guardrail).
- **D2 — Ghi §15:** sau khi anh duyệt plan phụ: backup file FINAL → verify đúng 958 dòng → write_fix §15 → recompute SHA-256 **trên git blob** (lưu ý: SHA `67CF0079…` trong plan này là SHA git blob, dòng LF; file trong working tree là CRLF nên Get-FileHash ra `923C951E…` — D2 ghi rõ SHA tính trên blob hoặc sau chuẩn hóa LF) → `git diff` chứng minh ngoài vùng chèn §15 file không đổi (write_fix ép file về LF nên không so byte trên working tree) → cập nhật SHA mới vào SOT → agent độc lập verify §15 khớp nội dung đã duyệt.

---

## 7. Cấm toàn cục

Không đổi `busy_timeout`. Không bật cờ hành vi (1.2+). Không test tải lên live.

## 8. Báo cáo tổng hợp (cuối)

Bằng chứng A1 (bảng so sánh + 3 diff artifact, gồm lệch baseline 13a3ab4/8007017) · pid/generation sau A3 · danh sách file tạm B3 · trạng thái trigger A4.

## 9. Sơ đồ phụ thuộc

```
A0 (verdict CLEAN/DIRTY/INDETERMINATE → baseline 13a3ab4 + 8007017)
 └→ B1 ─→ D3 (diff #3: throttle + boot INFO gắn cờ) ─→ A1 (lưu 3 diff) ─→ A2 ─→ A5-prep (chỉ đọc, hiệu chuẩn trigger) ─→ pre-A3 gate (agent độc lập, đúng 3 diff + whitelist)
                                                        └→ A3 (restart) ─→ A5 (+watcher M2, +archive) ─→ D (G0′)
                                                                             ↘ A4 (rollback, (i) giới hạn 2 trigger + check watcher)
B2, B3 (gồm .tmp\__smoke_a3.md), C1 (worktree riêng): song song độc lập · C2: sau G0′ · D2: sau khi anh duyệt plan này
```

---

## 10. Chỗ cần anh quyết (để thực thi)

1. **Duyệt plan phụ này** (bản v4: đã vá B1–B6) → coordinator ghi §15 vào plan FINAL (D2).
2. **Nói "tiến hành" cho việc 1** (camera: A0→D3→A3).
3. **Nói "tiến hành" cho việc 2a/2b** (dọn dẹp B1/B2).
4. **Việc 2c** (xóa file tạm): duyệt riêng sau khi có danh sách B3.

*Rollback A4: ClauLTN chốt (i) giới hạn theo ủy quyền của Anh 2026-10-14 — tự rollback chỉ cho /health fail và p95 >10% kéo dài 2h; trigger log/error-rate chỉ ALERT.*

*Ghi chú về độ tin cậy của plan này: 4 waves brainstorm (3 proposer/critic/researcher độc lập + 2 vòng adversarial), hiệu chỉnh số liệu restart (552 dòng module mới + 142 thêm/11 xóa trong 4 file cũ), 2 trigger tự hủy bị loại, fix audit L1/L2/L3 đã vá, Wave 5 vá M1/M2 + 5 sửa nhỏ, Wave 5.5 vá 6 điểm adversarial (B1–B6) (coordinator chốt theo ủy quyền 2026-10-14), reviewer cuối APPROVE v2 với xác nhận trung thực với chỉ dẫn gốc.*
