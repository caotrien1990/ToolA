# DISPATCH ClauC R2 — TOOLA-ARGVFALSE-R1

TASK_ID: TOOLA-ARGVFALSE-R1
OBJECTIVE_CLASS: bug-fix mot cho + test (regression + real test)
RISK_LEVEL: mid
DEV_LEVEL: mid
LOAD_BUDGET_SOURCE: conservative-heuristic
INDEPENDENT_QUESTIONS: 2
FILES_SUBSYSTEMS: 1
EXPECTED_TOOL_CALLS: unknown
EXPECTED_WRITES: 2
DEPENDENCY_DEPTH: 1
EXPECTED_END_MARKER: END_CLAUC_R2_ARGVFALSE

## Cwd va quyen
Cwd = `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`.
Day la cay [A] cua chinh no ⇒ native edit + `log_native_event` la duong AUTHORIZED
(`task_class=upgrade_orchestrator`). Truyen MOT duong dan tuyet doi moi lan.
Interpreter BAT BUOC cho test: `./venv312/Scripts/python.exe`. `python` tran = pythoncore-3.14,
thieu `psutil`, collection error — do la loi moi truong, KHONG phai test fail.
KHONG restart [A]. KHONG commit (leader se commit).

## Bug
`council_message` toi `clau` tra `error: COUNCIL_HARNESS_ARGV_TOO_LONG` trong khi harness
**da chay thanh cong**. Anh mat kha nang theo doi tien trinh vi dispatch bi gan nhan fail.

### Bang chung da verify (leader, 27/08)
Su kien that trong `.council/council_events.jsonl`:
- `ts=2026-08-27T13:24:34`, `request_id=mcp_a5f2657e1404`, `to=clau`, `mode=broker`, `model=claude-opus-5`
- **`returncode = 0`**
- `raw` bat dau bang mot dong stream-json hop le: `{"type":"system","subtype":"init","cwd":"...project-orchestrator","session_id":"4d445be3-0f41-4ba1-8563-9f6cd51e25b2","tools":[...],"mcp_servers":[{"name":"project-orchestrator","status":"connected"}],"model":"claude-opus-5",...}`
  ⇒ `claude.exe` **da spawn duoc**, MCP connected, model dung. Argv KHONG the qua dai.
- `model_resolution.warnings = [{"warning_name":"HARNESS_ARGV_TOO_LONG"}]`, `resolution_status=unavailable`
- `text = "[no clean response]"`
- `claude.exe` co that: `C:\Users\ADMIN\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe` (253,204,128 bytes, mtime 2026-08-27 07:31) ⇒ `_council_claudeltnai_target()` di duong direct-exe, gioi han la 32767 chu khong phai 8191 cua cmd.exe.

### Nguyen nhan (gia thuyet cua leader — anh phai xac minh, dung tin ngay)
`orchestrator_server.py:4537` trong `_council_finalize_model_resolution`:
```python
if raw and "command line is too long" in raw.lower():
    return (replace(resolution, resolution_status="unavailable",
                    warnings=resolution.warnings + ({"warning_name": "HARNESS_ARGV_TOO_LONG"},)),
            "COUNCIL_HARNESS_ARGV_TOO_LONG")
```
`raw` tai call site `orchestrator_server.py:7955` la **toan bo stdout+stderr cua ca lan chay worker**,
tuc la ca stream-json chua moi `tool_result` ma worker doc duoc va moi dong stderr cua tien trinh con
worker tu spawn. Substring match tren khoi do gan sai loi launcher cho bat ky lan xuat hien nao cua cau do.
Va `result_ok = returncode == 0 and not model_error` (`:7957`) ⇒ mot lan chay THANH CONG bi bien thanh fail.

Hai nguon co the sinh ra cau do trong mot lan chay returncode=0:
1. **Self-trigger:** `orchestrator_server.py:7287` chua nguyen van chuoi ``The command line is too long.``
   trong docstring cua `_council_claudeltnai_target`. Bat ky dispatch ClauC nao DOC file nay se day cau do
   vao stream ⇒ tu kich hoat. Lan 13:24 ClauC dang audit dung file nay.
