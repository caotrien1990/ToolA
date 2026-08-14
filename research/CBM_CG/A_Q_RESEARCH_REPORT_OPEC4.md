Repo	Path	Files
CBM	D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\research\CBM_CG\CBM	1,702
CG	D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\research\CBM_CG\CG	400
research\CBM_CG\
├── CBM\          (codebase-memory-mcp — C, 1702 files)
│   ├── src/           # Main C source (mcp/, store/, pipeline/, cypher/, ...)
│   ├── internal/cbm/  # Vendored tree-sitter grammars (158 languages)
│   ├── pkg/           # Package definitions
│   └── tests/         # 5604 tests
│
└── CG\           (codegraph — TypeScript, 400 files)
    ├── src/           # TypeScript source (extraction, resolution, mcp/, db/)
    ├── __tests__/     # Test suite
    └── scripts/       # Build/install scripts


---

## 1. [A] hiện có function nào trùng/gần với CBM/CodeGraph

| [A] tool hiện tại | Chức năng | Tương đương CBM/CodeGraph | Gap |
|---|---|---|---|
| `find_symbol(name, include=callers/usage/all)` | Regex scan filesystem tìm definition + callers | `search_graph` + `trace_path` | **[A] dùng regex, không có graph.** Không biết call chain depth, không type-aware |
| `find_callers(symbol)` | Wrapper find_symbol(include='callers') | `trace_path(direction='inbound')` | Không BFS, không depth limit |
| `import_graph(project_path)` | Regex scan import statements → adjacency list | CBM `IMPORTS` edges, CodeGraph import resolution | **[A] chỉ regex**, không resolve được alias, re-export, dynamic imports |
| `ast_query(pattern)` | Python AST, 4 preset patterns | CBM 158 languages tree-sitter | **[A] chỉ Python, chỉ 4 patterns cứng** |
| `search_code(query)` | Regex/smart text search | `search_code` (CBM), `codegraph_search` | OK, nhưng không structural |
| `get_file_symbols(file_path)` | Extract symbols từ 1 file | `get_code_snippet` | Giới hạn 1 file, không cross-file |
| `get_project_summary(project_path)` | Project overview | `get_architecture` | Khác — [A]summary là file tree/stats, không phải language/package/route/hotspot analysis |
| `architecture_audit_compact()` | Audit [A]'s own tool matrix (KtrB/KtrSK/KtrSH/KtrI) | Không có tương đương | **Đây là audit [A] chính nó**, không phải codebase architecture |

---

## 2. [A] chưa có — cần build mới từ CBM/CodeGraph

| Feature từ CBM/CodeGraph | Tại sao [A] cần | [A] hiện tại thiếu |
|---|---|---|
| **Knowledge graph (nodes + edges)** | Thay thế regex scan bằng graph queries — nhanh hơn, chính xác hơn | `find_symbol` chỉ regex, O(n) filesystem walk mỗi lần gọi |
| **Hybrid LSP type resolution** | CA đang phải "đoán function dựa trên skill và rule" — rất phiền | Không có type-aware resolution. `import_graph` chỉ regex |
| **Call chain BFS traversal (depth 1-5)** | "ai gọi X?" cần transitive call chain, không chỉ direct callers | `find_callers` chỉ finds `name(` pattern, không follow chain |
| **Framework route detection** | Web app: biết route → handler mapping | Không có |
| **Dead code detection** | Cleanup, reduce noise cho CA | Không có |
| **Louvain community detection** | Hiểu functional modules trong large codebase | Không có |
| **Architecture overview (languages, packages, routes, hotspots)** | Bootstrap session nhanh — CA không cần scan từng file | `get_project_summary` chỉ trả file tree/stats |
| **Git diff impact mapping** | "sửa file X affect gì?" — blast radius trước khi dispatch | Không có — CA phải tự grep |
| **Cross-service HTTP linking** | Microservice: biết API call → handler | Không có |

