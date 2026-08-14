# HermC7 Bug Hunt Report — JS/TS Extractor (javascript.py)

**Date**: 2026-07-06
**Scope**: Chỉ `orchestrator/graph/extractors/javascript.py` (so sánh với `python.py` tham chiếu)
**Method**: Line-by-line audit, trace walker behavior, edge case injection

---

## Kết quả: 4 bugs confirmed (2 CRITICAL, 2 MEDIUM)

---

### BUG-1 [CRITICAL] `export default ...` silently dropped

**File**: `javascript.py:339-343`
**Root cause**: `export_statement` handler chỉ check `child_by_field_name("declaration")`, bỏ qua `value` field dùng cho `export default`.

```python
# Line 339-343 (CURRENT - buggy)
if t == "export_statement":
    decl = node.child_by_field_name("declaration")
    if decl is not None:
        self.walk(decl, exported=True)
    return   # <-- NẾU decl=None (export default), return ngay, không xử lý gì
```

**Các pattern bị ảnh hưởng**:
| Pattern | Tree-sitter field | Bị drop? |
|---|---|---|
| `export function foo() {}` | `declaration` | OK |
| `export class Foo {}` | `declaration` | OK |
| `export const x = 1` | `declaration` | OK |
| **`export default function() {}`** | `value` | **YES - DROPPED** |
| **`export default class {}`** | `value` | **YES - DROPPED** |
| **`export default () => {}`** | `value` | **YES - DROPPED** |
| **`export { foo, bar }`** | neither | **YES - DROPPED** |

**Impact**: Toàn bộ symbols + edges bên trong `export default function/class/arrow` không được extract. Đây là pattern rất phổ biến trong React components, utility modules, và Node.js modules.

**Suggested fix** (pseudocode):
```python
if t == "export_statement":
    decl = node.child_by_field_name("declaration")
    value = node.child_by_field_name("value")
    if decl is not None:
        self.walk(decl, exported=True)
    elif value is not None:
        # export default <expr> — walk the expression
        if value.type in ("function", "function_expression", "arrow_function",
                          "class", "class_expression"):
            # push synthetic name for tracking
            self.walk(value, exported=True)
        else:
            self.walk(value)  # other default exports (objects, primitives)
    # else: named re-exports `export { x, y }` — see BUG-3
    return
```

---

### BUG-2 [CRITICAL] `export { foo, bar }` named re-exports — symbols dropped + no is_exported marking

**File**: `javascript.py:339-343`
**Root cause**: Giống BUG-1 — `export { localDef }` không có `declaration` field → bị skip.

**Impact kép**:
1. Các symbols được re-export (`foo`, `bar`) không được đánh dấu `is_exported=1`
2. Nếu re-export alias (`export { foo as bar }`), alias symbol không được tạo

**Compare với python.py**: Python extractor có BUG-2 fix (lines 369-376) xử lý post-visit `__all__` marking. JS extractor CHƯA có post-visit nào cho named re-exports.

---

### BUG-3 [MEDIUM] Thiếu post-visit `is_exported` marking cho named re-exports

**File**: `javascript.py` (không tồn tại — cần thêm)
**Root cause**: Ngay cả sau khi fix BUG-2 để walk `export { foo, bar }`, các node `foo`/`bar` đã được emit từ trước (trong lần walk đầu tiên) với `is_exported` không được set — vì `export { foo }` thường nằm CUỐI file, sau khi `foo` đã được define.

**Compare với python.py**: Python extractor đã có fix cho vấn đề tương tự với `__all__` (lines 369-376):
```python
# python.py lines 369-376 (đÃ FIX)
if ex._exported:
    for n in ex.nodes:
        if n.get("name") in ex._exported and n.get("kind") in ("function", "method", "class"):
            n["is_exported"] = 1
```

JS extractor cần post-visit tương tự sau khi collect danh sách tên từ `export { ... }` statements.

---

### BUG-4 [MEDIUM] Anonymous arrow functions / function expressions trong call arguments không được track → call attribution sai

**File**: `javascript.py:237-276, 371-375`
**Root cause**: `walk()` không có handler cho `arrow_function` hoặc `function_expression` khi chúng xuất hiện độc lập (không gán vào variable). Kết quả: body của chúng vẫn được walk (qua generic `for child in node.children`), nhưng `_func_stack` không được push → `_emit_call` dùng sai source (outer function) hoặc bỏ qua hoàn toàn.

**Ví dụ bug**:
```javascript
function outer() {
    fetch('/api').then(data => {
        processData(data);   // <-- call này bị attr về outer() thay vì arrow
    });
}
```

Arrow function `data => { processData(data); }` không được push vào `_func_stack`, nên `processData(data)` call edge có `source = outer()` (sai).

**Compare với python.py**: Python có gap tương tự với `lambda` trong call arguments — nhưng Python lambda thường ngắn (single expression) nên ít nghiêm trọng hơn. JS arrow functions thường có body block đầy đủ.

**Suggested fix**: Thêm handler cho `arrow_function` và `function_expression` trong `walk()`:
```python
if t in ("arrow_function", "function_expression"):
    # Push anonymous slot so nested calls don't pollute outer func_stack
    anon_id = f"{self.file_path}:anon:{self._line(node)}"
    self._func_stack.append(anon_id)
    body = node.child_by_field_name("body")
    if body is not None:
        self.walk(body)
    self._func_stack.pop()
    return
```

---

## Non-bugs (confirmed OK)

| Check | Status | Note |
|---|---|---|
| Edge deduplication `(source, target, kind, line)` | OK | Consistent with python.py |
| Forward reference pre-seeding (lines 406-427) | OK | Handles `export_statement` unwrapping |
| `_func_stack` push/pop symmetry | OK | All paths balanced |
| `_class_stack` / `_class_stack_line` symmetry | OK | Push/pop always paired |
| `_text()` decode with `"replace"` | OK | Source is pre-validated UTF-8 |
| Method definitions inside classes | OK | Correct `_func_stack` + `defines` edge |
| Variable declarations with arrow functions | OK | Correctly pushed to `_func_stack` |
| `call_expression` → `member_expression` suffix match | OK | Design choice (CBM_SUFFIX_HIGH) |

---

## Priority fix order

1. **BUG-1 + BUG-2** (gộp chung): Fix `export_statement` handler để xử lý `value` field và named re-exports — ảnh hưởng đến coverage của mọi project dùng `export default`
2. **BUG-3**: Thêm post-visit `is_exported` loop (mirror python.py BUG-2 fix) — cần thiết cho đúng export semantics
3. **BUG-4**: Arrow function tracking — cải thiện call graph accuracy, đặc biệt quan trọng cho React/Node codebases
