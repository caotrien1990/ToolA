# TOOLA-ARGVFALSE-R3 — ClauC report

TASK_ID: TOOLA-ARGVFALSE-R3
Agent: ClauC (claude-opus-5)
Repo: D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator
Trạng thái: fix áp dụng xong, test xanh, worktree để DIRTY, KHÔNG commit, KHÔNG restart daemon.

## 1. Root cause (xác nhận lại)

R2 dùng 2 scope AND: `not _council_harness_started(raw)` + phrase nằm trên dòng non-JSON.
Harness của GroC (`ltnai/grok-4.6-build`) xuất **text thuần tiếng Việt, không có bất kỳ dòng
JSON nào**, nên `_council_harness_started()` vĩnh viễn trả `False`; câu trả lời lại trích đúng
dòng `The command line is too long.` trên một dòng non-JSON → **cả 2 scope R2 thỏa mãn cùng lúc
trên một run THÀNH CÔNG (returncode=0)**. Bất kỳ harness non-JSON nào chỉ cần *nhắc* cụm từ đó
đều bị gắn nhãn `COUNCIL_HARNESS_ARGV_TOO_LONG`.

Scope còn thiếu là `returncode`: một shell refusal thật nghĩa là cmd.exe/CreateProcess chưa hề
spawn được child → child không thể exit 0. `returncode == 0` là bằng chứng dương,
**harness-agnostic**, khác với init-envelope vốn phụ thuộc harness có xuất stream-json hay không.

## 2. Diff regions (file:line — sau khi patch)

Source: `orchestrator_server.py`. Test: `tests/test_council_argv_false_positive_r1.py`.
`git diff --stat orchestrator_server.py` = **1 file changed, 33 insertions(+), 8 deletions(-)**.

### (1) Hằng số mới — `orchestrator_server.py:4557`
```python
_COUNCIL_ARGV_REFUSAL_MAX_LINES = 3
```

### (2) Đổi signature detector — `orchestrator_server.py:4560`
```python
def _council_launcher_argv_too_long(raw: str, returncode: int | None = None) -> bool:
```
(trước: `def _council_launcher_argv_too_long(raw: str) -> bool:`)

### (3) Docstring — `orchestrator_server.py:4561..4587`
Viết lại thành "THREE independent scopes, ALL required", nêu rõ:
- vì sao `returncode == 0` là bằng chứng harness-agnostic, và vì sao nó được kiểm tra ĐẦU TIÊN;
- vì sao `returncode is None` (killed/timeout) **không** có bằng chứng đó nên rơi xuống các scope
  cũ và giữ nguyên hành vi hôm nay;
- **giải thích hằng số 3**: một shell refusal thật được phát ra ĐƠN ĐỘC; chọn 3 (không phải 1) để
  còn dư chỗ cho banner của launcher / dòng wrapper quanh đúng 1 dòng refusal, nhưng vẫn thấp xa
  so với bất kỳ câu trả lời thật nào;
- ghi lại chứng cứ hiện trường mới: `run-979141e5`, agent `gro`, `grok-4.6-build`, 2026-08-27 14:48.

### (4) Scope 1 — guard `returncode` ĐẦU TIÊN — `orchestrator_server.py:4588..4589`
```python
    if returncode == 0:
        return False
    if not raw:
        return False
    if _council_harness_started(raw):
        return False
```
Hai scope R2 (`not raw`, `_council_harness_started`) giữ nguyên, đặt SAU guard mới.

### (5) Scope 3 — chặn blob có prose thật — `orchestrator_server.py:4597..4598`
```python
    if len([ln for ln in str(raw).splitlines() if ln.strip()]) > _COUNCIL_ARGV_REFUSAL_MAX_LINES:
        return False
```
Đặt ngay trước vòng `for line in str(raw).splitlines():` (scope non-JSON cũ, giữ nguyên nguyên văn).

### (6) `_council_finalize_model_resolution` — param mới — `orchestrator_server.py:4612..4614`
```python
def _council_finalize_model_resolution(resolution: CouncilModelResolution, raw: str,
                                       risk_level: str = "unknown", dev_level: str = "unknown",
                                       request_id: str = "", session_id: str = "",
                                       returncode: int | None = None) -> tuple[CouncilModelResolution, str]:
```
Param cuối, có default → mọi call site cũ (kể cả trong test hiện hữu) vẫn hợp lệ.

### (7) Forward vào detector — `orchestrator_server.py:4618`
```python
    if _council_launcher_argv_too_long(raw, returncode):
```

### (8) Caller timeout #1 — `orchestrator_server.py:7968..7969`
```python
            final_resolution, _ = _council_finalize_model_resolution(
                resolution, raw, risk_level, dev_level, request_id=request_id,
                session_id=resume_id or new_session_id, returncode=None)
```