---

## 3. [Q] hiện có vs CBM/CodeGraph

| [Q] hiện tại | Tương đương CBM/CodeGraph | Gap |
|---|---|---|
| **Vector search** (blake2b hash-based embedding) | CBM semantic_search (Nomic embedding), CodeGraph đã xóa vector | [Q] embedding đơn giản hơn — hash-based, không contextual |
| **Lexical scoring** (LIKE + term matching) | CBM BM25 FTS5, CodeGraph FTS5 | [Q] dùng SQL LIKE, không BM25 |
| **`schema_hint_fast_path`** | Không có tương đương | [Q] có specialized fast path — giữ lại |
| **`/pre_search` + `/query`** | CBM `query_graph` (Cypher), CodeGraph `codegraph_explore` | [Q] là semantic layer, CBM/CodeGraph là structural layer — **complementary** |

---

## 4. Trả lời thắc mắc cụ thể của Anh

### "graph giúp gì cho CA? để thay thế RAG tốt hơn?"

**Không thay thế RAG.** Graph bổ sung cho RAG:
- **[Q] RAG** = "tìm context liên quan về ý nghĩa" (semantic)
- **Graph** = "tìm mối quan hệ chính xác giữa code entities" (structural)

Ví dụ: CA hỏi "ProcessOrder gọi gì?"
- [Q] RAG trả: "có discussion về order processing từ 3 tháng trước" (fuzzy)
- Graph trả: `ProcessOrder → PaymentService.charge(), InventoryService.reserve(), OrderController.notify()` (chính xác)

**Graph giúp CA**: thay vì grep 10 files để tìm call chain, CA query 1 lần graph → kết quả chính xác, 120x fewer tokens.

### "2 và 6. graph để cho user dễ đồng hành mà thôi — Confirm"

**Không chỉ cho user.** Graph giúp CA tự hiểu codebase:
- CA dispatch worker → cần biết "sửa hàm này affect file nào?" → graph trả blast radius
- CA search skill/rule → graph trả call chain chính xác thay vì regex guess
- CA detect dead code → graph trả functions không ai gọi

### "3. CA đang phải đoán function dựa trên skill và rule — Confirm"

**Đúng.** Hiện tại CA dùng `find_symbol` (regex) → kết quả sai lệch vì:
- Regex match `name(` nhưng không biết `name` đó có phải đúng function không
- Không resolve được import alias, inheritance, generics
- CA phải "guess" và retry → tốn token

**Hybrid LSP sẽ fix**: graph biết chính xác `user.profile.display_name()` resolve tới `Profile.display_name` ở module khác.

### "4. Gọi 1 api tool thì auto gọi các tool khác? Confirm [A] có đang có chức năng này"

**[A] KHÔNG có auto-composition.** Hiện tại:
- CA phải tự gọi `find_symbol` → rồi tự gọi `find_callers` → rồi tự gọi `read_file_range`
- Mỗi step là 1 tool call riêng, CA tự orchestrate

**CBM/CodeGraph có thể thay thế**: 1 tool call `trace_path` trả call chain + source code + blast radius. thay vì 3-4 tool calls riêng.

---

## 5. Function [A] cần upgrade/loại bỏ khi tích hợp

### Nâng cấp (upgrade)

| [A] function | Upgrade cần làm | Nguồn algorithm |
|---|---|---|
| `find_symbol` | Thêm graph-backed search: query graph trước, fallback regex | CBM `search_graph` |
| `find_callers` | BFS traversal thay vì regex scan | CBM `trace_path` |
| `import_graph` | Tree-sitter AST thay vì regex, resolve alias/re-export | CodeGraph import resolution |
| `ast_query` | Mở rộng 158 languages thay vì Python-only 4 patterns | CBM tree-sitter vendored grammars |
| `get_project_summary` | Architecture analysis: languages, packages, routes, hotspots | CBM `get_architecture` |

