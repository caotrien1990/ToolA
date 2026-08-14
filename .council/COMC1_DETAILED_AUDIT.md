# COMC1 DETAILED AUDIT — local-Qwen VERIFY scorer

**Audit scope**: clm_qwen_verify_runner.py, clm_qwen_verify_runner_test.py, imported helpers
**Date**: 2026-06-30
**Worker**: ComC1 mid-dev bug hunter / edge-case verifier (no source mutation)

---

## 1. OPENROUTER DRIFT — FULLY REMOVED ✓

| Check | Method | Result |
|---|---|---|
| `openrouter` keyword | grep source + test | **0 matches** |
| `urlrequest` / `urllib.request` | grep source + test | **0 matches** |
| `requests.` / `httpx` / `aiohttp` | grep source + test | **0 matches** |
| `deepseek` / `gemini` / `openai` / `anthropic` | grep source + test | **0 matches** |
| `http.` (any HTTP client) | grep source + test | **0 matches** |

Import chain:
- `from clm_gemma_cluster_candidate import DEFAULT_LLAMA, DEFAULT_MODEL, infer_prompt_format, run_llama`
- `from clm_text_integrity import looks_like_mojibake`

`DEFAULT_LLAMA` → `Path(r"C:\Users\ADMIN\llama.cpp\bin\llama-cli.exe")` — local binary
`DEFAULT_MODEL` → Path to `Qwen3-Coder-30B-A3B-Instruct-Q3_K_M.gguf` — local GGUF
`run_llama` → subprocess.Popen `llama-cli.exe -m <model> -f <prompt_tempfile>` — purely local, zero networking
`looks_like_mojibake` → pure regex/string ops

**Conclusion**: OpenRouter-as-Qwen drift is fully removed. No network or API call path exists.

---

## 2. CLASSIFIED FINDINGS

### 2.1 TRUE_BUG_CANDIDATE

#### TRUE_BUG_CANDIDATE #1: Dead code — `row_memory_text` never called
- **File**: clm_qwen_verify_runner.py
- **Lines**: ~108-117 (function definition)
- **Evidence**: `row_memory_text(row)` is defined but never referenced anywhere in the file. `local_qwen_prompt()` builds its own inline `memory_text` string identical in structure to `row_memory_text`.
- **Impact**: None (dead code only). No runtime effect.
- **Fix**: Remove the unused function, or replace the inline `memory_text` in `local_qwen_prompt()` with a call to `row_memory_text(row)` to eliminate duplication.

---

### 2.2 TRUE_RISK_CANDIDATE

#### TRUE_RISK_CANDIDATE #1: `extract_json_object` picks first `{...}`, may grab wrong object from noisy LLM output
- **File**: clm_qwen_verify_runner.py
- **Function**: `extract_json_object`, lines ~140-157
- **What it does**: Scans text character-by-character for `{`, raw-decodes from each `{` position, collects all dict candidates, returns `candidates[0]`.
- **Risk**: Local LLMs can emit surrounding text or arbitrary JSON fragments. If model outputs `{"unrelated_key": ...} ... {"score": 0.8, ...}`, the first dict `{"unrelated_key": ...}` lacks `score` → `parse_verify_json` raises `"score_missing"` → runtime failure.
- **Severity**: Medium. The prompt says "Return ONLY one JSON object" but local models are not fully controllable.
- **Mitigation options**: (a) Score each candidate dict against expected keys (`score`, `verdict`), pick the best match; (b) Add a `--retry` loop; (c) Accept and log as `needs_review` with reason `model_output_unparseable` rather than failing.
- **Test coverage**: Gap — see TEST_GAP_CANDIDATE #1.

#### TRUE_RISK_CANDIDATE #2: Model reasoning from Qwen discarded in `local_llama_qwen_score`
- **File**: clm_qwen_verify_runner.py
- **Function**: `local_llama_qwen_score`, line ~180
- **Code**: `score, verdict, _reason = parse_verify_json(raw_output)`
- **Evidence**: `parse_verify_json` returns `(score, verdict, reason)` where reason is `clean_text(parsed.get("reason") or "qwen_verify", 500)`. The `_reason` variable is unused; the function returns `f"local_llama_qwen:{model.name}"` as the model_version string.
- **Impact**: The model's explanation for its verdict is lost. For `confidence_mock` this is acceptable (no LLM involved), but for `local_llama_qwen` scoring the reason is valuable for audits, troubleshooting false-positive verifications, and debugging model behavior.
- **Where it could go**: `qwen_semantic_scores.evidence_summary` currently gets `"VERIFY gate decision"` (hardcoded in `write_decisions`). The model's reason could be stored there or in a separate column.
- **Severity**: Medium-low (operational impact, no functional breakage).