### (9) Caller chính (đường thành công) — `orchestrator_server.py:8038..8039`
```python
        final_resolution, model_error = _council_finalize_model_resolution(
            resolution, raw, risk_level, dev_level, request_id=request_id,
            session_id=captured_session_id, returncode=returncode)
```
`returncode` đã có sẵn ở dòng ngay trên (`returncode = proc.returncode`) và dòng ngay dưới là
`result_ok = returncode == 0 and not model_error` — đúng như brief mô tả.

### (10) Caller `subprocess.TimeoutExpired` — `orchestrator_server.py:8068`
```python
            session_id=locals().get("captured_session_id", ""), returncode=None)
```

`./venv312/Scripts/python.exe -m py_compile orchestrator_server.py` → **COMPILE OK**.

## 3. Tests — `tests/test_council_argv_false_positive_r1.py` (EXTEND, không tạo file mới)

Giữ nguyên trick PHRASE lắp lúc runtime (`PHRASE = "command line " + "is too " + "long"`), nên file
test **không bao giờ tự arm** lại false positive. Class mới `TestCouncilArgvFalsePositiveR3`;
fixture `GROC_PLAIN_TEXT_REPLY` = 6 dòng text thuần, **không có JSON**, cụm từ nằm ở dòng 6 — mô
phỏng đúng `.council/live_raw_gro_grok-4.6-build.log`.

Helper `_finalize` của class R1 cũ được nâng thành `_finalize(self, raw, returncode=None)` và
forward `returncode=` — default `None` nên 7 test R1 giữ nguyên hành vi.

4 test name theo yêu cầu:

| # | Test name | Kịch bản | Kỳ vọng |
|---|-----------|----------|---------|
| a | `test_rc0_plain_text_harness_quoting_phrase_is_not_a_launcher_failure` | rc=0 + raw text thuần (0 dòng JSON) + có phrase | `False` |
| b | `test_rc_nonzero_bare_refusal_still_reported` | rc=1 + raw chỉ có đúng dòng refusal | `True` |
| c | `test_rc_none_bare_refusal_still_reported` | rc=None + raw chỉ có đúng dòng refusal | `True` |
| d | `test_rc_nonzero_long_plain_text_quoting_phrase_is_not_a_launcher_failure` | rc=1 + raw text thuần nhiều dòng có phrase | `False` (scope 3) |

Bonus (test thứ 5, để hằng số 3 không âm thầm trôi):
`test_third_scope_boundary_allows_wrapped_single_refusal` — rc=1 + 3 dòng non-empty bọc 1 dòng
refusal → vẫn `True`.

Test (a) assert cả 2 tiền đề khiến R2 sập: `assertIn(PHRASE, raw.lower())` và
`assertFalse(_council_harness_started(raw))` — tức chứng minh rõ ràng rằng chỉ có `returncode`
mới cứu được case này.
Test (d) assert `non_empty > _COUNCIL_ARGV_REFUSAL_MAX_LINES` để buộc test gắn với hằng số thật.

## 4. Pytest counts — BEFORE / AFTER

### BEFORE (chứng minh (a) và (d) THẬT SỰ fail trước patch)

Không dùng `git stash` (orchestrator_server.py có edit uncommitted khác cần bảo toàn). Thay vào đó
trích đúng 2 hàm `_council_harness_started` + `_council_launcher_argv_too_long` từ
`git show HEAD:orchestrator_server.py` (= R2 đã commit 3fb6f4b) qua AST rồi chạy 4 kịch bản:

```
R2 signature: (raw: str) -> bool
(a) rc=0 plain-text GroC  -> R2 says True | expected False  => FAIL(before)
(b) rc=1 bare refusal     -> R2 says True | expected True
(c) rc=None bare refusal  -> R2 says True | expected True
(d) rc=1 long prose       -> R2 says True | expected False  => FAIL(before)
```

→ **2/4 fail trước patch** ((a) và (d)), đúng như brief yêu cầu; (b) và (c) đã đúng từ R2 và patch
không được làm hỏng chúng.

Lưu ý trung thực: không thể chạy `pytest` "before" trực tiếp trên file test mới, vì test mới gọi
`_council_launcher_argv_too_long(raw, 0)` (2 tham số) và `_COUNCIL_ARGV_REFUSAL_MAX_LINES` — trên
code R2 chúng sẽ hỏng bằng `TypeError`/`AttributeError` (collection error) chứ không phải assertion
failure. Vì vậy phép chứng minh AST ở trên mới là bằng chứng hành vi đúng nghĩa.

