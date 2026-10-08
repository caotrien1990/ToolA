# Fix D2/D3 external_reconcile — 2026-10-07

Codo thực hiện theo báo cáo SOT của Cozo:
`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\24. ZCODE\BAO_CAO_audit_external_reconcile_2026-10-06.md`

Anh đã chọn D3: **cách ly file lỗi, tiếp tục đồng bộ phần còn lại và báo rõ snapshot thiếu file nào**. Không xóa file nguồn.

## Kết quả

D2: `_reconcile_external_files` dùng `get_file_hash_stat_gated` của P10. Vẫn duyệt/stat các file trong scope; nguồn không đổi sau lần hash đầu không bị đọc+hash lại. File được ghi và file có stat signature thay đổi vẫn được capture.

D3:
- Capture lỗi được lưu trong sidecar và `INDEX.snapshot_omitted_files` dạng đường dẫn tương đối → lý do.
- Không giữ entry/symbol cũ của file lỗi trong INDEX hoặc snapshot mới.
- Cả incremental, rebuild, periodic refresh và full-walk fallback dùng cùng danh sách loại trừ.
- File lỗi được thử capture lại ở lần đồng bộ sau; khi thành công, cảnh báo tự hết và file trở lại snapshot. Khi file bị xóa, omission hết ở lần reconcile sau.
- `write_new_file` và `write_fix` báo `cache_sync_status: partial`, `snapshot_omitted_files`, `cache_sync_warning`. `cache_synced: true` ở đây nghĩa là phần hợp lệ đã được đồng bộ, không có nghĩa snapshot bao phủ mọi file nguồn.
- Lỗi hạ tầng/cache không bị giả thành cách ly thành công: helper trả false, ghi traceback và đưa `cache_sync_error` với type/message vào response. Deadline vẫn propagate.
- Lỗi đọc/hash của một source được đưa qua capture để ghi lý do cách ly; lỗi ghi sidecar/storage vẫn làm đồng bộ thất bại.

Phải sửa thêm hai call-site trong `orchestrator/tools/write/core.py` để thông tin cách ly đi tới response HTTP.

## Kiểm tra hồi quy

Bộ D2/D3 ban đầu: **9 failed trước sửa → 9 passed sau sửa**.
Bổ sung vòng đời quarantine: phát hiện lỗi PermissionError tại hash vẫn thoát reconcile; sửa để capture ghi lý do rồi tiếp tục.

Bộ hồi quy mở rộng: 115 passed, 1 failed, 1 skipped. Failure duy nhất là wrapper mock của test health chưa nhận tham số diagnostics mới. Đã cập nhật chữ ký wrapper; giữ nguyên delay và assertions về health responsiveness. Chạy lại nhóm liên quan: **17 passed**.

Gộp kết quả mới nhất theo định danh từng test từ hai JUnit XML: **116 test khác nhau đạt, 0 lỗi còn lại, 1 skipped**. Không phải một lần chạy toàn bộ repo.

Artifact kiểm chứng:
- `.orchestrator_cache/test_results/d2_d3_regression.xml`
- `.orchestrator_cache/test_results/d2_d3_targeted.xml`

Các module gồm: external_reconcile_d2_d3, external_reconcile_quarantine_lifecycle, external_write_reconcile_scope, cache_delta_isolation, incremental_index_and_snapshot, snapshot_periodic_refresh_a30, snapshot_deadline_and_root_guard, autoheal_write_index_r1, write_cache_sync_offload_r51, write_base_and_session_disclosure, snapshot_retry_reconcile, surgical_mapper_atomic_cache, search_snapshots, search_snapshot_integration, project_ignore_snapshot_b027b.

Ca skip cần oversized snapshot store có sẵn tại HOME; không có fixture nên bỏ qua, không tính pass.
Auto-validation của các lần write source đều đạt. Diff check không có lỗi whitespace.

## Đo D2 bằng code thật có instrumentation

Fixture:
`C:\Users\ADMIN\AppData\Local\Temp\codo_d2_benchmark_q8b14gpn`

121 source files, mỗi lần update ghi một file:
- Cold: 121 raw source hash calls, gồm 120 file không đổi; 0.0873 giây.
- Warm: 1 raw source hash call cho file được ghi, **0 hash call cho file không đổi**; 0.0797 giây.

Đây là phép đo sandbox, không suy ra mức tăng tốc trên 49. CUA. D2 bỏ đọc nguồn dư thừa, không loại bỏ os.walk/stat, đọc cache hoặc công việc snapshot khác.

## HTTP A/B và restart

Fixture:
`C:\Users\ADMIN\AppData\Local\Temp\codo_d23_live_ab_cmycmuzk`

Hai project control/poisoned có cùng 10 file Python khỏe; poisoned có thêm `empty.js` rỗng. Dùng parser thật, không mock trong daemon.

Trước restart:
- Control write_new_file: ok=true, cache_synced=true (0.280s).
- Poisoned write_new_file: ok=true, cache_synced=false, cache_sync_status=pending_or_failed (0.219s).

Restart qua `restart_orchestrator_via_bat(delay_seconds=2)`, sau kiểm tra tracker không có worker đang chạy.
- Trước: PID 26252, generation 80691394890b.
- Sau: PID 4600, generation 079ec52ea1f5, /health ok=true.

Sau restart:
- Control new/fix: đều ok=true, cache_synced=true, không có omission.
- Poisoned new/fix: đều ok=true, cache_synced=true, cache_sync_status=partial.
- Response nêu `empty.js: __init__() takes exactly 1 argument (2 given)`.
- Thời gian poisoned new/fix: 0.305s / 0.478s.

Pin snapshot kiểm chứng:
- Cả hai snapshot có 12 file khỏe và bytes của `after.py` đúng `VALUE = 201\n`.
- Poisoned snapshot không có empty.js; pinned INDEX giữ danh sách omission và lý do.
- File empty.js vẫn tồn tại, 0 byte; Codo không xóa.
- File `before.py` từ lần ghi trước sửa cũng có trong snapshot mới.

## Trạng thái file gốc và giới hạn

Kiểm tra read-only ngày 2026-10-07:
`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\24. ZCODE\clean_full_simulation_engine.js`
hiện tồn tại với **39.512 byte**, không còn rỗng như lúc Cozo audit. MD5:
`3b7a3373953197f8dd8508890178b05f`.
analyze_file vẫn gặp `__init__() takes exactly 1 argument (2 given)`. Không xác định ai đã thay đổi file; Codo không sửa/xóa file này.

Bản sửa xử lý cách ly và công bố thiếu dữ liệu, không sửa dependency/parser tree-sitter. Chưa chạy write toàn project 24. ZCODE để đo tất cả các file parser lỗi của project đó.

P10 có giới hạn có chủ ý: warm memo không phát hiện in-place rewrite đồng thời khôi phục đúng size, mtime_ns và inode. Cold probe hoặc refresh toàn phần vẫn đo lại. Vì vậy nhận xét “luôn bắt equal-stat edits” trong báo cáo D1 trước đó không còn là hợp đồng của D2 này.

Không commit; giữ các thay đổi có sẵn. Các PROJECT_MAP/PROJECT_CONTEXT là output tự sinh của ToolA. Không xóa fixture/probe.

## Source/test thay đổi trong đợt D2/D3

- surgical_mapper.py
- code_tools.py
- orchestrator/tools/write/core.py
- tests/test_external_reconcile_d2_d3.py (mới)
- tests/test_external_reconcile_quarantine_lifecycle.py (mới)
- tests/test_autoheal_write_index_r1.py (chữ ký wrapper test)
