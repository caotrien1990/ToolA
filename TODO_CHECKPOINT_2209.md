# TODO / CHECK POINT 22-09-2026 -- campaign "toolA on dinh + su dung toi uu"

Branch `fix/campaign-1608`. Daemon generation khi lam viec: `962c283a27d1`.
Moi write trong file nay deu di qua [A] `write_fix` / `write_new_file`.

Cau chuyen goc cua chien dich: *CA dat timeout, budget khong du timeout, he thong van
chay ngam va ket qua bi bo khong.* Tat ca cac fix duoi day deu tra loi dung cau do.

---

## A. DA FIX (written + syntax-validated qua [A])

### FIX-1 (CRITICAL) -- MCP stdio serialization
- File: `orchestrator/server/mcp_stdio.py` :: `_mcp_stdio_concurrent()` (~line 694-818),
  goi tai `~line 1846` thay cho `_mcp.stdio()`.
- Root cause: `zeromcp.McpServer.stdio()` (`venv312/Lib/site-packages/zeromcp/mcp.py:376-395`)
  la vong lap **read -> dispatch -> write tuan tu tuyet doi**: mot request phai xong han
  moi doc dong ke tiep. Moi tool call chan **toan bo** call dang xep sau no.
- Do luong 2026-09-22 (USAGE_LOG, `start = ts - ms`): 5 read-only call ban trong MOT batch
  chay lien tiep nhau: `search_code` 60276ms -> `cbm_graph_status` 1ms -> `cbm_nodes_query`
  0ms -> `ocr_map` 41608ms -> `read_file_range` 120ms. Wall clock = **SUM chu khong phai
  MAX**; call 120ms doi ~102s. Day chinh la cau chuyen goc do transport gay ra, khong phai
  do tool nao cham.
- Fix: pump cua rieng ToolA -- doc tuan tu, dispatch CONCURRENT qua `ThreadPoolExecutor`
  (`ORCHESTRATOR_MCP_STDIO_WORKERS`, default 8, clamp 1..64), write duoi `threading.Lock`.
  `initialize` + `notifications/initialized` xu ly INLINE tren reader thread de giu thu tu
  handshake.
- An toan da chung minh bang doc source truoc khi viet: `JsonRpcRegistry.dispatch` khong giu
  state per-request (`_cache` chi la memo reflection keyed by function), `_MCP_FIELDS_LOCAL`
  la thread-local doc tren handler thread TRUOC khi HTTP worker chay, `_run_mcp_coro` dung
  `asyncio.run_coroutine_threadsafe` vao mot loop nen ben. `self._protocol_version` chi duoc
  SET boi HTTP handler, khong bao gio duoi stdio.
- Bat duoc mot guess sai truoc khi viet: `stdio()` dispatch vao `self.registry`, KHONG phai
  `self.tools`. Neu guess thi moi MCP call se vo.
- KHONG patch package vendor (`zeromcp`) vi pip reinstall se xoa sach va do khong phai source
  cua ToolA.

### FIX-2 -- comment noi sai su that
- File: `orchestrator/server/mcp_stdio.py` (~line 810-818).
- Comment cu khang dinh stdio da concurrent. Sai. Nay da dung + co ngay do luong.

### FIX-3 -- ProgressWatchdog: phase moi thua ke counter cua phase cu
- File: `orchestrator/progress_watchdog.py:32-48`, hash `1462312cfe70...`.
- Root cause: phase doi nhung `completed_units` / `total_units` khong reset; mot phase chi
  goi `increment=` thi khong bao gio ghi de `total` cu.
- Do luong: snapshot published `completed_units=1021 / total_units=16` (`snapshot_gc_blobs`
  dem blob nhung `16` la cua `snapshot_gc_generations` de lai). 1021/16 **te hon la khong co
  total**: doc ra nhu mot phase da xong va vuot budget 64 lan, nen CA size client timeout
  theo no se bo mot call dang tien trien binh thuong.
- Fix: doi phase -> `completed_units = 0`, `total_units = None` (chinh la default cua
  dataclass, moi consumer da handle). `completed=`/`total=` tuong minh van thang, nen reset
  chay TRUOC.
- Da kiem truoc khi viet: 25 call site `.progress(` (`surgical_mapper.py`,
  `orchestrator/search_snapshots.py`, `orchestrator/graph/indexer.py`, contract test) khong co
  caller nao doi phase trong vong lap; `tests/test_progress_watchdog_contract.py` khong assert
  gi ve carry-over.