### AFTER — file mục tiêu
```
./venv312/Scripts/python.exe -m pytest tests/test_council_argv_false_positive_r1.py -q
............                                                             [100%]
12 passed in 0.91s
```
→ **12 passed, 0 failed** = 7 test R1 cũ + 5 test R3 mới (4 bắt buộc + 1 boundary).

### AFTER — council subset (giống subset dùng ở R2)
```
./venv312/Scripts/python.exe -m pytest \
  tests/test_council_argv_false_positive_r1.py \
  tests/test_council_model_resolution_b1.py \
  tests/test_council_reply_golden.py \
  tests/test_council_message_b3.py \
  tests/test_council_async_dispatch.py \
  tests/test_council_status_b4.py -q
....................................................................   [100%]
68 passed, 2 subtests passed in 8.80s
```
→ **68 passed, 2 subtests passed, 0 failed**. Không có regression.

Dùng `./venv312/Scripts/python.exe` cho mọi lần chạy (KHÔNG dùng `python` bare = pythoncore-3.14,
thiếu `psutil`) — đúng chỉ dẫn.

## 5. Ràng buộc đã tôn trọng

- KHÔNG restart daemon. KHÔNG `git add` / `git commit` / `git stash`. Worktree để dirty.
- Edit uncommitted có sẵn trong `orchestrator_server.py` (migration model codo/ope/gro quanh dòng
  4410-4416) **được giữ y nguyên** — đã đọc lại `orchestrator_server.py:4409-4417` sau patch và xác
  nhận còn nguyên: `ope -> openai-compatible/grok-4.6-build`, `codo -> codex/grok-4.6-build`,
  `gro -> ltnai/grok-4.6-build`. `git diff -U0` không sinh hunk nào ở vùng 4410; mọi hunk của tôi
  nằm ở 4557+, 4612+, 7968, 8038, 8068.
- Line endings: file trên disk vốn đã là **bare LF** (baseline `_baseline_orchestrator_server_20260827.py`
  cũng bare LF) → patch KHÔNG gây churn EOL toàn file. Cảnh báo `LF will be replaced by CRLF` của
  git là hành vi `core.autocrlf` có từ trước, không phải do patch này.
- No scope creep: đúng 4 mục trong brief. Không sửa `_council_harness_started`, không sửa
  ledger/events writer, không sửa logic `result_ok`.
- Native edit trong chính cây [A] = đường AUTHORIZED theo CORE_RULE (`task_class=upgrade_orchestrator`).

## 6. KHÔNG verify được / cần chủ repo xác nhận

1. **Chưa chạy end-to-end trên daemon thật.** Daemon đang chạy pid 48800 / generation
   269d78d1eccf với code R2. Tôi bị cấm restart, nên **chưa có bằng chứng runtime** rằng một
   dispatch mới tới `gro/grok-4.6-build` sẽ cho `state=ok` thay vì `rejected`. Bằng chứng hiện có
   là unit-level + AST-diff. Cần Anh restart rồi dispatch lại 1 lần tới GroC để đóng vòng.
2. **Hằng số 3 chưa có dữ liệu thực chứng.** Tôi chỉ thấy đúng 1 mẫu launcher refusal thật, và nó
   là 1 dòng. Con số 3 là biên an toàn suy luận, chưa phải số đo. Nếu tồn tại launcher nào in >3
   dòng cùng với refusal, scope 3 sẽ bỏ sót một failure THẬT (false negative). Rủi ro thấp vì
   scope 3 chỉ áp dụng khi `returncode != 0` **và** không có envelope, nhưng đây là trade-off có ý
   thức, không phải chứng minh.
3. **Đường `returncode is None` vẫn là đường yếu còn lại.** Một run timeout trên harness non-JSON
   mà câu trả lời có nhắc cụm từ, và tổng số dòng non-empty <= 3, vẫn có thể false positive. Brief
   chỉ định giữ nguyên hành vi timeout nên tôi không mở rộng; ghi lại như nợ kỹ thuật.
4. **Chưa chạy full test suite.** Chỉ chạy file mục tiêu + council subset (68 test). `grep` chỉ
   thấy 3 caller của `_council_finalize_model_resolution` (7968, 8038, 8068) + 1 định nghĩa, nhưng
   tôi KHÔNG kiểm tra khả năng gọi gián tiếp qua `getattr`/dispatch table.
5. **File scratch còn lại:** `.council/_r3_before/os_head.py` (bản `git show HEAD:orchestrator_server.py`
   dùng để chứng minh BEFORE) và `.council/_r3_before/report_body.md`. Tôi **không tự xóa** vì
   delete là destructive gate riêng. Cả hai untracked nên không ảnh hưởng git chain — Anh quyết định
   giữ hay xóa.

END_CLAUC_R3_ARGVFALSE
