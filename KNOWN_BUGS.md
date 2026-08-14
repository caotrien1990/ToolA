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

_Last updated: 2026-06-04 (added R-series design proposals: R101, R102, R103)_
