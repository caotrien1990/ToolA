# COMC1 ROUTE + UI BREAKDOWN SCAN

> OBSOLETE ROUTING NOTE (2026-06-25): this scan artifact is not a routing source of truth.
> Current research route is HermC1=OpeC4 by default; ComC2/HermC2 are removed.
> Do not use old model-slot examples from this file as leader routing.
**Date:** 2026-06-24 | **Worker:** ComC1 (DeepSeek V4 Pro)
**Scope:** VERIFY/SCAN ONLY — no edits, no source mutation.

---

## 1. CURRENT STATE SUMMARY

### 1a. Key Files Read

| File | Role |
|------|------|
| `.council/council_config.json` | SINGLE SOURCE OF TRUTH: agents, slots, task_routing, roles |
| `.council/council_rule_leader.md` (94 lines) | Council leader coordination rule v1.0 |
| `.ToolA_Skill/skills/leader-council-skill/SKILL.md` (109 lines) | Leader anti-drift skill with dispatch/routing/protocol |
| `.council/worker_models.json` | Dispatch-time provider/model overrides (live-editable) |
| `orchestrator_server.py` (~8520 lines) | Backend: dispatch lock, council_message API, runtime state, ID_MODELS validation |
| `orchestrator/council/dashboard.py` (~988 lines) | Frontend: all UI (agent cards, model dropdown, broker textarea) |
| `.council/ID_MODELS/*.txt` | Curated model lists per provider |

### 1b. Current Dispatch Lock Architecture

`_council_enqueue_or_run()` (`orchestrator_server.py:2553`) checks **per-agent** lock:

`busy = (bool(agent_queue) OR _COUNCIL_AGENT_QUEUE_DRAINING.get(target) OR (current.state == "running" AND NOT stale))`

There is **no model_slot dimension** in the lock. Two dispatches to agent="ope" with different models (OpeC 1 flash vs OpeC 2 slow) are blocked from running in parallel.

### 1c. Current UI — Broker Textarea

`dashboard.py` exposes:
- `<select id="broker-to">` — **agent-level** selector: clau, codo, comc, ope, hermes, clin
- `<textarea id="broker-text">` — message composer
- Per-agent `<select class="worker-model-dropdown">` — sets model via `POST /api/council/worker-config`
- No **slot display** — user selects `ope` and must know to set model first

### 1d. Current Model Slot Definition (council_config.json)

```
ClauC   = judgement/scoped bug-fix (expensive)
CodoC   = code builder/plan drafter
ComC 1  = DeepSeek V4 Pro → edge/bug/breakdown/low-risk fix
ComC 2  = REMOVED         → do not route
ComC 3  = Mimo V2.5 Pro   → brainstorm/tool/test verify
OpeC 1  = DeepSeek V4 Flash → flash scan/internet [antibot]
OpeC 2  = Nemotron 3 Ultra  → scan+reason, short response only
OpeC 4  = Mimo V2.5 Free    → default research/scan/report peer
HermC 1 = DeepSeek V4 Flash → default research/scan/report
HermC 2 = REMOVED           → do not route
```

---

## 2. Ope MULTI-MODEL SLOTS: BACKEND CHANGE ANALYSIS

### 2a. Can OpeC model slots be represented with current API?

**YES — but ONLY as sequential (not parallel) dispatches.**

The current `council_worker_model` API supports `action=set` + `agent=ope` + `provider/model`. This writes to `worker_models.json`. The next dispatch to `agent=ope` picks up the new model. This works for:

1. Leader dispatches OpeC 1 (flash scan) → waits for completion
2. Leader calls `council_worker_model(action="set", agent="ope", model="opencode/nemotron-3-ultra-free")` only for OpeC2 scan+reason
3. Leader asks OpeC2 for short in-response output only, not long write/artifact

