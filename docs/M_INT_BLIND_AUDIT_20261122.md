# M-INT — blind audit báo cáo nguyên văn + provenance

**Đây là văn bản do auditor viết, khôi phục nguyên văn từ transcript — không phải bản viết lại.**
Bên sửa (leader Ope) chỉ thêm khối header này và PHỤ LỤC cuối file; thân báo cáo giữa hai mốc giữ
byte-for-byte (đã kiểm: chuỗi 27662 B của nguồn nằm nguyên trong file).

| | |
|---|---|
| Auditor | session `ses_dff953d73ffeMoEiiqUTL2B1ZV`, agent `bugs`, model `qwen4-max-sale` (kira-ai, variant `high`), title "Blind audit M-INT packet" |
| Brief duy nhất | `C:\Users\ADMIN\AppData\Local\Temp\opencode\M_INT_BLIND_PACKET.md` — 4311 B, 66 dòng, sha256 `9e9ffb65682a1242cee1c3eccf01df515ebf96ee0691fae03ea69690e20f30dc` |
| Báo cáo nguồn | `session_message` seq 1585, block `text` cuối: 25366 ký tự, 143 dòng, sha256 `00663085a6aace5c01d8f3d65dd107bf2bb46db9946acfd78b88ab52dbf1d40d`, kết thúc bằng `END_AGENT_REPORT:M-INT` |
| Bản tạm trong Temp | `C:\Users\ADMIN\AppData\Local\Temp\opencode\m_int_auditor_report_verbatim.md` — 27662 B, sha cùng `00663085a6aa…`, chép sang bằng `copy_file` (`overwrite:false`, hash khớp) chứ không gõ tay |
| Vòng đời | tạo 06:22:06 → compaction tự động 06:48:42, 07:19:08 → fail 402 `package_quota_exhausted` 07:26:57 → resume 08:10:27 → compaction 08:18:06 → idle `succeeded` 08:40:33 |
| Chi phí | 1 556 140 token vào / 478 095 ra / 15 217 010 cache-read |

## Tính độc lập của cuộc audit này — những chỗ phải khai

- Rationale không lọt vào auditor: cả hai message user nó nhận (06:22:06 và 08:10:27) đều được kiểm toàn văn — không chứa tên defect, tên fix, hàm `host_ok`/`_asset`, hay số cổng nào ngoài 8083 trong path. Message resume chỉ nhắc lại ràng buộc read-only/tự-sinh-bằng-chứng và thứ tự ưu tiên Step. Không có tài liệu giải thích "tại sao code viết vậy" được đưa cho nó.
- Ba compaction tự động (`reason: auto`) đã tóm tắt giữa chừng. Nội dung các summary vẫn nằm trong phạm vi brief + bằng chứng tự thu, không chứa lời lẽ bên sửa; nhưng đây là lý do chính đáng để nói rằng trạng thái ngữ cảnh của auditor ở seq 1585 không phải trạng thái thô lúc 06:22.
- Auditor vi phạm một phần tính "tái sinh bằng chứng" theo cách có lợi cho nó: nó tự đo mọi con số (bảng "Lệch giữa packet và điều tôi đo", :129-:139) và báo khác biệt thay vì tin packet — ví dụ pytest 23.49 s chứ không 30.53 s, và `evdetail?event_id=1` trả 400 là do sai tên tham số của packet chứ không phải lỗi API.
- Bước mà audit KHÔNG tự làm được: section-error isolation khi DB hỏng (:22) không quan sát được trên runtime vì ToolQ `:8082` đang chạy; cap 16 MiB chưa từng kích hoạt live (lớn nhất đo được 1 162 953 B = 1.109 MiB tại `history?limit=2000`). Các mục này nằm trong "not verified" của nó, không phải pass.
- Nghi án S9 (`MONITOR_TOOLA_REGISTRY` hỏng) do auditor tự rút sau khi đo: registry 14350 B, 11 agent khớp live. Giữ lại đây vì quá trình rút lui là một phần bằng chứng.
- Phát hiện nặng nhất của audit (S1: chuẩn được viết *sau* code 25 phút) ban đầu tựa vào `docs/TOOLA_WEB_UI_CAMPAIGN_BASELINE.md.bak` 4132 B / `9cdcd1c31da8`. Ảnh đó đã bị chính các lần viết caveat của bên sửa đè mất (`write_fix` chỉ giữ một thế hệ `.bak`). Kết luận S1 đứng lại được là nhờ chuỗi dry-run — nguồn mà bên sửa không xoá được — xem caveat trong baseline và PHỤ LỤC.

---

Đã đóng toàn bộ bằng chứng. Dưới đây là báo cáo.

---

# BLIND AUDIT — M-INT: Monitor integration trên ToolA :8083 vs `docs\TOOLA_WEB_UI_CAMPAIGN_BASELINE.md`

Mọi con số dưới đây do tôi tự sinh (git, HTTP, netstat, Chromium/Playwright, pytest, `sessions.db`/`durable_audit.db` đọc `mode=ro`). Không sửa file, không git write, không restart. Số liệu packet chỉ dùng để **đối chiếu**, chỗ nào lệch tôi báo ở mục 4.

## 1. Bảng verdict từng điều khoản

### Step 1 — diff thật là gì

