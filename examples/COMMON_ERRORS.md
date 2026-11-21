# COMMON_ERRORS — phiếu tra lỗi ToolA cho CA (Guidance v11)

Nguồn: GUIDANCE_V11_PACKAGE_20261121.md §3, đã hiệu chỉnh theo
GUIDANCE_V11_VERIFY_REPORT_CODO_20261121.md (41. CLAUDELTNAI). Ngày 21/11/2026.
Mỗi dòng là hành động tiếp theo ĐÃ đối chiếu code/schema; các lời khuyên sai của
bản v10 đã bị loại. Quy tắc chung (một SOT tại schema đang serve, khóa
`usage_guidance`): giữ đối tượng/phạm vi/hiệu ứng của tác vụ khi retry; bản sửa
máy móc có thể mất ý định — đọc `corrected_payload_source`.

| Mã | Lỗi | CA làm gì tiếp |
|---|---|---|
| G1 | TOOL_ERROR | Đây là nhãn fallback của dispatcher, không chứng minh script sai hay server đúng. Đọc exit_code + stdout/stderr liên quan. Test bắt được lỗi sản phẩm là kết quả hợp lệ của tác vụ test; chỉ sửa lời gọi khi tìm được lỗi TRONG lời gọi. |
| G2 | FILE_NOT_FOUND | Đối chiếu project_path và đường dẫn với nguồn sinh path. Chưa biết tên thực thì liệt kê ĐÚNG thư mục cha (không bắt buộc inventory trước mọi read/write). Dùng fuzzy_suggestions như gợi ý; code-index có giới hạn, file bị ignore có thể không xuất hiện. Tạo file mới: kiểm thư mục cha rồi write_new_file. |
| G3 | UNSUPPORTED_FIELD | Đối chiếu unsupported_fields / accepted_fields / accepted_aliases / suggestions. Đọc corrected_payload_source: alias_mapped_intent_preserved = ý định đã được dịch sang trường đúng, dùng được; stripped_unsupported_fields = có thể MẤT ý định (vd git_diff cached=true nghĩa là scope=staged; cbm_template_query from_symbol nghĩa là name=<symbol>) — điền lại giá trị thật theo tác vụ rồi retry một lần. Không gửi bản sửa máy móc khi nó mất ý định. |
| G4 | MISSING_REQUIRED_FIELD | Điền trường được nêu ở schema HOẶC lỗi từ handler — hai nguồn có thể khác nhau theo mode. Không giả định luôn có accepted_fields hay corrected_payload; corrected_payload có thể chứa null. Điền null bằng giá trị thật từ bước sinh dữ liệu. |
| G5 | SCRIPT_TIMEOUT | Phân biệt timeout của child, timeout của client, và việc đã chạy một phần. Timeout mặc định THEO SCHEMA ĐANG SERVE: run_project_command 60s; run_ps1_script / run_project_test_script 120s (default trong code có thể khác schema; MCP ceiling không áp cho HTTP). Đọc log/trạng thái, thu gọn việc hoặc tăng timeout có chủ đích. Kiểm hiệu ứng đã xảy ra trước khi chạy lại. |
| G6 | UNKNOWN_TOOL | Tìm tên hiện hành theo tác vụ (GET /api/tools/names, suggestions/surface trong lỗi). Alias deprecated vẫn được nhận nhưng names mặc định ẩn chúng: đọc đoạn file → read_file_range (read_chunks trỏ về đây, KHÔNG phải search_code smart); regex → search_code(mode=regex); callers → find_symbol(include=callers) hoặc search_code(mode=callers). |
| G7 | RANGES_WITH_LEGACY_PARAMS | Chọn MỘT trong hai body: một đoạn {file_path, start_line, end_line} hoặc nhiều đoạn {file_path, ranges:[{start_line,end_line},...]} (tối đa 16). Khi dùng ranges, bỏ start_line/end_line/max_lines cấp ngoài. Ví dụ serve của read_file_range là body một đoạn. |
| G8 | PATH_QUOTING_ERROR | Tách executable, từng đối số và shell; quote đúng thành phần có khoảng trắng, không chép mù mẫu bọc cả command. retry_example chỉ có ở nhánh stderr — nhánh chặn trước spawn không có; kiểm spawn_attempted/exit_code trước khi chạy lại thao tác gây hiệu ứng. |
| G9 | STALE_TOOL_DEFINITION | 409 đã kèm pushed_schema và version: dùng schema đã push, không cần fetch thêm. Nếu dùng cơ chế pin, thay definition_version bằng bản đang serve. |
| G10 | SCHEMA_VIOLATION | Đọc field/kiểu/điều kiện lỗi cụ thể của site đó (mỗi site có contract riêng; vd definition_version phải là chuỗi không rỗng — nhánh này trả pushed_schema nhưng KHÔNG có corrected_payload). Thiếu thông tin thì đọc schema của đúng tool. |
| G11 | REGEX_INVALID | Muốn tìm chuỗi nguyên văn như `foo(` → mode=text và giữ query (server trả corrected_payload mode=text). Cần regex thật → sửa cú pháp, giữ mode=regex. Lưu ý: text/smart tự escape metacharacter, nên literal có dấu ngoặc không tự gây lỗi ở các mode đó. |
| G12 | REGEX_SEARCH_TIMEOUT | Timeout có thể do lượng input/tải, không chỉ do query shape. Dùng thông tin attempt/partial; thu hẹp tập file hoặc đơn giản hóa mẫu (adaptive retry có sẵn). Chỉ tăng regex_deadline_seconds trong giới hạn được serve khi thực sự cần giữ regex. |
| G13 | SEARCH_TIMEOUT | Dùng accepted_narrowing_fields CỦA ĐÚNG MODE đã gọi: mode=callers thu hẹp bằng path (không phải file_pattern); các mode khác theo danh sách trả về. SEARCH_TEXT_DEADLINE_SECONDS mặc định 60s, có thể cấu hình. |
| G14 | GIT_COMMAND_FAILED | Đọc exit_code và thông báo Git/hook — nguyên nhân khác nhau (không phải repo, hook chặn code...). Xác minh repo khi sai gốc; hook bắt lỗi code thì sửa lỗi đã báo trước khi commit lại. Không mặc định chạy pyright cho mọi lỗi Git. |
| G15 | INVALID_GIT_DIFF_SCOPE | scope nhận worktree / staged / both — chọn theo lớp diff người dùng cần (staged = phần đã stage, tức git diff --cached). `cached` không phải scope value; ở HTTP cached=true được remap sang scope=staged (v11), `unstaged` là giá trị không hợp lệ. |

## Ghi chú sử dụng

- Bảng này là tài liệu tra cứu, KHÔNG phải rule bắt buộc nạp mỗi phiên; hướng
  dẫn hành vi ngắn nằm tại `usage_guidance` trong schema mỗi tool (SOT:
  `_V11_GUIDANCE` trong orchestrator_server.py). Không nhân bản khối hướng dẫn
  vào SLIM/CORE trừ khi pilot chứng minh cần (claim v11 §2a).
- Khi bảng và schema đang serve mâu thuẫn, schema đang serve thắng — bảng có
  thể cũ hơn daemon.