**Limitations (no backend change needed for current behavior):**
- **No parallel OpeC 1 + OpeC 2** — the per-agent dispatch lock blocks this
- **No queue separation by slot** — if OpeC 1 is running and OpeC 2 is queued, the queue is serialized

### 2b. What backend changes WOULD be needed for parallel model slots?

If Anh wants OpeC 1 AND OpeC 2 to run **in parallel** (which contradicts current architecture), backend changes required:

1. **`_council_enqueue_or_run()`** — change lock from `per-agent` to `per-agent+model_slot` key
2. **`_COUNCIL_AGENT_RUNTIME`** — change keying from `{agent}` to `{agent}:{model_slot}` 
3. **`_council_agent_command()`** — accept model_slot parameter, inject slot-specific model
4. **`worker_models.json`** — extend to support `{agent: {slot_N: {provider, model}}}` structure
5. **`council_worker_model` API** — add optional `slot` field (currently forbidden!)
6. **`council_message` API** — add `model_slot` to dispatch payload
7. **UI** — slot selector in broker, slot-aware runtime status display

**Risk assessment:** This is a SIGNIFICANT refactor. The current design explicitly chose per-agent lock for simplicity. Parallel model slots for the same agent would double subprocess footprint, complicate state tracking, and break the "one worker at a time" mental model.

### 2c. Recommendation

