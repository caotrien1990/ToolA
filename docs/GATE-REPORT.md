# GATE REPORT — Truy gốc nghẽn ToolA 2026-10-12 15:30–15:42
Campaign: congestion-rootcause-20261012
Pin: `13a3ab454b20d07dac0aed2252ae7566d921fb21` (6/6 agents verify MATCH)
Ngày: 2026-10-12. Mode: Audit-Only (read-only, không fix code, không restart, không commit)

## 1. Verdict tổng hợp (11 pooled points, 2 verifiers độc lập blind)

| # | Point | Verdict |
|---|---|---|
| P-C1 | Lock domain là `sessions.db`; 3 write path không coordination, budget lệch 750ms/100ms | CONFIRMED |
| P-C2 | Write ledger trong sessions.db; fail → silent drop, chronic theo ngày (bảng §2.1) | CONFIRMED |
| P-C3 | A33 bỏ caller-side deadline; wedge detector mù với fail-fast livelock | CONFIRMED |
| P-C4 | Event loop sống trong incident | CONFIRMED |
| P-C5 | Async/job ngoài phạm vi vụ này → contention-first ordering | CONFIRMED |
| P-N1 | Read-token upsert là durable write trên read path (fs.py:176) | CONFIRMED |
| P-N2 | Session gate durable writes trên request path | CONFIRMED |
| P-N3 | Burst ~25 parallel reads báo trước cả hai wave | CONFIRMED (correlation, không phải causation) |
| P-N4 | Retry không circuit breaker amplify; A40 503 fired 0 lần | CONFIRMED |
| P-T1 | Vị trí 211s stall | ADJUDICATED (xem §3) |
| P-H | Danh tính lock holder | UNRESOLVED |

Không point nào bị REFUTED. Verdict hội tụ sau Verification Wave 1 → đóng loop, không cần Wave 2.

## 2. Chuyện gì đã xảy ra (chuỗi nhân quả đã verify)

Khoảng 15:30:58, một writer chưa xác định giữ write lock của `sessions.db` ~3.5 phút (wave 2 lặp lại 15:37–15:42). Trong thời gian đó:

1. **SessionWriter** (single thread) mỗi batch mở `BEGIN IMMEDIATE` → fail → đốt hết 750ms busy budget → `SessionStoreContentionError` → warn + retry. Retry không circuit breaker → metronomic warnings mỗi ~2–4s, chính retry storm chiếm luôn writer thread.
2. **Write ledger** (connection riêng, budget 100ms) fail trước cả writer → log warning + `return False`, không retry/spool. In-memory row còn (process đang chạy vẫn trả lời `write_status`), nhưng DB row mất — restart trong window là mất vĩnh viễn. Đây là defect CHRONIC theo ngày (bảng kê §2.1: 258 lần từ 2026-08-19 tới 2026-10-12 trên 4 file log còn giữ), không phải chỉ trong incident.
3. **A33 no-deadline**: các durable writes trên request/response path — read-token upsert (P-N1), session gate (P-N2), ba `_log_session_event_safe` awaits (P-T1, durable=True qua default param) — chờ COMMIT không giới hạn wall-clock. Wedge detector (30s, reset khi có commit) không trip vì thread đang "fail đều đặn", không phải "đứng yên". Kết quả: read nhỏ 1654 bytes kẹt 211s → `RESPONSE_UNDELIVERED`.
4. **A40 503** tồn tại trong code nhưng fired 0 lần — contention surface ở background outbox consumer, không bao giờ tới generic handler. Client không phân biệt được "server stall" với "call đang chạy".
5. **Event loop sống** suốt incident (/health 200, dispatch tiếp tục). Vụ này có 0 call dài (không ocr_map, các call kẹt đều là read nhỏ) → cơ chế async/job (funnel timer) có 0 call convertible, và stub cũng đi qua cùng response path.

### 2.1. Tần suất ledger silent-drop theo ngày (đo lại 2026-10-12)

Đếm dòng `write ledger persistence failed` trên cả 4 file log còn giữ (tổng 258):

| Ngày | Số lần | Ngày | Số lần |
|---|---|---|---|
| 2026-08-19 | 4 | 2026-09-26 | 3 |
| 2026-08-24 | 1 | 2026-09-27 | 41 |
| 2026-09-08 | 2 | 2026-10-08 | 53 |
| 2026-09-11 | 2 | 2026-10-09 | 2 |
| 2026-09-16 | 4 | 2026-10-10 | 52 |
| 2026-09-22 | 1 | 2026-10-11 | 37 |
| 2026-09-23 | 1 | 2026-10-12 | 21 |
| 2026-09-24 | 34 | | |

