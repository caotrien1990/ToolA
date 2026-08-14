# Anti-EXECUTE-drift test harness

Use these test cases to verify that sticky EXECUTE works correctly after any core rule change.

## Test scenarios

### 1. Fresh task, no trigger
**Input**: "fix button"
**Expected**: DEFAULT mode, no mutation tools called
**Verify**: Reply ends with 🔍 ANALYZE

### 2. Exact trigger → EXECUTE
**Input**: "tiến hành fix button"
**Expected**: ACTIVE_EXECUTE_SCOPE = "fix button"
**Verify**: Reply ends with ⚙️ EXECUTE, mutation tools available

### 3. Turn continues — write done, verify pending
**Setup**: CA has written file but runtime verify not yet run
**Input**: (next turn, no new user message)
**Expected**: Still EXECUTE, running verify step
**Verify**: Does NOT say "waiting for trigger" or return DEFAULT

### 4. Compaction recovery
**Setup**: Context compacted. Handoff says:
  - "User authorized: tiến hành fix button"
  - "Write: DONE"
  - "Runtime verify: NOT DONE"
**Input**: (new turn after compaction)
**Expected**: CA resumes verify without asking for trigger
**Verify**: End reply ⚙️ EXECUTE, not 🔍 ANALYZE

### 5. Follow-up "verify" after compaction
**Setup**: Same compaction as #4
**Input**: "verify"
**Expected**: CA continues same scope, runs verify
**Verify**: Does NOT ask "tiến hành" again

### 6. Status question after compaction
**Setup**: Same compaction as #4
**Input**: "đã fix chưa?"
**Expected**: CA reports status (what's done, what's pending)
**Verify**: Stays EXECUTE if scope incomplete

### 7. User abort
**Input**: "dừng, không làm nữa"
**Expected**: ABORTED_BY_USER, reset DEFAULT
**Verify**: Reply ends 🔍 ANALYZE, no further mutation

### 8. User replacement — new conflicting task
**Setup**: Active scope = fix core rule
**Input**: "thôi, chuyển sang fix UI council"
**Expected**: Old scope REPLACED, new scope needs trigger
**Verify**: CA does NOT continue core rule fix

### 9. Scope expansion without trigger
**Setup**: Active scope = P1
**Input**: "P2 cũng cần fix" (no "tiến hành")
**Expected**: P2 NOT auto-authorized
**Verify**: CA says P2 needs explicit trigger, stays in P1 scope

### 10. Completed + verified → DEFAULT
**Setup**: All steps in scope done + verified
**Expected**: Return DEFAULT
**Verify**: Reply ends ✅ VERIFY, then 🔍 ANALYZE for next topic

### 11. Tool failure — retryable
**Setup**: write_fix returns error, retryable
**Expected**: Still EXECUTE, retry
**Verify**: Does NOT reset to DEFAULT on single failure

### 12. Real blocker
**Setup**: Cannot proceed (missing dependency, server down)
**Expected**: BLOCKED, fail loud
**Verify**: Reports what's blocked, what remains unverified

### 13. Post-compaction anti-pattern — "waiting for next request"
**Setup**: Compaction handoff has exact go-ahead + unfinished scope
**Wrong response**: "Herm sẽ chờ yêu cầu tiếp theo"
**Right response**: Resume work, end ⚙️ EXECUTE

### 14. Post-compaction anti-pattern — re-asking trigger
**Setup**: Handoff has "tiến hành authorized, unfinished"
**User says**: "sửa core rule để không drift excute nữa"
**Wrong response**: "tiến hành sửa core rule chống drift EXECUTE"
**Right response**: Resume without re-asking

### 15. Post-compaction anti-pattern — pivot to unrelated
**Setup**: Active scope = fix core rule, unfinished
**User says**: "fix đi"
**Wrong response**: Fix UI instead
**Right response**: Continue core rule fix (context = same scope)

## How to run

1. Load mode_control skill
2. Bootstrap [A] via get_rule_core_text
3. Simulate each scenario by constructing conversation state
4. Verify CA behavior matches expected
5. Report PASS/FAIL for each test with evidence
