---
name: session_audit
description: |
  Use for session lifecycle (open, close, rename), audit trail (events, native_log,
  pending audits), and multi-agent handoff (prepare_ca_session for [CA2]). T3 has been
  underused historically — V15 R4 validates viability via 2-agent simulation test.
trigger:
  - task_pattern: "session | audit | handoff | multi-agent | close work | summary report"
  - tool: "open_session | close_session | log_native_event | prepare_ca_session | get_session_events | session_audit_summary | get_usage_stats"
preconditions:
  - "[A] health OK"
  - "For close: an open session must exist for project_path"
related_skills:
  - 12_native_audit
  - 03_path_session
risk_level: low-medium
---

# 17 — Session / Audit / Auxiliary (T3)

## 🧭 V15 R18 — declare_mission @ session opener

Sau `open_session`, gọi `declare_mission` để [A] lọc skill + tool relevant. CA đọc 3-5 skills thay vì 23 (~5-18K tokens tiết kiệm/session).

```jsonc
open_session({project_path, session_title: "..."})
declare_mission({
  mission: "test Electron Rawcut UI: launch, fill manual input, click Step 1, watch Step 3, extract log",
  context_hints: ["electron","ui","log","cdp"],
  project_path: "<root>",
  top_skills: 5
})
// → relevant_skills: [{name:"18_runtime_dom_T4",score:0.92}, {name:"22_electron_automation",score:0.88}, ...]
// → recommended_sequence: ["1. read_skill(name='18')", "2. ensure_single_electron_instance(...)", ...]
// → irrelevant_skills_skip: [...18 skills CA không cần đọc...]
// → tokens_saved_estimate: ~18000
```

State pin to active session; reset khi `close_session`. Cross-session = fresh mission.

## 🔇 V15 R18 — Per-call verbosity protocol

Mọi tool có response verbose accept `verbosity ∈ {full, terse, ref}`:
- Round 1: `verbosity="full"` (default) — đọc educational blocks
- Round 2+: `verbosity="terse"` — strip educational, keep cheat-sheet ref
- Re-fetch: `verbosity="full"` bất cứ lúc nào để refresh

Drift detection trên response verbose tự bump `full` khi CA pattern bất thường → forget risk ~0%.

## 📦 V15 R17 — Artifact reference > full paste (S/L optimization)

**Default rule for final reports**: cite ARTIFACT REFERENCE, do NOT paste full multi-KB
content into chat reply, **UNLESS User explicitly requests** "paste full" / "show me
the whole thing" / "nguyên văn" in this turn's prompt.

### Why
8 KB log paste = ~1500 tokens; artifact ref (`path + sha256 + length + 200-char preview`)
= ~150 tokens → **~90% saving on log token sink** with zero correctness loss (checksum
guarantees no hallucination; User can re-fetch full from path if needed).

### How — use `save_log_snapshot`

```jsonc
save_log_snapshot({
  page_id, section_title: "System Error Log",
  project_path: "<root>", round_id: "<r-... from the current round marker>"
})
// → {path: ".orchestrator_cache/artifacts/r-.../System_Error_Log.txt",
//    sha256: "...", length: 8247, lines: 142,
//    head_200: "[2026-05-18 ...] INIT...", tail_200: "...COMPLETE"}
```

### Final report shape (artifact mode)

```markdown
**Log artifact**: `.orchestrator_cache/artifacts/r-1779.../System_Error_Log.txt`
- sha256: `a1b2c3d4...` | 8247 chars / 142 lines
- head: "[2026-05-18 00:48] INIT manualInput=D:\\..."
- tail: "...COMPLETE pid=12345 exit=0"
```

vs old full-paste shape (8 KB inline). Same evidence, ~90% fewer tokens.

### Exception: explicit User instruction

When prompt contains "Paste nguyên văn" / "full log" / "show me everything" / similar —
paste full. Document the override in the final report note: `"User-mandated full paste"`.

### Artifact location

`<project>/.orchestrator_cache/artifacts/<round_id>/<slug>.txt` — per-project, auto-managed
under existing cache dir. Audit logged. Safe to delete; not committed to repo.



## When to use

| Situation | Tool | Notes |
|-----------|------|-------|
| Start project work | `open_session(session_title, project_path)` | Required before any T2 mutation |
| Resume existing | `open_session` returns `resumed=true` if active session matches | No need to track session_id manually |
| Wrap up work | `get_session_events → close_session` | Always summarize before close |
| Handoff to next agent | `prepare_ca_session(project_path, mode, idea)` | Creates bootstrap prompt for [CA2] |
| Native fallback used | `log_native_event` immediately after | Mandatory; never batch |
| API down during native | `create_pending_audit` → `sync_pending_audit` later | Resilience |

## V15 R4 viability decision

**Question**: Is T3 worth maintaining as a tier, or fold into T2?

**Evidence so far**:
- ✅ `log_native_event` actively used in V15 session (R1 native edits logged)
- ✅ `open_session` used (auto on first API call)
- ✅ `close_session` works after R1 Q5 body fix (middleware guard still has bug, defer)
- ⏸ `prepare_ca_session` UNUSED — no real handoff yet
- ⏸ `rename_session` UNUSED
- ⏸ `create_pending_audit / sync_pending_audit` UNUSED

