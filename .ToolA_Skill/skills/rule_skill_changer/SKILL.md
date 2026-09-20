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
- `council_config_diff` -- the derived-doc check, TWO-WAY since 2026-09-20: enabled labels must be
  PRESENT and retired labels (the rows kept `enabled: false`) must be ABSENT, reported as
  `retired labels still present: ...` with the retired set echoed in `retired_labels`. It used to ask
  only the first question, which is why it answered `0 drift / 3 doc targets` while
  `council_rule_leader.md` still routed to two retired workers -- that one-way check WAS the root
  cause of "drift bao 0 nhung kiem tra lai con drift". Even two-way it is not a full doc check: it
  reads the roster labels and 4 literal markers, nothing else. The retired match is word-boundary,
  because the label `pic` sits inside `explicit`.
**Class B -- live code that hardcodes an id, a name or a count. No check reads these; grep them.**
- `orchestrator/core/registry_data.py` -- tool and field `description` text, which may name
  specific agents; measured, one named a retired id for months. After editing, confirm the daemon
  SERVES your bytes (`GET /api/tools/<tool>/schema`): a later layer in `code_tools.py` can override
  this file, so a correct edit here is not automatically the served text.
- `orchestrator/core/ca_policy_matrix.py` -- worker-id and desktop-id tuples, per-agent host gates.
- `orchestrator_server.py` -- council roster / label / default-provider-model tables, plus
  per-agent CLI branches and output parsers. The dashboard order tuple was the B3 hardcode here and
  is now DERIVED from `_COUNCIL_AGENTS` (2026-09-20), so that one can no longer drift; the LABEL
  table still can, because `canonical_labels` is applied via `labels.update(...)` AFTER the config
  labels and therefore OVERRIDES the SOT.
- `code_tools.py` -- agent names embedded in `reason`/`note` strings returned to a CA.
- `examples/SLIM_RULE.txt` -- also a PRODUCER: `sync_rule` renders every CA rule file from it, so
  one stale sentence here becomes 10 stale rule files at the next sync.

**Class C -- prose. No check, and not all of it is drift.**
- `.ToolA_Skill/skills/**/SKILL.md` and `.ToolA_Skill/SKILLS.md` -- routing lists, verification
  lists, restart instructions, id enums.
- `.council/council_config.json` (`agents` / `roster_order` / `messaging_to_enum`) -- presence here
  means a worker is treated as LIVE. Retiring one is `enabled: false`, NOT a row delete:
  `_council_active_from_config` in `orchestrator_server.py` and `_council_enabled_agents` in
  `code_tools.py` both filter on `enabled`, so a disabled row vanishes from the roster, the labels,
  `route_capabilities` and the 5 council tool schema descriptions while the row itself survives for a
  later re-enable -- and every test that uses that id as a live example keeps passing. ONE field
  escapes the filter: `_council_build_from_config` returns `messaging_to_enum` RAW, so a retired id
  stays in the rule's `to` enum until dropped by hand. This file is also XROOT-GATED -- `write_fix`
  answers `XROOT_DENIED`. It is the hand-edited SOT: edit it natively (it is inside [A]'s own tree,
  so `log_native_event` is the authorized path).
- `.council/council_rule_leader.md` and `.council/global_rule_worker.md` -- **hand-maintained SOT,
  NOT generated.** `code_tools.py:16621` names the leader doc the leader-rule source of truth, and
  `council_config_sync` only marker-DIFFS these two -- it never regenerates them from the config, so
  a retirement in `council_config.json` reaches them only when a CA edits them by hand. They are not
  XROOT-gated, so `write_fix` works (bottom-up, one region per call). Every place a retired id hides,
  measured 2026-09-20 -- in `council_rule_leader.md`: the per-agent route TABLE row, the
  `Suspended routing defaults` line, the `Chao <Agent>,` greeting list, and the old preferred-chain
  line. In `global_rule_worker.md`: the per-agent quick-reference bullet, the greeting list, the
  greeting line near the end, the `to='<...>'` enum inside the `council_message` PowerShell example,
  and the transport line naming which workers are HTTP-only. The greeting lists drift the OTHER way
  too -- the leader list had never included the live GroC. Rewriting a line also clears the
  double-encoded `ChA o` mojibake sitting in both files.
- Three literal markers are REQUIRED in each of those two docs. The live checks sit at
  `code_tools.py:16871-16875`, with the retired-label word-boundary scan just above at 16869-16870
  and the roster/retired reads at 16851-16853. An earlier version of THIS skill anchored them at
  `16849-16853` and was wrong -- a `file:line` anchor is exactly what the `constants` blind spot
  below cannot check, so the skill drifted against the code it documents.
