# ROOM — [A] Council backend + CA-UX investigation & upgrade

> Mục tiêu: làm cho **CA dùng [A]/council trơn tru mà không cần được nhắc liên tục**. Brief này tự-chứa để giao cho **1 CA khác** thực thi (KHÔNG dùng council). Mọi task có full-path + bằng chứng thật quan sát trong phiên live 2026-08-07 (Clau).
>
> [A:P] = `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
> Ràng buộc chung: [A] self-edit = native edit + `log_native_event` (HYBRID) → py_compile (venv312) → restart. KHÔNG phá golden/contract tests. Backup `*.bak_<name>_<date>` trước sửa. Đọc `[A:P]\ORCHESTRATOR_UPGRADE_ROADMAP.md` để không trùng.

---

## 0. Bằng chứng (triệu chứng THẬT, phiên 2026-08-07)
CA (Clau) hoàn thành được nhưng phải mò/tự-suy nhiều lần ở các điểm sau — đây là danh sách "đau" cần chữa:
- Set model worker `qwen3.8-max` → GET xác nhận → nhưng dispatch chạy `deepseek-v4-flash-free`. Không có warning rằng model không servable trên harness.
- Truyền `model` thẳng trong `council_message` param cũng KHÔNG override (vẫn deepseek).
- Text `council_message` tiếng Việt bị **mojibake** trong broker (encoding PS→JSON→HTTP).
- Worker chạy **SOLO** (không load skill `ope-agent`/không dùng sub-agent) dù prompt có trigger "load skill ope-agent và dùng agent" — vì `opencode run` one-shot không inject agent, model yếu bỏ qua trigger.
- **Restart [A]** phải reverse-engineer 1 chuỗi gate mù: `COUNCIL_PID_ALIVE` (false-positive guard bug) → `RESTART_DRAIN_REQUIRED` → controller service riêng giữ guard CŨ trong RAM (fix trên đĩa không tự nạp) → restart controller qua `schtasks ToolA_RestartControl` + kill process lingering → `BROKER_DISPATCH_UNMATCHED` (async dispatch artifact-first không đóng loop) → append reply đóng → mới restart được. Mỗi gate chỉ trả error-name cụt, không nói "cách clear".
- `leader-council-skill` KHÔNG có trong danh sách Skill của harness → phải đọc SOT `.council/council_rule_leader.md`.
- Thêm 1 tool phải sửa nhiều điểm rời (TOOL_MAP/TOOL_PHASES/TOOL_SCHEMAS/PROTECTED/registry_data). Gating lỗi cho error khó hiểu (400 body rỗng / PROJECT_PATH_REQUIRED / UNSUPPORTED_FIELD catch-22 ở stop_app).

---

## 1. Phase P0 — AUDIT (read-only, làm trước, 1 báo cáo)
Đọc + lập bản đồ hiện trạng (không sửa), xuất `AUDIT_council_ca_ux.md`:
- Council dispatch pipeline: `orchestrator_server.py` `council_message` (L9477), `_council_dispatch_message` (L8858), `_council_worker_model` (L6067), `_COUNCIL_DEFAULT_MODELS`/`_FALLBACK` (L3468/3410), cli_channel per agent (`code_tools.py` L12784+), `.council/worker_models.json`, `.council/ID_MODELS/`.
- Restart pipeline: `restart_orchestrator_via_bat` (L9892), `_restart_busy_state`, `orchestrator/lifecycle/restart_guard.py` (`evaluate_restart_guard`), `orchestrator/lifecycle/restart_control_service.py` (:8085, task `ToolA_RestartControl`), mission reaper (L13457).
- Skill/hint: harness Skill list vs `.council/*.md` SOT, `tool_decision_hint`, error `ca_guidance` builder, `SKILLS.md` router.
- Tool-registration: `orchestrator/core/registry.py` `register_tool`, `orchestrator/core/registry_data.py` specs, gating sets (`PROTECTED_TOOL_NAMES` L199, `TOKEN_REQUIRED_TOOLS` L238, `_MUTATION_SCOPE_MAP`).
- Chạy contract/golden test hiện có (đọc test dir) để biết baseline + tránh làm vỡ.

---

## 2. Tasks nâng cấp (ưu tiên; mỗi task: file + acceptance)

### T1 (HIGH) — Model routing trung thực với harness
Vấn đề: set/param model không phản ánh model THỰC worker chạy; fallback im lặng.
- Fix: (a) khi `council_worker_model set`, VALIDATE model có servable bởi harness/provider của agent; nếu không → cảnh báo/từ chối kèm danh sách servable. (b) `get` trả `effective_model` + `servable:bool`. (c) `council_message model` param PHẢI override. (d) `.council/ID_MODELS/<agent>` chỉ chứa model harness đó THỰC serve (ope=opencode-zen → chỉ `opencode/*`).
- Files: `orchestrator_server.py` (L6067, L8858, L3468), `.council/ID_MODELS/`, `.council/worker_models.json`, `code_tools.py` (L12784).
- Acceptance: set model không-servable → warn/reject + list; get hiện effective model; broker ghi đúng model dispatch chạy.

### T2 (HIGH, safety) — Enforce agent/skill cho worker (chống solo)
Vấn đề: worker yếu chạy solo, không dùng ope-agent → nguy hiểm cho [A]-core.
- Fix: inject agent/skill ở tầng HARNESS (vd `--agent` nếu opencode hỗ trợ); pre-flight yêu cầu worker report xác nhận đã load skill; route việc high-risk/[A]-core CHỈ tới worker mạnh (CodoC+gpt-5.6-sol) — chặn ope/deepseek solo cho [A]-core.
- Files: `code_tools.py` cli_channel, `orchestrator_server.py` dispatch spawn, `.council/council_rule_leader.md` (routing gate).
- Acceptance: report của worker có bằng chứng đã load agent/skill; dispatch [A]-core bằng model yếu bị chặn hoặc cảnh báo.

### T3 (HIGH) — Restart flow: quan sát được + 1-call
Vấn đề: restart [A] = chuỗi gate mù, controller giữ guard trong RAM.
- Fix: (a) controller HOT-RELOAD `restart_guard.py` (hoặc endpoint `/reload`) → fix guard không cần restart controller. (b) tool `restart_readiness` (T1): trả TỪNG blocker + cách clear chính xác (drain gì, đóng dispatch nào, kill controller pid nào). (c) async dispatch TỰ ĐÓNG khi worker exit (ghi reply đóng) + reaper đóng dispatch quá timeout → hết `BROKER_DISPATCH_UNMATCHED` treo. (d) thêm `force:true` (audited) cho `restart_orchestrator_via_bat` khi CA có quyền.
- Files: `orchestrator_server.py` (L9892, `_restart_busy_state`), `orchestrator/lifecycle/restart_guard.py`, `orchestrator/lifecycle/restart_control_service.py`, reaper (L13457).
- Acceptance: sau khi sửa guard, CA restart [A] trong ≤1 call; hoặc nhận 1 diagnostic liệt kê mọi blocker + cách clear; async dispatch xong tự đóng.

### T4 (HIGH) — Skill/hint cho CA (đúng lõi phàn nàn của Anh)
Vấn đề: leader-skill không loadable; hint chung chung; CA phải được nhắc.
- Fix: (a) đảm bảo `leader-council-skill`/`ope-agent`/`zcode-agent` **loadable qua harness Skill tool** (hoặc tài liệu hóa fallback SOT rõ ràng + tự-động trỏ). (b) hint CONTEXT-AWARE + actionable (câu lệnh kế tiếp chính xác), không phải "Follow payload_example and retry". (c) skill/hint `council_quickstart`: dẫn CA fresh qua convene→set-model→dispatch→monitor→verify kèm mọi gotcha ở mục 0. (d) AUDIT + refresh hint/skill LỖI THỜI so với tool surface hiện tại (nhiều hint mô tả sai/thiếu tool mới).
- Files: `.council/council_rule_leader.md`, `SKILLS.md`, `tool_decision_hint` impl, harness skill registration.
- Acceptance: 1 CA fresh chạy trọn 1 luồng council chỉ nhờ skill/hint, không cần nhắc ngoài; leader-skill load được.

### T5 (MEDIUM) — Register-tool 1 lệnh + gating error rõ
Vấn đề: thêm tool sửa nhiều điểm; gating error khó hiểu; stop_app 400-body-rỗng catch-22.
- Fix: (a) 1 `register_tool` khai báo đủ (name, func, phase, {token, session, mutation_scope}, schema) — 1 call cập nhật mọi map. (b) error gated-tool nói RÕ gate thiếu + cách (vd "thêm project_path; tool này mutating"). (c) FIX bug 400-body-rỗng khi session-gate fail (error mất body trước envelope).
- Files: `orchestrator/core/registry.py`, `code_tools.py` gating sets, error-response builder.
- Acceptance: thêm tool = 1 khai báo; error gating tự-giải-thích; không còn 400 body rỗng.

### T6 (MEDIUM) — council_message UTF-8
- Fix: `council_message` nhận text base64 (hoặc đảm bảo UTF-8 bytes end-to-end) → hết mojibake tiếng Việt.
- Acceptance: text tiếng Việt round-trip nguyên vẹn trong broker + tới worker.

---

## 3. Ràng buộc / guardrails
- KHÔNG phá contract/golden tests; chạy test trước+sau mỗi task.
- Backup mọi file trước sửa; py_compile (venv312) trước restart.
- **Gotcha restart:** guard nằm ở CẢ server chính LẪN controller service riêng (:8085, task `ToolA_RestartControl`) giữ trong RAM — sửa `restart_guard.py` phải restart controller (`schtasks /end`+`/run /tn ToolA_RestartControl`, chạy được non-elevated) mới nạp. (Chính T3 nên xoá gotcha này.)
- Dispatch async dùng artifact-first → NHỚ đóng loop (reply) kẻo chặn restart.

## 4. Definition of done
1. `AUDIT_council_ca_ux.md` (P0).
2. Patch T1–T6 + test cho mỗi cái, contract/golden PASS.
3. Skill/hint cập nhật + 1 `council_quickstart` để CA fresh tự chạy.
4. Demo: 1 CA fresh (không phải người viết) chạy trọn convene→set-model(servable)→dispatch(UTF-8, agent loaded)→monitor→verify→restart-[A]-1-call, KHÔNG cần nhắc ngoài, không dính gate mù.

---
*Nguồn: phiên live 2026-08-07 (Clau). Memory liên quan: `toola-upgrade-p134-landed`, `toola-multica-14point-repair`, `council-worker-native-tools-dispatch`, `toola-health-flap-mcp-alive`.*
