# COMC1_DETAILED_AUDIT

**Worker:** ComC1 (deepseek-v4-pro)  
**Task:** ComC1 Detailed Audit — ToolQ [A] Bridge, line-aware, function-aware  
**Mode:** audit-only, KHONG fix, KHONG sua source  
**Invariant check:** candidate_only tuyet doi — CHI /review_apply (human) moi ghi distilled_points

---

## A. clm_librarian_api.py :: handle_review_apply (lines 1835-1884)

### A1. MISSING_ARG validation (line 1839-1842)
**qid**: `str(body.get("queue_id") or "")` — OK, safe default.  
**actor**: `str((body.get("actor") ...) or "human")` — OK, safe default.  
Nếu qid rỗng -> trả về `MISSING_ARG`. **PASS.**

### A2. NOT_FOUND / ALREADY_DECIDED check (line 1846-1852)
Parameterized `WHERE queue_id=?`. row[5]=status positional check khớp với SELECT columns.  
`row[5] != "pending"` -> `ALREADY_DECIDED` kèm status hiện tại. **PASS.**  
*Tuy nhiên:* ALREADY_DECIDED có race condition — nếu 2 concurrent requests cùng nhận `pending` và cùng apply, cả 2 đều INSERT INTO distilled_points. Transaction cùng commit vì INSERT không vi phạm constraint (raw_turn_id không UNIQUE). Kết quả: duplicate distilled_point entries. Đây là **TRUE_BUG_CANDIDATE.**

**File:** clm_librarian_api.py:1846-1852  
**Classification: TRUE_BUG_CANDIDATE** — handle_review_apply race condition:  
SELECT+INSERT+UPDATE không được khóa (SELECT FOR UPDATE không được dùng). Hai concurrent callers có thể cùng thấy `status='pending'`, cùng INSERT distilled_points và cùng UPDATE applied. Kết quả: ghi duplicate point, applied_point_id sau cùng ghi đè applied_point_id trước (lost update).

### A3. Anchor resolution (line 1855-1864)
```
for r in (refs if isinstance(refs, list) else []):
    try: rid = int(r)
    except: continue
    if conn.execute("SELECT 1 FROM raw_turns WHERE id=?", (rid,)).fetchone():
        anchor = rid; break
```
Parameterized, validates int, bỏ qua non-int ref. **PASS.**  
Nếu không có anchor nào -> trả về `NO_RAW_ANCHOR`. Đúng spec.

### A4. Point mapping (line 1867-1868)
```
point = (payload.get("suggested_action") or payload.get("point")
         or payload.get("evidence_excerpt") or "(redistill candidate)")
```
Fallback chain OK. Spec says: `suggested_action or point or evidence_excerpt or "(redistill candidate)"`.  
**PASS.** Mapping fidelity OK.

### A5. INSERT distilled_points (line 1869-1875)
```
cur = conn.execute(
    "INSERT INTO distilled_points(raw_turn_id,subject,subtopic,point,point_type,confidence,
     evidence_snippet,verify_status,rag_indexed,created_at) "
    "VALUES(?,?,?,?, 'corrective', ?, ?, 'human_approved', 0, ?)",
    (anchor, str(row[2] or "redistill"), str(row[1] or ""), str(point)[:2000],
     float(payload.get("confidence") or 0.5), str(payload.get("evidence_excerpt") or "")[:1000],
     datetime.now(timezone.utc).isoformat()))
```
Parameterized. `str(point)[:2000]` có truncation. `evidence_excerpt[:1000]` có truncation.  
`point_type` hardcoded `'corrective'` — theo spec OK.  
**Vấn đề:** `distilled_points` table KHÔNG được tạo trong file này (ensure_api_tables chỉ tạo rule_bridge_index + schema_versions). Table này được tạo bởi offline pipeline (clm_librarian_distill.py). Nếu pipeline chưa chạy, INSERT sẽ thất bại với unhandled exception.

**File:** clm_librarian_api.py:1869-1875  
**Classification: TRUE_RISK_CANDIDATE** — handle_review_apply giả định `distilled_points` đã tồn tại. Nếu offline pipeline chưa tạo table, INSERT crash không có structured error response.

### A6. UPDATE review_queue + commit (line 1877-1880)
Cả INSERT và UPDATE nằm trong cùng 1 transaction (commit ở line 1880). Atomic. **PASS.**

---

