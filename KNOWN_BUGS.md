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

_Last updated: 2026-08-21 (campaign-1608 + JS-repo audit: added B002-B016, all fixed and mutation-verified; triage lives in MAINTENANCE_DOCTOR.md)_