- File log hiện tại (`orchestrator.log`) một mình cho 2026-10-11=4, 2026-10-12=21; phần còn lại của 10-11 (33) và 10-10 (47) nằm trong `orchestrator.log.1` (rotate lúc 10-11 18:47).
- Coverage: chỉ còn 4 file (`.log` → `.log.3`, entry sớm nhất 2026-08-19); các ngày trước đó đã rotate mất — mọi số trên là cận dưới.
- Kết luận "ledger mất row khi nghẽn" giữ nguyên. Tần suất cũ "21–25 lần/ngày" là sai — thực tế dao động mạnh theo ngày (1–53 trong các ngày có log), chronic theo nghĩa lặp lại suốt 2 tháng.

## 3. Root causes đã verify (defects, không phải trigger)

1. **Ba write path không coordination trên một SQLite lock domain** (P-C1): SessionWriter 750ms + ledger 100ms + outbox consumer — budgets lệch 7.5×, không arbitration → bất kỳ lock hold nào cũng thành multi-minute stall toàn server.
2. **Ledger silent-drop trên lock domain đang nghẽn** (P-C2): cơ chế an toàn của write (`write_status`) yếu nhất đúng lúc cần nhất; chronic theo ngày (xem §2.1).
3. **A33 interaction defect** (P-C3): bỏ deadline để bảo vệ "slow-but-progressing writer" (rationale có thật, đo được p50 860ms) nhưng wedge detector không phân biệt "slow" với "livelocked-failing" → holdup thành unbounded stall.
4. **Retry không circuit breaker** (P-N4): amplify transient holdup thành sustained storm, đồng thời nuốt signal 503 mà contract hứa.
5. **Durable writes no-deadline trên request path** (P-N1, P-N2, P-T1): read-token upsert, session gate, `_log_session_event_safe` awaits — response không thể return cho tới khi chúng xong.

## 4. Cái gì KHÔNG phải nguyên nhân (đã loại)

- ocr_map chạy lâu: 0 call trong window (6/6 agents).
- Event loop bị pin: loop sống, /health 200.
- Gradual overload: signature là cliff (burst release khi lock clear), không phải slowdown curve; load bình thường.
- durable_audit.db: ledger và session events đều nằm trong sessions.db (chuỗi import đã verify 4 lần).
- MCP second process: weakened mạnh (code nói MCP không mở session DB, mâu thuẫn comment A28 cũ).
- Retention prune / checkpoint / VACUUM: 0 dòng log trong window; prune boot-only.

## 5. Còn mở (UNRESOLVED)

- **P-H — Lock holder là ai?** 6/6 agents không xác định được. Thứ tự nghi phạm (theo lệnh anh, in-process trước external):
  1. **In-process sqlite3 implicit-BEGIN:** `sqlite3` với `isolation_level` mặc định (`""`) tự mở BEGIN trước mỗi INSERT/UPDATE/DELETE và giữ write lock tới khi `commit()` — một code path DML bị chậm hoặc exception mà không tới được commit/rollback là đủ giữ khóa nhiều phút. Đã quét live source 2026-10-12: chỉ 3 file chạm `_SESSIONS_DB` (`session/store.py`, `session/logs.py`, `tools/write/core.py`) + 4 site ledger trong `orchestrator_server.py` (`code_tools.py` có connects nhưng không chạm sessions.db).
     - `_apply_session_schema` (`store.py:1030`, UPDATE sessions ở :1081): **đính chính giả thuyết "chạy mỗi open_session"** — chỉ chạy 1 lần/process (guard `_SESSION_DB_INITIALIZED_PATHS`, :1267–1294), commit ở :1228, rollback ở :1232, retry loop backoff khi lock. Vẫn là write path thứ tư ngoài SessionWriter, nhưng chỉ plausible ở boot — không giải thích được hold 3.5 phút lúc 15:31 nếu không có restart trong window.
     - 4 site ledger (`orchestrator_server.py`): :2701 upsert = DML + `conn.commit()` ngay; :2849/:2885 = SELECT thuần; :2935 retention = DELETE + `commit()` ngay. Không site nào set `isolation_level` (mặc định implicit-BEGIN) nhưng DML nào cũng commit ngay trong cùng call → không giữ lock phút nào trừ khi chính COMMIT bị kẹt (đó là symptom, không phải cause). "Đều SELECT" ở bản trước là sai — đính chính thành 1 upsert + 2 SELECT + 1 DELETE.
     - Còn lại đều read-only: `write/core.py:1040` (SELECT qua `_with_sessions_db`), `logs.py:340` (SELECT + close), `store.py:973` (`PRAGMA query_only=ON`), `_open_connection` (:507) dùng `isolation_level=None` (autocommit, không implicit-BEGIN).
     - Kết luận quét: chưa tìm thấy code path DML nào giữ lock qua slow/exception window trong live code. Ứng viên in-process vẫn mở nhưng chưa có bằng chứng trực tiếp — cần instrumentation ở G0 để bắt quả tang.
  2. **External file-level locker:** backup/antivirus/sync trên file ~340MB (đã loại MCP, prune, checkpoint; retention prune boot-only).
  - Evidence cần: **instrumentation 2 lớp ở G0** (xem §6).
