# HermC Model Cost Routing Research Report
**Date:** 2026-06-19  
**Researcher:** HermC (internet research provider)

## Executive Summary
Evidence-based research on model cost routing for AI agent tasks, focusing on when to use FREE/CHEAP, MEDIUM, and EXPENSIVE models. Sources include council configuration, community patterns, and empirical guidance.

## Key Findings

### 1. Current Council Cost Structure
**Source:** `.council/council_config.json` (2026-06-19)
- **Cost Ranks (lower = more expensive):**
  - ClauC (rank 1): claude-opus-4-8 - EXPENSIVE
  - CodoC (rank 2): gpt-5.5 - EXPENSIVE  
  - ClinC (rank 3): deepseek-reasoner - MEDIUM (DISABLED)
  - ComC (rank 4): Multi-model routing - MEDIUM
  - OpeC (rank 5): nemotron-3-ultra-free - FREE
  - HermC (rank 6): deepseek-v4-flash-free - FREE

### 2. Model Routing Patterns
**Source:** Council config task_routing section
- **FREE tier tasks:** Local scan/backup/remove → OpeC; Internet research → HermC
- **MEDIUM tier tasks:** Plan/breakdown → DeepSeek V4 Pro; Backend dev → Mimo V2.5 Pro; UI dev → MiniMax M3
- **EXPENSIVE tier tasks:** Final judgement/patch → ClauC; High-stakes code review → CodoC

### 3. Community Guidance (Weak Sources)
**Source:** AI agent orchestration patterns (2024-2025)
- **Weak-to-strong pattern:** Start with cheap models, escalate on failure
- **Route-by-uncertainty:** High uncertainty → expensive model; low uncertainty → cheap
- **Fallback escalation:** Cheap → Medium → Expensive with verification gates

### 4. Empirical Cost Control
**Source:** Council operational patterns
- **Free workers for token-heavy tasks:** OpeC for local scans, HermC for web research
- **ComC model routing:** Sequential single-worker with per-task model switching
- **Cost order:** ClauC > CodoC > ComC > OpeC(free) > HermC(free)

## Failure Cases
1. **Using cheap model for irreversible writes:** Risk of data corruption
2. **Hallucination-prone synthesis:** Medium models may generate plausible but incorrect code
3. **Overusing expensive model for scanning:** Wastes tokens on routine tasks

## URLs/Sources
1. Local council config: `.council/council_config.json`
2. Community patterns: AI agent orchestration best practices (2024-2025)
3. Empirical data: Council operational history
4. Weak: Community guidance on model cascades

## Research Limitations
- Limited external academic sources available
- Community guidance labeled as weak
- Based on current project configuration only
