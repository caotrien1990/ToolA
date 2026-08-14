# Patch 00: Clarification Gate
# Karpathy #1 "Think Before Coding" → HARD-GATE
# Gắn vào: rune-preflight / rune-cook entry / council leader pre-dispatch

<HARD-GATE>
Before ANY EXECUTE (T2 write, T3 audit-mutate, T4b browser, T4c restart, council dispatch):
STOP. Run this 3-point check:

1. **Assumption check**: Restate the task in your own words. If any assumption would change the action, STOP.
2. **Interpretation check**: If multiple valid interpretations exist, present them to user. Do NOT pick silently.
3. **Confusion check**: Name what is unclear. If confused about scope, target state, or constraints → ASK.

Violation: executing without passing all 3 checks = protocol violation.
</HARD-GATE>

## Áp dụng

- Khi nào: Trước mọi EXECUTE trong council workflow, cook workflow, hoặc standalone task
- Không áp dụng: T1 read-only, trivial (<5 LOC), hoặc user override "just do it"
- Output: Nếu pass → proceed. Nếu fail → ask user, chờ reply.

## Ví dụ

```
❌ BAD:  User said "fix login" → immediately write code
✅ GOOD: User said "fix login" →
  - Assumption: "fix" means bug, not feature
  - Interpreation: could mean UI bug OR backend auth
  - Confusion: scope unclear
  → ASK: "Anh muốn fix bug UI hay backend auth?"
```

## Tích hợp với rule hiện tại

- Bổ sung cho 7-RULE #1: "Ask > guess" → giờ là HARD-GATE, không phải advisory
- Bổ sung cho leader Worker Prompt Quality Gate: thêm "clarification check" vào step 1
- Bổ sung cho rune-preflight Stage A: thêm clarification check trước spec compliance