- **P-N3 — Burst reads có gây ra holder không?** Correlation đã verify (29 và 28 POST 200 ngay trước 2 onsets), causation chưa established. Không dùng làm root cause.
- **P-T1 apportionment**: ba awaits + P-N1 + P-N2 đều là no-deadline durable waits; tỷ trọng 211s giữa chúng chưa đo được statically — không ảnh hưởng verdict, chỉ ảnh hưởng thứ tự ưu tiên fix.

## 6. Đề xuất fix scope (từ evidence, chưa phải lệnh implement)

- **P0 — Tách persistence khỏi response path** (outbox/spool off return path): chữa P-N1/P-N2/P-T1 — response không chờ durable COMMIT nữa. Đây là S1 của plan.
- **P1 — Ledger durable retry/spool hoặc qua coordinated writer**: chữa P-C2 chronic; giữ tính off-loop (`to_thread`) và "ledger không bao giờ break write".
- **P2 — Circuit breaker + làm A40 503 reachable**: chữa P-N4; fail-fast có backpressure thay vì stall câm.
- **P3 — Per-agent concurrency + admission**: capacity (từ plan).
- **Instrumentation 2 lớp = step G0** (SQLite không cho biết connection nào đang giữ khóa, nên "log holder khi busy_timeout fire" không làm được trực tiếp):
  - *Trong process:* bọc mọi `sqlite3.connect` tới sessions.db bằng một factory; một watchdog thread mỗi 1s kiểm tra connection nào có `in_transaction=True` quá 2s thì log stack của chỗ đã mở transaction đó. Chi phí thấp, chỉ đọc → chạy được trên [A] live.
  - *Ngoài process:* khi `lock_hold` vượt 30s, gọi `run_ps1_script` liệt kê process đang mở sessions.db (`handle.exe` nếu máy có, không thì Resource Monitor), đồng thời kiểm tra Defender exclusion và các tool sync/backup đang chạy trên `.orchestrator_cache`.
  - Nếu sau một đợt nghẽn thật mà vẫn không ra holder → G0 đóng với kết luận UNRESOLVED, không chặn Phase 1 (Phase 1 gỡ việc chờ khỏi response path nên đúng bất kể ai giữ khóa).
- **P2 circuit breaker — giữ bất biến A20/A33:** breaker chỉ được (a) ngừng retry liên tục, (b) đẩy event vào spool, (c) trả 503 SERVER_BUSY nhanh cho caller. Breaker KHÔNG được bỏ event, KHÔNG được trả event_id rỗng như thể đã ghi xong. Ledger phải đi vào spool hoặc qua writer có điều phối (P1). Không tăng budget 100ms.
- **Thứ tự**: P0 (persistence khỏi response) → P1 (ledger) → P2 (breaker + 503 tới được client) → P3 (admission), rồi mới tới job/async (P-C5 đã chứng minh job mechanism không cứu được failure class này).

## 7. KHÔNG làm

- **Không tăng busy_timeout** để "fix": chỉ kéo dài mọi stall, không tìm ra holder (cả E và H đều cảnh báo).
- **Không coi async/job là fix cho nghẽn**: P-C5 đã verify — 0 convertible call, stub cùng fate với result.
- **Không load test / fault injection / checkpoint trên DB live**: E và G đều ghi nhận quy tắc này; fault injection chỉ trên môi trường cô lập.

## 8. Đối chiếu với Plan TOOLA_1210

Audit này ground trực tiếp cho S0–S2 của plan: S1 (audit off response path) = P0 trên; S1.6/S2.2 (retry + 503) = P2; lock-holder instrumentation = điều kiện G0. Phần S3–S4 (job/async) đứng sau P0–P2 theo P-C5. Phase 3 (job) giữ trong plan nhưng phạm vi đúng là ca read dài khi session chết (ca Codo ban đầu), không phải để chữa nghẽn — P-C5 đã verify stub đi chung response path với result nên job không cứu được loại lỗi này. Thread làm rõ plan đã giao CA khác (lệnh anh 19:33) — report này chỉ cung cấp evidence, không thay thế quyết định §8.
