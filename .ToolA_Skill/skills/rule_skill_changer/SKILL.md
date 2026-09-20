---
name: rule_skill_changer
description: Trigger: "cập nhật skill rule". Use when changing rules, council routing, worker routing, skill bodies, skill router, or syncing rule/skill to all CA. Forces querying related [A] Python files before deciding whether code changes are needed.
trigger:
  - task_pattern: "cap nhat skill rule | update rule skill | sync skill rule | sync rule to all CA"
  - tool: "manage_agent"
---

# Rule Skill Changer

## Trigger

Use this skill when Anh says `cập nhật skill rule`, `update rule skill`, `sync skill rule`, or asks to update leader/worker/council/verify/loop skills across all CA.

## Goal

Update canonical rule/skill sources, check related [A] Python behavior, sync to all CA, verify propagation, and avoid stale generated files or missed adapter references.

## Required 15-Step Order

Do not sync first. Follow this numbered order exactly:

1. **Define scope**: write the exact target before editing, such as leader rule, worker rule, skill body, skill router, model route, tool schema, adapter reference, or runtime behavior.
2. **Query mandatory context**: read the canonical rule/skill/config files and related Python files listed below with bounded search.
3. **Classify the change type**: mark whether the change is wording-only, worker/model routing, tool schema/API, sync behavior, dashboard/UI, runtime dispatch, worker runtime, or adapter propagation.
4. **Decide whether Python must change**: if the change affects schema, route model, fallback rule text, dispatch, sync, worker runtime, tool fields, dashboard, or adapter behavior, inspect and update `code_tools.py` / `orchestrator_server.py` as needed. If not, record `Python checked; no backend/schema change required`.
5. **Edit canonical sources only**: update `.ToolA_Skill/...`, `.council/...`, and Python source if needed. Do not hand-edit generated CA rule files or copied skill folders.
6. **Run pre-sync diff gate**: inspect changed canonical files and search stale route/model/worker/tool-field references before any sync.
7. **Compile/test Python if changed**: run `py_compile`; restart [A] via `[A:S]` only if Python source changed and no worker dispatch is in flight; wait for `/health`.
8. **Verify live schema/tool behavior**: read relevant live schemas such as `council_message`, `council_worker_model`, `manage_agent`, or the changed tool; run a small behavior test when runtime/tool behavior changed.
9. **Dry-run sync**: run `manage_agent sync_rule dry_run=true` and/or `manage_agent sync_skill dry_run=true` only after source/diff/schema are clean.
10. **Apply sync**: run the required `sync_rule` / `sync_skill` only if dry-run is correct.
11. **Adapter sync after rule sync**: update only adapter targets returned by the live registry/tool dry-run, because `sync_rule` can overwrite adapter refs. Do not hardcode CA names.
12. **Verify propagation**: bounded-search canonical files and all target CA locations.
13. **Run final stale-reference check**: search again for removed model ids, removed worker slots, old triggers, obsolete fallback text, and old tool fields. Backup/log/session files are not blockers; canonical/live files are blockers.
14. **Log native event if native edit was used**: record files, reason, tests, restart status, and sync status through [A] `log_native_event`.
15. **Report result**: list changed files, Python changed or not, sync status, tests/schema checks, stale references, and any `unknown/not verified`.

## Mandatory Context Query

Before editing, query/read these sources with bounded search:

- `.ToolA_Skill/SKILLS.md`
- `.ToolA_Skill/skills/<target_skill>/SKILL.md`
- `.council/council_config.json`
- `.council/council_rule_leader.md`
- `.council/global_rule_worker.md`
- `agents_registry.json`
- `code_tools.py`
- `orchestrator_server.py`
- `orchestrator/core/registry_data.py` -- tool and field `description` text; a description may name specific agents or counts.
- `orchestrator/core/ca_policy_matrix.py` -- worker-id / desktop-id tuples and per-agent gates.
- `examples/SLIM_RULE.txt` -- the PRODUCER `sync_rule` renders every CA rule file from; one stale sentence here becomes one stale rule file per registered agent.

For `code_tools.py`, check these areas when relevant:

- `manage_agent` implementation and `_ma_write_skill*` helpers.
- `council_config_sync`, `council_config_diff`, and derived council docs.
- `convene_council`, fallback roster/coordination text, and council SOT loading.
- `read_skill`, `audit_tool_matrix`, tool schema declarations, and skill router checks.
- `TOOL_MAP`, `TOOL_SCHEMAS`, `TOOL_PHASES`, and mutation/auth metadata if a tool contract changes.

