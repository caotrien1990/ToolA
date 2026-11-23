---
version: "1.0.0"
last_verified: "2026-11-22"
verified_head: "dd41e2fc8f78761f8f7b9a104fb5cd98fdf1cad0"
name: update-rule-skill-all-ca
description: Use when updating council/core/worker rules or canonical skills and propagating them to every CA. Prevents missed sync_rule/sync_skill/adapter steps and stale generated rule files.
---

# Update Rule/Skill All CA

## Trigger

Use this skill when the task says update/sync rule, leader rule, worker rule, council skill, skill loop, verify skill, or "cập nhật rule skill cho toàn bộ CA".

## Canonical Sources

- Council SOT: `.council/council_config.json`, `.council/council_rule_leader.md`, `.council/global_rule_worker.md`.
- Skill SOT: `.ToolA_Skill/SKILLS.md` and `.ToolA_Skill/skills/<skill_name>/SKILL.md`.
- Runtime sync tool: HTTP `[A:API] /api/call/manage_agent`.
- Backend behavior/schema SOT: `code_tools.py` and `orchestrator_server.py`. Query these when changing tool contracts, sync behavior, council routing, or worker model handling.

## Required Workflow

1. Read live `manage_agent` schema before syncing.
2. Identify whether the change is:
   - rule-only,
   - skill-only,
   - council config/routing,
   - backend/tool-schema behavior,
   - or mixed.
3. Edit only canonical files. Do not edit generated CA rule files or copied skill folders by hand.
4. If `.council/council_config.json` changes, run `council_config_sync` if the change affects derived council docs; otherwise document why not.
5. Run dry-run first:
   - `manage_agent(action="sync_rule", target="all", dry_run=true)` for rule changes.
   - `manage_agent(action="sync_skill", target="all", skill_name="<name>", dry_run=true)` for each changed skill.
   - OpenCode folder targets: Before each OpenCode folder sync, inventory the effective loader roots and move discoverable backup trees (_ma_archive and loose .bak_ma folders) to a retained location outside every root. Record source and destination. Compare dry-run source, target, file count and folder SHA-256 with the manifest for this pair, including bundled references. After apply, check match and member hashes, relocate any newly created archive, then run opencode debug skill --pure from the target project with the real configuration. Match the frontmatter name to the expected active SKILL.md path. A missing or different winner stops the next sync. Repeat on future syncs.
6. Apply sync:
   - `manage_agent(action="sync_rule", target="all", dry_run=false)`.
   - `manage_agent(action="sync_skill", target="all", skill_name="<name>", dry_run=false)`.
7. Adapter targets need explicit follow-up after `sync_rule` because rule sync can overwrite adapter references:
   - Use only adapter targets returned by `manage_agent` dry-run / live registered CA metadata.
   - Run the matching adapter format reported or required by each registered target.
   - Do not hardcode retired or unregistered CA names.
8. If backend Python changed, run `py_compile`, restart [A] with `restart_orchestrator_via_bat` (delay_seconds=2 only), wait for health, then rerun schema verification. `[A:S]` is the MECHANISM that tool drives, NOT the call path -- launching the .bat yourself is a kill-pipeline reinvention. Proof of restart is `GET /health` showing a NEW `pid` AND a CHANGED `generation`; the tool answering ok:true is not proof. Do not restart [A] while worker dispatch is in flight.
9. Verify propagation with bounded `rg` against:
   - canonical files,
   - every rule_file in manage_agent(action='sync_rule', target='all', dry_run=true). Compare against the live manifest for this run.
10. Report: changed canonical files, sync commands/results, adapter targets, tests, stale references found/cleared, and any `unknown/not verified`.

## Guardrails

- Never use unsupported fields such as `slot` with `council_worker_model`; read live schema if unsure.
- Never assume `sync_skill target=all` writes adapter references; check `adapter_needed` and run explicit target sync.
- Never leave stale provider/model/cost text in leader rule, council config, or skill router.
- Never claim all CA updated until both native skill folders and adapter references are verified.
