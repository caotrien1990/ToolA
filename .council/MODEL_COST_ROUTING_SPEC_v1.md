# MODEL_COST_ROUTING_SPEC_v1.md
**Version:** 1.0  
**Date:** 2026-06-19  
**Purpose:** Evidence-based spec for routing AI agent tasks to model cost tiers

## 1. Cost Tier Definitions

### FREE/CHEAP Tier
**Cost Rank:** 5-6 (Free workers)
**Models:** deepseek-v4-flash-free, nemotron-3-ultra-free
**Use Cases:** Token-heavy, low-risk, reversible tasks

### MEDIUM Tier  
**Cost Rank:** 3-4 (ComC multi-model)
**Models:** DeepSeek V4 Pro, MiniMax M3, Mimo V2.5 Pro
**Use Cases:** Code generation, synthesis, medium-risk tasks

### EXPENSIVE Tier
**Cost Rank:** 1-2 (ClauC, CodoC)
**Models:** claude-opus-4-8, gpt-5.5
**Use Cases:** Final judgement, high-stakes code, irreversible writes

## 2. Tier Specifications

### FREE/CHEAP Tier
- **Task Types:** Local scan, backup, remove, internet research, data gathering
- **Examples:** Skill sync, backup/remove 24_council_tr, D:/BR project cleanup
- **Anti-examples:** Final code review, security audit, irreversible writes
- **Required Inputs:** Clear task scope, bounded search criteria
- **Allowed Outputs:** Read-only reports, bounded data summaries
- **Escalation Triggers:** Task requires write access, high-stakes decision
- **Max Context:** Unlimited (free tokens)
- **Verification Required:** None (low-risk tasks)

### MEDIUM Tier
- **Task Types:** Code generation, synthesis, planning, edge-case hunting
- **Examples:** Q gatekeeper patch, 2-round brainstorm, model speculation
- **Anti-examples:** Final security review, production deployment
- **Required Inputs:** Detailed requirements, acceptance criteria
- **Allowed Outputs:** Code drafts, synthesis reports, test cases
- **Escalation Triggers:** High uncertainty, security concerns, user request
- **Max Context:** Task-dependent (typically 10-50k tokens)
- **Verification Required:** Leader review, automated tests

### EXPENSIVE Tier
- **Task Types:** Final judgement, high-stakes code review, security audit
- **Examples:** [A] self-upgrade, final patch review, production deployment
- **Anti-examples:** Routine scanning, data gathering, backup tasks
- **Required Inputs:** Complete context, risk assessment, user authorization
- **Allowed Outputs:** Final code, security verdicts, deployment commands
- **Escalation Triggers:** None (user-requested or risk-justified only)
- **Max Context:** Task-dependent (typically 50-200k tokens)
- **Verification Required:** Multi-stage review, automated testing, user approval

## 3. Council Worker Mapping

### OpeC/HermC = FREE/Heavy Scan/Search
- **When:** Task fits local scan (OpeC) or internet research (HermC)
- **Examples:** Skill sync, backup/remove, internet docs fetch
- **Never:** Cleanup/backup/remove/scan routine → NOT ClauC

### ComC = MEDIUM/Specialized
- **Models:** DeepSeek V4 Pro (plan/edge/test), MiniMax M3 (UI), Mimo V2.5 Pro (backend)
- **When:** Code generation, synthesis, medium-risk tasks
- **Examples:** Q gatekeeper patch, visible CLI test, memory write

### ClauC/CodoC = EXPENSIVE/Special
- **When:** User-requested, T4/stateful MCP, high-stakes final judgement
- **Examples:** [A] self-upgrade, final security review
- **Never:** Cleanup/backup/remove/scan routine

## 4. Decision Tree for Leaders

```
START
├─ Is task reversible/low-risk?
│  ├─ YES → Use FREE tier (OpeC/HermC)
│  └─ NO → Is task code generation/synthesis?
│     ├─ YES → Use MEDIUM tier (ComC with appropriate model)
│     └─ NO → Is task high-stakes/final judgement?
│        ├─ YES → Use EXPENSIVE tier (ClauC/CodoC)
│        └─ NO → Use MEDIUM tier with verification
└─ END
```

## 5. Hard Gates
1. **Local scan/backup/remove** → OpeC/default/free, NOT ClauC
2. **Internet research** → HermC
3. **Model speculation** → MEDIUM tier
4. **Final high-risk patch review** → EXPENSIVE only if user requested or risk justifies
5. **[A] self-upgrade** → EXPENSIVE tier with full verification

## 6. Concrete Examples from Recent Work

1. **Skill sync** → FREE (OpeC/HermC) - routine data gathering
2. **Backup/remove 24_council_tr** → FREE (OpeC) - local file operations
3. **Q gatekeeper patch** → MEDIUM (ComC/DeepSeek V4 Pro) - code generation
4. **Visible CLI test** → MEDIUM (ComC/MiniMax M3) - UI testing
5. **Memory write** → MEDIUM (ComC) - data synthesis
6. **D:/BR project cleanup** → FREE (OpeC) - local file operations
7. **2-round brainstorm** → MEDIUM (ComC) - synthesis task
8. **Internet docs fetch** → FREE (HermC) - web research
9. **Raw latest responses query** → FREE (OpeC/HermC) - data gathering
10. **[A] self-upgrade** → EXPENSIVE (ClauC) - high-stakes code change

## 7. Implementation Notes
- **Model names are current config examples** - update as models change
- **Worker routing is config-driven** - use council_config.json/task_routing
- **ComC is single sequential worker** - model routing changes per task
- **Free workers for token-heavy tasks** - optimize cost first
- **Verification required for MEDIUM/EXPENSIVE** - no unverified code

## 8. Sources
1. Local council config: `.council/council_config.json`
2. Community patterns: AI agent orchestration best practices (2024-2025)
3. Empirical data: Council operational history
4. Weak: Community guidance on model cascades

**End of Spec**