For `orchestrator_server.py`, check these areas when relevant:

- HTTP alias/schema/drop-field behavior for tools.
- wrapper functions exposing `manage_agent`, `council_worker_model`, `council_message`, and `convene_council`.
- worker model runtime/config readers, queue/runtime status, and dashboard endpoints.
- any backend endpoint or UI contract touched by the rule/skill change.

If no Python change is needed, explicitly record: `Python checked; no backend/schema change required`.

## Pre-Sync Diff Gate

Before any sync, run a bounded diff/stale-reference pass against canonical files:

- Check `git diff -- .council .ToolA_Skill agents_registry.json code_tools.py orchestrator_server.py orchestrator/core examples` or an equivalent bounded diff if git scope is unavailable. `orchestrator/core/` and `examples/` are the two paths easiest to omit and both hold PRODUCERS (see Drift Location Map), so a diff that skips them reports a clean tree while a rule enum or a tool description is stale.
- Search for stale route/model/worker names introduced or removed by the change, for example old provider ids, removed worker slots, old trigger wording, and obsolete fallback text.
- If stale references remain in `code_tools.py` or `orchestrator_server.py`, fix them before sync. Do not assume `council_config_diff` is enough; it only checks generated council docs, not Python fallback constants.
- If `council_config_diff` is available, run it after canonical config/doc edits and record the result.

## Edit Rules

- Edit canonical files only. Never hand-edit generated CA rule files or copied skill folders.
- If the change alters tool schema, sync behavior, model routing, dashboard fields, queue behavior, or worker runtime behavior, update the related Python code and schema text too.
- If only wording/skill/routing guidance changes and live Python behavior is unchanged, do not touch Python.
- Do not restart [A] while worker dispatch is in flight.

## Sync Workflow

Only start this section after the Pre-Sync Diff Gate is clean.

1. Read live schema: `GET /api/tools/manage_agent/schema`.
2. Run dry-run:
   - `manage_agent(action="sync_rule", target="all", dry_run=true)` when rules/config changed.
   - `manage_agent(action="sync_skill", target="all", skill_name="<skill>", dry_run=true)` for every changed skill.
3. Apply:
   - `manage_agent(action="sync_rule", target="all", dry_run=false)`.
   - `manage_agent(action="sync_skill", target="all", skill_name="<skill>", dry_run=false)`.
4. Adapter follow-up after every `sync_rule`:
   - Use only targets reported by `manage_agent` dry-run / live registered CA metadata.
   - For each adapter target, run the matching `sync_skill` format reported or required by that registered target.
   - Do not hardcode retired or unregistered CA names.
5. If `.council/council_config.json` changed and derived docs are affected, run `council_config_sync`.

Do not put Python compile/restart after apply sync. Python compile/restart/live-schema verification belongs to steps 7-8 above, before dry-run/apply sync.

## Verification

Verify with bounded search in:

- canonical files under `[A:P]`.
- every `rule_file` path reported by the live `manage_agent(action='sync_rule', target='all', dry_run=true)` -- that dry-run IS the list and `agents_registry.json` is its SOT. Never verify against a path set written down here: it drifts both ways at once. Measured 2026-09-20: the live target set is 10 files, while the hardcoded list this line replaced named `C:\Users\ADMIN\.commandcode` (retired id `comc`, absent from the registry, `AGENTS.md` last written 2026-07-15, receives no sync) and omitted 6 registered targets -- so following it checked 4 live paths out of 10 plus one dead one and then reported "propagation verified".

Required report:

- scope and change type.
- canonical files changed.
- Python files checked and whether changed.
- pre-sync diff/stale-reference result.
- live schema/tool behavior verification.
- sync_rule result.
- sync_skill result per skill.
- adapter sync result.
- final stale-reference result.
- native event log status if native edit was used.
- stale references removed or still present.
- tests/validation run.
- `unknown/not verified` items, if any.

## Common Pitfalls

- `sync_skill target=all` may return `adapter_needed` for opencode/clin; explicit adapter sync is still required.
- `sync_rule` can overwrite adapter skill references, so adapter sync must run after rule sync.
- `council_worker_model` accepts only `action`, `agent`, optional `provider`, and `model`; never use `slot`. Normal routing sets only `agent` + `model` because backend keeps/derives provider from config.
- Updating guidance without checking `code_tools.py` / `orchestrator_server.py` can leave live API behavior inconsistent with rules.

