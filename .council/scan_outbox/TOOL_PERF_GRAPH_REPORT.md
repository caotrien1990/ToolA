# BÁO CÁO HIỆU SUẤT TOOL SCAN + GRAPH
**Date:** 2026-07-10
**Project:** project-orchestrator
**Data source:** 8 scan artifacts (Round 2 + Round 3) + live API tests

---

## PHẦN 1: HIỆU SUẤT TOOL SCAN BUG & DEAD CODE

### 1.1 cbm_dead_code — Đánh giá: THẤP (low recall)

| Metric | Giá trị |
|--------|---------|
| Engine | vulture + ruff (static AST) |
| Tổng lần gọi | 12 (8 Round 2 + 4 Round 3) |
| Result rate | 0 candidates trong 10/12 lần gọi (scope=full) |
| Peak result | 80 candidates (Round 2 P2, scope=core, không filter) |
| False positive | CAO — vulture báo 80 nhưng thực tế đa số là unused imports (time, json), unused exception classes trong errors.py — không phải dead code thật |
| False negative | CAO — 14 dead code thật (H6 broken file, M3 dead function, 11 research scripts) đều KHÔNG được cbm_dead_code phát hiện |

**Root cause recall thấp:**
```python
# cbm_dead_code chỉ query graph.db (nodes có 0 inbound call edges)
rows = conn.execute(
    "SELECT n.* FROM nodes n WHERE n.kind IN ('function','method') "
    "AND n.is_exported = 0 "
    "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.target=n.id AND e.kind='calls');")
```
- **.council/tmp_qsleep/build/** KHÔNG được index vào graph.db → cbm_dead_code không thấy
- **research/** scripts KHÔNG được index → invisible
- **script-style modules** (subprocess invocation) có 0 static callers → bị exclude vì is_exported hoặc decorator → miss
- Vulture fallback cũng chỉ quét files đã index

**Kết luận:** cbm_dead_code **advisory only, recall thấp**. Bỏ lỡ 14/14 dead code thật Round 3. Phát hiện 0 bug, 0 dead code hữu ích trong toàn bộ 2 round.

---

### 1.2 ast_query — Đánh giá: TRUNG BÌNH (good precision, limited recall)

| Metric | Giá trị |
|--------|---------|
| Patterns hỗ trợ | 4: try_bare_except, class_inheriting, function_returning_dict, async_function |
| Patterns KHÔNG hỗ trợ | except_exception, bare_return, mutable_default, global_mutation, ... |
| Tổng lần gọi | 12+ |
| Precision | CAO — mọi hit đều đúng (bare except thực sự là bare except) |
| Recall | GIỚI HẠN — chỉ 4 pattern, miss nhiều bug pattern phổ biến |
| Noise | CAO — 45/50 hits nằm trong .tmp/uv-cache-codoc (vendored), chỉ 5/50 trong project files |

**Kết quả thực tế:**
- Phát hiện 5 bare except thật trong project files → 3 được fix (M2, M5 + R3-H6 adjacent)
- ast_query `except_exception` → UNSUPPORTED_FIELD (by-design, workers workaround bằng search_code regex)

**Kết luận:** ast_query **precision tốt nhưng recall giới hạn**. Cần mở rộng SUPPORTED enum.

---

### 1.3 cbm_template_query (orphan_symbols) — Đánh giá: THẤP (high noise)

| Metric | Giá trị |
|--------|---------|
| Template | orphan_symbols, entry_points |
| Total orphans | 134 (truncated, hiển thị 50) |
| Orphans thật | 0-5 (phần lớn là library glue trong fileops/common.py) |
| Precision | THẤP — 134 "orphans" nhưng đa số là utility functions được dynamic dispatch |
| Recall | KHÔNG đáng tin — 14 dead code thật đều KHÔNG nằm trong orphan list |

**Kết luận:** cbm_template_query **noise cao, không actionable**. 134 orphans nhưng 0 cái nào dẫn đến fix.

---

### 1.4 search_code — Đánh giá: CAO (sau fix WinError 5)

| Metric | Giá trị |
|--------|---------|
| Modes | symbol, callers, usage, regex, content |
| Trước fix | WinError 5 BLOCKER (4/4 workers fail) |
| Sau fix | OK — callers_count=26, regex OK, stale_index_live_fallback OK |
| Precision | CAO — results đúng |
| Recall | CAO — nhưng stale_index warning (graph index cũ, fallback live filesystem) |

**Kết luận:** search_code **tool scan tốt nhất** sau Round 2 fix. Là backbone cho worker scan.

---

### 1.5 TỔNG KẾT TOOL SCAN

| Tool | Precision | Recall | Đã ứng dụng? | Đánh giá |
|------|-----------|--------|--------------|----------|
| cbm_dead_code | Thấp | Thấp (0/14) | Có (12 lần) | ❌ Advisory, bỏ lỡ hết |
| ast_query | Cao | Trung bình (4 pattern) | Có (12+ lần) | ⚠️ Tốt nhưng giới hạn |
| cbm_template_query | Thấp | Không đáng tin | Có (8 lần) | ❌ Noise cao, 0 actionable |
| search_code | Cao | Cao | Có (20+ lần) | ✅ Tốt nhất, backbone scan |

**Bug phát hiện thực tế đến fix:** 19/19 fix đến từ **worker đọc code + search_code cross-check**, KHÔNG đến từ cbm_dead_code hay cbm_template_query.

---

## PHẦN 2: TOOL BUILD GRAPH — HIỆU QUẢ & ỨNG DỤNG

### 2.1 Graph module — Trạng thái thực tế

| Component | Trạng thái |
|-----------|-----------|
| GRAPH_ENABLED env | `1` (enabled) |
| use_graph_for("find_symbol") | `True` |
| use_graph_for("search_code") | `True` |
| graph.db | 22MB, 2360 nodes, 2664 edges |
| graph.db mtime | 2026-07-10 15:16 (cũ — không tự reindex) |
| Endpoints | `/api/graph/query`, `/api/graph/context` |
| Tools registered | 0 graph tools, 0 cbm tools (chỉ endpoints, không phải /api/call tool) |

### 2.2 Graph ĐƯỢC ứng dụng — nhưng PARTIAL

**Có ứng dụng:**
```
search.py:78  →  if use_graph_for("find_symbol"):
                     graph_resp = find_symbol_as_search_response(name, project_root)
                     → graph.db query → return JSON

orchestrator_server.py:7317  →  graph_find_symbol(project_path, query)
                                → /api/graph/query endpoint

orchestrator_server.py:7369  →  cbm_trace_path, graph_find_symbol
                                → /api/graph/context endpoint
```

Real test confirm:
```
/api/graph/query?query=build_tool_schemas
→ graph_status: ok, graph_used: True, results: 1 ✅
→ file_path: orchestrator/core/registry_data.py:1419

/api/graph/context?name=build_tool_schemas
→ graph_status: ok, graph_used: True ✅
```

**Graph ĐANG hoạt động** — tìm symbol chính xác, trả về file:line đúng.

### 2.3 Graph KHÔNG hiệu quả cho — Đánh giá: PARTIAL

| Use case | Graph hiệu quả? | Lý do |
|----------|----------------|-------|
| Tìm symbol chính xác (find_symbol) | ✅ Có | graph.db có 2360 nodes, query nhanh |
| Tìm callers (call graph) | ⚠️ Partial | Stale index → fallback live filesystem |
| Dead code detection | ❌ Không | .council/tmp_qsleep, research/ không được index |
| Cross-project trace | ❌ Không | Chỉ index production code trong orchestrator/ |
| Auto-reindex | ⚠️ Partial | _auto_reindex_if_stale có, nhưng budget 2s + CBM_AUTO_REINDEX_MAX_STALE cap |

### 2.4 Graph index coverage — Đánh giá: THẤP

| Source | Indexed? | Nodes |
|--------|----------|-------|
| orchestrator/ (production) | ✅ Có | ~2000+ |
| orchestrator_server.py | ✅ Có | ~200+ |
| tools_v15.py | ✅ Có | ~50+ |
| .council/tmp_qsleep/build/ | ❌ KHÔNG | 0 |
| research/CBM_CG/ | ❌ KHÔNG | 0 |
| scripts/ | ❌ KHÔNG | 0 |
| tests/ | ❌ KHÔNG | 0 |

**Graph index chỉ cover ~60% codebase** (production orchestrator/ tree). 40% còn lại (scripts, research, council tmp) invisible → cbm_dead_code trên những vùng này trả về 0.

### 2.5 Stale index — Vấn đề thực tế

```
search_code callers _error_response:
→ stale_index_live_fallback warning
→ graph index cũ (mtime 15:16, hiện tại 18:xx)
→ _auto_reindex_if_stale: stale_files > CBM_AUTO_REINDEX_MAX_STALE → SKIP reindex
→ Fallback: live filesystem scan (chậm hơn nhưng đúng)
```

**Tất cả 4 worker Round 3 đều gặp stale_index warning.** Graph index không tự cập nhật khi code thay đổi nhiều.

---

## PHẦN 3: KẾT LUẬN & ĐỀ XUẤT

### Tool scan bug & dead code — Hiệu quả thực tế

| Phương pháp | Bugs phát hiện | Dead code phát hiện | Đánh giá |
|-------------|---------------|---------------------|----------|
| cbm_dead_code | 0 | 0/14 | ❌ Không hiệu quả |
| cbm_template_query | 0 | 0/14 | ❌ Noise |
| ast_query | 3 (bare except) | 0 | ⚠️ Tốt nhưng hẹp |
| search_code | 5 (callers cross-check) | 5 (0 callers) | ✅ Hiệu quả |
| Worker đọc code tay | 19 | 14 | ✅ Backbone |

**Sự thật:** 19/19 fix đến từ **worker đọc code + search_code**, không phải tool scan tự động.

### Graph build — Hiệu quả & ứng dụng

| Aspect | Đánh giá |
|--------|----------|
| Đã ứng dụng? | ✅ Có — search.py + orchestrator_server.py import + use |
| find_symbol chính xác? | ✅ Có — graph_used=True, results đúng |
| Callers/call graph? | ⚠️ Partial — stale index → fallback live |
| Dead code? | ❌ Không — index coverage thấp (60%) |
| Auto-reindex? | ⚠️ Partial — budget cap, skip khi stale lớn |

### Đề xuất cải thiện

1. **cbm_dead_code**: Mở rộng index coverage sang .council/, scripts/, research/ → tăng recall
2. **ast_query**: Thêm patterns: except_exception, mutable_default, bare_return, global_state
3. **Graph reindex**: Tăng CBM_AUTO_REINDEX_MAX_STALE hoặc trigger reindex sau mỗi fix batch
4. **cbm_template_query**: Filter library glue (fileops/common.py) khỏi orphan list
5. **search_code**: Graph index refresh sau [A] restart (hiện stale sau restart)

END_TOOL_PERF_REPORT