### Thêm mới (build từ CBM/CodeGraph)

| Function mới | Mô tả | Nguồn |
|---|---|---|
| `cbm_index(project_path)` | Index project vào knowledge graph | CBM `index_repository` |
| `cbm_trace_path(fn, depth, direction)` | BFS call chain traversal | CBM `trace_path` |
| `cbm_detect_changes(diff)` | Git diff → affected symbols + blast radius | CBM `detect_changes` |
| `cbm_dead_code detection` | Tìm functions không ai gọi | CBM Cypher |
| `cbm_get_architecture()` | Architecture overview | CBM `get_architecture` |
| `cbm_query_graph(cypher)` | Cypher-like queries | CBM `query_graph` |

### Loại bỏ (replace)

| [A] function | Lý do loại bỏ | Thay thế bằng |
|---|---|---|
| `ast_query` (Python-only 4 patterns) | Quá hạn chế, tree-sitter làm tốt hơn nhiều | CBM tree-sitter引擎 |
| `import_graph` (regex-based) | Inaccurate cho alias, re-export | CBM/CodeGraph import resolution |

### Giữ nguyên (không thay đổi)

| [A] function | Lý do giữ |
|---|---|
| `search_code` | Text search vẫn cần cho non-structural queries |
| `get_file_symbols` | Đọc 1 file vẫn hữu ích cho targeted reads |
| `architecture_audit_compact` | Audit [A] chính nó — khác với codebase architecture |

---

## 6. [Q] cần upgrade

| [Q] component | Upgrade | Nguồn |
|---|---|---|
| Vector embedding (blake2b hash) | Nâng cấp lên Nomic embed-code hoặc giữ hash nhưng thêm BM25 FTS5 | CBM BM25 |
| `/pre_search` | Thêm structural layer: query graph trước, merge với semantic results | CBM `semantic_query` |
| Lexical scoring | BM25 thay vì SQL LIKE | CBM FTS5 `cbm_camel_split` tokenizer |

---

## 7. Tóm tắt: CBM vs CodeGraph — nên lấy gì

| Lấy từ CBM | Lấy từ CodeGraph | Lý do |
|---|---|---|
| **Hybrid LSP** (type resolution) | **Native OS file watcher** | CBM: chính xác hơn. CodeGraph: real-time hơn |
| **Cypher query engine** | **Single-tool philosophy** | CBM: flexible hơn. CodeGraph: đơn giản hơn |
| **158 languages tree-sitter** | **Framework route detection** | CBM: broad hơn. CodeGraph: chi tiết hơn |
| **Dead code detection** | **Library embedding API** |各自有特色 |
| **Cross-service HTTP linking** | **Auto-sync debounce 2s** | |
| **Graph visualization 3D** | **`codegraph_affected` cho CI** | |

**Không lấy**: CodeGraph đã xóa vector search (đúng quyết định, [Q] đã có). CBM semantic search dùng Nomic embedding — cần evaluate xem có better hơn hash-based của [Q] không.

---

không phải extract nguyên si, mà là port algorithms + reimplement bằng Python.

Lý do:

CBM = C — tightly coupled, tree-sitter grammars compiled vào binary, Cypher enginelexing/parsing/planning/executingall trong C → không extract trực tiếp được, nhưng algorithms (graph schema, BFS traversal, import resolution, dead code detection) thì đơn giản enough để port Python
CG = TypeScript — modular hơn, có library API (import CodeGraph) → dễ extract hơn, nhưng vẫn là Node.js runtime, không gọi trực tiếp từ Python [A]
Nên làm:

Study algorithms từ C/TS source → hiểu logic
Reimplement bằng Python, dùng tree-sitter Python bindings + SQLite (đã có trong [A]/[Q])
Graph schema + BFS + FTS5 = Python native, không cần dependencies mới
Không nên: wrap C binary hay Node.js process — thêm complexity, thêm dependency, thêm failure point cho [A] framework.