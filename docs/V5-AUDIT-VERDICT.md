# Verdict audit PLAN_TOOLA_1210 V5 (theo §11) — 2026-10-12 ~22:50 +07

## SOT
- File: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\docs\PLAN_TOOLA_1210_V5.md`
- SHA-256: `A5967DCD31851376EF89FDAF6DD4D70CE281F1F460C851F6C3CCCD158B4BB9A8` (60952 bytes, 836 dòng) — 3 agent độc lập verify khớp pin.
- museR version: `2026-10-11-wave-loop`. Mode: read-only (không sửa máy A, không restart, không đổi flag).

## Phương pháp (§11)
- **Wave 1** — 3 lane blind song song: Reality (chạy lại lệnh kiểm E1–E27 + mọi số trong step), Engineering (đóng vai implementer: "làm đúng từng chữ thì hệ thống hỏng ở đâu?"), Regression (so với GATE-REPORT + spec v3 job_status).
- **Wave 2** — 2 verifier fresh độc lập: 1 cho E1–E25, 1 cho G1/G2/R1–R4. Verdict từng finding: CONFIRMED / REJECTED / PARTIAL.

## Verdict chung: CHƯA SẠCH — vòng audit chưa đóng (§11.5)
Engineering lane còn finding mở đã qua verify → theo §11.5 không đóng vòng audit được.

## Findings đã verify đứng

### HIGH (4)
- **E2** — Bất biến 1.7 ("write đã apply thì không bao giờ 503") không có cơ chế enforce, chỉ audit bằng mắt một lần (1.7.4). Plan không định nghĩa "điểm apply" cho từng write tool, A40 classifier giữ nguyên → post-apply spool-append gặp IOError → 503 retryable → CA retry → **write apply hai lần**, đúng thảm họa 1.7 tuyên bố ngăn.
- **E8** — 2.7.1: "khóa file = shared trên project, khóa project = exclusive". Đọc đúng chữ → hai `write_fix` cùng file cùng giữ shared → chạy song song → phá SLO5 ("Write cùng file chạy tuần tự") và H7(A)/T5.
- **E9** — Check 2.5.2 "20 agent đọc cùng range → `call_cache_joins_waited_total` ≈ 19" viết cho `read_file_range`, nhưng code thật (`call_cache.py:40-46`) cho thấy `read_file_range` ∉ `CACHEABLE_TOOLS` → `_JOINS_WAITED` đứng yên ở 0 → check FAIL đúng chữ → rẽ sang 2.6 "sửa" → cám dỗ widen whitelist → read bị cache TTL 300s → stale read.
- **E14** — Ca Codo (headline Phase 3): Phase 3 dựng trên "tầng RAM = call_cache/singleflight" nhưng `read_file_range` không có RAM tier, không poll singleflight; plan không mô tả bước "tra folder job trước khi chạy tool" ở funnel → caller 2 cùng args chạy tool lần 2, nhận `job_id` khác → real test A "tool chạy thật 1 lần" FAIL đúng chữ.

### TRUNG BÌNH (1)
- **G1** — V5 §8A.1 "Đã chốt": "Soft deadline B1 = clamp(…)". Spec v3 (IMPLEMENTATION-BRIEF §5) ghi rõ: "B1 (default timeout — 25s proposed, **NOT yet ratified**)". Vừa đóng đơn phương mục chưa ratify, vừa đổi nội dung (25s cố định → công thức). "B1" xuất hiện đúng 1 lần trong V5, không chỗ nào ghi chờ duyệt. **Cần anh phán.**

### MEDIUM-HIGH (4)
- **E3** — SLO4 "event vào DB chậm nhất 60s **sau khi gọi**" vs check step "≤ 60s **sau khi nhả khóa**"; real test giữ H2 120s mà PASS đòi SLO4 → implementation đúng vẫn FAIL → kẹt gate G1.
- **E4** — Một spool chung cho cả audit event lẫn ledger begin/end (1.2.1 + 1.6.1), nhưng drainer 1.3.1 chỉ biết event; "dòng cuối bị cắt dở thì bỏ qua" — nếu dòng đó là ledger begin/end → mất write-ahead → check 1.6.5 FAIL sau crash.
- **E20** — 4.2 cấm process con mở writer `sessions.db` nhưng quên `graph.db`; cbm_* (`derived_index`) có thể reindex → nhiều child song song → `SQLITE_BUSY` trên graph.db.
- **E23** — T12 "tắt flag → hành vi về HEAD" không thể đạt: 1.1 luôn bật không qua flag, 5.2/5.3 rollback bằng revert commit / nạp archive → tắt flag không phục hồi được.

### NHẸ + KHÁC
- **G2** (nhẹ) — `job_state: created` của v3 (created|running|done|failed|unknown) bị rơi trong V5 (0 hit), delta table không ghi lý do. Cần 1 dòng lý do.
- **R1–R4** (deviation số liệu, không material) — E15: 2,178→2,629; E16: 532→683 (đếm độc lập: 689, cùng tỉ trọng per-tool → cùng phương pháp, thêm dữ liệu); E25: 338→343.8→344.5 MB. Nguyên nhân: log/DB tăng sau thời điểm anh đo (log rotate 21:09:27, incident data giờ ở `orchestrator.log.1`). R4: quy ước `LOG=orchestrator.log` trong V5 §2 từ giờ ra sai cho cửa sổ incident — cần 1 dòng cập nhật.
- 9 MEDIUM/LOW-MEDIUM + 3 LOW khác (E5p, E6, E7, E11, E12p, E13, E15–E17, E19, E21, E22, E24, E25): xem engineering-lane-report đầy đủ.

### Bị verify BÁC (quy trình hoạt động đúng)
- **E1** — Lane đọc sai scope 1.2 (chỉ chuyển event của tool đọc sang spool; event write sau apply vẫn await `sessions.db`) → H8 **không** rỗng.
- **E10** — 2.2.3 đã chốt HTTP 503 + body fields (d34, d445), không ambiguity.

### Điểm cộng
- Reality: 14/14 bằng chứng code khớp 100% vị trí; 8/10 bằng chứng log khớp tuyệt đối (tổng 258 ledger-fail khớp GATE-REPORT đến từng đơn vị); 48 step đều có "Xong khi"; không mâu thuẫn nội bộ giữa các step.
- Regression GATE-REPORT: **SẠCH** — mọi root cause P-C1→P-C4, P-N1/N2/N4, P-T1, P-H, thứ tự P0→P3, cả 3 exclusion đều được cover, không mục nào rơi im lặng. §10: 15/15 claims nhất quán nội bộ.
- Phase 3 giữ đúng cấu trúc "job_status v3 + 5 delta tường minh"; O3/O4 không bóp méo; O1/O2/O5 vẫn mở.
- §8A.2 khôi phục đúng "Không chuyển DB" kèm điều kiện đo 0.8.
- Observation: U3/U4 V5 ghi "chưa biết" nhưng code đã có đáp án (đỡ việc Phase 0); §8B nên đổi nhãn thành "nhắc lại constraint đã chốt" thay vì xin duyệt lại.

## Khuyến nghị
- **Phase 0 (đo đạc/sandbox/instrumentation): scope sạch, không finding nào chặn.**
- **Nhưng 4 HIGH ở Phase 1–3 nghĩa là V5 hiện tại implement đúng chữ sẽ hỏng** → nên patch V5 (E2/E8/E9/E14 + phán G1 của anh) trước khi vào Phase 1.

## Caveat provenance (đã ghi nhận, không chặn verdict)
- GATE-REPORT.md trên đĩa (SHA `a9c855f4…`, mtime 19:52) ≠ pin đã ghi `13a3ab45…` (verify lần cuối 19:13, không có log sửa). Verdict regression dựa trên nội dung đọc được (đủ 8 section).
- Bản working copy V5 (830 dòng) khác format với SOT máy A (836 dòng) do quirk copy — nội dung tương đương, pin SOT đã được 3 agent verify độc lập.

---
Chi tiết đầy đủ nằm trong working files của campaign plan-v5-audit (output-history.md, 3 lane reports).