## Drift Location Map (complete)

A clean drift sweep is not a clean surface. Measured 2026-09-20: `audit_tool_matrix` answered
`drift 0 / alias_drift 0 / progressive_gaps 0` with `258 = 258 = 258` tools, `rule_drift_check`
answered `20 rows, 100% info` (its baseline), and `manage_agent(sync_mcp, dry_run)` answered
`10/10 ok` -- while **4 real drifts were open at that moment**. All three compare
**code <-> schema <-> registry**; not one of them opens a doc, a skill body, or a rule file and
reads its prose. The three tools clear class A only. They are the start of the sweep, never the end.

Sweep all three classes, in this order. For each the rule is the same: ask the call that PRODUCES
the set, never a path/id list copied into a doc -- a copy drifts in BOTH directions at once and
that is exactly why it reads as correct (see Verification above).

**Class A -- machine-checkable.** `audit_tool_matrix` is `scope: runtime_only` and says so in its
own `scope_note`: it does not parse `.py` on disk, so `drift_count=0` means the RUNNING process is
self-consistent, not that disk matches it. Run it AFTER the restart or it grades the old process.
- `audit_tool_matrix` -- code_tools vs schema vs inventory, alias drift, progressive gaps.
- `rule_drift_check` -- `checks_run` = agent_list, behavior_sets, constants, tool_names. Row list
  is `drifts` (plural), not `drift`.
- `manage_agent(action='sync_mcp', target='all', dry_run=true)` -- the [A] MCP block per agent.

**Class B -- live code that hardcodes an id, a name or a count. No check reads these; grep them.**
- `orchestrator/core/registry_data.py` -- tool and field `description` text, which may name
  specific agents; measured, one named a retired id for months. After editing, confirm the daemon
  SERVES your bytes (`GET /api/tools/<tool>/schema`): a later layer in `code_tools.py` can override
  this file, so a correct edit here is not automatically the served text.
- `orchestrator/core/ca_policy_matrix.py` -- worker-id and desktop-id tuples, per-agent host gates.
- `orchestrator_server.py` -- council roster / label / default-provider-model tables, per-agent CLI
  branches and output parsers, and the dashboard order tuple guarded by a set-equality assert that
  fails loudly on drift.
- `code_tools.py` -- agent names embedded in `reason`/`note` strings returned to a CA.
- `examples/SLIM_RULE.txt` -- also a PRODUCER: `sync_rule` renders every CA rule file from it, so
  one stale sentence here becomes 10 stale rule files at the next sync.

**Class C -- prose. No check, and not all of it is drift.**
- `.ToolA_Skill/skills/**/SKILL.md` and `.ToolA_Skill/SKILLS.md` -- routing lists, verification
  lists, restart instructions, id enums.
- `.council/council_config.json` (`agents` / `roster_order` / `messaging_to_enum`) and
  `.council/council_rule_leader.md` -- presence here means a worker is treated as LIVE.
- Historical council artifacts (`.council/BENCHMARK_*`, round reports, transcripts, `outputs/`) are
  EVIDENCE, not drift. Do not "clean" them: deleting a pointer deletes the measurement that
  justified a decision.

**Propagation is part of the sweep, not a follow-up.** A canonical edit under
`.ToolA_Skill/skills/` is live for nobody until `sync_skill` runs. Measured on two skill files: of
10 targets, 9 had no copy at all and the single target that did have one held the STALE (buggy)
copy. Two traps in that measurement: the dry-run `reason` says `would copy (native folder)` for
every target whether or not anything differs, so it is NOT evidence that propagation is owed --
hash-compare the destination instead; and a `sync_skill` row names its destination `target`
(already the full skill-folder path), not `skill_dir`, so guessing that key prints nothing for all
10 rows and invites the exact opposite conclusion.

**Close-out gate.** Before reporting a clean sweep, report per class: A = the three tool answers
plus the `generation` they came from; B = which files were grepped and for which id/name/count;
C = which prose files were read, and which mentions were deliberately KEPT as evidence. A sweep
that reports only class A is reporting 1 of 3, which is how "drift 0" and "4 drifts still open"
were both true on the same day.
