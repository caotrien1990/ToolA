---
name: workflow_ingestion
description: |
  V15 R35: User-authored workflows in MD format. [A] = stateful step dispenser.
  CA hỏi step hiện tại, làm, submit → [A] trả step kế. CA KHÔNG đọc toàn bộ MD
  để tự lập kế hoạch — bị ép đi từng step. Activate khi User nói "chạy workflow X",
  "run workflow X", hoặc task tham chiếu workflow name.
trigger:
  - task_pattern: "chạy workflow | run workflow | execute workflow | workflow X | testgf | test4_manual"
  - tool: "workflow_list | workflow_start | workflow_get_current | workflow_submit_step | workflow_retry_step | workflow_skip_step | workflow_status | workflow_create_from_ui"
  - file_pattern: "*.md trong workflows/"
preconditions:
  - "open_session done"
  - "Workflow MD tồn tại ở <project>/.orchestrator_cache/workflows/ HOẶC <[A]>/.ToolA_Skill/workflows/"
related_skills:
  - 18_runtime_dom_T4
  - 22_electron_automation
risk_level: medium
---

# 26 — Workflow ingestion (MD stateful dispenser, R35)

## Triết lý

[A] **CHỈ** là step dispenser. KHÔNG sửa nội dung User. KHÔNG tự thêm tool/selector. CA chịu trách nhiệm đọc title + context và **tự** chọn tool phù hợp.

User định nghĩa workflow qua **UI popup** (R35 build sẵn — sẽ wire vào port 8083 ở phần D). UI lưu MD file. Khi CA chạy workflow:

```
1. CA → workflow_start(name)           → [A] trả step 1 (title + context)
2. CA làm step 1 (tự suy luận dùng tool gì)
3. CA → workflow_submit_step(name, step=1, status='done', evidence=...)
4. [A] trả step 2
5. ... lặp
```

## Storage

| Source | Path | Use case |
|---|---|---|
| Project (ưu tiên) | `<project>/.orchestrator_cache/workflows/<slug>.md` | App-specific |
| Global (fallback) | `<[A]>/.ToolA_Skill/workflows/<slug>.md` | Reusable |
| State | `<project>/.orchestrator_cache/workflow_state/<slug>.state.json` | per-project tracking |

`workflow_list` merge cả 2 source. Project thắng nếu trùng slug.

## MD Format

```md
# rawcut - getfootage sheet

## Step 1

### Link sheet
https://docs.google.com/...

## Step 2

### Nhập link sheet
Dán link sheet vào panel Source - Google Sheet.

## Step 6

### Chờ copy hoàn tất
Không dừng khi mới thấy 1-2 file. Chờ hoàn tất.
[completion_signal: kind=folder_growth_stable folder=<TARGET> stable_seconds=5]
```

**Convention duy nhất [A] hiểu**:
- `# <title>` ở đầu file = workflow title
- `## Step N` = step boundary (N là số nguyên, có thể không liên tục)
- `### <subtitle>` trong step = title/context block
- `[completion_signal: kind=<x> key1=val1 key2=val2]` = idle gate (auto-call assert_target_idle khi submit_step done)

Mọi nội dung khác giữ nguyên text, [A] không transform.

## Tools

| Tool | Tier | Purpose |
|---|---|---|
| `workflow_list(project_path)` | T1 | List .md với status (active/idle/complete) |
| `workflow_start(name, project_path)` | T3 | Tạo state, trả step 1 |
| `workflow_get_current(name, project_path)` | T1 | Re-read step hiện tại (idempotent) |
| `workflow_submit_step(name, step, status, evidence)` | T3 | done→advance, fail→pause |
| `workflow_retry_step(name)` | T3 | Quay lại step hiện tại sau fail/pause |
| `workflow_skip_step(name, reason)` | T3 | Skip step, advance |
| `workflow_reset(name)` | T3 | Về step 1, archive state |
| `workflow_status(name)` | T1 | Full state |
| `workflow_create_from_ui(name, body_md, scope)` | T3 | UI popup save MD (không transform) |

## CA execution loop

```
1. workflow_start(name) → step 1 body
2. CA đọc step.title_blocks → tự chọn tool (click, fill_value, invoke_app_function, ...)
3. CA thực thi
4. workflow_submit_step(name, step=N, status='done', evidence="...")
5. Response trả step kế HOẶC workflow_complete=true HOẶC target_not_idle_warning
6. Nếu CA confidence thấp → workflow_get_current để re-read
7. Nếu CA fail step → workflow_submit_step(status='fail', evidence='...') → PAUSE → STOP báo cáo User
```

## Fail handling — quan trọng

Khi CA submit `status='fail'`:
- [A] trả `workflow_paused: true` + `pause_reason` + `failed_step_body`
- CA **PHẢI STOP**, KHÔNG tự tiến tới step kế, KHÔNG tự retry.
- CA báo cáo User chi tiết: step nào fail, evidence, lý do.
- User quyết định:
  - `workflow_retry_step(name)` → quay lại step hiện tại
  - `workflow_skip_step(name, reason='...')` → bỏ qua, tiến step kế
  - User mở UI sửa MD → CA `workflow_get_current` reload

