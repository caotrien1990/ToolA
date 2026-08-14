# [A] Skill Library — V15 Architecture

Active skill library for `[A]`. Cấu trúc theo TIEU CHUAN HE THONG SKILL AI v15 + Anthropic/OpenAI standards.

## Folder structure

```
.ToolA_Skill/
  README.md              ← this file (overview)
  SKILLS.md              ← Tầng 2 router: maps task/error → skill (metadata only)
  skills/                ← Tầng 3: each skill = folder with SKILL.md + frontmatter
    <NN_skill_name>/
      SKILL.md           ← entry point (YAML frontmatter: name, description, trigger)
      references/        ← knowledge/tables loaded on demand
      scripts/           ← deterministic executables
      assets/            ← images, diagrams
  references/            ← shared cross-skill references (e.g., tool_inventory_v15.csv)
  scripts/               ← shared scripts
  assets/                ← shared assets
  harness/               ← skill loop test matrix + results
```

## Tier layering (current alignment)

| Tier | Lives in | Load policy |
|------|----------|-------------|
| Super Core (Tầng 1) | `examples/CORE_RULE.txt` (outside this folder) | Always-on. ≤ 5% of total content. |
| Skill Router (Tầng 2) | `SKILLS.md` here | Metadata only. ≥ 95% routing accuracy. |
| Skill Con (Tầng 3) | `skills/<name>/SKILL.md` | Loaded on YAML frontmatter trigger match. |
| Scripts | `skills/<name>/scripts/` or `scripts/` | Executed, not loaded into context. |
| References | `skills/<name>/references/` or `references/` | On-demand only. |

## Skill activation rule (Progressive Disclosure)

1. CA reads `CORE_RULE.txt` (super core) on session start.
2. For non-trivial task → CA opens `SKILLS.md` (router only).
3. Router maps task type → recommended skill folder.
4. CA reads `skills/<name>/SKILL.md`.
5. Sub-references loaded only when SKILL.md explicitly cites them.
6. Skill `< 500` lines mandate; longer content goes to `references/`.

## YAML frontmatter contract (skill triggering)

Each `SKILL.md` MUST start with:

```yaml
---
name: skill_name
description: |
  One-paragraph trigger description. When CA reads SKILLS.md router,
  this description determines auto-activation.
trigger:
  - task_pattern: "..."
  - error_name: "..."
  - tool: "..."
preconditions:
  - "..."
related_skills:
  - other_skill_name
risk_level: low | medium | high
---
```

## Versioning

- V15 R1 — folder created, structure ready, skills not yet migrated
- V15 R2+ — skills built progressively

Backup of legacy `.md` files lives outside `[A:P]` in `V16+ Research` folders.
