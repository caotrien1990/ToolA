---
name: mode_control
description: |
  Use to determine whether CA is in DEFAULT (T1 read-only) or EXECUTE mode. Activate
  when User sends any prompt that might trigger mutating tools, when "tiến hành"
  appears, or when interpreting follow-up actions (post-write validation exception).
trigger:
  - task_pattern: "tiến hành | proceed | execute | do it | start | run"
  - task_pattern: "default | analyze | check | look at | read"
preconditions:
  - "Health OK"
  - "Super-core CORE_RULE.txt loaded (or in prompt)"
related_skills:
  - 00_skill_router
  - 03_path_session
  - 05_write_pipeline
risk_level: medium
---

# 02 — Mode Control

## Two modes (HARD)

### DEFAULT (always entry mode — for genuinely new tasks only)
- T1 read-only tools only
- No T2/T3 mutations, no write, no test, no deploy
- Browser/UI actions allowed only for T4a snapshot/inspect (read-only)
- End reply with `🔍 ANALYZE` or `❓ ASK`

### EXECUTE (gated — sticky once authorized)
- Requires User trigger: **`tiến hành`** OR **`tien hanh`** + clear non-infra action
- "tiến hành" alone is NOT enough — must have explicit action
- Use T1 freely; T2/T3 only for the triggered action; token fetch as needed
- T4b (browser click/fill), T4c server-restart, T4d desktop actions require EXECUTE
- Active progress ends with `⚙️ EXECUTE`
- Scope completed+verified → return to DEFAULT, end `✅ VERIFY`
- **Critical**: see "EXECUTE continuity" below — DEFAULT is NOT re-entered just because a turn ended

### EXECUTE continuity (sticky until terminal state)
- An exact `tiến hành <action/phase>` remains active across normal follow-up turns and context compaction until that named scope is completed+verified, blocked, explicitly aborted/paused/replaced by Anh, or a scope-changing ambiguity requires a question.
- Do **not** reset to DEFAULT merely because a turn ended, a tool returned, or a compaction handoff appeared.
- Compaction recovery must read the active authorization/scope from the handoff, then check the newest user message for stop/undo/new-topic signals. If none exists, resume execution.
- New unrelated mutations remain outside the sticky scope and require a fresh trigger.
- Detailed recovery checklist: `references/sticky-execute-compaction.md`.

## Trigger matrix

| Trigger pattern | Mode | Notes |
|-----------------|------|-------|
| "tiến hành <action>" | EXECUTE for the action | Scope completed+verified → DEFAULT |
| "tiến hành P1" / "tiến hành P2+P3" / "tiến hành all" | EXECUTE **only named phases** | Multi-phase CSLt plans: do not freestyle outside listed phase IDs |
| "chốt plan" | DEFAULT | Mark plan accepted; **no code** until tiến hành |
| "tiến hành" alone | ASK | What action / which phase? End ❓ |
| "phân tích / check / xem" | DEFAULT | T1 only |
| "fix bug X" (no tiến hành) | DEFAULT | Analyze + propose, no edit |
| "fix bug X tiến hành" | EXECUTE | Fix immediately |
| Follow-up after EXECUTE write batch | Auto-EXECUTE for post-write validate/cache_delta | One pass, then DEFAULT |

## Plan-phase scope (CSLt multi-step)

When a formal plan file exists (often `D:/BR/_CSLt_plan/CSLt_PLAN_*.md`) with
Phase IDs:

- `tiến hành P1` → only P1 deliverables + its verify gates.
- `tiến hành P1+P2` → those phases only, stop on first failed gate.
- Do **not** pull deferred non-goals (scoring, skill content rewrite, full-205 decorate)
  into the same EXECUTE unless Anh names them.
- After phase done → DEFAULT + report; next phase needs a new trigger (unless Anh said `all`).

## Post-write validation exception

After a successful EXECUTE write batch, CA may run these WITHOUT new trigger:
- `validate_file` on changed files
- `validate_symbols` if supported
- ONE `cache_delta` for the batch
- This is enforcement of CORE_RULE §MODE EXCEPTION, not bypass.