### FIX-4 -- `error=""` lam moi error path khong the chan doan
- File: `orchestrator_server.py` :: `_exception_text()` (helper moi) + 4 `except` arm +
  guard `EMPTY_ERROR_DETAIL` o nhanh accounting.
- Root cause: `str(exc)` la `""` voi `TimeoutError()` / `CancelledError()` tran. 4 arm day
  chuoi rong do vao `_error_msg`, `result['error']`, `result['reason']` va
  `_infer_error_name`, ma ham nay bat dau `lower = (error or "").lower()` roi fall-through
  `return "TOOL_ERROR"` -- moi nhanh substring truot **theo thiet ke** khi error rong.
- Do luong: `run_project_test_script` log `ms=231713`, `error_name=TOOL_ERROR`, `error=""`.
  231s ma nguyen nhan khong the phuc hoi tu log.
- Fix: MOT producer (`_exception_text`) chu khong phai 4 ban copy, nen arm thu 5 thua huong
  bao dam thay vi copy lai one-liner cu. Cong guard cuoi: neu van rong thi ghi
  `EMPTY_ERROR_DETAIL: ...` kem `error_name` that -- row con greppable bang 1 token.

### FIX-5 -- `timeout` cua child bi doc thanh timeout cua CALL (UX contract)
- 5 site, deu la layer **da do** dung la layer daemon serve (khong doan):
  - `code_tools.py:17822` (`ocr_review`), `:17849` (`ocr_rule`), `:17877` (`ocr_scan`),
    `:17914` (`ocr_map`) -- do bang `GET /api/tools/ocr_map/schema`, text serve trung
    `code_tools.py`.
  - `orchestrator/core/registry_data.py:2622` (`run_project_test_script`) -- do bang
    `GET /api/tools/run_project_test_script/schema`, text serve trung `registry_data.py`.
  - Da tra loi canh bao A36 ("mot description trong registry_data CO THE unreachable vi 2
    layer sau override"): voi 2 tool nay, layer duoc serve la 2 layer khac nhau, va ca 2 deu
    da sua.
- Root cause: `"Subprocess timeout in seconds"` / `"Seconds for the child"` khong he noi wall
  clock cua call co the vuot con so do (lane admission, git diff feed OCR, script staging,
  result assembly deu nam NGOAI). Do luong: `timeout=90` -> call 231.7s.
- Fix theo ROOT-CAUSE RULE: **siet contract da publish**, KHONG them response field. Contract
  moi noi ro `timeout` chi bound child/subprocess, khong bound call, va yeu cau size client
  timeout theo wall clock do duoc.
- Da grep: khong co test nao pin nhung chuoi description nay.

### FIX-6 (phat sinh trong luc lam) -- E302 do chinh toi tao ra
- `orchestrator_server.py`: `_exception_text` chi cach `def _infer_error_name` MOT dong trong.
  Da chen thanh 2 (`new_lines_count: 2`). Latent lint, khong phai runtime bug, nhung la do toi.

---

## B. KHONG PHAI BUG -- da document tai producer (dong chu khong mo lai)

### `write_status.duration_ms` 243503 vs `USAGE_LOG.ms` 297337 cho CUNG mot write
- Ket luan: **hai span khac nhau, ca hai deu trung thuc.**
  - `duration_ms` = `_write_ledger_begin_async` -> `_write_ledger_finish_async`
    (`orchestrator_server.py:15507`).
  - `USAGE_LOG.ms` = `_t0` sau semaphore (`:15265`) -> het dispatch (`:15884`).
- 53.8s chenh lech = **post-commit tail** (response assembly, auto-chain, validation summary,
  cache sync) -- phan viec khong duoc tinh vao "write mat bao lau".
- Da ghi ly do NGAY TAI producer (`orchestrator_server.py`, cho tinh `rec["duration_ms"]`) de
  auditor sau khong mo lai nhu mot measurement bug.
- **Con mo (hieu nang, khong phai dung/sai):** 53.8s post-commit cho 1 write 10MB la `t` thuan.
  Chua truy nguyen theo tung buoc. Do la 1 lan, chua repeat.

---

## C. CAN LAM DE CHOT (thu tu bat buoc)

1. **Restart [A]**: `POST /api/call/restart_orchestrator_via_bat` voi CHI `delay_seconds=2`.
   Proof phai la `GET /health` co **pid MOI VA generation DOI** (`ok:true` cua tool khong
   phai proof). FIX-3 / FIX-4 / FIX-5 / muc B len live o buoc nay.
2. **Anh reconnect MCP client** (`/mcp`). BUOC NAY KHONG THE BO: FIX-1 / FIX-2 sua chinh code
   cua bridge (`orchestrator/server/mcp_stdio.py`), nen daemon restart mot minh KHONG nap pump
   moi. Truoc khi reconnect, moi MCP call van serial nhu cu.
3. **Real test song song (warm)**: ban lai dung 5 read-only call trong MOT batch. Ky vong wall
   clock ~= **MAX** chu khong con la SUM. Chay kem mot baseline serial warm de tach cold-cache
   khoi contention -- post-restart do duoc `ocr_map` 41608ms cold vs 4568ms warm, `search_code`
   60276ms cold vs 18065ms warm; **D: la HDD**, tuyet doi khong ket luan contention tu so cold.
4. **Suite**: runner detached `_a42_full_suite_runner.py` -> `_a42_full_suite.status` /
   `_a42_full_suite.log` (suite ~12 phut, MCP proxy chet o ~180-195s nen khong the goi truc
   tiep). Fail nao cung phai baseline lai bang `git HEAD` truoc khi goi la pre-existing.
5. **Commit** (trailer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`; `git_commit`
   can `expected_staged_hash` tu `git_status`).

---

## D. CON MO (chua fix, co evidence, xep theo gia tri)

1. **53.8s post-commit tail** -- muc B. Can do lai nhieu lan roi truy tung buoc.
2. **ToolQ `.orchestrator_ignore` residual**: `search_code` tren ToolQ con tra hit rac tu
   `_backup_stabilize_*` / `_iso_campaign`. Root fix **thuoc scope ToolA**: default ignore
   patterns cua [A], khong phai va tay trong repo ToolQ.
3. **`search_code(file_pattern='tests/*.py')` -> `count:0` tren ToolQ** du file ton tai. Bay
   do luong da biet (index chi co CODE, `*.txt`/`.md` luon 0) nhung `tests/*.py` thi KHONG
   nam trong cai bay do -- can positive-control lai va truy `file_pattern` glob semantics.
4. **`durable_audit.db` (38.4 MB) chua doc.** Chua biet co gi trong do.
5. **Lint pre-existing, co y de nguyen** (khong dong nao do dot nay sua):
   - `orchestrator/progress_watchdog.py:2:1 I001` (import block chua sort: `threading, time, sys`).
   - `orchestrator_server.py`: `F401` x2 (line 14, 63), `E402` x7 (219, 365, 11132, 11385,
     11466, 11489, 11490, 11526, 11775), `F541` (7701:13), `F841` (14178:54).
   - Cac so dong nay do TRUOC khi chen comment ~8 dong tai `rec["duration_ms"]`; moi dong
     `> 2611` trong `orchestrator_server.py` da dich +8 sau do.

---

## E. BAY DO LUONG DA GAP TRONG DOT NAY (de lan sau khong mat vong)

- `call_tool` payload qua lon toi bridge duoi dang **string** -> `Invalid params: payload
  expected dict, got str`. Nguong chinh xac: **unknown / not verified** (payload 125 dong DA
  thanh cong, mot docstring ~35 dong thi khong).
- `write_fix` voi `new_content="\n"` de "chen dong trong" la **no-op** -- bang chung la
  `new_hash` khong doi. Muon 2 dong trong thi `"\n\n"`.
- `fields=` xin key khong ton tai -> `fields_unknown` + `fields_available` (khong mat lan call
  thu hai). `fields=` xin `result` tren `read_file_range` co the bi truncate ->
  `response_incomplete`.
- Viet mot file `.py` GIUA luc suite dang chay co the lam fail assert dua tren
  `inspect.getsource` (doc DISK). Lan chay dau tien cua `_a42` da bi toi ghi de len
  `orchestrator_server.py` giua dot -> neu co fail lien quan AST/lineno thi phai chay lai
   rieng, dung ket luan ngay.

---

## F. HAI DIEM ANH BO SUNG GIUA DOT (chua chot, can do moi ket luan)

### F1. Codo goi [A] bang HTTP, KHONG qua MCP -- DA GHI NHAN

Anh: "codo goi bang api khong goi mcp, vi co ve mcp toolA van chua dong bo duoc cho codex."

He qua truc tiep: FIX-1 (pump stdio concurrent trong `orchestrator/server/mcp_stdio.py`) **khong
anh huong gi den Codo**. Codo di duong `POST http://localhost:8083/api/call/<tool>`, va duong do
DA concurrent san tu truoc (daemon co `_tool_semaphore` + lane `read_io` 12/48). Nghia la:

- Sau khi restart [A], Codo dung duoc NGAY, khong can reconnect gi ca.
- Chi CA nao dung MCP (Clau/ClauC) moi phai `/mcp` reconnect de nap pump moi.
- Neu MCP toolA that su chua dong bo cho codex thi do la van de RIENG cua client codex, khong
  phai cua bridge nay -- **chua verify**, chua do.

### F2. Cau hoi: buoc CA chi dung tool TUAN TU (ca MCP + API)?

Anh: "toi nghi nen buoc ca mcp va api chi duoc dung tool tuan tu. bang chung la session
claudeltnai nay ban dung rat on dinh."

**Chua chot. Toi khong dong y ngay, va ly do la do luong, khong phai y kien:**

1. Transport tuan tu TU SINH RA cai C-loss ma chinh chien dich nay dang di sua. Do that:
   mot `read_file_range` 120ms phai doi ~102s dang sau `search_code` 60276ms +
   `ocr_map` 41608ms. Call re bi blow client timeout ma khong he co loi gi cua no.
2. "Session claudeltnai on dinh" chi chung minh tuan tu la AN TOAN, khong chung minh no TOT HON.
   Day la bang chung mot chieu: chua bao gio do song song de so sanh.
3. Daemon DA chan concurrency san: `_tool_semaphore`, lane `read_io` (12, 48), va **write
   serialize theo tung project**. Khoa API lai chi lam cham Codo/Terra -- ma log 09:38-09:44
   cho thay call cua ho DA overlap voi nhau, zero error.
4. Rui ro contention that nam o T4 / port 9223, khong nam o read/write. Xung dot `read_token`
   fail loud bang `FILE_CHANGED_OUTSIDE_ORCHESTRATOR`, khong bao gio corrupt am tham.
5. Cau hoi that su con mo la **SO WORKER** tren o D: (o nay la HDD, 1190ms sec/read), khong phai
   "co nen song song hay khong".

**Van dong huong reversible da co san:** `ORCHESTRATOR_MCP_STDIO_WORKERS=1` tra ve dung hanh vi
tuan tu cu, khong can sua mot dong code nao. Neu do ra tuan tu tot hon thi set bang 1 la xong.

**Viec phai lam de chot F2:** do wall clock cung 1 batch 5 call read-only o 3 muc worker
**8 vs 3 vs 1**, warm cache, cong them mot baseline tuan tu. Ky vong neu song song dung:
wall clock ~= MAX chu khong phai SUM. Chua do xong thi cau "song song tot hon" van la
**chua verify**.

---

## F2 DA DO XONG -- 2026-09-22 (ket qua do duoc, khong con la du doan)

Do bang 5 payload KHAC NHAU. Bat buoc phai khac nhau: A41 singleflight
(`call_cache._INFLIGHT`) join moi payload trung nhau O TRUOC admission, nen mot batch
payload giong nhau se bao cao 1 lan execute va moi muc worker deu nhanh y het -- dung cai
bay da lam `test_search_server_admission` trong nhu mot bug. Warm truoc moi lan do: D: la
HDD (1190ms sec/read), cold-vs-warm con lon hon contention.

### Tang HTTP / API (`/api/call/<tool>`)

| do | wall clock | ghi chu |
|---|---|---|
| SERIAL warm | 3.900s | sum of durations 3.9s |
| PARALLEL warm (5 thread) | **1.662s** | call cham nhat 1.659s |

- `PARALLEL_WALL_MINUS_SLOWEST = 0.003s` => wall clock ~= **MAX**, khong phai SUM.
- `ALL_200_SERIAL = True`, `ALL_200_PARALLEL = True`. 0 loi.
- **Ket luan: HTTP DA song song that su tu truoc den gio.** Khoa tuan tu la them mot
  gioi han moi, khong phai sua mot loi.

### Tang MCP stdio (`ORCHESTRATOR_MCP_STDIO_WORKERS`)

| workers | serial wall | batch wall (5 call) | speedup | answered | errors |
|---|---|---|---|---|---|
| 8 (default) | 2.016s | **1.035s** | 1.95x | 5/5 | 0 |
| 3 | 2.308s | **1.108s** | 2.08x | 5/5 | 0 |
| 1 | 2.729s | **2.400s** | 1.14x | 5/5 | 0 |

- Workers=1 arrivals la mot cau thang dung nghia: 0.563 / 1.616 / 1.863 / 2.084 / 2.396
  -- day la pattern SUM, dung nhu du doan cua chinh sach tuan tu.
- **0 error o ca 3 muc.** Song song khong doi correctness bang bat cu gia nao do duoc.

### Chot F2 (de nghi)

1. **KHONG khoa tuan tu.** Gia phai tra la ~2.3x wall clock tren batch 5 call, doi lai
   **zero** loi ich correctness do duoc.
2. `ORCHESTRATOR_MCP_STDIO_WORKERS=3` la mot van gan nhu mien phi (1.035 vs 1.108 = trong
   noise) neu Anh muon giam ap luc HDD. Con `=1` thi KHONG mien phi.
3. Ly do session claudeltnai on dinh KHONG phai vi transport bi khoa: on dinh vi ClauLTN
   **quyet dinh tuan tu** -- doi ket qua call nay moi chon call sau. Khoa transport se cat
   mat lua chon do ma khong doi duoc mot dong cach ClauLTN lam viec. Cai lam Codo/Terra
   bat on la pump stdio SERIAL cu, va FIX-1 da thay no.

---

## 13 FAIL CUA SUITE -- PHAN LOAI (4439 passed, 1 skipped, 4091.86s)

| loai | so | y nghia |
|---|---|---|
| that (A41 singleflight) | 1 | `test_search_server_admission` -- da root-fix |
| tu gay ra: source bi ghi GIUA run | 7 | `inspect.getsource` doc DISK, khong doc module da import |
| flake do tai (load) | 5 | pass lai khi chay rieng |

7 fail nhom giua la hau qua truc tiep cua viec ToolA duoc sua trong khi suite dang chay
(68 phut). Trong report chung khong the phan biet voi regression that.

## FIX GOC DA GHI TRONG ROUND NAY

1. **`conftest.py`** -- snapshot mtime cua `*.py` + `orchestrator/**/*.py` o
   `pytest_sessionstart`, in block `SOURCE CHANGED WHILE THIS RUN WAS IN FLIGHT` o
   `pytest_terminal_summary`. Chi observational: khong fail, khong skip, khong reorder --
   mot guard co quyen fail se cho mot edit khong may pha huy ket qua 68 phut. Dung mtime
   chu khong hash: hash ca cay source la mot lan doc full tree thu hai tren HDD.
   Positive control DA in dung block + `modified: orchestrator/progress_watchdog.py`;
   baseline im lang; `ruff check conftest.py` exit 0.
2. **`_a42_full_suite_runner.py`** -- bo `capture_output=True`, chuyen sang stream:
   `-u` (bat buoc, khong phai my pham: ghi ra file thay vi tty lam CPython block-buffer
   stdout, log se nhay tung 8KB), log line-buffered, STATUS refresh moi 30s mang
   `elapsed` + `log_bytes`. Docstring sua tu "~12 phut" thanh so do that 4091.86s.
3. **`examples/SLIM_RULE.txt:42` + `examples/CORE_RULE.txt:17`** -- cau "stdio host DOES
   register named wrappers" la SAI. `mcp_stdio.py:1930` doc
   `ORCHESTRATOR_MCP_FULL_SURFACE` default `"0"`, dang ky wrapper roi PRUNE lai con
   core-9. Do that tren `orchestrator_server.py --mcp`: `tools/list` = 9 ten,
   `search_code` / `read_file_range` / `git_status` / `cbm_graph_status` KHONG co, chi con
   `call_tool`. Mot CA doc rule cu se goi ten wrapper va khong nhan duoc gi. Sua o
   PRODUCER; `sync_rule target=all` da render vao 8 file rule (OLD=0 NEW=1 tren ca 8).

**Bai hoc do luong:** grep `DOES register named wrappers` bao 0 hit tren CORE_RULE.txt
nhung defect VAN o do -- ban goc viet `DOES register named per-tool wrappers`. Positive
control moi pattern sweep; mot chuoi 0-hit khong bao gio la bang chung vang mat.