- Each marker accepts SEVERAL literal variants, all matched lowercased, so grep for ANY variant and
  never for the single phrase: no-cost-chain accepts `no fixed chain`, `no cost chain`,
  `no fixed cost chain` or `do not use a cost chain`; the CodoC marker accepts `not default leader`
  or `not the default leader`; the ClauC marker accepts `clauc is excluded from normal routing
  order`, `clauc is outside the normal routing order` or `clauc is outside normal routing order`.
  Probing for one hand-picked phrase produces a FALSE marker gap: measured 2026-09-20, a probe
  reported 2 markers missing from both docs while `council_config_diff` correctly answered
  `in_sync` with 0 drift. The tool is authoritative; a hand probe that disagrees is the suspect.
  An edit that drops every variant of one marker flips the doc to `drift` for a reason that has
  nothing to do with the roster, so re-grep all three after any rewrite.
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

**Tool Coverage Map -- what each drift tool READS, and what it therefore cannot see.** Measured
2026-09-20 from the source plus a live call on daemon generation `068475af0aaf`. A tool whose NAME
contains `drift`/`audit`/`check` is no evidence it checks drift: the 26 tools matching those
fragments include a git-worktree probe and an HTML version scraper. Pick by what the tool OPENS,
and for every blind spot below the manual step is the only coverage that exists.