**Verdict**: KEEP T3 tier. Reason: audit trail (`log_native_event`, `get_session_events`) is critical for correctness via governance. Even if handoff tools sit unused for small projects, they're needed for multi-agent V15 architecture.

## 2-Agent simulation test plan (R4 Sprint)

```
SPRINT R4.1 — 2-agent handoff simulation
  Phase R4.1.1 [CA1]
    Step a: open_session(project_path=[Ab:P], title="Phase 1 audit")
    Step b: Run search_code on a sample query, log results
    Step c: log_native_event for any native call
    Step d: prepare_ca_session(project_path, mode="T1", idea="continue audit from [CA1]; investigate symbol X")
    Step e: STOP, return bootstrap prompt to operator
    Guardrail: session NOT closed; remains active for [CA2]
  Phase R4.1.2 [CA2]
    Step a: Reads bootstrap prompt from [CA1]
    Step b: open_session same project_path → expect resumed=true
    Step c: get_session_events → review [CA1] actions
    Step d: Continue: find_symbol X, propose write_fix (don't apply)
    Step e: session_audit_summary
    Step f: close_session
    Guardrail: events table has entries from BOTH [CA1] and [CA2] under same session_id
  Harness Sprint R4.1:
    - sql query: SELECT COUNT(DISTINCT operator) FROM events WHERE session_id=X
    - Expected: 2 distinct operators
    - native_fallback_count consolidated
    - prepare_ca_session generated valid prompt with [CA1] context

SPRINT R4.2 — Build session_audit_summary (new T3 tool)
  Phase R4.2.1: Spec
    Input: session_id OR project_path
    Output: { tools_used, mutations, validations, errors, native_fallback_count, pending_audit_count, open_risks, next_recommended_action }
  Phase R4.2.2: Implement
    Aggregates from events + pending_audits + sessions tables
  Phase R4.2.3: Integrate with close_session
    Auto-attach to close_session response
  Harness Sprint R4.2:
    - Open session, do 5 actions, close → summary count matches
    - Compare with manual get_session_events count

SPRINT R4.3 — Fix Q5 close_session middleware
  Phase R4.3.1: Identify protected-tool middleware code path
  Phase R4.3.2: Apply same _resolve_project_path resolution as body fix
  Phase R4.3.3: Add fallback to raw key
  Harness: open with subdir, close with sibling → resolves to same root
```

## Common patterns

### Pattern: Start + finish audit cycle
```
1. open_session(title, project_path)
2. <work>
3. get_session_events(session_id) → review
4. close_session(project_path) → summary returned
```

### Pattern: Handoff
```
[CA1]:
  prepare_ca_session(project_path, mode="T1", idea="<context for CA2>")
  → returns bootstrap_prompt
  → User pastes bootstrap_prompt as system message for [CA2]

[CA2]:
  Reads bootstrap_prompt (includes [Ab:P], mode, idea)
  open_session same path → resumed=true
  get_session_events → see [CA1] history
  Continue work
```

### Pattern: Native + sync
```
1. <API call failed after corrected retry>
2. Native action
3. log_native_event(action_type, tool_name, target, reason, instead_of, failed_error)
4. Continue
5. Before close_session: confirm pending_audit_count == 0 (else sync_pending_audit first)
```

## Guardrails

- `close_session` requires active session for project_path (after R1 Q5 fix: also tries resolved root)
- `log_native_event` must come BEFORE next [A] API call (don't batch native then log later)
- `prepare_ca_session` is HANDOFF bootstrap, not work tool — never use for own session
- Never log raw token, raw file content, or PII in `reason` field (300-char sanitize)
- Session_id is opaque — pass project_path, let server resolve

## Anti-patterns

- ❌ open_session inside open_session (auto-resume, don't double-call)
- ❌ Close session before logging final native event
- ❌ Use prepare_ca_session for current agent's own bootstrap
- ❌ Manual session_id management (server keys by project_path)

## Token-saving

- `get_session_events(limit=20)` instead of fetching all
- `session_audit_summary` once at end vs many `get_session_events`
- Sanitize `reason` to <200 chars when logging

## CA A-usage acceptance scoring

When scoring whether a CA used `[A]` correctly (see `.council/CA_A_USAGE_ACCEPTANCE_TEST.md`):

1. Prefer `session_audit_summary` + events over the CA’s self-report.
2. Record counters: `via_a_count`, `forbidden_native_count`, `allowed_native_count`, `unclassified_count`, `bypass_rate`.
3. `native_fallback_count` alone is insufficient — classify against policy matrix rows.
4. Negative control: intentional forbidden native must show up in audit; if not, harness fails.
5. Transcript claims without HTTP/session/ledger evidence → not PASS.

Related: skill `21_tool_matrix`. The policy matrix itself is `.council/CA_TOOL_POLICY_MATRIX.json` (echoed live by `open_session.cslt_policy`), not a markdown file.

## Harness verification

After R4 sprints implementation:
- [ ] 2-agent simulation passes (events from 2 operators in same session)
- [ ] session_audit_summary returns within 100ms for sessions <500 events
- [ ] close_session works with subdir / variant paths (Q5 middleware fix)
- [ ] pending_audit lifecycle: create → API down → sync → events table consistent
