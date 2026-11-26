// ═══════════════════════════════════════════════════════════════
// TOOL DATA — 260 tools with full metadata (256 in file + 4 missing)
// ═══════════════════════════════════════════════════════════════
const TOOLS = [
// T1 — Read/Inspect
{"n":"acceptance_dashboard","t":"T1","cat":"audit","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Bảng nghiệm thu gọn một lượt gọi cho các chỉ số R28/R29: chạy _audit_tool_matrix_sync và alias_contract_test ngay trong process (tắt HTTP smoke), tuỳ chọn chạy probe inspect_project_usage khi có probe_path, cộng một lượt quét schema (mô tả yếu, độ nhất quán của mutation_scope) và các lượt quét AST tìm khoá dict trùng cùng lỗi topo import ở tầng file; sau đó so số thực với target R29 rồi trả về all_pass. Mọi lượt gọi chạm tới phần audit matrix đều ghi thêm một bản ghi JSON vào .orchestrator_cache/matrix_log.jsonl, vì _audit_tool_matrix_sync được gọi với mặc định persist=True.","uc":"Sau một lần sửa code ToolA, một CA gọi acceptance_dashboard một lượt để kiểm drift, alias contract, schema yếu và khoá trùng trong một câu trả lời ~500 token thay vì mười lượt audit riêng lẻ.","cit":["tools_v15.py:5636","tools_v15.py:5643","tools_v15.py:5714","code_tools.py:3403","code_tools.py:4066","code_tools.py:4069","code_tools.py:4070"],"h":"tools_v15.py:5620","wk":"none","der":false,"wr":[{"kind":"file","target":".orchestrator_cache/matrix_log.jsonl"}],"err":{"c":"i","r":"output-history.md:L124 (R33)","q":"dashboard audit-log append via _audit_tool_matrix_sync persist=True"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"alias_contract_test","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Smoke hồi quy cho alias contract, chạy ngay trong process: với mọi alias đã đăng ký, nó đưa một body thăm dò chỉ chứa trường cũ qua orchestrator_server._normalize_body rồi kiểm trường cũ biến mất, trường canonical xuất hiện và giá trị được giữ nguyên; cùng probe đó chạy cho _HARMLESS_IGNORE_FIELDS (trường phải biến mất). Khi include_http_smoke=true, nó POST thêm một mẫu các ca từng fail trong lịch sử lên các endpoint /api/call đang sống. Lượt gọi mặc định (tắt HTTP smoke) không ghi gì vào đâu cả.","uc":"Sau khi sửa _ARG_ALIASES, một CA chạy alias_contract_test để xác nhận mọi tên tham số cũ vẫn normalize đúng sang tên canonical trước khi chốt thay đổi.","cit":["tools_v15.py:5259","tools_v15.py:5275","tools_v15.py:5284","tools_v15.py:5314","tools_v15.py:5332","tools_v15.py:5350","tools_v15.py:5357"],"h":"tools_v15.py:5236","wk":"none","der":false,"wr":[],"err":{"c":"ii","r":"output-history.md:L126 (R35)","q":"opt-in write; registry scope none admits no opt-in write"},"use":{"b":"light","c":18,"ls":"2026-11-14T22:28:29"}},
{"n":"architecture_audit_compact","t":"T1","cat":"audit","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Một lượt audit gọn cho bốn kiến trúc (KtrB drift backend, KtrSK vệ sinh alias trong skill, KtrSH sức khoẻ schema-hint qua alias_contract_test, KtrI vệ sinh inventory cộng các khoảng trống progressive), dựng từ _audit_tool_matrix_sync và alias_contract_test, trả về bản digest ~30 dòng kèm trạng thái ok/review_needed tổng thể thay vì toàn bộ ma trận. Mọi lượt gọi chạm tới phần audit matrix đều ghi thêm một bản ghi JSON vào .orchestrator_cache/matrix_log.jsonl, vì _audit_tool_matrix_sync được gọi với mặc định persist=True.","uc":"Khi mở session, một CA gọi architecture_audit_compact để lấy digest sức khoẻ bốn kiến trúc (backend, skill, schema hint, inventory) trong một lượt gọi rẻ, trước khi quyết định audit gì tiếp theo.","cit":["tools_v15.py:5433","tools_v15.py:5466","tools_v15.py:5439","tools_v15.py:5483","code_tools.py:4066","code_tools.py:4069","code_tools.py:4070"],"h":"tools_v15.py:5424","wk":"none","der":false,"wr":[{"kind":"file","target":".orchestrator_cache/matrix_log.jsonl"}],"err":{"c":"i","r":"output-history.md:L124 (R33)","q":"dashboard audit-log append via _audit_tool_matrix_sync persist=True"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"architecture_context_pack","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Dựng một gói đọc cho một symbol hoặc một query: tra symbol đó trong DB graph code suy diễn CBM (chỉ đọc) qua graph_find_symbol, lần theo callers/callees cả hai chiều tới độ sâu yêu cầu (clamp 1-5), rồi đọc các file/range đã xếp hạng qua read_context_batch trong cùng một ngân sách token. Khi graph không trả gì, nó lùi về đúng một file_path; khi cả hai đều không resolve, nó trả ARCHITECTURE_CONTEXT_UNAVAILABLE. Tool không bao giờ ghi và không bao giờ re-index; graph thiếu hoặc cũ làm gói đọc giảm chất lượng (graph_degraded_reason) chứ không khiến nó tự dựng lại.","uc":"Trước khi sửa một hàm lạ, một CA xin architecture context pack của hàm đó để lấy định nghĩa cùng callers/callees dưới dạng các range file chính xác mà đọc tiếp. (plausible-but-unverified)","cit":["orchestrator/tools/read/context.py:869","orchestrator/tools/read/context.py:802","orchestrator/tools/read/context.py:912","orchestrator/tools/read/context.py:903"],"h":"orchestrator/tools/read/context.py:858","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"assert_target_idle","t":"T1","cat":"assert","mut":false,"prot":false,"sess":false,"fg":"test","eff":"Cổng tín hiệu hoàn thành, một lượt, chỉ đọc và chỉ cảnh báo mềm. Đánh giá độc lập từng tín hiệu trong signals[] - folder_growth_stable (hai ảnh os.walk cách nhau một khoảng sleep, idle khi giống hệt nhau), process_cpu_idle và process_io_idle (mẫu psutil, idle khi dưới ngưỡng hoặc không đổi), log_pattern_match (tìm regex trong 50KB cuối log) - và chỉ trả idle=true khi TẤT CẢ tín hiệu được đánh giá đều báo idle, kèm một bộ phân loại outcome (idle, copy_in_progress, process_busy, busy, unknown, no_signals). Ba loại cdp_global_flag, ui_state_stable và window_title_match chỉ là stub best-effort, luôn báo idle=None vì không được đánh giá. ok=true chỉ nghĩa là cổng đã chạy; kết luận thật nằm ở outcome và idle. Các tham số cũ path/folder cộng stable_seconds cộng timeout_seconds tự tổng hợp thành một tín hiệu folder_growth_stable. Toàn bộ thân hàm không ghi file, DB, process hay browser nào.","uc":"Sau khi khởi động một lượt copy hoặc render dài trong một workflow step, một CA gọi assert_target_idle kèm tín hiệu folder_growth_stable trên thư mục xuất và chỉ đi tiếp khi outcome bằng idle, thay vì đoán một khoảng sleep cố định.","cit":["code_tools.py:14190","code_tools.py:14240","code_tools.py:14261","code_tools.py:14269","code_tools.py:14321","code_tools.py:14338","code_tools.py:14353"],"h":"code_tools.py:14190","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"ast_query","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Đi qua cây dự án (bỏ __pycache__, node_modules, .git, venv, .orchestrator_cache), parse từng file Python khớp bằng module ast, và trả tối đa max_results (mặc định 50) hit cho đúng MỘT trong bốn pattern dựng sẵn: async_function, class_inheriting, try_bare_except (except trần, rủi ro) hoặc function_returning_dict. File không đọc được hoặc không parse được bị bỏ qua lặng lẽ.","uc":"Một CA chạy ast_query với pattern try_bare_except trên một dự án để liệt kê mọi handler except trần trước khi review độ tin cậy.","cit":["tools_v15.py:546","tools_v15.py:563","tools_v15.py:573","tools_v15.py:579","tools_v15.py:589","tools_v15.py:600","tools_v15.py:601"],"h":"tools_v15.py:532","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"audit_reconciliation_report","t":"T1","cat":"audit","mut":false,"prot":false,"sess":false,"fg":"audit","eff":"Đối soát các error event của một session từ sessions.db bằng truy vấn chỉ SELECT: mỗi error còn mở được phân loại expected, recovered, still_open hoặc infrastructure, dùng bằng chứng thất bại dự kiến tường minh, một lần thành công về sau mang đúng correlation khác rỗng, hoặc các dấu infrastructure (no such table, connection refused, timeout, database is locked, backend down, service unavailable, broken pipe, outbox). Tool trả số đếm từng bucket kèm link bằng chứng theo từng error và một gợi ý record_error_resolution cho mỗi error đã giải; gợi ý chỉ là dữ liệu và không bao giờ được thực thi.","uc":"Sau một session chập chờn, một CA chạy audit_reconciliation_report cho session đó để xem error nào thật sự còn mở, error nào đã hồi, và error nào chỉ là nhiễu infrastructure.","cit":["tools_v15.py:1142","tools_v15.py:1152","tools_v15.py:1156","tools_v15.py:1159","tools_v15.py:1161","tools_v15.py:1166","tools_v15.py:1167","tools_v15.py:1170"],"h":"tools_v15.py:1111","wk":"none","der":false,"wr":[],"use":{"b":"rare","c":2,"ls":"2026-11-12T18:03:33"}},
{"n":"bootstrap_session","t":"T1","cat":"session","mut":false,"prot":false,"sess":false,"fg":"session","eff":"Điểm khám phá gọn cho lượt gọi đầu của một host session: bắt buộc có session_id và agent_id rõ ràng cùng một owner_claim_id entropy cao; ở lượt bootstrap đầu, nó lưu bền vững claim sở hữu task (owner_capability_sha256, owner_claim_sha256, trạng thái pending kèm hạn) vào dòng task_checkpoints của session-store, rồi trả owner_capability do server cấp cùng health, workflow đang chạy, danh sách key-tools gọn khoảng 30 tool, các bẫy thường gặp, chỉ mục skill và một ref ruleset bootstrap. Khi khôi phục (claim đã commit, cùng claim id), nó tái suy ra và trả capability mà không ghi. Từ chối claim id yếu hoặc thiếu, claim của owner khác, hoặc claim khôi phục lệch.","uc":"Một CA host mới gọi bootstrap_session(session_id, agent_id, owner_claim_id) một lần lúc mở session, giữ owner_capability được trả, rồi đưa nó cho begin_task ở mọi prompt thật của User.","cit":["code_tools.py:10845","code_tools.py:10862","code_tools.py:10912","code_tools.py:10917","code_tools.py:10924","orchestrator/session/store.py:2381"],"h":"code_tools.py:10845","wk":"none","der":false,"wr":[{"kind":"sqlite","target":"session-store task_checkpoints row (owner claim: owner_capability_sha256, owner_claim_sha256, pending + expiry)","condition":"first bootstrap with a new owner_claim_id; recovery of an existing committed claim writes nothing"}],"err":{"c":"i","r":"output-history.md:L143 (R52)","q":"first-bootstrap qualifier (R52)"},"use":{"b":"light","c":30,"ls":"2026-11-14T15:41:42"}},
{"n":"cache_status","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Báo sức khoẻ cache của project map: uỷ quyền cho surgical_mapper.get_cache_status (cache và index có tồn tại không, mtime cập nhật cuối, số file) và khi có index, chạy một lượt kiểm tra drift cache_delta đầy đủ, đánh dấu status stale kèm drift_warning cùng các danh sách added, modified, deleted đã cắt khi có gì lệch; thêm cache_age_hours và stale_warning khi index cũ hơn 6 giờ. Luôn trả ok=true khi bản thân phép kiểm đã chạy; độ stale nằm ở status, stale_warning và drift_warning, không nằm ở ok.","uc":"Khi bắt đầu làm việc trên một dự án, một CA gọi cache_status để quyết định map trong cache còn đủ tươi để tin hay phải refresh trước.","cit":["code_tools.py:2106","code_tools.py:2108","code_tools.py:2123","code_tools.py:2128","code_tools.py:2156","surgical_mapper.py:1775"],"h":"code_tools.py:2106","wk":"none","der":false,"wr":[],"use":{"b":"light","c":11,"ls":"2026-11-14T21:11:59"}},
{"n":"capability_status","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Báo daemon có những capability tuỳ chọn nào: một danh sách cố định (python_ast, tree_sitter_languages, yaml_parser, markdown, powershell_shell, sql_regex, playwright, psutil, pillow), mức khả dụng được thăm dò bằng import (module_available) hoặc một cờ của mapper, kèm gợi ý cài đặt. Với include nhận protected, optional hoặc missing, nó gom protected_tools_status, optional_tool_probe và suggest_missing_capabilities (tất cả đều chỉ đọc) vào cùng một response.","uc":"Khi một tính năng T4 hoặc tính năng parsing fail, một CA gọi capability_status để kiểm capability nền (playwright, psutil, tree-sitter) có được cài chưa trước khi debug tiếp.","cit":["code_tools.py:4366","code_tools.py:4376","code_tools.py:4382","code_tools.py:4391","code_tools.py:4394"],"h":"code_tools.py:4366","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"cbm_council_context","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Dựng một gói ngữ cảnh graph nhỏ và có khung cho council worker: các symbol quanh một symbol được nêu (mặc định depth 2), hoặc khi có file_path thì toàn bộ symbol trong file đó cộng callers trực tiếp. Fail-open và chỉ mang tính tư vấn: gói không bao giờ tự được tiêm vào; council leader phải tự nhúng nó vào prompt worker.","uc":"Một council leader chuẩn bị cho worker sửa bug trong một file gọi cbm_council_context(file_path='...') rồi dán khối context được trả vào prompt của worker để worker thấy các láng giềng của file đó.","cit":["orchestrator/graph/tools.py:1429","orchestrator/graph/tools.py:1445"],"h":"orchestrator/graph/tools.py:1429","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"cbm_dsm","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Dựng một trang thưa của ma trận cấu trúc phụ thuộc (DSM) từ một view phân tích chỉ đọc: dòng và cột được trang (mặc định 100 x 100) với ngữ nghĩa cạnh observed hoặc strict, tính hoàn toàn trong RAM. Tool không bao giờ ghi source hay graph DB (wrote:false).","uc":"Một agent muốn xem độ kết nối giữa các module thì hỏi cbm_dsm trang 100 x 100 quanh các module lõi để tìm những khối off-diagonal dày đặc.","cit":["orchestrator/graph/tools.py:1360","orchestrator/graph/tools.py:1404","orchestrator/graph/tools.py:1423"],"h":"orchestrator/graph/tools.py:1360","wk":"none","der":false,"wr":[],"use":{"b":"moderate","c":138,"ls":"2026-11-16T15:33:56"}},
{"n":"cbm_edges_query","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Duyệt các cạnh raw của graph suy diễn với bộ lọc (kind, source_contains, target_contains) và trang limit offset: đây là lớp liệt kê cạnh thấp nhất đứng sau các truy vấn template. Chỉ đọc.","uc":"Để audit mọi cạnh imports đi ra từ một module, một agent gọi cbm_edges_query(kind='imports', source_contains='orchestrator/core') rồi lật trang kết quả.","cit":["orchestrator/graph/tools.py:324","orchestrator/graph/tools.py:336"],"h":"orchestrator/graph/tools.py:324","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"cbm_evolution","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Đọc lịch sử git (chỉ gọi git qua argv, không bao giờ ghi) và báo các file được đưa vào đã tiến hoá thế nào trong một cửa sổ nhìn lại (mặc định 90 ngày, tối đa 3650), tuỳ chọn nhân trọng số bằng một map complexity do người gọi cung cấp; author ID được hash theo mặc định. Trả wrote:false.","uc":"Một agent cần xếp ưu tiên refactor gọi cbm_evolution(lookback_days=90) để tìm những file biến động nhiều nhất trong quý vừa rồi.","cit":["orchestrator/graph/tools.py:1314","orchestrator/graph/tools.py:1343","orchestrator/graph/tools.py:1354"],"h":"orchestrator/graph/tools.py:1314","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"cbm_graph_status","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Báo sức khoẻ graph code suy diễn của một dự án: graph có được bật và tươi không, số node và edge, trạng thái WAL và những file nào stale. Chỉ đọc. Thư mục dự án không tồn tại bị từ chối như lỗi của người gọi, chứ không bị báo thành một dự án hợp lệ nhưng chưa index.","uc":"Trước khi chạy một truy vấn graph, một agent kiểm cbm_graph_status xem graph còn tươi không; nếu có file stale được liệt kê thì re-index trước.","cit":["orchestrator/graph/tools.py:281","orchestrator/graph/tools.py:299"],"h":"orchestrator/graph/tools.py:281","wk":"none","der":false,"wr":[],"use":{"b":"moderate","c":267,"ls":"2026-11-16T15:33:46"}},
{"n":"cbm_nodes_query","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Duyệt các node raw của graph suy diễn với bộ lọc (kind, name_prefix, file_contains) và trang limit/offset: đây là lớp liệt kê node thấp nhất đứng sau các truy vấn template thân thiện hơn. Chỉ đọc.","uc":"Một agent cần mọi node class có tên bắt đầu bằng 'Tool' gọi cbm_nodes_query(kind='class', name_prefix='Tool') rồi lật qua các kết quả khớp.","cit":["orchestrator/graph/tools.py:304","orchestrator/graph/tools.py:316"],"h":"orchestrator/graph/tools.py:304","wk":"none","der":false,"wr":[],"use":{"b":"light","c":34,"ls":"2026-11-16T15:12:12"}},
{"n":"cbm_quality_gate","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Đánh giá các luật chất lượng code chống một view phân tích chỉ đọc, theo một trong ba mode: rules (đánh giá một bộ luật TOML), compare (so một snapshot mới với baseline JSON được đưa vào), hoặc snapshot (xuất snapshot metric dưới dạng JSON). Tool luôn trả wrote:false và không bao giờ tự ghi baseline; persist_hint của snapshot bảo lưu nó qua write_new_file. Một mode sai hoặc thiếu file luật trả về kết quả unavailable tường minh, không bao giờ báo pass.","uc":"Trong CI, một agent gọi cbm_quality_gate(mode='compare', baseline='<snapshot đã lưu>') để kiểm độ kết nối, chu trình và độ sâu có thoái hoá so với baseline gần nhất hay không.","cit":["orchestrator/graph/tools.py:1103","orchestrator/graph/tools.py:1126","orchestrator/graph/tools.py:1156","orchestrator/graph/tools.py:1186","orchestrator/graph/tools.py:1201"],"h":"orchestrator/graph/tools.py:1103","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"cbm_template_query","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Chạy một trong tám truy vấn graph có tên trên graph suy diễn: callers, callees, imports, imported_by, orphan_symbols, module_deps, entry_points, impact_scope, kèm bộ lọc name/node/module cộng depth, limit và offset. Chỉ đọc; đây là truy vấn cung cấp node id của graph cho cbm_trace_path.","uc":"Để tìm mọi thứ gọi write_fix, một agent gọi cbm_template_query(template='callers', name='write_fix') và nhận các dòng caller kèm node id của graph.","cit":["orchestrator/graph/tools.py:394","orchestrator/graph/tools.py:398","orchestrator/graph/tools.py:407"],"h":"orchestrator/graph/tools.py:394","wk":"none","der":false,"wr":[],"use":{"b":"rare","c":5,"ls":"2026-11-14T22:56:56"}},
{"n":"cbm_trace_path","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Đi trên graph code ra ngoài hoặc vào trong từ một node bắt đầu (mặc định depth 5, kèm trần node và timeout 2s, fail-open) rồi trả đường đi của các node và cạnh tìm thấy. Nếu start id không phải một node trong graph, nó fail loud với START_NODE_NOT_FOUND và bảo người gọi resolve một node id thật qua cbm_template_query(template='callers'); một tên symbol trần không được chấp nhận làm node id.","uc":"Một agent lần cách một request handler chạm tới database gọi cbm_trace_path từ node id của handler và nhận chuỗi lời gọi; một tên symbol gõ sai trả lỗi not-found rõ ràng thay vì một kết quả rỗng nhưng ok.","cit":["orchestrator/graph/tools.py:344","orchestrator/graph/tools.py:355","orchestrator/graph/tools.py:372"],"h":"orchestrator/graph/tools.py:344","wk":"none","der":false,"wr":[],"use":{"b":"light","c":54,"ls":"2026-11-16T15:12:16"}},
{"n":"cep_extension_status","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"deploy","eff":"Trả lời liệu Premiere sẽ nạp panel mới nhất: so hash file giữa repo và LIVE cho tập deploy đã chọn (same/diff/missing_in_live), parse CSXS/manifest.xml để kiểm hợp lệ, và đọc cờ PlayerDebugMode từ HKCU\\Software\\Adobe\\CSXS.6-14 (chỉ mở registry để đọc). Trả về in_sync, debug_mode_enabled và một ready_hint nêu đúng vật cản (thiếu thư mục LIVE, file đổi hoặc thiếu, manifest không hợp lệ, debug mode tắt).","uc":"Trước khi demo một thay đổi panel, một CA gọi cep_extension_status để xác nhận repo và LIVE đồng bộ, manifest hợp lệ và PlayerDebugMode bật - nếu không Premiere sẽ nạp một panel cũ hoặc không chữ ký.","cit":["code_tools.py:17163","code_tools.py:17172","code_tools.py:17181","code_tools.py:17193","code_tools.py:17205"],"h":"code_tools.py:17163","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"cep_manifest_validate","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"deploy","eff":"Thẩm định một CSXS/manifest.xml của CEP: bắt buộc project_path (gốc extension, mặc định CSXS/manifest.xml) hoặc manifest_path chỉ định rõ, fail loud khi file thiếu, và trả cấu trúc manifest đã parse (bundle id, sự tồn tại MainPath/ScriptPath theo từng extension, các host khai báo, trùng extension id, tính hợp lệ cộng lỗi) từ _cep_parse_manifest (chỉ ET.parse, không ghi).","uc":"Sau khi sửa manifest của một panel, một CA gọi cep_manifest_validate để bắt XML sai cú pháp hoặc MainPath trỏ vào file thiếu trước khi deploy.","cit":["code_tools.py:17148","code_tools.py:17151","code_tools.py:17154","code_tools.py:17157","code_tools.py:17075"],"h":"code_tools.py:17148","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"check_server_path","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Thăm dò một path có tới được không, tự nhận loại theo prefix: http(s):// nhận một request HEAD (mã status cộng 5 header đầu, TLS được kiểm trừ khi verify_tls=false), \\\\server (UNC) nhận một probe os.path.exists chạy trên thread kèm timeout, còn lại là kiểm tồn tại local cộng is_file/is_dir, kích thước và đọc/ghi được. Trả về reachable, mode_detected, latency_ms và một next action; không bao giờ fetch body và không bao giờ ghi.","uc":"Trước khi copy footage từ một network share, một CA gọi check_server_path trên path UNC để xác nhận share tới được (và phân biệt 'server tắt' với 'sai path') trước khi bắt đầu transfer.","cit":["code_tools.py:11252","code_tools.py:11300","code_tools.py:11319","code_tools.py:11338","code_tools.py:11347"],"h":"code_tools.py:11252","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"classify_ca_action","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Phân loại một action (tên tool, loại action, transport native hay via_a, scope, target, project, agent) chống ma trận chính sách tool của CA (.council/CA_TOOL_POLICY_MATRIX.json) qua classify_action, rồi trả các trường máy kiểm được: task_class, native_policy, classification, cờ forbidden/allowed native, cờ via_a, host gate và đường ToolA được ưa. Tool chỉ phân loại; không bao giờ thực thi action đang được phân loại.","uc":"Trước khi chạy một lệnh move bằng PowerShell native, một CA gọi classify_ca_action để kiểm ma trận có cấm làm native không và nên dùng tool ToolA nào thay thế.","cit":["tools_v15.py:9805","tools_v15.py:9814","orchestrator/core/ca_policy_matrix.py:436","orchestrator/core/ca_policy_matrix.py:466","orchestrator/core/ca_policy_matrix.py:594","orchestrator/core/ca_policy_matrix.py:603"],"h":"tools_v15.py:9794","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"classify_get_footage_outcome","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Bộ phân loại kết quả sau get-footage: đọc phần đuôi (50 KB cuối) của orchestrator.log (hoặc log_excerpt được đưa vào), match với các họ pattern folder_picker_fail, source_unc_fail và copy_runtime_fail, đếm file tạo ra trong output_folder qua folder_stats, rồi trả outcome thuộc {success, folder_picker_fail, source_unc_fail, copy_runtime_fail, unknown} kèm trích log, files_created và 5 match lỗi đầu tiên cộng next action theo từng outcome. Success đòi hỏi có file được tạo VÀ không có ERROR/EXCEPTION/TRACEBACK trong log.","uc":"Sau một lượt chạy get-footage, một CA gọi classify_get_footage_outcome kèm thư mục xuất để biết bản copy thành công hay fail ở folder picker, ở source UNC, hay ở chính lượt copy - thay vì đọc lại cả log.","cit":["code_tools.py:14417","code_tools.py:14439","code_tools.py:14450","code_tools.py:14459","code_tools.py:14471"],"h":"code_tools.py:14417","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"convene_council","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"council","eff":"Trả protocol council chỉ-dành-cho-CLI cho các receiver được trigger, dựng LIVE từ SOT .council/council_config.json (roster, route capability, luật coordination/parallelism, mission, enum messaging, luật guardrail harness, task routing) hoặc từ các fallback hardcode khi config thiếu hoặc hỏng, cộng format message và các luật addressing/verify/leadership/frugality/worker-prompt/broker/liveness/dispatch cùng text skill của council leader. Tool mô tả protocol; không dispatch gì và không ghi gì.","uc":"Khi Anh trigger council ('bat hoi dong tr'), CA nhận gọi convene_council để lấy roster và luật routing live trước khi dispatch bất kỳ council worker nào.","cit":["code_tools.py:19534","code_tools.py:19541","code_tools.py:19543","code_tools.py:19549","code_tools.py:19455"],"h":"code_tools.py:19534","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"council_config_diff","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"council","eff":"Kiểm drift CHỈ ĐỌC giữa council config SOT và ba đích rule-doc suy ra (examples/CORE_RULE.txt, .council/council_rule_leader.md, .council/global_rule_worker.md): xác nhận mọi label agent đang enabled có mặt, không còn label đã retire (match theo word-boundary), và các marker no-cost-chain, CodoC-không-phải-leader-mặc-định, ClauC-exception tồn tại; CORE_RULE được kiểm theo SOT pointer thay thế. Trả drift theo từng đích, roster đang enabled và một summary ('N drift / 3 doc targets'). Không ghi gì; chỉ nêu cái nào cần cập nhật.","uc":"Sau một lần đổi roster council, một CA gọi council_config_diff để tìm rule doc suy ra nào vẫn nêu một agent đã retire hoặc thiếu một routing marker, trước khi sync chúng.","cit":["code_tools.py:19635","code_tools.py:19638","code_tools.py:19648","code_tools.py:19654","code_tools.py:19660"],"h":"code_tools.py:19635","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"council_cron_job_list","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"council","eff":"List/get chỉ đọc các cron job council bền vững và lịch sử chạy của chúng từ SQLite council_cron.db. Có job_id thì trả đúng job đó (loại job đã xoá) kèm các lần chạy; không có thì trả toàn bộ job chưa xoá kèm các lần chạy gần đây trên mọi job. Trần số lần chạy được clamp 1..200 bên trong list_runs. Tool không ghi gì và không bao giờ start, stop hay fire một job.","uc":"Trước khi sửa một scheduled council job, leader liệt kê mọi job và kiểm các lần chạy cuối của một job để xem nó thành công hay thất bại.","cit":["orchestrator_server.py:12671","orchestrator_server.py:12674","orchestrator/council/cron_jobs.py:300","orchestrator/council/cron_jobs.py:347"],"h":"orchestrator_server.py:12671","wk":"none","der":false,"wr":[],"note":"Tier-split anomaly (R21): T1 while its council_cron_job_* siblings are T3 — unexplained, carried as an anomaly.","use":{"b":"rare","c":4,"ls":"2026-11-11T03:43:54"}},
{"n":"council_message","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"council","eff":"Gửi một message tới một council worker khác (hoặc tới User) qua dispatch core dùng chung và mặc định chờ đồng bộ câu trả lời (timeout_sec mặc định 6000). Message đến từ đúng một nguồn: text inline, hoặc một file .council/briefs/*.md hay *.txt nằm trong allowlist (chọn đúng một trong hai; mọi thứ khác bị từ chối trước khi dispatch). Metadata routing người gọi cung cấp (TASK_ID, OBJECTIVE_CLASS, RISK_LEVEL, DEV_LEVEL) được prepend thành các dòng KEY: value trừ khi đã có. Với User, tool chỉ post lên broker cho dashboard người và không chạy agent nào. Với worker, tool enqueue/chạy worker đó và trả text reply. Các trường hợp từ chối: nghi mojibake, thiếu text, target không rõ hoặc đang tắt, tự nhắn cho chính mình, và model của target chưa đặt. Với queue=true (mặc định) tool xếp hàng khi target bận; async_dispatch=true giao dispatch cho /api/council/send-async và trả ack ngay, còn reply rơi vào .council/broker_filtered.jsonl.","uc":"Leader yêu cầu worker codo audit một file và chờ reply trong cùng lượt gọi; hoặc post một note tới User xuất hiện trên council dashboard mà không chạy agent nào.","cit":["orchestrator_server.py:12923","orchestrator_server.py:12947","orchestrator_server.py:12182","orchestrator_server.py:12215","orchestrator_server.py:12259"],"h":"orchestrator_server.py:12923","wk":"none","der":false,"wr":[{"kind":"file","target":"dispatch ledger row (.council/dispatch_ledger.jsonl) + broker event file"}],"err":{"c":"i","r":"output-history.md:L112 (R21)","q":"broker/ledger appends"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"council_message_async","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"council","eff":"Dispatch council không chặn: tool thẩm định và nhận message, rồi trả ngay {ok, dispatched, request_id} và chạy worker trong một background task; không bao giờ chờ hay trả reply. Trước khi nhận, nó từ chối target không rõ, sender bằng target, và target chưa cấu hình model (WORKER_MODEL_NOT_SET, kèm cách sửa: đặt model trước qua council_worker_model). Model được resolve trước khi ack nên một model không sẵn sàng fail loud thay vì biến mất trong background task. Một request_id lặp lại bị từ chối như bản sao thay vì xếp lịch công việc hai lần. Background task enqueue/chạy worker và ghi kết quả cuối; reply rơi vào .council/broker_filtered.jsonl, theo dõi qua council_status hoặc tracker.","uc":"Leader toả cùng một task cho ba worker song song, thu một request_id cho mỗi cái, rồi poll council_status theo từng id thay vì giữ ba HTTP connection mở.","cit":["orchestrator_server.py:13009","orchestrator_server.py:13036","orchestrator_server.py:13056","orchestrator_server.py:13078","orchestrator_server.py:13081"],"h":"orchestrator_server.py:13009","wk":"none","der":false,"wr":[{"kind":"file","target":"accepted-dispatch ledger row (.council/dispatch_ledger.jsonl); reply recorded to broker file (.council/broker_filtered.jsonl)"}],"err":{"c":"i","r":"output-history.md:L112 (R21)","q":"broker/ledger appends"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"council_route_scorecard","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"council","eff":"Scorecard đo routing trên bằng chứng dispatch council: đọc .council/route_measurements.jsonl, dựng scorecard (theo từng bucket harness/model/objective/risk: số mẫu, tỷ lệ completion/timeout/artifact, duration p50/p95, quality proxy, confidence, recommendation, capacity dạng số; không bao giờ bịa capacity), và - theo MẶC ĐỊNH (write_artifacts=true) - ghi scorecard vào .council/route_scorecard.json và .council/route_scorecard.md qua _route_write_scorecard_files. Chỉ một write_artifacts=false tường minh mới làm lượt gọi thành chỉ đọc.","uc":"Sau một lô dispatch council, một CA gọi council_route_scorecard để lấy scorecard theo từng route (harness/model nào thật sự hoàn thành công việc) cùng artifact JSON/Markdown đã ghi cho hồ sơ.","cit":["code_tools.py:19709","code_tools.py:19714","code_tools.py:19718","code_tools.py:19719","orchestrator/council/route_metrics.py:235","orchestrator/council/route_metrics.py:245"],"h":"code_tools.py:19709","wk":"none","der":false,"wr":[{"kind":"file","target":".council/route_scorecard.json","condition":"write_artifacts=true (default); pass write_artifacts=false for a read-only call"},{"kind":"file","target":".council/route_scorecard.md","condition":"write_artifacts=true (default); pass write_artifacts=false for a read-only call"}],"err":{"c":"i","r":"output-history.md:L148 (R57)"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"council_status","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"council","eff":"Truy vấn trạng thái tất định chỉ đọc cho một dispatch_id (run-<uuid>). Thiếu dispatch_id bị từ chối (MISSING_DISPATCH_ID). Ngược lại nó suy trạng thái trong một worker thread từ broker files, runtime state và durable queue, trả một trong dispatching, working, done hoặc stuck (kèm stuck_reason), cộng worker_pid_alive và các cờ dispatch/queued. Khi không có chút bằng chứng dispatch nào, nó trả ok:false với DISPATCH_NOT_FOUND. Tool không bao giờ huỷ gì; runtime monitor vẫn là chủ sở hữu huỷ duy nhất.","uc":"Sau khi council_message_async trả một request_id, poll council_status với id đó để biết dispatch còn working, đã done hay đang stuck - mà không làm phiền lượt chạy.","cit":["orchestrator_server.py:13521","orchestrator_server.py:13532","orchestrator_server.py:13509","orchestrator_server.py:13480"],"h":"orchestrator_server.py:13521","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"council_tracker","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"council","eff":"Chạy bộ thu live-log council và trả snapshot: theo MẶC ĐỊNH (save_output=true) nó spawn COUNCIL_TRACKER.py làm subprocess (timeout 120s, headless), archive file .council/Live_council/council_live.txt trước đó vào archives/ dưới dạng zip gắn timestamp rồi xoá nó, và ghi một snapshot mới (các dispatch broker + prompt đầy đủ, ARGV/output thô của worker, council_events, delta số dòng, đuôi inbox/outbox, runtime_state, các reply cross-agent mới nhất); handler sau đó đọc file mới và trả output_path, archive_created, bytes, line_count cùng một trích đoạn đuôi có giới hạn (hoặc toàn bộ nội dung chỉ khi return_full=true VÀ diagnostic_full_prompts=true). Với save_output=false nó chỉ đọc snapshot hiện tại. open_file được chấp nhận nhưng bị bỏ qua (không bao giờ mở cửa sổ). Dùng _COUNCIL_TRACKER_ROOT hardcode (:19728) và không nhận tham số project_path.","uc":"Khi điều tra một dispatch council thất bại, một CA gọi council_tracker trước để lấy snapshot một-file tươi của toàn bộ traffic council thay vì tự cào log .council bằng tay.","cit":["code_tools.py:19735","code_tools.py:19781","code_tools.py:19800","code_tools.py:19817","COUNCIL_TRACKER.py:366","COUNCIL_TRACKER.py:374"],"h":"code_tools.py:19735","wk":"none","der":false,"wr":[{"kind":"file","target":".council/Live_council/council_live.txt (fresh snapshot)","condition":"save_output=true (default)"},{"kind":"file","target":".council/Live_council/archives/council_live_<timestamp>.zip (previous snapshot archived)","condition":"save_output=true (default) and a previous snapshot existed"},{"kind":"file","target":".council/Live_council/council_live.txt (previous snapshot, after archiving)","condition":"save_output=true (default) and a previous snapshot existed"},{"kind":"process","target":"COUNCIL_TRACKER.py subprocess (120s timeout)","condition":"save_output=true (default)"}],"err":{"c":"i","r":"output-history.md:L148 (R57)"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"council_worker_model","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"council","eff":"Lấy, đặt, hoặc liệt kê model của một council worker mà không cần dashboard UI hay Playwright DOM. Không có action thì nó set khi có agent+model, list khi chỉ có agent, và get trong các trường hợp còn lại. Get trả config hiện tại cộng model/provider đã resolve và danh sách id hợp lệ được tuyển chọn; list trả các model id hợp lệ được tuyển chọn cho worker đó, đọc từ .council/ID_MODELS/<PROVIDER>.txt. Set thẩm định model chống danh sách tuyển chọn đó và từ chối cấu hình sai, rồi ghi .council/worker_models.json dưới một lock; model mới áp dụng từ dispatch kế tiếp, không cần restart. Agent lạ bị từ chối với INVALID_AGENT kèm roster được phép.","uc":"Leader đổi worker codo sang một model id mới được pin, rồi gọi action=get để xác nhận thay đổi có hiệu lực trước khi dispatch việc cho nó.","cit":["orchestrator_server.py:13659","orchestrator_server.py:13688","orchestrator_server.py:13706","orchestrator_server.py:13716","orchestrator_server.py:13721"],"h":"orchestrator_server.py:13659","wk":"none","der":false,"wr":[{"kind":"file","target":"config file .council/worker_models.json","condition":"set only"}],"err":{"c":"i","r":"output-history.md:L112 (R21)"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"declare_mission","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"mission","eff":"Tuyển chọn mission lúc mở session: tokenize text mission cộng các gợi ý ngữ cảnh, chấm điểm phần đầu SKILL.md của mọi skill (2000 ký tự đầu) và mọi mô tả TOOL_SCHEMAS theo độ chồng lấp từ khoá (kiểu cosine, sàn skill 0.15, sàn tool 0.10), force-boost các skill và tool audit khi khớp cụm từ KTR, rồi trả các skill và tool liên quan đã xếp hạng, danh sách bỏ qua các skill không liên quan, một trình tự khuyến nghị, và một mission_id. Phần tuyển chọn được pin trong dict _SESSION_MISSION[session_id] trong RAM khi project_path resolve ra một session đang hoạt động; nó KHÔNG ghi missions.db.","uc":"Lúc mở session một CA khai 'test Electron Rawcut UI' và nhận về 3-5 skill liên quan cùng 20 tool liên quan để đọc, thay vì đọc cả 23 skill.","cit":["tools_v15.py:5045","tools_v15.py:5082","tools_v15.py:5129","tools_v15.py:5147","tools_v15.py:5178","tools_v15.py:5190","tools_v15.py:5197"],"h":"tools_v15.py:5017","wk":"none","der":false,"wr":[],"note":"Does NOT inherit the Batch 3 mission errata. MCP proxy note: dispatch goes through mcp_stdio.py:2066 (R38f).","use":{"b":"unused","c":0,"ls":null}},
{"n":"describe_page","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"desktop","eff":"Bản đồ cấu trúc DOM qua một lượt JavaScript evaluate chỉ đọc: liệt kê các section có tiêu đề trong log-area kèm số body, id và tag, liệt kê các textarea view-* kèm độ dài và độ hiển thị, và đếm tổng textarea, input, button. Khi auto_artifact=true và response đã serialize vượt max_bytes (mặc định 4096), toàn bộ response được ghi ra một file artifact và chỉ phần cốt yếu cùng đường dẫn artifact được trả về.","uc":"Một CA gọi describe_page trên một trang nặng log để lập bản đồ các section và panel view của nó, rồi chọn một tiêu đề section để đưa cho extract_log_section.","cit":["tools_v15.py:4182","tools_v15.py:4199","tools_v15.py:4203","tools_v15.py:178","tools_v15.py:196","tools_v15.py:202","tools_v15.py:203"],"h":"tools_v15.py:4119","wk":"none","der":false,"wr":[{"kind":"file","target":"<project>/.orchestrator_cache/artifacts/<round_id>/describe_page_response.json — only when auto_artifact=true AND serialized response > max_bytes AND project_path exists"}],"err":{"c":"ii","r":"output-history.md:L126 (R35)","q":"opt-in write; registry scope none admits no opt-in write"},"use":{"b":"light","c":25,"ls":"2026-11-14T09:40:21"}},
{"n":"dom_state_diff","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"desktop","eff":"Chụp một snapshot DOM được Playwright evaluate của một phần tử (các trường disabled/bg/color/className/visible/text/value/rect) rồi hoặc lưu nó làm baseline hoặc diff nó với một baseline đã lưu. Không có baseline_id thì nó cache snapshot vào dict _DOM_SNAPSHOTS trong process (TTL 600s, entry hết hạn bị dọn khi vào) và trả snapshot_id; có baseline_id thì nó trả changed=true/false cộng giá trị before/after theo từng trường. Bản thân trang không bao giờ bị sửa — biểu thức evaluate chỉ đọc computed style và text. Chế độ diff không lưu snapshot mới; các lần diff lặp lại so với cùng baseline đó cho tới khi TTL 600s hết hạn.","uc":"Sau khi bấm một nút submit, một CA chụp snapshot nó trước (chế độ baseline), bấm, rồi gọi lại dom_state_diff với baseline_id để chứng minh nút đã thành disabled mà không phải poll DOM thô.","cit":["tools_v15.py:3524","tools_v15.py:3541","tools_v15.py:3546","tools_v15.py:3557","tools_v15.py:3573","tools_v15.py:3580"],"h":"tools_v15.py:3524","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"extract_log_section","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Trích text của một section log DOM theo tiêu đề: một lượt Playwright evaluate tìm heading khớp section_title, đi lên ancestor gần nhất có area_class, và đọc nội dung textarea/pre/log-body, chọn body tốt nhất theo chiến lược smart/longest/first. Các mode full/ref/preview đánh đổi nội dung lấy token (ref chỉ trả tiền tố sha256 + preview). Mọi lượt gọi cũng cache toàn bộ nội dung hiện tại vào _LOG_BASELINE_CACHE trong RAM để một lượt gọi sau với baseline_sha256 có thể chỉ báo phần đuôi mới.","uc":"Trong một vòng test, một CA gọi extract_log_section(page_id='pg1', section_title='System Log', mode='ref') mỗi phút để kiểm rẻ xem hash log có đổi không, rồi chỉ lấy nội dung đầy đủ khi có đổi.","cit":["tools_v15.py:3627","tools_v15.py:3708","tools_v15.py:3783","tools_v15.py:3617","tools_v15.py:3784"],"h":"tools_v15.py:3627","wk":"none","der":false,"wr":[],"use":{"b":"rare","c":2,"ls":"2026-11-10T17:15:31"}},
{"n":"fetch_sheet_csv_summary","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Fetch bản xuất CSV của Google Sheets qua webfetch (trần 2.000.000 ký tự, timeout 15s, mặc định kiểm TLS), parse tối đa 5000 dòng, phân loại các ô thành path UNC / HTTP / ổ-đĩa-chữ-cái, loại các dòng header (ô có hậu tố STT và cả dòng có >=2 token nhãn cột), đếm các thư mục cha UNC (top 10), và trả số dòng cộng mẫu path UNC và N dòng dữ liệu đầu/cuối. Parse thuần trong RAM; không ghi file hay DB ở bất kỳ đâu trong thân hàm.","uc":"Trong một lượt audit sheet footage, một CA fetch CSV của sheet một lần và nhận số đếm thư mục cha UNC cùng mẫu path, thay vì tải và quét cả sheet bằng tay.","cit":["code_tools.py:11597","code_tools.py:11626","code_tools.py:11639","code_tools.py:11701","code_tools.py:11760"],"h":"code_tools.py:11597","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"file_profile","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Trả metadata loại/kích thước/encoding/số dòng/binary-hay-text/ngôn ngữ của một file mà KHÔNG đổ nội dung của nó: stat cho size và mtime, 8192 byte đầu để dò binary (byte NUL) và đoán encoding (UTF-8, lùi về latin-1), và đếm dòng đầy đủ cho file tới 20 MB (trên cỡ đó thì ước lượng từ mẫu đầu file). Thuần đọc: file được mở chỉ-đọc ở chế độ binary và không ghi gì ở đâu. Registry mutating=false là ĐÚNG.","uc":"Trước khi mở một file lạ cỡ lớn, một CA gọi file_profile để biết nó binary hay text, to cỡ nào, và khoảng bao nhiêu dòng, để chọn chiến lược đọc có giới hạn thay vì đổ cả file.","cit":["tools_upgrade_p4.py:114","tools_upgrade_p4.py:124","tools_upgrade_p4.py:126","tools_upgrade_p4.py:141"],"h":"tools_upgrade_p4.py:114","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"find_callers","t":"T1","cat":"search","mut":false,"prot":false,"sess":false,"fg":"search","eff":"Alias đã deprecated của search_code, trả các call site của một symbol được nêu tên: dispatcher mặc định mode của nó thành 'callers', và handler dùng chung route mode='callers' tới find_symbol với include='callers'. Làn callers thăm độ tươi của index trước và trả STALE_INDEX thay vì tự refresh khi index snapshot project-map đã drift.","uc":"Trước khi đổi một helper dùng chung, một CA liệt kê mọi call site để phán đoán bán kính ảnh hưởng. (plausible-but-unverified)","cit":["orchestrator/tools/search.py:1657","orchestrator/tools/search.py:1667","orchestrator_server.py:3927","orchestrator/fileops/common.py:386"],"h":"orchestrator/tools/search.py:1575","wk":"none","der":false,"wr":[],"dep":true,"use":{"b":"unused","c":0,"ls":null}},
{"n":"find_definition_and_usage","t":"T1","cat":"search","mut":false,"prot":false,"sess":false,"fg":"search","eff":"Alias đã deprecated của search_code, trả cả định nghĩa lẫn mọi usage của một symbol được nêu tên: dispatcher mặc định mode của nó thành 'definition_usage', và handler dùng chung route nó tới find_symbol với include='all'. Làn usage thăm độ tươi của index trước và trả STALE_INDEX thay vì tự refresh khi index snapshot project-map đã drift.","uc":"Trước khi đổi tên một symbol, một CA lấy định nghĩa cộng mọi usage trong một lượt gọi để viết lại từng tham chiếu. (plausible-but-unverified)","cit":["orchestrator/tools/search.py:1660","orchestrator/tools/search.py:1667","orchestrator_server.py:3928","orchestrator/fileops/common.py:386"],"h":"orchestrator/tools/search.py:1575","wk":"none","der":false,"wr":[],"dep":true,"use":{"b":"unused","c":0,"ls":null}},
{"n":"find_symbol","t":"T1","cat":"search","mut":false,"prot":false,"sess":false,"fg":"search","eff":"Alias của search_code, trả định nghĩa của một symbol: dispatcher mặc định mode của nó thành 'symbol', và bản thân handler cũng tự nhận dạng hình dispatch alias (query đến qua name/symbol/symbol_name mà không có mode) rồi nâng nó lên 'symbol', sau đó route tới find_symbol với include='def'. Làn def trả lời từ một symbol index — DB graph code suy diễn CBM khi tươi, nếu không thì các map functions/classes của INDEX.json — chứ không từ một lượt quét text toàn repo, và đóng dấu mọi kết quả 0-hit bằng stale_index kèm lý do.","uc":"Một CA định vị định nghĩa của một tên hàm để nhảy thẳng tới file và dòng của nó trước khi đọc. (plausible-but-unverified)","cit":["orchestrator/tools/search.py:1634","orchestrator/tools/search.py:1667","orchestrator/tools/search.py:364","orchestrator_server.py:3937"],"h":"orchestrator/tools/search.py:1575","wk":"none","der":false,"wr":[],"use":{"b":"light","c":39,"ls":"2026-11-16T13:01:59"}},
{"n":"folder_dialog_snapshot","t":"T1","cat":"desktop","mut":false,"prot":false,"sess":false,"fg":"desktop","eff":"Khám phá chỉ-đọc các dialog chọn thư mục/file đang mở, để dùng TRƯỚC fill_folder_dialog: uỷ quyền cho tools_v15.desktop_window_list, rồi lọc trong RAM theo regex tiêu đề (mặc định Select/Browse/Choose/Open/Mo/Chon + Folder/thu muc/File) VÀ class dialog thật (#32770 / FileDialog / DirectUIHWND / Browse.*Folder / CtrlNotifySink), cộng các bộ lọc owner_pid / parent_hwnd / launched_after_ts; phân loại theo số Edit/Button con và loại picker (classic_win32_with_edit vs modern_ifiledialog_no_edit). Cửa sổ chỉ khớp tiêu đề được báo riêng như nhiễu (other_windows), không bao giờ như candidate. Thuần đọc.","uc":"Trước khi điền một folder picker, một CA chụp snapshot các dialog đang mở để xác nhận đúng một candidate khớp PID của app, rồi đưa owner_pid đó cho fill_folder_dialog thay vì đoán.","cit":["code_tools.py:8661","code_tools.py:8666","orchestrator/runtime/desktop_dialog_controls.py:33","orchestrator/runtime/desktop_dialog_controls.py:56","orchestrator/runtime/desktop_dialog_controls.py:67"],"h":"code_tools.py:8661","wk":"none","der":false,"wr":[],"use":{"b":"rare","c":4,"ls":"2026-11-10T17:24:10"}},
{"n":"folder_diff","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"So các entry cấp cao nhất của hai thư mục (không đệ quy): compare_by name (chỉ tên), name+size (tên + cỡ file), hoặc name+sha (tên + sha256 cắt còn 16 hex, đường chậm opt-in). Bỏ qua venv/.venv/node_modules/.git/__pycache__/.orchestrator_cache cộng ignore_patterns của người gọi (khớp substring). Trả only_in_a / only_in_b / in_both / mismatched / matched_count / identical_top_level. Thuần đọc.","uc":"Sau khi copy một thư mục dự án sang ổ backup, một CA chạy folder_diff với compare_by=name+sha để chứng minh cấp cao nhất giống hệt từng byte trước khi xoá nguồn.","cit":["code_tools.py:8523","code_tools.py:8537","code_tools.py:8558","code_tools.py:8582","code_tools.py:8603"],"h":"code_tools.py:8523","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"folder_exists","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Probe tồn tại + loại thư mục tối giản: resolve path và trả exists / is_dir / is_file / is_symlink; path không tồn tại trả ok:true với exists:false (không phải lỗi). Fail bất ngờ trả ok:false với FOLDER_EXISTS_FAILED. Thuần đọc.","uc":"Trước khi ghi vào một thư mục, một CA probe folder_exists để phân biệt 'thiếu thư mục' (tạo nó) với 'path là một file' (sai đích) mà không chạm đĩa.","cit":["code_tools.py:8341","code_tools.py:8344","code_tools.py:8347","code_tools.py:8355"],"h":"code_tools.py:8341","wk":"none","der":false,"wr":[],"note":"Copy-drift hazard (R60i): desktop_dialog_controls.py:10 is a verbatim runtime copy, not called by the handler.","use":{"b":"unused","c":0,"ls":null}},
{"n":"folder_stats","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Thống kê thư mục: số file, số thư mục con, tổng byte (+MB), mtime mới nhất (+ISO), top 8 đuôi mở rộng. Mặc định không đệ quy (khớp hành vi dir); recursive=true đi cả cây. Bỏ qua venv/.venv/node_modules/.git/__pycache__/.orchestrator_cache cộng ignore_patterns của người gọi (khớp substring). Khi không đệ quy mà file_count=0 nhưng dir_count>0, nó thêm subdirs_present_hint (file nhiều khả năng nằm trong thư mục con — gọi lại với recursive=true). Lượt quét chạy trong làn executor read_io. Thuần đọc.","uc":"Một CA kiểm folder_stats trên một thư mục xuất, thấy file_count=0 kèm subdirs_present_hint, và gọi lại với recursive=true thay vì kết luận sai rằng thư mục trống.","cit":["code_tools.py:8438","code_tools.py:8467","code_tools.py:8470","code_tools.py:8512","code_tools.py:8520"],"h":"code_tools.py:8438","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"get_file_symbols","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Liệt kê các symbol cấp cao nhất của một file bằng cách đọc nó trực tiếp (không qua index): file Python được parse bằng ast (hàm và class kèm số dòng), file JS/TS được quét từng dòng bằng regex (function/class/arrow-function/variable/method), và mọi đuôi khác trả kind=unsupported_symbols với count 0. File thiếu và lỗi cú pháp Python được trả như JSON lỗi, không phải exception.","uc":"Trước khi đọc một module lớn, một CA liệt kê các hàm và class của nó để chọn range nào cần đọc. (plausible-but-unverified)","cit":["orchestrator/tools/read/symbols.py:39","orchestrator/tools/read/symbols.py:42","orchestrator/tools/read/symbols.py:50","orchestrator/tools/read/symbols.py:57"],"h":"orchestrator/tools/read/symbols.py:12","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"get_folder_size","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Đếm một cây thư mục bằng os.walk trong làn executor read_io: tổng file, tổng byte (+MB), số thư mục bị bỏ qua, số file không đọc được. Các loại trừ mặc định an toàn (venv, venv312, .venv, node_modules, .git, __pycache__, .orchestrator_cache) được cắt sớm trừ khi include_skipped=true. Thuần đọc.","uc":"Trước một lượt backup, một CA đo get_folder_size trên gốc dự án (các loại trừ mặc định an toàn) để quyết định bản backup có vừa ổ đích không.","cit":["code_tools.py:8282","code_tools.py:8303","code_tools.py:8313","code_tools.py:8322","code_tools.py:8331"],"h":"code_tools.py:8282","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"get_project_summary","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Tổng quan dự mục nhanh, đọc từ index snapshot project-map INDEX.json: số file/LOC theo từng nhóm, top-5 hotspot độ phức tạp, các entrypoint, generated_at và thời điểm ghi cuối, cộng cỡ thư mục và các UI asset ngoài từ một lượt quét filesystem nằm ngoài event loop có cache TTL. Trả một lỗi ('INDEX not found. Run generate_project_map first.') khi không có index. Thuần đọc; lượt quét cỡ được cache trong _project_size_cache RAM (TTL 300s).","uc":"Lần đầu chạm một dự án lạ, một CA gọi get_project_summary để lấy các nhóm, hotspot và entrypoint từ index có sẵn mà không phải quét repo.","cit":["code_tools.py:7586","code_tools.py:7589","code_tools.py:7603","code_tools.py:7617","code_tools.py:7506"],"h":"code_tools.py:7586","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"get_rule_core_text","t":"T1","cat":"mcp,read","mut":false,"prot":false,"sess":false,"fg":"session","eff":"Trả toàn bộ text CORE_RULE ở lượt fetch đầu tiên của mỗi agent trong một TTL (6h, ORCHESTRATOR_RULE_CORE_TTL_SECONDS), mở một bootstrap epoch và gắn kèm bản đồ tool_purposes; các lượt gọi lặp trong cùng TTL trả một stub already_fetched tí hon (text bị lược bỏ có chủ đích, kèm sha256 + bootstrap_epoch). force=true hoặc một agent_id khác sẽ fetch lại. Trạng thái duy nhất là dict _RULE_CORE_FETCH_CACHE trong RAM. Thuần đọc.","uc":"Lúc mở session một CA fetch get_rule_core_text một lần để nạp core rule + bản đồ purpose; các lượt gọi sau trong cùng process nhận stub already_fetched và tái sử dụng text đã có trong context.","cit":["code_tools.py:2844","code_tools.py:2853","code_tools.py:2886","code_tools.py:2892","code_tools.py:2901"],"h":"code_tools.py:2844","wk":"none","der":false,"wr":[],"use":{"b":"moderate","c":240,"ls":"2026-11-16T14:09:44"}},
{"n":"get_session_events","t":"T1","cat":"read,session","mut":false,"prot":false,"sess":false,"fg":"session","eff":"Trả các event có phân trang của MỘT session: SELECT từ sessions + events (ORDER BY id ASC, LIMIT/OFFSET) kèm số đếm badge tổng hợp theo từng loại action. target_session_id thắng session_id; một lượt gọi chỉ có project_path sẽ resolve session id đang hoạt động trước. Thuần đọc SQLite chạy ngoài event loop. Số lần dùng là UNKNOWN — tool này nằm trong _SKIP_SESSION_LOG nên không con số usage nào đượcclaim cho nó.","uc":"Để lần theo một session đã làm gì, một CA liệt kê các session, rồi lật trang get_session_events cho session_id đó để xem từng lượt gọi tool, đích đến, thời lượng và kết quả.","cit":["code_tools.py:16783","code_tools.py:16788","code_tools.py:16808","code_tools.py:16816","code_tools.py:16832"],"h":"code_tools.py:16783","wk":"none","der":false,"wr":[],"use":{"b":"unknown","c":null,"ls":null}},
{"n":"get_skill_state","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Liệt kê các skill có trên đĩa bằng cách quét .ToolA_Skill/skills/ tìm các thư mục chứa SKILL.md, trả tên của mỗi skill, đường dẫn tương đối/tuyệt đối, kích thước, và một payload read_file_range dán được ngay. Bất chấp tên gọi và các tham số session_id/project_path, thân hàm không bao giờ tham chiếu bất kỳ trạng thái đã-nạp nào theo session — nó là một heuristic liệt kê filesystem, và chính docstring nói việc theo dõi thật theo session được kế hoạch hoá (R+1) chứ chưa cài đặt.","uc":"Một CA cần text của một skill gọi get_skill_state() để lấy đúng payload read_file_range cho file skill thay vì đoán đường dẫn của nó.","cit":["tools_v15.py:9683","tools_v15.py:9697","tools_v15.py:9701","tools_v15.py:9724"],"h":"tools_v15.py:9683","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"get_task_checkpoint","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Đọc một task checkpoint (theo session_id, mặc định 'default') qua _task_checkpoint_load_with_meta và trả các trường công khai của nó sau khi đã cắt bỏ các trường bí mật của owner (owner_capability_sha256, owner_identity_id, owner_claim_sha256, owner_claim_status, owner_claim_expires_at, các trường heartbeat, delegation_capability_sha256). include_owner_status=true thêm owner_status cộng một snapshot hoạt động CA từ sổ session. Checkpoint thiếu trả TASK_CHECKPOINT_MISSING (kèm owner_status unowned khi include_owner_status=true). Thuần đọc; không có con số usage nào được đưa vào effect.","uc":"Để kiểm một session đã có task đang chạy và ai sở hữu nó, một CA đọc get_task_checkpoint với include_owner_status=true trước khi bắt việc mới.","cit":["code_tools.py:10756","code_tools.py:10759","code_tools.py:10793","code_tools.py:10803","code_tools.py:10765"],"h":"code_tools.py:10756","wk":"none","der":false,"wr":[],"use":{"b":"rare","c":1,"ls":"2026-11-08T16:59:19"}},
{"n":"get_token_meter","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Ước lượng token/byte đã dùng của một session bằng cách đếm các dòng của nó trong bảng events của sessions.db (resolve session đang hoạt động theo project_path khi không đưa session_id, và fail tường minh khi có nhiều session đang hoạt động khớp). Byte là ước lượng — 1200 byte vào + 300 byte ra mỗi lượt gọi — không phải đo trực tiếp; response nói rõ điều đó và ghi chú rằng đo byte trực tiếp cần instrument middleware (kế hoạch R+1).","uc":"Giữa một task, CA gọi get_token_meter(project_path='D:/.../Rawcut') để xem session đang hoạt động đã gọi bao nhiêu lượt và con số đó tương đương khoảng bao nhiêu kilobyte.","cit":["tools_v15.py:9599","tools_v15.py:9617","tools_v15.py:9652","tools_v15.py:9657","tools_v15.py:9661"],"h":"tools_v15.py:9599","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"get_usage_stats","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Tổng hợp stat lượt gọi/lỗi/độ trễ theo từng tool của MỘT dự mục từ APP_SESSION_LOG.jsonl (last_n dòng) hoặc, khi log rỗng, từ các bảng events trong SQLite join với sessions (khoanh vùng theo dự án, khớp bằng đúng canonical project key). Trả top-10 tool theo số lượt gọi, các lỗi gần nhất, errors_by_tool và các file write_fix/write_new_file gần đây. include_global_usage và include_http_log đã bị tombstone (GLOBAL_SCOPE_NOT_ENABLED) — các nhánh tổng hợp global/UI/HTTP đã bị gỡ. Chính số lượt dùng của tool này là UNKNOWN (nằm trong skip-list, đã chứng minh là không log được ở Phase 2); không con số usage nào đượcclaim cho bản thân get_usage_stats. Biên: sqlite3.connect tạo sessions.db nếu chưa có.","uc":"Để tìm tool nào lỗi nhiều nhất trên một dự án, một CA gọi get_usage_stats rồi đọc errors_by_tool, thay vì grep log session thô.","cit":["code_tools.py:8129","code_tools.py:8150","code_tools.py:8186","code_tools.py:8203","code_tools.py:8249"],"h":"code_tools.py:8129","wk":"none","der":false,"wr":[],"use":{"b":"unknown","c":null,"ls":null}},
{"n":"get_validation_queue","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Liệt kê các lượt dry-run validation còn treo của một dự án bằng cách quét dict _DRY_RUN_STORE trong RAM (import từ code_tools) và trả các entry có project_path khớp, mỗi entry kèm dry_run_id, tool, file_path và created_at. Nó chỉ đọc bộ nhớ process — không bao giờ chạm DB sessions hay bất kỳ file nào, và không nhìn thấy dry-run của một process server đã restart.","uc":"Sau khi một dry-run write đã được staging nhưng chưa validate, một CA gọi get_validation_queue(project_path='D:/.../ToolA') để liệt kê các dry_run_id đang chờ validation.","cit":["tools_v15.py:9762","tools_v15.py:9771","tools_v15.py:9778","tools_v15.py:9786"],"h":"tools_v15.py:9762","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"git_blame_symbol","t":"T1","cat":"git","mut":false,"prot":false,"sess":false,"fg":"git","eff":"Blame định nghĩa đầu tiên của một symbol: nó uỷ quyền cho code_tools.find_symbol nội bộ để định vị def đầu tiên của symbol (file + dòng), rồi chạy git log -L trên range 10 dòng đó (tối đa 5 commit, ngân sách 60s) và trả hash, author, ngày và subject của từng commit. Nó là một probe lịch sử chỉ-đọc — git log không bao giờ sửa repo.","uc":"Khi một hàm hoạt động sai, một CA gọi git_blame_symbol(symbol='launch_electron_with_cdp', project_path='D:/.../ToolA') để xem năm commit gần nhất đã chạm các dòng định nghĩa của nó.","cit":["tools_v15.py:604","tools_v15.py:611","tools_v15.py:622","tools_v15.py:642"],"h":"tools_v15.py:604","wk":"none","der":false,"wr":[],"use":{"b":"rare","c":3,"ls":"2026-11-15T00:22:50"}},
{"n":"git_diff","t":"T1","cat":"git","mut":false,"prot":false,"sess":false,"fg":"git","eff":"Unified diff chỉ-đọc của một repo git (git chỉ-argv, không shell) trên worktree, index đã stage, hoặc cả hai. Pathspec là một allowlist phải tồn tại trên đĩa, trong index, hoặc trong HEAD, nếu không lượt gọi fail loud thay vì trả một diff rỗng. Scope sai bị từ chối. Với check_whitespace nó chạy thêm ngữ nghĩa git diff --check và báo các lỗi whitespace (ok:false, exit_code 2 khi có). Output bị cắt (max_chars clamp 1000..500000) kèm cờ truncated và total_chars.","uc":"Trước khi stage, review chính xác những gì đã đổi trong hai file (worktree so với HEAD) với 3 dòng ngữ cảnh.","cit":["orchestrator_server.py:12606","orchestrator/git_tools.py:338","orchestrator/git_tools.py:345","orchestrator/git_tools.py:364","orchestrator/git_tools.py:15"],"h":"orchestrator_server.py:12606","wk":"none","der":false,"wr":[],"use":{"b":"heavy","c":1691,"ls":"2026-11-16T13:46:43"}},
{"n":"git_log","t":"T1","cat":"git","mut":false,"prot":false,"sess":false,"fg":"git","eff":"Lịch sử commit chỉ-đọc của một repo git: chạy git log và trả các dòng hash, short_hash, author, email, date (ISO nghiêm), và subject cho ref được đưa (mặc định HEAD; có thể đưa một file path đã track để lấy lịch sử riêng file đó). Limit được clamp 1..200 (mặc định 20). Không ghi gì.","uc":"Liệt kê 20 commit gần nhất trên HEAD để tìm hành vi thay đổi lần cuối khi nào, hoặc lấy lịch sử của một file.","cit":["orchestrator_server.py:12607","orchestrator/git_tools.py:644","orchestrator/git_tools.py:645","orchestrator/git_tools.py:15"],"h":"orchestrator_server.py:12607","wk":"none","der":false,"wr":[],"use":{"b":"moderate","c":121,"ls":"2026-11-15T00:23:04"}},
{"n":"git_stale_check","t":"T1","cat":"git","mut":false,"prot":false,"sess":false,"fg":"git","eff":"Phán quyết stale chỉ-đọc cho một repo git, tính từ một git_status tươi cộng các kiểm tra cục bộ (không fetch nào được thực hiện). Nó báo stale=true kèm danh sách reasons khi bất kỳ điều nào sau đây đúng: worktree bẩn, conflict merge, HEAD detached, một operation đang giữa chừng (MERGE_HEAD, CHERRY_PICK_HEAD, REVERT_HEAD, BISECT_LOG, hoặc một thư mục rebase), tụt sau upstream local ref, HEAD cũ hơn max_commit_age_hours (mặc định 168), hoặc state_hash hiện tại lệch khỏi expected_state_hash. Nó cũng trả commit_age_hours, các trạng thái operation nhận diện được, và snapshot status. Không thay đổi gì.","uc":"Trước khi bắt một task agent dài, kiểm repo đang bẩn, giữa merge, detached, tụt hậu, hay đơn giản là không được chạm tới quá một tuần.","cit":["orchestrator_server.py:12605","orchestrator/git_tools.py:325","orchestrator/git_tools.py:336","orchestrator/git_tools.py:15"],"h":"orchestrator_server.py:12605","wk":"none","der":false,"wr":[],"use":{"b":"rare","c":1,"ls":"2026-11-11T13:59:37"}},
{"n":"git_status","t":"T1","cat":"git","mut":false,"prot":false,"sess":false,"fg":"git","eff":"Status working-tree chỉ-đọc của một repo git: branch, cờ detached, HEAD, số ahead/behind (local ref), state_hash và staged_hash, hồ sơ theo từng file, và các số đếm changed/staged/unstaged/untracked/conflicts. Record bị chặn ở max_records (mặc định 100) kèm records_total/records_truncated được báo, nhưng hash và số đếm được tính trên TOÀN BỘ record nên chúng vẫn chính xác. staged_hash digest toàn bộ index (git ls-files --stage), không phải diff so với HEAD, nên nó không đổi sau một commit và tự thân không chứng minh được một commit còn treo; dùng head cộng counts.staged cho việc đó. File untracked được gom theo thư mục cấp cao nhất theo mặc định (chỉ để trình bày).","uc":"Lấy branch, trạng thái sạch/bẩn và lock staged_hash trước khi stage hoặc commit; hoặc xác nhận một repo sạch trước một thao tác rủi ro.","cit":["orchestrator_server.py:12604","orchestrator/git_tools.py:266","orchestrator/git_tools.py:307","orchestrator/git_tools.py:311","orchestrator/git_tools.py:15"],"h":"orchestrator_server.py:12604","wk":"none","der":false,"wr":[],"use":{"b":"heavy","c":1995,"ls":"2026-11-16T15:32:29"}},
{"n":"hermes_host_gate_check","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Cổng host preflight cho một action được đề xuất: nó uỷ quyền cho classify_action trong orchestrator/core/ca_policy_matrix.py, nơi phân loại action chống ma trận chính sách tool của CA và trả phán quyết host_gate (allowed/deny), task class, native policy, và đường via-A được ưa. Khi cổng deny, response mang một lỗi hard-stop (HOST_GATE_DENY / FORBIDDEN_NATIVE) bảo người gọi dùng đường ToolA thay thế. Tool chỉ phân loại — không bao giờ thực thi, cho phép, hay tự thân thực hiện action. Ok cấp cao nhất vẫn true ngay cả khi deny.","uc":"Trước khi chạy một lượt move file bằng PowerShell native, một CA gọi hermes_host_gate_check(action_type='move_file', target='D:/x/y.txt') để biết ma trận có cấm làm native không và nên dùng tool ToolA nào thay thế.","cit":["tools_v15.py:9850","tools_v15.py:9861","tools_v15.py:9871","tools_v15.py:9897","ca_policy_matrix.py:419"],"h":"tools_v15.py:9850","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"import_graph","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Quét các câu lệnh import trên các file Python/JS/TS của một dự án bằng regex (import/from-import, import-from, require) và trả một adjacency list các cạnh cộng một bản tóm tắt chia cạnh thành internal/stdlib/external/from-tests, top 15 external root, và số node/file/edge (edge bị chặn ở 1000 trong response). Việc đi thư mục bỏ qua __pycache__, node_modules, .git, venv, và .orchestrator_cache; file chỉ được đọc, không bao giờ bị sửa.","uc":"Trước khi refactor một module, một CA gọi import_graph(project_path='D:/.../ToolA') để xem file nào import nó và dependency đó là internal hay external.","cit":["tools_v15.py:446","tools_v15.py:460","tools_v15.py:469","tools_v15.py:500","tools_v15.py:522"],"h":"tools_v15.py:446","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"inspect_project_runtime","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Introspection pre-flight cho một dự án trước khi launch nó: nó chấm điểm các launcher .bat/.cmd/.ps1 cấp cao nhất theo việc chúng CÓ thật sự gọi Electron hay không (không phải khớp substring lỏng lẻo), đọc package.json lấy scripts/dependencies/entry file, phát hiện --remote-debugging-port trong launcher, probe xem node_modules/electron có tồn tại không, và liệt kê năm file log gần nhất. Kết quả cho CA biết công thức launch nào (đường launcher, cổng CDP, bước cài) phải dùng trước khi fail.","uc":"Trước khi khởi động một dự án Electron lạ, một CA gọi inspect_project_runtime(project_path='D:/.../Rawcut') để tìm file .bat launcher của nó, cổng CDP, và Electron đã được cài chưa.","cit":["tools_v15.py:2518","tools_v15.py:2553","tools_v15.py:2598","tools_v15.py:2638","tools_v15.py:2677"],"h":"tools_v15.py:2518","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"inspect_project_usage","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Dựng một profile cấp workflow của một dự án bằng cách regex-scan các file HTML và JS của nó: các input field và button chính (kèm label), các định nghĩa hàm runtime kèm bằng chứng file+dòng (kể cả các khối <script> inline), các binding getElementById/addEventListener, các IPC handler của Electron (ipcMain.handle/on và contextBridge exposure), và các control nguy hiểm (output/reset/delete/wipe/clear/format/kill). Output là một gợi ý workflow khuyến nghị cộng các bản kiểm kê thô mà CA cần để lái app mà không phải gọi search_code lặp lại.","uc":"Sau khi mở một session app Electron mới, một CA gọi inspect_project_usage(project_path='D:/.../Rawcut') để biết các input field, button, và tên hàm runtime cần gọi thay vì bấm mù.","cit":["tools_v15.py:2836","tools_v15.py:2866","tools_v15.py:2969","tools_v15.py:3020","tools_v15.py:3106"],"h":"tools_v15.py:2836","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"leader_bootstrap","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"council","eff":"Snapshot khởi động (chỉ đọc, trừ một lượt ghi dọn dẹp nêu dưới) cho một mission leader: nạp roster council từ .council/council_config.json và model của worker từ .council/worker_models.json khi file đó có, liệt kê các mission active/blocked từ store SQLite missions.db, và probe sức khoẻ ToolQ ở 127.0.0.1:8082 (probe fail bị nuốt và báo null, không bao giờ thành lỗi). Từ chối bằng NOT_LOCK_HOLDER trừ khi instance này đang giữ orchestrator lock. Lưu ý: MỌI lượt gọi qua lock đều chạy _ensure_db(), vốn upsert vô điều kiện dòng meta schema_version trong missions.db (store.py:131-134) — một lượt ghi dọn dẹp, không phải thay đổi trạng thái mission (R24). rule_sha luôn null (tools.py:564, :590) (R32c).","uc":"Sau một lần restart, một agent leader bootstrap bằng cách đọc roster, model của worker và các mission còn active trong một lượt gọi. (plausible-but-unverified)","cit":["orchestrator/mission/tools.py:63","orchestrator/mission/tools.py:568","orchestrator/mission/tools.py:576","orchestrator/mission/tools.py:582","orchestrator/mission/tools.py:588"],"h":"orchestrator/mission/tools.py:558","wk":"none","der":false,"wr":[{"kind":"sqlite","target":"missions.db schema_version meta row (upsert via _ensure_db, every lock-passing call)","condition":"lock held"}],"err":{"c":"i","r":"output-history.md:L115 (R24)","q":"bookkeeping qualifier: _ensure_db meta upsert"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"list_projects","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"session","eff":"Liệt kê các project_path khác nhau được ghi nhận trong bảng sessions của sessions.db, kèm số session theo từng dự án và thời điểm bắt đầu mới nhất, sắp theo mới nhất trước (mặc định limit 26), và map mỗi dòng thành một slot label ngắn ([Ab]..[Az]). Nó là một thư mục chỉ-đọc trên các session quá khứ — không quét filesystem để tìm dự án.","uc":"Khi một CA quên dự án nào có session, nó gọi list_projects() để lấy danh sách dự án kèm số đếm và slot label [Ab] để đưa tiếp cho list_sessions.","cit":["tools_v15.py:9733","tools_v15.py:9739","tools_v15.py:9741","tools_v15.py:9754","tools_v15.py:9759"],"h":"tools_v15.py:9733","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"list_sessions","t":"T1","cat":"read,session","mut":false,"prot":false,"sess":false,"fg":"session","eff":"Trả các session gần đây của MỘT dự án kèm số event theo từng session phân theo action_type cộng total_events, sắp theo started_at DESC có limit. Phạm vi được resolve qua helper dùng chung và các dòng bị lọc bằng khớp tuyệt đối canonical project key TRƯỚC khi sắp xếp/phân trang, nên một dòng cross-project không bao giờ lọt được. Thuần SELECT SQLite chạy ngoài event loop. Số lượt dùng là UNKNOWN — tool này nằm trong _SKIP_SESSION_LOG nên không con số usage nào được nhận cho nó.","uc":"Để nối việc, một CA liệt kê session của dự án tìm session_id đang hoạt động, rồi đọc event của session đó bằng get_session_events.","cit":["code_tools.py:16695","code_tools.py:16707","code_tools.py:16715","code_tools.py:16725","code_tools.py:16737"],"h":"code_tools.py:16695","wk":"none","der":false,"wr":[],"use":{"b":"unknown","c":null,"ls":null}},
{"n":"list_structure","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Trả một cây text của MỘT thư mục tới độ sâu yêu cầu (mặc định 2): entry lấy từ os.scandir, thư mục ẩn bị lọc qua should_skip_dir nhưng một lệnh un-skip !name tường minh trong overlay của dự án luôn phơi ra một thư mục, và chế độ compact chặn thư mục rộng ở 10 file cộng một dòng 'more files'. Path thiếu trả ok:true với exists:false trừ khi strict=true. Với save_output=true nó CÒN ghi structure_output.txt vào thư mục cache của dự án — lượt ghi duy nhất tool này thực hiện, và chỉ khi file marker của dự án tồn tại.","uc":"Một CA kiểm .orchestrator_ignore của dự án thật sự đã ẩn những gì bằng cách liệt kê cây và xem overlay phơi ra thư mục nào. (plausible-but-unverified)","cit":["orchestrator/tools/read/fs.py:31","orchestrator/tools/read/fs.py:60","orchestrator/tools/read/fs.py:71","orchestrator/tools/read/fs.py:76","orchestrator/tools/read/fs.py:112"],"h":"orchestrator/tools/read/fs.py:20","wk":"none","der":false,"wr":[{"kind":"file","target":"structure_output.txt in the project cache dir","condition":"save_output=true (default false)"}],"err":{"c":"ii","r":"output-history.md:L122 (R31)"},"use":{"b":"light","c":45,"ls":"2026-11-16T14:38:47"}},
{"n":"mcp_surface_diff","t":"T1","cat":"mcp","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"So các surface tool của backend — các key TOOL_MAP trong process, các tên hàm TOOL_SCHEMAS, và response /api/tools đang sống (lùi về schema trong process khi local server đang bận trả chính lượt gọi này) — cộng một danh sách tên MCP phía client nếu có, rồi báo các phần tử khác nhau và một drift_count. Nó phát hiện registry drift; không sửa chữa gì.","uc":"Sau một lần cập nhật ToolA, một CA gọi mcp_surface_diff() để kiểm TOOL_MAP, các schema và danh sách /api/tools đang sống còn thống nhất không, và chỉ ra tool nào có ở surface này nhưng thiếu ở surface kia.","cit":["tools_v15.py:5523","tools_v15.py:5536","tools_v15.py:5552","tools_v15.py:5573","tools_v15.py:5591"],"h":"tools_v15.py:5523","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"memory_capture","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Nắm các lượt của một session vào bộ nhớ dài hạn bằng cách POST chúng lên ToolQ /capture (writer A, dedup theo hash nội dung phía ToolQ). Nó thẩm định cục bộ trước và fail nhanh mà không dispatch sang ToolQ: thiếu session_id hoặc turns bị từ chối là MISSING_REQUIRED_FIELD, còn sai kiểu hoặc hình dạng (session_id không phải string, turns không phải list, phần tử turn không phải object, project không phải string) là SCHEMA_VIOLATION, mỗi ca trả write_performed=false và applied=false. Với input hợp lệ nó POST tới ToolQ ở 127.0.0.1:8082 và trả kết quả của ToolQ. Nếu ToolQ sập, nó trả ok:false, degraded:true, MEMORY_UNAVAILABLE thay vì raise. Nó ghi vào store bộ nhớ dài hạn của ToolQ, không bao giờ vào index ToolA nào.","uc":"Khi kết thúc một session, lưu các lượt của nó vào bộ nhớ dài hạn để một session sau nhớ lại những gì đã quyết.","cit":["orchestrator_server.py:14115","orchestrator_server.py:14130","orchestrator_server.py:14189","orchestrator_server.py:14050"],"h":"orchestrator_server.py:14115","wk":"none","der":false,"wr":[],"err":{"c":"ii","r":"output-history.md:L112 (R21)","q":"ToolQ external store"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"memory_ingest_path","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Đưa nội dung thô, một file, hoặc một thư mục vào thư viện ToolQ bằng cách POST lên ToolQ /ingest_path. Nó thẩm định cục bộ trước và fail nhanh mà không dispatch sang ToolQ: source_type là bắt buộc và phải là manual_text, file, hoặc folder; manual_text đòi content, file/folder đòi path, và một path chứa NUL hoặc ký tự điều khiển C0 bị từ chối; zone, subzone, session_id và project được kiểm kiểu khi có đưa. Với input hợp lệ, payload (kèm zone Z1/Z2/Z3, subzone, session_id, project và options/max_file_mb) được POST tới ToolQ ở 127.0.0.1:8082 với timeout 30s. Nếu ToolQ sập, nó trả ok:false, degraded:true, MEMORY_UNAVAILABLE thay vì raise. Nó ghi vào thư viện ToolQ, không bao giờ vào index ToolA nào.","uc":"Đưa một thư mục skill vào bộ nhớ ToolQ dưới zone Z2 để các lượt truy vấn bộ nhớ sau nhớ lại các rule mà nó chứa.","cit":["orchestrator_server.py:14218","orchestrator_server.py:14251","orchestrator_server.py:14394","orchestrator_server.py:14396","orchestrator_server.py:14195"],"h":"orchestrator_server.py:14218","wk":"none","der":false,"wr":[],"err":{"c":"ii","r":"output-history.md:L112 (R21)","q":"ToolQ external store"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"memory_query","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Đọc fail-soft bộ nhớ dài hạn bằng cách POST một query tới ToolQ: mode query dùng /query, mọi mode khác (mặc định pre_search) dùng /pre_search, với agent_id clau và top_k (mặc định 2) cùng timeout 30s. Thiếu query bị từ chối là MISSING_ARG mà không dispatch. Khi thành công nó trả {ok, source toolq, endpoint, result}. Khi thất bại nó phân biệt một timeout (MEMORY_TIMEOUT) với mọi thất bại khác (MEMORY_UNAVAILABLE), trả degraded:true kèm danh sách hits rỗng, và không bao giờ raise. Nó chỉ đọc bộ nhớ ToolQ và không ghi gì.","uc":"Trước khi bắt việc, hỏi bộ nhớ dài hạn về các quyết định trước đó của chủ đề này để việc mới không lặp lại hoặc mâu thuẫn với chúng.","cit":["orchestrator_server.py:14064","orchestrator_server.py:14076","orchestrator_server.py:14078","orchestrator_server.py:14087"],"h":"orchestrator_server.py:14064","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"memory_recall_cluster","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Recall fail-soft một cụm memory có neo từ ToolQ /machine_cluster_report. Nó cần một query hoặc một danh sách raw_ids tường minh (id lượt thô); không có cả hai thì từ chối MISSING_ARG mà không dispatch. Một recent_n tùy chọn được truyền xuống khi nó parse được thành integer. Khi thành công nó trả {ok, source toolq, result}; nếu ToolQ sập nó trả ok:false, degraded:true, MEMORY_UNAVAILABLE thay vì raise. Nó chỉ đọc bộ nhớ ToolQ và không ghi gì.","uc":"Recall cụm đã neo của các lượt trước quanh một quyết định, để một khẳng định về điều đã quyết có thể trích đúng các lượt earlier.","cit":["orchestrator_server.py:14094","orchestrator_server.py:14106","orchestrator_server.py:14109"],"h":"orchestrator_server.py:14094","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"mission_next","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"mission","eff":"Trả task sẵn sàng kế tiếp của một mission (thứ tự dependency do engine.next_ready_task giải) trừ một lượt ghi dọn dẹp (xem lưu ý dưới): nạp mission và các dòng task của nó từ missions.db rồi trả đúng một task kế tiếp, hoặc MISSION_NOT_FOUND cho mission lạ. Nó KHÔNG claim, lock hay dispatch task — dispatch là việc của mission_dispatch. Lưu ý: MỌI lượt gọi qua lock đều chạy _ensure_db(), vốn upsert vô điều kiện dòng meta schema_version trong missions.db (store.py:131-134) — một lượt ghi dọn dẹp, không phải thay đổi trạng thái mission (R24).","uc":"Một vòng leader poll task sẵn sàng kế tiếp của một mission trước khi dispatch nó. (plausible-but-unverified)","cit":["orchestrator/mission/tools.py:299","orchestrator/mission/tools.py:302","orchestrator/mission/tools.py:303"],"h":"orchestrator/mission/tools.py:293","wk":"none","der":false,"wr":[{"kind":"sqlite","target":"missions.db schema_version meta row (upsert via _ensure_db, every lock-passing call)"}],"err":{"c":"i","r":"output-history.md:L115 (R24)","q":"bookkeeping qualifier: _ensure_db meta upsert"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"mission_status","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"mission","eff":"Trạng thái của một mission (chỉ đọc, trừ một lượt ghi dọn dẹp nêu dưới): trả dòng mission cộng một danh sách task gọn theo từng task (id, seq, title, agent_hint, state, attempt, max_attempts, output_artifact_path), và với verbosity=fullappend 50 dòng ledger gần nhất. Mission lạ trả MISSION_NOT_FOUND. Lưu ý: MỌI lượt gọi qua lock đều chạy _ensure_db(), vốn upsert vô điều kiện dòng meta schema_version trong missions.db (store.py:131-134) — một lượt ghi dọn dẹp, không phải thay đổi trạng thái mission (R24).","uc":"Một leader kiểm một mission đã tiến tới đâu và task nào còn pending hoặc failed. (plausible-but-unverified)","cit":["orchestrator/mission/tools.py:504","orchestrator/mission/tools.py:507","orchestrator/mission/tools.py:521","orchestrator/mission/tools.py:522"],"h":"orchestrator/mission/tools.py:498","wk":"none","der":false,"wr":[{"kind":"sqlite","target":"missions.db schema_version meta row (upsert via _ensure_db, every lock-passing call)"}],"err":{"c":"i","r":"output-history.md:L115 (R24)","q":"bookkeeping qualifier: _ensure_db meta upsert"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"next_recommended_action","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Khuyến nghị action kế tiếp cho một dự án từ trạng thái thật, theo thứ tự ưu tiên: chạy generate_project_map / refresh_project_map khi trạng thái cache không ok; chạy backup_project khi manifest backup thiếu/không rõ; chạy prepare_ca_session khi chưa có bootstrap; ở chế độ T2 thì tạo BENCHMARK_REQUEST.md / BUILD_CONTEXT.md khi thiếu; nếu không thì dùng ref của INDEX để đọc đúng range, rồi edit/test/cache-delta. Fail loud (INVALID_PROJECT_PATH) trên một path không phải thư mục thay vì trả một action cụ thể. Thuần đọc.","uc":"Không chắc nên làm gì trước trên một dự án, một CA gọi next_recommended_action rồi làm theo đúng một action được trả (ví dụ dựng lại map) thay vì đoán.","cit":["code_tools.py:4328","code_tools.py:4332","code_tools.py:4341","code_tools.py:4345","code_tools.py:4347"],"h":"code_tools.py:4328","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"open_session","t":"T1","cat":"session","mut":false,"prot":false,"sess":false,"fg":"session","eff":"Mở hoặc resume một session đang hoạt động cho mỗi cặp project+agent: resolve gốc dự án (fail loud khi mơ hồ), chạy _init_session_db, rồi upsert dòng session qua _open_or_resume_session thông qua SessionWriter (Future commit bền vững); một session mới nhận title 'Untitled Session <ngày>' khi không đưa title. force_new_session đóng dòng cũ và tạo dòng mới một cách atomic; apply_requested_title đổi tên tại chỗ (cả hai mặc định false, loại trừ nhau, cả hai đều đòi agent_id). Resume mặc định giữ nguyên title và mode. Chỉ nhánh resume gắn một bản tóm tắt matrix-audit (R60b: :15859-15870) — lượt gọi đầu trong TTL 30s còn append .orchestrator_cache/matrix_log.jsonl — cộng các payload transport-policy và CSLt policy.","uc":"Lúc bắt đầu làm việc trên một dự án, một CA gọi open_session kèm path dự án và agent_id của mình để mở (hoặc resume) session và lấy session_id mà mọi lượt gọi audit sau được gán vào.","cit":["code_tools.py:15775","code_tools.py:15807","code_tools.py:15839","code_tools.py:15860","code_tools.py:15939"],"h":"code_tools.py:15775","wk":"none","der":false,"wr":[{"kind":"sqlite","target":"sessions.db sessions row (INSERT on create-path)"},{"kind":"sqlite","target":"sessions.db sessions row (UPDATE on stale-sweep)"},{"kind":"file","target":".orchestrator_cache/matrix_log.jsonl (on cache miss)"}],"err":{"c":"i","r":"output-history.md:L151 (R60)","q":"flag/scope self-contradiction: mutating=false + mutation_scope=audit + category write"},"note":"Flag/scope self-contradiction (R60m): registry mutating=false with mutation_scope=audit — the tool DOES write (session upsert); registry scope governs, flag does not.","use":{"b":"moderate","c":143,"ls":"2026-11-16T14:46:00"}},
{"n":"optional_tool_probe","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Probe các helper/endpoint tùy chọn mà KHÔNG thực thi chúng và KHÔNG đọc giá trị token: phân loại từng tên thành available (có trong TOOL_MAP, kèm tier + nhu cầu token), unavailable (thay thế đã biết: tool_inspector đã retire/deprecated, command_helper -> path_quote_helper, token_panel -> GET /api/token + protected_tools_status), hoặc unsupported (tên lạ -> xác minh qua GET /api/tools). Tra trong RAM thuần trên các bảng hardcode + TOOL_MAP.","uc":"Trước khi gọi một helper mà nó nhớ mang máng, một CA probe optional_tool_probe với ['tool_inspector', 'command_helper'] và biết cái đầu đã retire, cái thứ hai map sang path_quote_helper.","cit":["code_tools.py:5280","code_tools.py:5288","code_tools.py:5313","code_tools.py:5341","code_tools.py:5353"],"h":"code_tools.py:5280","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"parse_log_section","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Trích MỘT section có tiêu đề từ log của một trang qua tools_v15.extract_log_section (max_chars, tail), rồi theo từng dòng thử JSON parse + giải mã base64 các trường *_b64 (message / context / raw_issue) + parse JSON mã hóa kép; trả tối đa 50 entry có cấu trúc kèm số đếm error/warning/info/decoded và top-5 category. Lỗi của tool bên trong lan truyền nguyên văn (ok:false) thay vì bị biến thành một ok:true rỗng. Thuần đọc/giải mã.","uc":"Để đọc section console của một trang browser mà không tự giải mã base64, một CA gọi parse_log_section kèm page_id và tiêu đề section rồi nhận các entry có cấu trúc với message đã giải mã.","cit":["code_tools.py:12786","code_tools.py:12800","code_tools.py:12817","code_tools.py:12845","code_tools.py:12881"],"h":"code_tools.py:12786","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"path_quote_helper","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Dựng (không bao giờ thực thi) một wrapper Windows an toàn cho run_project_command và trả nó dưới dạng JSON: project_path/cwd, safe_command (PowerShell Set-Location -LiteralPath cho thư mục con, hoặc cmd /c pushd/popd cùng đường .bat đặt trong nháy kép), quoting_strategy, và cảnh báo cho các lệnh bị chặn (curl/wget/bash/cat/grep/sed/awk) hoặc các lệnh hủy diệt (del/erase/rd/rmdir/rm/remove-item), trường hợp đó ok=false. Dựng string thuần qua _path_quote_helper_payload.","uc":"Trước khi chạy một lệnh trong thư mục dự án có path chứa khoảng trắng, một CA hỏi path_quote_helper xin wrapper PowerShell an toàn rồi dán đúng lệnh đó vào run_project_command.","cit":["code_tools.py:5275","code_tools.py:653","code_tools.py:660","code_tools.py:678","code_tools.py:704"],"h":"code_tools.py:5275","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"progressive_matrix_check","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Snapshot chứng nhận tĩnh chỉ-đọc cho độ lệch workflow -> schema -> UI. Nó so TOOL_MAP đang sống với TOOL_SCHEMAS để tìm schema thiếu và x_phase lệch nhau, đọc WORKFLOW_T1-T4.md để kiểm tồn tại cùng các marker step, và đọc HTML của UI để tìm tab T4; nó trả ok_to_ship_static_matrix = (không có gap) kèm các gap được liệt kê theo khu vực (schema / workflow_docs / ui). Khi có gap, nó suy step kế tiếp từ chính khu vực gap đó — một gap schema nêu audit_tool_matrix, mọi gap khác nêu read_file_range — nhưng nó chỉ NÊU tên tool đó trong ca_hint/ca_next_action; không bao giờ tự gọi và không ghi gì.","uc":"Trước khi chốt một thay đổi registry hoặc workflow, một CA chạy progressive_matrix_check để chứng nhận độ lệch workflow -> schema -> UI và nhận đúng tool kế tiếp cho bất kỳ gap nào tìm thấy (audit_tool_matrix cho gap schema, read_file_range cho gap doc/UI).","cit":["tools_v15.py:2379","tools_v15.py:2385","tools_v15.py:2396","tools_v15.py:2444","tools_v15.py:2476"],"h":"tools_v15.py:2348","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"project_context_pack","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Profile dự án gọn một lượt gọi cho lần chạm đầu: các gốc component, ngôn ngữ, nhóm, top-5 hotspot, entrypoint (tối đa 10), độ tươi graph CBM và các tool kế tiếp được khuyến nghị — đọc từ index snapshot project-map INDEX.json. Kết quả được cache trong .orchestrator_cache/context_pack.json theo fingerprint của INDEX (mtime-kích thước); một lượt gọi lặp khớp fingerprint trả pack đã cache (cache_hit=true) mà không quét lại — nhưng một cache miss (hoặc refresh=true) sẽ GHI file pack mới trước khi trả. 'Index' ở đây là snapshot INDEX.json, không phải DB graph CBM.","uc":"Lần đầu chạm một dự án, một CA gọi project_context_pack một lượt để lấy các gốc, ngôn ngữ, hotspot và entrypoint; các lượt gọi lặp trong cùng trạng thái INDEX trả pack đã cache miễn phí.","cit":["code_tools.py:11147","code_tools.py:11158","code_tools.py:11170","code_tools.py:11221","code_tools.py:11222"],"h":"code_tools.py:11147","wk":"none","der":false,"wr":[{"kind":"file","target":".orchestrator_cache/context_pack.json","condition":"cache miss (fingerprint mismatch) or refresh=true; fingerprint-matching repeat calls return cached without writing"}],"err":{"c":"i","r":"output-history.md:L151 (R60)"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"protected_tools_status","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Trả báo cáo trạng thái bảo vệ-by-token chỉ từ các set registry trong RAM: tên tool protected vs unprotected (TOOL_MAP vs PROTECTED_TOOL_NAMES), protected_by_phase, tập mutating (PROTECTED ∪ TOKEN_REQUIRED_TOOLS), mutation_scopes theo từng tool, token_required_tools và nhãn tier. Không có file, DB, process hay network access nào trong thân hàm; giá trị thời gian duy nhất là datetime.now() cho trường last_updated.","uc":"Trước khi gọi một tool bị gate, một CA đọc protected_tools_status để biết tool nào đòi session/token và mỗi tool mang mutation_scope nào, mà không phơi bất kỳ material token nào.","cit":["code_tools.py:5026","code_tools.py:5028","code_tools.py:5041","code_tools.py:5070"],"h":"code_tools.py:5026","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"query_dom_candidates","t":"T1","cat":"search","mut":false,"prot":false,"sess":false,"fg":"search","eff":"Liệt kê các element có thể bấm/điền được của một trang browser đang sống: dispatcher mặc định mode của nó thành 'dom_candidates' và handler dùng chung đòi page_id, rồi implementation chạy document.querySelectorAll trong trang (input/button/textarea/contenteditable/link/select, thu hẹp bởi filter) và trả tag, id, name, type, placeholder, độ dài value, xem trước text, độ hiển thị cùng selector hint của từng element, cộng tổng số và số đếm theo tag. Một response quá khổ được đẩy sang file artifact chỉ khi auto_artifact=true (mặc định false).","uc":"Trước khi lái một browser test, một CA liệt kê các button và input của trang kèm selector hint của chúng để chọn đúng đích cần bấm hoặc điền. (plausible-but-unverified)","cit":["orchestrator/tools/search.py:1675","orchestrator/tools/search.py:1680","tools_v15.py:4069","tools_v15.py:4093","tools_v15.py:4111"],"h":"orchestrator/tools/search.py:1575","wk":"none","der":false,"wr":[{"kind":"file","target":"<project>/.orchestrator_cache/artifacts/<round_id>/<tool>_response.json","condition":"auto_artifact=true + oversize (spill path, tools_v15.py:162-230)"}],"err":{"c":"ii","r":"output-history.md:L137 (R46)","q":"artifact file only when auto_artifact=true and response oversize"},"use":{"b":"light","c":36,"ls":"2026-11-11T23:52:55"}},
{"n":"query_index","t":"T1","cat":"search","mut":false,"prot":false,"sess":false,"fg":"search","eff":"Lọc snapshot project-map CURRENT INDEX theo metadata, path, symbol và nội dung: query khớp đường dẫn và tên file, các symbol function/class đã index, và một lượt quét literal có giới hạn trên các blob snapshot đã pin (256 KB mỗi file), với các filter group, min_complexity, min_loc và file_pattern; kết quả sắp theo complexity và chặn ở 50. Nó pin snapshot dưới một deadline 12s và phát hiện drift, nhưng KHÔNG BAO GIỜ dựng lại index: khi snapshot thiếu, không có thẩm quyền hoặc stale, nó từ chối bằng INDEX_REFRESH_REQUIRED và bảo người gọi chạy generate_project_map hoặc refresh_project_map một cách tường minh rồi poll write_status. mutation_scope=derived_index trong registry là một nhãn rủi ro, không phải một lượt ghi.","uc":"Một agent tìm định nghĩa của write_fix gọi query_index(query='write_fix') và nhận các file khớp cùng symbol đã khớp; nếu snapshot stale nó nhận INDEX_REFRESH_REQUIRED và phải refresh project map trước.","cit":["code_tools.py:7704","code_tools.py:7819","code_tools.py:7823","code_tools.py:7893","code_tools.py:8008","code_tools.py:8058","code_tools.py:8096","code_tools.py:8114"],"h":"code_tools.py:7704","wk":"none","der":false,"wr":[],"note":"Registry-label errata (R18): registry mutation_scope says derived_index, but the code never writes the derived index — label-vs-code conflict; code governs.","use":{"b":"light","c":17,"ls":"2026-11-11T20:33:48"}},
{"n":"query_sqlite","t":"T1","cat":"search","mut":false,"prot":false,"sess":false,"fg":"search","eff":"Thẩm định schema của một DB SQLite (bảng + cột qua sqlite_master và PRAGMA table_info) hoặc chạy một truy vấn chỉ-đọc có giới hạn (SELECT/WITH/PRAGMA/EXPLAIN, LIMIT tự nối vào, trần 1000 dòng) trên file được mở bằng sqlite3.connect mode=ro (URI). Một pre-guard trả PATH_NOT_FOUND khi file không tồn tại, nên connect không bao giờ tạo được file. Từ khoá ghi/DDL bị chặn bởi một bộ lọc từ khoá, và kết nối mode=ro là chốt chặn cứng: ngay cả một PRAGMA có khả năng ghi cũng fail với lỗi readonly-database thay vì ghi. Một side effect trên filesystem mà không có thay đổi nội dung logic (R69): lượt mở chỉ-đọc + SELECT đầu tiên trên một database chế độ WAL chưa có sidecar sẽ tạo <db>-shm (32.768 B) + một <db>-wal rỗng (0 B) khi thư mục ghi được (đã verify trên máy A, sqlite 3.49.1, và trên VM, sqlite 3.45.1, kích thước giống hệt); database rollback-journal và WAL ấm (đã có sidecar) không tạo gì. Nó chỉ fail khi thư mục không ghi được (đã verify trên máy A, sqlite 3.49.1, 2026-11-16). Registry mutating=false là ĐÚNG. Biên case tạo file của get_usage_stats ở Batch 8 KHÔNG áp dụng ở đây vì phép mở mode=ro cộng guard tồn tại. Errata loại (ii) vẫn đứng.","uc":"Một CA điều tra một lượt ToolA bị kẹt gọi query_sqlite với sql rỗng để liệt kê bảng và cột của một session DB, rồi chạy một SELECT có giới hạn trên các dòng gần đây để xem trạng thái được ghi cuối là gì — mà không có rủi ro sửa database.","cit":["tools_upgrade_p4.py:37","tools_upgrade_p4.py:43","tools_upgrade_p4.py:46","tools_upgrade_p4.py:66"],"h":"tools_upgrade_p4.py:37","wk":"none","der":false,"wr":[{"kind":"sqlite","target":"<db_path>-shm and <db_path>-wal sidecar files","condition":"WAL-mode database without existing sidecars; empty/ephemeral SQLite bookkeeping, no logical content change"}],"err":{"c":"ii","r":"output-history.md:L160 (R69)","q":"WAL -shm/-wal sidecar creation on first read-only open of a cold WAL DB (R69)"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"read_all_logs","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Gom bốn panel log mặc định (System Error Log, User Error & Help Panel, Agent Task Log, AI Log Analysis Panel) trong MỘT lượt page.evaluate, chọn body của mỗi panel theo data_log_attr, rồi id_matches_logarea, rồi nội dung dài nhất, và cắt mỗi panel còn max_chars (mặc định 30000; tail=true giữ các ký tự cuối). Tùy chọn base64 và tùy chọn liệt kê textarea view-* (include_view_panels, pattern ^view-). Spill: response đi qua _auto_artifact_if_oversize, vốn chỉ ghi toàn bộ JSON vào một file artifact khi auto_artifact=true VÀ response đã serialize vượt auto_artifact_max_bytes (mặc định 4096); lượt gọi mặc định không ghi gì.","uc":"Sau một lượt test fail, một CA đổ cả bốn panel log trong một lượt gọi; khi bản dump quá lớn nó truyền auto_artifact=true để toàn bộ text nằm trong file artifact còn response chỉ mang path + sha256 + độ dài.","cit":["tools_v15.py:3881","tools_v15.py:3929","tools_v15.py:3941","tools_v15.py:4026","tools_v15.py:178","tools_v15.py:200"],"h":"tools_v15.py:3850","wk":"none","der":false,"wr":[{"kind":"file","target":"<project>/.orchestrator_cache/artifacts/<round_id>/read_all_logs_response.json","condition":"auto_artifact=true AND serialized response > auto_artifact_max_bytes (default 4096)"}],"err":{"c":"ii","r":"output-history.md:L296 (Wave 12 synthesis)","q":"opt-in spill (R35 shape)"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"read_chunks","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Trả một chunk có giới hạn của file theo dòng (mặc định, count chặn ở 2000 dòng) hoặc theo byte (seek + đọc tới count byte), kèm cờ has_more; mặc định nó không bao giờ trả cả file. Thuần đọc: file được mở chỉ-đọc và không ghi gì ở đâu. Registry mutating=false là ĐÚNG. DEPRECATED: tool là một thành viên machine-readable của _DEPRECATED_TOOLS (orchestrator_server.py:1929, 'Use read_file_range(...) directly') và docstring của nó mang banner DEPRECATED tương ứng (tools_upgrade_p4.py:200); caller nên dùng read_file_range thay thế.","uc":"Các caller cũ còn lật một log lớn từng chunk dùng read_chunks theo range dòng; caller mới nên dùng read_file_range thay vì read_chunks, vì read_chunks đã deprecated.","cit":["tools_upgrade_p4.py:198","tools_upgrade_p4.py:200","tools_upgrade_p4.py:211","orchestrator_server.py:1929"],"h":"tools_upgrade_p4.py:198","wk":"none","der":false,"wr":[],"dep":true,"use":{"b":"unused","c":0,"ls":null}},
{"n":"read_context_batch","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Đọc nhiều file/scope trong một lượt gọi dưới một ngân sách token tất định (mặc định 6000): khử trùng các request giống nhau, sắp theo priority, chặn items ở BATCH_MAX_ITEMS, chia ngân sách còn lại cho các item, gọi read_file_range một lần cho mỗi item đã chọn, và hoãn phần còn lại (item tràn và item quá ngân sách) vào một danh sách deferred thay vì làm lượt gọi fail. Trả kết quả theo từng item cộng token đã dùng/còn lại và next_ranges đã khử trùng.","uc":"architecture_context_pack gọi nó để đọc mọi range đã xếp hạng của lân cận một symbol trong một lượt gọi có ngân sách (context.py:912).","cit":["orchestrator/tools/read/context.py:720","orchestrator/tools/read/context.py:730","orchestrator/tools/read/context.py:748","orchestrator/tools/read/context.py:766","orchestrator/tools/read/context.py:791"],"h":"orchestrator/tools/read/context.py:695","wk":"none","der":false,"wr":[],"use":{"b":"light","c":7,"ls":"2026-11-15T10:56:03"}},
{"n":"read_file_range","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Đọc một range dòng hoặc một scope cấu trúc (range/function/class/file_summary/symbol/section, hoặc một mảng ranges[]) của một file text và trả text kèm content_hash, total_lines và các range láng giềng. Nó từ chối thư mục và file binary, và docstring của nó nêu rằng nó đọc 'không làm thay đổi trạng thái dự án suy diễn' — trạng thái duy nhất nó chạm là niêm phong token độ tươi khi-đọc cho các byte đã đọc, để một lượt write_fix sau đó trên cùng file không trả FILE_CHANGED_OUTSIDE_ORCHESTRATOR.","uc":"Mọi bước audit, sửa và review đều đọc range file chính xác qua tool này trước khi chạm code; các chuỗi write_fix phụ thuộc vào token nó gieo.","cit":["orchestrator/tools/read/context.py:206","orchestrator/tools/read/context.py:215","orchestrator/tools/read/context.py:269","orchestrator/tools/read/context.py:313","orchestrator/tools/read/fs.py:176-177","orchestrator/tools/write/core.py:1897","orchestrator/tools/search.py:1657-1680"],"h":"orchestrator/tools/read/fs.py:280","wk":"none","der":false,"wr":[{"kind":"sqlite","target":"read_tokens (upsert on every seeded read)"}],"err":{"c":"i","r":"output-history.md:L115 (R24)","q":"bookkeeping qualifier: token upsert is session-store housekeeping, not user-visible state"},"use":{"b":"heavy","c":273952,"ls":"2026-11-16T15:48:48"}},
{"n":"read_skill","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Đọc thân một skill theo tên ngắn mà không cần project_path: nó resolve <orchestrator>/.ToolA_Skill/skills/<name>/SKILL.md bằng khớp thư mục exact, prefix hoặc substring (các alias router như 'skills' hoặc '00' map tới 00_skill_router), đọc file, và trả nội dung của nó kèm số dòng, chặn ở max_lines (mặc định 400, tối đa 2000). Verbosity full/terse/ref quyết định lượng metadata được trả; một tên không khớp trả SKILL_NOT_FOUND kèm danh sách khả dụng.","uc":"Trước khi lái browser, một CA gọi read_skill('18') để đọc skill 18_runtime_dom_T4 mà không phải đoán tổ hợp project_path/file_path nào dùng.","cit":["tools_v15.py:4431","tools_v15.py:4440","tools_v15.py:4460","tools_v15.py:4464"],"h":"tools_v15.py:4404","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"read_value","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"read","eff":"Đọc giá trị HIỆN TẠI của một element (el.value với input, textContent với các trường hợp khác) qua một lượt page.evaluate duy nhất, ở mã hóa plain hoặc base64, cắt ở max_chars (mặc định 100000); selector thiếu trả SELECTOR_NOT_FOUND. Nó là phần đọc đối xứng của fill_value: cùng kiểu đọc theo selector, không ghi, không dispatch event.","uc":"Sau khi điền một textarea, một CA gọi read_value(page_id, '#txtPath') để đọc lại giá trị hiện tại và xác nhận path dự định thực sự đã vào.","cit":["tools_v15.py:6094","tools_v15.py:6108","tools_v15.py:6115","tools_v15.py:6120"],"h":"tools_v15.py:6069","wk":"none","der":false,"wr":[],"note":"Tier-split anomaly (R51f): read_value is T1 while fill_value is T2 — unexplained, carried as an anomaly.","use":{"b":"rare","c":3,"ls":"2026-11-10T17:16:52"}},
{"n":"regex_search","t":"T1","cat":"search","mut":false,"prot":false,"sess":false,"fg":"search","eff":"Alias đã deprecated của search_code cho tìm regex: query đến qua pattern/regex, mà handler lấy làm query và ép literal_search tắt nên mode thành 'regex'; làn regex chạy trong một worker cách ly có deadline (các lượt quét không khoanh vùng dưới 0.5s bị từ chối, 0.5-2.0s bị nâng lên sàn 2.0s), từ chối một lượt retry không đổi của cặp scope+query vừa timeout, và vớt các khớp một phần khi timeout. Lượt quét đọc record từ snapshot project-map đã pin (search_snapshots), chỉ publish một snapshot khi chưa có.","uc":"Một CA tìm một pattern regex như hình dạng gọi hàm khắp dự án, thu hẹp path hoặc file_pattern sau một lần từ chối vì timeout. (plausible-but-unverified)","cit":["orchestrator/tools/search.py:1599","orchestrator/tools/search.py:1636","orchestrator/tools/search.py:1759","orchestrator/tools/search.py:2034","orchestrator/tools/search.py:2092","orchestrator/tools/search.py:1575","orchestrator/tools/search.py:1657-1680","orchestrator/tools/search.py:2702-2823","orchestrator/tools/search.py:2801-2823"],"h":"orchestrator/tools/search.py:1575","wk":"none","der":false,"wr":[{"kind":"index","target":"project-map snapshot index (reconcile + republish, only on 0-hit + stale)"}],"dep":true,"use":{"b":"unused","c":0,"ls":null}},
{"n":"resolve_candidate_paths","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Kiểm tồn tại hàng loạt (CAN-053): nạp index snapshot project-map INDEX.json qua _load_index, rồi với tối đa 50 candidate path báo exists (Path.exists()) và, cho file thiếu, tối đa 3 gợi ý fuzzy từ basename của index qua difflib. Thuần đọc — chỉ nạp index + probe stat, không ghi bất kỳ kiểu nào.","uc":"Sau một lần đổi tên, một CA đưa path cũ và mới cho resolve_candidate_paths trong một lượt gọi để xác nhận path nào tồn tại và nhận gợi ý fuzzy cho những path không có, thay vì N lượt đọc mù.","cit":["code_tools.py:11229","code_tools.py:11234","code_tools.py:11241","code_tools.py:11243"],"h":"code_tools.py:11229","wk":"none","der":false,"wr":[],"use":{"b":"rare","c":2,"ls":"2026-11-12T06:07:36"}},
{"n":"resolve_project_path","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Resolve một path file/thư mục do user cung cấp thành metadata project/file ổn định: normalize path, stat nó (exists/is_file/is_dir), suy gốc dự án từ việc preferred_root chứa nó, các marker gốc dự án (find_project_root), hoặc chấp nhận một thư mục hiện hữu một cách tường minh, và trả project_path/file_path/folder_path cộng một suggested session title. Input UNC bị từ chối thẳng (UNC_PROJECT_ROOT_UNSUPPORTED) chứ không probe. Thuần đọc stat; không ghi.","uc":"Lúc mở session một CA đưa một path user dán vào cho resolve_project_path để biết gốc dự án canonical và path đó có cần user xác nhận trước khi mở session không.","cit":["code_tools.py:5021","code_tools.py:5023","code_tools.py:528","code_tools.py:575"],"h":"code_tools.py:5021","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"rule_drift_check","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Audit drift giữa các file text RULE (CORE_RULE.txt, SLIM_RULE.txt, SLIM_RULE_INLINE.txt) và code đang sống (TOOL_MAP, các tập enforcement, council config) qua bốn kiểm tra: tool_names (rule gọi tool mà registry không biết, nhận ra theo vị trí gọi), agent_list (agent mà rule nêu tên nhưng council_config thiếu), behavior_sets (mâu thuẫn và phantom của mutation-scope cùng gate-set), constants (PORT và các trần read/search). Chỉ đọc; nó trả drift_count, by_severity và các dòng drift, fail loud khi tên check lạ, và khi có drift thì hint write_fix để hoà giải, lưu ý rằng code có thể là phía sai chứ không chỉ có doc.","uc":"Sau khi đổi tên một tool, một agent gọi rule_drift_check để tìm mọi file rule còn dạy tên cũ, rồi hoà giải từng dòng drift bằng write_fix.","cit":["code_tools.py:5074","code_tools.py:5091","code_tools.py:5095","code_tools.py:5156","code_tools.py:5175","code_tools.py:5195","code_tools.py:5216","code_tools.py:5253","code_tools.py:5267"],"h":"code_tools.py:5074","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"sample_data_file","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Trả N dòng đầu/cuối/ngẫu nhiên (mặc định 5, trần 200) của một file CSV/TSV/JSONL/log/text mà không đổ cả file: head đọc N dòng đầu, tail giữ N dòng cuối qua một deque, random lấy mẫu N dòng. Dòng CSV/TSV được parse thành row và dòng JSONL thành object khi đuôi khớp; các đuôi khác trả dòng thô. Thuần đọc: file được mở chỉ-đọc ('r', errors='replace') và không ghi ở đâu. Registry mutating=false là ĐÚNG.","uc":"Trước khi viết parser cho một bản xuất CSV lạ, một CA gọi sample_data_file chế độ head với n=5 để xem header và các dòng đầu, rồi chế độ random để kiểm các dòng sau còn giữ cùng hình dạng không.","cit":["tools_upgrade_p4.py:156","tools_upgrade_p4.py:165","tools_upgrade_p4.py:175","tools_upgrade_p4.py:180"],"h":"tools_upgrade_p4.py:156","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"sample_unc_paths_with_root_check","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Lấy mẫu tối đa 3 path UNC (mặc định sample_count=3, từ ít nhất 2 thư mục cha khác nhau khi có thể), probe từng path qua Path.exists()/os.stat trong một timeout đa luồng (an toàn Unicode), đếm file/thư mục bằng iterdir cho thư mục tới được, và — khi bất kỳ mẫu nào fail và root_check=true (mặc định) — probe root share \\\\server\\share để phân biệt 'path cũ' với 'server sập'. Trả một phán quyết (all_reachable | partial_reachable_paths_stale | all_failed_server_down | unc_auth_context_mismatch khi WinError 1326/1219/1385/86/5 nổi lên, v.v.) kèm cảnh báo mojibake. Thuần đọc — chỉ probe tồn tại/stat/listing.","uc":"Sau khi fetch một sheet footage, một CA lấy mẫu 3 path thư mục UNC từ nó để kiểm share có tới được và các path có cũ không, trước khi kết luận server sập.","cit":["code_tools.py:11811","code_tools.py:11918","code_tools.py:11933","code_tools.py:11975"],"h":"code_tools.py:11811","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"search_code","t":"T1","cat":"search","mut":false,"prot":false,"sess":false,"fg":"search","eff":"Engine tìm kiếm code dùng chung mà cả sáu alias tìm kiếm đều dispatch qua: nó normalize query và mode (text literal, regex, smart token-OR, auto detection, cộng các làn symbol/callers/definition_usage/dom_candidates/index), rồi quét các record đọc từ snapshot project-map đã pin (search_snapshots) — chứ không phải filesystem đang sống — và trả các khớp file/dòng kèm ngữ cảnh, chặn bởi max_results và max_context_lines. Các guard từ chối query bắt hết mọi thứ (SEARCH_TOO_BROAD), regex không khoanh vùng dưới deadline sàn của nó, và các lượt retry cùng tham số sau một timeout; một snapshot stale được hoà giải trước khi trả lời.","uc":"Các lượt audit tìm callers, định nghĩa và usage qua tool này (mode='callers'/'symbol') trước khi sửa, thay vì grep cây đang sống. (plausible-but-unverified)","cit":["orchestrator/tools/search.py:1629","orchestrator/tools/search.py:1819","orchestrator/tools/search.py:1938","orchestrator/tools/search.py:2034","orchestrator/tools/search.py:2092","orchestrator/tools/search.py:1575","orchestrator/tools/search.py:1657-1680","orchestrator/tools/search.py:2702-2823","orchestrator/tools/search.py:2801-2823"],"h":"orchestrator/tools/search.py:1575","wk":"none","der":false,"wr":[{"kind":"index","target":"project-map snapshot index (reconcile + republish, only on 0-hit + stale)"}],"use":{"b":"heavy","c":13013,"ls":"2026-11-16T15:48:04"}},
{"n":"soft_data_reconcile","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Dựng một báo cáo overlap/bloat/conflict cross-layer về surface tool của chính [A] (schema/param + alias + see_also + skill + function + usage): import engine scripts/soft_data_reconcile.py và gọi build() của nó trong một worker thread, rồi trả báo cáo (gọn theo mặc định; full=true giữ các record theo từng tool ~240 dòng). Chỉ mang tính tư vấn — docstring nêu 'Never edits anything', và các lượt ghi file duy nhất của engine bị gate bởi CLI (Path.write_text dưới điều kiện 'if args.json_out', scripts/soft_data_reconcile.py:624/:630), không tới được từ build(). Phép insert sys.path của handler chỉ ở memory. Các lượt ghi duy nhất là artifact --json-out của CLI.","uc":"Trong một lượt audit surface tool, một CA chạy soft_data_reconcile để lấy báo cáo overlap/bloat/conflict (tên param mơ hồ, các cặp dễ nhầm, see_also treo) mà không thay đổi gì.","cit":["code_tools.py:22105","code_tools.py:22117","code_tools.py:22125","scripts/soft_data_reconcile.py:293","scripts/soft_data_reconcile.py:624"],"h":"code_tools.py:22105","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"state","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Dispatcher trạng thái hợp nhất trên bốn nhánh chỉ-đọc — cache (cache_status), workflow (workflow_status_registry), recommendation (next_recommended_action), session (get_session_events, chỉ SELECT, không tự mở). Nhánh workflow KHÔNG phải đọc thuần: workflow_status_registry làm giàu dict trạng thái (cache_status, drift, stale_decision, active_session_id, backup_status, updated_at) và lưu nó bằng _atomic_write_json vào WORKFLOW_STATE.json (code_tools.py:4307) ở MỌI lượt gọi, tạo file khi thiếu. Nên lượt gọi MẶC ĐỊNH (aspect=all) ghi lại WORKFLOW_STATE.json một cách bền vững; chỉ riêng aspect=cache/session/recommendation là tránh được lượt ghi. Các nhánh cache, recommendation và session là đọc. Mặc định aspect=all kích hoạt một lượt ghi lại WORKFLOW_STATE.json vô điều kiện.","uc":"Một CA gọi state một lần lúc bắt đầu một lượt để lấy độ tươi cache, chế độ workflow, action kế tiếp được khuyến nghị và event session mới nhất trong một snapshot — và nên biết rằng bản thân lượt gọi đó ghi lại WORKFLOW_STATE.json với enrichment mới.","cit":["code_tools.py:16974","code_tools.py:17007","code_tools.py:4307","code_tools.py:17000","code_tools.py:17019"],"h":"code_tools.py:16974","wk":"none","der":false,"wr":[{"kind":"file","target":"WORKFLOW_STATE.json in the project cache dir (via workflow_status_registry: enriched state + updated_at, atomic replace)","condition":"aspect=workflow or aspect=all (the default); aspect=cache/session/recommendation alone does not write"}],"err":{"c":"i","r":"output-history.md:L156 (R65)"},"use":{"b":"unused","c":0,"ls":null}},
{"n":"suggest_missing_capabilities","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Trả các capability còn thiếu: gọi capability_status (một danh sách capability tĩnh cộng các probe import _module_available — tree-sitter, yaml, playwright, psutil, pillow, v.v.) rồi lọc lấy các mục không khả dụng VÀ có lệnh cài, trả kèm auto_install:false. Thuần đọc; không cài gì và không ghi gì.","uc":"Khi một bước T4 browser fail vì thiếu thư viện, một CA gọi suggest_missing_capabilities để biết chính xác capability nào thiếu cùng lệnh pip install của chúng, rồi cùng user quyết định có cài không.","cit":["code_tools.py:4410","code_tools.py:4411","code_tools.py:4366","code_tools.py:4374"],"h":"code_tools.py:4410","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"tool_decision_hint","t":"T1","cat":"mcp","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Khuyến nghị routing cho một mô tả task: một wrapper mỏng uỷ quyền cho _legacy_decision_hint rồi thêm BM25 ranked_tools qua _rank_hint. Engine match các regex rule trong _DECISION_RULES với task thô và task đã mở rộng tiếng Việt (khớp đầu tiên thắng, confidence 0.85), cross-check các tool mà rule nêu với TOOL_MAP đang sống như một máy dò drift, và gắn ví dụ payload theo từng tool. Các đường đặc biệt: ý định LOOK route tới file_sha256/write_status; ý định restart trả một công thức chỉ-tư-vấn (server_lifecycle_status, rồi restart_orchestrator_via_bat cho server của chính nó) không thực thi gì; nó không bao giờ khuyến nghị write_fix khi task nói 'không sửa code'. Khi không rule nào khớp nó lùi về một lexical rank trên TOOL_SCHEMAS đang sống, tuỳ chọn được boost bởi ref bộ nhớ [Q] Z2 (một lượt đọc, fail-open), và mặc định rơi về search_code ở 0.4.","uc":"Một CA hỏi cách restart orchestrator và nhận công thức chỉ-tư-vấn — inspect port trước, rồi restart_orchestrator_via_bat sau khi được ủy quyền tường minh — thay vì thực thi bất kỳ gì.","cit":["tools_v15.py:2065","tools_v15.py:2068","tools_v15.py:2222","tools_v15.py:2244","tools_v15.py:2294"],"h":"tools_v15.py:2060","wk":"none","der":false,"wr":[],"use":{"b":"light","c":52,"ls":"2026-11-15T00:59:42"}},
{"n":"ui_state_summary","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"desktop","eff":"Tổng hợp trạng thái có cấu trúc, chỉ-bằng-text cho từng vùng UI trong MỘT lượt page.evaluate: các vùng {label, selector} do người gọi cung cấp, hoặc mặc định mọi phần tử [data-region] / section[id] cấp cao nhất (tối đa 30); theo từng vùng nó trả visible, disabled, text_preview (200 ký tự), value_preview (200), classes (8), has_error_class, màu nền và bounding rect. Nó KHÔNG phải screenshot — thuần text, tiết kiệm ~99% token so với một ảnh base64. Một eval fail trả DOM_EVAL_FAILED kèm phát hiện trang cũ.","uc":"Sau một cú click, một CA tóm lược trạng thái mọi vùng của một trang settings bằng text — cái gì hiển thị, cái gì đã đổi, cái gì mang error class — mà không phải trả tiền cho một screenshot.","cit":["tools_v15.py:4944","tools_v15.py:4960","tools_v15.py:4969","tools_v15.py:4978"],"h":"tools_v15.py:4922","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"verify_active_electron_source","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"desktop","eff":"Xác minh file nào một Electron CDP target thực sự đã nạp: evaluate một biểu thức JS chỉ-đọc (document.URL, window.location.href, baseURI, các ref script/link, top 10) trên trang qua tools_v15.evaluate_js, giải mã URL file:/// về path local, và so với expected_path (khớp hậu tố) để báo matches_expected. Biểu thức chỉ đọc trạng thái DOM; không sửa trang, không điều hướng, không ghi file. Lỗi evaluate (ví dụ PAGE_ID_INVALID) lan truyền thay vì báo một lượt nạp sạch sai sự thật.","uc":"Trước khi sửa Source/ui/index.html, một CA chạy verify_active_electron_source trên trang Electron để xác nhận app thực sự nạp file đó chứ không phải một bản dist/ hay public/.","cit":["code_tools.py:8715","code_tools.py:8741","code_tools.py:8748","code_tools.py:8767"],"h":"code_tools.py:8715","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"verify_get_footage_copy_completion","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Máy kiểm nhân quả hoàn tất copy của Rawcut trên hai lớp bằng chứng: folder_stats trên thư mục đích (số file / tổng byte / số thư mục / mtime mới nhất, so với baseline) và một lượt đọc đuôi tas-footage-err.log (200KB cuối, parse các FILE_COPIED / FILE_COPIED_REUSED / FILE_PATH_SKIPPED / GET_FOOTAGE_START / GET_FOOTAGE_COMPLETE / ERR_* / WARN_*), cross-check delta thư mục với số tổng của log thành một phán quyết (copy_complete_clean | copy_complete_with_warnings | mismatch_folder_vs_log | copy_did_not_start | copy_started_but_no_files_yet | copy_failed_with_errors, v.v.), kèm một drilldown chênh lệch tập tên file theo R51 khi thư mục và log không khớp. Thuần đọc — chỉ đi thư mục + đọc đuôi log.","uc":"Sau một lượt get-footage của Rawcut, một CA gọi verify_get_footage_copy_completion kèm thư mục đích và baseline trước-copy để lấy một phán quyết duy nhất về việc bản copy có thực sự hoàn tất, thay vì tự nhìn số đếm thư mục và các dòng log.","cit":["code_tools.py:12107","code_tools.py:12151","code_tools.py:12191","code_tools.py:12286"],"h":"code_tools.py:12107","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"wait_for_idle","t":"T1","cat":"runtime","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Vòng polling dựng sẵn bọc assert_target_idle: probe các signal được đưa mỗi check_interval_sec (mặc định 30s, tối thiểu 2s) cho tới khi đích idle liên tục trong stable_seconds (mặc định 20s) hoặc hết timeout_seconds (mặc định 900s), trả final_outcome (idle | copy_in_progress | process_busy | busy | unknown | no_signals) kèm tóm lược 5 lần kiểm cuối. Bản thân vòng lặp chỉ ngủ; các probe là lượt đọc (snapshot tăng trưởng thư mục, counter CPU/IO psutil, regex đuôi log; các loại signal cdp/ui/window là stub best-effort trả not_evaluated). Không ghi bất kỳ kiểu nào.","uc":"Sau khi khởi động một bản copy dài, một CA gọi wait_for_idle kèm một tín hiệu folder_growth_stable thay vì tự lặp 60 phép probe, và chỉ đi tiếp workflow khi final_outcome=idle.","cit":["code_tools.py:12711","code_tools.py:12736","code_tools.py:12746","code_tools.py:14240","code_tools.py:14338"],"h":"code_tools.py:12711","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"webfetch","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Fetch nội dung URL bằng HTTP/HTTPS GET (mặc định kiểm TLS; User-Agent 'Mozilla/5.0 (ToolA Orchestrator R44 webfetch)'), đọc tối đa max_chars+1 byte (mặc định 200.000), giải mã UTF-8, và trả text thô (format=text, kèm line_count) hoặc JSON đã parse (format=json) cộng status_code, content_type và cờ truncated. Một lượt đọc mạng, không phải lượt ghi: response được giữ trong memory và trả về; không lưu vào disk hay DB. verify_tls=false là opt-in tắt kiểm tra TLS (response không được xác thực — một ghi chú bảo mật, không phải một lượt ghi).","uc":"Một CA fetch một URL xuất CSV của Google Sheets một lượt qua webfetch để parse trong memory, thay vì tải sheet xuống disk trước.","cit":["code_tools.py:11373","code_tools.py:11403","code_tools.py:11410","code_tools.py:11413","code_tools.py:11425"],"h":"code_tools.py:11373","wk":"none","der":false,"wr":[],"use":{"b":"rare","c":3,"ls":"2026-11-10T18:16:20"}},
{"n":"workflow_active_get","t":"T1","cat":"workflow","mut":false,"prot":false,"sess":false,"fg":"workflow","eff":"Đọc marker workflow-đang-hoạt-động _active.json (qua _wf_active_path) và, khi có workflow đang hoạt động, file run-state của nó (qua _wf_load_state) cộng MD của workflow, rồi trả name / step hiện tại / tổng số step / thân step / complete / paused / trạng thái từng step. Không nội dung file, dòng DB hay giá trị trạng thái nào bị ghi: cả hai phép load đều chỉ-đọc (open 'r' + json.load). Có một side effect filesystem phát sinh được khai báo rõ, không bị tính là lượt ghi: _wf_active_path (13972) và _wf_load_state (13982) đều tới _wf_project_state_dir (code_tools.py:13007), nơi chạy base.mkdir(parents=True, exist_ok=True) — ở lượt gọi đầu nó tạo các thư mục rỗng .ToolA_Skill/workflow_state[/by_project/<hash>]. Việc dựng scaffold thư mục rỗng không ghi trạng thái nào, nên cờ mutating=false trong registry được giữ là ĐÚNG theo cách đọc durable-write của campaign; một cách đọc nghiêm hơn tính mkdir là ghi sẽ lật dòng này (xem notes).","uc":"Một CA vừa resume session gọi workflow_active_get để biết workflow nào (nếu có) đang hoạt động và nó ở step nào, trước khi quyết định có theo workflow_get_current / workflow_submit_step hay không.","cit":["code_tools.py:13970","code_tools.py:13972","code_tools.py:13976","code_tools.py:13982"],"h":"code_tools.py:13970","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"workflow_draft_get","t":"T1","cat":"workflow","mut":false,"prot":false,"sess":false,"fg":"workflow","eff":"Kéo một workflow draft khỏi store trong memory _WORKFLOW_DRAFTS theo token (13890) và trả name / body_md / created_at / expires_at của nó; token lạ hoặc hết hạn nhận lỗi DRAFT_NOT_FOUND. Mutation duy nhất là _wf_purge_expired_drafts (13889), pop các entry hết hạn khỏi cùng dict trong memory đó — chỉ memory, không bền vững. Không file, DB hay disk access nào trong thân hàm.","uc":"Editor UI mở /ui/workflow_editor?draft=<token> và gọi workflow_draft_get(token) để đổ form soạn thảo bằng draft do agent soạn mà user sắp review.","cit":["code_tools.py:13887","code_tools.py:13889","code_tools.py:13890","code_tools.py:13896"],"h":"code_tools.py:13887","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"workflow_get_current","t":"T1","cat":"workflow","mut":false,"prot":false,"sess":false,"fg":"workflow","eff":"Đọc thân step hiện tại của một workflow (idempotent, docstring 13370): tìm MD, nạp run state qua _wf_load_state (13382), parse MD, và trả thân step qua resolver dùng chung _wf_step_response cộng một tóm lược best-effort (outcome/file_count/total_bytes/error_summary) rút ra từ bằng chứng của các step đã hoàn tất (13395-13401). Workflow chưa bắt đầu resolve giống hệt workflow_status (current_step=0, started=False) theo contract E-01. Không giá trị trạng thái nào bị ghi; tóm lược được tính trong memory từ bằng chứng đã lưu. Một side effect filesystem phát sinh được khai báo, không bị tính là lượt ghi: _wf_load_state tới _wf_project_state_dir (code_tools.py:13007), nơi mkdir thư mục trạng thái workflow (có thể rỗng) ở lượt gọi đầu.","uc":"Trong một lượt chạy workflow, một CA gọi workflow_get_current(name) để đọc thân step nó phải thực hiện ngay, mà không phải đọc lại MD thô hay tiến bất kỳ trạng thái nào.","cit":["code_tools.py:13369","code_tools.py:13382","code_tools.py:13383","code_tools.py:13390"],"h":"code_tools.py:13369","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"workflow_list","t":"T1","cat":"workflow","mut":false,"prot":false,"sess":false,"fg":"workflow","eff":"Liệt kê mọi workflow .md trong thư mục workflows toàn cục: glob *.md (13192), đọc và parse từng MD, chỉ nạp run state của nó khi có project_path (13202), và trả name / title / step_count / status (idle/active/complete) / source / path theo từng workflow. Không giá trị trạng thái nào bị ghi. Một side effect filesystem phát sinh được khai báo, không bị tính là lượt ghi: khi có project_path, _wf_load_state tới _wf_project_state_dir (code_tools.py:13007), nơi mkdir thư mục trạng thái workflow (có thể rỗng) ở lượt gọi đầu; với project_path='' mặc định thì thư mục trạng thái không bị chạm tới chút nào.","uc":"Một CA không biết có workflow nào gọi workflow_list trước để khám phá tên, title, số step và trạng thái chạy, rồi hãy gọi workflow_start trên một trong số chúng.","cit":["code_tools.py:13182","code_tools.py:13192","code_tools.py:13202","code_tools.py:13220"],"h":"code_tools.py:13182","wk":"none","der":false,"wr":[],"use":{"b":"light","c":69,"ls":"2026-11-15T00:39:26"}},
{"n":"workflow_read_md","t":"T1","cat":"workflow","mut":false,"prot":false,"sess":false,"fg":"workflow","eff":"Đọc thân MD thô của một workflow theo tên (ưu tiên project, lùi về toàn cục qua _wf_find_md) và trả body_md + title + step_count. Thuần đọc file (open 'r', 13836) + parse trong memory (13841); nó không chạm file trạng thái, thư mục trạng thái hay store nào — là reader workflow duy nhất trong lô này có zero side effect filesystem bất kỳ kiểu nào.","uc":"Một CA cần sửa một workflow thì đọc nó bằng workflow_read_md, chỉnh text MD, rồi lưu kết quả bằng workflow_save_md.","cit":["code_tools.py:13825","code_tools.py:13830","code_tools.py:13836","code_tools.py:13842"],"h":"code_tools.py:13825","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"workflow_status","t":"T1","cat":"workflow","mut":false,"prot":false,"sess":false,"fg":"workflow","eff":"Trả trạng thái workflow đầy đủ BAO GỒM thân step hiện tại (title_blocks + completion_signals) cộng một manifest step gọn và các domain rule đã biết, để CA không cần đọc MD thô. Nạp state qua _wf_load_state (13768), đọc và parse MD (13769-13770), resolve step hiện tại bằng canonical resolver dùng chung (13775). Không giá trị trạng thái nào bị ghi. Một side effect filesystem phát sinh được khai báo, không bị tính là lượt ghi: _wf_load_state (13768) và state_path được show trong response (13793) đều tới _wf_project_state_dir (code_tools.py:13007), nơi mkdir thư mục trạng thái workflow (có thể rỗng) ở lượt gọi đầu. Khác với workflow_status_registry: đây là trạng thái của R35 MD-driven workflow runner, không phải project-registry inspector.","uc":"Một CA kiểm một lượt chạy workflow đang đứng ở đâu — thân step hiện tại, manifest step, cờ paused/complete — mà không tiến hay đổi bất kỳ trạng thái nào, ví dụ sau khi resume session giữa lượt chạy.","cit":["code_tools.py:13757","code_tools.py:13768","code_tools.py:13769","code_tools.py:13793"],"h":"code_tools.py:13757","wk":"none","der":false,"wr":[],"use":{"b":"unused","c":0,"ls":null}},
{"n":"workflow_status_registry","t":"T1","cat":"workflow","mut":false,"prot":false,"sess":false,"fg":"workflow","eff":"Inspector project-registry của T1 (đổi tên từ workflow_status để tránh đụng độ bản R35 MD, docstring 4250-4255): nạp WORKFLOW_STATE.json của dự án (hoặc một state mặc định khi thiếu, 4268-4271), làm giàu nó trong memory bằng trạng thái cache/drift đang sống (đọc cache_delta), trạng thái backup và session id đang hoạt động, đóng dấu updated_at (4306), rồi VÔ ĐIỀU KIỆN ghi lại file bằng _atomic_write_json(path, state, indent=2) (4307) — ở mọi lượt gọi thành công, dù file trạng thái đã tồn tại hay vừa được tạo. _atomic_write_json (code_tools.py:206-210) ghi qua _atomic_write_text, nên đây là một lượt ghi lại file bền vững, không phải chạm memory. Đây là delegate đứng sau lượt ghi class (i) của tool 'state' ở Batch 9 — phán quyết này đọc từ chính thân hàm của nó, không chép từ Batch 9. 'Index' ở đây là index snapshot project-map (INDEX.json), mà thân hàm này CHỈ ĐỌC qua cache_delta / get_cache_status — nó không bị ghi lại ở đây, nên derived_index_writer vẫn false (R54).","uc":"Một CA hỏi trạng thái registry của dự án — trạng thái cache/drift, trạng thái backup, session đang hoạt động — ví dụ để quyết định một lượt write T2 có phải gọi refresh_project_map trước không; mỗi lượt gọi cũng làm tươi file WORKFLOW_STATE.json đã lưu mà nó báo cáo từ đó.","cit":["code_tools.py:4249","code_tools.py:4268","code_tools.py:4307","code_tools.py:4314"],"h":"code_tools.py:4249","wk":"none","der":false,"wr":[{"kind":"file","target":"WORKFLOW_STATE.json (project session artifact path via _workflow_state_path, code_tools.py:925; rewritten unconditionally, code_tools.py:4307)"}],"err":{"c":"i","r":"output-history.md:L159 (R68)","q":"worst label failure: T1 + read + scope none vs unconditional rewrite"},"note":"Record self-contradiction (R68b): registry scope “none” + category [“read”,“t1”,“workflow”] vs unconditional WORKFLOW_STATE.json rewrite; scope should be “stateful”. E-03 corroboration: code_tools.py:394-401.","use":{"b":"unused","c":0,"ls":null}},
{"n":"ocr_map","t":"T1","cat":"t1","mut":false,"prot":false,"sess":false,"fg":"analysis","eff":"Render cùng bản xem trước delegate của OCR nhưng như một bản đồ điều hướng: các file mà một thay đổi thực sự nằm trong đó, sắp theo số dòng đã đổi, với mọi file bị loại được giải trình bằng lý do (thư mục trạng thái runtime, snapshot trạng thái, JSON dữ liệu không-phải-config, hoặc dưới mức tối thiểu 10 dòng) trong excluded_summary. Zero LLM và không đọc provider config nào. Nó trả lời 'thay đổi này nằm ở những file nào', trong khi ocr_review trả lời 'một reviewer sẽ mở gì'.","uc":"Trước một diff 19 file, một agent gọi ocr_map để lấy danh sách xếp hạng các file mang thay đổi (entry đầu là nơi thay đổi nằm) và số file bị loại như nhiễu.","cit":["orchestrator/tools/ocr_review.py:518","orchestrator/tools/ocr_review.py:552","orchestrator/tools/ocr_review.py:573","orchestrator/tools/ocr_review.py:588","orchestrator/tools/ocr_review.py:622"],"h":"orchestrator/tools/ocr_review.py:518","wk":"none","der":false,"wr":[],"use":{"b":"moderate","c":133,"ls":"2026-11-16T15:30:53"}},
{"n":"file_sha256","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Stream byte thô của một file qua SHA-256 theo khối 1 MB và trả hex digest, số byte và mtime, kèm một phép so sánh constant-time tùy chọn chống một expected digest 64-hex. Một kiểm tra race chữ ký fstat/stat từ chối kết quả nếu file đổi trong lúc hash. Thuần đọc: file chỉ được mở 'rb', không ghi gì, và kết quả báo tường minh read_token_seeded=false nên lượt gọi không bao giờ gieo quyền sửa đổi. Registry mutating=false là ĐÚNG. Tool hiện diện như một dòng MISSING được bổ sung ở Phase 5 (nó thiếu trong file tháng 10-2026; Phase 1 inventory v3) và có lượt dùng bằng 0 trong cửa sổ 2026-11-08 → 2026-11-16 (đo lại Wave 19); effect của nó được ghi ở đây mà không claim con số usage nào.","uc":"Sau khi tải xuống hoặc copy một file, một CA gọi file_sha256 kèm digest mong đợi của nhà xuất bản để xác minh file đến nguyên vẹn trước khi dùng nó làm baseline hay input deploy.","cit":["tools_upgrade_p5.py:18","tools_upgrade_p5.py:39","tools_upgrade_p5.py:47","tools_upgrade_p5.py:53"],"h":"tools_upgrade_p5.py:18","wk":"none","der":false,"wr":[],"miss":true,"use":{"b":"unused","c":0,"ls":null}},
{"n":"t4_host_status","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Đọc trạng thái cầu T4-over-HTTP đang sống và các sự thật về host: get_bridge().status_snapshot(agent_id) (loop_alive, loop_epoch, holder, các page của chính người gọi đầy đủ, page của owner khác chỉ dưới dạng số đếm, quarantine_records), cộng session id Windows của daemon (_t4_process_session_id), trạng thái console lock qua quét process tìm explorer.exe/logonui.exe (_t4_console_state), và SERVER_GENERATION. Thuần đọc — chỉ snapshot + liệt kê process, không ghi, không sửa page hay bridge. Tool hiện diện như một dòng MISSING được bổ sung ở Phase 5 (nó thiếu trong file tháng 10-2026; Phase 1 inventory v3).","uc":"Một CA kiểm vòng bridge T4 còn sống không, ai đang giữ nó và console có bị lock không trước khi phát một bước T4 browser, thay vì đoán từ một danh sách page cũ.","cit":["code_tools.py:2076","code_tools.py:2086","code_tools.py:2097","code_tools.py:2100"],"h":"code_tools.py:2076","wk":"none","der":false,"wr":[],"miss":true,"use":{"b":"light","c":49,"ls":"2026-11-14T16:18:46"}},
{"n":"write_status","t":"T1","cat":"read","mut":false,"prot":false,"sess":false,"fg":"ops","eff":"Tra cứu chỉ-đọc trong sổ ghi-commit trả lời 'lượt write của tôi có thực sự commit không?' sau một client-side timeout, mà không phát lại lượt write. Nó dựng đúng truy vấn mà endpoint HTTP GET /api/write_status trả lời: theo operation_id trước (sổ trong memory, nếu không thì sổ SQLite), vốn là tra cứu duy nhất không thể bị nhầm với entry của tool khác; nếu không thì theo đích project_path+file_path, thu hẹp bởi tool khi có đưa. Khi nhiều tool khác nhau cùng chia một target key, nó từ chối bằng WRITE_STATUS_AMBIGUOUS_TARGET thay vì trả bằng dòng mới nhất. Khi recovery sổ còn treo hoặc store bị tranh chấp, nó trả 503 với commit_state unknown và safe_to_retry false — một tra cứu fail không chứng minh lượt write đã fail. Nó không bao giờ ghi và không bao giờ gửi lại lượt write ban đầu.","uc":"Một lượt gọi write_fix timeout phía client; gọi write_status kèm operation_id của nó để biết lượt write đã commit chưa trước khi quyết định retry.","cit":["orchestrator_server.py:19695","orchestrator_server.py:19703","orchestrator_server.py:19567","orchestrator_server.py:19595","orchestrator_server.py:19550"],"h":"orchestrator_server.py:19695","wk":"none","der":false,"wr":[],"miss":true,"use":{"b":"light","c":14,"ls":"2026-11-11T14:34:03"}},
// T2 — Write/Mutate
{"n": "cbm_cycle_point", "t": "T2", "cat": "t1", "mut": true, "prot": true, "sess": true, "fg": "analysis", "eff": "Trước tiên làm mới đồ thị suy dẫn khi nó stale, rồi tính các tín hiệu cycle và quality của dự án từ đồ thị tươi và gắn kết quả auto-index vào. Từ chối với status unavailable khi không xác lập được độ tươi, thay vì báo cáo trên dữ liệu chưa kiểm chứng.", "uc": "Một agent muốn kiểm xem một refactor có gây ra circular import hay không thì gọi cbm_cycle_point và nhận báo cáo cycle hiện tại, với đồ thị được làm mới trước nếu cần.", "cit": ["orchestrator/graph/tools.py:860", "orchestrator/graph/tools.py:876", "orchestrator/graph/tools.py:880"], "h": "orchestrator/graph/tools.py:860", "wk": "conditional", "der": true, "wr": [{"kind": "index", "target": "CBM derived code-graph index", "condition": "only when the index is stale; refuses when freshness cannot be established"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "cbm_dead_code", "t": "T2", "cat": "t1", "mut": true, "prot": true, "sess": true, "fg": "analysis", "eff": "Trước tiên làm mới đồ thị suy dẫn khi nó stale, rồi trả về các ứng viên dead-code mang tính advisory thận trọng (scan Vulture cộng Ruff cho Python, một AST engine cho trường hợp khác), với giới hạn kết quả kẹp trong 1..200 và số đếm thật theo từng category. 'Dead' chỉ có nghĩa là 'no RESOLVED reference in the graph': các lời gọi qua instance receiver (self.fn(), obj.fn()) không tạo cạnh đồ thị, nên mọi phát hiện đều là advisory và response chỉ trỏ tới write_fix sau khi đã cross-check từng mục bằng search_code(mode='callers').", "uc": "Trước một đợt dọn dẹp, một agent gọi cbm_dead_code để liệt kê các symbol không được tham chiếu, rồi xác minh từng ứng viên bằng search_code(mode='callers') trước khi xóa bất cứ gì, vì một method đang sống được gọi qua self.fn() vẫn có thể xuất hiện trong danh sách.", "cit": ["orchestrator/graph/tools.py:452", "orchestrator/graph/tools.py:465", "orchestrator/graph/tools.py:469", "orchestrator/graph/tools.py:484", "orchestrator/graph/tools.py:492"], "h": "orchestrator/graph/tools.py:452", "wk": "conditional", "der": true, "wr": [{"kind": "index", "target": "CBM derived code-graph index", "condition": "only when the index is stale"}], "use": {"b": "rare", "c": 2, "ls": "2026-11-14T22:31:34"}},
{"n": "cbm_get_architecture", "t": "T2", "cat": "t1", "mut": true, "prot": true, "sess": true, "fg": "analysis", "eff": "Trước tiên làm mới đồ thị suy dẫn khi nó stale hoặc thiếu (auto-index dưới write lock, qua _ensure_graph_fresh), rồi trả về tổng quan kiến trúc có giới hạn của dự án, kèm các trần tùy chọn max_files, max_symbols và timeout_s, và gắn kết quả auto-index vào response. Nếu không xác lập được độ tươi thì từ chối với status unavailable thay vì phân tích dữ liệu chưa kiểm chứng.", "uc": "Một agent bị đặt vào một repo lạ gọi cbm_get_architecture để lấy tổng quan module/entry-point có giới hạn; đồ thị được âm thầm re-index trước nếu nó đã stale.", "cit": ["orchestrator/graph/tools.py:417", "orchestrator/graph/tools.py:429", "orchestrator/graph/tools.py:444", "orchestrator/graph/tools.py:446"], "h": "orchestrator/graph/tools.py:417", "wk": "conditional", "der": true, "wr": [{"kind": "index", "target": "CBM derived code-graph index", "condition": "only when the index is stale or has never been indexed; refuses when freshness cannot be established"}], "use": {"b": "moderate", "c": 169, "ls": "2026-11-16T15:33:51"}},
{"n": "cbm_incremental_index", "t": "T2", "cat": "t1", "mut": true, "prot": true, "sess": true, "fg": "analysis", "eff": "Chỉ re-index những file đã thay đổi, được thêm, xóa hoặc đổi tên kể từ lần chạy trước, dưới write lock đồ thị theo từng dự án, và trả về kết quả theo từng file của indexer. Đây là bước làm mới rẻ tiền mà các tool phân tích cbm_* khác tự kích hoạt khi đồ thị stale.", "uc": "Sau khi sửa ba file, một agent gọi cbm_incremental_index để cập nhật đồ thị trong vài giây thay vì chạy một cbm_index đầy đủ.", "cit": ["orchestrator/graph/tools.py:838", "orchestrator/graph/tools.py:849", "orchestrator/graph/tools.py:852"], "h": "orchestrator/graph/tools.py:838", "wk": "direct", "der": true, "wr": [{"kind": "index", "target": "CBM derived code-graph index"}], "use": {"b": "light", "c": 11, "ls": "2026-11-16T06:10:53"}},
{"n": "cbm_index", "t": "T2", "cat": "t1", "mut": true, "prot": true, "sess": true, "fg": "analysis", "eff": "Index một dự án vào đồ thị code suy dẫn: dưới write lock đồ thị theo từng dự án, nó chạy indexer trên dự án (scope core/full), xây nodes, edges và các FTS5 text entry trong graph DB, và trả về kết quả của indexer. File nguồn chỉ được đọc, không bao giờ bị sửa. Trước tiên nó từ chối project_path thiếu hoặc thư mục dự án không tồn tại, thay vì để indexer tạo một thư mục cache rác rồi báo thành công.", "uc": "Sau khi clone một repo, một agent gọi cbm_index một lần để mọi truy vấn cbm_* về sau (callers, dead code, architecture) đọc đồ thị tươi thay vì thất bại hoặc trả lời từ dữ liệu stale.", "cit": ["orchestrator/graph/tools.py:252", "orchestrator/graph/tools.py:269", "orchestrator/graph/tools.py:272"], "h": "orchestrator/graph/tools.py:252", "wk": "direct", "der": true, "wr": [{"kind": "index", "target": "CBM derived code-graph index"}], "use": {"b": "rare", "c": 1, "ls": "2026-11-14T22:30:27"}},
{"n": "cbm_test_gap", "t": "T2", "cat": "t1", "mut": true, "prot": true, "sess": true, "fg": "analysis", "eff": "Trước tiên làm mới đồ thị suy dẫn khi nó stale, rồi dựng báo cáo test-gap tĩnh: những file/hàm production nào được đưa vào mà không có test bao phủ, kèm per-test maps khi được yêu cầu và một architecture view tùy chọn (stability cộng entry reach). Khi có gap, nó gợi ý cbm_quality_gate như bước kế tiếp. Chỉ mang tính advisory: nó không bao giờ viết test.", "uc": "Trước một bản release, một agent gọi cbm_test_gap với các file đã đổi và test của chúng để liệt kê code production chưa được bao phủ, rồi theo gợi ý sang cbm_quality_gate để kiểm ngưỡng coverage.", "cit": ["orchestrator/graph/tools.py:947", "orchestrator/graph/tools.py:974", "orchestrator/graph/tools.py:1010", "orchestrator/graph/tools.py:1026"], "h": "orchestrator/graph/tools.py:947", "wk": "conditional", "der": true, "wr": [{"kind": "index", "target": "CBM derived code-graph index", "condition": "only when the index is stale"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "cbm_what_if", "t": "T2", "cat": "t1", "mut": true, "prot": true, "sess": true, "fg": "analysis", "eff": "Trước tiên làm mới đồ thị suy dẫn khi nó stale, rồi mô phỏng một chuỗi thao tác giả định (add, remove hoặc rename nodes/edges) trên một analysis view trong RAM và báo các metric kết quả; bản thân mô phỏng không bao giờ ghi đồ thị hay source, và kết quả mang wrote:false. Ngữ nghĩa cạnh là 'observed' (cạnh do đồ thị resolve) hoặc 'strict'.", "uc": "Trước khi xóa một module, một agent gọi cbm_what_if với một thao tác remove cho nó để xem những callers và cycles nào sẽ gãy, mà không chạm vào bất kỳ file nào.", "cit": ["orchestrator/graph/tools.py:1227", "orchestrator/graph/tools.py:1265", "orchestrator/graph/tools.py:1269", "orchestrator/graph/tools.py:1282"], "h": "orchestrator/graph/tools.py:1227", "wk": "conditional", "der": true, "wr": [{"kind": "index", "target": "CBM derived code-graph index", "condition": "only when the index is stale"}], "use": {"b": "light", "c": 10, "ls": "2026-11-16T06:11:36"}},
{"n": "ocr_review", "t": "T2", "cat": "t1", "mut": false, "prot": false, "sess": false, "fg": "analysis", "eff": "Chạy OCR reviewer bên ngoài như một subprocess (delegate preview trên một commit hoặc ref range) và trả về những file đã đổi nào đáng review. Zero LLM, zero token. Payload nền bị lược mặc định (chỉ hiển thị số byte của nó); response chain tới ocr_rule như bước kế tiếp.", "uc": "Sau khi stage một thay đổi, một agent gọi ocr_review để lấy danh sách rút gọn các file mà một reviewer thực sự sẽ mở, rồi hỏi ocr_rule checklist áp dụng cho chúng.", "cit": ["orchestrator/tools/ocr_review.py:431", "orchestrator/tools/ocr_review.py:444", "orchestrator/tools/ocr_review.py:448", "orchestrator/tools/ocr_review.py:466"], "h": "orchestrator/tools/ocr_review.py:431", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "ocr_rule", "t": "T2", "cat": "t1", "mut": false, "prot": false, "sess": false, "fg": "analysis", "eff": "Chạy delegate rule subprocess của OCR cho các file repo-relative đã cho và trả về review rule áp dụng cho chúng. Zero LLM. Thân rule bị giữ mặc định (một file .py đo được khoảng 7.9 KB) và báo dưới dạng số byte; compact=true thay bằng một checklist Python cô đọng 7 nhóm, nhưng chỉ cho system Python rule, và luôn khai báo verbatim_ocr_text=false. Yêu cầu cả full text lẫn compact sẽ bị từ chối.", "uc": "Khi đã có danh sách rút gọn file từ ocr_review, một agent gọi ocr_rule(paths=[...], compact=true) để lấy checklist 7 điểm rẻ tiền (boundary, mutable state, errors, resources, performance, concurrency, security) trước khi review.", "cit": ["orchestrator/tools/ocr_review.py:665", "orchestrator/tools/ocr_review.py:687", "orchestrator/tools/ocr_review.py:710", "orchestrator/tools/ocr_review.py:735"], "h": "orchestrator/tools/ocr_review.py:665", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "ocr_scan", "t": "T2", "cat": "t1", "mut": false, "prot": false, "sess": false, "fg": "analysis", "eff": "Chạy scan subprocess của OCR trên một path; với preview_only=true (mặc định) nó chỉ báo những gì một full review sẽ bao quát, nên zero LLM được tiêu. Một LLM scan thật (preview_only=false) bị từ chối với OCR_LLM_NOT_CONFIGURED trừ khi một provider đã được cấu hình trong ~/.opencodereview, nêu đúng các lệnh cấu hình. Output là text, JSON hoặc SARIF cho người hoặc agent đọc, với mỗi JSON list trần 50 mục và các phần lược bỏ được khai báo, không bao giờ im lặng.", "uc": "Một agent muốn xem nhanh phạm vi scan gọi ocr_scan(path='orchestrator/core', preview_only=true) để thấy một full review sẽ bao quát gì; một LLM review đầy đủ là bước riêng, phải cấu hình tường minh.", "cit": ["orchestrator/tools/ocr_review.py:792", "orchestrator/tools/ocr_review.py:818", "orchestrator/tools/ocr_review.py:832", "orchestrator/tools/ocr_review.py:874"], "h": "orchestrator/tools/ocr_review.py:792", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "apply_after_dry_run", "t": "T2", "cat": "write", "mut": true, "prot": true, "sess": true, "fg": "write", "eff": "Áp dụng một write dry-run đã preview trước đó: nạp bản ghi dry-run đã lưu theo dry_run_id, kiểm tra lại hạn hết hiệu lực, confirm_hash, các warning bắt buộc, target-nằm-trong-project và target-không-đổi-kể-từ-preview, rồi chạy payload write_fix hoặc write_new_file đã ghi (chỉ hai tool này được hỗ trợ; mọi thứ khác bị từ chối với DRY_RUN_UNSUPPORTED_TOOL), tùy chọn batch-verify (hoặc revert) kết quả, và xóa bản ghi dry-run.", "uc": "Một CA preview một write_fix lớn với dry_run=true, xem diff trả về, rồi áp dụng đúng preview đó bằng dry_run_id trả về thay vì gửi lại nội dung.", "cit": ["orchestrator/tools/write/core.py:2553", "orchestrator/tools/write/core.py:2570", "orchestrator/tools/write/core.py:2577", "orchestrator/tools/write/core.py:2602", "orchestrator/tools/write/core.py:2616", "orchestrator/tools/write/core.py:2629", "orchestrator/tools/write/core.py:2644"], "h": "orchestrator/tools/write/core.py:2547", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "target file (via delegated write_fix/write_new_file call)", "condition": "patched draft validates clean"}, {"kind": "file", "target": "dry-run record file (deleted)"}], "use": {"b": "rare", "c": 1, "ls": "2026-11-13T00:03:05"}},
{"n": "backup_project", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "deploy", "eff": "Tạo bản ZIP backup có timestamp của một dự án (full repo, hoặc chỉ target_paths khi được đưa) dưới <project-parent>/_orchestrator_backups (hoặc backup_dir), bỏ qua các file không đọc được và symlink, rồi chèn đầu entry vào .orchestrator_cache/BACKUP_MANIFEST.json và cắt tỉa các archive vượt keep_last_n (mặc định 2) — xóa cả ZIP của các entry bị loại khỏi manifest lẫn ZIP mồ côi trong thư mục backup. Từ chối backup toàn bộ thư mục home hoặc gốc ổ đĩa trừ khi target_paths thu hẹp phạm vi, từ chối symlink target và target ngoài root, và trả skipped:true không ghi khi một backup cùng phạm vi trong ngày đã tồn tại và force=False. Lời gọi đồng thời thứ hai cho cùng dự án trả BACKUP_IN_PROGRESS thay vì xếp hàng. Timeout hủy hợp tác và unlink archive dở dang.", "uc": "Trước một refactor lớn, một CA chạy backup_project(project_path, force=True) để cây trước thay đổi khôi phục được dưới dạng ZIP, rồi kiểm backup_status cho entry mới nhất.", "cit": ["code_tools.py:4488", "code_tools.py:4499", "code_tools.py:4542", "code_tools.py:4602", "code_tools.py:4694", "code_tools.py:4775"], "h": "code_tools.py:4488", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "<project-parent>/_orchestrator_backups/<name>_backup_<timestamp>.zip", "condition": "not skipped (no identical-scope backup from today unless force=true) and not already running"}, {"kind": "file", "target": ".orchestrator_cache/BACKUP_MANIFEST.json"}, {"kind": "file", "target": "backup ZIP archives beyond keep_last_n (manifest-dropped entries' ZIPs + orphan ZIPs)"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "backup_status", "t": "T2", "cat": "t2", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Báo cáo chỉ-đọc về manifest backup của dự án: trả status missing khi .orchestrator_cache/BACKUP_MANIFEST.json không tồn tại, ngược lại status available với total_backups, entry mới nhất và mọi entry còn tồn tại trên đĩa, mỗi entry được chú thích trong memory với size_mb và backup_age_hours. Từ chối project_path không phải thư mục thay vì âm thầm báo missing. Nó chỉ chú thích dict manifest đã parse trong memory và không bao giờ ghi ngược lại.", "uc": "Trước một edit mạo hiểm, một CA gọi backup_status để kiểm có backup dùng được (status available, entry mới nhất còn tươi); nếu missing, nó chạy backup_project trước.", "cit": ["code_tools.py:4416", "code_tools.py:4420", "code_tools.py:4427", "code_tools.py:4434", "code_tools.py:4444"], "h": "code_tools.py:4416", "wk": "none", "der": false, "wr": [], "use": {"b": "rare", "c": 3, "ls": "2026-11-13T17:53:30"}},
{"n": "browser_smoke_test", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "test", "eff": "Nạp url do caller cung cấp trong một Chromium headless mới qua tools_v15 open_browser_page (Playwright chromium.launch, context + page mới, page.goto), đọc body text (tối đa 8000 ký tự) cộng tiêu đề trang, và báo liệu expect_text tùy chọn có xuất hiện trong body không. Page, context, browser và Playwright session luôn được đóng trong finally block, nên không trạng thái browser bền nào được giữ sau lời gọi. Hiệu ứng là một process browser-runtime tạm thời: một Chromium process được spawn và JavaScript của trang đã nạp thực thi, đó là lý do cờ registry mutating=true / mutation_scope=process là ĐÚNG (một process spawn thực thi code của URL do caller cung cấp, R63) dù không gì được ghi xuống đĩa, vào DB, hay vào bất kỳ index nào. Không nội dung file, DB row hay index entry nào được ghi.", "uc": "Sau khi start_app khởi động một web app, một CA gọi browser_smoke_test với URL của app và expect_text đặt thành một heading đã biết để chứng minh app thực sự phục vụ trang của nó trong một Chromium headless thật, trước khi báo lượt khởi động là đã xác minh.", "cit": ["tools_upgrade_p5.py:283", "tools_upgrade_p5.py:295", "tools_v15.py:6609", "tools_upgrade_p5.py:316"], "h": "tools_upgrade_p5.py:283", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "headless Chromium process launched by Playwright (tools_v15.py:6609), closed in finally"}, {"kind": "browser-runtime", "target": "ephemeral Chromium page loading the caller-supplied url (closed in finally, tools_upgrade_p5.py:316)"}], "use": {"b": "rare", "c": 5, "ls": "2026-11-15T20:15:50"}},
{"n": "cache_delta", "t": "T2", "cat": "t2", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Tính drift trực tiếp của project-map snapshot index so với đĩa: so hash đã lưu của mỗi entry INDEX.json với hash file memoized gated-by-stat (modified), liệt kê các file đã index mất khỏi đĩa (deleted) và các file quét được trên đĩa vắng mặt khỏi index (added), tùy chọn thu hẹp về một POSIX subtree qua scope. Các caller đồng thời cho cùng project+scope nhập về một executor future đang chạy (singleflight); registry futures chỉ nằm trong memory. Fail loud trên project_path không phải thư mục.", "uc": "Trước khi tin một project map đã cache, một CA gọi cache_delta để biết chính xác những file nào đã thêm, sửa hoặc xóa kể từ khi index được sinh, rồi làm mới map nếu có gì thay đổi.", "cit": ["code_tools.py:4929", "code_tools.py:4939", "code_tools.py:4955", "code_tools.py:4863", "code_tools.py:4884", "code_tools.py:4913"], "h": "code_tools.py:4929", "wk": "none", "der": false, "wr": [], "use": {"b": "rare", "c": 2, "ls": "2026-11-15T02:54:50"}},
{"n": "cep_deploy", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "deploy", "eff": "Nhân bản một repo CEP extension vào thư mục extensions LIVE của Premiere (%APPDATA%/Adobe/CEP/extensions/<name>): xác thực CSXS/manifest.xml trước (trừ khi validate_first=false), rồi copy từng file (bộ curated mặc định: manifest + html/js/jsx/css/json/png, hoặc một tập con tường minh), backup mỗi file LIVE đã tồn tại sang <file>.bak_<timestamp> và xác minh bản copy bằng SHA-256. Báo per-file sha256, dst_existed, hash_verified và đường dẫn backup; ok=false khi bất kỳ file nào lỗi. Write đi tới thư mục LIVE ngoài dự án; project-map snapshot index không được cập nhật. File .bak không bao giờ bị cắt tỉa; các lần deploy lặp tích lũy backup có timestamp.", "uc": "Sau khi sửa một panel CEP Premiere, một CA chạy cep_deploy để copy các file đã đổi vào thư mục extensions LIVE (kèm backup từng file) để mở lại panel trong Premiere nạp code mới.", "cit": ["code_tools.py:17235", "code_tools.py:17247", "code_tools.py:17268", "code_tools.py:17269", "code_tools.py:17270"], "h": "code_tools.py:17235", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "LIVE extensions folder %APPDATA%/Adobe/CEP/extensions/<name>/<rel> (per deployed file)"}, {"kind": "file", "target": "<LIVE file>.bak_<timestamp> per-file backup of each overwritten LIVE file"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "cleanup_test_session", "t": "T2", "cat": "session", "mut": true, "prot": true, "sess": true, "fg": "session", "eff": "Dọn dẹp test một lời gọi gồm ba bước: (1) đóng trang CDP khi page_id được đưa (browser.close, Playwright stop, lấy entry _BROWSER_PAGES ra); (2) force-kill các process liệt kê bằng taskkill /F /IM /T — mặc định electron.exe cộng chrome.exe, tức TẤT CẢ instance Electron và Chrome trên máy; (3) đóng session ToolA cho project_path (bật mặc định). Tín hiệu idle-guard từ chối cleanup khi target vẫn bận trừ khi idle_guard_force=true, và một guard exception bỏ qua guard (fail-open). Session không tìm thấy được tính là đã kill.", "uc": "Khi kết thúc một lượt chạy test UI, một CA gọi cleanup_test_session để đóng trang test, kill các process Electron và Chrome test, và đóng session trong một lời gọi.", "cit": ["tools_v15.py:4229", "tools_v15.py:4239", "tools_v15.py:4283", "tools_v15.py:4292", "tools_v15.py:4299", "tools_v15.py:4301", "tools_v15.py:4308", "tools_v15.py:4313"], "h": "tools_v15.py:4207", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "electron.exe + chrome.exe by default (or listed processes)"}, {"kind": "browser-runtime", "target": "browser page in _BROWSER_PAGES (if page_id given)"}, {"kind": "sqlite", "target": "sessions.db via close_session (if close_session_after and project_path)"}], "use": {"b": "rare", "c": 1, "ls": "2026-11-10T17:17:31"}},
{"n": "copy_file", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "deploy", "eff": "Copy một file src->dst kèm xác minh SHA-256: giải đường dẫn relative theo project_path, từ chối source thiếu, áp chính sách ghi xroot lên đích, tạo thư mục cha, backup dst đã tồn tại sang .bak có timestamp khi backup_existing, copy các byte, và chỉ báo ok khi hash source và đích khớp.", "uc": "Nhân bản một file template hoặc config vào một vị trí dự án mới và xác nhận bản copy giống nhau từng byte từ sha256 trả về. (plausible-but-unverified)", "cit": ["orchestrator/tools/write/copy_file.py:25", "orchestrator/tools/write/copy_file.py:44", "orchestrator/tools/write/copy_file.py:65", "orchestrator/tools/write/copy_file.py:69", "orchestrator/tools/write/copy_file.py:70"], "h": "orchestrator/tools/write/copy_file.py:5", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "destination file"}, {"kind": "file", "target": "timestamped .bak of an overwritten dst"}, {"kind": "file", "target": "xroot intent/result ledger entries"}], "use": {"b": "light", "c": 22, "ls": "2026-11-13T10:06:22"}},
{"n": "council_config_sync", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "council", "eff": "Xác minh các doc council suy từ SOT (qua council_config_diff) và, mặc định (run_sync_rule=true), đồng bộ core rule chuẩn tới mọi CA đã đăng ký qua manage_agent(action='sync_rule', target='all'), thứ viết lại file rule toàn cục của từng agent (dòng persona được giữ, backup được tạo, đã validate) trừ khi dry_run. Trả về drift diff cộng cờ sync_rule ran/ok; với run_sync_rule=false nó chỉ báo diff. Không cost chain nào được sinh.", "uc": "Sau khi sửa drift routing council, một CA gọi council_config_sync để đẩy core rule chuẩn vào file rule của mọi agent đã đăng ký trong một lời gọi thay vì sửa từng cái bằng tay.", "cit": ["code_tools.py:19687", "code_tools.py:19693", "code_tools.py:19697", "code_tools.py:19344", "code_tools.py:17704"], "h": "code_tools.py:19687", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "each registered agent's global-rule file (managed body rewrite, persona preserved)", "condition": "run_sync_rule=true (default); skipped when run_sync_rule=false"}, {"kind": "file", "target": "per-rule-file backup created by _ma_backup before each rewrite", "condition": "run_sync_rule=true (default)"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "create_benchmark_request", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "ops", "eff": "Ghi <project>/BENCHMARK_REQUEST.md từ ý tưởng của user (một template liệt kê các input đã được user duyệt mà CA cần trước khi build: link benchmark, đường dẫn UI.md/library, mẫu dữ liệu, ràng buộc assets/brand, prefer stack, must-haves, out-of-scope) qua atomic write, rồi làm mới entry của file trong project-map snapshot index qua _update_cache_after_write. Ghi đè mọi BENCHMARK_REQUEST.md hiện có. Fail loud khi đường dẫn dự án không tồn tại.", "uc": "Đầu một lượt build, một CA gọi create_benchmark_request với ý tưởng của user để tạo file benchmark-request nói cho user biết chính xác những input đã duyệt nào cần trước bất kỳ việc build nào.", "cit": ["code_tools.py:7063", "code_tools.py:7088", "code_tools.py:7089", "code_tools.py:7090", "code_tools.py:732"], "h": "code_tools.py:7063", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "<project>/BENCHMARK_REQUEST.md"}, {"kind": "index", "target": "project-map snapshot index INDEX.json entry for BENCHMARK_REQUEST.md (via _update_cache_after_write -> surgical_mapper.update_file_cache)"}], "note": "Registry scope-label anomaly (R57i): scope “audit” vs “file_artifact” label; the INDEX.json write is carried in writes + effect, not as derived_index_writer (R54).", "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "create_build_context", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "ops", "eff": "Ghi <project>/BUILD_CONTEXT.md từ ý tưởng của user + các input đã duyệt (template: user idea, approved inputs, CA rules — chỉ build từ context này và file/đường dẫn đã user duyệt, không crawl internet, không dùng tool AI/model/proxy nội bộ, hỏi trước khi cài dependency, xác minh bằng Orchestrator tests và cache status sau khi ghi) qua atomic write, rồi làm mới entry của file trong project-map snapshot index qua _update_cache_after_write. Ghi đè mọi BUILD_CONTEXT.md hiện có.", "uc": "Khi user đã duyệt các input, một CA gọi create_build_context để đóng băng chúng vào BUILD_CONTEXT.md để mọi bước build về sau chỉ làm việc từ tài liệu đã duyệt.", "cit": ["code_tools.py:7094", "code_tools.py:7118", "code_tools.py:7119", "code_tools.py:7120", "code_tools.py:732"], "h": "code_tools.py:7094", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "<project>/BUILD_CONTEXT.md"}, {"kind": "index", "target": "project-map snapshot index INDEX.json entry for BUILD_CONTEXT.md (via _update_cache_after_write -> surgical_mapper.update_file_cache)"}], "note": "Registry scope-label anomaly (R57i): scope “audit” vs “file_artifact” label; the INDEX.json write is carried in writes + effect, not as derived_index_writer (R54).", "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "create_folder", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "ops", "eff": "Tạo thư mục idempotent (mkdir -p): giải đường dẫn (đường dẫn relative theo project_path khi được đưa), trả success với created=false khi thư mục đã tồn tại, lỗi NOT_A_DIRECTORY khi đường dẫn tồn tại dạng file và PARENT_NOT_FOUND khi recursive=false và thư mục cha thiếu. Tạo cây cha khi recursive=true (mặc định). Chỉ tạo thư mục — nó KHÔNG làm mới project-map snapshot index (không gọi _update_cache_after_write). Bản thân việc tạo thư mục (mkdir) không chứa nội dung và idempotent — không phải durable write (R66).", "uc": "Trước khi ghi các file output, một CA gọi create_folder cho thư mục đích (vd thư mục output footage) thay vì một script-file mkdir 9 round-trip.", "cit": ["code_tools.py:8359", "code_tools.py:8381", "code_tools.py:8383", "code_tools.py:8389"], "h": "code_tools.py:8359", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "create_folders", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "ops", "eff": "create_folder dạng batch: gọi create_folder một lần cho mỗi path trong paths[] (cùng cách xử lý recursive/project_path), thu kết quả per-path, trả ok=true chỉ khi mọi path thành công cộng số đếm created; một path lỗi (không phải chuỗi, thiếu cha, file đã tồn tại) làm entry đó fail mà không hủy những entry khác. Đòi một danh sách chuỗi không rỗng. Bản thân việc tạo các thư mục (mkdir) không chứa nội dung và idempotent — không phải durable write (R66). Giống create_folder, nó không đồng bộ project-map snapshot INDEX.json.", "uc": "Khi một workflow cần nhiều thư mục output cùng lúc, một CA gọi create_folders với cả danh sách và nhận kết quả per-folder trong một lời gọi thay vì một lời gọi mỗi folder.", "cit": ["code_tools.py:8402", "code_tools.py:8408", "code_tools.py:8426", "code_tools.py:8433"], "h": "code_tools.py:8402", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "delete_file", "t": "T2", "cat": "write", "mut": true, "prot": true, "sess": true, "fg": "write", "eff": "Xóa vĩnh viễn một file: từ chối đường dẫn relative không có project_path, từ chối xóa các file lifecycle restart-control, từ chối đường dẫn thiếu và không-phải-file, rồi unlink file và đồng bộ project-map snapshot cache cho nó. Không bản backup nào được tạo ở bất kỳ đâu trong thân handler này, nên unlink là chung cuộc. (Destructive-adjacent: chỉ text, không thực thi.)", "uc": "Gỡ một file sinh ra đã stale khỏi một dự án sau khi xác nhận nó không có consumer nào. (plausible-but-unverified)", "cit": ["orchestrator/tools/write/core.py:2659", "orchestrator/tools/write/core.py:2677", "orchestrator/tools/write/core.py:2689", "orchestrator/tools/write/core.py:2694", "orchestrator/tools/write/core.py:2704"], "h": "orchestrator/tools/write/core.py:2652", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "file deleted (permanent, no backup)"}, {"kind": "index", "target": "project-map snapshot cache entry"}], "note": "Errata note (R32f): the effect cites a phantom helper _is_ephemeral_temp_path — repo-wide search finds only the comment at core.py:2654-2657; no such function exists.", "use": {"b": "light", "c": 88, "ls": "2026-11-14T20:06:36"}},
{"n": "fill_value", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Fill DOM an toàn transport kèm verify. encoding='plain' truyền giá trị nguyên trạng qua Playwright .fill; encoding='base64' giải mã bên trong browser (atob + TextDecoder) và đặt el.value/textContent, rồi dispatch các input + change event bubbling, giữ nguyên UNC path, UTF-8, và text nhiều dòng mà JSON escaping sẽ làm hỏng. Với verify=true (mặc định) nó đọc lại giá trị element và báo matches_intent cùng chi tiết drift khi write không đáp đúng ý.", "uc": "Để dán một đường dẫn Windows có backslash vào field đích mà không bị JSON stripping, một CA gọi fill_value(page_id='pg1', selector='#targetPath', value='<base64>', encoding='base64') và kiểm matches_intent=true.", "cit": ["tools_v15.py:6142", "tools_v15.py:6166", "tools_v15.py:6191", "tools_v15.py:6184", "tools_v15.py:6201"], "h": "tools_v15.py:6142", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "first element matching selector in page"}], "note": "Tier-split anomaly (R42/R51f): fill_value is T2 while fill is T4 and read_value is T1 — unexplained, carried as an anomaly.", "use": {"b": "rare", "c": 1, "ls": "2026-11-10T16:57:42"}},
{"n": "generate_project_map", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "ops", "eff": "Xây lại (hoặc bỏ qua) project-map snapshot cho một dự án. Callable mỏng ủy quyền cho _project_map_build với surgical_mapper.generate_project_map là builder. Với force=false mặc định nó thăm độ tươi trước và BỎ QUA việc xây lại khi snapshot còn tươi (chỉ một timestamp _mark_index_refreshed trong memory); nó xây lại khi force=true hoặc snapshot stale/missing, ghi project-map snapshot index INDEX.json cộng PROJECT_MAP.md + PROJECT_CONTEXT.md (+ search snapshot) qua builder trong một worker thread. 'Index' ở đây là project-map snapshot index (INDEX.json), KHÔNG phải CBM derived code graph DB.", "uc": "Khi cache_status không ok, một CA chạy generate_project_map một lần để xây lại snapshot INDEX.json của dự án, rồi dùng query_index trên index tươi thay vì blind file probe.", "cit": ["code_tools.py:2004", "code_tools.py:1979", "code_tools.py:1988", "code_tools.py:1993", "code_tools.py:1853"], "h": "code_tools.py:2004", "wk": "none", "der": false, "wr": [{"kind": "index", "target": "project-map snapshot index INDEX.json (via surgical_mapper.generate_project_map)", "condition": "force=true or snapshot stale/missing; skipped when fresh and force=false"}, {"kind": "file", "target": "PROJECT_MAP.md + PROJECT_CONTEXT.md (+ search snapshot) in the project cache dir", "condition": "same as the INDEX.json rebuild"}], "note": "MEDIUM confidence (R60d): write inventory rests on the artifacts note + delegate reads; surgical_mapper internals unread (R23).", "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "git_commit", "t": "T2", "cat": "git", "mut": true, "prot": true, "sess": true, "fg": "git", "eff": "Commit staged index của một git repo (git argv-only, không shell) qua executor lane. Nó đòi message không rỗng dài tối đa 4000 ký tự, từ chối khi có conflict, và đòi expected_staged_hash khớp staged_hash trực tiếp (hoặc opt-out literal auto) như optimistic lock chống thay đổi index đồng thời. Nó từ chối NOTHING_STAGED trừ khi allow_empty, từ chối EOL-only churn trong staged diff trừ khi allow_eol_churn, và đòi git user.name/user.email đã được cấu hình. Khi thành công nó chạy git commit và trả hash HEAD mới, trạng thái post-commit, và danh sách file đã commit. Với ocr=true (mặc định) một ocr_review zero-LLM của phạm vi commit mới được gắn sau đó; review không bao giờ làm fail hay un-commit commit.", "uc": "Sau khi git_stage trả after.staged_hash, commit với hash đó làm expected_staged_hash để một thay đổi index đồng thời fail loud thay vì commit một bất ngờ.", "cit": ["orchestrator_server.py:12621", "orchestrator_server.py:12622", "orchestrator/git_tools.py:521", "orchestrator/git_tools.py:543", "orchestrator/git_tools.py:596", "orchestrator/git_tools.py:15"], "h": "orchestrator_server.py:12621", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "git commit object + HEAD move (repository history); git index unchanged in content"}], "use": {"b": "light", "c": 78, "ls": "2026-11-15T09:17:13"}},
{"n": "git_stage", "t": "T2", "cat": "git", "mut": true, "prot": true, "sess": true, "fg": "git", "eff": "Stage các path vào git index (git add argv-only, không shell) qua executor lane. Nó áp optimistic lock expected_state_hash tùy chọn, nhận paths hoặc paths_file (không bao giờ cả hai), và luôn từ chối allow_all: caller phải nêu path tường minh. Với mọi path đã stage có HEAD blob đồng nhất CRLF, nó stage lại các byte worktree dạng CRLF để một thay đổi một dòng không bị đọc thành EOL churn cả file. Response mang hash và số đếm before/after cộng gợi ý rằng after.staged_hash chính là thứ git_commit muốn làm expected_staged_hash, nên không cần lời gọi git_status thứ hai. Với ocr=true (mặc định) một ocr_review zero-LLM của cây chưa commit được gắn sau đó; review không bao giờ làm fail stage.", "uc": "Stage hai file đã sửa, rồi đưa thẳng after.staged_hash trả về vào git_commit làm optimistic lock của nó.", "cit": ["orchestrator_server.py:12608", "orchestrator_server.py:12614", "orchestrator/git_tools.py:433", "orchestrator/git_tools.py:440", "orchestrator/git_tools.py:469", "orchestrator/git_tools.py:15"], "h": "orchestrator_server.py:12608", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "git index entries (git add) for the named paths"}], "use": {"b": "light", "c": 70, "ls": "2026-11-15T09:16:51"}},
{"n": "git_unstage", "t": "T2", "cat": "git", "mut": true, "prot": true, "sess": true, "fg": "git", "eff": "Loại các path khỏi git index (git restore --staged, argv-only, không shell) qua executor lane; bản thân các file worktree không bị chạm. Nó áp optimistic lock expected_staged_hash tùy chọn, nhận paths hoặc paths_file (không bao giờ cả hai), và luôn từ chối allow_all: caller phải nêu path tường minh. Response mang trạng thái after (snapshot status tươi). Nó không bao giờ commit và không bao giờ xóa nội dung worktree.", "uc": "Một file bị stage nhầm; unstage nó để commit tiếp theo không bao gồm nó, mà không chạm file trên đĩa.", "cit": ["orchestrator_server.py:12620", "orchestrator/git_tools.py:474", "orchestrator/git_tools.py:481", "orchestrator/git_tools.py:483", "orchestrator/git_tools.py:15"], "h": "orchestrator_server.py:12620", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "git index entries removed (git restore --staged) for the named paths"}], "use": {"b": "rare", "c": 1, "ls": "2026-11-11T13:55:01"}},
{"n": "http_check", "t": "T2", "cat": "t2", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Phát một HTTP request có giới hạn tới url do caller cung cấp (mặc định GET, timeout 10 s, đọc body trần 20000 byte và preview 5000 ký tự) và trả status, thời gian trôi, content-type cùng một đoạn body; ok chỉ true khi status khớp expect_status và text_contains, khi được đưa, xuất hiện trong body. Với method POST và json_body, lời gọi gửi một JSON request body tới target. Không file cục bộ, DB row, process hay index entry nào được ghi. Bản thân lời gọi mạng là một external send và được nêu ở đây, không giấu: một GET probe không ghi gì ở bất kỳ đâu, trong khi POST kèm body là một request mà server đích có thể hành động theo.", "uc": "Sau khi start_app báo một health_url, một CA gọi http_check tới URL đó với expect_status 200 và text_contains đặt thành một health marker đã biết để chứng minh app thực sự đang trả lời trước khi tuyên bố lượt khởi động healthy.", "cit": ["tools_upgrade_p5.py:216", "tools_upgrade_p5.py:242", "tools_upgrade_p5.py:247", "tools_upgrade_p5.py:263"], "h": "tools_upgrade_p5.py:216", "wk": "none", "der": false, "wr": [], "err": {"c": "ii", "r": "output-history.md:L161 (R70)", "q": "mutating covers local writes AND external sends; POST + json_body capability (R70)"}, "use": {"b": "rare", "c": 2, "ls": "2026-11-10T18:15:57"}},
{"n": "kill_process", "t": "T2", "cat": "runtime", "mut": true, "prot": true, "sess": false, "fg": "ops", "eff": "Kết thúc các OS process theo PID hoặc theo substring match trên tên process: force=true (mặc định) gửi kill, force=false gửi terminate, chờ tối đa 5 giây mỗi process để thoát, và cuối cùng thu hồi pid khỏi registry background-process của ToolA. Một name match kill MỌI process có tên chứa substring — không bước xác nhận và không allow-list trong thân. Từ chối khi không pid lẫn process_name được đưa; một kill một phần trả ok:false với PROCESS_KILL_PARTIAL_FAILURE. (Destructive-adjacent: chỉ text, không thực thi.)", "uc": "Kill một child process treo runaway theo PID sau một lượt chạy test bị timeout. (plausible-but-unverified)", "cit": ["orchestrator/session/process.py:29", "orchestrator/session/process.py:39", "orchestrator/session/process.py:44", "orchestrator/session/process.py:64", "orchestrator/session/process.py:74"], "h": "orchestrator/session/process.py:15", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "OS process(es) terminated"}], "use": {"b": "light", "c": 20, "ls": "2026-11-11T22:00:28"}},
{"n": "mcp_pack_generate", "t": "T2", "cat": "mcp", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Sinh, liệt kê, test, cài đặt, hoặc gỡ các artifact MCP pack (một stdio MCP server cộng hermes fragment cộng pack.json) cho một pack id (builtin toolq hoặc council, hoặc một id tùy chỉnh), tùy chọn per-CA để mỗi CA nhận key MCP server riêng (<pack>__<ca>) nhằm cách ly. Wrapper chỉ chuyển tiếp đối số tới orchestrator.mcp_packs.generator.mcp_pack_generate; action router ở đó chấp nhận generate, list_packs, test_pack, install_hermes, install_targets, uninstall_hermes, remove_pack, và uninstall_targets, và từ chối action lạ. Mặc định chịu tải: dry_run=true, nên generate chỉ trả preview (danh sách file, server byte và sha256, hermes fragment) và không ghi gì; chỉ dry_run=false ghi bốn file (server/mcp_stdio.py, pack.json, hermes_fragment.yaml, ca_install_notes.md). test_pack từ chối PACK_NOT_FOUND cho tới khi một pack đã được sinh với dry_run=false. remove_pack chỉ xóa file với delete_files=true.", "uc": "Preview một MCP pack per-CA cho ToolQ với dry_run=true để kiểm danh sách file và hash server, rồi chạy lại với dry_run=false để ghi pack trước khi đấu nó vào Hermes.", "cit": ["orchestrator_server.py:14466", "orchestrator_server.py:14490", "orchestrator/mcp_packs/generator.py:1020", "orchestrator/mcp_packs/generator.py:1040", "orchestrator/mcp_packs/generator.py:556", "orchestrator/mcp_packs/generator.py:569"], "h": "orchestrator_server.py:14466", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "pack files (server/mcp_stdio.py, pack.json, hermes_fragment.yaml, ca_install_notes.md)", "condition": "dry_run=false"}], "err": {"c": "ii", "r": "output-history.md:L112 (R21)", "q": "writes only when dry_run=false; default writes nothing"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "post_write_batch_verify", "t": "T2", "cat": "t2", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Chạy bundle thẩm định chuẩn non-mutating sau một write batch: cho từng file đã đổi gọi validate_file + (với file không phải binary) validate_encoding + (với .py/.js/.mjs/.cjs/.ts/.tsx) validate_symbols, rồi cache_delta một lần khi run_cache_delta=true (cả ba cờ đều mặc định true). Trả về overall_status pass/fail kèm kết quả của từng tool và failed_checks. Pure read — các delegate đã được truy vết một mức xuống dưới (cache_delta đã được xác nhận pure read ở Batch 7; các write duy nhất của validate_file là những bản sao tempfile tạm cho node --check/esbuild, được tạo và unlink ngay bên trong lần gọi).", "uc": "Sau một loạt lời gọi write_fix, một CA chạy post_write_batch_verify trên các file đã đổi để lấy các kiểm tra syntax, encoding, symbol và cache-delta trong một phán quyết pass/fail duy nhất.", "cit": ["code_tools.py:15436", "code_tools.py:15485", "code_tools.py:15490", "code_tools.py:15500", "code_tools.py:15517"], "h": "code_tools.py:15436", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "project_map", "t": "T2", "cat": "t2", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Dispatcher project-map hợp nhất. Mặc định mode=status gọi cache_status (pure read chỉ mục snapshot project-map INDEX.json + cache_delta). Với mode=generate nó gọi generate_project_map và với mode=refresh nó gọi refresh_project_map, cả hai đều đi qua _project_map_build: với force=false (mặc định) việc rebuild bị BỎ QUA khi snapshot còn fresh (chỉ cập nhật timestamp _mark_index_refreshed trong memory), và nó rebuild (ghi lại chỉ mục snapshot project-map INDEX.json cùng PROJECT_MAP.md + PROJECT_CONTEXT.md + search snapshot) khi force=true hoặc snapshot stale/thiếu. 'Index' ở đây là chỉ mục snapshot project-map (INDEX.json), KHÔNG PHẢI DB code graph dẫn xuất của CBM.", "uc": "Một CA kiểm tra độ tươi của map dự án bằng project_map(mode=status); chỉ khi tool báo stale nó mới gọi chính tool đó với mode=refresh để rebuild snapshot, thay vì gọi refresh_project_map trực tiếp.", "cit": ["code_tools.py:16932", "code_tools.py:16940", "code_tools.py:16944", "code_tools.py:16959", "code_tools.py:1979"], "h": "code_tools.py:16932", "wk": "none", "der": false, "wr": [{"kind": "index", "target": "project-map snapshot index INDEX.json (via generate_project_map / refresh_project_map)", "condition": "mode=generate|refresh AND (force=true or snapshot stale/missing); default mode=status never writes"}, {"kind": "file", "target": "PROJECT_MAP.md + PROJECT_CONTEXT.md (+ search snapshot) in the project cache dir", "condition": "same as the INDEX.json rebuild"}], "err": {"c": "ii", "r": "output-history.md:L156 (R65)", "q": "write needs only opt-in mode=generate|refresh; default mode=status writes nothing"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "refresh_project_map", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "ops", "eff": "Rebuild (hoặc bỏ qua) snapshot project-map của một dự án. Callable mỏng delegate xuống _project_map_build với builder là surgical_mapper.refresh_project_map — và builder đó CHÍNH LÀ generate_project_map (surgical_mapper.py:1456-1460 delegate sang nó), nên refresh thực chất là một rebuild đầy đủ dưới _pmap_lock per-project: _reindex_from_disk (ghi lại chỉ mục snapshot project-map INDEX.json), _write_project_map_markdown (PROJECT_MAP.md), _write_project_context_markdown (PROJECT_CONTEXT.md), _publish_search_snapshot. Với force=false (mặc định) nó probe độ tươi trước và BỎ QUA rebuild khi snapshot còn fresh (chỉ có timestamp _mark_index_refreshed trong memory, code_tools.py:1824-1830); nó rebuild khi force=true hoặc snapshot stale/thiếu. 'Index' ở đây là chỉ mục snapshot project-map (INDEX.json), KHÔNG PHẢI DB code graph dẫn xuất của CBM.", "uc": "Khi cache_status báo project map đã stale, một CA chạy refresh_project_map một lần để rebuild snapshot INDEX.json của dự án, rồi dùng query_index trên chỉ mục mới thay vì dò file mù quáng.", "cit": ["code_tools.py:2012", "code_tools.py:2014", "code_tools.py:1979", "code_tools.py:1988", "surgical_mapper.py:1456", "surgical_mapper.py:1447"], "h": "code_tools.py:2012", "wk": "none", "der": false, "wr": [{"kind": "index", "target": "project-map snapshot index INDEX.json (via surgical_mapper.refresh_project_map = generate_project_map)", "condition": "force=true or snapshot stale/missing; skipped when fresh and force=false"}, {"kind": "file", "target": "PROJECT_MAP.md + PROJECT_CONTEXT.md (+ search snapshot) in the project cache dir", "condition": "same as the INDEX.json rebuild"}], "use": {"b": "rare", "c": 5, "ls": "2026-11-14T15:31:24"}},
{"n": "replace_between_anchors", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "write", "eff": "Patch file theo anchor, loại bỏ sự mong manh của số dòng: handler này là wrapper mỏng delegate toàn bộ xuống _r43_replace_between_anchors, vốn giải dòng ĐẦU TIÊN chứa mỗi anchor (substring, hoặc regex khi *_is_regex được đặt; end_anchor_inclusive quyết định dòng cuối có bị thay thế hay không), báo lỗi ANCHOR_NOT_FOUND khi một anchor thiếu hoặc sai thứ tự, rồi gọi write_fix — tool ghi lại file atomic kèm một bản backup. dry_run xuyên qua, nên dry_run=true không ghi gì.", "uc": "Một CA thay toàn bộ thân hàm nằm giữa hai comment marker duy nhất ('# BEGIN HANDLER' / '# END HANDLER') mà không cần biết số dòng hiện tại đang dịch chuyển của hàm đó.", "cit": ["tools_v15.py:797", "tools_v15.py:740", "tools_v15.py:748", "tools_v15.py:768", "orchestrator/tools/write/core.py:1897"], "h": "tools_v15.py:779", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "file_path (via write_fix atomic rewrite + .bak backup)", "condition": "dry_run=false (default writes)"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "restart_orchestrator_via_bat", "t": "T2", "cat": "runtime", "mut": true, "prot": true, "sess": false, "fg": "ops", "eff": "Khởi động lại chính process orchestrator ToolA ([A]) qua script [A:S] chuẩn start_orchestrator.bat — không phải reload config, không phải restart ToolQ, một worker, hay bất kỳ service nào khác. Wrapper trước hết kiểm tra work đang in-flight: work live (fresh) chặn restart với RESTART_DRAIN_REQUIRED trừ khi nó stale/hung hoặc caller truyền force_drain kèm một reason không rỗng; force_drain không có reason bị từ chối. Sau đó nó publish một restart snapshot đúng một lần và delegate xuống tools_v15.restart_orchestrator_via_bat với generation hiện tại, hàm này chạy một pre-bat guard (nếu bị chặn thì trả RESTART_GUARD_BLOCKED) và spawn một helper ẩn (venv312 python -m orchestrator.lifecycle.via_bat_helper, CREATE_NO_WINDOW, process group riêng) chờ delay_seconds (clamp 1..10, mặc định 2) rồi chạy start_orchestrator.bat, script giết pythonw cũ và khởi động lại [A] ở High-IL. Lời gọi return ngay với status restart_queued; chuỗi đó tự nó không chứng minh gì — caller phải poll /health cho tới khi nó trả lời và xác minh generation/PID đã đổi.", "uc": "Sau một đợt deploy code ToolA, gọi tool không tham số, chờ delay+8s, rồi poll /health cho tới khi nó trả lời và xác nhận generation cùng PID đã đổi trước khi tiếp tục công việc.", "cit": ["orchestrator_server.py:13949", "orchestrator_server.py:13972", "orchestrator_server.py:13988", "tools_v15.py:7997", "tools_v15.py:8141"], "h": "orchestrator_server.py:13949", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "spawns a hidden helper that runs start_orchestrator.bat (kills old pythonw, relaunches [A])"}], "use": {"b": "light", "c": 36, "ls": "2026-11-13T17:31:37"}},
{"n": "ruff_fix", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "write", "eff": "Xem trước hoặc áp dụng các auto-fix của Ruff (ruff check --fix với một lựa chọn rule cố định cộng các policy rule của dự án) trên một file hoặc thư mục bên trong dự án; subprocess ruff chạy với timeout 120s. Preview trả về diff; apply trước hết backup mọi file đích vào một temp dir, thẩm định từng file đã đổi sau đó, và auto-revert khi ruff thất bại hoặc thẩm định thất bại. Nó từ chối đường dẫn ngoài dự án, và từ chối apply khi preserve_hash=true làm mất hiệu lực của một pinned artifact hash.", "uc": "Một agent gọi ruff_fix(path='orchestrator/core', apply=false) để xem trước diff lint, rồi chạy lại với apply=true để fix và tự thẩm định các thay đổi.", "cit": ["orchestrator/graph/tools.py:502", "orchestrator/graph/tools.py:516", "orchestrator/graph/tools.py:524", "orchestrator/graph/tools.py:560", "orchestrator/graph/tools.py:563", "orchestrator/graph/tools.py:606", "orchestrator/graph/tools.py:616"], "h": "orchestrator/graph/tools.py:502", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "source files", "condition": "apply=true"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "run_node_script", "t": "T2", "cat": "runtime", "mut": true, "prot": true, "sess": true, "fg": "runtime", "eff": "Wrapper hẹp: ghi nguồn JS do caller cung cấp thành một artifact .js (.orchestrator_cache/test_scripts/<label>_<hash16>.js, UTF-8) và chạy nó bằng `node <temp.js>` out-of-process, thu stdout/stderr với trần 4000/2000 ký tự. Toàn bộ hành vi (CASE-2008 destructive scan + confirm_destructive gate + pre-run backup + dry_run preview) đều kế thừa từ run_project_test_script. Effects của script được spawn là của caller; bản thân tool chỉ ghi artifact script và spawn process.", "uc": "Một CA chạy một probe Node nhiều dòng (ví dụ parse một file JSON có đường dẫn Unicode) qua run_node_script thay vì `node -e` inline, thứ bị các transport guard R46/R49 chặn.", "cit": ["code_tools.py:12453", "code_tools.py:12473", "code_tools.py:12478", "code_tools.py:10061", "code_tools.py:10054"], "h": "code_tools.py:12453", "wk": "none", "der": false, "wr": [{"kind": "file", "target": ".orchestrator_cache/test_scripts/<label>_<hash16>.js script artifact (UTF-8, via run_project_test_script)"}, {"kind": "process", "target": "spawned [node, <script_path>] child process (via run_project_test_script; Popen, CREATE_NO_WINDOW on Windows)"}, {"kind": "file", "target": "pre-run project backup (via backup_project, through run_project_test_script)", "condition": "script body scanned destructive AND no backup manifest today AND skip_backup=false"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "run_project_command", "t": "T2", "cat": "runtime", "mut": true, "prot": true, "sess": true, "fg": "runtime", "eff": "Thực thi một command do caller cung cấp out-of-process với cwd đặt bằng project root: shell_mode auto/cmd/powershell/exec/posix, giải mã command base64, CASE-2008 destructive scan kèm confirm_destructive gate + pre-run backup (trừ khi skip_backup), dry_run preview (không gì chạy), và các transport guard cho PowerShell-inline (dài>800, >>, here-strings, nhiều dòng, mật độ quote, non-ASCII, tổ hợp regex/JSON/UNC) tự động route dạng list `powershell -Command <body>` sang run_project_test_script (code_tools.py:5914) và các node inline body cũng vậy (code_tools.py:6053). Đường dẫn mặc định spawn qua subprocess.Popen (code_tools.py:6615); keep_alive/background detach process và đăng ký nó trong _BACKGROUND_PROCESSES; elevated=true launch qua ShellExecuteW runas mà không thu stdout/stderr hay PID. Effects của command được spawn là của caller; bản thân tool chỉ spawn process (và, trên các đường dẫn routed/destructive, ghi một artifact script / backup).", "uc": "Một CA chạy `git status` hoặc một lệnh build trong project root và nhận về exit code + stdout/stderr đã cắt ngắn; một server sống lâu được start với keep_alive=true và readiness của nó được xác minh theo expected port.", "cit": ["code_tools.py:5503", "code_tools.py:5660", "code_tools.py:5914", "code_tools.py:6615", "code_tools.py:6627"], "h": "code_tools.py:5503", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "spawned shell/command process (subprocess.Popen, cwd=project root; keep_alive → detached background process in _BACKGROUND_PROCESSES; elevated → ShellExecuteW runas)"}, {"kind": "file", "target": "pre-run project backup (via backup_project)", "condition": "command scanned destructive AND no backup manifest today AND skip_backup=false"}, {"kind": "file", "target": ".orchestrator_cache/test_scripts/<hash>.ps1/.js script artifact (via run_project_test_script)", "condition": "inline PowerShell/node command trips the transport guards and is auto-routed"}], "use": {"b": "heavy", "c": 239047, "ls": "2026-11-16T15:48:55"}},
{"n": "run_project_test_script", "t": "T2", "cat": "runtime", "mut": true, "prot": true, "sess": true, "fg": "test", "eff": "Lưu script do caller cung cấp thành một artifact trong .orchestrator_cache/test_scripts/<label>_<hash16>.{js,py,ps1,sh} (UTF-8; BOM + OutputEncoding prelude cho PowerShell 5.1) và thực thi nó out-of-process: node <script>, python qua loader _PY_CHILD_BOOT, `powershell -NoProfile -ExecutionPolicy Bypass -File <script>`, hoặc sh <script> — Popen với CREATE_NO_WINDOW trên Windows, stdout/stderr được thu với trần 4000/2000 ký tự. CASE-2008 gate chạy trước bước ghi: một body destructive bị từ chối nếu không có confirm_destructive khớp số đếm của scanner, lấy một pre-run project backup trước (trừ khi skip_backup), và trả về output không cắt ngắn. dry_run=true trả về static-scan preview với executed:false — không ghi gì và không chạy gì. Effects của script được spawn là của caller; bản thân tool ghi artifact script và spawn process. KIND_TEST cache warm không phải một derived-index write: gated theo env và tắt mặc định (R64).", "uc": "Một CA chạy một script audit PowerShell 200 dòng có các literal tiếng Việt qua run_project_test_script (runtime=powershell) thay vì `powershell -Command` inline, thứ sẽ làm hỏng text khi đi qua console code page.", "cit": ["code_tools.py:9863", "code_tools.py:9994", "code_tools.py:10054", "code_tools.py:10103", "code_tools.py:9982"], "h": "code_tools.py:9863", "wk": "none", "der": false, "wr": [{"kind": "file", "target": ".orchestrator_cache/test_scripts/<label>_<hash16>.{js,py,ps1,sh} script artifact (UTF-8; BOM + prelude for powershell)"}, {"kind": "process", "target": "spawned [node|python-boot|powershell -File|sh, <script_path>] child process (Popen, CREATE_NO_WINDOW on Windows)"}, {"kind": "file", "target": "pre-run project backup (via backup_project)", "condition": "script body scanned destructive AND no backup manifest today AND skip_backup=false"}], "use": {"b": "heavy", "c": 3669, "ls": "2026-11-16T15:48:43"}},
{"n": "run_ps1_script", "t": "T2", "cat": "runtime", "mut": true, "prot": true, "sess": true, "fg": "runtime", "eff": "Wrapper hẹp: ghi nguồn PowerShell do caller cung cấp thành một artifact .ps1 (.orchestrator_cache/test_scripts/<label>_<hash16>.ps1, UTF-8 BOM + OutputEncoding prelude) và chạy nó bằng `powershell -NoProfile -ExecutionPolicy Bypass -File <temp.ps1>` out-of-process, thu stdout/stderr với trần 4000/2000 ký tự. Toàn bộ hành vi (CASE-2008 destructive scan + confirm_destructive gate + pre-run backup + dry_run preview) đều kế thừa từ run_project_test_script. Effects của script được spawn là của caller; bản thân tool chỉ ghi artifact script và spawn process.", "uc": "Một CA chạy một script PowerShell truy vấn Get-NetTCPConnection và in ra các listener port, qua run_ps1_script, để chuỗi tiếng Việt và các quote sống sót end-to-end nhờ transport file UTF-8 BOM.", "cit": ["code_tools.py:12414", "code_tools.py:12437", "code_tools.py:12440", "code_tools.py:10065", "code_tools.py:10054"], "h": "code_tools.py:12414", "wk": "none", "der": false, "wr": [{"kind": "file", "target": ".orchestrator_cache/test_scripts/<label>_<hash16>.ps1 script artifact (UTF-8 BOM + prelude, via run_project_test_script)"}, {"kind": "process", "target": "spawned [powershell, -NoProfile, -ExecutionPolicy Bypass, -File, <script_path>] child process (via run_project_test_script)"}, {"kind": "file", "target": "pre-run project backup (via backup_project, through run_project_test_script)", "condition": "script body scanned destructive AND no backup manifest today AND skip_backup=false"}], "use": {"b": "heavy", "c": 64513, "ls": "2026-11-16T15:48:50"}},
{"n": "run_test_profile", "t": "T2", "cat": "runtime", "mut": false, "prot": false, "sess": false, "fg": "test", "eff": "Chạy một test profile của dự án out-of-process bằng cách delegate xuống run_project_command: mặc định profile=unittest chạy [python, -m, unittest, discover, -s, tests|test, -p, test_*.py, -v]; py_compile chạy [python, -m, py_compile, <project .py files>]; pytest / py_compile_core / npm_test chạy [python, -m, pytest, -v, --basetemp, <fresh temp dir>] / [python, -m, py_compile, <core .py files>] / [npm, test]. Vì vậy code test của dự án thực thi out-of-process với đầy đủ quyền hạn local của runner trên đường dẫn mặc định; bản thân tool không ghi bất kỳ file/DB nào của ToolA — effects của các test được spawn là của caller (R45 qualifier). Các profile dựa trên workflow (rawcut_test4_manual, rawcut_testgf_sheet) là ngoại lệ: chúng delegate sang workflow_start và trả về bước đầu tiên mà không spawn. Profile mặc định chạy qua run_project_command; py_compile ghi __pycache__; pytest dùng một basetemp tạm.", "uc": "Sau một fix, một CA chạy run_test_profile với profile unittest mặc định để thực thi test suite của dự án và nhận pass/fail cùng output, thay vì tự dựng lệnh discover.", "cit": ["code_tools.py:6809", "code_tools.py:6843", "code_tools.py:6950", "code_tools.py:7014", "code_tools.py:6982"], "h": "code_tools.py:6809", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "spawned test-runner process via run_project_command ([python, -m, unittest|pytest|py_compile, ...] or [npm, test]) — the project's test code runs out-of-process; its effects are the caller's (R45 qualifier)"}], "err": {"c": "i", "r": "output-history.md:L152 (R61)", "q": "R45-breadth qualifier + py_compile __pycache__ note"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "save_log_snapshot", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "ops", "eff": "Trích nội dung log — theo selector qua một lần evaluate, hoặc theo section_title qua delegate extract_log_section — bắt buộc phải có project_path, rồi ghi atomic nội dung đó (temp file + replace) vào <project>/.orchestrator_cache/artifacts/<round_id_or_ts>/<slug>.txt và trả về path, sha256, head/tail 200, length và số dòng THAY CHO toàn văn, để một báo cáo cuối chỉ cần dẫn path + checksum + preview (~90% tiết kiệm token so với dán nguyên log). Các lời gọi lặp lại với cùng round_id + slug thay thế artifact một cách lặng lẽ.", "uc": "Sau một lần chạy test dài, một CA lưu System Error Log thành artifact và dẫn path cùng tiền tố sha256 của nó trong báo cáo cuối, thay vì dán 8 KB text log.", "cit": ["tools_v15.py:4717", "tools_v15.py:4749", "tools_v15.py:4762", "tools_v15.py:4766", "tools_v15.py:4774"], "h": "tools_v15.py:4659", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "<project>/.orchestrator_cache/artifacts/<round_id>/<slug>.txt (atomic tmp+replace)"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "set_workflow_mode", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "workflow", "eff": "Đổi workflow mode ở CẢ HAI bản ghi đều tự nhận là nơi giữ nó: trước hết update_workflow_state ghi WORKFLOW_STATE.json (atomic temp-file replace với mode/idea/updated_at) trên đường dẫn mặc định, sau đó — khi có một active session — _run_session_write áp _session_store_module._set_session_mode, một UPDATE bền của cột mode trong row sessions của sessions.db. Khi không có active session nào, file JSON vẫn được cập nhật và response báo session_mode applied:false với reason no_active_session (lần open_session kế tiếp sẽ persist mode khi tạo session).", "uc": "Sau khi một CA xong bước triage chỉ-đọc và user duyệt fix, CA gọi set_workflow_mode để chuyển dự án từ T1 sang T2, để T1/T2 gate và row session thống nhất về mode.", "cit": ["code_tools.py:4193", "code_tools.py:4205", "code_tools.py:4226", "code_tools.py:2449", "code_tools.py:4217"], "h": "code_tools.py:4193", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "WORKFLOW_STATE.json in the project cache dir (via update_workflow_state: atomic temp-file replace, mode/idea/updated_at)"}, {"kind": "sqlite", "target": "sessions.db sessions row mode UPDATE (via _set_session_mode)", "condition": "an active session exists for the project; otherwise JSON file only"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "start_app", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Launch một app thành một background process detached bằng cách delegate xuống run_project_command với keep_alive/background, dùng raw shell command do caller cung cấp nếu có (command ưu tiên hơn app) hoặc lấy tên/đường dẫn app làm command nếu không, rồi xác minh readiness qua health_url và/hoặc expected_port. Một lệnh launch PowerShell viết dạng -Command -File bị từ chối; script phải dùng -File trực tiếp. Process được spawn thực thi code do caller cung cấp và tồn tại sau khi lời gọi return, nên đây là một durable process write. Registry mutating=true, mutation_scope=process, requires_session=true, protected=true là ĐÚNG. Kết quả mang một lifecycle note rằng launch/readiness không chứng minh đã thay thế một runner đang tồn tại (một runner vốn đã healthy có thể exit 0); PID/generation phải được kiểm tra trước và sau một đợt recycle được ủy quyền.", "uc": "Một CA cần một local dev server chạy để kiểm tra về sau, nên nó gọi start_app với command start server, một health_url và một expected_port, và chờ bằng chứng readiness trước khi chạy browser_smoke_test trên trang được serve.", "cit": ["tools_upgrade_p5.py:75", "tools_upgrade_p5.py:91", "tools_upgrade_p5.py:101", "tools_upgrade_p5.py:106"], "h": "tools_upgrade_p5.py:75", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "background process spawned from caller-supplied command/app via run_project_command keep_alive"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "stop_app", "t": "T2", "cat": "t2", "mut": true, "prot": true, "sess": false, "fg": "desktop", "eff": "Dừng một app đã start bằng start_app, theo PID hoặc theo process name, kể cả các descendant của nó: khi một PID được cho và include_children là true (mặc định), các descendant được liệt kê deepest-first qua psutil và bị giết trước cha, để không orphan nào được re-parent giữa chừng đợt sweep; sau đó process cha (hoặc process được nêu tên) bị giết qua kill_process với force=true mặc định. Các PID đã biến mất được báo là already_exited, không phải failure. Thuộc nhóm gần-destructive và được khai báo như vậy: giết theo process_name nhắm vào MỌI process khớp tên đó, không chỉ con của start_app, và force mặc định là true. Registry mutating=true, mutation_scope=process, protected=true là ĐÚNG. Staleness (không đếm lại): file matrix ghi sess:true cho stop_app trong khi live registry requires_session là false — cờ live mới có hiệu lực.", "uc": "Sau khi một smoke test xong, một CA gọi stop_app với PID mà start_app đã trả về để chấm dứt dev server và toàn bộ process tree của nó, rồi xác nhận port đã đóng trước khi báo app đã dừng.", "cit": ["tools_upgrade_p5.py:125", "tools_upgrade_p5.py:148", "tools_upgrade_p5.py:161", "tools_upgrade_p5.py:167"], "h": "tools_upgrade_p5.py:125", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "target process tree (PID and/or process_name plus descendants) terminated via kill_process"}], "note": "Matrix sess:true was stale; live requires_session=false (R71c).", "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "validate", "t": "T2", "cat": "t2", "mut": false, "prot": false, "sess": false, "fg": "test", "eff": "Dispatcher validate hợp nhất trên ba sub-check: checks mặc định là [\"file\"]; 'file' chạy validate_file (syntax/lint), 'symbols' chạy validate_symbols (so sánh symbol AST với INDEX), 'encoding' chạy validate_encoding (kiểm tra UTF-8/mojibake). Gom kết quả vào một response; ok = mọi sub-check ok, và một failure nêu rõ sub-check gây ra nó (VALIDATION_FAILED + failed_checks). Pure read — cả ba delegate đã được xác nhận read-only trong batch này (các write duy nhất của validate_file là những tempfile tạm được tạo và unlink ngay bên trong lần gọi).", "uc": "Sau một write_fix, một CA chạy validate với checks=[file, symbols, encoding] trên file đã đổi để lấy các phán quyết syntax, symbol-drift và encoding trong một response.", "cit": ["code_tools.py:16871", "code_tools.py:16911", "code_tools.py:16913", "code_tools.py:16915"], "h": "code_tools.py:16871", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "validate_encoding", "t": "T2", "cat": "t2", "mut": false, "prot": false, "sess": false, "fg": "test", "eff": "Kiểm tra encoding của một file: giải file đó, từ chối thẳng file binary (BINARY_FILE_UNSUPPORTED), rồi chạy _encoding_check_from_file cho UTF-8 validity + mojibake risk và trả về ok = utf8_valid AND NOT mojibake_risk. Pure read — byte của file chỉ được đọc, không bao giờ được ghi.", "uc": "Trước khi áp một edit vào file có text tiếng Việt, một CA chạy validate_encoding để xác nhận file là UTF-8 hợp lệ và không có chuỗi nào mang mojibake risk.", "cit": ["code_tools.py:14498", "code_tools.py:14507", "code_tools.py:14516", "code_tools.py:14517"], "h": "code_tools.py:14498", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "validate_file", "t": "T2", "cat": "t2", "mut": false, "prot": false, "sess": false, "fg": "test", "eff": "Kiểm tra syntax/validity sau một lần ghi, theo ngôn ngữ: Python qua py_compile ra một tempfile .pyc (được tạo + unlink trong lần gọi) cộng ruff lint chạy như module (read-only, không --fix); JS qua `node --check`; TS/JSX/TSX/Vue qua esbuild syntax check ra một output .js tempfile (được tạo + unlink trong lần gọi); HTML qua tag-balance parse cộng mỗi block <script> được chép ra một tempfile .js rồi node --check (được tạo + unlink trong lần gọi); JSON/CSS/TOML qua strict stdlib parse. Các checker được spawn (ruff, node --check, esbuild) chỉ parse/lint — không cái nào thực thi code của file, nên R45 không áp dụng. Mọi tempfile đều tạm (được tạo và unlink ngay bên trong lần gọi); không có write bền nào.", "uc": "Sau khi write_fix sửa một file .py, một CA chạy validate_file trên nó để xác nhận nó vẫn biên dịch được và ruff không tìm thấy finding mới, trước khi chuyển sang file kế tiếp.", "cit": ["code_tools.py:14805", "code_tools.py:14900", "code_tools.py:14938", "code_tools.py:14962", "code_tools.py:14976", "code_tools.py:15027"], "h": "code_tools.py:14805", "wk": "none", "der": false, "wr": [], "use": {"b": "light", "c": 25, "ls": "2026-11-14T18:01:02"}},
{"n": "validate_symbols", "t": "T2", "cat": "t2", "mut": false, "prot": false, "sess": false, "fg": "test", "eff": "Kiểm tra integration sau khi ghi: syntax-check file qua validate_file, trích các symbol top-level + class-method bằng AST (Python) hoặc regex (JS/TS), và so chúng với chỉ mục snapshot project-map INDEX.json (được đọc tại code_tools.py:15291-15293, cộng một phép so mtime file-vs-index) để báo symbols_intact / symbols_added / symbols_removed — kèm xử lý stale_index khi chỉ mục cũ hơn file (một removal chống lại chỉ mục stale chỉ được báo, không được khẳng định). Pure read: đọc file + đọc INDEX.json + stat mtime; chỉ mục không bao giờ bị ghi lại ở đây. 'Index' ở đây là chỉ mục snapshot project-map (INDEX.json), KHÔNG PHẢI DB code graph dẫn xuất của CBM.", "uc": "Sau một refactor có chạm tới một file .py, một CA chạy validate_symbols để bắt một function hoặc class top-level bị xóa nhầm, bằng cách so các symbol AST hiện tại với snapshot INDEX.", "cit": ["code_tools.py:15196", "code_tools.py:15204", "code_tools.py:15230", "code_tools.py:15291", "code_tools.py:15371"], "h": "code_tools.py:15196", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "write_draft_patch", "t": "T2", "cat": "write", "mut": false, "prot": false, "sess": false, "fg": "write", "eff": "Chỉ patch một dải dòng của một draft đang chờ trong memory và rà lại toàn bộ draft: thay các dòng start-end, tăng patch_count và làm mới TTL 30 phút, rồi chạy full structural validation; nếu còn lỗi nó trả DRAFT_STILL_FAILING kèm các phần failed, và ngay khi draft sạch thì nó tự động được ship qua pipeline write_fix/write_new_file gốc (backup, validation, symbol check, cache sync) và bản ghi draft bị xóa. Một draft_id không rõ hoặc đã hết hạn, hoặc một dải dòng ngoài phạm vi, bị từ chối mà không đụng tới draft.", "uc": "Sau khi write_fix mở một draft cho một patch failed, CA chỉ sửa đúng dải failed qua write_draft_patch, thay vì gửi lại toàn bộ content.", "cit": ["orchestrator/tools/write/draft.py:404", "orchestrator/tools/write/draft.py:410", "orchestrator/tools/write/draft.py:433", "orchestrator/tools/write/draft.py:291", "orchestrator/tools/write/draft.py:32"], "h": "orchestrator/tools/write/draft.py:368", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "target file (via write_fix/write_new_file)", "condition": "patched draft validates clean"}], "err": {"c": "i", "r": "output-history.md:L122 (R31)", "q": "validation-gate qualifier (R25)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "write_fix", "t": "T2", "cat": "write", "mut": true, "prot": true, "sess": true, "fg": "write", "eff": "Thay thế một dải dòng của một file text hiện có qua full guarded write pipeline: giải target theo xroot policy, từ chối file binary, dựng content đề xuất giữ nguyên line ending và BOM của file, và ở chế độ dry_run thì thẩm định, xem trước và lưu một dry-run record mà không ghi. Một lần ghi thật tự seed một project-map INDEX.json tối thiểu khi thiếu, chạy pre-write syntax check (failure mở một draft thay vì ghi), rồi dưới một per-path gate kiểm tra base hash (WRITE_BASE_CHANGED khi base stale), ghi một bản backup .bak, ghi atomic content mới, post-validate và auto-revert khi failure, chạy symbol check (auto-revert khi drift trừ khi skip_symbol_check), sync entry của chỉ mục snapshot project-map INDEX.json, và trả về new_hash/new_mtime để lần ghi kế tiếp có thể chain qua expected_hash.", "uc": "Tool edit duy nhất của pipeline refractor: mỗi fix là một lời gọi write_fix cho từng region, từ dưới lên (bottom-up), sau một read_file_range seed. (museR G2)", "cit": ["orchestrator/tools/write/core.py:1653", "orchestrator/tools/write/core.py:1826", "orchestrator/tools/write/core.py:1856", "orchestrator/tools/write/core.py:1890", "orchestrator/tools/write/core.py:2057", "orchestrator/tools/write/core.py:2084"], "h": "orchestrator/tools/write/core.py:1629", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "target file"}, {"kind": "file", "target": ".bak backup"}, {"kind": "index", "target": "project-map snapshot INDEX.json entry"}, {"kind": "sqlite", "target": "read-freshness token seal (read_tokens)"}, {"kind": "file", "target": "dry-run record file", "condition": "dry_run=true"}], "use": {"b": "heavy", "c": 14610, "ls": "2026-11-16T15:48:24"}},
{"n": "write_fix_html", "t": "T2", "cat": "write", "mut": true, "prot": true, "sess": true, "fg": "write", "eff": "Alias bị deprecated ở mức docstring của write_fix (banner [DEPRECATED V15 R8], code_tools.py:1664 — chỉ docstring, KHÔNG PHẢI deprecated class dạng machine-readable của 5). File không phải .html được route thẳng sang write_fix với dry_run=False (1680-1683); file .html trước hết dry-run (1686), rồi chạy closing-delimiter safety check (<\\/script>/<\\/style>/<\\/body>/<\\/html> + brace balance, 1694-1711) và từ chối trừ khi force=true, sau đó áp lần ghi thật qua write_fix dry_run=False (1721-1724). Một mức dưới, write_fix (orchestrator/tools/write/core.py:1629) ghi một bản copy backup của bản gốc (1896) và content mới vào file đích (1897) qua _atomic_write_text, còn cache sync của nó reseed entry của chỉ mục snapshot project-map INDEX.json (core.py:1967-1968 nêu rõ việc INDEX reseed). Việc ghi file xảy ra trên default success path, ở mọi lời gọi vượt qua delimiter check.", "uc": "Một CA patch một dải của file .html và gọi write_fix_html, để closing-delimiter safety check từ chối patch (trừ khi force=true) nếu nó sẽ âm thầm làm mất một delimiter <\\/script>, <\\/style>, <\\/body> hay <\\/html>.", "cit": ["code_tools.py:1661", "code_tools.py:1680", "code_tools.py:1686", "code_tools.py:1721", "orchestrator/tools/write/core.py:1896", "orchestrator/tools/write/core.py:1897"], "h": "code_tools.py:1661", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "file_path (via write_fix dry_run=False, code_tools.py:1721-1724)"}, {"kind": "file", "target": "backup copy of the original file content (via write_fix, orchestrator/tools/write/core.py:1896)"}, {"kind": "index", "target": "project-map snapshot index INDEX.json entry for the file (write pipeline cache sync, orchestrator/tools/write/core.py:1967-1968)", "condition": "on successful write"}], "note": "Docstring-deprecated only [DEPRECATED V15 R8] code_tools.py:1664 — NOT a deprecated mark (R8). Delimiter/brace gate applies — NOT a plain alias of write_fix (R68j).", "use": {"b": "light", "c": 14, "ls": "2026-11-13T18:02:28"}},
{"n": "write_new_file", "t": "T2", "cat": "write", "mut": true, "prot": true, "sess": true, "fg": "write", "eff": "Tạo một file text mới (hoặc chỉ ghi đè khi overwrite=true): giải target theo xroot policy, từ chối target binary và file đã tồn tại khi không có overwrite, chuẩn hóa content .bat/.cmd/.ps1 sang CRLF, và ở chế độ dry_run thì xem trước và lưu một dry-run record mà không ghi. Một lần ghi thật tạo các thư mục cha, ghi atomic content, post-validate (một file mới failed bị xóa — revert của file mới CHÍNH LÀ deletion — hoặc được khôi về byte nguyên thủy khi ghi đè), sync entry của chỉ mục snapshot project-map INDEX.json, và trả về new_hash; INDEX thiếu chỉ sinh warning, không chặn. Nếu file đã đổi kể từ lúc read token được seed, lần ghi mới hơn thắng (superseded branch, core.py:2424-2426).", "uc": "Tạo một file test, script hoặc config mới trong một dự án, xem trước trước với dry_run=true rồi áp qua apply_after_dry_run. (plausible-but-unverified)", "cit": ["orchestrator/tools/write/core.py:2255", "orchestrator/tools/write/core.py:2352", "orchestrator/tools/write/core.py:2400", "orchestrator/tools/write/core.py:2471", "orchestrator/tools/write/core.py:2474"], "h": "orchestrator/tools/write/core.py:2198", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "target file"}, {"kind": "index", "target": "project-map snapshot INDEX.json entry"}, {"kind": "sqlite", "target": "read-freshness token seal (read_tokens)"}, {"kind": "file", "target": "dry-run record file", "condition": "dry_run=true"}], "use": {"b": "heavy", "c": 2509, "ls": "2026-11-16T15:43:51"}},
{"n": "write_transaction", "t": "T2", "cat": "write", "mut": true, "prot": true, "sess": true, "fg": "write", "eff": "Write batch multi-file atomic. Callable là def wrapper async nằm trong khối try V15 cấp module (code_tools.py:19863, def bị thụt lề — cái bẫy R17: chỉ grep ^def sẽ bỏ sót nó); nó từ chối skip_verify=true (19868-19873) và delegate xuống _write_transaction_v15 (19874), tức _v15.write_transaction được bind tại code_tools.py:19861. Hiện thực (tools_v15.py:811) chạy Phase 1 dry_run của mọi op (919) + structural check (930), Phase 2 backup qua backup_project khi backup=true (mặc định, 965), Phase 3 apply-all qua _dispatch dry_run=False (1038) với hash-chaining giữa các op trên cùng một target, rồi thẩm định cả batch và roll back (restore qua _atomic_write_bytes + _update_cache_after_write, tools_v15.py:1000-1001) khi bất kỳ failure nào xảy ra. Mỗi op mặc định là write_fix (thay thế dòng) và có thể thay bằng write_new_file hoặc replace_between_anchors (837-847). Các lần ghi file đích xảy ra trên default success path, ở mọi lời gọi có operations list hợp lệ và khác rỗng.", "uc": "Một CA áp một patch multi-hunk, multi-file như một batch atomic — ví dụ đổi tên một symbol qua ba file — gọi write_transaction với một operation cho mỗi hunk, để một failure trong bất kỳ op nào revert toàn bộ thay vì bỏ lại một thay đổi mới áp được nửa.", "cit": ["code_tools.py:19863", "code_tools.py:19874", "tools_v15.py:811", "tools_v15.py:965", "tools_v15.py:1038"], "h": "code_tools.py:19863", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "each operation's file_path (via _dispatch dry_run=False, tools_v15.py:1038)"}, {"kind": "file", "target": "backup copies of the operation targets (via backup_project, tools_v15.py:965)", "condition": "backup=true (default)"}, {"kind": "index", "target": "project-map snapshot index INDEX.json entries for the written files (via _update_cache_after_write, tools_v15.py:1001)", "condition": "on successful apply (and on rollback)"}, {"kind": "file", "target": "rollback restore of original bytes on verify failure", "condition": "verify failure after apply"}], "use": {"b": "unused", "c": 0, "ls": null}},
// T3 — Audit/Session
{"n": "audit_tool_matrix", "t": "T3", "cat": "audit", "mut": false, "prot": false, "sess": false, "fg": "audit", "eff": "Audit matrix tích hợp sẵn: đối chiếu chéo TOOL_MAP/TOOL_SCHEMAS/TOOL_PHASES đang sống với skill docs, router, harness và inventory (qua _audit_tool_matrix_sync) và trả về drift counts, drift entries, coverage và progressive-gap counts, kèm định dạng theo verbosity (terse chặn trần các mảng drift; ref bỏ hẳn các mảng) và một lưới an toàn drift ép verbosity=full khi có drift hoặc gap. Tool hướng user mặc định persist=False, nên lời gọi mặc định là đọc thuần: bản sync chỉ append vào .orchestrator_cache/matrix_log.jsonl KHI được truyền persist=True. Hai dashboard V15 gọi internal sync với mặc định persist=True của nó — hành vi dashboard đó KHÔNG áp dụng cho tool này.", "uc": "Sau khi thêm hoặc bỏ một tool, một CA chạy audit_tool_matrix để xác nhận drift_count=0 trước khi ship, mà bản thân lời gọi không append một audit record nào.", "cit": ["code_tools.py:4130", "code_tools.py:4131", "code_tools.py:4152", "code_tools.py:4066", "code_tools.py:4069", "code_tools.py:4161"], "h": "code_tools.py:4130", "wk": "none", "der": false, "wr": [{"kind": "file", "target": ".orchestrator_cache/matrix_log.jsonl", "condition": "persist=true only (opt-in); the default call writes nothing"}], "err": {"c": "ii", "r": "output-history.md:L147 (R56)"}, "use": {"b": "moderate", "c": 155, "ls": "2026-11-16T15:46:10"}},
{"n": "begin_task", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Ghi nhận prompt thật mới nhất của user làm active task của session: sửa mojibake Latin-1/UTF-8, chặn biên độ prompt (256-8000 chars), rồi thay thế atomic durable task checkpoint của session (task_epoch+1, prompt_hash, active_task, owner/subtask bookkeeping) qua _checkpoint_update_with_retry chạy trên SessionWriter. Root owner phải trình owner_capability do server cấp (hoặc xác nhận một owner claim đang chờ bằng owner_claim_id gốc); một non-owner cần một delegation capability hợp lệ cho một subtask, và có thể được cấp một capability cho delegate_agent_id. Từ chối mà không ghi khi không có bootstrap root nào tồn tại (TASK_OWNER_REQUIRED), claim đã hết hạn, hoặc kiểm tra capability/delegation thất bại.", "uc": "Ở đầu mỗi lượt user thật, host gọi begin_task(session_id, prompt, agent_id, owner_capability) một lần để mọi tool call sau đó trong lượt mang task_epoch/prompt_hash mới; một worker agent dùng nhánh delegation thay thế.", "cit": ["code_tools.py:10327", "code_tools.py:10351", "code_tools.py:10378", "code_tools.py:10420", "orchestrator/session/store.py:2381", "orchestrator/session/store.py:2361"], "h": "code_tools.py:10327", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "session-store task_checkpoints row for the session (task_epoch, prompt_hash, active_task, owner/delegation state)", "condition": "owner/delegation validation passes (reject paths write nothing)"}], "err": {"c": "i", "r": "output-history.md:L148 (R57)"}, "use": {"b": "light", "c": 40, "ls": "2026-11-14T15:41:42"}},
{"n": "ca_policy_metrics", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "audit", "eff": "Tổng hợp các Matrix bypass counters từ sessions.db events (forbidden/allowed native counts, via_a count, unclassified count, bypass rate) qua query_session_metrics, phạm vi theo session_id, project_path (khớp LIKE lỏng kèm fallback recent-events), hoặc agent_id, trên một cửa sổ thời gian (mặc định 24 h) với trần dòng 5000 (theo session) hoặc 2000 (các trường hợp còn lại). Bản delegate chỉ chạy các SELECT statement và đóng kết nối trước khi tổng hợp trong bộ nhớ.", "uc": "Một CA hỏi ca_policy_metrics cho 24 h gần nhất để xem các agent đã bypass matrix của ToolA bằng native calls thường xuyên cỡ nào so với đi qua via_a.", "cit": ["tools_v15.py:9829", "tools_v15.py:9835", "orchestrator/core/ca_policy_matrix.py:1090", "orchestrator/core/ca_policy_matrix.py:1097", "orchestrator/core/ca_policy_matrix.py:1112", "orchestrator/core/ca_policy_matrix.py:1117"], "h": "tools_v15.py:9819", "wk": "none", "der": false, "wr": [], "use": {"b": "rare", "c": 1, "ls": "2026-11-14T21:14:20"}},
{"n": "clear_closed_sessions", "t": "T3", "cat": "session", "mut": true, "prot": true, "sess": true, "fg": "session", "eff": "Xóa vĩnh viễn mọi closed session của một project (và events của chúng): select session_ids từ sessions nơi project_path khớp và status='closed', xóa các events row của chúng, rồi xóa các session row, tất cả trong một transaction durable _run_session_write chạy ngoài event loop, trả về deleted_sessions và deleted_events counts. Các active session không bị đụng tới. Mang tính hủy diệt và không thể đảo ngược — không có trash/undo.", "uc": "Sau khi công việc của một project xong và các closed session của nó đã được xem xét, một CA gọi clear_closed_sessions để quét sạch lịch sử closed session (và các event row của nó) khỏi session store.", "cit": ["code_tools.py:16749", "code_tools.py:16756", "code_tools.py:16763", "code_tools.py:16765", "code_tools.py:16771"], "h": "code_tools.py:16749", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "session-store events rows for closed sessions of the project"}, {"kind": "sqlite", "target": "session-store sessions rows where project_path matches and status='closed'"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "close_session", "t": "T3", "cat": "session", "mut": true, "prot": true, "sess": true, "fg": "session", "eff": "Đóng active session và tóm tắt nó: theo session_id (tra trực tiếp) hoặc theo project_path (resolve về registered root, kèm fallback raw-key cho các session cũ hơn). Khi thành công nó log một close_session event, UPDATE durable session row sang status='closed' với ended_at, và trả về duration_minutes cùng event counts theo action_type (success/error/total), native_fallback_count và pending_audit_count. Trả về SESSION_NOT_FOUND khi không có active session nào khớp; một SessionWriter contention error được trả về dạng retryable kèm backoff hint để session không bị kẹt ở trạng thái active.", "uc": "Khi kết thúc công việc trên một project, một CA gọi close_session(project_path) để đóng active session và lấy summary của nó (thời lượng, bao nhiêu tool call thành công so với lỗi) cho session record.", "cit": ["code_tools.py:15993", "code_tools.py:16034", "code_tools.py:16037", "code_tools.py:16138", "code_tools.py:16141"], "h": "code_tools.py:15993", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "session-store events row (close_session event)"}, {"kind": "sqlite", "target": "session-store sessions row (status='closed', ended_at)"}], "note": "Usage UNKNOWN (R16 CLOSED): the single event row is unreliable; do not read it as “1 call”.", "use": {"b": "unknown", "c": null, "ls": null}},
{"n": "council_cron_job_delete", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "council", "eff": "Soft-delete một durable council cron job: đánh dấu row của nó trong bảng jobs của SQLite council_cron.db bằng enabled=0, next_run_at=NULL, deleted_at=now, để nó không bao giờ fire nữa nhưng row và toàn bộ past run history của nó vẫn nằm lại trong database. Wrapper trước tiên load job, nên một job_id không biết sẽ thất bại (JobNotFound) thay vì âm thầm thành công; mọi error được trả về dạng ok:false với CRON_JOB_ERROR. Nó không xóa run row nào và không đụng đến bất kỳ job nào khác.", "uc": "Leader cho nghỉ một council job chạy hằng đêm không còn cần nữa; các run quá khứ của nó vẫn truy vấn được sau đó qua council_cron_job_list.", "cit": ["orchestrator_server.py:12686", "orchestrator_server.py:12687", "orchestrator/council/cron_jobs.py:341", "orchestrator/council/cron_jobs.py:344"], "h": "orchestrator_server.py:12686", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "council_cron.db jobs row (soft-delete: enabled=0, deleted_at set); run history untouched"}], "err": {"c": "i", "r": "output-history.md:L112 (R21)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "council_cron_job_save", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "council", "eff": "Tạo (không truyền job_id) hoặc cập nhật (có job_id) một durable council cron job trong SQLite council_cron.db, ghim worker, model/provider, prompt, schedule (interval hoặc cron), enabled flag, narrator worker/model/prompt tùy chọn, và timeout. Một job mới nhận id cron-<12 hex> và next_run_at đầu tiên được tính lúc insert. Một bản cập nhật vẫn để job enabled và không đụng tới schedule thì giữ next_run_at hiện có thay vì tính lại lịch. Các payload field không biết bị validation từ chối, và một job_id không biết thì thất bại; lỗi trả về ok:false với CRON_JOB_ERROR. Nó không chạy job.", "uc": "Leader lên lịch cho một worker chạy một prompt cố định mỗi ngày lúc 09:00; vài tuần sau họ chỉ sửa phần text của prompt, và thời điểm run kế tiếp được giữ nguyên.", "cit": ["orchestrator_server.py:12678", "orchestrator_server.py:12681", "orchestrator/council/cron_jobs.py:310", "orchestrator/council/cron_jobs.py:325", "orchestrator/council/cron_jobs.py:329"], "h": "orchestrator_server.py:12678", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "council_cron.db jobs row (INSERT on create, UPDATE on save)"}], "err": {"c": "i", "r": "output-history.md:L112 (R21)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "council_cron_job_trigger", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "council", "eff": "Fire ngay lập tức một council cron job đã cấu hình, ngoài schedule. Nó từ chối khi cron scheduler chưa được khởi động (dispatch unset). Nó claim một run row (trigger_kind manual, status queued) kèm chống chồng lấn: nếu một run queued hoặc running đã tồn tại cho job thì nó raise RunConflict thay vì chạy kép, được thực thi bằng một kiểm tra BEGIN IMMEDIATE cộng với một partial unique index trên các active run. Run sau đó được launch bất đồng bộ (worker dispatch, rồi narrator tùy chọn) và kết quả của nó được ghi là succeeded/failed trên run row. Lời gọi trả về run row vừa queued ngay lập tức; nó không chờ và không trả về reply của worker.", "uc": "Sau khi một job hằng đêm thất bại, leader trigger lại nó ngay lập tức thay vì chờ run theo lịch kế tiếp; run row cho biết lần retry có thành công hay không.", "cit": ["orchestrator_server.py:12691", "orchestrator_server.py:12692", "orchestrator/council/cron_jobs.py:376", "orchestrator/council/cron_jobs.py:364", "orchestrator/council/cron_jobs.py:394"], "h": "orchestrator_server.py:12691", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "council_cron.db runs row (INSERT, status queued then running/succeeded/failed); launches a worker dispatch"}], "err": {"c": "i", "r": "output-history.md:L112 (R21)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "create_pending_audit", "t": "T3", "cat": "audit", "mut": true, "prot": true, "sess": false, "fg": "audit", "eff": "Durable hóa một pending native-fallback audit entry: sanitize các trường action/tool/target/reason (bị cắt bởi _sanitize_audit_value, không mang raw secrets hay nội dung file), sinh một id pa-<16 hex>, và INSERT một row vào sessions.db pending_audits với sync_status='pending' qua durable write của SessionWriter chạy ngoài event loop. Trả về pending_audit_id + created_at.", "uc": "Sau khi một nỗ lực write_fix native thất bại, một CA log fallback đó qua create_pending_audit để audit trail ghi lại những gì đã được thử thay thế, phục vụ sync sau này khi API khả dụng.", "cit": ["code_tools.py:16452", "code_tools.py:16463", "code_tools.py:16469", "code_tools.py:16476", "code_tools.py:16489"], "h": "code_tools.py:16452", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "sessions.db pending_audits row (pending_audit_id, project_path, action_type, tool_name, instead_of, failed_error, target, reason, sync_status='pending', created_at, agent_id)"}], "note": "Matrix sess:true was stale; live requires_session=false (R60k).", "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "log_native", "t": "T3", "cat": "audit", "mut": true, "prot": true, "sess": false, "fg": "audit", "eff": "Log công việc native fallback vào lúc kết thúc session. Một callable mỏng; phần triển khai (orchestrator/session/logs.py:709, _log_native_sync :714) resolve session của caller qua _get_unique_caller_session (không resolve được -> NATIVE_LOG_SESSION_UNRESOLVED), append một session_end record ({ts, type:'session_end', native:[{tool, instead_of, reason}]}) vào .orchestrator_cache/USAGE_LOG.jsonl qua _log_to_file, cắt ngắn log, rồi persist một log_native event row trong sessions.db qua _log_session_event — và raise nếu việc persist event thất bại. Matrix file L374 xác nhận nó có trong mảng.", "uc": "Ở cuối một session mà một CA đã bypass ToolA bằng công việc shell/file thô, nó gọi log_native kèm danh sách các native tool đã dùng để việc bypass được ghi vào usage log và session ledger.", "cit": ["code_tools.py:7490", "code_tools.py:7492", "orchestrator/session/logs.py:714", "orchestrator/session/logs.py:734", "orchestrator/session/logs.py:739"], "h": "code_tools.py:7490", "wk": "none", "der": false, "wr": [{"kind": "file", "target": ".orchestrator_cache/USAGE_LOG.jsonl (session_end record, append)", "condition": "none - every successful call appends"}, {"kind": "sqlite", "target": "sessions.db events row (log_native)"}], "note": "log_native is NOT an alias of log_native_event (R60l): separate handler, separate registry entry.", "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "log_native_event", "t": "T3", "cat": "audit", "mut": true, "prot": true, "sess": false, "fg": "audit", "eff": "Ghi một native-fallback event (hoặc một batch qua native=[...], vốn đệ quy theo từng dict) vào sessions.db events thông qua durable write của SessionWriter: resolve active session hoặc tự mở một session, phân loại action (CSLt: một native action bị FORBIDDEN vẫn được ghi, rồi trả về ok:false POLICY_REJECT; công việc transport='via_a' được gắn nhãn via_A, không bị đếm như một native fallback), và trả về fallback_count_so_far (các event gốc native của session). Usage counts là UNKNOWN — tool này nằm trong _SKIP_SESSION_LOG, nên không có usage count nào được khẳng định cho nó.", "uc": "Khi một CA phải dùng PowerShell thô vì ToolA không khả dụng, nó gọi log_native_event kèm tên tool, target và reason để việc bypass được durable hóa gắn với active session.", "cit": ["code_tools.py:16245", "code_tools.py:16315", "code_tools.py:16361", "code_tools.py:16378", "code_tools.py:16394"], "h": "code_tools.py:16245", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "sessions.db events row (session_id, tool_name, tool_origin, action_type, status, target, duration_ms, reason, natural_log_line, created_at)"}, {"kind": "sqlite", "target": "sessions.db sessions row (auto-opened session)", "condition": "no active session for the project (auto-opens one)"}], "use": {"b": "unknown", "c": null, "ls": null}},
{"n": "manage_agent", "t": "T3", "cat": "audit", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Trình quản lý cấu hình multi-agent tập trung (vòng đời CA). Các read action list/get khảo sát agents_registry.json. Mọi action khác ghi theo mặc định (dry_run=false): register upsert bản ghi agent và lưu registry; set_mode lật mode và lưu cộng với viết lại rule file qua _ma_write_rule; unregister gỡ bản ghi (xóa file registry khi agent cuối cùng ra đi, kèm bản .bak backup) và tước bỏ managed rule body cùng MCP block; sync_rule / sync_mcp / sync_skill viết lại các file rule / MCP / skill (backup + validate + rollback khi thất bại); sync_hook cài các gate file, binding hook-agent.json và các hook_file entry, rồi chạy live node gate probes theo từng target. _ma_archive_legacy_bak_ma() chạy ở MỌI lời gọi (kể cả list/get) và chuyển mọi file *.bak_ma vào _ma_archive. Đây là delegate đứng sau đường sync_rule của council_config_sync (Batch 7, R53): council_config_sync chỉ gọi nó khi run_sync_rule=true; bản thân manage_agent là tool rộng hơn — đọc trên body của chính nó, không đồng nhất hai cái.", "uc": "Để onboard một CA mới, một operator gọi manage_agent(action='register', agent_id='muse3', persona='...', rule_file='...') rồi sync_rule để rule file của agent được sinh ra từ slim template.", "cit": ["code_tools.py:19118", "code_tools.py:19156", "code_tools.py:19247", "code_tools.py:19263", "code_tools.py:17663"], "h": "code_tools.py:19118", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "agents_registry.json (whole-registry atomic save)", "condition": "action=register/set_mode/unregister, dry_run=false"}, {"kind": "file", "target": "agent rule file (rule_file; markdown rewrite or json_field update, with .bak backup)", "condition": "action=set_mode/sync_rule/unregister-cleanup, content changed, dry_run=false"}, {"kind": "file", "target": "agent MCP config file (mcp_file; MCP block insert/remove, with .bak backup)", "condition": "action=sync_mcp/unregister-cleanup, dry_run=false"}, {"kind": "file", "target": "hook gate files + hook-agent.json binding + hook_file merge entries", "condition": "action=sync_hook, dry_run=false"}, {"kind": "file", "target": "skill files under the agent skill_dir", "condition": "action=sync_skill, dry_run=false"}, {"kind": "file", "target": "*.bak_ma files moved into _ma_archive", "condition": "legacy *.bak_ma files present (runs on every call, incl. list/get)"}, {"kind": "file", "target": "agents_registry.json.bak (backup before delete)", "condition": "action=unregister removing the last agent"}, {"kind": "process", "target": "node read/write gate probe subprocesses (~4 per hook target)", "condition": "action=sync_hook"}], "err": {"c": "i", "r": "output-history.md:L151 (R60)"}, "use": {"b": "light", "c": 77, "ls": "2026-11-16T07:37:38"}},
{"n": "mission_create", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "mission", "eff": "Tạo một mission và các task của nó trong store SQLite missions.db: validate tasks_json là một mảng khác rỗng và mọi task đều mang output_artifact_path, insert mission cộng một row mỗi task (sequence, dependencies, verify gate, retry policy, max_attempts mặc định 3, side_effect_class mặc định pure_artifact), đánh dấu mission active, và append một mission_create ledger row. Từ chối bằng NOT_LOCK_HOLDER trừ khi instance này đang giữ orchestrator lock.", "uc": "Một leader chia một job nhiều bước thành các task có output artifact và thứ tự phụ thuộc, rồi tạo mission để các worker kéo từng task một. (plausible-but-unverified)", "cit": ["orchestrator/mission/tools.py:241", "orchestrator/mission/tools.py:249", "orchestrator/mission/tools.py:253", "orchestrator/mission/tools.py:286", "orchestrator/mission/tools.py:287"], "h": "orchestrator/mission/tools.py:240", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "missions.db (mission + task rows, ledger row)"}], "err": {"c": "i", "r": "output-history.md:L122 (R31)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "mission_dispatch", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "mission", "eff": "Dispatch một queued task tới worker agent của nó: từ chối các task destructive khi chưa có human-gate approval được ghi nhận (block task và trả NEEDS_HUMAN_GATE), bảo đảm worker model đã được đặt (lấy từ model_hint của task nếu chưa đặt), dựng wrapper prompt, ghi một outbox row cộng một dispatch_intent ledger row, CAS-transition task từ pending/ready sang dispatching, gửi wrapper tới agent (agent mặc định 'codo'), rồi transition dispatching sang started kèm lease và child pid. Nó không bao giờ tự chạy task — nó trao wrapper cho worker agent.", "uc": "Sau khi mission_next trả về ready task kế tiếp, leader dispatch nó tới worker agent được hint và theo dõi outbox row cho tới khi worker báo về. (plausible-but-unverified)", "cit": ["orchestrator/mission/tools.py:322", "orchestrator/mission/tools.py:340", "orchestrator/mission/tools.py:362", "orchestrator/mission/tools.py:371", "orchestrator/mission/tools.py:380", "orchestrator/mission/tools.py:393"], "h": "orchestrator/mission/tools.py:309", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "missions.db (outbox + ledger rows, task state transitions)"}], "err": {"c": "i", "r": "output-history.md:L122 (R31)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "mission_gate", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "mission", "eff": "Ghi một quyết định approve/reject của con người cho một task: approve đưa blocked task trở lại pending (để nó có thể được dispatch), reject đưa nó sang skipped; một task không bị block bị force-move tới trạng thái đích, và mọi quyết định được append vào mission ledger kèm reason. Chỉ 'approve' hoặc 'reject' được chấp nhận; bất kỳ giá trị nào khác là INVALID_DECISION.", "uc": "Một con người approve một destructive task mà mission_dispatch đã block, mở khóa cho nó được dispatch. (plausible-but-unverified)", "cit": ["orchestrator/mission/tools.py:539", "orchestrator/mission/tools.py:542", "orchestrator/mission/tools.py:543", "orchestrator/mission/tools.py:546"], "h": "orchestrator/mission/tools.py:529", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "missions.db (task state + human_gate ledger row)"}], "err": {"c": "i", "r": "output-history.md:L122 (R31)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "mission_report", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "mission", "eff": "Báo cáo kết quả của một worker attempt và tiến state machine của task trong missions.db: một heartbeat chỉ gia hạn lease của task đã có lease (ngược lại bị từ chối bằng HEARTBEAT_NOT_LEASED); một final report validate attempt token, loại các stale attempt, kiểm verify gate của task với result artifact (file_exists, hoặc artifact_complete kèm một end marker), đưa task đạt qua verifying tới done, và retry một task trượt về pending (tối đa max_attempts) hoặc failed. Nó không bao giờ tự ghi artifact — nó chỉ kiểm tra artifact tồn tại hay đã hoàn tất.", "uc": "Một worker làm xong task báo về kèm attempt token và result path của nó; gate check của leader đánh dấu task done hoặc retry nó. (plausible-but-unverified)", "cit": ["orchestrator/mission/tools.py:432", "orchestrator/mission/tools.py:446", "orchestrator/mission/tools.py:463", "orchestrator/mission/tools.py:470", "orchestrator/mission/tools.py:486"], "h": "orchestrator/mission/tools.py:415", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "missions.db (task state transitions + ledger rows)"}], "err": {"c": "i", "r": "output-history.md:L122 (R31)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "normalize_ca_registration", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Chuẩn hóa mọi row của agents_registry.json về mode=A cộng một transport_policy mà máy kiểm tra được, bằng cách load registry, tính bản copy đã chuẩn hóa, và — chỉ khi dry_run=false — ghi một .bak_cslt backup của file hiện tại rồi thay thế atomic nó bằng bản đã chuẩn hóa. Với dry_run=true (mặc định) nó chỉ trả về preview các thay đổi và agent count, không ghi gì.", "uc": "Sau khi sửa agent registry bằng tay, một CA trước tiên gọi normalize_ca_registration() để preview các thay đổi chuẩn hóa, rồi gọi lại với dry_run=false để áp chúng vào agents_registry.json.", "cit": ["tools_v15.py:9911", "tools_v15.py:9927", "tools_v15.py:9929", "tools_v15.py:9943", "tools_v15.py:9945", "code_tools.py:206", "code_tools.py:17301"], "h": "tools_v15.py:9911", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "agents_registry.json (code_tools.py:17301)", "condition": "dry_run=false (default true)"}, {"kind": "file", "target": "agents_registry.json.bak_cslt", "condition": "dry_run=false (default true)"}], "err": {"c": "ii", "r": "output-history.md:L131 (R40)", "q": "dry_run=false rewrites agents_registry.json + .bak_cslt; default dry_run=true writes nothing"}, "use": {"b": "rare", "c": 1, "ls": "2026-11-10T16:43:15"}},
{"n": "owner_heartbeat", "t": "T3", "cat": "t3", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Ghi nhận owner liveness đã xác thực: load task checkpoint của session và từ chối trừ khi caller là committed root owner trình owner_capability SHA-256 khớp với owner_claim_status='committed' (TASK_OWNER_REQUIRED / TASK_OWNER_MISMATCH / OWNER_CLAIM_NOT_COMMITTED). Khi thành công nó durable upsert checkpoint với owner_last_heartbeat_at (+ISO), owner_heartbeat_count+1 và owner_heartbeat_source='owner_heartbeat' qua store INSERT INTO task_checkpoints ... ON CONFLICT DO UPDATE (COMMIT với WAL/NORMAL sống qua process crash), và trả về owner_liveness='active' kèm heartbeat_count + TTL. Các nhánh từ chối không ghi gì.", "uc": "Trong khi giữ một task dài, CA root-owner gọi owner_heartbeat trước khi TTL hết hạn để chứng minh nó vẫn còn sống, để task không bị nghi stuck hoặc orphaned.", "cit": ["code_tools.py:10426", "code_tools.py:10435", "code_tools.py:10458", "code_tools.py:10477", "orchestrator/session/store.py:2381"], "h": "code_tools.py:10426", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "sessions.db task_checkpoints row (owner_last_heartbeat_at, owner_heartbeat_count+1, owner_heartbeat_source)", "condition": "owner validation passes (reject paths write nothing)"}], "err": {"c": "i", "r": "output-history.md:L151 (R60)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "prepare_ca_session", "t": "T3", "cat": "session", "mut": true, "prot": true, "sess": false, "fg": "session", "eff": "Chuẩn bị CA session của một project trong một lời gọi: sinh lại project-map snapshot index (INDEX.json) khi thiếu — hoặc khi PROJECT_CONTEXT.md thiếu, qua update_file_cache + regenerate — dựng/cache structure_output.txt (cây depth-5) khi thiếu, rồi LUÔN ghi bootstrap prompt file và workflow-state JSON, chạy matrix audit (vốn append .orchestrator_cache/matrix_log.jsonl, vì _audit_tool_matrix_sync mặc định persist=True), mở/resume ToolA session qua open_session, và (khi host_session_id + agent_id được truyền) bootstrap canonical owner claim. Quyền ownership được resolve trước khi bất kỳ artifact nào được ghi. 'Index' ở đây là snapshot INDEX.json, không phải CBM graph DB.", "uc": "Khi bắt đầu công việc trên một project mới, một CA gọi prepare_ca_session một lần để lấy map, structure cache, bootstrap prompt, workflow state và một session đang mở, thay vì gọi riêng lẻ từng setup tool.", "cit": ["code_tools.py:2458", "code_tools.py:2480", "code_tools.py:2489", "code_tools.py:2525", "code_tools.py:2532", "code_tools.py:2546", "code_tools.py:2574"], "h": "code_tools.py:2458", "wk": "none", "der": false, "wr": [{"kind": "index", "target": "project-map snapshot index INDEX.json (via surgical_mapper.generate_project_map)", "condition": "INDEX.json missing, or PROJECT_CONTEXT.md missing"}, {"kind": "file", "target": "<project cache>/structure_output.txt (depth-5 tree)", "condition": "file missing"}, {"kind": "file", "target": "bootstrap prompt file (_bootstrap_path)", "condition": "none - every successful call writes"}, {"kind": "file", "target": "workflow state JSON (_workflow_state_path)", "condition": "none - every successful call writes"}, {"kind": "file", "target": ".orchestrator_cache/matrix_log.jsonl (audit record, append)", "condition": "every call (_audit_tool_matrix_sync persist=True default)"}, {"kind": "sqlite", "target": "sessions.db sessions row (via open_session step)", "condition": "open_session step always runs"}], "note": "Matrix sess:true was stale; live requires_session=false (R60k).", "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "record_error_resolution", "t": "T3", "cat": "t3", "mut": true, "prot": true, "sess": false, "fg": "ops", "eff": "Append một resolution event cho một error event xác định trong sessions.db — nhưng chỉ sau một ownership gate: calling_context phải mang validated=true cộng một session_id, và session đích phải tồn tại và là session của chính caller hoặc một session host-task-correlated (kiểm bằng SELECT trên sessions/events); nếu không nó từ chối bằng SESSION_OWNERSHIP_REQUIRED và write_performed=false. Khi qua được gate, delegate _store._record_error_resolution validate rằng đích là một error event trong phạm vi và INSERT một resolution row (event_kind='resolution', resolves_event_id=target, kind recovered / expected_failure / reopened) rồi trả về resolution_event_id mới. Usage là UNKNOWN — tool này nằm trong skip-list (không được log) (R11).", "uc": "Sau khi xác minh một fix, một CA đánh dấu error event 12345 trong session của chính nó là recovered để session_audit_summary đếm nó đã resolved thay vì open.", "cit": ["tools_v15.py:2729", "tools_v15.py:2738", "tools_v15.py:2760", "tools_v15.py:2781", "orchestrator/session/store.py:2558", "orchestrator/session/store.py:2571"], "h": "tools_v15.py:2714", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "sessions.db events (event_kind='resolution', resolves_event_id=<target>)", "condition": "ownership gate passes (validated host context + same-session or host-task-correlated target)"}], "use": {"b": "unknown", "c": null, "ls": null}},
{"n": "record_session_audit_checkpoint", "t": "T3", "cat": "audit,session", "mut": true, "prot": true, "sess": true, "fg": "audit", "eff": "Nửa mutating của session_audit_summary cũ: trước tiên nó tính summary chỉ-đọc bằng cách gọi session_audit_summary, rồi persist một checkpoint EVENT qua code_tools._log_session_event (chạy ngoài event loop trong một thread, vì SessionWriter block trên một durable-commit Future), mang theo một note bị cắt còn 200 chars và một counts block gọn (mutations, validations, errors, recovered_errors, open_errors, caller_errors, backend_errors, native_fallback_count). Nó trả về checkpoint_event_id; nếu bản thân summary lỗi, không có gì được persist.", "uc": "Tại một milestone của task, một CA ghi một checkpoint với note 'phase 2 done' để session log giữ một audit snapshot đông cứng (counts + note) mà session_audit_summary về sau có thể hiển thị.", "cit": ["tools_v15.py:1408", "tools_v15.py:1417", "tools_v15.py:1422", "tools_v15.py:1425"], "h": "tools_v15.py:1404", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "sessions.db events (tool_name='session_audit_checkpoint') via code_tools._log_session_event"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "rename_session", "t": "T3", "cat": "session", "mut": true, "prot": true, "sess": true, "fg": "session", "eff": "Đổi tên active session của agent này: SELECT sessions row đang active của project (và agent_id khi được truyền) bên trong _with_sessions_db, rồi áp _run_session_write với _session_store_module._rename_session_title, một UPDATE durable title của sessions trong sessions.db. Khi không có active session nào tồn tại, nó fail loud với SESSION_CLOSED ('Closed sessions cannot be renamed') — nó KHÔNG tự mở một session.", "uc": "Sau khi mở một session cho một feature, một CA đổi tên nó từ title tự động 'untitled-session-...' sang tên feature để list_sessions hiển thị một title có ý nghĩa.", "cit": ["code_tools.py:16196", "code_tools.py:16206", "code_tools.py:16220", "code_tools.py:16234"], "h": "code_tools.py:16196", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "sessions.db sessions row title UPDATE for the active session (via _rename_session_title)"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "session_audit_summary", "t": "T3", "cat": "audit,session", "mut": false, "prot": false, "sess": false, "fg": "audit", "eff": "Tổng hợp chỉ-đọc thuần trên sessions.db (SELECT-only): nó resolve scope bằng session_id/target_session_id hoặc active session mới nhất của một project_path, trả về SESSION_SCOPE_AMBIGUOUS khi có several active session tồn tại và không có agent_id nào phân định; rồi nó đếm tools_used, mutations (action_type write/workflow), validations, errors, native_fallback_count, và pending audits. Trạng thái resolution của từng error đến từ các lookup resolves_event_id / event_kind='resolution' — chính các row mà record_error_resolution ghi — và nó trả về open_risks cộng next_recommended_action (close_session khi sạch, ngược lại investigate_open_risks_first). Nó là nửa chỉ-đọc của record_session_audit_checkpoint, tool bổ sung nửa persist.", "uc": "Tại một milestone, một CA audit session hiện tại — bao nhiêu mutations và validations, những errors nào còn open, và liệu đóng session có an toàn hay không.", "cit": ["tools_v15.py:1198", "tools_v15.py:1222", "tools_v15.py:1267", "tools_v15.py:1315", "tools_v15.py:1393"], "h": "tools_v15.py:1176", "wk": "none", "der": false, "wr": [], "note": "Registry-label anomaly (R49): category includes “write” but the body is SELECT-only; not a flag conflict.", "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "sync_pending_audit", "t": "T3", "cat": "audit", "mut": true, "prot": true, "sess": false, "fg": "audit", "eff": "Đồng bộ các pending audit row trong phạm vi vào session audit trail: resolve session đích (target_session_id tường minh, nếu không thì active session, nếu không nữa thì tự mở một session qua _auto_open_session), rồi trong một transaction durable _run_session_write, với mỗi pending row nó INSERT một events row (tool_origin='native', status='ok', reason mang instead_of/failed_error/pending_audit_id) và UPDATE pending_audits row sang sync_status='synced' (hoặc 'failed' khi lỗi ở từng row) kèm session_id + synced_at. Các row legacy (agent_id NULL) chỉ đồng bộ khi có opt-in tường minh include_legacy=true VÀ một target_session_id. _init_session_db schema init chạy trước (CREATE TABLE IF NOT EXISTS idempotent, orchestrator/session/store.py:1482 — không được đếm là một write).", "uc": "Sau khi một native fallback (công việc làm ngoài [A]) được ghi như một pending audit, một CA chạy sync_pending_audit để chuyển các row đó vào bảng events của active session với sync_status='synced', để audit trail trở nên đầy đủ.", "cit": ["code_tools.py:16502", "code_tools.py:16509", "code_tools.py:16628", "code_tools.py:16637", "code_tools.py:16554"], "h": "code_tools.py:16502", "wk": "none", "der": false, "wr": [{"kind": "sqlite", "target": "sessions.db events INSERT (one per synced pending audit, tool_origin='native')"}, {"kind": "sqlite", "target": "sessions.db pending_audits UPDATE sync_status='synced'/'failed' + session_id + synced_at"}, {"kind": "sqlite", "target": "sessions.db sessions INSERT (auto-opened session)", "condition": "no active session and no target_session_id — the tool auto-opens one"}], "note": "Matrix sess:true was stale; live requires_session=false (R65c).", "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_activate", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Kích hoạt một workflow toàn cục: tìm workflow MD, tự khởi động state qua workflow_start khi chưa có state nào tồn tại (bước này lưu workflow state qua _wf_save_state, code_tools.py:13296), rồi vô điều kiện ghi file marker active-workflow _active.json trong thư mục workflow state dir của project (open(..., 'w') + json.dump, code_tools.py:13935-13936) ghi lại name/activated_at/source/md_path, và báo cáo current step. [A] sau đó inject workflow hints vào các responses. Việc ghi marker xảy ra trên default success path, mọi lời gọi.", "uc": "Một CA kích hoạt workflow getfootage theo tên để [A] bắt đầu inject các step hint của nó vào responses, và CA theo các step qua workflow_get_current + workflow_submit_step.", "cit": ["code_tools.py:13910", "code_tools.py:13923", "code_tools.py:13935", "code_tools.py:13905", "code_tools.py:13296"], "h": "code_tools.py:13910", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "workflow active marker _active.json in the project workflow state dir (via _wf_active_path)"}, {"kind": "file", "target": "workflow state file (via workflow_start → _wf_save_state)", "condition": "no workflow state exists yet — auto-start branch"}], "err": {"c": "i", "r": "output-history.md:L156 (R65)", "q": "registry self-contradiction: category write + scope stateful vs mutating=false"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_checkpoint", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Checkpoint drift giữa luồng: thăm dò session health (_get_active_session_id), các transport-policy violation gần đây (get_session_events, quét chỉ-đọc), và — chỉ khi workflow_name được truyền — workflow health qua workflow_get_current, rồi trả về một verdict tổng hợp drift_detected + warnings[]. KHÔNG CÓ GÌ được persist: checkpoint_id được sinh trong bộ nhớ (f\"ck-{int(time.time()*1000)}\", 12685) và recorded_at là một timestamp field trong JSON trả về (12686); không có write nào vào file, DB, session-store hay checkpoint-table ở bất kỳ đâu trong body (12500-12708, đọc hết từ đầu đến cuối). Bất chấp cái tên ('checkpoint', 'recorded_at'), tool không ghi lại gì — verdict được tính và trả về, rồi bị bỏ đi.", "uc": "Giữa các step mutating của một workflow dài, một CA gọi workflow_checkpoint(workflow_name='...') để hỏi liệu run vẫn đúng đường ray (session sống, không có policy drift gần đây, workflow không bị paused) hay chưa trước khi tiếp step kế.", "cit": ["code_tools.py:12500", "code_tools.py:12541", "code_tools.py:12570", "code_tools.py:12685", "code_tools.py:12686"], "h": "code_tools.py:12500", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_create_from_ui", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Lưu nguyên văn một workflow MD body vào thư mục workflows toàn cục: slugify tên, mkdir <repo>/.ToolA_Skill/workflows (14026), từ chối khi đích đã tồn tại trừ phi overwrite=true (14032), rồi ghi file bằng open(target, 'w') + f.write(body_md) (14038-14039) và trả về title/step count đã parse. scope bị ép thành 'global' và project_path bị bỏ qua (R37). Việc ghi xảy ra trên default success path, mọi lời gọi qua được các guard. mkdir thư mục workflow là vô nội dung và idempotent — không phải một durable write (R66).", "uc": "Sau khi user xem lại một workflow draft trong UI editor và bấm Save, UI gọi workflow_create_from_ui(name, body_md) để durable hóa MD dưới dạng <slug>.md trong thư mục workflows toàn cục để workflow_list / workflow_start tìm được nó.", "cit": ["code_tools.py:14009", "code_tools.py:14023", "code_tools.py:14026", "code_tools.py:14038", "code_tools.py:14039"], "h": "code_tools.py:14009", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "global workflow definition .ToolA_Skill/workflows/<slug>.md (open 'w' + write, code_tools.py:14038-14039)"}], "err": {"c": "i", "r": "output-history.md:L159 (R68)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_deactivate", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Dừng workflow mode bằng cách xóa active-workflow marker: resolve _active.json qua _wf_active_path (13951), đọc nó cho was_active echo, rồi ap.unlink() (13960) gỡ file khi nó tồn tại, và luôn trả về deactivated:true. Việc xóa là chức năng lõi của tool trên default path của nó mỗi khi một workflow đang active; khi không có marker nào tồn tại thì không có gì để xóa và lời gọi là một no-op trả về cùng hình dạng success.", "uc": "Một CA hoàn tất hoặc bỏ dở một workflow run và gọi workflow_deactivate để [A] ngừng inject các step hint của workflow đó vào responses.", "cit": ["code_tools.py:13949", "code_tools.py:13951", "code_tools.py:13960", "code_tools.py:13963"], "h": "code_tools.py:13949", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "workflow active marker _active.json — DELETED via unlink (code_tools.py:13960)", "condition": "only when the _active.json marker exists (the normal active case)"}], "err": {"c": "i", "r": "output-history.md:L159 (R68)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_draft_create", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Đẩy một workflow MD draft do agent soạn vào store trong bộ nhớ _WORKFLOW_DRAFTS (dict cấp module, code_tools.py:13813) dưới một random token (13869), TTL bị kẹp trong 60s..24h, và trả về token + editor_url để UI editor kéo về. Việc ghi chỉ-trong-BỘ-NHỚ: không file, DB hay disk state nào bị đụng; các draft tự hết hạn qua _wf_purge_expired_drafts (13858) và mất khi restart. Draft chỉ trở thành durable về sau, khi user lưu nó qua workflow_create_from_ui.", "uc": "Một CA soạn nháp một workflow MD mới và gọi workflow_draft_create để trao nó cho UI editor (qua URL /ui/workflow_editor?draft=<token> được trả về) cho user xem lại trước khi nó được persist.", "cit": ["code_tools.py:13852", "code_tools.py:13858", "code_tools.py:13869", "code_tools.py:13877"], "h": "code_tools.py:13852", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_goto_step", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Nhảy một workflow đã khởi động tới một step bất kỳ (tiến, lùi, hoặc noop): load state (12904), từ chối các workflow chưa khởi động, validate số step đích, append một jump_history entry (12920-12926), đặt current_step (12927) và step đích thành active (12928-12932), xóa paused/pause_reason (12934-12935), rồi persist mọi thứ với _wf_save_state (12936), vốn mở state file để ghi và json.dumps state (code_tools.py:13170-13174). Việc ghi state xảy ra trên default success path, mọi lời gọi qua được các guard. Save được thử, không được bảo đảm: _wf_save_state nuốt các save error (code_tools.py:13170-13177) — nói “save attempted”, đừng bao giờ nói “state persisted”.", "uc": "Một CA đã trôi khỏi thứ tự đã lên kế hoạch (hoặc phải làm lại một step sớm hơn) gọi workflow_goto_step(name, target_step) để nhảy workflow tới step đó, với cú nhảy được ghi trong jump_history phục vụ audit.", "cit": ["code_tools.py:12886", "code_tools.py:12904", "code_tools.py:12927", "code_tools.py:12936"], "h": "code_tools.py:12886", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "workflow state file <slug>.state.json in the project workflow state dir (via _wf_save_state, code_tools.py:12936)"}], "err": {"c": "i", "r": "output-history.md:L159 (R68)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_reset", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Reset một workflow: đổi tên state file hiện hữu thành một archive có timestamp <slug>.state.json.bak.<ts> (sp.rename, 13734), xóa các file .bak đã archive vượt quá 3 file mới nhất mỗi slug (rotation, 13744), rồi khởi động lại workflow qua workflow_start (13750), vốn tái tạo một state file mới qua _wf_save_state (code_tools.py:13296). Cả ba hiệu ứng filesystem đều xảy ra trên default success path (việc archive/xóa chỉ khi một state file trước đó tồn tại, tức trường hợp reset thông thường). Save được thử, không được bảo đảm: _wf_save_state nuốt các save error (code_tools.py:13170-13177) — nói “save attempted”, đừng bao giờ nói “state persisted”.", "uc": "Một workflow run bị hỏng hoặc MD của nó đã thay đổi, nên một CA gọi workflow_reset để archive state của run cũ và khởi động lại chính workflow đó từ đầu ở step 1.", "cit": ["code_tools.py:13719", "code_tools.py:13734", "code_tools.py:13744", "code_tools.py:13750"], "h": "code_tools.py:13719", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "workflow state file renamed to archive <slug>.state.json.bak.<ts> (code_tools.py:13734)", "condition": "only when a prior state file exists (the normal reset case)"}, {"kind": "file", "target": "oldest <slug>.state.json.bak.* archives — DELETED by rotation (destructive, code_tools.py:13744)", "condition": "only when more than 3 .bak archives exist for the slug"}, {"kind": "file", "target": "fresh workflow state file via workflow_start → _wf_save_state (code_tools.py:13750)"}], "err": {"c": "i", "r": "output-history.md:L159 (R68)"}, "note": "Rotation delete is destructive: archived .bak files beyond the newest 3 are DELETED (R68g).", "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_retry_step", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Retry step hiện tại sau một fail/pause: load state (13661), từ chối các workflow chưa khởi động, reset current step về status active kèm restarted_at (13666-13667), xóa paused/pause_reason (13668-13669), rồi persist với _wf_save_state (13670), vốn mở state file để ghi và json.dumps state (code_tools.py:13170-13174). Việc ghi state xảy ra trên default success path, mọi lời gọi qua được các guard. Save được thử, không được bảo đảm: _wf_save_state nuốt các save error (code_tools.py:13170-13177) — nói “save attempted”, đừng bao giờ nói “state persisted”.", "uc": "Sau khi một step fail và workflow paused, user quyết định retry, nên CA gọi workflow_retry_step để kích hoạt lại chính step đó thay vì tiến qua nó.", "cit": ["code_tools.py:13654", "code_tools.py:13666", "code_tools.py:13670", "code_tools.py:13673"], "h": "code_tools.py:13654", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "workflow state file <slug>.state.json in the project workflow state dir (via _wf_save_state, code_tools.py:13670)"}], "err": {"c": "i", "r": "output-history.md:L159 (R68)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_save_md", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Alias phía-agent của workflow_create_from_ui: lưu nguyên văn một workflow MD body vào thư mục workflows toàn cục — slugify tên, mkdir <repo>/.ToolA_Skill/workflows (14026), từ chối khi đích đã tồn tại trừ phi overwrite=true (14032), rồi ghi file bằng open(target, 'w') + f.write(body_md) (14038-14039) và trả về title/step count đã parse. scope bị ép thành 'global' và project_path bị bỏ qua (R37). Việc ghi xảy ra trên default success path, mọi lời gọi qua được các guard. KHÔNG có def workflow_save_md riêng: tên trong registry ánh xạ tới hàm workflow_create_from_ui (Wave 6 distinct-defs note: 254 distinct defs = 260 - 5 search_code - 1 workflow_create_from_ui x2). mkdir thư mục workflow là vô nội dung và idempotent — không phải một durable write (R66).", "uc": "Một CA đã đọc một workflow bằng workflow_read_md và sửa phần MD text gọi workflow_save_md(name, body_md) để durable hóa bản đã sửa dưới dạng <slug>.md trong thư mục workflows toàn cục.", "cit": ["code_tools.py:14009", "code_tools.py:14026", "code_tools.py:14038", "code_tools.py:14039"], "h": "code_tools.py:14009", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "global workflow definition .ToolA_Skill/workflows/<slug>.md (open 'w' + write, code_tools.py:14038-14039)"}], "err": {"c": "i", "r": "output-history.md:L159 (R68)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_skip_step", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Bỏ qua step hiện tại và tiến: load state (13683), từ chối các workflow chưa khởi động, đánh dấu current step skipped kèm skipped_at + reason (13688-13690), xóa paused/pause_reason (13691-13692), rồi hoặc đánh dấu workflow complete (13702-13704) hoặc tiến current_step và đánh dấu step kế active (13711-13713) — ở CẢ HAI nhánh đều persist với _wf_save_state (13704 / 13714), vốn mở state file để ghi và json.dumps state (code_tools.py:13170-13174). Việc ghi state xảy ra trên default success path, mọi lời gọi qua được các guard. Save được thử, không được bảo đảm: _wf_save_state nuốt các save error (code_tools.py:13170-13177) — nói “save attempted”, đừng bao giờ nói “state persisted”.", "uc": "Sau khi một step fail và workflow paused, user quyết định bỏ qua nó, nên CA gọi workflow_skip_step kèm một reason để đánh dấu step skipped và chuyển sang step kế.", "cit": ["code_tools.py:13676", "code_tools.py:13688", "code_tools.py:13704", "code_tools.py:13714"], "h": "code_tools.py:13676", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "workflow state file <slug>.state.json in the project workflow state dir (via _wf_save_state, code_tools.py:13704 / 13714)"}], "err": {"c": "i", "r": "output-history.md:L159 (R68)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_start", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Khởi động một workflow theo tên: tìm MD (13267), đọc và parse nó, dựng một state dict mới (workflow, title, started_at, current_step = số step đầu tiên, steps {first: active}, complete=False, 13282-13295), rồi persist nó với _wf_save_state (13296), vốn mở state file để ghi và json.dumps state (code_tools.py:13170-13174), và trả về body của step 1. Việc ghi state xảy ra trên default success path, mọi lời gọi tìm thấy MD. Đây là delegate đứng sau nhánh auto-start của workflow_activate (Batch 9) — verdict này đọc từ body của chính nó, không sao chép từ Batch 9. Save được thử, không được bảo đảm: _wf_save_state nuốt các save error (code_tools.py:13170-13177) — nói “save attempted”, đừng bao giờ nói “state persisted”.", "uc": "Một CA bắt đầu một workflow run bằng cách gọi workflow_start(name) sau khi khám phá nó qua workflow_list; state của run được tạo và body của step 1 được trả về để thực thi.", "cit": ["code_tools.py:13263", "code_tools.py:13282", "code_tools.py:13296", "code_tools.py:13297"], "h": "code_tools.py:13263", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "workflow state file <slug>.state.json in the project workflow state dir (via _wf_save_state, code_tools.py:13296)"}], "err": {"c": "i", "r": "output-history.md:L159 (R68)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "workflow_submit_step", "t": "T3", "cat": "workflow", "mut": false, "prot": false, "sess": false, "fg": "workflow", "eff": "Nộp step hiện tại: validate status (done|fail) và số step so với current_step, thực thi R77 artifact gate cho các step khai báo required artifacts (status=done chỉ được chấp nhận khi output đã khai báo thật sự tồn tại trên disk, 13548-13561), ghi step status + evidence trong bộ nhớ (13563-13567), rồi: khi fail nó pause workflow (paused=true, pause_reason=step_failed) và lưu state (13572); khi done nó tùy chọn soft-check các tag [completion_signal:] qua assert_target_idle (13593, chỉ cảnh báo), sau đó hoặc hoàn tất workflow (13631-13633) hoặc tiến current_step và đánh dấu step kế active (13644-13646) — lưu state ở MỌI nhánh thành công (batch path 13513/13524, fail path 13572, complete path 13633, advance path 13647), mỗi lần qua _wf_save_state, vốn mở state file để ghi và json.dumps state (code_tools.py:13170-13174). Việc ghi state xảy ra trên default success path, mọi lời gọi qua được validation. Lưu vào cả năm site: run_profile.json, workflow_start_params.json, workflow_state.json, workflow_state_archive.json và workflow_status.json. Save được thử, không được bảo đảm: _wf_save_state nuốt các save error (code_tools.py:13170-13177) — nói “save attempted”, đừng bao giờ nói “state persisted”.", "uc": "Sau khi thực thi step workflow hiện tại, một CA gọi workflow_submit_step(name, step, status='done', evidence='...') để ghi kết quả và tiến; khi thất bại nó nộp status='fail' và workflow pause để user quyết định retry/skip.", "cit": ["code_tools.py:13406", "code_tools.py:13502", "code_tools.py:13572", "code_tools.py:13647"], "h": "code_tools.py:13406", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "workflow state file <slug>.state.json in the project workflow state dir (via _wf_save_state, code_tools.py:13513 / 13524 / 13572 / 13633 / 13647)"}], "err": {"c": "i", "r": "output-history.md:L159 (R68)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "write_draft_abort", "t": "T3", "cat": "write", "mut": false, "prot": false, "sess": false, "fg": "write", "eff": "Bỏ một pending write draft khỏi draft store trong bộ nhớ mà không ship bất kỳ gì: pop draft_id và báo cáo draft đã abort cùng target file của nó; một draft_id không biết hoặc đã hết hạn trả lời DRAFT_NOT_FOUND. Target file không bao giờ bị đụng — không có gì được ghi, revert hay xóa trên disk.", "uc": "Một CA bỏ dở một draft đang fail sau khi quyết định viết lại thay đổi từ đầu thay vì patch nó. (plausible-but-unverified)", "cit": ["orchestrator/tools/write/draft.py:487", "orchestrator/tools/write/draft.py:489", "orchestrator/tools/write/draft.py:491"], "h": "orchestrator/tools/write/draft.py:483", "wk": "none", "der": false, "wr": [], "use": {"b": "rare", "c": 2, "ls": "2026-11-09T09:56:14"}},
{"n": "write_draft_status", "t": "T3", "cat": "write", "mut": false, "prot": false, "sess": false, "fg": "write", "eff": "Khảo sát các write draft mà không thay đổi bất kỳ file nào: không có draft_id thì nó liệt kê mọi pending draft (tool, target file, line count, failing flag, patch count, seconds to expiry) sau khi quét các draft hết hạn khỏi draft store trong bộ nhớ, và với một draft_id nó trả về full view của draft đó (lines, per-part failures, excerpts). Một draft_id không biết hoặc đã hết hạn trả lời DRAFT_NOT_FOUND.", "uc": "Một CA kiểm tra những draft nào vẫn đang fail và còn bao lâu nữa chúng hết hạn trước khi quyết định patch gì tiếp theo. (plausible-but-unverified)", "cit": ["orchestrator/tools/write/draft.py:443", "orchestrator/tools/write/draft.py:444", "orchestrator/tools/write/draft.py:472", "orchestrator/tools/write/draft.py:476"], "h": "orchestrator/tools/write/draft.py:438", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
// T4 — Desktop/Runtime/UI
{"n": "assert_button_click_changes_state", "t": "T4", "cat": "assert", "mut": true, "prot": true, "sess": true, "fg": "test", "eff": "Chụp lại văn bản của observed_selector, thực hiện một click thật trên button_selector trong trang Playwright đang sống, chờ 500 ms, chụp lại phần tử được quan sát một lần nữa, và trả về changed cùng văn bản trước/sau (mỗi bên cắt còn 200 ký tự). Click là input thật vào trang, không phải mô phỏng: phần tử được quan sát được kỳ vọng sẽ thay đổi vì nó. State vẫn trả lời ok:true ngay cả khi changed=false — rẽ nhánh theo trường changed, không theo ok.", "uc": "Một CA xác minh một bản sửa UI bấm nút Scan và khẳng định văn bản #result thực sự thay đổi, thay vì chỉ kiểm tra nút có tồn tại.", "cit": ["tools_v15.py:9566", "tools_v15.py:9567", "tools_v15.py:9568", "tools_v15.py:9569", "tools_v15.py:9570"], "h": "tools_v15.py:9557", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "browser page DOM state"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "assert_console_no_error", "t": "T4", "cat": "assert", "mut": false, "prot": false, "sess": false, "fg": "test", "eff": "Đọc log console trong bộ nhớ mà phiên đã thu của trang (state['console']) và trả về error_count, ok (chỉ true khi có zero console error), cùng tối đa năm mẫu lỗi. Tool không bao giờ click, điều hướng, hay evaluate bất cứ gì trong trang; nó chỉ xem xét log mà Playwright session đã ghi nhận sẵn.", "uc": "Sau khi nạp trang đang được test, một CA gọi assert_console_no_error để cho check thất bại nếu trang có ghi log console.error trong lúc nạp.", "cit": ["tools_v15.py:9583", "tools_v15.py:9586", "tools_v15.py:9587", "tools_v15.py:9591"], "h": "tools_v15.py:9581", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "assert_text_visible", "t": "T4", "cat": "assert", "mut": false, "prot": false, "sess": false, "fg": "test", "eff": "Chờ tối đa timeout_ms (mặc định 2000) để đoạn văn bản đã cho trở nên hiển thị trên trang qua locator text= của Playwright, rồi trả về visible:true; nếu văn bản không bao giờ xuất hiện, tool trả về ASSERTION_FAILED. Việc chờ thuần túy là quan sát trang đang sống.", "uc": "Một CA chờ văn bản 'Scan complete' xuất hiện trên trang kết quả trước khi đọc các giá trị kết quả.", "cit": ["tools_v15.py:9546", "tools_v15.py:9550", "tools_v15.py:9551", "tools_v15.py:9552", "tools_v15.py:9554"], "h": "tools_v15.py:9544", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "bundle_version_check", "t": "T4", "cat": "t4", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Fetch tối đa 50 KB của một trang HTML đang được serve qua HTTP (timeout 5 s) và trích một marker build/version bằng regex: pattern mặc định khớp meta name=\"version\" content=\"...\" hoặc một giá trị truy vấn ?v=, và nhóm capture khác rỗng đầu tiên được trả về dưới dạng version_found. Một regex không hợp lệ, hoặc một regex không có nhóm capture, bị từ chối trước khi có bất kỳ lượt fetch nào.", "uc": "Một CA gọi bundle_version_check trên URL của trang monitor đang được serve để xác nhận build đã triển khai mang đúng version marker mong đợi sau một lần deploy.", "cit": ["tools_v15.py:7148", "tools_v15.py:7155", "tools_v15.py:7162", "tools_v15.py:7163", "tools_v15.py:7164", "tools_v15.py:7167"], "h": "tools_v15.py:7144", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "cancel_native_dialog", "t": "T4", "cat": "desktop", "mut": true, "prot": true, "sess": false, "fg": "desktop", "eff": "Đóng một dialog Windows native bằng cách tìm cửa sổ top-level của nó (title regex, các bộ lọc tùy chọn owner_pid / parent_hwnd / exclude_titles) và bấm nút Cancel/Close của nó qua BM_CLICK, rơi về WM_CLOSE khi không có nút nào khớp, sau đó poll tối đa 2s cho đến khi cửa sổ biến mất. Trả về DIALOG_NOT_FOUND khi không có gì khớp và DIALOG_TARGET_AMBIGUOUS (không bấm gì) khi binding expected_hwnd không còn khớp đúng một ứng viên. Từ chối hoạt động trên các host thiếu pywin32. Handler chỉ là một alias wrapper mỏng: phần cài đặt nằm ở orchestrator/runtime/desktop_dialog_controls.py:148 (được import là _runtime_cancel_native_dialog tại code_tools.py:8616-8618). Nhánh WM_CLOSE fallback báo ok:true với button_clicked:false; dialog đóng lại ≠ cancelled.", "uc": "Trong một lượt tự động hóa desktop, một dialog native lạc (ví dụ popup lỗi) chặn app; CA gọi cancel_native_dialog kèm title pattern và owner_pid của dialog để gạt nó đi và tiếp tục flow.", "cit": ["code_tools.py:8675", "code_tools.py:8681", "orchestrator/runtime/desktop_dialog_controls.py:178", "orchestrator/runtime/desktop_dialog_controls.py:314", "orchestrator/runtime/desktop_dialog_controls.py:321"], "h": "code_tools.py:8675", "wk": "none", "der": false, "wr": [{"kind": "ui", "target": "native dialog Cancel/Close button via BM_CLICK, or the dialog window via WM_CLOSE fallback"}], "note": "Matrix sess:true was stale; live requires_session=false (R57f).", "use": {"b": "light", "c": 9, "ls": "2026-11-10T18:11:11"}},
{"n": "cancel_pending_restart", "t": "T4", "cat": "t4", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Hủy một lượt restart orchestrator đang chờ bằng cách quét mọi process và kill bất kỳ process nào có dòng lệnh nhắc tới các marker restart-helper là restart_helper.cmd, restart_bat_helper.cmd, hoặc via_bat_helper (helper do restart_orchestrator_via_bat sinh ra trong delay window của nó). Tool trả về các PID đã bị kill; khi không khớp gì nó trả ok với danh sách rỗng, và bản thân orchestrator tiếp tục chạy trên PID hiện tại của nó.", "uc": "Người dùng đổi ý trong restart delay window, nên CA gọi cancel_pending_restart để kill helper restart đã xếp hàng trước khi nó kích hoạt.", "cit": ["tools_v15.py:8176", "tools_v15.py:8188", "tools_v15.py:8193", "tools_v15.py:8199", "tools_v15.py:8200", "tools_v15.py:8204", "tools_v15.py:8217"], "h": "tools_v15.py:8160", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "restart helper processes (via_bat_helper.py / restart_helper.cmd)"}], "err": {"c": "i", "r": "output-history.md:L124 (R33)"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "cdp_attach_target", "t": "T4", "cat": "desktop", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Gắn Playwright vào một CDP target đang có (bằng target_id phân giải từ http://localhost:9223/json/list, hoặc bằng websocket_url trực tiếp), với cơ chế tự động rơi về websocket cấp browser từ /json/version khi Electron từ chối Target.createBrowserContext. Nó bind page đầu tiên của target — hoặc tạo một browser context mới cộng một trang trắng khi target không có page nào — gắn các listener console và network-failure, đăng ký session trong dict _BROWSER_PAGES trên bộ nhớ, và trả về một attach_id dùng được làm page_id cho click/fill/evaluate_js.", "uc": "Một CA gắn vào một app Electron đang chạy sẵn expose port 9223 để có thể click và đọc DOM của nó mà không phải khởi động lại app.", "cit": ["tools_v15.py:6376", "tools_v15.py:6426", "tools_v15.py:6432", "tools_v15.py:6507", "tools_v15.py:6508", "tools_v15.py:6521", "tools_v15.py:6532"], "h": "tools_v15.py:6334", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "attached CDP target browser (only when it has no pages)"}], "err": {"c": "i", "r": "output-history.md:L124 (R33)", "q": "target-state qualifier (R34)"}, "use": {"b": "rare", "c": 1, "ls": "2026-11-11T22:22:53"}},
{"n": "check_window_visibility", "t": "T4", "cat": "desktop", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Check visibility trước hành động cho T4 UI automation: phân giải một cửa sổ Win32 bằng hwnd hoặc title_pattern (khớp visible đầu tiên qua EnumWindows) và trả về trạng thái cụ thể của nó — visible, minimized, off-screen, rectangle, pid — cộng một gợi ý có thể thi hành (ok = an toàn để click/fill, restore = gọi desktop_app_focus trước). Tool chỉ truy vấn trạng thái Win32; không bao giờ di chuyển, restore, hay focus bất cứ gì.", "uc": "Trước khi bấm một nút trong app desktop, một CA gọi check_window_visibility để xác nhận cửa sổ đang trên màn hình và không bị minimize, tránh click vào một cửa sổ ẩn.", "cit": ["tools_v15.py:7354", "tools_v15.py:7380", "tools_v15.py:7395", "tools_v15.py:7396", "tools_v15.py:7408", "tools_v15.py:7415", "tools_v15.py:7417"], "h": "tools_v15.py:7335", "wk": "none", "der": false, "wr": [], "use": {"b": "light", "c": 12, "ls": "2026-11-10T18:12:43"}},
{"n": "checkpoint_loop_dom_state", "t": "T4", "cat": "t4", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Vòng poll phía server trên một phần tử DOM: phân giải baseline từ một snapshot_id của dom_state_diff trước đó (TTL 10 phút), từ baseline_fields inline, hoặc từ một snapshot mới tại t=0, rồi evaluate một biểu thức JavaScript chỉ đọc (mặc định disabled/visible/bg/color/className/value_length) mỗi interval_sec (tối thiểu 0.5 s) cho đến khi bất kỳ trường nào thay đổi (stop_on_change, mặc định true) hoặc hết timeout_sec (mặc định 90 s). Trả về baseline, baseline_source, trạng thái cuối, các trường đã đổi, số lần poll, và số giây đã trôi qua.", "uc": "Sau khi bấm Save, một CA poll một phần tử trạng thái cho đến khi văn bản hoặc class của nó đổi (hoặc 90 s trôi qua) để xác nhận lệnh save đã ngấm, thay vì ngủ một khoảng chờ cố định.", "cit": ["tools_v15.py:4532", "tools_v15.py:4542", "tools_v15.py:4547", "tools_v15.py:4561", "tools_v15.py:4568", "tools_v15.py:4572", "tools_v15.py:4579"], "h": "tools_v15.py:4478", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "click", "t": "T4", "cat": "t4", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Thực hiện một click Playwright thật trên phần tử đầu tiên khớp selector trong trang (timeout 5 s) và trả về clicked:true; một click thất bại trả về CLICK_FAILED. Đây là primitive mà các tool click giàu hơn bọc lại.", "uc": "Một CA bấm nút Submit bằng CSS selector trong một trang do Playwright điều khiển và kiểm tra clicked:true trước khi đọc kết quả.", "cit": ["tools_v15.py:6708", "tools_v15.py:6712", "tools_v15.py:6713", "tools_v15.py:6715"], "h": "tools_v15.py:6707", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "browser page DOM state"}], "use": {"b": "rare", "c": 2, "ls": "2026-11-10T16:57:45"}},
{"n": "click_and_checkpoint_loop", "t": "T4", "cat": "t4", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Click nguyên tử cộng vòng checkpoint: snapshot watch_selector TRƯỚC khi click (timing baseline đúng), chờ pre_click_settle_ms (mặc định 200 ms), bấm click_selector_str, rồi poll phần tử được theo dõi cho đến khi trạng thái của nó đổi hoặc hết timeout_sec (mặc định 90 s). watch_selector mặc định là chính phần tử được bấm. Tool này tồn tại vì mẫu ba lời gọi (dom_state_diff, click, checkpoint_loop) có thể chụp baseline sau khi click đã ngấm.", "uc": "Một CA bấm Apply và chờ thẻ trạng thái đổi từ 'idle' sang 'done' (hoặc hết giờ sau 90 s) trong một lời gọi nguyên tử duy nhất.", "cit": ["tools_v15.py:4844", "tools_v15.py:4856", "tools_v15.py:4868", "tools_v15.py:4880", "tools_v15.py:4891", "tools_v15.py:4905"], "h": "tools_v15.py:4798", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "browser page DOM state"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "click_and_handle_folder_dialog", "t": "T4", "cat": "desktop", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Workflow click nguyên tử -> folder picker native -> dán đường dẫn -> xác minh: (1) xác minh tùy chọn rằng thư mục đích tồn tại; (2) snapshot các cửa sổ baseline và phân giải owner_pid từ owner_title; (3) bấm một DOM selector qua CDP; (4) poll tìm một dialog native MỚI (hoặc nhận một picker đang mở sẵn khi đúng một cái khớp); (5) điền nó qua fill_folder_dialog (gõ đường dẫn, bấm nút) và trả về kết quả theo từng pha kèm phán quyết fill. Trả về NEW_DIALOG_NOT_FOUND cùng các ứng viên nới lỏng và top-10 cửa sổ visible khi không có dialog nào xuất hiện. Handler chỉ là một alias wrapper mỏng: phần cài đặt nằm ở orchestrator/runtime/desktop_dialog_controls.py:343 (được import là _runtime_click_and_handle_folder_dialog tại code_tools.py:8619-8621).", "uc": "Trong một flow của app Electron, một CA gọi click_and_handle_folder_dialog kèm page id, selector của nút 'Browse...' và thư mục đích, để folder picker của app được mở, điền và xác nhận trong một lời gọi thay vì ba bước thủ công.", "cit": ["code_tools.py:8691", "code_tools.py:8700", "orchestrator/runtime/desktop_dialog_controls.py:438", "orchestrator/runtime/desktop_dialog_controls.py:471", "orchestrator/runtime/desktop_dialog_controls.py:631"], "h": "code_tools.py:8691", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "DOM selector via CDP (page_id + click_selector)"}, {"kind": "ui", "target": "native folder-picker dialog fill (path typed + button clicked via fill_folder_dialog)"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "click_selector", "t": "T4", "cat": "desktop", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Wrapper giàu hơn bọc quanh click(): snapshot trạng thái phần tử đích (mặc định disabled, visible, bg, color, className, value_length) trước và sau một click thật rồi trả về các trường đã đổi, nên một lời gọi thay thế cho vòng dom_state_diff, click, dom_state_diff. Với before_after_state=false nó rơi về hành vi click thường.", "uc": "Một CA bấm một toggle và thấy ngay những trường nào đổi (ví dụ disabled từ true sang false) mà không cần lời gọi đọc trạng thái thứ hai.", "cit": ["tools_v15.py:4367", "tools_v15.py:4371", "tools_v15.py:4380", "tools_v15.py:4383", "tools_v15.py:4387", "tools_v15.py:4400"], "h": "tools_v15.py:4329", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "browser page DOM state"}], "use": {"b": "light", "c": 8, "ls": "2026-11-10T18:11:35"}},
{"n": "compare_dom_snapshot", "t": "T4", "cat": "t4", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Đọc nội dung văn bản của một selector (mặc định body) từ hai trang và trả về identical cộng một unified diff (200 dòng đầu, context 2 dòng) tính trong bộ nhớ bằng difflib. Thuần túy so sánh; không trang nào bị click, điều hướng, hay sửa đổi.", "uc": "Một CA so văn bản body của một trang trước và sau khi reload (hai page_id) để xác nhận nội dung render ra là giống hệt nhau.", "cit": ["tools_v15.py:6860", "tools_v15.py:6861", "tools_v15.py:6863", "tools_v15.py:6865", "tools_v15.py:6866", "tools_v15.py:6868"], "h": "tools_v15.py:6851", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "desktop_app_focus", "t": "T4", "cat": "desktop", "mut": true, "prot": true, "sess": false, "fg": "desktop", "eff": "Focus cửa sổ ưu tiên CDP: gửi Page.bringToFront tới CDP target khớp; khi CDP thất bại nó rơi về Win32 (AttachThreadInput, ShowWindow SW_RESTORE khi bị minimize hoặc off-screen, SetForegroundWindow cộng BringWindowToTop) kèm xác minh rectangle sau hành động. Theo hợp đồng, focus thất bại không gây chết: response ghi do_not_retry_focus, vì UIPI chặn SetForegroundWindow với nhiều cửa sổ và các flow file-bridge/CLI không cần focus.", "uc": "Trước khi gửi văn bản vào một app desktop, một CA gọi desktop_app_focus để đưa cửa sổ của nó lên trước (restore nếu đang minimize) để cú dán rơi đúng cửa sổ.", "cit": ["tools_v15.py:8247", "tools_v15.py:8325", "tools_v15.py:8342", "tools_v15.py:8352", "tools_v15.py:8353", "tools_v15.py:8419", "tools_v15.py:8423"], "h": "tools_v15.py:8222", "wk": "none", "der": false, "wr": [{"kind": "ui", "target": "target desktop window"}], "note": "Matrix sess:true was stale; live requires_session=false.", "use": {"b": "light", "c": 7, "ls": "2026-11-14T14:16:46"}},
{"n": "desktop_click", "t": "T4", "cat": "desktop", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Click theo tọa độ ưu tiên CDP cho Electron/Chromium, fallback Win32 cho cửa sổ native: trên đường CDP, nó đưa trang lên trước, dispatch mouseMoved (hover), mousePressed và mouseReleased tại (x,y) của viewport, thu bằng chứng DOM tại điểm click, và bắn một el.click() tổng hợp (theo target_selector khi được cho, ngược lại dùng elementFromPoint). Trên đường Win32, nó phân giải cửa sổ con nằm dưới (x,y) và gửi WM_MOUSEACTIVATE cộng WM_LBUTTONDOWN/UP qua SendMessageTimeout. force_win32=true bỏ hẳn CDP dành cho dialog native; tọa độ CDP tính theo viewport còn tọa độ Win32 tính theo client. Lưu ý: handler ghi state hai lần trong một lời gọi (state double-fire).", "uc": "Một CA bấm nút Scan tại tọa độ viewport trong một app Electron qua CDP, rồi kiểm tra dom_evidence_at_xy để xác nhận click trúng nút chứ không phải một div tooltip đè lên.", "cit": ["tools_v15.py:8696", "tools_v15.py:8700", "tools_v15.py:8705", "tools_v15.py:8716", "tools_v15.py:8758", "tools_v15.py:8779", "tools_v15.py:8888", "tools_v15.py:9031"], "h": "tools_v15.py:8602", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "page/DOM at (x,y) — CDP mouse events (mouseMoved/mousePressed/mouseReleased) + synthetic el.click()", "condition": "CDP path (default)"}, {"kind": "ui", "target": "native child window under (x,y) — WM_MOUSEACTIVATE + WM_LBUTTONDOWN/UP", "condition": "Win32 fallback / force_win32=true"}], "use": {"b": "light", "c": 18, "ls": "2026-11-14T15:41:01"}},
{"n": "desktop_screenshot", "t": "T4", "cat": "desktop", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Chụp vùng cửa sổ (hwnd), một monitor đơn lẻ (index bắt từ 1), hoặc toàn bộ virtual desktop trải trên mọi monitor bằng PIL ImageGrab trên một worker thread với timeout 20 s, và LUÔN lưu một file PNG: vào save_path khi được cho, nếu không thì vào .orchestrator_cache/screenshots/desktop_<milliseconds>.png. Trả về đường dẫn, kích thước ảnh, số byte và danh sách monitors để lần sau caller chọn một index.", "uc": "Một CA chụp toàn bộ virtual desktop ra một file PNG để xác nhận bằng mắt những gì một bài test để lại trên màn hình, rồi đọc lại file đó để xem xét.", "cit": ["tools_v15.py:9356", "tools_v15.py:9359", "tools_v15.py:9311", "tools_v15.py:9315", "tools_v15.py:9317", "tools_v15.py:9320", "tools_v15.py:9322"], "h": "tools_v15.py:9337", "wk": "none", "der": false, "wr": [{"kind": "file", "target": "save_path, or <orchestrator>/.orchestrator_cache/screenshots/desktop_<ms>.png by default"}], "err": {"c": "i", "r": "output-history.md:L124 (R33)"}, "use": {"b": "light", "c": 55, "ls": "2026-11-14T09:24:47"}},
{"n": "desktop_send_text", "t": "T4", "cat": "desktop", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Gõ hoặc dán văn bản vào một cửa sổ desktop: focus đích (AttachThreadInput cộng SetForegroundWindow, restore nếu đang minimize; hủy trước khi gõ bất kỳ phím nào khi require_focus=true mà focus thất bại, để tránh gửi nhầm cửa sổ), tùy chọn bấm vào ô nhập, rồi thay clipboard bằng văn bản và gửi Ctrl+V, cộng Enter khi submit=true (mặc định). Chỉ tồn tại phương pháp clipboard; mọi phương pháp khác trả về BAD_METHOD. Được thiết kế cho các cú poke ngắn một dòng, không phải nội dung nặng.", "uc": "Một CA dán cú poke ngắn 'doc inbox_codo.md MSG moi nhat' vào ô nhập file-bridge của council và gửi nó bằng Enter.", "cit": ["tools_v15.py:8514", "tools_v15.py:8533", "tools_v15.py:8559", "tools_v15.py:8561", "tools_v15.py:8569", "tools_v15.py:8572", "tools_v15.py:8566"], "h": "tools_v15.py:8431", "wk": "none", "der": false, "wr": [{"kind": "ui", "target": "Windows clipboard (CF_UNICODETEXT)"}, {"kind": "ui", "target": "target window's focused input (Ctrl+V + optional Enter)"}, {"kind": "ui", "target": "target desktop window"}], "use": {"b": "light", "c": 12, "ls": "2026-11-11T08:55:41"}},
{"n": "desktop_window_list", "t": "T4", "cat": "desktop", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Liệt kê các cửa sổ desktop top-level qua Win32 EnumWindows và trả về metadata giàu thông tin cho mỗi cửa sổ (hwnd, title, pid, tên process, class, hwnd của parent/owner, visible, enabled, các style topmost/toolwindow, rect). Bộ lọc tùy chọn thu hẹp theo chuỗi con của title hoặc owner PID, còn include_child_controls đi qua các child control (handle edit/button/combo, trần 40 mỗi cửa sổ). Mọi lời gọi chỉ là getter Win32 đọc; không gì trên desktop bị di chuyển, focus hay thay đổi.", "uc": "Trước khi tự động hóa một dialog native, một CA gọi desktop_window_list(title_filter='Save As') để lấy hwnd và pid sở hữu của dialog, để một hành động Win32 sau đó nhắm đúng cửa sổ.", "cit": ["tools_v15.py:7210", "tools_v15.py:7223", "tools_v15.py:7262", "tools_v15.py:7301", "tools_v15.py:7323", "tools_v15.py:7325"], "h": "tools_v15.py:7210", "wk": "none", "der": false, "wr": [], "use": {"b": "light", "c": 39, "ls": "2026-11-14T15:40:56"}},
{"n": "desktop_window_state", "t": "T4", "cat": "desktop", "mut": true, "prot": true, "sess": false, "fg": "desktop", "eff": "Thay đổi trạng thái một cửa sổ desktop qua API Win32 ShowWindow (maximize/restore/minimize/normal/hide/show, ánh xạ tới các hằng số SW_* tại :9154-9161), phân giải đích bằng hwnd hoặc bằng khớp title-pattern. Nhánh maximize còn gọi thêm SetForegroundWindow để cửa sổ trở thành active. Rect trước/sau và các cờ minimized/visible được báo cáo, nhưng bản thân việc đổi trạng thái mới là effect ở cấp OS. Khi nhiều cửa sổ khớp, khớp đầu tiên thắng.", "uc": "Sau khi khởi động một app Electron ở trạng thái minimize, một CA gọi desktop_window_state(state='maximize', title_pattern='Rawcut') để đưa cửa sổ của nó lên trước trước khi điều khiển.", "cit": ["tools_v15.py:9122", "tools_v15.py:9154", "tools_v15.py:9228", "tools_v15.py:9232", "tools_v15.py:9265"], "h": "tools_v15.py:9122", "wk": "none", "der": false, "wr": [{"kind": "ui", "target": "target window hwnd (maximize/restore/minimize/normal/hide/show; maximize also SetForegroundWindow)"}], "note": "Matrix sess:true was stale; live requires_session=false.", "use": {"b": "light", "c": 9, "ls": "2026-11-14T14:16:44"}},
{"n": "detect_stale_server", "t": "T4", "cat": "t4", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Probe một HTTP server cục bộ bằng cách GET http://127.0.0.1:{port}/ (8 KB đầu) và báo cáo ETag, Last-Modified cùng một bản xem trước nội dung. Khi expected_bundle_version được cho, tool trả lời liệu bundle marker đó có vắng trên trang đang được serve hay không (stale=true/false), tức server đang chạy có đang serve một build cũ hay không. Một probe thất bại trả về PROBE_FAILED; không gì được restart hay reload.", "uc": "Sau khi build lại một dev server trên port 3099, một CA gọi detect_stale_server(port=3099, expected_bundle_version='v2.4.1') để kiểm tra server đang chạy có còn serve bundle cũ hay không.", "cit": ["tools_v15.py:7064", "tools_v15.py:7069", "tools_v15.py:7073", "tools_v15.py:7078"], "h": "tools_v15.py:7064", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "electron_devtools_log", "t": "T4", "cat": "desktop", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Bất chấp tên gọi, tool này trả về các *target* CDP chứ không phải thông điệp log: nó GET http://127.0.0.1:{cdp_port}/json và trả về type, url, title, id của từng target (trần ở max_messages). Tool bị DEPRECATED từ V15 R41 (tư cách thành viên _DEPRECATED_TOOLS đọc được bằng máy, code_tools.py:19990-19992, đã xác minh trực tiếp) vì tên gắn nhãn sai việc nó làm; thay thế cho việc liệt kê là list_cdp_targets, còn cho thông điệp console thật thì đường đi là cdp_attach_target rồi read_console_errors.", "uc": "Một CA di cư script cũ thay electron_devtools_log(cdp_port=9223) bằng list_cdp_targets(cdp_port=9223) để có cùng danh sách target, và dùng read_console_errors cho output console thực.", "cit": ["tools_v15.py:9368", "tools_v15.py:9378", "tools_v15.py:9392", "tools_v15.py:9394", "tools_v15.py:9401"], "h": "tools_v15.py:9368", "wk": "none", "der": false, "wr": [], "dep": true, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "ensure_single_electron_instance", "t": "T4", "cat": "t4", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Bước vệ sinh trước khi khởi động một instance Electron mới: nó liệt kê các PID electron.exe đang chạy qua tasklist và probe xem CDP port có bận không. Khi kill_existing=true (mặc định) và PIDs tồn tại hoặc port bận, nó chạy taskkill /F /IM electron.exe /T, chờ 0.8 s và probe lại port. Việc kill dựa theo image name, nên nó kết thúc MỌI process Electron trên máy, không chỉ app của project đích.", "uc": "Trước khi khởi động lại Rawcut sau một lần chạy cũ, một CA gọi ensure_single_electron_instance(project_path=...) để kill electron.exe còn sót và giải phóng port 9223 để instance mới gắn vào sạch sẽ.", "cit": ["tools_v15.py:4591", "tools_v15.py:4614", "tools_v15.py:4637", "tools_v15.py:4639", "tools_v15.py:4650"], "h": "tools_v15.py:4591", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "every electron.exe process (taskkill /F /IM electron.exe /T)", "condition": "kill_existing=true (default) AND (existing electron.exe PIDs OR CDP port busy)"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "evaluate_js", "t": "T4", "cat": "t4", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Evaluate một biểu thức JavaScript tùy ý bên trong một trang browser do Playwright quản lý và trả về kết quả của nó. Cơ chế là Playwright page.evaluate bọc trong asyncio.wait_for kèm timeout (mặc định 8000 ms), nên một vòng lặp vô hạn hoặc một Promise treo sẽ fail thành EVAL_TIMEOUT thay vì treo. Mọi side effect là những gì bản thân biểu thức làm trong trang; handler không tự thêm lượt ghi nào ngoài việc chạy biểu thức. Một timeout hủy await, không hủy JS phía trang.", "uc": "Để đọc một giá trị mà locator của Playwright không với tới, một CA gọi evaluate_js(page_id='pg1', expression='document.querySelector(\"#total\").textContent') và nhận lại văn bản sống.", "cit": ["tools_v15.py:6729", "tools_v15.py:6741", "tools_v15.py:6744", "tools_v15.py:6756", "tools_v15.py:6764"], "h": "tools_v15.py:6729", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "page JS runtime via Playwright page.evaluate"}], "use": {"b": "light", "c": 23, "ls": "2026-11-11T10:18:54"}},
{"n": "fill", "t": "T4", "cat": "t4", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Điền một trường form trong một trang do Playwright quản lý: nó phân giải page_id qua _get_page và gọi Playwright locator.fill trên phần tử đầu tiên khớp selector (timeout 5 s), đặt giá trị của input. Đây là bản thuần đối tác của fill_value — không xử lý encoding, không đọc lại để verify, không event nào ngoài những event mà chính fill của Playwright dispatch.", "uc": "Trong một UI test, một CA gọi fill(page_id='pg1', selector='#username', value='tester') để gõ vào trường đăng nhập trước khi bấm submit.", "cit": ["tools_v15.py:6718", "tools_v15.py:6719", "tools_v15.py:6723", "tools_v15.py:6724"], "h": "tools_v15.py:6718", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "first element matching selector in page, via Playwright locator.fill"}], "note": "Tier-split anomaly (R42): fill is T4 while fill_value is T2 — unexplained, carried as an anomaly.", "use": {"b": "rare", "c": 2, "ls": "2026-11-10T16:29:03"}},
{"n": "fill_folder_dialog", "t": "T4", "cat": "desktop", "mut": true, "prot": true, "sess": false, "fg": "desktop", "eff": "Điều khiển một dialog Select Folder native của Windows đang sống (KHÔNG phải một trang browser). Lời gọi chỉ là một passthrough mỏng; phần cài đặt (orchestrator/runtime/desktop_dialogs.py:7) chờ tối đa wait_for_dialog_ms cho một dialog visible khớp title_pattern / owner_pid / parent_hwnd / class_name_pattern / launched_after_ts, rồi trên đường mặc định đặt thư mục qua WM_SETTEXT + Enter trên control Edit (đường classic) hoặc qua đường bàn phím hiện đại (focus, dán clipboard với Ctrl+L / Ctrl+V / Enter), sau đó BM_CLICK nút Select/OK và xác minh dialog đã đóng. dry_run=true trả về danh sách ứng viên mà KHÔNG mutate.", "uc": "Sau khi bấm nút Browse trong một app desktop, một CA gọi fill_folder_dialog kèm owner_pid của app và thư mục đích để picker native được điền và xác nhận mà không phải tự tay điều khiển chuột.", "cit": ["code_tools.py:8630", "code_tools.py:8643", "orchestrator/runtime/desktop_dialogs.py:293", "orchestrator/runtime/desktop_dialogs.py:374", "orchestrator/runtime/desktop_dialogs.py:482"], "h": "code_tools.py:8630", "wk": "none", "der": false, "wr": [{"kind": "ui", "target": "native Windows folder-picker dialog: WM_SETTEXT path + Enter, or clipboard + Ctrl+L/Ctrl+V/Enter, then BM_CLICK on Select/OK (NOT a browser page)", "condition": "dry_run=false (default); dry_run=true writes nothing"}], "note": "Matrix sess:true was stale; live requires_session=false (R60k).", "use": {"b": "light", "c": 17, "ls": "2026-11-10T18:11:19"}},
{"n": "invoke_app_function", "t": "T4", "cat": "t4", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Gọi một hàm top-level có tên của trang bằng cách evaluate một wrapper trong browser qua Playwright page.evaluate: nó kiểm tra function_name là một JS identifier hợp lệ, serialize args ra JSON, await lời gọi, và trả về giá trị return của nó. Khi poll_until_global được đặt, nó poll một window global cho đến khi nó thành truthy (hoặc hết timeout), để CA có thể chờ một cờ hoàn thành async mà hàm tự đặt.", "uc": "Thay vì bấm chuột qua một file picker, một CA gọi invoke_app_function(page_id='pg1', function_name='runGetFootageToTarget', args=['D:/target'], poll_until_global='__codexRawcutCopyDone') để chạy handler riêng của app và chờ cờ done của nó.", "cit": ["tools_v15.py:3142", "tools_v15.py:3163", "tools_v15.py:3173", "tools_v15.py:3185", "tools_v15.py:3198"], "h": "tools_v15.py:3142", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "page JS runtime via Playwright page.evaluate"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "launch_electron_with_cdp", "t": "T4", "cat": "runtime", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Một lượt launch Electron duy nhất với CDP injection và auto-attach. Nó phân giải launcher (trực tiếp, hoặc qua inspect_project_runtime khi không có path được cho), đọc file .bat để tìm lệnh electron và thư mục làm việc — bản thân file .bat không bao giờ bị sửa — dựng một lệnh inline có nối thêm --remote-debugging-port, và chạy nó ở nền qua run_project_command. Sau đó nó xác minh CDP trên /json/version (quét các fallback port nếu app đã ghi đè port được yêu cầu) và tự gắn qua cdp_attach_target, trả về attach_id, page_id, port và title. Khi CDP_PORT_MISMATCH xảy ra, Electron đã launch tiếp tục chạy mà không có attach_id (orphan).", "uc": "Để khởi động Rawcut dưới sự kiểm soát của test, một CA gọi launch_electron_with_cdp(project_path='D:/.../Rawcut') và nhận về một page_id mà sau đó điều khiển được bằng click/fill_value/dom_state_diff.", "cit": ["tools_v15.py:3227", "tools_v15.py:3255", "tools_v15.py:3265", "tools_v15.py:3376", "tools_v15.py:3404", "tools_v15.py:3458"], "h": "tools_v15.py:3227", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "Electron app process via run_project_command background=True"}, {"kind": "browser-runtime", "target": "CDP target via cdp_attach_target delegate (creates context+page when target has no pages, Batch 4 R34)"}], "note": "Inherits the R34 target-state qualifier via the cdp_attach_target delegate (tools_v15.py:3458) — recorded here, not double-counted (R41).", "use": {"b": "light", "c": 18, "ls": "2026-11-11T19:41:25"}},
{"n": "list_cdp_targets", "t": "T4", "cat": "read", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Liệt kê các target của Chromium DevTools Protocol bằng cách GET http://127.0.0.1:{cdp_port}/json/list qua helper _fetch_json_sync (một urllib GET thuần) và trả về danh sách gọn gồm title, url, type, id, webSocketDebuggerUrl của từng target. Đây là thay thế đang sống cho electron_devtools_log đã deprecated, cùng tham số port và cùng ngữ nghĩa liệt kê. Không trang nào được mở, gắn hay sửa.", "uc": "Để xem một app Electron trên port 9223 expose những gì, một CA gọi list_cdp_targets(cdp_port=9223) và chọn một target id để gắn bằng cdp_attach_target.", "cit": ["tools_v15.py:7529", "tools_v15.py:7532", "tools_v15.py:7453", "tools_v15.py:7462", "tools_v15.py:7536"], "h": "tools_v15.py:7529", "wk": "none", "der": false, "wr": [], "use": {"b": "light", "c": 12, "ls": "2026-11-11T22:22:41"}},
{"n": "open_browser_page", "t": "T4", "cat": "t4", "mut": true, "prot": true, "sess": true, "fg": "desktop", "eff": "Mở một trang trong một Chromium headless mới được launch qua Playwright (async_playwright start, chromium.launch, new_context với viewport được yêu cầu, new_page, goto với điều kiện chờ được yêu cầu) và đăng ký nó trong map _BROWSER_PAGES nội bộ process, dưới một page_id được mint. Khi có attach_id, nó thay vào đó tái dùng một trang đã CDP-attach sẵn và điều hướng trang đó tới URL. Đây là đối tác của close_browser_page (Batch 4, lớp (i)): open tạo ra browser state mà close phá hủy.", "uc": "Để test một web app trong browser sạch, một CA gọi open_browser_page(url='http://127.0.0.1:3099/') và dùng page_id trả về cho click/fill_value/dom_state_diff, rồi đóng nó sau bằng close_browser_page.", "cit": ["tools_v15.py:6570", "tools_v15.py:6582", "tools_v15.py:6607", "tools_v15.py:6627", "tools_v15.py:6629"], "h": "tools_v15.py:6570", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "Chromium via Playwright (launched branch)"}, {"kind": "browser-runtime", "target": "page URL (goto; attach branch navigates the existing page)"}], "use": {"b": "light", "c": 40, "ls": "2026-11-14T16:18:38"}},
{"n": "port_owner_inspect", "t": "T4", "cat": "runtime", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Trả lời ai sở hữu một TCP port bằng cách ủy quyền cho server_lifecycle_status: một lượt quét psutil các listener TCP4/TCP6 (ngân sách 8 s) tìm kết nối LISTEN trên port và mô tả process của nó (pid, name, cmdline, username, suy luận can_kill), và kết quả được cache trong bộ nhớ theo từng port, nên các probe lặp lại trong TTL nhận lại câu trả lời đã cache. Bản thân wrapper chỉ nắn lại kết quả của delegate thành {port, listening, pid, process_name}.", "uc": "Khi port 9223 bận và một lượt launch thất bại, một CA gọi port_owner_inspect(port=9223) để biết process nào đang giữ nó trước khi quyết định có kill hay không.", "cit": ["tools_v15.py:7048", "tools_v15.py:7055", "tools_v15.py:6964", "tools_v15.py:7019", "tools_v15.py:7041"], "h": "tools_v15.py:7048", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "process_tree_inspect", "t": "T4", "cat": "runtime", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Xem xét một cây process bằng psutil: từ một root_pid nó đi qua các con đệ quy (max_depth 3) và báo pid, name, ba token cmdline đầu, cùng bộ nhớ RSS cho mỗi node; hoặc, khi có name_pattern, nó quét mọi process để tìm khớp chuỗi con của name/cmdline (10 khớp đầu) và đi qua từng cây. Không process nào bị gửi signal, suspend hay kill — lượt đi cây là chỉ đọc.", "uc": "Khi một app Electron sinh ra các helper, một CA gọi process_tree_inspect(name_pattern='electron') để xem cây parent/child và mức dùng bộ nhớ trước khi quyết định kill cái gì.", "cit": ["tools_v15.py:9409", "tools_v15.py:9416", "tools_v15.py:9448", "tools_v15.py:9454", "tools_v15.py:9460"], "h": "tools_v15.py:9409", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "query_selector", "t": "T4", "cat": "desktop,search", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Probe chỉ đọc của một CSS selector trên trang đang sống: sau lượt tra chung _get_page, nó đếm số khớp, trả về exists=false khi số đếm là 0, và nếu không thì trả về visible cùng bounding box của khớp đầu tiên. Nó không bấm gì và không thay đổi gì trong trang; page_id hoặc selector sai trả về một lỗi.", "uc": "Trước khi bấm một nút dialog, một CA gọi query_selector(page_id, '#confirmBtn') để xác nhận nút tồn tại, đang visible, và lấy hộp trên màn hình của nó.", "cit": ["tools_v15.py:6690", "tools_v15.py:6696", "tools_v15.py:6699", "tools_v15.py:6701"], "h": "tools_v15.py:6689", "wk": "none", "der": false, "wr": [], "use": {"b": "light", "c": 14, "ls": "2026-11-10T18:11:32"}},
{"n": "read_console_errors", "t": "T4", "cat": "read", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Đọc buffer console trong bộ nhớ của trang (state['console']) và lọc theo level — error, warning, info, hoặc all, mặc định là error để các warning lành tính không làm test thất bại. Nó trả về 50 thông điệp khớp cuối cùng cộng một severity_summary (error/warning/info/total) được tính trên TẤT CẢ các level bất kể bộ lọc. Một level không được hỗ trợ trả về UNSUPPORTED_FIELD.", "uc": "Sau khi click hết một UI flow, một CA gọi read_console_errors(page_id) để kiểm tra lỗi console trong khi bỏ qua các CSP warning lành tính.", "cit": ["tools_v15.py:6780", "tools_v15.py:6785", "tools_v15.py:6788", "tools_v15.py:6804"], "h": "tools_v15.py:6770", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "read_network_errors", "t": "T4", "cat": "read", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Trả về danh sách lỗi network trong bộ nhớ của trang (state['network_errors']): 50 mục cuối cộng tổng số mục. Tool không tự thực hiện request network nào — nó chỉ báo cáo những failure mà page state đã ghi nhận sẵn.", "uc": "Khi một panel của trang render trống, một CA gọi read_network_errors(page_id) để xem liệu các request thất bại có giải thích được điều đó hay không trước khi đào sâu vào DOM.", "cit": ["tools_v15.py:6811", "tools_v15.py:6815", "tools_v15.py:6816"], "h": "tools_v15.py:6811", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "read_page", "t": "T4", "cat": "read", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Đọc text_content của phần tử đầu tiên khớp một selector (mặc định 'body') trên trang đang sống, cắt còn max_chars (mặc định 5000), và trả về văn bản kèm độ dài của nó cùng một cờ truncated. Tra cứu hoặc đọc thất bại trả về DOM_READ_FAILED.", "uc": "Một CA gọi read_page(page_id, '#resultPanel') để lấy văn bản đã render của một phần kết quả và xác minh thông điệp mong đợi xuất hiện.", "cit": ["tools_v15.py:6676", "tools_v15.py:6681", "tools_v15.py:6682", "tools_v15.py:6683"], "h": "tools_v15.py:6675", "wk": "none", "der": false, "wr": [], "use": {"b": "light", "c": 20, "ls": "2026-11-14T16:18:47"}},
{"n": "record_ui_flow", "t": "T4", "cat": "t4", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Một STUB dạng preview, không phải recorder: sau khi hợp lệ hóa page_id, nó chỉ trả về một xác nhận ý định (feature_status 'preview_stub', tracing_supported true) giải thích rằng một trace.zip đầy đủ đòi hỏi cờ --record-trace lúc tạo context (một phần mở rộng R+1 đã lên kế hoạch cho open_browser_page). Nó không start tracing, không ghi file, không đăng ký gì ở đâu cả — tham số output_path được chấp nhận nhưng không bao giờ được tham chiếu trong thân. Giải pháp thay thế nó gợi ý là screenshot_page + read_console_errors để chụp từng bước.", "uc": "Một CA kiểm tra xem UI-flow tracing có sẵn không sẽ nhận câu trả lời preview_stub và rơi về screenshot_page + read_console_errors để chụp từng bước, thay vì chờ một file trace.", "cit": ["tools_v15.py:9530", "tools_v15.py:9533", "tools_v15.py:9536", "tools_v15.py:9540"], "h": "tools_v15.py:9523", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "restart_orchestrator_via_task", "t": "T4", "cat": "runtime", "mut": true, "prot": true, "sess": false, "fg": "ops", "eff": "Restart [A] qua Windows Task Scheduler ở High IL mà không cần UAC lúc gọi: trước tiên nó xác minh task 'ToolA_Orchestrator' tồn tại bằng schtasks /query (task vắng mặt trả về INSTALL_TASK_REQUIRED — một lần cài admin duy nhất của install_orchestrator_admin_task.ps1, RunLevel=Highest); sau đó nó ghi một helper .cmd detached vào .orchestrator_cache/restart_helper.cmd để chờ, kết thúc task, rơi về Stop-Process trên các owner của port-8083, và chạy lại task; rồi nó spawn helper ở chế độ detached (BREAKAWAY + NEW_PROCESS_GROUP) để helper sống sót qua cái chết của daemon. Nó trả về ngay với status restart_queued và poll_health_after_seconds = delay+6 — caller phải poll /health để tìm một PID mới và generation đã đổi. delay_seconds bị kẹp trong [1,10], mặc định 2. Không có drain guard, không snapshot, không generation check trước restart.", "uc": "Sau khi được ủy quyền restart tường minh, khi orchestrator kẹt trong trạng thái admin/UIPI-blocked mà đường restart thường không điều khiển được, một CA xếp hàng lượt restart qua Task Scheduler và poll /health cho đến khi một PID và generation mới xuất hiện.", "cit": ["tools_v15.py:7670", "tools_v15.py:7688", "tools_v15.py:7711", "tools_v15.py:7722", "tools_v15.py:7736"], "h": "tools_v15.py:7641", "wk": "none", "der": false, "wr": [{"kind": "file", "target": ".orchestrator_cache/restart_helper.cmd (+ restart_helper.log appended by the helper)"}, {"kind": "process", "target": "ToolA_Orchestrator scheduled task (schtasks /end + /run); orchestrator process killed and relaunched at High IL"}], "note": "Matrix sess:true was stale; live requires_session=false.", "use": {"b": "rare", "c": 2, "ls": "2026-11-11T00:11:05"}},
{"n": "restart_server_via_bat_hint", "t": "T4", "cat": "runtime", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Trả về một hint restart có cấu trúc và không thực thi gì: khi một port được cho, trước tiên nó xem xét owner hiện tại của port qua port_owner_inspect; khi bat_path là start_orchestrator.bat của chính [A] thì restart_command định tuyến tới restart_orchestrator_via_bat(delay_seconds=2) để một CA không bao giờ bị đẩy đi launch daemon bằng native, nếu không thì nó nói Run '<bat_path>'; post_check bảo caller xác minh qua /health (một pid mới VÀ một generation đã đổi).", "uc": "Trước khi restart một server, một CA hỏi hint cho launcher .bat của nó để biết chính xác lệnh và post-check /health mà chưa thực thi gì.", "cit": ["tools_v15.py:7173", "tools_v15.py:7176", "tools_v15.py:7191", "tools_v15.py:7194"], "h": "tools_v15.py:7172", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "run_playwright_test", "t": "T4", "cat": "runtime", "mut": false, "prot": false, "sess": false, "fg": "test", "eff": "Thực thi một script Node Playwright/CDP như một child process, kèm preflight về ngôn ngữ và dependency: file phải tồn tại và hậu tố phải là .js/.mjs/.cjs (nếu không thì RUNNER_LANGUAGE_MISMATCH, chỉ tới run_project_test_script cho các Python fixture), và playwright hoặc playwright-core phải có mặt trong node_modules dưới thư mục script hoặc project_path (nếu không thì MODULE_NOT_FOUND). Sau đó nó chạy ['node', script] với timeout (mặc định 300s, cwd là project_path hoặc thư mục cha của script) và trả về exit code cộng 5000 ký tự cuối của stdout và stderr; một timeout trả về lỗi TIMEOUT.", "uc": "Một CA chạy một script Node Playwright fixture với một app test và đọc lại exit code của nó cùng phần đuôi của stdout/stderr để phán pass/fail.", "cit": ["tools_v15.py:9474", "tools_v15.py:9494", "tools_v15.py:9506", "tools_v15.py:9513"], "h": "tools_v15.py:9468", "wk": "none", "der": false, "wr": [{"kind": "process", "target": "child process `node <script>` (arbitrary script execution; the script itself may write files / launch browsers)", "condition": "preflight passes (script file exists, Node suffix, playwright installed)"}], "err": {"c": "i", "r": "output-history.md:L136 (R45)", "q": "process-spawn qualifier: the tool spawns node; the script effects are the script's"}, "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "run_script", "t": "T4", "cat": "runtime", "mut": true, "prot": true, "sess": true, "fg": "runtime", "eff": "Bộ chạy JS không cần escape: nó đọc biểu thức JS từ một file .js (ưu tiên) hoặc từ script_inline — script_path được ưu tiên hơn và bắt buộc phải có một trong hai nguồn — rồi evaluate nó trong trang browser với timeout mặc định 10s. Các thất bại ánh xạ tới các error name cụ thể: ILLEGAL_TOP_LEVEL_RETURN kèm hint wrap-in-IIFE (return ở top-level là bất hợp pháp trong eval), EVAL_UNDEFINED_REFERENCE, và EVAL_SYNTAX_ERROR. Một timeout hủy await chứ không hủy JS phía trang, nên một biểu thức đã hết giờ vẫn có thể tiếp tục chạy trong trang (cùng dạng residue như evaluate_js, R43e).", "uc": "Một CA đặt một textarea thành một UNC path đầy backslash bằng cách evaluate một file .js, thay vì vật lộn với escaping dấu nháy JSON trong một lời gọi evaluate_js inline.", "cit": ["tools_v15.py:6009", "tools_v15.py:6021", "tools_v15.py:6047", "tools_v15.py:6050"], "h": "tools_v15.py:5979", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "page DOM/JS state via the evaluated script", "condition": "when the evaluated script mutates page state (a pure read expression writes nothing)"}], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "screenshot_page", "t": "T4", "cat": "t4", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Chụp một screenshot của trang đang sống: khi save_path rỗng, nó tự sinh .orchestrator_cache/screenshots/{page_id}_{ts}.png và gọi page.screenshot(path=save_path, full_page=full_page), trả về đường dẫn, kích thước byte và cờ full_page. Một file PNG được ghi ở MỌI lời gọi trên đường mặc định; một save_path chỉ định rõ thì ghi vào đó thay thế.", "uc": "Trong một UI flow, một CA chụp trang hiện tại thành PNG làm bằng chứng trực quan từng bước tại mỗi checkpoint.", "cit": ["tools_v15.py:6825", "tools_v15.py:6827", "tools_v15.py:6829", "tools_v15.py:6830", "tools_v15.py:6832"], "h": "tools_v15.py:6819", "wk": "none", "der": false, "wr": [{"kind": "file", "target": ".orchestrator_cache/screenshots/{page_id}_{ts}.png (default) or caller save_path"}], "err": {"c": "i", "r": "output-history.md:L296 (Wave 12 synthesis)"}, "use": {"b": "rare", "c": 4, "ls": "2026-11-11T10:18:43"}},
{"n": "server_lifecycle_status", "t": "T4", "cat": "runtime", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Xem xét owner của port dựa trên psutil: port là bắt buộc (port thiếu trả về MISSING_REQUIRED_FIELD kèm hint gọi inspect_project_runtime trước để khám phá nó), và psutil phải được cài. Trước tiên nó tham khảo một scan cache trong bộ nhớ (TTL mặc định 3.0s qua ORCHESTRATOR_PSUTIL_PORT_SCAN_TTL_SEC; cache hit trả về cache_hit=true); nếu không thì nó quét tcp4 rồi tcp6 — FixA2407, vì [A] chỉ bind ::1 và một lượt quét chỉ tcp4 đã báo sai rằng 8083 không listening — và mô tả owner đang listening (pid, process name, 5 tham số cmdline đầu, username, can_kill suy ra từ khớp username mà không có probe suspend/resume). Lượt quét bị giới hạn 8s trong một thread, kết quả mới được cache, và khi đang listening nó khuyến nghị no_action_needed hoặc ask_user_end_via_task_manager.", "uc": "Trước khi attach CDP, một CA kiểm tra process nào sở hữu port 9223 và liệu nó có thể bị kill mà không cần admin hay không.", "cit": ["tools_v15.py:6950", "tools_v15.py:6964", "tools_v15.py:7019", "tools_v15.py:7041", "tools_v15.py:7043"], "h": "tools_v15.py:6944", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "static_asset_cache_check", "t": "T4", "cat": "t4", "mut": false, "prot": false, "sess": false, "fg": "ops", "eff": "Phát hiện các static asset bị cache-stale bằng một HEAD cộng hai GET (5s mỗi cái, 20s tổng, chạy trong một thread để URL ngoài không chặn loop): nó trả về Cache-Control, ETag, Last-Modified, size, cùng các tiền tố sha256 của cả hai body GET và một cờ ổn định (hash_fetch1 == hash_fetch2). Một loopback guard từ chối các URL trên chính port của [A] (8083) từ localhost với LOOPBACK_NOT_SUPPORTED, vì một urlopen đồng bộ sẽ deadlock event loop đơn luồng; hint chuyển hướng tới detect_stale_server(port=8083).", "uc": "Một CA kiểm tra một asset của app đang được serve có bị stale không bằng cách fetch nó hai lần và so hash của body, thay vì tin vào một response đã cache duy nhất.", "cit": ["tools_v15.py:7102", "tools_v15.py:7115", "tools_v15.py:7119", "tools_v15.py:7121", "tools_v15.py:7132"], "h": "tools_v15.py:7084", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "wait_for_selector", "t": "T4", "cat": "desktop,runtime", "mut": false, "prot": false, "sess": false, "fg": "desktop", "eff": "Chờ một locator đạt tới một trạng thái (mặc định visible, 5000ms) và trả về matched=true khi thành công; khi hết giờ nó trả về lỗi WAIT_TIMEOUT kèm matched=false. Việc chờ không thay đổi gì trong trang — đó là một lượt đọc đồng bộ thuần túy.", "uc": "Sau khi submit một form, một CA chờ dòng kết quả trở nên visible trước khi đọc nó, thay vì ngủ một khoảng chờ cố định rồi cầu mong.", "cit": ["tools_v15.py:6840", "tools_v15.py:6844", "tools_v15.py:6846", "tools_v15.py:6848"], "h": "tools_v15.py:6838", "wk": "none", "der": false, "wr": [], "use": {"b": "unused", "c": 0, "ls": null}},
{"n": "close_browser_page", "t": "T4", "cat": "read", "mut": false, "prot": false, "sess": false, "fg": "runtime", "eff": "Đóng trang tường minh với rẽ nhánh theo kind: một trang được launch thì bị đóng (context close, browser close, Playwright stop); một trang attached (CDP) chỉ bị DETACH MÀ THÔI (Playwright stop — app của chính user không bao giờ bị đóng); mục _BROWSER_PAGES bị pop trong cả hai trường hợp. Nó cố ý không gọi helper _close_browser_page vô-điều-kiện đã chết nằm phía trên, vốn sẽ đóng app của chính user khi gặp một entry attached.", "uc": "Sau khi hoàn tất một lượt kiểm tra do Playwright điều khiển, một CA gọi close_browser_page để đóng trang test đã launch; với một app Electron attached, cùng lời gọi đó chỉ detach mà không đóng app.", "cit": ["tools_v15.py:6886", "tools_v15.py:6899", "tools_v15.py:6901", "tools_v15.py:6902", "tools_v15.py:6906", "tools_v15.py:6911", "tools_v15.py:6915", "tools_v15.py:6924"], "h": "tools_v15.py:6885", "wk": "none", "der": false, "wr": [{"kind": "browser-runtime", "target": "browser page (launched: closed; attached: detached only)"}], "err": {"c": "i", "r": "output-history.md:L124 (R33)"}, "miss": true, "use": {"b": "light", "c": 33, "ls": "2026-11-14T16:18:48"}},
];

const SKILLS = [
"00_skill_router","01_bootstrap_health","02_mode_control","03_path_session",
"04_tool_tiers","05_write_pipeline","06_token_auth","07_error_schema_hints",
"08_command_windows","09_testing_validation","10_coverage_report","11_repo_vs_runtime",
"12_native_audit","13_apply_after_dry_run","14_token_jwt_redesign","15_search_code_intel",
"16_server_lifecycle","17_session_audit","18_runtime_dom_T4","19_image_mode",
"21_tool_matrix","22_electron_automation","26_workflow_ingestion","27_context_pool_write","28_ocr_review",
"broker-worker-bootstrap","council-brainstorm-loop","council-console-loop","council-cron-jobs",
"git-governance","leader-council-skill","rule_skill_changer","update-rule-skill-all-ca",
"verify-new-function","project-orchestrator-fix-loop"
];

const CHAINS = [
  {name:"Code Lifecycle",icon:"🔧",desc:"Standard read→edit→validate→commit cycle",steps:[
    "search_code","read_file_range","write_fix","validate_file","ruff_fix","git_stage","git_commit"],
    tools:["search_code","read_file_range","write_fix","validate_file","ruff_fix","git_stage","git_commit"]},
  {name:"Graph Analysis",icon:"🕸️",desc:"Build and query the code knowledge graph",steps:[
    "cbm_index","cbm_graph_status","cbm_nodes_query","cbm_edges_query","cbm_trace_path","cbm_get_architecture"],
    tools:["cbm_index","cbm_graph_status","cbm_nodes_query","cbm_edges_query","cbm_trace_path","cbm_get_architecture"]},
  {name:"Quality Pipeline",icon:"✅",desc:"Dead code + test gap + quality gate → fix",steps:[
    "cbm_index","cbm_dead_code","cbm_test_gap","cbm_quality_gate","cbm_what_if","cbm_incremental_index"],
    tools:["cbm_index","cbm_dead_code","cbm_test_gap","cbm_quality_gate","cbm_what_if","cbm_incremental_index"]},
  {name:"Session Lifecycle",icon:"🔑",desc:"Full CA session from open to close",steps:[
    "open_session","get_rule_core_text","read_file_range","write_fix","validate_file","log_native_event","close_session"],
    tools:["open_session","get_rule_core_text","read_file_range","write_fix","validate_file","log_native_event","close_session"]},
  {name:"Desktop/T4 E2E",icon:"🖥️",desc:"Electron app automation via CDP",steps:[
    "launch_electron_with_cdp","check_window_visibility","desktop_screenshot","describe_page",
    "dom_state_diff","evaluate_js","read_console_errors"],
    tools:["launch_electron_with_cdp","check_window_visibility","desktop_screenshot","describe_page",
    "dom_state_diff","evaluate_js","read_console_errors"]},
  {name:"Council Dispatch",icon:"🏛️",desc:"Leader convenes council, dispatches workers",steps:[
    "open_session","convene_council","council_worker_model","council_message","council_tracker","council_status"],
    tools:["open_session","convene_council","council_worker_model","council_message","council_tracker","council_status"]},
  {name:"Deploy Pipeline",icon:"🚀",desc:"Write → backup → deploy → validate",steps:[
    "write_fix","validate_file","backup_project","copy_file","run_project_command","validate"],
    tools:["write_fix","validate_file","backup_project","copy_file","run_project_command","validate"]},
  {name:"Mission Orchestration",icon:"📋",desc:"Multi-task mission lifecycle",steps:[
    "mission_create","mission_dispatch","mission_next","mission_gate","mission_report","mission_status"],
    tools:["mission_create","mission_dispatch","mission_next","mission_gate","mission_report","mission_status"]},
];

// CHAIN WIRES (APEX radial) — restored 2026-11-17 from Wave 19 sunburst_data.js.
// SB_LINKS: 30 cross-links (delegates-to 19, alias-dispatch 5, alias-shared-handler 1, recommends 5).
// SB_AUTO: 16 auto-chain proposals (unmeasured). The radial draws these as curved wires between tool nodes.
// The `r` on each row is a SNAPSHOT-LOCAL GROUP KEY, not a row of AUTO_CHAIN_RULES
// (code_tools.py:3297, 15 rows): measured 2026-11-24 it maps one rank to one trigger tool
// and back across all 16 rows -- ten ranks, ten distinct `f`, and ranks 9/10 simply do not
// occur. Registry row 2 is ca_hint_commit while rank 2 is cbm_dead_code -> cbm_index, and
// no SB_AUTO endpoint name is an advisory field at all. tests/test_web_ui_campaign.py
// test_sb_auto_rank_is_a_group_key_not_a_registry_row pins all of that, because the two
// comments that used to call the digit a "rule rank" are what invited a reader to go
// looking up a registry row that does not exist.
const SB_LINKS = [{"f":"regex_search","t":"search_code","k":"alias-dispatch","c":null,"q":"SHARED HANDLER: no own def exists - all 6 search aliases dispatch to search_code (search.py:1575)"},{"f":"find_callers","t":"search_code","k":"alias-dispatch","c":null,"q":"SHARED HANDLER: no own def exists - all 6 search aliases dispatch to search_code (search.py:1575)"},{"f":"find_definition_and_usage","t":"search_code","k":"alias-dispatch","c":null,"q":"SHARED HANDLER: no own def exists - all 6 search aliases dispatch to search_code (search.py:1575)"},{"f":"find_symbol","t":"search_code","k":"alias-dispatch","c":null,"q":"SHARED HANDLER: no own def exists - all 6 search aliases dispatch to search_code (search.py:1575)"},{"f":"query_dom_candidates","t":"search_code","k":"alias-dispatch","c":null,"q":"SHARED HANDLER: no own def exists - all 6 search aliases dispatch to search_code (search.py:1575)"},{"f":"workflow_save_md","t":"workflow_create_from_ui","k":"alias-shared-handler","c":null,"q":"ALIAS-GROUP: yes - SHARED HANDLER with workflow_create_from_ui (same def code_tools.py:14009)"},{"f":"create_folders","t":"create_folder","k":"delegates-to","c":null,"q":"Batch create_folder: calls create_folder once per path in paths[]"},{"f":"read_context_batch","t":"read_file_range","k":"delegates-to","c":null,"q":"calls read_file_range once per selected item"},{"f":"replace_between_anchors","t":"write_fix","k":"delegates-to","c":null,"q":"then calls write_fix, which atomically rewrites the file plus a backup"},{"f":"write_fix_html","t":"write_fix","k":"delegates-to","c":null,"q":"then apply the real write via write_fix dry_run=False (1721-1724)"},{"f":"run_node_script","t":"run_project_test_script","k":"delegates-to","c":null,"q":"implementation run_project_test_script code_tools.py:9863 (runtime=node)"},{"f":"run_ps1_script","t":"run_project_test_script","k":"delegates-to","c":null,"q":"implementation run_project_test_script code_tools.py:9863 (runtime=powershell)"},{"f":"run_test_profile","t":"run_project_command","k":"delegates-to","c":"default profile (non-workflow)","q":"Runs a project test profile out-of-process by delegating to run_project_command"},{"f":"run_test_profile","t":"workflow_start","k":"delegates-to","c":"workflow-based profiles only","q":"Workflow-based profiles ... delegate to workflow_start"},{"f":"project_map","t":"generate_project_map","k":"delegates-to","c":"mode=generate","q":"With mode=generate it calls generate_project_map"},{"f":"project_map","t":"refresh_project_map","k":"delegates-to","c":"mode=refresh","q":"with mode=refresh it calls refresh_project_map"},{"f":"project_map","t":"cache_status","k":"delegates-to","c":"mode=status (default)","q":"Default mode=status calls cache_status"},{"f":"refresh_project_map","t":"generate_project_map","k":"delegates-to","c":null,"q":"that builder IS generate_project_map ... so refresh is a full rebuild"},{"f":"restart_server_via_bat_hint","t":"port_owner_inspect","k":"delegates-to","c":"when a port is given","q":"when a port is given it first inspects the current port owner via port_owner_inspect"},{"f":"restart_server_via_bat_hint","t":"restart_orchestrator_via_bat","k":"delegates-to","c":"when bat_path is the own start_orchestrator.bat","q":"the restart_command routes to restart_orchestrator_via_bat(delay_seconds=2)"},{"f":"suggest_missing_capabilities","t":"capability_status","k":"delegates-to","c":null,"q":"calls capability_status ... and filters it to items that are unavailable"},{"f":"validate","t":"validate_file","k":"delegates-to","c":"mode='file'","q":"'file' runs validate_file (syntax/lint)"},{"f":"validate","t":"validate_symbols","k":"delegates-to","c":"mode='symbols'","q":"'symbols' runs validate_symbols (AST symbol comparison vs INDEX)"},{"f":"validate","t":"validate_encoding","k":"delegates-to","c":"mode='encoding'","q":"'encoding' runs validate_encoding (UTF-8/mojibake check)"},{"f":"tool_decision_hint","t":"file_sha256","k":"recommends","c":"LOOK intents","q":"LOOK intents route to file_sha256/write_status"},{"f":"tool_decision_hint","t":"write_status","k":"recommends","c":"LOOK intents","q":"LOOK intents route to file_sha256/write_status"},{"f":"tool_decision_hint","t":"server_lifecycle_status","k":"recommends","c":"restart intents (advisory recipe)","q":"returns an advisory-only recipe that executes nothing"},{"f":"tool_decision_hint","t":"restart_orchestrator_via_bat","k":"recommends","c":"restart intents (advisory recipe)","q":"returns an advisory-only recipe that executes nothing"},{"f":"tool_decision_hint","t":"search_code","k":"recommends","c":"no rule match: lexical-rank fallback at 0.4","q":"defaults to search_code at 0.4"},{"f":"search_code","t":"query_index","k":"delegates-to","c":"mode=index lane","q":"B-1 (Wave 19): search.py:1672-1673 calls query_index handler directly (code_tools.py:7704)"}];
const SB_AUTO = [{"f":"write_fix","t":"validate_file","r":1,"c":"→ auto-validate_file → auto-ruff_fix"},{"f":"write_fix","t":"ruff_fix","r":1,"c":"→ auto-validate_file → auto-ruff_fix"},{"f":"cbm_dead_code","t":"cbm_index","r":2,"c":"→ auto-cbm_index (nếu stale)"},{"f":"cbm_test_gap","t":"cbm_index","r":3,"c":"→ auto-cbm_index → auto-cbm_quality_gate"},{"f":"cbm_test_gap","t":"cbm_quality_gate","r":3,"c":"→ auto-cbm_index → auto-cbm_quality_gate"},{"f":"open_session","t":"get_rule_core_text","r":4,"c":"→ auto-get_rule_core_text → auto-cache_status"},{"f":"open_session","t":"cache_status","r":4,"c":"→ auto-get_rule_core_text → auto-cache_status"},{"f":"search_code","t":"read_file_range","r":5,"c":"→ auto-read_file_range (nếu tìm thấy)"},{"f":"git_commit","t":"git_status","r":6,"c":"→ auto-git_status (trước) → auto-validate_file (post)"},{"f":"git_commit","t":"validate_file","r":6,"c":"→ auto-git_status (trước) → auto-validate_file (post)"},{"f":"cbm_graph_status","t":"cbm_index","r":7,"c":"→ auto-cbm_index (nếu not_indexed)"},{"f":"launch_electron_with_cdp","t":"check_window_visibility","r":8,"c":"→ auto-check_window_visibility → auto-dom_state_diff(BASELINE)"},{"f":"launch_electron_with_cdp","t":"dom_state_diff","r":8,"c":"→ auto-check_window_visibility → auto-dom_state_diff(BASELINE)"},{"f":"cbm_what_if","t":"cbm_index","r":11,"c":"→ auto-cbm_index (nếu stale) → auto-cbm_quality_gate (baseline)"},{"f":"cbm_what_if","t":"cbm_quality_gate","r":11,"c":"→ auto-cbm_index (nếu stale) → auto-cbm_quality_gate (baseline)"},{"f":"ruff_fix","t":"validate_file","r":12,"c":"→ auto-validate_file (post-fix)"}];
const USAGE_WINDOW = "2026-11-08T03:40:01 → 2026-11-16T15:48:55 ICT (8.5d)";

// ═══════════════════════════════════════════════════════════════
// RENDERING
// ═══════════════════════════════════════════════════════════════

// All six views use the same click path, including shortcuts and lazy Radial setup.
const matrixTabs = Array.from(document.querySelectorAll('#tabs .tab'));
matrixTabs.forEach((tab, index) => {
  const panel = document.getElementById('panel-' + tab.dataset.tab);
  tab.id = 'tab-' + tab.dataset.tab;
  tab.setAttribute('role', 'tab');
  tab.setAttribute('aria-controls', panel.id);
  panel.setAttribute('role', 'tabpanel');
  panel.setAttribute('aria-labelledby', tab.id);
  const active = tab.classList.contains('active');
  tab.tabIndex = active ? 0 : -1;
  tab.setAttribute('aria-selected', String(active));
  tab.addEventListener('click', () => {
    matrixTabs.forEach(item => {
      const current = item === tab;
      item.tabIndex = current ? 0 : -1;
      item.setAttribute('aria-selected', String(current));
      item.classList.toggle('active', current);
      document.getElementById('panel-' + item.dataset.tab).classList.toggle('active', current);
    });
  });
  tab.addEventListener('keydown', event => {
    const targets = {ArrowLeft: (index + matrixTabs.length - 1) % matrixTabs.length,
      ArrowRight: (index + 1) % matrixTabs.length, Home: 0, End: matrixTabs.length - 1};
    if (!(event.key in targets) || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    const next = matrixTabs[targets[event.key]];
    next.focus();
    next.click();
  });
});

// Stats
const tierCounts = {T1:0,T2:0,T3:0,T4:0};
TOOLS.forEach(t => tierCounts[t.t]++);
document.getElementById('stats').innerHTML = `
  <div class="stat stat-total"><div class="stat-value">${TOOLS.length}</div><div class="stat-label">Tổng số tool</div></div>
  <div class="stat stat-t1"><div class="stat-value">${tierCounts.T1}</div><div class="stat-label">T1 Đọc</div></div>
  <div class="stat stat-t2"><div class="stat-value">${tierCounts.T2}</div><div class="stat-label">T2 Ghi</div></div>
  <div class="stat stat-t3"><div class="stat-value">${tierCounts.T3}</div><div class="stat-label">T3 Phiên</div></div>
  <div class="stat stat-t4"><div class="stat-value">${tierCounts.T4}</div><div class="stat-label">T4 Desktop</div></div>
  <div class="stat"><div class="stat-value" style="color:var(--pink)">${SKILLS.length}</div><div class="stat-label">Skills</div></div>
  <div class="stat"><div class="stat-value" style="color:var(--fg2)">${CHAINS.length}</div><div class="stat-label">Chuỗi tool</div></div>
`;

// Tool inventory
function normalizeLookup(value) {
  return value.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd').replace(/[^a-z0-9]+/g, ' ').trim();
}

const inventoryGrid = document.getElementById('tool-grid');
const inventoryLayout = document.getElementById('inventory-layout');
const inventoryDetail = document.getElementById('detail-panel');
const inventoryContent = document.getElementById('inventory-detail-content');
const inventorySearch = document.getElementById('search');
const categoryFilter = document.getElementById('inventory-category');
const tierFilters = document.querySelectorAll('.inventory-tiers [data-tier]');
const activeTiers = new Set(Object.keys(tierCounts));
let selectedTool = null;
const inventoryEntries = TOOLS.map(tool => {
  const categories = tool.cat.split(',').map(item => item.trim()).filter(Boolean);
  const name = normalizeLookup(tool.n);
  const searchable = [name, tool.t, ...categories, tool.fg, tool.eff, tool.uc].filter(Boolean).join(' ');
  return {tool, categories, name, searchable: normalizeLookup(searchable)};
});
const categoryNames = new Set(inventoryEntries.flatMap(entry => entry.categories));
Array.from(categoryNames).filter(name => !activeTiers.has(name.toUpperCase())).sort()
  .forEach(name => categoryFilter.add(new Option(name, name)));

function renderGrid() {
  const query = normalizeLookup(inventorySearch.value);
  const words = query.split(' ').filter(Boolean);
  const filtered = inventoryEntries.filter(entry => activeTiers.has(entry.tool.t)
    && (!categoryFilter.value || entry.categories.includes(categoryFilter.value))
    && words.every(word => entry.searchable.includes(word)));
  if (query) {
    const rank = entry => entry.name === query ? 0 : entry.name.startsWith(query) ? 1 : entry.name.includes(query) ? 2 : 3;
    filtered.sort((left, right) => rank(left) - rank(right));
  }
  if (selectedTool && !filtered.some(entry => entry.tool.n === selectedTool)) setInventoryDetail(null);
  document.getElementById('result-count').textContent = filtered.length + ' / ' + TOOLS.length + ' tool';
  document.getElementById('inventory-empty').hidden = filtered.length !== 0;
  inventoryGrid.innerHTML = filtered.map(({tool: t}) => `
    <button type="button" class="tool-card${selectedTool === t.n ? ' selected' : ''}" data-tool="${esc(t.n)}"
        aria-label="${esc(t.n)}" aria-controls="detail-panel" aria-expanded="${selectedTool === t.n}">
      <span class="tool-name">${esc(t.n)}</span>
      ${t.eff ? '<span class="inventory-summary">' + esc(t.eff) + '</span>' : ''}
      <span class="tool-meta">
        <span class="badge badge-${t.t.toLowerCase()}">${esc(t.t)}</span>
        <span class="badge badge-cat">${esc(t.cat.split(',')[0])}</span>
        ${t.mut ? '<span class="badge badge-mut">Ghi</span>' : ''}
        ${t.prot ? '<span class="badge badge-protect">Bảo vệ</span>' : ''}
        ${t.err ? '<span class="badge badge-err-' + esc(t.err.c) + '">Lỗi (' + esc(t.err.c) + ')</span>' : ''}
        ${t.dep ? '<span class="badge badge-dep">Ngừng dùng</span>' : ''}
        ${t.miss ? '<span class="badge badge-miss">Thiếu dữ liệu</span>' : ''}
      </span>
    </button>`).join('');
}

function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function usageLabel(t) {
  const u = t.use || {b: 'unknown', c: null, ls: null};
  if (u.b === 'unknown') return 'chưa rõ mức dùng (không có nhật ký)' + (t.n === 'close_session' ? ' — R16: một dòng sự kiện duy nhất, không đáng tin' : '');
  if (u.b === 'unused') return 'không dùng trong cửa sổ · 0 lượt · ' + USAGE_WINDOW_SHORT;
  return u.b + ' · ' + u.c + ' lượt · lần cuối ' + (u.ls || '—') + ' · ' + USAGE_WINDOW_SHORT;
}
function detailHTML(t, opts) {
  const slim = !!(opts && opts.slim);
  const inChains = CHAINS.filter(c => c.tools.includes(t.n));
  const stepIdx = {};
  CHAINS.forEach(c => { if (c.tools.includes(t.n)) stepIdx[c.name] = c.tools.indexOf(t.n); });
  const row = (label, value) => '<div class="detail-row"><span class="detail-label">' + label + '</span><span class="detail-value">' + value + '</span></div>';
  const yesNo = v => v === true ? 'Có' : v === false ? 'Không' : 'Chưa rõ';
  const NO_WRITES = 'Không ghi gì';
  const writesHtml = (!t.wr || !t.wr.length) ? NO_WRITES
    : '<ul class="detail-list">' + t.wr.map(w => '<li><b>' + esc(w.kind) + '</b> — ' + esc(w.target) + (w.condition ? ' <span class="detail-aside">(' + esc(w.condition) + ')</span>' : '') + '</li>').join('') + '</ul>';
  const errHtml = t.err ? row('Sai sót', '<span class="badge badge-err-' + t.err.c + '">SAI SÓT (' + t.err.c + ')</span> ' + esc(t.err.r) + (t.err.q ? ' — ' + esc(t.err.q) : '')) : '';
  const depHtml = t.dep ? row('Ngừng dùng', 'Có — máy đọc được (' + (t.n === 'electron_devtools_log' ? 'code_tools.py:19990-19992' : 'orchestrator_server.py:1922-1930') + ')') : '';
  const docHtml = t.n === 'write_fix_html' ? row('Ngừng dùng', 'Chỉ ở docstring [DEPRECATED V15 R8] code_tools.py:1664 — KHÔNG phải dấu ngừng dùng (R8)') : '';
  const missHtml = t.miss ? row('Thiếu', '⚠ THIẾU trong file gốc — bổ sung Wave 19 từ registry sống') : '';
  const wkHtml = (t.wk ?? 'Chưa rõ') + (t.der === true ? ' · derived_index_writer: CÓ (graph CBM)' : t.der === false ? ' · derived_index_writer: không' : ' · derived_index_writer: chưa rõ') +
    ((t.n === 'create_benchmark_request' || t.n === 'create_build_context') ? ' · lần ghi snapshot INDEX.json được ghi ở phần writes, không phải derived_index_writer (R54)' : '');
  const chainsInner = inChains.map(c =>
    '<div class="chain-row"><span class="chain-name">' + c.icon + ' ' + esc(c.name) + ' — bước ' + (stepIdx[c.name] + 1) + '/' + c.tools.length + '</span>'
    + '<div class="chain-steps">'
    + c.tools.map((s, i) => '<span class="chain-step' + (s === t.n ? ' highlight' : '') + '">' + (i === stepIdx[c.name] ? '→ ' : '') + esc(s) + '</span>' + (i < c.tools.length - 1 ? '<span class="chain-arrow">›</span>' : '')).join('')
    + '</div></div>').join('');
  const islandHtml = '<p class="detail-island">Chưa có trong chuỗi tool nào</p>';

  if (slim) {
    // The map panel answers "what is this for" first and hides the machine facts
    // behind disclosures. 18 always-open label/value rows made a leaf read as a
    // wall of text, which is what the inventory panel is for, not the map.
    const sect = (title, inner, count) => '<details class="detail-sect"><summary>' + title
      + (count === undefined ? '' : ' · ' + count) + '</summary>' + inner + '</details>';
    const facts = row('Danh mục', esc(t.cat || 'Chưa rõ'))
      + row('Nhóm chức năng', t.fg ? esc(t.fg) : '— (không có fg: THIẾU trong file)')
      + row('Ghi sửa', yesNo(t.mut)) + row('Được khoá', yesNo(t.prot)) + row('Phiên', t.sess === true ? 'Bắt buộc' : t.sess === false ? 'Độc lập' : 'Chưa rõ')
      + row('Hàm xử lý', esc(t.h || 'Chưa có trong bản kiểm kê'))
      + row('Ghi chỉ mục suy diễn', esc(wkHtml))
      + row('Mức dùng', esc(usageLabel(t)));
    const provenance = row('Ghi vào', writesHtml)
      + row('Nguồn dẫn', (t.cit || []).length ? t.cit.map(esc).join('<br>') : 'Chưa có nguồn trong bản kiểm kê')
      + (t.note ? row('Ghi chú', esc(t.note)) : '')
      + errHtml + depHtml + docHtml + missHtml;
    return '<div class="detail-slim">'
      + '<p class="detail-effect">' + esc(t.eff || 'Chưa có trong bản kiểm kê') + '</p>'
      + (t.uc ? '<p class="detail-use">' + esc(t.uc) + '</p>' : '')
      + sect('Thông số', facts)
      + sect('Ghi & nguồn', provenance)
      + (inChains.length ? sect('Chuỗi', chainsInner, inChains.length) : islandHtml)
      + '</div>';
  }

  return `
    <div class="detail-name">${t.n}</div>
    ${row('Tầng', '<span class="badge badge-' + t.t.toLowerCase() + '">' + t.t + '</span>')}
    ${row('Danh mục', esc(t.cat || 'Chưa rõ'))}
    ${row('Nhóm chức năng', t.fg ? esc(t.fg) : '— (không có fg: THIẾU trong file)')}
    ${row('Ghi sửa', yesNo(t.mut))}
    ${row('Được khoá', yesNo(t.prot))}
    ${row('Phiên', t.sess === true ? 'Bắt buộc' : t.sess === false ? 'Độc lập' : 'Chưa rõ')}
    ${row('Tác dụng', esc(t.eff || 'Chưa có trong bản kiểm kê'))}
    ${row('Trường hợp dùng', esc(t.uc || 'Chưa có trong bản kiểm kê'))}
    ${row('Nguồn dẫn', (t.cit || []).length ? t.cit.map(esc).join('<br>') : 'Chưa có nguồn trong bản kiểm kê')}
    ${row('Hàm xử lý', esc(t.h || 'Chưa có trong bản kiểm kê'))}
    ${row('Ghi chỉ mục suy diễn', esc(wkHtml))}
    ${row('Ghi vào', writesHtml)}
    ${errHtml}${depHtml}${docHtml}${missHtml}
    ${row('Mức dùng', esc(usageLabel(t)))}
    ${t.note ? row('Ghi chú', esc(t.note)) : ''}
    ${inChains.length ? '<div class="detail-chain"><h3>Chuỗi (' + inChains.length + ')</h3>' + chainsInner + '</div>' : islandHtml}
  `;
}
function setInventoryDetail(tool) {
  selectedTool = tool ? tool.n : null;
  inventoryDetail.hidden = !tool;
  inventoryLayout.classList.toggle('has-detail', Boolean(tool));
  inventoryContent.innerHTML = tool ? detailHTML(tool) : '';
  inventoryGrid.querySelectorAll('.tool-card.selected, .tool-card[aria-expanded="true"]').forEach(card => {
    card.classList.remove('selected');
    card.setAttribute('aria-expanded', 'false');
  });
  if (tool) {
    const card = inventoryGrid.querySelector('[data-tool="' + CSS.escape(tool.n) + '"]');
    card.classList.add('selected');
    card.setAttribute('aria-expanded', 'true');
    inventoryDetail.focus();
  }
}

// A map leaf can be outside the directory's last filter. Reveal that tool while
// preserving any category and tier filters compatible with the requested result.
function revealInInventory(name) {
  const entry = inventoryEntries.find(item => item.tool.n === name);
  if (!entry) return;
  document.getElementById('tab-inventory').click();
  inventorySearch.value = name;
  if (!entry.categories.includes(categoryFilter.value)) categoryFilter.value = '';
  const tierButton = Array.from(tierFilters).find(button => button.dataset.tier === entry.tool.t);
  if (!activeTiers.has(entry.tool.t)) tierButton.click();
  else renderGrid();
  setInventoryDetail(entry.tool);
}

inventoryGrid.addEventListener('click', event => {
  const card = event.target.closest('.tool-card');
  if (card) setInventoryDetail(selectedTool === card.dataset.tool ? null : TOOLS.find(t => t.n === card.dataset.tool));
});
function closeInventoryDetail() {
  const card = inventoryGrid.querySelector('.selected');
  setInventoryDetail(null);
  if (card) card.focus();
}
document.getElementById('inventory-close').addEventListener('click', closeInventoryDetail);
inventoryDetail.addEventListener('keydown', event => {
  if (event.key === 'Escape') { event.preventDefault(); closeInventoryDetail(); }
});
tierFilters.forEach(btn => btn.addEventListener('click', () => {
  const tier = btn.dataset.tier;
  if (activeTiers.has(tier)) activeTiers.delete(tier);
  else activeTiers.add(tier);
  btn.classList.toggle('active', activeTiers.has(tier));
  btn.setAttribute('aria-pressed', String(activeTiers.has(tier)));
  renderGrid();
}));
categoryFilter.addEventListener('change', renderGrid);
inventorySearch.addEventListener('input', renderGrid);
document.getElementById('inventory-reset').addEventListener('click', () => {
  inventorySearch.value = categoryFilter.value = '';
  tierFilters.forEach(btn => {
    activeTiers.add(btn.dataset.tier);
    btn.classList.add('active');
    btn.setAttribute('aria-pressed', 'true');
  });
  renderGrid();
  inventorySearch.focus();
});
renderGrid();

// Chains
const chainsEl = document.getElementById('chains-container');
chainsEl.innerHTML = CHAINS.map(c => `
  <div class="chain-card">
    <div class="chain-title"><span>${c.icon}</span> ${c.name}</div>
    <div class="chain-steps">
      ${c.steps.map((s,i) => `<span class="chain-step">${s}</span>${i<c.steps.length-1?'<span class="chain-arrow">→</span>':''}`).join('')}
    </div>
    <div class="chain-desc">${c.desc}</div>
  </div>
`).join('');

// Gaps
const allChainTools = new Set(CHAINS.flatMap(c => c.tools));
const islandTools = TOOLS.filter(t => !allChainTools.has(t.n));
const funcGroups = {};
TOOLS.forEach(t => { if (t.fg) funcGroups[t.fg] = (funcGroups[t.fg]||0)+1; });

const gapsEl = document.getElementById('gaps-container');
gapsEl.innerHTML = `
  <h2>Island Tools — Không có chain kết nối</h2>
  <p style="font-size:0.8rem;color:var(--fg2);margin-bottom:12px;">${islandTools.length} / ${TOOLS.length} tool không nằm trong bất kỳ chain nào. tool Decision Hint có thể giúp CA tìm tool phù hợp.</p>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px;margin-bottom:20px;">
    ${islandTools.slice(0,60).map(t => `<div class="gap-card info"><div class="gap-title mono" style="font-size:0.72rem">${t.n}</div><div class="gap-detail">${t.t} · ${t.fg||'—'} ${t.mut?'· MUT':''}</div></div>`).join('')}
    ${islandTools.length>60?`<div class="gap-card warn"><div class="gap-detail">+${islandTools.length-60} more...</div></div>`:''}
  </div>

  <h2>Function Group Distribution</h2>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin-bottom:20px;">
    ${Object.entries(funcGroups).sort((a,b)=>b[1]-a[1]).map(([k,v]) => `
      <div class="stat"><div class="stat-value" style="font-size:1.2rem">${v}</div><div class="stat-label">${k}</div></div>
    `).join('')}
  </div>

  <h2>Redundancy Check — Tool Overlap</h2>
  <div class="gap-card warn">
    <div class="gap-title">query_index vs search_code vs regex_search vs find_symbol</div>
    <div class="gap-detail">4 search tools có chức năng overlap. Query_index = INDEX semantic; search_code = full-text; regex_search = regex (DEPRECATED — machine-readable, server :1922; use search_code mode=regex instead); find_symbol = symbol-specific. Phân biệt rõ: INDEX cho callable refs, search_code cho text pattern.</div>
  </div>
  <div class="gap-card warn">
    <div class="gap-title">read_file_range vs read_chunks vs read_context_batch</div>
    <div class="gap-detail">3 read tools. read_file_range = primary (seeds write token); read_chunks = multi-file batch (DEPRECATED — machine-readable, server :1922; use read_file_range ranges[] instead); read_context_batch = pre-built context packs. Dùng read_file_range làm default.</div>
  </div>
  <div class="gap-card warn">
    <div class="gap-title">validate_file vs validate_symbols vs validate_encoding vs validate</div>
    <div class="gap-detail">4 validate tools. validate_file = syntax + drift; validate_symbols = function/class drift; validate_encoding = BOM/encoding; validate = generic. Dùng validate_file làm primary.</div>
  </div>
  <div class="gap-card info">
    <div class="gap-title">desktop_click vs click_selector vs click</div>
    <div class="gap-detail">3 click tools cho desktop. desktop_click = native OS click; click_selector = CDP selector; click = generic T4. Phân biệt: desktop_click cho OS-level, click_selector cho DOM.</div>
  </div>
  <div class="gap-card info">
    <div class="gap-title">log_native vs log_native_event</div>
    <div class="gap-detail">2 audit log tools. log_native_event = structured event log; log_native is NOT an alias of log_native_event (R60(l)) — separate handler, separate registry entry. Dùng log_native_event.</div>
  </div>

  <h2>Skill Coverage</h2>
  <p style="font-size:0.8rem;color:var(--fg2);margin-bottom:12px;">35 skills covering ${Object.keys(funcGroups).length} function groups.</p>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:6px;">
    ${SKILLS.map(s => `<div class="gap-card info"><div class="gap-title" style="font-size:0.78rem">${s}</div></div>`).join('')}
  </div>
`;

// Automation Opportunities
const autoEl = document.getElementById('auto-container');
const autoRules = [
  {trigger:"CA gọi write_fix",action:"→ auto-validate_file → auto-ruff_fix",reason:"Mỗi write phải validate syntax + drift. ruff_fix auto-fix lint issues. Không validate = silent syntax error."},
  {trigger:"CA gọi cbm_dead_code",action:"→ auto-cbm_index (nếu stale)",reason:"Dead code analysis cần graph index fresh. Nếu graph stale → kết quả sai. cbm_graph_status check freshness trước."},
  {trigger:"CA gọi cbm_test_gap",action:"→ auto-cbm_index → auto-cbm_quality_gate",reason:"Test gap cần architecture context. Chạy quality_gate cùng lúc để có full picture."},
  {trigger:"CA gọi open_session",action:"→ auto-get_rule_core_text → auto-cache_status",reason:"Mỗi session mới cần load core rule. Cache status check graph/index freshness."},
  {trigger:"CA gọi search_code",action:"→ auto-read_file_range (nếu tìm thấy)",reason:"Search code tìm file+line → CA thường cần read ngay. Không cần 2 round trips."},
  {trigger:"CA gọi git_commit",action:"→ auto-git_status (trước) → auto-validate_file (post)",reason:"git_commit cần expected_staged_hash từ git_status. Post-commit validate đảm bảo clean."},
  {trigger:"CA gọi cbm_graph_status",action:"→ auto-cbm_index (nếu not_indexed)",reason:"Graph status trả về not_indexed → auto-index thay vì CA phải gọi riêng."},
  {trigger:"CA gọi launch_electron_with_cdp",action:"→ auto-check_window_visibility → auto-dom_state_diff(BASELINE)",reason:"Launch app → check visibility → capture baseline. 3 steps luôn đi cùng nhau."},
  {trigger:"CA gọi convene_council",action:"→ load leader-council-skill (trước)",reason:"Rule yêu cầu load skill trước convene. Auto-load thay vì CA phải tự nhớ."},
  {trigger:"CA gọi delete_file",action:"→ DRY RUN first → confirm DESTRUCTIVE",reason:"Delete luôn cần 2-turn: list targets trước, confirm sau. Không bao giờ auto-delete."},
  {trigger:"CA gọi cbm_what_if",action:"→ auto-cbm_index (nếu stale) → auto-cbm_quality_gate (baseline)",reason:"What-if cần baseline quality + fresh graph. Auto-seed trước khi phân tích."},
  {trigger:"CA gọi ruff_fix",action:"→ auto-validate_file (post-fix)",reason:"Ruff auto-fix có thể introduce syntax errors. Validate sau fix."},
];

autoEl.innerHTML = `
  <h2>Automation Rules — Auto-Chain Proposals</h2>
  <p style="font-size:0.8rem;color:var(--fg2);margin-bottom:16px;">${autoRules.length} rule. Khi CA gọi tool trigger → nên auto-call action. Giảm round-trips, tăng reliability.</p>
  ${autoRules.map(r => `
    <div class="auto-card">
      <div class="auto-trigger">WHEN ${r.trigger}</div>
      <div class="auto-action">${r.action}</div>
      <div class="auto-reason">${r.reason}</div>
    </div>
  `).join('')}

  <h2 style="margin-top:20px">Tool Tier Usage Rules (từ CLAUDE.md)</h2>
  <div class="chain-card">
    <div class="chain-desc" style="font-size:0.82rem;line-height:1.7;">
      <strong>T1 Đọc</strong> — Không cần approval. Dùng native khi inspect, dùng [A] khi cần INDEX semantics.<br>
      <strong>T2 Ghi</strong> — Cần "tiến hành" + rõ ràng. Luôn validate post-write. Dùng [A] write tools, KHÔNG native write.<br>
      <strong>T3 Phiên</strong> — Internal lifecycle. CA không gọi trực tiếp trừ khi rule yêu cầu.<br>
      <strong>T4 Desktop</strong> — Dùng MCP hoặc HTTP. Không dùng computer-use inline. Cần CDP port.<br><br>
      <strong>Hard Rules:</strong><br>
      • Read Routing: native to LOOK, [A] to TOUCH<br>
      • Write Pipeline: read_file_range → write_fix → validate_file (LUÔN)<br>
      • Destructive: 2-turn rule — list first, confirm second<br>
      • Session: open_session first, log_native_event sau write, close_session cuối
    </div>
  </div>
`;

// ═══════════════════════════════════════════════════════════════
// RADIAL TREE (dashboard build, 2026-11-17) — replaces the APEX layout (2026-11-16).
// 4 levels: center ToolA → 4 tiers → 37 (tier,fg) groups → 260 tools.
// Uses TOOLS directly (no SUNBURST tree). Chain wires come from SB_LINKS/SB_AUTO.
// Leaf nodes are unreadable at overview scale, so a tier board (renderTierBoard)
// is the human-read surface: click a tier for large cards with full use cases.
// Dark theme: uses the matrix CSS variables.
// NOTE: USAGE_WINDOW_SHORT is kept because usageLabel() (used by detailHTML) needs it.
const USAGE_WINDOW_SHORT = '08 Nov–16 Nov 2026 (8.5d window)';
(function(){
  "use strict";
  var TIER_COL = {T1: '#4ade80', T2: '#fbbf24', T3: '#a78bfa', T4: '#22d3ee'};
  var TIER_LABEL = {T1: 'T1 Read', T2: 'T2 Write', T3: 'T3 Audit', T4: 'T4 Desktop'};
  var TIERS = ['T1', 'T2', 'T3', 'T4'];
  var tools = TOOLS.slice();
  var byTier = {};
  TIERS.forEach(function(t){ byTier[t] = []; });
  tools.forEach(function(t){ if (byTier[t.t]) byTier[t.t].push(t); });
  TIERS.forEach(function(t){ byTier[t].sort(function(a, b){ return a.n < b.n ? -1 : 1; }); });

  // Groups (draft v5 §1.2): key = tier + '|' + fg. Order: tier T1→T4, then
  // tool count desc, then fg asc. weight = max(tool count, 4) — the literal 4
  // is MIN_LEAVES (defined in the geometry block below; this build runs
  // before that assignment, so the literal is used here, not the variable).
  var groups = [];
  var groupByKeyMap = {};
  TIERS.forEach(function(tier){
    var byFg = {};
    byTier[tier].forEach(function(t){
      var fg = t.fg || '';
      if (!byFg[fg]) byFg[fg] = [];
      byFg[fg].push(t);
    });
    Object.keys(byFg).sort(function(fgA, fgB){
      var d = byFg[fgB].length - byFg[fgA].length;
      return d !== 0 ? d : (fgA < fgB ? -1 : (fgA > fgB ? 1 : 0));
    }).forEach(function(fg){
      var key = tier + '|' + fg;
      var g = { key: key, tier: tier, fg: fg, tools: byFg[fg].slice(), weight: Math.max(byFg[fg].length, 4) };
      groups.push(g);
      groupByKeyMap[key] = g;
    });
  });
  function groupByKey(key){ return groupByKeyMap[key] || null; }
  function groupLabel(key){
    var g = groupByKey(key);
    if (!g) return key;
    return g.fg ? g.fg : '(no group)';
  }
  var total = tools.length;
  var W = 900, H = 760, CX = 450, CY = 380;
  // Radial tree geometry (draft v5): root 54 / tier 36 / group 8 / tool 4.
  var R_TIER = 155, R_GROUP = 250, R_TOOL = 350;
  var R_CENTER = 54, R_TIER_NODE = 36, R_GROUP_NODE = 8, R_TOOL_NODE = 4;
  var MIN_LEAVES = 4; // a group with fewer tools still gets a 4-tool sector

  // Angular sectors proportional to tool count, starting at top (-90deg), clockwise.
  var tierSectors = {};
  var a = -Math.PI / 2;
  TIERS.forEach(function(tier){
    var frac = byTier[tier].length / total;
    var span = frac * 2 * Math.PI;
    tierSectors[tier] = { a0: a, a1: a + span, mid: a + span / 2 };
    a += span;
  });
  // Group sectors within each tier: weight = max(tool count, MIN_LEAVES).
  // Total weight 292; smallest sector 4.49° (T2|deploy). Groups are stored
  // tier-major, so a running cursor per tier assigns contiguous sectors.
  var tierWeight = {};
  TIERS.forEach(function(t){
    var w = 0;
    groups.forEach(function(g){ if (g.tier === t) w += g.weight; });
    tierWeight[t] = w;
  });
  var groupCursor = {};
  TIERS.forEach(function(t){ groupCursor[t] = tierSectors[t].a0; });
  groups.forEach(function(g){
    var sec = tierSectors[g.tier];
    var span = (g.weight / tierWeight[g.tier]) * (sec.a1 - sec.a0);
    g.a0 = groupCursor[g.tier];
    g.a1 = g.a0 + span;
    g.mid = g.a0 + span / 2;
    groupCursor[g.tier] = g.a1;
  });
  function polar(r, ang){ return [CX + r * Math.cos(ang), CY + r * Math.sin(ang)]; }
  // Cubic Bézier tree edge (draft v5 §1.5): control radius rm = (r1+r2)/2.
  // When a1 = a2 the curve degenerates to a straight radial segment.
  function treeEdgePath(r1, a1, r2, a2){
    var p1 = polar(r1, a1), p2 = polar(r2, a2);
    var rm = (r1 + r2) / 2;
    var c1 = polar(rm, a1), c2 = polar(rm, a2);
    return 'M ' + p1[0] + ' ' + p1[1] + ' C ' + c1[0] + ' ' + c1[1] + ', ' + c2[0] + ' ' + c2[1] + ', ' + p2[0] + ' ' + p2[1];
  }

  var host = document.getElementById('sunburst-container');
  if (!host) return;
  var svgNS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs){
    var e = document.createElementNS(svgNS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, width: '900', height: '760', role: 'img', 'aria-label': 'ToolA radial tree: 260 tools in 37 groups in 4 tiers' });
  var gZoom = el('g', { id: 'radial-zoom' });
  var gEdges = el('g', {});
  var gWires = el('g', { id: 'radial-wires' });
  var gNodes = el('g', {});
  var gLabels = el('g', {});
  var gZoomLabels = el('g', { id: 'radial-zoom-labels', style: 'display:none' });
  var gGv = el('g', { id: 'radial-groupview', style: 'display:none' });
  // Arrow markers for chain wires.
  var defs = el('defs', {});
  var mk1 = el('marker', { id: 'rw-arrow', viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' });
  mk1.appendChild(el('path', { d: 'M0,0 L10,5 L0,10 z', fill: '#c9d1d9' }));
  var mk2 = el('marker', { id: 'rw-arrow-auto', viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' });
  mk2.appendChild(el('path', { d: 'M0,0 L10,5 L0,10 z', fill: '#fbbf24' }));
  defs.appendChild(mk1);
  defs.appendChild(mk2);
  svg.appendChild(defs);
  gZoom.appendChild(gEdges);
  gZoom.appendChild(gWires);
  gZoom.appendChild(gNodes);
  gZoom.appendChild(gLabels);
  gZoom.appendChild(gZoomLabels);
  gZoom.appendChild(gGv);
  svg.appendChild(gZoom);
  host.innerHTML = '';
  host.appendChild(svg);
  var detailEl = document.getElementById('sb-detail');
  var searchEl = document.getElementById('sb-search');
  var resetBtn = document.getElementById('sb-reset');
  var crumbEl = document.getElementById('sb-breadcrumb');

  function toolByName(name){
    for (var i = 0; i < TOOLS.length; i++) if (TOOLS[i].n === name) return TOOLS[i];
    return null;
  }
  // Writer tracking (O7i content check): every write to #sb-detail records
  // who wrote it, what content kind it holds, and a monotonic counter.
  // 'hover' updates detailState but is NOT a panel writer.
  function notePanelWrite(via, state){
    if (via !== 'hover'){
      lastDetailWriter = via;
      lastPanelWriteAt = ++writeCounter;
    }
    detailState = state;
  }
  // Inventory edges only: neither the hierarchy nor observed calls are dependencies.
  const relationKinds = ['delegates-to', 'alias-dispatch', 'alias-shared-handler', 'recommends', 'auto-chain'];
  let relationIndex;
  function relationPanel(names){
    if (!relationIndex){
      const byKey = new Map();
      function add(from, to, kind, source, reason){
        const shared = kind === 'alias-shared-handler';
        const key = JSON.stringify([kind, ...(shared ? [from, to].sort() : [from,to])]);
        if (!byKey.has(key)) byKey.set(key, {from,to,kind,shared,evidence:new Set()});
        byKey.get(key).evidence.add((source || 'Chưa có nguồn') + (reason ? ' — ' + reason : ''));
      }
      SB_LINKS.forEach(e => add(e.f,e.t,e.k,e.c,e.q));
      SB_AUTO.forEach(e => add(e.f,e.t,'auto-chain','Auto-Chain · nhóm ' + e.r + ' (chưa đo runtime)',e.c));
      relationIndex = [...byKey.values()];
    }
    const scope = new Set(names);
    const edges = relationIndex.filter(e => scope.has(e.from) || scope.has(e.to));
    const rows = relationKinds.map(kind => {
      const subset = edges.filter(e => e.kind === kind);
      const incoming = subset.filter(e => scope.has(e.to)).length;
      const outgoing = subset.filter(e => scope.has(e.from)).length;
      return '<li><b>' + kind + '</b> · ' + subset.length + (kind === 'alias-shared-handler' ? ' cặp không hướng' : ' cạnh · ' + incoming + ' vào / ' + outgoing + ' ra') + '</li>';
    }).join('');
    const sources = edges.map(e => '<li><b>' + esc(e.kind) + '</b> · <span class="mono">' + esc(e.from) + (e.shared ? ' ↔ ' : ' → ') + esc(e.to) + '</span> · ' + [...e.evidence].map(esc).join(' · ') + '</li>').join('');
    // A single tool almost always has zero edges, and then the five-row census is five
    // lines of "0" that say nothing while costing 214px of a 553px panel. Say it once.
    const census = edges.length
      ? '<ul>' + rows + '</ul>'
      : '<p class="meta-empty">Chưa có cạnh nào ghi cho công cụ này trong bản kiểm kê.</p>';
    const method = '<details class="meta-method"><summary>Cách đọc</summary><p>Cạnh nội bộ tính một lần vào tổng, đồng thời có đầu vào và đầu ra. Không có cạnh đã ghi không có nghĩa là không có liên kết. Bộ lọc chỉ đổi đường vẽ.</p></details>';
    return '<details class="toola-matrix-meta"' + (names.length === 1 ? ' open' : '') + '><summary>Liên kết kiểm kê · ' + edges.length + ' cạnh/cặp phân biệt</summary>'
      + census + method
      + (sources ? '<details><summary>Nguồn và chứng cứ</summary><ul>' + sources + '</ul></details>' : '') + '</details>';
  }
  function renderSearchMatches(query){
    const results = document.getElementById('sb-search-results');
    results.replaceChildren();
    const q = query.trim().toLowerCase();
    if (!q) return;
    const found = tools.filter(t => t.n.toLowerCase().includes(q));
    const label = document.createElement('span');
    label.textContent = found.length + ' kết quả toàn đồ thị' + (found.length > 8 ? ' · 8 mục đầu' : '');
    results.appendChild(label);
    found.slice(0,8).forEach(t => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'filter-btn';
      button.textContent = t.n;
      button.onclick = () => {
        searchEl.value = '';
        renderSearchMatches('');
        preSearchDetail = preSearchDetailState = null;
        pillFilter = null; activePillKey = null; activePillLabel = '';
        clearHighlight();
        clearTierBoard();
        onLeafClick(t.n);
        detailEl.tabIndex = -1;
        detailEl.focus();
      };
      results.appendChild(button);
    });
  }
  function setDetailTool(t, via){
    if (!detailEl || !t) return;
    detailEl.innerHTML = '<div class="detail-panel" style="margin-top:0">' + detailHTML(t) + relationPanel([t.n]) + '<p class="toola-matrix-meta" id="sb-tool-activity"></p></div>';
    renderToolActivity(t.n);
    notePanelWrite(via || 'pin', { kind: 'tool', name: t.n });
  }
  function setDetailRoot(){
    if (!detailEl) return;
    detailEl.innerHTML = '<div class="detail-panel" style="margin-top:0"><div class="detail-name">ToolA — ' + total + ' tool</div>'
      + '<div class="detail-row"><span class="detail-label">Dạng</span><span class="detail-value">Radial tree: tâm → 4 tier → 37 nhóm → 260 tool</span></div>'
      + '<div class="detail-row"><span class="detail-label">Hướng dẫn</span><span class="detail-value">Rê chuột lên một node ngoài để xem nhanh use case. Bấm vào node để xem đầy đủ ở đây. Bấm vào một tier để làm nổi bật toàn bộ tool của tier đó và mở bảng đọc (tên + use case + chain) bên dưới.</span></div></div>';
    detailEl.insertAdjacentHTML('beforeend', relationPanel(tools.map(t => t.n)));
    notePanelWrite('reset', { kind: 'root' });
  }

  // Center node (root) — click = full reset (O7l): clears the search input
  // too, which the old handler did not.
  var centerCircle = el('circle', { cx: CX, cy: CY, r: R_CENTER, fill: '#1a2a52', stroke: 'var(--border)', 'stroke-width': 1.5, style: 'cursor:pointer' });
  centerCircle.addEventListener('click', function(){
    if (searchEl) searchEl.value = '';
    renderSearchMatches('');
    preSearchDetail = null;
    preSearchDetailState = null;
    pillFilter = null; activePillKey = null; activePillLabel = ''; // Center-click is Reset: clears the pill too (F-4)
    clearHighlight();
    clearTierBoard();
    setDetailRoot();
  });
  centerCircle.addEventListener('mouseenter', function(ev){
    showTip('<div style="font-size:13px;font-weight:700">ToolA</div><div style="color:var(--fg3);font-size:11px">' + total + ' tools &middot; 37 groups &middot; 4 tiers</div>', ev.clientX, ev.clientY);
  });
  centerCircle.addEventListener('mousemove', function(ev){
    showTip('<div style="font-size:13px;font-weight:700">ToolA</div><div style="color:var(--fg3);font-size:11px">' + total + ' tools &middot; 37 groups &middot; 4 tiers</div>', ev.clientX, ev.clientY);
  });
  centerCircle.addEventListener('mouseleave', function(){ hideTip(); });
  gNodes.appendChild(centerCircle);
  var ct1 = el('text', { x: CX, y: CY - 4, 'text-anchor': 'middle', 'font-size': 20, 'font-weight': 700, fill: '#ffffff', style: 'pointer-events:none' });
  ct1.textContent = 'ToolA';
  var ct2 = el('text', { x: CX, y: CY + 16, 'text-anchor': 'middle', 'font-size': 12, fill: '#bfdbfe', style: 'pointer-events:none' });
  ct2.textContent = total + ' tool';
  gLabels.appendChild(ct1);
  gLabels.appendChild(ct2);
  // Tier nodes + group nodes + tool nodes (radial tree).
  // Leaf hover writes no panel (v5 §1.8 writers exclude hover — tooltip +
  // focus only; the panel is for click/pin). This also keeps the zero-match
  // message intact while hovering (F-2).
  var toolNodes = [];
  var radialPos = {};
  var tierNodeEls = {};
  var groupNodeEls = {}; // group key -> circle
  var edgeEls = []; // {el, childKind: 'tier'|'group'|'tool', childKey, childTier}

  function tierTooltip(tier){
    var gCount = 0;
    groups.forEach(function(g){ if (g.tier === tier) gCount++; });
    return '<div style="font-size:13px;font-weight:700">' + esc(TIER_LABEL[tier]) + '</div>'
      + '<div style="color:var(--fg3);font-size:11px">' + byTier[tier].length + ' tools &middot; ' + gCount + ' groups</div>';
  }
  function groupTooltip(key){
    var g = groupByKey(key);
    return '<div style="font-size:13px;font-weight:700">' + esc(groupLabel(key)) + '</div>'
      + '<div style="color:var(--fg3);font-size:11px">' + esc(g.tier) + ' &middot; ' + g.tools.length + ' tools</div>';
  }

  TIERS.forEach(function(tier){
    var sec = tierSectors[tier];
    var tp = polar(R_TIER, sec.mid);
    // Root -> tier edge: a1 = a2 = the tier's angle, so the cubic degenerates
    // to a straight radial segment (draft v5 §1.5).
    var eTier = el('path', { d: treeEdgePath(0, sec.mid, R_TIER, sec.mid), fill: 'none', 'class': 'radial-edge-tier', 'data-tier': tier });
    gEdges.appendChild(eTier);
    edgeEls.push({ el: eTier, childKind: 'tier', childKey: tier, childTier: tier });
    var tc = el('circle', { cx: tp[0], cy: tp[1], r: R_TIER_NODE, fill: TIER_COL[tier], 'class': 'radial-tier', 'data-tier': tier });
    tc.addEventListener('click', function(){ onTierClick(tier); });
    tc.addEventListener('mouseenter', function(ev){ showTip(tierTooltip(tier), ev.clientX, ev.clientY); });
    tc.addEventListener('mousemove', function(ev){ showTip(tierTooltip(tier), ev.clientX, ev.clientY); });
    tc.addEventListener('mouseleave', function(){ hideTip(); });
    gNodes.appendChild(tc);
    tierNodeEls[tier] = tc;
    var tt1 = el('text', { x: tp[0], y: tp[1] - 2, 'text-anchor': 'middle', 'font-size': 15, 'font-weight': 700, fill: '#0f1117', style: 'pointer-events:none' });
    tt1.textContent = tier;
    var tt2 = el('text', { x: tp[0], y: tp[1] + 16, 'text-anchor': 'middle', 'font-size': 11, fill: '#0f1117', style: 'pointer-events:none' });
    tt2.textContent = byTier[tier].length + ' tool';
    gLabels.appendChild(tt1);
    gLabels.appendChild(tt2);
    groups.forEach(function(g){
      if (g.tier !== tier) return;
      var gp = polar(R_GROUP, g.mid);
      var eTG = el('path', { d: treeEdgePath(R_TIER, sec.mid, R_GROUP, g.mid), fill: 'none', 'class': 'radial-edge', 'data-tier': tier });
      gEdges.appendChild(eTG);
      edgeEls.push({ el: eTG, childKind: 'group', childKey: g.key, childTier: tier });
      var gn = el('circle', { cx: gp[0], cy: gp[1], r: R_GROUP_NODE, fill: TIER_COL[tier], 'class': 'radial-group', 'data-group': g.key });
      gNodes.appendChild(gn);
      groupNodeEls[g.key] = gn;
      var gh = el('circle', { cx: gp[0], cy: gp[1], r: 10, fill: '#ffffff', 'fill-opacity': '0', 'class': 'radial-group-hit', 'data-group': g.key });
      (function(key){
        gh.addEventListener('mouseenter', function(ev){ showTip(groupTooltip(key), ev.clientX, ev.clientY); });
        gh.addEventListener('mousemove', function(ev){ showTip(groupTooltip(key), ev.clientX, ev.clientY); });
        gh.addEventListener('mouseleave', function(){ hideTip(); });
        gh.addEventListener('click', function(){ onGroupClick(key); });
      })(g.key);
      gNodes.appendChild(gh);
      var list = g.tools;
      var m = list.length;
      for (var i = 0; i < m; i++){
        var ang = g.a0 + (i + 0.5) * (g.a1 - g.a0) / m;
        var p = polar(R_TOOL, ang);
        var e = el('path', { d: treeEdgePath(R_GROUP, g.mid, R_TOOL, ang), fill: 'none', 'class': 'radial-edge', 'data-tier': tier });
        gEdges.appendChild(e);
        edgeEls.push({ el: e, childKind: 'tool', childKey: list[i].n, childTier: tier });
        var dot = el('circle', { cx: p[0], cy: p[1], r: R_TOOL_NODE, fill: TIER_COL[tier], 'class': 'radial-tool', 'data-tier': tier });
        if (list[i].miss) {
          dot.setAttribute('stroke', '#fff');
          dot.setAttribute('stroke-width', '2');
          dot.setAttribute('stroke-dasharray', '3,2');
          var ttl = el('title', {});
          ttl.textContent = '⚠ MISSING from the original file — ' + list[i].n;
          dot.appendChild(ttl);
        }
        var hit = el('circle', { cx: p[0], cy: p[1], r: 11, fill: '#ffffff', 'fill-opacity': '0', 'class': 'radial-hit', 'data-tier': tier });
        (function(t, h){
          h.addEventListener('mouseenter', function(ev){ focusTool(t.n); showTip(tipHTML(t), ev.clientX, ev.clientY); });
          h.addEventListener('mousemove', function(ev){ if (hoverTool === t.n) showTip(tipHTML(t), ev.clientX, ev.clientY); });
          h.addEventListener('mouseleave', function(){ unfocusTool(); });
          h.addEventListener('click', function(ev){ onLeafClick(t.n, ev); });
        })(list[i], hit);
        gNodes.appendChild(dot);
        gNodes.appendChild(hit);
        radialPos[list[i].n] = { x: p[0], y: p[1], ang: ang };
        toolNodes.push({ el: dot, tier: tier, tool: list[i] });
      }
    });
  });
  // ZOOM — one <g id="radial-zoom"> transform (translate + scale), vanilla JS.
  // Wheel zooms about the cursor, drag pans, double-click on the background
  // (or the 1:1 button) resets. Overview tool-name labels (12px, built once
  // in #radial-zoom-labels) appear at k >= 1.8. Everything interactive lives
  // inside the transformed group, so hit areas, tooltips (clientX/Y), focus
  // ring and pin scale with it. Switching views (overview <-> group) resets
  // zoom to identity; Reset (clearHighlight path) also clears it.
  var zoomK = 1, zoomTX = 0, zoomTY = 0;
  var ZOOM_MIN = 0.5, ZOOM_MAX = 8, ZOOM_LABEL_K = 1.8;
  var zoomLabelEls = {}; // tool name -> <text> in #radial-zoom-labels
  var zoomLabelsShown = false; // zoom-label layer currently shown (show-transition)
  var suppressClickUntil = 0; // a >4px drag suppresses the following click
  // Clamp one label's bbox inside the viewBox with an 8px margin (F2: the
  // clamp must run for every label, not only for overlapping pairs — a
  // non-overlapping label at the east/west extreme would otherwise be cut
  // off by the svg viewport).
  function clampLabelToViewBox(l){
    var b = l.getBBox();
    var x = parseFloat(l.getAttribute('x'));
    var y = parseFloat(l.getAttribute('y'));
    if (b.x < 8) x += 8 - b.x;
    if (b.x + b.width > W - 8) x -= (b.x + b.width) - (W - 8);
    if (b.y < 8) y += 8 - b.y;
    if (b.y + b.height > H - 8) y -= (b.y + b.height) - (H - 8);
    l.setAttribute('x', x);
    l.setAttribute('y', y);
  }
  function applyZoom(){
    gZoom.setAttribute('transform', 'translate(' + zoomTX + ' ' + zoomTY + ') scale(' + zoomK + ')');
    var show = (zoomK >= ZOOM_LABEL_K && !gvActive);
    // Show the layer BEFORE measuring: getBBox() returns zeros for
    // display:none content, so the clamp/sync below must run while visible.
    gZoomLabels.style.display = show ? '' : 'none';
    if (show && !zoomLabelsShown){
      // Show-transition: clamp every zoom label into the viewBox once (the
      // layer is visible now, so getBBox is real) and sync each label's
      // opacity with its dot (F2/F3 — the wheel path calls no applyVisibility).
      zoomLabelsShown = true;
      toolNodes.forEach(function(o){
        var lab = zoomLabelEls[o.tool.n];
        if (!lab) return;
        clampLabelToViewBox(lab);
        lab.setAttribute('opacity', o.el.getAttribute('opacity') || '1');
      });
    } else if (!show){
      zoomLabelsShown = false;
    }
  }
  function resetZoom(){ zoomK = 1; zoomTX = 0; zoomTY = 0; applyZoom(); }
  function zoomAt(px, py, factor){
    var nk = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, zoomK * factor));
    if (nk === zoomK) return;
    var f = nk / zoomK;
    zoomTX = px - (px - zoomTX) * f;
    zoomTY = py - (py - zoomTY) * f;
    zoomK = nk;
    applyZoom();
  }
  function zoomAboutCenter(factor){ zoomAt((CX - zoomTX) / zoomK, (CY - zoomTY) / zoomK, factor); }
  function svgPoint(ev){
    // Map client -> viewBox through the real screen CTM so preserveAspectRatio
    // letterbox AND CSS scaling are both accounted for. The old manual
    // (client - rect) * W / rect.width mapping assumed the rendered box shares the
    // viewBox aspect; when the svg letterboxes vertically it drifted the y anchor
    // (measured 18.8px on a 1.25x button zoom). getScreenCTM is the same idiom the
    // wire geometry uses. Hidden tab: no CTM -> fall back to the centre.
    var m = svg.getScreenCTM();
    if (!m) return [CX, CY];
    var p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m.inverse());
    return [p.x, p.y];
  }
  // Build the overview tool-name labels once (12px, beside each dot, anchored
  // by half like the group view). Hidden until k >= 1.8; dimmed with their
  // tool by applyVisibility().
  function buildZoomLabels(){
    gZoomLabels.innerHTML = '';
    zoomLabelEls = {};
    toolNodes.forEach(function(o){
      var p = radialPos[o.tool.n];
      if (!p) return;
      var right = p.x >= CX;
      var t = el('text', { x: p.x + (right ? 8 : -8), y: p.y + 4, 'text-anchor': right ? 'start' : 'end', 'font-size': 12, style: 'pointer-events:none;fill:var(--fg)', 'data-tool': o.tool.n });
      t.textContent = o.tool.n;
      gZoomLabels.appendChild(t);
      zoomLabelEls[o.tool.n] = t;
    });
  }
  // A2: remember the last pointer over the overview so +/- anchor the dot under it;
  // keyboard-only activation falls back to the true viewport centre.
  var lastOverviewPointer = null;
  svg.addEventListener('mousemove', function(ev){ lastOverviewPointer = svgPoint(ev); });
  svg.addEventListener('wheel', function(ev){
    ev.preventDefault();
    var p = svgPoint(ev);
    zoomAt(p[0], p[1], Math.exp(-ev.deltaY * 0.0015));
  }, { passive: false });
  var panDrag = null; // {x, y, tx, ty, cx, cy, moved}; x/y viewBox units, cx/cy client px
  svg.addEventListener('mousedown', function(ev){
    if (ev.button !== 0) return;
    ev.preventDefault();
    var p = svgPoint(ev);
    panDrag = { x: p[0], y: p[1], tx: zoomTX, ty: zoomTY, cx: ev.clientX, cy: ev.clientY, moved: false };
  });
  window.addEventListener('mousemove', function(ev){
    if (!panDrag) return;
    // The >4px drag threshold is measured in screen px (F6), not viewBox
    // units — the svg can render narrower than its 900-unit viewBox.
    if (Math.abs(ev.clientX - panDrag.cx) > 4 || Math.abs(ev.clientY - panDrag.cy) > 4) panDrag.moved = true;
    if (panDrag.moved){
      var p = svgPoint(ev);
      zoomTX = panDrag.tx + (p[0] - panDrag.x);
      zoomTY = panDrag.ty + (p[1] - panDrag.y);
      applyZoom();
    }
  });
  window.addEventListener('mouseup', function(){
    if (panDrag && panDrag.moved) suppressClickUntil = Date.now() + 100;
    panDrag = null;
  });
  // A drag is not a click: swallow the click that follows a >4px drag.
  // Capture phase on the svg fires before the target's own click handler.
  svg.addEventListener('click', function(ev){
    if (Date.now() < suppressClickUntil){ ev.stopPropagation(); ev.preventDefault(); }
  }, true);
  svg.addEventListener('dblclick', function(ev){
    if (ev.target === svg) resetZoom();
  });
  (function wireZoomButtons(){
    var zi = document.getElementById('sb-zoom-in');
    var zo = document.getElementById('sb-zoom-out');
    var zr = document.getElementById('sb-zoom-reset');
    if (zi) zi.addEventListener('click', function(){ if (lastOverviewPointer) zoomAt(lastOverviewPointer[0], lastOverviewPointer[1], 1.25); else zoomAboutCenter(1.25); });
    if (zo) zo.addEventListener('click', function(){ if (lastOverviewPointer) zoomAt(lastOverviewPointer[0], lastOverviewPointer[1], 1 / 1.25); else zoomAboutCenter(1 / 1.25); });
    if (zr) zr.addEventListener('click', function(){ resetZoom(); });
  })();
  buildZoomLabels();
  applyZoom();
  // Focus state (single focus, last action wins — O4a): three persistent modes
  // (tier / group / pin) are mutually exclusive; hover is transient.
  var activeTier = null;
  var activeGroup = null; // group key (tier|fg) or null

  function focusScopeSet(){
    if (activeGroup){
      var s = {};
      groupByKey(activeGroup).tools.forEach(function(t){ s[t.n] = 1; });
      return s;
    }
    if (activeTier){
      var s2 = {};
      byTier[activeTier].forEach(function(t){ s2[t.n] = 1; });
      return s2;
    }
    if (pinnedTool){
      var s3 = {};
      s3[pinnedTool] = 1;
      chainOf(pinnedTool).out.forEach(function(o){ s3[o.to] = 1; });
      chainOf(pinnedTool).inn.forEach(function(o){ s3[o.from] = 1; });
      return s3;
    }
    return null;
  }
  function searchQ(){ return searchEl ? searchEl.value.trim().toLowerCase() : ''; }
  function matchesSearch(name){
    var q = searchQ();
    if (!q) return true;
    return name.toLowerCase().indexOf(q) !== -1;
  }
  function inPillSet(name){ return !pillFilter || !!pillFilter[name]; }
  function inScopeOrNoFocus(name){
    var scope = focusScopeSet();
    return !scope || !!scope[name];
  }
  // Conditions 1+2 of the composed function (search AND pill AND scope).
  // "Visible before the hover" (O7e) means passing 1+2, ignoring condition 3.
  function passesConditions12(name){
    return matchesSearch(name) && inPillSet(name) && inScopeOrNoFocus(name);
  }
  function visibleBeforeHover(name){ return passesConditions12(name); }
  function leafNeighbors(name){
    var m = {};
    chainOf(name).out.forEach(function(o){ m[o.to] = 1; });
    chainOf(name).inn.forEach(function(o){ m[o.from] = 1; });
    return m;
  }

  // Composed visibility function (O6b/O7, with the O8 fixes) — the single
  // source of truth for opacity, with one explicit exception: a wire hover
  // lights both endpoints of the hovered wire even when the function would
  // dim one of them, gated on >=1 endpoint visible before the hover (O8(5)).
  // A hover the hover rules classify as tooltip-only — a leaf outside the
  // focused scope, a leaf not matching the active search, or a wire with no
  // endpoint visible before the hover — is treated as no hover for condition
  // 3 (O8(1)). During a non-tooltip-only hover the hover bullets define the
  // narrowed lit-set for ALL elements (R2 Finding 1, Option A): nodes, tree
  // edges and non-hovered wires follow the narrowed set, not the function —
  // so a non-hovered wire sharing a lit endpoint still dims during a wire
  // hover. When no hover is active the function rules govern (nodes iff >=1
  // of their tools is visible; tree edges follow their child; chain wires
  // iff >=1 endpoint is visible).
  function applyVisibility(){
    var q = searchQ();
    var scope = focusScopeSet();
    var inScope = function(name){ return !scope || !!scope[name]; };
    var pass12 = function(name){
      if (q && name.toLowerCase().indexOf(q) === -1) return false;
      if (pillFilter && !pillFilter[name]) return false;
      return inScope(name);
    };
    var hover = null;
    if (hoverWire){
      if (pass12(hoverWire.f) || pass12(hoverWire.t)) hover = { kind: 'wire', f: hoverWire.f, t: hoverWire.t };
    } else if (hoverTool){
      if (pass12(hoverTool)) hover = { kind: 'leaf', name: hoverTool };
    }
    var neigh = (hover && hover.kind === 'leaf') ? leafNeighbors(hover.name) : null;
    var lit = {};
    toolNodes.forEach(function(o){
      var name = o.tool.n, ok;
      if (hover && hover.kind === 'wire' && (name === hover.f || name === hover.t)) ok = true;
      else {
        ok = pass12(name);
        if (ok && hover){
          if (hover.kind === 'leaf'){ if (name !== hover.name && !neigh[name]) ok = false; }
          else ok = false;
        }
      }
      o.el.setAttribute('opacity', ok ? '1' : '0.12');
      if (typeof zoomLabelEls !== 'undefined' && zoomLabelEls[name]) zoomLabelEls[name].setAttribute('opacity', ok ? '1' : '0.12');
      if (ok) lit[name] = 1;
    });
    var groupLit = {}, tierLit = {};
    groups.forEach(function(g){
      var on = false;
      if (!hover) g.tools.forEach(function(t){ if (lit[t.n]) on = true; });
      groupLit[g.key] = on;
      groupNodeEls[g.key].setAttribute('opacity', on ? '1' : '0.35');
    });
    TIERS.forEach(function(t){
      var on = false;
      if (!hover) byTier[t].forEach(function(x){ if (lit[x.n]) on = true; });
      tierLit[t] = on;
      tierNodeEls[t].setAttribute('opacity', on ? '1' : '0.35');
    });
    edgeEls.forEach(function(e){
      var on = false;
      if (!hover){
        if (e.childKind === 'tier') on = tierLit[e.childTier];
        else if (e.childKind === 'group') on = groupLit[e.childKey];
        else on = !!lit[e.childKey];
      }
      e.el.setAttribute('opacity', on ? '1' : (hover ? '0.08' : '0.12'));
    });
    wireEls.forEach(function(w){
      var on, sw = w.baseW;
      if (hover && hover.kind === 'leaf'){
        on = (w.f === hover.name || w.t === hover.name);
        if (on) sw = '3';
        w.el.setAttribute('opacity', on ? '1' : '0.06');
      } else if (hover && hover.kind === 'wire'){
        on = (w.f === hover.f && w.t === hover.t);
        if (on) sw = '3';
        w.el.setAttribute('opacity', on ? '1' : '0.06');
      } else {
        on = !!(lit[w.f] || lit[w.t]);
        w.el.setAttribute('opacity', on ? '1' : '0.25');
      }
      w.el.setAttribute('stroke-width', sw);
    });
    syncDashStages();
    syncDashPills();
  }

  function syncCrumb(){
    if (!crumbEl) return;
    if (activeGroup){
      var g = groupByKey(activeGroup);
      crumbEl.innerHTML = '<b>' + esc(groupLabel(activeGroup)) + '</b> &middot; ' + g.tools.length + ' tool';
    } else if (activeTier){
      crumbEl.innerHTML = '<b>' + esc(TIER_LABEL[activeTier]) + '</b> &middot; ' + byTier[activeTier].length + ' tool';
    } else if (pinnedTool){
      var ch = chainOf(pinnedTool);
      crumbEl.innerHTML = '<b>' + esc(pinnedTool) + '</b> &middot; ' + (ch.out.length + ch.inn.length) + ' chain link';
    } else if (typeof activePillKey !== 'undefined' && activePillKey){
      crumbEl.innerHTML = '<b>' + esc(activePillLabel || activePillKey) + '</b>';
    } else crumbEl.innerHTML = '';
  }
  function highlightTier(tier){
    exitGroupView();
    activeTier = tier;
    activeGroup = null;
    pinnedTool = null;
    hoverTool = null;
    hoverWire = null;
    hideTip();
    if (typeof focusRing !== 'undefined' && focusRing) focusRing.style.display = 'none';
    applyVisibility();
    syncCrumb();
  }
  function highlightGroup(key){
    activeGroup = key;
    activeTier = null;
    pinnedTool = null;
    hoverTool = null;
    hoverWire = null;
    hideTip();
    if (typeof focusRing !== 'undefined' && focusRing) focusRing.style.display = 'none';
    applyVisibility();
    syncCrumb();
  }
  function clearHighlight(){
    exitGroupView();
    resetZoom();
    activeTier = null;
    activeGroup = null;
    pinnedTool = null;
    hoverTool = null;
    hoverWire = null;
    hideTip();
    if (typeof focusRing !== 'undefined' && focusRing) focusRing.style.display = 'none';
    applyVisibility();
    syncCrumb();
  }
  function onTierClick(tier){
    // Clicking the already-focused tier always toggles off (O7i); the panels
    // persist (O6d). A tier click writes the tier board, NOT #sb-detail (O7g).
    if (activeTier === tier){ clearHighlight(); return; }
    highlightTier(tier);
    renderTierBoard(tier);
  }
  function onGroupClick(key){
    if (activeGroup === key){
      // Content-based recovery (O7i): reload the list unless it is already shown.
      if (!(detailState.kind === 'group' && detailState.key === key)){ setDetailGroup(key); return; }
      clearHighlight(); // toggle off; panels persist (O6d)
      return;
    }
    highlightGroup(key);
    enterGroupView(key);
    setDetailGroup(key);
  }
  function onLeafClick(name, ev){
    if (pinnedTool === name){
      pinnedTool = null; // unpin: panels + query persist (O7k)
      applyVisibility();
      syncCrumb();
      return;
    }
    pinnedTool = name;
    activeTier = null;
    activeGroup = null;
    hoverTool = null;
    hoverWire = null;
    var t = toolByName(name);
    if (t) setDetailTool(t, 'pin');
    applyVisibility();
    moveRingTo(name);
    if (t && ev) showTip(tipHTML(t), ev.clientX, ev.clientY);
    syncCrumb();
  }
  // Search — composed visibility + zero-match clause (O6c/O7g/O7h, O8(3)(4)(7)).
  // Search dims (does not hide). Activating a focus does not clear the query;
  // typing a query does not clear the focus. The zero-match message shows only
  // if the search is the most recent #sb-detail-writing action.
  if (searchEl){
    searchEl.addEventListener('input', function(){
      var q = searchEl.value.trim();
      renderSearchMatches(q);
      lastSearchAt = ++writeCounter;
      if (!q){
        // Clear-box restores (O8(3)): back to the focus-determined content
        // (group summary / pinned detail / pre-search content) — never wipes.
        restoreDetailAfterSearch();
        preSearchDetail = null;
        preSearchDetailState = null;
        applyVisibility();
        if (gvActive) gvApplyVisibility();
        return;
      }
      // Retain the pre-search panel content once (O8(6)); consecutive
      // zero-match queries do not overwrite it (O8(7)).
      if (preSearchDetail === null){
        preSearchDetail = detailEl ? detailEl.innerHTML : '';
        preSearchDetailState = detailState;
      }
      var scope = focusScopeSet();
      var matches = toolNodes.filter(function(o){
        if (!matchesSearch(o.tool.n) || !inPillSet(o.tool.n)) return false;
        if (scope && !scope[o.tool.n]) return false;
        return true;
      });
      if (matches.length === 0){
        if (lastPanelWriteAt <= lastSearchAt){
          if (detailEl) detailEl.innerHTML = '<div class="detail-panel" style="margin-top:0"><div class="detail-name">No tools match \'' + esc(q) + '\'</div><div class="detail-row"><span class="detail-label">Gợi ý</span><span class="detail-value">Xóa ô tìm kiếm để quay lại nội dung trước đó, hoặc bấm “Thu về gốc”.</span></div></div>';
          detailState = { kind: 'zero', query: q };
          lastDetailWriter = 'search';
          lastPanelWriteAt = lastSearchAt;
        }
      } else {
        // Query edit from zero-match to matching (O8(4)): the stale message
        // clears and the panel restores to the focus-determined content.
        if (detailState.kind === 'zero') restoreDetailAfterSearch();
        if (matches.length === 1){ setDetailTool(matches[0].tool, 'search'); }
      }
      applyVisibility();
      if (gvActive) gvApplyVisibility();
    });
  }
  function restoreDetailAfterSearch(){
    // Per focus mode (O7g + O8(3)): group -> group summary; pin -> pinned
    // detail; tier or no focus -> pre-search content (retention mechanism).
    // Fall-through restores ONLY search-written content (F-1): a tool detail
    // written by a click during the search is click-written content — v5 §1.8
    // says clearing the search box does not touch it. The group/pin branches
    // above are focus-determined (O7g) and are untouched by this gate.
    if (activeGroup){ setDetailGroup(activeGroup); return; }
    if (pinnedTool){
      var t = toolByName(pinnedTool);
      if (t){ setDetailTool(t, 'search'); return; }
    }
    if (lastDetailWriter === 'search' || detailState.kind === 'zero'){
      if (detailEl && preSearchDetail !== null) detailEl.innerHTML = preSearchDetail;
      if (preSearchDetail !== null){
        detailState = preSearchDetailState || { kind: 'empty' };
        lastDetailWriter = 'search';
      }
    }
  }
  if (resetBtn){
    resetBtn.addEventListener('click', function(){
      if (searchEl) searchEl.value = '';
      renderSearchMatches('');
      preSearchDetail = null;
      preSearchDetailState = null;
      pillFilter = null; activePillKey = null; activePillLabel = ''; // Reset clears the pill too (F-4)
      clearHighlight();
      clearTierBoard();
      setDetailRoot();
    });
  }

  // CHAIN WIRES — curved arcs between tool nodes (SB_LINKS + SB_AUTO).
  // Same geometry as the old sunburst: quadratic curve pulled 65% toward the center.
  function radialArcPath(f, t){
    var a = radialPos[f], b = radialPos[t];
    if (!a || !b) return null;
    var mx = (a.x + b.x) / 2 * 0.35 + CX * 0.65, my = (a.y + b.y) / 2 * 0.35 + CY * 0.65;
    return 'M ' + a.x.toFixed(1) + ' ' + a.y.toFixed(1) + ' Q ' + mx.toFixed(1) + ' ' + my.toFixed(1) + ' ' + b.x.toFixed(1) + ' ' + b.y.toFixed(1);
  }
  function relationArrow(kind){ return kind === 'alias-shared-handler' ? ' ↔ ' : ' → '; }
  function wireVisible(kind){
    var id = kind === 'auto' ? 'sb-toggle-auto'
      : kind === 'delegates-to' ? 'sb-toggle-delegates'
       : kind === 'recommends' ? 'sb-toggle-recommends'
       : kind === 'alias-dispatch' ? 'sb-toggle-alias-dispatch' : 'sb-toggle-alias-shared';
    var cb = document.getElementById(id);
    if (!cb) return kind === 'auto';
    return cb.checked;
  }
  function drawWires(){
    gWires.innerHTML = '';
    wireEls = [];
    var showAuto = wireVisible('auto');
    SB_LINKS.forEach(function(l){
      if (!wireVisible(l.k)) return;
      var d = radialArcPath(l.f, l.t);
      if (!d) return;
      var style = '', marker = '';
      if (l.k === 'delegates-to') { style = 'stroke:#58a6ff;stroke-width:1.7;stroke-linecap:round;opacity:.9'; marker = ' marker-end="url(#rw-arrow)"'; }
      else if (l.k === 'recommends') { style = 'stroke:#bc8cff;stroke-width:1;stroke-dasharray:4,3;opacity:.85'; marker = 'directed'; }
      else { style = 'stroke:#3fb950;stroke-width:1.1;opacity:.85'; }
      var baseW = (l.k === 'delegates-to') ? '1.5' : '1';
      var label = l.k + ': ' + l.f + relationArrow(l.k) + l.t + (l.c ? ' (' + l.c + ')' : '') + (l.q ? ' — ' + l.q : '');
      var p = el('path', { d: d, fill: 'none', style: style, 'data-f': l.f, 'data-t': l.t, 'data-kind': l.k });
      if (marker) p.setAttribute('marker-end', 'url(#rw-arrow)');
      var ttl = el('title', {});
      ttl.textContent = label;
      p.appendChild(ttl);
      wireEls.push({el: p, f: l.f, t: l.t, kind: l.k, baseW: baseW});
      (function(pp, ff, tt, kk, lab){
        pp.addEventListener('mouseenter', function(ev){ focusWire(ff, tt); showTip('<div style="font-size:13px;font-weight:700">' + esc(ff) + relationArrow(kk) + esc(tt) + '</div><div style="color:var(--fg3);font-size:11px">' + esc(kk) + '</div><div style="margin-top:4px">' + esc(lab) + '</div>', ev.clientX, ev.clientY); });
        pp.addEventListener('mousemove', function(ev){ showTip('<div style="font-size:13px;font-weight:700">' + esc(ff) + relationArrow(kk) + esc(tt) + '</div><div style="color:var(--fg3);font-size:11px">' + esc(kk) + '</div><div style="margin-top:4px">' + esc(lab) + '</div>', ev.clientX, ev.clientY); });
        pp.addEventListener('mouseleave', function(){ unfocusWire(); });
      })(p, l.f, l.t, l.k, label);
      gWires.appendChild(p);
    });
    if (showAuto){
      SB_AUTO.forEach(function(a){
        var d = radialArcPath(a.f, a.t);
        if (!d) return;
        var label = 'Auto-Chain proposal (unmeasured) — nhóm ' + a.r + ': ' + a.f + ' → ' + a.t + (a.c ? ' (' + a.c + ')' : '');
        var p = el('path', { d: d, fill: 'none', style: 'stroke:#fbbf24;stroke-width:1.5;stroke-dasharray:6,4', 'data-f': a.f, 'data-t': a.t, 'data-kind': 'auto-chain' });
        p.setAttribute('marker-end', 'url(#rw-arrow-auto)');
        var ttl = el('title', {});
        ttl.textContent = label;
        p.appendChild(ttl);
        wireEls.push({el: p, f: a.f, t: a.t, kind: 'auto-chain', baseW: '1.5'});
        (function(pp, ff, tt, lab){
          pp.addEventListener('mouseenter', function(ev){ focusWire(ff, tt); showTip('<div style="font-size:13px;font-weight:700">' + esc(ff) + ' &rarr; ' + esc(tt) + '</div><div style="color:var(--fg3);font-size:11px">auto-chain</div><div style="margin-top:4px">' + esc(lab) + '</div>', ev.clientX, ev.clientY); });
          pp.addEventListener('mousemove', function(ev){ showTip('<div style="font-size:13px;font-weight:700">' + esc(ff) + ' &rarr; ' + esc(tt) + '</div><div style="color:var(--fg3);font-size:11px">auto-chain</div><div style="margin-top:4px">' + esc(lab) + '</div>', ev.clientX, ev.clientY); });
          pp.addEventListener('mouseleave', function(){ unfocusWire(); });
        })(p, a.f, a.t, label);
        gWires.appendChild(p);
      });
    }
  }
  ['sb-toggle-auto', 'sb-toggle-delegates', 'sb-toggle-alias-dispatch', 'sb-toggle-alias-shared', 'sb-toggle-recommends'].forEach(function(id){
    var cb = document.getElementById(id);
    if (cb) cb.addEventListener('change', function(){ drawWires(); if (gvActive) gvApplyVisibility(); });
  });


  // TIER BOARD — human-readable cards for one tier's tools.
  // The radial overview cannot show 260 readable labels (T1 packs 120 dots into a
  // 166-degree arc: ~7.6px per dot, labels ~170px wide). So the board is the read
  // surface: click a tier and its tools appear here as large cards with the full
  // use-case proposition + chain links, while the radial keeps the model + wires.
  var tierboardEl = document.getElementById('sb-tierboard');
  function renderTierBoard(tier){
    if (!tierboardEl) return;
    var list = byTier[tier].slice().sort(function(a, b){ return a.n < b.n ? -1 : 1; });
    var cards = list.map(function(t){
      var miss = t.miss ? ' <span style="color:var(--amber)">⚠ MISSING</span>' : '';
      var uc = t.uc ? esc(t.uc) : '<i>Không có use case ghi trong matrix.</i>';
       var meta = esc(t.t) + ' · ' + esc(t.cat || 'Chưa rõ');
      return '<div class="gap-card info" style="text-align:left;cursor:pointer" data-tool="' + esc(t.n) + '">'
        + '<div class="gap-title" style="font-size:0.95rem">' + esc(t.n) + miss + '</div>'
        + '<div style="font-size:0.82rem;line-height:1.45;margin:6px 0">' + uc + '</div>'
        + '<div style="font-size:0.75rem;color:var(--fg3)">' + meta + ' — bấm để xem effect + citations đầy đủ</div>'
        + '</div>';
    }).join('');
    tierboardEl.innerHTML = '<div style="margin-bottom:8px;font-size:0.9rem"><b>' + esc(TIER_LABEL[tier]) + '</b> · ' + list.length + ' tool — bấm một card để xem chi tiết đầy đủ ở panel phía trên, bấm “Thu về gốc” để đóng bảng.</div>'
      + relationPanel(list.map(t => t.n))
      + '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr));gap:10px;max-height:640px;overflow-y:auto;padding-right:4px">' + cards + '</div>';
  }
  function clearTierBoard(){
    if (tierboardEl) tierboardEl.innerHTML = '';
  }
  if (tierboardEl){
    tierboardEl.addEventListener('click', function(ev){
      var card = ev.target.closest ? ev.target.closest('[data-tool]') : null;
      if (!card) return;
      var name = card.getAttribute('data-tool');
      var t = toolByName(name);
      if (t) setDetailTool(t, 'card'); // card click loads detail WITHOUT pin (O6e/O7j)
    });
  }

  // GROUP DETAIL — summary + tool list for one group (draft v5 §1.8).
  // Clicking a tool in the list loads its detail WITHOUT pinning (O5b);
  // the group focus persists.
  function setDetailGroup(key){
    if (!detailEl) return;
    var g = groupByKey(key);
    if (!g) return;
    var rows = g.tools.map(function(t){
      return '<div class="detail-row" data-group-tool="' + esc(t.n) + '" style="cursor:pointer"><span class="detail-label">' + esc(t.t) + '</span><span class="detail-value">' + esc(t.n) + ' &middot; ' + esc(t.cat || '') + '</span></div>';
    }).join('');
    detailEl.innerHTML = '<div class="detail-panel" style="margin-top:0"><div class="detail-name">' + esc(groupLabel(key)) + ' &middot; ' + esc(TIER_LABEL[g.tier]) + '</div>'
      + '<div class="detail-row"><span class="detail-label">Số tool</span><span class="detail-value">' + g.tools.length + ' tool</span></div>'
      + '<div class="detail-row"><span class="detail-label">Nhóm</span><span class="detail-value">' + esc(key) + '</span></div>'
      + relationPanel(g.tools.map(t => t.n))
      + '<div style="margin-top:8px">' + rows + '</div></div>';
    notePanelWrite('group', { kind: 'group', key: key });
  }
  if (detailEl){
    detailEl.addEventListener('click', function(ev){
      var row = ev.target.closest ? ev.target.closest('[data-group-tool]') : null;
      if (!row) return;
      var t = toolByName(row.getAttribute('data-group-tool'));
      if (t) setDetailTool(t, 'list');
    });
  }

  // Legend + notes (radial tree).
  var legendEl = document.getElementById('sb-legend');
  if (legendEl){
    legendEl.innerHTML =
      '<div><b>Tiers</b><br><span style="color:#4ade80">■</span> T1 Read/Inspect (120) · <span style="color:#fbbf24">■</span> T2 Write/Mutate (56) · <span style="color:#a78bfa">■</span> T3 Audit/Session (38) · <span style="color:#22d3ee">■</span> T4 Desktop/UI (46)</div>'
      + '<div><b>Dạng Radial Tree</b><br>Tâm ToolA → 4 tier (vòng 1) → 37 nhóm (vòng 2) → 260 tool (vòng 3). Góc mỗi tier tỉ lệ với số tool của tier đó; góc mỗi nhóm tỉ lệ với số tool của nhóm (tối thiểu 4 tool, MIN_LEAVES=4). Mỗi tool nối về nhóm của nó bằng một cạnh cong.</div>'
      + '<div><b>Nhóm (group)</b><br>Bấm một node nhóm để mở group view — chỉ tool của nhóm đó, node to + tên đọc được — đồng thời làm nổi bật toàn bộ tool của nhóm đó và xem danh sách nhóm ở panel chi tiết. Rê chuột lên node nhóm để xem tên nhóm + số tool. Bấm “← Overview” (hoặc bấm lại nhóm đang focus) để quay về toàn cảnh.</div>'
      + '<div><b>Liên kết đã kiểm kê</b><br>30 cross-link + 16 auto-chain ghi nhận quan hệ giữa một số tool, không suy ra từ lượt gọi: delegates-to (solid + arrow) · alias (solid thin) · recommends (dotted) · auto-chain (dashed + arrow, mặc định BẬT). Bật/tắt từng loại bằng checkbox phía trên. Rê chuột lên một wire để xem quan hệ (từ → tới) và làm nổi bật 2 tool ở 2 đầu.</div>'
      + '<div><b>Đọc tool — bảng tier, group view, zoom (human read)</b><br>Node ngọn ở tổng quan quá nhỏ để đọc (mỗi tool chỉ 6.86–31.2px, trung bình 8.46px). <b>Bấm vào một tier</b> để mở bảng bên dưới: mỗi tool là một card lớn, đọc được tên + mệnh đề use case đầy đủ + effect + chain ra/vào. <b>Bấm một node nhóm</b> để mở group view: chỉ tool của nhóm đó, node to + tên đọc được + wire trong nhóm. <b>Lăn chuột để zoom</b>: tên tool hiện ra khi zoom ≥1.8×, kéo để di chuyển, bấm đúp (hoặc nút 1:1) để về tỉ lệ gốc. Bấm “Thu về gốc” để đóng bảng và về toàn cảnh.</div>'
      + '<div><b>Use case + quan hệ ở node ngọn</b><br>Rê chuột lên một node ngọn để xem ngay tại con trỏ: tên tool, mệnh đề use case đầy đủ, chain ra/vào — đồng thời các wire liên quan và tool kề sáng lên, phần còn lại mờ đi. Bấm vào node để ghim (giữ) highlight + xem đầy đủ ở panel chi tiết (effect, use case, citations, writes); bấm lại để bỏ ghim.</div>'
      + '<div><b>Tìm kiếm</b><br>Gõ tên tool vào ô tìm kiếm để làm nổi bật tool khớp (các node khác mờ đi). Bấm “Thu về gốc” để bỏ highlight và về lại toàn cảnh.</div>';
  }
  var notesEl = document.getElementById('sb-notes');
  if (notesEl){
    notesEl.innerHTML =
      '<b>Notes.</b> Radial tree thay dạng APEX từ 2026-11-17 (dashboard build, theo thiết kế draft v5: 302 node, 37 nhóm, R 155/250/350). Dữ liệu lấy trực tiếp từ TOOLS (260 tool, không còn cây SUNBURST riêng). '
      + 'Thông tin usage band / errata / deprecated / multi-chain vẫn có trong các tab Inventory, Chains, Gaps và trong panel chi tiết từng tool. '
      + 'Chain step order is file order, not measured call order.';
  }
  // (c) gộp filter vào 1 thanh điều khiển chung - legend/notes cập nhật theo trạng thái mới
  if (legendEl){
    legendEl.insertAdjacentHTML('beforeend', '<div><b>Thanh điều khiển chung</b><br>Một thanh duy nhất ở trên cùng: ô tìm kiếm, cụm nút Thu về gốc / zoom (+ - 1:1), 4 checkbox bật/tắt chain wire (auto-chain, delegates-to, alias, recommends) và hàng nút lọc tier T1-T4 (chấm màu + số tool). Các pill trạng thái tĩnh (documented, deprecated, MISSING, unused, Errata) ở cột phải lọc trên radial - không còn cụm filter rời rạc.</div><div><b>Cành cây tô theo tier</b><br>Mỗi cạnh của cây (root &rarr; tier, tier &rarr; nhóm, nhóm &rarr; tool) tô màu theo tier: <span style="color:#4ade80">T1</span>, <span style="color:#fbbf24">T2</span>, <span style="color:#a78bfa">T3</span>, <span style="color:#22d3ee">T4</span>; cạnh root &rarr; tier đậm hơn cạnh trong tier.</div>');
  }
  if (notesEl){
    notesEl.insertAdjacentHTML('beforeend', ' <span style="color:var(--fg3)">(cập nhật code-lane: cành cây tô màu theo tier qua [data-tier]; filter gộp vào 1 thanh điều khiển chung ở đầu panel Radial.)</span>');
  }

  // LEAF FOCUS — hover/click a leaf tool: show its use case + chain relations
  // in a cursor tooltip and highlight its wires + neighbours. The overview
  // radial cannot print 260 readable labels, so the tooltip + focus are the
  // read surface for a single leaf (the tier board stays the read surface
  // for a whole tier).
  var tipEl = null;
  var wireEls = []; // {el, f, t, kind, baseW}
  var pinnedTool = null;
  var hoverTool = null;
  var hoverWire = null; // {f, t} or null — wire currently hovered
  var focusRing = el('circle', { r: R_TOOL_NODE + 5, fill: 'none', stroke: '#ffffff', 'stroke-width': 2, style: 'display:none;pointer-events:none' });
  gNodes.appendChild(focusRing);
  var pillFilter = null; // map name->1, or null = no pill filter (dashboard)
  var preSearchDetail = null; // retained #sb-detail HTML (O8(6)); null = nothing retained
  var preSearchDetailState = null; // detailState saved alongside preSearchDetail
  var lastDetailWriter = null; // 'search'|'group'|'list'|'pin'|'card'|'reset'|null
  var detailState = { kind: 'root' }; // content now in #sb-detail (O7i content check)
  var writeCounter = 0, lastSearchAt = 0, lastPanelWriteAt = 0;
  function ensureTip(){
    if (tipEl) return tipEl;
    tipEl = document.createElement('div');
    tipEl.id = 'sb-leaf-tip';
    // Was a hard-coded #0f172a card, which turned the light palette's --fg (#1a1d2b)
    // into 1.07:1 text -- unreadable. Tokens, so the card follows the palette; the
    // shadow is lighter too because .5 black is a slab on white.
    tipEl.style.cssText = 'position:fixed;z-index:60;max-width:360px;pointer-events:none;display:none;background:var(--bg2);border:1px solid var(--ta-control-border, var(--border));border-radius:8px;padding:10px 12px;box-shadow:0 8px 24px rgba(0,0,0,.28);font-size:12px;line-height:1.5;color:var(--fg)';
    document.body.appendChild(tipEl);
    return tipEl;
  }
  function chainOf(name){
    var out = [], inn = [];
    SB_LINKS.forEach(function(l){
      if (l.f === name) out.push({to: l.t, kind: l.k});
      if (l.t === name) inn.push({from: l.f, kind: l.k});
    });
    SB_AUTO.forEach(function(a){
      if (a.f === name) out.push({to: a.t, kind: 'auto-chain'});
      if (a.t === name) inn.push({from: a.f, kind: 'auto-chain'});
    });
    return {out: out, inn: inn};
  }
  function tipHTML(t){
    var ch = chainOf(t.n);
    var rel = '';
    if (ch.out.length) rel += '<div style="margin-top:6px"><b>&rarr; Chain ra (' + ch.out.length + '):</b> ' + esc(ch.out.map(function(o){ return o.to; }).join(', ')) + '</div>';
    if (ch.inn.length) rel += '<div><b>&larr; Chain v&agrave;o (' + ch.inn.length + '):</b> ' + esc(ch.inn.map(function(o){ return o.from; }).join(', ')) + '</div>';
    if (!ch.out.length && !ch.inn.length) rel = '<div style="margin-top:6px;color:var(--fg3)">Kh&ocirc;ng c&oacute; chain link trong SB_LINKS/SB_AUTO.</div>';
    return '<div style="font-size:13px;font-weight:700">' + esc(t.n) + (t.miss ? ' <span style="color:var(--amber)">&#9888; MISSING</span>' : '') + '</div>'
      + '<div style="color:var(--fg3);font-size:11px">' + esc(t.t) + ' &middot; ' + esc(t.cat || '') + '</div>'
      + '<div style="margin-top:4px">' + esc(t.uc || 'Không có use case ghi trong matrix.') + '</div>' + rel;
  }
  function showTip(html, x, y){
    var tip = ensureTip();
    tip.innerHTML = html;
    tip.style.display = 'block';
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var lx = Math.min(x + 14, window.innerWidth - w - 8);
    var ly = Math.min(y + 14, window.innerHeight - h - 8);
    if (lx < 8) lx = 8;
    if (ly < 8) ly = 8;
    tip.style.left = lx + 'px';
    tip.style.top = ly + 'px';
  }
  function hideTip(){ if (tipEl) tipEl.style.display = 'none'; }
  function moveRingTo(name){
    var p = radialPos[name];
    if (!p){ focusRing.style.display = 'none'; return; }
    focusRing.setAttribute('cx', p.x);
    focusRing.setAttribute('cy', p.y);
    focusRing.style.display = '';
  }
  function restoreBase(){
    hoverTool = null;
    hoverWire = null;
    if (typeof focusRing !== 'undefined' && focusRing) focusRing.style.display = 'none';
    applyVisibility();
    syncCrumb();
  }
  function focusTool(name){
    hoverTool = name;
    applyVisibility();
    // A tooltip-only hover (leaf outside the focused scope, or not matching
    // the active search) changes no opacity — and shows no focus ring either.
    if (passesConditions12(name)){
      moveRingTo(name);
      var ch = chainOf(name);
      if (crumbEl) crumbEl.innerHTML = '<b>' + esc(name) + '</b> &middot; ' + (ch.out.length + ch.inn.length) + ' chain link';
    } else {
      syncCrumb();
    }
  }
  function unfocusTool(){
    hideTip();
    hoverTool = null;
    if (pinnedTool){
      applyVisibility();
      moveRingTo(pinnedTool);
      syncCrumb();
      return;
    }
    restoreBase();
  }
  function focusWire(f, t){
    hoverWire = { f: f, t: t };
    applyVisibility();
    // Wire hover with no endpoint visible before the hover is tooltip-only
    // (O8(5) gate): no opacity change, no ring.
    if (visibleBeforeHover(f) || visibleBeforeHover(t)){
      moveRingTo(f);
      if (crumbEl) crumbEl.innerHTML = '<b>' + esc(f) + ' &rarr; ' + esc(t) + '</b>';
    } else {
      syncCrumb();
    }
  }
  function unfocusWire(){
    hideTip();
    hoverWire = null;
    if (pinnedTool){
      applyVisibility();
      moveRingTo(pinnedTool);
      syncCrumb();
      return;
    }
    if (hoverTool){ focusTool(hoverTool); return; }
    restoreBase();
  }
  // GROUP VIEW — one group's tools as a big-node radial sub-tree (the video
  // look: big nodes with readable name labels). Clicking a group node enters;
  // the four overview layers hide (state preserved, never rebuilt) and
  // <g id="radial-groupview"> shows. Exit (back button, gv center click,
  // Reset, center-click, tier click) reverses. Zoom resets on view switch.
  // Label overlap after de-collision is counted, never claimed: see
  // gvDecollideLabels() + data-gv-overlap on the group-view layer.
  var gvActive = false;
  var gvKey = null;
  var gvTools = []; // {el, hit, label, tool, x, y}
  var gvWires = []; // {el, f, t, kind, baseW}
  var gvFocusRing = null;
  var gvHoverTool = null;
  var gvHoverWire = null; // {f, t} or null
  var gvOverlapCount = 0;
  var gvBarEl = document.getElementById('sb-gvbar');
  var gvTitleEl = document.getElementById('sb-gv-title');
  var gvMetaEl = document.getElementById('sb-gv-meta');
  var gvBackBtn = document.getElementById('sb-gv-back');
  if (gvBackBtn) gvBackBtn.addEventListener('click', function(){ clearHighlight(); });
  function gvRingRadii(m){
    if (m <= 10) return [290];
    if (m <= 25) return [200, 310];
    return [160, 250, 330];
  }
  function gvLayout(m){
    // Contiguous blocks per ring, balanced: 43 -> 15/14/14, 25 -> 13/12.
    var radii = gvRingRadii(m);
    var n = radii.length;
    var base = Math.floor(m / n), rem = m % n;
    var rings = [];
    for (var i = 0; i < n; i++) rings.push({ r: radii[i], count: base + (i < rem ? 1 : 0) });
    return rings;
  }
  // Same 65%-toward-center quadratic as radialArcPath, group-view coords.
  function gvArcPath(a, b){
    var mx = (a.x + b.x) / 2 * 0.35 + CX * 0.65, my = (a.y + b.y) / 2 * 0.35 + CY * 0.65;
    return 'M ' + a.x.toFixed(1) + ' ' + a.y.toFixed(1) + ' Q ' + mx.toFixed(1) + ' ' + my.toFixed(1) + ' ' + b.x.toFixed(1) + ' ' + b.y.toFixed(1);
  }
  function gvFind(name){
    for (var i = 0; i < gvTools.length; i++) if (gvTools[i].tool.n === name) return gvTools[i];
    return null;
  }
  function gvMoveRing(name){
    if (!gvFocusRing) return;
    var o = gvFind(name);
    if (!o){ gvFocusRing.style.display = 'none'; return; }
    gvFocusRing.setAttribute('cx', o.x);
    gvFocusRing.setAttribute('cy', o.y);
    gvFocusRing.style.display = '';
  }
  function gvBuild(key){
    var g = groupByKey(key);
    if (!g) return;
    gGv.innerHTML = '';
    gvTools = [];
    gvWires = [];
    gvFocusRing = null;
    var tools = g.tools.slice().sort(function(a, b){ return a.n < b.n ? -1 : (a.n > b.n ? 1 : 0); });
    var m = tools.length;
    var rings = gvLayout(m);
    var pos = {}; // name -> {x, y}
    var idx = 0;
    var gEdgesGv = el('g', {});
    var gWiresGv = el('g', {});
    var gNodesGv = el('g', {});
    var gLabelsGv = el('g', {});
    gGv.appendChild(gEdgesGv);
    gGv.appendChild(gWiresGv);
    gGv.appendChild(gNodesGv);
    gGv.appendChild(gLabelsGv);
    // Center node = the group itself; click = back to overview.
    var gc = el('circle', { cx: CX, cy: CY, r: 16, fill: TIER_COL[g.tier], 'class': 'radial-group', 'data-group': key });
    gc.addEventListener('click', function(){ clearHighlight(); });
    gc.addEventListener('mouseenter', function(ev){ showTip(groupTooltip(key), ev.clientX, ev.clientY); });
    gc.addEventListener('mousemove', function(ev){ showTip(groupTooltip(key), ev.clientX, ev.clientY); });
    gc.addEventListener('mouseleave', function(){ hideTip(); });
    gNodesGv.appendChild(gc);
    var gct = el('text', { x: CX, y: CY + 34, 'text-anchor': 'middle', 'font-size': 12, 'font-weight': 700, fill: '#ffffff', style: 'pointer-events:none' });
    gct.textContent = groupLabel(key) + ' · ' + m + ' tool';
    gLabelsGv.appendChild(gct);
    rings.forEach(function(ring){
      for (var i = 0; i < ring.count; i++){
        var t = tools[idx++];
        var ang = -Math.PI / 2 + (i + 0.5) * 2 * Math.PI / ring.count;
        var x = CX + ring.r * Math.cos(ang), y = CY + ring.r * Math.sin(ang);
        pos[t.n] = { x: x, y: y };
        // Tree edge: center -> tool (a1 = a2, so the cubic is a radial spoke).
        var e = el('path', { d: treeEdgePath(0, ang, ring.r, ang), fill: 'none', 'class': 'radial-edge-tier' });
        gEdgesGv.appendChild(e);
        var dot = el('circle', { cx: x, cy: y, r: 10, fill: TIER_COL[g.tier], 'class': 'radial-tool', 'data-tier': g.tier });
        if (t.miss){
          dot.setAttribute('stroke', '#fff');
          dot.setAttribute('stroke-width', '2');
          dot.setAttribute('stroke-dasharray', '3,2');
          var ttl = el('title', {});
          ttl.textContent = '⚠ MISSING from the original file — ' + t.n;
          dot.appendChild(ttl);
        }
        var hit = el('circle', { cx: x, cy: y, r: 16, fill: '#ffffff', 'fill-opacity': '0', 'class': 'radial-hit', 'data-tier': g.tier });
        (function(tt, hh){
          hh.addEventListener('mouseenter', function(ev){ gvFocusTool(tt.n); showTip(tipHTML(tt), ev.clientX, ev.clientY); });
          hh.addEventListener('mousemove', function(ev){ if (gvHoverTool === tt.n) showTip(tipHTML(tt), ev.clientX, ev.clientY); });
          hh.addEventListener('mouseleave', function(){ gvUnfocusTool(); });
          hh.addEventListener('click', function(ev){
            onLeafClick(tt.n, ev);
            gvApplyVisibility();
            if (pinnedTool === tt.n) gvMoveRing(tt.n);
            else if (gvFocusRing) gvFocusRing.style.display = 'none';
          });
        })(t, hit);
        gNodesGv.appendChild(dot);
        gNodesGv.appendChild(hit);
        // Name label beside the node: right half -> start, left half -> end,
        // near top/bottom -> middle below. Fill via style (var() does not work
        // in a presentation attribute).
        var c = Math.cos(ang);
        var lab;
        if (c > 0.35) lab = el('text', { x: x + 14, y: y + 4, 'text-anchor': 'start', 'font-size': 12, style: 'pointer-events:none;fill:var(--fg)', 'data-tool': t.n });
        else if (c < -0.35) lab = el('text', { x: x - 14, y: y + 4, 'text-anchor': 'end', 'font-size': 12, style: 'pointer-events:none;fill:var(--fg)', 'data-tool': t.n });
        else lab = el('text', { x: x, y: y + 22, 'text-anchor': 'middle', 'font-size': 12, style: 'pointer-events:none;fill:var(--fg)', 'data-tool': t.n });
        lab.textContent = t.n;
        gLabelsGv.appendChild(lab);
        gvTools.push({ el: dot, hit: hit, label: lab, tool: t, x: x, y: y });
      }
    });
    // Chain wires between in-group tools (all kinds built; visibility — incl.
    // the kind toggles — is applied by gvApplyVisibility()).
    var inGroup = {};
    tools.forEach(function(t){ inGroup[t.n] = 1; });
    var wirePaths = [];
    SB_LINKS.forEach(function(l){
      if (!inGroup[l.f] || !inGroup[l.t]) return;
      wirePaths.push({ f: l.f, t: l.t, kind: l.k, label: l.k + ': ' + l.f + relationArrow(l.k) + l.t + (l.c ? ' (' + l.c + ')' : '') + (l.q ? ' — ' + l.q : '') });
    });
    SB_AUTO.forEach(function(a){
      if (!inGroup[a.f] || !inGroup[a.t]) return;
      wirePaths.push({ f: a.f, t: a.t, kind: 'auto-chain', label: 'Auto-Chain proposal (unmeasured) — nhóm ' + a.r + ': ' + a.f + ' → ' + a.t + (a.c ? ' (' + a.c + ')' : '') });
    });
    wirePaths.forEach(function(w){
      var d = gvArcPath(pos[w.f], pos[w.t]);
      if (!d) return;
      var style = w.kind === 'delegates-to' ? 'stroke:#58a6ff;stroke-width:1.7;stroke-linecap:round;opacity:.9'
        : w.kind === 'recommends' ? 'stroke:#bc8cff;stroke-width:1;stroke-dasharray:4,3;opacity:.85'
        : w.kind === 'auto-chain' ? 'stroke:#fbbf24;stroke-width:1.5;stroke-dasharray:6,4'
        : w.kind === 'alias-shared-handler' ? 'stroke:#7ee787;stroke-width:1.1;stroke-dasharray:3,3;opacity:.85'
        : 'stroke:#3fb950;stroke-width:1.1;opacity:.85';
      var p = el('path', { d: d, fill: 'none', style: style, 'data-f': w.f, 'data-t': w.t, 'data-kind': w.kind });
      if (w.kind === 'delegates-to' || w.kind === 'alias-dispatch' || w.kind === 'recommends') p.setAttribute('marker-end', 'url(#rw-arrow)');
      if (w.kind === 'auto-chain') p.setAttribute('marker-end', 'url(#rw-arrow-auto)');
      gvWires.push({ el: p, f: w.f, t: w.t, kind: w.kind, baseW: (w.kind === 'delegates-to' || w.kind === 'auto-chain') ? '1.5' : '1' });
      (function(pp, ff, tt, kk, lab){
        pp.addEventListener('mouseenter', function(ev){ gvFocusWire(ff, tt); showTip('<div style="font-size:13px;font-weight:700">' + esc(ff) + relationArrow(kk) + esc(tt) + '</div><div style="color:var(--fg3);font-size:11px">' + esc(kk) + '</div><div style="margin-top:4px">' + esc(lab) + '</div>', ev.clientX, ev.clientY); });
        pp.addEventListener('mousemove', function(ev){ showTip('<div style="font-size:13px;font-weight:700">' + esc(ff) + relationArrow(kk) + esc(tt) + '</div><div style="color:var(--fg3);font-size:11px">' + esc(kk) + '</div><div style="margin-top:4px">' + esc(lab) + '</div>', ev.clientX, ev.clientY); });
        pp.addEventListener('mouseleave', function(){ gvUnfocusWire(); });
      })(p, w.f, w.t, w.kind, w.label);
      gWiresGv.appendChild(p);
    });
    gvFocusRing = el('circle', { r: 15, fill: 'none', stroke: '#ffffff', 'stroke-width': 2, style: 'display:none;pointer-events:none' });
    gNodesGv.appendChild(gvFocusRing);
  }
  // Label de-collision (measured, not claimed): after the group view is
  // shown, every label is measured with getBBox() and overlapping pairs are
  // separated (AABB minimum-translation, <=10 passes, clamped to the viewBox
  // with an 8px margin). Runs only while the layer is visible — getBBox()
  // returns zeros for display:none content. The remaining overlap count is
  // stored in data-gv-overlap on the group-view layer.
  function gvDecollideLabels(){
    gvOverlapCount = 0;
    var labels = gvTools.map(function(o){ return o.label; });
    if (labels.length < 2){ gGv.setAttribute('data-gv-overlap', '0'); return; }
    function boxOf(l){
      var b = l.getBBox();
      return { l: l, x: b.x, y: b.y, w: b.width, h: b.height };
    }
    function shiftLabel(l, dx, dy){
      var x = parseFloat(l.getAttribute('x')) + dx;
      var y = parseFloat(l.getAttribute('y')) + dy;
      var b = l.getBBox();
      if (b.x + dx < 8) x += 8 - (b.x + dx);
      if (b.x + b.width + dx > W - 8) x -= (b.x + b.width + dx) - (W - 8);
      if (b.y + dy < 8) y += 8 - (b.y + dy);
      if (b.y + b.height + dy > H - 8) y -= (b.y + b.height + dy) - (H - 8);
      l.setAttribute('x', x);
      l.setAttribute('y', y);
    }
    // Clamp every label into the viewBox first (F2): a non-overlapping label
    // at the east/west extreme would otherwise be cut off by the viewport —
    // the clamp must not run only for overlapping pairs.
    labels.forEach(function(l){ shiftLabel(l, 0, 0); });
    for (var pass = 0; pass < 10; pass++){
      var bs = labels.map(boxOf);
      var moved = false;
      for (var i = 0; i < bs.length; i++){
        for (var j = i + 1; j < bs.length; j++){
          var a = bs[i], b = bs[j];
          var ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
          var oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
          if (ox > 0.5 && oy > 0.5){
            if (ox < oy){
              var dx = ox / 2 + 1;
              var dir = (a.x + a.w / 2) < (b.x + b.w / 2) ? -1 : 1;
              shiftLabel(a.l, dir * dx, 0);
              shiftLabel(b.l, -dir * dx, 0);
            } else {
              var dy = oy / 2 + 1;
              var diry = (a.y + a.h / 2) < (b.y + b.h / 2) ? -1 : 1;
              shiftLabel(a.l, 0, diry * dy);
              shiftLabel(b.l, 0, -diry * dy);
            }
            moved = true;
          }
        }
      }
      if (!moved) break;
    }
    var fin = labels.map(boxOf);
    var n = 0;
    for (var i2 = 0; i2 < fin.length; i2++){
      for (var j2 = i2 + 1; j2 < fin.length; j2++){
        var c = fin[i2], d = fin[j2];
        if (Math.min(c.x + c.w, d.x + d.w) - Math.max(c.x, d.x) > 0.5 && Math.min(c.y + c.h, d.y + d.h) - Math.max(c.y, d.y) > 0.5) n++;
      }
    }
    gvOverlapCount = n;
    gGv.setAttribute('data-gv-overlap', String(n));
    gGv.setAttribute('data-gv-group', gvKey || '');
  }
  // Group-view visibility: same composed function as the overview (search AND
  // pill AND scope), plus the hover/pin narrowed lit-set (hovered/pinned tool
  // + chain neighbors within the group). Kind toggles hide gv wires too.
  function gvApplyVisibility(){
    if (!gvActive) return;
    var inGroup = {};
    gvTools.forEach(function(o){ inGroup[o.tool.n] = 1; });
    var hover = null;
    if (gvHoverWire){
      if (passesConditions12(gvHoverWire.f) || passesConditions12(gvHoverWire.t)) hover = { kind: 'wire', f: gvHoverWire.f, t: gvHoverWire.t };
    } else if (gvHoverTool){
      if (passesConditions12(gvHoverTool)) hover = { kind: 'leaf', name: gvHoverTool };
    }
    var focusName = hover ? null : pinnedTool;
    var neigh = null;
    if (hover && hover.kind === 'leaf') neigh = leafNeighbors(hover.name);
    else if (focusName && inGroup[focusName]) neigh = leafNeighbors(focusName);
    var lit = {};
    gvTools.forEach(function(o){
      var name = o.tool.n, ok;
      if (hover && hover.kind === 'wire' && (name === hover.f || name === hover.t)) ok = true;
      else {
        ok = passesConditions12(name);
        if (ok && hover){
          if (hover.kind === 'leaf'){ if (name !== hover.name && !neigh[name]) ok = false; }
          else ok = false;
        } else if (ok && focusName && inGroup[focusName]){
          if (name !== focusName && !neigh[name]) ok = false;
        }
      }
      o.el.setAttribute('opacity', ok ? '1' : '0.12');
      o.label.setAttribute('opacity', ok ? '1' : '0.12');
      if (ok) lit[name] = 1;
    });
    gvWires.forEach(function(w){
      var kindOn = wireVisible(w.kind === 'auto-chain' ? 'auto' : w.kind);
      if (!kindOn){ w.el.style.display = 'none'; return; }
      w.el.style.display = '';
      var on, sw = w.baseW;
      if (hover && hover.kind === 'leaf'){
        on = (w.f === hover.name || w.t === hover.name);
        if (on) sw = '3';
        w.el.setAttribute('opacity', on ? '1' : '0.06');
      } else if (hover && hover.kind === 'wire'){
        on = (w.f === hover.f && w.t === hover.t);
        if (on) sw = '3';
        w.el.setAttribute('opacity', on ? '1' : '0.06');
      } else if (focusName && inGroup[focusName]){
        on = (w.f === focusName || w.t === focusName);
        if (on) sw = '3';
        w.el.setAttribute('opacity', on ? '1' : '0.06');
      } else {
        on = !!(lit[w.f] || lit[w.t]);
        w.el.setAttribute('opacity', on ? '1' : '0.25');
      }
      w.el.setAttribute('stroke-width', sw);
    });
  }
  function gvFocusTool(name){
    gvHoverTool = name;
    gvApplyVisibility();
    // A tooltip-only hover (leaf not matching the active search/pill) changes
    // no opacity — and shows no focus ring either.
    if (passesConditions12(name)) gvMoveRing(name);
    else if (gvFocusRing) gvFocusRing.style.display = 'none';
  }
  function gvUnfocusTool(){
    hideTip();
    gvHoverTool = null;
    if (pinnedTool){ gvApplyVisibility(); gvMoveRing(pinnedTool); return; }
    gvApplyVisibility();
    if (gvFocusRing) gvFocusRing.style.display = 'none';
  }
  function gvFocusWire(f, t){
    gvHoverWire = { f: f, t: t };
    gvApplyVisibility();
    if (passesConditions12(f) || passesConditions12(t)) gvMoveRing(f);
  }
  function gvUnfocusWire(){
    hideTip();
    gvHoverWire = null;
    if (pinnedTool){ gvApplyVisibility(); gvMoveRing(pinnedTool); return; }
    if (gvHoverTool){ gvFocusTool(gvHoverTool); return; }
    gvApplyVisibility();
    if (gvFocusRing) gvFocusRing.style.display = 'none';
  }
  function enterGroupView(key){
    var g = groupByKey(key);
    if (!g) return;
    gvBuild(key);
    gvActive = true;
    gvKey = key;
    gEdges.style.display = 'none';
    gWires.style.display = 'none';
    gNodes.style.display = 'none';
    gLabels.style.display = 'none';
    gGv.style.display = '';
    resetZoom();
    if (gvBarEl){
      gvBarEl.classList.add('on');
      if (gvTitleEl) gvTitleEl.textContent = groupLabel(key) + ' · ' + g.tier;
      if (gvMetaEl) gvMetaEl.textContent = g.tools.length + ' tool — node to, tên đọc được · bấm “← Overview” để về toàn cảnh';
    }
    gvDecollideLabels();
    gvApplyVisibility();
  }
  function exitGroupView(){
    if (!gvActive) return;
    gvActive = false;
    gvKey = null;
    gvHoverTool = null;
    gvHoverWire = null;
    gGv.style.display = 'none';
    gEdges.style.display = '';
    gWires.style.display = '';
    gNodes.style.display = '';
    gLabels.style.display = '';
    if (gvBarEl) gvBarEl.classList.remove('on');
    resetZoom();
  }
  // ── DASHBOARD (Radial tab only) ──────────────────────────────────────────
  // Top bar + tier stages + static pills + live log + status bar.
  // Static counts are computed from TOOLS at load (verified at build:
  // 260 tools, deprecated 5, MISSING 4, unused-in-window 163, Errata 57).
  // Live data uses only the same-origin Monitor adapter; bridge failure never hides the inventory.
  var dashStagesEl = document.getElementById('sb-stages');
  var dashStaticEl = document.getElementById('sb-static');
  var dashLiveEl = document.getElementById('sb-live');
  var dashLiveStateEl = document.getElementById('sb-live-state');
  var dashStatusEl = document.getElementById('sb-statusbar');
  var dashBridgePill = document.getElementById('sb-bridge-pill');
  var dashClockEl = document.getElementById('sb-clock');

  var dashCounts = { total: TOOLS.length, docs: 0, dep: 0, miss: 0, err: 0, unused: 0 };
  TOOLS.forEach(function(t){
    if (t.eff && t.uc) dashCounts.docs++;
    if (t.dep) dashCounts.dep++;
    if (t.miss) dashCounts.miss++;
    if (t.err) dashCounts.err++;
    if (t.use && t.use.b === 'unused') dashCounts.unused++;
  });

  var dashPills = [
    { key: 'docs', label: 'có mô tả', count: dashCounts.docs, test: function(t){ return !!(t.eff && t.uc); } },
    { key: 'dep', label: 'ngững dùng', count: dashCounts.dep, test: function(t){ return !!t.dep; } },
    { key: 'miss', label: 'thiếu', count: dashCounts.miss, test: function(t){ return !!t.miss; } },
    { key: 'unused', label: 'không dùng trong cửa sổ', count: dashCounts.unused, test: function(t){ return !!(t.use && t.use.b === 'unused'); } },
    { key: 'err', label: 'sai sót', count: dashCounts.err, test: function(t){ return !!t.err; } }
  ];
  var activePillKey = null;
  var activePillLabel = '';

  function pillSetFor(key){
    if (!key) return null;
    var def = null;
    dashPills.forEach(function(p){ if (p.key === key) def = p; });
    if (!def) return null;
    var s = {};
    TOOLS.forEach(function(t){ if (def.test(t)) s[t.n] = 1; });
    return s;
  }
  function setPillFilter(key){
    if (activePillKey === key){ activePillKey = null; activePillLabel = ''; pillFilter = null; }
    else {
      activePillKey = key;
      activePillLabel = key;
      pillFilter = pillSetFor(key);
    }
    syncDashPills();
    applyVisibility();
    if (gvActive) gvApplyVisibility();
    syncCrumb();
  }
  function syncDashPills(){
    if (!dashStaticEl) return;
    var btns = dashStaticEl.querySelectorAll('[data-pill]');
    btns.forEach(function(b){ b.classList.toggle('active', b.getAttribute('data-pill') === activePillKey); });
  }
  function buildDashStages(){
    if (!dashStagesEl) return;
    dashStagesEl.innerHTML = TIERS.map(function(tier){
      return '<button class="filter-btn ctl-tier" data-tier="' + tier + '" style="--sc:' + TIER_COL[tier] + '" title="' + esc(TIER_LABEL[tier]) + '">'
        + '<span class="tier-dot"></span>' + esc(tier)
        + ' <span class="ctl-count">' + byTier[tier].length + '</span></button>';
    }).join('');
  }
  function syncDashStages(){
    if (!dashStagesEl) return;
    var btns = dashStagesEl.querySelectorAll('[data-tier]');
    btns.forEach(function(b){ b.classList.toggle('active', b.getAttribute('data-tier') === activeTier); });
  }
  function dashStatusHTML(live){
    var pills = [];
    pills.push('<span class="dash-pill ok">matrix [' + dashCounts.total + ']</span>');
    pills.push('<span class="dash-pill ' + (dashCounts.docs === dashCounts.total ? 'ok' : 'warn') + '">docs [' + (dashCounts.docs === dashCounts.total ? 'complete' : dashCounts.docs + '/' + dashCounts.total) + ']</span>');
    pills.push('<span class="dash-pill warn">deprecated [' + dashCounts.dep + ']</span>');
    pills.push('<span class="dash-pill warn">missing [' + dashCounts.miss + ']</span>');
    pills.push('<span class="dash-pill">unused [' + dashCounts.unused + ']</span>');
    if (!live){
      pills.push('<span class="dash-pill">bridge [···]</span>');
      pills.push('<span class="dash-pill off">ToolA [?]</span>');
      pills.push('<span class="dash-pill off">ToolQ [?]</span>');
    } else if (!live.up){
      pills.push('<span class="dash-pill bad">bridge [offline]</span>');
      pills.push('<span class="dash-pill off">ToolA [?]</span>');
      pills.push('<span class="dash-pill off">ToolQ [?]</span>');
    } else {
      pills.push('<span class="dash-pill ' + (live.status === 'stale' ? 'warn">bridge [stale]' : 'ok">bridge [live]') + '</span>');
      var ta = live.services.ToolA;
      if (!ta) pills.push('<span class="dash-pill off">ToolA [?]</span>');
      else if (!ta.up) pills.push('<span class="dash-pill bad">ToolA [down]</span>');
      else if (ta.level === 'warn') pills.push('<span class="dash-pill warn">ToolA [up &middot; warn]</span>');
      else pills.push('<span class="dash-pill ok">ToolA [up]</span>');
      var tq = live.services.ToolQ;
      if (!tq) pills.push('<span class="dash-pill off">ToolQ [?]</span>');
      else if (tq.up === false) pills.push('<span class="dash-pill bad">ToolQ [down]</span>');
      else if (tq.status === 'degraded' || tq.level === 'warn') pills.push('<span class="dash-pill warn">ToolQ [degraded]</span>');
      else pills.push('<span class="dash-pill ok">ToolQ [up]</span>');
    }
    return pills.join('');
  }

  // Operations map: build topology on demand, then patch only telemetry text on poll.
  const mapStage = document.getElementById('mm-stage');
  const mapRoot = document.getElementById('mm-map');
  const mapSource = document.getElementById('mm-source');
  const mapTotal = document.getElementById('mm-total');
  const mapSample = document.getElementById('mm-sample');
  const mapDetail = document.getElementById('mm-detail');
  const mapButtons = [...mapStage.querySelectorAll('.mm-tier')];
  const mapCanvas = document.getElementById('mm-canvas');
  const mapAnchor = document.getElementById('mm-root');
  const mapWires = document.getElementById('mm-branch-wires');
  const mapRelations = document.getElementById('mm-relation-wires');
  const mapBranches = new Map(mapButtons.map(button => [button.dataset.mmTier, {
    button, panel:document.getElementById(button.getAttribute('aria-controls'))
  }]));
  const serviceNodes = {ToolA:document.getElementById('mm-service-a'), ToolQ:document.getElementById('mm-service-q')};
  const neverKnown = '— lượt · — lỗi';
  const mapToolIndex = new Map(tools.map(t => [t.n, t]));
  const mapGroupLabels = {
    analysis:'Phân tích', audit:'Kiểm toán', council:'Hội đồng', deploy:'Triển khai', desktop:'Desktop',
    git:'Git', mission:'Nhiệm vụ', ops:'Vận hành', read:'Đọc dữ liệu', runtime:'Runtime',
    search:'Tìm kiếm', session:'Phiên làm việc', test:'Kiểm thử', workflow:'Quy trình', write:'Ghi dữ liệu'
  };
  const serviceSnapshotTime = new Intl.DateTimeFormat('vi-VN', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  const mapStatusLabels = {connecting:'Đang kết nối', live:'Dữ liệu mới', stale:'Dữ liệu cũ / thiếu', offline:'Mất kết nối'};
  const terminalErrors = new Set(['error','err','fail','failed','timeout']);
  let mapSelected = null, selectedBranch = null, hoveredCard = null;
  function selectBranch(button){
    for (const previous of mapCanvas.querySelectorAll('.mm-return-flash')){
      clearTimeout(previous._mmFlashTimer);
      previous.classList.remove('mm-return-flash');
    }
    selectedBranch?.removeAttribute('aria-current');
    selectedBranch = button;
    button?.setAttribute('aria-current', 'location');
  }
  // The overlay is armed by default and painted only when a tool is selected -- Anh's
  // ruling was "Overlay khi chọn 1 tool", not a permanent second web of lines over a
  // 260-node map. The toolbar button flips this and says so through aria-pressed.
let mapRelationsOn = true;
  const mapSearch = document.getElementById('mm-search');
  const mapHits = document.getElementById('mm-hits');
  const mapSearchStatus = document.getElementById('mm-search-status');
  const mapSearchClear = document.getElementById('mm-search-clear');
  const mapTierNames = new Map(mapButtons.map(b => [b.dataset.mmTier,b.querySelector('strong').textContent]));
  const mapAliases = {
    analysis:'phân tích kiến trúc dependency', audit:'nhật ký kiểm toán log truy vết', council:'hội đồng agent worker',
    deploy:'khởi động server triển khai', desktop:'cửa sổ chụp màn hình screenshot', git:'commit diff nhánh',
    ops:'vận hành trạng thái sức khỏe', read:'đọc file tệp thư mục read file', runtime:'giao diện trình duyệt browser DOM',
    search:'tìm code mã nguồn tra cứu tìm kiếm symbol', session:'phiên làm việc session', test:'chạy test kiểm tra kiểm thử',
    workflow:'luồng công việc workflow', write:'sửa file ghi tệp tạo tập tin chỉnh sửa file edit file'
  };
  const lookupToolAliases = {
    read_file_range:'đọc file đọc tệp xem nội dung file đọc khoảng dòng',
    search_code:'tìm code tìm mã nguồn tìm ký tự trong mã nguồn',
    write_fix:'sửa file chỉnh sửa tệp sửa đoạn code thay thế dòng',
    write_new_file:'tạo file mới tạo tệp mới ghi file mới',
    run_project_test_script:'chạy test chạy kiểm thử chạy script kiểm thử',
    desktop_screenshot:'chụp màn hình chụp ảnh desktop',
    git_status:'trạng thái git kiểm tra thay đổi git'
  };
  const mapGroupName = key => mapGroupLabels[groupByKey(key)?.fg] || groupLabel(key);
  const mapGroupKey = t => t.t + '|' + (t.fg || '');
  // Cluster level (Anh asked for it 2026-11-22): ToolA -> tier -> cụm -> nhóm -> tool.
  // Authored from fg, NOT derived from name prefixes: T1|ops spreads 46 leaves over 41
  // two-token prefixes, so a prefix heuristic fragments it worse than fg does.
  const MAP_CLUSTER_ORDER = ['code','ops','council','audit','ui','khac'];
  const mapClusterLabels = { code:'Code & dữ liệu', ops:'Vận hành & server', council:'Hội đồng & quy trình',
    audit:'Kiểm toán & phiên', ui:'Giao diện & desktop', khac:'Khác' };
  const MAP_CLUSTER_OF_FG = { read:'code', search:'code', analysis:'code', git:'code', write:'code',
    ops:'ops', runtime:'ops', deploy:'ops', test:'ops',
    council:'council', workflow:'council', mission:'council',
    audit:'audit', session:'audit', desktop:'ui' };
  const mapClusterOf = fg => MAP_CLUSTER_OF_FG[fg] || 'khac';
  const mapClustersOf = tier => {
    const bag = new Map();
    for (const g of groups.filter(x => x.tier === tier)){
      const id = mapClusterOf(g.fg);
      if (!bag.has(id)) bag.set(id,[]);
      bag.get(id).push(g);
    }
    return MAP_CLUSTER_ORDER.filter(id => bag.has(id)).map(id => ({ id, label: mapClusterLabels[id],
      groups: bag.get(id).slice().sort((x,y) => y.tools.length - x.tools.length || String(x.fg).localeCompare(String(y.fg))) }));
  };
  // Inventory and map use the same accent-insensitive spelling.
  const mapLookupIndex = tools.map(t => ({
    t, name:normalizeLookup(t.n), alias:normalizeLookup(lookupToolAliases[t.n] || ''),
    tags:normalizeLookup([t.t, t.fg, t.cat, mapGroupName(mapGroupKey(t)), mapAliases[t.fg]].filter(Boolean).join(' ')),
    body:normalizeLookup([t.eff,t.uc].filter(Boolean).join(' '))
  }));
  let searchOptions = [], activeOption = -1, searchTiers = new Set(), searchGroups = new Set();
  const mapHasQuery = () => !!normalizeLookup(mapSearch.value);

  // WIRE HOVER (ported from the Atlas mindmap demo, which treats wires as
  // first-class interactive citizens). The index is rebuilt by
  // syncMapBranchLayout because every layout pass throws the paths away; it lives
  // here, outside initMap, so both the builder and the listeners see one truth.
  const wireEntries = [];
  const nodeWires = new Map();
  const mapKindLabels = { tier:'nh\u00e1nh', cluster:'c\u1EE5m', group:'nh\u00f3m', bucket:'prefix', tool:'tool' };
  function wireLabel(node){
    if (!node) return '?';
    if (node.id === 'mm-root') return 'ToolA';
    const tool = node.dataset.mmTool;
    if (tool) return tool;
    const group = node.dataset.mmGroup;
    if (group) return mapGroupName(group);
    if (node.classList && node.classList.contains('mm-cluster-btn')) {
      return mapClusterLabels[node.closest('.mm-cluster')?.dataset.mmCluster] || node.querySelector('strong')?.textContent || '?';
    }
    const tier = node.dataset.mmTier;
    if (tier) return (mapTierNames.get(tier) || tier);
    return node.querySelector('strong')?.textContent || '?';
  }
  // Clears by DOM query, NOT by walking wireEntries: a node lit through lightNode
  // may be a leaf chip that owns no wire at all (.mm-more), and syncMapBranchLayout
  // empties the index before it repaints -- either case strands data-mm-lit on the
  // node and the map accumulates lit chips until everything glows.
  function clearHoverPaint(){
    hoveredCard = null;
    for (const node of mapCanvas.querySelectorAll('[data-mm-lit]')) node.removeAttribute('data-mm-lit');
    for (const e of wireEntries) e.path.classList.remove('mm-wire-lit');
    mapCanvas.classList.remove('mm-wire-hover');
    hideTip();
  }
  function clearMapLit(){
    clearHoverPaint();
    paintMapFocus();
  }
  function lightWire(entry){
    clearHoverPaint();
    mapCanvas.classList.add('mm-wire-hover');
    entry.path.classList.add('mm-wire-lit');
    entry.a.setAttribute('data-mm-lit','true');
    entry.b.setAttribute('data-mm-lit','true');
    // Lighting the FAR endpoint's subtree, not both endpoints' subtrees: a wire's
    // parent is an ancestor of its child, so the parent is already kept, and taking
    // the child's whole rail is what makes hovering a leaf wire show its section.
    applyMapFocus(focusMapSubtree(entry.b));
  }
  function lightNode(node){
    clearHoverPaint();
    hoveredCard = node;
    mapCanvas.classList.add('mm-wire-hover');
    node.setAttribute('data-mm-lit','true');
    for (const e of nodeWires.get(node) || []){
      e.path.classList.add('mm-wire-lit');
      e.a.setAttribute('data-mm-lit','true');
      e.b.setAttribute('data-mm-lit','true');
    }
    applyMapFocus(focusMapSubtree(node));
  }
  // Opt-in audio (Anh asked for the demo's sound hooks; a 260-node map that
  // chirps on every hover is hostile, so it ships OFF and persists one choice).
  const MAP_SOUND_KEY = 'toola.matrix.sound';
  let audioCtx = null, lastToneAt = 0;
  function mapSoundOn(){ return mapRoot.dataset.mmSound === 'true'; }
  function playMapTone(freq, dur, type){
    if (!mapSoundOn()) return;
    const now = performance.now();
    if (now - lastToneAt < 45) return;
    lastToneAt = now;
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const osc = audioCtx.createOscillator(), gain = audioCtx.createGain();
      osc.type = type || 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.045, audioCtx.currentTime + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + dur);
      osc.connect(gain); gain.connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + dur + 0.02);
    } catch (err) { /* decoration only -- audio must never break the map */ }
  }

  // ---- FOCUS SUBTREE ----------------------------------------------------------
  // Anh 2026-11-23: 'khi chon hoac hover 1 node thi cac node con lai toi xuong va
  // blur nhe. xem lai html atom de dung co che'. The mechanism is the Atlas demo's
  // has-node-focus contract (:1470-1531): ONE class on the container arms the dim, and
  // every element of the focused subtree carries mm-lit -- here mm-focus-keep -- so the
  // CSS is two rules instead of a per-element calculation.
  //
  // WHAT WAS ACTUALLY BROKEN, and it was not a missing blur. lightNode added
  // mm-wire-hover, whose only CSS (:338) filters .mm-wires path -- WIRES. Not one card
  // was ever touched, so hovering a node dimmed some lines and Anh saw nothing happen
  // to the nodes. And paintMapFocus's selector listed '.mm-tier-wire' and nothing else,
  // so cluster, group, bucket and tool wires never learned is-focus or is-muted: a
  // selected tool muted three tier wires and left the rest of the map at full strength.
  //
  // Opacity is deliberately NOT part of the dim. Atlas :1479 pins node opacity to 1 with
  // the reason written in its own file -- a translucent card lets the wire behind it
  // read straight through, which is the exact defect this map was rebuilt to remove. So
  // the dim is grey + a darker multiply + blur, all three paint-only: no card's
  // getBoundingClientRect moves, which the anchor lock (delta 0.02px) and the wire
  // geometry (getScreenCTM) both depend on.
  // Every CARD on the map, and the census has to be complete: a bucket chip lives in
  // .mm-bucket-body rather than .mm-tools, so describing the leaves as '.mm-tools >
  // button' alone left every chip of every bucketed rail undimmed while its own prefix
  // head went grey -- the wire lighting nothing, which is the complaint this whole
  // level exists to answer. .mm-more is a real button in the rail too, and a dashed
  // 'xem thêm' pill glowing inside a dimmed map is the same defect in miniature.
  const MM_CARD_SEL = '.mm-root, .mm-tier, .mm-cluster-btn, .mm-group > button, .mm-bucket-btn, .mm-tools > button, .mm-bucket-body > button';
  // Pinned coordinates live in map space, independent of the camera and DOM lifetime.
  const pinnedMapNodes = new Map();
  function mapNodeKey(node){
    if (node === chainPanelEl) return 'panel:' + chainAnchorTool;
    return node.dataset.mmTool ? 'tool:' + node.dataset.mmTool : node.id || node.getAttribute('aria-controls');
  }
  function positionPinnedCards(){
    if (!pinnedMapNodes.size) return;
    const canvas = mapCanvas.getBoundingClientRect(), zoom = mapZoomNow();
    for (const card of mapCanvas.querySelectorAll(MM_CARD_SEL)){
      const pin = pinnedMapNodes.get(mapNodeKey(card));
      if (!pin) continue;
      const rect = card.getBoundingClientRect();
      if (!rect.width) continue;
      const baseX = (rect.left - canvas.left) / zoom - (parseFloat(card.style.left) || 0);
      const baseY = (rect.top - canvas.top) / zoom - (parseFloat(card.style.top) || 0);
      card.style.left = (pin.x - baseX) + 'px';
      card.style.top = (pin.y - baseY) + 'px';
      card.classList.add('mm-placed');
    }
  }
  function mapCardOf(el){
    if (!el) return null;
    if (el.matches && el.matches(MM_CARD_SEL)) return el;
    return (el.closest && el.closest(MM_CARD_SEL)) || null;
  }
  // The subtree of ONE card: itself, what is rendered under it, and the ROAD back to the
  // root. Two measured facts shape this, and the first version of the function got both
  // wrong -- a selected tool kept exactly one card (focus_probe: kept=1, dimmed=21 of 22)
  // because the road was walked with parentElement.
  //
  // 1. THE TREE IS NOT PARENT-CHILD. chain_probe walked up from the write_fix chip and
  //    found .mm-tools -> .mm-group -> .mm-cluster-body -> .mm-cluster -> .mm-groups ->
  //    .mm-branch -> .mm-branches: seven DIV/SECTION elements and not one button. Every
  //    expandable card is the PREVIOUS SIBLING of the container it owns, and the container
  //    carries the id -- ctl_probe measured all 20 aria-controls pairs in the canvas and
  //    every one came back rel='next-sibling'. So the road up is walked in reverse through
  //    aria-controls, which is the accordion's own relation and cannot drift from it.
  // 2. .mm-root IS NOT AN ANCESTOR EITHER. It is a sibling of .mm-branches inside the
  //    canvas (matrix.html:668), so no upward walk reaches it, and without it no root->tier
  //    wire has both ends kept -- the road would light from the tier down and stop.
  //
  // Descendants are collected for the STARTING card only. Walking up and pulling each
  // owner's whole subtree would light every sibling chip of the group the selected tool
  // belongs to, which is a different answer from 'the road to this tool'.
  function focusMapSubtree(node){
    const keep = new Set();
    const self = mapCardOf(node);
    if (!self) return keep;
    keep.add(self);
    const ctl = self.getAttribute('aria-controls');
    const body = ctl ? document.getElementById(ctl) : null;
    if (body) for (const c of body.querySelectorAll(MM_CARD_SEL)) keep.add(c);
    const owners = [...mapCanvas.querySelectorAll('[aria-controls]')];
    for (let p = self.parentElement; p && p !== mapCanvas; p = p.parentElement){
      if (p.matches && p.matches(MM_CARD_SEL)) keep.add(p);
      if (!p.id) continue;
      const owner = owners.find(o => o.getAttribute('aria-controls') === p.id);
      const card = owner ? mapCardOf(owner) : null;
      if (card) keep.add(card);
    }
    const root = document.getElementById('mm-root');
    if (root && mapCanvas.contains(root)) keep.add(root);
    return keep;
  }
  function unionMapSubtree(nodes){
    const keep = new Set();
    for (const n of nodes) for (const c of focusMapSubtree(n)) keep.add(c);
    return keep;
  }
  function clearMapFocus(){
    mapCanvas.classList.remove('mm-has-focus');
    for (const c of mapCanvas.querySelectorAll('.mm-focus-keep')) c.classList.remove('mm-focus-keep');
    for (const e of wireEntries) e.path.classList.remove('mm-wire-keep');
  }
  // Clear-then-apply, never apply-then-prune: a stale mm-focus-keep on a card that a
  // later render removed would strand the class on a detached node and the dim would
  // under-report forever.
  function applyMapFocus(keep){
    clearMapFocus();
    if (!keep.size) return;
    mapCanvas.classList.add('mm-has-focus');
    for (const c of mapCanvas.querySelectorAll(MM_CARD_SEL)) c.classList.toggle('mm-focus-keep', keep.has(c));
    // A wire belongs to the focus subtree when BOTH its ends do. That single condition
    // lights the run from the root down to the hovered card and the branches under it,
    // and mutes every sibling wire without a per-kind rule.
    for (const e of wireEntries) e.path.classList.toggle('mm-wire-keep', keep.has(e.a) && keep.has(e.b));
  }

  function paintMapFocus(){
    const tool = mapToolIndex.get(mapSelected);
    const branchRoad = selectedBranch ? focusMapSubtree(selectedBranch) : null;
    if (branchRoad){
      for (const {a,b,path} of wireEntries){
        if (branchRoad.has(a) && branchRoad.has(b)) branchRoad.add(path);
      }
    }
    const focused = tool ? new Set([tool.t]) : searchTiers;
    const focusedGroups = tool ? new Set([mapGroupKey(tool)]) : searchGroups;
    const dim = !!branchRoad || focused.size > 0;
    const focusedClusters = new Set();
    if (tool) focusedClusters.add(tool.t + '|' + mapClusterOf(tool.fg || ''));
    else for (const key of searchGroups) focusedClusters.add(key.split('|')[0] + '|' + mapClusterOf(groupByKey(key)?.fg || ''));
    // ALL FIVE WIRE KINDS, not just the tier ones. Excluding .mm-wire-hit: it is the
    // invisible 14px hover twin, and muting or lighting it would paint a second stroke
    // exactly where the real wire is.
    for (const node of [...mapButtons, ...mapCanvas.querySelectorAll('[data-mm-group], .mm-cluster, .mm-tier-wire, .mm-cluster-wire, .mm-group-wire, .mm-bucket-wire, .mm-tool-wire')]){
      const group = node.dataset.mmGroup;
      const tier = node.closest('[data-mm-tier]')?.dataset.mmTier;
      const clusterId = node.dataset.mmCluster;
      const match = branchRoad
        ? branchRoad.has(node) || (node.classList.contains('mm-cluster') && branchRoad.has(node.querySelector('.mm-cluster-btn')))
        : clusterId ? focusedClusters.has(tier + '|' + clusterId) : group ? focusedGroups.has(group) : focused.has(tier);
      node.classList.toggle('is-focus', match);
      node.classList.toggle('is-muted', dim && !match);
    }
    // THE NODE DIM ITSELF. is-focus / is-muted stay the wires' and the panel's signal
    // (the rail test reads them); the cards go through the same mm-has-focus channel a
    // hover uses, so one CSS contract serves both and the two can never disagree.
    if (tool){
      // mapButtons holds the TIERS only (matrix.js:2110), so it can never yield a tool
      // chip; asking it for one returned undefined, focusMapSubtree gave an empty set,
      // and the SELECTED tool dimmed along with everything else -- the opposite of a
      // focus. The chip is looked up by the attribute that actually names it, and the
      // names are compared in JS rather than interpolated into a selector.
      const btn = [...mapCanvas.querySelectorAll('[data-mm-tool]')]
        .find(b => b.dataset.mmTool === mapSelected) || null;
      // applyMapFocus is the whole point of this branch, and dropping it is what made a
      // selection paint nothing: an earlier edit replaced two lines with one and left
      // the bare 'return' standing, which is valid syntax, so pre-write validation
      // passed and only the focus meter caught it (armed=false, dimmed=0 across 22
      // cards, while a hover correctly dimmed 3 of 5).
      applyMapFocus(focusMapSubtree(btn));
      return;
    }
    if (branchRoad){ applyMapFocus(branchRoad); return; }
    if (!dim){ clearMapFocus(); return; }
    const cards = [];
    for (const t of focused){
      const el = mapCanvas.querySelector('.mm-tier[data-mm-tier="' + t + '"]');
      if (el) cards.push(el);
    }
    for (const key of focusedGroups){
      const el = mapCanvas.querySelector('button[data-mm-group="' + key + '"]');
      if (el) cards.push(el);
    }
    applyMapFocus(unionMapSubtree(cards));
  }
  function animateBranch(tier, kind){
    if (!mapRoot.closest('.panel').classList.contains('active') || document.hidden || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const wire = mapWires.querySelector('.mm-tier-wire[data-mm-tier="' + tier + '"]');
    if (!wire) return;
    const trace = wire.cloneNode(false);
    trace.setAttribute('class','mm-trace mm-trace-' + kind);
    trace.setAttribute('pathLength','100');
    trace.addEventListener('animationend', () => trace.remove(), {once:true});
    setTimeout(() => trace.remove(), 950);
    wire.after(trace);
  }
  function closeLookup(){
    mapHits.hidden = true;
    mapSearch.setAttribute('aria-expanded','false');
    mapSearch.removeAttribute('aria-activedescendant');
    activeOption = -1;
  }
  function setLookupOption(i){
    activeOption = i;
    [...mapHits.children].forEach((el,j) => el.setAttribute('aria-selected',String(i === j)));
    const option = mapHits.children[i];
    if (option){
      mapSearch.setAttribute('aria-activedescendant',option.id);
      option.scrollIntoView({block:'nearest'});
    }
  }
  function updateLookup(){
    selectBranch(null);
    closeMapDetail();
    closeLookup();
    const query = normalizeLookup(mapSearch.value);
    mapSearchClear.hidden = !mapSearch.value;
    searchTiers = new Set();
    searchGroups = new Set();
    searchOptions = [];
    mapHits.replaceChildren();
    if (!query){ setText(mapSearchStatus,''); paintMapFocus(); return; }
    const tokens = query.split(' ');
    const contains = (field, token) => field.includes(token);
    const matches = mapLookupIndex.map(row => {
      const {name,alias,tags,body} = row;
      if (name === query) return [row,1000];
      if (name.startsWith(query)) return [row,850];
      if (name.includes(query)) return [row,720];
      if (alias.includes(' ' + query + ' ') || alias.startsWith(query + ' ') || alias.endsWith(' ' + query) || alias === query) return [row,500];
      const scores = tokens.map(token => contains(name,token) ? 10 : contains(tags,token) ? 6 : contains(body,token) ? 1 : 0);
      if (!scores.every(Boolean)) return [row,0];
      const phrase = tags.includes(query) ? 45 : 0;
      return [row,phrase + scores.reduce((a,b) => a+b,0)];
    }).filter(([,score]) => score > 0).sort((a,b) => b[1]-a[1] || a[0].t.n.localeCompare(b[0].t.n));
    searchOptions = matches.slice(0,9).map(([r]) => r.t);
    searchTiers = new Set(matches.map(([r]) => r.t.t));
    searchGroups = new Set(matches.map(([r]) => mapGroupKey(r.t)));
    const count = matches.length;
    setText(mapSearchStatus, count ? (count > searchOptions.length ? searchOptions.length + ' / ' : '') + count + ' kết quả' : 'Không tìm thấy');
    mapHits.innerHTML = searchOptions.map((t,i) => '<div class="mm-hit" id="mm-hit-' + i + '" role="option" aria-selected="false" data-mm-hit="' + i + '" data-mm-tier="' + t.t + '">'
      + '<span class="mm-badge">' + t.t + '</span><strong>' + esc(t.n) + '</strong><small>' + esc(mapGroupName(mapGroupKey(t))) + '</small></div>').join('');
    mapHits.hidden = !count;
    mapSearch.setAttribute('aria-expanded',String(!!count));
    paintMapFocus();
  }
  function chooseLookup(i){
    const tool = searchOptions[i];
    if (!tool) return;
    if (!editingLookup) rememberMapStep();
    editingLookup = false;
    const group = mapGroupKey(tool);
    closeLookup();
    mapSearch.value = tool.n;
    mapSearchClear.hidden = false;
    const clusterId = mapClusterOf(tool.fg || '');
    setText(mapSearchStatus, tool.t + ' \u00b7 ' + mapClusterLabels[clusterId] + ' \u00b7 ' + mapGroupName(group));
    searchTiers = new Set([tool.t]);
    searchGroups = new Set([group]);
    const panel = setMapBranch(tool.t,true);
    // The cluster wraps the group now, so it has to open before the group can.
    const cluster = [...panel.querySelectorAll('.mm-cluster')].find(el => el.dataset.mmCluster === clusterId);
    if (cluster) toggleMapCluster(cluster,true);
    const groupButton = [...panel.querySelectorAll('[data-mm-group]')].find(el => el.dataset.mmGroup === group);
    const list = setMapGroup(groupButton,true);
    // A paged group keeps later leaves out of the DOM until asked, so walk the pages.
    const button = findMapLeaf(list, tool.n);
    if (!button) return;
    // selectMapTool reveals the chip and its route together.
    selectMapTool(button, undefined, true);
  }
  // ── ISSUE 2: CHAIN PANEL ON THE LEAF + RING HIGHLIGHT ────────────────────────────
  // Anh 2026-11-24: "đem tool chain ra thẳng node ngọn, tool nào có chain thì show ra
  // thêm 1 panel báo các tool trong chain, và các tool trong chain sẽ sáng ở cây".
  // The chain already lived in #mm-detail (detailHTML :412) but that panel closes on
  // deselect, and its wires hid under the cards. The leaf panel is a SEPARATE, absolute
  // node pinned to the chip: absolute => no reflow => the fit/anchor censuses (measured to
  // 0.02px) and the stageVisible cap are untouched. One panel per selected chip, removed
  // on deselect -- never a second ghost state.
  let chainPanelEl = null, chainConnector = null, chainAnchorTool = null;
  function clearChainPanel(){
    if (chainPanelEl){ chainPanelEl.remove(); chainPanelEl = null; }
    chainConnector?.remove();
    chainConnector = null;
    chainAnchorTool = null;
    activeRoute = null;
    for (const el of mapCanvas.querySelectorAll('.mm-chain-mate')) el.classList.remove('mm-chain-mate');
  }
  // A3 (direction B): the panel is an ABSOLUTE child of #mm-canvas, so it inherits the
  // canvas zoom + pan transform and travels WITH the map. left/top are MAP coords, not
  // viewport coords: both rects below are visual (zoom and translate already applied),
  // so (chip - canvas) / zoom is the chip's position in the canvas's own coordinate
  // space -- exactly the space an absolute child's left/top live in. No viewport clamp.
  function repositionChainPanel(){
    if (!chainPanelEl || !chainAnchorTool) return;
    const chip = mapCanvas.querySelector('[data-mm-tool=\"' + chainAnchorTool + '\"]');
    if (!chip) return;
    const r = chip.getBoundingClientRect();
    if (r.width < 1) return;   // folded chip has no map position; keep the last anchor
    const cv = mapCanvas.getBoundingClientRect();
    const k = parseFloat(mapCanvas.style.zoom || '1') || 1;
    const scale = Math.max(1, 1 / k);
    chainPanelEl.style.transform = 'scale(' + scale + ')';
    // Put the terminal node past every laid-out tree column. The absolutely
    // positioned card and connector do not move the tree or any existing node.
    const chipRight = (r.right - cv.left) / k;
    let lastColumn = chipRight;
    for (const card of mapCanvas.querySelectorAll(MM_CARD_SEL)){
      const rect = card.getBoundingClientRect();
      if (rect.width) lastColumn = Math.max(lastColumn, (rect.right - cv.left) / k);
    }
    const pin = pinnedMapNodes.get(mapNodeKey(chainPanelEl));
    const left = pin ? pin.x : lastColumn + 30;
    const middle = (r.top + r.height / 2 - cv.top) / k;
    const head = chainPanelEl.querySelector('.mm-chain-panel-head').getBoundingClientRect();
    const headerMid = head.height / (2 * k) + 11 * scale;
    const naturalTop = middle - headerMid;
    const top = pin ? pin.y : chip.classList.contains('mm-placed') ? naturalTop : Math.max(0, naturalTop);
    chainPanelEl.style.left = left + 'px';
    chainPanelEl.style.top = top + 'px';
    const panelWidth = chainPanelEl.offsetWidth * scale;
    const facesLeft = pin && left + panelWidth / 2 < chipRight - r.width / (2 * k);
    const x1 = facesLeft ? chipRight - r.width / k : chipRight;
    const x2 = facesLeft ? left + panelWidth : left;
    const dx = x2 - x1, dy = pin ? top + headerMid - middle : 0;
    chainConnector.style.left = x1 + 'px';
    chainConnector.style.top = middle + 'px';
    chainConnector.style.width = Math.hypot(dx, dy) + 'px';
    chainConnector.style.transform = 'translateY(-50%) rotate(' + Math.atan2(dy, dx) + 'rad)';
  }
  let activeRoute = null;
  function renderChainPanel(name, preferredRoute){
    const routes = CHAINS.filter(route => route.tools.includes(name));
    activeRoute = routes.find(route => route.name === preferredRoute) || routes[0] || null;
    const steps = activeRoute ? activeRoute.tools : [];
    const index = steps.indexOf(name);
    const neighbours = new Set(relationEdges().filter(edge => edge.from === name || edge.to === name)
      .map(edge => edge.from === name ? edge.to : edge.from).filter(tool => !steps.includes(tool)));
    const routePicker = routes.length > 1
      ? '<select aria-label="Chọn chuỗi tool" class="mm-chain-picker">' + routes.map(route =>
        '<option' + (route === activeRoute ? ' selected' : '') + '>' + esc(route.name) + '</option>').join('') + '</select>'
      : '<strong class="mm-chain-title">' + esc(activeRoute ? activeRoute.name : name) + '</strong>';
    const timeline = steps.map((tool, i) => {
      const current = i === index;
      return '<li><button type="button" data-mm-chain-tool="' + esc(tool) + '"'
        + (current ? ' class="mm-chain-step is-current" aria-current="step"' : ' class="mm-chain-step"') + '>'
        + '<span class="mm-step-number" aria-hidden="true">' + (i + 1) + '</span>'
        + '<span>' + esc(tool) + '</span></button></li>';
    }).join('');
    const related = neighbours.size ? '<details class="mm-chain-panel-rel"><summary>Tool liên quan · ' + neighbours.size
      + '</summary>' + [...neighbours].map(tool => '<button type="button" class="mm-chain-rel" data-mm-chain-tool="'
        + esc(tool) + '">' + esc(tool) + '</button>').join('') + '</details>' : '';
    chainPanelEl.innerHTML = '<div class="mm-chain-panel-head"><span class="mm-grip" title="Kéo để di chuyển" aria-hidden="true"></span>' + routePicker
      + '<button type="button" class="mm-chain-panel-close" aria-label="Đóng chuỗi">×</button></div>'
      + (activeRoute ? '<p class="mm-chain-progress">Bước ' + (index + 1) + ' / ' + steps.length + '</p>'
        + '<ol class="mm-chain-panel-steps" aria-label="Các bước theo thứ tự">' + timeline + '</ol>'
        : '<p class="mm-chain-empty">Tool này chưa thuộc chuỗi nào.</p>') + related
      + '<button type="button" class="mm-inspect" data-mm-inspect aria-controls="mm-detail">Xem chi tiết</button>';

    for (const chip of mapCanvas.querySelectorAll('.mm-chain-mate')) chip.classList.remove('mm-chain-mate');
    for (const tool of [steps[index - 1], steps[index + 1]]){
      if (!tool) continue;
      const chip = mapCanvas.querySelector('[data-mm-tool="' + CSS.escape(tool) + '"]');
      if (chip) chip.classList.add('mm-chain-mate');
    }
  }
  function showChainPanel(name, chip, preferredRoute){
    clearChainPanel();
    if (!chip) return;
    chainPanelEl = document.createElement('div');
    chainPanelEl.className = 'mm-chain-panel';
    chainPanelEl.setAttribute('role','group');
    chainPanelEl.setAttribute('aria-label','Tool chain của ' + name);
    renderChainPanel(name, preferredRoute);
    chainPanelEl.addEventListener('change', e => {
      if (!e.target.matches('.mm-chain-picker')) return;
      rememberMapStep();
      renderChainPanel(name, e.target.value);
      chainPanelEl.querySelector('.mm-chain-picker').focus({preventScroll:true});
      syncMapBranchLayout();
    });
    chainConnector = document.createElement('span');
    chainConnector.className = 'mm-chain-connector';
    chainConnector.setAttribute('aria-hidden', 'true');
    mapCanvas.append(chainConnector, chainPanelEl);
    chainAnchorTool = name;
    repositionChainPanel();
  }
  function selectMapTool(button, preferredRoute, reveal = false){
    const camera = panTotal();
    cancelMapPan();
    closeMapDetail();
    selectBranch(null);
    hoveredCard = null;
    mapSelected = button.dataset.mmTool;
    button.setAttribute('aria-current','true');
    showChainPanel(mapSelected, button, preferredRoute);
    syncMapBranchLayout();
    if (restoringMapStep) return;
    if (reveal){
      button.focus({preventScroll:true});
      revealMapNode(button, chainPanelEl);
    } else {
      // A click on a visible leaf already specifies where the user is looking.
      // Swapping terminal cards can temporarily shorten the native scroll range.
      setPanTotal(camera.x, camera.y);
    }
  }
  function openMapInfo(moveFocus = true){
    const tool = mapToolIndex.get(mapSelected);
    if (!tool) return;
    const path = [['H\u1EC6 TH\u1ED0NG','ToolA'],[tool.t,mapTierNames.get(tool.t)],
      ['C\u1EE4M',mapClusterLabels[mapClusterOf(tool.fg || '')]],
      ['NH\u00d3M',mapGroupName(mapGroupKey(tool))],['TOOL',tool.n]];
    const badges = ['<span class="badge badge-' + tool.t.toLowerCase() + '">' + tool.t + '</span>']
      .concat(tool.mut === true ? '<span class="badge badge-mut">ghi</span>' : [])
      .concat(tool.prot === true ? '<span class="badge badge-protect">bảo vệ</span>' : [])
      .concat(tool.dep ? '<span class="badge badge-dep">deprecated</span>' : [])
      .concat(tool.miss ? '<span class="badge badge-miss">missing</span>' : [])
      .concat(tool.err ? '<span class="badge badge-err-' + tool.err.c + '">errata ' + tool.err.c + '</span>' : [])
      .join('');
    mapDetail.innerHTML = '<div class="mm-detail-head">'
      + '<div class="mm-detail-id"><strong>' + esc(tool.n) + '</strong>' + badges + '</div>'
      + '<p data-mm-count></p>'
      + '<button type="button" data-mm-close>Đóng</button></div>'
      + '<div class="mm-path" data-mm-tier="' + tool.t + '" aria-label="Vị trí công cụ trong bản đồ">'
      + path.map(([label,value]) => '<div class="mm-path-node"><small>' + esc(label) + '</small><strong>' + esc(value) + '</strong></div>').join('') + '</div>'
      + detailHTML(tool, {slim: true}) + relationPanel([mapSelected])
      + '<button type="button" class="filter-btn" data-mm-inventory>Xem trong danh mục</button>';
    mapDetail.hidden = false;
    showMapCount(mapDetail.querySelector('[data-mm-count]'), liveState.mapCounts?.tools.get(mapSelected));
    if (moveFocus){
      mapDetail.focus({preventScroll:true});
    }
  }

  function setText(node, value){
    if (node.textContent !== value) node.textContent = value;
  }
  function isCompleted(e){
    const status = typeof e?.status === 'string' ? e.status.toLowerCase() : '';
    return e?.state === 'applied' && (status === 'ok' || terminalErrors.has(status));
  }
  function mapCounts(events, stamp){
    if (!Number.isFinite(stamp) || stamp <= 0) return null;
    const zero = () => ({calls:0, errors:0});
    const counts = {stamp, total:zero(), unmatched:0, tiers:new Map(), groups:new Map(), tools:new Map()};
    const add = (index, key, failed) => {
      if (!index.has(key)) index.set(key, zero());
      const row = index.get(key);
      row.calls++;
      if (failed) row.errors++;
    };
    for (const e of events){
      const status = typeof e.status === 'string' ? e.status.toLowerCase() : '';
      const failed = terminalErrors.has(status);
      // Pending or unclassified events are not completed calls; don't count future timestamps.
      if (!isCompleted(e) || !Number.isFinite(e.epoch) || e.epoch <= stamp - 300 || e.epoch > stamp) continue;
      counts.total.calls++;
      if (failed) counts.total.errors++;
      const t = mapToolIndex.get(e.tool);
      if (!t || !byTier[t.t]) { counts.unmatched++; continue; }
      add(counts.tiers, t.t, failed);
      add(counts.groups, t.t + '|' + (t.fg || ''), failed);
      add(counts.tools, t.n, failed);
    }
    return counts;
  }
  function showMapCount(el, row, prefix = ''){
    setText(el, prefix + (liveState.mapCounts ? (row?.calls || 0) + ' lượt · ' + (row?.errors || 0) + ' lỗi' : neverKnown));
    el.toggleAttribute('data-alert', liveState.status === 'live' && !!row?.errors);
    if (el.hasAttribute('data-alert')) el.dataset.alert = 'true';
  }
  function renderMap(){
    const {status, mapCounts:counts, services, serviceStatus} = liveState;
    if (mapSource.dataset.state !== status) mapSource.dataset.state = status;
    setText(mapSource, mapStatusLabels[status]);
    showMapCount(mapTotal, counts?.total);
    const maxCalls = Math.max(1,...mapButtons.map(b => counts?.tiers.get(b.dataset.mmTier)?.calls || 0));
    for (const button of mapButtons){
      const row = counts?.tiers.get(button.dataset.mmTier);
      showMapCount(button.querySelector('[data-mm-count]'),row);
      const width = status === 'live' && row ? Math.round(100 * row.calls / maxCalls) + '%' : '0%';
      if (button.style.getPropertyValue('--mm-activity') !== width) button.style.setProperty('--mm-activity',width);
      button.toggleAttribute('data-mm-errors', status === 'live' && !!row?.errors);
    }
    for (const {panel} of mapBranches.values()){
      if (panel.hidden) continue;
      for (const el of panel.querySelectorAll('[data-mm-group-count]')){
        const group = groupByKey(el.dataset.mmGroupCount);
        showMapCount(el, counts?.groups.get(group.key), group.tools.length + ' tool · ');
      }
    }
    if (!mapDetail.hidden) showMapCount(mapDetail.querySelector('[data-mm-count]'), counts?.tools.get(mapSelected));
    for (const [name, el] of Object.entries(serviceNodes)){
      const service = services[name];
      let state = '', text = 'Chưa rõ', reason = 'Feed chưa cung cấp dữ liệu tình trạng cho dịch vụ này.';
      if (status === 'connecting') { text = 'Đang kiểm tra'; reason = 'Đang chờ mẫu từ Monitor.'; }
      else if (status === 'offline') reason = 'Không lấy được dữ liệu Monitor; chưa thể kết luận dịch vụ đang chạy hay đã dừng.';
      else if (serviceStatus && serviceStatus !== 'fresh') { state = 'warn'; text = 'Dữ liệu cũ / thiếu'; reason = 'Chưa có mẫu dịch vụ đủ mới để xác nhận tình trạng hiện tại.'; }
      else if (serviceStatus === 'fresh' && service){
        if (service.up === false) { state = 'bad'; text = 'Không phản hồi'; reason = 'Monitor không nhận được phản hồi từ dịch vụ.'; }
        else if (service.level === 'bad') { state = 'bad'; text = 'Lỗi'; reason = 'Monitor báo lỗi dịch vụ; chưa xác nhận dịch vụ đã dừng.'; }
        else if (service.status === 'degraded' || service.level === 'warn' || service.ready === false || service.semantic_ready === false) {
          state = 'warn'; text = 'Suy giảm';
          if (name === 'ToolQ' && service.semantic_ready === false) reason = 'ToolQ vẫn phản hồi; tìm kiếm ngữ nghĩa chưa sẵn sàng.';
          else if (name === 'ToolA' && service.ready === false) reason = 'ToolA vẫn phản hồi nhưng báo chưa sẵn sàng.';
          else reason = 'Monitor báo suy giảm' + (service.reason?.trim?.() ? '.' : ' nhưng không cung cấp nguyên nhân.');
        } else if (service.up === true) { state = 'ok'; text = 'Đang hoạt động'; reason = 'Monitor nhận được phản hồi và không báo suy giảm.'; }
        if (state === 'bad' || state === 'warn'){
          if (typeof service.reason === 'string' && service.reason.trim()) reason += ' Monitor: ' + service.reason.trim();
          if (Array.isArray(service.missing_fields) && service.missing_fields.length) reason += ' Thiếu trường: ' + service.missing_fields.join(', ') + '.';
        }
      }
      if (el.dataset.serviceState !== state) el.dataset.serviceState = state;
      setText(el,text);
      setText(document.getElementById(el.id + '-reason'),reason);
    }
    const sampleText = counts
      ? (status === 'live' ? 'Mốc dữ liệu ' : 'Giữ mẫu lúc ') + serviceSnapshotTime.format(counts.stamp * 1000)
        + (counts.unmatched ? ' · ' + counts.unmatched + ' lượt không phân tier' : '')
        + ' · Mẫu giới hạn, không phải tổng lưu lượng.'
      : 'Chưa có mẫu hoạt động dùng được. Danh mục luôn xem được.';
    setText(mapSample, sampleText);
  }
  // --------------------------------------------------------------------------------
  // THE COLLAPSE SHIELD -- issue 1: "khi tra node ngon back ve lai thi bi giat".
  //
  // Measured (clamp_probe.py, four ARM runs, dispatchEvent and raw coordinates only, so
  // the harness cannot auto-scroll its own target -- CONTROL 0 proved locator.click() does
  // exactly that and every earlier return-gesture number it touched is contaminated):
  //   ARM 1  start y=0,  fold T2   -> moved  0px   (a clamp cannot take anything away from 0)
  //   ARM 2  start y=0,  close x   -> moved  0px   (so there is NO scrollTo and no hidden
  //                                                  app-side writer on the return gesture)
  //   ARM 3  start y=250, close x  -> landed 117 = exactly the new maxScroll
  //   ARM 4  start y=0,  select    -> 0..483 in ONE frame, docH 1017..1407
  // So both jumps are the DOCUMENT HEIGHT changing inside one frame: going back, the
  // browser clamps scrollY to the new ceiling in the same frame the panel leaves the flow,
  // and the whole tree snaps. The invariant that removes it is therefore not "animate the
  // scroll" -- it is: the document height must never FALL in one frame.
  //
  // glideHandler runs the collapse, measures how much height it took away, hands that exact
  // amount back as a spacer at the foot of the panel, then drains the spacer. Nothing here
  // ever calls scrollTo: the browser clamp does the movement, one small step per frame, which
  // is why the result is a glide instead of a second animation fighting the page. The spacer
  // sits at the FOOT, so the tree never reflows during the drain -- only the bottom edge of
  // the document rises and scrollY walks home on its own.
  //
  // prefers-reduced-motion: reduce returns the OLD instant clamp. Every motion test in the
  // suite runs under reduce, so this branch keeps their numbers byte-identical instead of
  // making them race a 500ms shield.
  let glideSpacer = null, glideRaf = 0, glideFrom = 0, glideT0 = 0, glideDur = 0;
  const glideEase = p => 1 - Math.pow(1 - p, 3);            // easeOutCubic, the reveal pan curve
  function glideStep(now){
    glideRaf = 0;
    if (!glideSpacer) return;
    // Wall-clock deadline: a hidden tab pauses rAF entirely, so on resume now is far past
    // the budget and the shield drops at once instead of gliding an inch per frame.
    const spent = now - glideT0;
    const p = (glideDur <= 0 || spent >= 2000) ? 1 : Math.min(1, spent / glideDur);
    glideSpacer.style.height = Math.round(glideFrom * (1 - glideEase(p))) + 'px';
    if (p >= 1){
      glideSpacer.remove();
      glideSpacer = null; glideFrom = 0; glideDur = 0;
      return;
    }
    glideRaf = requestAnimationFrame(glideStep);
  }
  function glideHold(px){
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    if (!glideSpacer){
      glideSpacer = document.createElement('div');
      glideSpacer.id = 'mm-glide-spacer';
      glideSpacer.setAttribute('aria-hidden', 'true');
      glideSpacer.style.cssText = 'pointer-events:none;height:0';
      // The foot of the active panel, exactly where #mm-detail sat. Anywhere higher and the
      // shield would push the tree it is meant to hold still.
      (mapDetail.parentElement || mapRoot).appendChild(glideSpacer);
    }
    // P1 (cross-audit): a collapse DURING a running glide gets the spacer height it
    // actually carries TODAY, not the original fell. glideFrom is reduced every frame by
    // glideStep, so adding px to it would jump the spacer UP by the portion already drained
    // and read as a reversed snap. Read the live height instead.
    const carried = parseFloat(glideSpacer.style.height) || 0;
    glideFrom = carried + px;
    glideSpacer.style.height = Math.round(glideFrom) + 'px';
    glideDur = Math.min(620, Math.max(240, 200 + glideFrom * 0.7));
    glideT0 = performance.now();
    if (!glideRaf) glideRaf = requestAnimationFrame(glideStep);
  }
  function glideHandler(fn){
    return (...args) => {
      // Reduced motion is the OLD behaviour, untouched: no measure, no spacer, the instant
      // clamp. Every motion test in the suite runs under reduce, so this branch keeps their
      // numbers exactly as they are today instead of racing them with a 500ms shield.
      if (matchMedia('(prefers-reduced-motion: reduce)').matches) return fn(...args);
      const before = document.documentElement.scrollHeight;
      const y0 = window.scrollY;
      let result;
      try { result = fn(...args); }
      finally {
        const fell = before - document.documentElement.scrollHeight;
        // 4px: a rounded re-measure of the canvas is not a collapse, and shielding one would
        // hang a node off the panel for nothing. No return in this clause -- a return inside
        // finally discards the value the try was handing back.
        if (fell > 4) {
          // Mount the shield FIRST, then undo the clamp: the document only becomes tall
          // enough to hold y0 again once the spacer is in the flow, and a scrollTo past the
          // ceiling is silently dropped. The order matters -- measuring the fall and then
          // compensating left the mid-fn clamp standing (GESTURE 2, fold T2, 507px in one
          // frame, before this line existed), because setMapBranch forces a layout inside fn
          // at the moment the chips are already hidden. All of it runs in the same
          // synchronous task, so no frame is painted at the clamped position and the reader
          // never sees the jump -- the drain is the only movement left, and it is a glide.
          try {
            glideHold(fell);
            if (window.scrollY !== y0) window.scrollTo(0, y0);
          } catch (e) {
            // P1-1 (cross-audit): spacer removal lives only in glideStep at p>=1. If mount
            // or scrollTo throws here, that removal never runs and the spacer pins the
            // document height open forever; every later collapse stacks onto the same log.
            // Tear it all down so the page stays clean.
            if (glideSpacer){ glideSpacer.remove(); glideSpacer = null; }
            glideFrom = 0; glideDur = 0;
            if (glideRaf){ cancelAnimationFrame(glideRaf); glideRaf = 0; }
          }
        }
      }
      return result;
    };
  }
  function closeMapDetail({returnFocus = false} = {}){
    if (!mapSelected) return;
    const button = mapRoot.querySelector('[data-mm-tool][aria-current="true"]');
    if (button){
      button.removeAttribute('aria-current');
      // The focus itself is the contract (:1796 requires the chip to hold it after Escape).
      // preventScroll drops only the scroll that came attached: cause_probe traced this call
      // as an app-side writer, scrollY 483..117, and the glide now owns that movement.
      if (returnFocus){
        button.focus({preventScroll:true});
        // C4: paint-only return flash so Anh can find the chip focus landed on. Skipped
        // under reduce; removed by timer so it never becomes ghost state.
        if (!matchMedia('(prefers-reduced-motion: reduce)').matches){
          button.classList.add('mm-return-flash');
          clearTimeout(button._mmFlashTimer);
          button._mmFlashTimer = setTimeout(() => button.classList.remove('mm-return-flash'), 2500);
        }
      }
    }
    mapSelected = null;
    mapDetail.hidden = true;
    mapDetail.replaceChildren();
    paintMapFocus();
    // THE OVERLAY HAS TO GO WITH THE SELECTION, and this is the second half of that
    // contract. Left standing, it would be a road to a tool nobody picked -- the same
    // class of ghost state as a lit chip whose panel has closed. It clears the layer
    // directly instead of asking for a layout pass, because nothing moved: a redraw here
    // would measure 260 rects to conclude there is nothing to draw.
    clearMapRelationLayer();
    clearChainPanel();
  }
  function syncMapBranchLayout(){
    if (!mapCanvas.offsetWidth) return;
    positionPinnedCards();
    // The CTM scale is divided by the measured box scale, which is 1 in
    // Chromium and 1/k in Firefox. Firefox leaves ancestor CSS zoom out of
    // getScreenCTM (ctm.a stayed 1.000 while the box measured 0.875) yet it
    // does scale the drawing, so a zoomed rect mapped through that inverse
    // drifts every wire by (1 - k) * x -- up to 83px off a tier edge -- and
    // the wires land on the cards they are meant to cross. The SVG carries no
    // viewBox and fills the canvas box, so its true scale is box width over
    // client width; only the scale is rewritten, ctm.e/f are the user-space
    // origin and stay correct in either engine.
    const ctm = mapWires.getScreenCTM();
    const box = mapWires.getBoundingClientRect();
    const unzoomed = mapWires.clientWidth || mapCanvas.offsetWidth;
    const corr = (unzoomed && ctm.a) ? box.width / unzoomed / ctm.a : 1;
    const transform = new DOMMatrix([ctm.a * corr, ctm.b, ctm.c, ctm.d * corr, ctm.e, ctm.f]).inverse();
    const point = (node,side) => {
      const rect = node.getBoundingClientRect();
      return new DOMPoint(rect[side], (rect.top + rect.bottom) / 2).matrixTransform(transform);
    };
    const gridOf = node => node.closest('.mm-tools, .mm-cluster-body');
    // Track index by measured x, not by index arithmetic: an auto-placement grid
    // with a short last row still reports the same set of column left edges.
    const trackRank = (child) => {
      const grid = gridOf(child);
      if (!grid) return 0;
      const lefts = new Set();
      for (const el of grid.children) {
        const r = el.getBoundingClientRect();
        if (r.width > 0) lefts.add(Math.round(r.left));
      }
      return Math.max(0, [...lefts].sort((a,b) => a - b).indexOf(Math.round(child.getBoundingClientRect().left)));
    };
    // Every card on the map, measured once per draw. The router needs the whole
    // set, not just the grid it is routing inside: with two group columns a lane
    // above a row sits inside the NEIGHBOURING group's chips.
    const mapNodeRects = [...mapCanvas.querySelectorAll(
      '.mm-root, .mm-tier, .mm-cluster-btn, .mm-group > button, .mm-tools button')];
    const nodeRects = mapNodeRects.map(n => n.getBoundingClientRect()).filter(r => r.width >= 1);
    const busPoints = (parent,child) => {
      const pr = parent.getBoundingClientRect(), cr = child.getBoundingClientRect();
      const midY = r => (r.top + r.bottom) / 2;
      const grid = gridOf(child);
      // Rows the grid actually occupies, measured from its own children. A grid
      // may end in a full-width tail (.mm-more) spanning every track, so a lane
      // taken outside the grid without measuring it runs across that tail.
      const bandMap = new Map();
      for (const el of (grid ? grid.children : [])) {
        const r = el.getBoundingClientRect();
        if (r.width < 1) continue;
        const top = Math.round(r.top);
        bandMap.set(top, Math.max(bandMap.get(top) || 0, Math.round(r.bottom)));
      }
      const tops = [...bandMap.keys()].sort((a,b) => a - b);
      // Candidate lanes, shortest detour first: the child's own row (a straight
      // shot, what a second-track GROUP sharing its parent's row wants), the gap
      // between two measured rows, then open air above the first row and below
      // the last. A single-row grid has NO interior gap, and the router used to
      // fall back to the child's midY there -- the very row a first-track chip
      // occupies -- so the stroke went straight through it. Measured: 165 of 192
      // samples of Desktop -> ui_state_summary inside folder_dialog_snapshot.
      const lanes = [midY(cr)];
      for (let i = 1; i < tops.length; i++) lanes.push((bandMap.get(tops[i - 1]) + tops[i]) / 2);
      if (tops.length) lanes.push(tops[0] - 7, bandMap.get(tops[tops.length - 1]) + 7);
      const jog = pr.right + Math.min(13, Math.max(6, (cr.left - pr.right) / 2));
      const colLane = cr.left - 2;
      // Clear of every card, not just of this grid. The 1px margin exempts the
      // endpoints by construction: the run starts ON the parent's right edge and
      // stops 2px before the child's, so neither own card can fail its own test.
      const clear = (y,x0,x1) => {
        for (const r of nodeRects)
          if (y > r.top + 1 && y < r.bottom - 1 && x1 > r.left + 1 && x0 < r.right - 1) return false;
        return true;
      };
      let rowLane = null, best = Infinity;
      for (let i = 0; i < lanes.length; i++) {
        const y = lanes[i], d = Math.abs(y - midY(cr));
        if (d >= best) continue;
        // The straight candidate spans the whole corridor; a bus candidate only
        // spans jog -> colLane, because its vertical runs live in the gutters.
        const straight = i === 0;
        if (clear(y, straight ? pr.right : jog, straight ? cr.left : colLane)) { rowLane = y; best = d; }
      }
      if (rowLane === null) rowLane = midY(cr);     // nowhere clear: stay short
      if (Math.abs(rowLane - midY(pr)) < 2 && Math.abs(rowLane - midY(cr)) < 2)
        return [[pr.right, midY(pr)], [cr.left, midY(cr)]].map(([x,y]) => screenPoint(x, y));
      return [[pr.right, midY(pr)], [jog, midY(pr)], [jog, rowLane],
              [colLane, rowLane], [colLane, midY(cr)], [cr.left, midY(cr)]]
        .map(([x,y]) => screenPoint(x, y));
    };
    const screenPoint = (x,y) => new DOMPoint(x, y).matrixTransform(transform);
    const paths = document.createDocumentFragment();
    const previousHover = hoveredCard;
    clearHoverPaint();
    wireEntries.length = 0;
    nodeWires.clear();
    const emit = (parent,child,d,tier,kind,group) => {
      const path = document.createElementNS('http://www.w3.org/2000/svg','path');
      path.setAttribute('d', d);
      path.setAttribute('class','mm-' + kind + '-wire');
      path.dataset.mmTier = tier;
      if (group) path.dataset.mmGroup = group;
      // The hit twin carries the class the census tests count, so it must NOT
      // reuse mm-<kind>-wire: test_matrix_horizontal_edges_zoom_and_independent_branches
      // asserts .mm-tool-wire == the visible leaf count and .mm-cluster-wire == 5.
      const hit = document.createElementNS('http://www.w3.org/2000/svg','path');
      hit.setAttribute('d', d);
      hit.setAttribute('class','mm-wire-hit');
      hit.dataset.mmKind = kind;
      hit.setAttribute('aria-hidden','true');
      const entry = { path, hit, a:parent, b:child, kind, tier, group };
      wireEntries.push(entry);
      if (!nodeWires.has(parent)) nodeWires.set(parent, []);
      if (!nodeWires.has(child)) nodeWires.set(child, []);
      nodeWires.get(parent).push(entry);
      nodeWires.get(child).push(entry);
      paths.append(path, hit);
    };
    // SIBLING LANES. Every wire a parent feeds used to share one control x --
    // mid = (start.x + end.x) / 2, identical for all of them because the corridor is
    // the same width at both ends -- so a cubic whose endpoints are 26px apart and
    // whose vertical travel is hundreds of px is, to the eye, a straight line, and
    // four of them landed on the same pixels. Measured on the shape census: EVERY wire
    // of EVERY kind has xSpan 26 (nothing crosses a card), yet all four tier wires
    // carry their steepest section at x=187 across 682px, and the six Desktop stems
    // at x=801 down the whole 612px rail. That fused ruler beside a column is what Anh
    // photographs as 'wire bi chen thang qua cot tiep theo', and it happens at all four
    // levels, which is why he sees it in 'rat nhieu khu vuc'. So each sibling gets its
    // own lane inside the corridor it already travels: the endpoints stay exactly
    // where the geometry locks pin them (parent right, child left -- the horizontal
    // edge test measures those two points and nothing else), and only the control
    // x's move. The whole curve stays inside [start.x, end.x] because a cubic never
    // leaves the convex hull of its control points, so midHits cannot regress.
    // The fan spreads across the corridor it already travels, but it is BUDGETED FROM
    // THE EDGES, not capped in the middle. A flat cap (Math.min(20, ...)) was the first
    // version and it failed twice on the same measurement: at 1440 it left the six
    // bucket arcs 2px apart -- still one line to the eye -- and in a 16px mobile
    // corridor a symmetric +-10px swing put the outer lanes 2.9px off a card edge,
    // which is the 'wire chen thang qua cot' look even though the wire is legally in
    // the gutter. So the usable span is the corridor minus LANE_CLEAR on each side,
    // and the outermost lane is guaranteed that clearance at ANY corridor width:
    // 34px desktop -> span 20, so +-10 from the middle: 7px clear at each end and a
    // 6.7px step between the four tier lanes. 26px mobile -> span 12, +-6, the SAME
    // 7px clearance with a 4px step. bus_meter.py's FAN census measured minClear 7
    // and distinct == n for every parent in all 38 states at both widths. Still
    // inside [start.x, end.x], because the offset
    // never exceeds half the corridor, so the cubic stays in its convex hull and
    // midHits cannot regress.
    const LANE_CLEAR = 7;
    const laneOffset = (lane, budget) => (!lane || lane.n <= 1) ? 0
      : (lane.i / (lane.n - 1) - 0.5) * Math.max(0, Math.abs(budget) - LANE_CLEAR * 2);
    // Manually placed endpoints can be above, below or behind their old parents.
    // Choose facing ports so the connector does not cross its own node.
    const connectPlaced = (parent, child, tier, kind, group) => {
      const a = parent.getBoundingClientRect(), b = child.getBoundingClientRect();
      const ax = (a.left + a.right) / 2, ay = (a.top + a.bottom) / 2;
      const bx = (b.left + b.right) / 2, by = (b.top + b.bottom) / 2;
      const horizontal = Math.abs(bx - ax) - (a.width + b.width) / 2 >= Math.abs(by - ay) - (a.height + b.height) / 2;
      const start = screenPoint(horizontal ? (bx >= ax ? a.right : a.left) : ax, horizontal ? ay : (by >= ay ? a.bottom : a.top));
      const end = screenPoint(horizontal ? (bx >= ax ? b.left : b.right) : bx, horizontal ? by : (by >= ay ? b.top : b.bottom));
      const x = (start.x + end.x) / 2, y = (start.y + end.y) / 2;
      const controls = horizontal ? [x, start.y, x, end.y] : [start.x, y, end.x, y];
      emit(parent, child, 'M ' + start.x + ' ' + start.y + ' C ' + controls.join(' ') + ' ' + end.x + ' ' + end.y, tier, kind, group);
    };
    const connect = (parent,child,tier,kind,group,lane) => {
      if (parent.classList.contains('mm-placed') || child.classList.contains('mm-placed')){
        connectPlaced(parent, child, tier, kind, group);
        return;
      }
      const start = point(parent,'right'), end = point(child,'left');
      // A child in the second track of its grid cannot take the straight arc:
      // that arc is drawn across every card of the first track, and the wire
      // layer paints above those cards. Such a wire is routed as a bus through
      // the two gutters the layout already leaves empty - between the tracks,
      // then between two rows - so the stroke never lands on a card. Both
      // endpoints stay on the parent right and child left midpoints, which is
      // all the geometry test measures. One column at both axes now makes this
      // branch unreachable; it is kept as the safety net the trackRank helper
      // documents, not as live geometry.
      let d;
      if (trackRank(child) > 0) {
        d = 'M ' + busPoints(parent,child).map(pt => pt.x + ' ' + pt.y).join(' L ');
      } else {
        const mid = (start.x + end.x) / 2 + laneOffset(lane, Math.abs(end.x - start.x));
        d = 'M ' + start.x + ' ' + start.y + ' C ' + mid + ' ' + start.y + ', ' + mid + ' ' + end.y + ', ' + end.x + ' ' + end.y;
      }
      emit(parent, child, d, tier, kind, group);
    };
    // PREFIX -> TOOL. This level is NOT a left-to-right edge: a bucket head shares
    // the rail's left edge with its chips by construction -- that is what keeps the
    // canvas from growing sideways, and what the rail test locks as ONE shared left
    // edge -- so the children sit BELOW the parent, and a bezier from head.right to
    // chip.left would run 170px backwards across the head's own card. A vertical run
    // inside the rail column is worse: it crosses every chip above the one it serves.
    // So this level is drawn as a tree: ONE vertical spine, and a tick into each chip.
    //
    // THE SPINE RUNS TO THE RIGHT OF THE RAIL (Anh 2026-11-23: "wire thang cua node ngon
    // prefix chuyen sang ben phai di. hien tai dang chong cheo voi wire cong"). It used
    // to run down the MIDDLE of the left gutter, on the claim that landing on the apex
    // of the group->head arc made a section read as one continuous line into the stem.
    // That claim IS the defect, and it is arithmetic: the arc's control x is
    // (groupRight + railLeft)/2 and the old trunkInset put the spine at
    // railLeft - corridor/2 -- THE SAME X -- so every group->head arc hugged the spine
    // along its whole length. runway_probe.py counted it on the longest rail in the map:
    // OVERLAPPING_ARCS = 6, six .mm-bucket-wire arcs within 3px of the spine across a
    // ~530px run, identically at 1440 and at 390. Two ink lines on one another is not a
    // continuous line, it is a thicker one, and that is what he photographed.
    //
    // Moving the spine right deletes the coincidence instead of hiding it: the left
    // gutter keeps the arcs and nothing else, and the spine runs in air to the right of
    // the column, where there is no neighbouring column at all (one column everywhere
    // since syncMapBranchLayout) so it cannot be mistaken for one either. This is a
    // RELOCATION, not a re-shape -- the wire still leaves a card edge, still runs
    // perpendicular into one, still four 'L' vertices -- so the rail test's arcs == 0
    // and bus == wires locks stand untouched, and no panel or border is needed.
    //
    // The runway is a CSS variable, because the layout and the geometry must not drift:
    // .mm-canvas reserves max(--mm-pad, --mm-trunk-runway) of padding-right, and that
    // reservation is what keeps the spine INSIDE the SVG box -- the wire layer is
    // inset:0 on the canvas padding box, so a pastedboard edge clips the stroke rather
    // than merely centreing it badly. Measured before the move: railRight 1214 against
    // canvasRight 1240 at 1440 (26px of pad) but 368 against 376 at 390 (8px), so the
    // mobile gutter alone would have clipped it. The spine sits at 0.625 of the runway
    // so 6px of air is left for a 1.65px stroke: 16px runway -> 10px in, 6px out.
    const trunkRunway = () => {
      const v = parseFloat(getComputedStyle(mapCanvas).getPropertyValue('--mm-trunk-runway'));
      return Number.isFinite(v) && v > 0 ? v : 16;
    };
    const connectTrunk = (head, chips, tier, group, lane) => {
      const hr = head.getBoundingClientRect();
      // The rail's RIGHT edge. Head and chips share a left edge and a width, so they
      // share the right one too and either extreme describes the column; the spine is
      // measured off the edge it actually clears.
      const railRight = Math.max(...chips.map(c => c.getBoundingClientRect().right));
      const runway = trunkRunway();
      const trunkX = railRight + Math.round(runway * 0.625);
      // ONE spine per bucket, and NO sideways fan -- the fan was written once and then
      // measured away (clear_probe: STEM-OVERLAP 0 at BOTH widths, because a head always
      // sits above its own chips, so six stems on six x's were six parallel lines nobody
      // could tell apart, and on one x they read as a spine with branches).
      const startY = (hr.top + hr.bottom) / 2;
      // THE TWO CORNERS ARE ROUNDED (Anh 2026-11-23: 'van con sot 1 so wire vuong').
      // elbow_probe named the producer exactly: 31 squared wires, every one of them
      // mm-tool-wire, every one of them in a bucketed rail, 90 degrees each. They are
      // this loop's own polyline -- out along the tick, down the spine, back along the
      // tick -- and it is the ONLY square-cornered producer left on the map, because
      // every other level is a cubic. Atom draws all of its connectors as cubics and so
      // has no corner at all; here a cubic is the wrong repair, since a bezier between
      // two points on one vertical would bow sideways out of the runway. A quarter arc
      // keeps the spine exactly where the rail test measures it and takes only the
      // vertex away.
      //
      // WHY AN ARC AND NOT MORE 'L' SEGMENTS: a chamfer (L x L) still has two vertices
      // and the heading census still reads two turns of 45 degrees, which at a 2px
      // sample step is 1.0 rad -- louder than the 0.6 rad threshold the census uses, so
      // a chamfer would be reported as squared wire forever. A quarter circle of radius
      // r turns 2/r rad per 2px step: r=6 gives 0.33 rad (19 deg), comfortably under.
      //
      // THE RADIUS IS CAPPED BY THE GEOMETRY, NOT CHOSEN. The horizontal run is
      // trunkX - railRight = round(runway * 0.625) = 10px at both widths, and the arc
      // eats r of it at EACH end, so r <= 10 keeps the arc inside the air and r <= 10 -
      // TICK_MIN keeps a visible stub of tick so the wire still reads as attaching to the
      // card edge rather than dissolving into it. The bulge reaches x = trunkX - r,
      // which at r=6 is railRight + 4: still RIGHT of every card, so midHits cannot
      // regress and the group -> head arcs in the left gutter are never touched.
      // |dy| / 2 caps the pair against a chip that sits almost level with its head.
      const MAX_R = 6, TICK_MIN = 3;
      const hh = trunkX - hr.right;
      const P = (x, y) => { const p = screenPoint(x, y); return p.x + ' ' + p.y; };
      for (const chip of chips){
        const cr = chip.getBoundingClientRect();
        const midY = (cr.top + cr.bottom) / 2;
        const dy = midY - startY;
        const r = Math.min(MAX_R, Math.max(0, hh - TICK_MIN), Math.abs(dy) / 2);
        if (r < 1){
          // Almost level, or no air to round inside: the plain polyline is honest there.
          emit(head, chip, 'M ' + P(hr.right, startY) + ' L ' + P(trunkX, startY)
            + ' L ' + P(trunkX, midY) + ' L ' + P(cr.right, midY), tier, 'tool', group);
          continue;
        }
        const k = dy > 0 ? 1 : -1;          // which way the spine runs
        const sweep = dy > 0 ? 1 : 0;       // screen-clockwise both times when it runs down
        // Radii are mapped through the same transform as the points, so an elliptical
        // arc is the exact image of a circle under the page's own scale -- the canvas
        // can be zoomed, and a radius typed in screen px would then cut the corner in
        // the wrong place. Differences cancel the matrix translation.
        const o = screenPoint(trunkX, 0);
        const rx = Math.round(Math.abs(screenPoint(trunkX - r, 0).x - o.x) * 100) / 100;
        const ry = Math.round(Math.abs(screenPoint(trunkX, r).y - o.y) * 100) / 100;
        const arc = ' A ' + rx + ' ' + ry + ' 0 0 ' + sweep + ' ';
        emit(head, chip,
          'M ' + P(hr.right, startY)
          + ' L ' + P(trunkX - r, startY) + arc + P(trunkX, startY + k * r)
          + ' L ' + P(trunkX, midY - k * r) + arc + P(trunkX - r, midY)
          + ' L ' + P(cr.right, midY), tier, 'tool', group);
      }
    };
    // Column counts are set here, not at build time: one place that also runs on
    // resize and zoom. Explicit counts because the canvas is width:max-content,
    // where an auto-fit track collapses back to one tall rail.
    //
    // ONE column at BOTH axes, always (Anh 2026-11-23: "wire bi chen thang qua cot
    // tiep theo. sap xep lai het 100%"). The leaf axis was collapsed for exactly this
    // complaint and the group axis was deliberately left standing on the claim that
    // busPoints would detour it. Measured on the state Anh screenshotted -- T1 open,
    // cluster[1] open, both groups open -- .mm-cluster-body#mm-cluster-T1-1 really does
    // carry two tracks (left edges 656 and 1027), so a group wire into track two travels
    // across the first track's column. The bus router keeps the STROKE off the CARDS,
    // which is all midHits measures, and that is why every census in this campaign stood
    // green while he saw a wire cutting through the next column: a detour inside someone
    // else's rail is not a layout. One column deletes the geometry that needs a detour,
    // and the height it costs is no longer scarce because the pan is unbounded.
    // Retired with the knob: 'wide', the mostGroups census that sized it, and
    // 'leavesOf', which had no caller left once chips moved two levels down into
    // .mm-bucket-body. trackRank / busPoints stay as the safety net -- with one track
    // everywhere trackRank returns 0 and they are never reached, but if a second track
    // ever reappears the stroke still misses every card, and the second-track test now
    // fails loudly instead of silently re-enabling a detour.
    const leafCols = '1';
    const groupCols = '1';
    if (mapCanvas.style.getPropertyValue('--mm-leaf-cols') !== leafCols) mapCanvas.style.setProperty('--mm-leaf-cols', leafCols);
    if (mapCanvas.style.getPropertyValue('--mm-group-cols') !== groupCols) mapCanvas.style.setProperty('--mm-group-cols', groupCols);
    // One wire per chip, whatever the rail's shape: a plain rail hangs its chips off
    // the GROUP card, a bucketed rail hangs them off the PREFIX node. Both are
    // counted as .mm-tool-wire, because "a wire into a tool" is what every census in
    // this campaign counts -- splitting the kind by rail shape would make the leaf
    // count a function of the bucket table.
    const leafEdges = (groupButton, list, tier, group) => {
      // Lane denominator counts EVERY child this parent feeds, plain chips and prefix
      // nodes together, so a mixed rail still spreads one lane per stroke instead of
      // restarting the sequence halfway down.
      const plain = [...list.querySelectorAll(':scope > [data-mm-tool]')];
      const buckets = [...list.querySelectorAll(':scope > .mm-bucket')];
      const n = plain.length + buckets.length;
      plain.forEach((chip, i) => connect(groupButton, chip, tier, 'tool', group, {i, n}));
      buckets.forEach((bucket, bi) => {
        const lane = {i: plain.length + bi, n};
        const head = bucket.querySelector('.mm-bucket-btn');
        const body = bucket.querySelector('.mm-bucket-body');
        // group -> prefix is an ordinary arc: the head shares the rail's left edge,
        // so the corridor between the group card and the rail is empty and the
        // bezier spends its whole travel there. gap = var(--mm-gap) > 0, which is
        // what test_matrix_horizontal_edges measures on every endpoint pair.
        connect(groupButton, head, tier, 'bucket', group, lane);
        // A folded prefix owns no chips to draw to. Its body reports a 0x0 rect, so
        // routing through it would put a wire at the origin -- the same phantom the
        // reveal fix had to filter out.
        if (!body || body.hidden) return;
        const chips = [...body.querySelectorAll('[data-mm-tool]')].filter(el => el.getBoundingClientRect().width >= 1);
        if (!chips.length) return;
        // The lane is NOT threaded into the stem any more. It used to be, on the claim
        // that a section then reads as one bracket; measured, that made six stems six
        // parallel lines (clear_probe: STEM-OVERLAP 0 -- no two buckets share a row, so
        // the fan separated nothing) and at 390 it pushed a stem behind the group card.
        // connectTrunk keeps ONE spine, and since Anh moved it the spine lives to the
        // RIGHT of the rail, so the left gutter belongs to the arc INTO the head and to
        // nothing else. lane is still passed so the router's sibling set stays uniform.
        const onRail = [];
        for (const chip of chips){
          if (head.classList.contains('mm-placed') || chip.classList.contains('mm-placed')) connectPlaced(head, chip, tier, 'tool', group);
          else onRail.push(chip);
        }
        if (onRail.length) connectTrunk(head, onRail, tier, group, lane);
      });
    };
    // Lane indices come from the loops themselves, because a lane is a property of a
    // SIBLING SET: the four tier wires share the root->tier corridor, the clusters of
    // one tier share the tier->cluster corridor, and so on. Indexing by DOM order keeps
    // the fan top-to-bottom, which is the order the cards are already in.
    const tierList = [...mapBranches.entries()];
    tierList.forEach(([tier, {button, panel}], ti) => {
      connect(mapAnchor, button, tier, 'tier', null, {i: ti, n: tierList.length});
      if (panel.hidden) return;
      const clusters = [...panel.querySelectorAll('.mm-cluster')];
      clusters.forEach((cluster, ci) => {
        const cBtn = cluster.querySelector('.mm-cluster-btn');
        const cBody = cluster.querySelector('.mm-cluster-body');
        connect(button, cBtn, tier, 'cluster', null, {i: ci, n: clusters.length});
        if (!cBody || cBody.hidden) return;
        const groups = [...cBody.querySelectorAll('.mm-group > button')];
        groups.forEach((groupButton, gi) => {
          const group = groupButton.dataset.mmGroup;
          connect(cBtn, groupButton, tier, 'group', group, {i: gi, n: groups.length});
          const list = document.getElementById(groupButton.getAttribute('aria-controls'));
          if (list.hidden) return;
          leafEdges(groupButton, list, tier, group);
        });
      });
    });
    mapWires.replaceChildren(paths);
    // One pass rebuilds BOTH layers, so a relation edge can never be left pointing at a
    // card the last layout moved. screenPoint is this pass's own screen->local map, handed
    // down rather than recomputed: a second getScreenCTM on a zoomed canvas is exactly the
    // drift the comment above it was written to stop.
    drawMapRelations(screenPoint);
    if (previousHover?.isConnected && previousHover.matches(':hover')) lightNode(previousHover);
    else paintMapFocus();
    // A3: every layout pass is also the moment a chip may have MOVED in map coords
    // (accordion toggle, label rewrap, resize). Re-anchor the chain panel to its chip
    // here -- the one place that sees every such move -- instead of on pan/zoom/scroll.
    repositionChainPanel();
  }
  // ── THE RELATION OVERLAY ──────────────────────────────────────────────────────────
  // Anh 2026-11-24: "phần mind maps cần thể hiện tool chain và các tool liên quan,
  // auto chain". The three tables that answer that -- CHAINS (8 routes), SB_LINKS (30
  // edges), SB_AUTO (16 auto-fires) -- shipped until now as PROSE ONLY, in the detail
  // panel's .chain-row and in relationPanel. So the map drew the taxonomy and nothing
  // else, and "what comes after this tool" had no picture at all.
  //
  // THREE MEASURED FACTS shape this, and each one killed a simpler design:
  // 1. THE ACCORDION CANNOT HOLD A CHAIN. setMapGroup closes every sibling group of the
  //    cluster (below, :3446) and toggleMapCluster closes every sibling cluster of the
  //    tier (:3200), so at most one cluster per tier and one group per cluster is open.
  //    Measured across the eight routes, only four (Graph Analysis, Quality Pipeline,
  //    Desktop/T4 E2E, Mission Orchestration) need a single stop per tier; Code Lifecycle
  //    needs T1|code + T2|code + T2|ops. An overlay that REVEALED every endpoint would
  //    fight the accordion and paint fewer cards than it claims to. So each endpoint
  //    falls back to the nearest ancestor card that is laid out, and a route reads as
  //    "step 3 lands on the group Ghi dữ liệu" instead of lying about a chip.
  // 2. A FOLDED ENDPOINT IS STILL IN THE DOM WITH A ZERO RECT. findMapLeaf (:3419) walks
  //    pages and unfolds buckets, which only makes sense if folded chips are present, and
  //    a hidden .mm-tools keeps its buttons. Presence is therefore NOT visibility: the
  //    anchor test is rect.width >= 1, the same test the wire router uses at :2669.
  // 3. THE LAYER IS UNDER THE CARDS, which is Anh's own ruling ("Layer SVG riêng,
  //    NGUnder thẻ bài"), so an edge that has to cross a rail is occluded by it rather
  //    than slashing over it. It sits at z-index 0 under .mm-wires' 1, which is why the
  //    hierarchy's paint order did not move, and why every wire census written so far --
  //    all of them targeting svg.mm-wires / #mm-branch-wires -- reads exactly what it
  //    read before this layer existed. That is a consequence of the box, not luck: the
  //    censuses are id/class-scoped, and this layer carries neither.
  const REL_SVG_NS = 'http://www.w3.org/2000/svg';
  const REL_KIND_CLASS = kind => kind === 'chain' ? 'mm-chain-edge'
    : kind === 'auto-chain' ? 'mm-auto-edge' : 'mm-link-edge mm-link-' + kind;
  let relationEdgeIndex = null;
  function relationEdges(){
    // The SAME dedupe the detail panel uses (relationPanel, :745): one key per
    // [kind, from, to], with alias-shared-handler sorted because it is a pair with no
    // direction. So the overlay's count and the panel's count cannot drift, and both read
    // the same top-level SB_LINKS / SB_AUTO arrays -- one truth, two views.
    if (relationEdgeIndex) return relationEdgeIndex;
    const byKey = new Map();
    const add = (from, to, kind, rank) => {
      const shared = kind === 'alias-shared-handler';
      const key = JSON.stringify([kind, ...(shared ? [from, to].sort() : [from, to])]);
      if (!byKey.has(key)) byKey.set(key, { from, to, kind, shared, rank });
      return byKey.get(key);
    };
    SB_LINKS.forEach(e => add(e.f, e.t, e.k, null));
    SB_AUTO.forEach(e => add(e.f, e.t, 'auto-chain', e.r));
    relationEdgeIndex = [...byKey.values()];
    return relationEdgeIndex;
  }
  // The nearest card that is LAID OUT: chip > bucket head > group head > cluster head >
  // tier head > root. Null only when the tool is not in the inventory at all (measured
  // 2026-11-24: zero of the 78 endpoints, so today this never returns null for a real edge).
  function relationAnchor(name){
    const tool = mapToolIndex.get(name);
    if (!tool) return null;
    // Tool names are snake_case, so the attribute selector needs no escaping -- noted so
    // the next reader does not add an escaper that has nothing to escape.
    const chip = mapCanvas.querySelector('[data-mm-tool="' + name + '"]');
    const ladder = [];
    if (chip){
      ladder.push(chip);
      const bucket = chip.closest('.mm-bucket');
      if (bucket) ladder.push(bucket.querySelector(':scope > .mm-bucket-btn'));
      const group = chip.closest('.mm-group');
      if (group) ladder.push(group.querySelector(':scope > button'));
      const cluster = chip.closest('.mm-cluster');
      if (cluster) ladder.push(cluster.querySelector('.mm-cluster-btn'));
    } else {
      // The group was never opened, so its list is still empty and no chip exists yet.
      const head = mapCanvas.querySelector('[data-mm-group="' + mapGroupKey(tool) + '"]');
      if (head){
        ladder.push(head);
        const cluster = head.closest('.mm-cluster');
        if (cluster) ladder.push(cluster.querySelector('.mm-cluster-btn'));
      }
    }
    const branch = mapBranches.get(tool.t);
    if (branch) ladder.push(branch.button);
    ladder.push(mapAnchor);
    for (const el of ladder){
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 1) continue;
      const depth = el === chip ? 'tool' : el === mapAnchor ? 'root'
        : el.classList.contains('mm-bucket-btn') ? 'bucket'
        : el.classList.contains('mm-cluster-btn') ? 'cluster'
        : el.classList.contains('mm-tier') ? 'tier' : 'group';
      return { el, rect: r, depth };
    }
    return null;
  }
  // Emptying the layer is a DOM write, not a measurement, so it stands apart from
  // drawMapRelations: closeMapDetail owes the map an honest blank and owes it WITHOUT a
  // full layout pass, which every accordion toggle already pays once (:3371).
  function clearMapRelationLayer(){
    if (!mapRelations) return;
    mapRelations.replaceChildren();
    // EVERY stamp resets with the paths it describes. The first version cleared only
    // edges and skipped, so after a deselect the empty layer still advertised the
    // collapsed count of the previous selection, and the equation the test checks
    // (drawn + collapsed + unanchored == expected) read 9 == 0 on a map with nothing on
    // it. An empty layer owes four zeroes, not two.
    mapRelations.dataset.mmRelationEdges = '0';
    mapRelations.dataset.mmRelationSkipped = '0';
    mapRelations.dataset.mmRelationCollapsed = '0';
    mapRelations.dataset.mmRelationUnanchored = '0';
  }
  function drawMapRelations(screenPoint){
    const layer = mapRelations;
    if (!layer) return;
    // The blank() closure this used to carry wrapped one call to clearMapRelationLayer,
    // which added a name to read and nothing to do; the guard now calls it directly.
    if (!mapRelationsOn || !mapSelected){ clearMapRelationLayer(); return; }
    // Two tables answer "this tool leads to what", and both are collected into one list
    // before anything is drawn. CHAINS contributes its hops IN ORDER, and the step number
    // stamped on an edge is that index -- the order is the whole difference between a
    // chain and a pile of related tools. relationEdges() contributes the inventory edges
    // in either direction, carrying the auto-chain group key where one exists.
    const wanted = [];
    for (const c of CHAINS){
      if (!c.tools.includes(mapSelected)) continue;
      for (let i = 0; i < c.tools.length - 1; i++)
        wanted.push({ from: c.tools[i], to: c.tools[i + 1], kind: 'chain',
          label: String(i + 1), chain: c.name });
    }
    for (const e of relationEdges()){
      if (e.from !== mapSelected && e.to !== mapSelected) continue;
      wanted.push({ from: e.from, to: e.to, kind: e.kind, shared: e.shared,
        label: e.kind === 'auto-chain' ? String(e.rank) : '' });
    }
    // Endpoints are resolved BEFORE drawing, and edges sharing one pair of cards are
    // fanned apart instead of painted on top of each other: two routes that read as one
    // line is a false census, the defect class this campaign keeps falling into.
    const laneCount = new Map();
    const placed = [];
    let collapsed = 0, unanchored = 0;
    for (const w of wanted){
      const a = relationAnchor(w.from), b = relationAnchor(w.to);
      if (!a || !b){ unanchored++; continue; }
      // A hop whose ends share a card is DROPPED, not drawn as a zero-length loop: the
      // chain still runs THROUGH that card and the surviving hop out of it carries the
      // step number, so the road stays continuous. What is lost is the count of hops
      // folded inside one card -- which is why it is stamped, never left to the eye.
      if (a.el === b.el){ collapsed++; continue; }
      const key = a.rect.left < b.rect.left ? w.from + '\u0000' + w.to : w.to + '\u0000' + w.from;
      const n = laneCount.get(key) || 0;
      laneCount.set(key, n + 1);
      placed.push({ ...w, a, b, lane: n, pairKey: key });
    }
    const frag = document.createDocumentFragment();
    for (const p of placed){
      const ra = p.a.rect, rb = p.b.rect;
      const total = laneCount.get(p.pairKey) || 1;
      const off = (p.lane - (total - 1) / 2) * 7;
      // Leave and arrive on the facing edges, so a backwards edge (a delegate that
      // returns to an earlier tier) points at its target instead of at empty gutter.
      const leftward = (rb.left + rb.width / 2) < (ra.left + ra.width / 2);
      const p1 = screenPoint(leftward ? ra.left : ra.right, (ra.top + ra.bottom) / 2 + off);
      const p2 = screenPoint(leftward ? rb.right : rb.left, (rb.top + rb.bottom) / 2 + off);
      const sign = leftward ? -1 : 1;
      // ISSUE 2 / q4=Z: lane routing. The old cubic bezier cut straight across the
      // cards and disappeared under them (the layer sits at z-index 0). Four
      // orthogonal legs keep the stroke inside the EMPTY gutter between chips, so
      // the wire reads on screen instead of being occluded. Same path count, same
      // class / stamps / marker / label as before, so every relation census holds.
      const elbow = Math.min(18, Math.max(8, Math.abs(p2.x - p1.x) * .18));
      const laneY = (p1.y + p2.y) / 2;
      const x1 = p1.x + sign * elbow, x2 = p2.x - sign * elbow;
      const path = document.createElementNS(REL_SVG_NS, 'path');
      path.setAttribute('d', 'M' + p1.x.toFixed(2) + ' ' + p1.y.toFixed(2)
        + ' L' + x1.toFixed(2) + ' ' + p1.y.toFixed(2)
        + ' L' + x1.toFixed(2) + ' ' + laneY.toFixed(2)
        + ' L' + x2.toFixed(2) + ' ' + laneY.toFixed(2)
        + ' L' + x2.toFixed(2) + ' ' + p2.y.toFixed(2)
        + ' L' + p2.x.toFixed(2) + ' ' + p2.y.toFixed(2));
      path.setAttribute('class', REL_KIND_CLASS(p.kind));
      // Stamped, not inferred from the path text: a test can then count edges per kind,
      // per chain and per endpoint depth without parsing geometry it would only guess at.
      path.dataset.mmRelKind = p.kind;
      path.dataset.mmRelFrom = p.from;
      path.dataset.mmRelTo = p.to;
      path.dataset.mmRelDepth = p.a.depth + '-' + p.b.depth;
      const contextHop = activeRoute && p.chain && (p.chain !== activeRoute.name || (p.from !== mapSelected && p.to !== mapSelected));
      if (contextHop) path.classList.add('mm-route-context');
      if (p.chain) path.dataset.mmRelChain = p.chain;
      frag.appendChild(path);
      if (p.label){
        // The digit means two different things, which is why the two kinds are two
        // separate label classes. For an auto edge it is a snapshot-local grouping key,
        // NOT an index into AUTO_CHAIN_RULES (code_tools.py:3297, 15 rows): rank 2 is
        // cbm_dead_code -> cbm_index while registry row 2 is ca_hint_commit, and ranks
        // 3/7/8/11 name tools the registry never mentions. :314 already labels the table
        // "unmeasured", so the digit groups edges for the eye and must never be read as
        // a pointer a reader can look up.
        const t = document.createElementNS(REL_SVG_NS, 'text');
        t.setAttribute('x', ((x1 + x2) / 2).toFixed(2));
        t.setAttribute('y', laneY.toFixed(2));
        t.setAttribute('class', p.kind === 'chain' ? 'mm-chain-edge-label' : 'mm-auto-edge-label');
        if (contextHop) t.classList.add('mm-route-context');
        t.textContent = p.label;
        frag.appendChild(t);
      }
    }
    layer.replaceChildren(frag);
    layer.dataset.mmRelationEdges = String(placed.length);
    // TWO SKIP COUNTS, because the two reasons are not the same news. COLLAPSED is
    // expected and honest: both endpoints of a hop folded onto one ancestor card, so the
    // road runs THROUGH that card rather than drawing a zero-length loop on it (measured
    // 2026-11-24: write_fix collapses 9 of 21 hops this way, all of them inside a folded
    // group). UNANCHORED is a real loss -- a named tool with no laid-out card anywhere --
    // and it is currently 0 across all 78 endpoints. Reporting one merged number would
    // let a future regression hide inside the benign one.
    layer.dataset.mmRelationCollapsed = String(collapsed);
    layer.dataset.mmRelationUnanchored = String(unanchored);
    layer.dataset.mmRelationSkipped = String(collapsed + unanchored);
  }
  let panAnim = null;
  // One cancel path for every actor that moves the stage by hand. Without it a
  // lookup pan keeps writing mapStage.scrollTo() over a drag, an arrow-key nudge
  // or a "Vua khung" reset.
  function cancelMapPan(){
    if (panAnim) cancelAnimationFrame(panAnim);
    panAnim = null;
  }
  // UNBOUNDED PAN. mmPan is the overflow the stage scroll area could not hold, in
  // the SAME unit as scrollLeft, so the content position is scroll + mmPan no
  // matter which channel carries it. The canvas wears the overflow as a
  // translate, and because .mm-wires is a CHILD of the canvas the wires ride
  // along: no redraw, and syncMapBranchLayout never learns a pan happened.
  // The conversion is MEASURED, not reasoned out: at zoom 2 a scroll of 120 moves
  // the canvas 120 client px, and translate(-120/2 px) moves it the same 120, so
  // the CSS value is -mmPan / zoom. A transform lives inside the zoomed box where
  // a scroll does not, which is the whole reason the sign and the divisor are
  // both needed - get either wrong and the map pans against the cursor.
  let mmPan = {x: 0, y: 0};
  const mapZoomNow = () => parseFloat(mapCanvas.style.zoom || '1') || 1;
  function applyPan(){
    const moved = mmPan.x !== 0 || mmPan.y !== 0;
    const k = mapZoomNow();
    mapCanvas.style.transform = moved
      ? 'translate(' + (-mmPan.x / k) + 'px, ' + (-mmPan.y / k) + 'px)' : '';
    // Match the grid's phase to the total camera offset on either side of home.
    const total = panTotal();
    mapStage.style.backgroundPosition = '0px 0px, ' + (-total.x % 22) + 'px ' + (-total.y % 22) + 'px';
  }
  function panTotal(){
    return {x: mapStage.scrollLeft + mmPan.x, y: mapStage.scrollTop + mmPan.y};
  }
  // Split one desired offset into the bounded scroll channel and the unbounded
  // translate. The scroll keeps the native scrollbars telling the truth about the
  // half they can still express; the remainder is CARRIED, not clamped away - and
  // clamping away is the wall Anh hit: after "Vua khung" the range is zero, so
  // every drag died on the first pixel.
  function setPanTotal(x, y){
    // A translated canvas can enlarge scrollWidth/Height. Measure the native
    // range at rest before assigning the signed remainder to the transform.
    if (mapCanvas.style.transform) mapCanvas.style.transform = '';
    const maxX = mapStage.scrollWidth - mapStage.clientWidth;
    const maxY = mapStage.scrollHeight - mapStage.clientHeight;
    mapStage.scrollTo(Math.max(0, Math.min(maxX, x)), Math.max(0, Math.min(maxY, y)));
    mmPan.x = x - mapStage.scrollLeft;
    mmPan.y = y - mapStage.scrollTop;
    applyPan();
    // A3: the chain panel is a canvas child -- pan carries it with the map, no re-pin.
  }
  function panMapBy(dx, dy){
    const t = panTotal();
    setPanTotal(t.x + dx, t.y + dy);
  }
  function revealMapNode(node, routePanel){
    if (!node) return;
    const viewport = mapStage.getBoundingClientRect(), rect = node.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return;
    const scale = viewport.width / mapStage.offsetWidth || 1;
    const inset = 9;
    // A route can sit flush with the canvas edge. Inventing a gutter there
    // would translate the entire board just to show nine pixels of blank space.
    const horizontalInset = routePanel ? 0 : inset;
    const left = viewport.left + horizontalInset, right = viewport.left + mapStage.clientWidth * scale - horizontalInset;
    const top = (routePanel ? Math.max(0, viewport.top) : viewport.top) + inset;
    const bottom = (routePanel ? Math.min(innerHeight, viewport.top + mapStage.clientHeight * scale)
      : viewport.top + mapStage.clientHeight * scale) - inset;
    const tray = routePanel ? routePanel.getBoundingClientRect() : rect;
    const nearX = Math.min(rect.left, tray.left), farX = Math.max(rect.right, tray.right);
    const nearY = Math.min(rect.top, tray.top), farY = Math.max(rect.bottom, tray.bottom);
    // When the pair is wider than the viewport, reveal the card with the smallest
    // movement that still leaves as much of the selected leaf on screen as possible.
    const cardOnly = routePanel && farX - nearX > right - left;
    const visibleLeft = cardOnly ? tray.left : nearX, visibleRight = cardOnly ? tray.right : farX;
    const x = visibleLeft < left || visibleRight - visibleLeft > right - left
      ? visibleLeft - left : Math.max(0, visibleRight - right);
    const y = nearY < top || farY - nearY > bottom - top ? nearY - top : Math.max(0, farY - bottom);
    const dx = x / scale, dy = y / scale;
    if (!dx && !dy) return;
    // The delta goes to the TOTAL, not to scrollTo. Past the scroll wall the
    // overflow channel is the only one left, so a reveal that ignored it would
    // silently do nothing for a node the user had already dragged off that edge.
    // The demo pans rather than jumps (smoothPanTo, easeOutCubic 650ms). Only the
    // programmatic reveal tweens: test_matrix_horizontal_edges presses ArrowRight
    // and reads scrollLeft on the next line, so the keyboard nudge stays instant,
    // and reduced_motion has to stay a single step for the same reason.
    if (matchMedia('(prefers-reduced-motion: reduce)').matches){ panMapBy(dx, dy); return; }
    cancelMapPan();
    const from = panTotal();
    const t0 = performance.now(), ms = 650;
    const step = now => {
      const p = Math.min(1, (now - t0) / ms);
      const e = 1 - Math.pow(1 - p, 3);
      setPanTotal(from.x + dx * e, from.y + dy * e);
      panAnim = p < 1 ? requestAnimationFrame(step) : null;
    };
    panAnim = requestAnimationFrame(step);
  }
  function revealMapBranch(button,list){
    if (!list || list.hidden){ revealMapNode(button); return; }
    const bounds = button.getBoundingClientRect();
    const center = (bounds.top + bounds.bottom) / 2;
    // A cluster body holds .mm-group wrappers; a tools list holds buttons directly.
    // MEASURED, not assumed visible: the branch has just been un-hidden and
    // arriveMapBranch has it under mm-arrive, whose fill mode is `both`, so every
    // child inside a stagger delay is still painting its from{opacity:0} frame and
    // reports a 0x0 rect. The old reduce picked one of those, read its midpoint as
    // 0, and asked for dy=-346. The scroll channel clamps a negative away to 0 and
    // setPanTotal CARRIES the remainder into the translate, so the canvas left the
    // origin by translate(44.55px, 346.02px) and stayed there -- the ToolA root
    // card slid out of the stage and never came back until "Vua khung". That is
    // the "giat vi tri canva" on the first node, and it is a phantom measurement,
    // not a timing accident: a second click on the settled branch asks for -2.8px.
    const children = [...list.querySelectorAll('button[data-mm-group], button[data-mm-tool]')]
      .filter(node => node.getBoundingClientRect().width > 0);
    if (!children.length){ revealMapNode(button); return; }
    const nearest = children.reduce((best,node) => !best || Math.abs((node.getBoundingClientRect().top + node.getBoundingClientRect().bottom) / 2 - center)
      < Math.abs((best.getBoundingClientRect().top + best.getBoundingClientRect().bottom) / 2 - center) ? node : best,null);
    revealMapNode(nearest || button);
  }
  // Closing a parent has to close its descendants too. Hiding only the body left
  // inner groups expanded, so re-opening a cluster restored every group ever opened
  // and the canvas jumped back to thousands of pixels -- the real overflow driver.
  function collapseMapGroups(scope){
    for (const gb of scope.querySelectorAll('.mm-group > button')){
      if (gb.getAttribute('aria-expanded') !== 'true') continue;
      const list = document.getElementById(gb.getAttribute('aria-controls'));
      if (list){
        if (list.querySelector('[aria-current="true"]')) closeMapDetail();
        list.hidden = true;
        resetMapLeaves(list);
      }
      gb.setAttribute('aria-expanded','false');
    }
  }
  function collapseMapClusters(scope,except){
    for (const cl of scope.querySelectorAll('.mm-cluster')){
      if (cl === except) continue;
      const cb = cl.querySelector('.mm-cluster-btn');
      if (cb.getAttribute('aria-expanded') !== 'true') continue;
      const body = cl.querySelector('.mm-cluster-body');
      collapseMapGroups(body);
      body.hidden = true;
      cb.setAttribute('aria-expanded','false');
    }
  }
  function arriveMapBranch(panel){
    // Forest grows a branch outward instead of popping it (projects.css
    // .project-map-enter .project-tree-node, delay calc(.18s + var(--branch-index)*.044s)).
    // The index is stamped here rather than in the markup because the panel is built
    // once and cached, and the class is dropped when the run ends so a later open of
    // another tier can never inherit a half-finished arrival.
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const clusters = [...panel.querySelectorAll('.mm-cluster')];
    if (!clusters.length) return;
    clusters.forEach((el, i) => el.style.setProperty('--branch-index', String(i)));
    panel.classList.add('mm-arriving');
    clearTimeout(panel._mmArrive);
    panel._mmArrive = setTimeout(() => {
      panel.classList.remove('mm-arriving');
      for (const el of clusters) el.style.removeProperty('--branch-index');
    }, 1400);
  }
  function setMapBranch(tier,open){
    const {button,panel} = mapBranches.get(tier);
    if (panel.hidden === !open) return panel;
    if (!open && panel.querySelector('[aria-current="true"]')) closeMapDetail();
    // Branches stay independent: opening one tier must not collapse another. That
    // invariant predates the fit work -- test_matrix_horizontal_edges_zoom_and_
    // independent_branches asserts T2 survives a T1 open -- and the fit never needed
    // it: one-line closed nodes plus leaf paging hold a SINGLE open tier inside the
    // stage, and several open tiers scroll inside the stage rather than the page.
    if (!open) collapseMapClusters(panel);
    panel.hidden = !open;
    button.setAttribute('aria-expanded',String(open));
    if (open && !panel.hasChildNodes()){
      // Tier -> cluster -> group -> tool, the extra level Anh asked for.
      panel.innerHTML = mapClustersOf(tier).map((cluster,ci) => {
        const cid = 'mm-cluster-' + tier + '-' + ci;
        const tools = cluster.groups.reduce((n,g) => n + g.tools.length, 0);
        return '<div class="mm-cluster" data-mm-cluster="' + esc(cluster.id) + '" data-mm-tier="' + tier + '">'
          + '<button type="button" class="mm-cluster-btn" aria-controls="' + cid + '" aria-expanded="false">'
          + '<span class="mm-cluster-copy"><strong>' + esc(cluster.label) + '</strong><small>' + cluster.groups.length + ' nh\u00f3m \u00b7 ' + tools + ' tool</small></span>'
          + '<span class="mm-toggle" aria-hidden="true"></span></button>'
          + '<div class="mm-cluster-body" id="' + cid + '" hidden>'
          + cluster.groups.map((group,gi) => {
            const id = 'mm-group-' + tier + '-' + ci + '-' + gi;
            return '<div class="mm-group"><button type="button" data-mm-group="' + esc(group.key) + '" aria-controls="' + id + '" aria-expanded="false">'
              + '<span class="mm-group-copy"><strong>' + esc(mapGroupName(group.key)) + '</strong><small data-mm-count data-mm-group-count="' + esc(group.key) + '"></small></span>'
              + '<span class="mm-toggle" aria-hidden="true"></span></button>'
              + '<div class="mm-tools" id="' + id + '" hidden></div></div>';
          }).join('') + '</div></div>';
      }).join('');
    }
    renderMap();
    syncMapBranchLayout();
    // The wire trace and the node arrival are two different ports: the trace was
    // already here, the stagger comes from Forest. Rationale lives on arriveMapBranch.
    if (open){ animateBranch(tier,'open'); arriveMapBranch(panel); }
    return panel;
  }
  function toggleMapCluster(cluster,open){
    const body = cluster.querySelector('.mm-cluster-body');
    if (!body || body.hidden === !open) return body;
    if (!open && body.querySelector('[aria-current="true"]')) closeMapDetail();
    if (open) collapseMapClusters(cluster.parentElement,cluster);
    else collapseMapGroups(body);
    body.hidden = !open;
    cluster.querySelector('.mm-cluster-btn').setAttribute('aria-expanded',String(open));
    syncMapBranchLayout();
    return body;
  }
  // A bucket folds on its own: unlike groups inside a cluster, siblings stay open,
  // because the whole point of promoting the prefix to a node is that Anh can walk
  // one section at a time through a 31-tool rail instead of scrolling all of it.
  function toggleMapBucket(bucket,open){
    const head = bucket.querySelector('.mm-bucket-btn');
    const body = bucket.querySelector('.mm-bucket-body');
    if (!head || !body || body.hidden === !open) return body;
    if (!open && body.querySelector('[aria-current="true"]')) closeMapDetail();
    body.hidden = !open;
    head.setAttribute('aria-expanded',String(open));
    syncMapBranchLayout();
    return body;
  }
  const leafIcons = {
    T1:'<circle cx="8" cy="8" r="5"/><path d="m12 12 5 5"/>',
    T2:'<path d="M3 13l-1 4 4-1L16 6l-3-3Z M11 5l3 3"/>',
    T3:'<path d="M4 2h10v14H4Z M6 6h6 M6 9h6 M6 12h4"/>',
    T4:'<path d="M2 3h14v10H2Z M6 16h6 M9 13v3"/>'
  };
  const BUCKET_MIN = 3;
  // LEAF BUCKETS (Anh 2026-11-23: "nếu tool nhiều thì nhóm lại, nhóm check hay
  // nhóm click"): a rail of 46 unlabeled chips is what he called lỗi, so a group
  // past BUCKET_MIN tools gets section headers. Ordered rules, FIRST MATCH WINS,
  // and the anchored prefixes sit BEFORE the substring rules on purpose: the first
  // draft had /path/ ahead of cbm_, which filed cbm_trace_path under PATH, a bucket
  // of one, and the promotion floor then dropped it into KHAC. That is a tool lost
  // to a rule order, so the order is part of the contract, not styling.
  const LEAF_BUCKET_RULES = [
    ['cdp',        n => /(^|_)(cdp|electron)(_|$)/.test(n)],
    ['cbm',        n => n.startsWith('cbm_')],
    ['ocr',        n => n.startsWith('ocr_')],
    ['git',        n => n.startsWith('git_')],
    ['workflow',   n => n.startsWith('workflow_')],
    ['mission',    n => n.startsWith('mission_')],
    ['council',    n => /(council|convene|leader_)/.test(n)],
    ['memory',     n => n.startsWith('memory_')],
    ['session',    n => /session/.test(n)],
    ['dialog',     n => n.includes('dialog')],
    ['click',      n => n.includes('click')],
    ['window',     n => /window/.test(n)],
    ['screenshot', n => /(screenshot|capture)/.test(n)],
    ['dom',        n => /(^|_)(dom|selector|page|fill|evaluate|invoke)(_|$)/.test(n)],
    ['log',        n => /(^|_)logs?(_|$)/.test(n)],
    ['folder',     n => n.includes('folder')],
    ['map',        n => /(^|_)maps?(_|$)/.test(n) || n.endsWith('_map')],
    ['index',      n => /(^|_)index(ing)?(_|$)/.test(n)],
    ['search',     n => /^(search_|find_|query_|regex_)/.test(n)],
    ['read',       n => /^(read_|get_(file|folder|project|skill|task|validation|rule|session))/.test(n)],
    ['write',      n => /^(write_|apply_|replace_|delete_|ruff_)/.test(n)],
    ['validate',   n => n.startsWith('validate')],
    ['test',       n => /(^|_)tests?(_|$)/.test(n) || n.endsWith('_test')],
    ['runtime',    n => /(restart|kill_process|server|port_|process_|wait_for|runtime|heartbeat)/.test(n)],
    ['token',      n => /(token|capability|permission|claim|owner_)/.test(n)],
    ['audit',      n => /(audit|_check$|(^|_)check_|verify|_diff$|scorecard|metrics)/.test(n)],
    ['usage',      n => /(usage|meter|_stats?$)/.test(n)],
    ['status',     n => /(status|state|checkpoint)/.test(n)],
    ['graph',      n => /(graph|architect|ast_|import_)/.test(n)],
    ['classify',   n => /(classify|hint|recommend|suggest|route_)/.test(n)],
    ['path',       n => /path/.test(n)]
  ];
  const LEAF_BUCKET_LABELS = {
    cdp:'CDP & electron', cbm:'CBM graph', ocr:'OCR scan', git:'Git', workflow:'Workflow',
    mission:'Mission', council:'Council', memory:'Memory', session:'Session',
    dialog:'Hộp thoại', click:'Click', window:'Cửa sổ', screenshot:'Chụp màn hình',
    dom:'DOM & trang', log:'Log', folder:'Thư mục', map:'Project map', index:'Index',
    search:'Tìm & query', read:'Đọc dữ liệu', write:'Ghi dữ liệu', validate:'Validate',
    test:'Test', runtime:'Server & process', token:'Token & quyền', audit:'Check & audit',
    usage:'Usage', status:'Status & state', graph:'Graph & kiến trúc',
    classify:'Gợi ý & phân loại', path:'Path', __khac:'Tool khác'
  };
  // An unmatched name falls back to its leading token, so a pile the table never
  // anticipated still groups instead of scattering (create_* in T2|ops is exactly
  // this case). A bucket under BUCKET_MIN merges into KHAC rather than becoming a
  // one-chip section.
  const mapBucketOf = name => {
    for (const [id, test] of LEAF_BUCKET_RULES) if (test(name)) return id;
    return 'tok:' + String(name).split('_')[0];
  };
  const mapBucketLabel = id => (id in LEAF_BUCKET_LABELS) ? LEAF_BUCKET_LABELS[id] : id.slice(4).toUpperCase();
  const byName = (a, b) => a.n < b.n ? -1 : (a.n > b.n ? 1 : 0);
  function mapLeafSections(group){
    const sorted = group.tools.slice().sort(byName);
    if (sorted.length <= BUCKET_MIN) return null;
    const bags = new Map();
    for (const t of sorted){
      const id = mapBucketOf(t.n);
      if (!bags.has(id)) bags.set(id, []);
      bags.get(id).push(t);
    }
    const kept = [], merged = [];
    for (const [id, list] of bags) (list.length >= BUCKET_MIN ? kept : merged).push([id, list]);
    // One pile for the whole group IS the group, so a node there only repeats the
    // chip above it. Null means "paint a plain rail", and mapLeafRows honours it.
    if (!kept.length || (kept.length === 1 && !merged.length)) return null;
    // RULE-TABLE ORDER, not size order. The head used to sort by bucket size, which
    // is NOT stable under a rename: one tool added or refiled and every prefix node
    // reshuffles. While the head was a caption nobody clicked that was invisible; now
    // that the prefix is a NODE, the shuffle moves the card under Anh's cursor and
    // reads as the canvas jumping. The table is the contract, so the rail follows it.
    const tableOrder = LEAF_BUCKET_RULES.map(rule => rule[0]);
    const rankOf = id => {
      const i = tableOrder.indexOf(id);
      return i < 0 ? tableOrder.length : i;
    };
    kept.sort((a, b) => rankOf(a[0]) - rankOf(b[0]) || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    const sections = kept.map(([id, list]) => [id, mapBucketLabel(id), list]);
    if (merged.length){
      sections.push(['__khac', LEAF_BUCKET_LABELS.__khac, merged.flatMap(([, list]) => list).sort(byName)]);
    }
    return sections;
  }
  // 71 of 241 names are wider than a chip, so keep the full name reachable by hover
  // and let CSS ellipsize instead of silently clipping the tail.
  const mapLeafButton = tool => '<button type="button" data-mm-tool="' + esc(tool.n) + '" title="' + esc(tool.n) + '">'
    + '<svg class="mm-tool-icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false">' + leafIcons[tool.t] + '</svg>'
    + '<span class="mm-tool-label">' + esc(tool.n) + '</span></button>';
  // An id, not an index: a page appends whole buckets, so an index would renumber
  // every aria-controls the moment a bucket was added above it.
  const mmBucketUid = (groupKey, id) => 'mm-bk-' + String(groupKey).replace(/[^A-Za-z0-9_-]/g, '-')
    + '-' + String(id).replace(/[^A-Za-z0-9_-]/g, '-');
  // ONE ROW = ONE BUCKET NODE. The head is a card like every other node on the map --
  // aria-expanded, a toggle, a wire in and wires out -- because Anh asked for the
  // prefix to BE a node rather than a caption: "gói prefix thì prefix thành node và
  // từ prefix mới dẫn ra node tool". Its chips hang below it inside .mm-bucket-body,
  // and the pair appends as one unit, so a page can never open on chips whose label
  // sits on the page before it.
  function mapLeafRows(group){
    const sections = mapLeafSections(group);
    if (!sections){
      const chips = group.tools.slice().sort(byName), rows = [];
      for (let i = 0; i < chips.length; i += LEAF_PAGE){
        const part = chips.slice(i, i + LEAF_PAGE);
        rows.push({ head: null, chips: part.length, html: part.map(mapLeafButton).join('') });
      }
      return rows;
    }
    return sections.map(([id, label, list]) => {
      const uid = mmBucketUid(group.key, id);
      // OPEN by default, and the reason is the measurement, not taste. A bucket is
      // ONE row of the rail grid, so the row-counted budget of 12 rows was never
      // reached by any rail in the inventory (measured then: 0 rails carried a tail,
      // the widest being 6 buckets). Folding everything on first paint bought no
      // height through paging -- it only hid the very wires Anh asked to see: with
      // all six closed the router drew 0 .mm-tool-wire, so "tu prefix dan ra node
      // tool" existed in the code and nowhere on screen. The head stays a toggle, so
      // a rail Anh wants short is one click, and findMapLeaf unfolds a folded bucket
      // before panning to a chip inside it.
      return { head: id, label, chips: list.length,
        html: '<div class="mm-bucket" data-mm-bucket="' + esc(id) + '">'
          + '<button type="button" class="mm-bucket-btn" aria-controls="' + uid + '" aria-expanded="true">'
          + '<span class="mm-bucket-copy"><strong>' + esc(label) + '</strong>'
          + '<small>' + list.length + ' tool</small></span>'
          + '<span class="mm-toggle" aria-hidden="true"></span></button>'
          + '<div class="mm-bucket-body" id="' + uid + '">' + list.map(mapLeafButton).join('') + '</div></div>' };
    });
  }
  // The budget is counted in PIXELS, and the reason is a measurement, not a
  // preference. It was counted in ROWS first (LEAF_PAGE=12 rows), which died silently
  // because a row became a whole BUCKET: 0 rails carried a tail and the widest rail
  // went back to 1364px against a 576px stage. It was then counted in CHIPS, which is
  // closer but still wrong, because a chip and a head are not the same size. Fitted by
  // least squares over the 24 PLAIN rails of rail_census3.py -- where height is a pure
  // function of chip count, so the fit carries no head term and its max residual is
  // 0.00px -- a chip row costs 35.00px and the rail pays -4.00px of overlap. Adding one
  // constant per head to that same model puts all 11 bucketed rails within 2.6px, and
  // that constant is 46.86px: A HEAD IS 1.34x A CHIP. So a flat chip budget under-counts
  // every bucketed rail, and four of them overflowed a 576px stage -- T1|ops 672px WITH
  // its tail already showing, T2|ops 615px with NO tail at all. T2|ops is the case that
  // proves the loop, not just the constant: 'chips < LEAF_PAGE' was tested AFTER adding
  // the row that broke the budget, so a rail that fitted in one overshoot had nothing
  // left to page and the tail disappeared from a rail that needed it most. The budget
  // now spends pixels BEFORE committing a row, reserves the tail row up front, and still
  // cuts only at bucket boundaries so a page never opens on chips whose label sits on
  // the page before it.
  const LEAF_PAGE = 12;  // plain-rail chunk size; the stage decides how many chunks ship
  const LEAF_ROW_PX = 35, LEAF_HEAD_PX = 47, LEAF_RAIL_BASE_PX = -4, LEAF_STAGE_PX = 576;
  const leafRowPx = row => (row.head ? LEAF_HEAD_PX : 0) + row.chips * LEAF_ROW_PX;
  function appendMapLeaves(list, restoreEnd){
    const group = groupByKey(list.dataset.mmListFor);
    if (!group) return;
    const rows = mapLeafRows(group);
    if (!rows.length) return;
    const shown = Math.min(Number(list.dataset.shown) || 0, rows.length);
    if (shown >= rows.length) return;
    // Live stage height, so a narrower viewport pages harder instead of overflowing;
    // a zero-height stage means the panel is not laid out yet, which is the fallback
    // case, not a budget of zero.
    const stagePx = mapStage.clientHeight > 0 ? mapStage.clientHeight : LEAF_STAGE_PX;
    // A tail is itself one row of the grid, so it is PAID FOR BEFORE the page is
    // planned -- otherwise the page that needs a tail is exactly the page that overflows.
    const tailPx = rows.length > shown + 1 ? LEAF_ROW_PX : 0;
    const budget = stagePx - LEAF_RAIL_BASE_PX - tailPx;
    let end = shown, px = 0;
    while (end < rows.length && px + leafRowPx(rows[end]) <= budget){ px += leafRowPx(rows[end]); end++; }
    // A bucket is ATOMIC: splitting it would orphan its chips from their label, which
    // is the one thing this pager must never do. So when a single row costs more than
    // the whole stage it still ships and the rail scrolls, rather than painting an
    // empty rail that carries a tail promising chips nobody can see.
    if (end === shown) end = shown + 1;
    if (restoreEnd !== undefined) end = Math.min(rows.length, restoreEnd);
    const batch = rows.slice(shown, end);
    const more = list.querySelector('.mm-more');
    const tpl = document.createElement('template');
    tpl.innerHTML = batch.map(r => r.html).join('');
    if (more) list.insertBefore(tpl.content, more);
    else list.append(tpl.content);
    list.dataset.shown = String(end);
    if (end >= rows.length){ if (more) more.remove(); return; }
    const chipsLeft = rows.slice(end).reduce((n, r) => n + r.chips, 0);
    if (more) more.querySelector('span').textContent = 'Còn ' + chipsLeft + ' tool ▸';
    else list.insertAdjacentHTML('beforeend', '<button type="button" class="mm-more" data-mm-more="' + esc(group.key)
      + '"><span>Còn ' + chipsLeft + ' tool ▸</span></button>');
  }
  function findMapLeaf(list,name){
    for (let guard = 0; guard < 60; guard++){
      const leaf = [...list.querySelectorAll('[data-mm-tool]')].find(el => el.dataset.mmTool === name);
      if (leaf){
        // Found in the DOM is not reachable: a bucket the pager appended and Anh
        // folded still holds its chips, and a lookup that pans to a hidden chip
        // scrolls the stage toward nothing. Unfold it, then hand it back.
        const body = leaf.closest('.mm-bucket-body');
        if (body && body.hidden) toggleMapBucket(body.closest('.mm-bucket'), true);
        return leaf;
      }
      if (!list.querySelector('.mm-more')) return null;
      appendMapLeaves(list);
    }
    return null;
  }
  // A closed group has to forget its pages. Keeping them meant re-opening restored
  // all 46 leaves at once -- the same overflow, reached by a different route.
  function resetMapLeaves(list){
    if (!list || !list.dataset.mmListFor) return;
    list.replaceChildren();
    list.dataset.shown = '0';
  }
  function setMapGroup(button,open){
    const list = document.getElementById(button.getAttribute('aria-controls'));
    if (list.hidden === !open) return list;
    if (!open && list.querySelector('[aria-current="true"]')) closeMapDetail();
    if (open){
      const holder = button.closest('.mm-group');
      for (const sib of holder.parentElement.querySelectorAll('.mm-group > button')){
        if (sib === button || sib.getAttribute('aria-expanded') !== 'true') continue;
        const sl = document.getElementById(sib.getAttribute('aria-controls'));
        if (sl){
          if (sl.querySelector('[aria-current="true"]')) closeMapDetail();
          sl.hidden = true;
          resetMapLeaves(sl);
        }
        sib.setAttribute('aria-expanded','false');
      }
    } else resetMapLeaves(list);
    if (open && !list.hasChildNodes()){
      const group = groupByKey(button.dataset.mmGroup);
      // 46 leaves at once were the 2144px rail. They page in by LEAF_PAGE so an open
      // group fits the stage on its own instead of leaning on a 30% zoom to look short.
      list.dataset.mmListFor = group.key;
      list.dataset.shown = '0';
      appendMapLeaves(list);
    }
    list.hidden = !open;
    button.setAttribute('aria-expanded',String(open));
    syncMapBranchLayout();
    return list;
  }
  // Navigation history stores logical IDs. Panning, zoom, polling and redraws
  // never create steps. A search result or a chain jump is one user action.
  const mapSteps = [];
  let restoringMapStep = false, editingLookup = false;
  function rememberMapStep(){
    if (restoringMapStep) return;
    editingLookup = false;
    const expanded = [...mapCanvas.querySelectorAll('button[aria-expanded="true"][aria-controls]')]
      .map(button => button.getAttribute('aria-controls'));
    const pages = [...mapCanvas.querySelectorAll('.mm-tools:not([hidden])[data-shown]')]
      .map(list => [list.id, Number(list.dataset.shown)]);
    const state = {expanded, pages, tool:mapSelected, route:activeRoute?.name,
      branch:selectedBranch?.getAttribute('aria-controls'), info:!mapDetail.hidden,
      panel:!!chainPanelEl, query:mapSearch.value, status:mapSearchStatus.textContent,
      camera:panTotal(), zoom:mapZoomNow()};
    if (JSON.stringify(state) === JSON.stringify(mapSteps.at(-1))) return;
    mapSteps.push(state);
    if (mapSteps.length > 100) mapSteps.shift();
  }
  function backOneMapStep(){
    const state = mapSteps.pop();
    if (!state) return;
    editingLookup = false;
    const scale = mapZoomNow() / state.zoom;
    const expanded = new Set(state.expanded);
    const isOpen = button => expanded.has(button.getAttribute('aria-controls'));
    restoringMapStep = true;
    try {
      cancelMapPan();
      closeMapDetail();
      closeLookup();
      for (const [tier, {button}] of mapBranches) setMapBranch(tier, isOpen(button));
      for (const button of mapCanvas.querySelectorAll('.mm-cluster-btn')){
        toggleMapCluster(button.closest('.mm-cluster'), isOpen(button));
      }
      for (const button of mapCanvas.querySelectorAll('button[data-mm-group]')) setMapGroup(button, isOpen(button));
      for (const [id, count] of state.pages){
        const list = document.getElementById(id);
        if (!list || Number(list.dataset.shown) === count) continue;
        resetMapLeaves(list);
        appendMapLeaves(list, count);
      }
      for (const button of mapCanvas.querySelectorAll('.mm-bucket-btn')) toggleMapBucket(button.closest('.mm-bucket'), isOpen(button));
      mapSearch.value = state.query;
      updateLookup();
      closeLookup();
      setText(mapSearchStatus, state.status);
      const leaf = state.tool && mapCanvas.querySelector('[data-mm-tool="' + CSS.escape(state.tool) + '"]');
      if (leaf){
        selectMapTool(leaf, state.route);
        if (!state.panel) clearChainPanel();
        if (state.info) openMapInfo(false);
      } else if (state.branch){
        selectBranch(mapCanvas.querySelector('button[aria-controls="' + CSS.escape(state.branch) + '"]'));
      }
      syncMapBranchLayout();
      // Return to the previous branch even when revealing the leaf panned it off screen.
      setPanTotal(state.camera.x * scale, state.camera.y * scale);
      mapStage.focus({preventScroll:true});
    } finally { restoringMapStep = false; }
  }
  function initMap(){
    const zoomLabel = document.getElementById('mm-zoom-level');
    let zoom = 1;
    function setZoom(next){
      // 4x is where a leaf label is readable on its own; 0.25 shows the whole
      // 1461px canvas inside a 1538px stage. The old 1.6 ceiling meant zoom-in
      // silently did nothing past that, and 0.01 let a stray click lose the map.
      zoom = Math.min(4, Math.max(.25, Math.round(next * 100) / 100));
      mapCanvas.style.zoom = zoom;
      // The overflow translate is divided by the zoom, so changing the zoom without
      // re-applying it slides the content by (new-old) * mmPan. The additive buttons
      // never touch the scroll, and this is the only thing keeping a panned map from
      // jumping sideways when Anh clicks "+".
      applyPan();
      // A3: no viewport re-pin -- the panel is a canvas child and zooms with the map.
      // zoomAt follows with syncMapBranchLayout, which re-anchors it in map coords.
      zoomLabel.textContent = Math.round(zoom * 100) + '%';
    }
    const DRAG_SLOP = 4;
    let drag = null, squashClickUntil = 0;
    // A1: last pointer position seen OVER the stage. The +/- buttons anchor there;
    // keyboard-only activation (pointer never entered the stage) keeps the legacy
    // pan-free additive path the zoom contract locks. Never cleared on leave, so a
    // button click after hovering the map still anchors at the last map point.
    let lastPointer = null;
    mapStage.addEventListener('mousemove', e => { lastPointer = {x: e.clientX, y: e.clientY}; });
    // The pan state and its primitives live at module scope, next to the reveal
    // that needs them: a node off the edge may already be displaced by the
    // translate, and a scroll alone cannot undo that half.
    function zoomAt(clientX, clientY, factor, pinchGesture = false){
      const k0 = zoom, k1 = Math.min(4, Math.max(.25, Math.round(k0 * factor * 100) / 100));
      if (k1 === k0) return;
      // A3: anchor math that also holds in the slack (margin:auto) state. Measure the
      // canvas rect BEFORE and AFTER the zoom so the margin shift is part of the
      // equation; with zero margin (overflow) this reduces to the old (t0+o)*f-o
      // formula the wheel contract locks. u is the cursor's content-point coordinate.
      const cr0 = mapCanvas.getBoundingClientRect();
      const ux = (clientX - cr0.left) / k0, uy = (clientY - cr0.top) / k0;
      const t0 = panTotal();
      cancelMapPan();
      setZoom(k1);
      syncMapBranchLayout();
      const cr1 = mapCanvas.getBoundingClientRect();
      const x = t0.x + (cr1.left - cr0.left) + ux * (k1 - k0);
      const y = t0.y + (cr1.top - cr0.top) + uy * (k1 - k0);
      // A zoom from home retains the fitted-map origin. A freely dragged axis
      // already beyond home keeps its signed offset and its cursor anchor.
      setPanTotal(pinchGesture || t0.x < 0 ? x : Math.max(0, x), pinchGesture || t0.y < 0 ? y : Math.max(0, y));
    }
    // The wheel zooms, as it does in the demo. The stage is capped at 620px so it
    // never owns the whole page, and Shift+wheel is left alone for horizontal
    // scroll. A trackpad pinch arrives as ctrlKey+wheel and lands here too.
    mapStage.addEventListener('wheel', e => {
      // Let the hovered local list own its wheel, including at either end.
      // The native select popup handles its own input outside this DOM surface.
      for (let el = e.target; el instanceof Element && el !== mapStage; el = el.parentElement){
        const scrollable = el.matches('.mm-chain-panel, [role="listbox"]') ||
          (/^(auto|scroll)$/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight);
        if (!scrollable) continue;
        e.preventDefault();
        const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? el.clientHeight : 1;
        el.scrollBy({top:e.deltaY * unit, left:e.deltaX * unit, behavior:'instant'});
        return;
      }
      if (e.shiftKey) return;
      e.preventDefault();
      zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.0015));
    }, {passive: false});
    // Double-clicking empty map space returns the map to its initial state.
    // Layout changes can move the card under the second click; remember the first
    // click so that a node dblclick stays two node toggles instead of resetting.
    const mapCardClickSel = MM_CARD_SEL + ',button,a,select,details,.mm-wires,.mm-relations,.mm-chain-panel';
    let pendingMapCardClick = null, firstEmptyClick = null, handledEmptyDouble = false;
    mapStage.addEventListener('click', e => {
      const target = e.target instanceof Element ? e.target : null;
      if (e.detail === 2 && firstEmptyClick && Date.now() < firstEmptyClick.until &&
          Math.hypot(e.clientX - firstEmptyClick.x, e.clientY - firstEmptyClick.y) <= 8 &&
          Date.now() >= squashClickUntil){
        // The first back-step can put a node under the pointer before click two.
        // Consume that click before it reaches the new node.
        firstEmptyClick = null;
        handledEmptyDouble = true;
        e.stopPropagation();
        e.preventDefault();
        glideHandler(resetMapToInitial)();
        return;
      }
      if (e.detail === 1){
        handledEmptyDouble = false;
        const card = target && target.closest(mapCardClickSel);
        firstEmptyClick = card || Date.now() < squashClickUntil ? null : {x:e.clientX, y:e.clientY, until:Date.now()+600};
        pendingMapCardClick = card ? {card, at: Date.now(), x: e.clientX, y: e.clientY, second: null} : null;
      } else if (e.detail === 2 && pendingMapCardClick && Date.now() - pendingMapCardClick.at <= 500){
        const first = pendingMapCardClick;
        first.second = target && target.closest(mapCardClickSel);
        if (first.card.matches('button[aria-expanded]') && first.second !== first.card &&
            first.card.isConnected && Math.hypot(e.clientX - first.x, e.clientY - first.y) <= 8){
          // If the first toggle moved a sibling under the cursor, both clicks still
          // belong to the original branch. Chromium may omit dblclick on retargeting.
          pendingMapCardClick = null;
          handledEmptyDouble = true;
          e.stopPropagation();
          e.preventDefault();
          first.card.click();
        }
      }
    }, true);
    mapStage.addEventListener('dblclick', glideHandler(e => {
      if (handledEmptyDouble){ handledEmptyDouble = false; return; }
      const target = e.target instanceof Element ? e.target : null;
      if (Date.now() < squashClickUntil){ pendingMapCardClick = null; return; }
      if (target && target.closest(mapCardClickSel)){ pendingMapCardClick = null; return; }
      const first = pendingMapCardClick;
      pendingMapCardClick = null;
      if (first && first.card.isConnected && first.second !== first.card &&
          Date.now() - first.at <= 500 && Math.hypot(e.clientX - first.x, e.clientY - first.y) <= 8){
        first.card.click();
        return;
      }
      e.preventDefault();
      resetMapToInitial();
    }));
    mapStage.addEventListener('click', glideHandler(e => {
      if (e.detail !== 1 || e.button !== 0 || Date.now() < squashClickUntil || e.target.closest(mapCardClickSel)) return;
      backOneMapStep();
    }));
    function gestureNode(target){
      if (target.closest('.mm-chain-panel-head') && !target.closest('button, select')) return chainPanelEl;
      const card = mapCardOf(target);
      return card && !card.hasAttribute('data-mm-more') ? card : null;
    }
    function startGesture(input){
      const node = gestureNode(input.target);
      const rect = node?.getBoundingClientRect();
      const zoom = mapZoomNow();
      return {node, x:input.clientX, y:input.clientY, total:panTotal(), moved:false,
        grabX:rect ? (input.clientX - rect.left) / zoom : 0, grabY:rect ? (input.clientY - rect.top) / zoom : 0};
    }
    function moveGesture(g, input){
      const dx = input.clientX - g.x, dy = input.clientY - g.y;
      if (!g.moved && Math.hypot(dx, dy) < DRAG_SLOP) return;
      g.moved = true;
      squashClickUntil = Date.now() + 250;
      mapStage.classList.add('is-panning');
      if (g.node){
        const rect = mapCanvas.getBoundingClientRect(), zoom = mapZoomNow();
        pinnedMapNodes.set(mapNodeKey(g.node), {x:(input.clientX - rect.left) / zoom - g.grabX,
          y:(input.clientY - rect.top) / zoom - g.grabY});
        requestMapDraw();
      } else {
        const scale = mapStage.getBoundingClientRect().width / mapStage.offsetWidth || 1;
        setPanTotal(g.total.x - dx / scale, g.total.y - dy / scale);
      }
    }
    mapStage.addEventListener('pointerdown', e => {
      if (e.pointerType === 'touch' || e.button !== 0 || !e.isPrimary) return;
      if (e.target.closest('.mm-chain-panel') && !gestureNode(e.target)) return;
      drag = startGesture(e);
      drag.id = e.pointerId;
      cancelMapPan();
    });
    let pinch = null, touchStart = null;
    const touchDist = ts => Math.hypot(ts[0].clientX - ts[1].clientX, ts[0].clientY - ts[1].clientY);
    mapStage.addEventListener('touchstart', e => {
      if (e.target.closest('.mm-chain-panel') && !gestureNode(e.target) && !touchStart) return;
      cancelMapPan();
      if (e.touches.length === 2){
        pinch = {d0: touchDist(e.touches), k0: zoom};
        touchStart = null;
        e.preventDefault();
      } else if (e.touches.length === 1){
        pinch = null;
        touchStart = startGesture(e.touches[0]);
      }
    }, {passive: false});
    mapStage.addEventListener('touchmove', e => {
      if (pinch && e.touches.length === 2){
        e.preventDefault();
        const mx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        const my = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        const target = Math.min(4, Math.max(.25, Math.round(pinch.k0 * touchDist(e.touches) / pinch.d0 * 100) / 100));
        if (target !== zoom) zoomAt(mx, my, target / zoom, true);
        squashClickUntil = Date.now() + 250;
      } else if (touchStart && e.touches.length === 1){
        // Cancel native scrolling from the first move, before Chromium commits
        // the gesture to the finite scroll range. A stationary tap still clicks.
        e.preventDefault();
        moveGesture(touchStart, e.touches[0]);
      }
    }, {passive: false});
    mapStage.addEventListener('touchend', e => {
      if (pinch || touchStart?.moved) squashClickUntil = Date.now() + 250;
      if (pinch && e.touches.length === 1){
        touchStart = startGesture(e.touches[0]);
        touchStart.node = null;
      }
      if (e.touches.length < 2) pinch = null;
      if (!e.touches.length){ touchStart = null; mapStage.classList.remove('is-panning'); }
    });
    mapStage.addEventListener('touchcancel', () => {
      pinch = null; touchStart = null;
      mapStage.classList.remove('is-panning');
    });
    const onPanMove = e => {
      if (!drag || drag.id !== e.pointerId) return;
      const wasMoving = drag.moved;
      moveGesture(drag, e);
      if (!wasMoving && drag.moved && e.isTrusted && e.buttons) mapStage.setPointerCapture(e.pointerId);
    };
    const endPan = e => {
      if (!drag || (e?.pointerId != null && drag.id !== e.pointerId)) return;
      if (drag.moved) squashClickUntil = Date.now() + 250;
      drag = null;
      mapStage.classList.remove('is-panning');
    };
    window.addEventListener('pointermove', onPanMove);
    window.addEventListener('pointerup', endPan);
    window.addEventListener('pointercancel', endPan);
    mapStage.addEventListener('lostpointercapture', endPan);
    window.addEventListener('blur', endPan);
    // Capture phase, so the bubble handler that activates a node never sees it.
    mapCanvas.addEventListener('click', e => {
      if (Date.now() >= squashClickUntil) return;
      // A3: the chain panel lives in the canvas now, but its buttons are panel controls,
      // not map cards -- a drag that just ended must not swallow a mate jump or the ×.
      if (e.target instanceof Element && e.target.closest('.mm-chain-panel')) return;
      e.preventDefault();
      e.stopPropagation();
    }, true);
    mapStage.addEventListener('keydown',e => {
      if (e.target !== mapStage) return;
      const byKey = {ArrowLeft:[-48,0],ArrowRight:[48,0],ArrowUp:[0,-48],ArrowDown:[0,48]};
      if (!byKey[e.key]) return;
      e.preventDefault();
      cancelMapPan();
      // The same total the pointer drags, so arrow keys past the edge do not bounce
      // off the scroll wall either.
      const t0 = panTotal();
      setPanTotal(t0.x + byKey[e.key][0], t0.y + byKey[e.key][1]);
    });
    mapRoot.querySelector('.mm-view-controls').addEventListener('click',e => {
      const action = e.target.closest('[data-mm-view-action]')?.dataset.mmViewAction;
      // Buttons stay additive: two "Thu nhỏ" from 100% must read 80%, which the
      // zoom contract locks. The wheel is the multiplicative, cursor-anchored lever.
      if (action === 'zoom-in' || action === 'zoom-out'){
        const next = Math.min(4, Math.max(.25, Math.round((zoom + (action === 'zoom-in' ? .1 : -.1)) * 100) / 100));
        if (lastPointer) zoomAt(lastPointer.x, lastPointer.y, next / zoom);
        else setZoom(next);
      }
      if (action === 'reset' && pinnedMapNodes.size){
        cancelMapPan();
        const bounds = () => {
          const rects = [...mapCanvas.querySelectorAll(MM_CARD_SEL + ',.mm-chain-panel')]
            .map(el => el.getBoundingClientRect()).filter(rect => rect.width > 0);
          return {left:Math.min(...rects.map(r => r.left)), right:Math.max(...rects.map(r => r.right)),
            top:Math.min(...rects.map(r => r.top)), bottom:Math.max(...rects.map(r => r.bottom))};
        };
        const frame = mapStage.getBoundingClientRect();
        for (let i = 0; i < 5; i++){
          const b = bounds(), ratio = Math.min((frame.width - 20) / (b.right - b.left), (frame.height - 20) / (b.bottom - b.top));
          const next = Math.min(1, Math.max(.25, Math.floor(zoom * ratio * 100) / 100));
          if (next === zoom) break;
          setZoom(next);
          syncMapBranchLayout();
        }
        const b = bounds();
        const scale = frame.width / mapStage.offsetWidth || 1;
        panMapBy((b.left + b.right - frame.left - frame.right) / (2 * scale),
          (b.top + b.bottom - frame.top - frame.bottom) / (2 * scale));
      } else if (action === 'reset'){
        cancelMapPan();
        setPanTotal(0, 0);
        const fit = Math.min(1, mapStage.clientWidth / mapCanvas.offsetWidth,
          mapStage.clientHeight / mapCanvas.offsetHeight);
        setZoom(Math.floor(fit * 100) / 100);
        // CSS zoom can rewrap a label, changing the canvas height after scaling.
        // Step down until the canvas fits. A counted loop with a no-progress exit
        // replaces `while (zoom > .01)`: the clamp floor is .25 now, so below that
        // the old test never turned false and the tab hung on the first click.
        for (let guard = 0; guard < 12; guard++){
          const ratio = Math.min(mapStage.clientWidth / mapStage.scrollWidth,
            mapStage.clientHeight / mapStage.scrollHeight);
          if (ratio >= 1) break;
          const prev = zoom, percent = Math.round(zoom * 100);
          setZoom(Math.min(percent - 1, Math.floor(percent * ratio)) / 100);
          if (zoom >= prev) break;      // sitting on the floor: nothing left to give
        }
        // Both channels home, and AFTER the fit loop because the loop changes the
        // zoom the translate is divided by. Content back at the ORIGIN, not
        // centred: the fit contract reads scrollLeft==0 after this button, and
        // centring would satisfy the fit while failing that.
        setPanTotal(0, 0);
      }
      // The relation overlay is state, not a query, and it owes the same one-redraw
      // contract the zoom buttons use: switching it off has to actually clear the layer,
      // which the next layout pass would not do on its own.
      const relBtn = e.target.closest('[data-mm-view-action="relations"]');
      if (action === 'relations' && relBtn){
        mapRelationsOn = !mapRelationsOn;
        relBtn.setAttribute('aria-pressed', String(mapRelationsOn));
      }
      if (action === 'theme') toggleTheme();
      if (action) syncMapBranchLayout();
    });
    // A double-click on empty space restores the page-load state and ends the history.
    // A plain function on purpose: the glide wrappers call it synchronously, and
    // "Vừa khung" (FIT) is a different gesture that must never collapse branches.
    function resetMapToInitial(){
      pinnedMapNodes.clear();
      for (const card of mapCanvas.querySelectorAll('.mm-placed')){
        card.classList.remove('mm-placed');
        card.style.removeProperty('left');
        card.style.removeProperty('top');
      }
      mapSteps.length = 0;
      editingLookup = false;
      closeMapDetail({returnFocus:false});
      clearMapLit();
      mapSearch.value = '';
      updateLookup();
      for (const [tier] of mapBranches) setMapBranch(tier, false);
      // Relations restore to the TRUE load-state: ON-armed (Anh ruling 2026-11-25).
      // The overlay paints only on selection; reset clears it, so the layer stays empty.
      mapRelationsOn = true;
      const relBtn = mapRoot.querySelector('[data-mm-view-action="relations"]');
      if (relBtn) relBtn.setAttribute('aria-pressed', 'true');
      clearMapRelationLayer();
      cancelMapPan();
      setZoom(1);
      setPanTotal(0, 0);
      syncMapBranchLayout();
    }
    const clusterCount = MAP_CLUSTER_ORDER.filter(id => tools.some(t => mapClusterOf(t.fg || '') === id)).length;
    setText(document.getElementById('mm-inventory'), total + ' tool \u00b7 ' + groups.length + ' nh\u00f3m \u00b7 ' + clusterCount + ' cụm');
    for (const [tier,{button}] of mapBranches){
      const cs = mapClustersOf(tier);
      setText(button.querySelector('[data-mm-static]'), byTier[tier].length + ' tool \u00b7 ' + cs.length + ' cụm \u00b7 ' + cs.reduce((n,c2) => n + c2.groups.length, 0) + ' nh\u00f3m');
    }
    // Folding any node drops chips out of the flow, and a fold is the gesture Anh named
    // ("bam vao node T thi ca cay giat ve trang thai dau"), so the whole delegated body
    // runs inside glideCollapse. Opening a node GROWS the document, which cannot clamp
    // anything, and glideCollapse then adds no spacer at all -- so this wrapper is a
    // no-op on the way in and a glide on the way out.
    mapCanvas.addEventListener('click',glideHandler(e => {
      const button = e.target.closest('button');
      if (!button || !button.matches(MM_CARD_SEL + ',.mm-more') || button.closest('.mm-chain-panel')) return;
      if (!chainPanelEl || !button.matches('[data-mm-tool][aria-current="true"]')) rememberMapStep();
      closeLookup();
      if (button.dataset.mmMore){
        appendMapLeaves(button.closest('.mm-tools'));
        syncMapBranchLayout();
        return;
      }
      if (!button.dataset.mmTool){
        closeMapDetail();
        selectBranch(button);
      }
      const open = button.getAttribute('aria-expanded') === 'false';
      if (button.classList.contains('mm-tier')) revealMapBranch(button,setMapBranch(button.dataset.mmTier,open));
      else if (button.classList.contains('mm-cluster-btn')) revealMapBranch(button,toggleMapCluster(button.closest('.mm-cluster'),open));
      // A bucket node toggles like every other node: it leads out its chips, and
      // folding it takes their wires with it. Checked before data-mm-group because
      // the head button carries neither data-mm-tool nor data-mm-group. It goes
      // through revealMapBranch for the same reason the group and cluster lines do
      // -- opening one pushes its chips below the fold of a 576px stage, and a node
      // that appears to do nothing is worse than a rail that needs a scroll.
      else if (button.classList.contains('mm-bucket-btn')) revealMapBranch(button,toggleMapBucket(button.closest('.mm-bucket'),open));
      else if (button.dataset.mmGroup) revealMapBranch(button,setMapGroup(button,open));
      else if (button.dataset.mmTool) selectMapTool(button);
    }));
    // The observer is a SECOND writer of the same picture the toggles already drew
    // synchronously, so it goes through one rAF slot: a branch growing across twenty
    // frames used to ask for twenty full redraws, each of them a forced layout per
    // card. Measured before this coalescer: 525 rect measures for one 12-row page.
    // It deliberately does NOT skip a draw whose canvas box looks unchanged, which
    // would kill wires in the real case: .mm-canvas has min-height 366px, so a short
    // branch can gain or lose chips without moving the canvas box at all, and a
    // signature built on that box would then report "nothing to do".
    let mapDrawQueued = false;
    const requestMapDraw = () => {
      if (mapDrawQueued) return;
      mapDrawQueued = true;
      requestAnimationFrame(() => { mapDrawQueued = false; syncMapBranchLayout(); });
    };
    new ResizeObserver(requestMapDraw).observe(mapCanvas);
    syncMapBranchLayout();
    // updateLookup() opens with closeMapDetail(), so a keystroke that replaces an open leaf
    // collapses the panel exactly like the X button does. Same shield, same reason.
    // Searching and choosing a result are one navigation. Save the old selection
    // before the first keystroke closes it, including when moving between two tools.
    mapSearch.addEventListener('beforeinput', () => {
      if (!editingLookup) rememberMapStep();
      editingLookup = true;
    });
    mapSearch.addEventListener('input',glideHandler(updateLookup));
    mapSearch.addEventListener('keydown',e => {
      if (e.isComposing) return;
      if (e.key === 'Escape') { e.preventDefault(); closeLookup(); return; }
      if (e.key === 'Tab') { closeLookup(); return; }
      if (e.key === 'Enter' && !mapHits.hidden && searchOptions.length){ e.preventDefault(); chooseLookup(Math.max(0,activeOption)); }
      if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && mapHasQuery()){
        e.preventDefault();
        if (mapHits.hidden) glideHandler(updateLookup)();
        const count = searchOptions.length;
        if (count) setLookupOption(activeOption < 0 ? (e.key === 'ArrowDown' ? 0 : count - 1) : (activeOption + count + (e.key === 'ArrowDown' ? 1 : -1)) % count);
      }
    });
    mapHits.addEventListener('mousedown', e => e.preventDefault());
    mapHits.addEventListener('click',e => {
      const row = e.target.closest('[data-mm-hit]');
      if (row) chooseLookup(Number(row.dataset.mmHit));
    });
    // ISSUE 2: clicks inside the leaf chain panel. A mate jump unfolds its leaf and
    // selects it, exactly what chooseLookup does -- but without touching the search box.
    // BOUND ON DOCUMENT, kept after A3 moved the panel INSIDE #mm-canvas: panel clicks
    // now bubble through the canvas node delegate first, but it only acts on map cards
    // (data-mm-tool/group/tier...), never on .mm-chain-step / .mm-chain-panel-close, and
    // the drag-squash capture handler exempts the panel -- so mate/× still land here.
    document.addEventListener('click', e => {
      if (e.target.closest('.tab')) { clearChainPanel(); return; }
      if (e.target.closest('[data-mm-inspect]')){
        if (mapDetail.hidden) rememberMapStep();
        openMapInfo();
        return;
      }
      if (e.target.closest('.mm-chain-panel-close')){
        rememberMapStep();
        e.stopPropagation();
        clearChainPanel();
        syncMapBranchLayout();
        mapCanvas.querySelector('button[data-mm-tool][aria-current="true"]')?.focus({preventScroll:true});
        return;
      }
      const mate = e.target.closest('[data-mm-chain-tool]');
      if (!mate) return;
      e.stopPropagation(); e.preventDefault();
      const name = mate.dataset.mmChainTool;
      const routeName = activeRoute?.name;
      if (name === mapSelected) return;
      rememberMapStep();
      const tool = mapToolIndex.get(name);
      if (!tool) return;
      const panel = setMapBranch(tool.t, true);
      const clusterId = mapClusterOf(tool.fg || '');
      const cluster = [...panel.querySelectorAll('.mm-cluster')].find(el => el.dataset.mmCluster === clusterId);
      if (cluster) toggleMapCluster(cluster, true);
      const group = mapGroupKey(tool);
      const gb = [...panel.querySelectorAll('[data-mm-group]')].find(el => el.dataset.mmGroup === group);
      const list = gb ? setMapGroup(gb, true) : null;
      if (list){
        const btn = findMapLeaf(list, name);
        if (btn){
          // The destination selection owns the reveal.
          selectMapTool(btn, routeName, true);
          const focusTarget = chainPanelEl?.querySelector('[aria-current="step"]') || chainPanelEl?.querySelector('.mm-chain-panel-close') || btn;
          focusTarget.focus({preventScroll:true});
        }
      }
    });
    // Every one of these can END a leaf, and ending one takes the 378px panel out of the
    // flow. glideHandler is the collapse shield (see shieldMapHeight): without it the
    // document height falls in one frame and the browser clamps scrollY with it, which is
    // the "giựt" Anh photographed. The document listener is NOT wrapped because closing the
    // lookup dropdown cannot change the height -- .mm-hits is out of the map flow.
    // mapSearch.focus() carried preventScroll:false and was measured moving scrollY 483->0
    // (cause_probe stack trace). The glide owns that movement now, so the focus stays put.
    mapSearchClear.addEventListener('click',glideHandler(() => {
      rememberMapStep();
      mapSearch.value = '';
      updateLookup();
      mapSearch.focus({preventScroll:true});
    }));
    document.addEventListener('click',e => { if (!e.target.closest('.mm-lookup')) closeLookup(); });
    mapDetail.addEventListener('click', glideHandler(e => {
      if (e.target.closest('[data-mm-close]')){
        rememberMapStep();
        closeMapDetail({returnFocus:true});
      }
      if (e.target.closest('[data-mm-inventory]') && mapSelected) revealInInventory(mapSelected);
    }));
    mapDetail.addEventListener('keydown', glideHandler(e => {
      if (e.key !== 'Escape') return;
      rememberMapStep();
      closeMapDetail({returnFocus:true});
    }));
    // Hover lights wires and paints a tooltip. It never writes #mm-detail: v5
    // \u00a71.8 excludes hover from the panel writers, and matrix.js:807 says so.
    mapWires.addEventListener('pointerover',e => {
      const hit = e.target.closest('.mm-wire-hit');
      if (!hit) return;
      const entry = wireEntries.find(w => w.hit === hit);
      if (!entry) return;
      lightWire(entry);
      playMapTone(520 + (entry.kind === 'tool' ? 140 : entry.kind === 'group' ? 70 : 0), 0.09, 'triangle');
      showTip('<div style="font-size:13px;font-weight:700">' + esc(wireLabel(entry.a)) + ' &rarr; ' + esc(wireLabel(entry.b)) + '</div>'
        + '<div style="color:var(--fg3);font-size:11px">' + esc(mapKindLabels[entry.kind] || entry.kind) + ' &middot; ' + esc(entry.tier)
        + (entry.group ? ' &middot; ' + esc(mapGroupName(entry.group)) : '') + '</div>', e.clientX, e.clientY);
    });
    mapWires.addEventListener('pointermove',e => {
      if (!e.target.closest('.mm-wire-hit')) return;
      const entry = wireEntries.find(w => w.hit === e.target);
      if (entry) showTip('<div style="font-size:13px;font-weight:700">' + esc(wireLabel(entry.a)) + ' &rarr; ' + esc(wireLabel(entry.b)) + '</div>', e.clientX, e.clientY);
    });
    mapWires.addEventListener('pointerout',e => {
      if (e.target.closest('.mm-wire-hit') && !e.relatedTarget?.closest?.('.mm-wire-hit')) clearMapLit();
    });
    mapCanvas.addEventListener('pointerover',e => {
      const hit = e.target.closest('.mm-wire-hit');
      if (hit) return;                       // the wire handler owns this pointer
      const node = e.target.closest('button');
      if (!node || !node.matches(MM_CARD_SEL) || hoveredCard === node) return;
      lightNode(node);
      playMapTone(node.dataset.mmTool ? 660 : 430, 0.08, 'sine');
      const tool = node.dataset.mmTool ? mapToolIndex.get(node.dataset.mmTool) : null;
      if (tool) showTip(tipHTML(tool), e.clientX, e.clientY);
      else if (node.dataset.mmGroup || node.classList.contains('mm-cluster-btn') || node.classList.contains('mm-tier') || node.classList.contains('mm-bucket-btn'))
        showTip('<div style="font-size:13px;font-weight:700">' + esc(wireLabel(node)) + '</div>'
          + '<div style="color:var(--fg3);font-size:11px">' + esc(node.dataset.mmTier || '') + ' &middot; '
          + ((nodeWires.get(node) || []).length) + ' d\u00e2y</div>', e.clientX, e.clientY);
    });
    mapCanvas.addEventListener('pointerout',e => {
      if (e.target.closest('.mm-wire-hit')) return;
      const node = e.target.closest('button');
      if (node && hoveredCard === node && !node.contains(e.relatedTarget)) clearMapLit();
    });
    mapCanvas.addEventListener('pointerleave', clearMapLit);
    // A3: the chain panel is a canvas child now, so scroll carries it with the map --
    // no viewport re-pin here (the old call re-pinned a fixed panel on every scroll).
    mapStage.addEventListener('scroll', () => { if (mapCanvas.classList.contains('mm-wire-hover')) hideTip(); }, {passive:true});
    // Sound ships off. One persisted choice, announced to assistive tech.
    const soundBtn = mapRoot.querySelector('[data-mm-view-action="sound"]');
    if (soundBtn){
      const on = localStorage.getItem(MAP_SOUND_KEY) === '1';
      mapRoot.dataset.mmSound = String(on);
      soundBtn.setAttribute('aria-pressed', String(on));
      soundBtn.addEventListener('click', () => {
        const next = mapRoot.dataset.mmSound !== 'true';
        mapRoot.dataset.mmSound = String(next);
        soundBtn.setAttribute('aria-pressed', String(next));
        try { localStorage.setItem(MAP_SOUND_KEY, next ? '1' : '0'); } catch (err) {}
        if (next) playMapTone(720, 0.1, 'triangle');
      });
    }
    // C5: persisted theme choice. A stored value wins over the system scheme; with
    // nothing stored the page keeps its media-query palette and NO data-theme stamp
    // (the both-palette WCAG lock reads fresh contexts). The stamp is runtime-only --
    // the source <html> carries no data-theme (string lock).
    const themeBtn = mapRoot.querySelector('[data-mm-view-action="theme"]');
    const THEME_KEY = 'toola_matrix_theme';
    function systemTheme(){ return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'; }
    function effectiveTheme(){ return document.documentElement.dataset.theme || systemTheme(); }
    function syncThemeButton(){ if (themeBtn) themeBtn.setAttribute('aria-pressed', String(effectiveTheme() === 'light')); }
    function toggleTheme(){
      const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem(THEME_KEY, next); } catch (err) {}
      syncThemeButton();
    }
    try {
      const stored = localStorage.getItem(THEME_KEY);
      if (stored === 'light' || stored === 'dark') document.documentElement.dataset.theme = stored;
    } catch (err) {}
    syncThemeButton();
    // C2: page shortcuts. '/' focuses search, 't' flips the theme, digits 1..6 switch
    // tabs through the existing click path. They never fire while typing or with a
    // modifier, and they leave the Arrow-pan and Escape contexts untouched.
    document.addEventListener('keydown', e => {
      if (e.isComposing || e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      if (e.key === '/') {
        const field = document.querySelector('.panel.active input[type="search"], .panel.active #sb-search');
        e.preventDefault();
        if (!field) document.getElementById('tab-mindmap').click();
        (field || mapSearch).focus();
        return;
      }
      if (e.key === 't' || e.key === 'T'){ toggleTheme(); return; }
      if (e.key >= '1' && e.key <= '6'){
        const tab = document.querySelectorAll('#tabs .tab')[Number(e.key) - 1];
        if (tab) tab.click();
      }
    });
    renderMap();
  }
  var liveState = {seq:null, boot:null, lastFullAt:0, events:[], services:{}, serviceStatus:null, mapCounts:null, inFlight:false, status:'connecting'};
  function renderToolActivity(name){
    const target = document.getElementById('sb-tool-activity');
    if (!target || !liveState) return;
    const count = liveState.events.filter(e => e.tool === name).length;
    target.textContent = 'Hoạt động · ' + (liveState.boot === null ? 'chưa có dữ liệu' : count + ' sự kiện trong ' + liveState.events.length + ' mục gần nhất')
      + ' · ' + liveState.status + ' · không dùng để tính liên kết.';
  }
  async function liveCycle(){
    if (liveState.inFlight) return;
    liveState.inFlight = true;
    const now = Date.now();
    const full = liveState.seq === null || now - liveState.lastFullAt >= 60000;
    const query = full ? '' : '?' + new URLSearchParams({since_seq:liveState.seq, boot_id:liveState.boot});
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/monitor/activity' + query, {cache:'no-store', signal:controller.signal});
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const d = await response.json();
      if (!d || typeof d.boot_id !== 'string' || !Number.isInteger(d.snapshot_seq) || !Array.isArray(d.events)) throw new Error('bad snapshot');
      if (d.delta && (full || d.truncated || d.boot_id !== liveState.boot || d.snapshot_seq < liveState.seq)) throw new Error('bad cursor');
      if (!d.delta){
        liveState.lastFullAt = now;
        liveState.services = {};
        liveState.serviceStatus = null;
      }
      const previousStamp = liveState.status === 'live' && d.boot_id === liveState.boot ? liveState.mapCounts?.stamp : null;
      const previousEvents = previousStamp ? new Map(liveState.events.map(e => [e.id,e])) : null;
      const entries = new Map();
      (d.delta ? liveState.events.concat(d.events) : d.events).forEach(e => { if (e && e.id) entries.set(e.id,e); });
      liveState.events = [...entries.values()].sort((a,b) => (b.epoch || 0) - (a.epoch || 0)).slice(0,240);
      if (d.boot_id !== liveState.boot) liveState.mapCounts = null;
      liveState.boot = d.boot_id;
      liveState.seq = d.snapshot_seq;
      if (Array.isArray(d.services)) liveState.services = Object.fromEntries(d.services.map(s => [s.name,s]));
      liveState.serviceStatus = d.section_state?.services || null;
      if (liveState.serviceStatus === 'fresh' && !(Number.isFinite(d.section_age_s?.services) && d.section_age_s.services <= 15 && d.section_age_s.services >= 0)) liveState.serviceStatus = 'stale';
      const stamp = d.section_built_at?.events;
      const usableStamp = Number.isFinite(stamp) && stamp > 0;
      const age = d.section_age_s?.events;
      const fresh = d.section_state?.events === 'fresh' && usableStamp && Number.isFinite(age) && age >= 0 && age <= 15;
      if (usableStamp && (fresh || !liveState.mapCounts)) {
        liveState.mapCounts = mapCounts(liveState.events, stamp);
      }
      liveState.status = fresh ? 'live' : 'stale';
      if (fresh && previousEvents && stamp >= previousStamp){
        const active = new Set();
        for (const e of entries.values()){
          if (!isCompleted(e) || !Number.isFinite(e.epoch) || e.epoch <= stamp - 300 || e.epoch > stamp) continue;
          const prior = previousEvents.get(e.id);
          if (prior ? isCompleted(prior) : e.epoch <= previousStamp) continue;
          const tier = mapToolIndex.get(e.tool)?.t;
          if (tier) active.add(tier);
        }
        for (const tier of active) animateBranch(tier,'live');
      }
    } catch (error) {
      liveState.seq = null;
      liveState.status = 'offline';
    } finally {
      clearTimeout(timeout);
      liveState.inFlight = false;
      renderMap();
      if (radialInitialized && radialPanel.classList.contains('active')) {
        renderLive(liveState.status);
        if (detailState.kind === 'tool') renderToolActivity(detailState.name);
      }
    }
  }
  function renderLive(status){
    dashBridgePill.textContent = 'bridge [' + status + ']';
    dashBridgePill.className = 'dash-pill ' + (status === 'live' ? 'ok' : status === 'stale' ? 'warn' : status === 'connecting' ? '' : 'bad');
    dashStatusEl.innerHTML = dashStatusHTML(status === 'connecting' ? null : {status, up:status !== 'offline', services:liveState.services});
    dashLiveStateEl.textContent = '· ' + status;
    const events = liveState.events.slice(0,12);
    const note = status === 'offline' ? (events.length ? 'Giữ bản cuối · đang thử kết nối lại.' : 'Bridge offline — đồ thị tĩnh vẫn dùng được.')
      : status === 'stale' ? 'Dữ liệu nguồn đã cũ hoặc chưa sẵn sàng.' : status === 'connecting' ? 'Đang kết nối…' : events.length ? '' : 'Chưa có sự kiện.';
    dashLiveEl.innerHTML = (note ? '<p>' + note + '</p>' : '') + events.map(e => {
      const ok = e.status === 'ok';
      return '<div class="dash-log-row" title="Session: ' + esc(e.session || '—') + '"><span class="lt">' + esc(e.ts || '') + '</span><span>' + esc(e.agent || '?') + '</span><span>' + esc(e.tool || '?') + '</span><span class="' + (ok ? 'ok' : 'err') + '">' + esc(e.status || '?') + '</span><span class="lt">' + (typeof e.dur_ms === 'number' ? e.dur_ms + 'ms' : '') + '</span></div>';
    }).join('');
  }
  function tickClock(){
    if (dashClockEl) dashClockEl.textContent = new Date().toLocaleTimeString('vi-VN', { hour12: false });
  }
  function initDashboard(){
    buildDashStages();
    if (dashStaticEl){
      dashStaticEl.innerHTML = dashPills.map(function(p){
        return '<button class="dash-pill" data-pill="' + p.key + '">' + esc(p.label) + ' [' + p.count + ']</button>';
      }).join('');
      dashStaticEl.addEventListener('click', function(ev){
        var b = ev.target.closest ? ev.target.closest('[data-pill]') : null;
        if (!b) return;
        setPillFilter(b.getAttribute('data-pill'));
      });
    }
    if (dashStagesEl){
      dashStagesEl.addEventListener('click', function(ev){
        var b = ev.target.closest ? ev.target.closest('[data-tier]') : null;
        if (!b) return;
        onTierClick(b.getAttribute('data-tier'));
      });
    }
    setDetailRoot();
    drawWires();
  }

  const radialPanel = document.getElementById('panel-sunburst');
  let radialInitialized = false;
  document.querySelector('[data-tab="sunburst"]').addEventListener('click', () => {
    if (!radialInitialized) { initDashboard(); radialInitialized = true; }
    renderLive(liveState.status);
    if (detailState.kind === 'tool') renderToolActivity(detailState.name);
    tickClock();
  });
  setInterval(() => { if (radialPanel.classList.contains('active')) tickClock(); }, 1000);
  initMap();
  liveCycle();
  setInterval(liveCycle, 5000);
})();
