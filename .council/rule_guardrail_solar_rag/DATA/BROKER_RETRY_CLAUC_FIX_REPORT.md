# CLAUC FIX REPORT — BROKER-RETRY-AFTER-SUCCESS

**Agent:** ClauC (broker worker, one-shot)
**Date:** 2026-06-17
**Issue:** `BROKER-RETRY-AFTER-SUCCESS`
**Project:** `D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\8. ToolA\project-orchestrator`
**File changed (only one):** `orchestrator_server.py`
**Scope:** Implemented approved P0 targets **F1 + F2 + F3** only. F4–F8 NOT implemented (deferred/denied per `comc_verify_targets\CODO_REVIEW_PACKET.md`).

---

## Root cause (recap)

A duplicate/retry dispatch of a one-shot worker task, sent right after the worker had
already written a valid output artifact and replied `ok:true`, could falsely FAIL when
the provider/API was transiently down (`Error: Network connection lost`). The broker
loop never treated the completed output artifact as the idempotent source of truth
before re-dispatching. Incident: `DISTILATION_FINAL_PLAN` Round 7 (ComC1→ComC2).

## Fix summary

Added a small, self-contained guard block to `orchestrator_server.py`. No existing
behavior changed outside the retry / idempotent-completion path; gate fires ONLY when a
prompt carries an explicit output-artifact contract.

### F3 — last-line completion detector (`_artifact_last_line_complete`, ~L2104)
- Returns True iff the artifact exists, is non-zero, and its **final non-empty stripped
  line** starts with a marker in `_COUNCIL_ARTIFACT_DONE_MARKERS` =
  (`BROKER TASK COMPLETE`, `STOP:`, `CONTINUE:`, `DONE`, `COMPLETE`).
- Checks the LAST line, not the first → defeats YAML-frontmatter `STOP:` false positives
  and catches truncated / mid-write artifacts. Trailing blank lines are skipped.

### F1 — pre-dispatch artifact-completion gate
- `_council_extract_artifact_path(text)` (~L2128): conservative extractor. Returns the
  LAST `.md` path tied to an output label (`write output to`, `fix_report`, `codo_packet`,
  `output file`, …), or a `D:\BR\...\*.md` path only when the prompt also contains a
  write/output/report verb. Otherwise `None` (free-form messages never gated).
- `_artifact_already_complete(target, text)` (~L2152): if an extracted artifact is already
  complete (F3), returns a short-circuit note dict; else `None`.
- Wired into `_council_dispatch_message` (~L6045), immediately after the SELF_MESSAGE
  guard and BEFORE the non-blocking worker handoff / enqueue. On hit it returns
  `{ok:true, dispatched:false, artifact, note:"BROKER-RETRY-AFTER-SUCCESS guard ..."}`
  for both worker→worker and leader/human callers — no subprocess is spawned.

### F2 — idempotency dedup cache (`.council\completed_tasks\`)
- `_compute_task_key(target, text, artifact_path)` (~L2170):
  `sha256(target + normalized_whitespace_lowercased_text + artifact_path)[:16]`. Including
  the extracted artifact path tightens the key (Codo F2 caution → avoid free-form
  collisions). Key computed ONLY when an artifact contract is present.
- `_council_completed_task_read/_write` (~L2179/L2189): atomic JSON read/write (unique
  tmp + `os.replace`) under `.council\completed_tasks\{key}.json`.
- Wired into `_council_enqueue_or_run`: pre-enqueue cache check (~L2532) returns the
  cached completion (`dispatched:false, cached:true`) instead of spawning a duplicate;
  post-success write (~L2583) records ONLY when reply `ok` AND artifact is genuinely
  complete on disk (artifact = source of truth → never masks a needed retry).
- Cache dir mkdir-once at server startup in `_run_server` (~L8479); write helper also
  mkdirs lazily.

### Import
- Added `import hashlib` (top of file, alphabetical).

---

## What was NOT done (per approved scope)

