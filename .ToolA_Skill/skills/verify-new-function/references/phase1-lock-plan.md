# Verify New Function Reference

This reference preserves the canonical five-phase pattern derived from the ToolQ 1.1-1.5 lock plan.

## Five Phases

1. Audit reports
2. Consolidated final audit
3. Devine audit
4. Fix verify
5. Test benchmark and guardrail

## Required Worker Flow

Phase 1:

- ComC1 writes an independent audit.
- One OpeC slot writes an independent audit; prefer OpeC1/OpeC3/OpeC4 for split scan/POV and reserve OpeC2 for consolidation when possible.
- HermC1 writes an independent audit.
- Codo checks all three reports are complete.
- Audit must cover code lines, function signatures, logic/control flow, data contracts, callers/callees, input validation, error handling, side effects, encoding, security/destructive risk, tests, runtime activation, and regression risk.

Phase 2:

- OpeC2 consolidates the three audits when available; otherwise use another OpeC slot and record the fallback.
- ComC1 verifies missing/extra/bad merges.
- OpeC2 or the routed OpeC fallback revises until ComC1 emits final-console pass markers.

Phase 3:

- CodoC verifies every final audit point through `[A]`.
- Same-identity dispatch uses `sender="user"`.
- CodoC does not fix code.

Phase 4:

- Codo fixes or routes one narrow bug to ClauC.
- ClauC only handles judgement or a specific existing-code bug with a narrow evidence pack.
- Codo runs smoke/unit.
- ComC1 and HermC1 verify the fix independently; if HermC1 is unavailable, use a small OpeC slot and record the fallback.
- HermC1 writes lock artifacts; if HermC1 is unavailable, use a small OpeC slot and record the fallback.

ComC1 verifies fixes and may handle dev code/low-risk fix when explicitly routed.

Phase 5:

- OpeC1 runs live real-use sanity/benchmark.
- Codo runs context-session benchmark for Q/retrieval functions.
- Guardrail gaps update the separate librarian guardrail artifact only.

## Final Console Markers

```text
NO_MISSING_POINTS
NO_EXTRA_POINTS
NO_BAD_MERGES
FINAL_CONSOLE_PASS
```

## Devine Audit Labels

```text
TRUE_BUG
TRUE_RISK
TEST_GAP
MISUNDERSTANDING_NOT_BUG
DUPLICATE_MERGED
OUT_OF_SCOPE
UNKNOWN_NOT_VERIFIED
```

## Lock Markers

```text
PASS
ALL_REQUIRED_AUDITS_PRESENT
FINAL_CONSOLE_PASS
CODOC_DEVINE_AUDIT_REVIEWED
FIX_VERIFY_PASS
REAL_USE_PASS
BENCHMARK_REVIEWED
GUARDRAIL_REVIEWED
NO_DESTRUCTIVE_LIVE_WRITE
UNKNOWN_NOT_VERIFIED_ZERO_OR_JUSTIFIED
```

## Librarian Guardrail Artifact

```text
C:\Users\ADMIN\.claude-mem\staging\q-hardening\Q_LIBRARIAN_GUARDRAILS.md
```

Use benchmark findings to update that artifact only when the issue is librarian judgement, not mechanical retrieval/function behavior.