2. **Tien trinh con cua worker:** ClauC chay mot lenh cmd/PowerShell voi argv dai, cmd.exe in
   `The command line is too long.` ra stderr cua chinh no.

Anh phai xac dinh nguon THAT SU cua lan 13:24 bang bang chung (khong doan). `raw` luu trong event bi cat
o 2817 ky tu (het giua JSON, sau `"memory_paths":{"auto":"C:\Users\ADMIN\.claude-ltnai\projects\D--0--TRIEN-1--DATA-DATA-5--VIBE-APP-1`)
nen ban luu KHONG con chua cau do — dung ket luan "khong co" tu ban bi cat. Tim ban day hon trong
`.council/broker_filtered.jsonl`, `.council/usage_log.jsonl`, log harness, hoac tai hien.

## Viec phai lam
1. **Sua detector cho dung pham vi.** Chi duoc ket luan HARNESS_ARGV_TOO_LONG khi cau do that su den tu
   **lan spawn harness**, khong phai tu noi dung worker sinh ra hay doc duoc. Rang buoc bat buoc:
   - `returncode == 0` + co mot envelope `{"type":"system","subtype":"init"}` hop le ⇒ **KHONG BAO GIO**
     duoc gan HARNESS_ARGV_TOO_LONG. Harness da chay.
   - Chon cach sua theo bang chung anh tim duoc (vi du: chi soi stderr truoc dong stream-json dau tien;
     hoac chi soi khi khong he co envelope init; hoac tach stderr launcher ra bien rieng). Ghi ro ly do chon.
   - Giu duoc kha nang phat hien argv-too-long THAT (khi harness that su khong start) — dung go guard.
2. **Lam ro tai sao `text = "[no clean response]"`** trong khi returncode=0. Neu day la mot bug thu hai
   (reply bi mat) thi BAO CAO chu dung tu y sua trong cung lan nay — leader se quyet dinh.
3. **Test.** Toi thieu:
   - regression test moi (them file `tests/test_council_argv_false_positive_r1.py`) chung minh: raw co chua
     cau `command line is too long` NHUNG co envelope init hop le + returncode 0 ⇒ khong sinh error_name;
     va raw khong co envelope init + launcher stderr co cau do ⇒ VAN sinh COUNCIL_HARNESS_ARGV_TOO_LONG.
   - chay lai toan bo test lien quan council/model-resolution bang `./venv312/Scripts/python.exe -m pytest`
     va bao cao con so THAT (passed/failed/skipped/xfailed) + baseline HEAD.
   - **REAL TEST:** goi that mot dispatch ngan qua HTTP `POST http://localhost:8083/api/call/council_message`
     toi mot worker, trong do prompt YEU CAU worker doc `orchestrator_server.py` quanh dong 7287 (de cau do
     chac chan vao stream), roi chung minh ket qua tra `ok:true` khong con `COUNCIL_HARNESS_ARGV_TOO_LONG`.
     Neu code sua chua duoc nap (server chua restart) thi noi ro dieu do va do bang unit test thay the —
     KHONG duoc bao "da real test" khi thuc te chua.

## Bao cao
Ghi vao `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\41. CLAUDELTNAI\FIX_TOOLQ_2508\TOOLA_FIX_ARGVFALSE_2708.md`.
Neu phai ghi ngoai cwd: bootstrap [A] va dung [A] write tools.
Cau truc: (1) nguon that su cua cau do + bang chung, (2) diff da ap dung (file:line), (3) ket qua test that
voi so lieu, (4) real test hoac ly do khong chay duoc, (5) muc "CHUA VERIFY / UNKNOWN" trung thuc.
Ket thuc bang dung mot dong: `END_CLAUC_R2_ARGVFALSE`
