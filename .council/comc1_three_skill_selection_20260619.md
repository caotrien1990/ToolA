# ComC1 — Three-Skill Starter Selection (R1)

**Worker**: ComC1 (deepseek-v4-pro)
**Date**: 2026-06-19
**Canonical Root**: `.ToolA_Skill/skills/`
**Source Digest**: `opec_skill_digest_for_comc1_20260619.md`

---

## Prompt Case Simulation

### Case 1: First session bootstrap
CA starts with zero context. First prompt arrives.

Without router: CA doesn't know `bootstrap_session()` exists, wastes 5-10 schema queries discovering tools one by one, likely misses active workflow and common pitfalls. Without tool_tiers: CA tries native tools (wrong) or T2 write (premature) on first read-only task.

**With 00 (router)** → `bootstrap_session()` call returns health + tool surface + skills index + pitfalls in 1 compact response (~5K saved). **With 04 (tiers)** → CA knows T1 read is zero-token and safe, avoids premature auth/T2 waste. **07 (error)** → idle on first bootstrap (no errors yet), but catches bootstrap hiccups (e.g., field alias errors on first API call).

### Case 2: Tool choice before action
CA has a task description and must pick tool/tier.

Without router: CA scans `/api/tools/names` + guesses, wastes 3-5 round-trips on "which tool" uncertainty. Without tiers: CA hits the right tool but wrong tier (e.g., T2 for a read task → 401 token wasted).

**00 + 04** complement perfectly: router gives the decision tree (task→tool), tiers gives the protection map (which tools need token + session). Together they resolve tool choice in 1 round-trip that would cost 3-5 otherwise. **07** still idle — no errors yet.

### Case 3: Error response from [A]
CA calls API, gets error (average: 2-5 errors in first 20 turns).

Without 07: CA reads verbose `ca_guidance.notes`, tries multiple wrong payloads, wastes 2-5K tokens of trial-and-error per error. **With 07**: CA reads `error_name` → `corrected_payload` → retry once with the exact fix. One-turn recovery, ~3K saved per error. Router and tiers don't help here — routing is already done, tier is already chosen.

**Cross-case impact**: 00+04 save on every turn of every case. 07 saves only on error turns but each save is 2-5X the per-turn savings of the others. Combined: all three cases covered with minimum overlap.

---

## Final Selection (Top 3)

| Rank | Skill | Rationale | Context saved |
|------|-------|-----------|--------------|
| **1** | **00_skill_router** | Bootstrap protocol eliminates 5-10K of schema-discovery overhead per session. Decision tree saves 5-15K on routing ambiguity. Every turn benefits. | ~10-20K/session |
| **2** | **04_tool_tiers** | Token protection map prevents 403 round-trips. Tier matrix prevents wrong-tool burns. Directly complements router — router says "what", tiers says "how to call safely". | ~3-8K/session |
| **3** | **07_error_schema_hints** | Error recovery is the #1 silent token drain. Each structured error recovery saves 2-5K vs trial-and-error. Hits 2-5X/session across ALL prompt cases (bootstraps get field alias errors, tool choices get SCHEMA_VIOLATION, all sessions get some errors). | ~6-15K/session |

---

## Two Tempting Skills Deferred

### 08_command_windows (deferred — would be #4)
**Why tempting**: Every shell command on Windows risks quoting failure. A single failure costs 2-3 round-trips.
**Why deferred**: Shell commands happen ~5-10/session. Tier decisions happen on EVERY turn. 04 saves more frequently. Router already covers `run_project_command` in its task→tool matrix. The quoting pain is real but less frequent than routing/errors.
**Synced after**: 5 sessions, if quoting failures exceed 2/session on average.

### 15_search_code_intel (deferred — would be #5)
**Why tempting**: Every code investigation uses search. Optimizations like `find_symbol(include="all")` save 2-3 calls per search.
**Why deferred**: Native search tools (`find_symbol`, `search_code`) are already intuitive — the skill adds marginal efficiency (~20% savings on search). Router/tiers/errors save 50-80% on their domains. Search optimization is a nice-to-have, not a must-have for first rounds.
**Synced after**: 5 sessions, when write volume increases and search efficiency becomes limiting.

---

## Disagreement with OpeC

**No disagreement.** OpeC's analysis is sound and the rank ordering matches my prompt-case walkthrough. The three chosen skills (00 + 04 + 07) cover all three prompt types with minimal overlap and maximal context savings per token spent.

---

## Exclusions Confirmed

| Skill | Status | Reason |
|-------|--------|--------|
| 24_council_tr | OLD / REMOVE | Baked into AGENTS.md; redundant sync causes dual-trigger noise |
| 14_token_jwt_redesign | DO NOT SYNC | Design spec only; zero CA value |
| 19_image_mode | DO NOT SYNC | Vision tokens > text tokens; inefficient for most CAs |

---

**Next**: `manage_agent(action='sync_skill', skill_name='00_skill_router', ..., dry_run=false)` for clau, codo, comc, hermes.

END_COMC1_3_SKILL_SELECT_R1
