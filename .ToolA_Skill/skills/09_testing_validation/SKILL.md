---
name: testing_validation
description: |
  Use after every T2 write to verify integrity (syntax, symbols, encoding) and to run
  test profiles (unittest, pytest, py_compile, npm_test). Activate post-write or when
  User asks "run tests". V15 R8: `validate` unified tool dispatches to file/symbols/encoding.
trigger:
  - tool: "validate | validate_file | validate_symbols | validate_encoding | run_test_profile | post_write_batch_verify | run_playwright_test | record_ui_flow | ruff_fix"
  - task_pattern: "test | validate | check syntax | run unit | verify file"
preconditions:
  - "File(s) exist (post-write context typical)"
  - "Active session for protected tools"
related_skills:
  - 05_write_pipeline
  - 10_coverage_report
  - 07_error_schema_hints
risk_level: medium
---

# 09 — Testing & Validation

## V15 R8 unified `validate` tool

```
validate(file_path, project_path="", checks=["file"], lang="auto", expected_encoding="utf-8", language_hint="")
```

`checks` accepts list of:
- `file` — syntax (py_compile for .py, node --check for .js/.ts, UTF-8 validity for .html)
- `symbols` — AST drift; compares against INDEX, flags accidentally deleted functions/classes
- `encoding` — UTF-8 integrity + BOM detection + mojibake + Vietnamese diacritic check

Aliases (backward compat): `validate_file` → `checks=["file"]`, `validate_symbols` → `["symbols"]`, `validate_encoding` → `["encoding"]`.

Response shape:
```json
{
  "ok": true,
  "file": { "ok": true, "lang": "python", "details": {} },
  "symbols": { "ok": true, "added": [], "removed": [], "drift_count": 0 },
  "encoding": { "ok": true, "encoding_used": "utf-8", "mojibake_risk": false }
}
```

## Post-write checklist (HARD per [PI:15])

After EXECUTE write batch:
```
1. validate(file_path, checks=["file","symbols","encoding"])
   - file check ALWAYS
   - symbols check IF supported (INDEX present)
   - encoding check IF content had Unicode/multiline
2. cache_delta(project_path) ONCE (covers whole batch)
3. If validate fail → auto-revert from .bak (already automatic in write_fix)
4. Report files / validation / symbols / warnings / native-audit
5. Return DEFAULT 🔍
```

## Test profiles (run_test_profile)

```
run_test_profile(project_path, profile="<name>", timeout=120)
```

Available profiles:
- `unittest` — Python stdlib unittest discovery (`-m unittest discover`)
- `pytest` — `pytest tests/` if pytest installed
- `py_compile_core` — quick `python -m py_compile` over core files (no test run)
- `py_compile` — same as core but broader file set
- `npm_test` — `npm test` if package.json + test script present

No `--keyword`, `--file` flags supported. For single-file test -> use `run_project_command` with explicit `python -m unittest <module>`.

### Ruff 0.16 lint vs fix

- Read-only lint/check: run Ruff without mutation and report findings; no EXECUTE trigger is needed.
- Auto-fix: use `ruff_fix(project_path, apply=false)` first, review the unified diff, then set `apply=true` only in active EXECUTE scope.
- Keep `unsafe_fixes=false` unless Anh explicitly requests unsafe fixes. The supported runtime dependency is `ruff>=0.16,<0.17`.

## Coverage classification (skill 10)

After test run, classify:
- **pass** — assertion succeeded
- **fail** — assertion failed (P0)
- **skip** — `@skip` decorated or `pytest.skip()` (NEVER count as pass)
- **untested** — function/class with no test file mention (audit gap)
- **retry-pass** — passed after explicit retry (flag flaky)

Each tracked separately; total = pass + fail + skip + untested. See skill 10 for report format.

## Anti-patterns

- ❌ Skipping `validate` after write (FILE_CHANGED risk on next read)
- ❌ Counting skipped tests as pass
- ❌ Inferring test success from exit_code 0 alone (must check `tests_failed=0`)
- ❌ Running pytest without `tests/` dir → profile mismatch warning, not silent fallback
- ❌ Custom `--file` flag (schema doesn't support)

## Validate edge cases

| Edge | Handler |
|------|---------|
| `.py` with BOM | py_compile reject → use utf-8-sig load + write back |
| `.json` syntactically wrong | `json.loads` failure → return error_name=JSON_PARSE_ERROR |
| `.html` non-UTF-8 byte | encoding check flags + suggests re-save |
| Empty file | symbols check skips (no drift possible) |
| Binary file | error_name=BINARY_FILE_UNSUPPORTED |

## Operational Impact

- Unified `validate` saves 2-3 tool calls per batch
- post_write_batch_verify wraps validate+cache_delta in 1 call
- Skipping for low-risk write (e.g., comment edit) saves cycles but loses governance — prefer always-validate

## Harness

- Test 1: write_fix .py → auto validate file+symbols+encoding → all OK
- Test 2: write broken .py syntax → validate returns ok:false, write auto-reverts
- Test 3: rename function → validate_symbols flags `removed: [oldName]`
- Test 4: write Vietnamese .md without BOM → validate_encoding ok=true; with BOM → flag
- Test 5: run_test_profile unittest with 5 tests → 4 pass 1 skip → coverage classification correct