| Câu hỏi | Verdict | Bằng chứng |
|---|---|---|
| `--stat` 21155/21146 có mô tả đúng thay đổi? | **FAIL** (là noise) | Worktree `orchestrator_server.py`: CRLF=21167, bare-LF=0. HEAD blob (`git show HEAD:orchestrator_server.py`): 21162 dòng, CRLF=65 → mixed. `core.autocrlf=true`, không `.gitattributes` → toàn bộ file bị chuẩn hoá CRLF, git tính mỗi dòng là 1 cặp −/+ |
| `--ignore-all-space` mô tả đúng? | **PASS** | `git diff --ignore-all-space --numstat HEAD` → `8 2 orchestrator_server.py`, `3 0 path_runtime.py`. Difflib (CR-normalized) = 4 hunk: :6818 `handle_root`→`decorate_page(...,"main")`, :15331/:15334 `handle_council_dashboard`→`decorate_page(...,"council")`, :20512-20514 `create_app`→`register_web_routes(app)`; `path_runtime.py::resolve_ui_html` thêm bundled early-return |
| HEAD / trạng thái repo | — | HEAD `dd41e2fc8f78761f8f7b9a104fb5cd98fdf1cad0` (main); `rev-list --left-right --count main...HEAD` = `0 0`; porcelain = 0 staged / 8 unstaged / 6 untracked |

### Step 5 — điều khoản chuẩn