- `rule_drift_check` opens exactly THREE files -- `RULE_DRIFT_SOURCES` at
  `code_tools.py:3235-3239` = `examples/CORE_RULE.txt`, `examples/SLIM_RULE.txt`,
  `examples/SLIM_RULE_INLINE.txt`. Every rule text OUTSIDE `examples/` (the 10 rendered CA rule
  files, `.council/*.md`, every `SKILL.md`) is invisible to all four of its checks. Manual: grep
  those paths yourself, per classes B and C above.
  - `tool_names` (`:4737`) matches by POSITION only: `` `tool(`` and `/api/call/<tool>`. A stale
    tool named in prose as bare `` `tool` `` is deliberately NOT flagged -- that change removed 16
    false positives out of 16. The one exception is a name in `_DEPRECATED_TOOLS`, which is a
    ONE-member set (`electron_devtools_log`, `:17198`), so a freshly deprecated alias stays
    unchecked until it is added there. Manual: after renaming or retiring a tool, grep the rule
    files for the BARE name and add the old name to `_DEPRECATED_TOOLS`.
  - `agent_list` (`:4787`) is blind in BOTH directions. The id list is HARDCODED at `:4796` --
    `\b(clau|codo|gro|pic|ope|hermes|kilo|cozo)\b` -- so `fbuf`, `claultnai` and `zcode` are live
    registry ids this regex can never match: a stale mention of them cannot be reported at all.
    And the config side reads `cfg["agents"].keys()` WITHOUT the `enabled` filter, so a row retired
    the approved way (`enabled: false`) still counts as present and a rule file may keep routing to
    it at severity zero. Those two facts together are the other half of "drift bao 0 nhung kiem tra
    lai con drift". Manual: take the id set from `manage_agent(action='list')` +
    `agents_registry.json`, intersect it with the `enabled: true` rows of `council_config.json`, and
    grep every retired id yourself with a word boundary.
  - `behavior_sets` (`:4805`) opens NO file -- it is code vs code (`_MUTATION_SCOPE_MAP`,
    `TOKEN_REQUIRED_TOOLS`, `PROTECTED_TOOL_NAMES` vs `TOOL_MAP`, `tool_mutation_scope`,
    `tool_phase`). It is also where 100% of the live rows come from: 20 rows, all `info`, all the
    CAN-012 decoupled-by-design baseline. So `drift_count: 20` is the FLOOR of a healthy surface and
    no doc drift can ever raise it. Compare the row count PER CHECK, never the total.
  - `constants` (`:4827`) checks exactly four values: `MAX_READ_LINES`, `MAX_READ_LINES_HARD_CAP`,
    `MAX_SEARCH_RESULTS`, and `PORT` scraped out of `orchestrator_server.py` (8082 whitelisted as
    the compat port). Every other number a rule states -- tool counts, agent counts, `file:line`
    anchors, sha prefixes, day counts -- is unchecked. Manual: re-measure each digit you write, or
    state the UNIT and the producing call instead of the digit.
- `audit_tool_matrix` is `scope: runtime_only` by its own `scope_note`: it compares the in-memory
  `TOOL_MAP`/`TOOL_SCHEMAS` against `.md` docs and NEVER parses `.py` on disk. `drift_count=0`
  proves the RUNNING process is self-consistent, not that your edit is in it. Manual: restart [A],
  re-run it, and quote the `generation` the answer came from.
- `architecture_audit_compact` is a ~30-line DIGEST of that same matrix audit (plus alias drift,
  progressive gaps, inventory classification). It inherits the runtime_only blind spot exactly and
  opens no additional file: using it INSTEAD of the matrix saves ~4500 tokens and widens nothing.
- `progressive_matrix_check` is a STATIC harness over 4 checks (`public_tool_count`,
  `workflow_docs`, `schema`, `ui`) and 5 matrix rows, and says so itself in `runtime_test_note`:
  static matrix checks cannot prove browser/server behavior. Blind to every T4 runtime behavior.
  Manual: run the T4 harness for the surface you touched.
- `council_config_diff` reads the SOT config against `CORE_RULE.txt` + `.council/*.md` and asserts
  three things only: enabled labels PRESENT, retired labels ABSENT, the 3 literal markers present.
  Everything else in those docs -- route-table cells, greeting text, transport lines, mojibake --
  is outside it. Manual: the Class C enumeration above.
- `council_config_sync` (T2 WRITE) rewrites ONLY the cost-order chain and the roster list and backs
  up `<file>.bak_ccsync`. It does not regenerate the two hand-maintained council docs, so it is not
  a way to propagate a retirement into them.
- Four tools match the `check`/`audit`/`verify` fragments and are NOT drift tools. Do not count
  them in a sweep: `git_stale_check` answers git worktree/commit staleness (`reasons:
  ['dirty_worktree']`, and `no fetch performed`, so remote divergence is invisible);
  `bundle_version_check` scrapes a version marker out of HTML with a regex;
  `audit_reconciliation_report` reconciles unresolved session ERRORS and answers
  `SESSION_SCOPE_AMBIGUOUS` until `target_session_id` is passed whenever more than one session is
  open; `validate_file` proves SYNTAX only (`py_compile` / `node --check`).
- A sweep PATTERN is itself a blind spot. `\b(kilo)\b` CANNOT match `KiloC` -- `C` is a word
  character, so there is no boundary after `kilo` -- while `\b(pic)\b` DOES match `PiC`. Measured
  2026-09-20: a whole sweep pass reported clean on `KiloC` and `CozoC` for exactly this reason, and
  the miss was invisible because the same pattern DID find plenty of `pic` hits. Sweep council ids
  with `\b(kilo|pic|cozo)c?5?\b`, which covers the bare id, the `C`-suffixed worker label and the
  `C5`-style model slot in one pattern, and positive-control it against a line you KNOW is stale.
- `audit_tool_matrix` has NO `drift_count` key. Measured top level: `drift` (a list), `alias_drift`,
  `progressive_gaps`, `summary`, `inventory_classification`, `scope`, `scope_note`,
  `router_topics_count`, `matrix_log_path`, `ca_next_action`. Its own `scope_note` prose says
  "drift_count=0 means runtime is internally consistent" -- naming a key the payload does not carry
  -- so a CA that trusts the note and projects `fields=drift_count` reads `None` and mistakes it for
  zero. Read `len(drift)`, never `drift_count`.
- For the three rule files the FILE beats the in-code constant. `_ma_rule_file_text`
  (`code_tools.py:15925-15933`) returns the `examples/` file whenever it is non-empty and falls back
  to the constant only when it is missing, and its docstring says so. So a constant that was already
  corrected proves NOTHING about what ships. Measured 2026-09-20: `_SLIM_RULE_INLINE`
  (`code_tools.py:15912`) already read "agents_registry.json is SOT -- never copy an id list into a
  rule", while `examples/SLIM_RULE_INLINE.txt` still shipped the literal
  `{"agent_id":"<clau|codo|gro|hermes|kilo|ope|pic>"}` to every CA at bootstrap -- naming 2 retired
  ids and omitting 3 live ones, drifting both ways at once. Fix the FILE first; fix the constant too,
  only so the fallback cannot regress.
- That same file is the end-to-end PROOF of the `agent_list` hole. `rule_drift_check` DOES open
  `examples/SLIM_RULE_INLINE.txt`, its regex DOES match the `kilo` and `pic` inside that literal, and
  `checks=agent_list` still answered `drift_count: 0` -- because the config side reads
  `cfg["agents"].keys()` unfiltered and a retired row is kept at `enabled:false`, so the id is still
  a key. A rule file can therefore ship a stale roster while every machine check reports clean.
- NO tool in the surface reads prose in a `.md`/`.txt`, a hardcoded id/path list in live code, or a
  `description` string in `registry_data.py`. That is not a gap to file -- it is WHY classes B and C
  exist, and why the close-out gate below refuses a class-A-only report.

**Close-out gate.** Before reporting a clean sweep, report per class: A = the three tool answers
plus the `generation` they came from; B = which files were grepped and for which id/name/count;
C = which prose files were read, and which mentions were deliberately KEPT as evidence. Name, for
each class, WHICH tool covered it and which blind spot from the Tool Coverage Map you closed by
hand. A sweep that reports only class A is reporting 1 of 3, which is how "drift 0" and "4 drifts
still open" were both true on the same day.