- **F4** (`_runtime_is_stale` → `worker_pid` + 7200s cap): deferred. Round 7 failed with
  `returncode:1` / network loss; `_runtime_is_stale` is unrelated. No reproduction need.
- **F5** (queue re-entry dedup): deferred — F2 covers duplicate dispatch.
- **F6** (STOP-in-reply-as-success on `returncode=1`): DENIED — risks masking real failures.
- **F7** (per-provider circuit breaker): DENIED — out of scope.
- **F8** (full outbox): DENIED — overbuilt; `broker_filtered.jsonl` + F2 cache suffice.
- No rule-text edits. No `[A]` restart (no restart required for these pure-Python
  additions to take effect on next natural daemon reload; see Residual risk).

---

## Tests run

| check | tool | result |
|---|---|---|
| Syntax | `python -m py_compile orchestrator_server.py` | **PASS** (`PY_COMPILE_OK`) |
| Syntax (venv312) | `venv312\Scripts\python -m py_compile` | **PASS** |
| Contract suite | `venv312 -m pytest tests/test_orchestrator_server_contract.py -q` | **PASS** — 141 passed, 1 pre-existing warning |
| F3 detector | ad-hoc functional (stop / yaml-frontmatter / partial / empty / missing / trailing-blanks / last-line-not-first) | **PASS** — all expected |
| F1 extractor+gate | ad-hoc (labelled / no-verb→None / complete→hit / partial→None) | **PASS** |
| F2 key+cache | ad-hoc (stable, whitespace-normalized, artifact-sensitive, roundtrip) | **PASS** |
| Tool-matrix gate | `scripts/sync_tool_matrix.py --ci` | **FAIL (PRE-EXISTING, unrelated)** — `drift_count=1: [low] inventory_outdated: rule_drift_check`; `code_tools=180` unchanged; my change registered 0 tools |

Functional test transcript (key lines):
```
F3 stop->True yaml(bad-last-line)->False part->False empty->False missing->False
last-line-not-first->True  trailing-blanks->True  frontmatter-only->False
F1 labelled->...a.md  noverb->None  gate-complete->{note:...}  gate-partial->None
F2 key stable->True  artifact-sensitive->True  cache roundtrip->True
141 passed, 1 warning in 4.76s
```
Note: one ad-hoc F3 case I initially labelled "exp True" used a malformed last line
(`final: CONTINUE: x`, which does NOT *start with* a marker). Detector correctly returned
False and correctly ignored the line-1 frontmatter `STOP:` — that is the intended
anti-false-positive behavior (re-confirmed with a properly-formed last-line case → True).

---

## Residual risk

1. **Tool-matrix drift (pre-existing):** `sync_tool_matrix.py --ci` reports 1 low-severity
   `inventory_outdated: rule_drift_check`. NOT introduced by this fix (no tools/schemas
   touched; `code_tools=180` unchanged). Should be resolved separately by Codo/owner.
2. **Intentional re-run of same output path:** if a user deliberately re-runs a task that
   writes to an already-complete artifact path, the F1 gate / F2 cache will skip it. This
   is the intended idempotency trade-off (artifact = source of truth). To force a re-run,
   delete/rename the artifact or its `.council\completed_tasks\{key}.json`.
3. **Extractor coverage:** extraction is conservative; an output contract phrased without
   a recognized label AND not under `D:\BR\` would not be gated (safe-fail → normal
   dispatch, i.e. pre-fix behavior). No regression risk.
4. **Daemon reload:** changes are in `orchestrator_server.py`; they take effect when the
   daemon next loads source. No restart performed (worker dispatch may be in-flight). If
   immediate activation is required, a controlled `[A]` restart is needed when no dispatch
   is in flight — REPORTED, not performed.

---

## Files written by this task

- `D:\BR\BROKER_RETRY_FIX\clauc_fix\CLAUC_FIX_REPORT.md` (this file)
- `D:\BR\BROKER_RETRY_FIX\clauc_fix\CODO_REVIEW_PACKET.md`

*End of CLAUC_FIX_REPORT.md*
