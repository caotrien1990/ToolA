# NHIEM VU CLAUC R1 — FIX [A] AUTO-HEAL KILL MID-WRITE

Ban la ClauC, worker cua hoi dong. Leader = Clau (desktop CA). Anh da cho phep EXECUTE ("tien hanh")
cho pham vi nay: fix + test + real test tren chinh [A] (ToolA). Day la duong upgrade_orchestrator
DUOC PHEP: native edit trong cay [A] + `log_native_event` (mot absolute path moi lan).

## ROUTE MEASUREMENT GATE
TASK_ID: TOOLA-AHEALTHHUNG-R1
OBJECTIVE_CLASS: scoped-bugfix-on-existing-code (orchestrator lifecycle / event-loop blocking)
RISK_LEVEL: high
DEV_LEVEL: high
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 3
FILES_SUBSYSTEMS: 2
EXPECTED_TOOL_CALLS: unknown
EXPECTED_WRITES: 3
DEPENDENCY_DEPTH: 2
EXPECTED_END_MARKER: END_CLAUC_R1_AHEALTHHUNG

## TRIEU CHUNG THUC TE (do leader do duoc hom nay 2026-08-27)

1. `write_new_file` mot file 5 KB (`ROUTE_RULE_2708.md`) vao mot project root CHUA CO INDEX
   chay > 120 s -> client timeout -> MCP tra `MCP_PROXY_FAILED` /
   `ConnectionResetError [WinError 10054]`, `commit_state: unknown`.
2. File that ra DA COMMIT (5022 bytes, sha256 dung, khong mojibake) nhung KHONG co dong nao trong
   `.orchestrator_cache/USAGE_LOG.jsonl` -> process bi kill TRUOC khi ghi duoc log.
3. `.orchestrator_cache/restart_control_audit.jsonl` cho thay dung luc do:
   `{"event":"auto_heal_triggered","hung_streak":5,"old_generation":"8cdcd3308a49","old_pid":23540,
     "reason":"livez_ok_health_hung"}` roi `auto_heal_result ok:true new_generation:"64b8536e7657"`.
4. Instance moi (`64b8536e7657` / pid 16700) len KHONG con INDEX cua project do ->
   `write_fix` tiep theo fail: `"INDEX not found. Call generate_project_map before writing."`
5. KHONG phai one-off: cung `reason=livez_ok_health_hung` truoc do da kill generation
   `2006f66f0acd` / pid 21544 (co trong cung file audit).
6. Do dai write khong phai ngoai le: `USAGE_LOG.jsonl` co `write_fix ok:true ms:51139` va `ms:9641`
   tren project `8. ToolA`. Write nhieu chuc giay la binh thuong o day.
7. DA LOAI TRU: kich thuoc backup (zip backup luc 12:06 chi 2707 bytes); duong doc cua [A]
   (`read_file_range` 67.9 ms); schema/transport (mot loi WRONG_ARGUMENTS truoc do la JSON sai cua
   leader, khong phai bug [A]).

## CODE DA DINH VI (leader da doc, khong can scan lai rong)

