# RES: HermC5_R3 — A+Q Upgrade Brainstorm Loop Round 3 Plan

## Task

HermC5 (GLM 5.2) Round 3: đọc peer plans (HermC7, ComC3, KiloC5 từ round 2) + helper evidence (HermC1, OpeC1 từ round 1), bootstrap orchestrator, viết tệp plan cho round 3.

## Deliverable

| Item | Path | Status |
|------|------|--------|
| Round 3 plan | D:\BR\A_Q_plan\round3\PLAN_hermc5.md (36,458 bytes) | **DONE** |

## Context inputs consumed

| Source | Path | Round |
|--------|------|-------|
| Peer plan HermC7 | D:\BR\A_Q_plan\round2\PLAN_hermc7.md | R2 |
| Peer plan ComC3 | D:\BR\A_Q_plan\round2\PLAN_comc3.md | R2 |
| Peer plan KiloC5 | D:\BR\A_Q_plan\round2\PLAN_kiloc5.md | R2 |
| Helper evidence HermC1 | D:\BR\A_Q_plan\round1\HELPER_hermc1.md | R1 |
| Helper evidence OpeC1 | D:\BR\A_Q_plan\round1\HELPER_opec1.md | R1 |
| Orchestrator bootstrap | POST /api/call/prepare_ca_session → T1 mode | session |

## Peer scoring (3 peers)

| Agent | Code opt | Sys complete | Release | Feasibility | Evidence | Total/100 |
|-------|----------|-------------|---------|-------------|----------|-----------|
| HermC7 | 8 | 9 | 8 | 7 | 8 | 80.0 |
| KiloC5 | 8 | 8 | 8 | 8 | 7 | 77.5 |
| ComC3 | 6 | 7 | 6 | 5 | 5 | 58.5 |

## Stolen strengths integrated (vs R2 changes)

1. From KiloC5: Dependency-ordered R3 strangler — extract theo import fan-in ranking (config 9 → path_runtime 6 → orchestrator_server 6 → code_tools 12 hub). Low-fan-in trước, high-fan-in cuối. (vs R2: registry-first → fan-in ordered)
2. From KiloC5: Q vector retire 4-table drop (v1/v2/backup_v1/backup_v2). (vs R2: 3 → 4 tables)
3. From KiloC5: Q vector retire song song Phase 1 (LOW risk, backup → disable step 5 → drop). (vs R2: Phase 3.5 → Phase 1)
4. From OpeC5 (R2 ref): SEARCH_TOOL mode= param + alias contract backward-compat test. (vs R2: thêm alias migration test)
5. From HermC7: Acceptance criteria thêm owner column (measure + target + owner). (vs R2: chỉ measure+target)
6. From HermC7: Risk matrix giữ likelihood column. Dead code batch 20. SOT mapping thêm CORE_RULE.txt + SKILL_council_brainstorm_loop.md.
7. From ComC3: interfaces.py break circular dep (session 5 theo fan-in). Extract TOOL_MAP → registry.py (session 2 theo fan-in).
8. From HermC1 helper: graph fresh 85/85, stale_files=0, integrity ok. cbm_orphan_symbols/impact_scope = template trong cbm_template_query (doc fix).
9. From OpeC1 helper: CLM_ env vars 14 unique trong source (không 17). QSLEEP_ = runner-side không API-side. GRAPH_ = 6 vars. CLM_BM25_WEIGHTED có prefix, bare BM25_WEIGHTED không tồn tại.

## Rejected weaknesses

1. ComC3 Phase 1/2 ngoài MASTER_MAP scope (transport MCP vs HTTP, Q integration depth, rule sync push/pull) — reject, task yêu cầu plan dựa trên 23 candidates.
2. HermC7 R3 strangler thiếu domain extract order — reject, thay bằng dependency-ordered (KiloC5).
3. ComC3 meta-comment "gợi ý cho Round 2" — reject, không phải plan content.
4. KiloC5 thiếu cite helper corrections — R3 tích hợp CLM_ 14 + QSLEEP_ runner-side.

## Plan structure (3 phases + 3 pillars)

- Phase 1: Cleanup nhanh + Q vector retire (12 tasks, LOW risk, 1-2 session)
- Phase 2: Aspirational decisions (3 decisions cần sign-off Anh)
- Phase 3: Refactor lớn (7 tasks, HIGH risk, multi-session, dependency-ordered)
- Pillar 1: Code optimization (A: 8 actions, Q: 7 actions, expected impact 13 metrics)
- Pillar 2: Rule/skill/hint/schema completeness (A: 10, Q: 5, SOT mapping 17 entries)
- Pillar 3: Release readiness (10 tests, 8 docs, 6 reliability, 5 reproducibility, 8 CI items)
- Risk matrix: 14 risks với severity + likelihood + mitigation + owner
- Acceptance criteria: 28 criteria với measure + owner
- Residual unknowns: 17 items

## Verification

- Plan file exists: D:\BR\A_Q_plan\round3\PLAN_hermc5.md — 36,458 bytes confirmed
- Bootstrap: prepare_ca_session T1 mode — ok:true confirmed
- All 5 context inputs consumed (3 peer plans R2 + 2 helper evidence R1)
- Peer scoring completed with 5 criteria × 0.25/0.25/0.25/0.15/0.10 weights
- Stolen strengths vs R2 changes documented per item
- RES block appended to outbox (this file)

END_HERMC5_R3