| Điều khoản (dòng chuẩn) | Verdict | Bằng chứng chính xác |
|---|---|---|
| **:9 Main** — parent `UI/8. AGENT TOOL - READ WRITE TEST_UI.html` *matches live HTML ignoring newline style* | **FAIL** | Normalize newline+BOM: bundled `web_ui/main.html` == parent **TRUE**; parent vs **live `/`** → khác +3/−1 dòng (`decorate_page` :48 thêm `data-toola-page="main"`, :49 thêm `<link … /assets/toola/theme.css>`, :47 thêm `<nav class="toola-nav">`). Claim đúng với file bundled, **sai với live HTML** |
| **:9 Main** — theme `toolC_theme`, folder, tier (T1 active after click), sessions, inspector, logs | **PASS** (existence-only cho sessions/inspector/logs) | Live Chromium 1280x800: `localStorage.toolC_theme="dark"`, `body bg rgb(8,9,11)`; `main.html` :2086/:2173 `toolC_theme`; folder 20 node; click `text="T1"` → class `"mode-tab t1"` → `"mode-tab t1 active"` (**changed=true**); sessions/inspector/log element tồn tại (`main.html` :379-390 `id="log…"`), 0 pageerror |
| **:10 Council** — preserve readiness placeholder | **PASS** | `orchestrator_server.py` :15306-15328 `if not SERVER_READY:` → "Council starting" + `refresh content="2"` + "Wait for startup_phase=ready". Cluster diff :15331/:15334 chỉ chạm `decorate_page` → placeholder không đổi. Live `SERVER_READY=true` nên không hiển thị (xem mục 4) |
| **:10 Council** — preserve local/origin checks | **PASS** (preservation) | :15303 `_is_local(request) or _origin_ok(request)`; định nghĩa :1906-1909 / :1912-1917 **không nằm trong diff** → giữ nguyên. Live: `Origin=https://evil.invalid` → **403**, `Origin=http://localhost:8083` → 200 |
| **:10 Council** — preserve existing Council controls | **PASS** | `/council` 200, 130314 B, title "Council Dashboard", 61 control hiển thị, 0 pageerror; `dashboard.py:24 render_council_dashboard` (2234 dòng) |
| **:11 Monitor** — D3 loaded | **PASS** | Live `/monitor`: `typeof window.d3 === "object"`, `d3.version="7.9.0"`, `<script src="/assets/toola/d3.min.js">`, 2 inline `<svg>` |
| **:11 Monitor** — Graphity storage namespace | **PASS** | `web_views.py` :367-368 → `55. GRAPHITY\graphs\<repo>\graphify-out\graph.json`; toola 17000028 B, toolq 14013439 B **đều tồn tại**; live `graph?repo=toola` 200 (11938 symbols), `repo=toolq` 200 (332545 B) |
| **:11 Monitor** — traffic / history / detail | **PASS** | Live: `activity` 200 129556 B, `history?limit=3` 200 2026 B, `events/dbd9fb523b674f48b312396aedde6d0f` 200 `{"state":"applied"}`, `evdetail?id=<real>` 200 |
| **:11 Monitor** — offline states | **PASS** (code) / **UNVERIFIABLE** (live) | `monitor.html` :3862 `stTxt()` map `never`→"CHƯA CÓ DỮ LIỆU", `expired`→"KHÔNG KHẢ DỤNG", `stale`→"trễ"; :3860/:4327/:4461 `section_errors`; :3872 `sev={fresh:0,stale:1,expired:2,never:3}`; :2402 `⚠ degraded`/`MẤT KẾT NỐI`. Literal "offline"/"unavailable"/"unreachable" = **0**. Live mọi source `ok=true` → không quan sát được state lỗi |
| **:11 Monitor** — wire format based on `63. MONITOR/bridge.py` | **PASS** | `bridge.py::_activity_payload` (char 41694) và `web_views.py` (char 44679): **30 key, set key giống hệt nhau** |
| **:12 Matrix** — parent `toolA_function_matrix.html` untracked, original unchanged | **PASS** | `git -C .. ls-files --error-unmatch` → rc=1 (untracked); 136 row `write_operations` cho file này, **LAST = 2026-11-18 13:45:56**, 0 row sau campaign start 11-21 13:53:53; mtime 11-18 13:45:57, sha256 `6f96416b53a81df1` |
| **:12 Matrix** — Inventory, chains, gaps, automation, Radial | **PASS** | Live tab `[data-tab]` = mindmap/inventory/chains/**gaps**/automation/**sunburst(label "Radial")**; `matrix.html` :342 tab "Gap Analysis", :451-453 `#panel-gaps>#gaps-container`, :176-181 `.gap-card`; `matrix.js` :464 "// Gaps", :470-471 `gapsEl.innerHTML`; 0 pageerror |
| **:12 Matrix** — 260 snapshot identifiers match `/api/tools` at baseline | **PASS** | `matrix.js` `TOOLS` = **260 unique**; live `/api/tools/names?curated=false&limit=400` → `count=233`, `hidden_from_discovery=27`; 233+27=260; **live−matrix = 0**, matrix−live = 27 (đúng tập hidden) |
| **:12 Matrix** — D3 is not required by existing SVG graph | **PASS** | Live `/matrixtool`: `typeof window.d3 === "undefined"`; `matrix.js`/`matrix.html` = **0** tham chiếu `d3.`; sunburst render 607 node, container 728 px |
| **:12 Matrix** — Radial search opens with bridge live | **PASS** | Click `[data-tab="sunburst"]` → `panel-sunburst.active`, `#sb-search` visible; gõ `read_file_range` → value đúng, 607 node, không lỗi. Bridge live: 3 gọi `/api/monitor/activity` trong 5 s đầu (full → `?since_seq=22765&boot_id=42032-…` → `since_seq=22767`), tất cả **200**. Mindmap `#mm-search` → `#mm-hits` 9 row, status "9 / 10 kết quả" |
| **:14** Computed Council tokens @Chromium 1280x800 | **FAIL (một phần)** | Khớp: bg `#08090b`, panel `#101216` (`section.broker`/`.agent-card`), text `#f3f4f6`, palette muted `#a4aab4` (117 element), control radius `7px` (30/61 control — dominant), body sans = `"Segoe UI Variable","Inter Variable",Inter,…`, `pre` mono chính = `"JetBrains Mono",…`. **Không khớp**: selector `.muted` → `#707783` (25 element); 4 `pre` → `ui-monospace,"Cascadia Code",…` (không có JetBrains Mono đầu); `#broker-text` → generic `monospace`; `#narrator-prompt` → sans stack; 3 button (`#clear-council-session`, `#broker-send`, `.copy-button`) → `Arial`; chỉ **8/61** control bắt đầu bằng sans stack đã nêu → câu "Earlier CSS declarations were overridden in the live page" chỉ đúng một phần |
| **:16** — `/monitor` + `/matrixtool` returned 405 before implementation | **PASS** (cơ chế) | HEAD blob: `/monitor` **ABSENT**, `matrixtool` **ABSENT**, `web_views`/`register_web_routes` **ABSENT**; có `add_route('OPTIONS','/{tail:.*}',handle_options)` @20592 → GET không khớp method trên path có OPTIONS → 405. Live tái hiện: `GET /zzz_not_registered` → **405** `405: Method Not Allowed`, `OPTIONS` cùng path → 200 (`Allow=None`) |
| **:16** — Both servers own `/api/health` | **PASS** | `:8083/api/health` 200 6174 B (== `/health`); `:8082/api/health` 200; `web_views.py` :369-370 `toola_health`/`toolq_health` |
| **:16** — `:8099/api/activity` returned full snapshot then delta, same boot ID, nondecreasing seq | **UNVERIFIABLE** | `netstat -ano -p tcp` → chỉ `127.0.0.1:8083` + `[::1]:8083` LISTENING pid 42032; **không có :8099**. Không probe được, không được restart/d khởi động lại service |
| **:16** — Activity measures calls, not A-to-B dependencies | **PASS** (structural) | 30 key payload (bridge ≡ web_views) không có key dependency/edge/from/to; `dispatch` :486-495 build events từ `durable_audit_outbox` row (`tool_name`,`status`,`session_id`) |
| **:16** — Source: `bridge.py::_activity_payload` + existing Matrix `liveCycle` | **PASS** | `bridge.py::_activity_payload` tồn tại; `matrix.js` :2520 `async function liveCycle()`, :2631 gọi, :2632 `setInterval(liveCycle,5000)`; parent cũng có `liveCycle` ×3 |
| **:20** — serves `GET /api/monitor/{activity,health,graph,history,evdetail}` + `/api/monitor/events/{event_id}` | **PASS** | `register_web_routes` :1468-1474 (2 `add_get` cho API + 2 page + 1 asset + `cleanup_ctx`); `_QUERY_RULES` :28-34 đúng 5 endpoint; :1425-1429 nhánh `event_id`. Live cả 6 → 200 |
| **:20** — loopback + local Host + matching Origin | **PASS** (route đã nêu) / **FAIL** (route asset) | `_require_loopback` :57-65. Live API/page: `Host=evil.com`→**403**, `Origin=https://evil.invalid`→**403**, `Host=localhost:8083 + Origin=http://localhost:8083`→**200**, `Sec-Fetch-Site: cross-site`→**403**, `same-origin`/`none`/absent→200. **Nhưng** `_asset` :82-86 **không gọi** `_require_loopback` → `GET /assets/toola/theme.css` với `Host=evil.com` **và** `Origin=https://evil.invalid` **và** `Sec-Fetch-Site: cross-site` → **200, 5110 B** (matrix.js, d3.min.js cũng 200). Điều khoản :20 chỉ liệt kê route API → tính PASS cho :20, lỗi asset đưa vào mục 2 |
| **:20** — query allowlist excludes graph refresh + unrestricted file metadata | **PASS** | Live: `graph?refresh=1`→**400**, `/api/monitor/filemeta`→**404**, `activity?window_s=300`→400, `activity?limit=50`→400, key lạ→400, `boot_id=a&boot_id=b`→400, 201 key→400, `evdetail?id=`×201 ký tự→400, `events/<129 ký tự>`→400, `events/<128>`→200/404, `repo=bad`→400, `repo=toola&repo=toolq`→400. Code :89-103. Test :224-241 |
| **:20** — Responses are limited to 16 MiB | **PASS** (code+pytest) | `_MAX_BYTES = 16*1024*1024` :37; cưỡng chế :1449 `status not in {200,400,404} or len(body) > _MAX_BYTES` → 502. pytest `test_response_limit_is_inclusive` :295-301 → đúng 16 MiB = 200, +1 byte = 502 `MONITOR_BRIDGE_UNAVAILABLE`. Live max đo được **1.109 MiB** (`history?limit=2000` 1162953 B) → cap không được kích hoạt live |
| **:20** — expired sequence / changed boot ID → full activity snapshot | **PASS** | Live: `since_seq=cur-1` → `delta=True truncated=False events=2`; `since_seq=999999999`, `since_seq=0`, `boot_id=deadbeef-1`, boot sai+seq đúng → `delta=False truncated=True events=240` (full). pytest :197-202. `since_seq=abc` → 200 fallback full |
| **:22** — one Monitor poller through aiohttp startup | **PASS** | :1382 `ThreadPoolExecutor(max_workers=1)`, :1387 `create_task(self.poll())`, :1474 `app.cleanup_ctx.append(_monitor_client)` (generator :1460-1465 chạy tới `yield` lúc startup). Live: một `boot_id` `42032-1795277694524635200`, `snapshot_seq` tăng 16900→19191→22767 trong phiên đo; `poller_errors` consecutive=0/total=0 |
| **:22** — waits for in-flight work before closing read-only SQLite conns | **PASS** (code+pytest) | `close()` :1408-1417: `stopping=True` → `wake.set()` → **`await self.poller` trước** → `await self.run(self.store.close)` → `executor.shutdown(wait=True, cancel_futures=True)`. `store.close` :409-412 đóng conn. `rows()` :398 `as_uri()+"?mode=ro"` + :400 `PRAGMA query_only=ON`. pytest `test_readonly_and_cleanup_waits_for_inflight_poll` :340-383 assert `order == ['poll','close']` + `monitor.poller.done()` |
| **:22** — uses `ORCHESTRATOR_DURABLE_AUDIT_DB` when set, else `durable_audit.db` next to `ORCHESTRATOR_SESSIONS_DB` or default cache | **PASS** | :361-364 đúng chain đó (`self.sessions_db.with_name("durable_audit.db")`). Live default `.orchestrator_cache\durable_audit.db` → `durable_audit_outbox` 119121 row, `sources.toola_audit.ok=true`. pytest :92-96 patch đủ 6 env |
| **:22** — `MONITOR_TOOLA_CACHE`/`MONITOR_TOOLA_REGISTRY`/`MONITOR_TOOLQ_DB`/`MONITOR_GRAPH_DIR` allow overrides | **PASS** (code+test) | :360 / :365 / :366 / :367 đọc cả 4. pytest :93-94 set cả 4 trong cùng fixture. Live không tách được nếu không restart → xem mục 4 |
| **:22** — unavailable DB → section errors while other data remains readable | **PASS** (code+pytest) / **UNVERIFIABLE** (live) | `rows()` :403-407 lỗi → `[{"_error":…}]`; state :385 `section_error`, :472/:939/:970/:986-1025; `last_ok_at` :382/:458/:666/:673 giữ last-good. pytest `test_db_errors_keep_last_good_data_and_pages_available` :256-292 assert `section_errors['events'] is not None` **và** `['sessions'] is None` **và** page vẫn 200 + `assertNotIn(':8099', page.text())`. Live: ToolQ :8082 **đang chạy** (`/health` → 200, `sources.toolq.ok=true`, `section_errors` toàn null) → không quan sát được |
| **:22** — legacy :8099 service is optional | **PASS** | `web_views.py` và `matrix.js`: chuỗi `8099` = **0**. netstat không có :8099. Monitor vẫn 200 trên mọi endpoint → tính optional chứng minh được live |
| **:26-:33** Git checkpoint (6 dòng) | **FAIL** | Live (`rev-list` / staged/unstaged/untracked): project-orchestrator `0/0` **0/8/6** (doc `0/4/1`); fb1-g3b `32/0` **0/1031/0** (doc `29/0`); `%TEMP%\t4wt_deploy_gate` `39/0` 0/1020/0 (doc `36/0`); `8. ToolA\t4wt_deploy_gate` `39/0` 0/7/5 (doc `36/0`); `wt_codo_t4_20261112` `29/0` 0/2/2 (doc `26/0`); `wt_codo_upgrade_20261105` `60/0` 1/2/1 (doc `57/0`). → 5/6 dòng commit lệch **+3**; dòng counts project-orchestrator lệch **+4 unstaged / +5 untracked** |
| **:35** "Monitor main was clean" | **FAIL** (nghĩa git) | `rev-list main...HEAD = 0 0` (không phân kỳ) nhưng working tree = **8 modified + 6 untracked**, 0 staged |
| **:35** parent có gitlink + pre-existing staged/unstaged/untracked | **PASS** | `git -C .. ls-files -s project-orchestrator` → `160000 3e053cb12a5b52b9d7fd839f00092c73594bf528 0 project-orchestrator`; parent porcelain 49 dòng = **6 staged / 8 unstaged / 35 untracked** |

## 2. Lỗi thật, xếp theo severity

**S1 — HIGH (audit integrity): hai điều khoản chuẩn được viết SAU code đã có hành vi đó.**
`web_views.py` patch 21:53:41 (`dry_8b5d1d223cef4e7d9d834702e5d90514.json`, target lines 1-146, `target_file_hash b369d95e36526605`, 1509 dòng) đã chứa `_MAX_BYTES = 16 * 1024 * 1024` (1), `len(body) > _MAX_BYTES` (1), `await self.poller` (1), `cancel_futures` (1), `cleanup_ctx.append` (1). Câu "Responses are limited to 16 MiB" và "waits for in-flight work before closing…" chỉ xuất hiện ở **write_fix 22:18:29** (doc :20/:22) — `.bak` (pre-22:18) và patch doc 21:59:23 đều đếm **0**. Hệ quả: mọi verdict cho 2 điều khoản này là "code khớp với văn bản được viết ra để mô tả chính nó", không phải "code khớp với chuẩn có trước".

**S2 — MEDIUM: `/assets/toola/{name}` không có gate nào.**
`web_views.py` :82-86 `_asset` chỉ check allowlist `_ASSETS` rồi `web.FileResponse`, **không gọi `_require_loopback`**. Live: `theme.css`/`matrix.js`/`d3.min.js` → 200 với `Host=evil.com` + `Origin=https://evil.invalid` + `Sec-Fetch-Site: cross-site` (5110 B / 451703 B). Cùng lúc API và page cùng surface → 403. Không test nào bọc case này (`tests` :119-148 chỉ kiểm membership allowlist, :233-235 chỉ Origin/Host/Sec-Fetch trên route API). Giới hạn mức độ: bind chỉ loopback (netstat) và payload là static UI không mật → MEDIUM, không phải critical.

**S3 — MEDIUM: điều khoản :9 Main sai theo nghĩa đen.**
"parent … matches live HTML ignoring newline style" — parent ≠ live HTML (+3/−1 do `decorate_page`). Đúng với `web_ui/main.html` so với parent, sai khi so với **live**. Chuẩn dùng chính từ "live HTML" → đây là lỗi phát biểu của chuẩn, không phải lỗi code.

**S4 — MEDIUM: điều khoản :14 computed tokens chỉ đúng một phần.**
`.muted` → `#707783` chứ không phải `#a4aab4`; 4 `pre` và `#broker-text` không bắt đầu bằng `JetBrains Mono`; `#narrator-prompt` dùng sans stack; 3 button dùng `Arial`; 53/61 control không bắt đầu bằng sans stack đã nêu. Mệnh đề "Earlier CSS declarations were overridden in the live page" vì thế không đầy đủ — vẫn còn declaration chưa bị override.

**S5 — MEDIUM: bảng Git checkpoint (:26-:33) và câu "Monitor main was clean" (:35) không còn đúng.**
Lệch +3 commit trên 5/6 worktree và +4 unstaged / +5 untracked trên project-orchestrator. Đây là bảng số liệu chuẩn hoá được nêu làm điều khoản, nên sai số = FAIL điều khoản.

**S6 — LOW-MEDIUM: `_HOST` (:35) không ghim port.**
Regex `(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?` → `Host=localhost:9999` **được chấp nhận** (live 200 trên API). Cùng loại: `/council` (handler cũ :15303) **không check Host** → `Host=evil.com` không Origin → 200 130314 B. Không phải regression (code cũ, chuẩn chỉ yêu cầu "preserve"), nhưng tạo bất nhất trên cùng một surface.

**S7 — LOW: mọi internal exception bị ánh thành 502 `MONITOR_BRIDGE_UNAVAILABLE`.**
:1455-1457 `except Exception: return _unavailable(502)` — lỗi nội bộ (store/JSON/serialize) được báo như lỗi upstream, sai chẩn đoán. Live chưa kích hoạt: `events/1` → 404 `{"error":"not_found"}`, `events/nope-zzz` → 404 (status đi qua :510 đúng).

**S8 — LOW: vệ sinh surface untracked.**
`orchestrator/web_ui/` ship `*.bak` cạnh file thật (`matrix.html` 503 vs 497 dòng, `matrix.js` 2633 vs 2626, `monitor.html`, `theme.css` 76 vs 73; mtime `.bak` == current) và một **nested `.orchestrator_cache/search_snapshots` bên trong web root**. Repo-root có file rác `=ro` (0 B, 11-21 18:08:54) — artifact của redirect shell, nằm ngay trong tập untracked mà checkpoint đếm.

**S9 — đã loại khỏi danh sách defect (tự sửa sai lầm trước đó).**
Nghi án `MONITOR_TOOLA_REGISTRY` default trỏ path không tồn tại là **SAI**. `agents_registry.json` **tồn tại** tại `project-orchestrator\agents_registry.json` (14350 B, 11 agent); `cache.parent` (:360/:365) chính là `project-orchestrator` → :365 resolve đúng. Live `registered_agents` = 11 = đúng 11 key trong registry (`clau, claultnai, codo, fbuf, gro, hermes, muse1, muse2, opencode, vito, zcode`). Không có fallback ẩn.

**S10 — LOW: `bundled matrix.js` không phải bản sao nguyên văn của parent.**
Parent 2424 dòng vs js 2634, **42 hunk**, hunk đầu = `parent[0:290]` bị bỏ hoàn toàn; `Radial` 9 → 6; `liveCycle` 3 = 3. Điều khoản :12 gắn "(untracked, original unchanged)" cho **parent** → parent đúng là không đổi (S-bảng :12 PASS), nhưng phát biểu dễ bị đọc nhầm thành "bản bundled = parent". `TOOLS`/`SB_LINKS`/`SB_AUTO`/`CHAINS`/`SKILLS` thì **identical** (`SB_LINKS` 30/30 có `q`, 16/30 có `c`; `SB_AUTO` 16/16 có `c`).

## 3. Nhận định Step 6 — chuẩn có trước code hay không

**Không. Chuẩn không hoàn toàn có trước; ít nhất hai điều khoản cốt lõi được viết sau khi code đã mang hành vi đó, và chuẩn đóng băng trong khi code còn đổi tiếp ~1,5 giờ.**

Thứ tự tôi tự dựng từ `sessions.db::write_operations` (51327 row, `started_at` là **epoch float** — mọi truy vấn so ISO string sẽ âm tính giả, đó là bẫy đo của chính bước này) cộng `.orchestrator_cache\dry_runs\*.json` (nguồn before-state duy nhất cho surface untracked, vì chúng không có trong Git):

| Thời điểm | Sự kiện | Nội dung quyết định |
|---|---|---|
| 11-21 **13:53:53** | `write_new_file` doc (4033 B, hash `fa4b6937a99743f2…`) | Lần đầu chuẩn tồn tại — **TRƯỚC** `web_views.py` 13:58:10 → phần này có trước |
| 13:56-14:08 | `main.html`, `monitor.html`, `d3.min.js`, `theme.css`, `web_views.py`, `path_runtime.py`, `orchestrator_server.py`, `matrix.js`, `matrix.html` | Code cụm đầu |
| 11-21 **21:53:41** | `write_fix web_views.py` 1-146 | **đã có** `_MAX_BYTES = 16*1024*1024`, `len(body) > _MAX_BYTES`, `await self.poller`, `cancel_futures`, `cleanup_ctx.append`; **không còn** `8099` |
| 21:57:16 | `write_fix web_views.py` (1471 dòng) | vẫn đủ các token trên |
| 21:59:23 | `write_fix` doc 3-20 | văn bản **chưa có** "16 MiB", **chưa có** "in-flight"; **chưa có** "expired sequence" |
| 22:00:45 | `write_fix` doc 3-20 | tạo ra trạng thái mà `.bak` lưu lại |
| 22:07-22:09 | 3× `write_fix web_views.py` | code tiếp |
| 22:10-22:17 | 4× `write_fix tests/test_web_ui_campaign.py` | test viết sau code |
| 11-21 **22:18:29** | `write_fix` doc **20-22** | **lúc này mới thêm** "Responses are limited to 16 MiB" và "waits for in-flight work before closing the read-only SQLite connections at cleanup" + câu fallback `durable_audit.db` |
| 22:19 → **23:51:39** | **15** write_fix nữa trên `matrix.html`, `matrix.js`, `tests`, `web_views.py` (cluster 23:11:29-35) | chuẩn đóng băng, code vẫn đổi |

Bằng chứng mạnh nhất là phép so sánh trực tiếp hai patch cùng ngày: **code mang cap 16 MiB và close-wait lúc 21:53:41; câu văn mô tả chúng xuất hiện lúc 22:18:29 — chậm 25 phút.** Diff `.bak` ↔ current chỉ khác đúng 2 dòng (:20, :22), tức toàn bộ phần còn lại của chuẩn đã ở trạng thái đó trước 22:18, nhưng hai mệnh đề tôi được giao để phán xét (cap + in-flight) thì không.

Hệ quả cho báo cáo này: verdict **PASS** ở dòng ":20 — 16 MiB" và ":22 — waits for in-flight work" phải đọc là *"code và test khớp với văn bản được viết ra sau khi code tồn tại"*, không phải bằng chứng độc lập rằng hành vi thoả một chuẩn có trước. Với các điều khoản còn lại (:20 allowlist/loopback/boot-id, :22 env overrides, :11/:12 rows) chuẩn có trước hoặc không bị sửa trong cụm 22:18 → verdict giữ nguyên giá trị.

Một giới hạn cần nói thẳng: văn bản doc tại 13:53:53 **không phục hồi nguyên văn** được — audit chỉ lưu hash `fa4b6937…` + 4033 B, và `durable_audit` body của `write_fix` chỉ có `start_line`/`end_line` (không có nội dung). Nên cột "có trước" cho **phần thân** doc 13:53 chứng minh được về thời điểm/kích thước, không chứng minh được từng câu.

## 4. Not verified (kèm lý do)

1. **`:8099/api/activity` full snapshot → delta, same boot ID, nondecreasing sequence (:16).** `netstat -ano -p tcp` chỉ có `127.0.0.1:8083` + `[::1]:8083` LISTENING pid 42032; không có listener :8099. Đây là claim lịch sử; không được khởi động service (mutation).
2. **Section-error isolation khi DB không khả dụng (:22) — trên runtime.** ToolQ :8082 **đang chạy** (`/health` → 200 `{"ok":true,"status":"degraded","service":"clm-librarian-api","port":8082}`; `sources.toolq.ok=true`; `section_errors` toàn null). Mọi source ok nên không có error để quan sát. Chỉ code+pytest-verified. (Khác với nhận định trước đó của tôi: ToolQ không hạ.)
3. **Poller startup/shutdown có hiệu lực trên process sống (:22).** Cần restart hoặc shutdown ToolA → bị cấm. Suy ra từ: một `boot_id` duy nhất + `snapshot_seq` đơn điệu 16900→19191→22767, `poller_errors.consecutive=0/total=0`, và pytest :340-383.
4. **Env overrides thực sự đổi nguồn trên process sống (:22).** Cần set env + restart. Chỉ có code :360-367 và fixture test :92-96.
5. **16 MiB cap bị kích hoạt live (:20).** Response lớn nhất đo được **1162953 B = 1.109 MiB** (`history?limit=2000`), headroom 14.89 MiB. Không tạo được payload vượt cap mà không đổi dữ liệu nguồn. Boundary chỉ chứng minh bằng pytest :295-301 (patch `store.dispatch`).
6. **Readiness placeholder hiển thị live (:10).** `SERVER_READY=true`; không được restart. Code path :15306-15328 đọc được, không chạy được.
7. **"Offline states" (:11) về mặt từ ngữ.** `monitor.html` không chứa literal `offline`/`unavailable`/`unreachable` (0 lần); state machine dùng `never`/`expired`/`stale`/`degraded`. Không kết luận được chuẩn định nghĩa "offline" là gì — đó là rationale, tôi không suy đoán.
8. **Văn bản doc nguyên thủy 13:53:53 (:20/:22 có trước hay không ở phần thân).** Chỉ có hash + kích thước; không có pre-image trong `dry_runs` cho `write_new_file` đó.
9. **Các con số checkpoint lịch sử (:26-:33).** Không re-observe được trạng thái 11-21 mà không `git checkout`/`stash` (bị cấm). Tôi chỉ đo được trạng thái **hiện tại** và báo lệch.
10. **`sessions`/`inspector`/`logs` trên `/` (:9) ở mức nội dung.** Element tồn tại + 0 pageerror, nhưng selector đếm row của tôi trả 0 → chỉ existence-verified, không verified về dữ liệu hiển thị.

### Lệch giữa packet và điều tôi đo (báo theo ràng buộc "tự sinh bằng chứng")

| Packet | Tôi đo | Ghi chú |
|---|---|---|
| pytest "17 passed, 31 subtests passed, **30.53s**" | **17 passed, 31 subtests passed, 23.49s** (wall 24.41s) | Counts khớp; duration lệch ~7 s. 0 FAILED, 0 SKIPPED. Playwright thật chạy (chromium-1217) → 5 test matrix không skip |
| probe `api/monitor/evdetail?event_id=1` | **400** `400: Bad Request` | Key allowlist là `id` (:33), không phải `event_id` → probe của packet dùng sai tên tham số, không phải lỗi API |
| probe `api/monitor/events/1` | **404** `{"error":"not_found"}` | id numeric không tồn tại trong `durable_audit_outbox`; `events/dbd9fb523b674f48b312396aedde6d0f` → 200 |
| probe `api/monitor/filemeta` | **404** (không phải 400) | Khớp test :226 và khớp "excludes … unrestricted file metadata" |
| probe `activity?window_s=300`, `?limit=50` | **400 / 400** | Đúng thiết kế allowlist (:29 chỉ `since_seq`,`boot_id`) |
| `git diff --stat` ≈ 21155/21146 | **21155 insertions / 21146 deletions** | Khớp, và là noise CRLF (mục 1) |
| UNTRACKED list (web_views, web_ui\*, tests) | thiếu 3 mục: `=ro`, `hooks/adapters/hermes-gate.js`, `docs/TOOLA_WEB_UI_CAMPAIGN_BASELINE.md` | Packet liệt kê không đủ tập untracked thực tế |

**Kết luận tổng:** phần lớn điều khoản hành vi của Monitor/API (:20, :22) và hai row Monitor/Matrix (:11, :12) **khớp chuẩn** với bằng chứng tự sinh; nhưng chuẩn **không phải chuẩn có trước** cho hai mệnh đề tôi được giao phán xét nhất (S1), một điều khoản :9 sai nghĩa đen (S3), một điều khoản :14 chỉ đúng một phần (S4), bảng checkpoint :26-:35 đã lệch (S5), và có một route thiếu gate thật (S2).

END_AGENT_REPORT:M-INT

---

# PHỤ LỤC — đối chiếu của bên sửa (2026-11-22). KHÔNG phải bằng chứng độc lập.

Ghi lại bởi leader đã ủy thác cuộc audit và đã áp các fix. Theo luật BLIND AUDIT, bên đó không được đưa
verdict, nên phần này tồn tại chỉ để các phép đo tái hiện được. Chỗ nào nó khớp với báo cáo ở trên, sự
khớp đó không cộng thêm gì: auditor tự đạt tới các con số của nó.

## Ba defect auditor gọi là thật, đo lại trước khi sửa code

Đo trên daemon đang chạy, code chưa sửa (PID 42032, generation 86573b5458fe):

| Auditor | Phép đo |
|---|---|
| S2 | `GET /assets/toola/theme.css` với `Host: evil.com` trả 200 / 5110 B; với `Origin: https://evil.invalid` trả 200; với `Sec-Fetch-Site: cross-site` trả 200. Cùng lúc đó `/api/monitor/health` với `Host: evil.com` trả 403 và `/monitor` trả 403. `_asset` (web_views.py:82-86) không gọi `_require_loopback` (khai tại :57). |
| S6 | `/api/monitor/health` với `Host: localhost:9999` trả 200 / 871 B. `/council` với `Host: evil.com`, không `Origin`, trả 200 / 130314 B. `_HOST` (web_views.py:35) khiến port trở thành tuỳ chọn. |
| S1 | Dry-run `dry_8b5d1d223cef4e7d9d834702e5d90514` (21:53:41, pre-image `b369d95e36526605`) đã mang `_MAX_BYTES` hai lần, `16 * 1024 * 1024`, `await self.poller`, `cancel_futures` và `cleanup_ctx.append`. Dry-run `dry_082bed7075704c86926a01155e2c0c5d` (21:59:23, pre-image `fa4b6937a997`) thay dòng 3-20 của baseline bằng văn bản không chứa "16 MiB", "in-flight" hay "waits for". |

S3, S4, S5 là defect của tài liệu chuẩn chứ không của code đang chạy; chúng được mang như caveat trong
`docs/TOOLA_WEB_UI_CAMPAIGN_BASELINE.md` thay vì bị sửa đi. S8 (`*.bak` nằm cạnh file thật,
`.orchestrator_cache/search_snapshots` lồng trong web root, file 0 byte `=ro` ở repo root) và S10
(`matrix.js` bản bundled lệch 42 hunk so với parent trong khi câu chữ gợi sự bằng nhau) không đụng tới:
cả hai cần xoá hoặc viết lại file untracked — một cổng destructive mà bên sửa không tự mở.

## Các sửa đã áp

| File | Thay đổi | Trước | Sau |
|---|---|---|---|
| `orchestrator/web_views.py` | S2: `_asset` gọi `_require_loopback`. S6: thêm `host_ok(request)` so port tường minh của Host với port của socket accepted, và `_require_loopback` dùng nó. S7: thêm `_internal()` trả 500 `MONITOR_INTERNAL_ERROR`; `_monitor_read` đưa lỗi store/serialisation về đó thay vì 502. | untracked, không còn before-state phục hồi được — dry-run 21:53:41 chỉ ghi hash pre-image `b369d95e3652`, không ghi kích thước | 71151 B, 1509 dòng, sha `39ed1ff88a46` (`.bak` giữ state ngay trước lần viết cuối: 71150 B, sha `3da42f362f94`) |
| `orchestrator_server.py` | S6: `handle_council_dashboard` (:15302-15304, 3 dòng) thay bằng 7 dòng import `host_ok` từ `orchestrator.web_views` một cách lazy và đưa nó vào phép thử `_is_local` / `_origin_ok` có sẵn, để hai gate dùng chung một helper. Không import `PORT`, và `web_views.py` không import ngược `orchestrator_server`, nên không circular import. | 1085623 B, CRLF 21167, sha `4e13608db718` (đúng bằng `.bak` trên đĩa) | 1085928 B, CRLF 21171, sha `779c2a61b0a2` |
| `tests/test_web_ui_campaign.py` | Ba regression mới (`test_assets_share_the_page_gate`, `test_host_port_must_be_the_bound_port`, `test_store_fault_is_not_reported_as_a_dead_bridge`) và một assertion cập nhật tại :275-276, chỗ đã mã hoá chẩn đoán cũ. | chỉ xác định được bằng hash `851d669080e9` (mắt đầu chuỗi `write_fix` hôm nay); kích thước không phục hồi được | 52264 B, 850 dòng, sha `e23313defb23` |
| `path_runtime.py` | Bên sửa không đụng tới; vẫn là early-return bundled +3 dòng của campaign. | 2427 B, 63 dòng, sha `18d4d2cc4487` (`.bak`, mtime 11-21 14:02:41) | 2544 B, 66 dòng, sha `0a4c39d12320` |

Line ending: `orchestrator_server.py` là 100% CRLF ở working tree (21171 CRLF, 0 bare LF) và giữ nguyên
như vậy, nên fix không tạo churn mới. Churn auditor đo là có trước và mang tính cấu trúc: HEAD lưu blob
đó là 21096 bare LF + 65 CRLF, còn `core.autocrlf=true` không kèm `.gitattributes` chuẩn hoá ngược lại
khi checkout. Vì thế `git diff --numstat HEAD` báo 21156/21146 cho file này mà `--ignore-all-space` báo
13/3 — và 13/3 chính xác là fix này (trước fix là 8/2).

## Bằng chứng hồi quy

- `tests/test_web_ui_campaign.py`: 20 passed, 44 subtests passed, 24.02 s (trước: 17 passed / 31 subtests).
- `tests/test_council_snapshot_handlers.py`: 14 passed, 3.56 s. Nhóm này gọi `handle_council_dashboard` qua `make_mocked_request`, mà host là `127.0.0.1` không kèm port, nên `host_ok` vẫn mở ở đó theo thiết kế.
- `tests/test_orchestrator_server_contract.py`: 241 passed, 27.79 s.
- Mutation proof, mỗi fix gỡ trong RAM chứ không sửa file: bỏ gate S2 → 10 failure trong `test_assets_share_the_page_gate`; thu `host_ok` về regex trần cũ → 1 failure trong `test_host_port_must_be_the_bound_port`; ánh `_internal` về `_unavailable(502)` → 1 failure trong `test_store_fault_is_not_reported_as_a_dead_bridge`. Cả ba pass khi fix còn, nên regression có răng chứ không phải xanh rỗng.
- Caveat về bằng chứng của S1: auditor ban đầu dựa vào `docs/TOOLA_WEB_UI_CAMPAIGN_BASELINE.md.bak` ở trạng thái 4132 B / hash `9cdcd1c31da8`. Ảnh đó đã mất, và chính các lần viết caveat của bên sửa đã xoá nó — `write_fix` chỉ giữ đúng một thế hệ `.bak` và đè lên mỗi lần sửa. Kết luận S1 giờ dựa vào dry-run `dry_082bed7075704c86926a01155e2c0c5d`, một nguồn mà các lần viết caveat không chạm được. Xem khối caveat trong tài liệu baseline.

## Điều vẫn chưa đúng

- Các fix CHƯA live. `:8083` vẫn chạy code chưa sửa; áp chúng cần `restart_orchestrator_via_bat`, cổng này thuộc về Anh và không được chạy khi có worker dispatch đang bay. Mọi probe ở trên đo trên code chưa sửa và được trích như bằng chứng của khuyết điểm, không phải của fix.
- Body 500 `MONITOR_INTERNAL_ERROR` an toàn cho UI vì cả hai bên tiêu thụ chỉ rẽ theo HTTP status: `monitor.html` (:1768, :5331, :5350, :5371) và `matrix.js` `liveCycle` (:2529-2533). Không chỗ nào rẽ theo `error_name`. Điều này xác minh bằng đọc, không bằng quan sát một lỗi nội bộ thật, thứ daemon chưa từng sinh.
- Assertion cập nhật tại :275-276 thay đổi một hợp đồng đã công bố: table bị drop nay báo 500 chứ không còn 502. Đó đúng là hành vi S7 mong muốn, nhưng bất kỳ tiêu thụ ngoài nào từng coi 502 là "Monitor storage hỏng" phải đọc lại status.