## Completion signal (idle gate)

Khi step có `[completion_signal: ...]`, sau `workflow_submit_step(status='done')` backend tự call `assert_target_idle` với signals đó. Nếu busy → response thêm `target_not_idle_warning` (soft). CA mặc định:
- Thấy `target_not_idle_warning` → KHÔNG đi step kế ngay. Wait 5-10s, gọi `workflow_get_current` để verify, hoặc `assert_target_idle` lại.
- Chỉ proceed khi idle confirmed HOẶC User explicit nói "proceed anyway".

**Signal kinds phổ quát**:

| Kind | Params | Áp dụng |
|---|---|---|
| `folder_growth_stable` | folder, stable_seconds | App copy/render/export |
| `process_cpu_idle` | process_name|pid, idle_threshold_pct, stable_seconds | Mọi app có process |
| `process_io_idle` | process_name|pid, stable_seconds | App ghi đĩa |
| `cdp_global_flag` | page_id, global_var, expected_value | Electron có cờ |
| `log_pattern_match` | log_path, regex, since_ts | App có log |
| `ui_state_stable` | page_id, stable_seconds | Electron/web UI |
| `window_title_match` | hwnd|title_pattern, regex | App desktop |

## V15 R39 — Template: "Repeatable run list + final audit"

Case workflow KHÔNG tuyến tính V1→V2→V3 (ví dụ Rawcut_preview: User test nhiều profile khác nhau, mỗi lần run lại cùng set step):

```md
# <app>_<flow>_runlist

## Step 1
### Setup chung
Mở app, đảm bảo state baseline. Reset nếu cần.

## Step 2
### Iteration: chọn profile
Liệt kê profile cần test: A, B, C, D. CA + User cùng quyết.

## Step 3
### Run profile A
Execute action với profile=A. Log artifact path: <artifact_A>.
[completion_signal: kind=folder_growth_stable folder=<output_A> stable_seconds=3]

## Step 4
### Run profile B
Same as Step 3 với profile=B. Artifact: <artifact_B>.
[completion_signal: kind=folder_growth_stable folder=<output_B> stable_seconds=3]

## Step 5
### Run profile C
Same với profile=C. Artifact: <artifact_C>.
[completion_signal: kind=folder_growth_stable folder=<output_C> stable_seconds=3]

## Step 6
### Run profile D (optional)
Skip nếu User confirm 3 profile đầu đủ.

## Step 7
### Final audit — compare across runs
Aggregate artifacts: file_count + bytes + duration per profile.
Báo cáo bảng so sánh cho User. KHÔNG đóng app/session trước khi audit done.
[completion_signal: kind=folder_growth_stable folder=<output_D> stable_seconds=5]

## Step 8
### Cleanup + close
Sau khi User xác nhận audit OK, đóng app + close session.
```

Pattern: lặp lại step 3 với param khác nhau ở mỗi run (CA pass `evidence="profile=A"` khi submit). Final step luôn là audit + cleanup.

## V15 R39 — Log/encoding guide cho app UI có mojibake risk

Khi app target có UI/log dễ mojibake (Electron UI string dài, mixed VN/EN, BOM issues):

**Workflow MD context của step nên prefer**:
- ASCII event code: `EVT_COPY_START`, `EVT_COPY_DONE`, `EVT_VALIDATE_FAIL` thay vì "Copy bắt đầu lúc..." log VN dài
- Structured metadata: `{event: "EVT_X", count: N, ts: "..."}` JSON encode
- Artifact reference: nếu log dài + có VN/binary → ghi ra file artifact bằng `save_log_snapshot`, MD chỉ chứa path + sha256, KHÔNG paste raw

Tránh trong workflow MD:
- ❌ Log line VN dài kèm `\n\t` mixed
- ❌ Quote raw string từ app UI (có thể có ký tự control)
- ❌ Paste binary header / hex dump trong context block

Nếu CA submit_step với evidence chứa log dài → encode `save_log_snapshot` rồi reference path trong evidence field.

## Anti-patterns

- ❌ CA đọc toàn bộ MD rồi tự lập plan vượt mặt User (cứ đi từng step).
- ❌ CA submit `done` khi mới thấy partial evidence (vd 2/30 file đã copy).
- ❌ CA tự skip step không hỏi User.
- ❌ CA gọi tool ngoài context (vd step nói "Bấm button A" nhưng CA bấm button B vì nghĩ B tốt hơn).
- ❌ Sửa MD content từ phía [A] — chỉ User sửa qua UI hoặc editor.

## UI popup

R35 ship endpoint `GET /ui/workflow_editor` + UI HTML. User định nghĩa workflow qua form rồi save → MD file. UI có:
- Workflow name input
- Step count với `+ Add` / `- Remove` / reorder
- Per-step: title (≥1), context (free text)
- Per-step optional completion_signal config
- Scope radio: project / global

Khi `Save` → POST `/api/call/workflow_create_from_ui` → ghi MD.

## Test profile integration

`run_test_profile(profile="rawcut_testgf_sheet")` delegate sang `workflow_start`. Profile name = MD slug.
