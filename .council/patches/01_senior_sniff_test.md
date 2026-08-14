# Patch 01: Senior Sniff Test
# Karpathy #2 "Simplicity First" → concrete heuristic
# Gắn vào: rune-preflight Step 4 / council leader pre-dispatch sanity check

## Trigger

Sau khi write code hoặc plan, trước khi提交/báo cáo:

```
Senior Sniff Test:
1. Nếu lines > 100: Có thể viết lại thành <50 dòng không?
2. Có abstraction nào chỉ dùng 1 lần không?
3. Có feature nào user KHÔNG yêu cầu nhưng mình thêm vào?
4. Có error handling cho scenario "impossible" không?
5. Nếu senior engineer review, họ sẽ nói "overcomplicated"?
```

Nếu 1 trong 5 câu trả lời CÓ → REWRITE trước khi提交.

## Ví dụ

```
❌ BAD: Viết 300 dòng cho function validate input (regex + helper + abstraction)
✅ GOOD: Viết 40 dòng regex directly solve problem

❌ BAD: Thêm caching layer cho 1 API endpoint nhỏ
✅ GOOD: Để nguyên, user KHÔNG yêu cầu caching
```

## Tích hợp với rule hiện tại

- Bổ sung cho 7-RULE #2 "Minimum footprint": thêm concrete heuristic check
- Bổ sung cho rune-preflight Step 4: thêm "simplicity check" sau completeness check
- Bổ sung cho council leader Worker Prompt Quality Gate: thêm "simplicity check" vào output contract