NOT covered by exception:
- New write_fix on different files
- Test runs beyond syntax validation
- Deploy / commit

## Anti-patterns

- ❌ Inferring EXECUTE from "fix this" (verb does NOT imply trigger)
- ❌ Dropping an unfinished, explicitly authorized action merely because a turn ended or context was compacted
- ❌ Saying "Herm sẽ chờ yêu cầu tiếp theo" when a compaction handoff records unfinished authorized scope
- ❌ Asking for "tiến hành" again when the exact go-ahead is already in the handoff and scope isn't terminal
- ❌ Treating compaction summary labels like "reference only" as cancellation of active authorization
- ❌ Pivoting to a different task (e.g. UI fix) when user says "fix đi" but the authorized core-rule scope is still unfinished
- ❌ Carrying EXECUTE beyond its named scope after completion, blocker, abort, or task replacement
- ❌ Using `apply_after_dry_run` without explicit user-confirmed `dry_run_id`
- ❌ Multiple EXECUTE actions chained from single trigger (only the action User named)
- ❌ Native fallback during DEFAULT mode (only allowed mid-EXECUTE per skill 12)
- ❌ Saying "chưa sửa vì chưa có tiến hành" when transcript already contains exact go-ahead for unfinished scope

## Workflow

```
1. Parse User message for trigger
2. If trigger absent:
   a. Check: is there an ACTIVE_EXECUTE_SCOPE from prior turns/compaction?
   b. If yes → continue EXECUTE (scope not terminal), DO NOT reset to DEFAULT
   c. If no → DEFAULT, proceed T1 only
3. If trigger present:
   a. Identify the named action (file/operation scope)
   b. Enter EXECUTE, use T2/T3 for that action
   c. Post-write: validate → cache_delta
   d. If all steps verified: return DEFAULT, end ✅ VERIFY
   e. If steps remain: stay EXECUTE, end ⚙️ EXECUTE
4. If trigger ambiguous → end ❓ asking to clarify scope
```

**Critical post-compaction step**: After any context compaction, BEFORE defaulting to DEFAULT:
1. Read handoff for exact go-ahead, authorized scope, completed steps, unfinished steps
2. Read newest user message for reverse/replace signals (dừng, stop, hủy, new conflicting task)
3. If handoff has exact go-ahead + unfinished scope + no reverse signal → restore ACTIVE_EXECUTE_SCOPE
4. If handoff says "waiting for next request" but scope is unfinished → that handoff was WRONG; restore anyway

## Guardrails

- Default mode at a genuinely new session/task; if a compaction handoff records an unfinished exact go-ahead, restore that scoped EXECUTE state unless the newest user message reverses it
- T2/T3 tools without active trigger → return 409 `EXECUTE_TRIGGER_REQUIRED` (server enforces)
- Never carry EXECUTE beyond the authorized action; do carry it across turns while that action is unfinished

## Operational Impact

- DEFAULT avoids burning tokens on dry_run previews when only analysis needed
- Single-trigger-single-action prevents accidental multi-write cascades
- Auto exception saves 1 extra trigger round-trip for post-write

## Harness

- Test 1: User msg "check button handler" → CA stays DEFAULT, returns analysis only
- Test 2: User msg "tiến hành fix line 50" → CA enters EXECUTE, edits, validates, returns DEFAULT
- Test 3: User msg "tiến hành" alone → CA asks ❓ what action
- Test 4: After EXECUTE write, attempt validate_file → allowed without new trigger; attempt second write → require new trigger
- Test 5: Compaction recovery — handoff says "tiến hành fix X authorized, write done, verify not done" → CA resumes verify without asking for trigger
- Test 6: Post-compaction follow-up "verify" → CA continues same EXECUTE scope
- Test 7: Post-compaction "đã fix chưa?" → CA reports status, stays EXECUTE if unfinished
- Test 8: Post-compaction "dừng" → ABORTED_BY_USER, reset DEFAULT
- Test 9: Post-compaction new conflicting task → replace scope, don't continue old
- Test 10: Scope expansion without trigger → blocked, ask for authorization
