# RES: HERMC5_P113 — Add `deprecated` flag to electron_devtools_log schema

## Task

P1.13 (AQ_UPGRADE_PLAN_FINAL): Add machine-readable `deprecated: true` boolean flag to `electron_devtools_log` tool schema so clients can programmatically detect deprecation status, not just rely on human-readable description text.

## Deliverable

| Item | Path | Status |
|------|------|--------|
| `_DEPRECATED_TOOLS` set + conditional injection | `code_tools.py:12278-12295` | **DONE** |

## Changes made

### 1. `_DEPRECATED_TOOLS` set (code_tools.py:12278-12282)

Added a new set `_DEPRECATED_TOOLS` containing `electron_devtools_log`, placed between `_R40_SEE_ALSO_MAP` (ends line 12276) and `_v15_schema()` (starts line 12284). Pattern mirrors `_R40_SEE_ALSO_MAP` — a name→metadata lookup dict.

### 2. Conditional injection in `_v15_schema()` (code_tools.py:12293-12295)

Added 3 lines inside `_v15_schema()` after the existing `see_also` conditional:

```python
# V15 R41: attach deprecated flag if defined
if name in _DEPRECATED_TOOLS:
    schema_function["deprecated"] = True
```

This follows the exact same pattern as `see_also` injection (lines 12291-12292): lookup tool name in a map/set, attach metadata field to the `function` dict if present.

## Design decisions

1. **Why `_v15_schema()` injection, not a post-hoc stamping loop?** The `see_also` pattern (R40) already proved that conditional injection inside the builder function is the clean approach. A separate stamping loop (like `x_phase` at lines 12299+ would add unnecessary iteration over all ~174 schemas for a single deprecated tool.

2. **Why a set, not a dict?** `deprecated` is a boolean — presence in the set means `True`, absence means `not deprecated`. No value mapping needed (unlike `see_also` which maps to a dict of related tools).

3. **Why not extend the tuple?** The 4-tuple `(name, description, properties, required)` in `registry_data.py` is consumed by a list comprehension that unpacks exactly 4 elements. Adding a 5th would break all other tuples or require `*` unpacking. The set-based approach is non-invasive.

4. **Extensibility:** Adding more deprecated tools in the future = one line addition to `_DEPRECATED_TOOLS`. No other code changes needed.

## API surface impact

- `GET /api/tools` — `handle_tools()` uses `copy.deepcopy(schema)` then only ADDS fields (payload_example, protected, mutating, etc.). It does NOT strip existing fields. So `deprecated: true` passes through unchanged.
- `GET /api/tools/{name}/schema` — `handle_tool_describe()` same pattern: `copy.deepcopy` + add fields only. `deprecated: true` preserved.
- `GET /api/tools/names` — returns only names, not schemas. No impact.

## Verification

1. **Patch applied:** `code_tools.py:12278-12295` confirmed via read_file — `_DEPRECATED_TOOLS` set at 12280-12282, conditional injection at 12293-12295.
2. **Syntax check:** `ast.parse` with `utf-8-sig` encoding → SYNTAX OK. (File has pre-existing UTF-8 BOM `efbbbf`, not introduced by this patch.)
3. **Lint:** patch tool auto-lint → status: ok, no errors.
4. **Logic verification (isolated):** Simulated `_v15_schema('electron_devtools_log', ...)` with the new logic → `deprecated: True` present in function dict, alongside `see_also`. Keys: `['name', 'description', 'parameters', 'see_also', 'deprecated']`.
5. **No `x_phase` loop touched:** The runtime stamping loop at lines 12299+ was NOT modified — constraint satisfied.
6. **Live API:** `curl /api/tools/electron_devtools_log/schema` returned `NOT_PRESENT` because the running server has not reloaded the new code. The code change is correct; a server restart is needed to reflect in live API. This is expected — no auto-reload mechanism exists in `orchestrator_server.py`.

## Files modified

| File | Lines | Change |
|------|-------|--------|
| `code_tools.py` | 12278-12282 | Added `_DEPRECATED_TOOLS` set |
| `code_tools.py` | 12293-12295 | Added `deprecated` conditional injection in `_v15_schema()` |

## Constraints satisfied

- [x] Did NOT touch `x_phase`/`x_phase_label` runtime stamping loop
- [x] `registry_data.py` not modified (no circular import risk)
- [x] Description text with "⚠️ DEPRECATED R41" preserved as-is
- [x] No API keys/tokens/credentials touched

END_HERMC5_P113
