# Patch 03: Goal Template
# Karpathy #4 "Goal-Driven Execution" → mandatory step→verify template
# Gắn vào: rune-plan / workflow_start / council Worker Prompt Quality Gate

## Template (bắt buộc cho multi-step tasks)

```
1. [Step name] → verify: [binary check]
2. [Step name] → verify: [binary check]
3. [Step name] → verify: [binary check]
```

Mỗi step PHẢI có verify criterion rõ ràng (pass/fail, không ambiguous).

## Ví dụ

```
✅ GOOD:
1. Read current login code → verify: file exists and function identified
2. Write failing test for login bug → verify: test FAILS with current code
3. Fix login code → verify: test PASSES

❌ BAD:
1. Read code
2. Fix bug
3. Test
```

## Áp dụng

- Khi nào: Mọi task > 2 steps hoặc council dispatch
- Không áp dụng: Single-step tasks, trivial changes
- Output: Template trong worker prompt (council) hoặc TodoWrite (standalone)

## Tích hợp với rule hiện tại

- Bổ sung cho 7-RULE #3 "Goal loop": thêm mandatory template
- Bổ sung cho council Worker Prompt Quality Gate: thêm template vào step 4 (target state)
- Bổ sung cho rune-plan: thêm template requirement
