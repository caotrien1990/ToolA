# Known Bugs & Design Proposals

Bug reports + design improvement proposals cho [A] (project-orchestrator).

**Bug format (B###):**
- ID (B###) + title
- Status (open / investigating / workaround / fixed)
- Reproduction
- Root cause analysis (best guess nếu chưa confirm)
- Workaround
- Severity (CRITICAL / HIGH / MEDIUM / LOW)

**Design proposal format (R###):**
- ID (R###) + title
- Status (proposed / accepted / implemented)
- Motivation (data-driven từ session thực tế)
- Proposal (concrete code change)
- Expected impact (savings estimated)
- Backward compatibility
- Effort estimate

File bug cũng được log qua `[A] MCP create_pending_audit` để audit trail.
File proposal là initiative của Ope/Anh, không bắt buộc theo AGENTS.md global rule.

---

## B001 — `write_fix_html` throws `str < int` TypeError on long backslash Windows paths

**Status**: fixed
**Severity**: HIGH (blocks T2 mutating work for any project on long Windows path)
**Discovered**: 2026-06-04 by Ope during on-open live-sync inject into `mcp_175_tool_rag.html`
**Affects**:
- `[A] MCP write_fix_html` (T2)
- Likely `[A] MCP write_fix` (same code path) and `[A] MCP write_transaction` (delegates to write_fix)
- Possibly `[A] MCP replace_between_anchors` (similar but separate error: "Tag mismatch 0 <script> vs 1 </script>")

### Summary

Khi gọi `write_fix_html` với `project_path` chứa **long backslash Windows path với 2+ spaces** (vd `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`), tool trả về `SCHEMA_VIOLATION / WRONG_ARGUMENTS` với Python TypeError `'<'' not supported between instances of ''str'' and ''int''` tại phase=schema. Bug xảy ra TRƯỚC khi hàm `write_fix_html` thực sự được gọi — chắc chắn ở FastMCP / Pydantic argument validation layer.

### DONE / Verified (2026-06-04)

- **B001 FIXED**: generic `str` -> `int` coercion added at the dispatcher boundary in `orchestrator_server.py` after the missing-required check (R91-G). Verified via API: numeric strings coerce to int, bad strings return `INVALID_FIELD`, bool is rejected, and `None` passes through. Scope covers all 78 int-param tools found by the audit.
- **R101 DONE + verified**: opt-in minimal-response `fields=` filter implemented at the dispatcher `ContextVar` choke point. Observed savings: ~49% on success responses and ~96% on error responses.
- **get_rule_core_text DONE + verified**: once-guard implemented; repeated same-agent/TTL calls return a tiny already-fetched stub. Observed savings: ~96% on repeat calls.
- **desktop_send_text DONE + verified**: new T4d tool registered through `TOOL_MAP`, tier metadata, `PROTECTED_TOOL_NAMES`, `_v15_schema`, and inventory. Matrix audit confirmed `drift_count=0`.
- **Adjudication recorded**: DENY-A AGREED (trigger is argument type, not path length); DENY-B AGREED (scope is any int-param tool); DENY-C AGREED (only generic type validation/coercion was absent, field-name validation already existed).

### Reproduction

**Test environment**: Windows 11, Python 3.12, [A] running on `localhost:8083`, OpenCode client, MCP transport.

**Test 1 — gọi KHÔNG có `project_path`** → PASS (returns `BACKUP_REQUIRED`):
```json
{
  "file_path": "examples/mcp_175_tool_rag.html",
  "start_line": 1, "end_line": 1,
  "new_content": "OnRvb3R7fQ==",
  "content_encoding": "base64"
}
```

**Test 2 — gọi với short backslash path (no spaces)** → PASS:
```json
{ "project_path": "D:\\test\\project", ... }
```

**Test 3 — gọi với forward-slash path** → PASS:
```json
{ "project_path": "D:/test/project", ... }
```

**Test 4 — gọi với spaces trong backslash path** → PASS:
```json
{ "project_path": "D:\\test project\\with spaces", ... }
```

**Test 5 — gọi với non-ASCII trong short path** → PASS:
```json
{ "project_path": "D:\\test \\u00ea non-ascii\\project", ... }
```

**Test 6 — gọi với REAL project path (7 components, 2 spaces, ASCII only)** → **FAIL**:
```json
{
  "project_path": "D:\\0. TRIEN\\1. DATA\\DATA\\5. VIBE APP\\1. APP\\8. ToolA\\project-orchestrator",
  "file_path": "examples/mcp_175_tool_rag.html",
  "start_line": 1, "end_line": 1,
  "new_content": "OnRvb3R7fQ==",
  "content_encoding": "base64"
}
```

→ Response:
```json
{
  "ok": false,
  "error": "Wrong arguments for 'write_fix_html': '<' not supported between instances of 'str' and 'int'",
  "error_name": "WRONG_ARGUMENTS",
  "type": "SCHEMA_VIOLATION",
  "phase": "schema"
}
```

### Test Matrix

| `project_path`                              | Components | Spaces | Non-ASCII | Result            |
|---------------------------------------------|------------|--------|-----------|-------------------|
| (absent)                                    | -          | -      | -         | BACKUP_REQUIRED ✓ |
| `D:/test/project`                           | 3          | 0      | no        | BACKUP_REQUIRED ✓ |
| `D:\test\project`                           | 3          | 0      | no        | BACKUP_REQUIRED ✓ |
| `D:\test project\with spaces`               | 3          | 2      | no        | BACKUP_REQUIRED ✓ |
| `D:\TRIEN\project\root`                     | 3          | 0      | no        | BACKUP_REQUIRED ✓ |
| `D:\test ê\project`                         | 3          | 0      | yes       | BACKUP_REQUIRED ✓ |
| `D:\0. TRIEN\1. DATA\5. VIBE\14. OPENCODE\project-orchestrator` | 5 | 2 | no | BACKUP_REQUIRED ✓ |
| `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator` | 7 | 2 | no | **WRONG_ARGUMENTS str/int** ✗ |
| `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\extras` | 8 | 2 | no | BACKUP_REQUIRED ✓ |

### Observation

- Bug chỉ xảy ra khi `project_path` có >= 7 path components VÀ spaces.
- Số components / spaces / non-ASCII alone không trigger.
- Có vẻ là tổ hợp (path length × components × spaces) đạt ngưỡng nào đó.
- Bug KHÔNG deterministic rõ ràng — đã thấy 1 case 7 components PASS, 1 case FAIL. Cần test thêm để xác nhận pattern chính xác.

### Root Cause Analysis (CONFIRMED framework + dispatch path; line-level source NOT pinpointed)

**Framework CONFIRMED: `zeromcp`** (NOT FastMCP) — `venv312\Lib\site-packages\zeromcp\{mcp.py,jsonrpc.py}`. zeromcp có `isinstance(value, expected_type)` validation tại `jsonrpc.py:232` (raise `JsonRpcException` -32602), nhưng validation này **bị bypass** trên call path của B001.

**Dispatch path CONFIRMED (shared by MCP `call_tool` + HTTP `/api/call`):**
- MCP `call_tool(name, payload)` (orchestrator_server.py:3646) → `_mcp_call_text` (L3632) → `dispatch_tool_call` (L2586) → `_dispatch_tool_call_request` (L2606) → `ct.TOOL_MAP.get(tool_name)` (L1969) → `await fn(**body)` (L2384).
- HTTP `/api/call/{tool_name}` → `handle_call` (L2604) → **cùng** `_dispatch_tool_call_request`.
- **Cả 2 paths đều gọi trực tiếp async function từ `ct.TOOL_MAP`, KHÔNG qua zeromcp's `_call()` validation layer.**

**Error wrapper CONFIRMED:** `orchestrator_server.py:2438-2459`:
```python
except TypeError as exc:
    return _error_response(
        tool_name, f"Wrong arguments for '{tool_name}': {exc}{hint}", 400, cors, body,
        error_name="WRONG_ARGUMENTS", ...
    )
```
→ Bất kỳ `TypeError` raised bên trong function chain đều bị rewrap thành `WRONG_ARGUMENTS / SCHEMA_VIOLATION` với `phase: "schema"` (mislabel — phase thực ra là `runtime`).

**R91 coercion pattern CONFIRMED:** `orchestrator_server.py:2146-2179`:
```python
# R91: MCP/HTTP clients sometimes serialize numeric workflow fields as
# strings. Coerce at the dispatcher boundary so workflow_submit_step can
# compute STEP_MISMATCH deltas instead of crashing with str-int TypeError.
if tool_name == "workflow_submit_step":
    if "step" in body:
        try: body["step"] = int(body["step"])
        except Exception: return _error_response(... "INVALID_STEP" ...)
```
→ R91 chỉ handle `workflow_submit_step` (fields `step`, `step_range`). KHÔNG có coercion tương tự cho T2 write tools (`write_fix_html`, `write_fix`, `write_new_file`).

**Suspect functions RULED OUT (clean of str<int compares in call chain):**
- `code_tools.py:write_fix` (L2012) — `if start_line < 1 or start_line > total` (L2126): int<1, int>int — fine
- `code_tools.py:write_fix_html` (L2302) — only bool/str ops
- `code_tools.py:validate_file` (L11041) — only int compares
- `code_tools.py:_decode_text_payload` (L678) — no compares
- `code_tools.py:_unified_diff_preview` (L788) — no compares
- `code_tools.py:_store_dry_run` (L817) — no compares
- `code_tools.py:_resolve_project_file` (L458) — no compares
- `code_tools.py:find_project_root` (L161) — no compares
- `code_tools.py:_resolve_project_path_payload` (L200) — no compares
- `code_tools.py:_backup_manifest_status` (L487) — no compares
- `code_tools.py:_normalize_body` (orchestrator_server.py:464) — no str<int
- `zeromcp/jsonrpc.py:_call` (L94) — uses `isinstance`, no str<int
- `zeromcp/mcp.py:_type_to_json_schema` (L612) — no compares
- `zeromcp/mcp.py:_generate_tool_schema` (L677) — no compares

**Candidate found (RED HERRING):** `code_tools.py:3232` có pattern `line_no <= len(txt.splitlines())` — nhưng nằm trong `obsolete_payload_in_harness` markdown scanner, KHÔNG nằm trong `write_fix_html` call chain.

**Hypothesis (best guess):** Bug surface là ở các helper path-length-dependent chưa kiểm tra:
- `path_runtime.find_orchestrator_dir` (gọi từ `_session_project_key:503`)
- Backup gate (orchestrator_server.py:2215-2230) resolves `project_path` via `find_project_root`
- Path-dependent cache lookup
- Path-dependent alias mapping
- Path-dependent JSON dump (vd `ensure_ascii=True` vs `False` flip)

Để pinpoint chính xác line, cần thêm `logger.exception(...)` tại `orchestrator_server.py:2438`, restart [A] qua `start_orchestrator.bat`, reproduce bug, đọc log.

### Workaround

1. **Omit `project_path`** (chỉ dùng `file_path` relative): works cho test environment hiện tại vì MCP session đã set `project_path` rồi. Trong trường hợp thực tế, Ope phải tạo session trước, sau đó gọi tool mà không truyền `project_path` — schema sẽ dùng giá trị từ session.
2. **Forward-slash path** (`D:/test/project` thay vì `D:\test\project`): bypass trigger trong một số case.
3. **Native fallback**: dùng raw `edit` tool (CA session), log `create_pending_audit` + `log_native_event`. Tốn 1 extra audit entry nhưng work reliably.
4. **Move project to short ASCII path** (vd `C:\dev\po`): ultimate fix nhưng disruptive.

### Recommended Fix

**Patch R91-extended: add str→int coercion cho T2 write tools trong `_dispatch_tool_call_request` boundary.**

Location: `orchestrator_server.py`, chèn ngay sau block R91 hiện tại (sau L2179, trước `_backup_gated_tools` ở L2181):

```python
# R91-extended: same coercion pattern cho T2 write tools.
# Reason: write_fix_html/write_fix/write_new_file/apply_after_dry_run/refresh_project_map/generate_project_map
# accept numeric fields (start_line, end_line, dry_run=False, force=False, timeout=N)
# and pass them as int into underlying functions. If client (MCP or HTTP)
# sends these as JSON string, [A] dispatcher bypasses zeromcp validation
# (uses ct.TOOL_MAP directly at L1969) and the inner function crashes with
# str<int TypeError -> rewrapped as WRONG_ARGUMENTS / SCHEMA_VIOLATION.
_NUMERIC_FIELDS_BY_TOOL = {
    "write_fix":         ["start_line", "end_line"],
    "write_fix_html":    ["start_line", "end_line"],
    "write_new_file":    [],  # bool dry_run, overwrite - separate handling if needed
    "apply_after_dry_run": [],
    "refresh_project_map": [],
    "generate_project_map": [],
    "read_file_range":   ["start_line", "end_line"],
    "delete_file":       [],
    "validate_file":     [],
    "validate_symbols":  [],
}
if tool_name in _NUMERIC_FIELDS_BY_TOOL:
    for _fld in _NUMERIC_FIELDS_BY_TOOL[tool_name]:
        if _fld in body and not isinstance(body[_fld], (int, float)):
            try:
                body[_fld] = int(body[_fld])
            except (TypeError, ValueError):
                return _error_response(
                    tool_name,
                    f"{_fld} must be an integer",
                    400, cors, body,
                    error_name=f"INVALID_{_fld.upper()}",
                    accepted_fields=sorted(allowed_params),
                    ca_next_action=f"send_{_fld}_as_integer",
                )
```

**Alternative fix (root-level, harder):** Add `isinstance(value, (int, str))` type check at the buggy comparison site. Requires adding `logger.exception()` at L2438 first, restart [A], reproduce, then patch the line. (Likely in `code_tools.py:write_fix:2126` or `code_tools.py:write_fix_html:2313` or one of the helpers in call chain.)

**Defensive fix (cheapest, in `write_fix_html` wrapper):** Add type coercion at function entry, mirroring the existing `force` str-to-bool pattern at L2313-2314:

```python
# In code_tools.py:write_fix_html (L2302), add at top:
if isinstance(start_line, str):
    try: start_line = int(start_line)
    except (TypeError, ValueError): pass
if isinstance(end_line, str):
    try: end_line = int(end_line)
    except (TypeError, ValueError): pass
# Same in write_fix (L2012).
```

This is the most surgical — 6 lines per function — and matches existing pattern.

### Related Bugs

- **B002** (suspected) — `replace_between_anchors` throws `"Tag mismatch: 0 <script> vs 1 </script>"` trên cùng file. Same workflow (try anchor-based replace on `mcp_175_tool_rag.html` line 2 → fail). Root cause có thể liên quan (cùng FastMCP validation issue) hoặc riêng (delimiter counting bug).
- **B003** — `[A] /api/tools` `Access-Control-Allow-Origin` chỉ allow `http://localhost:8083` (same-origin only). Blocks browser fetch từ port khác. Cần update CORS middleware allow `*` hoặc multi-origin.

### Audit Trail

- Session: `i-chi-u-html-tool-rag-v-i-h-th-ng-a-06040015`
- 4 native fallback events logged via `[A] MCP log_native_event` (transport=native, tool=edit, target=examples/mcp_175_tool_rag.html)
- 1 pending audit via `[A] MCP create_pending_audit` (id: `pa-9e9f278aced94897`, status: pending)
- Test scripts: `C:\Users\ADMIN\AppData\Local\Temp\opencode\verify_banner.js`, `verify_final.js`, `validate_html.js`, `find_str_lit_cmp.js`, `find_str_cmp2.js`
- Investigation reads: `orchestrator_server.py:97,464,1965,2383,2410,2438,2586,3620,3632,3700,3813,3877,3897`, `code_tools.py:161,200,458,487,678,788,817,2012,2302,3232,11041,13167`, `venv312\Lib\site-packages\zeromcp\mcp.py:432,600,612,677`, `venv312\Lib\site-packages\zeromcp\jsonrpc.py:1-256 (full)`
- Last update: 2026-06-04 (RCA upgraded to "framework+dispatch CONFIRMED, line-level TBD")

---

## Quick-Reference: Patches Ready for Verification

### B001 Patch 1 — `orchestrator_server.py` (R91-extended at dispatcher boundary)

Insert after line 2179 (after `workflow_submit_step` R91 block, before `_backup_gated_tools`):

```python
# R91-extended: str→int coercion cho T2 write tools
_NUMERIC_FIELDS_BY_TOOL = {
    "write_fix":       ["start_line", "end_line"],
    "write_fix_html":  ["start_line", "end_line"],
    "read_file_range": ["start_line", "end_line"],
}
if tool_name in _NUMERIC_FIELDS_BY_TOOL:
    for _fld in _NUMERIC_FIELDS_BY_TOOL[tool_name]:
        if _fld in body and not isinstance(body[_fld], (int, float)):
            try:
                body[_fld] = int(body[_fld])
            except (TypeError, ValueError):
                return _error_response(
                    tool_name, f"{_fld} must be an integer", 400, cors, body,
                    error_name=f"INVALID_{_fld.upper()}",
                    accepted_fields=sorted(allowed_params),
                    ca_next_action=f"send_{_fld}_as_integer",
                )
```

### B001 Patch 2 — `code_tools.py` (defensive at function entry, surgical)

In `write_fix` (L2012) and `write_fix_html` (L2302), add at top of function body (before existing logic):

```python
for _fld_name, _fld_val in (("start_line", start_line), ("end_line", end_line)):
    if isinstance(_fld_val, str):
        try: globals()[_fld_name] = int(_fld_val)
        except (TypeError, ValueError): pass
```

(Or use the cleaner explicit two-line version per field.)

### B003 Patch — `orchestrator_server.py` CORS

In CORS handler, change single-origin to wildcard (or multi-origin list):

```python
# Current: access-control-allow-origin: http://localhost:8083
# Fix:    use request Origin header, or wildcard "*"
```

### Verification Steps for B001

1. Apply Patch 1 OR Patch 2
2. `py_compile` patched file
3. Restart [A] via `[A:P]\project-orchestrator\start_orchestrator.bat` (do NOT use `restart_orchestrator_via_task`)
4. Wait for `[A:API]/health` to return ok
5. Re-run failing test (Test 6 from reproduction)
6. Expected: returns `BACKUP_REQUIRED` (same as 5/8 passing cases) instead of `WRONG_ARGUMENTS / SCHEMA_VIOLATION`
7. If still fails, add `logger.exception(...)` at `orchestrator_server.py:2438` and read `[A] log` for full stack trace → pinpoint line-level source

---

## R-Series — Design Improvements (proposed, data-driven từ session 2026-06-04)

> **Context**: Trong session inject on-open live-sync vào `mcp_175_tool_rag.html`, Ope phân tích token waste ratio của [A] MCP responses. Phát hiện **~91% tokens bị CA bỏ qua** (32k / 35k total). Root cause: (1) tool discovery eager-load 175 tools, (2) response luôn trả 15+ metadata fields kể cả khi CA chỉ cần 2-3, (3) một số field redundant (ca_next_action + ca_guidance nested).
>
> R-series đề xuất patches để giảm overhead từ 3-7x native xuống ~1-1.5x native, giữ governance + audit.

### Session Waste Data (baseline)

| [A] call category | Calls | Total tokens | Useful | Wasted | Waste % |
|---|---|---|---|---|---|
| Discovery (tool_names) | 2 | ~5,500 | ~100 | ~5,400 | 98% |
| 4KTR audit (audit_tool_matrix) | 2 | ~13,000 | ~40 | ~12,960 | 99.7% |
| B001 repro (write_fix_html) | 4 | ~2,200 | ~160 | ~2,040 | 93% |
| B002 repro (replace_between_anchors) | 2 | ~700 | ~20 | ~680 | 97% |
| B001 trigger test (optional_tool_probe) | 9 | ~2,700 | ~45 | ~2,655 | 98% |
| HTML verify (read_file_range + validate_file) | 12 | ~3,000 | ~2,200 | ~800 | 27% (content-heavy) |
| Audit/log (log_native_event + create_pending_audit + manage_agent + log_native) | 8 | ~1,500 | ~50 | ~1,450 | 97% |
| Diagnostic (search_code + find_symbol) | 4 | ~2,000 | ~150 | ~1,850 | 93% |
| Session/summary (resolve_project_path + get_project_summary + list_sessions + open_session) | 4 | ~1,500 | ~80 | ~1,420 | 95% |
| Schema lookup (tool_schema) | 4 | ~3,000 | ~200 | ~2,800 | 93% |
| **TOTAL** | **~50** | **~35,100** | **~3,045** | **~32,055** | **91%** |

→ **R-series aim**: reduce waste từ 91% xuống 10-20% qua 3 patches dưới.

---

## R101 — Minimal response mode (`?fields=` query param)

**Status**: implemented
**Severity**: MEDIUM (performance/token-efficiency, không break functionality)
**Discovered**: 2026-06-04 by Ope during 4KTR audit + B001 investigation
**Affects**: mọi `[A] MCP call_tool` invocation, `[A] HTTP /api/call/{tool}` request

### Motivation

Mỗi [A] response hiện tại trả **15+ metadata fields** luôn luôn (kể cả success):

```json
{
  "ok": true,
  "data": "...",
  "error": "", "error_name": "", "reason": "",
  "ca_next_action": "...", "ca_guidance": {...},
  "retry_sequence": [...], "hint": "...",
  "accepted_fields": [...], "accepted_aliases": {...},
  "payload_example": {...}, "corrected_payload": {...},
  "phase": "runtime", "severity": "info",
  "write_performed": false, "cache_synced": true,
  "new_hash": "...", "new_mtime": 1234.5,
  "path_normalized": [...], "suggested_alternative_tool": null,
  "sequential_chain_hint": "...",
}
```

Trong session thực tế, **CA chỉ dùng 2-5 fields/call**:
- Success: `ok` + 1-2 domain fields (`new_hash`, `session_id`, `file_content`, `pending_audit_id`)
- Error: `ok` + `error` + `error_name` (+ đôi khi `ca_next_action`)

→ **75-90% response bị đọc rồi bỏ qua** = pure waste.

### Proposal

Thêm `?fields=` query parameter cho HTTP `/api/call/{tool}` endpoint. [A] filter response trước khi trả về.

**File**: `orchestrator_server.py:handle_call` (~L2604)

```python
async def handle_call(request):
    # ... existing CORS, tool_name, body parsing ...
    
    # R101: parse ?fields= filter
    _fields_filter = None
    _fp = request.query.get("fields", "").strip()
    if _fp:
        _fields_filter = set(_fp.split(",")) | {"ok"}  # always keep ok
    
    # ... existing dispatch, try/except, result building ...
    
    # Trước return web.json_response:
    if _fields_filter and isinstance(_result, dict):
        _result = {k: v for k, v in _result.items() if k in _fields_filter}
        # Preserve JSON-RPC envelope: wrap minimal result
    return web.json_response(_result, headers=cors)
```

**Cách CA dùng:**

```bash
# Default (backward compat): full response
POST /api/call/write_fix_html
→ ~700 tokens

# Opt-in minimal: chỉ field cần thiết
POST /api/call/write_fix_html?fields=ok,error,error_name
→ ~50 tokens (saved ~93%)

# Common patterns:
?fields=ok,new_hash                    # write_fix success
?fields=ok,session_id                  # open_session success
?fields=ok,error,error_name,ca_next_action  # error path
?fields=ok,file_content,total_lines    # read_file_range
?fields=ok,pending_audit_id            # create_pending_audit
```

### Expected Impact (per this session)

| Call type | Default | With R101 | Saved |
|---|---|---|---|
| write_fix_html FAIL × 4 | ~2,200 | ~200 | **-91%** |
| replace_between_anchors FAIL × 2 | ~700 | ~60 | **-91%** |
| optional_tool_probe × 9 | ~2,700 | ~90 | **-97%** |
| log_native_event × 4 | ~1,200 | ~80 | **-93%** |
| create_pending_audit × 1 | ~200 | ~30 | **-85%** |
| manage_agent × 1 | ~250 | ~10 | **-96%** |
| read_file_range × 10 | ~2,800 | ~2,000 (content still needed) | -29% |
| **Subtotal (R101 alone)** | **~10,050** | **~2,470** | **-75%** |

### Backward Compatibility

- Default behavior (no `?fields=`): full response như cũ → existing clients không break.
- Opt-in minimal: CA explicit thêm `?fields=...` cho từng call.
- Migration path: Ope/Anh update calls dần dần; existing audits vẫn work.

### Effort Estimate

- **LOC changed**: ~15 (1 query parse + 1 dict comprehension trước return)
- **Risk**: Low (additive, không modify existing behavior)
- **Time**: 1 hour
- **Verification**: 
  1. Apply patch
  2. `py_compile orchestrator_server.py`
  3. Restart [A] qua `start_orchestrator.bat` (KHÔNG dùng `restart_orchestrator_via_task`)
  4. Poll `/health` until ok
  5. Test 2 paths: (a) no `?fields=` → full response như cũ, (b) `?fields=ok,error` → minimal response
  6. Compare token counts: tool output should be ~85% smaller

---

## R102 — Category-filtered tool discovery

**Status**: implemented
**Severity**: MEDIUM (token efficiency, không break functionality)
**Discovered**: 2026-06-04 — `tool_names()` full discovery trả 175 tools = ~5KB tokens, CA chỉ dùng 5-10
**Affects**: `[A] MCP tool_names` (core MCP), `[A] HTTP /api/tools`

### Motivation

`tool_names()` (no filter) returns **175 tools × ~30 tokens/tool summary = ~5,500 tokens**. Trong session này, CA load 175 tools nhưng chỉ thực sự dùng 5-10 (`write_fix_html`, `write_fix`, `audit_tool_matrix`, `optional_tool_probe`, `log_native_event`, `create_pending_audit`, `read_file_range`, `search_code`, `find_symbol`, `manage_agent`).

→ **98% tool summaries bị load nhưng không dùng**.

Tier filter hiện tại (`?tier=T2`) trả ~30 tools — vẫn quá nhiều cho 1 task. CA cần **category-based** filter (write_html, audit, session, browser) để load 3-8 tools cùng nhóm.

### Proposal

Thêm `?category=` query param cho `/api/tools` (HTTP) và `tool_names` (MCP). Map category → list tool names.

**File**: `orchestrator_server.py:tool_names` (MCP core, ~L3635) + handle_list_tools (HTTP, ~L1878)

```python
# In tool_names / handle_list_tools, sau existing tier/name_contains filter:

_CATEGORY_TOOLS = {
    "write_html": [
        "write_fix_html", "write_fix", "write_transaction",
        "replace_between_anchors",
    ],
    "write": [
        "write_fix", "write_fix_html", "write_new_file",
        "delete_file", "apply_after_dry_run",
    ],
    "read": [
        "read_file_range", "find_symbol", "search_code",
        "query_index", "get_file_symbols", "get_project_summary",
    ],
    "audit": [
        "audit_tool_matrix", "log_native_event",
        "create_pending_audit", "sync_pending_audit", "log_native",
    ],
    "session": [
        "open_session", "close_session", "list_sessions",
        "rename_session", "get_session_events", "prepare_ca_session",
    ],
    "browser": [
        "list_cdp_targets", "cdp_attach_target", "launch_electron_with_cdp",
        "fill_value", "click", "evaluate_js", "screenshot_page",
        "read_console_errors",
    ],
    "diagnostic": [
        "optional_tool_probe", "protected_tools_status",
        "audit_tool_matrix", "validate_file", "validate_symbols",
    ],
}

_category = request.query.get("category", "").strip()
if _category:
    if _category == "_list":
        return {"ok": True, "categories": list(_CATEGORY_TOOLS.keys()),
                "total": len(_CATEGORY_TOOLS)}
    if _category not in _CATEGORY_TOOLS:
        return _error_response(..., error_name="UNKNOWN_CATEGORY",
                                accepted_categories=list(_CATEGORY_TOOLS.keys()))
    _filter_set = set(_CATEGORY_TOOLS[_category])
    tools_summary = [t for t in tools_summary if t["name"] in _filter_set]
```

**Cách CA dùng:**

```bash
# Default (backward compat): full list
GET /api/tools
→ 175 tools, ~5,500 tokens

# List categories
GET /api/tools?category=_list
→ 7 category names, ~30 tokens

# Category filter
GET /api/tools?category=write_html
→ 4 tools, ~120 tokens (saved ~98%)

# Combine với tier (optional)
GET /api/tools?category=write&tier=T2
→ 5 tools matching
```

### Expected Impact (per this session)

| Discovery scenario | Default | With R102 | Saved |
|---|---|---|---|
| Initial discovery (no filter) | ~5,500 | ~120 (`category=write_html`) | **-98%** |
| Re-discovery (after change) | ~5,500 | ~90 (`category=audit`) | **-98%** |

→ Tiết kiệm **~5,400 tokens** trong session này (1-shot discovery).

### Backward Compatibility

- Default (no `?category=`): full 175 tools như cũ → existing clients không break.
- Opt-in: CA explicit thêm `?category=...` cho 1-shot discovery.
- Special `category=_list` returns category names (for CA bootstrap).
- Unknown category returns 400 với `accepted_categories` list (giống pattern `accepted_fields`).

### Effort Estimate

- **LOC changed**: ~40 (1 dict + 1 filter block + error path)
- **Risk**: Low-Med (additive, new param)
- **Time**: 1 hour
- **Verification**:
  1. Apply patch
  2. `py_compile`
  3. Restart [A]
  4. Test: `/api/tools?category=write_html` → trả 4 tools
  5. Test: `/api/tools?category=_list` → trả 7 category names
  6. Test: `/api/tools?category=invalid` → 400 với `accepted_categories`
  7. Test: `/api/tools` (no filter) → 175 tools như cũ

---

## R103 — Drop redundant `ca_guidance` metadata

**Status**: proposed (not implemented)
**Severity**: LOW (clarity vs token trade-off, không break functionality)
**Discovered**: 2026-06-04 — `ca_next_action` (flat string ~20 tokens) + `ca_guidance` (nested dict ~200 tokens) duplicate thông tin
**Affects**: mọi error response, default metadata envelope

### Motivation

Trong error response, [A] trả CẢ HAI:
- `ca_next_action`: "call_refresh_project_map" (flat string, ~20 tokens)
- `ca_guidance`: `{tool_category, retry_sequence, notes}` (nested object, ~200 tokens)

Trong session này, CA chỉ đọc `ca_next_action`. `ca_guidance` 100% waste.

Tương tự các cặp redundancy khác:
- `payload_example` (~200 tokens) — chỉ cần khi error
- `accepted_fields` (~60 tokens) — chỉ cần khi error
- `accepted_aliases` (~50 tokens) — chỉ cần khi error
- `phase` (~10 tokens) — chỉ cần khi error
- `severity` (~10 tokens) — chỉ cần khi error
- `hint` (~50 tokens) — trùng với `ca_next_action` thường

### Proposal

**Option A (conservative)**: Conditional metadata — chỉ build khi `?fields=` includes them.
- Build full envelope as before
- Apply `?fields=` filter at end (R101 already does this)
- CA explicitly request `?fields=ok,error,ca_guidance` nếu cần

**Option B (aggressive)**: Drop `ca_guidance` mặc định, keep `ca_next_action`.
- Remove `setdefault("ca_guidance", _ca_guidance(...))` từ `_error_response` (~L2400)
- Add `?fields=ca_guidance` opt-in via R101
- Cleaner default, but changes existing audit/response shape

**Recommendation**: Option A (no breaking change, leverage R101).

### Expected Impact (per this session)

| Field dropped | Calls where present | Tokens/call | Total saved |
|---|---|---|---|
| `ca_guidance` | 4 write_fix_html FAIL + 2 replace + 1 probe FAIL | ~200 | ~1,400 |
| `payload_example` (only on error) | 7 error responses | ~200 | ~1,400 |
| `hint` (redundant with ca_next_action) | 7 error responses | ~50 | ~350 |
| **Subtotal** | | | **~3,150** |

### Backward Compatibility

- **Option A**: Zero breaking change. CA reads `ca_guidance` qua `?fields=ca_guidance`.
- **Option B**: Breaking — existing audits/clients expecting `ca_guidance` sẽ miss. Cần version bump hoặc migration window.

→ Recommend Option A. Effort minimal, zero risk.

### Effort Estimate

- **LOC changed**: ~0 (just R101 already covers this)
- **Risk**: None (R101 already filters)
- **Time**: 0 (R103 is documentation/discovery, not code)

---

## Combined Impact (R101 + R102 + R103)

| Metric | Baseline | Sau R-series | Reduction |
|---|---|---|---|
| Per-call avg (non-discovery) | ~700 | ~80-150 | **-78-85%** |
| Discovery 1-shot | ~5,500 | ~120 | **-98%** |
| Per-session total | ~35,000 | ~5,000-7,000 | **-80-85%** |
| **vs Native (~50-200/call)** | 3-7x | **1-1.5x** | **gần tương đương** |

### Implementation Order (recommended)

1. **R101** (highest ROI, lowest risk) — 1 hour, ~15 LOC
2. **R102** (high ROI, low risk) — 1 hour, ~40 LOC
3. **R103** (zero-code, just leverage R101) — 0 hour

→ Total: 2 hours, ~55 LOC, expected 80-85% token savings.

### Audit Trail

- Session: `i-chi-u-html-tool-rag-v-i-h-th-ng-a-06040015`
- Pending audit: `pa-9e9f278aced94897` (B001 native fallback)
- R-series proposals derived từ: same session, 50 [A] calls, 35k total tokens, 91% waste ratio
- File này KHÔNG tạo pending_audit riêng cho R-series (proposals, không phải bug)

---

---

## Campaign-1608 (2026-08-20) — eight defects found while auditing a weak-model campaign

All eight are **fixed** and each is pinned by a mutation-verified test. Symptom-first
triage for every one of them lives in `MAINTENANCE_DOCTOR.md`; this section is the
defect registry entry. The recurring theme is stated there as a rule: the first
plausible cause was wrong in every single case. B008 was found last, while
verifying B006 live — the same rule applied to this campaign's own output.

---

## B002 — A stale `pytest-current` symlink aborts the whole suite at collection

**Status**: fixed
**Severity**: CRITICAL (no test runs at all; there is no smaller signal)
**Discovered**: 2026-08-20 by ClauLTN
**Affects**: every `pytest` invocation on this box

### Reproduction

`venv312\Scripts\python.exe -m pytest tests\ -q` aborts before any test executes:

```
PermissionError: [WinError 5] Access is denied:
'C:\Users\ADMIN\AppData\Local\Temp\pytest-of-ADMIN\pytest-current'
```

### Root cause

`pytest-current` is a symlink left behind by a killed run. On Windows an orphaned
symlink can be readable-but-not-stat-able, and `Path.exists()` propagates EACCES
instead of returning False. `_pytest/pathlib.py::cleanup_dead_symlinks` calls
`.exists()` in a bare loop, so pytest's own temp-dir garbage collector takes the
session down.

**Misdiagnosis that cost the most:** the path sits under `%LOCALAPPDATA%\Temp`,
which was read as "OneDrive is syncing the temp dir". `%LOCALAPPDATA%` is never
OneDrive-redirected. That wrong conclusion produced B003.

### Fix

`conftest.py` (marked `A54`) replaces `cleanup_dead_symlinks` with a version whose
fallback loop is **per-entry** — not a blanket `try/except`, which would stop
collecting garbage at the first unreadable entry and leak every dead link behind
it. Pinned by `tests/test_conftest_stale_symlink_tolerance.py` (5 tests). Reverting
the shim kills the entire session with the original error.

### Workaround (symptom only)

Deleting `%LOCALAPPDATA%\Temp\pytest-of-ADMIN\pytest-current` unblocks the run, but
the next killed run recreates it.

---

## B003 — `--basetemp` on `D:` saturates a HDD and looks exactly like a locked folder

**Status**: fixed
**Severity**: HIGH (unrelated directories stop opening; reads as a filesystem lock)
**Discovered**: 2026-08-20 by ClauLTN, confirmed by an independent session's report
**Affects**: `README.md` guidance, and every suite run that followed it

### Reproduction

Run the ~2430-test suite with `--basetemp` pointing anywhere on `D:`. During the
run, `du -sh` on an unrelated sibling directory hangs past 120s and Explorer will
not open the folder. The moment pytest stops, the same command completes in 1s.

### Root cause

`D:` is a **HDD**. The suite's temp I/O saturates the spindle. The symptom presents
as a held handle, so the instinct is to hunt for a process — measure **% Idle Time
/ IOPS** instead of bytes. A *fixed* basetemp path is a second, independent bug:
pytest wipes its basetemp at startup, so two agents running the suite concurrently
delete each other's fixtures (`test_graph_enabled_live_flag_a13.py` already
recorded this).

### Fix

`README.md` no longer recommends `--basetemp=tests\tmp_fresh`; the default `%TEMP%`
(SSD, per-run, never OneDrive-redirected) is correct. The last test in
`tests/test_conftest_stale_symlink_tolerance.py` enforces the distinction repo-wide:
a hardcoded drive letter fails, a path resolved through `tempfile.gettempdir()` or
`%TEMP%` passes. Measured effect: full suite 699s -> 352s.

---

## B004 — A test deadline tighter than the contract it tests

**Status**: fixed
**Severity**: MEDIUM (one intermittent failure that masqueraded as a lock bug)
**Discovered**: 2026-08-20 by ClauLTN
**Affects**: `tests/test_orchestrator_storage.py`

### Reproduction

`test_append_jsonl_parallel_process_thread_keeps_all_lines` fails with
`assert not thread.is_alive()` under `pytest tests/`, and passes 3/3 in isolation.

### Root cause

"Passes alone, fails together" is the signature of test pollution or a concurrency
bug, and `jsonl_store` has a history of both — so the lock implementation was the
natural suspect and was not the cause. `jsonl_store.LOCK_TIMEOUT_SECONDS = 15.0`:
a contended append is *allowed* to wait 15s. The test gave up at 10s. Each append
also pays one `fsync`, measured at 125ms on this disk, so 50 appends is ~6s of pure
platter time per worker before any contention. The deadline could not be met even
with nothing wrong.

### Fix

The join deadline is now derived from the module constant
(`LOCK_TIMEOUT_SECONDS + 50 * 0.5`) instead of hardcoded, so raising the module's
budget can never silently reintroduce a deadline tighter than its own contract. The
liveness assertion stays — a lost append or self-deadlock hangs forever, and the
deadline is what catches that. Only the arbitrary tightness was the bug.

**General lesson:** a timeout is an assertion too, and it can be wrong. Before
blaming a lock, check the timeout against the wait the implementation documents.

---

## B005 — A cache reindex deletes live CA sessions' own bootstrap files

**Status**: fixed
**Severity**: CRITICAL (production data loss, silent)
**Discovered**: 2026-08-20 by ClauLTN, while chasing one "flaky" failure
**Affects**: `surgical_mapper._cache_path_is_preserved`, every live CA session

### Reproduction

Any `generate_project_map` or periodic full refresh, while another agent holds a
prepared session: `_reindex_from_disk` deletes that agent's
`.orchestrator_cache/sessions/<label>-<digest>/CA_SESSION_BOOTSTRAP.md` while it is
still in use. The victim never observes the deletion at the moment it happens, and
the agent that triggered it sees nothing wrong at all.

### Root cause

Name-scoping drift between two modules that never reference each other.
`surgical_mapper.PRESERVED_CACHE_FILES` was written when session artifacts sat flat
in `.orchestrator_cache`, so the preserve check compared the **whole** relative
path. `code_tools._scoped_session_artifact_path` later moved them to
`sessions/<label>-<digest>/`. After that move the exact-match check no longer
recognised them.

`WORKFLOW_STATE.json` survived by accident — the prune loop skips a name whose stem
has no extension left after stripping `.json` — which is why this went unnoticed.

### Fix

Preservation now matches at three layers: the final name, the **basename at any
depth**, and the in-flight `.<name>.<random>.tmp` shape that `_atomic_write_bytes`
writes beside its target (preserving only the final name still deletes a file the
writer holds a path to). Deliberately narrow — a plain `scratch.tmp` stays prunable,
or reconciling external deletes would stop working. Pinned by
`tests/test_scoped_session_artifacts_survive_reindex.py` (10 tests), which also
asserts the fix did **not** widen to cover the A02/A30 write-ledger pact.

**Two test shapes that prove nothing here** (both were tried and discarded):
"prepare -> reindex -> prepare again" passes with the fix reverted because the
second prepare rewrites the lost file; and `generate_project_map` without
`force=True` returns a "skipped" payload when the cache is fresh — a prune that
never ran proves nothing about pruning.

---

## B006 — Git tool payloads truncated, and `expected_staged_hash="auto"` refused

**Status**: fixed
**Severity**: HIGH (git_commit unusable without a manual round-trip)
**Discovered**: 2026-08-18 from live use, reported by Anh
**Affects**: `git_status`, `git_stage`, `git_commit`

### Reproduction

On a mid-refactor tree, all three tools return past the transport's ~63k ceiling and
arrive TRUNCATED, so the response has to be routed through a file to be read. And
`git_commit` with `expected_staged_hash="auto"` is refused as `GIT_STATE_STALE` even
though the index has not changed; the only way through is to copy
`current_staged_hash` out of the error text and resend.

### Root cause

One causal chain, which is why they were fixed together. `records` was 89% of the
payload (~216 chars/record) and the only unbounded part. Worse, `staged_hash` sat
**after** it in the JSON, so a response cut at the limit lost exactly the field
`git_commit` requires — and an unreadable hash is why a caller reaches for `"auto"`
in the first place. `"auto"` then failed because it was compared as a literal string
against a sha256, so it always mismatched. `GIT_STATE_STALE` named the wrong cause
and sent the caller looking for a race that was not there.

### Fix

* `records` capped by `max_records`, and the cap is **reported**
  (`records_truncated`), never silent. `0` = uncapped and is the default, so
  in-process callers that walk `records` are unaffected.
* `counts` / `state_hash` / `staged_hash` computed over **every** record and moved
  **ahead** of `records`. A capped read and a full read are now interchangeable for
  every decision a caller makes.
* `"auto"` is an explicit opt-out of the staleness check only — an empty index is
  still `NOTHING_STAGED`, a genuine mismatch is still refused.
* A missing hash says `EXPECTED_STAGED_HASH_REQUIRED` and carries
  `current_staged_hash`, so the corrected retry needs no second call.
* `git_stage` no longer returns two whole statuses.

Pinned by `tests/test_git_status_payload_and_auto_hash.py` (11 tests); 7 fail on
revert. Its last test asserts a maintenance trap: a new parameter must go in the
**registry schema**, not just the Python signature — `allowed_params` is built from
the schema, so a signature-only parameter is unreachable over HTTP.

---

## B007 — CASE-2008: script and command tools delete files nobody named

**Status**: fixed
**Severity**: CRITICAL (real file loss, and the evidence was truncated away)
**Discovered**: 2026-08-18, reported in `REPORT_DELETEFILE_CASE2008.TXT`
**Affects**: `run_project_test_script` (and its `run_ps1_script` / `run_node_script`
wrappers), and independently `run_project_command`

### Reproduction

An agent ran a script that deleted files it was never asked to delete. The output
that would have shown the damage was truncated (68,978 chars became 4,000, and the
two filenames fell off the end). Separately, `rd /s /q junk` through
`run_project_command` returned `ok` with exit code 0 and really removed the
directory.

### Root cause

**Authorization was never the missing part.** The caller *was* authorised; the
mutation token proves authorization, not intent. A token-gated tool with no
confirmation gate is exactly what CASE-2008 was. And `run_project_command` was a
separate entry point to the same filesystem with the same gap — a safety property
on one tool and not its sibling.

### Fix

Whitelist, never blacklist: delete only what is named. `dry_run=true` previews with
`executed:false`; `confirm_destructive=<destructive_count>` acknowledges, and a
**stale count is refused** (a count, not a boolean, so editing the command
invalidates the acknowledgement). `DESTRUCTIVE_SCRIPT_FOOTGUN` is deliberately not
confirmable — PowerShell discards `-Include` when `-LiteralPath` is given, so there
is no count worth acknowledging, only a command to rewrite.

`run_project_command` runs four interpreters (cmd, PowerShell, `node -e`,
`python -c`); scanning with a single runtime's pattern set was *measured* to miss
`fs.rmSync`, `fs.unlinkSync`, `shutil.rmtree` and `os.remove`, so the gate scans the
union. A base64 body is scanned after decoding and a list `command` as the string it
becomes — encoding and list form are transport details, not privilege levels. A
destructive run never truncates its own output, and a failed backup refuses the run
rather than implying a recoverability that does not exist. The gate is **shared**
between both entry points, and a test asserts each error name is raised from exactly
one place.

Pinned by `tests/test_destructive_script_guard_case2008.py` (19) and
`tests/test_destructive_command_guard_case2008.py` (15).

**Cost check, so the gate is not read as a tax:** 34 real call sites in this repo
were measured against it — 0 flagged.

### Related: the mutation token now carries a two-sentence mini rule

`GET /api/token`, `/api/token/refresh`, and the `MUTATION_TOKEN_REQUIRED` /
`MUTATION_TOKEN_EXPIRED` refusals all return `mini_rule`: whitelist-only deletion,
and fail loud. The token **is** the destructive capability, so the reminder rides on
the handout and on the refusal — a CA's most likely first contact with the
destructive tier. Bounded to exactly four call sites and counted by a test:
`_error_response` splats `**extra`, so a fifth would leak the reminder onto
unrelated errors. Pinned by `tests/test_mutation_token_mini_rule.py` (7 tests).

---

## B008 - A schema `required` list pre-empts the error that carries the fix

**Status**: fixed
**Severity**: MEDIUM (B006's stated recovery was unreachable from every real caller)
**Discovered**: 2026-08-20 by ClauLTN, while verifying B006 live after a restart
**Affects**: `git_commit` over HTTP

### Reproduction

Call `git_commit` over HTTP with `project_path` and `message` but no
`expected_staged_hash`:

```
HTTP 400  MISSING_REQUIRED_FIELD
"Missing required field(s) for 'git_commit': expected_staged_hash"
```

B006 promises `EXPECTED_STAGED_HASH_REQUIRED` carrying `current_staged_hash`, so the
corrected retry needs no second call. That error never arrives. Passing an *empty
string* does produce it -- which is why 11 in-process tests saw nothing wrong.

### Root cause

Two layers refuse the same thing, and the wrong one won. `expected_staged_hash` was
in the registry schema's `required` list, so the generic validator rejected the call
before `git_tools.commit` ran. **The validator has no git state**, so its error
cannot carry `current_staged_hash` -- it does not know what a staged hash is. The
useful refusal was sitting one layer below, unreachable.

This is the same registry-schema trap the last test of
`test_git_status_payload_and_auto_hash.py` already warned about (B006: a
signature-only parameter is unreachable over HTTP), in the opposite direction:
there the schema was too narrow, here it was too strict. Both are the schema and the
Python layer disagreeing about a field.

Why the existing tests missed it: all 11 called `g.safe_call(g.commit, ...)`
in-process, below the validator. **The HTTP surface was never the tested surface.**

### Fix

`expected_staged_hash` removed from `required` in the `git_commit` registry entry; it
stays in `properties` and stays enforced -- by the layer holding the data. The
wrapper's default is `""`, precisely the falsy value `git_tools.commit` refuses, so
omitting the field now lands on `EXPECTED_STAGED_HASH_REQUIRED` with the retry hash
attached. The schema `description` no longer says "Required": it names the error and
documents `"auto"`, because the schema is what a CA actually reads.

`project_path` and `message` stay required -- neither has a domain-layer refusal that
carries a retry value, so for them the generic error is the best available.

Pinned by 3 tests appended to `tests/test_git_status_payload_and_auto_hash.py`
(14 total). 2 fail on revert; the third is a legitimate survivor -- it asserts the
wrapper default and the domain error, which a schema mutation does not touch.

**General lesson:** when two layers can refuse the same call, the one that runs
first must not be the one with less information.

---

## B009 - `query_sqlite` reported lock contention as a permanent failure

**Status**: fixed
**Severity**: LOW (correct answer, unusable classification)
**Discovered**: 2026-08-20 by ClauLTN, closing the last open item of the campaign
**Affects**: `query_sqlite` (`tools_upgrade_p4.py`)

### Reproduction

Query a database another process is writing (a `BEGIN EXCLUSIVE` transaction is
enough). The blanket `except Exception` answered:

```
error_name: QUERY_SQLITE_ERROR
error: OperationalError: database is locked
```

`retryable` defaults to `false`, so a CA reading that response concludes the tool
or the database is broken and stops. In fact the only correct action was to wait
and resend — the same call would have succeeded seconds later.

### Root cause

Two separate things, and the first report of this defect got one of them wrong.

The wrong half, corrected by measurement: the connection was **not** missing a
busy timeout. `sqlite3.connect(..., timeout=10)` installs the busy handler, and
`PRAGMA busy_timeout` reads back `10000`. An early version of this fix added an
explicit `PRAGMA busy_timeout=...` beside it; a mutant that deleted that line
survived all three new tests, which is exactly what a redundant statement looks
like. It was removed rather than kept as decoration.

The real half: contention was indistinguishable from a bad request. Every
failure — a SQL typo, a missing table, a busy writer — collapsed into one
`error_name` with no `retryable` field, and the 10s wait was the sqlite3 default
rather than a deliberate choice for a tool CAs poll by hand.

### Fix

`_SQLITE_BUSY_TIMEOUT_MS = 20000` as a named module constant feeding
`connect(timeout=...)` — one mechanism, one name, and a test can shrink it to
milliseconds instead of blocking for the real wait. `sqlite3.OperationalError` is
caught before the blanket handler: lock/busy text answers
`SQLITE_DATABASE_LOCKED` with `retryable: true`, `retry_after_seconds`,
`busy_timeout_ms` and a `ca_next_action`; every other `OperationalError` stays
`QUERY_SQLITE_ERROR` with `retryable: false`, so a typo never becomes an
infinite retry. The schema `description` says so too, because that string is
what a CA reads before calling.

Pinned by 3 tests in `tests/test_p4_soft_data.py` (20 total), one of which drives
a real writer transaction rather than mocking the exception. Both surviving
mutants (drop the `OperationalError` branch; pin `timeout=10` again) are killed.

**General lesson:** a retry hint is part of the answer, not a nicety — and
mutation testing is what tells you whether a line you just added does anything.
The `pragma` mutant surviving was the fix reviewing itself.

---

## B010 - MCP refused fields the daemon accepts, from its own stale schema

**Status**: fixed
**Severity**: MEDIUM (a valid call is refused; the caller is told its payload is wrong)
**Discovered**: 2026-08-21 by ClauLTN, while verifying the TA-5 self-heal after a restart
**Affects**: `dispatch_tool_call(transport="mcp")` (`orchestrator_server.py`) — every tool reached over MCP

### Reproduction

Same tool, same field, two transports, on generation `4c69c7c10f4b`:

```
HTTP  POST /api/call/git_status {"project_path": ..., "max_records": 0}
    -> ok: true

MCP   call_tool("git_status", {"project_path": ..., "max_records": 0})
    -> ok: false   UNSUPPORTED_FIELD
       accepted_fields: [include_ignored, project_path]
```

`max_records` had been in the registry since commit `ba88645` (21:18) and
`/api/tools/git_status/schema` listed it. The MCP client processes had started at
09:30, 17:56 and 18:37 — all before that commit.

The decisive observation was in a call the gate *did* let through: `include_ignored`
predates `ba88645`, and its MCP response came back carrying `records_total` and
`records_truncated`, fields only the new code emits. So the handler was the new
one all along. Only the gate was old.

### Root cause

`dispatch_tool_call(transport="mcp")` screened the payload before proxying,
using `_schema_for_tool()` — which reads `ct.TOOL_SCHEMAS` **in whichever process
runs the gate**. For an MCP call that process is the stdio bridge: a separate,
long-lived process that imported its registry once at launch. Every parameter the
daemon has gained since then is invisible to it.

This has always been the code's shape, but it was previously unreachable in a way
a caller would notice, because a generation mismatch made the bridge fail closed
with `MCP_STALE_RELOAD_REQUIRED`. TA-5 fixed that — correctly, a stale stamp
should not brick a session — and in doing so made the bridge proxy happily to a
daemon whose schema it no longer shares. The generation layer healed; the registry
layer did not, and the gap became visible.

Two symptoms, one cause: an unknown field answered `UNSUPPORTED_FIELD` with the
bridge's outdated `accepted_fields`, and a tool registered after the bridge
started had `_schema_for_tool` return `{}`, so every one of its fields looked
unknown.

The screen justified itself in a comment as sharing the duplicate-retry ledger
with the HTTP dispatcher, so a bad payload gets one correction instead of an
infinite loop. That was already false: the proxy path runs `_bridge_dedup`, which
applies `_duplicate_retry_guard` to every `_DETERMINISTIC_TOOLBODY_ERRORS`
verdict, `UNSUPPORTED_FIELD` included.

### Fix

The local screen is deleted. Every non-T4 MCP call proxies to the daemon and the
daemon's registry decides. A genuinely unknown field still comes back as
`UNSUPPORTED_FIELD` — from the daemon, with the daemon's `accepted_fields`, which
is the only version a CA can act on — and an identical resend is still blocked as
`DUPLICATE_RETRY_BLOCKED`.

Pinned by 4 tests in `tests/test_mcp_gate_uses_live_schema_b010.py`: a stale
schema missing one field, a tool missing entirely, a field no layer knows (must
still fail loud), and the ledger guarantee the old comment claimed the screen
provided. Reverting the fix kills 4 tests; deleting `_bridge_dedup` from the
error branch kills the ledger test.

Two existing tests were re-pointed, both because they pinned the *decision site*
rather than the outcome:

* `test_mcp_duplicate_bridge_r57.py` asserted `proxy_calls == []` for an invalid
  payload. Its real guarantee — 400 then 409 — is kept verbatim.
* `test_duplicate_retry_guard.py` had **no HTTP mock at all**. Before the fix an
  invalid payload never left the process, so it passed by accident; afterwards it
  POSTed to whatever `[A]` was on 8083 and asserted against *that* process's
  ledger, which is why it passed alone and failed in a group. It now forwards the
  proxy request into the real dispatcher. A canned error envelope was tried first
  and failed, correctly: the HTTP validator records its duplicate under a **tuple**
  key `(scope, sha256, error)` while `_bridge_dedup` uses a **string** key, so a
  canned reply takes a path that cannot see what the first leg wrote. Both legs
  reach the same validator in production; only a loopback reproduces that.

**General lesson:** two processes that answer for the same tool must not each
keep their own copy of the contract. And when a fix makes a previously-fatal
mismatch survivable, check what the mismatch was hiding — TA-5 did not cause this
bug, it made it observable.

---

## B011 — A full index never dropped the row of a deleted file, so `stale_files` could not converge

**Status**: fixed
**Severity**: HIGH (the graph kept answering from files that no longer exist)
**Discovered**: 2026-08-21, reported as "`stale_files: 24` right after `cbm_index(full)` said `completed` with `files_unchanged: 2` — the two numbers contradict each other"

### Reproduction

```
cbm_index(project_path=..., scope="full")   -> completed, files_unchanged: 2
cbm_status(project_path=...)                -> stale_files: 24, freshness: "fresh"
```
Repeat the index any number of times; the count never moves.

### Root cause

Two counters over **two different populations**:

* `store.stale_files` / `status._stale_count` iterate the **rows of the `files`
  table** and call a row stale when its file is missing on disk.
* `index_project` iterates the **on-disk candidate walk**. A file that left the
  tree simply stops appearing — nothing on that path ever deleted its row.

`_delete_file_entries` already existed and already did the right cascade; it was
only wired into `incremental_index`. Three consequences beyond the mismatched
number, and the number was the least of them:

1. Nodes and edges of deleted files stayed in the graph and kept surfacing in
   `dead_code` / architecture answers.
2. `query._auto_reindex_if_stale` gates on that count, so a full reindex could
   never clear it — it re-fired forever without converging.
3. `freshness` answered `"fresh"` for a whole hour on age alone while
   `stale_files > 0` — one field contradicting another in the same payload.

The contract had in fact been written down already, in
`tests/test_graph_stale_count_agreement.py::test_a_deleted_file_is_stale`
("*or a deletion would never prompt the reindex that drops its nodes*"). The
*drops its nodes* half was never implemented.

### Fix

`_prune_missing_files(conn, root)` in `orchestrator/graph/indexer.py`, called
**before** the dependency passes so no edge is built onto a node about to be
deleted. Missing-on-disk is the only criterion, deliberately: that is
scope-independent, so a `core` index never prunes the rows a previous `full`
index wrote for files outside `orchestrator/`. An `OSError` while testing
existence is treated as "present" — a transient stat failure must not delete a
file's graph. The count is reported as `files_pruned`, so the deletion is never
silent. `status.py` now derives `freshness` from a non-zero count instead of
letting the age branch overrule it.

**Pinned by** `tests/test_graph_index_prunes_deleted_files.py` (6 tests).

---

## B012 — `validate_file` answered `ok:true` for any `lang` it did not recognise

**Status**: fixed
**Severity**: HIGH (a validator that passes what it never read)
**Discovered**: 2026-08-21, reported as "`lang: json` returns `ok: true` for a syntactically broken JSON file"

### The report's diagnosis was wrong; the defect was worse

Measured first: `lang="json"` on broken JSON already returned `ok:false` with a
real `JSONDecodeError`, and `lang="js"` really did run `node --check`. Those
branches worked.

The door was the **terminal `else`**: any `lang` the dispatcher did not match
answered `ok:true, lang:"none"` — no check attempted, no mention of that. So a
broken file passed under `JSON`, `Json`, `jsonc`, `javascript`, `node`, `py`.
Three of those name a language the tool **does** implement, under a different
spelling. And the registered schema documented 4 languages (`python | js | ts |
auto`) while the code branched on 9, so a caller could not learn the vocabulary
except by reading source.

### Fix

In `code_tools.py`: `_VALIDATE_LANGS` (the 9 the code really implements) plus
`_VALIDATE_LANG_ALIASES`, normalisation before dispatch, and an
`UNSUPPORTED_LANG` refusal carrying `supported_langs` for anything else. A
`checked` boolean now rides on every answer, so `ok:true` is never ambiguous
about whether anything ran — `auto` on an unknown extension still passes
(`write_fix` validates every write and must not fail a `.txt`), but with
`checked:false`. The json branch additionally rejects RFC-8259 violations that
Python's `json` accepts by default: `NaN`/`Infinity` literals, and duplicate
keys (which parsers silently resolve last-wins, discarding data).

**Pinned by** `tests/test_validate_file_unknown_lang_fails_loud.py` (13 tests).

---

## B013 — JS dependency graph empty: `require()` never extracted, and ESM import keys were path-shaped

**Status**: fixed
**Severity**: HIGH (`module_deps` / `imports` / `imported_by` / cycles all empty on any JS repo)
**Discovered**: 2026-08-21, reported as "`dependency_edges: 0, modules_indexed: 65, dynamic_ref_edges: 0` despite `callable_names: 426` — CommonJS `require()` is not extracted"

### Two independent gaps, not one

The report named `require()`. Measurement found a second gap that would have
kept the symptom alive after fixing the first:

1. **`require()`** is a `call_expression`, so it never reached `_emit_import` —
   CommonJS produced no import node at all, only an `ext:require` call edge.
2. **ESM did emit an import node, but path-shaped**: `./lib/util.js.helper`.
   The indexer keys modules with `_dotted_from_path('lib/util.js')` →
   `lib.util`, and matches by dotted prefixes longest-first. The prefixes of a
   path-shaped name (`/lib/util.js.helper`, `/lib/util.js`, `/lib/util`) start
   with `/` and can never equal `lib.util`.

So fixing `require()` alone would have left every ESM project at zero edges and
the reported number unchanged.

### Fix

`orchestrator/graph/extractors/javascript.py`. `_module_key()` resolves a
relative specifier against the importing file's directory, strips a supported
extension and a trailing `/index`, and returns dots — at **extraction** time,
which is the only place it can be done, because the indexer no longer knows the
importing file's directory when it walks import nodes. A bare package specifier
(`fs`, `express`) is deliberately left unresolved rather than collided with a
same-named local file; a test pins that restraint. `_require_specifier` /
`_emit_require` handle the three real shapes — `const x = require('m')`,
`const {a, b: c} = require('m')`, and a bare `require('m');` side effect — with
`_consumed_requires` so a bound require is not also recorded as a call.

Measured on the reported repo: `dependency_edges` **0 → 46** (nodes 1864, edges
4504, `unresolved_imports: 38`, correctly the bare package specifiers).

**Pinned by** `tests/test_js_dependency_edges.py` (11 tests).

---

## B014 — `cbm_dead_code`'s `file_filter` was accepted and discarded

**Status**: fixed
**Severity**: HIGH (made the tool unusable on any repo with a large data directory)
**Discovered**: 2026-08-21, reported as "`file_filter: '5.backend/**/*.js'` returns exactly the same 207 items as no filter"

### Reproduction (the decisive one)

A **bogus** glob returned the full result set too:

```
no filter                        -> count 207
file_filter=5.backend/**/*.js    -> count 207
file_filter=no/such/dir/**/*.js  -> count 207   <-- matches zero files
```

Identical output for a filter that matches nothing is the proof the argument is
inert rather than merely broad.

### Root cause — two defects in one function

1. `_cbm_dead_code_dispatch` resolved `files` from the glob and then, for any
   non-Python repo, early-returned into `_cbm_dead_code_ast_p0`, which had no
   parameter to receive it. `query.dead_code_candidates` had no such parameter
   either. The filter was accepted, silently dropped, and never reported as
   dropped.
2. Even on the Python path the glob was hard-filtered with
   `path.endswith(".py")`, so a `*.js` pattern matched zero. The schema's
   "matching files are passed directly to Vulture/Ruff" was true only for Python
   paths.

The user's underlying need is the point: with `scope` limited to `full|core`
there was no way to exclude a large data directory.

### Fix

`rel_files` threaded from `_cbm_dead_code_dispatch` → `_cbm_dead_code_ast_p0` →
`query.dead_code_candidates`. An empty **set** means "the filter matched
nothing" and correctly yields nothing; `None` means "no filter". The `.py`
hard-filter moved to after the language check, where it belongs (the Python
engines only). The response echoes `file_filter`, `file_filter_applied` and
`files_matched`, so a glob that matched nothing reads as `files_matched: 0`
instead of looking like a full scan. Schema now points at `file_filter` for
restricting to a subtree.

Measured on the reported repo: **208** (no filter) / **131** with
`files_matched: 28` (`5.backend/**/*.js`) / **0** with `files_matched: 0`
(bogus glob).

**Pinned by** `tests/test_cbm_dead_code_file_filter.py` (9 tests).

---

## B015 — `cbm_dead_code` described itself as Ruff-powered while running a different engine

**Status**: fixed
**Severity**: MEDIUM (a correct fallback, presented as something it is not)
**Discovered**: 2026-08-21, reported alongside B014

A non-Python repo silently got `engine: "ast-p0-v1"` — the graph AST engine, a
different analysis with different semantics — under a schema advertising
"Ruff-powered ... Vulture supplements". Falling back is legitimate; not saying
so is the defect, and it also hid the fact that on a JS repo the other
categories (`bug_risk`, `perf`, `code_smell`, ...) are Ruff-only and therefore
come back empty by construction, not because the repo is clean.

**Fix**: every answer now carries `engine`, `engine_reason` and
`language_detected`. `_detect_majority_language` was returning only
`"python"`/`"other"`; adequate while it merely gated the engine choice, but as a
reported field it discarded what it had just counted — it now answers
`"javascript"` when it counted JS/TS files. The registered schema states which
engine each repo shape gets and that the Ruff-only categories will be empty on a
JS/TS repo.

**Pinned by** `tests/test_cbm_dead_code_file_filter.py::test_the_engine_name_says_what_actually_ran`.

---

## B016 — B013's fix did not ship: an extractor change without an `EXTRACTOR_VERSION` bump

**Status**: fixed
**Severity**: HIGH (the JS dependency fix was inert on every already-indexed repo)
**Discovered**: 2026-08-21, while live-verifying B013 after the restart that made the new code running

### Reproduction

After the restart to generation `c9b9ccd82333`, with B013 committed and live:

```
cbm_index(project_path=<the reported JS repo>, scope="full")
  -> status: completed, files_indexed: 0, files_unchanged: 41, dependency_edges: 0
cbm_template_query(template="module_deps")  -> 0 rows
```

The fix was in the process. The graph still held the old shape.

### Root cause

`store.stale_files` re-extracts a file when its content changed, when it was
never indexed, **or** when its row's `extractor_version` differs from
`config.EXTRACTOR_VERSION`. B013 changed the *extractor*, not the source: same
bytes, same hashes, so every row matched and nothing was re-extracted. The
repo kept its path-shaped import keys and its unrecorded `require()` calls.

The mechanism was already there, and `store.stale_files`'s own docstring had
already recorded this exact failure once: "*Shipping the W5 extractor left 241
of 250 [A] files frozen at the pre-`name_literal` shape through a full reindex,
because every hash still matched.*" The bump is the step that gets skipped,
twice now — which is why it is now pinned by a test rather than by a comment.

### Fix

`EXTRACTOR_VERSION` bumped `ast-p0-v8-all-is-not-a-reference` →
`ast-p0-v9-js-imports-are-modules`, with the reason written next to it.

**Pinned by** `tests/test_extractor_version_pins_js_import_shape.py` (5 tests).
Four of them pin the mechanism — a row records the version the code reports, an
unchanged file *is* re-indexed when the version moves, the JS import shape
travels with the version, and dependency edges come back after a bump. The
fifth is narrower on purpose: it refuses the one exact pre-B013 string, so any
legitimate future bump passes while *this* omission cannot recur.

### The general rule

**An extractor change and a version bump are one change.** Anything that alters
what extraction *produces* from unchanged bytes — a new node kind, a new edge
kind, a different key shape — is invisible to content hashing, so the fix ships
only for repos indexed after it. Test it against a repo that was **already
indexed**, not only a fresh temp one; a fresh fixture passes either way, which
is exactly why B013's own 11 tests were all green while the live repo was not.

---

---

## B017 — a native CREATE inside [A] was refused while a native EDIT was allowed

**Status**: fixed
**Severity**: MEDIUM (the audit trail for any NEW file inside [A] could not be written)
**Discovered**: 2026-08-21, while logging the audit trail for the live-answer doctor

### Reproduction

Two new files were created inside [A]'s own tree and logged the standard way.
Same tool, same tree, same session — only the `action_type` differed:

```
log_native_event(action_type="create_file",         target=<[A]>\scripts\live_answer_doctor.py)
  -> ok: false, audit_status: "logged_and_rejected"
     task_class: "unknown", native_policy: "forbid"
     host_gate: "deny_native_forbid", ca_next_action: "stop_native_use_a_tool"

log_native_event(action_type="write_existing_file", target=<[A]>\MAINTENANCE_DOCTOR.md)
  -> ok: true,  audit_status: "logged"
```

So editing a file inside [A] was authorised and creating one was refused — a
distinction no rule makes. The event is still *recorded* (`logged_and_rejected`),
but it is recorded as a policy violation, which is exactly backwards for the one
path the standing rule authorises.

### Root cause

`classify_action` resolves `action_type` through `_ACTION_TO_CLASS`. `create_file`
has no row there, so `task_class` fell through to `"unknown"`, whose
`native_policy` is `forbid`. The class it should reach — `create_project_file` —
already exists and is already reachable from the *tool* side
(`_TOOL_TO_CLASS["write_new_file"]`). Only the action word was missing.

This is **F021's defect a second time, in the same table**. F021's own comment in
that file reads: *"the log_native_event schema documents these action_type values,
but they had no row here and fell through to task_class='unknown' -> a
native_policy=forbid HOST_GATE_DENY on perfectly ordinary actions."* F021 added
rows for the seven verbs the schema description happens to list
(`read | write | execute | validate | cache | backup | workflow`) plus a few
synonyms — and `create_file` is not among them.

It is also the same shape as B012: the vocabulary a caller must use is documented
in one place and enforced in another, and the two disagree. `create_file` reads
like the obvious name for the thing being logged, exactly as `JSON` read like the
obvious spelling of `json`.

### Fix

`classify_action` now strips a `_file` / `_files` suffix and retries **through the
same table**:

```python
if not task_class:
    stem = re.sub(r"_files?$", "", action)
    if stem != action:
        task_class = _ACTION_TO_CLASS.get(stem)
```

Resolving only through the table is what bounds it: an unknown verb stays
unknown (`frobnicate_file` is still `forbid`), and a suffix can never mint a
privileged class, because `upgrade_orchestrator` and `a_unreachable_mid_task` have
no rows in `_ACTION_TO_CLASS` at all — they are derived from the target path.
That restraint is F021's, kept deliberately.

**Pinned by** `tests/test_policy_action_vocabulary_b017.py` (8 tests). Five pin
the fix — `create_file` is not `unknown`, create inside [A] carries the same
policy as edit, every `<verb>_file`/`<verb>_files` spelling matches its bare verb,
case and surrounding whitespace do not matter, and F021's bare verbs still work.
Three bound it, and they are the half that matters: an unknown word is still
`unknown`, a suffix cannot mint `upgrade_orchestrator` on a path outside [A], and
`delete_file` carries `delete`'s policy exactly — the suffix must never be a route
around the destructive gate.

### The general rule

**A vocabulary enforced in a lookup table must be generated from that table, or
the table must accept every spelling the docs and the callers use.** Three defects
now share this shape — B012 (`lang`), F021 (`action_type`), B017 (`action_type`
again) — and in each one the failing spelling named something the system already
implemented. When a schema description lists valid values by hand, that list is a
copy, and copies drift. The tell is always the same: a refusal, or a silent pass,
for a word that obviously means the right thing.

---


---

## B018 — `search_code` omitted `ok` on the success path

**Status**: fixed
**Severity**: MEDIUM (a successful search reads as a failure to any ordinary caller guard)
**Discovered**: 2026-08-21, smoke-testing the core tools after the stabilisation restart

### Reproduction

Measured against the live daemon at generation `7d870da7e301`:

```
search_code(query="_ACTION_TO_CLASS")     -> count=2  "ok" NOT IN the response
search_code(query="zzz_no_such_symbol")   -> count=0  "ok" NOT IN the response
search_code(query="")                     -> ok=False error_name=MISSING_REQUIRED_FIELD
```

So `search_code` answered `ok:False` when it failed and answered *nothing* when it
succeeded. Surveyed the same way, every other tool answers `ok` on both paths:
`read_file_range`, `git_status`, `validate_file`, `list_sessions`,
`cbm_graph_status`, `regex_search` — `search_code` was the only one missing it.

The consequence is that the ordinary caller guard

```python
if not resp.get("ok"):
    ...treat as failure...
```

reads a **successful** search with real hits as a failure, because a missing key is
falsy in exactly the same way `False` is.

### Root cause

`orchestrator/tools/search.py` builds two different shapes. Every error branch
returns `{"ok": False, "error": ..., "error_name": ...}`; the success branch built
`{"results", "count", "mode", "max_results", "max_context_lines", "bounded",
"snapshot_id"}` and never included `ok`. Nothing removed the key — it was never
added.

This is the **safe-looking direction** of the B012/F021/B017 family: the contract a
caller is told to rely on is enforced in one place and not honoured in another, and
the failing read names something the system did in fact do. It is the more dangerous
half of that family, because nothing errors, no gate denies, and the results are
right there in the payload — the caller simply throws them away and goes looking for
a bug that does not exist. That is precisely how a previous subagent came to declare
`cbm_cycle_point` (a real tool) non-existent, which is the same wound A03 was written
for.

### Fix

One key on the success branch, with the semantics written down so it is not later
overloaded:

```python
response = {
    "ok": True,
    "results": results,
    ...
}
```

A search that **ran** is `ok:True`. "Found nothing" is carried by `count` and
`stale_index` (A03/A03b), never by `ok` — otherwise a clean zero-hit and a broken
search become indistinguishable again, one layer down.

**Pinned by** `tests/test_search_ok_field_b018.py` (4 tests). Two pin the fix — a hit
says `ok:True`, and a clean zero-hit says `ok:True` too — one asserts the rest of the
success payload is untouched, and one bounds it: an empty query must still answer
`ok:False` with a named error, so adding the key never becomes "always say ok".
Mutation-verified: removing the line failed exactly 2. Adjacent suites clean —
77 passed / 1 xfailed across the 13 search suites plus `test_r101_fields_unknown_a23p2`.

### The general rule

**A field every failure path sets is part of the contract, and the success path owes
it too.** An absent key is not a neutral silence: in Python it is falsy, so omitting
`ok` on success is indistinguishable from setting it False. Two defects now share
this exact shape — B015 (an answer that did not name its engine) and B018 — and both
were invisible to a green suite because no test asserted on the *presence* of a
field, only on its value when present.


---

## B019 - `regex_search` advised narrowing by a field it refused

**Status**: fixed
**Severity**: MEDIUM (the tool's only actionable escape from a real ceiling was unreachable through the tool)
**Discovered**: 2026-08-21, while surveying 10 more tools for the B018 shape

### Reproduction

Measured on the live daemon at generation `528d657f9449`, against [A]'s own tree:

```
regex_search(project_path=<[A]>, pattern="def classify_action")
  -> ok:false  REGEX_INPUT_BUDGET_EXCEEDED  "regex input exceeds 16777216 bytes"
     ca_next_action: "narrow_path_or_reduce_regex_input"
     payload_example: {"project_path": "D:/path/to/project"}     <- the failing call

regex_search(..., path=<[A]>\orchestrator\core)  -> ok:false UNSUPPORTED_FIELD
search_code(..., mode="regex", path=<same>)       -> ok:true  count=1
```

So the tool advised narrowing by path, refused `path`, and echoed the very call
that had just failed as its example. `regex_search`'s own docstring calls it a
"convenience alias of `search_code(mode='regex')`" -- and the alias was strictly
weaker than its target on the one axis its own error names.

The 16 MB ceiling is NOT the bug. A named limit refusing loudly is correct; [A]'s
own tree really does exceed it. The bug is that the refusal was unactionable
through this tool: every route out of it was either a field this tool rejected or
an example identical to the failure.

### Root cause

THREE halves, and the third was found only after a restart proved the first two
were invisible over HTTP.

`tools_v15.regex_search` never had a `path` parameter, so it never forwarded one.

But adding it changed nothing live. **The dispatcher validates against the
REGISTRY, not the signature** -- it says so itself at
`orchestrator_server.py:13422`, *"S4: registry properties are the public
contract; signatures are internal"* -- so `allowed_params = set(_registry_props)`
still had no `path`, and a restart onto the fixed source answered
`UNSUPPORTED_FIELD` exactly as before (measured at gen `31156af84e36`). The first
nine tests all called the Python function directly, so all nine passed while the
tool stayed as broken as the day it was reported. `orchestrator/core/registry_data.py`
had already paid for this once, for `regex_deadline_seconds` in this same tool
(comment `B-01`, directly above the line that fixes this) -- and the second cut
of the docs still asserted the schema came from the signature, which is how a
wrong sentence outlives the bug it describes.

And in `orchestrator/tools/search.py`, the `RegexSearchBudgetExceeded` case fell
into a shared bare handler that could carry only `ca_next_action` -- no
`corrected_payload`, no `retry_sequence`, no `payload_example` of its own -- so
the generic envelope supplied one, and the only thing it had to work with was the
original call. The sibling `RegexSearchTimeout` case had had a dedicated builder
since A40 for exactly this reason: *two paths, one error_name, divergent
contracts*. BUDGET was the path that never got one.

### Fix

`path` accepted and forwarded in `regex_search`, and a `_regex_budget_payload`
builder modelled on `_regex_timeout_payload`, with the BUDGET branch routed
through it. The guidance now names the two fields that really shrink the input
(`path`, `file_pattern`) and hands back two concrete payloads.

One deliberate restraint: the builder takes the worker's own message rather than
restating a ceiling. `REGEX_INPUT_BUDGET_EXCEEDED` is raised for **two** limits --
`MAX_RECORDS` and `MAX_INPUT_BYTES` (`search.py:946,953`) -- so a builder that
hardcoded one would name the wrong ceiling for the other cause. That is B015's
defect (an answer that does not describe what actually happened) one layer down.

**Pinned by** `tests/test_regex_search_path_b019.py` (9 tests). Two pin the
signature and real forwarding, four pin the guidance, two bound it (the ceiling
still fires, the worker still enforces bytes), and one pins **routing**.

That last one exists because of a mistake worth recording: the first cut of this
file tested only the builder, and a mutant that unrouted the branch (`if False:`)
passed all 8 tests. A builder nothing reaches is decoration -- the exact failure
mode B011-B016 were written about, reproduced inside their own campaign's tests.
The routing test fails the mutant by patching `validate_next_record_budget`, the
function that really raises this error, rather than a name invented for the mock.

---

## B020 - a room token's TTL was truncated to the current second

**Status**: fixed
**Severity**: MEDIUM (a council room token could expire almost immediately after issue)
**Discovered**: 2026-08-21, from a single full-suite failure that passed 10/10 in isolation

### Reproduction

Deterministic, with the clock pinned:

```
register at t=100.95, ttl_sec=1   -> expires_at = 101
validate  at t=101.01 (60 ms later) -> PermissionError: ROOM_TOKEN_INVALID
```

A one-second token had an effective life of **0.05s**.

### Root cause

`_room_register` computed `expiry = int(time.time()) + ttl_sec` while
`_room_validate_token` compares `expiry <= int(time.time())`. Flooring the start
of the window while the end is also floored charges the caller for the fraction
of the second already elapsed, so the real lifetime was
`ttl_sec - (time.time() % 1)` -- anywhere from the full `ttl_sec` down to nearly
zero, decided by where in the wall-clock second the register happened to land.

Reachable from production: `POST /api/council/room/register` passes any caller's
`ttl_sec` straight through.

### How it presented, and why that matters

`test_council_rooms.py::test_register_meta_token_and_expiry` failed once in the
full suite and passed **10/10 in isolation**. That reads exactly like the
test-ordering pollution this campaign has already diagnosed twice, and the cheap
move was to file it as flake. It was not flake: the suite is slower, so the
register call lands late in its second more often. The probability was
environmental; the defect was not.

**A timing-dependent failure is a real defect until measured otherwise.** The
measurement that settles it is cheap -- pin the clock and see if the failure is
deterministic. Isolation passing is not that measurement.

### Fix

`expiry = math.ceil(time.time()) + max(1, int(ttl_sec))`. Ceiling makes the
advertised `expires_at` always at least `ttl_sec` away; it can overshoot by under
a second, which is the safe direction for an authorization window's own grace.

**Pinned by** `tests/test_room_token_ttl_b020.py` (4 tests, 8 subtests): a
1-second token survives 60 ms, the full TTL holds at every sub-second phase, the
advertised `expires_at` is never short -- and, bounding it, a token still
expires, so validity was not bought by making tokens immortal.
Mutation-verified: reverting to `int()` fails 6.

## B021 - two payloads that described their own contents wrongly

**Status**: fixed
**Severity**: HIGH (`git_status` CRASHED on any rename whose old path began with `u`, `1`, `2` or `!`, taking `git_stage` and `git_commit` down with it -- found while fixing the reported symptom, never reported)
**Discovered**: 2026-08-21 by Anh in live use, at gen `cac7187da50d`

### Reproduction

```
git_status(project_path=<repo>)
  -> 126,770 bytes, TRUNCATED by the transport, 270 records
     and EVERY record carried its own content twice:
       {"record_type":"1",
        "raw":"1 .M N... 100644 100644 100644 592f3d11.. 592f3d11.. PROJECT_CONTEXT.md",
        "xy":".M","sub":"N...","path":"PROJECT_CONTEXT.md"}

cbm_template_query(project_path=<repo>, template="module_deps", limit=12)
  -> count=12  truncated=True  total_count=0
```

Anh graded the second one "lỗi nhỏ, không cản việc". It is -- and it is also the
shape of a payload a paginating caller cannot act on: `total_count` said "nothing
here", `truncated` said "keep asking", and one of them had to be lying.

### Root cause, issue 1 (`git_status`)

`_porcelain` stored the porcelain line as `raw` next to `record_type`/`xy`/`sub`/
`path`/`orig_path` -- **the fields parsed out of it**. Nothing in `raw` was new
information and it was ~71% of the record's bytes, on the one *unbounded* part of
the payload. The B-series had already capped the record count and reported the cap
(`records_total` / `records_truncated`); the duplication survived that fix because
capping made the payload smaller without making any record smaller, and nobody had
counted a record's bytes against the information in it.

**The constraint that shaped the fix:** `state_hash` is a sha256 over
`{'branch','records'}` and callers hold it *between* calls as an optimistic lock --
`git_stage` refuses with `GIT_STATE_STALE`, `git_stale_check` reports
`state_hash_mismatch`. Hashing the tidied records would shift every hash at once,
so every lock a caller was holding would read as a concurrent modification that
never happened. `raw` is therefore dropped from the **response** while the **hash
input keeps it**. Tidying a payload must not be a protocol change.

### Two further defects at the same site, found by probing, never reported

In `-z` mode a rename/copy record (type `2`) is **two NUL-separated fields** -- the
record, then the original path -- because a path cannot carry the tab that
separates them in the non-`-z` format. `git-status(1)` documents this. The splitter
treated the second field as a record of its own, so:

1. **every rename produced a phantom record** whose `record_type` was the first
   character of the old path and whose only other content was `raw`
   (`{'record_type': 'o', 'raw': 'old.txt'}` from `old.txt`). It was counted in
   `counts['changed']`, and `orig_path` was always `''` because its value had been
   split off into the phantom.
2. **`git_status` RAISED IndexError** when the old path began with `u`, `1`, `2` or
   `!`: the phantom was then parsed as an unmerged / ordinary / ignored record and
   `rec.split(' ',10)[10]` blew up. Measured: `git mv util.py helper.py` made
   `git_status` raise. `git_stage` and `git_commit` both call `status()` first, so
   all three tools went down on a rename of any file named `util.py`, `1.txt`,
   `2.txt`...

The two fields are now rejoined with a **tab** -- exactly the shape the non-`-z`
format uses, and the shape the type-2 parser already expected. `_parse_records`
also keeps an unrecognised record's own line under `unparsed` instead of raising:
a future porcelain change must not be able to take these three tools offline.

### Root cause, issue 2 (`module_deps`)

`total_count` and `truncated` were **set independently**, and `module_deps` set
neither: it inherited the initializer `0` and the shared tail's
`if len(res) >= limit: truncated = True`. `total_count` is now the pre-cap
population per branch -- a `COUNT` over the *same predicate* as that branch's
LIMIT-ed `SELECT`, `DISTINCT` included -- and `truncated` is **derived** from it,
offset-aware: `truncated iff offset + count < total_count`. One number, not two
fields that can disagree.

`impact_scope` is the one exemption: it answers a single synthetic row whose
`truncated` reports the **trace-depth cap**, not a row count. Deriving it from
1-of-1 would report a blast radius that dropped files at
`GRAPH_CONTEXT_MAX_FILES` as complete.

### The docstring was part of the bug

`template_query`'s docstring read *"total_count is the true population for
paginated templates (orphan_symbols); 0 otherwise."* A reader checking whether
`total_count=0` was a defect would have concluded it was documented behaviour and
closed the report. Documentation that describes a bug as a feature is part of the
bug, and `test_the_docstring_no_longer_promises_zero` now pins that.

### What mutation testing caught that the first cut of the tests did not

The first 13 tests were green and **three mutants survived them**, each because a
fixture could not tell the mutant from the fix:

- `total_count` falling back to `0` instead of `len(res)` -- nothing asserted on
  `impact_scope`, the only branch that reaches the fallback, so `count=1
  total_count=0` (*the exact reported contradiction*) would have shipped in the
  template the fix deliberately exempted. The exemption is from deriving
  `truncated`; it was never an exemption from describing itself correctly.
- counting **raw edges** instead of the `DISTINCT` pairs -- every fixture had one
  import per file, where the two counts happen to agree. A file that imports the
  same module twice (`from util import helper` + `import util`) gives 2 edges for
  1 pair, so the mutant reports a total the LIMIT-ed select can never reach and
  `truncated` stays true on a **complete** read: the reported bug pointing the
  other way, a caller looping forever on the same last page.
- counting `_all_eps` instead of `_prod_eps` in `entry_points` -- that view drops
  test runners (CAN-053), so counting the pre-split list reports a total no page
  of the template will ever serve.

3 tests were added for exactly those three, plus one for the capped blast radius.
**A fixture too small to distinguish the mutant is a test that measures nothing**,
and the only way to find that out is to run the mutant.

### Pinned by

`tests/test_payload_says_each_thing_once_b021.py` (22 tests, 16 for the reported
pair and 6 for the offset follow-up below). git half: no record
carries `raw`, every parsed field survives for all five porcelain types
(`1 2 u ? !`), the payload is measurably smaller (<120 bytes/record), `state_hash`
is unchanged *against an independently recomputed literal*, and the lock still
detects drift end-to-end through `stale_check`. module_deps half: the real
population, the `truncated`/`total_count` invariant at limits 1/4/12/13/50, an
uncapped read reporting `total_count == count` with `truncated False`,
`orphan_symbols` keeping the total it already computed correctly, the **zero case**
where `total_count=0` is the truth, the `DISTINCT` predicate, the `entry_points`
production set, the depth-capped blast radius, and the docstring.
Mutation-verified: 6 mutants across the two files, each failing exactly the tests
written for it (`raw` back in the response -> 2; the `-z` rejoin removed -> 1;
hashing the tidied records -> 1; `total_count` falling back to 0 -> 1;
`module_deps` not counting -> 1; `truncated` back to `len(res) >= limit` -> 1).

### Follow-up: an offset-aware derivation whose offset cannot leave 0

Found by probing this fix, not reported. B021 derived `truncated` as
`offset + count < total_count` -- and `offset` **could not be passed**.
`cbm_template_query` had no `offset` in its signature and none in its **registered
schema**, and the server gates unknown fields on the registered schema
(`orchestrator_server.py:13482`), so a signature-only fix would still have answered
`UNSUPPORTED_FIELD`. Measured live at gen `94fa01936e89`:

```
cbm_template_query(template="module_deps", limit=500, offset=500)
  -> "Unsupported field(s) for 'cbm_template_query': offset"
     from the same daemon that reported total_count=619
```

So the honest total the fix had just started reporting named 619 rows while rows
501-619 were unreachable. Internally, only the `orphan_symbols` branch used
`offset` at all; `callers`, `callees`, `imports`, `imported_by`, `module_deps` and
`entry_points` ignored it, so even after the field arrived they would have answered
**page 1 forever** while `truncated` -- derived from an offset the SQL never saw --
said "keep asking". `tests/test_graph_ext_edges.py::test_offset_pagination` had
pinned offset paging since E3, but at the **function** level, which is exactly why
the tool-level gap stayed invisible: a test that calls past the layer holding the
defect cannot see it.

Fixed at all three layers: `LIMIT ? OFFSET ?` on the five SQL branches and
`_prod_eps[offset:offset+limit]` on the Python one, `offset: int = 0` on the async
wrapper and forwarded, and an `offset` property in the registered schema (whose
`limit` text also said "clamped to 200" where the code clamps at 500).

`max(0, int(offset or 0))` is load-bearing across the branch kinds: SQLite ignores
a negative `OFFSET` and returns page 1, but `_prod_eps[-3:-1]` counts from the
**end** -- without the clamp a SQL branch and a Python branch disagree about which
rows page 1 holds.

6 tests added (16 -> **22**). Mutation-verified **10/10 killed** -- and four of them
survived the first cut for the reason this entry already names: `_graph_repo`
yields `entry_points` 0, `imports` 0, `callees` 1, so a mutant that ignored
`offset` on those branches was indistinguishable. A second fixture
(`_entrypoint_repo`, measured 4/6/3 rows) plus a test that fails loudly if it stops
producing them is what killed M13/M16/M17. Two honest limits are recorded in the
tests' own docstrings rather than claimed: removing `ORDER BY` stays green at
fixture size (SQLite's plan is stable), so the page-walk test pins the **union**
invariant -- every row seen once, none twice -- not the clause.

### Two of my own tests were wrong, not the fix

`assert "state_hash_mismatch" not in matched["stale"]` raised
`TypeError: argument of type 'bool' is not iterable` -- `stale` is a **bool**, and
the named causes live in `reasons`. Read as a container it would have asserted
nothing on the happy path (`"x" not in True` does not even raise when the value is
a truthy list). And an assertion that rewriting an already-`.M` file moves
`state_hash` was **false before the fix too**: porcelain is byte-identical for a
content-only edit, so the assertion measured git's output rather than the lock.
Both are fixed, and both now carry the reason in the test.

---

## B022 — `query_index` answered `STALE_INDEX_AFTER_REFRESH` forever: the drift check and the indexer asked different questions

**Status**: fixed
**Severity**: HIGH (`query_index` unusable on [A]'s own repo; the tool has alternatives, so the loop was survivable, not harmless)

**Reproduction** (from Anh's real test, at generation `177faa8e5a45`):
`query_index(project_path=<[A]'s own repo>, query="…")` → `STALE_INDEX_AFTER_REFRESH`
after ~11s, stable across 3 consecutive calls. `refresh_project_map` completed
successfully each time and changed nothing.

**Root cause.** Not a missing ignore-list — the ignore-list exists and is correct.
The *caller* omitted the argument the ignore-list needs:

```
code_tools.py:3654      _should_scan_file(path)              # drift check  — NO project_root
surgical_mapper.py:1134 _should_scan_file(fp, project_root)  # indexer      — WITH it
```

`_should_scan_file` → `_skip_reason` → for `.json`/`.jsonl` calls
`_is_data_json(file_path, _depth_below_root(file_path, project_root))`.
`_depth_below_root` returns `None` when `project_root` is falsy, and R82's
**deliberate** conservative default then KEEPS the file
(`tests/test_data_json_and_reindex_r82.py`: "depth unknown -> conservative: index
it rather than silently drop it").

That default is right for the INDEXER and **inverted for the DRIFT CHECK**. In the
indexer, keeping a file means "index one file too many". In the drift check,
keeping it means **"there is a new file here"** — so every deep data-JSON the
indexer refuses was reported `added`, forever.

Measured on [A]'s own repo at generation `177faa8e5a45`:

```
files where the two predicates disagree     : 52
_cache_delta_sync                           : 0.44s total_changed=53 added=52 modified=1
of those 52 `added`, refused by the indexer : 52 / 52
genuinely addable                           : 0
```

An unbreakable loop: drift reports 52 new files → refresh → the indexer refuses
all 52 → drift reports 52 again. `query_index` is the only tool that gates **hard**
on drift, which is why only it died: `search_code` measured `ok=True` with
`cache_status` unaffected.

**One cause wearing two faces.** By the time the fix was written the same call
answered `INDEX_QUERY_TIMEOUT` ("budget 12.0s") instead — the phantom refresh had
grown to eat the whole `_query_index_deadline_seconds()` budget. Two error names,
one defect. A report that treated them as separate bugs would have chased the
timeout into the wrong module.

**Fix**: one argument, at `code_tools.py:3654` — `_should_scan_file(path, root)`.
Measured after: `total_changed 53 → 2`, and both remaining entries are files this
campaign really did change.

**The tests are the substance, not the one-line fix.** The invariant they pin is
not "the parameter is passed" but the consequence: **no file the drift check calls
`added` may be a file the indexer refuses.** A test asserting
`_should_scan_file(p) == _should_scan_file(p, root)` would be **wrong** and can
never pass — those two must still disagree, because R82's no-root default is
deliberate and untouched. The defect was never in the mapper; it was a caller not
passing what the mapper needs. Written that way first, the assertion failed, and
the honest form replaced it.

7 tests, `tests/test_drift_and_indexer_agree_b022.py`, mutation-verified: reverting
`, root` kills **5 of 7**. The 2 survivors are by design and named as such —
`test_the_fixture_really_contains_files_the_rule_refuses` (guard-the-guard: a
fixture with nothing to refuse proves nothing) and
`test_a_genuinely_new_source_file_is_still_reported` (the opposite failure: the fix
must not silence real drift). Both are independent of the fix on purpose.

Two of the seven exist because the first cut could not see the defect from where it
stood:

* `sm.build_index()` on a fresh tmp repo returns **0 files** and writes no
  INDEX.json — it only *assembles* an index from cache sidecars that already exist.
  Six tests were measuring an empty index rather than the drift rule.
  `generate_project_map` is the entry point that walks, writes the sidecars and
  publishes INDEX.json (measured: `build_index` → 0 files,
  `generate_project_map` → 2 files, `total_changed` 0). **My test was wrong, not
  the fix.**
* The invariant test asks what drift reports against a **real** index, so it only
  sees files that happen to be missing from it — an index already holding every
  phantom would hide the defect. `test_the_walk_admits_nothing_the_indexer_refuses`
  passes `index_override={"files": {}}` so `added` becomes the walk's complete
  admit-list, and every file the walk lets through gets checked.

**Audit of the other consumers** (Anh's second approved item). All six
`cache_delta` call sites — `code_tools.py:1794` (rebuild-skip probe), `1912`
(cache-status drift), `3092` (session state), `12599` (`validate_all`),
`6113`/`6161` (snapshot-scoped) and `orchestrator/tools/search.py:1165` (A03b) —
route through the **same** `_cache_delta_sync`, so the one-line fix reaches all of
them. There was exactly one other no-root call site,
`orchestrator/fileops/common.py:41` (`should_scan_for_text`), and its sign is the
**opposite**: measured 1312 admitted vs the indexer's 1260, 52 search-only files,
all `.json`. For a text search, keeping a file means finding a match; refusing it
means silently missing one. Left alone deliberately — the same missing argument is
a defect in one direction and the correct default in the other, which is exactly
why passing it blindly everywhere would be the wrong repair.

---

## B023 — `cbm_trace_path` answers `ok:true` with an empty trace when the start node does not resolve

**Status**: FIXED 2026-08-22, mutation-verified (2 mutants, see below)
**Severity**: MEDIUM (a wrong answer that reads as a correct one — "this symbol has no callers")
**Discovered**: 2026-08-21, measuring the five proposed "schema traps" against the live daemon at generation `177faa8e5a45`

### Reproduction

```
cbm_trace_path(project_path=<[A]'s own repo>, start_node_id="_cache_delta_sync",
               direction="out", depth=1)
  -> ok:true, edges:[], nodes_seen:0, truncated:false, error:null,
     truncation_reason:"start_node_not_found"

cbm_trace_path(project_path=<same>,
               start_node_id="code_tools.py:function:code_tools.cache_delta:3674")
  -> ok:true, edges:20, nodes_seen:18, truncation_reason:None
```

### What is and is not the defect

`start_node_id` requires a **graph node id**, not a symbol name, and the schema
already says so: *"Graph node id to traverse from (e.g.
'file.py:function:mod.fn:LINE')."* That requirement is correct and is **not** the
bug.

The bug is the answer shape when the requirement is missed. The response is
`ok:true`, `error:null`, `truncated:false`, and `edges:[]`. The only signal is
`truncation_reason:"start_node_not_found"` — a field named for truncation, carried
on a response that says it was not truncated. Every ordinary caller guard (`ok`,
`error`, `truncated`) reads success, and an empty `edges` list is
indistinguishable from a real symbol that genuinely has no callers.

The tool description declares the policy openly — *"Fail-open on
timeout/stale/missing"* — and fail-open is the right default for a bounded read
over a possibly-stale graph. Fail-open is only safe when the failure lands in a
field a caller actually guards on. This is B018's asymmetry the other way round:
there, a **successful** `search_code` omitted `ok` and read as a failure. Same
broken contract in both directions — **the guard field must track the outcome.**

### Fix (applied) — both proposed options were taken, at two different layers

Neither option alone was correct, and the reason is that `cbm_trace_path` has two
different kinds of caller. Option 2 went into the payload and option 1 went onto
the tool boundary:

* **`orchestrator/graph/query.py`** only *gains* a field. `start_node_resolved` is
  **three-state** — `None` = we never looked (`GRAPH_ENABLED=0`, or the DB never
  opened), `False` = we looked and the id is absent, `True` = resolved. `error`,
  `truncated` and `truncation_reason` are all left exactly as they were.
* **`orchestrator/graph/tools.py`** (the wrapper HTTP actually serves) refuses
  `start_node_resolved is False` with `ok:false`,
  `error_name="START_NODE_NOT_FOUND"`, and a `ca_next_action` naming
  `cbm_template_query(template="callers", name="<symbol>")`.

**Why the inner function must not refuse.** Three consumers call `query.py`
directly and read a dict: `orchestrator_server.py:12849` (`council_graph_context`),
`orchestrator/tools/read/context.py:399` (`architecture_context_pack`), and
`template_query`'s own `impact_scope` branch. All three treat a set `error` as a
graph failure, so setting it there would flip `council_graph_context`'s
`graph_status` from `ok` to `error` — the graph did not fail, the question was
unanswerable as asked. `truncation_reason` is kept for the same reason:
`orchestrator_server.py:12849` branches on that exact string.

**Why three states and not a bool.** `GRAPH_ENABLED=0` returns before any lookup.
A bool would make that path `False`, the wrapper would refuse it, and the
disabled-mode canary that guards every graph tool's degraded shape would break.
Hence `is False` in the wrapper, never `not ...`.

**The argument that this is a defect and not the declared fail-open policy**: the
same wrapper *already* refuses an **empty** `start_node_id` with `ok:false,
error_name:"SCHEMA_VIOLATION"`. An empty id and an unresolvable id are both "the
caller did not pass a usable node id", and the tool answered one with a refusal and
the other with a clean empty success. Fail-open is kept where it was promised —
`GRAPH_ENABLED=0` still answers the byte-identical degraded payload with `ok:true`.

7 tests, `tests/test_trace_path_start_node_b023.py`; 5 failed before the fix (the
2 that already passed were disabled-mode and the `truncation_reason` server signal,
which were correct all along). Two mutants:

* `if False:` at the wrapper → **3 failed** (the refusal tests).
* `if not result.get("start_node_resolved")` instead of `is False` → **1 failed**,
  exactly `test_graph_disabled_still_answers_the_degraded_payload`
  (`AssertionError: False is not True`, "GRAPH_ENABLED=0 must stay fail-open").
  That mutant is the whole reason that test exists.

### Where a real node id comes from

`cbm_template_query(template="callers", name="<sym>")` returns rows carrying `id`
in exactly the required shape (measured: `count 3, total_count 4`, rows like
`"id": "code_tools.py:function:code_tools._cache_delta_for_snapshot:3688"`). The
field is **`name`** — `symbol` and `symbol_name` are both refused, and a *repeat*
of the same wrong payload answers `DUPLICATE_RETRY_BLOCKED` ("Identical
deterministic failure already returned a correction"), which is the dedup guard
working rather than a new error. `find_symbol` cannot supply a node id: it is a
text search returning `file`/`line`/`match`.

### The four findings this one was filed alongside, all of which dissolved

Proposed as five "schema traps"; measured, four were caller error against schemas
that were already correct, and three of those four already refuse **loudly** with
`unsupported_fields` + `accepted_fields` + `ca_next_action`:

* `write_fix` is line-based (`start_line`/`end_line`/`new_content`) and its schema
  says so — no error to read, the payload was simply wrong.
* `copy_file` with `source`/`dest` → HTTP 400 `UNSUPPORTED_FIELD`,
  `accepted_fields: ["backup_existing","dst","overwrite","project_path","src"]`.
* `read_context_batch` with `reads` → HTTP 400 `UNSUPPORTED_FIELD`,
  `accepted_fields` naming `requests`.
* `write_new_file` accepts **both** `file_path` and `path` — measured `ok:true`
  both ways. There was never a trap here.

Two response-shape facts worth knowing, neither of which is a defect:
`read_file_range` returns the file text as a **`str`** in `result` and has **no
`content` key** (so `json.loads(resp["result"])` raises
`Extra data: line 1 column 2`), and `read_context_batch` nests that same shape per
item under `items[]`. And `expected_hash` is a **bypass for chained writes, not a
safety latch**: verified at `orchestrator/tools/write/core.py:590-663`, a matching
hash returns early (`:629`) and a wrong one falls through to the real `read_token`
guard, which still returns `_file_changed_error` (`:652`).

Recorded because writing all five up as tool defects would have made the docs lie
about four working tools. Full reasoning in MAINTENANCE_DOCTOR.md Entry 11.

---

## B024 — `cache_status` omitted `ok` on both paths, inside B018's own blind spot

**Status**: fixed
**Severity**: MEDIUM (a good cache report reads as a failure; a failed check cannot be told apart from a good one)
**Discovered**: 2026-08-21, smoke-testing the core tools on the live daemon after the B022 restart (generation `229018dbfe86`)

### Reproduction

```
cache_status(project_path=<[A]'s own repo>)
  -> keys: cache_age_hours, cache_exists, cache_files, generation, index_exists,
           last_updated, last_updated_iso, message, status
  -> "ok" NOT in the response
```

The failure path was worse: `except Exception as exc: return json.dumps({"error": str(exc)})`
— no `ok:false`, no `error_name`. CORE_RULE tells CAs to branch on `error_name`;
this path never set one, so no documented guard could distinguish "the cache is
fine" from "the check itself blew up".

### Why B018 did not catch it

B018 fixed exactly this defect in `search_code` and recorded, as part of the
finding, that *"every other tool surveyed answers `ok` on both paths — read_file_range,
git_status, validate_file, list_sessions, cbm_graph_status, regex_search"*.
`cache_status` was not in that list. **A survey is only evidence about the tools it
enumerated**, and the write-up generalised past its own sample.

Re-surveyed properly this time, and the first attempt at the re-survey was itself
wrong in a way worth recording: it read `ok` from `raw.get("result", raw)`, which
for `read_file_range` is a **`str`**, so it reported `read_file_range` and
`cbm_trace_path` as also missing `ok`. Reading `ok` at the **top of the HTTP
envelope** instead, the measured answer is that `cache_status` was the **only** one
missing it — `git_status`, `read_file_range`, `cache_delta`, `cbm_trace_path` and
`query_index` all answer it. That mistake is Entry 11's trap catching its own
author within the hour.

### Fix

`code_tools.py`, `cache_status`: `result.setdefault("ok", True)` on the success
path, and the `except` branch now answers
`{"ok": False, "error": …, "error_name": "CACHE_STATUS_FAILED", "ca_next_action": "refresh_project_map"}`.

`ok` deliberately means **"the check ran"**, not "the cache is fresh". Staleness
keeps its own fields (`status`, `stale_warning`, `drift_warning`), because
overloading `ok` into freshness would make a stale cache indistinguishable from a
broken check — the same conflation B018 warned about for zero-hit searches.

### Tests

4 tests, `tests/test_cache_status_ok_field_b024.py`. Reverting both halves of the
fix kills **3 of 4**. The survivor is `test_the_rest_of_the_payload_is_untouched`
and is **supposed** to survive: it pins that the fix did not reshape the report, so
a mutant that only removes the new key must leave it green.

The first cut of that test asserted `generation` was in the payload — true on the
live daemon's own repo, absent on a fresh tmp fixture. It failed on a correct
payload, which made it a test asserting the environment. Now recorded in its own
docstring as deliberately not asserted. Fourth time this campaign that a new test
was the bug rather than the code.

---

## B025 — `search_code`'s hot path re-`fnmatch`ed 56 constant globs for every single file

**Status**: FIXED 2026-08-22, mutation-verified (2 mutants, see below)

**Reported as**: "còn 1 tool chưa tối ưu" — a performance report naming `search_code`
as the last unoptimised tool, with five claims. **Three of the five were wrong about
where the code is**, and recording that is half the value of this entry:

| claim | verdict |
|---|---|
| 4.1 `search_code` uses `os.walk` + `read_text()` + a per-line Python scan (`orchestrator/tools/search.py:346`) | **wrong function.** `:346` is `find_symbol`'s inner `_search()`. `search_code` is at `:706` and its scanner `_search_snapshot` (`:883`) reads an immutable snapshot pin (`pin.iter_records`, `pin.read_bytes`) — no walk, no `read_text` |
| 4.2 `SKIP_DIRS` lacks the data dirs `1.footage`/`2.working`/`3.output`/`BIN` | real, **not fixed** — `SKIP_DIRS` is shared by search *and* the indexer, so widening it changes what gets **indexed**. Out of the authorised scope ("fix và tối ưu tool search_code") and needs its own gate |
| 4.3 no file-size threshold in the skip path | real, **not fixed** — same reason |
| 4.4 `search_code` ignores INDEX.json | it reads a snapshot pin instead, which is the stronger guarantee (size + sha256 verified per read). Not a defect |
| 4.5 `cache_status` calls `cache_delta` every time with no TTL | real, **not fixed** — a TTL on a staleness probe is exactly how a stale answer gets cached |

**The actual defect was somewhere the report did not look**, and two of my own
hypotheses had to be falsified by measurement before cProfile found it:

- *not* the regex — the whole per-line scan over cached payloads is **109ms**;
- *not* I/O — `read_bytes` is **94–125ms** warm for 17.9MB;
- *not* the snapshot's size+sha256 verification — **≈0ms** against a raw read (93 vs 94ms).

cProfile on a 0-hit `search_code` against [A]'s own repo (1261 indexed files,
17.9MB), 2.032s total, 5 753 475 calls:

```
1.109s  code_tools.py:3639        _cache_delta        <- A03's drift probe, on purpose
0.900s  surgical_mapper.py:957    _should_scan_file
0.898s  surgical_mapper.py:328    _skip_reason        <- 4369 calls
0.776s  surgical_mapper.py:266    _matches_any        <- 8716 calls
0.747s  surgical_mapper.py:268    <genexpr>           <- 246761 iterations
```

`_matches_any` is `any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)`.
For every file it re-lowercases all 56 patterns (11 `SECRET_FILE_GLOBS` + 45
`SKIP_FILE_GLOBS`) and re-dispatches each glob. `fnmatch` caches the compiled regex
by *pattern string*, so the parse is cached but the 246 761 per-call dispatches are
not. **The pattern set is a module constant. Nothing about it changes between files.**

### What was deliberately NOT changed: A03's drift probe

The report's model — "fewer hits is slower" — is real and reproduced at a uniform
`max_results=3`: `function` 185ms/3 hits vs `openActDialog` 1956ms/0 hits vs a
nonsense query 2023ms/0 hits. That 1.1s is `cache_delta`, and `search_code` pays it
**only** on `count == 0` (`orchestrator/tools/search.py:1155`) on purpose: a 0-hit over
a possibly-stale snapshot is *inconclusive*, not "absent", and silently degrading
there is what once let a real committed symbol be declared a phantom. The probe is
the feature; what it should not do is cost what it costs around it.
`TheDriftProbeIsNotTheDefect` exists so that "make the 0-hit path fast" is never
mistaken by a later reader for "make the 0-hit path skip the probe".

### Fix (applied) — compile each constant glob set once

`_compile_glob_union` builds one alternation per set with `fnmatch.translate`, which
is *the same translator `fnmatch` itself uses*, so the accepted language is preserved
**by construction** rather than by re-implementation. Patterns are `sorted()` first,
because a `set`'s iteration order is not stable across processes and an unstable
pattern string would compile to a differently-ordered regex between runs.
`_matches_any` is kept intact — it is the readable statement of what the unions mean.

### The trap: a faster classifier that silently relabels

The first candidate hoisted the cheap `SUPPORTED_EXTENSIONS` set-lookup to the front
of `_skip_reason`. It was **2.0×** — and it relabelled **227 files** in [A]'s own repo
(`.gitignore`, `.gitignore.bak`, `orchestrator.log`, `orchestrator.mcp.log`,
`orchestrator_access.log`, `phase1p5_run.log`, `pyrightconfig.json.bak`,
`restart_orchestrator.bat.lnk` and 219 more) from
`generated_archive_backup_or_internal_ai` to `unsupported_extension`. Both verdicts
mean "skip", so **nothing failed loudly** — only the reason string a caller reads
started lying. `_skip_reason` decides what gets indexed *and* what gets searched, and
its return value is surfaced. Rejected; the shipped fix keeps the original check order
and only makes each individual check cheaper.

A second candidate memoised the filename-only prefix with `lru_cache`: cold 265ms /
warm 235ms, i.e. **nothing**, because 4215 of 4400 filenames are distinct. Also
rejected. `test_no_file_changes_verdict` pins determinism whether or not a cache is
ever added back.

### Measured

Clean A/B, same process, 3 warm-ups + 5 timed runs, median, then **reverted and
re-measured under identical warm conditions**:

| query | reverted | fixed |
|---|---|---|
| `zzz-khong-ton-tai-b023` (0 hits) | 969ms | **735ms** (−24%) |
| `gradient` | 219ms | **172ms** (−21%) |
| `function` (3 hits) | 110ms | 125ms (noise; this path never reached the probe) |

`_skip_reason` micro-benchmark over 4400 real repo files: **453ms → 266ms (1.70×)**,
with **0 mismatches of 4400** against the original `fnmatch` loop.

### Mutants

- **drop the leading-dot test** in `_is_generated_or_junk` (the 227-file relabel in its
  purest form) → kills **2**: `test_a_dotfile_is_generated_not_unsupported` and
  `test_the_glob_sets_still_decide_the_same_names`;
- **revert the compiled unions** → kills exactly **1**,
  `test_it_is_faster_than_the_naive_glob_loop` (`235.0 not less than 218.0`). That test
  times the fix against the exact loop it replaced, both in the same process on the
  same files, so it is a floor rather than a hardware-dependent target.

**Pinned by**: `tests/test_skip_reason_glob_cost_b025.py` (11 tests).

---

## B026 - `search_code` mode='text' had no byte budget at all

**Status**: FIXED 2026-08-22 (commit `7d7284d`), pinned by
`tests/test_search_text_size_cap_b026.py` (7 tests)

`mode='regex'` has had a byte budget since it was written
(`regex_worker.validate_next_record_budget`), so a runaway pattern is stopped.
`mode='text'` had **nothing**: it re-scanned every byte of every record with no
ceiling, however heavy the repo.

Measured on `dreye_redesign`, a builder repo that keeps site DATA next to source:

| | records | bytes | `re.search` |
|---|---|---|---|
| everything the scanner reached | 760 | 230.9 MB | 2165 ms |
| the same minus 7 minified HTML files >2MB | 753 | 55.3 MB | **504 ms (-77%)** |
| actual source in that repo | 74 | 0.6 MB (largest 0.78 MB) | - |

Seven files were **76% of all bytes scanned**. cProfile on the live tool agrees:
of 3.86s, `re.Pattern.search` is 2.249s and reading snapshot blobs only 0.448s.
This is a **scan-cost** problem, not I/O -- so cutting what gets scanned is the
fix and skipping the read would not be.

**`max_file_bytes`** (default 2 000 000; `0` disables the cap for that one call).
Three placements were rejected on purpose:

- **not** in `should_scan_for_text` -- `index_delta` shares it, and a file
  vanishing from "should be scanned" would skew the drift report;
- skipped records are **counted and reported**
  (`skipped_large_files` / `_count` / `_listed` / `_hint`), never dropped
  silently: a hit inside a capped file returning as a confident 0-hit is exactly
  the failure **A03** exists to prevent.

**Two follow-ups shipped with it.** `registry_data.py` had to *register*
`max_file_bytes` -- without that line the dispatcher answers `UNSUPPORTED_FIELD`
over HTTP (reproduced), which is B-01's lesson from 10/08 again: *an escape hatch
nobody can reach is not an escape hatch*. And `test_skip_reason_glob_cost_b025.py`
carried a **pre-existing** defect (fails identically before and after B026,
verified by `git stash`): its `zzz_b025_...` sentinel was written as ONE literal,
so it sat in the test file itself, `search_code` scanned `tests/` and found 1 hit
-- the 0-hit probe was measuring itself. Fixed by splitting the string.

---

## B027 - `SKIP_DIRS` was global-only, so every repo's private names had to live in [A]

**Status**: FIXED 2026-08-22, mutation-verified (3 mutants, 3 killed)
**Severity**: MEDIUM (design defect; the workaround was to widen a shared global set)

**Reported as** the root cause behind B025's claim 4.2, which was filed real but
**not fixed**: `SKIP_DIRS` is shared by search *and* the indexer, so adding one
repo's `1.footage` / `2.working` / `3.output` / `BIN` changes what gets **indexed**
for every project on the daemon. Measured before writing anything: **0 grep hits**
for any per-project ignore mechanism. There was no narrow place to put a name.

Anh's framing is the one that shaped the design: *"khong lien quan den cu the 1 repo
nao ca, day la nang cap toolA"* -- so no repo name appears anywhere in the fix.

**Fix**: a `<project_root>/.orchestrator_ignore` overlay. `should_skip_dir` keeps
its one-argument form and gains an **optional** `project_root`
(`orchestrator/skip_rules.py:43`); the nine pre-B027 call sites that pass one
argument answer exactly the global rule, byte for byte, and are pinned to do so.

Decisions that are the entry:

- **Both directions.** `name` adds a skip; `!name` **un-skips** a global entry.
  A global set can only ever express one half, and `!` has to beat *both*
  `SKIP_DIRS` and the leading-dot rule or `!.venv_bge_m3` -- a real case -- does
  nothing and the hatch is not one.
- **`.council` is not hideable.** `DOT_DIRS_SEARCHABLE` is checked **before** the
  overlay: it is the SOT transcript tree and a project must not be able to hide
  it from search or index.
- **Parsed once per `(resolved_root, st_mtime_ns, st_size)`**, not per directory
  -- re-reading per directory is the exact per-file cost R82/B025 were fixing. The
  key is the content stat, so an **edit takes effect without a restart**: keying
  on the path alone is the A03 stale-read shape.
- **FAILS OPEN.** No file, unreadable file, unresolvable root -> two empty sets
  and the global rule. A broken ignore file silently *dropping* directories is
  the quiet-data-loss shape of CASE-2008.
- **`surgical_mapper.SKIP_DIRS is skip_rules.SKIP_DIRS` stays true** (CAN-042):
  the global set is never mutated, and a test asserts the identity.
- Only `#` starts a comment. `;` is a legal Windows directory character and
  treating it as a comment marker would silently drop an entry.

**Five walk sites forward the root** -- `orchestrator/fileops/common.py`,
two in `orchestrator/tools/search.py`, `surgical_mapper.py`, `code_tools.py` --
plus two `publish_snapshot` filters bound with `functools.partial`, the pattern
R82 already used there. The snapshot walk and the index scan **must** agree on
which directories exist, or `_validate_index_against_records` raises on the set
mismatch. `orchestrator/search_snapshots.py:_default_skip_dir` deliberately takes
no root, and says why in a comment: every real caller overrides `skip_dir`.

**Pinned by**: `tests/test_project_ignore_b027.py` (18 tests, 20 subtests) and
`tests/test_project_ignore_snapshot_b027b.py` (5 tests -- see B027b below).

### Mutants

| mutant | killed |
|---|---|
| skip wins over unskip (`!name` cannot beat a global entry) | **2** -- `test_bang_prefix_unskips_a_global_entry`, `test_a_bang_entry_can_reach_a_dot_directory` |
| cache keyed on the path only, dropping mtime+size | **1** -- `test_an_edit_is_picked_up_without_a_restart` |
| the `fileops/common.py` wrapper drops `project_root` | **1** -- `test_re_exported_helper_accepts_the_optional_root` |
| `search.py`'s `functools.partial` drops `project_root` (B027b) | **1** |
| `surgical_mapper.py`'s `functools.partial` drops `project_root` (B027b) | **1** |

The last three are the same shape and it is the one worth keeping: the leaf module is
still correct under every one of them, but the overlay never reaches a walk, so the
feature is completely dead while every unit test of `should_skip_dir` stays green.

---

## B027b - the overlay worked; the README described it wrongly

**Status**: FIXED 2026-08-22, mutation-verified (2 mutants, 2 killed)
**Severity**: LOW (documentation correctness), **found by live-smoke-testing B027**

My own defect, and the way it was found is the point: after restarting the daemon I
called `search_code` on a throwaway project, wrote `.orchestrator_ignore` **after** that
first call, and searched again -- **2 hits both times**. B027 looked like a feature that
did nothing.

It was not. Measured on the same tree, an ordinary **new source file** (`src/b.py`) is
equally invisible, because `search_code` never walks at query time -- it serves a
published **snapshot**. `refresh_project_map` republished it and the entry took effect:

| step | count |
|---|---|
| no ignore file | 2 |
| ignore file written, no refresh | 2 |
| after `refresh_project_map` | **1** |

So the overlay was right and **the sentence I wrote in the README was wrong**: *"An edit
takes effect immediately -- the file is cached on its `(mtime, size)`, so no restart is
needed."* Those are **two different claims**. No-restart is true and is pinned by
`test_an_edit_is_picked_up_without_a_restart`; immediately-visible-in-search is false. I
wrote it from the cache design rather than from a measurement.

**Fix**: the README now says no restart is needed **and** that `search_code` serves a
snapshot, so `refresh_project_map` is what makes the entry visible -- with the measured
2 -> 2 -> 1 chain and the note that this is not specific to the ignore file.

**Pinned by**: `tests/test_project_ignore_snapshot_b027b.py` (5 tests). Three assert the
overlay reaches the walk `publish_snapshot` is called from (the only walk search cares
about); two assert the README section names `refresh_project_map` and no longer promises
an immediate effect. Mutants: dropping `project_root=` from either `functools.partial`
kills 1 each.

**The lesson is the same one as B028b, in a different medium**: a doc sentence a caller
can follow to a wrong conclusion is a defect, exactly like a warning that names a field
the response does not carry. Both fixes were correct; both were invisible or misdescribed
at the surface a caller actually touches. **Restart and call the tool.**

---

## B028 - the destructive scanner read PowerShell here-string PROSE as commands

**Status**: FIXED 2026-08-22, mutation-verified (3 mutants, 3 killed)
**Severity**: MEDIUM (correctness defect in what `confirm_destructive` *means*)

**Reported and reproduced by Anh.** A commit message inside `@'...'@` -- a
single-quoted here-string, in which PowerShell does not even expand variables --
had two of its **sentences** classified as destructive commands:

```
line 8   "... wildcard, khong Remove-Item -Recurse tren thu muc):"   -> remove_item
line 30  "... git rm --cached -> 580/583 VAN NGUYEN TREN DIA ..."    -> git_rm
```

Anh's own words for the consequence: *"cang ghi chep trung thuc cang bi chan"*.
He worked around it with `git commit -F` on a separate file.

**One correction to the report, because it changes what the defect is**: the answer
was `DESTRUCTIVE_SCRIPT_CONFIRMATION_REQUIRED`, which **is** confirmable via
`confirm_destructive=N`, not a hard block. The defect is still real and is sharper
than "false positive": `confirm_destructive` is a number the caller must reproduce
and it is supposed to mean *"destructive statements I reviewed"*. Prose inflated it,
so documenting a deletion honestly **raised** the count, and editing a **word** of
the message moved it and invalidated an acknowledgement that was correct when
written. **A guard whose number means something other than what it says is a
correctness defect.**

### The reported fix was rejected, with a measurement

The report asked to *"bo qua noi dung ben trong `@'...'@` truoc khi quet"*.
Implemented as a mutant, that **breaks 8 tests**, because
`_DESTRUCTIVE_EMBEDDED_SHELL` exists for precisely this:

```powershell
powershell -Command @'
Remove-Item -Recurse -Force $target
'@
```

is a deletion, written as a string. Blanket-skipping here-strings waves it through.

**What shipped instead**: a here-string is treated as **data only when the body
contains no way to evaluate a string as code**. Any eval route present -- `iex`,
`Invoke-Command`, `-Command`, `cmd /c`, `node -e`, `python -c`, `subprocess`,
`bash -c`, `child_process`, `Start-Process`, `[scriptblock]::Create`, a bare `&`
call operator, or writing the string out to a `.ps1`/`.cmd`/`.sh` and running it
later -- and here-strings are scanned exactly as before B028
(`_STRING_EVAL_ROUTES`, `code_tools.py:7146`).

- **Nothing is dropped silently.** Prose findings move to a new
  **`prose_matches`** key with a `why_not_counted` string, plus a `warnings` line.
  They leave `count`, which is the number with meaning. `prose_matches` is
  **always present**, even empty -- a key that appears only sometimes is how a
  `KeyError` reaches production.
- **Footguns get the same exclusion for a sharper reason.**
  `DESTRUCTIVE_SCRIPT_FOOTGUN` is `retryable=False` with **no confirm path**, so a
  sentence merely *describing* the CASE-2008 `-Include`+`-LiteralPath` shape could
  make a script permanently unrunnable.
- **Malformed input fails SAFE.** PowerShell 5.1 syntax is followed exactly: an
  opener only opens when `@'` **ends** the line, a terminator must **start** a line
  (an indented `'@` is a parse error in 5.1), and an unterminated here-string
  yields an **empty** span, leaving every line executable. Guessing "this is prose"
  is the direction that costs files.
- Non-PowerShell runtimes are untouched -- `@'` in a JS or Python body is not a
  data delimiter. `runtime='any'` **does** get the rule, because
  `run_project_command` passes `'any'` and reaches PowerShell: without that, Anh's
  exact case still fails on the tool he actually used.

**Pinned by**: `tests/test_destructive_here_string_b028.py` (22 tests, 10 subtests) and
`tests/test_prose_matches_surface_b028b.py` (7 tests -- see B028b below).

### Mutants -- and a hole in my own test

| mutant | killed |
|---|---|
| drop the eval-route check (**the reported fix, verbatim**) | **8** |
| an unterminated here-string fails shut (`close_at = len(lines)`) | **2** |
| the opener matches anywhere on the line, not only at EOL | **1** |

The third mutant **survived the first cut, 22/22 green**, and the reason is the
lesson: `test_the_opener_must_end_the_line_like_real_powershell` used a body with
**no `'@` line at all**, so it passed through the *unterminated* fail-safe path and
never once exercised the end-of-line condition in its name. Rewritten with a body
that has a real terminator (`"$a = @'x'\nRemove-Item -Recurse $d\n'@\n"`) and
asserting `count == 1`, `findings[0]["line"] == 2`, `prose_matches == []`, it now
kills the mutant. **A test that passes for a reason other than the one in its name
is not a guard.**

---

## B028b - `prose_matches` never reached the caller

**Status**: FIXED 2026-08-22, mutation-verified (3 mutants, 3 killed)
**Severity**: MEDIUM, **found by live-smoke-testing B028**, and it is my own defect
from the first cut of B028

The scan dict gained `prose_matches` **and** a `warnings` line that says, in so many
words, *"(see prose_matches)"*. Not one of the **six** sites that flatten a scan into a
response copied the key. So the live answer told a caller to consult a field the
response did not have -- verified against the restarted daemon: the warning was present
and `prose_matches` was absent from all 18 keys.

That is worse than a missing escape hatch, because the warning **actively names it**. A
caller following the instruction finds nothing and then has to guess whether the guard
silently dropped two real destructive statements or correctly classified two sentences.
Same class as B026's unregistered `max_file_bytes` and B-01 before it: **a field nobody
can reach is not a field.**

**Fix**: added at all six sites -- the `run_project_command` dry-run preview and its
executed payload, both refusal builders (`_destructive_refusal`, `code_tools.py:7380`),
the script dry-run preview and the executed script result. Present **only when
non-empty**: the scan dict guarantees the key, a payload reports an event, and an
always-empty list on the 99% of scripts with no here-string is noise.

**Pinned by**: `tests/test_prose_matches_surface_b028b.py` (7 tests), including
`test_the_warning_names_a_field_that_is_actually_present` -- the exact defect in one
assertion -- and a **mechanical source assertion** over every line mentioning
`destructive_statements` with a +/-14-line window, because the defect was a *missing copy
at one of several structurally identical sites*, which no behavioural test on a single
tool can catch (B018: a survey only proves things about the sites it enumerated).

### Mutants

| mutant | killed |
|---|---|
| the dry-run preview drops the key | **4** |
| the refusal builders drop the key | **3** |
| the executed payload drops the key | **3** |

**Live verification** on generation `69f4f4e33aff` (PID 33988), three calls:

| case | count | confirm required | `prose_matches` |
|---|---|---|---|
| Anh's exact commit message | 0 | 0 | `[(2, remove_item), (3, git_rm)]` |
| the same here-string handed to `-Command` | **1** | 1 | absent |
| `Write-Host 'hello'` | 0 | - | key absent |

---

_Last updated: 2026-08-22 (campaign-1608 + JS-repo audit + stabilisation + real-test follow-up: added B002-B028, all fixed and mutation-verified; triage lives in MAINTENANCE_DOCTOR.md, and `scripts/live_answer_doctor.py` checks the live answers)_