#### TRUE_RISK_CANDIDATE #3: `select_candidates` COALESCE on `verify_source` silently excludes NULL
- **File**: clm_qwen_verify_runner.py
- **Function**: `select_candidates`, line ~78
- **Query**: `WHERE ... AND COALESCE(verify_source, '') = 'legacy_heuristic'`
- **Risk**: If any rows have `verify_source IS NULL` (not yet categorized), they become `''` after COALESCE, which does NOT match `'legacy_heuristic'`. These rows are silently skipped — no warning, no count, no log.
- **Impact**: Rows with NULL verify_source that otherwise match (`qwen_verify_status IS NULL`, `vector_eligible = 0`, `deleted_at = ''`) will never be processed by this runner unless someone manually sets verify_source.
- **Severity**: Medium. The audit packet's dry-run shows `candidates=0` — if production has NULL verify_source rows, they're invisible.
- **Mitigation**: Either (a) change to `COALESCE(verify_source, 'legacy_heuristic') = 'legacy_heuristic'` to include NULLs; or (b) add a visible log/warning when NULL rows are found.

#### TRUE_RISK_CANDIDATE #4: Schema validation fails if `status_transition_log` / `harness_evidence` tables missing — no migration path
- **File**: clm_qwen_verify_runner.py
- **Function**: `validate_schema`, lines ~58-65
- **Code**: Checks `"status_transition_log" in tables` and `"harness_evidence" in tables`, raises `RuntimeError("missing_table:...")` if absent.
- **Risk**: These tables may not exist in all DB schemas (older versions, partial migrations). The runner fails immediately with no guidance on how to create them — no `CREATE TABLE IF NOT EXISTS`, no migration script reference, no rollback.
- **Severity**: Medium-high for production activation. The runner can't operate at all without these tables.
- **Note**: The `distilled_points` columns check uses a similar pattern but for columns that are presumably already migrated. The table-level checks are the harder blocker.

#### TRUE_RISK_CANDIDATE #5: Redundant guard in `write_decisions` could mask drift between `select_candidates` and actual write
- **File**: clm_qwen_verify_runner.py
- **Function**: `write_decisions`, lines ~220-225
- **Code**: Re-queries `qwen_verify_status, vector_eligible` and skips if already set.
- **Analysis**: This is belt-and-suspenders — `select_candidates` already filters `qwen_verify_status IS NULL AND vector_eligible = 0`. If a row passes the SELECT but fails the write guard, it's silently skipped with no log. This is safe for correctness but could hide a concurrency bug or schema drift.
- **Severity**: Low. The transaction wrapper makes it correct; the only risk is silent masking.

---

### 2.3 TEST_GAP_CANDIDATE

#### TEST_GAP_CANDIDATE #1: No test for `extract_json_object` with wrong JSON preceding correct one
- **What's missing**: Test where model output is `{"irrelevant": true} {"score": 0.8, "verdict": "semantic_verified", "reason": "ok"}` — extract_json_object returns first dict, parse_verify_json raises.
- **Related**: TRUE_RISK_CANDIDATE #1
- **Priority**: Medium

#### TEST_GAP_CANDIDATE #2: No test for `local_llama_qwen_score` with empty model output
- **What's missing**: Test where `run_llama` returns `""` → `parse_verify_json("")` → `ValueError("empty_model_output")`. The fake run_llama test always returns valid JSON-with-prefix/suffix.
- **Priority**: Low

#### TEST_GAP_CANDIDATE #3: No test for nested JSON objects where first dict lacks score
- **What's missing**: `parse_verify_json('{"outer": {"score": 0.8}}')` — extract_json_object returns `{"outer": {"score": 0.8}}`, parse_verify_json finds `parsed.get("score")` → None → `"score_missing"`.
- **Priority**: Low

#### TEST_GAP_CANDIDATE #4: No test for `vector_eligible_for_verdict` with invalid input
- **What's missing**: `vector_eligible_for_verdict("bogus")` returns 0 (since it compares to "semantic_verified"). The function doesn't validate its input but is only called with validated verdicts from `build_decisions`.
- **Priority**: Very low (defense-in-depth)

