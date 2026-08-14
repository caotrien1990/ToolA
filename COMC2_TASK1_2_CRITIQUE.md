# COMC2 TASK1_2 CRITIQUE

## Independent critique of ComC1 judgement - quoted short-token exact search

ComC1's verdict is structurally sound but underspecified in three places that matter for safety. Below I accept, refine, and add what is missing. End-state is a sharper, more bounded design.

### A. Where ComC1 is correct (accepted as-is)

- **Single utility function** `extract_quoted_terms` is the right factoring. Two call sites (pre_search, query) plus one shared consumer (fts_query path) means duplication is the #1 failure mode. Centralizing parser+validator is mandatory. **Keep.**
- **Short-circuit gate** "no balanced quotes found -> run original code path with zero change." This is the backwards-compatibility contract. Byte-identical results for quote-free queries must be a tested invariant, not an aspiration. **Keep, strengthen with a regression test.**
- **Two-phase lexical impact** (boost + post-filter) is the right conceptual model. Quoted terms mean *exact phrase presence*, not *token overlap*. Boost alone over-recalls. **Keep concept, refine weights and filter placement.**
- **Implement now vs plan-only.** The risk of migrating ToolQ without quote support is real - the parser logic is a pure string transform, so it survives any backend swap. Plan-only adds latency without safety gain. **Keep implement-now, but with the stricter scoping below.**

### B. Where ComC1 underspecifies (must refine)

**B1. Parser definition is too vague.**
ComC1 says "balanced quotes with escaped-quote support" but does not pin the grammar. The 8-case table mentions `"` and `"""` as unbalanced, and `"a quoted inside"` as escaped, yet gives no state machine. Without a pinned grammar, two engineers will write two parsers.

- **Pin the grammar:** scan left-to-right, toggle `in_quote` on unescaped `"`, accumulate into the current term buffer. `\"` is the only escape; everything else literal. A `\` immediately before a closing `"` is a literal `\` followed by a closing quote (do not consume the backslash).
- **Length cap must be enforced during scan, not after.** ComC1's "cap at 200 chars" is correct but the cap belongs to the parser, not a post-step, otherwise pathological input (`"` + 100k chars) burns memory before rejection.
- **Hard reject on missing-close.** Unbalanced quote = no quoted terms = fall through to unquoted path. Never raise, never log-and-continue (logs leak user query content).

**B2. Lexical score change is undefined in magnitude.**
"3x-5x weight multiplier" is hand-waved. Two problems:

- A multiplier of 5 can still be drowned out when 8 unquoted tokens co-occur. Exact-phrase intent must not be drownable.
- `lexical_score` returning 0 when `important_terms` is empty (per packet evidence) means adding quoted terms to `important_terms` is necessary but not sufficient: if the quoted term is the *only* term, score must be meaningfully > 0 so a true exact match outranks empty hits.

**Refined rule:**
- Add quoted terms to `important_terms` with a **fixed weight floor of 1.0 per quoted token** (not a multiplier on existing weights).
- Cap the contribution of unquoted tokens relative to quoted: a single matched quoted token must always outrank a full unquoted-token match. Concretely: `score = sum(quoted_match) * W_q + sum(unquoted_weight)`, with `W_q >= max_possible_unquoted_sum + 1` for the current top-K. This makes quoted intent a hard ranking axis, not a soft tiebreaker.
- **Phase B filter is not optional.** ComC1 defers it. I disagree for two reasons:
  - Without Phase B, a quoted query still returns rows that do not contain the phrase - the user-visible behavior contradicts the "quoted = forced operator" contract. That is a UX bug, not a precision wart.
  - The filter is `phrase in row_text` over top-K (50-200), which is microseconds. There is no engineering reason to defer.
  - **Decision: ship Phase A and Phase B together. Do not split.**

**B3. Result acceptance gate is missing.**
ComC1 covers scoring but not *acceptance*. A quoted query that produces zero exact-phrase rows in FTS5 must return zero rows, not "best-effort unquoted matches." That is the difference between *forced operator* and *strong hint*.

- **Rule:** if any quoted term exists and Phase B exact-phrase filter rejects all top-K rows, **return empty result set** for that query. Do not silently fall back to unquoted scoring. Empty result is the honest answer for a forced exact-phrase operator that found nothing.

### C. What ComC1 missed (additions)