## B. clm_librarian_api.py :: handle_capture (lines 1596-1653)

### B1. Writer validation (line 1610-1612)
`writer != "A"` -> `BadWriter`. **PASS.**

### B2. session_id + turns validation (line 1613-1618)
`sid = str(body.get("session_id") or "").strip()`, kiểm tra rỗng.  
`turns = body.get("turns")`, kiểm tra isinstance list + không rỗng. **PASS.**

### B3. content_hash computation (line 1627-1635)
```
up = str(t.get("user_prompt") or "")
ar = str(t.get("assistant_response") or "")
...
ch = _sha("|".join([writer, session_id, sid_u, sid_a, ph, rh]))
```
`ph = _sha(up)` với `_sha` = `sha256(x.encode("utf-8", errors="ignore")).hexdigest()`.  
`errors="ignore"` mất dữ liệu khi unicode lỗi — nhưng đây là hash của nội dung, hash-mismatch chỉ mất dedup, không ảnh hưởng correctness. **LOW risk.**

### B4. INSERT OR IGNORE + content_hash UNIQUE dedup (line 1636-1648)
```
"INSERT OR IGNORE INTO raw_turns(...,content_hash,...,status='captured',...) VALUES(...)"
```
So sánh rowcount: `if cur.rowcount: inserted.append(...) else: skipped += 1`.  
**PASS:** Dedup qua content_hash UNIQUE constraint.

### B5. Missing turn-level error isolation (line 1625-1626)
```
if not isinstance(t, dict): continue
```
Turn không phải dict bị silent skip. Không có log_event cho skip. **Low risk** vì caller là [A] orchestrator đã validate first.

**File:** clm_librarian_api.py:1625-1626  
**Classification: MISUNDERSTANDING_OR_NEEDS_VERIFICATION** — handle_capture silent skip non-dict turns, không logging.

---

## C. clm_librarian_api.py :: handle_review_ingest / list / reject

### C1. handle_review_ingest path traversal (line 1769, 1776)
```
qdir = (body.get("queue_dir") if isinstance(body, dict) else None) or _default_queue_dir()
for fp in _glob.glob(os.path.join(qdir, "*.jsonl")):
```
`queue_dir` từ body không được validate. Caller có thể truyền bất kỳ path nào.  
Mitigation: localhost-only, trusted callers. **LOW risk.**

**File:** clm_librarian_api.py:1769-1776  
**Classification: TRUE_RISK_CANDIDATE** — handle_review_ingest queue_dir không sanitize, cho phép đọc .jsonl từ bất kỳ directory nào.

### C2. handle_review_ingest file exception isolation (line 1801-1802)
```
except Exception: continue
```
Một file lỗi -> bỏ qua file đó. **PASS.**

### C3. handle_review_list limit clamp (line 1817-1818)
```
limit = max(1, min(200, int(body.get("limit", 50))))
```
Clamp 1-200 đúng spec. **PASS.**

### C4. handle_review_reject ALREADY_DECIDED encoding (line 1896-1900)
```
WHERE queue_id=? AND status='pending'
```
Không có check riêng như handle_review_apply. Nếu item đã applied/rejected, rowcount=0, trả về `ok: False, changed: 0`. Inconsistent với apply — apply trả về `ALREADY_DECIDED` với status, reject chỉ trả về `changed: 0`. **Intentional per design** (optimistic locking via WHERE clause).

**File:** clm_librarian_api.py:1896-1900  
**Classification: MISUNDERSTANDING_OR_NEEDS_VERIFICATION** — handle_review_reject không có ALREADY_DECIDED check riêng, inconsistent với apply nhưng valid per optimistic locking design.

---

## D. clm_librarian_api.py :: handle_machine_cluster_report + /query enrich

### D1. recent_n query (line 1497-1502)
```
recent_n = clamp_int(body.get("recent_n"), 0, 0, 100)
if recent_n and not raw_ids:
    raw_ids = [int(r[0]) for r in _rc.execute(
        "SELECT id FROM raw_turns WHERE COALESCE(deleted_at,'')='' ORDER BY id DESC LIMIT ?",
        (recent_n,)).fetchall()]
```
Parameterized. `ORDER BY id DESC LIMIT ?` lấy newest đúng spec. **PASS.**

