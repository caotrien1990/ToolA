# R4 BOOT_STEP spam — Root cause (2-team blind audit, 2026-10-16)

SOT: PLAN TOOLA_1510_RECOVERY · repo `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator` @ `55d11a1f` + R0–R6 uncommitted
Skill: museR `2026-10-13-lanes3`, audit-only, 2 waves blind độc lập, hội tụ 100%.

## Verdict sau museB (2026-10-16) — đính chính báo cáo này

museB campaign (4 waves, wave 4 hội tụ 3/3 APPROVE, skill `2026-10-13-fixset-v3-cut1-c9c10law`)
kết luận: **DELETE dòng 1031 duy nhất** (`_boot_step("durable_audit_writer.start.done")`),
giữ 1025, không đụng logic accessor. Quyết định dựa trên 5 điểm đã verify,
**không dựa vào premise "R4 định one-shot"** (không có nguồn — đã loại khỏi cơ sở quyết định).
Cơ chế per-call của 1031 là CONFIRMED (3/3 wave-1); nhưng chữ "bug" theo nghĩa vi phạm thiết kế
thì KHÔNG được chứng minh — đây là quyết định hygiene trên evidence, không phải "fix bug thiết kế".
Sweep consumer 3 tầng = 0 consumer thật (repo 34 hits = 0 parser; VM hooks/museroom 0 hits;
Windows `8. ToolA\` + `50. MUSE\` recursive: *.ps1/*.bat/*.sh 12 hits đều là file cache
của chính script sweep, *.py 0 hits, ToolQ 0 hits).
Fix chưa apply — chờ literal "tiến hành"; small-loop; restart gộp vào đợt đã có kế hoạch.

## Kết luận

**Thủ phạm duy nhất: dòng 1031** — `_boot_step("durable_audit_writer.start.done")` trong `orchestrator_server.py`,
nằm trong nhánh `if start and writer is not None:` của `_get_durable_audit_writer()`.
Nhánh này chạy **mỗi tool call hoàn tất** (qua `_stage_durable_session_event:1532` ← dispatch epilogue `:16950`),
và dòng log là **vô điều kiện** → mỗi tool call = 1 dòng BOOT_STEP spam.

**Dòng 1025 (`init.done`) đặt ĐÚNG** — nằm trong block `if writer is None:` one-shot, chỉ bắn 1 lần/process. Không đụng.

## Vì sao đặt sai — đính chính sau museB (2026-10-16)

**Đoạn suy luận cũ bên dưới không có nguồn.** Design record R4 chỉ verify count/vị trí
17 sensors, không ghi one-shot intent (wave-2 R1/R2 bắt lỗi báo cáo này). museB hội tụ:
verdict DELETE 1031 **không dựa vào premise "R4 định one-shot"** — quyết định đứng trên
5 điểm đã verify, độc lập với intent. Giữ text cũ để lưu vết, không dùng làm evidence:

> R4 thiết kế 17 sensors như điểm đo boot một lần. Tác giả coi "writer start" là sự kiện một lần:
> `DurableAuditWriter.start()` idempotent nên "gọi start nhiều lần vô hại" bị lẫn với "log sau start vô hại".
> Boot-time start đã có sensor riêng `on_startup.post_call_accounting.writer.done` (dòng 1615)
> → 1031 vừa thừa cho K2 vừa spam runtime.

## Evidence (2 team tự re-derive độc lập, hội tụ)

- Log `orchestrator.log`: 1506 dòng BOOT_STEP; 1490 (98.9%) là `start.done`; 16 step còn lại đúng 1 dòng mỗi step.
- Boot window 08:17:07–08:17:12: đủ 17/17 tên step (ms 46→4952) → boot R7 thành công, không sensor nào hỏng.
- Post-boot: 100% dòng BOOT_STEP là `start.done`, ~21–23 dòng/phút, tương quan 1:1 với `INFO Tool call:`.
- Code: 1031 nằm ngoài one-shot block; 3 production callers của accessor với `start=True`:
  1532 (per-call), 1581 (drain), 1614 (boot-only).
- Blame: 1025+1031 là uncommitted (R4); thân hàm là code cũ (2026-08/09).

## Đã loại trừ

- "Tưởng accessor là boot-only": docstring ghi "Lazily create the durable writer for HTTP",
  docstring liệt kê 2/3 callers là runtime → bác bỏ.
- "Cố ý làm runtime metric": ms-since-import vô nghĩa cho metric tần suất → bác bỏ.
- "Boot lỗi / 16 sensor hỏng": đủ 17/17 trong boot window → bác bỏ.

## Khuyến nghị

Đáng fix, **không khẩn cấp**. Impact: log spam (~33k dòng/ngày khi flag ON),
overhead ~µs/call, không ảnh hưởng correctness của durable audit writer.
Scope ≤1 file: **xóa dòng 1031**, giữ nguyên 1025. KHÔNG move vào nhánh one-shot
(auto_start=False → "start.done" sẽ nói dối nếu move — alternative này đã bị bác bỏ).
Small-loop. **Không restart live chỉ vì việc này** — gom vào đợt restart đã có kế hoạch.
