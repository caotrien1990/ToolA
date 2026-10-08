# Reaudit và fix external_reconcile — 2026-10-07

Người thực hiện: Codo. Yêu cầu của Anh: reaudit, verify và sửa lỗi ToolA quét repo tổ tiên khi ghi file ngoài project; đã được cho phép “Tiến hành”, tiếp tục qua các lượt “tiếp”.

## Kết luận

Đã xác nhận lỗi và sửa tại nơi chọn root dùng chung: `code_tools.py:707`, hàm `_update_cache_after_write`.

- Có `project_root` được chỉ định và file thực sự nằm trong root đó: dùng root của project.
- Bỏ trống `project_root`, hoặc file nằm ngoài root được chỉ định: dùng thư mục cha trực tiếp của đường dẫn file sau `resolve()`.
- Không dùng marker ở thư mục tổ tiên để mở rộng phạm vi của một write ngoài project.
- Root này chi phối cả cập nhật INDEX, `external_reconcile`, refresh định kỳ, retry đồng bộ và publish snapshot. Chỉ đổi riêng `os.walk` sẽ không chặn được các nhánh quét lại còn lại.
- Trường hợp không truyền project trước đây có thể không tạo cache nếu không tìm thấy marker; nay đồng bộ cache tại thư mục cha của file.

“Thư mục cha” ở đây là root của cây con được reconcile, bao gồm các thư mục con được bộ lọc hiện hữu cho phép.

## Bằng chứng reaudit

Báo cáo đầu vào:
`D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\24. ZCODE\BAO_CAO_audit_external_reconcile_2026-10-06.md`

Trước sửa, bộ test hồi quy ban đầu tái hiện **7 failed, 4 passed**:
- Không truyền project hoặc truyền chuỗi rỗng: chọn nhầm ancestor có marker.
- Project được truyền không chứa file: cache-sync thất bại.
- Gọi write_new_file không truyền project: INDEX của ancestor bị thay đổi.
- In-project và các tình huống đã có cache ngay tại parent vẫn có thể đạt, nên chỉ test trường hợp này sẽ bỏ lọt lỗi.

Code hiện hữu xác nhận `_reconcile_external_files` quét root, hash nguồn và fail khi capture lỗi. Nhánh full refresh định kỳ cũng nhận cùng root. Bản sửa đặt ranh giới ở đầu pipeline để các nhánh dùng cùng phạm vi.

## Kiểm tra sau sửa

**63 passed, 1 skipped** trên 6 module liên quan:

| Module | Phạm vi |
|---|---|
| tests/test_external_write_reconcile_scope.py | 11 ca: chọn root; reconcile thường và refresh định kỳ; ancestor INDEX không đổi; snapshot đúng bytes; write_new_file và write_fix; equal-stat drift trong project |
| tests/test_incremental_index_and_snapshot.py | INDEX tăng dần và nội dung snapshot |
| tests/test_snapshot_deadline_and_root_guard.py | Deadline và guard root |
| tests/test_autoheal_write_index_r1.py | Write/index và autoheal |
| tests/test_xroot_policy.py | Chính sách cross-root |
| tests/test_xroot_core_rule_sot.py | Contract cross-root |

Bốn module đầu: 35 passed, 1 skipped. Hai module cross-root: 28 passed. Module mới được chạy lại sau khi bổ sung kiểm tra write_fix: 11 passed.

Test bị skip phụ thuộc snapshot store quá cỡ có sẵn ở HOME; dữ liệu đó không có. Không tính skip là pass. Không chạy toàn bộ test suite của repo.

ToolA auto-validation khi áp dụng source: syntax OK, symbol_check OK, cache_synced=true. `git diff --check` đạt.

## Daemon và kiểm chứng HTTP thật

Restart qua `restart_orchestrator_via_bat(delay_seconds=2)`, sau khi tracker chỉ hiển thị dispatch hoàn tất:
- Trước: PID 54512, generation f6c63c2e85eb.
- Sau: PID 62332, generation 84405da08a22.

Fixture:
`C:\Users\ADMIN\AppData\Local\Temp\codo_external_scope_live_tntk1_67`

Ancestor có marker, INDEX đã seed, 120 file Python không liên quan và một poison file Python. Hai thư mục đích thử riêng:
- project_path rỗng;
- project_path trỏ một project khác không chứa file.

Mỗi trường hợp chạy write_new_file lần đầu, write_new_file lần thứ hai khi INDEX đã tồn tại, rồi read_file_range → write_fix. **6/6 API write đạt ok=true và cache_synced=true**.

| Ca | Thời gian quan sát, giây |
|---|---:|
| project rỗng, new lần đầu | 0.658 |
| project khác, new lần đầu | 0.256 |
| project rỗng, new lần hai | 2.106 |
| project rỗng, write_fix | 0.345 |
| project khác, new lần hai | 0.482 |
| project khác, write_fix | 0.341 |

Đây là thời gian fixture, không phải benchmark tốc độ trên repo production.

Assertions kiểm chứng qua ToolA:
- SHA256 INDEX ancestor trước/sau giống nhau: `b9295cc0acf72877690deeab252b91707db6e8dd8baf83c80ca8d9754bbea5df`.
- Snapshot mỗi parent chỉ chứa `probe.py` và `second.py`, bytes đúng sau write_fix.
- Project khác được truyền vào không phát sinh INDEX của các file ngoài project.
- Poison file ngoài phạm vi không chặn cache-sync.

## Phạm vi và giới hạn

- Sửa D1 đối với write ngoài project; không thay thiết kế reconcile toàn root của write trong project.
- Giữ hash nội dung trong scope để tiếp tục phát hiện thay đổi có cùng size/mtime; không thay bằng stat-only rồi bỏ mất cam kết này.
- Không sửa parser hoặc cơ chế nuốt exception tổng quát đã nêu ở D3. File lỗi ngay trong scope được chọn vẫn có thể làm cache-sync thất bại. Trạng thái poison .js tại 24. ZCODE sau sửa chưa được đo lại.
- CBM status/DSM không cung cấp được graph tin cậy trong budget (freshness unknown / unavailable / deadline); phạm vi sửa được đối chiếu bằng caller và source bounded, cùng test hành vi.
- Không xác minh lại các số liệu hiệu năng lịch sử của 49. CUA trong báo cáo gốc.
- Dry-run trên file code_tools.py lớn báo lỗi syntax ở dòng không thuộc hunk; parse toàn nội dung dự kiến và auto-validation khi áp dụng đều đạt. Nguyên nhân lỗi preview riêng này chưa xác định.
- Chưa commit. Các thay đổi có sẵn của Anh được giữ; PROJECT_MAP/PROJECT_CONTEXT là output phát sinh của pipeline ToolA.
- Không xóa fixture hay các probe từ báo cáo cũ.

## Tệp thay đổi chính

- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\code_tools.py`
- `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator\tests\test_external_write_reconcile_scope.py`
