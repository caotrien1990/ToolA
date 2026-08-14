# OpeC Skill-Sync Local Audit R1 — 2026-06-19

## 1. Registry & CA Skill-Dir Evidence

Source: `agents_registry.json` (7 agents)

| CA | id | provider | skill_dir configured | skill_format | Notes |
|----|-----|----------|---------------------|-------------|-------|
| clau | clau | claude | `C:/Users/ADMIN/.claude/skills` | folder | route_id=clau, worker=ClauC |
| codo | codo | codex | `C:/Users/ADMIN/.codex/skills` | folder | route_id=codo, worker=CodoC |
| clin | clin | cline | **MISSING** | — | No skill_dir field |
| copi | copi | copilot | **MISSING** | — | No skill_dir field |
| opencode | ope | opencode | **MISSING** | — | No skill_dir in registry |
| comc | comc | commandcode | `C:/Users/ADMIN/.commandcode/skills` | folder | route_id=comc, worker=ComC |
| hermes | hermes | hermes | `C:/Users/ADMIN/AppData/Local/hermes/skills` | folder | route_id=hermes, worker=HermC |

**Key evidence:**
- Clau's `.claude/skills/` already has `24_council_tr/` synced (with `.bak_ma` backup present)
- Ope (opencode) has **no** `skill_dir` → `manage_agent(action='sync_skill')` will skip Ope with `"status":"skipped","reason":"no skill_dir configured"`
- Clin/copi also have no `skill_dir` → will be skipped
- Codo/comc/hermes have `skill_dir` but no skills currently synced (directories likely empty)

## 2. Canonical Skill Classification Table

Source: `.ToolA_Skill/skills/` — 24 skill folders, each with `SKILL.md` containing frontmatter.

| # | Skill | risk_level | Classification | Rationale |
|---|-------|-----------|---------------|-----------|
| 00 | skill_router | low | **essential_for_all_CA** | Master decision tree; every CA needs routing |
| 01 | bootstrap_health | low | **essential_for_all_CA** | Session entry point; verifies [A] reachable |
| 02 | mode_control | medium | **essential_for_all_CA** | DEFAULT vs EXECUTE gating; all CAs need this |
| 03 | path_session | medium | **essential_for_all_CA** | Path/session lifecycle; every CA operates on paths |
| 04 | tool_tiers | low | **essential_for_all_CA** | T1-T4 tier matrix; universal |
| 05 | write_pipeline | high | **essential_for_all_CA** | T2 write operations; core mutation path |
| 06 | token_auth | medium | **essential_for_all_CA** | Token lifecycle; needed before any T2/T3 write |
| 07 | error_schema_hints | low | **essential_for_all_CA** | Error recovery; saves tokens in every session |
| 08 | command_windows | medium | **essential_for_all_CA** | Windows-first quoting; all CAs run shell commands |
| 09 | testing_validation | medium | **essential_for_all_CA** | Post-write verify; all CAs validate |
| 10 | coverage_report | low | **worker_only** | Test reporting; only workers running bulk tests need this |
| 11 | repo_vs_runtime | medium | **essential_for_all_CA** | Debug routing; all CAs debug |
| 12 | native_audit | medium | **essential_for_all_CA** | Native fallback audit trail; governance requirement |
| 13 | apply_after_dry_run | high | **essential_for_all_CA** | Safe apply pipeline; all CAs doing writes |
| 14 | token_jwt_redesign | low | **risky_do_not_sync** | Future design spec only; would confuse CAs if loaded |
| 15 | search_code_intel | low | **essential_for_all_CA** | Code search; universal read tool |
| 16 | server_lifecycle | medium | **worker_only** | Port/server management; mainly leader/T4 workers |
| 17 | session_audit | low-medium | **essential_for_all_CA** | Session lifecycle; audit trail; universal |
| 18 | runtime_dom_T4 | medium | **worker_only** | Browser/DOM; only ClauC/T4 workers need this |
| 19 | image_mode | low | **risky_do_not_sync** | Token-saving via render; model-specific, easy to misuse |
| 21 | tool_matrix | low | **A_internal_only** | Audit drift tool; for [A] maintainers |
| 22 | electron_automation | medium | **worker_only** | Electron app driving; specific to Electron-target tasks |
| 24 | council_tr | high | **leader_only** | Council trigger/routing; only council leader CA needs this |
| 26 | workflow_ingestion | medium | **worker_only** | MD workflow dispenser; only workers running workflows |

## 3. manage_agent sync_skill Behavior (read-only verification)

**Code location:** `code_tools.py:12398-12473` (`manage_agent`), `code_tools.py:12358-12395` (`_ma_write_skill`)

**Behavior:**
1. Source = `{project_path}/.ToolA_Skill/skills/<skill_name>/` (canonical only; no shim paths allowed)
2. Validates SKILL.md exists in source
3. Target = agent's `skill_dir` + skill_name
4. If agent has no `skill_dir` → **skipped** with `"status":"skipped","reason":"no skill_dir configured (not_supported)"`
5. If dry_run → returns `"status":"dry_run"` with sha256 preview
6. On actual copy: creates `.bak_ma` backup of existing target, then `shutil.copytree`
7. Post-copy: validates sha256 match between source and target
8. Operates per-agent; `target="all"` loops all registered agents

**Currently-patched agents that will be skipped:** opencode (no skill_dir), clin (no skill_dir), copi (no skill_dir)

## 4. Same-Identity SELF_MESSAGE Workaround

**Problem:** Broker dispatches to same-identity worker (e.g., Ope → OpeC) cannot use `council_message` because both sides share the same agent process/token. `council_message` field `sender=` would produce self-loop.

**Workaround:** 
- For same-identity broker → worker: use **direct CLI visible terminal** (`opencode run "..." --model <model>`) instead of `council_message`. Output goes to console/log, not the broker bus.
- Field: use `sender=user` in council_message when the message originates from a broker acting on user's behalf (per `CORE_RULE` transport policy).
- The outbox file `outbox_ope.md` at `.council/outbox_ope.md` is the worker's designated output path for this session. Report must be written there or to the `official_output_path`.

## 5. Recommended Sync Order (Round 1)

Priority for `manage_agent(action='sync_skill', skill_name='<name>', dry_run=false)`:

| Batch | Skills | Target Agents |
|-------|--------|---------------|
| 1 (essential) | 00-09, 11-13, 15, 17 | clau, codo, comc, hermes |
| 2 (worker extras) | 10, 16, 18, 22, 26 | comc, hermes (and clau for T4) |
| 3 (leader only) | 24_council_tr | clau (leader CA only) |
| skip | 14, 19, 21 | none (risky/A_internal) |

Note: opencode/ope has no `skill_dir`; it reads skills inline from `AGENTS.md` references. If Ope needs skill files, a `skill_dir` entry must first be added via `manage_agent(action='register', agent_id='opencode', skill_dir='C:/Users/ADMIN/.config/opencode/skills', skill_format='folder')`.

---
END_OPEC_SKILL_SYNC_LOCAL_R1