### D2. /query assist_score enrich (line 1435-1446)
```
_hid = [int(h["id"]) for h in hits]
_ph = ",".join("?" * len(_hid))
_asc = {int(r[0]): r[1] for r in conn.execute(
    "SELECT r.id, d.assist_score FROM rag_chunks r "
    "LEFT JOIN distilled_points d ON d.id = r.point_id "
    "WHERE r.id IN (" + _ph + ")", _hid).fetchall()}
for _h in hits:
    _h["assist_score"] = _asc.get(int(_h["id"]))
```
Parameterized SQL. `_asc.get(...)` trả về None khi không có match — KHÔNG đổi sort order.  
Wrap trong `try/except: pass` — silent failure nếu `int(h["id"])` lỗi.  
Không có log_event khi enrich fail.

**File:** clm_librarian_api.py:1436-1446  
**Classification: TRUE_RISK_CANDIDATE** — /query assist_score enrich silent fail trong try/except pass. Nếu enrich lỗi, hits trả về không có assist_score nhưng không báo cho caller.

---

## E. orchestrator_server.py :: memory tools (lines 6554-6609)

### E1. _toolq_post timeout (line 6554-6563)
```
def _fire():
    req = urllib.request.Request(...)
    return urllib.request.urlopen(req, timeout=timeout).read()
raw = await asyncio.to_thread(_fire)
```
Default timeout 20s (30s cho cluster). **PASS.**

### E2. memory_query fail-soft (line 6574-6575)
```
return json.dumps({"ok": False, "degraded": True, "error_name": "MEMORY_UNAVAILABLE", "error": str(exc)[:160], "hits": []}, ...)
```
**PASS:** Đúng spec, trả về degraded=True, hits=[].

### E3. memory_recall_cluster missing arg validation (line 6580-6591)
```
if "query" not in payload and "raw_ids" not in payload:
    return json.dumps({"ok": False, "error_name": "MISSING_ARG", ...})
```
**PASS:** Validate trước khi gọi ToolQ.

### E4. memory_capture turns validation (line 6601-6604)
```
if not session_id or not turns: ... MISSING_ARG
if not isinstance(turns, list): ... BAD_ARG
```
**PASS.**

### E5. Registry registration (line 6612-6625+)
6 memory tools: memory_query, memory_recall_cluster, memory_capture. Phase T1. Schema khớp.  
Registry drift check: NONE (per spec). **PASS.**

---

## F. clm_migrate_raw_writer_A.py + clm_assist_rerank.py (not in bin/)

### F1. Migration idempotency (line 35)
```
if "'A'" in cur_sql: print("Already migrated"); return 0
```
String match check. Nếu CREATE TABLE SQL có chứa 'A' trong comment hoặc column name, false positive.  
`writer TEXT NOT NULL CHECK(writer IN ('claude','codex','A'))` — 'A' xuất hiện trong CHECK, OK.

**File:** clm_migrate_raw_writer_A.py:35  
**Classification: MISUNDERSTANDING_OR_NEEDS_VERIFICATION** — idempotency check là substring match "'A'" trong raw_turns DDL. Fragile nếu future migration thay đổi CHECK constraint chứa 'A' ở dạng khác.

### F2. Migration backup + rollback (line 37-57)
```
bak = DB + ".bak_" + time.strftime("%Y%m%d_%H%M%S")
shutil.copy2(DB, bak)
...
PRAGMA foreign_keys=OFF -> BEGIN -> CREATE -> INSERT -> DROP -> RENAME -> FK_CHECK -> COUNT_CHECK
-> COMMIT (hoặc ROLLBACK nếu lỗi)
```
Backup before mutation. BEGIN trước create+insert. FK_CHECK + COUNT_CHECK sau. Rollback on mismatch. **PASS.**

### F3. Migration PRAGMA foreign_keys toggle (line 42, 58)
`PRAGMA foreign_keys=OFF` trước migration, `ON` sau COMMIT. Giữa ON pragma và COMMIT, concurrent connection có thể INSERT vi phạm FK. Risk thấp vì migration chạm exclusive + timeout cao.

**File:** clm_migrate_raw_writer_A.py:42, 58  
**Classification: TRUE_RISK_CANDIDATE** — migration SET foreign_keys=OFF rồi ON sau COMMIT, tạo window mỏng cho FK violation.

---

## Test Gap Analysis

### Test coverage per function:

| Function | Coverage |
|----------|----------|
| handle_capture | clm_librarian_capture_test.py: test_insert/dedup (capture module, không API handler) |
| handle_review_apply | **KHÔNG có test nào** |
| handle_review_ingest | **KHÔNG có test nào** |
| handle_review_list | **KHÔNG có test nào** |
| handle_review_reject | **KHÔNG có test nào** |
| handle_machine_cluster_report | clm_librarian_api_machine_cluster_report_test.py |
| handle_query | clm_librarian_api_p2b1_test.py: QueryResponseShapeTests |
| handle_query enrich | **KHÔNG có test nào cho assist_score enrich** |
| migration | **KHÔNG có test nào cho clm_migrate_raw_writer_A.py** |

**Classification: TEST_GAP_CANDIDATE**
4 review endpoints (apply/ingest/list/reject) và 2 critical path segments (query enrich, migration) không có unit test coverage.

---

## Summary of Findings

| # | File | Func:Line | Classification | Description |
|---|------|-----------|----------------|-------------|
| 1 | clm_librarian_api.py | handle_review_apply:1846-1852 | **TRUE_BUG_CANDIDATE** | Race condition: 2 concurrent apply calls cùng thấy pending → duplicate distilled_points + last-write-wins applied_point_id |
| 2 | clm_librarian_api.py | handle_review_apply:1869-1875 | **TRUE_RISK_CANDIDATE** | INSERT distilled_points không kiểm tra table tồn tại — table do offline pipeline tạo, không phải ensure_api_tables |
| 3 | clm_librarian_api.py | _ensure_review_table:1752-1758 | **MISUNDERSTANDING_OR_NEEDS_VERIFICATION** | applied_point_id INTEGER không có FK constraint tham chiếu distilled_points.id |
| 4 | clm_librarian_api.py | handle_capture:1625-1626 | **MISUNDERSTANDING_OR_NEEDS_VERIFICATION** | Silent skip non-dict turns không log |
| 5 | clm_librarian_api.py | handle_review_ingest:1769-1776 | **TRUE_RISK_CANDIDATE** | queue_dir từ body không sanitize — path traversal đọc .jsonl bất kỳ |
| 6 | clm_librarian_api.py | handle_review_reject:1896-1900 | **MISUNDERSTANDING_OR_NEEDS_VERIFICATION** | Không ALREADY_DECIDED check riêng, inconsistent với apply (intentional per optimistic locking) |
| 7 | clm_librarian_api.py | handle_query:1436-1446 | **TRUE_RISK_CANDIDATE** | /query assist_score enrich silent fail trong try/except pass, không log |
| 8 | clm_migrate_raw_writer_A.py | main:35 | **MISUNDERSTANDING_OR_NEEDS_VERIFICATION** | Idempotency check là substring "'A'" fragile |
| 9 | clm_migrate_raw_writer_A.py | main:42,58 | **TRUE_RISK_CANDIDATE** | Migration foreign_keys OFF→ON giữa COMMIT và ON pragma — window nhỏ cho FK violation |
| 10 | (no test file) | review endpoints | **TEST_GAP_CANDIDATE** | handle_review_apply/ingest/list/reject — 0 unit tests |
| 11 | (no test file) | handle_query enrich | **TEST_GAP_CANDIDATE** | assist_score enrich path — 0 unit tests |
| 12 | (no test file) | clm_migrate_raw_writer_A | **TEST_GAP_CANDIDATE** | Migration script — 0 unit tests |

---

## Invariant Verification

**candidate_only tuyet doi — CHI /review_apply (human) moi ghi distilled_points.**

- `handle_capture`: INSERT raw_turns(status='captured'). KHÔNG chạm distilled_points. ✅
- `handle_review_ingest`: INSERT review_queue(status='pending'). KHÔNG chạm distilled_points. ✅
- `handle_review_reject`: UPDATE review_queue(status='rejected'). KHÔNG chạm distilled_points. ✅
- `handle_machine_cluster_report`: read-only. ✅
- `handle_query`: read-only (assist_score enrich dùng LEFT JOIN, không mutate). ✅
- `handle_review_apply`: ENDPOINT DUY NHẤT ghi distilled_points. verify_status='human_approved'. ✅
- orchestrator_server.py memory tools: tất cả fail-soft, không mutate trực tiếp. ✅

**INVARIANT: PASS** — candidate_only giữ vững.

---

END_COMC1_DETAILED_AUDIT
