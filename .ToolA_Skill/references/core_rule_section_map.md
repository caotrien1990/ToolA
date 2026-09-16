# CORE_RULE.txt Section Mapping — R1 Sprint 1.5

**HISTORICAL — R1 Sprint 1.5 planning snapshot. Do NOT read this page as a description of the current file.** It was written against a version of `examples/CORE_RULE.txt` that no longer exists, so every line number, line count and byte figure below is provenance only, not fact about today's rule file. For the live shape call `read_file_range` on `examples/CORE_RULE.txt` and use its `total_lines`; never quote a size from this page. Kept because the classification reasoning (what belongs in super-core vs skill vs reference) is still the design intent.

Target: super-core ≤ 4 KB (~70 lines). Migrate remainder to skills/references.

## Classification

| # | Section | Lines | Bytes (est) | Keep in Super-Core | Move to Skill | Move to Reference |
|---|---------|-------|------|------|------|------|
| ENV | ENV preamble | 1-7 | 350 | ✅ Keep (Windows constraints) | | |
| 0a | DEFINITIONS (new) | 11-40 | 1100 | ✅ Keep (identifier system) | | |
| 0a.1 | BOOTSTRAP RULE SOURCE | 41-48 | 600 | ✅ Keep (rule loading) | | |
| 0b | HEALTH CHECK | 50-59 | 950 | ✅ Keep (health command) | | |
| 0c | DEFAULT MODE | 62-72 | 600 | ✅ Keep (mode rule) | | |
| 0d | EXECUTE MODE | 74-88 | 1100 | ✅ Keep (trigger rule) | `02_mode_control/` (workflow details) | |
| 0e | STRICT API BOUNDARY | 90-97 | 850 | ✅ Keep (HARD safety) | | |
| 0f | PLATFORM TOOL BOUNDARY | 99-101 | 500 | ✅ Keep (HARD safety) | | |
| PI:1 | Orchestrator-first | 125-128 | 200 | ✅ Keep (1-line summary) | | |
| PI:2 | Session lifecycle | 130-138 | 700 | | `03_path_session/` | |
| PI:2b | Auto-register paths | 139-143 | 400 | ✅ Keep 1-line summary | `03_path_session/` (detail) | |
| PI:3 | Tool tiers / mode gate | 145-150 | 500 | ✅ Keep 1-line | `04_tool_tiers/` | |
| PI:3b | Repo vs Runtime | 152-155 | 350 | ✅ Keep 1-line | `11_repo_vs_runtime/` | |
| PI:4 | Token-protected tools | 157-163 | 700 | | `06_token_auth/` | |
| PI:5 | Path semantics | 165-172 | 850 | | `03_path_session/` | |
| PI:6 | Write/dry-run rule | 174-181 | 1000 | ✅ 1-line "follow write_pipeline skill" | `05_write_pipeline/` | |
| PI:6b | Backup threshold | 183-187 | 500 | | `05_write_pipeline/` | |
| PI:7 | Command rules (Windows) | 189-203 | 1500 | | `08_command_windows/` | |
| PI:7b | CLEANUP | 205-207 | 200 | ✅ Keep 1-line | | |
| PI:8 | Test profile rules | 209-211 | 400 | | `09_testing_validation/` | |
| PI:9 | Native fallback audit | 213-220 | 900 | ✅ Keep critical 1-line | `12_native_audit/` | |
| PI:10 | Error/retry guidance | 222-227 | 600 | | `07_error_schema_hints/` | |
| PI:11 | Live schema helpers | 229-235 | 700 | | `07_error_schema_hints/` | |
| PI:12 | Token Panel | 237-242 | 500 | | `06_token_auth/` | |
| PI:13 | Adaptive / Coverage | 244-246 | 250 | | `10_coverage_report/` | |
| PI:14 | Report contract | 248-250 | 200 | | `10_coverage_report/` | |
| PI:15 | Post-write checklist | 252-255 | 350 | ✅ Keep 1-line | `05_write_pipeline/` (full) | |
| --- | PowerShell API Pattern | 257-260 | 200 | | | `references/api_examples.md` |
| RC | RESPONSE CONTRACT | 262-266 | 700 | ✅ Keep (4-line summary) | | |

## Estimated super-core after migration

Keep:
- ENV: 350
- 0a–0f: ~5500 bytes (definitions + bootstrap + health + mode + API boundary)
- PI:1, PI:2b summary, PI:3 summary, PI:3b summary, PI:6 summary, PI:7b, PI:9 summary, PI:15 summary: ~1500 bytes
- Priority block (new): 200
- Skill routing block (new): 300
- RC: 700

**Total estimate: ~8.5 KB** — over the 4 KB target.

### Aggressive cut plan (to hit 4 KB)

1. Replace prose with **decision table** (saves 30%)
2. Compress 0a definitions into **single mini-block** (saves 0.8 KB)
3. PI:1-15 → **collapse into 1 routing table** mapping topic → skill (saves 2 KB)
4. Examples in 0b/0e move to `references/bootstrap_examples.md` (saves 0.5 KB)

After aggressive cut: ~4.0 KB ✅

## Migration sequence (R3)

1. Build all skill stubs first (folder + SKILL.md placeholders with frontmatter)
2. Move PI sections content into skill bodies
3. Replace PI sections in CORE_RULE with 1-line summary + skill link
4. Final compress + size check
5. Backend `recommended_skill` field (Round 6)

## Pass criteria check (R1 S1.5)

- ✅ All 30 sections classified
- ✅ Aggressive cut path to ≤ 4 KB identified
- ✅ No section left unclassified
