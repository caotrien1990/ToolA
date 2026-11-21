# R4 — Verdict museB: dòng 1031 `durable_audit_writer.start.done` (2026-10-16)

Người nhận verify: ClauLTN.
SOT: PLAN TOOLA_1510_RECOVERY · repo `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator` @ `55d11a1f`
Skill: museB `2026-10-13-fixset-v3-cut1-c9c10law` (4 waves, 12 agents, hội tụ 100% ở wave 4).
Câu hỏi gốc: "Dòng 1031 có đúng là bug không, hay lại sửa nhầm?"

## 1. Verdict

**DELETE dòng 1031 duy nhất** — `_boot_step("durable_audit_writer.start.done")` trong
Trạng thái: **ĐÃ apply 2026-10-16** — ClauLTN xóa đúng 1 dòng qua read_file_range → write_fix (user nói "tiến hành" trong chat Clau); operation_id \`8dd14bdf1ab4c773\`; diff = 1 dòng xóa; py_compile OK; behavior test PASS (50 accessor calls → 0 dòng start.done, BOOT_STEP khác vẫn ra 1 dòng); 79 passed + 8 subtests (durable_audit*, R3 boot grace, R5 camera). **ĐÃ live 2026-10-16 ~17:37 +07** — restart bằng restart_orchestrator_via_bat (.bat, user duyệt "tiến hành"); pid 28844→46448, generation 5fd41aac06a2→0128e227f2c6, /health ok. Verify live: boot window đúng 16 tên BOOT_STEP (không có \`durable_audit_writer.start.done\`); 105 tool calls sau boot → 0 dòng start.done (code cũ: 1:1 mỗi call).
`_get_durable_audit_writer()`). Giữ dòng 1025. Không đụng logic accessor.

Cơ sở: 5 điểm đã verify, **không dựa vào premise "R4 định one-shot"** (không có nguồn —
đã loại khỏi cơ sở quyết định, xem §3).

Trạng thái: fix **CHƯA apply** — chờ literal "tiến hành". Không restart live chỉ vì việc này.

## 2. Khác gì so với báo cáo audit ban đầu (`docs\incident_1510\R4_BOOTSTEP_SPAM_ROOTCAUSE.md`)

| # | Báo cáo ban đầu | Verdict museB |
|---|---|---|
| 1 | "Chắc là bug" — dòng 1031 đặt sai vì R4 thiết kế one-shot | **Không chứng minh được là bug.** Design record R4 (`docs\incident_1510\R0-R6_VERIFICATION_REPORT.md:118-142`) chỉ verify count/vị trí 17 sensors, không ghi one-shot intent ở đâu. Premise intent không có nguồn. |
| 2 | Đề xuất: move sensor 1031 vào nhánh one-shot | **Xóa hẳn.** Alternative "move" đã bị chứng minh sai sự thật: `auto_start=False` (`orchestrator_server.py:1022`) → nếu move "start.done" vào nhánh init, dòng log sẽ nói dối. |
| 3 | Cơ sở: ý định tác giả (suy luận MEDIUM-HIGH) | Cơ sở: 5 điểm verify độc lập với intent (§4). |

Giữ nguyên: cơ chế per-call của 1031 là CONFIRMED — 3/3 agents wave 1 đồng ý, 6 đòn
adversarial ở wave 3 không phá được.

Báo cáo gốc đã được đính chính theo verdict này (verdict banner ở đầu file, §"Vì sao đặt
sai" gắn nhãn suy luận không nguồn, §"Khuyến nghị" sửa thành xóa thay vì move).

## 3. Vì sao "bug" không được chứng minh (OB-1)

Wave 1 critic phá verdict BUG bằng 3 điểm, tất cả đứng vững qua 4 waves:

- **OB-1 (circularity):** 2 audit team trước chứng minh được *cơ chế* per-call, nhưng chưa
  chứng minh được *design intent* one-shot. Kết luận "đặt sai thiết kế" cần premise intent —
  premise không có nguồn.
- **OB-2/OB-3 (giá trị operational):** dòng 1031 bắn ở **success path** (trong `try:` sau
  `start_method()`), bị **skip** ở `except AuditClosedError` (`orchestrator_server.py:1031-1032`) —
  nên nó không phải "signal duy nhất trên failure path" như draft-v1 từng viết sai; giá trị
  còn lại chỉ là "unmonitored success-heartbeat", trùng 100% với `INFO Tool call:`.
- **OB-4 (external consumers):** sweep ban đầu chưa đủ → đã sweep 3 tầng, 0 consumer thật (§4.3).

Kết luận: verdict DELETE là **quyết định hygiene trên evidence**, không phải "fix bug thiết kế".

## 4. Năm điểm quyết định (kèm citation để re-derive)

**4.1. Sai mục đích ghi trên tên.** Dòng tên `BOOT_STEP` (boot timing) nhưng nằm trong nhánh
runtime `if start and writer is not None:` (`orchestrator_server.py:1026-1031`), bắn mỗi tool
call; giá trị là `time.monotonic() - SERVER_STARTED_MONOTONIC` (ms-since-import), vô nghĩa cho
boot timing (`orchestrator_server.py:19451-19458`).

**4.2. Thông tin ≈ 0.** Sau lần start đầu — `start()` idempotent
(`audit_writer.py:1486-1489`: `if self._thread is not None and self._thread.is_alive(): return`) —
mỗi dòng sau chỉ nói "accessor đã chạy". `INFO Tool call:` đã nói điều đó, tương quan 1:1
(verify trên `orchestrator.log`).

**4.3. Zero consumers (sweep 3 tầng).**
- Repo: search `BOOT_STEP` = 34 hits, **0 parser** (20 emitters trong `orchestrator_server.py`
  + 14 hits docs).
- Hooks/museroom phía agent: 0 hits.
- Windows recursive trên `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\` và
  `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\50. MUSE\`:
  `*.ps1/*.bat/*.sh` → 12 hits, tất cả nằm trong
  `project-orchestrator\.orchestrator_cache\test_scripts\` (file cache của chính các script
  sweep — pattern match vào `script_content` của script, không phải consumer);
  `*.py` → 0 hits; ToolQ text search → 0 hits (3 file lớn bỏ qua: `tokenizer.json` ~17MB +
  2 extension bundles trong thư mục trash — không phải consumer log).

**4.4. Chi phí giữ lại đã realized.** ~33k dòng/ngày (23 dòng/phút × 1440) đúng trong window
R8 cần log sạch; giá trị ms vô nghĩa đã gây 1 false alarm có thật (câu hỏi mở đầu
investigation: "16 cảm biến còn lại sao không lên?").

**4.5. Boot đã có sensor riêng.** First start lúc boot được đo ở
`on_startup.post_call_accounting.writer.done` (`orchestrator_server.py:1615`); accessor được
gọi ở dòng 1614. Xóa 1031 không mất visibility boot.

## 5. Lịch sử campaign (để audit quy trình)

- **Wave 1** — 3 agents blind (proposer / critic / researcher), cùng brief + SOT:
  **DIVERGE**. Proposer + researcher: BUG, đề xuất xóa 1031. Critic: NOT-A-BUG/not-established
  (OB-1..OB-4 ở §3).
- **Wave 2** — review draft-v1 (coordinator draft "UNDECIDED"): R3 APPROVE (pins + 9/9 citations);
  R1 CHANGES-REQUESTED — 1 lỗi substantive: fact "16 cái im post-boot là **đúng thiết kế**"
  không citation, mâu thuẫn Q1; R2 CHANGES-REQUESTED — 8 lỗi, nặng nhất CR-1: câu "xóa signal
  duy nhất trên failure path" **sai sự thật** (success-path, §3); CR-2/CR-3: checks "hỏi intent"
  và "giữ qua R8" malformed (không deadline, không tiêu chí quyết định).
- **Wave 3** — review draft-v2 (đã chữa 10 lỗi wave 2, verdict chuyển thành DELETE trên 5 điểm
  intent-free): R1 APPROVE (10/10 cures, 0 uncited claims); R3 APPROVE (pins, 6/6 citations,
  sweep JSONs); R2 CHANGES-REQUESTED **hẹp** — 2 lỗi text (CR-1: parenthetical "(3/3 wave-1 +
  R3 spot-check 9/9" trích dẫn quá đà; CR-2: thiếu evidence sweep `*.py`/ToolQ ở point 3),
  kèm verdict "decision sống sót — chữa xong 2 dòng thì APPROVE". 6 đòn adversarial
  (call site thứ 4 trong docstring, đường bắn 1581 dead-in-prod, chẩn đoán thread-death,
  failure visibility, move-into-init, vỡ test) **đều gãy**.
- **Wave 4** — verify hẹp draft-v3 (3 reviewers mới): **3/3 APPROVE** — 2 cures đúng verbatim,
  diff v2→v3 đúng 2 hunks, pins match, sweep JSONs khớp. **HỘI TỤ 100%.**

Ghi chú phương pháp: F1/F2 adjudication law không áp dụng — deliverable là DRAFT (verdict văn
bản), converge bằng APPROVE rule (§Convergence), không phải verdict-map reproduction.
F1 (wave 1) không hội tụ (2-1) nên không có converged point set để chạy F2 (item 4 gom-luôn).

## 6. Fix spec (khi có "tiến hành")

1. Xóa đúng 1 dòng 1031 trong `orchestrator_server.py`. Giữ 1025. Không đụng logic accessor.
2. Small-loop: fix (1 region) → 1 agent độc lập audit diff (G3b) → test: bật flag, assert
   post-boot `start.done` == 0 trong 10 phút; boot window đủ đúng 16 dòng BOOT_STEP (17 trừ `durable_audit_writer.start.done` đã xóa); assert không còn tên `durable_audit_writer.start.done` trong boot window.
3. Restart gộp vào đợt đã có kế hoạch. Không restart live chỉ vì việc này.

## 7. Checklist cho Clau re-derive độc lập

1. `orchestrator_server.py:1026-1031` — 1031 trong nhánh runtime, ngoài block one-shot của 1025.
2. `orchestrator_server.py:19451-19458` — `_boot_step` emit ms-since-import.
3. `orchestrator_server.py:1031-1048` — `except AuditClosedError` skip 1031 (success-path only).
4. `orchestrator_server.py:1022` — `auto_start=False` (bác alternative move-into-init).
5. `orchestrator_server.py:1612-1615` — sensor boot riêng tồn tại.
6. `audit_writer.py:1486-1489` — `start()` idempotent.
7. Đếm `BOOT_STEP` trong `orchestrator.log`: post-boot 100% là `start.done`, ~21–23/phút.
8. `docs\incident_1510\R0-R6_VERIFICATION_REPORT.md:118-142` — không câu nào ghi one-shot intent.
9. Re-run sweep Windows (read-only, PowerShell):
   `Get-ChildItem -Path "D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA" -Recurse -Include *.ps1,*.bat,*.sh,*.py -File -ErrorAction SilentlyContinue | Select-String -Pattern "durable_audit_writer\.start\.done|BOOT_STEP" | Select-Object -ExpandProperty Path -Unique`
   Kỳ vọng: chỉ hits trong `project-orchestrator\.orchestrator_cache\test_scripts\`.

---
*Tất cả path trong báo cáo này là Windows path (`D:\...`) hoặc relative từ repo root
trên máy Windows. Không có path nào chỉ tồn tại phía agent.*
