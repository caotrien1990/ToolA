# Patch 02: Traceability Rule
# Karpathy #3 "Surgical Changes" → line-level traceability
# Gắn vào: rune-scope-guard / rune-preflight Stage A / council Worker Prompt Quality Gate

## Rule

Mỗi dòng code đổi phải trace về 1 trong 3 nguồn:
1. User request (explicit requirement)
2. Test requirement (test yêu cầu implementation)
3. Bug fix (error trace yêu cầu change)

Nếu dòng KHÔNG trace được → FLAG hoặc REVERT.

## Check

```
Traceability Check:
- Dòng mới: Trace về user request nào? → ghi trong commit message
- Dòng sửa: Trace về bug/test nào? → ghi trong commit message
- Dòng xóa: Trace về cleanup nào? → chỉ xóa nếu là orphan của THAY ĐỔI NÀY
```

## Ví dụ

```
❌ BAD: Thêm helper function utils/formatDate() vì "tương lai sẽ cần"
✅ GOOD: Thêm helper function utils/formatDate() vì user yêu cầu "show date in format DD/MM/YYYY"

❌ BAD: Refactor function handleAuth() vì "code cũ xấu"
✅ GOOD: Giữ nguyên function handleAuth() — KHÔNG refactor vì user KHÔNG yêu cầu
```

## Tích hợp với rule hiện tại

- Bổ sung cho rune-scope-guard: thêm line-level check (hiện tại chỉ check file-level drift)
- Bổ sung cho rune-preflight Stage A: thêm traceability column vào plan-vs-diff table
- Bổ sung cho council Worker Prompt Quality Gate: thêm "traceability requirement" vào acceptance criteria