**OpeC model slots should remain SEQUENTIAL model switches under one agent**, not parallel workers. The leader skill and rule must EXPLICITLY state this (currently it's only stated for ComC). The UI slot dropdown would then show OpeC 1 / OpeC 2 as quick-model-switch presets, not as parallel run targets.

This avoids all backend changes to dispatch lock, runtime state, and API schema.

---

## 3. PROPOSED IMPLEMENTATION PHASES

### Phase 1: Rule + Skill Updates (VERIFY/SCAN → then edit)

**Files to CHANGE:**

| File | Change Description |
|------|-------------------|
| `.council/council_config.json` | Current route: HermC1=OpeC4 default research/scan/report pair; ComC2/HermC2 removed; OpeC slots are model switches, not parallel workers unless runtime is slot-keyed. |
| `.council/council_rule_leader.md` | Same HermC2 + OpeC sequential clarification. Add ClauC master dev role paragraph. |
| `.ToolA_Skill/skills/leader-council-skill/SKILL.md` | Update `council_worker_model` section: explicitly state OpeC 1/2/HermC 1/2 are sequential model switches under one agent, not parallel workers. Add ClauC master dev role. |

**Files needing VERIFICATION ONLY (no edit):**

| File | Reason |
|------|--------|
| `.council/ID_MODELS/OPENCODE.txt` | Verify current curated models match OpeC 1/2/3/4 definitions; do not add MiniMax M3. |
| `.council/ID_MODELS/HERMES.txt` | Verify HermC1 model only; HermC2 remains removed unless Anh explicitly restores it. |
| `orchestrator_server.py` | Backend reads ID_MODELS files — verify model IDs aren't stale. No dispatch-lock changes needed. |

**Risk gates for Phase 1:**
- GATE 1: After editing council_config.json, run `council_config_sync` to propagate (or ensure convene_council reads live)
- GATE 2: After editing leader_skill, run test-matrix to verify dispatch routing still works
- GATE 3: After all edits, leader must broker-test: OpeC 1 flash scan → switch model → OpeC 2 slow scan, verify sequential dispatch works

---

### Phase 2: UI Dropdown — Slot Selector in Broker Textarea

**Goal:** Replace `<select id="broker-to">` (agent-level) with a slot-aware dropdown showing human-readable slot names.

**Proposed UI:**

`<select id="broker-slot">`:
- ClauC (Judgement)
- CodoC (Code builder)
- OpeC 1 — Flash Scan (DeepSeek V4 Flash)
- OpeC 2 — Scan+Reason (short response)
- HermC 1 — Flash Scan (DeepSeek V4 Flash)
- ComC 1 — Edge/Bug (DeepSeek V4 Pro)
- ComC 2 — REMOVED
- ComC 3 — Brainstorm/Test (Mimo V2.5 Pro)

**On selection:**
1. Set `agent` from the slot mapping
2. Call `POST /api/council/worker-config {agent, provider, model}` for the slot's model
3. Enable broker send (if agent not running)

**Backend change — Add ONE new API endpoint:**
- `GET /api/council/slots` → returns `[{slot_id, agent, label, provider, model, description}]`
- Source data from `council_config.json` `task_routing` + `role` fields
- No dispatch-lock changes needed

**Frontend change — `dashboard.py`:**
- Replace `#broker-to` static `<select>` with dynamic slot selector
- Parse slot list from `GET /api/council/slots` at page load
- On slot change: call worker-config to set model + enable send
- Keep per-agent model dropdown for power users

**Files to CHANGE:**

| File | Change |
|------|--------|
| `orchestrator_server.py` | Add `GET /api/council/slots` endpoint + `_council_slot_definitions()` helper |
| `orchestrator/council/dashboard.py` | Replace `#broker-to` select with slot-dropdown; on slot-change call worker-config + update `#broker-to` value internally; update `setupBroker()` form submit to resolve slot→agent |
| `.council/council_config.json` | Ensure `role` fields have parseable slot info; add `slots` list if needed |

**Risk gates for Phase 2:**
- GATE 1: No dispatch-lock changes — verify existing lock still works after slot→agent resolution
- GATE 2: Backward compatible — old direct `#broker-to` usage still works (power users bypass slot selector)
- GATE 3: On slot change, wait for worker-config POST response before enabling send button (race condition)
- GATE 4: Model dropdown + provider inputs on agent card must still work independently

---

### Phase 3: Tests (for both phases)

**Backend tests:**
- `GET /api/council/slots` returns correct slot definitions matching `council_config.json`
- OpeC 1 → OpeC 2 sequential dispatch (model switch + second dispatch)
- `worker_models.json` after slot-based worker-config POST

**Frontend tests:**
- Slot selector renders correct options
- On slot change, `#broker-to` value updates correctly
- Dispatch with slot selection sends to correct agent with correct model
- Agent-card model dropdown still works when used directly

---

## 4. CONTRADICTIONS / DRIFT RISKS

### C1: OpeC sequential vs implicit parallel wording

**Location:** `council_rule_leader.md` "Council Coordination" section
**Problem:** "OpeC 1 = flash scanner... OpeC 2 = slow scanner" — listed as if two dispatchable workers, but dispatch lock treats "ope" as ONE agent.
**Fix:** EXPLICITLY state "OpeC model slots are sequential model switches under one ope agent. Switch OpeC's model and dispatch one task at a time."

### C2: HermC2 still referenced after removal

**Location:** `council_config.json` `agents.hermes.role` + `task_routing` + `council_rule_leader.md`
**Problem:** old notes referenced a removed HermC2 route. Anh confirmed HermC2 removed from default route.
**Drift risk:** A leader reading the rule will try to use HermC 2 and get unexpected failures.
**Fix:** Update all live routing files to say "HermC 2 = REMOVED" and use HermC1=OpeC4 as research default.

### C3: `council_worker_model` "slot is forbidden" vs UI slot concept

**Location:** `SKILL.md` "council_worker_model Payload Guardrail"
**Problem:** Skill says `slot` is "forbidden" in the API. But if UI shows slot-dropdown, the concept becomes visible.
**Fix:** Clarify: "slot is forbidden in the council_worker_model API because model slots are represented by changing the model on the same agent. The UI shows slot labels as human-readable routing hints, but the API only accepts agent+provider+model."

### C4: ClauC master dev role not documented

**Location:** Missing everywhere
**Problem:** Anh confirmed "ClauC adds master dev role for issues where council retry >2; fallback CodoC if limit." Not in council_config.json, council_rule_leader.md, or SKILL.md.
**Fix:** Add to all three files.

---

## 5. EXACT FILE CHANGE LIST

### Must change (rule + skill):
| # | File | Phase |
|---|------|-------|
| 1 | `.council/council_config.json` | P1 |
| 2 | `.council/council_rule_leader.md` | P1 |
| 3 | `.ToolA_Skill/skills/leader-council-skill/SKILL.md` | P1 |
| 4 | `.ToolA_Skill/skills/leader-council-skill/references/dispatch-template.md` | P1 (if it references old slots) |

### Must change (UI + backend):
| # | File | Phase |
|---|------|-------|
| 5 | `orchestrator_server.py` — add `GET /api/council/slots` endpoint | P2 |
| 6 | `orchestrator/council/dashboard.py` — slot selector in broker | P2 |

### Must verify (no edit needed unless stale):
| # | File | Reason |
|---|------|--------|
| 7 | `.council/ID_MODELS/OPENCODE.txt` | Verify OpeC 1/2 models present |
| 8 | `.council/ID_MODELS/HERMES.txt` | Verify HermC1 model only for default route |
| 9 | `.council/ID_MODELS/COMMANDCODE.txt` | Verify ComC1/ComC3 models present |
| 10 | `.ToolA_Skill/skills/leader-council-skill/references/test-matrix.md` | Update tests if procedure changes |
| 11 | `.ToolA_Skill/skills/leader-council-skill/references/q-protocol.md` | No change unless Q routing changes |

---

## 6. RECOMMENDATION SUMMARY

| Decision | Verdict | Rationale |
|----------|---------|-----------|
| Ope multi-slot parallel? | **NO — keep sequential** | Per-agent dispatch lock + subprocess model; parallel would require major backend refactor |
| Backend changes for slot UI? | **MINIMAL — add 1 endpoint** | `GET /api/council/slots` reads from council_config.json; no dispatch-lock changes |
| HermC2 removal from rule? | **YES — 3 files** | council_config.json, council_rule_leader.md, SKILL.md |
| ClauC master dev role? | **YES — add to 3 files** | Same three files; document retry >2 gate and CodoC fallback |
| UI slot dropdown? | **YES — P2 priority** | Replace agent-level broker select with slot-aware dropdown |
| First phase? | **P1: Rule + Skill edits** | Lowest risk, highest clarity gain; unblocks UI decisions |

**Implementation order:** P1 (rule/skill clarity) → P2 (UI dropdown) → P3 (tests)

---

## APPENDIX: KEY CODE REFERENCES

### Dispatch lock (orchestrator_server.py:2553-2562)
```
busy = (bool(agent_queue) OR _COUNCIL_AGENT_QUEUE_DRAINING.get(target)
        OR (current.state == "running" AND NOT stale))
```

### Slot definition source (council_config.json)
`task_routing` maps task_type → agent + role + implicit model.
`agents[agent].role` describes model slots inline.

### UI broker-to selector (dashboard.py)
`f'<option value="{esc(a)}" ...>{esc(label)}</option>'` — agent-level only, no slot awareness.

### council_worker_model validation (orchestrator_server.py:6296-6415)
- Validates against `.council/ID_MODELS/<PROVIDER>.txt`
- Writes `.council/worker_models.json`
- Applied at next dispatch (no restart)

### ID_MODELS current contents
- CLAUDE.txt: `claude-opus-4-8`, `claude-sonet-4-6`, `claude-haiku-4-5`
- COMMANDCODE.txt: `deepseek/deepseek-v4-pro`, `xiaomi/mimo-v2.5-pro`
- GPT.txt: `gpt-5.5`, `gpt-5.4`
- HERMES.txt: `deepseek-v4-flash-free`
- OPENCODE.txt: `opencode/deepseek-v4-flash-free`, `opencode/nemotron-3-ultra-free`, `opencode/north-mini-code-free`, `opencode/mimo-v2.5-free`
