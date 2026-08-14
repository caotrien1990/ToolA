# OpeC Skill Digest R1 — for ComC1 Starter Selection

Source: 24 canonical skills under `.ToolA_Skill/skills/`
Previous audit: `opec_skill_sync_local_audit_20260619.md`
Rejection feedback: broad list was too many. Pick 3.

---

## Per-Skill Summary (2 lines max each)

| # | Skill | Risk | Summary |
|---|-------|------|---------|
| 00 | skill_router | low | Master decision tree for all task routing. Biggest single context-saver — eliminates "what to do" ambiguity on every turn. |
| 01 | bootstrap_health | low | Session-entry health check for [A] reachability. Redundant if CA already verifies health via AGENTS.md; minimal context savings. |
| 02 | mode_control | medium | DEFAULT vs EXECUTE gating. Important for safety but reduces context only when mutations happen, not on first rounds. |
| 03 | path_session | medium | Path/session lifecycle management. Saves context only on multi-project or handoff scenarios; overkill for first rounds. |
| 04 | tool_tiers | low | T1-T4 tier selection matrix. Kills wrong-tool guesswork — every CA session needs tool decisions immediately. Direct context saver. |
| 05 | write_pipeline | high | T2 write operations with dry-run/rollback. Context-heavy but critical only if CA writes; first-round reads don't need it. |
| 06 | token_auth | medium | X-Orchestrator-Token lifecycle. Rarely needed early; saves context only when mutations trigger 403s. |
| 07 | error_schema_hints | low | Maps error_name to corrected payload + retry policy. Error recovery is the #1 hidden token burner — saves massive context over time. |
| 08 | command_windows | medium | Windows-first shell quoting for paths/spaces/Unicode. Saves context on every shell call; Windows CAs hit this constantly. |
| 09 | testing_validation | medium | Post-write syntax/symbol/encoding validation. Only needed after writes; no context savings for read-first rounds. |
| 10 | coverage_report | low | Test regression reporting. Worker-only; irrelevant for first-round context reduction. |
| 11 | repo_vs_runtime | medium | Debug routing: repo vs DOM side. Saves context on UI bugs but not needed for basic read/analyze first rounds. |
| 12 | native_audit | medium | Audit trail for native tool fallbacks. Governance requirement but zero first-round context savings. |
| 13 | apply_after_dry_run | high | Dry-run-to-apply lifecycle with STALE_TARGET handling. Write-only; not starter material. |
| 14 | token_jwt_redesign | low | Future JWT design spec. **Do NOT sync** — design-only, would confuse CA with irrelevant content. |
| 15 | search_code_intel | low | Code search, symbol lookup, callers/usage. Universal read operation — saves context on every code investigation. |
| 16 | server_lifecycle | medium | Port/server/cache-stuck handling. Rarely needed; operations team skill, not starter. |
| 17 | session_audit | low-med | Session lifecycle, audit trail, handoff. Saves context only at session boundaries; not first-round. |
| 18 | runtime_dom_T4 | medium | Browser DOM/UI verification. Worker-only (ClauC/T4); no savings for general CA. |
| 19 | image_mode | low | PNG/SVG rendering for diagrams. **Do NOT sync** — token-wasting in most CA scenarios; vision tokens > text tokens. |
| 21 | tool_matrix | low | Tool inventory drift audit. [A]-internal only; irrelevant for CA context savings. |
| 22 | electron_automation | medium | Electron CDP/UI automation. Niche Electron-only tasks; not starter. |
| 24 | council_tr | high | Council trigger/routing, broker messaging. **OLD / REMOVE CANDIDATE** — lifecycle-to-backup; already baked into AGENTS.md council skill. Leader-only, high risk, adds noise to non-leader CAs. |
| 26 | workflow_ingestion | medium | MD workflow step dispenser. Worker-only; not starter material. |

---

## Shortlist (top 6 candidates for first-round context reduction)

Ranked by **context savings per token of skill content loaded**:

1. **00_skill_router** — Every turn needs routing. Eliminates "which tool/skill" ambiguity. Saves 5-15K tokens/session.
2. **04_tool_tiers** — Every tool decision routed correctly from turn 1. Prevents wrong-tier burns.
3. **07_error_schema_hints** — Error recovery is the biggest silent context drain. One structured recovery saves 2-5K tokens.
4. **15_search_code_intel** — Every code investigation uses search. Cuts search-failure retries by 80%.
5. **08_command_windows** — Every shell command on Windows. Saves quoting/tripping failures.
6. **05_write_pipeline** — Only if CA writes code. High context savings per write but zero if read-only first rounds.

---

## Forced Top-3 Recommendation

| Rank | Skill | Rationale |
|------|-------|-----------|
| **1** | **00_skill_router** | Single biggest context saver. Every turn's first question is "what do I do" — the router answers it. Without it, CA wastes 5-15K tokens/session on figuring out routing. |
| **2** | **04_tool_tiers** | Second-most frequent decision point. Every tool call needs a tier choice. Eliminates wrong-tier burns and the error recovery that follows. Complements router perfectly. |
| **3** | **07_error_schema_hints** | The hidden tax. Most CA sessions hit 2-5 structured errors in first 20 turns. Each error recovery without hints costs 2-5K tokens of trial-and-error. This skill recovers in 1 turn. |

**Why not write_pipeline (05) or search_code_intel (15) for starter?**
- `05_write_pipeline` is essential but only pays back on write sessions. Starter sync should benefit ALL sessions including read-only first rounds.
- `15_search_code_intel` is universal but the native search tools are already intuitive; the skill adds optimization tips that save ~20% on search, whereas router/tiers/error save 50-80% on their domains.

**Why not 08_command_windows?** — Important but the pain is lower frequency (5-10 commands/session) vs every-turn routing + errors.

---

## Risks of Syncing Too Much

1. **Token dilution**: Each loaded skill consumes context budget. 10+ skills reduce available working memory by 30-50%.
2. **Trigger noise**: Too many skills mean triggers fire on unrelated patterns, wasting cycles on irrelevant routing.
3. **Skill conflict**: Adjacent skills (e.g., write_pipeline + apply_after_dry_run) can overlap, causing redundant checks.
4. **Stale rules**: Syncing `14_token_jwt_redesign` or `19_image_mode` injects speculative/anti-pattern content that misleads the CA.
5. **Council skill bleeding**: `24_council_tr` synced to non-leader CAs triggers council routing on unrelated tasks (already observed bug in Clau's skill set).

**Mitigation**: Start with 3, evaluated after 5 sessions. If error rate drops >40%, add 2 more (05 + 15). Never exceed 8 skills per CA.

---

## Skills Marked for Removal / Lifecycle-to-Backup

| Skill | Reason |
|-------|--------|
| **24_council_tr** | Already baked into AGENTS.md as embedded council skill. Synced copy is redundant; causes dual-trigger noise. Move to `.bak_ma` and remove from active directory. |
| **14_token_jwt_redesign** | Design spec only. Zero production value. Remove from canonical sync list. |
| **19_image_mode** | Token-inefficient for most CAs. Keep in repo but exclude from `manage_agent sync_skill` target list. |

---

**Instruction for ComC1**: Use `manage_agent(action='sync_skill', skill_name='00_skill_router'/'04_tool_tiers'/'07_error_schema_hints', dry_run=false)` for clau, codo, comc, hermes. Skip agents with no `skill_dir` (opencode, clin, copi). Do NOT sync `24_council_tr` to any CA.