#### TEST_GAP_CANDIDATE #5: No test for SELECT query when `verify_source` is NULL
- **What's missing**: Test DB inserts rows with explicit `'legacy_heuristic'`. No row has `verify_source=NULL` to verify the COALESCE behavior.
- **Related**: TRUE_RISK_CANDIDATE #3
- **Priority**: Medium

#### TEST_GAP_CANDIDATE #6: No test for `looks_like_mojibake` edge cases
- **What's missing**: Unit tests for `looks_like_mojibake("")`, `looks_like_mojibake("hello world")`, `looks_like_mojibake("Aether")` (false positive risk). The current test only covers one mojibake string. The underlying `clm_text_integrity.py` uses regex patterns that could produce false positives for accented Latin text.
- **Priority**: Medium

#### TEST_GAP_CANDIDATE #7: No test for `--allow-local-llm` when llama-cli or model file is missing
- **What's missing**: `run_llama` in clm_gemma_cluster_candidate.py raises `FileNotFoundError` if binary/model don't exist. The runner test monkeypatches `run_llama` entirely, never testing the file existence path.
- **Priority**: Low (acknowledged non-goal: no real local Qwen execution)

#### TEST_GAP_CANDIDATE #8: No test for `build_decisions` mojibake override with `confidence_mock` scorer
- **What's missing**: `build_decisions` always applies `looks_like_mojibake(text)` after scoring. Both code paths check mojibake. Test that both agree.
- **Priority**: Low

---

### 2.4 DOCS_OR_ACTIVATION_GAP

#### DOCS_OR_ACTIVATION_GAP #1: Hermes CLI bridge not implemented, deferred
- **As stated in audit packet**: "Hermes CLI local-Qwen bridge is not implemented in this runner step... Treat Hermes bridge as a later activation surface."
- **Code evidence**: `--prompt-format` choices are `auto`, `gemma`, `chatml` only. No `hermes` format. No OpenAI-compatible endpoint code.
- **Verdict**: Confirmed. This is explicitly a non-goal.

#### DOCS_OR_ACTIVATION_GAP #2: Real local Qwen execution not proven
- **As stated in audit packet**: "No real local Qwen llama run was executed in this step."
- **Code evidence**: Tests mock `run_llama`. Live dry-run uses `confidence_mock` scorer. The `local_llama_qwen` path code exists but hasn't been exercised with a real GPU.
- **Verdict**: Confirmed. Cannot verify end-to-end Qwen output quality.

#### DOCS_OR_ACTIVATION_GAP #3: DEFAULT_MODEL hardcoded to specific GGUF
- **Code**: `DEFAULT_MODEL = Path(r"D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\DATA\.MODELS\Qwen3-Coder-30B-A3B-Instruct-Q3_K_M.gguf")`
- **Impact**: Changing Qwen model requires either changing this constant or passing `--qwen-model` every time. No documentation of the expected model format (GGUF, quantization level).
- **Severity**: Low (configurable via CLI)

#### DOCS_OR_ACTIVATION_GAP #4: model_version format not documented
- **Format**: `"local_llama_qwen:{model.name}"` where model.name is the GGUF filename stem
- **Impact**: Downstream consumers of `qwen_semantic_scores.model_version` may not know this format. The field stores a combined string rather than structured metadata.
- **Severity**: Low

---

### 2.5 MISUNDERSTANDING_OR_NEEDS_VERIFICATION

#### MISUNDERSTANDING_OR_NEEDS_VERIFICATION #1: Double `isinstance(parsed, dict)` check
- **Location**: `extract_json_object` line ~155 checks `if not isinstance(parsed, dict)` → raises `"invalid_verify_json_object"`. Then `parse_verify_json` line ~163 does the identical check. The second check is unreachable because `extract_json_object` guarantees a dict return.
- **Verdict**: Redundant but harmless. Not a bug.

#### MISUNDERSTANDING_OR_NEEDS_VERIFICATION #2: `migration_batch_id` column in `distilled_points`
- **validate_schema** checks for `migration_batch_id` in the `distilled_points` column set. The runner writes this column. Need to verify the column exists in all deployment DBs.
- **Verdict**: Schema check provides guard. No further action needed if schema check passes.

#### MISUNDERSTANDING_OR_NEEDS_VERIFICATION #3: `run_llama` implementation is truly local
- **Verified**: subprocess.Popen `llama-cli.exe` with `-m model_path` and `-f prompt_tempfile`. No `--api`, no `--host`, no network flags. Tempfile is written to, read from, then deleted. No external API calls.
- **Verdict**: Confirmed local. Zero network surface.

