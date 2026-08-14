# Karpathy → [A] Mapping Report
# 2026-06-25 | Ope (leader)

## Nguồn: `multica-ai/andrej-karpathy-skills/CLAUDE.md`
- 4 nguyên tắc, 65 dòng, bias caution over speed

---

## BẢNG MAP TỔNG QUAN

| # | Karpathy | [A] Rule hiện có | Status | Gap |
|---|---|---|---|---|
| 1 | Think Before Coding | 7-RULE #1 Think+Read | ✅ Overlap | Thiếu HARD-GATE bắt buộc "stop → name confusion → ask" trước EXECUTE |
| 2 | Simplicity First | 7-RULE #2 Minimum footprint | ✅ Overlap | Thiếu heuristic cụ thể "senior engineer sniff test" |
| 3 | Surgical Changes | 7-RULE #2 Preserves | ⚠️ Partial | Thiếu traceability test "mỗi dòng đổi trace về user request" |
| 4 | Goal-Driven Execution | 7-RULE #3 Goal loop | ⚠️ Partial | Thiếu template bắt buộc "Step → verify: check" cho multi-step |

---

## CHI TIẾT TỪNG NGUYÊN TẮC

### 1. Think Before Coding
**Karpathy:**
- State assumptions explicitly
- If multiple interpretations, present them
- If unclear, STOP. Name what's confusing. ASK

**[A] hiện có:**
- 7-RULE #1: "Think + Read first: State assumptions. Ask > guess."
- leader-council-skill: Worker Prompt Quality Gate có "stop and report" conditions
- council_rule_leader.md: "Evidence and verdict discipline" có "unknown/not verified"

**Gap:**
- Karpathy: HARD-GATE bắt buộc STOP + ASK trước mọi EXECUTE
- [A]: Rule #1 là advisory, không có blocking gate
- → Cần `clarification_gate.md`

### 2. Simplicity First
**Karpathy:**
- Minimum code solving the problem
- No features beyond what was asked
- No abstractions for single-use code
- "Would a senior engineer say this is overcomplicated?"

**[A] hiện có:**
- 7-RULE #2: "Minimum footprint: Least code solving the problem. No extras/speculation/adjacent-cleanup/unasked-abstractions."
- rune-preflight Step 4 (Completeness Check)

**Gap:**
- Karpathy: Heuristic cụ thể "senior engineer sniff test" — check是否overcomplicated
- [A]: #2 là general principle, không có concrete heuristic
- → Cần `senior_sniff_test.md`

### 3. Surgical Changes
**Karpathy:**
- Touch only what you must
- Don't improve adjacent code
- Match existing style
- Every changed line should trace directly to the user's request

**[A] hiện có:**
- 7-RULE #2: "Preserve existing behavior + contracts"
- rune-scope-guard: drift detection (ON_TRACK/MINOR_DRIFT/SIGNIFICANT_DRIFT/OUT_OF_CONTROL)
- rune-preflight Stage A: spec compliance (plan vs diff)

**Gap:**
- Karpathy: "Every changed line should trace directly to the user's request" = concrete traceability test
- [A]: scope-guard check file-level drift, preflight check plan-vs-diff, nhưng KHÔNG có line-level traceability
- → Cần `traceability_rule.md`

### 4. Goal-Driven Execution
**Karpathy:**
- Define success criteria first
- Loop until verified
- Template: `1. [Step] → verify: [check]`

**[A] hiện có:**
- 7-RULE #3: "Goal loop: Define success criteria first; loop until verified — don't follow steps."
- leader-council-skill: Worker Prompt Quality Gate có "acceptance criteria / tests"
- council_rule_leader.md: "Mini task brief" có "Target state: what done means"

**Gap:**
- Karpathy: Template cụ thể bắt buộc "Step → verify: check" cho mỗi multi-step task
- [A]: Goal loop là general, worker prompt có acceptance criteria nhưng không có template bắt buộc
- → Cần `goal_template.md`

---

##映射 VỚI RUNE SKILLS

| Rune Skill |映射 Karpathy | Gap |
|---|---|---|
| rune-preflight (426 dòng) | #1 Think + #4 Goal (Stage A spec compliance) | Thiếu: clarification_gate, senior_sniff_test |
| rune-scope-guard (192 dòng) | #3 Surgical (drift detection) | Thiếu: line-level traceability |
| rune-constraint-check (176 dòng) | Meta-validator cho HARD-GATEs | Có thể dùng để enforce Karpathy gates |
| rune-skill-router (485 dòng) | #1 Think (anti-rationalization gate) | Thiếu: explicit "stop and ask" |

---

## ĐÁNH GIÁ ƯU TIÊN

| Patch | Ưu tiên | Lý do |
|---|---|---|
| 00_clarification_gate.md | CAO | Prevents wrong execution — most costly error |
| 01_senior_sniff_test.md | TRUNG BÌNH | Quick heuristic, low effort, high value |
| 02_traceability_rule.md | CAO | Catches scope creep early |
| 03_goal_template.md | TRUNG BÌNH | Improves multi-step quality |

---

## KẾT LUẬN

4 nguyên tắc Karpathy đều đã có根基 trong 7-RULE của [A], nhưng thiếu 4 patch cụ thể:
1. HARD-GATE clarification (stop → ask before EXECUTE)
2. Senior engineer sniff test (overcomplicated check)
3. Line-level traceability (trace to user request)
4. Mandatory step→verify template (multi-step)

Không có conflict — tất cả là bổ sung, không cần rewrite.
