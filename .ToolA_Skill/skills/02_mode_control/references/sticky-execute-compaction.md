# Sticky EXECUTE across turns and compaction

Use this when a mutating task has already received an exact `tiến hành <scope>` authorization.

## Durable rule

Authorization remains active for the named task or phase until one terminal condition occurs:

- requested scope is completed and verified;
- execution fails with a blocker that cannot be resolved in-scope;
- Anh explicitly aborts, pauses, narrows, reverses, or replaces the task;
- the agent reaches a genuine ambiguity that changes mutation scope and must ask.

A normal follow-up turn, tool result, context compaction, or handoff does **not** reset EXECUTE by itself. Compaction may shrink context, but it must preserve the active scope and authorization state.

## Post-compaction decision tree

```
1. READ handoff
   → Does it contain exact go-ahead? (e.g. "User authorized 'tiến hành fix X'")
   → Does it list unfinished steps?
   
2. READ newest user message
   → Is it a REVERSE signal? (dừng, stop, hủy, undo, không làm nữa)
   → Is it a REPLACE signal? (new task that conflicts with authorized scope)
   → Is it a CONTINUATION signal? (verify, tiếp tục, đã fix chưa?, fix đi)
   → Is it UNRELATED? (new topic, read-only question)

3. DECIDE
   If: exact go-ahead + unfinished scope + no reverse/replace
   Then: RESTORE ACTIVE_EXECUTE_SCOPE, continue work, end ⚙️ EXECUTE
   
   If: exact go-ahead + unfinished scope + continuation signal
   Then: RESTORE ACTIVE_EXECUTE_SCOPE, continue work
   
   If: reverse/replace signal
   Then: ABORTED/REPLACED, reset DEFAULT
   
   If: handoff has NO exact go-ahead (only plans/proposals)
   Then: DEFAULT (user never authorized execution)
   
   If: unrelated new topic
   Then: DEFAULT for new topic; old scope becomes BLOCKED (stale, not cancelled)
```

## Failure examples from real sessions

### Failure 1: "waiting for next request" when scope is unfinished
**Handoff says**: "User authorized 'tiến hành fix core rule'. Audit started. Patch/sync not done."
**CA responds**: "Herm sẽ chờ yêu cầu tiếp theo, không tự tiếp tục các hạng mục trong bản tóm tắt."
**WRONG**: The handoff has exact go-ahead + unfinished scope. CA should resume.

### Failure 2: Re-asking for trigger after compaction
**Handoff says**: "tiến hành authorized. Write done. Verify not done."
**User says**: "sửa core rule để không drift excute nữa"
**CA responds**: "tiến hành sửa core rule chống drift EXECUTE" (asks for literal again)
**WRONG**: User's message is a continuation signal. The original authorization is still valid.

### Failure 3: Pivoting to unrelated task
**Handoff says**: "tiến hành fix core rule authorized, in progress."
**User says**: "fix đi."
**CA pivots**: Starts fixing Council UI instead.
**WRONG**: "fix đi" in context of an unfinished authorized scope means "continue fixing", not "start something new".

### Failure 4: Treating "reference only" as cancellation
**Handoff labels**: "[CONTEXT COMPACTION — REFERENCE ONLY]"
**Handoff content**: "User confirmed EXECUTE. Steps unfinished."
**CA treats as cancelled**: Defaults to DEFAULT.
**WRONG**: The "reference only" label applies to the compaction format, not to the authorization state. The content saying "User confirmed EXECUTE" is authoritative.

## What the newest user message can and cannot do

| Message type | Example | Effect on EXECUTE |
|---|---|---|
| Reverse | "dừng", "stop", "hủy", "undo" | ABORTED → DEFAULT |
| Replace | New task that conflicts with authorized scope | REPLACED → new scope |
| Continuation | "verify", "tiếp tục", "đã fix chưa?", "fix đi", "xong chưa?" | CONTINUE same scope |
| Expansion | "P1 xong rồi, tiến hành P2" | P2 needs explicit trigger if not pre-authorized |
| Unrelated | "check the logs", "what time is it?" | DEFAULT for this msg; old scope stays pending |
| Status question | "trạng thái?" | Answer question, stay EXECUTE if unfinished |

## Reporting

- While work remains: report EXECUTE progress, not DEFAULT.
- At terminal completion: report changed artifacts and real verification evidence.
- On blocker: fail loudly and state exactly what remains unverified.

## Strings that signal stale/conflicting rule wording

If any canonical file or generated rule contains these strings, investigate for conflict with sticky EXECUTE:

- `EXECUTE_TRIGGER_REQUIRED` — server enforcement; check if it resets mode state
- `return to DEFAULT` — check if it fires on turn end vs scope completion
- `Never carry EXECUTE` — check if it means "beyond named scope" or "across turns"
- `Done → DEFAULT` — check if "Done" means "scope verified" or "one tool call finished"
- `always entry mode` — check if it means "initial mode" or "every turn starts DEFAULT"
- `single-trigger-single-action` — check if "action" means "one tool call" or "one named scope"