File: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\orchestrator\lifecycle\restart_control_service.py`
- `_health_is_responsive` (dong ~721): tra True voi BAT KY HTTP response nao, ke ca 503/slow;
  chi tra False khi that su khong tra loi duoc HTTP (timeout / refused / reset).
- `_auto_heal_monitor` (dong ~737): `poll_interval=30.0`, `hung_threshold=5`, `health_timeout=10.0`,
  `warmup_grace_s=60.0`, `cooldown_s=300`. Dong ~802: `hung_streak += 1 if (livez_ok and not health_alive)`.
  Dong ~812: append audit `auto_heal_triggered / livez_ok_health_hung`, roi `scheduler._call("/end")`
  + `/run` (high_il force-kill + relaunch).
- Docstring cua `_auto_heal_monitor` tu khang dinh: "Root cause of the recurring hang (on-loop
  index_delta/session-write) is now fixed, so re-hangs should be rare". Bang chung o tren cho thay
  KHANG DINH DO KHONG CON DUNG.

File: `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\orchestrator_server.py`
- dong 11472 va 11515: `hung_threshold_sec` = `_RESTART_HUNG_SEMAPHORE_SEC` (mac dinh 15.0). LUU Y:
  day la nguong SEMAPHORE-STALENESS cua busy-snapshot, KHONG phai nguong cua auto-heal. Dung nham
  hai thu nay. Nguong auto-heal thuc te la ~30 s x 5 = ~150 s lien tuc + health_timeout 10 s.

## SUY LUAN CUA LEADER (ban PHAI phan bien, dung nhan mac dinh la dung)

Vi `_health_is_responsive` da co chu y tha cho "busy nhung con tra loi", viec bi kill nghia la
`/health` KHONG tra loi duoc HTTP chut nao trong >= 5 lan poll lien tiep (~150 s) trong khi write
dang chay. Tuc la write dang CHAN CUNG event loop, khong chi lam cham. Vay defect chinh la
"on-loop blocking write", con classifier co the la dung. Ban phai xac nhan hoac bac bo bang bang chung
that (khong doan).

## 3 CAU HOI DOC LAP CAN TRA LOI + FIX

Q1 (goc): duong `write_new_file` / `write_fix` chan event loop o dau khi project CHUA CO INDEX?
  (nghi ngo: build index / project map dong bo chay tren loop). Chung minh bang code + do thoi gian
  that. Fix: dua phan chan nay ra khoi event loop (executor/thread) hoac cho no yield, sao cho
  `/health` van tra loi duoc trong suot mot write dai.
Q2 (an toan): `_auto_heal_monitor` hien khong he doc trang thai busy/executor truoc khi kill.
  Them lop phong ve: KHONG kill khi con operation dang in-flight (tham khao
  `.orchestrator_cache/restart_busy_snapshot.json`: `busy`, `executor_in_flight`, `requests_in_flight`,
  `accounting_pending`, va `restart_busy_snapshot_alive` tren `/livez`) — kill giua mot write la rui ro
  mat du lieu, khong chi mat log. Neu ban thay lop phong ve nay tu mo lai lo hong "livez gia" thi
  noi ro va de xuat cach khac.
Q3 (hau qua): sau khi mot generation moi len, INDEX cua project bi mat -> `write_fix` fail
  `"INDEX not found"`. Lam cho INDEX ben qua restart (persist/tu rebuild lazy), hoac it nhat cho
  `write_fix` tu goi/goi y `generate_project_map` thay vi fail cung.

## RANG BUOC

- KHONG restart [A] (leader dang dispatch; restart giua dispatch la vi pham). Neu fix can restart de
  verify, VIET RO trong bao cao va de leader lam.
- Moi write vao cay [A] = native edit + `log_native_event` voi MOT absolute path.
- Bat buoc co CA HAI: unit test (pytest, dat canh `tests/test_restart_autoheal_r45.py`) VA real test
  (do that: mot write dai tren project chua co index, chung minh `/health` van tra loi trong suot
  thoi gian do, va `hung_streak` khong tang). Neu real test khong the chay ma khong restart, noi ro.
- Test hien co `tests/test_restart_autoheal_r45.py` PHAI con pass. Bao cao baseline git HEAD truoc khi
  goi bat ky test nao la "pre-existing failure".
- KHONG xoa file nao. Khong mass-overwrite. Khong `-Recurse`, khong wildcard delete.
- Cloud LTNAI chi dung cho ask/pre_search/query; moi thu khac phai local.
- Khong bao "xong" khi chua chay test that. Thieu bang chung = ghi "unknown / not verified".

## BANG CHUNG DA STAGE SAN (doc truc tiep, khong can scan rong)

`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\.council\tmp_ahealthhung\DATA\`
- `restart_control_audit.jsonl`
- `restart_busy_snapshot.json`
- `server_generation.json`
- `watchdog_state.json`
- `usage_log_writes_tail.jsonl`

## ARTIFACT PHAI GHI (exclusive owner cua ban)

`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\TOOLA_FIX_AUTOHEAL_2708.md`

Neu task nay phai ghi ra ngoai cwd, bootstrap [A] va dung [A] write tools.

Cau truc bao cao:
1. ROOT CAUSE (khang dinh/bac bo suy luan cua leader, kem file:line + so do duoc)
2. FIX APPLIED (tung file:line, tung `log_native_event`)
3. UNIT TEST (path + lenh + ket qua that, dan output)
4. REAL TEST (kich ban + so do that truoc/sau)
5. REGRESSION (test_restart_autoheal_r45 + baseline HEAD)
6. RESIDUAL RISK / VIEC CON LAI CHO LEADER (co can restart de verify khong)
7. UNKNOWN / NOT VERIFIED

Ket thuc reply bang dung dong: END_CLAUC_R1_AHEALTHHUNG

## RETRY NOTE (leader them 2026-08-27, lan dispatch thu 2)

Lan dispatch dau that bai vi loi HARNESS cua council (Anh da fix, da real test toan bo), KHONG phai
vi noi dung task sai. Task va pham vi giu NGUYEN. Chi cap nhat 2 dieu kien moi:

- `[A]` hien la generation `2c7b980bc590` (khac generation `64b8536e7657` neu trong bang chung o tren).
  Cac generation `8cdcd3308a49` / `64b8536e7657` / `2006f66f0acd` trong audit van la bang chung that.
- Da co them mot lan restart do Anh thuc hien khi fix council. Neu ban thay them dong auto_heal moi
  trong `restart_control_audit.jsonl`, phan biet ro: restart do Anh vs auto_heal cua monitor.

Nhac lai gioi han cung: KHONG restart [A], KHONG xoa file, bat buoc unit test + real test that.
Ket thuc reply bang dung dong: END_CLAUC_R1_AHEALTHHUNG