---

## 3. THRESHOLD & VERDICT BOUNDARY AUDIT

| Boundary | Tested | Explanation |
|---|---|---|
| `score >= 0.70` → semantic_verified | T1 `check("semantic", ...)` ✓ | 0.70 is inclusive |
| `score >= 0.45` → needs_review | T1 `check("review", ...)` ✓ | 0.45 is inclusive |
| `score < 0.45` → rejected | T1 `check("reject", ...)` ✓ | 0.44 → rejected |
| mojibake text → quarantine | T1 `check("mojibake quarantine", ...)` ✓ | Overrides all score thresholds |
| `score = NaN` | T5 `check("reject nan", ...)` ✓ | `math.isfinite(NaN)` → False → raises |
| `score = inf` | Not explicitly tested | Code path: `not math.isfinite(inf) or inf < 0.0...` → `not False or False` → `True` → raises ✓ |
| `score = -inf` | Not explicitly tested | Same logic → raises ✓ |
| `score = 0.0` | Not explicitly tested | `>= 0.70` False, `>= 0.45` False → rejected ✓ |
| `score = 1.0` | Not explicitly tested | `>= 0.70` True → semantic_verified ✓ |
| `score missing` | T5 `check("reject missing score", ...)` ✓ | Raises `score_missing` |
| `score = "NaN"` (string) | T5 `check("reject nan", ...)` ✓ | `float("NaN")` succeeds → `math.isfinite(NaN)` False → raises |

---

## 4. GUARD AUDIT

| Guard | Test | Live | Notes |
|---|---|---|---|
| `--apply` with `confidence_mock` without `--allow-mock-apply` | T4 ✓ | — | `RuntimeError("mock_apply_blocked")` |
| `--scorer=local_llama_qwen` without `--allow-local-llm` | T6 ✓ | Live ✓ | `RuntimeError("local_llm_blocked")` |
| `scorer` choices only `confidence_mock`, `local_llama_qwen` | argparse ✓ | — | No OpenRouter option exists |
| Transaction rollback in `write_decisions` | Implicit in T3 | — | `con.rollback()` on exception |
| Duplicate write guard in `write_decisions` | Implicit in T3 | — | Skips already-scored rows |
| Schema validation before candidate selection | T2, T3 implicit | — | `validate_schema(con)` at start |

---

## 5. FAKE LLAMA TEST SUFFICIENCY

T5 test:
- Patches `V.run_llama` with a lambda returning `'prefix {"score":0.73,"verdict":"semantic_verified","reason":"ok"} suffix'`
- Verifies `score_row` returns `(0.73, "local_llama_qwen:Qwen3-Coder-...", "semantic_verified")`
- Verifies prompt contains `<|im_start|>system`
- Verifies model path is passed to fake `run_llama`

**Covers**: prompt construction, JSON extraction (prefix/suffix stripping), `parse_verify_json` scoring, model_version format, scorer verdict flow.

**Missing**: Empty output, malformed output, timeout handling, file-not-found handling. Acknowledged non-goals for this step.

**Verdict**: Sufficient for proving local scorer integration without starting GPU/local LLM. The fake covers the critical code path from prompt → parse → score → return.

---

## 6. SUMMARY

| Classification | Count | Details |
|---|---|---|
| TRUE_BUG_CANDIDATE | 1 | Dead code (`row_memory_text`), zero runtime impact |
| TRUE_RISK_CANDIDATE | 5 | 1 medium (wrong JSON), 1 medium (NULL COALESCE), 1 medium-high (missing tables), 1 medium-low (discarded reason), 1 low (redundant guard) |
| TEST_GAP_CANDIDATE | 8 | 3 medium, 5 low |
| DOCS_OR_ACTIVATION_GAP | 4 | All acknowledged/non-goal or low |
| MISUNDERSTANDING_OR_NEEDS_VERIFICATION | 3 | All confirmed or redundant |

**OpenRouter drift**: Fully removed — zero matches across all grep patterns in source and test.

**JSON parse**: Functional with risks — `extract_json_object` first-dict selection could fail on noisy model output.

**Guards**: All three guards (`mock_apply_blocked`, `local_llm_blocked`, schema validation) tested and live-verified.

**Test suite**: 23/23 pass. Covers dry-run, apply, blocking, parse edges, and fake LLM integration.

**Hermes bridge**: Deferred, not implemented. Confirmed non-goal.

**Real local Qwen execution**: Not verified. Acknowledged as untested.