**C1. Vietnamese / Unicode handling is half-stated.**
ComC1 notes "count Unicode chars, not bytes" - good. But two extra cases:
- **Combining marks and ZWJ/ZWNJ** in Vietnamese (rare but present in older texts) can make `len(s)` disagree with grapheme count. For our threshold (>= 2 meaningful chars), `len(stripped) >= 2` on the unescaped, NFC-normalized string is sufficient. Do not pull in a grapheme library.
- **NFC normalize on input.** If the user's quoted term uses precomposed `ế` and the index uses decomposed `e + combining circumflex`, exact match fails silently. Normalize once at extraction time.

**C2. JSON-string and Windows-path edge cases need explicit handling.**
- `"C:\path"` - backslash before non-quote is literal. Confirmed by the B1 grammar. But `"C:\"` (escaped closing quote) is a closing quote, not a path. The grammar above handles this; document it as a test case.
- JSON-encoded user input: `"foo\"bar"` should yield the single token `foo"bar` - the `\"` is an escape, not a path. Same grammar.
- Nested quotes are *not* supported. `"say "hi" please"` produces one token up to the first closing quote, leaving `hi" please` as unquoted tail. Document and test.

**C3. Logging and observability.**
- Log *count* of quoted terms, not their content. User queries may contain secrets, paths, or PII.
- Add a single counter `quoted_search_calls_total{result="hit|miss|empty"}` so we can observe adoption without reading content.

**C4. Test plan is too thin on the dangerous cases.**
ComC1's 9 tests miss:
- **Quote-free regression** (the most important test of all): a snapshot of pre_search and query results for 20 representative existing queries must be byte-identical before/after. This is the proof that the short-circuit gate works.
- **Quote inside a larger query:** `AI "Q3" pipeline` - quoted token boosts rank, unquoted tokens still contribute, exact-phrase filter applies only to `"Q3"`.
- **Multiple quoted terms:** `"Q3" "v2"` - both must appear, post-filter is AND not OR.
- **Empty quoted term:** `""` - no-op, no crash, no rank shift.
- **Whitespace-only quoted term:** `"   "` - no-op (trim, then len < 2).
- **Adversarial input:** a 1MB string with one `"` at position 999999 - must not OOM, must not hang. Bounded scan with cap.

**C5. Migration risk is underweighted.**
ComC1 says the parser "ports cleanly through migration as a data transform." This is true *only if* the parser is implemented as a pure function with no I/O, no logging, and no global state. Add this as an explicit constraint in the implementation ticket. Any `print` or `logger.debug(query)` inside the parser will leak content and will block the next migration.

### D. Final safe design recommendations

1. **One new utility** `extract_quoted_terms(query: str) -> list[str]`:
   - Pinned state-machine grammar (B1).
   - 200-char hard cap enforced *during* scan.
   - NFC-normalize, trim, require `len >= 2` meaningful chars.
   - No I/O, no logging, no globals. Pure function.
2. **Short-circuit gate** at the top of both `/pre_search` and `/query` paths: empty result -> original code path, byte-identical output.
3. **Lexical impact**:
   - Quoted tokens added to `important_terms` with fixed weight floor 1.0 per token (B2).
   - Weight calibration: one quoted match > full unquoted match (B2).
4. **Exact-phrase post-filter (Phase B)** shipped together with Phase A. Not deferred (B2, B3).
5. **Acceptance gate:** any quoted term + zero exact-phrase matches in top-K -> return empty result (B3).
6. **Edge cases** all 8 from ComC1 plus the additions in C1, C2: NFC normalization, nested quotes, JSON escapes, 1MB adversarial input, empty/whitespace quotes, multi-quote AND semantics.
7. **Tests**: minimum 15 cases - 4 unit (parser), 2 unit (lexical), 2 integration (smoke), 2 negative (single char, unbalanced), 1 real-use (Vietnamese), **plus 4 added in C4** (regression snapshot, mixed, multi-quote, adversarial). The 20-query byte-identical regression is the gate that cannot be skipped.
8. **Observability**: counter `quoted_search_calls_total{result}` only. No content logging (C3).
9. **Migration constraint**: pure function, no I/O, no logs inside parser (C5).
10. **Implement now** is correct, conditional on items 1-9 being in the implementation ticket. Without the regression snapshot test (item 7) and the acceptance gate (item 5), the change is not safe to ship.

### E. Verdict on ComC1

ComC1 judgement is a sound skeleton with one structural error (deferring Phase B) and several under-specifications (grammar, weight magnitude, acceptance gate, regression test). Adopt the architecture, tighten the rules above, and ship as one change with the full test gate. Do not split into A-then-B phases - the contract "quoted = forced exact operator" is only honest when both phases land together.

---

END_COMC2_TASK1_2_CRITIQUE
